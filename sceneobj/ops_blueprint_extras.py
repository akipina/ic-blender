"""Blueprint panel extras for scene objects: *Calc from model* (the footprint) and *Icon from this view* (the blueprint's picture).

Footprint: a blueprint footprint of 0 x 0 makes the game measure the model (``BuildingDynamics::GetEntityWidthHeight``,
``decomp/simengine/src/SimEngine/BuildingDynamics.cpp:134-141``: each half extent of the model's quantized entity box, rounded to whole path
cells). *Calc from model* writes that measurement into the panel's two numbers, from the model the export WOULD write (so a model scaled in
Blender is measured as scaled). The measuring is ``ICParse.ebp.footprint``.

Icon: the blueprint header holds a 64 x 64 palette picture (``BlueprintHeader::GetIcon``, ``EntityBlueprint.cpp:564``; the game's army
builder and info boxes draw it: ``ArmyBuilderScreen.cpp:603``, ``AnimalInfoPopup.cpp:59``). *Icon from this view* takes the 3D view as it is now
(``gpu`` off-screen draw of the scene, the middle square), shrinks it to 64 x 64 and reduces it to 256 colours with ICParse's own
``Icon.from_image`` -- what is stored is exactly what the preview shows. The picture waits in the panel (an image called ``IC icon ...``)
until Export Entity Folder writes it into the blueprint (``panels_blueprint.blueprint_bytes``).
"""
from __future__ import annotations

import bpy

from . import panels_blueprint as BP

ICON_SIZE = 64
ICON_PREFIX = "IC icon "


def icon_name_for(holder) -> str:
    return f"{ICON_PREFIX}{holder.name}"


# ------------------------------------------------------------------------------------------------------------------ pure parts
def icon_from_pixels(width: int, height: int, rgba: bytes, size: int = ICON_SIZE):
    """``(Icon, rgba of the icon as the game will show it, top-down)`` from a picture: the middle square of ``rgba`` (top-down rows, R G B A),
    shrunk to ``size`` x ``size`` and reduced to 256 colours (``ICParse.ebp.scene.Icon.from_image``)."""
    import numpy as np
    from ICParse.ebp.scene import Icon
    w, h = int(width), int(height)
    a = np.frombuffer(bytes(rgba), np.uint8)
    if w < 1 or h < 1 or a.size != w * h * 4:
        raise ValueError("the picture is empty or its pixel data does not match its size")
    a = a.reshape(h, w, 4)
    s = min(w, h)
    x0, y0 = (w - s) // 2, (h - s) // 2
    sq = np.ascontiguousarray(a[y0:y0 + s, x0:x0 + s]).copy()
    sq[:, :, 3] = 255                                                     # the game builds the picture without alpha (every stock palette alpha is 255)
    icon = Icon.from_image((s, s, sq.tobytes()), size)
    return icon, icon.to_rgba()[2]


def store_icon(holder, width: int, height: int, rgba: bytes):
    """Make the pending icon of ``holder``'s blueprint from a picture: the Blender image ``IC icon <holder>`` (64 x 64, what will be written) and
    ``bz_blueprint.icon_image``. Returns the image."""
    import numpy as np
    _icon, shown = icon_from_pixels(width, height, rgba)
    name = icon_name_for(holder)
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (ICON_SIZE, ICON_SIZE):
        if img is not None:
            bpy.data.images.remove(img)
        img = bpy.data.images.new(name, ICON_SIZE, ICON_SIZE, alpha=True)
    px = np.frombuffer(shown, np.uint8).reshape(ICON_SIZE, ICON_SIZE, 4)[::-1].astype(np.float32) / 255.0      # Blender stores rows bottom-up
    img.pixels.foreach_set(np.ascontiguousarray(px).reshape(-1))
    img.use_fake_user = True                                             # kept in the .blend: the pending icon survives a save
    img.update()
    try:
        img.preview_ensure()
    except Exception:                                                     # noqa: BLE001 -- the preview is only for the panel
        pass
    holder.bz_blueprint.icon_image = img.name
    return img


def pending_icon(bp):
    """The ``Icon`` the panel's pending picture makes, or None when there is none. Raises :class:`BP.Refused` when the picture is gone."""
    name = str(getattr(bp, "icon_image", "") or "")
    if not name:
        return None
    import numpy as np
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (ICON_SIZE, ICON_SIZE):
        raise BP.Refused(f"The new icon picture '{name}' is gone (deleted or changed). Press Icon from this view again, or remove the icon.")
    px = np.array(img.pixels[:], dtype=np.float32).reshape(ICON_SIZE, ICON_SIZE, 4)[::-1]
    rgba = np.clip(np.rint(px * 255.0), 0, 255).astype(np.uint8)
    from ICParse.ebp.scene import Icon
    return Icon.from_image((ICON_SIZE, ICON_SIZE, rgba.tobytes()), ICON_SIZE)


# ------------------------------------------------------------------------------------------------------------------ the viewport
def capture_view(context, max_side: int = 512):
    """``(width, height, rgba top-down)`` of the 3D view ``context`` sits in, drawn off-screen (the scene only: no grid, gizmos or overlays), or
    raises ``RuntimeError`` in words when there is no 3D view to take (a background Blender has none)."""
    import numpy as np
    import gpu
    area = context.area
    if area is None or area.type != "VIEW_3D":
        raise RuntimeError("Press the button inside the 3D view's sidebar: it takes a picture of that view.")
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    if region is None or rv3d is None:
        raise RuntimeError("This 3D view has no drawing area to take a picture of.")
    w, h = int(region.width), int(region.height)
    k = min(1.0, max_side / max(w, h, 1))
    w, h = max(8, int(w * k)), max(8, int(h * k))
    off = gpu.types.GPUOffScreen(w, h)
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0.2, 0.2, 0.2, 1.0), depth=1.0)
            off.draw_view3d(context.scene, context.view_layer, space, region, rv3d.view_matrix, rv3d.window_matrix, do_color_management=True)
            buf = fb.read_color(0, 0, w, h, 4, 0, "UBYTE")
        arr = np.asarray(buf.to_list() if hasattr(buf, "to_list") else buf, dtype=np.uint8).reshape(h, w, 4)
    finally:
        off.free()
    return w, h, np.ascontiguousarray(arr[::-1]).tobytes()                # the GPU's rows run bottom-up


# ------------------------------------------------------------------------------------------------------------------ operators
class BZ_OT_blueprint_footprint_calc(bpy.types.Operator):
    """Set the footprint from the model's size, the way the game measures a model when the footprint is 0 and 0"""
    bl_idname = "bz.blueprint_footprint_calc"
    bl_label = "Calc from model"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return BP._editable_poll(context)

    def execute(self, context):
        ent = BP.entity_of(context.active_object)
        bp = ent.holder.bz_blueprint
        from ICParse.ebp.footprint import footprint_of_model
        try:
            sgm, _r, _k, _n, _t = BP.scene_bytes_and_files(context, ent, refuse_errors=False)
            fp, half = footprint_of_model(sgm)
        except BP.Refused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:                                            # noqa: BLE001
            self.report({"ERROR"}, f"The model could not be measured: {e}")
            return {"CANCELLED"}
        if fp is None:
            self.report({"ERROR"}, "The model has no bounding box to measure.")
            return {"CANCELLED"}
        with BP.quiet():
            bp.footprint_x, bp.footprint_z = (min(max(int(v), 0), BP.FOOTPRINT_MAX) for v in fp)
        self.report({"INFO"}, f"Footprint set to {fp[0]} x {fp[1]} cells: half the model's box ({half[0]:.1f} x {half[2]:.1f} units), rounded to whole cells.")
        return {"FINISHED"}


class BZ_OT_blueprint_icon_from_view(bpy.types.Operator):
    """Make the blueprint's 64 x 64 icon from the 3D view as it is now (written when you export)"""
    bl_idname = "bz.blueprint_icon_from_view"
    bl_label = "Icon from this view"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return BP._editable_poll(context)

    def execute(self, context):
        ent = BP.entity_of(context.active_object)
        try:
            w, h, rgba = capture_view(context)
            img = store_icon(ent.holder, w, h, rgba)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except (ValueError, TypeError) as e:
            self.report({"ERROR"}, f"The picture could not be made into an icon: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"New icon made from this view ({img.name}). It is written into the blueprint when you export.")
        return {"FINISHED"}


class BZ_OT_blueprint_icon_clear(bpy.types.Operator):
    """Forget the new icon: the blueprint keeps the picture it has"""
    bl_idname = "bz.blueprint_icon_clear"
    bl_label = "Remove New Icon"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ent = BP.entity_of(context.active_object)
        bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
        return bp is not None and bool(bp.icon_image)

    def execute(self, context):
        ent = BP.entity_of(context.active_object)
        bp = ent.holder.bz_blueprint
        img = bpy.data.images.get(bp.icon_image)
        if img is not None and img.name.startswith(ICON_PREFIX):
            bpy.data.images.remove(img)
        bp.icon_image = ""
        return {"FINISHED"}


CLASSES = (BZ_OT_blueprint_footprint_calc, BZ_OT_blueprint_icon_from_view, BZ_OT_blueprint_icon_clear)
