"""sceneobj.panels_map_replay -- the Replay part of the Cinematics panel: the map's cinematics, Build / Clear, look through the camera, the
event list, and a short hint on how to watch it."""
from __future__ import annotations

import json

import bpy

from . import map_replay as R
from . import ops_map_replay as O
from . import ui_kit

ORDER = 64                       # after the Cinematics panel (BZ_PT_map_cinematics, ORDER 63): a child registers after its parent


def _hint(layout, text: str, icon: str = "NONE", width: int = 44) -> None:
    col = layout.column(align=True)
    for i, line in enumerate(ui_kit.wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def draw_replay(layout, context, top) -> None:
    _hint(layout, "Pick a cinematic, Build replay, then press Space to watch; the map is unchanged.", "INFO")
    cines = R.cached_cinematics(top)
    if cines is None:
        layout.operator("bz.replay_find", text="Find the cinematics", icon="VIEWZOOM")
    else:
        active = top.get(R.K_ACTIVE, "")
        if not cines:
            _hint(layout, "This map's scripts play no cinematics.", "INFO")
        box = layout.box()
        box.label(text=f"Cinematics ({len(cines)})", icon="SEQUENCE")
        col = box.column(align=True)
        for c in cines:
            key = str(R._g(c, "key", ""))
            op = col.operator("bz.replay_select", text=str(R._g(c, "title", key)), depress=(key == active))
            op.key = key
        row = layout.row(align=True)
        row.operator("bz.replay_build", text="Build replay", icon="PLAY")
        row.operator("bz.replay_clear", text="Clear replay", icon="X")
        row.operator("bz.replay_find", text="", icon="FILE_REFRESH")
    coll = R.replay_of(top)
    if coll is None:
        return
    try:
        rep = json.loads(coll.get("ic_replay_report", "{}"))
    except ValueError:
        rep = {}
    vb = layout.box()
    vb.label(text=str(coll.get(R.K_TITLE, "Replay")), icon="CAMERA_DATA")
    q = rep.get("quality", {})
    vb.label(text=f"{rep.get('stand_ins', 0)} stand-ins, {rep.get('events', 0)} events, built in {rep.get('seconds', 0):.1f} s")
    vb.label(text=f"{q.get('exact', 0)} exact, {q.get('approximate', 0)} approximate, {q.get('skipped', 0)} skipped")
    fr = rep.get("frames", [0, 0])
    vb.label(text=f"Frames {fr[0]} to {fr[1]} at {rep.get('fps', R.FPS)} a second")
    row = vb.row(align=True)
    row.operator("bz.replay_look", text="Look through the camera", icon="VIEW_CAMERA")
    row.operator("bz.replay_events", text="Event list", icon="TEXT")
    if cines is None:
        vb.operator("bz.replay_clear", text="Clear replay", icon="X")
    for n in rep.get("notes", [])[:3]:
        _hint(vb, n, "ERROR", 44)


class BZ_PT_map_replay(bpy.types.Panel):
    bl_label = "Replay"
    bl_idname = "BZ_PT_map_replay"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MAP
    bl_parent_id = "BZ_PT_map_cinematics"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return O.X.active_map(context) is not None

    def draw(self, context):
        top = O.X.active_map(context)
        if top is not None:
            draw_replay(self.layout, context, top)


CLASSES = (BZ_PT_map_replay,)
