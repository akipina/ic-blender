"""Boner > Limbs in headless Blender (increment B1): the limb list, Rename, the classification boxes, Add Standard Limb,
Delete (and its refusal), Assign Selected to Limb on new limbs, export, Validate, and Undo into a generation journaled
before the limb rows existed.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_limbs_test.py

A. registration: the five limb operators, scene.bz_boner.active_limb, the Limbs tab draws its rows and boxes;
B. New Creature: the list (13 limbs, class labels, stubs); Rename refuses a 64-character, a non-ASCII and a
   case-insensitive duplicate name, renames the head (export: only that name; Undo byte-identical, Redo renamed); LFArm
   Right / untick Arm take its connection along and back is identical; a class change no connection can follow is
   refused (untick Torso, untick Tail: the 'untick, then Delete' bypass), NoSwap on LFArm exports (/tmp/bz_boner_b1_noswap.sgm) and back is
   identical; Add Standard Limb offers exactly the four mid legs and refuses each (no mid_leg1 bone: never a boneless
   stub); a boneless LMLeg written through the store with two torso patches: status 'no bones', the panel alert, 'WARN
   export' + 'WARN oe' + one 'COMBINE ERROR precheck' line, nothing blocking (/tmp/bz_boner_b1_boneless.sgm: the OE
   loads it, ComboTest.exe must not take it); Delete sends the patches to the Torso (identical); Delete LFArm and the
   Torso are refused while connections use their class;
C. cheetah (stock): Rename Head exports only the name (/tmp/bz_boner_b1_rename.sgm), the combine precheck and the
   rulebook do not change (class-keyed); LTWing as Side takes its connection along (and back is identical); unticking
   Tail / Torso, deleting LFLeg are refused (connections), Add Standard RMLeg is refused (no mid_leg1_r);
D. a cheetah without arms (its LFArm / RFArm limbs and connections removed, written with ICParse): Validate reports the
   missing stub classes and the unlimbed front_arm1 bones; Add Standard LFArm (root front_arm1_l): the boxes are
   exclusive per group, a root another limb owns is refused, duplicate / not allowed shown, back to 0x15; torso patches
   on it turn the front_arms part button on (from the rows), Delete to the Torso / to Head is identical; a default name
   another limb has gets '_2'; Add Standard LFArm + RFArm makes stubs equal to cheetah's own arm records
   (/tmp/bz_boner_b1_arms.sgm) and both lines are gone;
E. the snail journaled before the limb rows: an Assign Limb generation and the import generation with no limb keys
   (stripped from the object, its journal and the session copy); a Rename upgrades and journals; Undo into each old
   generation keeps the limb names and exports what that state was (the import state byte-identical); Redo renames.
"""
import os
import sys
import traceback
import types
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops, ui, boner, limbops, structure  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

SNAIL = REPO + "/Assets/stock/snail.sgm"
CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
NC_SRC = "/tmp/bz_boner_limbs_nc_src.sgm"
NOARMS = "/tmp/bz_boner_noarms_src.sgm"
OUT = "/tmp/bz_boner_limbs_out.sgm"
_log = open("/tmp/bz_boner_limbs_log.txt", "w")


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


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"


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


def validate_verdict(obj):
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def rec(l):
    return (l.name, int(l.limb_id), sorted(int(x) for x in l.prims), list(l.bone_names))


def read(data):
    with open("/tmp/bz_boner_limbs_read.sgm", "wb") as f:
        f.write(data)
    return sgm_parser.read("/tmp/bz_boner_limbs_read.sgm")


def limb_index(obj, cls):
    return next(i for i, x in enumerate(store.limb_ids(obj)) if boner.class_of(x) == cls)


def select_patches(obj, pats):
    """Select the 4 corners of each patch (edit mode)."""
    want = {int(c) for p in pats for c in store.patches(obj)[p][[0, 3, 12, 15]]}
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in want
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


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


def tab(obj):
    log = []
    ui.BZ_PT_boner.limbs_tab(_Rec(log), bpy.context, obj)
    texts = [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]
    return log, texts


def flag(obj, idx, key):
    bit = next(f.bit for f in boner.LIMB_FLAGS if f.key == key)
    res, msg = call(bpy.ops.bz.limb_flag, index=idx, bit=bit)
    assert res == {"FINISHED"}, msg
    return store.limb_ids(obj)[idx]


def assign(obj, pats, limb):
    select_patches(obj, pats)
    res, msg = call(bpy.ops.bz.assign_limb, limb=str(limb))
    assert res == {"FINISHED"}, msg


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    for idn in ("limb_set_active", "rename_limb", "limb_flag", "add_standard_limb", "delete_limb"):
        getattr(bpy.ops.bz, idn).get_rna_type()
    s = bpy.context.scene.bz_boner
    assert s.active_limb == 0
    s.mode = "LIMBS"
    assert bpy.context.scene.bz_shade_mode == "LIMB"
    s.mode = "BONES"
    say("A REGISTER: 5 limb operators + scene.bz_boner.active_limb")


# ------------------------------------------------------------------------------------------------------------- B
BONELESS = "limbs are boneless!"


def refused(op, words, **kw):
    res, msg = call(op, **kw)
    assert res == {"CANCELLED"} and all(w in msg for w in words), (kw, msg)
    return msg


def part_b():
    fresh()
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath=NC_SRC, creature_name="BonerNC", detail=1,
                                                 clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    src_bytes = open(NC_SRC, "rb").read()
    src = sgm_parser.read(NC_SRC)
    assert exported(obj)[0] == src_bytes
    names0 = store.limb_names(obj)
    ids0 = store.limb_ids(obj)
    assert len(names0) == 13, names0
    s = bpy.context.scene.bz_boner
    log, texts = tab(obj)
    t = limbops.limb_table(obj)
    assert not t.error and t.status.count("ok") + t.status.count("stub") == 13, t.status
    for i, n in enumerate(names0):
        assert n in texts and boner.class_label(store.limb_ids(obj)[i]) in texts, (n, texts[:20])
    assert {"Rename", "Add Standard", "Delete", "Non-Swappable", "Left", "Mid 2", "Fin"} <= set(texts), texts
    assert "(Edit Mode: select and assign patches)" in texts
    assert not any("boneless" in x for x in texts), texts
    head = limb_index(obj, 0x40)
    arm = limb_index(obj, 0x15)
    torso = limb_index(obj, 0x800)
    assert call(bpy.ops.bz.limb_set_active, index=head)[0] == {"FINISHED"} and s.active_limb == head
    _log2, texts = tab(obj)
    assert f"{names0[head]}: 0x40 = Head" in texts, texts

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    _l, texts = tab(obj)
    assert "Select Limb" in texts and "Assign Selected to Limb" in texts, texts
    # Rename: refusals, then the head
    for bad, words in (("X" * 64, "at most 63"), ("Kopf_ü", "ASCII"), (names0[torso].upper(), "already called")):
        res, msg = call(bpy.ops.bz.rename_limb, index=head, name=bad)
        assert res == {"CANCELLED"} and words in msg, (bad, msg)
    assert store.limb_names(obj) == names0
    bpy.ops.ed.undo_push(message="before rename")
    res, msg = call(bpy.ops.bz.rename_limb, name="BonerNC_Noggin")            # the active limb
    assert res == {"FINISHED"}, msg
    assert store.limb_names(obj)[head] == "BonerNC_Noggin"
    data, msg = exported(obj)
    m = read(data)
    assert "limbs: renamed 1" in msg, msg
    m.limbs[head].name = names0[head]
    assert sgm_parser.write(m) == src_bytes, "Rename changed more than the limb name"
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert store.limb_names(obj) == names0 and exported(obj)[0] == src_bytes, "Undo of Rename"
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert store.limb_names(obj)[head] == "BonerNC_Noggin"
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert store.limb_names(obj) == names0

    # classification boxes on a connected limb (Boner Connections): the limb's only connection follows a class change in
    # the same step (LFArm Right / untick Arm, and back); a change no connection can follow is still refused -- the
    # Torso (every connection's parent), a class emptied (the 'untick, then Delete' bypass: Tail) -- and Non-Swappable
    # keeps the class
    bits = {f.key: f.bit for f in boner.LIMB_FLAGS}
    ca = next(i for i, x in enumerate(store.conn_flags(obj)[1]) if boner.class_of(x) == 0x15)
    for key, want in (("RIGHT", 0x16), ("LEFT", 0x15), ("ARM", 0x5), ("ARM", 0x15)):
        assert flag(obj, arm, key) == want
        assert boner.class_of(store.conn_flags(obj)[1][ca]) == want, (key, hex(store.conn_flags(obj)[1][ca]))
    assert exported(obj)[0] == src_bytes, "LFArm re-classed and back (with its connection) is not identical"
    refused(bpy.ops.bz.limb_flag, ("its parent", "delete or re-point its connection first"), index=torso,
            bit=bits["TORSO"])
    refused(bpy.ops.bz.limb_flag, ("attaches a limb of class 0x80", "not to no class"), index=limb_index(obj, 0x80),
            bit=bits["TAIL"])
    assert store.limb_ids(obj) == ids0
    bpy.ops.ed.undo_push(message="before flags")
    assert flag(obj, arm, "NOSWAP") == 0x800015
    data, msg = exported(obj, "/tmp/bz_boner_b1_noswap.sgm")
    assert read(data).limbs[arm].limb_id == 0x800015 and "class 1" in msg, msg
    assert flag(obj, arm, "NOSWAP") == 0x15 and exported(obj)[0] == src_bytes, "NoSwap ticked back is not identical"

    # Add Standard Limb: the menu offers exactly the missing classes; none has its root bone in this skeleton, so a
    # boneless stub is refused
    items = limbops._standard_items(None, bpy.context)
    assert [i[0] for i in items] == ["LMLeg", "RMLeg", "LM2Leg", "RM2Leg"], items
    assert all("not in this skeleton" in i[2] for i in items), items
    refused(bpy.ops.bz.add_standard_limb, ("no bone 'mid_leg1_l'", BONELESS), slot="LMLeg")
    assert store.limb_names(obj) == names0

    # a limb with no bones (an imported file, or a later bone edit, can hold one): shown, warned, a COMBINE ERROR;
    # exported for the engine tier (the OE loads it, ComboTest.exe must not take it); Delete sends its patches back
    store.begin_edit(obj)
    store.set_limb_rows(obj, store.limb_rows(obj) + [structure.LimbRow(-1, "BonerNC_LMLeg", 0x820021, ())])
    limbops.forget_table(obj)
    new = len(names0)
    s.active_limb = new
    assert limbops.limb_table(obj).status[new] == "no bones"
    _l, texts = tab(obj)
    assert any("boneless" in x for x in texts), texts
    pl = store.patch_limb(obj)
    tp = [int(x) for x in np.nonzero(pl == torso)[0][:2]]
    assign(obj, tp, new)
    data, msg = exported(obj, "/tmp/bz_boner_b1_boneless.sgm")
    m = read(data)
    assert rec(m.limbs[-1]) == ("BonerNC_LMLeg", 0x820021, sorted(tp), []), rec(m.limbs[-1])
    assert set(m.limbs[torso].prims) == set(src.limbs[torso].prims) - set(tp)
    assert "added 1 (BonerNC_LMLeg)" in msg and "patches 1" in msg, msg
    val = validate_verdict(obj)
    assert "WARN oe: There are no bones in limb 'BonerNC_LMLeg'" in val, [v for v in val if "oe:" in v]
    assert any(v.startswith("WARN export: limb BonerNC_LMLeg has no bones") and BONELESS in v for v in val), val
    cerr = [v for v in val if v.startswith("COMBINE ERROR precheck: limb 'BonerNC_LMLeg'")]
    bone_err = [v for v in cerr if "has no bones" in v]
    assert len(bone_err) == 1 and BONELESS in bone_err[0], [v for v in val if "COMBINE" in v]
    # the added limb also has no connection and no Right partner: the two ways ComboTest.exe refuses a creature
    assert sorted(v.split(": ", 2)[1].split(" (class")[0] + (" conn" if "m_pLimbConn" in v else " mirror")
                  for v in cerr if v not in bone_err) == ["limb 'BonerNC_LMLeg' conn", "limb 'BonerNC_LMLeg' mirror"], cerr
    assert not core.blocking(val), core.blocking(val)
    bpy.ops.object.mode_set(mode="EDIT")
    res, msg = call(bpy.ops.bz.delete_limb, index=new)
    assert res == {"FINISHED"}, msg
    assert exported(obj)[0] == src_bytes, "Delete (patches to the Torso) is not the source"

    # refusals: a connection attaches its class / is cut into it
    for idx, words in ((arm, "connection 'BonerNC_LFArm' attaches a limb of this class"), (torso, "its parent")):
        refused(bpy.ops.bz.delete_limb, (words, "delete or re-point its connection first"), index=idx)
    assert store.limb_names(obj) == names0
    bpy.ops.object.mode_set(mode="OBJECT")
    say("B NEW CREATURE: 13 rows; Rename refusals + Head renamed (only its name; Undo identical, Redo renamed); class "
        "LFArm re-classed with its connection and back identical, Torso / Tail untick refused, NoSwap export and back "
        "identical; Add Standard offers the 4 mid legs and refuses each (no root bone: no boneless stub); a boneless "
        "LMLeg + 2 torso patches: status, alert, WARN export + WARN oe + COMBINE ERROR, not blocking "
        "(/tmp/bz_boner_b1_boneless.sgm), Delete identical; Delete LFArm and Torso refused")


# ------------------------------------------------------------------------------------------------------------- C
def precheck(obj):
    return sorted(v for v in validate_verdict(obj) if v.startswith(("COMBINE ERROR", "COMBINE WARN", "STOCK", "ERROR")))


def part_c():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    assert exported(obj)[0] == raw
    names0 = store.limb_names(obj)
    ids0 = store.limb_ids(obj)
    before = precheck(obj)
    head = limb_index(obj, 0x40)
    res, msg = call(bpy.ops.bz.rename_limb, index=head, name="Cheetah_Noggin")
    assert res == {"FINISHED"}, msg
    data, msg = exported(obj, "/tmp/bz_boner_b1_rename.sgm")
    m = read(data)
    m.limbs[head].name = names0[head]
    assert sgm_parser.write(m) == raw
    after = precheck(obj)
    assert [a.replace("Cheetah_Noggin", "Cheetah_Head") for a in after] == before, (before, after)
    assert not [p for p in ICval.check_creature_rulebook(read(data), include_structural=False) if p.startswith("ERROR")]
    assert call(bpy.ops.bz.rename_limb, index=head, name=names0[head])[0] == {"FINISHED"}
    assert exported(obj)[0] == raw

    # every stock limb class has a connection: re-classing one takes its connection along (Boner Connections), unticking
    # it to no class (the Delete bypass) or the Torso is refused
    bits = {f.key: f.bit for f in boner.LIMB_FLAGS}
    lt = limb_index(obj, 0x301)
    assert flag(obj, lt, "SIDE") == 0x501 and "Cheetah_LTWing" in str(obj.get("bz_last_conn_follow", ""))
    assert flag(obj, lt, "TOP") == 0x301 and exported(obj)[0] == raw
    for key in ("TAIL", "TORSO"):
        refused(bpy.ops.bz.limb_flag, ("delete or re-point its connection first",),
                index=limb_index(obj, 0x80 if key == "TAIL" else 0x800), bit=bits[key])
    assert store.limb_ids(obj) == ids0 and exported(obj)[0] == raw
    refused(bpy.ops.bz.delete_limb, ("Cheetah_LFLeg", "delete or re-point its connection first"),
            index=limb_index(obj, 0x25))
    refused(bpy.ops.bz.add_standard_limb, ("no bone 'mid_leg1_r'", BONELESS), slot="RMLeg")
    assert store.limb_names(obj) == names0 and exported(obj)[0] == raw
    say("C CHEETAH: Rename Head exports only the name (/tmp/bz_boner_b1_rename.sgm), precheck / rulebook unchanged; "
        "LTWing as Side with its connection and back, untick Tail / Torso refused (connections); LFLeg delete refused; RMLeg (no mid_leg1_r) refused")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    m = sgm_parser.read(CHEETAH)
    arms = [l for l in m.limbs if boner.class_of(l.limb_id) in (0x15, 0x16)]
    m.limbs = [l for l in m.limbs if l not in arms]
    m.limb_connections = [c for c in m.limb_connections if boner.class_of(c.flags2) not in (0x15, 0x16)]
    with open(NOARMS, "wb") as f:
        f.write(sgm_parser.write(m))
    raw = open(NOARMS, "rb").read()
    fresh()
    obj = imp(NOARMS)
    assert exported(obj)[0] == raw
    val = validate_verdict(obj)
    miss = [v for v in val if "Required limb class not found (can be a stub limb): 0x1" in v]
    assert len(miss) == 2, [v for v in val if "oe:" in v]
    assert any(v.startswith("STOCK 2 bones are in no limb (front_arm1_l, front_arm1_r)") for v in val), \
        [v for v in val if v.startswith("STOCK")]
    items = [i[0] for i in limbops._standard_items(None, bpy.context)]
    assert items[:2] == ["LFArm", "RFArm"], items
    names0 = store.limb_names(obj)
    new = len(names0)
    s = bpy.context.scene.bz_boner

    # an arm with its root bone and no connection: the boxes follow Boner's groups
    assert call(bpy.ops.bz.add_standard_limb, slot="LFArm")[0] == {"FINISHED"}
    assert store.limb_rows(obj)[new] == structure.LimbRow(-1, "Cheetah_LFArm", 0x15, ("front_arm1_l",))
    assert s.active_limb == new and limbops.limb_table(obj).status[new] == "stub"
    assert flag(obj, new, "RIGHT") == 0x16                 # Left cleared
    refused(bpy.ops.bz.add_standard_limb, ("bone 'front_arm1_l'", "already belongs to limb 'Cheetah_LFArm'", BONELESS),
            slot="LFArm")                                  # offered again (no 0x15 limb), but its root is taken
    assert flag(obj, new, "REAR") == 0x1A                  # Front cleared
    assert flag(obj, new, "LEG") == 0x2A                   # Arm cleared: the RRLeg class, whose limb keeps it
    assert limbops.limb_table(obj).status[new] == "duplicate"
    val = validate_verdict(obj)
    assert "WARN oe: Limb 'Cheetah_LFArm' has same class as limb 'Cheetah_RRLeg'" in val, [v for v in val if "oe:" in v]
    assert not core.blocking(val), core.blocking(val)
    assert flag(obj, new, "NOSWAP") == 0x80002A and flag(obj, new, "NOSWAP") == 0x2A     # NoSwap stands alone
    assert flag(obj, new, "LEG") == 0x0A                   # a second click unticks
    assert limbops.limb_table(obj).status[new] == "not allowed"      # R Rear: no body type
    for key in ("ARM", "FRONT", "LEFT"):
        flag(obj, new, key)
    assert store.limb_ids(obj)[new] == 0x15

    # its patches, the part buttons (from the rows, not the source's records), Delete to the Torso / to a chosen limb
    sc = bpy.context.scene.bz_combine
    sc.role, sc.other_kind, sc.other_stock_name = "DONOR", "STOCK", "cheetah"
    from blender_native import combopreview
    assert combopreview.part_availability(bpy.context.scene, obj, sc)["front_arms"] is False
    bpy.ops.object.mode_set(mode="EDIT")
    torso, headi = limb_index(obj, 0x800), limb_index(obj, 0x40)
    tp = [int(x) for x in np.nonzero(store.patch_limb(obj) == torso)[0][:2]]
    assign(obj, tp, new)
    assert exported(obj)[0] != raw
    avail = combopreview.part_availability(bpy.context.scene, obj, sc)
    assert avail["front_arms"] is True, avail
    res, msg = call(bpy.ops.bz.delete_limb, index=new)
    assert res == {"FINISHED"}, msg
    assert exported(obj)[0] == raw, "Add + patches + Delete (to the Torso) is not the source"
    assert combopreview.part_availability(bpy.context.scene, obj, sc)["front_arms"] is False
    assert call(bpy.ops.bz.add_standard_limb, slot="LFArm")[0] == {"FINISHED"}
    hp = [int(x) for x in np.nonzero(store.patch_limb(obj) == headi)[0][:3]]
    assign(obj, hp, new)
    res, msg = call(bpy.ops.bz.delete_limb, index=new, target=str(headi))
    assert res == {"FINISHED"}, msg
    assert exported(obj)[0] == raw, "Delete (patches to Head) is not the source"

    # a default name another limb already has gets a suffix
    tail = limb_index(obj, 0x80)
    assert call(bpy.ops.bz.rename_limb, index=tail, name="Cheetah_RFArm")[0] == {"FINISHED"}
    assert call(bpy.ops.bz.add_standard_limb, slot="RFArm")[0] == {"FINISHED"}
    assert store.limb_rows(obj)[new] == structure.LimbRow(-1, "Cheetah_RFArm_2", 0x16, ("front_arm1_r",))
    assert call(bpy.ops.bz.delete_limb, index=new)[0] == {"FINISHED"}
    assert call(bpy.ops.bz.rename_limb, index=tail, name="Cheetah_Tail")[0] == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    assert exported(obj)[0] == raw

    for slot in ("LFArm", "RFArm"):
        res, msg = call(bpy.ops.bz.add_standard_limb, slot=slot)
        assert res == {"FINISHED"}, msg
    t = limbops.limb_table(obj)
    assert t.status[-2:] == ["stub", "stub"], t.status
    data, msg = exported(obj, "/tmp/bz_boner_b1_arms.sgm")
    out = read(data)
    assert [rec(l) for l in out.limbs[-2:]] == [rec(l) for l in arms], ([rec(l) for l in out.limbs[-2:]],
                                                                        [rec(l) for l in arms])
    assert [rec(l) for l in out.limbs[:-2]] == [rec(l) for l in m.limbs]
    val = validate_verdict(obj)
    assert not [v for v in val if "Required limb class not found (can be a stub limb): 0x1" in v]
    assert not [v for v in val if "in no limb" in v] and not core.blocking(val), (core.blocking(val), val[:5])
    say("D NO ARMS: Validate names the missing arm classes and the unlimbed front_arm1 bones; Add Standard LFArm (root "
        "front_arm1_l): boxes exclusive per group, a taken root refused, duplicate / not allowed shown, back to 0x15; "
        "torso patches enable the front_arms part button, Delete (to Torso / to Head) identical; a taken default name "
        "gets _2; LFArm + RFArm = cheetah's own stub records (/tmp/bz_boner_b1_arms.sgm), both lines gone")


# ------------------------------------------------------------------------------------------------------------- E
def strip_limb_keys(obj):
    """Make the object look journaled by the add-on before the limb rows: no limb identity / bone tables on it, and
    no limb key in any journal entry or session copy (the old add-on wrote names and ids directly, never journaled)."""
    for k in (store.KEY_LIMB_SRC, store.KEY_LIMB_BONES, store.KEY_LIMB_BONE_COUNTS):
        if k in obj:
            del obj[k]
    j = obj[store.KEY_JOURNAL]
    for g in list(j.keys()):
        for k in store.LIMB_TABLE_KEYS:
            if k in j[g]:
                del j[g][k]
    me = store._identity(obj)
    for (ident, _g), entry in store._JOURNAL_CACHE.items():
        if ident == me:
            for k in store.LIMB_TABLE_KEYS:
                entry.pop(k, None)


def part_e():
    fresh()
    obj = imp(SNAIL)
    raw = open(SNAIL, "rb").read()
    names0 = store.limb_names(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    head, torso = limb_index(obj, 0x40), limb_index(obj, 0x800)
    g0 = obj[store.KEY_GEN]
    tp = [int(np.nonzero(store.patch_limb(obj) == torso)[0][0])]
    bpy.ops.ed.undo_push(message="select")
    assign(obj, tp, head)
    g1 = obj[store.KEY_GEN]
    assigned = exported(obj)[0]
    assert assigned != raw
    strip_limb_keys(obj)
    assert all(k not in obj[store.KEY_JOURNAL][str(g)] for g in (g0, g1) for k in store.LIMB_TABLE_KEYS)
    assert store.limb_rows(obj) == structure.source_rows(sgm_parser.read(SNAIL)), "read-only rows before the upgrade"

    bpy.ops.ed.undo_push(message="before rename")
    res, msg = call(bpy.ops.bz.rename_limb, index=head, name="Snail_Noggin")
    assert res == {"FINISHED"}, msg
    j = obj[store.KEY_JOURNAL]
    assert all(k in j[str(g)] for g in (g0, g1) for k in store.LIMB_TABLE_KEYS), "the old generations were not backfilled"
    renamed = exported(obj)[0]
    assert read(renamed).limbs[head].name == "Snail_Noggin"

    obj = _history(bpy.ops.ed.undo, obj)                                    # the rename
    assert obj[store.KEY_GEN] == g1, (obj[store.KEY_GEN], g1)
    assert store.limb_names(obj) == names0, store.limb_names(obj)
    assert exported(obj)[0] == assigned, "Undo into the pre-upgrade Assign generation"
    steps = 0
    while obj[store.KEY_GEN] != g0 and steps < 4:
        obj = _history(bpy.ops.ed.undo, obj); steps += 1
    assert obj[store.KEY_GEN] == g0 and store.limb_names(obj) == names0
    assert exported(obj)[0] == raw, "Undo into the pre-upgrade import generation is not the source"
    while store.limb_names(obj)[head] != "Snail_Noggin" and steps < 10:
        obj = _history(bpy.ops.ed.redo, obj); steps += 1
    assert store.limb_names(obj)[head] == "Snail_Noggin" and exported(obj)[0] == renamed
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"E PRE-UPGRADE UNDO: generations {g0} (import) and {g1} (Assign) without limb keys backfilled by the Rename; "
        "Undo keeps the limb names (Assign state, then the source byte-identical); Redo renamed")


def main():
    blender_native.register()
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_limbs_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_limbs_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
