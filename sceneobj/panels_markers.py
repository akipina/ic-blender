"""Markers panel (IC Model tab of the 3D view sidebar): the attach points of the selected gatherer, character, structure or prop.

List of the markers (name, bone, how many .ebp events name it, a warning when an event names something the model lacks), and
buttons: Add (at the 3D cursor, on the rig's active bone when the rig is in Pose Mode), Delete, Rename, Bone (move it to another bone
or to model space, keeping its place) and Events (which .ebp events use it). Move and rotate a marker with Blender's own tools.
Export writes the markers with the model (File > Export Scene Object, Export Scene Geometry, Export Entity Folder).
"""
from __future__ import annotations

import os

import bpy
from . import ui_kit
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Matrix

from . import slice_markers as SM
from .. import enumitems

ORDER = 51

_REFS_CACHE: dict = {}     # ebp path -> (mtime, {value: count}, [(event name, anim, key, value)])


def _ebp_path(key):
    parts = SM.parts_of(key)
    if not parts:
        return None
    p = parts[0].get("bz_ebp")
    if p and os.path.isfile(bpy.path.abspath(str(p))):
        return bpy.path.abspath(str(p))
    src = key[0]
    if src.lower().endswith(".ebp"):
        return src if os.path.isfile(src) else None
    stem = os.path.splitext(src)[0]
    for ext in (".ebp", ".EBP", ".Ebp"):
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def ebp_refs(key):
    """({value: count}, [(event name, anim, key, value)]) of the .ebp's marker references, or ({}, []) without a readable .ebp."""
    path = _ebp_path(key)
    if not path:
        return {}, []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}, []
    hit = _REFS_CACHE.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1], hit[2]
    try:
        from ICParse.ebp import parser as EP
        with open(path, "rb") as f:
            e = EP.read(f.read())
        refs = EP.event_marker_refs(e)
        rows = []
        for c in e._chunks("EVNT"):
            name, pairs = EP.decode_event(bytes(c.raw))
            anim = next((v for k, v in pairs if k == "anim"), "")
            rows += [(name, anim, k, v) for k, v in pairs if k.lower() in EP.MARKER_KEYS]
    except Exception:                                                     # noqa: BLE001 -- no list rather than a broken panel
        refs, rows = {}, []
    _REFS_CACHE[path] = (mtime, refs, rows)
    return refs, rows


def _active_key(context):
    return SM.entity_key(context.active_object)


def _active_marker(context):
    obj = context.active_object
    return obj if SM.is_marker(obj) else None


def _bone_of(obj) -> str:
    return obj.parent_bone if obj.parent is not None and obj.parent_type == "BONE" else ""


class BZ_OT_marker_add(bpy.types.Operator):
    """Add a marker at the 3D cursor (on the rig's active bone in Pose Mode, else in model space)"""
    bl_idname = "bz.marker_add"
    bl_label = "Add Marker"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="")
    on_bone: BoolProperty(name="On the active bone", default=True)

    @classmethod
    def poll(cls, context):
        return _active_key(context) is not None

    def execute(self, context):
        key = _active_key(context)
        name = self.name.strip() or SM.unique_name(key)
        why = SM.name_problem(key, name)
        if why:
            self.report({"ERROR"}, why)
            return {"CANCELLED"}
        bone = ""
        arm = SM.marker_rig(key)
        if self.on_bone and arm is not None:
            pb = context.active_pose_bone if context.active_object is arm else None
            bone = pb.name if pb is not None else ""
        cur = context.scene.cursor
        world = Matrix.Translation(cur.location)
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        e = SM.add_marker(context, key, name, world, bone)
        for o in context.view_layer.objects:
            o.select_set(False)
        e.select_set(True)
        context.view_layer.objects.active = e
        self.report({"INFO"}, f"Added marker '{name}'" + (f" on bone '{bone}'" if bone else " in model space"))
        return {"FINISHED"}


class BZ_OT_marker_delete(bpy.types.Operator):
    """Delete the selected markers (the events in the blueprint that name them then play at the model's feet)"""
    bl_idname = "bz.marker_delete"
    bl_label = "Delete Marker"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(SM.is_marker(o) for o in context.selected_objects) or _active_marker(context) is not None

    def execute(self, context):
        objs = [o for o in context.selected_objects if SM.is_marker(o)] or [_active_marker(context)]
        key = SM.entity_key(objs[0])
        refs, _rows = ebp_refs(key)
        low = {}
        for v, n in refs.items():
            low[v.lower()] = low.get(v.lower(), 0) + n
        names = [SM.marker_name(o) for o in objs]
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
        used = sum(low.get(n.lower(), 0) for n in names if SM.can_find(n))
        self.report({"WARNING"} if used else {"INFO"}, f"Deleted {len(names)} marker(s)" +
                    (f"; {used} event(s) in the blueprint named them and will play at the model's feet" if used else ""))
        return {"FINISHED"}


class BZ_OT_marker_rename(bpy.types.Operator):
    """Rename the active marker (Export Entity Folder renames it in the blueprint's events too)"""
    bl_idname = "bz.marker_rename"
    bl_label = "Rename Marker"
    bl_options = {"REGISTER", "UNDO"}

    new_name: StringProperty(name="New name", default="")

    @classmethod
    def poll(cls, context):
        return _active_marker(context) is not None

    def invoke(self, context, event):
        self.new_name = SM.marker_name(_active_marker(context))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = _active_marker(context)
        new = self.new_name.strip()
        try:
            old = SM.rename_marker(obj, new)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        refs, _rows = ebp_refs(SM.entity_key(obj))
        n = sum(c for v, c in refs.items() if v.lower() == old.lower())
        self.report({"INFO"}, f"Renamed '{old}' to '{new}'" + (f"; Export Entity Folder renames it in {n} event(s) of the blueprint" if n else ""))
        return {"FINISHED"}


@enumitems.stable
def _bone_items(self, context):
    key = _active_key(context)
    arm = SM.marker_rig(key) if key else None
    items = [("__NONE__", "None (model space)", "Not on a bone: it does not follow the animation")]
    if arm is not None:
        items += [(b.name, b.name, "") for b in arm.data.bones]
    return items


class BZ_OT_marker_set_bone(bpy.types.Operator):
    """Put the active marker on another bone, or in model space, keeping where it is"""
    bl_idname = "bz.marker_set_bone"
    bl_label = "Marker Bone"
    bl_options = {"REGISTER", "UNDO"}

    bone: EnumProperty(name="Bone", items=_bone_items)

    @classmethod
    def poll(cls, context):
        return _active_marker(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = _active_marker(context)
        bone = "" if self.bone == "__NONE__" else self.bone
        try:
            SM.set_marker_bone(obj, bone)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"'{SM.marker_name(obj)}' is now " + (f"on bone '{bone}'" if bone else "in model space"))
        return {"FINISHED"}


class BZ_OT_marker_select(bpy.types.Operator):
    """Select this marker"""
    bl_idname = "bz.marker_select"
    bl_label = "Select Marker"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Object", default="")

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or not SM.is_marker(obj):
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError:
                pass
        for o in context.view_layer.objects:
            o.select_set(False)
        if obj.name in context.view_layer.objects:
            obj.select_set(True)
            context.view_layer.objects.active = obj
        return {"FINISHED"}


class BZ_OT_marker_select_users(bpy.types.Operator):
    """List the events in the blueprint that place an effect or sound at the active marker"""
    bl_idname = "bz.marker_select_users"
    bl_label = "Marker Events"

    @classmethod
    def poll(cls, context):
        return _active_marker(context) is not None

    def execute(self, context):
        obj = _active_marker(context)
        name = SM.marker_name(obj).lower()
        _refs, rows = ebp_refs(SM.entity_key(obj))
        hits = [r for r in rows if r[3].lower() == name]
        if not hits:
            self.report({"INFO"}, f"No event in the blueprint names '{SM.marker_name(obj)}'")
            return {"FINISHED"}
        for ev, anim, k, _v in hits[:12]:
            self.report({"INFO"}, f"event '{ev}' of clip '{anim}' ({k})")
        self.report({"INFO"}, f"{len(hits)} event(s) in the blueprint name '{SM.marker_name(obj)}'")
        return {"FINISHED"}


def draw_markers(layout, context) -> None:
    key = _active_key(context)
    if key is None:
        return
    objs = SM.markers_of(key)
    refs, _rows = ebp_refs(key)
    low = {}
    for v, n in refs.items():
        low[v.lower()] = low.get(v.lower(), 0) + n
    col = layout.column(align=True)
    if not objs:
        col.label(text="No markers.")
    active = context.active_object
    for o in objs:
        row = col.row(align=True)
        name = SM.marker_name(o)
        op = row.operator("bz.marker_select", text=name, depress=o is active, icon="EMPTY_ARROWS")
        op.name = o.name
        row.label(text=_bone_of(o) or "model space")
        if not SM.can_find(name):
            row.label(text="not found", icon="ERROR")      # GetMarkerIndex never finds a name starting with '0'
            continue
        n = low.get(name.lower(), 0)
        row.label(text=f"{n} ev" if n else "")
    names = {SM.marker_name(o).lower() for o in objs}
    arm = SM.marker_rig(key)
    bones = {b.name.lower() for b in arm.data.bones} if arm is not None else set()
    missing = sorted(v for v in refs if not SM.finds(v, names | bones))
    if missing:
        box = layout.box()
        box.label(text=f"{len(missing)} blueprint name(s) have no marker:", icon="ERROR")
        box.label(text=", ".join(missing[:6]) + (" ..." if len(missing) > 6 else ""))
    row = layout.row(align=True)
    row.operator("bz.marker_add", text="Add", icon="ADD")
    row.operator("bz.marker_delete", text="Delete", icon="REMOVE")
    row = layout.row(align=True)
    row.operator("bz.marker_rename", text="Rename")
    row.operator("bz.marker_set_bone", text="Bone")
    row.operator("bz.marker_select_users", text="Events")
    mk = _active_marker(context)
    if mk is not None and any(abs(s - 1.0) > 1e-4 for s in mk.scale):
        layout.label(text="Scaled marker: its effect scales too.", icon="ERROR")


class BZ_PT_scene_markers(bpy.types.Panel):
    bl_label = "Markers"
    bl_idname = "BZ_PT_scene_markers"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_order = 20
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _active_key(context) is not None

    def draw(self, context):
        draw_markers(self.layout, context)


CLASSES = (BZ_OT_marker_add, BZ_OT_marker_delete, BZ_OT_marker_rename, BZ_OT_marker_set_bone, BZ_OT_marker_select,
           BZ_OT_marker_select_users, BZ_PT_scene_markers)
