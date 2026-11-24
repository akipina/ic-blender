"""Scene objects (structures, characters, props) as Blender meshes.

A scene ``.sgm`` is a triangle mesh with stored normals/UVs, per-material submeshes, an optional
skeleton with rigid part binding (structures) or per-vertex skin weights (characters), external or
embedded textures and bone clips. It is imported exactly as the engine draws it (verified against
the Object Editor's draw stream): every part at its stored positions with its stored normals as
custom split normals, materials with the resolved images, the skeleton as an armature at bind.
Decoding lives in ``core.load_scene`` (ICParse); this module builds the Blender datablocks and writes an edited
object back (``export_scene_object``: any topology, per part, through ICParse's ``write_scene_part``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

import bpy

from . import core
from . import material
from . import rig

KEY_SCENE = "bz_scene"           # truthy on every part object of an imported scene object
KEY_SCENE_PART = "bz_scene_part"  # part index
KEY_NORMALS_ERROR = "bz_normals_error"  # set on a part whose stored normals Blender refused (it shows its own smooth normals)


def normals_errors(objs):
    """(number of parts whose stored normals could not be set, the first error) for the objects ``build_scene`` made."""
    errs = [o[KEY_NORMALS_ERROR] for o in objs if o.get(KEY_NORMALS_ERROR)]
    return len(errs), (errs[0] if errs else "")


def _images(data):
    """Blender image per texture key (packed, bottom-up; colour-keyed magenta -> alpha 0 like the
    engine's upload)."""
    out = {}
    for key, (w, h, rgba) in data.textures.items():
        if w <= 0 or h <= 0 or len(rgba) < w * h * 4:
            continue
        img = bpy.data.images.new(f"SGM_{key}", w, h, alpha=True)
        arr = np.frombuffer(rgba, dtype=np.uint8)[:w * h * 4].astype(np.float32) / 255.0
        arr = arr.reshape(h, w, 4)
        keyed = (arr[..., 0] > 0.99) & (arr[..., 1] < 0.01) & (arr[..., 2] > 0.99)
        arr[keyed, 3] = 0.0
        img.pixels.foreach_set(np.ascontiguousarray(arr[::-1]).reshape(-1))
        img.pack()
        out[key] = img
    return out


def build_scene(context, data, source_path=None, clips_as_actions=True):
    """Create the part objects (+ armature, materials, images) for a ``core.SceneData``; returns the
    list of mesh objects (the largest first)."""
    src = source_path or data.source_path
    images = _images(data)
    mats = material.build_blender_materials(
        [{"tex": mt["texture"], "color": mt["color"]} for mt in data.materials],
        images, [mt["name"] for mt in data.materials])

    has_skin = any(p.skin for p in data.parts)
    has_bind = any(p.bind is not None for p in data.parts)
    arm_obj = None
    if data.bone_names and (has_skin or has_bind):
        arm_obj = rig.create_armature(context, data.name + "_rig", data.bone_names, data.bone_parents, data.bind_world)

    objs = []
    for pi, part in enumerate(data.parts):
        me = bpy.data.meshes.new(part.name)
        me.from_pydata(part.verts.tolist(), [], part.tris.tolist())
        for mt in mats:
            me.materials.append(mt)
        if len(part.tri_mat) == len(me.polygons):
            me.polygons.foreach_set("material_index", part.tri_mat.astype(np.int32))
        for poly in me.polygons:
            poly.use_smooth = True
        me.update()
        if len(part.uvs) == len(me.vertices):
            uvl = me.uv_layers.new(name="UVMap")
            loop_v = np.empty(len(me.loops), dtype=np.int64)
            me.loops.foreach_get("vertex_index", loop_v)
            uvl.data.foreach_set("uv", part.uvs[loop_v].astype(np.float32).ravel())
        normals_error = None
        if len(part.normals) == len(me.vertices):
            try:
                me.normals_split_custom_set_from_vertices(part.normals.tolist())
            except Exception as e:                      # noqa: BLE001 -- kept on the part and reported by the importer
                normals_error = f"{type(e).__name__}: {e}"
        obj = bpy.data.objects.new(part.name, me)
        if normals_error:
            obj[KEY_NORMALS_ERROR] = normals_error
        context.collection.objects.link(obj)
        obj[KEY_SCENE] = True
        obj[KEY_SCENE_PART] = pi
        obj["bz_source"] = src
        obj["bz_name"] = data.name
        if arm_obj is not None:
            if part.skin:
                groups = {}
                for vi, infl in enumerate(part.skin):
                    for bi, wt in infl:
                        if 0 <= int(bi) < len(data.bone_names):
                            name = data.bone_names[int(bi)] or f"bone{int(bi)}"
                            g = groups.get(name) or obj.vertex_groups.new(name=name)
                            groups[name] = g
                            g.add([vi], float(wt), "REPLACE")
            elif part.bind is not None and 0 <= int(part.bind) < len(data.bone_names):
                name = data.bone_names[int(part.bind)] or f"bone{int(part.bind)}"
                obj.vertex_groups.new(name=name).add(list(range(len(me.vertices))), 1.0, "REPLACE")
            mod = obj.modifiers.new("Armature", "ARMATURE")
            mod.object = arm_obj
            mod.use_vertex_groups = True
            obj["bz_rig"] = arm_obj.name
            obj["bz_anims"] = [a.name or f"anim{i}" for i, a in enumerate(data.animations)]
        objs.append(obj)

    if arm_obj is not None and clips_as_actions and data.animations:
        from . import actions
        try:
            actions.import_scene_clips(arm_obj, data)
        except Exception as e:
            arm_obj["bz_actions_error"] = str(e)

    objs.sort(key=lambda o: -len(o.data.vertices))
    if len(objs) > 1 and arm_obj is None:
        group = bpy.data.objects.new(data.name, None)
        context.collection.objects.link(group)
        for o in objs:
            o.parent = group
    for o in context.view_layer.objects:
        o.select_set(False)
    if objs:
        objs[0].select_set(True)
        context.view_layer.objects.active = objs[0]
    return objs


def scene_objects(context=None):
    """Every part object of imported scene objects (in the current file)."""
    return [o for o in bpy.data.objects if o.get(KEY_SCENE)]


_UV_QUANT = 1.0e5            # two corners share one output vertex when their UVs agree to 1e-5


@dataclass
class BuiltPart:
    """One part object's mesh in the file's own terms: one entry per output VERTEX (a Blender vertex is split
    wherever its corners disagree on UV or sit on a flat-shaded face), per-material triangle lists, and the skin."""
    positions: np.ndarray       # (N, 3) SGM space
    normals: np.ndarray         # (N, 3) SGM space, unit length
    uvs: np.ndarray            # (N, 2) IC convention (V flipped back)
    skin: object                # per vertex [(bone index, weight), ...]; None for a rigid part (its bone is the part's own)
    submeshes: list             # [(shader name, [(a, b, c), ...])] over the output vertices, in order of first use
    source_vertex: np.ndarray   # (N,) the Blender vertex each output vertex came from
    notes: list = field(default_factory=list)


@dataclass
class SceneExport:
    data: bytes
    rebuilt: list               # part numbers written from the Blender mesh
    kept: list                  # part numbers whose source bytes were kept (unedited)
    missing: list               # parts of the file with no object in this scene: kept from the source
    notes: list
    damage_states_dropped: dict = field(default_factory=dict)   # part number -> damage states written out of it


def damage_states_refusal(name: str, count: int) -> str:
    """Why an edited part with damage states (morph targets) is not exported."""
    return (f"'{name}' has damage states ({count} stored shapes the game blends into this part, such as a building's damage "
            f"or a character's face); editing its mesh would break them, and editing them is not possible yet. Undo the "
            f"edit to keep them, or tick \"Drop the damage states of edited parts\" when you export (File > Export, or Export "
            f"Entity Folder in the Blueprint panel) to write the part without them")


def part_objects(obj):
    """Every part object of the scene object ``obj`` belongs to (same import: same source file and rig), by part number."""
    src, rig = obj.get("bz_source"), obj.get("bz_rig")
    found = {}
    for o in bpy.data.objects:
        if o.type != "MESH" or not o.get(KEY_SCENE) or o.get(KEY_SCENE_PART) is None:
            continue
        if o.get("bz_source") != src or o.get("bz_rig") != rig:
            continue
        pi = int(o[KEY_SCENE_PART])
        if pi in found:
            raise ValueError(f"'{found[pi].name}' and '{o.name}' are both part {pi} of {os.path.basename(src or '')}: "
                             f"delete or re-import one of them (a part can be written once)")
        found[pi] = o
    return [found[k] for k in sorted(found)]


def _foreach(coll, attr, dtype, per=1):
    a = np.empty(len(coll) * per, dtype=dtype)
    coll.foreach_get(attr, a)
    return a.reshape(-1, per) if per > 1 else a


def build_part(obj, data, part) -> BuiltPart:
    """Read a part object's mesh as the file stores it. Seams are split where a vertex's corners disagree on UV, or where a
    face is flat-shaded (its corners take the face normal); everything else shares one vertex and takes the smooth normal.
    Output vertices keep the Blender vertex order (copies from splitting are appended after), so an unedited mesh maps back
    to exactly the vertices it was imported from. Vertices no face uses are not written."""
    me = obj.data
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    if not len(me.polygons):
        raise ValueError(f"part '{obj.name}' has no faces")
    me.calc_loop_triangles()
    nl, nv, npoly, nt = len(me.loops), len(me.vertices), len(me.polygons), len(me.loop_triangles)
    notes = []
    tri_loops = _foreach(me.loop_triangles, "loops", np.int64, 3)
    tri_poly = _foreach(me.loop_triangles, "polygon_index", np.int64)
    loop_vert = _foreach(me.loops, "vertex_index", np.int64)
    poly_mat = _foreach(me.polygons, "material_index", np.int64)
    poly_smooth = _foreach(me.polygons, "use_smooth", np.bool_)
    poly_nrm = _foreach(me.polygons, "normal", np.float64, 3)
    co = _foreach(me.vertices, "co", np.float64, 3)
    vnrm = _foreach(me.vertices, "normal", np.float64, 3)
    uvl = me.uv_layers.active
    if uvl is not None:
        uv = _foreach(uvl.data, "uv", np.float64, 2)
    else:
        uv = np.zeros((nl, 2))
        notes.append("has no UV map: its UVs are written as zeros")

    corners = tri_loops.reshape(-1)                             # (T*3,) loop index of every triangle corner
    cv = loop_vert[corners]
    q = uv[corners] * _UV_QUANT
    cuv = np.where(np.isfinite(q), np.rint(np.where(np.isfinite(q), q, 0.0)), float(1 << 40)).astype(np.int64)   # NaN keys alike
    cpoly = np.repeat(tri_poly, 3)
    flat = np.where(poly_smooth[cpoly], -1, cpoly)              # a flat face is its own vertex set
    key = np.column_stack([cv, cuv[:, 0], cuv[:, 1], flat])
    uniq, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    order = np.lexsort((first, uniq[:, 0]))                     # by source vertex, then by first use
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order))
    tri_out = rank[inv.reshape(-1)].reshape(-1, 3)              # output vertex of every triangle corner
    rep = first[order]                                          # a corner that stands for each output vertex
    src_v, rep_loop, rep_flat = cv[rep], corners[rep], flat[rep]
    n_out = len(order)
    from ICParse.sgm.scene.model import MAX_PART_VERTICES
    if n_out > MAX_PART_VERTICES:
        raise ValueError(f"part '{obj.name}' comes to {n_out:,} vertices once it is split at UV seams and flat-shaded faces "
                         f"(the game keeps one per distinct position, normal and UV); the game draws at most "
                         f"{MAX_PART_VERTICES:,} per part and misreads the rest of the file above that. Reduce its detail "
                         f"(apply a Decimate modifier, or use fewer seams and flat faces), or split it into two parts; this exporter "
                         f"cannot add a part yet, so for now reducing the detail is the way")
    if n_out > nv:
        notes.append(f"{n_out - nv} vertices were added by splitting at UV seams / flat-shaded faces")
    unused = nv - len(np.unique(cv))
    if unused:
        notes.append(f"{unused} vertices belong to no face and were not written")

    nrm = np.where((rep_flat >= 0)[:, None], poly_nrm[np.maximum(rep_flat, 0)], vnrm[src_v])
    length = np.linalg.norm(nrm, axis=1)
    nrm = np.where(length[:, None] > 1e-9, nrm / np.maximum(length, 1e-9)[:, None], np.array([0.0, 0.0, 1.0]))
    out_uv = uv[rep_loop].copy()
    out_uv[:, 1] = 1.0 - out_uv[:, 1]
    nan_uv = int((~np.isfinite(out_uv)).any(axis=1).sum())
    if nan_uv:
        notes.append(f"{nan_uv} vertices have a UV that is not a number (as in some stock files); written as they are")

    tri_mat = poly_mat[tri_poly]
    good = (tri_out[:, 0] != tri_out[:, 1]) & (tri_out[:, 1] != tri_out[:, 2]) & (tri_out[:, 0] != tri_out[:, 2])
    if not good.all():
        notes.append(f"{int((~good).sum())} degenerate triangles (a repeated vertex) were left out")
    tri_out, tri_mat = tri_out[good], tri_mat[good]
    if not len(tri_out):
        raise ValueError(f"part '{obj.name}' has no usable triangles")
    _, fi = np.unique(tri_mat, return_index=True)
    submeshes = []
    for m in tri_mat[np.sort(fi)].tolist():
        if not 0 <= m < len(data.materials):
            raise ValueError(f"part '{obj.name}': material slot {m + 1} has no shader in the file ({len(data.materials)} "
                             f"come from the import) -- give those faces one of the imported materials")
        submeshes.append((data.materials[m]["name"], [tuple(t) for t in tri_out[tri_mat == m].tolist()]))

    skin = None
    if part.skin:                                               # a skinned part: every vertex carries bone weights
        bone_of = {(n or f"bone{i}").lower(): i for i, n in enumerate(data.bone_names)}
        group_bone = {g.index: bone_of.get(g.name.lower()) for g in obj.vertex_groups}
        root = next((i for i, p in enumerate(data.bone_parents) if p < 0), 0)
        cache, bare, skin = {}, 0, []
        for vi in src_v.tolist():
            infl = cache.get(vi)
            if infl is None:
                infl = [(group_bone[g.group], float(g.weight)) for g in me.vertices[vi].groups
                        if group_bone.get(g.group) is not None and g.weight > 0.0]
                if not infl:
                    infl = [(root, 1.0)]
                    bare += 1
                cache[vi] = infl
            skin.append(infl)
        if bare:
            notes.append(f"{bare} vertices have no bone weights; they were bound to the root bone "
                         f"'{data.bone_names[root] or root}' (they would not follow the skeleton in Blender)")
    return BuiltPart(positions=core.blender_to_sgm(co[src_v]), normals=core.blender_to_sgm(nrm), uvs=out_uv, skin=skin,
                     submeshes=submeshes, source_vertex=src_v, notes=notes)


def _source_submeshes(data, part, remap=None):
    """The source part's triangles per shader, in order of first use -- the shape ``build_part`` produces. ``remap``: old vertex
    index -> index after the vertices no triangle uses are left out."""
    subs, seen = {}, []
    for tri, mi in zip(part.tris.tolist(), part.tri_mat.tolist()):
        if mi not in subs:
            subs[mi] = []
            seen.append(mi)
        subs[mi].append(tuple(int(remap[i]) for i in tri) if remap is not None else tuple(tri))
    return [(data.materials[mi]["name"] if mi < len(data.materials) else f"mat{mi}", subs[mi]) for mi in seen]


def _skin_key(infl):
    top = sorted(infl, key=lambda t: -t[1])[:4]
    total = sum(w for _, w in top) or 1.0
    return {int(b): w / total for b, w in top if w / total > 5e-4}


def part_unchanged(built: BuiltPart, data, part) -> bool:
    """True when the part's mesh is what the file already holds (vertices, UVs, triangles per shader, weights), so
    its bytes need not be rewritten. Normals are derived from the geometry and are not compared."""
    n = len(part.verts)
    used = np.unique(part.tris) if len(part.tris) else np.zeros(0, dtype=np.int64)
    remap = keep = None
    if len(used) != n:                         # the file keeps vertices no triangle uses; a rebuild leaves them out
        remap = np.full(n, -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        keep = used
    src_pos = core.blender_to_sgm(part.verts)
    src_pos = src_pos if keep is None else src_pos[keep]
    if len(built.positions) != len(src_pos) or not np.allclose(built.positions, src_pos, atol=1e-5):
        return False
    if len(part.uvs):
        uv0 = np.asarray(part.uvs, dtype=np.float64).copy()
        uv0[:, 1] = 1.0 - uv0[:, 1]
        uv0 = uv0 if keep is None else uv0[keep]
        if not np.allclose(built.uvs, uv0, atol=1e-5, equal_nan=True):
            return False
    if built.submeshes != _source_submeshes(data, part, remap):
        return False
    if part.skin:
        skin0 = part.skin if keep is None else [part.skin[i] for i in keep.tolist()]
        if built.skin is None or len(built.skin) != len(skin0):
            return False
        for a, b in zip(built.skin, skin0):
            ka, kb = _skin_key(a), _skin_key(b)
            if ka.keys() != kb.keys() or any(abs(ka[k] - kb[k]) > 2e-3 for k in ka):
                return False
    return True


def export_scene_object(obj, legacy: bool = True, only=None, force: bool = False, drop_morphs: bool = False) -> SceneExport:
    """The scene object ``obj`` belongs to, as ``.sgm`` bytes: every part whose mesh was edited is rebuilt from its Blender
    object (any topology -- new or deleted faces, seams, UV edits, new bone weights, changed material slots) into the
    source file, and every part left as imported keeps the source's bytes, so an unedited object comes back byte-identical.
    The rig's clips are not touched here (``bz.export_scene`` adds them). The result is checked with the scene validator
    and refused if it has errors.

    ``only``: consider just these part numbers (the rest stay as in the source). ``force``: rebuild them even if unedited.
    ``legacy=True`` writes the v1.2 layout so the engine rebuilds the detail ladder itself (the path through the real
    game); ``False`` keeps v2.0 with one full-detail record (loads in the Object Editor, not yet seen in the game).

    A part with damage states (morph targets: stored shapes the game blends in, which name its vertices by number) cannot
    be edited yet without breaking them: an edited one is refused, unless ``drop_morphs`` writes it without them (counted
    in ``damage_states_dropped`` and the notes). Parts left as imported keep theirs byte for byte. Every rebuilt part gets
    the bounding volumes the game builds (its own, one per palette bone), and the entity box follows it (ICParse
    ``write_scene_part``)."""
    from ICParse.sgm.scene import model as SM
    from ICParse.sgm.scene.validate import validate_scene_sgm

    src = obj.get("bz_source", "")
    if not src or obj.get(KEY_SCENE_PART) is None:
        raise ValueError("not an imported scene part (no bz_source / bz_scene_part)")
    data = core.load_scene(src)
    raw = open(core.resolve_source(src), "rb").read()
    objs = part_objects(obj)
    src_forms = SM.scene_pmsh_forms(SM.IffDocument.from_bytes(raw))
    n_forms = len(src_forms)
    rebuilt, kept, notes, present, dropped = [], [], [], set(), {}
    for o in objs:
        pi = int(o[KEY_SCENE_PART])
        if pi >= len(data.parts):
            raise ValueError(f"part {pi} ('{o.name}') is out of range for {os.path.basename(src)} ({len(data.parts)} parts)")
        present.add(pi)
        if only is not None and pi not in only:
            continue
        part = data.parts[pi]
        built = build_part(o, data, part)
        notes += [f"{o.name}: {n}" for n in built.notes]
        unchanged = part_unchanged(built, data, part)
        if not force and unchanged:
            kept.append(pi)
            continue
        if not 0 <= part.mesh_index < n_forms:
            raise ValueError(f"'{o.name}' is not a PMSH mesh of the file (a GEOM block, as in projectiles and effects); "
                             f"rebuilding those is not supported")
        n_morphs = SM.morph_target_count(src_forms[part.mesh_index])
        if n_morphs and not drop_morphs and (legacy or not unchanged):
            raise ValueError(damage_states_refusal(o.name, n_morphs))
        report = {}
        raw = SM.write_scene_part(
            raw, part.mesh_index, [tuple(p) for p in built.positions.tolist()], [tuple(n) for n in built.normals.tolist()],
            [tuple(t) for t in built.uvs.tolist()], built.skin, built.submeshes, legacy=legacy,
            morphs="drop" if drop_morphs else "check", report=report)
        if report.get("morphs_dropped"):
            dropped[pi] = report["morphs_dropped"]
            notes.append(f"{o.name}: its {report['morphs_dropped']} damage states were left out (the part was edited)")
        rebuilt.append(pi)
    if rebuilt:
        problems = validate_scene_sgm(raw)
        errors = [p for p in problems if p.startswith("ERROR")]
        if errors:
            raise ValueError("the exported model would not load: " + "; ".join(e[:160] for e in errors))
        notes += [p for p in problems if not p.startswith("ERROR")]
    missing = [pi for pi in range(len(data.parts)) if pi not in present and (only is None or pi in only)]
    if missing:
        notes.append(f"{len(missing)} part(s) of the file have no object here ({', '.join(str(p) for p in missing[:6])}"
                     f"{'...' if len(missing) > 6 else ''}); they keep the source's geometry")
    return SceneExport(raw, rebuilt, kept, missing, notes, dropped)


def export_part_geometry(obj, legacy: bool = True) -> bytes:
    """Write ONE scene part's mesh (any topology) back into its source ``.sgm``, rebuilt even if unedited; the other parts,
    the skeleton, the clips and every other chunk are carried over from the source. See :func:`export_scene_object` for the
    whole object."""
    return export_scene_object(obj, legacy=legacy, only={int(obj[KEY_SCENE_PART])}, force=True).data
