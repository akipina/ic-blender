"""sceneobj.panels_map_settings -- the Map settings, Terrain look and Map problems panels (3D View sidebar, IC tab, under Map)."""
from __future__ import annotations

import bpy

from . import map_hooks as H
from . import map_settings as S
from . import ops_map_export as X
from . import ui_kit

ORDER = 66


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IC"
    bl_parent_id = H.MAP_PANEL_ID

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None


def draw_settings(layout, context, top) -> None:
    g = getattr(top, "ic_settings", None)
    if g is None or not g.ready:
        _hint(layout, "This map was opened before the settings existed: open it again to edit them.", "INFO")
        return
    col = layout.column(align=True)
    col.prop(g, "title")
    col.prop(g, "kind")
    col.prop(g, "players")
    if int(g.kind) == 2:
        _hint(layout, "Saved in Data/scenarios/mp and listed by the game for 2 to 6 players.", width=44)
    else:
        _hint(layout, "Single-player maps go in Data/scenarios/sp: move the saved files there.",
              "INFO", width=44)
    box = layout.box()
    box.label(text="Players and where they start", icon="USER")
    starts = S.starts_of(top)
    for slot in range(int(g.players)):
        row = box.row(align=True)
        labs = starts.get(slot)
        if labs:
            t = labs[0].location
            row.label(text=f"Player {slot + 1}: lab at {t.x:.0f}, {-t.y:.0f} m")
            op = row.operator("bz.map_select_start", text="", icon="RESTRICT_SELECT_OFF")
            op.slot = slot
        else:
            row.label(text=f"Player {slot + 1}: no start", icon="ERROR")
        op = row.operator("bz.map_add_start", text="", icon="ADD")
        op.slot = slot
    _hint(layout, "The plus adds a player's start (lab and henchmen) at the 3D cursor.", width=44)
    col = layout.column(align=True)
    col.operator("bz.map_make_preview", text="Make preview picture", icon="IMAGE_DATA").method = "GAME"
    col.operator("bz.map_make_preview", text="Make preview picture from a Blender render", icon="RENDER_STILL").method = "BLENDER"
    has = bool(top.get(S.KEY_ICON_TEXT))
    _hint(layout, "The map list icon and minimap. " + ("Made: saved with the map." if has else
                                                        "A new map has its template's until you make this."), width=44)


def draw_look(layout, context, top) -> None:
    g = getattr(top, "ic_look", None)
    if g is None or not g.ready:
        _hint(layout, "This map was opened before these settings existed: open it again to edit them.", "INFO")
        return
    box = layout.box()
    box.label(text="Light", icon="LIGHT_SUN")
    box.prop(g, "sun_colour")
    box.prop(g, "shadow_colour")
    box.prop(g, "time_hour")
    box = layout.box()
    box.label(text="Sky and sea", icon="WORLD")
    box.prop(g, "sky")
    col = box.column(align=True)
    col.prop(g, "ocean_colour")
    if not g.has_ocean_record and not g.ocean_touched:
        _hint(col, "This map has no ocean colour yet: changing it adds one.", width=44)
    if g.has_water_alphas:
        col = box.column(align=True)
        col.prop(g, "ocean_min")
        col.prop(g, "ocean_max")
        col.prop(g, "shore")
    if g.has_fog:
        box = layout.box()
        box.prop(g, "fog_on")
        sub = box.column(align=True)
        sub.active = g.fog_on
        sub.prop(g, "fog_start")
        sub.prop(g, "fog_end")
        sub.prop(g, "fog_colour")
    else:
        _hint(layout, "This map is saved in an older terrain format that has no fog.", "INFO", width=44)
    layout.prop(g, "detail_tiling")
    layout.operator("bz.map_look_reset", text="Back to the map's values", icon="LOOP_BACK")
    _hint(layout, "The game look shows changes at once; saving writes only what you changed.", width=44)


def draw_problems(layout, context, top) -> None:
    layout.operator("bz.map_check_problems", text="Check map", icon="VIEWZOOM")
    found = S.PROBLEMS.get(top.name)
    if found is None:
        _hint(layout, "Check map lists problems to fix before saving, such as players without a start.", width=44)
        return
    if not found:
        layout.label(text="No problems found.", icon="CHECKMARK")
        return
    for p in found:
        box = layout.box()
        _hint(box, p["text"], "CANCEL" if p["severity"] == H.ERROR else "ERROR", width=42)
        where = p.get("where")
        if where and bpy.data.objects.get(where) is not None:
            op = box.operator("bz.map_locate_problem", text="Locate", icon="VIEWZOOM")
            op.where = where


class BZ_PT_map_settings(_Sub, bpy.types.Panel):
    bl_label = "Map settings"
    bl_idname = "BZ_PT_map_settings"
    bl_order = -3

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_settings(self.layout, context, top)


class BZ_PT_map_terrain_look(_Sub, bpy.types.Panel):
    bl_label = "Terrain look"
    bl_idname = "BZ_PT_map_terrain_look"
    bl_order = -2
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_look(self.layout, context, top)


class BZ_PT_map_problems(_Sub, bpy.types.Panel):
    bl_label = "Map problems"
    bl_idname = "BZ_PT_map_problems"
    bl_order = 1

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_problems(self.layout, context, top)


CLASSES = (BZ_PT_map_settings, BZ_PT_map_terrain_look, BZ_PT_map_problems)
