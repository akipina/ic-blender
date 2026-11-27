"""Add-on preferences. Slices read them through these functions, never ``bpy.context`` directly, so the defaults apply
when the add-on is not enabled (headless tests).

Every setting here is read by something: Game folder (where the game's own files are read from), Test game folder (the copy of the
game "Install into Test Game" writes to, ``gameinstall.py``), Allow the Steam game folder (the explicit box that lets that install
write to a Steam library folder), and Game build (the kind numbers the Blueprint panel knows). The settings an earlier version listed
as "not used yet" (Build folder, Mods folder, Damage states, Texture type, Replace game files, Selection box) did nothing and were removed:
the export asks for its folder each time, a texture's type follows its alpha, and the replace-or-new-name choice is made in the export.
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
        name="Test game folder", subtype="DIR_PATH", default=DEFAULTS["test_game_dir"],
        description="A COPY of the game where finished items are installed for testing (it must hold Engine.sga). Install into Test Game "
                    "writes here and Remove takes it out again; the original game is never written")
    allow_steam_install: BoolProperty(
        name="Allow the Steam game folder", default=DEFAULTS["allow_steam_install"],
        description="Let Install into Test Game write to a game folder inside a Steam library, or to the Game folder set above (the "
                    "original game). Steam rewrites its folder (verifying files, updates, the Workshop) and would wipe the item. "
                    "Leave this off and use a copy")
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
        box.label(text="Test game (where Install into Test Game writes)", icon="PLAY")
        col = box.column()
        col.prop(self, "test_game_dir")
        col.prop(self, "allow_steam_install")
        col.operator("bz.create_test_game", icon="DUPLICATE")                # r2install: makes the copy and sets the folder above
        test = resolve_test_game_dir(context)
        if test is None:
            col.label(text="Not set: Install into Test Game is off.", icon="INFO")
        elif is_steam_folder(test) and not self.allow_steam_install:
            ui_kit.alert(col, "This is a Steam game folder: Install is refused. Use a copy of the game, or tick the box above.", icon="ERROR")
        elif folder is not None and _same_folder(test, folder) and not self.allow_steam_install:
            ui_kit.alert(col, "This is the Game folder above, the original game: Install is refused. Use a copy of the game, or tick the box above.", icon="ERROR")
        else:
            col.label(text=f"Installs go to {test}", icon="CHECKMARK")

        box = layout.box()
        box.label(text="Game build (used by the Blueprint panel)", icon="INFO")
        col = box.column()
        col.prop(self, "target_mod")

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
    """The Test game folder setting as an existing folder, else None (the folder ``gameinstall`` installs into)."""
    return _folder(get(context).test_game_dir)


def _same_folder(a: str, b: str) -> bool:
    import os
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))      # no case on Windows: C:\\Games and c:\\games are one folder


def is_steam_folder(folder: str) -> bool:
    """Whether ``folder`` is a Steam library's game folder (or the one the add-on reads the game from): see ICParse.hub.install."""
    try:
        from ICParse.hub.install import is_steam_folder as check
    except ImportError:
        return False
    return check(folder)


CLASSES = (BZ_AP_prefs,)
