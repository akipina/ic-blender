"""Export Creature for the Game: the creature as the game reads it, not just a model file.

Export Model writes only a ``.sgm``. The game finds a creature as a blueprint in ``data/art/ebps/stock`` (the model inside it) with a
``.lua`` of stats, a ``.lsc`` of limb scales and a ``<name>_torso.bmp`` skin -- the rules, each with its decompiled source line, are in
``ICParse/hub/package.py`` and ``ICParse/sgm/creature/torso.py``. This operator builds that folder ("the item") from the creature
on screen and checks it with the same checker the hub uses (``ICParse.hub.check_item``):

1. the creature is exported in memory exactly as Export Model builds it (``ops.gather_export`` / ``ops.build_export``);
2. ``core.validate_model`` and ``core.check_structure`` run on it: a problem that would stop the creature loading refuses the export;
3. the donor is found (the blueprint beside the source model, or the one the model was imported from, or the stock animal the
   project was started from) and the new model goes into ITS blueprint, keeping its motion tree, events and icon; a creature with no
   donor gets a generated blueprint and the events of a stock animal for the clips it has;
4. stats and limb scales start from the donor's own ``.lua`` / ``.lsc`` (a made-up baseline is refused by the hub checker);
5. the folder ``<out>/<name>/`` (all paths lower case) is written only when the hub checker passes it, and
   ``scene["bz_last_item_dir"]`` names it for Install;
6. "Replace the original <animal>" (instead of "A new creature of my own"): the same item under the stock animal's own name. The game takes
   a loose ``Data\\art\\ebps\\stock\\<animal>.ebp`` over the archive's (``SigmaApp.cpp:2764`` loose Data at priority 500, ``Engine.sga``
   at 100 :2786), the roster already names the animal (``CombinerCL.cpp:773-800``), so Install needs no roster change and clears the
   cached mesh. The hub checker refuses a stock name on purpose (``creature.basename``, MODDING_PLAN.md 2.5), so a replacement is for
   Install into Test Game only; the dialog says what it does to the game and to multiplayer
   (``BlueprintManager.cpp:605-646``: the start-of-game check compares the numbers and the bounding box of the numbered creatures, i.e. those in the match's armies).

Nothing here knows a byte layout; ``ICParse.hub.package.build_creature_item`` does the work and is tested without Blender.
"""
from __future__ import annotations

import os
import re
import textwrap

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import core
from . import ops
from . import store
from . import enumitems

KEY_NAME = "bz_game_name"          # on the creature: the last item name used
KEY_TITLE = "bz_game_title"
KEY_DESC = "bz_game_desc"
KEY_ICON = "bz_game_icon"          # on the creature: path of the picture "Icon from this view" / the operator saved
KEY_STATS_FROM = "bz_game_stats_from"
KEY_MODE = "bz_game_mode"          # on the creature: "REPLACE" when it replaces the stock animal it was started from (New Project sets it)
MODE_NEW, MODE_REPLACE = "NEW", "REPLACE"
KEY_LAST_ITEM = "bz_last_item_dir"  # on the scene (CONTRACT.md)
MAX_LINES = 6


def _clean_name(text: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", str(text).lower()).strip("_")
    return s


def default_name(obj) -> str:
    """A name for the item from what the creature is called: its last export name, else its source file's, in the shape the game's
    roster wants ([a-z0-9_], 3 to 24); a stock animal's own name gets ``_mod`` after it."""
    s = obj.get(KEY_NAME) or _clean_name(os.path.splitext(os.path.basename(store.source_path(obj)))[0]) or _clean_name(obj.name)
    s = s[:24].rstrip("_")
    if len(s) < 3:
        s = (s + "_creature")[:24]
    try:
        stock = _stock()
        if s in stock.animals:
            s = (s[:20] + "_mod")
    except Exception:                                       # noqa: BLE001 -- no game folder: the export says so itself
        pass
    return s


_STOCK = None


def _stock():
    """The game's archives' index (read once). The game folder is the add-on's Game folder setting (as Combine Preview reads it),
    else ``$IC_INSTALL`` or the Steam library."""
    global _STOCK
    from ICParse.hub import StockIndex
    from . import combocore
    root = combocore.install_root()
    if _STOCK is None or (root and _STOCK.game != root):
        _STOCK = StockIndex(root or None)
    return _STOCK


def name_problem(name: str):
    """What is wrong with ``name`` as the name of a creature of its own (checked while it is typed), or None."""
    from ICParse.hub.roster import valid_name
    if not valid_name(name):
        return "3 to 24 characters of a-z, 0-9 and _ (lower case, no spaces)"
    try:
        stock = _stock()
    except Exception:                                       # noqa: BLE001
        return None
    if name in stock.animals:
        return f"{name} is a game animal's name: choose 'Replace the original {name}' to change that animal, or another name"
    return None


def replace_target(obj):
    """The game animal this creature can REPLACE (its lower-case name), or None: the one the project was started from (the stamp New Project
    leaves, else the blueprint beside the model), when the game's own archives hold it. A creature built from nothing replaces nothing."""
    try:
        stock = _stock()
    except Exception:                                       # noqa: BLE001 -- no game folder
        return None
    for cand in (obj.get("bz_stock_donor"), getattr(find_donor(obj), "name", None)):
        if cand and str(cand).lower() in stock.animals:
            return str(cand).lower()
    return None


_MODE_ITEMS: list = []             # enum items must stay referenced while Blender shows them


@enumitems.stable
def _mode_items(self, context):
    """The two choices, a creature of its own first (so it is the default: nothing of the game is replaced unless you say so) and, when the
    project was started from one of the game's animals, replacing it, with its name."""
    items = [(MODE_NEW, "A new creature of my own", "Files under a name you choose: it is added to the creature creator and the game's "
                                                    "own animals stay as they are")]
    try:
        target = replace_target(store.active_creature(context)) if context is not None else None
    except Exception:                                       # noqa: BLE001 -- a draw never fails for this
        target = None
    if target:
        items.append((MODE_REPLACE, f"Replace the original {target}",
                      f"The files take the place of the game's own {target}: every army and every skirmish uses your version"))
    _MODE_ITEMS[:] = items
    return _MODE_ITEMS


_ICON_READ_MAX = 256               # the icon ends up 64 x 64: a bigger picture is scaled down before its pixels are read


def _read_image(path: str):
    """``(w, h, rgba)`` of a picture file through Blender, or None. Raises when Blender cannot load the file."""
    if not path or not os.path.isfile(path):
        return None
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if not w or not h:
            return None
        if max(w, h) > _ICON_READ_MAX:                      # scale the loaded copy, so no huge Python float list is built
            k = _ICON_READ_MAX / max(w, h)
            img.scale(max(1, int(round(w * k))), max(1, int(round(h * k))))
            w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(img)
    a = (px.reshape(h, w, 4)[::-1] * 255.0 + 0.5).clip(0, 255).astype(np.uint8)   # Blender stores bottom-up
    return w, h, a.tobytes()


def source_names(job):
    """``({old bone name: new name}, {old clip name: new name})``: what was renamed in Blender since the model was read, so the
    donor blueprint's events and motion nodes follow (the names the donor's blueprint holds are the SOURCE model's)."""
    bones = {}
    src = job.source
    for i, nm in (job.state.bone_renames or {}).items():
        if 0 <= int(i) < len(src.bones) and str(src.bones[int(i)].name) != str(nm):
            bones[str(src.bones[int(i)].name)] = str(nm)
    clips = {}
    plan = job.plan
    if plan is not None:
        from . import actions as bzactions
        for act, target in plan.encode:
            was = act.get(bzactions.KEY_CLIP)
            if was and str(was) != str(target) and str(was) in set(plan.drop):
                clips[str(was)] = str(target)
    return bones, clips


def default_out_dir(obj) -> str:
    """``ic_items`` beside the source model, else beside the saved .blend, else in the user's home folder (never Blender's working
    folder, which is wherever Blender happened to be started)."""
    here = os.path.dirname(store.source_path(obj) or "") or os.path.dirname(bpy.data.filepath or "") or os.path.expanduser("~")
    return os.path.join(here, "ic_items")


_DONORS: dict = {}                 # (source, stamps, files' mtimes) -> (when read, donor): a redraw must not list and read the project folder again
_DONOR_SECONDS = 30.0              # a file replaced under a name the stamp does not look at (another letter case) shows within this long


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _donor_key(obj):
    src = store.source_path(obj) or ""
    ebp = obj.get("bz_ebp") or ""
    key = [src, ebp, obj.get("bz_stock_donor") or "", obj.get("bz_stock_archive") or ""]
    for p in (src, ebp):
        if p:
            base = os.path.splitext(p)[0]
            key += [_mtime(os.path.dirname(base) or "."), _mtime(base + ".ebp"), _mtime(base + ".lua"), _mtime(base + ".lsc")]
    return tuple(key)


def find_donor(obj, fresh=False):
    """The donor of ``obj`` (see ``ICParse.hub.package.find_donor``). Draw code asks for it every redraw, so the answer is kept while the files
    beside the model are as they were; ``fresh`` reads the files again (what Export uses)."""
    import time
    from dataclasses import replace
    from ICParse.hub import package as PK
    src = store.source_path(obj)

    def look():
        return PK.find_donor(src or None, obj.get("bz_stock_donor") or None, obj.get("bz_stock_archive") or None, ebp_path=obj.get("bz_ebp") or None)
    if fresh:
        return look()
    key = _donor_key(obj)
    hit = _DONORS.get(key)
    now = time.monotonic()
    if hit is None or now - hit[0] > _DONOR_SECONDS:
        if len(_DONORS) > 64:
            _DONORS.clear()
        hit = _DONORS[key] = (now, look())
    return replace(hit[1]) if hit[1] is not None else None             # a copy: a caller that edits it must not change what is kept


def build(obj, context, name, title="", description="", icon_file="", stats_from="", out_dir="", dry=False, replace=False, auto_icon=True):
    """:func:`_build`, with every line of the report in plain words (no source files, function names or rule ids)."""
    res, lines = _build(obj, context, name, title, description, icon_file, stats_from, out_dir, dry, replace, auto_icon)
    from .sceneobj.ui_kit import plain
    return res, [(lvl, plain(text)) for lvl, text in lines]


def check_lines(obj, context):
    """What Export for the Game would refuse or warn about, found the way Export finds it but with nothing written: ``(errors, warnings)``, plain
    sentences. Check the Creature adds these to its own findings, so a creature Check passes is a creature Export accepts (the item
    check, the events, the motions and the numbers of the Creature Stats panel included). The model rules Check has run itself are not repeated.
    A project that replaces a game animal is checked as that replacement."""
    obj = store.creature_of(obj)
    if obj is None:
        return [], []
    try:
        obj["bz_check_numbers"] = []                        # what an earlier Check said about the numbers is out of date once this one starts
    except (TypeError, AttributeError):
        pass
    try:
        from . import statspanel
        res, lines = build(obj, context, default_name(obj), stats_from=statspanel.stats_from_of(obj), dry=True, replace=obj.get(KEY_MODE) == MODE_REPLACE and replace_target(obj) is not None)
    except Exception as e:                                  # noqa: BLE001 -- the check itself must never break Check
        return [], [f"The export check could not run: {type(e).__name__}: {e}"]
    errors = [t for lvl, t in lines if lvl == "ERROR" and not t.startswith("Export for the Game refused")]
    warns = [t for lvl, t in lines if lvl == "WARNING"]
    try:
        obj["bz_check_numbers"] = [t for lvl, t in lines if lvl == "INFO" and t.startswith(("Numbers changed", "With these numbers"))]
    except (TypeError, AttributeError):
        pass
    return errors, warns


def numbers_lines(sx) -> list:
    """The ``(level, text)`` lines that say which numbers of the Creature Stats panel were changed and what the creature comes to with them."""
    lines = []
    if sx.changes:
        lines.append(("INFO", "Numbers changed from the original's: " + "; ".join(sx.changes[:MAX_LINES]) + (" ..." if len(sx.changes) > MAX_LINES else "")))
    if sx.derived is not None:
        from . import statscore
        lines.append(("INFO", ("With these numbers the creature on its own comes to: " + "; ".join(statscore.summary_lines(sx.derived))
                              + f" (the rules of {sx.derived.rules.name}: {sx.derived.rules.where})") if sx.derived.ok
                      else f"The creature's rank and cost could not be worked out: {sx.derived.error}"))
    return lines


def _build(obj, context, name, title="", description="", icon_file="", stats_from="", out_dir="", dry=False, replace=False, auto_icon=True):
    """Everything the operator does, minus the Blender reporting: returns ``(PackageResult or None, lines)`` where ``lines`` are
    ``(level, text)`` pairs ('ERROR' / 'WARNING' / 'INFO'). ``dry``: the same checks and the same item, in memory: nothing is written (the
    Check button), and the model rules Check has already run are not run again. ``replace``: the item replaces the game animal
    :func:`replace_target` names (``name`` is then ignored: a replacement has the original's name). With no icon picture chosen
    (``icon_file``, "Icon from this view") and ``auto_icon``, the combiner icon is rendered from the creature (:mod:`gameicon`; never for a
    ``dry`` run); when that cannot be done the donor's picture stays, with a warning that says so."""
    from ICParse.hub import package as PK
    from ICParse.sgm import parser as sgm_parser
    from . import edittools, notelevels
    lines = []
    try:
        job = ops.gather_export(obj, context, animations=True)
        model, summary = ops.build_export(job, context)
        raw = sgm_parser.write(model)
    except ops.ExportGatherError as e:
        return None, [("ERROR", str(e))]
    except Exception as e:                                  # noqa: BLE001
        return None, [("ERROR", f"Export failed: {type(e).__name__}: {e}")]
    problems = []
    if not dry:
        problems = core.validate_model(model, edittools._verts(obj))
        problems += core.check_structure(store.limb_names(obj), store.patch_limb(obj), store.connections(obj), store.bone_names(obj),
                                         store.limb_ids(obj))
    blocking = core.blocking(problems)
    advisories = [p for p in problems if p not in blocking]            # WARN / STOCK / COMBINE lines: shown, they do not stop the export
    if blocking:
        lines += [("ERROR", f"the creature would not load: {p}") for p in blocking[:MAX_LINES]]
        if len(blocking) > MAX_LINES:
            lines.append(("ERROR", f"... and {len(blocking) - MAX_LINES} more (Check the Creature lists them all)"))
        lines.append(("ERROR", "Export for the Game refused: fix these first"))
        return None, lines
    donor = find_donor(obj, fresh=True)
    replace_of = None
    if replace:
        replace_of = replace_target(obj)
        if replace_of is None:
            return None, lines + [("ERROR", "There is no game animal to replace: this creature was not started from one of the game's own animals. "
                                            "Choose 'A new creature of my own'.")]
        name = replace_of
    # the events edited in the Events box and the project's effect and sound files (fxpanel.creature_export_hook)
    from . import fxpanel
    hook = fxpanel.creature_export_hook(obj, donor, name)
    if hook.error:
        return None, lines + [("ERROR", hook.error), ("ERROR", "Export for the Game refused: nothing was written")]
    donor = hook.donor
    kw_fx = hook.kwargs
    lines += [("WARNING" if notelevels.is_warning(n) else "INFO", n) for n in hook.notes]
    bone_renames, clip_renames = source_names(job)
    out = bpy.path.abspath(out_dir) if out_dir else default_out_dir(obj)
    if dry:
        out = None
    else:
        from .sceneobj import panels_blueprint as PB
        try:
            out = PB.check_target(out)                        # never the game's own folder, never inside a Steam folder
        except PB.Refused as e:
            return None, lines + [("ERROR", str(e)), ("ERROR", "Export for the Game refused: nothing was written")]
    icon = None
    pic = icon_file or obj.get(KEY_ICON) or ""
    if pic:
        try:
            icon = _read_image(bpy.path.abspath(pic))
        except Exception:                                   # noqa: BLE001 -- Blender could not load the file as a picture
            return None, lines + [("ERROR", f"The icon picture {os.path.basename(pic)} could not be opened. Choose another picture."),
                                  ("ERROR", "Export for the Game refused: nothing was written")]
        if icon is None:
            lines.append(("WARNING", f"the icon picture {pic} could not be read: the icon is not changed"))
    kw = {}
    if stats_from:
        kw["stats_from"] = stats_from
    # r2stats: the numbers edited in the Creature Stats panel go into the donor's .lua (nothing edited: its bytes are copied as they are)
    from . import statspanel
    try:
        sx = statspanel.export_numbers(obj, donor, stats_from, _stock())
    except Exception as e:                                  # noqa: BLE001
        return None, lines + [("ERROR", f"The numbers of the Creature Stats panel could not be applied: {type(e).__name__}: {e}")]
    if sx.problems:
        lines += [("ERROR", p) for p in sx.problems[:MAX_LINES]]
        lines.append(("ERROR", "Export for the Game refused: these numbers are outside what the export accepts (Creature Stats panel)"))
        return None, lines
    if sx.stats:
        kw["stats"] = sx.stats
    if sx.stats_add:
        kw["stats_add"] = sx.stats_add
    from . import motionspanel                              # r2motions: the edits of the Motions box, and what it says about new clips
    kw["motions"], motion_notes = motionspanel.creature_export(obj, donor)
    if icon is None and auto_icon and not dry:
        # the render costs a baked surface and a Blender render: only for an item the add-on's checks will take (this pass writes nothing)
        try:
            trial = PK.build_creature_item(raw, name, out_dir=None, title=title or None, description=description, donor=donor, game=None,
                                           stock=_stock(), bone_renames=bone_renames, clip_renames=clip_renames, replace_of=replace_of,
                                           **kw, **kw_fx)
        except Exception:                                   # noqa: BLE001 -- the real build below says what is wrong
            trial = None
        if trial is not None and trial.ok:
            from . import gameicon
            icon, why = gameicon.render_icon(obj, context)
            if icon is None:
                lines.append(("WARNING", f"No icon was made for the creature creator: {why}. It shows the original's picture; choose an icon picture "
                                         f"or press Icon from this view in a 3D view, then export again"))
            else:
                lines.append(("INFO", f"The creature creator's icon: {why}. Choose an icon picture or Icon from this view to use your own."))
    try:
        res = PK.build_creature_item(raw, name, out_dir=out, title=title or None, description=description, donor=donor,
                                     icon_image=icon, game=None, stock=_stock(), bone_renames=bone_renames,
                                     clip_renames=clip_renames, replace_of=replace_of, **kw, **kw_fx)
    except Exception as e:                                  # noqa: BLE001
        return None, lines + [("ERROR", f"Export for the Game failed: {type(e).__name__}: {e}")]
    seen = set()
    if res.replace_warning:
        lines.append(("WARNING", res.replace_warning))
    for w in list(summary.warnings) + advisories + list(res.warnings[:MAX_LINES * 2]) + motion_notes:
        if w not in seen:                                   # the export repeats a line for each place it applies
            seen.add(w)
            lines.append((notelevels.level(w), w))          # a plain fact is information; anything else stays a warning
    if not res.ok:
        lines += [("ERROR", e) for e in res.errors]
        lines.append(("ERROR", "Export for the Game refused: nothing was written"))
        return res, lines
    if dry:
        lines += [("WARNING", n) for n in sx.notes]
        lines += numbers_lines(sx)                          # what the numbers come to: Check tells it too, not only the export
        return res, lines
    who = f"built on {res.donor}" if res.donor else "built from nothing"
    if res.replaces:
        lines.append(("INFO", f"Wrote {len(res.files)} files to {res.item_dir}; they replace the game's own {res.replaces}. Press Install into "
                              f"Test Game in the Game panel; Remove puts the original back."))
    else:
        lines.append(("INFO", f"Wrote {len(res.files)} files to {res.item_dir} ({who}). Next: press Install into Test Game in the Game panel"))
        lines.append(("INFO", "Install into Test Game adds the creature to the creature creator. Copying the files by hand does not: "
                              "use Pack for Sharing."))
    lines += [("INFO", n) for n in res.notes]
    # r2stats: say what was changed and what the game makes of it
    lines += [("WARNING", n) for n in sx.notes]
    lines += numbers_lines(sx)
    return res, lines


class BZ_OT_export_creature_game(bpy.types.Operator):
    """Write the creature as an item folder the game can load"""
    bl_idname = "bz.export_creature_game"
    bl_label = "Export Creature for the Game"
    bl_options = {"REGISTER"}

    # SKIP_SAVE on every field: Blender would otherwise open the dialog of the NEXT creature with the last creature's name, and OK would write
    # the second creature over the first one's item folder
    mode: EnumProperty(name="Export as", items=_mode_items, options={"SKIP_SAVE"},
                       description="A new creature of your own, or the game's own animal this one started from, replaced")
    name: StringProperty(name="Name", description="The creature's file name: 3 to 24 letters a-z, digits and _", default="", options={"SKIP_SAVE"})
    title: StringProperty(name="Display name", description="The name the creature creator shows",
                          default="", options={"SKIP_SAVE"})
    description: StringProperty(name="Description", description="The text shown with the creature's stats", default="",
                                options={"SKIP_SAVE"})
    icon_file: StringProperty(name="Icon picture", subtype="FILE_PATH", default="", options={"SKIP_SAVE"},
                              description="A picture for the creature's icon. Empty: the picture from Icon from this View, if any")
    stats_from: StringProperty(name="Start stats from", default="cheetah", options={"SKIP_SAVE"},
                               description="The game's own animal whose numbers a creature built from nothing starts from")
    out_dir: StringProperty(name="Folder", subtype="DIR_PATH", default="", options={"SKIP_SAVE"},
                            description="The folder the item folder goes in. Empty: ic_items beside the source model")
    auto_icon: BoolProperty(name="Make the icon from the creature", default=True, options={"SKIP_SAVE"},
                            description="With no icon picture, draw the icon from the creature. Off: the original's icon stays")

    @classmethod
    def poll(cls, context):
        return store.active_creature(context) is not None          # the creature, or its rig

    def invoke(self, context, event):
        obj = store.active_creature(context)
        from . import statspanel
        self.stats_from = statspanel.stats_from_of(obj) or "cheetah"
        if obj.get(KEY_MODE) == MODE_REPLACE and replace_target(obj):         # a project started as "replace the original" stays one
            self.mode = MODE_REPLACE
        if not self.name:
            self.name = default_name(obj)
        if not self.title:
            self.title = obj.get(KEY_TITLE) or self.name.replace("_", " ").title()
        if not self.description:
            self.description = obj.get(KEY_DESC) or ""
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        if len(_mode_items(self, context)) > 1:
            col.prop(self, "mode", expand=True)
        replacing = self.mode == MODE_REPLACE and replace_target(store.active_creature(context))
        if replacing:
            from ICParse.hub import package as PK
            from .sceneobj.ui_kit import plain
            for line in textwrap.wrap(plain(PK.REPLACE_WARNING.format(name=replacing)), 60):
                col.label(text=line, icon="ERROR" if line.startswith("This REPLACES") else "BLANK1")
            col.prop(self, "icon_file")
            col.prop(self, "auto_icon")
            col.prop(self, "out_dir")
            return
        col.prop(self, "name")
        why = name_problem(self.name)
        if why:
            col.label(text=why, icon="ERROR")
        col.prop(self, "title")
        col.prop(self, "description")
        donor = find_donor(store.active_creature(context))
        if donor is None:
            col.prop(self, "stats_from")
            col.label(text="Built from nothing: a generated blueprint", icon="INFO")
        else:
            col.label(text=f"Built on: {donor.label} ({donor.origin})", icon="LINKED")
        col.prop(self, "icon_file")
        col.prop(self, "auto_icon")
        col.prop(self, "out_dir")

    def execute(self, context):
        obj = store.active_creature(context)
        replacing = self.mode == MODE_REPLACE and replace_target(obj) is not None
        name = self.name or default_name(obj)
        why = None if replacing else name_problem(name)
        if why:
            self.report({"ERROR"}, f"The name {name!r}: {why}")
            return {"CANCELLED"}
        res, lines = build(obj, context, name, self.title, self.description, self.icon_file, self.stats_from, self.out_dir, replace=replacing,
                           auto_icon=self.auto_icon)
        for level, text in lines:
            self.report({level}, text)
        if res is None or not res.ok:
            return {"CANCELLED"}
        if replacing:
            obj[KEY_MODE] = MODE_REPLACE
            name = res.name
        else:
            obj[KEY_MODE] = MODE_NEW
            obj[KEY_NAME], obj[KEY_TITLE], obj[KEY_DESC] = name, self.title, self.description
            if find_donor(obj, fresh=True) is None and self.stats_from:
                from . import statspanel
                statspanel.set_stats_from(obj, self.stats_from)       # the Creature Stats panel starts from the same animal
        context.scene[KEY_LAST_ITEM] = res.item_dir
        enumitems.forget()                                        # the Game panel shows this export at once
        obj[KEY_LAST_ITEM] = res.item_dir                         # this creature's own last export (the Game panel shows it, not another creature's)
        try:
            context.scene.bz_item_dir = ""                        # a folder picked in the Game panel no longer wins over this export
        except AttributeError:
            pass
        obj["bz_last_game_export"] = next(t for _l, t in lines if t.startswith("Wrote "))
        return {"FINISHED"}


class BZ_OT_game_icon_from_view(bpy.types.Operator):
    """Take the combiner icon from the 3D view as it is now"""
    bl_idname = "bz.game_icon_from_view"
    bl_label = "Icon from this view"

    @classmethod
    def poll(cls, context):
        return store.active_creature(context) is not None and context.area is not None and context.area.type == "VIEW_3D"

    def execute(self, context):
        obj = store.active_creature(context)
        try:
            png = capture_view(context, path=icon_picture_path(obj))
        except Exception as e:                              # noqa: BLE001
            self.report({"ERROR"}, f"Could not take the picture from the view ({e}). Use an icon picture file instead.")
            return {"CANCELLED"}
        obj[KEY_ICON] = png
        self.report({"INFO"}, f"The icon will be this view ({os.path.basename(png)}).")
        return {"FINISHED"}


def icon_picture_path(obj) -> str:
    """Where the picture of Icon from this view is kept: in the project's own ic_items folder (beside the source model, else beside the saved
    .blend), so it is still there after a restart and the next export of the project writes the same icon."""
    folder = default_out_dir(obj)
    from .sceneobj import panels_blueprint as PB
    try:
        PB.check_target(folder)                             # a model read from the game's own folder must not get files written beside it
    except PB.Refused:
        from . import startops
        folder = os.path.join(startops.project_dir(), "ic_items")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{default_name(obj)}_icon.png")


def capture_view(context, size: int = 256, path: str = "") -> str:
    """Draw the active 3D view into an offscreen buffer, crop its middle to a square and save it as a PNG (path returned). Needs a
    3D view with a window region (an interactive session): the picture is taken from what that view shows, the creature's surface
    overlay included when the view draws it."""
    import gpu
    import tempfile
    import numpy as np
    area = context.area
    region = next(r for r in area.regions if r.type == "WINDOW")
    space = area.spaces.active
    w, h = region.width, region.height
    off = gpu.types.GPUOffScreen(w, h)
    try:
        off.draw_view3d(context.scene, context.view_layer, space, region, space.region_3d.view_matrix,
                        space.region_3d.window_matrix, do_color_management=True)
        buf = off.texture_color.read()
        px = np.asarray(buf, dtype=np.float32).reshape(h, w, 4)
    finally:
        off.free()
    s = min(w, h)
    y0, x0 = (h - s) // 2, (w - s) // 2
    sq = px[y0:y0 + s, x0:x0 + s]
    ys = (np.arange(size) * s // size).clip(0, s - 1)
    sq = sq[ys][:, ys]
    img = bpy.data.images.new("bz_game_icon", size, size, alpha=True)
    try:
        img.pixels = sq.reshape(-1).tolist()
        if not path:
            fd, path = tempfile.mkstemp(prefix="bz_game_icon_", suffix=".png")
            os.close(fd)
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return path


def draw_game_export(layout, context):
    """The Export-for-the-Game block of the IC panel (also called by the Game panel when it exists)."""
    obj = store.active_creature(context)
    if obj is None:
        return
    box = layout.box()
    box.label(text="2. Export for the Game", icon="PACKAGE")
    donor = None
    try:
        donor = find_donor(obj)
    except Exception:                                       # noqa: BLE001 -- no game folder / unreadable files: the export says why
        pass
    if donor is not None:
        box.label(text=f"Built on {donor.label}'s blueprint and stats", icon="LINKED")
    else:
        box.label(text="Built from nothing: a generated blueprint", icon="INFO")
    try:
        target = replace_target(obj)
    except Exception:                                       # noqa: BLE001 -- a draw never fails for this
        target = None
    if target:                                              # the same two choices as the export dialog and New Project (and the scene objects' export)
        if obj.get(KEY_MODE) == MODE_REPLACE:
            box.label(text=f"Export as: Replace the original {target}", icon="ERROR")
        else:
            box.label(text="Export as: A new creature of my own", icon="INFO")
    row = box.row(align=True)
    row.operator(BZ_OT_export_creature_game.bl_idname, icon="EXPORT")
    row.operator(BZ_OT_game_icon_from_view.bl_idname, text="Icon from this View", icon="RESTRICT_RENDER_OFF")
    last = obj.get(KEY_LAST_ITEM)                                # this creature's own last export, never another creature's
    if last:
        box.label(text=f"Last export: {os.path.basename(str(last))}", icon="CHECKMARK")
    else:
        box.label(text="Not exported yet.", icon="INFO")


CLASSES = (BZ_OT_export_creature_game, BZ_OT_game_icon_from_view)
