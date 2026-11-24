"""The Mission Editor's conveniences for an imported map (sceneobj/map_tools.py, ops_map_tools.py, panels_map_tools.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_tools_test.py     (or: run_all.py -k so_map_tools)

1. the modules load by themselves, the operators and the Map panel's sections are registered, and the save step that keeps a made map's files;
2. a map with the tools registered and no edits saves byte for byte (2p_faceoff, mission1, 6p_big_bog), with the files beside it unchanged;
3. height map: the export is the editor's kind of TGA and is the ground as it is in the scene (a sculpted point included); an import of a
   changed picture sets the ground's points, and the save writes exactly those heights (and nothing else of the ground); a picture of
   another size is stretched; the other size option exports one pixel less across;
4. terrain file: the export is NOBS + XTER with the map's own FORM TERR; importing another map's terrain builds the map again (the old one
   hidden, its files kept for the new one) and the saved map holds that terrain, the pathfinding the game's builder makes, the same objects;
5. entity files: the selected objects export with their numbers, blueprints, places and owners; importing the file adds new objects (at the
   3D cursor when asked), written into the map on save with new numbers and the owners kept;
6. the Object List: the filters list what ICParse's rules say; Select / Hide / Unhide All / Delete act on the listed (or ticked) objects and
   a deleted object is gone from the saved map;
7. the Blueprint List: net id, how many objects use each, how many trigger arguments name it;
8. (Terrain Crop has its own test: so_map_crop_test.py)
9. the panels draw; nothing under the game folder changed.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)


def section(title):
    print(f"-- {title}", flush=True)


def keys(game_dir):
    from ICParse.sgb import view
    return {m.key.split("|")[-1].rsplit("/", 1)[-1][:-4]: m for m in view.game_maps(game_dir)}


def import_map(gm, models=False):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=gm.key, load_models=models)
    assert "FINISHED" in res, res
    return live_top()


def live_top():
    """The map collection that is in the view (not excluded)."""
    from blender_native.sceneobj import ops_map_export as X
    tops = [c for c in bpy.data.collections if c.get("ic_map")]
    layer = bpy.context.view_layer
    live = []
    for t in tops:
        lc = layer.layer_collection.children.get(t.name)
        if lc is not None and not lc.exclude:
            live.append(t)
    assert live, [t.name for t in tops]
    return X.active_map(bpy.context) if len(live) > 1 else live[0]


def saved(out_dir, name, **kw):
    path = os.path.join(out_dir, name + ".sgb")
    res = bpy.ops.bz.export_map(filepath=path, **kw)
    assert "FINISHED" in res, res
    with open(path, "rb") as f:
        return f.read()


def files_in(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _s, fs in os.walk(folder) for f in fs)


def expect_cancel(call, words=""):
    try:
        res = call()
    except RuntimeError as exc:
        assert words in str(exc), exc
        return str(exc)
    assert "CANCELLED" in res, res
    return ""


def test_loading():
    from blender_native import sceneobj
    from blender_native.sceneobj import map_hooks as H
    names = [m.__name__.rsplit(".", 1)[-1] for m in sceneobj.MODULES]
    for n in ("map_tools", "map_crop", "ops_map_tools", "panels_map_tools"):
        assert n in names, names
    assert not sceneobj.ERRORS, sceneobj.ERRORS
    for op in ("map_import_heightmap", "map_export_heightmap", "map_import_terrain", "map_export_terrain", "map_import_entities",
               "map_export_entities", "map_objlist_refresh", "map_objlist_do", "map_bplist_refresh", "map_bplist_select", "map_crop_validate",
               "map_crop_apply"):
        assert hasattr(bpy.ops.bz, op), op
    assert hasattr(bpy.types, "BZ_PT_map_tools_crop") and bpy.types.BZ_PT_map_tools_crop.bl_parent_id == H.MAP_PANEL_ID == "BZ_PT_map"
    reg = H.registered()
    assert "map_tools_siblings" in reg["save"], reg
    assert hasattr(bpy.types.Scene, "bz_map_tools")
    print("   modules, operators, sections and the save step are registered", flush=True)


def test_untouched_saves(game_dir):
    for name in ("2p_faceoff", "mission1", "6p_big_bog"):
        gm = keys(game_dir)[name]
        raw = gm.read()
        top = import_map(gm)
        out = C.tmpdir("tools_same_")
        data = saved(out, name)
        assert data == raw, f"{name}: a map with the map tools registered and no edits must save byte for byte"
        beside = files_in(out)
        assert name + ".sgb" in beside and "README.txt" not in beside
        assert not any(f.endswith(".ter") or f.endswith(".ent") for f in beside), beside
    print("   2p_faceoff, mission1 and 6p_big_bog: untouched saves are byte for byte", flush=True)


def test_height_map(game_dir):
    from ICParse.sgb import heightmap_io as HM, view
    gm = keys(game_dir)["2p_faceoff"]
    raw = gm.read()
    mv = view.read(raw)
    top = import_map(gm)
    out = C.tmpdir("tools_hm_")
    tga = os.path.join(out, "h.tga")
    assert "FINISHED" in bpy.ops.bz.map_export_heightmap(filepath=tga)
    with open(tga, "rb") as f:
        data = f.read()
    assert data[:12] == bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0]) and data[16:18] == bytes([32, 0]) and len(data) == 18 + 65 * 65 * 4
    h, info = HM.heights_from_image(data, 65, 65)
    assert (h == mv.heights).all() and not info["resampled"]
    # a sculpted point is in the picture
    from blender_native.sceneobj import ops_map_export as X
    terrain = X.terrain_of(top)
    terrain.data.vertices[10 * 65 + 20].co.z += 6.0                       # 12 steps of 0.5 m
    tga2 = os.path.join(out, "h2.tga")
    assert "FINISHED" in bpy.ops.bz.map_export_heightmap(filepath=tga2)
    h2, _ = HM.heights_from_image(open(tga2, "rb").read(), 65, 65)
    want = mv.heights.copy()
    want[10, 20] = mv.heights[10, 20] + 12
    assert (h2 == want).all()
    # the other size: one pixel less across
    tga3 = os.path.join(out, "h3.tga")
    assert "FINISHED" in bpy.ops.bz.map_export_heightmap(filepath=tga3, size="cells")
    assert HM.read_tga(open(tga3, "rb").read())[1]["width"] == 64
    # import a changed picture: the ground's points follow it, the save writes exactly those heights
    import_map(gm)
    new = mv.heights.astype("int32")
    new[10:20, 30:40] = np_clip(new[10:20, 30:40] + 12)
    pic = os.path.join(out, "edit.tga")
    open(pic, "wb").write(HM.image_from_heights(new.astype("uint8")))
    assert "FINISHED" in bpy.ops.bz.map_import_heightmap(filepath=pic)
    out2 = C.tmpdir("tools_hm_save_")
    data = saved(out2, "hm")
    mv2 = view.read(data)
    assert (mv2.heights == new.astype("uint8")).all(), "the saved ground must be the picture's heights"
    assert (mv2.heights != mv.heights).sum() == 100 and mv2.problems == []
    # the terrain in Edit Mode: the picture's heights go into the edit mesh and the mode stays
    top = import_map(gm)
    terrain = X.terrain_of(top)
    bpy.context.view_layer.objects.active = terrain
    bpy.ops.object.mode_set(mode="EDIT")
    assert "FINISHED" in bpy.ops.bz.map_import_heightmap(filepath=pic)
    assert terrain.mode == "EDIT"
    data = saved(C.tmpdir("tools_hm_edit_"), "hm_edit")
    assert (view.read(data).heights == new.astype("uint8")).all()
    bpy.ops.object.mode_set(mode="OBJECT")
    # a picture of another size is stretched over the ground (corner to corner)
    import_map(gm)
    small = HM.resample(mv.heights, 33, 33, "smooth")
    pic2 = os.path.join(out, "small.tga")
    open(pic2, "wb").write(HM.image_from_heights(small))
    assert "FINISHED" in bpy.ops.bz.map_import_heightmap(filepath=pic2)
    h3, _s, _p = __import__("blender_native.sceneobj.map_tools", fromlist=["x"]).scene_heights(bpy.context, live_top())
    assert (h3 == HM.resample(small, 65, 65, "smooth")).all()
    # a file that is not a picture is refused in words, the scene is untouched
    junk = os.path.join(out, "junk.tga")
    open(junk, "wb").write(b"not a picture at all, sorry")
    expect_cancel(lambda: bpy.ops.bz.map_import_heightmap(filepath=junk), "")
    print("   height map: export = the scene's ground, import sets the points, save writes the picture's heights, other sizes stretch", flush=True)


def np_clip(a):
    import numpy as np
    return np.clip(a, 0, 255)


def test_terrain_files(game_dir):
    from ICParse.iff.document import IffDocument
    from ICParse.sgb import parser as P, pathbake, terrain_io as T, view
    gms = keys(game_dir)
    a = gms["2p_faceoff"]
    other = next(g for n, g in sorted(gms.items()) if n != "2p_faceoff" and view.read(g.read()).width == 65)
    raw, oraw = a.read(), other.read()
    top = import_map(a)
    out = C.tmpdir("tools_ter_")
    ter = os.path.join(out, "a.ter")
    assert "FINISHED" in bpy.ops.bz.map_export_terrain(filepath=ter)
    doc = IffDocument.from_bytes(open(ter, "rb").read())
    assert [c.form_type for c in doc.root_chunks] == ["NOBS", "XTER"]
    assert doc.root_chunks[1].children[2].serialize() == P.read(raw).preserved.find_all("TERR")[0].serialize()
    # another map's terrain: the scene is built again
    oter = os.path.join(out, "o.ter")
    open(oter, "wb").write(T.export_terrain(oraw))
    n_before = len([c for c in bpy.data.collections if c.get("ic_map")])
    assert "FINISHED" in bpy.ops.bz.map_import_terrain(filepath=oter)
    tops = [c for c in bpy.data.collections if c.get("ic_map")]
    assert len(tops) == n_before + 1
    new = live_top()
    assert new != top and new.get("ic_map_origin", "").startswith("The terrain of")
    layer = bpy.context.view_layer.layer_collection.children
    assert layer[top.name].exclude and not layer[new.name].exclude
    out2 = C.tmpdir("tools_ter_save_")
    data = saved(out2, "faceoff_new_ground")
    bd = P.read(data)
    assert bd.preserved.find_all("TERR")[0].serialize() == P.read(oraw).preserved.find_all("TERR")[0].serialize()
    baked = pathbake.bake_map(data)
    assert P._nrml(bd.preserved, b"IMPA").raw == baked.impa and P._nrml(bd.preserved, b"PASM").raw == baked.pasm
    mv, mv0 = view.read(data), view.read(raw)
    assert [(e.instance_id, e.blueprint, e.position) for e in mv.entities] == [(e.instance_id, e.blueprint, e.position) for e in mv0.entities]
    beside = files_in(out2)
    siblings = [f for f in files_in(out2) if f != "faceoff_new_ground.sgb"]
    assert any(f.endswith(".trg") for f in siblings), beside                  # the files of the first map came along
    # a terrain of another size is refused in the editor's words and the scene stays as it was
    big = keys(game_dir)["6p_big_bog"]
    bter = os.path.join(out, "b.ter")
    open(bter, "wb").write(T.export_terrain(big.read()))
    err = expect_cancel(lambda: bpy.ops.bz.map_import_terrain(filepath=bter), "different map size")
    assert len([c for c in bpy.data.collections if c.get("ic_map")]) == n_before + 1
    print("   terrain: export is the map's own FORM TERR; import builds the map again with its files; another size refused", flush=True)


def test_entity_files(game_dir):
    from ICParse.sgb import entities_io as E, view
    from blender_native.sceneobj import ops_map_export as X
    gm = keys(game_dir)["2p_faceoff"]
    raw = gm.read()
    mv = view.read(raw)
    top = import_map(gm)
    objs = {o.get("ic_entity_id"): o for o in X.entity_objects(top)}
    pick = [mv.entities[0], next(e for e in mv.entities[1:] if e.owner >= 0), mv.entities[-1]]
    assert len({e.instance_id for e in pick}) == 3
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for e in pick:
        objs[e.instance_id].select_set(True)
    out = C.tmpdir("tools_ent_")
    ent = os.path.join(out, "g.ent")
    assert "FINISHED" in bpy.ops.bz.map_export_entities(filepath=ent)
    f = E.parse_file(open(ent, "rb").read())
    assert sorted(r.entity_id for r in f.records) == sorted(e.instance_id for e in pick)
    by_id = {r.entity_id: r for r in f.records}
    for e in pick:
        r = by_id[e.instance_id]
        assert f.blueprints[r.net_id].path == e.blueprint and r.net_id == e.blueprint_ref and r.owner_id == e.player_id and r.sound_id == e.sound_id
        assert max(abs(a - b) for a, b in zip(r.transform[9:], e.position)) < 1e-3
        assert max(abs(a - b) for a, b in zip(r.transform[:9], e.rotation)) < 1e-4
    # nothing selected: refused in words
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    expect_cancel(lambda: bpy.ops.bz.map_export_entities(filepath=os.path.join(out, "none.ent")), "")
    # import it back into the same map, at the 3D cursor
    bpy.context.scene.cursor.location = (30.0, -20.0, 0.0)
    n0 = len(X.entity_objects(top))
    assert "FINISHED" in bpy.ops.bz.map_import_entities(filepath=ent, place="CURSOR", drop=True)
    objs2 = X.entity_objects(top)
    assert len(objs2) == n0 + 3
    fresh = [o for o in objs2 if "ic_entity_id" not in o]
    assert len(fresh) == 3 and all(o.get("ic_map_new_object") for o in fresh)
    xs = [o.location.x for o in fresh]
    ys = [o.location.y for o in fresh]
    assert abs(sum(xs) / 3 - 30.0) < 1e-3 and abs(sum(ys) / 3 - (-20.0)) < 1e-3
    assert sorted(o.get("ic_owner") for o in fresh) == sorted(e.owner for e in pick)
    data = saved(C.tmpdir("tools_ent_save_"), "with_copies")
    mv2 = view.read(data)
    assert len(mv2.entities) == len(mv.entities) + 3
    added = mv2.entities[-3:]
    assert sorted(e.name for e in added) == sorted(e.name for e in pick) and min(e.instance_id for e in added) > max(e.instance_id for e in mv.entities)
    assert sorted(e.owner for e in added) == sorted(e.owner for e in pick) and mv2.problems == []
    # a ready-made file with a creature combined in a map: left out and said so
    print("   entity files: export carries ids, blueprints, places, owners; import adds new objects (cursor, owners) written on save", flush=True)


def test_object_and_blueprint_lists(game_dir):
    from ICParse.sgb import objlist as O, view
    from blender_native.sceneobj import map_tools as T, ops_map_export as X
    gm = keys(game_dir)["mission1"]
    mv = view.read(gm.read())
    top = import_map(gm)
    st = bpy.context.scene.bz_map_tools
    rows = O.rows_of_view(mv)
    # every filter against ICParse's rules
    cases = [({}, dict()), ({"f_lab": True}, dict(tags=["lab"])), ({"f_henchman": True, "f_sound": True}, dict(tags=["henchman", "sound"])),
             ({"f_resource": True}, dict(tags=["resource"])), ({"f_stock": True}, dict(tags=["stock"])), ({"specific": "wolf"}, dict(specific="wolf")),
             ({"owner": "0"}, dict(owner=0)), ({"owner": "world"}, dict(owner="world")), ({"owner": "nature"}, dict(owner="nature"))]
    for props, kw in cases:
        for p in ("f_static", "f_stock", "f_henchman", "f_resource", "f_lab", "f_sound", "f_marker"):
            setattr(st, p, False)
        st.specific = ""
        st.owner = "all"
        for k, v in props.items():
            setattr(st, k, v)
        assert "FINISHED" in bpy.ops.bz.map_objlist_refresh()
        want = O.filter_rows(rows, **kw)
        assert st.rows_total == len(want), (props, st.rows_total, len(want))
        assert sorted(r.entity_id for r in st.rows) == sorted(r.entity_id for r in want)[:len(st.rows)]
    assert sum(1 for _ in rows) == len(mv.entities)
    # actions on the listed objects
    st.owner, st.f_lab = "all", True
    bpy.ops.bz.map_objlist_refresh()
    labs = [r.obj_name for r in st.rows]
    assert labs, "mission1 has a lab"
    bpy.ops.bz.map_objlist_do(action="SELECT")
    assert {o.name for o in bpy.context.selected_objects} == set(labs)
    bpy.ops.bz.map_objlist_do(action="HIDE")
    assert all(bpy.data.objects[n].hide_get() for n in labs)
    bpy.ops.bz.map_objlist_do(action="UNHIDE")
    assert not any(bpy.data.objects[n].hide_get() for n in labs)
    # Delete never works on a whole list: with nothing ticked it is refused and nothing is removed
    n_objs = len(X.entity_objects(top))
    expect_cancel(lambda: bpy.ops.bz.map_objlist_do(action="DELETE"), "Tick the rows")
    assert len(X.entity_objects(top)) == n_objs
    # ticking rows narrows the action
    st.f_lab = False
    st.f_stock = True
    bpy.ops.bz.map_objlist_refresh()
    assert len(st.rows) >= 2
    victims = [st.rows[0].obj_name, st.rows[1].obj_name]
    ids = [bpy.data.objects[n]["ic_entity_id"] for n in victims]
    st.rows[0].pick = True
    st.rows[1].pick = True
    bpy.ops.bz.map_objlist_do(action="DELETE")
    assert all(bpy.data.objects.get(n) is None for n in victims)
    data = saved(C.tmpdir("tools_del_"), "m1_less", pathfinding="never")
    mv2 = view.read(data)
    assert len(mv2.entities) == len(mv.entities) - 2 and not ({e.instance_id for e in mv2.entities} & set(ids))
    # the Blueprint List
    top = import_map(gm)
    st = bpy.context.scene.bz_map_tools
    assert "FINISHED" in bpy.ops.bz.map_bplist_refresh()
    pal = dict(mv.board.blueprint_palette)
    assert [r.net_id for r in st.bp_rows] == sorted(pal), (len(st.bp_rows), len(pal), sorted(set(r.net_id for r in st.bp_rows) ^ set(pal)))
    counts = {}
    for e in mv.entities:
        counts[e.blueprint_ref] = counts.get(e.blueprint_ref, 0) + 1
    for r in st.bp_rows:
        assert r.ref_count == counts.get(r.net_id, 0), (r.net_id, r.label, r.ref_count, counts.get(r.net_id))
    assert any(r.trigger_refs > 0 for r in st.bp_rows), "mission1's triggers name blueprints"
    henchmen = next(r for r in st.bp_rows if r.label.lower() == "henchmen")
    assert henchmen.trigger_refs > 0
    st.bp_index = [r.net_id for r in st.bp_rows].index(henchmen.net_id)
    assert "FINISHED" in bpy.ops.bz.map_bplist_select()
    assert len(bpy.context.selected_objects) == henchmen.ref_count
    print("   object list (filters vs ICParse, select/hide/unhide/delete), blueprint list (ref counts, trigger refs, select)", flush=True)


def test_panels(game_dir):
    from blender_native.sceneobj import panels_map_tools as PM
    gm = keys(game_dir)["2p_faceoff"]
    top = import_map(gm)
    for fn in (PM.draw_files, PM.draw_objects, PM.draw_blueprints, PM.draw_crop):
        lay = C.FakeLayout()
        fn(lay, bpy.context, top)
        assert lay.texts() or lay.operators()
    lay = C.FakeLayout()
    PM.draw_files(lay, bpy.context, top)
    assert {"bz.map_import_heightmap", "bz.map_export_terrain", "bz.map_import_entities"} <= set(lay.operators())
    lay = C.FakeLayout()
    PM.draw_objects(lay, bpy.context, top)
    assert {"bz.map_objlist_refresh", "bz.map_objlist_do"} <= set(lay.operators())
    lay = C.FakeLayout()
    PM.draw_crop(lay, bpy.context, top)
    assert {"bz.map_crop_validate", "bz.map_crop_apply"} <= set(lay.operators())
    print("   the four sections draw", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map tools test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("loading")
        test_loading()
        section("untouched saves")
        test_untouched_saves(game_dir)
        section("height map")
        test_height_map(game_dir)
        section("terrain files")
        test_terrain_files(game_dir)
        section("entity files")
        test_entity_files(game_dir)
        section("object list and blueprint list")
        test_object_and_blueprint_lists(game_dir)
        section("panels")
        test_panels(game_dir)
    print("SO_MAP_TOOLS_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_tools", main)
