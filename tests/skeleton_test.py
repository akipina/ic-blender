"""Skeleton authoring: edit a rest bone, export, and confirm (a) the bone's bind transform is
written and (b) the creature's SHAPE is preserved (world positions unchanged). Headless Blender.
    blender --background --python blender_native/tests/skeleton_test.py
"""
import math
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
from mathutils import Matrix

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = REPO + "/Assets/stock/albatross.sgm"
_log = open("/tmp/bz_skel_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    model = core.load_creature(SRC).model
    W_orig = R.world_vertices(model)

    # 1) unedited export (bone transforms read from armature) stays byte-identical
    bpy.context.view_layer.objects.active = obj
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_sk0.sgm")
    ident = open("/tmp/bz_sk0.sgm", "rb").read() == open(SRC, "rb").read()
    say("UNEDITED_IDENTICAL", ident)

    # 2) rotate one rest bone (a mid-skeleton bone with children)
    bi = next((i for i, b in enumerate(model.bones)
               if any(c.parent == i for c in model.bones)), 3)
    bone_name = model.bones[bi].name
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones[bone_name]
    eb.matrix = eb.matrix @ Matrix.Rotation(math.radians(15), 4, "Z")
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_sk1.sgm")
    m2 = sgm_parser.read("/tmp/bz_sk1.sgm")
    bone_changed = not np.allclose(m2.bones[bi].transform, model.bones[bi].transform, atol=1e-4)
    W2 = R.world_vertices(m2)
    geo_err = float(np.abs(W2 - W_orig).max())
    scale = float(np.abs(W_orig).max())
    say(f"SKEL bone='{bone_name}' bone_changed={bone_changed} shape_err={geo_err:.3e} scale={scale:.2f}")

    blender_native.unregister()
    return ident, bone_changed, geo_err, scale


import traceback
try:
    ident, bone_changed, geo_err, scale = run()
    ok = ident and bone_changed and geo_err < max(1e-3, 1e-4 * scale)
    open("/tmp/bz_skel_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" ident={ident} bone_changed={bone_changed} shape_err={geo_err}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_skel_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
