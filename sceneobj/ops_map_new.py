"""sceneobj.ops_map_new -- File > New > Impossible Creatures Map: a new map from one of the Mission Editor's 15 terrain templates.

The template is the board the Mission Editor itself starts from (``Data/medata/defterrain.lua``: five kinds of ground, each small, medium or
large). The new map is that board with the header the game needs to list it (the name typed here, 2 to 6 players, multiplayer or single player;
``ICParse.sgb.scenario.new_board``), opened through ``map_hooks.import_map_bytes`` so it is a map like any other: sculpt it, paint it, place
objects, set the sun, then save. The files a map needs beside it (its ground picture, minimap, icon, triggers, dialogue) are added by the save.
"""
from __future__ import annotations

import re

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import map_hooks as H
from . import map_settings as S
from . import prefs

ORDER = 64


def _sc():
    from ICParse.sgb import scenario
    return scenario


def _kind_items(self, context):
    return [(k, kn, f"{kn} ground and sky") for k, kn in _sc().template_kinds()]


def _size_items(self, context):
    return [(s, f"{sn} ({c}x{c}, {m} m)", f"A map {m} m across") for s, sn, c, m in _sc().template_sizes()]


def file_name_of(title: str) -> str:
    """A file name the game can load for a map name (letters, digits and underscores, lower case: archive paths are looked up in lower case)."""
    return re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_") or "new_map"


def new_map(context, *, title: str, template_key: str, players: int, kind: int = 2, place_starts: bool = True, load_models: bool = True,
            game_look: bool = True, game_dir: str | None = None):
    """Make the map and return its collection. Raises ``ValueError`` in plain words."""
    from ICParse.sgb import view
    sc = _sc()
    game_dir = game_dir or prefs.resolve_game_dir(context)[0]
    if not game_dir:
        raise ValueError("A new map starts from one of the game's terrain templates, which are in the game's files, and no game folder was "
                         "found. Set the game folder in the add-on's preferences.")
    t = sc.template(template_key)
    raw = view.game_data(game_dir).read(t.path)
    if not raw:
        raise ValueError(f"The terrain template {t.key} ({t.path}) is not in the game's files.")
    title = sc.check_title(title)
    board = sc.new_board(raw, title, int(players), kind=int(kind))
    top = H.import_map_bytes(context, board, file_name_of(title), {"game_dir": game_dir, "load_models": load_models, "game_look": game_look})
    top[S.KEY_TEMPLATE] = t.key
    if place_starts and int(kind) == sc.KIND_MULTIPLAYER:
        for slot, (x, y) in enumerate(S.ring_positions(top, int(players))):
            S.place_start(context, top, slot, x, y)
    return top


class BZ_OT_new_map(bpy.types.Operator):
    """Make a new Impossible Creatures map from one of the game's terrain templates"""
    bl_idname = "bz.new_map"
    bl_label = "New Impossible Creatures Map"
    bl_options = {"REGISTER", "UNDO"}

    title: StringProperty(name="Map name", default="My Map", description="The name the game's map list shows")
    ground: EnumProperty(name="Ground", items=_kind_items, description="The kind of ground the map starts with")
    size: EnumProperty(name="Size", items=_size_items, description="How big the map is")
    map_kind: EnumProperty(name="Kind", items=[("2", "Multiplayer", "A map for the multiplayer list (2 to 6 players)"),
                                               ("1", "Single player", "A campaign or scripted map")], default="2")
    players: IntProperty(name="Players", min=1, max=6, default=2, description="How many players the map is made for")
    place_starts: BoolProperty(name="Give each player a start", default=True,
                               description="Give every player a lab and three henchmen, in a ring around the middle")
    load_models: BoolProperty(name="Show models", default=True, description="Show the placed objects as their models")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "title")
        col.prop(self, "ground")
        col.prop(self, "size")
        col.prop(self, "map_kind")
        col.prop(self, "players")
        col.prop(self, "place_starts")
        col.prop(self, "load_models")

    def execute(self, context):
        try:
            top = new_map(context, title=self.title, template_key=f"{self.ground}_{self.size}", players=self.players, kind=int(self.map_kind),
                          place_starts=self.place_starts, load_models=self.load_models)
        except Exception as exc:             # noqa: BLE001   every failure is said in words
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"New map '{self.title.strip()}' made from the {self.ground} {self.size} template. Save it with File > Export, "
                              "or from the Map panel (IC Map tab).")
        return {"FINISHED"}


def menu_new(self, context):
    self.layout.operator(BZ_OT_new_map.bl_idname, text="Impossible Creatures Map", icon="WORLD")


CLASSES = (BZ_OT_new_map,)


def register():
    bpy.types.TOPBAR_MT_file_new.append(menu_new)


def unregister():
    bpy.types.TOPBAR_MT_file_new.remove(menu_new)
