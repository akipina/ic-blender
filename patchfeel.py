"""The Max patch feel, live in Blender: corners drag their handles, interiors are automatic, interior points hidden.

The rules are bpy-free in ``patchrules``; this module applies them to the live edit mesh:

* :func:`settle` -- called by the overlay's per-redraw diff (``draw._sync``) with the control points as they were at
  the last redraw and as they are now: the handles of moved corners are translated and the interiors of the touched
  patches recomputed IN THE BMESH before this redraw's tessellation, so a G drag shows the carried surface. Only
  points the rule owns are written (a handle or interior the transform moved itself is left alone), only patches with
  a change are looked at, and an Undo restoring the mesh is recognised as no change (nothing is rewritten, so the
  export stays byte-identical). Off for scene objects (they are not native creatures) and when the scene's
  ``bz_patch_rules`` is off (raw control-point editing).
* :func:`apply_interior_visibility` -- the interior points (interior in every patch that uses them: 4 per patch, 44 %
  of a stock pool) get the mesh vertex ``hide`` flag: Blender neither draws, selects nor transforms them, which is
  exactly Max, where interiors are not sub-objects. The flag lives in the mesh (edit-mode Undo and Tab keep it); the
  scene's ``bz_show_interiors`` reveals them for manual interiors. A hidden interior follows its frame corner in every
  selection the skin edits read (:func:`selection`).
* ``BZ_OT_reset_interiors`` -- put the interiors of the selected patches (all when nothing is selected) back on the
  rule and clear their manual flags (a journaled table edit).
"""
from __future__ import annotations

import numpy as np

import bpy

from . import patchrules as PR
from . import store

KEY_RULES = "bz_patch_rules"
KEY_SHOW_INTERIORS = "bz_show_interiors"
KEY_LOCK_HANDLES = "bz_lock_handles"

_anchors: dict = {}          # obj.name -> (anchor (N, 3), anchored (N,), rel (N, 3), rel_act (N,)): where moved / carried
#                              points started, and the handle directions of the Coplanar / Lock rule (patchrules)


def forget(name=None) -> None:
    """Drop the anchors (draw.invalidate: an Undo / Redo, an operator that rewrote the points)."""
    if name is None:
        _anchors.clear()
    else:
        _anchors.pop(name, None)


def enabled(scene=None) -> bool:
    """Whether the live rules run (the scene toggle; default on)."""
    scene = scene if scene is not None else bpy.context.scene
    return bool(getattr(scene, KEY_RULES, True)) if scene is not None else True


def interiors_shown(scene=None) -> bool:
    scene = scene if scene is not None else bpy.context.scene
    return bool(getattr(scene, KEY_SHOW_INTERIORS, False)) if scene is not None else False


def lock_handles(scene=None) -> bool:
    """Lock Handles (Max): a handle moved by itself turns the corner's other handles by the same rotation."""
    scene = scene if scene is not None else bpy.context.scene
    return bool(getattr(scene, KEY_LOCK_HANDLES, False)) if scene is not None else False


def _edit_verts(obj) -> np.ndarray:
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return np.array([v.co[:] for v in bm.verts], dtype=np.float64)


def settle(obj, prev, cur=None, patches=None, scene=None):
    """Apply the carry / auto-interior rules to the live edit mesh for the change ``prev`` -> ``cur`` ((N, 3) control
    points; ``cur`` defaults to the bmesh now). Returns the control points after the rule (as the float32 mesh holds
    them) when it wrote any, else None. Nothing happens outside Edit Mode, for a non-native object, with the rules
    off, or while the pool is not what the patch table indexes."""
    if not store.is_native(obj) or obj.mode != "EDIT" or not enabled(scene):
        return None
    cur = _edit_verts(obj) if cur is None else np.asarray(cur, dtype=np.float64)
    prev = np.asarray(prev, dtype=np.float64)
    if prev.shape != cur.shape:
        return None
    moved = np.any(prev != cur, axis=1)
    if not moved.any():
        return None
    patches = store.patches(obj) if patches is None else np.asarray(patches, dtype=np.int64)
    if not len(patches) or int(patches.max()) >= len(cur):
        return None
    anchor, anchored, rel, rel_act = _anchors.get(obj.name, (None, None, None, None))
    from . import patchsel
    res = PR.carry_handles(prev, cur, patches, moved, store.manual_interior(obj, len(patches)), anchor=anchor,
                           anchored=anchored, coplanar=patchsel.coplanar_flags(obj), lock=lock_handles(scene),
                           rel=rel, rel_act=rel_act)
    _anchors[obj.name] = (res.anchor, res.anchored, res.rel, res.rel_act)
    if len(res.manual_new):
        store.set_manual_interior(obj, res.manual)
    if not res.changed.any():
        return None
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    out = res.verts.astype(np.float32)
    for i in np.nonzero(res.changed)[0]:
        bm.verts[int(i)].co = out[i]
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return out.astype(np.float64)


# ------------------------------------------------------------------------------------------- interior visibility
def interior_mask(obj, patches=None) -> np.ndarray:
    """(N,) bool: the pool points that are interior in every patch using them (the ones the display hides)."""
    patches = store.patches(obj) if patches is None else patches
    return PR.roles(patches, store.point_count(obj)).interior


def hidden_points(obj) -> np.ndarray:
    """(N,) bool: the mesh's hidden vertices (the live bmesh in Edit Mode)."""
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        return np.fromiter((v.hide for v in bm.verts), dtype=bool, count=len(bm.verts))
    hid = np.zeros(len(obj.data.vertices), dtype=bool)
    obj.data.vertices.foreach_get("hide", hid)
    return hid


def apply_interior_visibility(obj, show=None, patches=None) -> int:
    """Hide the interior points (``show`` False; the default follows the scene toggle) or reveal them. A hidden point
    is also deselected (Blender never keeps hidden elements selected). Returns how many are hidden now."""
    if not store.is_native(obj):
        return 0
    show = interiors_shown() if show is None else bool(show)
    mask = interior_mask(obj, patches)
    idx = np.nonzero(mask)[0]
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for i in idx:
            v = bm.verts[int(i)]
            v.hide = not show
            if not show:
                v.select = False
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    else:
        me = obj.data
        n = len(me.vertices)
        if n != len(mask):
            return 0
        hid = np.zeros(n, dtype=bool)
        me.vertices.foreach_get("hide", hid)
        hid[mask] = not show
        me.vertices.foreach_set("hide", hid)
        if not show and len(idx):
            sel = np.zeros(n, dtype=bool)
            me.vertices.foreach_get("select", sel)
            sel[mask] = False
            me.vertices.foreach_set("select", sel)
        me.update()
    return 0 if show else int(len(idx))


def apply_interior_visibility_all(context) -> None:
    """The Show Interior Points toggle: every native creature of the scene."""
    for obj in context.scene.objects:
        if store.is_native(obj):
            apply_interior_visibility(obj)


def _owners(obj):
    """The live owners (the table read against the bone vertex groups), the table alone when they can't be read."""
    try:
        return store.live_skin(obj).owners
    except ValueError:
        return store.vert_bone(obj)


def _raw_selection(obj) -> np.ndarray:
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        return np.fromiter((v.select for v in bm.verts), dtype=bool, count=len(bm.verts))
    sel = np.zeros(len(obj.data.vertices), dtype=bool)
    obj.data.vertices.foreach_get("select", sel)
    return sel


def selection(obj, selected=None) -> np.ndarray:
    """The selection the skin edits use: the selected points, plus every hidden interior point whose frame corner is
    selected and skinned like it (``patchrules.follow_corners``). Edit Mode only (elsewhere the plain flags)."""
    selected = _raw_selection(obj) if selected is None else np.asarray(selected, dtype=bool)
    if obj.mode != "EDIT":
        return selected
    hid = hidden_points(obj)
    if not hid.any():
        return selected
    return PR.follow_corners(selected, hid, store.patches(obj), _owners(obj))


def selection_note(obj):
    """Why a skin edit on the selection does not cover every hidden interior of the selected corners (None when it
    does): the ones skinned to another bone than their corner stay as they are, and are named."""
    if obj.mode != "EDIT":
        return None
    hid = hidden_points(obj)
    if not hid.any():
        return None
    left = PR.unfollowed_interiors(_raw_selection(obj), hid, store.patches(obj), _owners(obj))
    if not len(left):
        return None
    shown = ", ".join(str(int(i)) for i in left[:6]) + (", ..." if len(left) > 6 else "")
    return (f"{len(left)} hidden interior point(s) of the selected corners are skinned to another bone than their "
            f"corner and were left as they are (points {shown}); Display > Show Interior Points to select them")


# ------------------------------------------------------------------------------------------- operator
class BZ_OT_reset_interiors(bpy.types.Operator):
    """Put the interior points of the selected patches (every patch touched by a selected point; all patches when
    nothing is selected) back on the Auto Interior rule and clear their Manual flag"""
    bl_idname = "bz.reset_interiors"
    bl_label = "Auto Interiors"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        from . import draw, ops
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        patches = store.patches(obj)
        sel = ops.point_selection(obj)
        rows = np.nonzero(sel[patches].any(axis=1))[0] if sel.any() else np.arange(len(patches))
        verts = _edit_verts(obj)
        out, rewritten = PR.reset_interiors(verts, patches, rows)
        manual = store.manual_interior(obj, len(patches))
        clear = int(manual[rows].sum())
        if not len(rewritten) and not clear:
            self.report({"INFO"}, f"{len(rows)} patch(es) already on the rule")
            return {"CANCELLED"}
        if not ops._begin(self, obj):
            return {"CANCELLED"}
        if clear:
            manual[rows] = False
            store.set_manual_interior(obj, manual)
        if len(rewritten):
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            out32 = out.astype(np.float32)
            for i in np.unique(patches[rewritten][:, list(PR.INTERIOR_SLOTS)]):
                bm.verts[int(i)].co = out32[i]
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Auto Interiors: {len(rewritten)} of {len(rows)} patch(es) rewritten"
                              + (f", {clear} manual flag(s) cleared" if clear else ""))
        return {"FINISHED"}


CLASSES = (BZ_OT_reset_interiors,)
