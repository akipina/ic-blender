"""Add Bone without Blender (Boner increment B7): an ordinary bone added to the skeleton and exported.
    python blender_native/tests/bone_add_test.py

Needs the base-game creatures (Assets/engine_stock).
1. structure.insert_added_bones on cheetah (horn_l under head, a tip under it, given out of order): inserted before the
   first stub bone, parents before children, stub bones' parent indices shifted, the key -> file index map, owners
   remapped (an unknown key refused with the orange message), transform = inverse(parent bind) @ rest (a parent rest
   edit included), joint limits zero in the sibling's layout, flags / colour words from the parent with the stub byte
   cleared, skel_info followed (and left alone on woolly_mammoth, where it is not the count); a file whose stub flag
   sits before the parent inserts after the parent (every source bone keeps its exact parent: the index shift past the
   insertion point); refusals (under a stub bone, a source key, a repeated key, a missing
   parent, a cycle, no parent, a non-finite rest, a bad name); structure.added_limb_rows and classify_added_bones
   (the add rule: stamped / unstamped / chains / copies / roots / stubs); structure.added_bone_renames (the old names
   an Action's F-curves still key a renamed added bone by);
2. core.build_export: horn_l skinned with 8 head points (fast path) and on a grown cheetah (an extruded head patch
   skinned to it): every point's world within 1e-9, aux_count = the patch corners of every bone's range (corners
   first: core.aux_rule_problems empty), ICParse load gate and rulebook unchanged, the file reads back, the export
   summary names the bone and its limb, a bone owning nothing gives the STOCK line, a hard-coded name warns, a name
   clashing (ignoring case) with a renamed bone is refused, renames and added bones together; the build equals the
   file the B7 engine probe played in the Object Editor (/tmp/bz_b7_probe_horn.sgm's recipe, rebuilt here);
3. clips: kept clips keep their bytes; ICParse holds a bone with no track at its bind-local transform (it follows its
   parent: the engine fact the probe measured); a clip re-encoded against the export model gives the added bone the
   bind-local motion (no track or a bind track);
5. limb identity: an added bone is in its CURRENT parent's limb whatever the stored rows say -- a renamed bone and a
   second bone called by its old name both in the Head, an added bone deleted again exports the stock bytes (its entry
   dropped, said in the summary), an adopted chain's middle bone deleted, a bone re-parented into the Tail (moved) and
   into the Torso (moved, the stray-point warning and STOCK line);
6. export stub bones (structure.export_stub_bones, LIVE in both directions since fit-to-rig 2026-09-16): the flagged
   ones, minus any a live limb WITH patches lists, plus the point-less bones of any live limb with no patches -- the
   source's or a new one. A limb that loses its patches makes its point-less bones stubs, and a limb that gains patches
   takes its bones out of the set at once (before, only a save and a re-import did). With a new stub limb naming
   cheetah's endtail the fast and the grown path insert at the same place and both refuse a bone under endtail;
4. corpus: a leaf added under every creature's head (else its busiest bone; 61/61) owning 5 of that bone's points (2
   patch corners): before the stub bones, the points on it, aux count = its corners (aux rule empty), every point's world
   within 1e-6, in its parent's limb, and it reads back with the load gate and rulebook unchanged;
   and the last non-stub leaf bone of each creature taken out and added back through the export (its limb list position
   kept) is the stock record -- position, parent, range, aux count, float32 transform and joint limit values 61/61 --
   and the whole file is byte-identical once the flags word, the colour word (arbitrary in stock: the parent's on few)
   and the joint limits' zero signs (-0.0 on 10) are the stock ones.
"""
import copy
import glob
import os
import sys
import time
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(HERE))
import core  # noqa: E402
import boner  # noqa: E402
import structure  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import anim as ICanim  # noqa: E402

T0 = time.perf_counter()
STOCK = os.path.join(REPO, "Assets", "engine_stock")
ENGINE = sorted(glob.glob(os.path.join(STOCK, "*.sgm")))
CHEETAH = os.path.join(STOCK, "cheetah.sgm")
TMP = "/tmp/bz_b7"
AB = structure.AddedBone
RA = structure.RigAdded


def say(*a):
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def raises(fn, words):
    try:
        fn()
    except ValueError as e:
        assert all(w in str(e) for w in words), (words, str(e))
        return str(e)
    raise AssertionError(f"not refused: {words}")


def bidx(m, name):
    return next(i for i, b in enumerate(m.bones) if b.name == name)


def horn_world(m, head, deg=35.0, t=(0.03, 0.06, 0.05)):
    bw = R.bind_world_matrices(m)
    a = np.radians(deg)
    L = np.eye(4)
    L[:3, :3] = [[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]]
    L[:3, 3] = t
    return bw[head] @ L, L


def gate(m):
    return sorted(ICval.check_model(m)), sorted(ICval.check_creature_rulebook(m, include_structural=False))


def write_read(m, name):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(SP.write(m))
    return SP.read(p), p


# ------------------------------------------------------------------------------------------------------------- 1
def part_insert():
    src = SP.read(CHEETAH)
    n = len(src.bones)
    head = bidx(src, "head")
    stubs = structure.stub_bone_indices(src)
    assert min(stubs) == 48 and len(stubs) == 6, stubs
    W, L = horn_world(src, head)
    Wtip = W @ np.array([[1, 0, 0, 0], [0, 1, 0, 0.04], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64)
    m = copy.deepcopy(src)
    m.bones[head].meta0 = 0x12345600          # a parent flags word with high bytes (a stub-byte parent is refused below)
    ins = structure.insert_added_bones(m, [AB("horn_tip", n + 1, n, tuple(Wtip.reshape(-1))),
                                           AB("horn_l", n, head, tuple(W.reshape(-1)))], n)
    assert ins.at == 48 and ins.added == ["horn_l", "horn_tip"], (ins.at, ins.added)
    assert [b.name for b in m.bones[48:50]] == ["horn_l", "horn_tip"] and len(m.bones) == n + 2
    assert m.bones[48].parent == head and m.bones[49].parent == 48
    assert ins.index[n] == 48 and ins.index[n + 1] == 49 and ins.index[47] == 47 and ins.index[48] == 50
    assert [m.bones[i].name for i in range(50, 56)] == [src.bones[i].name for i in range(48, 54)]
    assert all(m.bones[i + 2].parent == src.bones[i].parent for i in range(48, 54))     # stub parents are below 48
    assert ins.keys(len(m.bones)) == list(range(48)) + [n, n + 1] + list(range(48, 54))
    h = m.bones[48]
    local = np.array(h.transform, dtype=np.float64).reshape(4, 3).T
    assert np.allclose(local, L[:3, :4], atol=1e-12), local
    assert h.tail == [0.0] * 6 and (h.start_vertex, h.vertex_count, h.meta5) == (0, 0, 0)
    assert h.meta0 == 0x12345600 and h.meta1 == src.bones[head].meta1, (hex(h.meta0), h.meta1)
    assert m.bones[49].meta0 == 0x12345600 and m.bones[49].meta1 == h.meta1
    assert m.skel_info == n + 2
    bw = R.bind_world_matrices(m)
    assert np.allclose(bw[48], W, atol=1e-9) and np.allclose(bw[49], Wtip, atol=1e-9)
    own = np.array(R.vertex_bone_owners(src))
    k = own.copy(); k[:3] = n; k[3] = n + 1
    got = ins.owners(k)
    assert list(got[:4]) == [48, 48, 48, 49] and np.array_equal(got[4:], np.where(own[4:] >= 48, own[4:] + 2, own[4:]))
    raises(lambda: ins.owners(np.array([0, n + 5, n + 5, -1])), ("2 control points are skinned to an added bone that is "
                                                                 "not on the rig", "orange"))
    # the parent's rest edit is in the bind the transform is taken against
    m2 = copy.deepcopy(src)
    tf = list(m2.bones[head].transform); tf[9] += 0.25
    m2.bones[head].transform = tf
    structure.insert_added_bones(m2, [AB("horn_l", n, head, tuple(W.reshape(-1)))], n)
    assert np.allclose(R.bind_world_matrices(m2)[48], W, atol=1e-9)
    # skel_info not the count (woolly_mammoth): left alone; no stub bones: appended
    wm = SP.read(os.path.join(STOCK, "woolly_mammoth.sgm"))
    si, nw = wm.skel_info, len(wm.bones)
    assert si != nw
    structure.insert_added_bones(wm, [AB("x_tip", nw, 0, tuple(np.eye(4).reshape(-1)))], nw)
    assert wm.skel_info == si
    flat = copy.deepcopy(src)
    for b in flat.bones:
        b.meta0 = int(b.meta0) & 0xFFFFFF00
    flat.limbs = [l for l in flat.limbs if l.prims]
    assert not structure.stub_bone_indices(flat)
    ins = structure.insert_added_bones(flat, [AB("horn_l", n, head, tuple(W.reshape(-1)))], n)
    assert ins.at == n and flat.bones[-1].name == "horn_l"
    # a stub flag before the parent (a file off the stub rule): after the parent; ordinary bones follow the insertion, and
    # every source bone keeps its parent (the parent indices past the insertion point shift) -- under head (after it)
    # and under a bone before the flagged one (at the flag)
    for parent in (head, 10):
        off = copy.deepcopy(src)
        off.bones[20].meta0 = 1
        ins = structure.insert_added_bones(off, [AB("horn_l", n, parent, tuple(W.reshape(-1)))], n)
        at = max(20, parent + 1)
        assert ins.at == at and off.bones[at].name == "horn_l" and off.bones[at].parent == parent, (ins.at, at)
        shifted = [b.name for b in src.bones if b.parent is not None and int(b.parent) >= at]
        assert len(shifted) >= 9, shifted                      # the test sees the shift: bones whose parent moved
        for i, b in enumerate(src.bones):
            nb = off.bones[ins.index[i]]
            want = None if b.parent is None else ins.index[int(b.parent)]
            assert nb.name == b.name and nb.parent == want, (parent, b.name, nb.parent, want)
        assert all(b.parent is None or b.parent < i for i, b in enumerate(off.bones))
    # the joint-limit layout of a sibling, else the parent's
    t7 = copy.deepcopy(src)
    for b in t7.bones:
        if b.parent == head:
            b.tail = list(b.tail) + [0.0]
    structure.insert_added_bones(t7, [AB("horn_l", n, head, tuple(W.reshape(-1)))], n)
    assert len(t7.bones[48].tail) == 7
    # refusals
    ww = tuple(W.reshape(-1))
    stub = sorted(stubs)[0]
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, stub, ww)], n),
           (f"added under '{src.bones[stub].name}', a stub bone",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", 3, head, ww)], n), ("owner key 3",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, head, ww), AB("y", n, head, ww)], n),
           ("share the owner key",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, n + 3, ww)], n), ("no bone of the export",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, n + 1, ww), AB("y", n + 1, n, ww)], n),
           ("its own ancestor",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, -1, ww)], n), ("has no parent",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x", n, head, (np.nan,) * 16)], n),
           ("non-finite",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("hörn", n, head, ww)], n), ("plain ASCII",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB("x" * 64, n, head, ww)], n), ("64 characters",))
    raises(lambda: structure.insert_added_bones(copy.deepcopy(src), [AB(" x", n, head, ww)], n), ("space",))
    raises(lambda: structure.insert_added_bones(m, [AB("x", n + 5, head, ww)], n), ("structure applied twice",))
    say("1a INSERT: before the first stub bone (48), parents first, stub parents kept, key map, owners (unknown key "
        "refused orange), transform exact (rest edit included), joint limits / flags / colour rule, skel_info, no stubs "
        "-> appended, off-rule file -> after the parent (every shifted parent exact), 7-float sibling layout, 10 refusals")

    # limb membership of added bones: their current parent's limb, by identity
    AL = structure.added_limb_rows
    hl = next(j for j, l in enumerate(src.limbs) if int(l.limb_id) & 0xFF7FFFFF == 0x40)
    tl = next(j for j, l in enumerate(src.limbs) if int(l.limb_id) & 0xFF7FFFFF == 0x80)
    r = AL(None, src, [AB("horn_l", n, head, ww, limb_default=True), AB("horn_tip", n + 1, n, ww, limb_default=True)])
    assert r.rows[hl].bones == tuple(src.limbs[hl].bone_names) + ("horn_l", "horn_tip"), r.rows[hl]
    assert all(x.bones is None for j, x in enumerate(r.rows) if j != hl) and not r.warnings and not r.moved
    assert r.placed == ["horn_l -> Cheetah_Head", "horn_tip -> Cheetah_Head"] and r.limb_of == {n: hl, n + 1: hl}, r
    assert AL(None, src, []).rows is None
    stored = [structure.LimbRow(j, l.name, int(l.limb_id) & 0xFFFFFFFF, tuple(l.bone_names) + (
        ("horn_l",) if j == hl else ())) for j, l in enumerate(src.limbs)]
    assert AL(stored, src, [AB("horn_l", n, head, ww)], ["horn_l"]).rows is stored      # listed where it belongs: kept
    # renamed since (listed by its stamp), a second bone called by that name: both in the Head, the renamed one in place
    r = AL(stored, src, [AB("horn_x", n, head, ww, stamp="horn_l"), AB("horn_l", n + 1, head, ww)], ["horn_l", "horn_l"])
    assert r.rows[hl].bones == tuple(src.limbs[hl].bone_names) + ("horn_x", "horn_l") and r.placed == \
        ["horn_l -> Cheetah_Head"] and not r.dropped and not r.warnings, r
    r = AL(stored, src, [AB("horn_x", n, head, ww, stamp="horn_l"), AB("horn_l", n + 1, head, ww)], ["horn_l", "horn_l"],
           by_stamp=True)
    assert r.rows[hl].bones == tuple(src.limbs[hl].bone_names) + ("horn_l", "horn_l"), r.rows[hl]
    # not on the rig any more: its entry dropped, the row back to the source's list (None)
    r = AL(stored, src, [], ["horn_l"])
    assert r.rows[hl].bones is None and r.dropped == [("horn_l", hl)] and not r.placed, r
    assert r.dropped_text([x.name for x in r.rows]) == "horn_l from Cheetah_Head (an added bone not on the rig any more)"
    # its parent is in another limb now: it moves (a child with it)
    tail4 = bidx(src, "tail4")
    stored2 = [structure.LimbRow(x.src, x.name, x.limb_id, (x.bones + ("tip",)) if j == hl else x.bones)
               for j, x in enumerate(stored)]
    r = AL(stored2, src, [AB("horn_l", n, tail4, ww), AB("tip", n + 1, n, ww)], ["horn_l", "tip"])
    assert r.rows[hl].bones is None and r.rows[tl].bones == tuple(src.limbs[tl].bone_names) + ("horn_l", "tip"), r.rows
    assert r.moved == [("horn_l", hl, tl, "tail4"), ("tip", hl, tl, "horn_l")] and not r.dropped and not r.placed, r
    assert r.moved_text([x.name for x in r.rows])[0] == "horn_l -> Cheetah_Tail (moved from Cheetah_Head: its parent " \
                                                        "'tail4' is there)"
    # a source bone's file name, a created stub bone's name are never an added bone's entry
    stored3 = [structure.LimbRow(x.src, x.name, x.limb_id, (x.bones[:-1] + ("stubby", "horn_l")) if j == hl else x.bones)
               for j, x in enumerate(stored)]
    r = AL(stored3, src, [AB("nose2", n, head, ww)], ["nose", "stubby", "horn_l"], keep=["stubby"])
    assert r.rows[hl].bones == tuple(src.limbs[hl].bone_names) + ("stubby", "nose2") and r.dropped == [("horn_l", hl)], r
    nolimb = copy.deepcopy(src)
    for l in nolimb.limbs:
        l.bone_names = [b for b in l.bone_names if b != "head"]
    r = AL(None, nolimb, [AB("horn_l", n, head, ww, limb_default=True), AB("tip", n + 1, n, ww)])
    assert r.rows is None and r.limb_of == {n: -1, n + 1: -1} and len(r.warnings) == 2, r
    assert r.warnings[0] == ("added bone 'horn_l' is in no limb (its parent 'head' is in none): the Object Editor warns "
                             "about a bone in no limb; put 'head' into a limb (Boner > Limbs > Add Active Bone), or "
                             "re-parent the bone under a bone of a limb"), r.warnings
    assert "'horn_l' is an added bone in no limb too" in r.warnings[1], r.warnings

    # the add rule
    S = {"pelvis": 0, "head": head, "neck3": 27, "swing1_r": 51}
    C = structure.classify_added_bones
    ok, bad = C([RA("ext", "head")], S, [], n)
    assert ok == [(RA("ext", "head"), n, head, True)] and not bad
    ok, bad = C([RA("horn_l", "head", 1, "u1", "horn_l"), RA("tip", "horn_l")], S, ["u0", "u1"], n)
    assert [(r.name, k, p, d) for r, k, p, d in ok] == [("horn_l", n + 1, head, False), ("tip", n + 2, n + 1, True)], ok
    ok, bad = C([RA("a", "head", 1), RA("b", "a")], S, [], n)                     # an unstamped chain: taken (C8.3)
    assert [(r.name, k, p, d) for r, k, p, d in ok] == [("a", n, head, True), ("b", n + 1, n, True)] and not bad, (ok, bad)
    ok, bad = C([RA("a", "head", 1), RA("b", "a")], S, [], n, chains=True)
    assert [(r.name, k, p) for r, k, p, _d in ok] == [("a", n, head), ("b", n + 1, n)] and not bad
    ok, bad = C([RA("ear_l.001", "head", 0, src_stamp=True)], S, [], n)
    assert not ok and dict(bad)["ear_l.001"].startswith("bone 'ear_l.001' was added (it carries a source bone's import")
    ok, bad = C([RA("Extra", "")], S, [], n)
    assert not ok and dict(bad)["Extra"].startswith("bone 'Extra' was added at the root")
    ok, bad = C([RA("tip", "swing1_r")], S, [], n, stub_names=["swing1_r"])
    assert not ok and "a stub bone" in dict(bad)["tip"]
    ok, bad = C([RA("tip", "nowhere")], S, [], n)
    assert not ok and "which is no bone the export writes" in dict(bad)["tip"]
    ok, bad = C([RA("wing_tip", "swing1_l", stub=False), RA("swing1_l", "spine3", stub=True)], S, [], n,
                stub_names=["swing1_l"])
    assert not ok and list(dict(bad)) == ["wing_tip"], bad            # the refused stub speaks for itself
    # copies of an added bone: the one keeping its stamp name is it, the other is adopted; none keeps it: refused
    ok, bad = C([RA("horn_l", "head", 0, "u1", "horn_l"), RA("horn_r", "head", 0, "u1", "horn_l")], S, ["u1"], n)
    assert sorted((r.name, k, d) for r, k, _p, d in ok) == [("horn_l", n, False), ("horn_r", n + 1, True)], ok
    ok, bad = C([RA("hornA", "head", 0, "u1", "horn_l"), RA("hornB", "head", 0, "u1", "horn_l")], S, ["u1"], n)
    assert not ok and len(bad) == 2 and "none of them keeps that name" in bad[0][1], bad
    # a copy of an added chain (Shift+D carries both stamps): the copy with a bone under it is named a copy, not "no stamp"
    ok, bad = C([RA("horn_l", "head", 1, "u1", "horn_l"), RA("tip", "horn_l", 0, "u2", "tip"),
                 RA("horn_l.001", "head", 1, "u1", "horn_l"), RA("tip.001", "horn_l.001", 0, "u2", "tip")], S, ["u1", "u2"], n)
    assert [(r.name, k) for r, k, _p, _d in ok] == [("horn_l", n), ("tip", n + 1)], ok
    assert dict(bad)["horn_l.001"] == (
        "bone 'horn_l.001' is a copy of the added bone 'horn_l' (Duplicate / Symmetrize) with 1 bone(s) under it, so the "
        "export can't tell a chain from a re-parent: Boner > Bones > Adopt Added Bones gives the copies their own "
        "identity"), bad
    assert dict(bad)["tip.001"] == "bone 'tip.001' was added under 'horn_l.001', an added bone the export refuses", bad
    ok, bad = C([RA("a", "head", 1, "u9", "a"), RA("b", "a")], S, [], n)          # a stale stamp (table undone): taken
    assert [(r.name, k) for r, k, _p, _d in ok] == [("a", n), ("b", n + 1)] and not bad, (ok, bad)
    # a case clash between two checked names is one sentence (each pair once), with a third spelling two
    P = structure.bone_name_problems
    got = P(["head", "spike", "SPIKE"], ["spike", "SPIKE"])
    assert got == ["bones 'spike' and 'SPIKE' differ only in case: bone names must differ ignoring case (the game looks "
                   "them up lower-cased)"], got
    got = P(["spike", "SPIKE", "Spike"], ["spike", "SPIKE", "Spike"])
    assert [g.split(" differ")[0] for g in got] == ["bones 'spike' and 'SPIKE'", "bones 'spike' and 'Spike'",
                                                     "bones 'SPIKE' and 'Spike'"], got
    # a renamed only holder keeps its identity; a stamp the table lost is a leaf adopted again
    ok, _b = C([RA("spike", "head", 2, "u1", "horn_l"), RA("s2", "spike"), RA("s3", "spike")], S, ["u0", "u1"], n)
    assert [(r.name, k) for r, k, _p, _d in ok] == [("spike", n + 1), ("s2", n + 2), ("s3", n + 3)]
    ok, _b = C([RA("horn_l", "head", 0, "gone", "horn_l")], S, ["u0"], n)
    assert ok[0][1:] == (n + 1, head, True)
    # the old names an Action's stale F-curve paths key renamed added bones by (Blender's own rename)
    N = structure.added_bone_renames
    rig_now = ["head", "horn_left", "tip", "horn_r"]
    ok, _b = C([RA("horn_left", "head", 1, "u1", "horn_l"), RA("tip", "horn_left")], S, ["u1"], n)
    assert N(ok, rig_now) == [("horn_l", "horn_left")], N(ok, rig_now)          # the unstamped leaf has no old name
    ok, _b = C([RA("Horn_L", "head", 0, "u1", "horn_l")], S, ["u1"], n)
    assert N(ok, ["head", "Horn_L"]) == [("horn_l", "Horn_L")]                     # a change of case alone
    ok, _b = C([RA("horn_left", "head", 0, "u1", "horn_l"), RA("horn_l", "head", 0, "u2", "horn_l")], S, ["u1", "u2"], n)
    assert N(ok, ["head", "horn_left", "horn_l"]) == []          # another bone has that name now: its paths are that bone's
    assert N(ok, ["head", "horn_left", "HORN_L"]) == []                            # ignoring case
    ok, _b = C([RA("horn_a", "head", 0, "u1", "horn_l"), RA("horn_b", "head", 0, "u2", "horn_l")], S, ["u1", "u2"], n)
    assert N(ok, ["head", "horn_a", "horn_b"]) == []                               # two renamed bones share it
    ok, _b = C([RA("horn_l", "head", 0, "u1", "horn_l"), RA("horn_r", "head", 0, "u1", "horn_l")], S, ["u1"], n)
    assert N(ok, ["head", "horn_l", "horn_r"]) == []                               # a Symmetrize copy is not a rename
    ok, _b = C([RA("ear_x", "head", 0, "u1", "ear_l")], S, ["u1"], n)
    assert N(ok, ["head", "ear_x", "ear_l"]) == [] and N(ok, ["head", "ear_x", "EAR_L"]) == []   # an import name
    say("1b RULES: added bones' limbs by identity (parent's limb, appended; listed in place kept; renamed + a bone by "
        "its old name both placed; by stamp for the stored rows; gone -> dropped, row back to the source's; parent moved "
        "-> moved with a child; file / stub names never taken; parent in no limb warns with a hint that works); "
        "the add rule (adopted leaf, stamped chain, unstamped chain taken (C8.3), source-stamp copy, root, stub, "
        "unknown parent, refused stub, added-bone copies, renamed holder, stale stamp); renamed added bones' old names "
        "for stale F-curve paths (a change of case kept; left out when a bone or an import name has it, two renamed bones "
        "share it, or the bone is a copy)")


# ------------------------------------------------------------------------------------------------------------- 2
def horn_state(src, path, data, points=8, extra=None, corners=0):
    """horn_l under head owning the ``points`` highest head points (``corners`` of them patch corners, the rest not)."""
    n = len(src.bones)
    head = bidx(src, "head")
    W, _L = horn_world(src, head)
    own = np.array(R.vertex_bone_owners(src))
    world = np.asarray(R.world_vertices(src), dtype=np.float64)
    hp = np.nonzero(own == head)[0]
    hp = hp[np.argsort(-world[hp, 1])]
    if corners:
        cm = core.corner_mask(core.source_table(src), len(src.vertices))
        top = np.concatenate([hp[cm[hp]][:corners], hp[~cm[hp]][:points - corners]])
    else:
        top = hp[:points]
    vo = own.copy()
    vo[top] = n
    ab = AB("horn_l", n, head, tuple(W.reshape(-1)), limb_default=True)
    return core.EditState(path, data.verts, vert_owner=vo, added_bones=[ab] + list(extra or [])), top


def part_build():
    src = SP.read(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    data = core.creature_arrays_from_model(src, CHEETAH)
    n = len(src.bones)
    head = bidx(src, "head")
    st, top = horn_state(src, CHEETAH, data)
    probe_m = core.build_export(st).model
    same_model(probe_recipe(src, top), probe_m)          # the engine-probed creature, up to float64 rounding
    st, top = horn_state(src, CHEETAH, data, corners=3)
    built = core.build_export(st)
    m = built.model
    mb, _p = write_read(m, "horn.sgm")
    assert SP.write(mb) == SP.write(m)
    hi = bidx(m, "horn_l")
    assert hi == 48 and len(m.bones) == n + 1 and built.bones_new == ["horn_l"] and built.bone_keys[48] == n
    o2n = built.o2n
    W0 = np.asarray(R.world_vertices(src), dtype=np.float64)
    W1 = np.asarray(R.world_vertices(m), dtype=np.float64)
    assert np.abs(W1[o2n] - W0).max() < 1e-9
    own1 = np.asarray(R.vertex_bone_owners(m))
    assert sorted(o2n[top].tolist()) == sorted(np.nonzero(own1 == hi)[0].tolist())
    assert core.aux_rule_problems(m) == [], core.aux_rule_problems(m)[:3]
    corner = core.corner_mask(core.source_table(m), len(m.vertices))
    assert m.bones[hi].meta5 == int(corner[own1 == hi].sum()) and m.bones[hi].meta5 > 0, m.bones[hi].meta5
    # the scripted horn takes head points without regard to their patches' corners, so some handles end up on a bone
    # none of their nearest corners has: check_model says so (a battle creature skins with corner bones only,
    # 2026-11-15); everything else is the source's
    loose = [x for x in gate(m)[0] if x not in gate(src)[0]]
    assert len(loose) == 1 and "not their nearest corner's" in loose[0], loose
    assert [x for x in gate(m)[0] if x not in loose] == gate(src)[0] and gate(m)[1] == gate(src)[1], \
        (set(gate(m)[0]) ^ set(gate(src)[0]), set(gate(m)[1]) ^ set(gate(src)[1]))
    head_limb = next(l for l in m.limbs if int(l.limb_id) & 0xFF7FFFFF == 0x40)
    assert head_limb.bone_names[-1] == "horn_l"
    s = core.export_summary(built)
    assert "bones: added 1 (horn_l), into their parent's limb: horn_l -> Cheetah_Head" in s.line, s.line
    assert "limbs: bones 1 (Cheetah_Head)" in s.line and not s.stock and not built.warnings, (s.line, s.stock)
    assert [a.name for a in m.animations] == [a.name for a in src.animations]
    assert SP.write(m).find(b"O_growl") > 0
    say(f"2a HORN: the engine probe's creature (8 head points) is what the export writes; cheetah + horn_l under head "
        f"with {len(top)} head points (3 corners) -> bone 48, world 1e-9, aux {m.bones[hi].meta5} (every bone on the "
        "corners-first rule), load gate + rulebook unchanged, reads back, Head limb, summary")

    # owning nothing: the STOCK line; a hard-coded name warns; a clash with a renamed bone is refused
    st0 = core.EditState(CHEETAH, data.verts, added_bones=[AB("horn_l", n, head, st.added_bones[0].world,
                                                              limb_default=True)])
    b0 = core.build_export(st0)
    assert b0.stock == ["STOCK added bone 'horn_l' owns no control points and is in no stub limb: it moves nothing in "
                        "game; skin points to it (Boner > Bones > Assign Selected) or delete it"], b0.stock
    assert core.export_summary(b0).stock == b0.stock and b0.o2n is None
    out0 = SP.write(b0.model)
    assert len(out0) > len(raw)
    bw = R.bind_world_matrices(src)
    bj = core.build_export(core.EditState(CHEETAH, data.verts, added_bones=[
        AB("front_arm2_l", n, bidx(src, "neck1"), tuple(bw[bidx(src, "neck1")].reshape(-1)), limb_default=True)]))
    assert any(w.startswith("added bone 'front_arm2_l': the game treats a bone called 'front_arm2_l' specially")
               for w in bj.warnings), bj.warnings
    raises(lambda: core.build_export(core.EditState(CHEETAH, data.verts, bone_renames={36: "horn"}, added_bones=[
        AB("HORN", n, head, st.added_bones[0].world)])), ("'horn'", "'HORN'", "differ only in case"))
    br = core.build_export(core.EditState(CHEETAH, data.verts, vert_owner=st.vert_owner, bone_renames={36: "ear_left"},
                                          added_bones=[AB("spike", n, head, st.added_bones[0].world, stamp="horn_l")],
                                          limbs=[structure.LimbRow(j, l.name, int(l.limb_id) & 0xFFFFFFFF,
                                                                   tuple(l.bone_names) + (("horn_l",) if int(l.limb_id)
                                                                                           & 0xFF7FFFFF == 0x40 else ()))
                                                 for j, l in enumerate(src.limbs)]))
    names = [b.name for b in br.model.bones]
    assert "ear_left" in names and names[48] == "spike" and "horn_l" not in names
    hl = next(l for l in br.model.limbs if int(l.limb_id) & 0xFF7FFFFF == 0x40)
    assert "spike" in hl.bone_names and "ear_left" in hl.bone_names and "horn_l" not in hl.bone_names, hl.bone_names
    line = core.export_summary(br).line
    assert "bones: added 1 (spike)" in line and "renamed 1 (ear_L -> ear_left)" in line, line
    say("2b EDGES: an added bone owning nothing -> the STOCK line; front_arm2_l warns (a name the game looks up); "
        "a name clashing with a renamed bone refused; renamed + added (by its stamp name in the limb row) together")

    # grown: an extruded head patch skinned to the horn
    P = np.asarray(data.patches)
    pl = core._patch_limb_of(src)
    own = np.array(R.vertex_bone_owners(src))
    hp = next(i for i in range(len(P)) if pl[i] == next(j for j, l in enumerate(src.limbs)
                                                        if int(l.limb_id) & 0xFF7FFFFF == 0x40)
              and all(own[P[i]] == head))
    nv, ncp = core.extrude_patch(data.verts, P[hp], side=3)
    verts = np.vstack([data.verts, nv])
    patches = np.vstack([P, np.asarray(ncp)[None]])
    vo = np.concatenate([st.vert_owner, np.full(len(nv), n)])
    g = core.EditState(CHEETAH, verts, patches=patches, vert_owner=vo, patch_limb=np.append(pl, pl[hp]),
                       patch_uv=np.vstack([data.patch_uv, data.patch_uv[hp][None]]),
                       patch_material=np.append(data.patch_mat, data.patch_mat[hp]),
                       patch_type=np.append([p.type for p in src.patches], src.patches[hp].type),
                       added_bones=st.added_bones)
    bg = core.build_export(g)
    mg = bg.model
    assert bg.grown and bidx(mg, "horn_l") == 48
    Wg = np.asarray(R.world_vertices(mg), dtype=np.float64)
    assert np.abs(Wg[bg.o2n] - core.blender_to_sgm(verts)).max() < 1e-6
    assert core.aux_rule_problems(mg) == []
    og = np.asarray(R.vertex_bone_owners(mg))
    assert int((og == 48).sum()) == len(top) + len(nv)
    mgb, _p = write_read(mg, "horn_grown.sgm")
    assert len(mgb.bones) == n + 1
    say(f"2c GROWN: an extruded head patch + 8 points on horn_l -> {int((og == 48).sum())} points, world 1e-6, aux rule "
        "holds, reads back")
    return m


def same_model(a, b, tol=1e-9):
    """``a`` and ``b`` are the same creature: equal records, floats (bone transforms, control points) within ``tol``."""
    assert len(a.bones) == len(b.bones) and a.skel_info == b.skel_info
    for x, y in zip(a.bones, b.bones):
        assert (x.name, x.parent, x.start_vertex, x.vertex_count, x.tail, x.meta0, x.meta1, x.meta5) == \
               (y.name, y.parent, y.start_vertex, y.vertex_count, y.tail, y.meta0, y.meta1, y.meta5), (x, y)
        assert np.allclose(x.transform, y.transform, atol=tol), (x.name, x.transform, y.transform)
    assert np.allclose(np.asarray(a.vertices), np.asarray(b.vertices), atol=1e-6)
    assert a.limbs == b.limbs and a.limb_connections == b.limb_connections
    assert [p.control_points for p in a.patches] == [p.control_points for p in b.patches]
    assert len(SP.write(a)) == len(SP.write(b))


def probe_recipe(src, top):
    """The B7 engine probe's creature, built with ICParse by hand (scratchpad probe_build.py): horn_l inserted at the
    first stub bone under head (35 degrees about x, offset 0.03 / 0.06 / 0.05), flags word of the parent with the stub
    byte cleared, colour of the parent, joint limits zero, the head limb listing it last, the 8 points reskinned."""
    from ICParse.sgm.creature.model import Bone
    m = copy.deepcopy(src)
    head = bidx(m, "head")
    _W, L = horn_world(src, head)
    k = min(structure.stub_bone_indices(m))
    bw0 = R.bind_world_matrices(src)
    owners = np.asarray(R.vertex_bone_owners(src), dtype=np.int64)
    world = np.asarray(R.world_vertices(src, owners, bw0), dtype=np.float64)
    tf = [float(x) for x in list(L[:3, 0]) + list(L[:3, 1]) + list(L[:3, 2]) + list(L[:3, 3])]
    par = m.bones[head]
    for b in m.bones:
        if b.parent is not None and int(b.parent) >= k:
            b.parent = int(b.parent) + 1
    m.bones.insert(k, Bone(name="horn_l", parent=head, start_vertex=0, vertex_count=0, transform=tf,
                           tail=[0.0] * len(par.tail), meta0=int(par.meta0) & 0xFFFFFF00, meta1=int(par.meta1), meta5=0))
    m.skel_info = len(m.bones)
    for l in m.limbs:
        if int(l.limb_id) & 0xFF7FFFFF == 0x40:
            l.bone_names = list(l.bone_names) + ["horn_l"]
    no = np.where(owners >= k, owners + 1, owners)
    no[top] = k
    core._apply_reskin(m, no, world, R.bind_world_matrices(m))
    return m


# ------------------------------------------------------------------------------------------------------------- 3
def part_clips(m):
    src = SP.read(CHEETAH)
    from ICParse.iff.document import IffDocument
    p = os.path.join(TMP, "horn.sgm")
    kept = {ICanim.read_anim(ch).name: ch.serialize() for ch in IffDocument.load(p).find_all("ANIM")}
    stock = {ICanim.read_anim(ch).name: ch.serialize() for ch in IffDocument.load(CHEETAH).find_all("ANIM")}
    assert kept == stock and len(kept) == 20, "kept clips changed"
    hi, head = bidx(m, "horn_l"), bidx(m, "head")
    bw = R.bind_world_matrices(m)
    local = np.linalg.inv(bw[head]) @ bw[hi]
    growl = next(a for a in m.animations if a.name == "O_growl")
    assert "horn_l" not in {t.bone_name.lower() for t in growl.tracks}
    worst = 0.0
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        Wt = R.animated_bone_world(m, growl, t)
        assert np.allclose(Wt[hi], Wt[head] @ local, atol=1e-9)
        worst = max(worst, float(np.abs(Wt[hi] - bw[hi]).max()))
    assert worst > 1e-2, "O_growl does not move the head: the hold check would not tell bind-local from bind-world"
    # a clip re-encoded against the export model: the added bone, at rest under its moving parent, plays bind-local
    K = 8
    times = [f / K for f in range(K + 1)]
    Wf = [R.animated_bone_world(m, growl, t) for t in times]
    tracks = ICanim.tracks_from_world(core.clip_track_model(m, Wf), times, Wf, ICanim.layout_of(growl))
    clip = ICanim.build_animation("O_growl2", K, 30.0, tracks, layout=ICanim.layout_of(growl), model=m)
    names = {t.bone_name.lower() for t in clip.tracks}
    follow = err = 0.0
    for j, t in enumerate(times):
        Wc = R.animated_bone_world(m, clip, t)
        follow = max(follow, float(np.abs(Wc[hi] - Wc[head] @ local).max()))    # rests bind-local under its parent
        err = max(err, float(np.abs(Wc[hi] - Wf[j][hi]).max()))                 # the clip builder's precision
    assert follow < 1e-5 and err < 1e-3, (follow, err)
    say(f"3 CLIPS: 20 kept clips byte-identical; O_growl has no horn_l track and ICParse holds it bind-local (the head "
        f"moves it up to {worst:.3f}); re-encoded against the export: horn_l {'tracked' if 'horn_l' in names else 'no track'}"
        f", bind-local under head within {follow:.1e} (clip precision {err:.1e})")


# ------------------------------------------------------------------------------------------------------------- 5
def head_rows(src, extra):
    """The source's limb rows with ``extra`` bone names appended to the Head limb (what Add Bone stores)."""
    return [structure.LimbRow(j, l.name, int(l.limb_id) & 0xFFFFFFFF, tuple(l.bone_names) + (
        tuple(extra) if int(l.limb_id) & 0xFF7FFFFF == 0x40 else ())) for j, l in enumerate(src.limbs)]


def limbs_of(m, bone):
    return [l.name for l in m.limbs if str(bone).lower() in {str(b).lower() for b in l.bone_names}]


def new_lines(m, src):
    """check_model / limb-bone lines the export has and the source has not. The handle-bone advisory is left out: the
    scripted owners here take points without regard to their patches' corners (part_build pins that it is reported)."""
    had = set(ICval.check_model(src)) | set(ICval.check_limb_bones(src))
    return sorted(x for x in (set(ICval.check_model(m)) | set(ICval.check_limb_bones(m))) - had
                  if "not their nearest corner's" not in x)


def part_limb_identity():
    src = SP.read(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    data = core.creature_arrays_from_model(src, CHEETAH)
    n = len(src.bones)
    head, nose = bidx(src, "head"), bidx(src, "nose")
    W, _L = horn_world(src, head)
    ww = tuple(W.reshape(-1))
    # (a) horn_l renamed horn_x (its entry still says horn_l), then a second bone horn_l under head: both in the Head
    b = core.build_export(core.EditState(CHEETAH, data.verts, limbs=head_rows(src, ["horn_l"]), bone_table_names=[
        "horn_l", "horn_l"], added_bones=[AB("horn_x", n, head, ww, stamp="horn_l"), AB("horn_l", n + 1, head, ww)]))
    assert (limbs_of(b.model, "horn_x"), limbs_of(b.model, "horn_l")) == (["Cheetah_Head"], ["Cheetah_Head"])
    assert not new_lines(b.model, src) and not [w for w in b.warnings if "no limb" in w], (new_lines(b.model, src), b.warnings)
    line = core.export_summary(b).line
    assert "bones: added 2 (horn_x, horn_l), into their parent's limb: horn_l -> Cheetah_Head" in line, line
    # (b) Add Bone then the bone deleted (no points): the never-added file, the dropped entry in the summary
    for extra, table in ((["horn_l"], ["horn_l"]), (["horn_l", "tip"], ["horn_l", "tip"])):
        b = core.build_export(core.EditState(CHEETAH, data.verts, limbs=head_rows(src, extra), bone_table_names=table))
        assert SP.write(b.model) == raw and not b.warnings, b.warnings
        line = core.export_summary(b).line
        assert line == "limbs: dropped " + ", ".join(f"{x} from Cheetah_Head" for x in extra) + (
            " (an added bone not on the rig any more)" if len(extra) == 1 else " (added bones not on the rig any more)"), line
    # an adopted chain nose > spike1 > spike2 with spike1 deleted (Blender puts spike2 under nose)
    Wn = R.bind_world_matrices(src)[nose].copy(); Wn[:3, 3] += 0.02
    b = core.build_export(core.EditState(CHEETAH, data.verts, limbs=head_rows(src, ["spike1", "spike2"]),
                                         bone_table_names=["spike1", "spike2"],
                                         added_bones=[AB("spike2", n + 1, nose, tuple(Wn.reshape(-1)))]))
    m = b.model
    assert m.find_bone("spike1") is None and m.bones[bidx(m, "spike2")].parent == nose
    assert limbs_of(m, "spike2") == ["Cheetah_Head"] and not new_lines(m, src), new_lines(m, src)
    line = core.export_summary(b).line
    assert "limbs: dropped spike1 from Cheetah_Head (an added bone not on the rig any more)" in line, line
    # (c) re-parented under tail4 with 8 head points: in the Tail limb (moved), no split limb, no stray point
    st, top = horn_state(src, CHEETAH, data)
    tail4, spine2 = bidx(src, "tail4"), bidx(src, "spine2")
    for parent, limb, strays in ((tail4, "Cheetah_Tail", False), (spine2, "Cheetah_Torso", True)):
        Wp = R.bind_world_matrices(src)[parent].copy(); Wp[:3, 3] += 0.01
        b = core.build_export(core.EditState(CHEETAH, data.verts, vert_owner=st.vert_owner, limbs=head_rows(src, ["horn_l"]),
                                             bone_table_names=["horn_l"],
                                             added_bones=[AB("horn_l", n, parent, tuple(Wp.reshape(-1)))]))
        m = b.model
        assert limbs_of(m, "horn_l") == [limb] and m.bones[bidx(m, "horn_l")].parent == parent, limbs_of(m, "horn_l")
        assert not [x for x in ICval.check_limb_bones(m) if "separate branches" in x]
        line = core.export_summary(b).line
        pname = src.bones[parent].name
        assert f"into their parent's limb: horn_l -> {limb} (moved from Cheetah_Head: its parent '{pname}' is there)" \
            in line, line
        stray = [w for w in b.warnings if w.startswith("added bone 'horn_l' owns 8 control points of 'Cheetah_Head'")]
        stock = [x for x in ICval.check_limb_bones(m) if x.startswith("STOCK limb 'Cheetah_Head': its patches use control "
                                                                        "points of horn_l (8 points")]
        if strays:
            assert stray == [f"added bone 'horn_l' owns 8 control points of 'Cheetah_Head' and moved to '{limb}' with its "
                             "parent 'spine2': grafted as a donor, ComboTest.exe puts them on the root bone (re-parent it "
                             "under a bone of 'Cheetah_Head', or assign those points to one of its bones)"], b.warnings
            assert len(stock) == 1, ICval.check_limb_bones(m)
        else:
            assert not stray and not stock and not new_lines(m, src), (b.warnings, new_lines(m, src))
    say("5 LIMB IDENTITY: a renamed added bone + a second bone by its old name both in the Head; an added bone (and two) "
        "deleted again -> the stock bytes, the summary says the entry was dropped; an adopted chain's middle bone deleted "
        "-> spike2 under nose in the Head; re-parented under tail4 -> Tail limb (moved, no split, no stray), under spine2 "
        "-> Torso (moved) with the stray warning and the check_limb_bones STOCK line")


# ------------------------------------------------------------------------------------------------------------- 6
def grown_state(src, data, limbs, added, owners=None):
    """cheetah grown by an extruded head patch (its points on head), with ``limbs`` rows and ``added`` bones."""
    n_head = bidx(src, "head")
    P = np.asarray(data.patches)
    pl = core._patch_limb_of(src)
    own = np.array(R.vertex_bone_owners(src)) if owners is None else np.asarray(owners)
    hp = next(i for i in range(len(P)) if all(own[P[i]] == n_head))
    nv, ncp = core.extrude_patch(data.verts, P[hp], side=3)
    return core.EditState(CHEETAH, np.vstack([data.verts, nv]), patches=np.vstack([P, np.asarray(ncp)[None]]),
                          vert_owner=np.concatenate([own, np.full(len(nv), n_head)]), patch_limb=np.append(pl, pl[hp]),
                          limbs=limbs, patch_uv=np.vstack([data.patch_uv, data.patch_uv[hp][None]]),
                          patch_material=np.append(data.patch_mat, data.patch_mat[hp]),
                          patch_type=np.append([p.type for p in src.patches], src.patches[hp].type), added_bones=added)


def part_export_stubs():
    src = SP.read(CHEETAH)
    data = core.creature_arrays_from_model(src, CHEETAH)
    n = len(src.bones)
    head, endtail, nose = bidx(src, "head"), bidx(src, "endtail"), bidx(src, "nose")
    stubs = structure.stub_bone_indices(src)
    assert endtail < head and not src.bones[endtail].vertex_count and endtail not in stubs and src.bones[nose].vertex_count
    ES = structure.export_stub_bones
    pl = core._patch_limb_of(src)
    own = np.array(R.vertex_bone_owners(src))
    rows = structure.source_rows(src) + [structure.LimbRow(-1, "NewStub", 0x2000, ("endtail",))]
    assert ES(src) == stubs and ES(src, structure.source_rows(src), pl, own) == stubs
    assert ES(src, rows, pl) == ES(src, rows, None, own) == stubs | {endtail}
    # a bone owning points is no stub bone (apply_stub_bones keeps its flags), nor one of a row with a patch;
    # a name the source lacks (a created stub bone) is left to apply_stub_bones
    two = structure.source_rows(src) + [structure.LimbRow(-1, "NewStub", 0x2000, ("Nose", "ENDTAIL", "swing9"))]
    assert ES(src, two, pl) == stubs | {endtail}
    o2 = own.copy(); o2[np.nonzero(own == head)[0][:2]] = endtail
    assert ES(src, rows, pl, o2) == stubs
    pl2 = pl.copy(); pl2[0] = len(rows) - 1
    assert ES(src, rows, pl2) == stubs
    # the rule is LIVE in both directions (fit-to-rig, 2026-09-16): a SOURCE limb that loses its patches makes its
    # point-less bones stub bones -- which is what stub_bone_indices says of the file the export then writes ...
    hl = next(j for j, l in enumerate(src.limbs) if int(l.limb_id) & 0xFF7FFFFF == 0x40)
    tl = next(j for j, l in enumerate(src.limbs) if int(l.limb_id) & 0xFF7FFFFF == 0x80)
    pl3 = np.where(pl == hl, tl, pl)
    head_names = {str(b).lower() for b in src.limbs[hl].bone_names}
    idle = {i for i, b in enumerate(src.bones) if str(b.name).lower() in head_names and not int(b.vertex_count)}
    assert idle and ES(src, structure.source_rows(src), pl3) == stubs | idle, sorted(idle)
    # ... and a limb that GAINS patches takes its bones out of the stub set at once (before, only a save and a
    # re-import did: the native pillbug build's 43 stub bones live, 4 after re-opening the same creature)
    wing = next(j for j, l in enumerate(src.limbs) if not l.prims and l.bone_names)
    wing_bones = {bidx(src, str(b)) for b in src.limbs[wing].bone_names}
    assert wing_bones <= stubs, (wing, wing_bones - stubs)
    pl4 = pl.copy(); pl4[:4] = wing
    assert ES(src, structure.source_rows(src), pl4, own) == stubs - wing_bones
    # the fast and the grown path insert at the same place (after head: endtail sorts before it now) and flag endtail
    W = R.bind_world_matrices(src)[head].copy(); W[:3, 3] += 0.02
    horn = [AB("horn_l", n, head, tuple(W.reshape(-1)), limb_default=True)]
    fast = core.build_export(core.EditState(CHEETAH, data.verts, limbs=rows, patch_limb=pl, added_bones=horn))
    grown = core.build_export(grown_state(src, data, rows, horn))
    assert grown.grown and not fast.grown
    assert fast.insert.at == grown.insert.at == head + 1, (fast.insert.at, grown.insert.at)
    assert fast.stub_flagged == grown.stub_flagged == ["endtail"], (fast.stub_flagged, grown.stub_flagged)
    for b in (fast, grown):
        m = b.model
        assert m.bones[head + 1].name == "horn_l" and m.bones[m.bones[head + 1].parent].name == "head"
        assert all(x.parent is None or x.parent < i for i, x in enumerate(m.bones))
        assert [x.name for x in m.bones if x.name != "horn_l"] == [x.name for x in src.bones]
    # without the new stub limb both insert before the source's stub bones
    plain = [core.build_export(core.EditState(CHEETAH, data.verts, added_bones=horn)).insert.at,
             core.build_export(grown_state(src, data, None, horn)).insert.at]
    assert plain == [min(stubs)] * 2, plain
    # a bone under endtail: both paths refuse it; skinned to endtail (no stub bone then): both take it
    Wt = R.bind_world_matrices(src)[endtail].copy(); Wt[:3, 3] += 0.02
    tip = [AB("tail_tip", n, endtail, tuple(Wt.reshape(-1)), limb_default=True)]
    raises(lambda: core.build_export(core.EditState(CHEETAH, data.verts, limbs=rows, patch_limb=pl, added_bones=tip)),
           ("'tail_tip' is added under 'endtail', a stub bone",))
    raises(lambda: core.build_export(grown_state(src, data, rows, tip)), ("'tail_tip' is added under 'endtail', a stub bone",))
    ats = [core.build_export(core.EditState(CHEETAH, data.verts, vert_owner=o2, limbs=rows, patch_limb=pl,
                                            added_bones=tip)).insert.at,
           core.build_export(grown_state(src, data, rows, tip, owners=o2)).insert.at]
    assert ats == [min(stubs)] * 2, ats
    say(f"6 EXPORT STUBS: flagged stubs + the point-less bones of ANY live patchless limb (not a bone with points, not "
        f"a row with a patch); the Head limb losing its patches makes {len(idle)} of its bones stubs and a stub limb "
        f"gaining patches takes its {len(wing_bones)} out; a new stub limb naming endtail: fast and grown insert horn_l at "
        f"{fast.insert.at} (after head) and flag endtail alike, a bone under endtail refused by both, taken by both once "
        "endtail owns points")


# ------------------------------------------------------------------------------------------------------------- 4
def part_corpus():
    n_ok = pts = corners = 0
    worst = 0.0
    t0 = time.perf_counter()
    for f in ENGINE:
        m = SP.read(f)
        data = core.creature_arrays_from_model(m, f)
        n = len(m.bones)
        stubs = structure.stub_bone_indices(m)
        own = np.asarray(R.vertex_bone_owners(m), dtype=np.int64)
        hb = next((i for i, b in enumerate(m.bones) if b.name.lower() == "head"), -1)
        if hb < 0 or hb in stubs or not (own == hb).any():       # no head owning points: the bone owning the most
            hb = int(np.argmax(np.bincount(own[own >= 0], minlength=n)))
        bw = R.bind_world_matrices(m)
        W = bw[hb].copy(); W[:3, 3] += 0.01
        # a few of its points skinned to the added leaf (two patch corners among them): reskin, aux layout, owner remap
        cm = core.corner_mask(core.source_table(m), len(m.vertices))
        mine = np.nonzero(own == hb)[0]
        top = np.concatenate([mine[cm[mine]][:2], mine[~cm[mine]][:3]])
        vo = own.copy(); vo[top] = n
        built = core.build_export(core.EditState(f, data.verts, vert_owner=vo, added_bones=[
            AB("added_tip", n, hb, tuple(W.reshape(-1)), limb_default=True)]))
        mm = built.model
        ai = bidx(mm, "added_tip")
        assert built.insert.at == ai and ai == max(min(stubs) if stubs else n, hb + 1), (f, ai)
        own1 = np.asarray(R.vertex_bone_owners(mm))
        assert sorted(built.o2n[top].tolist()) == sorted(np.nonzero(own1 == ai)[0].tolist()), f
        assert core.aux_rule_problems(mm) == [], (os.path.basename(f), core.aux_rule_problems(mm)[:3])
        assert mm.bones[ai].meta5 == int(cm[top].sum()), (f, mm.bones[ai].meta5)
        err = float(np.abs(np.asarray(R.world_vertices(mm), dtype=np.float64)[built.o2n]
                           - np.asarray(R.world_vertices(m), dtype=np.float64)).max())
        assert err < 1e-6, (f, err)
        worst, pts, corners = max(worst, err), pts + len(top), corners + int(cm[top].sum())
        hl = [l.name for l in m.limbs if m.bones[hb].name.lower() in {str(b).lower() for b in l.bone_names}]
        assert limbs_of(mm, "added_tip") == hl[:1], (f, limbs_of(mm, "added_tip"), hl)
        rb, _p = write_read(mm, "corpus.sgm")
        assert len(rb.bones) == n + 1 and rb.find_bone("added_tip") is not None, f
        g0, g1 = gate(m), gate(rb)
        # the scripted points (two corners, three others) leave some handles off their nearest corner's bone: the
        # advisory check_model adds for that is expected here (part_build pins it)
        g1 = ([x for x in g1[0] if "not their nearest corner's" not in x], g1[1])
        g1 = (g1[0], sorted(x.replace(f"the file has {n + 1}", f"the file has {n}") for x in g1[1]))  # woolly's SKEL INFO
        assert g0 == g1, (os.path.basename(f), set(g0[0]) ^ set(g1[0]), set(g0[1]) ^ set(g1[1]))
        assert all(b.parent is None or b.parent < i for i, b in enumerate(rb.bones))
        n_ok += 1
    say(f"4a CORPUS ADD: a leaf under the head (else the bone owning most points) of {n_ok}/{len(ENGINE)} base-game "
        f"creatures owning {pts} of its points ({corners} patch corners): before the stub bones, the points on it, "
        f"aux count = its corners and every bone on the corners-first rule, world within {worst:.1e}, in the parent's "
        f"limb, reads back with load gate + rulebook unchanged ({time.perf_counter() - t0:.1f}s)")
    assert n_ok == len(ENGINE) == 61

    exact = meta0 = meta1 = signs = 0
    for f in ENGINE:
        raw = open(f, "rb").read()
        m = SP.read(f)
        stubs = structure.stub_bone_indices(m)
        i = (min(stubs) if stubs else len(m.bones)) - 1
        b = m.bones[i]
        assert b.parent is not None and not b.vertex_count and not any(x.parent == i for x in m.bones), (f, b.name)
        bw = R.bind_world_matrices(m)
        cut = copy.deepcopy(m)
        del cut.bones[i]
        for x in cut.bones:
            if x.parent is not None and int(x.parent) > i:
                x.parent = int(x.parent) - 1
        if cut.skel_info is not None and int(cut.skel_info) == len(m.bones):
            cut.skel_info = len(cut.bones)
        rows = []
        for li, l in enumerate(cut.limbs):
            if b.name in l.bone_names:
                l.bone_names = [x for x in l.bone_names if x != b.name]
            rows.append(structure.LimbRow(li, l.name, int(l.limb_id) & 0xFFFFFFFF, tuple(m.limbs[li].bone_names)))
        p = os.path.join(TMP, "cut.sgm")
        with open(p, "wb") as fh:
            fh.write(SP.write(cut))
        core.clear_source_cache()
        d = core.creature_arrays_from_model(cut, p)
        built = core.build_export(core.EditState(p, d.verts, limbs=rows, added_bones=[
            AB(b.name, len(cut.bones), int(b.parent), tuple(bw[i].reshape(-1)))]))
        nb = built.model.bones[i]
        assert nb.name == b.name and built.insert.at == i
        assert np.array_equal(np.float32(nb.transform), np.float32(b.transform)), (f, b.name)
        assert (nb.parent, nb.start_vertex, nb.vertex_count, nb.meta5, list(nb.tail)) == \
               (b.parent, b.start_vertex, b.vertex_count, b.meta5, list(b.tail)), (f, b.name)
        meta0 += int(nb.meta0) == int(b.meta0)
        meta1 += int(nb.meta1) == int(b.meta1)
        signs += np.float32(nb.tail).tobytes() == np.float32(b.tail).tobytes()
        nb.meta0, nb.meta1, nb.tail = b.meta0, b.meta1, list(b.tail)
        exact += SP.write(built.model) == raw
    assert exact == 61, exact
    say(f"4b REMOVE THEN ADD: the last non-stub leaf bone of 61 creatures taken out and added back: position, parent, "
        f"range, aux count, transform (float32) and joint limits (values) = stock 61/61; the whole file = the stock bytes "
        f"61/61 once the flags word ({meta0}/61 by the parent rule), colour word ({meta1}/61) and the joint limits' zero "
        f"signs ({signs}/61 are +0.0) are the stock ones")


def main():
    os.makedirs(TMP, exist_ok=True)
    part_insert()
    m = part_build()
    part_clips(m)
    part_limb_identity()
    part_export_stubs()
    part_corpus()
    with open("/tmp/bz_bone_add_done.txt", "w") as f:
        f.write("ok\n")
    say("ALL PASS")


if __name__ == "__main__":
    main()
