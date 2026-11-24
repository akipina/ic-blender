"""Sculpt Surface / Refit Patches, in headless Blender:
    blender --background --python blender_native/tests/sculpt_refit_test.py

A. cheetah -> Sculpt Surface (density 7): a connected sculpt mesh (seam samples merged, every sample mapped to a
   vertex at its bake position, textured), the creature and its rig hidden, Sculpt Mode with X symmetry; Export and
   Validate warn that a sculpt is in progress; a Grab-like displacement (smooth falloff around a point on the back,
   symmetric about X) -> Refit Patches: only patches with moved samples (and their neighbours through shared points)
   change, every other patch's control points are bit-identical, the residual is small and reported, the creature is
   shown again and the sculpt mesh removed; Export loads back as the same surface; the refit stays mirror-symmetric
   where the creature was; Undo restores a byte-identical export;
B. an untouched sculpt refits to a byte-identical export;
C. refusals: the creature's control points changed since the bake (stale), the sculpt's topology changed, the bake
   density tampered, the creature deleted; Discard shows the creature again, unchanged;
D. Sculpt Surface from Edit Mode.
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
from blender_native import store, core, ops, sculptops  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
OUT = "/tmp/bz_sculpt_cheetah.sgm"            # the sculpted cheetah (run_all --engine loads it in the Object Editor)
OUT_TMP = "/tmp/bz_sculpt_tmp.sgm"
_log = open("/tmp/bz_sculpt_log.txt", "w")


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


def validate_verdict(obj):
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def mesh_verts32(obj) -> np.ndarray:
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def sculpt_of(obj):
    scs = sculptops.sculpts_of(obj)
    assert len(scs) == 1, [s.name for s in scs]
    return scs[0]


def grab(sc, centre, radius, delta):
    """What a Grab brush does: move the vertices within ``radius`` of ``centre`` by ``delta`` with a smooth falloff
    (the mesh is in Object Mode). Returns the number of vertices moved."""
    P = mesh_verts32(sc).astype(np.float64)
    d = np.linalg.norm(P - np.asarray(centre), axis=1)
    w = np.clip(1.0 - (d / radius) ** 2, 0.0, 1.0) ** 2
    P = P + w[:, None] * np.asarray(delta, dtype=np.float64)
    sc.data.vertices.foreach_set("co", P.astype(np.float32).ravel())
    sc.data.update()
    return int((w > 0).sum())


# ------------------------------------------------------------------------------------------------------ A
def part_a():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    pool0 = mesh_verts32(obj).copy()
    patches = store.patches(obj)
    n = 7
    K = n * n
    ctx = bpy.context
    bpy.ops.ed.undo_push(message="imported")
    r, msg = call(bpy.ops.bz.sculpt_surface, density=n)
    assert r == {"FINISHED"}, msg
    sc = sculpt_of(obj)
    assert sc.name == obj.name + "_sculpt" and ctx.view_layer.objects.active is sc, (sc.name, ctx.view_layer.objects.active)
    assert obj.hide_get() and arm.hide_get(), "the creature and its rig are not hidden"
    assert sc.mode == "SCULPT", sc.mode
    assert sc.data.use_mirror_x, "X symmetry is off on a symmetric creature"
    assert int(sc[sculptops.KEY_DENSITY]) == n and int(sc[sculptops.KEY_NPATCH]) == len(patches)
    s2v = np.asarray(list(sc[sculptops.KEY_MAP]), dtype=np.int64)
    V, _T, _N = core.tessellate(pool0.astype(np.float64), patches, n=n)
    assert len(s2v) == len(V) == len(patches) * K and s2v.max() < len(sc.data.vertices)
    P0 = mesh_verts32(sc)
    assert len(np.unique(np.round(P0 / 1e-6), axis=0)) == len(P0), "coincident vertices left unmerged"
    assert np.abs(P0[s2v] - V.astype(np.float32)).max() <= 1e-6, "a sample is not at its bake position"
    assert not sculptops.moved_samples(sc).any()
    assert sc.data.uv_layers.get("UVMap") is not None and len(sc.data.materials) > 0, "the sculpt mesh is not textured"
    patch_attr = np.empty(len(P0), dtype=np.int32)
    sc.data.attributes[sculptops.ATTR_PATCH].data.foreach_get("value", patch_attr)
    assert patch_attr.max() == len(patches) - 1
    say(f"A sculpt: {len(P0)} vertices from {len(V)} samples (density {n}, {len(sc.data.polygons)} faces), creature + "
        f"rig hidden, Sculpt Mode, X symmetry on, textured ({len(sc.data.materials)} materials)")

    # in progress: Export and Validate warn (the sculpt is not in the export yet)
    bpy.ops.object.mode_set(mode="OBJECT")
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, msg[:200])
    val = validate_verdict(obj)
    assert any(v.startswith("WARN export: a sculpt of this creature is in progress ('" + sc.name + "': nothing moved yet)")
               for v in val), val[:6]

    # a Grab on the back: a smooth bump of 6 cm around a point on the spine (x = 0), radius 25 cm
    P = P0.astype(np.float64)
    mid = (P[:, 1].min() + P[:, 1].max()) / 2
    band = np.abs(P[:, 1] - mid) < 0.1
    top = P[band][np.argmax(P[band][:, 2])]
    centre = np.array([0.0, float(top[1]), float(top[2])])
    moved_v = grab(sc, centre, 0.25, (0.0, 0.0, 0.06))
    assert moved_v > 50, moved_v
    mv = sculptops.moved_samples(sc)
    assert mv.sum() == moved_v
    val = validate_verdict(obj)
    assert any(f"('{sc.name}': {moved_v} vertex(es) moved, not refit yet)" in v for v in val), val[:6]
    sculpted = mesh_verts32(sc).astype(np.float64)[s2v]         # the samples as sculpted
    moved_patches = np.unique(np.flatnonzero(mv[s2v]) // K)

    ctx.view_layer.objects.active = sc
    bpy.ops.ed.undo_push(message="sculpted")
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"FINISHED"}, msg
    stats = dict(obj["bz_last_refit"])
    assert not obj.hide_get() and not arm.hide_get() and ctx.view_layer.objects.active is obj and obj.select_get()
    assert bpy.data.objects.get(obj.name + "_sculpt") is None and not sculptops.sculpts_of(obj), "the sculpt mesh stayed"
    pool1 = mesh_verts32(obj)
    changed = (pool1 != pool0).any(1)
    in_moved = np.zeros(len(pool0), dtype=bool)
    in_moved[patches[moved_patches].reshape(-1)] = True
    assert changed.any() and not (changed & ~in_moved).any(), \
        f"{int((changed & ~in_moved).sum())} control point(s) moved outside the patches with moved samples"
    touched = changed[patches].any(1)
    far = np.ones(len(patches), dtype=bool); far[moved_patches] = False
    far &= ~touched
    assert int(stats["patches_refit"]) == int(touched.sum()) and int(stats["moved_points"]) == int(changed.sum())
    assert int(stats["patches_untouched"]) == int((~touched).sum()) == len(patches) - int(touched.sum())
    assert far.sum() > len(patches) // 2, "most patches should be untouched"
    assert np.array_equal(pool1[patches[far].reshape(-1)], pool0[patches[far].reshape(-1)]), "an untouched patch changed"
    # residual: the refit surface against the sculpted samples
    V1, _T, _N = core.tessellate(pool1.astype(np.float64), patches, n=n)
    res = np.linalg.norm(V1 - sculpted, axis=1)
    rms, mx = float(np.sqrt((res ** 2).mean())), float(res.max())
    assert abs(rms - stats["rms"]) < 1e-6 and abs(mx - stats["max"]) < 1e-6, (rms, mx, stats)
    assert rms < 0.0005 and mx < 0.005, f"residual rms {rms*1000:.3f} mm max {mx*1000:.3f} mm"
    # the bump is in the creature: the surface rose where the grab was
    lifted = np.abs(V1[:, 2] - V[:, 2]).max()
    assert 0.04 < lifted < 0.08, lifted
    # mirror-symmetric where the creature was: the stock cheetah's net is itself asymmetric (0.59 mm at 1 mm pairing)
    # and 104 of its 288 patches have no mirror-partner patch (the sample sets differ between sides), so the refit
    # can only be as symmetric as its input -- it must not ADD asymmetry: on-plane points stay on the plane, the
    # per-pair displacement is symmetric to a small fraction of the bump, the net's asymmetry does not grow
    pairs, on_plane = core.mirror_pairs(pool0.astype(np.float64), axis=0)
    flip = np.array([-1.0, 1.0, 1.0])
    asym0 = max(np.abs(pool0[i] - pool0[j] * flip).max() for i, j in pairs.items())
    asym1 = max(np.abs(pool1[i] - pool1[j] * flip).max() for i, j in pairs.items())
    disp = (pool1 - pool0).astype(np.float64)
    asym_d = max(np.abs(disp[i] - disp[j] * flip).max() for i, j in pairs.items())
    plane = max(abs(float(pool1[i, 0])) - abs(float(pool0[i, 0])) for i in on_plane)
    assert plane < 1e-6, plane
    assert asym_d < 0.01 * lifted, (asym_d, lifted)
    assert asym1 <= asym0 + asym_d + 1e-9, (asym1, asym0, asym_d)
    say(f"A refit: {stats['patches_refit']} patches refit ({stats['moved_points']} control points moved, "
        f"{len(moved_patches)} patches had moved samples), {stats['patches_untouched']} untouched; residual rms "
        f"{rms*1000:.3f} mm max {mx*1000:.3f} mm over {len(res)} samples; bump {lifted*100:.1f} cm; symmetry: "
        f"{len(pairs)//2} pairs, net asymmetry {asym0*1000:.2f} mm before / {asym1*1000:.2f} mm after, displacement "
        f"asymmetry {asym_d*1000:.2f} mm ({asym_d/lifted*100:.2f}% of the bump), on-plane points kept")

    # export loads back as the same surface (the Object Editor tier loads OUT)
    state, msg, data = export(obj, OUT)
    assert state == "FINISHED" and data is not None and data != SRC_BYTES, (state, msg[:200])
    back = core.load_creature(OUT)
    assert np.abs(back.verts - pool1).max() < 1e-6, np.abs(back.verts - pool1).max()
    V2, _T, _N = core.tessellate(back.verts, patches, n=n)
    assert np.abs(V2 - V1).max() < 1e-5
    say(f"A export: {len(data)} bytes -> {OUT}; re-import surface within {np.abs(V2 - V1).max():.1e}; {msg[:70]}")

    # Undo restores the creature (byte-identical), the sculpt mesh and the hidden state
    bpy.ops.ed.undo()
    assert bpy.data.objects.get("Cheetah_sculpt") is not None, "undo did not bring the sculpt mesh back"
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    obj.hide_set(False)
    identical(obj)
    say("A undo: creature byte-identical, sculpt mesh back")


# ------------------------------------------------------------------------------------------------------ B
def part_b():
    fresh()
    obj = imp()
    r, msg = call(bpy.ops.bz.sculpt_surface, density=5)
    assert r == {"FINISHED"}, msg
    sc = sculpt_of(obj)
    nverts = int(sc[sculptops.KEY_NVERTS])
    bpy.ops.object.mode_set(mode="OBJECT")
    ctx = bpy.context
    ctx.view_layer.objects.active = obj                 # Refit with the creature active finds its sculpt
    obj.hide_set(False)
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"FINISHED"}, msg
    stats = dict(obj["bz_last_refit"])
    assert stats["moved_points"] == 0 and stats["patches_untouched"] == len(store.patches(obj)) and not sculptops.sculpts_of(obj)
    identical(obj)
    say(f"B untouched sculpt (density 5, {nverts} vertices): nothing moved, export byte-identical")


# ------------------------------------------------------------------------------------------------------ C
def part_c():
    # stale: the creature's control points changed after the bake
    fresh()
    obj = imp()
    r, msg = call(bpy.ops.bz.sculpt_surface, density=4)
    assert r == {"FINISHED"}, msg
    sc = sculpt_of(obj)
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.data.vertices[10].co.z += 0.01
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"CANCELLED"} and "changed since" in msg and "Sculpt Surface again" in msg, msg
    assert sculptops.sculpts_of(obj) == [sc] and obj.hide_get(), "a refused refit changed the scene"
    obj.data.vertices[10].co.z -= 0.01
    say(f"C stale: refused ({msg.strip()[:80]}...)")
    # the bake density tampered (a density / count mismatch)
    sc[sculptops.KEY_DENSITY] = 5
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"CANCELLED"} and "density 5" in msg and "Sculpt Surface again" in msg, msg
    sc[sculptops.KEY_DENSITY] = 4
    say(f"C density tampered: refused ({msg.strip()[:80]}...)")
    # the sculpt's topology changed
    bpy.context.view_layer.objects.active = sc
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(sc.data); bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[0]], context="VERTS")
    bmesh.update_edit_mesh(sc.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"CANCELLED"} and "topology" in msg and "vertices, the bake had" in msg, msg
    say(f"C topology: refused ({msg.strip()[:80]}...)")
    # Discard shows the creature again, unchanged
    r, msg = call(bpy.ops.bz.discard_sculpt)
    assert r == {"FINISHED"}, msg
    assert not obj.hide_get() and not sculptops.sculpts_of(obj) and bpy.context.view_layer.objects.active is obj
    identical(obj)
    say("C Discard: creature shown, byte-identical")
    # the creature deleted
    r, msg = call(bpy.ops.bz.sculpt_surface, density=4)
    assert r == {"FINISHED"}, msg
    sc = sculpt_of(obj)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.objects.active = sc
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"CANCELLED"} and "no longer in the file" in msg, msg
    say(f"C creature deleted: refused ({msg.strip()[:60]}...)")


# ------------------------------------------------------------------------------------------------------ D
def part_d():
    fresh()
    obj = imp()
    bpy.ops.object.mode_set(mode="EDIT")
    assert bpy.ops.bz.sculpt_surface.poll()
    r, msg = call(bpy.ops.bz.sculpt_surface, density=4)
    assert r == {"FINISHED"}, msg
    sc = sculpt_of(obj)
    assert obj.mode == "OBJECT" and obj.hide_get() and sc.mode == "SCULPT"
    bpy.ops.object.mode_set(mode="OBJECT")
    r, msg = call(bpy.ops.bz.refit_patches)
    assert r == {"FINISHED"}, msg
    identical(obj)
    say("D from Edit Mode: baked, refit, byte-identical")


def main():
    blender_native.register()
    for part in (part_a, part_b, part_c, part_d):
        part()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_sculpt_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_sculpt_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
