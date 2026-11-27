"""Effects panel and Events box (IC Game tab of the 3D view sidebar): the events of the selected thing's blueprint, the effects they start, and an editor for
those effects.

In the game an effect is a Lua file under ``art/fx`` (read by ``FXLoad``, FXLoad.cpp:336) that something starts by name: an event on a blueprint
(``RenderModelEventSink::OnEvent``, RenderModelEventSink.cpp:633-760), another effect (a combo, a conditional or a spray's particle) or a map
trigger. This panel works on the first two ways:

* **Events** (the box): the blueprint's events per clip, each a name and fields -- clip (``anim``), time (``animtime``, 0 to 1 along the clip, from
  the playhead), marker, effect (``fx``), sound -- and every other field as a raw row, kept as it is. Edits are kept in the scene and written into
  the ``.ebp`` by Export Entity Folder, which changes only the events that were edited (``ICParse.ebp.events``).
* **Effects**: the effects those events use (and the effects they start, down to the pictures and meshes), where each one is (the project, the
  game, or missing), Copy to my project (a copy of a game effect with a new name prefix, so editing it does not change the game's own), New effect
  (from the game's defaults), Duplicate, and the editor: one row per property with its type (number, whole number, on/off, colour, picture,
  mesh, blend mode, effect name), the game's range, and key frames (add, remove, move). Every edit is written to the effect's file in the project's
  ``<name>_fx/data/art/fx`` folder at once, refused when the game would ignore it, and Export Entity Folder copies the folder into
  ``<export>/data``.

The logic is in ``fxcore.py`` (no Blender) and the rules in ``ICParse/fx`` and ``ICParse/ebp/events.py``.
"""
from __future__ import annotations

import os
import textwrap

import bpy
from .sceneobj import ui_kit
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import fxcore
from ICParse.fx import fxlua, fxschema

BLEND_ITEMS = (("0", "None", "Opaque"), ("1", "Alpha", "Alpha blended"), ("2", "Additive", "Added to what is behind"),
               ("3", "Multiply", "Multiplied with what is behind"))
DIRECTION_ITEMS = (("0", "Normal", "Along the marker's Y axis"), ("1", "Up", ""), ("2", "Down", ""))
DYNAMICS_ITEMS = (("0", "Forward", ""), ("1", "Gravity (die)", ""), ("2", "Gravity (bounce)", ""), ("3", "Gravity (stick)", ""), ("4", "Wind", ""),
                  ("5", "Water", ""), ("6", "Local", ""), ("7", "Land", ""), ("8", "Gravity (ignore)", ""), ("9", "Surface", ""))
_ENUM_PROP = {"Blending": "blend", "Emitter_Direction": "direction", "Particle_Dynamics": "dynamics"}
KNOWN = ("anim", "animtime", "marker", "fx", "sound")           # the event fields with a row of their own
_BUSY = [False]                                                  # set while rows are being filled from a file (their update callbacks do nothing)
_KEEP: list = []                                                 # enum items must stay referenced while Blender shows them


# ---------------------------------------------------------------------------------------------------------- reaching the entity
def _pb():
    from .sceneobj import panels_blueprint
    return panels_blueprint


class CreatureEntity:
    """What the Events box needs of a creature, under the same names a scene object's entity has (``parts``, ``holder``, ``source``, ``rig``).
    A creature is not a scene object, but its blueprint holds events in the same way (``ICParse.ebp.events``)."""
    creature = True

    def __init__(self, obj):
        from . import store
        self.parts = [obj]
        self.holder = obj
        self.source = store.source_path(obj)
        self.rig = store.rig(obj)


def creature_of(obj):
    """The creature (the mesh the add-on made) that ``obj`` is, or the one whose rig ``obj`` is; None for anything else."""
    from . import store
    if obj is None:
        return None
    if store.is_native(obj) and not obj.get("bz_scene"):
        return obj
    if obj.type == "ARMATURE":
        owners = [o for o in bpy.data.objects if store.is_native(o) and not o.get("bz_scene") and store.rig(o) == obj]
        if len(owners) == 1:
            return owners[0]
    return None


def is_creature(ent) -> bool:
    return bool(getattr(ent, "creature", False))


def _entity(context=None, obj=None):
    obj = obj if obj is not None else (context.active_object if context is not None else None)
    ent = _pb().entity_of(obj)
    if ent is None:
        c = creature_of(obj)
        ent = CreatureEntity(c) if c is not None else None
    return ent


def _state(ent):
    return getattr(ent.holder, "bz_fx", None) if ent is not None else None


def _game_dir() -> str:
    return _pb().game_dir()[0] or ""


def _source(ent) -> str:
    return ent.source


def _is_effect_model(ent) -> bool:
    return bool(ent is not None and _pb()._stamp(ent, "bz_project") == "EFFECT")


def clip_names(ent) -> list:
    """The clips of the entity as the export will write them: the rig's Actions by the names the export gives them (a renamed, borrowed or
    new Action is named by its own name). The same list the Motions box shows, for a creature and for a scene object."""
    out = []
    if ent is not None and ent.rig is not None:
        from . import actions as bzactions
        try:
            plan = bzactions.plan_export(ent.rig, renames=is_creature(ent))
            return list(dict.fromkeys(list(plan.kept) + [c for _a, c in plan.encode]))
        except Exception:                                                 # noqa: BLE001 -- two Actions with one name and the like: Check says so
            return list(dict.fromkeys(str(a.get(bzactions.KEY_CLIP) or a.name) for a in bzactions.actions_of(ent.rig)))
    return out


def clip_range(ent, clip: str, scene):
    """``(first frame, last frame)`` of a clip's keys, else the scene's frame range. A clip is found by the name the export gives it
    (an Action made or borrowed in Blender has no stamped clip name: its own name is the clip's)."""
    if ent is not None and ent.rig is not None and clip:
        from . import actions as bzactions
        want = clip.lower()
        acts = []
        try:
            acts = [(a, str(c)) for a, c in bzactions.plan_export(ent.rig, renames=is_creature(ent)).encode]
        except Exception:                                                 # noqa: BLE001
            pass
        acts += [(a, str(a.get(bzactions.KEY_CLIP) or a.name)) for a in bzactions.actions_of(ent.rig)]
        for a, target in acts:
            if want in (target.lower(), str(a.get(bzactions.KEY_CLIP, "")).lower(), a.name.lower()):
                try:
                    lo, hi = bzactions.frame_range(a)
                    return float(lo), float(hi)
                except Exception:                                         # noqa: BLE001
                    break
    return float(scene.frame_start), float(scene.frame_end)


def marker_names(ent) -> list:
    """Markers of the model (the Markers panel's), then the bones of its skeleton: the names an event's ``marker`` can hold (the game looks a
    marker up first and then a bone, RenderModelEventSink.cpp:650-657 via GetMarkerIndex)."""
    out = []
    try:
        from .sceneobj import slice_markers as SM
        out += [SM.marker_name(o) for o in SM.markers_of(SM.entity_key(ent.parts[0]))]
    except Exception:                                                     # noqa: BLE001
        pass
    if ent is not None and ent.rig is not None:
        out += [b.name for b in ent.rig.data.bones if b.name not in out]
    return out


# ---------------------------------------------------------------------------------------------------------- property groups
def _unbusy():
    _BUSY[0] = False


class BZ_PG_fx_pair(bpy.types.PropertyGroup):
    key: StringProperty(name="Field")
    value: StringProperty(name="Value")


class BZ_PG_fx_event(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", description="The event's name (a label: the game does not read it)")
    src: IntProperty(default=-1, description="Where the event was in the blueprint file; -1 for one added here")
    pairs: CollectionProperty(type=BZ_PG_fx_pair)


def _row_edit(self, context):
    if _BUSY[0]:
        return
    ob = self.id_data
    state = getattr(ob, "bz_fx", None)
    if state is not None:
        apply_row_edit(ob, state, self)


class BZ_PG_fx_row(bpy.types.PropertyGroup):
    """One property of the effect being edited, drawn with the control of its type."""
    name: StringProperty()
    kind: StringProperty()                    # float / int / bool / enum / colour / vector / text / texture / mesh / effect
    keyed: BoolProperty()
    present: BoolProperty()
    label: StringProperty()
    help: StringProperty()
    f: FloatProperty(precision=3, step=10, update=_row_edit, min=-1e9, max=1e9)
    i: IntProperty(update=_row_edit, min=-(2 ** 31), max=2 ** 31 - 1)
    b: BoolProperty(update=_row_edit)
    s: StringProperty(update=_row_edit)
    c: FloatVectorProperty(size=4, subtype="COLOR", min=0.0, max=1.0, update=_row_edit)
    v: FloatVectorProperty(size=3, precision=3, update=_row_edit)
    blend: EnumProperty(items=BLEND_ITEMS, update=_row_edit)
    direction: EnumProperty(items=DIRECTION_ITEMS, update=_row_edit)
    dynamics: EnumProperty(items=DYNAMICS_ITEMS, update=_row_edit)


def _key_edit(self, context):
    if not _BUSY[0]:
        refill_rows(self.id_data)


class BZ_PG_fx(bpy.types.PropertyGroup):
    """The Effects panel's state of one entity (on the same object the Blueprint panel's values are on)."""
    loaded: BoolProperty(default=False, description="The blueprint's events were read")
    baseline: StringProperty(description="The events as they were read (JSON): an event counts as edited when it differs from this")
    events: CollectionProperty(type=BZ_PG_fx_event)
    event_index: IntProperty(default=0)
    clip: StringProperty(name="Clip", description="Show the events of this clip only; empty shows them all")
    fx_name: StringProperty(description="The effect shown in the editor")
    fx_key: IntProperty(name="Key frame", default=0, min=0, max=63, description="Which key frame's values the rows show", update=_key_edit)
    rows: CollectionProperty(type=BZ_PG_fx_row)
    fx_style: StringProperty()
    fx_where: StringProperty()
    fx_problems: StringProperty()
    fx_times: StringProperty(description="The key frame times of the edited effect (JSON)")
    message: StringProperty()
    origin: StringProperty(description="Where the event list came from (shown above the list)")
    prefix: StringProperty(name="Name prefix", default="my_", description="Put in front of every copied effect's name, so the copy is yours and the game's own stays as it is; empty keeps the game's names (the copies then REPLACE the game's effects)")


# ---------------------------------------------------------------------------------------------------------- events: state <-> items
def _item_of(ev) -> "fxcore.EventItem":
    return fxcore.EventItem(None if ev.src < 0 else int(ev.src), ev.name, [(p.key, p.value) for p in ev.pairs])


def items_of(state) -> list:
    return [_item_of(e) for e in state.events]


def _box_text(text: str) -> str:
    """The text a Blender text box can hold: up to an end mark (a NUL), as Blender keeps it. The stock ``volcano_fx`` event ends two of its fields
    with one; :func:`fxcore.restore_nul` puts the mark back at export for a field nobody changed."""
    return text.split("\0", 1)[0]


def _fill_event(ev, it) -> None:
    ev.name = _box_text(it.name)
    ev.src = -1 if it.src is None else int(it.src)
    ev.pairs.clear()
    for k, v in it.params:
        p = ev.pairs.add()
        p.key, p.value = _box_text(k), _box_text(v)


def baseline_items(state) -> list:
    return fxcore.items_from_json(state.baseline) if state.baseline else []


def creature_clips_and_bones(ent):
    """``(clip names, bone names)`` of a creature as Export Creature for the Game will write them."""
    bones = [b.name for b in ent.rig.data.bones] if ent.rig is not None else []
    return clip_names(ent), bones


def _fill_state(state, items, origin: str) -> None:
    state.events.clear()
    for it in items:
        _fill_event(state.events.add(), it)
    state.baseline = fxcore.items_to_json(items)
    state.event_index = 0
    state.loaded = True
    state.origin = origin


def load_creature_events(ent, state) -> str:
    """Read the events a creature's export starts from: its donor's blueprint (the stock animal it was made from, or the blueprint beside its
    model), else, for a creature with no donor, the events of the stock animal the export copies for the clips the creature has
    (``ICParse.hub.package.DEFAULT_STATS_FROM``)."""
    from . import gameexport
    try:
        donor = gameexport.find_donor(ent.holder)
    except Exception as e:                                                # noqa: BLE001
        return f"The blueprint of the animal this creature was started from cannot be read: {e}"
    if donor is not None:
        try:
            items = fxcore.read_events(donor.ebp)
        except Exception as e:                                            # noqa: BLE001
            return f"The events of {donor.label}'s blueprint cannot be read: {e}"
        _fill_state(state, items, f"{donor.label}'s blueprint ({donor.origin}); the export keeps these and brings them onto your model")
        return ""
    from ICParse.hub import package as PK
    from . import statspanel
    try:
        stock = gameexport._stock()
        base = PK.stock_donor((statspanel.stats_from_of(ent.holder) or PK.DEFAULT_STATS_FROM), "Engine.sga", stock.game)
    except Exception:                                                     # noqa: BLE001
        base = None
    if base is None:
        _fill_state(state, [], "none: no game folder was found to take events from")
        return "No game folder was found, so there are no events to start from. Set the Game folder in the add-on's preferences."
    clips, bones = creature_clips_and_bones(ent)
    items = fxcore.events_of_animal(base.ebp, clips, bones, keep_index=True)
    _fill_state(state, items, f"the stock {base.name}'s events for the clips this creature has (the export gives a new creature these)")
    return ""


def load_events(ent, state) -> str:
    """Read the events of the entity's blueprint into the state. Returns a one-line message ('' when all is well)."""
    if is_creature(ent):
        return load_creature_events(ent, state)
    PB = _pb()
    path = PB.blueprint_path(ent)
    items = []
    msg = ""
    if path:
        try:
            with open(path, "rb") as f:
                items = fxcore.read_events(f.read())
        except Exception as e:                                            # noqa: BLE001
            return f"The blueprint's events cannot be read: {e}"
    else:
        msg = "No blueprint file yet: events you add are written with a new blueprint."
    state.events.clear()
    for it in items:
        _fill_event(state.events.add(), it)
    state.baseline = fxcore.items_to_json(items)
    state.event_index = 0
    state.loaded = True
    state.origin = ""
    return msg


def events_edited(state) -> int:
    """How many events differ from what was read (changed, added or deleted)."""
    return fxcore.count_edited(items_of(state), baseline_items(state))


# ---------------------------------------------------------------------------------------------------------- effects: state <-> file
def _holder(ob):
    return ob


def refill_rows(ob) -> None:
    """Show the effect ``state.fx_name`` in the rows (from the project's file, else the game's)."""
    ent = _entity(obj=ob)
    if ent is not None:
        ob = ent.holder                                                   # the state lives on the entity's holder, whichever of its objects is active
    state = ob.bz_fx
    state.rows.clear()
    state.fx_problems, state.fx_style, state.fx_where, state.fx_times = "", "", "", ""
    name = state.fx_name
    if not name or ent is None:
        return
    fx, where = fxcore.load_effect(name, _source(ent), _game_dir())
    state.fx_where = where
    if fx is None:
        return
    state.fx_style = fx.style
    times = fx.frame_times or [0, 1]
    state.fx_times = ", ".join(("%g" % t) for t in times)
    state.fx_problems = "\n".join(fxcore.effect_problems(fx))
    key = min(max(int(state.fx_key), 0), len(times) - 1)
    _BUSY[0] = True
    try:
        for r in fxcore.rows(fx, key):
            row = state.rows.add()
            row.name, row.kind, row.keyed, row.present = r.name, r.kind, r.keyed, r.present
            row.help = r.text
            lab = r.name.replace("_", " ")
            if r.lo is not None:
                lab += f" ({r.lo:g} to {r.hi:g})"
            row.label = lab
            v = r.value
            if r.kind == "float":
                row.f = float(v)
            elif r.kind in ("int",):
                row.i = int(v)
            elif r.kind == "bool":
                row.b = bool(v)
            elif r.kind == "enum":
                setattr(row, _ENUM_PROP.get(r.name, "blend"), str(int(v)))
            elif r.kind == "colour":
                row.c = [max(0, min(255, float(x))) / 255.0 for x in v]
            elif r.kind == "vector":
                row.v = [float(x) for x in v]
            else:
                row.s = str(v)
    finally:
        _BUSY[0] = False


def _row_value(row):
    k = row.kind
    if k == "float":
        return float(row.f)
    if k == "int":
        return int(row.i)
    if k == "bool":
        return 1 if row.b else 0
    if k == "enum":
        return int(getattr(row, _ENUM_PROP.get(row.name, "blend")))
    if k == "colour":
        return [int(round(max(0.0, min(1.0, x)) * 255)) for x in row.c]
    if k == "vector":
        return [float(x) for x in row.v]
    return str(row.s)


def apply_row_edit(ob, state, row) -> None:
    """A row was edited: write it into the effect's file in the project (a game effect is copied in first by the Copy button, never here). A value the
    game would ignore is refused and the rows show what the file holds."""
    ent = _entity(obj=ob)
    if ent is None or not state.fx_name:
        return
    src = _source(ent)
    fx, where = fxcore.load_effect(state.fx_name, src, _game_dir())
    if fx is None or where != "project":
        state.message = "Copy the effect into your project first (Copy to my project), then edit it."
        refill_rows(ob)
        return
    try:
        key = min(max(int(state.fx_key), 0), len(fx.frame_times or [0, 1]) - 1)
        new = _row_value(row)
        old = next((r.value for r in fxcore.rows(fx, key) if r.name == row.name), None)
        if _same(old, new):
            return
        fxcore.apply_row(fx, row.name, key, new)
        fxcore.save_effect(src, state.fx_name, fx)
        state.message = ""
    except fxlua.FxError as e:
        state.message = str(e)
    refill_rows(ob)


def _same(a, b) -> bool:
    try:
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(abs(float(x) - float(y)) < 1e-4 for x, y in zip(a, b))
        if isinstance(a, str) or isinstance(b, str):
            return a == b
        return abs(float(a) - float(b)) < 1e-4
    except (TypeError, ValueError):
        return a == b


def edit_effect(ob, name: str, mutate) -> str:
    """Load the project's effect ``name``, ``mutate(fx)`` it and save it; returns '' or the reason it was refused."""
    ent = _entity(obj=ob)
    fx, where = fxcore.load_effect(name, _source(ent), _game_dir())
    if fx is None or where != "project":
        return "Copy the effect into your project first (Copy to my project)."
    try:
        mutate(fx)
    except fxlua.FxError as e:
        return str(e)
    fxcore.save_effect(_source(ent), name, fx)
    return ""


# ---------------------------------------------------------------------------------------------------------- the search popup
def _search_items(self, context):
    ent = _entity(context)
    kind = self.kind
    names = []
    labels = {}
    if kind == "marker":
        names = marker_names(ent)
    elif kind == "clip":
        names = clip_names(ent)
    elif kind in ("fx", "row_fx", "edit_fx"):
        names = list(dict.fromkeys(fxcore.project_effect_names(_source(ent)) + fxcore.game_effect_names(_game_dir()))) if ent else []
    elif kind == "copy_fx":
        names = fxcore.game_effect_names(_game_dir())
    elif kind == "sound":
        mine = fxcore.project_sounds(_source(ent)) if ent else []
        names = [v for _n, v in mine] + ["%footfall_front", "%footfall_rear", "%swim"] + fxcore.game_sound_names(_game_dir())
        labels = {v: "your sound: " + n for n, v in mine}
    if kind in ("fx", "row_fx", "edit_fx", "copy_fx", "sound"):       # a readable name, the full name as the tooltip, the folders together
        rows = []
        for n in names:
            short, folder = fxcore.pick_label(n)
            label = labels.get(n) or (f"{short}   [{folder}]" if folder else short)
            rows.append((folder.lower(), short.lower(), n, label))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        items = [(n, label, n) for _f, _s, n, label in rows]
    else:
        items = [(n, labels.get(n, n), "") for n in names]
    if not items:
        items = [("", "(nothing to choose from)", "")]
    _KEEP[:] = items
    return _KEEP


class BZ_OT_fx_search(bpy.types.Operator):
    """Choose a name from a list that is searched as you type"""
    bl_idname = "bz.fx_search"
    bl_label = "Choose"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"

    kind: EnumProperty(items=(("marker", "Marker", ""), ("clip", "Clip", ""), ("fx", "Effect", ""), ("sound", "Sound", ""), ("row_fx", "Effect row", ""),
                              ("copy_fx", "Copy", ""), ("edit_fx", "Edit", "")), options={"HIDDEN"})
    target: StringProperty(options={"HIDDEN"}, description="The property row the choice goes to (for an effect row)")
    choice: EnumProperty(name="Name", items=_search_items)

    @classmethod
    def poll(cls, context):
        return _entity(context) is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ob = context.active_object
        ent = _entity(context)
        state = _state(ent)
        if state is None or not self.choice:
            return {"CANCELLED"}
        k, name = self.kind, self.choice
        if k in ("marker", "clip", "fx", "sound"):
            if not state.loaded or not len(state.events):
                self.report({"ERROR"}, "Choose an event first.")
                return {"CANCELLED"}
            ev = state.events[min(state.event_index, len(state.events) - 1)]
            _set_pair(ev, {"marker": "marker", "clip": "anim", "fx": "fx", "sound": "sound"}[k], name)
        elif k == "row_fx":
            err = edit_effect(ob, state.fx_name, lambda fx: fx.set_property(self.target, name))
            state.message = err
            refill_rows(ob)
        elif k == "edit_fx":
            state.fx_name, state.fx_key = name, 0
            state.message = ""
            refill_rows(ob)
        elif k == "copy_fx":
            return bpy.ops.bz.fx_copy("EXEC_DEFAULT", name=name)
        return {"FINISHED"}


def _set_pair(ev, key: str, value: str) -> None:
    for p in ev.pairs:
        if p.key == key:
            p.value = value
            return
    p = ev.pairs.add()
    p.key, p.value = key, value


def _pair(ev, key: str):
    return next((p for p in ev.pairs if p.key == key), None)


# ---------------------------------------------------------------------------------------------------------- operators: events
def _entity_poll(context) -> bool:
    return _state(_entity(context)) is not None


def _event_poll(context) -> bool:
    state = _state(_entity(context))
    return state is not None and 0 <= state.event_index < len(state.events)


# (Blender looks a registered operator's poll up on the class itself, so every operator below defines its own; none inherits from another.)


class BZ_OT_fx_events_load(bpy.types.Operator):
    """Read the events of this thing's blueprint (throws away edits not yet exported)"""
    bl_idname = "bz.fx_events_load"
    bl_label = "Load events"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ent = _entity(context)
        msg = load_events(ent, ent.holder.bz_fx)
        ent.holder.bz_fx.message = msg
        if msg and "cannot" in msg:
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        return {"FINISHED"}


_ANIMALS: list = []


def _animal_items(self, context):
    try:
        from . import gameexport
        names = sorted(gameexport._stock().animals)
    except Exception:                                                     # noqa: BLE001
        names = []
    _ANIMALS[:] = [(n, n.replace("_", " ").title(), "") for n in names] or [("", "(no game folder found)", "")]
    return _ANIMALS


def _creature_poll(context) -> bool:
    return is_creature(_entity(context))


class BZ_OT_fx_events_from_animal(bpy.types.Operator):
    """Take the events of another animal for this creature: the ones for clips this creature has. Replaces the events in the list"""
    bl_idname = "bz.fx_events_from_animal"
    bl_label = "Copy events from another animal"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "animal"

    animal: EnumProperty(name="Animal", items=_animal_items)

    @classmethod
    def poll(cls, context):
        return _creature_poll(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import gameexport
        from ICParse.hub import package as PK
        ent = _entity(context)
        state = ent.holder.bz_fx
        if not self.animal:
            self.report({"ERROR"}, "Choose an animal.")
            return {"CANCELLED"}
        stock = gameexport._stock()
        base = PK.stock_donor(self.animal, stock.animals.get(self.animal), stock.game)
        if base is None:
            self.report({"ERROR"}, f"The game has no animal called {self.animal}.")
            return {"CANCELLED"}
        clips, bones = creature_clips_and_bones(ent)
        if not clips:
            self.report({"ERROR"}, "This creature has no animation clips yet, so no event of another animal fits it. Import or make its clips first.")
            return {"CANCELLED"}
        if not state.loaded:
            load_events(ent, state)
        items = fxcore.events_of_animal(base.ebp, clips, bones)
        keep_base = state.baseline
        _fill_state(state, items, f"copied from the stock {base.name} for the clips this creature has")
        state.baseline = keep_base                                        # what is in the file is still what edits are measured against
        left = len(fxcore.events_of_animal(base.ebp, clips, None)) - len(items)
        msg = f"Took {len(items)} event(s) of the {base.name} for the clips {', '.join(clips[:4])}{'...' if len(clips) > 4 else ''}."
        if left:
            msg += f" {left} more fit a clip but name a bone this creature lacks, so they were left out."
        if not items:
            msg = f"No event of the {base.name} fits this creature's clips ({', '.join(clips[:4])}). The list is now empty."
        self.report({"INFO"}, msg + " Export Creature for the Game writes the list.")
        return {"FINISHED"}


class BZ_OT_fx_event_add(bpy.types.Operator):
    """Add an event for the chosen clip at the playhead"""
    bl_idname = "bz.fx_event_add"
    bl_label = "Add event"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ent = _entity(context)
        state = ent.holder.bz_fx
        if not state.loaded:
            load_events(ent, state)
        clip = state.clip
        if not clip and ent.rig is not None:
            names = clip_names(ent)
            clip = names[0] if names else ""
        if not clip.strip():
            # an event with no clip is filed under the clip name "" and so never runs (AnimEvent.cpp:616-640, FindGroup :546-558)
            self.report({"ERROR"}, "Name the clip first (type it in the Clip box above the list): an event runs only while the clip of that name "
                                   "plays, and this model has no clip to take it from.")
            return {"CANCELLED"}
        first, last = clip_range(ent, clip, context.scene)
        t = fxcore.event_time(context.scene.frame_current, first, last)
        params = [("anim", clip), ("animtime", fxcore.fmt_time(t))]
        ev = state.events.add()
        _fill_event(ev, fxcore.EventItem(None, "NewEvent", params))
        state.event_index = len(state.events) - 1
        return {"FINISHED"}


class BZ_OT_fx_event_remove(bpy.types.Operator):
    """Delete the chosen event (it is removed from the blueprint at export)"""
    bl_idname = "bz.fx_event_remove"
    bl_label = "Delete event"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        state.events.remove(state.event_index)
        state.event_index = max(0, min(state.event_index, len(state.events) - 1))
        return {"FINISHED"}


class BZ_OT_fx_event_duplicate(bpy.types.Operator):
    """Copy the chosen event (the copy is a new event)"""
    bl_idname = "bz.fx_event_duplicate"
    bl_label = "Duplicate event"

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        it = _item_of(state.events[state.event_index])
        it.src = None
        _fill_event(state.events.add(), it)
        state.event_index = len(state.events) - 1
        return {"FINISHED"}


class BZ_OT_fx_event_time(bpy.types.Operator):
    """Set the chosen event's time from the playhead, or to the start of the clip"""
    bl_idname = "bz.fx_event_time"
    bl_label = "Event time"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(items=(("PLAYHEAD", "From the playhead", "0 at the clip's first frame, 1 at its last"),
                              ("START", "When the clip starts", "animtime -1: the event runs when the clip starts (a sound with it loops)")),
                       default="PLAYHEAD")

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def execute(self, context):
        ent = _entity(context)
        state = ent.holder.bz_fx
        ev = state.events[state.event_index]
        if self.mode == "START":
            text = "-1.00"
        else:
            p = _pair(ev, "anim")
            first, last = clip_range(ent, p.value if p else state.clip, context.scene)
            text = fxcore.fmt_time(fxcore.event_time(context.scene.frame_current, first, last))
        _set_pair(ev, "animtime", text)
        return {"FINISHED"}


class BZ_OT_fx_marker_use(bpy.types.Operator):
    """Use the active marker (select one in the Markers panel) as this event's marker"""
    bl_idname = "bz.fx_marker_use"
    bl_label = "Use active marker"

    @classmethod
    def poll(cls, context):
        if not _event_poll(context):
            return False
        try:
            from .sceneobj import slice_markers as SM
            return SM.is_marker(context.active_object)
        except Exception:                                                 # noqa: BLE001
            return False

    def execute(self, context):
        from .sceneobj import slice_markers as SM
        state = _entity(context).holder.bz_fx
        _set_pair(state.events[state.event_index], "marker", SM.marker_name(context.active_object))
        return {"FINISHED"}


class BZ_OT_fx_pair_add(bpy.types.Operator):
    """Add a field to the chosen event"""
    bl_idname = "bz.fx_pair_add"
    bl_label = "Add field"
    bl_options = {"REGISTER", "UNDO"}

    key: StringProperty(name="Field", default="")
    value: StringProperty(name="Value", default="")

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def invoke(self, context, event):
        if self.key:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "key")
        col.prop(self, "value")
        from ICParse.ebp.events import KNOWN_KEYS
        col.label(text="Known fields: " + ", ".join(sorted(KNOWN_KEYS)))

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        if not self.key.strip():
            self.report({"ERROR"}, "Give the field a name.")
            return {"CANCELLED"}
        ev = state.events[state.event_index]
        p = ev.pairs.add()
        p.key, p.value = self.key.strip(), self.value
        return {"FINISHED"}


class BZ_OT_fx_pair_remove(bpy.types.Operator):
    """Remove a field from the chosen event"""
    bl_idname = "bz.fx_pair_remove"
    bl_label = "Remove field"

    index: IntProperty()

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        ev = state.events[state.event_index]
        if 0 <= self.index < len(ev.pairs):
            ev.pairs.remove(self.index)
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------- operators: sounds
def _sound_stem(ent) -> str:
    """The name the sounds' folder takes: a creature's export name (the hub wants a creature's sounds in the folder of its own name), else the project's."""
    if is_creature(ent):
        from . import gameexport
        return gameexport.default_name(ent.holder)
    return _pb().default_name(ent)


def sound_name_from_file(path: str) -> str:
    import re
    stem = re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(os.path.basename(path))[0].lower()).strip("_")[:32]
    return stem or "sound"


def _use_sound(state, value: str) -> bool:
    """Put ``value`` in the chosen event's sound field; False when there is no chosen event."""
    if not state.loaded or not 0 <= state.event_index < len(state.events):
        return False
    _set_pair(state.events[state.event_index], "sound", value)
    return True


class BZ_OT_fx_sound_new(bpy.types.Operator):
    """Make a new sound for events from a WAV file. It is kept in your project and goes into the item with the effects"""
    bl_idname = "bz.fx_sound_new"
    bl_label = "New Sound From WAV"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.wav", options={"HIDDEN"})
    sound_name: StringProperty(name="Sound name", default="", description="Lower case letters, digits and _ (up to 32). Empty: the file's name")
    volume: FloatProperty(name="Volume", default=1.0, min=0.0, max=10.0, description="1 plays the wave as it is")
    positional: BoolProperty(name="Has a place in the world", default=True,
                             description="The sound comes from where the thing is: it gets quieter with distance and is not heard beyond the far distance. Turn it off for a sound everyone hears the same")
    min_distance: FloatProperty(name="Full volume up to", default=6.0, min=0.0, description="Up to this far from the listener the sound plays at full volume")
    max_distance: FloatProperty(name="Not heard beyond", default=80.0, min=0.1, description="Beyond this far from the listener the sound is not played")
    use_in_event: BoolProperty(name="Use it in the chosen event", default=True)

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "sound_name")
        col.prop(self, "volume")
        col.prop(self, "positional")
        if self.positional:
            col.prop(self, "min_distance")
            col.prop(self, "max_distance")
        col.prop(self, "use_in_event")
        state = _state(_entity(context))
        if self.use_in_event and state is not None and 0 <= state.event_index < len(state.events):
            ev = state.events[state.event_index]
            _hint(col, f"The chosen event is '{ev.name}'" + (", one of the original's: its sound is replaced." if ev.src >= 0 else ", one you added."))

    def execute(self, context):
        from ICParse import sound
        ent = _entity(context)
        state = ent.holder.bz_fx
        path = bpy.path.abspath(self.filepath)
        if not self.filepath or not os.path.isfile(path):
            self.report({"ERROR"}, "Choose a WAV file.")
            return {"CANCELLED"}
        try:
            with open(path, "rb") as f:
                wave = f.read()
        except OSError as e:
            self.report({"ERROR"}, f"The file could not be read: {e.strerror or e}")
            return {"CANCELLED"}
        name = self.sound_name.strip() or sound_name_from_file(path)
        settings = sound.SoundSettings(volume=self.volume, positional=self.positional, min_distance=self.min_distance, max_distance=self.max_distance)
        try:
            ns = fxcore.create_sound(_source(ent), _game_dir(), wave, name, fxcore.sound_folder(is_creature(ent), _sound_stem(ent)), settings)
        except ValueError as e:
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        for w in ns.warnings:
            self.report({"WARNING"}, ui_kit.plain(w))
        chosen = state.events[state.event_index] if 0 <= state.event_index < len(state.events) else None
        used = self.use_in_event and _use_sound(state, ns.event_value)
        info = ns.wave
        who = "" if chosen is None else f"The event '{chosen.name}'" + (" (one of the original's: its old sound is replaced)" if chosen.src >= 0 else " (the one you added)")
        self.report({"INFO"}, f"Made the sound '{name}' ({info.seconds:.1f} s, {info.rate} Hz, {'mono' if info.mono else 'stereo'}) in {fxcore.project_data_root(_source(ent))}. "
                              + (f"{who} plays it now. " if used and who else "The chosen event plays it now. " if used else "Pick it as an event's Sound (the magnifier next to Sound). ")
                              + "Give that event a time (0 up to just below 1) so the sound plays once; with no time the game loops it.")
        return {"FINISHED"}


class BZ_OT_fx_sound_use(bpy.types.Operator):
    """Play this sound in the chosen event"""
    bl_idname = "bz.fx_sound_use"
    bl_label = "Use in the chosen event"
    bl_options = {"REGISTER", "UNDO"}

    value: StringProperty()

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        if not _use_sound(state, self.value):
            self.report({"ERROR"}, "Choose an event in the list first.")
            return {"CANCELLED"}
        return {"FINISHED"}


class BZ_OT_fx_sound_delete(bpy.types.Operator):
    """Delete a sound of your project (its patch, sample and wave files; a file another sound of the project still uses stays). Events that name it then
    play nothing. This cannot be undone"""
    bl_idname = "bz.fx_sound_delete"
    bl_label = "Delete sound"
    bl_options = {"REGISTER"}                    # files on disk: Blender's Undo cannot bring them back, so the button is not an undo step

    value: StringProperty()

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Delete this sound?",
                                                     message="Its files are deleted from your project. Undo cannot bring them back.", confirm_text="Delete")

    def execute(self, context):
        try:
            gone = fxcore.delete_sound(_source(_entity(context)), self.value)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Deleted {len(gone)} file(s) of the sound.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------- operators: effects
class BZ_OT_fx_copy(bpy.types.Operator):
    """Copy a game effect, with the pictures, meshes and effects it names, into this project"""
    bl_idname = "bz.fx_copy"
    bl_label = "Copy to my project"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Effect")
    new_name: StringProperty(name="New name", description="Name the copy of this effect; empty gives it the prefix and the game's name")

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ob = context.active_object
        ent = _entity(context)
        state = ent.holder.bz_fx
        try:
            res = fxcore.copy_into_project(_source(ent), _game_dir(), self.name, prefix=state.prefix.strip(), new_name=self.new_name.strip())
        except Exception as e:                                            # noqa: BLE001
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        if not res.written and not res.kept:
            self.report({"ERROR"}, f"'{self.name}' is not an effect of the game." if self.name else "Choose an effect.")
            return {"CANCELLED"}
        state.fx_name, state.fx_key = res.top, 0
        refill_rows(ob)
        folder = fxcore.project_data_root(_source(ent))
        msg = f"Copied {len([w for w in res.written if w.endswith('.lua')])} effect(s) and {len([w for w in res.written if not w.endswith('.lua')])} file(s) into {folder}"
        if res.kept:
            msg += f"; {len(res.kept)} file(s) were there already and are left as they are"
        if res.missing:
            msg += f"; the game itself lacks: {', '.join(res.missing[:3])}"
        if res.renamed and any(" " in a for a in res.renamed):
            msg += ". A name with a space got an underscore instead (an item cannot hold a file name with a space): " + \
                ", ".join(f"{a} -> {b}" for a, b in list(res.renamed.items()) if " " in a)[:160]
        if not state.prefix.strip() and not self.new_name.strip():
            msg += ". With no prefix the copies REPLACE the game's effects of the same names (a name that had a space is a new name instead)"
        elif res.top:
            msg += f". Now set the Effect of an event to {res.top}: the game plays your copy only where an event names it"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BZ_OT_fx_new(bpy.types.Operator):
    """Make a new effect of a type, at the game's defaults"""
    bl_idname = "bz.fx_new"
    bl_label = "New effect"
    bl_options = {"REGISTER", "UNDO"}

    style: EnumProperty(name="Type", items=tuple((s, s[6:].title(), "") for s in fxschema.STYLE_NAMES), default="STYLE_SPRAY")
    new_name: StringProperty(name="Name", default="my_effect", description="Lower case letters, digits and _; a folder is written with /. A space becomes _ (an item cannot hold a file name with a space)")
    template: StringProperty(name="Start from", default="", description="The name of a game effect of the same type to start from; empty starts from the game's defaults for the type")

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob, ent = context.active_object, _entity(context)
        name = fxlua.normalise_name(self.new_name).replace(" ", "_")          # an item cannot list a file name with a space
        bad = fxlua.name_problem(name)
        if bad:
            self.report({"ERROR"}, bad + " Give the effect a name made of letters, digits and _ (a folder is written with /).")
            return {"CANCELLED"}
        if fxcore.effect_where(name, _source(ent), _game_dir()) != "missing":
            self.report({"ERROR"}, f"There is an effect called '{name}' already (in the project or the game): choose another name.")
            return {"CANCELLED"}
        try:
            if self.template.strip():
                from ICParse.fx import fxfiles
                fx = fxfiles.new_from_template(fxcore.game_data(_game_dir()), fxlua.normalise_name(self.template), name, style=self.style)
            else:
                fx = fxlua.new_effect(self.style)
        except (ValueError, AttributeError) as e:
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        fxcore.save_effect(_source(ent), name, fx)
        ent.holder.bz_fx.fx_name, ent.holder.bz_fx.fx_key = name, 0
        refill_rows(ob)
        return {"FINISHED"}


class BZ_OT_fx_duplicate(bpy.types.Operator):
    """Make a copy of the effect shown, under a new name"""
    bl_idname = "bz.fx_duplicate"
    bl_label = "Duplicate under a new name"
    bl_options = {"REGISTER", "UNDO"}

    new_name: StringProperty(name="New name")

    @classmethod
    def poll(cls, context):
        state = _state(_entity(context))
        return state is not None and bool(state.fx_name) and bool(state.fx_style)

    def invoke(self, context, event):
        self.new_name = _state(_entity(context)).fx_name + "_2"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob, ent = context.active_object, _entity(context)
        state = ent.holder.bz_fx
        name = fxlua.normalise_name(self.new_name).replace(" ", "_")          # an item cannot list a file name with a space
        bad = fxlua.name_problem(name)
        if bad:
            self.report({"ERROR"}, bad)
            return {"CANCELLED"}
        if fxcore.effect_where(name, _source(ent), _game_dir()) != "missing":
            self.report({"ERROR"}, f"There is an effect called '{name}' already: choose another name.")
            return {"CANCELLED"}
        fx, _w = fxcore.load_effect(state.fx_name, _source(ent), _game_dir())
        if fx is None:
            return {"CANCELLED"}
        fxcore.save_effect(_source(ent), name, fx.duplicate(fxlua.fx_path_of_name(name)))
        state.fx_name, state.fx_key = name, 0
        refill_rows(ob)
        return {"FINISHED"}


class BZ_OT_fx_key(bpy.types.Operator):
    """Add a key frame, remove the shown one, or move it in time"""
    bl_idname = "bz.fx_key"
    bl_label = "Key frame"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(items=(("ADD", "Add", ""), ("REMOVE", "Remove", ""), ("MOVE", "Move", "")), default="ADD")
    time: FloatProperty(name="Time", min=0.0, max=1.0, default=0.5, description="Along the effect's life: 0 is its start, 1 its end")

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        if self.mode == "REMOVE":
            return self.execute(context)
        state = _entity(context).holder.bz_fx
        times = _times(state)
        if self.mode == "MOVE" and 0 <= state.fx_key < len(times):
            self.time = times[state.fx_key]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob = context.active_object
        state = _entity(context).holder.bz_fx
        k = state.fx_key

        def go(fx):
            nonlocal k
            if self.mode == "ADD":
                k = fx.add_keyframe(self.time)
            elif self.mode == "REMOVE":
                fx.remove_keyframe(k)
                k = max(0, k - 1)
            else:
                fx.set_frame_time(k, self.time)
        err = edit_effect(ob, state.fx_name, go)
        state.message = err
        if err:
            self.report({"ERROR"}, ui_kit.plain(err))
            return {"CANCELLED"}
        state.fx_key = k
        refill_rows(ob)
        return {"FINISHED"}


def _times(state) -> list:
    try:
        return [float(x) for x in state.fx_times.split(",") if x.strip()]
    except ValueError:
        return [0.0, 1.0]


class BZ_OT_fx_reset(bpy.types.Operator):
    """Take this property out of the file: the game then uses its own default"""
    bl_idname = "bz.fx_reset"
    bl_label = "Use the game's default"
    bl_options = {"REGISTER", "UNDO"}

    prop: StringProperty()

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ob = context.active_object
        state = _entity(context).holder.bz_fx
        state.message = edit_effect(ob, state.fx_name, lambda fx: fx.remove_property(self.prop))
        refill_rows(ob)
        return {"FINISHED"}


class BZ_OT_fx_pick_file(bpy.types.Operator):
    """Use a picture or a mesh file from disk: it is copied into the project's effect folder and the property names it"""
    bl_idname = "bz.fx_pick_file"
    bl_label = "Choose a file"
    bl_options = {"REGISTER", "UNDO"}

    prop: StringProperty(options={"HIDDEN"})
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.tga;*.sgm", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        self.filter_glob = "*.sgm" if self.prop == "Mesh" else "*.tga"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ob, ent = context.active_object, _entity(context)
        state = ent.holder.bz_fx
        src = bpy.path.abspath(self.filepath)
        base = os.path.basename(src).lower().replace(" ", "_")
        want = ".sgm" if self.prop == "Mesh" else ".tga"
        if not os.path.isfile(src) or not base.endswith(want):
            self.report({"ERROR"}, f"Choose a {want} file.")
            return {"CANCELLED"}
        sub = "objects" if self.prop == "Mesh" else "textures"
        rel = f"art/fx/{fxlua.normalise_name(state.fx_name)}_{sub}/{base}"
        with open(src, "rb") as f:
            fxcore.write_file(os.path.join(fxcore.project_data_root(_source(ent)), *rel.split("/")), f.read())
        ref = "Data:" + rel
        err = edit_effect(ob, state.fx_name, lambda fx: fx.set_property(self.prop, ref))
        state.message = err
        if err:
            self.report({"ERROR"}, ui_kit.plain(err))
            return {"CANCELLED"}
        refill_rows(ob)
        return {"FINISHED"}


class BZ_OT_fx_use_model(bpy.types.Operator):
    """Make the shown effect draw this project's own model (an effect model project) as its mesh"""
    bl_idname = "bz.fx_use_model"
    bl_label = "Use my model"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_effect_model(_entity(context))

    def execute(self, context):
        ob, ent = context.active_object, _entity(context)
        state = ent.holder.bz_fx
        name, folder = effect_model_target(ent)
        ref = "Data:art/fx/" + (folder + "/" if folder else "") + name + ".sgm"

        def go(fx):
            fx.set_property("UseMesh", 1)
            fx.set_property("Mesh", ref)
        state.message = edit_effect(ob, state.fx_name, go)
        refill_rows(ob)
        return {"FINISHED"}


class BZ_OT_fx_select(bpy.types.Operator):
    """Show this effect in the editor"""
    bl_idname = "bz.fx_select"
    bl_label = "Edit"

    name: StringProperty()

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        state = _entity(context).holder.bz_fx
        state.fx_name, state.fx_key, state.message = self.name, 0, ""
        refill_rows(context.active_object)
        return {"FINISHED"}


class BZ_OT_fx_write_folder(bpy.types.Operator):
    """Write the project's effect files (and their pictures and meshes) into a folder, under data/art/fx"""
    bl_idname = "bz.fx_write_folder"
    bl_label = "Write effect files"
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ent = _entity(context)
        try:
            root = _pb().check_target(self.directory)
            refused, notes = fxcore.export_problems(_source(ent))
            if refused:
                raise _pb().Refused("The game would not load these effects: " + "; ".join(refused[:3]))
            written = fxcore.write_effects(_source(ent), root)
        except _pb().Refused as e:
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        for n in notes[:3]:
            self.report({"WARNING"}, ui_kit.plain(n))
        self.report({"INFO"}, f"Wrote {len(written)} file(s) under {os.path.join(root, 'data')}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------- export hooks
def effect_model_target(ent) -> tuple:
    """``(file name, folder under art/fx)`` an effect model project is exported as: the project's name, and the folder of the game model it was
    started from (``bz_donor``)."""
    PB = _pb()
    name = PB.default_name(ent)
    folder = fxcore.fx_folder_of_member(PB._stamp(ent, "bz_donor")) or ""
    return name, PB.clean_folder(folder)


def export_entity_ebp(ent, ebp_data: bytes):
    """Called by Export Entity Folder before anything is written: the blueprint with the events edited in the Effects box, and a refusal when the
    project holds an effect the game would not load. Returns ``(ebp bytes, notes)``."""
    PB = _pb()
    notes = []
    state = _state(ent)
    refused, fxnotes = fxcore.export_problems(_source(ent))
    if refused:
        raise PB.Refused("The game would not load " + ("this effect" if len(refused) == 1 else "these effects") + ": " + "; ".join(refused[:3]) +
                         " (fix it in Effects and events > Effect editor).")
    notes += fxnotes[:6]
    if state is None or not state.loaded:
        return ebp_data, notes
    if not events_edited(state):                                          # nothing edited: the blueprint's own bytes, whatever its layout
        return ebp_data, notes
    from ICParse.ebp.events import EventError
    try:
        out, ev_notes = fxcore.apply_events(ebp_data, items_of(state), baseline=baseline_items(state))
    except (EventError, ValueError) as e:
        raise PB.Refused(f"The events cannot be written into this blueprint: {ui_kit.plain(str(e))}") from None
    if ev_notes:
        notes.append("Events: " + ", ".join(ev_notes))
    notes += fxcore.project_sound_timing_notes(_source(ent), items_of(state))[:3]
    base = {b.src: b for b in baseline_items(state)}
    from ICParse.ebp.events import NO_CLIP_NOTE, check_event
    for it in items_of(state):
        if (it.src is None or it.src not in base or fxcore.event_changed(base[it.src], it)) and \
                any(p.startswith(NO_CLIP_NOTE) for p in check_event(it.name, it.params)):
            notes.append(f"The event '{it.name}' names no clip, so the game never runs it.")
    notes += fxcore.event_warnings(items_of(state), baseline_items(state), _source(ent), _game_dir())[:6]
    return out, notes


class CreatureHook:
    """What the effects and events of a creature add to Export Creature for the Game: the donor to build on (with the edited events in its
    blueprint), the extra arguments of ``build_creature_item`` and the lines to show. ``error`` is set when the export must stop."""

    def __init__(self, donor):
        self.donor = donor
        self.kwargs = {}
        self.notes = []
        self.error = ""


def creature_export_hook(obj, donor, name: str = "") -> "CreatureHook":
    """Called by Export Creature for the Game (``gameexport.build``) with the creature and the donor it found. Returns the donor to use -- the
    donor's blueprint carrying the events edited in the Events box -- and what else the item gets: the creature's effect and sound files
    (the project's ``<name>_fx/data`` folder) and, for a creature with no donor, the edited event list. A creature whose events were never
    loaded and which has no effect or sound files is exported exactly as before. ``name`` is the item's name: a creature's sounds go in the folder
    of its own name (``audio/animals/<name>``, the hub's rule) whatever name they were made under, and the events that name them follow."""
    import dataclasses
    hook = CreatureHook(donor)
    ent = CreatureEntity(obj)
    state = _state(ent)
    refused, fxnotes = fxcore.export_problems(ent.source) if ent.source else ([], [])
    if refused:
        hook.error = ("The game would not load " + ("this effect" if len(refused) == 1 else "these effects") + ": " + "; ".join(refused[:3]) +
                      " (fix it in Effects and events > Effect editor).")
        return hook
    hook.notes += fxnotes[:6]
    spaced = fxcore.spaced_paths(ent.source) if ent.source else []
    if spaced:
        hook.error = (f"The file '{spaced[0]}' has a space in its name, and an item cannot list a file like that. Delete it from the project's effect "
                      "folder and copy the effect from the game again (a copy now writes an underscore for each space and the effects that name it follow), "
                      "or rename the file and the effect that names it so they have no space.")
        return hook
    files = effect_file_blobs(ent) if ent.source else {}
    items = items_of(state) if state is not None and state.loaded else None
    if files and name:
        try:
            files, moved_items, rn = fxcore.retarget_sounds(files, f"audio/animals/{name}", items or [])
        except ValueError as e:
            hook.error = str(e)
            return hook
        hook.notes += rn
        if items is not None:
            items = moved_items
    hook.notes += fxcore.sound_problems(ent.source, _game_dir())[:4] if ent.source else []
    if state is not None and state.loaded:
        base = baseline_items(state)
        if donor is not None:
            try:
                now = [(i.name, list(i.params)) for i in fxcore.read_events(donor.ebp)]
            except Exception as e:                                        # noqa: BLE001
                hook.error = f"The events of the animal this creature was started from cannot be read: {e}"
                return hook
            if now != [(b.name, list(b.params)) for b in base] and fxcore.count_edited(items, base):
                hook.error = ("The blueprint the events were read from has changed since (another original animal, or its file was edited). Press Load events "
                              "in the Events box and make the edits again.")
                return hook
        from ICParse.ebp.events import EventError
        try:
            ebp, events, ev_notes = fxcore.creature_events(donor.ebp if donor is not None else None, items, base)
        except (EventError, ValueError) as e:
            hook.error = f"The events cannot be written into the blueprint: {ui_kit.plain(str(e))}"
            return hook
        if ebp is not None:
            hook.donor = dataclasses.replace(donor, ebp=ebp, edited=True)
        if events is not None:
            hook.kwargs["events"] = events
        if ev_notes:
            hook.notes.append("Events: " + ", ".join(ev_notes))
        hook.notes += fxcore.event_warnings(items, base, ent.source, _game_dir())[:6]
        hook.notes += fxcore.project_sound_timing_notes(ent.source, items)[:3]
        quiet = [it.get("sound") for it in items if it.get("sound") and fxcore.sound_where(it.get("sound"), ent.source, _game_dir()) == "missing"]
        if quiet and _game_dir():
            hook.notes.append(f"An event names a sound the game does not have and the project does not hold, so it plays nothing: {', '.join(dict.fromkeys(quiet))[:200]}")
    if files:
        hook.kwargs["extra_files"] = files
        hook.notes.append(f"{len(files)} effect or sound file(s) of the project go into the item")
    return hook


def effect_file_blobs(ent) -> dict:
    """The project's effect folder as ``{"data/<path>": bytes}``: what Export Entity Folder adds to the item's files (so the manifest lists them)."""
    out = {}
    for rel, full in sorted(fxcore.effect_files(_source(ent)).items()):
        with open(full, "rb") as f:
            out["data/" + rel] = f.read()
    return out


def effect_item_files(context, ent, *, name=None, folder=None, legacy_lod=True, drop_morphs=False, remove_missing=False, refuse_errors=True):
    """What Export Entity Folder writes for an effect model project, in memory: ``(files, model path, item id, name, folder, rebuilt parts, kept
    parts, notes, effect notes)`` with ``files`` = ``{path inside the item: bytes}``, the manifest included. The manifest's kind is what the hub's
    checker makes of the files (``ICParse.hub.scenery.EFFECT_KIND``: the pictures under art/fx are looks), so the two agree. Raises
    ``Refused`` when the game would not load an effect or the model."""
    import importlib
    PB = _pb()
    d_name, d_folder = effect_model_target(ent)
    out_name = PB.clean_name(name if name else d_name)
    out_folder = PB.clean_folder(d_folder if folder is None else folder)
    hub = importlib.import_module("ICParse.hub.scenery")
    item = hub.item_id(out_name)
    sgm_data, rebuilt, kept, notes, textures = PB.scene_bytes_and_files(context, ent, legacy_lod, drop_morphs, entity=out_name, remove_missing=remove_missing,
                                                                     refuse_errors=refuse_errors)
    refused, fxnotes = fxcore.export_problems(_source(ent))
    if refused:
        raise PB.Refused("The game would not load " + ("this effect" if len(refused) == 1 else "these effects") + ": " + "; ".join(refused[:3]))
    rel = f"art/fx/{out_folder + '/' if out_folder else ''}{out_name}.sgm"
    files = {f"{hub.ROOT}/{rel}": sgm_data}
    for r, blob in sorted(textures.items()):                              # the model's new texture files (lower-case paths inside data)
        files[f"{hub.ROOT}/{r}"] = blob
    files.update(effect_file_blobs(ent))
    files[f"{hub.ROOT}/hub/{item}.item"] = hub.manifest_bytes(item, out_name, files, kind=hub.EFFECT_KIND)
    return files, rel, item, out_name, out_folder, rebuilt, kept, notes, fxnotes


def export_effect_model(context, obj, directory, *, name=None, folder=None, legacy_lod=True, drop_morphs=False, remove_missing=False,
                        export_anyway=False, item_folder=""):
    """Export Entity Folder for an effect model project: the item folder ``<dir>/<item folder>/data/art/fx/<folder>/<name>.sgm`` (an effect names its
    mesh by that path; there is no blueprint), the model's new textures, the project's effect files and the hub manifest; the folder goes on the
    scene as ``bz_last_item_dir`` like every other item."""
    import importlib
    PB = _pb()
    ent = PB.entity_of(obj)
    root = PB.check_target(directory)
    hub = importlib.import_module("ICParse.hub.scenery")
    files, rel, item, out_name, out_folder, rebuilt, kept, notes, fxnotes = effect_item_files(
        context, ent, name=name, folder=folder, legacy_lod=legacy_lod, drop_morphs=drop_morphs, remove_missing=remove_missing,
        refuse_errors=not export_anyway)
    notes = list(notes) + [hub.EFFECT_HUB_NOTE]
    item_dir = os.path.join(root, PB.item_folder_name(ent, PB.MODE_NEW, item, item_folder))
    earlier = PB.earlier_export_files(item_dir, item)                     # refuses a folder that is not an earlier export of this item
    sgm_path = os.path.join(item_dir, *f"{hub.ROOT}/{rel}".split("/"))
    readme = os.path.join(item_dir, "README.txt")
    written = []
    try:
        for p, blob in sorted(files.items()):
            PB.write_file(os.path.join(item_dir, *p.split("/")), blob)
            written.append(p)
        PB.write_file(readme, (
            "HOW TO TRY THESE FILES IN THE GAME\n==================================\n\n"
            f"This folder was written by the Impossible Creatures add-on for Blender (Export Entity Folder) for an effect model.\n"
            f"The model is data/{rel}: an effect draws it when one of its properties says  Mesh = Data:{rel}  with UseMesh on.\n"
            f"There is no blueprint: the game loads an effect's mesh straight from its path.\n\n"
            "1. Work in a copy of the game that you own (not the Steam copy: Steam can overwrite files it did not put there).\n"
            "2. Copy the data folder of this item into the main folder of that copy and merge it with the data folder there\n"
            "   (or press Install into Test Game in the IC Game tab).\n"
            "3. Start the game from the copy. Loose files in its data folder are read before the packed archives.\n\n"
            "Files written: " + ", ".join(written) + "\n").encode("utf-8"))
    except OSError as e:
        raise PB.Refused(f"The files could not be written into '{item_dir}': {e.strerror or e}. Some of them may be there already.") from None
    gone = PB.remove_stale(item_dir, earlier, written, keep=[_source(ent)])
    if gone:
        notes = list(notes) + [f"{len(gone)} file(s) of the earlier export are no longer part of the item and were taken out of its folder "
                               f"({', '.join(gone[:3])}{' ...' if len(gone) > 3 else ''})"]
    try:
        context.scene["bz_last_item_dir"] = item_dir                      # Game > Install and the hub check start from this folder
        ent.holder["bz_last_item_dir"] = item_dir                         # and the Game panel shows THIS project's own last export
        context.scene.bz_item_dir = ""                                    # a folder picked in the Game panel no longer wins over this export
    except (AttributeError, TypeError):
        pass
    return PB.EntityExport(item_dir, sgm_path, "", readme, out_name, out_folder, rebuilt, kept, [], "none: an effect model has no blueprint",
                           list(notes) + fxnotes[:6], item=item, mode=PB.MODE_NEW,
                           manifest=os.path.join(item_dir, *f"{hub.ROOT}/hub/{item}.item".split("/")), files=sorted(written), problems=[])


# ---------------------------------------------------------------------------------------------------------- drawing
class BZ_UL_fx_events(bpy.types.UIList):
    bl_idname = "BZ_UL_fx_events"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        it = _item_of(item)
        t = it.animtime
        row = layout.row(align=True)
        row.label(text=item.name or "(no name)", icon="DOT" if item.src >= 0 else "ADD")
        row.label(text=(it.anim or "-") + ("" if t is None else f"  {t:g}"))
        row.label(text=fxcore.event_summary(it))

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        clip = (data.clip or "").lower()
        if clip:
            for i, ev in enumerate(items):
                p = _pair(ev, "anim")
                if (p.value if p else "").lower() != clip:
                    flags[i] = 0
        return flags, []


def _hint(layout, text: str, icon: str = "NONE"):
    col = layout.column(align=True)
    col.scale_y = 0.85
    for i, line in enumerate(textwrap.wrap(ui_kit.plain(text), 46, break_long_words=False, break_on_hyphens=False) or [""]):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def time_words(ent, ev, state, scene) -> str:
    """The event's time as a frame of its clip, in words ('' when the time is not a number or is the clip's start)."""
    pt, pa = _pair(ev, "animtime"), _pair(ev, "anim")
    from ICParse.ebp.events import atod
    t = atod(pt.value) if pt is not None and pt.value != "" else None
    if t is None:
        return ""
    first, last = clip_range(ent, pa.value if pa else state.clip, scene)
    if t < 0:
        return "That is when the clip starts."
    if t >= 1.0:
        return f"That is past the end of the clip (frames {first:g} to {last:g}): the event never runs."
    return f"That is frame {fxcore.frame_of_time(t, first, last):.1f} of the clip (frames {first:g} to {last:g})."


def reload_events_of(ob) -> None:
    """Read the events of the creature ``ob`` again when they were read before (the animal it starts from changed)."""
    ent = _entity(obj=ob)
    state = _state(ent)
    if state is not None and state.loaded and is_creature(ent):
        state.message = load_events(ent, state)


def _load_by_name(name: str) -> None:
    """Read the events of the object called ``name`` (the panel's first drawing asks for it)."""
    ob = bpy.data.objects.get(name)
    ent = _entity(obj=ob) if ob is not None else None
    state = _state(ent)
    if state is not None and not state.loaded:
        state.message = load_events(ent, state)


def draw_events(layout, context) -> None:
    ent = _entity(context)
    state = _state(ent)
    if state is None:
        return
    creature = is_creature(ent)
    if not state.loaded:
        from . import autoload
        name = ent.holder.name
        autoload.once(("events", ent.holder.session_uid), lambda: _load_by_name(name))
        if creature:
            _hint(layout, "Events make a creature play an effect or a sound at a moment of a clip (a bite, a footstep). They are read when this panel "
                          "opens and start from the stock animal this creature was made from; if nothing shows, press Load events.")
        else:
            _hint(layout, "The events of this thing's blueprint start the effects and sounds. They are read when this panel opens; if nothing shows, "
                          "press Load events.")
        layout.operator("bz.fx_events_load", icon="FILE_REFRESH")
        if creature:
            layout.operator("bz.fx_events_from_animal", icon="COPYDOWN")
        return
    if state.origin:
        _hint(layout, "List from " + state.origin + ".", "INFO")
    row = layout.row(align=True)
    row.prop(state, "clip", text="Clip", icon="ACTION")
    row.operator("bz.fx_search", text="", icon="VIEWZOOM").kind = "clip"
    layout.template_list("BZ_UL_fx_events", "", state, "events", state, "event_index", rows=4)
    row = layout.row(align=True)
    row.operator("bz.fx_event_add", text="Add", icon="ADD")
    row.operator("bz.fx_event_duplicate", text="Copy", icon="DUPLICATE")
    row.operator("bz.fx_event_remove", text="Delete", icon="REMOVE")
    if creature:
        layout.operator("bz.fx_events_from_animal", icon="COPYDOWN")
    n = events_edited(state)
    if n:
        _hint(layout, f"{n} event(s) edited: " + ("Export Creature for the Game writes them into the blueprint." if creature
                                                  else "Export Entity Folder writes them into the blueprint."), "INFO")
    if not len(state.events) or not 0 <= state.event_index < len(state.events):
        return
    ev = state.events[state.event_index]
    box = layout.box()
    box.prop(ev, "name", text="Name")
    # clip and time
    p = _pair(ev, "anim")
    row = box.row(align=True)
    if p is None:
        row.label(text="Clip: none, so it never runs", icon="ERROR")
        row.operator("bz.fx_pair_add", text="", icon="ADD").key = "anim"
    else:
        row.prop(p, "value", text="Clip")
        row.operator("bz.fx_search", text="", icon="VIEWZOOM").kind = "clip"
    p = _pair(ev, "animtime")
    row = box.row(align=True)
    if p is None:
        row.label(text="Time: when the clip starts")
        row.operator("bz.fx_event_time", text="At playhead", icon="TIME").mode = "PLAYHEAD"
    else:
        row.prop(p, "value", text="Time")
        row.operator("bz.fx_event_time", text="", icon="TIME").mode = "PLAYHEAD"
        row.operator("bz.fx_event_time", text="", icon="REW").mode = "START"
        frame = time_words(ent, ev, state, context.scene)
        if frame:
            _hint(box, frame, "TIME")
    # marker
    p = _pair(ev, "marker")
    row = box.row(align=True)
    if p is None:
        row.label(text="Marker: the model's origin")
        row.operator("bz.fx_pair_add", text="", icon="ADD").key = "marker"
    else:
        row.prop(p, "value", text="Marker")
    row.operator("bz.fx_marker_use", text="", icon="EMPTY_AXIS")
    row.operator("bz.fx_search", text="", icon="VIEWZOOM").kind = "marker"
    # effect and sound
    for key, label, kind in (("fx", "Effect", "fx"), ("sound", "Sound", "sound")):
        p = _pair(ev, key)
        row = box.row(align=True)
        if p is None:
            row.label(text=f"{label}: none")
            row.operator("bz.fx_pair_add", text="", icon="ADD").key = key
        else:
            row.prop(p, "value", text=label)
        row.operator("bz.fx_search", text="", icon="VIEWZOOM").kind = kind
        if key == "fx" and p is not None and p.value:
            op = row.operator("bz.fx_select", text="", icon="GREASEPENCIL")
            op.name = p.value
    # the other fields, as they are
    first = {}
    for i, q in enumerate(ev.pairs):
        if q.key in KNOWN and q.key not in first:
            first[q.key] = i
    more = [i for i, q in enumerate(ev.pairs) if first.get(q.key) != i]
    sub = box.column(align=True)
    if more:
        sub.label(text="Other fields (kept as they are)")
    for i in more:
        q = ev.pairs[i]
        r = sub.row(align=True)
        r.prop(q, "key", text="")
        r.prop(q, "value", text="")
        r.operator("bz.fx_pair_remove", text="", icon="X").index = i
    box.operator("bz.fx_pair_add", text="Add field...", icon="ADD")
    from ICParse.ebp.events import check_event
    probs = check_event(ev.name, [(q.key, q.value) for q in ev.pairs])
    for pr in probs[:3]:
        _hint(box, pr, "ERROR")
    note = fxcore.sound_timing_note(fxcore.EventItem(None, ev.name, [(q.key, q.value) for q in ev.pairs]))
    if note:
        _hint(box, note, "INFO")


def draw_sounds(layout, context) -> None:
    ent = _entity(context)
    state = _state(ent)
    if state is None:
        return
    _hint(layout, "Make your own sound from a WAV file and play it from an event. The game plays 16 bit WAV best; a sound that has a place in the world "
                  "should be mono. It cannot be played here: listen to it in the game.")
    mine = fxcore.project_sounds(_source(ent))
    if not mine:
        _hint(layout, "You have made no sound yet.")
    for name, value in mine:
        row = layout.row(align=True)
        row.label(text=name, icon="FILE_SOUND")
        row.operator("bz.fx_sound_use", text="Use", icon="CHECKMARK").value = value
        row.operator("bz.fx_sound_delete", text="", icon="TRASH").value = value
    layout.operator("bz.fx_sound_new", icon="ADD")
    for note in fxcore.sound_problems(_source(ent), _game_dir())[:3]:
        _hint(layout, note, "ERROR")


def draw_used(layout, context) -> None:
    ent = _entity(context)
    state = _state(ent)
    if state is None:
        return
    src, gd = _source(ent), _game_dir()
    names = fxcore.effects_named(items_of(state)) if state.loaded else []
    chain = fxcore.chain_cached(src, gd, names)
    if not chain:
        _hint(layout, "No effect is named by an event yet. Give an event an Effect, or copy one from the game below.")
    unused = fxcore.unused_project_effects(src, gd, names) if state.loaded else []
    if unused:
        _hint(layout, "In your project but named by no event, so the game never plays " + ("it" if len(unused) == 1 else "them") + ": "
              + ", ".join(unused[:4]) + (f" and {len(unused) - 4} more" if len(unused) > 4 else "")
              + ". Set the Effect of an event to " + ("it" if len(unused) == 1 else "one of them") + ", or delete the file.")
    for name, where, fx in chain:
        row = layout.row(align=True)
        icon = {"project": "FILE_TICK", "game": "PACKAGE", "missing": "ERROR"}[where]
        row.label(text=name, icon=icon)
        row.label(text=(fx.style[6:].title() if fx is not None else "?") + f" ({where})")
        row.operator("bz.fx_select", text="", icon="GREASEPENCIL").name = name
        if where == "game":
            op = row.operator("bz.fx_copy", text="", icon="COPYDOWN")
            op.name = name
    box = layout.box()
    box.prop(state, "prefix")
    row = box.row(align=True)
    row.operator("bz.fx_search", text="Copy a game effect", icon="COPYDOWN").kind = "copy_fx"
    row.operator("bz.fx_new", text="New", icon="ADD")
    layout.operator("bz.fx_search", text="Edit another effect", icon="GREASEPENCIL").kind = "edit_fx"


def draw_editor(layout, context) -> None:
    ent = _entity(context)
    state = _state(ent)
    if state is None:
        return
    if not state.fx_name:
        _hint(layout, "Choose an effect (the pencil next to it above) to see and edit its properties.")
        return
    row = layout.row(align=True)
    row.label(text=f"{state.fx_name}", icon="PARTICLES")
    row.operator("bz.fx_select", text="", icon="FILE_REFRESH").name = state.fx_name        # read the file again (an undo does not undo the file)
    if state.fx_where == "missing" or not state.fx_style:
        _hint(layout, "This effect is not in your project or the game's files.", "ERROR")
        return
    where = {"project": "in your project: every change is written to its file at once", "game": "the game's own: copy it into your project to edit"}[state.fx_where]
    _hint(layout, f"{state.fx_style[6:].title()} effect, {where}.")
    if fxcore.style_note(state.fx_style):
        _hint(layout, fxcore.style_note(state.fx_style), "INFO")
    editable = state.fx_where == "project"
    if state.message:
        _hint(layout, state.message, "ERROR")
    for line in [x for x in state.fx_problems.split("\n") if x][:4]:
        _hint(layout, "The game would not take this: " + line, "ERROR")
    if not editable:
        op = layout.operator("bz.fx_copy", text="Copy to my project", icon="COPYDOWN")
        op.name = state.fx_name
        return
    times = _times(state)
    keyed = bool(fxschema.STYLES.get(state.fx_style) and fxschema.STYLES[state.fx_style].keyed)
    if keyed:
        box = layout.box()
        row = box.row(align=True)
        row.prop(state, "fx_key", text=f"Key frame (of {len(times)})")
        k = min(state.fx_key, len(times) - 1)
        box.label(text=f"at time {times[k]:g} of the effect's life  (all key times: {state.fx_times})")
        row = box.row(align=True)
        op = row.operator("bz.fx_key", text="Add", icon="ADD")
        op.mode = "ADD"
        op = row.operator("bz.fx_key", text="Remove", icon="REMOVE")
        op.mode = "REMOVE"
        op = row.operator("bz.fx_key", text="Move", icon="TIME")
        op.mode = "MOVE"
    col = layout.column(align=True)
    for r in state.rows:
        sub = col.box() if r.kind == "colour" else col
        row = sub.row(align=True)
        if r.kind == "float":
            row.prop(r, "f", text=r.label)
        elif r.kind == "int":
            row.prop(r, "i", text=r.label)
        elif r.kind == "bool":
            row.prop(r, "b", text=r.label)
        elif r.kind == "enum":
            row.prop(r, _ENUM_PROP.get(r.name, "blend"), text=r.label)
        elif r.kind == "colour":
            row.prop(r, "c", text=r.label)
        elif r.kind == "vector":
            row.prop(r, "v", text=r.label)
        elif r.kind in ("texture", "mesh"):
            row.prop(r, "s", text=r.label.split(" ")[0])
            row.operator("bz.fx_pick_file", text="", icon="FILEBROWSER").prop = r.name
            if r.kind == "mesh":
                row.operator("bz.fx_use_model", text="", icon="MESH_CUBE")
        elif r.kind == "effect":
            row.prop(r, "s", text=r.label)
            op = row.operator("bz.fx_search", text="", icon="VIEWZOOM")
            op.kind = "row_fx"
            op.target = r.name
        else:
            row.prop(r, "s", text=r.label)
        if r.present:
            row.operator("bz.fx_reset", text="", icon="X").prop = r.name
    layout.operator("bz.fx_duplicate", text="Duplicate under a new name", icon="DUPLICATE")
    layout.operator("bz.fx_write_folder", text="Write effect files to a folder", icon="EXPORT")


# ---------------------------------------------------------------------------------------------------------- panels
class BZ_PT_fx(bpy.types.Panel):
    bl_label = "Effects and events"
    bl_idname = "BZ_PT_fx"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 30
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _state(_entity(context)) is not None

    def draw(self, context):
        ent = _entity(context)
        if is_creature(ent):
            _hint(self.layout, "Effects (smoke, sparks, beams) and sounds that the game starts at a moment of an animation. The events say when. "
                               "They are kept in your project and written by Export Creature for the Game (Game panel).")
        elif _is_effect_model(ent):
            _hint(self.layout, "An effect model is a small mesh that an effect draws. It has no blueprint and no events: an effect names it as its Mesh. "
                               "Export Entity Folder writes it into the folder data/art/fx.")
        else:
            _hint(self.layout, "Effects (smoke, sparks, beams) and sounds that the game starts at a moment of an animation. The events in the blueprint say "
                               "when. They are kept in your project and written by Export Entity Folder.")
        _hint(self.layout, "Effect files of this project: " + os.path.dirname(os.path.dirname(os.path.dirname(fxcore.effect_path(_source(ent), "x")))))


class BZ_PT_fx_events(bpy.types.Panel):
    bl_label = "Events"
    bl_idname = "BZ_PT_fx_events"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_fx"

    @classmethod
    def poll(cls, context):
        ent = _entity(context)
        return _state(ent) is not None and not _is_effect_model(ent)

    def draw(self, context):
        draw_events(self.layout, context)


class BZ_PT_fx_sounds(bpy.types.Panel):
    bl_label = "Sounds"
    bl_idname = "BZ_PT_fx_sounds"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_fx"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        ent = _entity(context)
        return _state(ent) is not None and not _is_effect_model(ent)

    def draw(self, context):
        draw_sounds(self.layout, context)


class BZ_PT_fx_used(bpy.types.Panel):
    bl_label = "Effects used"
    bl_idname = "BZ_PT_fx_used"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_fx"

    @classmethod
    def poll(cls, context):
        return _state(_entity(context)) is not None

    def draw(self, context):
        draw_used(self.layout, context)


class BZ_PT_fx_editor(bpy.types.Panel):
    bl_label = "Effect editor"
    bl_idname = "BZ_PT_fx_editor"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_fx"

    @classmethod
    def poll(cls, context):
        return _state(_entity(context)) is not None

    def draw(self, context):
        draw_editor(self.layout, context)


CLASSES = (BZ_PG_fx_pair, BZ_PG_fx_event, BZ_PG_fx_row, BZ_PG_fx, BZ_UL_fx_events,
           BZ_OT_fx_search, BZ_OT_fx_events_load, BZ_OT_fx_events_from_animal, BZ_OT_fx_sound_new, BZ_OT_fx_sound_use, BZ_OT_fx_sound_delete, BZ_OT_fx_event_add, BZ_OT_fx_event_remove, BZ_OT_fx_event_duplicate, BZ_OT_fx_event_time,
           BZ_OT_fx_marker_use, BZ_OT_fx_pair_add, BZ_OT_fx_pair_remove, BZ_OT_fx_copy, BZ_OT_fx_new, BZ_OT_fx_duplicate, BZ_OT_fx_key,
           BZ_OT_fx_reset, BZ_OT_fx_pick_file, BZ_OT_fx_use_model, BZ_OT_fx_select, BZ_OT_fx_write_folder,
           BZ_PT_fx, BZ_PT_fx_events, BZ_PT_fx_sounds, BZ_PT_fx_used, BZ_PT_fx_editor)


def register():
    """Point ``Object.bz_fx`` at the state (the classes are registered by the add-on's ``_CLASSES``)."""
    bpy.types.Object.bz_fx = PointerProperty(type=BZ_PG_fx)


def unregister():
    if hasattr(bpy.types.Object, "bz_fx"):
        del bpy.types.Object.bz_fx
