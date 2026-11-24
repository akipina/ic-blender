"""sceneobj.panels_map_cinematics -- the Cinematics sub-panel of the Map panel: the map's camera, object and move animations (NISlets),
their key frames and the values of the selected key frame."""
from __future__ import annotations

import bpy

from . import map_cinematics as C
from . import map_hooks as H
from . import ops_map_cinematics as O
from . import ui_kit
from ICParse.sgb import nislet as N

ORDER = 63

KIND_ICON = {"camera": "CAMERA_DATA", "obj": "OBJECT_DATA", "move": "CON_FOLLOWPATH"}
KIND_HELP = {
    "camera": "A camera animation flies the game's camera along its path.",
    "obj": "An object animation moves objects along its path.",
    "move": "A move animation moves a creature or object along its path, turning it by the key frames.",
}
# the values a key frame has, per kind, in the order they are shown (the Mission Editor's Keyframe Attributes)
KEY_VALUES = {
    "camera": ("nis_name", "nis_rotation", "nis_declination", "nis_roll", "nis_look_ahead", "nis_speed", "nis_tension", "nis_continuity",
               "nis_bias", "nis_target_entity", "nis_target_group", "nis_zoom"),
    "move": ("nis_name", "nis_rotation", "nis_declination", "nis_roll", "nis_look_ahead", "nis_speed", "nis_tension", "nis_continuity", "nis_bias"),
    "obj": ("nis_name", "nis_speed", "nis_tension", "nis_continuity", "nis_bias"),
}


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_cinematics(layout, context, top) -> None:
    root = C.root_of(top)
    if root is None:
        layout.operator("bz.nis_new_anim", text="New Camera Animation", icon="ADD").kind = "camera"
        return
    if root.get(C.K_BAD):
        _hint(layout, "The cinematics file beside this map could not be read, so it is left as it is and not saved again.", "ERROR")
        return
    _top, _root, active = O.active_anim(context)
    colls = C.anim_collections(root)
    if not colls:
        _hint(layout, "This map has no cinematics yet.", "INFO")
    for kind in N.KINDS:
        mine = [c for c in colls if c.get("nis_kind") == kind]
        if not mine:
            continue
        box = layout.box()
        box.label(text=f"{N.KIND_LABEL[kind]} animations", icon=KIND_ICON[kind])
        col = box.column(align=True)
        for c in mine:
            row = col.row(align=True)
            op = row.operator("bz.nis_select_anim", text=f"{c.get('nis_id', 0)}  {c.get('nis_name', c.name)}", depress=(c is active))
            op.name = c.name
            row.label(text=f"{len(C.key_objects(c))} keys")
    row = layout.row(align=True)
    for kind, text in (("camera", "Camera"), ("obj", "Object"), ("move", "Move")):
        row.operator("bz.nis_new_anim", text=f"New {text}", icon="ADD").kind = kind
    if active is None:
        return
    kind = active.get("nis_kind", "camera")
    box = layout.box()
    box.label(text=active.get("nis_name", active.name), icon=KIND_ICON[kind])
    box.prop(active, '["nis_name"]', text="Name")
    box.label(text=f"Number {active.get('nis_id', 0)} (the triggers use it)")
    if kind != "obj":
        row = box.row(align=True)
        row.prop(active, '["nis_blend_in"]', text="Blend in")
        row.prop(active, '["nis_blend_out"]', text="Blend out")
    row = box.row(align=True)
    row.operator("bz.nis_toggle_display", text="Always show", icon="HIDE_OFF", depress=bool(active.get(C.K_SHOW_PATH, True))).which = "path"
    row.operator("bz.nis_toggle_display", text="Show speed", icon="DRIVER", depress=bool(active.get(C.K_SHOW_SPEED, False))).which = "speed"
    row = box.row(align=True)
    row.operator("bz.nis_preview", text="Preview", icon="PLAY")
    row.operator("bz.nis_refresh", text="Update path", icon="FILE_REFRESH")
    row.operator("bz.nis_delete_anim", text="", icon="TRASH")
    # ---- key frames
    keys = C.key_objects(active)
    kb = layout.box()
    kb.label(text=f"Key frames ({len(keys)})", icon="KEYFRAME")
    sel = O.active_key(context, active)
    col = kb.column(align=True)
    for i, o in enumerate(keys):
        row = col.row(align=True)
        label = f"{i + 1}  {o.get('nis_name', '')}".rstrip()
        row.operator("bz.nis_select_key", text=label, depress=(o is sel)).name = o.name
        row.label(text=f"speed {o.get('nis_speed', 0):g}")
    row = kb.row(align=True)
    row.operator("bz.nis_add_key", text="Add before", icon="ADD").where = "BEFORE"
    row.operator("bz.nis_add_key", text="Add at cursor", icon="CURSOR").where = "CURSOR"
    row.operator("bz.nis_delete_key", text="", icon="X")
    row = kb.row(align=True)
    op = row.operator("bz.nis_move_key", text="Earlier", icon="TRIA_UP")
    op.step = -1
    op = row.operator("bz.nis_move_key", text="Later", icon="TRIA_DOWN")
    op.step = 1
    row = kb.row(align=True)
    row.operator("bz.nis_key_to_cursor", text="To cursor", icon="CURSOR")
    row.operator("bz.nis_key_to_object", text="To object", icon="SNAP_ON")
    if kind != "obj":
        kb.operator("bz.nis_key_from_view", text="Copy view from camera" if kind == "camera" else "Copy direction from camera", icon="CAMERA_DATA")
    if sel is None:
        _hint(kb, "Select a key frame (its empty in the 3D view, or a row above) to edit its values.", "INFO")
    else:
        vb = layout.box()
        vb.label(text=f"Key frame {keys.index(sel) + 1}", icon="EMPTY_ARROWS")
        col = vb.column(align=True)
        for prop in KEY_VALUES[kind]:
            if prop not in sel:
                continue
            row = col.row(align=True)
            row.prop(sel, f'["{prop}"]', text=C.PROP_LABELS[prop])
            if prop in ("nis_rotation", "nis_declination"):
                row.operator("bz.nis_key_auto", text="Auto").prop = prop
        if kind == "camera":
            row = vb.row(align=True)
            row.operator("bz.nis_key_target", text="Look at selected", icon="RESTRICT_SELECT_OFF").clear = False
            row.operator("bz.nis_key_target", text="No target", icon="X").clear = True
        _hint(vb, "Rotation and declination of 36000 mean the game points the view along the path. Declination is positive looking down. "
                  "Turn the key's arrows in the 3D view and saving reads the new angles.", width=44)
    layout.operator("bz.nis_aspect", text="Cinematic 1.85 picture", icon="FULLSCREEN_ENTER",
                    depress=bpy.context.scene.get("nis_prev_resolution") is not None)
    layout.operator("bz.nis_check", text="Check cinematics", icon="CHECKMARK")


class BZ_PT_map_cinematics(bpy.types.Panel):
    bl_label = "Cinematics"
    bl_idname = "BZ_PT_map_cinematics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"
    bl_parent_id = H.MAP_PANEL_ID
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return O.X.active_map(context) is not None

    def draw(self, context):
        top = O.X.active_map(context)
        if top is not None:
            draw_cinematics(self.layout, context, top)


CLASSES = (BZ_PT_map_cinematics,)
