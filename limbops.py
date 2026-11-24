"""Boner > Limbs: the limb table's operators (rename, classification flags, Add Standard Limb, Delete) and its read-out.

Every operator writes the whole limb table through ``store.set_limb_rows`` after ``store.begin_edit`` (a new journal
generation), so edit-mode Undo / Redo restore it and Export writes it (``structure.apply_limbs``). The rules -- the
checkbox groups, the standard classes, the name rule, the delete refusal -- are bpy-free in ``boner``; the Object
Editor's limb warnings are ``ICParse.validate.check_oe_structure`` (Validate's ``WARN oe:`` lines).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

import bpy

from . import boner
from . import core
from . import draw
from . import ops
from . import store
from . import structure


# ------------------------------------------------------------------------------------------------ read-out
_SRC_BONES: dict = {}


@dataclass
class SourceSkeleton:
    names: list                 # the file's bone names (what limb bone lists hold)
    parents: list               # parent index per bone, -1 for the root
    limb_bones: list            # bone names per SOURCE limb record
    added: tuple = ()           # (live_skeleton only) the names of the bones added on the rig
    note: str = ""              # (live_skeleton only) why it is the source's after all


# A name no bone can have (a deleted source bone's slot): slots keep their index, so an owner key still indexes the
# list, and no limb entry, no rig bone and no user input can ever match one.
GONE = "\x00gone"


def live_skeleton(obj) -> SourceSkeleton:
    """The skeleton EXPORT WRITES, which is what a limb bone list names: the source's bones under their current rig
    names, without the ones deleted on the rig, re-parented as the rig has them, plus the bones added on the rig (C8).

    Slot ``i < n_src`` stays source bone ``i`` under its FILE name (a deleted one becomes :data:`GONE`, parented to
    nothing) -- that is what a stored row names it by, and the export applies the renames -- and an added bone sits at
    its owner key under the name its Add Bone stamp holds (``AddedBone.listed_as``, what the stored rows use), so an
    owner key still indexes the list. Falls back to the source's skeleton with a ``note`` when the rig cannot be read --
    never silently. Before fit-to-rig (2026-09-16) the limb operators read :func:`source_skeleton`, so a bone C8 had
    added was "not a bone of this creature's skeleton" to all of them."""
    base = source_skeleton(obj)
    arm = store.rig(obj)
    if arm is None:
        return base
    from . import rig as _rig
    try:
        src = core.read_source(store.source_path(obj))
        added, _bad = _rig.added_bones(arm, store.new_bone_uids(obj), src)
        topo, _tbad = _rig.bone_topology(arm, model=src, added=added)
    except Exception as e:                              # the skeleton alert says why; the lists stay the source's
        return SourceSkeleton(base.names, base.parents, base.limb_bones,
                              note=f"the rig's own bones can't be read ({type(e).__name__}: {e}), so the bone lists "
                                   "name the source's skeleton")
    if not added and not topo:
        return base                                     # nothing added, deleted or re-parented (a rename is the file's)
    n = len(src.bones)
    gone = {int(d) for d in topo.deleted}
    pmap = topo.parent_map
    names = [GONE if i in gone else str(base.names[i]) for i in range(n)]
    parents = [-1 if i in gone else int(pmap.get(i, base.parents[i])) for i in range(n)]
    top = max([n - 1] + [int(ab.key) for ab in added])
    names += [GONE] * (top + 1 - n)
    parents += [-1] * (top + 1 - n)
    for ab in added:
        names[int(ab.key)] = str(ab.listed_as)
        parents[int(ab.key)] = int(ab.parent)
    return SourceSkeleton(names, parents, base.limb_bones, added=tuple(str(ab.listed_as) for ab in added))


def source_skeleton(obj) -> SourceSkeleton:
    """The source file's skeleton and limb bone lists (read once per file version). Limb bone lists name the bones the
    EXPORT writes, so the operators read :func:`live_skeleton`; this is its base and the fallback."""
    path = store.source_path(obj)
    key = os.path.abspath(path)
    stamp = core._source_stamp(path)
    hit = _SRC_BONES.get(key)
    if hit is None or hit[0] != stamp:
        m = core.read_source(path)
        hit = (stamp, SourceSkeleton(names=[str(b.name) for b in m.bones],
                                     parents=[-1 if b.parent is None else int(b.parent) for b in m.bones],
                                     limb_bones=[[str(b) for b in l.bone_names] for l in m.limbs]))
        _SRC_BONES.clear()
        _SRC_BONES[key] = hit
    return hit[1]


def source_limb_bones(obj):
    """Bone names per SOURCE limb record (read once per file version)."""
    return source_skeleton(obj).limb_bones


@dataclass
class LimbTable:
    rows: list = field(default_factory=list)          # [structure.LimbRow]
    bones: list = field(default_factory=list)         # effective bone names per row
    patches: list = field(default_factory=list)       # patch count per row
    status: list = field(default_factory=list)        # boner.limb_status per row
    error: str = ""
    added: list = field(default_factory=list)         # placed=True: the rig names of the added bones in ``bones``
    notes: list = field(default_factory=list)         # ... what Export moves or drops (the stored rows still say it)
    warnings: list = field(default_factory=list)      # ... an added bone in no limb, or why the placement can't be shown


def limb_table(obj, placed=False) -> LimbTable:
    """The Limbs list: rows, their effective bone lists (a row's own list, else its source record's), patch counts and
    the status word; ``error`` says why there is none. ``placed``: the bone lists as Export writes them -- every added
    bone in its current parent's limb, or where a limb operator pinned it (``core.added_limb_placement``,
    ``store.limb_pins``), so a bone re-parented or deleted on the rig shows where it goes before the next Add Bone /
    Adopt stores it (the panel; operators edit the stored rows)."""
    try:
        err = store.generation_error(obj)              # read-only: the panel draws this (no ID property writes there)
        if err:
            return LimbTable(error=err)
        rows = store.limb_rows(obj)
    except (ValueError, RuntimeError) as e:
        return LimbTable(error=str(e))
    if rows is None:
        return LimbTable(error="this creature has no limb table (import could not set it up): its source's limbs are "
                               "exported unchanged; re-import it to edit limbs")
    src_bones = None
    bones = []
    for r in rows:
        if r.bones is not None:
            bones.append(list(r.bones))
            continue
        if src_bones is None:
            try:
                src_bones = source_limb_bones(obj)
            except Exception as e:
                return LimbTable(error=f"the source's limb bone lists can't be read: {type(e).__name__}: {e}")
        bones.append(list(src_bones[r.src]) if 0 <= r.src < len(src_bones) else [])
    added, notes, warnings = [], [], []
    if placed:
        bones, added, notes, warnings = _export_placement(obj, rows, bones)
    pl = store.patch_limb(obj)
    counts = np.bincount(pl[(pl >= 0) & (pl < len(rows))], minlength=len(rows))[:len(rows)] if len(pl) \
        else np.zeros(len(rows), dtype=np.int64)
    ids = [r.limb_id for r in rows]
    status = [boner.limb_status(i, ids, int(counts[i]), len(bones[i])) for i in range(len(rows))]
    return LimbTable(rows=rows, bones=bones, patches=[int(c) for c in counts], status=status, added=added, notes=notes,
                     warnings=warnings)


def _export_placement(obj, rows, bones):
    """``(bones, added names, notes, warnings)``: the rows' bone lists with the rig's added bones where Export puts them
    (:func:`limb_table` ``placed``). Unchanged for a rig with no added bone, no created stub bone and no new-bone table."""
    from . import rig
    arm = store.rig(obj)
    uids = store.new_bone_uids(obj)
    if arm is None or (not uids and all(b.get(rig.KEY_BONE_SRC_INDEX) is not None for b in arm.data.bones)):
        return bones, [], [], []
    try:
        src = core.read_source(store.source_path(obj))
        added, _bad = rig.added_bones(arm, uids, src)      # refused bones: the skeleton alert says why
        placed = core.added_limb_placement(rows, src, added, store.new_bone_table_names(obj),
                                           rig.new_stub_bones(arm, src), pinned=store.limb_pins(obj))
    except Exception as e:                                  # never silent: the panel says the lists are the stored ones
        return bones, [], [], [f"the limb lists show the stored rows: where Export puts the added bones can't be worked "
                               f"out ({type(e).__name__}: {e})"]
    if placed is None:
        return bones, [], [], []
    names = [r.name for r in rows]
    out = bones if placed.rows is rows else [
        [str(b) for b in r.bones] if r.bones is not None
        else [str(b) for b in src.limbs[r.src].bone_names] if 0 <= r.src < len(src.limbs) else [] for r in placed.rows]
    notes = [f"at Export: {t}" for t in placed.moved_text(names)]
    if placed.dropped:
        notes.append(f"at Export: dropped {placed.dropped_text(names)}")
    return [list(b) for b in out], [ab.name for ab in added], notes, list(placed.warnings)


_TABLE_CACHE: dict = {}


def limb_table_cached(obj) -> LimbTable:
    """:func:`limb_table` with the export's placement of added bones, for panel draw (every redraw): recomputed when the
    generation, the patch or limb tables, the new-bone table or the rig's bones (names, parents, stamps) change."""
    from . import rig
    arm = store.rig(obj)
    key = (obj.get(store.KEY_GEN), store.mesh_generation(obj), int(obj.get(store.KEY_NPATCH, 0)),
           tuple(obj.get(store.KEY_LIMB_NAMES, [])), tuple(obj.get(store.KEY_LIMB_IDS, [])),
           tuple(obj.get(store.KEY_LIMB_BONE_COUNTS, [])), tuple(obj.get(store.KEY_LIMB_BONES, [])),
           tuple(store.new_bone_uids(obj)), tuple(store.new_bone_table_names(obj)),
           rig.bones_key(arm) if arm is not None else None)
    hit = _TABLE_CACHE.get(obj.name)
    if hit is None or hit[0] != key:
        hit = (key, limb_table(obj, placed=True))
        _TABLE_CACHE[obj.name] = hit
    return hit[1]


def forget_table(obj=None) -> None:
    if obj is None:
        _TABLE_CACHE.clear()
    else:
        _TABLE_CACHE.pop(obj.name, None)


def active_limb(context, obj, index=-1) -> int:
    """The operator's limb: ``index`` when given (>= 0), else the Boner panel's active limb."""
    if index >= 0:
        return int(index)
    s = getattr(context.scene, "bz_boner", None)
    return int(s.active_limb) if s is not None else -1


def _set_active(context, index) -> None:
    s = getattr(context.scene, "bz_boner", None)
    if s is not None:
        s.active_limb = max(int(index), 0)


def _limbs_poll(cls, context) -> bool:
    obj = context.active_object
    if not ops._guard_poll(cls, context) or obj.get("bz_scene"):
        return False
    if obj.get(store.KEY_LIMB_NAMES) is None:
        cls.poll_message_set("this creature has no limb table (import could not set it up): re-import it")
        return False
    return True


def _table_for_edit(op, obj):
    """Start a journaled edit and read the table (None + an ERROR report when it can't)."""
    if not ops._begin(op, obj):
        return None
    t = limb_table(obj)
    if t.error:
        op.report({"ERROR"}, t.error)
        return None
    return t


def _finish(obj):
    forget_table(obj)
    ops.forget_ring_status(obj)
    draw.invalidate(obj.name)


# ------------------------------------------------------------------------------------------------ operators
class BZ_OT_limb_set_active(bpy.types.Operator):
    """Make this the active limb (Rename, Delete and the classification boxes work on it)"""
    bl_idname = "bz.limb_set_active"
    bl_label = "Set Active Limb"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty(default=0, min=0)

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def execute(self, context):
        _set_active(context, self.index)
        return {"FINISHED"}


class BZ_OT_rename_limb(bpy.types.Operator):
    """Rename the active limb (at most 63 ASCII characters, unique ignoring case). Nothing matches a limb by name: the
    Object Editor and the combiner use its classification"""
    bl_idname = "bz.rename_limb"
    bl_label = "Rename Limb"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(name="Name", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _limbs_poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        idx = active_limb(context, obj, self.index)
        names = store.limb_names(obj)
        if not 0 <= idx < len(names):
            self.report({"ERROR"}, f"no limb {idx}: pick one in Boner > Limbs")
            return {"CANCELLED"}
        self.index = idx
        if not self.name:
            self.name = names[idx]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        idx = active_limb(context, obj, self.index)
        names = store.limb_names(obj)
        if not 0 <= idx < len(names):
            self.report({"ERROR"}, f"no limb {idx}: the creature has {len(names)} limbs")
            return {"CANCELLED"}
        why = boner.limb_name_error(self.name, [n for i, n in enumerate(names) if i != idx])
        if why:
            self.report({"ERROR"}, f"Rename Limb refused: {why}")
            return {"CANCELLED"}
        if self.name == names[idx]:
            return {"CANCELLED"}
        t = _table_for_edit(self, obj)
        if t is None:
            return {"CANCELLED"}
        old = t.rows[idx].name
        rows = list(t.rows)
        rows[idx] = structure.LimbRow(rows[idx].src, str(self.name), rows[idx].limb_id, rows[idx].bones)
        store.set_limb_rows(obj, rows)
        _finish(obj)
        self.report({"INFO"}, f"Renamed limb '{old}' to '{self.name}'")
        return {"FINISHED"}


class BZ_OT_limb_flag(bpy.types.Operator):
    """Tick or untick a classification box of the active limb (Boner's Limb Options: ticking a box clears the other
    boxes of its group -- Left/Right, Front/Rear/Mid/Mid 2, Top/Side, Torso/Head/Tail/Arm/Leg/Wing/Fin). When the limb's
    connection is the only one with its old class, the connection's Child class follows in the same step; refused when
    it would leave other connections without their limb"""
    bl_idname = "bz.limb_flag"
    bl_label = "Limb Classification"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    bit: bpy.props.IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _limbs_poll(cls, context)

    @classmethod
    def description(cls, context, props):
        f = next((f for f in boner.LIMB_FLAGS if f.bit == props.bit), None)
        if f is None:
            return cls.__doc__
        group = {"SIDE": "Left / Right", "POSITION": "Front / Rear / Mid / Mid 2", "PLANE": "Top / Side",
                 "TYPE": "Torso / Head / Tail / Arm / Leg / Wing / Fin"}.get(f.group)
        return (f"{f.label} (0x{f.bit:x})" + (f": one of {group}" if group else ": keeps the limb on its own creature "
                                                                                "(never swapped in a combination)"))

    def execute(self, context):
        obj = context.active_object
        names = store.limb_names(obj)
        idx = active_limb(context, obj, self.index)
        if not 0 <= idx < len(names):
            self.report({"ERROR"}, f"no limb {idx}: the creature has {len(names)} limbs")
            return {"CANCELLED"}
        try:
            boner.toggle_flag(0, self.bit)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        ids = store.limb_ids(obj) or []
        new_id = boner.toggle_flag(ids[idx], self.bit)
        try:
            f1, f2, _ids = ops.ring_meta(obj)
        except ValueError as e:
            self.report({"ERROR"}, f"Limb Classification refused: the connections can't be read: {e}")
            return {"CANCELLED"}
        cn = [n for n, _b, _v in store.connections(obj)]
        why, follow = boner.class_change_plan(idx, ids, new_id, cn, f1, f2, limb_name=names[idx])
        if why:
            self.report({"ERROR"}, f"Limb Classification refused: {why}")
            return {"CANCELLED"}
        t = _table_for_edit(self, obj)
        if t is None:
            return {"CANCELLED"}
        crows = None
        if follow:
            try:
                crows = store.conn_rows(obj)
            except ValueError as e:
                self.report({"ERROR"}, f"Limb Classification refused: the connections can't be read: {e}")
                return {"CANCELLED"}
        r = t.rows[idx]
        rows = list(t.rows)
        rows[idx] = structure.LimbRow(r.src, r.name, new_id, r.bones)
        store.set_limb_rows(obj, rows)                  # one journal generation: the limb and its connection together
        moved = []
        for ci, f2_new in follow:
            c = crows[ci]
            moved.append(f"connection '{c.name}' child 0x{int(c.flags2):x} -> 0x{f2_new:x}")
            crows[ci] = core.RingEdit(index=ci, bone=c.bone, corners=c.corners, raw=c.raw, src=c.src, name=c.name,
                                      flags1=c.flags1, flags2=f2_new)
        if follow:
            store.set_conn_rows(obj, crows)
        _finish(obj)
        old_cls, new_cls = boner.class_of(r.limb_id), boner.class_of(new_id)
        self.report({"INFO"}, f"'{r.name}': 0x{r.limb_id:x} -> 0x{new_id:x} ({boner.class_label(new_id)})"
                              + (f"; {', '.join(moved)} (its Child class follows the limb)" if moved else ""))
        obj["bz_last_conn_follow"] = "; ".join(moved)
        f2_after = list(f2)
        for ci, f2_new in follow:
            f2_after[ci] = f2_new
        socket = boner.socket_warnings(ids, [x.limb_id for x in rows], list(f2), f2_after, [x.name for x in rows])
        obj["bz_last_socket_warning"] = "; ".join(socket)
        if socket:
            self.report({"WARNING"}, "; ".join(socket))
        if old_cls != new_cls:
            dup = [x.name for i, x in enumerate(rows) if i != idx and new_cls and boner.class_of(x.limb_id) == new_cls]
            if dup:
                self.report({"WARNING"}, f"'{dup[0]}' has the same class: the Object Editor warns about a duplicate class"
                                         + (" and two connections now attach that class" if follow and any(
                                             boner.class_of(x) == new_cls for j, x in enumerate(f2)
                                             if j not in {ci for ci, _ in follow}) else ""))
        return {"FINISHED"}


_STD_ITEMS: list = []


def _standard_items(self, context):
    """Add Standard Limb's menu: the standard classes this creature has no limb of (kept referenced: Blender's dynamic
    enum strings must outlive the call)."""
    global _STD_ITEMS
    obj = context.active_object if context else None
    native = store.is_native(obj)
    ids = store.limb_ids(obj) if native else None
    miss = boner.missing_standard(ids or []) if ids is not None else []
    need = {"patches": "required, needs patches", "stub": "required, may be a stub", "optional": "optional"}
    skeleton = {b.lower() for b in store.bone_names(obj)} if native and miss else set()
    _STD_ITEMS = [(s.suffix, f"{s.suffix} ({boner.class_label(s.limb_id)})",
                   f"A {boner.class_label(s.limb_id)} limb, class 0x{boner.class_of(s.limb_id):x} "
                   f"({need[s.need]}); root bone {s.root}"
                   + ("" if s.root.lower() in skeleton else " -- not in this skeleton, so it can't be added yet"))
                  for s in miss] \
        or [("NONE", "(every standard class is present)", "")]
    return _STD_ITEMS


class BZ_OT_add_standard_limb(bpy.types.Operator):
    """Add a limb of a standard class this creature has none of, as a stub: no patches and its typical root bone. Refused
    when the skeleton lacks that bone or another limb owns it (a limb with no bones makes the engine report 'limbs are
    boneless!' and ComboTest.exe won't take the creature). The Object Editor requires the leg, arm and wing classes (a
    stub will do)"""
    bl_idname = "bz.add_standard_limb"
    bl_label = "Add Standard Limb"
    bl_options = {"REGISTER", "UNDO"}

    slot: bpy.props.EnumProperty(name="Limb", items=_standard_items)

    @classmethod
    def poll(cls, context):
        return _limbs_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        std = boner.STANDARD_BY_SUFFIX.get(self.slot)
        if std is None:
            self.report({"WARNING"}, "every standard limb class is present")
            return {"CANCELLED"}
        ids = store.limb_ids(obj) or []
        have = next((n for n, i in zip(store.limb_names(obj), ids) if boner.class_of(i) == boner.class_of(std.limb_id)),
                    None)
        if have is not None:
            self.report({"ERROR"}, f"'{have}' already has the {boner.class_label(std.limb_id)} class")
            return {"CANCELLED"}
        names = store.limb_names(obj)
        name = boner.default_limb_name(names, obj.get(store.KEY_NAME, obj.name), std.suffix)
        why = boner.limb_name_error(name, names)
        if why:
            self.report({"ERROR"}, f"Add Standard Limb refused: {why}")
            return {"CANCELLED"}
        t = _table_for_edit(self, obj)
        if t is None:
            return {"CANCELLED"}
        sk = live_skeleton(obj)                          # a bone C8 added can root a limb (fit-to-rig)
        if sk.note:
            self.report({"WARNING"}, sk.note)
        root = next((b for b in sk.names if b != GONE and b.lower() == std.root.lower()), None)
        at = next((i for i, bl in enumerate(t.bones) if root and root.lower() in {b.lower() for b in bl}), None)
        # A bone C8 added sits in its parent's limb by the rule, not by anyone's choice, so the new limb simply takes
        # it (a source bone stays the user's to move first: its limb IS a choice). The donor may not go boneless.
        take = at is not None and root in sk.added and len(t.bones[at]) > 1
        why = boner.standard_root_refusal(std, root, None if take else (t.rows[at].name if at is not None else None))
        if why:
            self.report({"ERROR"}, f"Add Standard Limb refused: {why}")
            return {"CANCELLED"}
        bones = (root,)
        rows = list(t.rows)
        if take:
            kept = tuple(b for b in t.bones[at] if b.lower() != root.lower())
            src_list = source_limb_bones(obj)
            r = rows[at]
            rows[at] = structure.LimbRow(r.src, r.name, r.limb_id,
                                         None if 0 <= r.src < len(src_list) and kept == tuple(src_list[r.src]) else kept)
        rows += [structure.LimbRow(-1, name, int(std.limb_id), bones)]
        store.set_limb_rows(obj, rows)
        _pin_added(obj, sk, [(root, at if at is not None else -1, len(rows) - 1)])
        _set_active(context, len(rows) - 1)
        _finish(obj)
        self.report({"INFO"}, f"Added limb '{name}' ({boner.class_label(std.limb_id)}), a stub with root bone '{root}'"
                    + (f" (a bone added on the rig, taken out of '{t.rows[at].name}': it stays in this limb instead of "
                       "following its parent)" if take else ""))
        return {"FINISHED"}


_TARGET_ITEMS: list = []


def _target_items(self, context):
    global _TARGET_ITEMS
    obj = context.active_object if context else None
    names = store.limb_names(obj) if store.is_native(obj) else []
    _TARGET_ITEMS = [("AUTO", "Torso (default)", "The Torso limb, else the first other limb")] + \
        [(str(i), n, f"Move the deleted limb's patches to '{n}'") for i, n in enumerate(names)]
    return _TARGET_ITEMS


class BZ_OT_delete_limb(bpy.types.Operator):
    """Delete the active limb: its patches go to the limb you choose (the Torso by default) and its bones are left in
    no limb (Validate lists them). Refused while it is the last limb of a class a connection uses (child or parent)"""
    bl_idname = "bz.delete_limb"
    bl_label = "Delete Limb"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    target: bpy.props.EnumProperty(name="Patches to", items=_target_items, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _limbs_poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        self.index = active_limb(context, obj, self.index)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        obj = context.active_object
        names = store.limb_names(obj)
        if 0 <= self.index < len(names):
            self.layout.label(text=f"Delete limb '{names[self.index]}'", icon="TRASH")
        self.layout.prop(self, "target")

    def execute(self, context):
        obj = context.active_object
        names = store.limb_names(obj)
        idx = active_limb(context, obj, self.index)
        if not 0 <= idx < len(names):
            self.report({"ERROR"}, f"no limb {idx}: the creature has {len(names)} limbs")
            return {"CANCELLED"}
        ids = store.limb_ids(obj) or []
        try:
            f1, f2, _ids = ops.ring_meta(obj)
        except ValueError as e:
            self.report({"ERROR"}, f"Delete Limb refused: the connections can't be read: {e}")
            return {"CANCELLED"}
        cn = [n for n, _b, _v in store.connections(obj)]
        why = boner.delete_limb_refusal(idx, ids, cn, f1, f2)
        if why:
            self.report({"ERROR"}, f"Delete Limb refused: {why}")
            return {"CANCELLED"}
        target = boner.default_patch_target(idx, ids) if self.target == "AUTO" else int(self.target)
        pl = store.patch_limb(obj)
        moving = int((pl == idx).sum()) if len(pl) else 0
        if target == idx:
            self.report({"ERROR"}, "Delete Limb refused: its patches can't go to the limb being deleted")
            return {"CANCELLED"}
        if moving and not 0 <= target < len(names):
            self.report({"ERROR"}, f"Delete Limb refused: its {moving} patches need another limb to go to")
            return {"CANCELLED"}
        t = _table_for_edit(self, obj)
        if t is None:
            return {"CANCELLED"}
        gone, gone_bones = t.rows[idx], t.bones[idx]
        pl = store.patch_limb(obj)
        if len(pl):
            pl = np.where(pl == idx, target, pl)
            pl = np.where(pl > idx, pl - 1, pl)
        rows = [r for i, r in enumerate(t.rows) if i != idx]
        store.set_limb_rows(obj, rows, pl if obj.get(store.KEY_PATCH_LIMB) is not None else None)
        s = getattr(context.scene, "bz_boner", None)
        if s is not None and s.active_limb >= len(rows):
            s.active_limb = max(len(rows) - 1, 0)
        _finish(obj)
        self.report({"INFO"}, f"Deleted limb '{gone.name}'"
                              + (f": {moving} patch{'es' if moving != 1 else ''} moved to '{names[target]}'" if moving else "")
                              + (f"; its bone{'s' if len(gone_bones) != 1 else ''} {', '.join(gone_bones[:4])}"
                                 f"{', ...' if len(gone_bones) > 4 else ''} are in no limb now (Validate lists them)"
                                 if gone_bones else ""))
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------------ limb bone lists
def rig_bone_name(obj, file_name) -> str:
    """The rig's current name of the skeleton bone ``file_name`` (by its source index), else the file name."""
    sk = source_skeleton(obj)
    i = boner.bone_lookup(sk.names, file_name)
    return store.bone_display_name(obj, i) if i is not None else str(file_name)


def stamp_clash(obj, name) -> list:
    """The OTHER rig bones carrying the same import stamp (``bz_src_index``) as the rig bone ``name``: a duplicated bone
    (Shift+D copies the stamp), which bone lists can't name (Export refuses a copy of a bone of the creature)."""
    arm = store.rig(obj)
    bone = arm.data.bones.get(str(name)) if arm is not None and name else None
    sid = bone.get("bz_src_index") if bone is not None else None
    if sid is None:
        return []
    return [b.name for b in arm.data.bones if b.name != bone.name and b.get("bz_src_index") is not None
            and int(b.get("bz_src_index")) == int(sid)]


def file_bone_name(obj, name, sk=None):
    """The name a limb bone list holds for a rig bone or file bone called ``name``: a source bone's FILE name (found by
    its source index first), an added bone's stamp name (``live_skeleton``), or None -- also for a rig bone whose import
    stamp another rig bone shares (:func:`stamp_clash`: which of the two is the file's bone is unknown)."""
    sk = live_skeleton(obj) if sk is None else sk
    if stamp_clash(obj, name):
        return None
    i = store.bone_index(obj, name)
    if 0 <= i < len(sk.names) and sk.names[i] != GONE:
        return sk.names[i]
    j = boner.bone_lookup(sk.names, name)                # ... else by name (an added bone under its stamp name)
    return sk.names[j] if j is not None and sk.names[j] != GONE else None


def not_a_bone_message(obj, name) -> str:
    """Why ``name`` can't be used as a skeleton bone here: a bone Add Stub Socket created (on the rig and written by the
    export, but it belongs only to its stub limb), an added bone (Add Bone, or a bone Blender added: the export puts it
    into its parent's limb) or simply not a bone of the skeleton."""
    arm = store.rig(obj)
    if arm is not None:
        from . import rig
        ok, bad = rig.created_stub_bones(arm)
        if any(b.name.lower() == str(name).lower() for b in ok) or any(n.lower() == str(name).lower() for n, _w in bad):
            return (f"'{name}' is a stub bone Add Stub Socket created: it belongs only to its stub limb (other limbs' "
                    "bone lists and connection hosts can name only the skeleton's own bones)")
        bone = next((b for b in arm.data.bones if b.name.lower() == str(name).lower()), None)
        sk = rig.rig_skeleton(arm) if bone is not None and bone.get(rig.KEY_BONE_UID) is None else None
        if bone is not None and (bone.get(rig.KEY_BONE_UID) is not None or (sk is not None and bone.name in sk.added_names)):
            # An added bone IS addressable now (fit-to-rig): it is only refused while the creature has not adopted it,
            # so the export has no owner key for it and no limb list can name it.
            return (f"'{bone.name}' is a bone Blender added that the creature has not adopted yet, so the export has no "
                    "name for it: Boner > Bones > Adopt Added Bones first (Add Bone stamps its own bones)")
    return f"'{name}' is not a bone of this creature's skeleton"


def _pin_added(obj, sk, moved) -> list:
    """Follow the pins after a bone-list edit: an ADDED bone the user moved INTO a limb is pinned there (it stays where
    the rows put it instead of following its parent, ``structure.added_limb_rows``), one moved out of every limb loses
    its pin. ``moved``: the edit's ``(bone, from row, to row)`` triples (``to`` -1 = no limb). Returns the pins written.
    Only added bones are ever pinned: a source bone's limb is the row's own business."""
    added = {str(a).lower() for a in (sk.added or ())}
    if not added:
        return store.limb_pins(obj)
    pins = {str(p).lower(): str(p) for p in store.limb_pins(obj)}
    for bone, _frm, to in moved or ():
        k = str(bone).lower()
        if k not in added:
            continue
        if int(to) >= 0:
            pins[k] = str(bone)
        else:
            pins.pop(k, None)
    out = list(pins.values())
    store.set_limb_pins(obj, out)
    return out


def apply_bone_edit(obj, t: LimbTable, edit) -> list:
    """Write a :class:`boner.BoneListEdit` into the limb rows (the caller has begun the journaled edit). A list equal to
    its source record's is stored as "the source's" (None), so an edit undone by its inverse leaves the import state.
    Returns the rows written."""
    src = source_limb_bones(obj)
    rows = list(t.rows)
    for j in edit.changed:
        r = rows[j]
        bones = tuple(edit.lists[j])
        if 0 <= r.src < len(src) and bones == tuple(src[r.src]):
            bones = None
        rows[j] = structure.LimbRow(r.src, r.name, r.limb_id, bones)
    store.set_limb_rows(obj, rows)
    return rows


def bone_edit_context(obj, t: LimbTable):
    """(torso connection hosts, per-limb owner counts, per-limb connection hosts) for the bone-list operators' refusals
    and warnings: host bones as file names (lower-cased keys, :func:`boner.torso_connection_hosts`), and per limb row
    the control points of its patches each bone owns in the live skin."""
    from ICParse import validate as ICval
    sk = live_skeleton(obj)                              # owner keys index it: an added bone's points count too
    try:
        f1, f2, _ids = ops.ring_meta(obj)
    except ValueError:
        f1 = f2 = []
    conns = []
    for i, (n, b, _ring) in enumerate(store.connections(obj)):
        fb = file_bone_name(obj, b) if b else None
        conns.append((n, fb or b, f1[i] if i < len(f1) else 0, f2[i] if i < len(f2) else 0))
    hosts = boner.torso_connection_hosts([(n, b, a) for n, b, a, _c in conns])
    limb_hosts = ICval.limb_connection_hosts([r.limb_id for r in t.rows], [(n, b, c) for n, b, _a, c in conns])
    try:
        owners = np.asarray(store.live_skin(obj).owners, dtype=np.int64)
    except ValueError:
        owners = np.asarray(store.vert_bone(obj), dtype=np.int64)
    pat = store.patches(obj)
    pl = store.patch_limb(obj)
    counts = []
    for j in range(len(t.rows)):
        sel = pat[pl == j] if len(pl) == len(pat) else pat[:0]
        pts = np.unique(sel.ravel()) if len(sel) else np.zeros(0, dtype=np.int64)
        pts = pts[(pts >= 0) & (pts < len(owners))]
        own = owners[pts] if len(pts) else np.zeros(0, dtype=np.int64)
        own = own[(own >= 0) & (own < len(sk.names))]
        bc = np.bincount(own, minlength=len(sk.names)) if len(own) else np.zeros(len(sk.names), dtype=np.int64)
        counts.append({sk.names[k]: int(c) for k, c in enumerate(bc) if c})
    return hosts, counts, limb_hosts


def _report_edit(op, obj, t, edit, text):
    """Store the move report and send it as INFO, or as a WARNING carrying :func:`boner.bone_edit_warnings`."""
    try:
        _hosts, counts, limb_hosts = bone_edit_context(obj, t)
        warns = boner.bone_edit_warnings(edit, t.bones, [r.limb_id for r in t.rows], [r.name for r in t.rows], counts,
                                         limb_hosts)
    except Exception as e:                              # a report must never fail an edit already committed
        warns = [f"(the combine check of this edit could not run: {type(e).__name__}: {e})"]
    obj["bz_last_bone_edit"] = edit.describe([r.name for r in t.rows])
    obj["bz_last_bone_warnings"] = list(warns)
    op.report({"WARNING"} if warns else {"INFO"}, text + ("; WARNING: " + "; ".join(warns) if warns else ""))


def _source_lists(obj, rows):
    src = source_limb_bones(obj)
    return [list(src[r.src]) if 0 <= r.src < len(src) else None for r in rows]


def select_rig_bones(arm, names) -> int:
    """Select exactly the rig bones called ``names`` (first = active); returns how many were found."""
    want = [str(n) for n in names]
    found = [n for n in want if arm.data.bones.get(n) is not None]
    sel = set(found)
    pose = getattr(arm, "pose", None)
    done = False
    if pose is not None and len(pose.bones) and hasattr(pose.bones[0], "select"):
        try:
            for pb in pose.bones:
                pb.select = pb.name in sel
            done = True
        except (AttributeError, TypeError):
            done = False
    if not done:
        for bb in arm.data.bones:
            bb.select = bb.name in sel
            try:
                bb.select_head = bb.select_tail = bb.name in sel
            except AttributeError:
                pass
    if found:
        arm.data.bones.active = arm.data.bones[found[0]]
    return len(found)


def selected_rig_bones(arm) -> list:
    """The names of the rig's selected bones (pose selection where Blender keeps it, else the bones')."""
    pose = getattr(arm, "pose", None)
    if pose is not None and len(pose.bones) and hasattr(pose.bones[0], "select"):
        return [pb.name for pb in pose.bones if pb.select]
    return [b.name for b in arm.data.bones if b.select]


def _bones_poll(cls, context) -> bool:
    if not _limbs_poll(cls, context):
        return False
    if store.rig(context.active_object) is None:
        cls.poll_message_set("the creature has no rig: limb bone lists name the rig's bones")
        return False
    return True


def _limb_for_edit(op, context, obj, index):
    """(index, table) for a bone-list edit of limb ``index`` (the active limb when -1), or None after an ERROR report.
    Reads the table without starting a journal generation (refusals change nothing)."""
    idx = active_limb(context, obj, index)
    t = limb_table(obj)
    if t.error:
        op.report({"ERROR"}, t.error)
        return None
    if not 0 <= idx < len(t.rows):
        op.report({"ERROR"}, f"no limb {idx}: the creature has {len(t.rows)} limbs (pick one in Boner > Limbs)")
        return None
    return idx, t


def _commit(op, obj, t, edit, verb, sk=None) -> bool:
    if not ops._begin(op, obj):
        return False
    t2 = limb_table(obj)                                # begin_edit may have re-synced: re-read before writing
    if t2.error or t2.rows != t.rows:
        op.report({"ERROR"}, f"{verb} refused: the limb table changed while it ran (Undo / Redo?): try again")
        return False
    apply_bone_edit(obj, t2, edit)
    if sk is not None:
        _pin_added(obj, sk, edit.moved)                 # an added bone the user moved stays where the rows put it
    _finish(obj)
    return True


class BZ_OT_limb_select_bones(bpy.types.Operator):
    """Select the active limb's bones on the rig as the Limbs panel lists them -- added bones where Export puts them (the
    first becomes the rig's active bone and the Boner panel's active bone). The creature's data does not change"""
    bl_idname = "bz.limb_select_bones"
    bl_label = "Select Limb Bones"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        got = _limb_for_edit(self, context, obj, self.index)
        if got is None:
            return {"CANCELLED"}
        idx, t = got
        placed = limb_table_cached(obj)                 # the lists the panel shows: added bones where Export puts them
        if placed.error or placed.rows != t.rows:
            self.report({"ERROR"}, placed.error or "the limb table changed while it was read (Undo / Redo?): try again")
            return {"CANCELLED"}
        arm = store.rig(obj)
        names = [rig_bone_name(obj, b) for b in placed.bones[idx]]
        n = select_rig_bones(arm, names)
        missing = [b for b, rn in zip(placed.bones[idx], names) if arm.data.bones.get(rn) is None]
        s = getattr(context.scene, "bz_boner", None)
        if s is not None and n:
            s.bone = next(rn for rn in names if arm.data.bones.get(rn) is not None)
        notes = list(placed.warnings)                   # e.g. the placement can't be worked out: the stored lists
        self.report({"WARNING"} if missing or notes else {"INFO"},
                    f"Selected {n} bone{'s' if n != 1 else ''} of '{t.rows[idx].name}'"
                    + (f"; not on the rig: {', '.join(missing)}" if missing else "")
                    + (f"; {'; '.join(notes)}" if notes else ""))
        return {"FINISHED"}


_ROOT_ITEMS: list = []


def _root_items(self, context):
    """Set Root Bone's list: every bone the export writes (``live_skeleton``: the source's by file name and the bones
    C8 added by their stamp name; the label shows the rig's name when it differs)."""
    global _ROOT_ITEMS
    obj = context.active_object if context else None
    items = []
    if store.is_native(obj) and store.rig(obj) is not None:
        try:
            sk = live_skeleton(obj)
            rig_names = store.bone_rig_names(obj)
            for i, n in enumerate(sk.names):
                if n == GONE:
                    continue                             # deleted on the rig: the export does not write it
                rn = rig_names[i] if i < len(rig_names) else n
                items.append((n, n if rn == n else f"{rn} ({n})", f"'{n}' and its children become the limb's bones"))
        except Exception:
            items = []
    _ROOT_ITEMS = items or [("", "(no skeleton)", "")]
    return _ROOT_ITEMS


class BZ_OT_limb_set_root(bpy.types.Operator):
    """Set the active limb's root bone: the limb takes that bone and its children, except branches another limb (not
    the Torso) owns and other classes' standard stub bones. Bones it takes leave the limb that had them; bones it no
    longer covers go back to the Torso. The report lists every bone that moved"""
    bl_idname = "bz.limb_set_root"
    bl_label = "Set Root Bone"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "root"

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    root: bpy.props.EnumProperty(name="Root Bone", items=_root_items)

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        obj = context.active_object
        got = _limb_for_edit(self, context, obj, self.index)
        if got is None:
            return {"CANCELLED"}
        idx, t = got
        sk = live_skeleton(obj)
        if sk.note:
            self.report({"WARNING"}, sk.note)
        try:
            edit = boner.set_root_edit(idx, self.root, sk.names, sk.parents, t.bones, [r.limb_id for r in t.rows],
                                       [r.name for r in t.rows], _source_lists(obj, t.rows),
                                       hosts=bone_edit_context(obj, t)[0])
        except ValueError as e:
            self.report({"ERROR"}, f"Set Root Bone refused: {e}")
            return {"CANCELLED"}
        name = t.rows[idx].name
        if not edit.changed:
            self.report({"INFO"}, f"'{name}' already is '{self.root}' and its children ({len(t.bones[idx])} bones): "
                                  "nothing changed")
            return {"CANCELLED"}
        if not _commit(self, obj, t, edit, "Set Root Bone", sk):
            return {"CANCELLED"}
        n = len(edit.lists[idx])
        _report_edit(self, obj, t, edit, f"'{name}' root '{self.root}': {n} bone{'s' if n != 1 else ''}; "
                     + edit.describe([r.name for r in t.rows]))
        return {"FINISHED"}


def _bone_arg(op, context, obj, sk=None):
    """The bone an Add / Remove works on: the operator's ``bone`` when given, else the Boner panel's active bone; as the
    name a limb list holds for it (a source bone's file name, an added bone's stamp name: :func:`file_bone_name` against
    ``sk``, the live skeleton), or None after an ERROR report."""
    name = op.bone
    if not name:
        s = getattr(context.scene, "bz_boner", None)
        name = s.bone if s is not None else ""
    if not name:
        op.report({"ERROR"}, "pick a bone first (Boner > Limbs: the Bone field, or Boner > Bones' eyedropper)")
        return None
    clash = stamp_clash(obj, name)
    if clash:
        op.report({"ERROR"}, f"'{name}' and {', '.join(repr(c) for c in clash)} are copies of one imported bone (a "
                             "duplicated bone): skeleton changes are pending, and a limb's bone list can only name the "
                             "file's bones (Export refuses a copy of a bone of the creature); delete the copy first")
        return None
    fn = file_bone_name(obj, name, sk)
    if fn is None:
        op.report({"ERROR"}, not_a_bone_message(obj, name))
    return fn


class BZ_OT_limb_add_bone(bpy.types.Operator):
    """Add the active bone to the active limb (it leaves any other limb that had it). Refused when it is another limb's
    only bone"""
    bl_idname = "bz.limb_add_bone"
    bl_label = "Add Active Bone"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    bone: bpy.props.StringProperty(name="Bone", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        got = _limb_for_edit(self, context, obj, self.index)
        if got is None:
            return {"CANCELLED"}
        idx, t = got
        sk = live_skeleton(obj)
        if sk.note:
            self.report({"WARNING"}, sk.note)
        bone = _bone_arg(self, context, obj, sk)
        if bone is None:
            return {"CANCELLED"}
        try:
            edit = boner.add_bone_edit(idx, bone, sk.names, t.bones, [r.limb_id for r in t.rows],
                                       [r.name for r in t.rows], _source_lists(obj, t.rows),
                                       hosts=bone_edit_context(obj, t)[0])
        except ValueError as e:
            self.report({"ERROR"}, f"Add Active Bone refused: {e}")
            return {"CANCELLED"}
        if not _commit(self, obj, t, edit, "Add Active Bone", sk):
            return {"CANCELLED"}
        _report_edit(self, obj, t, edit, edit.describe([r.name for r in t.rows]))
        return {"FINISHED"}


class BZ_OT_limb_remove_bone(bpy.types.Operator):
    """Remove the active bone from the active limb: it is in no limb afterwards (Validate lists it; Add Active Bone on
    another limb gives it a home). Refused for the limb's only bone"""
    bl_idname = "bz.limb_remove_bone"
    bl_label = "Remove Active Bone"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    bone: bpy.props.StringProperty(name="Bone", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _bones_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        got = _limb_for_edit(self, context, obj, self.index)
        if got is None:
            return {"CANCELLED"}
        idx, t = got
        sk = live_skeleton(obj)
        if sk.note:
            self.report({"WARNING"}, sk.note)
        bone = _bone_arg(self, context, obj, sk)
        if bone is None:
            return {"CANCELLED"}
        try:
            edit = boner.remove_bone_edit(idx, bone, t.bones, [r.name for r in t.rows], [r.limb_id for r in t.rows],
                                          bone_edit_context(obj, t)[0])
        except ValueError as e:
            self.report({"ERROR"}, f"Remove Active Bone refused: {e}")
            return {"CANCELLED"}
        if not _commit(self, obj, t, edit, "Remove Active Bone", sk):
            return {"CANCELLED"}
        if bone in sk.added:                            # an added bone always has a limb: its parent's
            self.report({"WARNING"}, f"'{bone}' is a bone added on the rig, so Export puts it back in its parent's "
                                     "limb (an added bone follows its parent unless a limb operator pins it): "
                                     "re-parent it, or add it to the limb it belongs in")
        _report_edit(self, obj, t, edit, edit.describe([r.name for r in t.rows]) + " (Validate lists bones in no limb)")
        return {"FINISHED"}


CLASSES = (BZ_OT_limb_set_active, BZ_OT_rename_limb, BZ_OT_limb_flag, BZ_OT_add_standard_limb, BZ_OT_delete_limb,
           BZ_OT_limb_select_bones, BZ_OT_limb_set_root, BZ_OT_limb_add_bone, BZ_OT_limb_remove_bone)
