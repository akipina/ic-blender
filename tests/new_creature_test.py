"""New Creature operator: a creature built from nothing imports as a native control net with its rig
and clips, is rulebook-clean, exports byte-identical while untouched and re-exports an edit.
    blender --background --python blender_native/tests/new_creature_test.py
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core  # noqa: E402
from ICParse import validate as V  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

SEED, OUT, EDIT = "/tmp/bz_new_creature.sgm", "/tmp/bz_new_creature_out.sgm", "/tmp/bz_new_creature_edit.sgm"
_log = open("/tmp/bz_new_creature_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.wm.read_homefile(use_empty=True)
    for f in (SEED, OUT, EDIT):
        if os.path.exists(f):
            os.remove(f)
    res = bpy.ops.bz.new_creature(filepath=SEED, creature_name="Newt", detail=1)
    assert "FINISHED" in res, res
    obj = bpy.context.view_layer.objects.active
    assert store.is_native(obj), "new creature is not a native patch object"
    assert store.source_path(obj) == SEED
    arm = bpy.data.objects[obj["bz_rig"]]
    nb, nact = len(arm.data.bones), len(arm.get("bz_clip_actions", []))
    say(f"created {obj.name}: {len(obj.data.vertices)} cps, {int(obj[store.KEY_NPATCH])} patches, rig {nb} bones, {nact} clip Actions, "
        f"{len(store.connections(obj))} rings, {len(store.limb_names(obj))} limbs")
    assert nb == 48 and nact == 9
    assert len(store.connections(obj)) == 12

    n = len(obj.data.vertices)
    arr = np.empty(n * 3); obj.data.vertices.foreach_get("co", arr)
    problems = core.validate_creature(store.source_path(obj), arr.reshape(n, 3))
    problems += core.check_structure(store.limb_names(obj), store.patch_limb(obj), store.connections(obj), store.bone_names(obj),
                                    store.limb_ids(obj))
    say("validate:", problems or "clean")
    assert not core.blocking(problems), problems
    assert not [p for p in problems if p.startswith("STOCK")], problems      # complete as a stock creature

    # Validate (the operator) also runs the Quick combine tier by default: a creature built from nothing combines
    # right with the stock donors (no COMBINE ERROR) and gets a COMBINE INFO summary; it never blocks loading
    import time
    assert bpy.context.scene.bz_combine.validate_level == "QUICK"
    t = time.perf_counter()
    assert "FINISHED" in bpy.ops.bz.validate()
    val = list(obj["bz_validation"])
    combine = [p for p in val if p.startswith("COMBINE")]
    say(f"Validate with the Quick combine tier: {time.perf_counter() - t:.2f} s (budget 5 s)\n  " + "\n  ".join(combine))
    assert not [p for p in val if p.startswith("COMBINE ERROR")], combine
    assert any(p.startswith("COMBINE INFO ") and " designs (13 combined" in p for p in combine), combine
    assert not core.blocking(val), val
    assert obj.get("bz_combine_summary"), "no combine summary on the object"
    bpy.context.scene.bz_combine.validate_level = "OFF"
    t = time.perf_counter()
    assert "FINISHED" in bpy.ops.bz.validate()
    off = [p for p in obj["bz_validation"] if p.startswith("COMBINE")]
    say(f"Validate with Combine Checks Off: {time.perf_counter() - t:.2f} s\n  " + "\n  ".join(off))
    # Off: no combines and no summary, but the static precheck still runs and says it is the only check
    assert off and off[0].startswith("COMBINE INFO setup: combine checks off: static precheck only"), off
    assert not [p for p in off if p.startswith("COMBINE ERROR") or " designs (" in p], off
    assert "bz_combine_summary" not in obj
    bpy.context.scene.bz_combine.validate_level = "QUICK"

    bpy.ops.bz.export_sgm(filepath=OUT)
    same = open(SEED, "rb").read() == open(OUT, "rb").read()
    say("untouched export byte-identical:", same)
    assert same

    obj.data.vertices[0].co.z += 0.05
    obj.data.update()
    bpy.ops.bz.export_sgm(filepath=EDIT)
    m = SP.read(EDIT)
    serrs = [x for x in V.check_model(m) if not x.startswith("WARN")]
    rerrs = [x for x in V.check_creature_rulebook(m, include_structural=False) if x.startswith("ERROR")]
    say(f"edited export: {len(m.patches)} patches, structural {serrs or 'clean'}, rulebook {rerrs or 'clean'}")
    assert not serrs and not rerrs
    assert open(SEED, "rb").read() != open(EDIT, "rb").read()
    blender_native.unregister()


try:
    run()
    open("/tmp/bz_new_creature_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_new_creature_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
