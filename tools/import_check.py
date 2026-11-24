"""Batch import check, headless. For every file given, import it with the add-on and verify:
geometry (pool + patches), rig (bone count), textures decoded, limbs / connections, EVERY animation
(baked onto the rig and checked at its middle frame against ICParse's animated vertices), and that
an untouched export is byte-identical to the creature. Files that cannot be imported (scene objects,
profiles) are reported with their error instead of aborting the run.

    blender --background --factory-startup --python blender_native/tools/import_check.py -- FILE [FILE ...]

Writes a table to stdout and /tmp/bz_import_check.txt plus JSON to /tmp/bz_import_check.json.
"""
import json
import os
import sys
import time
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, rig, store  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse import ebp as EBP  # noqa: E402

_log = open("/tmp/bz_import_check.txt", "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def _source_bytes(src):
    """What an untouched export must equal: the .sgm file, or the creature a .ebp embeds."""
    if src.lower().endswith(".ebp"):
        return EBP.read(src).embedded_sgm_bytes()
    return open(src, "rb").read()


def _check_animation(obj, arm, model, ai):
    """Bake animation ai and compare the deformed mesh at the middle frame with ICParse."""
    nframes = rig.apply_animation(arm, model, ai, frame_start=1)
    fmid = nframes // 2
    bpy.context.scene.frame_set(1 + fmid)
    t = fmid / (nframes - 1) if nframes > 1 else 0.0
    deps = bpy.context.evaluated_depsgraph_get()
    got = np.array([v.co[:] for v in obj.evaluated_get(deps).data.vertices])
    want = core.sgm_to_blender(R.animated_world_vertices(model, model.animations[ai], t))
    err = float(np.abs(got - want).max())
    scale = float(np.abs(want).max()) or 1.0
    return nframes, err, err / scale


def check_one(path):
    rec = {"file": os.path.basename(path), "ok": False}
    t0 = time.time()
    try:
        bpy.ops.bz.import_sgm(filepath=path)
    except RuntimeError as e:
        rec["error"] = str(e).strip().replace("Error: ", "")
        return rec
    obj = bpy.context.active_object
    if not store.is_native(obj):
        rec["error"] = "no native object after import"
        return rec
    src = store.source_path(obj)
    data = core.load_creature(src)
    model = data.model
    rec.update(name=data.name, source=os.path.basename(src), verts=len(obj.data.vertices),
               patches=int(obj.get(store.KEY_NPATCH)), bones_file=len(model.bones),
               limbs=len(store.limb_names(obj)), connections=len(store.connections(obj)))
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    rec["bones_rig"] = len(arm.data.bones) if arm else 0
    if obj.get("bz_rig_error"):
        rec["rig_error"] = str(obj["bz_rig_error"])
    slots, tex_names, colors = store.material_slots(obj)
    rec["textures"] = len(model.textures)
    rec["textures_decoded"] = sum(1 for n in set(tex_names) if n and bpy.data.images.get(n))
    rec["materials"] = len(colors)

    # every animation
    anims = []
    worst = 0.0
    for ai, an in enumerate(model.animations):
        try:
            nframes, err, rel = _check_animation(obj, arm, model, ai)
            anims.append({"name": an.name, "frames": nframes, "rel_err": rel})
            worst = max(worst, rel)
        except Exception as e:
            anims.append({"name": an.name, "error": f"{type(e).__name__}: {str(e)[:80]}"})
    rec["animations"] = len(model.animations)
    rec["anim_ok"] = sum(1 for a in anims if "error" not in a and a["rel_err"] < 1e-4)
    rec["anim_worst_rel"] = worst
    rec["anim_detail"] = anims
    rec["anim_list_ui"] = len(list(obj.get("bz_anims", [])))

    # untouched export == the creature
    bpy.context.scene.frame_set(1)
    out = f"/tmp/bz_check_{rec['file']}.sgm"
    bpy.ops.bz.export_sgm(filepath=out)
    rec["export_identity"] = open(out, "rb").read() == _source_bytes(src)
    rec["seconds"] = round(time.time() - t0, 1)
    rec["ok"] = (rec["export_identity"] and rec["bones_rig"] == rec["bones_file"]
                 and rec["anim_ok"] == rec["animations"] and rec["textures_decoded"] >= min(1, rec["textures"]))
    # clean up so the next creature starts from an empty scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)
    return rec


def main():
    files = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    blender_native.register()
    results = []
    for p in files:
        try:
            rec = check_one(p)
        except Exception:
            rec = {"file": os.path.basename(p), "ok": False, "error": traceback.format_exc()[-400:]}
        results.append(rec)
        if "error" in rec:
            print(f"{rec['file']:28s} FAILED  {rec['error'][:110]}")
        else:
            print(f"{rec['file']:28s} {'OK ' if rec['ok'] else 'BAD'} verts={rec['verts']:5d} patches={rec['patches']:4d} "
                  f"bones={rec['bones_rig']}/{rec['bones_file']} tex={rec['textures_decoded']}/{rec['textures']} "
                  f"limbs={rec['limbs']:2d} conns={rec['connections']:2d} anims={rec['anim_ok']}/{rec['animations']} "
                  f"worst_rel={rec['anim_worst_rel']:.1e} export_identity={rec['export_identity']} {rec['seconds']}s")
    with open("/tmp/bz_import_check.json", "w") as f:
        json.dump(results, f, indent=1)
    n_ok = sum(1 for r in results if r.get("ok"))
    print(f"SUMMARY {n_ok}/{len(results)} imported and verified")
    open("/tmp/bz_import_check_done.txt", "w").write(f"{n_ok}/{len(results)}\n")


main()
