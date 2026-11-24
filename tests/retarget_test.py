"""Retarget Creature to Mesh (roadmap A3), in headless Blender:
    blender --background --python blender_native/tests/retarget_test.py

A. identity: the cheetah retargeted to its own bake at density 7 (Bake Surface, seams welded): no control point
   moves, the export is byte-identical, the bones are untouched, the report says so;
B. shape change: the cheetah retargeted to its bake with a 6 cm bulge on the back and 10 % longer legs (a script
   deformation): the residual is small, the limb / connection / skeleton / skinning tables are unchanged, the ring
   control points are pinned exactly, the bones moved into the new skin by the mean displacement of the points
   they own (max move reported), Export re-imports as the same surface, Validate has no blocking line, the Quick
   combine tier still combines it, Undo restores a byte-identical creature and the bind pose; with Pin Rings off the
   rings move with the fit and the report says so;
C. refusals: no target, an unknown name, the creature itself, a target with a REAL floating shell (one that
   survives a positional weld), a non-mesh, another creature; nothing changes. Then the import contract's other
   half: the unwelded bake (hundreds of seam-split parts, one surface) is ACCEPTED and retargets as the identity.
"""
import os
import sys
sys.dont_write_bytecode = True
import json
import time
import traceback

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, fitops, retargetops  # noqa: E402
from ICParse.codecs import fit  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
OUT = "/tmp/bz_retarget_cheetah.sgm"          # the retargeted cheetah (run_all --engine loads + combines it)
OUT_TMP = "/tmp/bz_retarget_tmp.sgm"
DENSITY = 7
_log = open("/tmp/bz_retarget_log.txt", "w")


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
        msg = obj.get("bz_last_export", "")
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def identical(obj):
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, msg[:300], None if data is None else len(data))


def validate_verdict(obj):
    activate(obj)
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def mesh_verts32(obj) -> np.ndarray:
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def bake_target(obj, name, weld=True):
    """Bake Surface at DENSITY -> a mesh object called ``name``; with ``weld`` the per-patch seam vertices are merged so
    the bake is one connected skin (the bake duplicates every seam sample per patch: 288 loose parts otherwise)."""
    bpy.context.scene.bz_density = DENSITY
    activate(obj)
    r, msg = call(bpy.ops.bz.bake_surface)
    assert r == {"FINISHED"}, msg
    baked = bpy.data.objects[obj.name + "_baked"]
    if weld:
        bm = bmesh.new(); bm.from_mesh(baked.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
        bm.to_mesh(baked.data); bm.free()
        baked.data.update()
        assert fitops._loose_parts(baked.data) == 1, fitops._loose_parts(baked.data)
    baked.name = name
    return baked


def rest_pose(arm):
    return {b.name: np.array(b.matrix_local, dtype=np.float64) for b in arm.data.bones}


def tables(obj, arm):
    """Everything a retarget must leave alone (the control points and the bone rest pose are what it changes)."""
    return {"limbs": store.limb_names(obj), "patch_limb": store.patch_limb(obj).tolist(),
            "limb_rows": repr(store.limb_rows(obj)),
            "connections": [(n, b, list(int(x) for x in r)) for n, b, r in store.connections(obj)],
            "conn_enc": store.conn_encodings(obj), "conn_flags": store.conn_flags(obj),
            "bones": store.bone_names(obj), "vert_bone": store.vert_bone(obj).tolist(),
            "rig_bones": [(b.name, b.parent.name if b.parent else "") for b in arm.data.bones],
            "patches": store.patches(obj).tolist(), "patch_type": store.patch_type(obj).tolist(),
            "anims": list(obj.get("bz_anims", []))}


def deform(tgt):
    """The shape change: legs 10 % longer below the belly line (40 % of the height above the ground) -- an axial
    stretch of a tube, which a closest-point fit only sees at the paw soles -- and the legs 25 % further apart (X
    scaled below the belly over a 10 cm ramp: a displacement along the surface normal, which the fit follows and
    the leg bones must too), plus a 6 cm smooth bulge on the back (radius 25 cm around the highest point on the
    spine, X-symmetric). Returns the deformed points."""
    P = mesh_verts32(tgt).astype(np.float64)
    z = P[:, 2]
    belly = float(z.min() + 0.4 * (z.max() - z.min()))
    low = z < belly
    P[low, 2] = belly - 1.1 * (belly - z[low])
    P[:, 0] *= 1.0 + 0.25 * np.clip((belly - z) / 0.1, 0.0, 1.0)
    mid = (P[:, 1].min() + P[:, 1].max()) / 2
    band = np.abs(P[:, 1] - mid) < 0.1
    top = P[band][np.argmax(P[band][:, 2])]
    c = np.array([0.0, float(top[1]), float(top[2])])
    d = np.linalg.norm(P - c, axis=1)
    w = np.clip(1.0 - (d / 0.25) ** 2, 0.0, 1.0) ** 2
    P[:, 2] += 0.06 * w
    tgt.data.vertices.foreach_set("co", P.astype(np.float32).ravel())
    tgt.data.update()
    return mesh_verts32(tgt).astype(np.float64), belly, int(low.sum()), int((w > 0).sum())


# ------------------------------------------------------------------------------------------------------ A
def part_a():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    pool0 = mesh_verts32(obj).copy()
    rest0 = rest_pose(arm)
    tgt = bake_target(obj, "CheetahBake")
    activate(obj)
    bpy.ops.ed.undo_push(message="imported")
    t = time.perf_counter()
    r, msg = call(bpy.ops.bz.retarget_mesh, target=tgt.name)
    secs = time.perf_counter() - t
    assert r == {"FINISHED"}, msg
    pool1 = mesh_verts32(obj)
    dp = np.abs(pool1.astype(np.float64) - pool0).max()
    assert dp <= 1e-6, dp
    changed32 = int((pool1 != pool0).any(1).sum())          # the float32 snap: a byte changes only if this does
    st = dict(obj["bz_last_retarget_stats"])
    line = str(obj["bz_last_retarget"])
    assert changed32 == 0 and st["moved_points"] == 0, (changed32, st)
    assert abs(st["scale"] - 1.0) < 1e-6 and st["bones_moved"] == 0 and st["rings_pinned"] and st["symmetrized"], st
    assert st["rms"] < 1e-6 and st["max"] < 1e-5, st
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9), b.name
    assert line.startswith(f"Retargeted {obj.name} to CheetahBake: {len(store.patches(obj))} patches, rms 0.0 mm"), line
    assert "rings pinned (" in line and "symmetry kept (" in line, line
    identical(obj)
    say(f"A identity: {len(tgt.data.vertices)} target vertices (density {DENSITY}, welded), max control point move "
        f"{dp:.1e}, {changed32} float32 changes, scale {st['scale']:.6f}, bones unmoved, export byte-identical, "
        f"{secs:.1f} s\n  {line}")


# ------------------------------------------------------------------------------------------------------ B
def part_b():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    pool0 = mesh_verts32(obj).astype(np.float64)
    patches = store.patches(obj)
    rest0 = rest_pose(arm)
    tab0 = tables(obj, arm)
    rings = retargetops.ring_points(store.connections(obj), len(pool0))
    assert len(rings) >= 8, len(rings)
    tgt = bake_target(obj, "CheetahDeformed")
    P, belly, n_leg, n_bump = deform(tgt)
    activate(obj)
    bpy.ops.ed.undo_push(message="imported")
    t = time.perf_counter()
    # the bake is already in the creature's frame, so no alignment (Scale to Target would shrink the longer-legged
    # target's bigger box back onto the creature's and dilute the stretch; the free-rings run below covers it)
    r, msg = call(bpy.ops.bz.retarget_mesh, target=tgt.name, scale_to_target=False)   # rings pinned, symmetry, bones
    secs = time.perf_counter() - t
    assert r == {"FINISHED"}, msg
    pool1 = mesh_verts32(obj).astype(np.float64)
    st = dict(obj["bz_last_retarget_stats"])
    line = str(obj["bz_last_retarget"])
    say(f"B pinned: {line}")
    assert np.array_equal(pool1[rings], pool0[rings]), "a pinned ring control point moved"
    assert st["rings_pinned"] and st["ring_points"] == len(rings) and st["symmetrized"], st
    assert st["moved_points"] > len(pool0) // 2, st["moved_points"]
    assert st["scale"] == 1.0 and "target taken as is" in line, (st["scale"], line)
    # the residual, re-measured on the pool as written
    aligned = P
    _, _, _, d = fit.project(aligned, pool1, patches, n=9, candidates=retargetops.CANDIDATES)
    rms, mx = float(np.sqrt((d ** 2).mean())), float(d.max())
    assert abs(rms - st["rms"]) < 1e-6 and abs(mx - st["max"]) < 1e-6, (rms, mx, st)
    # the residual left is at the belly crease (the stretch below one Z is a C0 kink in the target, which a bicubic
    # surface rounds); the pinned rings cost nothing visible here (their targets barely moved)
    to_ring = np.min(np.linalg.norm(aligned[:, None, :] - pool0[rings][None, :, :], axis=2), axis=1)
    mx_far = float(d[to_ring > 0.1].max())
    worst = int(np.argmax(d))
    assert rms < 0.0025 and mx < 0.012, f"residual rms {rms * 1000:.2f} mm, max {1000 * mx:.2f} mm"
    # the tables are untouched
    tab1 = tables(obj, arm)
    assert tab1 == tab0, [k for k in tab0 if tab0[k] != tab1[k]]
    # the bones moved by the mean displacement of the points they own (rigid skinning); the rest kept their parent's
    disp = pool1 - pool0
    owners = store.vert_bone(obj)
    names = store.bone_rig_names(obj)
    moved, biggest, owning, by_bone, drift = 0, 0.0, 0, {}, 0.0
    for k in np.unique(owners):
        name = names[int(k)]
        b = arm.data.bones[name]
        want = disp[owners == k].mean(0)
        got = np.array(b.matrix_local)[:3, 3] - rest0[name][:3, 3]
        assert np.allclose(got, want, atol=1e-5), (name, got, want)
        # orientation: float32 head / tail quantize a sub-millimetre bone's direction (front_foot -> front_endfoot) at
        # ulp / length, ~0.7 mrad, which the operator measures and reports (retargetops.move_rig_bones)
        rot = retargetops.rotation_angle(rest0[name], np.array(b.matrix_local))
        assert rot < 1e-3, f"{name} rotated by {rot:.2e} rad"
        drift = max(drift, rot)
        owning += 1
        by_bone[name] = float(np.linalg.norm(want))
        if np.linalg.norm(want) > retargetops.BONE_MIN_MOVE:
            moved += 1
            biggest = max(biggest, float(np.linalg.norm(want)))
    assert st["bones_moved"] >= moved and abs(st["bone_max_move"] - biggest) < 1e-6, (st, moved, biggest)
    assert st["bone_max_drift"] < 1e-3 and st["bone_max_drift"] >= drift - 1e-12, (st["bone_max_drift"], drift)
    assert "orientation kept to" in line, line
    assert biggest > 0.01, biggest
    # the legs moved apart: every leg bone moved outward in X (with the side it is on); the paws also went down
    legs = [n for n in names if "leg" in n.lower() and n in rest0]
    out = {n: float((np.array(arm.data.bones[n].matrix_local)[0, 3] - rest0[n][0, 3]) * np.sign(rest0[n][0, 3]))
           for n in legs}
    down = [float((np.array(arm.data.bones[n].matrix_local)[2, 3] - rest0[n][2, 3])) for n in legs]
    assert max(out.values()) > 0.015 and min(out.values()) > -1e-3, out
    assert min(down) < -0.003, dict(zip(legs, down))
    top3 = ", ".join(f"{n} {1000 * v:.1f} mm" for n, v in sorted(by_bone.items(), key=lambda kv: -kv[1])[:3])
    say(f"B bones: {st['bones_moved']} moved (max {1000 * biggest:.1f} mm: {top3}; orientation drift "
        f"{1000 * st['bone_max_drift']:.2f} mrad, float32 bones), {owning} own points; leg bones outward up to "
        f"{1000 * max(out.values()):.1f} mm, paws {1000 * min(down):.1f} mm down; legs stretched and spread below "
        f"z={belly:.3f} ({n_leg} target vertices), bump on "
        f"{n_bump}; residual rms {1000 * rms:.2f} mm, max {1000 * mx:.2f} mm ({to_ring[worst] * 100:.0f} cm from a "
        f"pinned ring), max {1000 * mx_far:.2f} mm beyond 10 cm of any ring, over {len(d)} vertices; {secs:.1f} s")
    # export loads back as the same surface (the bones' new bind re-expresses the owned points: the shape is kept)
    state, msg, data = export(obj, OUT)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, msg[:200])
    back = core.load_creature(OUT)
    err = float(np.abs(back.verts - pool1).max())
    assert err < 1e-5, err
    V1, _T, _N = core.tessellate(pool1, patches, n=DENSITY)
    V2, _T, _N = core.tessellate(back.verts, patches, n=DENSITY)
    assert np.abs(V2 - V1).max() < 1e-5
    say(f"B export: {len(data)} bytes -> {OUT}; re-import control points within {err:.1e}; {msg[:90]}")
    # Validate: nothing blocks; the Quick combine tier still combines it
    val = validate_verdict(obj)
    blocking = core.blocking(val)
    assert not blocking, blocking
    bpy.context.scene.bz_combine.validate_level = "QUICK"
    t = time.perf_counter()
    val = validate_verdict(obj)
    t_quick = time.perf_counter() - t
    bpy.context.scene.bz_combine.validate_level = "OFF"
    cerr = [x for x in val if x.startswith("COMBINE ERROR")]
    cwarn = [x for x in val if x.startswith("COMBINE WARN")]
    info = [x for x in val if x.startswith("COMBINE INFO ") and " designs (" in x]
    say("B validate QUICK:", "\n  ".join(info + cerr + cwarn) if (info or cerr or cwarn) else "(no combine lines)")
    assert info, val
    summary = json.loads(obj["bz_combine_summary"])
    assert summary["designs"] >= 10 and summary["combined"] == summary["designs"], summary   # cheetah: 11 designs
    assert summary["errors"] == 0 and summary["designs_with_issues"] == 0 and not cerr, (summary, cerr)
    say(f"B combine: {summary['designs']} designs, {summary['errors']} errors, {len(cwarn)} warnings in {t_quick:.1f} s")
    # Undo restores the creature (byte-identical) and the bind pose
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    arm = store.rig(obj)
    assert np.array_equal(mesh_verts32(obj).astype(np.float64), pool0), "undo did not restore the control points"
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9), f"undo did not restore bone {b.name}"
    identical(obj)
    say("B undo: control points and bind pose back, export byte-identical")
    # rings free, bones kept, the target scaled x2 as an object: the world shape is taken and aligned into the
    # creature's frame (scale = half the bounding-box ratio), the rings follow the target and the report says what
    # that means
    tgt = bpy.data.objects["CheetahDeformed"]
    tgt.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    _aligned, box_scale, _shift = retargetops.align_target(P, pool0, patches)
    activate(obj)
    bpy.ops.ed.undo_push(message="restored")
    r, msg = call(bpy.ops.bz.retarget_mesh, target=tgt.name, pin_rings=False, move_bones=False, iterations=6,
                  scale_to_target=True)                 # (Blender remembers an operator's last-used properties)
    assert r == {"FINISHED"}, msg
    pool2 = mesh_verts32(obj).astype(np.float64)
    st2 = dict(obj["bz_last_retarget_stats"])
    line2 = str(obj["bz_last_retarget"])
    ring_move = float(np.abs(pool2[rings] - pool0[rings]).max())
    assert ring_move > 1e-4, ring_move
    assert not st2["rings_pinned"] and st2["bones_moved"] == 0, st2
    assert abs(st2["scale"] - box_scale / 2.0) < 1e-6 and 0.49 < st2["scale"] < 0.5, (st2["scale"], box_scale)
    assert st2["rms"] < 0.0025, st2["rms"]
    assert "rings free (" in line2 and "the combiner will scale them" in line2 and "bind pose" in line2, line2
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9), f"{b.name} moved with Move Bones off"
    assert tables(obj, arm) == tab0
    say(f"B free rings (target object scaled x2): ring control points moved up to {1000 * ring_move:.1f} mm, bones "
        f"kept, fit scale {st2['scale']:.4f} = box ratio {box_scale:.4f} / 2; {line2[:160]}...")
    bpy.ops.ed.undo()
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    identical(obj)


# ------------------------------------------------------------------------------------------------------ C
def part_c():
    fresh()
    obj = imp()
    pool0 = mesh_verts32(obj).copy()
    arm = store.rig(obj)
    rest0 = rest_pose(arm)
    activate(obj)
    r, msg = call(bpy.ops.bz.retarget_mesh, target="")
    assert r == {"CANCELLED"} and "Choose a target mesh" in msg, msg
    r, msg = call(bpy.ops.bz.retarget_mesh, target="NoSuchThing")
    assert r == {"CANCELLED"} and "No object called 'NoSuchThing'" in msg, msg
    r, msg = call(bpy.ops.bz.retarget_mesh, target=obj.name)
    assert r == {"CANCELLED"} and "is the creature itself" in msg, msg
    say("C refused: no target, unknown name, the creature itself")
    # A REAL floater (a shell that survives the weld) is still refused, with the three numbers apart
    floater = bake_target(obj, "CheetahFloater")
    bm = bmesh.new(); bm.from_mesh(floater.data)
    cube = bmesh.ops.create_cube(bm, size=0.2)
    bmesh.ops.translate(bm, verts=cube["verts"], vec=(0.0, 0.0, 3.0))
    bm.to_mesh(floater.data); bm.free()
    floater.data.update()
    topo = fitops.target_topology(floater.data, fitops.bounding_diagonal(mesh_verts32(floater)))
    assert (topo.parts_raw, topo.parts, topo.welded) == (2, 2, 0), topo
    activate(obj)
    r, msg = call(bpy.ops.bz.retarget_mesh, target=floater.name)
    assert r == {"CANCELLED"} and "CheetahFloater has 2 loose parts" in msg, msg
    assert f"{topo.boundary} boundary edges and {topo.nonmanifold} true non-manifold edges" in msg, msg
    assert "Merge by Distance" in msg, msg
    say(f"C refused: a real floater ({topo.parts} parts after welding, {topo.boundary} boundary / "
        f"{topo.nonmanifold} true non-manifold edges): {msg.strip()[:90]}...")
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    bpy.context.collection.objects.link(cam)
    r, msg = call(bpy.ops.bz.retarget_mesh, target="Cam")
    assert r == {"CANCELLED"} and "Cam is a camera object, not a mesh" in msg, msg
    other = imp()
    activate(obj)
    r, msg = call(bpy.ops.bz.retarget_mesh, target=other.name)
    assert r == {"CANCELLED"} and "is a patch creature" in msg and "Bake Textured Mesh" in msg, msg
    say(f"C refused: a camera, another creature ({other.name})")
    assert np.array_equal(mesh_verts32(obj), pool0) and "bz_last_retarget" not in obj
    for b in arm.data.bones:
        assert np.allclose(np.array(b.matrix_local), rest0[b.name], atol=1e-9)
    identical(obj)
    say("C nothing changed: byte-identical")
    # ...and the seam-split target is ACCEPTED: the unwelded bake duplicates every patch-seam sample, so it is
    # hundreds of parts as modelled and one surface once welded -- the same thing an atlased glTF is. It retargets
    # to the shape it already has (the identity of part A), so this goes last: it does change the creature.
    loose = bake_target(obj, "CheetahLoose", weld=False)
    raw = fitops._loose_parts(loose.data)
    topo = fitops.target_topology(loose.data, fitops.bounding_diagonal(mesh_verts32(loose)))
    assert raw == topo.parts_raw > 1 and topo.parts == 1 and topo.welded > 0, (raw, topo)
    activate(obj)
    r, msg = call(bpy.ops.bz.retarget_mesh, target=loose.name)
    assert r == {"FINISHED"}, msg
    st = dict(obj["bz_last_retarget_stats"])
    assert st["rms"] < 1e-6 and st["max"] < 1e-5, st          # the same surface: an identity retarget
    assert np.abs(mesh_verts32(obj).astype(np.float64) - pool0).max() <= 1e-6
    say(f"C accepted: the unwelded bake ({topo.parts_raw} parts as modelled, {topo.welded} seam duplicates -> "
        f"1 part, {topo.boundary} boundary / {topo.nonmanifold} true non-manifold edges) retargets as the identity")


def main():
    blender_native.register()
    for part in (part_a, part_b, part_c):
        part()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_retarget_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_retarget_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
