"""Bpy-free engine for bicubic-Bezier patch editing and the only module that talks to ICParse (keep bpy out of it).

A creature ``.sgm`` is a flat control-point pool plus patches of 16 pool indices (a shared index is C0 continuity).
The editable object's vertices ARE the pool, 1:1 and in order, so an untouched round trip is the identity: never
reorder or regenerate it. Points are bone-local in the file (``ICParse.codecs.reconstruct`` converts to world).
"""
from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ICParse.sgm import parser as sgm_parser
from ICParse import ebp as ICebp
from ICParse.codecs import reconstruct as R
from ICParse.codecs import bezier as BE
from ICParse.codecs import texture as ICtex
from ICParse.sgm.creature import layout as LAYOUT

try:
    from . import structure, boner, topology
except ImportError:                      # imported top-level (tests put ic_blender/ on sys.path)
    import structure  # type: ignore[no-redef]
    import boner  # type: ignore[no-redef]
    import topology  # type: ignore[no-redef]

# Row-major 4x4 corner indices, matching ICParse.codecs.bezier.PATCH_CORNERS / the SGM layout.
PATCH_CORNERS = BE.PATCH_CORNERS  # (0, 3, 15, 12)


# --- axis conversion: SGM (x, y, z) <-> Blender (x, -z, y) ----------------------------
# IC creatures are authored Y-up/Z-forward; Blender is Z-up. The map is linear, so we can
# tessellate directly in Blender space (Bernstein eval is a linear combination of points).

def sgm_to_blender(a: np.ndarray) -> np.ndarray:
    """(N,3) SGM coords -> (N,3) Blender coords: (x, y, z) -> (x, -z, y)."""
    a = np.asarray(a, dtype=np.float64)
    return a[:, [0, 2, 1]] * np.array([1.0, -1.0, 1.0])


def blender_to_sgm(a: np.ndarray) -> np.ndarray:
    """Inverse of :func:`sgm_to_blender`: (x, y, z) -> (x, z, -y)."""
    a = np.asarray(a, dtype=np.float64)
    return a[:, [0, 2, 1]] * np.array([1.0, 1.0, -1.0])


# --- loaded-creature container --------------------------------------------------------

@dataclass
class CreatureData:
    """Everything the Blender layer needs to build an editable object and draw it.

    ``verts`` are Blender-space control points (one per pool index, same order as the
    file's vertex pool). ``patches`` is (Np, 16) int row-major indices into ``verts``.
    """
    name: str
    verts: np.ndarray                       # (Nv, 3) float64, Blender space
    patches: np.ndarray                     # (Np, 16) int64, row-major into verts
    edges: List[Tuple[int, int]]            # control-net edges (4x4 grid, deduped)
    patch_mat: np.ndarray                   # (Np,) int   material slot per patch (for bake)
    patch_uv: np.ndarray                    # (Np, 8) float corner UVs (for bake)
    source_path: str = ""
    model: object = None                    # the parsed SgmModel (textures/materials/rig)

    @property
    def n_verts(self) -> int:
        return int(len(self.verts))


def _patch_grid_edges(patches: np.ndarray) -> List[Tuple[int, int]]:
    """Control-net edges: connect 4x4 grid neighbours within every patch, deduped.

    Shared boundary control points are shared pool indices, so adjacent patches' edges
    coincide and collapse to one -> a clean, watertight control net."""
    seen = set()
    out: List[Tuple[int, int]] = []
    for g in patches:
        for r in range(4):
            for c in range(4):
                i = int(g[r * 4 + c])
                if c < 3:
                    j = int(g[r * 4 + c + 1])
                    e = (i, j) if i < j else (j, i)
                    if i != j and e not in seen:
                        seen.add(e); out.append(e)
                if r < 3:
                    j = int(g[(r + 1) * 4 + c])
                    e = (i, j) if i < j else (j, i)
                    if i != j and e not in seen:
                        seen.add(e); out.append(e)
    return out


def resolve_source(path: str) -> str:
    """The path to record as a creature's lossless source. A ``.sgm`` is itself. A ``.ebp`` is
    resolved by ``ICParse.ebp``: a proxy blueprint (``FORM XSGM``; telluriandata's stock creatures)
    resolves to the same-named ``.sgm`` beside it; a blueprint that embeds its creature stays the
    ``.ebp`` and :func:`read_source` extracts the ``FORM BOBJ`` on every read. Profiles and proxies
    with no sibling raise with a message that says so."""
    if not path.lower().endswith(".ebp"):
        return path
    kind, src = ICebp.resolve_sgm(path)
    return src if kind == "external" else path


# Parsed creature sources, keyed by absolute path and validated by the file's stat (mtime, size, inode;
# for a .ebp also its sibling .sgm, which a proxy blueprint reads). One Export / Validate used to parse
# the same source 2-3 times. Entries hold a pickled snapshot, so every read hands out a private copy
# that the caller may edit (export_model does) without touching the cache.
_SOURCE_CACHE: dict = {}
_SOURCE_CACHE_MAX = 8


def _source_stamp(path: str) -> tuple:
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    if path.lower().endswith(".ebp"):
        sib = ICebp.sibling_sgm(path)
        if sib:
            s2 = os.stat(sib)
            stamp += (sib, s2.st_mtime_ns, s2.st_size, s2.st_ino)
    return stamp


def clear_source_cache() -> None:
    """Forget every parsed source (the next read re-parses)."""
    _SOURCE_CACHE.clear()
    _STUB_CACHE.clear()


_STUB_CACHE: dict = {}


def source_stub_bones(path: str) -> frozenset:
    """The stub bone indices of the creature behind ``path`` (``structure.stub_bone_indices``), parsed once per file
    version: what the panel and the skin tools give the add rule (a bone under a stub bone is refused) without reading
    the source on every redraw. Raises what :func:`read_source` raises."""
    key = os.path.abspath(path)
    stamp = _source_stamp(path)
    hit = _STUB_CACHE.get(key)
    if hit is None or hit[0] != stamp:
        hit = (stamp, frozenset(int(i) for i in structure.stub_bone_indices(read_source(path))))
        _STUB_CACHE.pop(key, None)
        _STUB_CACHE[key] = hit
        while len(_STUB_CACHE) > _SOURCE_CACHE_MAX:
            _STUB_CACHE.pop(next(iter(_STUB_CACHE)))
    return hit[1]


def _parse_source(path: str):
    if path.lower().endswith(".ebp"):
        _kind, src = ICebp.resolve_sgm(path)      # bytes (embedded) or the sibling .sgm path
        return sgm_parser.read(src)
    return sgm_parser.read(path)


def read_source(path: str):
    """Parse the creature behind ``path``: a ``.sgm`` directly, or the creature a ``.ebp`` embeds.

    Creature models are parsed once per file version (see ``_SOURCE_CACHE``); every call returns a
    private copy, so callers may edit it. Scene models are not cached."""
    key = os.path.abspath(path)
    stamp = _source_stamp(path)
    hit = _SOURCE_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return pickle.loads(hit[1])
    m = _parse_source(path)
    if not is_scene_model(m):
        _SOURCE_CACHE.pop(key, None)
        _SOURCE_CACHE[key] = (stamp, pickle.dumps(m, protocol=pickle.HIGHEST_PROTOCOL))
        while len(_SOURCE_CACHE) > _SOURCE_CACHE_MAX:
            _SOURCE_CACHE.pop(next(iter(_SOURCE_CACHE)))
    return m


def load_creature(path: str) -> CreatureData:
    """Parse a creature ``.sgm`` (or the creature a ``.ebp`` carries) into Blender-space control
    points + patch topology."""
    return creature_arrays_from_model(read_source(path), path)


def creature_arrays_from_model(m, source_path: str = "") -> CreatureData:
    """Blender-space control points + patch topology of an in-memory creature model (a parsed source,
    an export model, a combine result). Nothing is read from disk."""
    if not hasattr(m, "vertices") or not getattr(m, "patches", None):
        raise ValueError(f"{source_path or getattr(m, 'name', 'model')}: not a creature .sgm "
                         "(no control-point patches)")
    world_sgm = R.world_vertices(m)                       # (Nv, 3) bind-pose world, SGM space
    verts = sgm_to_blender(world_sgm)
    patches = np.array([list(p.control_points) for p in m.patches], dtype=np.int64)
    patch_mat = np.array([int(p.material_index) for p in m.patches], dtype=np.int64)
    patch_uv = np.array([list(p.uvs) for p in m.patches], dtype=np.float64)
    return CreatureData(
        name=getattr(m, "name", "Creature") or "Creature",
        verts=verts, patches=patches, edges=_patch_grid_edges(patches),
        patch_mat=patch_mat, patch_uv=patch_uv, source_path=source_path, model=m,
    )


# --- control-point classification -----------------------------------------------------

def corner_mask(patches: np.ndarray, n_verts: int) -> np.ndarray:
    """Boolean (n_verts,) -- True for ON-surface control points (patch corners 0,3,15,12).

    Corners lie ON the bicubic surface; the other 12 per patch are off-surface tangent /
    interior handles. A shared pool point is a corner iff it appears at a corner index in
    any patch (interior handles are never shared, edge handles never sit at a corner slot)."""
    mask = np.zeros(int(n_verts), dtype=bool)
    p = np.asarray(patches, dtype=np.int64)
    if len(p):
        mask[p[:, list(PATCH_CORNERS)].reshape(-1)] = True
    return mask


# --- display tessellation -------------------------------------------------------------

def surface_uvs(patch_uv: np.ndarray, n: int) -> np.ndarray:
    """Per-surface-vertex UVs (M,2) by bilinear interpolation of each patch's 4 corner UVs.

    Matches the tessellation grid order (vertex a*n+b within a patch block) and the corner order the
    ENGINE uses (verified against the Object Editor's own vertex stream, 2026-09-13): patch.uvs holds
    the corners in control-point-slot order 0, 3, 12, 15 = grid (0,0), (0,1), (1,0), (1,1). V is
    flipped to Blender's bottom-up convention."""
    pu = np.asarray(patch_uv, dtype=np.float64).reshape(-1, 4, 2)
    Np = len(pu)
    ts = np.linspace(0.0, 1.0, n)
    Sf, Tf = np.meshgrid(ts, ts, indexing="ij")
    w = np.stack([((1 - Sf) * (1 - Tf)).ravel(), ((1 - Sf) * Tf).ravel(),
                  (Sf * (1 - Tf)).ravel(), (Sf * Tf).ravel()], axis=1)   # (nn, 4): slots 0,3,12,15
    out = np.einsum("gc,pcd->pgd", w, pu).reshape(Np * n * n, 2)         # (M, 2)
    out[:, 1] = 1.0 - out[:, 1]
    return out


def tessellate(verts_blender: np.ndarray, patches: np.ndarray, n: int = 5, rest_blender=None):
    """Tessellate the exact bicubic surface from current Blender-space control points.

    Returns (V (M,3), T (T,3), N (M,3)) ready for a GPU batch. Delegates to ICParse's
    single-source-of-truth Bernstein evaluator -- no approximation beyond density ``n``. Cells are
    split along the diagonal the game picks (from the rest-pose grid: pass ``rest_blender`` when the
    control points are posed; the axis map is a rotation, so the rule holds in Blender space)."""
    return BE.tessellate(np.asarray(verts_blender, dtype=np.float64),
                         [row for row in np.asarray(patches, dtype=np.int64)], n=max(2, int(n)),
                         split="engine", split_cp=rest_blender)


def engine_normals(verts_blender: np.ndarray, patches: np.ndarray, patch_types=None, n: int = 5):
    """Per-surface-vertex normals exactly as the game shades the surface (ICParse's engine-verified
    rule: analytic normals, welded along shared edges and, at corners, within a smoothing group).
    The SGM->Blender axis map is a rotation, so evaluating on Blender-space control points gives
    Blender-space normals directly."""
    types = None
    if patch_types is not None and len(patch_types) == len(patches):
        types = [int(t) for t in patch_types]
    return BE.engine_normals(np.asarray(verts_blender, dtype=np.float64),
                             np.asarray(patches, dtype=np.int64), types, max(2, int(n)))


def tessellate_engine(verts_blender: np.ndarray, patches: np.ndarray, patch_types=None, n: int = 5, rest_blender=None):
    """:func:`tessellate` with engine normals: (V, T, N)."""
    V, T, _ = tessellate(verts_blender, patches, n, rest_blender)
    return V, T, engine_normals(verts_blender, patches, patch_types, n)


# --- validation (reuses ICParse load-gate checks) -------------------------------------

# Message prefixes that do NOT stop a creature loading: advisories, stock-rulebook gaps, and combiner
# findings ("won't combine right" is not "won't load").
NON_BLOCKING_PREFIXES = ("WARN", "STOCK", "COMBINE")


def blocking(problems) -> list:
    """The validation messages that would stop the creature loading (no ``WARN``/``STOCK``/``COMBINE``
    prefix)."""
    return [p for p in problems if not p.startswith(NON_BLOCKING_PREFIXES)]


def validate_model(model, edited_verts=None) -> list:
    """Problems that would stop a creature loading in the Object Editor, plus the stock rulebook gaps
    (``STOCK ...``) and advisories (``WARN ...``); see :func:`blocking`.

    ``model`` is the creature as it would be written: Validate passes the in-memory export model
    (:func:`export_model` / :func:`export_grown_model`), so limb, ring, skeleton and Action edits are
    checked, and a grown creature is judged by what Export writes. Topology gates (watertight /
    dangling edges, the 12288 display-vertex ceiling and the combine budget, index ranges) come from ICParse's single source of
    truth. ``edited_verts`` (Blender-space control points) adds the finite-position check."""
    from ICParse import validate as ICval
    m = model
    problems = list(ICval.check_model(m))
    # The stock-creature rulebook (clips, bones, limb ids, sockets, textures, IK chains): what every
    # base-game creature has BEYOND loading. Its universal items come back as "STOCK ..." (a creature
    # missing them still loads -- the Tellurian add-on creatures prove it -- but is not a complete stock
    # creature); its common items stay "WARN ...". Only unprefixed messages block loading.
    for p in ICval.check_creature_rulebook(m, include_structural=False):
        problems.append("STOCK " + p[len("ERROR "):] if p.startswith("ERROR ") else p)
    problems += host_bone_problems(m)
    problems += topology.ik_chain_problems(m)            # every IK chain one path of the hierarchy (C8.5; 905/905 stock)
    problems += ICval.check_limb_bones(m)               # every bone in exactly one limb (STOCK)
    oe = ICval.check_oe_structure(m)                    # the Object Editor's own limb warnings (never blocking)
    problems = [p for p in problems if not _oe_loads_unknown_limb_bone(p, oe)]
    problems += oe
    finite = np.isfinite(np.asarray(m.vertices, dtype=np.float64)).all()
    if edited_verts is not None:
        finite = finite and np.isfinite(np.asarray(edited_verts, dtype=np.float64)).all()
    if not finite:
        problems.append("non-finite (NaN/inf) control-point positions")
    return problems


_UNKNOWN_LIMB_BONE = re.compile(r"^limb (.*): references unknown bone '(.*)'$")


def _oe_loads_unknown_limb_bone(problem, oe_lines) -> bool:
    """True for ICParse check_model's ``limb X: references unknown bone 'b'`` when the Object Editor's own warning for
    it is among ``oe_lines``: the editor LOADS such a creature and only warns (harness 2026-09-15: cheetah whose new
    LFArm stub limb and socket name a deleted front_arm1_l -> LOADED), so the line must not block loading; the OE line
    (WARN) and the combiner precheck's COMBINE ERROR (ComboTest.exe fails on it) still report it."""
    hit = _UNKNOWN_LIMB_BONE.match(problem)
    if not hit:
        return False
    want = f"WARN oe: Bone '{hit.group(2)}' referenced by limb '{hit.group(1)}' was not found in skeleton"
    return want in oe_lines


def host_bone_problems(m) -> list:
    """``STOCK`` lines for connections whose host bone owns no control points. Measured: on the 61 base-game creatures
    all 730 connections' host bones own points (a limb grafted on a bone its own points don't follow); some Tellurian
    add-on creatures have empty hosts for limbs they lack (tuna's arms and wings), and they load, so it never blocks.
    A Boner Remove / Assign / Auto Skin can empty a host bone, which this line reports."""
    bones = {str(b.name).lower(): b for b in m.bones}
    out = []
    for c in getattr(m, "limb_connections", []) or []:
        b = bones.get(str(c.bone_name).lower())
        if b is not None and int(b.vertex_count) == 0:
            out.append(f"STOCK connection '{c.name}': its host bone '{b.name}' owns no control points (every "
                       "base-game creature's connection host bones own some): assign it the control points around "
                       "the connection")
    return out


def validate_creature(source_path: str, edited_verts: np.ndarray) -> list:
    """:func:`validate_model` on the SOURCE file with the edited control points' count and finiteness
    checked (for callers without an export model; the Validate operator uses the export model, which
    also covers grown creatures and every other edit)."""
    m = read_source(source_path)
    problems = validate_model(m, edited_verts)
    edited = np.asarray(edited_verts, dtype=np.float64)
    if len(edited) != len(m.vertices):
        problems.append(f"vertex count changed ({len(edited)} != {len(m.vertices)}) -- "
                        "adding/removing control points is not supported")
    return problems


# --- structure checks -----------------------------------------------------------------
# Ring formats / sizes, limb classes, host and canonical bones are the combiner's rules: they live in
# combotest.validate.precheck (the COMBINE lines Validate adds), not here -- one rule, one place.


def check_structure(limb_names, patch_limb, connections, bone_names, limb_ids) -> list:
    """Add-on state warnings (all prefixed 'WARN'): every patch in a limb that exists, Torso/Head/Tail present BY CLASS
    (``limb_ids``; a limb's name is free text), connection parent bones exist. How the creature combines (ring formats
    and sizes, classes, bones) is checked by the combine tier (``combotest.validate.precheck``, the ``COMBINE`` lines of
    Validate); the Object Editor's limb checks are ``ICParse.validate.check_oe_structure`` (``WARN oe:``)."""
    warns = []
    pl = np.asarray(patch_limb, dtype=np.int64)
    if len(pl):
        orphan = int((pl < 0).sum())
        if orphan:
            warns.append(f"WARN {orphan} patch(es) not assigned to any limb")
        beyond = int((pl >= len(limb_names)).sum())
        if beyond:
            warns.append(f"WARN {beyond} patch(es) name a limb that does not exist (the table has {len(limb_names)})")
    classes = {int(i) & CLASS_MASK for i in (limb_ids or [])}
    for need, code in (("Torso", 0x800), ("Head", 0x40), ("Tail", 0x80)):
        if limb_names and code not in classes:
            warns.append(f"WARN no {need} limb (class 0x{code:x}): creatures need Torso/Head/Tail to combine")
    bn_low = {b.lower() for b in bone_names}
    for name, bone, _verts in connections:
        if bone and bone.lower() not in bn_low:
            warns.append(f"WARN connection '{name}' parent bone '{bone}' is not in the skeleton")
    return warns


# --- bilateral symmetry ---------------------------------------------------------------

def mirror_pairs(verts: np.ndarray, axis: int = 0, eps: float = 1e-3):
    """Map each control point to its mirror partner across ``axis`` (positions, not topology).

    Returns (pairs {i: j}, on_plane [indices on the mirror plane]). Matching is spatial, so
    a partially symmetric creature pairs only the verts that have a partner: the nearest point within
    ``eps`` on every axis of the mirror image (points are bucketed on an ``eps`` grid and the 27
    neighbouring cells are searched, so a partner a hair across a cell boundary still pairs -- a fitted
    near-symmetric net missed 6 of 866 with a same-cell key)."""
    V = np.asarray(verts, dtype=np.float64)
    q = np.floor(V / eps).astype(np.int64)
    cells = {}
    for i, row in enumerate(q):
        cells.setdefault(tuple(row), []).append(i)
    flip = np.ones(3); flip[axis] = -1.0
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    pairs, on_plane = {}, []
    for i in range(len(V)):
        m = V[i] * flip
        c = np.floor(m / eps).astype(np.int64)
        best, best_d = None, None
        for off in offsets:
            for j in cells.get((c[0] + off[0], c[1] + off[1], c[2] + off[2]), ()):
                diff = np.abs(V[j] - m)
                if diff.max() <= eps:
                    d = float(diff.max())
                    if best is None or d < best_d or (d == best_d and j == i):
                        best, best_d = j, d
        if best is None:
            continue
        if best == i:
            on_plane.append(i)
        else:
            pairs[i] = best
    return pairs, on_plane


def symmetrize(verts: np.ndarray, axis: int = 0, eps: float = 1e-3) -> np.ndarray:
    """Snap a near-symmetric control net to exact bilateral symmetry (averaging partners)."""
    out = np.asarray(verts, dtype=np.float64).copy()
    pairs, on_plane = mirror_pairs(out, axis, eps)
    flip = np.ones(3); flip[axis] = -1.0
    for i, j in pairs.items():
        if i < j:
            tgt = (out[i] + out[j] * flip) / 2.0
            out[i] = tgt
            out[j] = tgt * flip
    for i in on_plane:
        out[i, axis] = 0.0
    return out


# --- G1 seam smoothing ----------------------------------------------------------------
# Each patch side (4 boundary control points) and the inner row of tangent handles beside it,
# positionally matched (side[k] <-> inner[k]), in row-major 4x4 indices.
_SIDE_INNER = (
    ([0, 1, 2, 3], [4, 5, 6, 7]),
    ([12, 13, 14, 15], [8, 9, 10, 11]),
    ([0, 4, 8, 12], [1, 5, 9, 13]),
    ([3, 7, 11, 15], [2, 6, 10, 14]),
)


def _seams(patches: np.ndarray):
    """Internal seams: (boundary pool indices, innerA, innerB) for sides shared by 2 patches."""
    groups: dict = {}
    for p in patches:
        for side, inner in _SIDE_INNER:
            b = [int(p[s]) for s in side]
            groups.setdefault(frozenset(b), []).append((b, [int(p[s]) for s in inner]))
    out = []
    for entries in groups.values():
        if len(entries) != 2:
            continue
        (bA, inA), (bB, inB) = entries
        posB = {idx: k for k, idx in enumerate(bB)}
        innerB_aligned = [inB[posB[idx]] for idx in bA]    # align B to A's boundary order
        out.append((bA, inA, innerB_aligned))
    return out


def seam_smooth(verts, patches, selected_mask=None):
    """C1-smooth shared seams: reflect each side's inner tangent handles about the boundary so
    the cross-seam tangents match.

    Local & predictable: an edge handle (e.g. index 4) is also a *boundary* point of the
    perpendicular seam, so moving it would reshape that neighbour's boundary curve too. (C0 is
    never at risk -- shared boundary points are a single shared vertex, so they always move
    together and the surface stays watertight regardless of position.) To keep a 'smooth this
    seam' click from rippling into untouched seams, we move only genuinely interior handles and
    leave shared boundaries fixed. If ``selected_mask`` is given, only seams whose boundary
    points are all selected are smoothed. Returns (new_verts, seams_smoothed)."""
    out = np.asarray(verts, dtype=np.float64).copy()
    seams = _seams(np.asarray(patches, dtype=np.int64))
    shared = {i for boundary, _, _ in seams for i in boundary}    # shared boundaries -> leave fixed
    count = 0
    for boundary, inA, inB in seams:
        if selected_mask is not None and not all(selected_mask[i] for i in boundary):
            continue
        moved = False
        for P, a, b in zip(boundary, inA, inB):
            if a in shared or b in shared:                       # protect watertight boundaries
                continue
            d = ((out[P] - out[a]) + (out[b] - out[P])) / 2.0
            out[a] = out[P] - d
            out[b] = out[P] + d
            moved = True
        if moved:
            count += 1
    return out, count


# --- lossless export ------------------------------------------------------------------

def _patch_limb_of(model) -> np.ndarray:
    pl = np.full(len(model.patches), -1, dtype=np.int64)
    for li, limb in enumerate(model.limbs):
        for p in limb.prims:
            if 0 <= int(p) < len(pl):
                pl[int(p)] = li
    return pl


# --- skinning layout (the stock rule for bone vertex ranges) ------------------------------------------------
# Every stock creature (61 base-game + 55 add-on stock; tests/boner_test.py pins it) lists, inside each bone's
# contiguous vertex range, the points that are patch corners (control points 0, 3, 12, 15 of some patch) FIRST, and
# stores their count in the BONE record's third count word (``aux_count``, ICParse ``Bone.meta5``). Points no bone owns
# (woolly_mammoth's two unused points) sit before every range. The reskin and grown exports write that layout, and so
# does Export of a source that arrives off it (``build_export``).
# The rule is the game's, not a convention (IC.exe, decompiled 2026-11-15): at its coarsest level the renderer poses
# only the first aux_count points of each range (Skeleton::UpdateMesh -> PatchMeshObject::SetupLevel0Vertices), and
# its combiner builds this layout. The helpers live in ICParse (``ICParse.sgm.creature.layout``), shared with
# ``build_creature`` and ``check_model``; the names here are kept for the add-on and its tests.


def unowned_patch_points(owners, patches) -> np.ndarray:
    """Pool indices of the control points a patch uses that have no bone (owner < 0), sorted. The engine asserts every
    patch control point has a bone (``vertBoneIndex >= 0``), so Export refuses them; unused unowned points are fine."""
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    p = np.asarray(patches, dtype=np.int64).reshape(-1)
    if not len(p) or not len(own):
        return np.zeros(0, dtype=np.int64)
    used = np.unique(p[(p >= 0) & (p < len(own))])
    return used[own[used] < 0]


skin_order = LAYOUT.skin_order          # by owner (unowned first), patch corners first, then pool index
aux_counts = LAYOUT.corner_counts       # per bone: how many patch-corner points it owns (the stock aux_count)


def aux_rule_problems(m) -> List[str]:
    """Where a creature model breaks the stock skinning layout (empty = it follows it): a bone whose ``aux_count``
    (``meta5``) is not the number of patch corners in its range, or runs past it, or whose range does not list those
    corners first (``ICParse.sgm.creature.layout.layout_problems``)."""
    return [text for _kind, text in LAYOUT.layout_problems(m)]


def _layout_bones(m, sorted_owners, owners, corner) -> None:
    """Rebuild each bone's vertex range from the sorted owners and recompute ``aux_count`` (``meta5``) for EVERY bone.
    A reskin or grown export re-sorts the whole pool into the stock layout (:func:`skin_order`: corners first in every
    bone's range), so every bone's word is re-derived from that layout: the stock rule holds on 61/61 base-game
    creatures, so their words come out unchanged, and a file off the rule comes out on it. ``owners`` / ``corner``:
    per pool point in the pool's old order; ``sorted_owners``: per point in file order."""
    own = np.asarray(owners, dtype=np.int64)
    cm = np.asarray(corner, dtype=bool)
    so = np.asarray(sorted_owners, dtype=np.int64)
    LAYOUT.lay_out_bones(m.bones, so, cm[skin_order(own, cm)])   # sorted_owners is owners in skin order


def _unowned_local(src_local, src_owner, world):
    """The stored triple of a point with no bone. ICParse (``world_vertices``: no bone frame) and the add-on place such
    a point at its stored coordinates, so its source bytes are kept while they still put it where it is (an untouched
    unowned point, woolly_mammoth's two); otherwise (a point just removed from its bone, or moved) its position is
    written as is, so it re-imports where Blender shows it. (The game puts it at the model ORIGIN instead:
    ``PatchMeshObject::SkinVertices``, IC.exe 0x005680c4, zero-fills the posed array and moves only the bones' ranges,
    and the mesh's bound volume -- the combiner's Creature Size rule reads it -- includes the point there. Only the
    bound can differ, since no patch may use an unowned point: Export refuses that.)"""
    w = np.asarray(world, dtype=np.float64)
    if src_local is not None and src_owner < 0 and np.array_equal(np.asarray(src_local, np.float64).astype(np.float32),
                                                                   w.astype(np.float32)):
        return src_local
    return (float(w[0]), float(w[1]), float(w[2]))


def _apply_reskin(m, new_owners, world_sgm, bw) -> np.ndarray:
    """Reassign control points to bones. The format requires each bone to own a CONTIGUOUS vertex range, so the
    vertices are sorted into the stock layout (:func:`skin_order`), every patch + connection index is remapped, the
    per-bone ranges (and ``aux_count``) are rebuilt, and each vertex is re-expressed bone-local in its new owner's bind
    frame (world preserved). A point with no bone (owner -1) sits before every range, written without a frame
    (:func:`_unowned_local`). Mutates ``m`` in place (the pool must still be the source's); returns the old -> new
    index map."""
    n = len(m.vertices)
    new_owners = np.asarray(new_owners, dtype=np.int64)
    if len(new_owners) and (int(new_owners.max()) >= len(m.bones) or int(new_owners.min()) < -1):
        bad = int(new_owners.max()) if int(new_owners.max()) >= len(m.bones) else int(new_owners.min())
        raise ValueError(f"a control point is skinned to bone {bad}, but the creature has {len(m.bones)} bones")
    src_owners = np.asarray(R.vertex_bone_owners(m), dtype=np.int64)
    corner = corner_mask(source_table(m), n)
    order = skin_order(new_owners, corner)
    o2n = np.zeros(n, dtype=np.int64)
    for ni, oi in enumerate(order):
        o2n[oi] = ni
    inv = [np.linalg.inv(b) for b in bw]
    new_local = [None] * n
    for v in range(n):
        own = int(new_owners[v])
        if own < 0:
            new_local[v] = _unowned_local(m.vertices[v], int(src_owners[v]), world_sgm[v])
            continue
        w = world_sgm[v]
        l = inv[own] @ np.array([w[0], w[1], w[2], 1.0])
        new_local[v] = (float(l[0]), float(l[1]), float(l[2]))
    m.vertices = [new_local[order[ni]] for ni in range(n)]
    for p in m.patches:
        p.control_points = [int(o2n[i]) for i in p.control_points]
    for c in m.limb_connections:
        c.vertex_indices = [int(o2n[i]) for i in c.vertex_indices]
    sorted_owners = np.array([int(new_owners[order[ni]]) for ni in range(n)], dtype=np.int64)
    _layout_bones(m, sorted_owners, new_owners, corner)
    return o2n


# --- growing geometry: Extrude Patch onto an open side ---------------------------------------------------
# Core side ids of a row-major 4x4 patch: 0 = row 0, 3 = row 3, 1 = column 0, 2 = column 3.
_SIDE_CPS = {0: (0, 1, 2, 3), 3: (12, 13, 14, 15), 1: (0, 4, 8, 12), 2: (3, 7, 11, 15)}
# ICParse's loader sides (validate._LOADER_SIDES, in its order) as core side ids
_LOADER_TO_CORE = (1, 3, 2, 0)
# Where the shared side lands on the new patch, so neighbours traverse the seam in OPPOSITE directions (the stock
# orientation, check_model's same-direction rule): source side -> (new-patch slots of the shared control points in
# source side order, new-patch slots of the rows/columns at +1, +2, +3 steps outward).
_EXTRUDE_LAYOUT = {
    3: ((0, 1, 2, 3), ((4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15))),
    0: ((12, 13, 14, 15), ((8, 9, 10, 11), (4, 5, 6, 7), (0, 1, 2, 3))),
    1: ((3, 7, 11, 15), ((2, 6, 10, 14), (1, 5, 9, 13), (0, 4, 8, 12))),
    2: ((0, 4, 8, 12), ((1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15))),
}


def extrude_patch(verts_blender, patch_cp, side=3):
    """Extrude a patch from one side into a new adjacent patch (Blender space).

    Returns (new_verts (12,3), new_patch_cp (16,)): the new patch shares the chosen side's 4 control points with the
    source (watertight on that edge) and its other 12 are new, continuing the surface outward by the source's own
    step across that side. The shared side is placed so the two patches traverse it in opposite directions, like
    every stock seam (see ``_EXTRUDE_LAYOUT``). The 12 new vertices are appended to the pool at indices starting at
    ``len(verts_blender)``, in step order (+1, +2, +3), each row in the side's own control-point order."""
    if side not in _EXTRUDE_LAYOUT:
        raise ValueError(f"side must be 0, 1, 2 or 3, not {side!r}")
    g = np.asarray(verts_blender, dtype=np.float64)[np.asarray(patch_cp, dtype=np.int64)].reshape(4, 4, 3)
    sides = {0: g[0], 3: g[3], 1: g[:, 0], 2: g[:, 3]}
    steps = {0: g[0] - g[1], 3: g[3] - g[2], 1: g[:, 0] - g[:, 1], 2: g[:, 3] - g[:, 2]}
    edge, d = sides[side], steps[side]
    new_rows = np.concatenate([edge + k * d for k in (1, 2, 3)], axis=0)   # (12,3)
    base = len(verts_blender)
    shared_slots, step_slots = _EXTRUDE_LAYOUT[side]
    new_cp = [0] * 16
    for k, s in enumerate(shared_slots):
        new_cp[s] = int(patch_cp[_SIDE_CPS[side][k]])
    for r, slots in enumerate(step_slots):
        for k, s in enumerate(slots):
            new_cp[s] = base + r * 4 + k
    return new_rows, new_cp


def side_users(patches) -> dict:
    """ICParse's loader rule (``validate.side_users``): every patch side key -> the patches using it."""
    from ICParse import validate as ICval
    return ICval.side_users(np.asarray(patches, dtype=np.int64).tolist())


def patch_sides(patches):
    """Every (patch, core side id, loader side key) of a patch table."""
    from ICParse import validate as ICval
    out = []
    for pi, cps in enumerate(np.asarray(patches, dtype=np.int64).tolist()):
        for li, side in enumerate(ICval._LOADER_SIDES):
            out.append((pi, _LOADER_TO_CORE[li], ICval._edge_key(cps, side)))
    return out


def open_side_count(patches) -> int:
    """Sides used by exactly one patch (where Extrude Patch can grow)."""
    return sum(1 for u in side_users(patches).values() if len(u) == 1)


def pick_extrude_side(patches, selected):
    """The (patch, core side) to extrude from a selection, or a readable refusal (str).

    A candidate is an OPEN side (one patch uses it) whose two end corners are both selected; selecting all four
    corners of a patch selects the end corners of each of its sides. Exactly one candidate is extruded. A shared side
    is refused: extruding it would make an edge used by three patches, which the loader rejects."""
    p = np.asarray(patches, dtype=np.int64)
    sel = set(int(i) for i in selected)
    users = side_users(p)
    cands, shared = [], []
    for pi, side, key in patch_sides(p):
        cps = _SIDE_CPS[side]
        a, b = int(p[pi][cps[0]]), int(p[pi][cps[3]])
        if a not in sel or b not in sel:
            continue
        if len(users[key]) == 1:
            cands.append((pi, side))
        else:
            shared.append((pi, side, users[key]))
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        return (f"{len(cands)} open sides selected (patches {sorted({c[0] for c in cands})}): select the two corners of "
                "one open side")
    if shared:
        pi, side, u = shared[0]
        cps = [int(p[pi][c]) for c in _SIDE_CPS[side]]
        return (f"side {cps} is shared by patches {sorted(set(u))}: extruding it would give an edge used by three "
                "patches, which the loader rejects. Select the two corners of an open side (one patch uses it)")
    k = open_side_count(p)
    return (f"select the two corners of an open patch side (one used by a single patch); this creature has {k} open "
            f"side{'s' if k != 1 else ''}")


# --- connection rings (pure): slot rules and guided picking ------------------------------------------------
# A connection ring is stored as closed edge pairs over its corner loop (``ICParse ring_pairs``); the add-on holds the
# corner loop. The combiner welds a donor ring to a socket ring by index along patch sides, so a ring must be the
# slot's corner count, run along patch sides, wind into the torso and start where the stock partners start
# (``combocore.ring_fix`` applies the last two, from ``combotest.validate``).

TORSO_CODE = 0x800
CLASS_MASK = 0xFF7FFFFF          # a limb / connection class is its code without the NoSwap bit (combotest.slots): every
                                 # slot / class match uses it (never the low 16 bits: 0x20000 Mid, 0x40000 Mid 2)
_RING_NAV_EVENTS = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE",
                    "NDOF_MOTION"}


def ring_slot(flags2):
    """``(key, label, corners wanted)`` of a connection's slot (``LCON.flags2``, its class ``& 0xFF7FFFFF``: Mid and
    Mid 2 legs are different slots): ``combotest.slots`` when it imports (every combiner slot, both mid-leg pairs
    included), else ICParse's rulebook. ``(None, "0x..", None)`` for a code that is no limb slot (the torso, or an
    unknown code): no corner count is enforced then."""
    code = int(flags2) & CLASS_MASK
    try:
        from combotest.slots import slot_for_flags2
    except ImportError:
        from ICParse import validate as ICval
        key = next((k for k, v in ICval.RULEBOOK_LIMB_IDS.items() if int(v) & CLASS_MASK == code and k != "torso"),
                   None)
        if key is None:
            return None, f"0x{code:x}", None
        return key, key, ICval.RULEBOOK_SOCKETS[key] // 2 if key in ICval.RULEBOOK_SOCKETS else 4
    sl = slot_for_flags2(code)
    if sl is None or not sl.ring:
        return None, (sl.label if sl is not None else f"0x{code:x}"), None
    return sl.key, sl.label, sl.ring // 2


def attach_text(flags1) -> str:
    """A connection's owner class (``LCON.flags1``) for a readout: "torso" or its code."""
    return "torso" if int(flags1) & CLASS_MASK == TORSO_CODE else f"0x{int(flags1) & CLASS_MASK:x}"


def corner_sides(patches) -> Dict[frozenset, List[int]]:
    """Every patch side as its two end corners (``{a, b}`` over ICParse's loader sides, interior handles ignored: the
    engine finds a ring edge's patch row by its corners, ``pPatch``) -> the patches using it."""
    from ICParse import validate as ICval
    out: Dict[frozenset, List[int]] = {}
    for pi, cps in enumerate(np.asarray(patches, dtype=np.int64).reshape(-1, 16).tolist()):
        for sd in ICval._LOADER_SIDES:
            a, b = cps[sd[0]], cps[sd[3]]
            if a != b:
                out.setdefault(frozenset((a, b)), []).append(pi)
    return out


def ring_edge_error(corners, sides) -> Optional[str]:
    """The first edge of a closed corner loop that is no patch side (``corner_sides``), as text, or None."""
    k = len(corners)
    for i in range(k):
        a, b = int(corners[i]), int(corners[(i + 1) % k])
        if frozenset((a, b)) not in sides:
            return f"edge {a}-{b} is no patch side (the engine welds a ring only along patch sides, 'pPatch')"
    return None


class RingPicker:
    """Guided picking of one connection ring, bpy-free: the Pick Ring modal and Set Connection Ring both run on it.

    * candidates: the corners of the patches of the connection's OWNER limb (``flags1``, the torso for every limb
      socket; all 726 torso-owned base-game rings have only torso corners, the horse / owl antler rings the head's);
      every patch corner when that limb has no patches (with a note);
    * next: a corner joined to the last pick by ANY patch side (every stock ring edge is one), not picked yet; the
      first corner again once the ring can close;
    * preferred sides (drawn brighter): sides between an owner patch and a slot-limb patch (97% of stock limb-ring
      edges; stub rings and insect legs use others, so they are not required);
    * closes at the slot's corner count (``ring_slot``: 6 head / tail, 4 otherwise) when the last pick is joined to
      the first.
    """

    def __init__(self, patches, patch_limb, limb_ids, flags1, flags2, name="", sides=None):
        P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
        pl = np.asarray(patch_limb, dtype=np.int64) if patch_limb is not None else np.full(len(P), -1)
        if len(pl) != len(P):
            raise ValueError(f"patch limb table has {len(pl)} entries for {len(P)} patches")
        self.name = str(name)
        self.flags1, self.flags2 = int(flags1) & 0xFFFFFFFF, int(flags2) & 0xFFFFFFFF
        self.slot_key, self.slot_label, self.want = ring_slot(self.flags2)
        self.owner_code = self.flags1 & CLASS_MASK
        ids = [int(x) for x in (limb_ids or [])]
        owner_limbs = {li for li, lid in enumerate(ids) if lid & CLASS_MASK == self.owner_code}
        slot_limbs = {li for li, lid in enumerate(ids) if lid & CLASS_MASK == self.flags2 & CLASS_MASK} - owner_limbs
        self.sides = sides if sides is not None else corner_sides(P)
        self.adj: Dict[int, set] = {}
        for key in self.sides:
            a, b = tuple(key)
            self.adj.setdefault(a, set()).add(b)
            self.adj.setdefault(b, set()).add(a)
        owner_p = {pi for pi in range(len(P)) if int(pl[pi]) in owner_limbs}
        slot_p = {pi for pi in range(len(P)) if int(pl[pi]) in slot_limbs}
        self.notes: List[str] = []
        pick_from = owner_p if owner_p else range(len(P))
        if not owner_p:
            self.notes.append(f"the owner limb ({attach_text(self.flags1)}) has no patches: every patch corner is a "
                              "candidate")
        self.candidates = {int(P[pi][k]) for pi in pick_from for k in PATCH_CORNERS}
        self.preferred = {key for key, users in self.sides.items()
                          if len(set(users) & owner_p) == 1 and len(set(users) & slot_p) == 1}
        self.slot_patches = len(slot_p)
        self.picked: List[int] = []
        self.message = ""

    # -- state
    def adjacent(self, a, b) -> bool:
        return frozenset((int(a), int(b))) in self.sides

    def closable(self) -> bool:
        n = len(self.picked)
        return (n >= 3 and (self.want is None or n == self.want)
                and self.adjacent(self.picked[-1], self.picked[0]))

    def allowed(self) -> set:
        """The corners a click may add now (the first corner again when the ring can close)."""
        if not self.picked:
            return set(self.candidates)
        if self.want is not None and len(self.picked) >= self.want:
            return {self.picked[0]} if self.closable() else set()
        out = (self.adj.get(self.picked[-1], set()) & self.candidates) - set(self.picked)
        if self.closable():
            out.add(self.picked[0])
        return out

    def wanted_text(self) -> str:
        return f"{len(self.picked)} / {self.want if self.want is not None else '?'} corners"

    def add(self, i) -> Optional[str]:
        """Add corner ``i``; a readable refusal (nothing changes) or None."""
        i = int(i)
        if self.picked and i == self.picked[0]:
            if self.closable():
                return None
            return (f"the ring can't close yet: {self.wanted_text()}" if self.want is None or len(self.picked) < self.want
                    else f"corner {self.picked[-1]} is not joined to the first corner {i} by a patch side")
        if i in self.picked:
            return f"corner {i} is already in the ring"
        if i not in self.candidates:
            return (f"control point {i} is not a patch corner of the {attach_text(self.flags1)} (a ring runs along its "
                    "patch corners)")
        if self.want is not None and len(self.picked) >= self.want:
            return f"the {self.slot_label} ring already has its {self.want} corners: close it on the first corner"
        if self.picked and not self.adjacent(self.picked[-1], i):
            return (f"corner {i} is not joined to corner {self.picked[-1]} by a patch side (the engine welds a ring "
                    "only along patch sides)")
        self.picked.append(i)
        return None

    def undo(self) -> None:
        if self.picked:
            self.picked.pop()

    def set_sequence(self, corners) -> Optional[str]:
        """Replace the picks with ``corners`` in order (Set Connection Ring's selection); the first refusal, or None."""
        self.picked = []
        for c in corners:
            why = self.add(c)
            if why:
                return why
        return None

    def check(self) -> Optional[str]:
        """Why the picks are not a finished ring (None = it closes at the slot's size)."""
        n = len(self.picked)
        if self.want is not None and n != self.want:
            return f"the {self.slot_label} ring needs {self.want} corners, {n} picked"
        if n < 3:
            return f"a ring needs at least 3 corners, {n} picked"
        if not self.adjacent(self.picked[-1], self.picked[0]):
            return (f"the ring does not close: its last corner {self.picked[-1]} and first corner {self.picked[0]} "
                    "share no patch side")
        return None

    def finish(self, fix=None):
        """``(corners, notes)`` of the finished ring; ``fix(corners) -> (corners, notes)`` puts it into the stock
        winding / start (``combocore.ring_fix``). Raises ValueError with the refusal."""
        why = self.check()
        if why:
            raise ValueError(why)
        corners, notes = list(self.picked), list(self.notes)
        if fix is not None:
            fixed, more = fix(list(corners))
            fixed = [int(c) for c in fixed]
            rot = {tuple(corners[k:] + corners[:k]) for k in range(len(corners))}
            rev = {tuple(r[::-1]) for r in rot}
            if tuple(fixed) not in rot | rev:
                raise ValueError(f"the ring fix returned {fixed}, which is not a rotation or reversal of {corners}")
            corners, notes = fixed, notes + list(more)
        return corners, notes

    def placement_problem(self, corners=None) -> Optional[str]:
        """Why a ring (default: the picks) is probably not where its slot's limb attaches, or None. When the slot's
        limb has patches, at least half of a stock ring's sides are boundary sides between an owner patch and a
        slot-limb patch (``preferred``; all 678 such rings of the base-game and add-on stock, the insect legs and arm
        stubs at exactly half), so a ring with fewer runs inside the torso or around another socket. Not checked
        without slot-limb patches (a stub socket)."""
        c = [int(x) for x in (self.picked if corners is None else corners)]
        if self.slot_patches == 0 or len(c) < 3:
            return None
        k = sum(frozenset((c[i], c[(i + 1) % len(c)])) in self.preferred for i in range(len(c)))
        if 2 * k >= len(c):
            return None
        return (f"only {k} of its {len(c)} sides border the {self.slot_label} limb's patches (stock rings have at least "
                f"half): it is probably inside the {attach_text(self.flags1)} or around another socket")

    def status_text(self) -> str:
        return (f"Pick ring {self.name}: {self.slot_label} (flags2 0x{self.flags2:x}, attached to "
                f"{attach_text(self.flags1)}): {self.wanted_text()}. Click a highlighted corner (Enter closes, Backspace "
                "undo, Esc cancel)")

    # -- the modal's event rule
    def handle(self, event_type: str, value: str, hover) -> str:
        """What the modal does with an event: "PASS_THROUGH" (viewport navigation), "RUNNING", "FINISH" or "CANCEL".
        A refused click leaves the reason in ``message``."""
        self.message = ""
        if event_type in _RING_NAV_EVENTS or event_type.startswith("NUMPAD_") and event_type != "NUMPAD_ENTER":
            return "PASS_THROUGH"
        if value != "PRESS":
            return "RUNNING"
        if event_type in ("ESC", "RIGHTMOUSE"):
            return "CANCEL"
        if event_type == "BACK_SPACE":
            self.undo()
            return "RUNNING"
        if event_type in ("RET", "NUMPAD_ENTER"):
            why = self.check()
            if why:
                self.message = why
                return "RUNNING"
            return "FINISH"
        if event_type == "LEFTMOUSE":
            if hover is None:
                self.message = "no corner under the cursor"
                return "RUNNING"
            closing = bool(self.picked) and int(hover) == self.picked[0]
            why = self.add(hover)
            if why:
                self.message = why
                return "RUNNING"
            if closing or (self.want is not None and self.closable()):
                return "FINISH"                             # closed on the first corner, or auto-closed at its size
            return "RUNNING"
        return "RUNNING"

    @staticmethod
    def nearest(screen, mouse, allowed, radius=15.0):
        """The corner under the cursor: ``screen`` {corner: (x, y) or None}, the nearest ALLOWED one within ``radius``
        pixels, else the nearest candidate within it (so a click on it can say why not), else None."""
        mx, my = float(mouse[0]), float(mouse[1])
        best = [None, None]
        bd = [radius * radius, radius * radius]
        for c, xy in screen.items():
            if xy is None:
                continue
            d = (xy[0] - mx) ** 2 + (xy[1] - my) ** 2
            slot = 0 if c in allowed else 1
            if d <= bd[slot]:
                bd[slot], best[slot] = d, c
        return best[0] if best[0] is not None else best[1]


# --- guard rails (pure): the control-point pool and the skeleton ------------------------------------------

POOL_UNDO_HINT = "Undo it (Ctrl+Z). Only SGM operators (Extrude Patch) may add control points"


def pool_error(ids, n_expected, patches, n_vert_owner=None, per_patch=None):
    """Why the Blender mesh no longer is the control-point pool the patch table indexes, or None.

    ``ids`` is the per-point ``bz_cp`` attribute (the pool index each Blender vertex is supposed to be; None when
    the attribute is missing), ``n_expected`` the pool size the SGM tables were written for, ``patches`` the (Np, 16)
    table, ``n_vert_owner`` the length of the per-point bone-owner array (None = absent) and ``per_patch`` a
    ``{name: row count or None}`` of the per-patch arrays as stored (None = absent, which is not an edit). The
    invariant: ``ids == arange(N)``, ``N == n_expected``, every patch index < N, and every present array sized to its
    domain."""
    p = np.asarray(patches, dtype=np.int64).reshape(-1, 16) if patches is not None else np.zeros((0, 16), np.int64)
    n = None
    if ids is not None:
        ids = np.asarray(ids, dtype=np.int64)
        n = len(ids)
        expect = n if n_expected is None else int(n_expected)
        if n != expect or not np.array_equal(ids, np.arange(n)):
            if n < expect:
                what = f"{expect - n} point{'s' if expect - n != 1 else ''} removed (deleted, dissolved or merged)"
            elif n > expect:
                what = f"{n - expect} point{'s' if n - expect != 1 else ''} added (duplicated or new)"
            elif len(np.unique(ids)) != n:
                what = "points removed and others added"
            else:
                what = "points reordered"
            return (f"a Blender topology tool changed the control-point pool ({what}): the patch table still indexes "
                    f"the old pool. {POOL_UNDO_HINT}")
    if n is None:
        n = n_expected
    if n is None:
        return "the control-point pool size is not recorded on this object: re-import it"
    if len(p) and (int(p.max()) >= n or int(p.min()) < 0):
        return (f"the patch table indexes control point {int(p.max())} but the pool has {n} points (control points "
                f"were removed): {POOL_UNDO_HINT}")
    if n_vert_owner is not None and n_vert_owner != n:
        return (f"the per-point bone owners have {n_vert_owner} entries for {n} control points: the model's data and the "
                "mesh disagree (re-import the creature)")
    for name, rows in (per_patch or {}).items():
        if rows is not None and rows != len(p):
            return (f"the per-patch {name} table has {rows} rows for {len(p)} patches: the model's data disagrees "
                    "(re-import the creature)")
    return None


def skeleton_changes(snapshot, current, snapshot_rest=None, current_rest=None, tol=1e-5, current_ids=None):
    """Structural skeleton edits since import, as readable sentences (empty = none): :func:`structure.resolve_bones`.

    ``snapshot`` / ``current``: ``[(bone name, parent name or "")]`` (import order / Blender's order).
    ``current_ids``: optional, per current bone, the source index stamped on it at import (None = unstamped: a bone
    added since, or a rig imported before the stamps). A stamped bone IS that source bone whatever its name, so two
    bones that swapped names are two renames (not two rest edits, which would skin each one's points with the other's
    rest); among copies carrying one stamp (Duplicate, Symmetrize) the one keeping the import name is it, else the one
    keeping the import rest (``snapshot_rest`` / ``current_rest``: ``{name: 4x4}``), else they are ambiguous. Unstamped
    bones match by name; on a rig with no stamps at all a missing bone paired with an added bone whose rest equals its
    snapshot rest is a rename too. Names compare case-sensitively (the file stores the name, and so do the fcurve
    paths)."""
    return resolve_skeleton(snapshot, current, snapshot_rest, current_rest, tol, current_ids).changes()


def resolve_skeleton(snapshot, current, snapshot_rest=None, current_rest=None, tol=1e-5, current_ids=None):
    """:func:`structure.resolve_bones` from the name / parent pairs :func:`skeleton_changes` takes."""
    snap = [(str(a), str(b or "")) for a, b in snapshot]
    ids = list(current_ids) if current_ids is not None else [None] * len(current)
    srest = None
    if snapshot_rest is not None:
        srest = [snapshot_rest.get(n) for n, _p in snap]
    bones = [structure.RigBone(name=str(n), parent=str(p or ""), src=None if sid is None else int(sid),
                               rest=None if current_rest is None or current_rest.get(str(n)) is None
                               else tuple(np.asarray(current_rest[str(n)], dtype=np.float64).reshape(-1)))
             for (n, p), sid in zip(current, ids)]
    return structure.resolve_bones(snap, bones, srest, tol)


def skin_group_state(point_weights, stored, tol=1e-4):
    """What the rig's bone vertex groups say about every control point: ``(owners, adopted, unskinned, blended)``.

    ``point_weights``: per point, ``[(bone index, weight)]`` over the Armature modifier's bone vertex groups;
    ``stored``: the add-on's owner table (``.bz_vert_bone``, -1 = unowned). A point in exactly one bone group at weight
    1 is owned by that bone (``owners``; ``adopted`` lists the points where that differs from ``stored``: a Ctrl+G /
    weight-paint reassignment). ``unskinned``: points the table skins (``stored >= 0``) that no group deforms;
    ``blended``: points with several or partial weights. Those two keep their stored owner in ``owners``."""
    st = np.asarray(stored, dtype=np.int64).reshape(-1)
    if len(point_weights) != len(st):
        raise ValueError(f"the vertex groups cover {len(point_weights)} points, the owner table {len(st)}")
    owners = st.copy()
    unskinned, blended, adopted = [], [], []
    for i, ws in enumerate(point_weights):
        ws = [(int(b), float(w)) for b, w in ws if float(w) > tol]
        if not ws:
            if st[i] >= 0:
                unskinned.append(i)
            continue
        if len(ws) != 1 or abs(ws[0][1] - 1.0) > tol:
            blended.append(i)
            continue
        if ws[0][0] != st[i]:
            owners[i] = ws[0][0]
            adopted.append(i)
    return owners, adopted, unskinned, blended


def live_owners(owners, unskinned):
    """The owner every point has as Blender deforms it (By Bone, the Boner read-outs and selections): the adopted
    ``owners`` of :func:`skin_group_state`, with a point no bone group deforms shown as unowned (-1)."""
    out = np.asarray(owners, dtype=np.int64).copy()
    if len(unskinned):
        out[np.asarray(unskinned, dtype=np.int64)] = -1
    return out


def _few(idx):
    idx = [int(i) for i in idx]
    return ", ".join(str(i) for i in idx[:6]) + (", ..." if len(idx) > 6 else "")


def _weights_desc(point_weights, i, bone_names, tol=1e-4):
    ws = [(int(b), float(w)) for b, w in point_weights[i] if float(w) > tol]
    return ", ".join(f"{bone_names[b] if bone_names is not None and 0 <= b < len(bone_names) else b} {w:.2f}"
                     for b, w in ws)


def skin_owners(point_weights, stored, bone_names=None, tol=1e-4):
    """The rigid bone owner of every control point as Blender deforms it: ``(owners, adopted, error)``
    (:func:`skin_group_state`). A point the file skins but no group deforms, or one with blended / partial weights,
    can't be written (the .sgm skins every point rigidly to one bone): ``error`` names them and nothing is adopted."""
    try:
        owners, adopted, unskinned, blended = skin_group_state(point_weights, stored, tol)
    except ValueError as e:
        return None, [], str(e)
    if not unskinned and not blended:
        return owners, adopted, None
    why = []
    if unskinned:
        why.append(f"{len(unskinned)} in no bone's vertex group, so Blender leaves them at rest (points {_few(unskinned)})")
    if blended:
        why.append(f"{len(blended)} with blended or partial weights (point {blended[0]}: "
                   f"{_weights_desc(point_weights, blended[0], bone_names, tol)})")
    return None, [], ("the rig's vertex groups skin control points the .sgm can't store (it skins each point to one "
                      "bone at full weight): " + "; ".join(why) + ". Give each point exactly one bone group at weight "
                      "1.0 (Assign Selected to Bone does), or Undo the weight edit")


def skin_edit_refusal(unskinned, blended, edit_points, point_weights=None, bone_names=None) -> Optional[str]:
    """Why a Boner skin edit must not run, or None. Every Boner write rewrites the bone vertex groups from the owner
    table, so a point whose groups hold what the table can't say (``blended`` weights, or ``unskinned``: its group
    removed) would be silently overwritten -- unless the edit itself targets it (``edit_points``: assigning a point
    gives it one full-weight group, which is the fix Export asks for)."""
    ed = set(int(i) for i in edit_points)
    bl = [int(i) for i in blended if int(i) not in ed]
    un = [int(i) for i in unskinned if int(i) not in ed]
    if not bl and not un:
        return None
    why = []
    if bl:
        d = f": {_weights_desc(point_weights, bl[0], bone_names)}" if point_weights is not None else ""
        why.append(f"{len(bl)} with blended or partial weights (point {bl[0]}{d})")
    if un:
        why.append(f"{len(un)} taken out of their bone's vertex group (points {_few(un)}; orange in By Bone)")
    return ("vertex-group edits on control points outside this edit can't be kept by the add-on's skin table (it skins "
            "each point to one bone at full weight): " + "; ".join(why) + ". This edit would overwrite them, so it "
            "stopped. Undo the weight edit, or include those points in the edit and assign them to one bone"
            + (" (Select Unassigned selects the ones with no bone group)" if un else ""))


# --- the export builder ------------------------------------------------------------------------------------

@dataclass
class RingEdit:
    """One connection record as the add-on holds it (a connection ROW), in POOL index space: ``raw`` ints kept
    verbatim, or a ``corners`` loop encoded as closed edge pairs (``ICParse ring_pairs``) at export.

    ``src`` is the source LCON record the row came from (-1 = made in Blender; None = ``index``, the ring edits before
    connection rows); ``name`` / ``flags1`` (Parent class) / ``flags2`` (Child class) None = the source record's.
    ``structure.apply_connections`` keeps the source record's bytes for a row equal to it."""
    index: int
    bone: str
    corners: object = None
    raw: object = None
    src: object = None
    name: object = None
    flags1: object = None
    flags2: object = None

    def source(self) -> int:
        return int(self.index) if self.src is None else int(self.src)

    def ints(self) -> List[int]:
        if self.raw is not None:
            return [int(i) for i in self.raw]
        from ICParse.sgm.creature.model import ring_pairs
        return ring_pairs(self.corners or [])


@dataclass
class EditState:
    """Everything an export takes from the add-on, bpy-free. ``None`` = "no override: keep the source's"."""
    source_path: str
    verts: np.ndarray                              # (N, 3) Blender space, pool order
    patches: object = None                         # (Np, 16); None = the source table
    vert_owner: object = None                      # (N,)
    patch_limb: object = None                      # (Np,) row index into ``limbs`` (-1 = no limb)
    limbs: object = None                           # [structure.LimbRow]; None = the source's limb records
    patch_uv: object = None                        # (Np, 8)
    patch_material: object = None                  # (Np,)
    patch_type: object = None                      # (Np,)
    connections: object = None                     # [RingEdit] rows = the whole LCON table in export order; None = the
                                                   # source's (remapped when grown)
    keep_connections: bool = True                  # grown with connections None: False clears them
    bone_rest: object = None                       # {bone index: 12-float bind-local}, edited bones only
    textures: object = None                        # {texture index: (rgba top-down, w, h)}
    texture_names: object = None                   # [file name per texture index]: the texture table replaced (Transfer
                                                   # Texture); a name not in the source needs its pixels in ``textures``
    slot_textures: object = None                   # [texture file name per material slot] with ``texture_names``
    new_bones: object = None                       # [structure.NewBone]: stub bones made in Blender (appended last)
    bone_renames: object = None                    # {source bone index: new name}: bones renamed in Blender
    added_bones: object = None                     # [structure.AddedBone]: ordinary bones added in Blender (inserted
                                                   # before the stub bones); ``vert_owner`` names them by key >= n_src
    bone_table_names: object = None                # the names the new-bone table's bones were made with (on the rig or
                                                   # not): limb entries of those that are gone are dropped
    limb_pins: object = None                       # the added bones a Boner limb operator put in a limb by hand: they
                                                   # stay in the row that lists them instead of following their parent
    pool_src: object = None                        # (N,) the source pool index of each current point, -1 = made since
                                                   # (Extrude); None = the pool is the source's (a prefix of it when grown)
    bone_topology: object = None                   # topology.BoneTopology: source bones deleted / re-parented on the rig
    bone_limits: object = None                     # {owner key: joint limit floats (6, + the flag word)}: edited bones
    ik_chains: object = None                       # [topology.IKRow]: the IK chain table; None = the source's IKCH chunk


@dataclass
class BuiltExport:
    model: object
    source: object                                 # the parsed source (a private copy, unedited)
    grown: bool = False
    o2n: object = None                             # old pool index -> new file index (None = identity)
    reencoded: int = 0                             # control points whose local bytes were rewritten
    tex_changed: dict = field(default_factory=dict)   # texture index -> texels changed
    limb_src: object = None                        # source limb index per exported limb (-1 = new)
    conn_src: object = None                        # source connection index per exported connection (-1 = new)
    warnings: list = field(default_factory=list)   # what the build implies (patches in no limb, ...)
    bones_added: list = field(default_factory=list)    # stub bones appended (structure.apply_stub_bones)
    stub_flagged: list = field(default_factory=list)   # existing bones given the stub byte (a new stub limb's)
    renames: object = None                         # structure.RenameReport of the bone renames (None = none)
    insert: object = None                          # structure.BoneInsert of the added bones (keys -> file indices)
    bones_new: list = field(default_factory=list)  # added bones inserted (structure.insert_added_bones), file order
    bone_keys: object = None                       # owner key per exported bone before the created stub bones
    limb_defaults: list = field(default_factory=list)  # "bone -> limb": added bones put in their parent's limb
    limb_moved: list = field(default_factory=list)     # "bone -> limb (moved from ...)": added bones that moved with their parent
    limb_dropped: str = ""                             # the limb entries of added bones not on the rig, dropped ("" = none)
    stock: list = field(default_factory=list)      # STOCK lines the build implies (Validate shows them)
    pool_src: object = None                        # EditState.pool_src as built with (the summary pairs source points by it)
    topology: object = None                        # topology.SkeletonEdit (== insert) when a source bone was deleted /
                                                   # re-parented: names, moved points, the IK and clip reports
    limits_changed: list = field(default_factory=list)   # bones whose joint limits were rewritten
    ik_changes: list = field(default_factory=list)       # what the IK chain table edit changed (phrases)


def source_table(m) -> np.ndarray:
    return np.array([list(p.control_points) for p in m.patches], dtype=np.int64).reshape(-1, 16)



def _refuse_unowned(owners, patches) -> None:
    bad = unowned_patch_points(owners, patches)
    if len(bad):
        raise ValueError(unowned_message(len(bad), bad))


def unowned_message(count, points=()) -> str:
    """The refusal for control points a patch uses that have no bone (Export, Validate)."""
    pts = [int(i) for i in list(points)[:6]]
    return (f"{count} control point{'s' if count != 1 else ''} used by patches ha{'ve' if count != 1 else 's'} no bone"
            + (f" (points {', '.join(map(str, pts))}{', ...' if count > len(pts) else ''})" if pts else "")
            + ": the game needs every patch point skinned to a bone. Boner > Bones > Select Unassigned selects them "
            "(orange in the By Bone overlay); assign them to a bone or run Auto Skin")


def _check_len(name, arr, n, required=False):
    if arr is None:
        if required:
            raise ValueError(f"the grown export needs the per-{'point' if name == 'bone owners' else 'patch'} "
                             f"{name}, which this object does not have (re-import the creature)")
        return None
    a = np.asarray(arr)
    if len(a) != n:
        raise ValueError(f"{name}: {len(a)} entries for {n} {'points' if name == 'bone owners' else 'patches'}")
    return a


def added_limb_placement(limbs, src, added, table_names=(), new_bones=(), pinned=()):
    """:func:`structure.added_limb_rows` as Export places the added bones (``structure.AddedBone``) in the limb rows
    ``limbs`` of the source ``src``: the new-bone table's names (``table_names``) are added-bone entries, the created stub
    bones (``new_bones``, ``structure.NewBone``: their names and creation names) never are. ``pinned``: the added bones a
    Boner limb operator put in a limb by hand, which stay in the row that lists them instead of following their parent.
    None when there is no added bone and no table entry (the rows are the export's as they are). The Limbs panel shows
    the same placement."""
    added = list(added or [])
    table_names = [str(t) for t in (table_names or ()) if t]
    if not added and not table_names:
        return None
    keep = [nb.name for nb in (new_bones or ())] + [nb.stamp for nb in (new_bones or ()) if getattr(nb, "stamp", None)]
    return structure.added_limb_rows(limbs, src, added, table_names, keep, pinned=pinned)


def build_export(st: EditState) -> BuiltExport:
    """The creature Export writes, from the add-on state: the source file with only the edits rewritten.

    Fast path (the patch table IS the source's): only moved control points, edited rest bones, reskinned owners,
    limb membership, UVs, patch types/materials, rings and repainted textures that actually differ are rewritten,
    so an untouched creature is byte-identical. Grown path (Extrude Patch added points / patches): rest edits are
    applied first, then every point is re-expressed under the new bind (a point whose world, owner and owner bind
    are unchanged keeps its source bytes), sorted into contiguous bone ranges, and patches, limb prims and rings are
    remapped. Rings and textures are applied the same way in both paths. Added bones (``st.added_bones``) are inserted
    before the stub bones in both paths before the owners are laid out (owner keys >= n_src name them), renames follow
    through the index map, and a bone Blender made goes into its parent's limb. Clips are not touched here
    (:func:`apply_clip_plan`, encoded against this model; a kept clip holds an added bone at its bind-local rest)."""
    src = read_source(st.source_path)
    m = read_source(st.source_path)                     # the private copy that becomes the export
    verts = np.asarray(st.verts, dtype=np.float64).reshape(-1, 3)
    added = list(st.added_bones or [])
    topo = st.bone_topology if st.bone_topology else None
    moved_points = []
    if topo is not None and topo.deleted:              # a deleted bone's points go to its parent (the file's), first
        import dataclasses
        base = st.vert_owner if st.vert_owner is not None else np.asarray(R.vertex_bone_owners(src), dtype=np.int64)
        vo, moved_points = topology.move_deleted_owners(base, src, topo.deleted)
        st = dataclasses.replace(st, vert_owner=vo)
    placed = added_limb_placement(st.limbs, src, added, st.bone_table_names, st.new_bones, st.limb_pins)
    if placed is not None:                              # every added bone in its current parent's limb, by identity
        if placed.rows is not st.limbs:
            import dataclasses
            st = dataclasses.replace(st, limbs=placed.rows)
    if topo is not None:                                # the delete / re-parent rules on the tables the export writes
        rows = st.limbs if st.limbs is not None else structure.source_rows(src)
        hosts = None
        if st.connections is not None:
            hosts = [(str(src.limb_connections[r.source()].name if r.name is None and 0 <= r.source() < len(src.limb_connections)
                          else r.name), str(r.bone)) for r in st.connections]
        rules = topology.rules_of(src, limb_lists=[structure.row_bones(r, src) for r in rows],
                                  limb_names=[r.name for r in rows], limb_ids=[int(r.limb_id) for r in rows], hosts=hosts,
                                  stubs=structure.export_stub_bones(src, st.limbs, st.patch_limb, st.vert_owner),
                                  added={ab.name: (int(ab.key), int(ab.parent)) for ab in added})
        probs = topology.topology_refusals(src, topo, rules)
        if probs:
            raise ValueError("; ".join(probs))
    src_tab = source_table(src)
    grown = st.patches is not None and (
        len(verts) != len(src.vertices) or len(st.patches) != len(src.patches)
        or not np.array_equal(np.asarray(st.patches, dtype=np.int64).reshape(-1, 16), src_tab))
    if grown:
        out = _build_grown(m, src, st, verts)
    else:
        out = _build_fast(m, src, st, verts)
    limb_src = out.limb_src if out.limb_src is not None else list(range(len(out.model.limbs)))
    ins = out.insert
    twarns = []
    ik_rows = st.ik_chains
    if ik_rows is not None:                             # the stored table first: a delete / re-parent then fixes it
        out.ik_changes = topology.apply_ik_rows(out.model, src, ik_rows,
                                                missing_ok=[src.bones[d].name for d in (topo.deleted if topo else ())])
    if topo is not None:
        ins.moved_points = [(str(src.bones[d].name), str(src.bones[t].name), int(k)) for d, t, k in moved_points]
        twarns += [f"{k} control point{'s' if k != 1 else ''} of deleted bone '{d}' moved to its parent '{t}' (to change "
                   "that: Boner > Bones > Assign Selected / Auto Skin)" for d, t, k in ins.moved_points]
        ins.limbs = topology.drop_from_limbs(out.model, ins.deleted)
        ins.ik = topology.fix_ik_chains(out.model, ins.deleted, [r[0] for r in ins.reparented])
        twarns += ins.ik.warnings
        ins.clips = topology.retrack_clips(out.model, src, ins, topo)
        twarns += ins.clips.warnings
        out.topology = ins
    out.limits_changed = topology.apply_bone_limits(out.model, ins, st.bone_limits)
    new_bones = list(st.new_bones or [])
    aliases = {nb.stamp: nb.name for nb in new_bones if getattr(nb, "stamp", None) and nb.stamp != nb.name}
    renames = {}
    for i, nm in (st.bone_renames or {}).items():
        if int(i) not in ins.index or int(i) >= ins.n_src:
            raise ValueError(f"rename of bone index {i}, but the source skeleton has {ins.n_src} bones")
        if ins.index[int(i)] < 0:
            continue                                    # a renamed bone deleted since: nothing to rename
        renames[ins.index[int(i)]] = nm
    out.renames = structure.apply_bone_renames(out.model, renames, aliases)   # before the stub bones: their
    rwarns = boner.bone_rename_warnings(out.renames.renamed) + out.renames.warnings  # names and parents are current
    if out.renames.renamed:
        parent_of = {str(src.bones[i].name): str(nm) for i, nm in (st.bone_renames or {}).items()
                     if 0 <= int(i) < len(src.bones)}
        new_bones = [structure.NewBone(name=nb.name, parent=parent_of.get(str(nb.parent), str(nb.parent)),
                                       world=nb.world, stamp=getattr(nb, "stamp", None)) for nb in new_bones]
    if ins.added:
        probs = structure.bone_name_problems([b.name for b in out.model.bones], ins.added)
        if probs:
            raise ValueError("; ".join(probs) + ": rename the added bone (Boner > Bones > Rename Bone)")
    out.bones_added, out.stub_flagged, bwarns = structure.apply_stub_bones(out.model, limb_src, new_bones)
    out.bones_new = list(ins.added)
    lwarns = []
    if placed is not None:
        names = [r.name for r in (st.limbs if st.limbs is not None else structure.source_rows(src))]
        out.limb_defaults, out.limb_moved = list(placed.placed), placed.moved_text(names)
        out.limb_dropped = placed.dropped_text(names) if placed.dropped else ""
        lwarns = list(placed.warnings) + moved_stray_warnings(out.model, placed.moved)
    out.bone_keys = ins.keys(len(out.model.bones) - len(out.bones_added))
    out.stock = structure.added_bone_stock_lines(out.model, ins.added)
    out.warnings = (list(out.warnings) + rwarns + boner.bone_add_warnings(ins.added) + list(ins.warnings) + lwarns
                    + bwarns + twarns)
    out.tex_changed = _apply_textures(out.model, st.textures, st.texture_names, st.slot_textures, out.warnings)
    return out


def moved_stray_warnings(m, moved) -> list:
    """Warnings for added bones that moved to another limb with their parent (``structure.AddedLimbs.moved``) and own
    control points of their old limb's patches that now go nowhere in a graft: the limb bone-list check
    (``ICParse.validate.limb_stray_points``, which Validate's STOCK line reports for the whole creature) on the
    exported lists, against the same lists with each moved bone back in its old limb."""
    from ICParse import validate as ICval
    if not moved:
        return []
    ids = [int(l.limb_id) for l in m.limbs]
    after = [[str(b) for b in l.bone_names] for l in m.limbs]
    before = [list(bl) for bl in after]
    for b, jo, j, _p in moved:
        if 0 <= j < len(before):
            before[j] = [x for x in before[j] if x.lower() != b.lower()]
        if 0 <= jo < len(before):
            before[jo].append(b)
    counts = ICval.limb_point_owners(m)
    hosts = ICval.limb_connection_hosts(ids, [(c.name, c.bone_name, int(c.flags2)) for c in m.limb_connections])
    old = {(j, b.lower()) for j, bad in ICval.limb_stray_points(ids, before, counts, hosts) for b, _n in bad}
    mine = {b.lower(): (jo, j, p) for b, jo, j, p in moved}
    out = []
    for jl, bad in ICval.limb_stray_points(ids, after, counts, hosts):
        for b, n in bad:
            if b.lower() not in mine or (jl, b.lower()) in old:
                continue
            _jo, j, p = mine[b.lower()]
            leg = boner.class_of(ids[jl]) & 0x20 and not ids[jl] & boner.NOSWAP
            out.append(f"added bone '{b}' owns {n} control point{'s' if n != 1 else ''} of '{m.limbs[jl].name}' and moved "
                       + (f"to '{m.limbs[j].name}'" if 0 <= j < len(m.limbs) else "to no limb")
                       + f" with its parent '{p}': grafted as a donor, ComboTest.exe puts them on the root bone"
                       + (" and fails an assertion (no export)" if leg else "")
                       + f" (re-parent it under a bone of '{m.limbs[jl].name}', or assign those points to one of its bones)")
    return out


def build_export_model(st: EditState):
    """:func:`build_export`'s model."""
    return build_export(st).model


def _apply_bone_rest(m, bone_rest) -> set:
    changed = set()
    for i, tf in (bone_rest or {}).items():
        i = int(i)
        if not 0 <= i < len(m.bones):
            raise ValueError(f"rest pose edit for bone index {i}, but the creature has {len(m.bones)} bones")
        if not np.allclose(m.bones[i].transform, tf, atol=1e-5):
            m.bones[i].transform = [float(x) for x in tf]
            changed.add(i)
    return changed


def _build_fast(m, src, st, verts) -> BuiltExport:
    if len(verts) != len(m.vertices):
        raise ValueError(f"vertex count changed ({len(verts)} != {len(m.vertices)}) without a grown patch table")
    np_ = len(m.patches)
    owners = R.vertex_bone_owners(m)
    old_bw = R.bind_world_matrices(m)
    orig_f32 = sgm_to_blender(R.world_vertices(m, owners, old_bw)).astype(np.float32)
    moved = np.any(verts.astype(np.float32) != orig_f32, axis=1)

    changed_bones = _apply_bone_rest(m, st.bone_rest)
    vo = _check_len("bone owners", st.vert_owner, len(verts))
    reskin = vo is not None and not np.array_equal(vo.astype(np.int64), np.asarray(owners, dtype=np.int64))
    if reskin:
        _refuse_unowned(vo, source_table(m))
    pl = _check_len("limbs", st.patch_limb, np_)
    # the skeleton first (added bones inserted, source bones deleted / re-parented, records re-sorted if a parent would
    # follow its child), so the bind worlds below are those of the file; the source bones' worlds are read back by key
    stubs = structure.export_stub_bones(src, st.limbs, pl, vo) if (st.added_bones or st.bone_topology) else None
    ins = topology.apply_skeleton(m, st.added_bones, len(src.bones), stubs, st.bone_topology)
    bw_file = R.bind_world_matrices(m)
    new_bw = [bw_file[ins.index[i]] if ins.index.get(i, -1) >= 0 else old_bw[i] for i in range(len(src.bones))]

    dirty = moved.copy()
    if changed_bones or st.bone_topology:
        world_changed = [i for i in range(len(src.bones)) if not np.allclose(new_bw[i], old_bw[i], atol=1e-5)]
        if world_changed:
            dirty |= np.isin(np.asarray(owners), world_changed)
    reencoded = 0
    if dirty.any() and not reskin:
        new_local = R.local_from_world(blender_to_sgm(verts), owners, new_bw)
        vl = list(m.vertices)
        for i in np.nonzero(dirty)[0]:
            vl[int(i)] = new_local[int(i)]
        m.vertices = vl
        reencoded = int(dirty.sum())

    limb_src, warns = structure.apply_structure(m, src, st, pl, np_)
    for name, arr, attr in (("materials", st.patch_material, "material_index"), ("types", st.patch_type, "type")):
        a = _check_len(name, arr, np_)
        if a is not None:
            for i, p in enumerate(m.patches):
                if int(getattr(p, attr)) != int(a[i]):
                    setattr(p, attr, int(a[i]))

    conn_src, cwarns = structure.apply_connections(m, src, st.connections, None)   # before reskin: its remap carries them
    o2n = None
    if reskin:
        o2n = _apply_reskin(m, ins.owners(vo), blender_to_sgm(verts), bw_file)   # owner keys -> file bones
        reencoded = len(verts)
    _apply_uvs(m, st.patch_uv, np_)
    return BuiltExport(model=m, source=src, grown=False, o2n=o2n, reencoded=reencoded, limb_src=limb_src,
                       conn_src=conn_src, warnings=warns + cwarns, insert=ins)


def _apply_uvs(m, patch_uv, n_rows):
    """Per-patch corner UVs, only where they differ by more than 1e-6 (the UV cage stores single precision and
    V-flips, so an untouched patch can drift about 1 ULP)."""
    if patch_uv is None:
        return
    uo = np.asarray(patch_uv, dtype=np.float64).reshape(-1, 8)
    if len(uo) != n_rows:
        raise ValueError(f"UVs: {len(uo)} rows for {n_rows} patches")
    for i in range(min(len(uo), len(m.patches))):
        if np.abs(np.asarray(m.patches[i].uvs, dtype=np.float64) - uo[i]).max() > 1e-6:
            m.patches[i].uvs = [float(x) for x in uo[i]]


def _build_grown(m, src, st, verts) -> BuiltExport:
    from ICParse.sgm.creature.model import Patch
    patches = np.asarray(st.patches, dtype=np.int64).reshape(-1, 16)
    n, np_ = len(verts), len(patches)
    if len(patches) and (int(patches.max()) >= n or int(patches.min()) < 0):
        raise ValueError(f"the patch table indexes control point {int(patches.max())} of a {n}-point pool")
    no = _check_len("bone owners", st.vert_owner, n, required=True).astype(np.int64)
    pmat = _check_len("materials", st.patch_material, np_, required=True)
    ptype = _check_len("types", st.patch_type, np_, required=True)
    puv = _check_len("UVs", st.patch_uv, np_, required=True)
    pl = _check_len("limbs", st.patch_limb, np_, required=True)
    if len(no) and int(no.min()) < -1:
        raise ValueError(f"a control point is skinned to bone {int(no.min())}, but the creature has {len(m.bones)} bones")
    if len(no) and int(no.max()) >= len(m.bones) and not st.added_bones:
        raise ValueError(f"a control point is skinned to bone {int(no.max())}, but the creature has {len(m.bones)} bones")
    _refuse_unowned(no, patches)

    src_owners = np.asarray(R.vertex_bone_owners(src), dtype=np.int64)
    src_bw = R.bind_world_matrices(src)
    src_world_f32 = sgm_to_blender(R.world_vertices(src, src_owners, src_bw)).astype(np.float32)
    _apply_bone_rest(m, st.bone_rest)                   # BEFORE the bind: points are re-expressed under the new rest
    stubs = structure.export_stub_bones(src, st.limbs, pl, no) if (st.added_bones or st.bone_topology) else None
    ins = topology.apply_skeleton(m, st.added_bones, len(src.bones), stubs, st.bone_topology)   # the bind includes them
    mo = ins.owners(no)                                  # owner keys -> file bone indices
    bw = R.bind_world_matrices(m)
    world = blender_to_sgm(verts)

    corner = corner_mask(patches, n)
    order = skin_order(mo, corner)
    o2n = np.zeros(n, dtype=np.int64)
    for ni, oi in enumerate(order):
        o2n[oi] = ni
    invs = [np.linalg.inv(b) for b in bw]
    local = [None] * n
    ns = len(src.vertices)
    vf32 = verts.astype(np.float32)
    kept = 0
    # which source point each current point is: st.pool_src (Delete Patch compacts the pool, so index v is not source
    # point v any more), else the source's own index for the first ns points (Extrude appends)
    psrc = None if st.pool_src is None else np.asarray(st.pool_src, dtype=np.int64).reshape(-1)
    if psrc is not None and len(psrc) != n:
        raise ValueError(f"pool identity: {len(psrc)} source indices for {n} points")
    for v in range(n):
        own = int(mo[v])
        sv = int(psrc[v]) if psrc is not None else (v if v < ns else -1)
        sv = sv if 0 <= sv < ns else -1
        if own < 0:                                      # no bone (never used by a patch: refused above)
            local[v] = _unowned_local(src.vertices[sv] if sv >= 0 else None, int(src_owners[sv]) if sv >= 0 else -1,
                                      world[v])
            kept += sv >= 0 and local[v] is src.vertices[sv]
            continue
        if (sv >= 0 and int(no[v]) == int(src_owners[sv]) and np.array_equal(vf32[v], src_world_f32[sv])
                and np.array_equal(bw[own], src_bw[int(no[v])])):
            local[v] = src.vertices[sv]                  # untouched point: its source bytes
            kept += 1
            continue
        l = invs[own] @ np.array([world[v][0], world[v][1], world[v][2], 1.0])
        local[v] = (float(l[0]), float(l[1]), float(l[2]))
    m.vertices = [local[order[ni]] for ni in range(n)]

    puv = np.asarray(puv, dtype=np.float64).reshape(-1, 8)
    new_patches = []
    for i in range(np_):
        uvs = [float(x) for x in puv[i]]
        if i < len(src.patches) and np.abs(np.asarray(src.patches[i].uvs, dtype=np.float64) - puv[i]).max() <= 1e-6:
            uvs = list(src.patches[i].uvs)
        new_patches.append(Patch(material_index=int(pmat[i]), type=int(ptype[i]),
                                 control_points=[int(o2n[int(j)]) for j in patches[i]], uvs=uvs))
    m.patches = new_patches

    so = np.array([int(mo[order[ni]]) for ni in range(n)], dtype=np.int64)
    _layout_bones(m, so, mo, corner)

    limb_src, warns = structure.apply_structure(m, src, st, np.asarray(pl, dtype=np.int64), np_)

    conn_src, cwarns = list(range(len(m.limb_connections))), []
    if st.connections is None and not st.keep_connections:
        m.limb_connections = []                          # geometry fully replaced: template rings don't apply
        conn_src = []
    else:
        # These records hold SOURCE pool indices, which after a compaction (Delete Patch) are not the current pool's,
        # so they are mapped source -> current pool (st.pool_src) -> file. A source ring whose points went with a
        # deleted patch is left alone: the stored table carries its own, already-current ring (and
        # apply_connections checks and reports that one) -- these copies only serve to keep an unchanged row's bytes.
        s2c = np.full(ns, -1, dtype=np.int64)
        if psrc is None:
            s2c[:min(ns, n)] = np.arange(min(ns, n))
        else:
            live = psrc >= 0
            s2c[psrc[live]] = np.nonzero(live)[0]
        for c in m.limb_connections:
            cur = [int(s2c[int(i)]) if 0 <= int(i) < ns else -1 for i in c.vertex_indices]
            if any(v < 0 for v in cur):
                if st.connections is None:               # no stored table: these records ARE the export
                    raise ValueError(f"connection {c.name}: ring index out of the {n}-point pool")
                continue
            c.vertex_indices = [int(o2n[v]) for v in cur]
        conn_src, cwarns = structure.apply_connections(m, src, st.connections, o2n)
    return BuiltExport(model=m, source=src, grown=True, o2n=o2n, reencoded=n - kept, limb_src=limb_src,
                       conn_src=conn_src, warnings=warns + cwarns, insert=ins, pool_src=psrc)


def _apply_textures(m, textures, names=None, slots=None, warnings=None) -> dict:
    """Repainted textures re-encoded with the source palette and every untouched texel kept
    (``ICParse.codecs.texture.encode_texture_edit``); returns {index: texels changed} for those that differ.

    With ``names`` (Transfer Texture) the texture table BECOMES ``names``: a name the source has keeps that texture
    (its pixels when unpainted), any other name is a new texture encoded from ``textures[i]`` (a fresh 256-colour
    palette + mip chain); ``slots`` then sets each material's TXTR tag (by slot position). A material's other texture
    stages (``TXRF`` reflection, ``TXSP`` specular, a second ``TXTR``) naming a texture the new table dropped would
    dangle -- the engine and ``ICParse.validate`` both want every reference embedded -- so those tags come off and
    ``warnings`` (a list appended in place) says which maps went."""
    changed = {}
    textures = textures or {}
    if names is not None:
        by_name = {}
        for t in m.textures:
            by_name.setdefault(t.name, t)
        new_list = []
        for i, name in enumerate(names):
            src_t = by_name.get(name)
            if i in textures:
                rgba, w, h = textures[i]
                if src_t is not None and (src_t.width, src_t.height) == (int(w), int(h)):
                    new_t, k = ICtex.encode_texture_edit(src_t, rgba, int(w), int(h))
                else:
                    new_t, k = ICtex.encode_texture(name, rgba, int(w), int(h)), int(w) * int(h)
                if k:
                    changed[i] = k
                new_list.append(new_t)
            elif src_t is not None:
                new_list.append(src_t)
            else:
                raise ValueError(f"texture table names '{name}' (index {i}) but no image holds its pixels")
        m.textures = new_list
        if slots is not None:
            kept = {t.name for t in new_list}
            tex_tags = ("TXTR", "TXRF", "TXSP")        # diffuse / reflection / specular, all embedded by name
            dropped = {}
            for i, mat in enumerate(m.materials):
                if i >= len(slots):
                    break
                tags = list(mat.tags)
                hit = next((k for k, (tag, _v) in enumerate(tags) if tag == "TXTR"), None)
                if hit is None:
                    tags.append(("TXTR", str(slots[i])))
                else:
                    tags[hit] = ("TXTR", str(slots[i]))
                out_tags = []
                for k, (tag, v) in enumerate(tags):
                    if k != hit and tag in tex_tags and v and v not in kept:
                        dropped.setdefault(tag, set()).add(v)
                        continue                       # a stage pointing at a texture the new table dropped
                    out_tags.append((tag, v))
                mat.tags = out_tags
            if dropped and warnings is not None:
                what = {"TXRF": "reflection", "TXSP": "specular", "TXTR": "extra diffuse"}
                warnings.append("the new texture table replaced the creature's textures, so the "
                                + ", ".join(f"{len(v)} {what.get(t, t)} map{'s' if len(v) != 1 else ''} ({t})"
                                            for t, v in sorted(dropped.items()))
                                + " that named the old ones came off the materials (the engine loads only embedded "
                                  "textures); Paint Skin or Transfer Texture again to give it a new one")
        return changed
    for idx, (rgba, w, h) in textures.items():
        idx = int(idx)
        if not 0 <= idx < len(m.textures):
            raise ValueError(f"painted texture {idx}: the creature has {len(m.textures)} textures")
        new, k = ICtex.encode_texture_edit(m.textures[idx], rgba, int(w), int(h))
        if k:
            m.textures[idx] = new
            changed[idx] = k
    return changed


def clip_track_model(model, world_per_frame):
    """The model a re-encoded clip's tracks are computed on: ``model`` itself, or -- when the export model has more
    bones than were sampled (the stub bones Add Stub Socket created are appended after the source bones) -- a view of
    the sampled bones only, so a created stub bone gets no track (Boner writes none for a stub bone; the engine keeps
    it at bind). Raises ValueError when more bones were sampled than the model has."""
    from types import SimpleNamespace
    nb = len(world_per_frame[0]) if len(world_per_frame) else len(model.bones)
    if nb > len(model.bones):
        raise ValueError(f"{nb} sampled bones for a {len(model.bones)}-bone creature")
    return model if nb == len(model.bones) else SimpleNamespace(bones=list(model.bones[:nb]))


def apply_clip_plan(m, add_clips=None, drop_names=None) -> None:
    """Drop the clips whose Actions were deleted or renamed, then append / replace the encoded clips by name."""
    if drop_names:
        gone = set(drop_names)
        m.animations = [a for a in m.animations if a.name not in gone]
    if add_clips:
        by_name = {a.name: i for i, a in enumerate(m.animations)}
        for a in add_clips:
            if a.name in by_name:
                m.animations[by_name[a.name]] = a
            else:
                by_name[a.name] = len(m.animations)
                m.animations.append(a)


@dataclass
class ExportSummary:
    parts: list
    warnings: list
    stock: list = field(default_factory=list)      # STOCK lines the build implies (added bones owning nothing)

    @property
    def line(self) -> str:
        return "; ".join(self.parts) if self.parts else "no edits"


def export_summary(built: BuiltExport, kept=(), written=(), dropped=(), warnings=()) -> ExportSummary:
    """What the export changed, diffed from the final model against the source (not from what the operators
    meant to do), plus warnings the diff implies. ``kept`` / ``written`` / ``dropped``: the clip plan, verified
    against the model's clip names (a planned clip missing from the model raises)."""
    src, m = built.source, built.model
    parts, warns = [], list(warnings) + [w for w in (built.warnings or []) if w not in warnings]
    names = [a.name for a in m.animations]
    for c in list(kept) + list(written):
        if c not in names:
            raise ValueError(f"clip {c!r} was planned but is not in the export model")
    for c in dropped:
        if c in names and c not in written:
            raise ValueError(f"clip {c!r} was planned to be dropped but is still in the export model")
    o2n = built.o2n
    # s2f: source pool index -> file index (-1: the point is gone), for pairing source patches and rings with the
    # file's; o2n maps the CURRENT pool, which is the source's (or a prefix of it) unless Delete Patch compacted it
    s2f = o2n
    if o2n is not None and built.pool_src is not None:
        ps = np.asarray(built.pool_src, dtype=np.int64).reshape(-1)
        s2f = np.full(len(src.vertices), -1, dtype=np.int64)
        have = (ps >= 0) & (ps < len(s2f))
        s2f[ps[have]] = np.asarray(o2n, dtype=np.int64)[np.nonzero(have)[0]]
    elif o2n is not None and len(o2n) < len(src.vertices):
        s2f = np.concatenate([np.asarray(o2n, dtype=np.int64), np.full(len(src.vertices) - len(o2n), -1)])
    keys = built.bone_keys if built.bone_keys is not None else list(range(min(len(src.bones), len(m.bones))))
    pairs = [(src.bones[k], m.bones[i]) for i, k in enumerate(keys) if 0 <= int(k) < len(src.bones)]   # by identity
    if built.grown:
        parts.append(f"geometry rebuilt: {len(m.vertices) - len(src.vertices):+d} control points, "
                     f"{len(m.patches) - len(src.patches):+d} patches ({built.reencoded} points re-encoded, bone "
                     "ranges rebuilt)")
    else:
        if o2n is not None:
            parts.append(f"skinning: {sum(1 for a, b in pairs if (a.start_vertex, a.vertex_count) != (b.start_vertex, b.vertex_count)) + sum(1 for nm in built.bones_new if m.find_bone(nm) is not None and m.find_bone(nm).vertex_count)} "
                         f"bone range(s) rebuilt ({len(m.vertices)} points re-encoded)")
        else:
            k = sum(1 for a, b in zip(src.vertices, m.vertices) if tuple(a) != tuple(b))
            if k:
                parts.append(f"geometry: {k} control point{'s' if k != 1 else ''} re-encoded")
    moved = {n for n, _was, _now in (built.topology.reparented if built.topology is not None else [])}
    rest = [b.name for a, b in pairs if list(a.transform) != list(b.transform) and b.name not in moved]
    if rest:
        parts.append(f"rest pose: {len(rest)} bone{'s' if len(rest) != 1 else ''} ({', '.join(rest[:4])}"
                     f"{', ...' if len(rest) > 4 else ''})")
    if built.bones_added or built.stub_flagged:
        parts.append("bones: " + "; ".join(
            ([f"added {len(built.bones_added)} stub bone{'s' if len(built.bones_added) != 1 else ''} "
              f"({', '.join(built.bones_added[:4])}{', ...' if len(built.bones_added) > 4 else ''})"]
             if built.bones_added else [])
            + ([f"stub flag set on {', '.join(built.stub_flagged[:4])}{', ...' if len(built.stub_flagged) > 4 else ''}"]
               if built.stub_flagged else [])))
    if built.bones_new:
        few = ", ".join(built.bones_new[:4]) + (", ..." if len(built.bones_new) > 4 else "")
        notes = built.limb_defaults[:3] + (["..."] if len(built.limb_defaults) > 3 else []) + list(built.limb_moved)
        parts.append(f"bones: added {len(built.bones_new)} ({few})"
                     + (f", into their parent's limb: {', '.join(notes)}" if notes else ""))
    if built.limb_dropped:
        parts.append(f"limbs: dropped {built.limb_dropped}")
    tp = built.topology
    if tp is not None:
        if tp.deleted:
            moved = {d: (t, k) for d, t, k in tp.moved_points}
            few = ", ".join(d + (f" ({moved[d][1]} points -> {moved[d][0]})" if d in moved else "") for d in tp.deleted[:4])
            parts.append(f"bones: deleted {len(tp.deleted)} ({few}{', ...' if len(tp.deleted) > 4 else ''})"
                         + (f", taken off limb list(s) {', '.join(sorted({l for _b, l in tp.limbs})[:3])}" if tp.limbs else ""))
        if tp.reparented:
            few = ", ".join(f"{n}: {was} -> {now}" for n, was, now in tp.reparented[:3])
            parts.append(f"bones: re-parented {len(tp.reparented)} ({few}{', ...' if len(tp.reparented) > 3 else ''})"
                         + (f", {tp.resorted} record(s) re-sorted (parents first)" if tp.resorted else ""))
        if tp.ik is not None and tp.ik.text():
            parts.append("IK chains: " + tp.ik.text())
        if tp.clips is not None and tp.clips.text():
            parts.append("clips: " + tp.clips.text())
    if built.limits_changed:
        parts.append(f"joint limits: {len(built.limits_changed)} bone{'s' if len(built.limits_changed) != 1 else ''} "
                     f"({', '.join(built.limits_changed[:4])}{', ...' if len(built.limits_changed) > 4 else ''})")
    if built.ik_changes:
        parts.append("IK chains: " + "; ".join(built.ik_changes))
    rn = built.renames
    unrename = None
    if rn is not None and rn.renamed:
        parts.append("bones: " + rn.text())
        unrename = structure.BoneRenamer(rn.renamed).inverse()
    limb_src = built.limb_src if built.limb_src is not None else list(range(len(m.limbs)))
    limbs = structure.limb_changes(src, m, limb_src, unrename)
    if limbs:
        parts.append("limbs: " + "; ".join(limbs))
    conn_src = built.conn_src if built.conn_src is not None else list(range(len(m.limb_connections)))
    rings, conns = structure.connection_changes(src, m, conn_src, s2f, unrename)
    if rings:
        parts.append(f"rings: {len(rings)} ({', '.join(rings[:3])}{', ...' if len(rings) > 3 else ''})")
    if conns:
        parts.append("connections: " + "; ".join(conns))
    nsp = min(len(src.patches), len(m.patches))
    def same_patch(i):                    # the same patch in both files (its control points, remapped)
        cps = src.patches[i].control_points
        return ([int(s2f[x]) for x in cps] if s2f is not None else list(cps)) == list(m.patches[i].control_points)
    common = [i for i in range(nsp) if same_patch(i)]
    if len(common) != nsp and not built.grown:
        parts.append(f"patch table: {nsp - len(common)} patch(es) with other control points")
    uvs = sum(1 for i in common if list(src.patches[i].uvs) != list(m.patches[i].uvs))
    if uvs:
        parts.append(f"UVs: {uvs} patch{'es' if uvs != 1 else ''}")
    mats = sum(1 for i in common if (src.patches[i].material_index, src.patches[i].type)
               != (m.patches[i].material_index, m.patches[i].type))
    if mats:
        parts.append(f"patch materials/types: {mats}")
    if built.tex_changed:
        parts.append(f"textures: {len(built.tex_changed)} edited ("
                     + ", ".join(f"{m.textures[i].name}: {k} texel{'s' if k != 1 else ''}"
                                 for i, k in sorted(built.tex_changed.items())) + ")")
    if written or dropped or kept:
        if written or dropped:
            parts.append(f"clips: {len(kept)} kept, {len(written)} written"
                         + (f", {len(dropped)} dropped" if dropped else ""))
    if rest and kept:
        warns.append(f"rest pose of {len(rest)} bone(s) changed: {len(kept)} kept clip(s) play in-game as imported "
                     "(bone-local tracks); Blender now previews them offset by the rest change")
    if kept and (built.grown or built.reencoded):
        try:
            a = R.world_vertices(src); b = R.world_vertices(m)
            ext_a = np.concatenate([a.min(0), a.max(0)]); ext_b = np.concatenate([b.min(0), b.max(0)])
            scale = max(1e-6, float(np.abs(ext_a).max()))
            if np.abs(ext_a - ext_b).max() > 1e-4 * scale:
                warns.append(f"{len(kept)} kept clip(s) have bounds from the source geometry, whose extent changed "
                             "(the engine culls by clip bounds): edit or re-key a clip to re-encode it")
        except Exception as e:                             # never silent
            warns.append(f"clip bounds check failed: {type(e).__name__}: {e}")
    return ExportSummary(parts=parts, warnings=warns, stock=list(built.stock or []))


# --- compatibility wrappers ----------------------------------------------------------------------------------

def export_grown(source_path, verts_blender, patches, vert_owner, patch_material, patch_uv,
                 patch_type, patch_limb, keep_connections=True) -> bytes:
    """The bytes of :func:`export_grown_model`."""
    return sgm_parser.write(export_grown_model(source_path, verts_blender, patches, vert_owner, patch_material,
                                               patch_uv, patch_type, patch_limb, keep_connections))


def export_grown_model(source_path, verts_blender, patches, vert_owner, patch_material, patch_uv,
                       patch_type, patch_limb, keep_connections=True):
    """:func:`build_export_model` for a creature whose geometry is rebuilt from arrays (the source's rings are
    remapped, or cleared with ``keep_connections=False``)."""
    return build_export_model(EditState(
        source_path=source_path, verts=np.asarray(verts_blender, dtype=np.float64),
        patches=np.asarray(patches, dtype=np.int64), vert_owner=vert_owner, patch_limb=patch_limb, patch_uv=patch_uv,
        patch_material=patch_material, patch_type=patch_type, connections=None,
        keep_connections=bool(keep_connections)))


def mesh_to_creature(template_path, coords_blender, quad_faces, bone=0):
    """Convert a quad mesh (from DAE/STL/OBJ via Blender) into a bicubic-patch creature.

    Each quad becomes one bicubic Bezier patch (ICParse ACC: a regular quad region is exactly a
    uniform bicubic B-spline -> Bezier; extraordinary vertices are welded watertight). The result
    borrows the template creature's skeleton + materials; every vertex is rigid-skinned to ``bone``
    and every patch goes to limb 0, with connections cleared -- a valid, editable starting point the
    user then re-rigs / re-limbs. Returns (sgm_bytes, n_control_points, n_patches)."""
    from ICParse.codecs import acc
    pool, patches = acc.cage_to_patches(len(coords_blender),
                                        [tuple(float(x) for x in c) for c in coords_blender],
                                        [list(int(i) for i in f) for f in quad_faces], None)
    return patches_to_creature(template_path, pool, patches, bone)


def patches_to_creature(template_path, pool_blender, patches, bone=0, vert_bone=None, patch_limb=None):
    """The creature bytes for a ready patch network (Blender-space ``pool_blender`` (N, 3) + ``patches`` (F, 16)):
    the tail of :func:`mesh_to_creature`, shared with Fit Patches to Mesh (``fitops``), whose pool is the fitted
    one. ``vert_bone`` / ``patch_limb`` give the skin and the limb membership (``fitops.rig_split``: the template's
    own body plan, so no bone of a limb is falsely a stub); without them every point goes on ``bone`` and every
    patch into limb 0. Returns (sgm_bytes, n_control_points, n_patches)."""
    N, F = len(pool_blender), len(patches)
    default_uv = [0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]      # uv slots = corners 0,3,12,15 (engine order)
    owners = np.full(N, bone, dtype=np.int64) if vert_bone is None \
        else np.asarray(vert_bone, dtype=np.int64).reshape(-1)
    limbs = np.zeros(F, dtype=np.int64) if patch_limb is None \
        else np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    if len(owners) != N or len(limbs) != F:
        raise ValueError(f"the split has {len(owners)} owners and {len(limbs)} limb rows for {N} points and {F} patches")
    data = export_grown(template_path, np.asarray(pool_blender, dtype=np.float64), np.asarray(patches),
                        owners, np.zeros(F, dtype=np.int64),
                        np.tile(default_uv, (F, 1)), np.ones(F, dtype=np.int64),
                        limbs, keep_connections=False)
    return data, N, F


def export_bytes(source_path: str, edited_verts_blender: np.ndarray, texture_overrides=None,
                 limb_membership=None, connection_overrides=None, add_animations=None,
                 uv_overrides=None, bone_overrides=None, vert_owner_overrides=None,
                 drop_animations=None) -> bytes:
    """The bytes of :func:`export_model` (byte-identical to the source while untouched)."""
    return sgm_parser.write(export_model(
        source_path, edited_verts_blender, texture_overrides=texture_overrides, limb_membership=limb_membership,
        connection_overrides=connection_overrides, add_animations=add_animations, uv_overrides=uv_overrides,
        bone_overrides=bone_overrides, vert_owner_overrides=vert_owner_overrides, drop_animations=drop_animations))


def export_model(source_path: str, edited_verts_blender: np.ndarray, texture_overrides=None,
                 limb_membership=None, connection_overrides=None, add_animations=None,
                 uv_overrides=None, bone_overrides=None, vert_owner_overrides=None,
                 drop_animations=None):
    """The in-memory model of the original ``.sgm`` with only the edits rewritten (the fast path of
    :func:`build_export`, plus a clip plan). ``connection_overrides``: ``{index: (ints, bone)}`` kept verbatim."""
    rows = None
    if connection_overrides:                     # the whole table (rows are the table; the overrides replace by index)
        src = read_source(source_path)
        rows = [RingEdit(index=i, bone=c.bone_name, raw=list(c.vertex_indices)) for i, c in enumerate(src.limb_connections)]
        for i, (v, b) in connection_overrides.items():
            if not 0 <= int(i) < len(rows):
                raise ValueError(f"connection edit {i}: the creature has {len(rows)} connections")
            rows[int(i)] = RingEdit(index=int(i), bone=b, raw=list(v))
    st = EditState(source_path=source_path, verts=np.asarray(edited_verts_blender, dtype=np.float64),
                   vert_owner=vert_owner_overrides, patch_limb=limb_membership, patch_uv=uv_overrides,
                   connections=rows,
                   bone_rest=bone_overrides, textures=texture_overrides)
    m = build_export_model(st)
    apply_clip_plan(m, add_animations, drop_animations)
    return m

# --- scene objects (structures / characters / props): decode through ICParse ------------------
# A scene ``.sgm`` (FORM NOBS) is a triangle mesh (progressive-mesh stream), not patches. The add-on
# imports it as ordinary Blender meshes so the henchman, structures and props render exactly as the
# engine draws them (positions / normals / UVs / triangles as stored, textures from the game's
# archives). Everything below is bpy-free; ``scene.py`` turns a SceneData into Blender objects.

@dataclass
class ScenePart:
    name: str
    verts: np.ndarray            # (N, 3) Blender space
    normals: np.ndarray          # (N, 3) Blender space, or empty
    uvs: np.ndarray              # (N, 2) Blender convention (V flipped), or empty
    tris: np.ndarray             # (T, 3)
    tri_mat: np.ndarray          # (T,) index into SceneData.materials
    skin: list                   # per vertex [(bone_index, weight), ...] (empty when rigid)
    bind: object = None          # bone index the whole part is rigidly attached to, or None
    mesh_index: int = -1         # index in the file's mesh list (ICParse Scene.meshes); empty meshes are skipped, so it can differ from the part number


@dataclass
class SceneData:
    name: str
    parts: List[ScenePart]
    materials: list              # [{"name", "texture": key or "", "color": (r, g, b)}]
    textures: dict               # key -> (w, h, rgba top-down bytes)
    bone_names: list
    bone_parents: list           # parent index or -1
    bind_world: list             # per bone 4x4 SGM-space bind matrix
    animations: list             # ICParse scene Animation objects
    source_path: str = ""
    scene: object = None         # the parsed ICParse Scene
    missing_textures: list = field(default_factory=list)


def is_scene_model(m) -> bool:
    return hasattr(m, "meshes")


def scene_texture_source(sgm_dir: str, data_root: str = None):
    """An ICParse asset resolver for a scene's ``Data:Art/Textures/*.txr`` references: the .sgm's own
    directory and its ancestors (a loose ``Data/`` tree), then the game install (``data_root``,
    ``$IC_INSTALL`` or the default Steam location) and every ``.sga`` archive in it."""
    import glob
    from ICParse.data import ICData, default_root
    mounts = []
    d = os.path.abspath(sgm_dir)
    for _ in range(5):
        mounts.append(d)
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    root = data_root or default_root()                 # $IC_INSTALL when it is a directory, else the Steam defaults
    if root and os.path.isdir(root):
        mounts.append(root)
        mounts += sorted(glob.glob(os.path.join(root, "*.sga")))
    return ICData(*mounts)


def _basename(ref: str) -> str:
    return str(ref).replace("\\", "/").rsplit("/", 1)[-1].lower()


def resolve_scene_textures(sc, sgm_dir: str, data_root: str = None, texture_source=None):
    """{key: (w, h, rgba top-down)} for every material texture, plus the material table. Embedded
    ``TXTR`` images (structures) win by name; external refs are read through the resolver and decoded
    with ICParse's ``.txr`` codec. Returns (materials, textures, missing). ``texture_source``: an open
    resolver (an ``ICParse.data.ICData``) to read them through instead of :func:`scene_texture_source`
    (the map import passes the map's own files, opened once for all its models)."""
    from ICParse.iff.document import IffDocument
    embedded = {_basename(k): v for k, v in (sc.embedded_textures or {}).items()}
    source = texture_source
    textures, missing, materials = {}, [], []
    for name in sc.materials:
        ref = sc.material_textures.get(name)
        key = ""
        if ref:
            key = _basename(ref)
            if key not in textures:
                emb = embedded.get(key)
                if emb is not None:
                    textures[key] = emb
                else:
                    if source is None:
                        source = scene_texture_source(sgm_dir, data_root)
                    raw = source.read(ref) if ref.lower().endswith(".txr") else None
                    if raw is None and not ref.lower().endswith(".txr"):
                        hit = source.find_by_name(key)
                        raw = source.read(hit) if hit else None
                    dec = None
                    if raw:
                        try:
                            txtr = next(iter(IffDocument.from_bytes(raw).find_all("TXTR")), None)
                            dec = ICtex.decode_txtr(txtr) if txtr is not None else None
                        except Exception:
                            dec = None
                    if dec:
                        textures[key] = dec
                    else:
                        missing.append(ref)
                        key = ""
        col = sc.material_colors.get(name, (0.7, 0.7, 0.7, 1.0))
        materials.append({"name": name, "texture": key, "color": tuple(float(c) for c in col[:3])})
    return materials, textures, missing


def load_scene(path: str, data_root: str = None, texture_source=None) -> SceneData:
    """Decode a scene ``.sgm`` (or the scene a ``.ebp`` points at) into Blender-space parts. ``texture_source``:
    an open resolver to read the textures through (see :func:`resolve_scene_textures`)."""
    from ICParse.codecs import pose as ICpose
    src = resolve_source(path)
    sc = read_source(src)
    if not is_scene_model(sc):
        raise ValueError(f"{path}: not a scene .sgm")
    sgm_dir = os.path.dirname(os.path.abspath(src if src.lower().endswith(".sgm") else path))
    materials, textures, missing = resolve_scene_textures(sc, sgm_dir, data_root, texture_source)
    mat_index = {mt["name"]: i for i, mt in enumerate(materials)}
    parts = []
    for i, mm in enumerate(sc.meshes):
        if not mm.positions:
            continue
        P = np.asarray(mm.positions, dtype=np.float64)
        nv = len(P)
        N = np.asarray(mm.normals, dtype=np.float64) if len(mm.normals) == nv else np.zeros((0, 3))
        UV = np.asarray(mm.uvs, dtype=np.float64).copy() if len(mm.uvs) == nv else np.zeros((0, 2))
        if len(UV):
            UV[:, 1] = 1.0 - UV[:, 1]
        tris, tmat = [], []
        for sub in mm.submeshes:
            mi = mat_index.get(sub.material)
            if mi is None:
                mi = len(materials)
                materials.append({"name": sub.material, "texture": "", "color": (0.7, 0.7, 0.7)})
                mat_index[sub.material] = mi
            for t in sub.triangles:
                if len(set(t)) == 3 and all(0 <= x < nv for x in t):
                    tris.append(tuple(int(x) for x in t)); tmat.append(mi)
        parts.append(ScenePart(
            name=(mm.name.strip() or f"part{i:02d}"), verts=sgm_to_blender(P),
            normals=sgm_to_blender(N) if len(N) else N, uvs=UV,
            tris=np.asarray(tris, dtype=np.int64).reshape(-1, 3), tri_mat=np.asarray(tmat, dtype=np.int64),
            skin=[list(infl) for infl in mm.skin] if mm.skin else [], bind=mm.bind, mesh_index=i))
    rig = ICpose.SceneRig(sc.bones) if sc.bones else None
    base = os.path.splitext(os.path.basename(path))[0]
    return SceneData(
        name=(sc.name or base) if getattr(sc, "name", "") else base, parts=parts, materials=materials,
        textures=textures, bone_names=[b.name for b in sc.bones], bone_parents=(rig.parents if rig else []),
        bind_world=(rig.bind_world if rig else []), animations=list(sc.animations or []),
        source_path=src, scene=sc, missing_textures=missing)


def scene_bone_world(data: SceneData, anim_index: int, t: float):
    """Per-bone SGM world matrices of scene clip ``anim_index`` at normalized time ``t``."""
    from ICParse.codecs import pose as ICpose
    rig = ICpose.SceneRig(data.scene.bones)
    an = data.animations[anim_index]
    tracks = {tr.bone_name.lower(): tr for tr in an.tracks}
    return rig.world(rig.anim_local(tracks, t))
