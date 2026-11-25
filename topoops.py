"""Topology tools: Weld, Attach, Mirror to Whole, Subdivide, Add Quad, Fill Hole, Extrude Region and the drag macros.

Each is a pure bpy-free ``*_plan`` written by the one applier ``apply_plan`` (one generation, one undo step, every
per-patch table and ring remapped). Seams are watertight only by shared pool indices: closing one must merge all 4
side points. A plan refuses anything that would not load (collapsed patch, edge used three times, split ring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ICParse import validate as ICV

try:
    from . import patchrules as PR
except ImportError:                      # imported top-level (pure tests put ic_blender/ on sys.path)
    import patchrules as PR  # type: ignore[no-redef]

# ICParse validate.MAX_PATCHES: one DirectX 8 vertex set holds the whole tessellated patch mesh
# (12,288 display vertices) and a patch costs at least 16 of them, so 767 is the ceiling even with
# every seam welded -- the practical budget is far lower (validate.display_vertices measures it).
MAX_PATCHES = ICV.MAX_PATCHES
CYCLE_SIDES = ((0, 4, 8, 12), (12, 13, 14, 15), (15, 11, 7, 3), (3, 2, 1, 0))   # ICParse _LOADER_SIDES: a patch's
                                                                              # cyclic traversal (col 0, row 3, col 3, row 0)
# a side as (row, col) grid steps: side k runs from CYCLE_SIDES[k][0] to CYCLE_SIDES[k][3]
_SIDE_NAMES = ("column 0", "row 3", "column 3", "row 0")


def side_key(cps, k) -> tuple:
    """ICParse's loader key of side ``k`` (``CYCLE_SIDES``) of a patch row: its 4 points, reversed when the last index
    is below the first (two sides pair only when all four coincide in that order)."""
    t = tuple(int(cps[i]) for i in CYCLE_SIDES[k])
    return t if t[3] >= t[0] else t[::-1]


def side_table(patches) -> Dict[tuple, List[Tuple[int, int]]]:
    """Every side key -> ``[(row, k)]`` of the patches using it."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    out: Dict[tuple, List[Tuple[int, int]]] = {}
    for r, cps in enumerate(P.tolist()):
        for k in range(4):
            out.setdefault(side_key(cps, k), []).append((r, k))
    return out


def open_sides(patches) -> List[Tuple[int, int, tuple]]:
    """``[(row, k, key)]`` of the sides a single patch uses."""
    return [(u[0][0], u[0][1], key) for key, u in side_table(patches).items() if len(u) == 1]


def traversal(cps, k) -> Tuple[int, int, int, int]:
    """Side ``k`` of a patch row in the patch's own cyclic direction: ``(a, h1, h2, b)``."""
    return tuple(int(cps[i]) for i in CYCLE_SIDES[k])


# ------------------------------------------------------------------------------------------- the plan
@dataclass
class TopoPlan:
    """What a tool does to the pool and the tables (virtual indices: an old point's index, or ``n_old + j`` for new
    point ``j``). ``finalize`` computes the compaction (``patchrules.compaction``: ``keep``, ``o2n``, ``patches_final``)."""
    n_old: int
    alias: np.ndarray                                   # (n_old,) the survivor of each old point (merges)
    new_points: np.ndarray                              # (k, 3)
    new_from: np.ndarray                                # (k,) old point each new one derives from (owner, type), -1 = none
    patches: np.ndarray                                 # (Np, 16) virtual indices (aliased)
    uv: np.ndarray                                      # (Np, 8)
    mat: np.ndarray                                     # (Np,)
    ptype: np.ndarray                                   # (Np,)
    limb: np.ndarray                                    # (Np,)
    manual: np.ndarray                                  # (Np,) bool
    row_src: np.ndarray                                 # (Np,) the old row each row continues (-1 = made)
    moved: Dict[int, np.ndarray] = field(default_factory=dict)    # old survivor index -> new position
    new_owner: object = None                            # (k,) explicit owners (Mirror), else from new_from
    new_coplanar: object = None                         # (k,) explicit vertex types, else from new_from
    refusal: object = None
    warnings: List[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)
    select_virtual: object = None                       # points to select afterwards (virtual), else the new rows
    # finalize():
    keep: object = None
    o2n: object = None
    n_kept: int = 0
    patches_final: object = None

    @property
    def n_new(self) -> int:
        return int(len(self.new_points))

    def virtual_to_final(self, v):
        v = np.asarray(v, dtype=np.int64)
        out = np.where(v >= self.n_old, self.n_kept + (v - self.n_old), 0)
        old = v < self.n_old
        out[old] = self.o2n[v[old]]
        return out


def finalize(plan: TopoPlan, patches_old) -> TopoPlan:
    """The compaction (``patchrules.compaction``, the rule Delete Patch uses): an old point stays when it is its own
    survivor and a patch still uses it (or no patch used it before -- as in the file); a merged-away point maps onto
    its survivor; the rest are dropped."""
    P0 = np.asarray(patches_old, dtype=np.int64).reshape(-1, 16)
    n = plan.n_old
    used_before = np.zeros(n, dtype=bool)
    if len(P0):
        used_before[P0.reshape(-1)] = True
    used_after = np.zeros(n, dtype=bool)
    Pv = np.asarray(plan.patches, dtype=np.int64).reshape(-1, 16)
    if len(Pv):
        old = Pv[Pv < n]
        used_after[old] = True
    keep, o2n = PR.compaction(n, used_before, used_after, plan.alias)     # the one compaction rule (patchrules)
    plan.keep, plan.o2n, plan.n_kept = keep, o2n, int(keep.sum())
    pf = plan.virtual_to_final(Pv.reshape(-1)).reshape(-1, 16) if len(Pv) else np.zeros((0, 16), dtype=np.int64)
    if len(pf) and (pf < 0).any():
        raise ValueError("a new patch uses a dropped point (plan bug)")
    plan.patches_final = pf
    merged = plan.alias != np.arange(n)                  # dropped = neither kept nor merged into a survivor
    plan.info.setdefault("dropped", int((~keep & ~merged).sum()))
    return plan


def _tables(uv, mat, ptype, limb, manual, n):
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 8) if uv is not None and len(uv) == n else np.zeros((n, 8))
    mat = np.asarray(mat, dtype=np.int64) if mat is not None and len(mat) == n else np.zeros(n, dtype=np.int64)
    ptype = np.asarray(ptype, dtype=np.int64) if ptype is not None and len(ptype) == n else np.ones(n, dtype=np.int64)
    limb = np.asarray(limb, dtype=np.int64) if limb is not None and len(limb) == n else np.full(n, -1, dtype=np.int64)
    manual = np.asarray(manual, dtype=bool) if manual is not None and len(manual) == n else np.zeros(n, dtype=bool)
    return uv, mat, ptype, limb, manual


def _ring_edges(rings):
    """``(edges {frozenset(a, b): name}, points {v: name})`` of ``[(name, ints, enc)]`` connection rows."""
    edges, points = {}, {}
    for name, ring, enc in rings or ():
        ring = [int(v) for v in ring]
        if enc and len(ring) >= 2:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                edges.setdefault(frozenset((a, b)), name)
        else:
            for v in ring:
                points.setdefault(v, name)
    return edges, points


def _plural(n, word) -> str:
    """``3 patches`` / ``1 patch`` (English plural for the words these tools use: patch, hole, corner, point, ring)."""
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word}{'es' if word.endswith(('s', 'x', 'ch', 'sh')) else 's'}"


# ------------------------------------------------------------------------------------------- Weld
def _clusters(V, idx, thr):
    """Union-find of ``idx`` by pairwise distance <= thr: list of clusters (lists of indices, each sorted)."""
    idx = [int(i) for i in idx]
    parent = {i: i for i in idx}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    X = V[idx]
    order = np.argsort(X[:, 0])
    xs = X[order, 0]
    for a_i in range(len(idx)):
        a = order[a_i]
        b_i = a_i + 1
        while b_i < len(idx) and xs[b_i] - xs[a_i] <= thr:
            b = order[b_i]
            if np.linalg.norm(X[a] - X[b]) <= thr:
                ra, rb = find(idx[a]), find(idx[b])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            b_i += 1
    groups: Dict[int, list] = {}
    for i in idx:
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def weld_plan(V, patches, selected, threshold, target=None, rings=(), uv=None, mat=None, ptype=None, limb=None,
              manual=None) -> TopoPlan:
    """Weld the selected corners (``selected``: pool indices; non-corners are ignored and counted): clusters within
    ``threshold`` (Weld Selected) or all into ``target`` (Weld Target). See the module rules."""
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    roles = PR.roles(P, n)
    sel = [int(i) for i in selected if 0 <= int(i) < n]
    corners = [i for i in sel if roles.corner[i]]
    ignored = len(sel) - len(corners)
    uv, mat, ptype, limb, manual = _tables(uv, mat, ptype, limb, manual, len(P))
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    if target is not None:
        target = int(target)
        if not roles.corner[target]:
            plan.refusal = f"the active point {target} is not a corner: Weld Target needs a corner as the active vertex"
            return plan
        others = [i for i in corners if i != target]
        if not others:
            plan.refusal = "Weld Target needs other corners selected with the active one"
            return plan
        groups = [[target] + others]
        positions = {target: V[target].copy()}
    else:
        if len(corners) < 2:
            plan.refusal = (f"select at least two corners to weld ({_plural(len(corners), 'corner')} selected"
                            + (f", {ignored} handle / interior points ignored" if ignored else "") + ")")
            return plan
        groups = _clusters(V, corners, float(threshold))
        if not groups:
            plan.refusal = (f"no two of the {len(corners)} selected corners are within {threshold:.4g} m of each other: "
                            "raise the threshold, or use Weld Target (all into the active corner)")
            return plan
        positions = {g[0]: V[g].mean(axis=0) for g in groups}
    alias = np.arange(n)
    for g in groups:
        surv = g[0]
        for i in g[1:]:
            alias[i] = surv
    # patch collapse: a patch with two corners on one point that did not have them before
    Pa = alias[P]
    before = np.array([len(set(row)) for row in P[:, list(PR.CORNER_SLOTS)].tolist()])
    after = np.array([len(set(row)) for row in Pa[:, list(PR.CORNER_SLOTS)].tolist()])
    bad = np.nonzero(after < before)[0]
    if len(bad):
        plan.refusal = (f"patch{'es' if len(bad) > 1 else ''} {[int(b) for b in bad[:8]]} would collapse (two corners on "
                        "one point): weld only corners no patch shares, or delete the patch first")
        return plan
    # rings: a corner loop with a repeated corner after the weld
    for name, ring, enc in rings or ():
        rr = [int(alias[int(v)]) if 0 <= int(v) < n else int(v) for v in ring]
        if len(set(rr)) < len(set(int(v) for v in ring)):
            plan.refusal = (f"connection ring '{name}' would have two of its corners welded together: the socket needs "
                            "its full corner loop. Weld corners off the ring, or delete the connection first")
            return plan
    # positions: the merged corners, their handles carried by the corner's delta
    moved: Dict[int, np.ndarray] = {}
    H = PR.handles_of(P)
    for g in groups:
        pos = positions[g[0]]
        for i in g:
            d = pos - V[i]
            moved[i] = pos.copy()
            for h in H.get(i, ()):
                h = int(h)
                if h not in moved:
                    moved[h] = V[h] + d
    # open sides that come together: the same corner pair after the weld -> merge their handle pairs
    table = side_table(P)
    users_after: Dict[frozenset, list] = {}
    for key, users in table.items():
        a, b = int(alias[key[0]]), int(alias[key[3]])
        users_after.setdefault(frozenset((a, b)), []).append((key, users))
    merged_handles = 0
    for pair, entries in users_after.items():
        total = sum(len(u) for _k, u in entries)
        if total > 2:
            keys = [k for k, _u in entries]
            plan.refusal = (f"welding would give the side between corners {sorted(pair)} {total} patches (sides "
                            f"{keys[:3]}): the loader takes at most two per edge. Weld a different pair of corners")
            return plan
        if len(entries) == 2:
            (k1, _u1), (k2, _u2) = entries
            same_dir = alias[k1[0]] == alias[k2[0]]
            pairs = ((k1[1], k2[1]), (k1[2], k2[2])) if same_dir else ((k1[1], k2[2]), (k1[2], k2[1]))
            for h1, h2 in pairs:
                h1, h2 = int(h1), int(h2)
                if h1 == h2:
                    continue
                p1 = moved.get(h1, V[h1]); p2 = moved.get(h2, V[h2])
                mean = (p1 + p2) / 2.0
                surv, gone = min(h1, h2), max(h1, h2)
                alias[gone] = surv
                moved[surv] = mean
                moved.pop(gone, None)
                merged_handles += 1
    Pa = alias[P]
    plan.alias = alias
    plan.patches = Pa
    # interiors of the touched patches follow the rule (manual ones stay)
    Vn = V.copy()
    for i, p in moved.items():
        Vn[i] = p
    stirred = np.array(list(moved.keys()) + [g_i for g in groups for g_i in g], dtype=np.int64)
    touched = np.nonzero(np.isin(Pa, stirred).any(axis=1))[0]
    Vr, rewritten = PR.reset_interiors(Vn, Pa, touched[~manual[touched]])
    for r in rewritten:
        for s in PR.INTERIOR_SLOTS:
            i = int(Pa[r, s])
            moved[i] = Vr[i]
    plan.moved = moved
    open_before = sum(1 for u in table.values() if len(u) == 1)
    open_after = sum(1 for u in side_table(Pa).values() if len(u) == 1)
    plan.info.update(groups=len(groups), corners_merged=sum(len(g) - 1 for g in groups), handles_merged=merged_handles,
                     open_before=open_before, open_after=open_after, ignored=ignored, touched=len(touched))
    plan.select_virtual = [g[0] for g in groups]
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Attach
def attach_plan(V, patches, tables, V2, patches2, tables2, limb_index, owner, mat_map, coplanar2=None) -> TopoPlan:
    """Append creature 2 (``V2``, ``patches2``, its tables) to creature 1. ``tables`` = (uv, mat, ptype, limb, manual);
    ``mat_map``: creature 2's material slot -> creature 1's (-1 = none: slot 0, warned)."""
    V = np.asarray(V, dtype=np.float64); V2 = np.asarray(V2, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    P2 = np.asarray(patches2, dtype=np.int64).reshape(-1, 16)
    n, n2 = len(V), len(V2)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    uv2, mat2, ptype2, limb2, manual2 = _tables(*tables2, len(P2))
    mm = np.asarray(mat_map, dtype=np.int64)
    m2 = np.array([int(mm[int(s)]) if 0 <= int(s) < len(mm) else -1 for s in mat2], dtype=np.int64)
    unmatched = int((m2 < 0).sum())
    m2[m2 < 0] = 0
    plan = TopoPlan(n, np.arange(n), V2.copy(), np.full(n2, -1, dtype=np.int64),
                    np.vstack([P, P2 + n]) if len(P2) else P.copy(),
                    np.vstack([uv, uv2]), np.concatenate([mat, m2]), np.concatenate([ptype, ptype2]),
                    np.concatenate([limb, np.full(len(P2), int(limb_index), dtype=np.int64)]),
                    np.concatenate([manual, manual2]), np.concatenate([np.arange(len(P)), np.full(len(P2), -1)]))
    plan.new_owner = np.full(n2, int(owner), dtype=np.int64)
    if coplanar2 is not None and len(coplanar2) == n2:
        plan.new_coplanar = np.asarray(coplanar2, dtype=np.int64)
    if len(plan.patches) > MAX_PATCHES:
        plan.refusal = (f"attaching would make {len(plan.patches)} patches; the game's patch mesh holds at most "
                        f"{MAX_PATCHES} (Validate shows what this creature costs)")
        return plan
    if unmatched:
        plan.warnings.append(f"{_plural(unmatched, 'attached patch')} used a material slot with no slot of that name "
                             "here: put on slot 0")
    plan.info.update(points=n2, patches=len(P2), unmatched=unmatched)
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Mirror to Whole
_SUFFIXES = (("_l", "_r"), ("_L", "_R"), (".l", ".r"), (".L", ".R"), ("_left", "_right"), ("_Left", "_Right"),
             ("left", "right"), ("Left", "Right"), ("LEFT", "RIGHT"))


def mirror_name(name: str) -> Optional[str]:
    """The mirror bone's name by the stock convention (``ear_l`` <-> ``ear_r``, ``feather2_L`` <-> ``feather2_R``,
    ``left`` / ``right`` words), or None when the name has no side."""
    s = str(name)
    for a, b in _SUFFIXES:
        for x, y in ((a, b), (b, a)):
            if s.endswith(x) and len(s) > len(x):
                return s[:-len(x)] + y
    for a, b in (("l_", "r_"), ("L_", "R_"), ("left_", "right_"), ("Left_", "Right_")):
        for x, y in ((a, b), (b, a)):
            if s.startswith(x) and len(s) > len(x):
                return y + s[len(x):]
    return None


def mirror_owner_map(names) -> np.ndarray:
    """owner index -> the index of its mirror bone by name (itself when there is none)."""
    names = [str(x) for x in names]
    index = {nm: i for i, nm in enumerate(names)}
    lower = {nm.lower(): i for i, nm in enumerate(names)}
    out = np.arange(len(names))
    for i, nm in enumerate(names):
        m = mirror_name(nm)
        if m is None:
            continue
        j = index.get(m, lower.get(m.lower()))
        if j is not None:
            out[i] = j
    return out


def mirror_limb_map(limb_ids) -> np.ndarray:
    """limb index -> the limb of the mirror class (Left bit 1 <-> Right bit 2, class without NoSwap), else itself."""
    if limb_ids is None:
        return np.zeros(0, dtype=np.int64)
    ids = [int(x) & 0xFF7FFFFF for x in limb_ids]
    out = np.arange(len(ids))
    for i, c in enumerate(ids):
        if c & 1:
            want = (c & ~3) | 2
        elif c & 2:
            want = (c & ~3) | 1
        else:
            continue
        j = next((k for k, d in enumerate(ids) if d == want), None)
        if j is not None:
            out[i] = j
    return out


def mirror_plan(V, patches, rows, threshold, tables, owners=None, owner_map=None, limb_map=None, snap=True,
                axis=0) -> TopoPlan:
    """Mirror patch ``rows`` across the plane ``axis = 0`` (see the module rules)."""
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    rows = np.array(sorted({int(r) for r in rows if 0 <= int(r) < len(P)}), dtype=np.int64)
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    thr = float(threshold)
    if not len(rows):
        plan.refusal = "select the patches to mirror (Face select), or select nothing to mirror every patch"
        return plan
    used = np.unique(P[rows].reshape(-1))
    x = V[used, axis]
    pos, neg = int((x > thr).sum()), int((x < -thr).sum())
    if pos and neg:
        plan.refusal = (f"the patches to mirror straddle the plane: {pos} points beyond +{thr:.4g} and {neg} beyond "
                        f"-{thr:.4g} on the {'XYZ'[axis]} axis. Select one half, or raise the threshold")
        return plan
    if not pos and not neg:
        plan.refusal = "every point of the selection lies on the plane: nothing to mirror"
        return plan
    sign = 1.0 if pos else -1.0
    others = np.setdiff1d(np.arange(len(P)), rows)
    if len(others):
        ox = V[np.unique(P[others].reshape(-1)), axis]
        far = int((ox * sign < -thr).sum())
        if far:
            plan.refusal = (f"{_plural(far, 'point')} of the unselected patches already lie on the mirror side: the "
                            "mirrored patches would sit on top of them. Delete Patch that side first, or select "
                            "only the half to mirror")
            return plan
    on_plane = np.abs(V[:, axis]) <= thr
    new_index: Dict[int, int] = {}
    new_pts, new_from = [], []
    moved: Dict[int, np.ndarray] = {}

    def image(i):
        i = int(i)
        if on_plane[i]:
            if snap and V[i, axis] != 0.0:
                p = V[i].copy(); p[axis] = 0.0
                moved[i] = p
            return i
        j = new_index.get(i)
        if j is None:
            j = n + len(new_pts)
            new_index[i] = j
            p = V[i].copy(); p[axis] = -p[axis]
            new_pts.append(p); new_from.append(i)
        return j
    new_rows, src_rows, skipped = [], [], 0
    for r in rows.tolist():
        cps = P[r]
        if on_plane[cps].all():
            skipped += 1
            continue
        # the transposed grid: the mirror flips the parametric normal once, the transpose flips it back
        g = cps.reshape(4, 4).T.reshape(-1)
        new_rows.append([image(v) for v in g.tolist()])
        src_rows.append(r)
    if not new_rows:
        plan.refusal = f"every selected patch lies on the plane ({skipped}): nothing to mirror"
        return plan
    k = len(new_rows)
    if len(P) + k > MAX_PATCHES:
        plan.refusal = (f"mirroring would make {len(P) + k} patches; the game holds at most {MAX_PATCHES} "
                        f"({ICV.MAX_DISPLAY_VERTICES} display vertices / 16 per patch at best)")
        return plan
    src = np.array(src_rows, dtype=np.int64)
    uv_m = uv[src][:, [0, 1, 4, 5, 2, 3, 6, 7]]         # corner UVs in slot order 0, 3, 12, 15 -> 0, 12, 3, 15
    lm = np.asarray(limb_map, dtype=np.int64) if limb_map is not None else None
    limb_m = np.array([int(lm[int(l)]) if lm is not None and 0 <= int(l) < len(lm) else int(l) for l in limb[src]],
                      dtype=np.int64)
    plan.new_points = np.array(new_pts, dtype=np.float64).reshape(-1, 3)
    plan.new_from = np.array(new_from, dtype=np.int64)
    if owners is not None and len(owners) == n:
        own = np.asarray(owners, dtype=np.int64)
        om = np.asarray(owner_map, dtype=np.int64) if owner_map is not None else None
        plan.new_owner = np.array([int(om[own[i]]) if om is not None and 0 <= own[i] < len(om) else int(own[i])
                                   for i in new_from], dtype=np.int64)
    plan.patches = np.vstack([P, np.array(new_rows, dtype=np.int64)])
    plan.uv = np.vstack([uv, uv_m]); plan.mat = np.concatenate([mat, mat[src]])
    plan.ptype = np.concatenate([ptype, ptype[src]]); plan.limb = np.concatenate([limb, limb_m])
    plan.manual = np.concatenate([manual, manual[src]])
    plan.row_src = np.concatenate([np.arange(len(P)), src])
    plan.moved = moved
    shared = int(sum(1 for i in used if on_plane[i]))
    plan.info.update(patches=k, points=len(new_pts), shared=shared, snapped=len(moved), skipped=skipped,
                     side="+" if sign > 0 else "-",
                     owners_mirrored=int(sum(1 for i, o in zip(new_from, plan.new_owner if plan.new_owner is not None
                                                                else [])
                                             if owners is not None and int(o) != int(owners[i]))),
                     limbs_mirrored=int((limb_m != limb[src]).sum()))
    if skipped:
        plan.warnings.append(f"{_plural(skipped, 'patch')} lying entirely on the plane not mirrored (it would "
                             "duplicate itself)")
    plan.warnings.append("connection rings are not mirrored: a mirrored limb needs its socket made in Boner > Connections")
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Subdivide
def _split_curve(Q):
    """De Casteljau at 1/2 of a cubic (4, 3) -> the 7 points of the two halves ``[P0, L1, L2, M, R1, R2, P3]``."""
    q01 = (Q[0] + Q[1]) / 2.0; q12 = (Q[1] + Q[2]) / 2.0; q23 = (Q[2] + Q[3]) / 2.0
    l2 = (q01 + q12) / 2.0; r1 = (q12 + q23) / 2.0
    m = (l2 + r1) / 2.0
    return np.stack([Q[0], q01, l2, m, r1, q23, Q[3]])


def propagate_splits(patches, rows, rings=()) -> Tuple[Dict[int, set], Optional[str], list]:
    """The split flags every patch ends with (``{row: {'c', 'r'}}``: 'c' cuts at u = 1/2 along the columns and
    subdivides the row sides 0 and 3, 'r' cuts along the rows and subdivides the column sides), after propagating each
    subdivided side to the neighbour across it. Returns ``(flags, refusal, propagated rows)``."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    table = side_table(P)
    edges, points = _ring_edges(rings)
    flags: Dict[int, set] = {}
    queue = []
    for r in sorted({int(x) for x in rows}):
        flags[r] = {"c", "r"}
        queue.append((r, "c")); queue.append((r, "r"))
    origin = {int(r) for r in rows}
    propagated = set()
    # a split direction subdivides two sides: 'c' -> the row sides (k = 1: row 3, k = 3: row 0); 'r' -> the column
    # sides (k = 0, k = 2)
    sides_of = {"c": (1, 3), "r": (0, 2)}
    while queue:
        r, d = queue.pop()
        cps = P[r]
        for k in sides_of[d]:
            a, b = int(cps[CYCLE_SIDES[k][0]]), int(cps[CYCLE_SIDES[k][3]])
            nm = edges.get(frozenset((a, b)))
            if nm is None:
                nm = points.get(a) or points.get(b)
            if nm is not None:
                how = "selected" if r in origin else "reached by the propagation"
                return flags, (f"patch {r} ({how}) would be split across side {a}-{b} of connection ring '{nm}', which "
                               "cannot be subdivided: choose patches whose strips do not cross the ring"), sorted(propagated)
            key = side_key(cps, k)
            for q, kq in table.get(key, ()):
                if q == r:
                    continue
                dq = "c" if kq in (1, 3) else "r"
                if dq not in flags.get(q, set()):
                    flags.setdefault(q, set()).add(dq)
                    if q not in origin:
                        propagated.add(q)
                    queue.append((q, dq))
    return flags, None, sorted(propagated)


def subdivide_plan(V, patches, rows, tables, rings=(), max_patches=MAX_PATCHES, exact=True) -> TopoPlan:
    """Split ``rows`` into 4 (propagating), see the module rules. ``exact``: keep the de Casteljau interiors (surface
    unchanged; sub-patches off the Auto Interior rule are flagged manual); False: interiors on the rule."""
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    rows = sorted({int(r) for r in rows if 0 <= int(r) < len(P)})
    if not rows:
        plan.refusal = "select the patches to subdivide (Face select, or all 4 corners of each)"
        return plan
    flags, refusal, propagated = propagate_splits(P, rows, rings)
    if refusal:
        plan.refusal = refusal
        return plan
    total = len(P) + sum(len(f) * 2 - 1 if len(f) == 2 else 1 for f in flags.values())
    if total > max_patches:
        plan.refusal = (f"subdividing {len(rows)} patch{'es' if len(rows) != 1 else ''} propagates to {len(propagated)} "
                        f"more, making {total} patches (max {max_patches}): select fewer patches or shorter strips")
        return plan
    new_pts, new_from = [], []
    side_pts: Dict[tuple, np.ndarray] = {}
    keyed: Dict[tuple, int] = {}

    def side_points(key):
        pts = side_pts.get(key)
        if pts is None:
            pts = _split_curve(V[list(key)])
            side_pts[key] = pts
        return pts

    def make(tag, pos, frm):
        j = keyed.get(tag)
        if j is None:
            j = n + len(new_pts)
            keyed[tag] = j
            new_pts.append(np.asarray(pos, dtype=np.float64)); new_from.append(int(frm))
        return j
    out_rows, out_src, out_uv, out_manual = [], [], [], []
    manual_new = 0
    for r in range(len(P)):
        f = flags.get(r)
        if not f:
            out_rows.append(P[r].tolist()); out_src.append(r); out_uv.append(uv[r]); out_manual.append(bool(manual[r]))
            continue
        G = V[P[r]].reshape(4, 4, 3)
        sc, sr = "c" in f, "r" in f
        # the 7 x 7 (or 4 x 7 / 7 x 4) grid of positions
        if sc:
            G = np.stack([_split_curve(G[i]) for i in range(4)])             # (4, 7, 3)
        if sr:
            G = np.stack([_split_curve(G[:, j]) for j in range(G.shape[1])], axis=1)   # (7, w, 3)
        nr, nc = G.shape[0], G.shape[1]
        cps = P[r]
        # the old index at an unsplit grid line; the side keys of the row / column sides
        row_keys = {0: side_key(cps, 3), nr - 1: side_key(cps, 1)}
        col_keys = {0: side_key(cps, 0), nc - 1: side_key(cps, 2)}
        # left -> right along a row (column 0 -> column 3), and top -> bottom along a column (row 0 -> row 3)
        row_dir = {0: traversal(cps, 3)[::-1], nr - 1: traversal(cps, 1)}
        col_dir = {0: traversal(cps, 0), nc - 1: traversal(cps, 2)[::-1]}
        idx = np.zeros((nr, nc), dtype=np.int64)
        for i in range(nr):
            for j in range(nc):
                on_row = i in row_keys
                on_col = j in col_keys
                oi = i if not sr else (0 if i == 0 else 3 if i == nr - 1 else None)
                oj = j if not sc else (0 if j == 0 else 3 if j == nc - 1 else None)
                if oi is not None and oj is not None:
                    idx[i, j] = int(cps[oi * 4 + oj]); continue
                if on_row and sc:                     # along a subdivided row side: shared by the neighbour
                    key = row_keys[i]
                    t = j if row_dir[i][0] == key[0] else 6 - j
                    idx[i, j] = make(("s", key, t), side_points(key)[t], row_dir[i][0]); continue
                if on_col and sr:
                    key = col_keys[j]
                    t = i if col_dir[j][0] == key[0] else 6 - i
                    idx[i, j] = make(("s", key, t), side_points(key)[t], col_dir[j][0]); continue
                # a point inside the patch: the corner it is nearest (owner, vertex type)
                ci = 0 if i * 2 < nr - 1 else 3
                cj = 0 if j * 2 < nc - 1 else 3
                idx[i, j] = make(("p", r, i, j), G[i, j], int(cps[ci * 4 + cj]))
        # sub-patches: 4 x 4 windows at steps of 3
        u = uv[r].reshape(4, 2)                           # slots 0, 3, 12, 15 = (0,0), (0,1), (1,0), (1,1) of (row, col)
        for i0 in range(0, nr - 1, 3):
            for j0 in range(0, nc - 1, 3):
                sub = idx[i0:i0 + 4, j0:j0 + 4].reshape(-1).tolist()
                out_rows.append(sub); out_src.append(r)
                cu = []
                for di, dj in ((0, 0), (0, 3), (3, 0), (3, 3)):
                    s = (i0 + di) / (nr - 1); t = (j0 + dj) / (nc - 1)
                    cu.append((1 - s) * (1 - t) * u[0] + (1 - s) * t * u[1] + s * (1 - t) * u[2] + s * t * u[3])
                out_uv.append(np.concatenate(cu))
                out_manual.append(bool(manual[r]))
    Pn = np.array(out_rows, dtype=np.int64)
    src = np.array(out_src, dtype=np.int64)
    plan.new_points = np.array(new_pts, dtype=np.float64).reshape(-1, 3)
    plan.new_from = np.array(new_from, dtype=np.int64)
    plan.patches = Pn
    plan.uv = np.array(out_uv, dtype=np.float64).reshape(-1, 8)
    plan.mat, plan.ptype, plan.limb = mat[src], ptype[src], limb[src]
    man = np.array(out_manual, dtype=bool)
    # the rule check on the sub-patches (virtual pool)
    Vv = np.vstack([V, plan.new_points]) if len(new_pts) else V
    made = src != np.arange(len(Pn)) if len(Pn) == len(P) else np.ones(len(Pn), dtype=bool)
    split_rows = np.array([i for i, s in enumerate(src.tolist()) if s in flags], dtype=np.int64)
    if exact:
        err = PR.interior_error(Vv, Pn[split_rows]) if len(split_rows) else np.zeros(0)
        off = split_rows[err > PR.TOL]
        manual_new = int((~man[off]).sum())
        man[off] = True
    else:
        Vr, _rw = PR.reset_interiors(Vv, Pn, split_rows[~man[split_rows]])
        for j in range(len(new_pts)):
            plan.new_points[j] = Vr[n + j]
    plan.manual = man
    plan.row_src = src
    plan.info.update(selected=len(rows), propagated=len(propagated), split=len(flags), patches=len(Pn),
                     patches_before=len(P), points=len(new_pts), manual=manual_new)
    plan.select_virtual = None
    plan.info["new_rows"] = [i for i, s in enumerate(src.tolist()) if s in flags]
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Add Quad (extrude sides)
def extrude_sides_plan(V, patches, sides, tables) -> TopoPlan:
    """New patches on the open ``sides`` (``[(row, k)]``), sharing the new column at a corner two of them meet at."""
    try:
        from . import core
    except ImportError:                  # imported top-level (pure tests)
        import core  # type: ignore[no-redef]
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    sides = [(int(r), int(k)) for r, k in sides]
    if not sides:
        plan.refusal = "select the open side(s) to extrude: an edge of a patch outline, its two corners, or a patch"
        return plan
    table = side_table(P)
    for r, k in sides:
        users = table.get(side_key(P[r], k), [])
        if len(users) != 1:
            a, b = traversal(P[r], k)[0], traversal(P[r], k)[3]
            plan.refusal = (f"side {a}-{b} of patch {r} is shared by patches {sorted({u[0] for u in users})}: extruding "
                            "it would give an edge used by three patches, which the loader rejects. Select open sides")
            return plan
    if len(P) + len(sides) > MAX_PATCHES:
        plan.refusal = f"extruding {len(sides)} sides would make {len(P) + len(sides)} patches (max {MAX_PATCHES})"
        return plan
    # core side ids: k (cycle) 0 = column 0 -> core 1, 1 = row 3 -> core 3, 2 = column 3 -> core 2, 3 = row 0 -> core 0
    to_core = {0: 1, 1: 3, 2: 2, 3: 0}
    steps: Dict[int, list] = {}                       # corner -> the outward steps of the sides meeting there
    per_side = []
    for r, k in sides:
        g = V[P[r]].reshape(4, 4, 3)
        cs = to_core[k]
        edge = {0: g[0], 3: g[3], 1: g[:, 0], 2: g[:, 3]}[cs]
        d = {0: g[0] - g[1], 3: g[3] - g[2], 1: g[:, 0] - g[:, 1], 2: g[:, 3] - g[:, 2]}[cs]
        cps_side = [int(P[r][i]) for i in core._SIDE_CPS[cs]]
        per_side.append((r, cs, cps_side, edge, d))
        steps.setdefault(cps_side[0], []).append(d[0]); steps.setdefault(cps_side[3], []).append(d[3])
    new_pts, new_from, keyed = [], [], {}

    def make(tag, pos, frm):
        j = keyed.get(tag)
        if j is None:
            j = n + len(new_pts); keyed[tag] = j
            new_pts.append(np.asarray(pos, dtype=np.float64)); new_from.append(int(frm))
        return j
    rows, src = [], []
    for r, cs, cps_side, edge, d in per_side:
        shared_slots, step_slots = core._EXTRUDE_LAYOUT[cs]
        cp = [0] * 16
        for kk, s in enumerate(shared_slots):
            cp[s] = cps_side[kk]
        for step, slots in enumerate(step_slots, start=1):
            for kk, s in enumerate(slots):
                pt = cps_side[kk]
                if kk in (0, 3):
                    dm = np.mean(steps[pt], axis=0)
                    cp[s] = make(("c", pt, step), V[pt] + step * dm, pt)
                else:
                    cp[s] = make(("h", pt, step), V[pt] + step * d[kk], pt)
        rows.append(cp); src.append(r)
    src = np.array(src, dtype=np.int64)
    plan.new_points = np.array(new_pts, dtype=np.float64).reshape(-1, 3)
    plan.new_from = np.array(new_from, dtype=np.int64)
    plan.patches = np.vstack([P, np.array(rows, dtype=np.int64)])
    plan.uv = np.vstack([uv, uv[src]]); plan.mat = np.concatenate([mat, mat[src]])
    plan.ptype = np.concatenate([ptype, ptype[src]]); plan.limb = np.concatenate([limb, limb[src]])
    plan.manual = np.concatenate([manual, np.zeros(len(src), dtype=bool)])
    plan.row_src = np.concatenate([np.arange(len(P)), src])
    plan.info.update(sides=len(sides), points=len(new_pts), patches=len(rows))
    plan.select_virtual = list(range(n, n + len(new_pts)))
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Fill Hole
def hole_loops(patches) -> List[List[Tuple[int, int, tuple]]]:
    """The closed loops of open sides, each as ``[(row, k, key)]`` in loop order (consecutive sides share a corner);
    a corner where more than two open sides meet (or a chain that does not close) gives no loop."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    opens = open_sides(P)
    at: Dict[int, list] = {}
    for i, (r, k, key) in enumerate(opens):
        at.setdefault(key[0], []).append(i); at.setdefault(key[3], []).append(i)
    if any(len(v) != 2 for v in at.values()):
        bad = {c for c, v in at.items() if len(v) != 2}
        loops = []
    else:
        bad = set()
        loops = []
    seen = set()
    for i0 in range(len(opens)):
        if i0 in seen or (opens[i0][2][0] in bad or opens[i0][2][3] in bad):
            continue
        loop = [i0]; seen.add(i0)
        c = opens[i0][2][3]
        cur = i0
        ok = True
        while True:
            nxt = [j for j in at.get(c, []) if j != cur]
            if len(nxt) != 1:
                ok = False; break
            j = nxt[0]
            if j == i0:
                break
            if j in seen:
                ok = False; break
            loop.append(j); seen.add(j)
            key = opens[j][2]
            c = key[3] if key[0] == c else key[0]
            cur = j
        if ok and len(loop) >= 3:
            loops.append([opens[i] for i in loop])
    return loops


def fill_plan(V, patches, tables, selected_points=(), rings=()) -> TopoPlan:
    """Fill the hole loop touching ``selected_points`` (the only hole when nothing is selected), see the module."""
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    loops = hole_loops(P)
    if not loops:
        k = len(open_sides(P))
        plan.refusal = ("this creature is closed: no hole to fill" if not k else
                        f"the {k} open sides form no closed loop (a chain, or a corner where more than two open sides "
                        "meet): Add Quad on the open sides, then Weld")
        return plan
    sel = {int(i) for i in selected_points}
    if sel:
        hit = [lp for lp in loops if any(key[0] in sel or key[3] in sel for _r, _k, key in lp)]
        if not hit:
            plan.refusal = (f"no corner of the selection is on a hole; the creature has {_plural(len(loops), 'hole')} of "
                            f"{[len(lp) for lp in loops]} sides. Select a corner of the hole to fill, or nothing for "
                            "the only hole")
            return plan
        if len(hit) > 1:
            plan.refusal = f"the selection touches {len(hit)} holes: select a corner of one hole only"
            return plan
        loop = hit[0]
    elif len(loops) == 1:
        loop = loops[0]
    else:
        plan.refusal = (f"the creature has {len(loops)} holes ({[len(lp) for lp in loops]} sides): select a corner of "
                        "the one to fill")
        return plan
    m = len(loop)
    if m % 2 or m < 4:
        plan.refusal = (f"the hole has {m} sides: a strip of patches needs an even count of at least 4. Add Quad on "
                        "one of its sides first (the hole gets 2 more sides), or weld two of its corners")
        return plan
    # the loop's corners in order, and the direction each side's patch traverses it
    corners = []
    c = loop[0][2][0] if loop[0][2][0] not in (loop[1][2][0], loop[1][2][3]) else loop[0][2][3]
    for r, k, key in loop:
        corners.append(c)
        c = key[3] if key[0] == c else key[0]
    # the patches traverse the sides in the loop direction (+1) or against it (-1): the fill must go the other way
    votes = 0
    for (r, k, key), a in zip(loop, corners):
        tr = traversal(P[r], k)
        votes += 1 if tr[0] == a else -1
    if votes > 0:                                     # they traverse it forward: our patches go backward
        # corners reversed, so side i now runs corners[i] -> corners[i + 1] = the old side m - 2 - i
        corners = corners[::-1]
        loop = [loop[(m - 2 - i) % m] for i in range(m)]
    if abs(votes) != m:
        plan.warnings.append(f"the hole's neighbours are not consistently oriented ({(m - abs(votes)) // 2} of {m} sides "
                             "run the other way): the fill follows the majority; check the surface normals")
    side_of = {}
    for i in range(m):
        a, b = corners[i], corners[(i + 1) % m]
        r, k, key = loop[i]
        assert {key[0], key[3]} == {a, b}, "loop / corner order mismatch"
        side_of[(a, b)] = key
    nn = m // 2
    # the rotation with the shortest chords: chords join corners j + 1 and 2n - 2 - j (j = 0 .. n - 3)
    best, best_len = 0, None
    for s in range(m):
        cs = [corners[(s + i) % m] for i in range(m)]
        length = sum(np.linalg.norm(V[cs[j + 1]] - V[cs[2 * nn - 2 - j]]) for j in range(nn - 2))
        if best_len is None or length < best_len - 1e-12:
            best, best_len = s, length
    cs = [corners[(best + i) % m] for i in range(m)]
    new_pts, new_from, keyed = [], [], {}

    def make(tag, pos, frm):
        j = keyed.get(tag)
        if j is None:
            j = n + len(new_pts); keyed[tag] = j
            new_pts.append(np.asarray(pos, dtype=np.float64)); new_from.append(int(frm))
        return j

    def side(a, b):
        """The 4 points from corner a to corner b: an existing open side, or a chord with new handles."""
        key = side_of.get((a, b)) or side_of.get((b, a))
        if key is not None:
            pts = list(key) if key[0] == a else list(key[::-1])
            return pts
        h1 = make(("h", min(a, b), max(a, b), 1 if a < b else 2), V[a] + (V[b] - V[a]) / 3.0, a)
        h2 = make(("h", min(a, b), max(a, b), 2 if a < b else 1), V[b] + (V[a] - V[b]) / 3.0, b)
        return [a, h1, h2, b]
    # per-corner UVs / the neighbours' tables
    corner_uv, votes_l, votes_m, votes_t = {}, {}, {}, {}
    for r, k, key in loop:
        cps = P[r].tolist()
        for s, slot in enumerate(PR.CORNER_SLOTS):
            corner_uv.setdefault(cps[slot], uv[r][2 * s:2 * s + 2])
        votes_l[int(limb[r])] = votes_l.get(int(limb[r]), 0) + 1
        votes_m[int(mat[r])] = votes_m.get(int(mat[r]), 0) + 1
        votes_t[int(ptype[r])] = votes_t.get(int(ptype[r]), 0) + 1
    fl = max(votes_l, key=votes_l.get); fm = max(votes_m, key=votes_m.get); ft = max(votes_t, key=votes_t.get)
    rows, uvs = [], []
    for j in range(nn - 1):
        c0, c1, c2, c3 = cs[j], cs[j + 1], cs[2 * nn - 2 - j], cs[2 * nn - 1 - j]
        cp = [0] * 16
        s0 = side(c0, c1); s1 = side(c1, c2); s2 = side(c2, c3); s3 = side(c3, c0)
        for slot, v in zip(CYCLE_SIDES[0], s0): cp[slot] = v
        for slot, v in zip(CYCLE_SIDES[1], s1): cp[slot] = v
        for slot, v in zip(CYCLE_SIDES[2], s2): cp[slot] = v
        for slot, v in zip(CYCLE_SIDES[3], s3): cp[slot] = v
        for slot in PR.INTERIOR_SLOTS:
            i_, c_, h1, h2 = next(f for f in PR.INTERIOR_FRAME if f[0] == slot)
            cp[slot] = make(("i", j, slot), np.zeros(3), cp[c_])
        rows.append(cp)
        uvs.append(np.concatenate([corner_uv.get(cp[s], np.zeros(2)) for s in PR.CORNER_SLOTS]))
    # interiors on the rule (virtual pool)
    Vv = np.vstack([V, np.array(new_pts).reshape(-1, 3)])
    Pn = np.array(rows, dtype=np.int64)
    auto = PR.auto_interiors(Vv, Pn)
    for pi, cp in enumerate(rows):
        for kk, slot in enumerate(PR.INTERIOR_SLOTS):
            new_pts[cp[slot] - n] = auto[pi, kk]
    plan.new_points = np.array(new_pts, dtype=np.float64).reshape(-1, 3)
    plan.new_from = np.array(new_from, dtype=np.int64)
    plan.patches = np.vstack([P, Pn])
    plan.uv = np.vstack([uv, np.array(uvs).reshape(-1, 8)])
    plan.mat = np.concatenate([mat, np.full(len(rows), fm)]); plan.ptype = np.concatenate([ptype, np.full(len(rows), ft)])
    plan.limb = np.concatenate([limb, np.full(len(rows), fl)])
    plan.manual = np.concatenate([manual, np.zeros(len(rows), bool)])
    plan.row_src = np.concatenate([np.arange(len(P)), np.full(len(rows), -1)])
    plan.info.update(sides=m, patches=len(rows), points=len(new_pts), chords=nn - 2, holes=len(loops))
    return finalize(plan, P)


# ------------------------------------------------------------------------------------------- Extrude Region
def extrude_region_plan(V, patches, rows, tables, amount=0.0, outline=1.0, rings=()) -> TopoPlan:
    """Lift the selected patches and wall them in (see the module rules)."""
    V = np.asarray(V, dtype=np.float64)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = len(V)
    uv, mat, ptype, limb, manual = _tables(*tables, len(P))
    plan = TopoPlan(n, np.arange(n), np.zeros((0, 3)), np.zeros(0, dtype=np.int64), P.copy(), uv, mat, ptype, limb,
                    manual.copy(), np.arange(len(P)))
    rows = sorted({int(r) for r in rows if 0 <= int(r) < len(P)})
    if not rows:
        plan.refusal = "select the patches to extrude (Face select, or all 4 corners of each)"
        return plan
    if len(rows) >= len(P):
        plan.refusal = "every patch is selected: an extrusion needs unselected neighbours to wall against"
        return plan
    selm = np.zeros(len(P), dtype=bool); selm[rows] = True
    region = np.zeros(n, dtype=bool); region[P[selm].reshape(-1)] = True
    outside = np.zeros(n, dtype=bool); outside[P[~selm].reshape(-1)] = True
    boundary = region & outside
    table = side_table(P)
    walls = []                                          # (row, k) of the selected patch's boundary sides
    deg: Dict[int, int] = {}
    for r in rows:
        for k in range(4):
            users = table[side_key(P[r], k)]
            if all(selm[u[0]] for u in users):
                continue                                # inside the region
            walls.append((r, k))
            tr = traversal(P[r], k)
            deg[tr[0]] = deg.get(tr[0], 0) + 1; deg[tr[3]] = deg.get(tr[3], 0) + 1
    for c in np.nonzero(boundary)[0].tolist():
        if PR.roles(P, n).corner[c] and deg.get(c, 0) == 0:
            plan.refusal = (f"corner {c} joins the region to unselected patches through the corner only (no shared "
                            "side): the extrusion would tear there. Select the patches around it too")
            return plan
    if len(P) + len(walls) > MAX_PATCHES:
        plan.refusal = f"the extrusion would make {len(P) + len(walls)} patches (max {MAX_PATCHES})"
        return plan
    # the lift: the region's mean corner-quad normal and centre
    quads = P[rows][:, list((0, 12, 15, 3))]
    c4 = V[quads]
    nrm = np.zeros(3)
    for kk in range(4):
        a, b = c4[:, kk], c4[:, (kk + 1) % 4]
        nrm[0] += ((a[:, 1] - b[:, 1]) * (a[:, 2] + b[:, 2])).sum()
        nrm[1] += ((a[:, 2] - b[:, 2]) * (a[:, 0] + b[:, 0])).sum()
        nrm[2] += ((a[:, 0] - b[:, 0]) * (a[:, 1] + b[:, 1])).sum()
    ln = np.linalg.norm(nrm)
    nrm = nrm / ln if ln > 0 else np.array([0.0, 0.0, 1.0])
    centre = V[np.unique(quads.reshape(-1))].mean(axis=0)

    def lifted(p):
        return centre + (p - centre) * float(outline) + float(amount) * nrm
    new_pts, new_from, keyed = [], [], {}

    def make(tag, pos, frm):
        j = keyed.get(tag)
        if j is None:
            j = n + len(new_pts); keyed[tag] = j
            new_pts.append(np.asarray(pos, dtype=np.float64)); new_from.append(int(frm))
        return j
    moved: Dict[int, np.ndarray] = {}
    Pn = P.copy()
    for r in rows:
        for s in range(16):
            v = int(P[r, s])
            if boundary[v]:
                Pn[r, s] = make(("copy", v), lifted(V[v]), v)
            elif v not in moved:
                moved[v] = lifted(V[v])
    wall_rows, wall_src, wall_uv = [], [], []
    for r, k in walls:
        a, h1, h2, b = traversal(P[r], k)
        cp = [0] * 16
        cp[0], cp[1], cp[2], cp[3] = b, h2, h1, a
        for step, base in ((1, 4), (2, 8)):
            for kk, v in enumerate((b, h2, h1, a)):
                cp[base + kk] = make(("wall", v, step), V[v] + (lifted(V[v]) - V[v]) * step / 3.0, v)
        for kk, v in enumerate((b, h2, h1, a)):
            cp[12 + kk] = keyed[("copy", v)] if boundary[v] else int(v)
        for slot in PR.INTERIOR_SLOTS:
            i_, c_, hh1, hh2 = next(f for f in PR.INTERIOR_FRAME if f[0] == slot)
            cp[slot] = make(("wi", r, k, slot), np.zeros(3), cp[c_] if cp[c_] < n else new_from[cp[c_] - n])
        wall_rows.append(cp); wall_src.append(r)
        u = uv[r].reshape(4, 2)
        cu = {int(P[r, s]): u[i] for i, s in enumerate(PR.CORNER_SLOTS)}
        wall_uv.append(np.concatenate([cu.get(b, np.zeros(2)), cu.get(a, np.zeros(2)), cu.get(b, np.zeros(2)),
                                       cu.get(a, np.zeros(2))]))
    Vv = np.vstack([V, np.array(new_pts).reshape(-1, 3)]) if new_pts else V
    for i, p in moved.items():
        Vv[i] = p
    W = np.array(wall_rows, dtype=np.int64).reshape(-1, 16)
    if len(W):
        auto = PR.auto_interiors(Vv, W)
        for pi, cp in enumerate(wall_rows):
            for kk, slot in enumerate(PR.INTERIOR_SLOTS):
                new_pts[cp[slot] - n] = auto[pi, kk]
    plan.new_points = np.array(new_pts, dtype=np.float64).reshape(-1, 3)
    plan.new_from = np.array(new_from, dtype=np.int64)
    plan.patches = np.vstack([Pn, W]) if len(W) else Pn
    src = np.array(wall_src, dtype=np.int64)
    plan.uv = np.vstack([uv, np.array(wall_uv).reshape(-1, 8)]) if len(W) else uv
    plan.mat = np.concatenate([mat, mat[src]]); plan.ptype = np.concatenate([ptype, ptype[src]])
    plan.limb = np.concatenate([limb, limb[src]]); plan.manual = np.concatenate([manual, np.zeros(len(src), bool)])
    plan.row_src = np.concatenate([np.arange(len(P)), src])
    plan.moved = moved
    lift_pts = sorted({int(v) for v in Pn[rows].reshape(-1).tolist()})
    plan.select_virtual = lift_pts
    plan.info.update(patches=len(rows), walls=len(walls), points=len(new_pts), copies=int(boundary.sum()),
                     normal=nrm.tolist())
    return finalize(plan, P)


# =========================================================================================== bpy: the applier
try:
    import bpy
except ImportError:                       # pure tests import the plans only
    bpy = None


def _rings_for_plan(obj):
    from . import patchsel
    return patchsel._ring_rows_for_plan(obj)


def _tables_of(obj):
    from . import store
    n = len(store.patches(obj))
    return (store.patch_uv(obj), store.patch_mat(obj), store.patch_type(obj), store.patch_limb(obj),
            store.manual_interior(obj, n))


def _verts(obj) -> np.ndarray:
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def creature_size(obj) -> float:
    V = _verts(obj)
    return float(np.linalg.norm(V.max(axis=0) - V.min(axis=0))) if len(V) else 1.0


def _hide_rows(bm, patches, rows) -> None:
    """Hide the corner quads of ``rows`` and the corners / handles no visible patch uses (BZ_OT_hide_patches's rule)."""
    from . import patchsel
    if not len(rows):
        return
    bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    frows = patchsel.face_rows(patches)
    want = {int(r) for r in rows}
    for i, f in enumerate(bm.faces):
        if i < len(frows) and int(frows[i]) in want:
            f.hide = True; f.select = False
    hidden = np.zeros(len(patches), dtype=bool)            # the mask the hide rule works from (faces just hidden too)
    for i, f in enumerate(bm.faces):
        if i < len(frows):
            hidden[frows[i]] = f.hide
    users_hidden = np.ones(len(bm.verts), dtype=bool)
    users_hidden[patches[~hidden].reshape(-1)] = False
    used = np.zeros(len(bm.verts), dtype=bool); used[patches.reshape(-1)] = True
    for i in np.nonzero(users_hidden & used)[0]:
        v = bm.verts[int(i)]; v.hide = True; v.select = False
    for e in bm.edges:
        if e.verts[0].hide or e.verts[1].hide:
            e.hide = True; e.select = False


def pole_refusal(patches_old, patches_new) -> Optional[str]:
    """Why a plan must not be applied because it makes a pole the game cannot hold, or None: more patch corners meeting
    at one control point than IC.exe's corner record has room for (12, ``ICParse.validate.MAX_CORNER_PATCHES``; the
    Object Editor allows 16, so it would still load there). A pole the creature already had is not held against the
    tool unless the plan makes it bigger."""
    from ICParse import validate as ICval
    if patches_new is None:
        return None
    before = max(ICval.corner_valence(np.asarray(patches_old, dtype=np.int64).reshape(-1, 16)).values(), default=0)
    after = ICval.corner_valence(np.asarray(patches_new, dtype=np.int64).reshape(-1, 16))
    worst = max(after.values(), default=0)
    if worst <= ICval.MAX_CORNER_PATCHES or worst <= before:
        return None
    v = max(after, key=after.get)
    return (f"{worst} patch corners would meet at control point {v}; the game holds at most {ICval.MAX_CORNER_PATCHES}. "
            "Split the pole into smaller fans")


def apply_plan(op, obj, plan: TopoPlan, patches_old, select="rows", keep_mode=True) -> bool:
    """Write a finalized plan into the object (see the module docstring); reports and returns False on a refusal."""
    import bmesh
    import dataclasses
    from . import draw, ops, patchfeel, patchsel, store
    if plan.refusal:
        op.report({"ERROR"}, f"{op.bl_label}: {plan.refusal}")
        return False
    P0 = np.asarray(patches_old, dtype=np.int64).reshape(-1, 16)
    pole = pole_refusal(P0, plan.patches_final)
    if pole:
        op.report({"ERROR"}, f"{op.bl_label}: {pole}")
        return False
    n0 = store.point_count(obj)
    if n0 != plan.n_old:
        op.report({"ERROR"}, f"{op.bl_label}: the plan was made for {plan.n_old} points, the mesh has {n0}")
        return False
    rings = _rings_for_plan(obj)
    for name, ring, _enc in rings:
        try:
            patchsel.remap_ring(ring, plan.o2n)
        except ValueError as e:
            op.report({"ERROR"}, f"{op.bl_label}: connection ring '{name}': {e}. Delete the connection first "
                                 "(Boner > Connections)")
            return False
    vb = store.vert_bone(obj)
    rigged = store.rig(obj) is not None and len(vb) == n0
    if rigged:
        live = ops.skin_state(op, obj)
        if live is None:
            return False
        vb = np.asarray(live.base, dtype=np.int64)
    if not ops._begin(op, obj):
        return False
    try:
        conn = store.conn_rows(obj)
    except ValueError as e:
        op.report({"ERROR"}, str(e))
        return False
    g = int(obj[store.KEY_GEN])
    started_object = obj.mode != "EDIT"
    if started_object:
        bpy.ops.object.mode_set(mode="EDIT")
    psrc = store.point_ints(obj, store.ATTR_SRC)
    if psrc is None:
        ns = int(obj.get(store.KEY_SRC_NVERTS, n0))
        psrc = np.where(np.arange(n0) < ns, np.arange(n0), -1)
    cop = store.point_ints(obj, store.ATTR_COPLANAR)
    cop = np.zeros(n0, dtype=np.int64) if cop is None or len(cop) != n0 else cop
    hidden_before = patchsel.hidden_rows(obj, P0)
    # 1. edit bmesh: every corner quad and edge goes (rebuilt below), then the merged / dropped points
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for f in list(bm.faces):
        bm.faces.remove(f)
    for e in list(bm.edges):
        bm.edges.remove(e)
    gone = [bm.verts[int(i)] for i in np.nonzero(~plan.keep)[0]]
    for v in gone:
        bm.verts.remove(v)
    bmesh.update_edit_mesh(obj.data)
    # 2. the mesh arrays (compacted in the old order): the new points, the moved ones, the corner quads
    bpy.ops.object.mode_set(mode="OBJECT")
    me = obj.data
    if len(me.vertices) != plan.n_kept:
        op.report({"ERROR"}, f"{op.bl_label}: {len(me.vertices)} points after the compaction, {plan.n_kept} planned; Undo")
        return False
    k = plan.n_new
    if k:
        me.vertices.add(k)
    n1 = plan.n_kept + k
    co = np.empty(n1 * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    if k:
        co[plan.n_kept:] = plan.new_points.astype(np.float32)
    for i_old, p in plan.moved.items():
        j = int(plan.o2n[int(i_old)])
        if j >= 0:
            co[j] = np.asarray(p, dtype=np.float32)
    me.vertices.foreach_set("co", co.reshape(-1))
    Pn = plan.patches_final
    _rows, quads = patchsel.face_quads(Pn)
    if len(quads):
        me.loops.add(4 * len(quads)); me.polygons.add(len(quads))
        me.loops.foreach_set("vertex_index", quads.reshape(-1).astype(np.int32))
        me.polygons.foreach_set("loop_start", np.arange(0, 4 * len(quads), 4, dtype=np.int32))
        me.polygons.foreach_set("loop_total", np.full(len(quads), 4, dtype=np.int32))
    me.update(calc_edges=True)
    kept_old = np.nonzero(plan.keep)[0]
    frm = plan.new_from
    src_new = np.full(k, -1, dtype=np.int64)
    cop_new = (np.asarray(plan.new_coplanar, dtype=np.int64) if plan.new_coplanar is not None
               else np.array([int(cop[f]) if f >= 0 else 0 for f in frm], dtype=np.int64))
    store.set_point_ints(obj, store.ATTR_CP, np.arange(n1))
    store.set_point_ints(obj, store.ATTR_GEN, np.full(n1, g))
    store.set_point_ints(obj, store.ATTR_SRC, np.concatenate([psrc[kept_old], src_new]))
    store.set_point_ints(obj, store.ATTR_COPLANAR, np.concatenate([cop[kept_old], cop_new]))
    if len(vb) == n0:
        own_new = (np.asarray(plan.new_owner, dtype=np.int64) if plan.new_owner is not None
                   else np.array([int(vb[f]) if f >= 0 else (int(vb[0]) if len(vb) else 0) for f in frm], dtype=np.int64))
        store.set_vert_bone(obj, np.concatenate([vb[kept_old], own_new]))
    # 3. the tables
    store.set_patches(obj, Pn)
    store.set_pool_n(obj, n1)
    store.set_patch_uv(obj, plan.uv)
    store.set_patch_mat(obj, plan.mat)
    store.set_patch_type(obj, plan.ptype)
    store.set_patch_limb(obj, plan.limb)
    store.set_manual_interior(obj, plan.manual)
    if conn is not None:
        out = []
        for r in conn:
            if r.raw is not None:
                out.append(dataclasses.replace(r, raw=patchsel.remap_ring(r.raw, plan.o2n)))
            else:
                out.append(dataclasses.replace(r, corners=patchsel.remap_ring(r.corners or [], plan.o2n)))
        store.set_conn_rows(obj, out)
    # 4. back in Edit Mode: hidden patches stay hidden, interiors hidden, the selection
    bpy.ops.object.mode_set(mode="EDIT")
    patchfeel.apply_interior_visibility(obj)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    if hidden_before.any():
        rows_h = [i for i, s in enumerate(plan.row_src.tolist()) if s >= 0 and hidden_before[s]]
        _hide_rows(bm, Pn, rows_h)
    if select == "points" and plan.select_virtual is not None:
        for j in plan.virtual_to_final(plan.select_virtual).tolist():
            v = bm.verts[int(j)]
            if not v.hide:
                v.select = True
    elif select == "rows":
        made = [i for i, s in enumerate(plan.row_src.tolist()) if s < 0 or i >= len(P0) or s != i]
        made = plan.info.get("new_rows", made)
        patchsel.select_rows(bm, Pn, made, extend=True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    if started_object and keep_mode:
        bpy.ops.object.mode_set(mode="OBJECT")
    ops.forget_skin_stats(obj)
    ops._OPEN_SIDES.pop(obj.name, None)
    draw.invalidate(obj.name)
    for w in plan.warnings:
        op.report({"WARNING"}, w)
    return True


# ------------------------------------------------------------------------------------------- selection readers
def _selected_points(obj) -> list:
    from . import ops
    return ops._selected_indices(obj)


def _rows_selected(obj, patches) -> list:
    from . import patchsel
    return patchsel._rows_from_selection(obj, patches)


def selected_open_sides(obj, patches) -> Tuple[list, Optional[str]]:
    """The open sides to extrude: the selected faces' open sides, else every open side whose two corners are selected
    (an edge of the patch outline in Edge select, the two corners in Vertex select). ``([(row, k)], why none)``."""
    from . import patchsel
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    table = side_table(P)
    rows = patchsel.selected_rows(obj, P)
    if rows:
        out = [(r, k) for r in rows for k in range(4) if len(table[side_key(P[r], k)]) == 1]
        if not out:
            return [], f"the {len(rows)} selected patch(es) have no open side (every side is shared)"
        return out, None
    sel = set(_selected_points(obj))
    if len(sel) < 2:
        k = sum(1 for u in table.values() if len(u) == 1)
        return [], (f"select the two corners of an open side, an outline edge, or a patch with open sides (this creature "
                    f"has {k} open side{'s' if k != 1 else ''})")
    out, shared = [], []
    for key, users in table.items():
        if key[0] in sel and key[3] in sel:
            (out if len(users) == 1 else shared).append(users[0])
    if not out:
        if shared:
            r, k = shared[0]
            return [], (f"side {side_key(P[r], k)} is shared by patches {sorted({u[0] for u in table[side_key(P[r], k)]})}: "
                        "extruding it would give an edge used by three patches. Select open sides")
        return [], "no open side has both of its corners selected"
    return out, None


# ------------------------------------------------------------------------------------------- operators
if bpy is not None:
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty

    def _poll(cls, context, edit=True):
        from . import ops
        return ops._guard_poll(cls, context, edit=edit)

    class BZ_OT_weld(bpy.types.Operator):
        """Weld the selected corners together, or into the active corner"""
        bl_idname = "bz.weld"
        bl_label = "Weld"
        bl_options = {"REGISTER", "UNDO"}

        mode: EnumProperty(name="Mode", items=[("SELECTED", "Selected", "Merge selected corners within the threshold"),
                                                ("TARGET", "Target", "Merge every selected corner into the active one")],
                           default="SELECTED")
        threshold: FloatProperty(name="Threshold", default=0.0, min=0.0, unit="LENGTH",
                                 description="Corners closer than this merge (0 = 1 % of the creature's size)")

        @classmethod
        def poll(cls, context):
            return _poll(cls, context)

        def execute(self, context):
            import bmesh
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            V = _verts(obj)
            sel = _selected_points(obj)
            target = None
            if self.mode == "TARGET":
                bm = bmesh.from_edit_mesh(obj.data)
                act = bm.select_history.active
                if not isinstance(act, bmesh.types.BMVert):
                    self.report({"ERROR"}, "Weld Target: make the corner to weld into the active vertex (click it last)")
                    return {"CANCELLED"}
                target = act.index
            thr = self.threshold if self.threshold > 0 else 0.01 * creature_size(obj)
            plan = weld_plan(V, patches, sel, thr, target=target, rings=_rings_for_plan(obj), uv=store.patch_uv(obj),
                             mat=store.patch_mat(obj), ptype=store.patch_type(obj), limb=store.patch_limb(obj),
                             manual=store.manual_interior(obj, len(patches)))
            if not apply_plan(self, obj, plan, patches, select="points"):
                return {"CANCELLED"}
            i = plan.info
            line = (f"Welded {i['corners_merged'] + i['groups']} corners into {i['groups']}"
                    f"{' (into the active corner)' if target is not None else f' within {thr:.4g} m'}, "
                    f"{i['handles_merged']} handle pairs merged, {i['dropped']} points dropped; open sides "
                    f"{i['open_before']} -> {i['open_after']}, {i['touched']} patches' interiors on the rule"
                    + (f"; {i['ignored']} non-corner points ignored" if i["ignored"] else ""))
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    def _limb_items(self, context):
        from . import store
        obj = context.active_object
        names = store.limb_names(obj) if store.is_native(obj) else []
        items = [(str(i), n, "") for i, n in enumerate(names)]
        return items or [("-1", "(no limbs: the patches go in no limb)", "")]

    def _bone_items(self, context):
        from . import store
        obj = context.active_object
        names = store.bone_rig_names(obj) if store.is_native(obj) else []
        items = [(str(i), n, "") for i, n in enumerate(names) if n]
        return items or [("0", "bone 0", "")]

    class BZ_OT_attach(bpy.types.Operator):
        """Join another selected creature into the active one"""
        bl_idname = "bz.attach"
        bl_label = "Attach"
        bl_options = {"REGISTER", "UNDO"}

        limb: EnumProperty(name="Limb", items=_limb_items, description="The limb the attached patches join")
        bone: EnumProperty(name="Bone", items=_bone_items, description="The bone the attached points are skinned to")

        @classmethod
        def poll(cls, context):
            from . import store
            if not _poll(cls, context, edit=False):
                return False
            obj = context.active_object
            others = [o for o in context.selected_objects if o != obj and o.type == "MESH"]
            if len(others) != 1:
                cls.poll_message_set("select the creature to attach together with this one (the active one keeps its "
                                     "skeleton)")
                return False
            if not store.is_native(others[0]):
                cls.poll_message_set(f"'{others[0].name}' is not a creature: use Convert Mesh or Fit Patches to Mesh first")
                return False
            return True

        def invoke(self, context, event):
            from . import store
            obj = context.active_object
            names = store.limb_names(obj)
            torso = next((i for i, n in enumerate(names) if n.lower().endswith("torso")), 0)
            self.limb = str(torso) if names else "-1"
            arm = store.rig(obj)
            act = arm.data.bones.active.name if arm is not None and arm.data.bones.active else None
            bn = store.bone_rig_names(obj)
            self.bone = str(bn.index(act)) if act in bn else "0"
            return context.window_manager.invoke_props_dialog(self)

        def execute(self, context):
            from . import store
            obj = context.active_object
            others = [o for o in context.selected_objects if o != obj and o.type == "MESH"]
            if len(others) != 1 or not store.is_native(others[0]):
                self.report({"ERROR"}, "Attach: select exactly one other creature with this one")
                return {"CANCELLED"}
            other = others[0]
            if other.mode == "EDIT":
                self.report({"ERROR"}, f"Attach: '{other.name}' is in Edit Mode; leave it (Tab) first")
                return {"CANCELLED"}
            for o in (obj, other):
                err = store.pool_error(o)
                if err:
                    self.report({"ERROR"}, f"Attach: '{o.name}': {err}")
                    return {"CANCELLED"}
            P1, P2 = store.patches(obj), store.patches(other)
            V2 = _verts(other)
            M = np.array(obj.matrix_world.inverted() @ other.matrix_world, dtype=np.float64)
            V2 = (V2 @ M[:3, :3].T) + M[:3, 3]
            names1 = store.material_slots(obj)[0]
            names2 = store.material_slots(other)[0]
            mat_map = np.array([names1.index(nm) if nm in names1 else -1 for nm in names2], dtype=np.int64)
            limb = int(self.limb) if store.limb_names(obj) else -1
            bone = int(self.bone)
            cop2 = store.point_ints(other, store.ATTR_COPLANAR)
            plan = attach_plan(_verts(obj), P1, _tables_of(obj), V2, P2, _tables_of(other), limb, bone, mat_map, cop2)
            if plan.refusal:
                self.report({"ERROR"}, f"Attach: {plan.refusal}")
                return {"CANCELLED"}
            n_conn = len(store.connections(other) or [])
            if n_conn:
                plan.warnings.append(f"{_plural(n_conn, 'connection ring')} of '{other.name}' dropped (a socket belongs to "
                                     "the creature it is cut into: make one with Boner > Connections if needed)")
            if not apply_plan(self, obj, plan, P1):
                return {"CANCELLED"}
            arm = store.rig(other)
            name = other.name
            bpy.data.objects.remove(other, do_unlink=True)
            if arm is not None and arm != store.rig(obj):
                bpy.data.objects.remove(arm, do_unlink=True)
            i = plan.info
            line = (f"Attached '{name}': +{i['points']} points, +{i['patches']} patches in limb "
                    f"'{store.limb_names(obj)[limb] if limb >= 0 else '(none)'}', skinned to "
                    f"'{store.bone_rig_names(obj)[bone] if bone < len(store.bone_rig_names(obj)) else bone}'"
                    + (f", {i['unmatched']} patches' material unmatched -> slot 0" if i["unmatched"] else "")
                    + (f", {n_conn} rings dropped" if n_conn else "") + "; weld the seams next")
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_mirror_whole(bpy.types.Operator):
        """Mirror half a creature across X to make it whole"""
        bl_idname = "bz.mirror_whole"
        bl_label = "Mirror to Whole"
        bl_options = {"REGISTER", "UNDO"}

        threshold: FloatProperty(name="Plane Threshold", default=0.0, min=0.0, unit="LENGTH",
                                 description="Points within this of X = 0 are shared (0 = 0.1 % of the creature's size)")
        snap: BoolProperty(name="Snap Seam to Plane", default=True,
                           description="Put the shared points exactly on X = 0")

        @classmethod
        def poll(cls, context):
            return _poll(cls, context)

        def execute(self, context):
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            rows = _rows_selected(obj, patches) or list(range(len(patches)))
            thr = self.threshold if self.threshold > 0 else 0.001 * creature_size(obj)
            vb = store.vert_bone(obj)
            owners = vb if len(vb) == store.point_count(obj) else None
            omap = mirror_owner_map(store.bone_rig_names(obj)) if owners is not None else None
            lmap = mirror_limb_map(store.limb_ids(obj))
            plan = mirror_plan(_verts(obj), patches, rows, thr, _tables_of(obj), owners=owners, owner_map=omap,
                               limb_map=lmap if len(lmap) else None, snap=self.snap)
            if not apply_plan(self, obj, plan, patches):
                return {"CANCELLED"}
            i = plan.info
            holes = open_sides(store.patches(obj))
            snapped = f", {i['snapped']} snapped to X = 0" if i["snapped"] else ""
            line = (f"Mirrored {i['patches']} patches from the {i['side']}X side: +{i['points']} points, {i['shared']} "
                    f"points on the plane shared (seam welded{snapped}), "
                    f"{i['owners_mirrored']} points skinned to mirror bones, {i['limbs_mirrored']} patches in mirror "
                    f"limbs; {len(holes)} open sides now")
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_subdivide(bpy.types.Operator):
        """Split the selected patches into four without changing the surface"""
        bl_idname = "bz.subdivide"
        bl_label = "Subdivide"
        bl_options = {"REGISTER", "UNDO"}

        exact: BoolProperty(name="Exact", default=True,
                            description="Keep the exact surface; off resets interiors, changing the shape slightly")
        max_patches: IntProperty(name="Max Patches", default=MAX_PATCHES, min=1, max=MAX_PATCHES,
                                 description="Refuse when the propagation would exceed this many patches")

        @classmethod
        def poll(cls, context):
            return _poll(cls, context)

        def execute(self, context):
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            rows = _rows_selected(obj, patches)
            plan = subdivide_plan(_verts(obj), patches, rows, _tables_of(obj), rings=_rings_for_plan(obj),
                                  max_patches=self.max_patches, exact=self.exact)
            if not apply_plan(self, obj, plan, patches):
                return {"CANCELLED"}
            i = plan.info
            line = (f"Subdivided {_plural(i['selected'], 'patch')} into 4 and {_plural(i['propagated'], 'neighbour')} "
                    f"into 2 (propagation): "
                    f"{i['patches_before']} -> {i['patches']} patches, +{i['points']} points, {i['dropped']} dropped"
                    + (f"; {i['manual']} sub-patches flagged manual (exact interiors off the Auto Interior rule)"
                       if i["manual"] else ""))
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_extrude_sides(bpy.types.Operator):
        """Add a patch on every selected open side"""
        bl_idname = "bz.extrude_sides"
        bl_label = "Add Quad"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            from . import ops
            if not _poll(cls, context):
                return False
            if ops._open_sides(context.active_object) == 0:
                cls.poll_message_set("Add Quad needs an open side: this creature is closed")
                return False
            return True

        def execute(self, context):
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            sides, why = selected_open_sides(obj, patches)
            if why:
                self.report({"ERROR"}, f"Add Quad: {why}")
                return {"CANCELLED"}
            plan = extrude_sides_plan(_verts(obj), patches, sides, _tables_of(obj))
            if not apply_plan(self, obj, plan, patches, select="points"):
                return {"CANCELLED"}
            i = plan.info
            line = f"Added {i['patches']} patches on {i['sides']} open sides: +{i['points']} points"
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_fill_hole(bpy.types.Operator):
        """Fill a hole with patches"""
        bl_idname = "bz.fill_hole"
        bl_label = "Fill Hole"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            from . import ops
            if not _poll(cls, context):
                return False
            if ops._open_sides(context.active_object) == 0:
                cls.poll_message_set("Fill Hole: this creature is closed")
                return False
            return True

        def execute(self, context):
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            plan = fill_plan(_verts(obj), patches, _tables_of(obj), _selected_points(obj), _rings_for_plan(obj))
            if not apply_plan(self, obj, plan, patches):
                return {"CANCELLED"}
            i = plan.info
            left = len(open_sides(store.patches(obj)))
            line = (f"Filled a {i['sides']}-side hole with {i['patches']} patch{'es' if i['patches'] != 1 else ''} "
                    f"(+{i['points']} points, {i['chords']} chords); {left} open sides left")
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_extrude_region(bpy.types.Operator):
        """Extrude the selected patches by Amount and scale them by Outline"""
        bl_idname = "bz.extrude_region"
        bl_label = "Extrude Region"
        bl_options = {"REGISTER", "UNDO"}

        amount: FloatProperty(name="Amount", default=0.0, unit="LENGTH", description="Lift along the mean normal")
        outline: FloatProperty(name="Outline", default=1.0, min=0.0, soft_max=3.0,
                               description="Scale of the lifted patches about their centre (bevel taper)")

        @classmethod
        def poll(cls, context):
            return _poll(cls, context)

        def execute(self, context):
            from . import store
            obj = context.active_object
            patches = store.patches(obj)
            rows = _rows_selected(obj, patches)
            plan = extrude_region_plan(_verts(obj), patches, rows, _tables_of(obj), amount=self.amount,
                                       outline=self.outline, rings=_rings_for_plan(obj))
            if not apply_plan(self, obj, plan, patches, select="points"):
                return {"CANCELLED"}
            i = plan.info
            line = (f"Extruded {i['patches']} patches by {self.amount:.4g} m (outline {self.outline:.3g}): {i['walls']} "
                    f"wall patches, {i['copies']} boundary points copied, +{i['points']} points")
            obj["bz_last_topo"] = line
            self.report({"INFO"}, line)
            return {"FINISHED"}

    class BZ_OT_extrude_drag(bpy.types.Macro):
        """Extrude the selected patches and drag them"""
        bl_idname = "bz.extrude_drag"
        bl_label = "Extrude Region (Drag)"
        bl_options = {"REGISTER", "UNDO"}

    class BZ_OT_add_quad_drag(bpy.types.Macro):
        """Add patches on the selected open sides and drag the new points"""
        bl_idname = "bz.add_quad_drag"
        bl_label = "Add Quad (Drag)"
        bl_options = {"REGISTER", "UNDO"}

    CLASSES = (BZ_OT_weld, BZ_OT_attach, BZ_OT_mirror_whole, BZ_OT_subdivide, BZ_OT_extrude_sides, BZ_OT_fill_hole,
               BZ_OT_extrude_region, BZ_OT_extrude_drag, BZ_OT_add_quad_drag)

    def register_macros() -> None:
        """The drag macros' steps (after the classes are registered): the tool, then Blender's translate."""
        if not getattr(BZ_OT_extrude_drag, "_defined", False):
            BZ_OT_extrude_drag.define("BZ_OT_extrude_region")
            BZ_OT_extrude_drag.define("TRANSFORM_OT_translate")
            BZ_OT_extrude_drag._defined = True
        if not getattr(BZ_OT_add_quad_drag, "_defined", False):
            BZ_OT_add_quad_drag.define("BZ_OT_extrude_sides")
            BZ_OT_add_quad_drag.define("TRANSFORM_OT_translate")
            BZ_OT_add_quad_drag._defined = True
else:
    CLASSES = ()

    def register_macros() -> None:
        pass
