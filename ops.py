"""Operators: import a creature, export it losslessly, bake the surface to a mesh."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

from . import boner
from . import core
from . import build
from . import draw
from . import gnsurface
from . import material
from . import rig
from . import store
from . import enumitems


class BZ_OT_import(bpy.types.Operator, ImportHelper):
    """Import a creature from a .sgm file or its .ebp blueprint"""
    bl_idname = "bz.import_sgm"
    bl_label = "Import Model"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm;*.ebp", options={"HIDDEN"})
    clips_as_actions: bpy.props.BoolProperty(
        name="Clips as Actions", default=True,
        description="Bake every animation clip into a named Action on the rig")

    def execute(self, context):
        try:
            src = core.resolve_source(self.filepath)   # .ebp -> the .sgm it points at / embeds
            model = core.read_source(src)
        except Exception as e:
            self.report({"ERROR"}, f"Load failed: {e}")
            return {"CANCELLED"}
        if core.is_scene_model(model):                  # structure / character / prop
            return self._import_scene(context, src)
        try:
            data = core.creature_arrays_from_model(model, src)   # the model parsed above, not a re-read
        except Exception as e:
            self.report({"ERROR"}, f"Load failed: {e}")
            return {"CANCELLED"}
        obj = build.build_object(context, data, model=data.model, clips_as_actions=self.clips_as_actions)
        if src != self.filepath:
            obj["bz_ebp"] = self.filepath                # the blueprint we came from (informational)
        draw.register()
        draw.invalidate(obj.name)
        msg = f"Imported {data.name} from {os.path.basename(src)}: {data.n_verts} CPs, {len(data.patches)} patches"
        if obj.get("bz_rig"):
            msg += f", rig {len(store.rig(obj).data.bones)} bones"
            if self.clips_as_actions:
                msg += f", {len(obj.get('bz_anims', []))} clips as Actions"
        elif obj.get("bz_rig_error"):
            self.report({"WARNING"}, f"Rig skipped: {obj['bz_rig_error']}")
        for e in obj.get(store.KEY_BUILD_ERRORS, []):
            self.report({"WARNING"}, f"Import could not set up {e}")
        if obj.get("bz_actions_error"):
            self.report({"WARNING"}, f"Clips not baked to Actions: {obj['bz_actions_error']}")
        self.report({"INFO"}, msg)
        return {"FINISHED"}

    def _import_scene(self, context, src):
        from . import scene as bzscene
        from .newproject import game_dir
        try:
            data = core.load_scene(src, data_root=game_dir(context)[0])    # textures: the Game folder setting, IC_INSTALL, Steam
            objs = bzscene.build_scene(context, data, source_path=src, clips_as_actions=self.clips_as_actions, markers=True)
        except Exception as e:
            self.report({"ERROR"}, f"Scene import failed: {e}")
            return {"CANCELLED"}
        if not objs:
            self.report({"ERROR"}, "Scene has no geometry")
            return {"CANCELLED"}
        if src != self.filepath:
            for o in objs:
                o["bz_ebp"] = self.filepath
        nverts = sum(len(o.data.vertices) for o in objs)
        msg = (f"Imported scene object {data.name}: {len(objs)} part(s), {nverts} vertices, "
               f"{len(data.materials)} materials, {len(data.textures)} textures")
        if data.bone_names:
            msg += f", rig {len(data.bone_names)} bones, {len(data.animations)} clips"
        if data.markers:
            msg += f", {len(data.markers)} markers"
        if data.missing_textures:
            self.report({"WARNING"}, f"{len(data.missing_textures)} texture(s) not found (set the Game folder in the "
                                     "add-on's preferences, or IC_INSTALL): "
                                     f"{', '.join(os.path.basename(t) for t in data.missing_textures[:3])}")
        n_bad, first = bzscene.normals_errors(objs)
        if n_bad:
            self.report({"WARNING"}, f"{n_bad} part(s) show Blender's own smooth normals: the file's stored normals could "
                                     f"not be set ({first})")
        rig = bpy.data.objects.get(objs[0].get("bz_rig", "")) if objs[0].get("bz_rig") else None
        if rig is not None and rig.get("bz_actions_error"):
            self.report({"WARNING"}, f"Clips not baked to Actions: {rig['bz_actions_error']}")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _active_native(context):
    obj = context.active_object
    return obj if store.is_native(obj) else None


# Skeleton Template default: the repo checkout's local test creature when there is one; otherwise empty, which means
# the game's wolf (see skeleton_template). $BZ_TEMPLATE overrides it.
_LOCAL_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Assets", "stock", "snail.sgm")
_DEFAULT_TEMPLATE = os.environ.get("BZ_TEMPLATE", _LOCAL_TEMPLATE if os.path.exists(_LOCAL_TEMPLATE) else "")


def skeleton_template(context, path: str) -> str:
    """The template file to use: ``path``, or with none the game's wolf, taken out of the game's files the first time it
    is needed. ``FileNotFoundError`` in plain words otherwise."""
    if path.strip():
        full = bpy.path.abspath(path)
        if os.path.exists(full):
            return full
        raise FileNotFoundError(f"Skeleton template not found: {path}")
    from . import stockextract
    from .sceneobj import prefs
    # its own folder: one creature in the stock folder would make Combine Preview take it for the whole stock
    folder = bpy.utils.user_resource("DATAFILES", path=os.path.join("pyic", "skeleton_template"), create=True)
    wolf = os.path.join(folder, "wolf.sgm")
    if not os.path.exists(wolf):
        game = prefs.resolve_game_dir(context)[0]
        if not game:
            raise FileNotFoundError("Game not found: set the Game folder in the add-on's preferences, or pick a "
                                    "Skeleton Template")
        stockextract.extract("Engine.sga", "art/ebps/stock/wolf.", folder, limit=True, exts=stockextract.STOCK_EXTS,
                             root=game)
        if not os.path.exists(wolf):
            raise FileNotFoundError("The game's files have no wolf: pick a Skeleton Template")
    return wolf


class BZ_OT_convert_mesh(bpy.types.Operator):
    """Turn the active mesh into a creature using a template's skeleton and materials"""
    bl_idname = "bz.convert_mesh"
    bl_label = "Convert Mesh to Creature"
    bl_options = {"REGISTER", "UNDO"}

    template_path: StringProperty(name="Skeleton Template (.sgm)", subtype="FILE_PATH",
                                  default=_DEFAULT_TEMPLATE,
                                  description="Creature whose skeleton and materials the result borrows. Empty: the "
                                              "game's wolf")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and not store.is_native(obj)

    def execute(self, context):
        import bmesh
        obj = context.active_object
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.triangulate(bm, faces=bm.faces)                       # normalize, then pair to quads
        bmesh.ops.join_triangles(bm, faces=bm.faces, angle_face_threshold=3.14159,
                                 angle_shape_threshold=3.14159, cmp_seam=False, cmp_sharp=False,
                                 cmp_uvs=False, cmp_vcols=False, cmp_materials=False)
        bm.verts.ensure_lookup_table()
        mw = obj.matrix_world
        coords = [tuple(mw @ v.co) for v in bm.verts]
        quads = [[v.index for v in f.verts] for f in bm.faces if len(f.verts) == 4]
        nonquad = sum(1 for f in bm.faces if len(f.verts) != 4)
        bm.free()
        if not quads:
            self.report({"ERROR"}, "No quad faces after tris-to-quads; need a quad-dominant mesh")
            return {"CANCELLED"}
        try:
            data, N, F = core.mesh_to_creature(skeleton_template(context, self.template_path), coords, quads)
        except Exception as e:
            self.report({"ERROR"}, f"Conversion failed: {e}")
            return {"CANCELLED"}
        from . import startops
        try:                                        # kept with the project, never in the temp folder (work must survive a reboot)
            tmp = startops.project_path(obj.name, "_creature")
            with open(tmp, "wb") as f:
                f.write(data)
        except OSError as e:
            self.report({"ERROR"}, f"Cannot write the converted creature: {e}")
            return {"CANCELLED"}
        bpy.ops.bz.import_sgm(filepath=tmp)
        msg = f"Converted to creature: {F} patches, {N} control points (re-rig + re-limb next); its file is {tmp}"
        if nonquad:
            self.report({"WARNING"}, f"{nonquad} non-quad face(s) dropped -- mesh may have holes")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _img_topdown_rgba(img):
    """A Blender image's pixels as top-down RGBA8 bytes (Blender stores bottom-up float)."""
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    arr = (np.clip(buf.reshape(h, w, 4), 0, 1) * 255.0).round().astype(np.uint8)[::-1]
    return arr.tobytes(), w, h


class ExportGatherError(RuntimeError):
    """A readable reason the object's state can't be turned into an export (reported as is)."""


def _painted_textures(obj):
    """{source-texture index: (rgba, w, h)} for textures the user ACTUALLY painted -- detected by
    comparing each image's pixel CRC to the import-time snapshot, so untouched skins are never
    re-encoded (byte-identical), independent of Blender image-storage round-trip quirks. An image that can't be
    found (deleted, or renamed and unstamped) refuses the export: its paint would be dropped."""
    out = {}
    for i, name in enumerate(store.texture_images(obj)):
        if not name:
            continue
        img, err = store.texture_image(obj, i)
        if err:
            raise ExportGatherError(err)
        if img.size[0] == 0 or img.size[1] == 0:
            raise ExportGatherError(f"texture {i}'s image '{img.name}' has no pixels (its data is missing): its paint "
                                    "can't be read")
        snap = img.get("bz_hash")
        if snap is not None and material.pixel_crc(img) == int(snap):
            continue                                          # unpainted -> keep source bytes
        out[i] = _img_topdown_rgba(img)
    return out


def _is_identity(mat) -> bool:
    return np.allclose(np.array(mat, dtype=np.float64), np.eye(4), atol=1e-9)


def object_state_error(obj):
    """Blender-side state the export can't carry, so it refuses instead of silently dropping what the viewport
    shows: an object transform (mesh or rig), shape keys, or modifiers other than the creature's own Armature."""
    arm = store.rig(obj)
    if not _is_identity(obj.matrix_world):
        return (f"'{obj.name}' has an object transform (moved, rotated or scaled in Object Mode): the .sgm stores the "
                "control points only, so it would be dropped. Apply it to the control points or clear it (Alt+G/R/S)")
    if arm is not None and not _is_identity(arm.matrix_world):
        return (f"the rig '{arm.name}' has an object transform: the .sgm skeleton is at the origin, so it would be "
                "dropped. Clear it (Alt+G/R/S)")
    if obj.data.shape_keys is not None:
        return (f"'{obj.name}' has shape keys: Export writes the base mesh only, so key shapes would be dropped. Apply "
                "the mix as the basis or delete the shape keys")
    from . import gnsurface              # D9: the evaluated-surface modifier is ours and changes nothing exported
    extra = [m.name for m in obj.modifiers if not (m.type == "ARMATURE" and m.object is not None and m.object == arm)
             and not (m.type == "NODES" and m.name == gnsurface.MODIFIER)]
    if extra:
        return (f"'{obj.name}' has modifier(s) {', '.join(extra)}: Export writes the control points, not a modifier's "
                "result. Apply or remove them")
    for m in obj.modifiers:
        if m.type == "ARMATURE" and m.object is not None and m.object == arm and (not m.use_vertex_groups
                                                                                 or m.use_bone_envelopes):
            return (f"the Armature modifier '{m.name}' deforms by "
                    f"{'bone envelopes' if m.use_bone_envelopes else 'no vertex groups'}, but Export uses vertex groups: "
                    "turn Vertex Groups on and Bone Envelopes off")
    return None


def _group_owners(obj, arm, bone_names, stored=None):
    """``core.skin_owners`` for the creature's mesh: the rigid owner of each point from the Armature modifier's vertex
    groups (groups named after a deforming bone of the rig), against the stored owner table (``stored``: a stand-in)."""
    weights = store.skin_group_weights(obj, bone_names)
    return core.skin_owners(weights, store.vert_bone(obj) if stored is None else stored, bone_names)


def _with_stale_groups(obj, arm, bone_names, stored, why, keys) -> str:
    """Export's skin refusal ``why`` (:func:`_group_owners`) led by :func:`stale_group_notes` when points skinned to
    added bones sit in a deleted bone's vertex group; the generic sentence stays only for points those notes don't
    explain (or blended weights)."""
    try:
        _o, _a, unskinned, blended = core.skin_group_state(store.skin_group_weights(obj, bone_names), stored)
    except ValueError:
        return why
    notes, explained = stale_group_notes(obj, stored, unskinned, keys)
    if not notes:
        return why
    rest = [int(i) for i in unskinned if int(i) not in set(explained)]
    if not rest and not blended:
        return "; ".join(notes)
    return "; ".join(notes) + "; and " + why


def _group_point_count(obj, group_name) -> int:
    """How many points hold a non-zero weight in the vertex group ``group_name`` (0 without the group)."""
    vg = obj.vertex_groups.get(str(group_name))
    if vg is None:
        return 0
    gi = vg.index
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        dl = bm.verts.layers.deform.active
        return 0 if dl is None else sum(1 for v in bm.verts if v[dl].get(gi, 0.0) > 0.0)
    return sum(1 for v in obj.data.vertices for g in v.groups if g.group == gi and g.weight > 0.0)


def _mesh_verts(obj) -> np.ndarray:
    me = obj.data
    arr = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", arr)
    return arr.reshape(-1, 3)


def _cage_of(obj):
    """The creature's UV cage object (found by its stamp when renamed), or None."""
    name = obj.get("bz_uv_cage")
    if not name:
        return None
    me = store.uid(obj)
    cage = bpy.data.objects.get(str(name))
    if cage is not None and cage.type == "MESH" and cage.get("bz_uv_for_uid", me) == me:
        return cage
    if me:
        return next((o for o in bpy.data.objects if o.type == "MESH" and o.get("bz_uv_for_uid") == me), None)
    return None


def uv_cage_error(obj):
    """Why the creature's UV cage can't be read (None: no cage, or it still matches the patch table). One rule in one
    place: the Export refusal below and the action-time check (``guards.issues``) both say this."""
    cage = _cage_of(obj)
    if cage is None:
        return None
    npatch = int(obj.get(store.KEY_NPATCH, 0))
    if len(cage.data.polygons) != npatch:
        return (f"the UV cage '{cage.name}' has {len(cage.data.polygons)} faces for {npatch} patches "
                "(it was built before the patch table changed): press Edit UVs to rebuild it, or delete the cage")
    if _read_cage_uvs(cage) is None:
        return f"the UV cage '{cage.name}' has no '{_UV_LAYER}' UV layer: its UVs can't be read"
    return None


def _cage_uvs(obj):
    """(Np, 8) UVs of the creature's UV cage, None when there is no cage. A cage that no longer matches the patch
    table (built before Extrude Patch) refuses: its edits can't be mapped onto the patches."""
    cage = _cage_of(obj)
    if cage is None:
        return None
    why = uv_cage_error(obj)
    if why:
        raise ExportGatherError(why)
    return _read_cage_uvs(cage)


def missing_added_owner_error(obj, owners, n_src, added):
    """Why the owner table can't be exported against the rig's added bones, or None: control points skinned to a bone of
    the new-bone table that is not on the rig (its Add Bone undone in the rig's own Edit Mode, or the bone deleted)."""
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    keys = {int(ab.key) for ab in added}
    bad = (own >= n_src) & ~np.isin(own, sorted(keys) if keys else [-2])
    if not bad.any():
        return None
    names = store.new_bone_table_names(obj)
    who = sorted({int(k) for k in own[bad]})

    def name_of(k):
        made = names[k - n_src] if 0 <= k - n_src < len(names) and names[k - n_src] else None
        last = _last_group_name(obj, np.nonzero(own == k)[0])
        if last and made and last != made:
            return f"'{last}' (made as '{made}')"
        return repr(last or made) if (last or made) else f"key {k}"
    label = ", ".join(name_of(k) for k in who[:3])
    cnt = int(bad.sum())
    return (f"{cnt} control point{'s are' if cnt != 1 else ' is'} skinned to added bone {label}, which is not on the rig "
            "any more (orange in By Bone): Redo, or assign another bone (Boner > Bones > Select Unassigned)")


def _last_group_name(obj, points):
    """The name a bone that is not on the rig any more had last, read from the vertex groups holding ``points`` (Blender
    renames a bone's group with the bone and keeps the group when the bone is deleted): the one group name among them
    that is no bone of the rig, else None."""
    arm = store.rig(obj)
    if arm is None or not len(points):
        return None
    group = {vg.index: vg.name for vg in obj.vertex_groups if arm.data.bones.get(vg.name) is None}
    found = set()
    bm = store._edit_bmesh(obj)                          # the live points in Edit Mode (a skin edit's refusal)
    if bm is not None:
        bm.verts.ensure_lookup_table()
        dl = bm.verts.layers.deform.active
        for i in points if dl is not None else ():
            if 0 <= int(i) < len(bm.verts):
                found |= {group[g] for g, w in bm.verts[int(i)][dl].items() if g in group and w > 0}
        return next(iter(found)) if len(found) == 1 else None
    verts = obj.data.vertices
    for i in points:
        if 0 <= int(i) < len(verts):
            found |= {group[g.group] for g in verts[int(i)].groups if g.group in group and g.weight > 0}
    return next(iter(found)) if len(found) == 1 else None


def stale_group_notes(obj, stored, unskinned, keys) -> tuple:
    """``(sentences, points they explain)`` for control points skinned to an added bone that no bone vertex group
    deforms because they sit in the group of a bone that is gone: a Duplicate / Symmetrize copy of a skinned added bone
    takes its identity (its owner key) when the original is deleted, but not its vertex group, and Blender deforms points
    by their group's name. ``stored``: the owner table; ``unskinned``: the points no bone group deforms
    (``core.skin_group_state``); ``keys``: {rig bone name: owner key} of the added bones."""
    arm = store.rig(obj)
    un = np.asarray(sorted(int(i) for i in unskinned), dtype=np.int64)
    if arm is None or not len(un) or not keys:
        return [], []
    own = np.asarray(stored, dtype=np.int64).reshape(-1)
    un = un[un < len(own)]
    notes, explained = [], []
    for name, key in sorted(keys.items(), key=lambda kv: kv[1]):
        pts = un[own[un] == int(key)]
        group = _last_group_name(obj, pts) if len(pts) else None
        if not group or group == name:
            continue
        bone = arm.data.bones.get(name)
        stamp = bone.get(rig.KEY_BONE_NEW_NAME) if bone is not None else None
        cnt = len(pts)
        head = f"{cnt} control point{'s' if cnt != 1 else ''} skinned to added bone '{name}' {'are' if cnt != 1 else 'is'}"
        if stamp is not None and str(stamp) == group:
            notes.append(f"{head} still in vertex group '{group}', but no bone has that name, so they stay at rest: rename "
                         f"'{name}' back to '{group}', or select them and Assign Selected to Bone")
        else:
            notes.append(f"{head} in vertex group '{group}', which names no bone of the rig, so Blender leaves them at "
                         f"rest: rename the group or the bone so they match, or select them and Assign Selected to Bone")
        explained += [int(i) for i in pts]
    return notes, explained


@dataclass
class ExportJob:
    """What Export / Validate / the combine preview build a creature from (gathered from Blender, guards passed)."""
    obj: object
    state: object                 # core.EditState
    arm: object                   # the rig, or None
    source: object                # the parsed source (a private copy)
    plan: object = None           # actions.ClipPlan, or None (animations not gathered)
    warnings: list = field(default_factory=list)


def gather_export(obj, context, animations=True) -> ExportJob:
    """Everything an export needs from a native creature object, after the guards. Shared by Export, Validate and
    the combine preview, so they all see the same edited creature. Edit-mode changes are flushed to the mesh FIRST,
    so the guards and the export read one mesh state. Raises :class:`ExportGatherError` with a readable message
    when the state can't be exported without losing or corrupting something.

    ``animations=False`` skips the Actions (their encoding is the slow part; Validate needs them, a combine preview
    does not)."""
    from . import actions as bzactions
    from . import structure
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    err = store.sync(obj) or store.source_error(obj) or store.pool_error(obj) or store.ring_store_error(obj) \
        or store.limb_store_error(obj) or store.rig_error(obj) or object_state_error(obj)
    if err:
        raise ExportGatherError(err)
    warnings = []
    from . import hardedges                              # round 2 (r2shape): Mark Sharp edges and material slots -> the tables
    try:
        warnings += hardedges.commit(obj)
    except ValueError as e:
        raise ExportGatherError(str(e)) from e
    src = store.source_path(obj)
    if obj.get(store.KEY_SRC_SHA1) is None:
        warnings.append(f"the source {os.path.basename(src)} was not stamped at import (an older object): the export "
                        "can't verify it is still the file you imported")
    for e in obj.get(store.KEY_BUILD_ERRORS, []):
        warnings.append(f"import could not set up: {e}")
    from . import sculptops
    warnings += sculptops.in_progress_warnings(obj)          # a sculpt not refit yet is not in this export
    source = core.read_source(src)
    arm = store.rig(obj)
    if arm is not None and obj.get(store.KEY_RIG) != arm.name:
        obj[store.KEY_RIG] = arm.name                    # follow a renamed rig
    if arm is not None:
        from . import boneops
        err = rig.skeleton_error(arm, source, rules=boneops.topology_rules(obj, source))
        if err:
            raise ExportGatherError(err)
    added, topo = [], None
    if arm is not None:
        added, _refused = rig.added_bones(arm, store.new_bone_uids(obj), source)   # the guard reported refusals
        topo, tbad = rig.bone_topology(arm, model=source, added=added)          # deleted / re-parented source bones
        if tbad:
            raise ExportGatherError("; ".join(tbad))
    try:
        bone_rest = rig.bone_transforms_from_armature(arm, source, topo) if arm is not None else None
        rig_names = rig.current_bone_names(arm, source, deleted="" if topo else None) if arm is not None else None
    except KeyError as e:
        raise ExportGatherError(f"the rig does not match the source skeleton: {e}") from e
    renames = rig.bone_renames(arm, source) if arm is not None else {}
    new_bones = rig.new_stub_bones(arm, source) if arm is not None else []
    for nb in new_bones:
        k = _group_point_count(obj, nb.name)
        if k:
            raise ExportGatherError(f"{k} control point(s) are in the vertex group of stub bone '{nb.name}', which owns no "
                                    "points: move them to another bone (Boner > Bones)")
    n_src = len(source.bones)
    vert_owner = store.vert_bone(obj) if store.has_vert_bone(obj) else None
    if vert_owner is not None:
        why = missing_added_owner_error(obj, vert_owner, n_src, added)
        if why:
            raise ExportGatherError(why)
    if arm is not None and vert_owner is not None:
        owner_names = list(rig_names)
        if added:                                        # the added bones' vertex groups, by owner key
            owner_names += [""] * (max(ab.key for ab in added) + 1 - n_src)
            for ab in added:
                owner_names[ab.key] = ab.name
        # a deleted bone's points sit in a vertex group no bone deforms (Blender keeps the group): they are not a weight
        # edit but the deleted bone's skin, which the builder moves to its parent (the summary warns)
        gone = np.isin(vert_owner, [int(d) for d in topo.deleted]) if topo is not None and topo.deleted else None
        owners, adopted, why = _group_owners(obj, arm, owner_names,
                                             np.where(gone, -1, vert_owner) if gone is not None else None)
        if why:
            raise ExportGatherError(_with_stale_groups(obj, arm, owner_names, vert_owner, why,
                                                       {ab.name: int(ab.key) for ab in added}))
        if adopted:
            vert_owner = np.where(gone, vert_owner, owners) if gone is not None else owners
            warnings.append(f"{len(adopted)} control point(s) re-skinned through the vertex groups (Ctrl+G / weight "
                            "paint): written with their new owner bone")
    if vert_owner is not None:
        bad = core.unowned_patch_points(vert_owner, store.patches(obj))
        if len(bad):
            raise ExportGatherError(core.unowned_message(len(bad), bad))
    cage_uv = _cage_uvs(obj)

    def stored(key, fn):
        return fn(obj) if obj.get(key) is not None else None
    if added:                                            # a new limb with no patches makes its point-less bones stub
        stubs = structure.export_stub_bones(source, store.limb_rows(obj), stored(store.KEY_PATCH_LIMB, store.patch_limb),
                                            vert_owner)   # bones: no added bone under one (as the export inserts them)
        if stubs != structure.stub_bone_indices(source):
            err = rig.skeleton_error(arm, source, src_stubs=stubs)
            if err:
                raise ExportGatherError(err)
    state = core.EditState(
        source_path=src, verts=_mesh_verts(obj), patches=store.patches(obj),
        vert_owner=vert_owner,
        patch_limb=stored(store.KEY_PATCH_LIMB, store.patch_limb), limbs=store.limb_rows(obj),
        patch_uv=cage_uv if cage_uv is not None else stored(store.KEY_PATCH_UV, store.patch_uv),
        patch_material=stored(store.KEY_PATCH_MAT, store.patch_mat),
        patch_type=stored(store.KEY_PATCH_TYPE, store.patch_type),
        connections=store.ring_edits(obj), bone_rest=bone_rest, textures=_painted_textures(obj),
        texture_names=store.texture_names(obj), slot_textures=store.slot_texture_names(obj),
        new_bones=new_bones or None, bone_renames=renames or None, added_bones=added or None,
        bone_table_names=store.new_bone_table_names(obj) or None, pool_src=store.pool_src(obj),
        limb_pins=store.limb_pins(obj) or None,
        bone_topology=topo if topo else None,
        bone_limits=boneops.rig_bone_limits(obj, arm, source, added) if arm is not None else None,
        ik_chains=store.ik_rows(obj))
    plan = None
    if animations and arm is not None:
        try:
            plan = bzactions.plan_export(arm, deleted=[source.bones[d].name for d in topo.deleted] if topo else ())
        except ValueError as e:
            raise ExportGatherError(f"Animation export refused: {e}") from e
        warnings += plan.warnings
    return ExportJob(obj=obj, state=state, arm=arm, source=source, plan=plan, warnings=warnings)


def build_export(job: ExportJob, context):
    """(model, summary) of a gathered job: the creature built by ``core.build_export``, then its clips encoded against
    that model (its bind decides track presence, its geometry the bounds) and the clip plan applied."""
    from . import actions as bzactions
    try:
        built = core.build_export(job.state)
    except ValueError as e:
        raise ExportGatherError(f"Export failed: {e}") from e
    plan = job.plan
    if plan is not None:
        clips = []
        if plan.encode:
            try:
                clips = bzactions.encode_actions(job.arm, built.model, plan.encode, context.scene.render.fps,
                                                 bone_names=sample_bone_names(job, built))
            except Exception as e:
                raise ExportGatherError(f"Animation export failed: {type(e).__name__}: {e}") from e
        core.apply_clip_plan(built.model, clips, plan.drop)
    warns = list(job.warnings)
    refreshed = core.refresh_kept_bounds(built, plan.kept if plan else (), warns)   # kept clips beside a changed body: new bounds, same keys
    summary = core.export_summary(built, kept=plan.kept if plan else (),
                                  written=[c for _, c in plan.encode] if plan else (),
                                  dropped=plan.drop if plan else (), warnings=warns, bounds_refreshed=refreshed)
    return built.model, summary


def sample_bone_names(job, built):
    """The rig bone to sample for each exported bone a clip keys (the export's order: source bones by identity, added
    bones by name; created stub bones get no track), or None when the export added no bone (the source order)."""
    n_src = len(job.source.bones)
    if built.bone_keys is None or (not built.bones_new and list(built.bone_keys) == list(range(n_src))):
        return None                                      # the source's bones in the source's order
    cur = rig.current_bone_names(job.arm, job.source, deleted="")
    name_of = {int(ab.key): ab.name for ab in (job.state.added_bones or [])}
    return [cur[k] if 0 <= k < n_src else name_of[k] for k in built.bone_keys]


def export_bytes(obj, context, animations=False) -> bytes:
    """The bytes Export would write for ``obj`` (``animations=False``: clips as in the source)."""
    from ICParse.sgm import parser as sgm_parser
    model, _summary = build_export(gather_export(obj, context, animations=animations), context)
    return sgm_parser.write(model)


def edited_model(obj, context, animations=True, info=None):
    """The creature as Export would write it, in memory (an ICParse SgmModel; nothing is written). ``info`` (a dict),
    when given, receives ``warnings``, ``summary`` (the report line) and ``anim_note``."""
    job = gather_export(obj, context, animations=animations)
    model, summary = build_export(job, context)
    if info is not None:
        info["warnings"] = list(summary.warnings)
        info["stock"] = list(summary.stock)
        info["summary"] = summary.line
        info["anim_note"] = next((p for p in summary.parts if p.startswith("clips:")), "")
    return model


def _same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file (the same inode when both exist, else the same absolute path)."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.abspath(bpy.path.abspath(a)) == os.path.abspath(bpy.path.abspath(b))


def in_place_refusal(job):
    """Why this export must not overwrite the creature's own base file, or None.

    Saving over the base is fine for everything the state describes ABSOLUTELY (moved points, the owner table, limb
    rows, rings, UVs, textures): re-exporting from the file just written gives the same bytes again. The skeleton
    edits are the exception -- they are a DIFF against the base ("add this bone", "delete that one", "this one is
    renamed"), so once the base contains them, exporting again would add the bone twice. A creature fitted, rigged
    and saved in place is the normal case (Fit Patches to Mesh writes its output as the creature's base), so the
    refusal names what is in the way instead of blocking the path outright."""
    st = job.state
    for what, v in (("the bones added on the rig", st.added_bones), ("the deleted / re-parented bones", st.bone_topology),
                    ("the renamed bones", st.bone_renames), ("the stub bones created in Blender", st.new_bones)):
        if v:
            return what
    return None


def _atomic_write(path: str, data: bytes) -> None:
    """Write via a temp file + rename, so a failed write can't truncate the target (which may be the source)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.bz-{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class BZ_OT_export(bpy.types.Operator, ExportHelper):
    """Export the creature to .sgm, rewriting only what you edited"""
    bl_idname = "bz.export_sgm"
    bl_label = "Export Model"

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm", options={"HIDDEN"})
    export_anyway: BoolProperty(name="Export Anyway", default=False, options={"SKIP_SAVE"},
                                description="Write the file even though the checks found problems that stop the game loading it")
    overwrite_stock: BoolProperty(name="Overwrite Extracted Stock File", default=False, options={"SKIP_SAVE"},
                                  description="Allow writing over one of the extracted stock creatures (the combiner's donors)")

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object)

    def invoke(self, context, event):
        from . import startops
        suggested = startops.default_export_path(_active_native(context), context)    # an extracted original is never the suggestion
        if suggested:
            self.filepath = suggested
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        from ICParse.sgm import parser as sgm_parser
        from . import startcheck, startops
        obj = _active_native(context)
        why = startcheck.stock_copy_refusal(self.filepath, startops.stock_dirs(context))
        if why and not self.overwrite_stock:
            self.report({"ERROR"}, f"Export refused: {why}. Tick 'Overwrite Extracted Stock File' to do it anyway")
            return {"CANCELLED"}
        try:
            job = gather_export(obj, context, animations=True)
            model, summary = build_export(job, context)
            out = sgm_parser.write(model)
        except ExportGatherError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Export failed: {type(e).__name__}: {e}")
            return {"CANCELLED"}
        try:
            problems = startops.export_problems(obj, model)
        except Exception as e:                                   # a check that fails is said, never skipped
            problems = [f"WARN the export checks failed to run: {type(e).__name__}: {e}"]
        errs = core.blocking(problems)
        from .sceneobj.ui_kit import plain
        src = store.source_path(obj)
        try:
            with open(src, "rb") as f:
                identical = f.read() == out
        except OSError:
            identical = False
        if errs and identical:                                   # nothing was edited: these are the source's own (a shipped Tellurian animal)
            for p in errs:
                self.report({"WARNING"}, f"unchanged from the source, which has it too: {plain(p)}")
        elif errs and not self.export_anyway:
            for p in errs:
                self.report({"ERROR"}, plain(p))
            self.report({"ERROR"}, f"Not exported: {len(errs)} problem{'s' if len(errs) != 1 else ''} would stop the game loading this "
                                   f"creature ({startops.count_text(problems)}). Fix them, or tick Export Anyway.")
            return {"CANCELLED"}
        for p in ([] if identical else errs):
            self.report({"WARNING"}, f"exported anyway: {plain(p)}")
        for w in summary.warnings:
            self.report({"WARNING"}, plain(w))
        in_place = _same_file(self.filepath, src)
        why = in_place_refusal(job) if (in_place and not identical) else None
        if why:
            self.report({"ERROR"}, f"Not exported: {os.path.basename(src)} is this creature's own base file, and "
                                   f"{why} would be applied twice. Use another file name.")
            return {"CANCELLED"}
        try:
            _atomic_write(self.filepath, out)
        except OSError as e:
            self.report({"ERROR"}, f"Export failed: cannot write {self.filepath}: {e}")
            return {"CANCELLED"}
        line = summary.line + ("; byte-identical to the source" if identical else "")
        checks = f"checks: {len(errs)} blocking, {startops.count_text(problems)}"
        if in_place and not identical:
            store.rebase_source(obj, src)              # the base is the file just written: stay in sync with it
            line += "; the creature's own base file was updated in place"
        self.report({"INFO"}, f"Wrote {len(out)} bytes to {self.filepath}: {line}; {checks}")
        obj["bz_last_export"] = line
        obj["bz_last_checks"] = checks
        return {"FINISHED"}


def _begin(op, obj) -> bool:
    """Start a journaled table edit (store.begin_edit); report and refuse when the object is out of sync."""
    try:
        store.begin_edit(obj)
    except RuntimeError as e:
        op.report({"ERROR"}, str(e))
        return False
    return True


def _guard_poll(cls, context, obj=None, edit=False):
    """Poll helper for operators that index the pool through the patch table: native (in edit mode when asked) and
    a consistent pool, with the reason as the poll message."""
    obj = obj if obj is not None else context.active_object
    if not store.is_native(obj):
        return False
    if edit and obj.mode != "EDIT":
        return False
    err = store.pool_error_cached(obj)
    if err:
        cls.poll_message_set(err)
        return False
    return True


KEY_BAKE_STAMP = "bz_bake_stamp"     # on a baked mesh: what it was made from (see :func:`bake_stamp`)
KEY_BAKE_POSED = "bz_bake_posed"


def bake_stamp(obj, density: int, posed: bool = False) -> str:
    """A fingerprint of what a baked mesh shows: the patches, UVs, materials and textures, the density and (a rest-pose bake) the control
    points. Paint Skin rebuilds a bake whose stamp is no longer the creature's, so paint lands on the layout that is exported."""
    import zlib
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    h = zlib.crc32(f"{int(density)}|{bool(posed)}".encode())
    if not posed:
        co = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
        obj.data.vertices.foreach_get("co", co)
        h = zlib.crc32(co.astype(np.float32).tobytes(), h)
    for part in (store.patches(obj), store.patch_uv(obj), store.patch_mat(obj), store.patch_type(obj)):
        h = zlib.crc32(np.ascontiguousarray(part).tobytes(), h)
    names, tex, col = store.material_slots(obj)
    h = zlib.crc32(repr((names, tex, np.round(col, 6).tolist(), store.texture_images(obj))).encode(), h)
    return str(h & 0xFFFFFFFF)


class BZ_OT_bake(bpy.types.Operator):
    """Bake the live surface to a real textured mesh (Eevee/Cycles render + material preview)"""
    bl_idname = "bz.bake_surface"
    bl_label = "Bake Textured Mesh"
    bl_options = {"REGISTER", "UNDO"}

    posed: bpy.props.BoolProperty(
        name="Posed", default=False,
        description="Bake the surface in the rig's current pose instead of the rest pose")

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context)

    def execute(self, context):
        obj = _active_native(context)
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        me = obj.data
        if self.posed:
            me = gnsurface.posed_control_points(obj, context.evaluated_depsgraph_get())
        n = len(me.vertices)
        arr = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", arr)
        density = int(getattr(context.scene, "bz_density", 4))
        patches = store.patches(obj)
        rest = None
        if self.posed:                                   # the game splits cells from the bind pose
            rest = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
            obj.data.vertices.foreach_get("co", rest); rest = rest.reshape(-1, 3)
        V, T, N = core.tessellate(arr.reshape(n, 3), patches, n=density, rest_blender=rest)
        if len(V) == 0:
            self.report({"ERROR"}, "Nothing to bake")
            return {"CANCELLED"}

        bm = bpy.data.meshes.new(obj.name + "_baked")
        bm.from_pydata(V.tolist(), [], T.tolist())
        for p in bm.polygons:
            p.use_smooth = True
        bm.update()

        textured = self._apply_appearance(obj, bm, patches, density)
        try:
            self._apply_engine_normals(obj, bm, patches, density, arr.reshape(n, 3))
        except Exception as e:
            self.report({"WARNING"}, f"The game's normals were not applied: {e}")

        baked = bpy.data.objects.new(obj.name + "_baked", bm)
        baked[KEY_BAKE_STAMP] = bake_stamp(obj, density, self.posed)
        baked[KEY_BAKE_POSED] = bool(self.posed)
        baked.matrix_world = obj.matrix_world.copy()
        context.collection.objects.link(baked)
        # nudge viewport to a shading mode that shows textures
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.type = "MATERIAL"
        self.report({"INFO"}, f"Baked {len(T)} tris at density {density}"
                              + (" (textured)" if textured else ""))
        return {"FINISHED"}

    @staticmethod
    def _apply_appearance(obj, bm, patches, density, face_patch=None, loop_sample=None) -> bool:
        """Set per-face material slots and per-loop UVs from the stored creature appearance. The mesh is a bake in
        tessellate order (faces per patch in blocks, vertex = sample) unless ``face_patch`` (patch per face) and
        ``loop_sample`` (bake sample per face corner) say otherwise (a sculpt mesh with merged seam vertices)."""
        slot_names, tex_names, colors = store.material_slots(obj)
        if len(colors) == 0:
            return False
        images = {nm: bpy.data.images.get(nm) for nm in tex_names if nm}
        table = [{"tex": tex_names[i] if i < len(tex_names) else "",
                  "color": tuple(colors[i])} for i in range(len(colors))]
        # build materials keyed by image NAME (decode stored image names, not original tex names)
        mats = material.build_blender_materials(
            [{"tex": t["tex"], "color": t["color"]} for t in table],
            {nm: img for nm, img in images.items() if img}, slot_names)
        for mt in mats:
            bm.materials.append(mt)

        pmat = store.patch_mat(obj)
        nslots = len(mats)
        tpp = max(1, 2 * (density - 1) * (density - 1))      # triangles per patch
        mat_idx = np.zeros(len(bm.polygons), dtype=np.int32)
        for fi in range(len(bm.polygons)):
            pi = int(face_patch[fi]) if face_patch is not None else fi // tpp
            mi = int(pmat[pi]) if pi < len(pmat) else 0
            mat_idx[fi] = mi if 0 <= mi < nslots else 0
        bm.polygons.foreach_set("material_index", mat_idx)

        uvarr = core.surface_uvs(store.patch_uv(obj), density)
        if loop_sample is None:
            loop_sample = np.empty(len(bm.loops), dtype=np.int64)
            bm.loops.foreach_get("vertex_index", loop_sample)
            if len(uvarr) != len(bm.vertices):
                loop_sample = None                           # not a bake in tessellate order: no UVs
        if loop_sample is not None and len(loop_sample) == len(bm.loops):
            uvl = bm.uv_layers.new(name="UVMap")
            uvl.data.foreach_set("uv", uvarr[np.asarray(loop_sample, dtype=np.int64)].astype("f4").ravel())
        bm.update()
        return True

    @staticmethod
    def _apply_engine_normals(obj, bm, patches, density, cps):
        """Shade the bake exactly like the game: ICParse's engine-verified normal rule (analytic
        normals welded along edges and per smoothing group at corners) as custom split normals."""
        N = core.engine_normals(np.asarray(cps, dtype=np.float64), patches, store.patch_type(obj), density)
        if len(N) != len(bm.vertices):
            return False
        loop_v = np.empty(len(bm.loops), dtype=np.int64)
        bm.loops.foreach_get("vertex_index", loop_v)
        bm.normals_split_custom_set([tuple(v) for v in N[loop_v].tolist()])
        return True


class BZ_OT_select_kind(bpy.types.Operator):
    """Select only on-surface corner points, or only off-surface handles (edit mode)"""
    bl_idname = "bz.select_kind"
    bl_label = "Select Control Points by Kind"
    bl_options = {"REGISTER", "UNDO"}

    kind: StringProperty(default="CORNER", options={"HIDDEN"})  # CORNER | HANDLE

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        mask = core.corner_mask(store.patches(obj), len(bm.verts))
        want_corner = self.kind == "CORNER"
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            v.select = bool(mask[v.index] == want_corner) and not v.hide     # hidden interiors stay unselected
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        n = sum(1 for v in bm.verts if v.select)
        self.report({"INFO"}, f"Selected {n} {'corner' if want_corner else 'handle'} points")
        return {"FINISHED"}


class BZ_OT_paint_skin(bpy.types.Operator):
    """Paint the creature's skin in Texture Paint; Export writes the textures back"""
    bl_idname = "bz.paint_skin"
    bl_label = "Paint Skin"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(store.texture_images(obj)) if _guard_poll(cls, context) else False

    def execute(self, context):
        obj = _active_native(context)
        baked = bpy.data.objects.get(obj.name + "_baked")
        if baked is not None:
            density = int(getattr(context.scene, "bz_density", 4))
            if baked.get(KEY_BAKE_STAMP) != bake_stamp(obj, density, bool(baked.get(KEY_BAKE_POSED))):
                old_mesh = baked.data                      # the UVs, texture or points changed since it was made: paint on a fresh one
                bpy.data.objects.remove(baked, do_unlink=True)
                if old_mesh is not None and old_mesh.users == 0:
                    bpy.data.meshes.remove(old_mesh)
                baked = None
        if baked is None:
            bpy.ops.bz.bake_surface()
            baked = bpy.data.objects.get(obj.name + "_baked")
        if baked is None:
            self.report({"ERROR"}, "Could not bake a paintable mesh")
            return {"CANCELLED"}
        for o in context.view_layer.objects:
            o.select_set(False)
        baked.select_set(True)
        context.view_layer.objects.active = baked
        over = {"active_object": baked, "selected_objects": [baked], "object": baked}
        if context.screen:
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    over.update(area=area, region=next((r for r in area.regions
                                                        if r.type == "WINDOW"), None))
                    break
        try:
            with context.temp_override(**over):
                bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
            context.scene.tool_settings.image_paint.mode = "MATERIAL"
        except Exception:
            self.report({"WARNING"}, "Switch the baked mesh to Texture Paint manually")
        self.report({"INFO"}, "Paint the baked mesh; Export writes the textures back to .sgm")
        return {"FINISHED"}


_UV_LAYER = "SGM_UV"


_CAGE_UV_SLOT = (0, 1, 3, 2)     # cage loop k (corners 0,3,15,12) -> file uv slot (corners 0,3,12,15)


def _read_cage_uvs(cage):
    """Read a UV cage's loops back to a (Np, 8) corner-UV array (un-flipping V)."""
    me = cage.data
    uvl = me.uv_layers.get(_UV_LAYER)
    if uvl is None:
        return None
    puv = np.zeros((len(me.polygons), 8), dtype=np.float64)
    for p, poly in enumerate(me.polygons):
        for k, loop in enumerate(poly.loop_indices):
            if k < 4:
                sl = _CAGE_UV_SLOT[k]
                uv = uvl.data[loop].uv
                puv[p, 2 * sl] = uv[0]
                puv[p, 2 * sl + 1] = 1.0 - uv[1]
    return puv


def _sync_uvs_from_cage(obj):
    """Pull the UV cage's edited UVs into the stored per-patch UVs (a journaled table write). Returns the error
    string when the cage can't be applied, or None."""
    try:
        puv = _cage_uvs(obj)
    except ExportGatherError as e:
        return str(e)
    if puv is None:
        return "no UV cage for this creature: press Edit UVs first"
    try:
        store.begin_edit(obj)
    except RuntimeError as e:
        return str(e)
    store.set_patch_uv(obj, puv)
    return None


class BZ_OT_edit_uvs(bpy.types.Operator):
    """Build a UV cage to edit the creature's UVs in Blender's UV Editor"""
    bl_idname = "bz.edit_uvs"
    bl_label = "Edit UVs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context) and store.has(context.active_object, store.KEY_PATCH_UV)

    def execute(self, context):
        obj = _active_native(context)
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        err = store.sync(obj) or store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        old = _cage_of(obj)
        if old is not None:                      # rebuild: apply what the old cage holds when it still fits
            if len(old.data.polygons) == int(obj.get(store.KEY_NPATCH, 0)):
                e = _sync_uvs_from_cage(obj)
                if e:
                    self.report({"ERROR"}, e)
                    return {"CANCELLED"}
            else:
                self.report({"WARNING"}, f"the old UV cage '{old.name}' had {len(old.data.polygons)} faces for "
                                         f"{int(obj.get(store.KEY_NPATCH, 0))} patches: its UV edits could not be "
                                         "applied and it was replaced")
            bpy.data.objects.remove(old, do_unlink=True)
        patches = store.patches(obj)
        puv = store.patch_uv(obj)
        co = _mesh_verts(obj)
        nv = len(co)
        corners = patches[:, [0, 3, 15, 12]]
        faces = [tuple(int(c) for c in corners[p]) for p in range(len(patches))]
        me = bpy.data.meshes.new(obj.name + "_UVcage")
        me.from_pydata([tuple(v) for v in co.reshape(nv, 3).tolist()], [], faces)
        me.update()
        uvl = me.uv_layers.new(name=_UV_LAYER)
        # cage face corners are control points 0,3,15,12; the file's uv slots are corners 0,3,12,15
        # (engine-verified), so loop k reads uv slot (0,1,3,2)[k]
        for p, poly in enumerate(me.polygons):
            for k, loop in enumerate(poly.loop_indices):
                if k < 4:
                    sl = _CAGE_UV_SLOT[k]
                    uvl.data[loop].uv = (puv[p, 2 * sl], 1.0 - puv[p, 2 * sl + 1])
        me.update()
        cage = bpy.data.objects.new(obj.name + "_UVcage", me)
        context.collection.objects.link(cage)
        cage["bz_uv_for"] = obj.name
        cage["bz_uv_for_uid"] = store.uid(obj)
        obj["bz_uv_cage"] = cage.name
        for o in context.view_layer.objects:
            o.select_set(False)
        cage.select_set(True)
        context.view_layer.objects.active = cage
        self.report({"INFO"}, "Open the UV Editor and edit the cage; then Apply UVs (or Export)")
        return {"FINISHED"}


def _cage_owner(obj):
    """The creature a UV cage belongs to (by its stamp, then its recorded name), or the object itself."""
    if obj.get("bz_uv_for_uid"):
        hit = next((o for o in bpy.data.objects if store.is_native(o) and store.uid(o) == obj["bz_uv_for_uid"]), None)
        if hit is not None:
            return hit
    if obj.get("bz_uv_for"):
        return bpy.data.objects.get(str(obj["bz_uv_for"]))
    return obj


class BZ_OT_apply_uvs(bpy.types.Operator):
    """Pull the UV cage's edited UVs back onto the creature (Export then writes them)"""
    bl_idname = "bz.apply_uvs"
    bl_label = "Apply UVs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or not (obj.get("bz_uv_for") or obj.get("bz_uv_cage")):
            return False
        main = _cage_owner(obj)
        return _guard_poll(cls, context, obj=main)

    def execute(self, context):
        obj = context.active_object
        main = _cage_owner(obj)
        if main is None or not store.is_native(main):
            self.report({"ERROR"}, "No creature linked to this UV cage")
            return {"CANCELLED"}
        err = store.sync(main) or store.pool_error(main) or _sync_uvs_from_cage(main)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        draw.invalidate(main.name)
        self.report({"INFO"}, "Applied edited UVs to the creature")
        return {"FINISHED"}


@enumitems.stable
def _limb_items(self, context):
    obj = context.active_object
    names = store.limb_names(obj) if obj else []
    pl = store.patch_limb(obj) if obj else np.array([])
    items = []
    for i, n in enumerate(names):
        cnt = int((pl == i).sum()) if len(pl) else 0
        if cnt:
            items.append((str(i), f"{n} ({cnt})", f"Select the {cnt} patches of {n}"))
    return items or [("-1", "(no limbs)", "")]


class BZ_OT_select_limb(bpy.types.Operator):
    """Select all control points belonging to a limb / body part (edit mode)"""
    bl_idname = "bz.select_limb"
    bl_label = "Select Limb"
    bl_options = {"REGISTER", "UNDO"}

    limb: bpy.props.EnumProperty(name="Limb", items=_limb_items)

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        obj = context.active_object
        idx = int(self.limb)
        if idx < 0:
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        pl = store.patch_limb(obj)
        patches = store.patches(obj)
        want = {int(v) for p in np.nonzero(pl == idx)[0] for v in patches[p]}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for v in bm.verts:
            v.select = v.index in want and not v.hide           # hidden interiors follow their corners
        from . import patchsel
        patchsel.select_rows(bm, patches, np.nonzero(pl == idx)[0])   # the corner quads too (Face select mode)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        self.report({"INFO"}, f"Selected limb '{store.limb_names(obj)[idx]}' ({len(want)} points)")
        return {"FINISHED"}


def _selected_indices(obj):
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    return [v.index for v in bm.verts if v.select]


def _selected_patches(obj):
    """Indices of the selected patches: the selected corner quads (Face select; a 4-corner vertex selection is
    flushed to its face by Blender), else every patch whose 4 corner control points are selected."""
    from . import patchsel
    patches = store.patches(obj)
    rows = patchsel.selected_rows(obj, patches)
    if rows:
        return rows
    sel = set(_selected_indices(obj))
    corners = patches[:, [0, 3, 15, 12]]
    return [p for p in range(len(patches)) if all(int(c) in sel for c in corners[p])]


_ALL_LIMB_ITEMS: list = []


@enumitems.stable
def _all_limb_items(self, context):
    """Every limb, stubs and new limbs included (Assign can give a limb its first patches); kept referenced for
    Blender's dynamic enum strings."""
    global _ALL_LIMB_ITEMS
    obj = context.active_object if context else None
    names = store.limb_names(obj) if store.is_native(obj) else []
    pl = store.patch_limb(obj) if names else np.array([])
    _ALL_LIMB_ITEMS = [(str(i), f"{n} ({int((pl == i).sum()) if len(pl) else 0})", f"Assign the selected patches to {n}")
                       for i, n in enumerate(names)] or [("-1", "(no limbs)", "")]
    return _ALL_LIMB_ITEMS


class BZ_OT_assign_limb(bpy.types.Operator):
    """Assign the selected patches (all 4 corners selected) to a limb / body part"""
    bl_idname = "bz.assign_limb"
    bl_label = "Assign Selected to Limb"
    bl_options = {"REGISTER", "UNDO"}

    limb: bpy.props.EnumProperty(name="Limb", items=_all_limb_items)

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context, edit=True) and len(store.limb_names(context.active_object)) > 0

    def execute(self, context):
        obj = context.active_object
        idx = int(self.limb)
        if idx < 0:
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        pats = _selected_patches(obj)
        if not pats:
            self.report({"WARNING"}, "Select all 4 corners of the patches to assign")
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        if idx >= len(store.limb_names(obj)):
            self.report({"ERROR"}, f"no limb {idx}: the creature has {len(store.limb_names(obj))} limbs")
            return {"CANCELLED"}
        pl = store.patch_limb(obj)
        pl[pats] = idx
        store.set_patch_limb(obj, pl)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Assigned {len(pats)} patches to '{store.limb_names(obj)[idx]}'")
        return {"FINISHED"}


@enumitems.stable
def _skin_bone_items(self, context):
    obj = context.active_object
    names = store.bone_rig_names(obj) if obj else []
    return [(str(i), n, "") for i, n in enumerate(names) if n] or [("-1", "(no rig)", "")]


_OPEN_SIDES: dict = {}          # obj name -> ((generation, patch count), open side count): poll-time cache


def _open_sides(obj) -> int:
    key = (obj.get(store.KEY_GEN), int(obj.get(store.KEY_NPATCH, 0)))
    hit = _OPEN_SIDES.get(obj.name)
    if hit is None or hit[0] != key:
        hit = (key, core.open_side_count(store.patches(obj)))
        _OPEN_SIDES[obj.name] = hit
    return hit[1]


class BZ_OT_extrude_patch(bpy.types.Operator):
    """Add a new patch along an open side (select its two corners)"""
    bl_idname = "bz.extrude_patch"
    bl_label = "Extrude Patch"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not _guard_poll(cls, context, edit=True):
            return False
        if _open_sides(context.active_object) == 0:
            cls.poll_message_set("Extrude Patch needs an open side: this creature is closed (every side is shared)")
            return False
        return True

    def execute(self, context):
        import bmesh
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        patches = store.patches(obj)
        pick = core.pick_extrude_side(patches, _selected_indices(obj))
        if isinstance(pick, str):
            self.report({"ERROR"}, f"Extrude Patch: {pick}")
            return {"CANCELLED"}
        pi, side = pick
        vb = store.vert_bone(obj)
        if store.rig(obj) is not None and len(vb) == store.point_count(obj):
            live = skin_state(self, obj)          # the owner write below rewrites the groups: adopt / refuse first
            if live is None:
                return {"CANCELLED"}
            vb = live.base
        if not _begin(self, obj):
            return {"CANCELLED"}
        g = int(obj[store.KEY_GEN])
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        verts = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
        n0 = len(verts)
        puv, pmat = store.patch_uv(obj), store.patch_mat(obj)
        ptype, plimb = store.patch_type(obj), store.patch_limb(obj)
        new_verts, new_cp = core.extrude_patch(verts, patches[pi], side=side)
        made = [bm.verts.new((float(xyz[0]), float(xyz[1]), float(xyz[2])))   # the 12 new control points (the
                for xyz in new_verts]                                          # control net is a gpu overlay)
        bm.verts.ensure_lookup_table(); bm.verts.index_update()
        if any(v.index < n0 for v in made):
            # BMesh reused a removed vertex's slot (a delete in this Edit Mode session): the new points would sit in
            # the middle of the pool and shift every index the tables hold. Nothing is written.
            for v in made:
                bm.verts.remove(v)
            bm.verts.ensure_lookup_table(); bm.verts.index_update()
            self.report({"ERROR"}, "Extrude Patch could not add points after an earlier delete. Press Tab twice, then try again.")
            return {"CANCELLED"}
        from . import patchsel
        patchsel.add_face(bm, new_cp, int(pmat[pi]) if len(obj.data.materials) else 0)   # the new patch's corner quad (patch selection)
        n1 = len(bm.verts)
        owner = int(vb[int(patches[pi][core._SIDE_CPS[side][0]])]) if len(vb) == n0 else 0
        store.set_point_ints(obj, store.ATTR_CP, np.arange(n1))
        store.set_point_ints(obj, store.ATTR_GEN, np.full(n1, g))
        psrc = store.point_ints(obj, store.ATTR_SRC)
        if psrc is not None:                                # the new points come from no source point
            store.set_point_ints(obj, store.ATTR_SRC, np.concatenate([psrc[:n0], np.full(n1 - n0, -1)]))
        if len(vb) == n0:
            store.set_vert_bone(obj, np.concatenate([vb, [owner] * (n1 - n0)]))   # the new points join its group too
        bmesh.update_edit_mesh(obj.data)
        store.set_patch_uv(obj, np.vstack([puv, [puv[pi]]]))
        store.set_patch_mat(obj, np.append(pmat, pmat[pi]))
        store.set_patch_type(obj, np.append(ptype, ptype[pi]))
        store.set_patch_limb(obj, np.append(plimb, plimb[pi]))
        store.set_patches(obj, np.vstack([patches, [new_cp]]))
        store.set_pool_n(obj, n1)
        from . import patchfeel
        patchfeel.apply_interior_visibility(obj)            # the new patch's interiors hidden like the others
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Extruded patch {pi} side {side}: +{n1 - n0} control points, +1 patch")
        return {"FINISHED"}


class BZ_OT_assign_bone(bpy.types.Operator):
    """Skin the selected control points to a bone (reassign which bone deforms them)"""
    bl_idname = "bz.assign_bone"
    bl_label = "Assign Selected to Bone"
    bl_options = {"REGISTER", "UNDO"}

    bone: bpy.props.EnumProperty(name="Bone", items=_skin_bone_items)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (_guard_poll(cls, context, edit=True) and len(store.bone_names(obj)) > 0
                and store.rig(obj) is not None)

    def execute(self, context):
        obj = context.active_object
        idx = int(self.bone)
        if idx < 0:
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        sel = np.nonzero(point_selection(obj))[0]           # hidden interiors follow their corners
        if not len(sel):
            hidden = hidden_left_behind(obj)
            self.report({"ERROR"}, "Select the control points to reassign"
                        + (" -- nothing is selected. " + hidden_selection_note(hidden, "of this creature's control "
                                                                               "points") if hidden else ""))
            return {"CANCELLED"}
        if not set_owners(self, obj, sel, idx):
            return {"CANCELLED"}
        from . import patchfeel
        note = patchfeel.selection_note(obj)
        if note:
            self.report({"WARNING"}, note)
        name = store.bone_display_name(obj, idx)
        self.report({"INFO"}, f"Skinned {len(sel)} control points to '{name}'")
        return {"FINISHED"}


# --- Boner: the skinning loop (Bones sub-mode) ------------------------------------------------------------------
def skin_state(op, obj, edit_points=()):
    """The skin a Boner edit starts from (:class:`store.LiveSkin`), or None after reporting why the edit must not run.
    Every skin write rewrites the bone vertex groups from the owner table, so the table first adopts the rigid group
    reassignments made with Blender's tools (Ctrl+G, weight paint: the rule Export uses) -- the edit then keeps them --
    and an edit refuses while points outside ``edit_points`` hold weights the table can't say (blended / partial, or no
    bone group), instead of silently overwriting them."""
    try:
        live = store.live_skin(obj)
    except ValueError as e:
        op.report({"ERROR"}, f"{e}: the skin table and the mesh disagree (Undo, or re-import the creature)")
        return None
    why = store.added_bone_check_error(obj)
    if why:
        op.report({"ERROR"}, f"the {why}: skin edits stop until it runs (points skinned to added bones would read as "
                             "skinned to no bone)")
        return None
    why = core.skin_edit_refusal(live.unskinned, live.blended, edit_points, live.weights,
                                 store.bone_rig_names(obj))
    if why:
        ed = {int(i) for i in edit_points}
        notes, explained = stale_group_notes(obj, live.stored, [i for i in live.unskinned if int(i) not in ed],
                                             store._added_keys(obj))
        rest = [int(i) for i in live.unskinned if int(i) not in ed and int(i) not in set(explained)]
        if notes:                                        # the cause is a deleted bone's group, not a weight edit
            why = ("; ".join(notes) + ". This skin edit stopped: it rewrites the bone vertex groups from the skin table, "
                   "which would move those points without asking"
                   + ("" if not rest and not [i for i in live.blended if int(i) not in ed] else "; and " + why))
        op.report({"ERROR"}, why)
        return None
    return live


def bone_deform_error(obj, bone) -> str | None:
    """Why control points must not be skinned to source bone ``bone``: its Deform is off in the rig, so the Armature
    modifier ignores its vertex group (the points would stay at rest and Export refuses them)."""
    arm = store.rig(obj)
    if arm is None or bone is None or int(bone) < 0:
        return None
    b = arm.data.bones.get(store.bone_display_name(obj, int(bone)))
    if b is not None and not b.use_deform:
        return (f"'{b.name}' does not deform (Bone Properties > Deform is off): points skinned to it would stay at "
                "rest, and Export refuses them. Turn its Deform on, or pick another bone")
    return None


def set_owners(op, obj, points, bone, live=None) -> bool:
    """One journaled skin edit: ``points`` (pool indices) get owner ``bone`` (a source bone index, or -1 = no bone). The
    owner table and the bone vertex groups (what Blender deforms, and what Export adopts) move together, starting from
    the live skin (:func:`skin_state`: group reassignments adopted, other weight edits refused)."""
    points = np.asarray(points, dtype=np.int64)
    why = bone_deform_error(obj, bone)
    if why:
        op.report({"ERROR"}, why)
        return False
    if live is None:
        live = skin_state(op, obj, points)
        if live is None:
            return False
    if not _begin(op, obj):
        return False
    vb = live.base.copy()
    vb[points] = int(bone)
    store.set_vert_bone(obj, vb)
    if obj.mode == "EDIT":
        import bmesh
        bmesh.update_edit_mesh(obj.data)
    else:
        obj.data.update()
    forget_skin_stats(obj)
    draw.invalidate(obj.name)
    return True


def point_selection(obj) -> np.ndarray:
    """(N,) bool: the selected control points (the live bmesh in edit mode), plus every hidden interior point whose
    frame corner is selected (patchfeel.selection: the interiors are hidden by default, and a skin edit must not
    leave them behind unseen)."""
    from . import patchfeel
    if obj.mode == "EDIT":
        return patchfeel.selection(obj)
    sel = np.zeros(len(obj.data.vertices), dtype=bool)
    obj.data.vertices.foreach_get("select", sel)
    return sel


def select_points(obj, mask, extend=False) -> int:
    """Select the control points in ``mask`` (edit mode; a hidden point is never selected, as in Blender's own
    selection); returns how many the mask holds. A hidden interior point of the mask whose frame corner ends up
    selected is still edited (the B5 rule: interiors follow their corner, :func:`point_selection`); the ones left
    behind are :func:`hidden_left_behind`, which every operator that acts on the selection names."""
    import bmesh
    mask = np.asarray(mask, dtype=bool)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        if mask[v.index] and not v.hide:
            v.select = True
        elif not extend:
            v.select = False
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return int(mask.sum())


def hidden_left_behind(obj, mask=None) -> int:
    """How many control points a selection can NOT reach because they are hidden: with ``mask``, the mask's hidden
    points the effective selection does not cover (their frame corner is not selected too); without one, every
    hidden point of the mesh. Edit Mode only (0 elsewhere: nothing is hidden there)."""
    if obj.mode != "EDIT":
        return 0
    from . import patchfeel
    hid = patchfeel.hidden_points(obj)
    if not hid.any():
        return 0
    if mask is None:
        return int(hid.sum())
    m = np.asarray(mask, dtype=bool)
    return int((m & hid & ~point_selection(obj)).sum())


def hidden_selection_note(n_hidden: int, what: str = "of the control points") -> str:
    """The plain-language line for points a skin edit cannot reach because they are hidden interiors."""
    return (f"{n_hidden} {what} are hidden interior points: show them with Display > Show Interior Points, or select their "
            "frame corner instead")


def active_bone(context, obj):
    """``(source bone index, name)`` of the Boner panel's active bone on this creature, or ``(-1, reason)``."""
    s = getattr(context.scene, "bz_boner", None)
    if s is None or not s.bone:
        return -1, "pick a bone first (Boner > Bones: the Bone field or the eyedropper)"
    idx = store.bone_index(obj, s.bone)
    if idx < 0:
        arm = store.rig(obj)
        bone = arm.data.bones.get(s.bone) if arm is not None else None
        if bone is not None:
            why = store.added_bone_check_error(obj) if bone.get(rig.KEY_BONE_UID) is not None else None
            if why:
                return -1, (f"'{s.bone}' has no owner key: the {why} (points skinned to added bones show orange until "
                            "it runs)")
            try:
                sk = rig.rig_skeleton(arm, src_stubs=store.export_stubs(obj))
            except Exception as e:                        # never silent: the poll message and the panel say it
                return -1, f"'{s.bone}' has no owner key: the added bone check failed: {type(e).__name__}: {e}"
            bad = dict(sk.added_bad) if sk is not None else {}
            if s.bone in bad:
                return -1, f"'{s.bone}' can't be skinned: {bad[s.bone]}"
            if sk is not None and s.bone in sk.added_names:
                return -1, (f"'{s.bone}' is a bone Blender added that the creature has not adopted yet: Boner > Bones > "
                            "Adopt Added Bones (Export takes it as it is)")
        return -1, f"'{s.bone}' is not a bone of this creature's rig"
    return idx, s.bone


_SKIN_STATS: dict = {}


def forget_skin_stats(obj=None) -> None:
    if obj is None:
        _SKIN_STATS.clear()
    else:
        _SKIN_STATS.pop(obj.name, None)


def skin_stats_cached(obj):
    """``boner.skin_stats`` of the LIVE skin for the panel (the owner table read against the bone vertex groups, so a
    Ctrl+G / weight-paint edit shows at once, as Export will see it). Group edits don't move any key, so the groups are
    read on every call (a few ms on the largest creature); the result is kept only for :func:`forget_skin_stats`."""
    try:
        live = store.live_skin(obj)
    except ValueError:
        live = None
    own = live.owners if live is not None else store.vert_bone(obj)
    st = boner.skin_stats(own, store.owner_count(obj), store.patches(obj),
                          blended=len(live.blended) if live is not None else 0)
    _SKIN_STATS[obj.name] = st
    return st


def _skin_poll(cls, context, edit=True, need_bone=False):
    obj = context.active_object
    if not _guard_poll(cls, context, edit=edit):
        return False
    if store.rig(obj) is None or not store.bone_names(obj) or not store.has_vert_bone(obj):
        cls.poll_message_set("the creature has no rig or no skinning table")
        return False
    if need_bone:
        idx, why = active_bone(context, obj)
        if idx < 0:
            cls.poll_message_set(why)
            return False
    return True


class BZ_OT_skin_assign_active(bpy.types.Operator):
    """Skin the selected control points to the active bone (Boner: Assign Vertices to Bone)"""
    bl_idname = "bz.skin_assign_active"
    bl_label = "Assign Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context, need_bone=True)

    def execute(self, context):
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        idx, name = active_bone(context, obj)
        if idx < 0:
            self.report({"ERROR"}, name)
            return {"CANCELLED"}
        sel = np.nonzero(point_selection(obj))[0]
        if not len(sel):
            hidden = hidden_left_behind(obj)
            self.report({"ERROR"}, "Select the control points to assign"
                        + (" -- nothing is selected. " + hidden_selection_note(hidden, "of this creature's control "
                                                                               "points") if hidden else ""))
            return {"CANCELLED"}
        live = skin_state(self, obj, sel)
        if live is None:
            return {"CANCELLED"}
        before = live.base[sel]
        if not set_owners(self, obj, sel, idx, live):
            return {"CANCELLED"}
        moved = int((before != idx).sum())
        from . import patchfeel
        note = patchfeel.selection_note(obj)              # hidden interiors of the selected corners not taken
        if note:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"Assigned {len(sel)} control points to '{name}' ({moved} changed bone)")
        return {"FINISHED"}


class BZ_OT_skin_select_points(bpy.types.Operator):
    """Select the control points the active bone owns (Boner: Get Selection from Bone)"""
    bl_idname = "bz.skin_select_points"
    bl_label = "Select Points"
    bl_options = {"REGISTER", "UNDO"}

    extend: bpy.props.BoolProperty(name="Extend", description="Add to the selection instead of replacing it",
                                   default=False)

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context, need_bone=True)

    def execute(self, context):
        obj = context.active_object
        idx, name = active_bone(context, obj)
        if idx < 0:
            self.report({"ERROR"}, name)
            return {"CANCELLED"}
        mask = store.live_skin(obj).owners == idx
        k = select_points(obj, mask, extend=self.extend)
        left = hidden_left_behind(obj, mask)
        self.report({"WARNING"} if left else {"INFO"},
                    f"'{name}' owns {k} control points" + (" (selected)" if k else "")
                    + ("; " + hidden_selection_note(left, "of them") if left else ""))
        return {"FINISHED"}


class BZ_OT_skin_remove(bpy.types.Operator):
    """Remove the selected control points from the active bone, leaving them unassigned"""
    bl_idname = "bz.skin_remove"
    bl_label = "Remove from Bone"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context, need_bone=True)

    def execute(self, context):
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        idx, name = active_bone(context, obj)
        if idx < 0:
            self.report({"ERROR"}, name)
            return {"CANCELLED"}
        sel = point_selection(obj)
        vb = store.live_skin(obj).owners
        pts = np.nonzero(sel & (vb == idx))[0]
        other = int((sel & (vb != idx)).sum())
        if not len(pts):
            hidden = hidden_left_behind(obj, vb == idx)
            self.report({"ERROR"}, f"None of the {int(sel.sum())} selected control points belong to '{name}'"
                        + (". " + hidden_selection_note(hidden, f"of '{name}'s points") if hidden else ""))
            return {"CANCELLED"}
        if not set_owners(self, obj, pts, -1):
            return {"CANCELLED"}
        used = len(core.unowned_patch_points(store.live_skin(obj).owners, store.patches(obj)))
        self.report({"INFO"}, f"Removed {len(pts)} control points from '{name}'"
                              + (f" ({other} selected points of other bones left)" if other else "")
                              + (f"; {used} patch points now have no bone: Export refuses them until they are "
                                 "assigned (Select Unassigned, Auto Skin)" if used else ""))
        return {"FINISHED"}


class BZ_OT_skin_select_unassigned(bpy.types.Operator):
    """Select every control point with no bone (orange in the By Bone overlay)"""
    bl_idname = "bz.skin_select_unassigned"
    bl_label = "Select Unassigned"
    bl_options = {"REGISTER", "UNDO"}

    extend: bpy.props.BoolProperty(name="Extend", description="Add to the selection instead of replacing it",
                                   default=False)

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context)

    def execute(self, context):
        obj = context.active_object
        vb = store.live_skin(obj).owners
        k = select_points(obj, vb < 0, extend=self.extend)
        left = hidden_left_behind(obj, vb < 0)
        used = len(core.unowned_patch_points(vb, store.patches(obj)))
        self.report({"WARNING"} if left else {"INFO"},
                    f"{k} control points have no bone" + (f" ({used} used by patches)" if k else "")
                    + ("; " + hidden_selection_note(left, "of them") if left else ""))
        return {"FINISHED"}


def bone_segments(obj, arm, n_bones):
    """``(heads, tails, stamped)`` in the creature mesh's space for every source bone index: the rig's rest bones
    (what the viewport shows), found by their source-index stamp or name; ``stamped`` is False for an index no rig
    bone resolves to."""
    heads = np.zeros((n_bones, 3))
    tails = np.zeros((n_bones, 3))
    found = np.zeros(n_bones, dtype=bool)
    to_mesh = obj.matrix_world.inverted() @ arm.matrix_world
    for b in arm.data.bones:
        i = store.bone_index(obj, b.name)
        if 0 <= i < n_bones and not found[i]:
            heads[i] = (to_mesh @ b.head_local)[:]
            tails[i] = (to_mesh @ b.tail_local)[:]
            found[i] = True
    return heads, tails, found


def auto_skin_rig(obj, arm, n_bones):
    """``(heads, tails, eligible)`` for Auto Skin in the creature mesh's space, one entry per owner key (``n_bones``:
    :func:`store.owner_count`, the source bones then the new-bone table's): the rig's rest heads (what the viewport
    shows, rest-pose edits included), tails by :func:`boner.skin_segments` (to the nearest child's head, an added child
    included; a leaf is its head alone -- never the rig's drawn 0.1 leaf stub), and the bones it may skin to: rig bones
    that deform, not stub and not end-site bones (:func:`boner.auto_skin_mask`, measured on the base-game creatures).
    Added bones on the rig are segments and targets like source bones (their parent from the rig, end sites by name);
    a table entry whose bone is not on the rig is none. Raises ValueError when the source creature can't be read or the
    added bones can't be checked."""
    heads, _t, found = bone_segments(obj, arm, n_bones)
    try:
        src = core.read_source(store.source_path(obj))
    except Exception as e:                                # never silent: said by the operator
        raise ValueError(f"the source creature can't be read for its bone hierarchy: {type(e).__name__}: {e}") from e
    n_src = len(store.bone_names(obj))
    if len(src.bones) != n_src or n_bones < n_src:
        raise ValueError(f"the source creature has {len(src.bones)} bones, the rig table {n_src}: re-import it")
    try:
        keys = store.new_bone_keys(obj)
    except store.AddedBoneCheckError as e:
        raise ValueError(str(e)) from e
    parents = [b.parent for b in src.bones] + [-1] * (n_bones - n_src)
    names = [""] * (n_bones - n_src)
    for nm, k in keys.items():
        b = arm.data.bones.get(nm)
        if b is not None and n_src <= k < n_bones:
            parents[k] = store.bone_index(obj, b.parent.name) if b.parent is not None else -1
            names[k - n_src] = nm
    tails = boner.skin_segments(heads, parents)
    ok = found & np.concatenate([boner.auto_skin_mask(src),
                                 np.array([bool(nm) for nm in names], dtype=bool) & ~boner.end_site_bones(names)])
    for b in arm.data.bones:
        i = store.bone_index(obj, b.name)
        if 0 <= i < n_bones and not b.use_deform:
            ok[i] = False
    return heads, tails, ok


def pending_added_bones(obj, arm) -> list:
    """The rig's added bones the add rule takes that the new-bone table does not hold yet (Blender's own Add / Extrude
    before Adopt Added Bones): they have no owner key, so no skin tool can give them points. Raises ValueError when the
    check can't run."""
    if arm is None:
        return []
    try:
        sk = rig.rig_skeleton(arm, src_stubs=store.export_stubs(obj))
        keys = store.new_bone_keys(obj)
    except Exception as e:
        raise ValueError(f"added bone check failed: {type(e).__name__}: {e}") from e
    return [] if sk is None else [n for n in sk.added_names if n not in keys]


class BZ_OT_skin_auto(bpy.types.Operator):
    """Skin control points to their nearest bone, as a rough first pass"""
    bl_idname = "bz.skin_auto"
    bl_label = "Auto Skin"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(name="Points", items=boner.AUTO_TARGETS, default="AUTO")
    handles_follow_corners: BoolProperty(
        name="Handles Follow Corners", default=True,
        description="Give each handle and interior point its nearest corner's bone, as stock creatures do")

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context, edit=False)

    def execute(self, context):
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        arm = store.rig(obj)
        n_bones = store.owner_count(obj)                     # the source bones and the new-bone table's
        in_edit = obj.mode == "EDIT"
        sel = point_selection(obj) if in_edit else None      # Object Mode: the selection is not shown, never used
        why = boner.auto_target_refusal(self.target, sel, in_edit)
        if why:
            self.report({"ERROR"}, f"Auto Skin: {why}")
            return {"CANCELLED"}
        try:
            live0 = store.live_skin(obj)
        except ValueError as e:
            self.report({"ERROR"}, f"Auto Skin: {e}")
            return {"CANCELLED"}
        pts = boner.skin_targets(live0.owners, sel, self.target)
        if not len(pts):
            self.report({"WARNING"}, "Auto Skin: no control points to skin (" + {
                "AUTO": "none selected and none unassigned" if in_edit else "none unassigned",
                "SELECTED": "none selected", "UNASSIGNED": "none unassigned",
                "ALL": "the creature has none"}[self.target] + ")")
            return {"CANCELLED"}
        live = skin_state(self, obj, pts)
        if live is None:
            return {"CANCELLED"}
        try:
            heads, tails, eligible = auto_skin_rig(obj, arm, n_bones)
            pending = pending_added_bones(obj, arm)
        except ValueError as e:
            self.report({"ERROR"}, f"Auto Skin: {e}")
            return {"CANCELLED"}
        if not eligible.any():
            self.report({"ERROR"}, "Auto Skin: the rig has no deforming bone that is not a stub or an end site")
            return {"CANCELLED"}
        new = boner.auto_skin(_live_blender_verts(obj)[pts], heads, tails, eligible)
        vb = live.base.copy()
        vb[pts] = new
        if self.handles_follow_corners:                  # the stock rule; battle skins with corner bones only
            from ICParse.sgm.creature import layout as LAYOUT
            vb = LAYOUT.handles_follow_corners(vb, store.patches(obj), pts)
            new = vb[pts]
        changed = int((new != live.owners[pts]).sum())
        if not _begin(self, obj):
            return {"CANCELLED"}
        store.set_vert_bone(obj, vb)
        if obj.mode == "EDIT":
            import bmesh
            bmesh.update_edit_mesh(obj.data)
        else:
            obj.data.update()
        forget_skin_stats(obj)
        draw.invalidate(obj.name)
        left_out = (f" Added bones not adopted yet, so left out: {', '.join(repr(n) for n in pending[:4])}"
                    f"{', ...' if len(pending) > 4 else ''}.") if pending else ""
        self.report({"INFO"}, f"Auto Skin: {len(pts)} control points weighted to their nearest bone ({changed} changed)."
                              f"{left_out} Look over the result in By Bone.")
        return {"FINISHED"}


# the Pick Bone overlay colours
_PICKB_HOVER = (1.0, 1.0, 1.0, 1.0)


class BZ_OT_pick_bone(bpy.types.Operator):
    """Pick the active bone in the viewport by clicking a control point or a bone"""
    bl_idname = "bz.pick_bone"
    bl_label = "Pick Bone"
    bl_options = set()               # UI state only (the panel's active bone): no undo step

    @classmethod
    def poll(cls, context):
        return _skin_poll(cls, context, edit=False)

    def execute(self, context):
        self.report({"ERROR"}, "Pick Bone only works in a 3D Viewport. In a script, set scene.bz_boner.bone to the bone's name.")
        return {"CANCELLED"}

    def invoke(self, context, event):
        obj = context.active_object
        area, region, rv3d = view3d_window_region(context, event)
        if region is None:
            self.report({"ERROR"}, "Pick Bone runs in a 3D Viewport: set scene.bz_boner.bone to the bone's name")
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        self._obj_name, self._area, self._region, self._rv3d = obj.name, area, region, rv3d
        self._rule, self._hit = boner.BonePickRule(), None
        self._verts = _live_blender_verts(obj)
        self._owners = store.live_skin(obj).owners
        arm = store.rig(obj)
        self._heads, self._tails, self._found = bone_segments(obj, arm, store.owner_count(obj))
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(self._draw_view, (context,), "WINDOW", "POST_VIEW")
        self._update_screen(context)
        if not self._start_modal(context):
            self._cleanup(context)
            self.report({"ERROR"}, "Pick Bone needs a 3D Viewport. In background mode, set scene.bz_boner.bone to the bone's name.")
            return {"CANCELLED"}
        area.header_text_set(self.status_text())
        return {"RUNNING_MODAL"}

    def _start_modal(self, context) -> bool:
        if bpy.app.background:                       # no event loop: a modal handler would never run
            return False
        context.window_manager.modal_handler_add(self)
        return True

    def status_text(self) -> str:
        hit = getattr(self, "_hit", None)
        obj = bpy.data.objects.get(getattr(self, "_obj_name", ""))
        under = (f"  --  under the cursor: {store.bone_display_name(obj, hit.bone)}"
                 if hit is not None and hit.bone is not None and obj is not None else "")
        msg = f"  --  {self._rule.message}" if getattr(self, "_rule", None) is not None and self._rule.message else ""
        return "Pick Bone: click a control point or a bone. Esc cancels" + under + msg

    def _update_screen(self, context):
        """Project the control points and the bone segments into the picking region (NaN where they don't project)."""
        from bpy_extras import view3d_utils
        from mathutils import Vector
        obj = bpy.data.objects.get(self._obj_name)
        mw = obj.matrix_world if obj is not None else None

        def proj(arr):
            out = np.full((len(arr), 2), np.nan)
            if mw is None:
                return out
            for k, p in enumerate(arr):
                q = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, mw @ Vector(p))
                if q is not None:
                    out[k] = (q.x, q.y)
            return out
        self._pt_xy = proj(self._verts)
        self._head_xy = proj(self._heads)
        self._tail_xy = proj(self._tails)
        self._head_xy[~self._found] = np.nan

    def _mouse(self, event):
        r = self._region
        x, y = event.mouse_x - r.x, event.mouse_y - r.y
        return (x, y), (0 <= x < r.width and 0 <= y < r.height)

    def _mine(self) -> bool:
        reg = getattr(bpy.context, "region", None)
        try:
            return reg is not None and reg.as_pointer() == self._region.as_pointer()
        except ReferenceError:
            return False

    def _cleanup(self, context):
        handle = getattr(self, "_h3d", None)
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            self._h3d = None
        area = getattr(self, "_area", None)
        try:
            if area is not None:
                area.header_text_set(None)
                area.tag_redraw()
        except ReferenceError:
            pass

    def modal(self, context, event):
        obj = bpy.data.objects.get(self._obj_name)
        if obj is None:
            self._cleanup(context)
            self.report({"WARNING"}, "Pick Bone cancelled: the creature is gone")
            return {"CANCELLED"}
        mouse, inside = self._mouse(event)
        if event.type == "MOUSEMOVE" or event.type in boner.NAV_EVENTS:
            self._update_screen(context)
            self._hit = (boner.pick_target(self._pt_xy, self._owners, self._head_xy, self._tail_xy, mouse)
                         if inside else None)
        if not inside and event.type in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"):
            return {"PASS_THROUGH"}
        act = self._rule.handle(event.type, event.value, self._hit)
        try:
            if self._area is not None:
                self._area.header_text_set(self.status_text())
                self._area.tag_redraw()
        except ReferenceError:
            pass
        if act == "PASS_THROUGH":
            return {"PASS_THROUGH"}
        if act == "CANCEL":
            self._cleanup(context)
            self.report({"INFO"}, "Pick Bone cancelled: the active bone is unchanged")
            return {"CANCELLED"}
        if act == "FINISH":
            self._cleanup(context)
            name = store.bone_display_name(obj, self._rule.picked)
            context.scene.bz_boner.bone = name
            k = int((self._owners == self._rule.picked).sum())
            self.report({"INFO"}, f"Active bone: {name} ({k} control points)")
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _draw_view(self, context):
        if not self._mine():
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        obj = bpy.data.objects.get(self._obj_name)
        hit = self._hit
        if obj is None or hit is None or hit.bone is None:
            return
        sh = gpu.shader.from_builtin("FLAT_COLOR")
        gpu.matrix.push()
        try:
            gpu.matrix.multiply_matrix(obj.matrix_world)
            gpu.state.depth_test_set("NONE")
            idx = np.nonzero(self._owners == hit.bone)[0]
            if len(idx):
                gpu.state.point_size_set(8.0)
                b = batch_for_shader(sh, "POINTS", {"pos": self._verts[idx].astype(np.float32),
                                                    "color": [_PICKB_HOVER] * len(idx)})
                sh.bind(); b.draw(sh)
            if self._found[hit.bone]:
                gpu.state.line_width_set(4.0)
                seg = np.array([self._heads[hit.bone], self._tails[hit.bone]], np.float32)
                b = batch_for_shader(sh, "LINES", {"pos": seg, "color": [_PICKB_HOVER] * 2})
                sh.bind(); b.draw(sh)
        except Exception as e:                                       # said in the header, never swallowed
            self._rule.message = f"overlay failed: {type(e).__name__}: {e}"
        finally:
            gpu.state.line_width_set(1.0)
            gpu.matrix.pop()


@enumitems.stable
def _conn_items(self, context):
    obj = context.active_object
    conns = store.connections(obj) if obj else []
    enc = store.conn_encodings(obj) if obj else []
    return [(str(i), f"{n} -> {b} ({len(v)} {'corners' if (enc[i] if i < len(enc) else 1) else 'ints, verbatim'})", "")
            for i, (n, b, v) in enumerate(conns)] or [("-1", "(no connections)", "")]


@enumitems.stable
def _bone_items(self, context):
    """The skeleton's bones as imported: connection rows name hosts by the file's names (a bone renamed in Blender since
    is offered under its import name; the export writes its new name)."""
    obj = context.active_object
    arm = store.rig(obj) if obj else None
    if arm:
        try:
            names = rig.source_bone_names(arm)
        except KeyError:
            names = [b.name for b in arm.data.bones]
        return [(n, n, "") for n in names] or [("", "(none)", "")]
    return [("", "(no rig)", "")]


# --- connection rings --------------------------------------------------------------------------------------
_RING_META: dict = {}


def ring_meta(obj):
    """(flags1 per connection, flags2 per connection, limb_id per limb): from the store, or -- for an object imported
    before they were stored -- from its source file (read once per file version; the source guard makes it the file
    imported)."""
    flags = store.conn_flags(obj)
    ids = store.limb_ids(obj)
    if flags is not None and ids is not None:
        return flags[0], flags[1], ids
    src = store.source_path(obj)
    try:
        _size, sha = store.file_sha1(src)
    except OSError as e:
        raise ValueError(f"this object predates the stored ring flags and its source {src} can't be read: {e}") from e
    key = (os.path.abspath(src), sha)
    hit = _RING_META.get(key)
    if hit is None:
        m = core.read_source(src)
        hit = ([int(c.flags1) for c in m.limb_connections], [int(c.flags2) for c in m.limb_connections],
               [int(lm.limb_id) for lm in m.limbs])
        _RING_META.clear()
        _RING_META[key] = hit
    f1, f2, lids = hit
    return (flags[0] if flags else f1), (flags[1] if flags else f2), (ids if ids is not None else lids)


def _live_blender_verts(obj) -> np.ndarray:
    """The control points as they are now (the live bmesh in edit mode)."""
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64).reshape(-1, 3)
    return _mesh_verts(obj)


def ring_rows(obj):
    """(name, bone, ring, enc, flags1, flags2) per connection, for combocore.ring_status."""
    f1, f2, _ids = ring_meta(obj)
    conns, encs = store.connections(obj), store.conn_encodings(obj)
    if len(f1) != len(conns) or len(f2) != len(conns):
        raise ValueError(f"{len(conns)} connections but flags for {len(f2)} (re-import the creature)")
    return [(n, b, v, encs[i], f1[i], f2[i]) for i, (n, b, v) in enumerate(conns)]


def ring_picker(obj, ci):
    """A core.RingPicker for connection ``ci`` of a native creature (raises ValueError with a readable reason)."""
    conns = store.connections(obj)
    if not 0 <= ci < len(conns):
        raise ValueError(f"connection {ci}: the creature has {len(conns)} connections")
    f1, f2, ids = ring_meta(obj)
    if ci >= len(f1) or ci >= len(f2):
        raise ValueError(f"connection {ci} has no flags in the source (it has {len(f2)} connections)")
    return core.RingPicker(store.patches(obj), store.patch_limb(obj) if obj.get(store.KEY_PATCH_LIMB) is not None else None,
                           ids, f1[ci], f2[ci], name=conns[ci][0])


_STOCK_DIR_CACHE: dict = {}


def _stock_dir(context) -> str:
    """The stock creature dir ring picking aligns to (the Combine Preview's), '' without one. Memoised for a moment per
    setting: the overlay asks every redraw, and resolving lists directories."""
    import time
    from . import combocore, combopreview
    if not combocore.available():
        return ""
    settings = getattr(context.scene, "bz_combine", None) if context.scene is not None else None
    key = settings.stock_dir if settings is not None else None
    hit = _STOCK_DIR_CACHE.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < 2.0:
        return hit[1]
    d = combopreview.resolve_stock_dir(settings)
    _STOCK_DIR_CACHE.clear()
    _STOCK_DIR_CACHE[key] = (now, d)
    return d


def fix_ring(obj, picker, corners, context):
    """``combocore.ring_fix`` for a picked ring of ``obj``: (corners, notes) in the stock winding and start."""
    from . import combocore
    if not combocore.available():
        return list(corners), [f"winding and start not checked: the combiner is not importable ({combocore.IMPORT_ERROR})"]
    world = core.blender_to_sgm(_live_blender_verts(obj))
    _f1, _f2, ids = ring_meta(obj)
    tc = combocore.torso_centre(world, store.patches(obj), store.patch_limb(obj), ids)
    stem = os.path.splitext(os.path.basename(store.source_path(obj)))[0]
    dirs, _names = combocore.partner_ring_dirs(_stock_dir(context), picker.slot_key, exclude_stem=stem)
    return combocore.ring_fix(world[list(corners)], corners, picker.slot_key, tc, dirs)


def apply_picked_ring(op, context, obj, ci, picker) -> set:
    """Finish a pick (size / closure refusals, then the stock winding and start), store it as a corner loop (one
    journaled table edit) and report what the fix did."""
    try:
        corners, notes = picker.finish(lambda c: fix_ring(obj, picker, c, context))
    except ValueError as e:
        op.report({"ERROR"}, f"Ring not set: {e}")
        return {"CANCELLED"}
    if not _begin(op, obj):
        return {"CANCELLED"}
    conns = store.connections(obj)
    n, b, _old = conns[ci]
    conns[ci] = (n, b, corners)
    store.set_connections(obj, conns)
    forget_ring_status(obj)
    draw.invalidate(obj.name)
    msg = f"Connection '{n}' ring set to {len(corners)} corners {corners}"
    op.report({"INFO"}, msg + (f" ({'; '.join(notes)})" if notes else ""))
    where = picker.placement_problem(corners)
    if where:
        op.report({"WARNING"}, f"Connection '{n}' ring: {where}")
    return {"FINISHED"}


_RING_STATUS: dict = {}
_RING_STATUS_TTL = 0.5


def forget_ring_status(obj=None) -> None:
    if obj is None:
        _RING_STATUS.clear()
    else:
        _RING_STATUS.pop(obj.name, None)


def ring_status_cached(obj, context, verts_blender=None):
    """combocore.ring_status for the panel and the overlay, recomputed when the rings, the patch / limb tables or the
    stock dir change, and at most every half second while only points move. [] when the combiner is missing or the
    pool is broken (the panel shows that error instead)."""
    import hashlib
    import time
    from . import combocore
    if not combocore.available() or store.pool_error_cached(obj):
        return []
    verts = _live_blender_verts(obj) if verts_blender is None else np.asarray(verts_blender, dtype=np.float64)
    h = hashlib.sha1()
    for k in (store.KEY_CONN_VERTS, store.KEY_CONN_COUNTS, store.KEY_CONN_ENC, store.KEY_CONN_BONES,
              store.KEY_CONN_FLAGS1, store.KEY_CONN_FLAGS2, store.KEY_CONN_NAMES, store.KEY_LIMB_IDS,
              store.KEY_STORE_VERSION, store.KEY_PATCH_LIMB, store.KEY_GEN, store.KEY_NPATCH):
        v = obj.get(k)
        h.update(k.encode() + repr(v.to_list() if hasattr(v, "to_list") else v).encode())
    stock = _stock_dir(context)
    key = (h.hexdigest(), stock)
    vkey = hashlib.sha1(np.ascontiguousarray(verts, dtype=np.float32).tobytes()).hexdigest()
    now = time.monotonic()
    hit = _RING_STATUS.get(obj.name)
    if hit is not None and hit[0] == key and (hit[1] == vkey or now - hit[2] < _RING_STATUS_TTL):
        return hit[3]
    try:
        f1, f2, ids = ring_meta(obj)
        stem = os.path.splitext(os.path.basename(store.source_path(obj)))[0]
        res = combocore.ring_status(core.blender_to_sgm(verts), store.patches(obj), store.patch_limb(obj), ids,
                                    ring_rows(obj), stock, exclude_stem=stem)
    except Exception as e:                                # shown in the panel, never a silently missing readout
        res = [f"ring check failed: {type(e).__name__}: {e}"]
    _RING_STATUS[obj.name] = (key, vkey, now, res)
    return res


class BZ_OT_set_connection_ring(bpy.types.Operator):
    """Set a connection's ring from the corners selected in order"""
    bl_idname = "bz.set_connection_ring"
    bl_label = "Set Connection Ring from Selection"
    bl_options = {"REGISTER", "UNDO"}

    conn: bpy.props.EnumProperty(name="Connection", items=_conn_items)

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context, edit=True) and bool(store.connections(context.active_object))

    def execute(self, context):
        import bmesh
        obj = context.active_object
        ci = int(self.conn)
        if ci < 0:
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        ordered = [v.index for v in bm.select_history if isinstance(v, bmesh.types.BMVert) and v.select]
        selected = [v.index for v in bm.verts if v.select]
        if len(set(ordered)) != len(selected):
            self.report({"ERROR"}, f"Ring not set: Shift+click the corners in order ({len(selected)} selected, "
                                   f"{len(set(ordered))} in click order), or use Pick Ring.")
            return {"CANCELLED"}
        try:
            picker = ring_picker(obj, ci)
        except ValueError as e:
            self.report({"ERROR"}, f"Ring not set: {e}")
            return {"CANCELLED"}
        why = picker.set_sequence(list(dict.fromkeys(ordered)))
        if why:
            self.report({"ERROR"}, f"Ring not set: {why}")
            return {"CANCELLED"}
        return apply_picked_ring(self, context, obj, ci, picker)


# the Pick Ring overlay colours
_PICK_CAND = (0.25, 0.75, 0.85, 0.55)
_PICK_NEXT = (1.0, 0.9, 0.2, 1.0)
_PICK_PATH = (0.2, 1.0, 0.35, 1.0)
_PICK_PREF = (0.35, 0.9, 1.0, 0.9)
_PICK_HOVER = (1.0, 1.0, 1.0, 1.0)


def view3d_window_region(context, event=None):
    """``(area, region, region_3d)`` of the 3D Viewport a viewport tool works in, or ``(None, None, None)``: the
    WINDOW region under the cursor, else the largest WINDOW region of the current 3D Viewport area (an operator run
    from its sidebar has the UI region as ``context.region``, which has no view to project into), else of the first 3D
    Viewport on the screen. The view is the area's ``SpaceView3D.region_3d``."""
    areas = []
    if context.area is not None and context.area.type == "VIEW_3D":
        areas.append(context.area)
    screen = getattr(context, "screen", None)
    if screen is not None:
        areas += [a for a in screen.areas if a.type == "VIEW_3D" and a not in areas]

    def view(area):
        space = area.spaces.active
        return getattr(space, "region_3d", None) if space is not None and space.type == "VIEW_3D" else None
    if event is not None:
        for area in areas:
            for r in area.regions:
                if (r.type == "WINDOW" and r.x <= event.mouse_x < r.x + r.width and r.y <= event.mouse_y < r.y + r.height
                        and view(area) is not None):
                    return area, r, view(area)
    for area in areas:
        wins = [r for r in area.regions if r.type == "WINDOW"]
        if wins and view(area) is not None:
            return area, max(wins, key=lambda x: x.width * x.height), view(area)
    return None, None, None


class BZ_OT_pick_ring(bpy.types.Operator):
    """Click the corners of a connection's ring in the viewport, one by one"""
    bl_idname = "bz.pick_ring"
    bl_label = "Pick Ring"
    bl_options = {"UNDO"}            # one undo step; no redo panel (re-running it is the interactive pick again)

    conn: bpy.props.EnumProperty(name="Connection", items=_conn_items)

    @classmethod
    def poll(cls, context):
        return _guard_poll(cls, context, edit=True) and bool(store.connections(context.active_object))

    def execute(self, context):
        self.report({"ERROR"}, "Pick Ring only works in a 3D Viewport. In a script, select the corners in order and run "
                               "bpy.ops.bz.set_connection_ring.")
        return {"CANCELLED"}

    def invoke(self, context, event):
        obj = context.active_object
        conns = store.connections(obj)
        if not self.properties.is_property_set("conn") and len(conns) > 1:
            # run from search / a shortcut: ask which connection instead of silently picking the first
            if bpy.app.background:                          # no window to pop a menu in
                self.report({"ERROR"}, f"Pick Ring: which connection? Pass conn (0-{len(conns) - 1})")
                return {"CANCELLED"}

            def menu(m, _ctx):
                m.layout.operator_context = "INVOKE_DEFAULT"
                for i, (n, _b, _v) in enumerate(conns):
                    m.layout.operator(BZ_OT_pick_ring.bl_idname, text=n).conn = str(i)
            context.window_manager.popup_menu(menu, title="Pick Ring: which connection?", icon="EYEDROPPER")
            return {"CANCELLED"}
        ci = int(self.conn)
        if ci < 0:
            return {"CANCELLED"}
        area, region, rv3d = view3d_window_region(context, event)
        if region is None:
            self.report({"ERROR"}, "Pick Ring runs in a 3D Viewport (use Set Connection Ring from a script)")
            return {"CANCELLED"}
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        try:
            self._picker = ring_picker(obj, ci)
        except ValueError as e:
            self.report({"ERROR"}, f"Pick Ring: {e}")
            return {"CANCELLED"}
        self._obj_name, self._ci, self._hover, self._screen = obj.name, ci, None, {}
        self._area, self._region, self._rv3d = area, region, rv3d
        verts = _live_blender_verts(obj)
        self._verts = verts
        self._current = [c for c in store.connections(obj)[ci][2] if 0 <= c < len(verts)]
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(self._draw_view, (context,), "WINDOW", "POST_VIEW")
        self._h2d = bpy.types.SpaceView3D.draw_handler_add(self._draw_pixel, (context,), "WINDOW", "POST_PIXEL")
        self._update_screen(context)
        area.header_text_set(self._picker.status_text())
        self._start_modal(context)
        return {"RUNNING_MODAL"}

    def _start_modal(self, context):
        context.window_manager.modal_handler_add(self)

    # -- helpers
    def _update_screen(self, context):
        """Project the candidate corners into the picking region (the viewport's WINDOW region, never the sidebar)."""
        from bpy_extras import view3d_utils
        from mathutils import Vector
        obj = bpy.data.objects.get(self._obj_name)
        mw = obj.matrix_world if obj is not None else None
        self._screen = {}
        for c in self._picker.candidates:
            if 0 <= c < len(self._verts) and mw is not None:
                p = view3d_utils.location_3d_to_region_2d(self._region, self._rv3d, mw @ Vector(self._verts[c]))
                self._screen[c] = None if p is None else (p.x, p.y)

    def _mouse(self, event):
        """The cursor in the picking region's pixels, and whether it is inside that region."""
        r = self._region
        x, y = event.mouse_x - r.x, event.mouse_y - r.y
        return (x, y), (0 <= x < r.width and 0 <= y < r.height)

    def _mine(self) -> bool:
        """A draw callback runs for every 3D Viewport: draw only in the picking region."""
        reg = getattr(bpy.context, "region", None)
        try:
            return reg is not None and reg.as_pointer() == self._region.as_pointer()
        except ReferenceError:
            return False

    def _cleanup(self, context):
        for h in ("_h3d", "_h2d"):
            handle = getattr(self, h, None)
            if handle is not None:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
                setattr(self, h, None)
        area = getattr(self, "_area", None)
        try:
            if area is not None:
                area.header_text_set(None)
                area.tag_redraw()
        except ReferenceError:                             # the area was closed while picking
            pass

    def modal(self, context, event):
        obj = bpy.data.objects.get(self._obj_name)
        if obj is None or obj.mode != "EDIT":
            self._cleanup(context)
            self.report({"WARNING"}, "Pick Ring cancelled: the creature left Edit Mode")
            return {"CANCELLED"}
        mouse, inside = self._mouse(event)
        if event.type == "MOUSEMOVE" or event.type in core._RING_NAV_EVENTS:
            self._update_screen(context)
            self._hover = core.RingPicker.nearest(self._screen, mouse, self._picker.allowed()) if inside else None
        if not inside and event.type in ("LEFTMOUSE", "RIGHTMOUSE", "MIDDLEMOUSE"):
            return {"PASS_THROUGH"}                          # the sidebar, the header, another editor
        act = self._picker.handle(event.type, event.value, self._hover)
        area = getattr(self, "_area", None)
        try:
            if area is not None:
                area.header_text_set(self._picker.status_text()
                                     + (f"  --  {self._picker.message}" if self._picker.message else ""))
                area.tag_redraw()
        except ReferenceError:                             # the area was closed while picking
            pass
        if act == "PASS_THROUGH":
            return {"PASS_THROUGH"}
        if act == "CANCEL":
            self._cleanup(context)
            self.report({"INFO"}, "Pick Ring cancelled: the ring is unchanged")
            return {"CANCELLED"}
        if act == "FINISH":
            self._cleanup(context)
            return apply_picked_ring(self, context, obj, self._ci, self._picker)
        return {"RUNNING_MODAL"}

    def _draw_view(self, context):
        if not self._mine():
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        obj = bpy.data.objects.get(self._obj_name)
        if obj is None:
            return
        pk, V = self._picker, self._verts
        sh = gpu.shader.from_builtin("FLAT_COLOR")
        gpu.matrix.push()
        try:
            gpu.matrix.multiply_matrix(obj.matrix_world)
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("ALPHA")

            def pts(idx, col, size):
                idx = [i for i in idx if 0 <= i < len(V)]
                if idx:
                    gpu.state.point_size_set(size)
                    b = batch_for_shader(sh, "POINTS", {"pos": V[idx].astype(np.float32), "color": [col] * len(idx)})
                    sh.bind(); b.draw(sh)

            def lines(pairs, col, width):
                pairs = [(a, b) for a, b in pairs if 0 <= a < len(V) and 0 <= b < len(V)]
                if pairs:
                    gpu.state.line_width_set(width)
                    pos = V[[i for ab in pairs for i in ab]].astype(np.float32)
                    b = batch_for_shader(sh, "LINES", {"pos": pos, "color": [col] * len(pos)})
                    sh.bind(); b.draw(sh)

            lines([tuple(k) for k in pk.preferred], _PICK_PREF, 2.5)
            if self._current and not pk.picked:
                loop = self._current + self._current[:1]
                lines(list(zip(loop, loop[1:])), (0.8, 0.8, 0.8, 0.6), 1.5)
            pts(sorted(pk.candidates), _PICK_CAND, 4.0)
            pts(sorted(pk.allowed()), _PICK_NEXT, 9.0)
            if pk.picked:
                lines(list(zip(pk.picked, pk.picked[1:])), _PICK_PATH, 3.0)
                pts(pk.picked, _PICK_PATH, 8.0)
                pts(pk.picked[:1], _PICK_PATH, 14.0)                 # the start corner
                if len(pk.picked) > 1:                               # direction tick from corner 0 toward corner 1
                    a, b = V[pk.picked[0]], V[pk.picked[1]]
                    tip = (a + 0.35 * (b - a)).astype(np.float32)
                    bt = batch_for_shader(sh, "LINES", {"pos": np.array([a, tip], np.float32),
                                                        "color": [_PICK_HOVER, _PICK_HOVER]})
                    gpu.state.line_width_set(5.0)
                    sh.bind(); bt.draw(sh)
            if self._hover is not None:
                pts([self._hover], _PICK_HOVER, 12.0)
        except Exception as e:                                       # said in the header, never swallowed
            self._picker.message = f"overlay failed: {type(e).__name__}: {e}"
        finally:
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
            gpu.matrix.pop()

    def _draw_pixel(self, context):
        if not self._mine():
            return
        import blf
        pk = self._picker
        font = 0
        try:
            blf.size(font, 14)
            for k, c in enumerate(pk.picked):
                xy = self._screen.get(c)
                if xy is not None:
                    blf.color(font, *_PICK_PATH)
                    blf.position(font, xy[0] + 8, xy[1] + 8, 0)
                    blf.draw(font, str(k))
            y = 30
            if pk.message:
                blf.color(font, 1.0, 0.45, 0.35, 1.0)
                blf.position(font, 20, y + 20, 0)
                blf.draw(font, pk.message[:160])
            where = pk.placement_problem() if pk.check() is None else None
            blf.color(font, 1.0, 1.0, 1.0, 1.0)
            blf.position(font, 20, y, 0)
            obj = bpy.data.objects.get(self._obj_name)
            blf.draw(font, f"{pk.name}: {pk.slot_label} {pk.wanted_text()}"
                           + (f"  (host {store.connections(obj)[self._ci][1]})" if obj is not None else "")
                           + ("  --  " + "; ".join(pk.notes) if pk.notes else "")
                           + (f"  --  WARNING {where}" if where else ""))
        except Exception as e:
            self._picker.message = f"overlay text failed: {type(e).__name__}: {e}"


class BZ_OT_set_connection_bone(bpy.types.Operator):
    """Set the parent bone a connection attaches to"""
    bl_idname = "bz.set_connection_bone"
    bl_label = "Set Connection Bone"
    bl_options = {"REGISTER", "UNDO"}

    conn: bpy.props.EnumProperty(name="Connection", items=_conn_items)
    bone: bpy.props.EnumProperty(name="Bone", items=_bone_items)

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object) and bool(store.connections(context.active_object))

    def execute(self, context):
        obj = context.active_object
        ci = int(self.conn)
        if ci < 0 or not self.bone:
            return {"CANCELLED"}
        arm = store.rig(obj)
        try:
            from . import boneops
            # the source and the C8 rules, as the rig box and Export check them: without the source a rig with a
            # deleted or re-parented bone can't be checked at all and every host edit would be refused
            src = core.read_source(store.source_path(obj))
            err = store.sync(obj) or (rig.skeleton_error(arm, src, src_stubs=store.export_stubs(obj),
                                                         rules=boneops.topology_rules(obj, src))
                                      if arm is not None else None)
        except Exception as e:                            # the source's stub bones can't be read
            err = f"the skeleton check failed: {type(e).__name__}: {e}"
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        if not _begin(self, obj):
            return {"CANCELLED"}
        conns = store.connections(obj)
        n, _b, v = conns[ci]
        conns[ci] = (n, self.bone, v)
        store.set_connections(obj, conns)
        self.report({"INFO"}, f"Connection '{n}' attaches to '{self.bone}'")
        return {"FINISHED"}


@enumitems.stable
def _anim_items(self, context):
    obj = context.active_object
    names = list(obj.get("bz_anims", [])) if obj else []
    return [(str(i), n, f"Bake '{n}' onto the rig") for i, n in enumerate(names)] \
        or [("-1", "(no animations)", "")]


class BZ_OT_load_anim(bpy.types.Operator):
    """Bake an IC animation onto the rig (keyframes); scrub the timeline to play it"""
    bl_idname = "bz.load_anim"
    bl_label = "Load Animation"
    bl_options = {"REGISTER", "UNDO"}

    anim: bpy.props.EnumProperty(name="Animation", items=_anim_items)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and (store.is_native(obj) or obj.get("bz_scene")) and obj.get("bz_rig") and obj.get("bz_anims")

    def execute(self, context):
        obj = context.active_object
        arm = store.rig(obj)
        idx = int(self.anim)
        if arm is None or idx < 0:
            self.report({"ERROR"}, "No rig / animation")
            return {"CANCELLED"}
        from . import actions as bzactions
        names = list(obj.get("bz_anims", []))
        clip_name = names[idx] if idx < len(names) else ""
        pairs = {str(c): str(a) for c, a in (arm.get(bzactions.KEY_CLIPS, []) or [])}
        mine = bzactions.actions_of(arm)
        act = (next((a for a in mine if str(a.get(bzactions.KEY_CLIP, "")) == clip_name), None)
               or next((a for a in mine if a.name in (pairs.get(clip_name, ""), clip_name)), None))
        scene = context.scene
        if act is not None:                                 # the clip already is an Action: activate it
            bzactions._assign(arm, act)
            start, end = bzactions.frame_range(act)
            scene.frame_start, scene.frame_end = start, end
            fps = int(act.get(bzactions.KEY_FPS, 0) or 0)
            if fps:
                scene.render.fps = fps
            scene.frame_set(start)
            self.report({"INFO"}, f"Active action '{act.name}' ({end - start + 1} frames): Space plays; "
                                  "Select Rig to see its keyframes")
            return {"FINISHED"}
        try:
            if obj.get("bz_scene"):
                data = core.load_scene(obj.get("bz_source", ""))
                nframes = rig.apply_scene_animation(arm, data, idx, frame_start=1)
                clip = data.animations[idx]
            else:
                model = core.read_source(store.source_path(obj))
                nframes = rig.apply_animation(arm, model, idx, frame_start=1)
                clip = model.animations[idx]
        except Exception as e:
            self.report({"ERROR"}, f"Animation failed: {e}")
            return {"CANCELLED"}
        scene.frame_start = 1
        scene.frame_end = nframes
        fps = int(getattr(clip, "fps", 0) or 0)
        if fps:
            scene.render.fps = fps
        scene.frame_set(1)
        self.report({"INFO"}, f"Baked '{clip.name}' ({nframes} frames)")
        return {"FINISHED"}


def _dropped_text(geo) -> str:
    """", 11 damage states dropped from 1 edited part" when the export left any out, plus what was written of the others
    (", damage states: 10 kept, 1 re-encoded"), else ""."""
    n = sum(geo.damage_states_dropped.values())
    parts = len(geo.damage_states_dropped)
    out = f", {n} damage states dropped from {parts} edited part{'s' if parts != 1 else ''}" if n else ""
    tot = {k: sum(r.get(k, 0) for r in getattr(geo, "morphs", {}).values()) for k in ("kept", "encoded", "added", "removed")}
    words = [f"{v} {'re-encoded' if k == 'encoded' else k}" for k, v in tot.items() if v and not (k == "removed" and n)]
    return out + (", damage states: " + ", ".join(words) if words else "")


def _parts_text(geo) -> str:
    """"; parts: 1 added, 2 removed" when the export added or removed parts, else ""."""
    words = [f"{len(v)} {w}" for v, w in ((geo.parts_added, "added"), (geo.parts_removed, "removed")) if v]
    return ("; parts: " + ", ".join(words) + (" (put into a part group)" if geo.converted else "")) if words else ""


def _markers_text(geo) -> str:
    """"; markers: 1 moved, 1 added" when the export changed any attach point, else ""."""
    parts = [f"{len(v)} {w}" for v, w in ((geo.markers_changed, "edited"), (geo.markers_added, "added"),
                                            (geo.markers_removed, "deleted")) if v]
    return ("; markers: " + ", ".join(parts)) if parts else ""


def _game_folder_refusal(filepath, data_dir=None) -> str:
    """Why an export of ``filepath`` (and its texture folder ``data_dir``) must not happen -- it would write into the game's own folder --, or ''.
    The same check Export Entity Folder makes."""
    from .sceneobj import panels_blueprint as PB
    here = os.path.dirname(os.path.abspath(bpy.path.abspath(str(filepath))))
    for d in (here, data_dir):
        if d:
            try:
                PB.check_target(d)
            except PB.Refused as e:
                return str(e)
    return ""


class BZ_OT_export_scene(bpy.types.Operator, ExportHelper):
    """Export the object to .sgm with edited parts and clips; the rest is unchanged"""
    bl_idname = "bz.export_scene"
    bl_label = "Export Scene Object (.sgm)"

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm", options={"HIDDEN"})
    rebuild_lod: BoolProperty(
        name="Let the engine rebuild the detail ladder",
        description="Write the older layout and let the game rebuild distance detail. Off may not load in the game",
        default=True)
    drop_damage_states: BoolProperty(
        name="Leave damage states out of edited parts",
        description="Write edited parts without their damage states (shape keys) instead of with them",
        default=False)
    carry_over_damage: BoolProperty(
        name="Carry over damage states",
        description="Copy the source's damage states onto the new mesh of an edited part that has none",
        default=False)
    remove_deleted_parts: BoolProperty(
        name="Remove parts deleted in Blender",
        description="Remove a part from the model when its object is deleted, instead of keeping its source geometry",
        default=False)

    export_anyway: BoolProperty(
        name="Export anyway",
        description="Write the file even if the checks find an error the game would refuse (the error is kept as a note)",
        default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and bool(obj.get("bz_scene")) and bool(obj.get("bz_rig"))

    def execute(self, context):
        from . import actions as bzactions
        from . import scene as bzscene
        from ICParse.sgm.scene import model as SM
        obj = context.active_object
        src = obj.get("bz_source", "")
        arm = store.rig(obj)
        if not src or arm is None:
            self.report({"ERROR"}, "No source .sgm / rig recorded on this object")
            return {"CANCELLED"}
        from . import scenesafety
        changed = scenesafety.source_error(obj)
        if changed:
            self.report({"ERROR"}, changed)
            return {"CANCELLED"}
        try:
            from . import scene_skeleton
            out, geo, plan = scene_skeleton.export_with_clips(context, obj, arm, self.rebuild_lod, self.drop_damage_states,
                                                         carry_over=self.carry_over_damage,
                                                         remove_missing=self.remove_deleted_parts,
                                                         refuse_errors=not self.export_anyway)
            unchanged, encode, drop = plan
            geo.notes = scenesafety.clip_blueprint_notes(obj, plan) + scene_skeleton.sgm_only_notes(obj, self.filepath, geo.notes + plan.warnings)
            in_place = scenesafety.guard_write(obj, self.filepath, out, geo, plan)      # onto the model's own file: only what the session can follow
        except scenesafety.InPlaceRefused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, f"Export failed: {e}" + scenesafety.anyway_hint(e))
            return {"CANCELLED"}
        data_dir = None
        if geo.files:                       # new texture files: they go into the Data folder the model is written into
            from . import scene_materials as bzmat
            data_dir = bzmat.data_folder_of(self.filepath)
            if data_dir is None:
                self.report({"ERROR"}, f"This model needs {len(geo.files)} new texture file(s). Export into a folder inside a Data folder, "
                                       f"or use Export Entity Folder. Nothing was written.")
                return {"CANCELLED"}
        why = _game_folder_refusal(self.filepath, data_dir)
        if why:
            self.report({"ERROR"}, why + " Nothing was written.")
            return {"CANCELLED"}
        try:
            if data_dir is not None:                # the textures first: a model that names a texture never lands without it
                bzmat.write_files(data_dir, geo.files)
            _atomic_write(self.filepath, out)
        except OSError as e:
            self.report({"ERROR"}, f"Export failed: cannot write {e.filename or self.filepath}: {e.strerror or e}. The model file was not changed.")
            return {"CANCELLED"}
        if data_dir is not None:
            self.report({"INFO"}, f"Wrote {len(geo.files)} texture file(s) into {os.path.join(data_dir, 'art', 'textures')}")
        if in_place:
            scenesafety.after_write(obj, self.filepath, arm, plan)           # the session now matches the file it was built on
            self.report({"INFO"}, "Written over the model's own source file; this session now matches it")
        for note in geo.notes[:4]:
            self.report({"WARNING"}, note)
        if len(geo.notes) > 4:
            print("Export notes:", *geo.notes, sep="\n  ")
            self.report({"WARNING"}, f"... and {len(geo.notes) - 4} more note(s), printed in the system console")
        self.report({"INFO"}, f"Wrote {len(out)} bytes to {self.filepath}; geometry: {len(geo.rebuilt)} part(s) rebuilt, "
                              f"{len(geo.kept)} unchanged" + _dropped_text(geo) + _parts_text(geo) + _markers_text(geo) + f"; clips: {len(unchanged)} kept, "
                              f"{len(encode)} written" + (f", {len(drop)} dropped" if drop else ""))
        return {"FINISHED"}


class BZ_OT_export_scene_geometry(bpy.types.Operator, ExportHelper):
    """Export only the edited geometry and skeleton to .sgm; the clips stay as in the source (following bone edits)"""
    bl_idname = "bz.export_scene_geometry"
    bl_label = "Export Scene Geometry (.sgm)"

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm", options={"HIDDEN"})
    rebuild_lod: BoolProperty(
        name="Let the engine rebuild the detail ladder",
        description="Write the older layout and let the game rebuild distance detail. Off may not load in the game",
        default=True)
    drop_damage_states: BoolProperty(
        name="Leave damage states out of edited parts",
        description="Write edited parts without their damage states (shape keys) instead of with them",
        default=False)
    carry_over_damage: BoolProperty(
        name="Carry over damage states",
        description="Copy the source's damage states onto the new mesh of an edited part that has none",
        default=False)
    remove_deleted_parts: BoolProperty(
        name="Remove parts deleted in Blender",
        description="Remove a part from the model when its object is deleted, instead of keeping its source geometry",
        default=False)

    export_anyway: BoolProperty(
        name="Export anyway",
        description="Write the file even if the checks find an error the game would refuse (the error is kept as a note)",
        default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and bool(obj.get("bz_scene")) and obj.get("bz_scene_part") is not None

    def execute(self, context):
        from . import scene as bzscene
        from . import scenesafety
        obj = context.active_object
        changed = scenesafety.source_error(obj)
        if changed:
            self.report({"ERROR"}, changed)
            return {"CANCELLED"}
        try:
            geo = bzscene.export_scene_object(obj, legacy=self.rebuild_lod, drop_morphs=self.drop_damage_states,
                                              carry_over=self.carry_over_damage, remove_missing=self.remove_deleted_parts,
                                              refuse_errors=not self.export_anyway)
            in_place = scenesafety.guard_write(obj, self.filepath, geo.data, geo, None)       # no clips are written, none are looked at
        except scenesafety.InPlaceRefused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except ValueError as e:
            self.report({"ERROR"}, str(e) + scenesafety.anyway_hint(e))
            return {"CANCELLED"}
        except Exception as e:                             # noqa: BLE001 -- surface anything else as UI
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}
        out = geo.data
        data_dir = None
        if geo.files:                       # new texture files: they go into the Data folder the model is written into
            from . import scene_materials as bzmat
            data_dir = bzmat.data_folder_of(self.filepath)
            if data_dir is None:
                self.report({"ERROR"}, f"This model needs {len(geo.files)} new texture file(s). Export into a folder inside a Data folder, "
                                       f"or use Export Entity Folder. Nothing was written.")
                return {"CANCELLED"}
        why = _game_folder_refusal(self.filepath, data_dir)
        if why:
            self.report({"ERROR"}, why + " Nothing was written.")
            return {"CANCELLED"}
        try:
            if data_dir is not None:                # the textures first: a model that names a texture never lands without it
                bzmat.write_files(data_dir, geo.files)
            _atomic_write(self.filepath, out)
        except OSError as e:
            self.report({"ERROR"}, f"Export failed: cannot write {e.filename or self.filepath}: {e.strerror or e}. The model file was not changed.")
            return {"CANCELLED"}
        if data_dir is not None:
            self.report({"INFO"}, f"Wrote {len(geo.files)} texture file(s) into {os.path.join(data_dir, 'art', 'textures')}")
        if in_place:
            scenesafety.after_write(obj, self.filepath)                       # the session now matches the file it was built on
            self.report({"INFO"}, "Written over the model's own source file; this session now matches it")
        for note in geo.notes[:4]:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"Wrote {len(out)} bytes to {self.filepath}: {len(geo.rebuilt)} part(s) rebuilt, "
                              f"{len(geo.kept)} unchanged" + _dropped_text(geo) + _parts_text(geo) + _markers_text(geo))
        return {"FINISHED"}


class BZ_OT_select_rig(bpy.types.Operator):
    """Select the rig, so the animation editors show its keyframes"""
    bl_idname = "bz.select_rig"
    bl_label = "Select Rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and (store.is_native(obj) or obj.get("bz_scene")) and obj.get("bz_rig")

    def execute(self, context):
        obj = context.active_object
        arm = store.rig(obj)
        if arm is None:
            self.report({"ERROR"}, "No rig")
            return {"CANCELLED"}
        if obj.mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass
        for o in context.view_layer.objects:
            o.select_set(False)
        arm.hide_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
        rig._select_all_bones(arm)                      # selected bones = channels shown in the editors
        return {"FINISHED"}


class BZ_OT_import_clips(bpy.types.Operator):
    """Bake every clip of the source file into an Action on the rig"""
    bl_idname = "bz.import_clips"
    bl_label = "Clips to Actions"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and (store.is_native(obj) or obj.get("bz_scene")) and obj.get("bz_rig") and obj.get("bz_anims")

    def execute(self, context):
        from . import actions as bzactions
        obj = context.active_object
        arm = store.rig(obj)
        if arm is None:
            self.report({"ERROR"}, "No rig")
            return {"CANCELLED"}
        try:
            if obj.get("bz_scene"):
                made = bzactions.import_scene_clips(arm, core.load_scene(obj.get("bz_source", "")))
            else:
                made = bzactions.import_creature_clips(arm, core.read_source(store.source_path(obj)))
        except Exception as e:
            self.report({"ERROR"}, f"Failed: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{len(made)} clips baked to Actions")
        return {"FINISHED"}


@persistent
def _on_undo_redo(*_args):
    """After an Undo / Redo (edit-mode steps restore the mesh but not the object's tables): re-select the tables the
    journal recorded for each creature's mesh generation, and drop every cached draw / poll state."""
    for obj in bpy.data.objects:
        if store.is_native(obj):
            try:
                err = store.sync(obj)
            except Exception as e:                       # never silent: say which object could not be synced
                err = f"{type(e).__name__}: {e}"
            if err:
                print(f"[bz] {obj.name}: {err}")
    store.forget_cache()
    _OPEN_SIDES.clear()
    forget_ring_status()
    forget_skin_stats()
    draw.invalidate()


@persistent
def _on_save_pre(*_args):
    """Before a .blend is written: keep the creature skin images (a fake user each) and re-pack painted ones, so a
    reopened file still exports its paint (material.keep_skin_images)."""
    try:
        n = material.keep_skin_images()
    except Exception as e:                               # never silent: the saved file may lack the paint
        print(f"[bz] skin images could not be kept for the save: {type(e).__name__}: {e}")
        return
    if n:
        print(f"[bz] re-packed {n} painted skin image(s) into the .blend")


_HANDLERS = (("undo_post", _on_undo_redo), ("redo_post", _on_undo_redo), ("load_post", _on_undo_redo),
             ("save_pre", _on_save_pre))


def register_handlers():
    for lst_name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, lst_name)
        if fn not in lst:
            lst.append(fn)


def unregister_handlers():
    for lst_name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, lst_name)
        for h in [h for h in lst if getattr(h, "__name__", "") == fn.__name__
                  and getattr(h, "__module__", "") == fn.__module__]:
            lst.remove(h)


def menu_import(self, context):
    self.layout.operator(BZ_OT_import.bl_idname, text="Impossible Creatures Model (.sgm / .ebp)")


def menu_export(self, context):
    self.layout.operator(BZ_OT_export.bl_idname, text="Impossible Creatures Creature (.sgm)")
    self.layout.operator(BZ_OT_export_scene.bl_idname, text="Impossible Creatures Scene Object (.sgm)")
    self.layout.operator(BZ_OT_export_scene_geometry.bl_idname, text="Impossible Creatures Scene Geometry (.sgm)")


CLASSES = (BZ_OT_import, BZ_OT_export, BZ_OT_export_scene, BZ_OT_export_scene_geometry, BZ_OT_bake, BZ_OT_select_kind, BZ_OT_load_anim, BZ_OT_import_clips, BZ_OT_select_rig,
           BZ_OT_select_limb, BZ_OT_paint_skin, BZ_OT_assign_limb, BZ_OT_set_connection_ring, BZ_OT_pick_ring,
           BZ_OT_set_connection_bone, BZ_OT_edit_uvs, BZ_OT_apply_uvs,
           BZ_OT_assign_bone, BZ_OT_extrude_patch, BZ_OT_convert_mesh, BZ_OT_skin_assign_active,
           BZ_OT_skin_select_points, BZ_OT_skin_remove, BZ_OT_skin_select_unassigned, BZ_OT_skin_auto, BZ_OT_pick_bone)
