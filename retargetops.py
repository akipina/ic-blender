"""Retarget Creature to Mesh: keep the creature's patches, limbs, rings, skeleton, skinning and clips and move only its
control points (``ICParse.codecs.fit.fit_points``, journaled, so one Undo restores it).

Defaults: rings pinned (stock socket geometry), target aligned into the creature's frame, symmetry kept, seam smoothing
0 (stock seams are only C0), bones moved with their points (exported as overrides). ``retarget_pool`` stays bpy-free.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import numpy as np

from ICParse.codecs import fit

try:
    from . import core, fitops
except ImportError:                      # imported top-level (a pure test puts ic_blender/ on sys.path)
    import core  # type: ignore[no-redef]
    import fitops  # type: ignore[no-redef]

try:
    import bpy
    _Operator = bpy.types.Operator
except ImportError:                      # no Blender: the pipeline only
    bpy = None
    _Operator = object

SNAP = 1e-6            # of the creature's size: a control point that moved less than this is put back EXACTLY, so a
                       # fit to the creature's own surface (a float32 bake) changes no byte (solve_pool's snap rule)
BONE_MIN_MOVE = 1e-6   # metres: a bone displaced less than this is not touched (the rest snapshot compares at 1e-6)
SURFACE_N = 7          # tessellation density of the creature's surface for its bounding box (the alignment)
CANDIDATES = 16        # grid samples each target point is projected from: a point ON a thin double-sided feature (an
                       # ear, a toe) can have the 4 nearest all on the neighbouring patch and converge there (cheetah
                       # bake: 10 of 10,370 points 0.9-1.9 mm off, which the solve turned into 279 moved points); with
                       # 16 every point of the creature's own bake projects to within 0.06 um (float32 noise)
CREATURE_SYMMETRIC_FRACTION = 0.9   # paired-or-on-plane control points to call a creature's net X-symmetric (a stock net
                                    # is only near-symmetric: cheetah 98.2 % at the pairing tolerance; sculptops' rule)


class RetargetError(ValueError):
    """A plain-language reason the retarget was refused (reported as is)."""


@dataclass
class Retarget:
    pool0: np.ndarray                    # the creature's control points before
    pool: np.ndarray                     # ... after (symmetrized when asked and possible, snapped)
    patches: np.ndarray
    points: np.ndarray                   # the target points as fitted (aligned into the creature's frame)
    before: fitops.FitStats              # target-to-surface distances before the fit
    after: fitops.FitStats               # ... after (measured on the pool as written)
    residual: np.ndarray                 # per target vertex
    coverage: fitops.FitStats            # surface-sample-to-nearest-target distances
    moved: np.ndarray                    # (N,) bool: control points that changed (past the snap)
    fixed: np.ndarray                    # the pinned pool indices (sorted, unique)
    scale: float = 1.0                   # the target was scaled by this into the creature's frame (1 = not aligned)
    shift: np.ndarray = field(default_factory=lambda: np.zeros(3))   # ... and moved by this (after scaling)
    creature_symmetric: bool = False
    target_symmetric: bool = False
    symmetrized: bool = False            # the displacement was mirrored
    paired: int = 0                      # net points with a mirror partner
    on_plane: int = 0
    seconds: float = 0.0
    history: List[tuple] = field(default_factory=list)
    coverage_raw: Optional[np.ndarray] = None   # per surface sample (patch order, 7 x 7 per patch): distance to the
                                                # nearest target point (what ``coverage`` summarises)

    @property
    def symmetry_text(self) -> str:
        if self.symmetrized:
            return (f"symmetry kept ({self.paired} control points paired, {self.on_plane} on the plane: the "
                    "displacement is mirrored, the net stays as symmetric as it was)")
        if not self.creature_symmetric and not self.target_symmetric:
            return "symmetry none (neither the creature's net nor the target is X-symmetric)"
        if not self.creature_symmetric:
            return "symmetry none (the creature's net is not X-symmetric)"
        return "symmetry none (the target is not X-symmetric)"


def surface_points(pool, patches, n: int = SURFACE_N) -> np.ndarray:
    """Samples of the creature's exact surface (its extent, not the control net's, which sits outside it)."""
    V, _T, _N = core.tessellate(np.asarray(pool, dtype=np.float64), np.asarray(patches, dtype=np.int64), n=n)
    return np.asarray(V, dtype=np.float64)


def align_target(points, pool, patches):
    """The target points centred on the creature's surface bounding box and scaled uniformly by the ratio of the
    bounding diagonals: (aligned points, scale, shift) with ``aligned = scale * points + shift``."""
    P = np.asarray(points, dtype=np.float64)
    S = surface_points(pool, patches)
    if not len(S):
        raise RetargetError("the creature has no surface to align to")
    dt = fitops.bounding_diagonal(P)
    dc = fitops.bounding_diagonal(S)
    if dt <= 0 or dc <= 0:
        raise RetargetError("the target or the creature has no extent (all points coincide)")
    scale = dc / dt
    ct = (P.max(0) + P.min(0)) / 2.0
    cc = (S.max(0) + S.min(0)) / 2.0
    shift = cc - scale * ct
    return P * scale + shift, float(scale), shift


def ring_points(connections, n: int) -> np.ndarray:
    """The pool indices every connection ring names (``store.connections``: (name, bone, ring)), unique and sorted;
    an index outside the pool is ignored (the ring store guard reports those)."""
    idx = sorted({int(v) for _n, _b, ring in connections for v in ring if 0 <= int(v) < n})
    return np.asarray(idx, dtype=np.int64)


def symmetrize_displacement(pool0, pool, pairs, on_plane, fixed_mask) -> np.ndarray:
    """``pool0 + d`` with the displacement ``d = pool - pool0`` made exactly X-symmetric over the net's mirror
    ``pairs`` (partners' displacements averaged; a pinned partner's is 0, so its free partner takes 0 too: both stay)
    and no X component on ``on_plane`` points. The result is as symmetric as ``pool0`` was."""
    P0 = np.asarray(pool0, dtype=np.float64)
    d = np.asarray(pool, dtype=np.float64) - P0
    flip = np.array([-1.0, 1.0, 1.0])
    for i, j in pairs.items():
        if i < j:
            fi, fj = bool(fixed_mask[i]), bool(fixed_mask[j])
            if fi and fj:
                continue
            if fi:
                d[j] = d[i] * flip
            elif fj:
                d[i] = d[j] * flip
            else:
                avg = (d[i] + d[j] * flip) / 2.0
                d[i] = avg
                d[j] = avg * flip
    for i in on_plane:
        if not fixed_mask[i]:
            d[i, 0] = 0.0
    return P0 + d


def retarget_pool(pool0, patches, points, normals=None, iters: int = 10, smooth: float = 0.0,
                  fixed: Optional[Iterable[int]] = None, symmetric: bool = True,
                  scale_to_target: bool = True, sym_base=None) -> Retarget:
    """The whole pipeline: align the target into the creature's frame (``scale_to_target``) -> ``fit.fit_points``
    on the creature's own pool and patches with ``fixed`` points pinned -> with ``symmetric``, the displacement
    mirrored when both the net and the target are X-symmetric -> sub-``SNAP`` moves put back exactly -> the metrics
    (``after`` and ``residual`` measure the pool as written). Refuses (``RetargetError``) an empty layout, no or
    non-finite target points, and a fit that ended worse than it started. ``sym_base`` (default ``pool0``): the net
    whose mirror pairs decide and define the symmetry, and the base the mirrored displacement is measured from -- the
    landmark retarget starts the fit from a warped net but keeps the ORIGINAL creature's symmetry."""
    P0 = np.asarray(pool0, dtype=np.float64).reshape(-1, 3)
    PS = P0 if sym_base is None else np.asarray(sym_base, dtype=np.float64).reshape(-1, 3)
    if len(PS) != len(P0):
        raise RetargetError(f"the symmetry base has {len(PS)} points, the pool {len(P0)}")
    patches = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    if not len(patches) or not len(P0):
        raise RetargetError("the creature has no patches to retarget")
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(pts):
        raise RetargetError("the target has no vertices")
    if not np.isfinite(pts).all():
        raise RetargetError("the target has non-finite (NaN/inf) vertex positions")
    nrm = None
    if normals is not None:
        nrm = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        if len(nrm) != len(pts):
            raise RetargetError(f"{len(nrm)} normals for {len(pts)} target vertices")
    t0 = time.perf_counter()
    scale, shift = 1.0, np.zeros(3)
    if scale_to_target:
        pts, scale, shift = align_target(pts, P0, patches)
    diag = fitops.bounding_diagonal(pts)
    if diag <= 0:
        raise RetargetError("the target's vertices all coincide")
    N = len(P0)
    fx = np.zeros(N, dtype=bool)
    if fixed is not None:
        idx = np.asarray(sorted({int(i) for i in fixed}), dtype=np.int64)
        if len(idx) and (idx.min() < 0 or idx.max() >= N):
            raise RetargetError(f"a pinned index is outside the {N}-point pool")
        fx[idx] = True
    else:
        idx = np.zeros(0, dtype=np.int64)
    _, _, _, d0 = fit.project(pts, P0, patches, n=9, candidates=CANDIDATES)
    before = fitops.stats(d0, diag)
    r = fit.fit_points(P0, patches, pts, iters=int(iters), smooth=float(smooth), normals=nrm,
                       fixed=idx if len(idx) else None, candidates=CANDIDATES)
    pool = np.asarray(r.pool, dtype=np.float64)
    if not np.isfinite(pool).all():
        raise RetargetError("the fit produced non-finite control points (the solve failed)")
    eps = max(fitops.SYMMETRY_EPS * diag, 1e-9)
    c_sym = fitops.symmetry(PS, diag)[2] >= CREATURE_SYMMETRIC_FRACTION
    t_sym = fitops.is_x_symmetric(pts, diag)
    symmetrized = False
    paired = on_plane_n = 0
    if symmetric and c_sym and t_sym:
        pairs, on_plane = core.mirror_pairs(PS, axis=0, eps=eps)
        pool = symmetrize_displacement(PS, pool, pairs, on_plane, fx)
        paired, on_plane_n, symmetrized = len(pairs), len(on_plane), True
    small = np.linalg.norm(pool - P0, axis=1) <= SNAP * diag
    pool[small] = P0[small]
    pool[fx] = P0[fx]                                   # pinned: exactly the input, whatever the arithmetic
    moved = ~small
    _, _, _, residual = fit.project(pts, pool, patches, n=9, candidates=CANDIDATES)
    after = fitops.stats(residual, diag)
    if after.rms > before.rms and moved.any():
        raise RetargetError(f"the fit diverged: {after.text()} after, {before.text()} before -- try fewer "
                            "iterations, or a target closer to the creature's shape (scale to target on)")
    return Retarget(pool0=P0, pool=pool, patches=patches, points=pts, before=before, after=after,
                    residual=np.asarray(residual, dtype=np.float64), coverage=fitops.stats(r.coverage, diag),
                    moved=moved, fixed=idx, scale=scale, shift=shift, creature_symmetric=c_sym,
                    target_symmetric=t_sym, symmetrized=symmetrized, paired=paired, on_plane=on_plane_n,
                    seconds=time.perf_counter() - t0, history=list(r.history),
                    coverage_raw=np.asarray(r.coverage, dtype=np.float64))


def bone_moves(owners, names, parents, disp):
    """{rig bone name: (3,) mean displacement of the control points it owns}; a bone owning no points takes its
    parent's move (a root without points: none). ``owners`` = owner key per control point (``store.vert_bone``),
    ``names`` = the rig's bone name per owner key, ``parents`` = {bone name: parent name or None} for every rig bone,
    ``disp`` = (N, 3) displacement per control point. Returns (moves, inherited count)."""
    owners = np.asarray(owners, dtype=np.int64).reshape(-1)
    D = np.asarray(disp, dtype=np.float64).reshape(-1, 3)
    own = {}
    if len(owners) == len(D):
        for k in np.unique(owners):
            k = int(k)
            if 0 <= k < len(names) and names[k]:
                own[str(names[k])] = D[owners == k].mean(0)
    moves, inherited = {}, 0

    def move_of(name, depth=0):
        if name in moves:
            return moves[name]
        if name in own:
            moves[name] = own[name]
            return own[name]
        p = parents.get(name)
        m = move_of(p, depth + 1) if p and depth < len(parents) + 1 else np.zeros(3)
        moves[name] = m
        return m

    for name in parents:
        move_of(name)
        if name not in own and float(np.linalg.norm(moves[name])) > 0:
            inherited += 1
    return moves, inherited


def report_line(creature: str, mesh: str, n_patches: int, r: Retarget, pin_rings: bool, bones_text: str) -> str:
    """``Retargeted Cheetah to Wolf: 288 patches, rms ...; rings pinned (60 points); symmetry kept ...; bones ...``
    (the operator report, stored as ``bz_last_retarget``)."""
    rings = (f"rings pinned ({len(r.fixed)} points)" if pin_rings
             else f"rings free ({len(r.fixed)} ring points moved with the fit: the combiner will scale them and may warn)")
    aligned = (f"target scaled x{r.scale:.4f} into the creature's frame" if r.scale != 1.0 or np.abs(r.shift).max() > 0
               else "target taken as is")
    return (f"Retargeted {creature} to {mesh}: {n_patches} patches, {r.after.text()}; {rings}; {r.symmetry_text}; "
            f"{bones_text}; {aligned}; {int(r.moved.sum())} control points moved, before the fit rms "
            f"{r.before.rms_pct:.2f} %; in {r.seconds:.1f} s")


# ------------------------------------------------------------------------------------------------ Blender

if bpy is not None:
    from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
    from mathutils import Vector
    from . import edittools, ops, rig, sculptops, store

    def _mesh_search(self, context, edit_text):
        return [o.name for o in context.scene.objects
                if o.type == "MESH" and not store.is_native(o) and not sculptops.is_sculpt(o)]


def _target_arrays(obj, tgt, me_eval):
    """(points, unit normals) of the evaluated target mesh IN THE CREATURE'S LOCAL SPACE (the target's world
    coordinates through the inverse of the creature's world matrix)."""
    pts, nrm = fitops._mesh_arrays(me_eval)
    M = np.array(obj.matrix_world.inverted() @ tgt.matrix_world, dtype=np.float64)
    P = pts @ M[:3, :3].T + M[:3, 3]
    Nn = nrm @ np.linalg.inv(M[:3, :3])                 # normals transform by the inverse transpose
    ln = np.linalg.norm(Nn, axis=1, keepdims=True); ln[ln == 0] = 1.0
    return P, Nn / ln


def rotation_angle(R0, R1) -> float:
    """The angle (radians) between two rotation matrices."""
    c = (np.trace(np.asarray(R0)[:3, :3].T @ np.asarray(R1)[:3, :3]) - 1.0) / 2.0
    return float(np.arccos(np.clip(c, -1.0, 1.0)))


def move_rig_bones(context, obj, arm, disp, warp=None):
    """Translate the rig's rest bones by :func:`bone_moves` (edit bones: head and tail together, the roll re-aligned
    to the bone's old Z axis, hierarchy untouched), through the rest-pose edit path Export detects. Returns (bones
    moved, max move in metres, bones that took their parent's move, bones that own points, max orientation drift in
    radians). The drift is Blender's, not the move's: a bone is stored as float32 head / tail / roll, so a bone whose
    child sits a fraction of a millimetre away (cheetah: front_foot -> front_endfoot) has its direction quantized at
    ulp / length, about 7e-4 rad -- restoring such a bone from its own matrix drifts as much, and so does any
    hand-made rest edit of it (P11). The creature must be in Object Mode (the caller sees to it).
    ``warp`` (the landmark retarget): a callable mapping (K, 3) armature-local points to their warped positions,
    applied to every bone's head AND tail before the translation (the bone turns with the warp; the returned angle
    is then the warp's turn, not a drift); ``disp`` is then the fit's displacement from the warped net."""
    owners = store.vert_bone(obj) if store.has_vert_bone(obj) else np.zeros(0, dtype=np.int64)
    names = store.bone_rig_names(obj)
    parents = {b.name: (b.parent.name if b.parent is not None else None) for b in arm.data.bones}
    moves, inherited = bone_moves(owners, names, parents, disp)
    owning = sum(1 for k in np.unique(owners) if 0 <= int(k) < len(names) and names[int(k)])
    todo = {n: m for n, m in moves.items() if float(np.linalg.norm(m)) > BONE_MIN_MOVE}
    new_ht = {}
    if warp is not None:
        bl = list(arm.data.bones)
        H = np.array([b.head_local for b in bl], dtype=np.float64).reshape(-1, 3)
        T = np.array([b.tail_local for b in bl], dtype=np.float64).reshape(-1, 3)
        Hw, Tw = warp(H), warp(T)
        for i, b in enumerate(bl):
            m = moves.get(b.name, np.zeros(3))
            h, t = Hw[i] + m, Tw[i] + m
            if max(float(np.linalg.norm(h - H[i])), float(np.linalg.norm(t - T[i]))) > BONE_MIN_MOVE:
                new_ht[b.name] = (h, t)
        todo = {b.name: new_ht[b.name][0] - H[i] for i, b in enumerate(bl) if b.name in new_ht}
    if not todo:
        return 0, 0.0, 0, owning, 0.0
    before = {b.name: np.array(b.matrix_local, dtype=np.float64) for b in arm.data.bones}
    prev_active = context.view_layer.objects.active
    was_hidden = arm.hide_get()
    if was_hidden:
        arm.hide_set(False)                                # edit mode needs a visible armature
    rig._set_mode(context, arm, "EDIT")
    try:
        for eb in arm.data.edit_bones:
            m = todo.get(eb.name)
            if m is not None:
                z = Vector(before[eb.name][:3, 2].tolist())
                ht = new_ht.get(eb.name)
                if ht is not None:
                    eb.head = Vector(ht[0].tolist())
                    eb.tail = Vector(ht[1].tolist())
                else:
                    eb.translate(Vector((float(m[0]), float(m[1]), float(m[2]))))
                eb.align_roll(z)
    finally:
        rig._set_mode(context, arm, "OBJECT")
        if was_hidden:
            arm.hide_set(True)
        context.view_layer.objects.active = prev_active
    drift = max(rotation_angle(before[n], np.array(arm.data.bones[n].matrix_local)) for n in todo
                if n in arm.data.bones)
    biggest = max(float(np.linalg.norm(m)) for m in todo.values())
    own_names = {str(names[int(k)]) for k in np.unique(owners) if 0 <= int(k) < len(names)}
    return len(todo), biggest, sum(1 for n in todo if n not in own_names), owning, drift


def resolve_target(op, context, obj, name):
    """The target mesh of a retarget: (object, evaluated mesh), or None after ``op.report``-ing why it is refused --
    no name, no such object, the creature itself, not a mesh, another creature, no faces, or more than one shell
    **after a positional weld** (seam-split vertices are not loose parts -- see :func:`fitops.target_topology`;
    the same rules for Retarget and Landmark Retarget)."""
    if not name:
        op.report({"ERROR"}, "Choose a target mesh (the shape the creature should take)")
        return None
    tgt = bpy.data.objects.get(name)
    if tgt is None:
        op.report({"ERROR"}, f"No object called '{name}' in the file")
        return None
    if tgt is obj:
        op.report({"ERROR"}, f"{obj.name} is the creature itself: choose another mesh as the target")
        return None
    if tgt.type != "MESH":
        op.report({"ERROR"}, f"{tgt.name} is a {tgt.type.lower()} object, not a mesh")
        return None
    if store.is_native(tgt):
        op.report({"ERROR"}, f"{tgt.name} is a patch creature (its vertices are control points, not a surface): "
                             "Bake Textured Mesh it first and retarget to the bake")
        return None
    deps = context.evaluated_depsgraph_get()
    me_eval = tgt.evaluated_get(deps).data              # modifiers applied: the shape the viewport shows
    if len(me_eval.polygons) == 0:
        op.report({"ERROR"}, f"{tgt.name} has no faces to fit to")
        return None
    # The surface, not the index buffer: seam-split vertices (an atlased glTF, an unwelded bake) are welded on
    # a throwaway copy before the target is judged, so only real floaters are refused. The mesh is untouched.
    pts, _ = fitops._mesh_arrays(me_eval)
    topo = fitops.target_topology(me_eval, fitops.bounding_diagonal(pts))
    if topo.parts != 1:
        op.report({"ERROR"}, f"{fitops.parts_reason(tgt.name, topo)}: a creature is one closed skin. Merge by Distance or "
                             "separate extra shells (Edit Mode: P > By Loose Parts) and retarget to the skin alone")
        return None
    return tgt, me_eval


class BZ_OT_retarget_mesh(_Operator):
    """Fit this creature to another mesh, keeping its patches, skeleton and animations"""
    bl_idname = "bz.retarget_mesh"
    bl_label = "Retarget Creature to Mesh"
    bl_options = {"REGISTER", "UNDO"}

    if bpy is not None:
        target: StringProperty(name="Target Mesh", default="", search=_mesh_search,
                               description="The mesh whose shape the creature takes")
        iterations: IntProperty(name="Iterations", default=10, min=1, max=50,
                                description="Project-and-solve rounds of the fit")
        smooth: FloatProperty(name="Seam Smoothness", default=0.0, min=0.0, soft_max=1.0, precision=3,
                              description="How strongly seams are smoothed; 0 keeps them as in the game")
        pin_rings: BoolProperty(name="Pin Rings", default=True,
                                description="Keep connection rings in place so the creature combines as before")
        symmetry: BoolProperty(name="Keep Symmetry", default=True,
                               description="Keep the result symmetric when the creature and target are symmetric in X")
        scale_to_target: BoolProperty(name="Scale to Target", default=True,
                                      description="First centre and scale the target to fit the creature")
        move_bones: BoolProperty(name="Move Bones", default=True,
                                 description="Move the rest bones along with the skin they own")

    @classmethod
    def poll(cls, context):
        return ops._guard_poll(cls, context)

    def execute(self, context):
        obj = ops._active_native(context)
        if obj is None:
            self.report({"ERROR"}, "Select a patch creature to retarget")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        got = resolve_target(self, context, obj, self.target)
        if got is None:
            return {"CANCELLED"}
        tgt, me_eval = got
        t_all = time.perf_counter()
        pool0 = edittools._verts(obj)
        patches = store.patches(obj)
        pts, nrm = _target_arrays(obj, tgt, me_eval)
        fixed = ring_points(store.connections(obj), len(pool0)) if self.pin_rings else None
        n_ring = len(ring_points(store.connections(obj), len(pool0)))
        try:
            r = retarget_pool(pool0, patches, pts, normals=nrm, iters=self.iterations, smooth=self.smooth,
                              fixed=fixed, symmetric=self.symmetry, scale_to_target=self.scale_to_target)
        except RetargetError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Retarget failed: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        if not self.pin_rings:
            r.fixed = ring_points(store.connections(obj), len(pool0))     # for the report: how many ring points there are
        arm = store.rig(obj)
        bones_text = "bones not moved (no rig)" if arm is None else "bones kept at their bind pose"
        n_bones = 0
        bone_max = bone_drift = 0.0
        if r.moved.any():
            if not ops._begin(self, obj):
                return {"CANCELLED"}
            mode = obj.mode
            if mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
            sculptops.write_pool(obj, r.pool)
            if self.move_bones and arm is not None:
                try:
                    n_bones, bone_max, inherited, owning, bone_drift = move_rig_bones(context, obj, arm,
                                                                                      r.pool - r.pool0)
                    bones_text = (f"bones: {n_bones} moved (max {1000 * bone_max:.1f} mm; {owning} own points, "
                                  f"{inherited} followed their parent; orientation kept to "
                                  f"{1000 * bone_drift:.2f} mrad)")
                except Exception as e:
                    self.report({"WARNING"}, f"the bones were not moved ({type(e).__name__}: {e}); the control points "
                                             "were retargeted, the rig keeps its bind pose")
                    bones_text = "bones kept at their bind pose (moving them failed)"
            if mode == "EDIT":
                context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode="EDIT")
        r.seconds = time.perf_counter() - t_all
        line = report_line(obj.name, tgt.name, len(patches), r, self.pin_rings, bones_text)
        obj["bz_last_retarget"] = line
        obj["bz_retarget_source"] = tgt.name      # Transfer Texture's and Trim Stray Surface's default source
        obj["bz_last_retarget_stats"] = {
            "rms": r.after.rms, "p95": r.after.p95, "max": r.after.max, "diag": r.after.diag,
            "rms_before": r.before.rms, "moved_points": int(r.moved.sum()), "ring_points": int(n_ring),
            "rings_pinned": bool(self.pin_rings), "symmetrized": bool(r.symmetrized), "paired": int(r.paired),
            "on_plane": int(r.on_plane), "scale": float(r.scale), "bones_moved": int(n_bones),
            "bone_max_move": float(bone_max), "bone_max_drift": float(bone_drift), "coverage_max": r.coverage.max,
            "seconds": r.seconds}
        if not r.moved.any():
            self.report({"INFO"}, f"{obj.name} already fits {tgt.name}: no control point moved ({r.after.text()})")
            return {"FINISHED"}
        if not ops._is_identity(tgt.matrix_world):
            self.report({"WARNING"}, f"{tgt.name} has an object transform: its world shape was used (transformed into "
                                     f"{obj.name}'s space)")
        if not ops._is_identity(obj.matrix_world):
            self.report({"WARNING"}, f"{obj.name} has an object transform: the fit is in its local space, and Export "
                                     "refuses a creature with a transform (apply or clear it)")
        if not self.pin_rings:
            self.report({"WARNING"}, f"the {n_ring} connection-ring control points moved with the fit, so combining may "
                                     "scale them and warn; Pin Rings keeps the stock rings")
        if r.coverage.max_pct > 2.0:
            self.report({"WARNING"}, f"parts of the retargeted surface are up to {r.coverage.max_pct:.1f} % of the "
                                     "size away from any target vertex (the target has no vertices there)")
        if bone_drift > 2e-3:
            self.report({"WARNING"}, f"a moved bone's orientation drifted by {1000 * bone_drift:.1f} mrad (very short bones "
                                     "lose precision): the rest shape is exact, clips differ by that much")
        if not self.move_bones and arm is not None:
            self.report({"INFO"}, "the bones keep their bind pose (valid; they may sit outside the new skin -- Move "
                                  "Bones translates them by the skin they own)")
        self.report({"INFO"}, line)
        return {"FINISHED"}


CLASSES = (BZ_OT_retarget_mesh,) if bpy is not None else ()
