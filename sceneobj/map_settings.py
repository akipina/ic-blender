"""sceneobj.map_settings -- Map settings: the map's name, kind and players, where each player starts, the preview pictures, the files a
playable map needs beside it, and the checks that list what is wrong before saving.

It plugs into the map tools through ``map_hooks`` (no other module is edited):

* an import step fills ``Collection.ic_settings`` from the map's header (``ICParse.sgb.scenario.read_header``);
* a save step writes the header back (name, number of players, kind) only when one of them differs from the map as it was opened
  (``scenario.set_header``: a header that is the same gives back the very bytes), so an untouched map is saved byte for byte;
* a save step adds the files a map made from a terrain template needs beside it (its ground picture, minimap, icon, triggers, dialogue,
  NISlets) and the preview pictures made with "Make preview picture";
* checks (``register_check``): players without a start, objects outside the map, blueprints the game does not have, heights out of range,
  a map the game would not list, the ground picture a new map needs.

What each thing is and where the game reads it is in ``ICParse/sgb/scenario.py``'s docstring (decompiled).
"""
from __future__ import annotations

import base64
import importlib
import math
import types
import zlib

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup

from . import map_hooks as H
from . import ops_map_import as M
from . import prefs

ORDER = 55
_PKG = M._PKG

KEY_TEMPLATE = "ic_new_map_template"        # on the collection of a map made with File > New: the terrain template's key ("sav_s")
KEY_ICON_TEXT = "ic_map_icon_text"          # the text that holds the icon made with "Make preview picture" (a TGA, packed)
KEY_MM_TEXT = "ic_map_minimap_text"
KEY_OWNER = "ic_owner"
PROBLEMS: dict = {}                         # map collection name -> the problems of the last "Check map"
MAX_LISTED = 12                             # objects named one by one in a check, the rest summed up


def _X():
    return importlib.import_module(f"{_PKG}.sceneobj.ops_map_export")


def _sc():
    from ICParse.sgb import scenario
    return scenario


def _view():
    return M._view()


# ---------------------------------------------------------------------------------------------------- the settings (header)
class BZ_PG_map_settings(PropertyGroup):
    """The map's header as Blender shows it (``Collection.ic_settings``)."""
    ready: BoolProperty(default=False)
    title: StringProperty(name="Map name", description="Name in the game's map list; a code like $42001 is a language-file "
                                                       "text")
    kind: EnumProperty(name="Kind", items=[("2", "Multiplayer", "Listed in the game's multiplayer map list; saved in Data/scenarios/mp"),
                                           ("1", "Single player", "A campaign or scripted map; saved in Data/scenarios/sp")], default="2")
    players: IntProperty(name="Players", min=1, max=8, default=2, description="How many players the map is made for (the game lists a "
                         "multiplayer map for 2 to 6)")


def fill_settings(g, header) -> None:
    g.title = header.title
    g.kind = str(header.kind) if str(header.kind) in ("1", "2") else "2"
    g.players = max(1, min(8, int(header.players)))
    g.ready = True


def _import_fill(ictx) -> None:
    fill_settings(ictx.top.ic_settings, _sc().read_header(ictx.source_bytes))


def save_header(sctx) -> None:
    """Save step: write the name, the number of players and the kind when they differ from the map as it was opened."""
    g = getattr(sctx.top, "ic_settings", None)
    if g is None or not g.ready:
        return
    sc = _sc()
    orig = sc.read_header(sctx.original)
    kw = {}
    if g.title.strip() != orig.title.strip():
        kw["title"] = g.title
    # compare with what the panel was filled with (fill_settings clamps: a template's 0 players is shown as 1, an odd kind as 2), so a
    # map the user did not touch is never rewritten
    if int(g.players) != max(1, min(8, int(orig.players))):
        kw["players"] = int(g.players)
    if int(g.kind) != (int(orig.kind) if int(orig.kind) in (1, 2) else 2):
        kw["kind"] = int(g.kind)
    if kw:
        try:
            new = sc.set_header(sctx.sgb, **kw)
        except ValueError as exc:
            sctx.refuse(f"The map settings could not be saved: {exc}")
        if new != sctx.sgb:
            sctx.sgb = new
            if "title" in kw:
                sctx.add_change(f"The map's name is now '{g.title.strip()}' (the text the game's map list shows).")
            if "players" in kw:
                sctx.add_change(f"The map is made for {int(g.players)} players.")
            if "kind" in kw:
                sctx.add_change("The map is marked as " + ("multiplayer." if int(g.kind) == 2 else "single player."))
    kind = int(g.kind)
    want_group = sc.GROUP_OF_KIND.get(kind, "mp")
    if want_group != sctx.group:
        sctx.add_warning(f"The map is marked {'single player' if kind == 1 else 'multiplayer'} but this save puts it in Data/scenarios/{sctx.group}: "
                         f"move its files to Data/scenarios/{want_group} so that the game lists it there.")
    for line in sc.listing_problems(sctx.sgb, group=sctx.group):
        sctx.add_warning(line)


# ---------------------------------------------------------------------------------------------------- the files beside a map
def pack_picture(raw: bytes) -> str:
    return base64.b64encode(zlib.compress(bytes(raw), 6)).decode("ascii")


def unpack_picture(text: str) -> bytes:
    return zlib.decompress(base64.b64decode(text.encode("ascii")))


def store_picture(top, key: str, raw: bytes, label: str) -> None:
    """Keep a preview picture (TGA bytes) in the .blend as a text, the way a map made in memory keeps its source."""
    name = top.get(key)
    txt = bpy.data.texts.get(name) if name else None
    if txt is None:
        txt = bpy.data.texts.new(f"Map {label} {top.name}")
        txt.use_fake_user = True
        top[key] = txt.name
    txt.clear()
    txt.write(pack_picture(raw))


def stored_picture(top, key: str):
    txt = bpy.data.texts.get(top.get(key, "") or "")
    return unpack_picture(txt.as_string()) if txt is not None else None


def save_side_files(sctx) -> None:
    """Save step: the files a map made from a terrain template needs beside it, then the preview pictures made here."""
    sc = _sc()
    top = sctx.top
    key = top.get(KEY_TEMPLATE, "")
    added = []
    if key and sctx.source == "":
        game_dir = prefs.resolve_game_dir(sctx.context)[0]
        data = _view().game_data(game_dir) if game_dir else None
        side = sc.template_side_files(data, key, sctx.name)
        if not side:
            sctx.add_warning(f"No game folder was found, so the new map's ground picture, minimap and icon (template {key}) "
                             "are missing and the game paints it magenta. Set the folder and save again.")
        side[f"{sctx.name}.trg"] = sc.TRG_EMPTY
        for fname, raw in side.items():
            if fname not in sctx.files:
                sctx.files[fname] = raw
                added.append(fname)
        for fname, default in ((f"{sctx.name}.dlg", sc.DLG_EMPTY), (f"{sctx.name}.na", sc.NA_EMPTY)):
            if fname not in sctx.files:
                sctx.files[fname] = default
                added.append(fname)
        if added:
            sctx.add_change(f"Files for the new map, beside it: {', '.join(sorted(added))} (the ground picture, minimap and icon are the terrain "
                            f"template's; make new ones with 'Make preview picture').")
    for tkey, suffix, what in ((KEY_ICON_TEXT, "_icon.tga", "icon"), (KEY_MM_TEXT, "_mm.tga", "minimap")):
        raw = stored_picture(top, tkey)
        if raw is not None:
            sctx.files[f"{sctx.name}{suffix}"] = raw
            sctx.add_change(f"The map's {what} picture ({sctx.name}{suffix}) is the one made with 'Make preview picture'.")


# ---------------------------------------------------------------------------------------------------- the players' starts
LAB = "structures/lab.ebp"


def _is_lab(o) -> bool:
    return str(o.get("ic_blueprint", "")).replace("\\", "/").lower().endswith("structures/lab.ebp")


def starts_of(top) -> dict:
    """``{player slot: [the lab objects that player owns]}``."""
    X = _X()
    out: dict = {}
    for o in X.entity_objects(top):
        if _is_lab(o) and int(o.get(KEY_OWNER, -1)) >= 0:
            out.setdefault(int(o[KEY_OWNER]), []).append(o)
    return out


def _player_id(mv, slot: int) -> int:
    return next((int(p.player_id) for p in mv.board.players if int(p.index) == slot), -1)


def place_start(context, top, slot: int, x: float, y: float) -> list:
    """Give player ``slot`` (0-based) a start at Blender position ``x, y``: a laboratory and three henchmen round it, all owned by that
    player (the way every stock map starts a player: measured on 2p_faceoff and 4p_hill_fort, 4 owned objects each). Returns the objects."""
    sc = _sc()
    X = _X()
    game_dir = prefs.resolve_game_dir(context)[0]
    _raw, mv, _gm = X.read_source(context, top)
    terrain = X.terrain_of(top)
    pid = _player_id(mv, slot)
    made = []
    spots = [(sc.LAB_BLUEPRINT, 0.0, 0.0)] + [(sc.HENCHMEN_BLUEPRINT, dx, dz) for dx, dz in sc.start_offsets()]
    for ref, dx, dz in spots:
        px, py = x + dx, y - dz                           # a game z is Blender's -y
        z = X.ground_height_at(terrain, px, py) if terrain is not None else None
        obj, _why = X.new_entity_object(context, top, mv, ref, (px, py, z if z is not None else 0.0), game_dir)
        obj[KEY_OWNER] = int(slot)
        obj["ic_player_id"] = pid
        made.append(obj)
    return made


def ring_positions(top, count: int) -> list:
    """Where ``count`` starts go on a new map: evenly round a ring a third of the way out from the middle."""
    terrain = _X().terrain_of(top)
    half = float(terrain["ic_tile_scale"]) * (int(terrain["ic_map_width"]) - 1) / 2.0
    r = 0.55 * half
    return [(r * math.cos(-math.pi / 2 + math.tau * i / count), r * math.sin(-math.pi / 2 + math.tau * i / count)) for i in range(count)]


# ---------------------------------------------------------------------------------------------------- preview pictures
def ground_picture(context, top):
    """``(RGBA array or None, where it came from)``: the map's ground picture (``<map>.tga``) the editor draws the minimap from."""
    sc = _sc()
    game_dir = prefs.resolve_game_dir(context)[0]
    key = top.get(KEY_TEMPLATE, "")
    try:
        if key and game_dir:
            raw = _view().game_data(game_dir).read(f"scenarios/mt/{key}.tga")
            if raw:
                return sc.read_tga(raw), f"the terrain template {key}"
        src = top.get(M.KEY_SOURCE, "")
        archive = top.get(M.KEY_ARCHIVE, "")
        if archive and game_dir:
            member = src.split("|")[-1]
            raw = _view().game_data(game_dir, archive).read(member.rsplit(".", 1)[0] + ".tga")
            if raw:
                return sc.read_tga(raw), f"{archive}"
        elif src and not archive:
            import os
            p = os.path.splitext(src)[0] + ".tga"
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return sc.read_tga(f.read()), p
    except Exception:                                      # noqa: BLE001   a picture that cannot be read leaves the flat colour
        pass
    return None, "a flat green"


def water_view(context, top, mv):
    """The map as the scene has it now, in the shape ``scenario.water_mask`` reads: the ground's heights and the water plane in the map's units."""
    from . import map_look
    terrain, heights = map_look._ground_heights(top)
    step = float(mv.height_scale)
    if heights is None:
        heights_u = mv.heights.astype(np.float64)
    else:
        heights_u = heights / step
    level = map_look.current_water_level(top, mv)
    plane = mv.water.astype(np.float64)
    if mv.water.any():
        own = mv.water.astype(np.int64) != int(mv.water_level)
        water_u = np.where(own, plane, level / step)
    else:
        water_u = np.full(plane.shape, level / step)
    return types.SimpleNamespace(tile_scale=mv.tile_scale, width=mv.width, height=mv.height, height_scale=step, heights=heights_u, water=water_u)


def render_ground(context, top, side: int = 512):
    """The map's ground as Blender shows it, seen from straight above: an ``(side, side, 4)`` uint8 array in the game's row order (the
    map's ``-z`` edge first). A throw-away scene with only the terrain object, a camera looking down and Blender's Workbench engine; nothing
    of the map's own scene changes."""
    import os
    import tempfile
    terrain = _X().terrain_of(top)
    if terrain is None:
        raise ValueError("The terrain object of this map is not in the scene.")
    half = float(terrain["ic_tile_scale"]) * (int(terrain["ic_map_width"]) - 1) / 2.0
    scn = bpy.data.scenes.new("ic_preview_scene")
    coll = bpy.data.collections.new("ic_preview_collection")
    cam_data = bpy.data.cameras.new("ic_preview_camera")
    cam = bpy.data.objects.new("ic_preview_camera", cam_data)
    tmp = tempfile.mkdtemp(prefix="ic_preview_")
    path = os.path.join(tmp, "ground.png")
    img = None
    try:
        scn.collection.children.link(coll)
        coll.objects.link(terrain)
        coll.objects.link(cam)
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = 2.0 * half
        cam_data.clip_start, cam_data.clip_end = 1.0, 20000.0
        cam.location = (0.0, 0.0, 10000.0)
        scn.camera = cam
        scn.render.engine = "BLENDER_WORKBENCH"
        scn.render.resolution_x = scn.render.resolution_y = int(side)
        scn.render.resolution_percentage = 100
        scn.render.film_transparent = False
        scn.render.image_settings.file_format = "PNG"
        scn.render.image_settings.color_mode = "RGBA"
        scn.render.filepath = path
        sh = scn.display.shading
        sh.light, sh.color_type, sh.show_shadows, sh.show_cavity, sh.show_object_outline = "STUDIO", "TEXTURE", False, False, False
        scn.view_settings.view_transform = "Standard"
        bpy.ops.render.render(scene=scn.name, write_still=True)
        img = bpy.data.images.load(path)
        px = np.empty(img.size[0] * img.size[1] * 4, np.float32)
        img.pixels.foreach_get(px)
        a = px.reshape(img.size[1], img.size[0], 4)
        a = np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)
        a[..., 3] = 255
        return a[::-1].copy()                               # Blender's rows start at the bottom; the game's first row is the +y edge
    finally:
        if img is not None:
            bpy.data.images.remove(img)
        bpy.data.scenes.remove(scn)
        bpy.data.collections.remove(coll)
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(cam_data)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def make_preview(context, top, method: str = "GAME") -> dict:
    """Make the map's icon (128 x 128) and minimap (256 x 256) and keep them in the .blend for the save. ``method`` ``"GAME"``: the ground picture
    (``<map>.tga``) the way the editor's fallback draws them (``TerrainEditorImp::GenerateIcon`` / ``GenerateMinimap``); ``"BLENDER"``: the ground
    rendered from above by Blender (a sculpted or painted ground shows), with the editor's water drawn on it. Returns
    ``{"where", "water_pixels", "icon", "minimap"}``."""
    sc = _sc()
    _raw, mv, _gm = _X().read_source(context, top)
    if method == "BLENDER":
        ground, where = render_ground(context, top), "Blender's render of the ground"
    else:
        ground, where = ground_picture(context, top)
    wv = water_view(context, top, mv)
    icon = sc.icon(ground, wv)
    mm = sc.minimap(ground, wv)
    store_picture(top, KEY_ICON_TEXT, sc.tga_bytes(icon), "icon")
    store_picture(top, KEY_MM_TEXT, sc.tga_bytes(mm), "minimap")
    water = int(sc.water_mask(wv).sum())
    return {"where": where, "water_pixels": water, "icon": list(icon.shape[:2]), "minimap": list(mm.shape[:2])}


# ---------------------------------------------------------------------------------------------------- checks
def _game_xz(o):
    """An object's position in the game's axes (x, z)."""
    t = o.matrix_world.translation
    return float(t.x), float(-t.y)


def _terrain_half(top):
    terrain = _X().terrain_of(top)
    if terrain is None:
        return None
    return float(terrain["ic_tile_scale"]) * (int(terrain["ic_map_width"]) - 1) / 2.0


def check_header(cctx) -> list:
    g = getattr(cctx.top, "ic_settings", None)
    if g is None or not g.ready:
        return []
    out = []
    kind = int(g.kind)
    if not g.title.strip():
        out.append(H.problem(H.WARNING, "The map has no name: the game would show its file name in the map list.", "Map settings"))
    if kind == 2 and g.players < 2:
        out.append(H.problem(H.ERROR, "A multiplayer map must be made for at least 2 players, or the game leaves it out of its map list.",
                             "Map settings"))
    if kind == 2 and g.players > 6:
        out.append(H.problem(H.WARNING, f"The map is made for {g.players} players: the stock multiplayer maps have 6 at most.", "Map settings"))
    return out


def check_starts(cctx) -> list:
    g = getattr(cctx.top, "ic_settings", None)
    if g is None or not g.ready or int(g.kind) != 2:           # a single-player map starts its players by script
        return []
    starts = starts_of(cctx.top)
    out = []
    for slot in range(int(g.players)):
        if slot not in starts:
            out.append(H.problem(H.ERROR, f"Player {slot + 1} has no start: give the player a laboratory (Map settings > Players).", None))
    for slot in starts:
        if slot >= int(g.players):
            out.append(H.problem(H.WARNING, f"Player {slot + 1} owns a laboratory but the map is made for {g.players} players: that player is "
                                            "not created in the game.", starts[slot][0].name))
    return out


def check_outside(cctx) -> list:
    if "objects_outside_the_map" in H.registered()["checks"]:      # the conveniences feature (map_tools) lists the same thing: not twice
        return []
    half = _terrain_half(cctx.top)
    if half is None:
        return []
    cctx.context.view_layer.update()                       # an object made a moment ago has its position in its matrix only after this
    out, n = [], 0
    for o in _X().entity_objects(cctx.top):
        x, z = _game_xz(o)
        if abs(x) > half or abs(z) > half:
            n += 1
            if n <= MAX_LISTED:
                out.append(H.problem(H.WARNING, f"{o.name} is outside the map (at {x:.0f} m, {z:.0f} m; the map reaches {half:.0f} m).", o.name))
    if n > MAX_LISTED:
        out.append(H.problem(H.WARNING, f"{n - MAX_LISTED} more objects are outside the map.", None))
    return out


def check_blueprints(cctx) -> list:
    game_dir = prefs.resolve_game_dir(cctx.context)[0]
    if not game_dir:
        return []
    data = _view().game_data(game_dir, cctx.top.get(M.KEY_ARCHIVE, ""))
    seen: dict = {}
    for o in _X().entity_objects(cctx.top):
        ref = str(o.get("ic_blueprint", ""))
        if ref and ref not in seen:
            seen[ref] = (data.exists(ref), o.name)
    out = []
    for ref, (ok, name) in seen.items():
        if not ok:
            out.append(H.problem(H.ERROR, f"The blueprint {ref} is not in the game's files: the game would not load a map that places it.", name))
    return out


def check_heights(cctx) -> list:
    terrain = cctx.terrain
    if terrain is None:
        return []
    from . import map_look
    _t, heights = map_look._ground_heights(cctx.top)
    if heights is None:
        return []
    step = float(terrain.get("ic_height_scale", 0.5))
    top_m = 255.0 * step
    low = int((heights < -step / 2).sum())
    high = int((heights > top_m + step / 2).sum())
    out = []
    if low:
        out.append(H.problem(H.ERROR, f"{low} ground points are below 0 m: the map keeps each height as one byte (0 to {top_m:g} m in steps of "
                                      f"{step:g} m), so they would be saved at 0.", terrain.name))
    if high:
        out.append(H.problem(H.ERROR, f"{high} ground points are above {top_m:g} m: the map keeps each height as one byte (0 to {top_m:g} m in "
                                      f"steps of {step:g} m), so they would be saved at the top.", terrain.name))
    return out


def check_files(cctx) -> list:
    """A map made with File > New needs its ground picture and the other files from the terrain template when it is saved."""
    key = cctx.top.get(KEY_TEMPLATE, "")
    if not key:
        return []
    game_dir = prefs.resolve_game_dir(cctx.context)[0]
    if not game_dir:
        return [H.problem(H.ERROR, "No game folder was found, so the new map's ground picture, minimap and icon cannot be "
                                   "copied (magenta in game): set it in the preferences.", None)]
    data = _view().game_data(game_dir)
    missing = [s for s in _sc().SIDE_SUFFIXES if not data.read(f"scenarios/mt/{key}{s}")]
    return [H.problem(H.WARNING, f"The terrain template {key} has no file {m} in the game's files.", None) for m in missing]


def save_problems(sctx) -> None:
    """Save step: the Map problems found at the moment of the save are listed in the save report ("Show Error Messages" before saving).
    Nothing is refused: the list says what the game would meet."""
    try:
        found = H.run_checks(sctx.context, sctx.top)
    except Exception as exc:                               # noqa: BLE001
        sctx.add_warning(f"The map could not be checked for problems: {exc}")
        return
    for p in found:
        sctx.add_warning(("Problem: " if p["severity"] == H.ERROR else "Note: ") + p["text"])


def register_checks() -> None:
    for name, fn in (("map header", check_header), ("player starts", check_starts), ("objects outside the map", check_outside),
                     ("unknown blueprints", check_blueprints), ("heights out of range", check_heights), ("new map files", check_files)):
        H.register_check(name, fn)


H.register_import_step("map settings values", _import_fill, order=20)
H.register_save_step("map settings header", save_header, order=20)
H.register_save_step("map settings files", save_side_files, order=22)
H.register_save_step("map problems", save_problems, order=90)
register_checks()

CLASSES = (BZ_PG_map_settings,)


def register():
    bpy.types.Collection.ic_settings = bpy.props.PointerProperty(type=BZ_PG_map_settings)


def unregister():
    del bpy.types.Collection.ic_settings
