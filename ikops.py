"""Boner > Bones > IK Chains and Joint Limits.

IK chains are journaled rows naming source bones by FILE names (``store.ik_rows``; absent = source chunk as is), applied
by ``topology.apply_ik_rows`` before the delete / re-parent fix and renames. No shipped binary reads them; keep each one
path of the hierarchy. Joint limits sit on rig bones (``bz_joint_limits``, radians) and are exported only when changed.
"""
from __future__ import annotations

import bpy

from . import boner
from . import draw
from . import rig
from . import store
from . import topology
from . import enumitems

FLAG_ITEMS = (("1", "Head", "stock bit 1"), ("2", "Spine", "stock bit 2"), ("4", "Arm", "stock bit 4"),
              ("8", "Leg", "stock bit 8"), ("16", "Tail", "stock bit 16"), ("64", "Ear", "stock bit 64"),
              ("128", "Jaw", "stock bit 128"), ("256", "Antenna", "stock bit 256"), ("512", "TopWing", "stock bit 512"),
              ("1024", "Foot", "stock bit 1024"), ("2048", "Neck", "stock bit 2048"))
FLAG_NAMES = {int(v): n for v, n, _d in FLAG_ITEMS}


def flags_label(flags) -> str:
    """'Neck' for the stock bit, else the bits set ('Head+Leg') or the number."""
    f = int(flags) & 0xFFFF
    if f in FLAG_NAMES:
        return FLAG_NAMES[f]
    names = [n for v, n in FLAG_NAMES.items() if f & v]
    rest = f & ~sum(v for v in FLAG_NAMES if f & v)
    return "+".join(names + ([str(rest)] if rest else [])) if names else str(f)


def _source(obj):
    from . import core
    return core.read_source(store.source_path(obj))


def chain_rows(obj, src=None):
    """``(rows, stored)``: the IK chain table as Export writes it -- the stored rows, else the source's chains
    (``[]`` when the file has no IKCH chunk). Raises ValueError for an inconsistent stored table."""
    rows = store.ik_rows(obj)
    if rows is not None:
        return rows, True
    src = _source(obj) if src is None else src
    return topology.source_ik_rows(src) or [], False


def rig_names_of(obj, arm, src=None):
    """{file name (lower): current rig name} for the source bones on the rig (deleted ones missing) plus the added
    bones by their own name, and ``{rig name: file name}`` the other way."""
    src = _source(obj) if src is None else src
    sk = rig.rig_skeleton(arm, src)
    file_to_rig, rig_to_file = {}, {}
    if sk is not None:
        for i, c in enumerate(sk.resolution.current):
            if c is not None and i < len(src.bones):
                file_to_rig[str(src.bones[i].name).lower()] = c
                rig_to_file[c] = str(src.bones[i].name)
        for nm in sk.added_names:
            file_to_rig[nm.lower()] = nm
            rig_to_file[nm] = nm
    return file_to_rig, rig_to_file


def rig_path_problem(arm, rig_bones) -> str | None:
    """:func:`topology.chain_path_problem` on the rig's current hierarchy (rig names)."""
    names = [b.name for b in arm.data.bones]
    index = {n: i for i, n in enumerate(names)}
    parents = [index.get(b.parent.name) if b.parent is not None else None for b in arm.data.bones]
    return topology.chain_path_problem(names, parents, rig_bones)


def chain_status(obj, arm, row, src=None) -> str:
    """Why a chain row is not a hierarchy path on the rig ('' = fine): a bone the rig lacks (deleted; Export takes it
    off), bones in separate branches (Export completes the path or drops the chain with a warning)."""
    f2r, _r2f = rig_names_of(obj, arm, src)
    missing = [b for b in row.bones if str(b).lower() not in f2r]
    on_rig = [f2r[str(b).lower()] for b in row.bones if str(b).lower() in f2r]
    why = rig_path_problem(arm, on_rig)
    parts = []
    if missing:
        parts.append(f"{', '.join(missing[:3])} not on the rig (deleted: Export takes it off)")
    if why:
        parts.append(why + " (Export completes the path or drops the chain)")
    return "; ".join(parts)


def _poll(cls, context) -> bool:
    obj = context.active_object
    if not store.is_native(obj) or obj.get("bz_scene"):
        return False
    if store.rig(obj) is None:
        cls.poll_message_set("this creature has no rig")
        return False
    return True


@enumitems.stable
def _bone_items(self, context):
    obj = context.active_object
    arm = store.rig(obj) if obj is not None else None
    return [(b.name, b.name, "") for b in arm.data.bones] if arm is not None else [("", "(no rig)", "")]


def chain_bones_between(arm, start, end):
    """The rig bones from ``start`` down to ``end`` (root-to-tip), or None when ``end`` does not hang from ``start``."""
    b = arm.data.bones.get(end)
    out = []
    while b is not None:
        out.append(b.name)
        if b.name == start:
            return list(reversed(out))
        b = b.parent
    return None


def _begin(op, obj) -> bool:
    from . import ops
    return ops._begin(op, obj)


class BZ_OT_ik_add_chain(bpy.types.Operator):
    """Add an IK chain from a start bone down to an end bone"""
    bl_idname = "bz.ik_add_chain"
    bl_label = "Add IK Chain"
    bl_options = {"REGISTER", "UNDO"}

    name: bpy.props.StringProperty(name="Name", default="", options={"SKIP_SAVE"})
    start: bpy.props.EnumProperty(name="From", items=_bone_items, options={"SKIP_SAVE"})
    end: bpy.props.EnumProperty(name="To", items=_bone_items, options={"SKIP_SAVE"})
    flags: bpy.props.EnumProperty(name="Kind", items=FLAG_ITEMS, default="1",
                                  description="The kind of chain")
    enabled: bpy.props.IntProperty(name="Enabled", default=1, min=0, max=65535,
                                   description="Whether the chain is enabled (normally 1)")

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        arm = store.rig(obj)
        s = getattr(context.scene, "bz_boner", None)
        active = s.bone if s is not None and s.bone and arm.data.bones.get(s.bone) is not None else ""
        if active:
            self.start = self.end = active
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "name")
        col.prop(self, "start")
        col.prop(self, "end")
        col.prop(self, "flags")
        col.prop(self, "enabled")

    def execute(self, context):
        obj = context.active_object
        arm = store.rig(obj)
        try:
            src = _source(obj)
            rows, _stored = chain_rows(obj, src)
        except Exception as e:
            self.report({"ERROR"}, f"Add IK Chain refused: the IK table can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        name = str(self.name).strip()
        why = boner.limb_name_error(name, [r.name for r in rows], what="IK chain")
        if why:
            self.report({"ERROR"}, f"Add IK Chain refused: {why}")
            return {"CANCELLED"}
        path = chain_bones_between(arm, self.start, self.end)
        if path is None:
            self.report({"ERROR"}, f"Add IK Chain refused: '{self.end}' does not hang from '{self.start}'. "
                                   "Pick the top bone as From and a bone below it as To")
            return {"CANCELLED"}
        _f2r, r2f = rig_names_of(obj, arm, src)
        unknown = [b for b in path if b not in r2f]
        if unknown:
            self.report({"ERROR"}, f"Add IK Chain refused: bone{'s' if len(unknown) != 1 else ''} "
                                   f"{', '.join(repr(b) for b in unknown[:3])} {'are' if len(unknown) != 1 else 'is'} no "
                                   "bone the export writes (a copy, a stub or a refused bone)")
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        rows = list(rows) + [topology.IKRow(-1, name, int(self.flags), int(self.enabled), tuple(r2f[b] for b in path))]
        store.set_ik_rows(obj, rows)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Added IK chain '{name}' ({flags_label(self.flags)}): {' > '.join(path)}; Export writes the "
                              f"table ({len(rows)} chains)")
        return {"FINISHED"}


class BZ_OT_ik_remove_chain(bpy.types.Operator):
    """Remove an IK chain from the creature's table (Export writes the table without it)"""
    bl_idname = "bz.ik_remove_chain"
    bl_label = "Remove IK Chain"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(name="Chain", default=-1, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        try:
            rows, _stored = chain_rows(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Remove IK Chain refused: the IK table can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        if not 0 <= int(self.index) < len(rows):
            self.report({"ERROR"}, f"Remove IK Chain refused: no chain {self.index} (the table has {len(rows)})")
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        gone = rows[int(self.index)]
        store.set_ik_rows(obj, [r for i, r in enumerate(rows) if i != int(self.index)])
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Removed IK chain '{gone.name}' ({len(rows) - 1} chains left)")
        return {"FINISHED"}


class BZ_OT_ik_edit_chain(bpy.types.Operator):
    """Rename an IK chain or change its kind and enabled values"""
    bl_idname = "bz.ik_edit_chain"
    bl_label = "Edit IK Chain"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty(name="Chain", default=-1, options={"SKIP_SAVE"})
    name: bpy.props.StringProperty(name="Name", default="", options={"SKIP_SAVE"})
    flags: bpy.props.IntProperty(name="Flags", default=1, min=0, max=65535,
                                 description="Chain kind bit: Head 1, Spine 2, Arm 4, Leg 8, Tail 16, Ear 64, Jaw 128, ...")
    enabled: bpy.props.IntProperty(name="Enabled", default=1, min=0, max=65535, description="The enabled value (normally 1)")

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def invoke(self, context, event):
        obj = context.active_object
        try:
            rows, _stored = chain_rows(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Edit IK Chain refused: the IK table can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        if not 0 <= int(self.index) < len(rows):
            self.report({"ERROR"}, f"Edit IK Chain refused: no chain {self.index}")
            return {"CANCELLED"}
        r = rows[int(self.index)]
        self.name, self.flags, self.enabled = r.name, int(r.flags), int(r.enabled)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        obj = context.active_object
        try:
            rows, _stored = chain_rows(obj)
        except Exception as e:
            self.report({"ERROR"}, f"Edit IK Chain refused: the IK table can't be read: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        i = int(self.index)
        if not 0 <= i < len(rows):
            self.report({"ERROR"}, f"Edit IK Chain refused: no chain {i}")
            return {"CANCELLED"}
        name = str(self.name).strip()
        why = boner.limb_name_error(name, [r.name for j, r in enumerate(rows) if j != i], what="IK chain")
        if why:
            self.report({"ERROR"}, f"Edit IK Chain refused: {why}")
            return {"CANCELLED"}
        r = rows[i]
        if (name, int(self.flags), int(self.enabled)) == (r.name, int(r.flags), int(r.enabled)):
            self.report({"INFO"}, f"IK chain '{r.name}' unchanged")
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        rows = list(rows)
        rows[i] = topology.IKRow(r.src, name, int(self.flags), int(self.enabled), tuple(r.bones))
        store.set_ik_rows(obj, rows)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"IK chain '{r.name}' -> '{name}' ({flags_label(self.flags)}, enabled {int(self.enabled)})")
        return {"FINISHED"}


class BZ_OT_ik_reset_chains(bpy.types.Operator):
    """Discard the IK chain edits and export the source file's chains"""
    bl_idname = "bz.ik_reset_chains"
    bl_label = "Reset IK Chains"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        if store.ik_rows(obj) is None:
            self.report({"INFO"}, "the IK chains are the source's already")
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        store.set_ik_rows(obj, None)
        draw.invalidate(obj.name)
        self.report({"INFO"}, "IK chains back to the source file's")
        return {"FINISHED"}


class BZ_OT_reset_joint_limits(bpy.types.Operator):
    """Put a bone's joint limits back to the values the creature had when it was imported (the stock ones)"""
    bl_idname = "bz.reset_joint_limits"
    bl_label = "Reset Joint Limits"
    bl_options = {"REGISTER", "UNDO"}

    bone: bpy.props.StringProperty(name="Bone", default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        arm = store.rig(obj)
        b = arm.data.bones.get(self.bone)
        if b is None:
            self.report({"ERROR"}, f"'{self.bone}' is not a bone of the rig")
            return {"CANCELLED"}
        vals = source_limits(obj, arm, self.bone)
        if vals is None:
            self.report({"ERROR"}, f"'{self.bone}' has no record in the source (an added bone: its limits start at zero)")
            return {"CANCELLED"}
        b[rig.KEY_BONE_LIMITS] = list(vals)
        self.report({"INFO"}, f"joint limits of '{self.bone}' back to the stock values")
        return {"FINISHED"}


def source_limits(obj, arm, bone_name):
    """The source record's joint limit floats of the rig bone ``bone_name`` (by its import stamp), or None."""
    b = arm.data.bones.get(bone_name)
    sid = b.get(rig.KEY_BONE_SRC_INDEX) if b is not None else None
    if sid is None:
        return None
    try:
        src = _source(obj)
    except Exception:
        return None
    i = int(sid)
    return [float(x) for x in src.bones[i].tail] if 0 <= i < len(src.bones) else None


# ------------------------------------------------------------------------------------------------ panel boxes
def _alert(layout, text):
    col = layout.column()
    col.alert = True
    col.label(text=text[:110], icon="ERROR")


def draw_limits_box(layout, context, obj, bone_name):
    """The active bone's joint limits: the stock values (read-only) and the editable copy on the rig bone."""
    arm = store.rig(obj)
    b = arm.data.bones.get(bone_name) if arm is not None and bone_name else None
    if b is None:
        return
    box = layout.box()
    box.label(text=f"Joint limits of {bone_name}, radians: min xyz, max xyz", icon="CON_ROTLIMIT")
    stock = source_limits(obj, arm, bone_name)
    if stock is not None:
        box.label(text="stock: " + " ".join(f"{v:.3f}" for v in stock[:6])
                  + (f"  flag {int.from_bytes(__import__('struct').pack('<f', stock[6]), 'little')}" if len(stock) > 6 else ""))
    elif b.get(rig.KEY_BONE_SRC_INDEX) is None:
        box.label(text="an added bone: zero limits unless edited")
    if b.get(rig.KEY_BONE_LIMITS) is None:
        if stock is not None:
            box.label(text="Re-import the creature to edit limits", icon="INFO")
        return
    box.prop(b, f'["{rig.KEY_BONE_LIMITS}"]', text="")
    if stock is not None:
        op = box.operator(BZ_OT_reset_joint_limits.bl_idname, text="Reset to Stock", icon="LOOP_BACK")
        op.bone = bone_name


def draw_ik_box(layout, context, obj):
    """The IK chain table: one row per chain (name, kind, bones, status), Edit / Remove per row, Add / Reset."""
    arm = store.rig(obj)
    if arm is None:
        return
    box = layout.box()
    try:
        src = _source(obj)
        rows, stored = chain_rows(obj, src)
    except Exception as e:
        _alert(box, f"IK chains can't be read: {type(e).__name__}: {e}")
        return
    box.label(text=f"IK chains: {len(rows)}" + (" (edited)" if stored else ""), icon="CON_KINEMATIC")
    f2r, _r2f = rig_names_of(obj, arm, src)
    for i, r in enumerate(rows):
        row = box.row(align=True)
        status = chain_status(obj, arm, r, src)
        row.alert = bool(status)
        row.label(text=f"{r.name} · {flags_label(r.flags)}" + ("" if int(r.enabled) == 1 else f" · enabled {int(r.enabled)}"))
        names = [f2r.get(str(b).lower(), f"{b}?") for b in r.bones]
        row.label(text=" > ".join(names) if names else "(no bones)")
        op = row.operator(BZ_OT_ik_edit_chain.bl_idname, text="", icon="GREASEPENCIL")
        op.index = i
        op = row.operator(BZ_OT_ik_remove_chain.bl_idname, text="", icon="X")
        op.index = i
        if status:
            _alert(box, f"{r.name}: {status}")
    row = box.row(align=True)
    row.operator(BZ_OT_ik_add_chain.bl_idname, text="Add IK Chain", icon="ADD")
    if stored:
        row.operator(BZ_OT_ik_reset_chains.bl_idname, text="", icon="LOOP_BACK")


CLASSES = (BZ_OT_ik_add_chain, BZ_OT_ik_remove_chain, BZ_OT_ik_edit_chain, BZ_OT_ik_reset_chains, BZ_OT_reset_joint_limits)
