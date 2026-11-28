"""Share an item with other players: Pack for Sharing, and Install From Zip or Folder.

The work is ``ICParse.hub.share`` (read its text for what the zip holds and every rule it enforces).  This module is the buttons:

* ``bz.pack_for_sharing`` -- writes ``<item id>.zip`` of the item folder (the picked one, else the last export): the files, a README that
  says in plain words what it is and how to install it with and without this add-on, and, for a creature with a NEW name, the creature
  list a player of the unmodified game also needs (the creature creator offers only the animals named on its list;
  ``ICParse.hub.install.retail_roster`` has the whole story);
* ``bz.install_from_zip`` / ``bz.install_from_folder`` -- install an item someone else made: the zip (or folder) is read safely
  (nothing outside ``data/``, no path that climbs out of its folder, lower-case names), checked like any item, and installed into the
  game folder.  An item the check refuses is remembered by its source (not copied anywhere) so the Game panel can show why and offer Install
  Anyway (``bz.install_shared_anyway``, which reads the source again).

Items go into the same folder as Install into Game (:func:`gameinstall.target_folder`).
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import tempfile

import bpy
from bpy.props import BoolProperty, StringProperty

from .sceneobj import prefs, ui_kit

_PENDING: dict = {"source": "", "check": None}     # the zip or folder the check refused last, and its check (the Share box shows it, with Install Anyway)
_LAST: dict = {"zip": ""}


def _share():
    return importlib.import_module("ICParse.hub.share")


def available() -> bool:
    try:
        return importlib.util.find_spec("ICParse.hub.share") is not None
    except (ImportError, ValueError):
        return False


def _gi():
    return importlib.import_module(f"{__package__}.gameinstall")


def pending():
    """``(source, check)`` of the item the check refused last, else None."""
    return (_PENDING["source"], _PENDING["check"]) if _PENDING["source"] and _PENDING["check"] is not None else None


# ---- pack --------------------------------------------------------------------------------------------------------------------------
def pack(context, out_zip: str, anyway: bool = False, overwrite: bool = False):
    """Pack the item folder of the panel into ``out_zip``.  ``overwrite``: replace an older zip of that name (only ever a zip file).
    Returns ``(ok, text, notes)``."""
    if not available():
        return False, "Part of this add-on is out of date, so it cannot pack an item. Install the newest version of the add-on.", []
    gi = _gi()
    item = gi.item_folder(context)
    if not item or not os.path.isdir(item):
        return False, "No item folder yet: export the project for the game first, or pick an item folder in the Game panel.", []
    game = prefs.resolve_game_dir(context)[0] or gi.target_folder(context)       # the original game, read only: its own creature list
    sh = _share()
    try:
        res = sh.pack_item(item, bpy.path.abspath(out_zip), game=game, stock=gi._stock(game) if game else None, allow_problems=anyway,
                          overwrite=overwrite)
    except sh.ShareError as e:
        text = str(e)
        if "already exists" in text:
            text += " (The file window shows an existing name in red; pressing Pack for Sharing there replaces that zip.)"
        if "problems the game would refuse" in text:
            text += " Fix them and export again, or tick Pack anyway in the options of the file window (the panel at the right; press the small gear button at the top right if it is hidden) to pack it as it is."
        return False, text, []
    kb = os.path.getsize(res.zip_path) / 1024
    text = (f"Packed {len(res.files)} files into {res.zip_path} ({kb:,.0f} KB). Send that zip file to other players: its README tells them how "
            f"to install it, with or without this add-on.")
    return True, text, list(res.notes)


class BZ_OT_pack_for_sharing(bpy.types.Operator):
    """Write a zip of the exported item, with a README, that other players can install"""
    bl_idname = "bz.pack_for_sharing"
    bl_label = "Pack for Sharing"
    bl_options = {"REGISTER"}

    filepath: StringProperty(name="Zip file", subtype="FILE_PATH", default="")
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})
    check_existing: BoolProperty(default=True, options={"HIDDEN"})              # the file window marks a name that exists
    anyway: BoolProperty(name="Pack anyway", default=False,
                         description="Pack the item although the check found problems the game would refuse")

    @classmethod
    def poll(cls, context):
        item = _gi().item_folder(context)
        return bool(item) and os.path.isdir(item)

    def invoke(self, context, event):
        if not self.filepath.strip():
            item = _gi().item_folder(context)
            ident = _share().safe_item_id(os.path.basename(os.path.normpath(item))) if available() else "item"
            self.filepath = os.path.join(os.path.dirname(os.path.normpath(item)), ident + ".zip")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = self.filepath if self.filepath.lower().endswith(".zip") else self.filepath + ".zip"
        ok, text, notes = pack(context, path, self.anyway, overwrite=True)      # a name the modder picked in the file window (red when it exists): replace that zip
        if not ok:
            self.report({"ERROR"}, text)
            return {"CANCELLED"}
        _LAST["zip"] = path
        self.report({"INFO"}, text)
        for n in notes:
            self.report({"WARNING"}, n)
        return {"FINISHED"}


# ---- install from a zip or a folder ------------------------------------------------------------------------------------------------
def install_source(context, source: str, anyway: bool = False):
    """Read ``source`` (a zip file or a folder), stage it, and install it into the game folder.  Returns ``(ok, text, warnings, infos)``."""
    if not available():
        return False, "Part of this add-on is out of date, so it cannot read a shared item. Install the newest version of the add-on.", [], []
    gi = _gi()
    problem = gi.target_problem(context)
    if problem:
        return False, problem, [], []
    source = bpy.path.abspath(source)
    sh = _share()
    _PENDING.update(source="", check=None)
    work = tempfile.mkdtemp(prefix="icblender_shared_")
    try:
        staged = sh.stage_source(source, os.path.join(work, "item"))
    except sh.ShareError as e:
        shutil.rmtree(work, ignore_errors=True)
        return False, str(e), [], []
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        return False, f"The item could not be read: {e.strerror or e}.", [], []
    try:
        out = gi.install_folder(context, staged.item_dir, anyway)
    finally:
        shutil.rmtree(work, ignore_errors=True)                 # the staged copy is never kept: Install Anyway reads the source again
    if out.ok:
        return True, out.text, out.warnings, out.infos + staged.notes
    if out.check is not None and not out.check.ok and not anyway:
        _PENDING.update(source=source, check=out.check)         # the Game panel shows the problems and Install Anyway
    return False, out.text, [], []


def _anyway():
    return BoolProperty(name="Install anyway", default=False,
                        description="Install although the check found problems the game would refuse (the item may not work)")


class _InstallSource:
    @classmethod
    def poll(cls, context):
        return not _gi().target_problem(context)

    def _go(self, context, source):
        ok, text, warnings, infos = install_source(context, source, self.anyway)
        if not ok:
            self.report({"ERROR"}, text)
            return {"CANCELLED"}
        self.report({"INFO"}, text)
        from .sceneobj.ui_kit import plain
        for n in warnings:
            self.report({"WARNING"}, plain(n))
        for n in infos:
            self.report({"INFO"}, plain(n))
        return {"FINISHED"}


class BZ_OT_install_shared_anyway(_InstallSource, bpy.types.Operator):
    """Install the item the check refused, as it is (it may not work in the game)"""
    bl_idname = "bz.install_shared_anyway"
    bl_label = "Install Anyway"
    bl_options = {"REGISTER"}

    source: StringProperty(name="Zip or folder", default="", options={"HIDDEN"})
    anyway: BoolProperty(default=True, options={"HIDDEN"})

    def execute(self, context):
        if not self.source.strip():
            self.report({"ERROR"}, "Nothing to install: use Install From Zip or Install From Folder first.")
            return {"CANCELLED"}
        return self._go(context, self.source)


class BZ_OT_install_from_zip(_InstallSource, bpy.types.Operator):
    """Install an item someone else made (a zip made by Pack for Sharing) into the game folder"""
    bl_idname = "bz.install_from_zip"
    bl_label = "Install From Zip"
    bl_options = {"REGISTER"}

    filepath: StringProperty(name="Zip file", subtype="FILE_PATH", default="")
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})
    anyway: _anyway()

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.filepath.strip():
            self.report({"ERROR"}, "Choose a zip file.")
            return {"CANCELLED"}
        return self._go(context, self.filepath)


class BZ_OT_install_from_folder(_InstallSource, bpy.types.Operator):
    """Install an item someone else made (a folder with a data folder in it) into the game folder"""
    bl_idname = "bz.install_from_folder"
    bl_label = "Install From Folder"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="Item folder", subtype="DIR_PATH", default="")
    anyway: _anyway()

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.directory.strip():
            self.report({"ERROR"}, "Choose the item's folder (the one that holds the data folder).")
            return {"CANCELLED"}
        return self._go(context, self.directory)


# ---- the panel part -----------------------------------------------------------------------------------------------------------------
def draw_share(layout, context) -> None:
    """The Share box of the Game panel."""
    gi = _gi()
    box = layout.box()
    box.label(text="4. Share", icon="EXPORT")
    item = gi.item_folder(context)
    have = bool(item) and os.path.isdir(item)
    row = box.row()
    row.enabled = have
    row.operator(BZ_OT_pack_for_sharing.bl_idname, icon="PACKAGE")
    if not have:
        box.label(text="Export for the game first (step 2).", icon="INFO")
    elif _LAST["zip"]:
        box.label(text=f"Last zip: {os.path.basename(_LAST['zip'])}", icon="CHECKMARK")
    box.label(text="Install an item someone else made:")
    col = box.column(align=True)
    col.enabled = not gi.target_problem(context)
    col.operator(BZ_OT_install_from_zip.bl_idname, icon="FILE_ARCHIVE")
    col.operator(BZ_OT_install_from_folder.bl_idname, icon="FILE_FOLDER")
    held = pending()
    if held is not None:
        source, chk = held
        box.label(text=f"The check refused {os.path.basename(os.path.normpath(source))}:", icon="ERROR")
        for line in chk.lines(5):
            for part in ui_kit.wrap_lines(line, 62):
                box.label(text=part, icon="BLANK1")
        row = box.row()
        row.enabled = not gi.target_problem(context)
        row.operator(BZ_OT_install_shared_anyway.bl_idname, icon="ERROR").source = source


CLASSES = (BZ_OT_pack_for_sharing, BZ_OT_install_from_zip, BZ_OT_install_from_folder, BZ_OT_install_shared_anyway)


def unregister():
    _PENDING.update(source="", check=None)
