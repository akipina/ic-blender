"""Create Test Game Copy: a button that makes a private copy of the game to try items in.

The work is ``ICParse.hub.gamecopy`` (read its text for what is linked, what is copied and why the copy is never in a Steam library).
This module is the button, the dialog that says what the copy will take on disk BEFORE it starts, the progress while it runs, and the
preference it sets: the new folder becomes the add-on's **Test game folder**, so Install into Test Game works at once.

* ``bz.create_test_game`` -- the dialog and the start; the copy runs in slices from a timer (Blender stays usable, the status bar shows
  progress) and ``bz.cancel_test_game`` stops it and removes what it made.  Called with ``wait=True`` (scripts, tests, background
  Blender) it does everything inside the call.
* :func:`draw_copy` -- the lines the Game panel shows about it (progress, the last result).

It never writes inside a Steam library (``ICParse.hub.gamecopy.dest_problem``) and never into the original game.
"""
from __future__ import annotations

import importlib
import importlib.util
import os

import bpy
from bpy.props import BoolProperty, StringProperty

from .sceneobj import prefs, ui_kit

_STATE: dict = {"job": None, "dest": "", "message": "", "level": "INFO", "wm": None}
_PLAN_CACHE: dict = {}                       # (source, folder, everything) -> the plan the dialog shows (a redraw must not scan the game again)


def _lib():
    """ICParse's copy module (late import: the add-on loads with an old ICParse; the button then says so)."""
    return importlib.import_module("ICParse.hub.gamecopy")


def available() -> bool:
    try:
        return importlib.util.find_spec("ICParse.hub.gamecopy") is not None
    except (ImportError, ValueError):
        return False


def source_folder(context=None):
    """The original game the copy is made from: the add-on's Game folder (setting, else IC_INSTALL, else the usual Steam folder)."""
    return prefs.resolve_game_dir(context)[0]


def default_folder() -> str:
    return _lib().default_destination()


def folder_text(raw: str) -> str:
    """A folder property as an absolute path ('' when empty); a ``//`` Blender-relative path is made absolute."""
    raw = (raw or "").strip()
    return os.path.abspath(bpy.path.abspath(raw)) if raw else ""


def make_plan(context, dest: str, everything: bool = False, cached: bool = True):
    """The plan for a copy into ``dest``, or ``(None, why)`` when there is no game to copy.  ``cached``: the dialog's redraws reuse the
    plan while nothing changed; the copy itself always plans afresh (the folder may have changed since the dialog)."""
    src = source_folder(context)
    if src is None:
        return None, prefs.NO_GAME
    key = (src, dest, everything)
    if not cached or key not in _PLAN_CACHE:
        _PLAN_CACHE.clear()
        _PLAN_CACHE[key] = _lib().plan_copy(src, dest, "never" if everything else "auto")
    return _PLAN_CACHE[key], None


# ---- running -------------------------------------------------------------------------------------------------------------------------
def busy() -> bool:
    return _STATE["job"] is not None


def _say(level: str, text: str) -> None:
    _STATE["level"], _STATE["message"] = level, text
    print(f"IC Blender: {text}", flush=True)


def _redraw() -> None:
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW3D":
                    area.tag_redraw()
    except (AttributeError, RuntimeError):
        pass


def _set_test_folder(dest: str) -> bool:
    """Make ``dest`` the Test game folder preference; False when the add-on's preferences are not live (nothing to set)."""
    live = prefs.get()
    if not isinstance(live, bpy.types.AddonPreferences):
        return False
    live.test_game_dir = dest
    return True


def _finish_ok(job) -> str:
    res = job.result
    lib = _lib()
    saved = _set_test_folder(job.plan.dest)
    parts = [f"{res.files_copied:,} files copied ({lib.describe_size(res.bytes_copied)})"]
    if res.files_linked:
        parts.append(f"{res.files_linked:,} big files linked to the original")
    text = (f"The test copy is ready in {job.plan.dest}: " + ", ".join(parts) + ". " +
            ("It is now your Test game folder: press Install into Test Game to put an item in it." if saved else
             "Set it as the Test game folder in the add-on's preferences (Edit > Preferences > Add-ons > IC Blender)."))
    for n in res.notes[:3]:
        text += f" ({n})"
    return text


def start(context, dest: str, everything: bool = False, wait: bool = False):
    """Start the copy into ``dest``.  Returns ``(ok, message)``.  ``wait``: do it all now (the message is the result), else it runs from
    a timer and the message says it started."""
    if not available():
        return False, "Part of this add-on is out of date, so it cannot make a test copy. Install the newest version of the add-on."
    if busy():
        return False, "A test copy is already being made. Wait for it, or press Cancel."
    plan, why = make_plan(context, dest, everything, cached=False)
    if plan is None:
        return False, why
    if plan.problem:
        return False, plan.problem
    if not plan.room:
        return False, " ".join(plan.summary())
    lib = _lib()
    job = lib.CopyJob(plan)
    if wait or bpy.app.background:
        try:
            job.run()
        except lib.CopyError as e:
            _say("ERROR", str(e))
            return False, str(e)
        text = _finish_ok(job)
        _say("INFO", text)
        return True, text
    _STATE.update(job=job, dest=dest)
    try:
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        _STATE["wm"] = wm
    except (AttributeError, RuntimeError):
        _STATE["wm"] = None
    _say("INFO", f"Making the test copy in {dest} ...")
    bpy.app.timers.register(tick, first_interval=0.05)
    return True, f"Started the test copy in {dest}. It runs in the background; the Game panel shows how far it is."


def tick():
    """One slice of the running copy (a timer calls this; returns the delay until the next, or None when it is over)."""
    job = _STATE["job"]
    if job is None:
        return None
    lib = _lib()
    try:
        done = job.run(0.05)
    except lib.CopyError as e:
        _end(None)
        _say("ERROR", str(e))
        return None
    wm = _STATE.get("wm")
    if wm is not None:
        try:
            wm.progress_update(int(1000 * job.done / job.total))
        except (AttributeError, RuntimeError):
            pass
    _redraw()
    if done:
        text = _finish_ok(job)
        _end(None)
        _say("INFO", text)
        return None
    return 0.02


def _end(_unused) -> None:
    wm = _STATE.get("wm")
    if wm is not None:
        try:
            wm.progress_end()
        except (AttributeError, RuntimeError):
            pass
    _STATE.update(job=None, wm=None)
    _PLAN_CACHE.clear()
    _redraw()


def cancel() -> str:
    """Stop the running copy and remove everything it made; returns what to tell the modder."""
    job = _STATE["job"]
    if job is None:
        return "No test copy is being made."
    job.cancel()
    dest = _STATE["dest"]
    _end(None)
    _say("WARNING", f"The test copy was cancelled and what it had made in {dest} was removed.")
    return _STATE["message"]


# ---- operators -----------------------------------------------------------------------------------------------------------------------
class BZ_OT_create_test_game(bpy.types.Operator):
    """Make a private copy of the game to test your items in, and use it as the Test game folder"""
    bl_idname = "bz.create_test_game"
    bl_label = "Create Test Game Copy"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="Put the copy in", subtype="DIR_PATH", default="",
                              description="A new or empty folder outside Steam. Empty uses 'IC Test Game' in your home folder")
    everything: BoolProperty(name="Copy everything (no links)", default=False,
                             description="Copy the big archives instead of linking to them. Slower and about 5 GB larger, "
                                         "but independent of the original game")
    wait: BoolProperty(name="Wait", default=False, options={"SKIP_SAVE", "HIDDEN"},
                       description="Do all the work inside this call (for scripts)")

    @classmethod
    def poll(cls, context):
        return not busy()

    def invoke(self, context, event):
        if not self.directory.strip():
            self.directory = default_folder()
        return context.window_manager.invoke_props_dialog(self, width=560, title="Create Test Game Copy",
                                                          confirm_text="Create Copy")

    def draw(self, context):
        lay = self.layout
        lay.label(text="A private copy of the game to test in.")
        lay.prop(self, "directory")
        lay.prop(self, "everything")
        if not available():
            ui_kit.alert(lay, "Part of this add-on is out of date. Install the newest version of the add-on.")
            return
        dest = folder_text(self.directory) or default_folder()
        plan, why = make_plan(context, dest, self.everything)
        if plan is None:
            ui_kit.alert(lay, why)
            return
        src = source_folder(context)
        lay.label(text=f"Copy of: {src}", icon="FILE_FOLDER")
        if plan.problem:
            ui_kit.alert(lay, plan.problem)
            return
        for i, line in enumerate(plan.summary()):
            if i == 1 and not plan.room:
                ui_kit.alert(lay, line)
            else:
                for part in ui_kit.wrap_lines(line, 80):
                    lay.label(text=part)

    def execute(self, context):
        dest = folder_text(self.directory) or default_folder()
        ok, text = start(context, dest, self.everything, self.wait)
        self.report({"INFO"} if ok else {"ERROR"}, text)
        return {"FINISHED"} if ok else {"CANCELLED"}


class BZ_OT_cancel_test_game(bpy.types.Operator):
    """Stop making the test copy and remove what it has made so far"""
    bl_idname = "bz.cancel_test_game"
    bl_label = "Cancel"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return busy()

    def execute(self, context):
        self.report({"WARNING"}, cancel())
        return {"FINISHED"}


def draw_copy(layout, context) -> None:
    """The panel's lines about the copy: how far a running copy is, else the result of the last one."""
    job = _STATE["job"]
    if job is not None:
        pct = int(100 * job.done / job.total)
        row = layout.row(align=True)
        row.label(text=f"Making the test copy: {pct}%", icon="TIME")
        row.operator(BZ_OT_cancel_test_game.bl_idname, text="Cancel", icon="CANCEL")
        return
    msg = _STATE["message"]
    if msg:
        if _STATE["level"] == "ERROR":
            ui_kit.alert(layout, msg)
        else:
            col = layout.column(align=True)
            for part in ui_kit.wrap_lines(msg, 60):
                col.label(text=part)


CLASSES = (BZ_OT_create_test_game, BZ_OT_cancel_test_game)
