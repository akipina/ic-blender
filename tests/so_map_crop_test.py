"""Terrain Crop for the map in the scene (sceneobj/map_crop.py, ops_map_tools.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_crop_test.py     (or: run_all.py -k so_map_crop)

1. Validate lists what would break (the objects the crop deletes, the triggers / groups / players that depend on them), shows the square
   that stays (a wire rectangle, not a placed object) and selects the objects outside it; the sizes the editor refuses are refused in words;
2. Crop is refused while something would break; with "Crop anyway" it builds the cropped map as a new map (the old one excluded from the
   view, its origin written on the new collection);
3. the cropped map saves, from memory, byte for byte what ICParse's crop makes of the same map and files, with the files beside it (the
   trigger, dialogue and loading files kept, the NISlet file moved, pictures of the old size left out), its ground is 65 x 65 points, and its
   pathfinding is what the game's builder (pathbake) makes of it;
4. edits not yet saved (painted passability) are in the bytes the tools work on, and those bytes are the ones a save writes;
nothing under the game folder changed.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402
import so_map_tools_test as TT  # noqa: E402  (its helpers: import a stock map, find the live map, save, list files)

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)


def test_crop(game_dir):
    import numpy as np
    from ICParse.sgb import crop as CROP, parser as P, pathbake, view
    from blender_native.sceneobj import map_crop as CR, map_tools as T, ops_map_export as X
    gms = TT.keys(game_dir)
    gm = gms["mission1"]
    raw = gm.read()
    top = TT.import_map(gm)
    st = bpy.context.scene.bz_map_tools
    st.crop_ratio = "2"
    st.crop_use_cursor = False
    st.crop_centre = (-30.0, 20.0)
    st.crop_force = False
    assert "FINISHED" in bpy.ops.bz.map_crop_validate()
    res = CR.RESULTS[top.name]
    assert res["errors"] > 0 and res["region"].new_cells == 64 and any("would delete" in p["text"] for p in res["problems"])
    assert any(o.get("ic_crop_area") for o in top.objects), "the square is drawn"
    outside = {o.name for o in bpy.context.selected_objects}
    assert outside == set(res["outside"]) and len(outside) > 100
    # refused while something would break
    err = TT.expect_cancel(lambda: bpy.ops.bz.map_crop_apply(), "would break")
    assert len([c for c in bpy.data.collections if c.get("ic_map")]) == 1
    # crop anyway
    st.crop_force = True
    n_before = len(bpy.data.collections)
    assert "FINISHED" in bpy.ops.bz.map_crop_apply()
    new = TT.live_top()
    assert new != top and "Cropped to 1/2" in new.get("ic_map_origin", "") and X.map_name(new) == "mission1_crop2"
    layer = bpy.context.view_layer.layer_collection.children
    assert layer[top.name].exclude and not layer[new.name].exclude and not any(o.get("ic_crop_area") for o in top.objects)
    tt = X.terrain_of(new)
    assert (tt["ic_map_width"], tt["ic_map_height"]) == (65, 65)
    out = C.tmpdir("tools_crop_")
    data = TT.saved(out, "mission1_crop2")
    # what ICParse's crop makes of the same map and the same files, byte for byte
    sibs = {}
    import ICParse.sga as sga
    arc = sga.read(gm.path)
    for e in arc.entries:
        p = e.path.replace("\\", "/").lower()
        if p.startswith("scenarios/sp/mission1") and not p.endswith(".sgb"):
            rest = p[len("scenarios/sp/mission1"):]
            if rest.startswith(".") or rest.split(".")[0] in ("_decaled", "_decaled_small", "_icon", "_mm"):
                sibs[("", rest)] = arc.read(e)
        elif p.startswith("scenarios/sp/loading/mission1."):
            sibs[("loading", p[len("scenarios/sp/loading/mission1"):])] = arc.read(e)
    want, files, rep = CROP.crop_map(raw, 2, (-30.0, 20.0), siblings=sibs, force=True)
    assert data == want, "the cropped map saved from Blender must be what ICParse's crop makes"
    beside = set(TT.files_in(out))
    expect = {"mission1_crop2.sgb"} | {(f"{s}/" if s else "") + "mission1_crop2" + r for (s, r), d in files.items() if d is not None}
    assert beside == expect, (sorted(beside ^ expect))
    for (s, r), d in files.items():
        if d is not None:
            with open(os.path.join(out, (s + "/" if s else "") + "mission1_crop2" + r), "rb") as f:
                assert f.read() == d, (s, r)
    assert "mission1_crop2.na" in beside and "mission1_crop2.trg" in beside and not any(b.endswith(("_mm.tga", ".tga", "_decaled.jpg")) for b in beside)
    bd = P.read(data)
    baked = pathbake.bake_map(data)
    assert P._nrml(bd.preserved, b"IMPA").raw == baked.impa and P._nrml(bd.preserved, b"PRMP").raw == baked.prmp and P._nrml(bd.preserved, b"PASM").raw == baked.pasm
    mv = view.read(data)
    assert mv.width == 65 and mv.problems == [] and len(mv.entities) == rep["entities_kept"]
    # a clean crop needs no "Crop anyway": 2p_faceoff cut to the part that holds everything the triggers use? try every corner
    gm2 = gms["6p_big_bog"]
    TT.import_map(gm2)
    st = bpy.context.scene.bz_map_tools
    st.crop_use_cursor = True
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    st.crop_force = False
    st.crop_ratio = "8"
    assert "FINISHED" in bpy.ops.bz.map_crop_validate()
    res2 = CR.RESULTS[TT.live_top().name]
    assert res2["region"].new_cells == 16 and res2["errors"] > 0          # the 128 cell map cut to 16: the players' bases are outside
    TT.expect_cancel(lambda: bpy.ops.bz.map_crop_apply(), "would break")
    st.crop_ratio = "2"                                                  # a half that holds the middle and every base of a 6 player map is not on offer: refused too
    # sizes the editor refuses
    TT.import_map(TT.keys(game_dir)["2p_faceoff"])                            # 65 points: 1/8 would be 8 cells
    st = bpy.context.scene.bz_map_tools
    st.crop_ratio = "8"
    TT.expect_cancel(lambda: bpy.ops.bz.map_crop_validate(), "")
    print("   crop: validate lists conflicts and draws the square, refused until 'Crop anyway', saved bytes = ICParse's crop, files kept, game pathfinding", flush=True)


def test_unsaved_edits(game_dir):
    """What the tools read is what a save would write: a cell painted blocked and not yet saved is in the bytes Crop, Validate and the
    terrain export work on (and the files beside the map are the ones a save writes)."""
    import numpy as np
    from ICParse.sgb import impass_edit
    from blender_native.sceneobj import map_paint as P, map_tools as T
    gm = TT.keys(game_dir)["2p_faceoff"]
    top = TT.import_map(gm)
    plane = impass_edit.read_plane(gm.read())
    P.ensure_passability(bpy.context, top)
    img = P.paint_image(top)
    pic = P.image_pixels(img).copy()
    cell = (3, 3)
    assert plane[cell] != 1
    pic[cell] = (0.9, 0.1, 0.1, 1.0)                                  # blocked, as Texture Paint paints it
    P.put_image_pixels(img, pic)
    data, beside, _plan, _rep = T.current_map(bpy.context, top)
    assert impass_edit.read_plane(data)[cell] == 1, "the painted cell is not in the bytes the tools work on"
    assert T.current_bytes(bpy.context, top)[0] == data
    out = C.tmpdir("tools_unsaved_")
    assert TT.saved(out, "2p_faceoff") == data, "the tools must read the bytes a save writes"
    for (sub, rest), blob in beside.items():
        with open(os.path.join(out, *( [sub] if sub else [] ), "2p_faceoff" + rest), "rb") as f:
            assert f.read() == blob, (sub, rest)
    print("   unsaved edits: painted passability is in the bytes Crop, Validate and the terrain file read; they equal a save's", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map crop test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        test_crop(game_dir)
        test_unsaved_edits(game_dir)
    print("SO_MAP_CROP_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_crop", main)
