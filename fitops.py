"""Fit Patches to Mesh (roadmap A1): any Blender mesh + a patch budget -> a fitted native creature.

The pipeline is the proven recipe of ``tools/fit_experiment.py``: a quad layout (a QuadriFlow remesh of the
target at about the budget, or the mesh's own quads as Convert Mesh uses them) -> ``ICParse.codecs.acc``
(the Catmull-Clark-style initial patches) -> ``ICParse.codecs.fit.fit_points`` against the target's vertices
(normal-aware, soft C1 seams) -> ``core.patches_to_creature`` (the template creature's skeleton and
materials, like Convert Mesh) -> imported through the normal path, so editing, Validate and Export work
unchanged. The errors reported are target-vertex-to-surface distances after the fit (rms / p95 / max, absolute
and as % of the target's bounding diagonal); the optional heat map paints them on the target mesh.

**The import contract** (:class:`Topology` / :func:`target_topology`): a target is judged on a positionally
welded copy of itself, never on its index buffer. Vertices split at UV or normal seams -- every atlased glTF,
our own Bake Surface -- are not damage; only parts that survive the weld are real floaters, and boundary edges
(one face) are not non-manifold edges (more than two faces). The target mesh is never modified.

The bpy-free part (``fit_cage`` and its helpers) is what ``tests/fit_mesh_test.py`` runs without Blender
(``blender_native/`` on sys.path, like the other pure tests); the operator below it needs ``bpy``.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from ICParse import validate as ICV
from ICParse.codecs import acc, fit

try:
    from . import core
except ImportError:                      # imported top-level (the pure test puts blender_native/ on sys.path)
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
        return (f"Patch budget {budget} cannot load: one patch mesh may have {ICV.MAX_DISPLAY_VERTICES} display "
                f"vertices and a patch costs at least 16, so {MAX_PATCHES} patches is the ceiling even with every "
                f"seam welded (dx8vertexset.c: Size too large for VertexSet::Create()). "
                f"About {SPLIT_UV_PATCHES} patches load once each has its own texture island, and about "
                f"{COMBINABLE_PATCHES} still combine with a stock creature", None)
    if budget > SPLIT_UV_PATCHES:
        return (None,
                f"Patch budget {budget} is over {SPLIT_UV_PATCHES}: a fitted creature whose patches each get their "
                f"own texture island costs 25 display vertices a patch, so it would need more than the "
                f"{ICV.MAX_DISPLAY_VERTICES} one patch mesh may have. It only loads if its seams weld (shared "
                f"material, smoothing group and UVs) -- Validate says what it actually costs -- and a combine needs "
                f"far less (about {COMBINABLE_PATCHES} patches)")
    if budget > COMBINABLE_PATCHES:
        return (None,
                f"Patch budget {budget} is over {COMBINABLE_PATCHES}: the creature will load, but the combiner adds "
                f"a donor's limbs to the same patch mesh (the dearest stock limb set costs "
                f"{ICV.WORST_STOCK_LIMB_SET} of the {ICV.MAX_DISPLAY_VERTICES} display vertices), so it may not "
                f"survive a graft")
    return (None, None)
SYMMETRY_EPS = 1e-3         # of the bounding diagonal: a point pairs with its mirror image within this
SYMMETRIC_FRACTION = 0.99   # a target is X-symmetric when this fraction of its vertices has a mirror partner
HEAT_FULL = 0.01            # the heat map's red = an error of 1 % of the diagonal (or more)
FEW_SAMPLES = 4             # below this many target points per patch the fit is mostly the fairness term (a warning)
WELD_EPS = 1e-5             # of the bounding diagonal: vertices this close are one point of the surface


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


def boundary_edges(faces) -> int:
    """How many edges of the polygon list belong to one face only (0 = closed)."""
    count = {}
    for f in faces:
        n = len(f)
        for k in range(n):
            e = (int(f[k]), int(f[(k + 1) % n]))
            e = e if e[0] < e[1] else (e[1], e[0])
            count[e] = count.get(e, 0) + 1
    return sum(1 for c in count.values() if c == 1)


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
    history: List[tuple] = field(default_factory=list)

    @property
    def points_per_patch(self) -> float:
        return len(self.residual) / max(len(self.patches), 1)

    @property
    def symmetry_text(self) -> str:
        n = len(self.pool)
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
    -> with ``symmetric``, exact X symmetry through the layout's mirror pairs -> the metrics (``after`` and
    ``residual`` measure the pool as exported). Refuses (``FitError``) a cage with no or non-quad faces, more than
    ``MAX_PATCHES`` faces, a target with no or non-finite points, and a fit that ended worse than it started."""
    quads = [list(int(i) for i in f) for f in cage_quads]
    bad = [f for f in quads if len(f) != 4]
    if bad:
        raise FitError(f"{len(bad)} of the layout's {len(quads)} faces are not quads; every patch needs a quad")
    if not quads:
        raise FitError("the layout has no faces")
    if len(quads) > MAX_PATCHES:
        raise FitError(f"the layout has {len(quads)} quads: even with every seam welded that needs "
                       f"{16 * len(quads) + 2} display vertices, over the {ICV.MAX_DISPLAY_VERTICES} one patch "
                       f"mesh may have, so no creature with more than {MAX_PATCHES} patches can load "
                       f"(dx8vertexset.c: Size too large for VertexSet::Create())")
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
    symmetrized = False
    paired = on_plane = unpaired_faces = 0
    residual = r.residual
    if symmetric:
        pairs, unpaired_faces, conflicts = layout_mirror_pairs(coords, quads, patches, max(SYMMETRY_EPS * diag, 1e-9))
        if conflicts:
            raise FitError(f"the layout's mirror pairing is inconsistent ({conflicts} control points with two mirror "
                           "partners): the cage is not a clean mirrored mesh")
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
                   unpaired_faces=unpaired_faces, history=list(r.history))


def report_line(n_patches: int, mesh_name: str, result: CageFit, layout: str) -> str:
    """``Fit 322 patches to Suzanne: rms ... in 31.2 s`` (the operator report, stored as ``bz_last_fit``)."""
    return (f"Fit {n_patches} patches to {mesh_name}: {result.after.text()} in {result.seconds:.1f} s "
            f"({layout}; before the fit rms {result.before.rms_pct:.2f} %; {result.symmetry_text})")


# ------------------------------------------------------------------------------------------------ Blender

if bpy is not None:
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
    from . import ops, store

    _LAYOUTS = (("QUADRIFLOW", "QuadriFlow",
                 "Remesh a copy of the mesh to about the budget of quads (Blender's QuadriFlow, symmetric when the "
                 "mesh is X-symmetric) and use those quads as the patch layout"),
                ("OWN_QUADS", "Own Quads",
                 "Use the mesh's own faces (after tris-to-quads) as the patch layout, as Convert Mesh does; every "
                 "face must become a quad"))


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


def _quads_of(me, join_triangles: bool, weld_plane: Optional[float] = None, weld_dist: Optional[float] = None):
    """(coords (N, 3), faces as index lists, non-quad count) of a mesh; with ``join_triangles`` the faces are first
    triangulated and paired back into quads, as Convert Mesh does. ``weld_plane`` (a distance) merges vertices
    that close to the X mirror plane with each other and puts them on it: QuadriFlow's symmetric remesh is two
    unwelded halves (Suzanne at 300: 68 boundary edges, 30 coincident vertex pairs up to 2e-4 apart), which ACC
    would take as boundaries and the export as an open mesh. ``weld_dist`` merges coincident vertices everywhere
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
        if weld_plane:
            near = [v for v in bm.verts if abs(v.co.x) <= weld_plane]
            bmesh.ops.remove_doubles(bm, verts=near, dist=weld_plane)
            for v in bm.verts:
                if abs(v.co.x) <= weld_plane:
                    v.co.x = 0.0
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


def _remesh_at(context, obj, me_eval, budget: int, symmetric: bool, seed: int, diag: float,
               weld_dist: Optional[float], scale: float):
    """One QuadriFlow remesh of a temporary copy of the evaluated mesh, scaled by ``scale`` (a power of two) for the
    remesh and scaled back exactly afterwards: (coords, faces, non-quad count), or None when QuadriFlow cancelled.
    Every length that goes with the mesh (the weld distances) is scaled with it."""
    me = me_eval.copy()
    k = float(scale)
    if k != 1.0:
        from mathutils import Matrix
        me.transform(Matrix.Diagonal((k, k, k, 1.0)))
        me.update()
    if weld_dist:
        import bmesh
        bm = bmesh.new(); bm.from_mesh(me)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist * k)
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
        coords, faces, nonquad = _quads_of(tmp.data, join_triangles=False,
                                           weld_plane=SYMMETRY_EPS * diag * k if symmetric else None)
        if k != 1.0:
            back = coords / k
            assert np.array_equal(back * k, coords), "scaling the QuadriFlow layout back is not exact"
            coords = back
        return coords, faces, nonquad
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.meshes.remove(me)
        context.view_layer.update()                 # the view layer lists a removed object until updated
        obj.select_set(True)
        context.view_layer.objects.active = obj


def _remesh_once(context, obj, me_eval, budget: int, symmetric: bool, seed: int, diag: float,
                 weld_dist: Optional[float] = None, scale: Optional[List[float]] = None):
    """One QuadriFlow layout of the evaluated mesh: (coords, faces, non-quad count), in the mesh's own space.

    QuadriFlow refuses a mesh that is only SMALL (see :data:`QUADRIFLOW_MIN_DIAG`), so the remesh walks
    :func:`_scale_ladder` until one finishes; ``scale`` (a one-element list) carries the factor that worked to the
    next seed, so the size is normalised once, not eight times. ``weld_dist`` merges the copy's coincident vertices
    first -- QuadriFlow wants a closed manifold, and a seam-split target (an atlased glTF) only looks open."""
    ladder = _scale_ladder(diag)
    if scale and scale[0] in ladder:                 # what worked for an earlier seed, first
        ladder = [scale[0]] + [k for k in ladder if k != scale[0]]
    for k in ladder:
        got = _remesh_at(context, obj, me_eval, budget, symmetric, seed, diag, weld_dist, k)
        if got is not None:
            if scale is not None:
                scale[0] = k
            return got
    raise FitError(f"QuadriFlow did not remesh {obj.name} (CANCELLED) at its own size (diagonal {diag:.3g}) or "
                   f"scaled up to {ladder[-1]:g}x it: it needs a closed, manifold mesh, so size is not the cause "
                   "here (Blender's QuadriFlow also cancels on a mesh that is merely small, which the fit "
                   "normalises away). Use the Own Quads layout, or repair the mesh")


def _quadriflow_layout(context, obj, me_eval, budget: int, symmetric: bool, diag: float, pts,
                       weld_dist: Optional[float] = None):
    """The best QuadriFlow layout of the target over ``QUADRIFLOW_SEEDS`` seeds: closed and all quads (a creature
    must be; QuadriFlow closes a mesh's small openings -- Suzanne's eye sockets -- but leaves small holes of its own
    at random: Suzanne at 300, seeds 0-4, holes in 4 of 5 symmetric and 1 of 5 plain remeshes, and a hole of 3
    edges cannot be quad-filled), and of those the one whose initial surface starts nearest the mesh
    (:func:`layout_score`; the seeds differ a lot: 3.1 % vs 1.7 % start rms on Suzanne, and the fit keeps that
    order). Returns (coords, faces, seed, note)."""
    tried, closed = [], []
    scale = [1.0]
    for seed in range(QUADRIFLOW_SEEDS):
        coords, faces, nonquad = _remesh_once(context, obj, me_eval, budget, symmetric, seed, diag, weld_dist, scale)
        holes = boundary_edges(faces)
        if nonquad or holes:
            tried.append(f"seed {seed}: {len(faces)} faces, {nonquad} non-quad, {holes} open edges")
            continue
        closed.append((layout_score(coords, faces, pts), seed, coords, faces))
    if not closed:
        raise FitError(f"QuadriFlow gave no closed all-quad layout of {obj.name} at budget {budget} in "
                       f"{QUADRIFLOW_SEEDS} attempts ({'; '.join(tried)}): try another budget, or close the mesh "
                       "(a creature is one closed, manifold skin)")
    closed.sort(key=lambda c: c[0])
    score, seed, coords, faces = closed[0]
    note = (f"seed {seed}, the best start of {len(closed)} closed layouts from {QUADRIFLOW_SEEDS} seeds: "
            + ", ".join(f"{100 * c[0] / diag:.1f} %" for c in closed))
    if scale[0] != 1.0:                              # said, not hidden: the layout was built on a scaled copy
        note += (f"; remeshed on a copy scaled x{scale[0]:g} and scaled back exactly (QuadriFlow cancels on a mesh "
                 "this small)")
    return coords, faces, seed, note


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
    except ImportError:                  # imported top-level (the pure test puts blender_native/ on sys.path)
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
    """Next to the .blend as ``<mesh>_fit.sgm``, or ``/tmp/bz_fit_<mesh>.sgm`` for an unsaved file."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj.name) or "mesh"
    if bpy.data.filepath:
        return os.path.join(os.path.dirname(bpy.data.filepath), f"{safe}_fit.sgm")
    return f"/tmp/bz_fit_{safe}.sgm"


def _paint_heat(me, residual, diag: float) -> None:
    """Write / replace the ``fit_error`` point colour attribute on the mesh (blue 0 -> red 1 % of the diagonal)."""
    old = me.color_attributes.get("fit_error")
    if old is not None:
        me.color_attributes.remove(old)
    col = me.color_attributes.new("fit_error", "FLOAT_COLOR", "POINT")
    col.data.foreach_set("color", heat_colors(residual, diag).reshape(-1))
    me.color_attributes.active_color = col


class BZ_OT_fit_mesh(_Operator):
    """Fit a bicubic-patch creature to the active mesh: a patch layout (QuadriFlow at the budget, or the mesh's own
    quads) is fitted to the mesh's vertices (ICParse.codecs.fit) and imported as a native creature borrowing the
    template's skeleton and materials, with the fit error reported (and painted on the mesh). The mesh itself is
    not changed; the fit is in its local space"""
    bl_idname = "bz.fit_mesh"
    bl_label = "Fit Patches to Mesh"
    bl_options = {"REGISTER", "UNDO"}

    if bpy is not None:
        budget: IntProperty(name="Patch Budget", default=300, min=1, soft_max=SPLIT_UV_PATCHES, max=100000,
                            description=f"Quads the layout aims for (patches of the creature). More patches follow "
                                        f"the mesh more closely and take longer to fit (about 30 s for 300). The "
                                        f"ceiling is display VERTICES, not patches: one patch mesh may have "
                                        f"{ICV.MAX_DISPLAY_VERTICES}, and a fitted patch costs up to 25 of them "
                                        f"(16 only where neighbouring patches share a material, a smoothing group "
                                        f"and their seam UVs), so about {SPLIT_UV_PATCHES} patches load and about "
                                        f"{COMBINABLE_PATCHES} still survive being combined with a stock creature; "
                                        f"over {MAX_PATCHES} nothing can load")
        layout: EnumProperty(name="Layout", items=_LAYOUTS, default="QUADRIFLOW")
        smooth: FloatProperty(name="Seam Smoothness", default=0.02, min=0.0, soft_max=1.0, precision=3,
                              description="Weight of the soft C1 term across patch seams (0 = seams only meet, "
                                          "larger = smoother seams at the cost of fidelity)")
        iterations: IntProperty(name="Iterations", default=10, min=1, max=50,
                                description="Project-and-solve rounds of the fit")
        template_path: StringProperty(name="Skeleton Template (.sgm)", subtype="FILE_PATH",
                                      default=ops._DEFAULT_TEMPLATE,
                                      description="The creature whose skeleton and materials the result borrows "
                                                  "(as Convert Mesh)")
        output_path: StringProperty(name="Output .sgm", subtype="FILE_PATH", default="",
                                    description="Where the fitted creature is written (it becomes the imported "
                                                "creature's source file); empty = next to the .blend, or /tmp for "
                                                "an unsaved file")
        heat_map: BoolProperty(name="Error Heat Map", default=True,
                               description="Paint the fit error on the mesh as the 'fit_error' colour attribute "
                                           "(blue 0 to red 1 % of the mesh's size; Viewport Shading > Attribute)")
        split_limbs: BoolProperty(name="Split into Limbs", default=True,
                                  description="Give the fitted creature the template's own limb and skin split: the "
                                              "net is brought into the template's frame, every control point takes "
                                              "its nearest bone there and every patch the limb of its points' bones. "
                                              "Off puts every patch in the Torso and every point on the root bone, "
                                              "which makes every other limb's bones count as stub bones and the "
                                              "bone operators refuse them")

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
            self.report({"ERROR"}, f"{obj.name} is already a native patch creature")
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
        if not os.path.exists(bpy.path.abspath(self.template_path)):
            self.report({"ERROR"}, f"Skeleton template not found: {self.template_path}")
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
        if topo.parts != 1:
            self.report({"ERROR"}, f"{parts_reason(obj.name, topo)}: a creature is one closed skin, and inner "
                                   "shells (eyes inside sockets) pull the surface inwards. Separate them (Edit Mode: "
                                   "P > By Loose Parts) and delete or hide what is not the skin, then fit the skin alone")
            return {"CANCELLED"}
        symmetric = is_x_symmetric(pts, diag)
        open_edges = topo.boundary
        # The layout is built from a welded copy when (and only when) the target has seam duplicates:
        # QuadriFlow wants a closed manifold, and the mesh's own quads would inherit the seams as holes.
        weld_dist = WELD_EPS * diag if topo.seam_split else None
        note = ""
        try:
            if self.layout == "QUADRIFLOW":
                coords, faces, _seed, note = _quadriflow_layout(context, obj, me_eval, self.budget, symmetric, diag,
                                                                pts, weld_dist=weld_dist)
            else:
                coords, faces, nonquad = _quads_of(me_eval, join_triangles=True, weld_dist=weld_dist)
                if nonquad:
                    raise FitError(f"{nonquad} of {obj.name}'s {len(faces)} faces are not quads after tris-to-quads: "
                                   "the layout would have holes. Make it all quads, or use the QuadriFlow layout")
            result = fit_cage(coords, faces, pts, normals=nrm, smooth=self.smooth, iters=self.iterations,
                              symmetric=symmetric)
            crowded = ICV.crowded_corners(np.asarray(result.patches, dtype=np.int64).reshape(-1, 16))
            if crowded:                                  # a layout vertex where more faces meet than the game holds
                v, k = max(crowded, key=lambda c: c[1])
                raise FitError(f"the layout has {len(crowded)} pole(s) where more than {ICV.MAX_CORNER_PATCHES} patches "
                               f"meet (up to {k}, at control point {v}): the game's corner record holds "
                               f"{ICV.MAX_CORNER_PATCHES} (the Object Editor 16) and the release build writes past it. "
                               "Use the QuadriFlow layout, or give the mesh smaller fans")
            tpl_path = bpy.path.abspath(self.template_path)
            split = rig_split(core.read_source(tpl_path), result.pool, result.patches) if self.split_limbs else None
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
        creature["bz_last_fit"] = line
        creature["bz_fit_source"] = obj.name           # Transfer Texture's default source
        if split is not None:
            self.report({"INFO"}, "The limbs and the skin are the TEMPLATE's body plan mapped onto the mesh's box, "
                                  "and the skeleton is the template's, unmoved: move the bones into the shape "
                                  "(Boner > Bones, or Retarget) before you animate it")
        if topo.seam_split:
            self.report({"INFO"}, f"{obj.name} is modelled as {topo.parts_raw} parts, but {topo.welded} of its "
                                  "vertices are seam duplicates (UV islands): it is one closed surface, and the "
                                  "layout was built from a welded copy (the mesh itself is untouched)")
        if open_edges and self.layout == "OWN_QUADS":
            self.report({"WARNING"}, f"{obj.name} is not closed ({open_edges} open edges after welding, "
                                     f"{topo.nonmanifold} true non-manifold edges), so neither is the creature: "
                                     "the Object Editor rejects open patch meshes (Validate names the dangling edges)")
        elif open_edges:
            self.report({"INFO"}, f"{obj.name} is not closed ({open_edges} open edges after welding); the QuadriFlow "
                                  "layout is")
        if not ops._is_identity(obj.matrix_world):
            self.report({"WARNING"}, f"{obj.name}'s object transform was ignored (the fit is in its local space): "
                                     "apply the transform first to fit the mesh at that size and place")
        if result.points_per_patch < FEW_SAMPLES:
            self.report({"WARNING"}, f"only {len(pts)} target vertices for {n_patches} patches ({result.points_per_patch:.1f} "
                                     f"per patch): between the vertices the surface is the fairness term's guess, not "
                                     "the mesh (subdivide the mesh, or lower the budget, for a decided fit)")
        if result.coverage.max_pct > 2.0:
            self.report({"WARNING"}, f"parts of the fitted surface are up to {result.coverage.max_pct:.1f} % of the "
                                     "size away from any target vertex (the mesh has no vertices there)")
        self.report({"INFO"}, line + f"; {n_points} control points; written to {out}")
        return {"FINISHED"}


CLASSES = (BZ_OT_fit_mesh,)
