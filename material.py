"""Texture / material reuse: decode a creature's TXMP textures to Blender images and build
Blender materials from its DIFC (base colour) + TXTR (texture name) tags.

Decoding goes through ICParse's single source of truth (`codecs.texture.decode_texture`,
top-down RGBA8); the material-tag conventions mirror the proven `sgm_blender` importer.
"""
from __future__ import annotations

import numpy as np

from ICParse.codecs import texture as ICtex

# bpy is imported inside the functions that create datablocks, so material_table / magenta_key_mask stay usable
# bpy-free (the combine preview's ghost overlay builds its arrays in combocore, which the venv tests import).


def magenta_key_mask(rgba) -> np.ndarray:
    """(h*w,) bool: the texels IC keys to transparent, pure magenta (255, 0, 255), from top-down RGBA8 bytes or a
    (..., 4) uint8 array. The float test ``r > 0.99 and g < 0.01 and b > 0.99`` on /255 values, in integers."""
    a = np.frombuffer(rgba, dtype=np.uint8).reshape(-1, 4) if isinstance(rgba, (bytes, bytearray, memoryview)) \
        else np.asarray(rgba, dtype=np.uint8).reshape(-1, 4)
    return (a[:, 0] >= 253) & (a[:, 1] <= 2) & (a[:, 2] >= 253)


def decode_textures(model) -> list:
    """One packed bpy.types.Image (or None) PER texture index, aligned to model.textures.

    Keyed by index, NOT name: creature texture names are not unique (e.g. albatross has two
    distinct 'head3.png'), and keying by name collapsed duplicates so materials mapped to the
    wrong image. Each texture gets its own image (Blender auto-suffixes the .name on collision)."""
    import bpy
    out = []
    for tex in model.textures:
        try:
            rgba = ICtex.decode_texture(tex, 0)              # top-down RGBA8, len w*h*4
        except Exception:
            out.append(None); continue
        w, h = int(tex.width), int(tex.height)
        if w <= 0 or h <= 0 or len(rgba) < w * h * 4:
            out.append(None); continue
        img = bpy.data.images.new(f"SGM_{tex.name}", w, h, alpha=True)
        arr = (np.frombuffer(rgba, dtype=np.uint8)[:w * h * 4].astype(np.float32) / 255.0)
        arr = arr.reshape(h, w, 4)
        # IC keys transparency on pure magenta (255,0,255); map those texels to alpha 0 so cut-out
        # areas (feathers, fins) render transparent instead of solid pink.
        key = magenta_key_mask(rgba[:w * h * 4]).reshape(h, w)
        arr[key, 3] = 0.0
        arr = arr[::-1]                                      # top-down -> Blender bottom-up
        img.pixels.foreach_set(np.ascontiguousarray(arr).reshape(-1))
        img.pack()
        # nothing links a skin image at import (materials may be rebuilt, the bake is optional): without a fake user
        # Blender drops it when the .blend is saved, and Export can no longer read its paint
        img.use_fake_user = True
        img["bz_hash"] = str(_pixel_crc(img))   # import-time snapshot -> detect real paint edits
        out.append(img)
    return out


def keep_skin_images() -> int:
    """Before a .blend is saved: every creature skin image (stamped at import) gets a fake user, and a painted one is
    re-packed, so the saved file holds the paint (a packed image stores the pixels it was packed with, not the ones
    painted since). Returns how many were re-packed."""
    import bpy
    n = 0
    for img in bpy.data.images:
        if img.get("bz_hash") is None and img.get("bz_tex_for") is None:
            continue
        if not img.use_fake_user:
            img.use_fake_user = True
        if img.is_dirty and img.packed_file is not None:
            img.pack()
            n += 1
    return n


def first_image_by_name(model, images) -> dict:
    """{texture name -> its FIRST decoded image} for material lookup (first match wins on dup names)."""
    out = {}
    for i, img in enumerate(images):
        if img is not None:
            out.setdefault(model.textures[i].name, img)
    return out


def _pixel_crc(img) -> int:
    """CRC of an image's current pixels (stable read), to detect paint edits regardless of any
    Blender image-storage quirks -- compared import-vs-export, not against the source decode."""
    import zlib
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return int(zlib.crc32(buf.tobytes()) & 0xFFFFFFFF)


def material_table(model) -> list:
    """Per material slot: {'tex': texture name or '', 'color': (r,g,b)} from DIFC/TXTR tags."""
    table = []
    for slot_i, slot_name in enumerate(model.material_index):
        src = next((mm for mm in model.materials if mm.name == slot_name), None)
        if src is None and slot_i < len(model.materials):
            src = model.materials[slot_i]
        texname, color = "", (0.7, 0.7, 0.7)
        for tag, val in (src.tags if src else []):
            if tag == "DIFC" and isinstance(val, (tuple, list)) and len(val) >= 4:
                color = (val[1] / 255.0, val[2] / 255.0, val[3] / 255.0)
            elif tag == "TXTR" and val and not texname:
                texname = val
        table.append({"tex": texname, "color": color})
    return table


def build_blender_materials(table, images, names=None) -> list:
    """One Blender material per slot: Principled BSDF + image-texture node (or base colour).

    `images` maps texture name -> bpy.Image; `names` optionally names each slot."""
    import bpy
    mats = []
    for i, slot in enumerate(table):
        name = (names[i] if names and i < len(names) else None) or f"sgm_mat{i}"
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        r, g, b = slot["color"]
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        img = images.get(slot["tex"]) if slot["tex"] else None
        if bsdf and img is not None:
            tnode = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tnode.image = img
            tnode.location = (-400, 200)
            mat.node_tree.links.new(tnode.outputs["Color"], bsdf.inputs["Base Color"])
            if "Alpha" in bsdf.inputs:
                mat.node_tree.links.new(tnode.outputs["Alpha"], bsdf.inputs["Alpha"])
            tnode.interpolation = "Closest"          # no pink fringe at the color-key edge
            for attr, value in (("blend_method", "CLIP"), ("shadow_method", "CLIP"),
                                ("surface_render_method", "DITHERED")):
                try:
                    setattr(mat, attr, value)        # alpha-clip cut-outs (Eevee legacy + Next)
                except Exception:
                    pass
            mat.node_tree.nodes.active = tnode
        mats.append(mat)
    return mats
