"""B6 patch-level selection in headless Blender: corner quads, face select, hide, Delete Patch, vertex types.
    blender --background --python blender_native/tests/patch_select_test.py

A. import cheetah: one quad face per patch over its corners (288), wound with the engine surface normal, export
   byte-identical; the faces survive Tab; hornbill (two patches over the same four corners: two faces) too;
B. Face select of 3 patches -> Assign Selected to Limb assigns exactly those (the 4-corner rule still works);
   Select Limb selects the limb's faces;
C. Hide Patches on 2 -> the overlay mask has 2 and the surface batch drops their triangles; the handles only they use
   are hidden; Unhide restores everything but the interiors;
D. Delete Patch on 2 non-ring patches: patch table, UVs, materials, types, limbs, manual flags shifted, the pool
   compacted (points only they used dropped), owners kept per point, rings remapped, faces one per patch, the
   export loads through ICParse with the expected dangling edges only (-> /tmp/bz_patch_delete_cheetah.sgm for the
   engine tier), Undo -> byte-identical; then Extrude on an open side of the hole adds a face;
E. Delete Patch on a ring patch is refused naming the ring;
F. Blender's own Delete Faces is caught by the pool guard (Export blocked, Delete Patch named); the keymap items
   for X / Delete / H / Shift+H / Alt+H exist (or the add-on keyconfig is absent in background mode);
G. Coplanar: Set Coplanar flattens a corner's handles into a plane; a handle moved out of the plane turns the others
   so they stay coplanar; Corner type leaves them; Lock Handles turns them by the driver's rotation; a cancelled
   handle drag puts the siblings back bit-exactly.
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
from blender_native import store, core, ops, draw, fastsurf, patchfeel, patchsel, patchrules as PR  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
HORNBILL = REPO + "/Assets/stock/hornbill.sgm"
OUT = "/tmp/bz_patch_delete_cheetah.sgm"     # the cheetah with 2 patches deleted (run_all --engine loads it)
OUT_TMP = "/tmp/bz_patch_select_tmp.sgm"
_log = open("/tmp/bz_patch_select_log.txt", "w")


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


def select_verts(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    want = {int(i) for i in np.atleast_1d(idx)}
    for f in bm.faces:
        f.select = False
    for v in bm.verts:
        v.select = v.index in want and not v.hide
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def move(obj, idx, delta):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for i in np.atleast_1d(idx):
        v = bm.verts[int(i)]
        v.co = (np.array(v.co[:], dtype=np.float64) + delta).astype(np.float32)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def set_pos(obj, i, pos):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[int(i)].co = np.asarray(pos, dtype=np.float32)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def quad_normals(V, quads):
    c = V[quads]
    n = np.zeros((len(quads), 3))
    for k in range(4):
        a, b = c[:, k], c[:, (k + 1) % 4]
        n[:, 0] += (a[:, 1] - b[:, 1]) * (a[:, 2] + b[:, 2])
        n[:, 1] += (a[:, 2] - b[:, 2]) * (a[:, 0] + b[:, 0])
        n[:, 2] += (a[:, 0] - b[:, 0]) * (a[:, 1] + b[:, 1])
    return n


def ring_sides(obj):
    """Every patch on a connection ring (a side joining two consecutive ring corners)."""
    patches = store.patches(obj)
    rings = [(n, r, e) for (n, _b, r), e in zip(store.connections(obj), store.conn_encodings(obj))]
    return {r for r, _n, _s in patchsel.ring_patch_refusals(patches, range(len(patches)), rings)}


def run():
    fresh()
    scene = bpy.context.scene
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), len(obj.data.vertices)
    V0 = verts_of(obj)

    # ---------------------------------------------------------------------------------------------------- A
    rows, quads = patchsel.face_quads(patches)
    assert len(rows) == Np == 288 and len(obj.data.polygons) == Np, (len(rows), len(obj.data.polygons))
    faces = patchsel.mesh_faces(obj)
    assert all(len(f) == 4 for f in faces) and [tuple(int(i) for i in q) for q in quads] == faces
    topo = fastsurf.TopoCache(patches, 4, store.patch_type(obj))
    _sv, sn = topo.eval_all(V0)
    centre = sn.reshape(Np, 16, 3).mean(axis=1)
    dots = np.einsum("ij,ij->i", quad_normals(V0, quads), centre)
    assert (dots > 0).all(), f"{int((dots <= 0).sum())} corner quads wound against the surface normal"
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None
    assert obj.display_type == "WIRE"
    identical(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    assert len(bm.faces) == Np and patchsel.face_error(obj) is None and store.pool_error(obj) is None
    hid = patchfeel.hidden_points(obj)
    assert np.array_equal(hid, PR.roles(patches, N).interior), "the hidden interiors changed with the faces"
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    say(f"A cheetah: {Np} corner quads (one per patch, corners {patchsel.FACE_SLOTS}), all {Np} wound with the "
        f"surface normal, export byte-identical, faces kept across Tab, {int(hid.sum())} interiors still hidden")
    hb = imp(HORNBILL)
    hp = store.patches(hb)
    hrows = patchsel.face_rows(hp)
    assert len(hb.data.polygons) == len(hrows) == len(hp), (len(hb.data.polygons), len(hrows), len(hp))
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.object.mode_set(mode="OBJECT")
    identical(hb, open(HORNBILL, "rb").read())
    say(f"A hornbill: {len(hp)} patches, {len(hb.data.polygons)} faces (2 pairs over the same corners), byte-identical")
    # an object saved before B6 (no faces at all) is tolerated by the guard and given its quads on load
    bm = bmesh.new(); bm.from_mesh(hb.data)
    for f in list(bm.faces):
        bm.faces.remove(f)
    for e in list(bm.edges):
        bm.edges.remove(e)
    bm.to_mesh(hb.data); bm.free(); hb.data.update()
    assert len(hb.data.polygons) == 0 and store.pool_error(hb) is None, store.pool_error(hb)
    k = patchsel.ensure_faces(hb)
    assert k == len(hrows) and len(hb.data.polygons) == len(hrows) and patchsel.face_error(hb) is None
    identical(hb, open(HORNBILL, "rb").read())
    say(f"A pre-B6 object (faces stripped): guard tolerant, ensure_faces added {k} quads, byte-identical")
    bpy.data.objects.remove(hb)
    bpy.context.view_layer.objects.active = obj

    # ---------------------------------------------------------------------------------------------------- B
    bpy.ops.object.mode_set(mode="EDIT")
    pl0 = store.patch_limb(obj)
    names = store.limb_names(obj)
    target = next(i for i, n in enumerate(names) if n.lower().endswith("head"))
    pick = [int(p) for p in np.nonzero(pl0 != target)[0][:3]]
    select_faces(obj, pick, patches)
    assert sorted(patchsel.selected_rows(obj, patches)) == sorted(pick) and sorted(ops._selected_patches(obj)) == sorted(pick)
    r, err = run_op(lambda: bpy.ops.bz.assign_limb(limb=str(target)))
    assert r == {"FINISHED"}, err
    pl1 = store.patch_limb(obj)
    assert (pl1[pick] == target).all() and int((pl1 != pl0).sum()) == 3, (int((pl1 != pl0).sum()))
    # the 4-corner rule still qualifies a patch (no face selected: vertex selection only, flushed to its face)
    p4 = int(np.nonzero(pl1 != target)[0][0])
    select_verts(obj, patches[p4][list(PR.CORNER_SLOTS)])
    assert ops._selected_patches(obj) == [p4], ops._selected_patches(obj)
    r, err = run_op(lambda: bpy.ops.bz.select_limb(limb=str(target)))
    assert r == {"FINISHED"}, err
    bm = bmesh.from_edit_mesh(obj.data)
    fsel = {int(patchsel.face_rows(patches)[i]) for i, f in enumerate(bm.faces) if f.select}
    assert fsel == set(np.nonzero(pl1 == target)[0].tolist()), (len(fsel), int((pl1 == target).sum()))
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.ed.undo_push(message="assigned")
    say(f"B Face select of patches {pick} -> Assign to Limb '{names[target]}': exactly those 3 assigned; 4-corner "
        f"rule picks patch {p4}; Select Limb selects its {len(fsel)} faces")
    bpy.ops.object.mode_set(mode="EDIT")
    r, err = run_op(lambda: bpy.ops.bz.assign_limb(limb=str(target)))     # nothing selected: refused in words
    assert r == {"CANCELLED"}, r
    for p in pick:                                                          # put them back for the export below
        pass
    pl = store.patch_limb(obj); pl[pick] = pl0[pick]
    select_faces(obj, pick, patches)
    r, err = run_op(lambda: bpy.ops.bz.assign_limb(limb=str(int(pl0[pick[0]]))))
    assert r == {"FINISHED"}, err
    for p in pick:
        if int(pl0[p]) != int(pl0[pick[0]]):
            select_faces(obj, [p], patches)
            r, err = run_op(lambda: bpy.ops.bz.assign_limb(limb=str(int(pl0[p]))))
            assert r == {"FINISHED"}, err
    assert np.array_equal(store.patch_limb(obj), pl0)
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    bpy.ops.object.mode_set(mode="EDIT")

    # ---------------------------------------------------------------------------------------------------- C
    ring_rows = ring_sides(obj)
    users = np.zeros(N, dtype=np.int64); np.add.at(users, patches.reshape(-1), 1)
    # two neighbouring non-ring patches (they share a side) far from any ring
    def neighbours(p):
        s = set(patches[p][list(PR.CORNER_SLOTS)].tolist())
        return [q for q in range(Np) if q != p and len(s & set(patches[q][list(PR.CORNER_SLOTS)].tolist())) == 2]
    pair = next((p, q) for p in range(Np) if p not in ring_rows
                for q in neighbours(p) if q not in ring_rows and q > p)
    select_faces(obj, list(pair), patches)
    r, err = run_op(lambda: bpy.ops.bz.hide_patches())
    assert r == {"FINISHED"}, err
    hp = patchsel.hidden_rows(obj, patches)
    assert int(hp.sum()) == 2 and set(np.nonzero(hp)[0].tolist()) == set(pair), np.nonzero(hp)[0]
    st = {"topo": fastsurf.TopoCache(patches, 4, store.patch_type(obj))}   # the draw state, GPU-free (headless)
    draw._apply_hidden(obj, st, patches)
    assert st.get("hidden") is not None and int(st["hidden"].sum()) == 2 and st["batch"] is None
    vt = draw._vis_tris(st)
    per = len(st["topo"].tris) // Np
    assert len(vt) == (Np - 2) * per, (len(vt), (Np - 2) * per)
    assert len(st["net_edges"]) < len(core._patch_grid_edges(patches)), "the control net still shows hidden patches"
    hidv = patchfeel.hidden_points(obj)
    only = np.zeros(N, dtype=bool)
    only[patches[list(pair)].reshape(-1)] = True
    only[patches[[p for p in range(Np) if p not in pair]].reshape(-1)] = False
    assert hidv[only].all(), "a point only the hidden patches use is still shown"
    shared = np.zeros(N, dtype=bool); shared[patches[list(pair)].reshape(-1)] = True; shared &= ~only
    inter = PR.roles(patches, N).interior
    assert not hidv[shared & ~inter].any(), "a point a visible patch uses was hidden"
    r, err = run_op(lambda: bpy.ops.bz.unhide_patches())
    assert r == {"FINISHED"}, err
    assert not patchsel.hidden_rows(obj, patches).any()
    assert np.array_equal(patchfeel.hidden_points(obj), inter), "Unhide changed the hidden interiors"
    draw._apply_hidden(obj, st, patches)
    assert not st["hidden"].any() and len(draw._vis_tris(st)) == Np * per
    bpy.ops.mesh.select_all(action="DESELECT")
    say(f"C Hide Patches {pair}: overlay mask 2 of {Np}, surface batch {(Np - 2) * per} of {Np * per} triangles, "
        f"{int(only.sum())} points only they use hidden, shared corners kept; Unhide: mask 0, interiors still hidden")

    # ---------------------------------------------------------------------------------------------------- D
    vb0 = store.vert_bone(obj)
    puv0, pmat0, ptype0 = store.patch_uv(obj), store.patch_mat(obj), store.patch_type(obj)
    conns0 = store.connections(obj)
    plan = patchsel.delete_plan(patches, list(pair), [], N)
    assert plan.refusal is None and len(plan.drop) == int(only.sum()) and plan.n_new == N - len(plan.drop)
    bpy.ops.ed.undo_push(message="before delete")
    select_faces(obj, list(pair), patches)
    r, err = run_op(lambda: bpy.ops.bz.delete_patch())
    assert r == {"FINISHED"}, err
    bpy.ops.ed.undo_push(message="after delete")
    P1 = store.patches(obj)
    n1 = store.point_count(obj)
    keep = plan.keep
    assert len(P1) == Np - 2 and n1 == plan.n_new and store.pool_n(obj) == n1, (len(P1), n1, plan.n_new)
    assert np.array_equal(P1, plan.o2n[patches[keep]]), "the patch table was not remapped as planned"
    assert np.array_equal(store.patch_limb(obj), pl0[keep]) and np.array_equal(store.patch_uv(obj), puv0[keep])
    assert np.array_equal(store.patch_mat(obj), pmat0[keep]) and np.array_equal(store.patch_type(obj), ptype0[keep])
    assert np.array_equal(store.vert_bone(obj), vb0[plan.o2n >= 0]), "owners did not travel with their points"
    V1 = verts_of(obj)
    assert np.array_equal(V1, V0[plan.o2n >= 0]), "positions did not travel with their points"
    for (n0_, b0, r0), (n1_, b1, r1) in zip(conns0, store.connections(obj)):
        assert n0_ == n1_ and b0 == b1 and [int(plan.o2n[i]) for i in r0] == list(r1), n0_
    assert store.pool_error(obj) is None and patchsel.face_error(obj) is None
    assert np.array_equal(store.point_ints(obj, store.ATTR_CP), np.arange(n1))
    bpy.ops.object.mode_set(mode="OBJECT")
    assert len(obj.data.polygons) == Np - 2
    state, msg, data = export(obj, OUT)
    assert state == "FINISHED" and data is not None, msg[:300]
    m = SP.read(OUT)
    assert len(m.patches) == Np - 2 and len(m.vertices) == n1
    msgs = ICval.check_model(m)
    say("D check_model:", msgs)
    assert msgs and all("dangling" in p for p in msgs if not p.startswith("WARN")), msgs
    holes = core.open_side_count(P1)
    assert holes == 6, holes                                    # two patches sharing a side: 4 + 4 - 2 open sides
    # Validate says the file will not load (blocking) and why
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    val = list(obj.get("bz_validation", []))
    assert core.blocking(val) and any("dangling" in p for p in core.blocking(val)), val[:4]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    if obj.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    assert len(store.patches(obj)) == Np and store.point_count(obj) == N and store.pool_error(obj) is None, \
        (len(store.patches(obj)), store.point_count(obj), store.pool_error(obj))
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    say(f"D Delete Patch {pair}: {Np - 2} patches, {n1} points ({len(plan.drop)} dropped), tables shifted, owners and "
        f"positions per point kept, {len(conns0)} rings remapped; export {len(data)} bytes -> {OUT}: check_model = "
        f"{len(msgs)} dangling-edge line(s) only ({holes} open sides), Validate blocks; Undo -> byte-identical")
    # Extrude on an open side of the hole (after the delete again) adds the new patch's face
    bpy.ops.object.mode_set(mode="EDIT")
    patches = store.patches(obj)
    select_faces(obj, list(pair), patches)
    r, err = run_op(lambda: bpy.ops.bz.delete_patch())
    assert r == {"FINISHED"}, err
    P1 = store.patches(obj)
    users1 = core.side_users(P1)
    pi, side, key = next((p, s, k) for p, s, k in core.patch_sides(P1) if len(users1[k]) == 1)
    select_verts(obj, [int(P1[pi][core._SIDE_CPS[side][0]]), int(P1[pi][core._SIDE_CPS[side][3]])])
    r, err = run_op(lambda: bpy.ops.bz.extrude_patch())
    assert r == {"FINISHED"}, err
    P2 = store.patches(obj)
    bm = bmesh.from_edit_mesh(obj.data)
    assert len(P2) == Np - 1 and len(bm.faces) == Np - 1 and patchsel.face_error(obj) is None
    assert store.pool_error(obj) is None
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"D Extrude after the delete: {len(P2)} patches, {len(obj.data.polygons)} faces (the new patch's added)")

    # ---------------------------------------------------------------------------------------------------- E
    fresh()
    obj = imp()
    patches = store.patches(obj)
    ring_rows = ring_sides(obj)
    conns = store.connections(obj)
    rp = min(ring_rows)
    bpy.ops.object.mode_set(mode="EDIT")
    select_faces(obj, [rp], patches)
    r, err = run_op(lambda: bpy.ops.bz.delete_patch())
    assert r == {"CANCELLED"} and err and "connection ring" in err, err
    named = [n for n, _b, _r in conns if f"'{n}'" in err]
    assert named, err
    assert len(store.patches(obj)) == len(patches) and store.pool_error(obj) is None
    say(f"E Delete Patch on ring patch {rp}: refused, names ring {named[0]}: {err.strip().splitlines()[-1][:150]}")

    # ---------------------------------------------------------------------------------------------------- F
    bpy.ops.ed.undo_push(message="before native delete")
    select_faces(obj, [rp + 1 if rp + 1 not in ring_rows else rp + 2], patches)
    bpy.ops.mesh.delete(type="FACE")
    bpy.ops.ed.undo_push(message="after native delete")
    err = store.pool_error(obj)
    assert err and "Delete Patch" in err and "Undo" in err, err
    state, msg, data = export(obj)
    assert state == "CANCELLED" and data is None and "Delete Patch" in msg, msg[:200]
    assert not bpy.ops.bz.extrude_patch.poll() and not bpy.ops.bz.delete_patch.poll()
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    if obj.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    assert store.pool_error(obj) is None, store.pool_error(obj)
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    kc = bpy.context.window_manager.keyconfigs.addon
    keys = {(k, tuple(sorted(o for o in opts if o in ("shift", "ctrl", "alt")))) for _i, k, opts in patchsel.KEYS}
    assert {("X", ()), ("DEL", ()), ("H", ()), ("H", ("shift",)), ("H", ("alt",))} <= keys, keys
    if kc is not None:
        km = kc.keymaps.get("Mesh")
        assert km is not None and len(patchsel._KEYMAP_ITEMS) == len(patchsel.KEYS), len(patchsel._KEYMAP_ITEMS)
    say(f"F native Delete Faces: pool guard refuses ({err[:90]}...), Export blocked, operators disabled, Undo -> "
        f"byte-identical; keymap: {len(patchsel.KEYS)} items " + ("registered" if kc is not None else
                                                                   "(no add-on keyconfig in background mode)"))

    # ---------------------------------------------------------------------------------------------------- G
    bpy.ops.object.mode_set(mode="EDIT")
    patches = store.patches(obj)
    N = store.point_count(obj)
    H = PR.handles_of(patches)
    roles = PR.roles(patches, N)
    V0 = verts_of(obj)
    c = next(int(i) for i in np.nonzero(roles.corner)[0] if len(H[i]) >= 4 and PR.coplanar_error(V0, patches, i) > 0.2)
    e0 = PR.coplanar_error(V0, patches, c)
    select_verts(obj, c)
    r, err = run_op(lambda: bpy.ops.bz.set_vertex_type(kind="COPLANAR"))
    assert r == {"FINISHED"}, err
    V1 = verts_of(obj)
    assert patchsel.coplanar_flags(obj)[c] and PR.coplanar_error(V1, patches, c) < 1e-5
    its = np.nonzero(np.isin(patches, H[c]).any(axis=1))[0]
    assert (PR.interior_error(V1, patches)[its] < PR.TOL).all(), "interiors not on the rule after the projection"
    draw.invalidate(obj.name)
    h = int(H[c][0]); sibs = [int(x) for x in H[c] if x != h]
    n0 = PR.plane_normal(V1[H[c]] - V1[c])
    prev = verts_of(obj)
    move(obj, h, 0.04 * n0)                                   # the driver leaves the plane
    out = patchfeel.settle(obj, prev)
    V2 = verts_of(obj)
    assert out is not None and PR.coplanar_error(V2, patches, c) < 1e-5, PR.coplanar_error(V2, patches, c)
    assert np.abs(V2[sibs] - V1[sibs]).max() > 1e-4, "the siblings did not turn"
    assert np.allclose(np.linalg.norm(V2[sibs] - V2[c], axis=1), np.linalg.norm(V1[sibs] - V1[c], axis=1), atol=1e-5)
    assert np.array_equal(V2[c], V1[c])
    assert (PR.interior_error(V2, patches)[its] < PR.TOL).all()
    # cancel: the driver back where it started -> siblings and interiors bit-exact
    prev = verts_of(obj)
    set_pos(obj, h, V1[h])
    out = patchfeel.settle(obj, prev)
    V3 = verts_of(obj)
    assert out is not None and np.array_equal(V3, V1), f"{int(np.any(V3 != V1, axis=1).sum())} points off after the cancel"
    # Corner type: the same move leaves the siblings
    r, err = run_op(lambda: bpy.ops.bz.set_vertex_type(kind="CORNER"))
    assert r == {"FINISHED"} and not patchsel.coplanar_flags(obj)[c], err
    draw.invalidate(obj.name)
    prev = verts_of(obj)
    move(obj, h, 0.04 * n0)
    patchfeel.settle(obj, prev)
    V4 = verts_of(obj)
    assert np.array_equal(V4[sibs], V1[sibs]) and PR.coplanar_error(V4, patches, c) > 1e-3
    set_pos(obj, h, V1[h]); patchfeel.settle(obj, verts_of(obj)); draw.invalidate(obj.name)
    # Lock Handles: the siblings turn by the driver's (minimal) rotation, lengths kept
    scene = bpy.context.scene
    scene.bz_lock_handles = True
    prev = verts_of(obj)
    R = PR.rotation_between([1.0, 0.0, 0.0], [0.7, 0.7, 0.1])
    d1 = R @ (V1[h] - V1[c])
    set_pos(obj, h, V1[c] + d1)
    out = patchfeel.settle(obj, prev)
    V5 = verts_of(obj)
    Rmin = PR.rotation_between(V1[h] - V1[c], V5[h] - V5[c])
    exp = V5[c] + (V1[sibs] - V1[c]) @ Rmin.T
    assert out is not None and np.abs(V5[sibs] - exp).max() < 1e-5, np.abs(V5[sibs] - exp).max()
    assert np.abs(V5[sibs] - V1[sibs]).max() > 1e-3
    scene.bz_lock_handles = False
    say(f"G corner {c} ({len(H[c])} handles, coplanar error {e0:.2f} -> {PR.coplanar_error(V1, patches, c):.1e} after Set "
        f"Coplanar): handle {h} moved 4 cm out of the plane -> {len(sibs)} siblings turned, error "
        f"{PR.coplanar_error(V2, patches, c):.1e}, lengths kept, interiors on the rule; cancel -> bit-exact; Corner "
        f"type: siblings untouched; Lock Handles: siblings follow the driver's rotation (max {np.abs(V5[sibs] - exp).max():.1e})")
    bpy.ops.object.mode_set(mode="OBJECT")
    blender_native.unregister()
    return True


try:
    ok = run()
    open("/tmp/bz_patch_select_done.txt", "w").write("PASS\n" if ok else "FAIL\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_patch_select_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    say("ERROR", traceback.format_exc())
    sys.exit(1)
