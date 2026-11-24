"""Animation ENCODE round-trip, headless Blender.
    blender --background --python blender_native/tests/anim_encode_test.py
Bake an animation onto the rig, sample the posed armature, encode it back to SGM tracks, and
confirm the decoded result reproduces the original animation (encode is the inverse of decode).
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, rig  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = REPO + "/Assets/stock/albatross.sgm"
_log = open("/tmp/bz_animenc_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    model = core.load_creature(SRC).model
    fly = model.animations[0]

    # bake Fly onto the rig (creates the Action), frame_start=1; file frames 0..K land on 1..K+1
    nframes = rig.apply_animation(arm, model, 0, frame_start=1)
    assert nframes == fly.frame_count + 1, (nframes, fly.frame_count)
    scene = bpy.context.scene

    # sample the posed armature per frame -> SGM world matrices
    W = []
    for f in range(nframes):
        scene.frame_set(1 + f)
        bpy.context.evaluated_depsgraph_get()
        frame = np.zeros((len(model.bones), 4, 4))
        for i, b in enumerate(model.bones):
            pb = arm.pose.bones.get(b.name)
            frame[i] = rig.from_blender_matrix(pb.matrix) if pb else np.eye(4)
        W.append(frame)
    times = [f / (nframes - 1) for f in range(nframes)]

    new = rig.encode_animation(model, "FlyCopy", fly.fps, times, W)      # from scratch: no template
    assert new.frame_count == fly.frame_count and new.vers == fly.vers, (new.frame_count, new.vers)
    say("encoded", new.name, "frames", new.frame_count, "tracks", len(new.tracks))

    # compare at SAMPLED frame times (encode fidelity); between-frame drift is just resampling
    errs = []
    for f in (5, 11, 17):
        t = f / (nframes - 1)
        a = R.animated_world_vertices(model, new, t)
        b = R.animated_world_vertices(model, fly, t)
        errs.append(float(np.abs(a - b).max()))
    scale = float(np.abs(R.animated_world_vertices(model, fly, 0.5)).max())
    say(f"ANIMENC_OK max_err@frames={max(errs):.4e} per_f={['%.1e'%e for e in errs]} scale={scale:.2f}")

    # full write path: export with the new animation appended, re-read, decode, compare
    nv = len(obj.data.vertices)
    arr = np.empty(nv * 3); obj.data.vertices.foreach_get("co", arr)
    out = core.export_bytes(SRC, arr.reshape(nv, 3), add_animations=[new])
    open("/tmp/bz_anim_out.sgm", "wb").write(out)
    from ICParse.sgm import parser as sgm_parser
    m2 = sgm_parser.read("/tmp/bz_anim_out.sgm")
    names = [a.name for a in m2.animations]
    added = "FlyCopy" in names and len(m2.animations) == len(model.animations) + 1
    new2 = next(a for a in m2.animations if a.name == "FlyCopy")
    werr = float(np.abs(R.animated_world_vertices(m2, new2, 11 / (nframes - 1))
                        - R.animated_world_vertices(model, fly, 11 / (nframes - 1))).max())
    say(f"ANIMWRITE_OK added={added} count={len(m2.animations)} reread_err={werr:.2e}")

    # the UI path (Save Animation is retired: an Action IS a clip): a new Action on the rig holding the sampled frames,
    # then Export -- the untouched creature's geometry and skeleton are carried verbatim, the other clips kept
    from blender_native import actions as bzactions
    names = rig.source_bone_names(arm, model)
    parents = [None if b.parent is None else int(b.parent) for b in model.bones]
    samples = bzactions.basis_samples(names, parents, R.bind_world_matrices(model), W)
    baked = arm.animation_data.action                # apply_animation keyed an untagged Action: not a clip here
    arm.animation_data.action = None
    if baked is not None and baked.get(bzactions.KEY_CLIP) is None:
        bpy.data.actions.remove(baked)
    op_act = bzactions.make_action(arm, "FlyOp", names, samples, frame_start=1, fps=fly.fps)
    arm.animation_data.action = None
    assert not hasattr(bpy.types, "BZ_OT_save_anim") and "save_anim" not in dir(bpy.ops.bz), "Save Animation is retired"
    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.bz.export_sgm(filepath="/tmp/bz_saveanim_out.sgm")
    assert res == {"FINISHED"}, res
    m3 = sgm_parser.read("/tmp/bz_saveanim_out.sgm")
    assert [a.name for a in m3.animations] == [a.name for a in model.animations] + ["FlyOp"], m3.animations
    assert m3.vertices == model.vertices and [b.transform for b in m3.bones] == [b.transform for b in model.bones]
    op = next(a for a in m3.animations if a.name == "FlyOp")
    oerr = float(np.abs(R.animated_world_vertices(m3, op, 11 / (nframes - 1))
                        - R.animated_world_vertices(model, fly, 11 / (nframes - 1))).max())
    say(f"ACTION_EXPORT_OK count={len(m3.animations)} err={oerr:.2e} (action {op_act.name})")
    assert oerr < max(1e-3, 1e-4 * scale), oerr
    blender_native.unregister()
    return max(errs), scale, added, werr


import traceback
try:
    err, scale, added, werr = run()
    ok = err < max(1e-3, 1e-4 * scale) and added and werr < max(1e-3, 1e-4 * scale)
    open("/tmp/bz_animenc_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" err={err} added={added} werr={werr}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_animenc_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
