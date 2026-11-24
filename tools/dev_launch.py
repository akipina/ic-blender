"""Open Blender's GUI with the add-on registered and a creature imported, ready to edit.

    blender --python blender_native/tools/dev_launch.py -- [creature.sgm]

Then: select the object, press Tab for edit mode, grab control points (G) and watch the
bicubic surface deform live. Sidebar (N) > SGM tab for density / show-surface / bake / export.
"""
import sys
import os

import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
src = argv[0] if argv else os.path.join(REPO, "Assets/stock/snail.sgm")

# fresh scene, no splash in the way (session-only; preferences are never saved here)
bpy.context.preferences.view.show_splash = False
bpy.ops.wm.read_homefile(use_empty=True)

# Disable the OLD io_scene_sgm addon so its cage/retopology panels don't clutter the SGM tab.
# Session-only (we never save userpref), and its files are untouched -- it stays installed.
for _name in ("io_scene_sgm",):
    try:
        bpy.ops.preferences.addon_disable(module=_name)
    except Exception:
        pass

blender_native.register()
bpy.ops.bz.import_sgm(filepath=src)

# The --python startup context has no active_object / screen yet (Blender 5.x): read the active
# object off the view layer, and frame it from a timer once the window exists.
obj = bpy.context.view_layer.objects.active
if obj.get("bz_scene"):                                  # structure / character / prop: frame every part, textured
    from blender_native import scene as bzscene
    parts = bzscene.scene_objects()
    for o in parts:
        o.select_set(True)
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    if arm is not None:
        arm.select_set(True)
    nv = sum(len(o.data.vertices) for o in parts)
    print(f"dev_launch: imported scene object {os.path.basename(src)} -> {len(parts)} part(s), {nv} vertices, "
          f"{len(bpy.data.actions)} clips as Actions. Material Preview shading shows the textures.")
else:
    print(f"dev_launch: imported {os.path.basename(src)} -> {obj.name}, "
          f"{len(obj.data.vertices)} control points. Tab into edit mode and grab handles.")


def _frame_once():
    # timers run without a window in their context: take the first window from the window manager
    wins = bpy.context.window_manager.windows
    if not wins:
        return 0.2                                   # window not up yet: try again
    win = wins[0]
    for area in win.screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if obj.get("bz_scene"):
                area.spaces.active.shading.type = "MATERIAL"
            with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
                bpy.ops.view3d.view_selected()
            break
    return None


bpy.app.timers.register(_frame_once, first_interval=0.2)
