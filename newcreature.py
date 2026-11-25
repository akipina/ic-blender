"""New Creature: start a creature from nothing, as a native control net ready to edit and export.

The scaffold comes from ``ICParse.sgm.creature.scaffold`` (the single home of the from-scratch
builder, shared with ``ICParse/tools/make_test_creature.py``): the canonical 48-bone skeleton, a
connected torso / head / tail / four-leg body joined at stock-layout connection rings (so the game's
combiner can graft it), a labelled texture, the standard clip set and the base-game IK chains. The
operator writes that creature to the ``.sgm`` the user picks -- that file is the object's *source*,
exactly as for an imported creature -- and imports it through the normal import path, so editing,
Validate, animation Actions and Export all work unchanged.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper


class BZ_OT_new_creature(bpy.types.Operator, ExportHelper):
    """Create a new creature: skeleton, simple body, texture and stock animations"""
    bl_idname = "bz.new_creature"
    bl_label = "New Creature (From Scratch)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm", options={"HIDDEN"})
    creature_name: StringProperty(
        name="Name", default="NewCreature",
        description="Creature name; limbs, connection rings and the texture are named after it")
    detail: IntProperty(
        name="Detail", default=1, min=1, max=3,
        description="Sections per body span (1 = the coarsest scaffold, 3 = three times the patches)")
    with_clips: BoolProperty(
        name="Standard Clips", default=True,
        description="Add simple placeholder animations (idle, walk, run, attacks and more)")
    with_ik: BoolProperty(name="IK Chains", default=True, description="Add the base-game IK chain set")
    clips_as_actions: BoolProperty(
        name="Clips as Actions", default=True,
        description="Bake the clips into named Actions (editable in the Action Editor; Export writes them back)")

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = self.creature_name + self.filename_ext
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        from ICParse.sgm.creature import scaffold
        from ICParse.sgm import parser as sgm_parser
        from ICParse import validate as ICval
        name = (self.creature_name or "NewCreature").strip() or "NewCreature"
        try:
            model = scaffold.build_scaffold_creature(name, detail=int(self.detail), clips=self.with_clips,
                                                     ik=self.with_ik)
            data = sgm_parser.write(model)
        except Exception as e:
            self.report({"ERROR"}, f"Build failed: {e}")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        if not path.lower().endswith(".sgm"):
            path += ".sgm"
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            self.report({"ERROR"}, f"Cannot write {path}: {e}")
            return {"CANCELLED"}
        res = bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=self.clips_as_actions)
        if "FINISHED" not in res:
            return {"CANCELLED"}
        problems = ICval.check_creature_rulebook(model)
        errs = [p for p in problems if p.startswith("ERROR")]
        warns = [p for p in problems if p.startswith("WARN")]
        for p in errs:
            self.report({"ERROR"}, p)
        for p in warns:
            self.report({"WARNING"}, p)
        self.report({"INFO"}, f"New creature {name}: {len(model.vertices)} control points, {len(model.patches)} patches, "
                              f"{len(model.bones)} bones, {len(model.limb_connections)} rings, {len(model.animations)} clips"
                              f" -> {os.path.basename(path)} (rulebook {'clean' if not problems else f'{len(errs)} errors, {len(warns)} warnings'})")
        return {"FINISHED"}


def menu_add(self, context):
    self.layout.separator()
    self.layout.operator(BZ_OT_new_creature.bl_idname, text="Impossible Creatures Creature (From Scratch)",
                         icon="OUTLINER_OB_ARMATURE")


CLASSES = (BZ_OT_new_creature,)
