"""sceneobj.panels_map_tools -- the Map panel's sections for the Mission Editor's conveniences: height map / terrain / entity files, the Object
List, the Blueprint List and Terrain Crop (the buttons are in ``ops_map_tools``)."""
from __future__ import annotations

import bpy

from . import map_crop as CR
from . import map_hooks as H
from . import ops_map_export as X
from . import ops_map_tools as OP
from . import ui_kit

ORDER = 66


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_files(layout, context, top) -> None:
    _hint(layout, "The Mission Editor's File menu entries. A height map is a grey TGA picture of the ground; a terrain file (.ter) holds the "
                  "ground, water, textures, sky, light and decals; an entities file (.ent) copies placed objects between maps.", width=44)
    col = layout.column(align=True)
    col.label(text="Height map")
    row = col.row(align=True)
    row.operator("bz.map_import_heightmap", text="Import...", icon="IMPORT")
    row.operator("bz.map_export_heightmap", text="Export...", icon="EXPORT")
    col.separator()
    col.label(text="Terrain (.ter)")
    row = col.row(align=True)
    row.operator("bz.map_import_terrain", text="Import...", icon="IMPORT")
    row.operator("bz.map_export_terrain", text="Export...", icon="EXPORT")
    col.separator()
    col.label(text="Entities (.ent)")
    row = col.row(align=True)
    row.operator("bz.map_import_entities", text="Import...", icon="IMPORT")
    row.operator("bz.map_export_entities", text="Export selected...", icon="EXPORT")
    _hint(layout, "Importing a terrain builds the map again as a new map (the old one stays, hidden). Pictures and files are written into a "
                  "folder you pick, never the game's own.", width=44)


def draw_objects(layout, context, top) -> None:
    st = context.scene.bz_map_tools
    _hint(layout, "Filters: with none of the toggles on every object is listed; with several on, the objects that match any of them. "
                  "Then Refresh.", width=44)
    layout.prop(st, "owner", text="")
    grid = layout.grid_flow(row_major=True, columns=2, align=True)
    for _tag, prop in OP.TAG_PROPS:
        grid.prop(st, prop, toggle=True)
    layout.prop(st, "specific")
    layout.operator("bz.map_objlist_refresh", text="Refresh the list", icon="FILE_REFRESH")
    if len(st.rows):
        total = st.rows_total
        layout.label(text=f"{total} objects" + (f" (first {len(st.rows)} shown)" if total > len(st.rows) else ""))
        layout.template_list("BZ_UL_map_objects", "", st, "rows", st, "row_index", rows=6)
        _hint(layout, "Select and Hide work on the rows ticked (on every row when none is ticked). Delete works on the ticked rows only.", width=44)
    else:
        layout.label(text="Nothing listed yet.")
    col = layout.column(align=True)
    row = col.row(align=True)
    row.operator("bz.map_objlist_do", text="Select").action = "SELECT"
    row.operator("bz.map_objlist_do", text="Hide").action = "HIDE"
    row = col.row(align=True)
    row.operator("bz.map_objlist_do", text="Delete", icon="TRASH").action = "DELETE"
    row.operator("bz.map_objlist_do", text="Unhide all").action = "UNHIDE"


def draw_blueprints(layout, context, top) -> None:
    st = context.scene.bz_map_tools
    _hint(layout, "The blueprints the map lists: the number the map gives each, how many placed objects use it, and how many trigger "
                  "arguments name it.", width=44)
    layout.operator("bz.map_bplist_refresh", text="Refresh the list", icon="FILE_REFRESH")
    if len(st.bp_rows):
        layout.template_list("BZ_UL_map_blueprints", "", st, "bp_rows", st, "bp_index", rows=6)
        layout.operator("bz.map_bplist_select", text="Select its objects")


def draw_crop(layout, context, top) -> None:
    st = context.scene.bz_map_tools
    _hint(layout, "Cut the map down to a square part of itself. Validate first: it shows the square and lists the objects, triggers, "
                  "dialogue and NISlet animations that would not survive. Crop builds a new, smaller map and keeps the old one, hidden.", width=44)
    layout.prop(st, "crop_ratio", expand=True)
    layout.prop(st, "crop_use_cursor")
    if not st.crop_use_cursor:
        layout.prop(st, "crop_centre", text="")
    layout.operator("bz.map_crop_validate", text="Validate", icon="VIEWZOOM")
    res = CR.RESULTS.get(top.name)
    if res:
        box = layout.box()
        for line in res["lines"]:
            _hint(box, line, width=44)
    layout.prop(st, "crop_force")
    layout.operator("bz.map_crop_apply", text="Crop!", icon="SELECT_SET")


class _MapSection:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"
    bl_parent_id = H.MAP_PANEL_ID
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None and getattr(context.scene, "bz_map_tools", None) is not None


class BZ_PT_map_tools_files(_MapSection, bpy.types.Panel):
    bl_label = "Height map, terrain and entity files"
    bl_idname = "BZ_PT_map_tools_files"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_files(self.layout, context, top)


class BZ_PT_map_tools_objects(_MapSection, bpy.types.Panel):
    bl_label = "Object list"
    bl_idname = "BZ_PT_map_tools_objects"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_objects(self.layout, context, top)


class BZ_PT_map_tools_blueprints(_MapSection, bpy.types.Panel):
    bl_label = "Blueprint list"
    bl_idname = "BZ_PT_map_tools_blueprints"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_blueprints(self.layout, context, top)


class BZ_PT_map_tools_crop(_MapSection, bpy.types.Panel):
    bl_label = "Terrain crop"
    bl_idname = "BZ_PT_map_tools_crop"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_crop(self.layout, context, top)


CLASSES = (BZ_PT_map_tools_files, BZ_PT_map_tools_objects, BZ_PT_map_tools_blueprints, BZ_PT_map_tools_crop)
