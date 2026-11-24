"""A short watchable tour of the Boner tools: opens cheetah and drives ~10 steps with captions.

    DISPLAY=:0 blender --python blender_native/tools/demo_tour.py -- [--delay 5] [--dry-run]

Every step runs in a timer with a pause between steps; errors are shown in the caption and the tour
moves on. Exports go to /tmp only. --dry-run runs the same steps back to back (for `--background`).
"""
import os
import sys
import traceback

import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DRY = "--dry-run" in argv
DELAY = float(argv[argv.index("--delay") + 1]) if "--delay" in argv else 5.0
SRC = os.path.join(REPO, "Assets/engine_stock/cheetah.sgm")
OUT = "/tmp/bz_demo_cheetah_horn.sgm"

bpy.context.preferences.view.show_splash = False
bpy.ops.wm.read_homefile(use_empty=True)
try:
    bpy.ops.preferences.addon_disable(module="io_scene_sgm")
except Exception:
    pass
blender_native.register()
bpy.ops.bz.import_sgm(filepath=SRC)
OBJ = bpy.context.view_layer.objects.active
ARM = bpy.data.objects.get(OBJ.get("bz_rig", ""))

CAPTION = ["Boner tour", "starting..."]
LOG = []


def say(title, detail=""):
    CAPTION[0], CAPTION[1] = title, detail
    print(f"TOUR: {title} -- {detail}")


def reports(op, key=None, **kw):
    """Run an operator; (result, messages): the stored report text when the operator keeps one, or the
    ERROR text when it refuses (raises in background mode; the GUI shows it in the status bar too)."""
    try:
        res = op("EXEC_DEFAULT", True, **kw)
    except RuntimeError as e:
        return {"CANCELLED"}, [str(e).replace("Error: ", "")]
    msg = OBJ.get(key, "") if key else ""
    return res, [msg] if msg else []


def view3d():
    win = bpy.context.window_manager.windows[0]
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return win, area, region


def frame(objs):
    if DRY:
        return
    win, area, region = view3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        for o in bpy.context.view_layer.objects:
            o.select_set(o in objs)
        bpy.ops.view3d.view_selected()
        area.spaces.active.show_region_ui = True


def set_mode(obj, mode):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    if bpy.context.mode != ("EDIT_MESH" if mode == "EDIT" and obj.type == "MESH" else
                            "EDIT_ARMATURE" if mode == "EDIT" else mode):
        bpy.ops.object.mode_set(mode=mode)


def top_head_points(n=6):
    """Indices of the n highest control points skinned to the head bone."""
    gi = OBJ.vertex_groups["head"].index
    pts = [(v.co.z, v.index) for v in OBJ.data.vertices if any(g.group == gi for g in v.groups)]
    return [i for _, i in sorted(pts, reverse=True)[:n]]


# ---- the steps: (caption, function) --------------------------------------------------------

def s_intro():
    say("Cheetah imported as a native creature", f"{len(OBJ.data.vertices)} control points, "
        f"{len(ARM.data.bones)} bones, {len(bpy.data.actions)} clips as Actions")
    frame([OBJ, ARM])


def s_limb_shade():
    bpy.context.scene.bz_shade_mode = "LIMB"
    say("Shade by limb", "each colour is one Boner limb (Torso, Head, Tail, legs, stubs)")


def s_bone_shade():
    bpy.context.scene.bz_shade_mode = "BONE"
    bpy.context.scene.bz_boner.mode = "BONES"
    say("Boner > Bones: colour by bone", "every control point is skinned rigidly to one bone")


def s_rename_limb():
    from blender_native import store, boner
    head = next(i for i, x in enumerate(store.limb_ids(OBJ)) if boner.class_of(x) == 0x40)
    bpy.context.scene.bz_boner.mode = "LIMBS"
    bpy.ops.bz.limb_set_active(index=head)
    res, msgs = reports(bpy.ops.bz.rename_limb, index=head, name="Demo_Head")
    say("Boner > Limbs: rename the Head limb to Demo_Head", "; ".join(msgs) or str(res))


def s_add_bone():
    set_mode(OBJ, "OBJECT")
    bpy.context.scene.bz_boner.mode = "BONES"
    res, msgs = reports(bpy.ops.bz.add_bone, key="bz_last_add_bone", parent="head", name="horn_l", offset=(0.0, 0.02, 0.0), length=0.08)
    say("Boner > Bones > Add Bone under Active: a horn under the head", "; ".join(msgs) or str(res))
    frame([ARM])


def s_skin_horn():
    set_mode(OBJ, "EDIT")
    bm = bmesh.from_edit_mesh(OBJ.data)
    bm.verts.ensure_lookup_table()
    want = set(top_head_points())
    for v in bm.verts:
        v.select = v.index in want
    bm.select_flush_mode()
    bmesh.update_edit_mesh(OBJ.data)
    bpy.context.scene.bz_boner.bone = "horn_l"
    res, msgs = reports(bpy.ops.bz.skin_assign_active)
    say("Skin the 6 highest head points to horn_l (Assign Selected to Bone)", "; ".join(msgs) or str(res))
    frame([OBJ])


def s_refusal():
    set_mode(OBJ, "OBJECT")
    res, msgs = reports(bpy.ops.bz.add_bone, parent="top_wing_r", name="wing_bit")
    say("A refusal: Add Bone under a stub bone", "; ".join(msgs) or str(res))


def s_play():
    set_mode(OBJ, "OBJECT")
    act = next(a for a in bpy.data.actions if "growl" in a.name.lower())
    if ARM.animation_data is None:
        ARM.animation_data_create()
    ARM.animation_data.action = act
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = int(act.frame_range[0]), int(act.frame_range[1])
    sc.frame_set(sc.frame_start)
    say(f"Play the {act.name} clip (a Blender Action): the horn has no keys, so it follows the head",
        f"frames {sc.frame_start}-{sc.frame_end}")
    frame([OBJ, ARM])
    if not DRY:
        win, area, region = view3d()
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
            bpy.ops.screen.animation_play()


def s_stop():
    if not DRY:
        win, area, region = view3d()
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
            bpy.ops.screen.animation_cancel(restore_frame=True)
    say("Clip stopped", "clips are ordinary Blender Actions: edit them in the Dope Sheet")


def s_rename_bone():
    res, msgs = reports(bpy.ops.bz.rename_bone, bone="horn_l", new_name="demo_horn")
    say("Boner > Bones > Rename Bone: horn_l -> demo_horn", "; ".join(msgs) or str(res))


def s_export():
    set_mode(OBJ, "OBJECT")
    res, msgs = reports(bpy.ops.bz.export_sgm, key="bz_last_export", filepath=OUT)
    say(f"Export to {OUT}", "; ".join(msgs) or str(res))


def s_validate():
    res, msgs = reports(bpy.ops.bz.validate)
    say("Validate", (" | ".join(msgs)[:300]) or "see the Validate report in the sidebar / status bar")


def s_done():
    say("Tour finished: Blender is yours", "Tab into the creature or the rig; sidebar (N) > SGM tab has the Boner panels")


STEPS = [(s_intro, 1), (s_limb_shade, 1), (s_bone_shade, 1), (s_rename_limb, 1), (s_add_bone, 1.5),
         (s_skin_horn, 1.5), (s_refusal, 1.5), (s_play, 2.5), (s_stop, 1), (s_rename_bone, 1),
         (s_export, 1.5), (s_validate, 1.5), (s_done, 1)]
_i = [0]


def run_step():
    fn, hold = STEPS[_i[0]]
    try:
        fn()
        LOG.append(f"{_i[0] + 1}/{len(STEPS)} {fn.__name__}: OK")
    except Exception as e:
        LOG.append(f"{_i[0] + 1}/{len(STEPS)} {fn.__name__}: ERROR {e}")
        say(f"{fn.__name__} failed", f"{type(e).__name__}: {e}"[:200])
        traceback.print_exc()
    _i[0] += 1
    return hold


def tick():
    if _i[0] >= len(STEPS):
        print("\n".join(LOG))
        return None
    hold = run_step()
    for a in bpy.context.window_manager.windows[0].screen.areas:
        a.tag_redraw()
    return DELAY * hold


def draw_caption():
    import blf
    font = 0
    blf.size(font, 26)
    blf.color(font, 1.0, 0.95, 0.4, 1.0)
    blf.position(font, 24, bpy.context.area.height - 60, 0)
    blf.draw(font, CAPTION[0])
    blf.size(font, 18)
    blf.color(font, 1.0, 1.0, 1.0, 1.0)
    blf.position(font, 24, bpy.context.area.height - 90, 0)
    blf.draw(font, CAPTION[1])


if DRY:
    while _i[0] < len(STEPS):
        run_step()
    print("\n".join(LOG))
    bad = [l for l in LOG if "ERROR" in l]
    sys.exit(1 if bad else 0)
else:
    bpy.types.SpaceView3D.draw_handler_add(draw_caption, (), "WINDOW", "POST_PIXEL")
    bpy.app.timers.register(tick, first_interval=1.5)
