"""Delete Bones, Merge Weights: the Edit Mode command for a scene object's rig (Armature menu in Edit Mode). The
selected bones are deleted and their vertex weights handed to each bone's parent first, so Blender shows what the
exported model does (a vertex group named after a deleted bone deforms nothing, and is not exported)."""
from __future__ import annotations

import bpy

from ..scene_skeleton import merge_weights_into_parent


class BZ_OT_scene_delete_bones_merge(bpy.types.Operator):
    """Delete the selected bones and give their vertex weights to each bone's parent"""
    bl_idname = "bz.scene_delete_bones_merge"
    bl_label = "Delete Bones, Merge Weights"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE" and obj.mode == "EDIT"

    def execute(self, context):
        arm = context.active_object
        names = [eb.name for eb in arm.data.edit_bones if eb.select]
        if not names:
            self.report({"WARNING"}, "Select the bones to delete first")
            return {"CANCELLED"}
        from ..rig import KEY_BONE_SRC_INDEX
        first = next((eb.name for eb in arm.data.edit_bones if eb.select and eb.get(KEY_BONE_SRC_INDEX) == 0), None)
        if first is not None:
            self.report({"ERROR"}, f"'{first}' is the model's first bone; it cannot be deleted (every clip is measured "
                                   "from it)")
            return {"CANCELLED"}
        bpy.ops.object.mode_set(mode="OBJECT")
        # deepest first, so a chain hands its weights up to the first bone that stays
        depth = {n: len(arm.data.bones[n].parent_recursive) for n in names}
        merged = merge_weights_into_parent(arm, sorted(names, key=lambda n: -depth[n]))
        moved = _markers_to_survivors(arm, names)
        bpy.ops.object.mode_set(mode="EDIT")
        for n in names:
            eb = arm.data.edit_bones.get(n)
            if eb is not None:
                arm.data.edit_bones.remove(eb)
        self.report({"INFO"}, f"Deleted {len(names)} bone(s); {merged} vertex group(s) merged into their parents"
                              + (f"; {len(moved)} marker(s) moved to the bone that stays" if moved else ""))
        return {"FINISHED"}



def _markers_to_survivors(arm, names) -> list:
    """Hang the markers of every bone in ``names`` on its nearest ancestor that stays (a deleted root's on the first bone),
    keeping their place: the bone ICParse ``skeleton.write_skeleton`` re-hangs them on. Returns the markers moved."""
    from ..rig import KEY_BONE_SRC_INDEX
    from . import slice_markers
    gone = set(names)
    first = next((b.name for b in arm.data.bones if b.get(KEY_BONE_SRC_INDEX) == 0), "")
    moved = []
    for n in names:
        b = arm.data.bones.get(n)
        to = b.parent if b is not None else None
        while to is not None and to.name in gone:
            to = to.parent
        moved += slice_markers.markers_off_bone(arm, n, to.name if to is not None else first)
    return moved


def _menu(self, context):
    self.layout.operator(BZ_OT_scene_delete_bones_merge.bl_idname)


CLASSES = (BZ_OT_scene_delete_bones_merge,)


def register():
    bpy.types.VIEW3D_MT_edit_armature.append(_menu)


def unregister():
    bpy.types.VIEW3D_MT_edit_armature.remove(_menu)
