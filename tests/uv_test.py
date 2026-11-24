"""UV editing (cage) + lossless write-back, headless Blender.
    blender --background --python blender_native/tests/uv_test.py
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
_log = open("/tmp/bz_uv_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    bpy.context.view_layer.objects.active = obj

    # 1) unedited export (uv_overrides passed) stays byte-identical
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_uv0.sgm")
    ident = open("/tmp/bz_uv0.sgm", "rb").read() == open(SRC, "rb").read()
    say("UNEDITED_IDENTICAL", ident)

    # 2) build the UV cage, edit one patch's UVs, export, re-read
    bpy.ops.bz.edit_uvs()
    cage = bpy.context.active_object
    say("CAGE", cage.name, "polys", len(cage.data.polygons), "uvlayer",
        cage.data.uv_layers[0].name if cage.data.uv_layers else None)
    uvl = cage.data.uv_layers["SGM_UV"]
    # move patch 0's first corner UV to a known value
    loop0 = cage.data.polygons[0].loop_indices[0]
    uvl.data[loop0].uv = (0.123, 0.456)
    bpy.ops.bz.apply_uvs()                                # active is cage -> finds main
    bpy.context.view_layer.objects.active = obj
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_uv1.sgm")

    m_src = sgm_parser.read(SRC)
    m = sgm_parser.read("/tmp/bz_uv1.sgm")
    changed0 = list(m.patches[0].uvs) != list(m_src.patches[0].uvs)
    # the edited corner u should be ~0.123 (v un-flipped: 1-0.456)
    u_ok = abs(m.patches[0].uvs[0] - 0.123) < 1e-3 and abs(m.patches[0].uvs[1] - (1 - 0.456)) < 1e-3
    same1 = list(m.patches[1].uvs) == list(m_src.patches[1].uvs)
    say(f"UV_EDIT changed0={changed0} u_ok={u_ok} patch1_same={same1} "
        f"uv0={[round(x,3) for x in m.patches[0].uvs[:2]]}")
    blender_native.unregister()
    return ident, changed0, u_ok, same1


import traceback
try:
    ident, changed0, u_ok, same1 = run()
    ok = ident and changed0 and u_ok and same1
    open("/tmp/bz_uv_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" ident={ident} changed0={changed0} u_ok={u_ok} same1={same1}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_uv_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
