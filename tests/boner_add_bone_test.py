"""Boner > Bones > Add Bone in headless Blender (increment B7): add a bone, skin it, export it.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_add_bone_test.py

A. registration: bz.add_bone / bz.adopt_bones; the Bones tab offers Add Bone under Active;
B. cheetah (clips as Actions): Add Bone under Active 'horn_l' under head -> a stamped deforming leaf, the new-bone table
   entry (one journal generation), the Head limb lists it; refusals (a stub parent, a taken / import name, a bad name);
   8 head points (3 patch corners) skinned to it with Assign Selected -> owner key n_src; export
   /tmp/bz_boner_b7_horn.sgm: bones +1 (horn_l at the first stub position, parent head), every point's world within
   1e-5 of Blender's, aux_count = its corners, the clip plan keeps all 20 clips and their bytes, the summary names the
   bone; Validate: nothing blocking, no STOCK line for horn_l, Quick combine tier without COMBINE ERROR; re-import of the
   export: horn_l on the rig owning the 8 points, an untouched export byte-identical;
C. Blender's own extrude (armature Edit Mode, endtail's tail) -> adopted at export: into the Tail limb, the STOCK line
   (it owns nothing); Adopt Added Bones stamps it; an Action keying horn_l (O_growl) is re-encoded with a horn_l track
   that plays like Blender, the other 19 clips kept -> /tmp/bz_boner_b7_extrude.sgm;
D. ambiguous / refused: a root bone, an unstamped chain (taken by Export as it is since C8.3; Adopt stamps it), a copy of a source bone, a bone
   under a stub bone, copies of horn_l (the one keeping its name is horn_l, the copy is adopted; both renamed: refused);
E. undo: Add Bone with the rig in its Edit Mode ends out of it -> Ctrl+Z takes the bone AND the tables back (export
   identical), Ctrl+Shift+Z brings both back; Blender's own add in the rig's Edit Mode + Ctrl+Z -> identical; Add Bone
   with the creature in Edit Mode + Ctrl+Z -> identical, Redo -> the bone; points skinned to horn_l and the bone deleted
   on the rig -> Export refuses naming it (orange: Select Unassigned selects them); Undo -> exports; the points moved
   off and the bone deleted -> its limb entry dropped (the summary says so);
F. limb identity: an added bone is in its current parent's limb -- a renamed added bone and a second bone called by its
   old name (Rename Bone, Outliner, extrude) both in the Head, Add Bone's report names the parent's limb; an added bone
   deleted again (plain, renamed, Add Bone from the creature's Edit Mode) exports byte-identical, the summary saying its
   entry was dropped; an adopted chain's middle bone deleted -> the child under nose in the Head; re-parented under tail4
   -> the Tail limb (moved), under spine2 -> the Torso with the stray-point warning and Validate's STOCK line; Limbs >
   Add / Remove Active Bone on an added bone say what it is;
G. clips and added bones: a re-encoded clip that does not key horn_l gives it no track whatever was played or posed
   before (O_growl keying it scrubbed in Blender, a hand pose): the exports are identical and the pose is the modder's
   again afterwards; a new Action keying only head does not bake a hand-posed tail4; horn_l renamed with Blender's own
   rename while O_growl (keying it) is on no NLA strip: the clip still has its horn track, playing like Blender; keys of
   a bone the rig has no bone called warn at Export.
H. UI and messages: no skeleton alert for an unstamped chain (C8.3), Adopt stamps it / Ctrl+Z unstamps, a copied chain alerted; then as before after
   Ctrl+Z; Add Bone whose journaled edit can't start leaves no bone (a removed generation attribute, a refused begin),
   Adopt Added Bones with an unreadable source refuses in words; a failing added-bone key check is said at the active
   bone, in the Bones tab and by Assign Selected; a bone under a SOURCE stub bone is refused in the Bones tab, the rig box
   and the skin tools (no owner key), not only at Export; a case clash is said once and Adopt refuses it before
   stamping; added-bone refusals carry no delete / re-parent tail (a deleted bone keeps it); points on a deleted added
   bone name its last rig name ("'horn_x' (made as 'horn_l')"); Add Bone's disabled-rig refusal talks about the bone;
   the Limbs panel shows added bones where Export puts them (re-parented: moved, deleted: gone).
I. stub bones, Auto Skin, Actions: a new limb with no patches naming endtail makes it a stub bone of the export -- Add
   Bone under it is refused, a leaf Blender extrudes under it is refused by Export's skeleton guard, and once endtail owns
   points the leaf exports; Auto Skin Reset All gives points to an added bone (horn_l with its head on a head control
   point takes that point, the export has them), not to an added end site, a non-deforming added bone or a bone not
   adopted yet; a new Action keying horn_l held only by its fake user is not exported, and plan_export and Export's
   warnings name it (not an unused Action keying no bone of the rig, nor one stamped for another rig); stashed on the
   rig's NLA it exports.
J. copies, stub set, Select Bones: Shift+D of an added chain is refused as a copy of the added bone (Adopt Added Bones
   gives the copies their own identity, then they export); Shift+D of a skinned added bone with the original deleted --
   the copy takes its identity, its points still in the deleted bone's vertex group -- is refused naming that group at
   Export and in a skin edit, and exports once renamed back; with a new stub limb naming endtail the panel read-outs
   (owner keys, the active bone, the Bones tab, the rig box), Add Bone's warning and Adopt Added Bones use the export's
   stub bones; Limbs > Select Bones selects the placed lists (a re-parented added bone with its new limb, a deleted one
   not at all).
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, structure, rig, actions, ops, boneops  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
OUT = "/tmp/bz_boner_add_out.sgm"
HORN = "/tmp/bz_boner_b7_horn.sgm"
EXTRUDE = "/tmp/bz_boner_b7_extrude.sgm"
_log = open("/tmp/bz_boner_add_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level
    bpy.context.preferences.edit.undo_steps = 64
    # exact point sets by pool index (corners included): interiors shown, so none follows a selected corner (B5)
    bpy.context.scene.bz_show_interiors = True


def imp(path, **kw):
    kw.setdefault("clips_as_actions", False)
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def ok(op, **kw):
    res, msg = call(op, **kw)
    assert res == {"FINISHED"}, (op, kw, msg)


def refused(op, words, **kw):
    res, msg = call(op, **kw)
    assert res == {"CANCELLED"} and all(w in msg for w in words), (kw, msg)
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


def read(data, path="/tmp/bz_boner_add_read.sgm"):
    with open(path, "wb") as f:
        f.write(data)
    return sgm_parser.read(path)


def anim_bytes(path):
    from ICParse.iff.document import IffDocument
    from ICParse.codecs import anim as ICanim
    return {ICanim.read_anim(ch).name: ch.serialize() for ch in IffDocument.load(path).find_all("ANIM")}


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


def select_points(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    for i in idx:
        bm.verts[int(i)].select = True
    bmesh.update_edit_mesh(obj.data)


def head_points(src, points=8, corners=3):
    """The highest head points of the source (``corners`` of them patch corners)."""
    head = next(i for i, b in enumerate(src.bones) if b.name == "head")
    own = np.array(R.vertex_bone_owners(src))
    world = np.asarray(R.world_vertices(src), dtype=np.float64)
    hp = np.nonzero(own == head)[0]
    hp = hp[np.argsort(-world[hp, 1])]
    cm = core.corner_mask(core.source_table(src), len(src.vertices))
    return np.concatenate([hp[cm[hp]][:corners], hp[~cm[hp]][:points - corners]])


def select_only(eb, name, tail=False):
    for e in eb:
        e.select = e.select_head = e.select_tail = False
    b = eb[name]
    if tail:
        b.select_tail = True
    else:
        b.select = b.select_head = b.select_tail = True
    eb.active = b


def built_of(obj):
    job = ops.gather_export(obj, bpy.context, animations=False)
    return core.build_export(job.state)


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    bpy.ops.bz.add_bone.get_rna_type()
    bpy.ops.bz.adopt_bones.get_rna_type()
    obj = imp(CHEETAH)
    assert bpy.ops.bz.add_bone.poll() and bpy.ops.bz.adopt_bones.poll()
    bpy.context.scene.bz_boner.mode = "BONES"
    log = []

    class Rec:
        def __init__(self, name=""):
            self.name = name

        def __getattr__(self, name):
            return Rec(name)

        def __call__(self, *a, **kw):
            log.append((self.name, a, kw))
            return Rec()

        def __setattr__(self, k, v):
            object.__setattr__(self, k, v)
    ui.BZ_PT_boner.bones_tab(Rec(), bpy.context, obj)
    assert "Add Bone under Active" in [kw.get("text", "") for _n, _a, kw in log], log[:10]
    say("A REGISTER: bz.add_bone + bz.adopt_bones; the Bones tab offers Add Bone under Active")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    src = sgm_parser.read(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    obj = imp(CHEETAH, clips_as_actions=True)
    name = obj.name
    arm = store.rig(obj)
    n_src = len(src.bones)
    assert exported(obj)[0] == raw
    s = bpy.context.scene.bz_boner
    s.bone = "head"
    refused(bpy.ops.bz.add_bone, ("'top_wing_r' is a stub bone",), parent="top_wing_r", name="x_tip")
    refused(bpy.ops.bz.add_bone, ("another bone is already called 'ear_r' (names must differ, ignoring case)",),
            parent="head", name="EAR_R")
    refused(bpy.ops.bz.add_bone, ("63",), parent="head", name="h" * 70)
    refused(bpy.ops.bz.add_bone, ("not a bone of the rig",), parent="no_such", name="x")
    gen = obj[store.KEY_GEN]
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l", offset=(0.0, 0.02, 0.0), length=0.08)
    text = str(obj.get("bz_last_add_bone", ""))
    assert text.startswith("Added bone 'horn_l' under 'head', in limb 'Cheetah_Head'"), text
    b = arm.data.bones["horn_l"]
    assert b.parent.name == "head" and b.use_deform and not len(b.children)
    uid = b.get(rig.KEY_BONE_UID)
    assert uid and b.get(rig.KEY_BONE_NEW_NAME) == "horn_l" and b.get(rig.KEY_BONE_SRC_INDEX) is None
    assert store.new_bone_uids(obj) == [uid] and store.new_bone_table_names(obj) == ["horn_l"]
    assert obj[store.KEY_GEN] != gen and store.KEY_BONE_NEW not in obj[store.KEY_JOURNAL][str(gen)]
    assert store.bone_index(obj, "horn_l") == n_src and store.bone_rig_names(obj)[n_src] == "horn_l"
    head_row = next(r for r in store.limb_rows(obj) if boner.class_of(r.limb_id) == 0x40)
    assert head_row.bones[-1] == "horn_l", head_row
    assert s.bone == "horn_l" and rig.skeleton_error(arm, src) is None
    # skin 8 head points (3 corners) to it
    pts = head_points(src)
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    bpy.ops.object.mode_set(mode="OBJECT")
    vb = store.vert_bone(obj)
    assert sorted(np.nonzero(vb == n_src)[0].tolist()) == sorted(pts.tolist())
    vg = obj.vertex_groups["horn_l"]
    assert sorted(v.index for v in obj.data.vertices if any(g.group == vg.index for g in v.groups)) == sorted(pts.tolist())
    assert ops.skin_stats_cached(obj).counts[n_src] == 8
    plan = actions.plan_export(arm)
    assert len(plan.kept) == 20 and not plan.encode and not plan.drop, (plan.kept, plan.encode)
    data, msg = exported(obj, HORN)
    assert "bones: added 1 (horn_l)" in msg and "limbs: bones 1 (Cheetah_Head)" in msg, msg
    m = read(data)
    hi = next(i for i, x in enumerate(m.bones) if x.name == "horn_l")
    assert len(m.bones) == n_src + 1 and hi == 48 and m.bones[m.bones[hi].parent].name == "head"
    own = np.array(R.vertex_bone_owners(m))
    assert int((own == hi).sum()) == 8
    corner = core.corner_mask(core.source_table(m), len(m.vertices))
    assert m.bones[hi].meta5 == 3 and core.aux_rule_problems(m) == []
    built = built_of(obj)
    assert sgm_parser.write(built.model) == data
    Wb = core.blender_to_sgm(np.array([v.co[:] for v in obj.data.vertices], dtype=np.float64))
    err = float(np.abs(np.asarray(R.world_vertices(m))[built.o2n] - Wb).max())
    assert err < 1e-5, err
    Wrest = rig.from_blender_matrix(arm.data.bones["horn_l"].matrix_local)
    assert np.allclose(R.bind_world_matrices(m)[hi], Wrest, atol=1e-5)
    assert anim_bytes(HORN) == anim_bytes(CHEETAH), "kept clips changed"
    assert next(l for l in m.limbs if int(l.limb_id) & 0xFF7FFFFF == 0x40).bone_names[-1] == "horn_l"
    val = validate_verdict(obj, "QUICK")
    assert not core.blocking(val) and not [v for v in val if v.startswith("COMBINE ERROR")], \
        core.blocking(val) or [v for v in val if v.startswith("COMBINE ERROR")]
    assert not [v for v in val if "horn_l" in v], [v for v in val if "horn_l" in v]
    assert str(obj.get("bz_combine_summary", "")), "the Quick tier did not run"
    summary = str(obj.get("bz_combine_summary", ""))
    # re-import the export: the bone is a bone of that creature now
    obj2 = imp(HORN)
    arm2 = store.rig(obj2)
    assert arm2.data.bones.get("horn_l") is not None and arm2.data.bones["horn_l"].get(rig.KEY_BONE_SRC_INDEX) == hi
    assert int((store.vert_bone(obj2) == hi).sum()) == 8
    assert exported(obj2)[0] == data, "re-imported horn export is not byte-identical"
    bpy.data.objects.remove(obj2, do_unlink=True)
    bpy.data.objects.remove(arm2, do_unlink=True)
    say(f"B HORN: Add Bone under Active horn_l (stamped deforming leaf, table entry, one generation, Head limb; 4 "
        f"refusals); 8 head points (3 corners) skinned -> {HORN}: bone 48 under head, 8 points, aux 3, world {err:.1e}, "
        f"rest = Blender's, 20 clips kept byte-identical; Validate clean, Quick tier {summary}; re-import owns the 8 "
        "points and exports byte-identical")
    return obj.name


# ------------------------------------------------------------------------------------------------------------- C
def part_c(name):
    obj = bpy.data.objects[name]
    bpy.context.view_layer.objects.active = obj
    arm = store.rig(obj)
    src = sgm_parser.read(CHEETAH)
    n_src = len(src.bones)
    horn = open(HORN, "rb").read()

    def extrude(eb):
        select_only(eb, "endtail", tail=True)
        assert bpy.ops.armature.extrude() == {"FINISHED"}
    edit_bones(arm, extrude)
    tip = next(b.name for b in arm.data.bones if b.name.startswith("endtail.") or (b.parent is not None and
                                                                                   b.parent.name == "endtail"))
    tb = arm.data.bones[tip]
    assert tb.parent.name == "endtail" and tb.get(rig.KEY_BONE_UID) is None
    assert rig.skeleton_error(arm, src) is None
    data, msg = exported(obj)
    assert f"bones: added 2 (horn_l, {tip}), into their parent's limb: {tip} -> Cheetah_Tail" in msg, msg
    m = read(data)
    assert [x.name for x in m.bones[48:50]] == ["horn_l", tip]
    assert next(l for l in m.limbs if int(l.limb_id) & 0xFF7FFFFF == 0x80).bone_names[-1] == tip
    val = validate_verdict(obj)
    assert f"STOCK added bone '{tip}' owns no control points and is in no stub limb: it moves nothing in game; skin " \
           "points to it (Boner > Bones > Assign Selected) or delete it" in val, [v for v in val if "added" in v]
    assert not core.blocking(val), core.blocking(val)
    bpy.context.scene.bz_boner.bone = tip
    idx, why = ops.active_bone(bpy.context, obj)
    assert idx < 0 and "Adopt Added Bones" in why, why
    ok(bpy.ops.bz.adopt_bones)
    assert store.bone_index(obj, tip) == n_src + 1 and arm.data.bones[tip].get(rig.KEY_BONE_NEW_NAME) == tip
    assert next(r for r in store.limb_rows(obj) if boner.class_of(r.limb_id) == 0x80).bones[-1] == tip
    assert exported(obj)[0] == data, "adopting changed the export"
    assert call(bpy.ops.bz.adopt_bones)[0] == {"CANCELLED"}          # nothing left: a WARNING report (no raise)
    # an Action keying horn_l: that clip is re-encoded, the others kept
    growl = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == "O_growl")
    ad = arm.animation_data
    prev = ad.action
    actions._assign(arm, growl)
    pb = arm.pose.bones["horn_l"]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (0.9239, 0.3827, 0.0, 0.0)
    pb.keyframe_insert("rotation_quaternion", frame=10)
    pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    pb.keyframe_insert("rotation_quaternion", frame=1)
    actions._assign(arm, prev)
    plan = actions.plan_export(arm)
    assert [c for _a, c in plan.encode] == ["O_growl"] and len(plan.kept) == 19, (plan.encode, plan.kept)
    data, msg = exported(obj, EXTRUDE)
    assert "clips: 19 kept, 1 written" in msg, msg
    m = read(data)
    hi = next(i for i, x in enumerate(m.bones) if x.name == "horn_l")
    clip = next(a for a in m.animations if a.name == "O_growl")
    assert "horn_l" in {t.bone_name.lower() for t in clip.tracks}
    kept = anim_bytes(EXTRUDE)
    stock = anim_bytes(CHEETAH)
    assert {k: v for k, v in kept.items() if k != "O_growl"} == {k: v for k, v in stock.items() if k != "O_growl"}
    start, end = actions.frame_range(growl)
    names = ops.sample_bone_names(ops.gather_export(obj, bpy.context, animations=False), built_of(obj))
    assert names[hi] == "horn_l" and names[49] == tip and len(names) == len(m.bones)
    W = actions.sample_world(arm, growl, names, start, end)
    K = end - start
    worst = max(float(np.abs(R.animated_bone_world(m, clip, f / K)[hi] - W[f][hi]).max()) for f in range(0, K + 1, 3))
    assert worst < 2e-3, worst
    say(f"C EXTRUDE: Blender's extrude under endtail ({tip}) adopted at export (Tail limb, STOCK line: owns nothing), "
        f"Adopt Added Bones stamps it (export unchanged); O_growl keying horn_l re-encoded with a horn_l track playing "
        f"like Blender (max {worst:.1e}), 19 clips kept byte-identical -> {EXTRUDE}")


# ------------------------------------------------------------------------------------------------------------- D
def part_d(name):
    obj = bpy.data.objects[name]
    bpy.context.view_layer.objects.active = obj
    arm = store.rig(obj)
    src = sgm_parser.read(CHEETAH)
    base, _msg = exported(obj)

    def root(eb):
        c = eb.new("floater"); c.head = (0, 0, 1); c.tail = (0, 0.1, 1)
    edit_bones(arm, root)
    export_refused(obj, ("bone 'floater' was added at the root",))
    edit_bones(arm, lambda eb: eb.remove(eb["floater"]))

    def chain(eb):
        a = eb.new("spike1"); a.head = eb["nose"].tail; a.tail = a.head + eb["nose"].vector * 0.5; a.parent = eb["nose"]
        b = eb.new("spike2"); b.head = a.tail; b.tail = b.head + eb["nose"].vector * 0.5; b.parent = a
    bpy.ops.ed.undo_push(message="before chain")
    edit_bones(arm, chain)
    auto, msg_auto = exported(obj)                                       # C8.3: a Blender-made chain is taken as it is
    assert "spike1, spike2" in msg_auto and "bones: added 4" in msg_auto, msg_auto
    ok(bpy.ops.bz.adopt_bones)                                           # stamps it (owner keys for skinning): same file
    data, msg = exported(obj)
    assert data == auto, "adopting a chain Export already took changed the file"
    tip = next(b.name for b in arm.data.bones if b.parent is not None and b.parent.name == "endtail")   # part C's extrude
    assert f"bones: added 4 (horn_l, {tip}, spike1, spike2)" in msg, msg
    m = read(data)
    nose = next(i for i, x in enumerate(src.bones) if x.name == "nose")
    assert [x.name for x in m.bones[48:52]] == ["horn_l", tip, "spike1", "spike2"], [x.name for x in m.bones[48:52]]
    assert m.bones[50].parent == nose and m.bones[51].parent == 50, (m.bones[50].parent, m.bones[51].parent)
    edit_bones(arm, lambda eb: [eb.remove(eb[x]) for x in ("spike2", "spike1")])
    info = {}
    ops.edited_model(obj, bpy.context, animations=False, info=info)      # adopted, then deleted: their limb entries dropped
    assert "limbs: dropped spike1 from Cheetah_Head, spike2 from Cheetah_Head (added bones not on the rig any more)" in \
        info["summary"] and not [w for w in info["warnings"] if "spike" in w], (info["summary"], info["warnings"])
    bpy.ops.ed.undo()                                                    # back before the chain (and its adoption)
    obj = bpy.data.objects[name]
    arm = store.rig(obj)
    bpy.context.view_layer.objects.active = obj
    assert arm.data.bones.get("spike1") is None and len(store.new_bone_uids(obj)) == 2
    assert exported(obj)[0] == base, "Undo of the adopted chain did not give the export back"

    def dup(eb):
        select_only(eb, "ear_r")
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup)
    copy = next(b.name for b in arm.data.bones if b.name.startswith("ear_r."))
    edit_bones(arm, lambda eb: setattr(eb[copy], "head", eb[copy].head + eb[copy].vector * 0.5))
    export_refused(obj, (f"bone '{copy}' was added (it carries a source bone's import stamp",))
    edit_bones(arm, lambda eb: eb.remove(eb[copy]))

    def under_stub(eb):
        c = eb.new("wing_bit"); c.head = eb["top_wing_r"].tail; c.tail = c.head + eb["top_wing_r"].vector
        c.parent = eb["top_wing_r"]
    edit_bones(arm, under_stub)
    export_refused(obj, ("bone 'wing_bit' was added under 'top_wing_r', a stub bone",))
    edit_bones(arm, lambda eb: eb.remove(eb["wing_bit"]))

    def dup_horn(eb):
        select_only(eb, "horn_l")
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup_horn)
    hcopy = next(b.name for b in arm.data.bones if b.name.startswith("horn_l."))
    assert arm.data.bones[hcopy].get(rig.KEY_BONE_UID) == arm.data.bones["horn_l"].get(rig.KEY_BONE_UID)
    data, msg = exported(obj)
    assert f"into their parent's limb: {hcopy} -> Cheetah_Head" in msg, msg
    assert read(data).find_bone(hcopy) is not None
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "name", "horn_x"))
    export_refused(obj, ("none of them keeps that name", "horn_x", hcopy))
    edit_bones(arm, lambda eb: setattr(eb["horn_x"], "name", "horn_l"))
    edit_bones(arm, lambda eb: eb.remove(eb[hcopy]))
    assert exported(obj)[0] == base
    say(f"D REFUSED / ADOPTED: a root bone; an unstamped chain (exported as it is, Adopt stamps it: same file, parent 50); "
        f"a moved copy of ear_r (source stamp); a bone under the stub top_wing_r; a Duplicate of horn_l ({hcopy}: "
        "adopted, horn_l keeps its identity), both renamed away from horn_l: refused; every undo by hand gives the export "
        "back")


# ------------------------------------------------------------------------------------------------------------- E
def part_e():
    fresh()
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    n_src = len(src.bones)
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    # 1. Add Bone with the rig in ITS Edit Mode: the operator leaves it, so Ctrl+Z restores the rig and the tables
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="rig edit")
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.mode == "OBJECT" and bpy.context.active_object == arm
    assert "the rig left its Edit Mode" in str(obj.get("bz_last_add_bone", ""))
    added, msg = exported(obj)
    assert "bones: added 1 (horn_l)" in msg, msg
    bpy.context.view_layer.objects.active = arm
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.mode == "EDIT" and arm.data.edit_bones.get("horn_l") is None and store.new_bone_uids(obj) == []
    bpy.ops.object.mode_set(mode="OBJECT")
    assert arm.data.bones.get("horn_l") is None
    assert exported(obj)[0] == raw, "Add Bone from the rig's Edit Mode, then Ctrl+Z: export not identical"
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="rig edit again")
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    bpy.ops.ed.undo()
    bpy.ops.ed.redo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("horn_l") is not None and len(store.new_bone_uids(obj)) == 1
    assert exported(obj)[0] == added, "Redo of Add Bone"
    bpy.context.view_layer.objects.active = arm
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    if arm.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert exported(obj)[0] == raw
    # 2. Blender's own add in the rig's Edit Mode, then Ctrl+Z (an armature Edit Mode step): identical
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="before own add")
    select_only(arm.data.edit_bones, "nose", tail=True)
    assert bpy.ops.armature.extrude() == {"FINISHED"}
    bpy.ops.ed.undo_push(message="own add")
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="OBJECT")
    assert len(arm.data.bones) == n_src and exported(obj)[0] == raw, "Blender's add + Ctrl+Z in the rig's Edit Mode"
    # 3. Add Bone with the creature in Edit Mode, Ctrl+Z / Redo
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="creature edit")
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert obj.mode == "EDIT" and arm.data.bones.get("horn_l") is not None
    data_e, msg = exported(obj)
    assert data_e == added, msg
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert obj.mode == "EDIT" and arm.data.bones.get("horn_l") is None, [b.name for b in arm.data.bones][-4:]
    assert exported(obj)[0] == raw, "Add Bone in the creature's Edit Mode, then Ctrl+Z"
    bpy.ops.ed.redo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    assert arm.data.bones.get("horn_l") is not None and exported(obj)[0] == added, "Redo in the creature's Edit Mode"
    # 4. points skinned to horn_l, then the bone deleted on the rig: refused, orange; Undo -> exports
    pts = head_points(src)
    bpy.context.scene.bz_boner.bone = "horn_l"
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    skinned, _m = exported(obj)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.ed.undo_push(message="skinned")
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    msg = export_refused(obj, ("8 control points are skinned to added bone 'horn_l', which is not on the rig any more",
                               "orange"))
    assert ops.skin_stats_cached(obj).unassigned >= 8
    bpy.ops.object.mode_set(mode="EDIT")
    ok(bpy.ops.bz.skin_select_unassigned)
    sel = ops.point_selection(obj)
    assert set(pts.tolist()) <= set(np.nonzero(sel)[0].tolist())
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]; arm = store.rig(obj)
    if obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    assert arm.data.bones.get("horn_l") is not None
    assert exported(obj)[0] == skinned, "Undo of the delete"
    # the limb entry of a vanished added bone (no points on it any more): dropped, and the summary says so
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_select_unassigned)
    ok(bpy.ops.bz.skin_auto, target="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    info = {}
    m = ops.edited_model(obj, bpy.context, animations=False, info=info)
    assert "limbs: dropped horn_l from Cheetah_Head (an added bone not on the rig any more)" in info["summary"], info
    assert not [w for w in info["warnings"] if "horn_l" in w] and m.find_bone("horn_l") is None, info["warnings"]
    assert "horn_l" not in {b.lower() for l in m.limbs for b in l.bone_names}
    say("E UNDO: Add Bone from the rig's Edit Mode leaves it (Ctrl+Z: bone and tables back, export identical; Redo: "
        "both); Blender's own add + Ctrl+Z in the rig's Edit Mode identical; Add Bone in the creature's Edit Mode + "
        "Ctrl+Z identical, Redo back; skinned points + the bone deleted: Export refuses naming it, Select Unassigned "
        "selects them, Undo exports; a vanished bone its limb lists: its entry dropped (summary)")


# ------------------------------------------------------------------------------------------------------------- F
def limbs_of(m, bone):
    return [l.name for l in m.limbs if str(bone).lower() in {str(b).lower() for b in l.bone_names}]


def new_lines(m, src):
    """ICParse load-gate and limb bone-list lines ``m`` has that the source has not."""
    from ICParse import validate as ICval
    had = set(ICval.check_model(src)) | set(ICval.check_limb_bones(src))
    # the handle-bone advisory (2026-11-15) is left out: these scripted skins take points without regard to their
    # patches' corners (bone_add_test's part_build pins that it is reported)
    return sorted(x for x in (set(ICval.check_model(m)) | set(ICval.check_limb_bones(m))) - had
                  if "not their nearest corner's" not in x)


def f_readd(how):
    """An added bone renamed (Rename Bone / the Outliner), then a second bone called by its old name: both in the Head."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    if how == "outliner":
        arm.data.bones["horn_l"].name = "horn_x"
    else:
        ok(bpy.ops.bz.rename_bone, bone="horn_l", new_name="horn_x")
    if how == "extrude":
        def ext(eb):
            select_only(eb, "head", tail=True)
            assert bpy.ops.armature.extrude() == {"FINISHED"}
        edit_bones(arm, ext)
        tip = next(b.name for b in arm.data.bones if b.name.startswith("head."))
        arm.data.bones[tip].name = "horn_l"
    else:
        assert boneops.default_bone_name(arm, "head") == "horn_l"
        ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
        text = str(obj.get("bz_last_add_bone", ""))
        assert text.startswith("Added bone 'horn_l' under 'head', in limb 'Cheetah_Head'"), text
    data, msg = exported(obj)
    m = read(data)
    assert (limbs_of(m, "horn_x"), limbs_of(m, "horn_l")) == (["Cheetah_Head"], ["Cheetah_Head"]), \
        (limbs_of(m, "horn_x"), limbs_of(m, "horn_l"))
    assert not new_lines(m, src), new_lines(m, src)
    return f"horn_x + horn_l both in Cheetah_Head ({msg})"


def f_readd_deleted():
    """Add Bone horn_l, delete it on the rig, Add Bone horn_l again: the report names the parent's limb."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    text = str(obj.get("bz_last_add_bone", ""))
    assert text.startswith("Added bone 'horn_l' under 'head', in limb 'Cheetah_Head'"), text
    m = read(exported(obj)[0])
    assert limbs_of(m, "horn_l") == ["Cheetah_Head"] and not new_lines(m, src), (limbs_of(m, "horn_l"), new_lines(m, src))
    return text


def f_deleted(how):
    """An added bone owning no points taken off the rig again: the export is the never-added file, the summary says its
    limb entry was dropped."""
    fresh()
    raw = open(CHEETAH, "rb").read()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    parent, bone, limb = ("endtail", "tail5", "Cheetah_Tail") if how == "creature_edit" else ("head", "horn_l",
                                                                                              "Cheetah_Head")
    if how == "creature_edit":
        bpy.ops.object.mode_set(mode="EDIT")
    ok(bpy.ops.bz.add_bone, parent=parent, name=bone)
    gone = bone
    if how == "renamed":
        ok(bpy.ops.bz.rename_bone, bone=bone, new_name="horn_x")
        gone = "horn_x"
    edit_bones(arm, lambda eb: eb.remove(eb[gone]))
    data, msg = exported(obj)
    assert data == raw, msg
    assert f"dropped {bone} from {limb} (an added bone not on the rig any more)" in msg, msg
    return msg


def f_chain_middle():
    """An adopted chain nose > spike1 > spike2 with spike1 deleted by Blender's Delete: spike2 hangs from nose now (Blender
    re-parents it) and is exported there, in nose's limb; spike1's limb entry is dropped."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)

    def chain(eb):
        a = eb.new("spike1"); a.head = eb["nose"].tail; a.tail = a.head + eb["nose"].vector * 0.5; a.parent = eb["nose"]
        b = eb.new("spike2"); b.head = a.tail; b.tail = b.head + eb["nose"].vector * 0.5; b.parent = a
    edit_bones(arm, chain)
    ok(bpy.ops.bz.adopt_bones)
    rest2 = rig.from_blender_matrix(arm.data.bones["spike2"].matrix_local)

    def del_mid(eb):
        select_only(eb, "spike1")
        assert bpy.ops.armature.delete() == {"FINISHED"}
    edit_bones(arm, del_mid)
    assert arm.data.bones["spike2"].parent.name == "nose"
    data, msg = exported(obj)
    m = read(data)
    i = next(k for k, x in enumerate(m.bones) if x.name == "spike2")
    assert m.find_bone("spike1") is None and m.bones[m.bones[i].parent].name == "nose"
    assert np.allclose(R.bind_world_matrices(m)[i], rest2, atol=1e-5)
    assert limbs_of(m, "spike2") == ["Cheetah_Head"] and not new_lines(m, src), (limbs_of(m, "spike2"), new_lines(m, src))
    assert "dropped spike1 from Cheetah_Head (an added bone not on the rig any more)" in msg, msg
    return msg


def f_reparented():
    """horn_l (8 head points) re-parented under tail4: exported in the Tail limb (the summary says it moved), no split
    limb; re-parented under spine2: in the Torso, and the head's points on it are the stray-point warning and STOCK line
    a bone-list edit gives."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    pts = head_points(src)
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    bpy.ops.object.mode_set(mode="OBJECT")
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["tail4"]))
    data, msg = exported(obj)
    m = read(data)
    i = next(k for k, x in enumerate(m.bones) if x.name == "horn_l")
    assert m.bones[m.bones[i].parent].name == "tail4" and int(m.bones[i].vertex_count) == 8
    assert limbs_of(m, "horn_l") == ["Cheetah_Tail"], limbs_of(m, "horn_l")
    assert not new_lines(m, src), new_lines(m, src)
    assert "horn_l -> Cheetah_Tail (moved from Cheetah_Head: its parent 'tail4' is there)" in msg, msg
    torso = next(l.name for l in src.limbs if boner.class_of(l.limb_id) == 0x800)
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["spine2"]))
    info = {}
    m = ops.edited_model(obj, bpy.context, animations=False, info=info)
    assert limbs_of(m, "horn_l") == [torso], limbs_of(m, "horn_l")
    assert f"horn_l -> {torso} (moved from Cheetah_Head: its parent 'spine2' is there)" in info["summary"], info["summary"]
    assert any(w.startswith(f"added bone 'horn_l' owns 8 control points of 'Cheetah_Head' and moved to '{torso}' with its "
                            "parent 'spine2'") for w in info["warnings"]), info["warnings"]
    val = validate_verdict(obj)
    assert any(v.startswith("STOCK limb 'Cheetah_Head': its patches use control points of horn_l (8 points") for v in val), \
        [v for v in val if "horn_l" in v]
    return msg


def f_limb_ops():
    """Limbs > Add / Remove Active Bone REACH an added bone (fit-to-rig, 2026-09-16: they read the live skeleton, not
    the source's, where an added bone simply did not exist -- "'horn_l' is an added bone ... not supported yet").
    Putting one in a limb PINS it there instead of following its parent; taking it out again drops the pin, and its
    parent's limb has it back."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    torso = next(j for j, l in enumerate(src.limbs) if boner.class_of(l.limb_id) == 0x800)
    head = next(j for j, l in enumerate(src.limbs) if boner.class_of(l.limb_id) == 0x40)
    tname, hname = src.limbs[torso].name, src.limbs[head].name
    assert limbs_of(ops.edited_model(obj, bpy.context, animations=False), "horn_l") == [hname]
    ok(bpy.ops.bz.limb_add_bone, index=torso, bone="horn_l")
    assert [p.lower() for p in store.limb_pins(obj)] == ["horn_l"], store.limb_pins(obj)
    m = ops.edited_model(obj, bpy.context, animations=False)
    assert limbs_of(m, "horn_l") == [tname], limbs_of(m, "horn_l")      # pinned: not pulled back into the Head
    ok(bpy.ops.bz.limb_remove_bone, index=torso, bone="horn_l")
    assert store.limb_pins(obj) == [], store.limb_pins(obj)
    m = ops.edited_model(obj, bpy.context, animations=False)
    assert limbs_of(m, "horn_l") == [hname], limbs_of(m, "horn_l")      # the pin gone: its parent's limb again
    return f"pinned to {tname}, un-pinned back to {hname}"


def part_f():
    fails = []
    for label, fn in (("a rename-bone then re-add", lambda: f_readd("renamebone")),
                      ("a outliner rename then re-add", lambda: f_readd("outliner")),
                      ("a rename then extrude named horn_l", lambda: f_readd("extrude")),
                      ("a delete then re-add: report", f_readd_deleted),
                      ("b add then delete", lambda: f_deleted("plain")),
                      ("b add, rename, delete", lambda: f_deleted("renamed")),
                      ("b creature Edit Mode add then delete", lambda: f_deleted("creature_edit")),
                      ("b adopted chain, middle deleted", f_chain_middle),
                      ("c re-parented into another limb", f_reparented),
                      ("d limb operators on an added bone", f_limb_ops)):
        try:
            got = fn()
            say(f"  F ok   [{label}]: {str(got)[:220]}")
        except Exception as e:
            fails.append(label)
            say(f"  F FAIL [{label}]: {type(e).__name__}: {str(e)[:900]}")
    assert not fails, f"part F failed: {fails}"
    say("F LIMB IDENTITY: a renamed added bone and a second bone called by its old name both export in the Head limb "
        "(Rename Bone, Outliner, extrude); re-add after delete reports the parent's limb; an added bone deleted again "
        "(plain, renamed, from the creature's Edit Mode) exports byte-identical with the dropped entry in the summary; an "
        "adopted chain's middle bone deleted -> the child under nose, in the Head limb; re-parented under tail4 -> Tail "
        "limb (moved), under spine2 -> Torso with the stray warning + STOCK line; Limbs Add / Remove Active Bone say it "
        "is an added bone")


# ------------------------------------------------------------------------------------------------------------- G
def key_rotation(arm, act, bone, frames):
    """Key ``bone``'s rotation in ``act`` at ``frames`` ((frame, quaternion), ...); the rig's Action and the bone's pose
    are put back."""
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


def clip_of(m, name):
    return next(a for a in m.animations if a.name == name)


def has_track(m, clip, bone):
    return str(bone).lower() in {t.bone_name.lower() for t in clip_of(m, clip).tracks}


def pose_of(pb):
    return tuple(pb.location) + tuple(pb.rotation_quaternion) + tuple(pb.scale)


def playback_err(obj, arm, m, clip, act, bone):
    """Largest difference between Blender's world matrix of ``bone`` under ``act`` and the exported clip's."""
    job = ops.gather_export(obj, bpy.context, animations=False)
    names = ops.sample_bone_names(job, core.build_export(job.state)) or rig.current_bone_names(arm, job.source)
    i = next(k for k, x in enumerate(m.bones) if x.name == bone)
    start, end = actions.frame_range(act)
    W = actions.sample_world(arm, act, names, start, end)
    K = end - start
    return max(float(np.abs(R.animated_bone_world(m, clip_of(m, clip), f / K)[i] - W[f][i]).max())
               for f in range(0, K + 1))


def g_unkeyed_added():
    """horn_l keyed in O_growl, head keyed in Melee1 (both re-encoded): Melee1 has no horn_l track -- after O_growl was
    scrubbed in Blender, after a hand pose -- the bytes never change, and the pose is put back."""
    fresh()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    growl = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == "O_growl")
    melee = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == "Melee1")
    key_rotation(arm, growl, "horn_l", ((1, (1.0, 0.0, 0.0, 0.0)), (10, (0.9239, 0.3827, 0.0, 0.0))))
    key_rotation(arm, melee, "head", ((1, (1.0, 0.0, 0.0, 0.0)), (8, (0.966, 0.0, 0.259, 0.0))))
    pb = arm.pose.bones["horn_l"]
    first, msg = exported(obj)
    assert "clips: 18 kept, 2 written" in msg, msg
    m = read(first)
    assert has_track(m, "O_growl", "horn_l") and not has_track(m, "Melee1", "horn_l")
    assert np.allclose(pose_of(pb), (0, 0, 0, 1, 0, 0, 0, 1, 1, 1), atol=1e-6), pose_of(pb)
    # the modder scrubs O_growl (Blender poses horn_l at 45 degrees), then goes back to no Action: the pose stays
    ad = arm.animation_data
    prev = ad.action
    actions._assign(arm, growl)
    bpy.context.scene.frame_set(10)
    actions._assign(arm, prev)
    bpy.context.scene.frame_set(1)
    left = pose_of(pb)
    assert abs(left[4] - 0.3827) < 1e-3, left
    again, _msg = exported(obj)
    assert again == first, "an export after playing O_growl differs (Melee1 baked the leftover horn pose)"
    assert np.allclose(pose_of(pb), left, atol=1e-6), (pose_of(pb), left)
    # a hand pose (location, rotation, scale) of horn_l before Export: the same bytes, and the pose is kept
    pb.location = (0.0, 0.05, 0.01)
    pb.rotation_quaternion = (0.7071, 0.0, 0.7071, 0.0)
    pb.scale = (1.5, 1.0, 1.0)
    hand = pose_of(pb)
    posed, _msg = exported(obj)
    assert posed == first, "an export with horn_l hand-posed differs"
    assert np.allclose(pose_of(pb), hand, atol=1e-6), (pose_of(pb), hand)
    assert playback_err(obj, arm, m, "O_growl", growl, "horn_l") < 2e-3
    assert np.allclose(pose_of(pb), hand, atol=1e-6), "sampling an Action left the pose changed"
    return "Melee1 without a horn_l track after O_growl was played and after a hand pose (bytes identical, pose kept)"


def g_unkeyed_source():
    """A new Action keying only head, active: a hand-posed tail4 (keyed by no F-curve of it) is not baked in."""
    fresh()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    nod = bpy.data.actions.new("HeadNod")
    nod.use_fake_user = True
    key_rotation(arm, nod, "head", ((1, (1.0, 0.0, 0.0, 0.0)), (11, (0.966, 0.259, 0.0, 0.0))))
    actions._assign(arm, nod)
    bpy.context.scene.frame_set(1)
    plan = actions.plan_export(arm)
    assert [c for _a, c in plan.encode] == ["HeadNod"], plan.encode
    rest, msg = exported(obj)
    assert "clips: 20 kept, 1 written" in msg, msg
    m = read(rest)
    pb = arm.pose.bones["tail4"]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (0.7071, 0.0, 0.0, 0.7071)
    hand = pose_of(pb)
    posed, _msg = exported(obj)
    assert posed == rest, "a hand-posed tail4 changed a clip whose Action does not key it"
    assert np.allclose(pose_of(pb), hand, atol=1e-6), (pose_of(pb), hand)
    i = next(k for k, x in enumerate(m.bones) if x.name == "tail4")
    bind = R.bind_world_matrices(m)[i]
    worst = max(float(np.abs(R.animated_bone_world(m, clip_of(m, "HeadNod"), t)[i] - bind).max()) for t in (0.0, 0.5, 1.0))
    assert worst < 1e-5 and has_track(m, "HeadNod", "head"), worst
    return f"HeadNod (keys head only) identical with tail4 hand-posed; tail4 at its bind in HeadNod ({worst:.1e}), pose kept"


def g_f2_rename():
    """horn_l keyed in O_growl, O_growl taken off its NLA track (kept by its fake user), horn_l renamed horn_left with
    Blender's own rename: O_growl is written with a horn_left track playing like Blender; renamed once more while a
    stale path is left under a name no bone has: Export warns."""
    fresh()
    obj = imp(CHEETAH, clips_as_actions=True)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    growl = next(a for a in actions.actions_of(arm) if a.get(actions.KEY_CLIP) == "O_growl")
    key_rotation(arm, growl, "horn_l", ((1, (1.0, 0.0, 0.0, 0.0)), (10, (0.9239, 0.3827, 0.0, 0.0))))
    ad = arm.animation_data
    for t in list(ad.nla_tracks):
        if any(s.action == growl for s in t.strips):
            ad.nla_tracks.remove(t)
    assert ad.action != growl and growl.use_fake_user and growl in actions.actions_of(arm)
    arm.data.bones["horn_l"].name = "horn_left"                      # Blender's own rename (F2 / Outliner)
    paths = {actions.bone_of_path(fc.data_path)[0] for fc in actions._fcurves(growl)
             if actions.bone_of_path(fc.data_path) is not None}
    assert "horn_l" in paths and "horn_left" not in paths, "Blender renamed O_growl's paths: the case is not set up"
    data, msg = exported(obj)
    m = read(data)
    assert has_track(m, "O_growl", "horn_left"), f"O_growl lost horn_left's keys ({msg})"
    assert rig.rename_canon(arm).get("horn_left") == "horn_l", rig.rename_canon(arm)
    plan = actions.plan_export(arm)
    assert [c for _a, c in plan.encode] == ["O_growl"] and not [w for w in plan.warnings if "horn" in w], plan.warnings
    assert {actions.bone_of_path(fc.data_path)[0] for fc in actions._fcurves(growl)
            if actions.bone_of_path(fc.data_path) is not None} == paths, "Export left O_growl's paths changed"
    # Blender plays O_growl with the stale path on no bone; read through the rename it plays like the clip
    renames = {"horn_l": "horn_left"}
    actions.fix_stale_bone_paths(growl, renames)
    err = playback_err(obj, arm, m, "O_growl", growl, "horn_left")
    actions.fix_stale_bone_paths(growl, {"horn_left": "horn_l"})
    assert err < 2e-3, err
    # Rename Bone after that: every Action follows (O_growl's stale path too)
    ok(bpy.ops.bz.rename_bone, bone="horn_left", new_name="horn_x")
    assert "horn_x" in {actions.bone_of_path(fc.data_path)[0] for fc in actions._fcurves(growl)
                        if actions.bone_of_path(fc.data_path) is not None}
    data2, _msg = exported(obj)
    assert has_track(read(data2), "O_growl", "horn_x")
    # a path naming no bone of the rig (a bone renamed with Blender's rename twice, the Action in use only the first
    # time): Export warns that those keys are not written
    for fc in actions._fcurves(growl):
        got = actions.bone_of_path(fc.data_path)
        if got is not None and got[0] == "horn_x":
            fc.data_path = actions.bone_path("horn_mid", got[1])
    plan = actions.plan_export(arm)
    lost = [w for w in plan.warnings if "horn_mid" in w]
    assert lost == ["Action O_growl keys bone 'horn_mid', which the rig has no bone called: Export writes the clip "
                    "without those keys (a bone deleted, or renamed with Blender's own rename again after the Action "
                    "was last in use). Give the bone that name back and rename it with Boner > Bones > Rename Bone (it "
                    "rewrites every Action), or delete those F-curves"], plan.warnings
    info = {}
    ops.edited_model(obj, bpy.context, animations=True, info=info)
    assert lost[0] in info["warnings"], info["warnings"]
    return f"O_growl keeps the horn track through Blender's rename (playback {err:.1e}); unknown bone warns"


def part_g():
    fails = []
    for label, fn in (("a unkeyed added bone: no leftover pose baked", g_unkeyed_added),
                      ("a unkeyed source bone in a new Action", g_unkeyed_source),
                      ("b Blender rename of a keyed added bone", g_f2_rename)):
        try:
            got = fn()
            say(f"  G ok   [{label}]: {str(got)[:220]}")
        except Exception as e:
            fails.append(label)
            say(f"  G FAIL [{label}]: {type(e).__name__}: {str(e)[:900]}")
    assert not fails, f"part G failed: {fails}"
    say("G CLIPS: a re-encoded clip that does not key horn_l (or a new Action that does not key tail4) exports the same "
        "bytes after O_growl was played and after a hand pose, the pose kept; horn_l renamed with Blender's rename "
        "while O_growl sat on no NLA strip keeps its horn track (playing like Blender), Rename Bone then rewrites it; "
        "keys of a bone the rig has no bone called warn")


# ------------------------------------------------------------------------------------------------------------- H
def drawn(fn, *args):
    """``(labels, alerts)`` a panel draw function ``fn(layout, *args)`` shows: every label text and every red message."""
    labels, alerts = [], []

    class Rec:
        def __init__(self, name=""):
            object.__setattr__(self, "_name", name)

        def __getattr__(self, name):
            return Rec(name)

        def __call__(self, *a, **kw):
            if self._name == "label":
                labels.append(str(kw.get("text", "")))
            return Rec()

        def __setattr__(self, k, v):
            object.__setattr__(self, k, v)
    was = ui._alert
    ui._alert = lambda layout, text, icon="ERROR", width=60: alerts.append(str(text))
    try:
        fn(Rec(), *args)
    finally:
        ui._alert = was
    return labels, alerts


def rig_box(obj):
    return drawn(lambda layout, o: ui.BZ_PT_panel._rig_box(layout, o), obj)


def bones_tab(obj):
    return drawn(lambda layout, o: ui.BZ_PT_boner.bones_tab(layout, bpy.context, o), obj)


def spike_chain(eb):
    a = eb.new("spike1"); a.head = eb["nose"].tail; a.tail = a.head + eb["nose"].vector * 0.5; a.parent = eb["nose"]
    b = eb.new("spike2"); b.head = a.tail; b.tail = b.head + eb["nose"].vector * 0.5; b.parent = a


def h_alert_after_adopt():
    """C8.3: an unstamped chain raises no skeleton alert (Export takes it); the Bones tab lists it as pending, Adopt Added
    Bones stamps it (then it is no longer pending) and Ctrl+Z brings the pending line back (the cache follows the
    stamps, not only names and parents); a Shift+D copy of the chain IS alerted (ambiguous) and Adopt names it."""
    fresh()
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    edit_bones(arm, spike_chain)
    _l, alerts = rig_box(obj)
    assert alerts == [], alerts
    added, bad = ui._added_bones_cached(arm, obj)
    assert added == ["spike1", "spike2"] and not bad, (added, bad)
    bpy.ops.ed.undo_push(message="chain")
    ok(bpy.ops.bz.adopt_bones)
    _l, alerts = rig_box(obj)
    assert alerts == [], alerts
    assert store.rig(obj).data.bones["spike1"].get(rig.KEY_BONE_UID) is not None
    bpy.context.view_layer.objects.active = obj
    bpy.ops.ed.undo()
    obj = bpy.data.objects[name]
    arm = store.rig(obj)
    assert arm.data.bones["spike1"].get(rig.KEY_BONE_UID) is None
    _l, alerts = rig_box(obj)
    assert alerts == [], alerts
    ok(bpy.ops.bz.adopt_bones)                                           # stamped again, then a copy of the chain

    def dup_chain(eb):
        for e in eb:
            e.select = e.select_head = e.select_tail = e.name in ("spike1", "spike2")
        eb.active = eb["spike1"]
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup_chain)
    assert arm.data.bones.get("spike1.001") is not None, [b.name for b in arm.data.bones][-4:]
    _l, alerts = rig_box(obj)
    assert any("is a copy of the added bone 'spike1'" in a for a in alerts), alerts
    ok(bpy.ops.bz.adopt_bones)                                           # gives the copies their own identity
    _l, alerts = rig_box(obj)
    assert alerts == [], alerts
    _data, msg = exported(obj)
    assert "bones: added 4" in msg, msg
    edit_bones(arm, lambda eb: [eb.remove(eb[x]) for x in ("spike2.001", "spike1.001")])

    def dup_source(eb):                                                  # a copy of a SOURCE bone: nothing to adopt
        select_only(eb, "ear_r")
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup_source)
    msg = refused(bpy.ops.bz.adopt_bones, ("no added bone to adopt", "every bone Blender added is taken by Export as it is",
                                           "ambiguous bones Adopt can't take", "carries a source bone's import stamp"))
    return ("no alert for a chain (pending in the Bones tab), Adopt stamps it, Ctrl+Z unstamps; a copied chain alerted, "
            "Adopt gives the copies identity; a copied source bone: Adopt names it as ambiguous")


def h_add_bone_begin_fails():
    """Add Bone whose journaled edit can't start leaves no bone on the rig (refused before the bone is made, or the bone
    taken off again); Adopt Added Bones with an unreadable source says so in plain words (no traceback)."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    n = len(arm.data.bones)
    obj.data.attributes.remove(obj.data.attributes[store.ATTR_GEN])
    msg = refused(bpy.ops.bz.add_bone, ("Add Bone refused", "generation attribute"), parent="head", name="horn_l")
    assert len(arm.data.bones) == n and arm.data.bones.get("horn_l") is None, (msg, [b.name for b in arm.data.bones][-3:])
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    was = ops._begin

    def no_begin(op, o):
        op.report({"ERROR"}, "journal refused (test)")
        return False
    ops._begin = no_begin
    try:
        msg2 = refused(bpy.ops.bz.add_bone, ("journal refused (test)",), parent="head", name="horn_l")
    finally:
        ops._begin = was
    assert len(arm.data.bones) == n and arm.data.bones.get("horn_l") is None, (msg2, [b.name for b in arm.data.bones][-3:])
    assert store.new_bone_uids(obj) == []
    obj[store.KEY_SOURCE] = "/tmp/bz_no_such_source.sgm"
    msg3 = refused(bpy.ops.bz.adopt_bones, ("Adopt Added Bones refused: the source creature can't be read",))
    assert "Traceback" not in msg3, msg3
    return f"{msg[:80]} | {msg3[:90]}"


def h_key_check_failed():
    """The added-bone key check failing is said where the bone is used: the active bone, the Bones tab, Assign Selected."""
    fresh()
    obj = imp(CHEETAH)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    was = rig.classify_added

    def broken(arm_obj, res, stubs, stub_bad, table_uids=(), *a, **kw):
        if list(table_uids):
            raise RuntimeError("boom (test)")
        return was(arm_obj, res, stubs, stub_bad, table_uids, *a, **kw)
    rig.classify_added = broken
    try:
        bpy.context.scene.bz_boner.bone = "horn_l"
        idx, why = ops.active_bone(bpy.context, obj)
        assert idx < 0 and "added bone check failed: RuntimeError: boom (test)" in why, why
        _l, alerts = bones_tab(obj)
        assert any("added bone check failed: RuntimeError: boom (test)" in a for a in alerts), alerts
        bpy.ops.object.mode_set(mode="EDIT")
        select_points(obj, [0])
        refused(bpy.ops.bz.skin_assign_active, ("added bone check failed",))
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        rig.classify_added = was
    return why


def h_source_stubs():
    """A bone under a SOURCE stub bone is refused where it is shown, not only at Export: a leaf Blender adds under
    top_wing_r is no pending added bone (the Bones tab and the rig box say why), and horn_l re-parented under it has no
    owner key (Assign Selected says why)."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)

    def under_stub(eb):
        c = eb.new("wing_bit"); c.head = eb["top_wing_r"].tail; c.tail = c.head + eb["top_wing_r"].vector
        c.parent = eb["top_wing_r"]
    edit_bones(arm, under_stub)
    stub_words = "bone 'wing_bit' was added under 'top_wing_r', a stub bone"
    labels, alerts = bones_tab(obj)
    assert not [x for x in labels if x.startswith("pending: added")], labels
    assert any(stub_words in a for a in alerts), alerts
    _l, alerts = rig_box(obj)
    assert any(stub_words in a for a in alerts), alerts
    bpy.context.scene.bz_boner.bone = "wing_bit"
    idx, why = ops.active_bone(bpy.context, obj)
    assert idx < 0 and "a stub bone" in why, why
    edit_bones(arm, lambda eb: eb.remove(eb["wing_bit"]))
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["top_wing_r"]))
    assert store.bone_index(obj, "horn_l") == -1, store.bone_index(obj, "horn_l")
    bpy.context.scene.bz_boner.bone = "horn_l"
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, [0])
    msg = refused(bpy.ops.bz.skin_assign_active, ("'top_wing_r', a stub bone",))
    bpy.ops.object.mode_set(mode="OBJECT")
    return msg


def h_messages():
    """Plain messages: a case clash is listed once and Adopt Added Bones refuses it before stamping; an added-bone
    refusal carries no delete / re-parent tail (a deleted bone still does); points on a deleted added bone name its last
    rig name; Add Bone's disabled-rig refusal talks about the bone."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)

    def clash(eb):
        a = eb.new("spike"); a.head = eb["nose"].tail; a.tail = a.head + eb["nose"].vector * 0.5; a.parent = eb["nose"]
        b = eb.new("SPIKE"); b.head = eb["head"].tail; b.tail = b.head + eb["head"].vector * 0.5; b.parent = eb["head"]
    edit_bones(arm, clash)
    msg = export_refused(obj, ("differ only in case",))
    assert msg.count("differ only in case") == 1, msg
    refused(bpy.ops.bz.adopt_bones, ("Adopt Added Bones refused", "differ only in case"))
    assert store.new_bone_uids(obj) == [] and all(arm.data.bones[x].get(rig.KEY_BONE_UID) is None for x in ("spike", "SPIKE"))
    edit_bones(arm, lambda eb: [eb.remove(eb[x]) for x in ("spike", "SPIKE")])

    def root(eb):
        c = eb.new("floater"); c.head = (0, 0, 1); c.tail = (0, 0.1, 1)
    edit_bones(arm, root)
    msg_root = export_refused(obj, ("bone 'floater' was added at the root",))
    assert "delete or re-parent" not in msg_root and "Undo the change" not in msg_root, msg_root
    edit_bones(arm, lambda eb: eb.remove(eb["floater"]))
    edit_bones(arm, lambda eb: eb.remove(eb["endtail"]))
    msg_del = export_refused(obj, ("bone 'endtail' was deleted, but the game looks 'endtail' up by name",))   # C8.1
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, head_points(src))
    ok(bpy.ops.bz.skin_assign_active)
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.data.bones["horn_l"].name = "horn_x"
    edit_bones(arm, lambda eb: eb.remove(eb["horn_x"]))
    msg_gone = export_refused(obj, ("8 control points are skinned to added bone 'horn_x' (made as 'horn_l'), which is not "
                                    "on the rig any more",))
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    arm.hide_viewport = True
    msg_hidden = refused(bpy.ops.bz.add_bone, ("disabled in viewports", "enable it, then add the bone"), parent="head",
                         name="horn_l")
    assert "stub socket" not in msg_hidden, msg_hidden
    return f"{msg_root[:70]} | {msg_del[:60]} | {msg_gone[:80]} | {msg_hidden[:90]}"


def h_limbs_panel():
    """The Limbs panel shows added bones where Export puts them: horn_l re-parented under tail4 in the Tail (with the
    move said), deleted again in no limb."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    from blender_native import limbops

    def rows_with(bone):
        t = limbops.limb_table_cached(obj)
        assert not t.error, t.error
        return [r.name for r, bl in zip(t.rows, t.bones) if bone.lower() in {str(b).lower() for b in bl}]
    assert rows_with("horn_l") == ["Cheetah_Head"], rows_with("horn_l")
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["tail4"]))
    assert rows_with("horn_l") == ["Cheetah_Tail"], rows_with("horn_l")
    s = bpy.context.scene.bz_boner
    s.mode = "LIMBS"
    s.bone = "horn_l"
    t = limbops.limb_table_cached(obj)
    s.active_limb = next(i for i, r in enumerate(t.rows) if r.name == "Cheetah_Tail")
    labels, _a = drawn(lambda layout, o: ui.BZ_PT_boner.limbs_tab(layout, bpy.context, o), obj)
    assert "horn_l: in Cheetah_Tail" in labels, [x for x in labels if "horn" in x]
    assert any("horn_l -> Cheetah_Tail (moved from Cheetah_Head: its parent 'tail4' is there)" in x for x in labels), \
        [x for x in labels if "horn" in x]
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    assert rows_with("horn_l") == [], rows_with("horn_l")
    return "Head -> Tail after the re-parent, none after the delete"


def part_h():
    fails = []
    for label, fn in (("a skeleton alert gone after Adopt", h_alert_after_adopt),
                      ("b Add Bone / Adopt when the edit can't start", h_add_bone_begin_fails),
                      ("c added bone key check failed is said", h_key_check_failed),
                      ("d source stub bones in the UI and skin tools", h_source_stubs),
                      ("e plain messages", h_messages),
                      ("f Limbs panel shows the export's placement", h_limbs_panel)):
        try:
            got = fn()
            say(f"  H ok   [{label}]: {str(got)[:260]}")
        except Exception as e:
            fails.append(label)
            say(f"  H FAIL [{label}]: {type(e).__name__}: {str(e)[:900]}")
    assert not fails, f"part H failed: {fails}"
    say("H UI AND MESSAGES: the skeleton alert follows Adopt and Ctrl+Z; Add Bone never leaves a bone when its edit can't "
        "start, Adopt with an unreadable source says so; a failed added-bone check is said at the active bone, the Bones "
        "tab and Assign Selected; a bone under a source stub bone is refused in the panel and the skin tools; a case "
        "clash once, refused by Adopt; no delete / re-parent tail on added-bone refusals; a deleted added bone named by "
        "its last name; the disabled-rig refusal talks about the bone; the Limbs panel shows the export's placement")


# ------------------------------------------------------------------------------------------------------------- I
def point_on_rig(obj, arm, i):
    """Control point ``i`` in the rig's space (where an edit bone's head goes)."""
    return arm.matrix_world.inverted() @ (obj.matrix_world @ obj.data.vertices[int(i)].co)


def i_new_stub_limb():
    """A new limb with no patches naming endtail (a point-less leaf) makes endtail a stub bone of the export: Add Bone
    under it is refused, a leaf Blender extrudes under it is refused by Export's skeleton guard (not by the build), and
    once endtail owns points it is no stub bone and the leaf exports."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    src = sgm_parser.read(CHEETAH)
    n_src = len(src.bones)
    store.begin_edit(obj)
    store.set_limb_rows(obj, store.limb_rows(obj) + [structure.LimbRow(-1, "NewStub", 0x2000, ("endtail",))])
    refused(bpy.ops.bz.add_bone, ("'endtail' is a stub bone",), parent="endtail", name="tail_tip")
    assert arm.data.bones.get("tail_tip") is None and not store.new_bone_uids(obj)

    def extrude(eb):
        select_only(eb, "endtail", tail=True)
        assert bpy.ops.armature.extrude() == {"FINISHED"}
    edit_bones(arm, extrude)
    tip = next(b.name for b in arm.data.bones if b.parent is not None and b.parent.name == "endtail")
    msg = export_refused(obj, (f"bone '{tip}' was added under 'endtail', a stub bone",))
    assert "Export failed" not in msg, msg
    endtail = next(i for i, b in enumerate(src.bones) if b.name == "endtail")
    pts = np.nonzero(store.vert_bone(obj) == next(i for i, b in enumerate(src.bones) if b.name == "tail4"))[0][:2]
    bpy.context.scene.bz_boner.bone = "endtail"
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    bpy.ops.object.mode_set(mode="OBJECT")
    data, summary = exported(obj)
    m = read(data)
    ti = next(i for i, b in enumerate(m.bones) if b.name == tip)
    assert m.bones[m.bones[ti].parent].name == "endtail" and ti == n_src - 6, ti
    assert int(m.bones[endtail].meta0) & 0xFF == 0, "endtail owns points: no stub byte"
    return f"Add Bone refused, extrude {tip} refused by the guard; endtail skinned -> {tip} exported at {ti}"


def i_auto_skin_added():
    """Auto Skin (Reset All) takes the added bones: horn_l with its head on a head control point gets that point; an
    added bone named like an end site, a non-deforming added bone and a bone not adopted yet get none."""
    fresh()
    obj = imp(CHEETAH)
    name = obj.name
    arm = store.rig(obj)
    src = sgm_parser.read(CHEETAH)
    n_src = len(src.bones)
    top = head_points(src, points=8, corners=0)
    p0, p1 = int(top[0]), int(top[-1])
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    ok(bpy.ops.bz.add_bone, parent="head", name="endhorn")

    def place(eb):
        for bn, p in (("horn_l", p0), ("endhorn", p1)):
            eb[bn].head = point_on_rig(obj, arm, p)
            eb[bn].tail = eb[bn].head + eb["head"].vector * 0.3
        c = eb.new("nose_bit"); c.head = eb["nose"].tail; c.tail = c.head + eb["nose"].vector; c.parent = eb["nose"]
    edit_bones(arm, place)
    bpy.context.view_layer.objects.active = obj
    assert store.bone_index(obj, "horn_l") == n_src and store.bone_index(obj, "endhorn") == n_src + 1
    ok(bpy.ops.bz.skin_auto, target="ALL")
    vb = store.vert_bone(obj)
    assert vb[p0] == n_src and not (vb == n_src + 1).any() and vb.max() < n_src + 2, (vb[p0], vb.max())
    heads, tails, eligible = ops.auto_skin_rig(obj, arm, store.owner_count(obj))
    assert len(eligible) == n_src + 2 and eligible[n_src] and not eligible[n_src + 1], eligible[n_src:]
    assert ops.pending_added_bones(obj, arm) == ["nose_bit"], ops.pending_added_bones(obj, arm)
    horn = int((vb == n_src).sum())
    data, _msg = exported(obj)
    m = read(data)
    hi = next(i for i, b in enumerate(m.bones) if b.name == "horn_l")
    assert int((np.asarray(R.vertex_bone_owners(m)) == hi).sum()) == horn and core.aux_rule_problems(m) == []
    arm.data.bones["horn_l"].use_deform = False
    ok(bpy.ops.bz.skin_auto, target="ALL")
    assert not (store.vert_bone(obj) >= n_src).any(), "a non-deforming added bone took points"
    arm.data.bones["horn_l"].use_deform = True
    return f"Reset All: horn_l takes {horn} points (its head's point {p0} among them), endhorn and nose_bit none"


def i_unclaimed_action():
    """A new Action keying horn_l held only by its fake user (not active, on no NLA track, unstamped) is not exported
    (as before) but Export and plan_export say so; an unused Action keying a bone the rig lacks, and one stamped for
    another rig, are not named; stashed on the rig's NLA it exports and the warning goes."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    wig = bpy.data.actions.new("HornWiggle")
    wig.use_fake_user = True
    key_rotation(arm, wig, "horn_l", ((1, (1.0, 0.0, 0.0, 0.0)), (9, (0.9239, 0.3827, 0.0, 0.0))))
    other = bpy.data.actions.new("WingFlap")
    other.use_fake_user = True
    key_rotation(arm, other, "head", ((1, (1.0, 0.0, 0.0, 0.0)),))
    for fc in actions._fcurves(other):
        fc.data_path = fc.data_path.replace('"head"', '"wing_x"')
    theirs = bpy.data.actions.new("TheirWiggle")
    theirs.use_fake_user = True
    key_rotation(arm, theirs, "horn_l", ((1, (1.0, 0.0, 0.0, 0.0)),))
    theirs[actions.KEY_ARM_UID] = "another-rig"
    assert arm.animation_data.action is None and wig.users == 1
    plan = actions.plan_export(arm)
    assert not plan.encode, plan.encode
    words = "Action HornWiggle keys bones of rig"
    loose = [w for w in plan.warnings if words in w]
    assert len(loose) == 1 and "not exported" in loose[0] and "active Action" in loose[0] and "NLA" in loose[0], plan.warnings
    assert "WingFlap" not in " ".join(plan.warnings) and "TheirWiggle" not in " ".join(plan.warnings), plan.warnings
    job = ops.gather_export(obj, bpy.context)                            # what Export reports as WARNINGs
    assert [w for w in job.warnings if words in w] == loose, job.warnings
    data, _summary = exported(obj)
    assert "HornWiggle" not in {a.name for a in read(data).animations}
    actions.stash(arm, wig)
    plan = actions.plan_export(arm)
    assert [c for _a, c in plan.encode] == ["HornWiggle"] and not [w for w in plan.warnings if words in w], plan.warnings
    return loose[0]


def part_i():
    fails = []
    for label, fn in (("a new stub limb's bone takes no added bone (Add Bone, Export guard)", i_new_stub_limb),
                      ("b Auto Skin takes added bones", i_auto_skin_added),
                      ("c an unclaimed Action is named", i_unclaimed_action)):
        try:
            got = fn()
            say(f"  I ok   [{label}]: {str(got)[:260]}")
        except Exception as e:
            fails.append(label)
            say(f"  I FAIL [{label}]: {type(e).__name__}: {str(e)[:900]}")
    assert not fails, f"part I failed: {fails}"
    say("I STUBS, AUTO SKIN, ACTIONS: a new stub limb's bone takes no added bone (Add Bone refuses, Export's guard refuses "
        "a Blender leaf, skinned it is no stub); Auto Skin gives added bones points (not end sites, non-deforming or "
        "unadopted ones); a new Action held only by its fake user is named by plan_export and Export, and exports once "
        "on the rig's NLA")


# ------------------------------------------------------------------------------------------------------------- J
def dup_bones(arm, names):
    """Shift+D of the rig bones ``names`` (in the rig's Edit Mode); returns the copies' names, in ``names`` order."""
    before = {b.name for b in arm.data.bones}

    def dup(eb):
        for e in eb:
            e.select = e.select_head = e.select_tail = False
        for nm in names:
            eb[nm].select = eb[nm].select_head = eb[nm].select_tail = True
        eb.active = eb[names[0]]
        assert bpy.ops.armature.duplicate() == {"FINISHED"}
    edit_bones(arm, dup)
    made = [b.name for b in arm.data.bones if b.name not in before]
    return [next(c for c in made if c.startswith(nm + ".")) for nm in names]


def j_copy_of_chain():
    """Shift+D of an added chain: the copy with a bone under it is refused as a copy of the added bone (not as a bone
    with no Add Bone stamp: it carries the original's), with the hint that works -- Adopt Added Bones -- in the rig box
    and at Export; adopted, the copies export beside the originals."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    ok(bpy.ops.bz.add_bone, parent="horn_l", name="horn_tip")
    copy, tip = dup_bones(arm, ["horn_l", "horn_tip"])
    assert arm.data.bones[copy].get(rig.KEY_BONE_UID) == arm.data.bones["horn_l"].get(rig.KEY_BONE_UID)
    words = (f"bone '{copy}' is a copy of the added bone 'horn_l' (Duplicate / Symmetrize) with 1 bone(s) under it, so the "
             "export can't tell a chain from a re-parent: Boner > Bones > Adopt Added Bones gives the copies their own "
             "identity")
    msg = export_refused(obj, (words, f"bone '{tip}' was added under '{copy}', an added bone the export refuses"))
    assert "no Add Bone stamp" not in msg, msg
    _l, alerts = rig_box(obj)
    assert any(words[:60] in a for a in alerts), alerts
    ok(bpy.ops.bz.adopt_bones)
    data, summary = exported(obj)
    m = read(data)
    assert [b.name for b in m.bones if b.name.startswith("horn")] == ["horn_l", "horn_tip", copy, tip], \
        [b.name for b in m.bones if b.name.startswith("horn")]
    return msg


def j_copy_took_identity():
    """Shift+D of a skinned added bone, then the original deleted: the copy holds the stamp alone and takes its identity
    (its points), but the points are still in the vertex group of the deleted bone's name, which Blender deforms by: the
    Export refusal and a skin edit on other points say that (not "Undo the weight edit"); renamed back, it exports."""
    fresh()
    src = sgm_parser.read(CHEETAH)
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    pts = head_points(src)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    bpy.ops.object.mode_set(mode="OBJECT")
    (copy,) = dup_bones(arm, ["horn_l"])
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    n_src = len(src.bones)
    assert store.new_bone_keys(obj) == {copy: n_src}, store.new_bone_keys(obj)
    words = (f"8 control points skinned to added bone '{copy}' are still in vertex group 'horn_l': '{copy}' is a copy "
             "Duplicate / Symmetrize made of 'horn_l' and took its place when 'horn_l' was deleted, but Blender deforms "
             f"points by their group's name and no bone is called 'horn_l', so they stay at rest. Rename '{copy}' back "
             "to 'horn_l', or select them and Assign Selected to Bone")
    msg = export_refused(obj, (words,))
    assert "Undo the weight edit" not in msg and "in no bone's vertex group" not in msg, msg
    other = int(np.nonzero(store.vert_bone(obj) == next(i for i, b in enumerate(src.bones) if b.name == "tail4"))[0][0])
    bpy.context.scene.bz_boner.bone = "tail4"
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, [other])
    msg2 = refused(bpy.ops.bz.skin_assign_active, (words,))
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.data.bones[copy].name = "horn_l"
    data, _summary = exported(obj)
    m = read(data)
    hi = next(i for i, b in enumerate(m.bones) if b.name == "horn_l")
    assert int((np.asarray(R.vertex_bone_owners(m)) == hi).sum()) == 8
    return f"{msg[:200]} | skin edit: {msg2[:60]}"


def j_new_stub_limb_readouts():
    """With a new limb with no patches naming endtail (so endtail is a stub bone of the export), the panel read-outs use
    the export's stub set: horn_l re-parented under endtail has no owner key (store.new_bone_keys, the active bone says
    why), a leaf Blender extruded under it is no pending bone (Bones tab) and the rig box and the Bones tab show the
    guard's refusal; Add Bone adopting a pending leaf on the way warns with that refusal, and neither it nor Adopt Added
    Bones stamps the leaf under endtail."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    store.begin_edit(obj)
    store.set_limb_rows(obj, store.limb_rows(obj) + [structure.LimbRow(-1, "NewStub", 0x2000, ("endtail",))])
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["endtail"]))
    assert "horn_l" not in store.new_bone_keys(obj), store.new_bone_keys(obj)
    bpy.context.scene.bz_boner.bone = "horn_l"
    idx, why = ops.active_bone(bpy.context, obj)
    assert idx < 0 and "bone 'horn_l' was added under 'endtail', a stub bone" in why, (idx, why)
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["head"]))
    assert "horn_l" in store.new_bone_keys(obj)

    def leaves(eb):
        select_only(eb, "endtail", tail=True)
        assert bpy.ops.armature.extrude() == {"FINISHED"}
        c = eb.new("nose_bit"); c.head = eb["nose"].tail; c.tail = c.head + eb["nose"].vector; c.parent = eb["nose"]
    edit_bones(arm, leaves)
    tip = next(b.name for b in arm.data.bones if b.parent is not None and b.parent.name == "endtail")
    stub_words = f"bone '{tip}' was added under 'endtail', a stub bone"
    labels, alerts = bones_tab(obj)
    assert not [x for x in labels if x.startswith("pending: added") and tip in x], labels
    assert any(stub_words in a for a in alerts), alerts
    _l, alerts = rig_box(obj)
    assert any(stub_words in a for a in alerts), alerts
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_r")
    text = str(obj.get("bz_last_add_bone", ""))
    assert "also adopted nose_bit" in text and f"Export refuses the skeleton as it is: {stub_words}" in text, text
    assert arm.data.bones[tip].get(rig.KEY_BONE_UID) is None, "Add Bone stamped a bone under a stub bone"
    arm.data.bones[tip][rig.KEY_BONE_UID] = "x"                         # whatever Add Bone did: Adopt on its own
    del arm.data.bones[tip][rig.KEY_BONE_UID]
    res, msg = call(bpy.ops.bz.adopt_bones)
    assert arm.data.bones[tip].get(rig.KEY_BONE_UID) is None, ("Adopt Added Bones stamped a bone under a stub bone", res, msg)
    src = sgm_parser.read(CHEETAH)                                      # endtail given points: no stub bone any more
    pts = np.nonzero(store.vert_bone(obj) == next(i for i, b in enumerate(src.bones) if b.name == "tail4"))[0][:2]
    bpy.context.scene.bz_boner.bone = "endtail"
    bpy.ops.object.mode_set(mode="EDIT")
    select_points(obj, pts)
    ok(bpy.ops.bz.skin_assign_active)
    _l, alerts = bones_tab(obj)                                          # the Edit Mode panel follows the new generation
    assert not any(stub_words in a for a in alerts), alerts
    bpy.ops.object.mode_set(mode="OBJECT")
    _l, alerts = rig_box(obj)
    assert not any(stub_words in a for a in alerts), alerts
    ok(bpy.ops.bz.adopt_bones)
    assert arm.data.bones[tip].get(rig.KEY_BONE_UID) is not None
    return text[-160:]


def j_select_bones_placed():
    """Limbs > Select Bones selects the bones the panel lists (the export's placement): horn_l re-parented under tail4
    with the Tail, not the Head; deleted, not listed as missing."""
    fresh()
    obj = imp(CHEETAH)
    arm = store.rig(obj)
    ok(bpy.ops.bz.add_bone, parent="head", name="horn_l")
    from blender_native import limbops
    rows = limbops.limb_table(obj).rows
    head = next(i for i, r in enumerate(rows) if r.name == "Cheetah_Head")
    tail = next(i for i, r in enumerate(rows) if r.name == "Cheetah_Tail")
    edit_bones(arm, lambda eb: setattr(eb["horn_l"], "parent", eb["tail4"]))
    bpy.context.view_layer.objects.active = obj
    ok(bpy.ops.bz.limb_select_bones, index=tail)
    assert "horn_l" in limbops.selected_rig_bones(arm), limbops.selected_rig_bones(arm)
    ok(bpy.ops.bz.limb_select_bones, index=head)
    assert "horn_l" not in limbops.selected_rig_bones(arm), limbops.selected_rig_bones(arm)
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))
    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.bz.limb_select_bones("EXEC_DEFAULT", True, index=tail)
    assert res == {"FINISHED"}
    sel = limbops.selected_rig_bones(arm)
    want = [b for b in limbops.limb_table_cached(obj).bones[tail]]
    assert sorted(sel) == sorted(limbops.rig_bone_name(obj, b) for b in want) and "horn_l" not in want, (sel, want)
    return f"Tail {len(want)} bones after the delete"


def part_j():
    fails = []
    for label, fn in (("a a copy of an added chain is named a copy", j_copy_of_chain),
                      ("b a copy that took the deleted bone's identity: its vertex group", j_copy_took_identity),
                      ("c a new stub limb's bones in the panel read-outs, Add Bone and Adopt", j_new_stub_limb_readouts),
                      ("d Limbs > Select Bones selects the placed lists", j_select_bones_placed)):
        try:
            got = fn()
            say(f"  J ok   [{label}]: {str(got)[:260]}")
        except Exception as e:
            fails.append(label)
            say(f"  J FAIL [{label}]: {type(e).__name__}: {str(e)[:900]}")
    assert not fails, f"part J failed: {fails}"
    say("J COPIES, STUB SET, SELECT BONES: a Shift+D copy of an added chain is refused as a copy (Adopt gives it its own "
        "identity); a copy that took a deleted skinned bone's identity is refused naming its vertex group, renamed back "
        "it exports; the panel, Add Bone and Adopt use the export's stub bones with a new stub limb; Limbs > Select Bones "
        "selects the placed lists")


def main():
    blender_native.register()
    parts = os.environ.get("BZ_ADD_BONE_PARTS", "ABCDEFGHIJ")
    name = None
    if "A" in parts:
        part_a()
    if "B" in parts:
        name = part_b()
    if "C" in parts:
        part_c(name)
    if "D" in parts:
        part_d(name)
    if "E" in parts:
        part_e()
    if "F" in parts:
        part_f()
    if "G" in parts:
        part_g()
    if "H" in parts:
        part_h()
    if "I" in parts:
        part_i()
    if "J" in parts:
        part_j()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_add_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_add_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
