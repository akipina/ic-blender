"""Vectorised, dirty-patch-aware bicubic tessellator for the live viewport.

``core.tessellate`` is the correctness reference (``tests/fastsurf_parity.py``). Topology is built once (the cell
diagonal follows the game's rest-pose rule) and only moved patches are re-evaluated. Vertices are laid out per patch
(block ``p`` = rows ``p*n*n : (p+1)*n*n``); normals are the engine's (``ICParse.codecs.bezier.SurfaceTopology``).
"""
from __future__ import annotations

import numpy as np

from ICParse.codecs import bezier as BE


def _bernstein_grid(n: int) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, n)
    return np.array([BE.bernstein(t) for t in ts])          # (n, 4)


def _dbernstein_grid(n: int) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, n)
    return np.array([BE.dbernstein(t) for t in ts])         # (n, 4)


class TopoCache:
    """Constant topology (triangles, adjacency, the engine's normal weld) for a fixed patch set +
    density. ``types`` are the patches' smoothing-group words (None = all one group)."""

    def __init__(self, patches: np.ndarray, n: int, types=None):
        self.patches = np.asarray(patches, dtype=np.int64)   # (Np, 16)
        self.n = int(n)
        self.Np = int(len(self.patches))
        self.nn = self.n * self.n
        self.B = _bernstein_grid(self.n)
        self.dB = _dbernstein_grid(self.n)
        if types is not None and len(types) != self.Np:
            types = None
        self.surf = BE.SurfaceTopology(self.patches, types, self.n)
        self._NA = np.zeros((self.Np * self.nn, 3))          # analytic normals of the last evaluation

        # triangles: the engine splits each cell along a diagonal chosen from the BIND-pose grid
        # (ICParse.codecs.bezier.cell_split); until the first evaluation use the main-diagonal pattern
        self.local_tris = BE.cell_triangles(self.n, 1)         # (K, 3) one patch, main diagonal
        self.tris = BE.cell_triangles(self.n, self.Np)         # (Np*K, 3), replaced by eval_all/eval_dirty
        self._anti = None

        # vertex index -> patch rows that reference it (for dirty mapping)
        self.vert_to_patches: dict[int, list[int]] = {}
        for p in range(self.Np):
            for vi in self.patches[p]:
                self.vert_to_patches.setdefault(int(vi), []).append(p)

    def _eval_block(self, verts: np.ndarray, rows: np.ndarray):
        """Evaluate the given patch rows -> (len(rows), nn, 3) positions and UNIT ANALYTIC normals."""
        P = verts[self.patches[rows]].reshape(len(rows), 4, 4, 3)
        S = np.einsum("ai,bj,pijk->pabk", self.B, self.B, P)      # (R, n, n, 3)
        du = np.einsum("ai,bj,pijk->pabk", self.dB, self.B, P)
        dv = np.einsum("ai,bj,pijk->pabk", self.B, self.dB, P)
        Nrm = np.cross(du, dv).reshape(len(rows), self.nn, 3)
        ln = np.linalg.norm(Nrm, axis=2, keepdims=True); ln[ln == 0] = 1.0
        return S.reshape(len(rows), self.nn, 3), Nrm / ln

    def _split(self, rest_verts, surfV):
        """Recompute the per-cell diagonal from the rest-pose grid (what the game builds its mesh from)."""
        if rest_verts is not None:
            R = np.asarray(rest_verts, dtype=np.float64)
            P = np.einsum("ai,bj,pijk->pabk", self.B, self.B, R[self.patches].reshape(self.Np, 4, 4, 3)).reshape(-1, 3)
        else:
            P = surfV
        anti = BE.cell_split(P, self.n)
        if self._anti is None or not np.array_equal(anti, self._anti):
            self._anti = anti
            self.tris = BE.cell_triangles(self.n, self.Np, anti)
            return True
        return False

    def eval_all(self, verts: np.ndarray, rest_verts=None):
        """Full surface (M,3) positions + engine normals from current control points. ``rest_verts``
        (the un-posed control points) decide the cell split when ``verts`` are posed."""
        verts = np.asarray(verts, dtype=np.float64)
        if self.Np == 0:
            z = np.zeros((0, 3)); return z, z
        S, NA = self._eval_block(verts, np.arange(self.Np))
        self._NA = NA.reshape(-1, 3)
        V = S.reshape(-1, 3)
        self._split(rest_verts, V)
        return V, self.surf.weld(self._NA)

    def eval_dirty(self, verts, surfV, surfN, dirty_patch_rows, rest_verts=None) -> bool:
        """Re-evaluate only ``dirty_patch_rows`` in place inside surfV/surfN (the normal weld is
        redone for the seams those patches touch, so neighbours shade consistently). Returns True
        when the triangle split changed (the caller must rebuild its index batch)."""
        rows = np.asarray(sorted(set(int(r) for r in dirty_patch_rows)), dtype=np.int64)
        if len(rows) == 0:
            return False
        S, NA = self._eval_block(np.asarray(verts, dtype=np.float64), rows)
        for k, p in enumerate(rows):
            surfV[p * self.nn:(p + 1) * self.nn] = S[k]
            self._NA[p * self.nn:(p + 1) * self.nn] = NA[k]
        self.surf.weld(self._NA, out=surfN, patches=rows)
        return self._split(rest_verts, surfV)

    def patches_for_dirty_verts(self, vert_ids) -> set:
        out: set = set()
        for vi in vert_ids:
            out.update(self.vert_to_patches.get(int(vi), ()))
        return out
