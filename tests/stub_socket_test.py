"""Add Stub Socket without Blender (Boner increment B4): the stub plan rules and refusals, the export of a created stub
bone, and the Object Editor / Boner stub-bone checks.
    python blender_native/tests/stub_socket_test.py

Needs the base-game creatures (Assets/engine_stock) and the add-on stock (Assets/stock).
1. corpus: every stub socket of a stub-able class on the 61 base-game creatures, its limb and connection taken out, is
   re-planned from its stock patch: never refused, the quad is the stock ring's corner set (358/358, one patch each),
   the root bone is the stock stub bone (the 3 creatures that named it after the limb get a created standard bone),
   the host is the stock host, ring_fix of the quad is the stock ring but for the 4 pinned arm stubs whose stock start
   is not what the partners prefer, and a side / top wing stub pair shares its quad (every creature with both);
2. export: cheetah's LFArm / RFArm stubs removed with their connections AND bones (a copy), re-added through the plan
   and core.build_export -> the stock LIMB / LCON / BONE records (bones appended last with the stub byte, transforms
   exact from the stock rest); cheetah's four wing stubs removed and re-added with CREATED bones placed by the plan (at
   the patch centre, the host's orientation) -> the stock LIMB / LCON records and bone records (names ignoring case,
   parent, range, flag, joint limits); a clip re-encoded against that model has no track for a created bone; kept
   clips none; untouched builds stay byte-identical; export_summary names the bones;
3. refusals: an unknown / present class, a connection without its limb, a patch outside the table or not in the Torso,
   a quad a patched limb's ring or another (non-partner) stub socket uses, a wing on the other side's partner quad, a
   root bone owned by a limb or owning points, no host in the Torso, no Torso, the selection count;
   structure.apply_stub_bones refuses a duplicate name, an unknown parent and a new stub limb naming a missing bone,
   skips (with a warning) a created bone no limb lists, sets the stub byte on a reused bone of a new stub limb (never on
   one owning points), and follows skel_info; Validate does not call a stub limb naming a missing bone load-blocking
   (the Object Editor loads it);
4. ICParse.validate.stub_bone_lines: silent on both corpora; a stub bone with its flag off and a stub bone before a
   non-stub bone each give their STOCK line (also through check_limb_bones).
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
import combocore as CC  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import anim as ICanim  # noqa: E402
from ICParse.sgm.creature.model import ring_corners  # noqa: E402

T0 = time.perf_counter()
M = 0xFF7FFFFF
STOCK = os.path.join(REPO, "Assets", "engine_stock")
ENGINE = sorted(glob.glob(os.path.join(STOCK, "*.sgm")))
ADDON = sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
TEST_FILES = {"ChickenTest2_oneSided.sgm", "ChickenTest2.sgm", "ChickenTest.sgm", "FuckyFish.sgm", "Squid6.sgm",
              "Squid7.sgm", "Squid8.sgm", "salamander_compat.sgm"}
CHEETAH = os.path.join(STOCK, "cheetah.sgm")
# the stub rings whose stock start is not the one the Quick partners prefer (tests/ring_test.py EXPECTED_MISSES has them)
RING_MISSES = {("dragonfly", "RFArm"), ("giraffe", "RFArm"), ("horse", "RFArm"), ("raven", "LFArm")}
STUB_SUFFIX = {boner.class_of(s.limb_id): s.suffix for s in boner.STUB_SLOTS}


def say(*a):
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def tables(m):
    W = np.asarray(R.world_vertices(m), dtype=np.float64)
    P = np.array([list(p.control_points) for p in m.patches], dtype=np.int64).reshape(-1, 16)
    return W, P, core._patch_limb_of(m), [int(l.limb_id) for l in m.limbs]


def point_counts(m):
    names = [b.name for b in m.bones]
    out = {}
    for o in R.vertex_bone_owners(m):
        if o >= 0:
            out[names[o].lower()] = out.get(names[o].lower(), 0) + 1
    return out


def stub_conns(m, skip=()):
    patched = {int(l.limb_id) & M for l in m.limbs if l.prims}
    return [boner.StubConn(c.name, int(c.flags1), int(c.flags2), tuple(ring_corners(c.vertex_indices) or c.vertex_indices),
                           int(c.flags2) & M in patched)
            for i, c in enumerate(m.limb_connections) if i not in skip]


def plan_for(m, suffix, patch, drop_limbs=(), drop_conns=(), creature="", counts=None, **over):
    """stub_plan on model ``m`` with the limbs / connections ``drop_*`` (indices) taken out of the tables."""
    W, P, pl, ids = tables(m)
    keep = [j for j in range(len(m.limbs)) if j not in drop_limbs]
    remap = {o: n for n, o in enumerate(keep)}
    pl2 = np.array([remap.get(int(x), -1) for x in pl], dtype=np.int64)
    args = dict(patches=P, patch_limb=pl2, limb_ids=[ids[j] for j in keep], limb_names=[m.limbs[j].name for j in keep],
                limb_bones=[list(m.limbs[j].bone_names) for j in keep], conns=stub_conns(m, drop_conns),
                skeleton_names=[b.name for b in m.bones],
                parents=[-1 if b.parent is None else int(b.parent) for b in m.bones],
                point_counts=point_counts(m) if counts is None else counts, creature=creature)
    args.update(over)
    return boner.stub_plan(suffix, patch, **args)


def fixed_ring(m, stem, plan):
    W, P, pl, ids = tables(m)
    key = core.ring_slot(plan.limb_id)[0]
    dirs, _names = CC.partner_ring_dirs(STOCK, key, exclude_stem=stem)
    q = list(plan.quad)
    return CC.ring_fix(W[q], q, key, CC.torso_centre(W, P, pl, ids), dirs)[0]


# ------------------------------------------------------------------------------------------------------------ 1
def corpus():
    n = quad_ok = root_ok = created = host_ok = ring_ok = shared = pairs = 0
    misses, roots_off, hosts_off = set(), [], []
    for p in ENGINE:
        m = SP.read(p)
        stem = os.path.basename(p)[:-4]
        W, P, pl, ids = tables(m)
        stubs = {}
        for ci, c in enumerate(m.limb_connections):
            cls = int(c.flags2) & M
            li = next((j for j, l in enumerate(m.limbs) if int(l.limb_id) & M == cls), None)
            if cls not in STUB_SUFFIX or li is None or m.limbs[li].prims:
                continue
            ring = ring_corners(c.vertex_indices)
            cand = [q for q in range(len(P)) if set(boner.stub_quad(P[q])) == set(ring)]
            assert len(cand) == 1, (stem, c.name, cand)
            why, plan = plan_for(m, STUB_SUFFIX[cls], cand[0], drop_limbs=(li,), drop_conns=(ci,),
                                 creature=m.limbs[0].name.rsplit("_", 1)[0])
            assert why is None, (stem, c.name, why)
            n += 1
            stubs[cls] = (ci, cand[0])
            quad_ok += set(plan.quad) == set(ring)
            stock_root = m.limbs[li].bone_names[0]
            if plan.root.lower() == str(stock_root).lower() and not plan.create:
                root_ok += 1
            else:
                roots_off.append((stem, c.name, stock_root, plan.root, plan.create))
                created += plan.create
            if plan.host == c.bone_name:
                host_ok += 1
            else:
                hosts_off.append((stem, c.name, c.bone_name, plan.host))
            assert plan.limb_id & M == cls and plan.parent_class == int(c.flags1) & M == 0x800
            if fixed_ring(m, stem, plan) == ring:
                ring_ok += 1
            else:
                misses.add((stem, STUB_SUFFIX[cls]))
        for side, top in ((0x501, 0x301), (0x502, 0x302)):
            if side in stubs and top in stubs:
                pairs += 1
                ci_top, q_top = stubs[top]
                assert stubs[side][1] == q_top, (stem, "a stub wing pair on two quads")
                li = next(j for j, l in enumerate(m.limbs) if int(l.limb_id) & M == top)
                why, plan = plan_for(m, STUB_SUFFIX[top], q_top, drop_limbs=(li,), drop_conns=(ci_top,))
                partner = next(c.name for c in m.limb_connections if int(c.flags2) & M == side)
                assert why is None and plan.shares == partner, (stem, why, plan)
                shared += 1
    assert n == 358 and quad_ok == n, (n, quad_ok)
    assert misses == RING_MISSES and ring_ok == n - len(RING_MISSES), (ring_ok, misses)
    assert len(roots_off) == created == 10 and {r[0] for r in roots_off} == EXPECTED_CREATED_ROOTS, roots_off
    assert all(r[2] in boner.STUB_BY_SUFFIX for r in roots_off), roots_off         # the bone is called like its limb
    assert host_ok == n - len(hosts_off) and {(h[0], h[1]) for h in hosts_off} == EXPECTED_HOSTS_OFF, hosts_off
    assert pairs == shared == 108, (pairs, shared)                  # both sides of the 54 creatures with both stubs
    say(f"1 CORPUS: {n} base-game stub sockets re-planned from their stock patch: quad = stock ring {quad_ok}/{n}; root "
        f"= the stock stub bone {root_ok}/{n} (+{created} created: 5 creatures name that bone after its limb); host = stock "
        f"{host_ok}/{n}; ring_fix = stock ring {ring_ok}/{n} (the {len(RING_MISSES)} pinned arm-stub starts); a side / "
        f"top wing stub pair shares its quad {shared}/{pairs} (54 creatures x 2 sides)")


# the base-game creatures whose stub bones are named after their limb (LSWing, LFArm, ...): the standard root is missing,
# so the plan creates it
EXPECTED_CREATED_ROOTS = {"grizzly_bear", "poison_frog", "polar_bear", "porcupine", "wolverine"}
# giraffe's top-wing stub bones hang from spine2 but their sockets use the stock host spine3 (RULES.md 4.3)
EXPECTED_HOSTS_OFF = {("giraffe", "Giraffe_LTWing"), ("giraffe", "Giraffe_RTWing")}


# ------------------------------------------------------------------------------------------------------------ 2
def strip_stubs(path, classes, out):
    """A copy of ``path`` without the limbs of ``classes``, their connections and their bones (stub bones: leaves owning
    no points, no clip tracks), written to ``out``."""
    m = SP.read(path)
    gone = [l for l in m.limbs if int(l.limb_id) & M in classes]
    assert gone and all(not l.prims for l in gone), "strip_stubs removes stub limbs only"
    bones = {str(b).lower() for l in gone for b in l.bone_names}
    m.limbs = [l for l in m.limbs if int(l.limb_id) & M not in classes]
    m.limb_connections = [c for c in m.limb_connections if int(c.flags2) & M not in classes]
    keep = [i for i, b in enumerate(m.bones) if b.name.lower() not in bones]
    o2n = {o: k for k, o in enumerate(keep)}
    nb = []
    for i in keep:
        b = copy.copy(m.bones[i])
        assert b.parent is None or b.parent in o2n, "a removed stub bone had children"
        b.parent = None if b.parent is None else o2n[b.parent]
        nb.append(b)
    assert all(int(m.bones[i].vertex_count) == 0 for i in range(len(m.bones)) if i not in o2n)
    if m.skel_info == len(m.bones):
        m.skel_info = len(nb)
    m.bones = nb
    for a in m.animations:
        assert not any(str(t.bone_name).lower() in bones for t in a.tracks), "a stub bone has a clip track"
    with open(out, "wb") as f:
        f.write(SP.write(m))
    return SP.read(out)


def conn_row(i, c):
    return core.RingEdit(index=i, bone=c.bone_name, corners=ring_corners(c.vertex_indices), src=i, name=c.name,
                         flags1=int(c.flags1), flags2=int(c.flags2))


def readd(path, m, stem, suffixes, stock, bone_world):
    """Re-add the stub sockets ``suffixes`` to the stripped ``m`` (file ``path``) on the patches of ``stock``'s rings,
    each through stub_plan (the tables growing as it goes) + ring_fix; ``bone_world(plan, W)`` -> the created bone's
    SGM world. Returns (BuiltExport, plans)."""
    W, P, pl, ids = tables(m)
    rows = structure.source_rows(m)
    lbones = [list(l.bone_names) for l in m.limbs]
    crows = [conn_row(i, c) for i, c in enumerate(m.limb_connections)]
    names = [b.name for b in m.bones]
    parents = [-1 if b.parent is None else int(b.parent) for b in m.bones]
    new_bones, plans = [], []
    for suffix in suffixes:
        cls = boner.class_of(boner.STUB_BY_SUFFIX[suffix].limb_id)
        ring = ring_corners(next(c for c in stock.limb_connections if int(c.flags2) & M == cls).vertex_indices)
        patch = next(q for q in range(len(P)) if set(boner.stub_quad(P[q])) == set(ring))
        conns = [boner.StubConn(r.name, r.flags1, r.flags2, tuple(r.corners), any(
            boner.class_of(x.limb_id) == boner.class_of(r.flags2) and (pl == j).any() for j, x in enumerate(rows)))
            for r in crows]
        why, plan = boner.stub_plan(suffix, patch, P, pl, [r.limb_id for r in rows], [r.name for r in rows], lbones,
                                    conns, names, parents, point_counts(m), "Cheetah")
        assert why is None, (suffix, why)
        plans.append(plan)
        rows.append(structure.LimbRow(-1, plan.limb_name, plan.limb_id, (plan.root,)))
        lbones.append([plan.root])
        crows.append(core.RingEdit(index=len(crows), bone=plan.host, corners=fixed_ring(m, stem, plan), src=-1,
                                   name=plan.conn_name, flags1=plan.parent_class, flags2=plan.limb_id))
        if plan.create:
            new_bones.append(structure.NewBone(plan.root, plan.host, tuple(np.asarray(bone_world(plan, W)).reshape(-1))))
            names.append(plan.root)
            parents.append(names.index(plan.host))
    d = core.load_creature(path)
    st = core.EditState(source_path=path, verts=d.verts, limbs=rows, patch_limb=structure.patch_limb_of_rows(m, rows),
                        connections=crows, new_bones=new_bones)
    return core.build_export(st), plans


def lrec(l, bones_low=False):
    return (l.name, int(l.limb_id), sorted(int(x) for x in l.prims),
            [str(b).lower() if bones_low else str(b) for b in l.bone_names])


def crec(c):
    return (c.name, int(c.flags1), int(c.flags2), list(c.vertex_indices), c.bone_name)


def brec(b, bones, low=False):
    return (b.name.lower() if low else b.name, bones[b.parent].name if b.parent is not None else None,
            int(b.start_vertex), int(b.vertex_count), int(b.meta0) & 0xFF, int(b.meta5), [float(x) for x in b.tail])


def export():
    raw = open(CHEETAH, "rb").read()
    stock = SP.read(CHEETAH)
    bw = R.bind_world_matrices(stock)
    b = core.build_export(core.EditState(source_path=CHEETAH, verts=core.load_creature(CHEETAH).verts, new_bones=[]))
    assert SP.write(b.model) == raw and not b.bones_added and core.export_summary(b).parts == []

    # arms: stripped with their bones, re-added (created bones at the stock rest) -> the stock records
    path = "/tmp/bz_stub_noarms_src.sgm"
    m = strip_stubs(CHEETAH, {0x15, 0x16}, path)
    assert len(m.bones) == len(stock.bones) - 2
    built, plans = readd(path, m, "cheetah", ("LFArm", "RFArm"), stock,
                         lambda plan, W: bw[next(i for i, x in enumerate(stock.bones) if x.name == plan.root)])
    assert all(pl.create and pl.host == "neck1" for pl in plans), plans
    out = SP.write(built.model)
    with open("/tmp/bz_stub_arms_readd.sgm", "wb") as f:
        f.write(out)
    r = SP.read("/tmp/bz_stub_arms_readd.sgm")
    assert sorted(map(lrec, r.limbs)) == sorted(map(lrec, stock.limbs)), "LIMB records"
    assert sorted(map(crec, r.limb_connections)) == sorted(map(crec, stock.limb_connections)), "LCON records"
    assert [brec(x, r.bones) for x in r.bones] == [brec(x, stock.bones) for x in stock.bones], "BONE records"
    assert max(abs(p - q) for x, y in zip(r.bones, stock.bones) for p, q in zip(x.transform, y.transform)) < 1e-6
    assert [x.name for x in r.bones[-2:]] == ["front_arm1_l", "front_arm1_r"] and r.skel_info == len(r.bones)
    assert all(int(x.meta0) == 1 for x in r.bones[-2:]), [hex(x.meta0) for x in r.bones[-2:]]
    assert built.bones_added == ["front_arm1_l", "front_arm1_r"] and not built.stub_flagged
    parts = core.export_summary(built).parts
    assert parts[0] == "bones: added 2 stub bones (front_arm1_l, front_arm1_r)", parts
    assert "limbs: added 2 (Cheetah_LFArm, Cheetah_RFArm)" in parts and \
        "connections: added 2 (Cheetah_LFArm, Cheetah_RFArm)" in parts, parts
    assert not ICval.stub_bone_lines(r) and not [x for x in ICval.check_oe_structure(r) if "Limb" in x or "limb" in x]
    # kept clips: none has a track for the created bones; a clip re-encoded from the stock pose has none either
    added = {"front_arm1_l", "front_arm1_r"}
    assert r.animations and not any(t.bone_name in added for a in r.animations for t in a.tracks)
    a = r.animations[0]
    times = [f / max(1, int(a.frame_count)) for f in range(int(a.frame_count) + 1)]
    Wf = [np.asarray(R.animated_bone_world(m, SP.read(path).animations[0], t))[:len(m.bones)] for t in times]
    try:
        ICanim.tracks_from_world(built.model, times, Wf)
        raise AssertionError("tracks_from_world took fewer sampled bones than the model has")
    except IndexError:
        pass
    tracks = ICanim.tracks_from_world(core.clip_track_model(built.model, Wf), times, Wf)
    assert tracks and not any(t.bone_name in added for t in tracks), [t.bone_name for t in tracks]
    clip = ICanim.build_animation(a.name, int(a.frame_count), float(a.fps), tracks, layout=ICanim.layout_of(a),
                                  model=built.model)
    A = np.asarray(R.animated_bone_world(built.model, clip, 0.5))
    Bw = R.bind_world_matrices(built.model)
    for k in (-2, -1):                                              # an untracked bone keeps its bind-local pose
        pi = built.model.bones[k].parent
        assert np.allclose(A[k], A[pi] @ np.linalg.inv(Bw[pi]) @ Bw[k], atol=1e-5)
    try:
        core.clip_track_model(m, [np.zeros((len(built.model.bones), 4, 4))])
        raise AssertionError("more sampled bones than the model has must raise")
    except ValueError:
        pass

    # wings: stripped with their bones, re-added with CREATED bones placed by the plan; side and top share quads
    path = "/tmp/bz_stub_nowings_src.sgm"
    m = strip_stubs(CHEETAH, {0x501, 0x502, 0x301, 0x302}, path)
    mbw = R.bind_world_matrices(m)
    Pm = tables(m)[1]

    def place(plan, W):
        hi = next(i for i, x in enumerate(m.bones) if x.name == plan.host)
        return boner.stub_bone_world(mbw[hi], boner.patch_centre(W[Pm[plan.patch]]))
    built, plans = readd(path, m, "cheetah", ("LSWing", "RSWing", "LTWing", "RTWing"), stock, place)
    assert [pl.create for pl in plans] == [True] * 4 and [pl.shares for pl in plans] == [None, None, "Cheetah_LSWing",
                                                                                          "Cheetah_RSWing"], plans
    assert plans[0].patch == plans[2].patch and plans[1].patch == plans[3].patch
    out = SP.write(built.model)
    with open("/tmp/bz_stub_wings_readd.sgm", "wb") as f:
        f.write(out)
    r = SP.read("/tmp/bz_stub_wings_readd.sgm")
    assert sorted(lrec(x, True) for x in r.limbs) == sorted(lrec(x, True) for x in stock.limbs), "LIMB records"
    assert sorted(map(crec, r.limb_connections)) == sorted(map(crec, stock.limb_connections)), "LCON records"
    assert sorted(brec(x, r.bones, True) for x in r.bones) == sorted(brec(x, stock.bones, True) for x in stock.bones)
    n0 = len(m.bones)
    assert [x.name for x in r.bones[n0:]] == ["swing1_l", "swing1_r", "top_wing_l", "top_wing_r"], "appended last"
    rbw = R.bind_world_matrices(r)
    W = tables(r)[0]
    for k, plan in enumerate(plans):
        x = r.bones[n0 + k]
        local = np.array(x.transform, dtype=np.float64).reshape(4, 3).T
        assert np.allclose(local[:, :3], np.eye(3), atol=1e-6), (x.name, local)          # the host's orientation
        assert np.allclose(rbw[n0 + k][:3, 3], boner.patch_centre(W[tables(r)[1][plan.patch]]), atol=1e-5)
        assert r.bones[x.parent].name == plan.host == "spine3" and int(x.meta0) == 1 and int(x.meta1) == int(r.bones[x.parent].meta1)
    assert not ICval.stub_bone_lines(r)
    assert list(R.vertex_bone_owners(r)) == list(R.vertex_bone_owners(m)), "a created bone took points"
    say("2 EXPORT: cheetah without its arm stubs (limbs, connections, bones) re-planned -> the stock LIMB / LCON / BONE "
        "records (bones appended last, flags word 1, transforms exact from the stock rest, skel_info follows); the "
        "four wing stubs re-added with created bones at the patch centre (the host's orientation, side / top sharing "
        "quads) -> the stock records; no clip track for a created bone (kept or re-encoded); untouched build identical")



# ------------------------------------------------------------------------------------------------------------ 3
def refused(got, *words):
    why, plan = got
    assert plan is None and why and all(w in why for w in words), (why, words)
    return why


def refusals():
    stock = SP.read(CHEETAH)
    m = strip_stubs(CHEETAH, {0x15, 0x16, 0x501, 0x502, 0x301, 0x302}, "/tmp/bz_stub_bare_src.sgm")
    W, P, pl, ids = tables(m)
    torso = next(j for j, l in enumerate(m.limbs) if int(l.limb_id) == 0x800)
    head = next(j for j, l in enumerate(m.limbs) if int(l.limb_id) & M == 0x40)
    lfleg = next(j for j, l in enumerate(m.limbs) if int(l.limb_id) & M == 0x25)
    ring_of = lambda cls: set(ring_corners(next(c for c in stock.limb_connections if int(c.flags2) & M == cls).vertex_indices))
    quad = lambda cls: next(q for q in range(len(P)) if set(boner.stub_quad(P[q])) == ring_of(cls))
    q_arm, q_wing = quad(0x15), quad(0x501)
    ok = plan_for(m, "LFArm", q_arm, creature="Cheetah")
    assert ok[0] is None and ok[1].create and ok[1].limb_name == "Cheetah_LFArm" and ok[1].conn_name == "Cheetah_LFArm"
    assert [s_.suffix for s_ in boner.stub_classes_missing(ids, [c.flags2 for c in m.limb_connections])] == \
        ["LSWing", "RSWing", "LTWing", "RTWing", "LFArm", "RFArm"]
    assert boner.stub_patch_refusal([]).startswith("no patch is selected") and \
        boner.stub_patch_refusal([1, 2]).startswith("2 patches are selected") and boner.stub_patch_refusal([7]) is None
    refused(plan_for(m, "Head", q_arm), "'Head' is not a stub-able limb class")
    refused(plan_for(m, "LFLeg", q_arm), "limb 'Cheetah_LFLeg' already has the L Front Leg class")
    orphan = [boner.StubConn("Old_LFArm", 0x800, 0x15, (1, 2, 3, 4), False)] + stub_conns(m)
    refused(plan_for(m, "LFArm", q_arm, conns=orphan), "connection 'Old_LFArm' already attaches L Front Arm limbs")
    refused(plan_for(m, "LFArm", len(P)), f"patch {len(P)} is not in the {len(P)}-patch table")
    hp = int(np.nonzero(pl == head)[0][0])
    refused(plan_for(m, "LFArm", hp), f"patch {hp} is in limb 'Cheetah_Head', not the Torso")
    refused(plan_for(m, "LFArm", q_arm, limb_ids=[0x40 if j == torso else i for j, i in enumerate(ids)]),
            "no Torso limb")
    # a quad a connection uses: a patched limb's ring, another stub socket, the other side's wing
    used = [boner.StubConn("Cheetah_LFLeg", 0x800, 0x25, tuple(boner.stub_quad(P[q_arm])), True)] + stub_conns(m)
    refused(plan_for(m, "LFArm", q_arm, conns=used), "already the ring of connection 'Cheetah_LFLeg'",
            "a limb with patches grafts there")
    arm = [boner.StubConn("Cheetah_LFArm", 0x800, 0x15, tuple(boner.stub_quad(P[q_wing])), False)] + stub_conns(m)
    refused(plan_for(m, "LSWing", q_wing, conns=arm), "connection 'Cheetah_LFArm'", "another stub socket")
    right = [boner.StubConn("Cheetah_RSWing", 0x800, 0x502, tuple(boner.stub_quad(P[q_wing])), False)] + stub_conns(m)
    refused(plan_for(m, "LTWing", q_wing, conns=right), "connection 'Cheetah_RSWing'")
    left = [boner.StubConn("Cheetah_LSWing", 0x800, 0x501, tuple(boner.stub_quad(P[q_wing])), False)] + stub_conns(m)
    why, plan = plan_for(m, "LTWing", q_wing, conns=left)
    assert why is None and plan.shares == "Cheetah_LSWing"
    patched = [boner.StubConn("Cheetah_LSWing", 0x800, 0x501, tuple(boner.stub_quad(P[q_wing])), True)] + stub_conns(m)
    refused(plan_for(m, "LTWing", q_wing, conns=patched), "a limb with patches grafts there")
    # the root bone: an existing standard bone owned by a limb or owning points; no host in the Torso
    sk = [b.name for b in m.bones] + ["front_arm1_l"]
    par = [-1 if b.parent is None else int(b.parent) for b in m.bones] + [[b.name for b in m.bones].index("neck1")]
    bones = [list(l.bone_names) for l in m.limbs]
    why, plan = plan_for(m, "LFArm", q_arm, skeleton_names=sk, parents=par)
    assert why is None and not plan.create and plan.root == "front_arm1_l" and plan.host == "neck1", (why, plan)
    owned = [b + (["front_arm1_l"] if j == head else []) for j, b in enumerate(bones)]
    refused(plan_for(m, "LFArm", q_arm, skeleton_names=sk, parents=par, limb_bones=owned),
            "bone 'front_arm1_l', the L Front Arm limb's root, already belongs to limb 'Cheetah_Head'")
    refused(plan_for(m, "LFArm", q_arm, skeleton_names=sk, parents=par, counts={"front_arm1_l": 3}),
            "owns 3 control points: a stub bone owns none")
    no_host = [[x for x in b if x.lower() != "neck1"] if j == torso else b for j, b in enumerate(bones)]
    refused(plan_for(m, "LFArm", q_arm, limb_bones=no_host), "no host bone for the L Front Arm socket",
            "neck1 (the stock host)")
    # the export side
    d = core.load_creature("/tmp/bz_stub_bare_src.sgm")
    rows = structure.source_rows(m)
    Wn = tuple(np.eye(4).reshape(-1))

    def build(new_rows=(), new_bones=()):
        rr = rows + list(new_rows)
        return core.build_export(core.EditState(source_path="/tmp/bz_stub_bare_src.sgm", verts=d.verts, limbs=rr,
                                                patch_limb=structure.patch_limb_of_rows(m, rr), new_bones=list(new_bones)))
    stub_row = structure.LimbRow(-1, "Cheetah_LFArm", 0x15, ("front_arm1_l",))
    for bones_, words in (([structure.NewBone("Neck1", "spine3", Wn)], "already has a bone of that name"),
                          ([structure.NewBone("front_arm1_l", "no_bone", Wn)], "its parent 'no_bone' is not a bone")):
        try:
            build([stub_row] if bones_[0].name == "front_arm1_l" else
                  [structure.LimbRow(-1, "X_LFArm", 0x15, ("Neck1",))], bones_)
            raise AssertionError(f"not refused: {words}")
        except ValueError as e:
            assert words in str(e), (words, e)
    b = build([stub_row])                                           # a stub limb naming a bone the skeleton lacks
    assert any("limb 'Cheetah_LFArm' lists bone 'front_arm1_l', which is not in the skeleton" in w for w in b.warnings)
    assert any("Bone 'front_arm1_l' referenced by limb 'Cheetah_LFArm' was not found" in x
               for x in ICval.check_oe_structure(b.model)), ICval.check_oe_structure(b.model)
    b = build([], [structure.NewBone("front_arm1_l", "neck1", Wn)])
    assert SP.write(b.model) == open("/tmp/bz_stub_bare_src.sgm", "rb").read() and not b.bones_added
    assert b.warnings and "front_arm1_l is on the rig but in no limb, so not exported" in b.warnings[0], b.warnings
    # a new stub limb reusing a bone without the stub flag: the export sets it (and says so)
    endjaw = next(x for x in m.bones if x.name.lower() == "endjaw")
    assert int(endjaw.meta0) & 0xFF != 1
    rows2 = [structure.LimbRow(r.src, r.name, r.limb_id,
                               tuple(x for x in m.limbs[r.src].bone_names if x != endjaw.name) if r.src == head else None)
             for r in rows]
    rr = rows2 + [structure.LimbRow(-1, "Cheetah_LFArm", 0x15, (endjaw.name,))]
    b = core.build_export(core.EditState(source_path="/tmp/bz_stub_bare_src.sgm", verts=d.verts, limbs=rr,
                                         patch_limb=structure.patch_limb_of_rows(m, rr)))
    fl = next(x for x in b.model.bones if x.name == endjaw.name)
    assert b.stub_flagged == [endjaw.name] and int(fl.meta0) == (int(endjaw.meta0) & ~0xFF) | 1
    assert f"bones: stub flag set on {endjaw.name}" in core.export_summary(b).parts
    lines = ICval.stub_bone_lines(b.model)
    assert len(lines) == 1 and lines[0].startswith(f"STOCK stub bone {endjaw.name} comes before bone"), lines
    # ... but never on a bone that owns points (it is no stub bone: Validate's 'owns control points' line speaks)
    tail1 = next(x for x in m.bones if x.name.lower() == "tail1")
    assert int(tail1.vertex_count) > 0 and int(tail1.meta0) & 0xFF != 1
    tl = next(i for i, x in enumerate(m.limbs) if any(bn == tail1.name for bn in x.bone_names))
    rows3 = [structure.LimbRow(r.src, r.name, r.limb_id,
                               tuple(x for x in m.limbs[r.src].bone_names if x != tail1.name) if r.src == tl else None)
             for r in rows]
    rr = rows3 + [structure.LimbRow(-1, "Cheetah_LFArm", 0x15, (tail1.name,))]
    b = core.build_export(core.EditState(source_path="/tmp/bz_stub_bare_src.sgm", verts=d.verts, limbs=rr,
                                         patch_limb=structure.patch_limb_of_rows(m, rr)))
    fl = next(x for x in b.model.bones if x.name == tail1.name)
    assert not b.stub_flagged and int(fl.meta0) == int(tail1.meta0), (b.stub_flagged, fl.meta0, tail1.meta0)
    assert not any(p.startswith("bones:") for p in core.export_summary(b).parts), core.export_summary(b).parts
    assert any("owns" in x and tail1.name in x for x in ICval.check_limb_bones(b.model)), ICval.check_limb_bones(b.model)
    # a stub limb naming a bone the skeleton lacks (a created stub bone deleted on the rig) LOADS in the Object Editor
    # (harness 2026-09-15: cheetah + LFArm limb/socket naming a deleted front_arm1_l -> LOADED, the OE's own warning):
    # Validate reports it as the OE's WARN line (+ the COMBINE ERROR precheck), not as a problem that blocks loading
    rr = rows + [stub_row]
    b = core.build_export(core.EditState(source_path="/tmp/bz_stub_bare_src.sgm", verts=d.verts, limbs=rr,
                                         patch_limb=structure.patch_limb_of_rows(m, rr)))
    vl = core.validate_model(b.model)
    assert not core.blocking(vl), core.blocking(vl)
    assert "WARN oe: Bone 'front_arm1_l' referenced by limb 'Cheetah_LFArm' was not found in skeleton" in vl, vl
    # skel_info follows the bone count only when it equalled it
    m2 = SP.read("/tmp/bz_stub_bare_src.sgm")
    m2.skel_info = len(m2.bones) + 3
    m2.limbs.append(copy.copy(m2.limbs[0])); m2.limbs[-1].name, m2.limbs[-1].prims, m2.limbs[-1].bone_names = "S", [], ["zz"]
    added, flagged, warns = structure.apply_stub_bones(m2, [0] * (len(m2.limbs) - 1) + [-1],
                                                       [structure.NewBone("zz", "neck1", Wn)])
    assert added == ["zz"] and m2.skel_info == len(m2.bones) + 2 and not warns
    say("3 REFUSALS: unknown / present class, a connection without its limb, patch outside the table / not the Torso, "
        "no Torso, a quad of a patched ring / another stub / the other side's wing (the partner's is shared), a root "
        "owned by a limb / owning points, no host; apply_stub_bones: duplicate name, unknown parent, a stub limb naming a "
        "missing bone (warned; Validate: not blocking), an unlisted created bone skipped with a warning, the stub byte set on a "
        "reused bone (and the not-last line) but never on a bone owning points, skel_info")


# ------------------------------------------------------------------------------------------------------------ 4
def validate_lines():
    n = 0
    for p in ENGINE + ADDON:
        if os.path.basename(p) in TEST_FILES:
            continue
        m = SP.read(p)
        if core.is_scene_model(m) or not m.patches:
            continue
        assert not ICval.stub_bone_lines(m), (os.path.basename(p), ICval.stub_bone_lines(m))
        n += 1
    assert n == 111, n
    m = SP.read(CHEETAH)
    arm = next(i for i, b in enumerate(m.bones) if b.name == "front_arm1_l")
    m.bones[arm].meta0 = 0x100
    lines = [x for x in ICval.check_limb_bones(m) if "stub" in x]
    assert len(lines) == 1 and lines[0].startswith("STOCK stub limb 'Cheetah_LFArm': its bone 'front_arm1_l' has the stub "
                                                   "flag off (BONE flags low byte 0x00, not 0x01)"), lines
    m = SP.read(CHEETAH)
    m.bones[arm].meta0 = 1
    m.limbs = [l for l in m.limbs if int(l.limb_id) & M != 0x16]      # its partner's bone: in no limb, flag off
    rb = next(b for b in m.bones if b.name == "front_arm1_r")
    rb.meta0 = 0
    lines = [x for x in ICval.check_limb_bones(m) if "stub" in x]
    assert lines == ["STOCK stub bones top_wing_r, Top_Wing_L, SWing1_L, swing1_r, ... come before bone 'front_arm1_r': "
                     "Boner writes stub bones after every other bone (every base-game creature does)"], lines
    say(f"4 VALIDATE: stub_bone_lines silent on {n} stock creatures; flag off and not-last lines through check_limb_bones")


if __name__ == "__main__":
    corpus()
    export()
    refusals()
    validate_lines()
    with open("/tmp/bz_stub_socket_done.txt", "w") as f:
        f.write("ok\n")
    say("stub_socket_test PASS")
