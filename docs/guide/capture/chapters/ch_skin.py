"""Chapter `skin`: skinning and re-skinning the stock rhinoceros with Boner > Bones (By Bone, Pick Bone, Select
Points, Assign Selected, Remove from Bone, Select Unassigned, Auto Skin, Ctrl+G groups, the export summary).
Operator messages are recorded as `note:` entries in skin.json (and printed as GUIDE NOTE lines)."""
import os
import subprocess

import numpy as np
import bpy
import bmesh
from mathutils import Vector

from blender_native import store, ops

NOTES = []


def _own_xauth():
    """The Xauthority file of this run's own Xvfb. The snap launcher points XAUTHORITY at the shared
    /run/user/<uid>/.Xauthority, which every concurrent /snap/bin/blender launch overwrites with another display's
    cookie: Blender keeps its connection, but `import` / `xdotool` then fail to open the display."""
    disp = os.environ.get("DISPLAY", "")
    try:
        out = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.startswith(f"Xvfb {disp} ") and " -auth " in line:
            return line.split(" -auth ", 1)[1].split()[0]
    return None


_XAUTH = _own_xauth()
_ENV0 = {"DISPLAY": os.environ.get("DISPLAY"), "XAUTHORITY": _XAUTH or os.environ.get("XAUTHORITY")}


def _fix_env():
    for k, v in _ENV0.items():
        if v is not None and os.environ.get(k) != v:
            os.environ[k] = v


_fix_env()
print(f"GUIDE NOTE xauth: DISPLAY={_ENV0['DISPLAY']} XAUTHORITY={os.environ.get('XAUTHORITY')}", flush=True)


def note(tag, text):
    NOTES.append((tag, text))
    print(f"GUIDE NOTE {tag}: {text}", flush=True)


def select(obj, idx):
    idx = set(int(i) for i in idx)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in idx
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def view(g, axis, orbit=0, up=0):
    """A perspective view from a named axis, orbited a little for depth."""
    win, area, region = g.view3d()
    with g.override():
        bpy.ops.view3d.view_axis(type=axis)
    if orbit:
        g.orbit(orbit)
    if up:
        g.orbit(up, "ORBITUP")
    area.spaces.active.region_3d.view_perspective = "PERSP"


def look(g, centre, dist):
    """Aim the viewport at a world point from a set distance (keeps the current view rotation)."""
    win, area, region = g.view3d()
    rv3d = area.spaces.active.region_3d
    rv3d.view_location = Vector(centre)
    rv3d.view_distance = float(dist)


def scroll_sidebar(g, steps=12):
    """Scroll the N sidebar down (the Boner sub-panel sits below the SGM panel's own boxes)."""
    win, area, region = g.view3d()
    ui = next(r for r in area.regions if r.type == "UI")
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        try:                                             # lay the panels out first (a mode switch changes them),
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)   # or the scroll clamps at the old end
        except RuntimeError as e:
            note("scroll", f"redraw_timer failed: {e}")
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=ui):
        for _ in range(steps):
            try:
                bpy.ops.view2d.scroll_down()
            except RuntimeError as e:
                note("scroll", f"view2d.scroll_down failed: {e}")
                break


def park_mouse(g):
    """Rest the cursor over empty viewport space (over the toolbar it raises a tooltip)."""
    win, area, region = g.view3d()
    x = win.x + region.x + region.width // 2
    y = win.y + (win.height - (region.y + 40))
    subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=False)


def screen_xy(g, world):
    """X-server pixel of a world point in the 3D Viewport's main region (for xdotool)."""
    from bpy_extras import view3d_utils
    win, area, region = g.view3d()
    rv3d = area.spaces.active.region_3d
    q = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(world))
    if q is None:
        return None
    return win.x + region.x + int(q.x), win.y + (win.height - (region.y + int(q.y)))


def radius(pts, c):
    return float(np.linalg.norm(pts - c, axis=1).max())


def nod_rotation(rig, bone, tip, amount=0.7):
    """The single-axis Euler rotation of ``bone`` that lowers the world point ``tip`` the most (a nod, readable in a
    profile view), measured on the pose bone."""
    pb = rig.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    rest = rig.data.bones[bone].matrix_local.copy()
    best = None
    for ax in range(3):
        for sgn in (1.0, -1.0):
            e = [0.0, 0.0, 0.0]
            e[ax] = sgn * amount
            pb.rotation_euler = e
            bpy.context.view_layer.update()
            p = (rig.matrix_world @ pb.matrix @ rest.inverted()) @ Vector(tip)
            if best is None or p.z < best[0]:
                best = (p.z, tuple(e))
    pb.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    return best[1]


def steps(g):
    for item in _steps(g):
        _fix_env()
        yield item
    _fix_env()


def _steps(g):
    scene = bpy.context.scene
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj = g.active()
    rig = store.rig(obj)
    names = store.bone_names(obj)
    bidx = {n: i for i, n in enumerate(names)}
    V = np.array([v.co[:] for v in obj.data.vertices])          # rest control points (mesh space = world here)
    owners0 = store.vert_bone(obj).copy()
    patches = store.patches(obj)
    used = set(int(i) for i in np.unique(patches.reshape(-1)))
    scene.bz_combine.validate_level = "OFF"
    s = scene.bz_boner
    s.mode = "BONES"
    scene.bz_shade_mode = "BONE"                                  # the By Bone overlay (Overlay in Boner > Bones)
    centre = V.mean(axis=0)
    g.frame([obj])
    g.sidebar()
    yield "sidebar_wait", "(sidebar opening)"
    for k in range(3):                                           # the tab strip takes xdotool events: make sure of it
        if g.sidebar_tab() == "SGM":
            break
        g.sidebar()
        yield f"sidebar_retry{k}", f"(sidebar tab retry, tab={g.sidebar_tab()})"
    note("sidebar", f"tab={g.sidebar_tab()}")
    park_mouse(g)
    view(g, "FRONT", orbit=-35, up=12)
    look(g, centre, 1.9 * radius(V, centre))
    yield "bybone", "The stock rhinoceros in the By Bone overlay: each control point coloured by the one bone that owns it."

    scroll_sidebar(g, 14)
    s.bone = "head"
    yield "active_head", f"Active bone 'head' (Boner > Bones > Bone): its points bright, every other bone dimmed. tab={g.sidebar_tab()}"

    # ---- Select Points, zoomed on the head
    g.mode(obj, "EDIT")
    scroll_sidebar(g, 40)                                        # Edit Mode adds boxes above Boner: scroll again
    select(obj, [])
    head_joint = np.array(rig.data.bones["head"].head_local[:])
    head_pts = V[owners0 == bidx["head"]]
    head_c = head_pts.mean(axis=0)
    nose_pts = V[owners0 == bidx["nose"]]
    nose_c = nose_pts.mean(axis=0)
    tip = nose_pts[np.argmin(nose_pts[:, 1] if head_c[1] < centre[1] else -nose_pts[:, 1])]      # the nose tip
    look(g, head_c, 2.4 * radius(head_pts, head_c))
    res, msg = g.call(bpy.ops.bz.skin_select_points)
    note("select_points", f"{res} {msg}")
    yield "select_points", "Edit Mode > Select Points: every control point 'head' owns is selected (white ring on each)."

    # ---- Pick Bone: the modal, driven by xdotool (hover a neck3 point clear of the head's points, then click)
    select(obj, [])
    neck3 = np.nonzero(owners0 == bidx["neck3"])[0]
    clear = np.array([np.linalg.norm(head_pts - V[i], axis=1).min() for i in neck3])
    target = int(neck3[np.argmax(clear)])
    xy = screen_xy(g, V[target])
    picked = False
    if xy is not None:
        subprocess.run(["xdotool", "mousemove", str(xy[0]), str(xy[1])], check=False)
        yield "pick_hover", "(cursor over a control point of neck3, ready for Pick Bone)"
        try:
            with g.override():
                r = bpy.ops.bz.pick_bone("INVOKE_DEFAULT")
            note("pick_invoke", str(r))
            picked = r == {"RUNNING_MODAL"}
        except RuntimeError as e:
            note("pick_invoke", f"refused: {e}")
        if picked:
            subprocess.Popen(["xdotool", "mousemove", str(xy[0] + 1), str(xy[1]), "sleep", "0.2",
                              "mousemove", str(xy[0]), str(xy[1])])
            yield "pick_bone", "Pick Bone running: every point of the bone under the cursor lights up and the header names it."
            subprocess.Popen(["xdotool", "click", "1"])
            yield "pick_done", "Clicked: the point's bone is the active bone; the status bar reports its point count."
            note("pick_result", f"active bone after click: {s.bone!r} (expected neck3)")
            if s.bone != "neck3":
                subprocess.Popen(["xdotool", "key", "Escape"])
            park_mouse(g)
    s.bone = "neck3"                                               # the scriptable way (what Pick Bone sets)

    # ---- pose before the reskin (profile view, head nodding)
    g.mode(obj, "OBJECT")
    s.bone = "head"
    g.mode(rig, "POSE")
    pb = rig.pose.bones["head"]
    nod = nod_rotation(rig, "head", tip)
    note("pose", f"head rotation_euler {nod} (nose tip lowered the most)")
    pb.rotation_euler = nod
    rig.data.bones.active = rig.data.bones["head"]
    view(g, "RIGHT", up=12)                                      # a profile: the nod and the neck bend read best
    neck_pts = V[(owners0 == bidx["neck3"]) | (owners0 == bidx["neck2"]) | (owners0 == bidx["neck1"])]
    zone = np.vstack([head_pts, neck_pts])
    zone_c = zone.mean(axis=0)
    look(g, zone_c, 3.4 * radius(zone, zone_c))
    yield "pose_before", "Pose Mode, the head bone rotated with the stock skin: neck3's points stay behind, the surface bends at the head/neck boundary."
    pb.rotation_euler = (0.0, 0.0, 0.0)
    g.mode(rig, "OBJECT")

    # ---- move the 20 neck3 points farthest from the head joint (the lower part of the neck) onto the head bone:
    # far from the pivot, so the nod shows what changed
    order = neck3[np.argsort(-np.linalg.norm(V[neck3] - head_joint, axis=1))]
    move = [int(i) for i in order[:20]]
    g.mode(obj, "EDIT")
    scroll_sidebar(g, 40)
    select(obj, move)
    s.bone = "head"
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    note("assign", f"{res} {msg} moved={len(move)} neck3->head")
    yield "assign", "Assign Selected: 20 points of neck3 (the lower part of the neck) now belong to 'head' and take its colour."

    g.mode(obj, "OBJECT")
    g.mode(rig, "POSE")
    pb.rotation_euler = nod
    yield "pose_after", "The same pose after the reskin: the reassigned neck points follow the head, so the bend sits further down the neck."
    pb.rotation_euler = (0.0, 0.0, 0.0)
    g.mode(rig, "OBJECT")

    # ---- Remove from Bone on the whole left ear, Export refusal, Select Unassigned, Auto Skin
    g.mode(obj, "EDIT")
    scroll_sidebar(g, 40)
    ear_all = [int(i) for i in np.nonzero(owners0 == bidx["ear_L"])[0]]
    ear_used = [i for i in ear_all if i in used]
    ear_pts = V[owners0 == bidx["ear_L"]]
    ear_c = ear_pts.mean(axis=0)
    view(g, "RIGHT" if ear_c[0] >= 0 else "LEFT", orbit=-20, up=15)
    look(g, ear_c, 3.2 * radius(ear_pts, ear_c))
    s.bone = "ear_L"
    select(obj, ear_all)
    res, msg = g.call(bpy.ops.bz.skin_remove)
    note("remove", f"{res} {msg}")
    stats = ops.skin_stats_cached(obj)
    note("stats_after_remove", f"unassigned={stats.unassigned} used={stats.unassigned_used} of {len(ear_all)} ear_L points")
    select(obj, [])
    yield "remove", f"Remove from Bone: the {len(ear_all)} points of ear_L now have no bone (orange); the panel counts them and warns that Export refuses."

    res, msg = g.call(bpy.ops.bz.export_sgm, filepath=os.path.join(g.OUT, "rhino_unassigned.sgm"))
    note("export_refused", f"{res} {msg}")
    res, msg = g.call(bpy.ops.bz.validate)
    verdict = list(obj.get("bz_validation", []))
    note("validate_refused", " | ".join(verdict[:2]))
    res, msg = g.call(bpy.ops.bz.skin_select_unassigned)
    note("select_unassigned", f"{res} {msg}")
    yield "select_unassigned", "Export refused them; Select (next to Unassigned) picks exactly the orange points."

    res, msg = g.call(bpy.ops.bz.skin_auto, target="AUTO")
    note("autoskin_selected", f"{res} {msg}")
    stats = ops.skin_stats_cached(obj)
    vb = store.vert_bone(obj)
    got = {}
    for i in ear_all:
        got[names[int(vb[i])]] = got.get(names[int(vb[i])], 0) + 1
    note("autoskin_result", f"unassigned={stats.unassigned}; the ear points went to {got}")
    select(obj, [])
    yield "autoskin", f"Auto Skin > Selected, else Unassigned: the selected points get their nearest bone segment ({', '.join(f'{k} {n}' for k, n in got.items())})."

    # ---- points given to a stub bone: Validate's STOCK line (the rhino's Torso lists only canonical bones, so the
    # stray-points line cannot be triggered here; the stub-limb line can)
    nose_used = [int(i) for i in np.nonzero(owners0 == bidx["nose"])[0] if int(i) in used]
    six = nose_used[:6]
    select(obj, six)
    s.bone = "SWing1_L"
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    note("stub_assign", f"{res} {msg}")
    res, msg = g.call(bpy.ops.bz.validate)
    verdict = list(obj.get("bz_validation", []))
    note("validate_stub", " | ".join(p for p in verdict if p.startswith("STOCK")) or "(no STOCK line)")
    select(obj, six)
    s.bone = "nose"
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    note("stub_undo", f"{res} {msg}")
    # and to another limb's bone (allowed by the stray rule: it names Torso / no-limb bones only)
    select(obj, six)
    s.bone = "Rear_leg1_l"
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    g.call(bpy.ops.bz.validate)
    verdict = list(obj.get("bz_validation", []))
    note("validate_otherlimb", " | ".join(p for p in verdict if p.startswith(("STOCK", "WARN", "ERROR"))) or "(no STOCK/WARN line)")
    select(obj, six)
    s.bone = "nose"
    g.call(bpy.ops.bz.skin_assign_active)

    # ---- Blender's own way: Ctrl+G (vertex_group_assign) rigid, then blended
    near = sorted(nose_used, key=lambda i: np.linalg.norm(V[i] - tip))
    X = near[:12]
    view(g, "RIGHT" if head_c[0] >= 0 else "LEFT", orbit=25, up=10)
    look(g, nose_c, 2.6 * radius(nose_pts, nose_c))
    s.bone = "jaw1"                                              # jaw1 bright: the adopted points stand out
    scroll_sidebar(g, 40)
    select(obj, X)
    obj.vertex_groups.active_index = obj.vertex_groups["nose"].index
    with g.override():
        bpy.ops.object.vertex_group_remove_from()
    obj.vertex_groups.active_index = obj.vertex_groups["jaw1"].index
    scene.tool_settings.vertex_group_weight = 1.0
    with g.override():
        bpy.ops.object.vertex_group_assign()
    stats = ops.skin_stats_cached(obj)
    live = store.live_skin(obj).owners
    note("ctrlg_rigid", f"live owner of the 12 nose-tip points = {sorted({names[int(k)] for k in live[X]})}, "
                        f"stored table still {sorted({names[int(k)] for k in store.vert_bone(obj)[X]})}; "
                        f"unassigned={stats.unassigned} blended={stats.blended}")
    select(obj, [])
    yield "ctrlg", "Twelve nose-tip points moved with Blender's own Ctrl+G (Remove from nose, Assign to jaw1 at weight 1.0): By Bone already shows them as jaw1's."

    Y = near[12:13]
    select(obj, Y)
    obj.vertex_groups.active_index = obj.vertex_groups["jaw1"].index
    scene.tool_settings.vertex_group_weight = 0.5
    with g.override():
        bpy.ops.object.vertex_group_assign()
    scene.tool_settings.vertex_group_weight = 1.0
    stats = ops.skin_stats_cached(obj)
    note("ctrlg_blended", f"blended={stats.blended} point {Y}")
    res, msg = g.call(bpy.ops.bz.export_sgm, filepath=os.path.join(g.OUT, "rhino_blended.sgm"))
    note("export_blended", f"{res} {msg}")
    select(obj, [near[13]])
    s.bone = "nose"
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    note("assign_while_blended", f"{res} {msg}")
    select(obj, Y)
    scroll_sidebar(g, 40)
    yield "blended", "One more nose point given a 0.5 weight in jaw1 on top of nose: the panel warns, and Export and the Boner edits refuse until it is rigid again."

    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    note("fix_blended", f"{res} {msg}")
    stats = ops.skin_stats_cached(obj)
    note("stats_fixed", f"unassigned={stats.unassigned} blended={stats.blended}")

    # ---- Export: the summary line
    g.mode(obj, "OBJECT")
    out = os.path.join(g.OUT, "rhino_reskin.sgm")
    res, msg = g.call(bpy.ops.bz.export_sgm, filepath=out)
    line = obj.get("bz_last_export", "")
    note("export", f"{res} {msg} summary={line!r} size={os.path.getsize(out) if os.path.exists(out) else None}")

    # ---- Auto Skin > Reset All on the whole creature
    view(g, "FRONT", orbit=-35, up=12)
    look(g, centre, 1.9 * radius(V, centre))
    s.bone = ""
    scroll_sidebar(g, 40)
    res, msg = g.call(bpy.ops.bz.skin_auto, target="ALL")
    note("reset_all", f"{res} {msg}")
    vb = store.vert_bone(obj)
    note("reset_all_stats", f"agreement with the stock skin {float((vb == owners0).mean()):.1%}; bones used {len(np.unique(vb))}")
    yield "reset_all", "Auto Skin > Reset All: the whole skin re-derived from the skeleton (nearest bone segment). A first pass only: compare with the stock colouring above."
    out2 = os.path.join(g.OUT, "rhino_resetall.sgm")
    res, msg = g.call(bpy.ops.bz.export_sgm, filepath=out2)
    note("export_resetall", f"{res} {msg} summary={obj.get('bz_last_export', '')!r}")
    for tag, text in NOTES:
        g.shots.append({"shot": "note:" + tag, "file": "", "caption": text})
