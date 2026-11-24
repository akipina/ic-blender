"""sceneobj.panels_map_script -- the scripting sub-panels of the Map panel: Groups and variables, Triggers, Dialog triggers, AI scripts
and armies.

They show for a map opened with the add-on (the state is read when the map is opened, ``map_script``) and are the Blender counterparts
of the Mission Editor's Create Group / Group List, Variable List, Trigger Wizard, Dialog Triggers and AI Scripts and Armies. The Script
Editor of the Mission Editor (it runs single Lua commands inside the running editor) has no counterpart here.
"""
from __future__ import annotations

import bpy

from ICParse.sgb import trigger_edit as _E

from . import map_hooks as H
from . import map_script as MS
from . import ops_map_export as X
from . import ui_kit

ORDER = 64

ROWS: dict = {}           # list id -> fn(top, layout, item)


def row_drawer(list_id: str):
    def register(fn):
        ROWS[list_id] = fn
        return fn
    return register


class BZ_UL_script_rows(bpy.types.UIList):
    """The one list of the scripting panels; each list id has a row drawer (``ROWS``)."""
    bl_idname = "BZ_UL_script_rows"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        fn = ROWS.get(self.list_id)
        top = X.active_map(context)
        if fn is None or top is None:
            layout.label(text=str(getattr(item, "name", item)))
        else:
            fn(top, layout, item, index)


@row_drawer("script_groups")
def _row_group(top, layout, g, index=0):
    row = layout.row(align=True)
    row.prop(g, "name", text="", emboss=False, icon="GROUP")
    row.label(text=f"{len(g.ids())}")


@row_drawer("script_vars")
def _row_var(top, layout, v, index=0):
    row = layout.row(align=True)
    row.prop(v, "name", text="", emboss=False)
    row.label(text=f"#{index}")


@row_drawer("script_triggers")
def _row_trigger(top, layout, t, index=0):
    row = layout.row(align=True)
    row.label(text=t.folder, icon="FILE_FOLDER")
    row.prop(t, "name", text="", emboss=False)
    row.prop(t, "active", text="", icon="CHECKBOX_HLT" if t.active else "CHECKBOX_DEHLT", emboss=False)


@row_drawer("script_exprs")
def _row_expr(top, layout, e, index=0):
    layout.label(text=MS.expr_text(top, e))


@row_drawer("script_dialogs")
def _row_dialog(top, layout, d, index=0):
    row = layout.row(align=True)
    row.prop(d, "name", text="", emboss=False, icon="TEXT")
    row.label(text=f"#{d.did}")


@row_drawer("script_dtriggers")
def _row_dtrigger(top, layout, t, index=0):
    row = layout.row(align=True)
    row.prop(t, "name", text="", emboss=False)
    row.label(text=f"{len(t.actions)} actions")


@row_drawer("script_ai")
def _row_ai(top, layout, t, index=0):
    layout.prop(t, "name", text="", emboss=False)


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


# ---------------------------------------------------------------------------------------------------- arguments
def draw_arg(layout, top, arg) -> None:
    """One typed argument: a group or variable from the map's lists, an object picked from the 3D view, a player, a comparison, a yes / no,
    a decimal or a plain number."""
    kind = _E.CODE_KIND.get(arg.code, _E.K_INT)
    label = arg.label or _E.GENERIC_LABEL.get(arg.code, "value")
    row = layout.row(align=True)
    if kind == _E.K_GROUP:
        if any(g.gid == arg.val for g in top.bz_script_groups):
            row.prop_search(arg, "ref_group", top, "bz_script_groups", text=label, icon="GROUP")
        else:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
            row.prop_search(arg, "ref_group", top, "bz_script_groups", text="", icon="GROUP")
    elif kind == _E.K_VARIABLE:
        if 0 <= arg.val < len(top.bz_script_vars):
            row.prop_search(arg, "ref_var", top, "bz_script_vars", text=label, icon="DRIVER")
        else:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
    elif kind == _E.K_ENTITY:
        name = MS.object_label(top, arg.val)
        row.prop(arg, "val", text=label)
        op = row.operator("bz.script_pick_entity", text="", icon="EYEDROPPER")
        op.path = arg.path_from_id()
        if not name:
            row.alert = True
        layout.label(text=("    = " + name) if name else "    (no such object in the map)")
    elif kind == _E.K_TRIGGER:
        if any(t.tid == arg.val for t in top.bz_script_triggers):
            row.prop_search(arg, "ref_trigger", top, "bz_script_triggers", text=label, icon="DRIVER")
        else:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
    elif kind == _E.K_DIALOG:
        if any(d.did == arg.val for d in top.bz_script_dialogs):
            row.prop_search(arg, "ref_dialog", top, "bz_script_dialogs", text=label, icon="TEXT")
        else:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
    elif kind == _E.K_PLAYER:
        if arg.player_known():
            row.prop(arg, "as_player", text=label)
        else:
            row.prop(arg, "val", text=f"{label} (number)")
    elif kind == _E.K_COMPARE:
        row.prop(arg, "as_compare", text=label)
    elif kind == _E.K_VAROP:
        row.prop(arg, "as_varop", text=label)
    elif kind == _E.K_BOOL:
        row.prop(arg, "as_bool", text=label, toggle=True)
    elif kind == _E.K_FLOAT:
        row.prop(arg, "as_float", text=label)
    elif kind in (_E.K_RESOURCE, _E.K_TEXT):
        entry = next((t for t in top.bz_script_texts if t.tid == arg.val), None)
        if entry is None:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
        else:
            row.prop_search(arg, "ref_text", top, "bz_script_texts", text=label, icon="TEXT")
        op = row.operator("bz.script_text_new", text="", icon="ADD")
        op.path = arg.path_from_id()
        if entry is not None:
            layout.prop(entry, "name", text="")
    elif kind == _E.K_BLUEPRINT:
        if any(b.tid == arg.val for b in top.bz_script_blueprints):
            row.prop_search(arg, "ref_bp", top, "bz_script_blueprints", text=label, icon="OUTLINER_OB_ARMATURE")
        else:
            row.alert = True
            row.prop(arg, "val", text=f"{label} (number)")
        op = row.operator("bz.script_pick_blueprint", text="", icon="ADD")
        op.path = arg.path_from_id()
    else:
        row.prop(arg, "val", text=label)


def draw_exprs(layout, top, owner, which: str, title: str, *, controller: bool = False) -> None:
    """A trigger's list of conditions or actions with add / delete / move, and the selected one's arguments."""
    coll = getattr(owner, which)
    index_name = "cond_index" if which == "conditions" else "act_index"
    layout.label(text=title)
    row = layout.row()
    row.template_list("BZ_UL_script_rows", "script_exprs", owner, which, owner, index_name, rows=3)
    col = row.column(align=True)
    op = col.operator("bz.script_expr_add", text="", icon="ADD")
    op.owner, op.which = owner.path_from_id(), which
    op = col.operator("bz.script_expr_delete", text="", icon="REMOVE")
    op.owner, op.which = owner.path_from_id(), which
    op = col.operator("bz.script_expr_move", text="", icon="TRIA_UP")
    op.owner, op.which, op.delta = owner.path_from_id(), which, -1
    op = col.operator("bz.script_expr_move", text="", icon="TRIA_DOWN")
    op.owner, op.which, op.delta = owner.path_from_id(), which, 1
    i = getattr(owner, index_name)
    if 0 <= i < len(coll):
        e = coll[i]
        box = layout.box()
        if _E.is_known(e.name):
            d = _E.description(e.name)
            if d:
                _hint(box, d, width=42)
        else:
            ui_kit.alert(box, f"'{e.name}' is not a condition or action the game knows.", width=42)
        for a in e.args:
            draw_arg(box, top, a)
        if not len(e.args):
            box.label(text="No arguments.")
        if controller:
            box.prop(e, "controller", text="Controller: the next action waits for this one to finish")


# ---------------------------------------------------------------------------------------------------- groups and variables
def draw_groups(layout, context, top) -> None:
    layout.label(text="Groups")
    _hint(layout, "A group is a named set of placed objects. Triggers use it to act on all of them at once.", width=44)
    row = layout.row()
    row.template_list("BZ_UL_script_rows", "script_groups", top, "bz_script_groups", top, "bz_script_groups_index", rows=4)
    col = row.column(align=True)
    col.operator("bz.script_group_new", text="", icon="ADD")
    col.operator("bz.script_group_delete", text="", icon="REMOVE")
    i = top.bz_script_groups_index
    if 0 <= i < len(top.bz_script_groups):
        g = top.bz_script_groups[i]
        box = layout.box()
        box.prop(g, "name", text="Rename")
        box.label(text=f"Number {g.gid} (what a trigger uses), {len(g.ids())} objects")
        r = box.row(align=True)
        for act, text in (("ADD", "Add selected"), ("REMOVE", "Remove selected")):
            op = r.operator("bz.script_group_members", text=text)
            op.action = act
        r = box.row(align=True)
        op = r.operator("bz.script_group_members", text="Regroup")
        op.action = "SET"
        op = r.operator("bz.script_group_members", text="Select members")
        op.action = "SELECT"
        missing = [m for m in g.ids() if m not in MS.entity_ids(top)]
        if missing:
            ui_kit.alert(box, f"{len(missing)} members are not in the map any more; they leave the group when the map is saved.", width=42)
    o = context.active_object
    if o is not None and "ic_entity_id" in o and "ic_blueprint" in o:
        mine = [g.name for g in top.bz_script_groups if int(o["ic_entity_id"]) in g.ids()]
        layout.label(text=f"{o.name} is in: " + (", ".join(mine) if mine else "no group"))
    layout.separator()
    layout.label(text="Variables")
    _hint(layout, f"The map has {len(top.bz_script_vars)} variables, numbered from 0. A trigger can set and test them. Rename one by "
                  "double-clicking its name.", width=44)
    layout.template_list("BZ_UL_script_rows", "script_vars", top, "bz_script_vars", top, "bz_script_vars_index", rows=4)


# ---------------------------------------------------------------------------------------------------- triggers
def draw_triggers(layout, context, top) -> None:
    _hint(layout, "A trigger fires its actions when all its conditions are true, for the players it is set for. Triggers are saved in "
                  "the map's .trg file.", width=44)
    row = layout.row()
    row.template_list("BZ_UL_script_rows", "script_triggers", top, "bz_script_triggers", top, "bz_script_triggers_index", rows=6)
    col = row.column(align=True)
    col.operator("bz.script_trigger_new", text="", icon="ADD")
    col.operator("bz.script_trigger_delete", text="", icon="REMOVE")
    col.operator("bz.script_trigger_copy", text="", icon="DUPLICATE")
    op = col.operator("bz.script_trigger_move", text="", icon="TRIA_UP")
    op.delta = -1
    op = col.operator("bz.script_trigger_move", text="", icon="TRIA_DOWN")
    op.delta = 1
    i = top.bz_script_triggers_index
    if 0 <= i < len(top.bz_script_triggers):
        t = top.bz_script_triggers[i]
        box = layout.box()
        box.prop(t, "name", text="Name")
        box.prop(t, "folder", text="Folder")
        r = box.row(align=True)
        r.prop(t, "active", text="Active", toggle=True)
        r.prop(t, "looping", text="Repeats", toggle=True)
        box.label(text=f"Number {t.tid}. Who runs it:")
        grid = box.column(align=True)
        for bit, _label in _E.PSET_ROWS:
            grid.prop(t, f"run_{bit}", toggle=False)
        draw_exprs(layout, top, t, "conditions", "Conditions (all must be true)")
        draw_exprs(layout, top, t, "actions", "Actions")
    layout.separator()
    layout.operator("bz.script_check", text="Check the scripts", icon="VIEWZOOM")
    found = MS.LAST_CHECK.get(top.name)
    if found is not None:
        box = layout.box()
        if not found:
            box.label(text="No problems found.", icon="CHECKMARK")
        for p in found[:12]:
            ui_kit.alert(box, (f"{p['where']}: " if p.get("where") else "") + p["text"], icon="ERROR" if p["severity"] == "error" else "INFO",
                         width=42)
        if len(found) > 12:
            box.label(text=f"... and {len(found) - 12} more.")


# ---------------------------------------------------------------------------------------------------- dialog triggers
def draw_dialogs(layout, context, top) -> None:
    _hint(layout, "A dialog is a list of actions only (no conditions), played one after another by the 'Play dialog' action with the "
                  "dialog's number. With Controller on, the next action waits until that one has finished. Saved in the map's .dlg file.",
          width=44)
    row = layout.row()
    row.template_list("BZ_UL_script_rows", "script_dialogs", top, "bz_script_dialogs", top, "bz_script_dialogs_index", rows=4)
    col = row.column(align=True)
    col.operator("bz.script_dialog_new", text="", icon="ADD")
    col.operator("bz.script_dialog_delete", text="", icon="REMOVE")
    i = top.bz_script_dialogs_index
    if 0 <= i < len(top.bz_script_dialogs):
        d = top.bz_script_dialogs[i]
        box = layout.box()
        box.prop(d, "name", text="Name")
        box.label(text=f"Number {d.did}: use it in the 'Play dialog' action.")
        row = box.row()
        row.template_list("BZ_UL_script_rows", "script_dtriggers", d, "triggers", d, "trig_index", rows=3)
        col = row.column(align=True)
        for act, icon in (("NEW", "ADD"), ("DELETE", "REMOVE"), ("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN")):
            op = col.operator("bz.script_dtrigger", text="", icon=icon)
            op.action = act
        j = d.trig_index
        if 0 <= j < len(d.triggers):
            t = d.triggers[j]
            box.prop(t, "name", text="Step")
            draw_exprs(layout, top, t, "actions", "Actions of this step", controller=True)


# ---------------------------------------------------------------------------------------------------- AI scripts and armies
def draw_ai(layout, context, top) -> None:
    _hint(layout, "For single-player maps: a Lua AI script and an army file for each player slot, as DATA:SCENARIOS\\SP\\... paths. Leave "
                  "a slot empty for no script.", width=44)
    if not len(top.bz_script_ai_scripts) and not len(top.bz_script_ai_armies):
        layout.label(text="This map has no per-player AI list.")
        return
    for title, coll in (("AI scripts", top.bz_script_ai_scripts), ("Armies", top.bz_script_ai_armies)):
        box = layout.box()
        box.label(text=title)
        for k, t in enumerate(coll):
            box.prop(t, "name", text=f"Player {k + 1}")


# ---------------------------------------------------------------------------------------------------- the panels
class _Sub:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"
    bl_parent_id = H.MAP_PANEL_ID
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        top = X.active_map(context)
        return top is not None and bool(top.get(MS.KEY_LOADED))


class BZ_PT_map_script_groups(_Sub, bpy.types.Panel):
    bl_label = "Groups and variables"
    bl_idname = "BZ_PT_map_script_groups"

    def draw(self, context):
        draw_groups(self.layout, context, X.active_map(context))


class BZ_PT_map_script_triggers(_Sub, bpy.types.Panel):
    bl_label = "Triggers"
    bl_idname = "BZ_PT_map_script_triggers"

    def draw(self, context):
        draw_triggers(self.layout, context, X.active_map(context))


class BZ_PT_map_script_dialogs(_Sub, bpy.types.Panel):
    bl_label = "Dialog triggers"
    bl_idname = "BZ_PT_map_script_dialogs"

    def draw(self, context):
        draw_dialogs(self.layout, context, X.active_map(context))


class BZ_PT_map_script_ai(_Sub, bpy.types.Panel):
    bl_label = "AI scripts and armies"
    bl_idname = "BZ_PT_map_script_ai"

    def draw(self, context):
        draw_ai(self.layout, context, X.active_map(context))


CLASSES = (BZ_UL_script_rows, BZ_PT_map_script_groups, BZ_PT_map_script_triggers, BZ_PT_map_script_dialogs, BZ_PT_map_script_ai)
