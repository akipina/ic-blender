"""Chapter `fit`: Fit Patches to Mesh (roadmap A1) -- an outside model becomes a patch creature.

The target is Blender's own Suzanne with its two eye shells removed and two Catmull-Clark levels applied (the
recipe of tools/fit_experiment.py and tests/blender_fit_mesh_test.py): one closed X-symmetric skin of about
7,000 vertices, which is what an import or a sculpt gives you.

Capture notes: the fit itself is one step of about 35 s at budget 300, which the driver's timer waits for; the
heat map is shown by switching the viewport's Solid shading to Color: Attribute.
"""
import os
import subprocess

import bmesh
import bpy

from blender_native import store


# --------------------------------------------------------------------------------------------------- helpers
def _view_axis(g, kind):
    with g.override():
        bpy.ops.view3d.view_axis(type=kind)


def _dist(g, d):
    win, area, region = g.view3d()
    area.spaces.active.region_3d.view_distance = float(d)


def _mouse_park(g, delay=1.8):
    win, area, region = g.view3d()
    x = win.x + area.x + area.width // 2
    y = win.y + (win.height - (area.y + area.height)) + 120
    subprocess.Popen(["xdotool", "sleep", str(delay), "mousemove", str(x), str(y)])


def _sidebar(g):
    g.sidebar()
    _mouse_park(g)


def _activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj)
    bpy.context.view_layer.objects.active = obj


def _colour(g, kind="MATERIAL"):
    win, area, region = g.view3d()
    sh = area.spaces.active.shading
    sh.type = "SOLID"
    sh.color_type = kind


def _keep_largest(obj):
    """Suzanne ships as three shells (skin + two eyes); the eyes sit INSIDE their sockets, and a fit refuses a
    target that is still several shells after a positional weld, so the skin is kept alone."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seen, parts = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, part = [f], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            part.append(x)
            for e in x.edges:
                stack.extend(h for h in e.link_faces if h not in seen)
        parts.append(part)
    parts.sort(key=len, reverse=True)
    bmesh.ops.delete(bm, geom=[f for p in parts[1:] for f in p], context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    return len(parts)


# ----------------------------------------------------------------------------------------------------- steps
def steps(g):
    scene = bpy.context.scene
    bpy.context.preferences.view.smooth_view = 0
    scene.bz_combine.validate_level = "OFF"

    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    target = g.active()
    target.name = "Suzanne"
    nparts = _keep_largest(target)
    mod = target.modifiers.new("subsurf", "SUBSURF")
    mod.levels = mod.render_levels = 2
    with g.override():
        bpy.ops.object.modifier_apply(modifier="subsurf")
    _activate(target)
    _view_axis(g, "FRONT")                 # Suzanne faces -Y, so the Front view looks her in the face
    g.orbit(25)
    g.orbit(10, axis="ORBITUP")
    g.frame([target])
    _sidebar(g)
    print("GUIDE target:", target.name, len(target.data.vertices), "vertices,", nparts, "shells before the trim")
    yield "target", (f"The target: Suzanne with her two eye shells removed and two Catmull-Clark levels applied -- "
                     f"one closed, X-symmetric skin of {len(target.data.vertices)} vertices. Any mesh in this "
                     f"shape does: a sculpt, a scan, an imported glTF.")

    g.scroll_sidebar(3)
    yield "panel", ("With an ordinary mesh active, the SGM panel offers Convert Mesh to Creature and Fit Patches "
                    "to Mesh above the Display box. Fit's options are in the redo panel (F9): Patch Budget, "
                    "Layout, Seam Smoothness, Iterations, the skeleton template, the output .sgm, Error Heat Map "
                    "and Split into Limbs.")

    out = os.path.join(g.OUT, "suzanne.sgm")
    res, err = g.call(bpy.ops.bz.fit_mesh, budget=300, layout="QUADRIFLOW", output_path=out)
    creature = g.active()
    line = str(creature.get("bz_last_fit", ""))
    print("GUIDE fit:", res, err, "|", line)
    print("GUIDE fitted:", creature.name, store.is_native(creature), len(creature.data.vertices), "points,",
          creature.get(store.KEY_NPATCH), "patches ->", out, os.path.exists(out))
    target.hide_set(True)
    _activate(creature)
    g.frame([creature])
    _sidebar(g)
    yield "fitted", ("The fitted creature: QuadriFlow lays out about the budget of quads on a welded copy of the "
                     "target, each quad becomes a bicubic patch (ICParse ACC), and the control points are solved "
                     "against the target's vertices. The target itself is not touched.")

    scene.bz_shade_mode = "LIMB"
    arm = store.rig(creature)
    if arm is not None:
        arm.hide_set(True)
    yield "limbs", ("The same creature shaded By Limb. Split into Limbs (on by default) gives it the TEMPLATE's "
                    "body plan: every control point takes its nearest eligible bone in the template's frame and "
                    "every patch the limb of its points' bones, so the creature is riggable at once instead of "
                    "having every patch in the Torso.")

    scene.bz_shade_mode = "TEXTURED"
    creature.hide_set(True)
    if arm is not None:
        arm.hide_set(True)
    target.hide_set(False)
    scene.bz_show_surface = False           # the overlay draws a creature even when its mesh is hidden
    _activate(target)
    _colour(g, "VERTEX")
    g.frame([target])
    heat = target.data.color_attributes.get("fit_error")
    print("GUIDE heat map:", None if heat is None else (heat.name, heat.domain, len(heat.data)))
    yield "heat", ("Error Heat Map: the per-vertex fit error painted on the target as the 'fit_error' colour "
                   "attribute (blue 0 to red 1 % of the mesh's size), shown with Viewport Shading > Color: "
                   "Attribute. It is the only thing the fit adds to your mesh.")
    _colour(g, "MATERIAL")
    scene.bz_show_surface = True
