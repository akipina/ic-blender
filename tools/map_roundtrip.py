"""map_roundtrip -- map editing and saving on several stock maps, in a headless Blender (release 0.6.0, STATUS.md).

    blender --background --factory-startup --python blender_native/tools/map_roundtrip.py -- OUT.json [MAP KEY ...]

A MAP KEY is ``<archive>|<member>`` as the game's map list gives it (default: the seven maps of STATUS.md, 65 to 257 points a side).
Per map: import with everything off (placed models, creatures and the game look off, as 0.5.0 opened a map without models) and with
everything on (timings of both), save the everything-on import untouched -> byte-identical; then one save holding every kind of edit (a block of points raised by
8 steps, the water up 1 m, one 8 x 8 block painted with a ground texture the map does not list, an entity moved and turned, one deleted,
one given to player 1, a savbush added at the 3D cursor); the saved file is read again with ``ICParse.sgb.view.read`` and compared with
what was edited (every other entity equal to the last bit); the saved map is opened again and a fresh plan must find no changes. The
JSON holds, per map, the timings, the chunks that changed, the library's pathfinding report and ``pathfinding_fidelity`` of the
untouched map. Needs the game's files (``$IC_INSTALL`` or a Steam folder); writes only to temp folders. Run it under the machine-wide
launch lock when other Blender tests run (``tests/_so_common.run_locked``)."""
import collections, json, os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
import _so_common as C
import bpy
import numpy as np
sys.path.insert(0, C.REPO)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv else os.path.join(C.tmpdir("rt_json_"), "map_roundtrip.json")
KEYS = argv[1:] or ["Engine.sga|scenarios/mt/des_s.sgb", "Engine.sga|scenarios/mp/2p_mystery_lake.sgb",
                    "InsectData.sga|scenarios/mp/2p_football_pool.sgb", "telluriandata.sga|scenarios/mp/4p_thin_ice.sgb",
                    "Engine.sga|scenarios/sp/mission12.sgb", "InsectData.sga|scenarios/mp/6p_big_bog.sgb",
                    "Engine.sga|scenarios/mp/6p_bikini_atoll.sgb"]
NEW_BLUEPRINT = "art/ebps/nature/savbush_01.ebp"


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


def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def terrain_co(terrain):
    me = terrain.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


OFF = dict(load_models=False, show_creatures=False, game_look=False)
ON = dict(load_models=True, show_creatures=True, game_look=True)


def import_map(key, options=ON):
    C.fresh()
    t = time.perf_counter()
    res = bpy.ops.bz.import_game_map(game_map=key, **options)
    assert "FINISHED" in res, (key, res)
    top = [c for c in bpy.data.collections if c.get("ic_map")]
    assert len(top) == 1
    return top[0], time.perf_counter() - t


def one(game_dir, key):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    raw = gm.read()
    src = view.read(raw)
    w, h, hs = src.width, src.height, src.height_scale
    row = {"map": key, "size": [w, h], "height_scale": round(hs, 3), "entities": len(src.entities), "bytes": len(raw)}
    top, t_off = import_map(key, OFF)
    row["import_off_s"] = round(t_off, 2)
    top, t_imp = import_map(key, ON)
    row["import_on_s"] = round(t_imp, 2)
    rep_on = json.loads(top["ic_map_report"])
    look = rep_on.get("look") or {}
    row["on"] = {"placed": rep_on["placed"], "instances": rep_on["instances"], "empties": rep_on["empties"],
                 "models": rep_on["models_loaded"], "creatures_placed": (rep_on.get("creatures") or {}).get("creatures_placed", 0),
                 "creatures_shown": (rep_on.get("creatures") or {}).get("creatures_shown", 0),
                 "suns": sum(1 for o in bpy.data.objects if o.type == "LIGHT" and o.data.type == "SUN" and o.get("ic_look_piece")),
                 "sea": bool(look.get("sea")), "decals": look.get("decals", 0), "fog": bool(look.get("fog")),
                 "game_water": bool(look.get("game_water")), "look_s": look.get("seconds")}
    out = C.tmpdir("rt_")
    # ---- untouched
    p = os.path.join(out, "untouched.sgb")
    t = time.perf_counter()
    plan = X.build_plan(bpy.context, top)
    row["plan_untouched_s"] = round(time.perf_counter() - t, 2)
    assert not plan.changed, plan.lines()
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=p)
    row["untouched_save_s"] = round(time.perf_counter() - t, 2)
    with open(p, "rb") as f:
        back = f.read()
    row["untouched_identical"] = back == raw
    assert back == raw, f"{key}: untouched save differs"
    # ---- every kind of edit, one save
    top, _ = import_map(key, ON)
    ents = {int(o["ic_entity_id"]): o for o in X.entity_objects(top) if "ic_entity_id" in o}
    terrain = X.terrain_of(top)
    co = terrain_co(terrain)
    cx, cz = w // 2, h // 2
    region = [(x, z) for x in range(cx - 2, cx + 3) for z in range(cz - 2, cz + 3)]
    up_m = 8 * hs                                      # a whole number of steps (3 m is 7.5 steps in a 0.4 m map: a tie)
    for (x, z) in region:
        co[z * w + x, 2] += up_m
    terrain.data.vertices.foreach_set("co", co.ravel())
    terrain.data.update()
    step_up = 8
    exp_h = src.heights.astype(np.float64).copy()
    for (x, z) in region:
        exp_h[z, x] = np.clip(np.rint(exp_h[z, x] + step_up), 0, 255)
    water = X.water_of(top)
    exp_water = src.water_level
    if water is not None:
        water.location.z += 1.0
        exp_water = src.water_level + int(round(1.0 / hs))
    block_ok = src.block_ids.size > 0
    tex_id = None
    if block_ok:
        have = {t.name.lower() for t in src.textures}
        NEW_G = next(n for n in X.game_listing(game_dir, gm.path.split("/")[-1])["ground"] if n.lower() not in have)
        assert "FINISHED" in bpy.ops.bz.map_add_ground(texture=NEW_G)
        slot = len(terrain.data.materials) - 1
        mi = np.empty(len(terrain.data.polygons), np.int32)
        terrain.data.polygons.foreach_get("material_index", mi)
        fb = src.face_blocks()
        nbx = src.blocks[0]
        mi[np.flatnonzero(fb == 3 * nbx + 3)] = slot
        terrain.data.polygons.foreach_set("material_index", mi)
        terrain.data.update()
    free = [e.instance_id for e in src.entities if e.owner < 0 and e.kind == "nature"]
    moved = deleted = owned = None
    if len(free) >= 3:
        moved, deleted, owned = free[0], free[1], free[2]
        ents[moved].location.x += 4.0
        ents[moved].rotation_euler.z += 0.4
        bpy.data.objects.remove(ents[deleted], do_unlink=True)
        select_only([ents[owned]])
        assert "FINISHED" in bpy.ops.bz.map_set_owner(owner="0")
    bpy.context.scene.cursor.location = (10.0, -10.0, 0.0)
    assert "FINISHED" in bpy.ops.bz.map_add_object(blueprint=NEW_BLUEPRINT)
    creature = next((o for eid, o in ents.items() if eid not in (moved, deleted, owned) and o.get("ic_kind") == "units"
                     and o.instance_type == "COLLECTION"), None)
    creature_id = None
    if creature is not None:                      # a creature shown as its model moves like any placed object
        creature_id = int(creature["ic_entity_id"])
        creature.location.x += 3.0
    if water is not None:                         # the water is drawn again from the new ground
        assert "FINISHED" in bpy.ops.bz.map_redraw_water()
        row["water_cells_after_redraw"] = len(water.data.polygons)
    plan = X.build_plan(bpy.context, top)
    row["plan"] = plan.lines()
    p = os.path.join(out, "edited.sgb")
    t = time.perf_counter()
    res = bpy.ops.bz.export_map(filepath=p)
    row["edited_save_s"] = round(time.perf_counter() - t, 2)
    assert "FINISHED" in res, res
    with open(p, "rb") as f:
        saved = f.read()
    stem = os.path.splitext(os.path.basename(p))[0]
    text = bpy.data.texts[f"Map save {stem}"].as_string()
    row["report_text"] = text
    new = view.read(saved)
    row["saved_bytes"] = len(saved)
    row["problems_after"] = list(new.problems)
    assert not new.problems, new.problems
    assert np.array_equal(new.heights.astype(np.int32), exp_h.astype(np.int32)), "heights differ"
    assert new.water_level == exp_water, (new.water_level, exp_water)
    if block_ok:
        names = {t.texid: t.name.lower() for t in new.textures}
        assert names[int(new.block_ids[3, 3])] == NEW_G.lower(), "painted block"
        diff = int((np.array([names[int(i)] for i in new.block_ids.reshape(-1)]) !=
                    np.array([{t.texid: t.name.lower() for t in src.textures}[int(i)] for i in src.block_ids.reshape(-1)])).sum())
        assert diff == 1, diff
    new_e = {e.instance_id: e for e in new.entities}
    src_e = {e.instance_id: e for e in src.entities}
    exp_count = len(src.entities) - (1 if deleted is not None else 0) + 1
    assert len(new.entities) == exp_count, (len(new.entities), exp_count)
    others = 0
    for eid, e in src_e.items():
        if eid in (moved, deleted, creature_id):
            continue
        n = new_e[eid]
        assert n.position == e.position and n.rotation == e.rotation and n.blueprint == e.blueprint, eid
        assert n.owner == (0 if eid == owned else e.owner), eid
        others += 1
    if moved is not None:
        assert deleted not in new_e
        assert abs(new_e[moved].position[0] - (src_e[moved].position[0] + 4.0)) < 1e-3
    if creature_id is not None:
        assert abs(new_e[creature_id].position[0] - (src_e[creature_id].position[0] + 3.0)) < 1e-3, "creature moved"
        row["creature_moved"] = creature_id
    fresh = [e for e in new.entities if e.instance_id not in src_e]
    assert len(fresh) == 1 and fresh[0].blueprint.upper().endswith("NATURE\\SAVBUSH_01.EBP")
    row["checks"] = {"heights_as_edited": True, "water_as_edited": water is not None, "block_painted": block_ok,
                     "entities_unchanged_exact": others, "moved": moved is not None, "deleted": deleted is not None,
                     "owner_changed": owned is not None, "added": 1}
    row["changed_chunks"] = changed_tags(raw, saved)
    # pathfinding numbers from the library's report and the fidelity of this very map
    edit = X._edit()
    new_bytes, lib = edit.apply_edits(raw, paths="auto", **X.build_plan(bpy.context, top).kwargs())
    row["paths"] = lib["paths"]
    row["terrain_report"] = {k: lib["terrain"].get(k) for k in ("heights_changed", "heights_rounded", "heights_clamped")}
    fid = edit.pathfinding_fidelity(raw)
    row["fidelity"] = {"impa_terrain_only": round(fid["impa"]["terrain_only"]["share"] * 100, 2),
                       "impa_with_footprints": round(fid["impa"]["with_footprints"]["share"] * 100, 2),
                       "impa_identical": fid["impa"]["identical"],
                       "stored_blocks_more": fid["impa"]["stored_blocks_more"], "stored_blocks_less": fid["impa"]["stored_blocks_less"],
                       "prmp_from_stored": round(fid["prmp"]["from_stored_impa"]["share"] * 100, 3),
                       "prmp_from_rebuilt": round(fid["prmp"]["from_rebuilt_impa"]["share"] * 100, 2),
                       "pasm_from_stored_identical": fid["pasm"]["from_stored_records_identical"],
                       "pasm_blocked_share": round(fid["pasm"]["blocked_state_share"] * 100, 2),
                       "sectors": [fid["pasm"]["sectors_stored"], fid["pasm"]["sectors_rebuilt"]],
                       "entities": {k: fid["entities"][k] for k in ("placed", "blocking", "open", "unknown")}}
    # the pathfinding of this map as it came: the older estimate (navgen) and the game's own builder (pathbake) against the stored records
    from ICParse.sgb import pathbake, navgen
    try:
        stored_cells, _cs = pathbake.cells_of(edit._records(src.board.preserved)[edit.C.MARK_IMPASS].raw)
        t = time.perf_counter()
        old_impa = navgen.generate_impa(src.heights, src.water_level, src.tile_scale, src.height_scale)
        old_cells, _ = pathbake.cells_of(old_impa)
        t = time.perf_counter()
        baked = pathbake.bake_map(raw)
        row["pathbake_s"] = round(time.perf_counter() - t, 2)
        n = int(stored_cells.size)
        row["pathfinding"] = {"cells": n,
                              "old_navgen_cells_differing": int((old_cells != stored_cells).sum()),
                              "new_pathbake_cells_differing": int((baked.cells != stored_cells).sum()),
                              "new_impa_byte_identical": bool(baked.impa == bytes(edit._records(src.board.preserved)[edit.C.MARK_IMPASS].raw)),
                              "new_all_three_identical": bool(fid.get("rebuild_identical")),
                              "painted_cells_applied": fid["impa"].get("edit_cells")}
    except Exception as exc:                      # noqa: BLE001
        import traceback
        row["pathfinding"] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
    # ---- the saved file opens again and is its own original
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_map(filepath=p, **ON)
    top2 = [c for c in bpy.data.collections if c.get("ic_map")][0]
    plan2 = X.build_plan(bpy.context, top2)
    assert not plan2.changed, plan2.lines()
    row["reimport_no_changes"] = True
    row["ok"] = True
    return row


def main():
    game_dir = C.install_root()
    assert game_dir
    C.addon()
    rows = []
    with C.install_guard(game_dir):
        for key in KEYS:
            t = time.perf_counter()
            try:
                row = one(game_dir, key)
            except Exception as exc:                    # noqa: BLE001
                import traceback
                row = {"map": key, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
            row["total_s"] = round(time.perf_counter() - t, 1)
            rows.append(row)
            print(f"{'OK  ' if row.get('ok') else 'FAIL'} {key} {row.get('total_s')} s", flush=True)
            with open(OUT, "w") as f:
                json.dump(rows, f, indent=1, default=str)
    bad = [r["map"] for r in rows if not r.get("ok")]
    print("MAP_ROUNDTRIP_DONE", len(rows) - len(bad), "of", len(rows), flush=True)
    assert not bad, f"failed: {bad}"


if __name__ == "__main__":
    C.run_test("map_roundtrip", main)
