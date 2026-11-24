"""Boner chapter: bones, limbs and connections on the stock rhinoceros (and a rhinoceros stripped of its arm stubs for
Add Stub Socket). Run through guidecap.py; every export goes to g.OUT.

Layout prep runs from timers registered at import time (before guidecap's first tick): region overlap off (an opaque
sidebar with a draggable edge), Developer Extras on (F3 then finds the add-on's operators, so a report lands in the
Info log like a click on the button would), the sidebar opened, switched to the SGM tab and widened by 200 px.
Operators whose report or dialog is the point of a shot are run through F3 (operator search) with xdotool; the rest
through g.call. Sidebar scrolling is real mouse-wheel input over the sidebar (view2d.scroll_* from Python is not
reliable before the region's first redraw)."""
import os
import subprocess
import sys

import bmesh
import bpy
import blender_native

TESTS = os.path.join(os.path.dirname(os.path.abspath(blender_native.__file__)), "tests")   # no __file__ in an exec'd chapter


# ----------------------------------------------------------------------------------------------- layout prep (timers)
def _view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    return win, area, next(r for r in area.regions if r.type == "UI")


def _park():
    """A spot with nothing under the mouse (the timeline header's empty middle): no tooltip, no hover highlight."""
    win, _area, _ui = _view3d()
    return [str(win.x + 350), str(win.y + 921)]


def _prep1():
    bpy.context.preferences.system.use_region_overlap = False
    bpy.context.preferences.view.show_developer_ui = True
    _win, area, _ui = _view3d()
    area.spaces.active.show_region_ui = True
    return None


def _prep2():
    """Ctrl+wheel on the tab strip (the category list clamps at SGM, its last entry), then drag the sidebar's left edge
    200 px to the left. Non-blocking: the region-scale modal needs Blender's loop between the events."""
    win, _area, ui = _view3d()
    sh = win.height
    tx = win.x + ui.x + ui.width - 12
    ty = win.y + (sh - (ui.y + ui.height // 2))
    ex = win.x + ui.x - 2
    cmd = ["xdotool", "mousemove", str(tx), str(ty), "keydown", "ctrl"]
    for _ in range(8):
        cmd += ["click", "--delay", "40", "5"]
    cmd += ["keyup", "ctrl", "sleep", "0.1", "mousemove", str(ex), str(ty), "sleep", "0.2", "mousedown", "1", "sleep", "0.1"]
    for k in range(1, 5):
        cmd += ["mousemove", str(ex - 200 * k // 4), str(ty), "sleep", "0.05"]
    cmd += ["sleep", "0.1", "mouseup", "1", "sleep", "0.1", "mousemove", *_park()]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)    # ~1 s; guidecap's first step is at 4 s
    return None


bpy.app.timers.register(_prep1, first_interval=1.2)
bpy.app.timers.register(_prep2, first_interval=1.6)


# ----------------------------------------------------------------------------------------------- input helpers
def xdo(*args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=30)


def xdo_bg(*args):
    subprocess.Popen(["xdotool", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wheel(n):
    """Scroll the sidebar n wheel clicks (about 40 px each; > 0 down)."""
    win, _area, ui = _view3d()
    x = win.x + ui.x + ui.width // 2
    y = win.y + (win.height - (ui.y + ui.height // 2))
    b = "5" if n > 0 else "4"
    half = max(1, abs(n) // 2)
    xdo("mousemove", str(x), str(y), "sleep", "0.15", "click", "--repeat", str(half), "--delay", "20", b, "sleep", "0.3",
        "click", "--repeat", str(max(1, abs(n) - half)), "--delay", "20", b, "sleep", "0.3", "mousemove", *_park())


def scroll_top():
    wheel(-120)


def scroll_bottom():
    """Clamp at the bottom: the Boner panel (Combine Preview collapsed below it) fills the sidebar."""
    wheel(120)


def scroll_boner(edit=False):
    """The Boner panel's header at the top of the sidebar (20 clicks from the top; Edit Mode adds two boxes above)."""
    scroll_top()
    wheel(26 if edit else 20)


def click_last_header():
    """Open the collapsed panel at the sidebar's bottom (Combine Preview) -- non-blocking, like the drag."""
    win, _area, ui = _view3d()
    hx = win.x + ui.x + 70
    hy = win.y + (win.height - ui.y) - 14
    xdo_bg("mousemove", str(hx), str(hy), "sleep", "0.3", "mousedown", "1", "sleep", "0.12", "mouseup", "1", "sleep", "0.2",
           "mousemove", *_park())


def f3(g, label, confirm=True):
    """Run an operator the way a user could: F3 over the viewport, type its label, Return."""
    win, _area, region = g.view3d()
    cx = win.x + region.x + region.width // 2
    cy = win.y + (win.height - (region.y + region.height // 2))
    xdo("mousemove", str(cx), str(cy), "sleep", "0.2", "key", "Escape", "sleep", "0.2", "key", "F3", "sleep", "0.4")
    xdo("type", "--delay", "25", label)
    if confirm:
        xdo("sleep", "0.5", "key", "Return")


def key(name):
    xdo("sleep", "0.1", "key", name)


def dismiss(g):
    """Close a report popup left by an F3-run operator (it stays until Escape or a click, and would swallow the next
    F3): Escape with the mouse over the viewport, then park the mouse."""
    win, _area, region = g.view3d()
    cx = win.x + region.x + region.width // 2
    cy = win.y + (win.height - (region.y + region.height // 2))
    xdo("mousemove", str(cx), str(cy), "sleep", "0.2", "key", "Escape", "sleep", "0.2", "mousemove", *_park(), "sleep", "0.2")


def zoom(g, delta):
    with g.override():
        bpy.ops.view3d.zoom(delta=delta)


def say(*a):
    print("GUIDE MSG", *a, flush=True)


# ----------------------------------------------------------------------------------------------- creature helpers
def select_verts(obj, idx, ordered=False):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    for f in bm.faces:
        f.select = False
    bm.select_history.clear()
    for i in idx:
        bm.verts[int(i)].select = True
        if ordered:
            bm.select_history.add(bm.verts[int(i)])
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def limb_index(obj, cls):
    from blender_native import store, boner
    return next(i for i, x in enumerate(store.limb_ids(obj)) if boner.class_of(x) == cls)


def conn_index(obj, cls):
    from blender_native import store, boner
    return next(i for i, x in enumerate(store.conn_flags(obj)[1]) if boner.class_of(x) == cls)


def flag_bit(key_):
    from blender_native import boner
    return next(f.bit for f in boner.LIMB_FLAGS if f.key == key_)


def rig_of(obj):
    from blender_native import store
    return store.rig(obj)


def rig_edit(g, obj, arm, fn):
    """Run fn(edit_bones) in the rig's Edit Mode and come back to the creature (Object Mode)."""
    g.mode(obj, "OBJECT")
    g.mode(arm, "EDIT")
    try:
        fn(arm.data.edit_bones)
    finally:
        g.mode(arm, "OBJECT")
        arm.select_set(False)
        g.mode(obj, "OBJECT")


# ----------------------------------------------------------------------------------------------- the chapter
def steps(g):
    from blender_native import store, boner
    from ICParse.sgm import parser as sgm_parser
    from ICParse.sgm.creature.model import ring_corners

    RHINO = g.STOCK + "/rhinoceros.sgm"
    stock = sgm_parser.read(RHINO)
    M = boner.CLASS_MASK
    scene = bpy.context.scene
    s = scene.bz_boner

    bpy.ops.bz.import_sgm(filepath=RHINO)
    obj = g.active()
    arm = rig_of(obj)
    arm.show_in_front = True
    scene.bz_show_connections = True
    g.frame([obj])
    g.orbit(20)
    s.mode = "LIMBS"
    xdo("mouseup", "1", "sleep", "0.1")          # in case the sidebar drag is still finishing
    scroll_boner()
    yield "overview", ("The stock rhinoceros in Boner > Limbs: the surface shaded By Limb, the 12 graft rings drawn "
                       "(Show Connections), the limb list with class, patch and bone counts.")

    # ---- Limbs: the active limb's boxes and bones
    lf = limb_index(obj, 0x25)
    g.call(bpy.ops.bz.limb_set_active, index=lf)
    s.bone = ""
    res, msg = g.call(bpy.ops.bz.limb_select_bones)
    say("select bones", res, msg)
    yield "limbs_lfleg", ("Rhinoceros_LFLeg active: its classification boxes (Left, Front, Leg) and its 7 bones; Select "
                          "Bones selected them on the rig and made the first the panel's Bone.")

    # Set Root Bone on a stock creature: every other root is refused (record the exact words for the text)
    res, msg = g.call(bpy.ops.bz.limb_set_root, index=lf, root="Front_leg2_l")
    say("set root Front_leg2_l:", res, msg)
    res, msg = g.call(bpy.ops.bz.limb_set_root, index=lf, root="spine3")
    say("set root spine3:", res, msg)

    # Add Active Bone through F3, so the report (moved bones + the combiner warning) shows in the UI
    torso, head = limb_index(obj, 0x800), limb_index(obj, 0x40)
    g.call(bpy.ops.bz.limb_set_active, index=torso)
    s.bone = "nose"
    f3(g, "Add Active Bone")
    yield "limb_move_bone", ("Add Active Bone with the Torso active and 'nose' as the Bone: the report names the move "
                             "and warns that 218 Head points now sit on a Torso bone (what ComboTest.exe does with it).")
    say("last bone edit:", obj.get("bz_last_bone_edit"), list(obj.get("bz_last_bone_warnings", [])))
    dismiss(g)
    g.call(bpy.ops.bz.limb_set_active, index=head)
    res, msg = g.call(bpy.ops.bz.limb_add_bone)          # back into the Head: the source's list again
    say("move back:", res, msg, obj.get("bz_last_bone_edit"))
    res, msg = g.call(bpy.ops.bz.limb_remove_bone, index=torso, bone="spine3")
    say("remove spine3 (host):", res, msg)

    # classification boxes: LFLeg -> Mid (one side only: warned), then back to Front
    g.call(bpy.ops.bz.limb_set_active, index=lf)
    res, msg = g.call(bpy.ops.bz.limb_flag, index=lf, bit=flag_bit("MID"))
    say("LFLeg Mid:", res, msg, "| follow:", obj.get("bz_last_conn_follow"), "| socket:", obj.get("bz_last_socket_warning"))
    yield "limb_reclass", ("Position: Mid ticked on Rhinoceros_LFLeg: the row reads L Mid Leg, its connection's Child "
                           "class followed in the same undo step, and the operator warned that the Right partner is "
                           "still R Front Leg.")
    res, msg = g.call(bpy.ops.bz.limb_flag, index=lf, bit=flag_bit("FRONT"))
    say("LFLeg Front again:", res, msg)
    for key_ in ("TAIL", "TORSO"):
        i = limb_index(obj, 0x80 if key_ == "TAIL" else 0x800)
        res, msg = g.call(bpy.ops.bz.limb_flag, index=i, bit=flag_bit(key_))
        say(f"untick {key_}:", res, msg)
    for slot in ("LMLeg", "RMLeg"):
        res, msg = g.call(bpy.ops.bz.add_standard_limb, slot=slot)
        say(f"add standard {slot}:", res, msg)

    # Delete Limb through F3: the dialog, then the refusal
    f3(g, "Delete Limb")
    key("Return")
    yield "limb_delete_refused", ("Delete on Rhinoceros_LFLeg is refused: a connection still attaches a limb of its "
                                  "class (delete or re-point the connection first).")
    res, msg = g.call(bpy.ops.bz.delete_limb, index=lf)
    say("delete LFLeg:", res, msg)
    dismiss(g)

    # ---- Connections
    g.mode(obj, "EDIT")
    s.mode = "CONNECTIONS"
    hc = conn_index(obj, 0x40)
    g.call(bpy.ops.bz.conn_set_active, index=hc)
    scroll_boner(edit=True)
    yield "conn_list", ("Boner > Connections in Edit Mode: every socket with Parent > Child class, corner count, host "
                        "bone and status; the active one (Rhinoceros_Head) is drawn thick in the viewport, the others "
                        "dimmed. Below: its Parent / Child boxes, host bone and ring.")

    lfc = conn_index(obj, 0x25)
    g.call(bpy.ops.bz.conn_set_active, index=lfc)
    res, msg = g.call(bpy.ops.bz.conn_set_host, index=lfc, bone="jaw1")
    say("host jaw1:", res, msg)
    res, msg = g.call(bpy.ops.bz.conn_set_host, index=lfc, bone="spine2")
    say("host spine2:", res, msg)
    res, msg = g.call(bpy.ops.bz.conn_set_host, index=lfc, default=True)
    say("host default:", res, msg, store.connections(obj)[lfc][1])

    # delete the Head connection, create it again, re-pick its ring from a scrambled selection
    g.call(bpy.ops.bz.conn_set_active, index=hc)
    res, msg = g.call(bpy.ops.bz.delete_connection, index=hc)
    say("delete head conn:", res, msg, "| socket:", obj.get("bz_last_socket_warning"))
    yield "conn_deleted", ("The Head connection deleted: the list warns that Rhinoceros_Head has no connection, which "
                           "makes ComboTest.exe refuse the whole creature.")
    hl = limb_index(obj, 0x40)
    res, msg = g.call(bpy.ops.bz.create_connection, limb=str(hl))
    say("create for head:", res, msg, store.conn_rows(obj)[-1])
    yield "conn_created", ("Create for Limb > Rhinoceros_Head: a new connection, Parent Torso, Child Head, host bone "
                           "neck1 (the parent of the Head's root bone), status 'no ring' until its ring is picked.")
    corners = ring_corners(stock.limb_connections[hc].vertex_indices)
    scrambled = corners[::-1][2:] + corners[::-1][:2]
    select_verts(obj, scrambled, ordered=True)
    g.frame()
    zoom(g, -2)
    ci = len(store.connections(obj)) - 1
    res, msg = g.call(bpy.ops.bz.set_connection_ring, conn=str(ci))
    say("set ring:", res, msg, store.connections(obj)[-1][2] == corners)
    yield "conn_ring_set", ("The six ring corners Shift+clicked in order (here in reverse, starting off the stock "
                            "corner) and From Selection: the ring is wound into the torso and started where the stock "
                            "creatures start, so it is the stock ring again -- status OK.")

    # ---- Add Stub Socket on a rhinoceros that has no arm stubs at all (limbs, connections and bones stripped)
    sys.path.insert(0, TESTS)
    import stub_socket_test as ST                        # the pure test's strip helper (ICParse only)
    NOARMS = os.path.join(g.OUT, "rhinoceros_noarms.sgm")
    ST.strip_stubs(RHINO, {0x15, 0x16}, NOARMS)
    g.mode(obj, "OBJECT")
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.objects.remove(arm, do_unlink=True)
    bpy.ops.bz.import_sgm(filepath=NOARMS)
    obj = g.active()
    arm = rig_of(obj)
    arm.show_in_front = True
    g.frame([obj])
    g.orbit(20)
    g.mode(obj, "EDIT")
    s.mode = "CONNECTIONS"
    quad_l = ring_corners(next(c for c in stock.limb_connections if c.flags2 & M == 0x15).vertex_indices)
    quad_r = ring_corners(next(c for c in stock.limb_connections if c.flags2 & M == 0x16).vertex_indices)
    select_verts(obj, quad_l)
    g.frame()
    zoom(g, -4)
    scroll_bottom()
    yield "stub_before", ("A rhinoceros stripped of its arm stubs (11 limbs, 52 bones): in Edit Mode the four corners of "
                          "one Torso patch at the shoulder are selected; Add Stub Socket lists the classes no limb or "
                          "connection has (LFArm, RFArm).")
    res, msg = g.call(bpy.ops.bz.add_stub_socket, slot="LFArm")
    say("stub LFArm:", res, msg, "|", obj.get("bz_last_stub_socket"), "| socket:", obj.get("bz_last_socket_warning"))
    scroll_bottom()
    yield "stub_after", ("Add Stub Socket > LFArm: one undo step made the stub limb Rhinoceros_LFArm (no patches, one "
                         "bone), its socket on that patch (ring wound like the stock ones, host neck1) and, since the "
                         "skeleton had no front_arm1_l, a new stub bone under the host at the patch centre.")
    select_verts(obj, quad_r)
    res, msg = g.call(bpy.ops.bz.add_stub_socket, slot="RFArm")
    say("stub RFArm:", res, msg, "|", obj.get("bz_last_stub_socket"), "| socket:", obj.get("bz_last_socket_warning"))
    select_verts(obj, [])

    # ---- Bones
    g.mode(obj, "OBJECT")
    g.frame([obj])
    g.orbit(20)
    s.mode = "BONES"
    scroll_boner()
    s.bone = "tail1"
    f3(g, "Rename Bone")
    yield "bone_rename_dialog", ("Rename Bone (the pencil next to the Bone field) on tail1: the dialog warns that the "
                                 "game looks this name up (tail seam, TailWag, tail clearance) before you confirm.")
    dismiss(g)                                        # Cancel the dialog: Escape with the mouse over it
    res, msg = g.call(bpy.ops.bz.rename_bone, bone="ear_L", new_name="ear_left")
    say("rename ear_L:", res, msg)
    res, msg = g.call(bpy.ops.bz.rename_bone, bone="antler_l", new_name="EAR_R")
    say("rename clash:", res, msg)
    s.bone = "head"
    res, msg = g.call(bpy.ops.bz.add_bone, parent="head", name="horn_l", offset=(0.0, 0.02, 0.0), length=0.12)
    say("add bone:", res, msg, "|", obj.get("bz_last_add_bone"))
    # skin the 8 highest head points to it
    vb = store.vert_bone(obj)
    hi = store.bone_index(obj, "head")
    pts = sorted((i for i in range(len(obj.data.vertices)) if int(vb[i]) == hi), key=lambda i: -obj.data.vertices[i].co.z)[:8]
    g.mode(obj, "EDIT")
    select_verts(obj, pts)
    res, msg = g.call(bpy.ops.bz.skin_assign_active)
    say("assign to horn_l:", res, msg)
    g.mode(obj, "OBJECT")
    g.frame([arm])
    zoom(g, 2)
    scroll_boner()
    yield "bone_add", ("Add Bone under Active (parent head, name horn_l, 2 cm along the head's tail, 12 cm long) and 8 "
                       "head points assigned to it: the Bones tab shows 'pending: renamed ear_L -> ear_left' from the "
                       "rename and 'horn_l - 8 points - parent head'; the new bone sits on the rig above the head.")

    # Blender's own Add: a leaf under endtail, pending until Adopt Added Bones
    def tail_tip(eb):
        e = eb["endtail"]
        b = eb.new("tail_tip")
        b.head = e.tail.copy()
        b.tail = e.tail + e.vector
        b.parent = e
    rig_edit(g, obj, arm, tail_tip)
    g.frame([obj])
    g.orbit(20)
    scroll_boner()
    yield "bone_pending", ("A bone made with Blender's own tools (tail_tip, a leaf under endtail, added in the rig's "
                           "Edit Mode): the Bones tab lists it as pending with the Adopt button; Export would take it "
                           "into the Tail limb anyway, Adopt Added Bones stamps it now so the skin tools can use it.")
    res, msg = g.call(bpy.ops.bz.adopt_bones)
    say("adopt:", res, msg)
    s.mode = "LIMBS"
    g.call(bpy.ops.bz.limb_set_active, index=limb_index(obj, 0x80))
    scroll_boner()
    yield "bone_adopted", ("After Adopt Added Bones, Boner > Limbs: the Tail limb lists tail_tip after its stock bones "
                           "(an added bone always follows its parent's limb).")

    # refusal: a bone under a stub bone (through F3: the dialog, then the error)
    s.mode = "BONES"
    s.bone = "top_wing_r"
    f3(g, "Add Bone under Active")
    key("Return")
    yield "bone_refused", ("Add Bone under Active with top_wing_r (a stub bone) as the parent is refused: a stub bone "
                           "is a stub limb's only bone, written after every other bone.")
    dismiss(g)

    # the finished creature exports: record the summary
    res, msg = g.call(bpy.ops.bz.export_sgm, filepath=os.path.join(g.OUT, "rhinoceros_boner.sgm"))
    say("export:", res, msg, "|", obj.get("bz_last_export"))

    # ---- Validate and the combine checks on a creature ComboTest.exe refuses
    s.mode = "LIMBS"
    lf = limb_index(obj, 0x25)
    res, msg = g.call(bpy.ops.bz.limb_flag, index=lf, bit=flag_bit("MID"))
    say("LFLeg Mid (kept):", res, msg, "| socket:", obj.get("bz_last_socket_warning"))
    ac = conn_index(obj, 0x15)
    res, msg = g.call(bpy.ops.bz.delete_connection, index=ac)
    say("delete LFArm socket:", res, msg, "| socket:", obj.get("bz_last_socket_warning"))
    res, msg = g.call(bpy.ops.bz.limb_remove_bone, index=limb_index(obj, 0x40), bone="nose")
    say("remove nose:", res, msg, obj.get("bz_last_bone_edit"))
    s.mode = "CONNECTIONS"
    scroll_boner()
    scene.bz_combine.validate_level = "QUICK"
    f3(g, "Validate (will it load?)")                  # queued input: runs after this step's shot, ~2 s (Quick tier)
    yield "conn_alerts", ("Boner > Connections after three edits (LFLeg re-classed Mid on its own, the LFArm socket "
                          "deleted, nose taken out of the Head): the tab names the limb without a connection and the "
                          "Left limb without its Right partner -- both make ComboTest.exe refuse the creature.")
    yield "validate_status", ("Validate (will it load?) with Combine Checks: Quick, run from the Tools box: the status bar "
                              "sums the report up; the lines themselves are in the Info editor.")
    say("validation:", list(obj.get("bz_validation", [])))
    say("combine summary:", obj.get("bz_combine_summary"))
    win, area, ui = _view3d()
    area.type = "INFO"
    yield "validate_info", ("Validate (Combine Checks: Quick) read in the Info editor (the viewport area switched to it "
                            "for the shot): STOCK lines for the bone in no limb and the added bone owning nothing, the "
                            "combiner's COMBINE ERROR lines for the socketless limb and the unmirrored Left limb, and "
                            "the Quick tier's summary.")
    area.type = "VIEW_3D"
    scene.bz_combine.role = "TORSO"
    scene.bz_combine.other_kind = "STOCK"
    wheel(80)
    click_last_header()
    yield "combine_panel", "Combine Preview opened (its header at the sidebar's bottom)."
    wheel(80)
    f3(g, "Preview Combination")
    yield "combine_refused", ("Combine Preview > Preview with the stock cheetah: instead of drawing a combination the "
                              "real tool never makes, the preview fails with 'ComboTest.exe does not take ...' and the "
                              "first reason.")
    dismiss(g)

    # ---- deleting one of the creature's own bones (C8): Blender's X in the rig's Edit Mode, and Export takes it
    def kill(name):
        def fn(eb):
            eb.remove(eb[name])
        return fn
    rig_edit(g, obj, arm, kill("nose"))
    scroll_top()
    wheel(8)
    out = os.path.join(g.OUT, "rhinoceros_boner.sgm")
    r, e = g.call(bpy.ops.bz.export_sgm, filepath=out)
    say("export after deleting nose:", r, e, "|", obj.get("bz_last_export", ""))
    yield "bone_deleted", ("One of the creature's OWN bones deleted with Blender's own X in the rig's Edit Mode "
                           "(nose, a leaf of the head). This exports: its record is dropped, its control points go "
                           "to its parent with a warning naming the count, it comes off the limb lists and the IK "
                           "chains, and the kept clips that keyed it are rebuilt without its track.")
    rig_name = arm.name
    n = 0
    while bpy.data.objects[rig_name].data.bones.get("nose") is None and n < 6:
        bpy.ops.ed.undo()                                # a memfile step: look the objects up again by name
        n += 1
    say("undo steps to restore nose:", n, bpy.data.objects[rig_name].data.bones.get("nose") is not None)

    # ---- and the refusal that remains: a bone the game looks up by name
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    arm = store.rig(obj)
    rig_edit(g, obj, arm, kill("tail1"))
    scroll_top()
    wheel(8)
    r, e = g.call(bpy.ops.bz.export_sgm, filepath=os.path.join(g.OUT, "rhinoceros_notail1.sgm"))
    say("export after deleting tail1:", r, e)
    yield "bone_deleted_alert", ("Deleting tail1 instead is refused, in the rig box and at Export: the game looks "
                                 "that name up. The same goes for the root bone, a stub bone, a connection's host "
                                 "bone, the only bone of a limb, and a re-parent that would split a limb in two.")
