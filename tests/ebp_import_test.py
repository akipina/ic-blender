"""In-Blender test: importing a creature through its .ebp blueprint. Run:
    blender --background --python blender_native/tests/ebp_import_test.py

Three routes, all resolved by ICParse.ebp (the add-on adds no blueprint parsing of its own):
  * a stock PROXY blueprint (FORM XSGM) -> the same-named .sgm beside it becomes the lossless source;
  * a blueprint that EMBEDS its creature (FORM BOBJ) -> the .ebp itself is the source and an untouched
    export equals the embedded creature's bytes;
  * a combined-creature PROFILE (no geometry) -> a clear error, nothing imported.
"""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402

STOCK = os.path.join(REPO, "Assets", "stock")

_log = open("/tmp/bz_ebp_log.txt", "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def _import(path):
    """Run the import operator; Blender turns an ERROR report into a RuntimeError from bpy.ops."""
    try:
        return bpy.ops.bz.import_sgm(filepath=path), None
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def main():
    import ICParse.ebp as ebp
    from blender_native import store, core

    blender_native.register()
    print("REGISTER_OK")
    ebps = sorted(glob.glob(os.path.join(STOCK, "*.ebp")))
    proxies = [p for p in ebps if ebp.read(p).kind == "proxy"]
    models = [p for p in ebps if ebp.read(p).kind == "model"]
    assert proxies, "no proxy blueprint in Assets/stock"

    # 1. proxy blueprint -> sibling .sgm is the source; untouched export == that file
    p = proxies[0]
    res, err = _import(p)
    assert res == {"FINISHED"}, (res, err)
    obj = bpy.context.active_object
    assert store.is_native(obj)
    src = store.source_path(obj)
    assert src.lower().endswith(".sgm") and os.path.isfile(src), src
    assert obj.get("bz_ebp") == p
    out = "/tmp/bz_ebp_proxy_out.sgm"
    assert bpy.ops.bz.export_sgm(filepath=out) == {"FINISHED"}
    same = open(out, "rb").read() == open(src, "rb").read()
    print(f"PROXY_OK {os.path.basename(p)} -> {os.path.basename(src)} identity={same}")
    assert same, "untouched export of a proxy-imported creature is not byte-identical to its .sgm"

    # 2. embedded creature -> the .ebp is the source; untouched export == the embedded FORM BOBJ
    if models:
        p = models[0]
        res, err = _import(p)
        assert res == {"FINISHED"}, (res, err)
        obj = bpy.context.active_object
        assert store.source_path(obj) == p
        out = "/tmp/bz_ebp_embedded_out.sgm"
        assert bpy.ops.bz.export_sgm(filepath=out) == {"FINISHED"}
        same = open(out, "rb").read() == ebp.read(p).embedded_sgm_bytes()
        print(f"EMBEDDED_OK {os.path.basename(p)} identity={same}")
        assert same, "untouched export of an embedded-creature blueprint != its embedded bytes"
        # an edit still exports (the .ebp source is re-read and only the moved point rewritten)
        obj.data.vertices[0].co.x += 0.1
        assert bpy.ops.bz.export_sgm(filepath="/tmp/bz_ebp_embedded_edit.sgm") == {"FINISHED"}
        edited = open("/tmp/bz_ebp_embedded_edit.sgm", "rb").read()
        print(f"EMBEDDED_EDIT_OK changed={edited != ebp.read(p).embedded_sgm_bytes()}")
    else:
        print("EMBEDDED_SKIP (no embedding blueprint in Assets/stock)")

    # 3. a combined-creature profile has no geometry -> refused with a message that says so
    profiles = sorted(glob.glob(os.path.join(REPO, "Assets", "defprofile", "**", "*.ebp"), recursive=True))
    if profiles:
        before = len(bpy.data.objects)
        res, err = _import(profiles[0])
        assert res == {"CANCELLED"}, res
        assert err and "profile" in err, err
        assert len(bpy.data.objects) == before
        print(f"PROFILE_REFUSED_OK {os.path.basename(profiles[0])}: {err.strip()[:80]}")
    else:
        print("PROFILE_SKIP (no Assets/defprofile)")

    print("ALL_OK")


import traceback
try:
    main()
    open("/tmp/bz_ebp_done.txt", "w").write("PASS\n")
except Exception:
    open("/tmp/bz_ebp_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
