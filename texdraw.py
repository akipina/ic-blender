"""Textured rendering of the live bicubic surface: a custom texture x lighting shader plus
per-material draw lists. Shared by the live viewport overlay (draw.py) and the offscreen
verifier (tools/offscreen_preview.py), so what we verify is exactly what ships.

Per surface vertex we carry pos, uv and a `col` that bakes the lambert shade (and, for
flat/untextured slots, the material base colour). One vertex buffer is drawn once per material
slot with that slot's texture bound -- so a creature's 7-10 textures become 7-10 small draws.
"""
from __future__ import annotations

import numpy as np

import gpu
from gpu_extras.batch import batch_for_shader

from . import core
from . import store

_SHADER = None
_WHITE = None
_TEX_CACHE: dict = {}        # image name -> GPUTexture

_L1 = np.array([0.4, 0.3, 0.86]); _L1 /= np.linalg.norm(_L1)
_L2 = np.array([-0.5, -0.4, 0.3]); _L2 /= np.linalg.norm(_L2)


def shader():
    global _SHADER
    if _SHADER is None:
        iface = gpu.types.GPUStageInterfaceInfo("bz_tex_iface")
        iface.smooth("VEC2", "uv_interp")
        iface.smooth("VEC4", "col_interp")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.vertex_in(0, "VEC3", "pos")
        info.vertex_in(1, "VEC2", "uv")
        info.vertex_in(2, "VEC4", "col")
        info.vertex_out(iface)
        info.sampler(0, "FLOAT_2D", "image")
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(
            "void main(){ uv_interp = uv; col_interp = col;"
            " gl_Position = ModelViewProjectionMatrix * vec4(pos, 1.0); }")
        # `discard` cuts out the magenta color-key texels (feathers, fins) so they're transparent.
        info.fragment_source(
            "void main(){ vec4 t = texture(image, uv_interp);"
            " if (t.a < 0.5) discard;"
            " fragColor = vec4(t.rgb * col_interp.rgb, t.a * col_interp.a); }")
        _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def white_texture():
    global _WHITE
    if _WHITE is None:
        buf = gpu.types.Buffer("FLOAT", 4, [1.0, 1.0, 1.0, 1.0])
        _WHITE = gpu.types.GPUTexture((1, 1), format="RGBA8", data=buf)
    return _WHITE


def texture_for(name: str):
    """GPUTexture for a stored image name, or the 1x1 white fallback for flat-colour slots."""
    import bpy
    if not name:
        return white_texture()
    tex = _TEX_CACHE.get(name)
    if tex is None:
        img = bpy.data.images.get(name)
        try:
            tex = gpu.texture.from_image(img) if img else white_texture()
        except Exception:
            tex = white_texture()
        _TEX_CACHE[name] = tex
    return tex


def gputex_from_rgba(rgba: bytes, w: int, h: int):
    """GPUTexture straight from top-down RGBA8 bytes (no Blender image datablock needed)."""
    flat = (np.frombuffer(rgba, dtype=np.uint8)[:w * h * 4].astype(np.float32) / 255.0)
    flat = flat.reshape(h, w, 4)[::-1].reshape(-1)           # top-down -> GL bottom-up
    buf = gpu.types.Buffer("FLOAT", w * h * 4, flat.tolist())
    return gpu.types.GPUTexture((w, h), format="RGBA8", data=buf)


def _shade(normals: np.ndarray) -> np.ndarray:
    ndl = np.clip(normals @ _L1, 0, 1) * 0.7 + np.clip(normals @ _L2, 0, 1) * 0.3
    return (0.3 + 0.7 * ndl).astype(np.float32)


def build_drawlist_raw(surfV, surfN, tris, density, uvs, patch_mat, slot_textures, slot_tints,
                       alpha=1.0):
    """Core builder (no bpy): one vertex buffer + per-slot (GPUTexture, index_array).

    `slot_textures[s]` is the bound texture for slot s (the 1x1 white for flat slots);
    `slot_tints[s]` is (1,1,1) for textured slots and the base colour for flat ones, so
    `col = shade * tint` and `frag = texture * col` yields textured*shade or basecolour*shade."""
    M = len(surfV)
    nn = density * density
    nslots = len(slot_textures)
    if nslots == 0 or len(uvs) != M:
        return None, None
    s = _shade(surfN)
    vslot = np.asarray(patch_mat)[np.arange(M) // nn]
    tint = np.asarray(slot_tints, dtype=np.float32)[vslot]   # (M, 3)
    col = np.empty((M, 4), dtype=np.float32)
    col[:, :3] = s[:, None] * tint
    col[:, 3] = alpha

    tri_slot = vslot[tris[:, 0]]
    drawlist = []
    for slot in range(nslots):
        idx = tris[tri_slot == slot]
        if len(idx):
            drawlist.append((slot_textures[slot], idx.reshape(-1).astype(np.int32)))
    vbuf = {"pos": surfV.astype(np.float32), "uv": np.asarray(uvs, dtype=np.float32), "col": col}
    return vbuf, drawlist


def build_drawlist(obj, surfV, surfN, tris, density: int, alpha: float = 1.0):
    """Live-overlay builder: reads the stored appearance off `obj` and resolves GPU textures
    from the packed Blender images. Returns (None, None) if the object has no appearance."""
    names, tex_names, colors = store.material_slots(obj)
    if len(colors) == 0:
        return None, None
    uvs = core.surface_uvs(store.patch_uv(obj), density)
    slot_textures = [texture_for(tex_names[i] if i < len(tex_names) else "")
                     for i in range(len(colors))]
    slot_tints = [(1.0, 1.0, 1.0) if (i < len(tex_names) and tex_names[i]) else tuple(colors[i])
                  for i in range(len(colors))]
    return build_drawlist_raw(surfV, surfN, tris, density, uvs, store.patch_mat(obj),
                              slot_textures, slot_tints, alpha)


def make_batches(vbuf, drawlist):
    """Build (GPUTexture, GPUBatch) pairs from a drawlist."""
    sh = shader()
    return [(tex, batch_for_shader(sh, "TRIS", vbuf, indices=idx)) for tex, idx in drawlist]


def draw_batches(batches, mvp):
    sh = shader()
    sh.bind()
    sh.uniform_float("ModelViewProjectionMatrix", mvp)
    for tex, batch in batches:
        sh.uniform_sampler("image", tex)
        batch.draw(sh)


def invalidate():
    global _SHADER, _WHITE
    _TEX_CACHE.clear()
    _SHADER = None
    _WHITE = None
