"""Creature Stats: the numbers a creature plays by (hit points, armour, speed, sight, damage, abilities), edited in Blender and written by
Export Creature for the Game.

**Where the numbers come from.** A creature's numbers are the ``limbattributes`` table of its ``<name>.lua``; the game reads the ``.lua`` with
the blueprint's own name (``decomp/ic/src/EntityBlueprint.cpp:2196-2201``, ``:2227-2243``; the rest of the pipeline is in ``statscore.py``).
The panel starts from the DONOR's ``.lua`` (the one beside the project's model, else the one the model was imported from, else the stock animal
the project was started from: ``gameexport.find_donor``; a creature with no donor starts from the stock animal Export Creature for the Game
names): never a made-up baseline, which the port's checker refuses. The numbers are kept as rows on the creature (``Object.bz_stats``), so they
are saved in the .blend; **Export Creature for the Game** applies them to the donor's file in place (``ICParse.lua.statsheet``: every other byte of
the file stays) and the hub's checker holds each number to the game's own animals' ranges.

**What the game makes of them** is shown live (``statscore.derive``): rank, cost, build time and population, by the ``attrcombiner.lua`` of the game
build chosen in the panel (the original game's is in ``Engine.sga``; Insect Invasion and Tellurian have their own).

The panel is in the IC Game tab and shows only for a creature (a scene object has its numbers in the Blueprint panel).
"""
from __future__ import annotations

import hashlib
import os
from collections import OrderedDict

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty

from . import combocore
from . import statscore as SC
from . import store
from .sceneobj import ui_kit

KEY_STATS_FROM = "bz_game_stats_from"
SEARCH_ROWS = 30
LIST_PRECISION = 3


def _sheet_lib():
    from ICParse.lua import statsheet
    return statsheet


# ---------------------------------------------------------------------------------------------------------------- the property groups
_RULE_ITEMS: list = []


def _rules_enum_items(self, context):
    return _rules_items()


class BZ_PG_stat_row(bpy.types.PropertyGroup):
    """One number of the creature's stats file."""
    key: StringProperty()
    group: FloatProperty(description="The body part the number belongs to (as the game's file writes it)")
    value: FloatProperty(name="Value", precision=LIST_PRECISION, description="The creature's number")
    original: FloatProperty(description="The original's number")
    kind: StringProperty(default="number")                          # number, flag, enum or text
    has_range: BoolProperty(default=False)
    lo: FloatProperty()
    hi: FloatProperty()
    has_stock: BoolProperty(default=False)
    stock_lo: FloatProperty()                                       # what the game's own animals use
    stock_hi: FloatProperty()
    absent: BoolProperty(default=False, description="The original's file does not have this ability. Switching it on adds it to the creature's stats file")

    def _get_on(self):
        return self.value != 0.0

    def _set_on(self, v):
        self.value = 1.0 if v else 0.0

    on: BoolProperty(get=_get_on, set=_set_on, description="Switch on or off")


class BZ_PG_stats(bpy.types.PropertyGroup):
    loaded: BoolProperty(default=False)
    source: StringProperty(default="", description="Which file the numbers were read from, in one plain line")
    stamp: StringProperty(default="")
    rows: CollectionProperty(type=BZ_PG_stat_row)
    search: StringProperty(name="Search", default="", options={"TEXTEDIT_UPDATE"},
                           description="Part of a number's name, such as armour, melee or torso")
    rules: EnumProperty(name="Game build", items=_rules_enum_items, description=(
        "Which game build's price script works out the rank, cost and build time shown here: the original game, Insect Invasion or Tellurian "
        "each price a creature their own way. The numbers are written the same whatever you choose; only what this page shows changes"))


def _rules_items():
    """The game builds installed with a price script, as Blender enum items (kept alive in a module list: Blender does not copy the strings)."""
    items = [(r.key, r.name, f"Worked out with {r.where}") for r in SC.rule_sets_cached(_game_dir())]
    _RULE_ITEMS[:] = items or [("none", "No game found", "Set the Game folder in the add-on's preferences")]
    return _RULE_ITEMS


def _game_dir():
    try:
        return combocore.install_root() or None
    except Exception:                                               # noqa: BLE001 -- no combiner library: no game folder either
        return None


def _stock():
    from . import gameexport
    return gameexport._stock()


# ---------------------------------------------------------------------------------------------------------------- reading the donor
class Unavailable(Exception):
    """The numbers cannot be loaded (plain words)."""


def base_numbers(obj):
    """``(lua bytes, plain line)``: the file the creature's numbers start from, the way Export Creature for the Game finds it."""
    from . import gameexport
    from ICParse.hub import package as PK
    donor = gameexport.find_donor(obj)
    if donor is not None:
        if donor.lua is None:
            raise Unavailable(f"The original animal {donor.label} has no numbers file, so there is nothing to start from.")
        return donor.lua, f"{donor.label}.lua ({donor.origin})"
    name = stats_from_of(obj) or PK.DEFAULT_STATS_FROM
    stock = PK.stock_donor(name, "Engine.sga", _stock().game)
    if stock is None or stock.lua is None:
        raise Unavailable(f"The game's {name} was not found, so there is no stats file to start from. Set the Game folder in the add-on's preferences.")
    return stock.lua, f"the game's {name}.lua (this creature was built from nothing, so it starts from this animal)"


def _stamp(lua: bytes) -> str:
    return hashlib.sha1(lua).hexdigest()[:12]


def load_rows(obj) -> int:
    """Fill ``obj.bz_stats`` from the donor's file; edits made before are dropped. Returns the number of rows."""
    lib = _sheet_lib()
    lua, where = base_numbers(obj)
    try:
        sheet = lib.StatsSheet.from_lua(lua, lib.StockRules(_stock()))
    except ValueError as e:
        raise Unavailable(f"The stats file {where} is not a creature's stats file ({e}).") from None
    pg = obj.bz_stats
    pg.rows.clear()

    def add(key, row, absent):
        r = pg.rows.add()
        r.key, r.group, r.value, r.original = key, float(row.group), float(row.value), float(row.value if not absent else 0.0)
        r.kind, r.absent = row.kind, absent
        r.has_range = row.lo is not None and row.hi is not None
        if r.has_range:
            r.lo, r.hi = float(row.lo), float(row.hi)
        r.has_stock = row.stock_lo is not None and row.stock_hi is not None
        if r.has_stock:
            r.stock_lo, r.stock_hi = float(row.stock_lo), float(row.stock_hi)
    for row in sheet.rows.values():
        add(row.key, row, False)
    for key, _name, row in sheet.abilities():
        if row is None:
            sheet.add(key, 0.0)
            add(key, sheet.rows[key], True)
            sheet.reset(key)
    pg.loaded = True
    pg.source = where
    pg.stamp = _stamp(lua)
    return len(pg.rows)


def _sync_rules(pg, context=None):
    """Start the rules choice at the add-on's Game build preference."""
    items = {i[0] for i in _rules_items()}
    if not items or "none" in items:
        return
    try:
        from .sceneobj import prefs
        build = str(prefs.get(context).target_mod)
    except Exception:                                               # noqa: BLE001
        build = "icmod"
    want = SC.BUILD_TO_MODULE.get(build, "ic")
    pg.rules = want if want in items else ("ic" if "ic" in items else sorted(items)[0])


def _row_view(r):
    lib = _sheet_lib()
    return lib.Row(r.key, r.group, r.value, r.original, r.kind, r.lo if r.has_range else None, r.hi if r.has_range else None,
                   r.stock_lo if r.has_stock else None, r.stock_hi if r.has_stock else None)


def row_problem(r):
    """What is wrong with this row's number in plain words, or None (a number nobody touched is the game's own and never a problem)."""
    if r.kind == "text" or r.value == r.original:
        return None
    return _row_view(r).problem()


def clean(v: float) -> float:
    """A number as the modder meant it: a Blender float property holds 32 bits (0.2 reads back as 0.20000000298), the stats file and the game's
    own float calculation want the short number (seven digits)."""
    return float(f"{float(v):.7g}")


def changed_rows(pg):
    return [r for r in pg.rows if r.value != r.original]


def entries_of(pg) -> "OrderedDict":
    """The table as the game calculates over it: ``{key: [group, value]}`` (an ability the donor lacks counts only when it is on)."""
    out = OrderedDict()
    for r in pg.rows:
        if r.absent and r.value == 0.0:
            continue
        out[r.key] = [r.group, clean(r.value)]
    return out


# ---------------------------------------------------------------------------------------------------------------- what Export Creature for the Game takes
class ExportStats:
    """The numbers for the export: ``stats`` (changed keys the donor has), ``stats_add`` (abilities it lacks), plain-words ``problems`` that stop
    the export, ``notes`` for the report, and the worked-out ``derived`` numbers."""

    def __init__(self):
        self.stats: dict = {}
        self.stats_add: dict = {}
        self.problems: list = []
        self.notes: list = []
        self.derived = None
        self.changes: list = []


def export_stats(obj, base_lua: bytes, stock) -> ExportStats:
    """Apply the creature's edited rows to the file the export starts from (``base_lua``), in the sheet's words. Nothing loaded or nothing
    changed gives an empty result (the donor's file is copied byte for byte)."""
    res = ExportStats()
    pg = getattr(obj, "bz_stats", None)
    if pg is None or not pg.loaded:
        return res
    lib = _sheet_lib()
    sheet = lib.StatsSheet.from_lua(base_lua, lib.StockRules(stock))
    if _stamp(base_lua) != pg.stamp:
        res.notes.append("The numbers in the Creature Stats panel were loaded from another file than the export starts from now; "
                         "the numbers you changed are applied by name. Press Load Numbers in the panel to start again from this file.")
    for r in changed_rows(pg):
        if r.kind == "text":
            continue
        if r.key not in sheet and not sheet.can_add(r.key):
            res.notes.append(f"{lib.label_of(r.key)} is not in this file's numbers, so your change to it was left out.")
            continue
        try:
            sheet.set(r.key, clean(r.value))
        except ValueError as e:
            res.problems.append(str(e))
    res.problems += sheet.problems()
    res.stats = {k: row.value for k, row in sheet.rows.items() if row.changed and not row.added}
    res.stats_add = {k: (row.group, row.value) for k, row in sheet.rows.items() if row.added}
    for k, row in sheet.rows.items():
        if row.changed:
            before = "off" if row.added else f"{row.original:g}"
            res.changes.append(f"{lib.label_of(k)}: {before} -> {row.value:g}")
    rules = _chosen_rules(pg)
    if rules is not None:
        res.derived = SC.derive(sheet.entries(), rules)
    return res


def export_numbers(obj, donor, stats_from: str, stock) -> ExportStats:
    """:func:`export_stats` over the file Export Creature for the Game starts from: the donor's ``.lua``, else the stock animal ``stats_from``."""
    pg = getattr(obj, "bz_stats", None)
    if pg is None or not pg.loaded:
        return ExportStats()
    if donor is not None and donor.lua is not None:
        base = donor.lua
    else:
        from ICParse.hub import package as PK
        base_donor = PK.stock_donor(stats_from or PK.DEFAULT_STATS_FROM, "Engine.sga", stock.game)
        if base_donor is None or base_donor.lua is None:
            return ExportStats()                                    # the export says the animal is missing
        base = base_donor.lua
    return export_stats(obj, base, stock)


def _chosen_rules(pg):
    sets = SC.rule_sets_cached(_game_dir())
    for r in sets:
        if r.key == pg.rules:
            return r
    return SC.pick_rules(sets)


# ---------------------------------------------------------------------------------------------------------------- operators
def _creature_poll(context):
    obj = store.active_creature(context)
    return obj is not None and hasattr(obj, "bz_stats")


class BZ_OT_stats_load(bpy.types.Operator):
    """Read the numbers of the animal this creature was started from. This replaces any number you changed here"""
    bl_idname = "bz.stats_load"
    bl_label = "Load the Original's Numbers"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _creature_poll(context)

    def invoke(self, context, event):
        pg = store.active_creature(context).bz_stats
        if pg.loaded and changed_rows(pg):
            return context.window_manager.invoke_confirm(self, event, title="Load the numbers again?",
                                                         message="The numbers you changed here are replaced by the original's.")
        return self.execute(context)

    def execute(self, context):
        obj = store.active_creature(context)
        try:
            n = load_rows(obj)
        except Unavailable as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:                                      # noqa: BLE001 -- a broken game folder or file is a plain message
            self.report({"ERROR"}, f"The numbers could not be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        _sync_rules(obj.bz_stats, context)
        self.report({"INFO"}, f"Loaded {n} numbers from {obj.bz_stats.source}")
        return {"FINISHED"}


class BZ_OT_stats_reset(bpy.types.Operator):
    """Put this number back to the original's"""
    bl_idname = "bz.stats_reset"
    bl_label = "Put Back"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    key: StringProperty()

    @classmethod
    def poll(cls, context):
        return _creature_poll(context)

    def execute(self, context):
        pg = store.active_creature(context).bz_stats
        for r in pg.rows:
            if r.key == self.key:
                r.value = r.original
                return {"FINISHED"}
        return {"CANCELLED"}


class BZ_OT_stats_reset_all(bpy.types.Operator):
    """Put every number back to the original's"""
    bl_idname = "bz.stats_reset_all"
    bl_label = "Put All Back"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _creature_poll(context) and bool(changed_rows(store.active_creature(context).bz_stats))

    def execute(self, context):
        for r in store.active_creature(context).bz_stats.rows:
            r.value = r.original
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------------- drawing
_DERIVED: dict = {}                     # (game folder, rules key, numbers) -> Derived: a redraw does not run the script again


def derived_for(pg):
    rules = _chosen_rules(pg)
    if rules is None:
        return None
    entries = entries_of(pg)
    key = (_game_dir(), rules.key, tuple((k, v[0], v[1]) for k, v in entries.items()))
    d = _DERIVED.get(key)
    if d is None:
        if len(_DERIVED) > 64:
            _DERIVED.clear()
        d = _DERIVED[key] = SC.derive(entries, rules)
    return d


def _label(key):
    return _sheet_lib().label_of(key)


def _draw_row(layout, r, label=None):
    row = layout.row(align=True)
    row.prop(r, "value", text=label or _label(r.key))
    changed = r.value != r.original
    if changed:
        op = row.operator("bz.stats_reset", text="", icon="LOOP_BACK")
        op.key = r.key
    why = row_problem(r)
    if why:
        ui_kit.alert(layout, why, width=44)


_QUIET: list = []                                                   # set while the add-on itself writes the choice (no reload then)


def stats_from_of(obj) -> str:
    """The stock animal a donor-less creature starts from ('' when none was chosen). Blender 5 keeps a property made with ``bpy.props`` out of
    the object's ``obj["name"]`` store, so the property itself is read first; ``obj[...]`` is only the fallback for a file made before."""
    try:
        v = getattr(obj, KEY_STATS_FROM, "")
    except Exception:                                               # noqa: BLE001
        v = ""
    return str(v or obj.get(KEY_STATS_FROM) or "")


def set_stats_from(obj, name: str) -> None:
    """Remember the animal the creature starts from without reading the numbers again (the export just used it)."""
    if stats_from_of(obj) == name:
        return
    _QUIET.append(True)
    try:
        setattr(obj, KEY_STATS_FROM, name)
    except Exception:                                               # noqa: BLE001
        obj[KEY_STATS_FROM] = name
    finally:
        _QUIET.pop()


def _stats_from_changed(self, context) -> None:
    """The modder chose another animal to start from: the numbers and the events are read again from that animal (numbers edited before are
    dropped: they were changes to the other animal's numbers). Nothing happens before the first load: the panel reads it when it opens."""
    if _QUIET:
        return
    try:
        if self.bz_stats.loaded:
            load_rows(self)
            _sync_rules(self.bz_stats, context)
        from . import fxpanel
        fxpanel.reload_events_of(self)
    except Exception:                                               # noqa: BLE001 -- the Load buttons of the panels say why
        pass


def _load_by_name(name: str) -> None:
    """Read the numbers of the creature called ``name`` (the panel's first drawing asks for it)."""
    obj = bpy.data.objects.get(name)
    if obj is not None and hasattr(obj, "bz_stats") and not obj.bz_stats.loaded:
        load_rows(obj)
        _sync_rules(obj.bz_stats, bpy.context)


def draw_stats(layout, context):
    obj = store.active_creature(context)
    pg = obj.bz_stats
    lib = _sheet_lib()
    try:
        from . import gameexport
        no_donor = gameexport.find_donor(obj) is None
    except Exception:                                                   # noqa: BLE001 -- no game folder: the Load button says why
        no_donor = False
    if no_donor:                                                        # the animal the numbers start from (also asked in the export dialog)
        layout.prop(obj, KEY_STATS_FROM, text="Start from animal")
    if not pg.loaded:
        from . import autoload
        name = obj.name
        autoload.once(("stats", obj.session_uid), lambda: _load_by_name(name))
        col = layout.column(align=True)
        for line in ui_kit.wrap_lines("A new creature starts with the numbers of the animal you began with (hit points, armour, speed, attacks, abilities). "
                                      "If nothing shows, press Load Numbers.", 44):
            col.label(text=line)
        layout.operator("bz.stats_load", icon="IMPORT")
        return
    layout.label(text=f"From {pg.source}", icon="TEXT")
    changed = changed_rows(pg)
    # ---- what the game makes of the numbers
    box = layout.box()
    box.label(text="What the game works out", icon="INFO")
    box.prop(pg, "rules", text="Game build")
    d = derived_for(pg)
    if d is None:
        ui_kit.alert(box, "No game was found, so rank and cost cannot be worked out. Set the Game folder in the add-on's preferences.", icon="INFO", width=44)
    elif not d.ok:
        ui_kit.alert(box, d.error, icon="ERROR", width=44)
    else:
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
        grid.label(text=f"Rank {d.rank_text}")
        grid.label(text=f"Population {d.population:g}")
        grid.label(text=f"Cost {d.cost:g} coal")
        grid.label(text=f"{d.cost_renew:g} electricity")
        grid.label(text=f"Build {d.build_seconds:.0f} s")
        grid.label(text=f"({d.build_ticks:g} ticks)")
        grid.label(text=f"Hit points {d.hitpoints:g}")
        grid.label(text=f"With armour {d.ehp:.0f}")
        if d.power is not None:
            box.label(text=f"Power {d.power:.1f}")
        for line in ui_kit.wrap_lines(f"Worked out with {d.rules.where}, the game's own rules for {d.rules.name}. "
                                      "A creature combined with another is priced from both: see Combine Preview.", 44):
            box.label(text=line)
        for n in d.notes:
            for line in ui_kit.wrap_lines(n, 44):
                box.label(text=line)
    # ---- the common numbers
    by_key = {r.key: r for r in pg.rows}
    for section in ("health", "move", "senses"):
        keys = [k for k in lib.COMMON_KEYS if lib.section_of(k) == section and k in by_key]
        if not keys:
            continue
        col = layout.box().column(align=True)
        col.label(text=lib.SECTIONS[section])
        for k in keys:
            _draw_row(col, by_key[k])
        if section == "move":
            for line in SC.speed_lines({r.key: r.value for r in pg.rows}):
                for part in ui_kit.wrap_lines(line, 44):
                    col.label(text=part)
    attack = [r for r in pg.rows if lib.section_of(r.key) == "attack" and r.kind not in ("text", "enum")
              and r.key.rsplit("_", 1)[-1] in ("damage", "rate", "contact", "max", "min")]
    if attack:
        col = layout.box().column(align=True)
        col.label(text=lib.SECTIONS["attack"])
        for r in attack:
            _draw_row(col, r)
    abil = [by_key[k] for k in lib.ABILITY_KEYS if k in by_key]
    if abil:
        box = layout.box()
        box.label(text=lib.SECTIONS["ability"])
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
        for r in abil:
            grid.prop(r, "on", text=_label(r.key), toggle=True)
    # ---- every other number
    box = layout.box()
    box.label(text="All numbers")
    box.prop(pg, "search", text="", icon="VIEWZOOM")
    text = pg.search.strip().lower()
    shown = [r for r in pg.rows if (not text or text in r.key.lower() or text in _label(r.key).lower()) and r.kind != "text"]
    for r in shown[:SEARCH_ROWS]:
        _draw_row(box, r, label=r.key)
    if len(shown) > SEARCH_ROWS:
        box.label(text=f"{len(shown) - SEARCH_ROWS} more: type part of a name to find them")
    elif not shown:
        box.label(text="No number has that in its name")
    if len(shown) == 1:
        r = shown[0]
        view = _row_view(r)
        info = lib.explain(r.key)
        if view.kind == "flag":
            more = "On (1) or off (0)."
        else:
            use, ok = view.animals_text(), view.range_text()
            more = (f"The game's own animals use {use}" + (f"; the export accepts {ok}." if ok and view.kind == "number" else ".")) if use else ""
        for line in ui_kit.wrap_lines(" ".join(x for x in (info, more) if x), 44):
            box.label(text=line)
    # ---- what will happen at export
    probs = [p for p in (row_problem(r) for r in changed) if p]
    row = layout.row(align=True)
    row.label(text=f"{len(changed)} number{'s' if len(changed) != 1 else ''} changed" if changed else "No number changed: the original's file is copied as it is")
    row.operator("bz.stats_reset_all", text="", icon="LOOP_BACK")
    if probs:
        ui_kit.alert(layout, f"{len(probs)} number{'s are' if len(probs) != 1 else ' is'} outside what the export accepts: Export Creature for the Game will refuse them.", width=44)
    row = layout.row()
    row.operator("bz.stats_load", text="Load Numbers Again", icon="FILE_REFRESH")


class BZ_PT_stats(bpy.types.Panel):
    bl_label = "Creature Stats"
    bl_idname = "BZ_PT_stats"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 40
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _creature_poll(context)

    def draw(self, context):
        draw_stats(self.layout, context)


CLASSES = (BZ_PG_stat_row, BZ_PG_stats, BZ_OT_stats_load, BZ_OT_stats_reset, BZ_OT_stats_reset_all, BZ_PT_stats)


def register():
    """Point ``Object.bz_stats`` at the state (the classes are registered by the add-on's ``_CLASSES``)."""
    bpy.types.Object.bz_stats = PointerProperty(type=BZ_PG_stats)
    bpy.types.Object.bz_game_stats_from = StringProperty(
        name="Start from animal", default="",
        description="A creature built from nothing starts its stats, limb scales and events from this stock animal (empty: the cheetah). "
                    "Choosing another animal reads its numbers and events again (numbers you changed are dropped)",
        update=_stats_from_changed)


def unregister():
    if hasattr(bpy.types.Object, "bz_stats"):
        del bpy.types.Object.bz_stats
    if hasattr(bpy.types.Object, "bz_game_stats_from"):
        del bpy.types.Object.bz_game_stats_from
