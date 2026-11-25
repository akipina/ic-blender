"""Show an imported map's creatures as real models: stock ones from their blueprint, combined ones rebuilt from the map's
recipe by ``combocore`` (``ICParse/FORMATS.md`` "Combine recipe"). Each kind is built once and instanced.

A failure leaves the empty with its reason (``ic_not_shown``). Keep the ``ic_*`` keys the save reads on the placed
objects, and never write into the game's folders.
"""
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

import bpy

ORDER = 61

KEY_MAP = "ic_map"
KEY_MODEL = "ic_map_model"                 # on the objects and the collection of a built model (not a placed entity)
KEY_ENTITY = "ic_entity_id"
KEY_CREATURE = "ic_map_creature"           # on a built creature's collection: how it was built, in words
DEFAULT_DENSITY = 3                        # surface points per patch side: light (a patch is 2 x 2 cells of 2 triangles each)

# where a parent animal named in a recipe ("COYOTE") may live, in the order tried
PARENT_FOLDERS = ("stock", "creatures", "characters", "zombies")
TORSO_CLASS = 0x800

_PKG = __package__ or "ic_blender"
if _PKG.endswith(".sceneobj"):
    _PKG = _PKG[:-len(".sceneobj")]


def _addon(name: str):
    return importlib.import_module(f"{_PKG}.{name}")


class Unbuildable(Exception):
    """A plain-words reason why one creature cannot be shown."""


@dataclass
class Recipe:
    """A creature combined in the map, as its ``BPID`` chunk holds it."""
    blueprint_id: int
    parents: List[str]                                  # the parent animals' blueprint names, in the recipe's order
    links: List[tuple]                                  # (limb class, parent limb class, source index, socket source index)
    name: str = ""


@dataclass
class CreatureResult:
    """What :func:`build_creature_models` made."""
    collections: Dict[str, object] = field(default_factory=dict)       # model key -> the collection holding the built creature
    reasons: Dict[str, str] = field(default_factory=dict)              # model key -> why it was not built
    seconds: Dict[str, float] = field(default_factory=dict)            # model key -> seconds to build it
    how: Dict[str, str] = field(default_factory=dict)                  # model key -> "stock creature" / "combined: A + B"
    patches: Dict[str, int] = field(default_factory=dict)              # model key -> patches
    triangles: Dict[str, int] = field(default_factory=dict)            # model key -> triangles drawn


# ---------------------------------------------------------------------------------------------------- the recipes in the map
def combined_recipes(board) -> Dict[int, Recipe]:
    """``{blueprint id: Recipe}`` of every creature combined in the map itself (a ``BPID`` chunk with a recipe instead of a path).
    Reads the same chunks as ``ICParse.sgb.view.combined_names``."""
    from construct import ConstructError
    from ICParse.constants.sgb import MARK_BLUEPRINT_ID, MARK_BLUEPRINT_PATH
    from ICParse.sgb.structs import BNET, BPID
    out: Dict[int, Recipe] = {}
    tree = getattr(board, "preserved", None)
    for bndt in (tree.find_all("BNDT") if tree is not None else ()):
        kids = getattr(bndt, "children", None) or ()
        bnet = next((k for k in kids if getattr(k, "raw", b"")[:4] == MARK_BLUEPRINT_ID), None)
        bpid = next((k for k in kids if getattr(k, "raw", b"")[:4] == MARK_BLUEPRINT_PATH), None)
        if bnet is None or bpid is None:
            continue
        try:
            rec = BPID.parse(bpid.raw)
            bid = int(BNET.parse(bnet.raw).blueprint_id)
        except ConstructError:
            continue
        if rec.flag == 0:
            continue
        body = rec.body
        name = ""
        for raw_name in (body.display_name, body.combined_name):
            name = bytes(raw_name).decode("utf-16-le", "ignore").rstrip("\x00").strip()
            if name:
                break
        parents = [bytes(p.name).decode("latin1").strip("\x00") for p in body.parents]
        links = [(int(k.part_a), int(k.part_b), int(k.flag_a), int(k.flag_b)) for k in body.links]
        out[bid] = Recipe(bid, parents, links, name)
    return out


# ---------------------------------------------------------------------------------------------------- staging creatures from the game's files
def _stage(data, ebp_ref: str, tmp: str) -> str:
    """Write the creature of blueprint ``ebp_ref`` (``art/ebps/stock/coyote.ebp``) and its ``.lua`` / ``.lsc`` beside it into ``tmp`` and
    return the blueprint's path there. The model is the ``.sgm`` beside the blueprint, else the one embedded in it."""
    from ICParse.ebp import parser as ebp_parser
    ebp_raw = data.read(ebp_ref)
    if ebp_raw is None:
        raise Unbuildable(f"its blueprint {ebp_ref} is not in the game's files")
    stem = os.path.splitext(os.path.basename(ebp_ref))[0]
    folder = os.path.join(tmp, "creatures", os.path.dirname(ebp_ref).replace("/", "_"))
    os.makedirs(folder, exist_ok=True)
    sgm_raw = data.read(os.path.splitext(ebp_ref)[0] + ".sgm")
    if sgm_raw is None:
        try:
            sgm_raw = ebp_parser.read(ebp_raw).embedded_sgm_bytes()
        except Exception as exc:
            raise Unbuildable(f"its blueprint {ebp_ref} could not be read: {exc}") from exc
    if sgm_raw is None:
        raise Unbuildable(f"its blueprint {ebp_ref} holds no creature model (it is an army profile, not an animal)")
    base = os.path.join(folder, stem)
    for ext, raw in ((".ebp", ebp_raw), (".sgm", sgm_raw), (".lua", data.read(os.path.splitext(ebp_ref)[0] + ".lua")),
                     (".lsc", data.read(os.path.splitext(ebp_ref)[0] + ".lsc"))):
        if raw is not None:
            with open(base + ext, "wb") as f:
                f.write(raw)
    return base + ".ebp"


def _parent_ref(data, name: str) -> str:
    """The blueprint reference of the parent animal a recipe names (``COYOTE`` -> ``art/ebps/stock/coyote.ebp``)."""
    from ICParse.data import ICData
    n = name.strip()
    if "/" in n or "\\" in n:
        ref = ICData.normalize(n)
        return ref if ref.endswith(".ebp") else ref + ".ebp"
    stem = n.lower()[:-4] if n.lower().endswith(".ebp") else n.lower()
    for folder in PARENT_FOLDERS:
        ref = f"art/ebps/{folder}/{stem}.ebp"
        if data.read(ref) is not None:
            return ref
    raise Unbuildable(f"the parent animal {name} is not in the game's files (looked in art/ebps/{', '.join(PARENT_FOLDERS)})")


def _load_parent(data, ebp_ref: str, tmp: str, cache: dict):
    """One combiner ``Parent`` per blueprint (the combiner tells animals apart by identity)."""
    hit = cache.get(ebp_ref)
    if hit is None:
        path = _stage(data, ebp_ref, tmp)
        combocore = _addon("combocore")
        hit = cache[ebp_ref] = combocore.parent_for_file(path)
    return hit


def _combine(recipe: Recipe, data, tmp: str, cache: dict):
    """The model of a creature combined in the map: its torso animal with the limbs the recipe moves onto it."""
    combocore = _addon("combocore")
    if not combocore.available():
        raise Unbuildable("the combiner is not available in this Blender, so a creature combined in the map cannot be built "
                          f"({combocore.IMPORT_ERROR})")
    from combotest import slots as ct_slots
    if not recipe.parents:
        raise Unbuildable("the map's recipe for this creature names no parent animal")
    parents = [_load_parent(data, _parent_ref(data, n), tmp, cache) for n in recipe.parents]

    def source(i: int, what: str):
        if not 0 <= i < len(parents):
            raise Unbuildable(f"the recipe sends {what} to animal number {i + 1}, but it names only {len(parents)}")
        return parents[i]
    torso = None
    choices = {}
    for cls, _parent_cls, src, _socket in recipe.links:
        if cls & ct_slots.SLOT_MASK == TORSO_CLASS:
            torso = source(src, "the torso")
    if torso is None:                                   # no torso link: the first animal is the body
        torso = parents[0]
    for cls, _parent_cls, src, _socket in recipe.links:
        if cls & ct_slots.SLOT_MASK == TORSO_CLASS:
            continue
        slot = ct_slots.slot_for_limb_id(cls)
        if slot is None:
            raise Unbuildable(f"the recipe moves a limb of class {cls:#x}, which the combiner has no slot for")
        choices[slot.key] = source(src, f"the {slot.label.lower()}")
    tables = combocore.size_tables()
    name = f"combined_{recipe.blueprint_id}"
    try:
        r = combocore._quiet_combine(torso, choices, name=name, animals=list(parents), run_lua=False, do_scale=tables is not None,
                                     use_rules=True, script_source="game", as_spec=True, size_tables=tables)
    except Exception as exc:
        raise Unbuildable(f"the combiner could not build it: {type(exc).__name__}: {exc}") from exc
    names = {id(p): combocore.creature_name(p) for p in parents}
    parts: Dict[str, list] = {}
    parts.setdefault(names[id(torso)], []).append("torso")
    for key, src in choices.items():
        parts.setdefault(names[id(src)], []).append(ct_slots.BY_KEY[key].label.lower())
    how = "combined in the map: " + "; ".join(f"{n}: {', '.join(v)}" for n, v in parts.items())
    return r.model, how


# ---------------------------------------------------------------------------------------------------- the mesh
def _new_collection(name: str, parent):
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def _mesh_from_surface(name: str, arrays: dict, materials: list):
    """One mesh from ``combocore.build_ghost_arrays``: the surface points as vertices, its triangles, the skin's UVs, one material per
    material slot of the creature, the engine's normals."""
    surf = np.ascontiguousarray(arrays["surfV"], dtype=np.float32)
    tris = np.ascontiguousarray(arrays["tris"], dtype=np.int32)
    nv, nt = len(surf), len(tris)
    me = bpy.data.meshes.new(name)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", surf.ravel())
    me.loops.add(nt * 3)
    me.loops.foreach_set("vertex_index", tris.ravel())
    me.polygons.add(nt)
    me.polygons.foreach_set("loop_start", np.arange(0, nt * 3, 3, dtype=np.int32))
    try:
        me.polygons.foreach_set("loop_total", np.full(nt, 3, dtype=np.int32))
    except (AttributeError, TypeError, RuntimeError):
        pass                                           # newer Blender works the sizes out from the starts
    me.update(calc_edges=True)
    uv = np.asarray(arrays["uvs"], dtype=np.float32)
    layer = me.uv_layers.new(name="UVMap")
    layer.data.foreach_set("uv", uv[tris.ravel()].ravel())
    for m in materials:
        me.materials.append(m)
    patches = len(arrays["patches"])
    per_patch = max(1, nt // max(1, patches))
    mat_of_patch = np.asarray(arrays["patch_mat"], dtype=np.int64)
    idx = mat_of_patch[np.minimum(np.arange(nt) // per_patch, max(0, patches - 1))] if patches else np.zeros(nt, np.int64)
    idx = np.clip(idx, 0, max(0, len(materials) - 1))
    me.polygons.foreach_set("material_index", idx.astype(np.int32))
    try:
        me.shade_smooth()
    except AttributeError:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))
    try:                                               # the engine's own normals, welded across the patches
        me.normals_split_custom_set_from_vertices(np.asarray(arrays["surfN"], dtype=np.float32).tolist())
    except Exception:                                  # noqa: BLE001  (an older Blender: Blender's own smooth shading stays)
        pass
    me.update()
    return me


def _materials(model, label: str) -> list:
    """One material per material slot of the creature, with its skin decoded from the model (the add-on's own creature materials)."""
    material = _addon("material")
    images = material.decode_textures(model)
    by_name = material.first_image_by_name(model, images)
    table = material.material_table(model)
    mats = material.build_blender_materials(table, by_name, names=[f"{label} {i}" for i in range(len(table))])
    for img in images:                                 # the images live in the materials; no stray user needed
        if img is not None:
            img["ic_map_skin"] = label
    return mats


def _build_one(key: str, model, label: str, hidden, density: int, out: CreatureResult, how: str) -> None:
    combocore = _addon("combocore")
    arrays = combocore.build_ghost_arrays(model, density)
    if not len(arrays["surfV"]):
        raise Unbuildable("the creature has no surface")
    mats = _materials(model, label)
    me = _mesh_from_surface(label, arrays, mats)
    obj = bpy.data.objects.new(label, me)
    coll = _new_collection(f"creature {label}", hidden)
    coll.objects.link(obj)
    obj[KEY_MODEL] = key                               # a part of the map's models, not a placed entity
    coll[KEY_MODEL] = key
    coll[KEY_CREATURE] = how
    out.collections[key] = coll
    out.patches[key] = int(len(arrays["patches"]))
    out.triangles[key] = int(len(arrays["tris"]))


# ---------------------------------------------------------------------------------------------------- the entry points
def creature_keys(mv, infos: dict) -> Dict[str, list]:
    """``{model key: [placed entities]}`` of the creatures the map places: those ``resolve_models`` marked as creatures."""
    from ICParse.sgb import view
    out: Dict[str, list] = {}
    for e in mv.entities:
        info = infos.get(e.model_key)
        if info is not None and info.status == view.CREATURE:
            out.setdefault(e.model_key, []).append(e)
    return out


def build_creature_models(mv, infos: dict, data, hidden, *, density: int = DEFAULT_DENSITY, tmp: Optional[str] = None) -> CreatureResult:
    """Build each distinct creature the map places once, as a collection inside ``hidden``. ``infos`` is ``view.resolve_models(mv, data)``,
    ``data`` the map's ``ICData``. A creature that cannot be built is left out and its plain-words reason is in ``reasons``."""
    out = CreatureResult()
    groups = creature_keys(mv, infos)
    if not groups:
        return out
    own_tmp = tmp is None
    tmp = tmp or tempfile.mkdtemp(prefix="ic_map_creatures_")
    recipes = combined_recipes(mv.board)
    cache: dict = {}
    try:
        for key in sorted(groups):
            ent = groups[key][0]
            t0 = time.perf_counter()
            try:
                if data is None:
                    raise Unbuildable("the game's files were not available to look the creature up")
                if ent.combined:
                    recipe = recipes.get(ent.blueprint_ref)
                    if recipe is None:
                        raise Unbuildable(f"the map holds no recipe for combined creature number {ent.blueprint_ref}")
                    model, how = _combine(recipe, data, tmp, cache)
                    label = f"combined {recipe.blueprint_id}"
                else:
                    ref = ent.ebp_ref
                    if not ref:
                        raise Unbuildable("it has no blueprint")
                    ebp = _stage(data, ref, tmp)
                    combocore = _addon("combocore")
                    if combocore.available():
                        model = combocore.parent_for_file(ebp).model
                    else:
                        from ICParse import read_sgm
                        model = read_sgm(os.path.splitext(ebp)[0] + ".sgm")
                    how = "stock creature " + os.path.basename(ref)
                    label = os.path.splitext(os.path.basename(ref))[0]
                _build_one(key, model, label, hidden, density, out, how)
                out.how[key] = how
            except Unbuildable as exc:
                out.reasons[key] = f"a creature that could not be shown: {exc}"
            except Exception as exc:                   # one creature that fails leaves the rest of the map
                out.reasons[key] = f"a creature that could not be shown: {type(exc).__name__}: {exc}"
            out.seconds[key] = round(time.perf_counter() - t0, 3)
    finally:
        if own_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def _hidden_collection(top, name_hint: str):
    for c in top.children:
        if c.name.endswith("(hidden)"):
            return c, False
    return _new_collection(f"{name_hint} models (hidden)", top), True


def _layer_collection(layer, coll):
    if layer.collection == coll:
        return layer
    for child in layer.children:
        hit = _layer_collection(child, coll)
        if hit is not None:
            return hit
    return None


def show_creatures(context, mv, top, *, data, density: int = DEFAULT_DENSITY, infos: Optional[dict] = None) -> dict:
    """Turn the placed creatures of an imported map into instances of their real models.

    ``top`` is the map's collection (``Map <name>``), as ``import_map`` made it; ``data`` the map's ``ICData`` (``view.game_data``);
    ``infos`` the ``view.resolve_models`` result when the caller has it. Every placed object that is an empty because it is a creature
    becomes an instance of the creature's built model (the object keeps all its properties; only ``ic_not_shown`` goes); one whose creature
    could not be built stays an empty and its ``ic_not_shown`` says why. Returns a report (a dict)."""
    from ICParse.sgb import view
    t0 = time.perf_counter()
    if infos is None:
        infos = view.resolve_models(mv, data)
    name = top.name[4:] if top.name.startswith("Map ") else top.name
    groups = creature_keys(mv, infos)
    report = {"creatures_placed": sum(len(v) for v in groups.values()), "creatures_shown": 0, "creatures_empty": 0,
              "distinct": len(groups), "built": 0, "built_models": [], "not_built": [], "per_creature_s": {}, "seconds": 0.0}
    if not groups:
        report["seconds"] = round(time.perf_counter() - t0, 3)
        return report
    hidden, created = _hidden_collection(top, name)
    result = build_creature_models(mv, infos, data, hidden, density=density)
    if created:
        layer = _layer_collection(context.view_layer.layer_collection, hidden)
        if layer is not None:
            layer.exclude = True
    by_id = {e.instance_id: e for e in mv.entities}
    for o in top.all_objects:
        eid = o.get(KEY_ENTITY)
        if eid is None or KEY_MODEL in o or o.instance_type == "COLLECTION":
            continue
        e = by_id.get(int(eid))
        if e is None or e.model_key not in groups:
            continue
        coll = result.collections.get(e.model_key)
        if coll is not None:
            o.instance_type = "COLLECTION"
            o.instance_collection = coll
            o.empty_display_size = 0.5
            if "ic_not_shown" in o:
                del o["ic_not_shown"]
            report["creatures_shown"] += 1
        else:
            o["ic_not_shown"] = result.reasons.get(e.model_key, "a creature that could not be shown")
            report["creatures_empty"] += 1
    report["built"] = len(result.collections)
    report["built_models"] = sorted(result.collections)
    report["not_built"] = [{"name": groups[k][0].name, "count": len(groups[k]), "why": w} for k, w in result.reasons.items()]
    report["per_creature_s"] = dict(result.seconds)
    report["patches"] = dict(result.patches)
    report["triangles"] = dict(result.triangles)
    report["seconds"] = round(time.perf_counter() - t0, 3)
    return report
