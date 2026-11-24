"""Delete / re-parent source bones, IK chains and joint limits without Blender (Boner increment C8).
    python blender_native/tests/bone_topology_test.py

Needs the base-game creatures (Assets/engine_stock).
1. topology.apply_skeleton on cheetah: a point-less leaf deleted (endear_l): the record gone, every other record's
   words kept, parent indices remapped, the index map, the limb list and the LEar IK chain without it, no clip rebuilt
   (it has no track); a bone with points and a child deleted (ear_L): its 50 points on the head (every point's world
   kept through the reskin), endear_l under head with its bind world kept, 15 clips rebuilt with the ear_l track dropped
   and every other track byte-identical, the LEar chain 1 bone; the summary names all of it;
2. re-parent: nose under jaw1 (a genuine re-parent): the record's parent, its bind world kept, its clip tracks
   re-expressed so the animated world is jaw1(t) @ B_new @ inv(B_old) @ L_old(t) -- the pose Blender plays for the same
   Action keys -- and every other bone's motion unchanged; neck4 inserted under neck3 with the head under it: neck4's
   record right before the head's (no source record leaves its order), every source bone's bind world and animated
   world unchanged in all 20 clips, the Neck IK chain completed with neck4, 18 kept clips rebuilt; head under tail4:
   parents before children after the re-sort (apply_skeleton), and the builder refuses it as a split Head limb;
3. refusals (topology.topology_refusals and the builder): the root, a looked-up name (tail1: the reason), a stub bone
   (front_arm1_l), a connection host (neck1), the only bone of a limb, a bone made a root, re-parented under a stub
   bone, a cycle, under a deleted bone, a kept bone whose parent was deleted and not re-parented (apply_skeleton);
4. IK: chain_path_problem / fix_ik_chains cases (a middle bone taken out keeps the path; separate branches drop the
   chain with a warning; completion spelt like the skeleton, alphabetical order kept); every stock chain (119 creatures)
   is a hierarchy path (ik_chain_problems empty); IKRow table: the source rows write the source bytes, a chain added /
   deleted / renamed / re-worded gives exactly that chunk, an unknown bone is refused;
5. joint limits: apply_bone_limits rewrites only the changed record (float32-equal values keep the bytes), a wrong
   length is refused, an added bone's limits are written;
6. corpus: on every base-game creature, one leaf bone that owns no points and no rule refuses is deleted through the
   builder: the file reads back, every point's world and every other bone's animated world unchanged, the load gate
   and limb-list lines gain nothing, clips carrying its track rebuilt with the other tracks byte-identical.
"""
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
import topology  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import anim as ICanim  # noqa: E402
from ICParse.codecs.pose import bind_local_matrix  # noqa: E402
from ICParse.sgm.creature.model import ik_chains  # noqa: E402

T0 = time.perf_counter()
STOCK = os.path.join(REPO, "Assets", "engine_stock")
CHEETAH = os.path.join(STOCK, "cheetah.sgm")


def say(*a):
    print(f"[{time.perf_counter() - T0:6.1f}s]", *a, flush=True)


def idx_of(m, name):
    return next(i for i, b in enumerate(m.bones) if b.name.lower() == name.lower())


def state(path, src, **kw):
    return core.EditState(source_path=path, verts=core.sgm_to_blender(R.world_vertices(src)), **kw)


def local12(M):
    return [float(x) for x in (list(M[:3, 0]) + list(M[:3, 1]) + list(M[:3, 2]) + list(M[:3, 3]))]


def track_bytes(m):
    """{(clip, bone lower): BANM bytes} of every decoded clip."""
    out = {}
    for a in m.animations:
        form = ICanim.write_anim(a)
        banm = [c for c in form.children if c.tag == "BANM"]
        for t, c in zip(a.tracks, banm):
            out[(a.name, t.bone_name.lower())] = c.serialize()
    return out


def reread(m, path="/tmp/bz_c8_topo_read.sgm"):
    with open(path, "wb") as f:
        f.write(SP.write(m))
    return SP.read(path)


def world_by_name(m, a=None, t=0.0):
    W = R.animated_bone_world(m, a, t) if a is not None else R.bind_world_matrices(m)
    return {b.name.lower(): W[i] for i, b in enumerate(m.bones)}


def record_words(b):
    return (b.name, [float(np.float32(x)) for x in b.transform], [float(np.float32(x)) for x in b.tail], int(b.meta0),
            int(b.meta1), int(b.start_vertex), int(b.vertex_count), int(b.meta5))


# ------------------------------------------------------------------------------------------------------------- 1
def test_delete():
    src = SP.read(CHEETAH)
    n = len(src.bones)
    src_tracks = track_bytes(src)
    # a. a point-less leaf: endear_l
    d = idx_of(src, "endear_l")
    topo = topology.BoneTopology(deleted=(d,))
    b = core.build_export(state(CHEETAH, src, bone_topology=topo))
    m = b.model
    assert len(m.bones) == n - 1 and m.find_bone("endear_l") is None
    src_words = {x.name: record_words(x) for x in src.bones}
    for x in m.bones:
        assert record_words(x) == src_words[x.name], x.name
        assert (m.bones[x.parent].name if x.parent is not None else None) == \
            (src.bones[src.bones[idx_of(src, x.name)].parent].name if src.bones[idx_of(src, x.name)].parent is not None else None)
    ins = b.topology
    assert ins.index[d] == -1 and all(ins.index[i] == (i if i < d else i - 1) for i in range(n) if i != d)
    assert ins.deleted == ["endear_l"] and not ins.reparented and ins.resorted == 0 and not ins.moved_points
    assert ins.limbs == [("endear_l", "Cheetah_Head")]
    assert "endear_l" not in {bn.lower() for l in m.limbs for bn in l.bone_names}
    assert [c.bones for c in ik_chains(m) if c.name == "LEar"] == [["ear_L"]], ik_chains(m)
    assert ins.ik.rebuilt == ["LEar: endear_l taken out (1 bone left)"] and not ins.ik.dropped
    assert ins.clips.clips == [] and track_bytes(m) == src_tracks, "a bone with no track: no clip rebuilt"
    assert b.o2n is None and m.vertices == src.vertices, "no points moved: the pool keeps its bytes"
    m2 = reread(m)
    assert sorted(set(ICval.check_model(m2)) - set(ICval.check_model(src))) == []
    assert sorted(set(ICval.check_limb_bones(m2)) - set(ICval.check_limb_bones(src))) == []
    line = core.export_summary(b).line
    assert "bones: deleted 1 (endear_l), taken off limb list(s) Cheetah_Head" in line and "IK chains: 1 rebuilt" in line, line
    assert "clips:" not in line, line
    say("1a endear_l deleted: record gone, 53 records word-identical, parents remapped, limb list + LEar chain without it, "
        "no clip rebuilt, pool bytes kept, load gate unchanged")
    # b. ear_L: 50 points, a child (endear_l goes under head), 15 clips with a track
    ear, endear, head = idx_of(src, "ear_L"), idx_of(src, "endear_l"), idx_of(src, "head")
    bw = R.bind_world_matrices(src)
    rest = {endear: local12(np.linalg.inv(bw[head]) @ bw[endear])}
    topo = topology.BoneTopology(deleted=(ear,), parents=((endear, head),))
    b = core.build_export(state(CHEETAH, src, bone_topology=topo, bone_rest=rest))
    m = b.model
    ins = b.topology
    assert m.find_bone("ear_L") is None and len(m.bones) == n - 1
    assert ins.moved_points == [("ear_L", "head", 50)] and ins.reparented == [("endear_l", "ear_L", "head")], ins
    e2 = idx_of(m, "endear_l")
    assert m.bones[m.bones[e2].parent].name == "head"
    nb = world_by_name(m)
    ob = world_by_name(src)
    assert max(np.abs(nb[k] - ob[k]).max() for k in nb) < 1e-9, "bind worlds kept (endear_l under head included)"
    assert b.o2n is not None
    w_src = R.world_vertices(src)
    w_new = R.world_vertices(m)
    assert np.abs(w_new[b.o2n] - w_src).max() < 1e-6, "every point's world kept through the reskin"
    own = np.asarray(R.vertex_bone_owners(m))
    src_own = np.asarray(R.vertex_bone_owners(src))
    assert int((own[b.o2n[src_own == ear]] == idx_of(m, "head")).sum()) == 50
    assert len(ins.clips.clips) == 15 and ins.clips.dropped_tracks == 15 and ins.clips.retracked == 0
    new_tracks = track_bytes(m)
    assert not [k for k in new_tracks if k[1] == "ear_l"]
    same = [k for k in src_tracks if k[1] != "ear_l"]
    assert all(new_tracks[k] == src_tracks[k] for k in same) and len(new_tracks) == len(same), "other tracks byte-identical"
    for a2 in m.animations:
        a1 = next(a for a in src.animations if a.name == a2.name)
        assert (a1.bbox1, a1.bbox2, a1.frame_count, a1.fps) == (a2.bbox1, a2.bbox2, a2.frame_count, a2.fps)
        for t in (0.0, 0.31, 0.77, 1.0):
            W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
            assert max(np.abs(W1[k] - W2[k]).max() for k in W2 if k != "endear_l") < 1e-9, (a2.name, t)
    assert [c.bones for c in ik_chains(m) if c.name == "LEar"] == [["endear_l"]]
    m2 = reread(m)
    assert sorted(set(ICval.check_model(m2)) - set(ICval.check_model(src))) == []
    assert sorted(set(ICval.check_limb_bones(m2)) - set(ICval.check_limb_bones(src))) == []
    assert not core.aux_rule_problems(m2)
    s = core.export_summary(b)
    assert "bones: deleted 1 (ear_L (50 points -> head)), taken off limb list(s) Cheetah_Head" in s.line, s.line
    assert "bones: re-parented 1 (endear_l: ear_L -> head)" in s.line and "clips: 15 kept clip(s) rebuilt (15 track(s) of " \
        "deleted bones dropped" in s.line, s.line
    assert any(w.startswith("50 control points of deleted bone 'ear_L' moved to its parent 'head'") for w in s.warnings), s.warnings
    assert not any(w.startswith("rest pose of") for w in s.warnings), s.warnings
    say("1b ear_L deleted: 50 points on head (worlds kept), endear_l under head (bind kept), 15 clips rebuilt with the "
        "ear_l track dropped and every other track byte-identical, motion of every kept bone unchanged, LEar 1 bone")


# ------------------------------------------------------------------------------------------------------------- 2
def test_reparent():
    src = SP.read(CHEETAH)
    n = len(src.bones)
    bw = R.bind_world_matrices(src)
    src_tracks = track_bytes(src)
    # a. nose under jaw1: a genuine re-parent
    nose, jaw, head = idx_of(src, "nose"), idx_of(src, "jaw1"), idx_of(src, "head")
    rest = {nose: local12(np.linalg.inv(bw[jaw]) @ bw[nose])}
    topo = topology.BoneTopology(parents=((nose, jaw),))
    b = core.build_export(state(CHEETAH, src, bone_topology=topo, bone_rest=rest))
    m = b.model
    i = idx_of(m, "nose")
    assert m.bones[m.bones[i].parent].name == "jaw1" and len(m.bones) == n
    assert b.topology.resorted == 1 and i == idx_of(m, "jaw1") + 1, "nose (before jaw1 in the source) moves right after it"
    assert all(x.parent is None or x.parent < j for j, x in enumerate(m.bones)), "parents before children"
    assert b.topology.reparented == [("nose", "head", "jaw1")]
    assert max(np.abs(world_by_name(m)[k] - world_by_name(src)[k]).max() for k in world_by_name(m)) < 1e-9
    B_old, B_new = bind_local_matrix(src.bones[nose].transform), bind_local_matrix(m.bones[i].transform)
    X = B_new @ np.linalg.inv(B_old)
    clips_with = [a.name for a in src.animations if any(t.bone_name.lower() == "nose" for t in a.tracks)]
    assert sorted(b.topology.clips.clips) == sorted(clips_with) and b.topology.clips.retracked == len(clips_with)
    worst_other, worst_nose, worst_mid = 0.0, 0.0, 0.0
    for a2 in m.animations:
        a1 = next(a for a in src.animations if a.name == a2.name)
        K = max(1, a1.frame_count)
        tr = next((x for x in a1.tracks if x.bone_name.lower() == "nose"), None)
        keys = {float(k[0]) for k in (tr.rot_keys + tr.trans_keys)} if tr is not None else set()
        for t in sorted({k / K for k in range(K + 1)} | keys | {0.123, 0.61}):
            W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
            worst_other = max(worst_other, max(np.abs(W1[k] - W2[k]).max() for k in W2 if k != "nose"))
            L_old = np.linalg.inv(W1["head"]) @ W1["nose"]                # the source's local under head
            want = W1["jaw1"] @ X @ L_old                                  # Blender's pose for the same keys
            if t in keys or tr is None:                                    # at the track's keys: exact
                worst_nose = max(worst_nose, float(np.abs(W2["nose"] - want).max()))
            else:                                                          # between keys: the engine blends the new keys
                worst_mid = max(worst_mid, float(np.abs(W2["nose"] - want).max()))
    assert worst_other < 1e-9 and worst_nose < 2e-6 and worst_mid < 1e-3, (worst_other, worst_nose, worst_mid)
    new_tracks = track_bytes(m)
    assert all(new_tracks[k] == src_tracks[k] for k in src_tracks if k[1] != "nose"), "other tracks byte-identical"
    assert [c.bones for c in ik_chains(m) if c.name == "Head"] == [["head", "jaw1", "nose"]], ik_chains(m)   # completed
    assert b.topology.ik.completed == ["Head: jaw1 inserted"]
    m2 = reread(m)
    assert sorted(set(ICval.check_model(m2)) - set(ICval.check_model(src))) == []
    assert sorted(set(ICval.check_limb_bones(m2)) - set(ICval.check_limb_bones(src))) == []
    s = core.export_summary(b)
    assert "bones: re-parented 1 (nose: head -> jaw1), 1 record(s) re-sorted (parents first)" in s.line, s.line
    assert "IK chains: 1 completed (Head: jaw1 inserted)" in s.line, s.line
    assert f"clips: {len(clips_with)} kept clip(s) rebuilt ({len(clips_with)} track(s) re-expressed" in s.line, s.line
    assert not any(w.startswith("rest pose of") for w in s.warnings), s.warnings
    say(f"2a nose under jaw1: parent written, bind kept, {len(clips_with)} clips re-tracked -- nose plays jaw1(t) @ B_new @ "
        f"inv(B_old) @ L_old(t) within {worst_nose:.1e} at the keys ({worst_mid:.1e} m between keys: the engine's key "
        f"blend), every other bone unchanged ({worst_other:.1e}); Head chain completed with jaw1")
    # b. neck4 inserted under neck3 with the head under it
    neck3 = idx_of(src, "neck3")
    W4 = bw[neck3].copy()
    W4[:3, 3] = 0.5 * (bw[neck3][:3, 3] + bw[head][:3, 3])
    ab = structure.AddedBone(name="neck4", key=n, parent=neck3, world=tuple(W4.reshape(-1)))
    rest = {head: local12(np.linalg.inv(W4) @ bw[head])}
    topo = topology.BoneTopology(parents=((head, n),))
    b = core.build_export(state(CHEETAH, src, bone_topology=topo, bone_rest=rest, added_bones=[ab]))
    m = b.model
    i4, ih = idx_of(m, "neck4"), idx_of(m, "head")
    assert ih == i4 + 1 and m.bones[ih].parent == i4 and m.bones[m.bones[i4].parent].name == "neck3"
    assert b.topology.resorted == 0 and b.topology.reparented == [("head", "neck3", "neck4")]
    order_src = [x.name for x in m.bones if x.name != "neck4"]
    assert order_src == [x.name for x in src.bones], "no source record leaves its order"
    assert b.o2n is None
    assert max(np.abs(world_by_name(m)[k] - world_by_name(src)[k]).max() for k in world_by_name(src)) < 1e-9
    worst = 0.0
    for a2 in m.animations:
        a1 = next(a for a in src.animations if a.name == a2.name)
        K = max(1, a1.frame_count)
        for t in [k / K for k in range(K + 1)] + [0.123, 0.61]:
            W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
            worst = max(worst, max(np.abs(W1[k] - W2[k]).max() for k in W1))
    assert worst < 1e-6, worst
    assert [c.bones for c in ik_chains(m) if c.name == "Neck"] == [["head", "neck1", "neck2", "neck3", "neck4"]]
    assert b.topology.ik.completed == ["Neck: neck4 inserted"]
    assert len(b.topology.clips.clips) == 18 and b.topology.clips.retracked == 18
    new_tracks = track_bytes(m)
    assert all(new_tracks[k] == src_tracks[k] for k in src_tracks if k[1] != "head")
    m2 = reread(m)
    assert sorted(set(ICval.check_model(m2)) - set(ICval.check_model(src))) == []
    assert sorted(set(ICval.check_limb_bones(m2)) - set(ICval.check_limb_bones(src))) == []
    assert "neck4" in {bn for l in m.limbs if l.name == "Cheetah_Head" for bn in l.bone_names}
    s = core.export_summary(b)
    assert "bones: added 1 (neck4)" in s.line and "bones: re-parented 1 (head: neck3 -> neck4)" in s.line, s.line
    with open("/tmp/bz_c8_neck4_pure.sgm", "wb") as f:
        f.write(SP.write(m))
    say(f"2b neck4 under neck3, head under neck4: neck4 right before head, bind and every clip's motion unchanged "
        f"({worst:.1e} over 20 clips), Neck chain completed, 18 clips re-tracked, other tracks byte-identical")
    # c. head under front_ankle_r (a later record): a re-sort (apply_skeleton); head under tail4: the builder refuses
    # the split Head limb
    ankle, tail4 = idx_of(src, "front_ankle_r"), idx_of(src, "tail4")
    assert ankle > head
    m = core.read_source(CHEETAH)
    m.bones[head].transform = local12(np.linalg.inv(bw[ankle]) @ bw[head])
    ins = topology.apply_skeleton(m, [], n, None, topology.BoneTopology(parents=((head, ankle),)))
    assert all(x.parent is None or x.parent < j for j, x in enumerate(m.bones)), "parents before children"
    assert ins.resorted > 0 and idx_of(m, "head") == idx_of(m, "front_ankle_r") + 1, ins.resorted
    assert max(np.abs(world_by_name(m)[k] - world_by_name(src)[k]).max() for k in world_by_name(src)) < 1e-9
    stubs = {j for j, x in enumerate(m.bones) if (x.meta0 & 0xFF) == 1}
    assert stubs == set(range(len(m.bones) - 6, len(m.bones))), "stub bones stay last"
    others = [x.name for x in m.bones if x.name != "head"]
    assert others == [x.name for x in src.bones if x.name != "head"], "only the head moved"
    rest = {head: local12(np.linalg.inv(bw[tail4]) @ bw[head])}
    try:
        core.build_export(state(CHEETAH, src, bone_topology=topology.BoneTopology(parents=((head, tail4),)), bone_rest=rest))
        raise AssertionError("a split Head limb was not refused")
    except ValueError as e:
        assert "splits limb 'Cheetah_Head' into 2 branches" in str(e) and "'head'" in str(e), str(e)
    say(f"2c head under front_ankle_r: apply_skeleton re-sorts ({ins.resorted} source record out of order: the head right "
        "after its new parent, stubs last, binds kept); head under tail4: the builder refuses it as a split Head limb")


# ------------------------------------------------------------------------------------------------------------- 3
def test_refusals():
    src = SP.read(CHEETAH)
    n = len(src.bones)
    i = idx_of

    def refused(topo, *words, rules=None):
        out = topology.topology_refusals(src, topo, rules)
        text = "; ".join(out)
        assert out and all(w in text for w in words), (words, out)
        return text
    refused(topology.BoneTopology(deleted=(i(src, "pelvis"),)), "root bone")
    t = refused(topology.BoneTopology(deleted=(i(src, "tail1"),)), "looks 'tail1' up by name", "tail ground clearance")
    refused(topology.BoneTopology(deleted=(i(src, "front_arm1_l"),)), "stub bone", "Cheetah_LFArm")
    refused(topology.BoneTopology(deleted=(i(src, "neck1"),)), "connections 'Cheetah_Head', 'Cheetah_LFArm', 'Cheetah_RFArm' attach")
    rules = topology.rules_of(src, limb_lists=[["endear_l"]] + [list(l.bone_names) for l in src.limbs[1:]])
    refused(topology.BoneTopology(deleted=(i(src, "endear_l"),)), "only bone of limb 'Cheetah_Torso'", rules=rules)
    assert topology.topology_refusals(src, topology.BoneTopology(deleted=(i(src, "endear_l"),))) == []
    assert topology.topology_refusals(src, topology.BoneTopology(deleted=(i(src, "nose"),))) == [], "nose: a universal-set name only"
    refused(topology.BoneTopology(parents=((i(src, "nose"), -1),)), "made a root bone")
    refused(topology.BoneTopology(parents=((i(src, "nose"), i(src, "front_arm1_l")),)), "under 'front_arm1_l', a stub bone")
    refused(topology.BoneTopology(parents=((i(src, "head"), i(src, "nose")),)), "a cycle")
    refused(topology.BoneTopology(deleted=(i(src, "endear_l"),), parents=((i(src, "nose"), i(src, "endear_l")),)),
            "which was deleted")
    refused(topology.BoneTopology(parents=((i(src, "pelvis"), i(src, "spine1")),)), "root bone")
    refused(topology.BoneTopology(parents=((i(src, "nose"), n + 7),)), "a bone the export does not write")
    # apply_skeleton: a kept bone whose parent was deleted and not re-parented
    m = core.read_source(CHEETAH)
    try:
        topology.apply_skeleton(m, [], n, None, topology.BoneTopology(deleted=(i(src, "ear_L"),)))
        raise AssertionError("not refused")
    except ValueError as e:
        assert "its parent 'ear_L' was deleted and it was not re-parented" in str(e), str(e)
    # the builder runs the rules on the export's own tables (a host moved onto the deleted bone's parent passes)
    try:
        core.build_export(state(CHEETAH, src, bone_topology=topology.BoneTopology(
            deleted=(i(src, "tail1"),), parents=((i(src, "tail2"), i(src, "pelvis")),))))
        raise AssertionError("not refused")
    except ValueError as e:
        assert "looks 'tail1' up by name" in str(e) and "connection 'Cheetah_Tail'" not in str(e), str(e)
    say("3 refusals: root, looked-up name (tail1 with its reasons), stub bone, host (3 connections named), the only bone of "
        "a limb, made a root, under a stub, a cycle, under a deleted bone, a child left under a deleted bone; 'nose' "
        "(universal set only) passes")


# ------------------------------------------------------------------------------------------------------------- 4
def test_ik():
    names = ["root", "a", "b", "c", "d", "x"]
    parents = [None, 0, 1, 2, 3, 0]
    assert topology.chain_path_problem(names, parents, ["a", "b", "c"]) is None
    assert topology.chain_path_problem(names, parents, ["c", "a", "b"]) is None                # order inert
    assert topology.chain_path_problem(names, parents, ["a", "c"]) is not None                 # b missing
    assert "separate" not in (topology.chain_path_problem(names, parents, ["a", "c"]) or "") and \
        "does not hang from" in topology.chain_path_problem(names, parents, ["a", "c"])
    assert topology.chain_path_problem(names, parents, ["a", "x"]) is not None
    assert topology.chain_path_problem(names, parents, ["q"]).startswith("names bone 'q'")
    assert topology.chain_path_problem(names, parents, ["a", "a"]) == "lists a bone twice"
    assert topology.chain_path_problem(names, parents, []) is None and topology.chain_path_problem(names, parents, ["a"]) is None
    # fix_ik_chains on the cheetah: LFLeg with Front_leg2_l re-parented under Front_leg1_l and Front_leg2_l... use a
    # synthetic model: chain [a, b, c, d]; b deleted with c under a: path a > c > d keeps; x re-parented so c under x:
    from ICParse.sgm.creature.model import Bone, IKChain, set_ik_chains
    from types import SimpleNamespace

    def model(parents, chains):
        m = SimpleNamespace(bones=[Bone(name=nm, parent=p, start_vertex=0, vertex_count=0, transform=[1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
                                   for nm, p in zip(names, parents)], ikch_raw=None)
        set_ik_chains(m, chains)
        return m
    m = model([None, 0, 0, 1, 3, 0], [IKChain("A", 1, 1, ["a", "b", "c", "d"])])   # b deleted: c now under a
    rep = topology.fix_ik_chains(m, deleted_names=["b"])
    assert [c.bones for c in ik_chains(m)] == [["a", "c", "d"]] and rep.rebuilt == ["A: b taken out"] and not rep.dropped
    m = model([None, 0, 1, 5, 3, 0], [IKChain("A", 1, 1, ["a", "b", "c", "d"])])   # c re-parented under x: branches
    rep = topology.fix_ik_chains(m, reparented_names=["c"])
    assert ik_chains(m) == [] and rep.dropped == ["A"] and "IK chain 'A' dropped" in rep.warnings[0]
    m = model([None, 0, 1, 2, 3, 0], [IKChain("A", 1, 1, ["a", "d"])])            # b, c missing: completed root-to-tip
    rep = topology.fix_ik_chains(m, reparented_names=["d"])
    assert [c.bones for c in ik_chains(m)] == [["a", "b", "c", "d"]] and rep.completed == ["A: b, c inserted"]
    m = model([None, 0, 1, 2, 3, 0], [IKChain("A", 1, 1, ["d", "a"])])            # not alphabetical: root-to-tip
    topology.fix_ik_chains(m, reparented_names=["d"])
    assert [c.bones for c in ik_chains(m)] == [["a", "b", "c", "d"]]
    # every stock chain is a path
    bad, files, chains = [], 0, 0
    for p in sorted(glob.glob(os.path.join(STOCK, "*.sgm")) + glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm"))):
        mm = SP.read(p)
        files += 1
        chains += len(ik_chains(mm) or [])
        bad += [(os.path.basename(p), w) for w in topology.ik_chain_problems(mm)]
    assert not bad, bad[:3]
    # IK rows
    src = SP.read(CHEETAH)
    rows = topology.source_ik_rows(src)
    assert len(rows) == 15 and rows[2] == topology.IKRow(2, "Neck", 2048, 1, ("head", "neck1", "neck2", "neck3"))
    b = core.build_export(state(CHEETAH, src, ik_chains=rows))
    assert SP.write(b.model) == open(CHEETAH, "rb").read() and b.ik_changes == [], "the source rows: byte-identical"
    edited = [r for r in rows if r.name != "REar"] + [topology.IKRow(-1, "Horn", 1, 1, ("head", "nose"))]
    edited[2] = topology.IKRow(2, "NeckChain", 2, 0, ("head", "neck1", "neck2", "neck3"))
    b = core.build_export(state(CHEETAH, src, ik_chains=edited))
    got = ik_chains(b.model)
    assert [c.name for c in got] == [r.name for r in edited] and got[-1].bones == ["head", "nose"] and got[2].enabled == 0
    assert set(b.ik_changes) == {"renamed 1 (Neck -> NeckChain)", "words 1 (NeckChain 2048/1 -> 2/0)", "added 1 (Horn)",
                                 "deleted 1 (REar)"}, b.ik_changes
    line = core.export_summary(b).line
    assert "IK chains: renamed 1 (Neck -> NeckChain); words 1" in line, line
    m2 = reread(b.model)
    assert [c.bones for c in ik_chains(m2)][-1] == ["head", "nose"]
    try:
        core.build_export(state(CHEETAH, src, ik_chains=rows + [topology.IKRow(-1, "Bad", 1, 1, ("head", "horn_l"))]))
        raise AssertionError("not refused")
    except ValueError as e:
        assert "IK chain 'Bad' names bone 'horn_l', which the skeleton lacks" in str(e), str(e)
    b = core.build_export(state(CHEETAH, src, ik_chains=[]))
    assert ik_chains(b.model) == [] and b.model.ikch_raw is not None
    say(f"4 IK: path rule cases; {chains} stock chains of {files} creatures are hierarchy paths; fix cases (middle taken out, "
        "branches dropped, completed root-to-tip / alphabetical); IK rows: source rows byte-identical, rename / words / "
        "add / delete give that chunk, an unknown bone refused, an empty table written")


# ------------------------------------------------------------------------------------------------------------- 5
def test_limits():
    src = SP.read(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    head = idx_of(src, "head")
    same = {head: [float(np.float32(x)) for x in src.bones[head].tail]}
    b = core.build_export(state(CHEETAH, src, bone_limits=same))
    assert SP.write(b.model) == raw and b.limits_changed == []
    vals = list(src.bones[head].tail)
    vals[0], vals[3] = -0.5, 0.5
    b = core.build_export(state(CHEETAH, src, bone_limits={head: vals}))
    m = b.model
    assert b.limits_changed == ["head"] and [float(np.float32(x)) for x in m.bones[head].tail][:4] == [-0.5] + [float(np.float32(x)) for x in vals[1:4]]
    out = SP.write(m)
    assert out != raw and len(out) == len(raw)
    diff = [k for k in range(len(raw)) if raw[k] != out[k]]
    assert len(diff) <= 8, len(diff)
    line = core.export_summary(b).line
    assert "joint limits: 1 bone (head)" in line, line
    try:
        core.build_export(state(CHEETAH, src, bone_limits={head: vals + [1.0]}))
        raise AssertionError("not refused")
    except ValueError as e:
        assert "7 values for a record holding 6" in str(e), str(e)
    n = len(src.bones)
    ab = structure.AddedBone(name="horn_l", key=n, parent=head, world=tuple(R.bind_world_matrices(src)[head].reshape(-1)))
    b = core.build_export(state(CHEETAH, src, added_bones=[ab], bone_limits={n: [-0.1, -0.2, -0.3, 0.1, 0.2, 0.3]}))
    h = b.model.find_bone("horn_l")
    assert [round(x, 3) for x in h.tail] == [-0.1, -0.2, -0.3, 0.1, 0.2, 0.3] and b.limits_changed == ["horn_l"]
    say("5 joint limits: float32-equal values keep the bytes; head's min/max x rewritten changes only its 8 bytes and the "
        "summary says so; a wrong length is refused; an added bone's limits are written")


# ------------------------------------------------------------------------------------------------------------- 6
def test_corpus():
    ok, skipped, fails = 0, [], []
    for p in sorted(glob.glob(os.path.join(STOCK, "*.sgm"))):
        name = os.path.basename(p)[:-4]
        src = SP.read(p)
        n = len(src.bones)
        kids = {}
        for i, b in enumerate(src.bones):
            if b.parent is not None:
                kids.setdefault(int(b.parent), []).append(i)
        pick = None
        for i in range(n - 1, -1, -1):
            b = src.bones[i]
            if kids.get(i) or int(b.vertex_count) or b.parent is None:
                continue
            if topology.topology_refusals(src, topology.BoneTopology(deleted=(i,))):
                continue
            pick = i
            break
        if pick is None:
            skipped.append(name)
            continue
        try:
            bld = core.build_export(state(p, src, bone_topology=topology.BoneTopology(deleted=(pick,))))
            m = reread(bld.model)
            assert len(m.bones) == n - 1 and m.find_bone(src.bones[pick].name) is None
            assert bld.o2n is None and np.abs(R.world_vertices(m) - R.world_vertices(src)).max() < 1e-9
            assert sorted(set(ICval.check_model(m)) - set(ICval.check_model(src))) == []
            assert sorted(set(ICval.check_limb_bones(m)) - set(ICval.check_limb_bones(src))) == []
            gone = src.bones[pick].name.lower()
            st, nt = track_bytes(src), track_bytes(m)
            assert not [k for k in nt if k[1] == gone] and all(nt[k] == st[k] for k in st if k[1] != gone) and \
                len(nt) == len([k for k in st if k[1] != gone])
            for a2 in m.animations[:4]:
                a1 = next(a for a in src.animations if a.name == a2.name)
                for t in (0.0, 0.42, 1.0):
                    W1, W2 = world_by_name(src, a1, t), world_by_name(m, a2, t)
                    assert max(np.abs(W1[k] - W2[k]).max() for k in W2) < 1e-9
            ok += 1
        except Exception as e:
            fails.append(f"{name} ({src.bones[pick].name}): {type(e).__name__}: {str(e)[:200]}")
    assert not fails, fails
    say(f"6 corpus: a point-less leaf no rule refuses deleted on {ok} creatures (skipped {len(skipped)}: {', '.join(skipped[:5])}): "
        "reads back, points and every other bone's motion unchanged, its tracks dropped with the others byte-identical, "
        "load gate and limb lines unchanged")


def main():
    test_delete()
    test_reparent()
    test_refusals()
    test_ik()
    test_limits()
    test_corpus()
    say("ALL OK")


if __name__ == "__main__":
    main()
