"""sceneobj.ops_map_cinematics -- the operators of the Cinematics panel: add and delete animations, add / delete / move / aim key frames,
copy a view or the 3D cursor into a key, set the cinematic 1.85 picture shape, preview an animation, check the cinematics.

Everything works on the empties and collections that ``map_cinematics`` builds; saving the map is what writes ``<name>.na``.
"""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ICParse.sgb import nislet as N

from . import map_cinematics as C
from . import map_hooks as H
from . import ops_map_export as X
from . import ops_map_import as M

SANE_ID = 100000                   # a few stock animations carry a garbage id (1718969480 in mission1): a new number is counted up from the real ones
ASPECT = 1.85                      # the Mission Editor's cinematic picture shape (width / height)
KIND_ITEMS = [("camera", "Camera", "A camera that flies along a path"), ("obj", "Object", "An object that flies along a path"),
              ("move", "Move", "A creature or object that moves along a path")]


def _top(context):
    return X.active_map(context)


def active_anim(context):
    """``(top, root, anim collection)``; the collection is None when none is chosen."""
    top = _top(context)
    if top is None:
        return None, None, None
    root = C.root_of(top)                            # (poll and draw may not create a collection; the operators that add one do)
    if root is None:
        return top, None, None
    obj = context.active_object
    coll = C.anim_of_object(obj) if obj is not None else None
    if coll is not None and coll.name in {c.name for c in C.anim_collections(root)}:
        return top, root, coll
    coll = bpy.data.collections.get(root.get(C.K_ACTIVE, ""))
    if coll is not None and coll.get(C.K_ANIM) and coll.name in {c.name for c in C.anim_collections(root)}:
        return top, root, coll
    colls = C.anim_collections(root)
    return top, root, (colls[0] if colls else None)


def active_key(context, coll):
    o = context.active_object
    return o if o is not None and o.get(C.K_KEY) and coll is not None and o.name in coll.objects else None


def _renumber(coll) -> list:
    keys = C.key_objects(coll)
    base = coll.get("nis_name", "anim")
    for i, o in enumerate(keys):
        o["nis_index"] = float(i)
        o.name = f"{base} key {i + 1}"
    return keys


def _new_key_object(coll, kind: str, k: N.Key, index: float):
    o = C._empty("key", coll)
    o[C.K_KEY] = True
    o["nis_index"] = float(index)
    C.set_key_values(o, k, kind)
    return o


def _select(context, obj) -> None:
    for o in context.view_layer.objects:
        o.select_set(False)
    if obj is not None:
        obj.select_set(True)
        context.view_layer.objects.active = obj
        coll = C.anim_of_object(obj)
        parent = next((p for p in bpy.data.collections if coll is not None and coll.name in p.children and p.get(C.K_ROOT)), None)
        if parent is not None:
            parent[C.K_ACTIVE] = coll.name                # (poll and draw may not write; operators do)


def _cursor_game(context) -> tuple:
    return C.b2g(context.scene.cursor.location)


def _finish(context, top, coll, select=None) -> None:
    context.view_layer.update()
    C.refresh_shapes(top, coll)
    if select is not None:
        _select(context, select)


class _NisOp:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _top(context) is not None


class BZ_OT_nis_select_anim(_NisOp, bpy.types.Operator):
    """Show this animation in the panel and select its first key frame"""
    bl_idname = "bz.nis_select_anim"
    bl_label = "Show Animation"
    name: StringProperty()

    def execute(self, context):
        top = _top(context)
        root = C.root_of(top, create=True)
        coll = bpy.data.collections.get(self.name)
        if coll is None or not coll.get(C.K_ANIM):
            return {"CANCELLED"}
        root[C.K_ACTIVE] = coll.name
        keys = C.key_objects(coll)
        _select(context, keys[0] if keys else None)
        return {"FINISHED"}


class BZ_OT_nis_select_key(_NisOp, bpy.types.Operator):
    """Select this key frame"""
    bl_idname = "bz.nis_select_key"
    bl_label = "Select Key Frame"
    name: StringProperty()

    def execute(self, context):
        o = bpy.data.objects.get(self.name)
        if o is None:
            return {"CANCELLED"}
        _select(context, o)
        return {"FINISHED"}


class BZ_OT_nis_new_anim(_NisOp, bpy.types.Operator):
    """Add a new animation of this kind: two key frames, the first at the 3D cursor and the second 30 m beside it"""
    bl_idname = "bz.nis_new_anim"
    bl_label = "New Animation"
    kind: EnumProperty(items=KIND_ITEMS, name="Kind", default="camera")

    def execute(self, context):
        top = _top(context)
        root = C.root_of(top, create=True)
        names = {str(c.get("nis_name")) for c in C.anim_collections(root)}
        ids = [int(c.get("nis_id", 0)) for c in C.anim_collections(root) if c.get("nis_kind") == self.kind]
        mapname = top.name[4:] if top.name.startswith("Map ") else top.name
        n = 1
        while f"{mapname}_{N.CLASS_NAME[self.kind]}_{n}" in names:
            n += 1
        anim = N.Anim(kind=self.kind, id=max((i for i in ids if i < SANE_ID), default=-1) + 1, name=f"{mapname}_{N.CLASS_NAME[self.kind]}_{n}")
        x, y, z = _cursor_game(context)
        anim.keys = [N.Key(x=x, y=y, z=z), N.Key(x=x + 30.0, y=y, z=z)]
        if self.kind == "camera":
            for k in anim.keys:
                k.speed = 10.0
        coll = C.build_anim(context, top, root, anim, -1)
        root[C.K_ACTIVE] = coll.name
        _select(context, C.key_objects(coll)[0])
        self.report({"INFO"}, f"Added the {C.LABEL[self.kind].lower()} animation {anim.name} (number {anim.id}). Saving the map writes it into the map's cinematics file.")
        return {"FINISHED"}


class BZ_OT_nis_delete_anim(_NisOp, bpy.types.Operator):
    """Delete the shown animation and all its key frames"""
    bl_idname = "bz.nis_delete_anim"
    bl_label = "Delete Animation"

    @classmethod
    def poll(cls, context):
        return _top(context) is not None and active_anim(context)[2] is not None

    def execute(self, context):
        top, root, coll = active_anim(context)
        name = coll.get("nis_name", coll.name)
        for o in list(coll.objects):
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                (bpy.data.curves if isinstance(data, bpy.types.Curve) else bpy.data.cameras).remove(data)
        bpy.data.collections.remove(coll)
        rest = C.anim_collections(root)
        root[C.K_ACTIVE] = rest[0].name if rest else ""
        self.report({"INFO"}, f"Deleted the animation {name}. Triggers that play it will find nothing: check the triggers.")
        return {"FINISHED"}


class BZ_OT_nis_add_key(_NisOp, bpy.types.Operator):
    """Add a key frame: halfway between the selected key and the one before it, or at the 3D cursor after the selected key"""
    bl_idname = "bz.nis_add_key"
    bl_label = "Add Key Frame"
    where: EnumProperty(items=[("BEFORE", "Before", "Between the selected key and the one before it"),
                               ("CURSOR", "At cursor", "At the 3D cursor, after the selected key")], default="BEFORE")

    @classmethod
    def poll(cls, context):
        top, root, coll = active_anim(context) if _top(context) is not None else (None, None, None)
        return coll is not None

    def execute(self, context):
        top, root, coll = active_anim(context)
        kind = coll.get("nis_kind", "camera")
        keys = C.key_objects(coll)
        sel = active_key(context, coll)
        i = keys.index(sel) if sel in keys else len(keys) - 1
        src = C.key_from_object(keys[i], kind) if keys else N.Key()
        k = src.copy()
        k.src = None
        k.name = ""
        if self.where == "CURSOR" or not keys:
            k.x, k.y, k.z = _cursor_game(context)
            index = (keys[i].get("nis_index", 0.0) + 0.5) if keys else 0.0
        else:
            pos = C.key_positions(coll)
            if i > 0:
                k.x, k.y, k.z = N.spline_point_segment(pos, i - 1, 0.5)
                a = C.key_from_object(keys[i - 1], kind)
                k.speed = (a.speed + src.speed) / 2.0
                index = keys[i].get("nis_index", 0.0) - 0.5
            else:
                k.x, k.y, k.z = _cursor_game(context)
                index = keys[0].get("nis_index", 0.0) - 0.5
        o = _new_key_object(coll, kind, k, index)
        _renumber(coll)
        _finish(context, top, coll, select=o)
        return {"FINISHED"}


class _KeyOp(_NisOp):
    @classmethod
    def poll(cls, context):
        if _top(context) is None:
            return False
        _t, _r, coll = active_anim(context)
        return coll is not None and active_key(context, coll) is not None


class BZ_OT_nis_delete_key(_KeyOp, bpy.types.Operator):
    """Delete the selected key frame"""
    bl_idname = "bz.nis_delete_key"
    bl_label = "Delete Key Frame"

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        keys = C.key_objects(coll)
        i = keys.index(o)
        bpy.data.objects.remove(o, do_unlink=True)
        keys = _renumber(coll)
        _finish(context, top, coll, select=keys[min(i, len(keys) - 1)] if keys else None)
        return {"FINISHED"}


class BZ_OT_nis_move_key(_KeyOp, bpy.types.Operator):
    """Move the selected key frame earlier or later in the animation's order"""
    bl_idname = "bz.nis_move_key"
    bl_label = "Move Key Frame in Order"
    step: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        keys = C.key_objects(coll)
        i = keys.index(o)
        j = i + self.step
        if not 0 <= j < len(keys):
            return {"CANCELLED"}
        keys[i]["nis_index"], keys[j]["nis_index"] = float(j), float(i)
        _renumber(coll)
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_key_to_cursor(_KeyOp, bpy.types.Operator):
    """Put the selected key frame at the 3D cursor"""
    bl_idname = "bz.nis_key_to_cursor"
    bl_label = "Key to 3D Cursor"

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        o.location = context.scene.cursor.location
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_key_from_view(_KeyOp, bpy.types.Operator):
    """Copy a camera's view into the selected key frame: its direction and roll, and, for a camera animation, its place (the Mission
    Editor's Copy POV and Copy Camera). Uses the selected camera, else the scene's camera"""
    bl_idname = "bz.nis_key_from_view"
    bl_label = "Copy View from Camera"
    place: BoolProperty(name="Also copy the place", default=True)

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        cam = next((s for s in context.selected_objects if s.type == "CAMERA" and s is not o), None) or context.scene.camera
        if cam is None or cam.type != "CAMERA":
            self.report({"ERROR"}, "There is no camera: select one, or set the scene's camera.")
            return {"CANCELLED"}
        context.view_layer.update()
        m = cam.matrix_world
        rot, dec, roll = C.angles_of(m.to_3x3().normalized(), camera=coll.get("nis_kind", "camera") == "camera")
        o["nis_rotation"], o["nis_declination"], o["nis_roll"] = round(rot, 5), round(dec, 5), round(roll, 5)
        if self.place and coll.get("nis_kind") != "obj":
            o.location = m.translation
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_key_to_object(_KeyOp, bpy.types.Operator):
    """Put the selected key frame on the placed object that is selected too (the Mission Editor's Copy Entity)"""
    bl_idname = "bz.nis_key_to_object"
    bl_label = "Key to Selected Object"

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        other = next((s for s in context.selected_objects if s is not o and s.get(M.KEY_ENTITY) is not None), None)
        if other is None:
            self.report({"ERROR"}, "Select a placed object as well as the key frame.")
            return {"CANCELLED"}
        o.location = other.matrix_world.translation
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_key_target(_KeyOp, bpy.types.Operator):
    """Make the selected key frame of a camera animation look at the selected placed object, or at nothing"""
    bl_idname = "bz.nis_key_target"
    bl_label = "Set Look-at Target"
    clear: BoolProperty(default=False)

    def execute(self, context):
        top, root, coll = active_anim(context)
        o = active_key(context, coll)
        if coll.get("nis_kind") != "camera":
            self.report({"ERROR"}, "Only camera animations look at a target.")
            return {"CANCELLED"}
        if self.clear:
            o["nis_target_entity"] = N.NO_TARGET
            o["nis_target_group"] = N.NO_TARGET
        else:
            other = next((s for s in context.selected_objects if s is not o and s.get(M.KEY_ENTITY) is not None), None)
            if other is None:
                self.report({"ERROR"}, "Select a placed object as well as the key frame.")
                return {"CANCELLED"}
            o["nis_target_entity"] = int(other[M.KEY_ENTITY])
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_key_auto(_KeyOp, bpy.types.Operator):
    """Let the path decide this angle (the game points the view along the path: the value 36000)"""
    bl_idname = "bz.nis_key_auto"
    bl_label = "Angle from the Path"
    prop: EnumProperty(items=[("nis_rotation", "Rotation", ""), ("nis_declination", "Declination", "")])

    def execute(self, context):
        top, root, coll = active_anim(context)
        active_key(context, coll)[self.prop] = N.AUTO
        _finish(context, top, coll)
        return {"FINISHED"}


class BZ_OT_nis_refresh(_NisOp, bpy.types.Operator):
    """Draw the animation's path, speed line, key arrows and preview again from its key frames as they stand now"""
    bl_idname = "bz.nis_refresh"
    bl_label = "Update Path"

    @classmethod
    def poll(cls, context):
        return _top(context) is not None and active_anim(context)[2] is not None

    def execute(self, context):
        top, root, coll = active_anim(context)
        context.view_layer.update()
        C.refresh_shapes(top, coll)
        return {"FINISHED"}


class BZ_OT_nis_toggle_display(_NisOp, bpy.types.Operator):
    """Show or hide the animation's path, or its speed line (higher where the animation is faster)"""
    bl_idname = "bz.nis_toggle_display"
    bl_label = "Show Path"
    which: EnumProperty(items=[("path", "Path", ""), ("speed", "Speed", "")])

    def execute(self, context):
        top, root, coll = active_anim(context)
        if coll is None:
            return {"CANCELLED"}
        key = C.K_SHOW_PATH if self.which == "path" else C.K_SHOW_SPEED
        coll[key] = not bool(coll.get(key, key == C.K_SHOW_PATH))
        C.refresh_shapes(top, coll)
        return {"FINISHED"}


class BZ_OT_nis_preview(_NisOp, bpy.types.Operator):
    """Look through the animation's preview camera over its own frames: set the scene's camera and frame range to it"""
    bl_idname = "bz.nis_preview"
    bl_label = "Preview Animation"

    @classmethod
    def poll(cls, context):
        return _top(context) is not None and active_anim(context)[2] is not None

    def execute(self, context):
        top, root, coll = active_anim(context)
        frames = list(coll.get("nis_frames", []))
        pv = C.part_object(coll, "preview")
        if len(frames) != 2 or pv is None:
            self.report({"ERROR"}, "The animation needs at least 2 key frames to play.")
            return {"CANCELLED"}
        scene = context.scene
        if pv.type == "CAMERA":
            scene.camera = pv
        scene.frame_start, scene.frame_end = int(frames[0]), max(int(frames[1]), int(frames[0]) + 1)
        scene.frame_set(scene.frame_start)
        self.report({"INFO"}, f"Frames {scene.frame_start} to {scene.frame_end} are the animation (about {(frames[1] - frames[0]) / max(scene.render.fps, 1):.1f} s). "
                              "This is an approximation made for looking; the game plays the key values.")
        return {"FINISHED"}


class BZ_OT_nis_aspect(_NisOp, bpy.types.Operator):
    """Switch the picture shape between normal (4 : 3) and the cinematic 1.85 : 1 the game uses for cinematics (NISlets)"""
    bl_idname = "bz.nis_aspect"
    bl_label = "Cinematic 1.85 Picture"

    def execute(self, context):
        r = context.scene.render
        prev = context.scene.get("nis_prev_resolution")
        if prev is None:
            context.scene["nis_prev_resolution"] = [r.resolution_x, r.resolution_y]
            r.resolution_y = max(int(round(r.resolution_x / ASPECT)), 1)
            self.report({"INFO"}, f"The picture is now {r.resolution_x} x {r.resolution_y} (1.85 : 1), the shape of the game's cinematics. Press again to go back.")
        else:
            r.resolution_x, r.resolution_y = int(prev[0]), int(prev[1])
            del context.scene["nis_prev_resolution"]
            self.report({"INFO"}, f"The picture is back to {r.resolution_x} x {r.resolution_y}.")
        return {"FINISHED"}


class BZ_OT_nis_check(_NisOp, bpy.types.Operator):
    """Look for problems in the map's cinematics (a key looking at a deleted object, too few key frames, shared numbers)"""
    bl_idname = "bz.nis_check"
    bl_label = "Check Cinematics"
    bl_options = {"REGISTER"}

    def execute(self, context):
        top = _top(context)
        found = C.check(H.CheckContext(context=context, top=top, name=top.name, terrain=None))
        if not found:
            self.report({"INFO"}, "The cinematics look fine.")
        for p in found[:8]:
            self.report({"ERROR" if p["severity"] == "error" else "WARNING"}, p["text"])
        return {"FINISHED"}


OPERATORS = (BZ_OT_nis_select_anim, BZ_OT_nis_select_key, BZ_OT_nis_new_anim, BZ_OT_nis_delete_anim, BZ_OT_nis_add_key, BZ_OT_nis_delete_key, BZ_OT_nis_move_key,
             BZ_OT_nis_key_to_cursor, BZ_OT_nis_key_from_view, BZ_OT_nis_key_to_object, BZ_OT_nis_key_target, BZ_OT_nis_key_auto,
             BZ_OT_nis_refresh, BZ_OT_nis_toggle_display, BZ_OT_nis_preview, BZ_OT_nis_aspect, BZ_OT_nis_check)
CLASSES = OPERATORS
