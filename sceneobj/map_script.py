"""Map scripting state (no operators or panels): groups, variables, AI scripts and armies (saved in the map file),
triggers (``.trg``) and dialogs (``.dlg``), kept on the map's collection so they save and undo with the .blend.

Byte work is in ``ICParse.sgb``. A part whose fingerprint still matches what was read must be left byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty)

from ICParse.sgb import dialog_edit as _D
from ICParse.sgb import script_edit as _S
from ICParse.sgb import trigger_edit as _E
from ICParse.sgb import triggers as _T

from . import map_hooks as H

ORDER = 55

KEY_FINGER = "ic_script_fingerprints"       # on the map's collection: JSON {"groups": sha1, "triggers": sha1, "dialogs": sha1}
KEY_BASE = "ic_script_baseline"             # JSON {"entities": [ids], "groups": [gids]} as the map was opened (what a check compares to)
KEY_LOADED = "ic_script_loaded"
KEY_HAS_TEXT = "ic_script_has_text"         # True when the map has a TEXT table (the strings are in bz_script_texts)
STEP = "script"
LAST_CHECK: dict = {}                       # map collection name -> the problems the last "Check scripts" found


# ---------------------------------------------------------------------------------------------------- the library (loaded when used)
def lib():
    """The format library's modules: ``(script_edit, trigger_edit, dialog_edit, triggers)``."""
    return _S, _E, _D, _T


# ---------------------------------------------------------------------------------------------------- property groups
PENDING_BASE = 1_900_000_000        # a blueprint picked for a trigger that the map's list does not hold yet gets a number from here until the save


class BZ_PG_script_text(bpy.types.PropertyGroup):
    """One row of a list: a folder's name, an AI script path, a text of the map's text table (``tid`` = its number), a blueprint of the
    map's list (``tid`` = its number, ``path`` = the .ebp path)."""
    name: StringProperty(name="Text")
    tid: IntProperty(name="Number")
    path: StringProperty(name="Path")


class BZ_PG_script_group(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", description="The group's name; a trigger names the group by its number, this name is for you")
    gid: IntProperty(name="Number", description="The number a trigger uses to name this group")
    members: StringProperty(name="Members", description="The numbers of the placed objects in the group, as a list")

    def ids(self) -> list:
        try:
            return [int(x) for x in json.loads(self.members or "[]")]
        except (ValueError, TypeError):
            return []

    def set_ids(self, ids) -> None:
        seen, out = set(), []
        for i in ids:
            if int(i) not in seen:
                seen.add(int(i))
                out.append(int(i))
        self.members = json.dumps(out)


class BZ_PG_script_var(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", description="The variable's name, for you; a trigger names a variable by its number")


def _owner(self):
    return self.id_data          # the map's collection that holds this property group


def _group_name_get(self):
    for g in _owner(self).bz_script_groups:
        if g.gid == self.val:
            return g.name
    return ""


def _group_name_set(self, value):
    for g in _owner(self).bz_script_groups:
        if g.name == value:
            self.val = g.gid
            return


def _var_name_get(self):
    vs = _owner(self).bz_script_vars
    return vs[self.val].name if 0 <= self.val < len(vs) else ""


def _var_name_set(self, value):
    for i, v in enumerate(_owner(self).bz_script_vars):
        if v.name == value:
            self.val = i
            return


def _text_get(self):
    for t in _owner(self).bz_script_texts:
        if t.tid == self.val:
            return t.name
    return ""


def _text_set(self, value):
    for t in _owner(self).bz_script_texts:
        if t.name == value:
            self.val = t.tid
            return


def _bp_get(self):
    for b in _owner(self).bz_script_blueprints:
        if b.tid == self.val:
            return b.name
    return ""


def _bp_set(self, value):
    for b in _owner(self).bz_script_blueprints:
        if b.name == value:
            self.val = b.tid
            return


def _trigger_get(self):
    for t in _owner(self).bz_script_triggers:
        if t.tid == self.val:
            return t.name
    return ""


def _trigger_set(self, value):
    for t in _owner(self).bz_script_triggers:
        if t.name == value:
            self.val = t.tid
            return


def _dialog_get(self):
    for d in _owner(self).bz_script_dialogs:
        if d.did == self.val:
            return d.name
    return ""


def _dialog_set(self, value):
    for d in _owner(self).bz_script_dialogs:
        if d.name == value:
            self.val = d.did
            return


def _float_get(self):
    return lib()[1].val_to_float(self.val)


def _float_set(self, value):
    self.val = lib()[1].float_to_val(value)


def _bool_get(self):
    return self.val != 0


def _bool_set(self, value):
    self.val = 1 if value else 0


def _items(rows):
    return [(str(code), text, "") for code, text in rows]


def _choice(rows):
    """(get, set) for an enum property that shows the integer ``val`` as one of ``rows`` (``[(value, text)]``)."""
    def get(self):
        for k, (code, _t) in enumerate(rows):
            if code == self.val:
                return k
        return 0

    def setter(self, index):
        if 0 <= index < len(rows):
            self.val = rows[index][0]
    return get, setter


_PLAYER_GET, _PLAYER_SET = _choice(_E.PLAYER_CHOICES)
_COMPARE_GET, _COMPARE_SET = _choice(_E.COMPARE_CHOICES)
_VAROP_GET, _VAROP_SET = _choice(_E.VAROP_CHOICES)


class BZ_PG_script_arg(bpy.types.PropertyGroup):
    code: IntProperty(name="Kind", description="The engine's kind of value")
    val: IntProperty(name="Value")
    label: StringProperty(name="Label")
    as_float: FloatProperty(name="Value", get=_float_get, set=_float_set, precision=3)
    as_bool: BoolProperty(name="On", get=_bool_get, set=_bool_set)
    ref_group: StringProperty(name="Group", get=_group_name_get, set=_group_name_set)
    ref_var: StringProperty(name="Variable", get=_var_name_get, set=_var_name_set)
    ref_text: StringProperty(name="Text", get=_text_get, set=_text_set)
    ref_bp: StringProperty(name="Blueprint", get=_bp_get, set=_bp_set)
    ref_trigger: StringProperty(name="Trigger", get=_trigger_get, set=_trigger_set)
    ref_dialog: StringProperty(name="Dialog", get=_dialog_get, set=_dialog_set)
    as_player: EnumProperty(name="Player", items=_items(_E.PLAYER_CHOICES), get=_PLAYER_GET, set=_PLAYER_SET)
    as_compare: EnumProperty(name="Compare", items=_items(_E.COMPARE_CHOICES), get=_COMPARE_GET, set=_COMPARE_SET)
    as_varop: EnumProperty(name="Operation", items=_items(_E.VAROP_CHOICES), get=_VAROP_GET, set=_VAROP_SET)

    def player_known(self) -> bool:
        return any(c == self.val for c, _t in _E.PLAYER_CHOICES)


class BZ_PG_script_expr(bpy.types.PropertyGroup):
    """A condition or an action: ``name`` is the engine's name for it."""
    name: StringProperty(name="Engine name")
    args: CollectionProperty(type=BZ_PG_script_arg)
    controller: BoolProperty(name="Controller", description="Dialog actions: the next action waits until this one has finished")


def _folder_update(self, _context):
    """A folder name typed on a trigger makes that folder exist (an empty folder is kept too)."""
    top = self.id_data
    name = self.folder.strip()
    if name and name not in {f.name for f in top.bz_script_folders}:
        top.bz_script_folders.add().name = name


class BZ_PG_script_trigger(bpy.types.PropertyGroup):
    name: StringProperty(name="Name")
    folder: StringProperty(name="Folder", description="The folder the trigger sits in (letters, digits and underscores)", update=_folder_update)
    tid: IntProperty(name="Number")
    pset: IntProperty(name="Players")
    active: BoolProperty(name="Active", default=True, description="Active triggers are checked from the start; inactive "
                                                                  "ones wait to be switched on")
    looping: BoolProperty(name="Repeats", description="A repeating trigger fires again and again; otherwise it fires once")
    conditions: CollectionProperty(type=BZ_PG_script_expr)
    actions: CollectionProperty(type=BZ_PG_script_expr)
    cond_index: IntProperty()
    act_index: IntProperty()


def _run_bit(bit):
    def get(self):
        return bool(self.pset & bit)

    def setter(self, value):
        self.pset = _E.pset_toggle(self.pset, bit, bool(value))
    return BoolProperty(name=dict(_E.PSET_ROWS)[bit], get=get, set=setter)


for _bit, _label in _E.PSET_ROWS:                  # run_1 .. run_2048: who runs the trigger (the engine's player-set bits)
    BZ_PG_script_trigger.__annotations__[f"run_{_bit}"] = _run_bit(_bit)


class BZ_PG_script_dialog(bpy.types.PropertyGroup):
    name: StringProperty(name="Name")
    did: IntProperty(name="Number", description="The number the Play dialog action uses")
    triggers: CollectionProperty(type=BZ_PG_script_trigger)
    trig_index: IntProperty()


CLASSES = (BZ_PG_script_text, BZ_PG_script_group, BZ_PG_script_var, BZ_PG_script_arg, BZ_PG_script_expr, BZ_PG_script_trigger,
           BZ_PG_script_dialog)

_PROPS = {
    "bz_script_groups": lambda: CollectionProperty(type=BZ_PG_script_group),
    "bz_script_groups_index": lambda: IntProperty(),
    "bz_script_vars": lambda: CollectionProperty(type=BZ_PG_script_var),
    "bz_script_vars_index": lambda: IntProperty(),
    "bz_script_triggers": lambda: CollectionProperty(type=BZ_PG_script_trigger),
    "bz_script_triggers_index": lambda: IntProperty(),
    "bz_script_folders": lambda: CollectionProperty(type=BZ_PG_script_text),
    "bz_script_dialogs": lambda: CollectionProperty(type=BZ_PG_script_dialog),
    "bz_script_dialogs_index": lambda: IntProperty(),
    "bz_script_texts": lambda: CollectionProperty(type=BZ_PG_script_text),
    "bz_script_blueprints": lambda: CollectionProperty(type=BZ_PG_script_text),
    "bz_script_ai_scripts": lambda: CollectionProperty(type=BZ_PG_script_text),
    "bz_script_ai_armies": lambda: CollectionProperty(type=BZ_PG_script_text),
}


def register():
    for key, make in _PROPS.items():
        setattr(bpy.types.Collection, key, make())


def unregister():
    for key in _PROPS:
        if hasattr(bpy.types.Collection, key):
            delattr(bpy.types.Collection, key)


# ---------------------------------------------------------------------------------------------------- the placed objects of a map
def map_entities(top) -> dict:
    """``{entity id: object}`` of the placed objects of the map that carry a number from the map (a new object has none until the
    map is saved and opened again; a copy carries the number of its original: the first object found keeps it)."""
    out = {}
    for o in top.all_objects:
        if "ic_blueprint" in o and "ic_map_model" not in o and "ic_entity_id" in o:
            out.setdefault(int(o["ic_entity_id"]), o)
    return out


def entity_ids(top) -> set:
    return set(map_entities(top))


def selected_ids(context, top) -> tuple:
    """``(ids of the selected placed objects that have a number, how many selected placed objects have none yet)``."""
    ids, fresh, seen = [], 0, set()
    inside = {o.name for o in top.all_objects}
    for o in context.selected_objects:
        if o.name not in inside or "ic_blueprint" not in o or "ic_map_model" in o:
            continue
        if "ic_entity_id" in o:
            i = int(o["ic_entity_id"])
            if i not in seen:
                seen.add(i)
                ids.append(i)
        else:
            fresh += 1
    return ids, fresh


def object_label(top, eid: int) -> str:
    o = map_entities(top).get(int(eid))
    return o.name if o is not None else ""


def text_of(top, rid: int) -> str:
    for t in top.bz_script_texts:
        if t.tid == int(rid):
            return t.name
    return ""


def blueprint_label(path: str) -> str:
    """``structures/lab`` for ``DATA:ART\\EBPS\\STRUCTURES\\LAB.EBP``: the blueprint's place and name in plain words."""
    p = _S.norm_blueprint(path)
    if p.startswith("art/ebps/"):
        p = p[len("art/ebps/"):]
    return p[:-4] if p.endswith(".ebp") else p


def blueprint_of(top, bid: int) -> str:
    for b in top.bz_script_blueprints:
        if b.tid == int(bid):
            return b.name
    return ""


def arg_text(top, a) -> str:
    """One argument in words (for the row of a condition or action)."""
    E = lib()[1]
    kind = E.CODE_KIND.get(a.code, E.K_INT)
    if kind == E.K_GROUP:
        return _group_name_get(a) or f"group {a.val}"
    if kind == E.K_VARIABLE:
        return _var_name_get(a) or f"variable {a.val}"
    if kind == E.K_ENTITY:
        return object_label(top, a.val) or f"object {a.val}"
    if kind == E.K_PLAYER:
        return dict(E.PLAYER_CHOICES).get(a.val, f"player {a.val}")
    if kind == E.K_COMPARE:
        return {0: "<=", 1: ">=", 2: "==", 3: "!="}.get(a.val, f"? {a.val}")
    if kind == E.K_VAROP:
        return {0: "+", 1: "-"}.get(a.val, f"? {a.val}")
    if kind == E.K_BOOL:
        return "on" if a.val else "off"
    if kind == E.K_FLOAT:
        return f"{E.val_to_float(a.val):g}"
    if kind in (E.K_RESOURCE, E.K_TEXT):
        return text_of(top, a.val).rsplit("\\", 1)[-1] or str(a.val)
    if kind == E.K_BLUEPRINT:
        return blueprint_of(top, a.val) or f"blueprint {a.val}"
    if kind == E.K_TRIGGER:
        return _trigger_get(a) or f"trigger {a.val}"
    if kind == E.K_DIALOG:
        return _dialog_get(a) or f"dialog {a.val}"
    return str(a.val)


def expr_text(top, expr) -> str:
    E = lib()[1]
    head = E.display_name(expr.name) if E.is_known(expr.name) else f"{expr.name} (unknown)"
    args = ", ".join(arg_text(top, a) for a in expr.args)
    return f"{head}: {args}" if args else head


# ---------------------------------------------------------------------------------------------------- state <-> the library
def groups_state(top):
    """The :class:`script_edit.ScriptState` the panel holds."""
    S = lib()[0]
    st = S.ScriptState()
    st.groups = [S.GroupRec(int(g.gid), g.name, g.ids()) for g in top.bz_script_groups]
    st.variables = [v.name for v in top.bz_script_vars]
    if len(top.bz_script_ai_scripts):
        st.ai_scripts = [t.name for t in top.bz_script_ai_scripts]
    if len(top.bz_script_ai_armies):
        st.ai_armies = [t.name for t in top.bz_script_ai_armies]
    if top.get(KEY_HAS_TEXT):
        st.texts = [(int(t.tid), t.name) for t in top.bz_script_texts]
    return st


def fill_groups(top, st) -> None:
    top.bz_script_groups.clear()
    for g in st.groups:
        item = top.bz_script_groups.add()
        item.name, item.gid = g.name, int(g.gid)
        item.set_ids(g.members)
    top.bz_script_vars.clear()
    for name in st.variables:
        top.bz_script_vars.add().name = name
    for key, values in (("bz_script_ai_scripts", st.ai_scripts), ("bz_script_ai_armies", st.ai_armies)):
        coll = getattr(top, key)
        coll.clear()
        for v in values or []:
            coll.add().name = v
    top.bz_script_texts.clear()
    top[KEY_HAS_TEXT] = st.texts is not None
    for tid, text in st.texts or []:
        row = top.bz_script_texts.add()
        row.tid, row.name = int(tid), text


def _fill_expr(top, item, name: str, args, label_from_signature: bool = True) -> None:
    E = lib()[1]
    item.name = name
    sig = E.signature(name) if E.is_known(name) else []
    item.args.clear()
    for k, a in enumerate(args):
        it = item.args.add()
        it.code, it.val = int(a.type), int(a.val)
        spec = sig[k] if k < len(sig) and sig[k].code == it.code else None
        it.label = spec.label if spec else E.GENERIC_LABEL.get(it.code, "value")


def fill_triggers(top, doc) -> None:
    top.bz_script_triggers.clear()
    top.bz_script_folders.clear()
    for folder, trigs in doc.folders.items():
        top.bz_script_folders.add().name = folder
        for t in trigs:
            item = top.bz_script_triggers.add()
            _fill_trigger(top, item, t, folder)


def _fill_trigger(top, item, t, folder="") -> None:
    item.name, item.folder, item.tid = t.name, folder, int(t.id)
    item.pset, item.active, item.looping = int(t.pset), bool(t.active), bool(t.looping)
    item.conditions.clear()
    item.actions.clear()
    for c in t.conditions:
        _fill_expr(top, item.conditions.add(), c.type, c.args)
    for a in t.actions:
        _fill_expr(top, item.actions.add(), a.type, a.args)


def copy_expr(src, dst) -> None:
    """Copy one condition or action (its name, controller flag and arguments) into a new row ``dst``."""
    dst.name, dst.controller = src.name, src.controller
    dst.args.clear()
    for a in src.args:
        b = dst.args.add()
        b.code, b.val, b.label = a.code, a.val, a.label


def copy_trigger(src, dst) -> None:
    """Copy a trigger's own settings, conditions and actions into the new row ``dst`` (the number is not copied)."""
    dst.name, dst.folder = src.name + " copy", src.folder
    dst.pset, dst.active, dst.looping = src.pset, src.active, src.looping
    dst.conditions.clear()
    dst.actions.clear()
    for c in src.conditions:
        copy_expr(c, dst.conditions.add())
    for a in src.actions:
        copy_expr(a, dst.actions.add())


def triggers_doc(top, remap=None):
    """The :class:`trigger_edit.TriggerDoc` the panel holds (``remap``: {provisional blueprint number: the number the saved map gives it})."""
    _S, E, _D, T = lib()
    remap = remap or {}

    def val(a):
        return remap.get(int(a.val), int(a.val)) if a.code == T.ARG_EBP_TYPE else int(a.val)
    folders = {f.name: [] for f in top.bz_script_folders}
    for item in top.bz_script_triggers:
        t = T.Trigger(id=int(item.tid), name=item.name,
                      conditions=[T.Condition(c.name, [T.Arg(a.code, val(a)) for a in c.args]) for c in item.conditions],
                      actions=[T.Action(a.name, [T.Arg(x.code, val(x)) for x in a.args]) for a in item.actions],
                      pset=int(item.pset), active=1 if item.active else 0, looping=1 if item.looping else 0)
        folders.setdefault(item.folder or "default", []).append(t)
    return E.TriggerDoc(folders=folders)


def fill_dialogs(top, dlg) -> None:
    top.bz_script_dialogs.clear()
    for d in dlg.dialogs:
        item = top.bz_script_dialogs.add()
        item.name, item.did = d.name, int(d.id)
        for t in d.triggers:
            ti = item.triggers.add()
            ti.name, ti.tid = t.name, int(t.id)
            for a in t.actions:
                ai = ti.actions.add()
                _fill_expr(top, ai, a.type, [_T.Arg(c, v) for c, v in a.args])
                ai.controller = bool(a.controller)


def dialogs_file(top, remap=None):
    _S, _E, D, _T = lib()
    remap = remap or {}
    f = D.DialogFile()
    for item in top.bz_script_dialogs:
        d = D.Dialog(item.name, int(item.did))
        for ti in item.triggers:
            d.triggers.append(D.DTrigger(int(ti.tid), ti.name, [D.DAction(a.name, [(x.code, remap.get(int(x.val), int(x.val)) if x.code == 13 else int(x.val)) for x in a.args],
                                                                1 if a.controller else 0)
                                                               for a in ti.actions]))
        f.dialogs.append(d)
    return f


def _digest(model) -> str:
    return hashlib.sha1(repr(model).encode("utf-8", "replace")).hexdigest()


def fingerprints(top) -> dict:
    """What each part holds now, as a short hash: the import records these, a save compares."""
    S, _E, _D, _T = lib()
    return {"groups": _digest(groups_state(top).model()), "triggers": _digest(triggers_doc(top).model()),
            "dialogs": _digest(dialogs_file(top).model())}


def recorded_fingerprints(top) -> dict:
    try:
        return json.loads(top[KEY_FINGER])
    except (KeyError, ValueError, TypeError):
        return {}


# ---------------------------------------------------------------------------------------------------- reading the files beside the map
def read_beside(top, game_dir, ext: str, siblings: dict | None = None):
    """The bytes of the file that sits beside the map it was opened from (``.trg``, ``.dlg``), or None. A map made in memory (a crop, a
    terrain import) has no file: its files come from ``siblings`` (``ictx.siblings``)."""
    from . import ops_map_import as M
    src = top.get(M.KEY_SOURCE, "")
    if not src:
        for (sub, rest), data in (siblings or {}).items():
            if not sub and rest.lower() == ext and data:
                return bytes(data)
        return None
    try:
        if "|" in src:
            import ICParse.sga as sga
            from ICParse.sgb import view
            archive, member = src.split("|", 1)
            wanted = member.rsplit(".", 1)[0].lower() + ext
            for path in view.game_archives(game_dir or ""):
                if os.path.basename(path).lower() == archive.lower():
                    arc = sga.read(path)
                    entry = arc.entry(wanted)
                    return arc.read(entry) if entry is not None else None
            return None
        base = os.path.splitext(src)[0]
        folder, stem = os.path.dirname(base), os.path.basename(base).lower()
        for n in os.listdir(folder):
            if n.lower() == stem + ext:
                with open(os.path.join(folder, n), "rb") as f:
                    return f.read()
    except (OSError, ValueError):
        return None
    return None


# ---------------------------------------------------------------------------------------------------- the import step
def import_step(ictx) -> None:
    S, E, D, _T = lib()
    top = ictx.top
    st = S.read_state(ictx.source_bytes)
    fill_groups(top, st)
    trg = read_beside(top, ictx.game_dir, ".trg", ictx.siblings)
    doc = E.TriggerDoc.from_text(trg) if trg else E.TriggerDoc(folders={})
    fill_triggers(top, doc)
    dlg_raw = read_beside(top, ictx.game_dir, ".dlg", ictx.siblings)
    dlg = D.DialogFile.from_text(dlg_raw) if dlg_raw else D.DialogFile()
    fill_dialogs(top, dlg)
    top[KEY_FINGER] = json.dumps(fingerprints(top))
    ids = {int(e.instance_id) for e in ictx.board.entities}
    top[KEY_BASE] = json.dumps({"entities": sorted(ids), "groups": sorted(int(g.gid) for g in st.groups)})
    top.bz_script_blueprints.clear()
    for bid, path in sorted(ictx.board.blueprint_palette.items()):
        row = top.bz_script_blueprints.add()
        row.tid, row.path, row.name = int(bid), _S.norm_blueprint(path), blueprint_label(path)
    top[KEY_LOADED] = True
    ictx.report["scripts"] = {"groups": len(st.groups), "variables": len(st.variables),
                              "trigger_folders": len(doc.folders), "triggers": len(doc.all_triggers()), "dialogs": len(dlg.dialogs),
                              "dialog_file_editable": not dlg.readonly_reason}
    if dlg.readonly_reason:
        ictx.warn(dlg.readonly_reason)


# ---------------------------------------------------------------------------------------------------- the save step
def save_step(sctx) -> None:
    top = sctx.top
    if not top.get(KEY_LOADED):
        return                                              # a map the scripting was never loaded for: nothing to write
    S, E, D, _T = lib()
    was = recorded_fingerprints(top)
    now = fingerprints(top)
    # ---- groups, variable names, AI scripts and armies: the map file
    if now["groups"] != was.get("groups"):
        state = groups_state(top)
        ids = {int(e.instance_id) for e in _final_board(sctx).entities}
        bad = S.validate(state, S.read_state(sctx.original))
        if bad:
            sctx.refuse("The groups and variables cannot be saved: " + bad[0])
        try:
            sctx.sgb = S.write_state(sctx.sgb, state, entity_ids=ids)
        except S.ScriptError as exc:
            sctx.refuse(f"The groups and variables cannot be saved: {exc}")
        orig = S.read_state(sctx.original)
        new = S.read_state(sctx.sgb)
        if [g.key() for g in new.groups] != [g.key() for g in orig.groups]:
            sctx.add_change(f"Groups: {len(new.groups)} named groups written ({len(orig.groups)} when the map was opened).")
        if new.variables != orig.variables:
            sctx.add_change(f"Variables: {sum(1 for a, b in zip(orig.variables, new.variables) if a != b)} renamed.")
        if (new.ai_scripts, new.ai_armies) != (orig.ai_scripts, orig.ai_armies):
            sctx.add_change("AI scripts and armies: the per-player paths were changed.")
        for line in S.name_warnings(new):
            sctx.add_warning(line)
    # ---- blueprints a trigger or dialog uses that the map's list does not hold yet go into the list now
    remap = _blueprint_remap(sctx, top)
    # ---- triggers: <name>.trg
    if now["triggers"] != was.get("triggers"):
        doc = triggers_doc(top, remap)
        key = f"{sctx.name}.trg"
        base = sctx.files.get(key) or sctx.initial_files.get(key)
        try:
            for folder, t in doc.all_triggers():
                E.check_trigger_name(t.name)
            for folder in doc.folders:
                E.check_folder_name(folder)
        except E.TriggerError as exc:
            sctx.refuse(f"The triggers cannot be saved: {exc}")
        if base:
            before = E.TriggerDoc.from_text(base)
            doc.header, doc.line_ending, doc.source = before.header, before.line_ending, before.source
        sctx.files[key] = doc.to_text().encode("latin1")
        sctx.add_change(f"Triggers: {len(doc.all_triggers())} triggers in {len(doc.folders)} folders written to {key}.")
        stuck = [p for p in E.problems(doc, entity_ids=entity_ids(top), group_ids={int(g.gid) for g in top.bz_script_groups})
                 if p["severity"] == "error"]
        for p in stuck[:5]:
            sctx.add_warning(f"Triggers: {p['where']}: {p['text']}")
    # ---- dialog triggers: <name>.dlg
    if now["dialogs"] != was.get("dialogs"):
        f = dialogs_file(top, remap)
        key = f"{sctx.name}.dlg"
        base = sctx.files.get(key) or sctx.initial_files.get(key)
        try:
            for d in f.dialogs:
                D.check_dialog_name(d.name)
                for t in d.triggers:
                    D.check_text(t.name)
        except D.DialogError as exc:
            sctx.refuse(f"The dialogs cannot be saved: {exc}")
        if base:
            before = D.DialogFile.from_text(base)
            if before.readonly_reason:
                sctx.refuse(before.readonly_reason + " Its dialogs cannot be changed here.")
            f.line_ending, f.extras = before.line_ending, before.extras
        sctx.files[key] = f.to_text().encode("latin1")
        sctx.add_change(f"Dialogs: {len(f.dialogs)} dialogs written to {key}.")


def _blueprint_remap(sctx, top) -> dict:
    """``{provisional number: the number in the saved map}`` for every blueprint a trigger or dialog argument uses that the map's own list
    did not hold when it was opened; the blueprints are added to the map's list (``script_edit.add_blueprints``)."""
    pend = {int(b.tid): b.path for b in top.bz_script_blueprints if int(b.tid) >= PENDING_BASE}
    if not pend:
        return {}
    used = set()
    for item in top.bz_script_triggers:
        for e in list(item.conditions) + list(item.actions):
            used |= {int(a.val) for a in e.args if a.code == _T.ARG_EBP_TYPE and int(a.val) in pend}
    for d in top.bz_script_dialogs:
        for t in d.triggers:
            for e in t.actions:
                used |= {int(a.val) for a in e.args if a.code == _T.ARG_EBP_TYPE and int(a.val) in pend}
    if not used:
        return {}
    order = sorted(used)
    try:
        sctx.sgb, ids = _S.add_blueprints(sctx.sgb, [pend[i] for i in order])
    except _S.ScriptError as exc:
        sctx.refuse(f"A blueprint used by a trigger cannot be added to the map: {exc}")
    sctx.add_change("Blueprint list: " + ", ".join(blueprint_label(pend[i]) for i in order) + " added for the triggers.")
    return {i: ids[_S.norm_blueprint(pend[i])] for i in order}


def _final_board(sctx):
    from ICParse.sgb import parser
    return parser.read(sctx.sgb)


H.register_save_step(STEP, save_step, order=120)
H.register_import_step(STEP, import_step, order=120)


# ---------------------------------------------------------------------------------------------------- the check
def check(cctx) -> list:
    """Triggers that name an object, a group or a variable the map does not have; a condition or action the game does not know; a
    trigger no player runs; groups with members the map no longer has."""
    top = cctx.top
    if not top.get(KEY_LOADED):
        return []
    _S, E, _D, _T = lib()
    try:
        base = json.loads(top[KEY_BASE])
    except (KeyError, ValueError, TypeError):
        base = {}
    doc = triggers_doc(top)
    ents = entity_ids(top)
    groups = {int(g.gid) for g in top.bz_script_groups}
    names = {int(g.gid): g.name for g in top.bz_script_groups}
    dids = {int(d.did) for d in top.bz_script_dialogs}
    texts = {int(t.tid) for t in top.bz_script_texts} if top.get(KEY_HAS_TEXT) else None
    found = E.problems(doc, entity_ids=ents, group_ids=groups, baseline_entity_ids=base.get("entities"), baseline_group_ids=base.get("groups"),
                       group_names=names, dialog_ids=dids, text_ids=texts)
    out = [H.problem(p["severity"], p["text"], p["where"]) for p in found]
    gone = {i for g in top.bz_script_groups for i in g.ids()} - ents
    gone &= set(base.get("entities", []))
    if gone:
        out.append(H.problem("warning", f"{len(gone)} objects in groups are not in the map any more; they leave their groups when the map is saved.",
                             "Groups"))
    return out


H.register_check(STEP, check)
