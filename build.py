"""Build the editable control-point mesh object from a loaded creature.

The object's vertices are the bicubic control points, 1:1 with the file's pool and in the
same index order. Edges are the 4x4 control net; there are NO faces -- the visible surface
is the gpu overlay (draw.py), never Blender mesh shading, so WYSIWYG stays honest.
"""
from __future__ import annotations

import bpy

from . import store
from . import material
from . import rig


def build_object(context, data, model=None, clips_as_actions=True):
    """Create and link the control-net mesh object; return it.

    If ``model`` (the parsed SgmModel) is given, decode its textures and material table so
    the textured bake / future overlay can show the real creature."""
    # Verts + one corner quad per patch (patchsel.face_rows: Face select / hide / delete a patch, occlusion for box
    # select); no other edges -- the control net is drawn as a toggleable gpu overlay (draw.py), not as mesh edges
    # (which Blender always renders and can't hide per-object). The quads are never shaded (Wire display).
    from . import patchsel
    me = bpy.data.meshes.new(data.name)
    _rows, quads = patchsel.face_quads(data.patches)
    me.from_pydata([tuple(v) for v in data.verts.tolist()], [], [tuple(int(i) for i in q) for q in quads])
    me.update()

    obj = bpy.data.objects.new(data.name, me)
    context.collection.objects.link(obj)
    store.tag(obj, data)

    if model is not None:
        # every step that can't be set up is recorded on the object (and reported by Import / Export), never
        # swallowed: a missing table would otherwise read as "no limbs" / "no UVs" and silently change the export
        try:
            images = material.decode_textures(model)         # list, one per texture index
            name_map = material.first_image_by_name(model, images)   # material lookup: first wins
            table = material.material_table(model)
            store.set_appearance(obj, table, {n: img.name for n, img in name_map.items()},
                                 list(model.material_index))
            store.set_texture_images(
                obj, [img.name if img is not None else "" for img in images])
            store.stamp_images(obj, images)
        except Exception as e:
            store.record_build_error(obj, "textures and materials", e)
        try:
            store.set_limbs(obj, model, len(data.patches))
        except Exception as e:
            store.record_build_error(obj, "limbs and connections", e)
        try:
            from ICParse.codecs import reconstruct as _R
            store.set_vert_bone(obj, _R.vertex_bone_owners(model))
            obj[store.KEY_BONE_NAMES] = [b.name or f"bone{i}" for i, b in enumerate(model.bones)]
        except Exception as e:
            store.record_build_error(obj, "skinning (bone owner per control point)", e)

    # Creatures are bilaterally symmetric: Blender's position-based X-mirror lets edits on one
    # side drive the other for free (partners found spatially, so partial symmetry is fine).
    me.use_mirror_x = True
    # the interior points are automatic (Max's Auto Interior, patchrules): hidden unless the scene shows them
    from . import patchfeel
    patchfeel.apply_interior_visibility(obj, patches=data.patches)

    # Show the control net as a wire over the (gpu-drawn) surface; keep it selectable.
    obj.display_type = "WIRE"
    obj.show_all_edges = True

    # Native rig: import the skeleton as an armature and rigid-skin the control points, so the
    # creature can be posed/animated with the bicubic surface following (export stays at rest).
    arm_obj = None
    if model is not None:
        try:
            arm_obj = rig.build_armature(context, obj, model)
            if arm_obj is not None:
                obj["bz_rig"] = arm_obj.name
                obj[store.KEY_BONE_NAMES] = rig.source_bone_names(arm_obj, model)   # the names Blender gave
                obj["bz_anims"] = [a.name or f"anim{i}" for i, a in enumerate(model.animations)]
                if clips_as_actions and model.animations:
                    from . import actions
                    try:
                        actions.import_creature_clips(arm_obj, model)
                    except Exception as e:
                        obj["bz_actions_error"] = str(e)
        except Exception as e:
            obj["bz_rig_error"] = str(e)               # visible instead of silent
            arm_obj = None

    for o in context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj
