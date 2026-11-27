"""The start lane's Blender side: Relink Source (the Problems box), the stock-creature button at the top of the IC panel, the
project folder, and the Export Model guards.

Pure rules live in :mod:`startcheck` (no bpy); the clip rules in ``ICParse.sgm.creature.clips``.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty

from . import startcheck, store


# ---- where work is kept ----------------------------------------------------------------------------------------------------------
def user_projects_dir() -> str:
    """Blender's user data folder for files made while the .blend is unsaved (``pyic/projects``): persistent, unlike the temp folder."""
    try:
        return bpy.utils.user_resource("DATAFILES", path=os.path.join("pyic", "projects"), create=True) or ""
    except Exception:                                                      # noqa: BLE001
        return ""


def project_dir() -> str:
    """The folder next to the saved .blend, else Blender's user data ``pyic/projects``. Raises OSError when neither exists."""
    return startcheck.project_dir(bpy.data.filepath, user_projects_dir())


def project_path(stem: str, suffix: str = "", ext: str = ".sgm") -> str:
    """A path in :func:`project_dir` that nothing holds yet: ``<stem><suffix>.sgm``, then ``<stem><suffix>_2.sgm`` ..."""
    return startcheck.unique_path(project_dir(), startcheck.safe_stem(stem) + suffix, ext)


def stock_dirs(context=None) -> list:
    """Where the extracted stock creatures are looked for (the combiner's pool folders): the files in them are the game's animals."""
    from . import combocore, combopreview
    setting = ""
    scene = getattr(context or bpy.context, "scene", None)
    if scene is not None and hasattr(scene, "bz_combine"):
        setting = bpy.path.abspath(scene.bz_combine.stock_dir)
    return combocore.stock_dir_candidates(setting, [d for d in (combopreview.user_stock_dir(),) if d])


def default_export_path(obj, context=None) -> str:
    """Where Export Model suggests writing when the creature's source is one of the extracted stock creatures: ``<name>_edit.sgm`` in the
    project folder (never over the original). '' when the source is not one of them (the file browser's own default stands)."""
    src = store.source_path(obj) if obj is not None else ""
    if src and startcheck.in_dirs(src, stock_dirs(context)):
        try:
            return project_path(os.path.splitext(os.path.basename(src))[0], "_edit")
        except OSError:
            return ""
    return ""


# ---- Export Model guards ---------------------------------------------------------------------------------------------------------
def export_problems(obj, model) -> list:
    """The checks Export Model runs on the creature it is about to write (the cheap ones of Validate: no combine run): the load and
    stock-rulebook checks, the add-on's own structure checks and the clip names the game will not play. ``core.blocking`` of the
    result are the lines that stop the creature loading."""
    from . import core
    problems = list(core.validate_model(model))
    problems += core.check_structure(store.limb_names(obj), store.patch_limb(obj), store.connections(obj),
                                     store.bone_names(obj), store.limb_ids(obj))
    problems += startcheck.clip_lines(model, store.source_path(obj))
    return problems


def count_text(problems) -> str:
    """``N stock-rulebook gaps, M warnings`` for an export report (what Validate lists in full)."""
    stock = sum(1 for p in problems if p.startswith("STOCK"))
    warn = sum(1 for p in problems if p.startswith("WARN"))
    return f"{warn} warning{'s' if warn != 1 else ''}, {stock} thing{'s' if stock != 1 else ''} every game creature has and this one lacks"


# ---- Relink Source -------------------------------------------------------------------------------------------------------------------
class BZ_OT_relink_source(bpy.types.Operator):
    """Point this creature at its source .sgm again, after the file was moved, renamed or changed"""
    bl_idname = "bz.relink_source"
    bl_label = "Relink Source"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_glob: StringProperty(default="*.sgm;*.ebp", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def invoke(self, context, event):
        src = store.source_path(context.active_object)
        self.filepath = src if src else ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import core, guards
        obj = context.active_object
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "Choose the creature's source .sgm file")
            return {"CANCELLED"}
        verdict, why = startcheck.relink_verdict(obj.get(store.KEY_SRC_SHA1), int(obj.get(store.KEY_SRC_NVERTS, 0)),
                                                 int(obj.get(store.KEY_SRC_NPATCH, 0)), path)
        if verdict == "refuse":
            self.report({"ERROR"}, f"Not relinked: {why}")
            return {"CANCELLED"}
        obj[store.KEY_SOURCE] = core.resolve_source(path)
        core.clear_source_cache()
        if verdict == "changed":
            store.stamp_source(obj, obj[store.KEY_SOURCE])        # the object is now built on THIS file: its identity follows
            self.report({"WARNING"}, f"Relinked to {os.path.basename(path)}, but {why}. Check Validate before you export")
        else:
            self.report({"INFO"}, f"Relinked to {os.path.basename(path)}: {why}")
        guards.forget(obj.name)
        return {"FINISHED"}


# ---- Load Skin Image ------------------------------------------------------------------------------------------------------------------
_SKIN_ITEMS: list = []


def _skin_items(self, context):
    """The creature's skins as the list shows them: texture index and the image that holds its pixels."""
    obj = context.active_object
    items = []
    if obj is not None:
        for i, name in enumerate(store.texture_images(obj)):
            if name:
                items.append((str(i), f"{i}: {name}", "The picture Export writes into this texture", "", i))
    _SKIN_ITEMS[:] = items or [("0", "(no skin)", "", "", 0)]
    return _SKIN_ITEMS


class BZ_OT_load_skin_image(bpy.types.Operator):
    """Use a picture file (PNG, JPEG, TGA, BMP) as the creature's skin: Export writes it into the texture"""
    bl_idname = "bz.load_skin_image"
    bl_label = "Load Skin Image"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})
    filter_image: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})
    skin: bpy.props.EnumProperty(name="Skin", items=_skin_items, description="Which of the creature's textures the picture replaces")
    resize: BoolProperty(name="Resize To The Skin", default=True,
                         description="Scale the picture to the skin's size when it is another size (the creature's UVs stay valid); "
                                     "off refuses a picture of another size")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return store.is_native(obj) and bool(store.texture_images(obj))

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import numpy as np
        obj = context.active_object
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, "Choose the picture file to use as the skin")
            return {"CANCELLED"}
        index = int(self.skin)
        target, err = store.texture_image(obj, index)
        if err or target is None:
            self.report({"ERROR"}, err or "This creature has no skin image to replace")
            return {"CANCELLED"}
        w, h = target.size
        try:
            src = bpy.data.images.load(path, check_existing=False)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Cannot read {os.path.basename(path)} as a picture: {e}")
            return {"CANCELLED"}
        try:
            sw, sh = src.size
            if (sw, sh) != (w, h):
                if not self.resize:
                    self.report({"ERROR"}, f"{os.path.basename(path)} is {sw}x{sh}; the skin is {w}x{h}. Resize the picture or tick "
                                           "'Resize To The Skin'")
                    return {"CANCELLED"}
                src.scale(w, h)
            buf = np.empty(w * h * 4, dtype=np.float32)
            src.pixels.foreach_get(buf)
            buf[3::4] = 1.0                                  # the skin has no alpha: a transparent corner would paint black
            target.pixels.foreach_set(buf)
            target.update()
        finally:
            bpy.data.images.remove(src)
        self.report({"INFO"}, f"Loaded {os.path.basename(path)} into skin '{target.name}'"
                              + (f" (scaled from {sw}x{sh} to {w}x{h})" if (sw, sh) != (w, h) else "")
                              + ": Export writes it into the texture")
        return {"FINISHED"}


def draw_problem_extras(layout, issue) -> None:
    """What a Problems box line can offer beside its words: the source problem offers Relink Source."""
    if issue.code == "source":
        layout.operator(BZ_OT_relink_source.bl_idname, icon="FILE_REFRESH")


def draw_start_buttons(layout) -> None:
    """The buttons a fresh user needs before any creature exists: the game's animals as files (no object needed)."""
    from . import combopreview
    layout.operator(combopreview.BZ_OT_combine_extract_stock.bl_idname, icon="IMPORT")


CLASSES = (BZ_OT_relink_source, BZ_OT_load_skin_image)
