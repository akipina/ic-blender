"""In-Blender test: scene-object animations edited as Actions and exported. Run:
    blender --background --python blender_native/tests/scene_anim_export_test.py
Lab (rigid parts) and henchmen (skinned character): untouched export byte-identical; edit a key,
rename, delete and add Actions -> the exported .sgm carries exactly that, the other clips and all
non-animation chunks byte-identical, and the edited clip decodes to the edited pose.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, scene as bzscene, actions as bzactions  # noqa: E402
from ICParse import read_scene  # noqa: E402
from ICParse.iff.document import IffDocument  # noqa: E402
from ICParse.sgm.scene import model as SM  # noqa: E402
from ICParse.codecs.pose import SceneRig  # noqa: E402

CASES = [("henchmen_lab.ebp", "Damage_04", "Damage_01", "Damage_02"), ("henchmen.sgm", "Run", "Idle1", "Melee1")]
_log = open("/tmp/bz_scene_anim_log.txt", "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def anim_forms(data_or_path):
    doc = IffDocument.load(data_or_path) if isinstance(data_or_path, str) else IffDocument.from_bytes(data_or_path)
    return {SM._anim_form_name(f): f.serialize() for f in doc.find_all("ANIM")}


def run_case(fn, edited, renamed, deleted, idx):
    path = os.path.join(REPO, "Assets", "engine_scene", fn)
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.register()
    assert bpy.ops.bz.import_sgm(filepath=path) == {"FINISHED"}
    objs = bzscene.scene_objects(); obj = objs[0]
    arm = bpy.data.objects[obj["bz_rig"]]
    src = obj["bz_source"]
    sc = read_scene(src)
    clips = [a.name for a in sc.animations]
    assert sorted(a.name for a in bzactions.actions_of(arm)) == sorted(clips)
    bpy.context.view_layer.objects.active = obj
    out0 = f"/tmp/bz_scene_anim_out0_{idx}.sgm"
    assert bpy.ops.bz.export_scene(filepath=out0) == {"FINISHED"}
    ident = open(out0, "rb").read() == open(src, "rb").read()
    print(f"{fn}: UNTOUCHED_IDENTICAL {ident}")
    assert ident
    # edit: lift a moving bone's location at the middle key of `edited`
    act = bpy.data.actions[edited]
    an = next(a for a in sc.animations if a.name == edited)
    bone = an.tracks[min(2, len(an.tracks) - 1)].bone_name
    bone = next(b.name for b in sc.bones if b.name.lower() == bone.lower())
    fc = next(f for f in bzactions._fcurves(act) if f.data_path == f'pose.bones["{bone}"].location' and f.array_index == 1)
    kp = fc.keyframe_points[len(fc.keyframe_points) // 2]; frame_mid = int(round(kp.co[0]))
    kp.co[1] += 0.3; fc.update()
    bpy.data.actions[renamed].name = renamed + "_v2"
    bpy.data.actions.remove(bpy.data.actions[deleted])
    new_act = act.copy(); new_act.name = "NewClip"; new_act[bzactions.KEY_ARM] = arm.name
    for k in (bzactions.KEY_CLIP, bzactions.KEY_HASH):
        if k in new_act:
            del new_act[k]
    out1 = f"/tmp/bz_scene_anim_out1_{idx}.sgm"
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.bz.export_scene(filepath=out1) == {"FINISHED"}
    sc1 = read_scene(out1)
    names = [a.name for a in sc1.animations]
    assert renamed not in names and deleted not in names and renamed + "_v2" in names and "NewClip" in names, names
    f0, f1 = anim_forms(src), anim_forms(out1)
    kept = [c for c in clips if c not in (edited, renamed, deleted)]
    assert all(f0[c] == f1[c] for c in kept), "a kept clip changed"
    # the edited clip: the bone moved 0.3 along its local axis at the middle frame, and not at the first
    K = an.frame_count
    rig = SceneRig(sc.bones); bi = rig.names.index(bone)
    t_mid = (frame_mid - 1) / K
    a1 = next(a for a in sc1.animations if a.name == edited)
    W0 = rig.world(rig.anim_local({t.bone_name.lower(): t for t in an.tracks}, t_mid))
    W1 = rig.world(rig.anim_local({t.bone_name.lower(): t for t in a1.tracks}, t_mid))
    d = np.linalg.norm(W1[bi][:3, 3] - W0[bi][:3, 3])
    W0a = rig.world(rig.anim_local({t.bone_name.lower(): t for t in an.tracks}, 0.0))
    W1a = rig.world(rig.anim_local({t.bone_name.lower(): t for t in a1.tracks}, 0.0))
    d0 = max(np.abs(W0a[i] - W1a[i]).max() for i in range(len(W0a)))
    print(f"{fn}: EDIT_ROUNDTRIP bone={bone} |delta at mid|={d:.4f} (expected 0.3), frame 0 drift {d0:.1e}; kept {len(kept)} clips verbatim")
    assert abs(d - 0.3) < 5e-3, d
    assert d0 < 1e-4, d0
    blender_native.unregister()


import traceback
try:
    for i, (fn, e, r, dl) in enumerate(CASES):
        if not os.path.exists(os.path.join(REPO, "Assets", "engine_scene", fn)):
            print(f"SKIP {fn}"); continue
        run_case(fn, e, r, dl, i)
    print("SCENE_ANIM_EXPORT_PASS")
    open("/tmp/bz_scene_anim_done.txt", "w").write("PASS\n")
except Exception:
    open("/tmp/bz_scene_anim_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
