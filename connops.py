"""Boner > Connections: the connection table's operators (Create for a limb, Rename, Delete, Parent / Child
classification boxes, host bone) and its read-out.

Every operator reads the rows (``store.conn_rows``), refuses before it starts a journal generation, then writes the
whole table through ``store.set_conn_rows`` after ``store.begin_edit``, so edit-mode Undo / Redo restore it and Export
writes it (``structure.apply_connections``: an unchanged row keeps the source record's bytes). The rules -- the name
rule, the default host, the classification boxes, NoSwap staying on the limb -- are bpy-free in ``boner``; the ring is
picked with Pick Ring / Set Connection Ring (``ops``); the Object Editor's connection warnings are
``ICParse.validate.check_oe_structure`` (Validate's ``WARN oe:`` lines).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import bpy

from . import boner
from . import core
from . import draw
from . import limbops
from . import ops
from . import store
from . import enumitems


# ------------------------------------------------------------------------------------------------ read-out
@dataclass
class ConnTable:
    rows: list = field(default_factory=list)          # [core.RingEdit]
    limbs: object = None                              # limbops.LimbTable
    status: list = field(default_factory=list)        # boner.conn_status per row
    child_limb: list = field(default_factory=list)    # the name of the limb each row attaches ("" = none)
    error: str = ""


def conn_table(obj) -> ConnTable:
    """The Connections list: rows (with classes), the limb table they refer to, each row's status word and child limb;
    ``error`` says why there is none."""
    t = limbops.limb_table(obj)
    if t.error:
        return ConnTable(error=t.error)
    try:
        rows = store.conn_rows(obj)
    except ValueError as e:
        return ConnTable(error=str(e))
    if rows is None:
        return ConnTable(limbs=t, error="this creature has no connection table (import could not set it up): its "
                                        "source's connections are exported unchanged; re-import it to edit them")
    if any(r.flags1 is None or r.flags2 is None for r in rows):
        try:
            f1, f2, _ids = ops.ring_meta(obj)
        except ValueError as e:
            return ConnTable(error=str(e))
        rows = [core.RingEdit(index=i, bone=r.bone, corners=r.corners, raw=r.raw, src=r.src, name=r.name,
                              flags1=f1[i], flags2=f2[i]) for i, r in enumerate(rows)]
    try:
        names = limbops.source_skeleton(obj).names
    except Exception as e:
        return ConnTable(error=f"the source skeleton can't be read: {type(e).__name__}: {e}")
    ids = [r.limb_id for r in t.rows]
    brief = [(r.name, r.bone, len(r.corners if r.raw is None else r.raw), r.flags1, r.flags2) for r in rows]
    status = [boner.conn_status(i, brief, ids, t.bones, names) for i in range(len(rows))]
    child = [next((lr.name for lr in t.rows if boner.class_of(lr.limb_id) == boner.class_of(r.flags2)), "")
             for r in rows]
    return ConnTable(rows=rows, limbs=t, status=status, child_limb=child)


_TABLE_CACHE: dict = {}


def conn_table_cached(obj) -> ConnTable:
    """:func:`conn_table` for panel draw: recomputed when the generation, the limb table or the connection table
    changes."""
    keys = (store.KEY_LIMB_NAMES, store.KEY_LIMB_IDS, store.KEY_LIMB_BONE_COUNTS, store.KEY_LIMB_BONES,
            store.KEY_CONN_NAMES, store.KEY_CONN_BONES, store.KEY_CONN_COUNTS, store.KEY_CONN_FLAGS1,
            store.KEY_CONN_FLAGS2, store.KEY_CONN_SRC)
    key = (obj.get(store.KEY_GEN), store.mesh_generation(obj)) + tuple(tuple(obj.get(k, [])) for k in keys)
    hit = _TABLE_CACHE.get(obj.name)
    if hit is None or hit[0] != key:
        hit = (key, conn_table(obj))
        _TABLE_CACHE[obj.name] = hit
    return hit[1]


def forget_table(obj=None) -> None:
    if obj is None:
        _TABLE_CACHE.clear()
    else:
        _TABLE_CACHE.pop(obj.name, None)


def active_conn(context, obj, index=-1) -> int:
    if index >= 0:
        return int(index)
    s = getattr(context.scene, "bz_boner", None)
    return int(s.active_conn) if s is not None else -1


def _set_active(context, obj, index, rows=None) -> None:
    s = getattr(context.scene, "bz_boner", None)
    if s is None:
        return
    s.active_conn = max(int(index), 0)
    rows = rows if rows is not None else (store.conn_rows(obj) or [])
    if 0 <= index < len(rows):
        s.conn_bone = limbops.rig_bone_name(obj, rows[index].bone) if rows[index].bone else ""


def _poll(cls, context) -> bool:
    obj = context.active_object
    if not ops._guard_poll(cls, context) or obj.get("bz_scene"):
        return False
    if obj.get(store.KEY_LIMB_NAMES) is None or obj.get(store.KEY_CONN_COUNTS) is None:
        cls.poll_message_set("this creature has no limb / connection table (import could not set it up): re-import it")
        return False
    return True


def _read(op, obj):
    """The connection table for an edit (None after an ERROR report). Read-only: refusals change nothing."""
    t = conn_table(obj)
    if t.error:
        op.report({"ERROR"}, t.error)
        return None
    return t


def _commit(op, obj, t, rows, verb) -> bool:
    """Start the journaled edit, check nothing moved underneath (Undo / Redo), write the rows."""
    if not ops._begin(op, obj):
        return False
    t2 = conn_table(obj)
    same = lambda a, b: [(r.source(), r.name, r.bone, r.flags1, r.flags2, r.corners, r.raw) for r in a] == \
        [(r.source(), r.name, r.bone, r.flags1, r.flags2, r.corners, r.raw) for r in b]
    if t2.error or not same(t2.rows, t.rows) or [r.limb_id for r in t2.limbs.rows] != [r.limb_id for r in t.limbs.rows]:
        op.report({"ERROR"}, f"{verb} refused: the connection table changed while it ran (Undo / Redo?): try again")
        return False
    store.set_conn_rows(obj, rows)
    _finish(obj)
    return True


def _finish(obj):
    forget_table(obj)
    limbops.forget_table(obj)
    ops.forget_ring_status(obj)
    draw.invalidate(obj.name)


def _row(r, **kw):
    d = dict(index=r.index, bone=r.bone, corners=r.corners, raw=r.raw, src=r.src, name=r.name, flags1=r.flags1,
             flags2=r.flags2)
    d.update(kw)
    return core.RingEdit(**d)


def _index(op, context, obj, t, index):
    idx = active_conn(context, obj, index)
    if not 0 <= idx < len(t.rows):
        op.report({"ERROR"}, f"no connection {idx}: the creature has {len(t.rows)} connections (pick one in Boner > "
                             "Connections)")
        return None
    return idx


def _parent_bones(t, parent_class):
    """The bone lists of the limbs of ``parent_class`` (the host bone must be in them)."""
    return [b for r, b in zip(t.limbs.rows, t.limbs.bones) if boner.class_of(r.limb_id) == boner.class_of(parent_class)]


def default_host_for(obj, t, child_class, parent_class, child_bones=None):
    """(host, how) by :func:`boner.default_host` for a connection of ``child_class`` cut into ``parent_class``."""
    sk = limbops.source_skeleton(obj)
    if child_bones is None:
        child_bones = next((b for r, b in zip(t.limbs.rows, t.limbs.bones)
                            if boner.class_of(r.limb_id) == boner.class_of(child_class)), [])
    pb = _parent_bones(t, parent_class)
    if not pb:
        return None, f"no limb has the parent class ({boner.class_label(parent_class)})"
    return boner.default_host(child_class, child_bones, pb[0], sk.names, sk.parents)


# ------------------------------------------------------------------------------------------------ operators
class BZ_OT_conn_set_active(bpy.types.Operator):
    """Make this the active connection"""
    bl_idname = "bz.conn_set_active"
    bl_label = "Set Active Connection"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty(default=0, min=0)

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def execute(self, context):
        try:
            _set_active(context, context.active_object, self.index)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


_LIMB_ITEMS: list = []


@enumitems.stable
def _limb_items(self, context):
    """Create Connection's menu: the non-torso limbs no connection attaches (kept referenced for Blender)."""
    global _LIMB_ITEMS
    obj = context.active_object if context else None
    items = []
    if store.is_native(obj):
        ids = store.limb_ids(obj) or []
        names = store.limb_names(obj)
        try:
            f1, f2, _ids = ops.ring_meta(obj)
        except ValueError:
            f2 = []
        for i in boner.limbs_without_connection(ids, f2):
            items.append((str(i), f"{names[i]} ({boner.class_label(ids[i])})",
                          f"A connection for '{names[i]}': Child class 0x{boner.class_of(ids[i]):x}, a "
                          f"{boner.ring_corner_count(ids[i])}-corner ring"))
    _LIMB_ITEMS = items or [("NONE", "(every limb has a connection)", "")]
    return _LIMB_ITEMS


class BZ_OT_create_connection(bpy.types.Operator):
    """Create a limb's connection and start picking its ring"""
    bl_idname = "bz.create_connection"
    bl_label = "Create Connection"
    bl_options = {"REGISTER", "UNDO"}

    limb: bpy.props.EnumProperty(name="Limb", items=_limb_items)
    parent: bpy.props.EnumProperty(name="Cut Into", items=[
        ("AUTO", "Auto", "Torso if a Torso bone can host the ring, else Head"),
        ("TORSO", "Torso", "A socket in the torso (the usual place for a limb's connection)"),
        ("HEAD", "Head", "A ring in the head, as antlers use")], default="AUTO")
    pick: bpy.props.BoolProperty(name="Pick Ring", default=True, description="Start Pick Ring for it (Edit Mode, "
                                 "3D Viewport)")

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        res = self.execute(context)
        obj = context.active_object
        if res == {"FINISHED"} and self.pick and obj is not None and obj.mode == "EDIT" and not bpy.app.background:
            ci = len(store.connections(obj)) - 1
            try:
                bpy.ops.bz.pick_ring("INVOKE_DEFAULT", conn=str(ci))
            except RuntimeError as e:
                self.report({"WARNING"}, f"Pick Ring did not start ({e}): run it from the connection's row")
        return res

    def execute(self, context):
        obj = context.active_object
        if self.limb in ("", "NONE"):
            self.report({"WARNING"}, "every limb already has a connection")
            return {"CANCELLED"}
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        idx = int(self.limb)
        if not 0 <= idx < len(t.limbs.rows):
            self.report({"ERROR"}, f"no limb {idx}: the creature has {len(t.limbs.rows)} limbs")
            return {"CANCELLED"}
        lim = t.limbs.rows[idx]
        cls = boner.class_of(lim.limb_id)
        if boner.is_torso(lim.limb_id) or not cls:
            self.report({"ERROR"}, f"Create Connection refused: '{lim.name}' is "
                                   + ("the Torso, which rings are cut into" if cls else "unclassified (Boner > Limbs)"))
            return {"CANCELLED"}
        have = next((r.name for r in t.rows if boner.class_of(r.flags2) == cls), None)
        if have is not None:
            self.report({"ERROR"}, f"Create Connection refused: connection '{have}' already attaches "
                                   f"{boner.class_label(cls)} limbs")
            return {"CANCELLED"}
        sk = limbops.source_skeleton(obj)
        auto = boner.default_parent(cls, t.limbs.bones[idx], sk.names, sk.parents, [r.limb_id for r in t.limbs.rows],
                                    t.limbs.bones)
        parent = {"HEAD": boner.PARENT_HEAD, "TORSO": boner.PARENT_TORSO}.get(self.parent, auto)
        host, how = default_host_for(obj, t, cls, parent, t.limbs.bones[idx])
        if host is None:
            other = boner.PARENT_HEAD if parent == boner.PARENT_TORSO else boner.PARENT_TORSO
            alt = default_host_for(obj, t, cls, other, t.limbs.bones[idx])[0]
            hint = (f"Cut Into: {boner.class_label(other)} would host it on '{alt}'" if alt is not None else
                    f"give the limb a root bone under a {boner.class_label(parent)} bone first, Boner > Limbs")
            self.report({"ERROR"}, f"Create Connection refused: {how} ({hint})")
            return {"CANCELLED"}
        name = boner.default_conn_name([r.name for r in t.rows], lim.name)
        why = boner.limb_name_error(name, [r.name for r in t.rows], "connection")
        if why:
            self.report({"ERROR"}, f"Create Connection refused: {why}")
            return {"CANCELLED"}
        new = core.RingEdit(index=len(t.rows), bone=host, corners=[], src=-1, name=name, flags1=parent, flags2=cls)
        rows = list(t.rows) + [new]
        if not _commit(self, obj, t, rows, "Create Connection"):
            return {"CANCELLED"}
        _set_active(context, obj, len(rows) - 1, rows)
        n = boner.ring_corner_count(cls)
        self.report({"INFO"}, f"Created connection '{name}' for limb '{lim.name}' (Parent {boner.class_label(parent)}, "
                              f"Child {boner.class_label(cls)}, bone {host}). Next, pick its {n} ring corners with Pick Ring in Edit Mode")
        return {"FINISHED"}


class BZ_OT_rename_connection(bpy.types.Operator):
    """Rename the active connection"""
    bl_idname = "bz.rename_connection"
    bl_label = "Rename Connection"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(name="Name", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        names = [n for n, _b, _v in store.connections(obj)]
        idx = active_conn(context, obj, self.index)
        if not 0 <= idx < len(names):
            self.report({"ERROR"}, f"no connection {idx}: pick one in Boner > Connections")
            return {"CANCELLED"}
        self.index = idx
        if not self.name:
            self.name = names[idx]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        idx = _index(self, context, obj, t, self.index)
        if idx is None:
            return {"CANCELLED"}
        why = boner.limb_name_error(self.name, [r.name for i, r in enumerate(t.rows) if i != idx], "connection")
        if why:
            self.report({"ERROR"}, f"Rename Connection refused: {why}")
            return {"CANCELLED"}
        old = t.rows[idx].name
        if self.name == old:
            return {"CANCELLED"}
        rows = list(t.rows)
        rows[idx] = _row(rows[idx], name=str(self.name))
        if not _commit(self, obj, t, rows, "Rename Connection"):
            return {"CANCELLED"}
        self.report({"INFO"}, f"Renamed connection '{old}' to '{self.name}'")
        return {"FINISHED"}


class BZ_OT_delete_connection(bpy.types.Operator):
    """Delete the active connection; its limb can't combine until it has a new one"""
    bl_idname = "bz.delete_connection"
    bl_label = "Delete Connection"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        self.index = active_conn(context, obj, self.index)
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        obj = context.active_object
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        idx = _index(self, context, obj, t, self.index)
        if idx is None:
            return {"CANCELLED"}
        gone = t.rows[idx]
        rows = [r for i, r in enumerate(t.rows) if i != idx]
        if not _commit(self, obj, t, rows, "Delete Connection"):
            return {"CANCELLED"}
        s = getattr(context.scene, "bz_boner", None)
        if s is not None and s.active_conn >= len(rows):
            s.active_conn = max(len(rows) - 1, 0)
        cls = boner.class_of(gone.flags2)
        lost = [r.name for r in t.limbs.rows if boner.class_of(r.limb_id) == cls and cls] \
            if not any(boner.class_of(r.flags2) == cls for r in rows) else []
        text = f"Deleted connection '{gone.name}'"
        ids = [r.limb_id for r in t.limbs.rows]
        warns = boner.socket_warnings(ids, ids, [r.flags2 for r in t.rows], [r.flags2 for r in rows],
                                      [r.name for r in t.limbs.rows])
        if warns:
            self.report({"WARNING"}, f"{text}: " + "; ".join(warns))
        else:
            self.report({"INFO"}, text)
        obj["bz_last_conn_delete"] = lost[0] if lost else ""
        obj["bz_last_socket_warning"] = "; ".join(warns)
        return {"FINISHED"}


class BZ_OT_conn_flag(bpy.types.Operator):
    """Tick or untick a class box of the active connection's parent or child"""
    bl_idname = "bz.conn_flag"
    bl_label = "Connection Classification"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    target: bpy.props.EnumProperty(name="Edit", items=[("PARENT", "Parent", "flags1: the limb the ring is cut into"),
                                                       ("CHILD", "Child", "flags2: the limb attached through it")],
                                   default="CHILD")
    bit: bpy.props.IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    @classmethod
    def description(cls, context, props):
        f = next((f for f in boner.CONN_FLAGS if f.bit == props.bit), None)
        if f is None:
            return cls.__doc__
        return f"{f.label} (0x{f.bit:x}) of the connection's {'Parent' if props.target == 'PARENT' else 'Child'} class"

    def execute(self, context):
        obj = context.active_object
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        idx = _index(self, context, obj, t, self.index)
        if idx is None:
            return {"CANCELLED"}
        r = t.rows[idx]
        word = "flags1" if self.target == "PARENT" else "flags2"
        old = int(getattr(r, word))
        try:
            new = boner.toggle_conn_flag(old, self.bit)
        except ValueError as e:
            self.report({"ERROR"}, f"Connection Classification refused: {e}")
            return {"CANCELLED"}
        rows = list(t.rows)
        rows[idx] = _row(r, **{word: new})
        if not _commit(self, obj, t, rows, "Connection Classification"):
            return {"CANCELLED"}
        role = "Parent" if word == "flags1" else "Child"
        self.report({"INFO"}, f"'{r.name}' {role}: 0x{old:x} -> 0x{new:x} ({boner.class_label(new)})")
        classes = {boner.class_of(x.limb_id) for x in t.limbs.rows}
        cls = boner.class_of(new)
        warns = []
        if cls not in classes:
            warns.append(f"no limb has the {role} class {boner.class_label(new)} (the combiner "
                         "needs it)")
        if word == "flags2" and any(boner.class_of(x.flags2) == cls for j, x in enumerate(rows) if j != idx):
            warns.append("another connection already attaches that class")
        socket = []
        if word == "flags2":
            ids = [x.limb_id for x in t.limbs.rows]
            socket = boner.socket_warnings(ids, ids, [x.flags2 for x in t.rows], [x.flags2 for x in rows],
                                           [x.name for x in t.limbs.rows])
            warns += socket
        obj["bz_last_socket_warning"] = "; ".join(socket)
        if word == "flags1" and cls in classes:
            sk = limbops.source_skeleton(obj)
            why = boner.host_refusal(r.bone, new, sk.names, [x.limb_id for x in t.limbs.rows], t.limbs.bones)
            if why:
                warns.append(f"its host bone: {why} (Host Bone > Default)")
        if warns:
            self.report({"WARNING"}, f"'{r.name}': " + "; ".join(warns))
        return {"FINISHED"}


class BZ_OT_conn_set_host(bpy.types.Operator):
    """Set the bone the active connection attaches to"""
    bl_idname = "bz.conn_set_host"
    bl_label = "Set Host Bone"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    bone: bpy.props.StringProperty(name="Bone", default="", options={"SKIP_SAVE"})
    default: bpy.props.BoolProperty(name="Default", default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        idx = _index(self, context, obj, t, self.index)
        if idx is None:
            return {"CANCELLED"}
        r = t.rows[idx]
        sk = limbops.source_skeleton(obj)
        ids = [x.limb_id for x in t.limbs.rows]
        if self.default:
            host, how = default_host_for(obj, t, r.flags2, r.flags1)
            if host is None:
                self.report({"ERROR"}, f"Set Host Bone refused: {how}")
                return {"CANCELLED"}
        else:
            name = self.bone
            if not name:
                s = getattr(context.scene, "bz_boner", None)
                name = s.conn_bone if s is not None else ""
            if not name:
                self.report({"ERROR"}, "Set Host Bone refused: pick a bone first (the Host Bone field)")
                return {"CANCELLED"}
            fn = limbops.file_bone_name(obj, name)
            if fn is None:
                self.report({"ERROR"}, f"Set Host Bone refused: {limbops.not_a_bone_message(obj, name)}")
                return {"CANCELLED"}
            why = boner.host_refusal(fn, r.flags1, sk.names, ids, t.limbs.bones)
            if why:
                self.report({"ERROR"}, f"Set Host Bone refused: {why}")
                return {"CANCELLED"}
            pb = _parent_bones(t, r.flags1)
            host = next((str(b) for b in (pb[0] if pb else []) if str(b).lower() == fn.lower()), fn)
            how = "picked"
        if host == r.bone:
            self.report({"INFO"}, f"'{r.name}' already attaches to '{host}'")
            _set_active(context, obj, idx, t.rows)
            return {"CANCELLED"}
        rows = list(t.rows)
        rows[idx] = _row(r, bone=host)
        if not _commit(self, obj, t, rows, "Set Host Bone"):
            return {"CANCELLED"}
        _set_active(context, obj, idx, rows)
        self.report({"INFO"}, f"Connection '{r.name}' host bone: {r.bone or '-'} -> {host} ({how})")
        return {"FINISHED"}


# --------------------------------------------------------------------------------------- Optimise Ring Start
def ring_start_plans(obj, context, rows, indices, margin=None, gain=None):
    """``[(row index, combocore.RingStartPlan)]`` for the connection ``indices`` of ``obj``: each torso socket ring's
    start corner tried in every rotation against the stock partners that really donate that part (the partner set
    ``combotest.validate.precheck`` measures against; when none of them has the limb -- top wings -- the wider set
    Pick Ring aligns to, said in the plan's reason). Rings with no ring, no combiner slot, a verbatim encoding or a
    Parent class other than the Torso are left out: nothing welds at them."""
    import numpy as np
    from . import combocore
    out = []
    world = core.blender_to_sgm(ops._live_blender_verts(obj))
    _f1, _f2, ids = ops.ring_meta(obj)
    tc = combocore.torso_centre(world, store.patches(obj), store.patch_limb(obj), ids) if combocore.available() else None
    stem = os.path.splitext(os.path.basename(store.source_path(obj)))[0]
    stock = ops._stock_dir(context) if combocore.available() else ""
    for i in indices:
        r = rows[i]
        key = core.ring_slot(r.flags2)[0]
        corners = list(r.corners or [])
        if not combocore.available():
            out.append((i, combocore.RingStartPlan(slot_key=key, corners=corners)))
            continue
        if r.raw is not None:
            plan = combocore.RingStartPlan(slot_key=key, corners=[int(v) for v in r.raw])
            plan.reason = "its ring is kept verbatim (a non-standard encoding): re-pick it (Pick Ring) first"
            out.append((i, plan))
            continue
        if int(r.flags1) & core.CLASS_MASK != core.TORSO_CODE:
            plan = combocore.RingStartPlan(slot_key=key, corners=corners)
            plan.reason = (f"its Parent class is {boner.class_label(r.flags1)}, not the Torso: the combiner grafts only "
                           "at torso sockets, so nothing welds at this ring")
            out.append((i, plan))
            continue
        if any(not 0 <= v < len(world) for v in corners):
            plan = combocore.RingStartPlan(slot_key=key, corners=corners)
            plan.reason = "a ring index is outside the control-point pool: re-pick it (Pick Ring)"
            out.append((i, plan))
            continue
        dirs, names = combocore.partner_ring_dirs(stock, key, exclude_stem=stem, donors_only=True)
        wide = False
        if not [d for d in dirs if len(d) == len(corners)]:
            dirs, names = combocore.partner_ring_dirs(stock, key, exclude_stem=stem)
            wide = True
        plan = combocore.ring_start_plan(np.asarray(world)[corners], corners, key, tc, dirs, names,
                                         margin=margin, **({} if gain is None else {"gain": gain}))
        if wide and plan.n:
            plan.reason += (f" [no stock creature donates a {key}, so the comparison is against the {plan.n} stock "
                            "rings of that slot, stub sockets included]")
        out.append((i, plan))
    return out


class BZ_OT_optimise_ring_start(bpy.types.Operator):
    """Rotate a ring's start corner to the one that welds best with stock creatures"""
    bl_idname = "bz.optimise_ring_start"
    bl_label = "Optimise Ring Start"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(default=-1, options={"SKIP_SAVE"})
    scope: bpy.props.EnumProperty(name="Rings", items=[
        ("ACTIVE", "Active Ring", "Only the active connection's ring"),
        ("ALL", "All Rings", "Every torso socket ring of the creature")], default="ACTIVE")
    margin: bpy.props.FloatProperty(
        name="Margin", default=20.0, min=0.0, max=180.0,
        description="A partner counts as mis-welded when its weld is this much worse than the best")
    gain: bpy.props.FloatProperty(
        name="Minimum Gain", default=1.0, min=0.0, max=180.0,
        description="Rotate a ring only when the better start gains at least this angle")

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        from . import combocore
        obj = context.active_object
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        if not combocore.available():
            self.report({"ERROR"}, f"Optimise Ring Start refused: the creature combiner could not be loaded "
                                   f"({combocore.IMPORT_ERROR}): the weld cannot be measured")
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, f"Optimise Ring Start refused: {err}")
            return {"CANCELLED"}
        if self.scope == "ALL":
            indices = list(range(len(t.rows)))
        else:
            idx = _index(self, context, obj, t, self.index)
            if idx is None:
                return {"CANCELLED"}
            indices = [idx]
        try:
            plans = ring_start_plans(obj, context, t.rows, indices, margin=float(self.margin), gain=float(self.gain))
        except ValueError as e:
            self.report({"ERROR"}, f"Optimise Ring Start refused: {e}")
            return {"CANCELLED"}
        done = [(i, p) for i, p in plans if p.ok]
        lines = [f"{t.rows[i].name}: {p.reason}" for i, p in plans]
        obj["bz_last_ring_start"] = "; ".join(lines)
        if not done:
            judged = [p for _i, p in plans if p.scores]      # the rotation search ran (not a refusal before it)
            if len(judged) != len(plans):
                head = ("this ring's start is not something the combiner welds by" if self.scope != "ALL" else
                        f"{len(plans) - len(judged)} of the {len(plans)} rings cannot be judged at all")
            else:
                head = ("the active ring is already started where the stock creatures start theirs"
                        if self.scope != "ALL" else
                        f"every one of the {len(plans)} rings is already started where the stock creatures start theirs")
            summary = f"Nothing to optimise: {head} -- " + "; ".join(lines)
            obj["bz_last_ring_start"] = summary
            self.report({"WARNING"}, summary)
            return {"CANCELLED"}
        rows = list(t.rows)
        for i, p in done:
            rows[i] = _row(rows[i], corners=list(p.corners))
        if not _commit(self, obj, t, rows, "Optimise Ring Start"):
            return {"CANCELLED"}
        _set_active(context, obj, done[0][0], rows)
        text = f"Optimised {len(done)} of {len(plans)} ring(s): " + "; ".join(f"{t.rows[i].name}: {p.reason}"
                                                                             for i, p in done)
        obj["bz_last_ring_start"] = "; ".join(lines)
        self.report({"INFO"}, text)
        stuck = [t.rows[i].name for i, p in done if p.over_after]
        if stuck:
            self.report({"WARNING"}, f"still over the {float(self.margin):.0f} deg margin after the rotation: "
                                     f"{', '.join(stuck)} (the ring's plane, not its start: see the report)")
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------------ Add Stub Socket
_STUB_ITEMS: list = []


@enumitems.stable
def _stub_items(self, context):
    """Add Stub Socket's menu: the stub-able classes (legs, arms, side / top wings) no limb and no connection has (kept
    referenced for Blender's dynamic enum strings)."""
    global _STUB_ITEMS
    obj = context.active_object if context else None
    items = []
    if store.is_native(obj):
        ids = store.limb_ids(obj) or []
        try:
            _f1, f2, _ids = ops.ring_meta(obj)
        except ValueError:
            f2 = []
        skeleton = {b.lower() for b in store.bone_names(obj)}
        arm = store.rig(obj)
        if arm is not None:
            from . import rig
            skeleton |= {b.name.lower() for b in rig.created_stub_bones(arm)[0]}
        for sl in boner.stub_classes_missing(ids, f2):
            have = sl.root.lower() in skeleton
            items.append((sl.suffix, f"{sl.suffix} ({boner.class_label(sl.limb_id)})",
                          f"A {boner.class_label(sl.limb_id)} stub limb (no patches, bone {sl.root}"
                          f"{'' if have else ', created'}) and its socket: the ring of the selected torso patch"))
    _STUB_ITEMS = items or [("NONE", "(no stub-able class is missing)", "")]
    return _STUB_ITEMS


def created_stub_skeleton(obj):
    """``(names, parents)``: the source skeleton (file names) followed by the rig's created stub bones (Add Stub Socket's,
    accepted by the skeleton guard) -- the skeleton the export writes."""
    from . import rig
    sk = limbops.source_skeleton(obj)
    names, parents = list(sk.names), list(sk.parents)
    arm = store.rig(obj)
    if arm is not None:
        ok, _bad = rig.created_stub_bones(arm)
        for b in ok:
            names.append(b.name)
            parents.append(int(b.parent.get(rig.KEY_BONE_SRC_INDEX)))
    return names, parents


def _point_counts(obj, names):
    """Lower-cased bone name -> the control points it owns in the live skin (source bones; created stub bones own
    none)."""
    import numpy as np
    try:
        own = np.asarray(store.live_skin(obj).owners, dtype=np.int64)
    except ValueError:
        own = np.asarray(store.vert_bone(obj), dtype=np.int64)
    n = len(limbops.source_skeleton(obj).names)
    own = own[(own >= 0) & (own < n)]
    counts = np.bincount(own, minlength=n) if len(own) else np.zeros(n, dtype=np.int64)
    return {str(names[i]).lower(): int(c) for i, c in enumerate(counts[:n]) if c}


def rig_edit_refusal(context, arm, action="add the stub socket (the socket needs a new bone on the rig)"):
    """Why Blender won't put the rig ``arm`` in Edit Mode to add a bone, in plain words (None = it will): the rig is not
    in the view layer (its collection excluded), or it is disabled in viewports (``hide_viewport``, the monitor icon:
    mode_set's poll refuses a hidden object). Hidden with the eye (``hide_set``) is fine. ``action``: what the modder
    does once it is fixed (Add Stub Socket's by default; Add Bone passes its own)."""
    if context.view_layer.objects.get(arm.name) is None:
        return (f"the rig '{arm.name}' is not in the view layer (its collection is excluded): include its collection, "
                f"then {action}")
    if arm.hide_viewport:
        return f"the rig '{arm.name}' is disabled in viewports (the monitor icon): enable it, then {action}"
    return None


def _create_stub_bone(context, obj, arm, plan, verts_blender, patch_cps):
    """Add the plan's root bone to the rig (leaving and re-entering the creature's Edit Mode): a leaf named ``plan.root``
    under the host bone, at the patch centre with the host's orientation and a short tail, deforming nothing, stamped as
    Add Stub Socket's (``rig.KEY_STUB_BONE`` / ``KEY_STUB_PARENT``). Returns the rig bone's name; raises RuntimeError."""
    import numpy as np
    from mathutils import Matrix
    from . import rig
    host_rig = limbops.rig_bone_name(obj, plan.host)
    host = arm.data.bones.get(host_rig)
    sid = host.get(rig.KEY_BONE_SRC_INDEX) if host is not None else None
    if host is None or sid is None:
        raise RuntimeError(f"the host bone '{plan.host}' is not on the rig '{arm.name}'")
    cps = np.asarray(verts_blender, dtype=np.float64)[np.asarray(patch_cps, dtype=np.int64)]
    centre = boner.patch_centre(cps)
    world = boner.stub_bone_world(np.array(host.matrix_local, dtype=np.float64), centre)
    length = boner.stub_tail_length(cps[[0, 3, 15, 12]], centre)
    why = rig_edit_refusal(context, arm)
    if why:
        raise RuntimeError(why)
    was = obj.mode
    arm_selected = arm.select_get()
    made = None
    try:
        rig._set_mode(context, obj, "OBJECT")
        rig._set_mode(context, arm, "EDIT")
        ebs = arm.data.edit_bones
        eb = ebs.new(plan.root)
        made = eb.name
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, length, 0.0)
        eb.matrix = Matrix(world.tolist())
        eb.length = length
        eb.parent = ebs[host_rig]
        eb.use_connect = False
        eb.use_deform = False
        eb[rig.KEY_STUB_BONE] = plan.root
        eb[rig.KEY_STUB_PARENT] = int(sid)
        if made != plan.root:
            ebs.remove(eb)
            made = None
    finally:
        # every step on its own: a failed one (a mode switch Blender refuses) must not keep the creature out of its mode
        for step in (lambda: rig._set_mode(context, arm, "OBJECT") if arm.mode != "OBJECT" else None,
                     lambda: setattr(context.view_layer.objects, "active", obj),
                     lambda: rig._set_mode(context, obj, was) if obj.mode != was else None,
                     lambda: arm.select_set(arm_selected)):
            try:
                step()
            except (RuntimeError, ReferenceError, TypeError):
                pass
    if made is None:
        raise RuntimeError(f"the rig already has a bone called '{plan.root}' that is not a bone of the skeleton (an added "
                           "bone the export refuses): delete or rename it first")
    return made


class BZ_OT_add_stub_socket(bpy.types.Operator):
    """Add an empty limb and its socket on a selected patch, for a missing part"""
    bl_idname = "bz.add_stub_socket"
    bl_label = "Add Stub Socket"
    bl_options = {"REGISTER", "UNDO"}

    slot: bpy.props.EnumProperty(name="Class", items=_stub_items)

    @classmethod
    def poll(cls, context):
        if not _poll(cls, context):
            return False
        if context.active_object.mode != "EDIT":
            cls.poll_message_set("Edit Mode: select the 4 corners of the torso patch the socket goes on")
            return False
        return True

    def execute(self, context):
        import numpy as np
        from types import SimpleNamespace
        obj = context.active_object
        if self.slot in ("", "NONE"):
            self.report({"WARNING"}, "no stub-able class is missing: every leg, arm and wing class has a limb or socket")
            return {"CANCELLED"}
        t = _read(self, obj)
        if t is None:
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, f"Add Stub Socket refused: {err}")
            return {"CANCELLED"}
        sel = ops._selected_patches(obj)
        why = boner.stub_patch_refusal(sel)
        if why:
            self.report({"ERROR"}, f"Add Stub Socket refused: {why}")
            return {"CANCELLED"}
        try:
            names, parents = created_stub_skeleton(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Add Stub Socket refused: the skeleton can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        patches = store.patches(obj)
        pl = store.patch_limb(obj)
        conns = [boner.StubConn(r.name, int(r.flags1), int(r.flags2), tuple(r.corners if r.raw is None else r.raw),
                                any(boner.class_of(lr.limb_id) == boner.class_of(r.flags2) and t.limbs.patches[j]
                                    for j, lr in enumerate(t.limbs.rows)))
                 for r in t.rows]
        ids = [r.limb_id for r in t.limbs.rows]
        lnames = [r.name for r in t.limbs.rows]
        why, plan = boner.stub_plan(self.slot, sel[0], patches, pl, ids, lnames, t.limbs.bones, conns, names, parents,
                                    _point_counts(obj, names), obj.get(store.KEY_NAME, obj.name))
        if why:
            self.report({"ERROR"}, f"Add Stub Socket refused: {why}")
            return {"CANCELLED"}
        arm = store.rig(obj)
        if plan.create and arm is None:
            self.report({"ERROR"}, f"Add Stub Socket refused: the skeleton has no bone '{plan.root}' and the creature has "
                                   "no rig to add it to. Re-import the creature with its rig")
            return {"CANCELLED"}
        if plan.create and rig_edit_refusal(context, arm):
            self.report({"ERROR"}, f"Add Stub Socket refused: {rig_edit_refusal(context, arm)}")
            return {"CANCELLED"}
        key = core.ring_slot(plan.limb_id)[0]
        verts = ops._live_blender_verts(obj)
        corners, notes = ops.fix_ring(obj, SimpleNamespace(slot_key=key), list(plan.quad), context)
        corners = [int(c) for c in corners]
        if sorted(corners) != sorted(plan.quad):
            self.report({"ERROR"}, f"Add Stub Socket refused: the ring fix returned {corners} for the quad {list(plan.quad)}")
            return {"CANCELLED"}
        made = None
        if plan.create:
            try:
                made = _create_stub_bone(context, obj, arm, plan, verts, patches[plan.patch])
            except RuntimeError as e:
                self.report({"ERROR"}, f"Add Stub Socket refused: {e}")
                return {"CANCELLED"}
        if not ops._begin(self, obj):
            return {"CANCELLED"}
        rows = list(t.limbs.rows) + [_stub_limb_row(plan)]
        crows = list(t.rows) + [core.RingEdit(index=len(t.rows), bone=plan.host, corners=corners, src=-1,
                                              name=plan.conn_name, flags1=plan.parent_class, flags2=plan.limb_id)]
        store.set_limb_rows(obj, rows)                  # one journal generation: the limb and its socket together
        store.set_conn_rows(obj, crows)
        _finish(obj)
        limbops._set_active(context, len(rows) - 1)
        _set_active(context, obj, len(crows) - 1, crows)
        text = (f"Added stub socket {plan.suffix} ({boner.class_label(plan.limb_id)}): limb '{plan.limb_name}' with "
                f"{'the new' if plan.create else 'the existing'} bone '{plan.root}', connection '{plan.conn_name}' on "
                f"patch {plan.patch} ({'shared with ' + plan.shares + ', ' if plan.shares else ''}ring {corners}), host "
                f"bone {plan.host} ({plan.host_how})" + (f"; {'; '.join(notes)}" if notes else ""))
        ids_after = [r.limb_id for r in rows]
        warns = boner.socket_warnings(ids, ids_after, [r.flags2 for r in t.rows], [r.flags2 for r in crows],
                                      [r.name for r in rows])
        obj["bz_last_stub_socket"] = text
        obj["bz_last_socket_warning"] = "; ".join(warns)
        if warns:
            self.report({"WARNING"}, f"{text}; WARNING: " + "; ".join(warns))
        else:
            self.report({"INFO"}, text)
        return {"FINISHED"}


def _stub_limb_row(plan):
    """The limb row a :class:`boner.StubPlan` adds: new, no patches, its one root bone."""
    from . import structure
    return structure.LimbRow(-1, plan.limb_name, int(plan.limb_id), (plan.root,))


CLASSES = (BZ_OT_conn_set_active, BZ_OT_create_connection, BZ_OT_rename_connection, BZ_OT_delete_connection,
           BZ_OT_conn_flag, BZ_OT_conn_set_host, BZ_OT_optimise_ring_start, BZ_OT_add_stub_socket)
