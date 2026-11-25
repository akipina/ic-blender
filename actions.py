"""Clips as native Blender Actions.

Every clip of an imported creature (or scene object) becomes a named Action on its armature --
baked in bulk into fcurves (location / quaternion / scale per bone, one key per clip frame, linear
interpolation like the engine) and stashed on a muted NLA track so it shows in the Action Editor,
the Dope Sheet and the NLA. Load Animation just makes one of them active. Export writes the
armature's Actions back as clips through ICParse's from-scratch clip builder: an Action whose
keys still match its import-time snapshot keeps the source clip's bytes (untouched creatures stay
byte-identical), an edited or renamed Action is re-encoded, a new Action is appended, and a clip
whose Action was deleted is dropped.

Frame convention: clip ``frame_count`` is the number of INTERVALS (keys at ``frame / frame_count``,
last key at 1.0), so a clip occupies Blender frames 1 .. frame_count + 1 and every key lands on a
frame. A user-authored Action is sampled on its own frame range the same way.
"""
from __future__ import annotations

import re
import zlib

import numpy as np

import bpy
from mathutils import Matrix

from . import rig

KEY_CLIP = "bz_clip"          # on an Action: the clip it came from
KEY_HASH = "bz_hash"          # on an Action: content hash at import (str; crc32 exceeds Blender's int prop)
KEY_FPS = "bz_fps"            # on an Action: the clip's frame rate
KEY_ARM = "bz_arm"            # on an Action: the armature object it animates
KEY_CLIPS = "bz_clip_actions"  # on the armature: [clip name, action name] pairs at import
KEY_ARM_UID = "bz_uid"         # on the armature: its identity; on an Action: the armature it was made for
KEY_IMPORT_NAME = "bz_import_name"  # on an Action: its own name when made (a later difference is a rename)

_CHANNELS = (("location", 3, 0), ("rotation_quaternion", 4, 3), ("scale", 3, 7))


# --------------------------------------------------------------------------- baking
def basis_samples(bone_names, parents, bind_world_sgm, frames_world_sgm):
    """(F, B, 10) per-frame, per-bone matrix_basis decomposed as loc(3) quat(4) scale(3), the same
    order-independent math as rig.bake_world_frames (root: R^-1 A; child: R^-1 R_p A_p^-1 A), with
    quaternion signs kept continuous frame to frame (Blender interpolates the raw components)."""
    Rm = [rig.to_blender_matrix(np.asarray(m, dtype=np.float64)) for m in bind_world_sgm]
    Rinv = [m.inverted_safe() for m in Rm]
    nb = len(bone_names)
    out = np.empty((len(frames_world_sgm), nb, 10), dtype=np.float64)
    for f, Wsgm in enumerate(frames_world_sgm):
        A = [rig.to_blender_matrix(np.asarray(Wsgm[i], dtype=np.float64)) for i in range(nb)]
        Ainv = {}
        for i in range(nb):
            p = None if parents[i] is None else int(parents[i])
            if p is None or not (0 <= p < nb):
                basis = Rinv[i] @ A[i]
            else:
                if p not in Ainv:
                    Ainv[p] = A[p].inverted_safe()
                basis = Rinv[i] @ Rm[p] @ Ainv[p] @ A[i]
            loc, quat, scale = basis.decompose()
            q = np.array(quat[:], dtype=np.float64)
            if f > 0 and float(np.dot(q, out[f - 1, i, 3:7])) < 0.0:
                q = -q
            out[f, i, 0:3] = loc[:]; out[f, i, 3:7] = q; out[f, i, 7:10] = scale[:]
    return out


def _fcurve(action, arm_obj, data_path, index, group):
    """An fcurve on ``action`` for the armature's ``data_path[index]`` (Blender 4.4+ slotted actions
    or the legacy API)."""
    try:
        return action.fcurve_ensure_for_datablock(arm_obj, data_path, index=index)
    except (AttributeError, TypeError):
        fc = action.fcurves.find(data_path, index=index)
        return fc or action.fcurves.new(data_path, index=index, action_group=group)


def _assign(arm_obj, action):
    """Make ``action`` the armature's active action (with its slot on slotted-action Blenders)."""
    ad = arm_obj.animation_data or arm_obj.animation_data_create()
    ad.action = action
    try:
        if action is not None and hasattr(ad, "action_slot") and len(action.slots):
            ad.action_slot = action.slots[0]
    except Exception:
        pass
    return ad


def make_action(arm_obj, name, bone_names, samples, frame_start=1, fps=None, clip_name=None):
    """A new Action holding ``samples`` ((F, B, 10) from :func:`basis_samples`) as fcurves with one
    key per frame from ``frame_start``, linear interpolation. Tagged for export."""
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    ad = arm_obj.animation_data or arm_obj.animation_data_create()
    prev, prev_slot = ad.action, getattr(ad, "action_slot", None)
    _assign(arm_obj, act)                          # creating fcurves needs the slot to exist
    F = samples.shape[0]
    frames = np.arange(F, dtype=np.float64) + float(frame_start)
    co = np.empty(2 * F, dtype=np.float64)
    co[0::2] = frames
    interp = np.full(F, 1, dtype=np.int32)         # 1 = LINEAR
    for i, bname in enumerate(bone_names):
        for path, cnt, off in _CHANNELS:
            for k in range(cnt):
                fc = _fcurve(act, arm_obj, f'pose.bones["{bname}"].{path}', k, bname)
                fc.keyframe_points.add(F)
                co[1::2] = samples[:, i, off + k]
                fc.keyframe_points.foreach_set("co", co)
                try:
                    fc.keyframe_points.foreach_set("interpolation", interp)
                except Exception:
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
                fc.update()
    ad.action = prev
    try:
        if prev is not None and prev_slot is not None:
            ad.action_slot = prev_slot
    except Exception:
        pass
    act[KEY_ARM] = arm_obj.name
    if arm_obj.get(KEY_ARM_UID):
        act[KEY_ARM_UID] = str(arm_obj[KEY_ARM_UID])
    act[KEY_IMPORT_NAME] = act.name               # the name Blender gave it (.001 on a collision)
    if fps:
        act[KEY_FPS] = float(fps)
    if clip_name is not None:
        act[KEY_CLIP] = str(clip_name)
    act[KEY_HASH] = action_hash(act, rig.rename_canon(arm_obj) if clip_name is not None else None)
    return act


def stash(arm_obj, action, name=None, frame_start=1):
    """Put ``action`` on its own muted NLA track (visible/selectable in the NLA editor)."""
    ad = arm_obj.animation_data or arm_obj.animation_data_create()
    trk = ad.nla_tracks.new()
    trk.name = name or action.name
    strip = trk.strips.new(action.name, int(frame_start), action)
    try:
        if hasattr(strip, "action_slot") and len(action.slots):
            strip.action_slot = action.slots[0]
    except Exception:
        pass
    trk.mute = True
    return trk


def import_clips(arm_obj, bone_names, parents, bind_world_sgm, clips, world_at, frame_start=1):
    """Bake every clip into an Action + NLA track. ``clips`` yields (name, frame_count, fps);
    ``world_at(clip_index, t)`` gives per-bone SGM world matrices. Returns the Actions."""
    have = [a.name for a in actions_of(arm_obj) if a.get(KEY_CLIP) is not None]
    if have:
        raise RuntimeError(f"rig {arm_obj.name!r} already has its clips as Actions ({', '.join(have[:4])}"
                           f"{', ...' if len(have) > 4 else ''}): baking them again would give two Actions per clip")
    made = []
    pairs = []
    for ci, (name, frame_count, fps) in enumerate(clips):
        K = max(1, int(frame_count))
        frames = [world_at(ci, f / K) for f in range(K + 1)]
        samples = basis_samples(bone_names, parents, bind_world_sgm, frames)
        act = make_action(arm_obj, name or f"clip{ci}", bone_names, samples, frame_start, fps, clip_name=name)
        stash(arm_obj, act, act.name, frame_start)
        made.append(act)
        pairs.append([str(name), act.name])
    arm_obj[KEY_CLIPS] = pairs
    return made


def import_creature_clips(arm_obj, model):
    from ICParse.codecs import reconstruct as R
    bones = model.bones
    parents = [None if b.parent is None else int(b.parent) for b in bones]
    bw = R.bind_world_matrices(model)
    clips = [(a.name, getattr(a, "frame_count", 0) or 11, getattr(a, "fps", 30.0)) for a in model.animations]
    return import_clips(arm_obj, rig.current_bone_names(arm_obj, model), parents, bw, clips,
                        lambda ci, t: R.animated_bone_world(model, model.animations[ci], t))


def import_scene_clips(arm_obj, data):
    from . import core
    clips = [(a.name, getattr(a, "frame_count", 0) or 1, getattr(a, "fps", 30.0)) for a in data.animations]
    return import_clips(arm_obj, data.bone_names, data.bone_parents, data.bind_world, clips,
                        lambda ci, t: core.scene_bone_world(data, ci, t))


# --------------------------------------------------------------------------- bookkeeping
def _fcurves(action):
    """Every fcurve of an Action (all slots on slotted-action Blenders, else the legacy list)."""
    out = []
    try:
        for layer in action.layers:
            for strip in layer.strips:
                for slot in action.slots:
                    bag = strip.channelbag(slot)
                    if bag is not None:
                        out.extend(bag.fcurves)
        if out:
            return out
    except Exception:
        pass
    return list(action.fcurves)


_BONE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\](.*)$', re.S)


def bone_of_path(data_path):
    """``(bone name, rest of the path)`` of a ``pose.bones["name"]...`` F-curve path (Blender's escaping undone), or
    None for any other path."""
    hit = _BONE_PATH.match(str(data_path))
    if not hit:
        return None
    return re.sub(r"\\(.)", r"\1", hit.group(1)), hit.group(2)


def bone_path(bone, rest) -> str:
    """The F-curve path of ``bone`` + ``rest`` (Blender's escaping of quotes and backslashes)."""
    return 'pose.bones["' + str(bone).replace("\\", "\\\\").replace('"', '\\"') + '"]' + str(rest)


def canonical_path(data_path, canon) -> str:
    """``data_path`` with its bone read back to the name it had at import (``canon``: {current name: import name},
    :func:`rig.rename_canon`); unchanged for other paths and bones."""
    if not canon:
        return str(data_path)
    got = bone_of_path(data_path)
    if got is None or got[0] not in canon:
        return str(data_path)
    return bone_path(canon[got[0]], got[1])


def action_hash(action, canon=None) -> str:
    """crc32 of every fcurve's (path, index, keys) -- detects edits regardless of how they were made. ``canon``
    ({current bone name: import name}) reads renamed bones' paths back to their import names first, so a bone rename
    alone -- whether Blender rewrote the Action's paths or not (an Action on no NLA strip keeps the old ones) -- hashes
    like the import. With nothing renamed it is the import hash itself."""
    h = 0
    curves = [(canonical_path(fc.data_path, canon), fc) for fc in _fcurves(action)]
    for path, fc in sorted(curves, key=lambda c: (c[0], c[1].array_index)):
        n = len(fc.keyframe_points)
        co = np.empty(2 * n, dtype=np.float64)
        fc.keyframe_points.foreach_get("co", co)
        h = zlib.crc32(f"{path}[{fc.array_index}]".encode() + co.astype(np.float32).tobytes(), h)
    return str(h & 0xFFFFFFFF)


def stale_bone_paths(action, renames):
    """``[(fcurve, current path)]``: the Action's F-curves that still name a renamed bone by its import name
    (``renames``: {import name: current name}) -- Blender renames the paths of the Actions an object uses (active, NLA
    strips), not of an Action kept only by its fake user."""
    out = []
    if not renames:
        return out
    for fc in _fcurves(action):
        got = bone_of_path(fc.data_path)
        if got is not None and got[0] in renames:
            out.append((fc, bone_path(renames[got[0]], got[1])))
    return out


def stale_path_clash(action, renames):
    """Why the Action's stale bone paths (:func:`stale_bone_paths`) can't be pointed at the new names -- it already keys
    the same channel under a new name --, or None."""
    stale = stale_bone_paths(action, renames)
    if not stale:
        return None
    have = {(fc.data_path, fc.array_index) for fc in _fcurves(action)}
    clash = [p for fc, p in stale if (p, fc.array_index) in have]
    seen = set()
    for fc, p in stale:                             # two stale names pointed at one new name
        if (p, fc.array_index) in seen:
            clash.append(p)
        seen.add((p, fc.array_index))
    if clash:
        return (f"Action {action.name} keys {clash[0]} under both a renamed bone's old name and its new name: delete "
                "one of the two F-curves")
    return None


def fix_stale_bone_paths(action, renames) -> int:
    """Rewrite the Action's F-curve paths that name a renamed bone by its import name (:func:`stale_bone_paths`) to the
    bone's current name. Raises ValueError, changing nothing, when the Action already keys the same channel under the
    new name. Returns how many paths were rewritten."""
    stale = stale_bone_paths(action, renames)
    if not stale:
        return 0
    why = stale_path_clash(action, renames)
    if why:
        raise ValueError(why)
    for fc, p in stale:
        fc.data_path = p
    return len(stale)


def unknown_bone_keys(action, rig_names, renames):
    """The bone names ``action``'s F-curves key that are no bone of the rig -- neither a current name (``rig_names``)
    nor the old name of a renamed bone the export reads back (``renames``: {old name: current name},
    :func:`rig.rename_canon`) --, sorted. Blender plays such keys on no bone, and the clip is written without them."""
    out = set()
    for fc in _fcurves(action):
        got = bone_of_path(fc.data_path)
        if got is not None and got[0] not in rig_names and got[0] not in renames:
            out.add(got[0])
    return sorted(out)


def frame_range(action):
    """(start, end) integer frames the Action spans."""
    fr = action.frame_range
    return int(round(fr[0])), int(round(fr[1]))


def actions_of(arm_obj):
    """The Actions that belong to this armature, by identity rather than by name: its active Action, the Actions
    on its NLA tracks, and Actions stamped with its ``bz_uid``. An Action tagged with the armature's NAME only
    (a legacy or hand-made tag, no uid) counts too; one stamped for another armature (an earlier import of the same
    creature, a deleted rig that had this name) never does -- see :func:`foreign_actions`."""
    seen, out = set(), []
    ad = arm_obj.animation_data
    me = str(arm_obj.get(KEY_ARM_UID, "") or "")
    cands = []
    if ad is not None:
        if ad.action is not None:
            cands.append(ad.action)
        for trk in ad.nla_tracks:
            for strip in trk.strips:
                if strip.action is not None:
                    cands.append(strip.action)
    for act in bpy.data.actions:
        au = str(act.get(KEY_ARM_UID, "") or "")
        if me and au == me:
            cands.append(act)
        elif not au and act.get(KEY_ARM) == arm_obj.name:
            cands.append(act)
    for act in cands:
        au = str(act.get(KEY_ARM_UID, "") or "")
        if me and au and au != me:
            continue                                 # made for another armature, even if assigned here
        if act.as_pointer() not in seen:
            seen.add(act.as_pointer()); out.append(act)
    return out


def unclaimed_actions(arm_obj, acts=None, renames=None):
    """Actions that key only bones of this armature but that no rule makes its own (:func:`actions_of`): not its active
    Action, on none of its NLA tracks, stamped or tagged for no armature, and used by no object (held only by a fake
    user, or by nothing). The export does not write them; :func:`plan_export` says so. ``acts``: :func:`actions_of`
    (computed when None); ``renames``: {old name: current name} of renamed bones whose old names still count."""
    mine = {a.as_pointer() for a in (actions_of(arm_obj) if acts is None else acts)}
    bones = {pb.name for pb in arm_obj.pose.bones} | set(renames or {})
    out = []
    for act in bpy.data.actions:
        if (act.as_pointer() in mine or act.get(KEY_ARM_UID) or act.get(KEY_ARM)
                or act.users - int(bool(act.use_fake_user)) > 0):
            continue
        keyed = {got[0] for got in (bone_of_path(fc.data_path) for fc in _fcurves(act)) if got is not None}
        if keyed and keyed <= bones:
            out.append(act)
    return out


def foreign_actions(arm_obj):
    """Actions tagged with this armature's name but stamped for another armature (ignored by the export)."""
    me = str(arm_obj.get(KEY_ARM_UID, "") or "")
    return [a for a in bpy.data.actions if a.get(KEY_ARM) == arm_obj.name
            and str(a.get(KEY_ARM_UID, "") or "") not in ("", me)]


def _isolate(arm_obj):
    """Mute the armature's NLA and make the active Action play alone (full influence, replace); returns a restore
    closure. What is sampled is then exactly the Action, which is what a clip is."""
    ad = arm_obj.animation_data
    if ad is None:
        return lambda: None
    saved = [(t, t.mute) for t in ad.nla_tracks]
    saved_ad = {k: getattr(ad, k) for k in ("action_influence", "action_blend_type", "action_extrapolation")
                if hasattr(ad, k)}
    for t, _m in saved:
        t.mute = True
    for k, v in (("action_influence", 1.0), ("action_blend_type", "REPLACE"), ("action_extrapolation", "HOLD")):
        if k in saved_ad:
            setattr(ad, k, v)

    def restore():
        for t, m in saved:
            t.mute = m
        for k, v in saved_ad.items():
            setattr(ad, k, v)
    return restore


_POSE_REST = (("location", (0.0, 0.0, 0.0)), ("rotation_quaternion", (1.0, 0.0, 0.0, 0.0)),
              ("rotation_euler", (0.0, 0.0, 0.0)), ("rotation_axis_angle", (0.0, 0.0, 1.0, 0.0)), ("scale", (1.0, 1.0, 1.0)))


def keyed_channels(action):
    """``{(bone name, channel, index)}`` of the pose-bone transform channels ``action`` has an unmuted F-curve for."""
    out = set()
    for fc in _fcurves(action):
        got = bone_of_path(fc.data_path) if not fc.mute else None
        if got is not None and got[1].startswith("."):
            out.add((got[0], got[1][1:], int(fc.array_index)))
    return out


def _rest_unkeyed(arm_obj, action):
    """Put every pose-bone transform channel ``action`` does not key at rest (Blender leaves such a channel as it was, so
    a pose left by an Action played before, or a hand pose, would be sampled into the clip); returns a closure that puts
    the pose the rig had back."""
    keyed = keyed_channels(action)
    saved = []
    for pb in arm_obj.pose.bones:
        for prop, rest in _POSE_REST:
            was = tuple(getattr(pb, prop))
            saved.append((pb.name, prop, was))
            now = tuple(was[k] if (pb.name, prop, k) in keyed else rest[k] for k in range(len(rest)))
            if now != was:
                setattr(pb, prop, now)

    def restore():
        pbs = arm_obj.pose.bones
        for name, prop, was in saved:
            pb = pbs.get(name)
            if pb is not None and tuple(getattr(pb, prop)) != was:
                setattr(pb, prop, was)
    return restore


def sample_world(arm_obj, action, bone_names, start, end):
    """Per-frame, per-bone SGM world matrices of the armature under ``action`` (played alone) on frames start..end,
    evaluated by Blender itself. A transform channel the Action does not key is sampled at rest (identity: the bone rests
    at its bind-local transform, as the engine holds a bone a clip has no track for), whatever pose an earlier playback
    or the modder left on it; the rig's pose is put back afterwards. A bone that is not on the rig raises KeyError (never
    an identity stand-in)."""
    pbs = arm_obj.pose.bones
    missing = [bn for bn in bone_names if pbs.get(bn) is None]
    if missing:
        raise KeyError(f"bone {missing[0]!r} is not on rig {arm_obj.name!r}"
                       + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""))
    ad = arm_obj.animation_data or arm_obj.animation_data_create()
    prev, prev_slot = ad.action, getattr(ad, "action_slot", None)
    scene = bpy.context.scene
    saved = scene.frame_current
    restore = _isolate(arm_obj)
    _assign(arm_obj, action)
    unpose = None
    W = []
    try:
        unpose = _rest_unkeyed(arm_obj, action)
        for f in range(int(start), int(end) + 1):
            scene.frame_set(f)
            pbs = arm_obj.pose.bones
            frame = np.zeros((len(bone_names), 4, 4))
            for i, bn in enumerate(bone_names):
                frame[i] = rig.from_blender_matrix(pbs[bn].matrix)
            W.append(frame)
    finally:
        ad.action = prev
        try:
            if prev is not None and prev_slot is not None:
                ad.action_slot = prev_slot
        except Exception:
            pass
        restore()
        if unpose is not None:
            unpose()                                # before the frame change: the Actions in use pose their channels again
        scene.frame_set(saved)
    return W


class ClipPlan(tuple):
    """``(kept clip names, [(Action, clip name to write)], clip names to drop)`` plus ``warnings``."""
    def __new__(cls, kept, encode, drop, warnings=()):
        self = super().__new__(cls, (kept, encode, drop))
        self.kept, self.encode, self.drop, self.warnings = kept, encode, drop, list(warnings)
        return self


def _evaluation_edits(action):
    """Why Blender plays ``action`` differently from its keys (F-curve modifiers, muted curves), or []."""
    out = []
    for fc in _fcurves(action):
        mods = [m for m in fc.modifiers if not m.mute]           # ``active`` only means "settings shown"
        if mods:
            out.append(f"{fc.data_path}[{fc.array_index}] has {', '.join(m.type for m in mods)} modifier(s)")
        if fc.mute:
            out.append(f"{fc.data_path}[{fc.array_index}] is muted")
    return out


def _rig_evaluation_edits(arm_obj):
    """Why the rig poses differently from its Actions' keys under any Action (constraints, drivers), or []."""
    out = []
    for pb in arm_obj.pose.bones:
        cons = [c for c in pb.constraints if c.enabled and c.influence > 0.0]
        if cons:
            out.append(f"bone {pb.name} has {', '.join(c.type for c in cons)} constraint(s)")
    ad = arm_obj.animation_data
    if ad is not None and len(ad.drivers):
        out.append(f"{len(ad.drivers)} driver(s)")
    dad = getattr(arm_obj.data, "animation_data", None)
    if dad is not None and len(dad.drivers):
        out.append(f"{len(dad.drivers)} driver(s) on the armature data")
    return out


def plan_export(arm_obj, renames=True, deleted=()):
    """What the armature's Actions mean for the file's clips (a :class:`ClipPlan`).

    An Action keeps its source clip's bytes when its keys still hash like at import, nothing changes how Blender
    evaluates it (F-curve modifiers, muted curves, rig constraints or drivers) and it was not renamed. A rename is
    the Action's name differing from the name it was created with (``bz_import_name``), so an Action Blender named
    ``fly.001`` on import still writes clip ``fly``. Edited Actions are re-encoded under their clip name, renamed
    and new ones under the Action's name (the old clip is dropped), and a source clip with no Action any more is
    dropped. Two Actions writing one clip name raise ValueError. ``renames``: a creature's bone renames export (the
    hash reads renamed bones' paths back to their import names, :func:`action_hash`); a scene object's do not. Keys of a
    bone the rig has no bone called (:func:`unknown_bone_keys`) are a warning: the clip is written without them. An
    Action keying only this rig's bones that is none of its own (:func:`unclaimed_actions`: a new Action held only by
    its fake user) is a warning too: it is not written, and which Actions export does not change. ``deleted``: names of
    source bones deleted on the rig (C8): their keys are dropped knowingly, said once for all clips."""
    me = str(arm_obj.get(KEY_ARM_UID, "") or "")
    if me:
        import bpy as _bpy
        twins = [o.name for o in _bpy.data.objects if o is not arm_obj and o.type == "ARMATURE"
                 and str(o.get(KEY_ARM_UID, "") or "") == me]
        if twins:
            raise ValueError(f"rig {arm_obj.name!r} shares its identity with {', '.join(twins)} (a duplicated rig): "
                             "which Actions belong to which is ambiguous. Delete the duplicate, or re-import")
    pairs = {str(c): str(a) for c, a in (arm_obj.get(KEY_CLIPS, []) or [])}
    acts = actions_of(arm_obj)
    warnings = []
    rig_edits = _rig_evaluation_edits(arm_obj)
    if rig_edits:
        warnings.append("the rig poses differently from the Action keys (" + "; ".join(rig_edits[:3])
                        + "): every Action is re-encoded as Blender plays it")
    ad = arm_obj.animation_data
    if ad is not None:
        live = [t.name for t in ad.nla_tracks if not t.mute]
        if live:
            warnings.append(f"NLA track(s) {', '.join(live[:3])} are not muted: Export writes each Action alone "
                            "(a clip is one Action, not an NLA blend)")
    foreign = foreign_actions(arm_obj)
    if foreign:
        warnings.append(f"{len(foreign)} Action(s) tagged for '{arm_obj.name}' belong to another (earlier) rig and "
                        f"are not exported: {', '.join(a.name for a in foreign[:4])}{', ...' if len(foreign) > 4 else ''}")
    kept, encode, drop = [], [], []
    canon = rig.rename_canon(arm_obj) if renames else None   # scene objects: a bone rename is not exported
    for act in acts:
        src_clip = act.get(KEY_CLIP)
        src_clip = None if src_clip is None else str(src_clip)
        born = act.get(KEY_IMPORT_NAME)
        renamed = (act.name != str(born)) if born is not None else (src_clip is not None and act.name != src_clip)
        target = src_clip if (src_clip is not None and not renamed) else act.name
        ev = _evaluation_edits(act)
        same = (src_clip is not None and target == src_clip and act.get(KEY_HASH) == action_hash(act, canon)
                and not ev and not rig_edits)
        if ev:
            warnings.append(f"Action {act.name}: " + "; ".join(ev[:2]) + " -- re-encoded as Blender plays it")
        if same:
            kept.append(src_clip)
        else:
            encode.append((act, target))
            if src_clip is not None and src_clip != target:
                drop.append(src_clip)                      # renamed: the old clip name goes
    on_rig = {pb.name for pb in arm_obj.pose.bones}
    old = {o: n for n, o in canon.items()} if canon else {}
    gone = {str(d) for d in deleted}
    gone_clips = {}
    for act in acts:
        lost = unknown_bone_keys(act, on_rig, old)
        for b in [b for b in lost if b in gone]:
            gone_clips.setdefault(b, []).append(act.name)
        lost = [b for b in lost if b not in gone]
        if lost:
            warnings.append(f"Action {act.name} keys bone{'s' if len(lost) > 1 else ''} "
                            f"{', '.join(repr(b) for b in lost[:4])}{', ...' if len(lost) > 4 else ''}, which the rig has "
                            "no bone called: Export writes the clip without those keys. Restore the name, then use Boner > "
                            "Bones > Rename Bone, or delete those F-curves")
    for b, clips in gone_clips.items():
        warnings.append(f"deleted bone '{b}': its keys in {len(clips)} Action(s) ({', '.join(clips[:4])}"
                        f"{', ...' if len(clips) > 4 else ''}) are not written (kept clips lose its track too)")
    loose = unclaimed_actions(arm_obj, acts, old)
    if loose:
        warnings.append(f"Action{'s' if len(loose) > 1 else ''} "
                        f"{', '.join(a.name for a in loose[:4])}{', ...' if len(loose) > 4 else ''} "
                        f"key{'' if len(loose) > 1 else 's'} bones of rig '{arm_obj.name}' but "
                        f"{'are' if len(loose) > 1 else 'is'} not exported: to export one, make it the rig's active Action "
                        "or push it onto a muted NLA track")
    present = {str(a.get(KEY_CLIP)) for a in acts if a.get(KEY_CLIP) is not None}
    for clip in pairs:
        if clip not in present and clip not in kept and clip not in [c for _, c in encode]:
            drop.append(clip)                              # the Action was deleted
    names = kept + [c for _, c in encode]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        who = {n: [a.name for a, c in encode if c == n] + ([f"(kept {n})"] if n in kept else []) for n in dups}
        raise ValueError("two Actions write the same clip: " + "; ".join(f"{n}: {', '.join(w)}" for n, w in who.items())
                         + ". Rename one of them")
    drop = [d for d in dict.fromkeys(drop) if d not in names]
    return ClipPlan(kept, encode, drop, warnings)


def encode_actions(arm_obj, model, actions, default_fps, bone_names=None):
    """ICParse clips for the given (Action, clip name) pairs, sampled from Blender's own evaluation and encoded
    against ``model`` (the export model: its bind decides track presence, its geometry the bounds). Bones are sampled
    in source order under their current names (:func:`rig.current_bone_names`); an Action whose F-curves still name a
    renamed bone by its import name (an added bone by its stamp name; Blender's own rename leaves an Action on no NLA
    strip alone) is sampled with those paths pointed at the bone's current name, and put back afterwards.
    ``bone_names``: the rig bones to sample in the export model's bone order (an export that added bones:
    ``ops.sample_bone_names``); a bone the Action does not key rests at its bind-local transform, as the engine holds a
    bone a clip has no track for (:func:`sample_world` puts its channels at rest)."""
    if bone_names is None:
        try:
            bone_names = rig.current_bone_names(arm_obj)
        except KeyError:
            bone_names = rig.source_bone_names(arm_obj, model)
    renames = {o: n for n, o in rig.rename_canon(arm_obj).items()}
    out = []
    for act, clip_name in actions:
        start, end = frame_range(act)
        if end <= start:
            end = start + 1
        stale = stale_bone_paths(act, renames)
        old_paths = [(fc, fc.data_path) for fc, _p in stale]
        try:
            fix_stale_bone_paths(act, renames)
            W = sample_world(arm_obj, act, bone_names, start, end)
        finally:
            for fc, path in old_paths:
                fc.data_path = path
        K = end - start
        times = [f / K for f in range(K + 1)]
        fps = float(act.get(KEY_FPS, 0) or default_fps)
        out.append(rig.encode_animation(model, clip_name, fps, times, W))
    return out


def encode_scene_actions(arm_obj, data, actions, default_fps):
    """Scene clips for the given (Action, clip name) pairs, sampled from Blender's own evaluation."""
    out = []
    for act, clip_name in actions:
        start, end = frame_range(act)
        if end <= start:
            end = start + 1
        W = sample_world(arm_obj, act, data.bone_names, start, end)
        K = end - start
        times = [f / K for f in range(K + 1)]
        fps = float(act.get(KEY_FPS, 0) or default_fps)
        out.append(rig.encode_scene_animation(data, clip_name, fps, times, W))
    return out
