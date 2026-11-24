"""Boner's skinning rules without Blender (increment B5).
    python blender_native/tests/boner_test.py

Needs the base-game creatures (Assets/engine_stock) and the add-on stock (Assets/stock).
1. the stock skinning layout, measured and pinned: inside every bone's vertex range the patch-corner control points
   (0, 3, 12, 15 of some patch) come first and the BONE aux_count word (meta5) is their number, and points no bone
   owns sit before every range -- 61/61 base-game creatures and every add-on stock creature except the three
   ChickenTest files (made from ICParse's template table, not by Relic's exporter);
2. reskin on all 61 base-game creatures (a corner and a handle moved to another bone): the export follows the layout
   for EVERY bone, the world shape is kept, and only the two touched bones get a new aux_count;
3. owner -1: woolly_mammoth's two unowned points stay first with their bytes through a reskin; a patch point with no
   bone is refused by the builder (fast and grown path) with the count; a grown export with an unused unowned point
   writes it at its position; the open snail + Extrude + reskin export follows the layout;
4. By Bone colours: a stable palette clear of the unassigned orange, -1 / unresolved owners orange, dimming, the
   nearest-control-point map (nearest Greville abscissa k/3 of the sample's parameters, on ICParse's
   evaluation grid: corner samples on the corner control points and the same layout as bezier.eval_patch, 3 creatures), per-bone sample counts;
5. Pick Bone's rule (nearest point vs segment, unassigned note, radius, NaN) and its modal event rule; Auto Skin's
   nearest-segment rule, eligibility and targets; stub bones own no points on 61/61; nearest-segment agreement with
   the stock skin is reported (floor pinned).
"""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import boner  # noqa: E402
import limbs  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import bezier as BE  # noqa: E402

T0 = time.perf_counter()
ENGINE = sorted(glob.glob(os.path.join(REPO, "Assets", "engine_stock", "*.sgm")))
ADDON = sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
TMP = os.environ.get("TMPDIR") or "/tmp"
# add-on stock files off the layout, and why: built from ICParse.sgm.creature.template (its meta5 table), not exported
# by Relic's tools
OFF_RULE = {"ChickenTest.sgm", "ChickenTest2.sgm", "ChickenTest2_oneSided.sgm"}
SNAIL = os.path.join(REPO, "Assets", "stock", "snail.sgm")
WOOLLY = os.path.join(REPO, "Assets", "engine_stock", "woolly_mammoth.sgm")


def say(*a):
    print(*a, flush=True)


def world_sorted(m):
    return np.array(sorted(map(tuple, np.round(R.world_vertices(m), 4))))


# ------------------------------------------------------------------------------------------ 1. the stock layout
def stock_layout():
    assert len(ENGINE) == 61, f"expected the 61 base-game creatures in Assets/engine_stock, found {len(ENGINE)}"
    assert len(ADDON) >= 55, f"Assets/stock has {len(ADDON)} creatures"
    off, bones = {}, 0
    for f in ENGINE + ADDON:
        m = core.read_source(f)
        bones += len(m.bones)
        probs = core.aux_rule_problems(m)
        if probs:
            off[os.path.basename(f)] = probs
    eng_off = [k for k in off if os.path.join(REPO, "Assets", "engine_stock", k) in ENGINE]
    assert not eng_off, {k: off[k][:3] for k in eng_off}
    assert set(off) == OFF_RULE, {k: v[:2] for k, v in off.items() if k not in OFF_RULE}
    say(f"1 LAYOUT: corners first + aux_count == corners + unowned first on {len(ENGINE)}/{len(ENGINE)} base-game and "
        f"{len(ADDON) - len(OFF_RULE)}/{len(ADDON)} add-on stock creatures ({bones} bones); off: {sorted(off)} "
        "(template-made)")


# ------------------------------------------------------------------------------------------ 2. reskin on the corpus
def reskin_corpus():
    n_ok = 0
    for f in ENGINE:
        d = core.load_creature(f)
        src = d.model
        owners = np.array(R.vertex_bone_owners(src), dtype=np.int64)
        corner = core.corner_mask(d.patches, len(d.verts))
        counts = np.bincount(owners[owners >= 0], minlength=len(src.bones))
        a = int(np.argmax(counts))
        b = next(i for i in np.argsort(-counts) if i != a and counts[i] > 0)
        pa = np.nonzero(owners == a)[0]
        moves = [int(pa[corner[pa]][0])] + ([int(pa[~corner[pa]][0])] if (~corner[pa]).any() else [])
        vo = owners.copy()
        vo[moves] = b
        built = core.build_export(core.EditState(f, d.verts, vert_owner=vo))
        m = built.model
        probs = core.aux_rule_problems(m)
        assert not probs, (os.path.basename(f), probs[:3])
        assert np.abs(world_sorted(m) - world_sorted(src)).max() < 2e-3, os.path.basename(f)
        new_owners = np.array(R.vertex_bone_owners(m))
        assert np.array_equal(new_owners[built.o2n], vo), os.path.basename(f)
        changed = {i for i, (x, y) in enumerate(zip(src.bones, m.bones)) if x.meta5 != y.meta5}
        assert changed <= {a, b}, (os.path.basename(f), changed, a, b)
        m2 = SP.read(SP.write(m))                                   # the words survive the writer
        assert [x.meta5 for x in m2.bones] == [x.meta5 for x in m.bones]
        n_ok += 1
    say(f"2 RESKIN: {n_ok}/{len(ENGINE)} base-game creatures reskinned (a corner + a handle to another bone): layout "
        "holds for every bone, world kept, aux_count changed only on the two bones")


# ------------------------------------------------------------------------------------------ 3. owner -1
def unowned_cases():
    d = core.load_creature(WOOLLY)
    src = d.model
    owners = np.array(R.vertex_bone_owners(src), dtype=np.int64)
    un = np.nonzero(owners < 0)[0]
    assert list(un) == [0, 1], un
    used = {int(i) for p in d.patches for i in p}
    assert not (set(un.tolist()) & used)
    assert len(core.unowned_patch_points(owners, d.patches)) == 0
    # untouched: identical; reskin: points 0,1 stay first, bytes kept
    assert SP.write(core.build_export(core.EditState(WOOLLY, d.verts, vert_owner=owners)).model) == open(WOOLLY, "rb").read()
    vo = owners.copy()
    tgt = int(np.nonzero(owners == 5)[0][0])
    vo[tgt] = 12
    b = core.build_export(core.EditState(WOOLLY, d.verts, vert_owner=vo))
    assert list(b.o2n[:2]) == [0, 1] and b.model.vertices[0] == src.vertices[0] and b.model.vertices[1] == src.vertices[1]
    assert not core.aux_rule_problems(b.model)
    # a patch point with no bone: refused (fast path), with the count and the hint
    vo2 = owners.copy()
    p0 = [int(i) for i in d.patches[0][[0, 5]]]
    vo2[p0] = -1
    try:
        core.build_export(core.EditState(WOOLLY, d.verts, vert_owner=vo2))
        raise AssertionError("a patch point with no bone was exported")
    except ValueError as e:
        assert "2 control points used by patches have no bone" in str(e) and "Select Unassigned" in str(e), str(e)
    assert list(core.unowned_patch_points(vo2, d.patches)) == sorted(p0)
    # a used point removed from its bone and an unused point: only the used one counts
    msg = core.unowned_message(1, [7])
    assert msg.startswith("1 control point used by patches has no bone (points 7)"), msg

    # grown: an extra unused point with no bone is written at its position; a used one is refused
    verts = np.vstack([d.verts, [[0.25, -0.5, 1.5]]])
    st = core.EditState(WOOLLY, verts, patches=d.patches, vert_owner=np.append(owners, -1),
                        patch_limb=core._patch_limb_of(src), patch_uv=d.patch_uv, patch_material=d.patch_mat,
                        patch_type=np.array([p.type for p in src.patches]))
    g = core.build_export(st)
    assert g.grown and not core.aux_rule_problems(g.model)
    back = np.asarray(core.sgm_to_blender(np.array([g.model.vertices[int(g.o2n[-1])]])))[0]
    assert np.allclose(back, [0.25, -0.5, 1.5]), back
    assert int(g.o2n[-1]) < 3, "an unowned point is not before the bone ranges"
    st.vert_owner = np.append(vo2, 3)
    try:
        core.build_export(st)
        raise AssertionError("grown: a patch point with no bone was exported")
    except ValueError as e:
        assert "have no bone" in str(e), str(e)

    # the open snail + Extrude + a reskin of the new points: the grown export follows the layout for every bone
    ds = core.load_creature(SNAIL)
    ms = ds.model
    so = np.array(R.vertex_bone_owners(ms), dtype=np.int64)
    keep = list(range(len(ds.patches) - 1))
    patches = ds.patches[keep]
    users = core.side_users(patches)
    pi, side, _k = next((p, s_, kk) for p, s_, kk in core.patch_sides(patches) if len(users[kk]) == 1)
    nv, cp = core.extrude_patch(ds.verts, patches[pi], side)
    other = int(np.argmax(np.bincount(so[so >= 0])))
    gst = core.EditState(SNAIL, np.vstack([ds.verts, nv]), patches=np.vstack([patches, [cp]]),
                         vert_owner=np.concatenate([so, [other] * 12]),
                         patch_limb=core._patch_limb_of(ms)[keep + [pi]],
                         patch_uv=np.vstack([ds.patch_uv[keep], [ds.patch_uv[keep][pi]]]),
                         patch_material=np.append(ds.patch_mat[keep], ds.patch_mat[keep][pi]),
                         patch_type=np.array([p.type for p in ms.patches])[keep + [pi]])
    ge = core.build_export(gst)
    assert ge.grown and not core.aux_rule_problems(ge.model), core.aux_rule_problems(ge.model)[:3]
    say("3 UNOWNED: woolly_mammoth's 2 unused unowned points stay first with their bytes through a reskin; patch points "
        "with no bone refused (fast + grown) with the count and the Select Unassigned hint; an unused unowned new point "
        "is written at its position; open snail + Extrude + reskin follows the layout")


# ------------------------------------------------------------------------------------------ 4. By Bone colours
def colours():
    p10, p60 = limbs.bone_palette(10), limbs.bone_palette(60)
    assert np.array_equal(p10, p60[:10]), "the palette is not stable per bone index"
    orange = limbs.UNASSIGNED_COLOR
    assert np.min(np.linalg.norm(p60 - orange, axis=1)) > 0.2, "a bone colour looks like the unassigned orange"
    tab = limbs.bone_color_table(6, active=2)
    assert np.array_equal(tab[2], limbs.bone_palette(6)[2]) and np.array_equal(tab[-1], orange)
    assert all(tab[i].sum() < limbs.bone_palette(6)[i].sum() for i in (0, 1, 3, 4, 5)), "others are not dimmed"
    assert np.array_equal(limbs.bone_color_table(6)[:6], limbs.bone_palette(6)), "no active bone: all bright"
    pc = limbs.point_bone_colors([0, -1, 5, 9], 6, active=-1)
    assert pc.dtype == np.float32 and pc.shape == (4, 4)
    assert np.allclose(pc[1, :3], orange) and np.allclose(pc[3, :3], orange), "-1 / unresolved are not orange"
    assert np.array_equal(limbs.sample_cp_map(4), np.arange(16))
    assert list(limbs.sample_cp_map(2)) == [0, 3, 12, 15]
    for dens in (2, 3, 4, 5, 6, 8, 16):          # the control point whose Greville abscissa (k/3) is nearest (half up)
        ts = np.linspace(0, 1, dens)
        near = np.array([min(range(4), key=lambda k: (abs(t - k / 3.0) - 1e-9 * k)) for t in ts])
        mp = limbs.sample_cp_map(dens)
        assert np.array_equal(mp, (near[:, None] * 4 + near[None, :]).reshape(-1)), dens
    import fastsurf
    for f in (SNAIL, os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm"), WOOLLY):
        d = core.load_creature(f)
        dens = 5
        topo = fastsurf.TopoCache(d.patches, dens)
        V, _N = topo.eval_all(d.verts)
        V = V.reshape(len(d.patches), dens * dens, 3)
        mp = limbs.sample_cp_map(dens)
        for k in (0, dens - 1, dens * (dens - 1), dens * dens - 1):     # corner samples sit on their corner CP
            assert np.allclose(V[:, k], d.verts[d.patches[:, mp[k]]], atol=1e-5), (f, k)
        ref = BE.eval_patch(d.verts[d.patches[3]], dens).reshape(-1, 3)   # the evaluator's layout is ICParse's
        assert np.allclose(V[3], ref, atol=1e-6), f
        owners = np.array(R.vertex_bone_owners(d.model))
        nb = len(d.model.bones)
        cols = limbs.vertex_bone_colors(owners, d.patches, dens, nb, active=-1)
        pal = limbs.bone_color_table(nb)
        want = owners[d.patches[:, mp].reshape(-1)]
        want = np.where(want >= 0, want, nb)
        assert np.array_equal(cols, pal[want]), f
        got_counts = np.bincount(want, minlength=nb + 1)
        exp = np.array([sum(int((owners[d.patches[p]][mp] == b).sum()) for p in range(len(d.patches)))
                        for b in range(nb)])
        assert np.array_equal(got_counts[:nb], exp), f
    lit = limbs.lit_colors(np.ones((3, 3)) * 0.5, np.array([[0, 0, 1.0]] * 3), 0.6)
    assert lit.shape == (3, 4) and np.allclose(lit[:, 3], 0.6)
    say("4 COLOURS: palette stable + clear of orange, -1/unresolved orange, others dimmed, sample->CP map = nearest Greville "
        "abscissa (7 densities) and corner samples on corner CPs + ICParse grid layout (3 creatures), per-bone "
        "sample counts exact")


# ------------------------------------------------------------------------------------------ 5. pick + auto skin
def picking():
    pts = np.array([[10.0, 10.0], [50.0, 50.0], [np.nan, np.nan], [100.0, 10.0]])
    own = np.array([0, 1, 2, -1])
    a = np.array([[0.0, 30.0], [200.0, 200.0]])
    b = np.array([[100.0, 30.0], [np.nan, np.nan]])
    h = boner.pick_target(pts, own, a, b, (12, 11))
    assert h.bone == 0 and h.kind == "POINT" and h.point == 0, h
    h = boner.pick_target(pts, own, a, b, (30, 25))                   # segment 0 is 5 px away, points > 15
    assert h.bone == 0 and h.kind == "BONE", h
    h = boner.pick_target(pts, own, a, b, (50, 38))                   # point 1 at 12 px, segment at 8 px: the segment
    assert h.kind == "BONE" and h.bone == 0, h
    h = boner.pick_target(pts, own, a, b, (50, 46))                   # point 1 at 4 px wins
    assert h.kind == "POINT" and h.bone == 1, h
    h = boner.pick_target(pts, own, a, b, (101, 12))                  # the unassigned point: a note, no bone
    assert h.bone is None and h.point == 3 and "no bone" in h.note, h
    h = boner.pick_target(pts, own, a, b, (180, 120))
    assert h.bone is None and h.kind is None and h.note, h
    assert boner.nearest_point(pts, (10, 10), allowed=[False, True, True, True]) == (None, None)
    r = boner.BonePickRule()
    assert r.handle("MIDDLEMOUSE", "PRESS", None) == "PASS_THROUGH"
    assert r.handle("NUMPAD_5", "PRESS", None) == "PASS_THROUGH"
    assert r.handle("MOUSEMOVE", "NOTHING", None) == "RUNNING"
    assert r.handle("LEFTMOUSE", "PRESS", boner.BoneHit(note="nothing here")) == "RUNNING" and r.message == "nothing here"
    assert r.handle("LEFTMOUSE", "RELEASE", boner.BoneHit(3, "BONE")) == "RUNNING" and r.picked is None
    assert r.handle("LEFTMOUSE", "PRESS", boner.BoneHit(3, "BONE")) == "FINISH" and r.picked == 3
    assert boner.BonePickRule().handle("ESC", "PRESS", None) == "CANCEL"
    assert boner.BonePickRule().handle("RIGHTMOUSE", "PRESS", None) == "CANCEL"

    heads = np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]])
    tails = np.array([[1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]])
    P = np.array([[0.2, 0.3, 0], [1.5, -0.2, 0], [2.9, 0.1, 0], [1.0, 0.5, 0]])
    assert list(boner.auto_skin(P, heads, tails)) == [0, 1, 2, 0]       # the tie at x=1 goes to the lower index
    assert list(boner.auto_skin(P, heads, tails, eligible=[True, False, True])) == [0, 0, 2, 0]
    assert list(boner.auto_skin(P, heads, tails, eligible=[False] * 3)) == [-1] * 4
    own = np.array([0, -1, 2, -1])
    sel = np.array([True, False, False, False])
    assert list(boner.skin_targets(own, sel)) == [0]
    assert list(boner.skin_targets(own, np.zeros(4, bool))) == [1, 3]
    assert list(boner.skin_targets(own, sel, "UNASSIGNED")) == [1, 3]
    assert list(boner.skin_targets(own, sel, "ALL")) == [0, 1, 2, 3]
    st = boner.skin_stats([0, 0, -1, 1, 7], 3, patches=np.array([[0, 2] * 8]))
    assert list(st.counts) == [2, 1, 0] and st.unassigned == 2 and st.unassigned_used == 1, st

    # segments: head -> nearest child's head, a leaf is its head alone
    hd3 = np.array([[0.0, 0, 0], [0, 0, 1.0], [0, 0, 3.0], [1.0, 0, 1.0]])
    assert np.array_equal(boner.skin_segments(hd3, [None, 0, 1, 1]), np.array([[0, 0, 1.0], [1.0, 0, 1.0],
                                                                                [0, 0, 3.0], [1.0, 0, 1.0]]))
    assert list(boner.end_site_bones(["endjaw", "front_endleg_r", "Rear_EndFoot_L", "tendon", "legend", "neck1"])) == \
        [True, True, True, False, False, False]
    # Object Mode never uses the (unshown) selection; a selection of every point is not "Selected"
    assert list(boner.skin_targets([0, -1, 2], None, "AUTO")) == [1]
    assert boner.auto_target_refusal("SELECTED", None, False) and not boner.auto_target_refusal("AUTO", None, False)
    assert "Reset All" in boner.auto_target_refusal("AUTO", np.ones(5, bool), True)
    assert boner.auto_target_refusal("SELECTED", np.ones(5, bool), True)
    assert not boner.auto_target_refusal("ALL", np.ones(5, bool), True)
    assert not boner.auto_target_refusal("AUTO", np.array([1, 0, 1], bool), True)

    stub_owning, stubs, ends, end_owning, agree, given = 0, 0, 0, 0, [], 0
    for f in ENGINE:
        m = core.read_source(f)
        sb = boner.stub_bones(m)
        eb = boner.end_site_bones([b.name for b in m.bones])
        stubs += int(sb.sum())
        ends += int(eb.sum())
        stub_owning += sum(1 for i, b in enumerate(m.bones) if sb[i] and b.vertex_count)
        end_owning += sum(1 for i, b in enumerate(m.bones) if eb[i] and b.vertex_count)
        owners = np.array(R.vertex_bone_owners(m))
        hd = np.array([x[:3, 3] for x in R.bind_world_matrices(m)])
        tl = boner.skin_segments(hd, [b.parent for b in m.bones])
        W = R.world_vertices(m)
        mask = boner.auto_skin_mask(m)
        assert np.array_equal(mask, ~sb & ~eb)
        got = boner.auto_skin(W[owners >= 0], hd, tl, eligible=mask)
        given += int((~mask[got]).sum())
        agree.append(float((got == owners[owners >= 0]).mean()))
    assert stubs > 300 and stub_owning == 0, (stubs, stub_owning)
    assert ends > 600 and end_owning == 0, (ends, end_owning)
    assert given == 0
    assert np.mean(agree) >= 0.50, np.mean(agree)
    say(f"5 PICK/AUTO: pick rule (point vs segment, unassigned note, radius, NaN) + modal rule; Auto Skin segments "
        f"(leaf = its head), targets (Object Mode: Unassigned; every point selected refused); {stubs} stub bones and "
        f"{ends} end-site bones own 0 points on 61/61 and get none from Auto Skin; nearest-segment agrees with the stock "
        f"skin on {np.mean(agree):.1%} of points (min {min(agree):.1%}): a first pass, fixed by hand")


# ------------------------------------------------------------------------------------------ 6. review fixes
def review_fixes():
    """The live vertex-group skin rules, the empty-host STOCK line and the scaffold layout after a reskin."""
    # live groups: a rigid reassignment is adopted, a removed group shows unowned, blended keeps its stored owner
    w = [[(0, 1.0)], [(2, 1.0)], [], [(0, 0.5), (1, 0.5)], []]
    stored = [0, 1, 1, 1, -1]
    own, adopted, unskinned, blended = core.skin_group_state(w, stored)
    assert list(own) == [0, 2, 1, 1, -1] and adopted == [1] and unskinned == [2] and blended == [3]
    assert list(core.live_owners(own, unskinned)) == [0, 2, -1, 1, -1]
    assert core.skin_owners(w, stored)[2] and core.skin_owners(w[:2] + [[(1, 1.0)], [(1, 1.0)], []], stored)[1] == [1]
    why = core.skin_edit_refusal(unskinned, blended, [0], w, ["a", "b", "c"])
    assert why and "1 with blended" in why and "a 0.50, b 0.50" in why and "(points 2;" in why, why
    assert core.skin_edit_refusal(unskinned, blended, [2, 3]) is None        # the edit covers them: it fixes them
    assert core.skin_edit_refusal([], [], []) is None

    # empty connection host bones: none on the base game, reported when a reskin empties one
    for f in ENGINE:
        assert not core.host_bone_problems(core.read_source(f)), os.path.basename(f)
    tuna = os.path.join(REPO, "Assets", "stock", "tuna.sgm")
    tp = core.host_bone_problems(core.read_source(tuna)) if os.path.exists(tuna) else ["STOCK skipped"]
    assert tp and all(x.startswith("STOCK ") for x in tp) and not core.blocking(tp)
    f = os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
    d = core.load_creature(f)
    src = d.model
    owners = np.array(R.vertex_bone_owners(src), dtype=np.int64)
    names = [b.name.lower() for b in src.bones]
    host = names.index(src.limb_connections[0].bone_name.lower())
    par = src.bones[host].parent
    vo = owners.copy()
    vo[owners == host] = int(par)
    m = core.build_export(core.EditState(f, d.verts, vert_owner=vo)).model
    hp = core.host_bone_problems(m)
    assert hp and any(src.limb_connections[0].name in x for x in hp), hp
    assert any(x in core.validate_model(m) for x in hp)

    # a file off the stock layout (the scaffold with every bone's aux_count stale -- what ICParse's scaffold wrote
    # before 2026-11-15, when it copied the template's words): one reskinned point puts EVERY bone on the layout
    from ICParse.sgm.creature import scaffold
    path = os.path.join(TMP, "bz_boner_scaffold.sgm")
    sc = scaffold.build_scaffold_creature("BonerScaffold", detail=1, clips=False, ik=False)
    assert not core.aux_rule_problems(sc), core.aux_rule_problems(sc)[:3]     # the builder follows the rule now
    for bone in sc.bones:
        bone.meta5 = bone.vertex_count
    with open(path, "wb") as fh:
        fh.write(SP.write(sc))
    d = core.load_creature(path)
    before = len(core.aux_rule_problems(d.model))
    own = np.array(R.vertex_bone_owners(d.model), dtype=np.int64)
    a = int(np.bincount(own[own >= 0]).argmax())
    b = next(i for i in range(len(d.model.bones)) if i != a and (own == i).any())
    vo = own.copy()
    vo[np.nonzero(own == a)[0][0]] = b
    m = core.build_export(core.EditState(path, d.verts, vert_owner=vo)).model
    assert before > 0 and not core.aux_rule_problems(m), (before, core.aux_rule_problems(m)[:3])
    say(f"6 FIXES: live groups (adopt / unskinned shown unowned / blended kept), edit refusal outside the edit; empty "
        f"host STOCK line (0 on 61/61, {len(tp)} on tuna, raised when a reskin empties cheetah's "
        f"'{src.bones[host].name}'); scaffold {before} layout problems -> 0 after a 1-point reskin")


if __name__ == "__main__":
    stock_layout()
    reskin_corpus()
    unowned_cases()
    colours()
    picking()
    review_fixes()
    say(f"BONER_TEST_PASS ({time.perf_counter() - T0:.1f} s)")
    open("/tmp/bz_boner_test_done.txt", "w").write("PASS\n")
