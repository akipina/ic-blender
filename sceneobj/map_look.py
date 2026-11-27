"""The game's look for an imported map: ground picture, sun, sky, fog, water, sea, border and decals (rules from
``ICParse.sgb.game_look``). Entry points: ``build_look``, ``remove_look``, ``redraw_water``, ``add_water``.

The save must never read the look: new objects carry no ``ic_water_level`` / ``ic_blueprint`` / ``ic_map`` mark, sit in
collections the save ignores, and dressed materials keep their marks (tests/so_map_look_test.py: byte-identical save).
"""
from __future__ import annotations

import importlib
import json
import math
import os
import shutil
import tempfile
import time

import numpy as np

import bpy
from bpy.props import FloatProperty

from . import ops_map_import as M

KEY_LOOK = "ic_map_look"                 # on the map's collection: the look report as JSON
KEY_PIECE = "ic_look_piece"              # on every object of the look: what it is ("sun", "sky", "sea", "decals", "water colour")
KEY_DECALS = "ic_decal_count"
KEY_PLAIN_WATER = "ic_plain_water"       # on the water object while it wears the game's water: the material it had
KEY_GROUND_NODES = "ic_game_ground"      # on a terrain material while it shows the game's ground
KEY_VIEW_BEFORE = "ic_view_before"       # on the map's collection: the scene's view transform before the look
DETAIL_VIEW_RANGE = 450.0                # m: the game draws a block's detail texture while the eye is this near
GAMMA = 2.2                              # the game works in screen values; Blender's colours are linear
SUN_STRENGTH = 3.0                       # Blender's sun lamp strength for the map's sun (the game's light is not in watts)
SOLID_WATER = (0.149, 0.275, 0.376)      # the water in the Solid view
DECAL_LIFT = 0.04                        # m off the ground along its normal, against the two drawing in one place
DECAL_LIFT_STEP = 0.001                  # ... and this much more for each further picture, where decals overlap
AUTO_REDRAW_MAX_POINTS = 70000           # the automatic redraw after a ground edit runs only on maps up to 257 x 257 points
_PKG = M._PKG


def _addon(name: str):
    return importlib.import_module(f"{_PKG}.{name}")


# ---------------------------------------------------------------------------------------------------- small helpers
def srgb_to_linear(c):
    """The game's colours are plain display values; Blender's colour inputs are linear."""
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin(rgb) -> tuple:
    return tuple(float(v) for v in srgb_to_linear(np.clip(np.asarray(rgb[:3], np.float64), 0.0, 1.0)))


def _gl():
    from ICParse.sgb import game_look
    return game_look


def _paint():
    return importlib.import_module(f"{_PKG}.sceneobj.map_paint")


def _node(nt, kind: str, name: str = "", **values):
    n = nt.nodes.new(kind)
    if name:
        n.name = n.label = name
    for key, value in values.items():
        setattr(n, key, value)
    return n


def _math(nt, op: str, a, b=None, *, clamp: bool = False):
    """A math node's output; ``a`` and ``b`` are numbers or other nodes' outputs."""
    n = _node(nt, "ShaderNodeMath", operation=op, use_clamp=clamp)
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = float(v)
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def _vector(nt, op: str, a, b, name: str = ""):
    """A vector-math node; ``a`` and ``b`` are triples or other nodes' outputs. Returns the node."""
    n = _node(nt, "ShaderNodeVectorMath", name, operation=op)
    for i, v in enumerate((a, b)):
        if isinstance(v, (tuple, list)):
            n.inputs[i].default_value = tuple(float(c) for c in v)
        else:
            nt.links.new(v, n.inputs[i])
    return n


def _combine(nt, x, y):
    n = _node(nt, "ShaderNodeCombineXYZ")
    for i, v in enumerate((x, y)):
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = float(v)
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs["Vector"]


def _image_node(nt, image, vector, name: str = "", *, repeat: bool = False):
    n = _node(nt, "ShaderNodeTexImage", name)
    n.image = image
    n.extension = "REPEAT" if repeat else "EXTEND"
    nt.links.new(vector, n.inputs["Vector"])
    return n


def _half(mv) -> float:
    return (mv.width - 1) * mv.tile_scale / 2.0


def _alphas(look) -> tuple:
    return look.ocean_min_alpha, look.ocean_max_alpha, look.terrain_water_min_alpha


def _name(top) -> str:
    return top.name[len("Map "):] if top.name.startswith("Map ") else top.name


def _sub(parent, name: str):
    existing = next((c for c in parent.children if c.name == name), None)
    return existing if existing is not None else M._new_collection(name, parent)


def look_collection(top):
    """The ``<map> look`` collection of the map, or None."""
    return next((c for c in top.children if c.name.endswith(" look")), None)


def _remove_objects_of(coll) -> None:
    for o in list(coll.all_objects):
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            store = bpy.data.lights if isinstance(data, bpy.types.Light) else bpy.data.meshes
            store.remove(data)
    for c in list(coll.children):
        _remove_objects_of(c)
        bpy.data.collections.remove(c)


def remove_look(top) -> None:
    """Take the look off the map again."""
    undress_ground(top)
    undress_water(top)
    if KEY_VIEW_BEFORE in top:
        try:
            bpy.context.scene.view_settings.view_transform = str(top[KEY_VIEW_BEFORE])
        except (TypeError, AttributeError):
            pass
        del top[KEY_VIEW_BEFORE]
    coll = look_collection(top)
    if coll is not None:
        _remove_objects_of(coll)
        bpy.data.collections.remove(coll)
    top.pop(KEY_LOOK, None)
    world = bpy.data.worlds.get(f"Map {_name(top)} look")
    if world is not None and world.users == 0:
        bpy.data.worlds.remove(world)


def _mark(obj, piece: str) -> None:
    obj[KEY_PIECE] = piece
    M.stamp(obj)


# ---------------------------------------------------------------------------------------------------- sun, world, sky
def _sun(context, coll, name, look):
    core = _addon("core")
    d = np.asarray([look.sun_direction], dtype=np.float64)
    db = core.sgm_to_blender(d)[0]                         # the game's axes -> Blender's
    from mathutils import Vector
    lamp = bpy.data.lights.new(f"{name} sun", "SUN")
    lamp.color = _lin(look.sun_colour)
    lamp.energy = SUN_STRENGTH
    lamp.angle = math.radians(0.53)
    obj = bpy.data.objects.new(f"{name} sun", lamp)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector(db.tolist()).to_track_quat("-Z", "Y")   # a sun lamp shines along its own -Z
    obj.location = (0.0, 0.0, 500.0)
    coll.objects.link(obj)
    _mark(obj, "sun")
    obj["ic_sun_direction"] = [float(v) for v in look.sun_direction]          # the game's own values, for a later look
    obj["ic_sun_colour"] = [float(v) for v in look.sun_colour]
    return obj


def _world(context, name, look):
    world = bpy.data.worlds.new(f"Map {name} look")
    return _fill_world(context, world, look)


def _fill_world(context, world, look):
    """Set the world's nodes and fog values from ``look`` (anything with ``ambient``, ``fog_on``, ``fog_start``, ``fog_end``, ``fog_colour``)."""
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (*_lin(look.ambient), 1.0)
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    world["ic_ambient"] = [float(v) for v in look.ambient]
    world["ic_fog_on"] = bool(look.fog_on)
    world["ic_fog_start"] = float(look.fog_start)
    world["ic_fog_end"] = float(look.fog_end)
    world["ic_fog_colour"] = [float(v) for v in look.fog_colour]
    try:                                                   # the mist pass, for a compositor
        world.mist_settings.start = max(0.0, look.fog_start)
        world.mist_settings.depth = max(1.0, look.fog_end - look.fog_start)
        world.mist_settings.falloff = "LINEAR"
    except Exception:
        pass
    if look.fog_on:                                        # Eevee-friendly: a thin scattering volume over the whole world
        vol = nt.nodes.new("ShaderNodeVolumeScatter")
        vol.inputs["Color"].default_value = (*_lin(look.fog_colour), 1.0)
        vol.inputs["Density"].default_value = 3.0 / max(1.0, look.fog_end)      # about 5 % of the light is left at the fog's end
        nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
        try:
            context_scene = bpy.context.scene
            context_scene.eevee.volumetric_start = max(0.1, look.fog_start)
            context_scene.eevee.volumetric_end = max(1.0, look.fog_end)
        except Exception:
            pass
    return world


def _picture(data, ref: str, tmp: str, label: str, *, raw_values: bool = False):
    """One of the game's pictures as a packed Blender image with the file's first row at the top (so that Blender's v = 1 - the
    game's v), or None. ``raw_values``: for shader arithmetic in screen values (no colour conversion)."""
    raw = data.read(ref) if (data is not None and ref) else None
    if not raw:
        return None
    path = os.path.join(tmp, label, os.path.basename(ref))
    try:
        img = M.load_game_picture(raw, path)
        img.name = f"{label} {os.path.basename(ref)}"
        if raw_values:
            img.colorspace_settings.name = "Non-Color"
        img.pack()
    except Exception:
        return None
    return img


def _sky_dome(context, coll, name, look, data, tmp):
    """The game's sky: its flattened dome with the map's sky picture, drawn to the camera only, following the scene's camera at
    0.9 of its position (the game draws it with the view's translation x 0.1)."""
    L = _gl()
    core = _addon("core")
    img = _picture(data, look.sky_ref, tmp, "Sky")
    verts, tris, uv = L.sky_dome()
    vb = core.sgm_to_blender(verts)
    normal = np.cross(vb[tris[:, 1]] - vb[tris[:, 0]], vb[tris[:, 2]] - vb[tris[:, 0]])
    outward = (normal * (vb[tris].mean(axis=1) - (0.0, 0.0, -400.0))).sum(axis=1) > 0
    tris = tris.copy()
    tris[outward] = tris[outward][:, ::-1]                     # every face looks inward: from outside the dome is not drawn
    me = bpy.data.meshes.new(f"{name} sky")
    me.from_pydata(vb.tolist(), [], tris.tolist())
    me.update()
    uvl = me.uv_layers.new(name="Sky")
    flat = np.stack([uv[:, 0], 1.0 - uv[:, 1]], -1)
    uvl.data.foreach_set("uv", M._loop_values(me, flat).astype(np.float32).ravel())
    mat = bpy.data.materials.new(f"Sky {os.path.basename(look.sky_texture) or 'none'}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    emit = nt.nodes.new("ShaderNodeEmission")
    if img is not None:
        uvn = _node(nt, "ShaderNodeUVMap", uv_map="Sky")
        tex = _image_node(nt, img, uvn.outputs["UV"])
        nt.links.new(tex.outputs["Color"], emit.inputs["Color"])
    else:
        emit.inputs["Color"].default_value = (*_lin(look.ambient), 1.0)
    nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    mat.use_backface_culling = True
    me.materials.append(mat)
    obj = bpy.data.objects.new(f"{name} sky", me)
    coll.objects.link(obj)
    _mark(obj, "sky")
    obj["ic_sky_texture"] = look.sky_texture
    obj["ic_sky_picture_loaded"] = img is not None
    camera = context.scene.camera
    if camera is not None:
        con = obj.constraints.new("COPY_LOCATION")
        con.target = camera
        con.influence = L.SKY_EYE_FACTOR
    for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter", "visible_shadow"):
        try:
            setattr(obj, attr, False)                       # it is a picture to look at, not a light or a shadow caster
        except Exception:
            pass
    obj.hide_select = True
    return obj


# ---------------------------------------------------------------------------------------------------- the water
def _floor_colour(mv, look, base) -> tuple:
    """A typical sea floor in screen values (the ground picture's mean in ambient light): what the water's light is added to."""
    mean = base.reshape(-1, 4)[:, :3].mean(axis=0) / 255.0 if base is not None else np.array([0.4, 0.4, 0.4])
    return tuple(float(min(1.0, m * a)) for m, a in zip(mean, look.ambient))


def _water_game_material(mat_name, sky_img, ripple_img, floor):
    """The game's water: sky(reflected view) x grey x ripple, added to what is behind."""
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    geo = _node(nt, "ShaderNodeNewGeometry")
    inc = _node(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Incoming"], inc.inputs["Vector"])
    pos = _node(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Position"], pos.inputs["Vector"])
    # the reflected view, in the game's axes: x = Ix, up = Iz, z = Iy (I points from the water to the eye; Blender's x is the game's -x)
    down = _math(nt, "MAXIMUM", _math(nt, "ADD", inc.outputs["Z"], 1.0), 0.001)
    u = _math(nt, "ADD", _math(nt, "MULTIPLY", _math(nt, "DIVIDE", inc.outputs["X"], down), 0.5), 0.5)
    v = _math(nt, "ADD", _math(nt, "MULTIPLY", _math(nt, "DIVIDE", inc.outputs["Y"], down), 0.5), 0.5)
    sky = _image_node(nt, sky_img, _combine(nt, u, _math(nt, "SUBTRACT", 1.0, v)), "IC water sky") if sky_img else None
    # ripple coordinates: the game's (x, z) / 32, here at the animation's first moment; the bitmap is stored bottom up
    ru = _math(nt, "DIVIDE", pos.outputs["X"], -32.0)
    rv = _math(nt, "ADD", _math(nt, "DIVIDE", pos.outputs["Y"], -32.0), 0.025)
    ripple = _image_node(nt, ripple_img, _combine(nt, ru, rv), "IC water ripple", repeat=True) if ripple_img else None
    grey = _node(nt, "ShaderNodeAttribute", attribute_name="ic_water_grey")
    base = sky.outputs["Color"] if sky else (0.45, 0.55, 0.7)
    ripples = ripple.outputs["Color"] if ripple else (0.5, 0.5, 0.5)
    colour = _vector(nt, "MULTIPLY", base, ripples).outputs["Vector"]
    scaled = _node(nt, "ShaderNodeVectorMath", operation="SCALE")
    nt.links.new(colour, scaled.inputs[0])
    nt.links.new(grey.outputs["Fac"], scaled.inputs["Scale"])
    # the game adds screen values: floor + water. In Blender's linear light the water's part is (floor + w)^g - floor^g
    total = _vector(nt, "ADD", scaled.outputs["Vector"], floor, "IC water floor")
    linear = _node(nt, "ShaderNodeGamma")
    nt.links.new(total.outputs["Vector"], linear.inputs["Color"])
    linear.inputs["Gamma"].default_value = GAMMA
    added = _vector(nt, "SUBTRACT", linear.outputs["Color"], tuple(c ** GAMMA for c in floor), "IC water floor linear")
    emit = _node(nt, "ShaderNodeEmission")
    nt.links.new(added.outputs["Vector"], emit.inputs["Color"])
    clear = _node(nt, "ShaderNodeBsdfTransparent")
    total_shader = _node(nt, "ShaderNodeAddShader")
    nt.links.new(clear.outputs["BSDF"], total_shader.inputs[0])
    nt.links.new(emit.outputs["Emission"], total_shader.inputs[1])
    out = _node(nt, "ShaderNodeOutputMaterial")
    nt.links.new(total_shader.outputs["Shader"], out.inputs["Surface"])
    mat.diffuse_color = (*SOLID_WATER, 0.6)
    M._set_blend(mat, "BLEND")
    return mat


def _water_pictures(top, look, data, tmp):
    """The water's two pictures: the sky and the first ripple frame, as raw screen values."""
    return (_picture(data, look.sky_ref, tmp, "Water sky", raw_values=True),
            _picture(data, "data:terrain/water/ranim_00000.bmp", tmp, "Water ripple", raw_values=True))


def _set_grey(me, grey) -> None:
    attr = me.attributes.get("ic_water_grey") or me.attributes.new("ic_water_grey", "FLOAT", "POINT")
    attr.data.foreach_set("value", np.asarray(grey, np.float32))


def grey_water(obj, mv, look) -> None:
    """The game's brightness on a water surface's points (from where they are and the depth the surface records)."""
    me = obj.data
    n = len(me.vertices)
    if not n:
        return
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    game = _addon("core").blender_to_sgm(co)                        # back to the game's axes
    depth = np.zeros(n, np.float32)
    att = me.attributes.get("ic_water_depth")
    if att is not None:
        att.data.foreach_get("value", depth)
    _set_grey(me, _gl().water_grey(game, depth, half=_half(mv), water_level=mv.water_height, alphas=_alphas(look)))


def dress_water(top, water, mv, look, material) -> None:
    """Put the game's water on the map's water surface (keeping the material it had for later)."""
    if KEY_PLAIN_WATER not in water:
        water[KEY_PLAIN_WATER] = water.data.materials[0].name if len(water.data.materials) and water.data.materials[0] else ""
    water.data.materials.clear()
    water.data.materials.append(material)
    grey_water(water, mv, look)


def undress_water(top) -> None:
    for o in top.all_objects:
        if KEY_PLAIN_WATER not in o or o.type != "MESH":
            continue
        plain = bpy.data.materials.get(str(o[KEY_PLAIN_WATER]))
        game = [m for m in o.data.materials if m is not None]
        o.data.materials.clear()
        if plain is not None:
            o.data.materials.append(plain)
        del o[KEY_PLAIN_WATER]
        for m in game:
            if m.users == 0:
                bpy.data.materials.remove(m)


def _sea(context, coll, name, mv, look, level_m, material):
    L = _gl()
    core = _addon("core")
    verts, quads, grey = L.open_sea(_half(mv), mv.tile_scale, level_m, _alphas(look))
    vb = core.sgm_to_blender(verts)
    flip = np.cross(vb[quads[:, 1]] - vb[quads[:, 0]], vb[quads[:, 2]] - vb[quads[:, 0]])[:, 2] < 0
    quads = quads.copy()
    quads[flip] = quads[flip][:, ::-1]
    me = M._mesh(f"{name} sea", vb, quads, game_quads=False)
    _set_grey(me, grey)
    me.materials.append(material)
    obj = bpy.data.objects.new(f"{name} sea", me)
    coll.objects.link(obj)
    _mark(obj, "sea")
    obj["ic_sea_level_m"] = float(level_m)
    obj["ic_sea_extent_m"] = float(L.SEA_BAND)
    return obj


# ---------------------------------------------------------------------------------------------------- the border
def _border_colour(look, base) -> tuple:
    first = base[0, 0, :3] if base is not None else (128, 128, 128)
    return _gl().border_colour(first, look.ambient)


def _flat_material(mat_name: str, colour, *, see_through: bool):
    """An unlit colour (screen values); ``see_through``: its alpha is the point attribute ``ic_border_alpha``."""
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    emit = _node(nt, "ShaderNodeEmission", "IC border colour")
    emit.inputs["Color"].default_value = (*_lin(colour), 1.0)
    out = _node(nt, "ShaderNodeOutputMaterial")
    if see_through:
        att = _node(nt, "ShaderNodeAttribute", attribute_name="ic_border_alpha")
        clear = _node(nt, "ShaderNodeBsdfTransparent")
        mix = _node(nt, "ShaderNodeMixShader")
        nt.links.new(att.outputs["Fac"], mix.inputs["Fac"])
        nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emit.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        M._set_blend(mat, "BLEND")
    else:
        nt.links.new(emit.outputs["Emission"], out.inputs["Surface"])
    mat.diffuse_color = (*_lin(colour), 1.0)
    return mat


def _border(coll, name, mv, level_m, colour):
    L = _gl()
    core = _addon("core")
    verts, quads, alpha = L.border(_half(mv), level_m)
    me = M._mesh(f"{name} border", core.sgm_to_blender(verts), quads)
    att = me.attributes.new("ic_border_alpha", "FLOAT", "POINT")
    att.data.foreach_set("value", alpha.astype(np.float32))
    me.materials.append(_flat_material(f"Border {name}", colour, see_through=False))
    me.materials.append(_flat_material(f"Border ring {name}", colour, see_through=True))
    ring = np.array([int((alpha[q] < 1.0).any()) for q in quads], np.int32)      # the faces that fade out
    me.polygons.foreach_set("material_index", ring)
    obj = bpy.data.objects.new(f"{name} border", me)
    coll.objects.link(obj)
    _mark(obj, "border")
    obj["ic_border_colour"] = [float(v) for v in colour]
    return obj


# ---------------------------------------------------------------------------------------------------- the ground
def _ground_group(name: str, image, mv, look, colour):
    """The node group every terrain material of a map shares: the ground picture with the detail blended over it, darkened under
    water, and how strongly the border colour covers it near the map's edge."""
    tree = bpy.data.node_groups.new(f"IC ground {name}", "ShaderNodeTree")
    face = tree.interface
    face.new_socket("Detail", in_out="INPUT", socket_type="NodeSocketColor")
    face.new_socket("Detail Alpha", in_out="INPUT", socket_type="NodeSocketFloat")
    face.new_socket("Colour", in_out="OUTPUT", socket_type="NodeSocketColor")
    face.new_socket("Ocean", in_out="OUTPUT", socket_type="NodeSocketFloat")
    face.new_socket("Ocean Colour", in_out="OUTPUT", socket_type="NodeSocketColor")
    nt = tree
    inp, out = _node(nt, "NodeGroupInput"), _node(nt, "NodeGroupOutput")
    co = _node(nt, "ShaderNodeTexCoord")
    xyz = _node(nt, "ShaderNodeSeparateXYZ")
    nt.links.new(co.outputs["Object"], xyz.inputs["Vector"])
    half, w, h = _half(mv), float(image.size[0]), float(image.size[1])
    # the picture's texels sit on fraction x (size - 1); u runs with the game's x = -Blender x, v with the game's z = -Blender y,
    # and Blender's v = 1 - that
    u = _math(nt, "ADD", _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", half, xyz.outputs["X"]), (w - 1) / w / (2 * half)), 0.5 / w)
    gz = _math(nt, "SUBTRACT", half, xyz.outputs["Y"])
    v = _math(nt, "ADD", _math(nt, "MULTIPLY", gz, (h - 1) / h / (2 * half)), 0.5 / h)
    base = _image_node(nt, image, _combine(nt, u, _math(nt, "SUBTRACT", 1.0, v)), "IC ground picture")
    base_lin = _node(nt, "ShaderNodeGamma")
    nt.links.new(base.outputs["Color"], base_lin.inputs["Color"])
    base_lin.inputs["Gamma"].default_value = GAMMA
    # the game does not light the detail by the sun's colour: divided out here, the sun lamp puts it back
    detail = _vector(nt, "DIVIDE", inp.outputs["Detail"], (1.0, 1.0, 1.0), "IC detail by sun")
    detail = _vector(nt, "MINIMUM", detail.outputs["Vector"], (1.0, 1.0, 1.0))
    cam = _node(nt, "ShaderNodeCameraData")
    near = _math(nt, "LESS_THAN", cam.outputs["View Distance"], DETAIL_VIEW_RANGE)
    blend = _node(nt, "ShaderNodeMix", data_type="RGBA")
    nt.links.new(_math(nt, "MULTIPLY", inp.outputs["Detail Alpha"], near), blend.inputs[0])
    nt.links.new(base_lin.outputs["Color"], blend.inputs[6])
    nt.links.new(detail.outputs["Vector"], blend.inputs[7])
    # under water the game takes the sun away over 15 m of depth: multiply by ((ambient + sun x k) / (ambient + sun))
    depth = _math(nt, "SUBTRACT", mv.water_height, xyz.outputs["Z"])
    k = _math(nt, "SUBTRACT", 1.0, _math(nt, "DIVIDE", depth, _gl().UNDERWATER_DEPTH, clamp=True), clamp=True)
    lit = _node(nt, "ShaderNodeVectorMath", "IC water sun", operation="SCALE")
    nt.links.new(k, lit.inputs["Scale"])
    lit = _vector(nt, "ADD", lit.outputs["Vector"], (0.5, 0.5, 0.5), "IC water ambient")
    ratio = _vector(nt, "DIVIDE", lit.outputs["Vector"], (1.0, 1.0, 1.0), "IC water light")
    ratio_lin = _node(nt, "ShaderNodeGamma")
    nt.links.new(ratio.outputs["Vector"], ratio_lin.inputs["Color"])
    ratio_lin.inputs["Gamma"].default_value = GAMMA
    final = _vector(nt, "MULTIPLY", blend.outputs[2], ratio_lin.outputs["Color"])
    nt.links.new(final.outputs["Vector"], out.inputs["Colour"])
    # the ocean blend: u = 1 - (distance inside the edge) / blend, alpha = (4u - 0.5) / 3
    if mv.ocean_blend:
        dx = _math(nt, "SUBTRACT", half, _math(nt, "ABSOLUTE", xyz.outputs["X"]))
        dz = _math(nt, "SUBTRACT", half, _math(nt, "ABSOLUTE", xyz.outputs["Y"]))
        edge = _math(nt, "MINIMUM", dx, dz)
        t = _math(nt, "SUBTRACT", 1.0, _math(nt, "DIVIDE", edge, mv.ocean_blend))
        alpha = _math(nt, "DIVIDE", _math(nt, "SUBTRACT", _math(nt, "MULTIPLY", t, 4.0), 0.5), 3.0, clamp=True)
        nt.links.new(alpha, out.inputs["Ocean"])
    colour_node = _node(nt, "ShaderNodeRGB", "IC ocean colour")
    nt.links.new(colour_node.outputs["Color"], out.inputs["Ocean Colour"])
    _light_ground(tree, look, colour)
    return tree


def _light_ground(tree, look, colour) -> None:
    """The values of the ground group that follow the map's light: the sun's colour, the ambient light and the border colour."""
    sun = [max(float(c), 0.05) for c in look.sun_colour]
    ambient = [float(c) for c in look.ambient]
    tree.nodes["IC detail by sun"].inputs[1].default_value = tuple(float(v) for v in srgb_to_linear(np.clip(sun, 0, 1)))
    tree.nodes["IC water sun"].inputs[0].default_value = tuple(sun)
    tree.nodes["IC water ambient"].inputs[1].default_value = tuple(ambient)
    tree.nodes["IC water light"].inputs[1].default_value = tuple(max(a + s, 0.01) for a, s in zip(ambient, sun))
    tree.nodes["IC ocean colour"].outputs[0].default_value = (*_lin(colour), 1.0)


def _terrain_materials(top) -> list:
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    terrain = X.terrain_of(top)
    return [m for m in terrain.data.materials if m is not None] if terrain is not None else []


def _ground_group_of(top):
    for m in _terrain_materials(top):
        if m.get(KEY_GROUND_NODES):
            return m.node_tree.nodes["IC game ground"].node_tree
    return None


def dress_ground_material(mat, tree) -> None:
    """Show a terrain material's texture the game's way: through the map's ground group."""
    if mat.get(KEY_GROUND_NODES):
        return
    bsdf = M._bsdf(mat)
    if bsdf is None:
        return
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    detail = bsdf.inputs["Base Color"].links[0].from_node if bsdf.inputs["Base Color"].links else None
    group = _node(nt, "ShaderNodeGroup", "IC game ground")
    group.node_tree = tree
    group.location = (-200, -200)
    if detail is not None and detail.type == "TEX_IMAGE":
        mat["ic_detail_node"] = detail.name
        detail.image.alpha_mode = "CHANNEL_PACKED"              # its alpha is a blend weight, not see-through-ness
        nt.links.new(detail.outputs["Color"], group.inputs["Detail"])
        nt.links.new(detail.outputs["Alpha"], group.inputs["Detail Alpha"])
    nt.links.new(group.outputs["Colour"], bsdf.inputs["Base Color"])
    edge = _node(nt, "ShaderNodeEmission", "IC ocean emission")
    nt.links.new(group.outputs["Ocean Colour"], edge.inputs["Color"])
    mix = _node(nt, "ShaderNodeMixShader", "IC ocean blend")
    nt.links.new(group.outputs["Ocean"], mix.inputs["Fac"])
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(edge.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    spec = bsdf.inputs.get("Specular IOR Level")
    if spec is not None:
        mat["ic_specular_before"] = float(spec.default_value)
        spec.default_value = 0.0                               # the game's ground has no shine
    mat[KEY_GROUND_NODES] = True


def dress_new_ground(top, mat) -> None:
    """A ground texture added while the look is on is shown like the others."""
    tree = _ground_group_of(top)
    if tree is not None:
        dress_ground_material(mat, tree)


def undress_ground(top) -> None:
    trees = set()
    for mat in _terrain_materials(top):
        if not mat.get(KEY_GROUND_NODES):
            continue
        nt = mat.node_tree
        bsdf = M._bsdf(mat)
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        detail = nt.nodes.get(str(mat.get("ic_detail_node", "")))
        for name in ("IC game ground", "IC ocean emission", "IC ocean blend"):
            n = nt.nodes.get(name)
            if n is not None:
                if n.type == "GROUP":
                    trees.add(n.node_tree)
                nt.nodes.remove(n)
        if detail is not None:
            nt.links.new(detail.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        spec = bsdf.inputs.get("Specular IOR Level")
        if spec is not None and "ic_specular_before" in mat:
            spec.default_value = float(mat["ic_specular_before"])
        for key in (KEY_GROUND_NODES, "ic_detail_node", "ic_specular_before"):
            if key in mat:
                del mat[key]
    for tree in trees:
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)


# ---------------------------------------------------------------------------------------------------- decals
def _decal_image(data, d, tmp):
    return _picture(data, d.ref, tmp, "Decal")


def _decal_material(img, key: str):
    mat = bpy.data.materials.new(f"Decal {key}")
    bsdf = M._bsdf(mat)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.extension = "EXTEND"
    tex.location = (-400, 200)
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = "Decal"
    uv.location = (-650, 200)
    links.new(uv.outputs["UV"], tex.inputs["Vector"])
    if bsdf is not None:
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 1.0
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    nodes.active = tex
    M._set_blend(mat, "BLEND")
    try:
        mat.use_backface_culling = True
    except Exception:
        pass
    return mat


def decal_mesh(mv, d, width_px: int, height_px: int, lift: float = 0.0):
    """One decal on the ground cells it covers, as the game builds it (``DecalObj::CreateDecalMesh``): the terrain's own points, so it
    lies exactly on every slope, raised ``lift`` metres along the ground's normal. ``(vertices (n, 3) in the game's axes, quads (m, 4),
    uv (n, 2) in Blender's sense)``. A texture coordinate is the offset from the decal's middle along its axes / its full size + 0.5;
    outside the picture the game repeats the edge. Empty arrays for a decal that is off the map or has no size."""
    empty = np.zeros((0, 3)), np.zeros((0, 4), np.int32), np.zeros((0, 2))
    hu, hv = d.half_size(width_px, height_px)
    wide, high = 2.0 * hu, 2.0 * hv
    c, s = math.cos(d.rotation), math.sin(d.rotation)
    ts = mv.tile_scale
    half_x, half_z = (mv.width - 1) * ts / 2.0, (mv.height - 1) * ts / 2.0
    ex, ez = (abs(wide * c) + abs(high * s)) / 2.0, (abs(wide * s) + abs(high * c)) / 2.0
    if not wide or not high or d.x + ex < -half_x or d.x - ex > half_x or d.z + ez < -half_z or d.z - ez > half_z:
        return empty

    def cell(value, half, points):
        return int(min(max(math.floor((value + half) / ts), 0), points - 2))

    x0, x1 = cell(d.x - ex, half_x, mv.width), cell(d.x + ex, half_x, mv.width)
    z0, z1 = cell(d.z - ez, half_z, mv.height), cell(d.z + ez, half_z, mv.height)
    ix, iz = np.meshgrid(np.arange(x0, x1 + 2), np.arange(z0, z1 + 2))
    x, z = ix * ts - half_x, iz * ts - half_z
    ground = mv.heights.astype(np.float64) * mv.height_scale
    verts = np.stack([x, ground[iz, ix], z], -1)
    if lift:
        gz, gx = np.gradient(ground, ts)
        normal = np.stack([-gx[iz, ix], np.ones_like(x), -gz[iz, ix]], -1)
        verts = verts + lift * normal / np.linalg.norm(normal, axis=-1, keepdims=True)
    dx, dz = x - d.x, z - d.z
    uv = np.stack([(dx * c + dz * s) / wide + 0.5, 1.0 - ((-dx * s + dz * c) / high + 0.5)], -1)
    nx, nz = x1 - x0 + 2, z1 - z0 + 2
    j, i = np.meshgrid(np.arange(nz - 1), np.arange(nx - 1), indexing="ij")
    v00 = (j * nx + i).reshape(-1)
    quads = np.stack([v00, v00 + nx, v00 + nx + 1, v00 + 1], -1).astype(np.int32)       # the terrain's own cell order
    return verts.reshape(-1, 3), quads, uv.reshape(-1, 2)


def _decals(context, coll, name, mv, look, data, tmp):
    core = _addon("core")
    by_tex: dict = {}
    for d in look.decals:
        by_tex.setdefault(os.path.basename(d.texture).lower(), []).append(d)
    made, missing = [], []
    for key in sorted(by_tex):
        group = by_tex[key]
        img = _decal_image(data, group[0], tmp)
        if img is None:
            missing.append(key)
            continue
        w, h = int(img.size[0]), int(img.size[1])
        V, Q, UV, ID = [], [], [], []
        base = 0
        lift = DECAL_LIFT + DECAL_LIFT_STEP * len(made)        # each picture a hair above the one before
        for n, d in enumerate(group):
            v, q, uv = decal_mesh(mv, d, w, h, lift)
            V.append(v)
            Q.append(q + base)
            UV.append(uv)
            ID.append(np.full(len(q), n, dtype=np.int32))
            base += len(v)
        V, Q, UV, ID = np.concatenate(V), np.concatenate(Q).astype(np.int32), np.concatenate(UV), np.concatenate(ID)
        vb = core.sgm_to_blender(V)
        flip = np.cross(vb[Q[:, 1]] - vb[Q[:, 0]], vb[Q[:, 2]] - vb[Q[:, 1]])[:, 2] < 0     # every face looks up
        Q[flip] = Q[flip][:, ::-1]
        me = M._mesh(f"{name} decal {key}", vb, Q, game_quads=False)
        uvl = me.uv_layers.new(name="Decal")
        uvl.data.foreach_set("uv", M._loop_values(me, UV).astype(np.float32).ravel())
        fa = me.attributes.new("ic_decal_index", "INT", "FACE")
        fa.data.foreach_set("value", ID)
        me.materials.append(_decal_material(img, key))
        obj = bpy.data.objects.new(f"{name} decal {key}", me)
        coll.objects.link(obj)
        _mark(obj, "decals")
        obj["ic_decal_picture"] = key
        obj[KEY_DECALS] = len(group)
        obj["ic_decal_positions"] = [[float(d.x), float(d.z), float(d.scale), float(d.rotation)] for d in group]
        made.append(obj)
    return made, missing


# ---------------------------------------------------------------------------------------------------- the water surface
def water_surface(mv, heights_m: np.ndarray, level_m: float):
    """The water surface over the ground as it is: ``(vertices (n, 3) in the game's axes, quads (m, 4), grid ids (n,), depth (n,))``.

    ``heights_m`` is the ground's height at every point ``(height, width)``, ``level_m`` the water level. A point follows the level where the
    map's water plane is at the map's own level; a point the plane holds at another height (a pond or a bog made at its own height) keeps that
    height, as the save does. The surface covers every cell with a corner under water."""
    plane = mv.water.astype(np.float64) * mv.height_scale
    if mv.water.any():
        own = mv.water.astype(np.int64) != int(mv.water_level)       # a point the plane holds at another height keeps it (a zero stays dry)
        surface = np.where(own, plane, level_m)
    else:
        surface = np.full(plane.shape, float(level_m))               # a map with no water plane: the level over the whole map
    wet = surface > heights_m
    wc = wet[:-1, :-1] | wet[:-1, 1:] | wet[1:, :-1] | wet[1:, 1:]
    cells = wc.reshape(-1)
    if not cells.any():
        return np.zeros((0, 3)), np.zeros((0, 4), np.int32), np.zeros(0, np.int64), np.zeros(0)
    q = mv.quads()[cells]
    used, inverse = np.unique(q.reshape(-1), return_inverse=True)
    pos = mv.vertex_positions(np.float64)[used]
    pos[:, 1] = surface.reshape(-1)[used]
    depth = np.clip(surface.reshape(-1)[used] - heights_m.reshape(-1)[used], 0.0, None)
    return pos, inverse.reshape(-1, 4).astype(np.int32), used.astype(np.int64), depth


def _set_water_mesh(obj, mv, pos, quads, ids, depth, look):
    """Fill the water object's mesh from a surface (replacing what it had)."""
    core = _addon("core")
    old = obj.data
    me = M._mesh(obj.data.name if obj.data else "water", core.sgm_to_blender(pos), quads)
    attr = me.attributes.new("ic_water_depth", "FLOAT", "POINT")
    attr.data.foreach_set("value", depth.astype(np.float32))
    for mat in (old.materials if old is not None else ()):
        me.materials.append(mat)
    obj.data = me
    obj.matrix_world = obj.matrix_world.Identity(4)
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    if look is not None and KEY_PLAIN_WATER in obj:
        grey_water(obj, mv, look)


def _ground_heights(top):
    """``(terrain object, ground heights in metres (height, width))`` read from the terrain object as it is now, in the game's units (the
    object's own transform included), or ``(None, None)``."""
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    terrain = X.terrain_of(top)
    if terrain is None:
        return None, None
    w, h = int(terrain["ic_map_width"]), int(terrain["ic_map_height"])
    me = terrain.data
    if len(me.vertices) != w * h:
        return terrain, None
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mw = np.array(terrain.matrix_world, dtype=np.float64)
    z = co @ mw[2, :3] + mw[2, 3]                          # Blender z is the game's height
    return terrain, z.reshape(h, w)


def current_water_level(top, mv) -> float:
    """The water level in metres as the scene has it now: the map's own level moved by how far the water object has moved (the way the
    save reads it); the map's level when there is no water object."""
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    water = X.water_of(top)
    level = mv.water_height
    if water is None:
        return level
    if water.get("ic_look_level_m") is not None:      # drawn again (or added) at this level; the transform was reset then
        return float(water["ic_look_level_m"]) + float(water.matrix_world.translation.z)
    wpos, _q, _v = mv.water_mesh(np.float64)
    me = water.data
    if not len(me.vertices) or not len(wpos):
        return level
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mw = np.array(water.matrix_world, dtype=np.float64)
    z = co @ mw[2, :3] + mw[2, 3]
    return float(level + np.median(z) - np.median(wpos[:, 1]))


def redraw_water(context, top, mv, *, look=None, level_m: float | None = None) -> dict:
    """Rebuild the water surface of the map from the ground and the water level as they are now. Returns ``{"cells", "level_m", "seconds"}``.

    Raises ``ValueError`` (plain words) when the map has no water object (use :func:`add_water`) or no terrain."""
    t0 = time.perf_counter()
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    water = X.water_of(top)
    if water is None:
        raise ValueError("This map has no water surface to redraw. Use \"Add water\" to give it one.")
    terrain, heights = _ground_heights(top)
    if heights is None:
        raise ValueError("The terrain of this map is not in the scene as it was imported (its points were added or removed), so the water "
                         "cannot be redrawn from it.")
    level = current_water_level(top, mv) if level_m is None else float(level_m)
    pos, quads, ids, depth = water_surface(mv, heights, level)
    if look is None:
        look = mv.look()
    _set_water_mesh(water, mv, pos, quads, ids, depth, look)
    water["ic_look_level_m"] = level
    return {"cells": int(len(quads)), "level_m": level, "seconds": round(time.perf_counter() - t0, 3)}


def add_water(context, top, mv, level_m: float | None = None, *, look=None) -> dict:
    """Give a map that has no water surface one at ``level_m`` (default the map's own water level): the surface over the ground that is lower
    than the level, in the map's ``<name> water`` collection, marked as the map's water so that the save reads its height. Saving the map
    writes that height as the map's water level. Raises ``ValueError`` when the map already has a water surface."""
    t0 = time.perf_counter()
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    if X.water_of(top) is not None:
        raise ValueError("This map already has a water surface. Move it up or down to change the water level.")
    terrain, heights = _ground_heights(top)
    if heights is None:
        raise ValueError("The terrain of this map is not in the scene as it was imported, so water cannot be added to it.")
    level = mv.water_height if level_m is None else float(level_m)
    pos, quads, ids, depth = water_surface(mv, heights, level)
    if look is None:
        look = mv.look()
    name = _name(top)
    me = bpy.data.meshes.new(f"{name} water")
    me.materials.append(M._water_material(mv.board))
    obj = bpy.data.objects.new(f"{name} water", me)
    M.stamp(obj)
    _set_water_mesh(obj, mv, pos, quads, ids, depth, look)
    own = next((c for c in top.children if c.name.endswith(" water")), None) or M._new_collection(f"{name} water", top)
    own.objects.link(obj)
    sea = next((o for o in top.all_objects if o.get(KEY_PIECE) == "sea"), None)
    if sea is not None:                                      # the look is on: the new water wears the game's water too
        dress_water(top, obj, mv, look, sea.data.materials[0])
    obj["ic_water_level"] = int(round(level / mv.height_scale))
    obj["ic_look_level_m"] = level
    return {"cells": int(len(quads)), "level_m": level, "seconds": round(time.perf_counter() - t0, 3)}


# ---------------------------------------------------------------------------------------------------- the whole look
def build_look(context, top, mv, *, game_dir: str | None = None, data=None, tmp: str | None = None,
               sun_and_sky: bool = True, sea: bool = True, decals: bool = True, ground: bool = True, look=None) -> dict:
    """Give the imported map ``top`` (its collection) the look of the game, from ``mv`` (``ICParse.sgb.view.MapView``). Returns the report
    (also on the collection as JSON in ``ic_map_look``). Any older look of the map is removed first. Leaves nothing half-built on a fault."""
    t0 = time.perf_counter()
    view = M._view()
    look = look if look is not None else mv.look()           # ``look``: the map's look with the values the Terrain look panel holds now
    name = _name(top)
    own_tmp = tmp is None
    tmp = tmp or tempfile.mkdtemp(prefix="ic_look_")
    if data is None and game_dir:
        data = view.game_data(game_dir, mv.archive)
    remove_look(top)
    root = M._new_collection(f"{name} look", top)
    report = {"sun": None, "world": None, "sky_picture": look.sky_texture, "sky_loaded": False, "fog": bool(look.fog_on),
              "fog_start_m": look.fog_start, "fog_end_m": look.fog_end, "sea": False, "decals": 0, "decal_pictures": 0,
              "decal_pictures_missing": [], "ground_picture": False, "game_water": False, "problems": list(look.problems),
              "seconds": 0.0}
    try:
        top[KEY_VIEW_BEFORE] = context.scene.view_settings.view_transform
        try:
            context.scene.view_settings.view_transform = "Standard"
        except TypeError:
            pass
        P = _paint()
        image = base = None
        try:
            image = P.ground_image(context, top)
            base = P.ground_pixels(image)
        except Exception as exc:                          # noqa: BLE001   a map without its picture shows the detail textures
            report["problems"].append(f"The ground shows only its detail textures: {exc}")
        colour = _border_colour(look, base)
        if sun_and_sky:
            coll = _sub(root, f"{name} look sun and sky")
            sun = _sun(context, coll, name, look)
            report["sun"] = {"object": sun.name, "direction": list(look.sun_direction), "colour": list(look.sun_colour),
                             "elevation_degrees": round(look.sun_elevation_degrees, 1)}
            world = _world(context, name, look)
            context.scene.world = world
            report["world"] = world.name
            sky = _sky_dome(context, coll, name, look, data, tmp)
            report["sky_loaded"] = bool(sky["ic_sky_picture_loaded"])
            if not report["sky_loaded"]:
                report["problems"].append(f"The sky picture {look.sky_texture or '(none)'} could not be loaded: the sky shows the map's ambient colour.")
        if ground and image is not None:
            tree = _ground_group(name, image, mv, look, colour)
            for mat in _terrain_materials(top):
                dress_ground_material(mat, tree)
            report["ground_picture"] = True
        if sea:
            coll = _sub(root, f"{name} look sea")
            sky_img, ripple_img = _water_pictures(top, look, data, tmp)
            if ripple_img is None:
                report["problems"].append("The water's ripple picture is not in the game's files: the water is smooth.")
            material = _water_game_material(f"Game water {name}", sky_img, ripple_img, _floor_colour(mv, look, base))
            _sea(context, coll, name, mv, look, mv.water_height, material)
            _border(_sub(root, f"{name} look border"), name, mv, mv.water_height, colour)
            report["sea"] = True
            X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
            water = X.water_of(top)
            if water is not None:
                dress_water(top, water, mv, look, material)
                report["game_water"] = True
        if decals and look.decals:
            coll = _sub(root, f"{name} look decals")
            made, missing = _decals(context, coll, name, mv, look, data, tmp)
            report["decals"] = int(sum(o[KEY_DECALS] for o in made))
            report["decal_pictures"] = len(made)
            report["decal_pictures_missing"] = missing
            if missing:
                report["problems"].append(f"{len(missing)} decal pictures are not in the game's files: {', '.join(missing[:5])}"
                                          + (" ..." if len(missing) > 5 else ""))
        view_far = (view.SKY_RADIUS * 1.1 + max(mv.world_size)) * 1.2
        report["view_distance_m"] = (_fit_far(view_far) or [None])[0]
    except Exception:
        remove_look(top)
        raise
    finally:
        if own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    report["seconds"] = round(time.perf_counter() - t0, 2)
    top[KEY_LOOK] = json.dumps(report)
    return report


def _fit_far(metres: float) -> list:
    changed = []
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.clip_end < metres:
                    space.clip_end = metres
                    changed.append(metres)
    return changed


# ---------------------------------------------------------------------------------------------------- operators
def _active_map(context):
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    return X, X.active_map(context)


class BZ_OT_map_redraw_water(bpy.types.Operator):
    """Draw the water surface again from the ground and the water level as they are now"""
    bl_idname = "bz.map_redraw_water"
    bl_label = "Redraw Water"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(c.get(M.KEY_MAP) for c in context.scene.collection.children_recursive)

    def execute(self, context):
        X, top = _active_map(context)
        try:
            _raw, mv, _gm = X.read_source(context, top)
            res = redraw_water(context, top, mv)
        except Exception as exc:                           # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"The water is drawn again over {res['cells']} cells, at {res['level_m']:g} m.")
        return {"FINISHED"}


class BZ_OT_map_add_water(bpy.types.Operator):
    """Add a water surface to the map; saving writes the water level"""
    bl_idname = "bz.map_add_water"
    bl_label = "Add Water"
    bl_options = {"REGISTER", "UNDO"}

    level: FloatProperty(name="Water level (m)", default=-1.0, min=-1.0, soft_max=200.0,
                         description="Water height in metres; below 0 uses the map's own water level")

    @classmethod
    def poll(cls, context):
        return any(c.get(M.KEY_MAP) for c in context.scene.collection.children_recursive)

    def execute(self, context):
        X, top = _active_map(context)
        try:
            _raw, mv, _gm = X.read_source(context, top)
            res = add_water(context, top, mv, None if self.level < 0 else self.level)
        except Exception as exc:                           # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Water added at {res['level_m']:g} m over {res['cells']} cells. Saving the map writes this water level.")
        return {"FINISHED"}


class BZ_OT_map_game_look(bpy.types.Operator):
    """Give the map the game's look: ground, sky, light, fog, water and decals"""
    bl_idname = "bz.map_game_look"
    bl_label = "Show Game Look"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(c.get(M.KEY_MAP) for c in context.scene.collection.children_recursive)

    def execute(self, context):
        X, top = _active_map(context)
        try:
            _raw, mv, _gm = X.read_source(context, top)
            from . import prefs
            rep = build_look(context, top, mv, game_dir=prefs.resolve_game_dir(context)[0])
        except Exception as exc:                           # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        for line in rep["problems"]:
            self.report({"WARNING"}, line)
        self.report({"INFO"}, f"The game look is on: {'the ground picture, ' if rep['ground_picture'] else ''}{rep['decals']} decals, "
                              f"{'fog, ' if rep['fog'] else ''}the sun, the sky and the water.")
        return {"FINISHED"}


class BZ_OT_map_remove_look(bpy.types.Operator):
    """Take the game look off the map (the ground, the water and the placed objects stay)"""
    bl_idname = "bz.map_remove_look"
    bl_label = "Remove Game Look"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(c.get(M.KEY_MAP) and look_collection(c) is not None for c in context.scene.collection.children_recursive)

    def execute(self, context):
        X, top = _active_map(context)
        if look_collection(top) is None:
            self.report({"ERROR"}, "This map has no game look to remove.")
            return {"CANCELLED"}
        remove_look(top)
        self.report({"INFO"}, "The game look is removed.")
        return {"FINISHED"}


OPERATORS = (BZ_OT_map_redraw_water, BZ_OT_map_add_water, BZ_OT_map_game_look, BZ_OT_map_remove_look)
CLASSES = OPERATORS      # the package registry registers them (this module is found by its map_ name); ops_map_import.register() adds the hooks


# ---------------------------------------------------------------------------------------------------- automatic redraw
_BUSY = [False]
AUTO_REDRAW = [True]


@bpy.app.handlers.persistent
def auto_redraw(scene, depsgraph=None) -> int:
    """After a ground edit (the terrain object's geometry changed while in Object Mode), draw the water again, on maps small enough for that
    to be quick (up to 257 x 257 points). Returns how many maps were redrawn. Never raises."""
    if _BUSY[0] or not AUTO_REDRAW[0]:
        return 0
    try:
        if depsgraph is None:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        if bpy.context.mode != "OBJECT":
            return 0
        changed = {u.id.original for u in depsgraph.updates if isinstance(u.id, bpy.types.Object) and u.is_updated_geometry}
    except Exception:
        return 0
    n = 0
    _BUSY[0] = True
    try:
        for top in (c for c in scene.collection.children_recursive if c.get(M.KEY_MAP)):
            terrain = next((o for o in top.all_objects if o.get(M.KEY_MAP) and o.type == "MESH"), None)
            if terrain is None or terrain not in changed or len(terrain.data.vertices) > AUTO_REDRAW_MAX_POINTS:
                continue
            X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
            if X.water_of(top) is None:
                continue
            try:
                _raw, mv, _gm = X.read_source(bpy.context, top)
                redraw_water(bpy.context, top, mv)
                n += 1
            except Exception:
                continue
    finally:
        _BUSY[0] = False
    return n


def install_hooks():
    """Start the automatic redraw (called by the integrator's ``register()``)."""
    if auto_redraw not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(auto_redraw)


def remove_hooks():
    while auto_redraw in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_redraw)


# ---------------------------------------------------------------------------------------------------- the look, editable (Terrain Properties)
# The Mission Editor's "Terrain Properties": the values the map file holds for light, sky, sea, fog and the ground's detail. They sit in a
# property group on the map's collection (``Collection.ic_look``), are filled from the file when the map opens, change the look objects
# at once (colours, the sun, the fog, the sea; a new sky picture builds the look again) and are written by a save step that touches only
# what differs from the map as it was opened (ICParse.sgb.look_edit): untouched, the map is saved byte for byte.
import dataclasses
import types

from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from . import map_hooks as H
from .. import enumitems


def _le():
    from ICParse.sgb import look_edit
    return look_edit


_FILLING = [False]
_SKY_NAMES: list = []            # the sky pictures the game has (filled when a map is opened)
_SKY_ITEMS: list = []            # what the enum shows: kept alive here (Blender keeps no copy of a dynamic item list)
SAVE_TOLERANCE = 1e-4


@enumitems.stable
def _sky_items(self, context):
    names = {n.lower(): n for n in _SKY_NAMES}
    cur = self.sky_current
    if cur:
        names[cur.lower()] = cur                          # the map's own spelling of its sky (stock maps write SKY_M02.TGA, mpsky_01.tga ...)
    _SKY_ITEMS[:] = [(n, n, "A sky picture of the game's data:islands folder") for _k, n in sorted(names.items())] or [("", "(none)", "")]
    return _SKY_ITEMS


def _u(key: str):
    def update(self, context):
        if _FILLING[0]:
            return
        if key == "ocean_colour":
            self.ocean_touched = True
        try:
            apply_live(bpy.context, self.id_data, self, key)
        except Exception as exc:                          # noqa: BLE001   a look that cannot be redrawn must never stop an edit
            print(f"map_look: the look could not be updated for {key}: {exc}")
    return update


class BZ_PG_map_look(PropertyGroup):
    """The map's Terrain Properties as Blender shows them (``Collection.ic_look``)."""
    ready: BoolProperty(default=False)
    has_fog: BoolProperty(default=True)
    has_water_alphas: BoolProperty(default=True)
    has_ocean_record: BoolProperty(default=True)
    ocean_touched: BoolProperty(default=False)
    sky_current: StringProperty(default="")
    sun_colour: FloatVectorProperty(name="Sunlight colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.75, 0.75, 0.75),
                                    description="The colour of the sun's light", update=_u("sun_colour"))
    shadow_colour: FloatVectorProperty(name="Shadow colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.25, 0.25, 0.25),
                                       description="Colour of the light in shadow; keep it darker than the sunlight", update=_u("shadow_colour"))
    time_hour: FloatProperty(name="Time of day", min=0.0, max=24.0, soft_min=6.0, soft_max=18.0, default=12.0, precision=2,
                             description="Sun position: 6 is one horizon, 12 overhead, 18 the other", update=_u("time_hour"))
    sky: EnumProperty(name="Sky", items=_sky_items, description="The sky picture (from the game's data:islands folder)", update=_u("sky"))
    ocean_colour: FloatVectorProperty(name="Ocean colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.0, 0.5, 0.75),
                                      description="Saved for the Mission Editor; the game ignores it", update=_u("ocean_colour"))
    ocean_min: IntProperty(name="Ocean opacity, min", min=0, max=255, default=192, description="How bright the water's reflection is in deep "
                           "water near the map (0 to 255)", update=_u("ocean_min"))
    ocean_max: IntProperty(name="Ocean opacity, max", min=0, max=255, default=255, description="How bright the water's reflection is far out "
                           "(0 to 255)", update=_u("ocean_max"))
    shore: IntProperty(name="Shore opacity", min=0, max=255, default=32, description="How bright the water's reflection is where it touches "
                       "the shore (0 to 255)", update=_u("shore"))
    fog_on: BoolProperty(name="Fog", default=False, description="Draw fog in the game", update=_u("fog_on"))
    fog_start: FloatProperty(name="Fog start", min=0.0, soft_max=4000.0, default=100.0, unit="LENGTH", description="Metres from the camera "
                             "where the fog begins", update=_u("fog_start"))
    fog_end: FloatProperty(name="Fog end", min=0.0, soft_max=4000.0, default=600.0, unit="LENGTH", description="Metres from the camera where "
                           "the fog is thickest", update=_u("fog_end"))
    fog_colour: FloatVectorProperty(name="Fog colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(0.5, 0.5, 0.75),
                                    update=_u("fog_colour"))
    detail_tiling: IntProperty(name="Detail texture repeat", min=1, max=16, default=3, description="How often the detail texture repeats "
                                                                                                   "across each patch of ground",
                               update=_u("detail_tiling"))


def group_values(g) -> dict:
    """The group's values as the keys ``look_edit.apply`` takes."""
    le = _le()
    out = {"sun_colour": tuple(g.sun_colour), "shadow_colour": tuple(g.shadow_colour), "time_angle": le.angle_of_hour(g.time_hour),
           "sky_texture": g.sky or g.sky_current, "detail_tiling": int(g.detail_tiling)}
    if g.has_ocean_record or g.ocean_touched:
        out["ocean_colour"] = tuple(g.ocean_colour)
    if g.has_water_alphas:
        out.update(ocean_min_alpha=int(g.ocean_min), ocean_max_alpha=int(g.ocean_max), shore_alpha=int(g.shore))
    if g.has_fog:
        out.update(fog_on=bool(g.fog_on), fog_start=float(g.fog_start), fog_end=float(g.fog_end), fog_colour=tuple(g.fog_colour))
    return out


def fill_group(g, vals: dict, data=None) -> None:
    """Fill the group from ``look_edit.values_of(map bytes)`` (a value the map does not hold keeps the group's default)."""
    le = _le()
    if data is not None:
        _SKY_NAMES[:] = le.sky_choices(data)
    _FILLING[0] = True
    try:
        g.sky_current = vals.get("sky_texture", "")
        if "sun_colour" in vals:
            g.sun_colour, g.shadow_colour = vals["sun_colour"], vals["shadow_colour"]
            g.time_hour = max(0.0, min(24.0, le.hour_of_angle(vals["time_angle"])))
        if vals.get("sky_texture"):
            g.sky = vals["sky_texture"]
        g.has_ocean_record = "ocean_colour" in vals
        g.ocean_touched = False
        if "ocean_colour" in vals:
            g.ocean_colour = vals["ocean_colour"]
        g.has_water_alphas = "ocean_min_alpha" in vals
        if g.has_water_alphas:
            g.ocean_min, g.ocean_max, g.shore = vals["ocean_min_alpha"], vals["ocean_max_alpha"], vals["shore_alpha"]
        g.has_fog = "fog_on" in vals
        if g.has_fog:
            g.fog_on, g.fog_start, g.fog_end, g.fog_colour = vals["fog_on"], vals["fog_start"], vals["fog_end"], vals["fog_colour"]
            if not vals["fog_on"]:                    # a map with no fog holds start 1, end 0: show something sensible to switch on
                g.fog_start, g.fog_end = 100.0, 600.0
        if "detail_tiling" in vals:
            g.detail_tiling = max(1, min(16, int(vals["detail_tiling"])))
        g.ready = True
    finally:
        _FILLING[0] = False


def _import_fill(ictx) -> None:
    fill_group(ictx.top.ic_look, _le().values_of(ictx.source_bytes), ictx.data)


def look_ns(mv, g) -> types.SimpleNamespace:
    """What the look builders read, with the group's values (cheap: no map data is copied)."""
    fog_on = bool(g.fog_on and g.has_fog and g.fog_end > g.fog_start >= 0.0 and g.fog_end > 0.0)
    return types.SimpleNamespace(ambient=tuple(g.shadow_colour), sun_colour=tuple(g.sun_colour), fog_on=fog_on,
                                 fog_start=float(g.fog_start), fog_end=float(g.fog_end), fog_colour=tuple(g.fog_colour),
                                 ocean_min_alpha=int(g.ocean_min), ocean_max_alpha=int(g.ocean_max), terrain_water_min_alpha=int(g.shore))


def full_look(mv, g):
    """The map's whole ``MapLook`` with the group's values in it (to build the look again)."""
    le = _le()
    base = mv.look()
    ns = look_ns(mv, g)
    d = le.direction_of_angle(le.angle_of_hour(g.time_hour))
    over = dict(ambient=ns.ambient, sun_colour=tuple(g.sun_colour), sun_direction=d, sky_texture=g.sky or g.sky_current)
    if g.has_fog:
        over.update(fog_start=ns.fog_start if ns.fog_on else base.fog_start, fog_end=ns.fog_end if ns.fog_on else base.fog_end,
                    fog_colour=ns.fog_colour)
    if g.has_water_alphas:
        over.update(ocean_min_alpha=int(g.ocean_min), ocean_max_alpha=int(g.ocean_max), terrain_water_min_alpha=int(g.shore))
    if g.has_ocean_record or g.ocean_touched:
        over["ocean_colour"] = (*g.ocean_colour, base.ocean_colour[3] if base.ocean_colour else 0.75)
    return dataclasses.replace(base, **over)


def apply_live(context, top, g, key: str) -> None:
    """Show a changed value on the look (when the map has one): cheap for colours, the sun, fog and the water; a new sky picture
    builds the look again."""
    root = look_collection(top)
    if root is None or not g.ready:
        return
    X = importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")
    le = _le()
    if key == "sky":
        _raw, mv, _gm = X.read_source(context, top)
        from . import prefs
        build_look(context, top, mv, game_dir=prefs.resolve_game_dir(context)[0], look=full_look(mv, g))
        return
    name = _name(top)
    ns = look_ns(None, g)
    if key in ("sun_colour", "time_hour"):
        for o in root.all_objects:
            if o.get(KEY_PIECE) == "sun":
                o.data.color = _lin(g.sun_colour)
                d = le.direction_of_angle(le.angle_of_hour(g.time_hour))
                core = _addon("core")
                from mathutils import Vector
                db = core.sgm_to_blender(np.asarray([d], dtype=np.float64))[0]
                o.rotation_quaternion = Vector(db.tolist()).to_track_quat("-Z", "Y")
                o["ic_sun_direction"] = [float(v) for v in d]
                o["ic_sun_colour"] = [float(v) for v in g.sun_colour]
    elif key in ("shadow_colour", "fog_on", "fog_start", "fog_end", "fog_colour"):
        world = bpy.data.worlds.get(f"Map {name} look")
        if world is not None:
            _fill_world(context, world, ns)
    elif key in ("ocean_min", "ocean_max", "shore"):
        _raw, mv, _gm = X.read_source(context, top)
        water = X.water_of(top)
        if water is not None and KEY_PLAIN_WATER in water:
            grey_water(water, mv, ns)
        for o in root.all_objects:
            if o.get(KEY_PIECE) == "sea":
                level = float(o.get("ic_sea_level_m", mv.water_height))
                _v, _q, grey = _gl().open_sea(_half(mv), mv.tile_scale, level, _alphas(ns))
                if len(grey) == len(o.data.vertices):
                    _set_grey(o.data, grey)
    if key in ("sun_colour", "shadow_colour"):                # the ground and the border follow the light
        tree = _ground_group_of(top)
        P = _paint()
        try:
            base = P.ground_pixels(P.ground_image(context, top))
        except Exception:                                     # noqa: BLE001
            base = None
        colour = _border_colour(ns, base)
        if tree is not None:
            _light_ground(tree, ns, colour)
        for o in root.all_objects:
            if o.get(KEY_PIECE) == "border":
                for mat in o.data.materials:
                    mat.node_tree.nodes["IC border colour"].inputs["Color"].default_value = (*_lin(colour), 1.0)
                o["ic_border_colour"] = [float(v) for v in colour]


def changes_of(g, original: dict) -> dict:
    """The values of the group that differ from the map as it was opened (``original`` = ``look_edit.values_of``)."""
    now = group_values(g)
    out = {}
    for k, v in now.items():
        o = original.get(k)
        if o is None:
            if k == "ocean_colour" and not g.ocean_touched:
                continue
            out[k] = v
            continue
        if isinstance(v, (tuple, list)):
            if any(abs(float(a) - float(b)) > SAVE_TOLERANCE for a, b in zip(v, o)):
                out[k] = v
        elif isinstance(v, str):
            if v.lower() != str(o).lower():
                out[k] = v
        elif isinstance(v, bool):
            if bool(o) != v:
                out[k] = v
        elif k == "time_angle":
            if abs(float(v) - float(o)) > 0.01:
                out[k] = v
        elif abs(float(v) - float(o)) > SAVE_TOLERANCE:
            out[k] = v
    if g.has_fog and not g.fog_on and not original.get("fog_on"):      # fog stays off: its start, end and colour are only the editing defaults
        for k in ("fog_start", "fog_end", "fog_colour"):
            out.pop(k, None)
    return out


def save_look(sctx) -> None:
    """Save step: write the Terrain look values that differ from the map as it was opened."""
    g = getattr(sctx.top, "ic_look", None)
    if g is None or not g.ready:
        return
    le = _le()
    changes = changes_of(g, le.values_of(sctx.original))
    if not changes:
        return
    try:
        new, lines = le.apply(sctx.sgb, **changes)
    except ValueError as exc:
        sctx.refuse(f"The Terrain look could not be saved: {exc}")
    if new != sctx.sgb:
        sctx.sgb = new
        for line in lines:
            sctx.add_change(line)


def check_look(cctx) -> list:
    """Check: the Mission Editor wants the shadow colour darker than the sunlight's in every part."""
    g = getattr(cctx.top, "ic_look", None)
    if g is None or not g.ready:
        return []
    return [H.problem(H.WARNING, line, "Terrain look") for line in _le().check_light(tuple(g.sun_colour), tuple(g.shadow_colour))]


class BZ_OT_map_look_reset(bpy.types.Operator):
    """Reset the Terrain look values to those in the map file"""
    bl_idname = "bz.map_look_reset"
    bl_label = "Reset Terrain Look"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(c.get(M.KEY_MAP) for c in context.scene.collection.children_recursive)

    def execute(self, context):
        X, top = _active_map(context)
        try:
            raw, mv, _gm = X.read_source(context, top)
            fill_group(top.ic_look, _le().values_of(raw))
            if look_collection(top) is not None:
                from . import prefs
                build_look(context, top, mv, game_dir=prefs.resolve_game_dir(context)[0])
        except Exception as exc:                           # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "The Terrain look is back to what the map holds.")
        return {"FINISHED"}


CLASSES = OPERATORS + (BZ_PG_map_look, BZ_OT_map_look_reset)

H.register_import_step("map look values", _import_fill, order=20)
H.register_save_step("map look values", save_look, order=21)
H.register_check("map look", check_look)


def register():
    bpy.types.Collection.ic_look = bpy.props.PointerProperty(type=BZ_PG_map_look)


def unregister():
    del bpy.types.Collection.ic_look
