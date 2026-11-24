"""Landmark-guided retarget (roadmap A3b), in headless Blender:
    blender --background --python blender_native/tests/landmark_retarget_test.py

A. identity: Create Landmarks on the cheetah, leave every Empty at the creature's own position, retarget to the
   cheetah's own bake at density 7: the TPS is the identity (residual 0 at the landmarks), no control point moves,
   the export is byte-identical and the bones are untouched;
B. shape change: the bake with the legs 25 % further apart and the head raised 10 cm, the landmarks dragged onto it
   (the same deformation applied to each Empty): the warped-then-fitted surface is within 2 mm rms of the target, the
   leg and head bones land in the moved limbs, the tables are untouched, Undo is byte-identical -- and every one of
   those numbers is better than the plain A3 retarget's on the same target (run from the same start for comparison);
C. refusals: fewer than 4 landmarks, an unknown LM_ name, a landmark naming a bone that is no longer on the rig;
   nothing changes and the export stays byte-identical.

``BZ_PARTS=b`` runs only the named parts (the whole file is ~6 minutes of solving).
"""
import os
import sys
sys.dont_write_bytecode = True
import time
import traceback

import numpy as np
import bpy
import bmesh
from mathutils import Vector

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, fitops, retargetops, landmarkops  # noqa: E402
from ICParse.codecs import fit  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
OUT = "/tmp/bz_landmark_cheetah.sgm"           # the landmark-retargeted cheetah (run_all --engine loads it)
OUT_TMP = "/tmp/bz_landmark_tmp.sgm"
DENSITY = 7
_log = open("/tmp/bz_landmark_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"


def imp(path=SRC, **kw):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False, **kw) == {"FINISHED"}
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
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
        msg = obj.get("bz_last_export", "")
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def identical(obj):
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, str(msg)[:300], None if data is None else len(data))


def mesh_verts32(obj) -> np.ndarray:
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def bake_target(obj, name):
    """Bake Surface at DENSITY -> one welded skin called ``name`` (the bake duplicates every seam sample per patch)."""
    bpy.context.scene.bz_density = DENSITY
    activate(obj)
    r, msg = call(bpy.ops.bz.bake_surface)
    assert r == {"FINISHED"}, msg
    baked = bpy.data.objects[obj.name + "_baked"]
    bm = bmesh.new(); bm.from_mesh(baked.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bm.to_mesh(baked.data); bm.free()
    baked.data.update()
    assert fitops._loose_parts(baked.data) == 1, fitops._loose_parts(baked.data)
    baked.name = name
    return baked


def rest_pose(arm):
    return {b.name: np.array(b.matrix_local, dtype=np.float64) for b in arm.data.bones}


def bone_heads(arm):
    return {b.name: np.array(b.head_local, dtype=np.float64) for b in arm.data.bones}


def tables(obj, arm):
    """Everything a retarget must leave alone (the control points and the bone rest pose are what it changes)."""
    return {"limbs": store.limb_names(obj), "patch_limb": store.patch_limb(obj).tolist(),
            "connections": [(n, b, list(int(x) for x in r)) for n, b, r in store.connections(obj)],
            "bones": store.bone_names(obj), "vert_bone": store.vert_bone(obj).tolist(),
            "rig_bones": [(b.name, b.parent.name if b.parent else "") for b in arm.data.bones],
            "patches": store.patches(obj).tolist(), "anims": list(obj.get("bz_anims", []))}


# ------------------------------------------------------------------------------- the deformation and the landmarks
def make_deform(P0):
    """The shape change as a function of position, so the target mesh AND the landmark Empties on it can be moved by
    the same rule (a user dragging each Empty onto the changed shape): the legs 25 % further apart (X scaled below the
    belly line, 40 % of the height, over a 10 cm ramp) and the head raised 10 cm (a smooth ramp over the 20 cm behind
    the head, above the belly only, so the front paws stay on the ground). Both read the ORIGINAL position."""
    z = P0[:, 2]
    belly = float(z.min() + 0.4 * (z.max() - z.min()))
    y_head = float(np.percentile(P0[:, 1], 3.0))        # the head is at -Y (the cheetah's heading); its base

    def f(P):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3).copy()
        spread = 1.0 + 0.25 * np.clip((belly - P[:, 2]) / 0.1, 0.0, 1.0)
        above = np.clip((P[:, 2] - belly) / 0.1, 0.0, 1.0)
        w = np.clip((y_head + 0.2 - P[:, 1]) / 0.2, 0.0, 1.0)
        w = w * w * (3.0 - 2.0 * w)                     # smoothstep, so the neck bends instead of shearing
        out = P.copy()
        out[:, 0] = P[:, 0] * spread
        out[:, 2] = P[:, 2] + 0.10 * w * above
        return out
    return f, belly, y_head


def deform_mesh(obj, f):
    P = mesh_verts32(obj).astype(np.float64)
    obj.data.vertices.foreach_set("co", f(P).astype(np.float32).ravel())
    obj.data.update()
    return mesh_verts32(obj).astype(np.float64)


def make_landmarks(obj, f=None, **kw):
    """Create Landmarks, then drag each Empty by ``f`` (the user placing it on the target). Returns the collection."""
    activate(obj)
    r, msg = call(bpy.ops.bz.landmarks_create, **kw)
    assert r == {"FINISHED"}, msg
    col = bpy.data.collections["Landmarks_" + obj.name]
    if f is not None:
        for o in col.objects:
            p = np.array([o.location.x, o.location.y, o.location.z], dtype=np.float64)
            o.location = Vector(f(p[None, :])[0].tolist())
    bpy.context.view_layer.update()
    return col


def far_samples(pool, patches, target_pts, diag, pct=2.0):
    """How many of the creature's surface samples (7 x 7 per patch) are farther than ``pct`` % of ``diag`` from any
    target vertex -- the honest "is the creature ON the target" number the report gives per limb."""
    S = landmarkops.surface_samples(pool, patches)
    d = np.array([np.linalg.norm(target_pts - s, axis=1).min() for s in S[::7]])   # every 7th sample: enough, fast
    return int((d > pct / 100.0 * diag).sum()), len(d), float(100 * d.max() / diag)


# ------------------------------------------------------------------------------------------------------ A
def part_a():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    pool0 = mesh_verts32(obj).copy()
    rest0 = rest_pose(arm)
    tgt = bake_target(obj, "CheetahBake")
    col = make_landmarks(obj)
    names = sorted(o.name for o in col.objects)
    assert len(names) >= 8 and "LM_Head" in names and "LM_Torso" in names and "LM_LFLeg" in names, names
    activate(obj)
    bpy.ops.ed.undo_push(message="imported")
    t = time.perf_counter()
    r, msg = call(bpy.ops.bz.landmark_retarget, target=tgt.name, regularisation=0.0)
    secs = time.perf_counter() - t
    assert r == {"FINISHED"}, msg
    pool1 = mesh_verts32(obj)
    dp = float(np.abs(pool1.astype(np.float64) - pool0.astype(np.float64)).max())
    st = dict(obj["bz_last_landmark_retarget_stats"])
    line = str(obj["bz_last_landmark_retarget"])
    assert dp <= 1e-6, dp
    assert int((pool1 != pool0).any(1).sum()) == 0 and st["moved_points"] == 0, (dp, st)
    assert st["warped_points"] == 0, st["warped_points"]
    assert st["tps_max_residual"] < 1e-9, st["tps_max_residual"]
    assert len(st["landmarks"]) == len(names) and st["bones_moved"] == 0, st
    assert st["rms"] < 1e-6 and st["max"] < 1e-5, st
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9), b.name
    assert "TPS residual at the landmarks max 0.00 mm" in line, line
    assert "no control point moved" in str(obj.get("bz_last_landmark_retarget", "")) or "rms 0.0 mm" in line, line
    identical(obj)
    say(f"A identity: {len(names)} landmarks at the creature's own positions ({', '.join(n[3:] for n in names)}), "
        f"{len(tgt.data.vertices)} target vertices, TPS residual {1000 * st['tps_max_residual']:.2e} mm, max control "
        f"point move {dp:.1e} m, 0 moved, bones untouched, export byte-identical, {secs:.1f} s\n  {line}")


# ------------------------------------------------------------------------------------------------------ B
def part_b():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    pool0 = mesh_verts32(obj).astype(np.float64)
    patches = store.patches(obj)
    rest0 = rest_pose(arm)
    heads0 = bone_heads(arm)
    tab0 = tables(obj, arm)
    rings = retargetops.ring_points(store.connections(obj), len(pool0))
    base = bake_target(obj, "CheetahMoved")
    f, belly, y_head = make_deform(mesh_verts32(base).astype(np.float64))
    P = deform_mesh(base, f)
    diag = fitops.bounding_diagonal(P)
    # where the tip bones must end up: the same rule that moved the target moved their landmarks
    want = {n: f(np.array(heads0[n])[None, :])[0] for n in heads0}
    legs = [n for n in heads0 if "endfoot" in n.lower()]
    head_bones = [n for n in heads0 if n.lower() in ("head", "endnose", "nose", "jaw1")]
    assert len(legs) == 4 and head_bones, (legs, head_bones)
    say(f"B target: legs 25 % apart below z={belly:.3f}, head +10 cm ahead of y={y_head + 0.2:.3f} "
        f"({len(P)} vertices, diagonal {diag:.3f} m); the 4 foot bones must move to "
        + ", ".join(f"{n} {np.round(want[n], 3)}" for n in legs))

    def run(landmark, **kw):
        """One retarget from the untouched creature (objects re-fetched by name: an Undo between the runs
        invalidates every reference); returns its numbers."""
        cre = next(o for o in bpy.data.objects if store.is_native(o))
        tgt = bpy.data.objects["CheetahMoved"]
        activate(cre)
        bpy.ops.ed.undo_push(message="before")
        t = time.perf_counter()
        if landmark:
            make_landmarks(cre, f)
            activate(cre)
            r, msg = call(bpy.ops.bz.landmark_retarget, target=tgt.name, scale_to_target=False, **kw)
        else:
            r, msg = call(bpy.ops.bz.retarget_mesh, target=tgt.name, scale_to_target=False, **kw)
        assert r == {"FINISHED"}, msg
        secs = time.perf_counter() - t
        key = "bz_last_landmark_retarget" if landmark else "bz_last_retarget"
        pool = mesh_verts32(cre).astype(np.float64)
        st = dict(cre[key + "_stats"])
        line = str(cre[key])
        h = bone_heads(store.rig(cre))
        bone_err = {n: float(np.linalg.norm(h[n] - want[n])) for n in want}
        far, n_s, far_max = far_samples(pool, patches, P, diag)
        return {"pool": pool, "st": st, "line": line, "heads": h, "bone_err": bone_err, "far": far,
                "samples": n_s, "far_max": far_max, "seconds": secs}

    plain = run(False)
    say(f"B plain A3: {plain['line']}")
    bpy.ops.ed.undo()                       # back to the imported creature (the landmark run starts from the same net)
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    assert np.array_equal(mesh_verts32(obj).astype(np.float64), pool0), "undo did not restore the plain run"
    lm = run(True)
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    arm = store.rig(obj)
    pool1, st, line = lm["pool"], lm["st"], lm["line"]
    say(f"B landmark:  {line}")
    # the surface reaches the target
    _, _, _, d = fit.project(P, pool1, patches, n=9, candidates=retargetops.CANDIDATES)
    rms, mx = float(np.sqrt((d ** 2).mean())), float(d.max())
    assert abs(rms - st["rms"]) < 1e-6, (rms, st["rms"])
    assert rms < 0.002, f"residual rms {1000 * rms:.2f} mm (wanted < 2 mm)"
    # the default Warp Smoothing (1e-3 of the landmarks' extent) trades an exact pass through each landmark for a
    # warp that does not ripple: sub-millimetre on a 2.7 m creature, and the report says by how much
    assert st["tps_max_residual"] < 0.001, st["tps_max_residual"]
    assert st["warped_points"] > len(pool0) // 2, st["warped_points"]
    assert np.array_equal(pool1[rings], pool0[rings]) or st["rings_warped"], "the rings neither warped nor pinned"
    # the bones landed in the moved limbs (the TPS carried them, the fit added its share)
    leg_lm = max(lm["bone_err"][n] for n in legs)
    leg_pl = max(plain["bone_err"][n] for n in legs)
    head_lm = max(lm["bone_err"][n] for n in head_bones)
    head_pl = max(plain["bone_err"][n] for n in head_bones)
    say("B bone placement (mm from where the deformation put each bone -- landmark / plain A3): "
        + "; ".join(f"{n} {1000 * lm['bone_err'][n]:.1f} / {1000 * plain['bone_err'][n]:.1f}"
                    for n in legs + head_bones))
    # the foot bones ARE landmarks (the TPS passes through them); the head bones are not -- no landmark says where
    # the skull went, only where the snout tip and the neck root did, so the warp interpolates them and the number
    # is bigger. Both have to beat the plain retarget's
    assert leg_lm < 0.01, f"a foot bone is {1000 * leg_lm:.1f} mm from the moved leg"
    assert leg_lm < leg_pl and head_lm < head_pl, (leg_lm, leg_pl, head_lm, head_pl)
    # ... and every headline number beats the plain retarget's on the same target
    assert rms < plain["st"]["rms"], (rms, plain["st"]["rms"])
    assert lm["far"] < plain["far"] and lm["far_max"] < plain["far_max"], (lm["far"], plain["far"])
    say(f"B bones: foot bones within {1000 * leg_lm:.1f} mm of the moved legs (plain A3 {1000 * leg_pl:.1f} mm), "
        f"head bones within {1000 * head_lm:.1f} mm (plain {1000 * head_pl:.1f} mm); {st['bones_moved']} moved, "
        f"max {1000 * st['bone_max_move']:.1f} mm, turned by up to {np.degrees(st['bone_max_turn']):.1f} deg")
    say(f"B fidelity: landmark rms {1000 * rms:.2f} mm / max {1000 * mx:.2f} mm vs plain "
        f"{1000 * plain['st']['rms']:.2f} / {1000 * plain['st']['max']:.2f}; creature surface samples > 2 % from the "
        f"target: {lm['far']} of {lm['samples']} (max {lm['far_max']:.1f} %) vs plain {plain['far']} "
        f"(max {plain['far_max']:.1f} %); {lm['seconds']:.0f} s vs {plain['seconds']:.0f} s")
    # the per-limb coverage the report promises
    cov = dict(st["limb_coverage"])
    assert cov and all(len(v) == 3 for v in cov.values()), cov
    assert "coverage by limb (surface samples > 2 % from any target point)" in line, line
    worst = sorted(cov.items(), key=lambda kv: -(kv[1][1] / max(kv[1][0], 1)))[:3]
    say("B per-limb coverage (samples > 2 % from the target, of the limb's samples, max %): "
        + "; ".join(f"{k} {int(v[1])} of {int(v[0])} ({v[2]:.1f} %)" for k, v in worst))
    # the tables are untouched and the file round-trips
    tab1 = tables(obj, arm)
    assert tab1 == tab0, [k for k in tab0 if tab0[k] != tab1[k]]
    state, msg, data = export(obj, OUT)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, str(msg)[:200])
    back = core.load_creature(OUT)
    err = float(np.abs(back.verts - pool1).max())
    assert err < 1e-5, err
    val = list(obj.get("bz_validation", []))
    activate(obj)
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    val = list(obj.get("bz_validation", []))
    blocking = core.blocking(val)
    assert not blocking, blocking
    say(f"B export: {len(data)} bytes -> {OUT}; re-import within {err:.1e} m; Validate {len(val)} lines, 0 blocking")
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    arm = store.rig(obj)
    assert np.array_equal(mesh_verts32(obj).astype(np.float64), pool0), "undo did not restore the control points"
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9), f"undo did not restore bone {b.name}"
    identical(obj)
    say("B undo: control points and bind pose back, export byte-identical")


# ------------------------------------------------------------------------------------------------------ C
def part_c():
    fresh()
    obj = imp()
    pool0 = mesh_verts32(obj).copy()
    arm = store.rig(obj)
    rest0 = rest_pose(arm)
    tgt = bake_target(obj, "CheetahBake")
    col = make_landmarks(obj)
    keep = ["LM_Head", "LM_Torso", "LM_Tail"]
    for o in list(col.objects):
        if o.name not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    activate(obj)
    r, msg = call(bpy.ops.bz.landmark_retarget, target=tgt.name)
    assert r == {"CANCELLED"} and "3 landmarks found" in msg and "at least 4" in msg, msg
    say(f"C refused (3 landmarks): {msg.strip()[:130]}")
    col = make_landmarks(obj)                                  # replaces the three, adds the rest back
    bad = bpy.data.objects.new("LM_Wibble", None)
    col.objects.link(bad)
    activate(obj)
    r, msg = call(bpy.ops.bz.landmark_retarget, target=tgt.name)
    assert r == {"CANCELLED"} and "LM_Wibble is neither a donor landmark nor a rig bone" in msg, msg
    assert "LM_Head" in msg and "any rig bone by name" in msg, msg
    say(f"C refused (unknown name): {msg.strip()[:130]}")
    bpy.data.objects.remove(bad, do_unlink=True)
    identical(obj)                                             # nothing written by a refusal
    # a landmark named after a RIG BONE resolves to that bone's head (the correspondence the limb tips do not offer)
    dl = landmarkops.creature_landmarks(obj)
    heads, _parents = landmarkops._bone_heads(obj, arm)
    p = np.array([0.0, 0.0, 2.0])
    used, pos = landmarkops.resolve_landmarks({"Head": p, "Torso": p, "LFLeg": p, "tail3": p}, dl, heads,
                                              store.bone_names(obj))
    assert [l.kind for l in used] == ["tip", "centre", "tip", "bone"], [(l.name, l.kind) for l in used]
    assert used[-1].bone == "tail3" and np.allclose(used[-1].position, heads["tail3"]), used[-1]
    assert np.array_equal(pos, np.tile(p, (4, 1))), pos
    say(f"C a rig bone by name: LM_tail3 resolves to bone tail3's head {np.round(used[-1].position, 4)}")
    # a landmark naming a bone the rig no longer has (renamed in Blender, which Export follows -- C8)
    gone = "ear_L"
    assert gone in [b.name for b in arm.data.bones], [b.name for b in arm.data.bones][:8]
    arm.data.bones[gone].name = gone + "_renamed"
    stale = bpy.data.objects.new("LM_" + gone, None)
    col.objects.link(stale)
    stale.location = Vector((0.0, 0.0, 1.0))
    activate(obj)
    r, msg = call(bpy.ops.bz.landmark_retarget, target=tgt.name)
    assert r == {"CANCELLED"} and f"LM_{gone} names bone '{gone}'" in msg and "not on the rig" in msg, msg
    say(f"C refused (bone not on the rig): {msg.strip()[:150]}")
    assert np.array_equal(mesh_verts32(obj), pool0) and "bz_last_landmark_retarget" not in obj
    for b in arm.data.bones:
        n = gone if b.name == gone + "_renamed" else b.name
        assert np.allclose(np.array(b.matrix_local), rest0[n], atol=1e-9), b.name
    say("C nothing changed: the control points and the bind pose are as imported")


def main():
    blender_native.register()
    want = os.environ.get("BZ_PARTS", "abc")
    for key, part in (("a", part_a), ("b", part_b), ("c", part_c)):
        if key in want:
            part()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_landmark_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_landmark_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
