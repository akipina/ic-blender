"""Editing-loop tools: validate (will it load?), symmetrize, and G1 seam-smooth.

All three operate on the live control points and push a single undo step. The heavy lifting
lives in bpy-free `core` functions so it is unit-tested headlessly.
"""
from __future__ import annotations

import time

import numpy as np

import bpy

from . import core
from . import draw
from . import store


def _verts(obj) -> np.ndarray:
    """The control points as they are now: edit-mode changes are flushed to the mesh first (reading obj.data in edit
    mode would see the state from when edit mode was entered, and writing that back would undo the user's moves)."""
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    me = obj.data
    n = len(me.vertices)
    a = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", a)
    return a.reshape(n, 3)


def _write_verts(obj, verts) -> None:
    obj.data.vertices.foreach_set("co", np.asarray(verts, dtype=np.float32).ravel())
    obj.data.update()
    draw.invalidate(obj.name)


def _selected_mask(obj) -> np.ndarray:
    sel = np.zeros(len(obj.data.vertices), dtype=bool)
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            sel[v.index] = v.select
    else:
        flags = np.zeros(len(obj.data.vertices), dtype=bool)
        obj.data.vertices.foreach_get("select", flags)
        sel = flags
    return sel


class BZ_OT_validate(bpy.types.Operator):
    """Check whether the current creature would load in the Object Editor (errors), whether it has
    everything every base-game creature has (STOCK: clips, bones, rings, IK chains...), advisories, and (Combine
    Checks: Quick) whether it combines right with stock creatures (COMBINE)"""
    bl_idname = "bz.validate"
    bl_label = "Validate (will it load?)"

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def execute(self, context):
        from . import ops
        obj = context.active_object
        # Judge the creature Export would write (in memory): limb, ring, skeleton, skinning and Action edits
        # are all in it, and a grown creature is checked as grown (not as "vertex count changed").
        model = None
        info = {}
        try:
            model = ops.edited_model(obj, context, animations=True, info=info)
            problems = core.validate_model(model, _verts(obj))
            problems += [f"WARN export: {w}" for w in info.get("warnings", [])]
            problems += [p for p in info.get("stock", []) if p not in problems]
        except ops.ExportGatherError as e:
            problems = [f"EXPORT BLOCKED: {e}"]
        except Exception as e:
            problems = [f"export failed, so there is nothing to validate: {type(e).__name__}: {e}"]
        problems += core.check_structure(store.limb_names(obj), store.patch_limb(obj),
                                         store.connections(obj), store.bone_names(obj), store.limb_ids(obj))
        from . import trimops                      # A5: surface the recorded fit / retarget target does not have
        problems += trimops.validate_lines(obj, context)
        problems += self._combine_checks(context, obj, model)
        errs = core.blocking(problems)
        stock = [p for p in problems if p.startswith("STOCK")]
        warns = [p for p in problems if p.startswith("WARN")]
        combine = [p for p in problems if p.startswith(("COMBINE ERROR", "COMBINE WARN"))]
        obj["bz_validation"] = problems
        if not [p for p in problems if not p.startswith("COMBINE INFO")]:
            self.report({"INFO"}, "Valid: should load in the Object Editor, complete as a stock creature"
                                  + (", and combines right" if any(p.startswith("COMBINE INFO ") and " designs (" in p
                                                                   for p in problems) else ""))
            return {"FINISHED"}
        for p in errs:
            self.report({"ERROR"}, p)
        for p in stock + warns + combine:
            self.report({"WARNING"}, p)
        tail = f", {len(combine)} combine issue(s)" if combine else ""
        if errs:
            self.report({"ERROR"}, f"{len(errs)} problem(s) block loading, {len(stock)} stock-rulebook gap(s), "
                                   f"{len(warns)} warning(s){tail} -- see info log")
        else:
            self.report({"WARNING"}, f"Loads in the Object Editor; {len(stock)} stock-rulebook gap(s) (things every "
                                     f"base-game creature has), {len(warns)} warning(s){tail} -- see info log")
        return {"FINISHED"}

    @staticmethod
    def _combine_checks(context, obj, model) -> list:
        """The Quick combine tier (L0 precheck + 13 combines) on the in-memory export model: ``COMBINE ...`` lines
        (never blocking), the summary on the object. With Combine Checks Off only the static precheck runs
        (milliseconds), so the ring-size and canonical-bone checks -- which live in combotest's precheck, not in
        core.check_structure -- are never silently skipped."""
        settings = getattr(context.scene, "bz_combine", None)
        from . import combocore, combopreview
        if not combocore.available():
            return [f"COMBINE INFO setup: combine checks skipped: the combiner (combotest) is not importable "
                    f"({combocore.IMPORT_ERROR})"]
        if model is None:
            return ["COMBINE INFO setup: combine checks skipped: the export failed (see above)"]
        if settings is not None and settings.validate_level == "OFF":
            try:
                edited, comp = combocore.edited_parent(model, obj, default_size=settings.default_size)
                lines = combocore.precheck_messages(edited, combopreview.options(settings))
                lines += combocore.ring_placement_messages(model)
            except Exception as e:
                lines = [f"COMBINE ERROR setup: the precheck failed: {type(e).__name__}: {e}"]
            if "bz_combine_summary" in obj:
                del obj["bz_combine_summary"]          # no Quick run: no summary that could be mistaken for one
            return lines
        wm = context.window_manager
        wm.progress_begin(0, 13)
        t = time.perf_counter()
        try:
            lines = combopreview.validate_combines(context.scene, obj, model,
                                                   progress=lambda i, n: wm.progress_update(i))
        except Exception as e:
            lines = [f"COMBINE ERROR setup: the combine checks failed: {type(e).__name__}: {e}"]
        finally:
            wm.progress_end()
        print(f"[bz validate] Quick combine tier: {time.perf_counter() - t:.2f} s (budget 5 s)")
        return lines


class BZ_OT_symmetrize(bpy.types.Operator):
    """Snap the control net to exact bilateral (X) symmetry"""
    bl_idname = "bz.symmetrize"
    bl_label = "Symmetrize (X)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def execute(self, context):
        obj = context.active_object
        v = _verts(obj)
        pairs, on_plane = core.mirror_pairs(v, axis=0)
        out = core.symmetrize(v, axis=0)
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
            _write_verts(obj, out)
            bpy.ops.object.mode_set(mode="EDIT")
        else:
            _write_verts(obj, out)
        self.report({"INFO"}, f"Symmetrized: {len(pairs)//2} mirror pairs, {len(on_plane)} on-plane "
                              f"({len(pairs)/max(1,len(v))*100:.0f}% paired)")
        return {"FINISHED"}


class BZ_OT_seam_smooth(bpy.types.Operator):
    """Relax interior tangent handles toward C1 across patch seams (local & safe; repeat to
    strengthen). Selected seams only (select all 4 of a seam's points), or all if none selected"""
    bl_idname = "bz.seam_smooth"
    bl_label = "Relax Seam Tangents"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        v = _verts(obj)
        err = store.pool_error(obj)
        if err:                                   # the patch table would move the wrong points
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        mask = _selected_mask(obj) if obj.mode == "EDIT" and _selected_mask(obj).any() else None
        out, count = core.seam_smooth(v, store.patches(obj), selected_mask=mask)
        if count == 0:
            self.report({"WARNING"}, "No seams matched (select all 4 points of a seam, or none for all)")
            return {"CANCELLED"}
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
            _write_verts(obj, out)
            bpy.ops.object.mode_set(mode="EDIT")
        else:
            _write_verts(obj, out)
        self.report({"INFO"}, f"Smoothed {count} seam(s)")
        return {"FINISHED"}


CLASSES = (BZ_OT_validate, BZ_OT_symmetrize, BZ_OT_seam_smooth)
