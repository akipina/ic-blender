"""Delete and re-parent SOURCE bones in headless Blender (Boner increment C8): Blender's own X / Ctrl+P in the rig's
Edit Mode export.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_bone_topology_test.py

A. delete (cheetah, clips as Actions): ear_L deleted with X in the rig's Edit Mode -> the rig box shows no alert, Export
   writes /tmp/bz_boner_c8_delete.sgm: 53 bones, endear_l under head with its bind world kept, ear_L's 50 points on the
   head (every point's world kept), ear_L off the Head limb list and the LEar chain, the 15 kept clips that keyed it
   rebuilt without its track (every other track byte-identical, ICParse's decode of every kept bone unchanged), the
   summary and the warning naming the moved points, one plan warning for the dropped keys; Validate has nothing
   blocking; an edited Action (head keyed) is re-encoded without ear_L; Ctrl+Z -> byte-identical;
B. refusals, in the rig box and at Export: pelvis (root), tail1 (looked up by name: the reason), front_arm1_l (a stub
   bone), neck1 (a host); each undone -> byte-identical;
C. neck4 inserted: Add Bone neck4 under neck3, then head re-parented under neck4 (Ctrl+P: keep offset) in the rig's Edit
   Mode -> /tmp/bz_boner_c8_neck4.sgm: neck4's record right before head's with head under it, no source record moved,
   every bind world kept, the Neck IK chain completed, 18 kept clips rebuilt with the head's track re-expressed so
   ICParse's decode of every source bone is unchanged in every clip, and Blender's own playback of the head under
   O_growl equals the exported clip's decode (the head follows); Ctrl+Z (twice) -> byte-identical; head under tail4 ->
   refused as a split Head limb (rig box + Export);
D. a genuine re-parent: nose under jaw1 -> exported with the nose's tracks re-expressed; Blender's playback of the nose
   under O_growl equals the exported clip's decode at the Action's keyframes (what the modder sees is what the game
   plays); an Action keying the nose after the re-parent is re-encoded and plays the same.
E. joint limits (C8.4): every source bone stamped with its record's limits (export byte-identical), head's min/max x
   edited -> only its 8 bytes change (summary), the Bones tab shows stock + the editable copy, Reset to Stock and Undo
   byte-identical, 7 values refused, an added bone's limits written;
F. IK chains (C8.5): the source's 15 rows listed; Add / Edit / Remove IK Chain export exactly that table, refusals
   (not a path, a taken name); the stored table follows a deleted, a re-parented and a renamed bone; a non-path row is
   refused at Export and blocking in Validate; Reset IK Chains + Undo byte-identical.
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, rig, actions, ops, boneops, topology  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import anim as ICanim  # noqa: E402
from ICParse.sgm.creature.model import ik_chains  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
OUT = "/tmp/bz_boner_topo_out.sgm"
DELETE = "/tmp/bz_boner_c8_delete.sgm"
NECK4 = "/tmp/bz_boner_c8_neck4.sgm"
NOSE = "/tmp/bz_boner_c8_nose.sgm"
_log = open("/tmp/bz_boner_topo_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level
    bpy.context.preferences.edit.undo_steps = 64
    bpy.context.scene.bz_show_interiors = True


def imp(path, **kw):
    kw.setdefault("clips_as_actions", True)
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    # headless Blender's undo stack can step back only once a step lies before the one an edit pushes: the import is it
    bpy.ops.ed.undo_push(message="imported")
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def ok(op, **kw):
    res, msg = call(op, **kw)
    assert res == {"FINISHED"}, (op, kw, msg)


def exported(obj, path=OUT):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
    except RuntimeError as e:
        raise AssertionError(f"export failed: {e}")
    assert r == {"FINISHED"} and os.path.exists(path), r
    return open(path, "rb").read(), str(obj.get("bz_last_export", ""))


def export_refused(obj, words):
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.export_sgm(filepath=OUT)
    except RuntimeError as e:
        assert all(w in str(e) for w in words), (words, str(e))
        return str(e)
    raise AssertionError(f"export not refused ({words})")


def read(data, path="/tmp/bz_boner_topo_read.sgm"):
    with open(path, "wb") as f:
        f.write(data)
    return sgm_parser.read(path)


def validate_verdict(obj, level="OFF"):
    bpy.context.scene.bz_combine.validate_level = level
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    bpy.context.scene.bz_combine.validate_level = "OFF"
    return list(obj.get("bz_validation", []))


def edit_bones(arm, fn):
    obj = bpy.context.view_layer.objects.active
    mode = obj.mode
    rig._set_mode(bpy.context, obj, "OBJECT")
    rig._set_mode(bpy.context, arm, "EDIT")
    try:
        fn(arm.data.edit_bones)
    finally:
        rig._set_mode(bpy.context, arm, "OBJECT")
        bpy.context.view_layer.objects.active = obj
        rig._set_mode(bpy.context, obj, mode)


def rig_alert(obj):
    arm = store.rig(obj)
    ui._SKEL_CACHE.clear()
    return ui._skeleton_error_cached(arm, obj)


def track_bytes(m):
    out = {}
    for a in m.animations:
        form = ICanim.write_anim(a)
        banm = [c for c in form.children if c.tag == "BANM"]
        for t, c in zip(a.tracks, banm):
            out[(a.name, t.bone_name.lower())] = c.serialize()
    return out


def world_by_name(m, a=None, t=0.0):
    W = R.animated_bone_world(m, a, t) if a is not None else R.bind_world_matrices(m)
    return {b.name.lower(): W[i] for i, b in enumerate(m.bones)}


def clip_of(m, name):
    return next(a for a in m.animations if a.name == name)


def action_of(arm, clip):
    return next(a for a in actions.actions_of(arm) if str(a.get(actions.KEY_CLIP, a.name)) == clip)


def playback_err(obj, arm, m, clip, act, bone, frames=None):
    """Largest difference between Blender's world matrix of ``bone`` under ``act`` and the exported clip's decode, over
    the Action's frames (or ``frames``)."""
    job = ops.gather_export(obj, bpy.context, animations=False)
    names = ops.sample_bone_names(job, core.build_export(job.state)) or rig.current_bone_names(arm, job.source)
    i = next(k for k, x in enumerate(m.bones) if x.name == bone)
    start, end = actions.frame_range(act)
    W = actions.sample_world(arm, act, names, start, end)
    K = end - start
    fs = range(0, K + 1) if frames is None else frames
    return max(float(np.abs(R.animated_bone_world(m, clip_of(m, clip), f / K)[i] - W[f][i]).max()) for f in fs)


def key_frames(m, clip, bone):
    """The frames of the FILE's keys for ``bone`` in ``clip`` (an imported Action keys every frame; between the file's
    keys Blender and the engine interpolate differently)."""
    a = clip_of(m, clip)
    tr = next((t for t in a.tracks if t.bone_name.lower() == bone.lower()), None)
    if tr is None:
        return [0]
    K = max(1, a.frame_count)
    return sorted({int(round(k[0] * K)) for k in tr.rot_keys + tr.trans_keys})


def key_rotation(arm, act, bone, frames):
    ad = arm.animation_data or arm.animation_data_create()
    prev = ad.action
    actions._assign(arm, act)
    pb = arm.pose.bones[bone]
    pb.rotation_mode = "QUATERNION"
    for f, q in frames:
        pb.rotation_quaternion = q
        pb.keyframe_insert("rotation_quaternion", frame=f)
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    actions._assign(arm, prev)


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    src_tracks = track_bytes(src)
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    bpy.ops.ed.undo_push(message="before delete")
    edit_bones(arm, lambda eb: eb.remove(eb["ear_L"]))
    assert arm.data.bones.get("ear_L") is None and arm.data.bones["endear_l"].parent.name == "head"
    assert rig_alert(obj) is None, rig_alert(obj)
    data, msg = exported(obj, DELETE)
    m = read(data)
    assert len(m.bones) == 53 and m.find_bone("ear_L") is None
    e2 = next(i for i, b in enumerate(m.bones) if b.name == "endear_l")
    assert m.bones[m.bones[e2].parent].name == "head"
    nb, ob = world_by_name(m), world_by_name(src)
    assert max(np.abs(nb[k] - ob[k]).max() for k in nb) < 1e-6, "bind worlds kept"
    # every point's world kept: match by position (the reskin re-sorts the pool)
    w_src = np.asarray(R.world_vertices(src)); w_new = np.asarray(R.world_vertices(m))
    d = np.linalg.norm(w_src[:, None, :] - w_new[None, :, :], axis=2).min(axis=1)
    assert d.max() < 1e-5, d.max()
    head = next(i for i, b in enumerate(m.bones) if b.name == "head")
    assert int(m.bones[head].vertex_count) == 272 + 50
    assert "ear_L" not in {b.lower() for l in m.limbs for b in l.bone_names}
    assert "ear_l" not in {b.lower() for l in m.limbs for b in l.bone_names}
    assert [c.bones for c in ik_chains(m) if c.name == "LEar"] == [["endear_l"]]
    nt = track_bytes(m)
    assert not [k for k in nt if k[1] == "ear_l"] and all(nt[k] == src_tracks[k] for k in src_tracks if k[1] != "ear_l")
    assert len(m.animations) == 20
    for a2 in m.animations:
        a1 = clip_of(src, a2.name)
        for t in (0.0, 0.37, 1.0):
            W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
            assert max(np.abs(W1[k] - W2[k]).max() for k in W2 if k != "endear_l") < 1e-6
    assert "bones: deleted 1 (ear_L (50 points -> head)), taken off limb list(s) Cheetah_Head" in msg, msg
    assert "bones: re-parented 1 (endear_l: ear_L -> head)" in msg and "IK chains: 1 rebuilt (LEar: ear_L taken out" in msg
    assert "clips: 15 kept clip(s) rebuilt (15 track(s) of deleted bones dropped" in msg, msg
    info = {}
    ops.edited_model(obj, bpy.context, animations=True, info=info)
    warns = info["warnings"]
    assert any(w.startswith("50 control points of deleted bone 'ear_L' moved to its parent 'head'") for w in warns), warns
    assert any(w.startswith("deleted bone 'ear_L': its keys in 20 Action(s)") for w in warns), warns   # import keys every bone
    assert not [w for w in warns if "which the rig has no bone called" in w or w.startswith("rest pose of")], warns
    val = validate_verdict(obj)
    assert not core.blocking(val), core.blocking(val)
    # an edited Action: re-encoded without ear_L, the others kept (rebuilt)
    act = action_of(arm, "O_growl")
    key_rotation(arm, act, "head", [(3, (0.98, 0.2, 0.0, 0.0)), (9, (1.0, 0.0, 0.0, 0.0))])
    data2, msg2 = exported(obj)
    m2 = read(data2)
    assert "clips: 19 kept, 1 written" in msg2 and "clips: 15 kept clip(s) rebuilt" in msg2, msg2
    assert not any(t.bone_name.lower() == "ear_l" for a in m2.animations for t in a.tracks)
    err = playback_err(obj, arm, m2, "O_growl", act, "head")
    assert err < 2e-3, err
    bpy.ops.ed.undo()                                   # the keys
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    while arm.data.bones.get("ear_L") is None:          # the delete (one or two steps back to the push)
        bpy.ops.ed.undo()
        obj = bpy.data.objects[name]; arm = store.rig(obj)
        bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert arm.data.bones["endear_l"].parent.name == "ear_L"
    assert exported(obj)[0] == raw, "Undo of the delete: export not byte-identical"
    say("A DELETE: ear_L deleted with X -> no rig alert; export: 53 bones, endear_l under head (bind kept), 50 points on "
        "head (worlds kept), off the Head list and the LEar chain, 15 kept clips rebuilt without the ear_l track (other "
        "tracks byte-identical, every kept bone's motion unchanged), the summary / warnings name it, one plan warning; "
        f"Validate clean; an edited Action re-encoded without ear_L (head playback {err:.1e}); Undo -> byte-identical")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    raw = open(CHEETAH, "rb").read()
    obj = imp(CHEETAH, clips_as_actions=False)
    name = obj.name
    for who, words in (("pelvis", ("bone 'pelvis' is the root bone and was deleted",)),
                       ("tail1", ("looks 'tail1' up by name", "tail ground clearance", "Undo the delete")),
                       ("front_arm1_l", ("front_arm1_l' was deleted, but it is a stub bone, the only bone of stub limb "
                                         "'Cheetah_LFArm'",)),
                       ("neck1", ("connections 'Cheetah_Head', 'Cheetah_LFArm', 'Cheetah_RFArm' attach to it",))):
        arm = store.rig(obj)
        bpy.ops.ed.undo_push(message=f"before {who}")
        edit_bones(arm, lambda eb: eb.remove(eb[who]))
        alert = rig_alert(obj)
        assert alert and all(w in alert for w in words), (who, alert)
        export_refused(obj, words)
        bpy.ops.ed.undo()
        obj = bpy.data.objects[name]; arm = store.rig(obj)
        bpy.context.view_layer.objects.active = obj
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        assert arm.data.bones.get(who) is not None and rig_alert(obj) is None
        assert exported(obj)[0] == raw, who
    say("B REFUSED: pelvis (root), tail1 (looked up: the reasons), front_arm1_l (stub bone of Cheetah_LFArm), neck1 (host "
        "of 3 connections) -- in the rig box and at Export; each Undo -> byte-identical")


# ------------------------------------------------------------------------------------------------------------- C
def part_c():
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    src_tracks = track_bytes(src)
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    bpy.ops.ed.undo_push(message="before neck4")
    ok(bpy.ops.bz.add_bone, parent="neck3", name="neck4", offset=(0.0, -0.02, 0.0), length=0.04)
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj

    def reparent(eb):                                   # Ctrl+P > Keep Offset: parent set, rest untouched
        eb["head"].use_connect = False
        eb["head"].parent = eb["neck4"]
    edit_bones(arm, reparent)
    assert arm.data.bones["head"].parent.name == "neck4"
    assert rig_alert(obj) is None, rig_alert(obj)
    data, msg = exported(obj, NECK4)
    m = read(data)
    i4 = next(i for i, b in enumerate(m.bones) if b.name == "neck4")
    ih = next(i for i, b in enumerate(m.bones) if b.name == "head")
    assert ih == i4 + 1 and m.bones[ih].parent == i4 and m.bones[m.bones[i4].parent].name == "neck3"
    assert [b.name for b in m.bones if b.name != "neck4"] == [b.name for b in src.bones]
    nb, ob = world_by_name(m), world_by_name(src)
    assert max(np.abs(nb[k] - ob[k]).max() for k in ob) < 1e-6
    assert [c.bones for c in ik_chains(m) if c.name == "Neck"] == [["head", "neck1", "neck2", "neck3", "neck4"]]
    worst = 0.0
    for a2 in m.animations:
        a1 = clip_of(src, a2.name)
        K = max(1, a1.frame_count)
        for t in [k / K for k in range(0, K + 1, max(1, K // 6))] + [1.0]:
            W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
            worst = max(worst, max(np.abs(W1[k] - W2[k]).max() for k in W1))
    assert worst < 1e-5, worst
    nt = track_bytes(m)
    assert all(nt[k] == src_tracks[k] for k in src_tracks if k[1] != "head")
    assert "bones: added 1 (neck4)" in msg and "bones: re-parented 1 (head: neck3 -> neck4)" in msg, msg
    assert "IK chains: 1 completed (Neck: neck4 inserted)" in msg and "clips: 18 kept clip(s) rebuilt (18 track(s) re-expressed" in msg, msg
    assert "re-sorted" not in msg and "rest pose of" not in msg, msg
    val = validate_verdict(obj)
    assert not core.blocking(val), core.blocking(val)
    act = action_of(arm, "O_growl")
    err_keys = playback_err(obj, arm, m, "O_growl", act, "head", key_frames(m, "O_growl", "head"))
    err_all = playback_err(obj, arm, m, "O_growl", act, "head")
    assert err_keys < 1e-4, err_keys
    # head under tail4: the Head limb would have two roots (neck2 and head)
    bpy.ops.ed.undo_push(message="before tail4")
    edit_bones(arm, lambda eb: setattr(eb["head"], "parent", eb["tail4"]))
    alert = rig_alert(obj)
    assert alert and "splits limb 'Cheetah_Head' into 2 branches" in alert and "'head'" in alert, alert
    export_refused(obj, ("splits limb 'Cheetah_Head' into 2 branches",))
    edit_bones(arm, reparent)                           # back under neck4: the same file again
    again, msg_again = exported(obj)
    assert again == data, ("export after re-parenting the head back under neck4 differs", msg_again)
    for _k in range(8):                                 # Ctrl+Z back to the import (the add + the two rig edits)
        if arm.data.bones.get("neck4") is None or not bpy.ops.ed.undo.poll():
            break
        bpy.ops.ed.undo()
        obj = bpy.data.objects[name]; arm = store.rig(obj)
        bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert arm.data.bones.get("neck4") is None and arm.data.bones["head"].parent.name == "neck3", \
        [b.name for b in arm.data.bones][-4:]
    assert exported(obj)[0] == raw, "Undo of neck4: not byte-identical"
    say("C NECK4: Add Bone neck4 under neck3 + head under it (Ctrl+P) -> neck4's record right before head's, no source "
        f"record moved, binds kept, Neck chain completed, 18 clips re-tracked (ICParse motion unchanged {worst:.1e}); "
        f"Blender's O_growl playback of the head = the exported clip at its keys ({err_keys:.1e}; {err_all:.1e} over all "
        "frames: the engine's key blend); head under tail4 refused (split Head limb) in the rig box and at Export; "
        "re-parented back -> the same file; Undo back to the import -> byte-identical")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    bpy.ops.ed.undo_push(message="before nose")
    edit_bones(arm, lambda eb: setattr(eb["nose"], "parent", eb["jaw1"]))
    assert rig_alert(obj) is None, rig_alert(obj)
    data, msg = exported(obj, NOSE)
    m = read(data)
    i = next(k for k, b in enumerate(m.bones) if b.name == "nose")
    assert m.bones[m.bones[i].parent].name == "jaw1" and i == next(k for k, b in enumerate(m.bones) if b.name == "jaw1") + 1
    assert "bones: re-parented 1 (nose: head -> jaw1), 1 record(s) re-sorted (parents first)" in msg, msg
    assert "IK chains: 1 completed (Head: jaw1 inserted)" in msg, msg
    act = action_of(arm, "O_growl")
    kf = key_frames(m, "O_growl", "nose")
    err_keys = playback_err(obj, arm, m, "O_growl", act, "nose", kf)
    err_all = playback_err(obj, arm, m, "O_growl", act, "nose")
    assert err_keys < 1e-4, (err_keys, kf)
    assert err_all < 3e-3, err_all
    # the same clip re-encoded from an edited Action after the re-parent plays the same way
    key_rotation(arm, act, "jaw1", [(5, (0.99, 0.0, 0.14, 0.0)), (12, (1.0, 0.0, 0.0, 0.0))])
    data2, msg2 = exported(obj)
    m2 = read(data2)
    assert "clips: 19 kept, 1 written" in msg2, msg2
    err2 = playback_err(obj, arm, m2, "O_growl", act, "nose")
    assert err2 < 2e-3, err2
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    while arm.data.bones["nose"].parent.name != "head":
        bpy.ops.ed.undo()
        obj = bpy.data.objects[name]; arm = store.rig(obj)
        bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert exported(obj)[0] == raw
    say(f"D RE-PARENT: nose under jaw1 exported (re-sorted after jaw1, Head chain completed); Blender's O_growl playback of "
        f"the nose = the exported clip at its {len(kf)} keys ({err_keys:.1e}; {err_all:.1e} over all frames); an Action "
        f"edited after the re-parent re-encodes and plays the same ({err2:.1e}); Undo -> byte-identical")


# ------------------------------------------------------------------------------------------------------------- E
def part_e():
    """Joint limits (C8.4): every source bone carries its record's limits; an edit exports into that record only; the
    Bones tab shows the stock values and the editable copy; Reset to Stock and Undo give the bytes back; a wrong count is
    refused; an added bone's limits are written."""
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH, clips_as_actions=False)
    name = obj.name
    arm = store.rig(obj)
    for i, b in enumerate(src.bones):
        vals = arm.data.bones[b.name].get(rig.KEY_BONE_LIMITS)
        assert vals is not None and [float(np.float32(x)) for x in vals] == [float(np.float32(x)) for x in b.tail], b.name
    assert exported(obj)[0] == raw, "the stamps alone: byte-identical"
    head = arm.data.bones["head"]
    stock = list(head[rig.KEY_BONE_LIMITS])
    bpy.ops.ed.undo_push(message="before limits")
    head[rig.KEY_BONE_LIMITS] = [-0.5, stock[1], stock[2], 0.5, stock[4], stock[5]]
    bpy.ops.ed.undo_push(message="limits")
    data, msg = exported(obj)
    assert "joint limits: 1 bone (head)" in msg, msg
    m = read(data)
    h = next(b for b in m.bones if b.name == "head")
    assert [round(x, 4) for x in h.tail[:4]] == [-0.5, round(stock[1], 4), round(stock[2], 4), 0.5], h.tail
    assert len(data) == len(raw) and sum(1 for k in range(len(raw)) if raw[k] != data[k]) <= 8
    bpy.context.scene.bz_boner.mode = "BONES"
    bpy.context.scene.bz_boner.bone = "head"
    labels, alerts = [], []

    class Rec:
        def __init__(self, name=""):
            object.__setattr__(self, "_name", name)

        def __getattr__(self, n):
            return Rec(n)

        def __call__(self, *a, **kw):
            if self._name == "label":
                labels.append(kw.get("text", ""))
            if self._name == "prop":
                labels.append(f"prop {a[1] if len(a) > 1 else kw}")
            return Rec()

        def __setattr__(self, k, v):
            if k == "alert" and v:
                alerts.append(True)
            object.__setattr__(self, k, v)
    ui.BZ_PT_boner.bones_tab(Rec(), bpy.context, obj)
    assert any(t.startswith("Joint limits of head") for t in labels) and any(t.startswith("stock: ") for t in labels), labels
    assert any(f'["{rig.KEY_BONE_LIMITS}"]' in t for t in labels), labels
    ok(bpy.ops.bz.reset_joint_limits, bone="head")
    assert exported(obj)[0] == raw, "Reset to Stock: not byte-identical"
    head[rig.KEY_BONE_LIMITS] = [-0.5, stock[1], stock[2], 0.5, stock[4], stock[5], 1.0]
    export_refused(obj, ("joint limits of bone 'head': 7 values for a record holding 6",))
    head[rig.KEY_BONE_LIMITS] = list(stock)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    arm.data.bones["horn_l"][rig.KEY_BONE_LIMITS] = [-0.1, -0.2, -0.3, 0.1, 0.2, 0.3]
    data2, msg2 = exported(obj)
    m2 = read(data2)
    hb = m2.find_bone("horn_l")
    assert [round(x, 3) for x in hb.tail] == [-0.1, -0.2, -0.3, 0.1, 0.2, 0.3] and "joint limits: 1 bone (horn_l)" in msg2, msg2
    while store.rig(bpy.data.objects[name]).data.bones.get("horn_l") is not None and bpy.ops.ed.undo.poll():
        bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert exported(obj)[0] == raw, "Undo: not byte-identical"
    say("E JOINT LIMITS: 54/54 bones stamped with their record's limits (export byte-identical); head's min/max x edited "
        "-> only its 8 bytes change, the summary says so; the Bones tab shows stock + the editable copy; Reset to Stock "
        "byte-identical; 7 values refused; an added bone's limits written; Undo byte-identical")


# ------------------------------------------------------------------------------------------------------------- F
def part_f():
    """IK chains (C8.5): the Bones tab lists the source's 15 chains; Add / Edit / Remove IK Chain export exactly that
    table; refusals (not a path, a taken name); a stored table follows a deleted bone (taken off), a renamed bone (the
    rename applies after the rows) and a re-parent (the chain completed); a hand-made non-path row is refused at Export
    and flagged by Validate; Reset IK Chains and Undo give the source bytes back."""
    from blender_native import ikops
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH, clips_as_actions=False)
    name = obj.name
    arm = store.rig(obj)
    rows, stored = ikops.chain_rows(obj)
    assert len(rows) == 15 and not stored and rows[2].name == "Neck"
    bpy.ops.ed.undo_push(message="before ik")
    ok(bpy.ops.bz.ik_add_chain, name="Horn", start="head", end="nose", flags="1", enabled=1)
    rows, stored = ikops.chain_rows(obj)
    assert stored and len(rows) == 16 and rows[-1] == topology.IKRow(-1, "Horn", 1, 1, ("head", "nose")), rows[-1]
    data, msg = exported(obj)
    m = read(data)
    chains = ik_chains(m)
    assert chains[-1].name == "Horn" and chains[-1].bones == ["head", "nose"] and chains[-1].flags == 1
    assert "IK chains: added 1 (Horn)" in msg, msg
    assert not [v for v in validate_verdict(obj) if "IK chain" in v]
    res, err = call(bpy.ops.bz.ik_add_chain, name="Bad", start="head", end="tail1", flags="1", enabled=1)
    assert res == {"CANCELLED"} and "does not hang from 'head'" in err, err
    res, err = call(bpy.ops.bz.ik_add_chain, name="horn", start="head", end="nose", flags="1", enabled=1)
    assert res == {"CANCELLED"} and "already" in err.lower() or "another" in err.lower(), err
    ok(bpy.ops.bz.ik_edit_chain, index=2, name="NeckChain", flags=2, enabled=0)
    ok(bpy.ops.bz.ik_remove_chain, index=4)                              # LEar
    data, msg = exported(obj)
    m = read(data)
    chains = ik_chains(m)
    assert [c.name for c in chains] == [r.name for r in ikops.chain_rows(obj)[0]] and "LEar" not in [c.name for c in chains]
    assert chains[2].name == "NeckChain" and chains[2].flags == 2 and chains[2].enabled == 0
    assert "IK chains: renamed 1 (Neck -> NeckChain); words 1 (NeckChain 2048/1 -> 2/0); added 1 (Horn); deleted 1 (LEar)" in msg, msg
    # the stored table follows the rig: ear_r deleted (REar loses it), nose under jaw1 (Head completed), tail4 renamed
    edit_bones(arm, lambda eb: eb.remove(eb["ear_r"]))
    edit_bones(arm, lambda eb: setattr(eb["nose"], "parent", eb["jaw1"]))
    arm.data.bones["tail4"].name = "tail_four"
    data, msg = exported(obj)
    m = read(data)
    chains = {c.name: c.bones for c in ik_chains(m)}
    assert chains["REar"] == ["endear_r"] and chains["Head"] == ["head", "jaw1", "nose"] and chains["Horn"] == ["head", "jaw1", "nose"]
    assert "tail_four" in chains["Tail"] and "tail4" not in chains["Tail"], chains["Tail"]
    assert "IK chains: 1 rebuilt (REar: ear_r taken out (1 bone left)); 2 completed (Head: jaw1 inserted, Horn: jaw1 inserted)" in msg, msg
    # a hand-made row that is no path: refused at Export, flagged by Validate
    bad = list(ikops.chain_rows(obj)[0]) + [topology.IKRow(-1, "Split", 8, 1, ("head", "tail1"))]
    bpy.ops.ed.undo_push(message="bad row")
    store.begin_edit(obj)
    store.set_ik_rows(obj, bad)
    export_refused(obj, ("IK chain 'Split'", "not one path of the hierarchy"))
    val = validate_verdict(obj)
    assert any("IK chain 'Split'" in v for v in val) and core.blocking(val), val
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    assert "Split" not in [r.name for r in ikops.chain_rows(obj)[0]]
    ok(bpy.ops.bz.ik_reset_chains)
    assert store.ik_rows(obj) is None
    while (store.rig(bpy.data.objects[name]).data.bones.get("ear_r") is None or store.ik_rows(bpy.data.objects[name]) is not None
           or store.rig(bpy.data.objects[name]).data.bones.get("tail4") is None) and bpy.ops.ed.undo.poll():
        bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert store.ik_rows(obj) is None and arm.data.bones.get("ear_r") is not None
    assert exported(obj)[0] == raw, "Undo back to the import: not byte-identical"
    say("F IK CHAINS: 15 source rows listed; Add IK Chain Horn (head > nose) exported and validated; not-a-path and a taken "
        "name refused; Edit (NeckChain 2/0) and Remove (LEar) give exactly that IKCH; the stored table follows a deleted "
        "ear_r, nose under jaw1 (Head + Horn completed) and tail4 renamed; a hand-made non-path row refused at Export and "
        "blocking in Validate; Reset IK Chains + Undo byte-identical")


def main():
    blender_native.register()
    parts = os.environ.get("BZ_TOPO_PARTS", "ABCDEF")
    fails = []
    for label, fn in (("A", part_a), ("B", part_b), ("C", part_c), ("D", part_d), ("E", part_e), ("F", part_f)):
        if label not in parts:
            continue
        try:
            fn()
        except Exception as e:
            fails.append(label)
            say(f"{label} FAIL: {type(e).__name__}: {e}")
            traceback.print_exc()
    assert not fails, fails
    say("ALL OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            blender_native.unregister()
        except Exception:
            pass
