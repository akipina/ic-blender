"""Effect preview in Blender: the effects an event starts, baked onto the timeline (IC Game tab > Effects and events > Effect preview).

What it does: for the events of a clip, :mod:`fxbake` (no Blender) runs each effect through the effect simulator frame by frame, and this module
turns the result into meshes: one object per frame that has something to draw, named ``fxprev_<effect>_<frame>``, kept in the collection
``<name>_FXbake`` and shown for exactly its frame by hidden / shown keyframes, so playing, scrubbing and rendering the timeline show the effect with
no handler running. The effects are placed at the event's marker (a marker empty of the Markers panel, or a bone; a name that is neither is the
model's origin, as in the game) and follow it while it moves with the animation.

It is a preview, not the game's own picture: mesh particles show as flat pictures, a spray has no ground or wind, a trail follows the marker's own
path, and both branches of a conditional effect play (the game's code starts both); a combo or conditional ends its
spray, looping and trail children when its first child ends, which :mod:`fxbake` repeats.

Baked meshes are throw-away: Clear preview removes them, and nothing of them is exported (an export never reads this collection).
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

import bpy
from .sceneobj import ui_kit

from . import fxbake, fxcore, fxpanel
from .fxpanel import (_entity, _entity_poll, _event_poll, _game_dir, _hint, _source, _state, clip_names, clip_range, is_creature, items_of, load_events)

SUFFIX = "_FXbake"
KEY_BAKE = "bz_fx_bake"
APPROX = ("This is a preview, not the game's own picture: mesh particles show as flat pictures, a spray has no ground or wind, and a trail follows "
          "the marker's own path. Both parts of a conditional effect play, and a group of effects stops its sprays, loops and trails when its first effect "
          "has ended. Look at the real thing in the game.")


# ---------------------------------------------------------------------------------------------------------- what is on the timeline
def collection_name(ent) -> str:
    return f"{ent.holder.name}{SUFFIX}"


def baked_objects(ent) -> list:
    coll = bpy.data.collections.get(collection_name(ent))
    return list(coll.objects) if coll is not None else []


def clear(ent) -> int:
    """Remove the baked meshes of this thing; returns how many objects went."""
    coll = bpy.data.collections.get(collection_name(ent))
    if coll is None:
        return 0
    n = 0
    for o in list(coll.objects):
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
        n += 1
    bpy.data.collections.remove(coll)
    return n


# ---------------------------------------------------------------------------------------------------------- where the effect is placed
def _action_of_clip(ent, clip: str):
    """The Action that holds a clip's keys, or None."""
    if ent is None or ent.rig is None or not clip:
        return None
    from . import actions as bzactions
    for a in bzactions.actions_of(ent.rig):
        if clip.lower() in (str(a.get(bzactions.KEY_CLIP, "")).lower(), a.name.lower() if is_creature(ent) else ""):
            return a
    return None


def _marker_objects(ent) -> dict:
    out = {}
    try:
        from .sceneobj import slice_markers as SM
        key = SM.entity_key(ent.parts[0])
        if key is not None:
            for o in SM.markers_of(key):
                out.setdefault(SM.marker_name(o), o)
    except Exception:                                                     # noqa: BLE001 -- a creature has no markers, only bones
        pass
    return out


def _root_matrix(ent, depsgraph):
    holder = ent.rig if ent.rig is not None else ent.parts[0]
    return np.array(holder.evaluated_get(depsgraph).matrix_world)


def marker_matrix(ent, name: str, objects: dict, depsgraph):
    """The 4x4 world matrix of the marker or bone ``name`` on the frame the scene is at; the model's own matrix for any other name."""
    o = objects.get(name)
    if o is not None:
        return np.array(o.evaluated_get(depsgraph).matrix_world)
    if name and ent.rig is not None and name in ent.rig.pose.bones:
        rig = ent.rig.evaluated_get(depsgraph)
        return np.array(rig.matrix_world @ rig.pose.bones[name].matrix)
    return _root_matrix(ent, depsgraph)


def _camera_axes(context):
    """The (right, up) world axes billboards face: the scene camera's, else the 3D view's, else None (the front view)."""
    cam = context.scene.camera
    if cam is not None:
        m = np.array(cam.matrix_world)
        return tuple(m[:3, 0] / (np.linalg.norm(m[:3, 0]) or 1.0)), tuple(m[:3, 1] / (np.linalg.norm(m[:3, 1]) or 1.0))
    rv3d = getattr(context, "region_data", None)
    if rv3d is not None:
        m = np.array(rv3d.view_matrix.inverted())
        return tuple(m[:3, 0]), tuple(m[:3, 1])
    return None


# ---------------------------------------------------------------------------------------------------------- materials
def _picture_source(ref: str, source: str, game_dir: str):
    """``(path, bytes, tag)`` of an effect's picture (the project's file, else the game's); ``(path, None, "")`` when there is none. The tag names the
    path AND the contents, so two pictures with the same file name in different folders, and a project picture edited since the last preview, are
    different pictures to the cache."""
    rel = fxbake.texture_path(ref)
    if not rel:
        return "", None, ""
    blob = None
    files = fxcore.effect_files(source) if source else {}
    if rel in files:
        with open(files[rel], "rb") as f:
            blob = f.read()
    else:
        data = fxcore.game_data(game_dir)
        if data is not None:
            blob = data.read(rel)
    if not blob:
        return rel, None, ""
    return rel, blob, hashlib.md5(rel.encode()).hexdigest()[:6] + hashlib.md5(blob).hexdigest()[:8]


def _picture(ref: str, source: str, game_dir: str, found=None):
    """The Blender image of an effect's picture (the project's file, else the game's), or None."""
    rel, blob, tag = found if found is not None else _picture_source(ref, source, game_dir)
    if not blob:
        return None
    name = f"fxpic_{os.path.basename(rel)}_{tag}"
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    folder = os.path.join(bpy.app.tempdir or "/tmp", "bz_fx_pictures")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, tag + os.path.splitext(rel)[1])
    with open(path, "wb") as f:
        f.write(blob)
    try:
        img = bpy.data.images.load(path)
        img.name = name
        img.pack()
        return img
    except RuntimeError:
        return None


def material_for(texture: str, blend: str, source: str, game_dir: str):
    """A material that draws the effect's picture (bright on black: its brightness is how much of it shows) times the vertex colour, blended as the
    effect says (additive glows; alpha lets the colour's own alpha dim it)."""
    found = _picture_source(texture, source, game_dir)
    key = f"fxmat_{os.path.basename(found[0]) or 'plain'}_{found[2]}_{blend}"
    mat = bpy.data.materials.get(key)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(key)
    additive = blend == "add"
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(mat, attr, val)
        except (AttributeError, TypeError):
            pass
    try:
        mat.use_backface_culling = False
        mat.use_nodes = True
    except (AttributeError, TypeError):
        pass
    mat.diffuse_color = (1.0, 0.6, 0.2, 1.0)
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    emis = nt.nodes.new("ShaderNodeEmission")
    emis.inputs["Strength"].default_value = 2.5 if additive else 1.0
    vcol = nt.nodes.new("ShaderNodeVertexColor")
    vcol.layer_name = "Col"
    img = _picture(texture, source, game_dir, found)
    coverage = None
    colour = vcol.outputs["Color"]
    if img is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.extension = "CLIP"
        lum = nt.nodes.new("ShaderNodeRGBToBW")
        nt.links.new(tex.outputs["Color"], lum.inputs["Color"])
        coverage = lum.outputs["Val"]
        times = nt.nodes.new("ShaderNodeVectorMath")
        times.operation = "MULTIPLY"
        nt.links.new(tex.outputs["Color"], times.inputs[0])
        nt.links.new(vcol.outputs["Color"], times.inputs[1])
        colour = times.outputs["Vector"]
    nt.links.new(colour, emis.inputs["Color"])
    if additive:
        alpha = coverage if coverage is not None else vcol.outputs["Alpha"]
    else:
        amul = nt.nodes.new("ShaderNodeMath")
        amul.operation = "MULTIPLY"
        if coverage is not None:
            nt.links.new(coverage, amul.inputs[0])
        else:
            amul.inputs[0].default_value = 1.0
        nt.links.new(vcol.outputs["Alpha"], amul.inputs[1])
        alpha = amul.outputs[0]
    nt.links.new(alpha, mix.inputs["Fac"])
    nt.links.new(transp.outputs["BSDF"], mix.inputs[1])
    nt.links.new(emis.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat


# ---------------------------------------------------------------------------------------------------------- meshes
def _frame_mesh(name: str, parts: list, mats: list):
    """One mesh of the frame: ``parts`` is ``[(material slot, Geom)]``."""
    verts = np.concatenate([g.verts for _s, g in parts])
    uvs = np.concatenate([g.uvs for _s, g in parts])
    cols = np.concatenate([g.cols for _s, g in parts])
    nq = len(verts) // 4
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    me.loops.add(len(verts))
    me.loops.foreach_set("vertex_index", np.arange(len(verts), dtype=np.int32))
    me.polygons.add(nq)
    me.polygons.foreach_set("loop_start", np.arange(0, len(verts), 4, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(nq, 4, dtype=np.int32))
    slots = np.concatenate([np.full(g.quads, s, dtype=np.int32) for s, g in parts])
    me.polygons.foreach_set("material_index", slots)
    me.update(calc_edges=True)
    uv = me.uv_layers.new(name="UVMap")
    uv.data.foreach_set("uv", uvs.astype(np.float32).ravel())
    ca = me.color_attributes.new("Col", "FLOAT_COLOR", "CORNER")
    ca.data.foreach_set("color", cols.astype(np.float32).ravel())
    for m in mats:
        me.materials.append(m)
    return me


def _show_only_on(obj, frame: int):
    """Hidden except on ``frame``: the keys are exact on whole frames."""
    for f, hide in ((frame - 1, True), (frame, False), (frame + 1, True)):
        for prop in ("hide_viewport", "hide_render"):
            setattr(obj, prop, hide)
            obj.keyframe_insert(prop, frame=f)
    obj.hide_viewport = obj.hide_render = True


class Result:
    def __init__(self):
        self.effects = 0
        self.quads = 0
        self.objects = 0
        self.first = None
        self.last = None
        self.notes: list = []


def bake(context, ent, spawns, notes) -> Result:
    """Bake ``spawns`` (:class:`fxbake.Spawn`) of the entity. The scene's frame is put back afterwards."""
    scene = context.scene
    res = Result()
    res.notes += notes
    source, game = _source(ent), _game_dir()
    fps = float(scene.render.fps) / float(scene.render.fps_base or 1.0)
    lookup = lambda n: fxcore.load_effect(n, source, game)[0]
    plans = []
    for sp in spawns:
        leaves = fxbake.flatten(lookup, sp.fx, res.notes)
        n = fxbake.length_frames(leaves, fps)
        if n:
            plans.append((sp, leaves, n))
    if not plans:
        return res
    # the marker's place and the camera on every frame any effect needs: the scene is stepped once through them
    needed = sorted({sp.frame + k for sp, _l, n in plans for k in range(n)})
    markers = _marker_objects(ent)
    wanted = sorted({sp.marker for sp, _l, _n in plans})
    saved_frame = scene.frame_current
    rig = ent.rig
    saved_action = rig.animation_data.action if rig is not None and rig.animation_data is not None else None
    cam_now = _camera_axes(context)
    mats_at, cams_at = {}, {}
    try:
        for clip in {sp.clip for sp, _l, _n in plans}:                     # a clip's key frames move the markers only while its Action is the active one
            act = _action_of_clip(ent, clip)
            if act is not None and rig is not None:
                if rig.animation_data is None:
                    rig.animation_data_create()
                rig.animation_data.action = act
            for f in needed:
                if not any(sp.clip == clip and sp.frame <= f < sp.frame + n for sp, _l, n in plans):
                    continue
                scene.frame_set(f)
                dg = context.evaluated_depsgraph_get()
                for name in wanted:
                    mats_at[(clip, name, f)] = marker_matrix(ent, name, markers, dg)
                cams_at[f] = _camera_axes(context) or cam_now
    finally:
        if rig is not None and rig.animation_data is not None and rig.animation_data.action is not saved_action:
            rig.animation_data.action = saved_action
        scene.frame_set(saved_frame)
    cname = collection_name(ent)
    coll = bpy.data.collections.get(cname)
    if coll is None:
        coll = bpy.data.collections.new(cname)
        scene.collection.children.link(coll)
    for sp, leaves, n in plans:
        mats = [mats_at[(sp.clip, sp.marker, sp.frame + k)] for k in range(n)]
        cams = [cams_at[sp.frame + k] for k in range(n)]
        baked = fxbake.bake(leaves, fps, n, mats, cams, start=sp.frame)
        res.notes += baked.notes
        if not baked.layers:
            continue
        res.effects += 1
        slot_mats = [material_for(l.texture, l.blend, source, game) for l in baked.layers]
        for k in range(n):
            parts = [(i, l.geoms[k]) for i, l in enumerate(baked.layers) if k < len(l.geoms) and l.geoms[k].quads]
            if not parts:
                continue
            frame = sp.frame + k
            me = _frame_mesh(f"fxprev_{sp.fx}_{frame}", parts, slot_mats)
            obj = bpy.data.objects.new(me.name, me)
            obj[KEY_BAKE] = True
            coll.objects.link(obj)
            _show_only_on(obj, frame)
            res.objects += 1
            res.quads += sum(g.quads for _i, g in parts)
            res.first = frame if res.first is None else min(res.first, frame)
            res.last = frame if res.last is None else max(res.last, frame)
    return res


# ---------------------------------------------------------------------------------------------------------- operators
def _clip_ranges(ent, context) -> dict:
    return {c.lower(): clip_range(ent, c, context.scene) for c in clip_names(ent)}


def _summary(res: Result) -> str:
    if not res.objects:
        return "Nothing to show."
    return f"Showed {res.effects} effect(s) from frame {res.first} to frame {res.last} ({res.objects} pictures). Press Play to see them."


def _report(op, res: Result, scene) -> None:
    op.report({"INFO"}, _summary(res))
    for n in dict.fromkeys(res.notes):
        op.report({"WARNING"}, n)
    if res.last is not None and res.last > scene.frame_end:
        op.report({"INFO"}, f"The effects run past the end of the timeline (frame {scene.frame_end}) up to frame {res.last}: move the end to see them.")


def _prepare(context):
    ent = _entity(context)
    state = ent.holder.bz_fx
    if not state.loaded:
        load_events(ent, state)
    return ent, state


class BZ_OT_fx_preview_bake(bpy.types.Operator):
    """Show the effects started by the chosen clip's events on the timeline. Press Play to see them"""
    bl_idname = "bz.fx_preview_bake"
    bl_label = "Show the clip's effects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ent, state = _prepare(context)
        clip = state.clip
        if not clip and ent.rig is not None:
            names = clip_names(ent)
            clip = names[0] if names else ""
        if not clip.strip():
            self.report({"ERROR"}, "Name the clip first (type it in the Clip box above the event list).")
            return {"CANCELLED"}
        spawns, notes = fxbake.plan_spawns(items_of(state), _clip_ranges(ent, context), clip=clip)
        if not spawns:
            self.report({"WARNING"}, f"The clip '{clip}' has no event that starts an effect.")
            for n in notes:
                self.report({"WARNING"}, n)
            return {"CANCELLED"}
        clear(ent)
        res = bake(context, ent, spawns, notes)
        _report(self, res, context.scene)
        return {"FINISHED"}


class BZ_OT_fx_preview_solo(bpy.types.Operator):
    """Show only the chosen event's effect (at the time its clip gives it). Replaces the preview made before"""
    bl_idname = "bz.fx_preview_solo"
    bl_label = "Show this event's effect"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _event_poll(context)

    def execute(self, context):
        ent, state = _prepare(context)
        ranges = _clip_ranges(ent, context)
        ranges[""] = (float(context.scene.frame_start), float(context.scene.frame_end))
        spawns, notes = fxbake.plan_spawns(items_of(state), ranges, event=state.event_index)
        if not spawns:
            self.report({"WARNING"}, "This event does not start an effect (it needs an effect name)." if not notes else notes[0])
            return {"CANCELLED"}
        clear(ent)
        res = bake(context, ent, spawns, notes)
        _report(self, res, context.scene)
        return {"FINISHED"}


class BZ_OT_fx_preview_clear(bpy.types.Operator):
    """Remove the effect preview from the timeline (nothing else changes)"""
    bl_idname = "bz.fx_preview_clear"
    bl_label = "Remove the preview"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        n = clear(_entity(context))
        self.report({"INFO"}, f"Removed {n} preview picture(s)." if n else "There is no preview to remove.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------- panel
def draw_preview(layout, context) -> None:
    ent = _entity(context)
    state = _state(ent)
    if state is None:
        return
    _hint(layout, "See the effects of the events on the timeline, at their markers. Choose a clip in the Events box, then press the button and Play.")
    layout.operator("bz.fx_preview_bake", icon="PLAY")
    row = layout.row()
    row.operator("bz.fx_preview_solo", icon="PARTICLES")
    row.operator("bz.fx_preview_clear", text="Remove", icon="X")
    n = len(baked_objects(ent))
    if n:
        _hint(layout, f"{n} preview picture(s) are on the timeline. They are not exported.", "INFO")
    _hint(layout, APPROX)


class BZ_PT_fx_preview(bpy.types.Panel):
    bl_label = "Effect preview"
    bl_idname = "BZ_PT_fx_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_fx"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        ent = _entity(context)
        return _state(ent) is not None and not fxpanel._is_effect_model(ent)

    def draw(self, context):
        draw_preview(self.layout, context)


CLASSES = (BZ_OT_fx_preview_bake, BZ_OT_fx_preview_solo, BZ_OT_fx_preview_clear, BZ_PT_fx_preview)
