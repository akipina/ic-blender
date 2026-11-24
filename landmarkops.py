"""Landmark-guided retarget (roadmap A3b): named Empties on the target mesh, matched to the donor creature's limb
tips / roots / head / tail / torso, drive a thin-plate-spline pre-warp of the donor's control net AND its rest bones
before the closest-point retarget of A3 (``retargetops``).

Why: a closest-point fit only sees displacement along the surface normal, so it cannot carry a donor limb across the
body -- the pillbug scan (3DInputs/Pillbug_ic/REPORT.md) left the beetle's legs bunched under the shell and its head
wedged into the scan's head although the scan-to-surface residual was 0.36 %. A handful of correspondences the user
places by hand (the foot tips, the head, the tail) fix that: the TPS moves whole limbs to where they belong, and the
closest-point fit then only has to settle the skin.

**Landmark names.** Donor landmarks are derived from the creature and named ``LM_<name>``:

* ``LM_<limb>`` -- the limb's TIP: the head of the last bone of its chain (the leaf of the limb's bone list farthest
  from its root: ``LM_LFLeg`` is the beetle's ``Front_endLeg_L``), except for the Head and Tail limbs, whose tip is
  the limb's surface point farthest along (Head) / against (Tail) the creature's heading (a feeler tip, the tail end);
* ``LM_<limb>_root`` -- the limb's ROOT: the head of its root bone (the bone of the limb's list whose parent is not in
  the list: ``Front_leg1_l``; skipped for a one-bone limb, whose tip is that bone);
* ``LM_Torso`` -- the centroid of the Torso limb's surface;
* ``LM_<bone>`` -- any rig bone by its current name (its head), for a correspondence the limb names don't offer.

``<limb>`` is the limb's standard suffix (``boner.STANDARD_LIMBS`` by class: Torso, Head, Tail, LFLeg, RFLeg, LRLeg,
RRLeg, LMLeg, RMLeg, LFArm, RFArm, LSWing, ...), else the last ``_`` token of its name. The heading is the XY direction
from the Torso's surface centroid to the Head's (the Tail's, reversed, without a Head). ``bz.landmarks_create`` adds a
collection of Empties with these names AT THE DONOR'S POSITIONS (so the user drags each onto the target); a Blender
``.001`` suffix on a name is ignored; a stub limb (no patches) gets its bone landmarks too (a landmark there moves the
bone only). The retarget reads every ``LM_*`` Empty of the chosen collection (or of the scene) back by name and refuses
fewer than 4, an unknown name, a name repeated, or a bone landmark whose bone is not on the rig.

**Pre-warp.** A 3-D thin-plate spline (kernel ``|r|``, affine part, a small regularisation ``reg`` x the landmarks'
bounding diagonal on the kernel diagonal) from the donor landmark positions to the user's (the target and its
landmarks first aligned into the creature's frame like A3's Scale to Target) is applied to every control point and to
every rest bone's head and tail (bones stay inside the warped skin), then the A3 closest-point retarget runs from that
start: rings pinned at their WARPED positions (default, ``warp_rings``: the combiner scales the changed rings) or at
their stock positions; symmetry as in A3 (the TOTAL displacement mirrored when the creature's net and the target are
X-symmetric); the fitted displacement of the points a bone owns is then added to the bone's warp (A3's Move Bones).
The report gives the landmarks used, the TPS residual at the landmarks (0 with no regularisation and non-degenerate
landmarks), the fit numbers as before and the per-limb coverage: the fraction of each limb's surface samples farther
than 2 % of the size from any target point -- the honest number that says which limbs still miss the target.

The bpy-free part (``tps_fit`` / ``TPS``, ``donor_landmarks``, ``coverage_by_limb``, ``landmark_retarget_pool``) is
what a pure test or script can run; the operators need ``bpy``.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ICParse.codecs import fit

try:
    from . import boner, core, fitops, retargetops
except ImportError:                      # imported top-level (a pure test puts blender_native/ on sys.path)
    import boner  # type: ignore[no-redef]
    import core  # type: ignore[no-redef]
    import fitops  # type: ignore[no-redef]
    import retargetops  # type: ignore[no-redef]

try:
    import bpy
    _Operator = bpy.types.Operator
except ImportError:                      # no Blender: the pipeline only
    bpy = None
    _Operator = object

PREFIX = "LM_"
MIN_LANDMARKS = 4          # an affine warp in 3-D needs 4 non-coplanar points; fewer can't place anything
TPS_REG = 1e-3             # default regularisation, x the landmarks' bounding diagonal (0 = exact interpolation)
COVERAGE_PCT = 2.0         # a surface sample farther than this % of the size from any target point "misses"
SURFACE_N = 7              # samples per patch side for the surface landmarks / coverage (fit_points' coverage_n)
_SUFFIX_RE = re.compile(r"\.\d{3}$")


class LandmarkError(ValueError):
    """A plain-language reason the landmark step was refused (reported as is)."""


# ------------------------------------------------------------------------------------------------ TPS

@dataclass
class TPS:
    """A 3-D thin-plate spline ``f(x) = A [1, x] + sum_i w_i |x - c_i|`` through the centres ``src``."""
    src: np.ndarray            # (K, 3) centres
    W: np.ndarray              # (K, 3) kernel weights
    A: np.ndarray              # (4, 3) affine part (row 0 = translation)
    residual: np.ndarray       # (K,) |f(src) - dst| (0 for exact interpolation)
    reg: float                 # the regularisation used (absolute, added to the kernel diagonal)

    def __call__(self, X) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64).reshape(-1, 3)
        if not len(X):
            return X.copy()
        out = self.A[0][None, :] + X @ self.A[1:]
        for start in range(0, len(X), 4096):                    # (chunk, K) distances: a 3,269 x 20 kernel is nothing,
            Xc = X[start:start + 4096]                          # a 40,000-point mesh at once would be 1.5 GB
            U = np.linalg.norm(Xc[:, None, :] - self.src[None, :, :], axis=2)
            out[start:start + 4096] += U @ self.W
        return out

    @property
    def max_residual(self) -> float:
        return float(self.residual.max()) if len(self.residual) else 0.0


def tps_fit(src, dst, reg: float = TPS_REG) -> TPS:
    """The thin-plate spline taking the ``src`` landmarks to ``dst`` (both (K, 3)), with ``reg`` x the bounding
    diagonal of ``src`` added to the kernel diagonal (0 = exact interpolation; a small value smooths a noisy placement
    and keeps the system well posed). Coplanar / collinear landmarks are solved in the least-squares sense (the affine
    part across the plane is then the identity's minimum-norm completion, which the caller reports)."""
    S = np.asarray(src, dtype=np.float64).reshape(-1, 3)
    D = np.asarray(dst, dtype=np.float64).reshape(-1, 3)
    K = len(S)
    if K != len(D):
        raise LandmarkError(f"{K} donor landmarks but {len(D)} target positions")
    if K < MIN_LANDMARKS:
        raise LandmarkError(f"{K} landmarks: at least {MIN_LANDMARKS} are needed to place a warp in 3-D")
    if not (np.isfinite(S).all() and np.isfinite(D).all()):
        raise LandmarkError("a landmark position is not finite")
    diag = fitops.bounding_diagonal(S)
    if diag <= 0:
        raise LandmarkError("the donor landmarks all coincide")
    lam = float(reg) * diag
    U = np.linalg.norm(S[:, None, :] - S[None, :, :], axis=2) + lam * np.eye(K)
    P = np.hstack([np.ones((K, 1)), S])
    M = np.zeros((K + 4, K + 4))
    M[:K, :K] = U
    M[:K, K:] = P
    M[K:, :K] = P.T
    rhs = np.zeros((K + 4, 3))
    rhs[:K] = D
    try:
        sol = np.linalg.solve(M, rhs)
        if not np.isfinite(sol).all():
            raise np.linalg.LinAlgError
    except np.linalg.LinAlgError:
        sol = np.linalg.lstsq(M, rhs, rcond=None)[0]
    t = TPS(src=S, W=sol[:K], A=sol[K:], residual=np.zeros(K), reg=lam)
    t.residual = np.linalg.norm(t(S) - D, axis=1)
    return t


def landmarks_coplanar(src, tol: float = 1e-6) -> bool:
    """True when the landmarks span less than 3 dimensions (the warp across their plane is undetermined)."""
    S = np.asarray(src, dtype=np.float64).reshape(-1, 3)
    if len(S) < 4:
        return True
    C = S - S.mean(0)
    s = np.linalg.svd(C, compute_uv=False)
    return bool(s[-1] <= tol * max(s[0], 1e-300))


# ------------------------------------------------------------------------------------------------ donor landmarks

@dataclass
class Landmark:
    name: str                  # without the LM_ prefix: "LFLeg", "LFLeg_root", "Head", "Torso", a bone name
    position: np.ndarray       # (3,) in the creature's space
    kind: str                  # "tip" | "root" | "centre" | "bone"
    limb: int = -1             # the limb index it belongs to (-1: a bone landmark)
    bone: str = ""             # the rig bone it sits on ("" for a surface landmark)

    @property
    def empty_name(self) -> str:
        return PREFIX + self.name


@dataclass
class DonorLandmarks:
    landmarks: List[Landmark]
    heading: np.ndarray                        # unit XY heading of the creature (torso -> head)
    notes: List[str] = field(default_factory=list)   # limbs without a landmark and why

    def by_name(self) -> Dict[str, Landmark]:
        return {l.name: l for l in self.landmarks}

    @property
    def positions(self) -> np.ndarray:
        return np.array([l.position for l in self.landmarks], dtype=np.float64).reshape(-1, 3)


def limb_suffix(name: str, limb_id: int) -> str:
    """The limb's standard suffix by class (``boner.STANDARD_LIMBS``), else the last ``_`` token of its name."""
    cls = boner.class_of(int(limb_id))
    for s in boner.STANDARD_LIMBS:
        if boner.class_of(s.limb_id) == cls:
            return s.suffix
    tok = str(name).rsplit("_", 1)[-1].strip()
    return tok or str(name)


def limb_chain(bones: Sequence[str], parents: Dict[str, Optional[str]]):
    """(roots, leaves) of a limb's bone list under the rig's ``parents``: a root's parent is not in the list, a leaf has
    no child in the list. Names are matched case-insensitively (stock lists mix cases: ``Rear_leg1_L`` / ``rear_leg1_r``)."""
    low = {str(b).lower(): str(b) for b in bones}
    parent_of = {str(k).lower(): (None if v is None else str(v).lower()) for k, v in parents.items()}
    inlist = set(low)
    roots = [low[b] for b in low if b in parent_of and parent_of[b] not in inlist]
    has_child = {parent_of[b] for b in low if b in parent_of and parent_of[b] in inlist}
    leaves = [low[b] for b in low if b in parent_of and b not in has_child]
    return roots, leaves


def surface_samples(pool, patches, n: int = SURFACE_N) -> np.ndarray:
    """(Np * n * n, 3) surface samples in patch order (fit_points' coverage grid), so a sample's patch is index // n^2."""
    pool = np.asarray(pool, dtype=np.float64)
    patches = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    Np = len(patches)
    gu, gv = fit.sample_grid(n)
    return fit.evaluate(pool, patches, np.repeat(np.arange(Np), n * n), np.tile(gu, Np), np.tile(gv, Np))


def donor_landmarks(pool, patches, patch_limb, limbs, bone_heads, bone_parents, n: int = SURFACE_N) -> DonorLandmarks:
    """The creature's landmarks (see the module docstring). ``limbs`` = [(name, limb_id, [rig bone names])] in row
    order, ``patch_limb`` the limb index per patch, ``bone_heads`` {rig bone name: (3,) head in the creature's space},
    ``bone_parents`` {rig bone name: parent name or None}. Raises LandmarkError when no heading can be told (no Head
    and no Tail limb with patches)."""
    pool = np.asarray(pool, dtype=np.float64)
    patches = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pl = np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    S = surface_samples(pool, patches, n)
    K = n * n
    heads_low = {str(k).lower(): (str(k), np.asarray(v, dtype=np.float64).reshape(3)) for k, v in bone_heads.items()}

    def head_of(name):
        h = heads_low.get(str(name).lower())
        return h

    def samples_of(li):
        idx = np.nonzero(pl == li)[0]
        return S[(idx[:, None] * K + np.arange(K)[None, :]).ravel()] if len(idx) else S[:0]

    classes = {}
    for li, (name, lid, _b) in enumerate(limbs):
        classes.setdefault(boner.class_of(int(lid)), li)
    torso_i = classes.get(boner.TORSO_CLASS)
    head_i = classes.get(boner.class_of(boner.STANDARD_BY_SUFFIX["Head"].limb_id))
    tail_i = classes.get(boner.class_of(boner.STANDARD_BY_SUFFIX["Tail"].limb_id))
    torso_S = samples_of(torso_i) if torso_i is not None else S[:0]
    head_S = samples_of(head_i) if head_i is not None else S[:0]
    tail_S = samples_of(tail_i) if tail_i is not None else S[:0]
    centre = torso_S.mean(0) if len(torso_S) else S.mean(0)
    if len(head_S):
        heading = head_S.mean(0) - centre
    elif len(tail_S):
        heading = centre - tail_S.mean(0)
    else:
        raise LandmarkError("the creature has neither a Head nor a Tail limb with patches, so its heading can't be "
                            "told: give it one (Boner > Limbs) before deriving landmarks")
    heading[2] = 0.0
    ln = float(np.linalg.norm(heading))
    if ln <= 0:
        raise LandmarkError("the Head sits right above the Torso's centre: the heading can't be told")
    heading /= ln
    out: List[Landmark] = []
    notes: List[str] = []
    used = set()

    def unique(base):
        s, k = base, 2
        while s in used:
            s = f"{base}{k}"; k += 1
        used.add(s)
        return s

    for li, (name, lid, bones) in enumerate(limbs):
        suffix = unique(limb_suffix(name, lid))
        cls = boner.class_of(int(lid))
        lS = samples_of(li)
        tip_done = False
        if li == torso_i:
            out.append(Landmark(suffix, centre.copy(), "centre", li))
            tip_done = True
        elif li == head_i and len(lS):
            out.append(Landmark(suffix, lS[int(np.argmax(lS @ heading))].copy(), "tip", li))
            tip_done = True
        elif li == tail_i and len(lS):
            out.append(Landmark(suffix, lS[int(np.argmin(lS @ heading))].copy(), "tip", li))
            tip_done = True
        roots, leaves = limb_chain(bones, bone_parents)
        roots = [r for r in roots if head_of(r) is not None]
        leaves = [l for l in leaves if head_of(l) is not None]
        if not bones:
            if not tip_done:
                notes.append(f"{name}: no bones and no Head / Tail / Torso rule, no landmark")
            continue
        if not roots:
            notes.append(f"{name}: none of its bones is on the rig, no bone landmark")
            continue
        root = roots[0]
        if len(roots) > 1:
            notes.append(f"{name}: {len(roots)} root bones ({', '.join(roots)}), the first is its root landmark")
        rname, rpos = head_of(root)
        if not tip_done:
            far = max(leaves, key=lambda l: float(np.linalg.norm(head_of(l)[1] - rpos))) if leaves else root
            fname, fpos = head_of(far)
            out.append(Landmark(suffix, fpos.copy(), "tip", li, fname))
            if fname.lower() == rname.lower():
                continue                                   # a one-bone limb: its tip is its root
        elif cls == boner.TORSO_CLASS:
            continue                                       # the torso's centre is enough
        out.append(Landmark(suffix + "_root", rpos.copy(), "root", li, rname))
    return DonorLandmarks(landmarks=out, heading=heading, notes=notes)


def strip_name(empty_name: str) -> Optional[str]:
    """``LM_LFLeg.001`` -> ``LFLeg``; None when the name has no ``LM_`` prefix."""
    s = str(empty_name)
    if not s.startswith(PREFIX):
        return None
    return _SUFFIX_RE.sub("", s[len(PREFIX):])


def resolve_landmarks(named: Dict[str, np.ndarray], donor: DonorLandmarks, bone_heads: Dict[str, np.ndarray],
                      file_bones: Sequence[str] = ()) -> Tuple[List[Landmark], np.ndarray]:
    """Match the user's ``{name: target position}`` (names without the prefix) to the donor's landmarks or rig bones:
    (the donor landmarks used, in the same order, (K, 3) target positions). Refuses an unknown name, a bone that is not
    on the rig (``file_bones``: the source's bone names, so a deleted bone is named as such) and fewer than 4."""
    by = donor.by_name()
    by_low = {k.lower(): v for k, v in by.items()}
    heads_low = {str(k).lower(): (str(k), np.asarray(v, dtype=np.float64).reshape(3)) for k, v in bone_heads.items()}
    file_low = {str(b).lower() for b in file_bones}
    used, pos = [], []
    for name, p in named.items():
        p = np.asarray(p, dtype=np.float64).reshape(3)
        if not np.isfinite(p).all():
            raise LandmarkError(f"landmark {PREFIX}{name} has a non-finite position")
        lm = by.get(name) or by_low.get(name.lower())
        if lm is None:
            h = heads_low.get(name.lower())
            if h is not None:
                lm = Landmark(h[0], h[1].copy(), "bone", -1, h[0])
            elif name.lower() in file_low:
                raise LandmarkError(f"landmark {PREFIX}{name} names bone '{name}', which is not on the rig (deleted or "
                                    "renamed): use the rig's current bone name or remove the Empty")
            else:
                known = ", ".join(PREFIX + l.name for l in donor.landmarks)
                raise LandmarkError(f"landmark {PREFIX}{name} is neither a donor landmark nor a rig bone; the donor's "
                                    f"landmarks are {known}, and any rig bone by name")
        used.append(lm); pos.append(p)
    if len(used) < MIN_LANDMARKS:
        raise LandmarkError(f"{len(used)} landmark{'s' if len(used) != 1 else ''} found: at least {MIN_LANDMARKS} "
                            f"(head, tail and two feet, say) are needed to place a warp in 3-D")
    return used, np.array(pos, dtype=np.float64).reshape(-1, 3)


# ------------------------------------------------------------------------------------------------ coverage

@dataclass
class LimbCoverage:
    name: str
    samples: int
    far: int                   # samples farther than the threshold from any target point
    max_pct: float

    @property
    def fraction(self) -> float:
        return self.far / self.samples if self.samples else 0.0


def coverage_by_limb(coverage, patch_limb, limb_names, diag: float, pct: float = COVERAGE_PCT,
                     n: int = SURFACE_N) -> List[LimbCoverage]:
    """Per limb (row order, then "no limb" when any patch is unassigned): how many of its surface samples (``n`` x ``n``
    per patch, patch order -- ``fit_points``' coverage) are farther than ``pct`` % of ``diag`` from any target point."""
    cov = np.asarray(coverage, dtype=np.float64).reshape(-1)
    pl = np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    K = n * n
    if len(cov) != len(pl) * K:
        raise LandmarkError(f"{len(cov)} coverage samples for {len(pl)} patches at {n} x {n}")
    per = np.repeat(pl, K)
    thr = pct / 100.0 * diag
    out = []
    names = list(limb_names) + ["no limb"]
    for li, name in enumerate(names):
        key = li if li < len(limb_names) else -1
        m = per == key
        if not m.any():
            if key == -1:
                continue
            out.append(LimbCoverage(str(name), 0, 0, 0.0))
            continue
        out.append(LimbCoverage(str(name), int(m.sum()), int((cov[m] > thr).sum()), float(100 * cov[m].max() / diag)))
    return out


def coverage_text(rows: List[LimbCoverage], pct: float = COVERAGE_PCT) -> str:
    """``coverage by limb (surface samples > 2 % from any target point): Head 25 % (1686 of 6615, max 10.5 %), ...``"""
    parts = [f"{r.name} {100 * r.fraction:.0f} % ({r.far} of {r.samples}, max {r.max_pct:.1f} %)" for r in rows
             if r.samples]
    return f"coverage by limb (surface samples > {pct:g} % from any target point): " + "; ".join(parts)


# ------------------------------------------------------------------------------------------------ pipeline

@dataclass
class LandmarkRetarget:
    tps: TPS
    landmarks: List[Landmark]
    target_landmarks: np.ndarray           # (K, 3) in the creature's frame (aligned)
    pool0: np.ndarray
    pool_warped: np.ndarray                # after the TPS (rings put back when not warped)
    pool: np.ndarray                       # after the fit (symmetrized, snapped)
    fit: retargetops.Retarget              # the closest-point retarget from the warped start
    before: fitops.FitStats                # target-to-surface distances of the UNWARPED creature
    moved: np.ndarray                      # (N,) bool: control points that changed against pool0
    warped_points: int                     # control points the TPS moved (past the snap)
    limb_coverage: List[LimbCoverage]
    scale: float
    shift: np.ndarray
    coplanar: bool
    seconds: float = 0.0

    @property
    def after(self) -> fitops.FitStats:
        return self.fit.after


def landmark_retarget_pool(pool0, patches, points, donor_lm, target_lm, patch_limb=None, limb_names=(),
                           normals=None, iters: int = 10, smooth: float = 0.0, fixed=None, warp_rings: bool = True,
                           symmetric: bool = True, scale_to_target: bool = True, reg: float = TPS_REG,
                           landmarks: Optional[List[Landmark]] = None) -> LandmarkRetarget:
    """The whole pipeline: align the target and its landmarks into the creature's frame -> TPS from ``donor_lm`` to
    ``target_lm`` applied to the pool (``fixed`` = ring indices: put back to ``pool0`` unless ``warp_rings``) ->
    ``retargetops.retarget_pool`` from the warped pool with the rings pinned there, symmetry judged and mirrored
    against ``pool0`` -> the metrics. ``patch_limb`` / ``limb_names`` give the per-limb coverage."""
    P0 = np.asarray(pool0, dtype=np.float64).reshape(-1, 3)
    patches = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        raise retargetops.RetargetError("the target has no vertices")
    if not np.isfinite(pts).all():
        raise retargetops.RetargetError("the target has non-finite (NaN/inf) vertex positions")
    L0 = np.asarray(donor_lm, dtype=np.float64).reshape(-1, 3)
    L1 = np.asarray(target_lm, dtype=np.float64).reshape(-1, 3)
    if len(L0) != len(L1):
        raise LandmarkError(f"{len(L0)} donor landmarks but {len(L1)} target positions")
    t0 = time.perf_counter()
    scale, shift = 1.0, np.zeros(3)
    if scale_to_target:
        pts, scale, shift = retargetops.align_target(pts, P0, patches)
        L1 = L1 * scale + shift
    diag = fitops.bounding_diagonal(pts)
    if diag <= 0:
        raise retargetops.RetargetError("the target's vertices all coincide")
    tps = tps_fit(L0, L1, reg=reg)
    coplanar = landmarks_coplanar(L0)
    Pw = tps(P0)
    small = np.linalg.norm(Pw - P0, axis=1) <= retargetops.SNAP * diag
    Pw[small] = P0[small]
    N = len(P0)
    idx = np.zeros(0, dtype=np.int64)
    if fixed is not None:
        idx = np.asarray(sorted({int(i) for i in fixed}), dtype=np.int64)
        if len(idx) and (idx.min() < 0 or idx.max() >= N):
            raise retargetops.RetargetError(f"a pinned index is outside the {N}-point pool")
        if not warp_rings:
            Pw[idx] = P0[idx]
    _, _, _, d0 = fit.project(pts, P0, patches, n=9, candidates=retargetops.CANDIDATES)
    before = fitops.stats(d0, diag)
    r = retargetops.retarget_pool(Pw, patches, pts, normals=normals, iters=iters, smooth=smooth,
                                  fixed=idx if len(idx) else None, symmetric=symmetric, scale_to_target=False,
                                  sym_base=P0)
    pool = r.pool
    moved = np.linalg.norm(pool - P0, axis=1) > retargetops.SNAP * diag
    pool = np.where(moved[:, None], pool, P0)
    if r.coverage_raw is None or patch_limb is None:
        limb_cov = []
    else:
        limb_cov = coverage_by_limb(r.coverage_raw, patch_limb, limb_names, diag)
    return LandmarkRetarget(tps=tps, landmarks=list(landmarks or []), target_landmarks=L1, pool0=P0, pool_warped=Pw,
                            pool=pool, fit=r, before=before, moved=moved, warped_points=int((~small).sum()),
                            limb_coverage=limb_cov, scale=scale, shift=shift, coplanar=coplanar,
                            seconds=time.perf_counter() - t0)


def report_line(creature: str, mesh: str, n_patches: int, r: LandmarkRetarget, warp_rings: bool,
                bones_text: str) -> str:
    """The operator report (also ``bz_last_landmark_retarget``)."""
    k = len(r.target_landmarks)
    names = ", ".join(l.name for l in r.landmarks) if r.landmarks else f"{k}"
    rings = (f"rings warped ({len(r.fit.fixed)} points follow the warp, then pinned: the combiner will scale them and "
             "may warn)" if warp_rings else f"rings pinned ({len(r.fit.fixed)} points at the stock geometry)")
    aligned = (f"target scaled x{r.scale:.4f} into the creature's frame" if r.scale != 1.0 or np.abs(r.shift).max() > 0
               else "target taken as is")
    cov = coverage_text(r.limb_coverage) if r.limb_coverage else "coverage by limb: no limb table"
    return (f"Landmark-retargeted {creature} to {mesh}: {k} landmarks ({names}), TPS residual at the landmarks max "
            f"{1000 * r.tps.max_residual:.2f} mm; {n_patches} patches, {r.after.text()}; before the warp rms "
            f"{r.before.rms_pct:.2f} %, after the warp rms {r.fit.before.rms_pct:.2f} %; {rings}; {r.fit.symmetry_text}; "
            f"{bones_text}; {aligned}; {int(r.moved.sum())} control points moved ({r.warped_points} by the warp); "
            f"{cov}; in {r.seconds:.1f} s")


# ------------------------------------------------------------------------------------------------ Blender

if bpy is not None:
    from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
    from mathutils import Vector
    from . import edittools, limbops, ops, rig, sculptops, store

    def _collection_search(self, context, edit_text):
        return [c.name for c in bpy.data.collections
                if any(o.name.startswith(PREFIX) for o in c.objects)]


def _bone_heads(obj, arm) -> Tuple[Dict[str, np.ndarray], Dict[str, Optional[str]]]:
    """{rig bone name: head in the creature's local space}, {name: parent name} (rest pose; the rig shares the
    creature's frame, so armature-local = creature-local when their world matrices agree)."""
    M = np.array(obj.matrix_world.inverted() @ arm.matrix_world, dtype=np.float64)
    heads, parents = {}, {}
    for b in arm.data.bones:
        h = np.array(b.head_local, dtype=np.float64)
        heads[b.name] = h @ M[:3, :3].T + M[:3, 3]
        parents[b.name] = b.parent.name if b.parent is not None else None
    return heads, parents


def _limb_rows_for(obj) -> List[Tuple[str, int, List[str]]]:
    """[(limb name, limb id, [rig bone names])] from the limb table, file names mapped to the rig's current names."""
    table = limbops.limb_table(obj, placed=True)
    if table.error:
        raise LandmarkError(f"the limb table can't be read: {table.error}")
    file_names = [str(n).lower() for n in store.bone_names(obj)]
    rig_names = store.bone_rig_names(obj)
    out = []
    for row, bones in zip(table.rows, table.bones):
        names = []
        for b in bones:
            try:
                i = file_names.index(str(b).lower())
                names.append(str(rig_names[i]) if i < len(rig_names) and rig_names[i] else str(b))
            except ValueError:
                names.append(str(b))                       # an added bone is listed by its rig name already
        out.append((str(row.name), int(row.limb_id), names))
    return out


def creature_landmarks(obj) -> DonorLandmarks:
    """The active creature's donor landmarks (creature-local space); raises LandmarkError with the reason."""
    arm = store.rig(obj)
    if arm is None:
        raise LandmarkError(f"{obj.name} has no rig: landmarks need the limb bones")
    heads, parents = _bone_heads(obj, arm)
    return donor_landmarks(edittools._verts(obj), store.patches(obj), store.patch_limb(obj), _limb_rows_for(obj),
                           heads, parents)


def scene_landmarks(context, obj, collection: str = "") -> Dict[str, np.ndarray]:
    """{name: creature-local position} of every ``LM_*`` object in ``collection`` (else the whole scene), a repeated
    name refused."""
    if collection:
        col = bpy.data.collections.get(collection)
        if col is None:
            raise LandmarkError(f"no collection called '{collection}'")
        objs = list(col.all_objects)
    else:
        objs = list(context.scene.objects)
    inv = obj.matrix_world.inverted()
    out, where = {}, {}
    for o in objs:
        name = strip_name(o.name)
        if name is None or o is obj:
            continue
        if name in where:
            raise LandmarkError(f"landmark {PREFIX}{name} is placed twice ({where[name]} and {o.name}): keep one")
        where[name] = o.name
        p = inv @ o.matrix_world.translation
        out[name] = np.array([p.x, p.y, p.z], dtype=np.float64)
    return out


class BZ_OT_landmarks_create(_Operator):
    """Add a collection of Empties named after this creature's landmarks (LM_<limb> tips, LM_<limb>_root, LM_Head,
    LM_Tail, LM_Torso) at the creature's own positions: drag each onto the target mesh, then Landmark Retarget"""
    bl_idname = "bz.landmarks_create"
    bl_label = "Create Landmarks"
    bl_options = {"REGISTER", "UNDO"}

    if bpy is not None:
        roots: BoolProperty(name="Limb Roots", default=True,
                            description="Also add the LM_<limb>_root Empties (the head of each limb's root bone). Off: "
                                        "tips, head, tail and torso only (the roots are then interpolated by the warp)")
        size: FloatProperty(name="Display Size", default=0.0, min=0.0, soft_max=1.0, precision=3,
                            description="Empty display size in Blender units (0: 2 % of the creature's size)")

    @classmethod
    def poll(cls, context):
        return ops._guard_poll(cls, context)

    def execute(self, context):
        obj = ops._active_native(context)
        if obj is None:
            self.report({"ERROR"}, "Select a native patch creature")
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        try:
            dl = creature_landmarks(obj)
        except LandmarkError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"landmarks could not be derived: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        lms = [l for l in dl.landmarks if self.roots or l.kind != "root"]
        if not lms:
            self.report({"ERROR"}, f"{obj.name} has no landmark: " + "; ".join(dl.notes))
            return {"CANCELLED"}
        name = f"Landmarks_{obj.name}"
        col = bpy.data.collections.get(name)
        replaced = 0
        if col is None:
            col = bpy.data.collections.new(name)
            context.scene.collection.children.link(col)
        else:
            for o in list(col.objects):
                if o.name.startswith(PREFIX):
                    bpy.data.objects.remove(o, do_unlink=True); replaced += 1
        S = surface_samples(edittools._verts(obj), store.patches(obj), n=3)
        size = self.size if self.size > 0 else 0.02 * fitops.bounding_diagonal(S)
        for l in lms:
            old = bpy.data.objects.get(l.empty_name)
            if old is not None and old.name not in col.objects:
                bpy.data.objects.remove(old, do_unlink=True); replaced += 1
            e = bpy.data.objects.new(l.empty_name, None)
            e.empty_display_type = "SPHERE" if l.kind in ("tip", "centre") else "PLAIN_AXES"
            e.empty_display_size = size
            e.location = obj.matrix_world @ Vector(l.position.tolist())
            e["bz_landmark_of"] = obj.name
            e["bz_landmark_kind"] = l.kind
            if l.bone:
                e["bz_landmark_bone"] = l.bone
            col.objects.link(e)
        heading = f"({dl.heading[0]:+.2f}, {dl.heading[1]:+.2f})"
        names = ", ".join(l.name for l in lms)
        obj["bz_landmarks"] = [l.name for l in lms]
        for n in dl.notes:
            self.report({"WARNING"}, n)
        self.report({"INFO"}, f"{len(lms)} landmarks in collection {name} at {obj.name}'s positions (heading {heading}): "
                              f"{names}. Drag each Empty onto the target mesh, then Landmark Retarget"
                              + (f"; {replaced} earlier Empties replaced" if replaced else ""))
        return {"FINISHED"}


class BZ_OT_landmark_retarget(_Operator):
    """Retarget this creature to a mesh guided by landmarks: the LM_* Empties (placed on the target) matched to the
    creature's limb tips / roots / head / tail / torso drive a thin-plate-spline pre-warp of the control net and the
    rest bones, then the closest-point retarget settles the skin (rings warped or pinned; undoable)"""
    bl_idname = "bz.landmark_retarget"
    bl_label = "Landmark Retarget"
    bl_options = {"REGISTER", "UNDO"}

    if bpy is not None:
        target: StringProperty(name="Target Mesh", default="", search=retargetops._mesh_search,
                               description="The mesh object whose shape the creature takes (one closed skin)")
        collection: StringProperty(name="Landmarks", default="", search=_collection_search,
                                   description="The collection holding the LM_* Empties (empty: every LM_* object in "
                                               "the scene)")
        iterations: IntProperty(name="Iterations", default=10, min=1, max=50,
                                description="Project-and-solve rounds of the closest-point fit after the warp")
        smooth: FloatProperty(name="Seam Smoothness", default=0.0, min=0.0, soft_max=1.0, precision=3,
                              description="Weight of the soft C1 term across patch seams (0 keeps stock seams)")
        regularisation: FloatProperty(name="Warp Smoothing", default=TPS_REG, min=0.0, soft_max=0.1, precision=4,
                                      description="Thin-plate-spline regularisation as a fraction of the landmarks' "
                                                  "extent: 0 passes exactly through every landmark, a small value "
                                                  "smooths a rough placement (the residual at the landmarks is "
                                                  "reported)")
        warp_rings: BoolProperty(name="Warp Rings", default=True,
                                 description="The connection rings follow the warp and are pinned there (the combiner "
                                             "scales the changed rings and may warn about them). Off: every ring stays "
                                             "exactly at the stock geometry, so the sockets don't move with the limbs")
        symmetry: BoolProperty(name="Keep Symmetry", default=True,
                               description="When the creature's control net and the target are both X-symmetric, "
                                           "mirror the total displacement exactly")
        scale_to_target: BoolProperty(name="Scale to Target", default=True,
                                      description="First align the target (and the landmarks on it) into the "
                                                  "creature's frame: centred on the creature's surface box, scaled "
                                                  "uniformly by the bounding-diagonal ratio")
        move_bones: BoolProperty(name="Move Bones", default=True,
                                 description="Warp each rest bone's head and tail with the landmarks, then add the "
                                             "mean fitted displacement of the points it owns (Export writes the moved "
                                             "bones as overrides). Off: bones keep their bind pose")

    @classmethod
    def poll(cls, context):
        return ops._guard_poll(cls, context)

    def execute(self, context):
        obj = ops._active_native(context)
        if obj is None:
            self.report({"ERROR"}, "Select a native patch creature to retarget")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        got = retargetops.resolve_target(self, context, obj, self.target)
        if got is None:
            return {"CANCELLED"}
        tgt, me_eval = got
        arm = store.rig(obj)
        t_all = time.perf_counter()
        try:
            dl = creature_landmarks(obj)
            named = scene_landmarks(context, obj, self.collection)
            heads, _parents = _bone_heads(obj, arm)
            used, L1 = resolve_landmarks(named, dl, heads, store.bone_names(obj))
        except LandmarkError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"the landmarks could not be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        pool0 = edittools._verts(obj)
        patches = store.patches(obj)
        pts, nrm = retargetops._target_arrays(obj, tgt, me_eval)
        rings = retargetops.ring_points(store.connections(obj), len(pool0))
        L0 = np.array([l.position for l in used], dtype=np.float64).reshape(-1, 3)
        try:
            r = landmark_retarget_pool(pool0, patches, pts, L0, L1, patch_limb=store.patch_limb(obj),
                                       limb_names=store.limb_names(obj), normals=nrm, iters=self.iterations,
                                       smooth=self.smooth, fixed=rings, warp_rings=self.warp_rings,
                                       symmetric=self.symmetry, scale_to_target=self.scale_to_target,
                                       reg=self.regularisation, landmarks=used)
        except (LandmarkError, retargetops.RetargetError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Landmark retarget failed: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        bones_text = "bones not moved (no rig)" if arm is None else "bones kept at their bind pose"
        n_bones = 0
        bone_max = bone_turn = 0.0
        if r.moved.any():
            if not ops._begin(self, obj):
                return {"CANCELLED"}
            mode = obj.mode
            if mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
            sculptops.write_pool(obj, r.pool)
            if self.move_bones and arm is not None:
                try:
                    M = np.array(obj.matrix_world.inverted() @ arm.matrix_world, dtype=np.float64)
                    Mi = np.linalg.inv(M)

                    def warp(X):                     # armature-local -> creature-local -> TPS -> armature-local
                        X = np.asarray(X, dtype=np.float64).reshape(-1, 3)
                        Y = r.tps(X @ M[:3, :3].T + M[:3, 3])
                        return Y @ Mi[:3, :3].T + Mi[:3, 3]
                    n_bones, bone_max, inherited, owning, bone_turn = retargetops.move_rig_bones(
                        context, obj, arm, r.pool - r.pool_warped, warp=warp)
                    bones_text = (f"bones: {n_bones} moved (max {1000 * bone_max:.1f} mm; warped with the landmarks, "
                                  f"turned by up to {np.degrees(bone_turn):.1f} deg; {owning} own points, {inherited} "
                                  "followed their parent for the fit's share)")
                except Exception as e:
                    self.report({"WARNING"}, f"the bones were not moved ({type(e).__name__}: {e}); the control points "
                                             "were retargeted, the rig keeps its bind pose")
                    bones_text = "bones kept at their bind pose (moving them failed)"
            if mode == "EDIT":
                context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode="EDIT")
        r.seconds = time.perf_counter() - t_all
        line = report_line(obj.name, tgt.name, len(patches), r, self.warp_rings, bones_text)
        obj["bz_last_landmark_retarget"] = line
        obj["bz_retarget_source"] = tgt.name      # Transfer Texture's and Trim Stray Surface's default source
        obj["bz_last_landmark_retarget_stats"] = {
            "landmarks": [l.name for l in used], "tps_max_residual": r.tps.max_residual,
            "tps_residual": [float(x) for x in r.tps.residual], "regularisation": float(self.regularisation),
            "coplanar": bool(r.coplanar),
            "rms": r.after.rms, "p95": r.after.p95, "max": r.after.max, "diag": r.after.diag,
            "rms_before": r.before.rms, "rms_warped": r.fit.before.rms, "moved_points": int(r.moved.sum()),
            "warped_points": int(r.warped_points), "ring_points": int(len(rings)), "rings_warped": bool(self.warp_rings),
            "symmetrized": bool(r.fit.symmetrized), "paired": int(r.fit.paired), "on_plane": int(r.fit.on_plane),
            "scale": float(r.scale), "bones_moved": int(n_bones), "bone_max_move": float(bone_max),
            "bone_max_turn": float(bone_turn), "coverage_max": r.fit.coverage.max,
            "limb_coverage": {c.name: [c.samples, c.far, c.max_pct] for c in r.limb_coverage},
            "seconds": r.seconds}
        if not r.moved.any():
            self.report({"INFO"}, f"{obj.name} already fits {tgt.name} at these landmarks: no control point moved "
                                  f"({r.after.text()}; TPS residual max {1000 * r.tps.max_residual:.2f} mm)")
            return {"FINISHED"}
        for n in dl.notes:
            self.report({"WARNING"}, n)
        if r.coplanar:
            self.report({"WARNING"}, "the landmarks are coplanar: the warp across their plane is undetermined (kept "
                                     "affine there); add a landmark off the plane (a foot and the back, say)")
        if r.tps.max_residual > 0.01 * r.after.diag:
            self.report({"WARNING"}, f"the warp misses a landmark by {1000 * r.tps.max_residual:.1f} mm (Warp "
                                     f"Smoothing {self.regularisation:g}): lower it for an exact pass")
        if not ops._is_identity(tgt.matrix_world):
            self.report({"WARNING"}, f"{tgt.name} has an object transform: its world shape was used (transformed into "
                                     f"{obj.name}'s space)")
        if not ops._is_identity(obj.matrix_world):
            self.report({"WARNING"}, f"{obj.name} has an object transform: the fit is in its local space, and Export "
                                     "refuses a creature with a transform (apply or clear it)")
        if self.warp_rings and len(rings):
            self.report({"WARNING"}, f"the {len(rings)} connection-ring control points moved with the warp: the combiner "
                                     "will scale the changed rings and may warn about them (Validate's combine tier "
                                     "shows it); Warp Rings off keeps the stock ring geometry")
        far = [c for c in r.limb_coverage if c.samples and c.fraction > 0.1]
        if far:
            self.report({"WARNING"}, "limbs still off the target (more than 10 % of their surface samples > "
                                     f"{COVERAGE_PCT:g} % of the size from any target point): "
                                     + ", ".join(f"{c.name} {100 * c.fraction:.0f} %" for c in far)
                                     + " -- add or move landmarks there")
        if not self.move_bones and arm is not None:
            self.report({"INFO"}, "the bones keep their bind pose (Move Bones warps them with the landmarks)")
        self.report({"INFO"}, line)
        return {"FINISHED"}


CLASSES = (BZ_OT_landmarks_create, BZ_OT_landmark_retarget) if bpy is not None else ()
