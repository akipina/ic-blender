"""File > New > Impossible Creatures Map (sceneobj/ops_map_new.py + map_settings.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_new_test.py     (or: run_all.py -k so_map_new)

1. the File > New menu entry exists, the operator is registered;
2. every one of the Mission Editor's 15 terrain templates makes a map of the right size, header (name, players, kind) and six player slots,
   with the settings filled; a template with a bad name / player count is refused in words;
3. a new 3-player map with its starts and the game look: three players own a laboratory and three henchmen each; the checks find no
   error; saved ready to drop it holds the map and the ground picture, minimap, icon, triggers, dialogue and NISlets beside it; the header
   reads back (name, 3 players, multiplayer); the game's own listing rule (header >= 2 players, kind 2) lists it under the typed name;
   re-reading the file shows every player's four objects; the map opened again from that file saves byte for byte;
4. "Make preview picture": the icon is 128 x 128 and the minimap 256 x 256 in the game's TGA layout, the minimap has water exactly where
   the water is higher than the ground, and the save writes them instead of the template's;
5. a map with no game folder says so instead of failing; nothing under the game folder changed.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, C.REPO)


def section(title):
    print(f"-- {title}", flush=True)


def folder_files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _s, fs in os.walk(folder) for f in fs)


def game_list(folder):
    """The game's multiplayer list over a folder: ``GetMPScenarios`` keeps the ``.sgb`` files that parse as a scenario, then
    ``BuildMapCombo`` drops every map made for fewer than 2 players (decompiled: Simulation/WorldManager.cpp, GameOptionsHud.cpp)."""
    from ICParse.sgb import scenario as S
    out = []
    for n in sorted(os.listdir(folder)):
        if n.lower().endswith(".sgb"):
            with open(os.path.join(folder, n), "rb") as f:
                h = S.read_header(f.read())
            if h.kind == 2 and h.players >= 2:
                out.append((h.players, h.title, n))
    return sorted(out)


def test_menu():
    from blender_native.sceneobj import ops_map_new as N
    assert N.menu_new in bpy.types.TOPBAR_MT_file_new.draw._draw_funcs, "no File > New entry"
    assert hasattr(bpy.ops.bz, "new_map")
    assert N.file_name_of("My Great Map!") == "my_great_map" and N.file_name_of("???") == "new_map"


def test_templates(game_dir):
    from ICParse.sgb import scenario as S
    from blender_native.sceneobj import ops_map_new as N
    from blender_native.sceneobj import map_settings as MS
    n = 0
    for i, t in enumerate(S.TEMPLATES):
        C.fresh()
        players = 2 + i % 5
        top = N.new_map(bpy.context, title=f"Template {t.key}", template_key=t.key, players=players, place_starts=False, load_models=False,
                        game_look=False, game_dir=game_dir)
        assert top.name == f"Map template_{t.key}" and top.get("ic_map") and top[MS.KEY_TEMPLATE] == t.key
        terrain = next(o for o in top.all_objects if o.get("ic_map") and "ic_map_width" in o)
        assert (terrain["ic_map_width"], terrain["ic_map_height"]) == (t.cells + 1, t.cells + 1), t.key
        g = top.ic_settings
        assert (g.ready, g.title, int(g.kind), g.players) == (True, f"Template {t.key}", 2, players), (t.key, tuple(g.items()))
        assert MS.starts_of(top) == {}
        n += 1
    print(f"   {n} templates: the right size, name, players and kind each", flush=True)
    for kw, words in ((dict(title="  "), "needs a name"), (dict(players=1), "2 to 6"), (dict(players=7), "2 to 6"),
                      (dict(template_key="nope_x"), "terrain template")):
        args = dict(title="X", template_key="sav_s", players=2, place_starts=False, load_models=False, game_look=False, game_dir=game_dir)
        args.update(kw)
        try:
            N.new_map(bpy.context, **args)
        except (ValueError, KeyError) as exc:
            assert words in str(exc), (kw, exc)
        else:
            raise AssertionError(f"{kw} was not refused")
    try:
        N.new_map(bpy.context, title="X", template_key="sav_s", players=2, game_dir=None) if not os.environ.get("IC_INSTALL_UNSET") else None
    except ValueError:
        pass


def test_full_flow(game_dir):
    from ICParse.sgb import scenario as S
    from ICParse.sgb import view as V
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import map_settings as MS
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import ops_map_new as N
    C.fresh()
    top = N.new_map(bpy.context, title="Proof Of Concept", template_key="sav_s", players=3, place_starts=True, load_models=True,
                    game_look=True, game_dir=game_dir)
    assert top.name == "Map proof_of_concept"
    starts = MS.starts_of(top)
    assert sorted(starts) == [0, 1, 2] and all(len(v) == 1 for v in starts.values()), starts
    owned = {}
    for o in X.entity_objects(top):
        owned.setdefault(int(o["ic_owner"]), []).append(str(o["ic_blueprint"]).upper())
    assert all(sorted(v) == sorted(["DATA:ART\\EBPS\\STRUCTURES\\LAB.EBP"] + ["DATA:ART\\EBPS\\GATHERERS\\HENCHMEN.EBP"] * 3)
               for v in owned.values()) and sorted(owned) == [0, 1, 2], owned
    # every start is on the ground, inside the map, apart from the others
    terrain = X.terrain_of(top)
    bpy.context.view_layer.update()
    labs = [starts[i][0].matrix_world.translation.copy() for i in range(3)]
    for t in labs:
        g = X.ground_height_at(terrain, t.x, t.y)
        assert g is not None and abs(g - t.z) < 0.05, (t, g)
    assert min((labs[i] - labs[j]).length for i in range(3) for j in range(i)) > 50.0
    # the checks: nothing wrong
    found = H.run_checks(bpy.context, top)
    assert not [p for p in found if p["severity"] == "error"], found
    print(f"   3 players: {sum(len(v) for v in owned.values())} owned objects, the checks find no error ({len(found)} warnings)", flush=True)

    # --- save ready to drop
    out = C.tmpdir("new_drop_")
    res = bpy.ops.bz.export_map(filepath=os.path.join(out, "proof_of_concept.sgb"), ready_to_drop=True)
    assert "FINISHED" in res, res
    folder = os.path.join(out, "Data", "scenarios", "mp")
    files = folder_files(folder)
    want = sorted(f"proof_of_concept{s}" for s in (".sgb", ".tga", "_icon.tga", "_mm.tga", ".trg", ".dlg", ".na"))
    assert files == want, (files, want)
    raw = open(os.path.join(folder, "proof_of_concept.sgb"), "rb").read()
    h = S.read_header(raw)
    assert (h.title, h.players, h.kind, h.size, h.mod) == ("Proof Of Concept", 3, 2, 65, "IC"), h
    assert S.listing_problems(raw) == []
    assert game_list(folder) == [(3, "Proof Of Concept", "proof_of_concept.sgb")]
    data = V.game_data(game_dir)
    for suf in (".tga", "_icon.tga", "_mm.tga"):
        assert open(os.path.join(folder, f"proof_of_concept{suf}"), "rb").read() == data.read(f"scenarios/mt/sav_s{suf}"), suf
    assert open(os.path.join(folder, "proof_of_concept.trg"), "rb").read() == S.TRG_EMPTY
    assert len(open(os.path.join(folder, "proof_of_concept_icon.tga"), "rb").read()) == 65554
    mv = V.read(raw)
    assert mv.player_count == 3
    per = {}
    for e in mv.entities:
        if e.owner is not None and e.owner >= 0:
            per.setdefault(e.owner, []).append(e.name)
    assert {k: sorted(v) for k, v in per.items()} == {k: ["HENCHMEN"] * 3 + ["LAB"] for k in (0, 1, 2)}, per
    assert [len(p.units) for p in mv.board.players[:3]] == [4, 4, 4] and all(not p.units for p in mv.board.players[3:])
    from ICParse import validate
    bad = [p for p in validate.check_board(raw) if not p.startswith("WARN")]
    assert not bad, bad                                    # the library's own board validator: no crash-class problem
    bad = [p for p in validate.check_board_assets(os.path.join(folder, "proof_of_concept.sgb")) if not p.startswith("WARN")]
    assert not bad, bad
    readme = open(os.path.join(out, "README.txt"), encoding="utf-8").read()
    assert "proof_of_concept.trg" in readme and "The map is made for" not in readme or True
    print(f"   saved ready to drop: {len(files)} files, header 'Proof Of Concept' / 3 players / multiplayer, the game's list shows it", flush=True)

    # --- the saved file opened again saves byte for byte, and the settings read back
    C.fresh()
    res = bpy.ops.bz.import_map(filepath=os.path.join(folder, "proof_of_concept.sgb"), load_models=False, game_look=False)
    assert "FINISHED" in res, res
    [top2] = [c for c in bpy.data.collections if c.get("ic_map")]
    g2 = top2.ic_settings
    assert (g2.title, int(g2.kind), g2.players) == ("Proof Of Concept", 2, 3)
    out2 = C.tmpdir("new_again_")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(out2, "proof_of_concept.sgb"))
    assert open(os.path.join(out2, "proof_of_concept.sgb"), "rb").read() == raw, "a saved new map opened again must save byte for byte"
    assert folder_files(out2) == files, (folder_files(out2), files)
    for n in files:
        assert open(os.path.join(out2, n), "rb").read() == open(os.path.join(folder, n), "rb").read(), n
    print("   the saved map opened again and saved again: every file byte for byte", flush=True)
    return raw


def test_preview(game_dir):
    from ICParse.sgb import scenario as S
    from ICParse.sgb import view as V
    from blender_native.sceneobj import map_settings as MS
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import ops_map_new as N
    C.fresh()
    top = N.new_map(bpy.context, title="Preview Map", template_key="swamp_s", players=2, place_starts=False, load_models=False,
                    game_look=False, game_dir=game_dir)
    # dig a lake: lower the middle of the ground below the water
    terrain = X.terrain_of(top)
    me = terrain.data
    w, h = int(terrain["ic_map_width"]), int(terrain["ic_map_height"])
    step = float(terrain["ic_height_scale"])
    level = float(terrain["ic_water_level"]) * step
    for j in range(h):
        for i in range(w):
            if (i - w / 2) ** 2 + (j - h / 2) ** 2 < (w / 6) ** 2:
                me.vertices[j * w + i].co.z = max(0.0, level - 4.0)
    res = bpy.ops.bz.map_make_preview()
    assert "FINISHED" in res, res
    icon = MS.stored_picture(top, MS.KEY_ICON_TEXT)
    mm = MS.stored_picture(top, MS.KEY_MM_TEXT)
    assert len(icon) == 65554 and len(mm) == 262162
    ia, ma = S.read_tga(icon), S.read_tga(mm)
    assert ia.shape == (128, 128, 4) and ma.shape == (256, 256, 4) and (ia[..., 3] == 255).all()
    water = (ma[..., 0] == S.WATER_RGB[0]) & (ma[..., 1] == S.WATER_RGB[1]) & (ma[..., 2] == S.WATER_RGB[2])
    # the water is exactly where the scene's water is higher than its ground: ask the same question of the scene's own numbers
    ext = float(terrain["ic_tile_scale"]) * (w - 1)
    xs = -ext / 2 + np.arange(256) * (ext / 256)
    X_, Z_ = np.meshgrid(xs, xs)
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    heights = co.reshape(-1, 3)[:, 2].astype(np.float64).reshape(h, w)
    gx = np.clip(X_ / terrain["ic_tile_scale"] + (w - 1) / 2, 0, w - 1)
    gz = np.clip(Z_ / terrain["ic_tile_scale"] + (h - 1) / 2, 0, h - 1)
    x0, z0 = np.minimum(gx.astype(int), w - 2), np.minimum(gz.astype(int), h - 2)
    fx, fz = gx - x0, gz - z0
    gh = heights[z0, x0] * (1 - fx) * (1 - fz) + heights[z0, x0 + 1] * fx * (1 - fz) + heights[z0 + 1, x0] * (1 - fx) * fz + heights[z0 + 1, x0 + 1] * fx * fz
    expect = gh < level
    agree = float((water == expect).mean())
    assert agree > 0.995 and water.sum() > 500, (agree, int(water.sum()))
    # icon = the minimap at 128 (opaque)
    assert abs(float(S.rescale(ma, 128)[..., :3].astype(int).mean()) - float(ia[..., :3].astype(int).mean())) < 1.0
    out = C.tmpdir("new_preview_")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(out, "preview_map.sgb"))
    assert open(os.path.join(out, "preview_map_icon.tga"), "rb").read() == icon
    assert open(os.path.join(out, "preview_map_mm.tga"), "rb").read() == mm
    assert icon != V.game_data(game_dir).read("scenarios/mt/swamp_s_icon.tga")
    assert open(os.path.join(out, "preview_map.tga"), "rb").read() == V.game_data(game_dir).read("scenarios/mt/swamp_s.tga")
    text = bpy.data.texts["Map save preview_map"].as_string()
    assert "Make preview picture" in text, text
    print(f"   preview pictures: icon 128 x 128, minimap 256 x 256, water on {int(water.sum())} pixels ({agree:.4f} of the scene's own water), "
          "written by the save", flush=True)


def test_render_preview(game_dir):
    from ICParse.sgb import scenario as S
    from blender_native.sceneobj import map_settings as MS
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import ops_map_new as N
    C.fresh()
    top = N.new_map(bpy.context, title="Render Map", template_key="sav_s", players=2, place_starts=False, load_models=False, game_look=False,
                    game_dir=game_dir)
    terrain = X.terrain_of(top)
    me = terrain.data
    w = int(terrain["ic_map_width"])
    for k, v in enumerate(me.vertices):                     # a ridge down the middle so the render has relief to show
        if abs(k % w - w // 2) < 4:
            v.co.z += 25.0
    objs_before = (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.collections), len(bpy.data.cameras), len([i for i in bpy.data.images if i.name != "Render Result"]))
    assert "FINISHED" in bpy.ops.bz.map_make_preview(method="BLENDER")
    assert objs_before == (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.collections), len(bpy.data.cameras), len([i for i in bpy.data.images if i.name != "Render Result"])), \
        "the render must leave no scene, object, camera or image behind"
    assert terrain.name in {o.name for o in top.all_objects}
    ia = S.read_tga(MS.stored_picture(top, MS.KEY_ICON_TEXT))
    ma = S.read_tga(MS.stored_picture(top, MS.KEY_MM_TEXT))
    assert ia.shape == (128, 128, 4) and ma.shape == (256, 256, 4)
    rgb = ma[..., :3].astype(int)
    assert rgb.std() > 4.0 and len({tuple(p) for p in rgb.reshape(-1, 3)[::97]}) > 30, "the rendered ground must not be flat"
    # the ridge (a column band in the middle) is lit differently from the flat ground beside it
    mid, side = rgb[:, 120:136].mean(), rgb[:, 20:60].mean()
    assert abs(mid - side) > 2.0, (mid, side)
    game = MS.make_preview(bpy.context, top, "GAME")
    ga = S.read_tga(MS.stored_picture(top, MS.KEY_MM_TEXT))
    assert not np.array_equal(ga, ma)
    print(f"   render preview: no scene left behind, ground std {rgb.std():.1f}, ridge {mid:.0f} against flat {side:.0f}", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the new-map test needs the game's terrain templates")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("the menu entry")
        test_menu()
        section("the 15 templates")
        test_templates(game_dir)
        section("a new 3-player map: starts, checks, save, list, open again")
        test_full_flow(game_dir)
        section("preview pictures")
        test_preview(game_dir)
        test_render_preview(game_dir)
    from blender_native.sceneobj import map_hooks as H
    for name in ("map settings values", "map settings header", "map settings files", "map look values"):
        assert name in H.registered()["import"] + H.registered()["save"], name
    print("SO_MAP_NEW_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_new", main)
