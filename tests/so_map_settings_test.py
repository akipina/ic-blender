"""Map settings, the Terrain look and the Map problems list (sceneobj/map_settings.py, map_look.py, ops_map_settings.py,
panels_map_settings.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_settings_test.py     (or: run_all.py -k so_map_settings)

1. three stock maps (2p_faceoff, mission1, 6p_big_bog) opened and saved with no edits: the map file and every file beside it are byte for
   byte what the game holds, with the settings and look steps registered; the settings and the look hold the map's own values;
2. the settings: a new name, player count and kind are written into the header and nothing else of the file changes; a single-player kind
   saved in the multiplayer folder says so; a name that is empty or too long, or a player count the map has no slots for, refuses the save;
3. the Terrain look: every value shows at once on the look objects (sun colour and direction, shadow colour, fog volume, water brightness and
   opacity, a new sky picture builds the look again) and is written by the save: only the records of the values changed differ, the file
   reads back the values, and putting a value back to the map's own writes nothing;
4. Make preview picture: the icon and minimap are the game's sizes and saved beside the map;
5. Map problems: a player without a start, a start for a player the map does not have, an object outside the map, a blueprint the game does
   not have, ground out of range, a multiplayer map for one player, a shadow colour not darker than the sunlight are all listed, with
   Locate selecting the object; the panels draw without error;
nothing under the game folder changed.
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

FACEOFF = "Engine.sga|scenarios/mp/2p_faceoff.sgb"
MISSION1 = "Engine.sga|scenarios/sp/mission1.sgb"
BIGBOG = "InsectData.sga|scenarios/mp/6p_big_bog.sgb"
ICE_CLASH = "Data|scenarios/mp/ice clash.sgb"


def section(title):
    print(f"-- {title}", flush=True)


def folder_files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _s, fs in os.walk(folder) for f in fs)


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def open_map(key, *, look=False):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=False, show_creatures=False, game_look=look)
    assert "FINISHED" in res, (key, res)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def save(top, stem):
    out = C.tmpdir("settings_")
    res = bpy.ops.bz.export_map(filepath=os.path.join(out, stem + ".sgb"))
    assert "FINISHED" in res, res
    with open(os.path.join(out, stem + ".sgb"), "rb") as f:
        return out, f.read()


def expect_refused(words, stem="x"):
    out = C.tmpdir("settings_refused_")
    try:
        bpy.ops.bz.export_map(filepath=os.path.join(out, stem + ".sgb"))
    except RuntimeError as exc:
        assert words in str(exc), exc
        assert folder_files(out) == [], folder_files(out)
        return str(exc)
    raise AssertionError("the save was not refused")


def test_untouched(game_dir):
    from blender_native.sceneobj import map_hooks as H
    reg = H.registered()
    assert "map settings header" in reg["save"] and "map look values" in reg["save"] and "map settings files" in reg["save"], reg
    for key in (FACEOFF, MISSION1, BIGBOG):
        gm = game_map(game_dir, key)
        raw = gm.read()
        stem = os.path.splitext(os.path.basename(key.split("|")[-1]))[0]
        top = open_map(key)
        assert top.ic_settings.ready and top.ic_look.ready
        from ICParse.sgb import scenario as S, look_edit as L
        h = S.read_header(raw)
        assert (top.ic_settings.title, int(top.ic_settings.kind), top.ic_settings.players) == (h.title, h.kind, h.players)
        v = L.values_of(raw)
        assert tuple(round(x, 5) for x in top.ic_look.sun_colour) == tuple(round(x, 5) for x in v["sun_colour"]), key
        assert top.ic_look.detail_tiling == v["detail_tiling"] and top.ic_look.fog_on == v["fog_on"]
        out, got = save(top, stem)
        assert got == raw, f"{key}: an untouched map must be saved byte for byte"
        # every file beside it is what the game holds: compare with a save that has none of this module's steps
        state = H.snapshot()
        try:
            for name in ("map settings values", "map settings header", "map settings files", "map look values"):
                H.unregister_step(name)
            out0, got0 = save(top, stem)
        finally:
            H.restore(state)
        assert got0 == raw and folder_files(out0) == folder_files(out), (folder_files(out0), folder_files(out))
        for n in folder_files(out):
            assert open(os.path.join(out, n), "rb").read() == open(os.path.join(out0, n), "rb").read(), n
        print(f"   {stem}: {len(raw)} bytes and {len(folder_files(out)) - 1} files beside it, byte for byte", flush=True)


def test_settings(game_dir):
    from ICParse.iff.document import IffDocument
    from ICParse.sgb import scenario as S
    key = FACEOFF
    raw = game_map(game_dir, key).read()
    top = open_map(key)
    g = top.ic_settings
    g.title, g.players = "Face Off Test", 4
    out, got = save(top, "2p_faceoff")
    h = S.read_header(got)
    assert (h.title, h.players, h.kind) == ("Face Off Test", 4, 2), h
    a, b = IffDocument.from_bytes(raw), IffDocument.from_bytes(got)
    ca = [bytes(c.raw) for c in a.find_all("NRML") if bytes(c.raw[:4]) != b"WMHD"]
    cb = [bytes(c.raw) for c in b.find_all("NRML") if bytes(c.raw[:4]) != b"WMHD"]
    assert ca == cb and len(got) != len(raw) or ca == cb
    text = bpy.data.texts["Map save 2p_faceoff"].as_string()
    assert "The map's name is now 'Face Off Test'" in text and "made for 4 players" in text, text
    # a single-player kind goes into the single-player folder of a ready-to-drop save (the folder follows the kind), with no warning
    g.kind = "1"
    out, got = save(top, "2p_faceoff")
    assert S.read_header(got).kind == 1
    drop = C.tmpdir("settings_drop_")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(drop, "2p_faceoff.sgb"), ready_to_drop=True)
    assert os.path.isfile(os.path.join(drop, "Data", "scenarios", "sp", "2p_faceoff.sgb")) and not os.path.isdir(os.path.join(drop, "Data", "scenarios", "mp"))
    text = bpy.data.texts["Map save 2p_faceoff"].as_string()
    assert "puts it in Data/scenarios" not in text, text
    g.kind = "2"
    drop = C.tmpdir("settings_drop2_")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(drop, "2p_faceoff.sgb"), ready_to_drop=True)
    assert os.path.isfile(os.path.join(drop, "Data", "scenarios", "mp", "2p_faceoff.sgb"))
    g.kind = "1"
    # untouched again: back to the original values writes nothing
    g.title, g.players, g.kind = S.read_header(raw).title, S.read_header(raw).players, "2"
    assert save(top, "2p_faceoff")[1] == raw
    # refusals
    g.title = "   "
    expect_refused("needs a name")
    g.title = "x" * 300
    expect_refused("at most")
    g.title = "ok"
    g.players = 8
    expect_refused("player slots")
    print("   name, players and kind: only the header changes; the folder warning; bad values refused", flush=True)


def test_look(game_dir):
    from ICParse.sgb import look_edit as L
    from blender_native.sceneobj import map_look as ML
    from blender_native.sceneobj import ops_map_export as X
    from blender_native import core
    from mathutils import Vector
    raw = game_map(game_dir, FACEOFF).read()
    v0 = L.values_of(raw)
    top = open_map(FACEOFF, look=True)
    g = top.ic_look
    root = ML.look_collection(top)
    assert root is not None
    sun = next(o for o in root.all_objects if o.get("ic_look_piece") == "sun")
    sea = next(o for o in root.all_objects if o.get("ic_look_piece") == "sea")
    name = ML._name(top)

    # --- live: the objects follow the values
    g.sun_colour = (1.0, 0.5, 0.25)
    assert all(abs(a - b) < 1e-5 for a, b in zip(sun.data.color, ML._lin((1.0, 0.5, 0.25))))
    g.time_hour = 15.0
    d = core.blender_to_sgm(np.asarray([list(sun.rotation_quaternion @ Vector((0, 0, -1)))]))[0]
    want = L.direction_of_angle(135.0)
    assert all(abs(a - b) < 1e-5 for a, b in zip(d, want)), (d, want)
    g.shadow_colour = (0.1, 0.2, 0.3)
    world = bpy.data.worlds[f"Map {name} look"]
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    assert all(abs(a - b) < 1e-5 for a, b in zip(bg.inputs["Color"].default_value[:3], ML._lin((0.1, 0.2, 0.3))))
    assert not any(n.type == "VOLUME_SCATTER" for n in world.node_tree.nodes)
    g.fog_on = True
    world = bpy.data.worlds[f"Map {name} look"]
    assert any(n.type == "VOLUME_SCATTER" for n in world.node_tree.nodes) and world["ic_fog_on"] is True
    g.fog_start, g.fog_end = 50.0, 900.0
    assert abs(world["ic_fog_end"] - 900.0) < 1e-3
    g.ocean_colour = (0.9, 0.1, 0.1)                      # the editor's value: saved with the map, not shown (the game never reads it)
    before = np.array([a.value for a in sea.data.attributes["ic_water_grey"].data])
    g.ocean_min = 20
    after = np.array([a.value for a in sea.data.attributes["ic_water_grey"].data])
    assert abs(after[after > 0].min() - 20 / 255.0) < 1e-3 and before[before > 0].min() > 0.7, (before[before > 0].min(), after[after > 0].min())
    other = next(n for n in ("sky_m05.tga", "sky_m01.tga") if n != v0["sky_texture"].lower())
    g.sky = other
    sky = next(o for o in ML.look_collection(top).all_objects if o.get("ic_look_piece") == "sky")
    assert sky["ic_sky_texture"].lower() == other, sky["ic_sky_texture"]
    print("   live: sun colour and direction, shadow colour, fog volume, water brightness, a new sky picture", flush=True)

    # --- saved: only the changed records differ, and the file reads back the values
    g.detail_tiling = 5
    g.shore = 99
    out, got = save(top, "2p_faceoff")
    v = L.values_of(got)
    assert all(abs(a - b) < 1e-5 for a, b in zip(v["sun_colour"], (1.0, 0.5, 0.25))) and abs(v["time_angle"] - 135.0) < 0.05
    assert all(abs(a - b) < 1e-5 for a, b in zip(v["shadow_colour"], (0.1, 0.2, 0.3)))
    assert v["fog_on"] and abs(v["fog_start"] - 50.0) < 1e-3 and abs(v["fog_end"] - 900.0) < 1e-3
    assert v["sky_texture"].lower() == other and v["detail_tiling"] == 5
    assert (v["ocean_min_alpha"], v["ocean_max_alpha"], v["shore_alpha"]) == (20, v0["ocean_max_alpha"], 99)
    assert all(abs(a - b) <= 1.0 / 255 + 1e-6 for a, b in zip(v["ocean_colour"], (0.9, 0.1, 0.1)))      # the stock map had no ocean record: one was added
    assert "ocean_colour" not in v0
    text = bpy.data.texts["Map save 2p_faceoff"].as_string()
    for words in ("shadow colour", "sunlight colour", "time of day", "fog", "ocean opacity", "ocean colour", f"sky of the map is now {other}",
                  "detail texture repeat of the map is now 5"):
        assert words in text, (words, text)
    # putting everything back writes nothing
    g.sun_colour, g.shadow_colour = v0["sun_colour"], v0["shadow_colour"]
    g.time_hour = L.hour_of_angle(v0["time_angle"])
    g.fog_on = False
    g.ocean_min, g.shore, g.detail_tiling = v0["ocean_min_alpha"], v0["shore_alpha"], v0["detail_tiling"]
    g.sky = v0["sky_texture"]
    g.ocean_touched = False
    assert save(top, "2p_faceoff")[1] == raw, "the values put back to the map's own must write nothing"
    # reset
    g.sun_colour = (0.0, 0.0, 0.0)
    assert "FINISHED" in bpy.ops.bz.map_look_reset()
    assert tuple(round(x, 5) for x in g.sun_colour) == tuple(round(x, 5) for x in v0["sun_colour"])
    print("   saved: each value read back from the file, the stock map got an ocean colour record, back to the map's own = byte for byte", flush=True)

    # --- a map with fog: switching it off writes the engine's 'no fog'
    top = open_map(ICE_CLASH)
    raw2 = game_map(game_dir, ICE_CLASH).read()
    assert top.ic_look.fog_on
    top.ic_look.fog_on = False
    out, got = save(top, "ice clash")
    w = L.values_of(got)
    assert w["fog_on"] is False and (w["fog_start"], w["fog_end"]) == L.FOG_NONE
    assert len(got) == len(raw2)
    print("   fog off on ice clash: start 1, end 0 written, the file keeps its size", flush=True)


def test_preview_and_panels(game_dir):
    from blender_native.sceneobj import map_settings as MS
    from blender_native.sceneobj import panels_map_settings as P
    top = open_map(FACEOFF)
    assert "FINISHED" in bpy.ops.bz.map_make_preview()
    ic = MS.stored_picture(top, MS.KEY_ICON_TEXT)
    mm = MS.stored_picture(top, MS.KEY_MM_TEXT)
    assert len(ic) == 65554 and len(mm) == 262162
    out, got = save(top, "2p_faceoff")
    assert open(os.path.join(out, "2p_faceoff_icon.tga"), "rb").read() == ic
    assert open(os.path.join(out, "2p_faceoff_mm.tga"), "rb").read() == mm
    from ICParse.sgb import scenario as S
    w = S.read_tga(mm)
    assert ((w[..., 0] == 0) & (w[..., 1] == 0x6E) & (w[..., 2] == 0x96)).sum() > 1000
    for fn in (P.draw_settings, P.draw_look, P.draw_problems):
        lay = C.FakeLayout()
        fn(lay, bpy.context, top)
        assert lay.calls
    lay = C.FakeLayout()
    P.draw_settings(lay, bpy.context, top)
    ops = [d["idname"] for k, d in lay.calls if k == "operator"]
    assert ops.count("bz.map_add_start") == 2 and "bz.map_make_preview" in ops and ops.count("bz.map_select_start") == 2, ops
    print("   preview pictures from a stock map; the three panels draw", flush=True)


def test_problems(game_dir):
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import map_settings as MS
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import ops_map_new as N
    from blender_native.sceneobj import panels_map_settings as P
    C.fresh()
    top = N.new_map(bpy.context, title="Problem Map", template_key="sav_s", players=3, place_starts=False, load_models=False, game_look=False,
                    game_dir=game_dir)
    state = H.snapshot()
    other = "objects_outside_the_map" in H.registered()["checks"]
    if other:                                           # the conveniences feature lists objects outside the map too: this test needs this module's own check
        lab0 = None
        H.unregister_step("objects_outside_the_map")

    def problems():
        return H.run_checks(bpy.context, top)

    def texts(kind=None):
        return [p["text"] for p in problems() if kind is None or p["severity"] == kind]

    errs = texts("error")
    assert sum("has no start" in t for t in errs) == 3, errs
    # give players 1 and 2 a start with the operator; player 3 stays without
    for slot, (x, y) in enumerate(MS.ring_positions(top, 3)[:2]):
        bpy.context.scene.cursor.location = (x, y, 0.0)
        assert "FINISHED" in bpy.ops.bz.map_add_start(slot=slot)
    errs = texts("error")
    assert sum("has no start" in t for t in errs) == 1 and "Player 3 has no start" in " ".join(errs), errs
    assert bpy.ops.bz.map_check_problems() == {"FINISHED"}
    assert len(MS.PROBLEMS[top.name]) >= 1
    lay = C.FakeLayout()
    P.draw_problems(lay, bpy.context, top)
    # a start for a player the map does not have
    top.ic_settings.players = 2
    warn = texts("warning")
    assert not any("owns a laboratory" in t for t in warn)
    top.ic_settings.players = 3
    # an object outside the map
    labs = MS.starts_of(top)[0]
    lab = labs[0]
    lab.location.x = 5000.0
    outside = [p for p in problems() if "outside the map" in p["text"]]
    assert len(outside) == 1 and outside[0]["where"] == lab.name and outside[0]["severity"] == "warning", outside
    assert "FINISHED" in bpy.ops.bz.map_locate_problem(where=lab.name)
    assert bpy.context.view_layer.objects.active == lab and lab.select_get()
    lab.location.x = 0.0
    # a blueprint the game does not have
    lab["ic_blueprint"] = "DATA:ART\\EBPS\\STRUCTURES\\NO_SUCH_LAB.EBP"
    bad = [p for p in problems() if "not in the game's files" in p["text"]]
    assert len(bad) == 1 and bad[0]["severity"] == "error" and bad[0]["where"] == lab.name, bad
    lab["ic_blueprint"] = "DATA:ART\\EBPS\\STRUCTURES\\LAB.EBP"
    assert not [p for p in problems() if "not in the game's files" in p["text"]]
    # ground out of range
    terrain = X.terrain_of(top)
    terrain.data.vertices[10].co.z = 400.0
    terrain.data.vertices[11].co.z = -20.0
    hs = [p for p in problems() if "ground points" in p["text"]]
    assert len(hs) == 2 and all(p["severity"] == "error" for p in hs), hs
    terrain.data.vertices[10].co.z = 20.0
    terrain.data.vertices[11].co.z = 20.0
    # a multiplayer map for one player; no name; shadow lighter than the sun
    top.ic_settings.players = 1
    assert any("at least 2 players" in p["text"] and p["severity"] == "error" for p in problems())
    top.ic_settings.players = 3
    top.ic_settings.title = ""
    assert any("no name" in p["text"] for p in problems())
    top.ic_settings.title = "Problem Map"
    top.ic_look.shadow_colour = (0.9, 0.9, 0.9)
    assert sum("shadow colour is not below" in p["text"] for p in problems()) == 3
    top.ic_look.shadow_colour = (0.25, 0.25, 0.25)
    x, y = MS.ring_positions(top, 3)[2]
    bpy.context.scene.cursor.location = (x, y, 0.0)
    assert "FINISHED" in bpy.ops.bz.map_add_start(slot=2)
    assert not [p for p in problems() if p["severity"] == "error"], problems()
    if other:                                           # with the other feature's check there, this module's says nothing more
        H.restore(state)
        assert not [p for p in H.run_checks(bpy.context, top) if "outside the map" in p["text"]]
        H.unregister_step("objects_outside_the_map")
    print("   problems: no start, outside the map, unknown blueprint, ground out of range, one player, no name, light order; Locate selects", flush=True)
    # many objects outside the map are summed up
    for o in X.entity_objects(top):
        o.location.x = 9000.0
    MS.MAX_LISTED = 5
    outside = [p for p in problems() if "outside the map" in p["text"]]
    assert len(outside) == MS.MAX_LISTED + 1 and "7 more objects" in outside[-1]["text"]
    MS.MAX_LISTED = 12
    H.restore(state) and "more objects are outside" in outside[-1]["text"], [p["text"] for p in outside]


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map settings test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("untouched maps are saved byte for byte")
        test_untouched(game_dir)
        section("the settings")
        test_settings(game_dir)
        section("the Terrain look")
        test_look(game_dir)
        section("preview pictures and panels")
        test_preview_and_panels(game_dir)
        section("map problems")
        test_problems(game_dir)
    print("SO_MAP_SETTINGS_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_settings", main)
