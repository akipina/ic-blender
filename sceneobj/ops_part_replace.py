"""Replace Part With Selected Mesh and Use This Mesh as the Model: put another mesh in the place of one part, or of the whole model,
of a scene object (IC Model tab, *Parts*).

*Replace Part With Selected Mesh* swaps the geometry of the active part for the selected mesh. The part object stays what it was --
its part number, its name (the clips find the part by it), its armature, its modifiers and the bone it follows -- so the export
writes it through the ordinary edited-part path (ICParse ``write_scene_part``: the mesh limits, the detail records, the bounding
volumes, the clip boxes) into the very part it replaces. Nothing is removed first, so a model's only part, or every part of it, can
be replaced. The part keeps its bone binding: a rigid part's new mesh is put on the same bone at full weight, a skinned part's weights
are copied from the old mesh by Blender's data transfer (nearest surface), and a mesh that already carries weights for the rig's
bones keeps them.

*Use This Mesh as the Model* does that for a whole model: the mesh is fitted to the size and place of the model's parts, replaces the
largest part, and the other parts are marked for removal (``scene.KEY_REMOVE_PARTS``), so the export writes a model of one part.
"""
from __future__ import annotations

import bpy
import bmesh
import numpy as np
from bpy.props import BoolProperty, EnumProperty
from mathutils import Matrix

from .. import scene as _scene

FIT_ITEMS = (
    ("INSIDE", "Fit inside the model's box", "Scale the mesh evenly so it fits inside the box of the model it replaces"),
    ("HEIGHT", "Match the model's height", "Scale the mesh evenly to the height of the model it replaces"),
    ("NONE", "Keep its size", "Do not scale the mesh; only put it where the model stood"),
)
MATERIAL_ITEMS = (
    ("KEEP", "The part's materials", "Every face takes the material of the part it replaces (a face whose material has the same "
                                     "name as one of the part's keeps it)"),
    ("ADD", "The mesh's own materials", "The mesh's materials are added to the part's, and its faces keep them; the export "
                                        "makes a shader of each (with its image as the texture)"),
)


# ------------------------------------------------------------------------------------------------------------------ helpers
def _numbers(a) -> np.ndarray:
    return np.array([[a[r][c] for c in range(4)] for r in range(4)], dtype=np.float64)


def scene_part_of(context):
    """``(part, [meshes to put in it])``: the active object when it is an imported part, else the one selected part; the
    meshes are the selected ones that are not imported parts (a mesh made a new part is taken too)."""
    sel = list(context.selected_objects or [])
    act = context.active_object
    isp = lambda o: o is not None and o.type == "MESH" and o.get(_scene.KEY_SCENE) and o.get(_scene.KEY_SCENE_PART) is not None
    part = act if isp(act) else next((o for o in sel if isp(o)), None)
    src = [o for o in sel if o.type == "MESH" and o is not part and not o.get(_scene.KEY_SCENE)]
    return part, src


def rigid_group(part):
    """The name of the vertex group the part follows as a rigid part -- a live group at full weight on every vertex a face
    uses, as the importer writes a bound rigid part -- or None (a skinned part, or one with no weights)."""
    me = part.data
    if not len(me.polygons):
        return None
    used = np.unique(_scene._foreach(me.loops, "vertex_index", np.int64))
    names = {g.index: g.name for g in part.vertex_groups}
    counts, low = {}, {}
    for vi in used.tolist():
        for g in me.vertices[vi].groups:
            if g.weight > 0.0:
                counts[g.group] = counts.get(g.group, 0) + 1
                low[g.group] = min(low.get(g.group, 1.0), float(g.weight))
    live = [gi for gi, n in counts.items() if n == len(used) and low[gi] >= 0.999 and gi in names]
    return names[live[0]] if len(live) == 1 and len(counts) == 1 else None


def has_weights(part) -> bool:
    return any(len(v.groups) for v in part.data.vertices)


def model_box(parts, root):
    """``(low, high)`` corners of the box around the parts, in the model root's own (Blender) coordinates."""
    Rinv = _numbers(root.matrix_world.inverted_safe())
    pts = []
    for o in parts:
        W = Rinv @ _numbers(o.matrix_world)
        c = np.array([tuple(v) + (1.0,) for v in o.bound_box], dtype=np.float64)
        pts.append((c @ W.T)[:, :3])
    pts = np.vstack(pts)
    return pts.min(0), pts.max(0)


def fit_matrix(lo, hi, src_lo, src_hi, mode: str = "INSIDE", align: bool = True) -> Matrix:
    """The 4x4 that scales the box ``src_lo..src_hi`` evenly (``mode``) and puts it at the centre of ``lo..hi`` on the floor of it
    (``align``), all in the model root's coordinates."""
    d, ds = np.asarray(hi) - np.asarray(lo), np.asarray(src_hi) - np.asarray(src_lo)
    s = 1.0
    if mode == "INSIDE":
        ratios = [d[i] / ds[i] for i in range(3) if ds[i] > 1e-9 and d[i] > 1e-9]
        s = min(ratios) if ratios else 1.0
    elif mode == "HEIGHT" and ds[2] > 1e-9 and d[2] > 1e-9:
        s = d[2] / ds[2]
    ctr = (np.asarray(src_lo) + np.asarray(src_hi)) / 2.0
    want = (np.asarray(lo) + np.asarray(hi)) / 2.0
    base = np.array([ctr[0], ctr[1], src_lo[2]])
    to = np.array([want[0], want[1], lo[2]]) if align else base * s
    return Matrix.Translation(tuple(to)) @ Matrix.Diagonal((s, s, s, 1.0)) @ Matrix.Translation(tuple(-base))


class Replaced:
    """What :func:`replace_part_mesh` did, for the report."""

    def __init__(self):
        self.vertices = 0
        self.faces = 0
        self.weights = "none"          # "rigid" | "own" | "transferred" | "none"
        self.scale = 1.0
        self.damage_states = 0         # shape keys the old mesh had (the new one has none)
        self.unmatched = 0             # faces whose material the part does not have (KEEP): they took the part's first
        self.notes = []


def _transfer_weights(target, donor):
    """Copy the vertex weights of the object ``donor`` onto ``target`` with Blender's Data Transfer modifier (vertex group
    weights, nearest surface, groups matched by name; the groups the target lacks are created) and apply it. ``target`` holds a
    mesh no other object uses. (The Data Transfer operator reports success in a background Blender and copies nothing; the
    modifier does the same work and does copy.)"""
    for g in donor.vertex_groups:
        if target.vertex_groups.get(g.name) is None:
            target.vertex_groups.new(name=g.name)
    mod = target.modifiers.new("bz_weights", "DATA_TRANSFER")
    mod.object = donor
    mod.use_vert_data = True
    mod.data_types_verts = {"VGROUP_WEIGHTS"}
    mod.vert_mapping = "POLYINTERP_NEAREST"
    mod.layers_vgroup_select_src = "ALL"
    mod.layers_vgroup_select_dst = "NAME"
    mod.use_object_transform = True
    bpy.context.view_layer.update()
    with bpy.context.temp_override(object=target, active_object=target, selected_objects=[target],
                                   selected_editable_objects=[target]):
        res = bpy.ops.object.modifier_apply(modifier=mod.name)
    if res != {"FINISHED"}:
        raise RuntimeError("Blender's Data Transfer did not run")
    if not any(len(v.groups) for v in target.data.vertices):
        raise RuntimeError("Blender's Data Transfer found no weights to copy")


def _donor_of(part):
    """A hidden copy of the part's mesh with its vertex groups, to take weights from once the part holds the new mesh."""
    donor = bpy.data.objects.new(part.name + ".weights", part.data)      # the group names live on the mesh: it brings them
    (part.users_collection[0] if part.users_collection else bpy.context.scene.collection).objects.link(donor)
    donor.matrix_world = part.matrix_world
    return donor


def joined_donor(parts):
    """One object holding every part of ``parts`` that has weights (copies; the vertex groups joined by name), in the place they
    stand: the weight source for a mesh that replaces a whole skinned model."""
    coll = parts[0].users_collection[0] if parts[0].users_collection else bpy.context.scene.collection
    copies = []
    for o in parts:
        if not has_weights(o):
            continue
        d = o.copy()
        d.data = o.data.copy()
        d.shape_key_clear()
        d.modifiers.clear()
        coll.objects.link(d)
        d.matrix_world = o.matrix_world
        copies.append(d)
    if not copies:
        return None
    if len(copies) > 1:
        with bpy.context.temp_override(active_object=copies[0], object=copies[0], selected_objects=copies,
                                       selected_editable_objects=copies):
            bpy.ops.object.join()
    return copies[0]


def _drop(obj):
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def replace_part_mesh(part, src, *, weights: bool = True, materials: str = "KEEP", fit=None, weight_donor=None) -> Replaced:
    """Put the mesh of ``src`` in the place of the mesh of the imported part object ``part`` (see the module). ``src`` is read
    with its modifiers applied and kept where it stands: its place in the world is the mesh's place in the model (``fit``:
    ``(parts, mode, align)`` first fits it to the box of those parts, :func:`fit_matrix`). ``weights``: copy a skinned part's
    weights over from ``weight_donor`` (an object holding the weights; default the part's own old mesh). ``materials``:
    "KEEP" or "ADD" (``MATERIAL_ITEMS``). The object ``src`` is left in place for the caller to delete."""
    if part.mode != "OBJECT":
        raise ValueError(f"leave {part.mode.title()} Mode of '{part.name}' first")
    out = Replaced()
    old = part.data
    out.damage_states = max(0, len(old.shape_keys.key_blocks) - 1) if old.shape_keys else 0
    rigid = rigid_group(part)
    had_weights = has_weights(part)
    arm = _scene.model_root(part)
    bones = {b.name.lower(): b.name for b in arm.data.bones} if arm is not None and arm.type == "ARMATURE" else {}

    # 1. the mesh to use: src with its modifiers applied, its weights by group NAME (the mesh itself carries none)
    dg = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(src, preserve_all_data_layers=True, depsgraph=dg)
    src_groups = {g.index: g.name for g in src.vertex_groups}
    own = {}
    bm = bmesh.new()
    bm.from_mesh(new)
    layer = bm.verts.layers.deform.active
    if layer is not None:
        for v in bm.verts:
            infl = [(src_groups[gi], w) for gi, w in v[layer].items() if gi in src_groups and w > 0.0
                    and src_groups[gi].lower() in bones]
            if infl:
                own[v.index] = infl
        bm.verts.layers.deform.remove(layer)
    bm.to_mesh(new)
    bm.free()

    # 2. where it goes: src's place in the world, through the model root, into the part's own coordinates
    nv = len(new.vertices)
    root = _scene.model_root(part)
    W = _numbers(src.matrix_world)
    if fit is not None:
        parts, mode, align = fit
        lo, hi = model_box(parts, root)
        co = _scene._foreach(new.vertices, "co", np.float64, 3)
        Rinv = _numbers(root.matrix_world.inverted_safe())
        in_root = (co @ (Rinv @ W)[:3, :3].T) + (Rinv @ W)[:3, 3]
        F = fit_matrix(lo, hi, in_root.min(0), in_root.max(0), mode, align) if nv else Matrix.Identity(4)
        out.scale = float(F.to_scale()[0])
        W = _numbers(root.matrix_world) @ _numbers(F) @ Rinv @ W
    M = _numbers(part.matrix_world.inverted_safe()) @ W
    new.transform(Matrix([list(r) for r in M]))
    if np.linalg.det(M[:3, :3]) < 0:                     # a mirror turns the faces inside out
        bm = bmesh.new()
        bm.from_mesh(new)
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
        bm.to_mesh(new)
        bm.free()
    new.update()

    # 3. materials: the part's slots (the importer gives every part the model's materials), the faces mapped onto them
    slots_old = list(old.materials)
    slots_new = list(new.materials)
    final = list(slots_old)
    if materials == "ADD":
        final += [m for m in slots_new if m is not None and m not in slots_old]
    mapping = []
    for m in slots_new or [None]:
        if m is not None and m in final:
            mapping.append(final.index(m))
        else:
            mapping.append(0)
    idx = _scene._foreach(new.polygons, "material_index", np.int64)
    new.materials.clear()                                 # (clearing the slots resets the faces' slot numbers: set them after)
    for m in final:
        new.materials.append(m)
    if len(idx):
        mapped = np.array(mapping, dtype=np.int64)[np.clip(idx, 0, len(mapping) - 1)]
        if materials == "KEEP":
            out.unmatched = int(sum(1 for i in idx.tolist() if i < len(slots_new) and slots_new[i] is not None
                                    and slots_new[i] not in slots_old))
        new.polygons.foreach_set("material_index", mapped.astype(np.int32))
    if not new.uv_layers:
        new.uv_layers.new(name="UVMap")
        out.notes.append("the mesh has no UV map; an empty one was added (every UV is 0)")

    # 4. the swap: the object keeps its stamps, name, armature modifier and vertex groups. (A mesh holds the NAMES of its vertex
    # groups, so the new one gets the part's, made through a stand-in object; the weights are copied there, before the swap.)
    name = old.name
    old.name = name + ".replaced"
    new.name = name
    group_names = [g.name for g in part.vertex_groups]
    stand = bpy.data.objects.new(name + ".new", new)
    (part.users_collection[0] if part.users_collection else bpy.context.scene.collection).objects.link(stand)
    stand.matrix_world = part.matrix_world
    for gname in group_names:
        stand.vertex_groups.new(name=gname)
    donor = None
    if weights and not own and rigid is None and had_weights:
        donor = weight_donor if weight_donor is not None else _donor_of(part)
        try:
            _transfer_weights(stand, donor)
            out.weights = "transferred"
        except Exception as e:                            # noqa: BLE001 -- reported; the part is then written without weights
            out.notes.append(f"the weights could not be copied ({e}); the vertices follow the root bone")
    bpy.data.objects.remove(stand, do_unlink=True)
    if donor is not None and weight_donor is None:
        bpy.data.objects.remove(donor, do_unlink=True)
    part.data = new
    for key in (_scene.KEY_NORMALS_ERROR,):
        if key in part:
            del part[key]
    out.vertices, out.faces = nv, len(new.polygons)

    # 5. the bone binding
    if own:
        for vi, infl in own.items():
            for gname, w in infl:
                g = part.vertex_groups.get(bones[gname.lower()]) or part.vertex_groups.new(name=bones[gname.lower()])
                g.add([vi], float(w), "ADD")
        out.weights = "own"
    elif rigid is not None:
        part.vertex_groups[rigid].add(list(range(nv)), 1.0, "REPLACE")
        out.weights = "rigid"
    elif had_weights and not weights:
        out.notes.append("the part's weights were not copied: its vertices follow the root bone")
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return out


# ------------------------------------------------------------------------------------------------------------------ operators
class BZ_OT_scene_replace_part(bpy.types.Operator):
    """Swap the active part's mesh for the selected mesh. The part keeps its name, bone and materials"""
    bl_idname = "bz.scene_replace_part"
    bl_label = "Replace Part With Selected Mesh"
    bl_options = {"REGISTER", "UNDO"}

    transfer_weights: BoolProperty(
        name="Copy the part's bone weights", default=True,
        description="Copy a skinned part's weights onto the new mesh. A rigid part's mesh follows the same bone. "
                    "Weights the mesh already has are kept")
    materials: EnumProperty(name="Materials", items=MATERIAL_ITEMS, default="KEEP")
    keep_mesh: BoolProperty(name="Keep the selected mesh", default=False,
                            description="Leave the mesh object in the scene (by default it is consumed: its geometry is now the part)")

    @classmethod
    def poll(cls, context):
        part, src = scene_part_of(context)
        return part is not None and len(src) >= 1 and context.mode == "OBJECT"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        part, src = scene_part_of(context)
        if part is None or not src:
            self.report({"WARNING"}, "Select the new mesh, then the part to replace (last)")
            return {"CANCELLED"}
        if len(src) > 1:
            self.report({"WARNING"}, "Select ONE mesh to put in the part (join several with Ctrl+J first)")
            return {"CANCELLED"}
        try:
            rep = replace_part_mesh(part, src[0], weights=self.transfer_weights, materials=self.materials)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        if not self.keep_mesh:
            _drop(src[0])
        bpy.ops.object.select_all(action="DESELECT")
        part.select_set(True)
        context.view_layer.objects.active = part
        rig = _scene.model_root(part)
        words = {"rigid": "it follows its bone", "own": "it keeps its own bone weights", "transferred": "bone weights copied",
                 "none": "it has no bone weights" if rig is not None and rig.type == "ARMATURE" else "it does not move"}[rep.weights]
        msg = f"'{part.name}' now holds {rep.vertices:,} vertices, {rep.faces:,} faces; {words}."
        if rep.damage_states:
            msg += (f" Its {rep.damage_states} damage states stay with the old mesh: the export removes them, or carries them "
                    "over by surface position (File > Export option).")
        if rep.unmatched:
            msg += (f" {rep.unmatched} face(s) had a material the part does not have; they took the part's first material "
                    "(choose 'The mesh's own materials' to keep theirs).")
        for n in rep.notes:
            msg += " " + n
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BZ_OT_scene_use_mesh_as_model(bpy.types.Operator):
    """Use the selected mesh as the whole model. It replaces the largest part and the other parts are removed on export"""
    bl_idname = "bz.scene_use_mesh_as_model"
    bl_label = "Use This Mesh as the Model"
    bl_options = {"REGISTER", "UNDO"}

    fit: EnumProperty(name="Size", items=FIT_ITEMS, default="INSIDE")
    align: BoolProperty(name="Put it where the model stood", default=True,
                        description="Centre the mesh over the model and rest it on the model's floor")
    transfer_weights: BoolProperty(name="Copy the model's bone weights", default=True,
                                   description="Copy the weights of all parts onto the mesh (skinned models). A rigid model keeps the replaced part's bone")
    materials: EnumProperty(name="Materials", items=MATERIAL_ITEMS, default="KEEP")
    keep_mesh: BoolProperty(name="Keep the selected mesh", default=False)

    @classmethod
    def poll(cls, context):
        part, src = scene_part_of(context)
        return part is not None and len(src) >= 1 and context.mode == "OBJECT"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        part, src = scene_part_of(context)
        if part is None or not src:
            self.report({"WARNING"}, "Select the new mesh, then any part of the model (last)")
            return {"CANCELLED"}
        if len(src) > 1:
            self.report({"WARNING"}, "Select ONE mesh to use as the model (join several with Ctrl+J first)")
            return {"CANCELLED"}
        try:
            parts = _scene.part_objects(part)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        main = max(parts, key=lambda o: len(o.data.polygons))
        donor = None
        try:
            if self.transfer_weights and rigid_group(main) is None and any(has_weights(o) for o in parts):
                donor = joined_donor(parts)
            rep = replace_part_mesh(main, src[0], weights=self.transfer_weights, materials=self.materials,
                                    fit=(parts, self.fit, self.align), weight_donor=donor)
        except (ValueError, RuntimeError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            if donor is not None:
                _drop(donor)
        others = [o for o in parts if o is not main]
        gone = sorted({int(o[_scene.KEY_SCENE_PART]) for o in others} | {int(x) for x in main.get(_scene.KEY_REMOVE_PARTS, [])})
        for o in others:
            _drop(o)
        main[_scene.KEY_REMOVE_PARTS] = gone
        main[_scene.KEY_SHOW_ALWAYS] = True              # the clips that hid this stage mesh must show it (the export does that)
        if not self.keep_mesh:
            _drop(src[0])
        bpy.ops.object.select_all(action="DESELECT")
        main.select_set(True)
        context.view_layer.objects.active = main
        msg = (f"'{main.name}' is now the model: {rep.vertices:,} vertices, scaled by {rep.scale:.3g}; the other {len(others)} "
               f"part(s) are removed by the next export.")
        if rep.weights == "none" and len(parts) > 1 and _scene.model_root(main).type == "ARMATURE":
            msg += " It has no bone weights."
        self.report({"INFO"}, msg)
        return {"FINISHED"}


CLASSES = (BZ_OT_scene_replace_part, BZ_OT_scene_use_mesh_as_model)
