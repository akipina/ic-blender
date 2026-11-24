"""Chapter `retarget`: Retarget, Landmark Retarget and Trim Stray Surface (roadmap A3 / A3b / A5).

Three stories, each on its own fresh import of the stock cheetah:

  A  Retarget Creature to Mesh onto a deformed copy of the cheetah's own bake (the legs 25 % further apart,
     the head 10 cm up) -- the closest-point fit, with the rings pinned and the rest bones carried along;
  B  Create Landmarks, the Empties dragged onto the target by the same rule a user would drag them by, then
     Landmark Retarget: the thin-plate-spline warp first, the closest-point fit after it;
  C  Trim Stray Surface on the cheetah against its own bake with the outer half of the tail cut away: Select
     names the invented patches, Collapse onto Target folds them flat.

The deformation and the tail cut are the ones tests/landmark_retarget_test.py and tests/trim_stray_test.py
measure, so the numbers in the captions are the tested numbers.
"""
import os
import subprocess

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from blender_native import fitops, store, trimops

DENSITY = 7


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


def _wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def _imp(g):
    _wipe()
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/cheetah.sgm", clips_as_actions=False)
    obj = g.active()
    _activate(obj)
    return obj


def _verts(obj):
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def _bake_target(g, obj, name, cut=None):
    """Bake Surface at DENSITY, weld the per-patch seam duplicates (the bake makes one vertex per patch corner
    sample) and optionally cut everything past ``cut`` = (axis, value, beyond) away."""
    bpy.context.scene.bz_density = DENSITY
    _activate(obj)
    g.call(bpy.ops.bz.bake_surface)
    baked = bpy.data.objects[obj.name + "_baked"]
    bm = bmesh.new()
    bm.from_mesh(baked.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    if cut is not None:
        ax, value, beyond = cut
        gone = [v for v in bm.verts if (v.co[ax] > value) == bool(beyond)]
        bmesh.ops.delete(bm, geom=gone, context="VERTS")
    bm.to_mesh(baked.data)
    bm.free()
    baked.data.update()
    baked.name = name
    print("GUIDE target", name, len(baked.data.vertices), "vertices,", fitops._loose_parts(baked.data), "part(s)")
    return baked


def _deform_rule(P0):
    """The shape change as a function of position, so the target mesh AND the landmarks on it move by one rule:
    the legs 25 % further apart and the head 10 cm up (tests/landmark_retarget_test.py's own deformation)."""
    z = P0[:, 2]
    belly = float(z.min() + 0.4 * (z.max() - z.min()))
    y_head = float(np.percentile(P0[:, 1], 3.0))

    def f(P):
        P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
        spread = 1.0 + 0.25 * np.clip((belly - P[:, 2]) / 0.1, 0.0, 1.0)
        above = np.clip((P[:, 2] - belly) / 0.1, 0.0, 1.0)
        w = np.clip((y_head + 0.2 - P[:, 1]) / 0.2, 0.0, 1.0)
        w = w * w * (3.0 - 2.0 * w)
        out = P.copy()
        out[:, 0] = P[:, 0] * spread
        out[:, 2] = P[:, 2] + 0.10 * w * above
        return out
    return f


def _deform_mesh(obj, f):
    P = _verts(obj)
    obj.data.vertices.foreach_set("co", f(P).astype(np.float32).ravel())
    obj.data.update()


def _tail_cut(obj):
    """(axis, value, beyond) of the plane leaving the tail's base 45 % of its length -- what trim_stray_test cuts."""
    patches = store.patches(obj)
    S = trimops.surface_samples(_verts(obj), patches).reshape(len(patches), trimops.SURFACE_N ** 2, 3)
    names = store.limb_names(obj)
    tail = next(i for i, n in enumerate(names) if n.endswith("_Tail"))
    rows = np.nonzero(store.patch_limb(obj) == tail)[0]
    St = S[rows].reshape(-1, 3)
    body = S[store.patch_limb(obj) != tail].reshape(-1, 3)
    ax = int(np.argmax(St.max(0) - St.min(0)))
    lo, hi = float(St.min(0)[ax]), float(St.max(0)[ax])
    tip_high = abs(hi - body.mean(0)[ax]) > abs(lo - body.mean(0)[ax])
    value = lo + 0.45 * (hi - lo) if tip_high else hi - 0.45 * (hi - lo)
    return ax, value, tip_high


# ----------------------------------------------------------------------------------------------------- steps
def steps(g):
    scene = bpy.context.scene
    bpy.context.preferences.view.smooth_view = 0
    scene.bz_combine.validate_level = "OFF"

    # ------------------------------------------------------------------------------ A. Retarget
    obj = _imp(g)
    size = float(np.linalg.norm(_verts(obj).max(0) - _verts(obj).min(0)))
    tgt = _bake_target(g, obj, "CheetahDeformed")
    f = _deform_rule(_verts(tgt))
    _deform_mesh(tgt, f)
    obj.hide_set(True)
    arm = store.rig(obj)
    if arm is not None:
        arm.hide_set(True)
    scene.bz_show_surface = False                 # the overlay draws a creature even when its mesh is hidden
    _activate(tgt)
    _view_axis(g, "FRONT")
    g.orbit(35)
    g.orbit(12, axis="ORBITUP")
    g.frame([tgt])
    _sidebar(g)
    yield "target", ("The target: the cheetah's own baked surface, welded into one skin and then deformed -- the "
                     "legs 25 % further apart and the head 10 cm up. In practice this is a sculpt, a scan or an "
                     "imported model; it only has to be one closed skin.")

    obj.hide_set(False)
    if arm is not None:
        arm.hide_set(False)
    scene.bz_show_surface = True
    _activate(obj)
    g.scroll_sidebar(11)
    yield "panel", ("The SGM panel's Tools box holds the three: Retarget Creature to Mesh, Create Landmarks / "
                    "Landmark Retarget, and Trim Stray Surface. Their options are in the redo panel (F9).")

    res, err = g.call(bpy.ops.bz.retarget_mesh, target=tgt.name)
    line = str(obj.get("bz_last_retarget", ""))
    print("GUIDE A retarget:", res, err, "|", line)
    tgt.hide_set(True)
    _activate(obj)
    g.frame([obj])
    yield "retargeted", ("Retarget Creature to Mesh: the creature's own control points are fitted to the target "
                         "(the target is hidden here). The patch layout, limbs, connection rings, skeleton, "
                         "skinning and clips are all still the cheetah's -- only the points and the rest bones "
                         "moved.")

    # ------------------------------------------------------------------------------ B. Landmark Retarget
    obj = _imp(g)
    _sidebar(g)                                   # early: the tab switch is X events delivered while Blender idles
    tgt = _bake_target(g, obj, "CheetahSpread")
    f = _deform_rule(_verts(tgt))
    _deform_mesh(tgt, f)
    _activate(obj)
    res, err = g.call(bpy.ops.bz.landmarks_create)
    col = bpy.data.collections.get("Landmarks_" + obj.name)
    names = []
    if col is not None:
        for o in col.objects:                     # the user drags each Empty onto the matching place; here the
            p = np.array([o.location.x, o.location.y, o.location.z])   # deformation's own rule does the dragging
            o.location = Vector(f(p[None, :])[0].tolist())
            names.append(o.name)
    bpy.context.view_layer.update()
    print("GUIDE B landmarks:", res, err, len(names), sorted(names)[:6])
    tgt.hide_set(False)
    _activate(obj)
    g.frame([obj])
    _dist(g, 1.15 * size)
    yield "landmarks", (f"Create Landmarks dropped {len(names)} Empties named LM_<limb> and LM_<limb>_root at the "
                        f"creature's own positions -- each limb's tip and root, plus LM_Torso -- and each has been "
                        f"dragged onto the matching place on the target.")

    res, err = g.call(bpy.ops.bz.landmark_retarget, target=tgt.name)
    line = str(obj.get("bz_last_landmark_retarget", ""))
    print("GUIDE B landmark retarget:", res, err, "|", line)
    tgt.hide_set(True)
    if col is not None:
        for o in list(col.objects):
            o.hide_set(True)
    _activate(obj)
    g.frame([obj])
    yield "landmark_retargeted", ("Landmark Retarget: a thin-plate spline from the landmarks' donor positions to "
                                  "where you put them warps every control point AND every rest bone's head and "
                                  "tail, and the closest-point fit runs from that start. That is what carries a "
                                  "limb ACROSS the body, which a closest-point fit alone cannot do.")

    # ------------------------------------------------------------------------------ C. Trim Stray Surface
    obj = _imp(g)
    _sidebar(g)
    tgt = _bake_target(g, obj, "CheetahNoTail", cut=_tail_cut(obj))
    _activate(obj)
    _view_axis(g, "RIGHT")
    g.orbit(20)
    g.frame(all=True)
    _dist(g, 1.25 * size)
    yield "trim_target", ("A target the creature has MORE than: the cheetah's bake with the outer half of the "
                          "tail cut away. Retargeting to it moves not one control point and reports rms 0.00 %, "
                          "because a closest-point fit only measures target to surface -- the invented patches "
                          "are invisible to it.")

    scene.bz_show_surface = True
    tgt.hide_set(True)
    _activate(obj)
    g.mode(obj, "EDIT")
    _sidebar(g)
    res, err = g.call(bpy.ops.bz.trim_stray, target=tgt.name, action="SELECT")
    st = dict(obj.get("bz_last_trim_stats", {}) or {})
    print("GUIDE C select:", res, err, "|", str(obj.get("bz_last_trim", "")), "|",
          {k: st[k] for k in list(st)[:6]})
    yield "trim_select", ("Trim Stray Surface with Action: Select -- the default, because the modder decides. It "
                          "measures, per patch, how much of its 7 x 7 surface samples is farther than Stray "
                          "Distance (2 % of the size) from the nearest target point, and selects the patches "
                          "where that reaches Stray Fraction (half). Here that is exactly the tail beyond the cut.")

    g.mode(obj, "OBJECT")
    _sidebar(g)
    res, err = g.call(bpy.ops.bz.trim_stray, target=tgt.name, action="COLLAPSE")
    print("GUIDE C collapse:", res, err, "|", str(obj.get("bz_last_trim", "")))
    _activate(obj)
    g.frame(all=True)
    _dist(g, 1.25 * size)
    yield "trim_collapse", ("Collapse onto Target: the stray patches' control points move onto their nearest "
                            "target point, so the tail folds flat against the body instead of standing in the "
                            "air. The patch table is never touched, so the creature stays exactly as watertight, "
                            "skinned and combinable as it was. (Delete + Fill Hole is the other action.)")
    print("GUIDE retarget out dir:", os.path.isdir(g.OUT))
