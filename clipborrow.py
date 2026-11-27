"""Borrow Clips From...: give a creature or a scene object the animations of another model, as new Actions on its rig.

A creature borrows from another creature (``newproject.borrow_clips``, the same code the New Project dialog uses); a gatherer, character, structure or
prop borrows from another scene model (:func:`borrow_scene_clips`, below). Both match bones BY NAME and give each shared bone the donor's pose in the
bone's own frame, with its translation scaled by the ratio of the two bones' lengths; a bone the donor has no bone for stays at its bind pose. That is
faithful when the two skeletons share bone names and axes. The report says how many bones matched.

A borrowed clip is a new Action: it is written under its own name, and a clip with a name the game does not know plays only when the Motions box has a
motion for it (a name the game knows, such as Walk, gets its motion from the game). Borrow one clip under a new name with *New name*.
"""
from __future__ import annotations

import os

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, StringProperty
from .sceneobj import ui_kit

from . import actions as bzactions
from . import core, rig, store
from . import enumitems

_ITEMS: list = []                       # enum items must stay referenced while Blender shows them


# ---------------------------------------------------------------------------------------------------------- scene models
def _scene_skeleton(sc):
    """``(names, parents (None for a root), bind_world (SGM 4x4 per bone), rig)`` of a scene model."""
    from ICParse.codecs import pose as ICpose
    r = ICpose.SceneRig(sc.bones)
    return list(r.names), [None if p is None or int(p) < 0 else int(p) for p in r.parents], r.bind_world, r


def scene_clip_world(skel_rig, anim, t: float):
    tracks = {tr.bone_name.lower(): tr for tr in anim.tracks}
    return skel_rig.world(skel_rig.anim_local(tracks, t))


def _local_lengths(bind_world, parents):
    mats = [rig.to_blender_matrix(np.asarray(m, dtype=np.float64)) for m in bind_world]
    out = []
    for i, p in enumerate(parents):
        local = mats[i] if p is None else mats[p].inverted_safe() @ mats[i]
        out.append(float(local.translation.length))
    return out


def _target_names(arm, names):
    """The rig's current name of each bone of the target model, in the model's order (a renamed bone under its new name)."""
    try:
        got = rig.current_bone_names(arm)
        if len(got) == len(names):
            return list(got)
    except Exception:                                                     # noqa: BLE001
        pass
    on = {b.name.lower(): b.name for b in arm.data.bones}
    return [on.get(n.lower(), n) for n in names]


def borrow_scene_clips(arm, target_scene, donor_scene, only=None, as_name=None) -> dict:
    """Give the scene object's rig the donor model's clips as new Actions (and NLA strips), matched by bone name; see the module text. Returns
    ``{clips, matched, unmatched_target, unmatched_donor, skipped}``. ``as_name``: for ONE clip, the name it gets on this rig."""
    from . import newproject
    t_names0, t_par, t_bw, _r = _scene_skeleton(target_scene)
    t_names = _target_names(arm, t_names0)
    d_names, d_par, d_bw, drig = _scene_skeleton(donor_scene)
    d_index = {n.lower(): i for i, n in enumerate(d_names)}
    match = {ti: d_index[n.lower()] for ti, n in enumerate(t_names0) if n.lower() in d_index}
    lt, ld = _local_lengths(t_bw, t_par), _local_lengths(d_bw, d_par)
    ratios = {ti: lt[ti] / ld[di] for ti, di in match.items() if lt[ti] > 1e-6 and ld[di] > 1e-6}
    overall = float(np.median(list(ratios.values()))) if ratios else 1.0
    scale = {ti: float(min(5.0, max(0.2, ratios.get(ti, overall)))) for ti in match}
    have = {str(a.get(bzactions.KEY_CLIP, a.name)).lower() for a in bzactions.actions_of(arm)} | {a.name.lower() for a in bzactions.actions_of(arm)}
    wanted = {s.strip().lower() for s in (only or []) if s.strip()}
    made, skipped = [], []
    for ai, anim in enumerate(donor_scene.animations or []):
        name = anim.name or f"clip{ai}"
        if wanted and name.lower() not in wanted:
            continue
        name = as_name or name
        if name.lower() in have:
            skipped.append(name)
            continue
        K = max(1, int(getattr(anim, "frame_count", 0) or 1))
        frames = [scene_clip_world(drig, anim, f / K) for f in range(K + 1)]
        sd = bzactions.basis_samples(d_names, d_par, d_bw, frames)
        st = np.zeros((K + 1, len(t_names), 10), dtype=np.float64)
        st[:, :, 3] = 1.0
        st[:, :, 7:10] = 1.0
        for ti, di in match.items():
            st[:, ti, :] = sd[:, di, :]
            st[:, ti, 0:3] *= scale[ti]
        act = bzactions.make_action(arm, name, t_names, st, 1, float(getattr(anim, "fps", 30.0) or 30.0), clip_name=None)
        bzactions.stash(arm, act, act.name, 1)
        made.append((name, act.name))
    current = next((list(o.get("bz_anims", [])) for o in bpy.data.objects if o.get("bz_rig") == arm.name), [])
    newproject._set_anims(arm, current + [c for c, _ in made])
    return {"clips": [c for c, _ in made], "matched": len(match), "unmatched_target": len(t_names0) - len(match),
            "unmatched_donor": len(d_names) - len(set(match.values())), "skipped": skipped}


# ---------------------------------------------------------------------------------------------------------- the donors to choose from
def donor_items(kind: str, context):
    """The game's models whose clips can be borrowed: the animals for a creature, the gatherers, characters and structures for a scene object."""
    from . import newproject
    items = []
    if kind == "creature":
        for i, d in enumerate(newproject.creature_donors(context)):
            items.append((d.key, d.label, f"The game's {d.name.replace('_', ' ')} ({d.archive_name})", "", i))
    else:
        i = 0
        for k in ("CHARACTER", "STRUCTURE"):
            for ap, member in newproject.scene_donors(k, context):
                items.append((f"{ap}|{member}", newproject._label(member), member, "", i))
                i += 1
    return items


def read_donor(kind: str, key: str, filepath: str, context):
    """The donor model: from a file on disk when ``filepath`` is given, else the game model ``key``. Raises ValueError with a plain reason."""
    from . import newproject
    if filepath:
        path = bpy.path.abspath(filepath)
        if not os.path.isfile(path):
            raise ValueError("That file does not exist.")
        model = core.read_source(core.resolve_source(path))
    elif key in ("", "NONE"):
        raise ValueError("Choose a model to borrow from, or a file.")
    elif kind == "creature":
        from ICParse import read_sgm
        from ICParse.sgm.creature import stock_donor
        d = next((x for x in newproject.creature_donors(context) if x.key == key), None)
        if d is None:
            raise ValueError("Choose a model to borrow from.")
        model = read_sgm(stock_donor.read_files(d).sgm)
    else:
        if "|" not in key:
            raise ValueError("Choose a model to borrow from.")
        ap, member = key.rsplit("|", 1)
        from ICParse.sgm.scene import model as SM
        sgm, _ebp = newproject.read_donor(ap, member)
        model = SM.read_scene(sgm)
    if (kind == "scene") != core.is_scene_model(model):
        raise ValueError("That model is " + ("a creature, but this is a gatherer, character, structure or prop: borrow from one of those."
                                              if kind == "scene" else "not a creature: borrow from a creature."))
    if not len(model.animations or []):
        raise ValueError("That model has no animations to borrow.")
    return model


# ---------------------------------------------------------------------------------------------------------- how well a model fits
_BONES: dict = {}                       # donor key -> the lower-case bone names of the model (read once: a list of a hundred models is read once)


def donor_bone_names(kind: str, key: str, context) -> set:
    """The lower-case bone names of the game model ``key`` (empty when it cannot be read)."""
    from . import newproject
    hit = _BONES.get((kind, key))
    if hit is None:
        try:
            m = read_donor(kind, key, "", context)
            names = newproject._skeleton(m)[0] if kind == "creature" else _scene_skeleton(m)[0]
            hit = {n.lower() for n in names}
        except Exception:                                                 # noqa: BLE001 -- a model that cannot be read matches nothing
            hit = set()
        _BONES[(kind, key)] = hit
    return hit


def match_counts(kind: str, rig_bone_names, keys, context) -> dict:
    """``{donor key: how many of the rig's bones the model has a bone of the same name for}``. Clips move the bones that match, and only those."""
    mine = {n.lower() for n in rig_bone_names}
    return {k: len(mine & donor_bone_names(kind, k, context)) for k in keys}


def ordered_by_match(items, counts: dict, total: int) -> list:
    """The donor items, best fit first (then by name), each label saying how many bones match."""
    ranked = sorted(items, key=lambda it: (-counts.get(it[0], 0), it[1].lower()))
    return [(k, f"{label}  -  {counts[k]} of {total} bones match" if k in counts else label, desc, icon, n)
            for k, label, desc, icon, n in ranked]


def match_words(matched: int, total: int) -> str:
    if total <= 0:
        return ""
    if matched >= 0.9 * total:
        return f"{matched} of your {total} bones match this model's by name: its clips will fit."
    if matched >= 0.6 * total:
        return f"{matched} of your {total} bones match this model's by name: its clips will fit most of the body."
    return f"Only {matched} of your {total} bones match this model's by name: its clips will move little. Choose a model that matches more."


# ---------------------------------------------------------------------------------------------------------- the operators
def _target_rig_names(context):
    from . import motionspanel
    t = motionspanel._context_target(context)
    return [b.name for b in t.rig.data.bones] if t is not None and t.rig is not None else []


@enumitems.stable
def _items(self, context):
    from . import motionspanel
    t = motionspanel._context_target(context)
    kind = t.kind if t is not None else "creature"
    got = donor_items(kind, context)
    names = _target_rig_names(context)
    counts = {k: len({n.lower() for n in names} & _BONES[(kind, k)]) for k, *_r in got if (kind, k) in _BONES}      # only the models already read (invoke reads them)
    ranked = ordered_by_match(got, counts, len(names)) if counts else got
    _ITEMS[:] = [("NONE", "(choose one)", "Pick a model of the game, or a file below", "", 0)] + [
        (k, label, desc, icon, i + 1) for i, (k, label, desc, icon, _n) in enumerate(ranked)]     # numbers must not repeat
    return _ITEMS


def do_borrow(op, context, donor_key: str, filepath: str, only: list, new_name: str):
    """Borrow the clips ``only`` (empty: every clip the rig lacks) of the model ``donor_key`` / ``filepath`` and report, as both operators do."""
    from . import motionspanel, newproject
    t = motionspanel._context_target(context)
    if t is None or t.rig is None:
        op.report({"ERROR"}, "Select a creature or an object with a skeleton.")
        return {"CANCELLED"}
    try:
        donor = read_donor(t.kind, donor_key, filepath, context)
    except Exception as e:                                            # noqa: BLE001
        op.report({"ERROR"}, f"Cannot read the model to borrow from: {e}")
        return {"CANCELLED"}
    new_name = new_name.strip()
    if new_name:
        names = [a.name.lower() for a in bzactions.actions_of(t.rig)] + [str(a.get(bzactions.KEY_CLIP, "")).lower() for a in bzactions.actions_of(t.rig)]
        if new_name.lower() in names:
            op.report({"ERROR"}, f"This rig already has a clip called '{new_name}'. Choose another name.")
            return {"CANCELLED"}
        how_many = len(only) if only else len(donor.animations)
        if how_many != 1:
            op.report({"ERROR"}, "A new name works for one clip: tick that clip only.")
            return {"CANCELLED"}
    try:
        if t.kind == "creature":
            target = core.read_source(store.source_path(t.holder))
            rep = newproject.borrow_clips(t.rig, target, donor, only, new_name or None)
        else:
            rep = borrow_scene_clips(t.rig, core.read_source(t.ent.source), donor, only, new_name or None)
    except ValueError as e:                                           # a reason in plain words (read_source and the like)
        op.report({"ERROR"}, f"Borrowing failed: {e}")
        return {"CANCELLED"}
    except Exception as e:                                            # noqa: BLE001
        import traceback
        traceback.print_exc()                                         # the details are in Blender's system console
        op.report({"ERROR"}, f"Borrowing failed ({type(e).__name__}). Press Ctrl+Z to undo what it did; the details are in Blender's system console.")
        return {"CANCELLED"}
    msg = (f"Borrowed {len(rep['clips'])} clips ({rep['matched']} bones matched by name, {rep['unmatched_target']} of yours not in that model)"
           + (f"; already there: {', '.join(rep['skipped'])}" if rep["skipped"] else "") + ".")
    if rep["clips"] and rep["matched"] < 0.6 * max(1, rep["matched"] + rep["unmatched_target"]):
        op.report({"WARNING"}, "Few bones share names with that model: its clips will move little.")
    if rep["clips"]:
        msg += " A clip with a name the game does not know needs a motion: use New Clip's 'Play it while idle', or Add Copy in the Motions box."
    op.report({"INFO"}, msg)
    return {"FINISHED"}


class BZ_PG_borrow_clip(bpy.types.PropertyGroup):
    name: StringProperty()
    use: BoolProperty(name="Borrow", default=False)
    have: BoolProperty(description="The rig has a clip of this name already")


class BZ_OT_borrow_clips(bpy.types.Operator):
    """Copy animations from another model onto this rig as new clips"""
    bl_idname = "bz.borrow_clips"
    bl_label = "Borrow Clips From..."
    bl_options = {"REGISTER", "UNDO"}

    donor: EnumProperty(name="Borrow from", items=_items, description="A model of the game whose animations to copy; the models whose bones fit "
                        "this rig best are first")
    filepath: StringProperty(name="Or a file", subtype="FILE_PATH", default="",
                             description="A .sgm model on disk to copy animations from (used instead of the list when set)")
    only: StringProperty(name="Only these clips", default="", options={"HIDDEN"},
                         description="Clip names to copy, separated by commas. Empty copies every clip the rig lacks")
    new_name: StringProperty(name="New name", default="", options={"HIDDEN"}, description="Name for the copied clip (one clip only). A name the game knows, like Walk, "
                             "uses the game's motion; any other name needs a motion in the Motions box")
    ask: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"}, description="Open the list of clips after this step (the dialog sets it)")

    @classmethod
    def poll(cls, context):
        from . import motionspanel
        t = motionspanel._context_target(context)
        return t is not None and t.rig is not None

    def invoke(self, context, event):
        from . import motionspanel
        t = motionspanel._context_target(context)
        if t is not None:
            for k, *_r in donor_items(t.kind, context):               # read the models once: the list is sorted by how many bones fit
                donor_bone_names(t.kind, k, context)
        self.ask = True
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Choose Clips...")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "donor")
        col.prop(self, "filepath")
        names = _target_rig_names(context)
        if self.donor not in ("", "NONE") and not self.filepath:
            from . import motionspanel
            t = motionspanel._context_target(context)
            if t is not None and (t.kind, self.donor) in _BONES:
                got = len({n.lower() for n in names} & _BONES[(t.kind, self.donor)])
                words = match_words(got, len(names))
                for line in ui_kit.wrap_lines(words, 52):
                    col.label(text=line, icon="CHECKMARK" if got >= 0.6 * len(names) else "ERROR")
        for line in ui_kit.wrap_lines("The next step lists the model's clips: tick the ones you need. Only those are borrowed, so no clip is left over "
                                      "that nothing plays.", 52):
            col.label(text=line)

    def execute(self, context):
        if self.ask:
            return bpy.ops.bz.borrow_clips_pick("INVOKE_DEFAULT", donor=self.donor, filepath=self.filepath)
        only = [s for s in self.only.split(",") if s.strip()]
        return do_borrow(self, context, self.donor, self.filepath, only, self.new_name)


class BZ_OT_borrow_clips_pick(bpy.types.Operator):
    """Tick the clips to borrow from the chosen model"""
    bl_idname = "bz.borrow_clips_pick"
    bl_label = "Borrow Clips"
    bl_options = {"REGISTER", "UNDO"}

    donor: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    filepath: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    new_name: StringProperty(name="New name", default="", options={"SKIP_SAVE"},
                             description="Name for the copied clip (one clip only). A name the game knows, like Walk, "
                                         "uses the game's motion; any other name needs a motion in the Motions box")
    clips: bpy.props.CollectionProperty(type=BZ_PG_borrow_clip, options={"HIDDEN", "SKIP_SAVE"})
    words: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        from . import motionspanel
        t = motionspanel._context_target(context)
        return t is not None and t.rig is not None

    def invoke(self, context, event):
        from . import motionspanel
        t = motionspanel._context_target(context)
        try:
            model = read_donor(t.kind, self.donor, self.filepath, context)
        except Exception as e:                                            # noqa: BLE001
            self.report({"ERROR"}, f"Cannot read the model to borrow from: {e}")
            return {"CANCELLED"}
        have = {str(a.get(bzactions.KEY_CLIP, a.name)).lower() for a in bzactions.actions_of(t.rig)} | {a.name.lower() for a in bzactions.actions_of(t.rig)}
        self.clips.clear()
        for i, an in enumerate(model.animations or []):
            c = self.clips.add()
            c.name = an.name or f"clip{i}"
            c.have = c.name.lower() in have
        names = [b.name for b in t.rig.data.bones]
        mine = {n.lower() for n in names}
        theirs = (newproject_names(model) if t.kind == "creature" else _scene_skeleton(model)[0])
        self.words = match_words(len(mine & {n.lower() for n in theirs}), len(names))
        return context.window_manager.invoke_props_dialog(self, width=460, confirm_text="Borrow the Ticked Clips")

    def draw(self, context):
        col = self.layout.column()
        for line in ui_kit.wrap_lines(self.words, 52):
            col.label(text=line)
        col.label(text="Tick the clips to borrow:")
        flow = col.column(align=True)
        for c in self.clips:
            if c.have:
                flow.label(text=f"{c.name}  (the rig has it)", icon="CHECKMARK")
            else:
                flow.prop(c, "use", text=c.name)
        col.prop(self, "new_name")

    def execute(self, context):
        only = [c.name for c in self.clips if c.use and not c.have]
        if not only:
            self.report({"ERROR"}, "Tick at least one clip to borrow.")
            return {"CANCELLED"}
        return do_borrow(self, context, self.donor, self.filepath, only, self.new_name)


def newproject_names(model) -> list:
    from . import newproject
    return newproject._skeleton(model)[0]


CLASSES = (BZ_PG_borrow_clip, BZ_OT_borrow_clips, BZ_OT_borrow_clips_pick)
