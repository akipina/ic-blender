"""Trim Stray Surface (roadmap A5): the patches of a fitted / retargeted creature that are surface the TARGET
DOES NOT HAVE.

The user's observation that this answers, on the pillbug (``3DInputs/Pillbug_ic/REPORT.md``): *"the legs of the
pillbug almost seem webbed in areas, filling in space that was not filled in the input model"*. The reason is in
the solver: a closest-point fit (``ICParse.codecs.fit``, A1 / A3 / A3b) only minimises TARGET -> SURFACE distance.
Donor surface with no target under it -- a beetle leg tube where the scan has no leg -- is pulled towards whatever
target is nearest (the belly, the neighbouring leg), and the fairness + C1 seam terms stop it collapsing to a
point, so it spreads into sheets: webbing. Nothing in the fit measures it, because SURFACE -> TARGET is the other
direction (``fit_points`` reports it as ``coverage``, and A3b's per-limb coverage names the limbs, but neither
removes anything).

This module measures that direction per patch and offers the three honest answers:

* **Select** (the default): the stray patches' faces are selected and nothing is changed -- the modder decides.
* **Delete**: the stray patches are deleted through the one delete path (``patchsel.delete_plan`` /
  ``bz.delete_patch``: tables, rings, pool compaction) and every hole left behind that ``bz.fill_hole`` (B7) can
  take -- a loop of an even number of sides, at least 4 -- is closed, so the creature stays watertight and the game
  still loads it. A patch carrying a connection ring is refused by name, as Delete Patch does. It is one undo step
  per edit (the delete, then one per hole) and the report says how many.
* **Collapse**: the stray patches' control points are moved onto their nearest target point, so the sheet folds
  flat against the body instead of webbing. The patch table is untouched, so the mesh stays exactly as watertight
  as it was -- this is what you want for a limb the target does not have (a beetle leg on a pillbug): the geometry
  stays, skinned and combinable, lying on the body rather than hanging in the air. ``keep_shared`` (the operator's
  **Keep the Shared Edge**, default on) pins the points a patch that IS on the target also uses, so nothing that
  fits the target moves; off, those fold too and the patches sharing them follow.

Stray-ness is measured, per patch, as the fraction of its surface samples (``SURFACE_N`` x ``SURFACE_N``, the grid
``fit.fit_points`` computes its own coverage on) that are farther than ``distance`` % of the creature's size from
the nearest target point. The nearest-point search is ``fit._nearest`` and the surface sampling is ``fit.evaluate``
on ``fit.sample_grid`` -- the same helpers the fit uses, not a second implementation.

The bpy-free part (``stray_patches``, ``collapse_pool`` and their helpers) is what a pure test can run; the
operator below needs ``bpy``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import numpy as np

from ICParse.codecs import fit

try:
    from . import core, fitops, retargetops
except ImportError:                      # imported top-level (a pure test puts blender_native/ on sys.path)
    import core  # type: ignore[no-redef]
    import fitops  # type: ignore[no-redef]
    import retargetops  # type: ignore[no-redef]

try:
    import bpy
    _Operator = bpy.types.Operator
except ImportError:                      # no Blender: the measurements only
    bpy = None
    _Operator = object

SURFACE_N = 7          # samples per patch per direction: fit_points' own coverage density (49 per patch)
STRAY_PCT = 2.0        # % of the creature's size: a surface sample farther than this from the target misses it
                       # (the threshold A3b's per-limb coverage and the pillbug report already use)
STRAY_FRACTION = 0.5   # a patch is stray when this share of its samples miss
SNAP = 1e-6            # of the size: a control point that would move less than this is left EXACTLY where it is
                       # (so a collapse that changes nothing keeps the export byte-identical)
SIZE_MISMATCH = 0.35   # the target's diagonal may differ from the creature's by this much before we warn (the two
                       # must be in one frame for the distances to mean anything; a target missing a whole limb is
                       # legitimately smaller, so the warning names both causes)


class TrimError(ValueError):
    """A plain-language reason the trim was refused (reported as is)."""


# ------------------------------------------------------------------------------------------------ measurement

def surface_samples(pool, patches, n: int = SURFACE_N) -> np.ndarray:
    """The creature's exact surface sampled ``n`` x ``n`` per patch, in patch order -- ``fit.evaluate`` on
    ``fit.sample_grid``, the same samples ``fit.fit_points`` measures its coverage on."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pool = np.asarray(pool, dtype=np.float64).reshape(-1, 3)
    Np = len(P)
    if not Np:
        return np.zeros((0, 3))
    gu, gv = fit.sample_grid(int(n))
    return fit.evaluate(pool, P, np.repeat(np.arange(Np), n * n), np.tile(gu, Np), np.tile(gv, Np))


def nearest_target(points, targets):
    """(distance, index of the nearest target point) for every point -- ``fit._nearest``, the one nearest-point
    helper (brute force in chunks; numpy only, so it runs in Blender's python)."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tgt = np.asarray(targets, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        return np.zeros(0), np.zeros(0, dtype=np.int64)
    idx, dist = fit._nearest(pts, tgt, k=1)
    return dist[:, 0], idx[:, 0]


@dataclass
class Stray:
    """What of the creature's surface the target does not have."""
    dist: np.ndarray                 # (Np * n * n,) distance of every surface sample to the nearest target point
    nearest: np.ndarray              # (Np * n * n,) which target point that is
    far: np.ndarray                  # (Np,) samples of each patch farther than ``threshold``
    fraction: np.ndarray             # (Np,) that as a fraction of the patch's samples
    rows: np.ndarray                 # the stray patch rows (fraction >= the cutoff), sorted
    threshold: float                 # metres
    diag: float                      # the creature's size (its surface's bounding diagonal)
    n: int
    cutoff: float
    seconds: float = 0.0

    @property
    def total(self) -> int:
        return int(len(self.dist))

    @property
    def far_total(self) -> int:
        return int(self.far.sum())

    @property
    def far_pct(self) -> float:
        return 100.0 * self.far_total / self.total if self.total else 0.0

    @property
    def max_pct(self) -> float:
        return 100.0 * float(self.dist.max()) / self.diag if self.total and self.diag else 0.0

    def text(self) -> str:
        return (f"{len(self.rows)} of {len(self.far)} patches stray ({self.far_total} of {self.total} surface "
                f"samples more than {100.0 * self.threshold / self.diag:.2g} % of the size from the target, "
                f"{self.far_pct:.1f} %; worst {self.max_pct:.1f} %)")


def stray_patches(pool, patches, points, pct: float = STRAY_PCT, fraction: float = STRAY_FRACTION,
                  n: int = SURFACE_N, diag: Optional[float] = None) -> Stray:
    """Per-patch stray-ness of a creature against a target point cloud IN THE SAME FRAME: the fraction of each
    patch's ``n`` x ``n`` surface samples farther than ``pct`` % of the creature's size from the nearest target
    point, and the rows whose fraction reaches ``fraction``. Refuses (``TrimError``) an empty layout, a target
    with no or non-finite points, and a creature with no extent."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pool = np.asarray(pool, dtype=np.float64).reshape(-1, 3)
    if not len(P) or not len(pool):
        raise TrimError("the creature has no patches to measure")
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        raise TrimError("the target has no vertices")
    if not np.isfinite(pts).all():
        raise TrimError("the target has non-finite (NaN/inf) vertex positions")
    t0 = time.perf_counter()
    S = surface_samples(pool, P, n)
    size = float(diag) if diag is not None else fitops.bounding_diagonal(S)
    if size <= 0:
        raise TrimError("the creature has no extent (all its control points coincide)")
    d, j = nearest_target(S, pts)
    thr = pct / 100.0 * size
    K = n * n
    far = (d > thr).reshape(len(P), K).sum(1).astype(np.int64)
    frac = far / float(K)
    rows = np.nonzero(frac >= float(fraction))[0]
    return Stray(dist=d, nearest=j, far=far, fraction=frac, rows=rows, threshold=thr, diag=size, n=int(n),
                 cutoff=float(fraction), seconds=time.perf_counter() - t0)


def limb_counts(rows, patch_limb, limb_names) -> List[tuple]:
    """``[(limb name, stray patches, patches of that limb)]`` for the limbs the stray rows fall in, biggest first
    (a patch in no limb is listed as ``no limb``)."""
    pl = np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    names = list(limb_names)
    out = {}
    for r in np.asarray(rows, dtype=np.int64).reshape(-1):
        k = int(pl[r]) if r < len(pl) else -1
        name = names[k] if 0 <= k < len(names) else "no limb"
        n_stray, n_all = out.get(name, (0, int((pl == k).sum()) if len(pl) else 0))
        out[name] = (n_stray + 1, n_all)
    return sorted(((n, v[0], v[1]) for n, v in out.items()), key=lambda t: (-t[1], t[0]))


def limb_text(rows, patch_limb, limb_names) -> str:
    """``Tail 24 of 24, Torso 2 of 32``."""
    parts = limb_counts(rows, patch_limb, limb_names)
    return ", ".join(f"{name} {k}" + (f" of {m}" if m else "") for name, k, m in parts) or "no limbs recorded"


# ------------------------------------------------------------------------------------------------ collapse

@dataclass
class Collapse:
    pool0: np.ndarray
    pool: np.ndarray
    free: np.ndarray              # (N,) bool: the control points allowed to move (candidates)
    moved: np.ndarray             # (N,) bool: the ones actually moved (past the snap)
    pinned_shared: int            # candidates left alone because a kept patch uses them too (``keep_shared``)
    pinned_rings: int             # ... because a connection ring names them
    near_already: int             # ... because they were already within the threshold of the target
    touched_kept: int = 0         # patches that are NOT stray but use a moved point (0 with ``keep_shared``)
    max_move: float = 0.0
    symmetrized: bool = False


def collapse_pool(pool, patches, points, rows, threshold: float, fixed: Optional[Iterable[int]] = None,
                  symmetric: bool = True, diag: Optional[float] = None, sym_pairs=None,
                  keep_shared: bool = True) -> Collapse:
    """Fold the stray patches onto the target: every control point of the patches in ``rows`` that is farther than
    ``threshold`` from the target is moved onto its nearest target point (``fit._nearest``).

    With ``keep_shared`` (the default) a point a KEPT patch also uses stays exactly where it is, so the stray sheet
    keeps its base where the good surface is and folds down from there and no patch that is on the target moves at
    all; without it those points fold too and the kept patches that share them follow (``touched_kept`` counts
    them). The ``fixed`` points (the connection rings: the combiner welds and scales at that stock ring geometry)
    never move either way. The patch table never changes, which is why the result is exactly as watertight as the
    input. With ``symmetric`` and an X-symmetric net the displacement is mirrored exactly
    (``retargetops.symmetrize_displacement``, the A3 rule), so a symmetric creature stays symmetric."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    P0 = np.asarray(pool, dtype=np.float64).reshape(-1, 3)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    N = len(P0)
    rows = np.asarray(sorted({int(r) for r in rows}), dtype=np.int64)
    if not len(rows):
        raise TrimError("no patch is stray, so there is nothing to collapse")
    if not len(pts):
        raise TrimError("the target has no vertices")
    size = float(diag) if diag is not None else fitops.bounding_diagonal(surface_samples(P0, P))
    keep = np.ones(len(P), dtype=bool)
    keep[rows] = False
    in_stray = np.zeros(N, dtype=bool)
    in_stray[P[rows].reshape(-1)] = True
    in_kept = np.zeros(N, dtype=bool)
    if keep.any():
        in_kept[P[keep].reshape(-1)] = True
    ring = np.zeros(N, dtype=bool)
    if fixed is not None:
        idx = np.asarray(sorted({int(i) for i in fixed}), dtype=np.int64)
        if len(idx) and (idx.min() < 0 or idx.max() >= N):
            raise TrimError(f"a pinned index is outside the {N}-point pool")
        ring[idx] = True
    free = in_stray & ~ring & (~in_kept if keep_shared else np.ones(N, dtype=bool))
    out = P0.copy()
    near_already = 0
    max_move = 0.0
    if free.any():
        fi = np.nonzero(free)[0]
        d, j = nearest_target(P0[fi], pts)
        go = d > threshold
        near_already = int((~go).sum())
        if go.any():
            out[fi[go]] = pts[j[go]]
    symmetrized = False
    if symmetric and len(P0):
        pairs, on_plane = (sym_pairs if sym_pairs is not None
                           else core.mirror_pairs(P0, axis=0, eps=max(fitops.SYMMETRY_EPS * size, 1e-9)))
        # the A3 rule: a stock net is only NEAR-symmetric (cheetah 98.2 % at the pairing tolerance)
        if (len(pairs) + len(on_plane)) / max(N, 1) >= retargetops.CREATURE_SYMMETRIC_FRACTION:
            out = retargetops.symmetrize_displacement(P0, out, pairs, on_plane, ~free)
            symmetrized = True
    step = np.linalg.norm(out - P0, axis=1)
    small = step <= SNAP * size
    out[small] = P0[small]
    moved = ~small
    if moved.any():
        max_move = float(step[moved].max())
    touched = int(moved[P[keep]].any(axis=1).sum()) if keep.any() and moved.any() else 0
    return Collapse(pool0=P0, pool=out, free=free, moved=moved,
                    pinned_shared=int((in_stray & in_kept & ~ring).sum()) if keep_shared else 0,
                    pinned_rings=int((in_stray & ring).sum()), near_already=near_already, touched_kept=touched,
                    max_move=max_move, symmetrized=symmetrized)


# ------------------------------------------------------------------------------------------------ Blender

def _validate_line(n_stray: int, n_patches: int, target: str) -> str:
    return (f"WARN trim: {n_stray} patch(es) of {n_patches} are surface the target ({target}) does not have "
            f"(more than {STRAY_PCT:g} % of the size from it over more than {100 * STRAY_FRACTION:g} % of their "
            f"surface): Tools > Trim Stray Surface selects, deletes or collapses them")


def frame_mismatch(creature_diag: float, target_diag: float, target: str, tol: float = SIZE_MISMATCH):
    """Why a recorded target cannot be the creature's own surface -- it is a different SIZE, so the distances mean
    nothing and every patch comes out stray -- in plain words, or None. (The native pillbug build recorded the fit's
    input mesh, a copy scaled x5.2, as ``bz_fit_source``; the Validate read-out then called all 518 patches stray,
    which says nothing about the fit. The operator warns the same way, :data:`SIZE_MISMATCH`.)"""
    if creature_diag <= 0 or target_diag <= 0:
        return None
    if abs(target_diag - creature_diag) / creature_diag <= tol:
        return None
    return (f"'{target}' is {target_diag / creature_diag:.2f} x the creature's size in the creature's frame: the "
            "stray-surface check measures distances between the two, so it is not run (place and scale the target "
            "as the fit or retarget left it, or pick another target in Tools > Trim Stray Surface)")


if bpy is not None:
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

    from . import edittools, ops, patchsel, store, texops, topoops

    _ACTIONS = (
        ("SELECT", "Select",
         "Select the stray patches' faces and change nothing: look at what the fit put where the target has "
         "nothing, then decide (this is the default -- the trim is yours to make)"),
        ("DELETE", "Delete",
         "Delete the stray patches (the Delete Patch path: tables, rings, pool compaction) and close every hole "
         "they leave that Fill Hole can take (an even loop of 4 sides or more), so the creature stays watertight "
         "-- the game refuses an open mesh. A patch carrying a connection ring is refused by name; Undo takes one "
         "step per edit and the report says how many"),
        ("COLLAPSE", "Collapse onto Target",
         "Move the stray patches' own control points onto the nearest target surface, so the sheet folds flat "
         "against the body instead of webbing across it. The patch table is untouched, so the mesh stays exactly "
         "as watertight, skinned and combinable as it was -- this is what you want for a LIMB THE TARGET DOES "
         "NOT HAVE (a beetle leg on a pillbug): its geometry stays, lying on the body instead of hanging in "
         "the air"))

    def default_target(obj):
        """(name, why there is none): the mesh this creature was fitted or retargeted from (``bz_fit_source`` /
        ``bz_retarget_source`` through ``texops.recorded_source``, the default Transfer Texture uses too)."""
        name = texops.recorded_source(obj)
        if name:
            return name, None
        recorded = [str(obj[k]) for k in ("bz_retarget_source", "bz_fit_source") if obj.get(k)]
        if recorded:
            return "", (f"the mesh {obj.name} was fitted / retargeted to ('{recorded[0]}') is not in the file any "
                        "more: choose the target mesh")
        return "", (f"choose the target mesh: {obj.name} records no fit or retarget source (Fit Patches to Mesh "
                    "and Retarget record theirs), and stray surface only means something against a target")

    def stray_of(obj, context, target_name, pct=STRAY_PCT, fraction=STRAY_FRACTION):
        """(Stray, target object, target points in the creature's space) for a named target mesh; raises
        ``TrimError`` with the reason when the target cannot be used."""
        tgt = bpy.data.objects.get(str(target_name))
        if tgt is None:
            raise TrimError(f"no object called '{target_name}' in the file")
        if tgt is obj or tgt.type != "MESH" or store.is_native(tgt):
            raise TrimError(f"'{target_name}' is not a plain target mesh")
        deps = context.evaluated_depsgraph_get()
        me_eval = tgt.evaluated_get(deps).data
        pts, _nrm = retargetops._target_arrays(obj, tgt, me_eval)
        st = stray_patches(edittools._verts(obj), store.patches(obj), pts, pct=pct, fraction=fraction)
        return st, tgt, pts

    def validate_lines(obj, context) -> list:
        """The Validate read-out (A5): a WARN naming how many patches are surface the RECORDED target does not
        have -- never blocking, and nothing at all when no fit / retarget source is recorded or it has gone."""
        name, _why = default_target(obj)
        if not name:
            return []
        try:
            st, tgt, pts = stray_of(obj, context, name)
        except Exception as e:                       # never silent: say the check could not run and why
            return [f"WARN trim: the stray-surface check against '{name}' failed: {type(e).__name__}: {e}"]
        why = frame_mismatch(st.diag, fitops.bounding_diagonal(pts), tgt.name)
        if why:
            return [f"WARN trim: {why}"]
        if not len(st.rows):
            return []
        return [_validate_line(len(st.rows), len(st.far), tgt.name)]

    class BZ_OT_trim_stray(_Operator):
        """Find the patches that are surface the target mesh does not have -- what a closest-point fit leaves
        webbed across empty space when the donor has a limb the target has not -- and select, delete or collapse
        them. The target defaults to the mesh this creature was fitted or retargeted to"""
        bl_idname = "bz.trim_stray"
        bl_label = "Trim Stray Surface"
        bl_options = {"REGISTER", "UNDO"}

        target: StringProperty(name="Target Mesh", default="", search=retargetops._mesh_search,
                               description="The mesh the creature should be surface of (empty = the mesh it was "
                                           "fitted or retargeted to, which Fit / Retarget record). It must be in "
                                           "the creature's frame, as the fit left it")
        action: EnumProperty(name="Action", items=_ACTIONS, default="SELECT")
        distance: FloatProperty(name="Stray Distance", default=STRAY_PCT, min=0.01, max=50.0, precision=2,
                                subtype="PERCENTAGE",
                                description="How far from the target a surface sample must be to count as missing "
                                            "it, in per cent of the creature's size (2 % is the threshold the "
                                            "retarget's coverage report uses)")
        fraction: FloatProperty(name="Stray Fraction", default=STRAY_FRACTION, min=0.0, max=1.0, precision=2,
                                description="How much of a patch's surface must miss the target for the patch to "
                                            "count as stray (0.5 = more than half of it)")
        fill_holes: BoolProperty(name="Fill Holes", default=True,
                                 description="Delete only: close each hole the deleted patches leave with Fill Hole "
                                             "(a loop of an even number of sides, at least 4: one patch for 4, a "
                                             "strip of two for 6, ...), so the creature stays watertight -- the game "
                                             "refuses an open mesh. A hole Fill Hole cannot take is named, not "
                                             "guessed at")
        pin_rings: BoolProperty(name="Pin Rings", default=True,
                                description="Collapse only: leave every connection ring's control points exactly "
                                            "where they are, so each socket keeps the stock ring geometry the "
                                            "combiner welds and scales at")
        keep_shared: BoolProperty(name="Keep the Shared Edge", default=True,
                                  description="Collapse only: leave a control point a patch that IS on the target "
                                              "also uses exactly where it is, so nothing that fits the target moves "
                                              "and the stray sheet folds down from its base. Off: those points fold "
                                              "too and the patches sharing them follow -- more of the webbing goes, "
                                              "at the price of pulling the good surface at the seam")
        symmetry: BoolProperty(name="Keep Symmetry", default=True,
                               description="Collapse only: on an X-symmetric control net, mirror the collapse "
                                           "exactly (the A3 rule), so a symmetric creature stays symmetric")

        @classmethod
        def poll(cls, context):
            return ops._guard_poll(cls, context)

        def _enter_edit(self, context, obj) -> bool:
            if obj.mode == "EDIT":
                return True
            context.view_layer.objects.active = obj
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
                bpy.ops.object.mode_set(mode="EDIT")
            except RuntimeError as e:
                self.report({"ERROR"}, f"Trim Stray Surface: could not enter Edit Mode: {e}")
                return False
            return True

        def execute(self, context):
            obj = ops._active_native(context)
            if obj is None:
                self.report({"ERROR"}, "Select a native patch creature to trim")
                return {"CANCELLED"}
            if obj.mode == "EDIT":
                obj.update_from_editmode()
            err = store.pool_error(obj)
            if err:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}
            name = self.target
            if not name:
                name, why = default_target(obj)
                if not name:
                    self.report({"ERROR"}, f"Trim Stray Surface: {why}")
                    return {"CANCELLED"}
            got = retargetops.resolve_target(self, context, obj, name)   # the A3 target rules, reported as they are
            if got is None:
                return {"CANCELLED"}
            tgt, me_eval = got
            t_all = time.perf_counter()
            pool0 = edittools._verts(obj)
            patches = store.patches(obj)
            pts, _nrm = retargetops._target_arrays(obj, tgt, me_eval)
            try:
                st = stray_patches(pool0, patches, pts, pct=self.distance, fraction=self.fraction)
            except TrimError as e:
                self.report({"ERROR"}, f"Trim Stray Surface: {e}")
                return {"CANCELLED"}
            except Exception as e:
                self.report({"ERROR"}, f"Trim Stray Surface failed: {type(e).__name__}: {e}")
                return {"CANCELLED"}
            t_diag = fitops.bounding_diagonal(pts)
            if frame_mismatch(st.diag, t_diag, tgt.name) is not None:
                self.report({"WARNING"}, f"{tgt.name} is {t_diag / st.diag:.2f} x the creature's size in the "
                                         "creature's frame -- either it is not in the creature's frame (place and "
                                         "scale it as the retarget did: the distances mean nothing otherwise), or "
                                         "the creature really does have that much surface the target has not")
            limbs = limb_text(st.rows, store.patch_limb(obj), store.limb_names(obj))
            head = f"Trim Stray Surface ({obj.name} vs {tgt.name}): {st.text()}"
            obj["bz_last_trim_stats"] = {"patches": len(st.far), "stray": len(st.rows), "samples": st.total,
                                         "far": st.far_total, "far_pct": st.far_pct, "max_pct": st.max_pct,
                                         "threshold": st.threshold, "diag": st.diag, "target": tgt.name,
                                         "action": self.action, "rows": [int(r) for r in st.rows]}
            if not len(st.rows):
                line = (f"{head}: no patch is stray -- every patch has target under more than "
                        f"{100 * (1 - self.fraction):.0f} % of its surface. Nothing to trim")
                obj["bz_last_trim"] = line
                self.report({"INFO"}, line)
                return {"FINISHED"}
            if self.action == "SELECT":
                return self._select(context, obj, patches, st, tgt, limbs, head, t_all)
            if self.action == "DELETE":
                return self._delete(context, obj, patches, st, tgt, limbs, head, t_all)
            return self._collapse(context, obj, patches, pts, st, tgt, limbs, head, t_all)

        # ------------------------------------------------------------------------------------ Select
        def _select(self, context, obj, patches, st, tgt, limbs, head, t_all):
            import bmesh
            if not self._enter_edit(context, obj):
                return {"CANCELLED"}
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
            for v in bm.verts:
                v.select = False
            for e in bm.edges:
                e.select = False
            n = patchsel.select_rows(bm, patches, st.rows)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            missing = len(st.rows) - n
            line = (f"{head}; {limbs}; selected {n} of them"
                    + (f" ({missing} have no corner quad: a degenerate patch)" if missing else "")
                    + ". Look at them in Face select, then: Delete (with Fill Holes) to take them out, or Collapse "
                      "onto Target to fold them onto the body -- or move them by hand")
            obj["bz_last_trim"] = line
            self.report({"INFO"}, line + f"; in {time.perf_counter() - t_all:.1f} s")
            return {"FINISHED"}

        # ------------------------------------------------------------------------------------ Delete
        def _delete(self, context, obj, patches, st, tgt, limbs, head, t_all):
            if len(st.rows) >= len(patches):
                self.report({"ERROR"}, f"Trim Stray Surface: every one of the {len(patches)} patches is stray -- the "
                                       "creature is nowhere near the target (is it in the creature's frame?)")
                return {"CANCELLED"}
            plan = patchsel.delete_plan(patches, st.rows, patchsel._ring_rows_for_plan(obj), store.point_count(obj))
            if plan.refusal:
                self.report({"ERROR"}, f"Trim Stray Surface: {plan.refusal}. Collapse onto Target keeps the ring "
                                       "and folds the patch onto the body instead")
                return {"CANCELLED"}
            if not self._enter_edit(context, obj):
                return {"CANCELLED"}
            import bmesh
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
            for v in bm.verts:
                v.select = False
            for e in bm.edges:
                e.select = False
            patchsel.select_rows(bm, patches, st.rows)
            bm.select_flush_mode()
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            try:
                res = bpy.ops.bz.delete_patch()
            except RuntimeError as e:
                self.report({"ERROR"}, f"Trim Stray Surface: the delete was refused: {e}")
                return {"CANCELLED"}
            if "FINISHED" not in res:
                self.report({"ERROR"}, "Trim Stray Surface: Delete Patch did not run (see the info log)")
                return {"CANCELLED"}
            filled, left = self._fill(obj)
            open_now = core.open_side_count(store.patches(obj))
            line = (f"{head}; {limbs}; deleted them ({len(store.patches(obj))} patches, "
                    f"{store.point_count(obj)} control points left)"
                    + (f"; {filled} hole(s) filled" if filled else "")
                    + (f"; {len(left)} hole(s) left open ({', '.join(str(k) + ' sides' for k in left)}): Fill Hole "
                       "needs an even loop of 4 or more, Add Quad grows one" if left else "")
                    + f"; {open_now} open side(s) now"
                    + (f"; Undo takes {1 + filled} steps (Delete Patch, then {filled} x Fill Hole)" if filled
                       else ""))
            obj["bz_last_trim"] = line
            if open_now:
                self.report({"WARNING"}, f"{open_now} open side(s) left: the game refuses an open patch mesh "
                                         "(Validate names the dangling edges) -- close them with Fill Hole / Add "
                                         "Quad / Weld before exporting")
            self.report({"INFO"}, line + f"; in {time.perf_counter() - t_all:.1f} s")
            return {"FINISHED"}

        def _fill(self, obj):
            """Close every hole Fill Hole can close -- a loop of an EVEN number of sides, at least 4 (4 sides -> one
            patch, 6 -> a strip of two, ...; B7's rule, and the format cannot express the odd case) -- with
            ``bz.fill_hole``: (filled, the side counts of the holes left)."""
            filled = 0
            if not self.fill_holes:
                return 0, [len(lp) for lp in topoops.hole_loops(store.patches(obj))]
            import bmesh
            for _ in range(len(store.patches(obj)) + 1):
                patches = store.patches(obj)
                loops = topoops.hole_loops(patches)
                fillable = [lp for lp in loops if len(lp) >= 4 and len(lp) % 2 == 0]
                if not fillable:
                    return filled, [len(lp) for lp in loops]
                corner = int(fillable[0][0][2][0])
                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                for f in bm.faces:
                    f.select = False
                for e in bm.edges:
                    e.select = False
                for v in bm.verts:
                    v.select = v.index == corner
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                try:
                    res = bpy.ops.bz.fill_hole()
                except RuntimeError as e:
                    self.report({"WARNING"}, f"a hole could not be filled: {e}")
                    return filled, [len(lp) for lp in topoops.hole_loops(store.patches(obj))]
                if "FINISHED" not in res:
                    break
                filled += 1
            return filled, [len(lp) for lp in topoops.hole_loops(store.patches(obj))]

        # ------------------------------------------------------------------------------------ Collapse
        def _collapse(self, context, obj, patches, pts, st, tgt, limbs, head, t_all):
            rings = retargetops.ring_points(store.connections(obj), store.point_count(obj)) if self.pin_rings else None
            try:
                c = collapse_pool(edittools._verts(obj), patches, pts, st.rows, st.threshold, fixed=rings,
                                  symmetric=self.symmetry, diag=st.diag, keep_shared=self.keep_shared)
            except TrimError as e:
                self.report({"ERROR"}, f"Trim Stray Surface: {e}")
                return {"CANCELLED"}
            open_before = core.open_side_count(patches)
            if not c.moved.any():
                line = (f"{head}; {limbs}; nothing to collapse: every control point of the stray patches is either "
                        f"shared with a patch that is on the target ({c.pinned_shared}: Keep the Shared Edge), on a "
                        f"connection ring ({c.pinned_rings}) or already within the threshold ({c.near_already})")
                obj["bz_last_trim"] = line
                self.report({"INFO"}, line)
                return {"FINISHED"}
            if not ops._begin(self, obj):
                return {"CANCELLED"}
            from . import sculptops
            sculptops.write_pool(obj, c.pool)
            after = stray_patches(c.pool, patches, pts, pct=self.distance, fraction=self.fraction, diag=st.diag)
            open_after = core.open_side_count(store.patches(obj))
            obj["bz_last_trim_stats"] = dict(obj["bz_last_trim_stats"],
                                             moved_points=int(c.moved.sum()), max_move=c.max_move,
                                             stray_after=len(after.rows), far_after=after.far_total,
                                             far_pct_after=after.far_pct, max_pct_after=after.max_pct,
                                             symmetrized=bool(c.symmetrized), open_sides=int(open_after),
                                             keep_shared=bool(self.keep_shared), touched_kept=int(c.touched_kept))
            line = (f"{head}; {limbs}; collapsed onto {tgt.name}: {int(c.moved.sum())} control points moved (max "
                    f"{1000 * c.max_move:.1f} mm; "
                    + (f"{c.pinned_shared} kept where a patch on the target shares them (Keep the Shared Edge -- off, "
                       f"they fold too), " if self.keep_shared else
                       f"{c.touched_kept} patches that are on the target follow a moved point (Keep the Shared Edge "
                       f"is off), ")
                    + f"{c.pinned_rings} on connection rings, {c.near_already} already close)"
                    + ("; symmetry mirrored" if c.symmetrized else "")
                    + f"; stray patches {len(st.rows)} -> {len(after.rows)}, surface off the target "
                      f"{st.far_pct:.1f} % -> {after.far_pct:.1f} % (worst {st.max_pct:.1f} -> "
                      f"{after.max_pct:.1f} %); {open_after} open side(s) (was {open_before}): the patch table is "
                      "untouched, so the mesh is as watertight as it was")
            obj["bz_last_trim"] = line
            if open_after != open_before:
                self.report({"ERROR"}, "Trim Stray Surface: the open-side count changed, which a collapse cannot do "
                                       "-- Undo (Ctrl+Z) and report this")
            if len(after.rows):
                self.report({"WARNING"}, f"{len(after.rows)} patch(es) are still stray after the collapse (their "
                                         "points are shared with patches that are on the target, or pinned rings): "
                                         "Select them to see where, or Delete them")
            self.report({"INFO"}, line + f"; in {time.perf_counter() - t_all:.1f} s")
            return {"FINISHED"}

    CLASSES = (BZ_OT_trim_stray,)
else:                                          # no Blender: the measurements only
    CLASSES = ()

    def validate_lines(obj, context) -> list:
        return []
