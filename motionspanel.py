"""Motions box (IC Game tab of the 3D view sidebar): which clip each motion of a creature or scene object plays, how fast, and when.

In the game a clip plays only through a *motion*: an entry of the blueprint's motion list that names the clip, with a speed rule (the clip plays faster
the faster the unit moves, around a reference speed) and a condition (it plays only while a variable such as ``Velocity`` is in a range). The game makes
motions by itself for the standard clip names (Idle, Walk, Run, Melee1 ...), so a clip with any other name plays only when you add a motion for it, and a
walk cycle made for another speed slides on the ground. This box lists the motions, lets you change them and add one for a new clip, and measures the
speed a walking clip was made for. ``Export Creature for the Game`` and ``Export Entity Folder`` write the changes into the blueprint (only the motions
you changed are rewritten; ``ICParse.ebp.motions``).

The state is on the creature's mesh object, or on the scene object's holder (the same object as the Blueprint panel's values). The list is kept in the
scene as plain values and compared with what was read: a motion counts as changed when it differs from that.

Logic without Blender is in ``motionscore.py``; the rules (decompiled game code) are in ``ICParse/ebp/motions.py``.
"""
from __future__ import annotations

import json
import os

import bpy
from .sceneobj import ui_kit
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

from . import motionscore as MC
from ICParse.ebp import motions as MO

_BUSY = [False]                      # set while rows are being filled (their update callbacks do nothing)
_KEEP: list = []                     # enum items must stay referenced while Blender shows them


# ---------------------------------------------------------------------------------------------------------- what the box works on
class Target:
    """What the box works on: a creature (the patch mesh) or a scene object (its parts)."""

    def __init__(self, kind, holder, rig, ent=None):
        self.kind = kind          # "creature" or "scene"
        self.holder = holder      # the object that carries the state
        self.rig = rig            # the armature (None for a model with no skeleton)
        self.ent = ent            # the scene entity (scene objects)

    @property
    def state(self):
        return getattr(self.holder, "bz_motions", None)

    @property
    def what(self) -> str:
        return "creature" if self.kind == "creature" else "object"


def _pb():
    from .sceneobj import panels_blueprint
    return panels_blueprint


def creature_mesh(obj):
    """The creature's patch mesh for ``obj`` (the mesh itself, or its armature)."""
    from . import store
    if obj is None:
        return None
    if store.is_native(obj) and not obj.get("bz_scene"):
        return obj
    if obj.type == "ARMATURE":
        for m in bpy.data.objects:
            if store.is_native(m) and not m.get("bz_scene") and store.rig(m) is obj:
                return m
    return None


def target_of(obj) -> "Target | None":
    from . import store
    m = creature_mesh(obj)
    if m is not None:
        return Target("creature", m, store.rig(m))
    ent = _pb().entity_of(obj)
    if ent is not None and _pb()._stamp(ent, "bz_project") != "EFFECT":
        return Target("scene", ent.holder, ent.rig, ent)
    return None


def _context_target(context):
    return target_of(getattr(context, "active_object", None))


# ---------------------------------------------------------------------------------------------------------- the clips
def clip_info(t: Target):
    """``(clips, new, actions)``: the clip names the export writes (the rig's Actions), the names that are new (made or borrowed in Blender, so
    the blueprint cannot know them), and {clip name: Action}."""
    from . import actions as bz
    from . import scenesafety
    clips, new, acts = [], set(), {}
    if t.rig is None:
        return clips, new, acts
    try:
        plan = bz.plan_export(t.rig, renames=(t.kind == "creature"))
        renamed = set(scenesafety.clip_renames(plan)[0].values())       # a clip renamed in Blender is the blueprint's own clip under a new name
        for c in plan.kept:
            clips.append(str(c))
        for a, c in plan.encode:
            clips.append(str(c))
            acts[str(c)] = a
            src = a.get(bz.KEY_CLIP)
            if (src is None or str(src) != str(c)) and str(c) not in renamed:
                new.add(str(c))
        for a in bz.actions_of(t.rig):
            s = a.get(bz.KEY_CLIP)
            if s is not None:
                acts.setdefault(str(s), a)
    except Exception:                                                     # noqa: BLE001 -- two Actions on one clip name etc.: the export says so
        for a in bz.actions_of(t.rig):
            c = str(a.get(bz.KEY_CLIP) or a.name)
            clips.append(c)
            acts[c] = a
            if a.get(bz.KEY_CLIP) is None:
                new.add(c)
    return list(dict.fromkeys(clips)), new, acts


def clip_renames(t: Target) -> dict:
    """``{clip name in the blueprint: new name}`` for the clips renamed in Blender: the export makes the blueprint's motions (and events) name the new
    name (``scenesafety.follow_clips_in_blueprint``; a creature's donor: ``ICParse.ebp.package.follow_renames``)."""
    from . import actions as bz
    from . import scenesafety
    if t.rig is None:
        return {}
    try:
        return dict(scenesafety.clip_renames(bz.plan_export(t.rig, renames=(t.kind == "creature")))[0])
    except Exception:                                                     # noqa: BLE001 -- two Actions on one clip name etc.: the export says so
        return {}


def table_as_exported(t: Target, table: MO.MotionTable) -> MO.MotionTable:
    """The table with the clip renames made since the model was read, as the export will write them (what the checks compare with the rig's clips)."""
    ren = clip_renames(t)
    return MO.rename_clips(table, ren) if ren else table


def clip_seconds(act, fallback_fps: float = 30.0):
    """``(first frame, last frame, seconds)`` of an Action the way the export reads it (the frames are the clip's steps, the rate the Action's)."""
    from . import actions as bz
    lo, hi = bz.frame_range(act)
    if hi <= lo:
        hi = lo + 1
    fps = float(act.get(bz.KEY_FPS, 0) or fallback_fps)
    return lo, hi, (hi - lo) / fps


def durations(t: Target, scene) -> dict:
    out = {}
    _clips, _new, acts = clip_info(t)
    for c, a in acts.items():
        try:
            out[c] = clip_seconds(a, float(scene.render.fps))[2]
        except Exception:                                                 # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------------------------------------------------- property groups
class BZ_PG_motion(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", description="The motion's name. The game starts some motions by name (Idle, Walk, Run, Melee1 ...): "
                         "keep those names")
    clip: StringProperty(name="Clip", description="The clip this motion plays. A name the model does not have makes the motion do nothing")
    children: StringProperty(description="The motions started with this one (their numbers)")
    loop: BoolProperty(name="Loop", default=True, description="Play the clip again and again; off plays it once")
    invert_speed: BoolProperty(name="Invert", description="Play slower the bigger the variable is (for an attack that must fit a time)")
    sync_children: BoolProperty(name="Keep children in step", description="Start the clips of the children at the same place in their loops")
    absolute_time: BoolProperty(name="Variable is the position in the clip", description="The speed variable (0 to 1) sets where in the clip to be, "
                                "instead of how fast to play")
    playback_speed: FloatProperty(name="Play speed", default=1.0, precision=3, soft_min=-1000.0, soft_max=1000.0,
                                  description="1 plays the clip at its own pace")
    start_delay: FloatVectorProperty(name="Start delay", size=2, precision=2, soft_min=0.0, description="Seconds to wait before the clip starts (from, to: "
                                     "a time in between is picked at random)")
    loop_delay: FloatVectorProperty(name="Pause between loops", size=2, precision=2, soft_min=0.0, description="Seconds to wait before the clip plays "
                                    "again (from, to)")
    blend_weight: FloatProperty(name="Strength", default=1.0, precision=2, soft_min=0.0, soft_max=1.0, description="How much of this clip shows (1 all of it)")
    blend_lower_weight: FloatProperty(name="Let lower clips through", default=0.0, precision=2, soft_min=0.0, soft_max=1.0,
                                      description="How much of the clips below this one shows through")
    fade_in: FloatProperty(name="Fade in", default=0.5, precision=2, soft_min=0.0, description="Seconds to blend this clip in")
    fade_out: FloatProperty(name="Fade out", default=0.0, precision=2, soft_min=0.0, description="Seconds to blend this clip out")
    speed_var: StringProperty(name="Speed follows", description="The variable that sets how fast the clip plays (Velocity is the unit's speed). "
                              "Empty: always at the play speed")
    speed_ref: FloatProperty(name="Made for", default=0.0, precision=3, soft_min=0.0, soft_max=1e6,
                             description="The speed the clip was made for: the clip plays at its own pace when the variable has this value, "
                                         "twice as fast at twice this. 0 does not use the variable")
    cond_var: StringProperty(name="Plays while", description="The variable this motion's range is about (Velocity is the unit's speed). Empty: always plays")
    cond_min: FloatProperty(name="Above", default=-1.0, precision=3, soft_min=-1.0, soft_max=1e6,
                            description="Plays only when the variable is above this (-1: no lower limit)")
    cond_max: FloatProperty(name="Up to", default=-1.0, precision=3, soft_min=-1.0, soft_max=1e6,
                            description="Plays only when the variable is at most this (-1: no upper limit)")
    src: IntProperty(default=-1, description="Which motion of the blueprint this is; -1 for one added here")
    template: IntProperty(default=-1, description="The motion of the blueprint a new motion was copied from")


class BZ_PG_motion_item(bpy.types.PropertyGroup):
    node: IntProperty()
    depth: IntProperty()


class BZ_PG_motions(bpy.types.PropertyGroup):
    """The Motions box's state of one creature or object."""
    loaded: BoolProperty(default=False)
    has_list: BoolProperty(default=True, description="The blueprint has a motion list of its own")
    baseline: StringProperty(description="The motions as they were read (JSON): a motion counts as changed when it differs from this")
    rows: CollectionProperty(type=BZ_PG_motion)
    outline: CollectionProperty(type=BZ_PG_motion_item)
    index: IntProperty(default=0)
    default: IntProperty(default=-1)
    version: IntProperty(default=4)
    big_endian: BoolProperty(default=False)
    note: StringProperty()
    message: StringProperty()
    measure: StringProperty(description="The measured speeds (JSON)")


# ---------------------------------------------------------------------------------------------------------- state <-> table
def table_of(state) -> MO.MotionTable:
    rows = []
    for i, p in enumerate(state.rows):
        r = MO.MotionRow(name=p.name, children=[int(x) for x in p.children.split(",") if x.strip() != ""], clip=p.clip, loop=bool(p.loop),
                         invert_speed=bool(p.invert_speed), sync_children=bool(p.sync_children), playback_speed=float(p.playback_speed),
                         start_delay=tuple(float(x) for x in p.start_delay), loop_delay=tuple(float(x) for x in p.loop_delay),
                         blend_weight=float(p.blend_weight), blend_lower_weight=float(p.blend_lower_weight), fade_in=float(p.fade_in),
                         fade_out=float(p.fade_out), speed_var=p.speed_var, speed_ref=float(p.speed_ref), cond_var=p.cond_var,
                         cond_min=float(p.cond_min), cond_max=float(p.cond_max),
                         absolute_time=(bool(p.absolute_time) if state.version >= 4 else None),
                         src=None if p.src < 0 else int(p.src), template=None if p.template < 0 else int(p.template), index=i)
        rows.append(r)
    return MO.MotionTable(rows, int(state.default), int(state.version), bool(state.big_endian))


def baseline_of(state) -> MO.MotionTable:
    return MO.table_from_dict(json.loads(state.baseline)) if state.baseline else MO.MotionTable([], -1, int(state.version), bool(state.big_endian))


def fill(state, table: MO.MotionTable, keep_index: bool = False) -> None:
    """Show a table in the state (the rows, the default motion, the outline)."""
    _BUSY[0] = True
    try:
        state.rows.clear()
        for r in table.rows:
            p = state.rows.add()
            p.name, p.clip, p.children = r.name, r.clip, ",".join(str(c) for c in r.children)
            p.loop, p.invert_speed, p.sync_children = r.loop, r.invert_speed, r.sync_children
            p.absolute_time = bool(r.absolute_time)
            p.playback_speed = r.playback_speed
            p.start_delay, p.loop_delay = list(r.start_delay), list(r.loop_delay)
            p.blend_weight, p.blend_lower_weight, p.fade_in, p.fade_out = r.blend_weight, r.blend_lower_weight, r.fade_in, r.fade_out
            p.speed_var, p.speed_ref, p.cond_var, p.cond_min, p.cond_max = r.speed_var, r.speed_ref, r.cond_var, r.cond_min, r.cond_max
            p.src = -1 if r.src is None else int(r.src)
            p.template = -1 if r.template is None else int(r.template)
        state.default = int(table.default)
        state.version = int(table.version)
        state.big_endian = bool(table.big_endian)
    finally:
        _BUSY[0] = False
    node = active_node(state) if keep_index else -1
    rebuild_outline(state)
    if node >= 0:
        select_node(state, node)


def rebuild_outline(state) -> None:
    state.outline.clear()
    for node, depth in table_of(state).outline():
        it = state.outline.add()
        it.node, it.depth = node, depth
    state.index = min(max(state.index, 0), max(len(state.outline) - 1, 0))


def active_node(state) -> int:
    return int(state.outline[state.index].node) if 0 <= state.index < len(state.outline) else -1


def select_node(state, node: int) -> None:
    for i, it in enumerate(state.outline):
        if it.node == node:
            state.index = i
            return


def edited_words(state) -> str:
    if not state.loaded:
        return ""
    return MC.changes_words(baseline_of(state), table_of(state))


# ---------------------------------------------------------------------------------------------------------- loading
def blueprint_bytes(t: Target):
    """``(the blueprint's bytes or None, where it is from)``: a scene object's own ``.ebp``, a creature's donor blueprint."""
    if t.kind == "scene":
        path = _pb().blueprint_path(t.ent)
        if not path:
            return None, "This object has no blueprint file yet."
        try:
            with open(path, "rb") as f:
                return f.read(), os.path.basename(path)
        except OSError as e:
            raise MO.MotionError(f"The blueprint file could not be read: {e.strerror or e}") from None
    from . import gameexport
    try:
        don = gameexport.find_donor(t.holder)
    except Exception:                                                     # noqa: BLE001 -- no game folder: the export says so
        don = None
    if don is None:
        return None, "This creature was built from nothing, so its export makes a blueprint from the clips."
    return bytes(don.ebp), f"the blueprint of {don.label}"


def load(t: Target, scene) -> str:
    """Read the motions into the box. Returns one plain line ('' when all is well); raises :class:`MotionError` when the blueprint cannot be read."""
    from ICParse.iff.document import IffDocument
    state = t.state
    data, where = blueprint_bytes(t)
    clips, _new, _acts = clip_info(t)
    table = None
    if data is not None:
        try:
            table = MO.motion_table(IffDocument.from_bytes(data))
        except MO.MotionError:
            raise
        except Exception as e:                                            # noqa: BLE001
            raise MO.MotionError(f"The blueprint could not be read: {e}") from None
    note = ""
    state.has_list = True
    if table is None:
        state.has_list = False
        if t.kind == "creature":
            table = MO.table_from_clips(clips, durations(t, scene))      # what the export generates: the model's own clip lengths (Make the Motion List does the same)
            for i, r in enumerate(table.rows):
                r.src = r.template = i
            if data is None:
                note = "This creature was built from nothing: the export makes the standard motions from the clips, and changes here are applied to them."
            else:
                note = ("The blueprint of the animal you started from has no motion list of its own, so motion changes cannot be exported for this "
                        "creature: start from an animal that has one, or leave the motions as they are.")
        else:
            table = MO.MotionTable([], -1, 4, False)
            what = where if data is None else "This object's blueprint has no motion list of its own."
            note = (f"{what} The game makes motions for the standard clip names (Idle, Walk, Run, Melee1 ...) when it loads. To give another "
                    f"clip a motion, press Make the Motion List.")
    else:
        note = f"Read from {where}."
    state.baseline = json.dumps(MO.table_to_dict(table))
    state.index = 0
    state.measure = ""
    state.message = ""
    state.note = note
    fill(state, table)
    state.loaded = True
    return note


def ensure_loaded(t: Target, scene) -> bool:
    if t.state is not None and not t.state.loaded:
        load(t, scene)
    return t.state is not None and t.state.loaded


# ---------------------------------------------------------------------------------------------------------- export
def _new_clips(t: Target, plan=None) -> list:
    clips, new, _acts = clip_info(t)
    return [c for c in clips if c in new]


def creature_export(obj, donor):
    """For ``Export Creature for the Game``: ``(motions argument for build_creature_item or None, notes)``. The notes name each new clip no motion plays
    and each problem the edits bring."""
    from ICParse.iff.document import IffDocument
    t = target_of(obj)
    if t is None or t.state is None:
        return None, []
    state = t.state
    clips = clip_info(t)[0]
    new = _new_clips(t)
    current = None                                                        # the motion list the blueprint will have before the edits
    if donor is not None:
        try:
            current = MO.motion_table(IffDocument.from_bytes(bytes(donor.ebp)))
        except Exception:                                                 # noqa: BLE001
            current = None
        if current is not None:
            current = table_as_exported(t, current)                       # the export renames the clips the modder renamed
    else:
        current = MO.table_from_clips(clips)
    if state.loaded and MC.changes(baseline_of(state), table_of(state)) != (0, 0, 0):
        base, edited = baseline_of(state), table_of(state)
        try:
            table = MO.merge_edits(current, base, edited) if current is not None else edited
        except MO.MotionError:
            table = edited                                                # the export itself refuses and says why
        return (base, edited), MC.export_notes(table, new, baseline=table_as_exported(t, base))
    return None, MC.export_notes(current, new)


def export_entity_ebp(ent, ebp_data: bytes, plan=None):
    """For ``Export Entity Folder``, after the blueprint's other edits: ``(the blueprint bytes with the Motions box's edits, notes)``."""
    from ICParse.iff.document import IffDocument
    PB = _pb()
    holder = ent.holder
    state = getattr(holder, "bz_motions", None)
    t = Target("scene", holder, ent.rig, ent)
    new = _new_clips(t)
    notes = []
    if state is not None and state.loaded and MC.changes(baseline_of(state), table_of(state)) != (0, 0, 0):
        base, edited = baseline_of(state), table_of(state)
        try:
            sb = PB.scene_lib().SceneBlueprint.read(ebp_data)
            MO.apply_edits(sb, base, edited)
            out = sb.to_bytes()
            now = MO.motion_table(sb)
        except MO.MotionError as e:
            raise PB.Refused(f"The motions cannot be written into this blueprint: {e}") from None
        except ValueError as e:
            raise PB.Refused(f"The motions cannot be written into this blueprint: {e}") from None
        notes.append("Motions: " + MC.changes_words(base, edited))
        notes += MC.export_notes(now, new, baseline=base)
        return out, notes
    try:
        table = MO.motion_table(IffDocument.from_bytes(ebp_data))
    except Exception:                                                     # noqa: BLE001
        table = None
    notes += MC.export_notes(table, new)
    return ebp_data, notes


# ---------------------------------------------------------------------------------------------------------- operators
def _poll_target(context) -> bool:
    t = _context_target(context)
    return t is not None and t.state is not None


def _poll_loaded(context) -> bool:
    t = _context_target(context)
    return t is not None and t.state is not None and t.state.loaded


def _poll_row(context) -> bool:
    t = _context_target(context)
    return t is not None and t.state is not None and t.state.loaded and active_node(t.state) >= 0


def _clip_items(self, context):
    t = _context_target(context)
    names = clip_info(t)[0] if t is not None else []
    items = [("SAME", "(the same clip as the original)", "")] if getattr(self, "bl_idname", "") == "bz.motions_add_copy" else []
    items += [(n, n, "") for n in names]
    if not items:
        items = [("NONE", "(no clips)", "")]
    _KEEP[:] = items
    return _KEEP


class BZ_OT_motions_load(bpy.types.Operator):
    """Read the motions of this blueprint (throws away motion edits not yet exported)"""
    bl_idname = "bz.motions_load"
    bl_label = "Load Motions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_target(context)

    def execute(self, context):
        t = _context_target(context)
        try:
            load(t, context.scene)
        except MO.MotionError as e:
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        return {"FINISHED"}


class BZ_OT_motions_start(bpy.types.Operator):
    """Make the motion list from the clips: the standard motions for Idle, Walk, Run, the attacks and so on, to change and add to"""
    bl_idname = "bz.motions_start"
    bl_label = "Make the Motion List"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        t = _context_target(context)
        return t is not None and t.state is not None and t.state.loaded and not t.state.has_list and not len(t.state.rows)

    def execute(self, context):
        t = _context_target(context)
        clips, _n, _a = clip_info(t)
        n = make_standard_list(t, context.scene)
        self.report({"INFO"}, f"Made {n} motions from {len(clips)} clips. Change them, or add one for a clip of your own.")
        return {"FINISHED"}


class BZ_OT_motions_pick_clip(bpy.types.Operator):
    """Choose a clip of this model for the motion"""
    bl_idname = "bz.motions_pick_clip"
    bl_label = "Choose Clip"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"

    choice: EnumProperty(name="Clip", items=_clip_items)

    @classmethod
    def poll(cls, context):
        return _poll_row(context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        t = _context_target(context)
        if self.choice in ("", "NONE"):
            return {"CANCELLED"}
        t.state.rows[active_node(t.state)].clip = self.choice
        return {"FINISHED"}


class BZ_OT_motions_add_copy(bpy.types.Operator):
    """Add a motion that starts as a copy of the chosen one: the same speed rule, condition and place in the list. Give it the clip you made, then change its
    range: where both motions start, the one listed first covers the other"""
    bl_idname = "bz.motions_add_copy"
    bl_label = "Add Copy"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", description="The new motion's name. Empty: the original's name with a number")
    clip: EnumProperty(name="Clip", items=_clip_items, description="The clip the new motion plays")
    same_parents: BoolProperty(name="Start it wherever the original starts", default=True,
                               description="Also list it as a child of every motion that lists the original, so it is started there. Off: only "
                                           "a motion that lists it, or the game's rules starting it by name, start it")
    on_top: BoolProperty(name="List it before the original", default=False,
                         description="The motion listed first is on top and covers the other wherever both start. Off: the copy goes after the "
                                     "original and is seen only where the original does not play (give it another range). On: the copy covers "
                                     "the original wherever both start")

    @classmethod
    def poll(cls, context):
        return _poll_row(context)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "name")
        col.prop(self, "clip")
        col.prop(self, "same_parents")
        sub = col.column()
        sub.active = self.same_parents
        sub.prop(self, "on_top")
        _hint(col, "The copy keeps the original's range, so where both start only one is seen. After adding it, change its range (Above / Up to) "
                   "so that the speeds your clip is for are not the original's.")

    def invoke(self, context, event):
        t = _context_target(context)
        acts = clip_info(t)[2]
        names = clip_info(t)[0]
        try:                                                              # the clip the rig shows now is the likeliest wanted
            cur = t.rig.animation_data.action
            self.clip = next((c for c in names if acts.get(c) is cur), "SAME")
        except Exception:                                                 # noqa: BLE001
            self.clip = "SAME"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        t = _context_target(context)
        state = t.state
        table = table_of(state)
        node = active_node(state)
        try:
            n = table.add_copy(node, self.name.strip() or None, self.same_parents, self.on_top)
            if self.clip not in ("SAME", "NONE", ""):
                table.rows[n].clip = self.clip
            MO.check_name(table.rows[n].name)
        except MO.MotionError as e:
            self.report({"ERROR"}, ui_kit.plain(str(e)))
            return {"CANCELLED"}
        fill(state, table)
        select_node(state, n)
        row = table.rows[n]
        orig = table.rows[node].name
        both = ""
        if self.same_parents and table.parents(node):
            both = (f" It is listed {'before' if self.on_top else 'after'} '{orig}' and has its range, so where both play, "
                    + (f"the copy covers '{orig}'." if self.on_top else f"'{orig}' covers the copy."))
        self.report({"INFO"}, f"Added the motion '{row.name}'" + (f" playing '{row.clip}'" if row.clip else "") + ". " + MC.start_words(table, n)
                              + both + " Set its speed and range below.")
        return {"FINISHED"}


class BZ_OT_motions_delete(bpy.types.Operator):
    """Delete the chosen motion (it leaves every list of children; the others keep their place)"""
    bl_idname = "bz.motions_delete"
    bl_label = "Delete"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_row(context)

    def execute(self, context):
        t = _context_target(context)
        state = t.state
        table = table_of(state)
        before = table_as_exported(t, table)
        node = active_node(state)
        old = table.delete(node)
        fill(state, table)
        state.index = min(state.index, max(len(state.outline) - 1, 0))
        msg = f"Deleted the motion '{old.name}'."
        if MO.comes_back(before, table_as_exported(t, table), clip_info(t)[0], old.name):
            msg += (" The game makes a motion like it by itself when the model loads (it makes the standard ones that the blueprint lacks), so it "
                    "comes back. Change the motion instead of deleting it, or give the clip another name.")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BZ_OT_motions_default(bpy.types.Operator):
    """Make the chosen motion the one the game starts with when the model loads"""
    bl_idname = "bz.motions_default"
    bl_label = "Start With This"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll_row(context)

    def execute(self, context):
        t = _context_target(context)
        t.state.default = active_node(t.state)
        rebuild_outline(t.state)
        select_node(t.state, t.state.default)
        return {"FINISHED"}


class BZ_OT_motions_play(bpy.types.Operator):
    """Show this motion's clip on the rig (sets the active Action) and play it from its first frame"""
    bl_idname = "bz.motions_play"
    bl_label = "Play this clip"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        t = _context_target(context)
        if t is None or t.state is None or not t.state.loaded or t.rig is None:
            return False
        n = active_node(t.state)
        return n >= 0 and bool(t.state.rows[n].clip)

    def execute(self, context):
        from . import actions as bz
        t = _context_target(context)
        clip = t.state.rows[active_node(t.state)].clip
        acts = clip_info(t)[2]
        act = next((a for c, a in acts.items() if c.lower() == clip.lower()), None)
        if act is None:
            self.report({"WARNING"}, f"The rig has no clip '{clip}' (the Actions are the clips). Make or borrow one first.")
            return {"CANCELLED"}
        bz._assign(t.rig, act)
        lo, hi, secs = clip_seconds(act, float(context.scene.render.fps))
        context.scene.frame_start, context.scene.frame_end, context.scene.frame_current = lo, hi, lo
        self.report({"INFO"}, f"'{clip}' is the rig's Action now: {hi - lo} steps, {secs:.2f} seconds. Press Space in the 3D view to play it.")
        return {"FINISHED"}


class BZ_OT_motions_measure(bpy.types.Operator):
    """Measure the speed each walking or running clip (a motion whose speed follows Velocity) moves the model at, to set the value it was made for"""
    bl_idname = "bz.motions_measure"
    bl_label = "Measure Walk and Run Speeds"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        t = _context_target(context)
        return t is not None and t.state is not None and t.state.loaded and t.rig is not None

    def execute(self, context):
        import numpy as np
        from . import actions as bz, rig as bzrig
        t = _context_target(context)
        state = t.state
        acts = clip_info(t)[2]
        names = [b.name for b in t.rig.data.bones]
        root = next((i for i, b in enumerate(t.rig.data.bones) if b.parent is None), 0)
        scale = float(np.mean(t.rig.matrix_world.to_scale()[:])) or 1.0
        out, done = [], {}
        for i, r in enumerate(state.rows):
            if not MC.follows_ground_speed(r):                            # only a speed rule on Velocity is a ground speed (not an attack's time)
                continue
            act = next((a for c, a in acts.items() if c.lower() == r.clip.lower()), None)
            if act is None:
                continue
            key = r.clip.lower()
            if key not in done:
                lo, hi, secs = clip_seconds(act, float(context.scene.render.fps))
                try:
                    W = bz.sample_world(t.rig, act, names, lo, hi)
                    pts = np.array([[list(bzrig.to_blender_matrix(np.asarray(fr[b], dtype=np.float64)).translation) for b in range(len(names))]
                                    for fr in W]) * scale
                    done[key] = MC.ground_speed(pts, secs, up=2, root=root)
                except Exception as e:                                    # noqa: BLE001
                    self.report({"WARNING"}, f"Could not sample '{r.clip}': {e}")
                    done[key] = None
            sp = done[key]
            if sp is None:
                out.append({"node": i, "clip": r.clip, "speed": None, "how": "", "now": float(r.speed_ref)})
            else:
                out.append({"node": i, "clip": r.clip, "speed": sp.speed * float(r.playback_speed), "how": sp.how, "now": float(r.speed_ref)})
        state.measure = json.dumps(out)
        if not out:
            self.report({"INFO"}, "No motion of this model plays a clip at a speed that follows Velocity (the unit's speed): nothing to measure.")
        return {"FINISHED"}


class BZ_OT_motions_use_speed(bpy.types.Operator):
    """Use the measured speed as the speed this motion's clip was made for"""
    bl_idname = "bz.motions_use_speed"
    bl_label = "Use"
    bl_options = {"REGISTER", "UNDO"}

    node: IntProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _poll_loaded(context)

    def execute(self, context):
        t = _context_target(context)
        state = t.state
        try:
            hit = next(m for m in json.loads(state.measure or "[]") if m["node"] == self.node and m["speed"])
        except StopIteration:
            return {"CANCELLED"}
        if not 0 <= self.node < len(state.rows):
            return {"CANCELLED"}
        if state.rows[self.node].speed_var.lower() != "velocity" or state.rows[self.node].invert_speed:
            self.report({"WARNING"}, "This motion's speed no longer follows Velocity: measure again.")
            return {"CANCELLED"}
        state.rows[self.node].speed_ref = round(float(hit["speed"]), 3)
        hit["now"] = state.rows[self.node].speed_ref
        state.measure = json.dumps([hit if m["node"] == self.node else m for m in json.loads(state.measure)])
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------- the panel
class BZ_UL_motions(bpy.types.UIList):
    bl_idname = "BZ_UL_motions"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        state = data
        node = item.node
        if not 0 <= node < len(state.rows):
            return
        r = state.rows[node]
        row = layout.row(align=True)
        row.label(text=("      " * item.depth) + (r.name or "(no name)"), icon="PLAY" if node == state.default else "NONE")
        what = r.clip or ""
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=what)


def _hint(layout, text: str, icon: str = "NONE") -> None:
    import textwrap
    col = layout.column(align=True)
    for i, line in enumerate(textwrap.wrap(ui_kit.plain(text), 44) or [""]):
        col.label(text=line, icon=icon if i == 0 else "NONE")


def _load_by_name(name: str) -> None:
    """Read the motions of the object called ``name`` (the panel's first drawing asks for it)."""
    t = target_of(bpy.data.objects.get(name))
    if t is not None and t.state is not None and not t.state.loaded:
        load(t, bpy.context.scene)


def draw_motions(layout, context) -> None:
    t = _context_target(context)
    if t is None or t.state is None:
        return
    state = t.state
    clips, new, _acts = clip_info(t)
    if not state.loaded:
        from . import autoload
        name = t.holder.name
        autoload.once(("motions", t.holder.session_uid), lambda: _load_by_name(name))
        _hint(layout, "A clip plays only through a motion of the blueprint. The motions are read when this panel opens; if nothing shows, press "
                      "Load Motions to try again.")
        layout.operator("bz.motions_load", icon="FILE_REFRESH")
        return
    if state.note:
        _hint(layout, state.note, "INFO")
    if not len(state.rows):
        if not state.has_list:                                           # the button only works for a blueprint with no motion list of its own
            layout.operator("bz.motions_start", icon="ADD")
        else:
            _hint(layout, "This blueprint has a motion list with no motions in it, so there is nothing to change here.", "INFO")
        layout.operator("bz.motions_load", text="Load Again", icon="FILE_REFRESH")
        return
    ren_low = {k.lower(): v for k, v in clip_renames(t).items()}
    tab = table_as_exported(t, table_of(state))
    layout.template_list("BZ_UL_motions", "", state, "outline", state, "index", rows=6)
    row = layout.row(align=True)
    row.operator("bz.motions_add_copy", text="Add Copy", icon="DUPLICATE")
    row.operator("bz.motions_delete", text="Delete", icon="REMOVE")
    row.operator("bz.motions_default", text="", icon="PLAY")
    words = edited_words(state)
    if words:
        _hint(layout, f"Motions {words}: Export writes them into the blueprint.", "INFO")
    nodes = active_node(state)
    if nodes >= 0:
        p = state.rows[nodes]
        box = layout.box()
        box.prop(p, "name", text="Name")
        box.label(text="The clip it plays", icon="ACTION")
        row = box.row(align=True)
        row.prop(p, "clip", text="")
        row.operator("bz.motions_pick_clip", text="", icon="VIEWZOOM")
        row.operator("bz.motions_play", text="", icon="PLAY")
        shown = ren_low.get(p.clip.lower(), p.clip)                       # a clip renamed in Blender: the export renames it in the motion too
        if p.clip and shown not in clips and not p.clip.lower().startswith("proc:") and t.rig is not None:
            _hint(box, f"The rig has no clip called '{p.clip}': this motion would do nothing.", "ERROR")
        elif shown != p.clip:
            _hint(box, f"The clip was renamed '{shown}' on the rig: the export writes the new name here.", "INFO")
        box.prop(p, "loop")
        sp = box.box()
        sp.label(text="Speed", icon="DRIVER")
        sp.prop(p, "speed_var", text="Speed follows")
        sp.prop(p, "speed_ref", text="Clip made for")
        _hint(sp, "Clip made for: the value of that variable at which the clip plays at its own pace. Velocity is the unit's speed.")
        row = sp.row(align=True)
        row.prop(p, "playback_speed", text="Play speed")
        row.prop(p, "invert_speed", toggle=True)
        cn = box.box()
        cn.label(text="Plays only while", icon="FILTER")
        cn.prop(p, "cond_var", text="Variable")
        row = cn.row(align=True)
        row.prop(p, "cond_min", text="Above")
        row.prop(p, "cond_max", text="Up to")
        _hint(cn, "-1 means no limit. Velocity is the unit's speed (the game moves it); the rules set Build, Damage, AttackTime and a few more.")
        kids = [state.rows[int(c)].name for c in p.children.split(",") if c.strip() != "" and 0 <= int(c) < len(state.rows)]
        _hint(box, ("It also starts: " + ", ".join(kids)) if kids else "It has no child motions.")
        more = box.column(align=True)
        more.label(text="More")
        more.prop(p, "fade_in")
        more.prop(p, "fade_out")
        more.prop(p, "blend_weight")
        more.prop(p, "blend_lower_weight")
        more.prop(p, "start_delay", text="Start delay")
        more.prop(p, "loop_delay", text="Pause between loops")
        more.prop(p, "sync_children")
        if state.version >= 4:
            more.prop(p, "absolute_time")
    # walk and run speeds
    box = layout.box()
    box.label(text="Walk and run speeds", icon="DRIVER")
    box.operator("bz.motions_measure", icon="DRIVER")
    for m in json.loads(state.measure or "[]"):
        row = box.row(align=True)
        if m["speed"] is None:
            row.label(text=f"{m['clip']}: no answer (no foot on the ground)")
            continue
        how = "the unit travels" if m["how"] == "root" else "the feet on the ground"
        row.label(text=f"{m['clip']}: {m['speed']:.2f} (now {m['now']:.2f}, from {how})")
        row.operator("bz.motions_use_speed", text="Use").node = int(m["node"])
    known = set(MO.motion_check(table_as_exported(t, baseline_of(state)), clips if t.rig is not None else None))      # what the blueprint had before
    problems = [x for x in MO.motion_check(tab, clips if t.rig is not None else None) if x not in known]
    if problems:
        pb = layout.box()
        pb.label(text="Check what you changed", icon="ERROR")
        for ptxt in problems[:5]:
            _hint(pb, ptxt)
        if len(problems) > 5:
            pb.label(text=f"... and {len(problems) - 5} more")
    unplayed = MO.clips_without_motion(tab, [c for c in clips if c in new])
    for c in unplayed:
        _hint(layout, MC.unplayed_note(c), "ERROR")
    for ptxt in MC.unstarted_notes(tab, table_as_exported(t, baseline_of(state))):
        _hint(layout, ptxt, "ERROR")
    if state.message:
        _hint(layout, state.message)


def idle_clip_of(clips) -> str:
    """The model's idle clip (the game's own name for it is Idle1, older models Idle), '' when it has none."""
    low = {c.lower(): c for c in clips}
    return low.get("idle1") or low.get("idle") or ""


def _copy_items(self, context):
    """The clips of the model that can be copied (each has an Action on the rig), the idle clip first. With no idle clip the first row asks for a choice."""
    t = _context_target(context)
    names, acts = [], {}
    if t is not None:
        names, _new, acts = clip_info(t)
    have = [c for c in names if c in acts]
    idle = idle_clip_of(have)
    ordered = ([idle] if idle else []) + [c for c in have if c != idle]
    items = [(c, c + (" (the idle clip)" if c == idle else ""), "Start the new clip as a copy of this one") for c in ordered]
    if not idle:
        items.insert(0, ("", "(choose a clip)", "Choose the clip the new one starts from"))
    _KEEP[:] = items
    return _KEEP


def _instead_items(self, context):
    """The motions of the blueprint that play a clip, for 'Play it instead of ...'."""
    t = _context_target(context)
    items = []
    if t is not None and t.state is not None and t.state.loaded:
        for node, _depth in [(it.node, it.depth) for it in t.state.outline]:
            r = t.state.rows[node]
            if r.clip and not r.clip.lower().startswith("proc:"):
                items.append((str(node), r.name, f"plays '{r.clip}'"))
    if not items:
        items = [("", "(no motion plays a clip)", "")]
    _KEEP[:] = items
    return _KEEP


def make_standard_list(t: Target, scene) -> int:
    """Make the motion list of a blueprint that has none, from the clips (what Make the Motion List does). Returns the number of motions."""
    clips, _n, _a = clip_info(t)
    table = MO.table_from_clips(clips, durations(t, scene))
    t.state.baseline = json.dumps(MO.table_to_dict(MO.MotionTable([], -1, table.version, table.big_endian)))
    fill(t.state, table)
    t.state.note = "The standard motions, made from the clips. Export writes them into the blueprint."
    return len(table.rows)


def give_motion(t: Target, scene, clip: str, how: str, instead: int = -1) -> str:
    """Give the clip ``clip`` a motion that something starts, in the Motions box. ``how``: ``IDLE`` (it plays while the thing idles: a new motion
    under the Idle motion, listed first so it covers the idle pose) or ``INSTEAD`` (it plays where motion ``instead`` plays: a copy of that motion
    under the same parents, listed first; a motion with no parent, which the game starts by its name, plays the clip itself). Returns one plain
    sentence about what was made and what starts it; raises :class:`MotionError` when this model has no place for it."""
    ensure_loaded(t, scene)
    state = t.state
    if not len(state.rows) and not state.has_list:
        make_standard_list(t, scene)
    table = table_of(state)
    if not len(table.rows):
        raise MO.MotionError("This blueprint has a motion list with no motions in it, so there is nothing to put the clip under. Add a motion by hand.")
    name = MO.unused_name(table, clip) if table.find(clip) >= 0 else clip
    if how == "IDLE":
        idle = table.find("Idle")
        if idle < 0:
            raise MO.MotionError("This blueprint has no Idle motion, so nothing can play the clip while the thing idles. Choose 'Play it instead of' "
                                 "another motion.")
        kids = table.rows[idle].children
        pose = next((k for k in kids if table.rows[k].clip.lower() in ("idle1", "idle")), None)
        if pose is None:
            pose = next((k for k in kids if table.rows[k].clip and not table.rows[k].clip.lower().startswith("proc:")), None)
        if pose is None:
            if not table.rows[idle].clip:
                raise MO.MotionError("The Idle motion has nothing below it that plays a clip: choose 'Play it instead of' another motion.")
            old = table.rows[idle].clip
            table.rows[idle].clip = clip
            msg = f"The Idle motion played '{old}' itself: it plays '{clip}' now."
            n = idle
        else:
            n = table.add_copy(pose, name, True, True, only_under=[idle])             # not under Melee1 and the others that also list the pose
            table.rows[n].clip = clip
            msg = f"Made the motion '{table.rows[n].name}' playing '{clip}', under Idle only."
    else:
        if not 0 <= instead < len(table.rows):
            raise MO.MotionError("Choose the motion the clip should play instead of.")
        if table.parents(instead):
            n = table.add_copy(instead, name, True, True)
            table.rows[n].clip = clip
            msg = (f"Made the motion '{table.rows[n].name}' playing '{clip}', listed before '{table.rows[instead].name}' with its range, so it "
                   f"covers '{table.rows[instead].name}' wherever both play.")
        else:
            old = table.rows[instead].clip
            table.rows[instead].clip = clip
            n = instead
            msg = (f"'{table.rows[instead].name}' has no motion above it (the game starts it by its name), so it plays '{clip}' now instead of "
                   f"'{old}'.")
    fill(state, table)
    select_node(state, n)
    return msg + " " + MC.start_words(table, n)


class BZ_OT_clip_new(bpy.types.Operator):
    """Make a new clip (animation) on the rig: a copy of a clip you choose, under a new name, to change and key in the Action Editor. It can also give the clip a motion so the game plays it"""
    bl_idname = "bz.clip_new"
    bl_label = "New Clip"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Clip name", default="", options={"SKIP_SAVE"},
                         description="The name the game knows the clip by. Keep one of the game's names (Idle, Walk, Run, Melee1 ...) to replace that "
                                     "animation; any other name needs a motion in the Motions box to be played")
    copy_from: EnumProperty(name="Start from", items=_copy_items, description="The clip the new one starts as a copy of (the idle clip is the usual "
                            "start)")
    then: EnumProperty(name="Who plays it", items=[
        ("NONE", "Decide later", "Add a motion for the clip yourself with Add Copy in the Motions box"),
        ("IDLE", "Play it while idle", "A motion under Idle starts the clip whenever the thing idles"),
        ("INSTEAD", "Play it instead of a motion", "A copy of the motion you choose, listed before it, plays the clip where that motion plays")],
        default="NONE")
    instead: EnumProperty(name="Instead of", items=_instead_items, description="The motion the new clip takes the place of")

    @classmethod
    def poll(cls, context):
        t = _context_target(context)
        return t is not None and t.rig is not None

    def invoke(self, context, event):
        t = _context_target(context)
        self.name = "my_clip"
        self.then = "IDLE"
        try:
            ensure_loaded(t, context.scene)                               # the motions are needed to offer 'instead of' and to give the clip a motion
        except Exception:                                                 # noqa: BLE001 -- the dialog still works; the motion part says what is wrong
            pass
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "name")
        col.prop(self, "copy_from")
        col.separator()
        col.label(text="Who plays it in the game?")
        col.prop(self, "then", expand=True)
        if self.then == "INSTEAD":
            col.prop(self, "instead", text="Instead of")
        _hint(col, "A clip starts as a copy with a key on every frame. In the game a clip has one key per frame; the export reads the rig frame by "
                   "frame from the Action's first key to its last, so you can delete keys you do not need and Blender fills in the frames between. "
                   "Keep the first and the last key: take one away and the clip gets shorter, and its events move with the length. A bone with "
                   "no key left at all is held at its rest pose, not at the pose of the copy.")

    def execute(self, context):
        from . import actions as bz
        t = _context_target(context)
        rig = t.rig
        name = self.name.strip()
        if not name or len(name) > 32:
            self.report({"ERROR"}, "Give the clip a name of 1 to 32 characters.")
            return {"CANCELLED"}
        clips, _new, acts = clip_info(t)
        if name in clips or name in bpy.data.actions:
            self.report({"ERROR"}, f"There is already a clip or an Action called '{name}'. Choose another name.")
            return {"CANCELLED"}
        if not any(acts.values()) and not list(bz.actions_of(rig)):
            self.report({"ERROR"}, "The rig has no clip to start from. Borrow Clips... brings clips from another model; "
                                   "then New Clip copies one of them.")
            return {"CANCELLED"}
        if not self.copy_from or self.copy_from not in acts:
            self.report({"ERROR"}, "Choose which clip the new one starts from (the idle clip is the usual start).")
            return {"CANCELLED"}
        src = acts[self.copy_from]
        instead = -1
        if self.then == "INSTEAD":
            try:
                instead = int(self.instead)
            except ValueError:
                self.report({"ERROR"}, "Choose the motion the clip should play instead of.")
                return {"CANCELLED"}
        act = src.copy()
        act.name = name
        for key in (bz.KEY_CLIP, bz.KEY_HASH, bz.KEY_IMPORT_NAME):      # it is a clip of its own: not the source clip's bytes, not its name
            if key in act:
                del act[key]
        act.use_fake_user = True
        act[bz.KEY_ARM] = rig.name
        if rig.get(bz.KEY_ARM_UID):
            act[bz.KEY_ARM_UID] = str(rig[bz.KEY_ARM_UID])
        act[bz.KEY_IMPORT_NAME] = act.name
        bz._assign(rig, act)
        msg = f"Made the clip '{name}' (a copy of '{self.copy_from}') and set it as the rig's active Action. Change its keys in the Action Editor."
        if self.then == "NONE":
            msg += " To play it in the game, give it a motion in the Motions box (Add Copy, on a motion that has a motion above it), then export."
            self.report({"INFO"}, msg)
            return {"FINISHED"}
        try:
            msg += " " + give_motion(t, context.scene, name, self.then, instead)
        except MO.MotionError as e:
            self.report({"WARNING"}, msg + f" It has no motion yet: {ui_kit.plain(str(e))}")
            return {"FINISHED"}
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BZ_PT_motions(bpy.types.Panel):
    bl_label = "Motions"
    bl_idname = "BZ_PT_motions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 20
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _poll_target(context)

    def draw(self, context):
        t = _context_target(context)
        lay = self.layout
        n = len(clip_info(t)[0]) if t is not None else 0
        row = lay.row(align=True)
        row.label(text=f"{n} clips on the rig" if t.rig is not None else "No skeleton, so no clips", icon="ACTION")
        if t.rig is not None:
            row.operator("bz.borrow_clips", text="Borrow Clips...", icon="IMPORT")
            row.operator(BZ_OT_clip_new.bl_idname, icon="ADD")
            hint = lay.column(align=True)
            hint.scale_y = 0.85
            for line in ui_kit.wrap_lines("A new animation: press New Clip (a copy of a clip you choose; it offers to give the clip a motion), change its keys in the "
                                          "Action Editor, then export. An Action that was not made with New Clip or Borrow Clips is exported "
                                          "only while it is the rig's active Action.", 44):
                hint.label(text=line)
        draw_motions(lay, context)


CLASSES = (BZ_PG_motion, BZ_PG_motion_item, BZ_PG_motions, BZ_UL_motions, BZ_OT_motions_load, BZ_OT_motions_start, BZ_OT_motions_pick_clip,
           BZ_OT_motions_add_copy, BZ_OT_motions_delete, BZ_OT_motions_default, BZ_OT_motions_play, BZ_OT_motions_measure,
           BZ_OT_motions_use_speed, BZ_OT_clip_new, BZ_PT_motions)


def register():
    """Point ``Object.bz_motions`` at the state (the classes are registered by the add-on's ``_CLASSES``)."""
    bpy.types.Object.bz_motions = PointerProperty(type=BZ_PG_motions)


def unregister():
    if hasattr(bpy.types.Object, "bz_motions"):
        del bpy.types.Object.bz_motions
