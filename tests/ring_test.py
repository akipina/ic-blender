"""Connection rings without Blender: the storage encoding, the winding / start rules, the auto-fix and guided picking.
    python blender_native/tests/ring_test.py

Needs the base-game creatures (Assets/engine_stock, or $BZ_STOCK_DIR) and the add-on stock (Assets/stock).
1. corpus: every ring of both corpora is closed edge pairs over its corner loop -- ICParse ring_pairs(ring_corners(vi))
   == vi, ring_corners == combotest.geometry.ring_loop, ring_corners(ring_pairs(c)) == c -- so the add-on's
   corner-loop store re-encodes every untouched ring to its own ints; every base-game ring is a valid RingPicker
   sequence (owner-limb corners joined by patch sides, the slot's corner count); the scaffold's rings are ring_pairs;
2. winding: winding_into_torso (with the editor-table torso centre, combocore.torso_centre) holds on all 730 base-game
   rings and every New Creature ring, and fails on every reversed copy; precheck (11a included) is ERROR-free on the
   61 base-game creatures without partners;
3. ring_fix on every reversal x rotation of the 726 base-game limb-slot rings restores >= 6236 / 6296, the misses sit
   in exactly the 12 pinned rings, and >= 724 / 726 unscrambled rings are left as they are;
4. RingPicker on the snail head: candidates, next corners, the modal's event rule (refused click, Backspace, auto-close,
   navigation pass-through, Esc), a reversed + rotated pick finished back to the stock order (with stock partners),
   the winding alone and the start note without them, the size refusal;
5. ring_status: the snail's rings all OK against the stock partners; reversed ERROR, rotated WARN, a verbatim
   non-standard ring ERROR, a 3-corner leg ring ERROR, no partners WARN;
6. placement: the torso / slot-limb boundary rule is silent on every stock ring of both corpora and WARNs for a ring on
   the wrong socket or around an interior torso patch (ring_status and Validate's precheck lines); ring_corners
   refuses non-pair ints; RingPicker refuses an open chain, a corner outside the owner limb and a bad fix.
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
from ICParse.sgm.creature.model import ring_corners, ring_pairs  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

T0 = time.perf_counter()
SNAIL = os.path.join(REPO, "Assets", "stock", "snail.sgm")
SNAIL_HEAD = [769, 770, 772, 771, 767, 768]
# the base-game rings whose stock order is not what the Quick donors prefer (arm stubs, raven's side wing, the poison
# frog's rear legs): ring_fix restores them only from some scrambles (t0_ring_autofix_misses.py)
EXPECTED_MISSES = {("dragonfly", "lfarm"), ("dragonfly", "rfarm"), ("giraffe", "lfarm"), ("giraffe", "rfarm"),
                   ("horse", "lfarm"), ("horse", "rfarm"), ("poison_frog", "lrleg"), ("poison_frog", "rrleg"),
                   ("raven", "lfarm"), ("raven", "lswing"), ("zebra", "lfarm"), ("zebra", "rfarm")}


def say(*a):
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def tables(m):
    """The add-on's view of a creature: IC-axis world points, (Np, 16) patches, patch limb, limb ids."""
    W = np.asarray(R.world_vertices(m), dtype=np.float64)
    P = np.array([list(p.control_points) for p in m.patches], dtype=np.int64).reshape(-1, 16)
    return W, P, core._patch_limb_of(m), [int(lm.limb_id) for lm in m.limbs]


def picker_for(m, lc, P=None, pl=None, ids=None, sides=None):
    if P is None:
        _W, P, pl, ids = tables(m)
    return core.RingPicker(P, pl, ids, lc.flags1, lc.flags2, name=lc.name, sides=sides)


def corpus(stock):
    from combotest import geometry as G
    cnt = collections.Counter()
    for d in (stock, os.path.join(REPO, "Assets", "stock")):
        for f in sorted(glob.glob(os.path.join(d, "*.sgm"))):
            m = SP.read(f)
            if core.is_scene_model(m) or not getattr(m, "patches", None):
                continue
            base = d == stock
            _W, P, pl, ids = tables(m)
            sides = core.corner_sides(P)
            for lc in m.limb_connections:
                vi = [int(v) for v in lc.vertex_indices]
                c = ring_corners(vi)
                assert c is not None and ring_pairs(c) == vi, (f, lc.name, vi)
                assert c == G.ring_loop(vi) and ring_corners(ring_pairs(c)) == c, (f, lc.name)
                assert core.ring_edge_error(c, sides) is None, (f, lc.name, core.ring_edge_error(c, sides))
                cnt["base" if base else "addon"] += 1
                pk = picker_for(m, lc, P, pl, ids, sides)
                where = pk.placement_problem(c)
                assert where is None, (f, lc.name, where)       # the placement rule never fires on a stock ring
                cnt["placed_checked"] += pk.slot_patches > 0 and pk.slot_key is not None
                if base:
                    why = pk.set_sequence(c) or pk.check()
                    assert why is None, (f, lc.name, why)
                    assert pk.want is None or pk.want == len(c), (f, lc.name, pk.want, len(c))
                    cnt["picker_ok"] += 1
    assert cnt["base"] == 730 and cnt["picker_ok"] == 730 and cnt["addon"] >= 700, dict(cnt)
    assert cnt["placed_checked"] >= 600, dict(cnt)                     # rings whose slot limb has patches (678)
    # ICParse ring_corners refuses what is not closed edge pairs
    for bad in ([1, 2, 3, 4], [1, 2, 2, 3, 3, 4], [1, 2, 2, 3, 3], [], [5, 5, 5, 5, 5, 5], [1, 2, 3, 1, 2, 3]):
        assert ring_corners(bad) is None, (bad, ring_corners(bad))
    assert ring_corners([1, 2, 2, 3, 3, 1]) == [1, 2, 3] and ring_pairs([1, 2, 3]) == [1, 2, 2, 3, 3, 1]
    from ICParse.sgm.creature import scaffold
    for detail in (1, 2):
        sm = scaffold.build_scaffold_creature("RingT", detail=detail, clips=False, ik=False)
        for lc in sm.limb_connections:
            c = ring_corners(lc.vertex_indices)
            assert c is not None and ring_pairs(c) == list(lc.vertex_indices), lc.name
    say(f"corpus: {cnt['base']} base-game + {cnt['addon']} add-on stock rings are closed edge pairs over their corners "
        f"(== ring_loop), every edge a patch side; {cnt['picker_ok']} base-game rings are valid RingPicker sequences; "
        f"the placement rule is silent on all {cnt['placed_checked']} rings whose slot limb has patches; ring_corners "
        "refuses non-pair / open / odd ints")


def winding(stock, models):
    from combotest import validate as V
    from combotest.loader import Parent
    from ICParse.sgm.creature import scaffold
    n = 0
    for stem, m in models.items():
        W, P, pl, ids = tables(m)
        tc = CC.torso_centre(W, P, pl, ids)
        par = Parent.from_model(m, name=stem)
        tl = par.limb_for(V.TORSO)
        assert np.allclose(tc, V.torso_centroid(W, [p.control_points for p in m.patches], tl.prims)), stem
        for lc in m.limb_connections:
            c = ring_corners(lc.vertex_indices)
            assert V.winding_into_torso(W[c], tc)[0], (stem, lc.name)
            assert not V.winding_into_torso(W[c[::-1]], tc)[0], (stem, lc.name, "reversed")
            n += 1
    assert n == 730, n
    for detail in (1, 2):
        sm = scaffold.build_scaffold_creature("RingT", detail=detail, clips=False, ik=False)
        W, P, pl, ids = tables(sm)
        tc = CC.torso_centre(W, P, pl, ids)
        for lc in sm.limb_connections:
            c = ring_corners(lc.vertex_indices)
            assert V.winding_into_torso(W[c], tc)[0] and not V.winding_into_torso(W[c[::-1]], tc)[0], lc.name
    geo = V._Geo()
    parents = V.load_stock(stock)                           # with their .lua (a missing size is an ERROR of its own)
    assert len(parents) == len(models)
    bad = {stem: [x for x in V.precheck(p, _geo=geo) if x.startswith("ERROR")] for stem, p in parents.items()}
    assert not {k: v for k, v in bad.items() if v}, str({k: v for k, v in bad.items() if v})[:2000]
    say(f"winding: {n} base-game rings wind into the torso, reversed copies do not; New Creature rings too; precheck "
        f"(11a) 0 ERROR on {len(models)} creatures")


def autofix(stock, models):
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
            dirs, names = CC.partner_ring_dirs(stock, s.key, exclude_stem=stem)
            assert stem not in names
            cs = ring_corners(lc.vertex_indices)
            k = len(cs)
            for rev in (0, 1):
                for r in range(k):
                    scr = cs[::-1] if rev else list(cs)
                    scr = scr[r:] + scr[:r]
                    out, notes = CC.ring_fix(W[scr], scr, s.key, tc, dirs)
                    cnt["cases"] += 1
                    if out == cs:
                        cnt["restored"] += 1
                    else:
                        misses[(stem, s.key)] += 1
                    if rev == 0 and r == 0:
                        cnt["rings"] += 1
                        cnt["untouched_kept"] += out == cs
                        assert out != cs or not notes, (stem, s.key, notes)
                    if rev and out == cs:
                        assert "reversed to wind into the torso" in notes, notes
    say(f"ring_fix: {cnt['restored']}/{cnt['cases']} scrambles restored, {cnt['untouched_kept']}/{cnt['rings']} "
        f"unscrambled rings kept; misses in {sorted(misses)}")
    assert cnt["cases"] == 6296 and cnt["rings"] == 726, dict(cnt)
    assert cnt["restored"] >= 6236 and cnt["untouched_kept"] >= 724, dict(cnt)
    assert set(misses) == EXPECTED_MISSES, sorted(set(misses) ^ EXPECTED_MISSES)


def picker(stock):
    m = SP.read(SNAIL)
    W, P, pl, ids = tables(m)
    lc = m.limb_connections[0]
    assert ring_corners(lc.vertex_indices) == SNAIL_HEAD, lc.vertex_indices
    pk = picker_for(m, lc)
    assert (pk.slot_key, pk.want, pk.owner_code) == ("head", 6, 0x800), (pk.slot_key, pk.want, pk.owner_code)
    assert set(SNAIL_HEAD) <= pk.candidates and pk.allowed() == pk.candidates
    for a, b in zip(SNAIL_HEAD, SNAIL_HEAD[1:]):
        assert pk.add(a) is None and b in pk.allowed(), (a, b, sorted(pk.allowed()))
    pk.picked = []
    assert any(len(pk.sides[k]) == 2 for k in pk.preferred) and pk.preferred, "no preferred torso/head sides"
    assert "6 corners" in pk.status_text() and "Head" in pk.status_text()

    # the modal's event rule
    scr = SNAIL_HEAD[::-1]
    scr = scr[3:] + scr[:3]                                 # reversed, starting at its 4th corner
    far = next(c for c in sorted(pk.candidates) if c not in pk.adj.get(scr[0], set()) and c != scr[0])
    assert pk.handle("LEFTMOUSE", "PRESS", scr[0]) == "RUNNING" and pk.picked == [scr[0]]
    assert pk.handle("LEFTMOUSE", "PRESS", far) == "RUNNING" and "patch side" in pk.message and pk.picked == [scr[0]]
    assert pk.handle("LEFTMOUSE", "PRESS", None) == "RUNNING" and pk.message
    assert pk.handle("LEFTMOUSE", "PRESS", 999999) == "RUNNING" and "not a patch corner" in pk.message
    assert pk.handle("MIDDLEMOUSE", "PRESS", None) == "PASS_THROUGH"
    assert pk.handle("WHEELUPMOUSE", "PRESS", None) == "PASS_THROUGH" and pk.handle("NUMPAD_5", "PRESS", None) == "PASS_THROUGH"
    assert pk.handle("LEFTMOUSE", "RELEASE", scr[1]) == "RUNNING" and pk.picked == [scr[0]]
    for c in scr[1:5]:
        assert pk.handle("LEFTMOUSE", "PRESS", c) == "RUNNING", (c, pk.message)
    assert pk.handle("RET", "PRESS", None) == "RUNNING" and "needs 6 corners, 5 picked" in pk.message
    try:
        pk.finish()
        raise AssertionError("5 corners finished")
    except ValueError as e:
        assert "needs 6 corners, 5 picked" in str(e), e
    assert pk.handle("BACK_SPACE", "PRESS", None) == "RUNNING" and pk.picked == scr[:4]
    assert pk.handle("LEFTMOUSE", "PRESS", scr[4]) == "RUNNING"
    assert pk.handle("LEFTMOUSE", "PRESS", scr[5]) == "FINISH", pk.message        # auto-closed at 6 corners
    # hover: the nearest allowed corner within 15 px, else the nearest candidate (to say why), else nothing
    screen = {1: (10.0, 10.0), 2: (14.0, 10.0), 3: (100.0, 100.0), 4: None}
    assert core.RingPicker.nearest(screen, (13.0, 10.0), {1}) == 1
    assert core.RingPicker.nearest(screen, (13.0, 10.0), set()) == 2
    assert core.RingPicker.nearest(screen, (60.0, 60.0), {1, 2, 3}) is None

    tc = CC.torso_centre(W, P, pl, ids)
    dirs, names = CC.partner_ring_dirs(stock, "head", exclude_stem="snail")
    assert len(dirs) == 6, names
    corners, notes = pk.finish(lambda c: CC.ring_fix(W[c], c, "head", tc, dirs))
    assert corners == SNAIL_HEAD, (corners, notes)
    assert notes[0] == "reversed to wind into the torso" and notes[1].startswith("rotated by 3 to match the stock start"), notes
    corners2, notes2 = pk.finish(lambda c: CC.ring_fix(W[c], c, "head", tc, []))
    assert corners2 == scr[::-1] and corners2 != SNAIL_HEAD and notes2 == ["reversed to wind into the torso",
                                                                           CC.NO_PARTNERS_NOTE], (corners2, notes2)
    assert CC.ring_fix(W[SNAIL_HEAD], SNAIL_HEAD, "head", tc, dirs) == (SNAIL_HEAD, [])
    pk2 = picker_for(m, lc)
    assert pk2.set_sequence(SNAIL_HEAD[:5]) is None and "needs 6 corners, 5 picked" in pk2.check()
    assert pk2.set_sequence([SNAIL_HEAD[0], SNAIL_HEAD[2]]) and pk2.set_sequence([SNAIL_HEAD[0], SNAIL_HEAD[0]])
    # an open chain of 6 torso corners joined by sides whose ends share no side: check() refuses it (does not close)
    def dfs(path):
        if len(path) == 6:
            return path if not pk2.adjacent(path[-1], path[0]) else None
        for nxt in sorted(pk2.adj.get(path[-1], ())):
            if nxt in pk2.candidates and nxt not in path:
                r = dfs(path + [nxt])
                if r:
                    return r
        return None
    chain = dfs([SNAIL_HEAD[0]])
    assert chain and pk2.set_sequence(chain) is None and "does not close" in (pk2.check() or ""), (chain, pk2.check())
    try:
        pk2.finish()
        raise AssertionError("an open chain finished as a ring")
    except ValueError as e:
        assert "does not close" in str(e), e
    # only the owner limb's (torso's) corners: a head-only corner is refused
    torso = {li for li, lid in enumerate(ids) if lid & core.CLASS_MASK == core.TORSO_CODE}
    torso_c = {int(P[p][k]) for p in range(len(P)) if pl[p] in torso for k in core.PATCH_CORNERS}
    other = min({int(P[p][k]) for p in range(len(P)) if pl[p] not in torso for k in core.PATCH_CORNERS} - torso_c)
    why = picker_for(m, lc).add(other)
    assert why and "not a patch corner of the torso" in why, why
    # a fix that returns something other than a rotation / reversal of the pick is refused
    try:
        pk.finish(lambda c: (list(c[:-1]) + [c[0]], []))
        raise AssertionError("a bad ring fix was accepted")
    except ValueError as e:
        assert "not a rotation or reversal" in str(e), e
    say(f"RingPicker: snail head picked reversed from its 4th corner -> {corners} ({'; '.join(notes)}); without "
        f"partners {corners2}")
    return m, W, P, pl, ids


def status(stock, m, W, P, pl, ids):
    rows = [(lc.name, lc.bone_name, ring_corners(lc.vertex_indices), 1, lc.flags1, lc.flags2) for lc in m.limb_connections]
    st = CC.ring_status(W, P, pl, ids, rows, stock, exclude_stem="snail")
    assert [s.level for s in st] == ["OK"] * len(rows), [(s.name, s.problems) for s in st if s.level != "OK"]
    assert st[0].size_text == "6/6 corners" and st[0].attach == "torso" and st[0].slot_label == "Head", st[0]
    key = {s.slot_key: i for i, s in enumerate(st)}
    head, tail, leg, wing = key["head"], key["tail"], key["lfleg"], key["rswing"]
    bad = list(rows)
    bad[head] = rows[head][:2] + (SNAIL_HEAD[::-1],) + rows[head][3:]
    bad[tail] = rows[tail][:2] + (rows[tail][2][2:] + rows[tail][2][:2],) + rows[tail][3:]
    bad[wing] = rows[wing][:2] + (list(m.limb_connections[wing].vertex_indices)[:6], 0) + rows[wing][4:]
    bad[leg] = rows[leg][:2] + (rows[leg][2][:3],) + rows[leg][3:]
    sb = CC.ring_status(W, P, pl, ids, bad, stock, exclude_stem="snail")
    assert sb[head].level == "ERROR" and any("wound with its normal out of the torso" in p for p in sb[head].problems), \
        sb[head]
    assert sb[tail].level == "WARN" and any("off the stock start" in p for p in sb[tail].problems), sb[tail]
    assert sb[wing].level == "ERROR" and "non-standard ring encoding (6 ints)" in sb[wing].problems[0], sb[wing]
    assert sb[wing].size_text == "6 ints (verbatim)"
    assert sb[leg].level == "ERROR" and sb[leg].problems[0].startswith("ERROR 3 corners, the L Front Leg slot needs 4") \
        and any("no patch side" in p for p in sb[leg].problems) and len(sb[leg].problems) == 2, sb[leg].problems
    assert all(sb[i].level == "OK" for i in range(len(sb)) if i not in (head, tail, leg, wing))
    far = list(rows)
    far[head] = rows[head][:2] + ([999999] + SNAIL_HEAD[1:],) + rows[head][3:]
    so = CC.ring_status(W, P, pl, ids, far, stock, exclude_stem="snail")
    assert so[head].level == "ERROR" and so[head].problems == ["ERROR ring index out of the control-point pool"], so[head]
    sn = CC.ring_status(W, P, pl, ids, rows, None)
    assert all(s.level == "WARN" and "no stock creatures" in s.problems[0] for s in sn if s.slot_key), sn[0].problems
    say("ring_status: snail all OK; reversed ERROR, rotated WARN, verbatim ERROR, 3-corner leg ERROR, no partners WARN")


def placement():
    """The placement rule on the lion (its leg limbs have patches): the stock L Front Leg ring is on the torso / leg
    boundary; the R Front Leg's ring set as the L Front Leg's, and a loop around one interior torso patch, are not
    (ring_status WARN, placement_problem text, ring_placement_messages for Validate)."""
    m = SP.read(os.path.join(REPO, "Assets", "stock", "lion.sgm"))
    W, P, pl, ids = tables(m)
    conns = {lc.name.lower().rsplit("_", 1)[-1]: i for i, lc in enumerate(m.limb_connections)}
    li, ri = conns["lfleg"], conns["rfleg"]
    lf, rf = m.limb_connections[li], m.limb_connections[ri]
    pk = picker_for(m, lf)
    stock_l, stock_r = ring_corners(lf.vertex_indices), ring_corners(rf.vertex_indices)
    assert pk.slot_patches > 0 and pk.placement_problem(stock_l) is None
    why = pk.placement_problem(stock_r)
    assert why and why.startswith("only 0 of its 4 sides border the L Front Leg limb's patches"), why
    torso = {k for k, lid in enumerate(ids) if lid & core.CLASS_MASK == core.TORSO_CODE}
    sides = core.corner_sides(P)
    interior = None
    for p in range(len(P)):
        if pl[p] in torso:
            c = [int(P[p][k]) for k in (0, 3, 15, 12)]
            if core.ring_edge_error(c, sides) is None and pk.placement_problem(c):
                interior = c
                break
    assert interior is not None
    rows = [(lc.name, lc.bone_name, ring_corners(lc.vertex_indices), 1, lc.flags1, lc.flags2) for lc in m.limb_connections]
    rows[li] = rows[li][:2] + (stock_r,) + rows[li][3:]
    st = CC.ring_status(W, P, pl, ids, rows, None)
    assert st[li].level == "WARN" and any("sides border the L Front Leg" in x for x in st[li].problems), st[li].problems
    rows[li] = rows[li][:2] + (interior,) + rows[li][3:]
    st = CC.ring_status(W, P, pl, ids, rows, None)
    assert any("sides border the L Front Leg" in x for x in st[li].problems), st[li].problems
    assert not CC.ring_placement_messages(m)
    m.limb_connections[li].vertex_indices = ring_pairs(stock_r)
    msgs = CC.ring_placement_messages(m)
    assert len(msgs) == 1 and msgs[0].startswith(f"COMBINE WARN precheck: ring {lf.name}: only 0 of its 4 sides"), msgs
    say(f"placement: lion R Front Leg ring as L Front Leg and an interior torso loop {interior} WARN; stock rings silent")


def main():
    assert CC.available(), CC.IMPORT_ERROR
    stock = CC.stock_dir()
    assert stock and os.path.exists(os.path.join(stock, "cheetah.sgm")), stock
    corpus(stock)
    models = {os.path.basename(f)[:-4]: SP.read(f) for f in sorted(glob.glob(os.path.join(stock, "*.sgm")))}
    assert len(models) == 61, len(models)
    winding(stock, models)
    autofix(stock, models)
    status(stock, *picker(stock))
    placement()
    say(f"PASS in {time.perf_counter() - T0:.1f} s")


if __name__ == "__main__":
    main()
