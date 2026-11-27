"""Hard edges and materials of a creature: Blender's Mark Sharp edges and the faces' material slots, read at export into the
per-patch smoothing group (``store.KEY_PATCH_TYPE``) and material (``store.KEY_PATCH_MAT``) tables.

What the game does with them (decompiled, ``decomp/ic/src``):

* A patch's ``type`` is a 32-bit smoothing-group mask (Shared/Cross/RenderAnim/PatchMesh.cpp:300-330, ``HandlePRIM``: ``iShader``,
  ``smGroup``, 16 control points, uvs). The mesh builder pairs the two patches of a shared SIDE (``BuildEdgeAdjacency``,
  PatchMesh.cpp:1669-1745): the side is *smooth* when the SECOND patch's group is not 0 (line 1715 reads ``B & B``, not ``A & B``:
  the two masks need not overlap; measured against the Object Editor's vertex counts, RE_LOG 2026-11-15), and its display vertices
  are shared when it is smooth and both patches use one shader with equal uvs. ``WeldLodNormals`` (PatchMesh.cpp:2104-2178) then
  averages the normals along every smooth side (:2146-2153, the points between the two corners, at detail levels above 0).
  The CORNERS are different: at a shared corner the patches are split into smoothing components -- patches whose masks overlap,
  transitively -- and each component gets its own vertex and normal (``BuildCornerAdjacency``, PatchMesh.cpp:1749-1908, the
  component loop :1822-1850; ``WeldLodNormals`` :2156-2175). So two patches whose masks share no bit are creased at the
  corners they share; the points between the corners stay smooth (at detail levels above 0) because the side is still smooth.
  A mask of 0 is never given a component and ``WeldLodNormals`` then indexes its stack arrays with the missing number
  (``ICParse.validate.check_model`` refuses it): this module never writes 0.
* A combination never takes a part's smoothing groups. ``CombinerImp`` writes each combined patch's control points, uvs and
  shader and never its group (CombinerImp.cpp:5196-5220; the ``CombinerPatchInfo`` it copies from has no group field,
  :2820-2835). ``ResizePatches`` (PatchMesh.cpp:695, called at CombinerImp.cpp:5191) is a plain vector resize that copies a default
  ``Patch`` (PatchMeshObject.h:43-51), so every combined patch carries the same word the default ``Patch`` had (RE_LOG 2026-11-15:
  uninitialised in retail; the value was not measured). So a hard edge is the creature's own look: how a combination shades there
  is not known from the code (a non-zero word would smooth it, ``combotest`` writes group 1).
* A patch's material is a slot of the creature's material table. A combined creature takes the torso limb's first patch's shader and
  paints the combined skin on it (CombinerImp.cpp:5286-5386); ``ICParse.validate.material_layout_problems`` warns when a layout
  would put that skin in the wrong place.

How it works here. The mesh of a creature has one corner quad per patch (``patchsel``): the edge between two quads is the SIDE two
patches share, and Blender's *sharp edge* flag on it says "hard". At export (and before every topology tool, so a tool never
forgets a crease) :func:`commit` reads the flags and the faces' material slots and writes the two tables:

* a creature whose mesh has no sharp edge and was never shown its creases is left alone (an untouched creature is byte-identical);
* otherwise the sharp edges say which shared sides are hard. The first time they are used (``adopt_stored_creases``: a Mark Sharp in
  plain Blender, Make Hard / Make Smooth, Show Hard Edges, or an export) the creases the FILE already has are put on the mesh next to
  the modder's marks, so marking one edge never removes the stock creases. When that differs from what the stored groups already say (two patches
  with no bit in common = hard), only the changed sides are repaired (``assign_groups``): bits come off or go onto the patches
  at those sides, and every other patch keeps its mask exactly;
* the faces' material slots are read when the mesh has material slots (:func:`show_materials` makes them from the creature's own
  table); a slot number past the table is refused.

``seams``, ``derive_sharp`` and ``assign_groups`` are pure (numpy only).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import bpy
except ImportError:                      # the pure functions only
    bpy = None

try:
    from . import topoops
except ImportError:                      # imported top-level (a pure test puts ic_blender/ on sys.path)
    import topoops  # type: ignore[no-redef]

KEY_SHARP = "bz_sharp_authored"          # set once the creature's creases were shown in Blender: its flags are then the truth
MAX_GROUP_BITS = 32
FACE_SLOTS = (0, 12, 15, 3)              # patchsel.FACE_SLOTS (the corner quad's winding), repeated so the pure part needs no bpy


def _faced_rows(P) -> set:
    """Rows that have a corner quad: four different corner points (``patchsel.face_rows``)."""
    c = np.asarray(P, dtype=np.int64).reshape(-1, 16)[:, list(FACE_SLOTS)]
    return {int(r) for r, q in enumerate(c) if len(set(q.tolist())) == 4}


# ------------------------------------------------------------------------------------------------------ pure: groups
def seams(patches) -> Dict[Tuple[int, int], Tuple[int, int]]:
    """``{(a, b): (row, row)}`` for every side exactly two patches share (a < b: its two corner points), where both
    patches have a corner quad and no other pair of patches shares the same corners -- the sides whose Blender edge is
    unambiguous, and so the only ones a sharp flag can speak for."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    faced = _faced_rows(P)
    found: Dict[Tuple[int, int], list] = {}
    for key, users in topoops.side_table(P).items():
        if len(users) != 2 or key[0] == key[3]:
            continue
        (r1, _k1), (r2, _k2) = users
        if r1 == r2 or r1 not in faced or r2 not in faced:
            continue
        found.setdefault((min(key[0], key[3]), max(key[0], key[3])), []).append((r1, r2))
    return {pair: v[0] for pair, v in found.items() if len(v) == 1}


def _bits(mask) -> int:
    """A mask as the mesh builder would read it; 0 reads as group 1 here (the stock default: a real 0 is refused by Validate)."""
    return (int(mask) & 0xFFFFFFFF) or 1


def derive_sharp(patches, masks) -> set:
    """The seams the stored groups make hard: the two patches share no group bit."""
    m = np.asarray(masks, dtype=np.int64).reshape(-1)
    return {pair for pair, (a, b) in seams(patches).items() if _bits(m[a]) & _bits(m[b]) == 0}


def assign_groups(patches, masks, want) -> Tuple[np.ndarray, dict]:
    """``(masks, info)``: the smoothing groups that make exactly the seams in ``want`` (a set of corner pairs) hard.

    A smooth seam needs its two patches to share a group bit, a hard seam needs them to share none. The stored ``masks``
    already satisfy that for every seam whose state is not changing, so only the changing seams are repaired, one by one,
    touching as few patches as it takes (a stock creature's creases often do not cut the surface in two, so a "one group per
    smooth region" rewrite would not do, and it would rewrite most of the creature):

    * a seam that must become hard while its patches share bits: the shared bits are taken off the patch whose other smooth
      seams lose the fewest sharings (ties: fewer seams, then the later patch); every smooth seam of that patch that now
      shares nothing is repaired as below;
    * a seam that must be smooth while its patches share nothing: both patches get one more bit, the lowest bit no patch
      across a hard seam of either of them holds (so a bit is reused wherever it cannot touch a hard seam);
    * a patch left with no bit gets the lowest one its hard neighbours do not hold.

    Raises ``ValueError`` when no bit is free. ``info``: ``changed`` (seams that changed), ``rewritten`` (patches whose mask
    changed)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    orig = np.asarray(masks, dtype=np.int64).reshape(-1)
    table = seams(P)
    want = {pair for pair in want if pair in table}
    changed = want ^ derive_sharp(P, orig)
    info = {"changed": len(changed), "rewritten": 0}
    if not changed:
        return orig.copy(), info
    work = [_bits(x) for x in orig.tolist()]
    seam_of: Dict[int, list] = {}                          # patch -> [(other patch, is hard)]
    for pair, (a, b) in table.items():
        seam_of.setdefault(a, []).append((b, pair in want))
        seam_of.setdefault(b, []).append((a, pair in want))

    def hard_nbrs(p):
        return [q for q, hard in seam_of.get(p, ()) if hard]

    def smooth_nbrs(p):
        return [q for q, hard in seam_of.get(p, ()) if not hard]

    def lost(s, partner, share):                           # smooth seams of s that would share nothing without ``share``
        return sum(1 for r in smooth_nbrs(s) if r != partner and not (work[s] & ~share & work[r]))

    def fresh(holders):
        """The lowest bit that no patch across a hard seam of a holder has (so adding it to all holders breaks no hard seam)."""
        taken = 0
        for h in holders:
            for t in hard_nbrs(h):
                taken |= work[t]
        for b in range(MAX_GROUP_BITS):
            if not (taken >> b) & 1:
                return 1 << b
        raise ValueError("every smoothing group is already used by the patches around the hard edges: make fewer "
                         "edges hard")
    queue = [table[pair] + (pair in want,) for pair in sorted(changed)]
    steps = 0
    while queue:
        steps += 1
        if steps > 50 * (len(changed) + 10) + 10 * len(P):
            raise ValueError("the smoothing groups did not settle: make fewer edges hard")
        p, q, hard = queue.pop(0)
        share = work[p] & work[q]
        if hard and share:
            s = min((p, q), key=lambda x: (lost(x, p if x == q else q, share), len(seam_of.get(x, ())), -x))
            work[s] &= ~share
            queue += [(s, r, False) for r in smooth_nbrs(s)]
            if not work[s]:
                queue.append((s, s, False))              # a patch with no bit left: see below
        elif not hard and not share:
            if p == q:
                work[p] |= fresh([p])
                continue
            f = fresh([p, q])
            work[p] |= f
            work[q] |= f
    out = np.array(work, dtype=np.int64)
    out = np.where(np.array([_bits(x) for x in orig.tolist()]) == out, orig, out)       # an untouched mask stays as stored
    info["rewritten"] = int((out != orig).sum())
    return out, info


# ------------------------------------------------------------------------------------------------------ Blender side
def _mesh_edges(obj):
    """``(pairs {(a, b): edge index}, sharp (E,) bool)`` of the object's mesh as stored (edit-mode changes flushed)."""
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    me = obj.data
    ne = len(me.edges)
    ev = np.empty(ne * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", ev)
    ev = ev.reshape(-1, 2)
    sharp = np.zeros(ne, dtype=bool)
    me.edges.foreach_get("use_edge_sharp", sharp)
    pairs = {(int(min(a, b)), int(max(a, b))): i for i, (a, b) in enumerate(ev.tolist())}
    return pairs, sharp


def sharp_pairs(obj, patches=None) -> set:
    """The seams Blender marks sharp on this creature's mesh."""
    from . import store
    P = store.patches(obj) if patches is None else patches
    pairs, sharp = _mesh_edges(obj)
    return {pair for pair in seams(P) if pair in pairs and sharp[pairs[pair]]}


def authored(obj) -> bool:
    """Whether the mesh's sharp flags are the truth for the creature's smoothing groups: they were shown, or any is set."""
    if obj.get(KEY_SHARP):
        return True
    _pairs, sharp = _mesh_edges(obj)
    return bool(sharp.any())


def set_sharp_flags(obj, sharp_set) -> None:
    """Mark exactly the seams in ``sharp_set`` sharp on the mesh (every other shared side smooth), in either mode."""
    from . import store
    tab = seams(store.patches(obj))
    me = obj.data
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(me)
        for e in bm.edges:
            a, b = e.verts[0].index, e.verts[1].index
            pair = (min(a, b), max(a, b))
            if pair in tab:
                e.smooth = pair not in sharp_set
        bmesh.update_edit_mesh(me)
        return
    pairs, sharp = _mesh_edges(obj)
    for pair in tab:
        if pair in pairs:
            sharp[pairs[pair]] = pair in sharp_set
    me.edges.foreach_set("use_edge_sharp", sharp)
    me.update()


def adopt_stored_creases(obj) -> int:
    """The first time the mesh's sharp flags speak for the creature: put the creases the FILE already has on the mesh, next to
    any the modder marked meanwhile, so that taking the flags as the truth keeps them. Does nothing once the creature is
    authored (its flags are the truth, edited by the modder). Returns how many of the file's creases were not marked yet."""
    from . import store
    if obj.get(KEY_SHARP):
        return 0
    P = store.patches(obj)
    masks = store.patch_type(obj)
    stored = derive_sharp(P, masks) if len(masks) == len(P) else set()
    marked = sharp_pairs(obj, P)
    set_sharp_flags(obj, stored | marked)
    obj[KEY_SHARP] = 1
    return len(stored - marked)


def commit_sharp(obj) -> Optional[str]:
    """Write the smoothing groups the sharp edges ask for into the creature's table. ``None`` when nothing needed to
    change, else a plain sentence about what changed. ``ValueError`` (plain words) when it cannot be done."""
    from . import store
    if not authored(obj):
        return None
    adopt_stored_creases(obj)                 # sharp edges marked in plain Blender never replace the file's own creases
    P = store.patches(obj)
    masks = store.patch_type(obj)
    if len(masks) != len(P):
        return None
    new, info = assign_groups(P, masks, sharp_pairs(obj, P))
    if not info["changed"]:
        return None
    store.set_patch_type(obj, new)
    try:
        from . import draw
        draw.invalidate(obj.name)
    except Exception:
        pass
    return (f"hard edges: {info['changed']} shared side(s) changed, {info['rewritten']} patches got new smoothing groups")


def show_creases(obj) -> int:
    """Mark sharp, on the mesh, every shared side the stored groups make hard (the modder's own marks stay), and remember the
    creature is now authored by its flags. Returns how many sides are hard. Once the creature is authored its flags are the truth
    and are left as they are."""
    from . import store
    adopt_stored_creases(obj)
    return len(sharp_pairs(obj, store.patches(obj)))


def _faces(obj):
    """``(patch row of each corner quad, material slot of each face)``."""
    from . import patchsel, store
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    me = obj.data
    rows = patchsel.face_rows(store.patches(obj))
    mi = np.zeros(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    return rows, mi


def commit_materials(obj) -> Optional[str]:
    """Write the faces' material slots into the creature's per-patch material table, when the mesh has material slots.
    ``None`` when nothing changed. ``ValueError`` for a slot the creature's material table has no entry for."""
    from . import store
    if len(obj.data.materials) == 0:
        return None
    names = store.material_slots(obj)[0]
    rows, mi = _faces(obj)
    if len(mi) != len(rows):
        return None                           # the pool guard reports a mesh that lost faces
    if len(mi) and int(mi.max()) >= len(names):
        raise ValueError(f"a patch uses material slot {int(mi.max()) + 1}, but the creature has {len(names)} materials "
                         f"({', '.join(names)}): give it one of those, a creature's materials are the file's own")
    old = store.patch_mat(obj)
    if len(old) != len(store.patches(obj)):
        return None
    new = old.copy()
    new[rows] = mi
    if np.array_equal(new, old):
        return None
    store.set_patch_mat(obj, new)
    return f"materials: {int((new != old).sum())} patches changed material"


def push_materials(obj) -> None:
    """Set every corner quad's material slot from the creature's table (after a tool rebuilt the faces)."""
    from . import patchsel, store
    me = obj.data
    if len(me.materials) == 0:
        return
    rows = patchsel.face_rows(store.patches(obj))
    pm = store.patch_mat(obj)
    if len(rows) != len(me.polygons) or len(pm) != len(store.patches(obj)):
        return
    vals = np.minimum(pm[rows], len(me.materials) - 1).astype(np.int32)
    if obj.mode == "EDIT":                    # the edit mesh is the truth in Edit Mode: a write to the mesh would be undone
        import bmesh
        bm = bmesh.from_edit_mesh(me)
        bm.faces.ensure_lookup_table()
        for i, f in enumerate(bm.faces):
            f.material_index = int(vals[i])
        bmesh.update_edit_mesh(me)
        return
    me.polygons.foreach_set("material_index", vals)
    me.update()


def show_materials(obj) -> int:
    """Give the mesh one material slot per entry of the creature's material table (named after it, in the table's colour) and
    set each corner quad to its patch's material. Returns the number of slots. A mesh that has slots keeps them."""
    from . import store
    me = obj.data
    if len(me.materials) == 0:
        names, _tex, colors = store.material_slots(obj)
        for i, nm in enumerate(names):
            mat = bpy.data.materials.new(f"{obj.name}.{nm}")
            if i < len(colors):
                mat.diffuse_color = (float(colors[i][0]), float(colors[i][1]), float(colors[i][2]), 1.0)
            me.materials.append(mat)
    push_materials(obj)
    return len(me.materials)


def commit(obj) -> List[str]:
    """Both tables, from the mesh. The sentences to report (empty when nothing changed). ``ValueError`` in plain words."""
    out = []
    for fn in (commit_sharp, commit_materials):
        said = fn(obj)
        if said:
            out.append(said)
    return out


def before_rebuild(obj) -> None:
    """A topology tool is about to read the tables and rebuild the faces and edges: keep what the mesh says first."""
    try:
        commit(obj)
    except ValueError:
        pass                                  # the export reports it; the tool must not be stopped by it


def after_rebuild(obj) -> None:
    """The tool rebuilt the mesh from the tables: put the creases and material slots back on it."""
    from . import store
    if obj.get(KEY_SHARP):
        set_sharp_flags(obj, derive_sharp(store.patches(obj), store.patch_type(obj)))
    push_materials(obj)


# ------------------------------------------------------------------------------------------------------ operators, panel
def _selected_seam_edges(obj):
    """The bmesh edges a Make Hard / Make Smooth press is about: the outline of the selected patches when patches (faces)
    are selected, else the selected edges; only sides two patches share. ``(bm, [edges])``."""
    import bmesh
    from . import store
    bm = bmesh.from_edit_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    tab = seams(store.patches(obj))

    def shared(e):
        a, b = e.verts[0].index, e.verts[1].index
        return (min(a, b), max(a, b)) in tab
    edges = [e for e in bm.edges if shared(e)]
    if any(f.select and not f.hide for f in bm.faces):
        outline = [e for e in edges if sum(1 for f in e.link_faces if f.select) == 1]
        if outline:
            return bm, outline
    return bm, [e for e in edges if e.select and not e.hide]


if bpy is not None:
    from bpy.props import BoolProperty, EnumProperty

    def _ops():
        from . import ops
        return ops

    class BZ_OT_hard_edges(bpy.types.Operator):
        """Make the shared sides around the selected patches (or the selected edges) hard, so the shading is creased there, or smooth again"""
        bl_idname = "bz.hard_edges"
        bl_label = "Hard Edges"
        bl_options = {"REGISTER", "UNDO"}

        hard: BoolProperty(name="Hard", default=True, description="On: make the edges hard. Off: make them smooth")

        @classmethod
        def description(cls, context, properties):
            if properties.hard:
                return ("Make the edges around the selected patches (or the selected edges) hard: the shading is creased at the "
                        "corners where the patches meet. The game does not copy hard edges into a combination of this creature")
            return "Make the edges around the selected patches (or the selected edges) smooth again"

        @classmethod
        def poll(cls, context):
            return _ops()._guard_poll(cls, context, edit=True)

        def execute(self, context):
            import bmesh
            obj = context.active_object
            adopt_stored_creases(obj)           # the file's own creases first: Make Hard / Make Smooth change only what is selected
            bm, edges = _selected_seam_edges(obj)
            if not edges:
                self.report({"ERROR"}, "Select patches (Face select) or edges first. Only a side that two patches share can be "
                                       "hard: the outer edge of the creature cannot")
                return {"CANCELLED"}
            for e in edges:
                e.smooth = not self.hard
            bmesh.update_edit_mesh(obj.data)
            obj[KEY_SHARP] = 1
            try:
                said = commit(obj)
            except ValueError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            self.report({"INFO"}, f"{len(edges)} edge(s) made {'hard' if self.hard else 'smooth'}"
                                  + (f" ({'; '.join(said)})" if said else " (already so)"))
            return {"FINISHED"}

    class BZ_OT_hard_edges_show(bpy.types.Operator):
        """Mark on the mesh (as Blender's sharp edges) the sides the creature's smoothing groups make hard. Edges you marked yourself stay"""
        bl_idname = "bz.hard_edges_show"
        bl_label = "Show Hard Edges"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            return _ops()._guard_poll(cls, context)

        def execute(self, context):
            obj = context.active_object
            n = show_creases(obj)
            self.report({"INFO"}, f"{n} hard edge(s) are marked (cyan in Edit Mode). Mark or clear more with Make Hard / Make "
                                  "Smooth; Export keeps what you leave")
            return {"FINISHED"}

    class BZ_OT_materials_show(bpy.types.Operator):
        """Give the mesh the creature's material slots, so patches can be given a material in Blender's material list"""
        bl_idname = "bz.materials_show"
        bl_label = "Show Material Slots"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            return _ops()._guard_poll(cls, context)

        def execute(self, context):
            obj = context.active_object
            n = show_materials(obj)
            self.report({"INFO"}, f"{n} material slots: select patches in Edit Mode, pick a slot in the Material tab and press "
                                  "Assign (or use Set Material). Export writes them")
            return {"FINISHED"}

    def _material_items(self, context):
        from . import store
        obj = context.active_object
        names = store.material_slots(obj)[0] if obj is not None and store.is_native(obj) else []
        return [(str(i), f"{i + 1}  {nm}", "") for i, nm in enumerate(names)] or [("0", "(none)", "")]

    class BZ_OT_set_patch_material(bpy.types.Operator):
        """Give the selected patches one of the creature's materials"""
        bl_idname = "bz.set_patch_material"
        bl_label = "Set Material"
        bl_options = {"REGISTER", "UNDO"}

        slot: EnumProperty(name="Material", items=_material_items, description="The material the patches take")

        @classmethod
        def poll(cls, context):
            return _ops()._guard_poll(cls, context, edit=True)

        def invoke(self, context, event):
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            from . import patchsel, store
            obj = context.active_object
            rows = patchsel.selected_rows(obj, store.patches(obj))
            if not rows:
                self.report({"ERROR"}, "Select patches first (Face select)")
                return {"CANCELLED"}
            show_materials(obj)
            pm = store.patch_mat(obj).copy()
            pm[rows] = int(self.slot)
            store.set_patch_mat(obj, pm)
            push_materials(obj)
            names = store.material_slots(obj)[0]
            self.report({"INFO"}, f"{len(rows)} patch(es) now use '{names[int(self.slot)]}'")
            return {"FINISHED"}

    CLASSES = (BZ_OT_hard_edges, BZ_OT_hard_edges_show, BZ_OT_materials_show, BZ_OT_set_patch_material)
else:
    CLASSES = ()


def draw_box(layout, obj) -> None:
    """The Hard Edges and Materials box of a creature's panel."""
    box = layout.box()
    box.label(text="Hard Edges and Materials", icon="MOD_EDGESPLIT")
    if obj.mode == "EDIT":
        r = box.row(align=True)
        r.operator(BZ_OT_hard_edges.bl_idname, text="Make Hard").hard = True
        r.operator(BZ_OT_hard_edges.bl_idname, text="Make Smooth").hard = False
        box.label(text="Select patches (Face select) or edges first.")
    box.operator(BZ_OT_hard_edges_show.bl_idname, icon="EDGESEL")
    box.label(text="A hard edge creases the shading at the corners")
    box.label(text="where two patches meet; between corners it stays")
    box.label(text="smooth. The game does not copy hard edges into a")
    box.label(text="combination of this creature.")
    if obj.mode == "EDIT" and len(obj.data.materials):
        box.operator(BZ_OT_set_patch_material.bl_idname, icon="MATERIAL")
    else:
        box.operator(BZ_OT_materials_show.bl_idname, icon="MATERIAL")
    box.label(text="Each patch has one material (a texture); the")
    box.label(text="slots are the creature's own. The first torso patch's")
    box.label(text="material is the one a combination repaints.")
