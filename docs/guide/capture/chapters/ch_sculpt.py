"""Chapter `sculpt`: Sculpt Surface / Refit Patches (roadmap A2) on the stock cheetah.

Bake the exact bicubic surface into a sculptable mesh, push it about, and let the least-squares refit put the
change back into the control points of the patches you actually touched.

Capture note: a scripted session cannot hold a brush, so the bump is applied to the sculpt mesh's vertices with
a smooth radial falloff -- exactly the vertices a Grab brush moves, and the refit cannot tell the difference
(it reads the mesh, not the brush). The captions say what a user would do instead.
"""
import os
import subprocess

import bpy
import numpy as np

from blender_native import sculptops


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


def _verts(obj):
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def _bump(obj, centre, radius, delta):
    """A Grab-brush bump: every vertex within ``radius`` of ``centre`` moved by ``delta`` with a smooth falloff."""
    P = _verts(obj)
    d = np.linalg.norm(P - np.asarray(centre), axis=1)
    w = np.clip(1.0 - d / radius, 0.0, 1.0)
    w = w * w * (3.0 - 2.0 * w)
    P = P + w[:, None] * np.asarray(delta)
    obj.data.vertices.foreach_set("co", P.astype(np.float32).ravel())
    obj.data.update()
    return int((w > 1e-6).sum())


# ----------------------------------------------------------------------------------------------------- steps
def steps(g):
    scene = bpy.context.scene
    bpy.context.preferences.view.smooth_view = 0
    scene.bz_combine.validate_level = "OFF"

    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/cheetah.sgm", clips_as_actions=False)
    obj = g.active()
    _activate(obj)
    V = _verts(obj)
    size = float(np.linalg.norm(V.max(0) - V.min(0)))
    _view_axis(g, "RIGHT")
    g.orbit(-25)
    g.frame([obj])
    _sidebar(g)
    yield "before", ("The stock cheetah as imported: 2594 control points in 288 patches, the live surface drawn "
                     "over them.")

    _scroll = g.scroll_sidebar
    _scroll(9)
    yield "panel", ("The SGM panel's Sculpt box. On a creature with no sculpt in progress it holds one button, "
                    "Sculpt Surface; while one is in progress it holds Refit Patches and Discard Sculpt.")

    res, err = g.call(bpy.ops.bz.sculpt_surface, density=7)
    sculpt = next((o for o in bpy.data.objects if sculptops.is_sculpt(o)), None)
    print("GUIDE sculpt surface:", res, err, "|", None if sculpt is None else
          (sculpt.name, len(sculpt.data.vertices), bpy.context.mode))
    _sidebar(g)
    yield "sculptmode", (f"Sculpt Surface at Density 7: the exact surface is baked into "
                         f"'{'' if sculpt is None else sculpt.name}' "
                         f"({0 if sculpt is None else len(sculpt.data.vertices)} vertices -- the 14,112 samples "
                         f"with the coincident seam samples merged, so the brushes cannot tear the skin at a "
                         f"patch seam), the creature and its rig are hidden, and Blender is in Sculpt Mode.")

    n = 0
    if sculpt is not None:
        P = _verts(sculpt)
        back = P[(P[:, 2] > np.percentile(P[:, 2], 92)) & (np.abs(P[:, 1]) < 0.25 * size)]
        centre = back.mean(axis=0) if len(back) else P.mean(axis=0)
        n = _bump(sculpt, centre, 0.22 * size, (0.0, 0.0, 0.055 * size))
        _activate(sculpt)
        g.frame([sculpt])
        _dist(g, 0.95 * size)
    print("GUIDE bump:", n, "vertices")
    yield "bump", (f"A Grab brush pulled up on the back (here {n} vertices moved with a smooth falloff, about "
                   f"6 cm on a 1 m creature). Nothing has happened to the creature yet: this is an ordinary "
                   f"Blender mesh that remembers which creature and which samples it came from.")

    _activate(sculpt if sculpt is not None else obj)
    res, err = g.call(bpy.ops.bz.refit_patches)
    line = dict(obj.get("bz_last_refit", {}) or {})
    print("GUIDE refit:", res, err, "|", line)
    _activate(obj)
    g.frame([obj])
    _dist(g, 0.95 * size)
    _sidebar(g)
    yield "refit", ("Refit Patches: the moved samples are scattered back to the patches they came from and the "
                    "least-squares refit (ICParse.codecs.fit.refit_samples) solves for the control points. "
                    "Patches no moved sample touched keep their control points bit for bit. The sculpt mesh is "
                    "removed and the creature is shown again.")

    out = os.path.join(g.OUT, "cheetah_sculpted.sgm")
    r2, e2 = g.call(bpy.ops.bz.export_sgm, filepath=out)
    print("GUIDE export:", r2, e2, "|", obj.get("bz_last_export", ""), os.path.exists(out))
    _scroll(9)
    yield "after", ("The refitted cheetah, exported. The export report names what was rewritten -- only the "
                    "control points the refit moved; every other byte of the file is the original.")
