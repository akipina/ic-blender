"""Skinning ownership editing + lossless write-back, headless Blender.
    blender --background --python blender_native/tests/skin_test.py
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
_log = open("/tmp/bz_skin_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    bpy.context.view_layer.objects.active = obj

    # 1) unedited export (ownership passed) stays byte-identical
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_sn0.sgm")
    ident = open("/tmp/bz_sn0.sgm", "rb").read() == open(SRC, "rb").read()
    say("UNEDITED_IDENTICAL", ident)

    # 2) reassign all verts of one bone to another, export, re-read
    m_src = sgm_parser.read(SRC)
    owners = np.array(R.vertex_bone_owners(m_src))
    W_orig = R.world_vertices(m_src)
    have = [b for b in sorted(set(owners.tolist())) if b >= 0 and (owners == b).sum() > 0]
    src_b, dst_b = have[2], have[3]
    moving = np.nonzero(owners == src_b)[0]
    vb = store.vert_bone(obj)
    vb[moving] = dst_b
    store.set_vert_bone(obj, vb)
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_sn1.sgm")

    m2 = sgm_parser.read("/tmp/bz_sn1.sgm")
    o2 = np.array(R.vertex_bone_owners(m2))
    moved_ok = (o2 == src_b).sum() == 0 and (o2 == dst_b).sum() == len(moving) + int((owners == dst_b).sum())
    # geometry preserved? verts are reordered, so compare sorted world-point sets
    W2 = R.world_vertices(m2)
    ws = np.array(sorted(map(tuple, np.round(W_orig, 4))))
    w2s = np.array(sorted(map(tuple, np.round(W2, 4))))
    geo_err = float(np.abs(ws - w2s).max())
    cps_valid = all(0 <= i < len(m2.vertices) for p in m2.patches for i in p.control_points)
    say(f"SKIN moved={len(moving)} bone{src_b}->bone{dst_b} owners_ok={moved_ok} "
        f"shape_err={geo_err:.3e} cps_valid={cps_valid}")
    # the owner table written through the store moved the points' bone vertex groups too (what Blender deforms)
    names = store.bone_names(obj)
    groups = {vg.index: vg.name for vg in obj.vertex_groups}
    for i in moving[:5]:
        got = [(groups[g.group], round(g.weight, 6)) for g in obj.data.vertices[int(i)].groups if groups[g.group] in names]
        assert got == [(names[dst_b], 1.0)], (int(i), got)

    # Assign Selected to Bone in Edit Mode: owners and vertex groups move together, Undo brings both back
    import bmesh
    from blender_native import ops, patchfeel
    vb_before = store.vert_bone(obj).copy()
    hidden = patchfeel.hidden_points(obj)                  # the interior points (B5): not selectable by hand
    pick = [int(i) for i in np.nonzero((vb_before == have[4]) & ~hidden)[0][:6]]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in pick
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.ed.undo_push(message="select")
    # a hidden interior follows the corner of its frame: the skin edit covers the picked points plus those
    expect = [int(i) for i in np.nonzero(ops.point_selection(obj))[0]]
    assert set(pick) <= set(expect), (pick, expect)
    assert bpy.ops.bz.assign_bone("EXEC_DEFAULT", True, bone=str(have[5])) == {"FINISHED"}
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    dl = bm.verts.layers.deform.active
    tgt = obj.vertex_groups[names[have[5]]].index
    assign_ok = all(dict(bm.verts[i][dl]) == {tgt: 1.0} for i in expect) and all(store.vert_bone(obj)[expect] == have[5])
    bpy.ops.ed.undo()
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    dl = bm.verts.layers.deform.active
    src_g = obj.vertex_groups[names[have[4]]].index
    undo_ok = all(dict(bm.verts[i][dl]) == {src_g: 1.0} for i in pick) and np.array_equal(store.vert_bone(obj), vb_before)
    bpy.ops.ed.redo()
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_sn2.sgm")
    o3 = np.array(R.vertex_bone_owners(sgm_parser.read("/tmp/bz_sn2.sgm")))
    counts_ok = (o3 == have[5]).sum() == (o2 == have[5]).sum() + int((vb_before[expect] != have[5]).sum())
    say(f"ASSIGN_BONE edit mode: {len(pick)} points (+{len(expect) - len(pick)} hidden interiors following their "
        f"corners) bone{have[4]}->bone{have[5]} groups+owners {assign_ok}, undo {undo_ok}, export owners {counts_ok}")
    moved_ok = moved_ok and assign_ok and undo_ok and counts_ok

    blender_native.unregister()
    return ident, moved_ok, geo_err, cps_valid


import traceback
try:
    ident, moved_ok, geo_err, cps_valid = run()
    ok = ident and moved_ok and geo_err < 1e-3 and cps_valid
    open("/tmp/bz_skin_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" ident={ident} owners_ok={moved_ok} shape_err={geo_err} cps_valid={cps_valid}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_skin_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
