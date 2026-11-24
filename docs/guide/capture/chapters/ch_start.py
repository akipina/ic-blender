"""Chapter `start`: import the stock rhinoceros, tour the viewport overlays, the SGM sidebar (Display, Boner tabs,
Combine Preview), an animation as an Action, Validate, a byte-identical Export and a textured bake.

Validate and Export are triggered through the UI (session-only key bindings pressed with xdotool) because reports of
operators called from Python never reach the Info editor / status bar. Heavy steps force a synchronous redraw so the
capture timer is not starved by the first (llvmpipe) draw."""
import os
import subprocess

import bpy

OUT_SGM = None


def _win():
    return bpy.context.window_manager.windows[0]


def shot(name, caption):
    """Yield a shot twice: the driver's capture timer can fire after the next step when a draw stalls Blender, so
    the state is held for two ticks (the JSON is deduplicated after the run)."""
    yield name, caption
    yield name, caption


def keep_auth(out_dir):
    """Copy xvfb-run's temporary Xauthority into the chapter's output dir: a client that finds it gone can't connect."""
    import shutil
    xa = os.environ.get("XAUTHORITY")
    if xa and os.path.exists(xa):
        dst = os.path.join(out_dir, "Xauthority")
        shutil.copy(xa, dst)
        os.environ["XAUTHORITY"] = dst


def probe(tag):
    disp = os.environ.get("DISPLAY", "")
    sock = "/tmp/.X11-unix/X" + disp.lstrip(":").split(".")[0]
    r = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True)
    print(f"GUIDE probe {tag}: display={disp} socket={os.path.exists(sock)} "
          f"auth={os.path.exists(os.environ.get('XAUTHORITY', '/nonexistent'))} xdotool={r.returncode} {r.stderr.strip()[:60]}")


def redraw_now(g, n=1):
    win, area, region = g.view3d()
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=n)


def park_mouse():
    """Move the pointer over the empty top-left of the viewport (no tooltip there)."""
    win = _win()
    subprocess.run(["xdotool", "mousemove", str(win.x + 330), str(win.y + 150)], check=False)


def open_sgm_tab(g):
    """Show the N sidebar, lay it out, then Ctrl+wheel-down on its tab strip until the last tab (SGM) -- synchronously,
    while the scene is light, so the events are not lost in a slow draw."""
    win, area, region = g.view3d()
    area.spaces.active.show_region_ui = True
    redraw_now(g)
    ui = next(r for r in area.regions if r.type == "UI")
    x = win.x + ui.x + ui.width - 12
    y = win.y + (win.height - (ui.y + ui.height // 2))
    cmd = ["xdotool", "mousemove", str(x), str(y), "sleep", "0.2", "keydown", "ctrl"]
    for _ in range(8):
        cmd += ["click", "--delay", "150", "5"]
    cmd += ["keyup", "ctrl", "mousemove", str(win.x + 330), str(win.y + 150)]
    subprocess.run(cmd, check=False)


def _ui_region(g):
    win, area, region = g.view3d()
    return win, area, next(r for r in area.regions if r.type == "UI")


def scroll_sidebar(g, steps=10, down=True):
    """Scroll the N sidebar (the Boner / Combine panels sit below the main panel)."""
    win, area, ui = _ui_region(g)
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=ui):
        for _ in range(steps):
            (bpy.ops.view2d.scroll_down if down else bpy.ops.view2d.scroll_up)(page=False)


def scroll_sidebar_top(g):
    scroll_sidebar(g, 80, down=False)


def bottom_area():
    """The Timeline: the lowest, left-most area (the Properties editor also reaches y = 0)."""
    return min(_win().screen.areas, key=lambda a: (a.y, a.x))


def set_bottom(kind, ui_type=None):
    a = bottom_area()
    a.type = kind
    if ui_type:
        a.ui_type = ui_type
    return a


def edge_xy(g):
    """X-server coordinates of the edge between the 3D viewport and the bottom area."""
    win, v3d, region = g.view3d()
    return win.x + v3d.x + v3d.width // 2, win.y + (win.height - v3d.y)


def mouse_to_edge(g):
    x, y = edge_xy(g)
    subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=False)


def grow_bottom(g, px=230):
    """Move the viewport / bottom-area edge up by ``px``: screen.area_move (needs the pointer on the edge, so
    mouse_to_edge one step earlier), else an xdotool drag."""
    win, v3d, region = g.view3d()
    before = bottom_area().height
    with bpy.context.temp_override(window=win, screen=win.screen, area=v3d, region=region):
        try:
            bpy.ops.screen.area_move(x=v3d.x + v3d.width // 2, y=v3d.y, delta=px)
            print("GUIDE area_move ok")
            return
        except Exception as e:
            print("GUIDE area_move failed:", e, "| bottom height", before)
    x, y = edge_xy(g)
    subprocess.run(["xdotool", "mousemove", str(x), str(y), "sleep", "0.3", "mousedown", "1", "sleep", "0.2",
                    "mousemove", str(x), str(y - 20), "sleep", "0.2", "mousemove", str(x), str(y - px), "sleep", "0.2",
                    "mouseup", "1"], check=False)


def bind_keys():
    """Session-only key bindings so Validate / Export run through the UI (reports land in the Info editor)."""
    wm = bpy.context.window_manager
    km = wm.keyconfigs.addon.keymaps.new(name="Window", space_type="EMPTY")
    km.keymap_items.new("bz.validate", "F6", "PRESS")
    kmi = km.keymap_items.new("bz.export_sgm", "F7", "PRESS")
    kmi.properties.filepath = OUT_SGM


def press(key):
    win = _win()
    subprocess.run(["xdotool", "mousemove", str(win.x + 330), str(win.y + 150), "sleep", "0.2", "key", "--delay", "80",
                    key], check=False)


def expand_combine_panel(g):
    """The Combine Preview sub-panel is DEFAULT_CLOSED and Blender keeps a panel's open state per region, so re-register
    the same class under a fresh idname (a new panel instance starts open); the collapsed one is unregistered first.
    Session-only: the add-on's source is untouched."""
    from blender_native import ui
    base = ui.BZ_PT_combine
    try:
        bpy.utils.unregister_class(base)
    except Exception as e:
        print("GUIDE combine panel unregister:", e)

    class BZ_PT_combine_open(base):
        bl_idname = "BZ_PT_combine_open"
        bl_options = set()

    bpy.utils.register_class(BZ_PT_combine_open)


def zoom(g, factor):
    win, area, region = g.view3d()
    area.spaces.active.region_3d.view_distance *= factor


def steps(g):
    global OUT_SGM
    scene = bpy.context.scene
    src = os.path.join(g.STOCK, "rhinoceros.sgm")
    OUT_SGM = os.path.join(g.OUT, "rhinoceros.sgm")
    if os.path.exists(OUT_SGM):
        os.remove(OUT_SGM)
    bind_keys()
    keep_auth(g.OUT)
    probe("start")

    # 0. the bare SGM tab -----------------------------------------------------------------------
    open_sgm_tab(g)
    yield from shot("empty", ("A fresh scene with the add-on enabled: the N sidebar's SGM tab offers Import SGM / EBP and "
                    "New Creature (From Scratch); the Display toggles do nothing until a creature is loaded."))
    for _ in range(2):
        if g.sidebar_tab() == "SGM":
            break
        print("GUIDE sidebar tab is", g.sidebar_tab(), "- retrying")
        open_sgm_tab(g)

    # 1. import (Import SGM / EBP) ------------------------------------------------------------
    res, err = g.call(bpy.ops.bz.import_sgm, filepath=src)
    print("GUIDE import:", res, err)
    obj = g.active()
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    print("GUIDE object:", obj.name, len(obj.data.vertices), "cps; rig", arm and arm.name, arm and len(arm.data.bones),
          "clips", list(obj.get("bz_anims", [])))
    import blender_native
    print("GUIDE PATHS_STATUS:", {k: v.get("source") for k, v in getattr(blender_native, "PATHS_STATUS", {}).items()})
    g.frame([obj])
    zoom(g, 0.8)
    redraw_now(g, 2)                    # the first textured draw compiles the overlay shaders: do it now
    park_mouse()
    yield from shot("import", ("The stock rhinoceros just imported (Object Mode): the orange dots are the file's own control points, "
                     "the grey lines the control net, the textured skin the live bicubic surface; the rig sits inside."))
    print("GUIDE tab at import:", g.sidebar_tab())
    probe("after import")

    # 2. Surface Only ------------------------------------------------------------------------
    scene.bz_surface_only = True
    g.orbit(20)
    redraw_now(g)
    yield from shot("surface_only", "Display > Surface Only hides the control points and the rig: the creature as the game tessellates it.")
    scene.bz_surface_only = False

    # 3. Edit Mode with handle dots --------------------------------------------------------------
    scene.bz_show_handles = True
    g.mode(obj, "EDIT")
    g.orbit(-20)
    redraw_now(g)
    park_mouse()
    yield from shot("edit", ("Tab: Edit Mode. The surface turns translucent (Edit Opacity) and Show Handle Dots colours corners gold "
                   "and off-surface handles blue. Grab any point (G) and the surface follows."))
    scene.bz_show_handles = False
    g.mode(obj, "OBJECT")

    # 4. By Limb + Boner > Limbs --------------------------------------------------------------
    scene.bz_boner.mode = "LIMBS"           # Follow Shading switches the overlay to By Limb
    scene.bz_shade_mode = "LIMB"
    redraw_now(g)
    scroll_sidebar_top(g)
    scroll_sidebar(g, 26)
    redraw_now(g)
    yield from shot("limbs", "Shading By Limb, and the Boner > Limbs tab: every limb with its class, patch and bone counts and status.")

    # 5. By Bone + Boner > Bones ----------------------------------------------------------------
    names = [b.name for b in arm.data.bones] if arm else []
    pick = "head" if "head" in names else (names[0] if names else "")
    scene.bz_boner.mode = "BONES"
    scene.bz_shade_mode = "BONE"
    scene.bz_boner.bone = pick
    redraw_now(g)
    scroll_sidebar_top(g)
    scroll_sidebar(g, 30)
    redraw_now(g)
    yield from shot("bones", f"Shading By Bone with '{pick}' active in Boner > Bones: its points and surface bright, the other bones dimmed.")

    # 6. Connections ----------------------------------------------------------------------------
    scene.bz_boner.mode = "CONNECTIONS"
    scene.bz_shade_mode = "PLAIN"
    scene.bz_show_connections = True
    scene.bz_boner.active_conn = 0
    redraw_now(g)
    scroll_sidebar_top(g)
    scroll_sidebar(g, 40)
    g.orbit(35)
    redraw_now(g)
    mouse_to_edge(g)                    # so the next step's area_move finds no active region
    yield from shot("connections", ("Show Connections highlights the graft rings (where limbs attach; the active ring bright); "
                          "Boner > Connections lists each ring with its classes, host bone and status."))
    scene.bz_show_connections = False
    scene.bz_shade_mode = "TEXTURED"
    scroll_sidebar_top(g)

    # 7. an animation as an Action ---------------------------------------------------------------
    grow_bottom(g, 230)
    anims = list(obj.get("bz_anims", []))
    idx = next((i for i, n in enumerate(anims) if "walk" in n.lower()), 0) if anims else -1
    if idx >= 0:
        res, err = g.call(bpy.ops.bz.load_anim, anim=str(idx))
        print("GUIDE load_anim:", anims[idx], res, err)
        mid = (scene.frame_start + scene.frame_end) // 2
        scene.frame_set(mid)
        if arm:
            arm.select_set(True)
        ds = set_bottom("DOPESHEET_EDITOR")
        try:
            ds.spaces.active.mode = "DOPESHEET"
            ds.spaces.active.show_region_channels = True
        except Exception as e:
            print("GUIDE dopesheet mode:", e)
        g.orbit(-35)
        redraw_now(g)
        park_mouse()
        yield from shot("anim", (f"Load Animation > {anims[idx]} makes that clip the rig's active Action (posed here at frame {mid}); "
                       "the bottom area is a Dope Sheet showing its keys. Scrub or press Space to play."))
        print("GUIDE bottom area:", bottom_area().type, bottom_area().height)
        scene.frame_set(scene.frame_start)
        if arm and arm.animation_data:
            arm.animation_data.action = None          # back to the rest pose
        if arm:
            arm.select_set(False)
        bpy.context.view_layer.objects.active = obj

    # 8. Validate (through the UI: F6 = the Validate button) --------------------------------------
    set_bottom("INFO")
    scene.bz_combine.validate_level = "QUICK"
    press("F6")
    yield from shot("validate", ("Tools > Validate (will it load?) with Combine Checks: Quick. The verdict and every line go to the "
                       "Info editor (the bottom area here) and the status bar."))
    print("GUIDE validation:", list(obj.get("bz_validation", []))[:6])

    # 9. Export (F7 = Export SGM with the output path preset; Return confirms the file browser) ----
    press("F7")
    subprocess.run(["xdotool", "sleep", "0.8", "mousemove", "650", "330"], check=False)
    yield from shot("export_dialog", ("Export SGM (Native Patches) opens Blender's file browser; pick the .sgm to write (never the "
                            "stock file you imported)."))
    scr = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True).stdout.split()
    cx, cy = (int(scr[0]) // 2, int(scr[1]) // 2) if len(scr) == 2 else (800, 500)
    subprocess.run(["xdotool", "mousemove", str(cx), str(cy), "sleep", "0.2", "key", "--delay", "80", "Return"],
                   check=False)
    mouse_to_edge(g)                    # so the next step's area_move (shrinking the bottom area back) can run
    yield from shot("export", ("The untouched rhinoceros writes back byte-identical to the source, and the Info line says so; "
                     "only what you edit is ever rewritten."))
    same = os.path.exists(OUT_SGM) and open(OUT_SGM, "rb").read() == open(src, "rb").read()
    print("GUIDE export: identical:", same, "|", obj.get("bz_last_export"))
    probe("after export")

    # 10. Combine Preview --------------------------------------------------------------------------
    grow_bottom(g, -230)
    park_mouse()
    set_bottom("DOPESHEET_EDITOR", "TIMELINE")
    scene.bz_boner.mode = "BONES"
    scene.bz_shade_mode = "TEXTURED"
    expand_combine_panel(g)
    s = scene.bz_combine
    from blender_native import combocore
    s.other_kind, s.other_stock_name, s.role = "STOCK", "cheetah", combocore.ROLE_TORSO
    for grp in combocore.PART_LABELS:
        setattr(s, f"part_{grp}", grp in ("head", "tail"))
    res, err = g.call(bpy.ops.bz.combine_preview)
    print("GUIDE combine_preview:", res, err)
    probe("after combine")
    redraw_now(g)
    scroll_sidebar(g, 80)
    zoom(g, 1.7)
    g.orbit(25)
    redraw_now(g, 2)
    yield from shot("combine", ("Combine Preview: this creature is the torso, the stock cheetah donates Head and Tail. The game's "
                      "combiner draws the result beside the rhinoceros as a ghost, with its stats in the readout."))
    g.call(bpy.ops.bz.combine_clear)
    scroll_sidebar_top(g)

    # 11. Bake --------------------------------------------------------------------------------------
    zoom(g, 0.6)
    res, err = g.call(bpy.ops.bz.bake_surface)
    print("GUIDE bake:", res, err, [o.name for o in bpy.data.objects])
    scene.bz_show_surface = False
    scene.bz_show_controlnet = False
    obj.hide_set(True)
    if arm:
        arm.hide_set(True)
    g.shading("MATERIAL")
    g.orbit(-25)
    redraw_now(g, 30)                   # EEVEE compiles the materials in the background: give it time
    yield from shot("bake", ("Bake Textured Mesh makes a real mesh (Rhinoceros_baked) with the creature's materials, UVs and the "
                   "engine's normals for Eevee / Cycles; shown alone here (overlays off, Material Preview)."))
