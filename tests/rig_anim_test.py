"""Verify baked animation == ICParse ground truth, in headless Blender.
    blender --background --python blender_native/tests/rig_anim_test.py
Bakes an animation onto the imported armature, then checks the evaluated (deformed) mesh equals
sgm_to_blender(reconstruct.animated_world_vertices(...)) at a sampled frame.
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
_log = open("/tmp/bz_anim_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    data = core.load_creature(SRC)
    model = data.model
    say("anims", [a.name for a in model.animations][:6])

    ai = 0
    nframes = rig.apply_animation(arm, model, ai, frame_start=1)
    say("baked", nframes, "frames of", model.animations[ai].name)

    fmid = nframes // 2
    bpy.context.scene.frame_set(1 + fmid)
    t = fmid / (nframes - 1)

    deps = bpy.context.evaluated_depsgraph_get()
    got = np.array([v.co[:] for v in obj.evaluated_get(deps).data.vertices])
    want = core.sgm_to_blender(R.animated_world_vertices(model, model.animations[ai], t))
    err = float(np.abs(got - want).max())
    scale = float(np.abs(want).max())
    say(f"ANIM_OK frame={1+fmid}/{nframes} max_err={err:.4e} scale={scale:.2f} rel={err/scale:.2e}")
    blender_native.unregister()
    return err, scale


import traceback
try:
    err, scale = run()
    ok = err < max(1e-3, 1e-4 * scale)
    open("/tmp/bz_anim_done.txt", "w").write(("PASS" if ok else "FAIL") + f" err={err}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_anim_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
