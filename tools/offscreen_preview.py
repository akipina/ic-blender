"""Render the live surface (textured) through a real GPU offscreen buffer, then exit.

    DISPLAY=:0 blender --python blender_native/tools/offscreen_preview.py -- [creature.sgm] [out.png]

Builds the SAME texdraw drawlist the live overlay uses, but straight from the parsed model
(no Blender datablocks -- those hang in startup context), so it both verifies the shader and
produces a PNG of exactly what the viewport draws. Needs a real GL context (run WITHOUT --background).
"""
import os
import sys

import numpy as np
import bpy
import gpu
from mathutils import Matrix, Vector

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, fastsurf, material, texdraw  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
src = argv[0] if len(argv) > 0 else os.path.join(REPO, "Assets/stock/snail.sgm")
out = argv[1] if len(argv) > 1 else "/tmp/bz_tex.png"
W = H = 900


def look_at(eye, target, up=Vector((0, 0, 1))):
    f = (target - eye).normalized(); s = f.cross(up).normalized(); u = s.cross(f)
    M = Matrix.Identity(4); M[0][:3] = s; M[1][:3] = u; M[2][:3] = (-f)
    return M @ Matrix.Translation(-eye)


def main():
    m = core.load_creature(src)
    model = m.model
    n = 6
    topo = fastsurf.TopoCache(m.patches, n)
    surfV, surfN = topo.eval_all(m.verts)
    uvs = core.surface_uvs(m.patch_uv, n)

    # appearance straight from the model -> GPU textures (no Blender image datablocks)
    images = {}
    from ICParse.codecs import texture as ICtex
    for tex in model.textures:
        try:
            images[tex.name] = texdraw.gputex_from_rgba(ICtex.decode_texture(tex, 0),
                                                        int(tex.width), int(tex.height))
        except Exception:
            pass
    table = material.material_table(model)
    slot_textures = [images.get(s["tex"]) or texdraw.white_texture() for s in table]
    slot_tints = [(1.0, 1.0, 1.0) if (s["tex"] in images) else s["color"] for s in table]
    patch_mat = m.patch_mat

    vbuf, drawlist = texdraw.build_drawlist_raw(surfV, surfN, topo.tris, n, uvs, patch_mat,
                                                slot_textures, slot_tints, 1.0)
    print(f"DRAWLIST {len(drawlist)} material draws, {len(images)} textures", flush=True)

    bb_min = surfV.min(0); bb_max = surfV.max(0)
    center = Vector((bb_min + bb_max) / 2); radius = float(np.linalg.norm(bb_max - bb_min)) / 2 or 1.0
    eye = center + Vector((radius * 2.2, -radius * 2.6, radius * 1.4))
    mvp = Matrix.Perspective(0.9, 1.0, radius * 0.05, radius * 12) @ look_at(eye, center)

    off = gpu.types.GPUOffScreen(W, H)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.05, 0.06, 0.08, 1.0), depth=1.0)
        gpu.state.depth_test_set("LESS_EQUAL"); gpu.state.depth_mask_set(True)
        gpu.state.face_culling_set("NONE")
        batches = texdraw.make_batches(vbuf, drawlist)
        texdraw.draw_batches(batches, mvp)
        buf = fb.read_color(0, 0, W, H, 4, 0, "UBYTE")
        gpu.state.depth_test_set("NONE"); gpu.state.depth_mask_set(False)
    off.free()

    buf.dimensions = W * H * 4
    img = bpy.data.images.new("bz_preview", W, H)
    img.pixels.foreach_set(np.frombuffer(bytes(buf), dtype=np.uint8).astype(np.float32) / 255.0)
    img.filepath_raw = out; img.file_format = "PNG"; img.save()
    print(f"OFFSCREEN_OK textured -> {out}", flush=True)


main()
os._exit(0)
