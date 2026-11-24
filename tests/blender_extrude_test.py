"""Extrude Patch + the one export builder, in headless Blender (the D1 repro turned into asserts).
    blender --background --python blender_native/tests/blender_extrude_test.py

1) a fresh stock snail exports byte-identical and validates clean; it is CLOSED, so Extrude Patch refuses (poll
   message "closed");
2) every edit at once on the ungrown snail (paint, Action rename, connection bone, rest pose) -> /tmp/bz_edits_all.sgm
   has them all (the engine tier loads it);
3) an OPEN creature (the snail minus its last patch, built here) gets the same edits plus a ring rotation and then an
   Extrude onto an open side: the grown export (/tmp/bz_ex1.sgm) carries EVERY edit (nothing "not written"), the
   report line names each, the rest-pose / kept-clip warning is there, the new seam is manifold with stock
   orientation, and Validate's blocking set equals check_model of the written file (the open sides).
"""
import math
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh
from mathutils import Matrix

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops, actions as bzactions  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import texture as ICtex  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
OPEN = "/tmp/bz_open.sgm"
_log = open("/tmp/bz_extrude_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def build_open_creature(path=OPEN):
    """The stock snail without its last patch: four open sides to extrude from (indices of the rest unchanged)."""
    d = core.load_creature(SRC)
    m = d.model
    keep = list(range(len(d.patches) - 1))
    data = core.export_grown(SRC, d.verts, d.patches[keep], np.array(R.vertex_bone_owners(m)), d.patch_mat[keep],
                             d.patch_uv[keep], np.array([p.type for p in m.patches])[keep],
                             core._patch_limb_of(m)[keep])
    with open(path, "wb") as f:
        f.write(data)
    return path


def world_set(points_sgm):
    return np.array(sorted(map(tuple, np.round(np.asarray(points_sgm, dtype=np.float64), 3))))


def edit_everything(obj, ring_rotate=False):
    """Paint one texel of texture 0 with another existing colour, rename Action idle1, host connection 0 on pelvis,
    rotate the spine2 rest 10 deg (and optionally rotate connection 0's corner loop by 2). Returns what was done."""
    ctx = bpy.context
    src = core.read_source(store.source_path(obj))
    img, err = store.texture_image(obj, 0)
    assert img is not None, err
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32); img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    top = px[::-1].reshape(-1, 4)                                  # top-down, like the file
    j = next(i for i in range(1, len(top)) if not np.allclose(top[i, :3], top[0, :3]))
    px[h - 1, 0, :3] = top[j, :3]                                  # top-down texel 0 = Blender row h-1
    img.pixels.foreach_set(px.reshape(-1))
    arm = store.rig(obj)
    act = next(a for a in bzactions.actions_of(arm) if a.name == "idle1")
    act.name = "idle1_renamed"
    ctx.view_layer.objects.active = obj
    assert bpy.ops.bz.set_connection_bone(conn="0", bone="pelvis") == {"FINISHED"}
    rotated = None
    if ring_rotate:
        conns = store.connections(obj)
        n0, b0, v0 = conns[0]
        rotated = v0[2:] + v0[:2]
        conns[0] = (n0, b0, rotated)
        store.set_connections(obj, conns)
    ctx.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones["spine2"]
    eb.matrix = eb.matrix @ Matrix.Rotation(math.radians(10), 4, "Z")
    bpy.ops.object.mode_set(mode="OBJECT")
    ctx.view_layer.objects.active = obj
    return dict(src=src, texel_rgb=np.round(top[j, :3] * 255).astype(int), ring=rotated)


def check_file(path, obj, did, grown):
    src = did["src"]
    m = sgm_parser.read(path)
    names = [a.name for a in m.animations]
    assert "idle1_renamed" in names and "idle1" not in names, names
    assert m.limb_connections[0].bone_name == "pelvis", m.limb_connections[0].bone_name
    bi = [b.name for b in m.bones].index("spine2")
    assert not np.allclose(m.bones[bi].transform, src.bones[bi].transform, atol=1e-4), "spine2 rest not written"
    d0 = np.frombuffer(ICtex.decode_texture(src.textures[0], 0), np.uint8).reshape(-1, 4)
    d1 = np.frombuffer(ICtex.decode_texture(m.textures[0], 0), np.uint8).reshape(-1, 4)
    diff = list(np.nonzero(np.any(d0 != d1, axis=1))[0])
    assert diff == [0] and list(d1[0, :3]) == list(did["texel_rgb"]), (diff[:5], d1[0], did["texel_rgb"])
    # the shape is what Blender shows (every control point, whatever order the file uses)
    n = len(obj.data.vertices)
    v = np.empty(n * 3); obj.data.vertices.foreach_get("co", v)
    a, b = world_set(core.blender_to_sgm(v.reshape(n, 3))), world_set(R.world_vertices(m))
    assert a.shape == b.shape and float(np.abs(a - b).max()) < 2e-3, float(np.abs(a - b).max()) if a.shape == b.shape else (a.shape, b.shape)
    return m


def run():
    blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"
    res = bpy.ops.bz.import_sgm(filepath=SRC)
    assert res == {"FINISHED"}, res
    obj = bpy.context.active_object
    assert store.is_native(obj), "import did not make a native object"

    # 1) unedited export byte-identical, validates clean; the closed snail can't be extruded
    assert not store.grew(obj), "a fresh import must not count as grown"
    assert bpy.ops.bz.export_sgm(filepath="/tmp/bz_ex0.sgm") == {"FINISHED"}
    ident = open("/tmp/bz_ex0.sgm", "rb").read() == open(SRC, "rb").read()
    say(f"UNEDITED_IDENTICAL {ident} report={obj.get('bz_last_export')}")
    assert ident and obj.get("bz_last_export") == "no edits; byte-identical to the source", obj.get("bz_last_export")
    assert bpy.ops.bz.validate() == {"FINISHED"}
    assert not core.blocking(list(obj.get("bz_validation", [])))
    bpy.ops.object.mode_set(mode="EDIT")
    assert not bpy.ops.bz.extrude_patch.poll(), "a closed creature has no open side to extrude"
    try:
        bpy.ops.bz.extrude_patch()
        raise AssertionError("extrude ran on a closed creature")
    except RuntimeError as e:
        assert "closed" in str(e), e
        say("CLOSED_REFUSED", str(e).strip().splitlines()[-1][:160])
    bpy.ops.object.mode_set(mode="OBJECT")

    # 2) every edit on the ungrown snail
    did = edit_everything(obj)
    assert bpy.ops.bz.export_sgm(filepath="/tmp/bz_edits_all.sgm") == {"FINISHED"}
    check_file("/tmp/bz_edits_all.sgm", obj, did, grown=False)
    line = obj["bz_last_export"]
    say("UNGROWN_ALL_EDITS", line)
    for part in ("rest pose: 1 bone (spine2)", "rings: 1 (Snail_Head bone)", "textures: 1 edited", "clips: 10 kept, 1 written, 1 dropped"):
        assert part in line, (part, line)

    # 3) the open creature: the same edits + a ring rotation, then Extrude onto an open side
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"
    build_open_creature()
    assert bpy.ops.bz.import_sgm(filepath=OPEN) == {"FINISHED"}
    obj = bpy.context.active_object
    nv0, np0 = len(obj.data.vertices), int(obj[store.KEY_NPATCH])
    did = edit_everything(obj, ring_rotate=True)
    patches = store.patches(obj)
    users = core.side_users(patches)
    pi, side, key = next((p, s, k) for p, s, k in core.patch_sides(patches) if len(users[k]) == 1)
    corners = [int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])]
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in corners
    bmesh.update_edit_mesh(obj.data)
    assert bpy.ops.bz.extrude_patch.poll()
    res = bpy.ops.bz.extrude_patch()
    bpy.ops.object.mode_set(mode="OBJECT")
    nv1, np1 = len(obj.data.vertices), int(obj[store.KEY_NPATCH])
    say(f"EXTRUDE res={res} patch {pi} side {side} verts {nv0}->{nv1} patches {np0}->{np1} grew={store.grew(obj)}")
    assert res == {"FINISHED"} and nv1 == nv0 + 12 and np1 == np0 + 1 and store.grew(obj)
    assert np.array_equal(store.point_ints(obj, store.ATTR_CP), np.arange(nv1)) and store.pool_error(obj) is None

    info = {}
    ops.edited_model(obj, bpy.context, animations=True, info=info)
    say("WARNINGS", info["warnings"])
    assert not [w for w in info["warnings"] if "not written" in w], info["warnings"]
    assert any(w.startswith("rest pose of 1 bone(s) changed") for w in info["warnings"]), info["warnings"]
    assert bpy.ops.bz.export_sgm(filepath="/tmp/bz_ex1.sgm") == {"FINISHED"}
    line = obj["bz_last_export"]
    say("GROWN_ALL_EDITS", line)
    for part in ("geometry rebuilt: +12 control points, +1 patches", "rest pose: 1 bone (spine2)",
                 "rings: 1 (Snail_Head ring+bone)", "textures: 1 edited", "clips: 10 kept, 1 written, 1 dropped"):
        assert part in line, (part, line)
    m2 = check_file("/tmp/bz_ex1.sgm", obj, did, grown=True)
    assert len(m2.vertices) == nv0 + 12 and len(m2.patches) == np0 + 1
    # the ring the user set is in the file, remapped to the file's order: same world corners as in Blender
    n = len(obj.data.vertices)
    v = np.empty(n * 3); obj.data.vertices.foreach_get("co", v); v = core.blender_to_sgm(v.reshape(n, 3))
    W2 = R.world_vertices(m2)
    from ICParse.sgm.creature.model import ring_corners
    file_corners = ring_corners(m2.limb_connections[0].vertex_indices)      # stored as closed edge pairs
    assert file_corners is not None and len(file_corners) == len(did["ring"]) == 6, m2.limb_connections[0].vertex_indices
    ring_err = float(np.abs(W2[file_corners] - v[did["ring"]]).max())
    assert ring_err < 2e-3, ring_err
    # the new seam: manifold, stock orientation, and the open side it grew from is closed
    file_msgs = ICval.check_model(m2)
    say("CHECK_MODEL_OF_EXPORT", file_msgs)
    assert not [p for p in file_msgs if "more than two" in p or "same direction" in p], file_msgs
    assert any("dangling" in p for p in file_msgs), "the open creature still has open sides"

    # Validate judges the grown creature by what Export writes
    obj["bz_validation"] = ["stale"]
    try:
        bpy.ops.bz.validate()
    except RuntimeError as e:          # an operator that reports ERROR raises in background mode
        say(f"VALIDATE reported errors ({str(e).count('Error:')} lines)")
    val = list(obj.get("bz_validation", []))
    assert val != ["stale"], "Validate did not store a verdict"
    assert not [p for p in val if "vertex count changed" in p or "export failed" in p or "BLOCKED" in p], val
    assert any(p.startswith("WARN export: rest pose of 1 bone(s) changed") for p in val), \
        [p for p in val if p.startswith("WARN")][:6]
    assert sorted(core.blocking(val)) == sorted(p for p in file_msgs if not p.startswith("WARN")), (val, file_msgs)
    blender_native.unregister()
    return True


import traceback
try:
    ok = run()
    open("/tmp/bz_extrude_done.txt", "w").write("PASS\n" if ok else "FAIL\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_extrude_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    say("ERROR", traceback.format_exc())
    sys.exit(1)
