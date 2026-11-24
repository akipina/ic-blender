"""The ring-start optimiser without Blender: the rotation search, what it does to the 61 base-game creatures, and
the reversal the format allows but the winding rule forbids.

    python blender_native/tests/ring_start_test.py

Needs the base-game creatures (Assets/engine_stock, or $BZ_STOCK_DIR).
1. synthetic: a square socket ring and a partner square; every rotation of the ring is found and undone, the stock
   start is refused as already best, the plan's per-rotation scores are the mean by-index corner angle, and a partner
   whose ring runs the other way round is REPORTED (reversed_wins / reversed_gain), never reversed;
2. stock corpus: the plan judges every one of the 726 base-game limb-slot rings, and moves exactly the 2 pinned raven
   rings (Eagle_LSWing / Eagle_LFArm, both over the precheck's margin against a real donor); no base-game ring's
   partners prefer the reversed pairing by more than the margin;
3. the mirror rule, a second and exact measure that needs no partners: a creature's Left and Right socket rings are
   geometric mirrors, and 300 of the 302 base-game pairs also start at mirror corners -- the 2 that do not are the
   same 2 rings the partner metric moves;
4. restore: every rotation of every base-game ring is put back on its own start corner (or, for the 2 pinned rings,
   on the start the metric prefers), with the winding untouched;
5. the metric in metres: rotating a ring's start moves each welded corner along the seam by up to the ring's own size
   (cheetah's L Front Leg socket: 320 mm at shift 2; a compact insect leg socket: tens of mm) -- the number the
   operator reports next to the degrees;
6. refusals: a ring wound out of the torso, a verbatim (non-standard) ring, fewer than 3 corners, no partners.
"""
import collections
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import combocore as CC  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.sgm.creature.model import ring_corners  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

T0 = time.perf_counter()
# the base-game rings the optimiser moves: the raven's left side wing and left front arm (its right-side twins are
# on the stock start, so the base game itself authored these two out of order)
EXPECTED_MOVES = {("raven", "lswing"), ("raven", "lfarm")}


def say(*a):
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def tables(m):
    W = np.asarray(R.world_vertices(m), dtype=np.float64)
    P = np.array([list(p.control_points) for p in m.patches], dtype=np.int64).reshape(-1, 16)
    return W, P, core._patch_limb_of(m), [int(lm.limb_id) for lm in m.limbs]


def partners(stock, key, stem, k):
    """The optimiser's partner set: the donors that really have that limb, else every stock ring of the slot."""
    dirs, names = CC.partner_ring_dirs(stock, key, exclude_stem=stem, donors_only=True)
    if not [d for d in dirs if len(d) == k]:
        dirs, names = CC.partner_ring_dirs(stock, key, exclude_stem=stem)
    return dirs, names


def synthetic():
    """A square ring against a square partner, in the plane z = 0 with the torso above it."""
    from combotest import validate as V
    X = np.array([[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]])
    corners = [10, 11, 12, 13]
    tc = np.array([0.0, 0.0, 5.0])                     # the torso: the winding normal must point at it
    assert V.winding_into_torso(X, tc)[0] and not V.winding_into_torso(X[::-1], tc)[0]
    dd = V._corner_dirs(X)                             # the partner: the same ring
    plan = CC.ring_start_plan(X, corners, "head", tc, [dd], ["twin"])
    assert not plan.ok and plan.shift == 0 and plan.n == 1 and plan.partners == ["twin"], plan
    assert plan.before_deg < 1e-4 and plan.gap_before < 1e-4 and plan.over_before == 0, plan
    assert "already the best" in plan.reason, plan.reason
    for s in (1, 2, 3):                                 # every rotation of it is found and undone
        rot = np.roll(X, -s, axis=0)
        rc = corners[s:] + corners[:s]
        p = CC.ring_start_plan(rot, rc, "head", tc, [dd], ["twin"])
        assert p.ok and p.corners == corners, (s, p.corners, p.reason)
        assert p.shift == (4 - s) % 4 and p.over_before == 1 and p.over_after == 0, (s, p)
        assert abs(p.after_deg) < 1e-4 and p.before_deg > 20.0, (s, p.before_deg, p.after_deg)
        assert len(p.scores) == 4 and abs(p.scores[p.shift] - p.after_deg) < 1e-9, p.scores
        assert abs(p.move_mm - max(np.linalg.norm(rot[i] - rot[(i + p.shift) % 4]) for i in range(4)) * 1000) < 1e-6
    # a partner ring wound the other way: the by-index weld is beaten by the REVERSED pairing, which the plan reports
    # and never applies (rotating cannot reach it; reversing would break the winding rule)
    back = V._corner_dirs(np.array([[-1.0, -1.0, 0.0], [-1.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, -1.0, 0.0]]))
    p = CC.ring_start_plan(X, corners, "head", tc, [back], ["mirror"])
    assert not p.ok and p.reversed_wins == 1 and p.reversed_gain > 20.0, p
    assert "REVERSED" in p.reason and "winding rule forbids" in p.reason, p.reason
    # one ring, two partners that disagree: the shift that leaves the fewest partners over the margin wins
    other = V._corner_dirs(np.roll(X, -1, axis=0))
    p = CC.ring_start_plan(X, corners, "head", tc, [dd, other], ["twin", "off_by_one"])
    assert not p.ok and p.over_before == 1 and p.n == 2, p          # 1 of 2 is not fewer than the alternatives' 1 of 2
    assert abs(min(p.scores) - p.scores[0]) < 1e-4, p.scores        # the stock-matching start is still the best mean
    say("synthetic: a square ring's 3 wrong starts are found and undone (mean corner angle 90/180/90 -> 0 deg, "
        "partners over the margin 1 -> 0), the right one is refused, a mirrored partner is reported as REVERSED")


def refusals():
    from combotest import validate as V
    X = np.array([[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]])
    c = [10, 11, 12, 13]
    dd = V._corner_dirs(X)
    tc = np.array([0.0, 0.0, 5.0])
    p = CC.ring_start_plan(X[::-1], c[::-1], "head", tc, [dd], ["twin"])
    assert not p.ok and "out of the torso" in p.reason and "no rotation fixes a reversed ring" in p.reason, p.reason
    p = CC.ring_start_plan(X[:2], c[:2], "head", tc, [dd], ["twin"])
    assert not p.ok and "at least 3 corners" in p.reason, p.reason
    p = CC.ring_start_plan(X, c, None, tc, [dd], ["twin"])
    assert not p.ok and "no combiner limb slot" in p.reason, p.reason
    p = CC.ring_start_plan(X, c, "head", tc, [], [])
    assert not p.ok and p.n == 0 and p.reason == CC.NO_PARTNERS_NOTE, p.reason
    p = CC.ring_start_plan(X, c, "head", tc, [V._corner_dirs(np.eye(3) * 2.0)], ["tri"])
    assert not p.ok and p.n == 0 and "no stock head ring has 4 corners" in p.reason, p.reason
    try:
        CC.ring_start_plan(X, c[:3], "head", tc, [dd], ["twin"])
        raise AssertionError("mismatched positions accepted")
    except ValueError as e:
        assert "4 positions for 3 corners" in str(e), e
    say("refusals: a reversed ring, a 2-corner ring, no slot, no partners, partners of another size, "
        "positions that do not match the corners")


def corpus(stock, models):
    """Every base-game limb-slot ring judged by the plan."""
    from combotest.slots import slot_for_flags2, TORSO
    cnt = collections.Counter()
    moved, rev = [], []
    for stem, m in models.items():
        W, P, pl, ids = tables(m)
        tc = CC.torso_centre(W, P, pl, ids)
        for lc in m.limb_connections:
            s = slot_for_flags2(lc.flags2)
            if s is None or s is TORSO:
                continue
            cs = ring_corners(lc.vertex_indices)
            assert cs is not None, (stem, lc.name)
            dirs, names = partners(stock, s.key, stem, len(cs))
            p = CC.ring_start_plan(W[cs], cs, s.key, tc, dirs, names)
            cnt["rings"] += 1
            cnt["judged"] += p.n > 0
            cnt["over_margin"] += p.over_before > 0
            if p.ok:
                moved.append((stem, s.key))
                cnt["moved"] += 1
            else:
                assert p.shift == 0 and p.corners == cs, (stem, s.key, p.corners)
            if p.reversed_gain > p.margin:
                rev.append((stem, s.key, round(p.reversed_gain, 1)))
    assert cnt["rings"] == 726, dict(cnt)
    assert cnt["judged"] == 726, dict(cnt)                      # every ring has a partner ring of its own size
    assert set(moved) == EXPECTED_MOVES, sorted(set(moved) ^ EXPECTED_MOVES)
    assert not rev, rev                                          # no base-game ring welds better reversed
    say(f"corpus: {cnt['rings']} base-game limb-slot rings judged, {cnt['moved']} moved "
        f"({sorted(set(moved))}), {cnt['over_margin']} over the margin as authored, 0 whose partners prefer the "
        "reversed pairing")
    return moved


def restore(stock, models):
    """Every rotation of every base-game ring goes back to the start the metric prefers (its own, but for the 2
    pinned rings), and the corner set and the winding never change."""
    from combotest import validate as V
    from combotest.slots import slot_for_flags2, TORSO
    cnt = collections.Counter()
    misses = collections.Counter()
    for stem, m in models.items():
        W, P, pl, ids = tables(m)
        tc = CC.torso_centre(W, P, pl, ids)
        for lc in m.limb_connections:
            s = slot_for_flags2(lc.flags2)
            if s is None or s is TORSO:
                continue
            cs = ring_corners(lc.vertex_indices)
            dirs, names = partners(stock, s.key, stem, len(cs))
            want = CC.ring_start_plan(W[cs], cs, s.key, tc, dirs, names)
            want = want.corners if want.ok else cs               # where the metric wants this ring to start
            for r in range(1, len(cs)):
                scr = cs[r:] + cs[:r]
                p = CC.ring_start_plan(W[scr], scr, s.key, tc, dirs, names)
                cnt["cases"] += 1
                assert sorted(p.corners) == sorted(cs), (stem, s.key, r, p.corners)
                assert V.winding_into_torso(W[p.corners], tc)[0], (stem, s.key, r)
                if p.corners == want:
                    cnt["restored"] += 1
                else:
                    misses[(stem, s.key)] += 1
    say(f"restore: {cnt['restored']}/{cnt['cases']} rotations of the base-game rings put back on the preferred start"
        + (f"; misses {sorted(misses)}" if misses else ""))
    assert cnt["cases"] == 2422, dict(cnt)                       # every ring x its k - 1 wrong starts
    assert cnt["restored"] == cnt["cases"], dict(misses)          # 100 %: the search is exhaustive, so it always wins


def metres(stock, models):
    """The seam displacement a wrong start costs, in millimetres: the cheetah's L Front Leg socket rotated by 2 (what
    the engine run measured) and the spread over every base-game ring."""
    m = models["cheetah"]
    W, P, pl, ids = tables(m)
    tc = CC.torso_centre(W, P, pl, ids)
    lc = next(x for x in m.limb_connections if x.name == "Cheetah_LFLeg")
    cs = ring_corners(lc.vertex_indices)
    dirs, names = partners(stock, "lfleg", "cheetah", len(cs))
    scr = cs[2:] + cs[:2]
    p = CC.ring_start_plan(W[scr], scr, "lfleg", tc, dirs, names)
    assert p.ok and p.corners == cs and p.shift == 2, p
    assert p.over_before == 4 and p.over_after == 0 and p.n == 4, p     # every stock front-leg donor
    assert 300.0 < p.move_mm < 340.0, p.move_mm                  # the ring's own diagonal
    assert p.before_deg > 150.0 and p.after_deg < 30.0, (p.before_deg, p.after_deg)
    assert f"{p.move_mm:.0f} mm along the seam" in p.reason, p.reason
    sizes = []
    from combotest.slots import slot_for_flags2, TORSO
    for stem, mm in models.items():
        Ww = np.asarray(R.world_vertices(mm), dtype=np.float64)
        for c in mm.limb_connections:
            if slot_for_flags2(c.flags2) in (None, TORSO):
                continue
            X = Ww[ring_corners(c.vertex_indices)]
            k = len(X)
            sizes.append(max(np.linalg.norm(X[i] - X[(i + 1) % k]) for i in range(k)) * 1000.0)
    say(f"metres: the cheetah's L Front Leg ring started 2 corners out moves every welded corner up to "
        f"{p.move_mm:.0f} mm ({p.before_deg:.0f} -> {p.after_deg:.0f} deg, 4 -> 0 partners over the margin); one "
        f"corner step on a base-game ring is {np.median(sizes):.0f} mm median, {min(sizes):.0f}-{max(sizes):.0f} mm")


MIRROR_PAIRS = [("lfleg", "rfleg"), ("lmleg", "rmleg"), ("lm2leg", "rm2leg"), ("lrleg", "rrleg"),
                ("lswing", "rswing"), ("ltwing", "rtwing"), ("lfarm", "rfarm")]


def mirror_pairing(W, left, right):
    """``((reversed, shift), mean distance in m)``: how a creature's Left ring, mirrored across x = 0, pairs with its
    Right ring. The base game's convention is ``(1, 2)`` -- mirroring flips the winding, so the Right ring runs the
    other way round, and it starts 2 corners on."""
    A = np.asarray(W, dtype=np.float64)[left].copy()
    A[:, 0] *= -1.0
    B = np.asarray(W, dtype=np.float64)[right]
    k = len(A)
    out, bd = None, float("inf")
    for rev in (0, 1):
        for sh in range(k):
            idx = [((k - 1 - ((i + sh) % k)) if rev else ((i + sh) % k)) for i in range(k)]
            d = float(np.linalg.norm(A - B[idx], axis=1).mean())
            if d < bd:
                bd, out = d, (rev, sh)
    return out, bd


def mirror(stock, models, moved):
    """A second, exact rule, independent of any partner: a creature's Left and Right socket rings are geometric
    mirrors, and the base game starts them at mirror corners. It picks out exactly the rings the partner metric
    moves, which is why the metric is trusted."""
    from combotest.slots import slot_for_flags2, TORSO
    cnt = collections.Counter()
    odd = []
    worst = 0.0
    for stem, m in models.items():
        W = np.asarray(R.world_vertices(m), dtype=np.float64)
        size = float(np.linalg.norm(W.max(axis=0) - W.min(axis=0)))
        rings = {}
        for lc in m.limb_connections:
            s = slot_for_flags2(lc.flags2)
            if s is not None and s is not TORSO:
                rings[s.key] = ring_corners(lc.vertex_indices)
        for L, Rk in MIRROR_PAIRS:
            if L not in rings or Rk not in rings or len(rings[L]) != len(rings[Rk]):
                continue
            pairing, d = mirror_pairing(W, rings[L], rings[Rk])
            cnt["pairs"] += 1
            cnt["mirrored"] += d < 0.004 * size                 # the two rings ARE mirror images (median 4e-5 %,
                                                    # worst 0.30 % of the creature: the hammerhead shark)
            worst = max(worst, d / size)
            if pairing == (1, 2):
                cnt["stock_convention"] += 1
            else:
                odd.append((stem, L, pairing, round(d * 1000, 2)))
    assert cnt["pairs"] == 302 and cnt["mirrored"] == 302, dict(cnt)
    assert cnt["stock_convention"] == 300, dict(cnt)
    assert {(s, k) for s, k, _p, _d in odd} == {("raven", "lswing"), ("raven", "lfarm")}, odd
    assert {(s, k) for s, k in moved} == {(s, k) for s, k, _p, _d in odd}, (sorted(moved), odd)
    say(f"mirror rule: all {cnt['pairs']} Left/Right socket-ring pairs of the 61 base-game creatures are geometric "
        f"mirrors (worst {worst * 100:.3f} % of the creature's size) and {cnt['stock_convention']} of them start at "
        f"mirror corners -- the pairing "
        f"(reversed, shift 2). The 2 that do not are {sorted((s, k) for s, k, _p, _d in odd)}, EXACTLY the rings the "
        f"partner metric moves: two independent measures, one answer")


def pillbug(stock):
    """The fitted pillbug (3DInputs, not in git): its leg sockets are ERRORs in the precheck that NO rotation fixes --
    the reversed pairing beats every rotation, so it is the ring's plane, not its start."""
    path = os.path.join(REPO, "3DInputs", "Pillbug_ic", "pillbug_native_combinable.sgm")
    if not os.path.exists(path):
        say("pillbug: skipped (3DInputs/Pillbug_ic is not in this checkout)")
        return
    from combotest.slots import slot_for_flags2, TORSO
    m = SP.read(path)
    W, P, pl, ids = tables(m)
    tc = CC.torso_centre(W, P, pl, ids)
    rev = []
    for lc in m.limb_connections:
        s = slot_for_flags2(lc.flags2)
        if s is None or s is TORSO:
            continue
        cs = ring_corners(lc.vertex_indices)
        dirs, names = partners(stock, s.key, "pillbug_native_combinable", len(cs))
        p = CC.ring_start_plan(W[cs], cs, s.key, tc, dirs, names)
        if p.reversed_gain > p.margin:
            rev.append((s.key, round(p.reversed_gain), p.over_before, round(p.move_mm)))
    assert {k for k, _g, _o, _m in rev} >= {"lfleg", "rfleg"}, rev
    say(f"pillbug: {len(rev)} leg sockets whose partners weld them better REVERSED (slot, degrees, partners over the "
        f"margin, mm one corner step is worth): {rev} -- a rotation cannot fix a ring whose PLANE differs")


def main():
    assert CC.available(), CC.IMPORT_ERROR
    stock = CC.stock_dir()
    assert stock and os.path.exists(os.path.join(stock, "cheetah.sgm")), stock
    models = {os.path.basename(f)[:-4]: SP.read(f) for f in sorted(glob.glob(os.path.join(stock, "*.sgm")))}
    assert len(models) == 61, len(models)
    synthetic()
    refusals()
    moved = corpus(stock, models)
    mirror(stock, models, moved)
    restore(stock, models)
    metres(stock, models)
    pillbug(stock)
    say(f"PASS in {time.perf_counter() - T0:.1f} s")


if __name__ == "__main__":
    main()
