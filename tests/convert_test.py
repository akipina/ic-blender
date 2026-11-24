"""Mesh -> bezier-patch creature conversion, headless Blender.
    blender --background --python blender_native/tests/convert_test.py
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core  # noqa: E402
from ICParse import validate as V  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402

_log = open("/tmp/bz_convert_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.wm.read_homefile(use_empty=True)
    # a quad mesh: a subdivided cube (still all quads, closed)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.active_object
    m = cube.modifiers.new("sub", "SUBSURF"); m.subdivision_type = "SIMPLE"; m.levels = 2
    bpy.ops.object.modifier_apply(modifier="sub")
    nq = len(cube.data.polygons)
    say(f"source mesh quads={nq} verts={len(cube.data.vertices)}")

    res = bpy.ops.bz.convert_mesh()
    say("convert", str(res))
    creature = bpy.context.active_object
    is_native = store.is_native(creature)
    npat = int(creature.get(store.KEY_NPATCH, 0))
    say(f"created native={is_native} patches={npat} cps={len(creature.data.vertices)}")

    # export it and check it's watertight / loadable
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_converted_out.sgm")
    mm = sgm_parser.read("/tmp/bz_converted_out.sgm")
    errs = [x for x in V.check_model(mm) if not x.startswith("WARN")]
    say(f"CONVERT_OK exported patches={len(mm.patches)} watertight={'CLEAN' if not errs else errs}")

    blender_native.unregister()
    return is_native, npat, not errs


import traceback
try:
    is_native, npat, watertight = run()
    ok = is_native and npat > 0 and watertight
    open("/tmp/bz_convert_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" native={is_native} patches={npat} watertight={watertight}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_convert_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
