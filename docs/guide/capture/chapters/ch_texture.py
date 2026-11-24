"""Texture chapter: textures in the .sgm, the textured overlay, Paint Skin + export, UV cage editing,
texturing a New Creature from scratch (labelled texture, loading an external PNG), refusals.

Shots named ``warm*`` are throw-away: after a heavy step the first redraw can outlast the capture timer, so a
no-op yield keeps the real shot on the right state. They are removed from img/ after the run."""
import os

import numpy as np
import bpy

from blender_native import store, draw, texdraw, ops as bzops
from ICParse.sgm import parser as sgm_parser
from ICParse.codecs import texture as ICtex


def _log(*a):
    print("GUIDE NOTE", *a, flush=True)


_SIDE = {"ptr": None}


def _split_view(g, factor=0.58):
    """Split the 3D Viewport side by side; the right part becomes the chapter's Image / UV editor."""
    win, area, region = g.view3d()
    before = {a.as_pointer() for a in win.screen.areas}
    with bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region):
        bpy.ops.screen.area_split(direction="VERTICAL", factor=factor)
    new = next(a for a in win.screen.areas if a.as_pointer() not in before)
    new.type = "IMAGE_EDITOR"
    _SIDE["ptr"] = new.as_pointer()
    return new


def _side():
    win = bpy.context.window_manager.windows[0]
    return next(a for a in win.screen.areas if a.as_pointer() == _SIDE["ptr"])


def _image_editor(g, img, ui="VIEW"):
    """The side area as Image Editor (ui 'VIEW' / 'PAINT') or UV Editor (ui 'UV') showing `img`."""
    area = _side()
    area.type = "IMAGE_EDITOR"
    area.ui_type = "UV" if ui == "UV" else "IMAGE_EDITOR"
    sp = area.spaces.active
    sp.image = img
    if ui in ("VIEW", "PAINT"):
        try:
            sp.ui_mode = ui
        except Exception as e:
            _log("ui_mode", e)
    try:
        sp.overlay.show_overlays = ui == "UV"
        sp.uv_editor.show_faces = False          # wire only, so the texture stays readable under the cage
    except Exception as e:
        _log("overlay", e)
    _fit_side()
    return area


def _fit_side():
    """Fit the side editor's view to its image (call again after a redraw: a just-resized area fits wrongly)."""
    area = _side()
    try:
        with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
            bpy.ops.image.view_all(fit_view=True)
    except Exception as e:
        _log("view_all", e)


def _park_mouse():
    """Move the pointer off the toolbar (the sidebar helper leaves it there, where a tooltip pops up)."""
    import subprocess
    win = bpy.context.window_manager.windows[0]
    subprocess.Popen(["xdotool", "mousemove", str(win.x + win.width - 90), str(win.y + win.height - 60)])


def _report(text):
    """Show an operator's report line in the bottom strip (a Text Editor): reports of operators run from a script
    do not reach the Info editor, so the exact line is put where the reader can see it."""
    win = bpy.context.window_manager.windows[0]
    area = next((a for a in win.screen.areas if a.type in ("DOPESHEET_EDITOR", "TIMELINE", "TEXT_EDITOR")), None)
    if area is None:
        return
    area.type = "TEXT_EDITOR"
    txt = bpy.data.texts.get("report") or bpy.data.texts.new("report")
    txt.clear()
    txt.write(text)
    try:
        txt.cursor_set(0, character=0)                   # a cursor past a wrapped line scrolls the strip to a blank
    except Exception as e:
        _log("cursor", e)
    sp = area.spaces.active
    sp.text = txt
    sp.show_word_wrap = True
    sp.show_line_numbers = False
    try:
        sp.top = 0
    except Exception:
        pass


def _export(g, obj, path):
    """Export `obj` to `path` and return the report line Export shows (rebuilt from the same summary)."""
    res, err = g.call(bpy.ops.bz.export_sgm, filepath=path)
    _log("export", path, res, err)
    if err:
        return res, "Export refused: " + err
    info = {}
    with g.override():
        bzops.edited_model(obj, bpy.context, animations=True, info=info)
    line = f"Wrote {os.path.getsize(path)} bytes to {path}: {info['summary']}"
    return res, line


def _tex_shading(g):
    win, area, region = g.view3d()
    area.spaces.active.shading.type = "SOLID"
    area.spaces.active.shading.color_type = "TEXTURE"


def _refresh(img):
    img.update()
    texdraw.invalidate()          # the overlay caches GPU textures by image name: re-upload the painted one
    draw.invalidate()


def _select_only(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _frame(g, objs, zoom_out=1, shift=0.2):
    """Frame `objs`, zoom out `zoom_out` steps and shift the view so the N sidebar does not cover the creature."""
    from mathutils import Vector
    bpy.context.view_layer.update()
    g.frame(objs)
    win, area, region = g.view3d()
    with g.override():
        for _ in range(zoom_out):
            bpy.ops.view3d.zoom(delta=-1)
    rv3d = area.spaces.active.region_3d
    rv3d.view_location += rv3d.view_rotation @ Vector((shift * rv3d.view_distance, 0.0, 0.0))


def steps(g):
    # ------------------------------------------------------------------ 1. import: textured live overlay
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj = g.active()
    rig = store.rig(obj)
    g.frame([obj])
    g.orbit(-25)
    g.shading("SOLID")
    g.sidebar()
    bpy.context.scene.bz_show_controlnet = False
    names = store.texture_images(obj)
    _log("texture images:", names)
    yield "warm0", "-"
    yield "overlay", "The stock rhinoceros right after Import: the live surface overlay draws the creature's own textures (Display > Textured)."

    bpy.context.scene.bz_shade_mode = "PLAIN"
    yield "plain", "Display > Plain: the same surface without textures (By Limb / By Bone are the other choices)."
    bpy.context.scene.bz_shade_mode = "TEXTURED"

    # ------------------------------------------------------------------ 2. the embedded textures as images
    _split_view(g)
    head = bpy.data.images.get(names[1])              # rhino02.bmp = rhino_head's texture (128x128)
    body = bpy.data.images.get(names[0])              # rhino01.bmp = rhino_body (256x256)
    _image_editor(g, body)
    _frame(g, [obj])
    _park_mouse()
    yield "warm_images", "-"
    _fit_side()
    yield "images", "Every texture embedded in the .sgm is a Blender image named SGM_<file>: here rhino01.bmp, the body skin, opened in an Image Editor."

    # ------------------------------------------------------------------ 3. Paint Skin
    res, err = g.call(bpy.ops.bz.paint_skin)
    _log("paint_skin", res, err)
    baked = bpy.data.objects.get(obj.name + "_baked")
    _tex_shading(g)
    obj.hide_set(True)                                  # the creature's points and live overlay sit on the bake
    if rig is not None:
        rig.hide_set(True)
    _image_editor(g, head, "PAINT")
    yield "warm1", "-"
    _fit_side()
    yield "paintskin", "Paint Skin bakes the surface to a textured mesh and enters Texture Paint on it; the Image Editor shows the head skin (rhino02.bmp)."

    # a visible paint: a red band and a yellow spot on the head texture, written straight into the image
    w, h = head.size
    px = np.empty(w * h * 4, dtype=np.float32)
    head.pixels.foreach_get(px)
    a = px.reshape(h, w, 4)
    a[h // 2 - 12:h // 2 + 12, :, :3] = (0.9, 0.05, 0.05)
    yy, xx = np.mgrid[0:h, 0:w]
    spot = (yy - h * 0.72) ** 2 + (xx - w * 0.3) ** 2 < (h * 0.12) ** 2
    a[spot, :3] = (1.0, 0.85, 0.1)
    head.pixels.foreach_set(a.reshape(-1))
    _refresh(head)
    yield "painted", "After painting: a red band and a yellow spot on rhino02.bmp show on the head at once (Solid shading, Colour: Texture)."

    # ------------------------------------------------------------------ 4. Export: only the painted texture re-encoded
    g.mode(baked, "OBJECT")
    baked.hide_set(True)
    obj.hide_set(False)
    if rig is not None:
        rig.hide_set(False)
    _select_only(obj)
    _image_editor(g, head, "VIEW")
    out1 = os.path.join(g.OUT, "rhinoceros_painted.sgm")
    res, line = _export(g, obj, out1)
    _report(line)
    m_src = sgm_parser.read(g.STOCK + "/rhinoceros.sgm")
    m_out = sgm_parser.read(out1)
    same = [ICtex.decode_texture(m_src.textures[i], 0) == ICtex.decode_texture(m_out.textures[i], 0)
            for i in range(len(m_src.textures))]
    _log("textures unchanged per index:", same, "palette entries", m_src.textures[1].palette_entries, "->",
         m_out.textures[1].palette_entries)
    yield "export", "Export with the creature selected. Its report line (bottom strip) names the one re-encoded texture; the other six textures stay byte-identical."

    # ------------------------------------------------------------------ 5. UV cage
    res, err = g.call(bpy.ops.bz.edit_uvs)
    _log("edit_uvs", res, err)
    cage = g.active()
    _log("cage", cage.name, len(cage.data.polygons))
    obj.hide_set(True)
    g.mode(cage, "EDIT")
    with g.override():
        bpy.ops.mesh.select_all(action="SELECT")
    _image_editor(g, head, "UV")
    _report("Edit UVs: Open the UV Editor and edit the cage; then Apply UVs (or Export)")
    yield "warm2", "-"
    _fit_side()
    yield "uvcage", "Edit UVs builds the UV cage (one quad per patch, 288 here) and selects it. In Edit Mode, with all faces selected, the UV Editor shows every patch's corner UVs over the texture you pick."

    # rotate the head patches' UVs a quarter turn (a scripted stand-in for G/R/S in the UV Editor)
    g.mode(cage, "OBJECT")
    pmat = store.patch_mat(obj)
    uvl = cage.data.uv_layers["SGM_UV"]
    n = 0
    for p, poly in enumerate(cage.data.polygons):
        if int(pmat[p]) == 1:                            # rhino_head slot
            for li in poly.loop_indices:
                u, v = uvl.data[li].uv
                uvl.data[li].uv = (1.0 - v, u)
            n += 1
    _log("head patches rotated", n)
    me = cage.data                                       # select the head quads only (vertex flags: edit mode
    for v in me.vertices:                                # rebuilds face selection from them)
        v.select = False
    for e in me.edges:
        e.select = False
    for p, poly in enumerate(me.polygons):
        poly.select = int(pmat[p]) == 1
        if poly.select:
            for vi in poly.vertices:
                me.vertices[vi].select = True
    g.mode(cage, "EDIT")
    yield "uvedit", "Only the head patches selected: their UVs rotated a quarter turn (in the UV Editor: select them, R, 90). The cage quads move over the texture; the texture itself stays."

    g.mode(cage, "OBJECT")
    res, err = g.call(bpy.ops.bz.apply_uvs)
    _log("apply_uvs", res, err)
    cage.hide_set(True)
    obj.hide_set(False)
    _select_only(obj)
    _image_editor(g, head, "VIEW")
    out2 = os.path.join(g.OUT, "rhinoceros_uv.sgm")
    res, line = _export(g, obj, out2)
    _report(line)
    m2 = sgm_parser.read(out2)
    changed = sum(1 for i in range(len(m2.patches)) if list(m2.patches[i].uvs) != list(m_src.patches[i].uvs))
    _log("patches with changed uvs", changed)
    yield "uvapplied", "Apply UVs pulls the cage's UVs back onto the creature: the red band now runs the other way across the head. Export reports UVs: 62 patches (plus the earlier paint)."

    # ------------------------------------------------------------------ 6. from scratch: New Creature's labelled texture
    for o in (cage, baked, obj, rig):                 # done with the rhino: the overlay draws hidden creatures too
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)
    draw.invalidate()
    newpath = os.path.join(g.OUT, "guidezilla.sgm")
    res, err = g.call(bpy.ops.bz.new_creature, filepath=newpath, creature_name="Guidezilla")
    _log("new_creature", res, err)
    new = g.active()
    _log("new creature", new.name, store.texture_images(new))
    _frame(g, [new])
    g.orbit(-30)
    nimg = bpy.data.images.get(store.texture_images(new)[0])
    _image_editor(g, nimg)
    _report("New creature Guidezilla: 2234 control points, 248 patches, 48 bones, 12 rings, 9 clips -> guidezilla.sgm")
    yield "warm3", "-"
    _fit_side()
    yield "newcreature", "New Creature (From Scratch) ships one 256x256 blue / orange checker texture (guidezilla01.bmp) used by every material slot, so you can see where each patch lands before you paint."

    # ------------------------------------------------------------------ 7. replace the texture with an external PNG
    w, h = nimg.size
    yy, xx = np.mgrid[0:h, 0:w]
    art = np.empty((h, w, 4), dtype=np.float32)
    art[..., 0] = 0.15 + 0.6 * (xx / w)
    art[..., 1] = 0.55 - 0.3 * (yy / h)
    art[..., 2] = 0.2
    rings = (((xx - w / 2) ** 2 + (yy - h / 2) ** 2) ** 0.5 // 20) % 2 == 0
    art[rings, :3] *= 0.6
    art[..., 3] = 1.0
    png = bpy.data.images.new("guide_skin_src", w, h, alpha=True)
    png.pixels.foreach_set(art.reshape(-1))
    png.filepath_raw = os.path.join(g.OUT, "guidezilla_skin.png")
    png.file_format = "PNG"
    png.save()
    bpy.data.images.remove(png)
    ext = bpy.data.images.load(os.path.join(g.OUT, "guidezilla_skin.png"))
    _log("external png", ext.size[:], "target", nimg.size[:])
    if tuple(ext.size) != tuple(nimg.size):
        ext.scale(nimg.size[0], nimg.size[1])
    buf = np.empty(nimg.size[0] * nimg.size[1] * 4, dtype=np.float32)
    ext.pixels.foreach_get(buf)
    nimg.pixels.foreach_set(buf)
    _refresh(nimg)
    _report("guidezilla_skin.png loaded (Image > Open) and its pixels copied into SGM_guidezilla01.bmp")
    yield "pngswap", "An external PNG loaded with Image > Open and copied into the creature's own image (same size): the scaffold wears it at once."

    out3 = os.path.join(g.OUT, "guidezilla_skinned.sgm")
    res, line = _export(g, new, out3)
    _report(line)
    m3 = sgm_parser.read(out3)
    _log("guidezilla textures", [(t.name, t.width, t.height, t.palette_entries) for t in m3.textures])
    yield "warm4", "-"
    yield "pngexport", "Export re-encodes the replaced texture (every texel changed, so it gets a fresh 256-colour palette) and says so."

    # ------------------------------------------------------------------ 8. refusal: the painted image is gone
    bpy.data.images.remove(nimg)
    _image_editor(g, None)
    res, line = _export(g, new, os.path.join(g.OUT, "guidezilla_refused.sgm"))
    _report(line)
    yield "warm5", "-"
    yield "refused", "Delete the skin image and Export refuses rather than write the creature without its paint (the reason names the image and the fix)."
    yield "warm_end", "-"                              # the quit must not overtake the last capture

    # ------------------------------------------------------------------ 9. Transfer Texture: a source mesh's skin
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm")
    obj2 = g.active()
    _select_only(obj2)
    bpy.context.scene.bz_density = 7
    g.call(bpy.ops.bz.bake_surface)                    # a textured mesh with UVs and images = a valid source
    src = bpy.data.objects.get(obj2.name + "_baked")
    _log("transfer source:", None if src is None else (src.name, len(src.data.vertices)))
    _select_only(obj2)
    res, err = g.call(bpy.ops.bz.transfer_texture, source="" if src is None else src.name, size="1024")
    line = str(obj2.get("bz_last_transfer", ""))
    _log("transfer texture:", res, err, "|", line)
    if src is not None:
        bpy.data.objects.remove(src, do_unlink=True)
    atlas = [bpy.data.images.get(n) for n in store.texture_images(obj2)]
    atlas = next((i for i in atlas if i is not None), None)
    _image_editor(g, atlas)
    _select_only(obj2)
    _frame(g, [obj2])
    _park_mouse()
    yield "warm_transfer", "-"
    _fit_side()
    _report(line)
    yield "transfer", ("Transfer Texture: one atlas cell per patch, baked from a source mesh with Cycles, and the "
                       "creature's whole texture table replaced by that one image.")

    out4 = os.path.join(g.OUT, "rhinoceros_atlas.sgm")
    res, line2 = _export(g, obj2, out4)
    _report(line2)
    m4 = sgm_parser.read(out4) if os.path.exists(out4) else None
    _log("atlas textures:", [] if m4 is None else [(t.name, t.width, t.height, t.palette_entries) for t in m4.textures])
    yield "warm_transfer2", "-"
    yield "transfer_export", "Export re-encodes the atlas through the same texture codec as any other skin."
    yield "warm_end2", "-"
