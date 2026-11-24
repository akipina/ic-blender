"""In-Blender test: clips as named Actions, edited and exported natively. Run:
    blender --background --python blender_native/tests/anim_actions_test.py
1) import a creature: one Action per clip (named like the clip, on a muted NLA track), the first
   Action reproduces ICParse's animated world vertices at a clip frame;
2) untouched export -> byte-identical (the Actions still hash like at import);
3) edit one Action (move a bone's key), rename another, delete a third, add a new Action -> export:
   the edited clip re-encodes to the edited pose, the renamed one is written under its new name, the
   deleted one is gone, the new one is appended, every other clip keeps its bytes.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, store, actions as bzactions  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = os.path.join(REPO, "Assets", "stock", "snail.sgm")
OUT0, OUT1 = "/tmp/bz_actions_out0.sgm", "/tmp/bz_actions_out1.sgm"
_log = open("/tmp/bz_actions_log.txt", "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def main():
    blender_native.register()
    assert bpy.ops.bz.import_sgm(filepath=SRC) == {"FINISHED"}
    obj = bpy.context.active_object
    arm = bpy.data.objects[obj["bz_rig"]]
    model = SP.read(SRC)
    clips = [a.name for a in model.animations]
    acts = bzactions.actions_of(arm)
    names = sorted(a.name for a in acts)
    assert names == sorted(clips), (names[:5], clips[:5])
    assert len(arm.animation_data.nla_tracks) == len(clips)
    print(f"ACTIONS_OK {len(acts)} actions for {len(clips)} clips")

    # 1) the first Action reproduces ICParse at a mid frame
    act = bpy.data.actions[clips[0]]
    K = max(1, model.animations[0].frame_count)
    fmid = K // 2
    bzactions._assign(arm, act)
    bpy.context.scene.frame_set(1 + fmid)
    deps = bpy.context.evaluated_depsgraph_get()
    got = np.array([v.co[:] for v in obj.evaluated_get(deps).data.vertices])
    want = core.sgm_to_blender(R.animated_world_vertices(model, model.animations[0], fmid / K))
    err = float(np.abs(got - want).max())
    print(f"POSE_OK clip={clips[0]} frame={1 + fmid} max_err={err:.2e}")
    assert err < 1e-3, err
    arm.animation_data.action = None

    # 2) untouched export is byte-identical
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.bz.export_sgm(filepath=OUT0) == {"FINISHED"}
    ident = open(OUT0, "rb").read() == open(SRC, "rb").read()
    print(f"UNTOUCHED_IDENTICAL {ident}")
    assert ident

    def clip_gaps():
        """Validate (on the in-memory export model, Actions included) -> its messages about clips."""
        bpy.context.view_layer.objects.active = obj
        assert bpy.ops.bz.validate() == {"FINISHED"}
        val = list(obj.get("bz_validation", []))
        assert not [p for p in val if "export failed" in p], val
        return [p for p in val if "clip '" in p]
    gaps0 = clip_gaps()
    print("CLIP_GAPS_UNTOUCHED", gaps0)
    assert not [p for p in gaps0 if "'idle1'" in p or "'melee1'" in p], gaps0

    # 3) edit / rename / delete / add
    edited, renamed, deleted = clips[0], clips[1], clips[2]
    act_e = bpy.data.actions[edited]
    bone = model.bones[min(3, len(model.bones) - 1)].name
    fc = next(f for f in bzactions._fcurves(act_e) if f.data_path == f'pose.bones["{bone}"].location' and f.array_index == 2)
    kp = fc.keyframe_points[len(fc.keyframe_points) // 2]
    kp.co[1] += 0.2                                         # lift the bone 0.2 at the middle key
    fc.update()
    bpy.data.actions[renamed].name = renamed + "_v2"
    bpy.data.actions.remove(bpy.data.actions[deleted])
    # a brand-new clip: copy of the (edited) first action shifted, named NewClip
    new_act = act_e.copy(); new_act.name = "NewClip"; new_act[bzactions.KEY_ARM] = arm.name
    for k in (bzactions.KEY_CLIP, bzactions.KEY_HASH):
        if k in new_act:
            del new_act[k]
    unchanged, encode, drop = bzactions.plan_export(arm)
    print("PLAN unchanged", len(unchanged), "encode", [c for _, c in encode], "drop", drop)
    # Validate sees the Action edits: renaming idle1 / deleting melee1 (both required by the stock rulebook)
    assert (renamed, deleted) == ("idle1", "melee1"), (renamed, deleted)
    gaps1 = clip_gaps()
    print("CLIP_GAPS_EDITED", gaps1)
    assert any(p.startswith("STOCK") and "'idle1' missing" in p for p in gaps1), gaps1
    assert any(p.startswith("STOCK") and "'melee1' missing" in p for p in gaps1), gaps1
    assert set(c for _, c in encode) == {edited, renamed + "_v2", "NewClip"}, encode
    assert set(drop) == {renamed, deleted}, drop
    assert bpy.ops.bz.export_sgm(filepath=OUT1) == {"FINISHED"}
    m1 = SP.read(OUT1)
    names1 = [a.name for a in m1.animations]
    assert renamed not in names1 and deleted not in names1 and renamed + "_v2" in names1 and "NewClip" in names1, names1
    # untouched clips keep their bytes (the ANIM forms of both files, by clip name)
    from ICParse.iff.document import IffDocument
    from ICParse.codecs import anim as ICanim
    def anim_bytes(path):
        out = {}
        for ch in IffDocument.load(path).find_all("ANIM"):
            try:
                out[ICanim.read_anim(ch).name] = ch.serialize()
            except Exception:
                pass
        return out
    raw0, raw1 = anim_bytes(SRC), anim_bytes(OUT1)
    kept = [c for c in clips if c not in (edited, renamed, deleted)]
    same = bool(raw0) and all(raw0.get(c) is not None and raw0.get(c) == raw1.get(c) for c in kept)
    print(f"KEPT_VERBATIM {same} ({len(kept)} clips, {len(raw0)}/{len(raw1)} ANIM forms read)")
    assert same
    # the edited clip decodes to the edited pose: the bone moved 0.2 along its own axis at the middle frame
    ai = names1.index(edited)
    W1 = R.animated_bone_world(m1, m1.animations[ai], fmid / K)
    W0 = R.animated_bone_world(model, model.animations[0], fmid / K)
    bi = model.bone_names.index(bone)
    d = W1[bi][:3, 3] - W0[bi][:3, 3]
    print(f"EDIT_ROUNDTRIP bone={bone} |delta|={np.linalg.norm(d):.4f} (expected 0.2)")
    assert abs(np.linalg.norm(d) - 0.2) < 2e-3, d
    # ... and the frame before it is untouched
    Wb1 = R.animated_bone_world(m1, m1.animations[ai], (fmid - 1) / K)
    Wb0 = R.animated_bone_world(model, model.animations[0], (fmid - 1) / K)
    assert np.abs(Wb1[bi] - Wb0[bi]).max() < 1e-4
    # renaming the Action back restores the clip: the rulebook stops reporting idle1 (melee1 stays deleted)
    bpy.data.actions[renamed + "_v2"].name = renamed
    gaps2 = clip_gaps()
    print("CLIP_GAPS_RENAMED_BACK", gaps2)
    assert not [p for p in gaps2 if "'idle1'" in p], gaps2
    assert any("'melee1' missing" in p for p in gaps2), gaps2
    blender_native.unregister()
    return ident, same


import traceback
try:
    ident, same = main()
    ok = ident and (same is not False)
    open("/tmp/bz_actions_done.txt", "w").write(("PASS" if ok else "FAIL") + f" ident={ident} kept={same}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_actions_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
