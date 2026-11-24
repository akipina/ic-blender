"""Trim Stray Surface (roadmap A5), in headless Blender:
    blender --background --python blender_native/tests/trim_stray_test.py

The target throughout is the cheetah's OWN bake at density 7 (welded) with the outer half of the tail cut away,
so it is a target the creature has 14 patches more than -- exactly the pillbug's case (a donor limb the scan has
not), in miniature and with a known answer.

A. the fit is blind, the trim is not: Retarget Creature to Mesh to that target moves NOT ONE control point and
   reports rms 0.00 % (a closest-point fit only measures target -> surface, and every target vertex is already on
   the surface), the export stays byte-identical -- and Trim Stray Surface, with no target given (it takes the one
   the retarget recorded), finds 14 of 288 patches stray, every one of them in the Tail limb and none on a
   connection ring; SELECT selects exactly those 14 faces and changes nothing else; Validate carries the A5 WARN
   line and nothing blocks; Undo -> byte-identical;
B. DELETE: the 14 patches leave through the Delete Patch path and the 6-side hole is closed by Fill Hole (2
   patches): 276 patches, 0 open sides, ICParse's check_model clean (no dangling edge), the export re-imports and
   is loaded by the Object Editor (-> /tmp/bz_trim_delete_cheetah.sgm for the engine tier); Undo -> byte-identical;
C. COLLAPSE: the stray patches' own control points move onto the target, the shared and ring ones stay: the mesh
   is exactly as watertight (0 open sides), the stray patches go 14 -> 0 and the surface off the target 5.2 % ->
   0.6 %, the displacement is mirrored exactly, the export re-imports as the same surface and loads (->
   /tmp/bz_trim_collapse_cheetah.sgm); Undo -> byte-identical;
D. the refusals in words: no target and none recorded, a recorded target that has gone, an unknown name, the
   creature itself, another creature, and DELETE on a stray patch that carries a connection ring (named); nothing
   changes;
E. a recorded fit source at the WRONG SIZE (the native pillbug build's x5.2 fit input): the check would call every
   patch stray, so Validate reports the size instead -- non-blocking, and the right-sized target still gets its line.
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
from blender_native import core, fitops, patchsel, store, topoops, trimops  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
DELETED = "/tmp/bz_trim_delete_cheetah.sgm"     # 14 stray tail patches deleted, the hole filled (watertight)
COLLAPSED = "/tmp/bz_trim_collapse_cheetah.sgm"  # the same 14 folded onto the target instead
OUT_TMP = "/tmp/bz_trim_tmp.sgm"
DENSITY = 7
KEEP = 0.45                 # how much of the tail's length the target keeps
_log = open("/tmp/bz_trim_stray_log.txt", "w")


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
    bpy.ops.ed.undo_push(message="start")       # background mode: the undo system needs one explicit push


def imp(path=SRC):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def export(obj, path=OUT_TMP):
    if os.path.exists(path):
        os.remove(path)
    activate(obj)
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
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def undo(n=1):
    for _ in range(n):
        bpy.ops.ed.undo()
    return next(o for o in bpy.data.objects if store.is_native(o))


def validate_lines(obj):
    activate(obj)
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def tail_rows(obj):
    names = store.limb_names(obj)
    tail = next(i for i, n in enumerate(names) if n.endswith("_Tail"))
    return tail, np.nonzero(store.patch_limb(obj) == tail)[0]


def tail_cut(obj):
    """(axis, cut, beyond) of the plane that leaves the tail's base KEEP of its length: the Tail limb's own surface
    samples decide the axis (its longest) and which end is the tip (the one farther from the rest of the body)."""
    patches = store.patches(obj)
    S = trimops.surface_samples(verts_of(obj), patches).reshape(len(patches), trimops.SURFACE_N ** 2, 3)
    tail, rows = tail_rows(obj)
    St = S[rows].reshape(-1, 3)
    body = S[store.patch_limb(obj) != tail].reshape(-1, 3)
    ax = int(np.argmax(St.max(0) - St.min(0)))
    lo, hi = float(St.min(0)[ax]), float(St.max(0)[ax])
    tip_high = abs(hi - body.mean(0)[ax]) > abs(lo - body.mean(0)[ax])
    cut = lo + KEEP * (hi - lo) if tip_high else hi - KEEP * (hi - lo)
    return ax, cut, tip_high


def make_target(obj, name, ax=None, cut=None, beyond=None, keep_z=None):
    """Bake the creature at DENSITY, weld the per-patch seams (the bake duplicates every seam sample) and cut away
    everything beyond ``cut`` along ``ax`` (optionally only above ``keep_z``): one closed-enough target mesh that
    the creature has surface beyond."""
    bpy.context.scene.bz_density = DENSITY
    activate(obj)
    r, msg = call(bpy.ops.bz.bake_surface)
    assert r == {"FINISHED"}, msg
    baked = bpy.data.objects[obj.name + "_baked"]
    bm = bmesh.new(); bm.from_mesh(baked.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    if cut is not None:
        gone = [v for v in bm.verts if (v.co[ax] > cut) == bool(beyond) and (keep_z is None or v.co.z > keep_z)]
        bmesh.ops.delete(bm, geom=gone, context="VERTS")
    bm.to_mesh(baked.data); bm.free()
    baked.data.update()
    baked.name = name
    assert fitops._loose_parts(baked.data) == 1, f"{name}: {fitops._loose_parts(baked.data)} loose parts"
    return baked


def setup(name="CheetahNoTail", keep_z=None, keep=KEEP):
    """A fresh cheetah and the tail-less target, with the creature active and one undo step pushed."""
    fresh()
    obj = imp()
    ax, cut, tip_high = tail_cut(obj)
    if keep_z is not None:                        # the whole tail (and the rump above keep_z) gone: ring patches stray
        _, rows = tail_rows(obj)
        S = trimops.surface_samples(verts_of(obj), store.patches(obj)).reshape(-1, trimops.SURFACE_N ** 2, 3)
        St = S[rows].reshape(-1, 3)
        cut = float(St.min(0)[ax]) - 0.05 if tip_high else float(St.max(0)[ax]) + 0.05
    tgt = make_target(obj, name, ax, cut, tip_high, keep_z)
    activate(obj)
    bpy.ops.ed.undo_push(message="imported")
    return obj, tgt, ax, cut, tip_high


def stray_rows_of(obj):
    st = dict(obj["bz_last_trim_stats"])
    return [int(r) for r in st["rows"]], st


# ========================================================================================================= A
def section_a():
    obj, tgt, ax, cut, tip_high = setup()
    patches = store.patches(obj)
    pool0 = verts_of(obj)
    tail, trows = tail_rows(obj)
    # the fit is blind to surface the target does not have: every target vertex is already ON the surface
    r, msg = call(bpy.ops.bz.retarget_mesh, target=tgt.name, scale_to_target=False, iterations=4)
    assert r == {"FINISHED"}, msg
    line = str(obj["bz_last_retarget"])
    rst = dict(obj["bz_last_retarget_stats"])
    assert rst["moved_points"] == 0 and rst["rms"] < 1e-6, rst
    assert np.array_equal(verts_of(obj), pool0)
    assert obj.get("bz_retarget_source") == tgt.name, obj.get("bz_retarget_source")
    identical(obj)
    say(f"A the fit is blind: retargeting to a target the creature has a tail more than moved "
        f"{rst['moved_points']} control points, rms {rst['rms'] * 1000:.3f} mm -- export byte-identical\n  {line[:150]}")
    # the trim is not: no target given, so the retarget's record is used
    r, msg = call(bpy.ops.bz.trim_stray, action="SELECT")
    assert r == {"FINISHED"}, msg
    rows, st = stray_rows_of(obj)
    line = str(obj["bz_last_trim"])
    assert st["target"] == tgt.name and st["patches"] == len(patches), st
    assert rows, "no patch flagged stray"
    assert set(rows) <= set(int(r) for r in trows), sorted(set(rows) - set(int(r) for r in trows))
    assert f"{len(rows)} of {len(patches)} patches stray" in line and "Cheetah_Tail" in line, line
    # what a stray row is: every one of them is beyond the cut, and every tail patch wholly inside it is not
    S = trimops.surface_samples(pool0, patches).reshape(len(patches), trimops.SURFACE_N ** 2, 3)
    for r_ in rows:
        beyond = (S[r_][:, ax] > cut) if tip_high else (S[r_][:, ax] < cut)
        assert beyond.mean() >= 0.4, (r_, beyond.mean())
    for r_ in trows:
        inside = (S[r_][:, ax] <= cut) if tip_high else (S[r_][:, ax] >= cut)
        if inside.all():
            assert int(r_) not in rows, f"patch {r_} is wholly on the target and was called stray"
    # none of them is on a connection ring (so Delete may run)
    on_ring = patchsel.ring_patch_refusals(patches, rows, patchsel._ring_rows_for_plan(obj))
    assert not on_ring, on_ring
    # SELECT selected exactly them and changed nothing
    bpy.ops.object.mode_set(mode="EDIT")
    sel = patchsel.selected_rows(obj, patches)
    assert sorted(sel) == sorted(rows), (sorted(sel)[:10], sorted(rows)[:10])
    bpy.ops.object.mode_set(mode="OBJECT")
    assert np.array_equal(verts_of(obj), pool0)
    identical(obj)
    # Validate: the A5 read-out, not blocking
    val = validate_lines(obj)
    trim = [p for p in val if p.startswith("WARN trim:")]
    assert len(trim) == 1 and f"{len(rows)} patch(es) of {len(patches)}" in trim[0] and tgt.name in trim[0], val
    assert not core.blocking(val), core.blocking(val)
    say(f"A trim: {line}\n  selected exactly the {len(sel)} stray faces, export byte-identical, "
        f"{len(on_ring)} on rings; Validate: {trim[0]}")
    obj = undo(1)
    identical(obj)
    say("A undo: byte-identical")
    return len(rows)


# ========================================================================================================= B
def section_b(n_stray):
    obj, tgt, ax, cut, tip_high = setup()
    patches = store.patches(obj)
    n0, p0 = store.point_count(obj), len(patches)
    r, msg = call(bpy.ops.bz.trim_stray, target=tgt.name, action="DELETE")
    assert r == {"FINISHED"}, msg
    rows, st = stray_rows_of(obj)
    line = str(obj["bz_last_trim"])
    assert len(rows) == n_stray, (len(rows), n_stray)
    P1 = store.patches(obj)
    open_now = core.open_side_count(P1)
    assert open_now == 0, f"{open_now} open sides after the fill"
    assert not topoops.hole_loops(P1), [len(lp) for lp in topoops.hole_loops(P1)]
    assert "hole(s) filled" in line and "0 open side(s) now" in line, line
    assert len(P1) == p0 - n_stray + 2, (len(P1), p0)        # the 6-side hole is filled by a strip of 2
    state, msg2, data = export(obj, DELETED)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, msg2[:200])
    m = SP.read(DELETED)
    probs = [p for p in ICval.check_model(m) if not p.startswith("WARN")]
    assert not probs, probs
    val = validate_lines(obj)
    assert not core.blocking(val), core.blocking(val)
    say(f"B delete: {line}\n  {len(P1)} patches / {store.point_count(obj)} points (was {p0} / {n0}), "
        f"{len(data)} bytes -> {DELETED}, check_model clean, Validate 0 blocking")
    # Delete runs two journaled edits (Delete Patch, then Fill Hole), so it is two undo steps -- which the report
    # says; one undo leaves the deleted-and-filled table
    assert "Undo takes 2 steps" in line, line
    obj = undo(2)
    assert len(store.patches(obj)) == p0, len(store.patches(obj))
    identical(obj)
    say("B undo (2 steps: Delete Patch, Fill Hole): patch table and bytes back")


# ========================================================================================================= C
def section_c(n_stray):
    obj, tgt, ax, cut, tip_high = setup()
    tgt_name = tgt.name
    patches = store.patches(obj)
    pool0 = verts_of(obj)
    open_before = core.open_side_count(patches)
    rings = set(int(v) for _n, _b, ring in store.connections(obj) for v in ring)
    r, msg = call(bpy.ops.bz.trim_stray, target=tgt.name, action="COLLAPSE")
    assert r == {"FINISHED"}, msg
    rows, st = stray_rows_of(obj)
    line = str(obj["bz_last_trim"])
    pool1 = verts_of(obj)
    moved = np.nonzero(np.linalg.norm(pool1 - pool0, axis=1) > 0)[0]
    assert len(rows) == n_stray and st["stray_after"] == 0, st
    assert st["far_pct_after"] < st["far_pct"] / 4.0 and st["far_pct_after"] < 1.0, st
    assert st["max_pct_after"] <= trimops.STRAY_PCT * 1.5, st          # nothing far from the target is left
    assert len(moved) == st["moved_points"] > 0, (len(moved), st)
    assert core.open_side_count(store.patches(obj)) == open_before == 0, open_before
    assert np.array_equal(store.patches(obj), patches), "the patch table changed"
    assert not (set(int(i) for i in moved) & rings), "a connection ring control point moved"
    # only points the stray patches alone use moved
    stray_only = np.zeros(len(pool0), dtype=bool)
    stray_only[patches[rows].reshape(-1)] = True
    keep = np.ones(len(patches), dtype=bool); keep[rows] = False
    stray_only[patches[keep].reshape(-1)] = False
    assert stray_only[moved].all(), "a point a kept patch uses moved"
    # the collapse is mirrored exactly (the A3 rule: the DISPLACEMENT is mirrored, so the net keeps its own
    # asymmetry -- the stock cheetah's is 0.95 mm -- and gains none)
    pairs, on_plane = core.mirror_pairs(pool0, axis=0)
    flip = np.array([-1.0, 1.0, 1.0])
    d = pool1 - pool0
    worst = max((float(np.abs(d[i] - d[j] * flip).max()) for i, j in pairs.items()), default=0.0)
    asym0 = max((float(np.abs(pool0[i] - pool0[j] * flip).max()) for i, j in pairs.items()), default=0.0)
    asym1 = max((float(np.abs(pool1[i] - pool1[j] * flip).max()) for i, j in pairs.items()), default=0.0)
    assert st["symmetrized"] and worst < 1e-6, worst
    assert asym1 <= asym0 + 1e-9, (asym0, asym1)
    assert not any(d[i, 0] for i in on_plane), "an on-plane point moved off the plane"
    state, msg2, data = export(obj, COLLAPSED)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, msg2[:200])
    back = core.load_creature(COLLAPSED)
    err = float(np.abs(back.verts - pool1).max())
    assert err < 1e-5, err
    m = SP.read(COLLAPSED)
    probs = [p for p in ICval.check_model(m) if not p.startswith("WARN")]
    assert not probs, probs
    val = validate_lines(obj)
    assert not core.blocking(val), core.blocking(val)
    assert not [p for p in val if p.startswith("WARN trim:")], [p for p in val if p.startswith("WARN trim:")]
    say(f"C collapse: {line}\n  {len(moved)} points moved (max {1000 * st['max_move']:.0f} mm), the displacement is "
        f"mirrored to {worst:.1e} m (the net's own asymmetry {1000 * asym0:.2f} -> {1000 * asym1:.2f} mm), "
        f"{len(data)} bytes -> {COLLAPSED} (re-imports within {err:.1e}), check_model clean, Validate 0 blocking "
        f"and no stray line left")
    obj = undo(1)
    assert np.array_equal(verts_of(obj), pool0)
    identical(obj)
    say("C undo: control points and bytes back")
    # Keep the Shared Edge off: the points the stray patches share with patches that ARE on the target fold too,
    # and those patches follow (more of the webbing goes, at the price of pulling the good surface at the seam).
    # (Blender rebuilds the datablocks on undo, so the target is fetched again by name, not kept as a reference.)
    tgt = bpy.data.objects[tgt_name]
    activate(obj)
    bpy.ops.ed.undo_push(message="restored")
    r, msg = call(bpy.ops.bz.trim_stray, target=tgt.name, action="COLLAPSE", keep_shared=False)
    assert r == {"FINISHED"}, msg
    st2 = dict(obj["bz_last_trim_stats"])
    line2 = str(obj["bz_last_trim"])
    moved2 = np.nonzero(np.linalg.norm(verts_of(obj) - pool0, axis=1) > 0)[0]
    assert st2["moved_points"] > st["moved_points"] and st2["touched_kept"] > 0, (st2, st)
    assert not st2["keep_shared"] and "Keep the Shared Edge is off" in line2, line2
    assert not (set(int(i) for i in moved2) & rings), "a connection ring control point moved"
    assert core.open_side_count(store.patches(obj)) == 0
    say(f"C keep-shared off: {st2['moved_points']} points moved (against {st['moved_points']}), "
        f"{st2['touched_kept']} patches on the target follow, stray {st2['stray']} -> {st2['stray_after']}, "
        f"off the target {st2['far_pct']:.1f} % -> {st2['far_pct_after']:.1f} %, still watertight")
    obj = undo(1)
    identical(obj)


# ========================================================================================================= D
def section_d():
    fresh()
    obj = imp()
    pool0 = verts_of(obj)
    activate(obj)
    r, msg = call(bpy.ops.bz.trim_stray)
    assert r == {"CANCELLED"} and "records no fit or retarget source" in msg, msg
    r, msg = call(bpy.ops.bz.trim_stray, target="NoSuchThing")
    assert r == {"CANCELLED"} and "No object called 'NoSuchThing'" in msg, msg
    r, msg = call(bpy.ops.bz.trim_stray, target=obj.name)
    assert r == {"CANCELLED"} and "is the creature itself" in msg, msg
    other = imp()
    activate(obj)
    r, msg = call(bpy.ops.bz.trim_stray, target=other.name)
    assert r == {"CANCELLED"} and "is a patch creature" in msg, msg
    say("D refused: no target and none recorded, an unknown name, the creature itself, another creature")
    # a recorded target that has gone
    obj["bz_retarget_source"] = "CheetahGhost"
    r, msg = call(bpy.ops.bz.trim_stray)
    assert r == {"CANCELLED"} and "('CheetahGhost') is not in the file any more" in msg, msg
    del obj["bz_retarget_source"]
    say(f"D refused: {msg.strip()[:110]}")
    assert np.array_equal(verts_of(obj), pool0) and "bz_last_trim" not in obj
    identical(obj)
    # DELETE on a patch that carries a connection ring: the whole tail and the rump above it gone
    obj, tgt, ax, cut, tip_high = setup(name="CheetahNoRump", keep_z=0.55)
    patches = store.patches(obj)
    pool0 = verts_of(obj)
    r, msg = call(bpy.ops.bz.trim_stray, target=tgt.name, action="DELETE")
    assert r == {"CANCELLED"} and "on connection ring" in msg and "Collapse onto Target keeps the ring" in msg, msg
    rows, st = stray_rows_of(obj)
    on_ring = patchsel.ring_patch_refusals(patches, rows, patchsel._ring_rows_for_plan(obj))
    assert on_ring and any(f"'{nm}'" in msg for _r, nm, _s in on_ring), (msg, on_ring[:3])
    assert np.array_equal(verts_of(obj), pool0) and len(store.patches(obj)) == len(patches)
    identical(obj)
    say(f"D refused (delete on a ring patch): {msg.strip()[:170]}\n  {len(rows)} stray, {len(on_ring)} of them on "
        f"rings; nothing changed, byte-identical")
    # ... and Collapse takes the same creature, keeping every ring point where it is
    rings = set(int(v) for _n, _b, ring in store.connections(obj) for v in ring)
    r, msg = call(bpy.ops.bz.trim_stray, target=tgt.name, action="COLLAPSE")
    assert r == {"FINISHED"}, msg
    pool1 = verts_of(obj)
    moved = set(int(i) for i in np.nonzero(np.linalg.norm(pool1 - pool0, axis=1) > 0)[0])
    assert moved and not (moved & rings), len(moved & rings)
    st = dict(obj["bz_last_trim_stats"])
    assert st["stray_after"] < st["stray"] and core.open_side_count(store.patches(obj)) == 0, st
    say(f"D collapse instead: {len(moved)} points moved, 0 ring points, stray {st['stray']} -> "
        f"{st['stray_after']}, still watertight")
    obj = undo(1)
    identical(obj)


# ========================================================================================================= E
def section_e():
    """A recorded source at the WRONG SIZE says so, instead of calling every patch stray (fit-to-rig, gap 9).

    The native pillbug build fitted a copy of its scan scaled x5.2 (Blender's QuadriFlow cancels at creature size,
    which is gap 1) and scaled the creature back afterwards, so ``bz_fit_source`` named a mesh 5.2 times the
    creature's size: Validate's A5 read-out then measured distances between two things in different frames and
    reported ALL 288 patches as stray, which says nothing about the fit."""
    obj, tgt, _ax, _cut, _tip = setup()
    obj["bz_retarget_source"] = tgt.name                 # what a retarget records
    val = validate_lines(obj)
    good = [p for p in val if p.startswith("WARN trim:")]
    assert len(good) == 1 and "are surface the target" in good[0], val
    # the same target, scaled up as the pillbug's fit input was, recorded as the fit source
    big = bpy.data.objects.new(tgt.name + "Big", tgt.data.copy())
    bpy.context.collection.objects.link(big)
    from mathutils import Matrix
    big.data.transform(Matrix.Diagonal((5.2, 5.2, 5.2, 1.0)))
    big.data.update()
    obj["bz_fit_source"] = big.name
    del obj["bz_retarget_source"]
    st, _tgt2, pts = trimops.stray_of(obj, bpy.context, big.name)
    assert len(st.rows) == len(store.patches(obj)), (len(st.rows), len(store.patches(obj)))   # every patch "stray"
    val2 = validate_lines(obj)
    trim = [p for p in val2 if p.startswith("WARN trim:")]
    assert len(trim) == 1, val2
    assert "x the creature's size" in trim[0] and big.name in trim[0], trim
    assert "are surface the target" not in trim[0], trim
    assert not core.blocking(val2), core.blocking(val2)
    # the operator says the same thing (one wording, trimops.frame_mismatch) and still runs: the user asked for it
    r, msg = call(bpy.ops.bz.trim_stray, target=big.name, action="SELECT")
    assert r == {"FINISHED"}, msg
    say(f"E a source at the wrong size: the check would call {len(st.rows)} of {len(store.patches(obj))} patches "
        f"stray; Validate says instead\n  {trim[0][:170]}")
    assert "are surface the target" in good[0], good   # ... and the right-sized target still gets the A5 line


def main():
    blender_native.register()
    n = section_a()
    section_b(n)
    section_c(n)
    section_d()
    section_e()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_trim_stray_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_trim_stray_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
