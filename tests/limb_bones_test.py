"""Boner > Limbs, bone lists, without Blender (increment B2): which bones swap with each limb.
    python blender_native/tests/limb_bones_test.py

Needs the base-game creatures (Assets/engine_stock) and the add-on stock (Assets/stock).
1. the rule reproduces the stock lists: Set Root Bone with each non-torso limb's own root changes nothing on 727 of the
   728 base-game limbs (scorpion's Head sweeps in its two unlimbed antlers, reported as from no limb) and on every
   add-on limb; the torso remainder is the Torso's list on 60/61 (scorpion) and every add-on creature; merge_order of an
   equal set gives the source list on every limb; rows written from those lists are byte-identical;
2. the known exceptions: horse's antler bones and piranha's arm stubs belong to their own limbs, so the Head's rule
   leaves them out; with those limbs deleted the horse Head sweeps the antlers in (reported), piranha's Head never
   takes the arm stubs (other classes' standard roots) and a stub root is never swept into a leg;
3. edits on cheetah: Set Root Bone refuses to leave a standard limb root in the Torso (LFLeg at Front_leg2_l, the Head
   at head, LFArm at jaw1: ComboTest.exe then exports a torso with two front_leg1_l bones) and to take a torso
   connection's host bone out of the Torso (LFLeg at spine3: ComboTest.exe fails 'Index >= 0'); without the host check a
   root at spine3 takes spine3 + neck1 and nothing another limb owns; refusals (the Torso, an unknown root, another
   limb's only bone); Add / Remove Active Bone: a bone moved between limbs and back, removed and re-added, is the source
   list again (source order kept); new bones append in skeleton order; refusals (already listed, another limb's only
   bone, a limb's only bone, a bone it does not list, a host bone leaving the Torso); the edit warnings
   (boner.bone_edit_warnings): Front_leg1_l moved to the Torso names its 46 LFLeg points and the Torso's standard root,
   nose removed names its 218 Head points, putting them back warns nothing;
3b. a root another non-torso limb owns comes with that limb's bones below it (the other limb loses a whole branch and
   stays one subtree), on a synthetic skeleton;
4. export bytes through structure.apply_limbs: removing nose from the Head changes only that LIMB record; moved and back
   is byte-identical;
5. the STOCK lines (ICParse.validate.check_limb_bones): scorpion only on the base game, no add-on creature (test
   files aside); each line on an edited cheetah (no limb, two limbs, two branches, a one-bone leg with patches -- not
   when Non-Swappable or a stub --, a stub bone owning points, a limb's points owned by a Torso / unlimbed bone, a
   Torso listing a standard root, a torso connection host outside the Torso); none blocks; ICParse's standard roots are
   boner.STANDARD_LIMBS'.
"""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import boner  # noqa: E402
import structure  # noqa: E402
from structure import LimbRow  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

ENGINE = sorted(glob.glob(os.path.join(REPO, "Assets", "engine_stock", "*.sgm")))
ADDON = sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
CHEETAH = os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
HORSE = os.path.join(REPO, "Assets", "engine_stock", "horse.sgm")
PIRANHA = os.path.join(REPO, "Assets", "engine_stock", "piranha.sgm")
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


class Sk:
    """A creature's skeleton and limb table as the operators see them."""
    def __init__(self, m):
        self.m = m
        self.names = [b.name for b in m.bones]
        self.parents = [-1 if b.parent is None else int(b.parent) for b in m.bones]
        self.lists = [tuple(l.bone_names) for l in m.limbs]
        self.ids = [int(l.limb_id) for l in m.limbs]
        self.ln = [l.name for l in m.limbs]
        self.hosts = boner.torso_connection_hosts([(c.name, c.bone_name, c.flags1) for c in m.limb_connections])
        self.limb_hosts = ICval.limb_connection_hosts(self.ids, [(c.name, c.bone_name, c.flags2)
                                                                  for c in m.limb_connections])
        self.owners = ICval.limb_point_owners(m)

    def limb(self, cls):
        return next(i for i, x in enumerate(self.ids) if boner.class_of(x) == cls)

    def root(self, i):
        s = {b.lower() for b in self.lists[i]}
        idx = [boner.bone_lookup(self.names, b) for b in self.lists[i]]
        return [self.names[k] for k in sorted(idx) if self.parents[k] < 0 or self.names[self.parents[k]].lower() not in s]

    def set_root(self, i, root, lists=None, hosts=True):
        lists = self.lists if lists is None else lists
        return boner.set_root_edit(i, root, self.names, self.parents, lists, self.ids, self.ln, self.lists,
                                   self.hosts if hosts else None)

    def add(self, i, bone, lists):
        return boner.add_bone_edit(i, bone, self.names, lists, self.ids, self.ln, self.lists, self.hosts)

    def remove(self, i, bone, lists):
        return boner.remove_bone_edit(i, bone, lists, self.ln, self.ids, self.hosts)

    def warnings(self, edit, before):
        return boner.bone_edit_warnings(edit, before, self.ids, self.ln, self.owners, self.limb_hosts)


def refused(fn, words):
    try:
        fn()
    except ValueError as e:
        assert all(w in str(e) for w in words), (words, str(e))
        return str(e)
    raise AssertionError(f"not refused: {words}")


# ------------------------------------------------------------------------------------------------------------ 1
def corpus():
    counts = {}
    for corpus, paths in (("E", ENGINE), ("T", ADDON)):
        limbs = same = torsos = torso_ok = order = n_order = 0
        odd = []
        for p, raw, m in creatures(paths):
            sk = Sk(m)
            for i, l in enumerate(m.limbs):
                if not l.bone_names:
                    continue
                n_order += 1
                order += boner.merge_order(l.bone_names, set(l.bone_names), sk.names, l.bone_names) == tuple(l.bone_names)
                if boner.is_torso(l.limb_id):
                    torsos += 1
                    rem = boner.torso_remainder(sk.names, sk.lists, sk.ids)
                    if {b.lower() for b in rem} == {b.lower() for b in l.bone_names}:
                        torso_ok += 1
                    else:
                        odd.append((os.path.basename(p), "torso", sorted({b.lower() for b in rem} ^
                                                                         {b.lower() for b in l.bone_names})))
                    continue
                limbs += 1
                roots = sk.root(i)
                assert len(roots) == 1, (p, l.name, roots)
                e = sk.set_root(i, roots[0])
                if not e.changed and not e.moved:
                    same += 1
                else:
                    odd.append((os.path.basename(p), l.name, e.moved))
            if corpus == "E":
                # the rows the operators would write (explicit lists) are byte-identical
                m2 = SP.read(p)
                rows = [LimbRow(i, l.name, int(l.limb_id), tuple(sk.lists[i])) for i, l in enumerate(m.limbs)]
                structure.apply_limbs(m2, m, rows, None)
                assert SP.write(m2) == raw, p
        counts[corpus] = (same, limbs, torso_ok, torsos, order, n_order)
        if corpus == "E":
            assert (same, limbs) == (727, 728) and (torso_ok, torsos) == (60, 61) and order == n_order == 789, counts
            odd = sorted(odd, key=lambda o: o[1])
            sc = Sk(SP.read(os.path.join(REPO, "Assets", "engine_stock", "scorpion.sgm")))
            h = sc.limb(0x40)
            assert odd == [("scorpion.sgm", "Scorpion_Head", [("antler_l", -1, h), ("Antler_R", -1, h)]),
                           ("scorpion.sgm", "torso", ["antler_l", "antler_r"])], odd
        else:
            assert same == limbs and limbs >= 650 and torso_ok == torsos >= 50 and order == n_order, (counts, odd[:3])
    say(f"1 CORPUS: Set Root Bone with the own root changes nothing on E {counts['E'][0]}/{counts['E'][1]} (scorpion Head "
        f"sweeps its unlimbed antlers) and T {counts['T'][0]}/{counts['T'][1]} limbs; torso remainder E "
        f"{counts['E'][2]}/{counts['E'][3]}, T {counts['T'][2]}/{counts['T'][3]}; merge_order keeps E "
        f"{counts['E'][4]}/{counts['E'][5]}, T {counts['T'][4]}/{counts['T'][5]} source lists; rows byte-identical (E)")


# ------------------------------------------------------------------------------------------------------------ 2
def exceptions():
    # horse: antler_l / antler_r are their own (disallowed-class) limbs: the Head's rule leaves them out
    sk = Sk(SP.read(HORSE))
    head = sk.limb(0x40)
    assert not sk.set_root(head, "neck2").changed
    keep = [i for i, x in enumerate(sk.ids) if boner.class_of(x) not in (0x10001, 0x10002)]
    lists = [sk.lists[i] for i in keep]
    sk2 = Sk(SP.read(HORSE))
    sk2.lists, sk2.ids, sk2.ln = lists, [sk.ids[i] for i in keep], [sk.ln[i] for i in keep]
    e = sk2.set_root(sk2.limb(0x40), "neck2")
    h2 = sk2.limb(0x40)
    assert e.moved == [("antler_l", -1, h2), ("antler_r", -1, h2)], e.moved
    assert e.lists[h2] == sk.lists[head] + ("antler_l", "antler_r"), e.lists[h2]      # appended in skeleton order
    assert e.describe(sk2.ln) == "moved 2 bones: antler_l, antler_r (no limb -> 'Horse_Head')", e.describe(sk2.ln)

    # piranha: front_arm1_l / _r hang under neck3 (inside the Head's subtree) and are the arm stubs
    sk = Sk(SP.read(PIRANHA))
    head = sk.limb(0x40)
    assert not sk.set_root(head, "neck2").changed
    keep = [i for i, x in enumerate(sk.ids) if boner.class_of(x) not in (0x15, 0x16)]
    sk.lists, sk.ids, sk.ln = [sk.lists[i] for i in keep], [sk.ids[i] for i in keep], [sk.ln[i] for i in keep]
    h = sk.limb(0x40)
    e = boner.set_root_edit(h, "neck2", sk.names, sk.parents, sk.lists, sk.ids, sk.ln)
    assert not e.changed and not e.moved, e.moved                  # never sweeps another class's standard root
    assert "front_arm1_l" in boner.standard_root_skips(0x40) and "front_arm1_l" not in boner.standard_root_skips(0x15)
    # ... but an arm limb takes its own root, and a Head that already lists it keeps it
    lists = list(sk.lists) + [("neck3",)]
    ids, ln = list(sk.ids) + [0x15], list(sk.ln) + ["Piranha_LFArm"]
    e = boner.set_root_edit(len(lists) - 1, "front_arm1_l", sk.names, sk.parents, lists, ids, ln)
    assert e.lists[-1] == ("front_arm1_l",) and ("front_arm1_l", -1, len(lists) - 1) in e.moved, e.moved
    lists2 = list(sk.lists)
    lists2[h] = lists2[h] + ("front_arm1_l",)
    e = boner.set_root_edit(h, "neck2", sk.names, sk.parents, lists2, sk.ids, sk.ln)
    assert "front_arm1_l" in e.lists[h] and "front_arm1_r" not in e.lists[h], e.lists[h]
    say("2 EXCEPTIONS: horse antler / piranha arm limbs keep their bones out of the Head; without them the horse Head "
        "sweeps the antlers in (reported), piranha's Head never takes the arm stubs (another class's standard root)")


# ------------------------------------------------------------------------------------------------------------ 3
def edits():
    sk = Sk(SP.read(CHEETAH))
    lf, torso, head = sk.limb(0x25), sk.limb(0x800), sk.limb(0x40)
    assert sk.root(lf) == ["Front_leg1_l"]
    # a standard limb root is never left to the Torso (wf14: ComboTest.exe's skeleton gets two front_leg1_l bones and
    # the grafted leg lands 0.21 m off; as a donor the leg's Front_leg1_l points go on the root bone and it asserts)
    refused(lambda: sk.set_root(lf, "Front_leg2_l"), ("would leave Front_leg1_l (the standard root of L Front Leg limbs) to "
                                                      "'Cheetah_Torso'", "two bones of that name"))
    refused(lambda: sk.set_root(head, "head"), ("would leave neck2 (the standard root of Head limbs)",))
    refused(lambda: sk.set_root(sk.limb(0x15), "jaw1"), ("would leave front_arm1_l",))
    refused(lambda: sk.set_root(sk.limb(0x80), "tail2"), ("would leave tail1 (the standard root of Tail limbs)",))
    # a torso connection's host bone never leaves the Torso (ComboTest.exe 'Index >= 0' with this creature as the torso)
    msg = refused(lambda: sk.set_root(lf, "spine3"), ("out of the Torso", "Index >= 0"))
    assert "neck1 (hosts Cheetah_Head, Cheetah_LFArm, Cheetah_RFArm)" in msg and "spine3 (hosts Cheetah_LFLeg" in msg, msg
    # ... and without the host check the sweep rule alone: the Torso is the remainder (taken from), other limbs'
    # branches and stubs are not
    e = sk.set_root(lf, "spine3", hosts=False)
    assert sorted(e.moved) == sorted([("spine3", torso, lf), ("neck1", torso, lf)]), e.moved
    assert e.lists[lf] == sk.lists[lf] + ("spine3", "neck1"), e.lists[lf]
    assert all(e.lists[j] == sk.lists[j] for j in range(len(sk.lists)) if j not in (lf, torso))
    back = sk.set_root(lf, "front_LEG1_L", e.lists, hosts=False)          # names match ignoring case
    assert sorted(back.moved) == sorted([("spine3", lf, torso), ("neck1", lf, torso)]), back.moved
    assert back.lists[lf] == sk.lists[lf] and set(back.lists[torso]) == set(sk.lists[torso]), back.lists[torso]
    assert back.lists[torso] == sk.lists[torso], "the Torso's source order is not restored"
    # a dropped bone another limb still lists (a duplicate) just leaves this limb
    dup = list(sk.lists)
    dup[head] = dup[head] + ("Front_leg1_l",)
    e = boner.set_root_edit(head, "neck2", sk.names, sk.parents, dup, sk.ids, sk.ln, sk.lists, sk.hosts)
    assert e.moved == [("Front_leg1_l", head, lf)] and e.lists[torso] == sk.lists[torso] and e.lists[head] == sk.lists[head]
    refused(lambda: sk.set_root(torso, "pelvis"), ("is the Torso", "no root"))
    refused(lambda: sk.set_root(lf, "no_such_bone"), ("no bone 'no_such_bone'",))
    rt = sk.limb(0x302)
    refused(lambda: sk.set_root(lf, "top_wing_r"), ("'Cheetah_RTWing' would have no bones left", "limbs are boneless!"))
    # a free stub root is never swept into another class: LTWing deleted, LFLeg rooted at spine3 leaves Top_Wing_L alone
    lt = sk.limb(0x301)
    keep = [i for i in range(len(sk.lists)) if i != lt]
    e = boner.set_root_edit(keep.index(lf), "spine3", sk.names, sk.parents, [sk.lists[i] for i in keep],
                            [sk.ids[i] for i in keep], [sk.ln[i] for i in keep])
    assert "Top_Wing_L" not in e.lists[keep.index(lf)] and not any(b == "Top_Wing_L" for b, _f, _t in e.moved), e.moved
    assert rt != lt

    # Add / Remove Active Bone
    e = sk.remove(head, "NOSE", sk.lists)
    assert e.moved == [("nose", head, -1)] and e.lists[head] == tuple(b for b in sk.lists[head] if b != "nose")
    assert e.describe(sk.ln) == "moved 1 bone: nose ('Cheetah_Head' -> no limb)"
    w = sk.warnings(e, sk.lists)
    assert w == ["nose owns 218 control points of 'Cheetah_Head' and is now in no limb: grafted as a donor, ComboTest.exe "
                 "puts them on the root bone (give nose back to 'Cheetah_Head', or those points to one of its bones)"], w
    a = sk.add(head, "nose", e.lists)
    assert a.moved == [("nose", -1, head)] and a.lists == sk.lists, "remove + re-add is not the source list"
    assert sk.warnings(a, e.lists) == []
    m1 = sk.add(torso, "nose", sk.lists)                            # moved between limbs
    assert m1.moved == [("nose", head, torso)] and m1.lists[torso] == sk.lists[torso] + ("nose",)
    assert m1.lists[head] == e.lists[head]
    m2 = sk.add(head, "nose", m1.lists)
    assert m2.moved == [("nose", torso, head)] and m2.lists == sk.lists, "moved and back is not the source lists"
    # the verified ComboTest.exe failure: Front_leg1_l in the Torso (as a donor: 'Assigning Vertex to the root bone' +
    # dbBreak; as the torso: two front_leg1_l bones) -- allowed as a single-bone edit, with both warnings
    f1 = sk.add(torso, "Front_leg1_l", sk.lists)
    assert f1.moved == [("Front_leg1_l", lf, torso)], f1.moved
    w = sk.warnings(f1, sk.lists)
    assert len(w) == 2 and w[0].startswith("Front_leg1_l owns 46 control points of 'Cheetah_LFLeg' and is now in "
                                           "'Cheetah_Torso'") and "fails an assertion (no export)" in w[0], w
    assert w[1] == ("'Cheetah_Torso' now lists Front_leg1_l, the standard root of L Front Leg limbs: with this creature "
                    "as the torso, a grafted L Front Leg limb brings a second 'front_leg1_l' bone"), w
    f2 = sk.add(lf, "Front_leg1_l", f1.lists)
    assert f2.lists == sk.lists and sk.warnings(f2, f1.lists) == []
    # host bones never leave the Torso, by Add or Remove either
    refused(lambda: sk.add(head, "neck1", sk.lists), ("Add Active Bone 'neck1' would take neck1 (hosts Cheetah_Head",
                                                      "Index >= 0"))
    refused(lambda: sk.remove(torso, "spine3", sk.lists), ("Remove Active Bone 'spine3' would take spine3", "Index >= 0"))
    assert sk.add(head, "spine1", sk.lists).moved == [("spine1", torso, head)]       # not a host on cheetah
    # a bone new to a limb made in Blender appends in skeleton order
    e = boner.add_bone_edit(lf, "spine1", sk.names, e.lists, sk.ids, sk.ln, [None] * len(sk.lists))
    assert e.lists[lf] == sk.lists[lf] + ("spine1",)
    refused(lambda: sk.add(head, "nose", sk.lists), ("already a bone of 'Cheetah_Head'",))
    refused(lambda: sk.add(head, "swing1_r", sk.lists), ("'Cheetah_RSWing' would have no bones left",))
    refused(lambda: sk.add(head, "nope", sk.lists), ("no bone 'nope'",))
    refused(lambda: boner.remove_bone_edit(rt, "top_wing_r", sk.lists, sk.ln), ("'Cheetah_RTWing' would have no bones",))
    refused(lambda: boner.remove_bone_edit(head, "spine1", sk.lists, sk.ln), ("not a bone of 'Cheetah_Head'",))
    refused(lambda: boner.remove_bone_edit(99, "spine1", sk.lists, sk.ln), ("no limb 99",))
    say("3 EDITS: Set Root refuses a standard root left to the Torso (LFLeg at Front_leg2_l, Head at head, LFArm at jaw1, "
        "Tail at tail2) and a host bone out of the Torso (spine3, also by Add / Remove); the sweep alone takes spine3 + "
        "neck1 from the Torso only and back restores the source order; refusals (Torso, unknown, another limb's only "
        "bone); moved between limbs and back / removed and re-added = the source lists; warnings name the stray "
        "points (46 LFLeg, 218 Head) and the Torso's standard root, none when put back")


# ----------------------------------------------------------------------------------------------------------- 3b
def whole_branch():
    """pelvis > spine > x > y > z, spine > w: limb A = x, y, z; limb B = w (classes no standard root uses)."""
    names = ["pelvis", "spine", "x", "y", "z", "w"]
    parents = [-1, 0, 1, 2, 3, 1]
    lists = [("pelvis", "spine"), ("x", "y", "z"), ("w",)]
    ids, ln = [0x800, 0x10001, 0x10002], ["T", "A", "B"]
    e = boner.set_root_edit(2, "y", names, parents, lists, ids, ln, lists)
    assert e.lists == [("pelvis", "spine", "w"), ("x",), ("y", "z")], e.lists
    assert e.moved == [("y", 1, 2), ("z", 1, 2), ("w", 2, 0)], e.moved
    assert e.describe(ln) == "moved 3 bones: y, z ('A' -> 'B'); w ('B' -> 'T')", e.describe(ln)
    refused(lambda: boner.set_root_edit(2, "x", names, parents, lists, ids, ln, lists), ("'A' would have no bones left",))
    # a branch of a third limb below the root stays out: y > q in A, y > z in C
    ids3, ln3 = ids + [0x10004], ln + ["C"]
    lists3 = [("pelvis", "spine"), ("x", "y", "q"), ("w",), ("z",)]
    names3, parents3 = names + ["q"], parents + [3]
    e = boner.set_root_edit(2, "y", names3, parents3, lists3, ids3, ln3)
    assert e.lists[1] == ("x",) and e.lists[2] == ("y", "q") and e.lists[3] == ("z",), e.lists
    say("3b WHOLE BRANCH: a root in another limb takes that limb's bones below it (y, z), never a third limb's branch; "
        "the other limb stays one subtree")


# ------------------------------------------------------------------------------------------------------------ 4
def export_bytes():
    raw = open(CHEETAH, "rb").read()
    src = SP.read(CHEETAH)
    sk = Sk(src)
    head, torso = sk.limb(0x40), sk.limb(0x800)

    def build(lists):
        m = SP.read(CHEETAH)
        rows = [LimbRow(i, l.name, int(l.limb_id), None if tuple(lists[i]) == tuple(l.bone_names) else tuple(lists[i]))
                for i, l in enumerate(src.limbs)]
        structure.apply_limbs(m, src, rows, None)
        return m, SP.write(m)

    e = boner.remove_bone_edit(head, "nose", sk.lists, sk.ln)
    m, data = build(e.lists)
    assert data != raw and len(raw) - len(data) == 4 + len("nose"), (len(raw), len(data))   # one length-prefixed name
    assert structure.limb_changes(src, m, list(range(len(src.limbs)))) == ["bones 1 (Cheetah_Head)"]
    assert "STOCK 1 bone is in no limb (nose)" in ICval.check_limb_bones(m)[0]
    m.limbs[head].bone_names = list(src.limbs[head].bone_names)
    assert SP.write(m) == raw, "removing nose changed more than the Head's LIMB record"
    m1 = sk.add(torso, "nose", sk.lists)
    m2 = sk.add(head, "nose", m1.lists)
    assert build(m1.lists)[1] != raw and build(m2.lists)[1] == raw
    f1 = sk.add(torso, "Front_leg1_l", sk.lists)
    assert build(sk.add(sk.limb(0x25), "Front_leg1_l", f1.lists).lists)[1] == raw
    back = sk.set_root(sk.limb(0x25), "Front_leg1_l", sk.set_root(sk.limb(0x25), "spine3", hosts=False).lists,
                       hosts=False)
    assert build(back.lists)[1] == raw
    say("4 BYTES: nose out of the Head changes only that LIMB record (one name, 8 bytes) and says 'bones 1'; moved and back, "
        "re-rooted and back: byte-identical")


# ------------------------------------------------------------------------------------------------------------ 5
def stock_lines():
    seen = {}
    for corpus, paths in (("E", ENGINE), ("T", ADDON)):
        for p, raw, m in creatures(paths):
            if os.path.basename(p) in TEST_FILES:
                continue
            got = ICval.check_limb_bones(m)
            if got:
                seen[(corpus, os.path.basename(p))] = got
    assert list(seen) == [("E", "scorpion.sgm")] and len(seen[("E", "scorpion.sgm")]) == 1, seen

    def lines(edit):
        m = SP.read(CHEETAH)
        edit(m)
        out = ICval.check_limb_bones(m)
        assert all(x.startswith("STOCK ") for x in out) and not core.blocking(out), out
        return out
    L = {l.name: i for i, l in enumerate(SP.read(CHEETAH).limbs)}

    def two_limbs(m):
        m.limbs[L["Cheetah_Torso"]].bone_names.append("nose")
    out = lines(two_limbs)
    assert out == ["STOCK bone 'nose' is in 2 limbs (Cheetah_Torso, Cheetah_Head): a bone belongs to one limb"], out

    def branches(m):
        m.limbs[L["Cheetah_LFLeg"]].bone_names.remove("Front_leg2_l")
    out = lines(branches)
    assert len(out) == 3 and out[0].startswith("STOCK 1 bone is in no limb (Front_leg2_l)"), out
    assert out[1].startswith("STOCK limb 'Cheetah_LFLeg': its bones form 2 separate branches (Front_leg1_l, "
                             "Front_leg3_l)"), out
    assert out[2].startswith("STOCK limb 'Cheetah_LFLeg': its patches use control points of Front_leg2_l (36 points, in "
                             "no limb), not one of its bones"), out

    def torso_branches(m):                       # the Torso is the remainder: never a branch line
        m.limbs[L["Cheetah_Torso"]].bone_names.append("endjaw")
        m.limbs[L["Cheetah_Head"]].bone_names.remove("endjaw")
    assert lines(torso_branches) == [], lines(torso_branches)

    def one_bone_leg(m):
        l = m.limbs[L["Cheetah_LFLeg"]]
        m.limbs[L["Cheetah_Torso"]].bone_names += [b for b in l.bone_names if b != "Front_leg1_l"]
        l.bone_names = ["Front_leg1_l"]
    out = lines(one_bone_leg)
    assert out[0] == ("STOCK leg 'Cheetah_LFLeg' has patches but only one bone ('Front_leg1_l'): ComboTest.exe never "
                      "offers a one-bone leg as a donor (every base-game one-bone leg is a stub with no patches)"), out
    assert len(out) == 2 and out[1].startswith("STOCK limb 'Cheetah_LFLeg': its patches use control points of "
                                               "Front_ankle_l (76 points, in Cheetah_Torso), Front_leg3_l"), out

    def noswap_leg(m):
        one_bone_leg(m)
        m.limbs[L["Cheetah_LFLeg"]].limb_id |= boner.NOSWAP
    out = lines(noswap_leg)
    assert len(out) == 1 and out[0].startswith("STOCK limb 'Cheetah_LFLeg': its patches use") and \
        "for a leg" not in out[0], out                                  # never grafted: no assertion to warn of

    def stub_leg(m):
        one_bone_leg(m)
        l = m.limbs[L["Cheetah_LFLeg"]]
        m.limbs[L["Cheetah_Torso"]].prims += l.prims
        l.prims = []
    out = lines(stub_leg)
    assert out == ["STOCK stub limb 'Cheetah_LFLeg' (no patches): its bone Front_leg1_l (56 points) owns control "
                   "points: no base-game stub bone owns any (Boner > Bones: move those points to a bone of a limb with "
                   "patches)"], out

    def stub_owner(m):
        m.limbs[L["Cheetah_LTWing"]].bone_names = ["Top_Wing_L", "spine1"]
        m.limbs[L["Cheetah_Torso"]].bone_names.remove("spine1")
    out = lines(stub_owner)
    assert len(out) == 2 and "separate branches (spine1, Top_Wing_L)" in out[0] and \
        out[1].startswith("STOCK stub limb 'Cheetah_LTWing' (no patches): its bone spine1 (36 points) owns"), out

    def lf_torso(m):                             # the wf14 ComboTest.exe failure (donor asserts, torso duplicates)
        m.limbs[L["Cheetah_LFLeg"]].bone_names.remove("Front_leg1_l")
        m.limbs[L["Cheetah_Torso"]].bone_names.append("Front_leg1_l")
    out = lines(lf_torso)
    assert len(out) == 2 and out[0].startswith("STOCK limb 'Cheetah_LFLeg': its patches use control points of "
                                               "Front_leg1_l (46 points, in Cheetah_Torso), not one of its bones: "
                                               "grafted onto another torso, ComboTest.exe puts those points on the root "
                                               "bone and, for a leg, fails an assertion with no export"), out
    assert out[1].startswith("STOCK the Torso 'Cheetah_Torso' lists 'Front_leg1_l', the standard root of the 0x25 (Left "
                             "Front Leg) limb"), out

    def jaw_torso(m):                            # exports, but 1264 engine points on the root bone
        for b in ("jaw1", "endjaw"):
            m.limbs[L["Cheetah_Head"]].bone_names.remove(b)
            m.limbs[L["Cheetah_Torso"]].bone_names.append(b)
    out = lines(jaw_torso)
    assert len(out) == 1 and "jaw1 (158 points, in Cheetah_Torso)" in out[0] and "for a leg" not in out[0], out

    def hosts_out(m):                            # LFLeg at spine3: ComboTest.exe 'Index >= 0'
        for b in ("spine3", "neck1"):
            m.limbs[L["Cheetah_Torso"]].bone_names.remove(b)
            m.limbs[L["Cheetah_LFLeg"]].bone_names.append(b)
    out = lines(hosts_out)
    host = [x for x in out if x.startswith("STOCK connection ")]
    assert len(host) == 9 and host[0] == ("STOCK connection 'Cheetah_Head': its host bone 'neck1' is in limb "
                                          "'Cheetah_LFLeg', not the Torso: ComboTest.exe fails 'Index >= 0' when this "
                                          "creature is the torso (every base-game torso connection's host bone is a "
                                          "Torso bone)"), out
    # the host-bone exemption: a limb's points on its own connection's host bone are fine (skipper_caterpillar's legs
    # on hip1, arapaima's tail on pretail2), on another bone of the Torso they are not
    ids = [0x800, 0x25]
    assert ICval.limb_stray_points(ids, [["pelvis", "hip1"], ["leg1"]], [{}, {"hip1": 9, "leg1": 3}], [[], ["hip1"]]) == []
    assert ICval.limb_stray_points(ids, [["pelvis", "hip1"], ["leg1"]], [{}, {"hip1": 9, "leg1": 3}], [[], []]) == \
        [(1, [("hip1", 9)])]
    assert ICval.limb_stray_points(ids, [["pelvis", "spine2"], ["leg1"]], [{}, {"Spine2": 4}], [[], []]) == []
    assert {v: k for k, v in ICval.STANDARD_LIMB_ROOTS.items()} == \
        {s.root.lower(): boner.class_of(s.limb_id) for s in boner.STANDARD_LIMBS if not boner.is_torso(s.limb_id)}
    say("5 STOCK LINES: base game = scorpion's unlimbed antlers only, add-on creatures none; two limbs / branches / "
        "one-bone leg (not NoSwap, not a stub) / stub bone owning points / stray patch points (Front_leg1_l, jaw1 in the "
        "Torso) / a Torso standard root / 9 torso hosts outside the Torso on an edited cheetah; the Torso never "
        "branches; none blocks")


if __name__ == "__main__":
    corpus()
    exceptions()
    edits()
    whole_branch()
    export_bytes()
    stock_lines()
    print("ALL PASS")
