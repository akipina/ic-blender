"""Parts of a scene object (IC Model tab, *Parts*): which meshes the export writes as the model's parts, which are new, and which
were deleted, with *Make Part of Scene Object* (a selected mesh becomes a new part of the active part's model) and *Leave
Out of Model* (undo that). The writing itself is ``scene.export_scene_object`` over ICParse ``sgm.scene.parts``."""
from __future__ import annotations

import bpy
from . import ui_kit
from bpy.props import EnumProperty

from .. import scene as _scene

_NONE = "__none__"
_ITEMS = {}          # Blender keeps no reference to a dynamic enum's strings: they live here
_COUNTS = {}         # source path -> (mtime, number of parts in the file), so a redraw reads no file


def _file_parts(src: str) -> int:
    """How many parts the source file holds (ICParse: the meshes with geometry), cached by modification time."""
    import os
    from .. import core
    path = core.resolve_source(src)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return -1
    hit = _COUNTS.get(path)
    if hit is None or hit[0] != mt:
        from ICParse.sgm.scene import model as SM
        sc = SM.read_scene(path)
        hit = _COUNTS[path] = (mt, sum(1 for m in sc.meshes if m.positions))
    return hit[1]


def _model_part(context):
    """The imported part the panel speaks for: the active object if it is one, else the first selected one, else the part
    a selected new part belongs to."""
    cands = [context.active_object] + list(context.selected_objects or [])
    for o in cands:
        if o is not None and o.type == "MESH" and o.get(_scene.KEY_SCENE) and o.get(_scene.KEY_SCENE_PART) is not None:
            return o
    for o in cands:
        if o is not None and o.get(_scene.KEY_NEW_PART):
            return next((p for p in bpy.data.objects if p.get(_scene.KEY_SCENE_PART) is not None
                         and p.get("bz_source") == o.get("bz_source") and p.get("bz_rig") == o.get("bz_rig")), None)
    return None


def _bone_items(self, context):
    part = _model_part(context)
    arm = _scene.model_root(part) if part is not None else None
    items = [(_NONE, "(none: keep its own weights)", "A part with no bone weights does not move with any bone")]
    if arm is not None and arm.type == "ARMATURE":
        items += [(b.name, b.name, f"The part follows '{b.name}'") for b in arm.data.bones]
    _ITEMS["bone"] = items
    return items


def _like_items(self, context):
    part = _model_part(context)
    items = [(_NONE, "(visible in every clip)", "The new part shows in every clip")]
    if part is not None:
        try:
            names = [o.name for o in _scene.part_objects(part)]
        except ValueError:
            names = []
        items += [(n, n, f"The new part shows and hides with '{n}' in each clip") for n in names]
    _ITEMS["like"] = items
    return items


class BZ_OT_scene_add_part(bpy.types.Operator):
    """Make the selected meshes new parts of the active scene object: the export writes each as a part of the model"""
    bl_idname = "bz.scene_add_part"
    bl_label = "Make Part of Scene Object"
    bl_options = {"REGISTER", "UNDO"}

    bone: EnumProperty(name="Follows bone", items=_bone_items,
                       description="A rigid part follows this bone (the whole mesh at full weight); none keeps its own "
                                   "vertex groups: one group at full weight follows that bone, several make it skinned")
    like: EnumProperty(name="Shows like", items=_like_items,
                       description="Show and hide the new part with this part in every clip (for a build stage or a "
                                   "damage piece), or keep it visible in every clip")

    @classmethod
    def poll(cls, context):
        return _model_part(context) is not None and any(o.type == "MESH" and not o.get(_scene.KEY_SCENE)
                                                        for o in context.selected_objects or [])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        part = _model_part(context)
        arm = _scene.model_root(part)
        arm = arm if arm is not None and arm.type == "ARMATURE" else None
        made = []
        for o in context.selected_objects:
            if o.type != "MESH" or o.get(_scene.KEY_SCENE):
                continue
            o[_scene.KEY_NEW_PART] = True
            o["bz_source"] = part.get("bz_source", "")
            o["bz_name"] = part.get("bz_name", "")
            if part.get("bz_rig"):
                o["bz_rig"] = part["bz_rig"]
            elif "bz_rig" in o:
                del o["bz_rig"]
            o[_scene.KEY_PART_LIKE] = "" if self.like == _NONE else self.like
            if not o.material_slots and part.material_slots:     # the model's own materials, so its faces have a shader
                for slot in part.material_slots:
                    o.data.materials.append(slot.material)
            if arm is not None:
                if not any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers):
                    mod = o.modifiers.new("Armature", "ARMATURE")
                    mod.object = arm
                    mod.use_vertex_groups = True
                if self.bone != _NONE:
                    for g in [g for g in o.vertex_groups if g.name in arm.data.bones]:
                        o.vertex_groups.remove(g)
                    o.vertex_groups.new(name=self.bone).add(list(range(len(o.data.vertices))), 1.0, "REPLACE")
            made.append(o.name)
        if not made:
            self.report({"WARNING"}, "Select the meshes to add (not parts of a model) and the model's part last")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{len(made)} new part(s): {', '.join(made[:4])}. The next export writes them into the model.")
        return {"FINISHED"}


class BZ_OT_scene_remove_new_part(bpy.types.Operator):
    """Leave the selected new parts out of the model again (the meshes stay in Blender)"""
    bl_idname = "bz.scene_remove_new_part"
    bl_label = "Leave Out of Model"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.get(_scene.KEY_NEW_PART) for o in context.selected_objects or [])

    def execute(self, context):
        n = 0
        for o in context.selected_objects:
            if o.get(_scene.KEY_NEW_PART):
                for k in (_scene.KEY_NEW_PART, _scene.KEY_PART_LIKE):
                    if k in o:
                        del o[k]
                n += 1
        self.report({"INFO"}, f"{n} mesh(es) left out of the model")
        return {"FINISHED"}


def draw_parts(layout, context) -> None:
    part = _model_part(context)
    if part is None:
        layout.label(text="Select a part of a scene object.")
        return
    try:
        parts = _scene.part_objects(part)
    except ValueError as e:
        layout.label(text=str(e)[:60], icon="ERROR")
        return
    new = _scene.new_part_objects(part)
    layout.label(text=f"{len(parts)} part(s) from the file, {len(new)} new")
    for o in new:
        like = o.get(_scene.KEY_PART_LIKE, "")
        row = layout.row()
        row.label(text=o.name + (f"  (shows like {like})" if like else ""), icon="ADD")
    try:
        n_file = _file_parts(part.get("bz_source", ""))
    except Exception:                                                     # noqa: BLE001 -- the panel never fails over this
        n_file = len(parts)
    present = {int(o[_scene.KEY_SCENE_PART]) for o in parts}
    marked = {int(x) for o in parts for x in (o.get(_scene.KEY_REMOVE_PARTS) or [])} - present
    if marked:                                    # Use This Mesh as the Model: these go at the next export whatever the option says
        layout.label(text=f"{len(marked)} part(s) are removed by the next export", icon="TRASH")
    gone = n_file - len(parts) - len(marked)
    if gone > 0:
        col = layout.column(align=True)
        col.label(text=f"{gone} part(s) deleted here: they stay in the model", icon="INFO")
        col.label(text="unless the export removes them (its option).")
    row = layout.row(align=True)
    row.operator(BZ_OT_scene_add_part.bl_idname, icon="ADD")
    row.operator(BZ_OT_scene_remove_new_part.bl_idname, text="Leave Out", icon="REMOVE")
    col = layout.column(align=True)
    col.operator("bz.scene_replace_part", icon="FILE_REFRESH")             # sceneobj/ops_part_replace.py
    col.operator("bz.scene_use_mesh_as_model", icon="MESH_DATA")


class BZ_PT_scene_parts(bpy.types.Panel):
    bl_label = "Parts"
    bl_idname = "BZ_PT_scene_parts"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_order = 10
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _model_part(context) is not None

    def draw(self, context):
        draw_parts(self.layout, context)


CLASSES = (BZ_OT_scene_add_part, BZ_OT_scene_remove_new_part, BZ_PT_scene_parts)
