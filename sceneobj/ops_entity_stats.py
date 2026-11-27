"""Editable gameplay numbers of a structure, gatherer, character or prop (the Blueprint tab's "Gameplay numbers").

**Which script the game loads.** ``BlueprintHeader::LoadAttributes`` (decompiled, ``decomp/ic/src/EntityBlueprint.cpp:2185``) takes the blueprint's own
path, swaps ``.ebp`` for ``.lua`` (:2196-2199) and loads that script; when no such file exists it loads ``data:art/ebps/defstats<class>.lua`` instead
(:2200-2201, the class is the blueprint's ``defstats class``, :232). The script is run as Lua (``LuaConfig::LoadFile``, :2204: so ``dofilepath``
inherits another script and the LAST assignment of a name wins), and then the table ``gameattributes`` is read, number by number (:2215-2225). A script that
fails to load leaves the entity without any numbers (no second fallback: :2204-2205). The numbers are what the game's rules read per entity
(``decomp/icmod/src/ModRDN/Simulation/ExtInfo``: hit points ``HealthExtInfo.cpp:24``, cost ``CostExtInfo.h:41-44``, speeds ``MovingExtInfo.cpp:30-32`` ...),
each clamped into a range by ``GetVal`` (``ModStaticInfo.h:156-176``): ``ICParse.ebp.stats.GAME_RANGES``.

**What the panel writes.** Export Entity Folder writes ``<name>.lua`` beside the blueprint, in the stock spelling: ``dofilepath`` of the class default,
``ext = gameattributes``, ``ext.name = number`` for each number that differs (``ICParse.ebp.stats.entity_overrides``). It inherits what the original's
script inherits (the class default for most; ``defperson.lua`` for the characters), never the original's own script, because the hub's validator
accepts a scene object's script only in that shape (``ICParse/hub/check.py`` ``_extends_gameattributes``: one stock ``def*.lua``) and because the same
file can then replace the original's script at its own path (a script that included itself would never end). So the numbers the original's script
sets over its base are written again first; then the author's changes. An original that inherits nothing (the lab's script is one plain table)
gets its whole table written: it would otherwise gain every number of the class default.

* **Replace the original**: the file lands at the original's path and takes the place of its script (a loose file in ``Data`` outranks the archive:
  ``App_SetupFilePathAliases``, IC.exe 0x004034cc). Nothing is written when no number was changed.
* **A new name**: with no ``.lua`` of the new name the game gives the thing the class default's numbers, not the original's (:2200-2201). So "Use the
  original's numbers" (``bz_blueprint.stats_base``) decides what a new name starts from: ticked, the original's numbers (the script is written even
  when nothing was changed); unticked, the default of its kind (a script only when a number was changed). The list shows what the export starts from.

The rows are the properties ``stats`` of the Blueprint (``panels_blueprint.BZ_PG_blueprint_stat``); this module fills the extra fields, keeps edits when
the list is read again (the kind or folder was changed), draws the editable list and builds the script at export.
"""
from __future__ import annotations

import importlib
import os
import re

import bpy
from bpy.props import FloatProperty, StringProperty

NAME_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
LIST_MAX = 40                         # rows of the search list shown at once


def clean(v: float) -> float:
    """A number as the modder meant it (a Blender float holds 32 bits: 0.1 reads back as 0.10000000149)."""
    return float(f"{float(v):.7g}")


def _lib():
    return importlib.import_module("ICParse.ebp.stats")


def _pb():
    return importlib.import_module(__package__ + ".panels_blueprint")


# ---------------------------------------------------------------------------------------------------------------- the rows
def number_row(row, value: float, class_default: dict | None) -> None:
    """Fill the extra fields of a listed row: the number as the files give it, what it is by default for the kind, and no edit yet."""
    row.orig = float(value)
    row.new_value = float(value)
    row.added = False
    d = None if class_default is None else class_default.get(row.key)
    row.has_dflt = d is not None
    row.dflt = float(d) if d is not None else 0.0


def is_edited(row) -> bool:
    return bool(row.added) or clean(row.new_value) != clean(row.orig)


def edited_rows(bp) -> list:
    return [r for r in bp.stats if is_edited(r)]


def class_default(lib, target: str, class_id: int, resolver):
    """``{name: number}`` of the class default script of this kind of thing, or ``None`` when the class has none (or nothing can read it)."""
    ref = lib.stats_candidates(target, class_id)[1]
    try:
        if resolver is None or not resolver.exists(ref):
            return None
        return dict(lib.read_stats(ref, resolver).attrs)
    except Exception:                                                  # noqa: BLE001 -- unreadable = no class default to build on (the export says so)
        return None


def snapshot(bp) -> dict:
    """The edits in the list, to put back after the list was read again."""
    return {r.key: (float(r.new_value), bool(r.added), float(r.dflt), bool(r.has_dflt)) for r in bp.stats if is_edited(r)}


def restore(bp, snap: dict) -> None:
    """Put edits back on the freshly read list: by name; a number the new list has not is shown again as an added one."""
    by = {r.key: r for r in bp.stats}
    for key, (value, added, dflt, has_dflt) in snap.items():
        r = by.get(key)
        if r is None:
            r = bp.stats.add()
            r.key, r.value, r.origin = key, f"{value:g}", "added"
            r.orig, r.added, r.dflt, r.has_dflt = 0.0, True, dflt, has_dflt
        r.new_value = value


# ---------------------------------------------------------------------------------------------------------------- what the export writes
class StatsFiles:
    """What the export adds for the numbers: ``files`` ({path in the item: bytes}), plain-words ``notes``, ``script`` (the text or None), ``edits``."""

    def __init__(self):
        self.files: dict = {}
        self.notes: list = []
        self.script = None
        self.edits: dict = {}
        self.carried: dict = {}


def original_target(ent):
    """The thing of the game the project was started from (``panels_blueprint.replace_target``): where its own stats script lies, or ``None``."""
    try:
        return _pb().replace_target(ent)
    except Exception:                                                  # noqa: BLE001 -- nothing to replace: no original to show
        return None


def _target_ref(rt) -> str:
    return f"data:art/ebps/{rt.folder + '/' if rt.folder else ''}{rt.name}.ebp"


_DEF_FILE = re.compile(r"^data:art/ebps/def[^/]*\.lua\Z", re.I)         # the stock base scripts a scene object's script may inherit (the hub validator's rule)


def _inherited(lib, view, class_ref: str, resolver):
    """What the original's own script builds on, as ``(kind, ref, numbers)``: ``("class", None, None)`` when it inherits the class default,
    ``("base", ref, numbers)`` when it inherits another stock base script (``defperson.lua``), ``("none", None, None)`` when it is a plain
    table that inherits nothing (the lab's) or builds on something a scene object's script may not inherit."""
    if len(view.chain) < 2:
        return "none", None, None
    first = view.chain[1].replace("\\", "/")
    if first.lower() == class_ref.lower():
        return "class", None, None
    if not _DEF_FILE.match(first):
        return "none", None, None
    try:
        return "base", first, dict(lib.read_stats(first, resolver).attrs)
    except Exception:                                                  # noqa: BLE001 -- unreadable base: write the whole table instead
        return "none", None, None


def export_files(ent, bp, mode: str, ebp_rel: str, new_mode: str = "NEW") -> StatsFiles:
    """The stats script of the item: nothing unless a number was edited (or, for a new name that starts from the original's numbers, even
    when none was). ``ebp_rel`` is the blueprint's path inside the item; the script sits beside it with ``.lua``.

    What the author's changes go onto (the same thing the list shows): **replace the original**: the numbers of the original thing (its own stats
    script when it has one, else the class default), because the file takes that script's place; **a new name**: the original's numbers when
    "Use the original's numbers" is ticked, else the default numbers of its kind (that is what the game gives a name with no script).
    The script inherits what the original's script inherits (the class default, or another stock base script such as ``defperson.lua``) and sets what
    differs; an original that inherits nothing gets its whole table written."""
    out = StatsFiles()
    new_name = mode == new_mode
    from_original = bool(getattr(bp, "stats_base", False)) and new_name
    edits = {r.key: clean(r.new_value) for r in edited_rows(bp)}
    PB = _pb()
    lib = _lib()
    for key, value in edits.items():                                   # a number the game would cut, or cannot use (ICParse.ebp.stats.GAME_RANGES)
        why = lib.stat_hint(key, value, None)
        if why:
            out.notes.append(why)
    if not edits and not from_original:
        return out
    resolver = None
    try:
        resolver = PB._addon("core").scene_texture_source(os.path.dirname(ent.source), data_root=PB.game_dir()[0])
    except Exception:                                                  # noqa: BLE001
        resolver = None
    cls = int(bp.class_id)
    target = f"data:art/ebps/{bp.folder + '/' if bp.folder else ''}{PB.default_name(ent)}.ebp"
    cd = class_default(lib, target, cls, resolver)
    original = {r.key: clean(r.orig) for r in bp.stats if not r.added}
    own = bool(getattr(bp, "stats_own", True))
    view = None
    rt = original_target(ent)
    if new_name and not from_original and cd is not None:
        original, own = dict(cd), False                                # a new name starts from the default of its kind
    elif rt is not None and resolver is not None and (not new_name or from_original):
        ref = lib.stats_candidates(_target_ref(rt), cls)[0]
        try:
            if resolver.exists(ref):
                view = lib.read_stats(ref, resolver)
                original, own = {k: clean(v) for k, v in view.attrs.items()}, True
                if view.unsupported:
                    out.notes.append(f"{len(view.unsupported)} line(s) of the original's stats file could not be understood, so what they set is not "
                                     f"repeated and the new file may play differently from the original.")
                if view.errors:
                    out.notes.append("The original's stats file stops with an error, so the game gives the original no numbers at all; the new "
                                     "file gives this thing the numbers listed here.")
                if view.non_numeric:
                    out.notes.append(f"{len(view.non_numeric)} entry(ies) of the original's stats file are not plain numbers and are not repeated.")
            elif cd is not None:
                original, own = dict(cd), False                          # the original has none of its own: it has the default of its kind
        except Exception:                                              # noqa: BLE001 -- the list's numbers stand
            view = None
    base_ref, base_nums, standalone = None, cd, False
    if own and view is not None:
        kind, bref, bnums = _inherited(lib, view, lib.stats_candidates(target, cls)[1], resolver)
        if kind == "base":
            base_ref, base_nums = bref, bnums
        elif kind == "none":
            base_nums, standalone = None, True
    es = lib.entity_overrides(cls, original, base_nums, edits, original_is_own=own, force=from_original, base=base_ref)
    if es.text is None:
        return out
    rel = ebp_rel[:-4] + ".lua"
    out.files[rel] = es.text.encode("latin-1")
    out.script, out.edits, out.carried = es.text, es.edits, es.carried
    leaf = os.path.basename(rel)
    if es.full and standalone:
        out.notes.append(f"The original's stats file builds on no other file, so {leaf} holds the whole table of numbers.")
    elif es.full:
        out.notes.append(f"The kind of thing (class {cls}) has no default stats file in the game, so {leaf} holds the whole table of numbers.")
    if es.carried:
        out.notes.append(f"{leaf} also repeats {len(es.carried)} number(s) the original's own stats file sets, so this "
                         f"thing plays like the original apart from what you changed.")
    if base_ref:
        out.notes.append(f"{leaf} builds on {os.path.basename(base_ref)}, as the original's stats file does.")
    if base_nums is None and not es.full:
        out.notes.append("The default numbers of this kind of thing could not be read, so the stats file was written without them.")
    return out


# ---------------------------------------------------------------------------------------------------------------- operators
def _entity_bp(context):
    PB = _pb()
    ent = PB.entity_of(context.active_object)
    bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
    return ent, bp


class BZ_OT_ent_stat_reset(bpy.types.Operator):
    """Put this number back to what the game's files give"""
    bl_idname = "bz.ent_stat_reset"
    bl_label = "Put Back"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    key: StringProperty()

    @classmethod
    def poll(cls, context):
        _ent, bp = _entity_bp(context)
        return bp is not None and bp.loaded

    def execute(self, context):
        _ent, bp = _entity_bp(context)
        for i, r in enumerate(bp.stats):
            if r.key == self.key:
                if r.added:
                    bp.stats.remove(i)
                else:
                    r.new_value = r.orig
                return {"FINISHED"}
        return {"CANCELLED"}


class BZ_OT_ent_stat_reset_all(bpy.types.Operator):
    """Put every number back to what the game's files give"""
    bl_idname = "bz.ent_stat_reset_all"
    bl_label = "Put All Back"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        _ent, bp = _entity_bp(context)
        return bp is not None and bp.loaded and bool(edited_rows(bp))

    def execute(self, context):
        _ent, bp = _entity_bp(context)
        for i in range(len(bp.stats) - 1, -1, -1):
            r = bp.stats[i]
            if r.added:
                bp.stats.remove(i)
            else:
                r.new_value = r.orig
        return {"FINISHED"}


class BZ_OT_ent_stat_add(bpy.types.Operator):
    """Add a number the thing does not have yet (the game reads it by its exact name)"""
    bl_idname = "bz.ent_stat_add"
    bl_label = "Add a Number"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(name="Name", description="The number's name as the game reads it, such as hitpoints or sight_radius1 (letters, digits and _)")
    value: FloatProperty(name="Value", default=0.0)

    @classmethod
    def poll(cls, context):
        _ent, bp = _entity_bp(context)
        return bp is not None and bp.loaded and bp.state in _pb().EDITABLE

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        _ent, bp = _entity_bp(context)
        key = self.key.strip()
        if not NAME_OK.match(key):
            self.report({"ERROR"}, "A number's name uses letters, digits and _ only, and does not start with a digit.")
            return {"CANCELLED"}
        if any(r.key == key for r in bp.stats):
            self.report({"ERROR"}, f"{key} is in the list already: change it there.")
            return {"CANCELLED"}
        r = bp.stats.add()
        r.key, r.value, r.origin = key, f"{self.value:g}", "added"
        r.orig, r.new_value, r.added = 0.0, float(self.value), True
        r.has_dflt, r.dflt = False, 0.0
        why = _lib().stat_hint(key, self.value, None)
        self.report({"INFO"} if not why else {"WARNING"}, why or f"Added {key}. Export Entity Folder writes it into the stats file.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------------- drawing
def _row_hint(r):
    lib = _lib()
    return lib.stat_hint(r.key, clean(r.new_value), r.dflt if r.has_dflt else None)


def _draw_number(layout, r, editable: bool, label=None):
    lib = _lib()
    col = layout.column(align=True)
    row = col.row(align=True)
    row.enabled = editable
    row.prop(r, "new_value", text=label or lib.stat_name(r.key))
    if is_edited(r):
        row.operator("bz.ent_stat_reset", text="", icon="LOOP_BACK").key = r.key
        why = _row_hint(r)
        if why:
            PB = _pb()
            PB._alert(col, why, icon="INFO")
        elif r.has_dflt:
            PB = _pb()
            PB._hint(col, f"Was {clean(r.orig):g}; the usual for this kind of thing is {clean(r.dflt):g}.")


def draw_edit(layout, bp, editable: bool, ent=None) -> None:
    """The editable list under the Blueprint's gameplay numbers (called by ``panels_blueprint.draw_stats``)."""
    PB = _pb()
    lib = _lib()
    if not len(bp.stats):
        if editable:
            layout.operator("bz.ent_stat_add", icon="ADD")
        return
    PB._hint(layout, "Change a number and Export Entity Folder writes a stats file (.lua) with the changes. Every player in a multiplayer game needs the same numbers.")
    rt = original_target(ent) if ent is not None else None
    if rt is not None:
        col = layout.column()
        col.prop(bp, "stats_base", text=f"Use the numbers of {rt.name}")
        if bp.stats_base:
            PB._hint(layout, f"These are the numbers of {rt.name}. Replace the original starts from them. A new name starts from them too, and its "
                             f"stats file is written even when you change nothing.")
        else:
            PB._hint(layout, f"These are the numbers the game gives a thing with a name of its own: the default numbers of its kind. A new name starts "
                             f"from them. Replace the original starts from the numbers of {rt.name} whatever is ticked here; tick the box to see them.")
    by = {r.key: r for r in bp.stats}
    common = [by[k] for k in lib.STAT_FIRST if k in by]
    if common:
        box = layout.box()
        box.label(text="Common numbers")
        for r in common:
            _draw_number(box, r, editable)
    box = layout.box()
    box.label(text="All numbers")
    box.prop(bp, "stats_filter", text="", icon="VIEWZOOM")
    text = bp.stats_filter.strip().lower()
    shown = [r for r in bp.stats if not text or text in r.key.lower() or text in lib.stat_name(r.key).lower()]
    for r in shown[:LIST_MAX]:
        _draw_number(box, r, editable, label=r.key)
    if len(shown) > LIST_MAX:
        box.label(text=f"{len(shown) - LIST_MAX} more: type part of a name to find them")
    elif not shown:
        box.label(text="No number has that in its name")
    box.operator("bz.ent_stat_add", icon="ADD")
    n = len(edited_rows(bp))
    row = layout.row(align=True)
    row.label(text=f"{n} number{'s' if n != 1 else ''} changed" if n else "No number changed: no stats file is written")
    row.operator("bz.ent_stat_reset_all", text="", icon="LOOP_BACK")


CLASSES = (BZ_OT_ent_stat_reset, BZ_OT_ent_stat_reset_all, BZ_OT_ent_stat_add)
