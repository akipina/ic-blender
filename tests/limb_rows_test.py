"""Boner > Limbs without Blender (increment B1): limb rows, the class-keyed rulebook, the OE limb check, the Boner
checkbox rules and the limb-table migration.
    python blender_native/tests/limb_rows_test.py

Needs the base-game creatures (Assets/engine_stock) and the add-on stock (Assets/stock).
1. identity: every stock creature is byte-identical through structure.apply_limbs with its source rows (bones "the
   source's") and with explicit bone lists; an export model never holds a parsed-source limb object and the source is
   never changed;
2. edits on the snail / cheetah through core.build_export: rename changes only that LIMB name, a class toggle only its
   id, an added stub row appends exactly Limb(name, id, [], [root]), a deleted stub row drops only its record, a deleted
   patched row gives the prototype's records (patches to the torso), a reordered table reads back in row order, the grown
   path applies the rows too; unwritable rows (repeated / unknown source, new without bones, a patch in a row that does
   not exist) raise; export_summary names each change by identity;
3. the rulebook (ICParse.validate.check_creature_rulebook) and core.check_structure key on CLASS: renaming every limb
   and connection changes nothing on the 61 base-game creatures; re-classing the head is reported as missing;
4. ICParse.validate.check_oe_structure equals an independent re-implementation of the Object Editor's limb check
   (FUN_00421569, from the wf11 oecheck oracle) message for message on both corpora, clean counts pinned;
5. Boner's rules: the checkbox table and its exclusive groups, class labels, the 17 standard classes = the OE's, the
   limb-name rule, the delete refusal and default patch target, the row status;
6. the store: an object journaled before the limb rows (two generations without limb keys) is upgraded on its first
   sync; a Rename journals a new generation; Undo into each pre-upgrade generation keeps the limb table (and exports
   byte-identical), Redo brings the rename back, a memfile-restored pre-upgrade object finds the rename in the session
   copy; without the backfill the same Undo drops the limb names (the guard is not vacuous).
"""
import copy
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import boner  # noqa: E402
import store  # noqa: E402
import structure  # noqa: E402
from structure import LimbRow  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

ENGINE = sorted(glob.glob(os.path.join(REPO, "Assets", "engine_stock", "*.sgm")))
ADDON = sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
SNAIL = os.path.join(REPO, "Assets", "stock", "snail.sgm")
CHEETAH = os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
EAGLE = os.path.join(REPO, "Assets", "engine_stock", "eagle.sgm")
HORSE = os.path.join(REPO, "Assets", "engine_stock", "horse.sgm")
TEST_FILES = {"ChickenTest2_oneSided.sgm", "ChickenTest2.sgm", "ChickenTest.sgm", "FuckyFish.sgm", "Squid6.sgm",
              "Squid7.sgm", "Squid8.sgm", "salamander_compat.sgm"}


def say(*a):
    print(*a, flush=True)


def creatures(paths):
    for p in paths:
        raw = open(p, "rb").read()
        m = SP.read(p)
        if core.is_scene_model(m) or not getattr(m, "patches", None) or SP.write(m) != raw:
            continue
        yield p, raw, m


def rec(l):
    return (l.name, int(l.limb_id), sorted(int(x) for x in l.prims), list(l.bone_names))


# ------------------------------------------------------------------------------------------------------------ 1
def identity():
    n = 0
    for p, raw, src in creatures(ENGINE + ADDON):
        for rows in (structure.source_rows(src),
                     [LimbRow(i, l.name, int(l.limb_id), tuple(l.bone_names)) for i, l in enumerate(src.limbs)]):
            m = SP.read(p)
            copies = list(m.limbs)
            warns = structure.apply_limbs(m, src, rows, None)
            assert SP.write(m) == raw, os.path.basename(p)
            assert all(a is b for a, b in zip(m.limbs, copies)), "an unchanged row did not keep the copy's record"
            assert not any(l is s for l in m.limbs for s in src.limbs), "an export limb is the parsed source's object"
            orphans = len(src.patches) - len({int(q) for l in src.limbs for q in l.prims})
            assert bool(warns) == bool(orphans), (os.path.basename(p), warns)
        # rename + rename back through the rows is the source again
        rows = structure.source_rows(src)
        m = SP.read(p)
        structure.apply_limbs(m, src, [LimbRow(r.src, r.name + "X", r.limb_id, None) for r in rows], None)
        assert SP.write(m) != raw and SP.write(src) == raw, "a renamed build changed the source"
        n += 1
    assert n >= 110, n
    say(f"1 IDENTITY: {n} stock creatures byte-identical through source rows and explicit bone lists; no export limb "
        "is a source object; rename changes the bytes and leaves the source alone")


# ------------------------------------------------------------------------------------------------------------ 2
def state(path, **kw):
    d = core.load_creature(path)
    return core.EditState(source_path=path, verts=d.verts, **kw)


def build(path, rows, pl=None, **kw):
    b = core.build_export(state(path, limbs=rows, patch_limb=pl, **kw))
    return b, SP.write(b.model)


def edits():
    raw = open(SNAIL, "rb").read()
    src = SP.read(SNAIL)
    rows = structure.source_rows(src)
    pl0 = core._patch_limb_of(src)
    head = next(i for i, l in enumerate(src.limbs) if l.limb_id & boner.CLASS_MASK == 0x40)
    torso = next(i for i, l in enumerate(src.limbs) if l.limb_id == 0x800)
    tail = next(i for i, l in enumerate(src.limbs) if l.limb_id == 0x80)
    b, out = build(SNAIL, rows, pl0)
    assert out == raw and core.export_summary(b).parts == []

    # rename: only that LIMB name
    r2 = list(rows); r2[head] = LimbRow(head, "Snail_Noggin", rows[head].limb_id, None)
    b, out = build(SNAIL, r2, pl0)
    m2 = SP.read(write_tmp(out))
    assert [rec(l) for l in m2.limbs] == [rec(l) if i != head else ("Snail_Noggin",) + rec(l)[1:]
                                         for i, l in enumerate(src.limbs)]
    m2.limbs[head].name = src.limbs[head].name
    assert SP.write(m2) == raw, "rename changed more than the limb name"
    parts = core.export_summary(b).parts
    assert parts == ["limbs: renamed 1 (Snail_Head -> Snail_Noggin)"], parts
    assert SP.write(b.source) == raw and SP.write(core.read_source(SNAIL)) == raw

    # class toggle: NoSwap on the arms stub
    arm = next(i for i, l in enumerate(src.limbs) if l.limb_id == 0x15)
    r3 = list(rows); r3[arm] = LimbRow(arm, rows[arm].name, boner.toggle_flag(rows[arm].limb_id, boner.NOSWAP), None)
    b, out = build(SNAIL, r3, pl0)
    m3 = SP.read(write_tmp(out))
    assert m3.limbs[arm].limb_id == 0x800015 and [rec(l) for i, l in enumerate(m3.limbs) if i != arm] == \
        [rec(l) for i, l in enumerate(src.limbs) if i != arm]
    m3.limbs[arm].limb_id = 0x15
    assert SP.write(m3) == raw
    assert core.export_summary(b).parts == ["limbs: class 1 (Snail_LFArm 0x15 -> 0x800015)"], core.export_summary(b).parts

    # an added stub row appends exactly that record
    r4 = rows + [LimbRow(-1, "Snail_LMLeg", 0x820021, ("mid_leg1_l",))]
    b, out = build(SNAIL, r4, pl0)
    m4 = SP.read(write_tmp(out))
    assert len(m4.limbs) == len(src.limbs) + 1 and rec(m4.limbs[-1]) == ("Snail_LMLeg", 0x820021, [], ["mid_leg1_l"])
    m4.limbs.pop()
    assert SP.write(m4) == raw
    assert core.export_summary(b).parts == ["limbs: added 1 (Snail_LMLeg)"], core.export_summary(b).parts
    # ... and assigning it two torso patches moves exactly those prims
    tp = [int(x) for x in np.nonzero(pl0 == torso)[0][:2]]
    pl4 = pl0.copy(); pl4[tp] = len(rows)
    b, out = build(SNAIL, r4, pl4)
    m4 = SP.read(write_tmp(out))
    assert sorted(m4.limbs[-1].prims) == sorted(tp) and set(m4.limbs[torso].prims) == set(src.limbs[torso].prims) - set(tp)

    # delete a patched limb after moving its patches to the torso: the prototype's records
    pl5 = np.where(pl0 == tail, torso, pl0)
    pl5 = np.where(pl5 > tail, pl5 - 1, pl5)
    r5 = [r for i, r in enumerate(rows) if i != tail]
    b, out = build(SNAIL, r5, pl5)
    proto = SP.read(SNAIL)
    proto.limbs[torso].prims = sorted(set(proto.limbs[torso].prims) | set(proto.limbs[tail].prims))
    del proto.limbs[tail]
    assert [rec(l) for l in b.model.limbs] == [rec(l) for l in proto.limbs]
    assert out == SP.write(proto), "deleting the tail limb is not the prototype's LIMB result"
    parts = core.export_summary(b).parts
    assert parts == ["limbs: patches 1 (Snail_Torso); deleted 1 (Snail_Tail)"], parts
    # a deleted stub (no patches) drops only its record; re-inserting it at its place is the source again
    r6 = [r for i, r in enumerate(rows) if i != arm]
    pl6 = np.where(pl0 > arm, pl0 - 1, pl0)
    b, out = build(SNAIL, r6, pl6)
    m6 = SP.read(write_tmp(out))
    assert [rec(l) for l in m6.limbs] == [rec(l) for i, l in enumerate(src.limbs) if i != arm]
    assert build(SNAIL, r6[:arm] + [rows[arm]] + r6[arm:], pl0)[1] == raw
    # a reordered table: records in row order, membership follows the rows
    order = list(range(len(rows)))[::-1]
    r7 = [rows[i] for i in order]
    inv = {o: k for k, o in enumerate(order)}
    pl7 = np.array([inv[int(x)] for x in pl0])
    b, out = build(SNAIL, r7, pl7)
    assert [rec(l) for l in b.model.limbs] == [rec(src.limbs[i]) for i in order]
    assert "order changed" in core.export_summary(b).parts[0]

    # unwritable rows
    for bad_rows, bad_pl, words in ((rows + [rows[0]], pl0, "both come from source limb 0"),
                                    ([LimbRow(99, "x", 1, None)] + rows[1:], pl0, "the source has"),
                                    (rows + [LimbRow(-1, "Snail_New", 0x15, None)], pl0, "has no bone list"),
                                    (rows, np.where(np.arange(len(pl0)) == 0, len(rows), pl0), "but the creature has")):
        try:
            build(SNAIL, bad_rows, bad_pl)
        except ValueError as e:
            assert words in str(e), (words, str(e))
        else:
            raise AssertionError(f"not refused: {words}")
    # patches in no limb: written, but the summary warns
    pl8 = pl0.copy(); pl8[0] = -1
    b, _out = build(SNAIL, rows, pl8)
    assert any("1 patch is in no limb" in w for w in core.export_summary(b).warnings), core.export_summary(b).warnings
    # a limb with no bones: written (the OE loads it), but the summary warns that ComboTest.exe won't take it
    b, _out = build(SNAIL, rows + [LimbRow(-1, "Snail_LMLeg", 0x820021, ())], pl0)
    assert any(w.startswith("limb Snail_LMLeg has no bones") and "limbs are boneless!" in w
               for w in core.export_summary(b).warnings), core.export_summary(b).warnings

    # the grown path applies the rows too (one patch appended: a duplicate of patch 0, in the renamed head)
    d = core.load_creature(SNAIL)
    g = core.EditState(source_path=SNAIL, verts=d.verts, patches=np.vstack([d.patches, d.patches[:1]]),
                       vert_owner=np.array(R.vertex_bone_owners(src)), patch_uv=np.vstack([d.patch_uv, d.patch_uv[:1]]),
                       patch_material=np.append(d.patch_mat, d.patch_mat[0]),
                       patch_type=np.array([p.type for p in src.patches] + [src.patches[0].type]),
                       patch_limb=np.append(pl0, head), limbs=r2)
    bg = core.build_export(g)
    assert bg.grown and bg.model.limbs[head].name == "Snail_Noggin"
    assert len(src.patches) in bg.model.limbs[head].prims
    assert any(p.startswith("limbs: renamed 1 (Snail_Head -> Snail_Noggin)") for p in core.export_summary(bg).parts)
    say("2 EDITS: rename / class / added stub (+ patches) / deleted patched limb (= prototype) / deleted stub / "
        "reorder / grown path; 4 unwritable-row refusals; summary names each change")


_TMP = [0]


def write_tmp(data):
    _TMP[0] += 1
    path = f"/tmp/bz_limb_rows_{_TMP[0]}.sgm"
    with open(path, "wb") as f:
        f.write(data)
    return path


# ------------------------------------------------------------------------------------------------------------ 3
def class_keyed():
    n = 0
    for p, raw, m in creatures(ENGINE):
        before = ICval.check_creature_rulebook(m, include_structural=False)
        pl = core._patch_limb_of(m)
        conns = [(c.name, c.bone_name, list(c.vertex_indices)) for c in m.limb_connections]
        bn = [b.name for b in m.bones]
        cs_before = core.check_structure([l.name for l in m.limbs], pl, conns, bn, [l.limb_id for l in m.limbs])
        for i, l in enumerate(m.limbs):
            l.name = f"Part{i}"
        for i, c in enumerate(m.limb_connections):
            c.name = f"Socket{i}"
        assert ICval.check_creature_rulebook(m, include_structural=False) == before, os.path.basename(p)
        conns2 = [(c.name, c.bone_name, list(c.vertex_indices)) for c in m.limb_connections]
        cs = core.check_structure([l.name for l in m.limbs], pl, conns2, bn, [l.limb_id for l in m.limbs])
        assert [x.replace("Socket", "") for x in cs] == [x for x in cs_before], (cs, cs_before)
        n += 1
    assert n == 61, n
    m = SP.read(CHEETAH)
    head = next(l for l in m.limbs if l.limb_id == 0x40)
    head.name = "Cheetah_Torso_but_really_head"
    assert not [x for x in ICval.check_creature_rulebook(m, include_structural=False) if x.startswith("ERROR")]
    head.limb_id = 0
    msgs = ICval.check_creature_rulebook(m, include_structural=False)
    assert "ERROR limb 'head' missing (no limb of class 0x40)" in msgs, msgs
    cs = core.check_structure([l.name for l in m.limbs], core._patch_limb_of(m), [], [], [l.limb_id for l in m.limbs])
    assert cs == ["WARN no Head limb (class 0x40): creatures need Torso/Head/Tail to combine"], cs
    say(f"3 CLASS-KEYED: renaming every limb and connection changes neither the rulebook nor check_structure on {n} "
        "base-game creatures; a head re-classed to 0 is 'missing' whatever its name")


# ------------------------------------------------------------------------------------------------------------ 4
def oe_oracle(m):
    """The limb half of wf11 oecheck.py (FUN_00421569), independent of ICParse: message kinds in order."""
    M = 0xFF7FFFFF
    REQ, REQS, OTHER = [0x800, 0x40, 0x80], [0x25, 0x26, 0x29, 0x2A, 0x15, 0x16, 0x301, 0x302, 0x501, 0x502], \
        [0x20021, 0x20022, 0x40021, 0x40022]
    out = []
    L = m.limbs
    bl = {b.name.lower() for b in m.bones}
    for i, l in enumerate(L):
        cls = l.limb_id & M
        if cls == 0:
            out.append(("no classification", l.name))
        if not l.bone_names:
            out.append(("no bones", l.name))
        for b in l.bone_names:
            if b.lower() not in bl:
                out.append(("bone not found", b))
        for j in range(i):
            if L[j].limb_id & M == cls:
                out.append(("same class", l.name))
    for c in REQ:
        f = [l for l in L if l.limb_id & M == c]
        if not f:
            out.append(("required", c))
        elif not f[0].prims:
            out.append(("stub", f[0].name))
    for c in REQS:
        if not any(l.limb_id & M == c for l in L):
            out.append(("required stub ok", c))
    for l in L:
        if l.limb_id & M not in REQ + REQS + OTHER:
            out.append(("disallowed", l.limb_id & M))
    return out


def _kind(line):
    t = line[len("WARN oe: "):]
    for key, words in (("no classification", "has no classification"), ("no bones", "There are no bones"),
                       ("bone not found", "was not found in skeleton"), ("same class", "has same class"),
                       ("stub", "must not be a stub"), ("required stub ok", "(can be a stub limb)"),
                       ("required", "Required limb class not found:"), ("disallowed", "disallowed class")):
        if words in t:
            return key
    raise AssertionError(line)


def oe_check():
    clean = {}
    for corpus, paths in (("E", ENGINE), ("T", ADDON)):
        k = n = 0
        for p, raw, m in creatures(paths):
            if corpus == "T" and os.path.basename(p) in TEST_FILES:
                continue
            got = [x for x in ICval.check_oe_structure(m) if "connection '" not in x]    # the connection half: conn_rows_test
            want = oe_oracle(m)
            assert [_kind(x) for x in got] == [w[0] for w in want], (os.path.basename(p), got, want)
            for x, w in zip(got, want):
                if w[0] == "stub":                     # the OE's own text: "Limb must not be a stub limb: %s"
                    assert x == f"WARN oe: Limb must not be a stub limb: {w[1]}", (x, w)
                else:
                    assert (f"'{w[1]}'" in x) if isinstance(w[1], str) else (f"0x{w[1]:x}" in x), (x, w)
            n += 1
            k += not got
        clean[corpus] = (k, n)
    # the OE limb check alone: E birds (5 no front legs) + dragonfly/eagle/hornet top wings + horse antlers = 8 creatures
    assert clean["E"] == (53, 61), clean
    assert clean["T"][1] >= 48 and clean["T"][1] - clean["T"][0] <= 5, clean
    eagle = ICval.check_oe_structure(SP.read(EAGLE))
    assert eagle == ["WARN oe: Required limb class not found (can be a stub limb): 0x25 (Left Front Leg)",
                     "WARN oe: Required limb class not found (can be a stub limb): 0x26 (Right Front Leg)",
                     "WARN oe: Required limb class not found (can be a stub limb): 0x301 (Left Top Wing)",
                     "WARN oe: Required limb class not found (can be a stub limb): 0x302 (Right Top Wing)"], eagle
    horse = ICval.check_oe_structure(SP.read(HORSE))
    assert horse == ["WARN oe: Limb 'Horse_LAntler1' has a disallowed class: 0x10001 (Left)",
                     "WARN oe: Limb 'Horse_RAntler1' has a disallowed class: 0x10002 (Right)"], horse
    # the other messages, on an edited cheetah
    m = SP.read(CHEETAH)
    m.limbs[1].limb_id = 0
    m.limbs[3].limb_id = 0x26
    m.limbs[4].bone_names = []
    m.limbs[5].bone_names = ["no_such_bone"]
    m.limbs[2].prims = []
    got = [x for x in ICval.check_oe_structure(m) if "connection '" not in x]
    for words in ("Limb 'Cheetah_Head' has no classification", "There are no bones in limb 'Cheetah_RFLeg'",
                  "Bone 'no_such_bone' referenced by limb 'Cheetah_LRLeg' was not found in skeleton",
                  "Limb 'Cheetah_RFLeg' has same class as limb 'Cheetah_LFLeg'",
                  "Limb must not be a stub limb: Cheetah_Tail",
                  "Required limb class not found: 0x40 (Head)",
                  "Required limb class not found (can be a stub limb): 0x25 (Left Front Leg)",
                  "Limb 'Cheetah_Head' has a disallowed class: 0x0"):
        assert f"WARN oe: {words}" in got, (words, got)
    assert all(not core.blocking([x]) for x in got)
    # every bone in one limb: scorpion is the base-game exception, a deleted limb's bones are reported
    part = {os.path.basename(p): ICval.check_limb_bones(m) for p, raw, m in creatures(ENGINE)}
    assert [k for k, v in part.items() if v] == ["scorpion.sgm"], [k for k, v in part.items() if v]
    m = SP.read(CHEETAH)
    del m.limbs[1]
    assert ICval.check_limb_bones(m)[0].startswith("STOCK 12 bones are in no limb ("), ICval.check_limb_bones(m)
    say(f"4 OE LIMB CHECK: = the oracle message for message; clean E {clean['E'][0]}/{clean['E'][1]}, "
        f"T {clean['T'][0]}/{clean['T'][1]}; every message kind produced on an edited cheetah; bone partition "
        "(scorpion only)")


# ------------------------------------------------------------------------------------------------------------ 5
def rules():
    bits = {f.key: f.bit for f in boner.LIMB_FLAGS}
    assert bits == {"LEFT": 0x1, "RIGHT": 0x2, "FRONT": 0x4, "REAR": 0x8, "ARM": 0x10, "LEG": 0x20, "HEAD": 0x40,
                    "TAIL": 0x80, "WING": 0x100, "TOP": 0x200, "SIDE": 0x400, "TORSO": 0x800, "FIN": 0x8000,
                    "MID": 0x20000, "MID2": 0x40000, "NOSWAP": 0x800000}, bits
    groups = {}
    for f in boner.LIMB_FLAGS:
        groups.setdefault(f.group, set()).add(f.key)
    assert groups == {"SIDE": {"LEFT", "RIGHT"}, "POSITION": {"FRONT", "REAR", "MID", "MID2"}, "PLANE": {"TOP", "SIDE"},
                      "TYPE": {"TORSO", "HEAD", "TAIL", "ARM", "LEG", "WING", "FIN"}, "": {"NOSWAP"}}, groups
    # Boner's set / clear rows: ticking a box clears exactly the rest of its group
    for f in boner.LIMB_FLAGS:
        for g in boner.LIMB_FLAGS:
            v = boner.toggle_flag(boner.toggle_flag(0, g.bit), f.bit)
            if f.key == g.key:
                assert v == 0, (f.key, hex(v))                      # a second click unticks
            elif f.group and f.group == g.group:
                assert v == f.bit, (g.key, f.key, hex(v))
            else:
                assert v == f.bit | g.bit, (g.key, f.key, hex(v))
    assert boner.toggle_flag(0x25, bits["REAR"]) == 0x29 and boner.toggle_flag(0x25, bits["RIGHT"]) == 0x26
    assert boner.toggle_flag(0x820021, bits["MID2"]) == 0x840021
    assert boner.toggle_flag(0x10001, bits["HEAD"]) == 0x10041, "a bit no checkbox owns was not kept"
    try:
        boner.toggle_flag(0, 0x10000)
    except ValueError:
        pass
    else:
        raise AssertionError("0x10000 is no checkbox")
    for v, label in ((0x800, "Torso"), (0x25, "L Front Leg"), (0x502, "R Side Wing"), (0x301, "L Top Wing"),
                     (0x840021, "L Mid 2 Leg · Non-Swappable"), (0, "no class"), (0x10001, "L 0x10000"),
                     (0x800000, "no class · Non-Swappable")):
        assert boner.class_label(v) == label, (hex(v), boner.class_label(v))
    std = {boner.class_of(s.limb_id) for s in boner.STANDARD_LIMBS}
    assert std == set(ICval.OE_CLASSES_REQUIRED) | set(ICval.OE_CLASSES_STUB_OK) | set(ICval.OE_CLASSES_OPTIONAL)
    assert len(boner.STANDARD_LIMBS) == 17
    for s in boner.STANDARD_LIMBS:                     # the suffix names its class the way stock does
        want = ICval.RULEBOOK_LIMB_IDS[s.suffix.lower()]
        assert boner.class_of(s.limb_id) == want, s
    e = SP.read(EAGLE)
    assert [s.suffix for s in boner.missing_standard([l.limb_id for l in e.limbs])] == \
        ["LFLeg", "RFLeg", "LTWing", "RTWing", "LMLeg", "RMLeg", "LM2Leg", "RM2Leg"]
    assert boner.default_limb_name([l.name for l in e.limbs], "Eagle", "LFLeg") == "Eagle_LFLeg"
    assert boner.default_limb_name(["Eagle_Torso", "Eagle_Head", "Raven_Tail"], "Raven", "LFArm") == "Eagle_LFArm"
    assert boner.default_limb_name([], "Blob", "Torso") == "Blob_Torso"
    assert boner.default_limb_name(["Eagle_Torso", "eagle_lfleg", "Eagle_LFLeg_2"], "Eagle", "LFLeg") == "Eagle_LFLeg_3"
    E = boner.limb_name_error
    assert E("A" * 63, []) is None and "at most 63" in E("A" * 64, [])
    assert "ASCII" in E("Kopf_ü", []) and "a limb needs a name" in E("  ", []) and "space" in E(" x", [])
    assert "already called 'Snail_Head'" in E("snail_HEAD", ["Snail_Head"]) and E("Snail_Head2", ["Snail_Head"]) is None
    ids = [l.limb_id for l in SP.read(CHEETAH).limbs]
    cm = SP.read(CHEETAH).limb_connections
    cn, f1, f2 = [c.name for c in cm], [c.flags1 for c in cm], [c.flags2 for c in cm]
    arm = ids.index(0x15)
    assert "connection 'Cheetah_LFArm' attaches a limb of this class" in boner.delete_limb_refusal(arm, ids, cn, f1, f2)
    assert "delete or re-point its connection first" in boner.delete_limb_refusal(arm, ids, cn, f1, f2)
    assert "its parent" in boner.delete_limb_refusal(0, ids, cn, f1, f2)            # the torso: every ring is cut into it
    ids2 = ids + [0x820021]
    assert boner.delete_limb_refusal(len(ids), ids2, cn, f1, f2) is None
    # another limb keeping the class keeps the connection resolved: that delete is allowed (either of the two)
    dup = ids + [0x800025]
    assert boner.delete_limb_refusal(len(ids), dup, cn, f1, f2) is None
    assert boner.delete_limb_refusal(ids.index(0x25), dup, cn, f1, f2) is None
    # a class change may not orphan a connection either (the two-step 'untick, then Delete' bypass)
    C = boner.class_change_refusal
    lfleg = ids.index(0x25)
    for bit in (0x20, 0x1, 0x4):                                                      # untick Leg / Left / Front
        why = C(lfleg, ids, boner.toggle_flag(ids[lfleg], bit), cn, f1, f2)
        assert "connection 'Cheetah_LFLeg' attaches a limb of class 0x25 (L Front Leg)" in why and \
            "delete or re-point its connection first" in why, why
    assert "its parent" in C(0, ids, 0, cn, f1, f2)                                   # untick Torso
    assert C(lfleg, ids, 0x800025, cn, f1, f2) is None                                # Non-Swappable: same class
    assert C(lfleg, dup, 0x5, cn, f1, f2) is None and C(len(ids), ids2, 0x840021, cn, f1, f2) is None
    assert C(1, ids, 0x41, cn, f1, f2) and "(and" not in C(1, ids, 0x41, cn, f1, f2)
    assert boner.orphaned_connections(ids, [x for x in ids if x != 0x800], cn, f1, f2)[0] == ("Cheetah_Head", "parent",
                                                                                              0x800)
    assert len(boner.orphaned_connections(ids, [x for x in ids if x != 0x800], cn, f1, f2)) == 12
    # Add Standard Limb never makes a boneless stub
    lf = boner.STANDARD_BY_SUFFIX["LFLeg"]
    assert boner.standard_root_refusal(lf, "front_leg1_l", None) is None
    assert "no bone 'front_leg1_l'" in boner.standard_root_refusal(lf, None, None)
    assert "already belongs to limb 'Eagle_LFArm'" in boner.standard_root_refusal(lf, "Front_Leg1_L", "Eagle_LFArm")
    assert all("limbs are boneless!" in boner.standard_root_refusal(lf, *a) for a in ((None, None), ("x", "y")))
    assert boner.default_patch_target(len(ids), ids2) == 0 and boner.default_patch_target(0, [0x800, 0x40]) == 1
    assert boner.limb_status(1, ids, 98, 12) == "ok" and boner.limb_status(arm, ids, 0, 1) == "stub"
    assert boner.limb_status(1, ids, 0, 12) == "needs patches" and boner.limb_status(len(ids), ids2, 0, 0) == "no bones"
    assert boner.limb_status(3, ids[:3] + [0x26] + ids[4:], 33, 7) == "duplicate"
    assert boner.limb_status(0, [0], 5, 1) == "no class" and boner.limb_status(0, [0x8200], 5, 1) == "not allowed"
    assert boner.limb_status(1, ids, 0, 0) == "no bones" and boner.limb_status(0, [0], 5, 0) == "no bones"   # worst first
    say("5 RULES: checkbox table and groups (every ordered pair), class labels, 17 standard classes = the OE's, "
        "names (63 bytes, ASCII, unique ignoring case; unique default names), delete / class-change refusals (connection "
        "classes never orphaned; a duplicate keeps them), no boneless standard stub, default target, status words")


# ------------------------------------------------------------------------------------------------------------ 6
class FakeObj(dict):
    """The ID-property side of a native object, with its per-point generation stamp (store's point attributes are
    patched to read ``attrs``)."""
    def __init__(self, n_points):
        super().__init__()
        self.name, self.mode, self.type, self.n = "fake", "OBJECT", "MESH", n_points
        self.attrs = {}


def migration():
    real = (store.point_ints, store.set_point_ints, store.point_count)
    store.point_ints = lambda obj, name: obj.attrs.get(name)
    store.set_point_ints = lambda obj, name, vals: obj.attrs.__setitem__(name, np.asarray(vals, dtype=np.int64).reshape(-1)
                                                                          if np.ndim(vals) else np.full(obj.n, vals))
    store.point_count = lambda obj: obj.n
    try:
        _migration()
    finally:
        store.point_ints, store.set_point_ints, store.point_count = real
        store.forget_journal_cache()


def _pre_upgrade_object(src, pl_a, pl_b):
    """An object as the previous add-on left it: limb names / ids set directly (not journaled), two generations whose
    journal entries (and session copies) have no limb keys, the mesh at the second."""
    obj = FakeObj(len(src.vertices))
    obj[store.MARK] = True
    obj[store.KEY_UID] = "fake-uid"
    obj[store.KEY_SOURCE] = SNAIL
    obj[store.KEY_NPATCH] = len(src.patches)
    obj[store.KEY_POOL_N] = len(src.vertices)
    obj[store.KEY_LIMB_NAMES] = [l.name for l in src.limbs]
    obj[store.KEY_LIMB_IDS] = [store._i32(l.limb_id) for l in src.limbs]
    g0, g1 = 1001, 1002
    obj[store.KEY_JOURNAL] = {str(g0): {store.KEY_PATCH_LIMB: list(pl_a), store.KEY_NPATCH: len(src.patches)},
                              str(g1): {store.KEY_PATCH_LIMB: list(pl_b), store.KEY_NPATCH: len(src.patches)}}
    for g in (g0, g1):
        store._cache_put(obj, g, obj[store.KEY_JOURNAL][str(g)])
    obj[store.KEY_PATCH_LIMB] = list(pl_b)
    obj[store.KEY_GEN] = g1
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
    return obj, g0, g1


def _export(obj):
    b = core.build_export(state(SNAIL, patch_limb=store.patch_limb(obj), limbs=store.limb_rows(obj)))
    return SP.write(b.model)


def _migration():
    raw = open(SNAIL, "rb").read()
    src = SP.read(SNAIL)
    pl_a = core._patch_limb_of(src)
    torso = [l.limb_id for l in src.limbs].index(0x800)
    head = [l.limb_id & boner.CLASS_MASK for l in src.limbs].index(0x40)
    pl_b = pl_a.copy()
    pl_b[int(np.nonzero(pl_a == torso)[0][0])] = head                   # an old-code Assign Limb
    names0 = [l.name for l in src.limbs]

    # without the backfill, an Undo into a pre-upgrade generation after a Rename drops the limb names
    store.forget_journal_cache()
    obj, g0, g1 = _pre_upgrade_object(src, pl_a, pl_b)
    real_upgrade = store.upgrade_limb_tables
    store.upgrade_limb_tables = lambda o: False
    try:
        obj[store.KEY_LIMB_SRC] = list(range(len(names0)))                # what a naive first write would do
        obj[store.KEY_LIMB_BONE_COUNTS] = [-1] * len(names0)
        obj[store.KEY_LIMB_BONES] = []
        store.begin_edit(obj)
        obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
        assert store.sync(obj) is None
        assert store.KEY_LIMB_NAMES not in obj, "the unguarded sync did not drop the names: the guard would be vacuous"
    finally:
        store.upgrade_limb_tables = real_upgrade

    store.forget_journal_cache()
    obj, g0, g1 = _pre_upgrade_object(src, pl_a, pl_b)
    pre = copy.deepcopy(dict(obj))
    assert store.limb_rows(obj) == structure.source_rows(src), "read-only rows of a pre-upgrade object"
    # a Rename (what bz.rename_limb does): begin_edit syncs (the upgrade backfills), then the whole table is written
    g2 = store.begin_edit(obj)
    j = obj[store.KEY_JOURNAL]
    for g in (g0, g1):
        assert all(k in j[str(g)] for k in store.LIMB_TABLE_KEYS), f"generation {g} was not backfilled"
        assert j[str(g)][store.KEY_LIMB_NAMES] == names0 and j[str(g)][store.KEY_LIMB_SRC] == list(range(len(names0)))
        assert all(k in store._cache_get(obj, g) for k in store.LIMB_TABLE_KEYS)
    rows = store.limb_rows(obj)
    rows[head] = LimbRow(rows[head].src, "Snail_Noggin", rows[head].limb_id, None)
    store.set_limb_rows(obj, rows)
    renamed = _export(obj)
    assert renamed != raw and SP.read(write_tmp(renamed)).limbs[head].name == "Snail_Noggin"

    # Undo into g1 (pre-upgrade, after the old Assign), then g0 (import), Redo to g2
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
    assert store.sync(obj) is None
    assert store.limb_names(obj) == names0 and np.array_equal(store.patch_limb(obj), pl_b)
    at_g1 = _export(obj)
    m1 = SP.read(write_tmp(at_g1))
    assert [l.name for l in m1.limbs] == names0 and int(pl_b[int(np.nonzero(pl_a == torso)[0][0])]) == head
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g0)
    assert store.sync(obj) is None
    assert store.limb_names(obj) == names0 and np.array_equal(store.patch_limb(obj), pl_a)
    assert _export(obj) == raw, "Undo into the pre-upgrade import generation is not the source"
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g2)
    assert store.sync(obj) is None and store.limb_names(obj)[head] == "Snail_Noggin" and _export(obj) == renamed

    # a memfile Undo restores the pre-upgrade object (no limb keys anywhere on it); the edit mesh comes back at g2
    obj.clear()
    obj.update(copy.deepcopy(pre))
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g2)
    assert store.sync(obj) is None
    assert store.limb_names(obj)[head] == "Snail_Noggin" and _export(obj) == renamed, "the session copy lost the rename"
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g0)
    assert store.sync(obj) is None and _export(obj) == raw
    # an inconsistent table is refused, not guessed
    obj[store.KEY_LIMB_BONE_COUNTS] = [2] + list(obj[store.KEY_LIMB_BONE_COUNTS])[1:]
    assert "re-import" in store.limb_store_error(obj)
    say("6 MIGRATION: pre-upgrade journal (2 generations, no limb keys) backfilled on the first sync; Undo into each "
        "keeps the names (g0 byte-identical), Redo renames; memfile-restored object finds the rename; the unguarded "
        "sync drops the names")


if __name__ == "__main__":
    identity()
    edits()
    class_keyed()
    oe_check()
    rules()
    migration()
    print("ALL PASS")
