"""Add-on preferences. Slices read them through these functions, never ``bpy.context`` directly, so the defaults apply
when the add-on is not enabled (headless tests).

Only the Game folder and Game build are read so far; the rest are listed in ``NOT_USED`` and labelled "(not used yet)".
"""
from __future__ import annotations

import os
import sys

import bpy
from bpy.props import EnumProperty, StringProperty

from . import model, ui_kit

ORDER = 6
ADDON_ID = __package__.rpartition(".")[0]        # the add-on's own module name: "ic_blender" here, bl_ext.<repo>.<id> as an extension

# Every preference and its default: the class below takes its defaults from here, and get() falls back to it.
DEFAULTS = {
    "game_dir": "",
    "test_game_dir": "",
    "build_dir": "",
    "mods_dir": "",
    "target_mod": "icmod",
    "morph_export": "V2_SINGLE_LOD",
    "txr_type_default": "stock1",
    "delivery_mode": "AUTO",
    "bounds_mode": "AUTO",
}

# the settings nothing reads yet: their labels and descriptions say so (the module docstring says what reads the others)
NOT_USED = ("test_game_dir", "build_dir", "mods_dir", "morph_export", "txr_type_default", "delivery_mode", "bounds_mode")
_NOT_USED_LABEL = " (not used yet)"
_NOT_USED_WORDS = "Not used yet. "

TARGET_MOD_ITEMS = (
    ("icmod", "Original game (icmod)", "Made for the original game's rules"),
    ("tellurian", "Tellurian build (tellurian)", "For the Tellurian expansion build, which has more kinds (class numbers)"),
    ("insectmod", "Insect build (insectmod)", "For the Insect expansion build, which has more kinds (class numbers)"),
)
MORPH_EXPORT_ITEMS = (
    ("V2_SINGLE_LOD", "Rebuilt mesh, one detail level", "Write the newer single-detail mesh layout; not yet shown to load "
                                                        "in the game"),
    ("DROP", "Leave the damage states out", "A part with damage states is written without them. Safe, but the states are lost"),
)
TXR_TYPE_ITEMS = (
    ("stock1", "Type 1", "Textures are written as type 1, the add-on's long-standing choice"),
    ("stock0", "Type 0", "Textures are written as type 0, which most of the game's own textures use"),
)
DELIVERY_ITEMS = (
    ("AUTO", "Automatic", "Replace game files only for blueprints looked up by path; otherwise use a new name"),
    ("NEW_NAME", "Always a new name", "Never replace a game file"),
    ("REPLACE_STOCK", "Always replace the game's file", "The item takes the place of the game's own file of that name"),
)
BOUNDS_ITEMS = (
    ("AUTO", "Recompute for changed parts", "The selection and blocking box follows the parts you changed, with a before / after report"),
    ("KEEP_ENTITY_BOX", "Keep the original box", "The selection and blocking box stays exactly as the starting model had it"),
)


class BZ_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    game_dir: StringProperty(
        name="Game folder", subtype="DIR_PATH", default=DEFAULTS["game_dir"],
        description="The game folder (with IC.exe); empty uses IC_INSTALL or the usual Steam folders")
    test_game_dir: StringProperty(
        name="Test game folder" + _NOT_USED_LABEL, subtype="DIR_PATH", default=DEFAULTS["test_game_dir"],
        description=_NOT_USED_WORDS + "Planned: a copy of the game where finished items are installed for testing")
    build_dir: StringProperty(
        name="Build folder" + _NOT_USED_LABEL, subtype="DIR_PATH", default=DEFAULTS["build_dir"],
        description=_NOT_USED_WORDS + "Planned: where exports are written")
    mods_dir: StringProperty(
        name="Mods folder" + _NOT_USED_LABEL, subtype="DIR_PATH", default=DEFAULTS["mods_dir"],
        description=_NOT_USED_WORDS + "Planned: where finished mod items are collected")
    target_mod: EnumProperty(
        name="Game build", items=TARGET_MOD_ITEMS, default=DEFAULTS["target_mod"],
        description="The game build you work for; it sets which kind (class) numbers are known")
    morph_export: EnumProperty(
        name="Damage states" + _NOT_USED_LABEL, items=MORPH_EXPORT_ITEMS, default=DEFAULTS["morph_export"],
        description=_NOT_USED_WORDS + "Planned: how parts with damage states are exported")
    txr_type_default: EnumProperty(
        name="Texture type" + _NOT_USED_LABEL, items=TXR_TYPE_ITEMS, default=DEFAULTS["txr_type_default"],
        description=_NOT_USED_WORDS + "Planned: which texture type new textures use")
    delivery_mode: EnumProperty(
        name="Replace game files" + _NOT_USED_LABEL, items=DELIVERY_ITEMS, default=DEFAULTS["delivery_mode"],
        description=_NOT_USED_WORDS + "Planned: whether new items replace game files or get new names")
    bounds_mode: EnumProperty(
        name="Selection box" + _NOT_USED_LABEL, items=BOUNDS_ITEMS, default=DEFAULTS["bounds_mode"],
        description=_NOT_USED_WORDS + "Planned: whether the selection box is recomputed when parts change")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Game", icon="FILE_FOLDER")
        col = box.column()
        col.prop(self, "game_dir")
        folder, source = resolve_game_dir(context)
        if folder is None:
            ui_kit.alert(col, "No game found. Set the Game folder above, or set the IC_INSTALL environment variable.", icon="ERROR")
        else:
            col.label(text=f"Using {folder} ({SOURCE_WORDS[source]})", icon="CHECKMARK")
        col.prop(self, "test_game_dir")

        box = layout.box()
        box.label(text="Where finished work goes", icon="EXPORT")
        col = box.column()
        col.prop(self, "build_dir")
        col.prop(self, "mods_dir")
        col.prop(self, "target_mod")

        box = layout.box()
        box.label(text="Defaults to change only after trying things in the game (none is used yet)", icon="INFO")
        col = box.column()
        col.prop(self, "morph_export")
        col.prop(self, "txr_type_default")
        col.prop(self, "delivery_mode")
        col.prop(self, "bounds_mode")

        problems = list(getattr(sys.modules.get(__package__), "ERRORS", ()))
        if problems:
            box = layout.box()
            box.label(text="Parts of the add-on that could not be loaded", icon="ERROR")
            for name, reason in problems:
                ui_kit.alert(box, f"{name}: {reason}")


SOURCE_WORDS = {"preferences": "from the setting above", "IC_INSTALL": "from the IC_INSTALL environment variable",
                "steam default": "the usual Steam folder"}
# the same, said outside the preferences page (in a panel or a dialog)
SOURCE_WORDS_ELSEWHERE = {**SOURCE_WORDS, "preferences": "from the Game folder in the add-on's preferences"}
NO_GAME = ("No game folder found: set the Game folder in the add-on's preferences (Edit > Preferences > Add-ons, IC Blender), "
           "or set the IC_INSTALL environment variable.")


class _Defaults:
    """The preferences' names with their default values, for when the add-on is not enabled in the preferences."""
    bl_idname = ADDON_ID

    def __init__(self):
        for name, value in DEFAULTS.items():
            setattr(self, name, value)


def get(context=None):
    """The live preferences of this add-on, or an object with the same names holding the defaults."""
    ctx = context if context is not None else bpy.context
    addon = ctx.preferences.addons.get(ADDON_ID)
    live = getattr(addon, "preferences", None)
    return live if live is not None and all(hasattr(live, name) for name in DEFAULTS) else _Defaults()


def _folder(value: str):
    """An absolute existing folder from a path setting, or None."""
    path = bpy.path.abspath((value or "").strip()) if (value or "").strip() else ""
    return path if path and os.path.isdir(path) else None


def resolve_game_dir(context=None) -> tuple:
    """``(folder or None, source)``: the Game folder setting, else ``$IC_INSTALL``, else ICParse's usual Steam folders."""
    return model.pick_game_dir(_folder(get(context).game_dir) or "")


def game_dir_line(context=None) -> str:
    """One plain line saying which game folder the add-on reads the game's own files from, and why that one (for a panel or a
    dialog); :data:`NO_GAME` when there is none."""
    folder, source = resolve_game_dir(context)
    if folder is None:
        return NO_GAME
    return f"Game folder: {folder} ({SOURCE_WORDS_ELSEWHERE.get(source, source)})"


def resolve_test_game_dir(context=None):
    return _folder(get(context).test_game_dir)


def resolve_mods_dir(context=None):
    return _folder(get(context).mods_dir)


def resolve_build_dir(project_dir: str, context=None) -> str:
    """The Build folder setting when it is set, else ``<project_dir>/build`` (it may not exist yet: the exporter creates it)."""
    value = (get(context).build_dir or "").strip()
    return bpy.path.abspath(value) if value else os.path.join(project_dir, "build")


CLASSES = (BZ_AP_prefs,)
