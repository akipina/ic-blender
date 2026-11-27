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

import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np

import bpy

from . import core
from . import material
from . import rig
from . import store

KEY_SCENE = "bz_scene"           # truthy on every part object of an imported scene object
KEY_SCENE_PART = "bz_scene_part"  # part index
KEY_NORMALS_ERROR = "bz_normals_error"  # set on a part whose stored normals Blender refused (it shows its own smooth normals)
KEY_NEW_PART = "bz_new_part"     # truthy on a mesh made a part of a scene object in Blender (with its bz_source / bz_rig)
KEY_PART_LIKE = "bz_part_like"   # on a new part: the mesh whose clip visibility it copies ("" = visible in every clip)
KEY_REMOVE_PARTS = "bz_remove_parts"   # on a part object: the part numbers of the file the export removes (Use This Mesh as the Model)
KEY_SHOW_ALWAYS = "bz_show_always"     # on a part object: the export makes it visible in every clip (Use This Mesh as the Model)


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


# ------------------------------------------------------------------------------------ edits that export cannot save yet
KEY_SCENE_LOOK = "bz_scene_look"       # on an imported material: [its slot, what it showed]
KEY_SCENE_HASH = "bz_scene_hash"       # on an imported texture image: its pixels


def _value(v):
    if isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 4)
    if hasattr(v, "__len__"):
        return [_value(x) for x in v]
    return getattr(v, "name", None)


def _look(mat) -> str:
    """What a material shows: its nodes, their images and unlinked values, and the links (node positions don't count)."""
    seen = [_value(mat.diffuse_color)]
    tree = mat.node_tree
    if tree is not None:
        for node in sorted(tree.nodes, key=lambda n: n.name):
            image = getattr(node, "image", None)
            seen.append([node.bl_idname, image.name if image else "",
                         [_value(s.default_value) for s in node.inputs if not s.is_linked and hasattr(s, "default_value")]])
        seen += sorted([k.from_node.name, k.from_socket.identifier, k.to_node.name, k.to_socket.identifier] for k in tree.links)
    return json.dumps(seen)


def _remember_looks(mats, images):
    for img in images:
        img[KEY_SCENE_HASH] = str(material.pixel_crc(img))
    for slot, mat in enumerate(mats):
        mat[KEY_SCENE_LOOK] = [str(slot), _look(mat)]


def unsaved_looks(objs) -> list:
    """The material and texture edits on the parts ``objs`` since import, in words; export keeps the file's own."""
    found = {}
    for obj in objs:
        mats = [s.material for s in obj.material_slots]
        if not any(m is not None and m.get(KEY_SCENE_LOOK) for m in mats):
            continue                                        # imported by an earlier version: nothing to compare with
        for slot, mat in enumerate(mats):
            record = mat.get(KEY_SCENE_LOOK) if mat is not None else None
            if record is None or str(record[0]) != str(slot):
                found[f"material slot {slot + 1} of '{obj.name}' was given another material"] = True
                continue
            if _look(mat) != str(record[1]):
                found[f"material '{mat.name}' was edited"] = True
            for node in (mat.node_tree.nodes if mat.node_tree else ()):
                img = getattr(node, "image", None)
                if img is not None and img.get(KEY_SCENE_HASH) and str(material.pixel_crc(img)) != str(img[KEY_SCENE_HASH]):
                    found[f"texture '{img.name}' was painted"] = True
    return list(found)


def bone_changes(obj) -> list:
    """How the skeleton of ``obj`` differs from the imported one, in words (empty without a rig)."""
    arm = next((m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None) \
        or bpy.data.objects.get(obj.get("bz_rig", ""))
    if arm is None or arm.type != "ARMATURE":
        return []
    if arm.mode == "EDIT":
        arm.update_from_editmode()
    read = rig.rig_skeleton(arm)
    if read is None:
        return []
    out = read.resolution.changes()
    rest, names = list(arm.get(rig.KEY_BONE_REST, [])), read.resolution.current
    if len(rest) == 16 * len(names):
        for i, name in enumerate(names):
            bone = arm.data.bones.get(name) if name else None
            if bone is not None and not np.allclose([bone.matrix_local[r][c] for r in range(4) for c in range(4)],
                                                    rest[16 * i:16 * i + 16], atol=1e-5):
                out.append(f"bone '{name}' was moved")
    return out


def build_scene(context, data, source_path=None, clips_as_actions=True, markers=False):
    """Create the part objects (+ armature, materials, images) for a ``core.SceneData``; returns the
    list of mesh objects (the largest first). ``markers``: also build the attach points as empties
    (``sceneobj/slice_markers.py``; the plain model import does, the map importers do not)."""
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
        store.stamp_axes(arm_obj)

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
        store.stamp_axes(obj)                                   # made in the current axes (core.sgm_to_blender)
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
        if part.morphs:                                     # damage states as shape keys, their clips as Actions
            from . import scene_damage
            scene_damage.build_shape_keys(obj, data, part)
            scene_damage.import_key_clips(obj, data, part)
        objs.append(obj)

    if arm_obj is not None and clips_as_actions and data.animations:
        from . import actions
        try:
            actions.import_scene_clips(arm_obj, data)
        except Exception as e:
            arm_obj["bz_actions_error"] = str(e)

    _remember_looks(mats, images.values())
    _remember_shaders(mats, data, src, images)
    objs.sort(key=lambda o: -len(o.data.vertices))
    group = None
    if len(objs) > 1 and arm_obj is None:
        group = bpy.data.objects.new(data.name, None)
        store.stamp_axes(group)
        context.collection.objects.link(group)
        for o in objs:
            o.parent = group
    if markers and objs:
        from .sceneobj import slice_markers
        slice_markers.build_markers(context, data, src, arm_obj, group, objs)
    from . import scenesafety
    scenesafety.stamp_source(src)                           # the file the model was built on: an export checks it is still that file
    for o in context.view_layer.objects:
        o.select_set(False)
    if objs:
        objs[0].select_set(True)
        context.view_layer.objects.active = objs[0]
    return objs


def _remember_shaders(mats, data, src, images):
    """Record each material's source shader for the materials export (``scene_materials.remember``); skipped for a model
    that has no file of its own (a map's archived models)."""
    from . import scene_materials
    try:
        path = core.resolve_source(src) if src else ""
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                scene_materials.remember(mats, data, f.read(), images)
    except Exception as e:                              # noqa: BLE001 -- the import itself must not fail over this
        for mat in mats:
            mat["bz_shader_error"] = f"{type(e).__name__}: {e}"


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
    bind: object = "source"     # a rigid part read against a rig edit: the target bone it follows (None = none)


@dataclass
class SceneExport:
    data: bytes
    rebuilt: list               # part numbers written from the Blender mesh
    kept: list                  # part numbers whose source bytes were kept (unedited)
    missing: list               # parts of the file with no object in this scene: kept from the source
    notes: list
    damage_states_dropped: dict = field(default_factory=dict)   # part number -> damage states written out of it
    files: dict = field(default_factory=dict)   # texture files the model needs beside it: lower-case path inside Data -> bytes
    skeleton: object = None     # ICParse SkeletonReport when the skeleton was written (scene_skeleton)
    rig_edit: object = None     # the scene_skeleton.RigEdit the export used
    markers: dict = field(default_factory=dict)   # what happened to the attach points (ICParse write_scene_markers report)
    parts_added: list = field(default_factory=list)     # [(object name, part number in the written file)] new parts
    parts_removed: list = field(default_factory=list)   # names of the parts removed because their object was deleted
    converted: bool = False     # the model's single mesh was put into a part group to hold the new parts

    @property
    def markers_added(self) -> list:
        return list(self.markers.get("added", []))

    @property
    def markers_removed(self) -> list:
        return list(self.markers.get("removed", []))

    @property
    def markers_changed(self) -> list:
        return list(self.markers.get("changed", []))

    @property
    def markers_renamed(self) -> list:
        return list(self.markers.get("renamed", []))
    morphs: dict = field(default_factory=dict)  # part number -> {kept, encoded, added, removed, forced_v2} of its damage states


def damage_states_refusal(name: str, count: int) -> str:
    """Why an edited part with damage states (morph targets) but no shape keys for them is not exported."""
    from . import scene_damage
    return scene_damage.damage_states_refusal(name, count)


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


def model_root(obj):
    """The object whose place is the model's origin for the part ``obj``: its rig, else the group empty the parts hang
    under, else the part itself (a single part with no rig: moving it moves the whole model, as its markers do). A part's
    model-space mesh is its vertices through ``inverse(root.matrix_world) @ obj.matrix_world``, the identity for every
    imported part."""
    from . import scene_skeleton
    arm = scene_skeleton.rig_of(obj)
    if arm is not None:
        return arm
    p = obj.parent
    return p if p is not None and p.type == "EMPTY" else obj


def part_matrix(o, root):
    """``inverse(root.matrix_world) @ o.matrix_world`` as a 4x4 numpy array (Blender space), None when it is the identity
    (to 1e-6). A mesh parented to a bone is placed by the bone's REST, so a clip posing the rig does not move it."""
    M = o.matrix_world
    if o.parent is not None and o.parent_type == "BONE" and o.parent.type == "ARMATURE" and o.parent_bone in o.parent.data.bones:
        from mathutils import Matrix
        b = o.parent.data.bones[o.parent_bone]
        M = o.parent.matrix_world @ b.matrix_local @ Matrix.Translation((0.0, b.length, 0.0)) @ o.matrix_parent_inverse \
            @ o.matrix_basis
    if root is not None:
        M = root.matrix_world.inverted_safe() @ M
    A = np.array([[M[r][c] for c in range(4)] for r in range(4)], dtype=np.float64)
    return None if np.allclose(A, np.eye(4), atol=1e-6) else A


def new_part_objects(obj) -> list:
    """The meshes that become NEW parts of the scene object ``obj`` belongs to, by name: those stamped ``bz_new_part`` with
    its source and rig (*Make Part of Scene Object*), and any other mesh that is no part of a scene object but a child of
    its rig or group empty. Only meshes in a scene count."""
    src, rig_name = obj.get("bz_source"), obj.get("bz_rig")
    root = model_root(obj)
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH" or o.get(KEY_SCENE_PART) is not None or not o.users_scene:
            continue
        if o.get(KEY_NEW_PART):
            if o.get("bz_source") == src and o.get("bz_rig") == rig_name:
                out.append(o)
        elif not o.get(KEY_SCENE) and o.parent is root:
            out.append(o)
    return sorted(out, key=lambda o: o.name)


def new_part_binding(o, skel):
    """How a new part moves: ``("skin", None)`` (weighted to several bones), ``("rigid", target)`` (it follows one bone of
    the rig edit ``skel``: parented to that bone, or one vertex group at full weight on every vertex) or ``("rigid",
    None)`` (it does not move: no skeleton, or no group naming a bone)."""
    if skel is None:
        return "rigid", None
    if o.parent is not None and o.parent_type == "BONE" and o.parent_bone:
        t = skel.target_of(o.parent_bone)
        if t is None:
            raise ValueError(f"'{o.name}' is parented to bone '{o.parent_bone}', which the model's skeleton does not have")
        return "rigid", t
    me = o.data
    used = np.unique(_foreach(me.loops, "vertex_index", np.int64)) if len(me.loops) else np.zeros(0, np.int64)
    weights = {}
    for vi in used.tolist():
        for g in me.vertices[vi].groups:
            if g.weight > 0.0:
                weights.setdefault(g.group, []).append(float(g.weight))
    live = [g for g in o.vertex_groups if skel.target_of(g.name) is not None and weights.get(g.index)]
    if not live:
        return "rigid", None
    if len(live) == 1 and len(weights[live[0].index]) == len(used) and min(weights[live[0].index]) >= 0.999:
        return "rigid", skel.target_of(live[0].name)
    return "skin", None


def _foreach(coll, attr, dtype, per=1):
    a = np.empty(len(coll) * per, dtype=dtype)
    coll.foreach_get(attr, a)
    return a.reshape(-1, per) if per > 1 else a


def build_part(obj, data, part, slot_names=None, skel=None, matrix=None) -> BuiltPart:
    """Read a part object's mesh as the file stores it. Seams are split where a vertex's corners disagree on UV, or where a
    face is flat-shaded (its corners take the face normal); everything else shares one vertex and takes the smooth normal.
    Output vertices keep the Blender vertex order (copies from splitting are appended after), so an unedited mesh maps back
    to exactly the vertices it was imported from. Vertices no face uses are not written. ``slot_names``: the shader name of
    each material slot (the materials export's ``slot_names``); without it a slot is the imported material of its number.
    ``matrix``: the object's place in the model (:func:`part_matrix`, Blender space), applied to its vertices and normals;
    a mirroring matrix also turns the triangles round, so they keep facing out."""
    me = obj.data
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    if not len(me.polygons):
        raise ValueError(f"part '{obj.name}' has no faces")
    me.calc_loop_triangles()
    nl, nv, npoly, nt = len(me.loops), len(me.vertices), len(me.polygons), len(me.loop_triangles)
    notes = []
    tri_loops = _foreach(me.loop_triangles, "loops", np.int64, 3)[:, [0, 2, 1]]   # the file's winding
    tri_poly = _foreach(me.loop_triangles, "polygon_index", np.int64)
    loop_vert = _foreach(me.loops, "vertex_index", np.int64)
    poly_mat = _foreach(me.polygons, "material_index", np.int64)
    poly_smooth = _foreach(me.polygons, "use_smooth", np.bool_)
    poly_nrm = _foreach(me.polygons, "normal", np.float64, 3)
    co = _foreach(me.vertices, "co", np.float64, 3)
    vnrm = _foreach(me.vertices, "normal", np.float64, 3)
    mirrored = False
    if matrix is not None:                                      # moved in Object Mode / a new part placed in the model
        A = np.asarray(matrix, dtype=np.float64)
        co = co @ A[:3, :3].T + A[:3, 3]
        Nm = np.linalg.inv(A[:3, :3]).T
        vnrm, poly_nrm = vnrm @ Nm.T, poly_nrm @ Nm.T             # normalised below
        mirrored = bool(np.linalg.det(A[:3, :3]) < 0)
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
        raise ValueError(f"part '{obj.name}' comes to {n_out:,} vertices once split at UV seams and flat faces; the game "
                         f"draws at most {MAX_PART_VERTICES:,} per part. Reduce its detail (Decimate, fewer seams) or split "
                         "it into two parts")
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
    if mirrored:
        tri_out = tri_out[:, [0, 2, 1]]
    if not len(tri_out):
        raise ValueError(f"part '{obj.name}' has no usable triangles")
    _, fi = np.unique(tri_mat, return_index=True)
    submeshes = []
    for m in tri_mat[np.sort(fi)].tolist():
        if slot_names is not None and 0 <= m < len(slot_names) and slot_names[m]:
            submeshes.append((slot_names[m], [tuple(t) for t in tri_out[tri_mat == m].tolist()]))
            continue
        if not 0 <= m < len(data.materials):
            raise ValueError(f"part '{obj.name}': material slot {m + 1} has no shader in the file ({len(data.materials)} "
                             f"come from the import) -- give those faces one of the imported materials")
        submeshes.append((data.materials[m]["name"], [tuple(t) for t in tri_out[tri_mat == m].tolist()]))

    skin = None
    bind = "source"
    if part.skin:                                               # a skinned part: every vertex carries bone weights
        if skel is not None:                                    # the rig's bones as the export writes them (scene_skeleton)
            group_bone = {g.index: skel.target_of(g.name) for g in obj.vertex_groups}
            dead = {g.index: skel.deleted_groups[g.name] for g in obj.vertex_groups
                    if group_bone[g.index] is None and g.name in skel.deleted_groups}
            root = next(t for t, b in enumerate(skel.edit.bones) if b.src == 0)
            root_name = skel.rig_names[root]
        else:
            bone_of = {(n or f"bone{i}").lower(): i for i, n in enumerate(data.bone_names)}
            group_bone = {g.index: bone_of.get(g.name.lower()) for g in obj.vertex_groups}
            dead = {}
            root = next((i for i, p in enumerate(data.bone_parents) if p < 0), 0)
            root_name = data.bone_names[root] or root
        cache, bare, skin, only_dead, part_dead = {}, 0, [], [], set()
        for vi in src_v.tolist():
            infl = cache.get(vi)
            if infl is None:
                groups = me.vertices[vi].groups
                infl = [(group_bone[g.group], float(g.weight)) for g in groups
                        if group_bone.get(g.group) is not None and g.weight > 0.0]
                gone = [dead[g.group] for g in groups if g.group in dead and g.weight > 0.0]
                part_dead.update(gone)
                if not infl:
                    if gone:
                        only_dead.append(vi)
                    infl = [(root, 1.0)]
                    bare += 1
                cache[vi] = infl
            skin.append(infl)
        if only_dead:
            raise ValueError(f"{len(only_dead)} vertices of '{obj.name}' are weighted only to deleted bone(s) "
                             f"{', '.join(repr(n) for n in sorted(part_dead)[:3])}, so they follow no bone in Blender. "
                             "Undo the delete and use Armature > Delete Bones, Merge Weights (it hands the weights to "
                             "the parent), or weight those vertices to another bone")
        if part_dead:
            notes.append(f"weights on deleted bone(s) {', '.join(repr(n) for n in sorted(part_dead)[:3])} were left out, "
                         "as Blender ignores them")
        if bare:
            notes.append(f"{bare} vertices have no bone weights; they were bound to the root bone "
                         f"'{root_name}' (they would not follow the skeleton in Blender)")
    elif skel is not None and (part.bind is not None or len(obj.vertex_groups)):
        bind = _rigid_bind(obj, me, src_v, skel, notes)
    return BuiltPart(positions=core.blender_to_sgm(co[src_v]), normals=core.blender_to_sgm(nrm), uvs=out_uv, skin=skin,
                     submeshes=submeshes, source_vertex=src_v, notes=notes, bind=bind)


def _rigid_bind(obj, me, src_v, skel, notes):
    """The bone a rigid part follows, read like Blender deforms it: the live vertex group every vertex has at full weight
    (or the heaviest one, with a note), None when no group names a bone of the rig."""
    used = np.unique(src_v)
    total = {}
    for vi in used.tolist():
        for g in me.vertices[vi].groups:
            if g.weight > 0.0:
                total.setdefault(g.group, []).append(float(g.weight))
    best, best_w = None, 0.0
    for g in obj.vertex_groups:
        t = skel.target_of(g.name)
        ws = total.get(g.index, [])
        if t is None or not ws:
            continue
        if len(ws) == len(used) and min(ws) >= 0.999:
            return t
        if sum(ws) > best_w:
            best, best_w = t, sum(ws)
    if best is None:
        dead = sorted({skel.deleted_groups[g.name] for g in obj.vertex_groups
                       if g.name in skel.deleted_groups and skel.target_of(g.name) is None and total.get(g.index)})
        if dead:
            raise ValueError(f"'{obj.name}' is a rigid part that follows only deleted bone(s) "
                             f"{', '.join(repr(n) for n in dead[:3])}, so it would follow no bone. Undo the delete and use "
                             "Armature > Delete Bones, Merge Weights (the part then follows the parent), or assign the "
                             "part to another bone's vertex group")
    if best is not None:
        notes.append(f"is a rigid part (the game moves it with one bone) but its vertices are weighted to several bones or "
                     f"below full weight: it follows '{skel.rig_names[best]}', its heaviest group")
    return best


def _source_submeshes(data, part, remap=None):
    """The source part's triangles per shader, in order of first use -- the shape ``build_part`` produces. ``remap``: old vertex
    index -> index after the vertices no triangle uses are left out."""
    subs, seen = {}, []
    for tri, mi in zip(part.tris.tolist(), part.tri_mat.tolist()):
        if mi not in subs:
            subs[mi] = []
            seen.append(mi)
        tri = (tri[0], tri[2], tri[1])                          # the file's winding
        subs[mi].append(tuple(int(remap[i]) for i in tri) if remap is not None else tuple(int(i) for i in tri))
    return [(data.materials[mi]["name"] if mi < len(data.materials) else f"mat{mi}", subs[mi]) for mi in seen]


def _skin_key(infl):
    top = sorted(infl, key=lambda t: -t[1])[:4]
    total = sum(w for _, w in top) or 1.0
    return {int(b): w / total for b, w in top if w / total > 5e-4}


def part_unchanged(built: BuiltPart, data, part, skel=None) -> bool:
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
            if skel is not None:                    # compare bones by identity: the part keeps its bytes through a rename
                a = [(skel.src_of(t) if skel.src_of(t) is not None else -1 - t, w) for t, w in a]
            ka, kb = _skin_key(a), _skin_key(b)
            if ka.keys() != kb.keys() or any(abs(ka[k] - kb[k]) > 2e-3 for k in ka):
                return False
    return True


def export_scene_object(obj, legacy: bool = True, only=None, force: bool = False, drop_morphs: bool = False,
                        entity: str = "", carry_over: bool = False, remove_missing: bool = False,
                        refuse_errors: bool = True) -> SceneExport:
    """The scene object ``obj`` belongs to, as ``.sgm`` bytes: every part whose mesh was edited is rebuilt from its Blender
    object (any topology -- new or deleted faces, seams, UV edits, new bone weights, changed material slots) into the
    source file, and every part left as imported keeps the source's bytes, so an unedited object comes back byte-identical.
    The rig's clips are not touched here (``bz.export_scene`` adds them). The result is checked with the scene validator
    and refused if it has errors.

    ``only``: consider just these part numbers (the rest stay as in the source). ``force``: rebuild them even if unedited.
    ``legacy=True`` writes the v1.2 layout so the engine rebuilds the detail ladder itself (the path through the real
    game); ``False`` keeps v2.0 with one full-detail record (loads in the Object Editor, not yet seen in the game).

    Damage states (morph targets) are the part's shape keys (``scene_damage``): unedited keys keep their stored bytes,
    edited ones are re-encoded over the written vertices, added and deleted keys add and remove targets (the clips follow),
    and a part that carries any is written in the v2.0 layout; ``morphs`` reports it per part. A part whose source has
    damage states but which has no shape keys (imported by an older version) keeps them while unedited; edited, it is
    refused, unless ``carry_over`` carries them onto the new mesh or ``drop_morphs`` leaves them out (counted in
    ``damage_states_dropped``). Edited shape-key Actions write their clip's weights. Every rebuilt part gets
    the bounding volumes the game builds (its own, one per palette bone), and the entity box follows it (ICParse
    ``write_scene_part``).

    Materials and textures go first (``scene_materials.export_materials``): an untouched material keeps its shader's bytes;
    a painted or replaced image, a new material, an opacity or team-colour change is written, and the texture files the
    model then needs are returned in ``files`` (named after ``entity``, default the source's name), with any texture the
    model loads that the game lacks copied from the source's own Data folder (an earlier export's).

    Parts (ICParse ``sgm.scene.parts``): every part is read in model space (:func:`part_matrix`), so a part moved in
    Object Mode is written where it is. The meshes of :func:`new_part_objects` become NEW parts after the file's own
    (a rigid part following one bone, a skinned one, or one that does not move: :func:`new_part_binding`), each visible in
    every clip (or showing and hiding like ``bz_part_like``); a model with one top-level mesh is put into a part group
    first. ``remove_missing``: the parts whose object was deleted are removed from the file (with their clip channels)
    instead of keeping the source's geometry. ``parts_added``, ``parts_removed`` and ``converted`` report it.

    ``refuse_errors=False`` ("Export anyway"): an error of the load validation is kept as a note ("written anyway: ...") instead of refusing the export."""
    from ICParse.sgm.scene import model as SM
    from . import scene_materials
    from ICParse.sgm.scene.validate import validate_scene_sgm

    src = obj.get("bz_source", "")
    if not src or obj.get(KEY_SCENE_PART) is None:
        raise ValueError("not an imported scene part")
    data = core.load_scene(src)
    raw = open(core.resolve_source(src), "rb").read()
    objs = part_objects(obj)
    from . import scene_skeleton
    why = store.axes_refusal(*objs, scene_skeleton.rig_of(obj))     # an object imported before the axis flip is in the old axes
    if why:
        raise ValueError(why)
    root = model_root(obj)
    new_objs = new_part_objects(obj) if only is None else []
    skel = scene_skeleton.read_rig(obj, raw)          # the rig's bone edits (renames, moves, adds, deletes, re-parents)
    src_forms = SM.scene_pmsh_forms(SM.IffDocument.from_bytes(raw))
    n_forms = len(src_forms)
    rebuilt, kept, notes, present, dropped = [], [], [], set(), {}
    from . import scene_damage
    morph_report, written_keys, source_raw = {}, {}, raw
    archives = scene_materials.game_archives()        # 1. materials and textures (SHDR, TXRF, TXTR)
    looks = scene_materials.export_materials(objs + new_objs, data, raw, entity=entity, resolver=archives,
                                             loose_root=scene_materials.data_folder_of(core.resolve_source(src)))
    files = {**looks.carried, **looks.files}
    raw = looks.sgm
    notes += looks.notes + scene_materials.other_edits(objs)
    jobs, binds = [], {}
    for o in objs:
        pi = int(o[KEY_SCENE_PART])
        if pi >= len(data.parts):
            raise ValueError(f"part {pi} ('{o.name}') is out of range for {os.path.basename(src)} ({len(data.parts)} parts)")
        present.add(pi)
        if only is not None and pi not in only:
            continue
        part = data.parts[pi]
        placed = part_matrix(o, root)
        built = build_part(o, data, part, looks.slot_names.get(o.name), skel, matrix=placed)
        notes += [f"{o.name}: {n}" for n in built.notes]
        unchanged = part_unchanged(built, data, part, skel)
        if placed is not None and not unchanged:
            notes.append(f"{o.name}: was moved, turned or scaled in Object Mode; written where it is now in the model")
        if skel is not None and built.bind != "source" and 0 <= part.mesh_index < n_forms:
            was = next((t for t, b in enumerate(skel.edit.bones) if part.bind is not None and b.src == int(part.bind)), None)
            if built.bind != was:
                binds[part.mesh_index] = built.bind        # a rigid part re-bound (Ctrl+G, a deleted bone)
        src_form = src_forms[part.mesh_index] if 0 <= part.mesh_index < n_forms else None
        mplan = scene_damage.plan_part(o, data, part, built, unchanged, src_form, drop_morphs, carry_over)
        if not force and unchanged and mplan.untouched:
            kept.append(pi)
            continue
        if src_form is None:
            raise ValueError(f"'{o.name}' is not a mesh of the model file (it is a projectile or effect model); "
                             f"rebuilding those is not supported")
        jobs.append((o, pi, part, built, mplan, unchanged))
    new_jobs = []                                           # new parts: read now (the rig's targets), written after the rest
    for o in new_objs:
        kind, target = new_part_binding(o, skel)
        like_part = SimpleNamespace(skin=[[(0, 1.0)]] if kind == "skin" else [], bind=None)
        built = build_part(o, data, like_part, looks.slot_names.get(o.name), skel if kind == "skin" else None,
                           matrix=part_matrix(o, root))
        if kind == "rigid":
            built.bind = target
        if not o.material_slots:
            notes.append(f"{o.name}: has no material; its faces use the model's first, '{data.materials[0]['name']}'"
                         if data.materials else f"{o.name}: has no material")
        notes += [f"{o.name}: {n}" for n in built.notes]
        new_jobs.append((o, built, kind))
    skeleton = None                                         # 2. the skeleton (SKEL, XFRM, palettes, binds, MRKR bones, clips)
    if skel is not None and (skel.changed or binds or jobs):
        from ICParse.sgm.scene import skeleton as SK
        skel.edit.part_bones = {part.mesh_index: sorted({t for infl in built.skin for t, _w in infl})
                                for _o, _pi, part, built, _mp, _u in jobs if built.skin}
        skel.edit.binds = binds
        raw, skeleton = SK.write_skeleton(raw, skel.edit)
        notes += [f"skeleton: {line}" for line in skeleton.summary()]
    for o, pi, part, built, mplan, unchanged in jobs:      # 3. the parts, with their damage states (morph targets)
        skin = built.skin
        if skin and skel is not None:                       # skeleton bones -> this part's palette slots
            skin = scene_skeleton.skin_slots(raw, part.mesh_index, skel, skin)
        report = {}
        raw = scene_damage.write_part(raw, part, built, mplan, legacy, unchanged, force, report, skin=skin)
        notes += mplan.notes
        mw = report.get("morphs")
        if mw is not None:
            raw = scene_damage.after_write(raw, scene_damage.mesh_name(data, part), mw, notes)
            morph_report[pi] = {"kept": mw.kept, "encoded": mw.encoded, "added": mw.added, "removed": len(mw.removed),
                                "forced_v2": bool(report.get("forced_v2"))}
            written_keys[pi] = mplan.names
            if mplan.left_out:
                dropped[pi] = mplan.left_out
            if report.get("forced_v2"):
                notes.append(f"{o.name}: written in the v2.0 layout, because it has damage states (one detail level; the game "
                             f"may not load it)")
        rebuilt.append(pi)
    raw = scene_damage.write_key_clips(raw, objs if only is None else [o for o in objs if int(o[KEY_SCENE_PART]) in only],
                                       data, written_keys, notes)  # 4. edited shape-key Actions: their clips' weights
    from .sceneobj import slice_markers                     # 5. the markers (MRKR), against the written skeleton
    raw, marker_report, marker_notes = slice_markers.apply_to_export(obj, data, raw)
    notes += marker_notes
    moved = [part.mesh_index for _o, _pi, part, _b, _m, unchanged in jobs if not unchanged and 0 <= part.mesh_index < n_forms]
    if moved:                       # a part whose geometry changed may outgrow the clips' stored boxes: it would be culled and
        from ICParse.sgm.scene import parts as SP_      # picked by the old size (AnimPlayList::GetBounds), so the boxes grow
        raw = SP_.grow_bounds_for_parts(raw, moved, before=source_raw)    # by what the edit added to the part, no more
    shown = [(o, int(o[KEY_SCENE_PART])) for o in objs if o.get(KEY_SHOW_ALWAYS) and only is None
             and 0 <= data.parts[int(o[KEY_SCENE_PART])].mesh_index < n_forms]
    if shown:                       # a mesh that is the whole model must not be hidden by the clips that hid its old stage
        from ICParse.sgm.scene import parts as SP_
        for o, pi in shown:
            doc_ = SM.IffDocument.from_bytes(raw)
            clips_ = SP_.show_mesh_always(doc_, data.parts[pi].mesh_index)
            raw = doc_.to_bytes()
            if clips_:
                notes.append(f"{o.name}: made visible in {len(clips_)} clip(s) that hid it ({', '.join(clips_[:4])}"
                             f"{' ...' if len(clips_) > 4 else ''})")
    missing = [pi for pi in range(len(data.parts)) if pi not in present and (only is None or pi in only)]
    explicit = {int(x) for o in objs for x in (o.get(KEY_REMOVE_PARTS) or [])} if only is None else set()
    drop = missing if remove_missing else [pi for pi in missing if pi in explicit]
    # 6. the parts that leave go first, so a new part may take the name of one deleted in the same export (the clips find a mesh by
    # name, and add_scene_part refuses a name the model still has). An object of the model is always left (this export is called
    # on one, and it is never in ``missing``), so the model never loses its last node here. 7. then the new parts, appended after
    # the parts that stay, so the number each reports is the one in the file as written.
    raw, removed, left, gone = _remove_parts(raw, data, drop, n_forms, notes)
    raw, added, converted = _add_parts(raw, new_jobs, skel, legacy, notes)
    missing = sorted([pi for pi in missing if pi not in drop] + left)
    if rebuilt or raw != source_raw:
        problems = validate_scene_sgm(raw, files=files, resolver=archives, new_refs=looks.new_refs,
                                      new_submesh_shaders=looks.new_shaders)
        errors = [p for p in problems if p.startswith("ERROR")]
        if errors and refuse_errors:
            raise ValueError("the exported model would not load: " + "; ".join(e[:160] for e in errors))
        notes += [("written anyway, the game may refuse it: " + p) if p.startswith("ERROR") else p for p in problems]
    if missing:
        notes.append(f"{len(missing)} part(s) of the file have no object here ({', '.join(str(p) for p in missing[:6])}"
                     f"{'...' if len(missing) > 6 else ''}); they keep the source's geometry"
                     + ("" if remove_missing or only is not None or not [p for p in missing if p not in explicit] else
                        " (turn on 'Remove parts deleted in Blender' to remove them)"))
    return SceneExport(raw, rebuilt, kept, missing, notes, dropped, files=files, skeleton=skeleton, rig_edit=skel,
                       markers=marker_report, morphs=morph_report, parts_added=added, parts_removed=removed,
                       converted=converted)


def _written_bone(skel, t) -> str:
    """The name the written skeleton gives rig-edit target ``t`` (as a C string: the game reads one)."""
    return skel.plan.names[skel.plan.new_of_target[t]].split("\x00", 1)[0]


def _remove_parts(raw, data, missing, n_forms, notes):
    """Remove the parts ``missing`` (part numbers of ``data``) from ``raw``, the highest mesh number first so the others
    keep theirs. Returns ``(bytes, removed names, parts left as they were, the mesh numbers removed)``."""
    from ICParse.sgm.scene import parts as SP
    removed, kept, gone = [], [], []
    for pi in sorted(missing, key=lambda p: -data.parts[p].mesh_index):
        part = data.parts[pi]
        if not 0 <= part.mesh_index < n_forms:
            notes.append(f"part '{part.name}' (a projectile or effect model) was deleted in Blender but cannot be removed; it stays")
            kept.append(pi)
            continue
        rep = SP.PartReport()
        try:
            raw = SP.remove_scene_part(raw, part.mesh_index, report=rep)
        except SP.PartError as e:
            raise ValueError(f"part '{part.name}' was deleted in Blender but cannot be removed: {e}")
        removed.append(part.name)
        gone.append(part.mesh_index)
        notes += [f"removing '{part.name}': {n}" for n in rep.notes]
    if removed:
        notes.append(f"{len(removed)} part(s) deleted in Blender were removed, with their clip channels: "
                     + ", ".join(repr(n) for n in removed[:6]) + (" ..." if len(removed) > 6 else ""))
    return raw, removed, sorted(kept), gone


def _add_parts(raw, new_jobs, skel, legacy, notes):
    """Write the new parts ``new_jobs`` (``(object, BuiltPart, kind)``) into ``raw`` (ICParse ``parts.add_scene_part``).
    Returns ``(bytes, [(name, part number)], whether the model was put into a part group)``."""
    if not new_jobs:
        return raw, [], False
    from ICParse.sgm.scene import parts as SP
    added, converted = [], False
    for o, built, kind in new_jobs:
        skin = [[(_written_bone(skel, t), w) for t, w in infl] for infl in built.skin] if kind == "skin" else None
        bind = _written_bone(skel, built.bind) if kind == "rigid" and built.bind is not None else None
        rep = SP.PartReport()
        try:
            raw = SP.add_scene_part(raw, SP.NewPart(o.name, built.positions, built.normals, built.uvs, built.submeshes,
                                                    skin=skin, bind=bind, like=o.get(KEY_PART_LIKE) or None),
                                    legacy=legacy, report=rep)
        except SP.PartError as e:
            raise ValueError(f"new part '{o.name}': {e}")
        added.append((o.name, rep.part))
        converted = converted or rep.converted
        how = (f"skinned to {len(rep.palette)} bone(s)" if kind == "skin" else
               f"follows bone '{rep.bind}'" if rep.bind else "does not move with any bone")
        notes.append(f"{o.name}: added as part {rep.part}, {how}; visible in {len(rep.clips_named)} clip(s)"
                     + (f", {len(rep.clips_grown)} clip box(es) grown to hold it" if rep.clips_grown else ""))
        notes += rep.notes
    return raw, added, converted


def export_part_geometry(obj, legacy: bool = True) -> bytes:
    """Write ONE scene part's mesh (any topology) back into its source ``.sgm``, rebuilt even if unedited; the other parts,
    the skeleton, the clips and every other chunk are carried over from the source. See :func:`export_scene_object` for the
    whole object."""
    return export_scene_object(obj, legacy=legacy, only={int(obj[KEY_SCENE_PART])}, force=True).data
