"""In-Blender smoke test. Run:
    blender --background --python blender_native/tests/blender_smoke.py
Exercises register -> import -> build mesh -> export (byte-identity) -> edit -> bake.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)  # so `import blender_native` works as a package
import blender_native  # noqa: E402

SRC = os.path.join(REPO, "Assets/stock/snail.sgm")

# tee every print to a file (Blender's background stdout is unreliable in this env)
_log = open("/tmp/bz_smoke_log.txt", "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


# A second headless Blender with the combiner hidden: $BZ_COMBOTEST points nowhere, so _setup_paths must not
# fall back to the sibling checkout, and the add-on must still register (combotest is optional).
_HIDDEN_CHILD = r"""
import sys, os, importlib.util, traceback
sys.dont_write_bytecode = True
try:
    sys.path.insert(0, os.environ["PYIC_REPO"])
    import blender_native, bpy
    st = blender_native.PATHS_STATUS["combotest"]
    assert st["source"] == "missing", st
    assert importlib.util.find_spec("combotest") is None, importlib.util.find_spec("combotest")
    blender_native.register()
    bpy.ops.bz.import_sgm.get_rna_type()              # raises when the operator is not registered
    blender_native.unregister()
    print("HIDDEN_COMBOTEST_OK", st["note"][:60])
except Exception:
    traceback.print_exc()
    sys.exit(1)
"""


def check_paths():
    """combotest comes from the pyRDN/combotest project dir (or $BZ_COMBOTEST); lupa's status is logged
    (absent is allowed: stats are limited then); the add-on registers with combotest hidden."""
    import importlib, shutil, subprocess
    st = blender_native.PATHS_STATUS
    for name in ("construct", "ICParse", "combotest", "lupa"):
        print(f"PATH {name}: {st.get(name)}")
    want = os.path.abspath(os.environ.get("BZ_COMBOTEST") or os.path.join(REPO, "pyRDN", "combotest"))
    import combotest
    got = os.path.dirname(os.path.dirname(os.path.abspath(combotest.__file__)))
    print(f"COMBOTEST_OK {combotest.__file__}")
    assert got == want, (got, want)
    try:
        lupa = importlib.import_module("lupa")
        print(f"LUPA_OK {getattr(lupa, '__version__', '?')} {lupa.__file__}")
    except ImportError as e:
        print(f"LUPA_MISSING ({e}): {st['lupa']['note']}")
    blender = os.environ.get("BLENDER") or shutil.which("blender") or bpy.app.binary_path
    env = dict(os.environ, BZ_COMBOTEST="/nonexistent", PYIC_REPO=REPO)
    env.pop("LD_LIBRARY_PATH", None)
    r = subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "1",
                        "--python-expr", _HIDDEN_CHILD], capture_output=True, text=True, timeout=300, env=env)
    line = next((l for l in r.stdout.splitlines() if l.startswith("HIDDEN_COMBOTEST_OK")), None)
    print(f"HIDDEN_COMBOTEST rc={r.returncode} {line}")
    assert r.returncode == 0 and line, (r.stdout + r.stderr)[-3000:]


def main():
    check_paths()
    blender_native.register()
    print("REGISTER_OK")

    # import
    res = bpy.ops.bz.import_sgm(filepath=SRC)
    assert res == {"FINISHED"}, res
    obj = bpy.context.active_object
    from blender_native import store, core
    assert store.is_native(obj), "object not tagged native"
    nverts = len(obj.data.vertices)
    npatch = int(obj.get(store.KEY_NPATCH))
    print(f"IMPORT_OK verts={nverts} patches={npatch} edges={len(obj.data.edges)}")

    # export untouched -> must be byte-identical to source
    out_path = "/tmp/bz_snail_out.sgm"
    res = bpy.ops.bz.export_sgm(filepath=out_path)
    assert res == {"FINISHED"}, res
    same = open(out_path, "rb").read() == open(SRC, "rb").read()
    print(f"EXPORT_IDENTITY {same}")
    assert same, "untouched export not byte-identical"

    # tessellation path (what the gpu overlay draws)
    arr = np.empty(nverts * 3); obj.data.vertices.foreach_get("co", arr)
    V, T, N = core.tessellate(arr.reshape(nverts, 3), store.patches(obj), n=4)
    print(f"TESSELLATE_OK surfV={len(V)} tris={len(T)}")
    assert len(V) > 0 and len(T) > 0

    # real edit: move one control point, re-export -> differs, count-stable
    obj.data.vertices[0].co.x += 0.25
    res = bpy.ops.bz.export_sgm(filepath="/tmp/bz_snail_edit.sgm")
    edited = open("/tmp/bz_snail_edit.sgm", "rb").read()
    print(f"EDIT_CHANGED {edited != open(SRC, 'rb').read()} len_stable {len(edited)==os.path.getsize(SRC)}")

    # symmetry enabled at import
    print(f"SYMMETRY_OK use_mirror_x={obj.data.use_mirror_x}")
    assert obj.data.use_mirror_x

    # appearance stored: textures decoded, material table, uvs
    from blender_native import material as bzmat
    slot_names, tex_names, colors = store.material_slots(obj)
    decoded = [n for n in tex_names if n and bpy.data.images.get(n)]
    print(f"APPEARANCE_OK slots={len(colors)} textured_slots={sum(1 for t in tex_names if t)} "
          f"decoded_images={len(decoded)} patch_uv={store.patch_uv(obj).shape}")
    assert len(colors) > 0

    # textured bake: materials + UV layer present
    res = bpy.ops.bz.bake_surface()
    assert res == {"FINISHED"}, res
    baked = bpy.data.objects.get(obj.name + "_baked")
    has_uv = len(baked.data.uv_layers) > 0
    textured_mats = sum(1 for m in baked.data.materials
                        if m and any(nd.type == "TEX_IMAGE" for nd in m.node_tree.nodes))
    print(f"BAKE_OK verts={len(baked.data.vertices)} faces={len(baked.data.polygons)} "
          f"materials={len(baked.data.materials)} uv_layer={has_uv} textured_mats={textured_mats}")
    assert has_uv and len(baked.data.materials) > 0

    # corner/handle classification + select operators (edit mode)
    mask = core.corner_mask(store.patches(obj), nverts)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    res = bpy.ops.bz.select_kind(kind="CORNER")
    assert res == {"FINISHED"}, res
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    sel_corner = sum(1 for v in bm.verts if v.select)
    res = bpy.ops.bz.select_kind(kind="HANDLE")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    sel_handle = sum(1 for v in bm.verts if v.select)
    hidden = sum(1 for v in bm.verts if v.hide)                 # the interior points (B5: hidden, never selected)
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"CLASSIFY_OK corners={int(mask.sum())} sel_corner={sel_corner} "
          f"handles={int((~mask).sum()) - hidden} sel_handle={sel_handle} hidden_interiors={hidden}")
    assert sel_corner == int(mask.sum()) and sel_handle == int((~mask).sum()) - hidden and hidden > 0

    # limbs & connections: mapping, retrieval, and select-by-limb
    pl = store.patch_limb(obj)
    lnames = store.limb_names(obj)
    conns = store.connections(obj)
    assigned = int((pl >= 0).sum())
    limb_idx = next((i for i in range(len(lnames)) if (pl == i).any()), -1)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.bz.select_limb(limb=str(limb_idx))
    bmL = bmesh.from_edit_mesh(obj.data); bmL.verts.ensure_lookup_table()
    sel_limb = sum(1 for v in bmL.verts if v.select)
    bpy.ops.object.mode_set(mode="OBJECT")
    print(f"LIMB_OK limbs={len(lnames)} assigned={assigned}/{len(pl)} conns={len(conns)} "
          f"select '{lnames[limb_idx]}'={sel_limb}")
    assert len(lnames) > 0 and len(conns) > 0 and assigned > 0 and sel_limb > 0

    # draw _sync path (dirty update) without a GL surface: exercise fastsurf state machine
    from blender_native import fastsurf
    topo = fastsurf.TopoCache(store.patches(obj), 4)
    sV, sN = topo.eval_all(np.array([v.co[:] for v in obj.data.vertices]))
    print(f"DRAW_SYNC_OK surf_blocks_verts={len(sV)}")

    # edit-loop tools: validate / symmetrize / seam-smooth operators
    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.bz.validate()
    assert res == {"FINISHED"}, res
    nprob = len(core.blocking(list(obj.get("bz_validation", []))))
    res = bpy.ops.bz.symmetrize()
    assert res == {"FINISHED"}, res
    res = bpy.ops.bz.seam_smooth()
    assert res in ({"FINISHED"}, {"CANCELLED"}), res
    # symmetrized + smoothed creature still exports and validates clean
    res = bpy.ops.bz.validate()
    nprob2 = len(core.blocking(list(obj.get("bz_validation", []))))
    print(f"EDITTOOLS_OK validate_errs={nprob} after_edits_errs={nprob2} "
          f"seam_smooth={str(res)}")
    assert nprob == 0 and nprob2 == 0

    # textured drawlist data path (numpy only; real GL draw needs a viewport, tested by user)
    from blender_native import texdraw
    topo2 = fastsurf.TopoCache(store.patches(obj), 4)
    sV2, sN2 = topo2.eval_all(np.array([v.co[:] for v in obj.data.vertices]))
    uvs2 = core.surface_uvs(store.patch_uv(obj), 4)
    names2, texn2, cols2 = store.material_slots(obj)
    slot_tex = ["IMG" if texn2[i] else "WHITE" for i in range(len(cols2))]       # opaque dummies
    slot_tint = [(1, 1, 1) if texn2[i] else tuple(cols2[i]) for i in range(len(cols2))]
    vbuf2, dl = texdraw.build_drawlist_raw(sV2, sN2, topo2.tris, 4, uvs2,
                                           store.patch_mat(obj), slot_tex, slot_tint, 1.0)
    idx_total = sum(len(idx) for _, idx in dl)
    cmin, cmax = float(vbuf2["col"].min()), float(vbuf2["col"].max())
    print(f"TEXDRAW_OK draws={len(dl)} idx_total={idx_total} tris={topo2.tris.size} "
          f"col=[{cmin:.2f},{cmax:.2f}] uv_len={len(vbuf2['uv'])}")
    assert idx_total == topo2.tris.size and len(vbuf2["pos"]) == len(sV2)
    assert 0 <= cmin and cmax <= 1.0001

    # native rig: armature + rigid skin; posing must deform the EVALUATED mesh while the rest
    # mesh (and thus export) stays byte-identical.
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    nbones = len(arm.data.bones) if arm else 0
    nvg = len(obj.vertex_groups)
    has_mod = any(m.type == "ARMATURE" for m in obj.modifiers)
    base_before = np.empty(len(obj.data.vertices) * 3); obj.data.vertices.foreach_get("co", base_before)
    deps = bpy.context.evaluated_depsgraph_get()
    rest = np.array([v.co[:] for v in obj.evaluated_get(deps).data.vertices])
    moved = 0
    if arm:
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="POSE")
        arm.pose.bones[min(3, nbones - 1)].location = (0.0, 0.5, 0.0)   # nudge a bone
        bpy.ops.object.mode_set(mode="OBJECT")
        deps = bpy.context.evaluated_depsgraph_get()
        posed = np.array([v.co[:] for v in obj.evaluated_get(deps).data.vertices])
        moved = int((np.abs(posed - rest).max(axis=1) > 1e-6).sum())
    # posing deforms the EVALUATED mesh but must NOT touch the rest base mesh (export source)
    base_after = np.empty(len(obj.data.vertices) * 3); obj.data.vertices.foreach_get("co", base_after)
    base_unchanged = bool(np.array_equal(base_before, base_after))
    print(f"RIG_OK bones={nbones} vgroups={nvg} modifier={has_mod} posed_moved_verts={moved} "
          f"rest_unchanged_by_pose={base_unchanged}")
    assert nbones > 0 and has_mod and moved > 0 and base_unchanged
    bpy.context.view_layer.objects.active = obj

    blender_native.unregister()
    print("UNREGISTER_OK")
    print("SMOKE_ALL_PASS")


# file-based result so it survives Blender's flaky background stdout
import traceback
try:
    main()
    open("/tmp/bz_smoke_done.txt", "w").write("SMOKE_ALL_PASS\n")
except Exception:
    open("/tmp/bz_smoke_done.txt", "w").write("FAIL\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
