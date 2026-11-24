"""Boner > Bones > Rename Bone and Blender's own bone rename, in headless Blender (increment B6): a renamed bone exports
under its new name everywhere, unedited clips stay kept.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_rename_test.py

A. registration and refusals: bz.rename_bone; a name another bone has ignoring case, 64 characters, non-ASCII, an empty
   name, a rename chain (a bone's old name for another bone) are refused with the reason; a rig imported before the
   identity stamps is stamped by its snapshot and renames;
B. cheetah with its 20 clips as Actions, 'Walk' kept only by its fake user (no NLA strip): Rename Bone ear_L ->
   ear_left renames the rig bone, its vertex group and the F-curves of every Action (the fake-user one too); Export
   (/tmp/bz_boner_b6_rename.sgm): the skeleton, the Head limb list, the LEar IK chain and the clip tracks carry the new
   name and no old one, 20 clips kept with key bytes untouched, summary names the rename; Validate (Quick combine
   tier): nothing blocking, no COMBINE ERROR, no warning about the ear; Undo / Redo of the rename in Object Mode and in
   the creature's Edit Mode give exactly the stock / renamed exports; re-imported and renamed back: byte-identical to
   the stock cheetah; editing a key of the fake-user Action re-encodes that one clip under the new name;
C. the same through Blender's own rename (bone.name = ...): the fake-user Action keeps its old F-curve paths, the export
   is byte-identical to B's (kept) and, with the same key edit, to B's re-encoded export; the paths are put back after
   sampling; Duplicate of the renamed bone (the copy unmoved: ambiguous; moved: added), Symmetrize (a copy with another
   name), a rename chain and a case-only clash (Validate: EXPORT BLOCKED) are refused, and deleting the copies exports
   again;
D. eagle: Rear_endleg_L -> rear_heel_l (Rename Bone: its LRLeg limb, the LRLeg IK chain) and feather2_L -> quill2_L
   (Blender's rename: the LSWing limb) export (/tmp/bz_boner_b6_eagle.sgm) with 13 clips kept; spine1 -> back1 warns
   (operator report, panel read-out, Validate's WARN line) and is renamed back; re-imported and renamed back:
   byte-identical to the stock eagle.
E. there and back: both exports re-imported and renamed back are byte-identical to the stock files;
F. Symmetrize onto an existing counterpart (cheetah antler_l, rest edited): the mirror bone keeps its identity by its
   import name and exports as a rest edit, Rename Bone restamps it; onto a counterpart renamed first: refused naming
   Symmetrize, renamed back it exports; a Rename Bone whose Action path fix would clash is refused before the rig bone
   changes; tail1 -> tail_a (/tmp/bz_boner_b6_tail1.sgm): the measured WARN and the Quick tier's torn-seam COMBINE
   ERRORs.
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, rig, actions, structure, boneops  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.sgm.creature.model import ik_chains  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
EAGLE = REPO + "/Assets/engine_stock/eagle.sgm"
OUT = "/tmp/bz_boner_rename_out.sgm"
RENAMED = "/tmp/bz_boner_b6_rename.sgm"
EAGLE_OUT = "/tmp/bz_boner_b6_eagle.sgm"
_log = open("/tmp/bz_boner_rename_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level
    bpy.context.preferences.edit.undo_steps = 64


def imp(path, **kw):
    kw.setdefault("clips_as_actions", False)
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def rename(obj, old, new):
    bpy.context.view_layer.objects.active = obj
    res, msg = call(bpy.ops.bz.rename_bone, bone=old, new_name=new)
    assert res == {"FINISHED"}, (old, new, msg)


def rename_refused(obj, old, new, words):
    bpy.context.view_layer.objects.active = obj
    res, msg = call(bpy.ops.bz.rename_bone, bone=old, new_name=new)
    assert res == {"CANCELLED"} and all(w in msg for w in words), (old, new, msg)
    return msg


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


def read(data, path="/tmp/bz_boner_rename_read.sgm"):
    with open(path, "wb") as f:
        f.write(data)
    return sgm_parser.read(path)


def anim_bytes(path):
    from ICParse.iff.document import IffDocument
    from ICParse.codecs import anim as ICanim
    return {ICanim.read_anim(ch).name: ch.serialize() for ch in IffDocument.load(path).find_all("ANIM")}


def clips_named_back(path, renames):
    """Every clip of ``path`` serialised with its track names renamed back (``renames``: [(old, new)])."""
    from ICParse.codecs import anim as ICanim
    back = structure.BoneRenamer(renames, lower_first=True).inverse()
    out = {}
    for a in sgm_parser.read(path).animations:
        for t in a.tracks:
            t.bone_name = back(t.bone_name)
        out[a.name] = ICanim.write_anim(a).serialize()
    return out


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


def bone_paths(act):
    return {actions.bone_of_path(fc.data_path)[0] for fc in actions._fcurves(act)
            if actions.bone_of_path(fc.data_path) is not None}


def refs(m):
    return ([b.name for b in m.bones] + [b for l in m.limbs for b in l.bone_names]
            + [c.bone_name for c in m.limb_connections] + [b for c in (ik_chains(m) or []) for b in c.bones]
            + [t.bone_name for a in m.animations for t in a.tracks])


def key_data(m):
    return {a.name: [(t.rot_keys, t.trans_keys, t.flag) for t in a.tracks] for a in m.animations}


def fake_user_only(arm, clip):
    """The Action of ``clip``, taken off its NLA track (kept only by its fake user)."""
    act = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == clip)
    ad = arm.animation_data
    for t in list(ad.nla_tracks):
        if any(s.action == act for s in t.strips):
            ad.nla_tracks.remove(t)
    if ad.action == act:
        ad.action = None
    assert act.use_fake_user and act in actions.actions_of(arm)
    return act


def nudge_key(act, bone, frame_index=0, delta=0.2):
    """Change one rotation key of ``bone`` in ``act`` (by its current or its old path)."""
    fc = next(fc for fc in actions._fcurves(act) if actions.bone_of_path(fc.data_path) is not None
              and actions.bone_of_path(fc.data_path)[0] == bone and fc.data_path.endswith("rotation_quaternion")
              and fc.array_index == 1)
    kp = fc.keyframe_points[frame_index]
    kp.co = (kp.co[0], kp.co[1] + delta)
    fc.update()


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    assert hasattr(bpy.types, "BZ_OT_rename_bone")
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    for new, words in (("EAR_R", ("another bone is already called 'ear_r'",)), ("x" * 64, ("64 characters",)),
                       ("ohr_é", ("outside plain ASCII",)), ("", ("needs a name",)), (" ear", ("starts or ends",))):
        rename_refused(obj, "ear_L", new, words)
    rename_refused(obj, "no_such", "x", ("'no_such' is not a bone",))
    rename(obj, "ear_L", "ear_left")
    rename_refused(obj, "ear_r", "ear_L", ("another bone ('ear_left') was called at import 'ear_L'", "two bones"))
    rename_refused(obj, "ear_r", "EAR_l", ("was called at import 'ear_L'",))
    rename(obj, "ear_left", "EAR_L")                 # the bone's own import name, in another case: not a chain
    rename(obj, "EAR_L", "ear_L")
    assert exported(obj)[0] == open(CHEETAH, "rb").read()
    # a rig imported before the identity stamps: Rename Bone stamps it from the snapshot, then renames
    for b in arm.data.bones:
        del b[rig.KEY_BONE_SRC_INDEX]
    arm.data.bones["ear_L"].name = "ear_x"
    export_refused(obj, ("was renamed to 'ear_x'", "imported before bones carried identity stamps"))
    rename_refused(obj, "ear_x", "ear_left", ("bone 'ear_L' is not on it",))
    arm.data.bones["ear_x"].name = "ear_L"
    rename(obj, "ear_L", "ear_left")
    assert all(b.get(rig.KEY_BONE_SRC_INDEX) is not None for b in arm.data.bones)
    data, msg = exported(obj)
    assert "bones: renamed 1 (ear_L -> ear_left)" in msg and "ear_left" in [b.name for b in read(data).bones], msg
    say("A REFUSALS: case clash / 64 chars / non-ASCII / empty / space / unknown bone / rename chain refused; a case "
        "change of the bone's own name is not a chain; a stamp-less rig refuses Blender's rename and Rename Bone stamps "
        "it and renames")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh("OFF")
    stock = sgm_parser.read(CHEETAH)
    src_bytes = open(CHEETAH, "rb").read()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    walk = fake_user_only(arm, "Walk")
    assert "ear_L" in bone_paths(walk)
    name = obj.name
    rename(obj, "ear_L", "ear_left")
    assert arm.data.bones.get("ear_left") and arm.data.bones.get("ear_L") is None
    assert obj.vertex_groups.get("ear_left") and obj.vertex_groups.get("ear_L") is None
    for act in actions.actions_of(arm):
        assert "ear_L" not in bone_paths(act), act.name
    assert "ear_left" in bone_paths(walk)
    plan = actions.plan_export(arm)
    assert len(plan.kept) == 20 and not plan.encode and not plan.drop, (len(plan.kept), plan.encode, plan.drop)
    renamed, msg = exported(obj, RENAMED)
    ntr = sum(1 for a in stock.animations for t in a.tracks if t.bone_name.lower() == "ear_l")
    assert ntr >= 10, ntr
    assert (f"bones: renamed 1 (ear_L -> ear_left), followed by 1 limb list(s), 1 IK chain(s), {ntr} clip track(s)"
            in msg), msg
    assert "clips:" not in msg, msg
    m = read(renamed)
    names = [x.lower() for x in refs(m)]
    assert "ear_l" not in names and names.count("ear_left") == 1 + 1 + 1 + ntr, names.count("ear_left")
    assert "ear_left" in next(l for l in m.limbs if l.name == "Cheetah_Head").bone_names
    assert next(c for c in ik_chains(m) if c.name == "LEar").bones == ["ear_left", "endear_l"]
    assert key_data(m) == key_data(stock), "clip keys changed"
    assert clips_named_back(RENAMED, [("ear_L", "ear_left")]) == anim_bytes(CHEETAH), \
        "a kept clip differs beyond its track names"
    assert len(renamed) == len(src_bytes) + 3 * (3 + ntr), (len(renamed), len(src_bytes))
    val = validate_verdict(obj, "QUICK")
    assert not core.blocking(val) and not [v for v in val if v.startswith("COMBINE ERROR")], \
        core.blocking(val) or [v for v in val if v.startswith("COMBINE ERROR")]
    assert not [v for v in val if "ear_L" in v or "ear_left" in v], [v for v in val if "ear" in v.lower()]
    summary = str(obj.get("bz_combine_summary", ""))
    assert summary, "the Quick tier did not run"
    assert ui._bone_renames_cached(arm) == ([("ear_L", "ear_left")], [])

    # Undo / Redo of the rename (Object Mode), then the same in the creature's Edit Mode
    rename(obj, "ear_left", "ear_L")
    bpy.ops.ed.undo_push(message="before rename")
    rename(obj, "ear_L", "ear_left")
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("ear_L") is not None and obj.vertex_groups.get("ear_L") is not None
    assert exported(obj)[0] == src_bytes, "Undo of the rename"
    bpy.ops.ed.redo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("ear_left") is not None
    assert exported(obj)[0] == renamed, "Redo of the rename"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit mode")
    rename(obj, "nose", "snout")
    assert obj.mode == "EDIT" and store.rig(obj).data.bones.get("snout") is not None
    data_nose, msg_nose = exported(obj)
    assert "renamed 2 (nose -> snout, ear_L -> ear_left)" in msg_nose, msg_nose
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("nose") is not None and arm.data.bones.get("snout") is None, [b.name for b in arm.data.bones][:40]
    assert exported(obj)[0] == renamed, "Undo of the Edit Mode rename"
    bpy.ops.ed.redo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("snout") is not None
    assert exported(obj)[0] == data_nose, "Redo of the Edit Mode rename"
    rename(obj, "snout", "nose")
    bpy.ops.object.mode_set(mode="OBJECT")
    assert exported(obj)[0] == renamed

    # the fake-user Action edited on the renamed bone: that one clip is re-encoded (tracks under the new name)
    walk = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == "Walk")
    nudge_key(walk, "ear_left")
    edited, msg_e = exported(obj)
    assert "clips: 19 kept, 1 written" in msg_e, msg_e
    me = read(edited)
    wtr = [t.bone_name for t in next(a for a in me.animations if a.name == "Walk").tracks]
    assert "ear_left" in wtr and not [t for t in wtr if t.lower() == "ear_l"], wtr
    ga, gb = anim_bytes(OUT), anim_bytes(RENAMED)
    assert [k for k in gb if ga[k] != gb[k]] == ["Walk"]
    say(f"B CHEETAH RENAME BONE: ear_L -> ear_left: rig bone, vertex group, every Action's F-curves (the fake-user Walk "
        f"too); {RENAMED}: skeleton + Head limb + LEar IK + {ntr} tracks renamed, 20 clips kept (keys untouched, bytes "
        f"differ only by the name); Validate Quick clean ({summary}); Undo/Redo in Object and Edit Mode exact; the "
        f"fake-user Walk edited -> 19 kept, 1 written under the new name")
    return renamed, edited


def there_and_back(path, stock_path, pairs):
    fresh()
    obj = imp(path)
    for new, old in pairs:
        rename(obj, new, old)
    data, msg = exported(obj)
    assert data == open(stock_path, "rb").read(), (path, msg)


# ------------------------------------------------------------------------------------------------------------- C
def part_c(renamed, edited):
    fresh()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    walk = fake_user_only(arm, "Walk")
    arm.data.bones["ear_L"].name = "ear_left"                      # Blender's own rename (Outliner / Properties / F2)
    assert obj.vertex_groups.get("ear_left") is not None
    assert "ear_L" in bone_paths(walk) and "ear_left" not in bone_paths(walk), "Blender fixed the fake-user Action?"
    others = [a for a in actions.actions_of(arm) if a != walk]
    assert all("ear_left" in bone_paths(a) and "ear_L" not in bone_paths(a) for a in others)
    data, msg = exported(obj, OUT)
    assert data == renamed, f"Blender's rename exports differently from Rename Bone: {msg}"
    nudge_key(walk, "ear_L")                                       # the same key, through the old path
    data, msg = exported(obj, OUT)
    assert data == edited, f"the edited fake-user Action exports differently: {msg}"
    assert "ear_L" in bone_paths(walk) and "ear_left" not in bone_paths(walk), "the sampled paths were not put back"

    # a duplicated bone: unmoved copy of a renamed bone -> ambiguous; moved -> added; Symmetrize -> added; chains
    def select_only(eb, name):
        for e in eb:
            e.select = e.select_head = e.select_tail = (e.name == name)
        eb.active = eb[name]

    def dup(eb):
        select_only(eb, "ear_left")
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup)
    copy = next(b.name for b in arm.data.bones if b.name.startswith("ear_left."))
    export_refused(obj, (f"bones 'ear_left', '{copy}' all carry the import stamp of 'ear_L'", "none of them"))
    edit_bones(arm, lambda eb: setattr(eb[copy], "head", eb[copy].head + eb[copy].vector * 0.5))
    export_refused(obj, (f"bone '{copy}' was added",))
    edit_bones(arm, lambda eb: eb.remove(eb[copy]))

    def sym(eb):
        select_only(eb, "ear_left")
        assert bpy.ops.armature.symmetrize() == {"FINISHED"}
    edit_bones(arm, sym)
    made = [b.name for b in arm.data.bones if b.name not in {x.name for x in sgm_parser.read(CHEETAH).bones}
            and b.name != "ear_left"]
    assert made and all(arm.data.bones[x].get(rig.KEY_BONE_SRC_INDEX) is not None for x in made), made
    export_refused(obj, (f"bone '{made[0]}' was added",))
    edit_bones(arm, lambda eb: [eb.remove(eb[x]) for x in made])
    assert exported(obj, OUT)[0] == edited
    arm.data.bones["ear_r"].name = "ear_L"                          # chain: ear_L is ear_left's import name
    export_refused(obj, ("bone 'ear_L' was renamed to 'ear_left' and bone 'ear_L' (it was 'ear_r')", "two bones"))
    val = validate_verdict(obj)
    assert val[0].startswith("EXPORT BLOCKED: bone 'ear_L' was renamed to 'ear_left'"), val[:1]
    arm.data.bones["ear_L"].name = "ear_r"
    assert exported(obj, OUT)[0] == edited
    arm.data.bones["endear_l"].name = "ENDEAR_R"                    # Blender's rename to a name another bone has in
    export_refused(obj, ("bones 'ENDEAR_R' and 'endear_r' differ only in case",))   # another case: the name rule
    val = validate_verdict(obj)
    assert val[0].startswith("EXPORT BLOCKED: bones 'ENDEAR_R' and 'endear_r' differ only in case"), val[:1]
    arm.data.bones["ENDEAR_R"].name = "endear_l"
    assert exported(obj, OUT)[0] == edited
    say("C BLENDER'S RENAME: the fake-user Walk keeps its old paths; export identical to Rename Bone's (kept and "
        "edited), paths put back; Duplicate (unmoved: ambiguous, moved: added), Symmetrize (added), a rename chain and a "
        "case-only clash refused; removed -> identical")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    fresh()
    stock = sgm_parser.read(EAGLE)
    obj = imp(EAGLE, clips_as_actions=True)
    arm = store.rig(obj)
    rename(obj, "Rear_endleg_L", "rear_heel_l")
    arm.data.bones["feather2_L"].name = "quill2_L"
    # a hard-coded name: allowed, warned by the operator, the panel and Validate; renamed back
    bpy.context.view_layer.objects.active = obj
    res, msg = call(bpy.ops.bz.rename_bone, bone="spine1", new_name="back1")
    assert res == {"FINISHED"}
    assert "back1" in [b.name for b in arm.data.bones]
    ren, warns = ui._bone_renames_cached(arm)
    assert ("spine1", "back1") in ren and any("'spine1' up by name" in w and "procedural animation" in w for w in warns)
    val = validate_verdict(obj)
    wl = [v for v in val if v.startswith("WARN export: bone 'spine1' renamed to 'back1'")]
    assert wl and "creature scaling" in wl[0] and not core.blocking(val), (wl, core.blocking(val))
    assert not [v for v in val if "rear_heel_l" in v or "quill2_L" in v], [v for v in val if "heel" in v or "quill" in v]
    rename(obj, "back1", "spine1")
    data, msg = exported(obj, EAGLE_OUT)
    assert "bones: renamed 2 (Rear_endleg_L -> rear_heel_l, feather2_L -> quill2_L)" in msg, msg
    assert "clips:" not in msg and len(actions.plan_export(arm).kept) == 13, msg
    m = read(data)
    assert next(c for c in ik_chains(m) if c.name == "LRLeg").bones == ["Rear_ankle_L", "rear_heel_l", "Rear_leg1_l",
                                                                         "Rear_leg2_L"]
    assert "rear_heel_l" in next(l for l in m.limbs if l.name == "Eagle_LRLeg").bone_names
    assert "quill2_L" in next(l for l in m.limbs if l.name == "Eagle_LSWing").bone_names
    low = [x.lower() for x in refs(m)]
    assert "rear_endleg_l" not in low and "feather2_l" not in low
    assert key_data(m) == key_data(stock)
    val = validate_verdict(obj, "OFF")
    assert not core.blocking(val), core.blocking(val)
    say(f"D EAGLE: Rear_endleg_L -> rear_heel_l (Rename Bone; LRLeg limb + IK) and feather2_L -> quill2_L (Blender; LSWing "
        f"limb) -> {EAGLE_OUT}, 13 clips kept; spine1 -> back1 warned by operator, panel and Validate, renamed back")


# ------------------------------------------------------------------------------------------------------------- F
TAIL1_OUT = "/tmp/bz_boner_b6_tail1.sgm"


def _select_only(eb, name):
    for e in eb:
        e.select = e.select_head = e.select_tail = (e.name == name)
    eb.active = eb[name]


def part_f():
    K = rig.KEY_BONE_SRC_INDEX
    # F1: antler_l's rest edited, then Symmetrize onto the EXISTING antler_r: Blender copies antler_l's stamp onto it;
    # antler_r still holds its import name, so it is antler_r (a rest edit, as before the stamps) and Rename Bone
    # restamps it before renaming
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    sid_l, sid_r = int(arm.data.bones["antler_l"][K]), int(arm.data.bones["antler_r"][K])

    def move_sym(eb):
        eb["antler_l"].tail = eb["antler_l"].tail + eb["antler_l"].z_axis * eb["antler_l"].length * 0.5
        _select_only(eb, "antler_l")
        assert bpy.ops.armature.symmetrize() == {"FINISHED"}
    edit_bones(arm, move_sym)
    assert int(arm.data.bones["antler_r"][K]) == sid_l, "Blender's Symmetrize no longer copies the stamp?"
    data, msg = exported(obj)
    assert "rest pose: 2 bones" in msg and "antler_r" in msg and "renamed" not in msg, msg
    rename(obj, "antler_r", "horn_r")
    assert int(arm.data.bones["horn_r"][K]) == sid_r
    data2, msg2 = exported(obj)
    assert "bones: renamed 1 (antler_r -> horn_r)" in msg2 and "rest pose: 2 bones" in msg2, msg2
    assert "horn_r" in [b.name for b in read(data2).bones]
    # F2: both antlers renamed (Blender's rename), then Symmetrize horn_l onto horn_r: its stamp is overwritten and no
    # name tells it -> refused naming Symmetrize (not a plain delete + add); renaming it back to antler_r exports
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    arm.data.bones["antler_l"].name = "horn_l"
    arm.data.bones["antler_r"].name = "horn_r"

    def sym(eb):
        _select_only(eb, "horn_l")
        assert bpy.ops.armature.symmetrize() == {"FINISHED"}
    edit_bones(arm, sym)
    assert int(arm.data.bones["horn_r"][K]) == sid_l
    err = export_refused(obj, ("bone(s) 'horn_r' carry the import stamp of 'antler_l'", "'antler_r'", "Symmetrize"))
    assert "was deleted" not in err, err
    val = validate_verdict(obj)
    assert val[0].startswith("EXPORT BLOCKED: bone(s) 'horn_r' carry the import stamp"), val[:1]
    arm.data.bones["horn_r"].name = "antler_r"
    data, msg = exported(obj)
    assert "bones: renamed 1 (antler_l -> horn_l)" in msg and "antler_r" in msg, msg
    # F3: a Rename Bone whose Action fix would clash is refused BEFORE the rig changes (no half-done rename): Blender
    # renamed neck2 -> neck_mid while Walk sat on no NLA strip (its paths still say neck2), Walk made active and keyed
    # on neck_mid
    fresh()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    walk = fake_user_only(arm, "Walk")
    arm.data.bones["neck2"].name = "neck_mid"
    arm.animation_data.action = walk
    arm.pose.bones["neck_mid"].keyframe_insert("rotation_quaternion", frame=1)
    assert {"neck2", "neck_mid"} <= bone_paths(walk), bone_paths(walk)
    rename_refused(obj, "neck_mid", "neck_middle", ("Rename Bone refused", "keys", "under both"))
    assert arm.data.bones.get("neck_mid") is not None and arm.data.bones.get("neck_middle") is None
    assert obj.vertex_groups.get("neck_mid") is not None and obj.vertex_groups.get("neck_middle") is None
    # F4: tail1 renamed (a looked-up name): allowed, the WARN names the measured ComboTest.exe result, and the Quick
    # combine tier reports the torn tail seam of the designs that replace a tail as COMBINE ERROR (the engine pair is
    # COMBINE_MUST_FLAG in run_all)
    fresh()
    obj = imp(CHEETAH)
    rename(obj, "tail1", "tail_a")
    data, msg = exported(obj, TAIL1_OUT)
    assert "bones: renamed 1 (tail1 -> tail_a)" in msg, msg
    val = validate_verdict(obj, "QUICK")
    assert not core.blocking(val), core.blocking(val)
    warn = [v for v in val if v.startswith("WARN export: bone 'tail1' renamed to 'tail_a'")]
    assert warn and "measured in ComboTest.exe" in warn[0], warn
    torn = [v for v in val if v.startswith("COMBINE ERROR") and "torso seam vertices" in v]
    assert torn and any("'tail_a'" in v for v in torn) and any("'tail1'" in v for v in torn), \
        [v for v in val if v.startswith("COMBINE")][:12]
    say(f"F SYMMETRIZE + CLASHES: Symmetrize onto the existing antler_r -> a rest edit (stamp copied, found by name), "
        f"Rename Bone restamps it; onto a renamed horn_r -> refused naming Symmetrize, renamed back -> exports; a clashing "
        f"Action fix refused before the rig changes; tail1 -> tail_a ({TAIL1_OUT}): measured WARN, Quick tier "
        f"{len(torn)} torn-seam COMBINE ERROR(s) in both roles")


def main():
    blender_native.register()
    part_a()
    renamed, edited = part_b()
    part_c(renamed, edited)
    part_d()
    there_and_back(RENAMED, CHEETAH, [("ear_left", "ear_L")])
    there_and_back(EAGLE_OUT, EAGLE, [("rear_heel_l", "Rear_endleg_L"), ("quill2_L", "feather2_L")])
    say("E THERE AND BACK: both exports re-imported and renamed back -> byte-identical to the stock cheetah and eagle")
    part_f()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_rename_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_rename_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
