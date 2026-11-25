"""sceneobj.ops_map_paint -- the operators of the map painting panels (passability, overlays, terrain types, water levels, decals).

Each operator works on the active map (the one the Map panel shows). Painting is done with Blender's own paint modes (Texture Paint for
the passability picture, Vertex Paint for terrain types) or on the faces / points selected in Edit Mode; a save writes what was painted
(see ``map_paint``). Plain-words messages: an operator that cannot do its job says why and changes nothing.
"""
from __future__ import annotations

import contextlib
import dataclasses
import math
import os
import tempfile

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import map_overlays as OV
from . import map_paint as P
from . import ops_map_import as M
from . import prefs

ORDER = 63

STATE_ITEMS = (("BLOCK", "Blocked", "Nobody can walk here"),
               ("OPEN", "Open", "The cell counts as flat ground: the slope no longer stops units (deep water still does)"),
               ("CLEAR", "Terrain decides", "Give the cell back to the game's own rule for the ground"))
STATE_VALUE = {"BLOCK": P.BLOCK, "OPEN": P.OPEN, "CLEAR": P.CLEAR}
TYPE_ITEMS = tuple((str(n), name, f"Footsteps and effects of {name.lower()} ground") for n, name in
                   ((0, "Unknown"), (1, "Grass"), (2, "Snow"), (3, "DirtSand"), (4, "Rock")))


def _need_map(op, context):
    X = P._x()
    top = X.active_map(context)
    if top is None:
        op.report({"ERROR"}, "There is no imported map in this scene. Open one with File > Import > Impossible Creatures Map.")
        return None, None
    terrain = X.terrain_of(top)
    if terrain is None:
        op.report({"ERROR"}, "The terrain of this map is not in the scene.")
        return None, None
    return top, terrain


def _poll_map(context) -> bool:
    return P._x().active_map(context) is not None


@contextlib.contextmanager
def object_mode(context, obj):
    """Leave Edit Mode (selection kept) so the mesh holds what is selected; go back into it afterwards."""
    was = obj.mode if obj is not None else "OBJECT"
    if was == "EDIT":
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if was == "EDIT":
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")


def selected_faces(me) -> np.ndarray:
    sel = np.zeros(len(me.polygons), bool)
    me.polygons.foreach_get("select", sel)
    return sel


def selected_points(me) -> np.ndarray:
    sel = np.zeros(len(me.vertices), bool)
    me.vertices.foreach_get("select", sel)
    return sel


def _enter_mode(context, obj, mode: str) -> None:
    for o in context.view_layer.objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.hide_viewport = False
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if obj.mode != mode:
        bpy.ops.object.mode_set(mode=mode)


def _set_brush_colour(context, rgb, erase: bool = False, vertex: bool = False) -> str:
    """Give the active paint brush a colour (the colour box as the user sees it); returns "" or a plain note when it could not be done."""
    ts = context.tool_settings
    paint = ts.vertex_paint if vertex else ts.image_paint
    note = ""
    done = False
    try:
        ups = paint.unified_paint_settings
        if ups.use_unified_color:
            ups.color = rgb
            done = True
    except Exception:                                            # noqa: BLE001
        pass
    try:
        if paint.brush is not None:
            paint.brush.color = rgb
            done = True
    except Exception:                                            # noqa: BLE001
        pass
    if not done:
        note = " Pick the colour in the paint tool's colour box."
    try:
        brush = paint.brush
        if erase:
            brush.blend = "ERASE_ALPHA"
        elif brush is not None and brush.blend == "ERASE_ALPHA":
            brush.blend = "MIX"
    except Exception:                                            # noqa: BLE001
        if erase:
            note += " Choose the Erase Alpha blend mode in the tool settings."
    return note


# ---------------------------------------------------------------------------------------------------- passability
class BZ_OT_map_impass_paint(bpy.types.Operator):
    """Paint blocked (red) and flat open (green) cells on the terrain"""
    bl_idname = "bz.map_impass_paint"
    bl_label = "Paint Passability"
    bl_options = {"REGISTER", "UNDO"}

    state: EnumProperty(name="Paint", items=STATE_ITEMS[:2] + (("ERASE", "Erase", "Rub paint out: the terrain decides again"),),
                        default="BLOCK")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            obj = P.ensure_passability(context, top)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        img = P.paint_image(top)
        try:
            _enter_mode(context, obj, "TEXTURE_PAINT")
            ip = context.tool_settings.image_paint
            ip.mode = "IMAGE"
            ip.canvas = img
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, f"Texture Paint could not be started: {exc}")
            return {"CANCELLED"}
        rgba = P.PAINT_RGBA[P.BLOCK if self.state == "ERASE" else STATE_VALUE[self.state]]
        note = _set_brush_colour(context, rgba[:3], erase=self.state == "ERASE")
        word = {"BLOCK": "blocked (red)", "OPEN": "open (green)", "ERASE": "erasing"}[self.state]
        self.report({"INFO"}, f"Painting passability: {word}. Paint on the terrain; press Stop painting when done.{note}")
        return {"FINISHED"}


class BZ_OT_map_paint_stop(bpy.types.Operator):
    """Leave the paint mode"""
    bl_idname = "bz.map_paint_stop"
    bl_label = "Stop Painting"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is not None and obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        X = P._x()
        top = X.active_map(context)
        terrain = X.terrain_of(top) if top is not None else None
        if terrain is not None:
            _enter_mode(context, terrain, "OBJECT")
        return {"FINISHED"}


class BZ_OT_map_impass_faces(bpy.types.Operator):
    """Set passability under the terrain faces selected in Edit Mode"""
    bl_idname = "bz.map_impass_faces"
    bl_label = "Set Passability of Selected Faces"
    bl_options = {"REGISTER", "UNDO"}

    state: EnumProperty(name="Set to", items=STATE_ITEMS, default="BLOCK")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            P.ensure_passability(context, top)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        img = P.paint_image(top)
        w, h, _ts, _hs = P.terrain_grid(terrain)
        with object_mode(context, terrain):
            sel = selected_faces(terrain.data)
        if len(sel) != (w - 1) * (h - 1):
            self.report({"ERROR"}, "The terrain no longer has the faces it was opened with.")
            return {"CANCELLED"}
        if not sel.any():
            self.report({"ERROR"}, "No faces are selected. Select terrain faces in Edit Mode first.")
            return {"CANCELLED"}
        mask = np.repeat(np.repeat(sel.reshape(h - 1, w - 1), 2, axis=0), 2, axis=1)
        pic = P.image_pixels(img).copy()
        pic[mask] = P.PAINT_RGBA[STATE_VALUE[self.state]]
        P.put_image_pixels(img, pic)
        self.report({"INFO"}, f"{int(mask.sum())} path cells of {int(sel.sum())} faces set to "
                              f"{dict((k, n) for k, n, _d in STATE_ITEMS)[self.state].lower()}.")
        return {"FINISHED"}


class BZ_OT_map_impass_reset(bpy.types.Operator):
    """Throw away what was painted and show the map's own passability layer again"""
    bl_idname = "bz.map_impass_reset"
    bl_label = "Reset Passability Paint"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context) and P.paint_object(P._x().active_map(context), "impass") is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            P.ensure_passability(context, top, reset=True)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "The passability paint is back to the map's own layer.")
        return {"FINISHED"}


class BZ_OT_map_paint_visible(bpy.types.Operator):
    """Show or hide one of the paint layers over the terrain"""
    bl_idname = "bz.map_paint_visible"
    bl_label = "Show / Hide Paint"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="impass", options={"HIDDEN"})
    show: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        obj = P.paint_object(top, self.kind)
        if obj is None:
            self.report({"ERROR"}, "That paint layer has not been made yet.")
            return {"CANCELLED"}
        obj.hide_set(not self.show)
        obj.hide_viewport = not self.show
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- overlays
def _overlay_items(self, context):
    from ICParse.sgb import overlays
    return [(k, t, d) for k, t, d in overlays.KINDS]


class BZ_OT_map_overlay_show(bpy.types.Operator):
    """Show a pathfinding overlay of the map as it would be saved"""
    bl_idname = "bz.map_overlay_show"
    bl_label = "Show Overlay"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Overlay", items=_overlay_items)

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            res = OV.show(context, top, self.kind)
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, f"The overlay could not be made: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{res['title']}: {res['cells']} {'cells' if res['key'] not in ('sub_sector', 'land_sector', 'amphibian_sector') else 'regions'} "
                              f"marked, made in {res['seconds']} s.{(' ' + res['note']) if res['note'] else ''}")
        return {"FINISHED"}


class BZ_OT_map_overlay_hide(bpy.types.Operator):
    """Take the overlay off the terrain"""
    bl_idname = "bz.map_overlay_hide"
    bl_label = "Hide Overlay"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        OV.hide(top)
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- terrain types
class BZ_OT_map_types_paint(bpy.types.Operator):
    """Paint terrain types (footstep sounds) on the terrain with Vertex Paint"""
    bl_idname = "bz.map_types_paint"
    bl_label = "Paint Terrain Types"
    bl_options = {"REGISTER", "UNDO"}

    type: EnumProperty(name="Type", items=TYPE_ITEMS, default="1")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            obj = P.ensure_types(context, top)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        try:
            _enter_mode(context, obj, "VERTEX_PAINT")
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, f"Vertex Paint could not be started: {exc}")
            return {"CANCELLED"}
        from ICParse.sgb import terrain_types as T
        c = np.array(T.colour_of(int(self.type)), np.float64) / 255.0
        note = _set_brush_colour(context, tuple(float(v) for v in c), vertex=True)
        self.report({"INFO"}, f"Painting {T.name_of(int(self.type))} ground. Press Stop painting when done.{note}")
        return {"FINISHED"}


class BZ_OT_map_types_set(bpy.types.Operator):
    """Give the terrain points selected in Edit Mode a terrain type"""
    bl_idname = "bz.map_types_set"
    bl_label = "Set Terrain Type of Selected Points"
    bl_options = {"REGISTER", "UNDO"}

    type: EnumProperty(name="Type", items=TYPE_ITEMS, default="1")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            P.ensure_types(context, top)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        with object_mode(context, terrain):
            sel = selected_points(terrain.data)
            if not sel.any():
                self.report({"ERROR"}, "No points are selected. Select terrain points in Edit Mode first.")
                return {"CANCELLED"}
            attr = terrain.data.color_attributes[P.TYPE_ATTR]
            col = np.empty(len(sel) * 4, np.float32)
            attr.data.foreach_get("color", col)
            col = col.reshape(-1, 4)
            col[sel, :3] = P.type_palette()[int(self.type)]
            attr.data.foreach_set("color", col.ravel())
            terrain.data.update()
        from ICParse.sgb import terrain_types as T
        self.report({"INFO"}, f"{int(sel.sum())} points are now {T.name_of(int(self.type))} ground.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- water at its own level
def redraw_painted_water(context, top) -> dict:
    """Draw the water surface again with the painted water heights (and the map's own level) in it."""
    from . import map_look as ML
    X = P._x()
    _raw, mv, _gm = X.read_source(context, top)
    terrain = X.terrain_of(top)
    painted = P.water_paint_of(terrain)
    plane = mv.water if painted is None else P.merge_water(mv.water.reshape(-1), painted).reshape(mv.water.shape)
    mv2 = dataclasses.replace(mv, water=plane)
    level = ML.current_water_level(top, mv)                       # the level as the scene has it, read against the ORIGINAL surface
    if X.water_of(top) is None:
        return ML.add_water(context, top, mv2, level)
    return ML.redraw_water(context, top, mv2, level_m=level)


class _WaterPaint:
    """What the three water paint operators share (a plain mixin: Blender operator classes cannot inherit from each other)."""

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def _paint(self, context, top, terrain, value: int, words: str, redraw: bool = True):
        with object_mode(context, terrain):
            sel = selected_points(terrain.data)
            if not sel.any():
                self.report({"ERROR"}, "No points are selected. Select terrain points in Edit Mode first.")
                return {"CANCELLED"}
            attr = P.ensure_water_attr(terrain)
            arr = np.empty(len(sel), np.int32)
            attr.data.foreach_get("value", arr)
            arr[sel] = value
            attr.data.foreach_set("value", arr)
            terrain.data.update()
        msg = f"{int(sel.sum())} points hold water {words}."
        if redraw:
            try:
                res = redraw_painted_water(context, top)
                msg += f" The water is drawn again over {res['cells']} cells."
            except Exception as exc:                              # noqa: BLE001
                msg += f" The water could not be drawn again: {exc}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BZ_OT_map_water_set(_WaterPaint, bpy.types.Operator):
    """Add water at the height of the terrain points selected in Edit Mode"""
    bl_idname = "bz.map_water_set"
    bl_label = "Set Water Height of Selected Points"
    bl_options = {"REGISTER", "UNDO"}

    height: FloatProperty(name="Water height (m)", default=20.0, min=0.0, soft_max=127.0,
                          description="Water surface height in metres; ground below it is wet")
    redraw: BoolProperty(name="Draw the water again", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        step = float(terrain["ic_height_scale"])
        value = int(round(self.height / step))
        if value < 0 or value > 255:
            self.report({"ERROR"}, f"The map keeps water heights from 0 to {255 * step:g} m in steps of {step:g} m.")
            return {"CANCELLED"}
        return self._paint(context, top, terrain, value, f"at {value * step:g} m", self.redraw)


class BZ_OT_map_water_dry(_WaterPaint, bpy.types.Operator):
    """Take the water away from the terrain points selected in Edit Mode"""
    bl_idname = "bz.map_water_dry"
    bl_label = "Dry Selected Points"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        return self._paint(context, top, terrain, 0, "no water")


class BZ_OT_map_water_revert(_WaterPaint, bpy.types.Operator):
    """Put the selected terrain points' water back to what the map had"""
    bl_idname = "bz.map_water_revert"
    bl_label = "Undo Water Paint on Selected Points"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        return self._paint(context, top, terrain, -1, "as the map had it")


class BZ_OT_map_water_redraw_painted(bpy.types.Operator):
    """Draw the water surface again from the ground and the water you painted"""
    bl_idname = "bz.map_water_redraw_painted"
    bl_label = "Redraw Water With My Paint"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            res = redraw_painted_water(context, top)
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"The water is drawn again over {res['cells']} cells.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- decals
def decal_pictures(game_dir: str, archive: str = "") -> list:
    """The decal pictures of the game's files (``decals/*.tga``)."""
    if not game_dir:
        return []
    view = M._view()
    names: set = set()
    data_dir = view._child(game_dir, "Data")
    root = view._child(data_dir, "decals") if data_dir else None
    if root:
        names |= {f.lower() for f in os.listdir(root) if f.lower().endswith(".tga")}
    import ICParse.sga as sga
    for path in view.archive_order(game_dir, archive):
        try:
            arc = sga.read(path)
        except Exception:                                         # noqa: BLE001
            continue
        for e in arc.entries:
            p = e.path.replace("\\", "/").lower()
            if p.startswith("decals/") and p.endswith(".tga") and p.count("/") == 1:
                names.add(p[len("decals/"):])
    return sorted(names)


_DECAL_ITEMS: list = []


def _decal_items(self, context):
    top = P._x().active_map(context)
    game_dir = prefs.resolve_game_dir(context)[0]
    names = decal_pictures(game_dir, top.get(M.KEY_ARCHIVE, "") if top else "")
    items = [(n, os.path.splitext(n)[0], f"decals/{n} in the game's files", "", i) for i, n in enumerate(names)]
    _DECAL_ITEMS[:] = items or [("NONE", "No decal pictures found: set the game folder in the add-on's preferences", "", "ERROR", 0)]
    return _DECAL_ITEMS


def new_handle(context, top, mv, name: str, picture: str, x: float, z: float, scale: float, rot: float, eid: int):
    """A decal handle: an empty at (x, z) on the ground, turned and scaled as the decal is. ``eid`` -1 for a new decal."""
    core, rig = M._addon("core"), M._addon("rig")
    coll = P.decal_collection(top, create=True)
    y = float(mv.ground_height(x, z)) + 0.5
    c, s = math.cos(rot), math.sin(rot)
    game = np.array([[c, 0.0, -s, x], [0.0, 1.0, 0.0, y], [s, 0.0, c, z], [0.0, 0.0, 0.0, 1.0]])
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = "ARROWS"
    obj.empty_display_size = max(2.0, 4.0 * abs(scale))
    coll.objects.link(obj)
    obj.matrix_world = rig.to_blender_matrix(game)
    obj[P.KEY_DECAL] = int(eid)
    obj[P.KEY_DECAL_PICTURE] = picture
    obj["ic_decal_scale"] = abs(float(scale))
    obj["ic_decal_mirror"] = bool(scale < 0)
    return obj


class BZ_OT_map_decals_edit(bpy.types.Operator):
    """Make a movable handle for every decal of the map"""
    bl_idname = "bz.map_decals_edit"
    bl_label = "Edit Decals"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        from ICParse.sgb import decals_edit
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        if P.decal_collection(top) is not None and P.decal_handles(top):
            self.report({"WARNING"}, "The decal handles are already there.")
            return {"CANCELLED"}
        X = P._x()
        raw, mv, _gm = X.read_source(context, top)
        rows = decals_edit.read_decals(raw)
        for i, r in enumerate(rows):
            new_handle(context, top, mv, f"{P.DECAL_HANDLE_PREFIX}{i} {os.path.splitext(r.texture)[0]}", r.texture, r.x, r.z, r.scale,
                       r.rot, r.eid)
        P.decal_collection(top, create=True)
        self.report({"INFO"}, f"{len(rows)} decal handles made in '{P.decal_collection(top).name}'. Move, turn (R) or scale (S) one to "
                              "change its decal; delete one to remove the decal.")
        return {"FINISHED"}


class BZ_OT_map_decal_add(bpy.types.Operator):
    """Put a new decal at the 3D cursor"""
    bl_idname = "bz.map_decal_add"
    bl_label = "Add Decal"
    bl_options = {"REGISTER", "UNDO"}

    picture: EnumProperty(name="Picture", items=_decal_items, description="A decal picture of the game")
    scale: FloatProperty(name="Scale", default=1.0, min=0.05, soft_max=8.0,
                         description="The decal's size: its width is its picture's pixels x 0.125 m x this scale")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        if self.picture == "NONE":
            self.report({"ERROR"}, "No decal pictures found: set the game folder in the add-on's preferences.")
            return {"CANCELLED"}
        X = P._x()
        core = M._addon("core")
        _raw, mv, _gm = X.read_source(context, top)
        cur = context.scene.cursor.location
        game = core.blender_to_sgm(np.array([[cur.x, cur.y, cur.z]]))[0]
        n = len(P.decal_handles(top))
        obj = new_handle(context, top, mv, f"{P.DECAL_HANDLE_PREFIX}new {os.path.splitext(self.picture)[0]}", self.picture,
                         float(game[0]), float(game[2]), float(self.scale), 0.0, -1)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({"INFO"}, f"A new {os.path.splitext(self.picture)[0]} decal is at the 3D cursor ({n + 1} decal handles).")
        return {"FINISHED"}


class BZ_OT_map_decals_redraw(bpy.types.Operator):
    """Redraw the decals where their handles are now"""
    bl_idname = "bz.map_decals_redraw"
    bl_label = "Redraw Decals"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        from . import map_look as ML
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        coll = next((c for c in top.children_recursive if c.name.endswith(" look decals")), None)
        if coll is None or not P.decal_handles(top):
            self.report({"ERROR"}, "Show the game look and make the decal handles (Edit Decals) first.")
            return {"CANCELLED"}
        X = P._x()
        _raw, mv, _gm = X.read_source(context, top)
        look = mv.look()
        from ICParse.sgb.view import LookDecal
        rows = P.decal_rows(top)
        shim = dataclasses.replace(look, decals=[LookDecal(texture=r.texture, x=r.x, z=r.z, scale=r.scale, rotation=r.rot) for r in rows])
        for o in list(coll.objects):
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
        game_dir = prefs.resolve_game_dir(context)[0]
        data = M._view().game_data(game_dir, mv.archive) if game_dir else None
        tmp = tempfile.mkdtemp(prefix="ic_decals_")
        try:
            made, missing = ML._decals(context, coll, P.map_name_of(top), mv, shim, data, tmp)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        self.report({"INFO"}, f"{len(rows)} decals drawn on the ground in {len(made)} pictures."
                              + (f" Not in the game's files: {', '.join(missing)}." if missing else ""))
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- texture stamping
def load_stamp(context, top, picture: str) -> np.ndarray:
    """A decal picture of the game as ``(h, w, 4)`` float RGBA, rows as stored in the file."""
    from ICParse.sgb import ground_picture as G
    game_dir = prefs.resolve_game_dir(context)[0]
    data = M._view().game_data(game_dir, top.get(M.KEY_ARCHIVE, "")) if game_dir else None
    raw = data.read("data:decals/" + picture) if data is not None else None
    if not raw:
        raise ValueError(f"The picture {picture} is not in the game's files.")
    try:
        return G.decode(raw).pixels.astype(np.float64) / 255.0
    except G.PictureError as exc:
        raise ValueError(str(exc)) from None


class BZ_OT_map_ground_picture(bpy.types.Operator):
    """Show or hide the map's ground picture on the terrain"""
    bl_idname = "bz.map_ground_picture"
    bl_label = "Ground Picture"
    bl_options = {"REGISTER", "UNDO"}

    show: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def execute(self, context):
        top, _terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            obj = P.ensure_ground(context, top)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        obj.hide_set(not self.show)
        obj.hide_viewport = not self.show
        img = P.paint_image(top, "ground")
        self.report({"INFO"}, f"The ground picture ({img.size[0]} x {img.size[1]} pixels) is {'shown' if self.show else 'hidden'}.")
        return {"FINISHED"}


class BZ_OT_map_stamp(bpy.types.Operator):
    """Paint a decal permanently into the map's ground picture at the 3D cursor"""
    bl_idname = "bz.map_stamp"
    bl_label = "Stamp Texture"
    bl_options = {"REGISTER", "UNDO"}

    picture: EnumProperty(name="Picture", items=_decal_items, description="A decal picture of the game")
    scale: FloatProperty(name="Size", default=1.0, min=0.05, soft_max=8.0,
                         description="The stamp's width is its picture's pixels x 0.125 m x this size, as a decal's")
    turn: FloatProperty(name="Turn (degrees)", default=0.0, min=-360.0, max=360.0)
    strength: FloatProperty(name="Strength", default=1.0, min=0.0, max=1.0, description="How opaque the stamp is: low is see-through")
    tint: bpy.props.FloatVectorProperty(name="Colour", subtype="COLOR", size=3, default=(1.0, 1.0, 1.0), min=0.0, max=1.0,
                                        description="Tint for the stamp's colours (white leaves them unchanged)")
    count: bpy.props.IntProperty(name="Stamps", default=1, min=1, max=200, description="How many stamps in a row")
    spacing: FloatProperty(name="Spacing (m)", default=8.0, min=0.0, soft_max=100.0,
                           description="The distance between stamps of a row, along the way the stamp is turned")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        from ICParse.sgb import ground_picture as G
        top, terrain = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        if self.picture == "NONE":
            self.report({"ERROR"}, "No decal pictures found: set the game folder in the add-on's preferences.")
            return {"CANCELLED"}
        try:
            P.ensure_ground(context, top)
            stamp = load_stamp(context, top, self.picture)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        img = P.paint_image(top, "ground")
        pixels = P.ground_pixels(img)
        w, h, ts, _hs = P.terrain_grid(terrain)
        world = (w - 1) * ts
        core = M._addon("core")
        cur = context.scene.cursor.location
        gx, _gy, gz = core.blender_to_sgm(np.array([[cur.x, cur.y, cur.z]]))[0]
        hs_px, ws_px = stamp.shape[:2]
        half_u, half_v = ws_px * 0.0625 * self.scale, hs_px * 0.0625 * self.scale      # half of a decal's width and height
        rot = math.radians(-self.turn)                                # Blender turns the other way round the game's vertical axis
        changed = 0
        for k in range(self.count):
            along = k * self.spacing
            changed += G.stamp(pixels, stamp, x=float(gx) + along * math.cos(rot), z=float(gz) + along * math.sin(rot),
                               half_u=half_u, half_v=half_v, rotation=rot, world_size=world, strength=self.strength, tint=tuple(self.tint))
        P.put_ground_pixels(img, pixels)
        if not changed:
            self.report({"WARNING"}, "The stamp changed no pixel: it is off the map, or has no strength, or is the colour that is already there.")
            return {"FINISHED"}
        self.report({"INFO"}, f"{self.count} stamp{'s' if self.count > 1 else ''} painted into the ground picture ({changed} pixels). "
                              "Saving writes the picture as <name>.tga.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- ground texture blend tiles
_TILE_ITEMS: list = []


def blend_tiles_for(top, names) -> list:
    """The blend tiles of the game (``detail/<n>%corner_...tga``, ``...-to-...-from-...tga``) that join two ground textures of which at
    least one is on this map: ``[(file name, plain description)]``. ``names`` are the game's ground texture files."""
    from ICParse.sgb import transitions
    terrain = P._x().terrain_of(top)
    used = {os.path.splitext(str(m["ic_ground_texture"]).lower())[0] for m in terrain.data.materials
            if m is not None and m.get("ic_ground_texture")} if terrain is not None else set()
    out = []
    for n in names:
        rec = transitions.parse_tile(os.path.splitext(n)[0])
        if rec is None or not ({rec["a"], rec["b"]} & used):
            continue
        if rec["kind"] == "grad":
            words = f"{rec['a']} turning into {rec['b']}, coming from the {rec['dir']}"
        else:
            words = f"{rec['pct']}% {rec['kind']}: {rec['a']} ({rec['a_pos']}) against {rec['b']} ({rec['b_pos']})"
        out.append((n, words))
    return out


def _tile_items(self, context):
    X = P._x()
    top = X.active_map(context)
    game_dir = prefs.resolve_game_dir(context)[0]
    names = X.game_listing(game_dir, top.get(M.KEY_ARCHIVE, "") if top else "")["ground"] if game_dir and top else []
    tiles = blend_tiles_for(top, names) if top is not None and names else []
    items = [(n, words, f"detail/{n} in the game's files", "", i) for i, (n, words) in enumerate(tiles)]
    _TILE_ITEMS[:] = items or [("NONE", "No blend tiles found for this map's ground textures", "", "ERROR", 0)]
    return _TILE_ITEMS


class BZ_OT_map_add_blend_tile(bpy.types.Operator):
    """Add one of the game's blend tiles (a soft edge between two ground textures)"""
    bl_idname = "bz.map_add_blend_tile"
    bl_label = "Add Blend Tile"
    bl_options = {"REGISTER", "UNDO"}

    tile: EnumProperty(name="Tile", items=_tile_items, description="A blend tile that joins a ground texture of this map to another")

    @classmethod
    def poll(cls, context):
        return _poll_map(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if self.tile == "NONE":
            self.report({"ERROR"}, "No blend tiles found for this map's ground textures (is the game folder set in the add-on's preferences?).")
            return {"CANCELLED"}
        return bpy.ops.bz.map_add_ground(texture=self.tile)


CLASSES = (BZ_OT_map_impass_paint, BZ_OT_map_paint_stop, BZ_OT_map_impass_faces, BZ_OT_map_impass_reset, BZ_OT_map_paint_visible,
           BZ_OT_map_overlay_show, BZ_OT_map_overlay_hide, BZ_OT_map_types_paint, BZ_OT_map_types_set, BZ_OT_map_water_set,
           BZ_OT_map_water_dry, BZ_OT_map_water_revert, BZ_OT_map_water_redraw_painted, BZ_OT_map_decals_edit, BZ_OT_map_decal_add,
           BZ_OT_map_decals_redraw, BZ_OT_map_add_blend_tile, BZ_OT_map_ground_picture, BZ_OT_map_stamp)
