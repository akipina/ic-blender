"""Mission Editor conveniences, without operators: height map pictures, terrain (.ter) and entity (.ent) files, Object
and Blueprint Lists (crop is in ``map_crop``).

Every change goes through what the save already reads, or the map's current bytes (``current_map``, unsaved edits
included). A map built in memory keeps its source's side files in the .blend, and a save step puts them beside it.
"""
from __future__ import annotations

import base64
import json
import zlib
from types import SimpleNamespace

import numpy as np

import bpy

from ICParse.data import ICData

from . import map_hooks as H
from . import ops_map_export as X
from . import ops_map_import as M

ORDER = 55

KEY_SIBLINGS = "ic_map_siblings_text"       # on a map collection made in memory: the name of the text that holds the files beside the map it came from
KEY_ORIGIN = "ic_map_origin"                # on a map collection made by a crop or a terrain import: what it was made from, in words
SAVE_STEP = "map_tools_siblings"


class ToolError(Exception):
    """A plain-words reason why a map tool could not do its work."""


# ---------------------------------------------------------------------------------------------------- the ICParse modules
def _mod(name: str):
    import importlib
    try:
        return importlib.import_module(f"ICParse.sgb.{name}")
    except ImportError as exc:
        raise ToolError(f"This feature needs a newer ICParse than the one installed (ICParse.sgb.{name} is missing: {exc}). "
                        "Update the add-on's ICParse and try again.") from exc


def heightmap_io():
    return _mod("heightmap_io")


def terrain_io():
    return _mod("terrain_io")


def entities_io():
    return _mod("entities_io")


def objlist():
    return _mod("objlist")


def crop_mod():
    return _mod("crop")


# ---------------------------------------------------------------------------------------------------- files kept beside a map made in memory
def pack_files(files: dict) -> str:
    """``{(sub, rest): bytes}`` -> text (JSON of zlib + base64), for the .blend."""
    rows = {f"{sub}|{rest}": base64.b64encode(zlib.compress(bytes(data), 6)).decode("ascii") for (sub, rest), data in files.items() if data is not None}
    return json.dumps(rows)


def unpack_files(text: str) -> dict:
    out = {}
    for key, blob in json.loads(text or "{}").items():
        sub, _, rest = key.partition("|")
        out[(sub, rest)] = zlib.decompress(base64.b64decode(blob.encode("ascii")))
    return out


def stash_siblings(top, files: dict) -> None:
    """Keep ``files`` (``{(folder, text after the map's name): bytes}``) for ``top``'s saves."""
    old = top.get(KEY_SIBLINGS)
    txt = bpy.data.texts.get(old) if old else None
    if not files:
        return
    if txt is None:
        txt = bpy.data.texts.new(f"Map files {top.name}")
        txt.use_fake_user = True
    txt.clear()
    txt.write(pack_files(files))
    top[KEY_SIBLINGS] = txt.name


def stashed_siblings(top) -> dict:
    name = top.get(KEY_SIBLINGS, "")
    txt = bpy.data.texts.get(name) if name else None
    if txt is None:
        return {}
    try:
        return unpack_files(txt.as_string())
    except Exception:                                          # noqa: BLE001  a damaged text keeps nothing rather than stopping a save
        return {}


def _siblings_step(sctx) -> None:
    """Save step: a map made in memory (nothing beside it) gets the files of the map it came from. Maps opened from a file are not touched."""
    if sctx.source:
        return
    kept = stashed_siblings(sctx.top)
    if not kept:
        return
    added = []
    for (sub, rest), data in sorted(kept.items()):
        key = f"{sub}/{sctx.name}{rest}" if sub else f"{sctx.name}{rest}"
        if key not in sctx.files and data is not None:
            sctx.files[key] = data
            added.append(key)
    if added:
        sctx.add_change("Files kept from the map this one was made from, written beside it: " + ", ".join(added) + ".")


H.register_save_step(SAVE_STEP, _siblings_step, order=90)


# ---------------------------------------------------------------------------------------------------- the map's current bytes
def current_map(context, top):
    """``(map bytes, {(folder, text after the map's name): bytes}, plan, library report)``: the map file and the files beside it as a save
    would write them now, so a tool that works on them sees every edit made in the scene. That is the core edits (``apply_edits``) AND every
    map feature's save step (painted passability, terrain types, water and decals, the Terrain look, the Map settings, edited triggers,
    dialogues and cinematics), run on a save context that is never written. Raises ``ToolError`` / ``ops_map_export.MapSaveError`` in plain
    words (also when a feature's save step refuses the map)."""
    plan = X.build_plan(context, top)
    data, rep = X._edit().apply_edits(plan.raw, paths="auto", **plan.kwargs())
    name = X.map_name(top)
    start = siblings_of(context, top, plan)
    files = {(f"{sub}/{name}{rest}" if sub else f"{name}{rest}"): d for (sub, rest), d in start.items()}
    sctx = H.SaveContext(context=context, top=top, name=name, group=X.group_of(plan, top), folder="", target="", ready_to_drop=False,
                         source=top.get(M.KEY_SOURCE, ""), original=plan.raw, view=plan.mv, sgb=data, report={"changes": [], "warnings": []},
                         files=dict(files), initial_files=files, library=rep)
    try:
        H.run_save_steps(sctx)
    except H.Refused as exc:
        raise ToolError(str(exc)) from None
    beside = {}
    for fname, blob in sctx.files.items():
        if blob is not None:
            sub, _slash, leaf = fname.rpartition("/")
            beside[(sub, leaf[len(name):])] = blob
    return sctx.sgb, beside, plan, rep


def current_bytes(context, top):
    """``(bytes, plan, library report)``: the map file of :func:`current_map`."""
    data, _beside, plan, rep = current_map(context, top)
    return data, plan, rep


def siblings_of(context, top, plan) -> dict:
    """``{(folder, text after the map's name): bytes}`` of the files that sit beside the map (what a save copies)."""
    kept = stashed_siblings(top)
    if kept:
        return kept
    source = top.get(M.KEY_SOURCE, "")
    if not source:
        return {}
    return {(sub, rest): data for sub, rest, data in X.sibling_files(plan, source)}


def import_options(top) -> dict:
    """The import options that rebuild the same picture of the map: models, water, creatures, the game look, as the first import had them."""
    try:
        rep = json.loads(top[M.KEY_REPORT])
    except (KeyError, ValueError, TypeError):
        rep = {}
    return {"load_models": bool(rep.get("models_loaded", 0)) or not rep, "show_water": X.water_of(top) is not None or not rep,
            "show_creatures": "creatures" in rep or not rep, "game_look": "look" in rep or not rep}


def reopen(context, top, data: bytes, name: str, siblings: dict, origin: str, options: dict | None = None):
    """Build a new map collection from ``data`` (in memory) and put the old one aside: its collection stays in the scene, excluded from the
    view. The files of the old map beside it are kept for the new one's saves. Returns the new map's collection."""
    opts = dict(import_options(top) if options is None else options)
    opts["siblings"] = dict(siblings or {})                    # the import steps (triggers, cinematics) read their files from here
    new = H.import_map_bytes(context, data, name, opts)
    if siblings:
        stash_siblings(new, siblings)
    new[KEY_ORIGIN] = origin
    layer = M._layer_collection(context.view_layer.layer_collection, top)
    if layer is not None:
        layer.exclude = True
    terrain = X.terrain_of(new)
    if terrain is not None:
        for o in context.view_layer.objects:
            o.select_set(False)
        terrain.select_set(True)
        context.view_layer.objects.active = terrain
    return new


# ---------------------------------------------------------------------------------------------------- the ground's heights
def terrain_info(top):
    terrain = X.terrain_of(top)
    if terrain is None:
        raise ToolError("The terrain object of this map is not in the scene.")
    return terrain


def scene_heights(context, top):
    """``(height bytes (h, w) as the map would store them from the scene now, MapView, plan)``: the ground as it is in the scene, sculpt
    edits included, rounded to the map's steps and clamped to 0..255."""
    plan = X.build_plan(context, top)
    mv = plan.mv
    if plan.heights is None:
        return mv.heights.copy(), mv, plan
    steps = np.rint(np.asarray(plan.heights, np.float64) / float(mv.height_scale))
    return np.clip(steps, 0, 255).astype(np.uint8), mv, plan


def set_scene_heights(context, top, heights: np.ndarray) -> dict:
    """Set every point of the terrain object to the height bytes ``heights`` ``(h, w)``; returns ``{"changed": n, "largest_m": ...}``.
    Only the heights move (a point keeps its sideways place)."""
    terrain = terrain_info(top)
    w, h = int(terrain["ic_map_width"]), int(terrain["ic_map_height"])
    step = float(terrain.get("ic_height_scale", 0.5))
    a = np.asarray(heights, np.uint8)
    if a.shape != (h, w):
        raise ToolError(f"The heights are {a.shape[1]} x {a.shape[0]} but the terrain has {w} x {h} points.")
    X._flush(terrain)
    me = terrain.data
    if len(me.vertices) != w * h:
        raise ToolError("The terrain no longer has the grid it was opened with, so its heights cannot be set from a picture.")
    co = np.empty(w * h * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mw = np.array(terrain.matrix_world, dtype=np.float64)
    world = co @ mw[:3, :3].T + mw[:3, 3]
    old = world[:, 2].copy()
    world[:, 2] = a.reshape(-1).astype(np.float64) * step
    local = (world - mw[:3, 3]) @ np.linalg.inv(mw[:3, :3]).T
    changed = np.abs(world[:, 2] - old) > 1e-4
    if terrain.mode == "EDIT":                         # the edit mesh keeps its own copy of the points: write there, the mode stays
        import bmesh
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()
        for v, c in zip(bm.verts, local):
            v.co = (float(c[0]), float(c[1]), float(c[2]))
        bmesh.update_edit_mesh(me)
    else:
        me.vertices.foreach_set("co", local.astype(np.float32).reshape(-1))
        me.update()
    return {"changed": int(changed.sum()), "largest_m": float(np.abs(world[:, 2] - old).max()), "step": step, "points": w * h}


# ---------------------------------------------------------------------------------------------------- the placed objects
def entity_rows(context, top) -> list:
    """The Object List rows (``ICParse.sgb.objlist.Row``) of the scene's placed objects, in the scene's order; ``row.ref`` is the object."""
    O = objlist()
    rows = []
    for o in X.entity_objects(top):
        bp = str(o.get("ic_blueprint", ""))
        combined = bool(o.get("ic_combined_creature")) or (not bp and "ic_kind" in o and o.get("ic_kind") == "units")
        try:
            sel = bool(o.select_get())
        except RuntimeError:
            sel = False
        eid = o.get(M.KEY_ENTITY)
        name = o.name.rsplit(" ", 1)[0] if (combined and not bp) else ""
        rows.append(O.make_row(int(eid) if eid is not None else -1, bp, int(o.get("ic_owner", -1)), name, str(o.get("ic_kind", "")), combined, sel, o))
    return rows


def blueprint_rows(context, top) -> list:
    """The Blueprint List (``ICParse.sgb.objlist.BlueprintRow``): the map's blueprint list with how many scene objects use each and how
    many trigger arguments name it."""
    O = objlist()
    raw, mv, gm = X.read_source(context, top)
    palette = dict(mv.board.blueprint_palette)
    for o in X.entity_objects(top):                                       # a blueprint added with "Add object" is in the list the save writes
        bp = str(o.get("ic_blueprint", ""))
        if bp and not any(ICData.normalize(bp) == ICData.normalize(p) for p in palette.values()):
            palette[max(list(palette) + [0]) + 1] = bp
    texts = []
    sib = stashed_siblings(top)
    if not sib:                                                           # ``sibling_files`` only reads the plan's ``gm``
        sib = {(s, r): d for s, r, d in X.sibling_files(SimpleNamespace(gm=gm), top.get(M.KEY_SOURCE, ""))}
    for (sub, rest), data in sib.items():
        if not sub and rest.lower() in (".trg", ".dlg") and data:
            texts.append(bytes(data).decode("latin1"))
    return O.blueprint_rows(palette, [str(o.get("ic_blueprint", "")) for o in X.entity_objects(top)], texts)


def objects_of_blueprint(top, path: str) -> list:
    want = ICData.normalize(path)
    return [o for o in X.entity_objects(top) if ICData.normalize(str(o.get("ic_blueprint", ""))) == want]


def apply_action(context, rows: list, action: str, top=None) -> int:
    """``DELETE`` / ``HIDE`` / ``SELECT`` on the objects of ``rows`` (``UNHIDE`` ignores ``rows``: every placed object of ``top``); returns how
    many objects it touched."""
    n = 0
    if action == "UNHIDE":
        for o in X.entity_objects(top):
            try:
                if o.hide_get() or o.hide_viewport:
                    o.hide_set(False)
                    o.hide_viewport = False
                    n += 1
            except RuntimeError:
                continue
        return n
    objs = [r.ref for r in rows if r.ref is not None]
    if action == "SELECT":
        for o in context.view_layer.objects:
            o.select_set(False)
        last = None
        for o in objs:
            try:
                o.hide_set(False)
                o.select_set(True)
                last = o
                n += 1
            except RuntimeError:
                continue
        if last is not None:
            context.view_layer.objects.active = last
    elif action == "HIDE":
        for o in objs:
            try:
                o.select_set(False)
                o.hide_set(True)
                n += 1
            except RuntimeError:
                continue
    elif action == "DELETE":
        for o in objs:
            bpy.data.objects.remove(o, do_unlink=True)
            n += 1
    else:
        raise ToolError(f"Unknown action {action}.")
    return n


# ---------------------------------------------------------------------------------------------------- entity files
def selected_entity_objects(context, top) -> list:
    out = []
    for o in X.entity_objects(top):
        try:
            if o.select_get():
                out.append(o)
        except RuntimeError:
            continue
    return out


def entity_file_records(context, top, objects: list):
    """``(blueprints, records, left out)`` for an ``.ent`` file from placed objects of the scene (position and turn as they are now)."""
    E = entities_io()
    raw, mv, gm = X.read_source(context, top)
    players = {int(p.index): int(p.player_id) for p in mv.board.players}
    nets: dict = {}
    for e in mv.entities:                                                # the map's own numbers for the blueprints it lists
        if e.blueprint:
            nets.setdefault(ICData.normalize(e.blueprint), e.blueprint_ref)
    nxt = max(list(nets.values()) + list(mv.board.blueprint_palette) + [0]) + 1
    blueprints: dict = {}
    records = []
    left = []
    for o in objects:
        bp = str(o.get("ic_blueprint", ""))
        if not bp:
            left.append(f"{o.name} is a creature combined inside the map: it has no blueprint file, so it is not exported.")
            continue
        key = ICData.normalize(bp)
        if key not in nets:
            nets[key] = nxt
            nxt += 1
        net = nets[key]
        blueprints.setdefault(net, E.EntBlueprint(net, E.FLAG_FILE, bp))
        m, _scale = X._rigid(X._game_matrix(o))
        rot = tuple(float(v) for v in m[:3, :3].T.reshape(-1))
        pos = tuple(float(v) for v in m[:3, 3])
        slot = int(o.get("ic_owner", -1))
        owner = players.get(slot, -1) if slot >= 0 else -1
        eid = o.get(M.KEY_ENTITY)
        records.append(E.EntRecord(int(eid) if eid is not None else 0, net, rot + pos, int(o.get("ic_sound_id", -1)), owner))
    return list(blueprints.values()), records, left


def add_entities_from_file(context, top, ent_bytes: bytes, *, mode: str = "WHERE", drop_to_ground: bool = False) -> dict:
    """Add the entities of an ``.ent`` file to the scene as new placed objects (written into the map on save). ``mode``: ``WHERE`` (they land
    where they were), ``CURSOR`` (the group's middle goes to the 3D cursor). Returns ``{"added": [objects], "skipped": [...], ...}``."""
    E = entities_io()
    ent = E.parse_file(ent_bytes)
    raw, mv, gm = X.read_source(context, top)
    from . import prefs
    game_dir = prefs.resolve_game_dir(context)[0]
    rig = M._addon("rig")
    by_pid = {int(p.player_id): int(p.index) for p in mv.board.players if int(p.player_id) >= 0}
    usable = [r for r in ent.records if (bp := ent.blueprint_of(r)) is not None and bp.flags & E.FLAG_FILE and bp.path]
    out = {"added": [], "skipped": [], "owners_lost": 0, "moved": (0.0, 0.0)}
    for r in ent.records:
        bp = ent.blueprint_of(r)
        if r not in usable:
            out["skipped"].append(f"Entity {r.entity_id} is " + ("a creature combined in the exporting map" if bp is not None and bp.flags & E.FLAG_COMBINED
                                                                else "a blueprint the file does not name") + ", so it was not imported.")
    dx = dz = 0.0
    if mode == "CURSOR" and usable:
        cur = context.scene.cursor.location
        cx = float(np.mean([r.position[0] for r in usable]))
        cz = float(np.mean([r.position[2] for r in usable]))
        dx, dz = float(cur.x) - cx, float(-cur.y) - cz              # the game's z is Blender's -y
    out["moved"] = (dx, dz)
    terrain = X.terrain_of(top)
    for r in usable:
        bp = ent.blueprint_of(r)
        ref = ICData.normalize(bp.path)
        m = np.eye(4)
        m[:3, :3] = np.asarray(r.transform[:9], np.float64).reshape(3, 3).T
        x, y, z = r.position
        x, z = x + dx, z + dz
        m[:3, 3] = (x, y, z)
        obj, why = X.new_entity_object(context, top, mv, ref, (0.0, 0.0, 0.0), game_dir)
        if drop_to_ground and terrain is not None:
            g = X.ground_height_at(terrain, x, -z)
            if g is not None:
                m[1, 3] = g
        obj.matrix_world = rig.to_blender_matrix(m)
        slot = by_pid.get(r.owner_id, -1) if r.owner_id >= 0 else -1
        if r.owner_id >= 0 and slot < 0:
            out["owners_lost"] += 1
        obj["ic_owner"] = slot
        obj["ic_player_id"] = r.owner_id if slot >= 0 else -1
        obj["ic_sound_id"] = int(r.sound_id)
        out["added"].append(obj)
    return out
