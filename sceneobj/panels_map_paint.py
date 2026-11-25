"""sceneobj.panels_map_paint -- the painting panels of the Map panel (3D View sidebar, IC tab): passability, overlays, terrain types,
water levels and decals. Plain words; every sub-panel says what the feature saves into."""
from __future__ import annotations

import bpy

from . import map_hooks as H
from . import map_overlays as OV
from . import map_paint as P
from . import ops_map_paint as O
from . import ui_kit

ORDER = 64


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def _top(context):
    return P._x().active_map(context)


def draw_passability(layout, context, top) -> None:
    _hint(layout, "Paint cells blocked (red) or flat open ground (green); saved with the map.")
    col = layout.column(align=True)
    op = col.operator("bz.map_impass_paint", text="Paint blocked", icon="BRUSH_DATA")
    op.state = "BLOCK"
    op = col.operator("bz.map_impass_paint", text="Paint open", icon="BRUSH_DATA")
    op.state = "OPEN"
    op = col.operator("bz.map_impass_paint", text="Erase paint", icon="BRUSH_DATA")
    op.state = "ERASE"
    col.operator("bz.map_paint_stop", text="Stop painting", icon="LOOP_BACK")
    layout.label(text="Or select faces in Edit Mode:")
    row = layout.row(align=True)
    for state, text in (("BLOCK", "Block"), ("OPEN", "Open"), ("CLEAR", "Clear")):
        op = row.operator("bz.map_impass_faces", text=text)
        op.state = state
    obj = P.paint_object(top, "impass")
    if obj is not None:
        row = layout.row(align=True)
        op = row.operator("bz.map_paint_visible", text="Show" if obj.hide_viewport else "Hide", icon="HIDE_OFF")
        op.kind, op.show = "impass", obj.hide_viewport
        row.operator("bz.map_impass_reset", text="Reset", icon="LOOP_BACK")
        img = P.paint_image(top)
        if img is not None:
            _hint(layout, f"The picture '{img.name}' has one pixel per path cell ({img.size[0]} x {img.size[1]}).")


def draw_overlays(layout, context, top) -> None:
    from ICParse.sgb import overlays
    _hint(layout, "Shows where units can go; pick it again to update (nothing is saved).")
    now = OV.shown(top)
    col = layout.column(align=True)
    for key, title, _desc in overlays.KINDS:
        op = col.operator("bz.map_overlay_show", text=title, icon="CHECKBOX_HLT" if key == now else "CHECKBOX_DEHLT")
        op.kind = key
    if now:
        desc = next((d for k, _t, d in overlays.KINDS if k == now), "")
        _hint(layout, desc)
        layout.operator("bz.map_overlay_hide", text="Hide overlay", icon="HIDE_ON")


def draw_types(layout, context, top) -> None:
    _hint(layout, "Terrain types set footstep sounds and effects (Unknown counts as grass).")
    col = layout.column(align=True)
    for value, name in ((1, "Grass"), (2, "Snow"), (3, "DirtSand"), (4, "Rock"), (0, "Unknown")):
        row = col.row(align=True)
        op = row.operator("bz.map_types_paint", text=f"Paint {name}", icon="BRUSH_DATA")
        op.type = str(value)
        op = row.operator("bz.map_types_set", text="Selected")
        op.type = str(value)
    col.operator("bz.map_paint_stop", text="Stop painting", icon="LOOP_BACK")
    obj = P.paint_object(top, "types")
    if obj is not None:
        op = layout.operator("bz.map_paint_visible", text="Show" if obj.hide_viewport else "Hide", icon="HIDE_OFF")
        op.kind, op.show = "types", obj.hide_viewport


def draw_water(layout, context, top) -> None:
    _hint(layout, "Give selected points water at their height (lakes, pools); saved with the map.")
    col = layout.column(align=True)
    col.operator("bz.map_water_set", text="Water at a height...", icon="MOD_OCEAN")
    col.operator("bz.map_water_dry", text="Dry the selected points", icon="X")
    col.operator("bz.map_water_revert", text="Undo water paint here", icon="LOOP_BACK")
    col.operator("bz.map_water_redraw_painted", text="Redraw the water", icon="FILE_REFRESH")


def draw_decals(layout, context, top) -> None:
    _hint(layout, "Make handles, then move, turn (R), scale (S) or delete them to edit decals.")
    col = layout.column(align=True)
    n = len(P.decal_handles(top))
    if n:
        layout.label(text=f"{n} decal handles in '{P.decal_collection(top).name}'.")
    else:
        col.operator("bz.map_decals_edit", text="Edit decals (make handles)", icon="EMPTY_AXIS")
    col.operator("bz.map_decal_add", text="Add decal at the 3D cursor...", icon="ADD")
    col.operator("bz.map_decals_redraw", text="Redraw decals on the ground", icon="FILE_REFRESH")


def draw_tiles(layout, context, top) -> None:
    _hint(layout, "Add a blend tile, then 'Use on selected faces' where two textures meet.")
    layout.operator("bz.map_add_blend_tile", text="Add a blend tile...", icon="ADD")


def draw_stamp(layout, context, top) -> None:
    _hint(layout, "Stamps are permanent in <name>.tga, which the game shows only at top ground detail.")
    obj = P.paint_object(top, "ground")
    col = layout.column(align=True)
    if obj is None:
        col.operator("bz.map_ground_picture", text="Show the ground picture", icon="IMAGE_DATA").show = True
    else:
        op = col.operator("bz.map_ground_picture", text="Show the ground picture" if obj.hide_viewport else "Hide the ground picture",
                          icon="IMAGE_DATA")
        op.show = obj.hide_viewport
    col.operator("bz.map_stamp", text="Stamp at the 3D cursor...", icon="BRUSH_DATA")


class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IC"
    bl_parent_id = H.MAP_PANEL_ID
    bl_options = {"DEFAULT_CLOSED"}
    draw_fn = None

    @classmethod
    def poll(cls, context):
        return _top(context) is not None

    def draw(self, context):
        top = _top(context)
        if top is not None:
            type(self).draw_fn(self.layout, context, top)


def _panel(idname: str, label: str, fn):
    return type(idname, (_Sub, bpy.types.Panel), {"bl_idname": idname, "bl_label": label, "draw_fn": staticmethod(fn)})


BZ_PT_map_paint_impass = _panel("BZ_PT_map_paint_impass", "Passability", draw_passability)
BZ_PT_map_paint_overlays = _panel("BZ_PT_map_paint_overlays", "Overlays", draw_overlays)
BZ_PT_map_paint_types = _panel("BZ_PT_map_paint_types", "Terrain types", draw_types)
BZ_PT_map_paint_water = _panel("BZ_PT_map_paint_water", "Water levels", draw_water)
BZ_PT_map_paint_decals = _panel("BZ_PT_map_paint_decals", "Decals", draw_decals)
BZ_PT_map_paint_tiles = _panel("BZ_PT_map_paint_tiles", "Blend tiles", draw_tiles)
BZ_PT_map_paint_stamp = _panel("BZ_PT_map_paint_stamp", "Texture stamping", draw_stamp)

CLASSES = (BZ_PT_map_paint_impass, BZ_PT_map_paint_overlays, BZ_PT_map_paint_types, BZ_PT_map_paint_water, BZ_PT_map_paint_decals,
           BZ_PT_map_paint_tiles, BZ_PT_map_paint_stamp)
