"""Bone identity and renames, bpy-free (increment B6).
    python blender_native/tests/bone_rename_test.py

1. structure.resolve_bones: a stamped rename, a duplicate whose original keeps its name, a duplicate whose original was
   renamed (the copy moved: the rest tells; not moved: ambiguous), a Symmetrize copy (another name, the same stamp), a
   rig without stamps (a rename found by rest), deleted / added / re-parented; name_conflicts refuses rename chains
   (A->B then C->A, a swap) and allows a case-only rename; bone_name_problems (empty, non-ASCII, 64 characters, a
   case-only clash); Symmetrize onto an existing counterpart (its stamp overwritten: found by its import name; renamed
   first: refused, naming Symmetrize);
2. structure.respell: exact / lower / upper / mixed-case references follow a rename and come back exactly;
3. the stock corpus (Assets/engine_stock 61 + Assets/stock 58): EVERY bone renamed through core.build_export -- the
   bones, every limb bone list, connection host, IK chain and clip track carry the new name and no old one (tracks
   naming no bone of the skeleton are left alone), the world shape, the load gate (ICParse check_model) and the
   Object Editor's limb warnings are unchanged, IKCH is rebuilt only when a chain names a renamed bone, clip key bytes
   are untouched; renaming back from the renamed file is byte-identical to the stock file;
4. hard-coded names: boner.bone_rename_warnings / the export warnings name what a rename of spine1 / tail1 / pelvis
   breaks in game (tail1: the measured torn seam; Front_ankle_l: no effect observed), a rename to such a name warns, a
   plain name (ear_l) does not; the export summary line; the whale's orphan 'endtail' track: adopted with a warning,
   duplicated refused.
"""
import glob
import os
import sys
import time
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import structure  # noqa: E402
import boner  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.sgm.creature.model import ik_chains  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

TMP = "/tmp/bz_b6"
RB = structure.RigBone


def eye(k):
    return tuple((np.eye(4) + k).reshape(-1))


def resolve_cases():
    snap = [("pelvis", ""), ("spine1", "pelvis"), ("ear_l", "spine1"), ("ear_r", "spine1"), ("tail1", "pelvis")]
    rest = [eye(i) for i in range(5)]

    def rig(names=None, extra=(), ids=True, rests=None, parents=None):
        names = names or {}
        out = []
        for i, (n, p) in enumerate(snap):
            if n in names and names[n] is None:
                continue
            nm = names.get(n, n)
            par = (parents or {}).get(n, names.get(p, p) if p else "")
            out.append(RB(nm, par, i if ids else None, (rests or {}).get(n, rest[i])))
        return out + list(extra)

    res = structure.resolve_bones(snap, rig(), rest)
    assert res.changes() == [] and res.current == [n for n, _p in snap]
    # a stamped rename: children follow (no re-parent), the map says it
    res = structure.resolve_bones(snap, rig({"spine1": "back1"}), rest)
    assert res.changes() == ["bone 'spine1' was renamed to 'back1'"], res.changes()
    assert res.rename_map() == {1: "back1"} and not structure.name_conflicts([n for n, _ in snap], res)
    # Duplicate keeps the stamp: the original keeps its name -> the copy is added
    res = structure.resolve_bones(snap, rig(extra=[RB("ear_l.001", "spine1", 2, rest[2])]), rest)
    assert res.changes() == ["bone 'ear_l.001' was added"], res.changes()
    # Duplicate, then the original renamed and the copy moved: the rest tells the original
    res = structure.resolve_bones(snap, rig({"ear_l": "ear_left"}, extra=[RB("ear_l.001", "spine1", 2, eye(9))]), rest)
    assert res.renames == [(2, "ear_l", "ear_left")] and res.added == ["ear_l.001"], res.changes()
    # ... the copy not moved: nobody can tell -> ambiguous (refused), neither added nor renamed
    res = structure.resolve_bones(snap, rig({"ear_l": "ear_left"}, extra=[RB("ear_l.001", "spine1", 2, rest[2])]), rest)
    assert res.ambiguous == [("ear_l", ["ear_left", "ear_l.001"])] and not res.renames and not res.added, res.changes()
    assert "none of them can be told to be that bone" in res.changes()[0] and res.current[2] is None
    # Symmetrize on ear_l after ear_r was renamed: the copy 'ear_r' carries ear_l's stamp -> added, and its name is
    # ear_r's import name (a conflict as well)
    res = structure.resolve_bones(snap, rig({"ear_r": "ear_right"}, extra=[RB("ear_r", "spine1", 2, eye(7))]), rest)
    assert res.renames == [(3, "ear_r", "ear_right")] and res.added == ["ear_r"], res.changes()
    conf = structure.name_conflicts([n for n, _ in snap], res)
    assert len(conf) == 1 and "bone 'ear_r' was renamed to 'ear_right' and bone 'ear_r' now has its old name" in conf[0]
    # Symmetrize onto a counterpart that EXISTS copies the selected bone's stamp onto it (ear_r now carries ear_l's 2):
    # still found by its import name -> no change but its rest (and so with ear_l's rest edited too)
    for moved in ({}, {"ear_l": eye(6)}):
        bones = [b if b.name != "ear_r" else RB("ear_r", "spine1", 2, eye(7)) for b in rig(rests=moved)]
        res = structure.resolve_bones(snap, bones, rest)
        assert res.changes() == [] and res.current == [n for n, _p in snap] and not res.overwritten, res.changes()
    # ... the pair renamed first (ear_left / ear_right) and Symmetrize run on ear_left: ear_right's stamp overwritten,
    # no name to find it by -> refused, saying what happened (not a plain delete + add)
    for moved in ({}, {"ear_l": eye(6)}):
        bones = [b if b.name != "ear_right" else RB("ear_right", "spine1", 2, eye(7))
                 for b in rig({"ear_l": "ear_left", "ear_r": "ear_right"}, rests=moved)]
        res = structure.resolve_bones(snap, bones, rest)
        assert res.overwritten and res.overwritten[0][0] == "ear_l" and "ear_right" in res.overwritten[0][1] \
            and res.overwritten[0][2] == ["ear_r"], res.overwritten
        assert "Blender's Symmetrize" in res.changes()[0] and "'ear_r'" in res.changes()[0], res.changes()
    # a Duplicate copy (name.001) while another bone was deleted is no Symmetrize overwrite
    res = structure.resolve_bones(snap, rig({"tail1": None}, extra=[RB("ear_l.001", "spine1", 2, eye(9))]), rest)
    assert not res.overwritten and res.added == ["ear_l.001"] and res.deleted == ["tail1"], res.changes()
    # a rig imported before the stamps: a rename is found by its rest, a moved one is delete + add
    res = structure.resolve_bones(snap, rig({"tail1": "tail_a"}, ids=False), rest)
    assert res.by_rest == [(4, "tail1", "tail_a")] and not res.stamped, res.changes()
    res = structure.resolve_bones(snap, rig({"tail1": "tail_a"}, ids=False, rests={"tail1": eye(8)}), rest)
    assert res.deleted == ["tail1"] and res.added == ["tail_a"], res.changes()
    # deleted, re-parented (onto a renamed parent: not a re-parent; onto another bone: one)
    res = structure.resolve_bones(snap, rig({"ear_r": None}), rest)
    assert res.changes() == ["bone 'ear_r' was deleted"]
    res = structure.resolve_bones(snap, rig({"spine1": "back1"}, parents={"tail1": "back1"}), rest)
    assert res.reparented == [("tail1", "pelvis", "back1")], res.changes()
    # chains: A->B then C->A is refused; a swap is refused; a case-only rename is not a conflict
    names = [n for n, _ in snap]
    res = structure.resolve_bones(snap, rig({"ear_l": "ear_left", "ear_r": "ear_l"}), rest)
    conf = structure.name_conflicts(names, res)
    assert len(conf) == 1 and "'ear_l' would mean two bones" in conf[0] and "(it was 'ear_r')" in conf[0], conf
    res = structure.resolve_bones(snap, rig({"ear_l": "ear_r", "ear_r": "ear_l"}), rest)
    assert len(structure.name_conflicts(names, res)) == 2
    res = structure.resolve_bones(snap, rig({"ear_l": "Ear_L"}), rest)
    assert res.renames == [(2, "ear_l", "Ear_L")] and not structure.name_conflicts(names, res)
    # a created stub bone renamed to a source bone's import name conflicts too
    res = structure.resolve_bones(snap, rig({"ear_l": "ear_left"}), rest)
    assert structure.name_conflicts(names, res, extra=[("LSWing", "ear_l")])
    # name rules on renamed names
    P = structure.bone_name_problems
    assert P(["a", "b"], ["b"]) == []
    assert "is empty" in P(["a", ""], [""])[0]
    assert "outside plain ASCII" in P(["a", "bé"], ["bé"])[0]
    assert "64 characters" in P(["a", "x" * 64], ["x" * 64])[0]
    assert "differ only in case" in P(["Ear_l", "ear_l"], ["Ear_l"])[0]
    assert "starts or ends with a space" in P(["a", "b "], ["b "])[0]
    assert boner.bone_name_error("Ear_R", ["ear_r"]) and boner.bone_name_error("ear_left", ["ear_r"]) is None
    print("RESOLVE_OK 23 cases")


def respell_cases():
    S = structure.respell
    for ref, old, new, want in (("ear_l", "ear_l", "ear_left", "ear_left"), ("front_leg1_l", "Front_leg1_l", "Front_Leg_A",
                                "front_leg_a"), ("TAIL1", "tail1", "tail_one", "TAIL_ONE"),
                                ("Feather1_l", "feather1_L", "feather1_L_rn", "Feather1_l_rn"),
                                ("EndWing_l", "EndWing_L", "wingtip_L", "wingtip_l")):
        got = S(ref, old, new)
        assert got == want, (ref, old, new, got)
        if ref != "EndWing_l":            # its respelling is all lower case: back, it reads as a lower-case reference
            assert S(got, new, old) == ref, (got, new, old, S(got, new, old))
    # tracks (lower_first): a lower-case track of a lower-case bone renamed to a mixed-case name comes back lower case
    assert S("ear_l", "ear_L", "ear_left", True) == "ear_left" and S("ear_left", "ear_left", "ear_L", True) == "ear_l"
    assert S("ear_left", "ear_left", "ear_L") == "ear_L"
    assert S("neck1", "neck1", "Neck_A", True) == "neck_a" and S("neck_a", "Neck_A", "neck1", True) == "neck1"
    r = structure.BoneRenamer([("spine1", "back1")])
    assert r("SPINE1") == "BACK1" and r("neck1") == "neck1" and r.inverse()("back1") == "spine1"
    try:
        structure.BoneRenamer([("a", "b"), ("A", "c")])
        raise AssertionError("case-duplicate source names accepted")
    except ValueError as e:
        assert "ignoring case" in str(e)
    print("RESPELL_OK")


def _refs(m):
    """Every bone reference of the file: limb lists, hosts, IK chain bones, clip tracks (lower-cased)."""
    out = {"limbs": [b for l in m.limbs for b in l.bone_names], "hosts": [c.bone_name for c in m.limb_connections],
           "ik": [b for c in (ik_chains(m) or []) for b in c.bones],
           "tracks": [t.bone_name for a in m.animations for t in a.tracks]}
    return out


def _key_bytes(m):
    return [(a.name, [(tuple(map(tuple, t.rot_keys or [])), tuple(map(tuple, t.trans_keys or [])), t.flag) for t in a.tracks])
            for a in m.animations]


def corpus():
    files = sorted(glob.glob(os.path.join(REPO, "Assets", "engine_stock", "*.sgm"))) + \
        sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
    assert files, "no stock creatures"
    os.makedirs(TMP, exist_ok=True)
    t0 = time.time()
    ik_rebuilt = ik_kept = tracks = 0
    skipped = []
    for f in files:
        raw = open(f, "rb").read()
        src = SP.read(f)
        low = {b.name.lower() for b in src.bones}
        if SP.write(src) != raw:
            skipped.append(os.path.basename(f))           # poison_frog: ICParse's writer is not exact on it (known)
            continue
        if len(low) != len(src.bones):
            print("  skip (case-duplicate bone names):", os.path.basename(f))
            continue
        ren = {i: f"{b.name}_rn" for i, b in enumerate(src.bones)}
        verts = core.load_creature(f).verts
        b = core.build_export(core.EditState(source_path=f, verts=verts, bone_renames=ren))
        m = b.model
        data = SP.write(m)
        assert [x.name for x in m.bones] == [f"{x.name}_rn" for x in src.bones]
        refs, srcrefs = _refs(m), _refs(src)
        for kind in refs:
            for a, s in zip(refs[kind], srcrefs[kind]):
                if s.lower() in low:
                    assert a.lower() == s.lower() + "_rn", (f, kind, s, a)
                else:
                    assert a == s and kind == "tracks", (f, kind, s, a)     # a track naming no bone: left alone
        assert np.array_equal(np.asarray(R.world_vertices(m)), np.asarray(R.world_vertices(src))), f
        cm_a = [x.replace("_rn:", ":").replace("_rn ", " ").replace("_rn'", "'") for x in ICval.check_model(m)]
        assert cm_a == ICval.check_model(src), (f, cm_a)  # (the layout lines name bones)
        oe_a = [x.replace("_rn'", "'") for x in ICval.check_oe_structure(m)]
        assert oe_a == ICval.check_oe_structure(src), (f, oe_a, ICval.check_oe_structure(src))
        assert _key_bytes(m) == _key_bytes(src), f
        if src.ikch_raw is not None:
            if srcrefs["ik"]:
                ik_rebuilt += m.ikch_raw != src.ikch_raw
            else:
                assert m.ikch_raw is src.ikch_raw or m.ikch_raw == src.ikch_raw
                ik_kept += 1
        tracks += b.renames.tracks
        assert b.renames.renamed and len(b.renames.renamed) == len(src.bones)
        summ = core.export_summary(b)
        assert summ.parts and summ.parts[0].startswith(f"bones: renamed {len(src.bones)} ("), summ.parts
        assert not any(p.startswith(("limbs:", "rings:", "connections:")) for p in summ.parts), summ.parts
        # there and back: the renamed file re-imported, every bone renamed back -> the stock bytes
        tmp = os.path.join(TMP, "rn_" + os.path.basename(f))
        open(tmp, "wb").write(data)
        back = core.build_export(core.EditState(source_path=tmp, verts=core.load_creature(tmp).verts,
                                                bone_renames={i: x.name for i, x in enumerate(src.bones)}))
        assert SP.write(back.model) == raw, f"rename there and back is not byte-identical: {f}"
        os.remove(tmp)
    assert skipped == ["poison_frog.sgm"] or len(files) != 119, skipped
    print(f"CORPUS_OK {len(files) - len(skipped)} creatures (skipped, the writer is not exact on them: {skipped}): every "
          f"bone renamed and back byte-identical; {tracks} clip tracks "
          f"followed; IKCH rebuilt on {ik_rebuilt}, chain-less IKCH kept on {ik_kept} ({time.time() - t0:.0f} s)")


def warnings_and_refusals():
    f = os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
    src = SP.read(f)
    verts = core.load_creature(f).verts
    idx = {b.name: i for i, b in enumerate(src.bones)}
    W = boner.bone_rename_warnings
    assert W([("ear_l", "ear_left")]) == []
    w = W([("spine1", "back1")])
    assert len(w) == 1 and "procedural animation" in w[0] and "creature scaling" in w[0], w
    assert "tail ground clearance" in W([("tail1", "tail_a")])[0]
    assert "measured in ComboTest.exe" in W([("tail1", "tail_a")])[0] and "seam tears" in W([("tail1", "tail_a")])[0]
    assert "seam skinning" in W([("pelvis", "hips")])[0]
    w = W([("Front_ankle_l", "front_hock_l")])     # looked up by name, but renaming it changed no ComboTest export
    assert len(w) == 1 and "no effect observed" in w[0] and "limb and leg lookups" not in w[0], w
    assert "treats a bone called 'front_arm1_l' specially" in W([("ear_l", "front_arm1_l")])[0]
    assert W([("spine1", "Spine1")]) == []                            # case only: the lookups ignore case
    b = core.build_export(core.EditState(source_path=f, verts=verts, bone_renames={idx["spine1"]: "back1",
                                                                                 idx["ear_L"]: "ear_left"}))
    assert [x for x in b.warnings if "'spine1' renamed to 'back1'" in x] and not [x for x in b.warnings if "ear_L" in x]
    line = core.export_summary(b).line
    assert "bones: renamed 2 (spine1 -> back1, ear_L -> ear_left)" in line, line
    # a bone in no IK chain: IKCH keeps its bytes (the chunk object itself); a bone in one: only that chain changes
    in_ik = {x.lower() for c in ik_chains(src) for x in c.bones}
    free = next(x.name for x in src.bones if x.name.lower() not in in_ik)
    b = core.build_export(core.EditState(source_path=f, verts=verts, bone_renames={idx[free]: free + "_x"}))
    assert b.model.ikch_raw == src.ikch_raw and b.renames.ik_chains == 0, free
    b = core.build_export(core.EditState(source_path=f, verts=verts, bone_renames={idx["neck2"]: "neck_b"}))
    before, after = ik_chains(src), ik_chains(b.model)
    diff = [(x.name, y.bones) for x, y in zip(before, after) if x != y]
    assert diff == [("Neck", [("neck_b" if n == "neck2" else n) for n in next(c.bones for c in before if c.name == "Neck")])], diff
    assert b.renames.ik_chains == 1 and b.renames.limbs == 1 and b.renames.connections == 0
    # refusals: a name another bone has ignoring case, a name the file can't hold, an index outside the skeleton
    for ren, words in (({idx["ear_L"]: "EAR_R"}, "differ only in case"), ({idx["ear_L"]: "x" * 64}, "64 characters"),
                       ({999: "x"}, "bone index 999")):
        try:
            core.build_export(core.EditState(source_path=f, verts=verts, bone_renames=ren))
            raise AssertionError(f"not refused: {ren}")
        except ValueError as e:
            assert words in str(e), (words, str(e))
    # the humpback whale's clips carry an 'endtail' track naming no bone (its bone is end_tail): renaming a bone to
    # 'endtail' adopts those keys (a warning naming the clips); renaming a bone whose own track then duplicates it is
    # refused
    wf = os.path.join(REPO, "Assets", "stock", "humpback_whale.sgm")
    wv = core.load_creature(wf).verts
    wsrc = SP.read(wf)
    widx = {b.name: i for i, b in enumerate(wsrc.bones)}
    assert "endtail" not in {b.name.lower() for b in wsrc.bones}
    assert all("endtail" in [t.bone_name for t in a.tracks] for a in wsrc.animations)
    try:
        core.build_export(core.EditState(source_path=wf, verts=wv, bone_renames={widx["tail4"]: "endtail"}))
        raise AssertionError("tail4 -> endtail not refused")
    except ValueError as e:
        assert "two 'endtail' tracks" in str(e), str(e)
    b = core.build_export(core.EditState(source_path=wf, verts=wv, bone_renames={widx["end_tail"]: "endtail"}))
    adopt = [x for x in b.warnings if "already carry a 'endtail' track that named no bone" in x]
    assert len(adopt) == 1 and "bone 'end_tail' renamed to 'endtail'" in adopt[0], b.warnings
    b = core.build_export(core.EditState(source_path=wf, verts=wv, bone_renames={widx["end_tail"]: "tail_end"}))
    assert not [x for x in b.warnings if "named no bone" in x], b.warnings
    print("WARNINGS_OK")


def main():
    resolve_cases()
    respell_cases()
    warnings_and_refusals()
    corpus()
    print("BONE_RENAME_PASS")


if __name__ == "__main__":
    main()
