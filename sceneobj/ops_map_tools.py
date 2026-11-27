"""sceneobj.ops_map_tools -- the buttons of the Mission Editor's conveniences for an imported map (the work is in ``map_tools`` and ``map_crop``).

File > Import / Export entries (and the Map panel's "Files" section):
* Height Map (.tga): the ground as a grey picture, or a picture read into the ground's points;
* Terrain (.ter): the ground, water, textures, sky, light and decals, as the Mission Editor's File > Export Terrain / Import Terrain;
* Entities (.ent): the selected placed objects, as File > Export Selected Entities / Import Entities.

And the operators of the Object List (filters; Delete, Hide, Select, Unhide All), the Blueprint List, and Terrain Crop (Validate, Crop).
Nothing here writes into the game's own folder (a Steam game folder is refused, as for the map save).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import map_crop as CR
from . import map_tools as T
from . import ops_map_export as X
from . import ops_map_import as M
from . import panels_blueprint as PB
from .. import enumitems

ORDER = 64

LIST_CAP = 3000                                   # rows the Object List shows at once (a map holds up to ~10 000 objects)
TAG_PROPS = (("static", "f_static"), ("stock", "f_stock"), ("henchman", "f_henchman"), ("resource", "f_resource"), ("lab", "f_lab"),
             ("sound", "f_sound"), ("marker", "f_marker"))


# ---------------------------------------------------------------------------------------------------- properties
class BZ_PG_map_row(bpy.types.PropertyGroup):
    """One row of the Object List."""
    entity_id: IntProperty(default=-1)
    label: StringProperty()
    blueprint: StringProperty()
    owner_text: StringProperty()
    obj_name: StringProperty()
    pick: BoolProperty(name="Pick", default=False, description="Tick rows for Hide, Select and Delete")


class BZ_PG_map_bp(bpy.types.PropertyGroup):
    """One row of the Blueprint List."""
    net_id: IntProperty()
    label: StringProperty()
    path: StringProperty()
    ref_count: IntProperty()
    trigger_refs: IntProperty()


@enumitems.stable
def _owner_items(self, context):
    items = [("all", "All entities", "Every placed object"), ("selected", "Selected in the 3D view", "Only the objects selected in the 3D view"),
             ("nature", "Nature", "Trees, rocks, resources and the other nature entities"), ("world", "No player", "Objects that belong to no player")]
    return items + [(str(i), f"Player {i + 1}", f"Objects of the player in slot {i + 1}") for i in range(8)]


class BZ_PG_map_tools(bpy.types.PropertyGroup):
    """The settings of the Object List, the Blueprint List and Terrain Crop (on the scene)."""
    owner: EnumProperty(name="Show", items=_owner_items, description="Which owner's objects to list")
    f_static: BoolProperty(name="Static objects", description="Trees, rocks, buildings, villagers and the other characters")
    f_stock: BoolProperty(name="Stock animals", description="The wild animals you can gather")
    f_henchman: BoolProperty(name="Henchmen", description="Henchmen")
    f_resource: BoolProperty(name="Resources", description="Coal and geysers")
    f_lab: BoolProperty(name="Labs", description="Labs")
    f_sound: BoolProperty(name="Sound emitters", description="Sound emitters")
    f_marker: BoolProperty(name="Markers", description="Points and AI hints")
    specific: StringProperty(name="Specific", description="Creature or blueprint names (or parts), comma separated, e.g. "
                                                          "wolf, zebra")
    rows: CollectionProperty(type=BZ_PG_map_row)
    row_index: IntProperty()
    rows_total: IntProperty(description="How many objects matched the filters (the list shows the first few)")
    bp_rows: CollectionProperty(type=BZ_PG_map_bp)
    bp_index: IntProperty()
    crop_ratio: EnumProperty(name="Crop size", default="2", items=(("2", "1/2", "Half the map's side (a quarter of its area)"),
                                                                   ("4", "1/4", "A quarter of the map's side"), ("8", "1/8", "An eighth of the map's side")),
                             description="How much of the map's side stays")
    crop_use_cursor: BoolProperty(name="Centre on the 3D cursor", default=True, description="Centre the square that stays on the 3D cursor "
                                                                                           "(off: the point typed below)")
    crop_centre: FloatVectorProperty(name="Centre (x, z in m)", size=2, default=(0.0, 0.0), description="The game's x and z of the square's middle, in metres")
    crop_force: BoolProperty(name="Crop anyway", default=False, description="Go on even when the validation found things the crop would break")


def tools(context) -> "BZ_PG_map_tools":
    return context.scene.bz_map_tools


def _need_map(op, context):
    top = X.active_map(context)
    if top is None:
        op.report({"ERROR"}, "There is no imported map in this scene. Open one with File > Import > Impossible Creatures Map.")
    return top


def _say(op, lines: list, level: str = "INFO", name: str = "Map tools") -> None:
    """Report the first line, print all, keep all in the text 'Map tools'."""
    op.report({level}, lines[0] if len(lines) == 1 else f"{lines[0]} (the whole report is in the text '{name}')")
    for line in lines:
        print(line, flush=True)
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.clear()
    txt.write("\n".join(lines) + "\n")


def _fail(op, exc) -> set:
    op.report({"ERROR"}, str(exc))
    return {"CANCELLED"}


def _target(path: str) -> str:
    """The checked absolute path to write (never the game's own folder)."""
    p = os.path.abspath(bpy.path.abspath(path))
    PB.check_target(os.path.dirname(p))
    return p


_ERRORS = (T.ToolError, X.MapSaveError, PB.Refused, ValueError, OSError)


def _errors():
    """The exceptions that mean "say it in words" (the ICParse modules' own errors are ValueErrors)."""
    return _ERRORS


# ---------------------------------------------------------------------------------------------------- height map
class BZ_OT_map_import_heightmap(bpy.types.Operator, ImportHelper):
    """Set ground heights from a grey TGA height map picture"""
    bl_idname = "bz.map_import_heightmap"
    bl_label = "Import Height Map"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".tga"
    filter_glob: StringProperty(default="*.tga", options={"HIDDEN"})
    resample: EnumProperty(name="Other sizes", default="smooth", items=(
        ("smooth", "Smooth, corner to corner", "A picture of another size is stretched over the ground, the pixels between blended"),
        ("nearest", "Nearest pixel", "Each point takes the nearest pixel (pixels repeat)")),
        description="What to do if the picture's size differs from the ground grid")
    flip: BoolProperty(name="Flip rows", default=False, description="Flip the picture upside down")

    @classmethod
    def poll(cls, context):
        top = X.active_map(context)
        return top is not None and X.terrain_of(top) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            note = X._ensure_object_mode(context)
            HM = T.heightmap_io()
            with open(bpy.path.abspath(self.filepath), "rb") as f:
                data = f.read()
            old, mv, _plan = T.scene_heights(context, top)
            heights, info = HM.heights_from_image(data, mv.width, mv.height, mode=self.resample, flip=self.flip)
            res = T.set_scene_heights(context, top, heights)
        except _errors() as exc:
            return _fail(self, exc)
        step = res["step"]
        lines = [f"Height map {os.path.basename(self.filepath)}: {res['changed']} of {res['points']} ground points changed (the largest by {res['largest_m']:.1f} m).",
                 f"The picture is {info['width']} x {info['height']} pixels, the ground {mv.width} x {mv.height} points"
                 + (f"; stretched {'corner to corner' if info['mode'] == 'smooth' else 'by the nearest pixel'}." if info["resampled"] else ": one pixel for each point."),
                 f"The ground now runs from {info['low'] * step:g} m to {info['high'] * step:g} m (the map keeps heights in steps of {step:g} m).",
                 "The map file changes when you save: the save reads each point's height and rebuilds the pathfinding."]
        if note:
            lines.append(note)
        if info.get("differs_between_channels"):
            lines.append("The picture is not grey: the red value of each pixel was used.")
        _say(self, lines)
        return {"FINISHED"}


class BZ_OT_map_export_heightmap(bpy.types.Operator, ExportHelper):
    """Save the ground as a grey TGA height map picture"""
    bl_idname = "bz.map_export_heightmap"
    bl_label = "Export Height Map"
    filename_ext = ".tga"
    filter_glob: StringProperty(default="*.tga", options={"HIDDEN"})
    size: EnumProperty(name="Size", default="points", items=(
        ("points", "One pixel per ground point", "65 x 65, 129 x 129, 257 x 257 ...: imports back exactly"),
        ("cells", "One pixel per cell", "64 x 64, 128 x 128, 256 x 256 ... (one less across; a little blurred on the way back)")),
        description="The picture's size")
    flip: BoolProperty(name="Flip rows", default=False, description="Turn the picture upside down")

    @classmethod
    def poll(cls, context):
        top = X.active_map(context)
        return top is not None and X.terrain_of(top) is not None

    def invoke(self, context, event):
        top = X.active_map(context)
        if top is not None:
            self.filepath = X.map_name(top) + "_height.tga"
        return super().invoke(context, event)

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            X._ensure_object_mode(context)
            HM = T.heightmap_io()
            heights, mv, _plan = T.scene_heights(context, top)
            data = HM.image_from_heights(heights, size=self.size, flip=self.flip)
            path = _target(self.filepath)
            PB.write_file(path, data)
        except _errors() as exc:
            return _fail(self, exc)
        side = mv.width - (1 if self.size == "cells" else 0)
        _say(self, [f"Wrote the height map {path}: {side} x {side} pixels, the grey value is each ground point's height (0 to 255 steps of {mv.height_scale:g} m)."])
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- terrain files
class BZ_OT_map_export_terrain(bpy.types.Operator, ExportHelper):
    """Save the map's terrain as a terrain file (.ter)"""
    bl_idname = "bz.map_export_terrain"
    bl_label = "Export Terrain"
    filename_ext = ".ter"
    filter_glob: StringProperty(default="*.ter", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def invoke(self, context, event):
        top = X.active_map(context)
        if top is not None:
            self.filepath = X.map_name(top) + ".ter"
        return super().invoke(context, event)

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            data, _plan, _rep = T.current_bytes(context, top)
            ter = T.terrain_io().export_terrain(data)
            path = _target(self.filepath)
            PB.write_file(path, ter)
        except _errors() as exc:
            return _fail(self, exc)
        _say(self, [f"Wrote the terrain file {path} ({len(ter)} bytes): the terrain of {X.map_name(top)} as it is in the scene now, "
                    "for the Mission Editor's File > Import Terrain or this add-on's."])
        return {"FINISHED"}


class BZ_OT_map_import_terrain(bpy.types.Operator, ImportHelper):
    """Replace the map's terrain with a terrain file (.ter) of the same size"""
    bl_idname = "bz.map_import_terrain"
    bl_label = "Import Terrain"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".ter"
    filter_glob: StringProperty(default="*.ter", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            data, sib, _plan, _rep = T.current_map(context, top)
            with open(bpy.path.abspath(self.filepath), "rb") as f:
                ter = f.read()
            out, rep = T.terrain_io().import_terrain(data, ter)
            base = X.map_name(top)
            new = T.reopen(context, top, out, base, sib, f"The terrain of {base} replaced with {os.path.basename(self.filepath)}")
        except _errors() as exc:
            return _fail(self, exc)
        lines = [f"Imported the terrain {os.path.basename(self.filepath)} into {base}: {rep['heights_changed']} ground points changed, "
                 f"water level {rep['water_level'][0]} -> {rep['water_level'][1]}, {rep['decals'][0]} -> {rep['decals'][1]} decals, "
                 f"ground textures: {', '.join(rep['ground_textures']) or 'none'}.",
                 f"The pathfinding was rebuilt like the game does ({rep['paths']}).",
                 "The map is built again as a new map in the scene; the old one is still there, hidden."]
        if rep["floating"] or rep["buried"]:
            lines.append(f"{rep['floating']} placed objects now float above the ground and {rep['buried']} sit under it: use Drop selected to the ground.")
        lines += [f"Warning: {w}" for w in rep["warnings"]]
        _say(self, lines, "WARNING" if (rep["floating"] or rep["buried"] or rep["warnings"]) else "INFO")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- entity files
class BZ_OT_map_export_entities(bpy.types.Operator, ExportHelper):
    """Save the selected placed objects as an entities file (.ent)"""
    bl_idname = "bz.map_export_entities"
    bl_label = "Export Selected Entities"
    filename_ext = ".ent"
    filter_glob: StringProperty(default="*.ent", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def invoke(self, context, event):
        top = X.active_map(context)
        if top is not None:
            self.filepath = X.map_name(top) + ".ent"
        return super().invoke(context, event)

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        objs = T.selected_entity_objects(context, top)
        if not objs:
            self.report({"ERROR"}, "No placed object of the map is selected. Select the objects to copy in the 3D view (or with the Object List), then export.")
            return {"CANCELLED"}
        try:
            bps, recs, left = T.entity_file_records(context, top, objs)
            data = T.entities_io().build_file(bps, recs)
            path = _target(self.filepath)
            PB.write_file(path, data)
        except _errors() as exc:
            return _fail(self, exc)
        lines = [f"Wrote the entities file {path}: {len(recs)} objects with {len(bps)} blueprints, at the places they have in the scene now."] + [f"Note: {t}" for t in left]
        _say(self, lines, "WARNING" if left else "INFO")
        return {"FINISHED"}


class BZ_OT_map_import_entities(bpy.types.Operator, ImportHelper):
    """Add the objects from an entities file (.ent) to the map"""
    bl_idname = "bz.map_import_entities"
    bl_label = "Import Entities"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".ent"
    filter_glob: StringProperty(default="*.ent", options={"HIDDEN"})
    place: EnumProperty(name="Place", default="WHERE", items=(("WHERE", "Where they were", "Each object lands at the position it had in its map"),
                                                              ("CURSOR", "At the 3D cursor", "The middle of the group goes to the 3D cursor")))
    drop: BoolProperty(name="Drop to the ground", default=False, description="Set each object's height to the ground's at its place")

    @classmethod
    def poll(cls, context):
        top = X.active_map(context)
        return top is not None and X.terrain_of(top) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            with open(bpy.path.abspath(self.filepath), "rb") as f:
                data = f.read()
            res = T.add_entities_from_file(context, top, data, mode=self.place, drop_to_ground=self.drop)
        except _errors() as exc:
            return _fail(self, exc)
        for o in context.view_layer.objects:
            o.select_set(False)
        for o in res["added"]:
            o.select_set(True)
        if res["added"]:
            context.view_layer.objects.active = res["added"][-1]
        lines = [f"Imported {len(res['added'])} objects from {os.path.basename(self.filepath)} (selected in the scene). They are written into the map, with new numbers, when you save."]
        if self.place == "CURSOR":
            lines.append(f"Moved by {res['moved'][0]:+.1f} m, {res['moved'][1]:+.1f} m to put their middle at the 3D cursor.")
        if res["owners_lost"]:
            lines.append(f"{res['owners_lost']} objects belonged to a player this map does not have: they belong to nobody now.")
        lines += [f"Note: {t}" for t in res["skipped"]]
        _say(self, lines, "WARNING" if (res["skipped"] or res["owners_lost"]) else "INFO")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- the Object List
def fill_object_list(context, top) -> int:
    """Refresh the scene's Object List from the filters; returns how many objects matched."""
    O = T.objlist()
    st = tools(context)
    rows = T.entity_rows(context, top)
    owner = st.owner
    owner = int(owner) if owner.isdigit() else owner
    tags = [t for t, p in TAG_PROPS if getattr(st, p)]
    shown = O.filter_rows(rows, tags=tags, specific=st.specific, owner=owner)
    st.rows.clear()
    for r in shown[:LIST_CAP]:
        it = st.rows.add()
        it.entity_id = r.entity_id
        it.label = r.name
        it.blueprint = r.blueprint
        it.owner_text = r.owner_text
        it.obj_name = r.ref.name if r.ref is not None else ""
    st.rows_total = len(shown)
    st.row_index = 0
    return len(shown)


class BZ_OT_map_objlist_refresh(bpy.types.Operator):
    """List the placed objects that pass the filters"""
    bl_idname = "bz.map_objlist_refresh"
    bl_label = "Refresh the List"

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        n = fill_object_list(context, top)
        self.report({"INFO"}, f"{n} placed objects match" + (f" (the list shows the first {LIST_CAP}: narrow the filters)" if n > LIST_CAP else "."))
        return {"FINISHED"}


class BZ_OT_map_objlist_do(bpy.types.Operator):
    """Hide, select, delete or show again the listed objects"""
    bl_idname = "bz.map_objlist_do"
    bl_label = "Object List Action"
    bl_options = {"REGISTER", "UNDO"}
    action: EnumProperty(items=(("DELETE", "Delete Selected", "Remove the objects from the map (they leave the map when you save)"),
                                ("HIDE", "Hide Selected", "Hide the objects in the 3D view (they stay in the map)"),
                                ("SELECT", "Select Selected", "Select the objects in the 3D view"),
                                ("UNHIDE", "Unhide All", "Show every hidden placed object again")))

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        st = tools(context)
        if self.action != "UNHIDE" and not len(st.rows):
            fill_object_list(context, top)
        picked = [r for r in st.rows if r.pick]
        if self.action == "DELETE" and not picked:
            self.report({"ERROR"}, "Tick the rows of the objects to delete first: Delete works on the ticked rows only, never on the whole list.")
            return {"CANCELLED"}
        picked = picked or list(st.rows)
        rows = []
        for r in picked:
            o = bpy.data.objects.get(r.obj_name)
            if o is not None:
                rows.append(SimpleNamespace(ref=o))
        try:
            n = T.apply_action(context, rows, self.action, top)
        except _errors() as exc:
            return _fail(self, exc)
        what = {"DELETE": "deleted (they leave the map when you save)", "HIDE": "hidden", "SELECT": "selected", "UNHIDE": "shown again"}[self.action]
        self.report({"INFO"}, f"{n} placed objects {what}.")
        if self.action == "DELETE":
            fill_object_list(context, top)
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- the Blueprint List
class BZ_OT_map_bplist_refresh(bpy.types.Operator):
    """List the map's blueprints and how many objects and triggers use each"""
    bl_idname = "bz.map_bplist_refresh"
    bl_label = "Refresh the Blueprint List"

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            rows = T.blueprint_rows(context, top)
        except _errors() as exc:
            return _fail(self, exc)
        st = tools(context)
        st.bp_rows.clear()
        for r in rows:
            it = st.bp_rows.add()
            it.net_id, it.label, it.path, it.ref_count, it.trigger_refs = r.net_id, r.name, r.path, r.ref_count, r.trigger_refs
        st.bp_index = 0
        self.report({"INFO"}, f"{len(rows)} blueprints, {sum(r.ref_count for r in rows)} placed objects use them.")
        return {"FINISHED"}


class BZ_OT_map_bplist_select(bpy.types.Operator):
    """Select every placed object that uses the chosen blueprint"""
    bl_idname = "bz.map_bplist_select"
    bl_label = "Select Its Objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        st = getattr(context.scene, "bz_map_tools", None)
        return X.active_map(context) is not None and st is not None and 0 <= st.bp_index < len(st.bp_rows)

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        st = tools(context)
        row = st.bp_rows[st.bp_index]
        objs = T.objects_of_blueprint(top, row.path)
        for o in context.view_layer.objects:
            o.select_set(False)
        for o in objs:
            try:
                o.hide_set(False)
                o.select_set(True)
            except RuntimeError:
                pass
        if objs:
            context.view_layer.objects.active = objs[-1]
        self.report({"INFO"}, f"{len(objs)} objects use {row.label} (number {row.net_id}).")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- Terrain Crop
def _crop_args(context):
    st = tools(context)
    centre = CR.cursor_centre(context) if st.crop_use_cursor else (float(st.crop_centre[0]), float(st.crop_centre[1]))
    return int(st.crop_ratio), centre


class BZ_OT_map_crop_validate(bpy.types.Operator):
    """Show what a crop would delete or break, without changing the map"""
    bl_idname = "bz.map_crop_validate"
    bl_label = "Validate the Crop"

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        ratio, centre = _crop_args(context)
        try:
            res = CR.validate(context, top, ratio, centre)
        except _errors() as exc:
            CR.RESULTS.pop(top.name, None)
            return _fail(self, exc)
        _say(self, res["lines"], "WARNING" if res["errors"] else "INFO", "Map crop")
        return {"FINISHED"}


class BZ_OT_map_crop_apply(bpy.types.Operator):
    """Build a cropped copy of the map; the old one is kept, hidden"""
    bl_idname = "bz.map_crop_apply"
    bl_label = "Crop the Map"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        ratio, centre = _crop_args(context)
        try:
            res = CR.crop(context, top, ratio, centre, force=bool(tools(context).crop_force))
        except _errors() as exc:
            return _fail(self, exc)
        CR.RESULTS.pop(top.name, None)
        _say(self, res["lines"], "INFO", "Map crop")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- lists
class BZ_UL_map_objects(bpy.types.UIList):
    bl_idname = "BZ_UL_map_objects"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "pick", text="")
        row.label(text=str(item.entity_id) if item.entity_id >= 0 else "new")
        row.label(text=item.label)
        row.label(text=item.owner_text)


class BZ_UL_map_blueprints(bpy.types.UIList):
    bl_idname = "BZ_UL_map_blueprints"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=str(item.net_id))
        row.label(text=item.label)
        row.label(text=f"x{item.ref_count}")
        row.label(text=f"{item.trigger_refs} trig")


# ---------------------------------------------------------------------------------------------------- menus
def menu_import(self, context):
    col = self.layout
    col.operator(BZ_OT_map_import_heightmap.bl_idname, text="Impossible Creatures Height Map (.tga)")
    col.operator(BZ_OT_map_import_terrain.bl_idname, text="Impossible Creatures Terrain (.ter)")
    col.operator(BZ_OT_map_import_entities.bl_idname, text="Impossible Creatures Entities (.ent)")


def menu_export(self, context):
    col = self.layout
    col.operator(BZ_OT_map_export_heightmap.bl_idname, text="Impossible Creatures Height Map (.tga)")
    col.operator(BZ_OT_map_export_terrain.bl_idname, text="Impossible Creatures Terrain (.ter)")
    col.operator(BZ_OT_map_export_entities.bl_idname, text="Impossible Creatures Selected Entities (.ent)")


CLASSES = (BZ_PG_map_row, BZ_PG_map_bp, BZ_PG_map_tools, BZ_OT_map_import_heightmap, BZ_OT_map_export_heightmap, BZ_OT_map_export_terrain,
           BZ_OT_map_import_terrain, BZ_OT_map_export_entities, BZ_OT_map_import_entities, BZ_OT_map_objlist_refresh, BZ_OT_map_objlist_do,
           BZ_OT_map_bplist_refresh, BZ_OT_map_bplist_select, BZ_OT_map_crop_validate, BZ_OT_map_crop_apply, BZ_UL_map_objects, BZ_UL_map_blueprints)


def register():
    bpy.types.Scene.bz_map_tools = PointerProperty(type=BZ_PG_map_tools)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    if hasattr(bpy.types.Scene, "bz_map_tools"):
        del bpy.types.Scene.bz_map_tools
