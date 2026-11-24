"""sceneobj.ops_map_replay -- the operators of the Replay part of the Cinematics panel: find the map's cinematics, choose one, build its
replay on the timeline, clear it, look through its camera, open its event list.

The work is in ``map_replay``; these are thin wrappers that turn its errors into plain sentences.
"""
from __future__ import annotations

import bpy
from bpy.props import StringProperty

from . import map_replay as R
from . import ops_map_export as X

ORDER = 60


def _top(context):
    return X.active_map(context)


class _ReplayOp:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _top(context) is not None


class BZ_OT_replay_find(_ReplayOp, bpy.types.Operator):
    """Read the map's scripts and list its cinematics"""
    bl_idname = "bz.replay_find"
    bl_label = "Find the Cinematics"

    def execute(self, context):
        top = _top(context)
        try:
            found = R.cinematics(context, top, refresh=True)
        except (R.ReplayError, X.MapSaveError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, f"The map's cinematics could not be read: {exc}")
            return {"CANCELLED"}
        keys = [R._g(c, "key") for c in found]
        if keys and top.get(R.K_ACTIVE) not in keys:
            top[R.K_ACTIVE] = keys[0]
        self.report({"INFO"}, f"{len(found)} cinematics found." if found else "This map's scripts play no cinematics.")
        return {"FINISHED"}


class BZ_OT_replay_select(_ReplayOp, bpy.types.Operator):
    """Choose this cinematic"""
    bl_idname = "bz.replay_select"
    bl_label = "Choose Cinematic"
    key: StringProperty()

    def execute(self, context):
        _top(context)[R.K_ACTIVE] = self.key
        return {"FINISHED"}


class BZ_OT_replay_build(_ReplayOp, bpy.types.Operator):
    """Act the chosen cinematic out on the timeline: stand-ins for the objects it moves, a camera, markers. The map itself is not changed"""
    bl_idname = "bz.replay_build"
    bl_label = "Build Replay"

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and bool(top.get(R.K_ACTIVE))

    def execute(self, context):
        top = _top(context)
        try:
            rep = R.build_replay(context, top, str(top.get(R.K_ACTIVE)))
        except (R.ReplayError, X.MapSaveError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:                                  # noqa: BLE001
            self.report({"ERROR"}, f"The replay could not be built: {exc}")
            return {"CANCELLED"}
        q = rep["quality"]
        self.report({"INFO"}, f"Replay built in {rep['seconds']:.1f} s: {rep['stand_ins']} stand-ins, {rep['events']} events "
                              f"({q.get('exact', 0)} exact, {q.get('approximate', 0)} approximate, {q.get('skipped', 0)} skipped). "
                              "Press Space in the 3D view to play it.")
        return {"FINISHED"}


class BZ_OT_replay_clear(_ReplayOp, bpy.types.Operator):
    """Remove the replay and show the map's own objects again"""
    bl_idname = "bz.replay_clear"
    bl_label = "Clear Replay"

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and R.replay_of(top) is not None

    def execute(self, context):
        R.clear_replay(context, _top(context))
        self.report({"INFO"}, "The replay is cleared; the map's objects are shown again.")
        return {"FINISHED"}


class BZ_OT_replay_look(_ReplayOp, bpy.types.Operator):
    """Make the 3D view look through the scene camera (the replay camera, which switches to the cinematic's other cameras by itself)"""
    bl_idname = "bz.replay_look"
    bl_label = "Look Through the Camera"

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and R.replay_of(top) is not None

    def execute(self, context):
        coll = R.replay_of(_top(context))
        cam = R.camera_of(coll)
        scene = context.scene
        if cam is None and scene.camera is None:
            self.report({"WARNING"}, "This cinematic has no camera animation, so there is no camera to look through.")
            return {"CANCELLED"}
        if cam is not None:
            scene.camera = cam
        scene.frame_set(scene.frame_start)
        screen = context.screen
        for area in (screen.areas if screen is not None else []):
            if area.type == "VIEW_3D":
                space = area.spaces.active
                if space is not None and space.region_3d is not None:
                    space.region_3d.view_perspective = "CAMERA"
                break
        self.report({"INFO"}, "Looking through the camera. Press Space to play; the view follows the camera.")
        return {"FINISHED"}


class BZ_OT_replay_events(_ReplayOp, bpy.types.Operator):
    """Open the list of the replay's events (time, what happens, exact / approximate / skipped, why)"""
    bl_idname = "bz.replay_events"
    bl_label = "Open the Event List"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        top = _top(context)
        return top is not None and R.event_text(top) is not None

    def execute(self, context):
        text = R.event_text(_top(context))
        wm = context.window_manager
        for win in (wm.windows if wm is not None else []):
            for area in win.screen.areas:
                if area.type == "TEXT_EDITOR":
                    area.spaces.active.text = text
                    area.tag_redraw()
                    return {"FINISHED"}
        try:
            bpy.ops.wm.window_new()
            win = wm.windows[-1]
            area = win.screen.areas[0]
            area.type = "TEXT_EDITOR"
            area.spaces.active.text = text
            return {"FINISHED"}
        except Exception:                                         # noqa: BLE001 -- no window to open (background)
            self.report({"INFO"}, f"The list is the text called '{text.name}': open it in a Text Editor.")
            return {"FINISHED"}


CLASSES = (BZ_OT_replay_find, BZ_OT_replay_select, BZ_OT_replay_build, BZ_OT_replay_clear, BZ_OT_replay_look, BZ_OT_replay_events)
