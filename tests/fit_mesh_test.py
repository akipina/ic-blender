"""Fit Patches to Mesh without Blender (roadmap A1): the bpy-free pipeline of ``fitops`` on a synthetic quad cage.
    python blender_native/tests/fit_mesh_test.py

1. a hand-built closed quad cage (a cube with 4 x 4 quads per side, 96 quads, consistent winding) -> ACC patches;
   the target is a known bicubic surface: the same patch layout with its control points smoothly deformed (a
   squash, a bend and a bulge, X-symmetric, 2.6 % rms from the cage's own surface), sampled at mirrored random
   parameters with its normals. The fit from the undeformed cage must land on it: rms below 0.1 % of the diagonal
   (0.058 % measured), max below 1 % (0.58 %), at least 20 x better than the start (45 x), exactly symmetric after
   the layout pairing (every control point paired through its mirror face or on the plane, and the pairing is the
   identity on the symmetric start; the fitted net itself is only near-symmetric because the correspondence step
   is not exactly mirrored). Finding: with the regularisers
   off the known surface is still not recovered exactly (rms 0.035 %): the solver's convergence from that far a start,
   not the layout, is the limit -- pinned as a bound, not as exactness;
2. the metrics: stats / percentages / the report text, the heat-map ramp (blue at 0, green at half, red at 1 % of the
   diagonal and beyond), the symmetry test on symmetric and skewed point sets;
3. the refusals: non-quad faces (with the count), an empty layout, more quads than the display-vertex ceiling allows, no target points,
   non-finite targets, mismatched normals, a diverged fit; few points (100 on 96 patches) fit, with the count
   reported (the operator warns below 4 per patch).
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fitops  # noqa: E402
from ICParse.codecs import fit  # noqa: E402
from ICParse.sgm.creature import scaffold  # noqa: E402

T0 = time.perf_counter()


def cube_cage(n=4, size=2.0):
    """A closed quad cage: a cube of side ``size`` with n x n quads per face, shared vertices, outward winding."""
    verts, index = [], {}

    def vid(p):
        key = tuple(np.round(p, 9))
        if key not in index:
            index[key] = len(verts); verts.append(np.asarray(p, dtype=np.float64))
        return index[key]

    faces = []
    h = size / 2.0
    ts = np.linspace(-h, h, n + 1)
    for axis in range(3):
        for sign in (-1.0, 1.0):
            u_axis, v_axis = [a for a in range(3) if a != axis]
            for i in range(n):
                for j in range(n):
                    quad = []
                    for (a, b) in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                        p = np.zeros(3); p[axis] = sign * h; p[u_axis] = ts[a]; p[v_axis] = ts[b]
                        quad.append(vid(p))
                    faces.append(tuple(quad))
    faces = scaffold._orient(faces)
    V = np.array(verts)
    if scaffold._signed_volume(V, faces) < 0:
        faces = [f[::-1] for f in faces]
    return V, faces


def deform(P):
    """A smooth, X-symmetric deformation: squash in z, bend along y with x, bulge with x."""
    Q = np.asarray(P, dtype=np.float64).copy()
    x, y, z = Q[:, 0].copy(), Q[:, 1].copy(), Q[:, 2].copy()
    Q[:, 0] = x * (1.075 + 0.075 * np.cos(0.8 * y))
    Q[:, 1] = y + 0.06 * x * x
    Q[:, 2] = 0.9 * z + 0.05 * np.sin(1.1 * y) + 0.025 * x * x
    return Q


def sample_surface(pool, patches, n, rng):
    """n random surface samples with normals, mirrored in X (each sample and its mirror image: a symmetric mesh's
    vertices come in pairs, which is what the operator's symmetry rule sees)."""
    pid = rng.integers(0, len(patches), n // 2)
    u = rng.uniform(0, 1, n // 2); v = rng.uniform(0, 1, n // 2)
    X = fit.evaluate(pool, patches, pid, u, v)
    N = fit.surface_normals(pool, patches, pid, u, v)
    flip = np.array([-1.0, 1.0, 1.0])
    return np.concatenate([X, X * flip]), np.concatenate([N, N * flip])


def expect_refusal(words, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except fitops.FitError as e:
        for w in words:
            assert w in str(e), (w, str(e))
        return str(e)
    raise AssertionError(f"no refusal ({words})")


# --- 1. a cage fitted to a known bicubic surface -------------------------------------------------------------
V, faces = cube_cage(4)
assert len(faces) == 96 and len(V) == 98, (len(faces), len(V))
pool0, patches = fitops.acc.cage_to_patches(len(V), [tuple(map(float, p)) for p in V], [list(f) for f in faces], None)
pool0 = np.asarray(pool0); patches = np.asarray(patches)
rng = np.random.default_rng(7)
pool_true = deform(pool0)
X, N = sample_surface(pool_true, patches, 6000, rng)
diag = fitops.bounding_diagonal(X)
assert fitops.is_x_symmetric(X, diag), fitops.symmetry(X, diag)
print(f"target: {len(X)} mirrored samples of a deformed {len(patches)}-patch surface, diag {diag:.3f}")

r = fitops.fit_cage(V, faces, X, normals=N, smooth=0.02, iters=10, symmetric=True)
print(f"fit (defaults): before rms {r.before.rms_pct:.3f} % max {r.before.max_pct:.2f} % | after {r.after.text()} | "
      f"coverage max {r.coverage.max_pct:.2f} % | {r.seconds:.1f} s | {r.symmetry_text}")
assert r.after.rms_pct < 0.1, r.after.text()
assert r.after.max_pct < 1.0, r.after.text()
assert r.after.rms < r.before.rms / 20, (r.after.rms, r.before.rms)
assert r.symmetrized and r.paired + r.on_plane == len(r.pool) and r.unpaired_faces == 0, r.symmetry_text
assert len(r.residual) == len(X) and abs(float(np.sqrt(np.mean(r.residual ** 2))) - r.after.rms) < 1e-12
# the layout pairing: every face has a mirror face, no conflicts, and it is the identity on the symmetric start
lp, unpaired, conflicts = fitops.layout_mirror_pairs(V, faces, patches, fitops.SYMMETRY_EPS * diag)
assert (len(lp), unpaired, conflicts) == (len(pool0), 0, 0), (len(lp), unpaired, conflicts)
assert sum(1 for i, j in lp.items() if i == j) == 48 and all(lp[lp[i]] == i for i in lp)    # an involution
assert np.abs(fitops.symmetrize_pairs(pool0, lp) - pool0).max() < 1e-12
# the layout score is the start rms (coarser grid: within 10 %), and the closed cube has no open edges
score = fitops.layout_score(V, faces, X)
assert abs(score - r.before.rms) < 0.1 * r.before.rms, (score, r.before.rms)
assert fitops.boundary_edges(faces) == 0 and fitops.boundary_edges(faces[:-1]) == 4 and fitops.boundary_edges([]) == 0
# ... and pairs the whole net even when the fit left it visibly asymmetric (a 5 % of the diagonal shove on one side)
shoved = r.pool.copy(); shoved[r.pool[:, 0] > 0.5] += [0.05 * diag, 0.0, 0.0]
sym = fitops.symmetrize_pairs(shoved, lp)
pairs_s, plane_s = fitops.core.mirror_pairs(sym, 0, eps=1e-9)
assert len(pairs_s) + len(plane_s) == len(sym) and len(fitops.core.mirror_pairs(shoved, 0, eps=1e-9)[0]) < len(pairs_s)
assert r.pool.shape == pool0.shape and np.array_equal(r.patches, patches)
assert len(r.history) == 10
# the symmetrized pool is exactly mirrored
pairs, plane = fitops.core.mirror_pairs(r.pool, 0, eps=fitops.SYMMETRY_EPS * diag)
flip = np.array([-1.0, 1.0, 1.0])
assert all(np.allclose(r.pool[i] * flip, r.pool[j], atol=1e-12) for i, j in pairs.items())
assert all(r.pool[i, 0] == 0.0 for i in plane)
# without the regularisers (fairness, seams) the fit is a little closer but still not the exact known surface
r0 = fit.fit_points(pool0, patches, X, iters=10, fair=0.0, smooth=0.0, normals=N)
noreg = fitops.stats(r0.residual, diag)
print(f"fit (no regularisers): {noreg.text()}")
assert noreg.rms_pct < 0.1 and noreg.max_pct < 1.0, noreg.text()
# a non-symmetric run does not touch the pool
r_ns = fitops.fit_cage(V, faces, X, normals=N, symmetric=False)
assert not r_ns.symmetrized and r_ns.symmetry_text == "target not X-symmetric, no symmetry applied"
assert r_ns.after.rms_pct < 0.1
# the report line
line = fitops.report_line(len(patches), "Cube", r, "Own Quads")
print(line)
assert line.startswith("Fit 96 patches to Cube: rms ") and " mm (p95 " in line and "% of size) in " in line
assert "(Own Quads; before the fit rms " in line and "symmetry kept" in line

# --- 2. metrics ------------------------------------------------------------------------------------------------
s = fitops.stats([0.0, 3.0, 4.0], 100.0)
assert (s.rms, s.p95, s.max) == (5.0 / np.sqrt(3), 3.9, 4.0) and s.max_pct == 4.0 and s.rms_pct == 5.0 / np.sqrt(3)
assert s.text() == "rms 2886.8 mm (p95 3900.0 mm, max 4000.0 mm; 2.89 / 3.90 / 4.00 % of size)"
expect_refusal(["no target points"], fitops.stats, [], 1.0)
heat = fitops.heat_colors([0.0, 0.005, 0.01, 0.02], 1.0)
assert np.allclose(heat, [[0, 0, 1, 1], [0, 1, 0, 1], [1, 0, 0, 1], [1, 0, 0, 1]]), heat
Q = np.array([[1.0, 0, 0], [-1.0, 0, 0], [0.0, 2.0, 0], [0.5, 1, 1], [-0.5, 1, 1]])
assert fitops.symmetry(Q, 3.0) == (4, 1, 1.0) and fitops.is_x_symmetric(Q, 3.0)
Q2 = Q.copy(); Q2[3, 0] = 0.6                                # one pair broken: 3 of 5 still symmetric
assert fitops.symmetry(Q2, 3.0) == (2, 1, 3 / 5) and not fitops.is_x_symmetric(Q2, 3.0)
assert fitops.symmetry(np.zeros((0, 3)), 1.0) == (0, 0, 0.0)
assert fitops.bounding_diagonal(np.zeros((0, 3))) == 0.0

# --- 3. refusals -----------------------------------------------------------------------------------------------
tri_faces = [list(f) for f in faces]; tri_faces[0] = tri_faces[0][:3]; tri_faces[5] = tri_faces[5][:3]
print(expect_refusal(["2 of the layout's 96 faces are not quads"], fitops.fit_cage, V, tri_faces, X))
print(expect_refusal(["no faces"], fitops.fit_cage, V, [], X))
many = [list(f) for f in faces] * 9                           # 864 quads (a layout, not a mesh: refused on count first)
# the ceiling is display VERTICES (12288 in one DirectX 8 vertex set); 767 patches is the best case,
# reached only when every seam welds -- ICParse/PATCH_CEILING.md
print(expect_refusal(["864 quads", str(fitops.MAX_PATCHES), "13826 display vertices", "12288",
                      "Size too large for VertexSet::Create()"], fitops.fit_cage, V, many, X))
assert (fitops.MAX_PATCHES, fitops.SPLIT_UV_PATCHES, fitops.COMBINABLE_PATCHES) == (767, 491, 263)   # 263: budgets re-derived 2026-11-15
print(expect_refusal(["no vertices"], fitops.fit_cage, V, faces, np.zeros((0, 3))))
# few points are not refused (Own Quads fits a mesh's own vertices, about one per patch): the count is reported
r_few = fitops.fit_cage(V, faces, X[:100], iters=3)
assert abs(r_few.points_per_patch - 100 / 96) < 1e-9 and abs(r.points_per_patch - 6000 / 96) < 1e-9
assert r_few.after.rms < r_few.before.rms
print(f"100 points on 96 patches: {r_few.after.text()} (start {r_few.before.rms_pct:.2f} %)")
bad = X.copy(); bad[3, 1] = np.nan
print(expect_refusal(["non-finite"], fitops.fit_cage, V, faces, bad))
print(expect_refusal(["6000 target vertices"], fitops.fit_cage, V, faces, X, normals=N[:10]))
print(expect_refusal(["coincide"], fitops.fit_cage, V, faces, np.tile([[1.0, 2.0, 3.0]], (400, 1))))
# diverged: the "target" is the initial surface itself, so the start is already exact (rms 0) and the seam term,
# which the initial ACC surface does not satisfy exactly at extraordinary corners, can only make it worse
X0, N0 = sample_surface(pool0, patches, 6000, rng)
print(expect_refusal(["the fit diverged"], fitops.fit_cage, V, faces, X0, normals=N0, smooth=5.0, iters=2))

print(f"PASS fit_mesh_test in {time.perf_counter() - T0:.1f} s")
