"""B5 patch feel in headless Blender: corners carry handles, interiors automatic, interiors hidden.
    blender --background --python blender_native/tests/patch_feel_test.py

Headless Blender never redraws, so the rule is driven by the function the draw handler calls (patchfeel.settle with
the control points of the "last redraw" and the moved bmesh), the moves made through bmesh as a G drag would.

A. import cheetah: the interior points (interior in every patch that uses them) are hidden, Select All selects none,
   the export is byte-identical with the rules on; hidden survives Tab;
B. a corner moved 5 cm up: its handles move by the same delta, the interiors of its patches are back on the rule,
   every other point is untouched; Export writes exactly those points and the file's interiors obey the rule
   (/tmp/bz_patch_feel_cheetah.sgm: the engine tier loads it); a handle moved alone: no corner or other handle moves,
   only the interiors framed by it are recomputed;
C. interiors shown: an interior moved directly flags its patch manual and the rule leaves it while the corner moves;
   Auto Interiors puts it back and clears the flag; hidden again afterwards;
D. X-mirror: a corner and its mirror partner moved together (what Blender's mirror does): both handle sets carried,
   the result mirror-symmetric where the source was;
E. Undo restores the mesh: the rule then writes nothing and the export is byte-identical;
F. rules off: raw behaviour (the corner moves alone, its patches leave the rule);
G. selections: Select > Handles selects no hidden point; a selected corner's hidden interiors follow it in the
   selection the skin edits read; Auto Interiors on nothing selected is a no-op on a stock net; a plain mesh (not a
   creature) is ignored by the rule.
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
from blender_native import store, core, patchfeel, patchrules as PR, ops  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
OUT = "/tmp/bz_patch_feel_cheetah.sgm"        # the corner-dragged cheetah (run_all --engine loads it in the Object Editor)
OUT_TMP = "/tmp/bz_patch_feel_tmp.sgm"
DV = np.array([0.0, 0.0, 0.05])
_log = open("/tmp/bz_patch_feel_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    try:
        blender_native.unregister()
    except RuntimeError:
        pass                                            # the first time: nothing registered yet
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


def identical(obj):
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, msg[:300], None if data is None else len(data))


def verts_of(obj) -> np.ndarray:
    """The control points as they are now (edit or object mode), float64 of the float32 mesh."""
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def move(obj, idx, delta):
    """A G drag on the points ``idx``: set their bmesh positions."""
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for i in np.atleast_1d(idx):
        v = bm.verts[int(i)]
        v.co = (np.array(v.co[:], dtype=np.float64) + delta).astype(np.float32)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def select_only(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    want = set(int(i) for i in np.atleast_1d(idx))
    for v in bm.verts:
        v.select = v.index in want and not v.hide
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def selected(obj) -> np.ndarray:
    bm = bmesh.from_edit_mesh(obj.data)
    return np.fromiter((v.select for v in bm.verts), dtype=bool, count=len(bm.verts))


def changed_points(a, b) -> set:
    return set(np.nonzero(np.any(np.asarray(a) != np.asarray(b), axis=1))[0].tolist())


def run():
    fresh()
    scene = bpy.context.scene
    assert scene.bz_patch_rules and not scene.bz_show_interiors, "defaults: rules on, interiors hidden"
    obj = imp()
    patches = store.patches(obj)
    Np, N = len(patches), len(obj.data.vertices)
    roles = PR.roles(patches, N)
    H = PR.handles_of(patches)
    V0 = verts_of(obj)

    # ---------------------------------------------------------------------------------------------------- A
    hid = patchfeel.hidden_points(obj)
    n_pure = int(roles.interior.sum())
    assert n_pure == 4 * Np and int(hid.sum()) == n_pure and np.array_equal(hid, roles.interior), (hid.sum(), n_pure)
    identical(obj)                                      # rules on, nothing moved: byte-identical
    bpy.ops.object.mode_set(mode="EDIT")
    assert np.array_equal(patchfeel.hidden_points(obj), roles.interior), "hidden interiors lost on Tab"
    bpy.ops.mesh.select_all(action="SELECT")
    sel = selected(obj)
    assert not (sel & hid).any() and int(sel.sum()) == N - n_pure, (int((sel & hid).sum()), int(sel.sum()))
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    assert np.array_equal(patchfeel.hidden_points(obj), roles.interior), "hidden interiors lost on Tab out"
    identical(obj)
    say(f"A hidden interiors {n_pure} = 4 x {Np} patches of {N} points ({100 * n_pure / N:.0f} %), Select All picks "
        f"{int(sel.sum())} (none hidden), untouched export byte-identical, hidden kept across Tab")

    # ---------------------------------------------------------------------------------------------------- B
    bpy.ops.object.mode_set(mode="EDIT")
    corners = np.nonzero(roles.corner)[0]
    # a corner with 3+ patches, off the mirror plane, with a mirror partner (for D)
    pairs, _on_plane = core.mirror_pairs(V0, axis=0)
    users = np.zeros(N, dtype=np.int64); np.add.at(users, patches[:, list(PR.CORNER_SLOTS)].reshape(-1), 1)
    c = next(int(i) for i in corners if users[i] >= 3 and int(i) in pairs and abs(V0[i, 0]) > 0.05
             and roles.corner[pairs[int(i)]])
    its = np.nonzero((patches[:, list(PR.CORNER_SLOTS)] == c).any(axis=1))[0]
    its_ints = set(patches[its][:, list(PR.INTERIOR_SLOTS)].reshape(-1).tolist())
    prev = verts_of(obj)
    move(obj, c, DV)
    out = patchfeel.settle(obj, prev)
    assert out is not None, "the rule wrote nothing for a moved corner"
    V1 = verts_of(obj)
    assert np.allclose(V1, out) and np.allclose(V1[c], V0[c] + DV, atol=1e-6)
    assert np.allclose(V1[H[c]], V0[H[c]] + DV, atol=1e-6), "handles not carried by the corner's delta"
    err = PR.interior_error(V1, patches)
    assert (err[its] < PR.TOL).all(), err[its]
    ch = changed_points(V0, V1)
    assert ch <= {c} | set(H[c].tolist()) | its_ints, ch - ({c} | set(H[c].tolist()) | its_ints)
    assert ch >= {c} | set(H[c].tolist()), "a handle did not move"
    far = np.ones(Np, dtype=bool); far[its] = False
    assert np.array_equal(V1[patches[far].reshape(-1)], V0[patches[far].reshape(-1)]), "an untouched patch changed"
    assert not store.manual_interior(obj, Np).any()
    # the same state again: nothing more to do
    assert patchfeel.settle(obj, V1) is None
    # export: exactly the changed points differ in the file, and the file's interiors obey the rule
    state, msg, data = export(obj, OUT)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, msg[:200])
    back = core.load_creature(OUT)
    src = core.load_creature(SRC)
    file_ch = changed_points(src.verts, back.verts)
    assert file_ch == ch, (sorted(file_ch ^ ch)[:10])
    ferr = PR.interior_error(back.verts, back.patches)
    assert (ferr[its] < PR.TOL).all() and (ferr < PR.TOL).all(), (ferr[its], ferr.max())
    assert np.abs(back.verts - V1).max() < 1e-6
    say(f"B corner {c} ({users[c]} patches, {len(H[c])} handles) +5 cm: {len(ch)} points moved ({len(H[c])} handles "
        f"carried, {len(ch) - 1 - len(H[c])} interiors recomputed), {int(far.sum())} patches untouched; export {len(data)} "
        f"bytes -> {OUT}, the file differs in exactly those points, all {Np} patches on the rule; {msg[:60]}")
    # a handle alone: no corner / other handle moves; only the interiors framed by it are recomputed
    h = int(H[c][0])
    hp = np.nonzero((patches == h).any(axis=1))[0]
    framed = {int(patches[p, i]) for p in hp for i, _c, h1, h2 in PR.INTERIOR_FRAME
              if h in (int(patches[p, h1]), int(patches[p, h2]))}
    prev = verts_of(obj)
    move(obj, h, DV)
    out = patchfeel.settle(obj, prev)
    V2 = verts_of(obj)
    assert out is not None and changed_points(prev, V2) == {h} | framed, changed_points(prev, V2) ^ ({h} | framed)
    assert (PR.interior_error(V2, patches)[hp] < PR.TOL).all()
    say(f"B handle {h} alone: {len(framed)} interior(s) recomputed, nothing else moved")

    # ---------------------------------------------------------------------------------------------------- C
    scene.bz_show_interiors = True
    assert not patchfeel.hidden_points(obj).any(), "Show Interior Points left points hidden"
    pi = int(its[0])
    # an interior of a frame NOT at corner c: one framed by c would be explained again when c and its handles move
    # by the same delta (the rule is translation-invariant), which is the right answer, not the case tested here
    i5 = next(int(patches[pi, i]) for i, cc, _h1, _h2 in PR.INTERIOR_FRAME if int(patches[pi, cc]) != c)
    prev = verts_of(obj)
    move(obj, i5, DV)
    out = patchfeel.settle(obj, prev)
    V3 = verts_of(obj)
    man = store.manual_interior(obj, Np)
    assert out is None and man[pi] and man.sum() == 1, (out is None, np.nonzero(man)[0])
    assert np.allclose(V3[i5], prev[i5] + DV, atol=1e-6), "the hand-placed interior was rewritten"
    # then its corner moves: handles carried, the manual patch's interiors left, the other patches recomputed
    prev = verts_of(obj)
    move(obj, c, DV)
    out = patchfeel.settle(obj, prev)
    V4 = verts_of(obj)
    assert out is not None and np.allclose(V4[H[c]], prev[H[c]] + DV, atol=1e-6)
    pints = set(patches[pi][list(PR.INTERIOR_SLOTS)].tolist())
    assert not (changed_points(prev, V4) & pints), "a manual patch's interiors were rewritten"
    others = [int(p) for p in its if p != pi]
    e = PR.interior_error(V4, patches)
    assert (e[others] < PR.TOL).all() and e[pi] > PR.TOL
    # Auto Interiors on the manual patch (its corners selected): back on the rule, flag cleared
    select_only(obj, patches[pi][list(PR.CORNER_SLOTS)])
    r = bpy.ops.bz.reset_interiors()
    assert r == {"FINISHED"}, r
    V5 = verts_of(obj)
    assert PR.interior_error(V5, patches)[pi] < PR.TOL and not store.manual_interior(obj, Np).any()
    assert changed_points(V4, V5) <= pints, changed_points(V4, V5) - pints
    scene.bz_show_interiors = False
    assert np.array_equal(patchfeel.hidden_points(obj), roles.interior), "interiors not hidden again"
    say(f"C interior {i5} of patch {pi} moved by hand: patch manual (1 of {Np}), left alone while corner {c} moved "
        f"(its {len(others)} other patches recomputed); Auto Interiors: {len(changed_points(V4, V5))} points back on "
        f"the rule, flag cleared; hidden again")

    # ---------------------------------------------------------------------------------------------------- D
    m = int(pairs[c])
    flip = np.array([-1.0, 1.0, 1.0])
    prev = verts_of(obj)
    move(obj, [c, m], DV)                               # what X-mirror does to the partner (DV mirrors to itself)
    out = patchfeel.settle(obj, prev)
    V6 = verts_of(obj)
    assert out is not None
    assert np.allclose(V6[H[c]], prev[H[c]] + DV, atol=1e-6) and np.allclose(V6[H[m]], prev[H[m]] + DV, atol=1e-6)
    # the carried displacement is mirror-symmetric: each handle of c whose mirror partner is a handle of m moves by
    # the mirror image of the partner's move (parts B / C moved c's side alone before, so the positions are not)
    sym = [(int(a), int(pairs[int(a)])) for a in H[c] if int(a) in pairs and int(pairs[int(a)]) in set(H[m].tolist())]
    assert sym, "no mirrored handle pairs"
    disp = V6 - prev
    asym = max(float(np.abs(disp[a] - disp[b] * flip).max()) for a, b in sym)
    assert asym < 1e-6, asym
    say(f"D X-mirror: corner {c} + partner {m} moved together: both handle sets carried ({len(H[c])} + {len(H[m])}), "
        f"{len(sym)} mirrored handle pairs move as mirror images (asymmetry {asym * 1000:.4f} mm)")

    # ---------------------------------------------------------------------------------------------------- E
    bpy.ops.object.mode_set(mode="OBJECT")
    fresh()
    obj = imp()
    bpy.ops.ed.undo_push(message="imported")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit mode")
    prev = verts_of(obj)
    move(obj, c, DV)
    out = patchfeel.settle(obj, prev)
    assert out is not None
    after = verts_of(obj)
    bpy.ops.ed.undo_push(message="moved")
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    if obj.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")
    V7 = verts_of(obj)
    assert np.array_equal(V7, V0), f"undo did not restore the mesh ({len(changed_points(V7, V0))} points differ)"
    assert np.array_equal(patchfeel.hidden_points(obj), roles.interior), "hidden interiors lost on Undo"
    # the rule sees the undo as a change it must not answer: nothing written, whatever it thought the last state was
    assert patchfeel.settle(obj, after) is None and np.array_equal(verts_of(obj), V0)
    assert not store.manual_interior(obj, Np).any()
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    say("E undo: mesh restored, the rule writes nothing on the restore, export byte-identical, hidden kept")

    # ---------------------------------------------------------------------------------------------------- F
    scene = bpy.context.scene
    scene.bz_patch_rules = False
    bpy.ops.object.mode_set(mode="EDIT")
    prev = verts_of(obj)
    move(obj, c, DV)
    assert patchfeel.settle(obj, prev) is None, "the rule ran with Patch Rules off"
    V8 = verts_of(obj)
    assert changed_points(prev, V8) == {c} and (PR.interior_error(V8, patches)[its] > PR.TOL).any()
    move(obj, c, -DV)
    assert np.array_equal(verts_of(obj), V0)
    scene.bz_patch_rules = True
    say(f"F rules off: corner {c} moved alone, its patches off the rule (raw editing)")

    # ---------------------------------------------------------------------------------------------------- H
    # a G drag over several frames, then cancelled (Esc restores the corner exactly): handles and interiors return
    # bit-exactly (anchors), the export is byte-identical
    patchfeel.forget(obj.name)
    prev = verts_of(obj)
    for k, step in enumerate((0.01, 0.03, 0.05)):
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        bm.verts[c].co = (V0[c] + np.array([0.0, 0.0, step])).astype(np.float32)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        out = patchfeel.settle(obj, prev)
        assert out is not None
        prev = verts_of(obj)
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[c].co = V0[c].astype(np.float32)                              # Esc
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    out = patchfeel.settle(obj, prev)
    assert out is not None
    V9 = verts_of(obj)
    off = changed_points(V9, V0)
    assert not off, f"{len(off)} points off after a cancelled drag: {sorted(off)[:8]}"
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    say(f"H a 3-frame drag of corner {c} cancelled: every point back bit-exactly, export byte-identical")

    # ---------------------------------------------------------------------------------------------------- G
    assert bpy.ops.bz.select_kind(kind="HANDLE") == {"FINISHED"}
    sel = selected(obj)
    assert not (sel & roles.interior).any() and sel.sum() == roles.handle.sum(), (int(sel.sum()), int(roles.handle.sum()))
    select_only(obj, c)
    full = ops.point_selection(obj)
    frames = {int(patches[p, i]) for p in its for i, cc, _h1, _h2 in PR.INTERIOR_FRAME if int(patches[p, cc]) == c}
    assert set(np.nonzero(full)[0].tolist()) == {c} | frames, set(np.nonzero(full)[0].tolist()) ^ ({c} | frames)
    bpy.ops.mesh.select_all(action="DESELECT")
    try:
        r = bpy.ops.bz.reset_interiors()
    except RuntimeError as e:
        r = {"CANCELLED"}; assert "already on the rule" in str(e), e
    assert r == {"CANCELLED"} and np.array_equal(verts_of(obj), V0), "Auto Interiors rewrote a stock net"
    bpy.ops.object.mode_set(mode="OBJECT")
    identical(obj)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    bpy.ops.object.mode_set(mode="EDIT")
    assert patchfeel.settle(cube, np.zeros((8, 3))) is None
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"G Select > Handles: {int(sel.sum())} handles, no hidden point; corner {c} selected -> {len(frames)} hidden "
        f"interiors follow it for the skin edits; Auto Interiors on a stock net: no-op; a plain mesh is ignored")
    blender_native.unregister()
    return True


try:
    ok = run()
    open("/tmp/bz_patch_feel_done.txt", "w").write("PASS\n" if ok else "FAIL\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_patch_feel_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    say("ERROR", traceback.format_exc())
    sys.exit(1)
