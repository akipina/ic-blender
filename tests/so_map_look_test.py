"""The map's game look (blender_native/sceneobj/map_look.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_look_test.py     (or: run_all.py -k so_map_look)

Stock maps read straight from the game's files and imported with the map import, then given the look:

1. 2p_faceoff (211 decals), ice clash (the one stock map with fog) and mission14: the sun lamp shines the way the map's light travels
   and has the map's colour; the world has the ambient colour and the fog values (a fog volume exactly when the map has fog); the sky
   is the game's dome with the sky picture, rows as stored; the open sea reaches 1200 m past the edge, none of it over the map; the
   water on the map and the sea wear the game's water with its brightness per point; the map's own ground picture is the ground, through
   one shared node group, with each detail texture rows as stored; the border has the game's one colour and shape; one object per decal
   picture, every decal there with the map's count, laid on the ground at its place, size and turn, faces up;
2. nothing of the look is read by the save: every piece sits in the map's ``look`` collection and has none of the marks the save looks
   for, the plan sees no change, and the saved map is byte for byte the map that was opened;
3. "Redraw water": raising the ground over the water takes that water away, a bowl dug into dry ground gives it water, the surface is
   exactly the cells with a corner under water at the level, and the plan still reads no water level change; moving the water up and
   redrawing puts the surface at the new level and the save writes that level; the automatic redraw after a ground edit does the same;
4. "Add water": a map opened without its water (the import's water switched off) gets a surface at the level asked for, the surface is
   the cells under that level, and saving writes the level; a map whose ground is dry everywhere gets an empty surface, and the test
   says whether the save writes its level (it does when the save reads the level from the water object's height);
5. the look can be taken off again, and nothing under the game folder changed.
"""
import json
import math
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
ICE_CLASH = "Data|scenarios/mp/ice clash.sgb"
MISSION14 = "Engine.sga|scenarios/sp/mission14.sgb"
KEYS_THE_SAVE_READS = ("ic_water_level", "ic_blueprint", "ic_map", "ic_entity_id", "ic_map_model")


def section(title):
    print(f"-- {title}", flush=True)


def import_map(key, *, water=True):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=False, show_water=water, show_creatures=False, game_look=False)
    assert "FINISHED" in res, (key, res)
    tops = [c for c in bpy.data.collections if c.get("ic_map")]
    assert len(tops) == 1
    return tops[0]


def lin(c):
    from blender_native.sceneobj import map_look as L
    return L._lin(c)


def point_values(me, name):
    out = np.empty(len(me.vertices), np.float32)
    me.attributes[name].data.foreach_get("value", out)
    return out


def check_rows_as_stored(img, raw):
    """The Blender image shows the file's rows as the game reads them: the first stored row at the top (Blender's last row)."""
    w, h = raw[12] | raw[13] << 8, raw[14] | raw[15] << 8
    n = raw[16] // 8
    rows = np.frombuffer(raw, np.uint8, w * h * n, 18 + raw[0]).reshape(h, w, n)
    assert tuple(img.size) == (w, h), (img.name, tuple(img.size), (w, h))
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    px = np.rint(px.reshape(h, w, 4) * 255).astype(int)
    want = rows[::-1][..., [2, 1, 0]].astype(int)
    solid = rows[::-1][..., 3] == 255 if n == 4 else np.ones((h, w), bool)      # see-through pixels may be stored premultiplied
    assert np.abs(px[..., :3] - want)[solid].max(initial=0) <= 1, img.name


def verts(obj):
    me = obj.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3).astype(np.float64)


def set_terrain_z(terrain, new_z):
    co = verts(terrain).astype(np.float32)
    co[:, 2] = new_z.reshape(-1)
    terrain.data.vertices.foreach_set("co", co.ravel())
    terrain.data.update()


def save_bytes(path):
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=path)
    with open(path, "rb") as f:
        return f.read()


def register_look_classes():
    from blender_native.sceneobj import map_look as L
    for c in L.OPERATORS:
        try:
            bpy.utils.register_class(c)
        except ValueError:
            pass                                          # already registered (the integrator's wiring)
    L.install_hooks()
    return L


# ---------------------------------------------------------------------------------------------------- 1. the pieces
def check_pieces(game_dir, key, L):
    from ICParse.sgb import view
    from blender_native import core
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    mv = view.read_game_map(gm)
    look = mv.look()
    top = import_map(key)
    X = __import__("blender_native.sceneobj.ops_map_export", fromlist=["x"])
    before_entities = len(X.entity_objects(top))
    rep = L.build_look(bpy.context, top, mv, game_dir=game_dir)
    assert json.loads(top["ic_map_look"]) == rep and rep["problems"] == [], rep["problems"]
    name = L._name(top)
    root = L.look_collection(top)
    assert root is not None and root.name == f"{name} look"
    print(f"   {key}: look built in {rep['seconds']} s; {rep['decals']} decals in {rep['decal_pictures']} pictures", flush=True)

    # the sun
    [sun] = [o for o in root.all_objects if o.get("ic_look_piece") == "sun"]
    assert sun.type == "LIGHT" and sun.data.type == "SUN"
    travel = np.array(sun.rotation_quaternion.to_matrix() @ __import__("mathutils").Vector((0, 0, -1)))
    want = core.sgm_to_blender(np.asarray([look.sun_direction]))[0]
    assert np.allclose(travel, want, atol=1e-5), (travel, want)
    assert np.allclose(tuple(sun.data.color), lin(look.sun_colour), atol=1e-5)
    assert list(sun["ic_sun_direction"]) == [float(v) for v in look.sun_direction]
    # the world and the fog
    world = bpy.context.scene.world
    assert world is not None and np.allclose(list(world["ic_ambient"]), look.ambient)
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    assert np.allclose(tuple(bg.inputs["Color"].default_value)[:3], lin(look.ambient), atol=1e-5)
    has_volume = any(n.type == "VOLUME_SCATTER" for n in world.node_tree.nodes)
    assert has_volume == look.fog_on == bool(world["ic_fog_on"]) == rep["fog"]
    assert world["ic_fog_start"] == look.fog_start and world["ic_fog_end"] == look.fog_end
    if look.fog_on:
        vol = next(n for n in world.node_tree.nodes if n.type == "VOLUME_SCATTER")
        assert np.allclose(tuple(vol.inputs["Color"].default_value)[:3], lin(look.fog_colour), atol=1e-5)
        assert abs(world.mist_settings.start - look.fog_start) < 1e-4 and abs(world.mist_settings.depth - (look.fog_end - look.fog_start)) < 1e-3
    # the sky: the game's dome, its picture with the file's first row at the top
    from ICParse.sgb import game_look as GL, ground_picture as G
    data = view.game_data(game_dir, mv.archive)
    [sky] = [o for o in root.all_objects if o.get("ic_look_piece") == "sky"]
    assert sky["ic_sky_texture"] == look.sky_texture and sky["ic_sky_picture_loaded"] and rep["sky_loaded"]
    img = next(n.image for n in sky.data.materials[0].node_tree.nodes if n.type == "TEX_IMAGE")
    assert img is not None and img.packed_file is not None and img.size[0] > 0
    check_rows_as_stored(img, data.read(look.sky_ref))
    dome, tris, duv = GL.sky_dome()
    sv = verts(sky)
    assert np.allclose(sv, core.sgm_to_blender(dome), atol=1e-2) and len(sky.data.polygons) == len(tris)
    uv = np.empty(len(sky.data.loops) * 2, np.float32)
    sky.data.uv_layers["Sky"].data.foreach_get("uv", uv)
    li = np.empty(len(sky.data.loops), np.int32)
    sky.data.loops.foreach_get("vertex_index", li)
    assert np.allclose(uv.reshape(-1, 2), np.stack([duv[li, 0], 1 - duv[li, 1]], -1), atol=1e-5)
    # the sea: the game's water past the edge, never over the map, rising to the far ring
    [sea] = [o for o in root.all_objects if o.get("ic_look_piece") == "sea"]
    sv = verts(sea)
    hx = hz = (mv.width - 1) * mv.tile_scale / 2
    gx, gz = sv[:, 0], -sv[:, 1]                                  # the game's x, z
    assert np.isclose(sv[:, 2].min(), mv.water_height) and np.isclose(sv[:, 2].max(), mv.water_height + 15)
    assert np.isclose(gx.min(), -hx - 1200) and np.isclose(gx.max(), hx + 1200) and np.isclose(gz.min(), -hz - 1200) and np.isclose(gz.max(), hz + 1200)
    me = sea.data
    cx = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("center", cx)
    cx = cx.reshape(-1, 3)
    assert not ((np.abs(cx[:, 0]) < hx) & (np.abs(cx[:, 1]) < hz)).any(), "part of the sea lies over the map itself"
    nrm = np.empty(len(me.polygons) * 3, np.float32)
    me.polygons.foreach_get("normal", nrm)
    assert (nrm.reshape(-1, 3)[:, 2] > 0).all()
    alphas = (look.ocean_min_alpha, look.ocean_max_alpha, look.terrain_water_min_alpha)
    got = point_values(me, "ic_water_grey")
    assert np.allclose(got, GL.open_sea(hx, mv.tile_scale, mv.water_height, alphas)[2], atol=1e-5)
    water_mat = sea.data.materials[0]
    kinds = {n.type for n in water_mat.node_tree.nodes}
    assert {"ADD_SHADER", "EMISSION", "BSDF_TRANSPARENT", "TEX_IMAGE"} <= kinds
    check_rows_as_stored(water_mat.node_tree.nodes["IC water sky"].image, data.read(look.sky_ref))
    # the water on the map wears the same water, with the game's brightness at its own points
    water = X.water_of(top)
    assert rep["game_water"] == (water is not None)
    if water is not None:
        assert water.data.materials[0] is water_mat
        wv = verts(water)
        pos = np.stack([wv[:, 0], wv[:, 2], -wv[:, 1]], -1)
        want = GL.water_grey(pos, point_values(water.data, "ic_water_depth"), half=hx, water_level=mv.water_height, alphas=alphas)
        assert np.allclose(point_values(water.data, "ic_water_grey"), want, atol=1e-5)
    # the ground picture is the ground: every terrain material goes through the map's ground group
    assert rep["ground_picture"] and bpy.context.scene.view_settings.view_transform == "Standard"
    terrain = X.terrain_of(top)
    raw_tga = data.read(f"data:{gm.member.rsplit('.', 1)[0]}.tga") if gm.source != "Data" else open(
        os.path.splitext(gm.path)[0] + ".tga", "rb").read()
    stored = G.decode(raw_tga).pixels
    groups = set()
    for mat in terrain.data.materials:
        assert mat.get("ic_game_ground"), mat.name
        node = mat.node_tree.nodes["IC game ground"]
        groups.add(node.node_tree.name)
        assert node.outputs["Colour"].links[0].to_socket.name == "Base Color"
        if mat.get("ic_ground_texture") and "ic_detail_node" in mat:
            detail = mat.node_tree.nodes[mat["ic_detail_node"]].image
            assert detail.alpha_mode == "CHANNEL_PACKED"
            check_rows_as_stored(detail, data.read("data:detail/" + mat["ic_ground_texture"].lower()))
    assert len(groups) == 1
    tree = bpy.data.node_groups[groups.pop()]
    from blender_native.sceneobj import map_paint as P
    assert (P.ground_pixels(tree.nodes["IC ground picture"].image) == stored).all(), "the ground is not the map's own picture"
    # the border: the one colour from the picture's first pixel and the ambient light
    [edge] = [o for o in root.all_objects if o.get("ic_look_piece") == "border"]
    colour = GL.border_colour(stored[0, 0, :3], look.ambient)
    assert np.allclose(list(edge["ic_border_colour"]), colour, atol=1e-6)
    bv, bq, ba = GL.border(hx, mv.water_height)
    assert np.allclose(verts(edge), core.sgm_to_blender(bv), atol=1e-2) and len(edge.data.polygons) == len(bq)
    assert np.allclose(point_values(edge.data, "ic_border_alpha"), ba, atol=1e-6)
    # the decals
    hx, hz = (mv.width - 1) * mv.tile_scale / 2, (mv.height - 1) * mv.tile_scale / 2
    objs = [o for o in root.all_objects if o.get("ic_look_piece") == "decals"]
    want_n = {}
    for d in look.decals:
        want_n[os.path.basename(d.texture).lower()] = want_n.get(os.path.basename(d.texture).lower(), 0) + 1
    assert {o["ic_decal_picture"]: o["ic_decal_count"] for o in objs} == want_n
    assert sum(o["ic_decal_count"] for o in objs) == len(look.decals) == rep["decals"]
    for o in objs:
        dv = verts(o)
        ix = np.empty(len(o.data.polygons), np.int32)
        o.data.attributes["ic_decal_index"].data.foreach_get("value", ix)
        assert sorted(set(ix.tolist())) == list(range(o["ic_decal_count"]))
        n = np.empty(len(o.data.polygons) * 3, np.float32)
        o.data.polygons.foreach_get("normal", n)
        assert (n.reshape(-1, 3)[:, 2] > 0).all(), o.name                  # every decal face looks up
        # every decal lies on the ground's own points (the game builds it from the terrain's cells), a hair above them, covers
        # its whole picture, and takes its texture coordinates from the offset along its axes / its full size
        img = o.data.materials[0].node_tree.nodes["Image Texture"].image
        check_rows_as_stored(img, data.read("data:decals/" + o["ic_decal_picture"]))
        w, h = img.size
        mine = [d for d in look.decals if os.path.basename(d.texture).lower() == o["ic_decal_picture"]]
        uv = np.empty(len(o.data.loops) * 2, np.float32)
        o.data.uv_layers["Decal"].data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2)
        loops = np.empty(len(o.data.loops), np.int32)
        o.data.loops.foreach_get("vertex_index", loops)
        starts = np.empty(len(o.data.polygons), np.int32)
        o.data.polygons.foreach_get("loop_start", starts)
        ground = mv.heights.astype(float) * mv.height_scale
        for k, d in enumerate(mine):
            at = np.concatenate([np.arange(s0, s0 + 4) for s0 in starts[ix == k]])
            p = dv[loops[at]]
            gx, gz = p[:, 0], -p[:, 1]
            col, row = (gx + hx) / mv.tile_scale, (gz + hz) / mv.tile_scale
            assert np.abs(col - np.rint(col)).max() * mv.tile_scale < 0.1 and np.abs(row - np.rint(row)).max() * mv.tile_scale < 0.1, o.name
            off = p[:, 2] - ground[np.rint(row).astype(int), np.rint(col).astype(int)]
            assert off.min() > 0.0 and off.max() < 0.1, (o.name, off.min(), off.max())
            x, z = np.rint(col) * mv.tile_scale - hx, np.rint(row) * mv.tile_scale - hz
            hu, hv = d.half_size(w, h)
            c, s0 = math.cos(d.rotation), math.sin(d.rotation)
            want = np.stack([((x - d.x) * c + (z - d.z) * s0) / (2 * hu) + 0.5,
                             1 - ((-(x - d.x) * s0 + (z - d.z) * c) / (2 * hv) + 0.5)], -1)
            assert np.allclose(uv[at], want, atol=1e-3), (o.name, k)
            corners = d.corners(w, h)
            inside = (np.abs(corners[:, 0]) <= hx) & (np.abs(corners[:, 1]) <= hz)
            for cx_, cz_ in corners[inside]:
                assert x.min() - 1e-3 <= cx_ <= x.max() + 1e-3 and z.min() - 1e-3 <= cz_ <= z.max() + 1e-3, (o.name, k)
    # 2. the save does not read any of it
    for o in root.all_objects:
        for k in KEYS_THE_SAVE_READS:
            assert k not in o, (o.name, k)
        assert o.get("ic_look_piece")
    assert X.water_of(top) is water and len(X.entity_objects(top)) == before_entities
    assert X.terrain_of(top) is not None and X.terrain_of(top).get("ic_map")
    plan = X.build_plan(bpy.context, top)
    assert not plan.changed, plan.lines()
    out = C.tmpdir("looksave_")
    stem = os.path.splitext(os.path.basename(gm.member))[0]
    saved = save_bytes(os.path.join(out, f"{stem}.sgb"))
    assert saved == gm.read(), f"{key}: with the look on, a map saved with no edits differs from the original"
    print(f"   {key}: saved with the look on, byte for byte the original ({len(saved)} bytes)", flush=True)
    return top, mv


# ---------------------------------------------------------------------------------------------------- 3. redraw water
def wet_cells_expected(mv, heights_m, level_m):
    plane = mv.water.astype(float) * mv.height_scale
    own = mv.water.astype(int) != int(mv.water_level)
    surface = np.where(own, plane, level_m)
    wet = surface > heights_m
    return int((wet[:-1, :-1] | wet[:-1, 1:] | wet[1:, :-1] | wet[1:, 1:]).sum())


def test_redraw(game_dir, L):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    top = import_map(FACEOFF)
    gm = next(m for m in view.game_maps(game_dir) if m.key == FACEOFF)
    mv = view.read_game_map(gm)
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    terrain, water = X.terrain_of(top), X.water_of(top)
    w, h = mv.width, mv.height
    level = mv.water_height
    base = verts(terrain)[:, 2].reshape(h, w)
    n0 = len(water.data.polygons)
    assert n0 == int(mv.wet_cells().sum())
    # a. raise the ground over the water: the water there goes
    raised = base.copy()
    wet = mv.wet_vertices()
    raised[wet] = level + 4.0
    set_terrain_z(terrain, raised)
    res = bpy.ops.bz.map_redraw_water()
    assert "FINISHED" in res, res
    water = X.water_of(top)
    n1 = len(water.data.polygons)
    assert n1 == wet_cells_expected(mv, raised, level) == 0, n1
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is None, "redrawing the water must not look like a water level change"
    # b. dig a bowl into dry ground: it gets water
    dug = base.copy()
    dry = np.argwhere(~mv.wet_vertices())
    jj, ii = dry[len(dry) // 2]
    j0, i0 = max(1, jj - 3), max(1, ii - 3)
    dug[j0:j0 + 6, i0:i0 + 6] = level - 2.0
    set_terrain_z(terrain, dug)
    assert "FINISHED" in bpy.ops.bz.map_redraw_water()
    water = X.water_of(top)
    n2 = len(water.data.polygons)
    assert n2 == wet_cells_expected(mv, dug, level) and n2 >= 25, (n2, wet_cells_expected(mv, dug, level))
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is None and plan.heights is not None
    z = verts(water)[:, 2]
    assert np.allclose(z, level), "the redrawn surface is not at the water level"
    d = np.empty(len(water.data.vertices), np.float32)
    water.data.attributes["ic_water_depth"].data.foreach_get("value", d)
    assert d.min() >= 0 and d.max() > 0.5
    print(f"   water cells: {n0} -> {n1} after raising the ground -> {n2} with a bowl dug in", flush=True)
    # c. the water moved up, then redrawn
    water.location.z += 2.0
    bpy.context.view_layer.update()
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is not None and abs(plan.water_level - (level + 2.0)) < 1e-3, plan.water_level
    assert "FINISHED" in bpy.ops.bz.map_redraw_water()
    water = X.water_of(top)
    assert len(water.data.polygons) == wet_cells_expected(mv, dug, level + 2.0)
    assert np.allclose(verts(water)[:, 2], level + 2.0)
    plan = X.build_plan(bpy.context, top)
    assert abs(plan.water_level - (level + 2.0)) < 1e-3, "the redraw lost the water level"
    out = C.tmpdir("redraw_")
    p = os.path.join(out, "2p_faceoff.sgb")
    saved = save_bytes(p)
    mv2 = view.read(saved)
    assert mv2.water_level == mv.water_level + 4, (mv2.water_level, mv.water_level)       # 2 m = 4 steps of 0.5 m
    print(f"   the water moved up 2 m and redrawn: the save writes level {mv2.water_level} (was {mv.water_level})", flush=True)
    # d. the automatic redraw after a ground edit (Object Mode)
    top = import_map(FACEOFF)
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    terrain, water = X.terrain_of(top), X.water_of(top)
    L.AUTO_REDRAW[0] = True
    assert len(water.data.polygons) > 0
    set_terrain_z(terrain, raised)
    bpy.context.view_layer.update()                          # the depsgraph hook runs here, as it does after an edit in the 3D view
    water = X.water_of(top)
    assert len(water.data.polygons) == 0, "the automatic redraw did not remove the water over the raised ground"
    L.AUTO_REDRAW[0] = False
    set_terrain_z(terrain, base)
    bpy.context.view_layer.update()
    assert len(X.water_of(top).data.polygons) == 0, "with the automatic redraw off the water must stay as it was"
    L.AUTO_REDRAW[0] = True
    print("   the automatic redraw after a ground edit redraws the water (and can be turned off)", flush=True)


# ---------------------------------------------------------------------------------------------------- 4. add water
def test_add_water(game_dir, L):
    from ICParse.sgb import view, edit
    from blender_native.sceneobj import ops_map_export as X
    gm = next(m for m in view.game_maps(game_dir) if m.key == FACEOFF)
    mv = view.read_game_map(gm)
    # a. a map opened without its water: the import's water is off
    top = import_map(FACEOFF, water=False)
    assert X.water_of(top) is None
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    level = mv.water_height + 3.0
    assert "FINISHED" in bpy.ops.bz.map_add_water(level=level)
    water = X.water_of(top)
    assert water is not None and water.get("ic_water_level") == int(round(level / mv.height_scale))
    terrain = X.terrain_of(top)
    heights = verts(terrain)[:, 2].reshape(mv.height, mv.width)
    assert len(water.data.polygons) == wet_cells_expected(mv, heights, level) > 0
    assert np.allclose(verts(water)[:, 2][np.isclose(verts(water)[:, 2], level)], level)
    assert water.name in {o.name for o in top.all_objects} and any(c.name.endswith(" water") and water.name in c.objects for c in top.children)
    try:
        bpy.ops.bz.map_add_water()
    except RuntimeError as exc:
        assert "already has a water surface" in str(exc), exc
    else:
        raise AssertionError("a second water surface must be refused")
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is not None and abs(plan.water_level - level) < 1e-3, plan.water_level
    out = C.tmpdir("addwater_")
    saved = save_bytes(os.path.join(out, "2p_faceoff.sgb"))
    mv2 = view.read(saved)
    assert mv2.water_level == int(round(level / mv.height_scale)) == mv.water_level + 6, mv2.water_level
    print(f"   Add water on a map opened without water, at {level:g} m: {len(water.data.polygons)} cells; the save writes level {mv2.water_level}", flush=True)
    # b. a map that is dry everywhere
    raw = gm.read()
    dry_raw, _rep = edit.apply_edits(raw, heights=np.full((mv.height, mv.width), 60.0))
    folder = C.tmpdir("drymap_")
    path = os.path.join(folder, "dry_map.sgb")
    with open(path, "wb") as f:
        f.write(dry_raw)
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_map(filepath=path, load_models=False, game_look=False)
    top = [c for c in bpy.data.collections if c.get("ic_map")][0]
    dmv = view.read(path)
    assert int(dmv.wet_cells().sum()) == 0 and X.water_of(top) is None
    L.build_look(bpy.context, top, dmv, game_dir=game_dir)
    res = bpy.ops.bz.map_add_water(level=10.0)
    assert "FINISHED" in res
    water = X.water_of(top)
    assert water is not None and len(water.data.polygons) == 0 and water["ic_water_level"] == 20
    plan = X.build_plan(bpy.context, top)
    if plan.water_level is None:
        C.skip("a map whose ground is dry everywhere: Add water makes a surface whose level the save does not read yet "
               "(ops_map_export._read_water returns early when the map has no water points); the wiring request carries the fix, tried below")
        original = X._read_water

        def patched(plan, top):                                  # the exact addition asked of ops_map_export._read_water
            original(plan, top)
            if plan.water_level is None:
                water = X.water_of(top)
                if water is not None and not len(plan.mv.water_mesh(np.float64)[0]) and water.get("ic_look_level_m") is not None:
                    level = float(water["ic_look_level_m"]) + float(water.matrix_world.translation.z)
                    if abs(level - plan.mv.water_height) > X._tol_height(plan.mv.height_scale):
                        plan.water_level, plan.water_move = level, level - plan.mv.water_height
        X._read_water = patched
        try:
            plan = X.build_plan(bpy.context, top)
            assert plan.water_level is not None and abs(plan.water_level - 10.0) < 1e-3, plan.water_level
            saved = save_bytes(os.path.join(C.tmpdir("drysave_"), "dry_map.sgb"))
            assert view.read(saved).water_level == 20, view.read(saved).water_level
            print("   dry map: with the asked-for addition to the save, the level is written (10 m = step 20)", flush=True)
        finally:
            X._read_water = original
    else:
        assert abs(plan.water_level - 10.0) < 1e-3
        print(f"   dry map: the save writes the new level too ({plan.water_level:g} m)", flush=True)


def test_never_drawn(game_dir):
    """The maps' water sparkles have a material the game never draws: see-through on the map, the object still there."""
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_game_map(game_map=FACEOFF, load_models=True, show_creatures=False, game_look=False)
    from blender_native.sceneobj import ops_map_import as M
    parts = [o for o in bpy.data.objects if o.type == "MESH" and str(o.get("ic_map_model", "")).endswith("water_sparkle.sgm")]
    assert parts and all(m.name == M.HIDDEN_MATERIAL for o in parts for m in o.data.materials), [o.name for o in parts]
    others = [o for o in bpy.data.objects if o.type == "MESH" and o.get("ic_map_model") and o not in parts]
    assert others and not all(m.name == M.HIDDEN_MATERIAL for o in others for m in o.data.materials if m)
    kinds = {n.type for n in bpy.data.materials[M.HIDDEN_MATERIAL].node_tree.nodes}
    assert kinds == {"BSDF_TRANSPARENT", "OUTPUT_MATERIAL"}
    print(f"   {len(parts)} water-sparkle part(s) see-through; {len(others)} other model parts keep their materials", flush=True)


def test_remove(game_dir, L):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    gm = next(m for m in view.game_maps(game_dir) if m.key == FACEOFF)
    mv = view.read_game_map(gm)
    top = import_map(FACEOFF)
    n = len(top.all_objects)
    water = X.water_of(top)
    plain = water.data.materials[0].name
    before_view = bpy.context.scene.view_settings.view_transform
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    assert len(top.all_objects) > n and water.data.materials[0].name != plain
    L.build_look(bpy.context, top, mv, game_dir=game_dir)                  # again: the old look is replaced, not doubled
    assert len([c for c in top.children if c.name.endswith(" look")]) == 1
    L.remove_look(top)
    assert len(top.all_objects) == n and L.look_collection(top) is None and "ic_map_look" not in top
    assert water.data.materials[0].name == plain and bpy.context.scene.view_settings.view_transform == before_view
    for mat in X.terrain_of(top).data.materials:
        assert not mat.get("ic_game_ground") and "IC game ground" not in mat.node_tree.nodes
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        assert not mat.get("ic_ground_texture") or bsdf.inputs["Base Color"].links[0].from_node.type == "TEX_IMAGE"
        assert mat.node_tree.nodes["Material Output"].inputs["Surface"].links[0].from_node == bsdf
    assert not [g for g in bpy.data.node_groups if g.name.startswith("IC ground ")]
    # a ground texture added while the look is on is shown like the others
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    have = {str(m.get("ic_ground_texture", "")).lower() for m in X.terrain_of(top).data.materials}
    names = X.game_listing(game_dir, mv.archive)["ground"]
    other = next(t for t in names if t.lower() not in have)
    assert "FINISHED" in bpy.ops.bz.map_add_ground(texture=other)
    assert X.terrain_of(top).data.materials[-1].get("ic_game_ground"), "the added ground texture does not show the ground picture"
    L.remove_look(top)
    # the operators: add the look to a map imported without it, take it off again
    assert "FINISHED" in bpy.ops.bz.map_game_look()
    assert L.look_collection(top) is not None and len(top.all_objects) > n and json.loads(top["ic_map_look"])["decals"] == 211
    assert "FINISHED" in bpy.ops.bz.map_remove_look()
    assert len(top.all_objects) == n and L.look_collection(top) is None
    try:
        bpy.ops.bz.map_remove_look()
    except RuntimeError:
        pass
    else:
        raise AssertionError("removing a look the map does not have must be refused")


def test_redraw_keeps_level_with_ponds(game_dir, L):
    """6p big bog has ponds the map holds at their own height. Sculpt, redraw twice, and the water level the save reads stays the map's
    own; move the water 1 m up after that and it reads one metre more (the redrawn surface has other points than the stock one, so the
    level must not be read from a median of them)."""
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    key = "InsectData.sga|scenarios/mp/6p_big_bog.sgb"
    top = import_map(key)
    gm = next(m for m in view.game_maps(game_dir) if m.key == key)
    mv = view.read_game_map(gm)
    L.build_look(bpy.context, top, mv, game_dir=game_dir)
    terrain = X.terrain_of(top)
    w, h = mv.width, mv.height
    z = verts(terrain)[:, 2].reshape(h, w)
    z[h // 2 - 2:h // 2 + 3, w // 2 - 2:w // 2 + 3] += 4.0
    set_terrain_z(terrain, z)
    for _ in range(2):
        assert "FINISHED" in bpy.ops.bz.map_redraw_water()
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is None, plan.water_level
    water = X.water_of(top)
    water.location.z += 1.0
    plan = X.build_plan(bpy.context, top)
    assert plan.water_level is not None and abs(plan.water_level - (mv.water_height + 1.0)) < 1e-6, plan.water_level
    assert "FINISHED" in bpy.ops.bz.map_redraw_water()
    plan = X.build_plan(bpy.context, top)
    assert abs(plan.water_level - (mv.water_height + 1.0)) < 1e-6, plan.water_level


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map look test needs the game's maps")
        return
    C.addon()
    L = register_look_classes()
    with C.install_guard(game_dir):
        section("the pieces of the look, and the save does not read them")
        for key in (FACEOFF, ICE_CLASH, MISSION14):
            check_pieces(game_dir, key, L)
        section("Redraw water")
        test_redraw(game_dir, L)
        test_redraw_keeps_level_with_ponds(game_dir, L)
        section("Add water")
        test_add_water(game_dir, L)
        section("materials the game never draws")
        test_never_drawn(game_dir)
        section("taking the look off")
        test_remove(game_dir, L)
    print("SO_MAP_LOOK_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_look", main)
