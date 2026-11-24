"""Scene-level display/edit settings (registered onto bpy.types.Scene)."""
from __future__ import annotations

import bpy

from . import draw


def _on_density(self, context):
    draw.invalidate()
    from . import gnsurface                      # D9: the evaluated surface follows the same density
    if getattr(context.scene, "bz_gn_surface", False):
        gnsurface.apply_scene(context)
    _redraw(context)


def _on_gn_surface(self, context):
    from . import gnsurface
    gnsurface.apply_scene(context)
    _redraw(context)


def _redraw(context):
    if context and context.screen:
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _apply_surface_only(context):
    """Hide/show the control-point meshes + their armatures; the gpu surface keeps drawing."""
    from . import store
    on = bool(getattr(context.scene, "bz_surface_only", False))
    for obj in context.scene.objects:
        if store.is_native(obj):
            obj.hide_set(on)
            arm = store.rig(obj)
            if arm is not None:
                arm.hide_set(on)
    _redraw(context)


PROPS = {
    "bz_density": bpy.props.IntProperty(
        name="Surface Density", description="Samples per patch edge (n x n grid per patch)",
        default=4, min=2, max=16, update=_on_density),
    "bz_surface_only": bpy.props.BoolProperty(
        name="Surface Only", description="Hide the control points + armature and show only the "
        "creature surface (turn off to edit again)",
        default=False, update=lambda s, c: _apply_surface_only(c)),
    "bz_show_surface": bpy.props.BoolProperty(
        name="Show Surface", description="Draw the live bicubic surface overlay",
        default=True, update=lambda s, c: _redraw(c)),
    "bz_gn_surface": bpy.props.BoolProperty(
        name="Evaluated Surface", description="Build the bicubic surface as REAL geometry with a Geometry "
        "Nodes modifier instead of drawing it as an overlay: it then renders in Eevee/Cycles with the "
        "creature's materials and UVs, and modifiers, snapping and click-select see it. Costs a full "
        "re-evaluation on every control-point move, so the overlay stays the default for editing",
        default=False, update=_on_gn_surface),
    "bz_show_controlnet": bpy.props.BoolProperty(
        name="Show Control Net", description="Draw the patch control-net wireframe overlay",
        default=True, update=lambda s, c: _redraw(c)),
    "bz_shade_mode": bpy.props.EnumProperty(
        name="Shading", description="How to colour the live surface",
        items=[("TEXTURED", "Textured", "Real creature textures"),
               ("LIMB", "By Limb", "Colour each patch by its limb / body part"),
               ("BONE", "By Bone", "Colour the surface and the control points by the bone that owns them (the active "
                "Boner bone bright, the others dimmed, points with no bone orange)"),
               ("PLAIN", "Plain", "Flat shaded grey")],
        default="TEXTURED", update=lambda s, c: _redraw(c)),
    "bz_show_connections": bpy.props.BoolProperty(
        name="Show Connections", description="Highlight limb graft-ring control points",
        default=False, update=lambda s, c: _redraw(c)),
    "bz_show_handles": bpy.props.BoolProperty(
        name="Show Handle Dots",
        description="Overlay control points coloured by kind (corner vs off-surface handle)",
        default=False, update=lambda s, c: _redraw(c)),
    "bz_edit_opacity": bpy.props.FloatProperty(
        name="Edit Opacity", description="Surface opacity in edit mode (see handles through it)",
        default=0.6, min=0.05, max=1.0, update=lambda s, c: (draw.invalidate(), _redraw(c))),
    "bz_patch_rules": bpy.props.BoolProperty(
        name="Patch Rules (Max feel)",
        description="Edit patches the way 3ds Max / gmax Editable Patch does: a corner you move drags its tangent "
                    "handles with it (a handle moved on its own is a tangent edit and stays), and the four interior "
                    "points of every touched patch are recomputed by Max's Auto Interior rule (interior = row handle "
                    "+ column handle - corner; every stock creature obeys it). Off: raw control-point editing, every "
                    "point moves alone",
        default=True, update=lambda s, c: _redraw(c)),
    "bz_show_interiors": bpy.props.BoolProperty(
        name="Show Interior Points",
        description="Reveal the interior control points (4 per patch, automatic and hidden by default, like Max). "
                    "Shown, they can be moved by hand, which flags their patch Manual Interior so the rule leaves "
                    "it; Auto Interiors (Modeling) puts a patch back on the rule. Hidden, an interior follows the "
                    "corner of its frame in the skin edits when it is skinned to the same bone (every stock one is)",
        default=False, update=lambda s, c: _apply_interiors(c)),
    "bz_lock_handles": bpy.props.BoolProperty(
        name="Lock Handles",
        description="3ds Max Lock Handles: a handle you move by itself turns the corner's other handles by the same "
                    "rotation (their lengths kept). Off: a handle moved alone is a tangent edit of that handle only "
                    "(a Coplanar corner still keeps its handles in one plane)",
        default=False, update=lambda s, c: _redraw(c)),
    "bz_live_checks": bpy.props.BoolProperty(
        name="Live Checks",
        description="Run Export's refusals as you edit (D10): an object transform, shape keys, an extra modifier, a "
                    "deleted or merged control point, a blended weight, a base file that changed -- each is shown in "
                    "the Problems box and in the viewport the moment it happens, instead of first at Export. Nothing "
                    "is fixed or undone; Export refuses exactly as before. Off: the checks run only at Export",
        default=True, update=lambda s, c: (_forget_checks(), _redraw(c))),
}


def _forget_checks():
    from . import guards
    guards.forget()
    guards._LAST.clear()


def _apply_interiors(context):
    from . import patchfeel
    patchfeel.apply_interior_visibility_all(context)
    _redraw(context)


_MODE_SHADING = {"BONES": "BONE", "LIMBS": "LIMB"}


def _on_boner_mode(self, context):
    """Boner's sub-mode picks the overlay while Follow Shading is on (Bones: By Bone, Limbs: By Limb)."""
    shade = _MODE_SHADING.get(self.mode)
    if self.follow_shading and shade and context is not None and context.scene is not None:
        if context.scene.bz_shade_mode != shade:
            context.scene.bz_shade_mode = shade
    _redraw(context)


def _on_boner_bone(self, context):
    _redraw(context)


class BZBonerSettings(bpy.types.PropertyGroup):
    """Boner panel UI state (scene.bz_boner). Creature data never lives here: every creature edit is an operator
    that writes through the store (undo-safe). (Update callbacks are named functions: the annotations are evaluated
    from strings, where a lambda can't see this module's helpers.)"""
    mode: bpy.props.EnumProperty(
        name="Boner Mode", description="Boner's sub-mode",
        items=[("BONES", "Bones", "Skin control points to bones"),
               ("LIMBS", "Limbs", "The limbs (body parts) and their patches"),
               ("CONNECTIONS", "Connections", "The connection rings limbs graft at")],
        default="BONES", update=_on_boner_mode)
    bone: bpy.props.StringProperty(
        name="Bone", description="The active bone: Assign, Select Points and Remove work on it (pick it in the viewport "
        "with the eyedropper)", default="", update=_on_boner_bone)
    follow_shading: bpy.props.BoolProperty(
        name="Follow Shading", description="Switch the overlay with the Boner sub-mode (Bones: By Bone, Limbs: By "
        "Limb)", default=True)
    active_limb: bpy.props.IntProperty(
        name="Active Limb", description="The limb Rename, Delete and the classification boxes work on (Boner > Limbs)",
        default=0, min=0, update=_on_boner_bone)
    active_conn: bpy.props.IntProperty(
        name="Active Connection", description="The connection Rename, Delete, the classification boxes, the host bone "
        "and the ring work on (Boner > Connections)", default=0, min=0, update=_on_boner_bone)
    conn_target: bpy.props.EnumProperty(
        name="Classification", description="Which class of the active connection the boxes edit (Boner's Parent / Child "
        "radio buttons)",
        items=[("PARENT", "Parent", "flags1: the limb the ring is cut into (the Torso for every limb socket)"),
               ("CHILD", "Child", "flags2: the class of the limb attached through it")],
        default="CHILD", update=_on_boner_bone)
    conn_bone: bpy.props.StringProperty(
        name="Host Bone", description="The bone to attach the active connection to (Set applies it; Default picks the "
        "parent of the limb's root bone)", default="", update=_on_boner_bone)
    show_points: bpy.props.BoolProperty(
        name="Bone Points", description="By Bone: draw the control points in their bone's colour (orange: no bone; "
        "a white ring marks a selected point in Edit Mode)", default=True, update=_on_boner_bone)


def register():
    from . import combopreview
    for k, v in PROPS.items():
        setattr(bpy.types.Scene, k, v)
    # the Combine Preview settings group: its class must be registered before the pointer that uses it
    bpy.utils.register_class(combopreview.BZCombineSettings)
    bpy.types.Scene.bz_combine = bpy.props.PointerProperty(type=combopreview.BZCombineSettings)
    bpy.utils.register_class(BZBonerSettings)            # the class before the pointer that uses it
    bpy.types.Scene.bz_boner = bpy.props.PointerProperty(type=BZBonerSettings)


def unregister():
    from . import combopreview
    if hasattr(bpy.types.Scene, "bz_boner"):
        del bpy.types.Scene.bz_boner
    try:
        bpy.utils.unregister_class(BZBonerSettings)
    except RuntimeError:
        pass                                    # not registered (a failed register)
    if hasattr(bpy.types.Scene, "bz_combine"):
        del bpy.types.Scene.bz_combine
    try:
        bpy.utils.unregister_class(combopreview.BZCombineSettings)
    except RuntimeError:
        pass                                    # not registered (a failed register)
    for k in PROPS:
        if hasattr(bpy.types.Scene, k):
            delattr(bpy.types.Scene, k)
