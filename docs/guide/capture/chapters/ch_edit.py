"""Chapter `edit`: editing an existing animal (the stock rhinoceros).

Geometry workflow: Tab, select control points by kind, move them (the horn), export, undo across Tab,
Symmetrize (X), X-mirror (the ears), proportional editing (the back), Relax Seam Tangents, Extrude Patch on an
open copy of the rhino (stock creatures are closed, so the button is greyed out on them), Validate and Export.

Capture notes (learned the hard way):
- a mode switch (Tab, and the operators that toggle modes internally) resets the sidebar to the Tool tab, so
  g.sidebar() is called again after each one;
- every edit-mode selection is done BEFORE any area type is switched (switching the Properties editor to the Info
  editor earlier made later bmesh selections vanish in the GUI), so the Info editor only appears for the last shots;
- reports of an operator called from Python never reach the Info editor / status bar: Validate is run through the
  operator search (wm.search_operator, its name typed with xdotool) and Export is invoked with a prefilled path and
  confirmed with Return; a WM-invoked operator with warnings leaves a "Report: Warning" popup that swallows keys,
  so Escape is sent after those shots;
- Blender's X-mirror needs bit-exact partners: Symmetrize (X) right before the mirrored move, with no
  proportional / relax edit in between (those leave sub-millimetre asymmetry);
- run with a fixed display number (xvfb-run -n NNN): -a raced with other captures and lost the X server.
"""
import os
import subprocess

import bmesh
import bpy
import numpy as np
from mathutils import Vector

from blender_native import core, store


# --------------------------------------------------------------------------------------------------- helpers
def _select(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()          # an operator that touched the mesh leaves the index table dirty (index -1),
                                     # and then `v.index in idx` is False for every vertex: nothing gets selected
    s = set(int(i) for i in idx)
    for v in bm.verts:
        v.select = v.index in s
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _selected(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    return [v.index for v in bm.verts if v.select]


def _nverts(obj):
    if obj.mode == "EDIT":
        return len(bmesh.from_edit_mesh(obj.data).verts)
    return len(obj.data.vertices)


def _verts(obj):
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    n = len(obj.data.vertices)
    a = np.empty(n * 3)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(n, 3)


def _exact_partners(V, idx, tol=1e-6):
    """How many of the points ``idx`` have a bit-exact mirror partner across X (what Blender's X-mirror needs)."""
    M = V[idx].copy()
    M[:, 0] *= -1
    return int(sum(1 for m in M if np.abs(V - m).max(axis=1).min() < tol))


def _view_axis(g, kind):
    with g.override():
        bpy.ops.view3d.view_axis(type=kind)


def _zoom(g, delta):
    with g.override():
        bpy.ops.view3d.zoom(delta=delta)


def _tilt_above(g, deg):
    """Orbit the view vertically so the camera ends up ABOVE the horizon by about ``deg`` (the sign of
    ORBITUP depends on the view, so it is checked on the view rotation and flipped when needed)."""
    win, area, region = g.view3d()
    rv3d = area.spaces.active.region_3d
    g.orbit(deg, axis="ORBITUP")
    back = rv3d.view_rotation @ Vector((0.0, 0.0, 1.0))      # from the scene toward the viewer
    if back.z < 0:
        g.orbit(-2 * deg, axis="ORBITUP")


def _clip(g, beyond=None, start=0.01):
    """View depth clipping: in perspective, clip_end a little beyond the view pivot hides the far side of the
    body (the hole then shows the background instead of the inside of the opposite flank). No argument: reset."""
    win, area, region = g.view3d()
    sp = area.spaces.active
    rv3d = sp.region_3d
    if beyond is None:
        sp.clip_end, sp.clip_start = 1000.0, 0.01
        return
    rv3d.view_perspective = "PERSP"
    sp.clip_start = start
    sp.clip_end = float(rv3d.view_distance) + beyond
    print("GUIDE clip: view_distance", round(rv3d.view_distance, 3), "clip_end", round(sp.clip_end, 3))


def _mouse_park(g, delay=1.8):
    """Park the mouse over the viewport (no toolbar tooltip in the shots; popups open there)."""
    win, area, region = g.view3d()
    x = win.x + area.x + area.width // 2
    y = win.y + (win.height - (area.y + area.height)) + 120
    subprocess.Popen(["xdotool", "sleep", str(delay), "mousemove", str(x), str(y)])


def _sidebar(g):
    """Re-open the SGM tab (a mode switch resets it to Tool), then park the mouse."""
    g.sidebar()
    _mouse_park(g)


def _escape():
    """Close a report popup (a WM-invoked operator with warnings leaves one that swallows keys)."""
    subprocess.run(["xdotool", "key", "Escape"])


def _info_clear(area):
    win = bpy.context.window_manager.windows[0]
    region = next(r for r in area.regions if r.type == "WINDOW")
    try:
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
            bpy.ops.info.select_all(action="SELECT")
            bpy.ops.info.report_delete()
    except Exception as e:
        print("GUIDE note: info clear failed:", e)


def _export_ui(g, path, wait=0.3):
    """Export SGM the way the user does it: the file browser opens on the path, Return confirms. The report
    then reaches the status bar and the Info editor (a Python call's reports never do)."""
    with g.override():
        res = bpy.ops.bz.export_sgm("INVOKE_DEFAULT", filepath=path)
    subprocess.Popen(["xdotool", "sleep", str(wait), "key", "Return"])
    return res


def _hide(obj, on):
    obj.hide_set(on)
    arm = store.rig(obj)
    if arm is not None:
        arm.hide_set(on)


def _renative(name=None):
    """The native creature again after an Undo / Redo: Blender rebuilds its datablocks, so the handle a step held
    before the undo is a dead StructRNA ("StructRNA of type Object has been removed"). None when the undo went
    back past the import."""
    obj = next((o for o in bpy.data.objects if store.is_native(o) and (name is None or o.name == name)), None)
    if obj is not None:
        bpy.context.view_layer.objects.active = obj
    return obj


def _activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj)
    bpy.context.view_layer.objects.active = obj


def _open_rhino(g):
    """The stock rhino minus one patch on top of its back, written into the chapter's OUT dir (never Assets/)."""
    from ICParse.codecs import reconstruct as R
    src = g.STOCK + "/rhinoceros.sgm"
    d = core.load_creature(src)
    m = d.model
    C = d.patches[:, [0, 3, 12, 15]]
    z = d.verts[C, 2].mean(axis=1)
    x = np.abs(d.verts[C, 0]).max(axis=1)
    plimb = core._patch_limb_of(m)
    torso = [i for i, l in enumerate(m.limbs) if l.name.endswith("Torso")]
    xm = d.verts[C, 0].mean(axis=1)
    ym = d.verts[C, 1].mean(axis=1)
    cand = [pi for pi in range(len(d.patches)) if plimb[pi] in torso and xm[pi] > 0.5 and abs(ym[pi]) < 0.6
            and 1.2 < z[pi] < 2.0]
    drop = max(cand, key=lambda pi: xm[pi])                 # the right flank, seen from the Right view
    P = d.verts[C]
    tgt = P[drop].copy()
    tgt[:, 0] *= -1                                          # and its mirror partner on the left flank, so the
    partner = min(range(len(d.patches)),                     # Right view looks through both holes at the background
                  key=lambda pi: max(min(float(np.abs(q - t).max()) for q in P[pi]) for t in tgt))
    keep = [pi for pi in range(len(d.patches)) if pi not in (drop, partner)]
    data = core.export_grown(src, d.verts, d.patches[keep], np.array(R.vertex_bone_owners(m)), d.patch_mat[keep],
                             d.patch_uv[keep], np.array([p.type for p in m.patches])[keep], plimb[keep])
    path = os.path.join(g.OUT, "rhinoceros_open.sgm")
    with open(path, "wb") as f:
        f.write(data)
    print(f"GUIDE open rhino: dropped patches {drop} + {partner} (z {z[drop]:.2f}) -> {path}")
    return path


# ----------------------------------------------------------------------------------------------------- steps
def steps(g):
    scene = bpy.context.scene
    bpy.context.preferences.view.smooth_view = 0
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj = g.active()
    g.frame([obj])
    _zoom(g, 1)
    g.orbit(-20)
    _sidebar(g)
    yield "import", "The stock rhinoceros imported (Object Mode, Textured): the surface the game draws, the control net and the rig."

    # Tab: edit mode, translucent surface + control net
    g.mode(obj, "EDIT")
    _sidebar(g)
    yield "editmode", "Tab into Edit Mode: the surface turns translucent (Edit Opacity) and every control point is a vertex you can select and grab. Extrude Patch is greyed out: a stock creature is closed."

    # the head, from the right: corners vs handles
    V = _verts(obj)
    head_pts = np.nonzero(V[:, 1] < -1.25)[0]
    _select(obj, head_pts)
    _view_axis(g, "RIGHT")
    g.frame()
    _zoom(g, -1)
    scene.bz_show_handles = True
    g.call(bpy.ops.bz.select_kind, kind="CORNER")
    yield "corners", "Show Handle Dots on, Select > Corners: only the on-surface points (gold dots) are selected."
    g.call(bpy.ops.bz.select_kind, kind="HANDLE")
    yield "handles", "Select > Handles: only the off-surface tangent and interior points (blue dots)."
    scene.bz_show_handles = False

    # lengthen the horn: grab the horn's points (corners AND their handles) and move them
    horn = np.nonzero((V[:, 1] < -2.2) & (V[:, 2] > 1.5))[0]
    _select(obj, horn)
    yield "horn_before", f"The {len(horn)} control points of the horn selected (its corners and the handles around them)."
    res, err = g.call(bpy.ops.transform.translate, value=(0.0, -0.15, 0.45))
    print("GUIDE horn move:", res, err)
    yield "horn_after", "G, then move: the live surface follows the selected points. The horn is longer."
    v_moved = _verts(obj)[horn[0]].copy()

    # export now: only the moved points are rewritten (the report lands in the status bar)
    _export_ui(g, os.path.join(g.OUT, "rhinoceros_horn.sgm"))
    yield "export_horn", "Export SGM (Native Patches) right after the horn move: the status bar reports what was rewritten, the popup carries the warning."
    line_h = obj.get("bz_last_export", "")
    print("GUIDE horn export:", line_h, os.path.exists(os.path.join(g.OUT, "rhinoceros_horn.sgm")))
    _escape()

    # undo across Tab keeps the add-on's tables
    g.mode(obj, "OBJECT")
    steps_undone = 0
    for _k in range(3):        # how many steps the mode switch and the move are worth is Blender's business:
        g.call(bpy.ops.ed.undo)                      # undo until the move is back, and no further
        steps_undone += 1
        obj = _renative()      # Blender rebuilds the datablocks on undo: the old handle is a dead StructRNA
        if obj is None:        # past the import: one redo puts the creature back
            g.call(bpy.ops.ed.redo)
            steps_undone -= 1
            obj = _renative()
            break
        if not np.allclose(_verts(obj)[horn[0]], v_moved, atol=1e-4):
            break
    v_undone = _verts(obj)[horn[0]].copy()
    print("GUIDE undo:", steps_undone, "step(s), mode", obj.mode, "moved", v_moved.round(3),
          "undone", v_undone.round(3), "points", _nverts(obj), "patches", obj.get(store.KEY_NPATCH),
          "pool", store.pool_error(obj))
    _sidebar(g)
    yield "undo", (f"Tab out, Ctrl+Z twice (the mode switch, then the move): the horn is back, and the panel still "
                   f"knows its {_nverts(obj)} control points / {int(obj.get(store.KEY_NPATCH, 0))} patches.")
    for _k in range(steps_undone):
        g.call(bpy.ops.ed.redo)
    obj = _renative() or obj
    print("GUIDE redo: mode", obj.mode, "pt", _verts(obj)[horn[0]].round(3))
    if obj.mode != "EDIT":
        g.mode(obj, "EDIT")
        _sidebar(g)
    if not np.allclose(_verts(obj)[horn[0]], v_moved, atol=1e-4):
        _select(obj, horn)
        g.call(bpy.ops.transform.translate, value=(0.0, -0.15, 0.45))
        print("GUIDE redo did not restore the move; re-applied")

    # Symmetrize (X) (toggles the mode internally: sidebar again), whole body from the front-left
    _select(obj, [])
    r1, e1 = g.call(bpy.ops.bz.symmetrize)
    print("GUIDE symmetrize:", r1, e1)
    _select(obj, np.arange(len(V)))
    _view_axis(g, "FRONT")
    _tilt_above(g, 12)
    g.orbit(40)
    g.frame()
    _zoom(g, 1)
    _select(obj, [])
    _sidebar(g)
    yield "symmetrize", "Tools > Symmetrize (X): every control point that has a mirror partner is snapped to exact bilateral symmetry (one undo step)."

    # X-mirror: move the left ear, the right ear should follow (exact partners exist now)
    _view_axis(g, "FRONT")
    _tilt_above(g, 5)
    scene.bz_show_controlnet = False
    Vn = _verts(obj)
    crown = np.nonzero((Vn[:, 1] > -1.6) & (Vn[:, 1] < -0.8) & (Vn[:, 2] > 1.7))[0]
    _select(obj, crown)
    g.frame()
    _zoom(g, -2)
    ear = np.nonzero((Vn[:, 0] > 0.3) & (Vn[:, 1] > -1.45) & (Vn[:, 1] < -0.9) & (Vn[:, 2] > 1.95))[0]
    mirror = np.nonzero((Vn[:, 0] < -0.3) & (Vn[:, 1] > -1.45) & (Vn[:, 1] < -0.9) & (Vn[:, 2] > 1.95))[0]
    print("GUIDE ear exact partners:", _exact_partners(Vn, ear), "of", len(ear), "use_mirror_x", obj.data.use_mirror_x,
          "topology", obj.data.use_mirror_topology)
    _select(obj, ear)
    z_before = Vn[mirror, 2].copy()
    yield "mirror_before", (f"X-Mirror Editing is on (SGM panel). The {len(_selected(obj))} selectable points of the "
                            f"left ear are selected ({len(ear)} points sit in that region; the interiors among them "
                            f"are hidden and follow their corners by the patch rule); the right ear is not.")
    # the scripted X-mirror move, measured and undone (see the log)
    res, err = g.call(bpy.ops.transform.translate, value=(0.0, 0.0, 0.3), mirror=True)
    Vn = _verts(obj)
    moved_x = int((np.abs(Vn[mirror, 2] - z_before) > 1e-4).sum())
    print("GUIDE x-mirror move:", res, err, "right-ear partners moved", moved_x, "of", len(mirror))
    g.call(bpy.ops.ed.undo)
    _select(obj, ear)
    rs, es = g.call(bpy.ops.mesh.select_mirror, extend=True)
    res, err = g.call(bpy.ops.transform.translate, value=(0.0, 0.0, 0.3))
    Vn = _verts(obj)
    moved = int((np.abs(Vn[mirror, 2] - z_before) > 1e-4).sum())
    print("GUIDE select-mirror move:", rs, es, res, err, "moved", moved, "selected", len(_selected(obj)))
    yield "mirror_after", (f"Select > Select Mirror (Shift+Ctrl+M, Extend) adds the {moved} mirror partners, then G Z 0.3 "
                           f"raises both ears together (control net hidden for clarity).")
    scene.bz_show_controlnet = True

    # proportional editing: raise the back from one corner
    _select(obj, np.arange(len(V)))
    _view_axis(g, "RIGHT")
    g.frame()
    Vn = _verts(obj)
    corners = set(int(i) for i in store.patches(obj).reshape(-1, 16)[:, [0, 3, 12, 15]].ravel())
    back = np.array([i for i in np.nonzero((np.abs(Vn[:, 0]) < 0.05) & (Vn[:, 1] > -0.4)
                                          & (Vn[:, 1] < 0.4))[0] if int(i) in corners])
    top = int(back[np.argmax(Vn[back, 2])])       # a CORNER: the interior points are hidden and cannot be selected
    _select(obj, [top])
    scene.tool_settings.use_proportional_edit = True
    scene.tool_settings.proportional_size = 1.0
    yield "back_before", "One corner on top of the back selected; proportional editing on (O), size 1 m."
    res, err = g.call(bpy.ops.transform.translate, value=(0.0, 0.0, 0.35), use_proportional_edit=True,
                      proportional_edit_falloff="SMOOTH", proportional_size=1.0)
    print("GUIDE back move:", res, err, "selected", len(_selected(obj)))
    yield "back_after", "G Z 0.35 with proportional editing: the neighbouring control points follow with a smooth falloff, so the whole back rises instead of one point spiking."
    scene.tool_settings.use_proportional_edit = False

    # Relax Seam Tangents on everything (no selection = all seams)
    _select(obj, [])
    r2, e2 = g.call(bpy.ops.bz.seam_smooth)
    print("GUIDE relax:", r2, e2)

    # extrude on an open copy (the stock rhino is closed: the button is disabled with the reason)
    g.mode(obj, "OBJECT")
    _hide(obj, True)
    path = _open_rhino(g)
    bpy.ops.bz.import_sgm(filepath=path)
    obj2 = g.active()
    patches = store.patches(obj2)
    users = core.side_users(patches)
    V2 = _verts(obj2)
    opens_all = [(pi, s, k) for pi, s, k in core.patch_sides(patches) if len(users[k]) == 1]
    opens = [t for t in opens_all if V2[[int(patches[t[0]][c]) for c in core._SIDE_CPS[t[1]]], 0].mean() > 0]
    print("GUIDE open sides:", len(opens_all), "on the right flank:", len(opens))

    def _score(t):
        pi, s, _k = t
        cps = core._SIDE_CPS[s]
        a, b = int(patches[pi][cps[0]]), int(patches[pi][cps[3]])
        return V2[[a, b], 1].mean()
    pi, side, _k = max(opens, key=_score)
    cps = core._SIDE_CPS[side]
    corners = [int(patches[pi][cps[0]]), int(patches[pi][cps[3]])]
    rim = sorted({int(patches[p][c]) for p, s, _k in opens for c in core._SIDE_CPS[s]})
    # By Limb shading, rig hidden, the exact Right view (so the near hole looks through the far one at the
    # background): show the rim, then select the two corners through Object Mode (its flags carry into Edit Mode)
    scene.bz_shade_mode = "LIMB"
    arm2 = store.rig(obj2)
    if arm2 is not None:
        arm2.hide_set(True)
    for v in obj2.data.vertices:
        v.select = v.index in set(rim)
    g.mode(obj2, "EDIT")
    _view_axis(g, "RIGHT")                                   # exactly from the side: the two holes line up
    g.frame()
    _zoom(g, -2)
    _sidebar(g)
    yield "extrude_hole", "An open creature: this copy of the rhino lacks one patch on each flank, so the Right view looks through the near hole and out of the far one (By Limb shading, rig hidden). The control points around the near hole are selected: four open sides."
    g.mode(obj2, "OBJECT")
    for v in obj2.data.vertices:
        v.select = v.index in corners
    g.mode(obj2, "EDIT")
    _sidebar(g)
    sel = _selected(obj2)
    print("GUIDE extrude corners", corners, "selected", sel)
    if sorted(sel) != sorted(corners):
        _select(obj2, corners)
        print("GUIDE re-selected via bmesh:", _selected(obj2))
    n0, p0 = _nverts(obj2), int(obj2[store.KEY_NPATCH])
    yield "extrude_before", "The two corners of one open side selected (nothing else), so Modeling > Extrude Patch is enabled."
    res, err = g.call(bpy.ops.bz.extrude_patch)
    n1, p1 = _nverts(obj2), int(obj2[store.KEY_NPATCH])
    print("GUIDE extrude:", res, err, n0, "->", n1, p0, "->", p1)
    yield "extrude_after", f"Extrude Patch: +{n1 - n0} control points and +1 patch sharing that side ({p0} -> {p1} patches), one handle-spacing out from the side; its other three sides are still open."
    g.mode(obj2, "OBJECT")
    for o in [obj2] + ([arm2] if arm2 else []):
        bpy.data.objects.remove(o, do_unlink=True)
    scene.bz_shade_mode = "TEXTURED"
    _hide(obj, False)
    _activate(obj)

    # the Info editor for the reports (the Properties editor becomes it), then Validate (operator search) and Export
    info = g.area_type("INFO")
    _activate(obj)
    _view_axis(g, "FRONT")
    _tilt_above(g, 20)
    g.orbit(35)
    g.frame([obj])
    _zoom(g, 1)
    _activate(obj)
    _info_clear(info)
    _sidebar(g)
    yield "validate_ready", "Back in Object Mode with every edit made (the Info editor opened in place of the Properties editor)."
    with g.override():
        bpy.ops.wm.search_operator("INVOKE_DEFAULT")
    subprocess.Popen(["bash", "-c", "sleep 0.4; xdotool type --delay 40 'Validate (will'; sleep 0.6; xdotool key Return"])
    yield "validate_search", "The operator search finds the SGM operators by name (Tools > Validate (will it load?) on the panel does the same)."
    yield "validate", "Validate with Combine Checks: Quick: the verdict in the status bar, every finding in the Info editor."
    val = list(obj.get("bz_validation", []))
    print("GUIDE validate:", len(val), val[:12])
    if not val:
        _escape()
        g.call(bpy.ops.bz.validate)
        print("GUIDE validate fallback:", list(obj.get("bz_validation", []))[:12])
    _escape()

    _info_clear(info)
    out = os.path.join(g.OUT, "rhinoceros_edit.sgm")
    _export_ui(g, out)
    yield "export", "Export SGM (Native Patches) on the edited rhino: the report says what was re-encoded."
    line = obj.get("bz_last_export", "")
    print("GUIDE export:", line, os.path.exists(out))
    _escape()
    # and, for comparison, a fresh untouched import exported next to it (the edited one removed first)
    arm = store.rig(obj)
    for o in [obj] + ([arm] if arm else []):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj3 = g.active()
    _activate(obj3)
    _export_ui(g, os.path.join(g.OUT, "rhinoceros_untouched.sgm"))
    yield "export_untouched", "A fresh import exported without any edit: byte-identical to the source."
    line0 = obj3.get("bz_last_export", "")
    print("GUIDE untouched export:", line0)
