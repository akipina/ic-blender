"""Boner > Limbs, bone lists, in headless Blender (increment B2): Select Limb Bones, Set Root Bone, Add / Remove Active
Bone on an imported cheetah and a New Creature -- journaled, undoable, exported through ICParse's writer, checked by
Validate.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_limb_bones_test.py

A. registration: the four bone-list operators; the Limbs tab shows the active limb's bone count, every bone name and the
   four buttons; the rows show their bone counts;
B. cheetah (stock): Set Root Bone with every non-torso limb's stock root changes nothing (byte-identical, rows still
   "the source's"); Select Limb Bones selects exactly LFLeg's bones on the rig (the first active, and the panel's
   bone); re-rooting LFLeg at Front_leg2_l is refused (it would leave Front_leg1_l, a standard root, to the Torso) and
   so is LFLeg at spine3 (torso connection hosts); Front_leg1_l moved to the Torso with Add Active Bone -- what
   ComboTest.exe fails on as a donor (wf14) -- reports exactly that move plus both warnings (its 46 LFLeg points, the
   Torso's standard root), exports only those two bone lists (/tmp/bz_boner_b2_lf_torso.sgm), and Validate adds exactly
   the two STOCK lines and, at Quick, the precheck's two COMBINE ERRORs; nothing blocks; Undo is byte-identical, Redo
   moved, back is byte-identical with the rows "the source's"; nose moved to the Torso (/tmp/bz_boner_b2_move.sgm: the
   Head's stray-points line) and back; nose removed (/tmp/bz_boner_b2_nose.sgm: only the Head's LIMB record, the
   no-limb and stray-points lines, nothing blocking) and re-added: byte-identical; refusals change nothing (the Torso's
   root, a standard root, host bones by Set Root / Add / Remove, another limb's only bone, a limb's only bone, a bone the
   limb lacks, no active bone, a duplicated rig bone sharing the import stamp);
C. New Creature: LFLeg / RFLeg re-rooted at front_leg1 (front_leg_root goes to the Torso, the stock convention) export
   /tmp/bz_boner_b2_reroot.sgm with no STOCK / OE line and no COMBINE ERROR in the Quick combine tier (13 real
   combines, both roles); re-rooted back and upperMane removed + re-added: byte-identical; Set Root at a leaf is refused
   (front_leg1_l); a one-bone leg made by removing the other bones gives the STOCK donor line, the combiner's WARN and
   the stray-points STOCK line + COMBINE ERROR; re-added: byte-identical.
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, limbops, structure  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
NC_SRC = "/tmp/bz_boner_bones_nc_src.sgm"
OUT = "/tmp/bz_boner_bones_out.sgm"
_log = open("/tmp/bz_boner_bones_log.txt", "w")


def _history(op, obj):
    """``bpy.ops.ed.undo`` / ``redo``, then the live creature object. In Blender 5.3 an Undo or Redo in Edit Mode
    can replace the Object and the Scene, so a reference taken before it is dead ("StructRNA of type Object has been
    removed"), and writing through a dead Scene setting can crash Blender: look the object up again by name, and
    re-read ``bpy.context.scene`` settings after every call (Blender 5.2 kept both alive)."""
    name = obj.name
    op()
    return bpy.data.objects[name]


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level


def imp(path, **kw):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def export(obj, path=OUT):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
        msg = obj.get("bz_last_export", "")
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def exported(obj, path=OUT):
    st, msg, data = export(obj, path)
    assert st == "FINISHED" and data is not None, (st, msg[:300])
    return data, msg


def read(data):
    with open("/tmp/bz_boner_bones_read.sgm", "wb") as f:
        f.write(data)
    return sgm_parser.read("/tmp/bz_boner_bones_read.sgm")


def validate_verdict(obj):
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def limb_index(obj, cls):
    return next(i for i, x in enumerate(store.limb_ids(obj)) if boner.class_of(x) == cls)


def refused(op, words, **kw):
    res, msg = call(op, **kw)
    assert res == {"CANCELLED"} and all(w in msg for w in words), (kw, msg)
    return msg


def last_edit(obj):
    return str(obj.get("bz_last_bone_edit", ""))


class _Rec:
    """Records UILayout calls (the panel tab runs headless on it)."""
    def __init__(self, log, name=""):
        object.__setattr__(self, "log", log)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, name):
        return _Rec(self.log, name)

    def __call__(self, *a, **kw):
        self.log.append((self._name, a, kw))
        return _Rec(self.log)

    def __setattr__(self, k, v):
        self.log.append(("set", k, v))


def tab_texts(obj):
    log = []
    ui.BZ_PT_boner.limbs_tab(_Rec(log), bpy.context, obj)
    return [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]


def rows_are_source(obj):
    return all(r.bones is None for r in store.limb_rows(obj))


def interesting(val):
    """Validate's lines that an edit could add: everything but the always-there setup / summary INFO lines."""
    return sorted(v for v in val if not v.startswith("COMBINE INFO"))


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    for idn in ("limb_select_bones", "limb_set_root", "limb_add_bone", "limb_remove_bone"):
        getattr(bpy.ops.bz, idn).get_rna_type()
    obj = imp(CHEETAH)
    s = bpy.context.scene.bz_boner
    s.active_limb = limb_index(obj, 0x40)
    texts = tab_texts(obj)
    t = limbops.limb_table(obj)
    head = s.active_limb
    assert f"Bones of Cheetah_Head: {len(t.bones[head])}" in texts, texts
    joined = " ".join(texts)
    assert all(b in joined for b in t.bones[head]), (t.bones[head], texts)
    assert {"Select Bones", "Set Root Bone", "Add Active Bone", "Remove Active Bone"} <= set(texts), texts
    assert any(x.startswith("98p 12b") for x in texts), texts            # the row's patch and bone counts
    s.bone = "nose"
    assert "nose: in Cheetah_Head" in tab_texts(obj)
    say("A REGISTER: 4 bone-list operators; the Limbs tab lists the Head's 12 bones and the four buttons; the active "
        "bone's limb is shown")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    assert exported(obj)[0] == raw
    s = bpy.context.scene.bz_boner
    names = store.limb_names(obj)
    torso, head, lf = limb_index(obj, 0x800), limb_index(obj, 0x40), limb_index(obj, 0x25)
    val0 = interesting(validate_verdict(obj))
    gen0 = obj[store.KEY_GEN]

    # Set Root Bone with each stock root: nothing changes (no journal generation either)
    for i, l in enumerate(src.limbs):
        if boner.is_torso(l.limb_id):
            continue
        inside = {b.lower() for b in l.bone_names}
        low = {b.name.lower(): k for k, b in enumerate(src.bones)}
        roots = [b for b in l.bone_names
                 if src.bones[low[b.lower()]].parent is None or src.bones[src.bones[low[b.lower()]].parent].name.lower()
                 not in inside]
        assert len(roots) == 1, (l.name, roots)
        res, msg = call(bpy.ops.bz.limb_set_root, index=i, root=roots[0])
        assert res == {"CANCELLED"} and not msg, (l.name, res, msg)
    assert rows_are_source(obj) and obj[store.KEY_GEN] == gen0 and exported(obj)[0] == raw

    # Select Limb Bones
    s.active_limb = lf
    s.bone = ""
    res, msg = call(bpy.ops.bz.limb_select_bones)
    assert res == {"FINISHED"}, msg
    arm = store.rig(obj)
    want = [limbops.rig_bone_name(obj, b) for b in src.limbs[lf].bone_names]
    assert sorted(limbops.selected_rig_bones(arm)) == sorted(want), (limbops.selected_rig_bones(arm), want)
    assert arm.data.bones.active.name == want[0] and s.bone == want[0], (arm.data.bones.active.name, s.bone)
    assert exported(obj)[0] == raw

    # re-rooting LFLeg below its standard root / at a host bone: refused, nothing journaled
    gen = obj[store.KEY_GEN]
    refused(bpy.ops.bz.limb_set_root, ("Set Root Bone refused", "would leave Front_leg1_l (the standard root of L Front "
                                       "Leg limbs) to 'Cheetah_Torso'"), index=lf, root="Front_leg2_l")
    refused(bpy.ops.bz.limb_set_root, ("out of the Torso", "hosts Cheetah_Head", "Index >= 0"), index=lf, root="spine3")
    assert obj[store.KEY_GEN] == gen and rows_are_source(obj)

    # Front_leg1_l to the Torso (Edit Mode: Undo / Redo): allowed as a single-bone edit, with the combine warnings
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="before move")
    s.active_limb = torso
    res, msg = call(bpy.ops.bz.limb_add_bone, bone="Front_leg1_l")
    assert res == {"FINISHED"}, msg
    assert last_edit(obj) == "moved 1 bone: Front_leg1_l ('Cheetah_LFLeg' -> 'Cheetah_Torso')", last_edit(obj)
    warns = list(obj.get("bz_last_bone_warnings", []))
    assert len(warns) == 2 and warns[0].startswith("Front_leg1_l owns 46 control points of 'Cheetah_LFLeg' and is now in "
                                                   "'Cheetah_Torso'") and "standard root" in warns[1], warns
    rows = store.limb_rows(obj)
    assert rows[lf].bones == tuple(b for b in src.limbs[lf].bone_names if b != "Front_leg1_l"), rows[lf]
    assert rows[torso].bones == tuple(src.limbs[torso].bone_names) + ("Front_leg1_l",), rows[torso]
    assert all(r.bones is None for j, r in enumerate(rows) if j not in (lf, torso))
    lf_torso, msg = exported(obj, "/tmp/bz_boner_b2_lf_torso.sgm")
    assert "bones 2 (Cheetah_Torso, Cheetah_LFLeg)" in msg, msg
    m = read(lf_torso)
    for j in (lf, torso):
        m.limbs[j].bone_names = list(src.limbs[j].bone_names)
    assert sgm_parser.write(m) == raw, "the move changed more than the two bone lists"
    val = interesting(validate_verdict(obj))                   # Combine Checks Off: the static precheck still runs
    added = sorted(set(val) - set(val0))
    assert len(added) == 4 and not set(val0) - set(val) and not core.blocking(val), added
    assert added[0].startswith("COMBINE ERROR precheck: limb 'Cheetah_LFLeg' (lfleg) uses control points of Front_leg1_l "
                               "(46)") and "dbBreak" in added[0], added
    assert added[1].startswith("COMBINE ERROR precheck: torso limb 'Cheetah_Torso' lists 'Front_leg1_l', the standard "
                               "root of 0x25 limbs"), added
    assert added[2].startswith("STOCK limb 'Cheetah_LFLeg': its patches use control points of Front_leg1_l (46 points, "
                               "in Cheetah_Torso)") and "fails an assertion" in added[2], added
    assert added[3].startswith("STOCK the Torso 'Cheetah_Torso' lists 'Front_leg1_l'"), added
    bpy.context.scene.bz_combine.validate_level = "QUICK"
    val = validate_verdict(obj)
    bpy.context.scene.bz_combine.validate_level = "OFF"
    errs = sorted({v for v in val if v.startswith("COMBINE ERROR")})
    say("B quick tier with Front_leg1_l in the Torso:", str(obj.get("bz_combine_summary", "")), errs)
    # the precheck's two, no combine crash, and one design: hornet's front-leg ring vertices are skinned to its
    # Front_leg1_l, which the grafted legs no longer bring (combotest's torso seam check: the root bone, as the 56
    # 'Assigning Vertex to the root bone' lines ComboTest.exe logs before its assertion on bull + these legs)
    torn = [e for e in errs if "torso seam vertices" in e]
    assert len(torn) == 1 and torn[0].startswith("COMBINE ERROR hornet torso + cheetah (all): warning: Hornet: torso seam "
                                                 "vertices (17 of bone 'Front_leg1_l')"), errs
    assert [e for e in errs if e not in torn] == added[:2] and not core.blocking(val), errs
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert rows_are_source(obj) and exported(obj)[0] == raw, "Undo of Add Active Bone"
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == lf_torso, "Redo of Add Active Bone"
    s.active_limb = lf
    res, msg = call(bpy.ops.bz.limb_add_bone, bone="Front_leg1_l")
    assert res == {"FINISHED"}, msg
    assert last_edit(obj) == "moved 1 bone: Front_leg1_l ('Cheetah_Torso' -> 'Cheetah_LFLeg')", last_edit(obj)
    assert list(obj.get("bz_last_bone_warnings", [])) == []
    assert rows_are_source(obj) and exported(obj)[0] == raw, "moved back is not the source"

    # move nose Head -> Torso (Add Active Bone on the Torso) and back
    s.bone = limbops.rig_bone_name(obj, "nose")
    s.active_limb = torso
    res, msg = call(bpy.ops.bz.limb_add_bone)
    assert res == {"FINISHED"}, msg
    assert last_edit(obj) == "moved 1 bone: nose ('Cheetah_Head' -> 'Cheetah_Torso')", last_edit(obj)
    moved, msg = exported(obj, "/tmp/bz_boner_b2_move.sgm")
    mm = read(moved)
    assert "nose" not in mm.limbs[head].bone_names and mm.limbs[torso].bone_names[-1] == "nose"
    val = interesting(validate_verdict(obj))
    added = sorted(set(val) - set(val0))
    assert len(added) == 2 and added[0].startswith("COMBINE WARN precheck: limb 'Cheetah_Head' (head) uses control points "
                                                   "of nose (218)"), added
    assert added[1].startswith("STOCK limb 'Cheetah_Head': its patches use control points of nose (218 points, in "
                               "Cheetah_Torso)"), added
    refused(bpy.ops.bz.limb_add_bone, ("already a bone of 'Cheetah_Torso'",))
    s.active_limb = head
    res, msg = call(bpy.ops.bz.limb_add_bone)
    assert res == {"FINISHED"}, msg
    assert rows_are_source(obj) and exported(obj)[0] == raw, "moved and back is not the source"

    # remove nose and re-add it
    bpy.ops.ed.undo_push(message="before remove")
    res, msg = call(bpy.ops.bz.limb_remove_bone, bone="nose")
    assert res == {"FINISHED"}, msg
    assert last_edit(obj) == "moved 1 bone: nose ('Cheetah_Head' -> no limb)", last_edit(obj)
    nose, msg = exported(obj, "/tmp/bz_boner_b2_nose.sgm")
    assert "bones 1 (Cheetah_Head)" in msg, msg
    m = read(nose)
    m.limbs[head].bone_names = list(src.limbs[head].bone_names)
    assert sgm_parser.write(m) == raw, "removing nose changed more than the Head's bone list"
    val = interesting(validate_verdict(obj))
    added = sorted(set(val) - set(val0))
    want = ("COMBINE WARN precheck: limb 'Cheetah_Head' (head) uses control points of nose (218)",
            "STOCK 1 bone is in no limb (nose): every base-game creature'", "STOCK limb 'Cheetah_Head': its patches use")
    assert len(added) == 3 and all(a.startswith(w) for a, w in zip(added, want)), added
    assert "nose (218 points, in no limb)" in added[2], added
    assert not set(val0) - set(val) and not core.blocking(val), core.blocking(val)
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert rows_are_source(obj) and exported(obj)[0] == raw, "Undo of Remove Active Bone"
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == nose, "Redo of Remove Active Bone"
    res, msg = call(bpy.ops.bz.limb_add_bone, bone="NOSE")          # file / rig names match ignoring case
    assert res == {"FINISHED"}, msg
    assert rows_are_source(obj) and exported(obj)[0] == raw, "removed and re-added is not the source"

    # refusals change nothing (and journal nothing)
    gen = obj[store.KEY_GEN]
    rt = limb_index(obj, 0x302)
    refused(bpy.ops.bz.limb_set_root, ("Set Root Bone refused", "is the Torso"), index=torso, root="pelvis")
    refused(bpy.ops.bz.limb_set_root, ("'Cheetah_RTWing' would have no bones left", "limbs are boneless!"), index=lf,
            root="top_wing_r")
    refused(bpy.ops.bz.limb_add_bone, ("'Cheetah_RSWing' would have no bones left",), index=head, bone="swing1_r")
    refused(bpy.ops.bz.limb_remove_bone, ("'Cheetah_RTWing' would have no bones left",), index=rt, bone="top_wing_r")
    refused(bpy.ops.bz.limb_remove_bone, ("not a bone of 'Cheetah_Head'",), index=head, bone="spine1")
    refused(bpy.ops.bz.limb_add_bone, ("not a bone of this creature's skeleton",), index=head, bone="no_such_bone")
    refused(bpy.ops.bz.limb_set_root, ("would leave neck2 (the standard root of Head limbs)",), index=head, root="head")
    refused(bpy.ops.bz.limb_add_bone, ("Add Active Bone 'neck1' would take neck1 (hosts Cheetah_Head", "Index >= 0"),
            index=head, bone="neck1")
    refused(bpy.ops.bz.limb_remove_bone, ("Remove Active Bone 'spine3' would take spine3", "Index >= 0"), index=torso,
            bone="spine3")
    s.bone = ""
    refused(bpy.ops.bz.limb_add_bone, ("pick a bone first",), index=head)
    assert obj[store.KEY_GEN] == gen and rows_are_source(obj) and store.limb_names(obj) == names
    bpy.ops.object.mode_set(mode="OBJECT")

    # a duplicated rig bone carries the original's import stamp: neither name is taken as the file's bone
    arm = store.rig(obj)
    rn = limbops.rig_bone_name(obj, "nose")
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    orig = arm.data.edit_bones[rn]
    eb = arm.data.edit_bones.new(rn + ".001")
    eb.head, eb.tail, eb.parent = orig.head.copy(), orig.tail.copy(), orig.parent
    eb["bz_src_index"] = int(orig["bz_src_index"])
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    assert limbops.stamp_clash(obj, rn + ".001") == [rn], limbops.stamp_clash(obj, rn + ".001")
    for name in (rn + ".001", rn):
        refused(bpy.ops.bz.limb_add_bone, ("copies of one imported bone", "delete the copy"), index=torso, bone=name)
    s.bone = rn + ".001"
    s.active_limb = torso
    assert f"'{rn}.001' shares its import stamp with {rn} (a duplicated bone: delete the copy)" in tab_texts(obj)
    s.bone = ""
    assert obj[store.KEY_GEN] == gen and rows_are_source(obj)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    arm.data.edit_bones.remove(arm.data.edit_bones[rn + ".001"])
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    assert exported(obj)[0] == raw
    say("B CHEETAH: Set Root Bone with every stock root changes nothing; Select Limb Bones = LFLeg's bones (first "
        "active); LFLeg at Front_leg2_l / spine3 refused; Front_leg1_l to the Torso (/tmp/bz_boner_b2_lf_torso.sgm) warns "
        "46 points + standard root, 2 STOCK lines + 2 COMBINE ERRORs, Undo identical / Redo moved, back identical; nose "
        "to the Torso (/tmp/bz_boner_b2_move.sgm) and back; nose removed (/tmp/bz_boner_b2_nose.sgm) and re-added "
        "identical; refusals (standard root, host bones, duplicated rig bone) journal nothing")


# ------------------------------------------------------------------------------------------------------------- C
def part_c():
    fresh(level="QUICK")
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath=NC_SRC, creature_name="BonerNC", detail=1,
                                                 clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    src_bytes = open(NC_SRC, "rb").read()
    src = sgm_parser.read(NC_SRC)
    assert exported(obj)[0] == src_bytes
    torso, lf, rf, head = (limb_index(obj, c) for c in (0x800, 0x25, 0x26, 0x40))
    assert src.limbs[lf].bone_names[-1] == "front_leg_root_l"
    val0 = validate_verdict(obj)
    assert not [v for v in val0 if v.startswith(("STOCK", "COMBINE ERROR", "WARN oe"))], val0

    for idx, side in ((lf, "l"), (rf, "r")):
        res, msg = call(bpy.ops.bz.limb_set_root, index=idx, root=f"front_leg1_{side}")
        assert res == {"FINISHED"}, msg
        assert last_edit(obj) == (f"moved 1 bone: front_leg_root_{side} ('{src.limbs[idx].name}' -> "
                                  f"'{src.limbs[torso].name}')"), last_edit(obj)
    data, msg = exported(obj, "/tmp/bz_boner_b2_reroot.sgm")
    m = read(data)
    assert m.limbs[torso].bone_names == list(src.limbs[torso].bone_names) + ["front_leg_root_l", "front_leg_root_r"]
    assert "front_leg_root_l" not in m.limbs[lf].bone_names and len(m.limbs[lf].bone_names) == 5
    val = validate_verdict(obj)
    bad = [v for v in val if v.startswith(("STOCK", "COMBINE ERROR", "WARN oe"))]
    assert not bad and not core.blocking(val), bad
    summary = str(obj.get("bz_combine_summary", ""))
    assert summary, "the Quick combine tier did not run"
    say(f"C quick tier on the re-rooted New Creature: {summary}")

    for idx, side in ((lf, "l"), (rf, "r")):
        res, msg = call(bpy.ops.bz.limb_set_root, index=idx, root=f"front_leg_root_{side}")
        assert res == {"FINISHED"}, msg
    assert rows_are_source(obj) and exported(obj)[0] == src_bytes, "re-rooted back is not the New Creature"

    bpy.context.scene.bz_combine.validate_level = "OFF"
    res, msg = call(bpy.ops.bz.limb_remove_bone, index=head, bone="upperMane")
    assert res == {"FINISHED"}, msg
    assert any(v.startswith("STOCK 1 bone is in no limb (upperMane)") for v in validate_verdict(obj))
    res, msg = call(bpy.ops.bz.limb_add_bone, index=head, bone="upperMane")
    assert res == {"FINISHED"}, msg
    assert rows_are_source(obj) and exported(obj)[0] == src_bytes, "upperMane removed and re-added is not the source"

    # a leaf root would leave front_leg1_l to the Torso: refused. A one-bone leg by removing the other bones instead
    refused(bpy.ops.bz.limb_set_root, ("would leave front_leg1_l",), index=lf, root="front_endfoot_l")
    others = [b for b in src.limbs[lf].bone_names if b != "front_leg_root_l"]
    for b in others:
        res, msg = call(bpy.ops.bz.limb_remove_bone, index=lf, bone=b)
        assert res == {"FINISHED"}, msg
    assert store.limb_rows(obj)[lf].bones == ("front_leg_root_l",), store.limb_rows(obj)[lf]
    bpy.context.scene.bz_combine.validate_level = "QUICK"
    val = validate_verdict(obj)
    bpy.context.scene.bz_combine.validate_level = "OFF"
    assert any(v.startswith("STOCK leg 'BonerNC_LFLeg' has patches but only one bone ('front_leg_root_l')")
               for v in val), [v for v in val if "STOCK" in v]
    assert any(v.startswith("STOCK limb 'BonerNC_LFLeg': its patches use control points of") for v in val), \
        [v for v in val if "STOCK" in v]
    assert any(v.startswith("COMBINE WARN precheck: leg 'BonerNC_LFLeg'") and "never a donor candidate" in v
               for v in val), [v for v in val if "COMBINE" in v]
    assert any(v.startswith("COMBINE ERROR precheck: limb 'BonerNC_LFLeg' (lfleg) uses control points of") for v in val), \
        [v for v in val if "COMBINE" in v]
    assert not core.blocking(val)
    for b in others:
        res, msg = call(bpy.ops.bz.limb_add_bone, index=lf, bone=b)
        assert res == {"FINISHED"}, msg
    assert rows_are_source(obj) and exported(obj)[0] == src_bytes
    say("C NEW CREATURE: LFLeg / RFLeg at front_leg1 send front_leg_root to the Torso (/tmp/bz_boner_b2_reroot.sgm): no "
        "STOCK / OE line, no COMBINE ERROR in the Quick tier; back identical; upperMane removed / re-added identical; "
        "Set Root at a leaf refused; a one-bone leg (bones removed) gives the STOCK donor + stray lines, the combiner's "
        "WARN + ERROR; re-added identical")


def main():
    blender_native.register()
    part_a()
    part_b()
    part_c()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_bones_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_bones_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
