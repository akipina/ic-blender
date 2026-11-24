"""Limb and bone colouring + selection helpers (pure / numpy: the draw module turns these arrays into GPU batches)."""
from __future__ import annotations

import colorsys

import numpy as np

_GRAY = np.array([0.55, 0.55, 0.58], dtype=np.float64)


def palette(n: int) -> np.ndarray:
    """(n,3) visually distinct colours (golden-ratio hue spacing)."""
    cols = np.empty((max(n, 1), 3), dtype=np.float64)
    for i in range(max(n, 1)):
        h = (i * 0.6180339887) % 1.0
        cols[i] = colorsys.hsv_to_rgb(h, 0.55, 0.95)
    return cols


def vertex_limb_colors(patch_limb: np.ndarray, n_limbs: int, density: int, n_surf: int) -> np.ndarray:
    """(M,3) per-surface-vertex base colour by the limb of each vertex's patch (gray if none)."""
    pal = palette(n_limbs)
    nn = density * density
    vpatch = np.arange(n_surf) // nn
    vlimb = patch_limb[vpatch] if len(patch_limb) else np.full(n_surf, -1)
    out = np.tile(_GRAY, (n_surf, 1))
    has = vlimb >= 0
    out[has] = pal[np.clip(vlimb[has], 0, len(pal) - 1)]
    return out


# --- By Bone colouring (Boner's skin view) -----------------------------------------------------------------------
UNASSIGNED_COLOR = np.array([1.0, 0.42, 0.0], dtype=np.float64)     # Boner's orange: a point with no bone
_DIM_GREY = np.array([0.42, 0.42, 0.45], dtype=np.float64)


def bone_palette(n: int) -> np.ndarray:
    """(n, 3) colours, one per SOURCE bone index and stable for it (bone i's colour never depends on how many bones
    there are): golden-ratio hues, kept clear of the unassigned orange."""
    cols = np.empty((max(int(n), 0), 3), dtype=np.float64)
    for i in range(len(cols)):
        h = (0.55 + i * 0.6180339887) % 1.0
        if 0.02 <= h <= 0.14:                       # orange-ish: pushed to yellow-green / magenta, never Boner's orange
            h = h + 0.14 if h >= 0.08 else h - 0.10
        v = 0.95 if i % 2 == 0 else 0.78            # neighbours in index differ in value too
        cols[i] = colorsys.hsv_to_rgb(h % 1.0, 0.62, v)
    return cols


def bone_color_table(n_bones: int, active: int = -1, dim: float = 0.35) -> np.ndarray:
    """(n_bones + 1, 3): row b = bone b's display colour, the last row the unassigned orange. With an active bone
    (``active`` >= 0) every other bone is dimmed toward grey; the active bone and unassigned points stay bright."""
    pal = bone_palette(n_bones)
    if 0 <= int(active) < n_bones:
        keep = np.zeros(n_bones, dtype=bool)
        keep[int(active)] = True
        pal[~keep] = (pal[~keep] * dim + _DIM_GREY * (1.0 - dim)) * 0.75
    return np.vstack([pal, UNASSIGNED_COLOR[None, :]])


def _owner_rows(owners, n_bones) -> np.ndarray:
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    return np.where((own >= 0) & (own < n_bones), own, n_bones)      # -1 / unresolved -> the orange row


def point_bone_colors(owners, n_bones: int, active: int = -1) -> np.ndarray:
    """(N, 4) float32 RGBA per control point by its owner bone (orange when it has none)."""
    tab = bone_color_table(n_bones, active)
    out = np.ones((len(np.asarray(owners).reshape(-1)), 4), dtype=np.float32)
    out[:, :3] = tab[_owner_rows(owners, n_bones)]
    return out


def sample_cp_map(density: int) -> np.ndarray:
    """(d*d,) for each surface sample of a patch (row-major, sample (i, j) = row i of the evaluator's grid), the
    patch-local index 0..15 of its nearest control point in the 4x4 grid: the one whose Greville abscissa (k/3) is
    nearest the sample's parameters, ``floor(3i/(d-1) + 0.5) * 4 + floor(3j/(d-1) + 0.5)`` (a half rounds up)."""
    d = int(density)
    k = np.floor(3.0 * np.arange(d) / max(d - 1, 1) + 0.5).astype(np.int64)
    return (k[:, None] * 4 + k[None, :]).reshape(-1)


def vertex_bone_colors(owners, patches, density: int, n_bones: int, active: int = -1) -> np.ndarray:
    """(Np*d*d, 3) base colour of every surface sample: the owner bone of its nearest control point in its patch's
    4x4 grid (:func:`sample_cp_map`), orange for a point with no bone or an unresolved owner."""
    p = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    tab = bone_color_table(n_bones, active)
    if not len(p):
        return np.zeros((0, 3))
    cps = p[:, sample_cp_map(density)].reshape(-1)                   # pool index per sample
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    valid = (cps >= 0) & (cps < len(own))
    so = np.full(len(cps), -1, dtype=np.int64)
    so[valid] = own[cps[valid]]
    return tab[_owner_rows(so, n_bones)]


def lit_colors(base, normals, alpha: float) -> np.ndarray:
    """(M, 4) float32: a base colour shaded by the overlay's two fixed lights (the By Limb / By Bone look)."""
    l1 = np.array([0.4, 0.3, 0.86]); l1 /= np.linalg.norm(l1)
    l2 = np.array([-0.5, -0.4, 0.3]); l2 /= np.linalg.norm(l2)
    n = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    sh = np.clip(n @ l1, 0, 1) * 0.7 + np.clip(n @ l2, 0, 1) * 0.3
    col = np.empty((len(n), 4), dtype=np.float32)
    col[:, :3] = (np.asarray(base, dtype=np.float64) * (0.25 + 0.75 * sh)[:, None]).astype(np.float32)
    col[:, 3] = alpha
    return col
