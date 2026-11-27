"""Fit Patches to Mesh: any mesh plus a patch budget gives a fitted creature (quad layout, ``codecs.acc``, ``codecs.fit``,
``core.patches_to_creature``), imported through the normal path.

A target is judged on a positionally welded copy of itself (:func:`target_topology`), never on its index buffer: seam
splits are not damage and open edges are not non-manifold edges. The target mesh is never modified.
``fit_cage`` and its helpers must stay bpy-free (``tests/fit_mesh_test.py``).
"""
from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from ICParse import validate as ICV
from ICParse.codecs import acc, fit

try:
    from . import core
except ImportError:                      # imported top-level (the pure test puts ic_blender/ on sys.path)
    import core  # type: ignore[no-redef]

try:
    import bpy
    _Operator = bpy.types.Operator
except ImportError:                      # no Blender: the pipeline only (tests/fit_mesh_test.py)
    bpy = None
    _Operator = object

# The real ceiling is display VERTICES, not patches: the engine puts the whole tessellated patch mesh in one
# DirectX 8 vertex set of at most ICV.MAX_DISPLAY_VERTICES (12288), and a patch costs 16 of them when its seams
# weld with its neighbours' and 25 when every patch has its own texture island (ICParse.validate.display_vertices
# has the decompiled rule and the measurements). MAX_PATCHES is the best case, so it is a hard refusal only;
# COMBINABLE_PATCHES is the budget a fitted creature can still be COMBINED at, which is what an author wants.
MAX_PATCHES = ICV.MAX_PATCHES                                  # 767: even a perfectly welded mesh dies above this
SPLIT_UV_PATCHES = ICV.MAX_DISPLAY_VERTICES // 25              # 491: a fit + per-patch texture atlas costs 25 each
COMBINABLE_PATCHES = ICV.COMBINE_BUDGET_TORSO // 25            # 263: ...and must still survive a graft


def budget_advice(budget: int):
    """(refusal, warning) in plain words for a patch budget — ``None`` where there is nothing to say.

    Pure, so the tests can read the exact wording without running a fit. Fit to Mesh reports the refusal
    as an ERROR (cancelling) and the warning as a WARNING (the fit goes ahead)."""
    budget = int(budget)
    if budget > MAX_PATCHES:
        return (f"Patch budget {budget} cannot load: {MAX_PATCHES} patches is the ceiling ({ICV.MAX_DISPLAY_VERTICES} "
                f"display vertices). About {SPLIT_UV_PATCHES} patches load with "
                f"split UVs, about {COMBINABLE_PATCHES} still combine", None)
    if budget > SPLIT_UV_PATCHES:
        return (None,
                f"Patch budget {budget} is over {SPLIT_UV_PATCHES}: at up to 25 display vertices a patch it can pass the "
                f"{ICV.MAX_DISPLAY_VERTICES} limit unless the seams weld (see Validate)")
    if budget > COMBINABLE_PATCHES:
        return (None,
                f"Patch budget {budget} is over {COMBINABLE_PATCHES}: it will load, but may not combine, as the combiner "
                f"adds a donor's limbs (up to {ICV.WORST_STOCK_LIMB_SET} display vertices)")
    return (None, None)
KEY_FIT_SRC = "bz_fit_src"        # where the template's body was (landmarks, flat x y z ...), for Move Skeleton to the Body
KEY_FIT_TAGS = "bz_fit_src_tags"  # per landmark: -1 = a corner of the body's box, else the limb row whose centre it is
KEY_FIT_MOVED = "bz_fit_skeleton_moved"
# What every Fit result is, said the same way everywhere (the report, the panel, the guide).
FIT_RESULT_NOTE = ("This creature is shape only: it has the template's skeleton and limbs, and no connection rings, so the "
                   "game cannot combine it with other animals. Press Move Skeleton to the Body to put the bones inside the "
                   "new shape. For a creature that can be combined, use Retarget Creature to Mesh or Landmark Retarget on a "
                   "stock animal instead")
SYMMETRY_EPS = 1e-3         # of the bounding diagonal: a point pairs with its mirror image within this
SYMMETRIC_FRACTION = 0.99   # a target is X-symmetric when this fraction of its vertices has a mirror partner
HEAT_FULL = 0.01            # the heat map's red = an error of 1 % of the diagonal (or more)
FEW_SAMPLES = 4             # below this many target points per patch the fit is mostly the fairness term (a warning)
WELD_EPS = 1e-5             # of the bounding diagonal: vertices this close are one point of the surface
SEAM_REACH = 0.25           # of the median layout edge: a QuadriFlow vertex this near the X plane belongs to the seam


class FitError(ValueError):
    """A plain-language reason the fit was refused (reported as is)."""


@dataclass
class FitStats:
    """Distances from the target vertices to the surface (Blender units), and as % of the bounding diagonal."""
    rms: float
    p95: float
    max: float
    diag: float

    @property
    def rms_pct(self) -> float:
        return 100.0 * self.rms / self.diag

    @property
    def p95_pct(self) -> float:
        return 100.0 * self.p95 / self.diag

    @property
    def max_pct(self) -> float:
        return 100.0 * self.max / self.diag

    def text(self) -> str:
        """``rms 3.2 mm (p95 6.1 mm, max 25.0 mm; 0.09 / 0.17 / 0.69 % of size)``, units = metres (IC's)."""
        return (f"rms {1000 * self.rms:.1f} mm (p95 {1000 * self.p95:.1f} mm, max {1000 * self.max:.1f} mm; "
                f"{self.rms_pct:.2f} / {self.p95_pct:.2f} / {self.max_pct:.2f} % of size)")


def stats(d, diag: float) -> FitStats:
    d = np.asarray(d, dtype=np.float64)
    if not len(d):
        raise FitError("no target points to measure")
    return FitStats(rms=float(np.sqrt(np.mean(d * d))), p95=float(np.percentile(d, 95)), max=float(d.max()),
                    diag=float(diag))


def bounding_diagonal(points) -> float:
    P = np.asarray(points, dtype=np.float64)
    return float(np.linalg.norm(P.max(0) - P.min(0))) if len(P) else 0.0


def symmetry(points, diag: float):
    """(paired, on_plane, fraction) of ``points`` that have an X-mirror partner within ``SYMMETRY_EPS`` of the
    diagonal (``core.mirror_pairs``, positions only). ``fraction`` counts partners and on-plane points."""
    P = np.asarray(points, dtype=np.float64)
    if not len(P):
        return 0, 0, 0.0
    pairs, on_plane = core.mirror_pairs(P, axis=0, eps=max(SYMMETRY_EPS * diag, 1e-9))
    return len(pairs), len(on_plane), (len(pairs) + len(on_plane)) / len(P)


def is_x_symmetric(points, diag: float) -> bool:
    return symmetry(points, diag)[2] >= SYMMETRIC_FRACTION


_CORNER = {0: (0, 0), 1: (0, 3), 2: (3, 3), 3: (3, 0)}          # loop position (a, b, c, d) -> grid corner (i, j)
_DIHEDRAL = (lambda i, j: (i, j), lambda i, j: (j, i), lambda i, j: (3 - i, j), lambda i, j: (i, 3 - j),
             lambda i, j: (3 - i, 3 - j), lambda i, j: (3 - j, i), lambda i, j: (j, 3 - i), lambda i, j: (3 - j, 3 - i))


def layout_mirror_pairs(cage_coords, faces, patches, eps: float):
    """Pool-index mirror pairs ``{i: j}`` (``i == j`` on the plane) from the LAYOUT: cage vertices are paired
    spatially (``core.mirror_pairs`` within ``eps``), a face with the mirrored vertex set of another is its mirror
    face, and the 16 control points of mirror faces are matched through the 4x4-grid symmetry that maps the
    corners (``acc.cage_to_patches`` puts a face's a, b, c, d at grid corners (0,0), (0,3), (3,3), (3,0)).
    Returns (pairs, faces without a mirror face, conflicts). Pairing by the layout rather than by position
    (``core.symmetrize``) matters because a fitted net is only near-symmetric: the correspondences of mirrored
    target points drift apart over the iterations (Suzanne, 286 patches: control points up to 6 % of the
    diagonal from their mirror image), which no spatial tolerance pairs safely."""
    V = np.asarray(cage_coords, dtype=np.float64)
    vpairs, vplane = core.mirror_pairs(V, axis=0, eps=eps)
    mv = dict(vpairs); mv.update({i: i for i in vplane})
    by_set = {}
    for fi, f in enumerate(faces):
        by_set.setdefault(frozenset(int(x) for x in f), fi)
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 4, 4)
    pairs, unpaired_faces, conflicts = {}, 0, 0
    for fi, f in enumerate(faces):
        f = [int(x) for x in f]
        gj = by_set.get(frozenset(mv[v] for v in f)) if all(v in mv for v in f) else None
        if gj is None:
            unpaired_faces += 1
            continue
        g = [int(x) for x in faces[gj]]
        want = [_CORNER[g.index(mv[v])] for v in f]              # where f's corners land in g's grid
        T = next((t for t in _DIHEDRAL if all(t(*_CORNER[k]) == want[k] for k in range(4))), None)
        if T is None:
            unpaired_faces += 1
            continue
        for i in range(4):
            for j in range(4):
                a, b = int(P[fi, i, j]), int(P[gj][T(i, j)])
                if pairs.get(a, b) != b:
                    conflicts += 1
                pairs[a] = b
    return pairs, unpaired_faces, conflicts


def symmetrize_pairs(pool, pairs) -> np.ndarray:
    """Exact X symmetry over ``pairs`` (partners averaged, on-plane points put on the plane; the rule of
    ``core.symmetrize`` with the pairing given)."""
    out = np.asarray(pool, dtype=np.float64).copy()
    flip = np.array([-1.0, 1.0, 1.0])
    for i, j in pairs.items():
        if i < j:
            avg = (out[i] + out[j] * flip) / 2.0
            out[i] = avg
            out[j] = avg * flip
        elif i == j:
            out[i, 0] = 0.0
    return out


def _edge_runs(faces) -> dict:
    """{(a, b) with a < b: one entry per face on that edge, True where the face runs it from a to b}."""
    out = {}
    for f in faces:
        for k in range(len(f)):
            a, b = int(f[k - 1]), int(f[k])
            out.setdefault((a, b) if a < b else (b, a), []).append(a < b)
    return out


def boundary_edges(faces) -> int:
    """How many edges of the polygon list belong to one face only (0 = closed)."""
    return sum(1 for runs in _edge_runs(faces).values() if len(runs) == 1)


def _vertex_parts(faces) -> int:
    """How many groups of faces share no vertex with each other."""
    parent = {}

    def root(i):
        while parent.setdefault(i, i) != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for f in faces:
        first = root(int(f[0]))
        for i in f[1:]:
            parent[root(int(i))] = first
    return len({root(i) for i in list(parent)})


def layout_problems(faces) -> str:
    """Why a face list cannot be a creature's patch layout, in a few words; "" when it can: quads on four different
    vertices, one part, every edge shared by two faces that run it in opposite directions, and at most
    ``ICV.MAX_CORNER_PATCHES`` faces at a vertex."""
    if not len(faces):
        return "no faces"
    runs = list(_edge_runs(faces).values())
    valence = Counter(int(i) for f in faces for i in f)
    found = ((sum(1 for f in faces if len(f) != 4 or len(set(f)) != 4), "non-quad faces"),
             (sum(1 for r in runs if len(r) == 1), "open edges"),
             (sum(1 for r in runs if len(r) > 2), "edges with more than two faces"),
             (sum(1 for r in runs if len(r) == 2 and r[0] == r[1]), "edges between faces that face opposite ways"),
             (sum(1 for k in valence.values() if k > ICV.MAX_CORNER_PATCHES),
              f"vertices with more than {ICV.MAX_CORNER_PATCHES} faces"))
    text = [f"{n} {what}" for n, what in found if n]
    parts = _vertex_parts(faces)
    if parts > 1:
        text.append(f"{parts} separate parts")
    return ", ".join(text)


def mirrored_layout(coords, faces):
    """(coords, faces) of QuadriFlow's symmetric remesh as one exactly X-mirrored surface. QuadriFlow remeshes one
    half and mirrors it without welding the two: their rims drift a little off the plane, and a short rim comes
    closed with faces lying in the plane, which a weld would turn into a wall inside the body. So the +X half is kept
    without the faces in the plane, its rim is put on the plane and that half is mirrored. A hole away from the plane
    stays open (:func:`layout_problems`)."""
    V = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    faces = [[int(i) for i in f] for f in faces]
    if not faces:
        return V, faces
    reach = SEAM_REACH * np.median([np.linalg.norm(V[f[k]] - V[f[k - 1]]) for f in faces for k in range(len(f))])
    near = np.abs(V[:, 0]) <= reach

    def in_plane(f):
        n = np.cross(V[f[2]] - V[f[0]], V[f[-1]] - V[f[1]])
        return all(near[i] for i in f) and abs(n[0]) > 0.7 * np.linalg.norm(n)

    half = [f for f in faces if V[f, 0].mean() > 0.0 and not in_plane(f)]
    rim = {i for edge, runs in _edge_runs(half).items() if len(runs) == 1 for i in edge if near[i]}
    half = [f for f in half if not all(i in rim for i in f)]      # such a face would lie on its own mirror image
    used = sorted({i for f in half for i in f})
    off = [i for i in used if i not in rim]
    own = {i: k for k, i in enumerate(used)}
    other = dict(own)                                             # a rim vertex is its own mirror image
    other.update({i: len(used) + k for k, i in enumerate(off)})
    out = V[used].copy()
    out[[own[i] for i in used if i in rim], 0] = 0.0
    out = np.concatenate([out, out[[own[i] for i in off]] * np.array([-1.0, 1.0, 1.0])])
    return out, [[own[i] for i in f] for f in half] + [[other[i] for i in f[::-1]] for f in half]


def layout_score(cage_coords, cage_quads, points, grid: int = 5) -> float:
    """How close a quad layout's initial surface (``acc.cage_to_patches``) starts to the target: the rms
    target-to-surface distance on a coarse projection grid. Cheap (a second for a few hundred quads), and on
    Suzanne it orders the layouts as the fit does (start 3.1 / 1.7 / 1.5 % -> fitted 0.34 / 0.19 / 0.17 %)."""
    coords = [tuple(float(x) for x in c) for c in np.asarray(cage_coords, dtype=np.float64).reshape(-1, 3)]
    pool0, patches = acc.cage_to_patches(len(coords), coords, [list(int(i) for i in f) for f in cage_quads], None)
    _, _, _, d = fit.project(np.asarray(points, dtype=np.float64), np.asarray(pool0), np.asarray(patches), n=grid)
    return float(np.sqrt(np.mean(d * d)))


def heat_colors(residual, diag: float) -> np.ndarray:
    """(N, 4) RGBA for a per-vertex error: blue at 0 through green to red at ``HEAT_FULL`` of the diagonal."""
    t = np.clip(np.asarray(residual, dtype=np.float64) / (HEAT_FULL * diag), 0.0, 1.0)
    return np.stack([np.clip(2 * t - 1, 0, 1), 1 - np.abs(2 * t - 1), np.clip(1 - 2 * t, 0, 1), np.ones_like(t)], 1)


@dataclass
class CageFit:
    pool0: np.ndarray                    # the initial (ACC) control points
    pool: np.ndarray                     # the fitted control points (symmetrized when asked and possible)
    patches: np.ndarray                  # (F, 16) indices into the pool
    before: FitStats                     # target-to-surface distances of the initial surface
    after: FitStats                      # ... of the fitted surface (re-projected after any symmetrization)
    residual: np.ndarray                 # per target vertex, the distance ``after`` summarises (the heat map)
    coverage: FitStats                   # surface-sample-to-nearest-target distances (parts the target does not reach)
    seconds: float
    symmetrized: bool = False            # symmetrize_pairs was applied (the target was X-symmetric)
    paired: int = 0                      # pool points with a mirror partner in the layout
    on_plane: int = 0                    # pool points on the mirror plane
    unpaired_faces: int = 0              # layout faces without a mirror face (their points are left as fitted)
    unmirrored: bool = False             # the target is X-symmetric but the layout is not mirrored: no symmetry applied
    history: List[tuple] = field(default_factory=list)

    @property
    def points_per_patch(self) -> float:
        return len(self.residual) / max(len(self.patches), 1)

    @property
    def symmetry_text(self) -> str:
        n = len(self.pool)
        if self.unmirrored:
            return "no symmetry applied, the layout is not mirrored"
        if not self.symmetrized:
            return "target not X-symmetric, no symmetry applied"
        missing = n - self.paired - self.on_plane
        if missing == 0:
            return f"symmetry kept ({self.paired} control points paired, {self.on_plane} on the plane)"
        return (f"symmetry kept for {self.paired + self.on_plane} of {n} control points ({self.unpaired_faces} "
                f"faces of the layout have no mirror face)")


def fit_cage(cage_coords, cage_quads, points, normals=None, smooth: float = 0.02, iters: int = 10,
             symmetric: bool = False) -> CageFit:
    """The whole pipeline for a ready quad cage: ``acc.cage_to_patches`` -> ``fit.fit_points`` (the one solver)
    -> with ``symmetric``, exact X symmetry through the layout's mirror pairs (none, and ``unmirrored`` set, when
    those pairs contradict each other) -> the metrics (``after`` and ``residual`` measure the pool as exported).
    Refuses (``FitError``) a cage with no or non-quad faces, more than ``MAX_PATCHES`` faces, a target with no or
    non-finite points, and a fit that ended worse than it started."""
    quads = [list(int(i) for i in f) for f in cage_quads]
    bad = [f for f in quads if len(f) != 4]
    if bad:
        raise FitError(f"{len(bad)} of the layout's {len(quads)} faces are not quads; every patch needs a quad")
    if not quads:
        raise FitError("the layout has no faces")
    if len(quads) > MAX_PATCHES:
        raise FitError(f"the layout has {len(quads)} quads, needing {16 * len(quads) + 2} display vertices, over "
                       f"{ICV.MAX_DISPLAY_VERTICES}: at most {MAX_PATCHES} patches can load")
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        raise FitError("the target has no vertices")
    if not np.isfinite(pts).all():
        raise FitError("the target has non-finite (NaN/inf) vertex positions")
    diag = bounding_diagonal(pts)
    if diag <= 0:
        raise FitError("the target's vertices all coincide")
    t0 = time.perf_counter()
    coords = [tuple(float(x) for x in c) for c in np.asarray(cage_coords, dtype=np.float64).reshape(-1, 3)]
    pool0, patches = acc.cage_to_patches(len(coords), coords, quads, None)
    pool0 = np.asarray(pool0, dtype=np.float64)
    patches = np.asarray(patches, dtype=np.int64)
    _, _, _, d0 = fit.project(pts, pool0, patches, n=9)
    before = stats(d0, diag)
    nrm = None
    if normals is not None:
        nrm = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        if len(nrm) != len(pts):
            raise FitError(f"{len(nrm)} normals for {len(pts)} target vertices")
    r = fit.fit_points(pool0, patches, pts, iters=int(iters), smooth=float(smooth), normals=nrm)
    pool = np.asarray(r.pool, dtype=np.float64)
    if not np.isfinite(pool).all():
        raise FitError("the fit produced non-finite control points (the solve failed)")
    symmetrized = unmirrored = False
    paired = on_plane = unpaired_faces = 0
    residual = r.residual
    if symmetric:
        pairs, unpaired_faces, conflicts = layout_mirror_pairs(coords, quads, patches, max(SYMMETRY_EPS * diag, 1e-9))
        unmirrored = conflicts > 0
        if not unmirrored:
            pool = symmetrize_pairs(pool, pairs)
            paired = sum(1 for i, j in pairs.items() if i != j)
            on_plane = sum(1 for i, j in pairs.items() if i == j)
            symmetrized = True
            _, _, _, residual = fit.project(pts, pool, patches, n=9)   # measure what is exported, not the pre-snap fit
    after = stats(residual, diag)
    if after.rms > before.rms:
        raise FitError(f"the fit diverged: {after.text()} after, {before.text()} before -- try fewer "
                       "iterations, more patches or a smoother layout")
    return CageFit(pool0=pool0, pool=pool, patches=patches, before=before, after=after,
                   residual=np.asarray(residual, dtype=np.float64), coverage=stats(r.coverage, diag),
                   seconds=time.perf_counter() - t0, symmetrized=symmetrized, paired=paired, on_plane=on_plane,
                   unpaired_faces=unpaired_faces, unmirrored=unmirrored, history=list(r.history))


def report_line(n_patches: int, mesh_name: str, result: CageFit, layout: str) -> str:
    """``Fit 322 patches to Suzanne: rms ... in 31.2 s`` (the operator report, stored as ``bz_last_fit``)."""
    return (f"Fit {n_patches} patches to {mesh_name}: {result.after.text()} in {result.seconds:.1f} s "
            f"({layout}; before the fit rms {result.before.rms_pct:.2f} %; {result.symmetry_text})")


# ------------------------------------------------------------------------------------------ separate pieces (eyes, teeth)
# A mesh of several shells (a body, two eyes, a row of teeth) is fitted piece by piece and the pieces are joined into one
# patch net through ``topoops.attach_plan`` (the planner behind the Attach button). The body is the piece with the most
# surface; every other piece gets a share of the patch budget by its surface area, at least one patch per cube face.
# A small piece is fitted from a rounded box (a cube whose vertices lie on the ellipsoid through the piece's bounding
# box): QuadriFlow cancels or returns holes on the tiny, near-spherical meshes eyes and teeth are, a rounded box never
# does. Pieces are fitted on their own, so none of the body's symmetry is applied to them.
MAX_SHELLS = 32              # the body plus up to 31 eyes, teeth, claws, horns ...
SHELL_MIN_PATCHES = 6        # the smallest closed patch surface: one patch per face of a cube
QUADRIFLOW_MIN_SHARE = 40    # a piece with fewer patches than this is fitted from a rounded box, not remeshed


def shell_budgets(budget: int, areas) -> List[int]:
    """The patch budget of each piece. ``areas``: the surface area of every piece, the body (the largest) first.
    Every other piece takes its share of ``budget`` by area, at least :data:`SHELL_MIN_PATCHES`; the body keeps the
    rest, never less than half of ``budget`` (so the pieces can add up to a little more than ``budget``, which the
    fit reports)."""
    areas = [max(float(a), 0.0) for a in areas]
    budget = int(budget)
    if len(areas) <= 1:
        return [budget]
    total = sum(areas) or 1.0
    extras = [max(SHELL_MIN_PATCHES, int(round(budget * a / total))) for a in areas[1:]]
    return [max(budget // 2, budget - sum(extras))] + extras


def rounded_box_cage(points, share: int):
    """``(coords (N, 3), faces)``: a closed quad cage for a small piece. A cube of k x k quads per side, with
    ``k = floor(sqrt(share / 6))`` (at least 1), its vertices pushed out onto the ellipsoid through the bounding box of
    ``points``; every face wound outward, as the layouts QuadriFlow gives are."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(P):
        raise FitError("the piece has no vertices")
    k = max(1, int(np.sqrt(max(int(share), SHELL_MIN_PATCHES) / 6.0)))
    lo, hi = P.min(0), P.max(0)
    radii = (hi - lo) / 2.0
    radii = np.maximum(radii, 1e-3 * max(float(radii.max()), 1e-9))     # a flat piece still gets a thin box, never a collapsed one
    verts, index = [], {}

    def vid(p):
        key = tuple(np.round(p, 9))
        if key not in index:
            index[key] = len(verts)
            verts.append(np.asarray(p, dtype=np.float64))
        return index[key]

    ts = np.linspace(-1.0, 1.0, k + 1)
    faces = []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            u_axis, v_axis = [a for a in range(3) if a != axis]
            for i in range(k):
                for j in range(k):
                    quad = []
                    for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                        p = np.zeros(3)
                        p[axis], p[u_axis], p[v_axis] = sign, ts[a], ts[b]
                        quad.append(vid(p))
                    faces.append(quad)
    U = np.array(verts)
    for n, f in enumerate(faces):                   # outward: (b - a) x (d - a) points away from the centre
        a, b, c, d = (U[v] for v in f)
        if float(np.dot(np.cross(b - a, d - a), (a + b + c + d) / 4.0)) < 0.0:
            faces[n] = f[::-1]
    unit = U / np.linalg.norm(U, axis=1, keepdims=True)
    return (lo + hi) / 2.0 + unit * radii, faces


def join_pieces(pieces):
    """``(pool, patches)`` of several fitted pieces (:class:`CageFit`, body first) as one net: each next piece is
    appended through ``topoops.attach_plan`` (the planner behind Attach, so its patch ceiling applies). The pieces stay
    separate surfaces: Weld would be what joins their seams."""
    try:
        from . import topoops
    except ImportError:                  # imported top-level (the pure test puts ic_blender/ on sys.path)
        import topoops  # type: ignore[no-redef]
    pool = np.asarray(pieces[0].pool, dtype=np.float64)
    patches = np.asarray(pieces[0].patches, dtype=np.int64).reshape(-1, 16)
    none = (None, None, None, None, None)
    for p in pieces[1:]:
        plan = topoops.attach_plan(pool, patches, none, p.pool, p.patches, none, 0, 0, [0])
        if plan.refusal:
            raise FitError(f"joining the pieces: {plan.refusal}")
        pool = np.vstack([pool, plan.new_points])
        patches = np.asarray(plan.patches, dtype=np.int64).reshape(-1, 16)
    return pool, patches


def combine_fits(body: CageFit, extras, points, diag: float) -> CageFit:
    """The fit of the whole target from the body's and the other pieces': the pieces joined (:func:`join_pieces`), and
    the distances (``before``, ``after``, ``residual``) measured from ALL the target's points to the joined surface (a
    point of an eye lands on the eye's patches, not the head's). Symmetry and layout facts stay the body's."""
    import dataclasses
    pieces = [body] + list(extras)
    pool, patches = join_pieces(pieces)
    pool0, _ = join_pieces([dataclasses.replace(p, pool=p.pool0) for p in pieces])
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    _, _, _, d0 = fit.project(pts, pool0, patches, n=9)
    _, _, _, d1 = fit.project(pts, pool, patches, n=9)
    return dataclasses.replace(body, pool0=pool0, pool=pool, patches=patches, before=stats(d0, diag), after=stats(d1, diag),
                               residual=np.asarray(d1, dtype=np.float64),
                               seconds=sum(p.seconds for p in pieces))


# ----------------------------------------------------------------------------------- the skeleton follows the body
# Fit keeps the template's skeleton, which still sits where the template's body was. ``landmark_sources`` records, at fit
# time, where the template's body is (the corners of its bounding box and the centre of every limb that got patches);
# ``landmark_targets`` finds the same landmarks on the fitted net later, and the thin-plate warp between the two (the
# warp Landmark Retarget moves the bones with) carries every bone to the body.
def _limb_points(pool, patches, patch_limb, limb: int) -> np.ndarray:
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    rows = np.nonzero(np.asarray(patch_limb, dtype=np.int64).reshape(-1) == int(limb))[0]
    if not len(rows):
        return np.zeros((0, 3))
    return np.asarray(pool, dtype=np.float64)[np.unique(P[rows])]


def _box_corners(points) -> np.ndarray:
    lo, hi = np.min(points, axis=0), np.max(points, axis=0)
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


def landmark_sources(pool, patches, patch_limb, n_limbs: int):
    """``(points (K, 3), tags (K,))``: the 8 corners of the bounding box of the net (tag -1) and the centre of every
    limb that has patches (tag = the limb row), in the net's own space."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pool = np.asarray(pool, dtype=np.float64)
    pts = [row for row in _box_corners(pool[np.unique(P)])]
    tags = [-1] * 8
    for limb in range(int(n_limbs)):
        lp = _limb_points(pool, P, patch_limb, limb)
        if len(lp):
            pts.append(lp.mean(axis=0))
            tags.append(limb)
    return np.array(pts), np.array(tags, dtype=np.int64)


def landmark_targets(tags, pool, patches, patch_limb):
    """``(keep (K,) bool, points (M, 3))``: where the landmarks of :func:`landmark_sources` (by ``tags``) are on this
    net; a limb with no patches now has no landmark (``keep`` False for it)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    pool = np.asarray(pool, dtype=np.float64)
    tags = np.asarray(tags, dtype=np.int64).reshape(-1)
    keep = np.ones(len(tags), dtype=bool)
    out = []
    for i, t in enumerate(tags):
        if t < 0:
            continue
        lp = _limb_points(pool, P, patch_limb, int(t))
        if len(lp):
            out.append((i, lp.mean(axis=0)))
        else:
            keep[i] = False
    box = _box_corners(pool[np.unique(P)])
    rows = [None] * len(tags)
    for i, t in enumerate(tags):
        if t < 0:
            rows[i] = box[i]                 # the corners come first, in the same order as the sources
    for i, c in out:
        rows[i] = c
    return keep, np.array([r for r, k in zip(rows, keep) if k])


def template_landmarks(model):
    """:func:`landmark_sources` of a template creature (an ICParse model): its control points in the bind pose, its
    patches' limbs, in Blender's space."""
    from ICParse.codecs import reconstruct as R
    bw = R.bind_world_matrices(model)
    pool = core.sgm_to_blender(R.world_vertices(model, R.vertex_bone_owners(model), bw))
    P = np.array([p.control_points for p in model.patches], dtype=np.int64).reshape(-1, 16)
    limb_of = np.full(len(P), -1, dtype=np.int64)
    for j, limb in enumerate(model.limbs):
        for p in limb.prims:
            if 0 <= int(p) < len(P):
                limb_of[int(p)] = j
    return landmark_sources(pool, P, limb_of, len(model.limbs))


def body_warp(src, tags, pool, patches, patch_limb):
    """The warp that carries the template's skeleton to the fitted body: a thin-plate spline from the source landmarks
    (``landmark_sources``) to their positions on this net. ``None`` when fewer than 4 landmarks survive."""
    try:
        from . import landmarkops
    except ImportError:                  # imported top-level
        import landmarkops  # type: ignore[no-redef]
    keep, dst = landmark_targets(tags, pool, patches, patch_limb)
    src = np.asarray(src, dtype=np.float64).reshape(-1, 3)[keep]
    if len(src) < landmarkops.MIN_LANDMARKS:
        return None
    return landmarkops.tps_fit(src, dst)


# ------------------------------------------------------------------------------------------------ Blender

if bpy is not None:
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
    from . import ops, store

    _LAYOUTS = (("QUADRIFLOW", "QuadriFlow",
                 "Remesh a copy of the mesh to quads (QuadriFlow) and use them as patches"),
                ("OWN_QUADS", "Own Quads",
                 "Use the mesh's own faces (made into quads) as patches"))


def _bm_parts(bm) -> int:
    """How many connected parts a bmesh's faces form (edges shared by faces connect them)."""
    seen = set(); parts = 0
    for f in bm.faces:
        if f in seen:
            continue
        parts += 1
        stack = [f]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            for e in x.edges:
                stack.extend(g for g in e.link_faces if g not in seen)
    return parts


def _split_shells(me, diag: float, eps: float = WELD_EPS):
    """``[(mesh datablock, area)]`` of the surface's shells, the one with the most area first. The mesh is welded by
    position on a throwaway bmesh first (as :func:`target_topology` does), so seam duplicates are not pieces; ``me``
    is never modified. The caller removes the returned datablocks."""
    import bmesh
    bm = bmesh.new(); bm.from_mesh(me)
    out = []
    try:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=max(float(eps) * float(diag), 1e-9))
        bm.faces.ensure_lookup_table()
        seen = set(); comps = []
        for f in bm.faces:
            if f in seen:
                continue
            stack, part = [f], []
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x); part.append(x)
                for e in x.edges:
                    stack.extend(g for g in e.link_faces if g not in seen)
            comps.append(part)
        comps = sorted(((sum(f.calc_area() for f in part), part) for part in comps), key=lambda c: -c[0])
        for area, part in comps:
            keep = {f.index for f in part}
            b2 = bm.copy()
            b2.faces.ensure_lookup_table()
            bmesh.ops.delete(b2, geom=[f for f in b2.faces if f.index not in keep], context="FACES")
            new = bpy.data.meshes.new("fit_piece")
            b2.to_mesh(new); b2.free()
            new.update()
            out.append((new, float(area)))
        return out
    except BaseException:
        for new, _a in out:
            bpy.data.meshes.remove(new)
        raise
    finally:
        bm.free()


def _loose_parts(me) -> int:
    """How many connected parts the mesh's faces form, **as modelled**. Vertices split at UV / normal seams
    (any atlased glTF, our own unwelded bake) count as separate parts although the surface is one: judge a
    target with :func:`target_topology`, which welds first."""
    import bmesh
    bm = bmesh.new(); bm.from_mesh(me)
    try:
        return _bm_parts(bm)
    finally:
        bm.free()


@dataclass
class Topology:
    """What a mesh's *surface* is, measured on a positionally welded copy (the mesh itself is untouched).

    An index buffer is not a surface: glTF stores one vertex per position+UV+normal tuple, so a UV atlaser
    (xatlas, as image-to-3D pipelines run) duplicates every vertex on a chart boundary, and our own Bake
    Surface duplicates every patch-seam sample. Measured on a Hunyuan3D glTF (3DInputs/Pillbug.glb): as
    modelled 472 parts and 14524 boundary edges; welded by position 1 part, 0 boundary edges, 0 edges with
    more than two faces -- one closed watertight shell all along. Judging it as modelled is what sent that
    mesh through a voxel remesh and cost it its texture, so: weld first, then judge, and keep **boundary**
    edges (one face: holes) and **true non-manifold** edges (more than two faces) as separate numbers.
    """
    parts_raw: int      # loose parts as modelled
    parts: int          # loose parts of the surface -- the real shell count
    welded: int         # vertices the weld merged (seam duplicates)
    boundary: int       # welded edges with exactly one face
    nonmanifold: int    # welded edges with more than two faces
    verts: int          # vertices of the welded surface

    @property
    def seam_split(self) -> bool:
        return self.welded > 0


def target_topology(me, diag: float, eps: float = WELD_EPS) -> Topology:
    """:class:`Topology` of a mesh datablock, welding vertices closer than ``eps`` of ``diag`` on a
    throwaway bmesh (Blender's Merge by Distance). ``me`` is never modified."""
    import bmesh
    bm = bmesh.new(); bm.from_mesh(me)
    try:
        parts_raw = _bm_parts(bm)
        n0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=max(float(eps) * float(diag), 1e-9))
        bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        return Topology(parts_raw=parts_raw, parts=_bm_parts(bm), welded=n0 - len(bm.verts),
                        boundary=sum(1 for e in bm.edges if len(e.link_faces) == 1),
                        nonmanifold=sum(1 for e in bm.edges if len(e.link_faces) > 2),
                        verts=len(bm.verts))
    finally:
        bm.free()


def parts_reason(name: str, t: Topology) -> str:
    """The numbers to quote when a target is refused for being more than one shell: the part count **after**
    welding, and the two edge classes apart -- so nobody reads seams as damage again."""
    s = f"{name} has {t.parts} loose parts"
    if t.parts_raw > t.parts:
        s += (f" ({t.parts_raw} as modelled -- Merge by Distance welds {t.welded} seam-duplicate vertices, "
              "and that part is fine)")
    return s + f", {t.boundary} boundary edges and {t.nonmanifold} true non-manifold edges"


def _mesh_arrays(me):
    """(vertices (N, 3), unit vertex normals (N, 3)) of a mesh datablock, in its local space."""
    n = len(me.vertices)
    V = np.empty(n * 3); me.vertices.foreach_get("co", V)
    N = np.empty(n * 3); me.vertices.foreach_get("normal", N)
    N = N.reshape(-1, 3)
    ln = np.linalg.norm(N, axis=1, keepdims=True); ln[ln == 0] = 1.0
    return V.reshape(-1, 3), N / ln


def _quads_of(me, join_triangles: bool, weld_dist: Optional[float] = None):
    """(coords (N, 3), faces as index lists, non-quad count) of a mesh; with ``join_triangles`` the faces are first
    triangulated and paired back into quads, as Convert Mesh does. ``weld_dist`` merges coincident vertices
    first (a seam-split target -- an atlased glTF -- would otherwise turn its seams into holes in the layout);
    pass it only when there is something to weld, so a clean mesh's layout is bit-for-bit what it always was."""
    import bmesh
    bm = bmesh.new(); bm.from_mesh(me)
    try:
        if weld_dist:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
        if join_triangles:
            bmesh.ops.triangulate(bm, faces=bm.faces)
            bmesh.ops.join_triangles(bm, faces=bm.faces, angle_face_threshold=3.14159, angle_shape_threshold=3.14159,
                                     cmp_seam=False, cmp_sharp=False, cmp_uvs=False, cmp_vcols=False, cmp_materials=False)
        bm.verts.ensure_lookup_table()
        coords = np.array([tuple(v.co) for v in bm.verts], dtype=np.float64).reshape(-1, 3)
        faces = [[v.index for v in f.verts] for f in bm.faces]
    finally:
        bm.free()
    return coords, faces, sum(1 for f in faces if len(f) != 4)


QUADRIFLOW_SEEDS = 8        # remesh attempts (QuadriFlow leaves small holes at random; the best closed layout is taken)
# QuadriFlow cancels on a mesh that is simply SMALL: measured 2026-09-16 on Suzanne's skin (6,964 vertices, budget
# 300), same mesh, only scaled -- diagonal 3.64 / 0.73 / 0.51 / 0.36 m finish, 0.18 m CANCELS, and 18.9 / 364 m
# finish; the pillbug scan (0.44 m, 25,300 faces) cancels too, so it is the absolute size of the cells, not the
# diagonal alone. The remesh is therefore retried on a copy scaled up by a POWER OF TWO (exact in binary floating
# point, so scaling the layout back is lossless -- asserted) until it finishes.
QUADRIFLOW_MIN_DIAG = 4.0   # the size a scaled-up copy is taken to at least (well inside the proven range)
QUADRIFLOW_SCALE_TRIES = 4  # doublings past that before size is ruled out as the cause


def _scale_ladder(diag: float) -> List[float]:
    """The scale factors :func:`_remesh_once` tries, the mesh's own size first: powers of two (so ``x * k / k == x``
    exactly) taking the diagonal to at least :data:`QUADRIFLOW_MIN_DIAG`, then :data:`QUADRIFLOW_SCALE_TRIES`
    doublings more."""
    out = [1.0]
    k = 1.0
    while diag > 0 and diag * k < QUADRIFLOW_MIN_DIAG:
        k *= 2.0
    if k != 1.0:
        out.append(k)
    for _ in range(QUADRIFLOW_SCALE_TRIES):
        k *= 2.0
        out.append(k)
    return out


def _remesh_at(context, obj, me_eval, budget: int, symmetric: bool, seed: int,
               weld_dist: Optional[float], scale: float, fill_holes: bool = False):
    """One QuadriFlow remesh of a temporary copy of the evaluated mesh, scaled by ``scale`` (a power of two) for the
    remesh and scaled back exactly afterwards: (coords, faces), or None when QuadriFlow cancelled. A ``symmetric``
    remesh goes through :func:`mirrored_layout`. The weld distance is scaled with the mesh; ``fill_holes`` closes
    the copy's openings first."""
    me = me_eval.copy()
    k = float(scale)
    if k != 1.0:
        from mathutils import Matrix
        me.transform(Matrix.Diagonal((k, k, k, 1.0)))
        me.update()
    if weld_dist or fill_holes:
        import bmesh
        bm = bmesh.new(); bm.from_mesh(me)
        if weld_dist:
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist * k)
        if fill_holes:
            bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
        bm.to_mesh(me); bm.free()
    me.use_mirror_x, me.use_mirror_y, me.use_mirror_z = bool(symmetric), False, False   # what use_mesh_symmetry reads
    tmp = bpy.data.objects.new(f"{obj.name}_quadriflow", me)
    context.scene.collection.objects.link(tmp)
    try:
        for o in context.view_layer.objects:
            o.select_set(False)
        tmp.select_set(True)
        context.view_layer.objects.active = tmp
        try:
            res = bpy.ops.object.quadriflow_remesh(target_faces=int(budget), use_mesh_symmetry=bool(symmetric),
                                                   use_preserve_sharp=False, seed=int(seed))
        except RuntimeError as e:
            raise FitError(f"QuadriFlow could not remesh {obj.name}: {str(e).strip()[-200:]}")
        if "FINISHED" not in res:
            return None
        coords, faces, _ = _quads_of(tmp.data, join_triangles=False)
        if symmetric:
            coords, faces = mirrored_layout(coords, faces)
        if k != 1.0:
            back = coords / k
            assert np.array_equal(back * k, coords), "scaling the QuadriFlow layout back is not exact"
            coords = back
        return coords, faces
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(me)
        context.view_layer.update()                 # the view layer lists a removed object until updated
        obj.select_set(True)
        context.view_layer.objects.active = obj


def _remesh_once(context, obj, me_eval, budget: int, symmetric: bool, seed: int, diag: float,
                 weld_dist: Optional[float] = None, scale: Optional[List[float]] = None, fill_holes: bool = False):
    """One QuadriFlow layout of the evaluated mesh: (coords, faces), in the mesh's own space.

    QuadriFlow refuses a mesh that is only SMALL (see :data:`QUADRIFLOW_MIN_DIAG`), so the remesh walks
    :func:`_scale_ladder` until one finishes; ``scale`` (a one-element list) carries the factor that worked to the
    next seed, so the size is normalised once, not eight times. ``weld_dist`` merges the copy's coincident vertices
    first -- QuadriFlow wants a closed manifold, and a seam-split target (an atlased glTF) only looks open."""
    ladder = _scale_ladder(diag)
    if scale and scale[0] in ladder:                 # what worked for an earlier seed, first
        ladder = [scale[0]] + [k for k in ladder if k != scale[0]]
    for k in ladder:
        got = _remesh_at(context, obj, me_eval, budget, symmetric, seed, weld_dist, k, fill_holes)
        if got is not None:
            if scale is not None:
                scale[0] = k
            return got
    raise FitError(f"QuadriFlow did not remesh {obj.name} (CANCELLED), even scaled up to {ladder[-1]:g}x: it needs a "
                   "closed, manifold mesh. Repair the mesh or use the Own Quads layout")


def _quadriflow_layout(context, obj, me_eval, budget: int, symmetric: bool, diag: float, pts,
                       weld_dist: Optional[float] = None, is_open: bool = False):
    """The best QuadriFlow layout of the target over ``QUADRIFLOW_SEEDS`` seeds: a sound one (:func:`layout_problems`;
    QuadriFlow fails or leaves small holes at random -- a hole of 3 edges cannot be quad-filled), and of those the one
    whose initial surface starts nearest the mesh (:func:`layout_score`; the seeds differ a lot: 3.1 % vs 1.7 % start
    rms on Suzanne, and the fit keeps that order). A ``symmetric`` target is remeshed mirrored, and plain when no seed
    gives a sound mirrored layout; an open one (``is_open``) is tried as it is and then with its openings filled in
    the copy. Returns (coords, faces, mirrored, note)."""
    scale = [1.0]
    why = failed = ""
    passes = [(mirrored, fill) for mirrored in ((True, False) if symmetric else (False,))
              for fill in ((False, True) if is_open else (False,))]
    for mirrored, fill in passes:
        good = []
        for seed in range(QUADRIFLOW_SEEDS):
            try:
                coords, faces = _remesh_once(context, obj, me_eval, budget, mirrored, seed, diag, weld_dist, scale, fill)
            except FitError as e:
                failed = str(e)
                continue
            problems = layout_problems(faces)
            if problems:
                why = problems
            else:
                good.append((layout_score(coords, faces, pts), seed, coords, faces))
        if good:
            break
    else:
        if not why:                                  # QuadriFlow never returned a layout: its own reason
            raise FitError(failed)
        raise FitError(f"QuadriFlow made no usable layout of {obj.name} at budget {budget} ({why}): try another "
                       "budget, or close the mesh (a creature is one closed skin)")
    good.sort(key=lambda c: c[0])
    score, seed, coords, faces = good[0]
    note = (f"seed {seed}, the best start of {len(good)} closed layouts from {QUADRIFLOW_SEEDS} seeds: "
            + ", ".join(f"{100 * c[0] / diag:.1f} %" for c in good))
    if scale[0] != 1.0:                              # said, not hidden: the layout was built on a scaled copy
        note += (f"; remeshed on a copy scaled x{scale[0]:g} and scaled back exactly (QuadriFlow cancels on a mesh "
                 "this small)")
    return coords, faces, mirrored, note


def _fit_piece(context, obj, me, share: int, layout: str, smooth: float, iterations: int):
    """``(CageFit, how)`` for one separate piece of the target (an eye, a tooth, a horn): a layout of about ``share``
    patches, then the usual fit, never mirrored. A piece with a real share of the budget is remeshed with QuadriFlow, a
    small one (and any piece QuadriFlow fails on) is fitted from a rounded box (:func:`rounded_box_cage`); with the
    Own Quads layout the piece's own quads are used when it is all quads."""
    pts, nrm = _mesh_arrays(me)
    diag = bounding_diagonal(pts)
    if diag <= 0:
        raise FitError("its vertices all coincide")
    coords = faces = None
    how = ""
    if layout == "OWN_QUADS":
        c, f, nonquad = _quads_of(me, join_triangles=True)
        if not nonquad and not layout_problems(f):
            coords, faces, how = c, f, "its own quads"
    elif share >= QUADRIFLOW_MIN_SHARE:
        topo = target_topology(me, diag)
        try:
            coords, faces, _mirrored, _note = _quadriflow_layout(context, obj, me, share, False, diag, pts,
                                                                 is_open=topo.boundary > 0)
            how = "QuadriFlow"
        except FitError:
            coords = faces = None
    if coords is None:
        coords, faces = rounded_box_cage(pts, share)
        how = "rounded box"
    return fit_cage(coords, faces, pts, normals=nrm, smooth=smooth, iters=iterations, symmetric=False), how


# ---------------------------------------------------------------------------------- the template's limbs and skin
@dataclass
class RigSplit:
    """A fitted net's first limb and skin split against the template's own body plan."""
    owners: np.ndarray            # (N,) template bone index per control point
    patch_limb: np.ndarray        # (F,) template limb row per patch
    counts: List[tuple]           # [(limb name, patches)] for the limbs that got any
    empty: List[str]              # the limbs the split left with no patches (stub sockets, honestly)
    bones: int                    # how many bones own points


def rig_split(model, pool_blender, patches) -> Optional[RigSplit]:
    """Split a freshly fitted control net across the TEMPLATE creature's limbs and bones, so the result is a creature
    that can be rigged straight away. None when the template has no bones or no limbs.

    Why it exists (fit-to-rig, 2026-09-16): with every patch in limb 0 and every point on bone 0, every OTHER limb of
    the template has no patches and its bones own no points -- which is exactly the file's definition of a stub bone
    (``ICParse.validate.stub_bone_lines``), so 43 of the pillbug template's 48 bones counted as stubs and every C8
    operator refused them ("'head' is a stub bone ..."). They are not stubs; they are ordinary bones whose limb has
    no geometry yet.

    The split is the body plan's, not a guess: the fitted net is brought into the template's own frame (uniform scale
    of the bounding boxes, centres aligned -- the template's surface against the fitted surface), every control point
    takes its nearest eligible bone segment there (``boner.auto_skin``: the Reset rule, so no stub bone and no end site
    takes points), and every patch goes to the limb of the majority of its 16 points' bones (ties to the lowest, points
    on a bone in no limb to the Torso). The bones are NOT moved: the skeleton is still the template's, and the fit
    report says so."""
    from ICParse.codecs import reconstruct as R
    try:
        from . import boner
    except ImportError:                  # imported top-level (the pure test puts ic_blender/ on sys.path)
        import boner  # type: ignore[no-redef]
    P = np.asarray(pool_blender, dtype=np.float64).reshape(-1, 3)
    F = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    if not len(model.bones) or not len(model.limbs) or not len(P) or not len(F):
        return None
    bw = R.bind_world_matrices(model)
    heads = core.sgm_to_blender(np.array([np.asarray(b, dtype=np.float64)[:3, 3] for b in bw]))
    parents = [None if b.parent is None or int(b.parent) < 0 else int(b.parent) for b in model.bones]
    tails = boner.skin_segments(heads, parents)
    eligible = boner.auto_skin_mask(model)
    if not eligible.any():
        return None
    tpl = core.sgm_to_blender(R.world_vertices(model, R.vertex_bone_owners(model), bw))
    if not len(tpl):
        return None
    # The fitted net into the template's frame, box onto box PER AXIS: the split needs a correspondence between the
    # two body plans, not an isometry -- one uniform scale puts a compact mesh in a long template's middle, where
    # every point's nearest bone is a leg (measured: Suzanne on the pillbug scaffold gave the Head limb 0 patches).
    # It assumes the mesh faces the way the template does, which is the add-on's convention everywhere.
    lo, hi = P.min(0), P.max(0)
    tlo, thi = tpl.min(0), tpl.max(0)
    span = hi - lo
    s = np.where(span > 0, (thi - tlo) / np.where(span > 0, span, 1.0), 1.0)
    mapped = (P - lo) * s + tlo
    owners = boner.auto_skin(mapped, heads, tails, eligible)
    # each handle on its nearest corner's bone, as every stock creature has it (battle skins with corner bones only)
    from ICParse.sgm.creature import layout as LAYOUT
    owners = LAYOUT.handles_follow_corners(owners, F)
    # bone -> limb row (the first row listing it), and the Torso for a bone in none
    limb_of = np.full(len(model.bones), -1, dtype=np.int64)
    index = {}
    for i, b in enumerate(model.bones):
        index.setdefault(str(b.name).lower(), i)
    for j, l in enumerate(model.limbs):
        for bn in l.bone_names:
            i = index.get(str(bn).lower())
            if i is not None and limb_of[i] < 0:
                limb_of[i] = j
    torso = next((j for j, l in enumerate(model.limbs) if boner.class_of(int(l.limb_id)) == boner.TORSO_CLASS), 0)
    rows = np.full(len(F), torso, dtype=np.int64)
    n_limbs = len(model.limbs)
    for k, quad in enumerate(F):
        own = owners[quad[(quad >= 0) & (quad < len(owners))]]
        j = limb_of[own[own >= 0]] if len(own) else np.zeros(0, dtype=np.int64)
        j = j[j >= 0]
        if len(j):
            rows[k] = int(np.bincount(j, minlength=n_limbs).argmax())
    counts = np.bincount(rows, minlength=n_limbs)
    return RigSplit(owners=owners.astype(np.int64), patch_limb=rows,
                    counts=[(str(model.limbs[j].name), int(counts[j])) for j in range(n_limbs) if counts[j]],
                    empty=[str(model.limbs[j].name) for j in range(n_limbs) if not counts[j]],
                    bones=int(len(np.unique(owners[owners >= 0]))))


def _default_output(obj) -> str:
    """Next to the .blend as ``<mesh>_fit.sgm`` (``_fit_2`` ... when that name is taken); for an unsaved file in Blender's user data ``pyic/projects`` folder under a name
    nothing holds yet (never the temp folder: the work must survive a reboot)."""
    from . import startops
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj.name) or "mesh"
    if bpy.data.filepath:                                  # a second fit of the same mesh must not overwrite the first: that file is the
        return startops.startcheck.unique_path(os.path.dirname(bpy.data.filepath), f"{safe}_fit", ".sgm")      # source of the creature it made
    return startops.project_path(safe, "_fit")


def _paint_heat(me, residual, diag: float) -> None:
    """Write / replace the ``fit_error`` point colour attribute on the mesh (blue 0 -> red 1 % of the diagonal)."""
    old = me.color_attributes.get("fit_error")
    if old is not None:
        me.color_attributes.remove(old)
    col = me.color_attributes.new("fit_error", "FLOAT_COLOR", "POINT")
    col.data.foreach_set("color", heat_colors(residual, diag).reshape(-1))
    me.color_attributes.active_color = col


class BZ_OT_fit_mesh(_Operator):
    """Fit a new patch creature to the active mesh, borrowing a template's skeleton. The result is shape only (no connection rings, so the game cannot combine it); use Retarget for a creature that combines"""
    bl_idname = "bz.fit_mesh"
    bl_label = "Fit Patches to Mesh"
    bl_options = {"REGISTER", "UNDO"}

    if bpy is not None:
        budget: IntProperty(name="Patch Budget", default=300, min=1, soft_max=SPLIT_UV_PATCHES, max=100000,
                            description=f"Target patch count; more fit closer but take longer, and over {MAX_PATCHES} none load")
        layout: EnumProperty(name="Layout", items=_LAYOUTS, default="QUADRIFLOW")
        smooth: FloatProperty(name="Seam Smoothness", default=0.02, min=0.0, soft_max=1.0, precision=3,
                              description="How strongly seams are smoothed (0 = seams only meet)")
        iterations: IntProperty(name="Iterations", default=10, min=1, max=50,
                                description="Project-and-solve rounds of the fit")
        template_path: StringProperty(name="Skeleton Template (.sgm)", subtype="FILE_PATH",
                                      default=ops._DEFAULT_TEMPLATE,
                                      description="Creature whose skeleton and materials the result borrows. Empty: the "
                                                  "game's wolf")
        output_path: StringProperty(name="Output .sgm", subtype="FILE_PATH", default="",
                                    description="Where to write the fitted creature; empty = next to the .blend, else Blender's user data folder (pyic/projects)")
        heat_map: BoolProperty(name="Error Heat Map", default=True,
                               description="Paint the fit error on the mesh as the 'fit_error' colour attribute")
        split_limbs: BoolProperty(name="Split into Limbs", default=True,
                                  description="Copy the template's limbs and skinning; off puts all on the Torso and root "
                                              "bone")
        separate_pieces: BoolProperty(name="Fit Separate Pieces", default=True,
                                      description="A mesh of several shells (a body with eyes or teeth) is fitted piece by "
                                                  "piece: the biggest shell is the body, the others are fitted on their own "
                                                  "and joined to it. Off: refuse a mesh with more than one shell")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and not store.is_native(obj)

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object to fit patches to")
            return {"CANCELLED"}
        if store.is_native(obj):
            self.report({"ERROR"}, f"{obj.name} is already a patch creature")
            return {"CANCELLED"}
        refusal, warning = budget_advice(self.budget)
        if refusal:
            self.report({"ERROR"}, refusal)
            return {"CANCELLED"}
        if warning:
            self.report({"WARNING"}, warning)
        if obj.mode != "OBJECT":
            self.report({"ERROR"}, f"{obj.name} is in {obj.mode.title()} mode: switch to Object Mode first")
            return {"CANCELLED"}
        try:
            ops.skeleton_template(context, self.template_path)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        t_all = time.perf_counter()
        deps = context.evaluated_depsgraph_get()
        me_eval = obj.evaluated_get(deps).data              # modifiers applied: the shape the viewport shows
        if len(me_eval.polygons) == 0:
            self.report({"ERROR"}, f"{obj.name} has no faces to fit")
            return {"CANCELLED"}
        pts, nrm = _mesh_arrays(me_eval)
        diag = bounding_diagonal(pts)
        # Judge the surface, not the index buffer: seam-split vertices (an atlased glTF, our own bake) are
        # not damage, and welding them is what tells a UV seam from a real floater. The mesh is untouched.
        topo = target_topology(me_eval, diag)
        pieces = []                       # [(temporary mesh, area)], the body first, when the target is several shells
        if topo.parts != 1:
            if not self.separate_pieces:
                self.report({"ERROR"}, f"{parts_reason(obj.name, topo)}: a creature is one closed skin. Tick Fit Separate "
                                       "Pieces to fit each shell on its own (eyes, teeth) and join them, or separate the "
                                       "extra shells (Edit Mode: P > By Loose Parts) and fit the skin alone")
                return {"CANCELLED"}
            if topo.parts > MAX_SHELLS:
                self.report({"ERROR"}, f"{parts_reason(obj.name, topo)}: more than {MAX_SHELLS} separate pieces is too many "
                                       "to fit one by one. Join the small ones (Ctrl+J) or delete what the game will not "
                                       "show")
                return {"CANCELLED"}
            try:
                pieces = _split_shells(me_eval, diag)
            except Exception as e:
                self.report({"ERROR"}, f"Fit failed while splitting {obj.name} into pieces: {type(e).__name__}: {e}")
                return {"CANCELLED"}
        # the body is the whole mesh when it is one shell, else its biggest shell
        fit_me, fit_pts, fit_nrm, fit_diag, fit_topo = me_eval, pts, nrm, diag, topo
        if pieces:
            fit_me = pieces[0][0]
            fit_pts, fit_nrm = _mesh_arrays(fit_me)
            fit_diag = bounding_diagonal(fit_pts)
            fit_topo = target_topology(fit_me, fit_diag)
        budgets = shell_budgets(self.budget, [a for _m, a in pieces]) if pieces else [int(self.budget)]
        symmetric = is_x_symmetric(fit_pts, fit_diag)
        open_edges = fit_topo.boundary
        # The layout is built from a welded copy when (and only when) the target has seam duplicates:
        # QuadriFlow wants a closed manifold, and the mesh's own quads would inherit the seams as holes.
        weld_dist = WELD_EPS * fit_diag if fit_topo.seam_split else None
        note = ""
        piece_notes, skipped = [], []
        try:
            if self.layout == "QUADRIFLOW":
                coords, faces, mirrored, note = _quadriflow_layout(context, obj, fit_me, budgets[0], symmetric, fit_diag,
                                                                   fit_pts, weld_dist=weld_dist, is_open=open_edges > 0)
            else:
                coords, faces, nonquad = _quads_of(fit_me, join_triangles=True, weld_dist=weld_dist)
                if nonquad:
                    raise FitError(f"{nonquad} of {obj.name}'s {len(faces)} faces are not quads after tris-to-quads: "
                                   "the layout would have holes. Make it all quads, or use the QuadriFlow layout")
                mirrored = symmetric
            result = fit_cage(coords, faces, fit_pts, normals=fit_nrm, smooth=self.smooth, iters=self.iterations,
                              symmetric=mirrored)
            result.unmirrored = result.unmirrored or (symmetric and not mirrored)
            if pieces:                                   # the other shells, each on its own, then one net
                extras = []
                for n, ((me_i, _area), share) in enumerate(zip(pieces[1:], budgets[1:]), start=2):
                    try:
                        r_i, how = _fit_piece(context, obj, me_i, share, self.layout, self.smooth, self.iterations)
                    except FitError as e:
                        skipped.append(f"piece {n} ({len(me_i.polygons)} faces) left out: {e}")
                        continue
                    extras.append(r_i)
                    piece_notes.append(f"piece {n}: {len(r_i.patches)} patches from {how}, {r_i.after.text()}")
                result = combine_fits(result, extras, pts, diag)
            crowded = ICV.crowded_corners(np.asarray(result.patches, dtype=np.int64).reshape(-1, 16))
            if crowded:                                  # a layout vertex where more faces meet than the game holds
                v, k = max(crowded, key=lambda c: c[1])
                raise FitError(f"the layout has {len(crowded)} pole(s) where more than {ICV.MAX_CORNER_PATCHES} patches "
                               f"meet (up to {k}, at control point {v}), which the game can't hold: use QuadriFlow or "
                               "smaller fans")
            if pieces:
                refusal_n, warning_n = budget_advice(len(result.patches))
                if refusal_n:
                    raise FitError(f"the pieces together make {len(result.patches)} patches: " + refusal_n)
                if warning_n:
                    skipped.append(warning_n)
            tpl_path = ops.skeleton_template(context, self.template_path)
            tpl_model = core.read_source(tpl_path)
            split = rig_split(tpl_model, result.pool, result.patches) if self.split_limbs else None
            data, n_points, n_patches = core.patches_to_creature(
                tpl_path, result.pool, result.patches,
                vert_bone=None if split is None else split.owners,
                patch_limb=None if split is None else split.patch_limb)
        except FitError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Fit failed: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        finally:
            for me_i, _area in pieces:
                bpy.data.meshes.remove(me_i)
        out = bpy.path.abspath(self.output_path) if self.output_path else _default_output(obj)
        try:
            ops._atomic_write(out, data)
        except OSError as e:
            self.report({"ERROR"}, f"Cannot write {out}: {e}")
            return {"CANCELLED"}
        if self.heat_map:
            if len(obj.data.vertices) == len(result.residual):
                _paint_heat(obj.data, result.residual, diag)
            else:
                self.report({"WARNING"}, f"Heat map skipped: {obj.name}'s modifiers change its vertex count "
                                         f"({len(obj.data.vertices)} in the mesh, {len(pts)} evaluated)")
        res = bpy.ops.bz.import_sgm(filepath=out)
        if "FINISHED" not in res:
            self.report({"ERROR"}, f"The fitted creature was written to {out} but could not be imported")
            return {"CANCELLED"}
        creature = context.view_layer.objects.active
        result.seconds = time.perf_counter() - t_all
        layout = dict((k, v) for k, v, _ in _LAYOUTS)[self.layout] + (f" {note}" if note else "")
        line = report_line(n_patches, obj.name, result, layout)
        if split is not None:
            line += ("; split into " + ", ".join(f"{n} {nm}" for nm, n in split.counts)
                     + f" on {split.bones} bones" + (f" (no patches: {', '.join(split.empty)})" if split.empty else ""))
        if piece_notes:
            line += "; separate pieces: " + "; ".join(piece_notes)
        creature["bz_last_fit"] = line
        creature["bz_fit_source"] = obj.name           # Transfer Texture's default source
        try:                                           # where the template's body is: Move Skeleton to the Body needs it
            src, tags = template_landmarks(tpl_model)
            creature[KEY_FIT_SRC] = [float(x) for x in src.reshape(-1)]
            creature[KEY_FIT_TAGS] = [int(t) for t in tags]
        except Exception as e:
            self.report({"WARNING"}, f"Move Skeleton to the Body will not be offered for this creature "
                                     f"({type(e).__name__}: {e})")
        if pieces:
            self.report({"INFO"}, f"{obj.name} has {len(pieces)} separate pieces: the biggest is the body, "
                                  f"{len(piece_notes)} others were fitted on their own and joined to it "
                                  "(each keeps its own seams: Weld joins them for good)")
        for why in skipped:
            self.report({"WARNING"}, why)
        self.report({"INFO"}, FIT_RESULT_NOTE)
        if result.unmirrored:
            self.report({"WARNING"}, f"No clean mirrored layout for {obj.name}: fitted without symmetry")
        if topo.seam_split:
            self.report({"INFO"}, f"{obj.name} is modelled as {topo.parts_raw} parts, but {topo.welded} vertices are seam "
                                  "duplicates (UV islands): the layout used a welded copy (your mesh is untouched)")
        if (open_edges or topo.nonmanifold) and self.layout == "OWN_QUADS":
            self.report({"WARNING"}, f"{obj.name} is not one closed skin ({open_edges} open edges after welding, "
                                     f"{topo.nonmanifold} true non-manifold edges): the Object Editor rejects such a mesh "
                                     "(see Validate)")
        elif open_edges:
            self.report({"INFO"}, f"{obj.name} is not closed ({open_edges} open edges after welding); the QuadriFlow "
                                  "layout is")
        if not ops._is_identity(obj.matrix_world):
            self.report({"WARNING"}, f"{obj.name}'s object transform was ignored (the fit is in its local space): "
                                     "apply the transform first to fit the mesh at that size and place")
        if result.points_per_patch < FEW_SAMPLES:
            self.report({"WARNING"}, f"only {len(pts)} target vertices for {n_patches} patches "
                                     f"({result.points_per_patch:.1f} per patch), so the surface between them is a guess: "
                                     "subdivide the mesh or lower the budget")
        if result.coverage.max_pct > 2.0:
            self.report({"WARNING"}, f"parts of the fitted surface are up to {result.coverage.max_pct:.1f} % of the "
                                     "size away from any target vertex (the mesh has no vertices there)")
        self.report({"INFO"}, line + f"; {n_points} control points; written to {out}")
        return {"FINISHED"}


class BZ_OT_fit_move_skeleton(_Operator):
    """Move the skeleton into the shape Fit Patches to Mesh made, so the bones sit inside the new body"""
    bl_idname = "bz.fit_move_skeleton"
    bl_label = "Move Skeleton to the Body"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not ops._guard_poll(cls, context):
            return False
        if obj.get(KEY_FIT_SRC) is None:
            cls.poll_message_set("only for a creature made by Fit Patches to Mesh in this file (for any other creature, "
                                 "use Landmark Retarget)")
            return False
        if store.rig(obj) is None:
            cls.poll_message_set("the creature has no skeleton to move")
            return False
        if obj.get(KEY_FIT_MOVED):
            cls.poll_message_set("the skeleton was already moved to the body (Undo it first to do it again)")
            return False
        return True

    def execute(self, context):
        obj = ops._active_native(context)
        arm = store.rig(obj) if obj is not None else None
        if obj is None or arm is None or obj.get(KEY_FIT_SRC) is None:
            self.report({"ERROR"}, "Select a creature made by Fit Patches to Mesh")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        from . import retargetops
        src = np.array(obj[KEY_FIT_SRC], dtype=np.float64).reshape(-1, 3)
        tags = np.array(obj[KEY_FIT_TAGS], dtype=np.int64)
        pool = ops._mesh_verts(obj)
        warp_fn = body_warp(src, tags, pool, store.patches(obj), store.patch_limb(obj))
        if warp_fn is None:
            self.report({"ERROR"}, "Too few limbs have patches to place the skeleton: the bones were not moved")
            return {"CANCELLED"}
        mode = obj.mode
        if mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        try:
            M = np.array(obj.matrix_world.inverted() @ arm.matrix_world, dtype=np.float64)
            Mi = np.linalg.inv(M)

            def warp(X):                     # armature-local -> creature-local -> the body warp -> armature-local
                X = np.asarray(X, dtype=np.float64).reshape(-1, 3)
                Y = warp_fn(X @ M[:3, :3].T + M[:3, 3])
                return Y @ Mi[:3, :3].T + Mi[:3, 3]
            n_bones, bone_max, _inherited, _owning, turn = retargetops.move_rig_bones(
                context, obj, arm, np.zeros((len(pool), 3)), warp=warp)
        except Exception as e:
            self.report({"ERROR"}, f"The bones were not moved ({type(e).__name__}: {e})")
            return {"CANCELLED"}
        finally:
            if mode == "EDIT":
                context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode="EDIT")
        obj[KEY_FIT_MOVED] = 1
        line = (f"Moved {n_bones} bones to the body (the most by {1000 * bone_max:.0f} mm, turned by up to "
                f"{np.degrees(turn):.1f} degrees). Check them in the rig's Edit Mode and nudge any that sit off; "
                "Export writes the new bone positions")
        obj["bz_last_fit_skeleton"] = line
        self.report({"INFO"}, line)
        return {"FINISHED"}


def draw_fit_help(layout, obj) -> None:
    """The note under the Tools of a creature Fit Patches to Mesh made: what it is, and what to press next."""
    if obj is None or not obj.get("bz_last_fit"):
        return
    box = layout.box()
    box.label(text="Made by Fit Patches to Mesh: shape only", icon="INFO")
    for text in ("No connection rings, so the game cannot combine it.",
                 "For a creature that combines, use Retarget Creature",
                 "to Mesh or Landmark Retarget on a stock animal."):
        box.label(text=text)
    if obj.get(KEY_FIT_SRC) is not None and store.rig(obj) is not None:
        if obj.get(KEY_FIT_MOVED):
            box.label(text="The skeleton was moved to the body.", icon="CHECKMARK")
        else:
            box.operator(BZ_OT_fit_move_skeleton.bl_idname, icon="ARMATURE_DATA")


CLASSES = (BZ_OT_fit_mesh, BZ_OT_fit_move_skeleton)
