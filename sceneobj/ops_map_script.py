"""sceneobj.ops_map_script -- the operators of the map scripting panels: groups, triggers, dialogs, checks.

Every operator works on the active map (``ops_map_export.active_map``) and changes only the state kept on its collection
(``map_script``); nothing is written to a file until the map is saved.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from ICParse.sgb import dialog_edit as _D
from ICParse.sgb import script_edit as _S
from ICParse.sgb import trigger_edit as _E
from ICParse.sgb import triggers as _T

from . import map_hooks as H
from . import map_script as MS
from . import ops_map_export as X
from .. import enumitems

ORDER = 63


def _top(context):
    return X.active_map(context)


def _resolve(top, path: str):
    try:
        return top.path_resolve(path)
    except (ValueError, KeyError, TypeError):
        return None


def _poll(cls, context):
    top = _top(context)
    return top is not None and bool(top.get(MS.KEY_LOADED))


# ---------------------------------------------------------------------------------------------------- groups
class BZ_OT_script_group_new(bpy.types.Operator):
    bl_idname = "bz.script_group_new"
    bl_label = "Create group from selected"
    bl_description = "Group the selected placed objects"
    bl_options = {"REGISTER", "UNDO"}
    name: StringProperty(name="Group name", default="")

    poll = classmethod(_poll)

    def invoke(self, context, event):
        top = _top(context)
        self.name = self.name or f"Group {len(top.bz_script_groups) + 1}"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        top = _top(context)
        ids, fresh = MS.selected_ids(context, top)
        if not ids:
            self.report({"ERROR"}, "Select one or more placed objects of the map first." + (
                " The selected new objects get a number when the map is saved; add them to a group after saving and opening it again." if fresh else ""))
            return {"CANCELLED"}
        try:
            name = _S.clean_name(self.name, "group name")
        except _S.ScriptError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        item = top.bz_script_groups.add()
        item.name = name
        item.gid = max([g.gid for g in top.bz_script_groups if g != item] + [0]) + 1
        item.set_ids(ids)
        top.bz_script_groups_index = len(top.bz_script_groups) - 1
        self.report({"INFO"}, f"Group {name} made from {len(ids)} objects" + (f"; {fresh} new objects were left out (they have no number until the map is saved)" if fresh else "") + ".")
        return {"FINISHED"}


class BZ_OT_script_group_delete(bpy.types.Operator):
    bl_idname = "bz.script_group_delete"
    bl_label = "Delete group"
    bl_description = "Delete this group; its objects stay"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_groups_index < len(top.bz_script_groups)

    def execute(self, context):
        top = _top(context)
        i = top.bz_script_groups_index
        name = top.bz_script_groups[i].name
        top.bz_script_groups.remove(i)
        top.bz_script_groups_index = max(0, min(i, len(top.bz_script_groups) - 1))
        self.report({"INFO"}, f"Group {name} deleted.")
        return {"FINISHED"}


class BZ_OT_script_group_members(bpy.types.Operator):
    bl_idname = "bz.script_group_members"
    bl_label = "Group members"
    bl_options = {"REGISTER", "UNDO"}
    action: EnumProperty(name="Action", items=[("ADD", "Add selected", "Add the selected objects to the group"),
                                               ("REMOVE", "Remove selected", "Take the selected objects out of the group"),
                                               ("SET", "Regroup", "Make the group the selected objects only"),
                                               ("SELECT", "Select members", "Select the group's objects in the 3D view")])

    @classmethod
    def description(cls, context, properties):
        return {"ADD": "Add the selected placed objects to the group in the list", "REMOVE": "Take the selected objects out of the group",
                "SET": "Make the group exactly the selected objects (Regroup)", "SELECT": "Select the group's objects in the 3D view"}[properties.action]

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_groups_index < len(top.bz_script_groups)

    def execute(self, context):
        top = _top(context)
        g = top.bz_script_groups[top.bz_script_groups_index]
        have = g.ids()
        if self.action == "SELECT":
            objs = MS.map_entities(top)
            for o in context.selected_objects:
                o.select_set(False)
            n = 0
            for i in have:
                o = objs.get(i)
                if o is not None and o.name in context.view_layer.objects:
                    o.select_set(True)
                    n += 1
            self.report({"INFO"}, f"{n} of the {len(have)} members selected.")
            return {"FINISHED"}
        ids, fresh = MS.selected_ids(context, top)
        if not ids:
            self.report({"ERROR"}, "Select one or more placed objects of the map first.")
            return {"CANCELLED"}
        if self.action == "ADD":
            g.set_ids(have + ids)
        elif self.action == "REMOVE":
            g.set_ids([i for i in have if i not in set(ids)])
        else:
            g.set_ids(ids)
        self.report({"INFO"}, f"Group {g.name} now has {len(g.ids())} objects." + (f" {fresh} new objects were left out (no number until the map is saved)." if fresh else ""))
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- triggers
def _fresh_trigger_id(top) -> int:
    ids = [t.tid for t in top.bz_script_triggers]
    return (max(ids) + 1) if ids else 0


class BZ_OT_script_trigger_new(bpy.types.Operator):
    bl_idname = "bz.script_trigger_new"
    bl_label = "New trigger"
    bl_description = "Add a trigger with the condition 'Always' and no action"
    bl_options = {"REGISTER", "UNDO"}
    name: StringProperty(name="Name", default="New trigger")
    folder: StringProperty(name="Folder", default="", description="Letters, digits and underscores; a new name makes a new folder")

    poll = classmethod(_poll)

    def invoke(self, context, event):
        top = _top(context)
        i = top.bz_script_triggers_index
        if not self.folder:
            self.folder = top.bz_script_triggers[i].folder if 0 <= i < len(top.bz_script_triggers) else (
                top.bz_script_folders[0].name if len(top.bz_script_folders) else "default")
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        top = _top(context)
        try:
            name = _E.check_trigger_name(self.name) or "New trigger"
            folder = _E.check_folder_name(self.folder or "default")
        except _E.TriggerError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if folder not in {f.name for f in top.bz_script_folders}:
            top.bz_script_folders.add().name = folder
        item = top.bz_script_triggers.add()
        item.name, item.folder, item.tid = name, folder, _fresh_trigger_id(top)
        item.pset, item.active, item.looping = 1, True, False
        MS._fill_expr(top, item.conditions.add(), "Always", [])
        # the new trigger goes after the last trigger of its folder
        last = max([k for k, t in enumerate(top.bz_script_triggers) if t.folder == folder and t != item] + [-1])
        new_at = len(top.bz_script_triggers) - 1
        target = last + 1
        if last >= 0 and target < new_at:
            top.bz_script_triggers.move(new_at, target)
        else:
            target = new_at
        top.bz_script_triggers_index = target
        return {"FINISHED"}


class BZ_OT_script_trigger_copy(bpy.types.Operator):
    bl_idname = "bz.script_trigger_copy"
    bl_label = "Copy trigger"
    bl_description = "Duplicate this trigger, placing the copy right after it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_triggers_index < len(top.bz_script_triggers)

    def execute(self, context):
        top = _top(context)
        i = top.bz_script_triggers_index
        src = top.bz_script_triggers[i]
        item = top.bz_script_triggers.add()
        MS.copy_trigger(src, item)
        item.tid = max([t.tid for t in top.bz_script_triggers if t != item] + [-1]) + 1
        new_at = len(top.bz_script_triggers) - 1
        top.bz_script_triggers.move(new_at, i + 1)
        top.bz_script_triggers_index = i + 1
        return {"FINISHED"}


class BZ_OT_script_trigger_delete(bpy.types.Operator):
    bl_idname = "bz.script_trigger_delete"
    bl_label = "Delete trigger"
    bl_description = "Delete the trigger in the list"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_triggers_index < len(top.bz_script_triggers)

    def execute(self, context):
        top = _top(context)
        i = top.bz_script_triggers_index
        name = top.bz_script_triggers[i].name
        top.bz_script_triggers.remove(i)
        top.bz_script_triggers_index = max(0, min(i, len(top.bz_script_triggers) - 1))
        self.report({"INFO"}, f"Trigger {name} deleted.")
        return {"FINISHED"}


class BZ_OT_script_trigger_move(bpy.types.Operator):
    bl_idname = "bz.script_trigger_move"
    bl_label = "Move trigger"
    bl_description = "Move the trigger up or down inside its folder"
    bl_options = {"REGISTER", "UNDO"}
    delta: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_triggers_index < len(top.bz_script_triggers)

    def execute(self, context):
        top = _top(context)
        i = top.bz_script_triggers_index
        folder = top.bz_script_triggers[i].folder
        j = i + (1 if self.delta > 0 else -1)
        while 0 <= j < len(top.bz_script_triggers) and top.bz_script_triggers[j].folder != folder:
            j += 1 if self.delta > 0 else -1
        if not 0 <= j < len(top.bz_script_triggers):
            return {"CANCELLED"}
        top.bz_script_triggers.move(i, j)
        top.bz_script_triggers_index = j
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- conditions and actions
_OP_ITEMS: dict = {}


def op_items(kind: str) -> list:
    """The enum items of every condition or action: the plain name as the label, the manual's line as the tooltip. Built once and kept
    (Blender needs the strings of a dynamic enum to stay alive)."""
    if kind not in _OP_ITEMS:
        _OP_ITEMS[kind] = [(n, _E.display_name(n), _E.description(n) or f"{n} (no description)")
                           for n in _E.names(kind)]
    return _OP_ITEMS[kind]


@enumitems.stable
def _op_items_for(self, context):
    return op_items("condition" if self.which == "conditions" else "action")


class BZ_OT_script_expr_add(bpy.types.Operator):
    bl_idname = "bz.script_expr_add"
    bl_label = "Add condition or action"
    bl_description = "Add a condition or an action from the full list (type to search)"
    bl_options = {"REGISTER", "UNDO"}
    owner: StringProperty(options={"HIDDEN"})
    which: StringProperty(options={"HIDDEN"})         # "conditions" or "actions"
    op: EnumProperty(name="Add", items=_op_items_for)

    poll = classmethod(_poll)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        top = _top(context)
        owner = _resolve(top, self.owner)
        if owner is None or not self.op:
            return {"CANCELLED"}
        coll = getattr(owner, self.which)
        item = coll.add()
        proto = _E.new_expression(self.op, _T.Condition)
        MS._fill_expr(top, item, self.op, proto.args)
        if getattr(owner, "cond_index", None) is not None and self.which == "conditions":
            owner.cond_index = len(coll) - 1
        if self.which == "actions":
            owner.act_index = len(coll) - 1
        self.report({"INFO"}, _E.display_name(self.op) + (f": {_E.description(self.op)}" if _E.description(self.op) else ""))
        return {"FINISHED"}


class BZ_OT_script_expr_delete(bpy.types.Operator):
    bl_idname = "bz.script_expr_delete"
    bl_label = "Delete"
    bl_description = "Delete the selected condition or action"
    bl_options = {"REGISTER", "UNDO"}
    owner: StringProperty(options={"HIDDEN"})
    which: StringProperty(options={"HIDDEN"})         # "conditions" or "actions"

    def execute(self, context):
        top = _top(context)
        owner = _resolve(top, self.owner)
        if owner is None:
            return {"CANCELLED"}
        coll = getattr(owner, self.which)
        attr = "cond_index" if self.which == "conditions" else "act_index"
        i = getattr(owner, attr)
        if not 0 <= i < len(coll):
            return {"CANCELLED"}
        coll.remove(i)
        setattr(owner, attr, max(0, min(i, len(coll) - 1)))
        return {"FINISHED"}


class BZ_OT_script_expr_move(bpy.types.Operator):
    bl_idname = "bz.script_expr_move"
    bl_label = "Move"
    bl_description = "Move the selected condition or action up or down"
    bl_options = {"REGISTER", "UNDO"}
    owner: StringProperty(options={"HIDDEN"})
    which: StringProperty(options={"HIDDEN"})         # "conditions" or "actions"
    delta: IntProperty(default=-1)

    def execute(self, context):
        top = _top(context)
        owner = _resolve(top, self.owner)
        if owner is None:
            return {"CANCELLED"}
        coll = getattr(owner, self.which)
        attr = "cond_index" if self.which == "conditions" else "act_index"
        i = getattr(owner, attr)
        j = i + (1 if self.delta > 0 else -1)
        if not (0 <= i < len(coll) and 0 <= j < len(coll)):
            return {"CANCELLED"}
        coll.move(i, j)
        setattr(owner, attr, j)
        return {"FINISHED"}


class BZ_OT_script_pick_entity(bpy.types.Operator):
    bl_idname = "bz.script_pick_entity"
    bl_label = "Use the selected object"
    bl_description = "Fill this argument with the number of the active placed object of the map"
    bl_options = {"REGISTER", "UNDO"}
    path: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        top = _top(context)
        arg = _resolve(top, self.path)
        o = context.active_object
        if arg is None or o is None or "ic_entity_id" not in o or "ic_blueprint" not in o:
            self.report({"ERROR"}, "Make a placed object of the map the active object first (a new object has no number until the map is saved).")
            return {"CANCELLED"}
        arg.val = int(o["ic_entity_id"])
        return {"FINISHED"}


class BZ_OT_script_text_new(bpy.types.Operator):
    bl_idname = "bz.script_text_new"
    bl_label = "New text"
    bl_description = ("Add a text to the map's text table and use it here")
    bl_options = {"REGISTER", "UNDO"}
    path: StringProperty(options={"HIDDEN"})
    text: StringProperty(name="Text", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        top = _top(context)
        arg = _resolve(top, self.path)
        if arg is None:
            return {"CANCELLED"}
        if not top.get(MS.KEY_HAS_TEXT):
            self.report({"ERROR"}, "This map has no text table, so no text can be added to it.")
            return {"CANCELLED"}
        text = self.text.strip()
        if not text:
            self.report({"ERROR"}, "Type the text first.")
            return {"CANCELLED"}
        try:
            text.encode("latin1")
        except UnicodeEncodeError:
            self.report({"ERROR"}, "The map file can hold Western European letters only.")
            return {"CANCELLED"}
        row = next((t for t in top.bz_script_texts if t.name == text), None)
        if row is None:
            row = top.bz_script_texts.add()
            row.tid = max([t.tid for t in top.bz_script_texts if t != row] + [2999]) + 1
            row.name = text
        arg.val = row.tid
        return {"FINISHED"}


_BP_ITEMS: list = []


@enumitems.stable
def _bp_items(self, context):
    items = X._blueprint_items(self, context)
    _BP_ITEMS[:] = items
    return _BP_ITEMS


class BZ_OT_script_pick_blueprint(bpy.types.Operator):
    bl_idname = "bz.script_pick_blueprint"
    bl_label = "Another blueprint"
    bl_description = "Pick any of the game's blueprints for this argument"
    bl_options = {"REGISTER", "UNDO"}
    path: StringProperty(options={"HIDDEN"})
    blueprint: EnumProperty(name="Blueprint", items=_bp_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        top = _top(context)
        arg = _resolve(top, self.path)
        if arg is None or self.blueprint in ("", "NONE"):
            self.report({"ERROR"}, "No blueprints found: set the game folder in the add-on's preferences.")
            return {"CANCELLED"}
        try:
            norm = _S.norm_blueprint(self.blueprint)
        except Exception:                                 # noqa: BLE001
            norm = self.blueprint.lower()
        row = next((b for b in top.bz_script_blueprints if b.path == norm), None)
        if row is None:
            row = top.bz_script_blueprints.add()
            row.tid = max([b.tid for b in top.bz_script_blueprints if b != row and b.tid >= MS.PENDING_BASE] + [MS.PENDING_BASE - 1]) + 1
            row.path, row.name = norm, MS.blueprint_label(norm)
        arg.val = row.tid
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- dialogs
class BZ_OT_script_dialog_new(bpy.types.Operator):
    bl_idname = "bz.script_dialog_new"
    bl_label = "New dialog"
    bl_description = "Add a dialog: a list of actions played one after another by the 'Play dialog' action"
    bl_options = {"REGISTER", "UNDO"}
    name: StringProperty(name="Name", default="new_dialog", description="Letters, digits and underscores")

    poll = classmethod(_poll)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        top = _top(context)
        try:
            name = _D.check_dialog_name(self.name, [d.name for d in top.bz_script_dialogs])
        except _D.DialogError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        item = top.bz_script_dialogs.add()
        item.name = name
        item.did = max([d.did for d in top.bz_script_dialogs if d != item] + [-1]) + 1
        top.bz_script_dialogs_index = len(top.bz_script_dialogs) - 1
        return {"FINISHED"}


class BZ_OT_script_dialog_delete(bpy.types.Operator):
    bl_idname = "bz.script_dialog_delete"
    bl_label = "Delete dialog"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_dialogs_index < len(top.bz_script_dialogs)

    def execute(self, context):
        top = _top(context)
        i = top.bz_script_dialogs_index
        top.bz_script_dialogs.remove(i)
        top.bz_script_dialogs_index = max(0, min(i, len(top.bz_script_dialogs) - 1))
        return {"FINISHED"}


class BZ_OT_script_dtrigger(bpy.types.Operator):
    bl_idname = "bz.script_dtrigger"
    bl_label = "Dialog step"
    bl_options = {"REGISTER", "UNDO"}
    action: EnumProperty(items=[("NEW", "New step", "Add a step (a dialog trigger: actions only) to the dialog"),
                                ("DELETE", "Delete step", "Delete the step in the list"),
                                ("UP", "Move up", ""), ("DOWN", "Move down", "")])

    @classmethod
    def description(cls, context, properties):
        return {"NEW": "Add a step (a dialog trigger: actions only) to the dialog", "DELETE": "Delete the step in the list",
                "UP": "Move the step up", "DOWN": "Move the step down"}[properties.action]

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and 0 <= top.bz_script_dialogs_index < len(top.bz_script_dialogs)

    def execute(self, context):
        top = _top(context)
        d = top.bz_script_dialogs[top.bz_script_dialogs_index]
        if self.action == "NEW":
            used = [t.tid for dd in top.bz_script_dialogs for t in dd.triggers]
            t = d.triggers.add()
            t.name, t.tid = f"step_{len(d.triggers)}", (max(used) + 1) if used else 1
            d.trig_index = len(d.triggers) - 1
            return {"FINISHED"}
        i = d.trig_index
        if not 0 <= i < len(d.triggers):
            return {"CANCELLED"}
        if self.action == "DELETE":
            d.triggers.remove(i)
            d.trig_index = max(0, min(i, len(d.triggers) - 1))
            return {"FINISHED"}
        j = i + (1 if self.action == "DOWN" else -1)
        if not 0 <= j < len(d.triggers):
            return {"CANCELLED"}
        d.triggers.move(i, j)
        d.trig_index = j
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------- checks
class BZ_OT_script_check(bpy.types.Operator):
    bl_idname = "bz.script_check"
    bl_label = "Check the scripts"
    bl_description = "Check scripts for missing objects, groups, variables and other mistakes"
    bl_options = {"REGISTER"}

    poll = classmethod(_poll)

    def execute(self, context):
        top = _top(context)
        found = [p for p in H.run_checks(context, top) if p["check"] == MS.STEP]
        MS.LAST_CHECK[top.name] = found
        errors = sum(1 for p in found if p["severity"] == "error")
        self.report({"WARNING" if found else "INFO"}, f"{errors} errors, {len(found) - errors} warnings." if found else "No problems found in the scripts.")
        return {"FINISHED"}


CLASSES = (BZ_OT_script_group_new, BZ_OT_script_group_delete, BZ_OT_script_group_members, BZ_OT_script_trigger_new,
           BZ_OT_script_trigger_copy, BZ_OT_script_trigger_delete, BZ_OT_script_trigger_move, BZ_OT_script_expr_add, BZ_OT_script_expr_delete,
           BZ_OT_script_expr_move, BZ_OT_script_pick_entity, BZ_OT_script_text_new, BZ_OT_script_pick_blueprint, BZ_OT_script_dialog_new, BZ_OT_script_dialog_delete,
           BZ_OT_script_dtrigger, BZ_OT_script_check)
