"""The combiner icon of a creature, made from the creature itself (Export Creature for the Game, when no icon was chosen).

Every creature exported for the game used to show its donor's picture in the creature creator, and "Icon from this view" needs an open 3D
view. This renders the creature from a standard three-quarter view instead, with no view open: the creature's surface is baked as Bake
Textured Mesh does it (the game's own surface, skin and shading), put in a scene of its own and drawn by Blender's Workbench renderer on a
transparent background, then laid on the dark-to-olive gradient the stock icons have and averaged down to the icon's 64 x 64.

What the game reads (decompiled, ``decomp/ic/src``): a creature's icon is the paletted image in its blueprint (``BlueprintHeader::Load``
``EntityBlueprint.cpp:860-889``, ``Image_Palette::Load`` ``Shared/Cross/RenderAnim/Image.cpp:1152``: width, height, the rows as they come,
256 palette entries); the creature creator draws it as it is, and for a creature the player combines it takes a 64 x 64 picture of the screen
(``CombinerCL::TakePicture`` ``ArmyBuilder/CombinerCL.cpp:709-765``: opaque RGB, each icon pixel the average of 2 x 2 screen pixels). So the
icon is an OPAQUE 64 x 64 picture; ``ICParse.ebp.Ebp.set_icon_from_image`` stores it the way the stock icons are stored (rows bottom-up,
palette alpha 255; the stock animals' 61 icons, measured).

Needs Blender's renderer (a GPU or a software GL): when it cannot render, :func:`render_icon` returns ``None`` and a plain reason, and the
export keeps the donor's picture and says so.
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np

SIZE = 64                  # the icon's size (every stock icon, BlueprintHeader icon)
RENDER = 256               # rendered at 4 x the icon and averaged down
TOP = (34, 15, 2)          # the stock icons' background: dark brown at the top of the picture ...
BOTTOM = (79, 71, 30)      # ... to olive at the bottom (measured on the 61 stock icons)
AZIMUTH, ELEVATION = 35.0, 18.0     # degrees: a three-quarter view from the front and the creature's left, a little above


def background(size: int = SIZE) -> np.ndarray:
    """``(size, size, 3)`` float RGB (0-255): the stock gradient, top to bottom."""
    t = np.linspace(0.0, 1.0, size)[:, None, None]
    return np.broadcast_to(np.array(TOP, float) * (1 - t) + np.array(BOTTOM, float) * t, (size, size, 3)).copy()


def compose(render_rgba: np.ndarray, size: int = SIZE) -> bytes:
    """The rendered picture ``(n, n, 4)`` float 0-1 (top-down rows, straight alpha) on the gradient, averaged down to ``size``: opaque RGBA
    bytes, top-down. ``n`` must be a multiple of ``size``."""
    n = render_rgba.shape[0]
    k = n // size
    bg = background(n)
    a = render_rgba[..., 3:4]
    rgb = render_rgba[..., :3] * 255.0 * a + bg * (1.0 - a)
    small = rgb.reshape(size, k, size, k, 3).mean(axis=(1, 3))
    out = np.empty((size, size, 4), np.uint8)
    out[..., :3] = np.clip(np.rint(small), 0, 255)
    out[..., 3] = 255
    return out.tobytes()


def camera_for(points, azimuth: float = AZIMUTH, elevation: float = ELEVATION):
    """``(position, right, up, forward, ortho_scale)`` of an orthographic camera looking at the middle of ``points`` (the surface's
    vertices, ``(n, 3)``) from the three-quarter direction (the creature faces -Y in Blender: ``core.sgm_to_blender`` maps the file's +Z,
    its front, to -Y), centred on what the camera sees of them and scaled to fit them with a margin."""
    az, el = math.radians(azimuth), math.radians(elevation)
    d = np.array([math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)])       # from the centre toward the camera
    fwd = -d
    right = np.cross(fwd, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    pts = np.asarray(points, float)
    mid = (pts.min(0) + pts.max(0)) / 2
    rel = pts - mid
    r, u = rel @ right, rel @ up
    centre = mid + right * (r.max() + r.min()) / 2 + up * (u.max() + u.min()) / 2        # the middle of what the camera sees
    ext = max(np.ptp(r), np.ptp(u), 1e-6)
    radius = float(np.linalg.norm(rel, axis=1).max())
    return centre + d * (radius * 2.0 + 1.0), right, up, fwd, float(ext) * 1.1, centre


def render_icon(obj, context):
    """``((64, 64, opaque rgba bytes) or None, note)``: the creature ``obj`` rendered for its combiner icon. ``note`` says what happened
    in plain words (why there is no picture, or how it was made). Leaves the scene as it found it (the baked copy, the temporary scene,
    camera and pictures are removed; the selection and the active object are put back)."""
    import bpy
    import mathutils
    before = {k: set(getattr(bpy.data, k).keys()) for k in ("objects", "meshes", "materials", "images", "scenes", "cameras", "worlds")}
    active, selected = context.view_layer.objects.active, [o for o in context.view_layer.objects if o.select_get()]
    density = getattr(context.scene, "bz_density", None)
    shading = _shading_types(context)
    sc = None
    try:
        for o in selected:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if density is not None:
            context.scene.bz_density = 3                   # a 64 x 64 picture needs no finer surface
        if "FINISHED" not in bpy.ops.bz.bake_surface():
            return None, "the surface could not be built"
        new = [bpy.data.objects[k] for k in set(bpy.data.objects.keys()) - before["objects"]]
        baked = next((o for o in new if o.type == "MESH"), None)
        if baked is None or not len(baked.data.polygons):
            return None, "the creature has no surface to draw"
        sc = bpy.data.scenes.new("bz_icon_tmp")
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = sc.render.resolution_y = RENDER
        sc.render.resolution_percentage = 100
        sc.render.film_transparent = True
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        sc.view_settings.view_transform = "Standard"
        sc.display.shading.light = "STUDIO"
        sc.display.shading.color_type = "TEXTURE"
        sc.display.shading.show_shadows = False
        sc.display.render_aa = "8"
        sc.collection.objects.link(baked)
        co = np.empty(len(baked.data.vertices) * 3)
        baked.data.vertices.foreach_get("co", co)
        mw = np.array(baked.matrix_world)
        pts = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        pos, right, up, fwd, scale, centre = camera_for(pts)
        cam_data = bpy.data.cameras.new("bz_icon_cam")
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = scale
        cam_data.clip_start, cam_data.clip_end = 0.01, float(np.linalg.norm(pos - centre) * 4 + 10)
        cam = bpy.data.objects.new("bz_icon_cam", cam_data)
        sc.collection.objects.link(cam)
        cam.location = tuple(pos)
        cam.rotation_euler = mathutils.Vector(tuple(fwd)).to_track_quat("-Z", "Y").to_euler()
        sc.camera = cam
        fd, png = tempfile.mkstemp(prefix="bz_icon_", suffix=".png")
        os.close(fd)
        try:
            sc.render.filepath = png
            bpy.ops.render.render(write_still=True, scene=sc.name)
            img = bpy.data.images.load(png, check_existing=False)
            try:
                w, h = img.size
                if (w, h) != (RENDER, RENDER):
                    return None, f"the renderer made a {w} x {h} picture, not {RENDER} x {RENDER}"
                px = np.asarray(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[::-1]       # Blender stores bottom-up
            finally:
                bpy.data.images.remove(img)
        finally:
            try:
                os.remove(png)
            except OSError:
                pass
        if float(px[..., 3].max()) < 0.5:
            return None, "the render came out empty"
        covered = float((px[..., 3] > 0.5).mean())
        return (SIZE, SIZE, compose(px)), f"rendered from the creature, three-quarter view ({covered * 100:.0f} % of the picture is creature)"
    except Exception as e:                               # noqa: BLE001 -- no GPU / no render in this session: the export goes on without
        return None, f"the picture could not be rendered here ({type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''})"
    finally:
        _cleanup(bpy, before, sc)
        _restore_shading(shading)                        # Bake Textured Mesh puts every 3D view in Material shading: the user's views are theirs
        for o in selected:
            if o.name in bpy.data.objects:
                o.select_set(True)
        if active is not None and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
        if density is not None:
            context.scene.bz_density = density


def _shading_types(context):
    """``[(space, shading type)]`` of every 3D view open now (none in a background Blender)."""
    out = []
    try:
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                for sp in area.spaces:
                    if sp.type == "VIEW_3D":
                        out.append((sp, sp.shading.type))
    except Exception:                                    # noqa: BLE001 -- no screen (a background session): nothing to keep
        pass
    return out


def _restore_shading(saved):
    for sp, t in saved:
        try:
            sp.shading.type = t
        except Exception:                                # noqa: BLE001 -- the view is gone
            pass


def _cleanup(bpy, before, sc):
    """Remove everything :func:`render_icon` made (a temporary scene first, then the objects, meshes, materials, pictures, camera, world)."""
    if sc is not None and sc.name in bpy.data.scenes:
        bpy.data.scenes.remove(sc)
    for k, rem in (("objects", bpy.data.objects.remove), ("meshes", bpy.data.meshes.remove), ("cameras", bpy.data.cameras.remove),
                   ("materials", bpy.data.materials.remove), ("images", bpy.data.images.remove), ("worlds", bpy.data.worlds.remove)):
        coll = getattr(bpy.data, k)
        for name in sorted(set(coll.keys()) - before[k]):
            try:
                rem(coll[name])
            except Exception:                            # noqa: BLE001 -- still used by something: leave it
                pass
