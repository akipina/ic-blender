"""Boner > Bones > Rename Bone: rename a bone of the creature's rig, and Export writes the new name everywhere.

A rig bone keeps its identity through a rename (its import stamp, ``rig.KEY_BONE_SRC_INDEX``), so the export follows it:
the skeleton gets the new name, and the limb bone lists, connection hosts, IK chains and clip tracks that named the bone
are rewritten (``structure.apply_bone_renames``); clips whose keys did not change stay kept (``actions.action_hash``
reads renamed bones' F-curve paths back to their import names). Blender's own rename (Outliner, Bone Properties, F2)
exports the same: Blender renames the bone, its vertex groups and the F-curves of the Actions the rig uses, and the
export reads the Actions it left alone (on no NLA strip) through the identity. This operator does the same edit and also
rewrites those Actions' F-curves, so the Dope Sheet shows every Action under the new name.

The rules (bpy-free): ``boner.bone_name_error`` (unique ignoring case, at most 63 ASCII characters),
``structure.name_conflicts`` (no rename chains), ``boner.bone_rename_warnings`` (names the game looks up).
"""
from __future__ import annotations

import bpy
import numpy as np

from . import actions
from . import boner
from . import draw
from . import rig
from . import store


def _poll(cls, context) -> bool:
    obj = context.active_object
    if not store.is_native(obj) or obj.get("bz_scene"):
        return False
    if store.rig(obj) is None:
        cls.poll_message_set("this creature has no rig")
        return False
    return True


def _stamp_legacy(arm) -> str | None:
    """Give a rig imported before the identity stamps its stamps from the import snapshot (every snapshot name still on
    the rig, nothing renamed yet). Returns why it can't, or None (also when the rig is stamped already)."""
    names = arm.get(rig.KEY_BONE_SRC_NAMES)
    if names is None:
        return ("this rig was imported before the bone snapshot, so a renamed bone can't be told from a new one: "
                "re-import the creature to rename bones")
    if any(b.get(rig.KEY_BONE_SRC_INDEX) is not None for b in arm.data.bones):
        return None
    missing = [str(n) for n in names if arm.data.bones.get(str(n)) is None]
    if missing:
        return (f"this rig was imported before bones carried identity stamps and bone '{missing[0]}' is not on it (renamed "
                "or deleted since): rename it back first, or re-import the creature")
    for i, n in enumerate(names):
        arm.data.bones[str(n)][rig.KEY_BONE_SRC_INDEX] = i
    return None


def _restamp(arm) -> None:
    """Point every rig bone's stamp at the source bone it resolves to: a bone Symmetrize wrote onto (it copies the
    mirrored bone's stamp) that is still found by its import name gets its own stamp back, so a later rename of it keeps
    its identity."""
    sk = rig.rig_skeleton(arm)
    if sk is None or not sk.resolution.stamped:
        return
    for i, c in enumerate(sk.resolution.current):
        b = arm.data.bones.get(c) if c is not None else None
        if b is not None and b.get(rig.KEY_STUB_BONE) is None and b.get(rig.KEY_BONE_SRC_INDEX) != i:
            b[rig.KEY_BONE_SRC_INDEX] = i


def stale_map(arm, old, new) -> dict:
    """{name an Action's F-curve may still use: the name it gets} for renaming the rig bone ``old`` to ``new``: ``old``
    itself and the import (or creation) name of every renamed bone, the bone being renamed pointing at ``new``."""
    out = {old: new}
    for cur, was in rig.rename_canon(arm).items():
        out[was] = new if cur == old else cur
    return out


def rename_refusal(arm, old, new) -> str | None:
    """Why the rig bone ``old`` can't be renamed ``new``, or None: the name rule, and no rename chain (``new`` is what
    another bone was called at import or created as)."""
    if arm.data.bones.get(old) is None:
        return f"'{old}' is not a bone of the rig '{arm.name}'"
    if new == old:
        return f"'{old}' already has that name"
    stale = stale_map(arm, old, new)
    for act in actions.actions_of(arm):
        why = actions.stale_path_clash(act, stale)
        if why:
            return why
    why = boner.bone_name_error(new, [b.name for b in arm.data.bones if b.name != old])
    if why:
        return why
    sk = rig.rig_skeleton(arm)
    if sk is None:
        return None
    bone = arm.data.bones[old]
    sid = bone.get(rig.KEY_BONE_SRC_INDEX)
    mine = str(bone.get(rig.KEY_STUB_BONE)) if bone.get(rig.KEY_STUB_BONE) is not None else (
        sk.snapshot[int(sid)][0] if sid is not None and 0 <= int(sid) < len(sk.snapshot) else None)
    taken = [(n, "was called at import") for n, _p in sk.snapshot] + \
        [(str(b.get(rig.KEY_STUB_BONE)), "was created as") for b in arm.data.bones if b.get(rig.KEY_STUB_BONE) is not None]
    for idn, how in taken:
        if idn.lower() == new.lower() and (mine is None or idn.lower() != mine.lower()):
            holder = next((c for (n, _p), c in zip(sk.snapshot, sk.resolution.current) if n == idn), None) or idn
            return (f"another bone ('{holder}') {how} '{idn}', so the name would mean two bones: pick a name no bone had "
                    "(or export, re-import, then rename)")
    return None


def rename_bone(context, obj, arm, old, new) -> list:
    """Rename the rig bone ``old`` to ``new`` (the caller checked :func:`rename_refusal`): the rig bone (Blender renames
    its vertex groups and the F-curves of the Actions the rig uses), then the F-curves of the rig's other Actions (on
    no NLA strip, or naming the bone by its import name after an earlier Blender rename). Leaves a mesh in Edit Mode
    and returns to it. Returns the warnings of :func:`boner.bone_rename_warnings` for the import name."""
    was = obj.mode
    prev_active = context.view_layer.objects.active
    arm_was = arm.mode
    arm_selected = arm.select_get()
    try:
        if was == "EDIT":
            # the creature's Edit Mode undo steps hold its mesh only: rename through the rig's own Edit Mode (as Add Stub
            # Socket creates its bone), which Blender's undo restores from that step
            rig._set_mode(context, obj, "OBJECT")
            rig._set_mode(context, arm, "EDIT")
        if arm.mode == "EDIT":
            arm.data.edit_bones[old].name = new
            got = arm.data.edit_bones.get(new)
        else:
            arm.data.bones[old].name = new
            got = arm.data.bones.get(new)
        if got is None:
            raise RuntimeError(f"Blender did not rename '{old}' to '{new}'")
        if arm.mode != arm_was:
            rig._set_mode(context, arm, arm_was)
        stale = {old: new}
        stale.update({o: n for n, o in rig.rename_canon(arm).items()})
        try:
            for act in actions.actions_of(arm):          # every Action checked before any is rewritten
                why = actions.stale_path_clash(act, stale)
                if why:
                    raise ValueError(why)
            for act in actions.actions_of(arm):
                actions.fix_stale_bone_paths(act, stale)
        except ValueError:
            # roll the bone back (its vertex groups and in-use Actions follow): a CANCELLED operator leaves no undo step
            if arm.mode == "EDIT":
                arm.data.edit_bones[new].name = old
            else:
                arm.data.bones[new].name = old
            raise
    finally:
        # every restore step on its own: a failing one must not keep the creature out of its mode
        for step in (lambda: rig._set_mode(context, arm, arm_was) if arm.mode != arm_was else None,
                     lambda: setattr(context.view_layer.objects, "active", obj),
                     lambda: rig._set_mode(context, obj, was) if obj.mode != was else None,
                     lambda: arm.select_set(arm_selected),
                     lambda: setattr(context.view_layer.objects, "active", prev_active)):
            try:
                step()
            except (RuntimeError, ReferenceError, TypeError):
                pass
    s = getattr(context.scene, "bz_boner", None)
    if s is not None and s.bone == old:
        s.bone = new
    draw.invalidate(obj.name)
    sk = rig.rig_skeleton(arm)
    import_name = old
    if sk is not None:
        import_name = next((o for o, n in sk.renames() if n == new), old)
    return boner.bone_rename_warnings([(import_name, new)])


class BZ_OT_rename_bone(bpy.types.Operator):
    """Rename a bone of the rig; its vertex group, Actions and the export follow"""
    bl_idname = "bz.rename_bone"
    bl_label = "Rename Bone"
    bl_options = {"REGISTER", "UNDO"}

    bone: bpy.props.StringProperty(name="Bone", default="", options={"SKIP_SAVE"})
    new_name: bpy.props.StringProperty(name="New Name", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        arm = store.rig(obj)
        if not self.bone:
            s = getattr(context.scene, "bz_boner", None)
            self.bone = s.bone if s is not None and s.bone else (arm.data.bones.active.name if arm.data.bones.active
                                                                  else "")
        if not self.bone or arm.data.bones.get(self.bone) is None:
            self.report({"ERROR"}, "pick a bone first (Boner > Bones > Bone)")
            return {"CANCELLED"}
        if not self.new_name:
            self.new_name = self.bone
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        col = self.layout.column()
        col.label(text=f"Rename '{self.bone}'", icon="BONE_DATA")
        col.prop(self, "new_name")
        uses = boner.hard_coded_uses(self.bone)
        if uses:
            sub = col.column()
            sub.alert = True
            sub.label(text=f"The game looks '{self.bone}' up by name:", icon="ERROR")
            for u in uses:
                sub.label(text="  " + u[:70])

    def execute(self, context):
        obj = context.active_object
        arm = store.rig(obj)
        old, new = str(self.bone), str(self.new_name)
        why = _stamp_legacy(arm)
        if not why and arm.mode != "EDIT":
            _restamp(arm)
        why = why or rename_refusal(arm, old, new)
        if why:
            self.report({"ERROR"}, f"Rename Bone refused: {why}")
            return {"CANCELLED"}
        try:
            warns = rename_bone(context, obj, arm, old, new)
        except (RuntimeError, ValueError) as e:
            self.report({"ERROR"}, f"Rename Bone failed: {e}")
            return {"CANCELLED"}
        text = f"Renamed bone '{old}' to '{new}': Export writes it into the skeleton, limb lists, hosts, IK chains and clips"
        if warns:
            self.report({"WARNING"}, text + "; WARNING: " + "; ".join(warns))
        else:
            self.report({"INFO"}, text)
        return {"FINISHED"}


CLASSES = (BZ_OT_rename_bone,)


# ------------------------------------------------------------------------------------------------ Add Bone
# A bone added to the rig exports as a new BONE record (``structure.insert_added_bones``: before the stub bones, bind-local
# from its rest, flags and colour from its parent). Identity: the bone's ``rig.KEY_BONE_UID`` stamp, listed in the
# creature's journaled new-bone table (``store.KEY_BONE_NEW``); control points skinned to it hold owner key n_src + j.
# Undo: Add Bone always finishes with the rig out of its Edit Mode, so its undo step is one Blender restores for the rig
# AND the creature's tables together (an armature Edit Mode step holds the bones only, and would leave the tables
# naming a bone that is gone); with the creature in Edit Mode its tables follow its mesh generation (the journal).
def _source(obj):
    from . import core
    return core.read_source(store.source_path(obj))


def _stamp(arm, name, uid, stamp_name) -> None:
    bones = arm.data.edit_bones if arm.mode == "EDIT" else arm.data.bones
    b = bones[name]
    b[rig.KEY_BONE_UID] = str(uid)
    b[rig.KEY_BONE_NEW_NAME] = str(stamp_name)


def adopt_pending(obj, arm, src, chains=True, src_stubs=None) -> list:
    """Stamp the rig's added bones no Boner operator has stamped yet (Blender's Add / Extrude, copies of added bones) --
    every one the add rule takes, chains too -- append them to the new-bone table, and lay the added bones' limb entries
    out by identity (``structure.added_limb_rows``, as Export does): each in its current parent's limb, listed by its
    stamp name; entries of added bones no longer on the rig are dropped. The caller has begun the journaled edit.
    ``src_stubs``: the export's stub bone indices (``store.export_stubs``, read before the edit began; None = the
    source's), so a bone under a new stub limb's bone is not stamped. Returns ``[(name, limb or "")]`` of the bones it
    stamped."""
    import uuid
    from . import structure
    sk = rig.rig_skeleton(arm, src, src_stubs)
    if sk is None:
        return []
    uids = store.new_bone_uids(obj)
    rows, _bad = rig.classify_added(arm, sk.resolution, sk.stubs, sk.stub_bad, uids, src, chains=chains,
                                    src_stubs=src_stubs)
    todo = [(ra, key, pk) for ra, key, pk, default in rows if default]
    if not todo:
        return []
    n_src = len(src.bones)
    base = n_src + len(uids)
    remap, made = {}, []
    for j, (ra, key, _pk) in enumerate(todo):
        remap[int(key)] = base + j
        u = uuid.uuid4().hex
        _stamp(arm, ra.name, u, ra.name)
        made.append((ra.name, u))
    store.add_new_bones(obj, [u for _n, u in made], [n for n, _u in made])
    added = []
    for ra, key, pk, default in rows:
        k = remap.get(int(key), int(key))
        p = remap.get(int(pk), int(pk))
        stamp = None if default or ra.stamp is None or ra.stamp == ra.name else ra.stamp
        added.append(structure.AddedBone(ra.name, k, p, tuple(np.eye(4).reshape(-1)), stamp=stamp,
                                         limb_default=int(key) in remap))
    lrows = store.limb_rows(obj)
    keep = [b.name for b in sk.stubs] + [str(b.get(rig.KEY_STUB_BONE)) for b in sk.stubs] + [n for n, _w in sk.stub_bad]
    placed = structure.added_limb_rows(lrows, src, added, store.new_bone_table_names(obj), keep, by_stamp=True,
                                       pinned=store.limb_pins(obj))
    if placed.rows is not lrows:
        store.set_limb_rows(obj, placed.rows)
    rows = placed.rows if placed.rows is not None else structure.source_rows(src)
    key_of = {ab.name: int(ab.key) for ab in added}
    return [(n, rows[placed.limb_of[key_of[n]]].name if placed.limb_of.get(key_of[n], -1) >= 0 else "")
            for n, _u in made]


def add_bone_refusal(obj, arm, parent, name, src) -> str | None:
    """Why Add Bone can't add ``name`` under the rig bone ``parent``, or None."""
    from . import structure
    if not parent or arm.data.bones.get(parent) is None:
        return f"'{parent}' is not a bone of the rig '{arm.name}': pick the parent bone first (Boner > Bones > Bone)"
    why = boner.bone_name_error(name, [b.name for b in arm.data.bones])
    if why:
        return why
    sk = rig.rig_skeleton(arm, src)
    if sk is not None:
        for idn in [n for n, _p in sk.snapshot] + [str(b.get(rig.KEY_STUB_BONE)) for b in sk.stubs]:
            if idn.lower() == str(name).lower():
                return (f"a bone of the creature was called '{idn}' at import or creation: limb bone lists, connection hosts "
                        "and Actions name bones by that name, so it would mean two bones. Pick another name")
        stubs = {b.name for b in sk.stubs} | {n for n, _w in sk.stub_bad}
        cur = sk.resolution.current
        pl = store.patch_limb(obj) if obj.get(store.KEY_PATCH_LIMB) is not None else None
        vb = store.vert_bone(obj) if store.has_vert_bone(obj) else None
        try:
            src_stubs = structure.export_stub_bones(src, store.limb_rows(obj), pl, vb)   # a new stub limb's bones too
        except ValueError as e:
            return f"the limb table can't be read for the stub bones: {e}"
        stubs |= {cur[i] for i in src_stubs if i < len(cur) and cur[i] is not None}
        if parent in stubs:
            return (f"'{parent}' is a stub bone (a stub limb's only bone, written after every other bone): add the bone "
                    "under the stub's host instead")
        bad = dict(sk.added_bad)
        if parent in bad:
            return f"its parent can't be exported: {bad[parent]}"
        if parent not in {c for c in cur if c is not None} and parent not in sk.added_names:
            return f"'{parent}' is no bone the export writes (a copy, or a bone Export refuses)"
    return None


def default_bone_name(arm, parent) -> str:
    base = "horn_l" if str(parent).lower() == "head" else f"{parent}_tip"
    taken = {b.name.lower() for b in arm.data.bones}
    name, k = base, 1
    while name.lower() in taken:
        k += 1
        name = f"{base}{k}"
    return name


class _RigEditMode:
    """``with _RigEditMode(context, obj, arm) as ebs:`` the rig's edit bones, the creature in Object Mode meanwhile;
    afterwards the rig is out of its Edit Mode and the creature is active again in its mode, the rig's selection kept."""

    def __init__(self, context, obj, arm):
        self.context, self.obj, self.arm = context, obj, arm

    def __enter__(self):
        self.was, self.arm_selected = self.obj.mode, self.arm.select_get()
        try:
            rig._set_mode(self.context, self.obj, "OBJECT")
            rig._set_mode(self.context, self.arm, "EDIT")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self.arm.data.edit_bones

    def __exit__(self, *exc):
        context, obj, arm = self.context, self.obj, self.arm
        for step in (lambda: rig._set_mode(context, arm, "OBJECT") if arm.mode != "OBJECT" else None,
                     lambda: setattr(context.view_layer.objects, "active", obj),
                     lambda: rig._set_mode(context, obj, self.was) if obj.mode != self.was else None,
                     lambda: arm.select_set(self.arm_selected)):
            try:
                step()
            except (RuntimeError, ReferenceError, TypeError):
                pass
        return False


def add_bone(context, obj, arm, parent, name, offset, length):
    """Create bone ``name`` under the rig bone ``parent``: head at the parent's tail moved by ``offset`` (in the parent
    bone's axes), the parent's orientation, ``length`` long, deforming. Leaves the rig out of its Edit Mode and returns the
    creature to its mode. Returns the bone's name; raises RuntimeError."""
    from mathutils import Vector
    made = None
    with _RigEditMode(context, obj, arm) as ebs:
        par = ebs.get(parent)
        if par is None:
            raise RuntimeError(f"'{parent}' is not a bone of the rig")
        M = par.matrix.copy()
        head = par.tail + M.to_3x3() @ Vector(offset)
        eb = ebs.new(name)
        made = eb.name
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, max(float(length), 1e-3), 0.0)
        R = M.to_3x3().normalized().to_4x4()
        R.translation = head
        eb.matrix = R
        eb.length = max(float(length), 1e-3)
        eb.parent = par
        eb.use_connect = False
        eb.use_deform = True
        if made != name:
            ebs.remove(eb)
            made = None
    if made is None:
        raise RuntimeError(f"the rig already has a bone called '{name}'")
    return made


def remove_bone(context, obj, arm, name) -> bool:
    """Take the rig bone ``name`` off again (Add Bone whose journaled edit could not start); True when it is gone."""
    try:
        with _RigEditMode(context, obj, arm) as ebs:
            eb = ebs.get(name)
            if eb is not None:
                ebs.remove(eb)
    except RuntimeError:
        pass
    return arm.data.bones.get(name) is None


def creature_of(context):
    """The native creature Add Bone works on: the active object, or -- with the rig active (in its Edit or Pose Mode) --
    the one creature whose rig it is. None otherwise."""
    obj = context.active_object
    if store.is_native(obj) and not obj.get("bz_scene"):
        return obj
    if obj is not None and obj.type == "ARMATURE":
        owners = [o for o in bpy.data.objects if store.is_native(o) and not o.get("bz_scene") and store.rig(o) == obj]
        if len(owners) == 1:
            return owners[0]
    return None


def _bones_poll(cls, context) -> bool:
    obj = creature_of(context)
    if obj is None:
        return False
    if store.rig(obj) is None:
        cls.poll_message_set("this creature has no rig")
        return False
    return True


def _active_parent(context, obj, arm) -> str:
    s = getattr(context.scene, "bz_boner", None)
    if s is not None and s.bone and arm.data.bones.get(s.bone) is not None:
        return s.bone
    return arm.data.bones.active.name if arm.data.bones.active else ""


class BZ_OT_add_bone(bpy.types.Operator):
    """Add a bone under the active bone, in its limb"""
    bl_idname = "bz.add_bone"
    bl_label = "Add Bone under Active"
    bl_options = {"REGISTER", "UNDO"}

    parent: bpy.props.StringProperty(name="Parent", default="", options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(name="Name", default="", options={"SKIP_SAVE"})
    offset: bpy.props.FloatVectorProperty(name="Offset", size=3, default=(0.0, 0.0, 0.0), subtype="TRANSLATION",
                                          description="Where the head goes from the parent's tail, in the parent bone's axes")
    length: bpy.props.FloatProperty(name="Length", default=0.0, min=0.0,
                                    description="Bone length (0 = half the parent's)")

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def invoke(self, context, event):
        obj = creature_of(context)
        arm = store.rig(obj)
        if not self.parent:
            self.parent = _active_parent(context, obj, arm)
        if not self.parent:
            self.report({"ERROR"}, "pick the parent bone first (Boner > Bones > Bone)")
            return {"CANCELLED"}
        if not self.name:
            self.name = default_bone_name(arm, self.parent)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        from . import connops, ops
        obj = creature_of(context)
        arm = store.rig(obj)
        rig_active = context.active_object == arm
        arm_was = arm.mode
        left_edit = arm.mode == "EDIT"
        if left_edit:                                   # flush the rig's edit bones (the checks read Bone data); Add Bone
            rig._set_mode(context, arm, "OBJECT")       # ends out of the rig's Edit Mode, so its undo step is Blender's
            context.view_layer.objects.active = obj     # global one: the rig and the creature's tables together
        if obj.mode != "OBJECT" and context.view_layer.objects.active != obj:
            context.view_layer.objects.active = obj
        parent = self.parent or _active_parent(context, obj, arm)
        name = self.name or (default_bone_name(arm, parent) if parent else "")
        try:
            src = _source(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Add Bone refused: the source creature can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        why = (store.sync(obj) or _stamp_legacy(arm) or add_bone_refusal(obj, arm, parent, name, src)  # sync: what the
               or connops.rig_edit_refusal(context, arm, "add the bone"))       # journaled edit needs, before the bone
        if not why:
            try:
                stubs = store.export_stubs(obj)          # what the bones it adopts on the way are classified against
            except Exception as e:
                why = f"the stub bones can't be worked out: {type(e).__name__}: {e}"
        if why:
            self.report({"ERROR"}, f"Add Bone refused: {why}")
            return {"CANCELLED"}
        pb = arm.data.bones[parent]
        length = float(self.length) if self.length > 0 else max(pb.length * 0.5, 1e-3)
        try:
            made = add_bone(context, obj, arm, parent, name, tuple(self.offset), length)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Add Bone failed: {e}")
            return {"CANCELLED"}
        if not ops._begin(self, obj):                   # reported; no bone left without its stamp, table entry and limb
            if not remove_bone(context, obj, arm, made):
                self.report({"ERROR"}, f"Add Bone failed and bone '{made}' could not be taken off the rig again: delete "
                                       "it in the rig's Edit Mode")
            return {"CANCELLED"}
        adopted = adopt_pending(obj, arm, src, src_stubs=stubs)   # the new bone and any bone Blender added before it
        mine = next((lim for n, lim in adopted if n == made), "")
        others = [n for n, _l in adopted if n != made]
        s = getattr(context.scene, "bz_boner", None)
        if s is not None:
            s.bone = made
        ops.forget_skin_stats(obj)
        limbops_forget(obj)
        draw.invalidate(obj.name)
        if rig_active:
            context.view_layer.objects.active = arm
            if arm_was == "POSE" and arm.mode != "POSE":
                rig._set_mode(context, arm, "POSE")
        text = (f"Added bone '{made}' under '{parent}'" + (f", in limb '{mine}'" if mine else
                                                              f", in no limb (its parent '{parent}' is in none)")
                + (f"; also adopted {', '.join(others)}" if others else "")
                + ": skin control points to it (Assign Selected) and Export writes it"
                + ("; the rig left its Edit Mode (one undo step for the rig and the creature)" if left_edit else ""))
        warns = boner.bone_add_warnings([made])
        if others:
            err = rig.skeleton_error(arm, src, src_stubs=stubs)   # a bone it adopted on the way may still stop the export
            if err:
                warns.append(f"Export refuses the skeleton as it is: {err}")
        text += ("; WARNING: " + "; ".join(warns)) if warns else ""
        obj["bz_last_add_bone"] = text
        self.report({"WARNING"} if warns else {"INFO"}, text)
        return {"FINISHED"}


def limbops_forget(obj):
    from . import limbops
    limbops.forget_table(obj)


class BZ_OT_adopt_bones(bpy.types.Operator):
    """Take bones made with Blender's Add, Extrude or Duplicate into the creature"""
    bl_idname = "bz.adopt_bones"
    bl_label = "Adopt Added Bones"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def execute(self, context):
        from . import ops, structure
        obj = creature_of(context)
        arm = store.rig(obj)
        rig_active = context.active_object == arm
        if arm.mode == "EDIT":
            rig._set_mode(context, arm, "OBJECT")
            context.view_layer.objects.active = obj
        try:
            src = _source(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Adopt Added Bones refused: the source creature can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        why = _stamp_legacy(arm)
        if not why:
            try:
                stubs = store.export_stubs(obj)          # a bone under a new stub limb's bone is not adopted
            except Exception as e:
                why = f"the stub bones can't be worked out: {type(e).__name__}: {e}"
        if why:
            self.report({"ERROR"}, f"Adopt Added Bones refused: {why}")
            return {"CANCELLED"}
        sk = rig.rig_skeleton(arm, src, stubs)
        _rows, bad = rig.classify_added(arm, sk.resolution, sk.stubs, sk.stub_bad, store.new_bone_uids(obj), src,
                                        chains=True, src_stubs=stubs)
        todo = [ra.name for ra, _k, _p, d in _rows if d]
        if not todo:
            # leaves and chains Blender adds are taken at export by themselves; what is left is ambiguous, and
            # each bone is named with why (copies of a source bone, copies of an added bone, bones under a stub bone)
            self.report({"ERROR"} if bad else {"WARNING"},
                        "no added bone to adopt: every bone Blender added is taken by Export as it is" + (
                            " -- ambiguous bones Adopt can't take: " + "; ".join(w for _n, w in bad[:3])
                            + (f"; and {len(bad) - 3} more" if len(bad) > 3 else "") if bad else ""))
            return {"CANCELLED"}
        final = ([c for c in sk.resolution.current if c is not None] + [b.name for b in sk.stubs]
                 + [ra.name for ra, _k, _p, _d in _rows])
        probs = structure.bone_name_problems(final, todo)      # the export's name rule, before anything is stamped
        if probs:
            self.report({"ERROR"}, "Adopt Added Bones refused: " + "; ".join(probs) + ": rename the bone first (Boner > "
                                   "Bones > Rename Bone), then adopt")
            return {"CANCELLED"}
        if not ops._begin(self, obj):
            return {"CANCELLED"}
        got = adopt_pending(obj, arm, src, src_stubs=stubs)
        if rig_active:
            context.view_layer.objects.active = arm
        ops.forget_skin_stats(obj)
        limbops_forget(obj)
        draw.invalidate(obj.name)
        self.report({"INFO"}, "Adopted " + ", ".join(f"'{n}'" + (f" ({lim})" if lim else " (no limb)") for n, lim in got))
        return {"FINISHED"}


CLASSES = CLASSES + (BZ_OT_add_bone, BZ_OT_adopt_bones)


# ------------------------------------------------------------------------------------------------ delete / re-parent
# A source bone deleted or re-parented in the rig's Edit Mode (X / Ctrl+P / the Outliner) exports (``topology``): the
# BONE record dropped or its parent index rewritten, the bind world kept, points of a deleted bone on its parent, limb
# lists, connection hosts, IK chains and kept clips' tracks following. The rules (``topology.topology_refusals``) run in
# the skeleton guard against the tables the export writes (:func:`topology_rules`).
KEY_BONE_LIMITS = rig.KEY_BONE_LIMITS    # on a rig Bone: its BONE record's joint limits (6 floats, + the flag word)


def topology_rules(obj, src):
    """``topology.Rules`` for the delete / re-parent rules of ``obj``'s rig: the limb rows as Export places them (added
    bones in their parent's limb), the connection hosts of the stored rows, the export's stub bones and the added bones
    (name -> (key, parent key)). Falls back to the source's records where a table can't be read (the guard says so
    elsewhere)."""
    from . import core, structure, topology
    arm = store.rig(obj)
    rows = None
    try:
        rows = store.limb_rows(obj)
    except (ValueError, RuntimeError):
        rows = None
    added, sk_added = [], {}
    try:
        uids = store.new_bone_uids(obj)
        added, _bad = rig.added_bones(arm, uids, src) if arm is not None else ([], [])
        sk_added = {ab.name: (int(ab.key), int(ab.parent)) for ab in added}
    except Exception:
        pass
    lists, names, ids = None, None, None
    try:
        placed = core.added_limb_placement(rows, src, added, store.new_bone_table_names(obj),
                                           rig.new_stub_bones(arm, src) if arm is not None else (),
                                           pinned=store.limb_pins(obj))
        use = placed.rows if placed is not None and placed.rows is not None else (rows if rows is not None
                                                                                   else structure.source_rows(src))
        lists = [structure.row_bones(r, src) for r in use]
        names = [r.name for r in use]
        ids = [int(r.limb_id) for r in use]
    except Exception:
        lists = names = ids = None
    hosts = None
    try:
        conns = store.ring_edits(obj)
        if conns is not None:
            hosts = []
            for r in conns:
                s = r.source()
                base = src.limb_connections[s] if 0 <= s < len(src.limb_connections) else None
                hosts.append((str(base.name if r.name is None and base is not None else r.name), str(r.bone)))
    except Exception:
        hosts = None
    try:
        stubs = store.export_stubs(obj)
    except Exception:
        stubs = None
    return topology.rules_of(src, limb_lists=lists, limb_names=names, limb_ids=ids, hosts=hosts, stubs=stubs,
                             added=sk_added)


def rig_bone_limits(obj, arm, src, added=()):
    """{owner key: joint limit floats} for every rig bone carrying the :data:`KEY_BONE_LIMITS` custom property (source
    bones by their import stamp, added bones by their owner key); None when no bone has it. The export writes a bone's
    limits only when they differ from its record (``topology.apply_bone_limits``)."""
    out = {}
    key_of = {ab.name: int(ab.key) for ab in (added or ())}
    n = len(src.bones)
    for b in arm.data.bones:
        vals = b.get(KEY_BONE_LIMITS)
        if vals is None:
            continue
        sid = b.get(rig.KEY_BONE_SRC_INDEX)
        key = int(sid) if sid is not None and 0 <= int(sid) < n else key_of.get(b.name)
        if key is None:
            continue
        out[key] = [float(x) for x in vals]
    return out or None
