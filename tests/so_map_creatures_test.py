"""The creatures of an imported map show as their real models (sceneobj/map_creatures.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_creatures_test.py     (or: run_all.py -k so_map_creatures)

1. mission1 (stock creatures and four creatures combined inside the map): after the map import and ``show_creatures`` every placed
   creature is an instance of a built creature model, the counts match ``ICParse.sgb.view``'s creature list (per creature and in all),
   each distinct creature is built once, the four combined creatures are built from their recipes (the parent animals and the limbs each
   one gives are read back from what was built), they are not the same shape, and every one stands on the ground at its own position;
2. the placed objects keep what the save reads (blueprint, id, owner, groups, ic_combined_creature) and lose only ``ic_not_shown``;
3. a map saved with no edits is still byte for byte the map that was opened; a creature moved in Blender (a stock one and a combined one)
   is moved in the saved map and nothing else changes; one deleted is gone from the saved map;
4. a creature that cannot be built stays an empty with its reason in words (the game's files not given; a recipe naming an animal the
   game does not have);
5. the biggest maps: mission9 (140 creatures, 106 combined) and the 630-creature Zombie hill fort, a map with the game folder's own loose
   creatures (ice clash): all shown, with the time per creature and per map;
6. nothing under the game's folder changed.
"""
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, C.REPO)

MISSION1 = "Engine.sga|scenarios/sp/mission1.sgb"
MISSION9 = "Engine.sga|scenarios/sp/mission9.sgb"
HILL_FORT = "telluriandata.sga|scenarios/mp/zombies_hill_fort_spec.sgb"
ICE_CLASH = "Data|scenarios/mp/ice clash.sgb"
PER_CREATURE_LIMIT_S = 2.0         # one distinct creature, with its skin
PER_MAP_LIMIT_S = 60.0


def section(title):
    print(f"-- {title}", flush=True)


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def open_map(game_dir, key, *, give_data=True):
    """Import the map with the add-on's own import, then show its creatures. Returns (top, mv, data, report)."""
    from ICParse.sgb import view
    from blender_native.sceneobj import map_creatures as MC
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=True, show_creatures=False, game_look=False)
    assert "FINISHED" in res, (key, res)
    top = next(c for c in bpy.data.collections if c.get("ic_map"))
    gm = game_map(game_dir, key)
    mv = view.read_game_map(gm)
    data = view.game_data(game_dir, mv.archive) if give_data else None
    t0 = time.perf_counter()
    report = MC.show_creatures(bpy.context, mv, top, data=data)
    report["wall_s"] = time.perf_counter() - t0
    return top, mv, data, report


def entity_objects(top):
    from blender_native.sceneobj import ops_map_export as X
    return {int(o["ic_entity_id"]): o for o in X.entity_objects(top) if "ic_entity_id" in o}


def creature_entities(mv, data):
    """{entity id: PlacedEntity} the map view says are creatures."""
    from ICParse.sgb import view
    infos = view.resolve_models(mv, data)
    return {e.instance_id: e for e in mv.entities if infos[e.model_key].status == view.CREATURE}, infos


def is_creature_instance(o):
    return o.instance_type == "COLLECTION" and o.instance_collection is not None and o.instance_collection.name.startswith("creature ")


def mesh_points(coll):
    me = coll.objects[0].data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def save(top, name):
    out = C.tmpdir("creatures_")
    path = os.path.join(out, f"{name}.sgb")
    res = bpy.ops.bz.export_map(filepath=path)
    assert "FINISHED" in res, res
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------------------------------- 1. mission1
def test_mission1(game_dir):
    from ICParse.sgb import view
    from blender_native.sceneobj import map_creatures as MC
    top, mv, data, rep = open_map(game_dir, MISSION1)
    creatures, infos = creature_entities(mv, data)
    ents = entity_objects(top)
    assert len(creatures) == 21 == rep["creatures_placed"], (len(creatures), rep)
    assert rep["creatures_shown"] == 21 and rep["creatures_empty"] == 0 and not rep["not_built"], rep
    shown = {i: o for i, o in ents.items() if is_creature_instance(o)}
    assert set(shown) == set(creatures), "the placed creatures that show as models are not the map's creature list"
    # per creature: the objects of one model key share one built collection, and every model key has its own
    by_key = {}
    for i, e in creatures.items():
        by_key.setdefault(e.model_key, set()).add(shown[i].instance_collection.name)
    assert all(len(v) == 1 for v in by_key.values()), by_key
    assert len({next(iter(v)) for v in by_key.values()}) == len(by_key) == rep["distinct"] == rep["built"] == 8
    for key, e_count in ((k, sum(1 for e in creatures.values() if e.model_key == k)) for k in by_key):
        assert infos[key].count == e_count
    combined_keys = sorted(k for k in by_key if k.startswith("combined:"))
    assert combined_keys == ["combined:139", "combined:140", "combined:141", "combined:142"], combined_keys
    print(f"   {len(creatures)} creatures placed, {len(by_key)} different ones built once each: "
          f"{rep['built_models']}", flush=True)

    # the combined creatures are built from their recipes
    recipes = MC.combined_recipes(mv.board)
    assert sorted(recipes) == [139, 140, 141, 142]
    expect = {139: ("COYOTE", "WOLF"), 140: ("LEMMING", "COYOTE"), 141: ("COYOTE", "LEMMING"), 142: ("POLAR_BEAR", "MUSK_OX")}
    shapes = []
    for bid, parents in expect.items():
        assert tuple(recipes[bid].parents) == parents, recipes[bid]
        coll = next(iter(by_key[f"combined:{bid}"]))
        how = bpy.data.collections[coll][MC.KEY_CREATURE]
        torso_src = next(l[2] for l in recipes[bid].links if l[0] == 0x800)
        torso_name = parents[torso_src].lower()
        assert how.startswith("combined in the map:") and f"{torso_name}: torso" in how, (bid, how)
        assert all(p.lower() in how for p in parents) or len({recipes[bid].parents[l[2]] for l in recipes[bid].links}) == 1, how
        pts = mesh_points(bpy.data.collections[coll])
        shapes.append(tuple(round(float(v), 3) for v in pts.max(0) - pts.min(0)))
        assert abs(float(pts[:, 2].min())) < 0.05, (bid, "the creature does not stand on its origin", pts[:, 2].min())
        assert len(bpy.data.collections[coll].objects[0].data.materials) > 0
        print(f"   combined {bid}: {how}; size {shapes[-1]}", flush=True)
    assert len(set(shapes)) == 4, shapes
    # a creature built by the combiner is not simply its torso animal's own model
    coyote = mesh_points(bpy.data.collections[next(iter(by_key["art/ebps/stock/coyote.ebp"]))])
    c141 = mesh_points(bpy.data.collections[next(iter(by_key["combined:141"]))])
    assert coyote.shape == c141.shape and not np.allclose(coyote, c141)

    # each stock creature's model has its skin and stands on the ground
    for key in by_key:
        if key.startswith("combined:"):
            continue
        coll = bpy.data.collections[next(iter(by_key[key]))]
        pts = mesh_points(coll)
        me = coll.objects[0].data
        assert 0.5 < float((pts.max(0) - pts.min(0)).max()) < 6.0, (key, pts.max(0) - pts.min(0))
        assert abs(float(pts[:, 2].min())) < 0.05 and len(me.uv_layers) == 1 and len(me.polygons) > 1000, key
        tex = [n.image for m in me.materials if m.node_tree for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
        assert tex and all(i.size[0] > 0 for i in tex), key

    # placed where the map says, turned the way it says
    from blender_native import rig
    for i, e in creatures.items():
        want = rig.to_blender_matrix(e.matrix())
        got = shown[i].matrix_world
        assert np.abs(np.array(got) - np.array(want)).max() < 1e-4, (i, e.name)

    # what the save reads is still on every placed creature; only ic_not_shown is gone
    for i, e in creatures.items():
        o = shown[i]
        assert o["ic_blueprint"] == e.blueprint and int(o["ic_entity_id"]) == i and int(o["ic_owner"]) == e.owner
        assert ("ic_groups" in o) == bool(e.groups) and (not e.groups or o["ic_groups"] == ", ".join(e.groups))
        assert bool(o.get("ic_combined_creature")) == e.combined
        assert "ic_not_shown" not in o
    # the built models are in the hidden collection and are not mistaken for placed entities
    from blender_native.sceneobj import ops_map_export as X
    assert all("ic_blueprint" not in o for c in top.children if c.name.endswith("(hidden)") for o in c.all_objects)
    assert len(X.entity_objects(top)) == len(mv.entities)
    per = rep["per_creature_s"]
    assert max(per.values()) < PER_CREATURE_LIMIT_S, per
    print(f"   per creature {min(per.values()):.2f} to {max(per.values()):.2f} s, all {rep['seconds']:.2f} s", flush=True)


# ---------------------------------------------------------------------------------------------------- 2. saving
def test_save(game_dir):
    from ICParse.sgb import view
    gm = game_map(game_dir, MISSION1)
    raw = gm.read()
    top, mv, data, rep = open_map(game_dir, MISSION1)
    assert rep["creatures_shown"] == 21
    saved = save(top, "mission1")
    assert saved == raw, f"a map with its creatures shown and no edits differs from the original ({len(saved)} vs {len(raw)} bytes)"
    print(f"   mission1 with its 21 creatures shown, saved with no edits: {len(raw)} bytes back byte for byte", flush=True)

    # move a stock creature and a combined one; delete one
    ents = entity_objects(top)
    stock = next(i for i, o in ents.items() if is_creature_instance(o) and not o.get("ic_combined_creature"))
    comb = next(i for i, o in ents.items() if is_creature_instance(o) and o.get("ic_combined_creature"))
    gone = next(i for i, o in ents.items() if is_creature_instance(o) and i not in (stock, comb))
    ents[stock].location.x += 3.0
    ents[comb].location.y -= 2.0           # Blender y is the game's -z
    bpy.data.objects.remove(ents[gone], do_unlink=True)
    new = view.read(save(top, "mission1_moved"))
    old = {e.instance_id: e for e in mv.entities}
    now = {e.instance_id: e for e in new.entities}
    assert set(now) == set(old) - {gone}, (set(old) - set(now))
    assert abs(now[stock].position[0] - (old[stock].position[0] + 3.0)) < 1e-3
    assert abs(now[stock].position[1] - old[stock].position[1]) < 1e-3 and abs(now[stock].position[2] - old[stock].position[2]) < 1e-3
    assert abs(now[comb].position[2] - (old[comb].position[2] + 2.0)) < 1e-3
    assert abs(now[comb].position[0] - old[comb].position[0]) < 1e-3
    assert now[comb].combined and now[comb].name == old[comb].name
    others = [i for i in now if i not in (stock, comb)]
    for i in others:
        assert np.abs(np.array(now[i].position) - np.array(old[i].position)).max() < 1e-4 and now[i].blueprint == old[i].blueprint
    print(f"   one stock creature moved 3 m, one combined creature moved 2 m, one deleted: {len(others)} others unchanged", flush=True)


# ---------------------------------------------------------------------------------------------------- 3. what cannot be built
def test_cannot_build(game_dir):
    from ICParse.sgb import view
    from blender_native.sceneobj import map_creatures as MC
    # no game files given: every creature stays an empty, with the reason in words
    top, mv, data, rep = open_map(game_dir, MISSION1, give_data=False)
    assert rep["distinct"] == 0 or rep["creatures_shown"] == 0
    # (the infos need the game's files, so build with the real data, then ask for creatures without it)
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=MISSION1, load_models=True, show_creatures=False, game_look=False)
    assert "FINISHED" in res
    top = next(c for c in bpy.data.collections if c.get("ic_map"))
    mv = view.read_game_map(game_map(game_dir, MISSION1))
    real = view.game_data(game_dir, mv.archive)
    infos = view.resolve_models(mv, real)
    rep = MC.show_creatures(bpy.context, mv, top, data=None, infos=infos)
    assert rep["creatures_shown"] == 0 and rep["creatures_empty"] == 21 and rep["built"] == 0, rep
    objs = [o for o in entity_objects(top).values() if o.get("ic_kind") == "units" and "ic_not_shown" in o]
    assert len(objs) >= 21 and all("could not be shown" in o["ic_not_shown"] for o in objs), [o["ic_not_shown"] for o in objs][:3]
    assert all(o.instance_type == "NONE" and o.type == "EMPTY" for o in objs if "creature" in o["ic_not_shown"])
    print(f"   no game files: 21 creatures stay empties, e.g. '{objs[0]['ic_not_shown']}'", flush=True)

    # a recipe that names an animal the game does not have
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=MISSION1, load_models=True, show_creatures=False, game_look=False)
    top = next(c for c in bpy.data.collections if c.get("ic_map"))
    mv = view.read_game_map(game_map(game_dir, MISSION1))
    recipes = MC.combined_recipes(mv.board)
    real_recipes = MC.combined_recipes

    def bent(board):
        out = real_recipes(board)
        out[139].parents[1] = "NO_SUCH_ANIMAL"
        return out
    MC.combined_recipes = bent
    try:
        rep = MC.show_creatures(bpy.context, mv, top, data=real)
    finally:
        MC.combined_recipes = real_recipes
    assert rep["built"] == 7 and len(rep["not_built"]) == 1 and rep["creatures_empty"] == 7, rep
    assert "NO_SUCH_ANIMAL" in rep["not_built"][0]["why"] and "not in the game's files" in rep["not_built"][0]["why"], rep["not_built"]
    left = [o for o in entity_objects(top).values() if o.get("ic_combined_creature") and "ic_not_shown" in o]
    assert len(left) == 7 and all(o.type == "EMPTY" and o.instance_type == "NONE" for o in left)
    print(f"   a recipe naming an animal the game lacks: its 7 creatures stay empties ('{rep['not_built'][0]['why']}'), "
          "the other 7 kinds are built", flush=True)
    assert recipes


# ---------------------------------------------------------------------------------------------------- 4. the big maps
def test_big_maps(game_dir):
    for key, minimum in ((MISSION9, 140), (HILL_FORT, 630), (ICE_CLASH, 41)):
        top, mv, data, rep = open_map(game_dir, key)
        creatures, _infos = creature_entities(mv, data)
        ents = entity_objects(top)
        shown = [i for i in creatures if is_creature_instance(ents[i])]
        assert len(creatures) == minimum == rep["creatures_placed"], (key, len(creatures), rep["creatures_placed"])
        assert len(shown) == len(creatures) == rep["creatures_shown"], (key, rep["not_built"])
        assert rep["built"] == rep["distinct"] and not rep["not_built"], rep["not_built"]
        assert max(rep["per_creature_s"].values()) < PER_CREATURE_LIMIT_S and rep["seconds"] < PER_MAP_LIMIT_S, rep
        tris = sum(rep["triangles"].values())
        print(f"   {key}: {len(shown)} of {len(creatures)} creatures shown, {rep['built']} built once each ({tris} triangles), "
              f"{rep['seconds']:.2f} s for the map, {rep['seconds'] / rep['built']:.2f} s per creature "
              f"(slowest {max(rep['per_creature_s'].values()):.2f} s)", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the creature test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("mission1: stock and combined creatures show as models")
        test_mission1(game_dir)
        section("saving: no edits byte for byte, a moved creature moves")
        test_save(game_dir)
        section("a creature that cannot be built stays an empty, with the reason")
        test_cannot_build(game_dir)
        section("the biggest maps")
        test_big_maps(game_dir)
    print("SO_MAP_CREATURES_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_creatures", main)
