"""Scripting a map (sceneobj/map_script.py, ops_map_script.py, panels_map_script.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_script_test.py     (or: run_all.py -k so_map_script)

1. the add-on loads the three parts and the panels are registered; the steps and the check are registered;
2. opening stock maps loads the groups, the 256 variables, the triggers and the dialogs of the map and its files, and an UNTOUCHED save of
   three maps (2p_faceoff, mission1, 6p_big_bog) writes the map and every file beside it byte for byte;
3. groups: create from the selected objects, add / remove / regroup / select, rename, delete; the save writes only the group records
   (every other record of the map is byte for byte the same); an object deleted in Blender leaves its groups;
4. variables: rename; AI scripts: set a path; both saved into the map file only;
5. triggers: add, name, folder, players, conditions and actions from the full list, typed arguments, move, delete; the saved .trg
   parses back to exactly the edited triggers and every other trigger of the file is unchanged;
6. the check finds a trigger that names an object or a group the user deleted (an error), and the stock map's own dangling references
   only as warnings;
7. dialog triggers: add a dialog, a step, an action with a controller; the saved .dlg reads back; the other dialogs are unchanged;
8. a map made in memory: untouched it writes no .trg; with a trigger added it writes <name>.trg;
9. the panels draw (fake layout) without errors and offer the operators.
nothing under the game folder changed.
"""
import json
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)

FACEOFF = "Engine.sga|scenarios/mp/2p_faceoff.sgb"
MISSION1 = "Engine.sga|scenarios/sp/mission1.sgb"
MISSION3 = "Engine.sga|scenarios/sp/mission3.sgb"
BIGBOG = "InsectData.sga|scenarios/mp/6p_big_bog.sgb"


def section(title):
    print(f"-- {title}", flush=True)


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def open_map(key):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=False)
    assert "FINISHED" in res, res
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def beside(game_dir, key, ext):
    import ICParse.sga as sga
    gm = game_map(game_dir, key)
    arc = sga.read(gm.path)
    e = arc.entry(gm.member[:-4] + ext)
    return arc.read(e) if e is not None else None


def folder_files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _s, fs in os.walk(folder) for f in fs)


def save(name, **kw):
    out = C.tmpdir("script_")
    res = bpy.ops.bz.export_map(filepath=os.path.join(out, name + ".sgb"), **kw)
    assert "FINISHED" in res, res
    return out


def read(out, name):
    with open(os.path.join(out, name), "rb") as f:
        return f.read()


def select(top, ids):
    from blender_native.sceneobj import map_script as MS
    for o in bpy.context.selected_objects:
        o.select_set(False)
    objs = MS.map_entities(top)
    for i in ids:
        objs[i].select_set(True)
        assert objs[i].select_get(), i
    bpy.context.view_layer.objects.active = objs[ids[0]]


def leaves_except(raw, skip_tags=("ESGP", "TVAR"), skip_markers=(b"AISC",)):
    from ICParse.iff.document import IffDocument
    d = IffDocument.from_bytes(raw)
    return [(c.tag, bytes(c.raw)) for c in d.all_chunks() if not hasattr(c, "children") and c.tag not in skip_tags
            and not (c.tag == "NRML" and c.raw[:4] in skip_markers)]


def test_loading():
    from blender_native import sceneobj
    from blender_native.sceneobj import map_hooks as H
    names = [m.__name__.rsplit(".", 1)[-1] for m in sceneobj.MODULES]
    for n in ("map_script", "ops_map_script", "panels_map_script"):
        assert n in names, names
    assert not sceneobj.ERRORS, sceneobj.ERRORS
    reg = H.registered()
    assert "script" in reg["import"] and "script" in reg["save"] and "script" in reg["checks"], reg
    for pid in ("BZ_PT_map_script_groups", "BZ_PT_map_script_triggers", "BZ_PT_map_script_dialogs", "BZ_PT_map_script_ai"):
        cls = getattr(bpy.types, pid)
        assert cls.bl_parent_id == H.MAP_PANEL_ID == "BZ_PT_map"
    for op in ("script_group_new", "script_trigger_new", "script_expr_add", "script_dialog_new", "script_check"):
        assert hasattr(bpy.ops.bz, op), op


def test_untouched(game_dir):
    from blender_native.sceneobj import map_script as MS
    from ICParse.sgb import trigger_edit as E
    for key, name in ((FACEOFF, "2p_faceoff"), (MISSION1, "mission1"), (BIGBOG, "6p_big_bog")):
        t0 = time.perf_counter()
        gm = game_map(game_dir, key)
        raw = gm.read()
        top = open_map(key)
        assert top.get(MS.KEY_LOADED) and len(top.bz_script_vars) == 256
        trg = beside(game_dir, key, ".trg")
        doc = E.TriggerDoc.from_text(trg)
        assert len(top.bz_script_triggers) == len(doc.all_triggers()), (len(top.bz_script_triggers), len(doc.all_triggers()))
        assert [f.name for f in top.bz_script_folders] == list(doc.folders)
        out = save(name)
        assert read(out, name + ".sgb") == raw, f"{key}: the map was not saved byte for byte"
        for ext in (".trg", ".dlg", ".na"):
            was = beside(game_dir, key, ext)
            if was is not None:
                assert read(out, name + ext) == was, f"{key}: {ext} changed"
        print(f"   {name}: {len(top.bz_script_groups)} groups, {len(top.bz_script_triggers)} triggers in {len(top.bz_script_folders)} folders, "
              f"{len(top.bz_script_dialogs)} dialogs read; untouched save byte for byte ({len(raw)} bytes, {len(folder_files(out)) - 1} files "
              f"beside it) in {time.perf_counter() - t0:.1f} s", flush=True)


def test_groups_variables_ai(game_dir):
    from blender_native.sceneobj import map_script as MS
    from ICParse.sgb import parser, script_edit as S
    gm = game_map(game_dir, MISSION3)
    raw = gm.read()
    top = open_map(MISSION3)
    before = S.read_state(raw)
    n0 = len(top.bz_script_groups)
    objs = MS.map_entities(top)
    free = [i for i in objs if not any(i in g.ids() for g in top.bz_script_groups)]
    pick = free[:3]
    select(top, pick)
    assert "FINISHED" in bpy.ops.bz.script_group_new(name="My squad")
    assert len(top.bz_script_groups) == n0 + 1
    g = top.bz_script_groups[top.bz_script_groups_index]
    assert g.name == "My squad" and g.ids() == pick and g.gid == max(x.gid for x in before.groups) + 1
    # add / remove / regroup / select
    select(top, free[3:5])
    assert "FINISHED" in bpy.ops.bz.script_group_members(action="ADD") and g.ids() == pick + free[3:5]
    select(top, [free[0]])
    assert "FINISHED" in bpy.ops.bz.script_group_members(action="REMOVE") and free[0] not in g.ids()
    select(top, free[5:7])
    assert "FINISHED" in bpy.ops.bz.script_group_members(action="SET") and g.ids() == free[5:7]
    assert "FINISHED" in bpy.ops.bz.script_group_members(action="SELECT")
    assert {o.get("ic_entity_id") for o in bpy.context.selected_objects} == set(free[5:7])
    g.name = "Renamed squad"
    my_gid = g.gid
    # an object that is not a placed object of the map is refused
    try:
        bpy.ops.bz.script_group_members(action="ADD")                # (the selection is the group's own members: fine, nothing new)
    except RuntimeError:
        raise AssertionError("adding members already in the group must be allowed")
    # delete the second group, rename a variable, set an AI path
    victim = top.bz_script_groups[1].gid                              # (stock groups can share a name: go by number)
    top.bz_script_groups_index = 1
    assert "FINISHED" in bpy.ops.bz.script_group_delete() and all(x.gid != victim for x in top.bz_script_groups)
    top.bz_script_vars[5].name = "kills"
    top.bz_script_ai_scripts[2].name = "DATA:SCENARIOS\\SP\\MY_TEST_AI.LUA"
    out = save("mission3")
    new = read(out, "mission3.sgb")
    st = S.read_state(new)
    assert st.variables[5] == "kills" and st.ai_scripts[2] == "DATA:SCENARIOS\\SP\\MY_TEST_AI.LUA" and st.ai_scripts[0] == before.ai_scripts[0]
    names = [x.name for x in st.groups]
    assert "Renamed squad" in names and victim not in [x.gid for x in st.groups] and len(st.groups) == n0
    mine = next(x for x in st.groups if x.name == "Renamed squad")
    assert mine.members == free[5:7] and mine.gid == my_gid
    assert leaves_except(new) == leaves_except(raw), "records other than groups, variables and the AI scripts changed"
    assert st.ai_armies == before.ai_armies
    trg = beside(game_dir, MISSION3, ".trg")
    assert read(out, "mission3.trg") == trg, "the trigger file changed with a group edit"
    # the untouched groups keep their exact records
    from ICParse.iff.document import IffDocument
    old = {bytes(c.raw) for c in IffDocument.from_bytes(raw).all_chunks() if c.tag == "ESGP"}
    kept = [bytes(c.raw) for c in IffDocument.from_bytes(new).all_chunks() if c.tag == "ESGP"]
    assert sum(1 for k in kept if k in old) == len(kept) - 1
    print(f"   mission3: a group made, grown, shrunk, regrouped, renamed; another deleted; a variable renamed and an AI script set: only those records "
          f"changed ({len(new) - len(raw):+d} bytes)", flush=True)
    # an object deleted in Blender leaves its groups
    top = open_map(MISSION3)
    gm_ = next(x for x in top.bz_script_groups if len(x.ids()) >= 1)
    gone = gm_.ids()[0]
    objs = MS.map_entities(top)
    bpy.data.objects.remove(objs[gone], do_unlink=True)
    out = save("mission3")
    st = S.read_state(read(out, "mission3.sgb"))
    assert all(gone not in x.members for x in st.groups), "a deleted object must leave every group"
    assert gone not in {e.instance_id for e in parser.read(read(out, "mission3.sgb")).entities}


def test_triggers(game_dir):
    from blender_native.sceneobj import map_script as MS
    from blender_native.sceneobj import map_hooks as H
    from ICParse.sgb import trigger_edit as E, triggers as T
    top = open_map(MISSION1)
    orig = E.TriggerDoc.from_text(beside(game_dir, MISSION1, ".trg"))
    n0 = len(top.bz_script_triggers)
    top.bz_script_triggers_index = 0
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="Win when the lab dies", folder="MY_FOLDER")
    t = top.bz_script_triggers[top.bz_script_triggers_index]
    assert t.name == "Win when the lab dies" and t.folder == "MY_FOLDER" and len(t.conditions) == 1 and t.conditions[0].name == "Always"
    assert t.tid == max(x.id for _f, x in orig.all_triggers()) + 1 and "MY_FOLDER" in [f.name for f in top.bz_script_folders]
    path = t.path_from_id()
    # conditions and actions from the full list
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="conditions", op="GroupNotAlive")
    c = t.conditions[t.cond_index]
    assert c.name == "GroupNotAlive" and [(a.code, a.label) for a in c.args] == [(12, "group")]
    lab = next(g for g in top.bz_script_groups if g.name == "Lab")
    c.args[0].ref_group = "Lab"
    assert c.args[0].val == lab.gid and c.args[0].ref_group == "Lab"
    t.conditions.remove(0)                                          # drop 'Always'
    t.cond_index = 0
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="Win")
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="GivePlayerScrap")
    t.act_index = 0
    win, scrap = t.actions[0], t.actions[1]
    win.args[0].as_player = "0"
    assert win.args[0].val == 0
    scrap.args[0].as_player = "13"
    scrap.args[1].val = 250
    assert scrap.args[0].val == 13
    assert "FINISHED" in bpy.ops.bz.script_expr_move(owner=path, which="actions", delta=1) and t.actions[1].name == "Win"
    assert "FINISHED" in bpy.ops.bz.script_expr_move(owner=path, which="actions", delta=-1) and t.actions[0].name == "Win"
    # an entity argument picked from the selected object
    eids = sorted(MS.entity_ids(top))
    select(top, [eids[0]])
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="DespawnEntity")
    despawn = t.actions[t.act_index]
    assert "FINISHED" in bpy.ops.bz.script_pick_entity(path=despawn.args[0].path_from_id()) and despawn.args[0].val == eids[0]
    # players, flags, a float
    t.run_16 = True
    t.run_1 = False
    t.looping = True
    assert t.pset == 16 and t.active
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="Group speed override")
    sp = t.actions[t.act_index]
    sp.args[1].as_float = 2.5
    assert sp.args[1].val == E.float_to_val(2.5) and abs(sp.args[1].as_float - 2.5) < 1e-6
    # copy it
    n_before = len(top.bz_script_triggers)
    idx = top.bz_script_triggers_index
    assert "FINISHED" in bpy.ops.bz.script_trigger_copy()
    cp = top.bz_script_triggers[top.bz_script_triggers_index]
    assert top.bz_script_triggers_index == idx + 1 and cp.name == "Win when the lab dies copy" and cp.tid == max(x.tid for x in top.bz_script_triggers)
    assert cp.tid != top.bz_script_triggers[idx].tid and len(cp.actions) == len(top.bz_script_triggers[idx].actions) and cp.pset == 16
    assert [MS.expr_text(top, e) for e in cp.actions] == [MS.expr_text(top, e) for e in top.bz_script_triggers[idx].actions]
    top.bz_script_triggers_index = idx + 1
    assert "FINISHED" in bpy.ops.bz.script_trigger_delete() and len(top.bz_script_triggers) == n_before
    top.bz_script_triggers_index = idx
    t = top.bz_script_triggers[idx]
    # a second new trigger then delete it; move one
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="temp", folder="MY_FOLDER")
    assert "FINISHED" in bpy.ops.bz.script_trigger_move(delta=-1)
    assert "FINISHED" in bpy.ops.bz.script_trigger_delete()
    assert len(top.bz_script_triggers) == n0 + 1
    # the saved .trg
    out = save("mission1")
    text = read(out, "mission1.trg")
    back = E.TriggerDoc.from_text(text)
    mine = next(x for _f, x in back.all_triggers() if x.name == "Win when the lab dies")
    assert [(c.type, [(a.type, a.val) for a in c.args]) for c in mine.conditions] == [("GroupNotAlive", [(12, lab.gid)])]
    acts = [(a.type, [(x.type, x.val) for x in a.args]) for a in mine.actions]
    assert acts[0] == ("Win", [(3, 0)]) and acts[1] == ("GivePlayerScrap", [(3, 13), (0, 250)]) and ("DespawnEntity", [(2, eids[0])]) in acts, acts
    assert acts[-1] == ("Group speed override", [(12, 0), (1, E.float_to_val(2.5))]), acts
    assert mine.pset == 16 and mine.looping == 1 and mine.active == 1
    assert list(back.folders)[-1] == "MY_FOLDER" and list(back.folders)[:-1] == list(orig.folders)
    # every other trigger is exactly as it was, folder by folder
    for f, trigs in orig.folders.items():
        a = [(x.id, x.name, x.pset, x.active, x.looping, [(c.type, [(q.type, q.val) for q in c.args]) for c in x.conditions],
              [(c.type, [(q.type, q.val) for q in c.args]) for c in x.actions]) for x in trigs]
        b = [(x.id, x.name, x.pset, x.active, x.looping, [(c.type, [(q.type, q.val) for q in c.args]) for c in x.conditions],
              [(c.type, [(q.type, q.val) for q in c.args]) for c in x.actions]) for x in back.folders[f]]
        assert a == b, f
    assert read(out, "mission1.sgb") == game_map(game_dir, MISSION1).read(), "a trigger edit must not change the map file"
    assert "Triggers:" in bpy.data.texts["Map save mission1"].as_string()
    print(f"   mission1: a trigger with a group condition, four typed actions, players and a decimal saved to mission1.trg "
          f"({len(text)} bytes); the other {n0} triggers and the map file are unchanged", flush=True)


def test_texts_and_blueprints(game_dir):
    from blender_native.sceneobj import map_script as MS
    from blender_native.sceneobj import ops_map_export as X
    from ICParse.sgb import parser, script_edit as S, trigger_edit as E
    gm = game_map(game_dir, MISSION1)
    raw = gm.read()
    top = open_map(MISSION1)
    before = S.read_state(raw)
    assert [(t.tid, t.name) for t in top.bz_script_texts] == before.texts and top.get(MS.KEY_HAS_TEXT)
    assert [(b.tid, b.path) for b in top.bz_script_blueprints] == [(i, S.norm_blueprint(p)) for i, p in sorted(S.blueprint_palette(raw).items())]
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="Talk and build", folder="MY_FOLDER")
    t = top.bz_script_triggers[top.bz_script_triggers_index]
    path = t.path_from_id()
    # a text: a new line, then the same line again (it must reuse the number), and an edit of an existing text
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="Show NISlet text")
    show = t.actions[t.act_index]
    kinds = [E.CODE_KIND.get(a.code) for a in show.args]
    arg = next(a for a in show.args if E.CODE_KIND.get(a.code) in (E.K_TEXT, E.K_RESOURCE))
    new_id = max(t_.tid for t_ in top.bz_script_texts) + 1
    assert "FINISHED" in bpy.ops.bz.script_text_new(path=arg.path_from_id(), text="REX: a brand new line.")
    assert arg.val == new_id and arg.ref_text == "REX: a brand new line."
    assert "FINISHED" in bpy.ops.bz.script_text_new(path=arg.path_from_id(), text="REX: a brand new line.") and arg.val == new_id
    top.bz_script_texts[0].name = "LUCY: an edited first line."
    # a blueprint of the map's own list, then one the list does not have
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="PlayerCanBuild")
    can = t.actions[t.act_index]
    bp = next(a for a in can.args if a.code == 13)
    pal = {b.path: b.tid for b in top.bz_script_blueprints}
    own = next(p for p in pal if p.endswith("structures/lab.ebp"))
    game_listing = X.game_listing(game_dir, top.get("ic_map_archive", ""))["blueprints"]
    refs = [r for r, _k in game_listing if S.norm_blueprint(r) not in pal and S.norm_blueprint(r).startswith("art/ebps/stock/")]
    assert refs, "the game lists stock blueprints the map does not have"
    assert "FINISHED" in bpy.ops.bz.script_pick_blueprint(path=bp.path_from_id(), blueprint=next(r for r, _k in game_listing if S.norm_blueprint(r) == own))
    assert bp.val == pal[own], "a blueprint the map has keeps its number"
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="PlayerCannotBuild")
    cannot = t.actions[t.act_index]
    bp2 = next(a for a in cannot.args if a.code == 13)
    assert "FINISHED" in bpy.ops.bz.script_pick_blueprint(path=bp2.path_from_id(), blueprint=refs[0])
    assert bp2.val >= MS.PENDING_BASE and next(b.path for b in top.bz_script_blueprints if b.tid == bp2.val) == S.norm_blueprint(refs[0]), bp2.val
    out = save("mission1")
    new = read(out, "mission1.sgb")
    st = S.read_state(new)
    assert st.texts[0] == (before.texts[0][0], "LUCY: an edited first line.") and st.texts[1:len(before.texts)] == before.texts[1:]
    assert st.texts[-1] == (new_id, "REX: a brand new line.") and len(st.texts) == len(before.texts) + 1
    pal2 = S.blueprint_palette(new)
    added = [p for i, p in pal2.items() if i not in S.blueprint_palette(raw)]
    assert len(added) == 1 and S.norm_blueprint(added[0]) == S.norm_blueprint(refs[0]), added
    new_bid = next(i for i, p in pal2.items() if i not in S.blueprint_palette(raw))
    doc = E.TriggerDoc.from_text(read(out, "mission1.trg"))
    mine = next(x for _f, x in doc.all_triggers() if x.name == "Talk and build")
    got = {a.type: [(x.type, x.val) for x in a.args] for a in mine.actions}
    assert (13, own and pal[own]) in got["PlayerCanBuild"] and (13, new_bid) in got["PlayerCannotBuild"], (got, pal[own], new_bid)
    assert (arg.code, new_id) in got["Show NISlet text"]
    assert parser.read(new).entities == parser.read(raw).entities, "the placed objects changed"
    def other_leaves(b):
        import collections
        from ICParse.iff.document import IffDocument
        return collections.Counter((c.tag, bytes(c.raw)) for c in IffDocument.from_bytes(b).all_chunks()
                                   if not hasattr(c, "children") and not (c.tag == "NRML" and c.raw[:4] == b"TEXT"))
    old_l, new_l = other_leaves(raw), other_leaves(new)
    assert not (old_l - new_l), "a record of the map changed or went"
    assert sum((new_l - old_l).values()) == 3, "only the new blueprint's three records (version, number, path) were added"
    print(f"   mission1: a new text and an edited one written to the text table; a trigger's blueprint taken from the map's list and one the list "
          f"lacked (added to it as number {new_bid})", flush=True)
    # the check knows a text number the table lacks
    from blender_native.sceneobj import map_hooks as H
    arg.val = 999999
    found = [p for p in H.run_checks(bpy.context, top) if p["check"] == "script"]
    assert any("Text number 999999" in p["text"] for p in found), found


def test_checks(game_dir):
    from blender_native.sceneobj import map_script as MS
    from blender_native.sceneobj import map_hooks as H
    from ICParse.sgb import trigger_edit as E
    top = open_map(MISSION1)
    base = [p for p in H.run_checks(bpy.context, top) if p["check"] == "script"]
    assert all(p["severity"] == "warning" for p in base), [p for p in base if p["severity"] == "error"][:3]
    n_warn = len(base)
    # an object a trigger names, then deleted
    doc = MS.triggers_doc(top)
    ents = MS.map_entities(top)
    hit = None
    for f, t in doc.all_triggers():
        for c in t.conditions + t.actions:
            for a in c.args:
                if a.type == 2 and a.val in ents:
                    hit = (t, c, a.val)
                    break
            if hit:
                break
        if hit:
            break
    assert hit is not None
    bpy.data.objects.remove(ents[hit[2]], do_unlink=True)
    found = [p for p in H.run_checks(bpy.context, top) if p["check"] == "script"]
    errors = [p for p in found if p["severity"] == "error"]
    assert errors and f"Object {hit[2]}" in errors[0]["text"] and "deleted" in errors[0]["text"] and hit[0].name in errors[0]["where"], errors
    # a group a trigger names, then deleted
    gid = next(a.val for f, t in doc.all_triggers() for c in t.conditions + t.actions for a in c.args
               if a.type == 12 and any(g.gid == a.val for g in top.bz_script_groups))
    idx = next(i for i, g in enumerate(top.bz_script_groups) if g.gid == gid)
    top.bz_script_groups_index = idx
    assert "FINISHED" in bpy.ops.bz.script_group_delete()
    found = [p for p in H.run_checks(bpy.context, top) if p["check"] == "script"]
    assert any(p["severity"] == "error" and f"Group {gid}" in p["text"] for p in found), found
    # the operator keeps the problems for the panel
    assert "FINISHED" in bpy.ops.bz.script_check() and MS.LAST_CHECK[top.name]
    print(f"   mission1: {n_warn} warnings and no error as opened; deleting object {hit[2]} and group {gid} gives errors that name them", flush=True)


def test_dialogs(game_dir):
    from blender_native.sceneobj import map_script as MS
    from ICParse.sgb import dialog_edit as D
    top = open_map(MISSION1)
    raw = beside(game_dir, MISSION1, ".dlg")
    orig = D.DialogFile.from_text(raw)
    n0 = len(top.bz_script_dialogs)
    assert n0 == len(orig.dialogs) > 5
    assert "FINISHED" in bpy.ops.bz.script_dialog_new(name="my_dialog")
    d = top.bz_script_dialogs[top.bz_script_dialogs_index]
    assert d.did == max(x.id for x in orig.dialogs) + 1
    assert "FINISHED" in bpy.ops.bz.script_dtrigger(action="NEW")
    t = d.triggers[d.trig_index]
    t.name = "say it"
    path = t.path_from_id()
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="Countdown action")
    t.actions[0].args[0].val = 3
    t.actions[0].controller = True
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=path, which="actions", op="PlayAnimation")
    assert len(t.actions) == 2 and t.actions[1].controller is False
    try:
        bpy.ops.bz.script_dialog_new(name="bad name")
    except RuntimeError as exc:
        assert "letters, digits and underscores" in str(exc)
    else:
        raise AssertionError("a dialog name with a space must be refused")
    # a trigger that plays the new dialog: the argument picks the dialog by name; and one that switches that trigger on (a trigger argument)
    from ICParse.sgb import trigger_edit as E
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="Plays my dialog", folder="MY_FOLDER")
    pt = top.bz_script_triggers[top.bz_script_triggers_index]
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=pt.path_from_id(), which="actions", op="PlayDialog")
    pa = pt.actions[pt.act_index].args[0]
    pa.ref_dialog = "my_dialog"
    assert pa.val == d.did
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=pt.path_from_id(), which="actions", op="TriggerState")
    ts = pt.actions[pt.act_index]
    assert [E.CODE_KIND.get(a.code) for a in ts.args] == [E.K_TRIGGER, E.K_BOOL]
    ts.args[0].ref_trigger = "enable_trigger_skipping"
    assert ts.args[0].val == next(x.tid for x in top.bz_script_triggers if x.name == "enable_trigger_skipping")
    out = save("mission1")
    doc = E.TriggerDoc.from_text(read(out, "mission1.trg"))
    mine_t = next(x for _f, x in doc.all_triggers() if x.name == "Plays my dialog")
    assert [(a.type, [(q.type, q.val) for q in a.args][:1]) for a in mine_t.actions] == [("PlayDialog", [(21, d.did)]), ("TriggerState", [(7, ts.args[0].val)])]
    f = D.DialogFile.from_text(read(out, "mission1.dlg"))
    assert not f.readonly_reason and len(f.dialogs) == n0 + 1
    mine = f.find(d.did)
    assert mine.name == "my_dialog" and mine.triggers[0].name == "say it"
    got = [(a.type, a.controller, a.args) for a in mine.triggers[0].actions]
    assert got[0] == ("Countdown action", 1, [(0, 3)]), got
    old = {x.name: x for x in orig.dialogs}
    for x in f.dialogs:
        if x.name != "my_dialog":
            o = old[x.name]
            assert (x.id, [(t_.id, t_.name, [(a.type, a.controller, a.args) for a in t_.actions]) for t_ in x.triggers]) == (
                o.id, [(t_.id, t_.name, [(a.type, a.controller, a.args) for a in t_.actions]) for t_ in o.triggers]), x.name
    from blender_native.sceneobj import map_hooks as H
    assert not [p for p in H.run_checks(bpy.context, top) if p["check"] == "script" and "Dialog" in p["text"]], "the new dialog is known to the check"
    print(f"   mission1: a dialog with a controlled countdown and an animation saved to mission1.dlg; the other {n0} dialogs are unchanged", flush=True)


def test_from_memory():
    from ICParse.sgb import model, parser, trigger_edit as E
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import map_script as MS
    data = parser.write(model.Sgb.blank(16, 16, name="Script test", players=2))
    C.fresh()
    top = H.import_map_bytes(bpy.context, data, "scriptmap", {"load_models": False, "game_look": False})
    assert top.get(MS.KEY_LOADED) and len(top.bz_script_vars) == 256 and len(top.bz_script_triggers) == 0
    out = save("scriptmap")
    assert folder_files(out) == ["scriptmap.sgb"] and read(out, "scriptmap.sgb") == data
    # no placed objects: a group cannot be made
    try:
        bpy.ops.bz.script_group_new(name="empty")
    except RuntimeError as exc:
        assert "Select one or more placed objects" in str(exc)
    else:
        raise AssertionError("a group of nothing must be refused")
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="Start", folder="default")
    t = top.bz_script_triggers[0]
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=t.path_from_id(), which="actions", op="Play Music")
    out = save("scriptmap")
    assert folder_files(out) == ["scriptmap.sgb", "scriptmap.trg"] and read(out, "scriptmap.sgb") == data
    doc = E.TriggerDoc.from_text(read(out, "scriptmap.trg"))
    assert [x.name for _f, x in doc.all_triggers()] == ["Start"] and doc.all_triggers()[0][1].actions[0].type == "Play Music"
    print("   a map made in memory: untouched it writes only the map; with one trigger it writes scriptmap.trg", flush=True)


def test_panels(game_dir):
    from blender_native.sceneobj import map_script as MS
    from blender_native.sceneobj import panels_map_script as P
    top = open_map(MISSION1)
    top.bz_script_triggers_index = 0
    top.bz_script_groups_index = 0
    top.bz_script_dialogs_index = 0
    class Ctx:
        active_object = None
        selected_objects = []
    for name in ("draw_groups", "draw_triggers", "draw_dialogs", "draw_ai"):
        lay = C.FakeLayout()
        getattr(P, name)(lay, Ctx, top)
        assert lay.calls, name
    lay = C.FakeLayout()
    P.draw_triggers(lay, Ctx, top)
    assert "bz.script_trigger_new" in lay.operators() and "bz.script_expr_add" in lay.operators() and "bz.script_check" in lay.operators()
    # every kind of argument draws: walk all the triggers
    n = 0
    for t in top.bz_script_triggers:
        for e in list(t.conditions) + list(t.actions):
            for a in e.args:
                P.draw_arg(C.FakeLayout(), top, a)
                n += 1
    for d in top.bz_script_dialogs:
        for t in d.triggers:
            for e in t.actions:
                for a in e.args:
                    P.draw_arg(C.FakeLayout(), top, a)
                    n += 1
    print(f"   the panels draw; {n} arguments of mission1's triggers and dialogs draw as typed fields", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map scripting test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("loading")
        test_loading()
        section("stock maps opened and saved untouched")
        test_untouched(game_dir)
        section("groups, variables, AI scripts")
        test_groups_variables_ai(game_dir)
        section("triggers")
        test_triggers(game_dir)
        section("texts and blueprints")
        test_texts_and_blueprints(game_dir)
        section("checks")
        test_checks(game_dir)
        section("dialogs")
        test_dialogs(game_dir)
        section("a map from memory")
        test_from_memory()
        section("panels")
        test_panels(game_dir)
    print("SO_MAP_SCRIPT_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_script", main)
