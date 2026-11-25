"""3ds Max Editable Patch rules on the control-point pool (bpy-free). Slots 0..15 row-major: corners 0, 3, 12, 15,
interiors 5, 6, 9, 10. Auto Interior is ``P11 = P01 + P10 - P00`` (true of 99.98 % of stock patches).

After a move, :func:`carry_handles` drags a moved corner's handles and recomputes interiors unless the patch is manual.
Never rewrite an untouched patch or an interior within :data:`TOL`: stock import / export must stay byte-identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

CORNER_SLOTS = (0, 3, 12, 15)
HANDLE_SLOTS = (1, 2, 4, 7, 8, 11, 13, 14)
INTERIOR_SLOTS = (5, 6, 9, 10)
# corner slot -> its two edge handles (along the row, along the column)
CORNER_HANDLES = {0: (1, 4), 3: (2, 7), 12: (13, 8), 15: (14, 11)}
# each interior slot with the frame it is derived from: (interior, corner, handle along the row, handle along the column)
INTERIOR_FRAME = ((5, 0, 1, 4), (6, 3, 2, 7), (9, 12, 13, 8), (10, 15, 14, 11))
# the rule's precision, relative to the patch's bounding diagonal: the stock data's own (float32 world coordinates)
TOL = 1e-5


def patch_size(verts: np.ndarray, patches: np.ndarray) -> np.ndarray:
    """(Np,) the longer of each patch's two corner diagonals (never zero: a degenerate patch gets 1e-12)."""
    G = verts[patches]
    d1 = np.linalg.norm(G[:, 0] - G[:, 15], axis=1)
    d2 = np.linalg.norm(G[:, 3] - G[:, 12], axis=1)
    return np.maximum(np.maximum(d1, d2), 1e-12)


def auto_interiors(verts, patches) -> np.ndarray:
    """(Np, 4, 3) the Auto Interior positions of every patch, in slot order 5, 6, 9, 10:
    ``P[interior] = P[row handle] + P[column handle] - P[corner]`` for each corner's frame."""
    V = np.asarray(verts, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    out = np.empty((len(P), 4, 3))
    for k, (_i, c, h1, h2) in enumerate(INTERIOR_FRAME):
        out[:, k] = V[P[:, h1]] + V[P[:, h2]] - V[P[:, c]]
    return out


def interior_error(verts, patches) -> np.ndarray:
    """(Np,) how far each patch's interiors are from the rule: the largest of the four distances, relative to the
    patch's bounding diagonal (0 = exactly Auto Interior)."""
    V = np.asarray(verts, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    if not len(P):
        return np.zeros(0)
    cur = V[P[:, list(INTERIOR_SLOTS)]]
    d = np.linalg.norm(cur - auto_interiors(V, P), axis=2).max(axis=1)
    return d / patch_size(V, P)


@dataclass
class Roles:
    """Per pool point: at a corner slot in some patch; at a handle slot in some patch; ``interior``: at an interior slot
    in EVERY patch that uses it (the points the display hides: nothing but the rule ever needs to move them)."""
    corner: np.ndarray
    handle: np.ndarray
    interior: np.ndarray


def roles(patches, n_verts: int) -> Roles:
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = int(n_verts)
    corner = np.zeros(n, dtype=bool)
    handle = np.zeros(n, dtype=bool)
    inter = np.zeros(n, dtype=bool)
    if len(P):
        corner[P[:, list(CORNER_SLOTS)].reshape(-1)] = True
        handle[P[:, list(HANDLE_SLOTS)].reshape(-1)] = True
        inter[P[:, list(INTERIOR_SLOTS)].reshape(-1)] = True
    return Roles(corner, handle, inter & ~corner & ~handle)


def compaction(n_points, used_before, used_after, alias=None):
    """The one pool-compaction rule (Delete Patch and every topology plan): an old point keeps a slot when it is its
    own survivor (``alias``: the point each one merges into, identity when nothing merges) and either a patch still
    uses it or no patch used it before -- a point no patch ever used stays, as in the file. Returns ``(keep, o2n)``:
    ``keep`` (n,) bool, and ``o2n`` mapping an old index to its new one, a merged point to its survivor's new index
    and a dropped point to -1."""
    n = int(n_points)
    used_before = np.asarray(used_before, dtype=bool)
    used_after = np.asarray(used_after, dtype=bool)
    alias = np.arange(n) if alias is None else np.asarray(alias, dtype=np.int64)
    survivor = alias == np.arange(n)
    keep = survivor & (used_after | ~used_before)
    o2n = np.full(n, -1, dtype=np.int64)
    o2n[keep] = np.arange(int(keep.sum()))
    merged = ~survivor
    o2n[merged] = o2n[alias[merged]]
    return keep, o2n


def corner_handle_pairs(patches) -> np.ndarray:
    """(8 Np, 2) every (corner pool index, handle pool index) attachment over every patch (with repeats)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    out = []
    for c, (h1, h2) in CORNER_HANDLES.items():
        out.append(np.stack([P[:, c], P[:, h1]], axis=1))
        out.append(np.stack([P[:, c], P[:, h2]], axis=1))
    return np.concatenate(out, axis=0) if out else np.zeros((0, 2), dtype=np.int64)


def handles_of(patches) -> Dict[int, np.ndarray]:
    """corner pool index -> the pool indices of its tangent handles over every patch that uses it (unique, sorted). A
    corner shared by several patches has two handles in each."""
    acc: Dict[int, set] = {}
    for ci, hi in corner_handle_pairs(patches):
        acc.setdefault(int(ci), set()).add(int(hi))
    return {c: np.array(sorted(s), dtype=np.int64) for c, s in acc.items()}


def handles_of_selected(selected, patches, n_verts: int) -> np.ndarray:
    """(N,) bool: the handles attached to a selected corner (what Max shows the vectors of)."""
    out = np.zeros(int(n_verts), dtype=bool)
    pairs = corner_handle_pairs(patches)
    sel = np.asarray(selected, dtype=bool)
    if len(pairs) and len(sel) == len(out):
        out[pairs[sel[pairs[:, 0]], 1]] = True
    return out


def _framed_hidden(selected, hidden, patches, owners):
    """(interior indices, corner indices) of every hidden pure interior whose frame corner is selected and, with
    ``owners`` given, that shares the corner's owner (the stock skin: 68,192 / 68,192 base-game interiors are owned
    by their frame corner's bone); and the same pair lists for those that do NOT share it."""
    sel = np.asarray(selected, dtype=bool)
    hid = np.asarray(hidden, dtype=bool)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    fi, fc, ui, uc = [], [], [], []
    if len(P) and len(sel) == len(hid):
        pure = roles(P, len(sel)).interior
        own = None if owners is None or len(owners) != len(sel) else np.asarray(owners, dtype=np.int64)
        for i, c, _h1, _h2 in INTERIOR_FRAME:
            ii, ci = P[:, i], P[:, c]
            m = hid[ii] & pure[ii] & sel[ci]
            same = m if own is None else m & (own[ii] == own[ci])
            fi.append(ii[same]); fc.append(ci[same])
            ui.append(ii[m & ~same]); uc.append(ci[m & ~same])
    cat = lambda xs: np.concatenate(xs) if xs else np.zeros(0, dtype=np.int64)  # noqa: E731
    return cat(fi), cat(fc), cat(ui), cat(uc)


def follow_corners(selected, hidden, patches, owners=None) -> np.ndarray:
    """The selection plus every HIDDEN interior point (interior in every patch that uses it) whose frame corner is
    selected -- when ``owners`` are given, only one skinned to the same bone as that corner (every stock interior is)
    -- so the skin edits take an interior with the corner it is derived from and a box selection never leaves 44 % of
    the pool behind unseen; an interior deliberately skinned to another bone is left to a visible selection."""
    sel = np.asarray(selected, dtype=bool).copy()
    fi, _fc, _ui, _uc = _framed_hidden(sel, hidden, patches, owners)
    sel[fi] = True
    return sel


def unfollowed_interiors(selected, hidden, patches, owners) -> np.ndarray:
    """The hidden interiors whose frame corner is selected but that :func:`follow_corners` leaves out (another owner)."""
    _fi, _fc, ui, _uc = _framed_hidden(selected, hidden, patches, owners)
    return np.unique(ui)


# ------------------------------------------------------------------------------------------- vertex types (Max)
# A Max patch vertex is Corner (its vectors are free: every stock handle) or Coplanar (its vectors stay in one plane
# through the vertex: what a fresh box starts with); Lock Handles moves all of a vertex's vectors together. Here the
# handles are pool points, so both are rules on the handles of a corner after one of them was moved by itself.
REL_TOL = 1e-6           # a handle direction this close (relative) to its anchor direction counts as unmoved


def rotation_between(a, b) -> np.ndarray:
    """The 3x3 rotation taking direction ``a`` onto direction ``b`` (the minimal one, about ``a x b``); the identity
    when either is zero or they are parallel, a half turn about a perpendicular axis when they are opposite."""
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < 1e-15 or nb < 1e-15:
        return np.eye(3)
    a = a / na; b = b / nb
    v = np.cross(a, b)
    s, c = np.linalg.norm(v), float(a @ b)
    if s < 1e-12:
        if c > 0:
            return np.eye(3)
        p = np.cross(a, [1.0, 0.0, 0.0])                     # any axis perpendicular to a
        if np.linalg.norm(p) < 1e-6:
            p = np.cross(a, [0.0, 1.0, 0.0])
        p /= np.linalg.norm(p)
        return 2.0 * np.outer(p, p) - np.eye(3)
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K * ((1 - c) / (s * s))


def plane_normal(dirs):
    """The unit normal of the least-squares plane through the origin of the direction vectors ``dirs`` ((k, 3)), or
    None when they do not span a plane (fewer than 2, or all parallel)."""
    D = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
    D = D[np.linalg.norm(D, axis=1) > 1e-15]
    if len(D) < 2:
        return None
    _u, s, vt = np.linalg.svd(D, full_matrices=True)
    if len(s) < 2 or s[1] < 1e-9 * max(s[0], 1e-15):
        return None
    return vt[-1] if len(vt) == 3 else None


def coplanar_error(verts, patches, corner) -> float:
    """How far the handles of ``corner`` are from one plane through it: the largest |n . d| over its unit handle
    directions (0 = coplanar; 0 too for a corner whose handles span no plane)."""
    V = np.asarray(verts, dtype=np.float64)
    H = handles_of(patches).get(int(corner))
    if H is None:
        return 0.0
    d = V[H] - V[int(corner)]
    n = plane_normal(d)
    if n is None:
        return 0.0
    ln = np.linalg.norm(d, axis=1)
    return float(np.max(np.abs(d @ n) / np.maximum(ln, 1e-15)))


def project_coplanar(verts, patches, corners):
    """Set Coplanar: the handles of each corner in ``corners`` projected onto their least-squares plane through the
    corner. Returns ``(verts, handle indices moved)``; a corner whose handles already lie in one plane (or span none)
    is left as it is."""
    V = np.asarray(verts, dtype=np.float64).copy()
    H = handles_of(patches)
    moved = []
    for c in sorted({int(x) for x in corners}):
        hs = H.get(c)
        if hs is None:
            continue
        d = V[hs] - V[c]
        n = plane_normal(d)
        if n is None:
            continue
        off = d @ n
        for h, o in zip(hs, off):
            if abs(o) > 1e-12:
                V[h] = V[h] - o * n
                moved.append(int(h))
    return V, np.array(sorted(set(moved)), dtype=np.int64)


def _rotate_siblings(prev, out, P, moved, coplanar, lock, A, act, rel, rel_act):
    """The Coplanar / Lock Handles step of :func:`carry_handles` (in place on ``out``, ``A``, ``act``, ``rel``,
    ``rel_act``): for every corner that is Coplanar or, with ``lock``, every corner, a handle the user moved by itself
    (the DRIVER: the lowest-indexed one when several) turns the corner's other handles (those not moved by the user):
    Lock rotates them by the rotation that took the driver's anchored direction to its current one; Coplanar rotates
    the least-squares plane of the anchored directions by the least rotation that makes it contain the driver's
    current direction, and the siblings with it (a driver moved within the plane leaves them alone). Directions are
    relative to the corner and anchored at the gesture's start (``rel`` / ``rel_act``), so a driver put back where it
    started puts the siblings back bit-exactly. Returns the rotated handle indices."""
    n = len(out)
    pairs = corner_handle_pairs(P)
    ci, hi = pairs[:, 0], pairs[:, 1]
    self_h = moved[hi] & (ci != hi)
    if not self_h.any():
        return np.zeros(0, dtype=np.int64)
    cop = coplanar
    drivers = {}
    for c, h in zip(ci[self_h].tolist(), hi[self_h].tolist()):
        if lock or cop[c]:
            drivers[c] = min(drivers.get(c, h), h)
    if not drivers:
        return np.zeros(0, dtype=np.int64)
    H = handles_of(P)
    rotated = []
    for c, h in sorted(drivers.items()):
        hs = H[c]
        _anchor_home(prev, c, hs, rel, rel_act)             # the gesture starts: positions anchored
        a0 = rel[h] - rel[c]
        d1 = out[h] - out[c]
        sibs = np.array([x for x in hs if x != h and not moved[x]], dtype=np.int64)
        if not len(sibs):
            continue
        new_s = ~act[sibs]
        A[sibs[new_s]] = prev[sibs[new_s]]                 # a sibling first turned is anchored where it was
        act[sibs] = True
        if np.linalg.norm(d1 - a0) <= REL_TOL * max(np.linalg.norm(a0), 1e-12):
            back = np.array_equal(out[c], rel[c])          # the driver is back: siblings back on their home
            out[sibs] = rel[sibs] if back else rel[sibs] + (out[c] - rel[c])
            rotated += sibs.tolist()
            continue
        if lock:
            R = rotation_between(a0, d1)
        else:
            n0 = plane_normal(rel[hs] - rel[c])
            if n0 is None:
                continue
            l1 = np.linalg.norm(d1)
            if l1 < 1e-15:
                continue
            u = d1 / l1
            n1 = n0 - float(n0 @ u) * u
            if np.linalg.norm(n1) < 1e-9:
                continue
            R = rotation_between(n0, n1 / np.linalg.norm(n1))
        out[sibs] = out[c] + (rel[sibs] - rel[c]) @ R.T
        rotated += sibs.tolist()
    return np.array(sorted(set(rotated)), dtype=np.int64)


def _anchor_home(prev, c, hs, home, home_act) -> None:
    """Anchor the HOME positions of corner ``c`` and its handles ``hs`` that have none yet (``home`` (N, 3) absolute
    positions, ``home_act`` (N,)): where they are before this frame's move. A handle's home direction is
    ``home[h] - home[c]``; a corner's home is set with its first handle gesture and re-anchored on a commit."""
    hs = np.asarray(hs, dtype=np.int64)
    if not home_act[c]:
        home[c] = prev[c]
        home_act[c] = True
    fresh = ~home_act[hs]
    if fresh.any():
        home[hs[fresh]] = prev[hs[fresh]]
        home_act[hs[fresh]] = True


@dataclass
class Carry:
    """What :func:`carry_handles` did. ``verts``: the pool after the rule. ``changed``: (N,) the points the rule
    moved (handles carried or turned, interiors recomputed). ``carried``: the handle indices translated. ``rotated``:
    the handle indices turned by the Coplanar / Lock Handles rule. ``touched``: the patch rows whose corners or handles
    changed this step. ``recomputed``: the patch rows whose interiors were rewritten. ``manual``: the per-patch manual
    flags after this step; ``manual_new``: the rows this step flagged. ``anchor`` / ``anchored`` / ``rel`` /
    ``rel_act``: the anchors after this step (pass them to the next call)."""
    verts: np.ndarray
    changed: np.ndarray
    carried: np.ndarray
    touched: np.ndarray
    recomputed: np.ndarray
    manual: np.ndarray
    manual_new: np.ndarray
    anchor: np.ndarray
    anchored: np.ndarray
    rotated: np.ndarray = None
    rel: np.ndarray = None
    rel_act: np.ndarray = None


def carry_handles(prev, cur, patches, moved_mask=None, manual=None, tol: float = TOL, anchor=None,
                  anchored=None, coplanar=None, lock: bool = False, rel=None, rel_act=None) -> Carry:
    """The Max feel from a before / after diff of the pool (``prev`` -> ``cur``, (N, 3) each): corners that moved drag
    the handles that did not move themselves (a handle shared by two moved corners gets their mean delta); then the
    interiors of every patch with a changed corner or handle are recomputed by the Auto Interior rule, except in
    patches flagged ``manual`` ((Np,) bool, default none) and in patches an interior was just moved to somewhere the
    rule does not explain (those are flagged manual now). Interiors within ``tol`` of the rule (relative to the patch's
    bounding diagonal) are not rewritten. ``moved_mask``: (N,) which points moved (default: ``prev != cur``).

    Anchors (``anchor`` (N, 3), ``anchored`` (N,) bool, from the previous call's result; none = fresh): a point is
    anchored at its position when it first moves or is first carried, and a carried handle is placed at its anchor
    plus its corner's offset from the corner's anchor (not at last frame plus a delta), so a corner dragged back to
    where it started -- a cancelled transform restores exactly that -- puts its handles back on their anchors
    bit-exactly instead of a float32 rounding away; an interior whose whole frame is back on its anchors goes back
    to its own anchor (its pre-edit bytes). A handle moved by itself is re-anchored so the relation holds from there.

    ``coplanar`` ((N,) bool: the corners of the Coplanar vertex type) and ``lock`` (Lock Handles) turn the other
    handles of a corner when one of its handles is moved by itself (:func:`_rotate_siblings`; the turned handles
    count as changed by the rule and their patches' interiors follow); ``rel`` / ``rel_act`` are that rule's
    direction anchors from the previous call."""
    prev = np.asarray(prev, dtype=np.float64)
    cur = np.asarray(cur, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(cur)
    if prev.shape != cur.shape:
        raise ValueError(f"carry_handles: {len(prev)} points before, {n} after (the rule needs the same pool)")
    moved = np.any(prev != cur, axis=1) if moved_mask is None else np.asarray(moved_mask, dtype=bool)
    manual = np.zeros(len(P), dtype=bool) if manual is None else np.asarray(manual, dtype=bool).copy()
    if len(manual) != len(P):
        m = np.zeros(len(P), dtype=bool)
        m[:min(len(m), len(manual))] = manual[:len(m)]
        manual = m
    fresh = anchor is None or anchored is None or np.shape(anchor) != cur.shape or len(anchored) != n
    A = prev.copy() if fresh else np.asarray(anchor, dtype=np.float64).copy()
    act = np.zeros(n, dtype=bool) if fresh else np.asarray(anchored, dtype=bool).copy()
    rfresh = rel is None or rel_act is None or np.shape(rel) != cur.shape or len(rel_act) != n
    rel = np.zeros((n, 3)) if rfresh else np.asarray(rel, dtype=np.float64).copy()
    rel_act = np.zeros(n, dtype=bool) if rfresh else np.asarray(rel_act, dtype=bool).copy()
    out = cur.copy()
    empty_rows = np.zeros(0, dtype=np.int64)
    if not len(P) or not moved.any():
        return Carry(out, np.zeros(n, dtype=bool), empty_rows, empty_rows, empty_rows, manual, empty_rows, A, act,
                     empty_rows, rel, rel_act)

    # a point that starts moving is anchored where it was
    newly = moved & ~act
    A[newly] = prev[newly]
    act[newly] = True
    pairs = corner_handle_pairs(P)
    ci, hi = pairs[:, 0], pairs[:, 1]
    self_h = moved[hi] & (ci != hi)
    cop = np.zeros(n, dtype=bool) if coplanar is None else np.asarray(coplanar, dtype=bool)
    if len(cop) != n:
        c2 = np.zeros(n, dtype=bool); c2[:min(n, len(cop))] = cop[:n]; cop = c2
    # 0. commits: a handle with a home that is NOT moving itself this frame while its corner moves (or while another
    # handle of a Corner-type corner is moved by itself), and that is not at its home direction, holds a COMMITTED
    # edit (a tangent edit, or a turn by the Coplanar / Lock rule): its corner's homes are re-anchored where they
    # are, its carry anchor too, and the interiors it frames keep their current values as theirs, so a corner's
    # out-and-back returns to the edited state bit-exactly. Not the siblings of a corner the Coplanar / Lock rule is
    # driving now. Before the carry: the carry works from the re-anchored positions.
    if rel_act.any():
        driven = np.zeros(n, dtype=bool)
        driven[ci[self_h]] = True
        rule_c = cop | lock
        cand = rel_act[hi] & rel_act[ci] & ~moved[hi] & (ci != hi) & ~(driven[ci] & rule_c[ci]) & (moved[ci] | driven[ci])
        if cand.any():
            h_c, c_c = hi[cand], ci[cand]
            off = np.any(prev[h_c] - prev[c_c] != rel[h_c] - rel[c_c], axis=1)
            commit_c = np.unique(c_c[off])
            if len(commit_c):
                H = handles_of(P)
                mask_h = np.zeros(n, dtype=bool)
                for c0 in commit_c.tolist():
                    hs = H[c0]
                    still = hs[~moved[hs]]                       # the ones not in a gesture of their own now
                    rel[c0] = prev[c0]
                    rel[still] = prev[still]
                    rel_act[still] = True
                    A[still] = prev[still] - (prev[c0] - A[c0])   # the carry relation from here
                    act[still] = True
                    mask_h[still] = True
                for i, _c, h1, h2 in INTERIOR_FRAME:
                    fr = mask_h[P[:, h1]] | mask_h[P[:, h2]]
                    ii = P[fr, i]
                    A[ii] = prev[ii]
                    act[ii] = True
    # 1. corners drag their handles (those that did not move themselves): handle = its anchor + the corner's offset
    # from the corner's anchor; a handle two moved corners share gets the mean offset
    acc = np.zeros((n, 3))
    cnt = np.zeros(n, dtype=np.int64)
    sel = moved[ci] & ~moved[hi] & (ci != hi)
    if sel.any():
        c_sel, h_sel = ci[sel], hi[sel]
        new_h = ~act[h_sel]
        A[h_sel[new_h]] = prev[h_sel[new_h]]                      # a handle first carried is anchored where it was
        act[h_sel] = True
        np.add.at(acc, h_sel, cur[c_sel] - A[c_sel])
        np.add.at(cnt, h_sel, 1)
    carried_mask = cnt > 0
    if carried_mask.any():
        out[carried_mask] = A[carried_mask] + acc[carried_mask] / cnt[carried_mask, None]
    carried = np.nonzero(carried_mask)[0]
    # 1b. Coplanar corners / Lock Handles: a handle moved by itself turns the corner's other handles (their homes
    # are anchored BEFORE the tangent re-anchoring below)
    rotated = _rotate_siblings(prev, out, P, moved, cop, lock, A, act, rel, rel_act)
    rot_mask = np.zeros(n, dtype=bool)
    rot_mask[rotated] = True
    # a handle moved by itself (a tangent edit): its home (position at the gesture's start, with its corner's: the
    # home test below) is anchored, then it is re-anchored so that handle = anchor + corner offset holds from here
    if self_h.any():
        c_s, h_s = ci[self_h], hi[self_h]
        for c0, h0 in zip(c_s.tolist(), h_s.tolist()):
            _anchor_home(prev, c0, [h0], rel, rel_act)
        A[h_s] = cur[h_s] - (cur[c_s] - A[c_s])

    # 2. interiors: recompute for touched patches, unless manual (or just made manual by a direct interior move)
    changed_pt = moved | carried_mask | rot_mask
    boundary = P[:, list(CORNER_SLOTS) + list(HANDLE_SLOTS)]
    touched_mask = changed_pt[boundary].any(axis=1)
    int_idx = P[:, list(INTERIOR_SLOTS)]                                   # (Np, 4)
    auto = auto_interiors(out, P)                                          # (Np, 4, 3)
    # a frame back on its anchors puts its interior back on ITS anchor (bit-exact) rather than a recomputation: the
    # corner and a carried handle at their anchored positions, a handle ever moved by itself (or turned by the
    # Coplanar / Lock rule) at its home DIRECTION from the corner (its carry anchor follows the corner instead)
    for k, (i, c, h1, h2) in enumerate(INTERIOR_FRAME):
        ii, cc = P[:, i], P[:, c]
        c_home = np.all(out[cc] == A[cc], axis=1)                          # an unmoved point's anchor is itself

        def h_home(hh):
            by_pos = ~rel_act[hh] & np.all(out[hh] == A[hh], axis=1)
            by_dir = rel_act[hh] & c_home & np.all(out[hh] - out[cc] == rel[hh] - rel[cc], axis=1)
            return by_pos | by_dir
        home = c_home & h_home(P[:, h1]) & h_home(P[:, h2])
        if home.any():
            auto[home, k] = A[ii[home]]
    size = patch_size(out, P)
    dev = np.linalg.norm(out[int_idx] - auto, axis=2) / size[:, None]     # (Np, 4)
    moved_int = moved[int_idx]                                             # (Np, 4)
    manual_new_mask = (moved_int & (dev > tol)).any(axis=1) & ~manual
    manual |= manual_new_mask
    recompute = (touched_mask | moved_int.any(axis=1)) & ~manual
    write = recompute[:, None] & (dev > tol)
    rows, cols = np.nonzero(write)
    changed = changed_pt & ~moved                                          # what the RULE moved: carried handles ...
    if len(rows):
        wi = int_idx[rows, cols]
        new_i = ~act[wi]                                                   # an interior first rewritten: anchored
        A[wi[new_i]] = out[wi[new_i]]
        act[wi] = True
        out[wi] = auto[rows, cols]
        changed[wi] = True                                                 # ... and rewritten interiors
    return Carry(out, changed, carried, np.nonzero(touched_mask)[0], np.unique(rows), manual,
                 np.nonzero(manual_new_mask)[0], A, act, rotated, rel, rel_act)


def reset_interiors(verts, patches, rows=None, tol: float = TOL):
    """The pool with the interiors of ``rows`` (default every patch) put back on the rule; returns
    ``(verts, rows rewritten)``. Interiors already within ``tol`` are left as they are (bytes unchanged)."""
    V = np.asarray(verts, dtype=np.float64).copy()
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    rows = np.arange(len(P)) if rows is None else np.asarray(sorted(set(int(r) for r in rows)), dtype=np.int64)
    if not len(rows):
        return V, rows
    sub = P[rows]
    int_idx = sub[:, list(INTERIOR_SLOTS)]
    auto = auto_interiors(V, sub)
    dev = np.linalg.norm(V[int_idx] - auto, axis=2) / patch_size(V, sub)[:, None]
    r, c = np.nonzero(dev > tol)
    V[int_idx[r, c]] = auto[r, c]
    return V, rows[np.unique(r)]
