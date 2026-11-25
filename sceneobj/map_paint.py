"""Paint passability (a picture, one pixel per path cell), terrain types (point colours), per-point water
(``ic_water_paint``, -1 = unchanged) and decals (empties); the format side is in ``ICParse.sgb``.

A save writes only what changed and updates pathfinding. Paint objects carry no ``ic_map`` / ``ic_blueprint`` /
``ic_water_level`` mark, so the core save never reads them.
"""
from __future__ import annotations

import importlib
import math

import numpy as np

import bpy

from . import map_hooks as H
from . import ops_map_import as M

KEY_PAINT = "ic_paint"                 # on a paint object: "impass", "types", "overlay"
KEY_IMAGE = "ic_paint_image"           # on a paint object: the name of its picture
PAINT_SUFFIX = " paint (not saved)"
UV_NAME = "Passability"
TYPE_ATTR = "IC terrain type"
WATER_ATTR = "ic_water_paint"
DECAL_COLLECTION = " decals"           # "<map> decals"
KEY_DECAL = "ic_decal_handle"          # on a decal handle: the decal's number in the map, -1 for a new one
KEY_DECAL_PICTURE = "ic_decal_handle_picture"
DECAL_HANDLE_PREFIX = "decal "

SAVE_ORDER = 150                       # after the map features that change the terrain's size or the map's records

# the three states of a path cell and how they are painted (straight colour; the picture's alpha marks "painted")
CLEAR, BLOCK, OPEN = 0, 1, 2
PAINT_RGBA = {CLEAR: (0.0, 0.0, 0.0, 0.0), BLOCK: (0.90, 0.10, 0.10, 1.0), OPEN: (0.10, 0.80, 0.25, 1.0)}
LIFT = {"impass": 0.10, "types": 0.14, "overlay": 0.18, "ground": 0.06}          # metres above the ground, so the pictures do not flicker through it
OPACITY = {"impass": 0.65, "types": 0.55, "overlay": 0.70, "ground": 1.0}


def _x():
    return importlib.import_module(f"{__package__}.ops_map_export")


# ---------------------------------------------------------------------------------------------------- pure helpers (no Blender)
def plane_to_rgba(plane: np.ndarray) -> np.ndarray:
    """A passability layer ``(n, n)`` (0 / 1 / 2, rows along z) as an ``(n, n, 4)`` float picture, rows along z (top row first)."""
    pic = np.zeros(plane.shape + (4,), np.float32)
    for value, rgba in PAINT_RGBA.items():
        pic[plane == value] = rgba
    return pic


def classify_rgba(pic: np.ndarray) -> np.ndarray:
    """The state of every painted pixel: clear where it is see-through (alpha under a half), else red -> blocked, green -> open."""
    a = pic[..., 3]
    return np.where(a < 0.5, CLEAR, np.where(pic[..., 0] >= pic[..., 1], BLOCK, OPEN)).astype(np.uint8)


def merge_plane(original: np.ndarray, painted: np.ndarray) -> np.ndarray:
    """The layer a save writes: the pixels whose state differs from what the original layer paints are taken from the picture, all others
    keep the original value (so a value the picture cannot show survives, and an untouched picture changes nothing)."""
    shown = np.where(np.isin(original, (CLEAR, BLOCK, OPEN)), original, CLEAR).astype(np.uint8)
    return np.where(painted != shown, painted, original).astype(np.uint8)


def srgb_to_linear(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def type_palette() -> dict:
    """``{type number: linear (r, g, b)}`` for every number a map may hold (0 to 6)."""
    from ICParse.sgb import terrain_types as T
    return {n: tuple(srgb_to_linear(np.array(T.colour_of(n)) / 255.0)) for n in range(7)}


def classify_types(rgb: np.ndarray) -> np.ndarray:
    """The nearest terrain type (by colour) of every point colour ``(n, 3)`` linear."""
    pal = type_palette()
    keys = sorted(pal)
    table = np.array([pal[k] for k in keys])
    d = ((rgb[:, None, :] - table[None, :, :]) ** 2).sum(axis=2)
    return np.array(keys, np.uint8)[d.argmin(axis=1)]


def merge_water(original: np.ndarray, painted: np.ndarray) -> np.ndarray:
    """The water plane a save writes: the painted points (>= 0) replace the map's, the others (-1) keep it."""
    return np.where(painted >= 0, np.clip(painted, 0, 255), original).astype(np.uint8)


# ---------------------------------------------------------------------------------------------------- finding things
def map_name_of(top) -> str:
    return _x().map_name(top)


def paint_collection(top, create: bool = False):
    name = f"{map_name_of(top)}{PAINT_SUFFIX}"
    own = next((c for c in top.children if c.name.endswith(PAINT_SUFFIX)), None)
    if own is None and create:
        own = M._new_collection(name, top)
    return own


def paint_object(top, kind: str):
    own = paint_collection(top)
    if own is None:
        return None
    return next((o for o in own.objects if o.get(KEY_PAINT) == kind), None)


def paint_image(top, kind: str = "impass"):
    obj = paint_object(top, kind)
    return bpy.data.images.get(obj.get(KEY_IMAGE, "")) if obj is not None else None


def terrain_grid(terrain):
    """``(width, height, tile_scale, height_scale)`` of a map's terrain object."""
    return (int(terrain["ic_map_width"]), int(terrain["ic_map_height"]), float(terrain["ic_tile_scale"]),
            float(terrain["ic_height_scale"]))


# ---------------------------------------------------------------------------------------------------- the paint objects
def _ensure_uv(terrain) -> None:
    """The terrain mesh's second UV map ``Passability``: u along x and v along z over the whole map, so a picture with one pixel per
    path cell fits it exactly (rows run down the picture as z grows, like the ground texture's)."""
    me = terrain.data
    w, h, _ts, _hs = terrain_grid(terrain)
    if UV_NAME in me.uv_layers:
        return
    previous = me.uv_layers.active.name if me.uv_layers.active else None
    layer = me.uv_layers.new(name=UV_NAME)
    idx = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", idx)
    gx, gz = idx % w, idx // w
    uv = np.stack([gx / float(w - 1), 1.0 - gz / float(h - 1)], -1).astype(np.float32)
    layer.data.foreach_set("uv", uv.ravel())
    if previous and previous in me.uv_layers:                    # the ground material names its own map; keep the ground one active
        me.uv_layers.active = me.uv_layers[previous]


def _overlay_material(name: str, *, image=None, uv: str = UV_NAME, attribute: str = "", opacity: float = 0.6, ignore_alpha: bool = False):
    """A see-through, unlit material showing a picture (nearest pixel, not smoothed) or a colour attribute over the ground."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    emit = nt.nodes.new("ShaderNodeEmission")
    scale = nt.nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY"
    scale.inputs[1].default_value = opacity
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = "Closest"
        tex.extension = "EXTEND"
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = uv
        nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        colour, alpha = tex.outputs["Color"], tex.outputs["Alpha"]
    else:
        src = nt.nodes.new("ShaderNodeAttribute")
        src.attribute_type = "GEOMETRY"
        src.attribute_name = attribute
        colour, alpha = src.outputs["Color"], None
    nt.links.new(colour, emit.inputs["Color"])
    if alpha is not None and not ignore_alpha:
        nt.links.new(alpha, scale.inputs[0])
    else:
        scale.inputs[0].default_value = 1.0
    nt.links.new(scale.outputs["Value"], mix.inputs["Fac"])
    nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emit.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    M._set_blend(mat, "BLEND")
    try:
        mat.use_backface_culling = False
    except Exception:                                            # noqa: BLE001
        pass
    return mat


def _make_object(top, terrain, kind: str, label: str, material, image=None):
    """A paint object that shares the terrain's mesh (so it follows every sculpt), lifted a little above the ground."""
    coll = paint_collection(top, create=True)
    old = paint_object(top, kind)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    obj = bpy.data.objects.new(f"{map_name_of(top)} {label}", terrain.data)
    coll.objects.link(obj)
    obj.parent = terrain
    obj[KEY_PAINT] = kind
    if image is not None:
        obj[KEY_IMAGE] = image.name
    mod = obj.modifiers.new("Lift", "DISPLACE")
    mod.direction = "Z"
    mod.space = "GLOBAL"
    mod.mid_level = 0.0
    mod.strength = LIFT[kind]
    return obj


def _slot_material(obj, material) -> None:
    """Show ``material`` on a paint object although its mesh is the terrain's: every material slot of the object is linked to the object
    and holds ``material``, so the terrain's own mesh (and its ground materials) are not touched."""
    for slot in obj.material_slots:
        slot.link = "OBJECT"
        slot.material = material


def _new_image(name: str, n: int):
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, width=n, height=n, alpha=True, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "STRAIGHT"
    return img


def put_image_pixels(img, rgba_top_down: np.ndarray) -> None:
    """Set a picture's pixels from rows that run along z (the first row is z = 0, which is the TOP row of the picture)."""
    arr = np.ascontiguousarray(np.flipud(rgba_top_down), np.float32).ravel()
    img.pixels.foreach_set(arr)
    img.update()


def image_pixels(img) -> np.ndarray:
    """A picture's pixels as ``(h, w, 4)`` float, rows along z (the top row of the picture first)."""
    h, w = int(img.size[1]), int(img.size[0])
    arr = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(arr)
    return np.flipud(arr.reshape(h, w, 4))


def ensure_passability(context, top, *, reset: bool = False):
    """Make the passability paint object (and its picture) for the map, from the map's own passability layer. Returns the object."""
    from ICParse.sgb import impass_edit
    X = _x()
    terrain = X.terrain_of(top)
    if terrain is None:
        raise ValueError("The terrain of this map is not in the scene, so there is nothing to paint on.")
    existing = paint_object(top, "impass")
    if existing is not None and not reset and paint_image(top) is not None:
        return existing
    raw, _mv, _gm = X.read_source(context, top)
    plane = impass_edit.read_plane(raw)
    if plane is None:
        raise ValueError("This map has no passability layer (its terrain block is too old to hold one), so there is nothing to paint.")
    _ensure_uv(terrain)
    n = plane.shape[0]
    img = _new_image(f"{map_name_of(top)} passability", n)
    put_image_pixels(img, plane_to_rgba(np.where(np.isin(plane, (0, 1, 2)), plane, 0)))
    img.pack()
    mat = _overlay_material(f"{map_name_of(top)} passability paint", image=img, opacity=OPACITY["impass"])
    obj = _make_object(top, terrain, "impass", "passability paint", mat, img)
    _slot_material(obj, mat)
    return obj


# ---------------------------------------------------------------------------------------------------- terrain types
def terrain_types_of(terrain) -> np.ndarray | None:
    """The terrain-type numbers the colour attribute holds now (nearest type per point), or None when there is no such attribute."""
    attr = terrain.data.color_attributes.get(TYPE_ATTR)
    if attr is None:
        return None
    n = len(terrain.data.vertices)
    col = np.empty(n * 4, np.float32)
    attr.data.foreach_get("color", col)
    return classify_types(col.reshape(-1, 4)[:, :3].astype(np.float64))


def ensure_types(context, top):
    """Make the terrain type colours (from the map's own types) and the paint object that shows them. Returns the object."""
    from ICParse.sgb import terrain_types as T
    X = _x()
    terrain = X.terrain_of(top)
    if terrain is None:
        raise ValueError("The terrain of this map is not in the scene, so there is nothing to paint on.")
    me = terrain.data
    if me.color_attributes.get(TYPE_ATTR) is None:
        raw, _mv, _gm = X.read_source(context, top)
        types = T.read_types(raw)
        if types is None:
            raise ValueError("This map has no terrain type layer (its terrain block is too old to hold one).")
        if types.size != len(me.vertices):
            raise ValueError("The terrain no longer has the points it was opened with, so its terrain types cannot be shown.")
        attr = me.color_attributes.new(TYPE_ATTR, "FLOAT_COLOR", "POINT")
        pal = type_palette()
        rgba = np.ones((types.size, 4), np.float32)
        flat = types.reshape(-1)
        for value in np.unique(flat):
            rgba[flat == value, :3] = pal.get(int(value), pal[0])
        attr.data.foreach_set("color", rgba.ravel())
    obj = paint_object(top, "types")
    if obj is None:
        mat = _overlay_material(f"{map_name_of(top)} terrain type paint", attribute=TYPE_ATTR, opacity=OPACITY["types"])
        obj = _make_object(top, terrain, "types", "terrain type paint", mat)
        _slot_material(obj, mat)
    try:
        me.color_attributes.active_color = me.color_attributes[TYPE_ATTR]
        me.color_attributes.render_color_index = me.color_attributes.find(TYPE_ATTR)
    except Exception:                                            # noqa: BLE001
        pass
    return obj


# ---------------------------------------------------------------------------------------------------- water painting
def ensure_water_attr(terrain):
    me = terrain.data
    attr = me.attributes.get(WATER_ATTR)
    if attr is None:
        attr = me.attributes.new(WATER_ATTR, "INT", "POINT")
        attr.data.foreach_set("value", np.full(len(me.vertices), -1, np.int32))
    return attr


def water_paint_of(terrain) -> np.ndarray | None:
    attr = terrain.data.attributes.get(WATER_ATTR)
    if attr is None:
        return None
    arr = np.empty(len(terrain.data.vertices), np.int32)
    attr.data.foreach_get("value", arr)
    return arr


# ---------------------------------------------------------------------------------------------------- texture stamping
GROUND_LABEL = "ground picture"
KEY_GROUND_IMAGE = "ic_ground_image"     # on the map's collection: the name of its ground picture image


def ground_source(context, top):
    """``(the map's base ground picture's bytes, the file name's ending)`` from beside the map in the game's files (or on disk)."""
    X = _x()
    raw, mv, gm = X.read_source(context, top)
    source = top.get(M.KEY_SOURCE, "")
    plan = X.Plan(name=map_name_of(top), raw=raw, mv=mv, gm=gm)
    for sub, rest, data in X.sibling_files(plan, source):
        if sub == "" and rest.lower() == ".tga":
            return bytes(data)
    raise ValueError("There is no ground picture (<map>.tga) beside this map, so there is nothing to stamp into.")


def ground_image(context, top, *, reset: bool = False):
    """The map's base picture as a packed image, its rows as stored in the file (the first at the top): what the game look shows as
    the ground and what the stamps paint into. Made from the file the first time. ``ValueError`` when the map has none."""
    from ICParse.sgb import ground_picture as G
    img = bpy.data.images.get(top.get(KEY_GROUND_IMAGE, ""))
    if img is not None and not reset:
        return img
    try:
        pic = G.decode(ground_source(context, top))
    except G.PictureError as exc:
        raise ValueError(str(exc)) from None
    h, w = pic.pixels.shape[:2]
    if img is None or tuple(img.size) != (w, h):
        img = bpy.data.images.new(f"{map_name_of(top)} {GROUND_LABEL}", width=w, height=h, alpha=True, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"                  # the colours are shown whatever the alpha says
    put_image_pixels(img, pic.pixels.astype(np.float32) / 255.0)
    img.pack()
    top[KEY_GROUND_IMAGE] = img.name
    return img


def ensure_ground(context, top, *, reset: bool = False):
    """Make the ground picture paint object: the map's own base picture over the terrain. Returns the object."""
    terrain = _x().terrain_of(top)
    if terrain is None:
        raise ValueError("The terrain of this map is not in the scene, so there is nothing to stamp on.")
    existing = paint_object(top, "ground")
    if existing is not None and not reset and paint_image(top, "ground") is not None:
        return existing
    img = ground_image(context, top, reset=reset)
    _ensure_uv(terrain)
    mat = _overlay_material(f"{map_name_of(top)} ground picture", image=img, opacity=1.0, ignore_alpha=True)
    obj = _make_object(top, terrain, "ground", GROUND_LABEL, mat, img)
    _slot_material(obj, mat)
    return obj


def ground_pixels(img) -> np.ndarray:
    """The ground picture's pixels as ``(h, w, 4)`` uint8 RGBA, rows as stored in the file."""
    return np.rint(np.clip(image_pixels(img), 0.0, 1.0) * 255.0).astype(np.uint8)


def put_ground_pixels(img, pixels: np.ndarray) -> None:
    put_image_pixels(img, pixels.astype(np.float32) / 255.0)


def save_ground(sctx) -> None:
    """Write the stamped ground picture as ``<name>.tga`` beside the map (the map file itself holds no stamp)."""
    from ICParse.sgb import ground_picture as G
    img = paint_image(sctx.top, "ground")
    if img is None:
        return
    name = f"{sctx.name}.tga"
    original = sctx.initial_files.get(name)
    if original is None:
        return
    if sctx.files.get(name) != original:
        sctx.add_warning(f"Another map feature already replaced {name}, so the stamps were not written into it.")
        return
    pic = G.decode(original)
    now = ground_pixels(img)
    if now.shape != pic.pixels.shape:
        sctx.add_warning(f"The ground picture is {now.shape[1]} x {now.shape[0]} but {name} is {pic.pixels.shape[1]} x "
                         f"{pic.pixels.shape[0]}: the stamps were not written. Stamp again.")
        return
    changed = int((now != pic.pixels).any(axis=2).sum())
    if not changed:
        return
    pic.pixels = now
    sctx.files[name] = G.encode(pic, G.trailer_of(original))
    sctx.add_change(f"Ground picture: {changed} pixels stamped, written as {name}.")
    sctx.add_warning(f"{name} is what the game draws at its highest ground detail setting only: the {sctx.name}_decaled.jpg "
                     "/ _decaled_small.jpg pictures were not changed.")


# ---------------------------------------------------------------------------------------------------- decals
def decal_collection(top, create: bool = False):
    own = next((c for c in top.children if c.name.endswith(DECAL_COLLECTION) and not c.name.endswith(" look decals")), None)
    if own is None and create:
        own = M._new_collection(f"{map_name_of(top)}{DECAL_COLLECTION}", top)
    return own


def decal_handles(top) -> list:
    own = decal_collection(top)
    return [o for o in own.objects if KEY_DECAL in o] if own is not None else []


def save_passability(sctx) -> None:
    """Write the painted passability into TFAC's last plane and keep the pathfinding records in step."""
    from ICParse.sgb import impass_edit
    img = paint_image(sctx.top)
    if img is None:
        return
    orig = impass_edit.read_plane(sctx.sgb)
    if orig is None:
        return
    if int(img.size[0]) != orig.shape[1] or int(img.size[1]) != orig.shape[0]:
        sctx.add_warning(f"The passability picture is {img.size[0]} x {img.size[1]} but the map's layer is {orig.shape[1]} x "
                         f"{orig.shape[0]}: the map's terrain size changed, so the painted cells were not saved. Paint them again.")
        return
    base = impass_edit.read_plane(sctx.original)
    base = orig if base is None or base.shape != orig.shape else base
    painted = classify_rgba(image_pixels(img))
    shown = np.where(np.isin(base, (0, 1, 2)), base, 0)
    changed = painted != shown                                # the pixels the user painted (against the layer as it was opened)
    if not changed.any():
        return
    new = np.where(changed, painted, orig).astype(np.uint8)
    out, rep = impass_edit.set_plane(sctx.sgb, new)
    if not rep["changed"]:
        return
    sctx.sgb = out
    blocked = int((changed & (painted == BLOCK)).sum())
    opened = int((changed & (painted == OPEN)).sum())
    cleared = int((changed & (painted == CLEAR)).sum())
    bits = [f"{n} {w}" for n, w in ((blocked, "painted blocked"), (opened, "painted open"), (cleared, "given back to the terrain")) if n]
    p = rep["paths"]
    sctx.add_change(f"Passability: {rep['cells_changed']} path cells changed ({', '.join(bits)}). Pathfinding "
                    f"{'updated (' + str(p['impa_cells_changed']) + ' cells, sectors ' + str(p['sectors_before']) + ' to ' + str(p['sectors_after']) + ')' if p['mode'] == 'patched' else 'unchanged'}.")
    for w in rep["warnings"]:
        sctx.add_warning(w)


def save_terrain_types(sctx) -> None:
    """Write the painted terrain types into TFAC's per-point terrain type plane."""
    from ICParse.sgb import terrain_types as T
    terrain = _x().terrain_of(sctx.top)
    if terrain is None:
        return
    painted = terrain_types_of(terrain)
    if painted is None:
        return
    orig = T.read_types(sctx.sgb)
    if orig is None or orig.size != painted.size:
        if orig is not None:
            sctx.add_warning("The terrain no longer has the points it was opened with, so the painted terrain types were not saved.")
        return
    base = T.read_types(sctx.original)
    base = orig if base is None or base.shape != orig.shape else base
    shown = np.where(base <= 6, base, 0).astype(np.uint8).reshape(-1)
    now = painted.reshape(-1)
    changed = now != shown
    if not changed.any():
        return
    new = np.where(changed, now, orig.reshape(-1)).reshape(orig.shape).astype(np.uint8)
    out, rep = T.set_types(sctx.sgb, new)
    if rep["changed"]:
        sctx.sgb = out
        counts = ", ".join(f"{n} {k}" for k, n in rep["counts"].items())
        sctx.add_change(f"Terrain types: {rep['points_changed']} points changed (the map now has {counts}).")


def save_water(sctx) -> None:
    """Write the water painted at its own level into the map's water plane and keep the pathfinding in step."""
    from ICParse.sgb import water_paint
    terrain = _x().terrain_of(sctx.top)
    if terrain is None:
        return
    painted = water_paint_of(terrain)
    if painted is None or not (painted >= 0).any():
        return
    orig = water_paint.read_water(sctx.sgb)
    if orig is None:
        sctx.add_warning("The map has no water plane, so the water painted on it was not saved.")
        return
    if orig.size != painted.size:
        sctx.add_warning("The terrain no longer has the points it was opened with, so the painted water was not saved.")
        return
    new = merge_water(orig.reshape(-1), painted).reshape(orig.shape)
    out, rep = water_paint.set_water(sctx.sgb, new)
    if rep["changed"]:
        sctx.sgb = out
        p = rep["paths"]
        sctx.add_change(f"Water: {rep['cells_changed']} points given their own water height ({rep['wet_points_before']} wet points "
                        f"before, {rep['wet_points_after']} now). Pathfinding "
                        f"{'updated (' + str(p['impa_cells_changed']) + ' cells)' if p['mode'] == 'patched' else 'unchanged'}.")
        for w in rep["warnings"]:
            sctx.add_warning(w)


def decal_rows(top):
    """``[DecalRow]`` of the decal handles of a map (``eid`` None for a new one), in the map's own units."""
    from ICParse.sgb import decals_edit as D
    rows = []
    for o in decal_handles(top):
        eid = int(o[KEY_DECAL])
        m = _x()._game_matrix(o)                              # the game's matrix: position x, y, z; the turn about up
        x, z = float(m[0, 3]), float(m[2, 3])
        rot = math.atan2(float(m[2, 0]), float(m[0, 0]))
        scale = float(o.get("ic_decal_scale", 1.0)) * float(np.linalg.norm(m[:3, 0])) * (-1.0 if o.get("ic_decal_mirror") else 1.0)
        rows.append(D.DecalRow(eid if eid >= 0 else None, str(o.get(KEY_DECAL_PICTURE, "")), x, z, scale, rot))
    return rows


def save_decals(sctx) -> None:
    from ICParse.sgb import decals_edit as D
    handles = decal_handles(sctx.top)
    if not handles and decal_collection(sctx.top) is None:
        return                                               # decals were never opened for editing: leave them alone
    rows = decal_rows(sctx.top)
    out, rep = D.set_decals(sctx.sgb, rows)
    if rep["added"] or rep["removed"] or rep["changed"]:
        sctx.sgb = out
        sctx.add_change(f"Decals: {rep['added']} added, {rep['removed']} removed, {rep['changed']} moved, turned or scaled"
                        + (f" (new pictures: {', '.join(rep['new_pictures'])})" if rep["new_pictures"] else "") + ".")


H.register_save_step("paint passability", save_passability, order=SAVE_ORDER)
H.register_save_step("paint terrain types", save_terrain_types, order=SAVE_ORDER + 1)
H.register_save_step("paint water", save_water, order=SAVE_ORDER + 2)
H.register_save_step("paint decals", save_decals, order=SAVE_ORDER + 3)
H.register_save_step("paint ground picture", save_ground, order=SAVE_ORDER + 4)


# ---------------------------------------------------------------------------------------------------- checks
def check_starts(cctx) -> list:
    """Units placed for a player on a cell painted blocked; a passability picture that no longer fits the terrain."""
    X = _x()
    img = paint_image(cctx.top)
    if img is None or cctx.terrain is None:
        return []
    w, h, ts, _hs = terrain_grid(cctx.terrain)
    n = 2 * (w - 1)
    if int(img.size[0]) != n or int(img.size[1]) != n:
        return [H.problem("warning", "The passability picture does not fit the terrain any more: painted cells will not be saved.",
                          where=img.name)]
    painted = classify_rgba(image_pixels(img))
    half = (w - 1) * ts / 2.0
    cell = ts / 2.0
    bad = []
    for o in X.entity_objects(cctx.top):
        if int(o.get("ic_owner", -1)) < 0:
            continue
        m = X._game_matrix(o)
        cx, cz = int((m[0, 3] + half) // cell), int((m[2, 3] + half) // cell)
        if 0 <= cx < n and 0 <= cz < n and painted[cz, cx] == BLOCK:
            bad.append(o.name)
    if not bad:
        return []
    return [H.problem("error", f"{len(bad)} objects placed for a player stand on cells painted as blocked ({', '.join(bad[:4])}"
                               + (" ..." if len(bad) > 4 else "") + "): they could not move. Open those cells or move the objects.",
                      where=bad[0])]


H.register_check("paint start cells", check_starts)


# ---------------------------------------------------------------------------------------------------- keeping the paint
_PACKED_ENDINGS = (" passability", f" {GROUND_LABEL}")   # the pictures this add-on paints into (the overlay and look pictures are never edited)


@bpy.app.handlers.persistent                                     # else Blender drops the handler when a .blend is opened
def _pack_before_save(*_args) -> None:
    """Pack the passability and ground pictures just before the .blend is written, so what was painted or stamped is kept in the file
    (an edit made after ``pack()`` is otherwise lost when the file is reopened, and the next map save would write the unedited picture)."""
    for img in bpy.data.images:
        try:
            if img.name.endswith(_PACKED_ENDINGS) and img.packed_file is not None and img.is_dirty:
                img.pack()
        except Exception:                                        # noqa: BLE001
            pass


def register() -> None:
    if _pack_before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_before_save)


def unregister() -> None:
    if _pack_before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_pack_before_save)
