"""sceneobj.ops_map_settings -- the operators of the Map settings panels: give a player a start, make the preview pictures, check the map
for problems and go to a problem."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import map_hooks as H
from . import map_settings as S
from . import ops_map_export as X
from . import ops_map_import as M

ORDER = 63


def _top(op, context):
    top = X.active_map(context)
    if top is None:
        op.report({"ERROR"}, "There is no map in this scene. Open one with File > Import > Impossible Creatures Map, or make one with "
                             "File > New > Impossible Creatures Map.")
    return top


class BZ_OT_map_add_start(bpy.types.Operator):
    """Give a player a start at the 3D cursor: a laboratory and three henchmen round it, all owned by that player"""
    bl_idname = "bz.map_add_start"
    bl_label = "Add Player Start"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(name="Player", min=0, max=7, default=0, description="The player's slot (0 is Player 1)")

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _top(self, context)
        if top is None:
            return {"CANCELLED"}
        cur = context.scene.cursor.location
        try:
            existing = S.starts_of(top).get(int(self.slot), [])
            made = S.place_start(context, top, int(self.slot), cur.x, cur.y)
        except Exception as exc:                          # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        note = f" (Player {self.slot + 1} already had {len(existing)} start{'s' if len(existing) != 1 else ''}.)" if existing else ""
        self.report({"INFO"}, f"Player {self.slot + 1} now starts at the 3D cursor: {len(made)} objects.{note}")
        return {"FINISHED"}


class BZ_OT_map_select_start(bpy.types.Operator):
    """Select a player's laboratory and bring it into view"""
    bl_idname = "bz.map_select_start"
    bl_label = "Show Start"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _top(self, context)
        if top is None:
            return {"CANCELLED"}
        labs = S.starts_of(top).get(int(self.slot), [])
        if not labs:
            self.report({"ERROR"}, f"Player {self.slot + 1} has no laboratory.")
            return {"CANCELLED"}
        _select(context, labs[0])
        return {"FINISHED"}


def _select(context, obj) -> None:
    try:
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:                                      # noqa: BLE001
        pass
    for o in context.view_layer.objects:
        o.select_set(False)
    try:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except Exception:                                      # noqa: BLE001
        return
    for area in context.window.screen.areas if context.window else ():
        if area.type == "VIEW_3D":
            for region in area.regions:
                if region.type == "WINDOW":
                    try:
                        with context.temp_override(window=context.window, area=area, region=region):
                            bpy.ops.view3d.view_selected()
                    except Exception:                      # noqa: BLE001
                        pass
            break


class BZ_OT_map_make_preview(bpy.types.Operator):
    """Make the map's icon (the picture beside the map list, 128 x 128) and its minimap (256 x 256) from its ground picture and water, the way the Mission Editor does"""
    bl_idname = "bz.map_make_preview"
    bl_label = "Make Preview Picture"
    bl_options = {"REGISTER", "UNDO"}

    method: EnumProperty(name="From", items=[("GAME", "The ground picture", "Like the Mission Editor: the map's ground picture with the water drawn on it"),
                                            ("BLENDER", "A render from Blender", "Render the ground as it is now (sculpted, painted) from above, with the water drawn on it")],
                         default="GAME")

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _top(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            res = S.make_preview(context, top, self.method)
        except Exception as exc:                          # noqa: BLE001
            self.report({"ERROR"}, f"The preview picture could not be made: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"The icon ({res['icon'][1]} x {res['icon'][0]}) and the minimap ({res['minimap'][1]} x {res['minimap'][0]}) are made "
                              f"from {res['where']}, with the water drawn in. Saving the map writes them beside it.")
        return {"FINISHED"}


class BZ_OT_map_check_problems(bpy.types.Operator):
    """List what is wrong with the map before saving: players without a start, objects outside the map, blueprints the game does not have, heights out of range"""
    bl_idname = "bz.map_check_problems"
    bl_label = "Check Map"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return X.active_map(context) is not None

    def execute(self, context):
        top = _top(self, context)
        if top is None:
            return {"CANCELLED"}
        try:
            found = H.run_checks(context, top)
        except Exception as exc:                          # noqa: BLE001
            self.report({"ERROR"}, f"The map could not be checked: {exc}")
            return {"CANCELLED"}
        S.PROBLEMS[top.name] = found
        errors = sum(1 for p in found if p["severity"] == H.ERROR)
        if not found:
            self.report({"INFO"}, "No problems found.")
        else:
            self.report({"WARNING" if errors else "INFO"}, f"{errors} errors and {len(found) - errors} warnings. They are listed in Map problems.")
        return {"FINISHED"}


class BZ_OT_map_locate_problem(bpy.types.Operator):
    """Go to the object a problem is about"""
    bl_idname = "bz.map_locate_problem"
    bl_label = "Locate"
    bl_options = {"REGISTER", "UNDO"}

    where: StringProperty(default="")

    def execute(self, context):
        obj = bpy.data.objects.get(self.where)
        if obj is None:
            self.report({"INFO"}, f"{self.where or 'This problem'} is not an object that can be shown.")
            return {"CANCELLED"}
        _select(context, obj)
        return {"FINISHED"}


CLASSES = (BZ_OT_map_add_start, BZ_OT_map_select_start, BZ_OT_map_make_preview, BZ_OT_map_check_problems, BZ_OT_map_locate_problem)
