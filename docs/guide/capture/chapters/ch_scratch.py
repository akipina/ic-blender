"""Chapter 'scratch': an animal from scratch -- a crude rhino from New Creature (From Scratch) to an exported,
editor-loadable .sgm: shape the scaffold, pull a horn and give it a bone, check limbs / connections / skinning,
key a head bob, Validate, Combine Preview with the stock bull, Export."""
import math
import os
import subprocess

import numpy as np
import bpy
import bmesh

from blender_native import store, ops, actions as bzactions, combocore, combopreview


def note(*a):
    print("GUIDE NOTE", *a, flush=True)


def settle(g):
    """Draw the window now, so a slow first redraw never delays the capture past the next step."""
    try:
        bpy.context.view_layer.update()
        with g.override():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    except Exception as e:
        note("settle failed:", e)


def shot(g, name, caption):
    """Settle, take the shot, then an idle 'zz_<name>' shot (deleted after the run) so a capture that comes late
    still sees this state."""
    settle(g)
    for a in bpy.context.window_manager.windows[0].screen.areas:
        a.tag_redraw()
    settle(g)
    yield name, caption
    yield "zz_" + name, "(idle, deleted)"


def ui_region(g):
    win, area, region = g.view3d()
    return win, area, next(r for r in area.regions if r.type == "UI")


def scroll_sidebar(g, steps, up=False):
    """Scroll the N sidebar by wheel steps (about 36 px each): the Boner panel sits below the fold."""
    win, area, ui = ui_region(g)
    op = bpy.ops.view2d.scroll_up if up else bpy.ops.view2d.scroll_down
    try:
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=ui):
            for _ in range(int(steps)):
                op()
        ui.tag_redraw()
        area.tag_redraw()
    except Exception as e:
        note("scroll_sidebar failed:", e)


def look(g, az=0.0, elev=0.0, persp=True):
    """Point the viewport: az = degrees around Z from the Right view (negative = toward the head at -Y),
    elev = degrees above the horizon; then frame the selection."""
    from mathutils import Euler
    win, area, region = g.view3d()
    rv = area.spaces.active.region_3d
    rv.view_perspective = "PERSP" if persp else "ORTHO"
    rv.view_rotation = Euler((math.radians(90 - elev), 0.0, math.radians(90 + az)), "XYZ").to_quaternion()
    with g.override():
        bpy.ops.view3d.view_selected()


def zoom(g, factor, dx=0.0, dz=0.0):
    win, area, region = g.view3d()
    rv = area.spaces.active.region_3d
    rv.view_distance *= factor
    loc = rv.view_location.copy()
    loc.x += dx
    loc.z += dz
    rv.view_location = loc


def mouse_to_viewport(g):
    win, area, region = g.view3d()
    x = win.x + region.x + region.width // 2
    y = win.y + (win.height - (region.y + region.height // 2))
    subprocess.Popen(["xdotool", "mousemove", str(x), str(y)])


def bottom_area():
    win = bpy.context.window_manager.windows[0]
    areas = [a for a in win.screen.areas if a.width > win.width // 2]
    return win, min(areas, key=lambda a: (a.y, -a.width))


def drag_timeline_up(g, px=260):
    """Drag the edge between the 3D viewport and the Timeline up with real mouse events (xdotool)."""
    win, bottom = bottom_area()
    x = win.x + bottom.x + bottom.width // 2
    y = win.y + (win.height - (bottom.y + bottom.height)) - 1
    subprocess.Popen(["xdotool", "mousemove", str(x), str(y), "sleep", "0.15", "mousedown", "1", "sleep", "0.15",
                      "mousemove_relative", "--", "0", str(-int(px // 2)), "sleep", "0.15",
                      "mousemove_relative", "--", "0", str(-int(px - px // 2)), "sleep", "0.15", "mouseup", "1",
                      "sleep", "0.1", "mousemove", str(x), str(y - px - 200)])


def dope_sheet(g, mode="DOPESHEET"):
    win, bottom = bottom_area()
    bottom.type = "DOPESHEET_EDITOR"
    sp = bottom.spaces.active
    try:
        sp.mode = mode
        sp.dopesheet.show_only_selected = False
        sp.show_region_channels = True
    except Exception as e:
        note("dope sheet setup:", e)
    return bottom


def set_selection(obj, idx):
    idx = set(int(i) for i in idx)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in idx
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def edit_verts(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return bm, np.array([v.co[:] for v in bm.verts], dtype=np.float64)


def write_verts(obj, bm, V):
    for v, co in zip(bm.verts, V):
        v.co = (float(co[0]), float(co[1]), float(co[2]))
    bmesh.update_edit_mesh(obj.data)


def point_sets(obj):
    """{limb name: set of control-point indices its patches use}."""
    patches = store.patches(obj)
    pl = store.patch_limb(obj)
    names = store.limb_names(obj)
    by = {n: set() for n in names}
    for p, l in zip(patches, pl):
        if l >= 0:
            by[names[l]].update(int(i) for i in p)
    return by


def steps(g):
    scene = bpy.context.scene
    scene.bz_combine.validate_level = "QUICK"
    bpy.context.preferences.view.smooth_view = 0
    g.sidebar()
    yield "warmup", "(sidebar warm-up, not used in the chapter)"
    drag_timeline_up(g, 250)
    yield "warmup2", "(timeline resize, not used in the chapter)"
    src = os.path.join(g.OUT, "rhino.sgm")
    done = os.path.join(g.OUT, "rhino_done.sgm")
    for f in (src, done):
        if os.path.exists(f):
            os.remove(f)

    # ------------------------------------------------------------------ 1. New Creature (From Scratch)
    res, err = g.call(bpy.ops.bz.new_creature, filepath=src, creature_name="Rhino", detail=1)
    note("new_creature", res, err)
    obj = g.active()
    arm = store.rig(obj)
    arm.show_in_front = True
    note("scaffold:", len(obj.data.vertices), "control points,", int(obj[store.KEY_NPATCH]), "patches,",
         len(arm.data.bones), "bones,", len(arm.get("bz_clip_actions", [])), "clip Actions,",
         len(store.connections(obj)), "rings,", len(store.limb_names(obj)), "limbs")
    note("limbs:", store.limb_names(obj))
    note("rings:", [(n, b, len(r)) for n, b, r in store.connections(obj)])
    g.frame([obj])
    look(g, -35, 18)
    zoom(g, 0.8)
    mouse_to_viewport(g)
    yield from shot(g, "new", "New Creature (From Scratch): the Rhino scaffold as it comes -- a connected torso, head, tail and four legs, textured with its labelled skin.")

    look(g, 0, 0, persp=False)
    zoom(g, 0.8)
    scene.bz_shade_mode = "LIMB"
    scene.bz_show_connections = True
    yield from shot(g, "new_side", "Side view shaded By Limb with Show Connections on: the 13 limbs (7 with patches, 6 stubs) and the 12 graft rings the scaffold already carries.")

    # ------------------------------------------------------------------ 2. shape it in Edit Mode
    scene.bz_show_connections = False
    scene.bz_shade_mode = "TEXTURED"
    scene.bz_show_handles = True
    g.mode(obj, "EDIT")
    set_selection(obj, [])
    yield from shot(g, "edit_before", "Tab into Edit Mode: the vertices are the file's control points (corners gold, handles blue); the surface is drawn translucent over them.")

    sets = point_sets(obj)
    torso = sets["Rhino_Torso"]
    head = sets["Rhino_Head"] - torso
    tail = sets["Rhino_Tail"] - torso
    legs = {k: sets[k] - torso for k in ("Rhino_LFLeg", "Rhino_RFLeg", "Rhino_LRLeg", "Rhino_RRLeg")}
    torso_only = torso - head - tail - set().union(*legs.values())
    head_ring = torso & sets["Rhino_Head"]
    tail_ring = torso & sets["Rhino_Tail"]
    bm, V = edit_verts(obj)
    V[:, 2] *= 0.78                                   # squat: shorter legs, lower body (feet stay on the ground)
    V[:, 0] *= 1.35                                   # wider everywhere: thick legs and torso
    idx = np.array(sorted(torso_only))
    tc = V[idx].mean(0)
    V[idx, 0] = tc[0] + (V[idx, 0] - tc[0]) * 1.15     # a barrel torso
    V[idx, 2] = tc[2] + (V[idx, 2] - tc[2]) * 1.25
    hc = V[list(head_ring)].mean(0)
    idx = np.array(sorted(head))
    V[idx] = hc + (V[idx] - hc) * 1.35                 # bigger head, scaled about the neck ring
    V[idx, 2] -= 0.12                                  # ... carried lower
    V[idx, 1] -= 0.05
    trc = V[list(tail_ring)].mean(0)
    idx = np.array(sorted(tail))
    V[idx, 1] = trc[1] + (V[idx, 1] - trc[1]) * 0.45   # stubby tail
    V[idx, 2] -= 0.05 + 0.3 * (V[idx, 1] - trc[1])     # ... drooping
    for pts in legs.values():
        idx = np.array(sorted(pts))
        lc = V[idx].mean(0)
        V[idx, 0] = lc[0] + (V[idx, 0] - lc[0]) * 1.3   # thick legs
        V[idx, 1] = lc[1] + (V[idx, 1] - lc[1]) * 1.3
    write_verts(obj, bm, V)
    note("shaped: torso-only", len(torso_only), "head", len(head), "tail", len(tail), "leg points", [len(v) for v in legs.values()])
    yield from shot(g, "edit_after", "The same view after the scripted moves: whole regions of control points scaled and shifted (squat, wide, big low head, stubby tail). Every patch shares its points, so the body stays watertight.")

    g.mode(obj, "OBJECT")
    g.frame([obj])
    look(g, -35, 18)
    scene.bz_show_handles = False
    yield from shot(g, "shape_persp", "Back in Object Mode: the reshaped body. Crude, but it is a rhino-shaped, closed, rigged creature. The bones stayed where they were (the skeleton is edited separately).")

    # ------------------------------------------------------------------ 3. a horn: pull head points, add a bone, skin
    g.mode(obj, "EDIT")
    bm, V = edit_verts(obj)
    hp = np.array(sorted(head))
    ymin = V[hp, 1].min()
    hz = V[hp, 2].mean()
    horn = hp[(V[hp, 1] < ymin + 0.2) & (V[hp, 2] > hz)]
    set_selection(obj, horn)
    note("horn points:", len(horn))
    with g.override():
        bpy.ops.view3d.view_selected()
    zoom(g, 3.0)
    can_extrude = bpy.ops.bz.extrude_patch.poll()
    note("Extrude Patch poll on the closed scaffold:", can_extrude)
    scroll_sidebar(g, 12)
    yield from shot(g, "horn_select", "The top-front control points of the head cap selected (22 points). Extrude Patch (Modeling box) is greyed out: the scaffold is closed, and Extrude Patch only grows from an open patch side.")

    bm, V = edit_verts(obj)
    V[horn, 1] -= 0.28
    V[horn, 2] += 0.32
    write_verts(obj, bm, V)
    yield from shot(g, "horn_pulled", "G-grab them forward and up (here scripted: -0.28 m along Y, +0.32 m up): the head cap bulges into a horn.")

    g.mode(obj, "OBJECT")
    s = scene.bz_boner
    s.mode = "BONES"
    s.bone = "head"
    hb = arm.data.bones["head"]
    target = V[horn].mean(0)
    Mw = np.array(arm.matrix_world @ hb.matrix_local)
    tail_w = np.array(arm.matrix_world @ hb.tail_local)
    off = Mw[:3, :3].T @ (target - tail_w)
    res, err = g.call(bpy.ops.bz.add_bone, parent="head", name="horn", offset=tuple(float(x) for x in off), length=0.25)
    note("add_bone", res, err, "|", obj.get("bz_last_add_bone"))
    arm.data.show_names = True
    settle(g)
    scroll_sidebar(g, 60)
    yield from shot(g, "horn_bone", "Boner > Bones > Add Bone under Active with 'head' active and the name 'horn': a new deforming leaf bone at the horn (rig drawn in front, bone names on). It becomes the active bone, with 0 points so far.")

    g.mode(obj, "EDIT")
    set_selection(obj, horn)
    s.bone = "horn"
    res, err = g.call(bpy.ops.bz.skin_assign_active)
    note("skin_assign_active", res, err, "horn owns", int((store.vert_bone(obj) == store.bone_index(obj, "horn")).sum()),
         "unassigned", ops.skin_stats_cached(obj).unassigned)
    settle(g)
    scroll_sidebar(g, 60)
    yield from shot(g, "horn_skinned", "With the 22 horn points still selected and 'horn' the active bone, Assign Selected skins them to it. The By Bone overlay colours them with the horn's colour (active bone bright, the rest dimmed); the tab reads 'horn · 22 points · parent head'.")

    # ------------------------------------------------------------------ 4. limbs and connections
    arm.data.show_names = False
    g.mode(obj, "OBJECT")
    g.frame([obj])
    look(g, -35, 18)
    s.mode = "LIMBS"
    names = store.limb_names(obj)
    hi = names.index("Rhino_Head")
    res, err = g.call(bpy.ops.bz.limb_set_active, index=hi)
    res, err = g.call(bpy.ops.bz.rename_limb, index=hi, name="Rhino_HornedHead")
    note("rename_limb", res, err, store.limb_names(obj))
    settle(g)
    scroll_sidebar(g, 60)
    scroll_sidebar(g, 8, up=True)
    yield from shot(g, "limbs", "Boner > Limbs: the scaffold's 13 limbs with their class labels and patch / bone counts; the head limb renamed to Rhino_HornedHead with Rename. Its classification boxes (Head) are below the list.")

    s.mode = "CONNECTIONS"
    scene.bz_show_connections = True
    ci = [r.name for r in store.conn_rows(obj)].index("Rhino_Head")
    res, err = g.call(bpy.ops.bz.conn_set_active, index=ci)
    note("conn_set_active", res, err, [(r.name, r.bone, len(r.corners)) for r in store.conn_rows(obj)])
    with g.override():
        bpy.ops.view3d.view_selected()
    zoom(g, 0.8)
    settle(g)
    scroll_sidebar(g, 60)
    scroll_sidebar(g, 10, up=True)
    yield from shot(g, "connections", "Boner > Connections with Show Connections on: the Head ring (6 corners on neck1, Torso > Head) drawn thick and bright, the other 11 rings dimmed. Each ring's status is checked against the stock layout.")

    # ------------------------------------------------------------------ 5. animation
    scene.bz_show_connections = False
    s.mode = "BONES"
    scene.bz_shade_mode = "TEXTURED"
    scroll_sidebar(g, 40, up=True)
    g.mode(obj, "OBJECT")
    acts = bzactions.actions_of(arm)
    note("actions:", sorted(a.name for a in acts))
    idx = list(obj.get("bz_anims", [])).index("Idle1")
    res, err = g.call(bpy.ops.bz.load_anim, anim=str(idx))
    note("load_anim Idle1", res, err, "frame range", scene.frame_start, scene.frame_end)
    dope_sheet(g, "DOPESHEET")
    scroll_sidebar(g, 6)
    scene.frame_set(6)
    yield from shot(g, "actions", "The scaffold's 9 standard clips are Actions on the rig: Load Animation > Idle1 makes it the active Action (the panel says 'active: Idle1'); the Timeline turned into a Dope Sheet shows its keys, one per clip frame, and the viewport plays the pose.")

    bob = bpy.data.actions.new("headbob")
    bob.use_fake_user = True
    bzactions._assign(arm, bob)
    for bone, amp in (("neck2", 0.15), ("neck3", 0.15), ("head", 0.35)):
        pb = arm.pose.bones[bone]
        pb.rotation_mode = "QUATERNION"
        for f, a in ((1, 0.0), (11, amp), (21, 0.0)):
            pb.rotation_quaternion = (math.cos(a / 2), math.sin(a / 2), 0.0, 0.0)
            pb.keyframe_insert("rotation_quaternion", frame=f)
    scene.frame_start, scene.frame_end = 1, 21
    scene.frame_set(11)
    plan = bzactions.plan_export(arm)
    note("plan_export kept", len(plan.kept), "encode", [c for _a, c in plan.encode], "drop", plan.drop, "warnings", plan.warnings)
    yield from shot(g, "headbob", "A new Action 'headbob' (21 frames) keying neck2, neck3 and head at frames 1, 11 and 21; the timeline scrubbed to frame 11 shows the head dipping. Export writes it as a tenth clip.")

    # ------------------------------------------------------------------ 6. Validate, Combine Preview, Export
    scene.frame_set(1)
    res, err = g.call(bpy.ops.bz.validate)
    val = list(obj.get("bz_validation", []))
    note("validate", res, err)
    for v in val:
        note("VALIDATE LINE:", v)
    note("combine summary:", obj.get("bz_combine_summary"))
    settle(g)
    scroll_sidebar(g, 60, up=True)
    scroll_sidebar(g, 11)
    yield from shot(g, "validate", "Tools > Validate (will it load?) with Combine Checks: Quick. The verdict goes to the Info log: nothing blocks loading, two advisories, and the Quick tier's 13 stock combines all pass.")

    sc = scene.bz_combine
    sc.other_kind, sc.other_stock_name, sc.role = "STOCK", "bull", combocore.ROLE_TORSO
    for grp in combocore.PARTS:
        setattr(sc, f"part_{grp}", grp == "head")
    combopreview.donor_index(sc, build=True)
    res, err = g.call(bpy.ops.bz.combine_preview)
    pr = combopreview._ghost.get("preview")
    note("combine_preview", res, err, pr.ok if pr else None, pr.error if pr else None, pr.header() if pr else "",
         "|", pr.summary() if pr else "", pr.dropped_groups if pr else "")
    if pr is not None and pr.ok:
        for row in pr.slots:
            note("COMBINE SLOT:", row)
        for w in pr.warnings:
            note("COMBINE WARN:", w)
        for n in pr.notes:
            note("COMBINE NOTE:", n)
    look(g, -55, 15)
    zoom(g, 1.6, dx=1.3)
    yield from shot(g, "combine", "Combine Preview: This Creature Is the Torso, Stock > bull, Parts: Head, Preview. The game's combiner grafts the bull's head onto the rhino at the neck ring and draws the result as a ghost beside the creature (+X).")

    g.call(bpy.ops.bz.combine_clear)
    look(g, -35, 18)
    res, err = g.call(bpy.ops.bz.export_sgm, filepath=done)
    note("export", res, err, "|", obj.get("bz_last_export"))
    note("exists", os.path.exists(done), os.path.getsize(done) if os.path.exists(done) else 0)
    settle(g)
    scroll_sidebar(g, 60)
    yield from shot(g, "export", "Export SGM (Native Patches) to rhino_done.sgm: the report line says what changed against the scaffold file (skinning rebuilt, 1 bone added, 1 limb renamed, 9 clips kept and 1 written).")
