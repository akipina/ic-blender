"""Headless checks for patchrules (no Blender): the Max Auto Interior rule measured on the stock creatures, the
carry rules and the manual-interior detection.

    .venv/bin/python3 blender_native/tests/patchrules_test.py
"""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import patchrules as PR  # noqa: E402

CORPUS = sorted(glob.glob(os.path.join(REPO, "Assets/engine_stock/*.sgm")))


def candidate_errors(V, P):
    """Per-patch relative error of each candidate interior rule (the worst of the four corner frames)."""
    G = V[P].reshape(-1, 4, 4, 3)
    size = PR.patch_size(V, P)
    errs = {}
    for f0 in (False, True):
        for f1 in (False, True):
            b = G[:, ::-1] if f0 else G
            b = b[:, :, ::-1] if f1 else b
            cands = {"parallelogram P01+P10-P00": b[:, 0, 1] + b[:, 1, 0] - b[:, 0, 0],
                     "midpoint (P01+P10)/2": (b[:, 0, 1] + b[:, 1, 0]) / 2,
                     "Coons (corner-tangent blend)": (-4 * b[:, 0, 0] + 6 * (b[:, 0, 1] + b[:, 1, 0])
                                                      - 2 * (b[:, 0, 3] + b[:, 3, 0]) + 3 * (b[:, 1, 3] + b[:, 3, 1])
                                                      - b[:, 3, 3]) / 9.0,
                     "bilinear corners": (4 * b[:, 0, 0] + 2 * b[:, 0, 3] + 2 * b[:, 3, 0] + b[:, 3, 3]) / 9.0}
            for k, v in cands.items():
                errs.setdefault(k, []).append(np.linalg.norm(v - b[:, 1, 1], axis=1) / size)
    return {k: np.max(es, axis=0) for k, es in errs.items()}


def measure():
    total = 0
    hits = {}
    exceptions = []
    worst_stock = 0.0
    for p in CORPUS:
        d = core.load_creature(p)
        errs = candidate_errors(d.verts, d.patches)
        total += len(d.patches)
        for k, e in errs.items():
            hits[k] = hits.get(k, 0) + int((e < PR.TOL).sum())
        e = errs["parallelogram P01+P10-P00"]
        # the module's own measure agrees with the candidate sweep
        assert np.allclose(PR.interior_error(d.verts, d.patches), e, atol=1e-12)
        for i in np.nonzero(e >= PR.TOL)[0]:
            exceptions.append((os.path.basename(p), int(i), float(e[i])))
        worst_stock = max(worst_stock, float(e.max()))
        # stock pool roles are strict: no point is an interior in one patch and a corner / handle in another
        r = PR.roles(d.patches, len(d.verts))
        assert not (r.corner & r.handle).any(), f"{p}: a point is both corner and handle"
        assert r.interior.sum() == 4 * len(d.patches), f"{p}: interiors shared or mixed"
        # (woolly_mammoth has pool points no patch uses; they are none of the three roles)
        assert (r.corner | r.handle | r.interior).sum() <= len(d.verts), f"{p}: roles overlap"
    assert total > 0, "no stock creatures (Assets/engine_stock)"
    for k, h in hits.items():
        print(f"  {k:32s} within {PR.TOL:g} of the diagonal: {h}/{total} = {100 * h / total:.3f} %")
    rate = hits["parallelogram P01+P10-P00"] / total
    print(f"  exceptions: {exceptions}")
    assert rate > 0.999, f"the parallelogram rule holds on only {100 * rate:.3f} %"
    assert all(hits[k] == 0 for k in hits if not k.startswith("parallelogram")), hits
    return total, rate, exceptions


def carry_checks():
    d = core.load_creature(os.path.join(REPO, "Assets/engine_stock/cheetah.sgm"))
    V, P = d.verts, d.patches
    r = PR.roles(P, len(V))
    H = PR.handles_of(P)
    assert set(H) == set(np.nonzero(r.corner)[0].tolist())
    # a corner used by k patches has 2k handle slots; handles are shared along edges (a closed fan: k unique ones,
    # an open fan k+1, a T-junction handle shared by 3 patches fewer)
    users = np.zeros(len(V), dtype=np.int64)
    np.add.at(users, P[:, list(PR.CORNER_SLOTS)].reshape(-1), 1)
    for c, hs in H.items():
        assert r.handle[hs].all() and 2 <= len(hs) <= 2 * users[c], (c, users[c], len(hs))

    # 1. a corner moves: its handles follow by the same delta; interiors of its patches are recomputed; nothing else
    c = int(np.nonzero(r.corner)[0][10])
    dv = np.array([0.0, 0.0, 0.05])
    cur = V.copy(); cur[c] += dv
    res = PR.carry_handles(V, cur, P)
    assert np.array_equal(res.carried, H[c]), (res.carried, H[c])
    assert np.allclose(res.verts[H[c]], V[H[c]] + dv)
    its = np.nonzero((P[:, list(PR.CORNER_SLOTS)] == c).any(axis=1))[0]
    assert set(res.touched.tolist()) == set(its.tolist())
    assert set(res.recomputed.tolist()) <= set(its.tolist()) and len(res.recomputed) == len(its)
    err = PR.interior_error(res.verts, P)
    assert (err[its] < PR.TOL).all(), err[its]
    moved_pts = np.nonzero(np.any(res.verts != V, axis=1))[0]
    expect = {c} | set(H[c].tolist()) | set(P[its][:, list(PR.INTERIOR_SLOTS)].reshape(-1).tolist())
    assert set(moved_pts.tolist()) <= expect, set(moved_pts.tolist()) - expect
    assert not res.manual.any() and not len(res.manual_new)
    # the translated frame keeps the interior exactly where the rule says: the recomputed interiors of a whole
    # patch translated rigidly would also be its old interiors + dv (rule invariance under translation)
    rows = its[0]
    assert np.allclose(PR.auto_interiors(V + dv, P[[rows]]), PR.auto_interiors(V, P[[rows]]) + dv)

    # 2. a handle moves alone: no corner or other handle moves, its patches' interiors are recomputed
    h = int(H[c][0])
    cur = V.copy(); cur[h] += dv
    res = PR.carry_handles(V, cur, P)
    assert not len(res.carried)
    hp = np.nonzero((P == h).any(axis=1))[0]
    assert set(res.touched.tolist()) == set(hp.tolist()) and set(res.recomputed.tolist()) == set(hp.tolist())
    moved_pts = set(np.nonzero(np.any(res.verts != V, axis=1))[0].tolist())
    # only the interiors whose frame uses that handle (one per patch sharing it) leave the rule and are rewritten
    framed = {int(P[p, i]) for p in hp for i, _c, h1, h2 in PR.INTERIOR_FRAME if h in (int(P[p, h1]), int(P[p, h2]))}
    assert moved_pts == {h} | framed and len(framed) == len(hp), (moved_pts, framed)

    # 3. an interior moved directly to where the rule does not explain: the patch is manual and left alone
    pi = int(its[0])
    i5 = int(P[pi, 5])
    cur = V.copy(); cur[i5] += dv
    res = PR.carry_handles(V, cur, P)
    assert res.manual[pi] and list(res.manual_new) == [pi] and not len(res.recomputed)
    assert np.allclose(res.verts[i5], V[i5] + dv)
    # then its corner moves: handles carried, the manual interiors are left where they are
    cur2 = res.verts.copy(); cur2[c] += dv
    res2 = PR.carry_handles(res.verts, cur2, P, manual=res.manual)
    assert pi not in res2.recomputed and np.allclose(res2.verts[i5], V[i5] + dv)
    others = [int(x) for x in its if x != pi]
    assert set(res2.recomputed.tolist()) == set(others)
    # an interior moved together with its whole patch (a rigid translation) is explained: not manual
    cur = V.copy(); cur[P[pi]] += dv
    res = PR.carry_handles(V, cur, P)
    assert not res.manual[pi] and not len(res.manual_new)
    assert pi not in res.recomputed, "a rigidly moved patch's interiors were rewritten"

    # 4. two moved corners sharing an edge: the edge's handles belong to one corner each, both carried by their own
    # corner; a handle shared by two moved corners (synthetic) gets the mean delta
    c2 = int(P[pi, 3]) if c == int(P[pi, 0]) else int(P[pi, 0])
    cur = V.copy(); cur[c] += dv; cur[c2] -= dv
    res = PR.carry_handles(V, cur, P)
    assert set(res.carried.tolist()) == set(H[c].tolist()) | set(H[c2].tolist())
    assert np.allclose(res.verts[H[c]], V[H[c]] + dv) and np.allclose(res.verts[H[c2]], V[H[c2]] - dv)
    Q = np.array([[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
                  [3, 16, 17, 18, 7, 19, 20, 21, 11, 22, 23, 24, 15, 25, 26, 27]])
    # patch 2 uses corner 3 at its slot 0 and handle 16 at slot 1; make handle 16 also patch 1's slot 2 handle
    Q[0, 2] = 16                                            # slot 2 (adjacent to corner 3): same handle as patch 2's slot 1
    W = np.random.default_rng(0).normal(size=(28, 3))
    cur = W.copy(); cur[3] += [1, 0, 0]; cur[0] += [0, 0, 0]
    res = PR.carry_handles(W, cur, Q)
    assert np.allclose(res.verts[16], W[16] + [1, 0, 0])   # one moved corner: its delta
    cur = W.copy(); cur[3] += [1, 0, 0]; cur[15] += [0, 1, 0]
    Q2 = Q.copy(); Q2[1, 1] = 14                             # patch 2's slot 1 handle = patch 1's slot 14 (corner 15's)
    res = PR.carry_handles(W, cur, Q2)
    assert np.allclose(res.verts[14], W[14] + [0.5, 0.5, 0]), res.verts[14] - W[14]

    # 5. no change -> nothing written; an interior already on the rule within TOL is not rewritten (bytes kept)
    res = PR.carry_handles(V, V, P)
    assert not res.changed.any() and not len(res.touched)
    cur = V.copy(); cur[c] += dv
    once = PR.carry_handles(V, cur, P).verts
    back = once.copy(); back[c] -= dv; back[H[c]] -= dv
    ints = P[its][:, list(PR.INTERIOR_SLOTS)].reshape(-1)
    back[ints] = V[ints]                                     # what Undo restores: the stock interiors
    res = PR.carry_handles(once, back, P)
    assert not res.changed.any(), "an Undo back to stock interiors was rewritten"
    assert np.array_equal(res.verts, back)

    # 5b. anchors: a corner dragged over several frames and back to where it started (a cancelled transform restores
    # exactly that) puts its handles AND the rewritten interiors back bit-exactly, in float32 as the mesh holds them
    V32 = V.astype(np.float32).astype(np.float64)
    state = V32.copy(); anchor = anchored = None
    for step in (0.013, 0.029, 0.041, 0.0):
        nxt = state.copy(); nxt[c] = V32[c] + np.array([step, 0.7 * step, -0.3 * step])
        res = PR.carry_handles(state, nxt, P, anchor=anchor, anchored=anchored)
        anchor, anchored = res.anchor, res.anchored
        state = res.verts.astype(np.float32).astype(np.float64)       # what the mesh keeps
        if step:
            assert np.allclose(state[H[c]], V32[H[c]] + (nxt[c] - V32[c]), atol=1e-6)
    assert np.array_equal(state, V32), f"{int(np.any(state != V32, axis=1).sum())} points off after the return"
    # a handle moved by itself keeps its new place through a corner's out-and-back (the whole anchor state -- carry
    # anchors and the B6 home anchors -- is threaded between calls, as patchfeel.settle does)
    state = V32.copy(); kw = {}
    nxt = state.copy(); nxt[h] += dv
    res = PR.carry_handles(state, nxt, P, **kw)
    kw = dict(anchor=res.anchor, anchored=res.anchored, rel=res.rel, rel_act=res.rel_act)
    state = res.verts.astype(np.float32).astype(np.float64); after_h = state.copy()
    for step in (0.02, 0.0):
        nxt = state.copy(); nxt[c] = V32[c] + step
        res = PR.carry_handles(state, nxt, P, **kw)
        kw = dict(anchor=res.anchor, anchored=res.anchored, rel=res.rel, rel_act=res.rel_act)
        state = res.verts.astype(np.float32).astype(np.float64)
    assert np.array_equal(state, after_h), "the corner's out-and-back disturbed a handle edited by itself"

    # 6. reset: manual interiors back on the rule, only the rows asked for
    cur = V.copy(); cur[i5] += dv
    out, rows = PR.reset_interiors(cur, P, [pi])
    assert list(rows) == [pi] and PR.interior_error(out, P)[pi] < PR.TOL
    out2, rows2 = PR.reset_interiors(V, P)
    assert len(rows2) == 0 and np.array_equal(out2, V), "a stock net was rewritten by reset"
    print(f"  carry: corner {c} ({len(H[c])} handles, {len(its)} patches), handle {h}, manual patch {pi} OK")


def main():
    total, rate, exceptions = measure()
    carry_checks()
    print(f"PATCHRULES_PASS rule=P11=P01+P10-P00 patches={total} match={100 * rate:.3f}% exceptions={len(exceptions)}")


if __name__ == "__main__":
    main()
