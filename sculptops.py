"""Sculpt Surface / Refit Patches: sculpt the baked bicubic surface with Blender's Sculpt Mode, then write
the sculpt back into the control points -- only the patches whose samples moved change, the rest stay
byte-identical on export.

Sculpt Surface bakes the exact surface (core.tessellate, density n) into a mesh whose vertices are the bake's
samples in tessellate order (sample s = patch s // n**2, grid (a, b)) with coincident seam samples MERGED, so
the brushes see one connected skin and do not tear it at patch seams. The mesh remembers what it is a sculpt
of (creature name + uid, density, patch count, a hash of the control points at the bake), the merge map
(sample -> vertex, an ID property, ``KEY_MAP``) and every vertex's bake position (``bz_rest``, float32 like
the mesh, so an untouched vertex compares exactly equal). Refit Patches scatters the vertices back to every
sample they represent, finds the samples that moved and calls the ONE solver, ICParse.codecs.fit.refit_samples,
which pins the control points of patches with no moved sample (and no point shared with a moved patch), then
writes the fitted pool into the creature through the journaled edit (ops._begin) so Undo restores it.
"""
from __future__ import annotations

import hashlib

import numpy as np

import bpy

from . import core
from . import draw
from . import edittools
from . import store

KEY_OF = "bz_sculpt_of"                  # the creature object's name at the bake
KEY_UID = "bz_sculpt_uid"                # store.uid of the creature (follows a renamed creature)
KEY_DENSITY = "bz_sculpt_density"        # samples per patch edge
KEY_NPATCH = "bz_sculpt_npatch"          # patches at the bake
KEY_NVERTS = "bz_sculpt_nverts"          # merged vertices at the bake (a changed topology is refused)
KEY_MAP = "bz_sculpt_map"                # sample index -> mesh vertex (npatch * density**2 ints)
KEY_POOL_SHA = "bz_sculpt_pool_sha1"     # sha1 of the creature's float32 control points at the bake (stale guard)
KEY_HIDDEN = "bz_sculpt_hidden"          # names of the objects the bake hid (shown again by Refit / Discard)
ATTR_PATCH = "bz_patch"                  # per vertex: the patch of its first sample
ATTR_SAMPLE = "bz_sample"                # per vertex: its first sample index (patch * n*n + a * n + b)
ATTR_REST = "bz_rest"                    # per vertex: its bake position (float32, exactly the initial mesh position)

SUFFIX = "_sculpt"
MERGE_TOL = 1e-7                         # of the bounding-box diagonal: coincident seam samples differ by rounding only
SYMMETRIC_FRACTION = 0.9                 # paired-or-on-plane control points needed to call a creature symmetric


class SculptError(RuntimeError):
    """A readable reason a sculpt mesh can't be made or refit (nothing was changed)."""


def is_sculpt(obj) -> bool:
    return bool(obj is not None and obj.type == "MESH" and obj.get(KEY_OF) is not None)


def creature_of(sculpt):
    """The native creature a sculpt mesh was baked from (by uid, else by its recorded name), or None."""
    uid = str(sculpt.get(KEY_UID, ""))
    if uid:
        for o in bpy.data.objects:
            if store.is_native(o) and store.uid(o) == uid:
                return o
    o = bpy.data.objects.get(str(sculpt.get(KEY_OF, "")))
    return o if store.is_native(o) else None


def sculpts_of(obj) -> list:
    """The sculpt meshes baked from creature ``obj`` (those still in the file)."""
    return [o for o in bpy.data.objects if is_sculpt(o) and creature_of(o) is obj]


def pool_sha1(verts) -> str:
    """Identity of a control-point set as the mesh holds it (float32)."""
    return hashlib.sha1(np.ascontiguousarray(np.asarray(verts, dtype=np.float32)).tobytes()).hexdigest()


def _sculpt_verts(sculpt) -> np.ndarray:
    me = sculpt.data
    a = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def _rest(sculpt) -> np.ndarray:
    attr = sculpt.data.attributes.get(ATTR_REST)
    if attr is None or attr.domain != "POINT" or attr.data_type != "FLOAT_VECTOR":
        raise SculptError(f"'{sculpt.name}' has no '{ATTR_REST}' attribute (the bake positions): it is not a sculpt "
                          "mesh made by Sculpt Surface, or the attribute was removed. Sculpt Surface again")
    a = np.empty(len(attr.data) * 3, dtype=np.float32)
    attr.data.foreach_get("vector", a)
    return a.reshape(-1, 3).astype(np.float64)


def moved_samples(sculpt) -> np.ndarray:
    """(Nverts,) bool: vertices that are no longer at their bake position (exact float32 compare)."""
    return (_sculpt_verts(sculpt) != _rest(sculpt)).any(1)


def in_progress_warnings(obj) -> list:
    """Export / Validate warning lines for the sculpt meshes of creature ``obj`` that were not refit yet."""
    out = []
    for sc in sculpts_of(obj):
        try:
            k = int(moved_samples(sc).sum())
            state = f"{k} vertex(es) moved, not refit yet" if k else "nothing moved yet"
        except SculptError:
            state = "its bake data is damaged"
        out.append(f"a sculpt of this creature is in progress ('{sc.name}': {state}): Refit Patches, or delete "
                   f"{sc.name}, before you rely on this export")
    return out


# ------------------------------------------------------------------------------------------------ the bake

def merge_samples(V: np.ndarray, tol: float):
    """Merge coincident bake samples: (U (M, 3) merged positions in first-occurrence order, first (M,) the sample
    each merged vertex takes its position from, sample_to_vertex (len(V),))."""
    q = np.round(np.asarray(V, dtype=np.float64) / tol).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    order = np.argsort(first, kind="stable")                 # merged vertices follow the tessellate order
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order))
    first = first[order]
    return V[first], first, rank[inv]


def bake_sculpt_mesh(obj, context, density: int):
    """Bake creature ``obj`` at ``density`` into a new ``<name>_sculpt`` object (linked, not yet active); returns
    (sculpt object, number of merged vertices, number of samples, symmetric: bool)."""
    from . import ops
    err = store.pool_error(obj)
    if err:
        raise SculptError(err)
    verts = edittools._verts(obj)
    patches = store.patches(obj)
    n = int(density)
    K = n * n
    V, T, _ = core.tessellate(verts, patches, n=n)
    if len(V) == 0:
        raise SculptError("nothing to sculpt: the creature has no patches")
    diag = float(np.linalg.norm(verts.max(0) - verts.min(0))) or 1.0
    U, first, s2v = merge_samples(V, MERGE_TOL * diag)
    F = s2v[T]
    keep = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])   # a collapsed patch edge: no face
    F, T = F[keep], T[keep]
    name = obj.name + SUFFIX
    me = bpy.data.meshes.new(name)
    me.from_pydata(U.tolist(), [], F.tolist())
    for p in me.polygons:
        p.use_smooth = True
    me.update()
    loop_v = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_v)
    if not np.array_equal(loop_v, F.reshape(-1)):
        raise SculptError("Blender re-ordered the bake's face corners: the per-corner UVs can't be placed")
    a = me.attributes.new(ATTR_PATCH, "INT", "POINT")
    a.data.foreach_set("value", (first // K).astype(np.int32))
    a = me.attributes.new(ATTR_SAMPLE, "INT", "POINT")
    a.data.foreach_set("value", first.astype(np.int32))
    a = me.attributes.new(ATTR_REST, "FLOAT_VECTOR", "POINT")
    rest = np.empty(len(U) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", rest)                      # exactly the float32 the mesh holds
    a.data.foreach_set("vector", rest)
    try:
        ops.BZ_OT_bake._apply_appearance(obj, me, patches, n, face_patch=T[:, 0] // K, loop_sample=T.reshape(-1))
    except Exception as e:                                   # the skin is a convenience; say so, keep the sculpt
        print(f"[bz sculpt] appearance not applied: {type(e).__name__}: {e}")
    sc = bpy.data.objects.new(name, me)
    sc.matrix_world = obj.matrix_world.copy()
    context.collection.objects.link(sc)
    sc[KEY_OF] = obj.name
    sc[KEY_UID] = store.uid(obj)
    sc[KEY_DENSITY] = n
    sc[KEY_NPATCH] = int(len(patches))
    sc[KEY_NVERTS] = int(len(U))
    sc[KEY_MAP] = [int(i) for i in s2v]
    sc[KEY_POOL_SHA] = pool_sha1(verts)
    pairs, on_plane = core.mirror_pairs(verts, axis=0)
    symmetric = (len(pairs) + len(on_plane)) >= SYMMETRIC_FRACTION * max(1, len(verts))
    me.use_mirror_x = bool(symmetric)                        # Sculpt Mode's X symmetry lives on the mesh
    return sc, len(U), len(V), symmetric


def show_creature(sculpt) -> None:
    """Show again what the bake hid (only what was visible before it)."""
    for name in list(sculpt.get(KEY_HIDDEN, [])):
        o = bpy.data.objects.get(str(name))
        if o is not None:
            try:
                o.hide_set(False)
            except RuntimeError:                             # not in this view layer
                pass
    sculpt[KEY_HIDDEN] = []


# ------------------------------------------------------------------------------------------------ the refit

def gather(sculpt):
    """Everything Refit needs, checked: (creature, density, pool (N, 3), samples (Np*K, 3), rest (Np*K, 3),
    moved_vertices (bool per mesh vertex)). Raises SculptError with the reason."""
    obj = creature_of(sculpt)
    if obj is None:
        raise SculptError(f"the creature '{sculpt.get(KEY_OF)}' this sculpt was baked from is no longer in the file: "
                          f"nothing to refit (delete {sculpt.name}, or import the creature and Sculpt Surface again)")
    err = store.pool_error(obj)
    if err:
        raise SculptError(err)
    n = int(sculpt.get(KEY_DENSITY, 0))
    K = n * n
    patches = store.patches(obj)
    Np = len(patches)
    if n < 2 or int(sculpt.get(KEY_NPATCH, -1)) != Np:
        raise SculptError(f"'{sculpt.name}' was baked from {sculpt.get(KEY_NPATCH)} patches at density {n}; the "
                          f"creature now has {Np} patches: Sculpt Surface again")
    nv = len(sculpt.data.vertices)
    if nv != int(sculpt.get(KEY_NVERTS, -1)):
        raise SculptError(f"the topology of '{sculpt.name}' changed ({nv} vertices, the bake had "
                          f"{sculpt.get(KEY_NVERTS)}): Refit needs the bake's vertices (no Dyntopo, remesh, delete "
                          "or subdivide). Undo the topology change, or Sculpt Surface again")
    s2v = np.asarray(list(sculpt.get(KEY_MAP, [])), dtype=np.int64)
    if len(s2v) != Np * K or (len(s2v) and (s2v.min() < 0 or s2v.max() >= nv)):
        raise SculptError(f"'{sculpt.name}' has no valid sample map ({len(s2v)} entries for {Np * K} samples at "
                          f"density {n}): Sculpt Surface again")
    verts = edittools._verts(obj)
    if pool_sha1(verts) != str(sculpt.get(KEY_POOL_SHA, "")):
        raise SculptError(f"the control points of '{obj.name}' changed since '{sculpt.name}' was baked, so its "
                          "samples no longer lie on the creature's surface: Undo the change, or Sculpt Surface again "
                          "(and redo the sculpt on the new bake)")
    ms = np.asarray(sculpt.matrix_world) - np.asarray(obj.matrix_world)
    if np.abs(ms).max() > 1e-6:
        raise SculptError(f"'{sculpt.name}' was moved, rotated or scaled as an object: its vertices are read in "
                          "the creature's space. Clear its transform (Alt+G, Alt+R, Alt+S) and Refit again")
    P = _sculpt_verts(sculpt)
    R = _rest(sculpt)
    return obj, n, verts, P[s2v], R[s2v], (P != R).any(1)


def refit(sculpt):
    """Refit the creature to the sculpt: (creature, fitted pool (N, 3), fit result or None when nothing moved,
    stats dict)."""
    from ICParse.codecs import fit
    obj, n, pool, samples, rest, moved_v = gather(sculpt)
    patches = store.patches(obj)
    stats = {"moved_vertices": int(moved_v.sum()), "samples": len(samples), "density": n, "npatch": len(patches)}
    if not moved_v.any():
        stats.update(moved_points=0, patches_refit=0, patches_untouched=len(patches), rms=0.0, max=0.0)
        return obj, pool, None, stats
    r = fit.refit_samples(pool, patches, n, samples, original_samples=rest)
    changed = (r.pool != pool).any(1)                        # the solver snaps unmoved points back to the input
    touched = changed[patches].any(1)
    stats.update(moved_points=int(changed.sum()), patches_refit=int(touched.sum()),
                 patches_untouched=int((~touched).sum()), rms=r.rms, max=r.max)
    return obj, r.pool, r, stats


def write_pool(obj, pool) -> None:
    """Write fitted control points into the creature (in whatever mode it is)."""
    if obj.mode == "EDIT":
        bpy.ops.object.mode_set(mode="OBJECT")
        edittools._write_verts(obj, pool)
        bpy.ops.object.mode_set(mode="EDIT")
    else:
        edittools._write_verts(obj, pool)
    draw.invalidate(obj.name)


def _report_line(stats) -> str:
    return (f"Refit {stats['patches_refit']} patches ({stats['moved_points']} control points moved); residual "
            f"rms {stats['rms'] * 1000:.2f} mm, max {stats['max'] * 1000:.2f} mm; {stats['patches_untouched']} "
            f"patches untouched")


# ------------------------------------------------------------------------------------------------ operators

def _to_object_mode(context, obj) -> None:
    if obj.mode != "OBJECT":
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")


def _select_only(context, obj) -> None:
    for o in context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


class BZ_OT_sculpt_surface(bpy.types.Operator):
    """Bake the creature's exact surface into a connected mesh and sculpt it with Blender's brushes (the creature
    and its rig are hidden meanwhile); Refit Patches then writes the sculpt back into only the patches you touched"""
    bl_idname = "bz.sculpt_surface"
    bl_label = "Sculpt Surface"
    bl_options = {"REGISTER", "UNDO"}

    density: bpy.props.IntProperty(
        name="Density", default=7, min=4, max=12,
        description="Samples per patch edge (n x n per patch): more gives the brushes finer vertices, but a bicubic "
                    "patch only holds smooth shapes -- Refit reports what the patches could not take")
    enter_sculpt: bpy.props.BoolProperty(
        name="Enter Sculpt Mode", default=True,
        description="Switch to Sculpt Mode on the new mesh (X symmetry on when the creature is symmetric)")

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context)

    def execute(self, context):
        from . import ops
        obj = ops._active_native(context)
        try:
            sc, nverts, nsamples, symmetric = bake_sculpt_mesh(obj, context, self.density)
        except SculptError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _to_object_mode(context, obj)
        hidden = []
        for o in (obj, store.rig(obj)):
            if o is not None and not o.hide_get():
                o.hide_set(True)
                hidden.append(o.name)
        sc[KEY_HIDDEN] = hidden
        _select_only(context, sc)
        mode = "Object Mode"
        if self.enter_sculpt:
            try:
                bpy.ops.object.mode_set(mode="SCULPT")
                mode = "Sculpt Mode"
            except RuntimeError as e:
                self.report({"WARNING"}, f"could not enter Sculpt Mode ({e}): use the mode menu")
        self.report({"INFO"}, f"Sculpting '{sc.name}': {nverts} vertices ({nsamples} samples at density {self.density}"
                              f"), X symmetry {'on' if symmetric else 'off'}, in {mode}. Refit Patches writes it back")
        return {"FINISHED"}


def _sculpt_for(context):
    """The sculpt mesh Refit / Discard act on: the active one, else the (single) one of the active creature."""
    obj = context.active_object
    if is_sculpt(obj):
        return obj, None
    if store.is_native(obj):
        scs = sculpts_of(obj)
        if len(scs) == 1:
            return scs[0], None
        if scs:
            return None, f"'{obj.name}' has {len(scs)} sculpt meshes: select the one to use"
        return None, f"'{obj.name}' has no sculpt mesh: Sculpt Surface first"
    return None, "select a sculpt mesh (or its creature)"


class BZ_OT_refit_patches(bpy.types.Operator):
    """Write the sculpt back into the creature's control points: only patches with moved samples are refit (the
    rest export byte-identical); the creature is shown again and the sculpt mesh removed"""
    bl_idname = "bz.refit_patches"
    bl_label = "Refit Patches"
    bl_options = {"REGISTER", "UNDO"}

    keep: bpy.props.BoolProperty(name="Keep Sculpt Mesh", default=False,
                                 description="Leave the sculpt mesh in the file (hidden) instead of removing it")

    @classmethod
    def poll(cls, context):
        sc, why = _sculpt_for(context)
        if sc is None:
            cls.poll_message_set(why)
            return False
        return True

    def execute(self, context):
        from . import ops
        sc, why = _sculpt_for(context)
        if sc is None:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        _to_object_mode(context, sc)                         # Sculpt Mode's strokes are flushed to the mesh
        try:
            obj, pool, r, stats = refit(sc)
        except SculptError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if r is not None:
            if not ops._begin(self, obj):
                return {"CANCELLED"}
            write_pool(obj, pool)
        show_creature(sc)
        _select_only(context, obj)
        if self.keep:
            sc.hide_set(True)
        else:
            me = sc.data
            bpy.data.objects.remove(sc, do_unlink=True)
            if me.users == 0:
                bpy.data.meshes.remove(me)
        if r is None:
            self.report({"INFO"}, f"Nothing moved in the sculpt: '{obj.name}' is unchanged ({stats['npatch']} patches "
                                  "untouched)")
        else:
            self.report({"INFO"}, _report_line(stats))
        obj["bz_last_refit"] = stats
        return {"FINISHED"}


class BZ_OT_discard_sculpt(bpy.types.Operator):
    """Throw the sculpt away: show the creature again and remove the sculpt mesh (the creature is unchanged)"""
    bl_idname = "bz.discard_sculpt"
    bl_label = "Discard Sculpt"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _sculpt_for(context)[0] is not None

    def execute(self, context):
        sc, why = _sculpt_for(context)
        if sc is None:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        _to_object_mode(context, sc)
        obj = creature_of(sc)
        show_creature(sc)
        me = sc.data
        name = sc.name
        bpy.data.objects.remove(sc, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
        if obj is not None:
            _select_only(context, obj)
        self.report({"INFO"}, f"Discarded '{name}'" + (f"; '{obj.name}' unchanged" if obj is not None else ""))
        return {"FINISHED"}


CLASSES = (BZ_OT_sculpt_surface, BZ_OT_refit_patches, BZ_OT_discard_sculpt)
