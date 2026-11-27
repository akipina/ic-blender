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
    """Check whether the creature will load, matches stock creatures and combines"""
    bl_idname = "bz.validate"
    bl_label = "Validate (will it load?)"

    @classmethod
    def poll(cls, context):
        return store.active_creature(context) is not None            # the creature, or its rig

    def execute(self, context):
        from . import ops
        obj = store.active_creature(context)
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
        if model is not None:                      # clip names the game will not play (decomp MotionTree::Build wiring)
            from . import startcheck
            problems += startcheck.clip_lines(model, store.source_path(obj))
        from . import trimops                      # A5: surface the recorded fit / retarget target does not have
        problems += trimops.validate_lines(obj, context)
        problems += self._combine_checks(context, obj, model)
        obj["bz_check_numbers"] = []                      # the numbers of an earlier Check are stale when this one stops before the export check
        if model is not None and not core.blocking(problems):
            # What Export for the Game would refuse (the item check, events, motions, numbers): found now, so a creature that passes
            # Check is one Export accepts. Nothing is written.
            from . import gameexport
            ex_err, ex_warn = gameexport.check_lines(obj, context)
            # A creature that is still being built (it has connection rings, and the checks above list something every game creature has and
            # this one lacks, e.g. one ring) cannot be exported or combined YET: that is said as a warning with what to do, not as an error,
            # so the modelling tools carry on. A creature with no rings at all (a plain mesh) is not being built on, it needs Retarget
            # Creature to Mesh, and with nothing of that kind missing an export refusal stays an error.
            unfinished = any(p.startswith("STOCK") for p in problems) and bool(store.connections(obj))
            for e in ex_err:
                if e in problems:
                    continue
                if unfinished and e.startswith("the game would not take the item"):
                    todo = ("Create the missing connection (Boner, Create Connection) and pick its ring, "
                            if "connection ring" in e else "Add what the other lines say this creature lacks, ")
                    problems.append(f"WARN export: Export for the Game and the creature creator cannot take this creature yet: {e} {todo}"
                                    "then press Check again. Until then you can go on modelling.")
                else:
                    problems.append(e)
            problems += [f"WARN export: {w}" for w in ex_warn if not any(w in p for p in problems)]
        errs = core.blocking(problems)
        stock = [p for p in problems if p.startswith("STOCK")]
        warns = [p for p in problems if p.startswith("WARN")]
        combine = [p for p in problems if p.startswith(("COMBINE ERROR", "COMBINE WARN"))]
        obj["bz_validation"] = problems
        numbers = [str(t) for t in obj.get("bz_check_numbers", [])] if model is not None else []        # what the changed numbers come to (gameexport.check_lines)
        if not [p for p in problems if not p.startswith("COMBINE INFO")]:
            self.report({"INFO"}, "Valid: the game should load this creature, and Export for the Game accepts it"
                                  + (", and combines right" if any(p.startswith("COMBINE INFO ") and " designs (" in p
                                                                   for p in problems) else ""))
            for t in numbers:
                self.report({"INFO"}, t)
            return {"FINISHED"}
        from .sceneobj.ui_kit import plain
        for p in errs:
            self.report({"ERROR"}, plain(p))
        for p in stock + warns + combine:
            self.report({"WARNING"}, plain(p))
        for t in numbers:
            self.report({"INFO"}, t)
        tail = f", {len(combine)} combine issue(s)" if combine else ""
        if errs:
            self.report({"ERROR"}, f"{len(errs)} problem(s) stop the game loading or exporting this creature, {len(stock)} thing(s) every "
                                   f"game creature has and this one lacks, {len(warns)} warning(s){tail}. The Info editor lists them")
        else:
            self.report({"WARNING"}, f"The game loads this creature; {len(stock)} thing(s) every game creature has and this one lacks, "
                                     f"{len(warns)} warning(s){tail}. The Info editor lists them")
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
    """Smooth the handles across patch seams (the selected seams, or all)"""
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
