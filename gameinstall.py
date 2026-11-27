"""The Game panel: export for the game, the test copy, install into it, take items out again, and share.

The item folder is what the "for the game" exports write (``<out>/<item_id>/data/...``; the export stores it on the scene as
``scene["bz_last_item_dir"]``). The work is ICParse's :mod:`ICParse.hub.install`, which copies the item over the game folder the way
retail installs a Workshop item -- loose files in ``Data\\`` beat the archives (``App/SigmaApp.cpp:2764``, see that module's text for
every rule and its decompiled source line) -- keeps what it overwrites so **Remove** puts every byte back, writes retail's uninstall
list, puts aside the game's cached mesh of every model the item carries (Remove puts it back), and adds a new creature's name to the creature
creator list of every mod (each mod's own list, written in that mod's folder: ``ICParse.hub.install``).

This module is the buttons and the panel:

* ``bz.install_item`` -- "Install into Test Game": the picked item folder (else the last export) into the *Test game folder* of the
  add-on's preferences.  **It checks the item first** (:mod:`ICParse.hub.preinstall`: the hub's own validator in plain words, and a basic
  check of its own for effect items, which the hub has no kind for) and refuses an item the game would refuse, with the reasons;
  ``anyway=True`` ("Install Anyway" in the panel) installs it as it is;
* ``bz.remove_item`` -- "Remove" beside an installed item;
* ``BZ_PT_game`` -- the "Game" panel in the IC Game tab: which game copy, what is installed, the last export and the NEXT STEP when something
  is missing (:func:`next_step`), the export buttons (``gameexport.draw_game_export``), the test copy (``gamecopyops``), sharing
  (``gameshare``) and the items installed.

The target is never the Steam game folder unless the preference "Allow the Steam game folder" is ticked: Steam rewrites that folder.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import time

import bpy
from bpy.props import BoolProperty, StringProperty

from .sceneobj import prefs, ui_kit
from . import gamecopyops, gameshare           # Create Test Game Copy, Pack for Sharing / Install From Zip (their own modules; their classes are registered with ours)

KEY_LAST_ITEM = "bz_last_item_dir"          # scene["bz_last_item_dir"]: the item folder of the last "for the game" export (the contract)
_STATE_CACHE: dict = {}                      # test game folder -> (stamp, [InstalledItem]) so a redraw does not read the folder each time
_LAST_CHECK: dict = {"folder": "", "check": None}      # the pre-install check that refused the item picked now (the panel shows it, with Install Anyway)


def _install():
    """ICParse's installer (imported late: the add-on must still load when an old ICParse is around; the panel says so)."""
    return importlib.import_module("ICParse.hub.install")


def installer_available() -> bool:
    try:
        return importlib.util.find_spec("ICParse.hub.install") is not None
    except (ImportError, ValueError):
        return False


# ---- what the buttons act on -------------------------------------------------------------------------------------------------------
def test_game_folder(context=None):
    """The Test game folder preference as an existing folder, else None."""
    return prefs.resolve_test_game_dir(context)


def allow_steam(context=None) -> bool:
    return bool(getattr(prefs.get(context), "allow_steam_install", False))


def protected_folders(context=None) -> list:
    """The folders that are the original game (the add-on's Game folder setting): never an install target without the explicit box."""
    try:
        folder = prefs.resolve_game_dir(context)[0]
    except Exception:                                  # a preferences page that cannot answer must not stop the install guard
        folder = None
    return [folder] if folder else []


def target_problem(context=None):
    """Why nothing can be installed now (plain words), else None."""
    if not installer_available():
        return "Part of this add-on is out of date, so it cannot install. Install the newest version of the add-on."
    folder = test_game_folder(context)
    raw = (getattr(prefs.get(context), "test_game_dir", "") or "").strip()
    if folder is None:
        if raw:
            return f"The Test game folder {raw} does not exist. Fix it in the add-on's preferences."
        return "No Test game folder is set. Make a copy of the game and set it in the add-on's preferences (Test game folder)."
    return _install().game_folder_problem(folder, allow_steam(context), protected_folders(context))


def project_holder(obj):
    """The object that carries a project's own record of its last export: the creature (also when its rig is active) or the scene object's
    holder; None when ``obj`` is neither."""
    if obj is None:
        return None
    from . import store
    c = store.creature_of(obj)
    if c is not None:
        return c
    if ui_kit.is_scene_object(obj):
        pb = _optional("sceneobj.panels_blueprint")
        ent = pb.entity_of(obj) if pb is not None else None
        if ent is not None:
            return ent.holder
    return None


def project_item(context) -> str:
    """The folder of the active project's OWN last export for the game ('' when it was never exported). With no project active (nothing
    selected, a map), the last export of the scene."""
    holder = project_holder(getattr(context, "active_object", None))
    if holder is not None:
        last = holder.get(KEY_LAST_ITEM) or ""
    else:
        last = context.scene.get(KEY_LAST_ITEM) or ""
    return bpy.path.abspath(last) if last else ""


def item_folder(context) -> str:
    """The item folder to install: the one picked in the panel, else the active project's own last export (never another project's)."""
    scene = context.scene
    raw = (getattr(scene, "bz_item_dir", "") or "").strip()
    return bpy.path.abspath(raw) if raw else project_item(context)


def installed(game: str) -> list:
    """The items installed in ``game`` (cached until the folder changes)."""
    try:
        stamp = (os.stat(game).st_mtime_ns, os.stat(os.path.join(game, _install().STATE_DIR)).st_mtime_ns
                 if os.path.isdir(os.path.join(game, _install().STATE_DIR)) else 0)
    except OSError:
        return []
    hit = _STATE_CACHE.get(game)
    if hit and hit[0] == stamp:
        return hit[1]
    items = _install().installed_items(game)
    _STATE_CACHE[game] = (stamp, items)
    return items


# ---- the check and the install --------------------------------------------------------------------------------------------------------
def _stock(game):
    """The hub's index of the game's archives (cached), or None."""
    try:
        from . import scenecheck
        return scenecheck.stock_index(game)
    except Exception:                                  # noqa: BLE001 -- no index: the checks that need the game's tables are skipped
        return None


def check_folder(folder: str, game=None):
    """The pre-install check of an item folder (:func:`ICParse.hub.preinstall.check_for_install`), or None when ICParse is too old."""
    try:
        pre = importlib.import_module("ICParse.hub.preinstall")
    except ImportError:
        return None
    return pre.check_for_install(folder, _stock(game) if game else None)


class Outcome:
    """What :func:`install_folder` did: ``ok``, the ``text`` to tell the modder, ``warnings`` (more lines), the ``check`` that ran."""

    def __init__(self, ok, text, warnings=(), check=None, result=None, infos=()):
        self.ok, self.text, self.warnings, self.check, self.result, self.infos = ok, text, list(warnings), check, result, list(infos)


def install_folder(context, folder: str, anyway: bool = False) -> Outcome:
    """Check ``folder`` and install it into the Test game folder.  A refused item is not installed unless ``anyway``; the check is
    kept for the panel (:func:`last_check`)."""
    problem = target_problem(context)
    if problem:
        return Outcome(False, problem)
    if not folder or not os.path.isdir(folder):
        return Outcome(False, "No item folder yet: export the project for the game first, or pick an item folder in the Game panel.")
    game = test_game_folder(context)
    mod = _install()
    chk = check_folder(folder, game)
    _LAST_CHECK.update(folder=folder, check=chk)
    if chk is not None and not chk.ok and not anyway:
        shown = chk.lines(3)
        return Outcome(False, "The game would not take this item as it is, so nothing was installed. " + " ".join(shown) +
                       " Fix this and export again, or press Install Anyway in the Game panel to install it as it is.", check=chk)
    try:
        res = mod.install_item(folder, game, allow_steam=allow_steam(context), protect=protected_folders(context))
    except mod.InstallError as e:
        return Outcome(False, str(e), check=chk)
    except OSError as e:                              # a full disk, a file in use: the install undid itself where it could
        _STATE_CACHE.pop(game, None)
        return Outcome(False, f"The install stopped: {e}. Anything already written was taken back; press Remove if the item is listed.", check=chk)
    _STATE_CACHE.pop(game, None)
    _LAST_CHECK.update(folder="", check=None)
    bits = [f"{len(res.files)} files"]
    if res.replaced:
        bits.append(f"{len(res.replaced)} replaced (Remove puts them back)")
    if res.cache_cleared:
        bits.append(f"{len(res.cache_cleared)} saved model(s) of the game put aside (the game builds them again; Remove puts them back)")
    if res.roster_added:
        bits.append("added to the creature creator (" + ", ".join(res.roster_mods) + "): " + ", ".join(res.roster_added))
    text = (f"Installed '{res.item_id}' into {game}: " + "; ".join(bits) +
            ". Start the game from that folder (a game that is already running must be restarted).")
    if chk is not None and not chk.ok:
        text += " It was installed although the check found problems (Install Anyway)."
    extra = list(res.notes) + ([f"Advice: {w}" for w in chk.warnings[:4]] if chk is not None else [])
    return Outcome(True, text, extra, chk, res, chk.notes[:2] if chk is not None else ())


def last_check(folder: str):
    """The refused check of ``folder`` that the panel shows, else None."""
    c = _LAST_CHECK.get("check")
    return c if c is not None and _LAST_CHECK.get("folder") == folder and not c.ok else None


# ---- operators -----------------------------------------------------------------------------------------------------------------------
class BZ_OT_install_item(bpy.types.Operator):
    """Check the exported item, then copy it into the Test game folder, as the game's own Workshop installer would"""
    bl_idname = "bz.install_item"
    bl_label = "Install into Test Game"
    bl_options = {"REGISTER"}

    item_dir: StringProperty(name="Item folder", subtype="DIR_PATH", default="",
                             description="The item folder (it holds data/...); empty uses the folder picked in the panel, else the last export")
    anyway: BoolProperty(name="Install anyway", default=False,
                         description="Install although the check found problems the game would refuse (the item may not work)")

    def execute(self, context):
        folder = bpy.path.abspath(self.item_dir) if self.item_dir.strip() else item_folder(context)
        out = install_folder(context, folder, self.anyway)
        if not out.ok:
            self.report({"ERROR"}, out.text)
            return {"CANCELLED"}
        self.report({"INFO"}, out.text)
        for note in out.warnings:
            self.report({"WARNING"}, note)
        for note in out.infos:
            self.report({"INFO"}, note)
        return {"FINISHED"}


class BZ_OT_remove_item(bpy.types.Operator):
    """Take an installed item out of the Test game folder and put back what it replaced"""
    bl_idname = "bz.remove_item"
    bl_label = "Remove"
    bl_options = {"REGISTER"}

    item_id: StringProperty(name="Item", default="", description="The installed item to remove")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title=f"Remove '{self.item_id}' from the Test game?",
                                                     message="Its files are deleted and every file it replaced is put back.")

    def execute(self, context):
        problem = target_problem(context)
        if problem:
            self.report({"ERROR"}, problem)
            return {"CANCELLED"}
        game = test_game_folder(context)
        mod = _install()
        try:
            res = mod.remove_item(game, self.item_id, allow_steam=allow_steam(context), protect=protected_folders(context))
        except mod.InstallError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except OSError as e:
            _STATE_CACHE.pop(game, None)
            self.report({"ERROR"}, f"The removal stopped: {e}. Press Remove again once the file is free.")
            return {"CANCELLED"}
        _STATE_CACHE.pop(game, None)
        self.report({"INFO"}, f"Removed '{res.item_id}': {len(res.deleted)} files deleted, {len(res.restored)} put back as they were.")
        for note in res.notes:
            self.report({"WARNING"}, note)
        return {"FINISHED"}


class BZ_OT_open_preferences(bpy.types.Operator):
    """Open this add-on's preferences (Test game folder)"""
    bl_idname = "bz.open_game_preferences"
    bl_label = "Set Test Game Folder"

    def execute(self, context):
        try:
            bpy.ops.preferences.addon_show(module=prefs.ADDON_ID)
        except RuntimeError as e:
            self.report({"WARNING"}, f"Open Edit > Preferences > Add-ons > IC Blender yourself ({e})")
        return {"FINISHED"}


# ---- the panel ------------------------------------------------------------------------------------------------------------------------
def export_time(item: str):
    """When the item folder was last exported: the newest file time under its ``data/`` folder (a folder's own time moves only when a direct
    child is added or removed, not when an export rewrites files two levels down), else the folder's own time; None when unreadable."""
    newest = None
    try:
        for dp, _dns, fns in os.walk(os.path.join(item, "data")):
            for n in fns:
                try:
                    t = os.stat(os.path.join(dp, n)).st_mtime
                except OSError:
                    continue
                if newest is None or t > newest:
                    newest = t
        if newest is None:
            newest = os.path.getmtime(item)
    except OSError:
        return None
    return newest


def _when(path: str) -> str:
    t = export_time(path)
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t is not None else ""


def copy_info(folder):
    """What the add-on knows about a test copy it made (``ICParse.hub.gamecopy.test_copy_info``), else None."""
    try:
        return importlib.import_module("ICParse.hub.gamecopy").test_copy_info(folder) if folder else None
    except ImportError:
        return None


def next_step(context) -> str:
    """The one next thing to do, in a plain sentence, from the first thing that is missing: a game folder, a test copy, an export, an
    install, then starting the game.  '' when nothing is missing and the item is installed."""
    folder = test_game_folder(context)
    if folder is None:
        if gamecopyops.source_folder(context) is None:
            return ("Next: tell the add-on where your game is. Open Edit > Preferences > Add-ons > IC Blender and set the Game folder "
                    "to the folder that holds IC.exe.")
        return "Next: press Create Test Game Copy. It makes your own copy of the game to try your work in."
    if target_problem(context):
        return ("Next: fix the Test game folder above: it cannot be used. Set it in Edit > Preferences > Add-ons > IC Blender, "
                "or press Create Test Game Copy to make a new one.")
    item = item_folder(context)
    if not item or not os.path.isdir(item):
        return f"Next: export your project for the game ({_export_button(context)}, step 2 above). Nothing has been exported yet."
    name = os.path.basename(os.path.normpath(item))
    if name not in {i.item_id for i in installed(folder) if not i.foreign}:
        return f"Next: press Install into Test Game to put '{name}' into your test copy."
    start = (f"Next: start the game from {folder}: run IC.exe there. If the game is already running, close it and start it again "
             f"to see the change.")
    hidden = _new_name_words(context, item)
    return f"{start} {hidden}" if hidden else start


def _export_button(context) -> str:
    """What to press in step 2 for the selected thing: a creature, a scene object, or (nothing selected) a way to choose."""
    obj = getattr(context, "active_object", None)
    if ui_kit.is_scene_object(obj):
        return "press Export Entity Folder"
    store = _optional("store")
    if store is not None and store.creature_of(obj) is not None:
        return "press Export Creature for the Game"
    return "select your creature, gatherer, character, structure or prop, then export it"


def _new_name_words(context, item: str) -> str:
    """For a structure, prop, gatherer or character exported under a name of its own: the game shows it only where a map or a script names it,
    so a modder who starts the game and finds nothing is told so (and how to see it now). '' for anything else."""
    obj = getattr(context, "active_object", None)
    holder = project_holder(obj)
    if holder is None or not ui_kit.is_scene_object(obj) or holder.get(KEY_LAST_ITEM) is None:
        return ""
    if os.path.normpath(bpy.path.abspath(str(holder.get(KEY_LAST_ITEM)))) != os.path.normpath(item) or holder.get("bz_last_item_mode") != "NEW":
        return ""
    return ("Your thing has a name of its own, so nothing in the game shows it until a map or a script names it. To see it now, export again "
            "with Replace the original (the game's own thing then looks like your model), or place it in a map that names it.")


def draw_check_step(layout, context) -> None:
    """Step 1 of the Game panel: Check. A creature gets the Check button and what the last check found; a scene object (gatherer,
    character, structure, prop) the Check button and Problems box of its own (``sceneobj.panels_check``); with nothing selected it says
    what to select."""
    box = layout.box()
    box.label(text="1. Check", icon="CHECKMARK")
    obj = getattr(context, "active_object", None)
    if ui_kit.is_scene_object(obj):
        panels_check = _optional("sceneobj.panels_check")
        if panels_check is not None:
            panels_check.draw_check(box, obj)
        return
    store = _optional("store")
    creature = store.creature_of(obj) if store is not None else None
    if creature is not None:                                  # the creature, or its rig (the rig is what is selected while animating)
        obj = creature
        box.operator("bz.validate", text="Check the Creature", icon="CHECKMARK")
        if not store.connections(obj):
            ui_kit.alert(box, "This creature has no connection rings, so the creature creator cannot use it and Export for the Game refuses it. "
                              "Use Retarget Creature to Mesh (IC Model tab, Tools) to get a creature with rings.", width=62)
        found = obj.get("bz_validation")
        if found is None:
            ui_kit.alert(box, "Not checked yet. Check shows what would stop the game from loading the creature.", icon="INFO", width=62)
            return
        found = [str(p) for p in found]
        core = _optional("core")
        blocking = core.blocking(found) if core is not None else []
        warns = [p for p in found if p not in blocking and not p.startswith("COMBINE INFO")]
        if not blocking and not warns:
            box.label(text="The last check found no problems.", icon="CHECKMARK")
        for p in blocking[:3]:
            ui_kit.alert(box, p, width=62)
        if len(blocking) > 3:
            box.label(text=f"... and {len(blocking) - 3} more problems.")
        if warns:
            box.label(text=f"{len(warns)} warning(s): the Info editor lists them after Check.", icon="INFO")
        if blocking:
            box.label(text="Fix these first: Export refuses a creature the game would not load.", icon="ERROR")
        return
    ui_kit.alert(box, "Select your creature, gatherer, character, structure or prop to check it.", icon="INFO", width=62)


def draw_game(layout, context) -> None:
    """The contents of the Game panel, in the order a modder works: 1. Check, 2. Export for the Game, 3. Install into Test Game, 4. Share
    (a function so a test can draw it into a fake layout)."""
    draw_check_step(layout, context)
    gameexport = _optional("gameexport")
    if gameexport is not None and hasattr(gameexport, "draw_game_export"):
        gameexport.draw_game_export(layout, context)
    obj = getattr(context, "active_object", None)
    if ui_kit.is_scene_object(obj):
        ebox = layout.box()                                  # a scene object (gatherer, structure, prop, effect model): its export is the item-folder one
        ebox.label(text="2. Export for the Game", icon="PACKAGE")
        ebox.operator("bz.export_entity_folder", text="Export Entity Folder", icon="EXPORT")
        own = obj and project_item(context)
        if own:
            ebox.label(text=f"Last export: {os.path.basename(os.path.normpath(own))}", icon="CHECKMARK")
        else:
            ebox.label(text="Not exported yet.", icon="INFO")
    elif project_holder(obj) is None:
        ebox = layout.box()                                  # nothing to export is selected: the step is still there, so "step 2" in the Next line has a place
        ebox.label(text="2. Export for the Game", icon="PACKAGE")
        ui_kit.alert(ebox, "Select your creature, gatherer, character, structure or prop to export it.", icon="INFO", width=62)

    box = layout.box()
    box.label(text="3. Install into Test Game", icon="PLAY")
    problem = target_problem(context)
    folder = test_game_folder(context)
    gamecopyops.draw_copy(box, context)
    item = item_folder(context)
    have_item = bool(item) and os.path.isdir(item)

    # which game copy
    if folder is None:
        ui_kit.alert(box, problem)
        box.operator(gamecopyops.BZ_OT_create_test_game.bl_idname, icon="DUPLICATE")
        box.operator(BZ_OT_open_preferences.bl_idname, icon="PREFERENCES")
    else:
        box.label(text=folder, icon="FILE_FOLDER")
        info = copy_info(folder)
        if info is not None and not info.get("complete", True):
            ui_kit.alert(box, "This copy was not finished (the copy was interrupted). Delete the folder and press Create Test Game Copy again.")
        elif info is not None:
            box.label(text=f"A copy of the game made here on {str(info.get('made', ''))[:10]}")
        elif not problem:
            box.label(text="A folder you set yourself.")
        if problem:
            ui_kit.alert(box, problem)
            box.operator(gamecopyops.BZ_OT_create_test_game.bl_idname, icon="DUPLICATE")

    # the last export, and what Install would take
    box.prop(context.scene, "bz_item_dir", text="Item folder")
    if have_item:
        shown = os.path.basename(os.path.normpath(item))
        box.label(text=f"Installs: {shown}", icon="PACKAGE")
        when = _when(item)
        if when:
            box.label(text=f"Last export: {when}", icon="TIME")
    else:
        box.label(text="Last export: none yet.", icon="INFO")

    # the check that refused the item, with the way on
    refused = last_check(item) if have_item and folder is not None else None
    if refused is not None:
        box.label(text="The check found problems the game would refuse:", icon="ERROR")
        for line in refused.lines(5):
            for part in ui_kit.wrap_lines(line, 62):
                box.label(text=part, icon="BLANK1")
        row = box.row()
        row.enabled = not problem
        row.operator(BZ_OT_install_item.bl_idname, text="Install Anyway", icon="ERROR").anyway = True
    if folder is not None:
        row = box.row()
        row.enabled = not problem and have_item
        row.operator(BZ_OT_install_item.bl_idname, icon="IMPORT")

    step = next_step(context)
    if step:
        col = box.column(align=True)
        for k, part in enumerate(ui_kit.wrap_lines(step, 62)):
            col.label(text=part, icon="FORWARD" if k == 0 else "BLANK1")

    items = installed(folder) if folder is not None and not problem else []
    mine = [i for i in items if not i.foreign]
    other = [i for i in items if i.foreign]
    if folder is not None and not problem:
        inst = layout.box()
        inst.label(text=f"Installed here: {len(mine)}", icon="PACKAGE")
        for it in mine:
            row = inst.row(align=True)
            what = ", ".join(it.creatures[:3]) if it.creatures else f"{it.files} files"
            row.label(text=f"{it.item_id}  ({what})")
            row.operator(BZ_OT_remove_item.bl_idname, text="Remove", icon="TRASH").item_id = it.item_id
        if other:
            inst.label(text=f"{len(other)} more installed by other tools (not touched here)", icon="INFO")
    gameshare.draw_share(layout, context)


def _optional(name: str):
    if importlib.util.find_spec(f"{__package__}.{name}") is None:
        return None
    return importlib.import_module(f"{__package__}.{name}")


class BZ_PT_game(bpy.types.Panel):
    """Export for the game, install into the Test game folder, and see what is installed"""
    bl_label = "Game"
    bl_idname = "BZ_PT_game"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 60             # the last panel of the IC Game tab: check, export, install, share

    def draw(self, context):
        draw_game(self.layout, context)


CLASSES = (BZ_OT_install_item, BZ_OT_remove_item, BZ_OT_open_preferences, *gamecopyops.CLASSES, *gameshare.CLASSES, BZ_PT_game)


def register():
    bpy.types.Scene.bz_item_dir = StringProperty(
        name="Item folder", subtype="DIR_PATH", default="",
        description="The item folder to install (it holds data/...). Empty: the folder of the last export for the game")


def unregister():
    gameshare.unregister()
    try:
        del bpy.types.Scene.bz_item_dir
    except AttributeError:
        pass
