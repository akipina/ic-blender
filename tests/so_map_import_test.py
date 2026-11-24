"""Map import (File > Import > Impossible Creatures Map), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_import_test.py     (or: run_all.py -k so_map_import)

Four of the game's own maps, read straight from its archives, of three sizes: 2p_faceoff (65 x 65, water), mission1 (129 x 129,
the campaign: stock creatures and creatures combined in the map), 6p_big_bog (129 x 129, the most placed entities of any archive
map, water above the water level in places) and 6p_bikini_atoll (257 x 257, the largest archive map). For each:

1. the terrain has width x height vertices and one face per cell, at the map's size and heights, and every face shows the ground
   texture of its 8 x 8 block (one material per ground texture, with its image);
2. one object per ENTT record, with its id; every one is an instance of a loaded model (the entity's own model) or an empty whose
   reason is in the report; instances sit where the map puts them, in the add-on's axes, so most of them stand on the ground;
3. the water surface sits on the water plane: at the water level where the plane is flat at it, over exactly the cells under water;
4. importing again into a fresh file gives the same counts.

Then the same map from a file on disk (the File > Import operator); a file that is no map and a map that cannot be built (a fault
put in by the test) are refused in plain words and leave nothing behind; the game-map list and the menu entries; and nothing under
the game folder changed. Import times are printed, never asserted.
"""
import json
import os
import shutil
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, C.REPO)

MAPS = ("Engine.sga|scenarios/mp/2p_faceoff.sgb", "Engine.sga|scenarios/sp/mission1.sgb",
        "InsectData.sga|scenarios/mp/6p_big_bog.sgb", "Engine.sga|scenarios/mp/6p_bikini_atoll.sgb")
COUNT_KEYS = ("size", "cells", "world_size_m", "ground_textures", "ground_textures_missing", "water_cells", "entities",
              "placed", "instances", "empties", "models_loaded", "models", "not_shown", "models_failed", "problems")


def section(title):
    print(f"-- {title}", flush=True)


def map_collection():
    tops = [c for c in bpy.data.collections if c.get("ic_map")]
    assert len(tops) == 1, [c.name for c in tops]
    return tops[0]


def import_game_map(key, **options):
    """The import with the creatures and the game look off (their own tests are so_map_creatures_test and so_map_look_test)
    unless asked for."""
    C.fresh()
    options = {"show_creatures": False, "game_look": False, **options}
    res = bpy.ops.bz.import_game_map(game_map=key, **options)
    assert "FINISHED" in res, (key, res)
    top = map_collection()
    return top, json.loads(top["ic_map_report"])


def check_map(key, game_dir):
    from ICParse.sgb import view
    from blender_native import core
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    mv = view.read_game_map(gm)
    top, rep = import_game_map(key)
    section(f"{key}: {mv.width} x {mv.height}, {len(mv.entities)} entities, imported in {rep['seconds']} s")
    assert rep["source"] == key and top["ic_map_source"] == key and top["ic_map_archive"] == gm.source

    # 1. terrain
    [terrain] = [o for o in top.all_objects if o.type == "MESH" and o.get("ic_map")]
    me = terrain.data
    assert len(me.vertices) == mv.width * mv.height == rep["size"][0] * rep["size"][1], len(me.vertices)
    assert len(me.polygons) == (mv.width - 1) * (mv.height - 1)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    assert np.allclose(co, core.sgm_to_blender(mv.vertex_positions(np.float64)), atol=1e-3)
    sx, sz = mv.world_size
    assert np.isclose(co[:, 0].max() - co[:, 0].min(), sx) and np.isclose(co[:, 1].max() - co[:, 1].min(), sz)
    nrm = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("normal", nrm)
    assert (nrm.reshape(-1, 3)[:, 2] > 0).all(), "every terrain face must face up"
    mi = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("material_index", mi)
    names = [m.get("ic_ground_texture_id") for m in me.materials]
    assert sorted(n for n in names if n is not None) == sorted(t.texid for t in mv.textures)
    shown = np.array([names[i] for i in mi])
    assert (shown == mv.face_texids()).all(), "a face does not show its block's ground texture"
    for m in me.materials:
        assert any(n.type == "TEX_IMAGE" and n.image is not None and n.image.packed_file for n in m.node_tree.nodes), m.name
    assert "Ground" in me.uv_layers and rep["ground_textures_missing"] == []

    # 2. entities
    ents = [o for o in top.all_objects if "ic_entity_id" in o]
    n_entt = len(mv.board.preserved.find_all("ENTT"))
    assert len(ents) == n_entt == rep["placed"] == rep["entities"], (len(ents), n_entt, rep["placed"])
    assert sorted(o["ic_entity_id"] for o in ents) == sorted(e.instance_id for e in mv.entities)
    by_id = {e.instance_id: e for e in mv.entities}
    hidden = [c for c in top.children if c.name.endswith("(hidden)")]
    loaded = {c["ic_map_model"]: c for c in (hidden[0].children if hidden else ())}
    reasons = {n["name"]: n for n in rep["not_shown"]}
    instances = empties = 0
    ground = []
    for o in ents:
        e = by_id[o["ic_entity_id"]]
        assert np.allclose(o.matrix_world.translation, core.sgm_to_blender(np.asarray([e.position]))[0], atol=1e-3)
        assert o["ic_owner"] == e.owner and o["ic_blueprint"] == e.blueprint and o["ic_kind"] == e.kind
        if o.instance_type == "COLLECTION":
            assert o.instance_collection is loaded.get(e.model_ref), (o.name, e.model_ref)
            instances += 1
            gx = (o.matrix_world.translation.x / mv.tile_scale) + (mv.width - 1) / 2.0
            gz = (-o.matrix_world.translation.y / mv.tile_scale) + (mv.height - 1) / 2.0
            i, j = int(np.clip(round(gx), 0, mv.width - 1)), int(np.clip(round(gz), 0, mv.height - 1))
            ground.append(abs(o.matrix_world.translation.z - mv.heights[j, i] * mv.height_scale))
        else:
            assert o.get("ic_not_shown") and e.name in reasons and reasons[e.name]["why"] == o["ic_not_shown"], o.name
            empties += 1
    assert instances == rep["instances"] and empties == rep["empties"] and instances + empties == n_entt
    assert sum(n["count"] for n in rep["not_shown"]) == empties
    assert len(loaded) == rep["models_loaded"] and rep["models_failed"] == []
    if ground:
        assert float(np.median(ground)) < 0.6, f"placed models do not stand on the ground (median {np.median(ground):.2f} m)"
    for o in (hidden[0].all_objects if hidden else ()):
        assert "bz_scene" not in o and "bz_source" not in o, o.name      # parts of the map, not add-on projects
    if hidden:
        layer = next((lc for lc in bpy.context.view_layer.layer_collection.children[top.name].children
                      if lc.collection == hidden[0]), None)
        assert layer is not None and layer.exclude, "the loaded models must stay out of the view"
    print(f"   {instances} instances of {len(loaded)} models, {empties} empties, median height above the ground point "
          f"{np.median(ground) if ground else 0:.3f} m", flush=True)

    # 3. water
    wpos, wquads, wvid = mv.water_mesh(np.float64)
    waters = [o for o in top.all_objects if o.type == "MESH" and "ic_water_level" in o and not o.get("ic_map")]
    assert rep["water_cells"] == len(wquads) == int(mv.wet_cells().sum())
    if len(wquads):
        [water] = waters
        wco = np.empty(len(water.data.vertices) * 3, np.float32)
        water.data.vertices.foreach_get("co", wco)
        wz = wco.reshape(-1, 3)[:, 2]
        assert len(water.data.polygons) == len(wquads)
        assert np.allclose(wz, mv.water.reshape(-1)[wvid] * mv.height_scale, atol=1e-3)
        flat = mv.water.reshape(-1)[wvid] == mv.water_level
        assert np.allclose(wz[flat], mv.water_height, atol=1e-3)
        mat = water.data.materials[0]
        assert mat.diffuse_color[3] < 1.0, "the water must be see-through"
        print(f"   water: {len(wquads)} cells, {int(flat.sum())} of {len(wz)} points at the water level "
              f"({mv.water_height:g} m)", flush=True)
    else:
        assert not waters

    # 4. again, in a fresh file
    _top2, rep2 = import_game_map(key)
    assert {k: rep2[k] for k in COUNT_KEYS} == {k: rep[k] for k in COUNT_KEYS}, "a second import gave other counts"
    return rep


def test_maps(game_dir):
    reps = {}
    for key in MAPS:
        reps[key] = check_map(key, game_dir)
    big = reps["InsectData.sga|scenarios/mp/6p_big_bog.sgb"]
    assert big["entities"] > 2000
    m1 = reps["Engine.sga|scenarios/sp/mission1.sgb"]
    assert any("creature" in n["why"] for n in m1["not_shown"]), "mission1's creatures must be reported as creatures"
    return reps


def test_everything_on(game_dir):
    """With every option on (the defaults) the creatures are real models, the look is built, and the report says so."""
    section("every option on: mission1's creatures as models, the game look")
    key = "Engine.sga|scenarios/sp/mission1.sgb"
    top, rep = import_game_map(key, show_creatures=True, game_look=True)
    crep, look = rep["creatures"], rep["look"]
    assert crep["creatures_placed"] == 21 and crep["creatures_shown"] == 21 and not crep["not_built"], crep
    assert not any(n["why"].startswith("a creature") for n in rep["not_shown"]), rep["not_shown"]
    assert rep["instances"] + rep["empties"] == rep["placed"]
    assert look["sun"] and look["sea"] and look["decals"] > 0, look
    assert bpy.data.collections.get("mission1 look") is not None or any(c.name.endswith(" look") for c in top.children)
    assert rep["seconds"] < 60
    from blender_native.sceneobj import ops_map_import as M
    text = "\n".join(M.report_lines(rep))
    assert "Creatures: 21 of 21 shown as models" in text and "Game look:" in text, text
    # off: as before, the creatures are empties again with their reason
    _t, rep_off = import_game_map(key, show_creatures=False, game_look=False)
    assert "creatures" not in rep_off and "look" not in rep_off
    assert any("creature" in n["why"] for n in rep_off["not_shown"])
    print(f"   on {rep['seconds']} s, off {rep_off['seconds']} s", flush=True)


def test_from_file(game_dir):
    section("the same map from a file on disk (File > Import > Impossible Creatures Map)")
    from ICParse.sgb import view
    gm = next(m for m in view.game_maps(game_dir) if m.key == MAPS[0])
    tmp = C.tmpdir("map_")
    path = os.path.join(tmp, "2p_faceoff.sgb")
    with open(path, "wb") as f:
        f.write(gm.read())
    C.fresh()
    res = bpy.ops.bz.import_map(filepath=path)
    assert "FINISHED" in res, res
    top = map_collection()
    rep = json.loads(top["ic_map_report"])
    assert top["ic_map_source"] == os.path.abspath(path) and top["ic_map_archive"] == ""
    assert rep["placed"] == 164 and rep["size"] == [65, 65] and rep["models_failed"] == []
    text = bpy.data.texts.get("Map import 2p_faceoff")
    assert text is not None and "Placed: 164 of 164 entities" in text.as_string()
    try:                                   # an operator that reports an error raises it when called from a script
        res = bpy.ops.bz.import_map(filepath=os.path.join(tmp, "nothing.sgb"))
    except RuntimeError as exc:
        assert "There is no map file at" in str(exc), exc
    else:
        assert "CANCELLED" in res, res
    shutil.rmtree(tmp, ignore_errors=True)


def expect_error(words, call):
    """An operator that reports an error raises it when called from a script: the message must say ``words``."""
    try:
        res = call()
    except RuntimeError as exc:
        assert words in str(exc), exc
        return str(exc)
    raise AssertionError(f"no error reported (result {res})")


def test_failures_leave_nothing(game_dir):
    section("a file that is no map, and a map that cannot be built, leave nothing behind")
    from blender_native.sceneobj import ops_map_import as M
    tmp = C.tmpdir("map_")
    junk = os.path.join(tmp, "junk.sgb")
    with open(junk, "wb") as f:
        f.write(b"FORM\x00\x00\x00\x10SCENnot a map at all")
    C.fresh()
    expect_error("could not be read as a map", lambda: bpy.ops.bz.import_map(filepath=junk))
    original = M._water_material

    def broken(board):
        raise ValueError("a fault put in by the test")
    M._water_material = broken
    try:
        msg = expect_error("could not be built", lambda: bpy.ops.bz.import_game_map(game_map=MAPS[0]))
        assert "a fault put in by the test" in msg, msg
    finally:
        M._water_material = original
    assert not [c for c in bpy.data.collections if c.get("ic_map") or c.name.startswith("2p_faceoff")]
    assert not [o for o in bpy.data.objects if "ic_entity_id" in o or o.get("ic_map") or "ic_map_model" in o]
    shutil.rmtree(tmp, ignore_errors=True)


def test_list_and_menu(game_dir):
    section("the game-map list and the menu entries")
    from blender_native.sceneobj import ops_map_import as M
    items = M._map_items(None, bpy.context)
    keys = [i[0] for i in items]
    assert all(k in keys for k in MAPS), "a test map is missing from the list"
    assert len(keys) == len(set(keys)) >= 80
    labels = {i[0]: i[1] for i in items}
    assert labels[MAPS[0]] == "2p faceoff  (multiplayer, Engine.sga)", labels[MAPS[0]]
    assert labels[MAPS[1]] == "mission1  (campaign, Engine.sga)", labels[MAPS[1]]
    assert all("|" not in label for label in labels.values())
    funcs = bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize()
    assert M.menu_import in funcs, "File > Import has no map entries"


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map import needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        reps = test_maps(game_dir)
        test_everything_on(game_dir)
        test_from_file(game_dir)
        test_failures_leave_nothing(game_dir)
        test_list_and_menu(game_dir)
    for key, rep in reps.items():
        print(f"   {key}: {rep['size'][0]} x {rep['size'][1]}, {rep['entities']} entities, {rep['models_loaded']} models, "
              f"{rep['empties']} empties, {rep['water_cells']} water cells, {rep['seconds']} s", flush=True)
    print("SO_MAP_IMPORT_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_import", main)
