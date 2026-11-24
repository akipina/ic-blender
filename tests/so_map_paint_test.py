"""Painting a map (passability, terrain types, water at its own level, decals), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_paint_test.py     (or: run_all.py -k so_map_paint)

Stock maps from the game's files, imported with the map import:

1. the modules are discovered, the save steps and the check are registered; with every paint layer made (passability picture, terrain
   type colours, water attribute, decal handles, an overlay shown) and nothing painted, a save is byte for byte the map opened;
2. passability: the picture has one pixel per path cell in the map's own layer, the terrain's second UV map spans 0..1, the paint object
   shares the terrain's mesh and sits above it; cells set by the "selected faces" operator and pixels painted straight into the picture
   reach TFAC's passability layer, the saved pathfinding is what the game's builder makes of it, only TFAC and the three pathfinding
   records change, painting back restores the original bytes; sculpting and painting together both reach the file;
3. overlays: every kind draws, with the picture the format module makes; hiding removes the object and the picture; none is saved;
4. terrain types: the colours are the map's own types, setting points reaches the type plane and only it;
5. water: points given their own height reach the water plane, the wet count follows, the pathfinding is updated; redraw keeps the level;
6. decals: handles of every decal; moved, deleted and added handles reach the decal block and only it; unmoved handles change nothing;
7. the check: a unit's start cell painted blocked is an error; the panels draw; nothing under the game folder changed.
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
CORE = "Data|scenarios/mp/core of the problem.sgb"
STOCK = (FACEOFF, "Engine.sga|scenarios/sp/mission1.sgb", "InsectData.sga|scenarios/mp/6p_big_bog.sgb")


def section(title):
    print(f"-- {title}", flush=True)


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def import_map(key):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=False)
    assert "FINISHED" in res, (key, res)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def save(top, name="painted"):
    out = C.tmpdir("mappaint_")
    path = os.path.join(out, f"{name}.sgb")
    res = bpy.ops.bz.export_map(filepath=path)
    assert "FINISHED" in res, res
    with open(path, "rb") as f:
        return f.read(), out


def terrain_of(top):
    from blender_native.sceneobj import ops_map_export as X
    return X.terrain_of(top)


def chunk_diff(a: bytes, b: bytes):
    from ICParse.iff.document import IffDocument
    import collections

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
    la, lb = leaves(IffDocument.from_bytes(a)), leaves(IffDocument.from_bytes(b))
    return {f"{k[0]}/{k[1]}" for k in (la - lb)} | {f"{k[0]}/{k[1]}" for k in (lb - la)}


def select_faces(terrain, mask):
    me = terrain.data
    me.polygons.foreach_set("select", np.asarray(mask, bool))
    me.update()


def select_points(terrain, mask):
    me = terrain.data
    me.vertices.foreach_set("select", np.asarray(mask, bool))
    me.update()


# ---------------------------------------------------------------------------------------------------- 1. loading, identity
def test_loading_and_identity(game_dir):
    from blender_native import sceneobj
    from blender_native.sceneobj import map_hooks as H
    names = [m.__name__.rsplit(".", 1)[-1] for m in sceneobj.MODULES]
    for n in ("map_paint", "map_overlays", "ops_map_paint", "panels_map_paint"):
        assert n in names, (n, names)
    assert not sceneobj.ERRORS, sceneobj.ERRORS
    reg = H.registered()
    for n in ("paint passability", "paint terrain types", "paint water", "paint decals"):
        assert n in reg["save"], reg
    assert "paint start cells" in reg["checks"], reg
    from blender_native.sceneobj import map_paint as P
    for key in STOCK:
        raw = game_map(game_dir, key).read()
        top = import_map(key)
        out, _ = save(top, "plain")
        assert out == raw, f"{key}: with the paint steps registered a map saved with no edits differs from the original"
        print(f"   {key}: {len(raw)} bytes back byte for byte with the paint steps registered", flush=True)
    # every layer made, nothing painted
    raw = game_map(game_dir, FACEOFF).read()
    top = import_map(FACEOFF)
    P.ensure_passability(bpy.context, top)
    P.ensure_types(bpy.context, top)
    P.ensure_water_attr(terrain_of(top))
    bpy.ops.bz.map_decals_edit()
    assert "FINISHED" in bpy.ops.bz.map_overlay_show(kind="land")
    assert "FINISHED" in bpy.ops.bz.map_ground_picture(show=True)
    assert P.paint_object(top, "impass") is not None and P.paint_object(top, "types") is not None and P.paint_object(top, "ground") is not None
    out, out_dir = save(top, "layers")
    from ICParse.sgb import ground_picture as G
    gm = game_map(game_dir, FACEOFF)
    assert G.decode(open(os.path.join(out_dir, "layers.tga"), "rb").read()).pixels.shape == (505, 505, 4)
    assert out == raw, "every paint layer made and nothing painted: the save differs from the original"
    print(f"   {FACEOFF}: all layers made, saved byte for byte", flush=True)
    # the paint objects are not part of what the core save reads
    from blender_native.sceneobj import ops_map_export as X
    assert X.terrain_of(top) is not None and "ic_map" not in P.paint_object(top, "impass")
    assert all("ic_blueprint" not in o for o in P.paint_collection(top).objects)


# ---------------------------------------------------------------------------------------------------- 2. passability
def test_passability(game_dir):
    from ICParse.sgb import edit, impass_edit, view
    from blender_native.sceneobj import map_paint as P
    raw = game_map(game_dir, FACEOFF).read()
    mv = view.read(raw)
    top = import_map(FACEOFF)
    terrain = terrain_of(top)
    plane = impass_edit.read_plane(raw)
    n = plane.shape[0]
    obj = P.ensure_passability(bpy.context, top)
    img = P.paint_image(top)
    assert (img.size[0], img.size[1]) == (n, n) and img.packed_file is not None
    # the picture shows the map's own layer
    assert (P.classify_rgba(P.image_pixels(img)) == plane).all()
    # the second UV map spans the whole map; the ground one stays the active one
    me = terrain.data
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers[P.UV_NAME].data.foreach_get("uv", uv)
    assert abs(float(uv.min())) < 1e-6 and abs(float(uv.max()) - 1.0) < 1e-6
    assert me.uv_layers.active.name != P.UV_NAME and "Ground" in me.uv_layers
    # shares the mesh, follows the terrain, sits above it
    assert obj.data is me and obj.parent == terrain and len(obj.modifiers) == 1
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    co = np.empty(len(ev.data.vertices) * 3, np.float32)
    ev.data.vertices.foreach_get("co", co)
    base = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", base)
    lift = float((co.reshape(-1, 3)[:, 2] - base.reshape(-1, 3)[:, 2]).mean())
    assert 0.05 < lift < 0.2, lift
    assert len(me.materials) == len(terrain.data.materials) and all(m is None or not m.name.endswith("passability paint") for m in me.materials)

    # ---- Texture Paint starts on the picture, with the colour asked for
    assert "FINISHED" in bpy.ops.bz.map_impass_paint(state="BLOCK")
    ip = bpy.context.tool_settings.image_paint
    assert bpy.context.object.mode == "TEXTURE_PAINT" and bpy.context.object == obj and ip.canvas == img
    assert [round(v, 2) for v in ip.brush.color] == [0.9, 0.1, 0.1]
    assert "FINISHED" in bpy.ops.bz.map_impass_paint(state="ERASE") and ip.brush.blend == "ERASE_ALPHA"
    assert "FINISHED" in bpy.ops.bz.map_paint_stop() and bpy.context.object.mode == "OBJECT"
    assert "FINISHED" in bpy.ops.bz.map_types_paint(type="3") and bpy.context.object.mode == "VERTEX_PAINT"
    assert "FINISHED" in bpy.ops.bz.map_paint_stop() and bpy.context.object.mode == "OBJECT"
    # ---- faces: block a 3 x 3 patch of faces, open another
    w, h = mv.width, mv.height
    mask = np.zeros((h - 1, w - 1), bool)
    mask[30:33, 30:33] = True
    select_faces(terrain, mask.reshape(-1))
    assert "FINISHED" in bpy.ops.bz.map_impass_faces(state="BLOCK")
    got = P.classify_rgba(P.image_pixels(img))
    want = plane.copy()
    want[60:66, 60:66] = 1
    assert (got == want).all(), int((got != want).sum())
    mask2 = np.zeros((h - 1, w - 1), bool)
    mask2[10:12, 20:22] = True
    select_faces(terrain, mask2.reshape(-1))
    assert "FINISHED" in bpy.ops.bz.map_impass_faces(state="OPEN")
    want[20:24, 40:44] = 2
    # ---- a pixel painted straight into the picture (what Texture Paint does)
    pic = P.image_pixels(img).copy()
    pic[100, 100] = (0.9, 0.1, 0.1, 1.0)
    P.put_image_pixels(img, pic)
    want[100, 100] = 1
    saved, out = save(top, "imp")
    got_plane = impass_edit.read_plane(saved)
    changed = got_plane != plane
    wanted = want != plane
    assert (changed == wanted).all(), (int(changed.sum()), int(wanted.sum()))
    assert (got_plane == want).all()
    fid = edit.pathfinding_fidelity(saved)
    assert fid["rebuild_identical"], fid["summary"]
    assert chunk_diff(raw, saved) <= {"TERR/TFAC", "PFDR/NRML", "PFDR/NRML"} or all(k.endswith(("TFAC", "NRML")) for k in chunk_diff(raw, saved)), chunk_diff(raw, saved)
    text = bpy.data.texts.get("Map save imp").as_string()
    assert "Passability: " in text and "path cells changed" in text, text
    print(f"   {int(wanted.sum())} cells painted: layer saved, pathfinding = the game builder's ({chunk_diff(raw, saved)})", flush=True)
    # painting back restores the bytes
    P.put_image_pixels(img, P.plane_to_rgba(np.where(np.isin(plane, (0, 1, 2)), plane, 0)))
    back, _ = save(top, "back")
    assert back == raw, "painting back to the original did not restore the original bytes"
    # sculpting + painting
    me.vertices.foreach_get("co", base)
    co3 = base.reshape(-1, 3).copy()
    co3[10 * w + 10:10 * w + 14, 2] += 3.0
    me.vertices.foreach_set("co", co3.ravel())
    me.update()
    pic = P.image_pixels(img).copy()
    pic[50, 50] = (0.9, 0.1, 0.1, 1.0)
    P.put_image_pixels(img, pic)
    both, _ = save(top, "both")
    pl2 = impass_edit.read_plane(both)
    assert pl2[50, 50] == 1 and (pl2 != plane).sum() == 1
    v2 = view.read(both)
    assert (v2.heights != mv.heights).sum() == 4
    cells = edit._grid(bytes(edit._records(__import__("ICParse.sgb.parser", fromlist=["x"]).read(both).preserved)[b"IMPA"].raw))[0]
    assert cells[50, 50] == 7
    print("   sculpt + paint saved together", flush=True)
    # a picture that no longer fits the terrain is not saved, and says so
    big = bpy.data.images.new("odd", 8, 8, alpha=True)
    P.paint_object(top, "impass")[P.KEY_IMAGE] = big.name
    odd, out = save(top, "odd")
    assert "passability picture" in bpy.data.texts.get("Map save odd").as_string()


# ---------------------------------------------------------------------------------------------------- 3. overlays
def test_overlays(game_dir):
    from ICParse.sgb import overlays
    from blender_native.sceneobj import map_overlays as OV
    from blender_native.sceneobj import map_paint as P
    raw = game_map(game_dir, FACEOFF).read()
    top = import_map(FACEOFF)
    want = overlays.layers(raw)
    for key in overlays.KEYS:
        assert "FINISHED" in bpy.ops.bz.map_overlay_show(kind=key), key
        img = P.paint_image(top, "overlay")
        n = want[key].rgba.shape[0]
        assert (img.size[0], img.size[1]) == (n, n), (key, tuple(img.size))
        got = P.image_pixels(img)
        marked = int((got[..., 3] > 0).sum())
        assert marked == int((want[key].rgba[..., 3] > 0).sum()), key
        assert OV.shown(top) == key
    obj = P.paint_object(top, "overlay")
    assert obj is not None and len([o for o in P.paint_collection(top).objects if o.get(P.KEY_PAINT) == "overlay"]) == 1
    # a change shows up in the next overlay
    terrain = terrain_of(top)
    pic_img = None
    bpy.ops.bz.map_overlay_hide()
    assert P.paint_object(top, "overlay") is None and P.paint_image(top, "overlay") is None and OV.shown(top) == ""
    from ICParse.sgb import impass_edit
    plane = impass_edit.read_plane(raw)
    P.ensure_passability(bpy.context, top)
    pic = P.image_pixels(P.paint_image(top)).copy()
    pic[40:44, 40:48] = (0.9, 0.1, 0.1, 1.0)
    P.put_image_pixels(P.paint_image(top), pic)
    assert "FINISHED" in bpy.ops.bz.map_overlay_show(kind="land")
    got = P.image_pixels(P.paint_image(top, "overlay"))
    assert (got[40:44, 40:48, 3] > 0).all(), "the painted cells are not red in the land overlay"
    saved, _ = save(top, "ov")
    assert (impass_edit.read_plane(saved) != plane).sum() == int((plane[40:44, 40:48] != 1).sum())
    assert not any(k for k in chunk_diff(raw, saved) if k.endswith(("overlay",)))
    # a big map: how long
    import time
    big = game_map(game_dir, "InsectData.sga|scenarios/mp/6p_big_bog.sgb")
    top = import_map(big.key)
    t = time.perf_counter()
    assert "FINISHED" in bpy.ops.bz.map_overlay_show(kind="precise")
    print(f"   all {len(overlays.KEYS)} overlays on faceoff match the format module; precise map of 6p_big_bog in "
          f"{time.perf_counter() - t:.1f} s", flush=True)


# ---------------------------------------------------------------------------------------------------- 4. terrain types
def test_terrain_types(game_dir):
    from ICParse.sgb import terrain_types as T
    from blender_native.sceneobj import map_paint as P
    raw = game_map(game_dir, FACEOFF).read()
    types = T.read_types(raw)
    top = import_map(FACEOFF)
    terrain = terrain_of(top)
    P.ensure_types(bpy.context, top)
    assert (P.terrain_types_of(terrain).reshape(types.shape) == types).all()
    w = types.shape[0]
    mask = np.zeros((w, w), bool)
    mask[10:14, 10:14] = True
    select_points(terrain, mask.reshape(-1))
    assert "FINISHED" in bpy.ops.bz.map_types_set(type="2")
    saved, _ = save(top, "types")
    got = T.read_types(saved)
    want = types.copy()
    want[10:14, 10:14] = 2
    assert (got == want).all()
    assert chunk_diff(raw, saved) == {"TERR/TFAC"}, chunk_diff(raw, saved)
    assert "Terrain types: " in bpy.data.texts.get("Map save types").as_string()
    # a colour painted by hand (Vertex Paint writes colours): the nearest type is taken
    attr = terrain.data.color_attributes[P.TYPE_ATTR]
    col = np.empty(w * w * 4, np.float32)
    attr.data.foreach_get("color", col)
    col = col.reshape(-1, 4)
    col[30 * w + 30] = (*P.type_palette()[4], 1.0)
    col[31 * w + 30] = (0.02, 0.25, 0.04, 1.0)       # off-palette: nearest is grass
    attr.data.foreach_set("color", col.ravel())
    saved2, _ = save(top, "types2")
    got2 = T.read_types(saved2)
    assert got2[30, 30] == 4 and got2[31, 30] == 1, (got2[30, 30], got2[31, 30], want[30, 30], want[31, 30])
    assert int((got2 != want).sum()) == int(want[30, 30] != 4) + int(want[31, 30] != 1), int((got2 != want).sum())
    print("   terrain types: selected points and painted colours saved, only TFAC changed", flush=True)


# ---------------------------------------------------------------------------------------------------- 5. water
def test_water(game_dir):
    from ICParse.sgb import edit, view, water_paint
    from blender_native.sceneobj import map_paint as P
    raw = game_map(game_dir, FACEOFF).read()
    mv = view.read(raw)
    plane = water_paint.read_water(raw)
    top = import_map(FACEOFF)
    terrain = terrain_of(top)
    w = mv.width
    j, i = np.unravel_index(np.argmax(mv.heights), mv.heights.shape)
    mask = np.zeros((w, w), bool)
    mask[max(0, j - 3):j + 4, max(0, i - 3):i + 4] = True
    select_points(terrain, mask.reshape(-1))
    top_m = float(mv.heights[j, i]) * mv.height_scale + 2.0
    assert "FINISHED" in bpy.ops.bz.map_water_set(height=top_m)
    from blender_native.sceneobj import ops_map_export as X
    water = X.water_of(top)
    assert water is not None
    assert abs(float(water["ic_look_level_m"]) - mv.water_height) < 1e-6, "redrawing the water moved the map's own level"
    saved, _ = save(top, "water")
    got = water_paint.read_water(saved)
    step = int(round(top_m / mv.height_scale))
    assert (got[mask] == step).all() and (got[~mask] == plane[~mask]).all()
    v2 = view.read(saved)
    assert v2.water_level == mv.water_level
    assert int(v2.wet_vertices().sum()) > int(mv.wet_vertices().sum())
    assert edit.pathfinding_fidelity(saved)["rebuild_identical"]
    assert chunk_diff(raw, saved) <= {"TERR/TFAC", "PFDR/NRML"} or all(k.endswith(("TFAC", "NRML")) for k in chunk_diff(raw, saved))
    assert "Water: " in bpy.data.texts.get("Map save water").as_string()
    # dry them again and undo: back to the original bytes
    assert "FINISHED" in bpy.ops.bz.map_water_revert()
    back, _ = save(top, "waterback")
    assert back == raw, "undoing the water paint did not restore the original bytes"
    assert "FINISHED" in bpy.ops.bz.map_water_dry()
    dry, _ = save(top, "dry")
    assert (water_paint.read_water(dry)[mask] == 0).all()
    print("   water at its own level: saved, level kept, pathfinding = the game builder's, undo restores the bytes", flush=True)


# ---------------------------------------------------------------------------------------------------- 6. decals
def test_decals(game_dir):
    from ICParse.sgb import decals_edit
    from blender_native.sceneobj import map_paint as P
    from blender_native.sceneobj import ops_map_paint as O
    raw = game_map(game_dir, CORE).read()
    rows = decals_edit.read_decals(raw)
    assert len(rows) > 50
    top = import_map(CORE)
    assert "FINISHED" in bpy.ops.bz.map_decals_edit()
    hs = P.decal_handles(top)
    assert len(hs) == len(rows)
    back = P.decal_rows(top)
    bad = [(a, b) for a, b in zip(back, rows) if not (abs(a.x - b.x) < 1e-3 and abs(a.z - b.z) < 1e-3
                                                         and abs(decals_edit._turn(a.rot, b.rot)) < 1e-4 and abs(a.scale - b.scale) < 1e-4)]
    assert not bad, ("a handle does not give back its decal's own numbers", len(bad), bad[:2])
    saved, _ = save(top, "dec0")
    assert saved == raw, "handles made and not touched: the save differs from the original"
    # move one 5 m along game x, turn another, scale a third, delete a fourth, add one
    by_eid = {int(o[P.KEY_DECAL]): o for o in hs}
    a, b, c, d = (by_eid[r.eid] for r in rows[:4])
    a.location.x += 5.0                                # Blender x is the game's x
    b.rotation_euler.z += 0.3                          # about the vertical axis (Blender y is the game's -z: the turn reads the other way)
    c.scale = (1.5, 1.5, 1.5)
    bpy.data.objects.remove(d, do_unlink=True)
    game_dir_p = O.prefs.resolve_game_dir(bpy.context)[0]
    pics = O.decal_pictures(game_dir_p, "")
    assert len(pics) > 20, len(pics)
    bpy.context.scene.cursor.location = (10.0, -20.0, 3.0)
    assert "FINISHED" in bpy.ops.bz.map_decal_add(picture=pics[0], scale=2.0)
    saved, _ = save(top, "dec1")
    got = decals_edit.read_decals(saved)
    assert len(got) == len(rows)                       # one deleted, one added
    g = {r.eid: r for r in got}
    assert abs(g[rows[0].eid].x - (rows[0].x + 5.0)) < 1e-3 and abs(g[rows[0].eid].z - rows[0].z) < 1e-3
    assert abs(decals_edit._turn(g[rows[1].eid].rot, rows[1].rot - 0.3)) < 1e-3 and abs(g[rows[2].eid].scale - rows[2].scale * 1.5) < 1e-3
    assert rows[3].eid not in g
    new = got[-1]
    assert new.texture.lower() == pics[0] and abs(new.x - 10.0) < 1e-3 and abs(new.z - 20.0) < 1e-3 and abs(new.scale - 2.0) < 1e-3, new
    assert chunk_diff(raw, saved) <= {"DECL/ENTY", "DECL/SMAP"} or all(k.endswith(("ENTY", "SMAP")) for k in chunk_diff(raw, saved)), chunk_diff(raw, saved)
    assert "Decals: 1 added, 1 removed, 3 moved" in bpy.data.texts.get("Map save dec1").as_string()
    print("   decals: moved, turned, scaled, deleted, added; only the decal block changed", flush=True)


# ---------------------------------------------------------------------------------------------------- 7. checks and panels
def test_check_and_panels(game_dir):
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import map_paint as P
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import panels_map_paint as PP
    top = import_map(FACEOFF)
    assert H.run_checks(bpy.context, top) == [] or all(p["check"] != "paint start cells" for p in H.run_checks(bpy.context, top))
    P.ensure_passability(bpy.context, top)
    assert [p for p in H.run_checks(bpy.context, top) if p["check"] == "paint start cells"] == []
    owned = [o for o in X.entity_objects(top) if int(o.get("ic_owner", -1)) >= 0]
    assert owned
    m = X._game_matrix(owned[0])
    terrain = terrain_of(top)
    w, h, ts, _hs = P.terrain_grid(terrain)
    half = (w - 1) * ts / 2.0
    cx, cz = int((m[0, 3] + half) // (ts / 2)), int((m[2, 3] + half) // (ts / 2))
    img = P.paint_image(top)
    pic = P.image_pixels(img).copy()
    pic[cz, cx] = (0.9, 0.1, 0.1, 1.0)
    P.put_image_pixels(img, pic)
    problems = [p for p in H.run_checks(bpy.context, top) if p["check"] == "paint start cells"]
    assert len(problems) == 1 and problems[0]["severity"] == "error" and "painted as blocked" in problems[0]["text"], problems
    # the panels draw (into a stand-in layout), every operator named exists
    import _so_common
    problems_seen = []
    from so_map_edit_test import CheckedLayout   # noqa: F401  (the same stand-in the edit test uses)
    for fn in (PP.draw_passability, PP.draw_overlays, PP.draw_types, PP.draw_water, PP.draw_decals, PP.draw_tiles, PP.draw_stamp):
        lay = CheckedLayout(problems=problems_seen)
        fn(lay, bpy.context, top)
    assert not problems_seen, problems_seen
    print("   check: a start cell painted blocked is an error; the seven panels draw", flush=True)


def test_stamping(game_dir):
    from ICParse.sgb import ground_picture as G
    from ICParse.sgb import view
    from blender_native.sceneobj import map_paint as P
    from blender_native.sceneobj import ops_map_paint as O
    import ICParse.sga as sga
    gm = game_map(game_dir, FACEOFF)
    raw = gm.read()
    arc = sga.read(gm.path)
    orig_tga = arc.read(next(e for e in arc.entries if e.path.replace("\\", "/").lower() == "scenarios/mp/2p_faceoff.tga"))
    mv = view.read(raw)
    top = import_map(FACEOFF)
    terrain = terrain_of(top)
    assert "FINISHED" in bpy.ops.bz.map_ground_picture(show=True)
    img = P.paint_image(top, "ground")
    assert (img.size[0], img.size[1]) == (505, 505) and img.packed_file is not None
    assert (P.ground_pixels(img) == G.decode(orig_tga).pixels).all(), "the picture shown is not the map's own"
    pics = O.decal_pictures(O.prefs.resolve_game_dir(bpy.context)[0], "")
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    assert "FINISHED" in bpy.ops.bz.map_stamp(picture=pics[0], scale=2.0, turn=30.0, strength=0.8, tint=(1.0, 0.6, 0.6), count=3, spacing=12.0)
    now = P.ground_pixels(img)
    before = G.decode(orig_tga).pixels
    diff = np.argwhere((now != before).any(axis=2))
    assert len(diff) > 100
    saved, out = save(top, "stamp")
    assert saved == raw, "a stamp changed the map file: stamps live in the picture beside it"
    with open(os.path.join(out, "stamp.tga"), "rb") as f:
        got = f.read()
    pic = G.decode(got)
    assert len(got) == len(orig_tga) and got[:18] == orig_tga[:18] and (pic.pixels == now).all()
    assert (pic.pixels[..., 3] == before[..., 3]).all()
    text = bpy.data.texts.get("Map save stamp").as_string()
    assert "Ground picture: " in text and "_decaled" in text, text
    # the other pictures beside the map are as they were
    for rest in (".dlg", ".na", ".trg", "_icon.tga", "_mm.tga"):
        entry = next(e for e in arc.entries if e.path.replace("\\", "/").lower() == f"scenarios/mp/2p_faceoff{rest}")
        with open(os.path.join(out, f"stamp{rest}"), "rb") as f:
            assert f.read() == arc.read(entry), rest
    # the stamps live in the .blend too: saved and reopened, the picture is still the stamped one (the picture was packed when it was made,
    # so the pack must be renewed before the file is written -- and the handler that does it must survive loading a file)
    blend = os.path.join(C.tmpdir("mappaint_blend_"), "stamped.blend")
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=blend)
    assert "FINISHED" in bpy.ops.wm.open_mainfile(filepath=blend)
    img = P.paint_image([c for c in bpy.data.collections if c.get("ic_map")][0], "ground")
    assert (P.ground_pixels(img) == now).all(), "the stamps were lost when the .blend was saved and reopened"
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)             # a second session: stamp again, save and reopen again
    assert "FINISHED" in bpy.ops.bz.map_stamp(picture=pics[0], scale=1.0, strength=1.0, tint=(0.2, 0.2, 1.0), count=1)
    again = P.ground_pixels(img)
    assert (again != now).any()
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=blend)
    assert "FINISHED" in bpy.ops.wm.open_mainfile(filepath=blend)
    img = P.paint_image([c for c in bpy.data.collections if c.get("ic_map")][0], "ground")
    assert (P.ground_pixels(img) == again).all(), "the stamps of the second session were lost: the pack handler did not survive loading the file"
    # a stamp off the map warns and changes nothing
    bpy.context.scene.cursor.location = (5000.0, 5000.0, 0.0)
    P.put_ground_pixels(img, G.decode(orig_tga).pixels)
    bpy.ops.bz.map_stamp(picture=pics[0])
    assert (P.ground_pixels(img) == G.decode(orig_tga).pixels).all()
    print(f"   stamping: {len(diff)} pixels painted into the 505 x 505 ground picture, saved as <name>.tga, the map file untouched", flush=True)


def test_blend_tiles(game_dir):
    from blender_native.sceneobj import ops_map_paint as O
    from blender_native.sceneobj import ops_map_export as X
    top = import_map(FACEOFF)
    names = X.game_listing(game_dir, top.get("ic_map_archive", ""))["ground"]
    tiles = O.blend_tiles_for(top, names)
    assert tiles, "no blend tile found for the textures of 2p_faceoff"
    used = {"beach_01", "savanna_01", "savanna_02"}
    from ICParse.sgb import transitions
    assert all({transitions.parse_tile(n[:-4])["a"], transitions.parse_tile(n[:-4])["b"]} & used for n, _w in tiles)
    terrain = terrain_of(top)
    before = len(terrain.data.materials)
    assert "FINISHED" in bpy.ops.bz.map_add_blend_tile(tile=tiles[0][0])
    mats = terrain.data.materials
    assert len(mats) == before + 1 and str(mats[-1]["ic_ground_texture"]).lower() == tiles[0][0].lower()
    print(f"   blend tiles: {len(tiles)} tiles join the textures of 2p_faceoff; one added as a ground material", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map paint test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("loading, and every layer made with nothing painted saves byte for byte")
        test_loading_and_identity(game_dir)
        section("passability")
        test_passability(game_dir)
        section("overlays")
        test_overlays(game_dir)
        section("terrain types")
        test_terrain_types(game_dir)
        section("water at its own level")
        test_water(game_dir)
        section("decals")
        test_decals(game_dir)
        section("texture stamping")
        test_stamping(game_dir)
        section("blend tiles")
        test_blend_tiles(game_dir)
        section("the check and the panels")
        test_check_and_panels(game_dir)
    print("SO_MAP_PAINT_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_paint", main)
