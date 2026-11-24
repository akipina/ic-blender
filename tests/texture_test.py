"""Texture write-back round-trip, headless Blender.
    blender --background --python blender_native/tests/texture_test.py
1) fresh import + export with no edits -> byte-identical (unpainted textures untouched)
2) paint one texture -> export -> that texture changed, another stays verbatim
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import texture as ICtex  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
_log = open("/tmp/bz_tex_test_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    bpy.context.view_layer.objects.active = obj

    # 1) unpainted export is byte-identical
    out0 = "/tmp/bz_tx0.sgm"
    bpy.ops.bz.export_sgm(filepath=out0)
    identical = open(out0, "rb").read() == open(SRC, "rb").read()
    say("UNPAINTED_IDENTICAL", identical)

    # 2) paint texture 0: flip its image to a flat colour
    imgs = store.texture_images(obj)
    tname0 = imgs[0]
    img = bpy.data.images.get(tname0)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    px.fill(0.0)
    px[0::4] = 1.0; px[3::4] = 1.0                      # solid red
    img.pixels.foreach_set(px)

    out1 = "/tmp/bz_tx1.sgm"
    bpy.ops.bz.export_sgm(filepath=out1)
    m_src = sgm_parser.read(SRC)
    m_out = sgm_parser.read(out1)

    d_src0 = ICtex.decode_texture(m_src.textures[0], 0)
    d_out0 = ICtex.decode_texture(m_out.textures[0], 0)
    changed0 = d_src0 != d_out0
    # painted texture should now be ~red
    a = np.frombuffer(d_out0, np.uint8).reshape(-1, 4)
    redness = float((a[:, 0].mean() - a[:, 1].mean()))
    # a different texture stays verbatim
    d_src1 = ICtex.decode_texture(m_src.textures[1], 0)
    d_out1 = ICtex.decode_texture(m_out.textures[1], 0)
    other_same = d_src1 == d_out1
    say(f"PAINTED tex0_changed={changed0} redness={redness:.0f} tex1_unchanged={other_same}")

    blender_native.unregister()
    return identical, changed0, redness, other_same


import traceback
try:
    ident, changed0, redness, other_same = run()
    ok = ident and changed0 and redness > 100 and other_same
    open("/tmp/bz_tex_test_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" ident={ident} changed0={changed0} redness={redness:.0f} other_same={other_same}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_tex_test_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
