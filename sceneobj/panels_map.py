"""sceneobj.panels_map -- the Map panel (3D View sidebar, IC Map tab): edit an imported Impossible Creatures map and save it.

It shows when the scene holds an imported map (File > Import > Impossible Creatures Map). It says which map it is, its size and what
it holds, and gives the actions of ``ops_map_export``: sculpt the ground, move the water, give faces a ground texture, move / copy /
delete / add placed objects, set their owner, drop them to the ground, check what changed, and save the map.
"""
from __future__ import annotations

import json

import bpy

from . import ops_map_export as X
from . import ops_map_import as M
from . import ui_kit

ORDER = 62


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def _report(top) -> dict:
    try:
        return json.loads(top[M.KEY_REPORT])
    except (KeyError, ValueError, TypeError):
        return {}


def object_counts(top) -> dict:
    """How many placed objects each kind collection holds now (a copy or a deleted object shows at once)."""
    out = {}
    for c in top.children:
        if c.name.endswith("(hidden)"):
            continue
        kind = c.name.rsplit(" ", 1)[-1]
        n = len(c.objects) if kind in X.SIDE_WORDS else 0
        if n:
            out[kind] = n
    return out


def draw_summary(layout, context, top) -> None:
    rep = _report(top)
    layout.label(text=X.map_name(top), icon="WORLD")
    if rep:
        w, h = rep.get("size", (0, 0))
        sx, sz = rep.get("world_size_m", (0, 0))
        layout.label(text=f"{w} x {h} height points, {sx:g} x {sz:g} m")
    counts = object_counts(top)
    if counts:
        layout.label(text="Placed: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    where = top.get(M.KEY_SOURCE, "").split("|")[-1]
    if where:
        _hint(layout, f"Opened from {where}", width=44)
    problem = X.library_problem()
    if problem:
        ui_kit.alert(layout, problem, width=44)


def draw_terrain(layout, context, top) -> None:
    terrain = X.terrain_of(top)
    if terrain is None:
        ui_kit.alert(layout, "The terrain object of this map is not in the scene.", width=44)
        return
    _hint(layout, "Sculpt or move the ground's points, but do not add or remove any.", width=44)
    step = float(terrain.get("ic_height_scale", 0.5))
    _hint(layout, f"The map keeps heights in steps of {step:g} m; the save report says how many were rounded.", width=44)
    water = X.water_of(top)
    level = float(terrain.get("ic_water_level", 0)) * step
    if water is not None:
        _hint(layout, f"Water: the surface is at {level:g} m. Move the water object to change it, then press Redraw water.", width=44)
        layout.operator("bz.map_redraw_water", text="Redraw water", icon="FILE_REFRESH")
    else:
        _hint(layout, f"Water level {level:g} m, with no water surface. Add water makes one.", width=44)
        layout.operator("bz.map_add_water", text="Add water...", icon="ADD")


def draw_look(layout, context, top) -> None:
    from . import map_look
    if map_look.look_collection(top) is not None:
        layout.operator("bz.map_remove_look", text="Remove game look")
    else:
        layout.operator("bz.map_game_look", text="Show game look", icon="LIGHT_SUN")
    _hint(layout, "The game look is for viewing only and is not saved.", width=44)


def draw_ground(layout, context, top) -> None:
    terrain = X.terrain_of(top)
    if terrain is None:
        return
    mats = X.ground_materials(terrain)
    for slot, mat in mats:
        row = layout.row(align=True)
        row.label(text=mat.name, icon="MATERIAL")
        op = row.operator("bz.map_use_ground", text="Use on selected faces")
        op.slot = slot
    if not mats:
        layout.label(text="This map has no ground materials.")
    layout.operator("bz.map_add_ground", text="Add ground texture...", icon="ADD")
    _hint(layout, "Select faces in Edit Mode, then 'Use on selected faces' (per 8 x 8 cell block).", width=44)


def draw_objects(layout, context, top) -> None:
    _hint(layout, "Move, turn, delete or copy (Shift+D) objects to edit the placed objects. Copies of creatures combined in the map are not saved.", width=44)
    col = layout.column(align=True)
    col.operator("bz.map_add_object", text="Add object at the 3D cursor...", icon="ADD")
    col.operator("bz.map_set_owner", text="Set owner of the selected...", icon="USER")
    col.operator("bz.map_drop_to_ground", text="Drop selected to the ground", icon="TRIA_DOWN_BAR")


def draw_save(layout, context, top) -> None:
    layout.operator("bz.map_check_changes", text="Check my changes", icon="VIEWZOOM")
    lines = X.LAST_CHECK.get(top.name)
    if lines:
        box = layout.box()
        for line in lines:
            _hint(box, line, width=44)
    col = layout.column(align=True)
    op = col.operator("bz.export_map", text="Save Map...", icon="EXPORT")
    op.ready_to_drop = False
    op.map_name = top.name
    op = col.operator("bz.export_map", text="Save ready-to-drop folder...", icon="PACKAGE")
    op.ready_to_drop = True
    op.map_name = top.name
    _hint(layout, "Saves into a folder you pick, never into the game's own folder.", width=44)


class _MapPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MAP

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None


class BZ_PT_map(_MapPanel, bpy.types.Panel):
    bl_label = "Map"
    bl_idname = "BZ_PT_map"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_summary(self.layout, context, top)


class BZ_PT_map_terrain(_MapPanel, bpy.types.Panel):
    bl_label = "Ground and water"
    bl_idname = "BZ_PT_map_terrain"
    bl_parent_id = "BZ_PT_map"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_terrain(self.layout, context, top)


class BZ_PT_map_look(_MapPanel, bpy.types.Panel):
    bl_label = "Game look"
    bl_idname = "BZ_PT_map_look"
    bl_parent_id = "BZ_PT_map"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_look(self.layout, context, top)


class BZ_PT_map_ground(_MapPanel, bpy.types.Panel):
    bl_label = "Ground textures"
    bl_idname = "BZ_PT_map_ground"
    bl_parent_id = "BZ_PT_map"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_ground(self.layout, context, top)


class BZ_PT_map_objects(_MapPanel, bpy.types.Panel):
    bl_label = "Placed objects"
    bl_idname = "BZ_PT_map_objects"
    bl_parent_id = "BZ_PT_map"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_objects(self.layout, context, top)


class BZ_PT_map_save(_MapPanel, bpy.types.Panel):
    bl_label = "Save"
    bl_idname = "BZ_PT_map_save"
    bl_parent_id = "BZ_PT_map"

    def draw(self, context):
        top = X.active_map(context)
        if top is not None:
            draw_save(self.layout, context, top)


CLASSES = (BZ_PT_map, BZ_PT_map_terrain, BZ_PT_map_look, BZ_PT_map_ground, BZ_PT_map_objects, BZ_PT_map_save)
