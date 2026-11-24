"""sceneobj.ops_map_export -- edit an imported Impossible Creatures map in Blender and save it as a map (.sgb) again.

In plain words. The map import (``ops_map_import``) shows a map as Blender objects. This module reads the changes back and writes
the map file: File > Export > "Impossible Creatures Map (.sgb)", or the Save Map button of the Map panel (``panels_map``).

What you can change, and how it is read back (every edit is read against the map file the scene was opened from, which is read again
at save time, so a save never depends on an earlier one):

* the ground -- sculpt or move the terrain's points up and down (Sculpt Mode, proportional editing). Each point's height is read
  back. The terrain must keep its grid: the same points in the same order (the game stores one height per grid point), so a terrain
  with points added or removed is refused. The map stores a height in steps (0.5 m in most stock maps); the save report says how
  many heights had to be rounded or clamped. A point pushed sideways keeps only its height;
* the water -- moving the water surface up or down sets the map's water level;
* the ground textures -- give faces one of the map's ground materials (or one added with "Add ground texture"). Each 8 x 8 block of
  cells takes the material most of its faces have (a tie keeps the block's own);
* the placed objects -- moving or turning an object moves that entity; deleting one removes it; a copy (Shift+D, Alt+D) is a new
  entity with the same blueprint; "Add object" places a blueprint from the game's own list at the 3D cursor on the ground;
  "Set owner" and "Drop to ground" work on the selected ones. Scale is ignored (the map stores a position and a turn only). A creature
  combined in the map has no blueprint file, so a copy of one is left out and said so;
* nothing else: an object you did not touch is written back exactly as it was read, so a map saved with no edits is byte for byte the
  map that was opened.

Where it writes. Into a folder the user picks, never into the game's own folder (a Steam game folder is refused: Steam rewrites it).
Next to the map goes every file that sits beside it in the game's files and belongs to it (its triggers ``.trg``, dialogue, preview
pictures ...), copied unchanged. The second option writes a ready-to-drop folder ``Data/scenarios/mp/<name>.sgb`` with a README that
says how to put it into a game copy the user owns (loose files in the game's ``Data`` folder load and win over the archives: CLAUDE.md,
App_SetupFilePathAliases).

The byte work is done by ``ICParse.sgb.edit`` (``apply_edits``, ``pathfinding_fidelity``): this module gives it the heights, the water
level, the per-block ground textures and the complete list of entities, and passes nothing for what was not changed.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector

from . import map_hooks as H
from . import ops_map_import as M
from . import panels_blueprint as PB
from . import prefs

ORDER = 61

KEY_NEW = "ic_map_new_object"           # on an object added with "Add object"
SIDE_WORDS = {"structures": "structures", "nature": "nature", "units": "units", "other": "other"}
LOOSE_SUFFIXES = ("_decaled", "_decaled_small", "_icon", "_mm")     # the picture files that carry a map's name and belong to it
LAST_CHECK: dict = {}                    # map collection name -> the lines of the last "Check my changes"


class MapSaveError(Exception):
    """A plain-words reason why a map could not be read from the scene or saved."""


def _addon(name: str):
    return M._addon(name)


def _edit():
    """``ICParse.sgb.edit`` or a plain reason why the save is not available."""
    try:
        return importlib.import_module("ICParse.sgb.edit")
    except ImportError as exc:
        raise MapSaveError("Saving maps needs a newer ICParse than the one installed (ICParse.sgb.edit is missing: "
                           f"{exc}). Update the add-on's ICParse and try again.") from exc


def library_problem() -> str:
    """"" when saving is available, else the reason in plain words (the panel shows it)."""
    try:
        _edit()
    except MapSaveError as exc:
        return str(exc)
    return ""


# ---------------------------------------------------------------------------------------------------- finding the map in the scene
def map_collections(scene) -> list:
    """Every imported map of the scene (its top collection)."""
    return [c for c in scene.collection.children_recursive if c.get(M.KEY_MAP)]


def active_map(context):
    """The map the active object belongs to, else the first map of the scene, else None."""
    maps = map_collections(context.scene)
    if not maps:
        return None
    obj = context.active_object
    if obj is not None and len(maps) > 1:
        for top in maps:
            inside = {top, *top.children_recursive}
            if any(c in inside for c in obj.users_collection):
                return top
    return maps[0]


def map_by_name(name: str):
    coll = bpy.data.collections.get(name) if name else None
    return coll if coll is not None and coll.get(M.KEY_MAP) else None


def _own_objects(top, kind: str):
    """The objects of the map's ``<name> <kind>`` collection (cheap), then every object of the map (when someone moved them)."""
    own = next((c for c in top.children if c.name.endswith(f" {kind}")), None)
    if own is not None and len(own.objects):
        yield from own.objects
    yield from top.all_objects


def terrain_of(top):
    for o in _own_objects(top, "terrain"):
        if o.type == "MESH" and o.get(M.KEY_MAP):
            return o
    return None


def water_of(top):
    for o in _own_objects(top, "water"):
        if o.type == "MESH" and "ic_water_level" in o and not o.get(M.KEY_MAP):
            return o
    return None


def entity_objects(top) -> list:
    """The placed entities of the map: every object with a blueprint, not the parts of the loaded models."""
    return [o for o in top.all_objects if "ic_blueprint" in o and M.KEY_MODEL not in o]


def hidden_collection(top):
    return next((c for c in top.children if c.name.endswith("(hidden)")), None)


def map_name(top) -> str:
    try:
        return str(json.loads(top[M.KEY_REPORT])["map"])
    except (KeyError, ValueError, TypeError):
        return top.name[4:] if top.name.startswith("Map ") else top.name


def _flush(obj) -> None:
    """Edit Mode keeps its changes in its own copy of the mesh: write them into the mesh so they can be read (the mode stays)."""
    if obj is not None and obj.mode == "EDIT":
        obj.update_from_editmode()


def _ensure_object_mode(context) -> str:
    """Leave Sculpt Mode and the paint modes so the mesh holds every change (Edit Mode is flushed instead and stays); returns a plain
    note ("" when nothing was left)."""
    obj = context.view_layer.objects.active
    if obj is None or obj.mode in ("OBJECT", "EDIT"):
        _flush(obj)
        return ""
    mode = obj.mode
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:                                    # noqa: BLE001  (no screen in a background run)
        return ""
    return f"Left {mode.replace('_', ' ').title()} Mode so every change of the terrain is read."


# ---------------------------------------------------------------------------------------------------- the map's source file
_SOURCES: dict = {}          # map collection name -> (identity, raw bytes, MapView, GameMap or None)


def _source_identity(top, game_dir) -> tuple:
    memory = top.get(H.KEY_SOURCE_TEXT, "")
    if memory:                                            # a map made in memory (map_hooks.import_map_bytes): its bytes are in a text
        txt = bpy.data.texts.get(memory)
        return ("memory", memory, len(txt.as_string()) if txt is not None else -1)
    src = top.get(M.KEY_SOURCE, "")
    archive = top.get(M.KEY_ARCHIVE, "")
    if archive:
        return (src, archive, game_dir or "")
    try:
        st = os.stat(src)
        return (src, st.st_size, st.st_mtime_ns)
    except OSError:
        return (src, -1, -1)


def read_source(context, top):
    """``(raw bytes, MapView, GameMap or None)`` of the file the map was opened from, read again (kept until it changes)."""
    game_dir = prefs.resolve_game_dir(context)[0]
    ident = _source_identity(top, game_dir)
    hit = _SOURCES.get(top.name)
    if hit is not None and hit[0] == ident:
        return hit[1:]
    if ident[0] == "memory":
        txt = bpy.data.texts.get(ident[1])
        if txt is None:
            raise MapSaveError("The map was made in memory and the text that holds its original bytes is gone, so the changes cannot be "
                               "compared with the original. Make the map again.")
        try:
            raw = H.unpack_source(txt.as_string())
            mv = M._view().read(raw)
        except Exception as exc:                          # noqa: BLE001
            raise MapSaveError(f"The original map could not be read: {exc}") from exc
        _SOURCES[top.name] = (ident, raw, mv, None)
        return raw, mv, None
    src = top.get(M.KEY_SOURCE, "")
    archive = top.get(M.KEY_ARCHIVE, "")
    view = M._view()
    gm = None
    if archive:
        if not game_dir:
            raise MapSaveError(f"The map was opened from {archive} in the game's files, and no game folder was found now: set the "
                               "game folder in the add-on's preferences, then save again.")
        gm = next((m for m in M.game_maps(game_dir) if m.key == src), None)
        if gm is None:
            raise MapSaveError(f"The map {src.split('|')[-1]} is not in the game's files any more, so the changes cannot be compared "
                               "with the original. Open the map again from the game's files.")
        try:
            raw = gm.read()
        except Exception as exc:                          # noqa: BLE001
            raise MapSaveError(f"The original map could not be read from {archive}: {exc}") from exc
    else:
        if not src or not os.path.isfile(src):
            raise MapSaveError(f"The map file it was opened from is not there any more ({src or 'unknown'}). The changes are read "
                               "against that file: put it back, or open the map again and redo the changes.")
        with open(src, "rb") as f:
            raw = f.read()
    try:
        mv = view.read(raw, source=src, archive=archive)
    except Exception as exc:                              # noqa: BLE001
        raise MapSaveError(f"The original map could not be read: {exc}") from exc
    _SOURCES[top.name] = (ident, raw, mv, gm)
    return raw, mv, gm


# ---------------------------------------------------------------------------------------------------- the game's own lists
_LISTINGS: dict = {}


def game_listing(game_dir: str, archive: str = "") -> dict:
    """``{"ground": [file names], "blueprints": [(reference, kind)]}`` from the game's files (the loose ``Data`` folder and the archives
    of the build that ships the map): the ground textures (``detail/*.tga``) and every blueprint (``art/ebps/**/*.ebp``)."""
    if not game_dir:
        return {"ground": [], "blueprints": []}
    stamp = (M._stamp(game_dir), archive)
    hit = _LISTINGS.get((game_dir, archive))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    view = M._view()
    members: list = []
    data_dir = view._child(game_dir, "Data")
    if data_dir:
        for sub in ("detail", "art"):
            root = view._child(data_dir, sub)
            for dirpath, _d, files in os.walk(root) if root else ():
                members += [os.path.relpath(os.path.join(dirpath, f), data_dir).replace(os.sep, "/").lower() for f in files]
    import ICParse.sga as sga
    for path in view.archive_order(game_dir, archive):
        try:
            arc = sga.read(path)
        except Exception:                                 # noqa: BLE001
            continue
        members += [e.path.replace("\\", "/").lower() for e in arc.entries]
    ground, blueprints, seen = [], [], set()
    for m in members:
        if m in seen:
            continue
        seen.add(m)
        if m.startswith("detail/") and m.endswith(".tga") and m.count("/") == 1:
            ground.append(m[len("detail/"):])
        elif m.startswith("art/ebps/") and m.endswith(".ebp"):
            blueprints.append((m, view.entity_kind(m)))
    out = {"ground": sorted(ground), "blueprints": sorted(blueprints, key=lambda b: (b[1], b[0]))}
    _LISTINGS[(game_dir, archive)] = (stamp, out)
    return out


def blueprint_words(ref: str) -> str:
    """``art/ebps/nature/savtree_01.ebp`` -> ``nature / savtree_01``."""
    parts = ref.split("/")
    return f"{'/'.join(parts[2:-1])} / {os.path.splitext(parts[-1])[0]}"


# ---------------------------------------------------------------------------------------------------- reading the changes
@dataclass
class Plan:
    """Everything a save needs, read from the scene: what to pass to ``apply_edits`` and what to tell the user."""
    name: str
    raw: bytes
    mv: object
    gm: object = None
    heights: np.ndarray | None = None
    height_points: int = 0              # how many points were moved
    height_largest: float = 0.0         # the largest move, metres
    water_level: float | None = None    # metres
    water_move: float = 0.0
    blocks: np.ndarray | None = None    # per 8 x 8 block: the ground texture name (an array of strings, view.py's layout)
    block_count: int = 0
    entities: list | None = None        # [(id or None, blueprint, 4 x 4 matrix, owner or None)]
    moved: int = 0
    added: int = 0
    removed: int = 0
    owners: int = 0
    notes: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.heights is not None or self.water_level is not None or self.blocks is not None or self.entities is not None)

    def kwargs(self) -> dict:
        """The arguments of ``apply_edits`` (only what was changed)."""
        out: dict = {}
        if self.heights is not None:
            out["heights"] = self.heights
        if self.water_level is not None:
            out["water_level"] = self.water_level
        if self.blocks is not None:
            out["block_textures"] = self.blocks
        if self.entities is not None:
            edit = _edit()
            out["entities"] = [edit.EntityEdit(id=i, blueprint=b, matrix=m, owner=o) for i, b, m, o in self.entities]
        return out

    def lines(self) -> list:
        """What was changed, in plain words."""
        out = []
        step = self.mv.height_scale
        if self.heights is not None:
            out.append(f"Ground: {self.height_points} height points moved (the largest by {self.height_largest:.2f} m). "
                       f"The map keeps heights in steps of {step:g} m.")
        if self.water_level is not None:
            out.append(f"Water: the surface moved by {self.water_move:+.2f} m, to {self.water_level:.2f} m.")
        if self.blocks is not None:
            out.append(f"Ground textures: {self.block_count} blocks of 8 x 8 cells change their texture.")
        if self.entities is not None:
            bits = [f"{n} {w}" for n, w in ((self.moved, "moved or turned"), (self.added, "added"), (self.removed, "removed"),
                                            (self.owners, "given to another owner")) if n]
            out.append("Placed objects: " + (", ".join(bits) if bits else "none changed") + ".")
        if not self.changed:
            out.append("Nothing changed: saving writes the map file byte for byte as it was opened.")
        out += [f"Note: {n}" for n in self.notes]
        return out


def _tol_height(step: float) -> float:
    return max(1e-4, min(5e-3, 0.02 * step))


def _read_terrain(plan: Plan, top) -> None:
    mv = plan.mv
    terrain = terrain_of(top)
    if terrain is None:
        plan.notes.append("The terrain object is not in the scene, so the ground, the water level and the ground textures are kept as they were.")
        return
    me = terrain.data
    _flush(terrain)
    w, h = mv.width, mv.height
    nv, nf = len(me.vertices), len(me.polygons)
    quads = mv.quads()
    if nv != w * h or nf != len(quads):
        raise MapSaveError(f"The terrain no longer has the grid it was opened with: {nv} points and {nf} faces now, {w * h} and "
                           f"{len(quads)} when the map was opened. The map stores one height for each point of a fixed grid, so points "
                           "cannot be added, removed or merged. Undo the change (Ctrl+Z) or open the map again.")
    loops = np.empty(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", loops)
    if len(loops) != quads.size or not (np.sort(loops.reshape(-1, 4), axis=1) == np.sort(quads, axis=1)).all():
        raise MapSaveError("The terrain's faces no longer join the same points in the same order as when the map was opened (points or "
                           "faces were rearranged, joined or split). The map stores one height for each point of a fixed grid. "
                           "Undo the change (Ctrl+Z) or open the map again.")
    co = np.empty(nv * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mw = np.array(terrain.matrix_world, dtype=np.float64)
    world = co @ mw[:3, :3].T + mw[:3, 3]
    core = _addon("core")
    base = mv.vertex_positions(np.float64)
    expect = core.sgm_to_blender(base)                    # the points where the map's grid put them
    step = float(mv.height_scale)
    # ---- sideways: the map stores no sideways position
    side = world[:, :2] - expect[:, :2]
    shift = np.median(side, axis=0)
    if float(np.abs(shift).max()) > 0.25 * mv.tile_scale:
        plan.notes.append(f"The terrain object was moved sideways by {shift[0]:.1f} m, {shift[1]:.1f} m. The map's ground cannot move, "
                          "so only the heights were read, and the placed objects keep their own positions.")
    drift = np.abs(side - shift).max(axis=1)
    far = int((drift > 0.5 * mv.tile_scale).sum())
    if far:
        plan.notes.append(f"{far} terrain points were pushed sideways by more than half a cell. The map stores heights only, so only "
                          "the height of those points was read.")
    # ---- heights
    orig = mv.heights.astype(np.float64) * step           # metres, as the map holds them
    new = world[:, 2].reshape(h, w)
    diff = np.abs(new - orig)
    moved = diff > _tol_height(step)
    if moved.any():
        out = orig.copy()
        out[moved] = new[moved]
        plan.heights = out
        plan.height_points = int(moved.sum())
        plan.height_largest = float(diff[moved].max())
    _read_blocks(plan, terrain)


def _read_blocks(plan: Plan, terrain) -> None:
    mv = plan.mv
    if mv.block_ids.size == 0:
        plan.notes.append("The map has no ground texture table, so the ground textures are kept as they were.")
        return
    me = terrain.data
    nf = len(me.polygons)
    mi = np.empty(nf, np.int32)
    me.polygons.foreach_get("material_index", mi)
    slot_key = []                                          # the lower-case texture file name of each material slot, "" for none
    slot_name = []
    for mat in me.materials:
        name = str(mat.get("ic_ground_texture", "")) if mat is not None else ""
        slot_name.append(name)
        slot_key.append(name.lower())
    keys = sorted({k for k in slot_key if k})
    index = {k: i for i, k in enumerate(keys)}
    face_key = np.array([index.get(slot_key[s], -1) if 0 <= s < len(slot_key) else -1 for s in mi], dtype=np.int64)
    nbx, nbz = mv.blocks
    blocks = nbx * nbz
    fb = mv.face_blocks()
    counts = np.zeros((blocks, max(1, len(keys))), np.int64)
    ok = face_key >= 0
    np.add.at(counts, (fb[ok], face_key[ok]), 1)
    names_by_id = {t.texid: t.name for t in mv.textures}
    orig_names = np.array([names_by_id.get(int(i), "") for i in mv.block_ids.reshape(-1)], dtype=object)
    orig_key = np.array([index.get(n.lower(), -1) for n in orig_names], dtype=np.int64)
    best = counts.argmax(axis=1)
    top_count = counts.max(axis=1)
    own = np.where(orig_key >= 0, counts[np.arange(blocks), np.clip(orig_key, 0, None)], -1)
    take = (top_count > 0) & (best != orig_key) & (own < top_count)       # a tie keeps the block's own texture
    if not take.any():
        return
    display = {}
    for n in slot_name:
        display.setdefault(n.lower(), n)
    out = orig_names.copy()
    for b in np.flatnonzero(take):
        out[b] = display[keys[int(best[b])]]
    plan.blocks = out.reshape(mv.block_ids.shape)
    plan.block_count = int(take.sum())


def _read_water(plan: Plan, top) -> None:
    mv = plan.mv
    water = water_of(top)
    if water is None:
        return
    wpos, _q, _v = mv.water_mesh(np.float64)
    level_m = water.get("ic_look_level_m")          # a water surface that was drawn again, or added to a map with no water points:
    if level_m is not None:                         # the level it was drawn at, moved by how far the object has moved since
        level = float(level_m) + float(water.matrix_world.translation.z)
        if abs(level - mv.water_height) > _tol_height(mv.height_scale):
            plan.water_level, plan.water_move = level, level - mv.water_height
        return
    if not len(wpos):
        return
    me = water.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    mw = np.array(water.matrix_world, dtype=np.float64)
    z = co @ mw[2, :3] + mw[2, 3]
    move = float(np.median(z) - np.median(wpos[:, 1]))
    if abs(move) > _tol_height(mv.height_scale):
        plan.water_level = float(mv.water_height + move)
        plan.water_move = move


# ---- the placed objects
def _game_matrix(obj) -> np.ndarray:
    return _addon("rig").from_blender_matrix(obj.matrix_world)


def _rigid(m: np.ndarray):
    """The matrix with its scale taken out (a pure turn and position), and the scale."""
    u, s, vt = np.linalg.svd(m[:3, :3])
    if np.linalg.det(u @ vt) < 0:
        u[:, -1] *= -1
    out = np.eye(4)
    out[:3, :3] = u @ vt
    out[:3, 3] = m[:3, 3]
    return out, s


def _same_place(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.abs(a[:3, 3] - b[:3, 3]).max() <= 1e-3 and np.abs(a[:3, :3] - b[:3, :3]).max() <= 1e-4)


def _read_entities(plan: Plan, top) -> None:
    mv = plan.mv
    src = {e.instance_id: e for e in mv.entities}
    by_id: dict = {}
    fresh: list = []
    for o in entity_objects(top):
        eid = o.get(M.KEY_ENTITY)
        if eid is None or int(eid) not in src:
            fresh.append(o)
        else:
            by_id.setdefault(int(eid), []).append(o)
    rows = []
    stray = 0
    for eid, objs in by_id.items():
        if len(objs) > 1:                                  # a copy carries the id of the original: the one that stayed is the original
            target = src[eid].matrix()
            objs.sort(key=lambda o: (float(np.abs(_game_matrix(o)[:3, 3] - target[:3, 3]).sum()), o.name != f"{src[eid].name} {eid}"))
            fresh += objs[1:]
        by_id[eid] = objs[:1]
    scaled = 0
    changed = False
    for e in mv.entities:                                  # the map's own order, then the new ones
        objs = by_id.get(e.instance_id)
        if not objs:
            plan.removed += 1
            changed = True
            continue
        obj = objs[0]
        now, s = _rigid(_game_matrix(obj))
        if np.abs(s - 1.0).max() > 1e-3:
            scaled += 1
        base = e.matrix()
        if _same_place(now, base):
            matrix = base                                   # untouched: the very numbers the map held
        else:
            matrix = now
            plan.moved += 1
            changed = True
        owner = int(obj.get("ic_owner", e.owner))
        if owner != e.owner:
            plan.owners += 1
            changed = True
        rows.append((e.instance_id, str(obj.get("ic_blueprint", e.blueprint)), matrix, owner if owner >= 0 else None))
    for obj in fresh:
        blueprint = str(obj.get("ic_blueprint", ""))
        if not blueprint:
            stray += 1
            continue
        now, s = _rigid(_game_matrix(obj))
        if np.abs(s - 1.0).max() > 1e-3:
            scaled += 1
        owner = int(obj.get("ic_owner", -1))
        rows.append((None, blueprint, now, owner if owner >= 0 else None))
        plan.added += 1
        changed = True
    if stray:
        plan.notes.append(f"{stray} copies of creatures combined inside the map were left out: such a creature has no blueprint "
                          "file, its recipe lives only in the map, so it cannot be copied.")
    if scaled:
        plan.notes.append(f"{scaled} placed objects were scaled. The map stores a position and a turn only, so the scale is ignored.")
    if changed:
        plan.entities = rows


def build_plan(context, top) -> Plan:
    """Read every change from the scene (nothing is written): raises :class:`MapSaveError` when the map cannot be saved as it is."""
    raw, mv, gm = read_source(context, top)
    plan = Plan(name=map_name(top), raw=raw, mv=mv, gm=gm)
    context.view_layer.update()                            # a position set by a script is in the object's matrix only after this
    note = _ensure_object_mode(context)
    if note:
        plan.notes.append(note)
    _read_terrain(plan, top)
    _read_water(plan, top)
    _read_entities(plan, top)
    return plan


# ---------------------------------------------------------------------------------------------------- the files beside the map
def sibling_files(plan: Plan, source: str) -> list:
    """``[(folder under the map's folder, the text after the map's name, bytes)]``: what sits beside the map in the game's files (or on
    disk) and belongs to it, not the map itself."""
    stem = os.path.splitext(os.path.basename(source.split("|")[-1]))[0].lower()
    out = []
    if not source:                                        # a map made in memory has nothing beside it
        return out

    def wanted(base: str) -> str | None:
        low = base.lower()
        if not low.startswith(stem):
            return None
        rest = base[len(stem):]
        if rest.lower() == ".sgb" or not rest:
            return None
        if rest.startswith(".") or rest.lower().split(".")[0] in LOOSE_SUFFIXES:
            return rest
        return None

    if plan.gm is not None:
        import ICParse.sga as sga
        arc = sga.read(plan.gm.path)
        folder = plan.gm.member.rsplit("/", 1)[0] if "/" in plan.gm.member else ""
        seen = set()
        for entry in arc.entries:
            member = entry.path.replace("\\", "/").lower()
            head, _, base = member.rpartition("/")
            sub = "" if head == folder else ("loading" if head == f"{folder}/loading" else None)
            if sub is None or member in seen:
                continue
            seen.add(member)
            rest = wanted(base)
            if rest is not None and (sub == "" or rest.startswith(".")):
                out.append((sub, rest, arc.read(entry)))
    else:
        folder = os.path.dirname(source)
        for sub in ("", "loading"):
            where = os.path.join(folder, sub) if sub else folder
            try:
                names = sorted(os.listdir(where))
            except OSError:
                continue
            for n in names:
                rest = wanted(n)
                path = os.path.join(where, n)
                if rest is not None and (sub == "" or rest.startswith(".")) and os.path.isfile(path):
                    with open(path, "rb") as f:
                        out.append((sub, rest, f.read()))
    return out


README = """How to put this map into a copy of Impossible Creatures

This folder was written by the Impossible Creatures add-on for Blender. It holds one map and the files that belong to it:

    Data/scenarios/{group}/{name}.sgb      the map
    Data/scenarios/{group}/{name}.*        the files that sat beside the original map, copied unchanged

To play it:

1. Use a copy of the game that you own and look after yourself. Do not use the folder Steam manages: Steam rewrites that folder and
   removes files it did not put there.
2. Copy the Data folder from here into the game's folder (the folder that holds IC.exe), so the files end up in
   Data/scenarios/{group}/ next to the game's own. Say yes if it asks to merge the folders.
3. Start the game. The game reads loose files in its Data folder before the ones inside its archives, so the map should be in the
   map list. This has not been tried in the game from this add-on: if the map does not show up, tell whoever gave you the add-on.

To take the map out again, delete the {name} files from Data/scenarios/{group}/.

The map was saved from: {source}
{renamed}Changes made in Blender:
{changes}
"""


def _clean_name(raw: str, *, strict: bool) -> str:
    name = os.path.splitext(os.path.basename(raw))[0].strip()
    name = name.lower() if strict else name
    name = re.sub(r"[^A-Za-z0-9_]+" if strict else r'[\\/:*?"<>|]+', "_", name).strip("_ ")
    return name or "map"


def group_of(plan, top) -> str:
    """``"sp"`` or ``"mp"``: the scenarios folder the map belongs in (the kind chosen in the Map settings, else where it came from)."""
    settings = getattr(top, "ic_settings", None)
    if settings is not None and settings.ready:            # Map settings: the kind the user chose decides the folder
        return "sp" if int(settings.kind) == 1 else "mp"
    return "sp" if plan.gm is not None and plan.gm.group == "sp" else "mp"


def save_map(context, top, filepath: str, *, ready_to_drop: bool = False, replace_original: bool = False,
             paths: str = "auto") -> dict:
    """Write the map. ``filepath`` names the map file (the folder is where it goes; with ``ready_to_drop`` the folder is the root
    of ``Data/scenarios/<group>/``). Returns the report (a dict); raises :class:`MapSaveError` with a plain reason."""
    t0 = time.perf_counter()
    if not filepath or not str(filepath).strip():
        raise MapSaveError("Choose where to save the map.")
    path = os.path.abspath(bpy.path.abspath(filepath))
    folder = os.path.dirname(path)
    try:
        PB.check_target(folder)
    except PB.Refused as exc:
        raise MapSaveError(str(exc)) from None
    edit = _edit()
    plan = build_plan(context, top)
    name = _clean_name(path, strict=ready_to_drop)
    source = top.get(M.KEY_SOURCE, "")
    group = group_of(plan, top)
    if ready_to_drop:
        root = folder
        folder = os.path.join(root, "Data", "scenarios", group)
    target = os.path.join(folder, f"{name}.sgb")
    if (not replace_original and not top.get(M.KEY_ARCHIVE) and source and os.path.isfile(target)
            and os.path.realpath(target) == os.path.realpath(source)):
        raise MapSaveError("That would replace the map file this map was opened from. Pick another folder or name, or turn on "
                           "'Replace the original file'.")
    try:
        new_bytes, rep = edit.apply_edits(plan.raw, paths=paths, **plan.kwargs())
    except MapSaveError:
        raise
    except Exception as exc:                               # noqa: BLE001
        raise MapSaveError(f"The map could not be written: {exc}") from exc
    fidelity = None
    if _paths_mode(rep) != "kept":                         # how far a rebuild from the terrain is from the game's own, on this very map
        try:
            fidelity = edit.pathfinding_fidelity(plan.raw)
        except Exception as exc:                           # noqa: BLE001
            plan.notes.append(f"How closely the rebuilt pathfinding matches the game's own could not be measured: {exc}")
    files = {(f"{sub}/{name}{rest}" if sub else f"{name}{rest}"): data for sub, rest, data in sibling_files(plan, source)}
    sctx = H.SaveContext(context=context, top=top, name=name, group=group, folder=folder, target=target, ready_to_drop=ready_to_drop,
                         source=source, original=plan.raw, view=plan.mv, sgb=new_bytes, report={"changes": [], "warnings": []},
                         files=dict(files), initial_files=files, library=rep)
    try:                                                   # the map features' steps: nothing is written until they have all run
        H.run_save_steps(sctx)
    except H.Refused as exc:
        raise MapSaveError(str(exc)) from None
    new_bytes = sctx.sgb
    feature_files = [n for n, d in sctx.files.items() if d is not None and files.get(n) != d]
    feature_lines = list(sctx.report.get("changes", ()))
    feature_warnings = list(sctx.report.get("warnings", ()))
    outputs = [(os.path.join(folder, *fname.replace("\\", "/").split("/")), bytes(data)) for fname, data in sctx.files.items() if data is not None]
    if ready_to_drop:
        stem = os.path.splitext(os.path.basename(source.split("|")[-1]))[0]
        renamed = ""
        if stem and stem.lower() != name:
            renamed = (f"The map was renamed from {stem} to {name}. The files beside it keep their contents; the first line of the .trg "
                       f"is a comment that still names {stem}, which changes nothing.\n")
        text = README.format(group=group, name=name, source=source.split("|")[-1] or "an unsaved scene", renamed=renamed,
                             changes="\n".join(f"  - {line}" for line in plan.lines() + feature_lines))
        outputs.append((os.path.join(root, "README.txt"), text.encode("utf-8")))
    written = [target] + [path_ for path_, _blob in outputs]
    done = []
    try:                                                   # the map file goes last: a failed write cannot leave a new map file without its files
        for path_, blob in outputs + [(target, new_bytes)]:
            PB.write_file(path_, blob)
            done.append(path_)
    except OSError as exc:
        raise MapSaveError(f"The map could not be written: {exc}. " + (f"Files already written: {', '.join(done)}." if done else "Nothing was written.")) from exc
    report = {"map": plan.name, "name": name, "path": target, "files": written, "bytes": len(new_bytes),
              "identical": new_bytes == plan.raw, "lines": plan.lines(), "library": rep, "fidelity": fidelity,
              "ready_to_drop": ready_to_drop, "feature_changes": feature_lines, "feature_warnings": feature_warnings,
              "feature_files": [os.path.join(folder, *n.split("/")) for n in feature_files],
              "seconds": round(time.perf_counter() - t0, 2)}
    report["text"] = report_lines(report)
    _report_text(report)
    return report


def _paths_mode(rep) -> str:
    """"kept", "patched" or "rebuilt": what ``apply_edits`` did to the pathfinding records."""
    return str(rep.get("paths", {}).get("mode", "kept")) if isinstance(rep, dict) else "kept"


def _library_lines(rep) -> list:
    """What ``apply_edits`` wrote, in plain words (its report is a dict of counts)."""
    if not isinstance(rep, dict):
        return []
    out = []
    t = rep.get("terrain", {})
    if t.get("heights_changed"):
        bits = [f"{t['heights_changed']} height points written"]
        if t.get("heights_rounded"):
            largest = "" if t.get("heights_clamped") else f" (the largest by {t.get('max_rounding_error', 0):.2f} m)"
            bits.append(f"{t['heights_rounded']} of them rounded to the map's steps{largest}")
        if t.get("heights_clamped"):
            bits.append(f"{t['heights_clamped']} cut to the lowest or highest height the map can store")
        out.append("Ground: " + ", ".join(bits) + ".")
    if t.get("water_level_changed"):
        line = f"Water: the level is now step {t.get('water_level')} ({t.get('water_cells_moved', 0)} water points follow it"
        if t.get("water_cells_kept"):
            line += f", {t['water_cells_kept']} ponds or bogs at their own height stay"
        out.append(line + ").")
    x = rep.get("textures", {})
    if x.get("blocks_changed") or x.get("added"):
        line = f"Ground textures: {x.get('blocks_changed', 0)} blocks changed"
        if x.get("added"):
            line += "; new in the map: " + ", ".join(a["name"] for a in x["added"])
        out.append(line + ".")
    e = rep.get("entities", {})
    bits = [f"{len(e[k])} {w}" for k, w in (("moved", "moved"), ("added", "added"), ("removed", "removed"),
                                           ("blueprint_changed", "with another blueprint"), ("owner_changed", "with another owner"))
            if e.get(k)]
    if bits:
        out.append("Placed objects: " + ", ".join(bits) + f", {e.get('unchanged', 0)} left as they were.")
    if e.get("palette_added"):
        out.append("New in the map's blueprint list: " + ", ".join(p.split("\\")[-1] for p in e["palette_added"]) + ".")
    p = rep.get("paths", {})
    mode = p.get("mode", "kept")
    if mode == "patched":
        out.append(f"Pathfinding: updated ({p.get('impa_cells_changed', 0)} passability cells and {p.get('prmp_cells_changed', 0)} "
                   f"precise cells changed): {p.get('reason', '')}".rstrip(": ") + ".")
    elif mode == "rebuilt":
        how = "with the game's own method" if p.get("engine", "pathbake") == "pathbake" else "with the older estimate"
        out.append(f"Pathfinding: rebuilt {how} from the ground, the painted cells and the scenery ({p.get('reason', '')}).")
    else:
        out.append("Pathfinding: kept as it was" + (f" ({p['reason']})" if p.get("reason") else "") + ".")
    out += [f"Warning: {w}" for w in rep.get("warnings", ())]
    return out


def report_lines(report: dict) -> list:
    out = [f"Saved the map {report['map']} as {report['path']}"]
    if report["identical"]:
        out.append("No edits: the file is byte for byte the map that was opened.")
    else:
        out.append("Changes read from the scene:")
        out += [f"  {line}" for line in report["lines"]]
        out.append("Written into the map:")
        out += [f"  {line}" for line in _library_lines(report["library"])]
    if report.get("feature_changes"):
        out.append("Map features:")
        out += [f"  {line}" for line in report["feature_changes"]]
    out += [f"Warning: {w}" for w in report.get("feature_warnings", ())]
    fid = report.get("fidelity")
    if fid:
        out.append("How close a pathfinding rebuilt like the game's is to the game's own on this map: " + str(fid.get("summary", "")))
    made = set(report.get("feature_files", ()))
    others = [p for p in report["files"] if p != report["path"] and os.path.basename(p) != "README.txt" and p not in made]
    root = os.path.dirname(report["path"])
    if others:
        out.append("Also written, unchanged from the game's files: "
                   + ", ".join(os.path.relpath(p, root).replace(os.sep, "/") for p in others))
    if made:
        out.append("Also written by map features: " + ", ".join(os.path.relpath(p, root).replace(os.sep, "/") for p in sorted(made)))
    out.append(f"Took {report['seconds']:g} s")
    return out


def _report_text(report: dict) -> None:
    name = f"Map save {report['name']}"
    txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    txt.clear()
    txt.write("\n".join(report["text"]) + "\n")


# ---------------------------------------------------------------------------------------------------- placing things
def ground_height_at(terrain, x: float, y: float) -> float | None:
    """The terrain's height (Blender z) at world x, y, read from the points as they are now; None outside the map."""
    _flush(terrain)
    me = terrain.data
    w, h = int(terrain["ic_map_width"]), int(terrain["ic_map_height"])
    ts = float(terrain["ic_tile_scale"])
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    z = co.reshape(-1, 3)[:, 2].astype(np.float64).reshape(h, w)
    p = terrain.matrix_world.inverted() @ Vector((x, y, 0.0))
    gx = p.x / ts + (w - 1) / 2.0
    gz = -p.y / ts + (h - 1) / 2.0
    if gx < 0 or gz < 0 or gx > w - 1 or gz > h - 1:
        return None
    i, j = min(int(gx), w - 2), min(int(gz), h - 2)
    fx, fz = gx - i, gz - j
    local = (z[j, i] * (1 - fx) * (1 - fz) + z[j, i + 1] * fx * (1 - fz) + z[j + 1, i] * (1 - fx) * fz + z[j + 1, i + 1] * fx * fz)
    return float((terrain.matrix_world @ Vector((p.x, p.y, local))).z)


def map_blueprint_spelling(mv, ref: str) -> str:
    """The blueprint as the map writes it: its own spelling when the map already names it, else ``DATA:ART\\EBPS\\...``."""
    from ICParse.data import ICData
    want = ICData.normalize(ref)
    for path in mv.board.blueprint_palette.values():
        if ICData.normalize(path) == want:
            return path
    return "DATA:" + ref.upper().replace("/", "\\")


def _load_model(context, top, ref: str, game_dir: str):
    """The collection holding the model of blueprint ``ref`` (loaded once, into the map's hidden collection), and the reason when there
    is none. A model already loaded for the map's own objects is reused."""
    view = M._view()
    hidden = hidden_collection(top)
    model_ref = os.path.splitext(ref)[0] + ".sgm"
    if hidden is not None:
        for c in hidden.children:
            if c.get(M.KEY_MODEL) == model_ref:
                return c, ""
    data = view.game_data(game_dir, top.get(M.KEY_ARCHIVE, "")) if game_dir else None
    stub = view.PlacedEntity(instance_id=-1, blueprint_ref=-1, blueprint="DATA:" + ref.upper().replace("/", "\\"),
                             name=os.path.splitext(os.path.basename(ref))[0], kind=view.entity_kind(ref), position=(0.0, 0.0, 0.0))
    info = view._classify(stub, data)
    if info.status != view.MODEL:
        return None, info.reason
    if hidden is None:
        hidden = M._new_collection(f"{map_name(top)} models (hidden)", top)
    layer = M._layer_collection(context.view_layer.layer_collection, hidden)
    if layer is not None:
        layer.exclude = False                              # the builder selects what it makes: not possible in an excluded collection
    coll = M._new_collection(f"model {os.path.basename(model_ref)[:-4]}", hidden)
    tmp = tempfile.mkdtemp(prefix="ic_map_")
    info.model_ref = model_ref                             # the spelling the lookup above compares with
    try:
        M.load_model_collection(context, coll, info, data, game_dir, tmp)
    except Exception as exc:                               # noqa: BLE001
        return None, f"the model {model_ref} could not be imported: {exc}"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        if layer is not None:
            layer.exclude = True                           # the instances draw the model; the original stays out of the view
    return coll, ""


def new_entity_object(context, top, mv, ref: str, location, game_dir: str):
    """A new placed object for blueprint ``ref`` at ``location`` (Blender axes); returns ``(object, reason it is an empty or "")``."""
    view = M._view()
    spelling = map_blueprint_spelling(mv, ref)
    name = os.path.splitext(os.path.basename(ref))[0].upper()
    obj = bpy.data.objects.new(f"{name} new", None)
    coll, why = _load_model(context, top, ref, game_dir)
    if coll is not None:
        obj.instance_type = "COLLECTION"
        obj.instance_collection = coll
        obj.empty_display_size = 0.5
    else:
        obj.empty_display_type = "SPHERE"
        obj.empty_display_size = 1.5
        obj["ic_not_shown"] = why
    obj.location = location
    obj["ic_blueprint"] = spelling
    obj["ic_blueprint_number"] = -1
    kind = view.entity_kind(ref)
    obj["ic_kind"] = kind
    obj["ic_owner"] = -1
    obj["ic_player_id"] = -1
    obj[KEY_NEW] = True
    target = next((c for c in top.children if c.name == f"{map_name(top)} {SIDE_WORDS.get(kind, 'other')}"), None) or top
    target.objects.link(obj)
    return obj, why


# ---------------------------------------------------------------------------------------------------- operators
def _need_map(op, context):
    top = active_map(context)
    if top is None:
        op.report({"ERROR"}, "There is no imported map in this scene. Open one with File > Import > Impossible Creatures Map.")
    return top


class BZ_OT_export_map(bpy.types.Operator, ExportHelper):
    """Save the map as a map file (.sgb): the ground, the water, the ground textures and the placed objects as they are in the scene.
    Written into a folder you pick, never into the game's own folder"""
    bl_idname = "bz.export_map"
    bl_label = "Export Impossible Creatures Map"
    bl_options = {"REGISTER"}

    filename_ext = ".sgb"
    filter_glob: StringProperty(default="*.sgb", options={"HIDDEN"})
    map_name: StringProperty(default="", options={"HIDDEN"})
    ready_to_drop: BoolProperty(
        name="Ready-to-drop folder", default=False,
        description="Write Data/scenarios/mp/<name>.sgb inside the chosen folder, with a README on putting it into a copy of the game. "
                    "Off: write <name>.sgb directly into the chosen folder")
    replace_original: BoolProperty(
        name="Replace the original file", default=False,
        description="Allow writing over the map file this map was opened from (off: that is refused)")
    pathfinding: EnumProperty(
        name="Pathfinding", default="auto",
        items=(("auto", "Update when needed", "Update the map's own pathfinding only where the terrain or a blocking object changed; "
                                              "what the game baked elsewhere stays as it was"),
               ("always", "Rebuild like the game", "Throw the pathfinding away and build it again the way the game's own editor does: "
                                                    "from the ground, the cells painted passable or blocked in the map, and the "
                                                    "scenery that blocks (read from its blueprint)"),
               ("never", "Leave as it is", "Do not touch the pathfinding: units walk as on the old terrain")),
        description="What to do with the map's pathfinding (where units can walk) after the terrain changed")

    @classmethod
    def poll(cls, context):
        return active_map(context) is not None

    def invoke(self, context, event):
        top = map_by_name(self.map_name) or active_map(context)
        if top is None:
            self.report({"ERROR"}, "There is no imported map in this scene. Open one with File > Import > Impossible Creatures Map.")
            return {"CANCELLED"}
        self.map_name = top.name
        self.filepath = map_name(top) + ".sgb"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "ready_to_drop")
        col.prop(self, "replace_original")
        col.prop(self, "pathfinding")

    def execute(self, context):
        top = map_by_name(self.map_name) or active_map(context)
        if top is None:
            self.report({"ERROR"}, "There is no imported map in this scene.")
            return {"CANCELLED"}
        try:
            report = save_map(context, top, self.filepath, ready_to_drop=self.ready_to_drop, replace_original=self.replace_original,
                              paths=self.pathfinding)
        except MapSaveError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {report['path']} ({len(report['files'])} files). The whole report is in the text "
                              f"'Map save {report['name']}'.")
        for line in report["text"]:
            print(line, flush=True)
        return {"FINISHED"}


class BZ_OT_map_check(bpy.types.Operator):
    """List what has changed in the scene since the map was opened, without saving anything"""
    bl_idname = "bz.map_check_changes"
    bl_label = "Check My Changes"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return active_map(context) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            plan = build_plan(context, top)
        except MapSaveError as exc:
            LAST_CHECK[top.name] = [f"Cannot be saved: {exc}"]
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        LAST_CHECK[top.name] = plan.lines()
        self.report({"INFO"}, " ".join(plan.lines()[:2]))
        return {"FINISHED"}


# ---- ground textures
_GROUND_ITEMS: list = []


def _ground_items(self, context):
    top = active_map(context)
    game_dir = prefs.resolve_game_dir(context)[0]
    names = game_listing(game_dir, top.get(M.KEY_ARCHIVE, "") if top else "")["ground"] if game_dir else []
    if names:
        items = [(n, os.path.splitext(n)[0], f"detail/{n} in the game's files", "", i) for i, n in enumerate(names)]
    else:
        items = [("NONE", "No ground textures found: set the game folder in the add-on's preferences", "", "ERROR", 0)]
    _GROUND_ITEMS[:] = items
    return _GROUND_ITEMS


def ground_materials(terrain) -> list:
    """``[(slot, material)]`` of the terrain's ground materials."""
    return [(i, m) for i, m in enumerate(terrain.data.materials) if m is not None and m.get("ic_ground_texture")]


class BZ_OT_map_add_ground(bpy.types.Operator):
    """Add one of the game's ground textures to this map, so it can be given to faces of the terrain"""
    bl_idname = "bz.map_add_ground"
    bl_label = "Add Ground Texture"
    bl_options = {"REGISTER", "UNDO"}

    texture: EnumProperty(name="Texture", items=_ground_items, description="A ground texture of the game")

    @classmethod
    def poll(cls, context):
        top = active_map(context)
        return top is not None and terrain_of(top) is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        if self.texture == "NONE":
            self.report({"ERROR"}, "No ground textures found: set the game folder in the add-on's preferences")
            return {"CANCELLED"}
        terrain = terrain_of(top)
        for slot, mat in ground_materials(terrain):
            if str(mat["ic_ground_texture"]).lower() == self.texture.lower():
                self.report({"WARNING"}, f"The map already has {os.path.splitext(self.texture)[0]}: it is the material '{mat.name}' "
                                         f"(slot {slot + 1}).")
                terrain.active_material_index = slot
                return {"CANCELLED"}
        game_dir = prefs.resolve_game_dir(context)[0]
        view = M._view()
        data = view.game_data(game_dir, top.get(M.KEY_ARCHIVE, "")) if game_dir else None
        tex = view.GroundTexture(-1, self.texture)
        tmp = tempfile.mkdtemp(prefix="ic_ground_")
        try:
            img = M._ground_image(tex, data, tmp)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        mat = M._ground_material(tex, img)
        terrain.data.materials.append(mat)
        from . import map_look
        map_look.dress_new_ground(top, mat)
        terrain.active_material_index = len(terrain.data.materials) - 1
        note = "" if img is not None else " Its picture was not found in the game's files, so it shows as a plain colour."
        self.report({"INFO"}, f"Added the ground texture {os.path.splitext(self.texture)[0]} as the material '{mat.name}'. In Edit Mode, "
                              f"select faces and press 'Use on selected faces' (or Assign in the Material tab).{note}")
        return {"FINISHED"}


class BZ_OT_map_use_ground(bpy.types.Operator):
    """Give the selected faces of the terrain (in Edit Mode) this ground material. Each 8 x 8 block of cells takes the material most
    of its faces have"""
    bl_idname = "bz.map_use_ground"
    bl_label = "Use on Selected Faces"
    bl_options = {"REGISTER", "UNDO"}

    slot: bpy.props.IntProperty(default=0, min=0, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        top = active_map(context)
        return top is not None and terrain_of(top) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        terrain = terrain_of(top)
        if context.active_object is not terrain or terrain.mode != "EDIT":
            self.report({"ERROR"}, "Select the terrain, go to Edit Mode and select the faces to paint first.")
            return {"CANCELLED"}
        import bmesh
        bm = bmesh.from_edit_mesh(terrain.data)
        n = 0
        for f in bm.faces:
            if f.select:
                f.material_index = self.slot
                n += 1
        bmesh.update_edit_mesh(terrain.data, loop_triangles=False, destructive=False)
        terrain.active_material_index = self.slot
        if not n:
            self.report({"WARNING"}, "No faces are selected.")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{n} faces now use '{terrain.data.materials[self.slot].name}'.")
        return {"FINISHED"}


# ---- objects
_BLUEPRINT_ITEMS: list = []


def _blueprint_items(self, context):
    top = active_map(context)
    game_dir = prefs.resolve_game_dir(context)[0]
    listing = game_listing(game_dir, top.get(M.KEY_ARCHIVE, "") if top else "")["blueprints"] if game_dir else []
    if listing:
        items = [(ref, blueprint_words(ref), f"{ref} ({kind})", "", i) for i, (ref, kind) in enumerate(listing)]
    else:
        items = [("NONE", "No blueprints found: set the game folder in the add-on's preferences", "", "ERROR", 0)]
    _BLUEPRINT_ITEMS[:] = items
    return _BLUEPRINT_ITEMS


class BZ_OT_map_add_object(bpy.types.Operator):
    """Place a blueprint from the game's own list at the 3D cursor, on the ground"""
    bl_idname = "bz.map_add_object"
    bl_label = "Add Object"
    bl_options = {"REGISTER", "UNDO"}

    blueprint: EnumProperty(name="Blueprint", items=_blueprint_items, description="A blueprint from the game's files")

    @classmethod
    def poll(cls, context):
        top = active_map(context)
        return top is not None and terrain_of(top) is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        if self.blueprint == "NONE":
            self.report({"ERROR"}, "No blueprints found: set the game folder in the add-on's preferences")
            return {"CANCELLED"}
        terrain = terrain_of(top)
        cur = context.scene.cursor.location
        z = ground_height_at(terrain, cur.x, cur.y)
        if z is None:
            self.report({"ERROR"}, "The 3D cursor is outside the map. Put it on the ground first (Shift+right-click, or Shift+S > Cursor to Selected).")
            return {"CANCELLED"}
        try:
            _raw, mv, _gm = read_source(context, top)
        except MapSaveError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        game_dir = prefs.resolve_game_dir(context)[0]
        obj, why = new_entity_object(context, top, mv, self.blueprint, (cur.x, cur.y, z), game_dir)
        for o in context.view_layer.objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if why:
            self.report({"WARNING"}, f"Added {obj.name}, shown as an empty sphere: {why}")
        else:
            self.report({"INFO"}, f"Added {obj.name} on the ground at the 3D cursor. It is written into the map when you save.")
        return {"FINISHED"}


_OWNER_ITEMS: list = []


def _owner_items(self, context):
    top = active_map(context)
    count = 8
    hit = _SOURCES.get(top.name) if top is not None else None
    if hit is not None:
        count = max(2, int(hit[2].player_count))
    items = [("-1", "World (no player)", "Not owned by any player: trees, rocks, ruins", "", 0)]
    items += [(str(i), f"Player {i + 1}", f"The player in slot {i + 1}", "", i + 1) for i in range(count)]
    _OWNER_ITEMS[:] = items
    return _OWNER_ITEMS


def selected_entities(context, top) -> list:
    inside = {top, *top.children_recursive}
    return [o for o in context.selected_objects
            if "ic_blueprint" in o and M.KEY_MODEL not in o and any(c in inside for c in o.users_collection)]


class BZ_OT_map_set_owner(bpy.types.Operator):
    """Give the selected placed objects to a player, or to the world (no player)"""
    bl_idname = "bz.map_set_owner"
    bl_label = "Set Owner"
    bl_options = {"REGISTER", "UNDO"}

    owner: EnumProperty(name="Owner", items=_owner_items, description="Who owns the selected objects at the start of the game")

    @classmethod
    def poll(cls, context):
        return active_map(context) is not None

    def invoke(self, context, event):
        top = active_map(context)
        if top is not None:
            try:
                read_source(context, top)                 # so the list holds as many players as the map is made for
            except MapSaveError:
                pass
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        objs = selected_entities(context, top)
        if not objs:
            self.report({"ERROR"}, "Select one or more placed objects of the map first.")
            return {"CANCELLED"}
        slot = int(self.owner)
        player_id = -1
        try:
            _raw, mv, _gm = read_source(context, top)
            if slot >= 0:
                player_id = next((int(p.player_id) for p in mv.board.players if int(p.index) == slot), -1)
                if mv.player_count and slot >= mv.player_count:
                    self.report({"WARNING"}, f"This map is made for {mv.player_count} players: player {slot + 1} will not be playing.")
        except MapSaveError:
            pass
        for o in objs:
            o["ic_owner"] = slot
            o["ic_player_id"] = player_id
        who = "the world" if slot < 0 else f"player {slot + 1}"
        self.report({"INFO"}, f"{len(objs)} objects now belong to {who}.")
        return {"FINISHED"}


class BZ_OT_map_drop_to_ground(bpy.types.Operator):
    """Move the selected placed objects straight down or up onto the terrain, as it is now"""
    bl_idname = "bz.map_drop_to_ground"
    bl_label = "Drop to Ground"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        top = active_map(context)
        return top is not None and terrain_of(top) is not None

    def execute(self, context):
        top = _need_map(self, context)
        if top is None:
            return {"CANCELLED"}
        context.view_layer.update()
        _ensure_object_mode(context)
        objs = selected_entities(context, top)
        if not objs:
            self.report({"ERROR"}, "Select one or more placed objects of the map first.")
            return {"CANCELLED"}
        terrain = terrain_of(top)
        done = outside = 0
        for o in objs:
            loc = o.matrix_world.translation
            z = ground_height_at(terrain, loc.x, loc.y)
            if z is None:
                outside += 1
                continue
            mw = o.matrix_world.copy()
            mw.translation.z = z
            o.matrix_world = mw
            done += 1
        msg = f"{done} objects dropped onto the ground."
        if outside:
            msg += f" {outside} are outside the map and were not moved."
        self.report({"WARNING" if outside else "INFO"}, msg)
        return {"FINISHED"}


def menu_export(self, context):
    self.layout.operator(BZ_OT_export_map.bl_idname, text="Impossible Creatures Map (.sgb)")


CLASSES = (BZ_OT_export_map, BZ_OT_map_check, BZ_OT_map_add_ground, BZ_OT_map_use_ground, BZ_OT_map_add_object,
           BZ_OT_map_set_owner, BZ_OT_map_drop_to_ground)


def register():
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
