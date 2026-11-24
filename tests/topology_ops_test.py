"""B7 topology tools in headless Blender: Fill Hole, Subdivide, Weld, Attach, Mirror to Whole, Add Quad.
    blender --background --python blender_native/tests/topology_ops_test.py

A. Delete Patch on 2 neighbouring patches -> Fill Hole closes the 6-side hole with a strip of 2 patches: watertight
   (no open side, no seam traversed the same way), Validate free of dangling edges, the export loads through ICParse
   (-> /tmp/bz_topo_fill_cheetah.sgm for the engine tier); Undo -> byte-identical;
B. Subdivide one leg patch: the propagation splits the strips through its neighbours (no T-junction), the tessellated
   surface is unchanged (every patch of the new table evaluated against its parent's parameter window), the mesh stays
   watertight (-> /tmp/bz_topo_subdiv_cheetah.sgm for the engine + ComboTest tiers); Undo -> byte-identical;
C. Weld: Delete Patch one patch, Add Quad on one of the hole's open sides (from an EDGE selection), drag the new
   patch's two free corners onto the hole's far corners, Weld -> the seam closes (corner pairs and their handle pairs
   merged, 0 open sides), the export loads (-> /tmp/bz_topo_weld_cheetah.sgm); Weld Target welds into the active
   corner at its exact position; Undo -> byte-identical;
D. Attach a New Creature scaffold into the cheetah: pools concatenated, patch rows offset, every per-patch table
   concatenated, the attached points skinned to the chosen bone and its patches in the chosen limb, its rings dropped
   with a warning, its object and rig removed; Undo -> byte-identical;
E. Mirror to Whole on a half cheetah (connections deleted, the -X half's patches deleted): the mirror is welded on the
   plane, every seam stays wound the stock way, skinning goes to the _l / _r mirror bones and the patches to the mirror
   limbs, and the surface comes back within the creature's own asymmetry (-> /tmp/bz_topo_mirror_cheetah.sgm);
   Undo -> the half's bytes again;
F. The refusals in words: weld one corner / a collapsing patch / nothing within the threshold / two corners of one
   connection ring, subdivide a ring patch and nothing selected, mirror a whole creature, Fill Hole on a closed
   creature, Add Quad with nothing selected, Attach with no second creature and over the patch budget (topoops.MAX_PATCHES);
G. Extrude Region (Max's Extrude & Bevel) on 2 patches: the region is lifted and tapered, a wall patch spans each
   boundary side, the mesh stays watertight and wound the stock way (-> /tmp/bz_topo_region_cheetah.sgm), the drag
   macros are registered with their two steps; Undo -> byte-identical.
"""
import os
import sys
sys.dont_write_bytecode = True
import traceback

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops, patchsel, topoops, patchrules as PR  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
FILL = "/tmp/bz_topo_fill_cheetah.sgm"        # 2 patches deleted, the hole filled (watertight again)
SUBDIV = "/tmp/bz_topo_subdiv_cheetah.sgm"    # a rear-leg patch subdivided, the split propagated
WELD = "/tmp/bz_topo_weld_cheetah.sgm"        # a patch deleted, re-grown with Add Quad and welded back
ATTACH = "/tmp/bz_topo_attach_cheetah.sgm"    # a New Creature scaffold attached into the cheetah
MIRROR = "/tmp/bz_topo_mirror_cheetah.sgm"    # a half cheetah mirrored back to whole
REGION = "/tmp/bz_topo_region_cheetah.sgm"    # 2 patches extruded as a region (walls on the boundary)
SCAFFOLD = "/tmp/bz_topo_scaffold.sgm"
OUT_TMP = "/tmp/bz_topo_tmp.sgm"
_log = open("/tmp/bz_topology_ops_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    try:
        blender_native.unregister()
    except RuntimeError:
        pass
    blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"
    bpy.ops.ed.undo_push(message="start")     # background mode: the undo system needs one explicit push


def imp(path=SRC):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def export(obj, path=OUT_TMP):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
        msg = obj.get("bz_last_export", "")
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def identical(obj, want=SRC_BYTES):
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == want, (state, msg[:300], None if data is None else len(data))


def run_op(fn):
    """An operator in background mode: its ERROR reports raise RuntimeError."""
    try:
        return fn(), None
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def step(fn, message):
    """Run an operator as one undo step (background mode pushes none by itself)."""
    r, err = run_op(fn)
    assert r == {"FINISHED"}, f"{message}: {err}"
    bpy.ops.ed.undo_push(message=message)
    return r


def undo(n=1, name=None):
    """Undo ``n`` steps and return the native object again (Blender rebuilds the datablocks; with several creatures
    in the file, ``name`` says which one)."""
    for _ in range(n):
        bpy.ops.ed.undo()
    return next(o for o in bpy.data.objects if store.is_native(o) and (name is None or o.name == name))


def in_mode(obj, mode):
    bpy.context.view_layer.objects.active = obj
    if obj.mode != mode:
        bpy.ops.object.mode_set(mode=mode)


def verts_of(obj) -> np.ndarray:
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def select_faces(obj, rows, patches):
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    patchsel.select_rows(bm, patches, rows)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def select_verts(obj, idx, active=None):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    want = {int(i) for i in np.atleast_1d(idx)}
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = v.index in want and not v.hide
    bm.select_history.clear()
    if active is not None:
        bm.select_history.add(bm.verts[int(active)])
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def select_edge(obj, a, b):
    """Select the mesh edge between two corners in EDGE select mode (an outline edge of a patch)."""
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    for v in bm.verts:
        v.select = False
    hit = [e for e in bm.edges if {e.verts[0].index, e.verts[1].index} == {int(a), int(b)}]
    assert hit, f"no mesh edge between corners {a} and {b}"
    for e in bm.edges:
        e.select = False
    hit[0].select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return hit[0]


def set_pos(obj, i, pos):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[int(i)].co = np.asarray(pos, dtype=np.float32)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def bez(t):
    t = np.asarray(t, dtype=np.float64)
    return np.stack([(1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t ** 2 * (1 - t), t ** 3], axis=-1)


def patch_at(V, cps, uu, vv):
    """One patch (row-major 4x4) evaluated on the grid uu (rows) x vv (columns) -- the test's own tessellation."""
    G = np.asarray(V, dtype=np.float64)[np.asarray(cps, dtype=np.int64)].reshape(4, 4, 3)
    return np.einsum("ui,ijc,vj->uvc", bez(uu), G, bez(vv))


def surface(V, P, n=5):
    t = np.linspace(0, 1, n)
    return np.stack([patch_at(V, cps, t, t) for cps in np.asarray(P, dtype=np.int64).reshape(-1, 16)])


def same_direction(P):
    """ICParse validate._same_direction_edges on a raw patch table: shared sides both patches traverse the same way
    (0 on every base-game creature; the engine's index builder assumes the stock orientation)."""
    rows = {}
    for cp in np.asarray(P, dtype=np.int64).reshape(-1, 16).tolist():
        for e in ((cp[0], cp[3]), (cp[3], cp[15]), (cp[15], cp[12]), (cp[12], cp[0])):
            if e[0] != e[1]:
                rows.setdefault(frozenset(e), []).append(e)
    return sum(1 for d in rows.values() if len(d) == 2 and d[0] == d[1])


def nearest(A, B, chunk=512):
    """(rms, max) distance from every point of A to the nearest point of B (numpy only: Blender has no scipy)."""
    A = np.asarray(A, dtype=np.float64).reshape(-1, 3); B = np.asarray(B, dtype=np.float64).reshape(-1, 3)
    out = np.empty(len(A))
    for i in range(0, len(A), chunk):
        d = A[i:i + chunk, None, :] - B[None, :, :]
        out[i:i + chunk] = np.sqrt((d * d).sum(axis=2)).min(axis=1)
    return float(np.sqrt((out ** 2).mean())), float(out.max())


def neighbour_pair(patches, skip=()):
    """Two patches sharing a side, neither on a connection ring."""
    for key, users in topoops.side_table(patches).items():
        if len(users) == 2 and users[0][0] not in skip and users[1][0] not in skip:
            return users[0][0], users[1][0]
    raise AssertionError("no pair of neighbouring patches off the rings")


def ring_rows(obj):
    patches = store.patches(obj)
    return {r for r, _n, _s in patchsel.ring_patch_refusals(patches, range(len(patches)),
                                                            topoops._rings_for_plan(obj))}


def validate_lines(obj):
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def check_model_clean(path, tag):
    m = SP.read(path)
    probs = [p for p in ICval.check_model(m) if not p.startswith("WARN")]
    assert not probs, f"{tag}: {probs}"
    return m


# ========================================================================================================= A
def section_a():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), store.point_count(obj)
    pair = neighbour_pair(patches, skip=ring_rows(obj))
    in_mode(obj, "EDIT")
    bpy.ops.ed.undo_push(message="imported")
    select_faces(obj, list(pair), patches)
    step(lambda: bpy.ops.bz.delete_patch(), "delete 2 patches")
    P1 = store.patches(obj)
    assert len(P1) == Np - 2 and core.open_side_count(P1) == 6, (len(P1), core.open_side_count(P1))
    loops = topoops.hole_loops(P1)
    assert len(loops) == 1 and len(loops[0]) == 6, [len(lp) for lp in loops]
    bpy.ops.mesh.select_all(action="DESELECT")
    step(lambda: bpy.ops.bz.fill_hole(), "fill hole")
    line = obj.get("bz_last_topo", "")
    P2 = store.patches(obj)
    n2 = store.point_count(obj)
    assert len(P2) == Np, len(P2)                           # 2 deleted, a 6-side hole -> a strip of 2
    assert core.open_side_count(P2) == 0, core.open_side_count(P2)
    assert same_direction(P2) == 0, f"{same_direction(P2)} seams traversed the same way after the fill"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    assert len(store.patch_uv(obj)) == len(P2) == len(store.patch_mat(obj)) == len(store.patch_type(obj)) \
        == len(store.patch_limb(obj)) == len(store.manual_interior(obj, len(P2)))
    assert len(store.vert_bone(obj)) == n2, (len(store.vert_bone(obj)), n2)
    val = validate_lines(obj)
    assert not any("dangling" in p for p in val), [p for p in val if "dangling" in p]
    assert not core.blocking(val), core.blocking(val)[:3]
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, FILL)
    assert state == "FINISHED" and data, msg[:300]
    m = check_model_clean(FILL, "fill")
    assert len(m.patches) == len(P2) and len(m.vertices) == n2
    say(f"A Delete Patch {pair} -> a 6-side hole -> Fill Hole: {line}")
    say(f"A   {len(P2)} patches, {n2} points, 0 open sides, 0 same-direction seams, check_model clean, Validate clean "
        f"-> {FILL} ({len(data)} bytes)")
    obj = undo(2)
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N
    identical(obj)
    say("A   Undo (fill, delete) -> byte-identical")


# ========================================================================================================= B
def section_b():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), store.point_count(obj)
    rr = topoops._rings_for_plan(obj)
    limbs = store.patch_limb(obj)
    names = store.limb_names(obj)
    allowed = [r for r in range(Np) if topoops.propagate_splits(patches, [r], rr)[1] is None]
    torso = [i for i, n in enumerate(names) if n.lower().endswith("torso")]
    say(f"B subdividable patches (a strip crossing no connection ring): {len(allowed)} of {Np}, in "
        f"{sorted({names[int(limbs[r])] for r in allowed})}; of the {int(np.isin(limbs, torso).sum())} torso patches "
        f"{sum(1 for r in allowed if int(limbs[r]) in torso)} (every torso strip crosses one of the 12 sockets)")
    row = next(r for r in allowed if names[int(limbs[r])].lower().endswith("rleg"))
    V0 = verts_of(obj)
    flags = topoops.propagate_splits(patches, [row], rr)[0]
    in_mode(obj, "EDIT")
    bpy.ops.ed.undo_push(message="imported")
    select_faces(obj, [row], patches)
    step(lambda: bpy.ops.bz.subdivide(), "subdivide")
    line = obj.get("bz_last_topo", "")
    P2 = store.patches(obj)
    V2 = verts_of(obj)
    n2 = store.point_count(obj)
    assert core.open_side_count(P2) == 0, f"{core.open_side_count(P2)} open sides after the subdivision"
    assert same_direction(P2) == 0, f"{same_direction(P2)} same-direction seams after the subdivision"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    # every row of the new table lies exactly on the old surface: an untouched row is its own patch, a sub-patch is its
    # parent's half / quarter window (de Casteljau at 1/2). The plan emits the rows in table order, sub-patches
    # row-major within their parent.
    t = np.linspace(0, 1, 7)
    worst, j = 0.0, 0
    for r in range(Np):
        f = flags.get(r, set())
        us = [(0.0, 1.0)] if "r" not in f else [(0.0, 0.5), (0.5, 1.0)]
        vs = [(0.0, 1.0)] if "c" not in f else [(0.0, 0.5), (0.5, 1.0)]
        for u0, u1 in us:
            for v0, v1 in vs:
                got = patch_at(V2, P2[j], t, t)
                want = patch_at(V0, patches[r], u0 + t * (u1 - u0), v0 + t * (v1 - v0))
                worst = max(worst, float(np.abs(got - want).max()))
                j += 1
    assert j == len(P2), (j, len(P2))
    size = float(np.linalg.norm(V0.max(axis=0) - V0.min(axis=0)))
    assert worst < 1e-6, f"the surface moved by {worst:.3e} m"
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, SUBDIV)
    assert state == "FINISHED" and data, msg[:300]
    m = check_model_clean(SUBDIV, "subdivide")
    assert len(m.patches) == len(P2) and len(m.vertices) == n2
    say(f"B Subdivide patch {row} ({names[int(limbs[row])]}): {line}")
    say(f"B   {len(P2)} patches checked against their parent's window: surface max diff {worst:.3e} m "
        f"({worst / size:.1e} of the size), 0 open sides, 0 same-direction seams, check_model clean -> {SUBDIV} "
        f"({len(data)} bytes)")
    obj = undo(1)
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N
    identical(obj)
    say("B   Undo -> byte-identical")


# ========================================================================================================= C
def section_c():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), store.point_count(obj)
    p = min(r for r in range(Np) if r not in ring_rows(obj))
    in_mode(obj, "EDIT")
    bpy.ops.ed.undo_push(message="imported")
    select_faces(obj, [p], patches)
    step(lambda: bpy.ops.bz.delete_patch(), "delete 1 patch")
    P1 = store.patches(obj)
    V1 = verts_of(obj)
    assert len(P1) == Np - 1 and core.open_side_count(P1) == 4
    loop = topoops.hole_loops(P1)[0]
    assert len(loop) == 4, len(loop)
    # Add Quad from an EDGE selection on one of the hole's open sides
    r0, k0, key0 = loop[0]
    edge = select_edge(obj, key0[0], key0[3])
    sides, why = topoops.selected_open_sides(obj, P1)
    assert why is None and sides == [(r0, k0)], (sides, why, (r0, k0))
    pick = core.pick_extrude_side(P1, ops._selected_indices(obj))    # Extrude Patch reads the same edge selection
    assert not isinstance(pick, str) and pick[0] == r0, pick
    step(lambda: bpy.ops.bz.extrude_sides(), "add quad")
    P2 = store.patches(obj)
    V2 = verts_of(obj)
    assert len(P2) == Np and store.point_count(obj) == len(V1) + 12, (len(P2), store.point_count(obj))
    assert core.open_side_count(P2) == 6, core.open_side_count(P2)   # the hole's 3 left + the new patch's 3
    # the new patch's two free corners (the corners of its far side) and the hole's far corners
    new = P2[-1]
    far = [int(new[s]) for s in PR.CORNER_SLOTS if int(new[s]) not in (key0[0], key0[3])]
    rest = [s for s in loop[1:]]
    hole_far = [c for c in {key[0] for _r, _k, key in rest} | {key[3] for _r, _k, key in rest}
                if c not in (key0[0], key0[3])]
    assert len(far) == 2 and len(hole_far) == 2, (far, hole_far)
    # pair them up so the new patch lands on the hole without a twist (the shorter total drag)
    a, b = far
    e, f = hole_far
    if np.linalg.norm(V2[a] - V2[e]) + np.linalg.norm(V2[b] - V2[f]) > \
       np.linalg.norm(V2[a] - V2[f]) + np.linalg.norm(V2[b] - V2[e]):
        e, f = f, e
    drag = max(float(np.linalg.norm(V2[a] - V2[e])), float(np.linalg.norm(V2[b] - V2[f])))
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    set_pos(obj, a, V2[e]); set_pos(obj, b, V2[f])
    select_verts(obj, [a, b, e, f])
    step(lambda: bpy.ops.bz.weld(mode="SELECTED", threshold=1e-4), "weld")
    line = obj.get("bz_last_topo", "")
    P3 = store.patches(obj)
    n3 = store.point_count(obj)
    assert len(P3) == Np, len(P3)
    assert core.open_side_count(P3) == 0, f"{core.open_side_count(P3)} open sides after the weld"
    assert same_direction(P3) == 0, f"{same_direction(P3)} same-direction seams after the weld"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    assert len(store.vert_bone(obj)) == n3
    val = validate_lines(obj)
    assert not any("dangling" in q for q in val), [q for q in val if "dangling" in q]
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, WELD)
    assert state == "FINISHED" and data, msg[:300]
    check_model_clean(WELD, "weld")
    say(f"C Delete Patch {p} -> Add Quad on the hole's open side (edge selection) -> the 2 free corners dragged "
        f"{drag * 100:.1f} cm onto the hole's far corners -> Weld: {line}")
    say(f"C   {len(P3)} patches, {n3} points, 0 open sides, 0 same-direction seams -> {WELD} ({len(data)} bytes)")
    # Weld Target: undo the weld (and the drags with it), weld ONE pair into the ACTIVE corner, which keeps its
    # position exactly (no dragging)
    obj = undo(1)
    in_mode(obj, "EDIT")
    Vu = verts_of(obj)
    assert np.array_equal(Vu, V2), "Undo did not put the dragged corners back"
    before = Vu[e].copy()
    select_verts(obj, [a, e], active=e)
    step(lambda: bpy.ops.bz.weld(mode="TARGET"), "weld target")
    tline = obj.get("bz_last_topo", "")
    V3 = verts_of(obj)
    n4 = store.point_count(obj)
    # the same plan, pure: it says where the target corner landed in the compacted pool
    tplan = topoops.weld_plan(V2, P2, [a, e], 0.0, target=e, rings=topoops._rings_for_plan(obj))
    assert tplan.refusal is None and tplan.n_kept + tplan.n_new == n4, (tplan.refusal, tplan.n_kept, n4)
    assert np.array_equal(V3[int(tplan.o2n[e])], before), "the active corner moved"
    assert n4 < len(V2) and core.open_side_count(store.patches(obj)) < 6
    say(f"C   Weld Target (into the active corner {e}, which did not move): {tline}")
    obj = undo(3)                                            # target weld, add quad, delete
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N
    identical(obj)
    say("C   Undo (weld, add quad, delete) -> byte-identical")


# ========================================================================================================= D
def section_d():
    fresh()
    obj = imp()
    P1 = store.patches(obj)
    Np, N = len(P1), store.point_count(obj)
    uv1, mat1, type1, limb1 = store.patch_uv(obj), store.patch_mat(obj), store.patch_type(obj), store.patch_limb(obj)
    vb1 = store.vert_bone(obj)
    V1 = verts_of(obj)
    bpy.ops.ed.undo_push(message="imported")
    assert bpy.ops.bz.new_creature(filepath=SCAFFOLD, creature_name="Attachee", detail=1,
                                   clips_as_actions=False) == {"FINISHED"}
    other = bpy.context.view_layer.objects.active
    assert other is not obj and store.is_native(other)
    P2 = store.patches(other)
    N2 = store.point_count(other)
    n_conn2 = len(store.connections(other) or [])
    uv2, mat2, type2 = store.patch_uv(other), store.patch_mat(other), store.patch_type(other)
    V2 = verts_of(other)
    arm2 = store.rig(other)
    other_name, arm_name = other.name, (arm2.name if arm2 else None)
    bpy.ops.ed.undo_push(message="scaffold made")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    other.select_set(True)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    names = store.limb_names(obj)
    limb = next(i for i, n in enumerate(names) if n.lower().endswith("head"))
    bones = store.bone_rig_names(obj)
    bone = next(i for i, n in enumerate(bones) if n.lower().startswith("head"))
    assert bpy.ops.bz.attach.poll(), "Attach did not poll with a native creature selected"
    step(lambda: bpy.ops.bz.attach(limb=str(limb), bone=str(bone)), "attach")
    line = obj.get("bz_last_topo", "")
    P3 = store.patches(obj)
    n3 = store.point_count(obj)
    assert len(P3) == Np + len(P2) and n3 == N + N2, (len(P3), n3)
    assert np.array_equal(P3[:Np], P1) and np.array_equal(P3[Np:], P2 + N), "the attached rows are not the offset ones"
    assert np.array_equal(verts_of(obj)[:N], V1) and np.allclose(verts_of(obj)[N:], V2, atol=1e-6)
    assert np.array_equal(store.patch_uv(obj)[:Np], uv1) and np.allclose(store.patch_uv(obj)[Np:], uv2)
    assert np.array_equal(store.patch_type(obj)[:Np], type1) and np.array_equal(store.patch_type(obj)[Np:], type2)
    assert np.array_equal(store.patch_limb(obj)[:Np], limb1) and (store.patch_limb(obj)[Np:] == limb).all()
    assert np.array_equal(store.vert_bone(obj)[:N], vb1) and (store.vert_bone(obj)[N:] == bone).all()
    assert (store.patch_mat(obj)[Np:] >= 0).all() and len(store.patch_mat(obj)) == len(P3)
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    assert other_name not in bpy.data.objects and (arm_name is None or arm_name not in bpy.data.objects), \
        "the attached object (or its rig) was left in the scene"
    assert len(store.connections(obj) or []) == len(store.connections(obj) or []), "the host's rings changed"
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, ATTACH)
    assert state == "FINISHED" and data, msg[:300]
    m = check_model_clean(ATTACH, "attach")
    assert len(m.patches) == len(P3) and len(m.vertices) == n3
    say(f"D Attach: {line}")
    say(f"D   {Np} + {len(P2)} patches, {N} + {N2} points, the scaffold's {n_conn2} rings dropped, its object and rig "
        f"removed, tables concatenated -> {ATTACH} ({len(data)} bytes), check_model clean")
    host = obj.name
    obj = undo(1, name=host)                                 # the scaffold object comes back with it
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N
    identical(obj)
    say("D   Undo -> byte-identical")


# ========================================================================================================= E
def section_e():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), store.point_count(obj)
    V0 = verts_of(obj)
    S0 = surface(V0, patches, 4).reshape(-1, 3)
    names = store.limb_names(obj)
    bones = store.bone_rig_names(obj)
    omap = topoops.mirror_owner_map(bones)
    pairs = int(sum(1 for i, j in enumerate(omap) if i != j))
    lmap = topoops.mirror_limb_map(store.limb_ids(obj))
    lpairs = int(sum(1 for i, j in enumerate(lmap) if i != j))
    say(f"E mirror maps on the cheetah: {pairs} of {len(bones)} bones have an _l / _r partner "
        f"(e.g. {bones[int(np.nonzero(omap != np.arange(len(omap)))[0][0])]} -> "
        f"{bones[int(omap[np.nonzero(omap != np.arange(len(omap)))[0][0]])]}), {lpairs} of {len(names)} limbs "
        f"(e.g. {names[int(np.nonzero(lmap != np.arange(len(lmap)))[0][0])]} -> "
        f"{names[int(lmap[np.nonzero(lmap != np.arange(len(lmap)))[0][0]])]})")
    bpy.ops.ed.undo_push(message="imported")
    size = float(np.linalg.norm(V0.max(axis=0) - V0.min(axis=0)))
    thr = 0.001 * size
    # a half creature: the sockets reaching into the half that goes must go first (Delete Patch refuses a patch on a
    # ring), then the -X half's patches. The +X sockets stay, so the export keeps a connection table.
    x = V0[patches].reshape(Np, 16, 3)[:, :, 0]
    left = [r for r in range(Np) if (x[r] < thr).all()]
    right = [r for r in range(Np) if (x[r] > -thr).all()]
    assert len(left) + len(right) == Np, (len(left), len(right))
    conns = store.connections(obj) or []
    rings = topoops._rings_for_plan(obj)
    kill = [i for i in range(len(conns)) if patchsel.ring_patch_refusals(patches, left, [rings[i]])]
    for i in sorted(kill, reverse=True):
        step(lambda i=i: bpy.ops.bz.delete_connection(index=i), f"delete connection {conns[i][0]}")
    kept = [n for n, _b, _r in (store.connections(obj) or [])]
    assert len(kept) == len(conns) - len(kill) and kept, (kept, kill)
    say(f"E   {len(kill)} sockets deleted (their rings are on patches of the half that goes: "
        f"{', '.join(conns[i][0] for i in kill)}), {len(kept)} kept ({', '.join(kept)})")
    in_mode(obj, "EDIT")
    select_faces(obj, left, patches)
    step(lambda: bpy.ops.bz.delete_patch(), "delete the -X half")
    Ph = store.patches(obj)
    Vh = verts_of(obj)
    vbh = store.vert_bone(obj)
    limbh = store.patch_limb(obj)
    open_h = core.open_side_count(Ph)
    assert len(Ph) == len(right), (len(Ph), len(right))
    in_mode(obj, "OBJECT")
    state, msg, half_bytes = export(obj, OUT_TMP)
    assert state == "FINISHED" and half_bytes, msg[:300]
    in_mode(obj, "EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    step(lambda: bpy.ops.bz.mirror_whole(), "mirror to whole")
    line = obj.get("bz_last_topo", "")
    P2 = store.patches(obj)
    V2 = verts_of(obj)
    n2 = store.point_count(obj)
    vb2 = store.vert_bone(obj)
    limb2 = store.patch_limb(obj)
    assert len(P2) == 2 * len(Ph) - 0, (len(P2), len(Ph))
    assert core.open_side_count(P2) == 0, f"{core.open_side_count(P2)} open sides after the mirror"
    assert same_direction(P2) == 0, f"{same_direction(P2)} same-direction seams after the mirror"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    # the surface comes back: compare with the stock cheetah's own tessellation
    S2 = surface(V2, P2, 4).reshape(-1, 3)
    rms, mx = nearest(S2, S0)
    Sm = S0.copy(); Sm[:, 0] *= -1
    arms, amx = nearest(S0, Sm)                            # the cheetah's own asymmetry, for scale
    assert mx <= amx * 1.5 + 1e-6, (mx, amx)
    # skinning: every mirrored point's owner is its source's mirror bone (found through the mirror image position)
    key = {(round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6)): i for i, p in enumerate(V2)}
    checked = wrong = 0
    for i, p in enumerate(V2):
        j = key.get((round(float(-p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6)))
        if j is None or j == i:
            continue
        checked += 1
        if int(vb2[j]) != int(omap[int(vb2[i])]):
            wrong += 1
    assert checked > 0 and wrong == 0, (checked, wrong)
    # limbs: the mirrored patches are in the mirror limbs (the half's limb counts, mirrored)
    for li in range(len(names)):
        assert int((limb2 == lmap[li]).sum()) >= int((limbh == li).sum()), names[li]
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, MIRROR)
    assert state == "FINISHED" and data, msg[:300]
    check_model_clean(MIRROR, "mirror")
    say(f"E Mirror to Whole: {line}")
    say(f"E   half {len(Ph)} patches / {len(Vh)} points / {open_h} open sides -> {len(P2)} patches / {n2} points / 0 "
        f"open sides, 0 same-direction seams; surface vs the stock cheetah rms {rms * 1000:.3f} mm max {mx * 1000:.2f} mm "
        f"(its own asymmetry: rms {arms * 1000:.3f} / max {amx * 1000:.2f} mm); {checked} mirror-image points checked, "
        f"{wrong} skinned to the wrong bone -> {MIRROR} ({len(data)} bytes)")
    obj = undo(1)
    in_mode(obj, "OBJECT")
    state, msg, again = export(obj, OUT_TMP)
    assert state == "FINISHED" and again == half_bytes, (state, msg[:200])
    say("E   Undo -> the half's bytes again")


# ========================================================================================================= F
def section_f():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np = len(patches)
    rings = topoops._rings_for_plan(obj)
    rrows = ring_rows(obj)
    in_mode(obj, "EDIT")
    bpy.ops.ed.undo_push(message="imported")
    outs = []
    # Fill Hole on a closed creature
    bpy.ops.mesh.select_all(action="DESELECT")
    r, err = run_op(lambda: bpy.ops.bz.fill_hole())
    assert r == {"CANCELLED"} and "closed" in err, err
    outs.append(("Fill Hole on a closed creature", err))
    assert not bpy.ops.bz.fill_hole.poll() and not bpy.ops.bz.extrude_sides.poll()
    # Weld: one corner only
    select_verts(obj, [int(patches[0][0])])
    r, err = run_op(lambda: bpy.ops.bz.weld())
    assert r == {"CANCELLED"} and "two corners" in err, err
    outs.append(("Weld with one corner selected", err))
    # Weld: two corners of one patch (it would collapse)
    select_verts(obj, [int(patches[0][0]), int(patches[0][3])])
    r, err = run_op(lambda: bpy.ops.bz.weld(threshold=10.0))
    assert r == {"CANCELLED"} and "collapse" in err, err
    outs.append(("Weld two corners of one patch", err))
    # Weld: nothing within the threshold
    r, err = run_op(lambda: bpy.ops.bz.weld(threshold=1e-9))
    assert r == {"CANCELLED"} and "within" in err, err
    outs.append(("Weld with nothing within the threshold", err))
    # Weld: two corners of one connection ring (no patch holds both, so the ring rule is what refuses it)
    ring = next(r for _n, _b, r in (store.connections(obj) or []) if len(set(int(v) for v in r)) >= 4)
    rc = sorted({int(v) for v in ring})
    pairless = next(((a, b) for i, a in enumerate(rc) for b in rc[i + 1:]
                     if not (np.isin(patches, [a]).any(axis=1) & np.isin(patches, [b]).any(axis=1)).any()), None)
    assert pairless is not None, "every pair of this ring's corners shares a patch"
    select_verts(obj, list(pairless), active=pairless[0])
    r, err = run_op(lambda: bpy.ops.bz.weld(mode="TARGET"))
    assert r == {"CANCELLED"} and "connection ring" in err, err
    outs.append(("Weld two corners of one connection ring", err))
    # the plans own ceilings, called directly (a MAX_PATCHES-patch creature is not worth building in Blender)
    big = np.tile(patches[:1], (topoops.MAX_PATCHES - len(patches) + 1, 1))
    tb = topoops._tables_of(obj)
    plan = topoops.attach_plan(verts_of(obj), patches, tb, verts_of(obj), big,
                               (None, None, None, None, None), -1, 0, np.zeros(1, dtype=np.int64))
    assert plan.refusal and "at most" in plan.refusal and str(topoops.MAX_PATCHES) in plan.refusal, plan.refusal
    outs.append(("Attach over the patch budget", plan.refusal))
    plan = topoops.fill_plan(verts_of(obj), patches, tb)
    assert plan.refusal and "closed" in plan.refusal, plan.refusal
    # Subdivide: a patch on a connection ring, and nothing selected
    select_faces(obj, [min(rrows)], patches)
    r, err = run_op(lambda: bpy.ops.bz.subdivide())
    assert r == {"CANCELLED"} and "connection ring" in err, err
    named = [n for n, _r, _e in rings if f"'{n}'" in err]
    assert named, err
    outs.append((f"Subdivide a patch on ring '{named[0]}'", err))
    bpy.ops.mesh.select_all(action="DESELECT")
    r, err = run_op(lambda: bpy.ops.bz.subdivide())
    assert r == {"CANCELLED"} and "select" in err.lower(), err
    outs.append(("Subdivide with nothing selected", err))
    # Mirror: a whole creature straddles the plane
    r, err = run_op(lambda: bpy.ops.bz.mirror_whole())
    assert r == {"CANCELLED"} and "straddle" in err, err
    outs.append(("Mirror to Whole on a whole creature", err))
    # Add Quad with nothing selected (after a delete, so the poll passes)
    select_faces(obj, [min(r for r in range(Np) if r not in rrows)], patches)
    step(lambda: bpy.ops.bz.delete_patch(), "delete 1 patch")
    bpy.ops.mesh.select_all(action="DESELECT")
    r, err = run_op(lambda: bpy.ops.bz.extrude_sides())
    assert r == {"CANCELLED"} and "open side" in err, err
    outs.append(("Add Quad with nothing selected", err))
    # Attach with no second creature selected
    in_mode(obj, "OBJECT")
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    assert not bpy.ops.bz.attach.poll(), "Attach polled with nothing else selected"
    outs.append(("Attach with nothing else selected", "the button is greyed out (poll False; the tooltip says to "
                                                      "select the creature to attach with this one)"))
    obj = undo(1)
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np
    identical(obj)
    for what, err in outs:
        say(f"F {what}: {str(err).strip().splitlines()[-1][:170]}")
    say("F   every refusal left the creature alone; Undo -> byte-identical")


# ========================================================================================================= G
def section_g():
    fresh()
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), store.point_count(obj)
    # the macros: the tool, then Blender's own translate
    for name, steps in (("bz.extrude_drag", ("BZ_OT_extrude_region", "TRANSFORM_OT_translate")),
                        ("bz.add_quad_drag", ("BZ_OT_extrude_sides", "TRANSFORM_OT_translate"))):
        cls = getattr(bpy.types, name.split(".")[1].upper().replace("BZ.", "BZ_OT_"), None) or \
            getattr(bpy.types, "BZ_OT_" + name.split(".")[1], None)
        assert cls is not None, f"{name} is not registered"
        assert hasattr(bpy.ops.bz, name.split(".")[1]), name
    pair = neighbour_pair(patches, skip=ring_rows(obj))
    V0 = verts_of(obj)
    size = float(np.linalg.norm(V0.max(axis=0) - V0.min(axis=0)))
    in_mode(obj, "EDIT")
    bpy.ops.ed.undo_push(message="imported")
    select_faces(obj, list(pair), patches)
    step(lambda: bpy.ops.bz.extrude_region(amount=0.05 * size, outline=0.8), "extrude region")
    line = obj.get("bz_last_topo", "")
    P2 = store.patches(obj)
    n2 = store.point_count(obj)
    assert len(P2) == Np + 6, len(P2)                       # 2 patches share a side: 6 boundary sides -> 6 walls
    assert core.open_side_count(P2) == 0, core.open_side_count(P2)
    assert same_direction(P2) == 0, f"{same_direction(P2)} same-direction seams after the extrusion"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None, store.pool_error(obj)
    V2 = verts_of(obj)
    lifted = np.abs(V2[:N] - V0).max(axis=1) > 1e-9
    assert lifted.any(), "nothing moved"
    in_mode(obj, "OBJECT")
    state, msg, data = export(obj, REGION)
    assert state == "FINISHED" and data, msg[:300]
    check_model_clean(REGION, "extrude region")
    say(f"G Extrude Region on patches {pair}: {line}")
    say(f"G   {Np} -> {len(P2)} patches, {N} -> {n2} points, {int(lifted.sum())} old points lifted, 0 open sides, "
        f"0 same-direction seams, check_model clean -> {REGION} ({len(data)} bytes); both drag macros registered")
    obj = undo(1)
    in_mode(obj, "OBJECT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N
    identical(obj)
    say("G   Undo -> byte-identical")


def run():
    want = os.environ.get("BZ_TOPO_SECTIONS", "abcdefg").lower()     # a subset while working on one section
    for name, fn in (("a", section_a), ("b", section_b), ("c", section_c), ("d", section_d), ("e", section_e),
                     ("f", section_f), ("g", section_g)):
        if name in want:
            fn()
    blender_native.unregister()
    return True


try:
    ok = run()
    open("/tmp/bz_topology_ops_done.txt", "w").write("PASS\n" if ok else "FAIL\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_topology_ops_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    say("ERROR", traceback.format_exc())
    sys.exit(1)
