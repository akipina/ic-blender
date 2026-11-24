"""sceneobj.ops_map_import -- open an Impossible Creatures map (.sgb) in Blender and see it as the game's editor shows it.

File > Import > "Impossible Creatures Map (.sgb)" opens a map file; File > Import > "Impossible Creatures Map from the Game..."
picks one of the game's own maps from a list (the loose ``Data/scenarios`` folder and every archive of the install). The map can
be edited in Blender and saved with File > Export > Impossible Creatures Map (ops_map_export.py, panels_map.py). What the save needs is kept on
the objects: the map's source on its collection and its terrain, and each placed entity's id, blueprint, owner and groups on its object.

What is built (the meanings come from ``ICParse.sgb.view``, which says where each one was read from):

* one collection per map, ``Map <name>``, with a collection per kind: terrain, water, structures, nature, units, other;
* the terrain: one mesh, one vertex per height point (``width x height``), one face per cell, at the map's real size and
  heights, in the axes the add-on gives every scene model (the game's (x, y up, z) becomes Blender's (x, -z, y), so placed
  models sit on the ground they sit on in the game); one material per ground texture, each repeated across its 8 x 8 cell
  blocks the way the game repeats it, assigned block by block;
* the water: a see-through surface over the cells where the map's water plane is above the ground, at the water plane's
  height, more see-through where it is shallow (the map's own shore and deep-water settings);
* every placed entity (``ENTT``) as an object at its stored position and rotation: an instance of its model when the model
  could be loaded (each distinct model is loaded once, into a hidden collection), else an empty named after its blueprint,
  with the reason, in the report. Creatures (stock or combined in the map) are built from their parts (``map_creatures``): each
  kind once, in the bind pose, as a model of its own, and the placed creatures are instances of it;
* the game look (``map_look``): the map's sun, sky, fog, the open sea round it, its decals and its water colours, in a collection of
  their own that a save does not read;
* a report: map size, how many entities were placed, how many distinct models, which could not be shown and why, the time.

Left and right: in the 3D view the map (like every model the add-on imports) is the mirror image of what the game shows, so
writing on a model reads backwards. The game's coordinates are left-handed and (x, -z, y) only turns them. Positions and the
save are not affected. The cameras that stand for the game's camera are flipped (:func:`show_game_picture`), so what is seen
through them is the game's picture.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import tempfile
import time

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import prefs

ORDER = 60

# custom properties the import writes (a later edit / export reads them)
KEY_MAP = "ic_map"                     # on the map's collection and its terrain: True
KEY_SOURCE = "ic_map_source"           # where the map was read from: a file path, or "<archive>|<member>"
KEY_ARCHIVE = "ic_map_archive"         # the archive's file name when it came from one, else ""
KEY_REPORT = "ic_map_report"           # on the map's collection: the import report as JSON
KEY_ENTITY = "ic_entity_id"            # on every placed entity: its id in the map
KEY_MODEL = "ic_map_model"             # on the objects of a loaded model: the model's game reference

KIND_TITLES = {"terrain": "terrain", "water": "water", "structures": "structures", "nature": "nature", "units": "units",
               "other": "other"}
GROUP_WORDS = {"mp": "multiplayer", "sp": "campaign", "mt": "empty map"}

_PKG = __package__ or "blender_native"
if _PKG.endswith(".sceneobj"):
    _PKG = _PKG[:-len(".sceneobj")]


def _addon(name: str):
    """A module of the add-on ('core', 'scene', 'rig')."""
    return importlib.import_module(f"{_PKG}.{name}")


def _view():
    from ICParse.sgb import view
    return view


class MapImportError(Exception):
    """A plain-words reason why a map could not be opened."""


# ---------------------------------------------------------------------------------------------------- the game's own maps
_MAPS: dict = {}               # game folder -> (stamp, [GameMap])
_MAP_ITEMS: list = []          # enum items must stay referenced while Blender shows them


def _stamp(game_dir: str) -> tuple:
    view = _view()
    paths = view.game_archives(game_dir)
    data_dir = view._child(game_dir, "Data")
    scen = view._child(data_dir, "scenarios") if data_dir else None
    if scen:
        paths = paths + [dp for dp, _d, _f in os.walk(scen)]
    out = []
    for p in paths:
        try:
            out.append((p, os.path.getmtime(p)))
        except OSError:
            pass
    return tuple(out)


def game_maps(game_dir: str) -> list:
    """The game's own maps (``ICParse.sgb.view.GameMap``), kept until the install's archives or its scenarios folder change."""
    if not game_dir:
        return []
    stamp = _stamp(game_dir)
    hit = _MAPS.get(game_dir)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    maps = _view().game_maps(game_dir)
    _MAPS[game_dir] = (stamp, maps)
    return maps


def map_label(gm) -> str:
    """How a game map reads in the list: its name, then what kind of map it is and where it is kept."""
    where = "game folder" if gm.source == "Data" else gm.source
    group = GROUP_WORDS.get(gm.group, gm.group)
    return f"{gm.label}  ({group}, {where})" if group else f"{gm.label}  ({where})"


def _map_items(self, context):
    game_dir = prefs.resolve_game_dir(context)[0]
    maps = game_maps(game_dir) if game_dir else []
    if maps:
        items = [(gm.key, map_label(gm), f"{gm.member} in {'the game folder' if gm.source == 'Data' else gm.source}", "", i)
                 for i, gm in enumerate(maps)]
    else:
        items = [("NONE", "No game found: set the game folder in the add-on's preferences", "", "ERROR", 0)]
    _MAP_ITEMS[:] = items
    return _MAP_ITEMS


# ---------------------------------------------------------------------------------------------------- building blocks
class _Into:
    """``bpy.context`` with another collection: the scene-model builder links what it makes into ``context.collection``."""

    def __init__(self, context, collection):
        self._context = context
        self.collection = collection

    def __getattr__(self, name):
        return getattr(self._context, name)


def _layer_collection(layer, coll):
    if layer.collection == coll:
        return layer
    for child in layer.children:
        hit = _layer_collection(child, coll)
        if hit is not None:
            return hit
    return None


def _new_collection(name: str, parent):
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def show_game_picture(camera) -> None:
    """Make the camera object ``camera`` show the picture the game shows. The game's camera has right = up x forward (a Direct3D
    left-handed projection: ``RenderCamera::Apply``, IC.exe 0x005483f5), and the add-on's (x, -z, y) turns the game's coordinates
    without mirroring them, so the world in Blender is the game's seen in a mirror. A camera scaled -1 on its own X looks at it
    with right = up x forward; Blender still culls and shades the right sides of the faces through it (Workbench and EEVEE)."""
    camera.scale = (-1.0, 1.0, 1.0)


def _set_blend(mat, method: str) -> None:
    """See-through or cut-out drawing in both the older and the newer Eevee."""
    for attr, value in (("blend_method", method), ("surface_render_method", "BLENDED" if method == "BLEND" else "DITHERED")):
        try:
            setattr(mat, attr, value)
        except Exception:
            pass


def _bsdf(mat):
    mat.use_nodes = True
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def load_game_picture(raw: bytes, path: str):
    """A loose picture of the game as a Blender image that shows its rows as the game reads them: the first row in the file at the
    top, so that Blender's v = 1 - the game's v. The game's TGA reader never looks at the header's origin bit; Blender does, so a
    TGA is handed over with that bit saying "the first row is the top". Writes ``path`` on the way."""
    if path.lower().endswith(".tga") and len(raw) > 18:
        raw = raw[:17] + bytes([raw[17] | 0x20]) + raw[18:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(raw)
    return bpy.data.images.load(path, check_existing=False)


def _ground_image(texture, data, tmp: str):
    """The ground texture's image, packed into the file (so nothing points at the game's folder), or None."""
    raw = data.read(texture.ref) if data is not None else None
    if not raw:
        return None
    path = os.path.join(tmp, "ground", os.path.basename(texture.name.lower()) or "ground.tga")
    try:
        img = load_game_picture(raw, path)
        img.name = f"Ground {texture.label}"
        img.pack()
    except Exception:
        return None
    return img


def _average_colour(img) -> tuple:
    try:
        px = np.empty(len(img.pixels), dtype=np.float32)
        img.pixels.foreach_get(px)
        rgb = px.reshape(-1, 4)[:, :3].mean(axis=0)
        return float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0
    except Exception:
        return 0.5, 0.5, 0.5, 1.0


def _ground_material(texture, img):
    mat = bpy.data.materials.new(f"Ground {texture.label if texture is not None else 'unnamed'}")
    bsdf = _bsdf(mat)
    if bsdf is not None and "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.9
    if img is None:
        colour = (0.45, 0.42, 0.36, 1.0)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = colour
    else:
        colour = _average_colour(img)
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = "REPEAT"
        tex.location = (-400, 200)
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map = "Ground"
        uv.location = (-650, 200)
        links.new(uv.outputs["UV"], tex.inputs["Vector"])
        if bsdf is not None:
            links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nodes.active = tex
    mat.diffuse_color = colour                 # what the Solid view shows
    if texture is not None:
        mat["ic_ground_texture"] = texture.name
        mat["ic_ground_texture_id"] = texture.texid
    return mat


def _water_material(board):
    shore = float(getattr(board, "water_shore_alpha", 0.3) or 0.3)
    deep = float(getattr(board, "water_max_ocean_alpha", 1.0) or 1.0)
    mat = bpy.data.materials.new("Water")
    colour = (0.149, 0.275, 0.376)
    bsdf = _bsdf(mat)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.15
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        depth = nodes.new("ShaderNodeAttribute")
        depth.attribute_name = "ic_water_depth"
        depth.location = (-650, -150)
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.location = (-400, -150)
        ramp.inputs["From Min"].default_value = 0.0
        ramp.inputs["From Max"].default_value = 4.0           # metres to reach the deep-water look (the renderer's value)
        ramp.inputs["To Min"].default_value = shore
        ramp.inputs["To Max"].default_value = deep
        links.new(depth.outputs["Fac"], ramp.inputs["Value"])
        links.new(ramp.outputs["Result"], bsdf.inputs["Alpha"])
    mat.diffuse_color = (*colour, 0.6)
    _set_blend(mat, "BLEND")
    return mat


def _mesh(name: str, verts: np.ndarray, quads: np.ndarray):
    """A mesh of quads from numpy arrays (fast enough for a million points)."""
    me = bpy.data.meshes.new(name)
    nv, nf = len(verts), len(quads)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", np.ascontiguousarray(verts, dtype=np.float32).ravel())
    me.loops.add(nf * 4)
    me.loops.foreach_set("vertex_index", np.ascontiguousarray(quads, dtype=np.int32).ravel())
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, nf * 4, 4, dtype=np.int32))
    try:
        me.polygons.foreach_set("loop_total", np.full(nf, 4, dtype=np.int32))
    except (AttributeError, TypeError, RuntimeError):
        pass                                    # newer Blender works the sizes out from the starts
    me.update(calc_edges=True)
    try:
        me.shade_smooth()
    except AttributeError:
        me.polygons.foreach_set("use_smooth", np.ones(nf, dtype=bool))
    return me


def _loop_values(me, per_vertex: np.ndarray) -> np.ndarray:
    idx = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", idx)
    return per_vertex[idx]


def _fit_view(size: float) -> list:
    """Let every 3D view draw far enough to show the whole map; returns the new distances it set."""
    want = max(1000.0, 3.0 * size)
    changed = []
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "VIEW_3D" and space.clip_end < want:
                    space.clip_end = want
                    changed.append(want)
    return changed


# ---------------------------------------------------------------------------------------------------- the import
HIDDEN_MATERIAL = "IC not drawn by the game"


def _hidden_material():
    """See-through in renders and the Material view, faint in the Solid view, so the object can still be seen there and picked."""
    mat = bpy.data.materials.get(HIDDEN_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(HIDDEN_MATERIAL)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        clear = nt.nodes.new("ShaderNodeBsdfTransparent")
        nt.links.new(clear.outputs["BSDF"], out.inputs["Surface"])
        mat.diffuse_color = (0.6, 0.8, 1.0, 0.2)
        _set_blend(mat, "BLEND")
    return mat


def hide_never_drawn(coll, sd) -> int:
    """On a map's copy of a model, give the faces the game never draws (``Scene.material_hidden``: the water sparkles, helper
    shapes) the see-through material. Returns how many material slots were changed. ``sd``: the ``core.SceneData`` the objects
    were built from (material slot i is ``sd.materials[i]``)."""
    hidden = getattr(sd.scene, "material_hidden", ()) or ()
    slots = [i for i, m in enumerate(sd.materials) if m["name"] in hidden]
    changed = 0
    for o in coll.all_objects:
        if o.type != "MESH":
            continue
        for i in slots:
            if i < len(o.data.materials):
                o.data.materials[i] = _hidden_material()
                changed += 1
    return changed


def load_model_collection(context, coll, info, data, game_dir, tmp: str) -> None:
    """Build the model ``info`` (a ``view`` model record, with its ``model_ref`` and ``raw`` bytes) into the empty collection ``coll``. Used by
    the import and by the save's "Add object". The objects are marked as parts of the map (``KEY_MODEL``), not as a project the add-on
    exports. When the model cannot be built, what was made and ``coll`` itself are removed again and the error is raised."""
    core, scene = _addon("core"), _addon("scene")
    path = os.path.join(tmp, "models", *info.model_ref.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(info.raw)
    try:
        sd = core.load_scene(path, data_root=game_dir, texture_source=data)         # the map's files, opened once
        objs = scene.build_scene(_Into(context, coll), sd, source_path=path, clips_as_actions=False)
        if not objs:
            raise MapImportError("the model has no parts")
        hide_never_drawn(coll, sd)
    except Exception:
        for o in list(coll.all_objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(coll)
        raise
    for o in coll.all_objects:
        for key in ("bz_scene", "bz_source"):                # a part of the map, not a project the add-on exports
            if key in o:
                del o[key]
        o[KEY_MODEL] = info.model_ref
    coll[KEY_MODEL] = info.model_ref


def _source_bytes_of(mv) -> bytes:
    """The map file's bytes when the caller did not pass them: the file it was read from, else the board written back (byte-exact)."""
    if mv.source and not mv.archive and os.path.isfile(mv.source):
        with open(mv.source, "rb") as f:
            return f.read()
    from ICParse.sgb import parser
    return parser.write(mv.board)


def import_map(context, mv, *, game_dir: str | None = None, load_models: bool = True, show_water: bool = True,
               show_creatures: bool = True, game_look: bool = True, source_bytes: bytes | None = None,
               in_memory: bool = False, siblings: dict | None = None) -> dict:
    """Build the map ``mv`` (an ``ICParse.sgb.view.MapView``) in the current file. Returns the report (a dict; ``report["collection"]``
    is the name of the map's collection). ``source_bytes`` is the map file's bytes (for the import steps of ``map_hooks``; read
    from the file when not given). ``in_memory`` keeps those bytes in the .blend, for a map that has no file
    (``map_hooks.import_map_bytes``)."""
    view = _view()
    core, scene, rig = _addon("core"), _addon("scene"), _addon("rig")
    t0 = time.perf_counter()
    name = mv.name if mv.name and not mv.name.startswith("$") else ""
    stem = os.path.splitext(os.path.basename(mv.source.split("|")[-1]))[0] if mv.source else ""
    name = stem or name or "map"
    data = view.game_data(game_dir, mv.archive) if game_dir else None
    tmp = tempfile.mkdtemp(prefix="ic_map_")
    report = {"map": name, "source": mv.source, "archive": mv.archive, "title": mv.name,
              "size": [mv.width, mv.height], "cells": list(mv.cells), "world_size_m": list(mv.world_size),
              "ground_textures": [t.name for t in mv.textures], "ground_textures_missing": [],
              "detail_tiling": mv.detail_tiling, "water_cells": 0, "water_height_m": mv.water_height,
              "entities": len(mv.entities), "placed": 0, "instances": 0, "empties": 0, "models_loaded": 0,
              "models": [], "not_shown": [], "models_failed": [], "problems": list(mv.problems),
              "game_folder": game_dir or "", "seconds": 0.0}
    top = None
    try:
        top = bpy.data.collections.new(f"Map {name}")
        context.scene.collection.children.link(top)
        top[KEY_MAP] = True
        top[KEY_SOURCE] = mv.source
        top[KEY_ARCHIVE] = mv.archive
        if in_memory:
            from . import map_hooks
            text = bpy.data.texts.new(f"Map source {top.name}")
            text.use_fake_user = True
            text.write(map_hooks.pack_source(source_bytes if source_bytes is not None else _source_bytes_of(mv)))
            top[map_hooks.KEY_SOURCE_TEXT] = text.name
        kinds = {k: _new_collection(f"{name} {title}", top) for k, title in KIND_TITLES.items()}

        # ---- terrain
        verts = core.sgm_to_blender(mv.vertex_positions(np.float64))
        quads = mv.quads()
        me = _mesh(f"{name} terrain", verts, quads)
        texids = mv.face_texids()
        slots = {}
        for t in mv.textures:
            img = _ground_image(t, data, tmp)
            if img is None:
                report["ground_textures_missing"].append(t.name)
            slots[t.texid] = len(me.materials)
            me.materials.append(_ground_material(t, img))
        if (texids < 0).any() or any(int(i) not in slots for i in np.unique(texids)):
            unnamed = len(me.materials)
            me.materials.append(_ground_material(None, None))
        else:
            unnamed = 0
        mat_index = np.full(len(texids), unnamed, dtype=np.int32)
        for tid, slot in slots.items():
            mat_index[texids == tid] = slot
        me.polygons.foreach_set("material_index", mat_index)
        uv = mv.vertex_uvs()
        uv[:, 1] = 1.0 - uv[:, 1]                               # Blender's images start at the bottom row
        uvl = me.uv_layers.new(name="Ground")
        uvl.data.foreach_set("uv", _loop_values(me, uv).astype(np.float32).ravel())
        terrain = bpy.data.objects.new(f"{name} terrain", me)
        kinds["terrain"].objects.link(terrain)
        terrain[KEY_MAP] = True
        terrain[KEY_SOURCE] = mv.source
        terrain[KEY_ARCHIVE] = mv.archive
        for key, value in (("ic_map_width", mv.width), ("ic_map_height", mv.height), ("ic_tile_scale", mv.tile_scale),
                           ("ic_height_scale", mv.height_scale), ("ic_water_level", mv.water_level),
                           ("ic_detail_tiling", mv.detail_tiling), ("ic_patch_size", mv.patch_size)):
            terrain[key] = value

        # ---- water
        if show_water:
            wpos, wquads, wvid = mv.water_mesh(np.float64)
            report["water_cells"] = int(len(wquads))
            if len(wquads):
                wme = _mesh(f"{name} water", core.sgm_to_blender(wpos), wquads)
                depth = np.clip(wpos[:, 1] - mv.heights.reshape(-1)[wvid].astype(np.float64) * mv.height_scale, 0.0, None)
                attr = wme.attributes.new("ic_water_depth", "FLOAT", "POINT")
                attr.data.foreach_set("value", depth.astype(np.float32))
                wme.materials.append(_water_material(mv.board))
                water = bpy.data.objects.new(f"{name} water", wme)
                kinds["water"].objects.link(water)
                water["ic_water_level"] = mv.water_level

        # ---- the models, each loaded once into a hidden collection
        infos = view.resolve_models(mv, data) if load_models else {}
        models = {}
        hidden = None
        if any(i.status == view.MODEL for i in infos.values()):
            hidden = _new_collection(f"{name} models (hidden)", top)
            for info in sorted(infos.values(), key=lambda i: i.key):
                if info.status != view.MODEL:
                    continue
                coll = _new_collection(f"model {os.path.basename(info.model_ref)[:-4]}", hidden)
                try:
                    load_model_collection(context, coll, info, data, game_dir, tmp)
                except Exception as exc:                   # one model that fails leaves the rest of the map
                    info.status, info.reason = "failed", f"the model {info.model_ref} could not be imported: {exc}"
                    report["models_failed"].append({"model": info.model_ref, "why": str(exc)})
                    continue
                models[info.key] = coll
                report["models"].append(info.model_ref)
                info.raw = b""
        report["models_loaded"] = len(models)

        # ---- the placed entities
        for e in mv.entities:
            obj = bpy.data.objects.new(f"{e.name} {e.instance_id}", None)
            coll = models.get(e.model_key)
            if coll is not None:
                obj.instance_type = "COLLECTION"
                obj.instance_collection = coll
                obj.empty_display_size = 0.5
                report["instances"] += 1
            else:
                obj.empty_display_type = "SPHERE"
                obj.empty_display_size = 1.5
                info = infos.get(e.model_key)
                why = info.reason if info is not None else "the models were not loaded (the import was asked not to)"
                obj["ic_not_shown"] = why
                report["empties"] += 1
            obj.matrix_world = rig.to_blender_matrix(e.matrix())      # the game's axes -> the add-on's, as for every model
            obj[KEY_ENTITY] = e.instance_id
            obj["ic_blueprint"] = e.blueprint
            obj["ic_blueprint_number"] = e.blueprint_ref
            obj["ic_kind"] = e.kind
            obj["ic_owner"] = e.owner
            obj["ic_player_id"] = e.player_id
            obj["ic_sound_id"] = e.sound_id
            if e.groups:
                obj["ic_groups"] = ", ".join(e.groups)
            if e.combined:
                obj["ic_combined_creature"] = True
            kinds.get(e.kind, kinds["other"]).objects.link(obj)
            report["placed"] += 1
        for info in sorted(infos.values(), key=lambda i: (-i.count, i.name)):
            if info.key not in models:
                report["not_shown"].append({"name": info.name, "count": info.count, "why": info.reason or "not loaded"})
        if not load_models:
            report["not_shown"] = [{"name": "every entity", "count": len(mv.entities),
                                    "why": "the models were not loaded (the import was asked not to)"}]

        # ---- the creatures, as their real models (stock and combined)
        if show_creatures and load_models and data is not None:
            from . import map_creatures
            crep = map_creatures.show_creatures(context, mv, top, data=data, infos=infos)
            report["creatures"] = crep
            report["instances"] += crep["creatures_shown"]
            report["empties"] -= crep["creatures_shown"]
            report["not_shown"] = [n for n in report["not_shown"]
                                   if not n["why"].startswith(("a creature:", "a creature combined"))]
            report["not_shown"] += crep["not_built"]
            hidden = hidden or next((c for c in top.children if c.name.endswith("(hidden)")), None)

        if hidden is not None:                     # the instances draw the models; the originals stay out of the view
            layer = _layer_collection(context.view_layer.layer_collection, hidden)
            if layer is not None:
                layer.exclude = True
        for o in context.view_layer.objects:
            o.select_set(False)
        terrain.select_set(True)
        context.view_layer.objects.active = terrain
        # ---- the game look: ground picture, sun, sky, fog, water, border, decals (none of it is saved with the map)
        if game_look:
            from . import map_look
            report["look"] = map_look.build_look(context, top, mv, game_dir=game_dir, data=data, tmp=tmp)
        report["view_distance_m"] = (_fit_view(max(mv.world_size)) or [None])[0]
        if report.get("look") and report["look"].get("view_distance_m"):
            report["view_distance_m"] = max(report["view_distance_m"] or 0, report["look"]["view_distance_m"])
        report["collection"] = top.name
        # ---- the map features that plug in (map_hooks): with none registered nothing happens
        from . import map_hooks
        if map_hooks.registered()["import"]:
            map_hooks.run_import_steps(map_hooks.ImportContext(
                context=context, top=top, name=name,
                source_bytes=source_bytes if source_bytes is not None else _source_bytes_of(mv), view=mv, board=mv.board,
                options={"load_models": load_models, "show_water": show_water, "show_creatures": show_creatures,
                         "game_look": game_look},
                terrain=terrain, kinds=kinds, game_dir=game_dir, data=data, report=report, tmp=tmp,
                siblings=dict(siblings or {})))
    except Exception as exc:                   # leave nothing half-built behind
        if top is not None:
            _remove_collection(top)
        raise MapImportError(f"The map {name} could not be built: {exc}") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["seconds"] = round(time.perf_counter() - t0, 2)
    top[KEY_REPORT] = json.dumps(report)
    _report_text(report)
    return report


def _remove_collection(coll) -> None:
    """Remove a collection, its sub-collections and every object in them."""
    for o in list(coll.all_objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for child in list(coll.children):
        _remove_collection(child)
    bpy.data.collections.remove(coll)


def report_lines(report: dict) -> list:
    """The report in plain words, one line each."""
    where = f"{report['archive']}, {report['source'].split('|')[-1]}" if report["archive"] else report["source"]
    w, h = report["size"]
    cw, ch = report["cells"]
    sx, sz = report["world_size_m"]
    lines = [f"Map {report['map']} (from {where})",
             f"Size: {w} x {h} height points ({cw} x {ch} cells), {sx:g} x {sz:g} m"]
    tex = ", ".join(os.path.splitext(t)[0] for t in report["ground_textures"]) or "none"
    lines.append(f"Ground textures: {len(report['ground_textures'])} ({tex}), each repeated {report['detail_tiling']} "
                 "times across a block of 8 x 8 cells")
    if report["ground_textures_missing"]:
        lines.append("Ground textures not found in the game's files (shown as a plain colour): "
                     + ", ".join(report["ground_textures_missing"]))
    lines.append(f"Water: {report['water_cells']} cells under water" if report["water_cells"] else "Water: none")
    lines.append(f"Placed: {report['placed']} of {report['entities']} entities -- {report['instances']} shown with their model "
                 f"({report['models_loaded']} different models), {report['empties']} shown as empties")
    crep = report.get("creatures")
    if crep:
        lines.append(f"Creatures: {crep['creatures_shown']} of {crep['creatures_placed']} shown as models "
                     f"({crep['built']} kinds built, {crep['seconds']:g} s)")
    look = report.get("look")
    if look:
        lines.append("Game look: " + ("the ground picture, " if look.get("ground_picture") else "") + "the sun"
                     + (f" ({look['sun']['elevation_degrees']:g} degrees up)" if look.get("sun") else "")
                     + ", sky" + (" picture" if look.get("sky_loaded") else " (picture not found)")
                     + (", water and open sea" if look.get("sea") else "")
                     + f", {look['decals']} decals in {look['decal_pictures']} pictures"
                     + f", fog ({'on' if look.get('fog') else 'off'})")
        lines += [f"Note: {p}" for p in look.get("problems", [])]
    if report["not_shown"]:
        lines.append("Shown as empties:")
        lines += [f"  {n['name']} x{n['count']}: {n['why']}" for n in report["not_shown"]]
    if report["models_failed"]:
        lines.append("Models that could not be imported:")
        lines += [f"  {m['model']}: {m['why']}" for m in report["models_failed"]]
    else:
        lines.append("Models that could not be imported: none")
    for p in report["problems"]:
        lines.append(f"Note: {p}")
    if report.get("view_distance_m"):
        lines.append(f"The 3D view now draws up to {report['view_distance_m']:g} m away, so the whole map fits.")
    lines.append(f"Took {report['seconds']:g} s")
    return lines


def _report_text(report: dict) -> None:
    """The report as a text in the file (Text Editor), so it can be read after the status bar has moved on."""
    name = f"Map import {report['map']}"
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.clear()
    txt.write("\n".join(report_lines(report)) + "\n")


def _finish(op, report: dict) -> None:
    op.report({"INFO"}, f"Map {report['map']}: {report['placed']} entities placed ({report['instances']} with their model, "
                        f"{report['empties']} as empties), {report['models_loaded']} different models, "
                        f"in {report['seconds']:g} s. The whole report is in the text 'Map import {report['map']}'.")
    for n in report["not_shown"]:
        op.report({"WARNING"}, f"{n['name']} x{n['count']} shown as empties: {n['why']}")
    for m in report["models_failed"]:
        op.report({"WARNING"}, f"{m['model']}: {m['why']}")
    print("\n".join(report_lines(report)), flush=True)


# ---------------------------------------------------------------------------------------------------- operators
class _Options:
    load_models: BoolProperty(name="Placed models", default=True,
                              description="Load the model of every placed building, plant and unit (each model once). Off: every "
                                          "placed entity is an empty, which is quicker")
    show_water: BoolProperty(name="Water", default=True, description="Add the water surface where the map has water")
    show_creatures: BoolProperty(name="Creatures", default=True,
                                 description="Show the creatures the map places as their real models (each kind built once, in the "
                                             "bind pose). Off: they stay empties, which is quicker")
    game_look: BoolProperty(name="Game look", default=True,
                            description="Show the map as the game draws it: the sun, sky, fog, open sea, the water's own colours "
                                        "and the decals")


class BZ_OT_import_map(bpy.types.Operator, ImportHelper, _Options):
    """Open an Impossible Creatures map (.sgb) to look at: its terrain with the ground textures, the water and every placed
    entity. Edit it in Blender and save it with File > Export > Impossible Creatures Map"""
    bl_idname = "bz.import_map"
    bl_label = "Import Impossible Creatures Map"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sgb"
    filter_glob: StringProperty(default="*.sgb", options={"HIDDEN"})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"There is no map file at {path}")
            return {"CANCELLED"}
        try:
            with open(path, "rb") as f:
                raw = f.read()
            mv = _view().read(path)
        except Exception as exc:
            self.report({"ERROR"}, f"{os.path.basename(path)} could not be read as a map: {exc}")
            return {"CANCELLED"}
        game_dir = prefs.resolve_game_dir(context)[0]
        if game_dir is None:
            self.report({"WARNING"}, "No game found, so the ground textures and the models cannot be looked up: set the "
                                     "game folder in the add-on's preferences")
        try:
            report = import_map(context, mv, game_dir=game_dir, load_models=self.load_models, show_water=self.show_water,
                               show_creatures=self.show_creatures, game_look=self.game_look, source_bytes=raw)
        except MapImportError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _finish(self, report)
        return {"FINISHED"}


class BZ_OT_import_game_map(bpy.types.Operator, _Options):
    """Open one of the game's own maps to look at, read straight from the game's files: its terrain with the ground textures,
    the water and every placed entity"""
    bl_idname = "bz.import_game_map"
    bl_label = "Import a Map from the Game"
    bl_options = {"REGISTER", "UNDO"}

    game_map: EnumProperty(name="Map", items=_map_items,
                           description="The map to open. A map that several game versions carry is listed once for each")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "game_map")
        col.prop(self, "load_models")
        col.prop(self, "show_water")
        col.prop(self, "show_creatures")
        col.prop(self, "game_look")

    def execute(self, context):
        game_dir = prefs.resolve_game_dir(context)[0]
        if game_dir is None:
            self.report({"ERROR"}, "No game found: set the game folder in the add-on's preferences")
            return {"CANCELLED"}
        gm = next((m for m in game_maps(game_dir) if m.key == self.game_map), None)
        if gm is None:
            self.report({"ERROR"}, "Pick a map from the list")
            return {"CANCELLED"}
        try:
            raw = gm.read()
            mv = _view().read_game_map(gm)
        except Exception as exc:
            self.report({"ERROR"}, f"{gm.member} could not be read as a map: {exc}")
            return {"CANCELLED"}
        try:
            report = import_map(context, mv, game_dir=game_dir, load_models=self.load_models, show_water=self.show_water,
                               show_creatures=self.show_creatures, game_look=self.game_look, source_bytes=raw)
        except MapImportError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _finish(self, report)
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(BZ_OT_import_map.bl_idname, text="Impossible Creatures Map (.sgb)")
    self.layout.operator(BZ_OT_import_game_map.bl_idname, text="Impossible Creatures Map from the Game...")


CLASSES = (BZ_OT_import_map, BZ_OT_import_game_map)


def register():
    from . import map_look                        # not imported at the top of this file: map_look imports this one
    map_look.install_hooks()                      # its operators are registered by the package registry (map_look.CLASSES)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    from . import map_look
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    map_look.remove_hooks()
