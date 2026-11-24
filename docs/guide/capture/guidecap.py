"""Screenshot driver for the how-to guide: runs a chapter script inside Blender's GUI on a hidden X
server and captures the window after every step.

    xvfb-run -a -s "-screen 0 1600x1000x24" blender --python guidecap.py -- chapters/ch_edit.py

A chapter script defines ``def steps(g)``: a generator that performs actions on ``g`` and yields
``(shot_name, caption)`` whenever a screenshot should be taken. Shots land in ``../img/<chapter>_<shot>.jpg``
(downscaled to 1400 px wide) and ``../img/<chapter>.json`` records the shot list, captions and any
step errors, so a chapter can be re-captured on its own. Never writes anywhere else.

``GUIDE_SHOTS=horn_after,export`` re-shoots only those shots of the chapter: every step still runs (the
session has to reach them), but the images of the shots not listed are left exactly as they are. That is how
a chapter whose UI changed in one place is refreshed without re-taking twenty screenshots.
"""
import json
import os
import subprocess
import sys
import time
import traceback

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
IMG = os.path.join(os.path.dirname(HERE), "img")
sys.path.insert(0, REPO)
import blender_native  # noqa: E402


def _own_xauth():
    """The Xauthority file of this run's own Xvfb. The snap launcher points XAUTHORITY at the shared
    /run/user/<uid>/.Xauthority, which every concurrent /snap/bin/blender launch overwrites with another
    display's cookie: Blender keeps its connection, but `import` / `xdotool` then fail to open the display."""
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
if _XAUTH:
    os.environ["XAUTHORITY"] = _XAUTH

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
CHAPTER = os.path.abspath(argv[0])
NAME = os.path.splitext(os.path.basename(CHAPTER))[0].replace("ch_", "")
STOCK = os.path.join(REPO, "Assets/engine_stock")
OUT = os.path.join("/tmp/bz_guide", NAME)        # every export / new creature of a chapter goes here
os.makedirs(OUT, exist_ok=True)
os.makedirs(IMG, exist_ok=True)

bpy.context.preferences.view.show_splash = False
bpy.ops.wm.read_homefile(use_empty=True)
try:
    bpy.ops.preferences.addon_disable(module="io_scene_sgm")
except Exception:
    pass
blender_native.register()


class G:
    """What a chapter script gets: paths, context helpers and the shot log."""
    REPO, STOCK, OUT, IMG = REPO, STOCK, OUT, IMG
    shots = []
    errors = []

    def view3d(self):
        win = bpy.context.window_manager.windows[0]
        area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        return win, area, region

    def override(self):
        win, area, region = self.view3d()
        return bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region)

    def frame(self, objs=None, all=False):
        with self.override():
            if objs is not None:
                for o in bpy.context.view_layer.objects:
                    o.select_set(o in objs)
            bpy.ops.view3d.view_all() if all else bpy.ops.view3d.view_selected()

    def orbit(self, angle=15, axis="ORBITRIGHT"):
        with self.override():
            bpy.ops.view3d.view_orbit(angle=angle * 3.14159 / 180, type=axis)

    def sidebar(self, show=True):
        """Open the N sidebar on the SGM tab. The active tab is read-only from Python, so the tab strip
        gets Ctrl+wheel-down events (xdotool) -- the category list clamps at its end, and SGM is last."""
        win, area, region = self.view3d()
        area.spaces.active.show_region_ui = show
        if not show:
            return
        ui = next(r for r in area.regions if r.type == "UI")
        sh = win.height
        x = win.x + ui.x + ui.width - 12                     # the vertical tab strip at the sidebar's right edge
        y = win.y + (sh - (ui.y + ui.height // 2))             # X counts y from the top, Blender from the bottom
        cmd = ["xdotool", "mousemove", str(x), str(y), "keydown", "ctrl"]
        for _ in range(8):
            cmd += ["click", "--delay", "120", "5"]
        cmd += ["keyup", "ctrl", "mousemove", str(win.x + 40), str(win.y + sh - 200)]
        subprocess.Popen(cmd)                                  # events arrive while the timer is idle
        # the tab switch is timing-sensitive: chapters yield a shot after calling this and may call it again
        # when sidebar_tab() is still not "SGM"

    def scroll_sidebar(self, lines=20):
        """Scroll the sidebar down; the layout changes after a mode switch, so redraw first."""
        win, area, region = self.view3d()
        ui = next(r for r in area.regions if r.type == "UI")
        with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=ui):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN", iterations=1)
            for _ in range(lines):
                bpy.ops.view2d.scroll_down()

    def sidebar_tab(self):
        win, area, region = self.view3d()
        return next(r for r in area.regions if r.type == "UI").active_panel_category

    def area_type(self, kind, which=1):
        """Turn the second-largest area (the Timeline by default) into e.g. DOPESHEET_EDITOR / IMAGE_EDITOR."""
        win = bpy.context.window_manager.windows[0]
        areas = sorted(win.screen.areas, key=lambda a: -(a.width * a.height))
        areas[which].type = kind
        return areas[which]

    def shading(self, kind="SOLID"):
        win, area, region = self.view3d()
        area.spaces.active.shading.type = kind

    def mode(self, obj, mode):
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with self.override():
            if not bpy.context.mode.startswith("EDIT") if mode == "EDIT" else bpy.context.mode != mode:
                bpy.ops.object.mode_set(mode=mode)

    def call(self, op, **kw):
        """(result, message): an ERROR report comes back as the message instead of raising."""
        try:
            with self.override():
                return op("EXEC_DEFAULT", True, **kw), ""
        except RuntimeError as e:
            return {"CANCELLED"}, str(e).replace("Error: ", "")

    def active(self):
        return bpy.context.view_layer.objects.active


ONLY = {s for s in os.environ.get("GUIDE_SHOTS", "").split(",") if s}   # re-shoot a few shots of a chapter


def _capture(shot, caption):
    """Grab the window into ``../img/<chapter>_<shot>.jpg`` (1400 px wide, what the chapters reference).
    With ``GUIDE_SHOTS`` set, every other shot's step still runs but its image is left alone."""
    jpg = os.path.join(IMG, f"{NAME}_{shot}.jpg")
    if ONLY and shot not in ONLY:
        print(f"GUIDE SKIP {NAME}_{shot} (not in GUIDE_SHOTS)")
        return
    png = os.path.join(IMG, f"{NAME}_{shot}.png")
    subprocess.run(["import", "-window", "root", png], check=True, env={**os.environ})
    subprocess.run(["convert", png, "-resize", "1400x>", "-quality", "88", jpg], check=False)
    if os.path.exists(jpg):
        os.remove(png)
    G.shots.append({"shot": shot, "file": os.path.basename(jpg), "caption": caption})
    print(f"GUIDE SHOT {NAME}_{shot}: {caption}")


g = G()
ns = {}
with open(CHAPTER) as f:
    exec(compile(f.read(), CHAPTER, "exec"), ns)
gen = None


def tick():
    global gen
    t0 = time.monotonic()
    try:
        if gen is None:
            gen = ns["steps"](g)
            return 1.5                                       # let the window come up and draw once
        for a in bpy.context.window_manager.windows[0].screen.areas:
            a.tag_redraw()
        shot, caption = next(gen)
        bpy.app.timers.register(lambda: (_capture(shot, caption), None)[1], first_interval=0.9)
        # Blender schedules the next call from when THIS call started, so a step longer than 0.7 s would
        # run the next step before the capture: add the step's own duration.
        return 1.6 + (time.monotonic() - t0)                 # redraw, capture, then the next step
    except StopIteration:
        pass
    except Exception as e:
        G.errors.append(f"{type(e).__name__}: {e}")
        traceback.print_exc()
    with open(os.path.join(IMG, f"{NAME}.json"), "w") as f:
        json.dump({"chapter": NAME, "shots": G.shots, "errors": G.errors}, f, indent=1)
    print(f"GUIDE DONE {NAME}: {len(G.shots)} shots, {len(G.errors)} errors")
    bpy.app.timers.register(lambda: (bpy.ops.wm.quit_blender(), None)[1], first_interval=1.2)
    return None


bpy.app.timers.register(tick, first_interval=2.5)
