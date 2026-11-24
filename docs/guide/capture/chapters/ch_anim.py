"""Chapter `anim`: playing, editing and creating clips on the stock rhinoceros (clips = Blender Actions).

Run (a dedicated display number, and this run's own X cookie handed past the snap launcher -- see steps()):
    xvfb-run -n 141 -s "-screen 0 1600x1000x24" sh -c \
      'GUIDE_XAUTH=$XAUTHORITY exec /snap/bin/blender --python guidecap.py -- chapters/ch_anim.py'

Validate and Export run through the UI (session-only F6 / F7 bindings pressed with xdotool, as ch_start does) because
reports of operators called from Python never reach the Info editor / status bar. A UI-triggered operator runs in the
event loop AFTER the step returns, so its shot is yielded twice: the first capture can only land after the next tick,
which is therefore a no-op that yields the same shot again; ``finish`` drops the duplicate list entries.
"""
import math
import os
import subprocess

import bpy
from mathutils import Quaternion

from blender_native import actions as bzactions, ops as bzops, store

MELEE = "Melee1"
HEAD_FRAMES = ((9, 0.5), (12, 1.0), (15, 0.5))      # (Blender frame, weight) of the scripted head edit
HEAD_DEG = 40.0
DROP = set()                                          # shots yielded only to pace the driver (deleted at the end)


def _win():
    return bpy.context.window_manager.windows[0]


def settle(g):
    """Draw the window now, so the redraw after the yield is quick and the capture lands before the next step."""
    try:
        with g.override():
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    except Exception as e:
        print("GUIDE settle", e)


def split_bottom(g, kind, factor=0.36):
    """Split the 3D viewport horizontally; the lower part becomes an editor of type ``kind``. Returns the area
    (or the Timeline area, retyped, when the split is refused)."""
    win, area, region = g.view3d()
    try:
        with g.override():
            bpy.ops.screen.area_split(direction="HORIZONTAL", factor=factor)
        low = sorted([a for a in win.screen.areas if a.type == "VIEW_3D"], key=lambda a: a.y)[0]
    except Exception as e:                            # pragma: no cover - depends on the window manager
        print(f"GUIDE split refused ({e}); using the timeline area")
        low = next(a for a in win.screen.areas if a.ui_type == "TIMELINE")
    low.type = kind
    return low


def retype(area, kind, mode=None, only_selected=False):
    area.type = kind
    if mode is not None:
        try:
            area.spaces.active.mode = mode
        except Exception as e:
            print(f"GUIDE mode {mode} on {kind} refused: {e}")
    try:
        area.spaces.active.dopesheet.show_only_selected = only_selected
    except Exception:
        pass
    return area


def show_info(g, area, clear=True):
    """The Info editor in ``area``, its log cleared so the next reports are the only lines."""
    area.type = "INFO"
    settle(g)                                          # lay the region out before operating in it
    if not clear:
        return
    try:
        region = next(r for r in area.regions if r.type == "WINDOW")
        win = _win()
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
            bpy.ops.info.select_all(action="SELECT")
            bpy.ops.info.report_delete()
    except Exception as e:
        print("GUIDE info clear", e)


def bind_keys(out1, out2):
    """Session-only key bindings: F6 = Validate, F7 / F8 = Export SGM to ``out1`` / ``out2`` (reports land in the Info
    editor). Two bindings, since a binding's preset path changed after its first use was not taken."""
    wm = bpy.context.window_manager
    km = wm.keyconfigs.addon.keymaps.new(name="Window", space_type="EMPTY")
    km.keymap_items.new("bz.validate", "F6", "PRESS")
    for key, path in (("F7", out1), ("F8", out2)):
        kmi = km.keymap_items.new("bz.export_sgm", key, "PRESS")
        kmi.properties.filepath = path


def press(key):
    """Press ``key`` with the mouse over the 3D viewport (no window manager: keys go to the window under the pointer)."""
    win = _win()
    subprocess.run(["xdotool", "mousemove", str(win.x + 330), str(win.y + 150), "sleep", "0.2", "key", "--delay", "80",
                    key], check=False)


def confirm_dialog():
    """Return in Blender's file browser (a separate, centred window): confirms the export."""
    scr = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True).stdout.split()
    cx, cy = (int(scr[0]) // 2, int(scr[1]) // 2) if len(scr) == 2 else (800, 500)
    subprocess.run(["xdotool", "mousemove", str(cx), str(cy), "sleep", "0.2", "key", "--delay", "80", "Return"],
                   check=False)


def zoom(g, factor):
    win, area, region = g.view3d()
    area.spaces.active.region_3d.view_distance *= factor


def select_bone(arm, name):
    """Select one bone (Blender 5: selection lives on the pose bone; older: on the bone)."""
    for pb in arm.pose.bones:
        try:
            pb.select = (pb.name == name)
        except AttributeError:
            pb.bone.select = pb.bone.select_head = pb.bone.select_tail = (pb.name == name)
    arm.data.bones.active = arm.data.bones[name]


def activate(g, obj, also=()):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj or o in also)
    bpy.context.view_layer.objects.active = obj


def summary_line(g, obj):
    """Export's report line for ``obj`` (what the operator prints), computed in memory."""
    activate(g, obj)
    with g.override():
        job = bzops.gather_export(obj, bpy.context, animations=True)
        _model, summary = bzops.build_export(job, bpy.context)
    for w in summary.warnings:
        print("GUIDE WARN", w)
    print("GUIDE SUMMARY", summary.line)
    return summary


def finish(g):
    """Keep one list entry per shot (the last capture of a name is the one on disk) and drop the pacing shots."""
    seen, kept = set(), []
    for s in reversed(g.shots):
        if s["shot"] in DROP or s["shot"] in seen:
            continue
        seen.add(s["shot"])
        kept.append(s)
    g.shots[:] = list(reversed(kept))
    for name in DROP:
        p = os.path.join(g.IMG, f"anim_{name}.png")
        if os.path.exists(p):
            os.remove(p)


def steps(g):
    # The snap launcher points XAUTHORITY at a shared /run/user/<uid>/.Xauthority that every concurrent snap Blender
    # launch overwrites; the capture's `import` / xdotool need this run's own cookie (GUIDE_XAUTH, see the module doc).
    if os.environ.get("GUIDE_XAUTH"):
        os.environ["XAUTHORITY"] = os.environ["GUIDE_XAUTH"]
    scene = bpy.context.scene
    out1 = os.path.join(g.OUT, "rhinoceros_headedit.sgm")
    out2 = os.path.join(g.OUT, "rhinoceros_clips.sgm")
    for p in (out1, out2):
        if os.path.exists(p):
            os.remove(p)
    bind_keys(out1, out2)

    # ---- 1. import: clips become Actions -------------------------------------------------------------------
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj = g.active()
    arm = store.rig(obj)
    print("GUIDE actions:", [a.name for a in bzactions.actions_of(arm)],
          "tracks:", [(t.name, t.mute) for t in arm.animation_data.nla_tracks][:3])
    g.frame([obj])
    g.orbit(35)
    activate(g, obj)
    settle(g)
    yield "import", "The stock rhinoceros imported: its 10 clips became Actions on the rig; the rig rests."

    g.sidebar()
    settle(g)
    yield "panel", "SGM panel: '10 clips as Actions', Load Animation, 'active: none (rest pose)', Select Rig."

    # ---- 2. the NLA shows the stash -------------------------------------------------------------------------
    low = split_bottom(g, "NLA_EDITOR")
    retype(low, "NLA_EDITOR")
    g.frame([obj])
    zoom(g, 1.15)                                    # margin for the raised-head poses later on
    activate(g, obj)
    settle(g)
    yield "nla", "NLA editor: one muted track per clip holds each Action, so nothing plays until you pick one."

    # ---- 3. play a clip: Load Animation > Melee1, scrub to a mid frame ---------------------------------------
    retype(low, "DOPESHEET_EDITOR", "TIMELINE")
    activate(g, obj)
    names = list(obj.get("bz_anims", []))
    res, err = g.call(bpy.ops.bz.load_anim, anim=str(names.index(MELEE)))
    print("GUIDE load_anim", res, err, "range", scene.frame_start, scene.frame_end, "fps", scene.render.fps)
    scene.frame_set(12)
    g.orbit(-20)
    activate(g, obj, also=(arm,))
    settle(g)
    yield "melee_pose", "Load Animation > Melee1, frame 12 of 25: the rhino lowers its head to gore. Space plays the clip."

    # ---- 3b. a walk mid-stride ------------------------------------------------------------------------------
    res, err = g.call(bpy.ops.bz.load_anim, anim=str(names.index("walk")))
    scene.frame_set(26)
    activate(g, obj, also=(arm,))
    settle(g)
    yield "walk_pose", "Load Animation > walk, frame 26 of 51, mid-stride; the Timeline shows one key per frame."

    # ---- 4. Select Rig, Dope Sheet / Action Editor with the keys -----------------------------------------------
    g.call(bpy.ops.bz.load_anim, anim=str(names.index(MELEE)))
    scene.frame_set(12)
    g.call(bpy.ops.bz.select_rig)
    retype(low, "DOPESHEET_EDITOR", "ACTION")
    settle(g)
    yield "dopesheet", "Select Rig, then the Action Editor: Melee1's keys, one per clip frame on every bone."

    # ---- 5. edit the clip: rotate the head further on three frames, keyed ---------------------------------------
    g.mode(arm, "POSE")
    select_bone(arm, "head")
    pb = arm.pose.bones["head"]
    scene.frame_set(12)
    before = pb.tail.copy()
    for f, w in HEAD_FRAMES:
        scene.frame_set(f)
        pb.rotation_quaternion = pb.rotation_quaternion @ Quaternion((1.0, 0.0, 0.0), math.radians(HEAD_DEG * w))
        pb.keyframe_insert(data_path="rotation_quaternion", frame=f)
    scene.frame_set(12)
    bpy.context.view_layer.update()
    print("GUIDE head tip moved", (arm.pose.bones["head"].tail - before).length)
    retype(low, "GRAPH_EDITOR", only_selected=True)
    with bpy.context.temp_override(area=low, region=next(r for r in low.regions if r.type == "WINDOW")):
        try:
            bpy.ops.graph.view_all()
        except Exception as e:
            print("GUIDE graph view_all", e)
    settle(g)
    yield "head_edit", "Pose Mode, head bone: frames 9, 12 and 15 rotated 20-40 degrees further and keyed (I). The Graph Editor shows the head's curves."

    # ---- 6. export (F7 = Export SGM with the path preset): the edited Action is re-encoded, the rest kept --------
    g.mode(arm, "OBJECT")
    summary_line(g, obj)
    show_info(g, low)
    activate(g, obj)
    settle(g)
    press("F7")
    yield "export_dialog", "Export SGM (Native Patches) opens the file browser: pick the .sgm to write (never the stock file)."

    confirm_dialog()
    yield "export_info", "Export SGM (Native Patches): the Info editor and status bar report 'clips: 9 kept, 1 written'."
    settle(g)
    yield "export_info", "Export SGM (Native Patches): the Info editor and status bar report 'clips: 9 kept, 1 written'."
    print("GUIDE export1 written:", os.path.exists(out1), obj.get("bz_last_export"))

    # ---- 7. a new clip: a new Action, keyed over 21 frames -----------------------------------------------------
    activate(g, arm)
    ad = arm.animation_data
    ad.action = None
    taunt = bpy.data.actions.new("Taunt")
    bzactions._assign(arm, taunt)
    if hasattr(taunt, "slots") and not len(taunt.slots):
        try:
            slot = taunt.slots.new("OBJECT", arm.name)
            ad.action_slot = slot
        except Exception as e:
            print("GUIDE slot", e)
    g.mode(arm, "POSE")
    poses = {"head": ((1.0, 0.0, 0.0), -35.0), "neck1": ((1.0, 0.0, 0.0), -15.0), "tail1": ((0.0, 0.0, 1.0), 40.0),
             "jaw1": ((1.0, 0.0, 0.0), 25.0)}
    for f, w in ((1, 0.0), (11, 1.0), (21, 0.0)):
        scene.frame_set(f)
        for bn, (axis, deg) in poses.items():
            pb = arm.pose.bones[bn]
            pb.rotation_quaternion = Quaternion(axis, math.radians(deg * w))
            pb.keyframe_insert(data_path="rotation_quaternion", frame=f)
    scene.frame_start, scene.frame_end = 1, 21
    scene.frame_set(11)
    select_bone(arm, "head")
    print("GUIDE taunt fcurves", len(bzactions._fcurves(taunt)), "range", bzactions.frame_range(taunt))
    retype(low, "DOPESHEET_EDITOR", "ACTION")
    settle(g)
    yield "new_clip", "A new Action 'Taunt' on the rig: head, neck1, jaw1 and tail1 keyed at frames 1, 11 and 21 (rest, pose, rest)."

    # ---- 8. rename / delete, an unlinked Action and an unmuted track: what Validate (F6) and Export say ----------
    g.mode(arm, "OBJECT")
    bpy.data.actions["OAttack1"].name = "Gore"                   # rename: written under the new name, OAttack1 dropped
    bpy.data.actions.remove(bpy.data.actions["Crouch"])          # delete: the clip is dropped
    orphan = taunt.copy()                                        # a copy held only by its fake user: not exported
    orphan.name = "Taunt_sketch"
    orphan.use_fake_user = True
    for t in ad.nla_tracks:
        if t.name == "walk":
            t.mute = False                                       # an unmuted track: Export warns
    show_info(g, low)
    activate(g, obj)
    settle(g)
    press("F6")
    yield "validate_info", "Tools > Validate after deleting Crouch and renaming OAttack1 to Gore: the rulebook lists the missing common clips, plus the export warnings."
    settle(g)
    yield "validate_info", "Tools > Validate after deleting Crouch and renaming OAttack1 to Gore: the rulebook lists the missing common clips, plus the export warnings."
    print("GUIDE validate", [p for p in obj.get("bz_validation", []) if "clip" in p or "Action" in p])

    summary_line(g, obj)
    show_info(g, low)
    activate(g, obj)
    settle(g)
    press("F8")
    DROP.add("export2_dialog")
    yield "export2_dialog", "(pacing shot, dropped)"
    confirm_dialog()
    yield "export2_info", "Export again: warnings for the unmuted track and the fake-user-only Action, then 'clips: 7 kept, 3 written, 2 dropped'."
    settle(g)
    yield "export2_info", "Export again: warnings for the unmuted track and the fake-user-only Action, then 'clips: 7 kept, 3 written, 2 dropped'."
    print("GUIDE export2 written:", os.path.exists(out2), obj.get("bz_last_export"))

    # ---- 9. the exported creature re-imported: Taunt and Gore are clips now ---------------------------------------
    for t in ad.nla_tracks:
        t.mute = True
    obj.hide_set(True)
    arm.hide_set(True)
    bpy.ops.bz.import_sgm(filepath=out2)
    obj2 = g.active()
    arm2 = store.rig(obj2)
    print("GUIDE reimport actions:", [a.name for a in bzactions.actions_of(arm2)])
    names2 = list(obj2.get("bz_anims", []))
    activate(g, obj2)
    g.call(bpy.ops.bz.load_anim, anim=str(names2.index("Taunt")))
    scene.frame_set(11)
    retype(low, "DOPESHEET_EDITOR", "TIMELINE")
    g.frame([obj2])
    zoom(g, 0.75)
    activate(g, obj2, also=(arm2,))
    win = _win()                                     # close the last export's warning banner (a popup: Escape)
    subprocess.run(["xdotool", "mousemove", str(win.x + 600), str(win.y + 500), "sleep", "0.3", "key", "Escape",
                    "sleep", "0.3", "mousemove", str(win.x + 330), str(win.y + 150), "sleep", "0.3"], check=False)
    finish(g)
    settle(g)
    yield "reimport", "The exported file imported again (the first creature hidden): Load Animation lists Taunt and Gore; Taunt at frame 11."
