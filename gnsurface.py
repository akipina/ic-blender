"""D9 spike: the bicubic patch surface as REAL evaluated geometry, through Geometry Nodes.

Today the surface is a gpu overlay (``draw.py``): exact, fast, and invisible to everything that reads
*geometry* -- Sculpt Mode, downstream modifiers, snapping, ray-cast selection and the renderer (Bake
Surface is the workaround). This module evaluates the same Bernstein surface inside a Geometry Nodes
group on the creature object, so the depsgraph-evaluated mesh IS the surface.

How the patch table reaches the nodes
-------------------------------------
The mesh vertices are the control-point pool and the mesh faces are the B6 corner quads, one per
patch (``patchsel.face_rows``). A node group cannot hold a table, so the table is written as ordinary
mesh attributes on the FACE domain, one face per patch:

* ``bz_p0`` .. ``bz_p15`` (INT) -- the patch's 16 pool indices;
* ``bz_uv0`` .. ``bz_uv3`` (FLOAT2) -- its 4 stored corner UVs (slots 0, 3, 12, 15).

The group then, per output point: ``Sample Index`` (FACE) to read the 16 pool indices of its patch,
``Sample Index`` (POINT) to read those control points' positions, and the bicubic Bernstein sum of
them at the point's (u, v). The output geometry is built inside the group -- an n x n ``Grid``
instanced on one point per face and realized -- so nothing outside the creature object is needed.

This is a spike: read ``ROADMAP_NATIVE.md`` D9 for the verdict and the numbers.

A patch whose four corners are not distinct has no corner quad (6 in the base game), so it has no
face to carry its row and is NOT evaluated here; ``missing_patches`` counts them and the caller says
so rather than dropping them silently.
"""
from __future__ import annotations

import numpy as np

import bpy

from . import draw
from . import patchsel
from . import store

GROUP = "BZ Patch Surface"          # the node group (one per .blend, built by script)
# The group's layout version, stored on it as a custom property; node_group() rebuilds an older group a .blend carries.
# 2 (2026-11-15): Merge by Distance runs only when Weld Distance > 0. From Blender 5.3 that node merges points that
# sit exactly on top of each other even at distance 0 (5.2 merged none), so version 1 welded every patch seam by default.
GROUP_VERSION = 2
MODIFIER = "BZ Patch Surface"       # the modifier on the creature object
ATTR_P = "bz_p%d"                   # 16 INT face attributes: the patch's pool indices
ATTR_UV = "bz_uv%d"                 # 4 FLOAT2 face attributes: the patch's stored corner UVs
ATTR_ROW = "bz_row"                 # INT face attribute: which patch ROW this face is (face i = patch face_rows[i])
ATTR_PID = "bz_pid"                 # written on the OUTPUT points: which patch the point belongs to
ATTR_U, ATTR_V = "bz_u", "bz_v"     # written on the OUTPUT points: its (u, v) in that patch
ATTR_MINE = "bz_gn_surface"         # marks the generated points while the input mesh is joined for its materials
UV_MAP = "UVMap"


# --------------------------------------------------------------------------- the patch table


def missing_patches(patches) -> np.ndarray:
    """Patch rows with no corner quad, so no face to carry their table row (a repeated corner)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    rows = patchsel.face_rows(P)
    keep = np.ones(len(P), dtype=bool)
    keep[rows] = False
    return np.nonzero(keep)[0]


def write_tables(obj, patches=None, uvs=None) -> int:
    """Write the per-patch table onto the mesh's FACE domain; return the number of faces written.

    Pure bookkeeping on attributes the exporter never reads (``ops._mesh_verts`` reads vertices, the
    pool guard reads faces), so an untouched creature still exports byte-identical."""
    me = obj.data
    if patches is None:
        patches = store.patches(obj)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    rows = patchsel.face_rows(P)
    if len(rows) != len(me.polygons):
        raise ValueError(f"{obj.name}: {len(me.polygons)} corner quads for {len(rows)} patches with corners "
                         "-- the pool guard refuses this state; re-import or Undo")
    table = P[rows]                                     # (F, 16) pool indices in face order
    for k in range(16):
        _set_face_attr(me, ATTR_P % k, "INT", table[:, k].astype(np.int32))
    # the patch ROW, so an output point names its patch and not the face that carried the row
    _set_face_attr(me, ATTR_ROW, "INT", rows.astype(np.int32))
    if uvs is None:
        uvs = store.patch_uv(obj)
    if uvs is not None and len(np.asarray(uvs).reshape(-1)) == 8 * len(P):
        U = np.asarray(uvs, dtype=np.float32).reshape(-1, 8)[rows]
        for c in range(4):
            _set_face_attr(me, ATTR_UV % c, "FLOAT2", U[:, 2 * c:2 * c + 2].ravel())
    return len(rows)


def _set_face_attr(me, name, data_type, values) -> None:
    at = me.attributes.get(name)
    if at is not None and (at.domain != "FACE" or at.data_type != data_type):
        me.attributes.remove(at)
        at = None
    if at is None:
        at = me.attributes.new(name, data_type, "FACE")
    at.data.foreach_set("value" if data_type in ("INT", "FLOAT") else "vector", np.asarray(values).ravel())


def clear_tables(obj) -> None:
    me = obj.data
    for name in [ATTR_P % k for k in range(16)] + [ATTR_UV % c for c in range(4)] + [ATTR_ROW]:
        at = me.attributes.get(name)
        if at is not None:
            me.attributes.remove(at)


# --------------------------------------------------------------------------- the node group


def _sock(node, name, out=False):
    for s in (node.outputs if out else node.inputs):
        if s.identifier == name or s.name == name:
            return s
    raise KeyError(f"{node.bl_idname}: no {'output' if out else 'input'} socket {name!r} "
                   f"(has {[s.identifier for s in (node.outputs if out else node.inputs)]})")


def node_group(rebuild=False):
    """The Geometry Nodes group, built by script (no .blend asset, nothing to install)."""
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None and not rebuild and int(ng.get("bz_version", 1)) >= GROUP_VERSION:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    return _build_group()


def _build_group():
    ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    ifc = ng.interface
    ifc.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    dens = ifc.new_socket("Density", in_out="INPUT", socket_type="NodeSocketInt")
    dens.default_value, dens.min_value, dens.max_value = 5, 2, 32
    weld = ifc.new_socket("Weld Distance", in_out="INPUT", socket_type="NodeSocketFloat")
    weld.default_value, weld.min_value = 0.0, 0.0
    ifc.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    N = ng.nodes
    L = ng.links
    gi = N.new("NodeGroupInput")
    go = N.new("NodeGroupOutput")
    geo = _sock(gi, "Geometry", out=True)

    # one point per patch (per corner quad), carrying its face index
    m2p = N.new("GeometryNodeMeshToPoints"); m2p.mode = "FACES"
    L.new(geo, _sock(m2p, "Mesh"))
    idx = N.new("GeometryNodeInputIndex")
    cap_pid = N.new("GeometryNodeCaptureAttribute"); cap_pid.domain = "POINT"
    cap_pid.capture_items.new("INT", "pid")
    L.new(_sock(m2p, "Points", out=True), _sock(cap_pid, "Geometry"))
    L.new(_sock(idx, "Index", out=True), cap_pid.inputs[-2])
    pid = cap_pid.outputs[-2]

    # the evaluation grid: n x n vertices over the unit square, its local position captured as (u, v)
    grid = N.new("GeometryNodeMeshGrid")
    _sock(grid, "Size X").default_value = 1.0
    _sock(grid, "Size Y").default_value = 1.0
    L.new(_sock(gi, "Density", out=True), _sock(grid, "Vertices X"))
    L.new(_sock(gi, "Density", out=True), _sock(grid, "Vertices Y"))
    gpos = N.new("GeometryNodeInputPosition")
    cap_uv = N.new("GeometryNodeCaptureAttribute"); cap_uv.domain = "POINT"
    cap_uv.capture_items.new("VECTOR", "uv")
    L.new(_sock(grid, "Mesh", out=True), _sock(cap_uv, "Geometry"))
    L.new(_sock(gpos, "Position", out=True), cap_uv.inputs[-2])
    uv = cap_uv.outputs[-2]

    iop = N.new("GeometryNodeInstanceOnPoints")
    L.new(cap_pid.outputs[0], _sock(iop, "Points"))
    L.new(cap_uv.outputs[0], _sock(iop, "Instance"))
    real = N.new("GeometryNodeRealizeInstances")
    L.new(_sock(iop, "Instances", out=True), _sock(real, "Geometry"))

    sep = N.new("ShaderNodeSeparateXYZ")
    L.new(uv, _sock(sep, "Vector"))
    u = _add(N, L, "ADD", _sock(sep, "X", out=True), 0.5)     # grid is centred on the origin
    v = _add(N, L, "ADD", _sock(sep, "Y", out=True), 0.5)

    Bu = _bernstein(N, L, u)
    Bv = _bernstein(N, L, v)

    # 16 pool indices of this point's patch, then those control points' positions
    pos_in = N.new("GeometryNodeInputPosition")
    total = None
    for k in range(16):
        na = N.new("GeometryNodeInputNamedAttribute"); na.data_type = "INT"
        _sock(na, "Name").default_value = ATTR_P % k
        si = N.new("GeometryNodeSampleIndex"); si.data_type = "INT"; si.domain = "FACE"
        L.new(geo, _sock(si, "Geometry"))
        L.new(_sock(na, "Attribute", out=True), _sock(si, "Value"))
        L.new(pid, _sock(si, "Index"))
        sp = N.new("GeometryNodeSampleIndex"); sp.data_type = "FLOAT_VECTOR"; sp.domain = "POINT"
        L.new(geo, _sock(sp, "Geometry"))
        L.new(_sock(pos_in, "Position", out=True), _sock(sp, "Value"))
        L.new(_sock(si, "Value", out=True), _sock(sp, "Index"))
        w = _mul(N, L, Bu[k // 4], Bv[k % 4])
        scaled = N.new("ShaderNodeVectorMath"); scaled.operation = "SCALE"
        L.new(_sock(sp, "Value", out=True), scaled.inputs[0])
        L.new(w, _sock(scaled, "Scale"))
        if total is None:
            total = _sock(scaled, "Vector", out=True)
        else:
            add = N.new("ShaderNodeVectorMath"); add.operation = "ADD"
            L.new(total, add.inputs[0]); L.new(_sock(scaled, "Vector", out=True), add.inputs[1])
            total = _sock(add, "Vector", out=True)

    setpos = N.new("GeometryNodeSetPosition")
    L.new(_sock(real, "Geometry", out=True), _sock(setpos, "Geometry"))
    L.new(total, _sock(setpos, "Position"))
    cur = _sock(setpos, "Geometry", out=True)

    # The generated grid has no material list of its own, so the creature's material slots would not be
    # on the output mesh and every patch would render with Blender's default (measured: the evaluated
    # mesh's material list was [None] while the corner quads carried the right 9 indices). Joining the
    # input mesh brings its list along; the input's own points -- and with them its corner quads -- are
    # deleted again immediately, and the list stays.
    mark = N.new("GeometryNodeStoreNamedAttribute"); mark.data_type = "BOOLEAN"; mark.domain = "POINT"
    L.new(cur, _sock(mark, "Geometry"))
    _sock(mark, "Name").default_value = ATTR_MINE
    _sock(mark, "Value").default_value = True
    join = N.new("GeometryNodeJoinGeometry")
    # a multi-input socket lists its links in REVERSE insertion order, and Join Geometry concatenates the
    # material lists in that order: link the generated surface first so the INPUT mesh's slots come first
    # and material index k still means slot k (pinned by the test against the object's own slot list)
    L.new(_sock(mark, "Geometry", out=True), _sock(join, "Geometry"))
    L.new(geo, _sock(join, "Geometry"))
    mine = N.new("GeometryNodeInputNamedAttribute"); mine.data_type = "BOOLEAN"
    _sock(mine, "Name").default_value = ATTR_MINE
    notmine = N.new("FunctionNodeBooleanMath"); notmine.operation = "NOT"
    L.new(_sock(mine, "Attribute", out=True), notmine.inputs[0])
    dele = N.new("GeometryNodeDeleteGeometry"); dele.domain = "POINT"; dele.mode = "ALL"
    L.new(_sock(join, "Geometry", out=True), _sock(dele, "Geometry"))
    L.new(_sock(notmine, "Boolean", out=True), _sock(dele, "Selection"))
    cur = _sock(dele, "Geometry", out=True)

    # the creature's per-patch material, and its stored corner UVs bilinearly over the patch
    mat_na = N.new("GeometryNodeInputNamedAttribute"); mat_na.data_type = "INT"
    _sock(mat_na, "Name").default_value = "material_index"
    mat_si = N.new("GeometryNodeSampleIndex"); mat_si.data_type = "INT"; mat_si.domain = "FACE"
    L.new(geo, _sock(mat_si, "Geometry"))
    L.new(_sock(mat_na, "Attribute", out=True), _sock(mat_si, "Value"))
    L.new(pid, _sock(mat_si, "Index"))
    setmat = N.new("GeometryNodeSetMaterialIndex")
    L.new(cur, _sock(setmat, "Geometry"))
    L.new(_sock(mat_si, "Value", out=True), _sock(setmat, "Material Index"))
    cur = _sock(setmat, "Geometry", out=True)

    corner_uv = []
    for c in range(4):
        na = N.new("GeometryNodeInputNamedAttribute"); na.data_type = "FLOAT_VECTOR"
        _sock(na, "Name").default_value = ATTR_UV % c
        si = N.new("GeometryNodeSampleIndex"); si.data_type = "FLOAT_VECTOR"; si.domain = "FACE"
        L.new(geo, _sock(si, "Geometry"))
        L.new(_sock(na, "Attribute", out=True), _sock(si, "Value"))
        L.new(pid, _sock(si, "Index"))
        corner_uv.append(_sock(si, "Value", out=True))
    # ICParse patch_uvs: w = [(1-u)(1-v), (1-u)v, u(1-v), uv] over the stored corners 0, 3, 12, 15
    iu = _add(N, L, "SUBTRACT", 1.0, u)
    iv = _add(N, L, "SUBTRACT", 1.0, v)
    uvsum = None
    for c, (a, b) in enumerate(((iu, iv), (iu, v), (u, iv), (u, v))):
        sc = N.new("ShaderNodeVectorMath"); sc.operation = "SCALE"
        L.new(corner_uv[c], sc.inputs[0])
        L.new(_mul(N, L, a, b), _sock(sc, "Scale"))
        if uvsum is None:
            uvsum = _sock(sc, "Vector", out=True)
        else:
            add = N.new("ShaderNodeVectorMath"); add.operation = "ADD"
            L.new(uvsum, add.inputs[0]); L.new(_sock(sc, "Vector", out=True), add.inputs[1])
            uvsum = _sock(add, "Vector", out=True)
    # V flipped to Blender's bottom-up convention, exactly as core.surface_uvs does for the bake
    sep_uv = N.new("ShaderNodeSeparateXYZ")
    L.new(uvsum, _sock(sep_uv, "Vector"))
    comb_uv = N.new("ShaderNodeCombineXYZ")
    L.new(_sock(sep_uv, "X", out=True), _sock(comb_uv, "X"))
    L.new(_add(N, L, "SUBTRACT", 1.0, _sock(sep_uv, "Y", out=True)), _sock(comb_uv, "Y"))
    st_uv = N.new("GeometryNodeStoreNamedAttribute"); st_uv.data_type = "FLOAT2"; st_uv.domain = "CORNER"
    L.new(cur, _sock(st_uv, "Geometry"))
    _sock(st_uv, "Name").default_value = UV_MAP
    L.new(_sock(comb_uv, "Vector", out=True), _sock(st_uv, "Value"))
    cur = _sock(st_uv, "Geometry", out=True)

    # what the point came from, so a test (and any tool) can match an output point to (patch, u, v).
    # The captured index is the FACE the row was carried on; bz_row turns it back into the patch row.
    row_na = N.new("GeometryNodeInputNamedAttribute"); row_na.data_type = "INT"
    _sock(row_na, "Name").default_value = ATTR_ROW
    row_si = N.new("GeometryNodeSampleIndex"); row_si.data_type = "INT"; row_si.domain = "FACE"
    L.new(geo, _sock(row_si, "Geometry"))
    L.new(_sock(row_na, "Attribute", out=True), _sock(row_si, "Value"))
    L.new(pid, _sock(row_si, "Index"))
    for name, src, dtype in ((ATTR_PID, _sock(row_si, "Value", out=True), "INT"),
                             (ATTR_U, u, "FLOAT"), (ATTR_V, v, "FLOAT")):
        st = N.new("GeometryNodeStoreNamedAttribute"); st.data_type = dtype; st.domain = "POINT"
        L.new(cur, _sock(st, "Geometry"))
        _sock(st, "Name").default_value = name
        L.new(src, _sock(st, "Value"))
        cur = _sock(st, "Geometry", out=True)

    # the optional weld: Merge by Distance only when Weld Distance > 0 (a Switch), so the default 0 keeps every
    # patch's own n x n points -- one output point per (patch, u, v) -- on every Blender (see GROUP_VERSION)
    merge = N.new("GeometryNodeMergeByDistance")
    L.new(cur, _sock(merge, "Geometry"))
    L.new(_sock(gi, "Weld Distance", out=True), _sock(merge, "Distance"))
    on = N.new("FunctionNodeCompare"); on.data_type = "FLOAT"; on.operation = "GREATER_THAN"
    L.new(_sock(gi, "Weld Distance", out=True), _sock(on, "A"))
    _sock(on, "B").default_value = 0.0
    sw = N.new("GeometryNodeSwitch"); sw.input_type = "GEOMETRY"
    L.new(_sock(on, "Result", out=True), _sock(sw, "Switch"))
    L.new(cur, _sock(sw, "False"))
    L.new(_sock(merge, "Geometry", out=True), _sock(sw, "True"))
    smooth = N.new("GeometryNodeSetShadeSmooth")
    L.new(_sock(sw, "Output", out=True), _sock(smooth, "Geometry"))
    L.new(_sock(smooth, "Geometry", out=True), _sock(go, "Geometry"))

    for i, n in enumerate(N):
        n.location = ((i % 12) * 220, -(i // 12) * 180)
    ng["bz_version"] = GROUP_VERSION
    return ng


def _add(N, L, op, a, b):
    m = N.new("ShaderNodeMath"); m.operation = op
    for i, x in enumerate((a, b)):
        if isinstance(x, float):
            m.inputs[i].default_value = x
        else:
            L.new(x, m.inputs[i])
    return _sock(m, "Value", out=True)


def _mul(N, L, a, b):
    return _add(N, L, "MULTIPLY", a, b)


def _bernstein(N, L, t):
    """The cubic Bernstein basis at ``t`` as four float sockets."""
    mt = _add(N, L, "SUBTRACT", 1.0, t)
    mt2 = _mul(N, L, mt, mt)
    t2 = _mul(N, L, t, t)
    b0 = _mul(N, L, mt2, mt)
    b1 = _mul(N, L, _mul(N, L, mt2, t), 3.0)
    b2 = _mul(N, L, _mul(N, L, t2, mt), 3.0)
    b3 = _mul(N, L, t2, t)
    return [b0, b1, b2, b3]


# --------------------------------------------------------------------------- the modifier


def apply_appearance(obj, patches=None) -> int:
    """Give the object the creature's material slots and put each corner quad on its patch's slot, so the
    evaluated surface renders with the real skins (``material.build_blender_materials`` and the stored
    tables Bake Surface uses -- the faces still never reach the file)."""
    from . import material
    slot_names, tex_names, colors = store.material_slots(obj)
    if len(colors) == 0:
        return 0
    me = obj.data
    if not me.materials:
        images = {nm: bpy.data.images.get(nm) for nm in tex_names if nm}
        mats = material.build_blender_materials(
            [{"tex": tex_names[i] if i < len(tex_names) else "", "color": tuple(colors[i])}
             for i in range(len(colors))],
            {nm: img for nm, img in images.items() if img}, slot_names)
        for mt in mats:
            me.materials.append(mt)
    # An Image Texture node with nothing wired to its Vector input reads the mesh's ACTIVE RENDER UV
    # map, and a UV map the node group creates from nothing is neither active nor active-render (the
    # texture then samples one texel and the creature renders flat-coloured -- measured). Giving the
    # control mesh an empty UV map of the same name makes the joined output inherit the flags, and the
    # group's Store Named Attribute fills it in. Nothing reads the control mesh's UV layers (the UV
    # editing cage is its own object, ops._UV_LAYER) and no UV reaches the file from here.
    if UV_MAP not in me.uv_layers:
        me.uv_layers.new(name=UV_MAP)
    me.uv_layers[UV_MAP].active_render = True
    rows = patchsel.face_rows(store.patches(obj) if patches is None else patches)
    pmat = store.patch_mat(obj)
    n = len(me.materials)
    idx = np.array([int(pmat[r]) if r < len(pmat) else 0 for r in rows], dtype=np.int32)
    idx[(idx < 0) | (idx >= n)] = 0
    me.polygons.foreach_set("material_index", idx)
    me.update()
    return n


def _input_socket(mod, name):
    """The modifier's value holder for the group input called ``name``.

    Blender 5.x keeps a Nodes modifier's input values in ``modifier.properties.inputs.<identifier>``
    (an RNA struct with ``.value``), not as object ID properties as 4.x did -- assigning the ID
    property is silently ignored there, which is how a density setting can go missing."""
    for s in mod.node_group.interface.items_tree:
        if getattr(s, "in_out", None) == "INPUT" and s.name == name:
            ins = getattr(getattr(mod, "properties", None), "inputs", None)
            if ins is not None and hasattr(ins, s.identifier):
                return getattr(ins, s.identifier), s.identifier
            return None, s.identifier
    raise KeyError(f"the node group has no input {name!r}")


def set_input(mod, name, value) -> None:
    sock, ident = _input_socket(mod, name)
    if sock is not None:
        sock.value = value
    else:
        mod[ident] = value                  # Blender 4.x
    got = get_input(mod, name)
    if got != value and abs(float(got) - float(value)) > 1e-9:
        raise RuntimeError(f"the modifier kept {name} = {got!r}, not {value!r}")


def get_input(mod, name):
    sock, ident = _input_socket(mod, name)
    return sock.value if sock is not None else mod[ident]


def modifier(obj):
    for m in obj.modifiers:
        if m.type == "NODES" and m.name == MODIFIER:
            return m
    return None


def is_enabled(obj) -> bool:
    return modifier(obj) is not None


def enable(obj, density=5, weld=0.0, rebuild=False):
    """Give the creature the evaluated surface: write the table, build the group, add the modifier.

    Returns ``(modifier, faces written, note)``; ``note`` is "" or why the creature's materials could not
    be put on the object (never swallowed). The Armature modifier (when there is one) stays FIRST, so the
    surface is evaluated from the posed control points exactly as the overlay draws it."""
    n = write_tables(obj)
    note = ""
    try:
        if apply_appearance(obj) == 0:
            note = "no material table on the object: the surface renders with Blender's default material"
    except Exception as e:                      # never swallowed: the caller reports it
        note = f"materials not applied ({type(e).__name__}: {e}): the surface renders untextured"
    ng = node_group(rebuild=rebuild)
    mod = modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(MODIFIER, "NODES")
        mod.node_group = ng
    else:
        mod.node_group = ng
    set_input(mod, "Density", int(density))
    set_input(mod, "Weld Distance", float(weld))
    mod.show_in_editmode = True
    mod.show_on_cage = False
    obj.display_type = "TEXTURED"
    obj.update_tag()
    return mod, n, note


def disable(obj, keep_tables=False) -> None:
    mod = modifier(obj)
    if mod is not None:
        obj.modifiers.remove(mod)
    if not keep_tables:
        clear_tables(obj)
    obj.display_type = "WIRE"
    obj.update_tag()


def posed_control_points(obj, depsgraph):
    """The mesh holding the creature's control points AS POSED by its rig.

    With the evaluated surface on, ``obj.evaluated_get(...)`` is the SURFACE, not the pool, so anything
    that wants posed control points (Bake Surface > Posed) must evaluate without this modifier."""
    mod = modifier(obj)
    if mod is None or not mod.show_viewport:
        return obj.evaluated_get(depsgraph).data
    mod.show_viewport = False
    try:
        depsgraph.update()
        me = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    finally:
        mod.show_viewport = True
        depsgraph.update()
    return me


# --------------------------------------------------------------------------- the scene toggle

_KEY = "bz_gn_key"          # the state the written tables belong to, so a topology edit refreshes them
_busy = False


def table_key(obj):
    """What the face tables depend on: the journal generation, the patch count and the mesh's size."""
    return [int(obj.get(store.KEY_GEN, 0) or 0), int(obj.get(store.KEY_NPATCH, 0) or 0),
            len(obj.data.polygons), len(obj.data.vertices)]


def refresh(obj) -> bool:
    """Rewrite the face tables when the creature's topology changed since they were written (Delete Patch,
    Extrude, Subdivide, Weld, Undo, a re-import). Returns True when it rewrote them."""
    if not is_enabled(obj):
        return False
    key = table_key(obj)
    if list(obj.get(_KEY, [])) == key:
        return False
    write_tables(obj)
    apply_appearance(obj)
    obj[_KEY] = key
    return True


def _on_depsgraph(scene, depsgraph=None):
    global _busy
    if _busy or scene is None:
        return
    _busy = True
    try:
        for obj in scene.objects:
            if obj.type == "MESH" and store.is_native(obj) and is_enabled(obj):
                try:
                    refresh(obj)
                except Exception as e:      # never silent: the panel reads it back
                    obj["bz_gn_error"] = f"{type(e).__name__}: {e}"
                else:
                    obj.pop("bz_gn_error", None)
    finally:
        _busy = False


def apply_scene(context) -> None:
    """Put every native creature in the scene on (or off) the evaluated surface, following the scene
    toggle ``bz_gn_surface`` and the density."""
    scene = context.scene
    on = bool(getattr(scene, "bz_gn_surface", False))
    density = int(getattr(scene, "bz_density", 5))
    for obj in scene.objects:
        if obj.type != "MESH" or not store.is_native(obj):
            continue
        if on:
            err = store.pool_error(obj)
            if err:                        # the mesh is not the pool the patch table indexes: say so, do nothing
                obj["bz_gn_error"] = err
                continue
            obj.pop("bz_gn_error", None)
            enable(obj, density=density)
            obj[_KEY] = table_key(obj)
        elif is_enabled(obj):
            disable(obj)
            obj.pop(_KEY, None)
    draw.invalidate()


class BZ_OT_gn_surface(bpy.types.Operator):
    """Turn the evaluated (Geometry Nodes) surface on or off for every creature in the scene"""
    bl_idname = "bz.gn_surface"
    bl_label = "Evaluated Surface"
    bl_options = {"REGISTER", "UNDO"}

    enable: bpy.props.BoolProperty(name="Enable", default=True)

    def execute(self, context):
        context.scene.bz_gn_surface = bool(self.enable)     # the property's update callback applies it
        notes = [f"{o.name}: {o['bz_gn_error']}" for o in context.scene.objects if o.get("bz_gn_error")]
        for n in notes:
            self.report({"WARNING"}, n)
        on = [o for o in context.scene.objects if store.is_native(o) and is_enabled(o)]
        self.report({"INFO"}, f"Evaluated surface {'on' if self.enable else 'off'} for {len(on)} creature(s)"
                              + (f"; {len(notes)} refused" if notes else ""))
        return {"FINISHED"}


CLASSES = (BZ_OT_gn_surface,)


def register_handlers() -> None:
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister_handlers() -> None:
    while _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)


def evaluated(obj, depsgraph=None):
    """``(positions, pid, u, v)`` of the evaluated surface points, read off the depsgraph.

    Through ``bpy.data.meshes.new_from_object``: in EDIT MODE ``obj.evaluated_get(dg).data`` hands back
    the edit cage's own datablock (the node result's attributes are not on it -- measured), while a
    new_from_object copy carries the whole evaluated mesh in both modes."""
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    tmp = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    try:
        n = len(tmp.vertices)
        pos = np.empty(n * 3, dtype=np.float64)
        tmp.vertices.foreach_get("co", pos)
        out = [pos.reshape(-1, 3)]
        for name, dt in ((ATTR_PID, np.int32), (ATTR_U, np.float64), (ATTR_V, np.float64)):
            at = tmp.attributes.get(name)
            if at is None:
                raise KeyError(f"the evaluated mesh has no {name!r} attribute "
                               f"(has {[a.name for a in tmp.attributes]})")
            buf = np.empty(n, dtype=dt)
            at.data.foreach_get("value", buf)
            out.append(buf)
        return tuple(out)
    finally:
        bpy.data.meshes.remove(tmp)
