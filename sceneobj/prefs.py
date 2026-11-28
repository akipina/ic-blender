"""Add-on preferences. Slices read them through these functions, never ``bpy.context`` directly, so the defaults apply
when the add-on is not enabled (headless tests).

Every setting here is read by something: Game folder (where the game's own files are read from, and where "Install into Game" writes
unless the Install folder is set, ``gameinstall.py``), Install folder (optional: another copy of the game to install into instead),
and Game build (the kind numbers the Blueprint panel knows). ``allow_steam_install`` is not used any more; it stays defined so saved
preferences still load.
"""
from __future__ import annotations

import os
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import model, ui_kit

ORDER = 6
ADDON_ID = __package__.rpartition(".")[0]        # the add-on's own module name: "ic_blender" here, bl_ext.<repo>.<id> as an extension

# Every preference and its default: the class below takes its defaults from here, and get() falls back to it.
DEFAULTS = {
    "game_dir": "",
    "test_game_dir": "",
    "target_mod": "icmod",
    "allow_steam_install": False,
}

TARGET_MOD_ITEMS = (
    ("icmod", "Original game (icmod)", "Made for the original game's rules"),
    ("tellurian", "Tellurian build (tellurian)", "For the Tellurian expansion build, which has more kinds (class numbers)"),
    ("insectmod", "Insect build (insectmod)", "For the Insect expansion build, which has more kinds (class numbers)"),
)


class BZ_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    game_dir: StringProperty(
        name="Game folder", subtype="DIR_PATH", default=DEFAULTS["game_dir"],
        description="The game folder (with IC.exe); empty uses IC_INSTALL or the usual Steam folders")
    test_game_dir: StringProperty(
        name="Install folder (optional)", subtype="DIR_PATH", default=DEFAULTS["test_game_dir"],
        description="Leave empty to install into the game folder. Set it to install into another copy of the game instead")
    allow_steam_install: BoolProperty(
        name="Allow the Steam game folder", default=DEFAULTS["allow_steam_install"], options={"HIDDEN"},
        description="Kept so saved preferences still load")
    target_mod: EnumProperty(
        name="Game build", items=TARGET_MOD_ITEMS, default=DEFAULTS["target_mod"],
        description="The game build you work for; it sets which kind (class) numbers are known")

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

        box = layout.box()
        box.label(text="Install", icon="PLAY")
        col = box.column()
        col.prop(self, "test_game_dir")
        col.operator("bz.create_test_game", text="Make a Separate Game Copy", icon="DUPLICATE")        # makes the copy and sets the folder above
        raw = (self.test_game_dir or "").strip()
        own = resolve_test_game_dir(context)
        if raw and own is None:
            ui_kit.alert(col, "The install folder does not exist. Fix it above, or empty it to install into the game folder.", icon="ERROR")
        elif own is not None:
            col.label(text=f"Items install into {own}", icon="CHECKMARK")
        elif folder is not None:
            col.label(text=f"Items install into the game folder: {folder}", icon="CHECKMARK")

        box = layout.box()
        box.label(text="Game build (used by the Blueprint panel)", icon="INFO")
        col = box.column()
        col.prop(self, "target_mod")

        problems = list(getattr(sys.modules.get(__package__), "ERRORS", ()))
        if problems:
            box = layout.box()
            box.label(text="Add-on parts that failed to load", icon="ERROR")
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
    """The optional Install folder setting as an existing folder, else None."""
    return _folder(get(context).test_game_dir)


def resolve_install_dir(context=None) -> tuple:
    """``(folder or None, source)`` of the folder ``gameinstall`` installs into: the Install folder when it is set and exists
    (source ``"install folder"``; None when it is set but does not exist), else the Game folder (:func:`resolve_game_dir`)."""
    if (get(context).test_game_dir or "").strip():
        return resolve_test_game_dir(context), "install folder"          # set but missing: None, never the game folder by mistake
    return resolve_game_dir(context)


CLASSES = (BZ_AP_prefs,)
