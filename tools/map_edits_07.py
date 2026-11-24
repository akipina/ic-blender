"""map_edits_07 -- release 0.7.0: untouched saves with every side file, then one edit of each new kind, in a headless Blender.

    blender --background --factory-startup --python blender_native/tools/map_edits_07.py -- OUT.json [--maps KEY,KEY,...] [--edits NAME,...] [--no-new]

Part 1, per map (default: eight stock maps of different sizes, two of them campaign maps): import with everything on, save it untouched under
its own name, compare the map file and EVERY file beside it with the original (what a save copies, ``ops_map_export.sibling_files``).
Part 2, per edit kind on three maps: import, make ONE edit (a look value, a blocked area painted, a terrain type, a decal moved, a group,
a variable name, a trigger, a NISlet key, a crop), save, list the chunks of the map file and the side files that changed, open the saved map
again, check the edit is there, and save it again (the second save must be byte for byte the first). Part 3: a new map from each of the 15
templates, saved ready to drop, read again, checked (no error in the problem list, ``validate.check_board`` clean), opened again and saved
again byte for byte. Timings throughout. Writes only to temp folders."""
import collections, json, os, shutil, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
import _so_common as C
import bpy
import numpy as np
sys.path.insert(0, C.REPO)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv and not argv[0].startswith("--") else os.path.join(C.tmpdir("e07_json_"), "map_edits_07.json")


def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


UNTOUCHED = (opt("--maps") or ",".join(["Engine.sga|scenarios/mt/des_s.sgb", "Engine.sga|scenarios/mp/2p_mystery_lake.sgb",
             "InsectData.sga|scenarios/mp/2p_football_pool.sgb", "telluriandata.sga|scenarios/mp/4p_thin_ice.sgb",
             "Engine.sga|scenarios/sp/mission1.sgb", "Engine.sga|scenarios/sp/mission12.sgb",
             "InsectData.sga|scenarios/mp/6p_big_bog.sgb", "Engine.sga|scenarios/mp/6p_bikini_atoll.sgb"])).split(",")
EDIT_MAPS = ["Engine.sga|scenarios/sp/mission1.sgb", "Engine.sga|scenarios/mp/2p_mystery_lake.sgb", "InsectData.sga|scenarios/mp/6p_big_bog.sgb"]
ON = dict(load_models=True, show_creatures=True, game_look=True)
OFF = dict(load_models=False, show_creatures=False, game_look=False)
EDITS = (opt("--edits") or "look,blocked,types,decal,group,variable,trigger,nis,crop").split(",")


def leaves(doc):
    out = collections.Counter()
    def walk(chunks, parent):
        for c in chunks:
            kids = getattr(c, "children", None)
            if kids:
                walk(kids, c.form_type)
            else:
                out[(parent, c.tag, bytes(c.raw))] += 1
    walk(doc.root_chunks, "")
    return out


def changed_tags(a, b):
    from ICParse.iff.document import IffDocument
    la, lb = leaves(IffDocument.from_bytes(a)), leaves(IffDocument.from_bytes(b))
    return sorted({f"{k[0]}/{k[1]}" for k in (la - lb)} | {f"{k[0]}/{k[1]}" for k in (lb - la)})


def saved_set(folder):
    out = {}
    for d, _s, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder).replace(os.sep, "/")] = fh.read()
    return out


def open_game_map(key, options):
    C.fresh()
    t = time.perf_counter()
    res = bpy.ops.bz.import_game_map(game_map=key, **options)
    assert "FINISHED" in res, (key, res)
    top = [c for c in bpy.data.collections if c.get("ic_map")][0]
    return top, time.perf_counter() - t


def open_file(path, options):
    C.fresh()
    res = bpy.ops.bz.import_map(filepath=path, **options)
    assert "FINISHED" in res, (path, res)
    return [c for c in bpy.data.collections if c.get("ic_map") and not c.get("ic_map_excluded")][-1]


def save(top, folder, stem, **kw):
    t = time.perf_counter()
    res = bpy.ops.bz.export_map(filepath=os.path.join(folder, f"{stem}.sgb"), **kw)
    assert "FINISHED" in res, res
    return saved_set(folder), time.perf_counter() - t


def expected(gm, top):
    from blender_native.sceneobj import ops_map_export as X
    stem = os.path.splitext(os.path.basename(gm.member))[0]
    plan = X.build_plan(bpy.context, top)
    want = {f"{stem}.sgb": gm.read()}
    for sub, rest, data in X.sibling_files(plan, top.get("ic_map_source", "")):
        want[(f"{sub}/{stem}{rest}" if sub else f"{stem}{rest}")] = data
    return stem, want


def part1(game_dir, key):
    from ICParse.sgb import view
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    raw = gm.read()
    src = view.read(raw)
    top, t_imp = open_game_map(key, ON)
    stem, want = expected(gm, top)
    out = C.tmpdir("e1_")
    got, t_save = save(top, out, stem)
    diff = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
    row = {"map": key, "size": [src.width, src.height], "entities": len(src.entities), "files": len(want), "import_s": round(t_imp, 2),
           "save_s": round(t_save, 2), "identical": not diff, "differs": diff}
    shutil.rmtree(out, ignore_errors=True)
    return row


# ---------------------------------------------------------------------------------------------------- the edits
def select_faces(terrain, mask):
    terrain.data.polygons.foreach_set("select", np.asarray(mask, bool))
    terrain.data.update()


def select_points(terrain, mask):
    terrain.data.vertices.foreach_set("select", np.asarray(mask, bool))
    terrain.data.update()


def e_look(ctx):
    from ICParse.sgb import look_edit as L
    top = ctx["top"]
    g = top.ic_look
    assert g.ready
    g.sun_colour = (1.0, 0.5, 0.25)
    g.fog_on = not g.fog_on
    g.fog_start, g.fog_end = 40.0, 700.0
    def verify(top2, saved):
        v = L.values_of(saved)
        assert tuple(round(x, 4) for x in v["sun_colour"]) == (1.0, 0.5, 0.25), v["sun_colour"]
        assert top2.ic_look.fog_on == g_fog_on and abs(top2.ic_look.fog_end - 700.0) < 1e-3
    g_fog_on = bool(g.fog_on)
    return {"allowed": {"TERR/EFFC"}, "files": set(), "verify": verify, "options": ON}


def e_blocked(ctx):
    from blender_native.sceneobj import map_paint as P
    from blender_native.sceneobj import ops_map_export as X
    top, mv = ctx["top"], ctx["view"]
    terrain = X.terrain_of(top)
    P.ensure_passability(bpy.context, top)
    w, h = mv.width, mv.height
    cx, cz = w // 3, h // 3
    mask = np.zeros((h - 1, w - 1), bool)
    mask[cz:cz + 3, cx:cx + 3] = True
    select_faces(terrain, mask.reshape(-1))
    assert "FINISHED" in bpy.ops.bz.map_impass_faces(state="BLOCK")
    def verify(top2, saved):
        P.ensure_passability(bpy.context, top2)
        plane = P.classify_rgba(P.image_pixels(P.paint_image(top2)))
        assert (plane[2 * cz:2 * cz + 6, 2 * cx:2 * cx + 6] == 1).all(), "the blocked block is not in the saved passability layer"
    return {"allowed": {"TERR/TFAC", "PATH/IMPA", "PATH/PRMP", "PATH/PASM", "SCEN/IMPA", "SCEN/PRMP", "SCEN/PASM", "PFDR/NRML"}, "files": set(), "verify": verify,
            "options": OFF, "loose": True}


def e_types(ctx):
    from ICParse.sgb import terrain_types as T
    from blender_native.sceneobj import map_paint as P
    from blender_native.sceneobj import ops_map_export as X
    top, raw = ctx["top"], ctx["raw"]
    terrain = X.terrain_of(top)
    types = T.read_types(raw)
    P.ensure_types(bpy.context, top)
    n = types.shape[0]
    mask = np.zeros((n, n), bool)
    mask[10:14, 10:14] = True
    select_points(terrain, mask.reshape(-1))
    new_type = 2 if int(types[10, 10]) != 2 else 3
    assert "FINISHED" in bpy.ops.bz.map_types_set(type=str(new_type))
    def verify(top2, saved):
        got = T.read_types(saved)
        want = types.copy()
        want[10:14, 10:14] = new_type
        assert (got == want).all()
    return {"allowed": {"TERR/TFAC"}, "files": set(), "verify": verify, "options": OFF}


def e_decal(ctx):
    from ICParse.sgb import decals_edit
    from blender_native.sceneobj import map_paint as P
    top, raw = ctx["top"], ctx["raw"]
    rows = decals_edit.read_decals(raw)
    if not rows:
        return None
    assert "FINISHED" in bpy.ops.bz.map_decals_edit()
    hs = P.decal_handles(top)
    by = {int(o[P.KEY_DECAL]): o for o in hs}
    by[rows[0].eid].location.x += 5.0
    def verify(top2, saved):
        g = {r.eid: r for r in decals_edit.read_decals(saved)}
        assert abs(g[rows[0].eid].x - (rows[0].x + 5.0)) < 1e-3 and len(g) == len(rows)
    return {"allowed": {"DECL/ENTY"}, "files": set(), "verify": verify, "options": OFF}


def e_group(ctx):
    from blender_native.sceneobj import map_script as MS
    from ICParse.sgb import script_edit as S
    top, raw = ctx["top"], ctx["raw"]
    objs = MS.map_entities(top)
    pick = list(objs)[:3]
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for eid in pick:
        objs[eid].select_set(True)
    bpy.context.view_layer.objects.active = objs[pick[0]]
    assert "FINISHED" in bpy.ops.bz.script_group_new(name="Release squad")
    def verify(top2, saved):
        st = S.read_state(saved)
        mine = [g for g in st.groups if g.name == "Release squad"]
        assert len(mine) == 1 and sorted(mine[0].members) == sorted(pick), mine
    return {"allowed": {"SMEG/EGRP", "EGRP/ESGP", "SMEG/ESGP"}, "files": set(), "verify": verify, "options": OFF, "loose": True}


def e_variable(ctx):
    from ICParse.sgb import script_edit as S
    top = ctx["top"]
    top.bz_script_vars[5].name = "kills"
    def verify(top2, saved):
        assert S.read_state(saved).variables[5] == "kills"
        assert top2.bz_script_vars[5].name == "kills"
    return {"allowed": {"TVRS/TVAR"}, "files": set(), "verify": verify, "options": OFF, "loose": True}


def e_trigger(ctx):
    from ICParse.sgb import trigger_edit as E
    top, stem = ctx["top"], ctx["stem"]
    n0 = len(top.bz_script_triggers)
    assert "FINISHED" in bpy.ops.bz.script_trigger_new(name="Release trigger", folder="RELEASE_FOLDER")
    t = top.bz_script_triggers[top.bz_script_triggers_index]
    assert "FINISHED" in bpy.ops.bz.script_expr_add(owner=t.path_from_id(), which="actions", op="Win")
    def verify(top2, saved):
        assert len(top2.bz_script_triggers) == n0 + 1 and any(x.name == "Release trigger" for x in top2.bz_script_triggers)
    return {"allowed": set(), "files": {f"{stem}.trg"}, "verify": verify, "options": OFF, "map_unchanged": True}


def e_nis(ctx):
    from blender_native.sceneobj import map_cinematics as MC
    top, stem = ctx["top"], ctx["stem"]
    root = MC.root_of(top)
    colls = [c for c in MC.anim_collections(root) if c["nis_kind"] == "camera" and len(MC.key_objects(c)) >= 3]
    if not colls:
        return None
    c = colls[0]
    keys = MC.key_objects(c)
    keys[1].location.x += 5.0
    keys[1]["nis_tension"] = 0.5
    name, x0 = c["nis_name"], keys[1].location.x
    def verify(top2, saved):
        c2 = next(x for x in MC.anim_collections(MC.root_of(top2)) if x["nis_name"] == name)
        k2 = MC.key_objects(c2)[1]
        assert abs(k2.location.x - x0) < 1e-3 and abs(k2["nis_tension"] - 0.5) < 1e-6
    return {"allowed": set(), "files": {f"{stem}.na"}, "verify": verify, "options": OFF, "map_unchanged": True}


def e_crop(ctx):
    from blender_native.sceneobj import ops_map_export as X
    top, mv = ctx["top"], ctx["view"]
    st = bpy.context.scene.bz_map_tools
    st.crop_ratio = "2"
    st.crop_use_cursor = False
    st.crop_centre = (0.0, 0.0)
    st.crop_force = True
    assert "FINISHED" in bpy.ops.bz.map_crop_apply()
    new_pts = (mv.width - 1) // 2 + 1
    def verify(top2, saved):
        from ICParse.sgb import view
        v = view.read(saved)
        assert (v.width, v.height) == (new_pts, new_pts) and not v.problems, (v.width, v.problems)
    return {"allowed": None, "files": None, "verify": verify, "options": OFF, "crop": True, "new_top": True}


EDIT_FUNCS = {"look": e_look, "blocked": e_blocked, "types": e_types, "decal": e_decal, "group": e_group, "variable": e_variable,
              "trigger": e_trigger, "nis": e_nis, "crop": e_crop}


def live_top():
    from blender_native.sceneobj import map_tools as T
    return T.live_top() if hasattr(T, "live_top") else [c for c in bpy.data.collections if c.get("ic_map")][-1]


def part2(game_dir, key, edit):
    from ICParse.sgb import view
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    raw = gm.read()
    # the options an edit needs are known only after it is made; every edit here is made on a fresh import with the options it names
    opts = ON if edit == "look" else OFF
    top, t_imp = open_game_map(key, opts)
    mv = view.read(raw)
    stem, want = expected(gm, top)
    ctx = {"top": top, "raw": raw, "view": mv, "stem": stem}
    t = time.perf_counter()
    spec = EDIT_FUNCS[edit](ctx)
    if spec is None:
        return {"map": key, "edit": edit, "skipped": "the map has none"}
    t_edit = time.perf_counter() - t
    if spec.get("new_top"):
        top = live_top()
        stem = os.path.splitext(os.path.basename(gm.member))[0] + "_crop2"
    out = C.tmpdir("e2_")
    got, t_save = save(top, out, stem)
    row = {"map": key, "edit": edit, "edit_s": round(t_edit, 2), "save_s": round(t_save, 2)}
    sgb = got[f"{stem}.sgb"]
    if spec.get("crop"):
        row["changed_chunks"] = ["(a cropped map)"]
    else:
        row["changed_chunks"] = changed_tags(raw, sgb)
        row["changed_files"] = sorted(k for k in set(want) | set(got) if k != f"{stem}.sgb" and want.get(k) != got.get(k))
        if spec.get("map_unchanged"):
            assert sgb == raw, f"{key}: the map file must not change for this edit: {row['changed_chunks']}"
        if spec.get("allowed") is not None:
            tags = {t.split("/")[-1] for t in row["changed_chunks"]}
            ok = {a.split("/")[-1] for a in spec["allowed"]}
            assert tags <= ok, f"{key}: {edit} changed chunks it should not: {row['changed_chunks']} (allowed {sorted(spec['allowed'])})"
        if spec.get("files") is not None:
            assert set(row["changed_files"]) <= set(spec["files"]), f"{key}: {edit} changed files {row['changed_files']} (allowed {sorted(spec['files'])})"
    assert row["changed_chunks"] or row.get("changed_files"), f"{key}: {edit} changed nothing"
    # opened again: the edit is there, and saving it again gives the same files
    path = os.path.join(out, f"{stem}.sgb")
    t = time.perf_counter()
    top2 = open_file(path, spec["options"])
    row["reimport_s"] = round(time.perf_counter() - t, 2)
    spec["verify"](top2, sgb)
    out2 = C.tmpdir("e2b_")
    got2, _ = save(top2, out2, stem)
    diff = sorted(k for k in set(got) | set(got2) if got.get(k) != got2.get(k))
    assert not diff, f"{key}: {edit}: opened again and saved again differs in {diff}"
    row["reimport_resave_identical"] = True
    row["ok"] = True
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(out2, ignore_errors=True)
    return row


def part3(game_dir):
    from ICParse.sgb import scenario as S, view
    from ICParse import validate
    from blender_native.sceneobj import map_hooks as H, ops_map_new as N
    rows = []
    for i, t in enumerate(S.TEMPLATES):
        C.fresh()
        t0 = time.perf_counter()
        players = 2 + i % 5
        top = N.new_map(bpy.context, title=f"Release {t.key}", template_key=t.key, players=players, place_starts=True, load_models=False,
                        game_look=False, game_dir=game_dir)
        t_new = time.perf_counter() - t0
        found = H.run_checks(bpy.context, top)
        errs = [p for p in found if p["severity"] == "error"]
        out = C.tmpdir("e3_")
        stem = f"release_{t.key}"
        t0 = time.perf_counter()
        res = bpy.ops.bz.export_map(filepath=os.path.join(out, f"{stem}.sgb"), ready_to_drop=True)
        assert "FINISHED" in res, res
        t_save = time.perf_counter() - t0
        folder = os.path.join(out, "Data", "scenarios", "mp")
        files = saved_set(folder)
        v = view.read(files[f"{stem}.sgb"])
        header = None
        try:
            from ICParse.sgb import scenario as SC
            header = SC.read_header(files[f"{stem}.sgb"])
        except Exception:                                  # noqa: BLE001
            pass
        top2 = open_file(os.path.join(folder, f"{stem}.sgb"), OFF)
        out2 = C.tmpdir("e3b_")
        files2, _ = save(top2, out2, stem)
        same = files2 == files
        board_problems = [p for p in validate.check_board(files[f"{stem}.sgb"]) if not str(p).upper().startswith("WARN")]
        row = {"template": t.key, "board_problems": board_problems, "cells": t.cells, "players": players, "new_s": round(t_new, 2), "save_s": round(t_save, 2), "files": len(files),
               "problems_error": [p["text"] for p in errs], "warnings": len(found) - len(errs), "read_problems": list(v.problems),
               "resave_identical": same, "points": [v.width, v.height]}
        row["ok"] = not errs and not v.problems and not board_problems and same and v.width == t.cells + 1
        rows.append(row)
        print(("OK   " if row["ok"] else "FAIL ") + f"new map {t.key} {row['new_s']} s / save {row['save_s']} s", flush=True)
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(out2, ignore_errors=True)
    return rows


def main():
    game_dir = C.install_root()
    assert game_dir
    C.addon()
    rep = {"untouched": [], "edits": [], "new_maps": []}

    def dump():
        with open(OUT, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    with C.install_guard(game_dir):
        for key in UNTOUCHED:
            try:
                row = part1(game_dir, key)
            except Exception as exc:                       # noqa: BLE001
                import traceback
                row = {"map": key, "identical": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
            rep["untouched"].append(row)
            print(("OK   " if row.get("identical") else "FAIL ") + f"untouched {key} {row.get('import_s')} s / {row.get('save_s')} s {row.get('differs') or row.get('error') or ''}", flush=True)
            dump()
        for key in EDIT_MAPS:
            for edit in EDITS:
                try:
                    row = part2(game_dir, key, edit)
                except Exception as exc:                   # noqa: BLE001
                    import traceback
                    row = {"map": key, "edit": edit, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
                rep["edits"].append(row)
                print(("OK   " if row.get("ok") else ("SKIP " if row.get("skipped") else "FAIL ")) + f"edit {edit} on {key}: " +
                      json.dumps({k: row.get(k) for k in ("changed_chunks", "changed_files", "edit_s", "save_s", "reimport_s", "error", "skipped") if row.get(k) is not None}), flush=True)
                dump()
        if "--no-new" not in argv:
            rep["new_maps"] = part3(game_dir)
            dump()
    bad = [r for r in rep["untouched"] if not r.get("identical")] + [r for r in rep["edits"] if not r.get("ok") and not r.get("skipped")] + \
          [r for r in rep["new_maps"] if not r.get("ok")]
    print("MAP_EDITS_DONE", len(bad), "failed", flush=True)
    assert not bad, f"{len(bad)} failed"


if __name__ == "__main__":
    C.run_test("map_edits_07", main)
