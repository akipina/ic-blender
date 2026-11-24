"""Dump what the add-on renders headlessly for an asset, for comparison with the Object Editor.

    blender --background --factory-startup --python blender_native/tools/render_dump.py -- SRC OUT.npz [--density 5] [--anim CLIP --t T]

Imports SRC through the add-on (a creature .sgm/.ebp, or a scene object: structure / character / prop),
bakes a creature's surface at the given density (the editor's default is 5 samples per patch edge),
then writes the DEPSGRAPH-EVALUATED meshes -- the exact geometry Blender renders (modifiers applied,
custom normals resolved) -- per face-corner in SGM space, plus the material images. With ``--anim CLIP
--t T`` the clip is baked onto the rig at normalized time T (frame 1) first, so the dump is the POSED
render (creature: posed bake + posed overlay; scene object: the armature-deformed meshes):

    kind            "creature" | "scene"
    loop_pos        (L, 3) SGM-space positions, three rows per triangle
    loop_normal     (L, 3) SGM-space unit normals as rendered (custom split normals)
    loop_uv         (L, 2) texture coordinates in the file's convention (V not flipped)
    loop_mat        (L,)   material index per corner
    mat_names / mat_image   per material: name, image key ('' = untextured)
    image_keys + img_<i>    (h, w, 4) uint8 top-down RGBA of every image referenced
    overlay_pos / overlay_normal   (creature only) the live gpu-overlay surface (fastsurf), SGM space

Compare with ``ICParse/tools/oe_geometry_compare.py FRAME.json OUT.npz``.
"""
import os
import sys

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, store, fastsurf  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(argv) < 2:
    print(__doc__); sys.exit(2)
SRC, OUT = argv[0], argv[1]
DENSITY = int(argv[argv.index("--density") + 1]) if "--density" in argv else 5
ANIM = argv[argv.index("--anim") + 1] if "--anim" in argv else None
T = float(argv[argv.index("--t") + 1]) if "--t" in argv else 0.0


def _pose(obj, is_creature):
    """Bake clip ANIM at time T onto frame 1 and go there (the rig deforms the evaluated meshes)."""
    from blender_native import rig
    arm = bpy.data.objects.get(obj.get("bz_rig", ""))
    names = list(obj.get("bz_anims", []))
    idx = next((i for i, nm in enumerate(names) if nm.lower() == ANIM.lower()), -1)
    assert arm is not None and idx >= 0, f"clip {ANIM!r} not on the rig (have {names[:8]}...)"
    if is_creature:
        rig.apply_animation(arm, core.load_creature(store.source_path(obj)).model, idx, frame_start=1, times=[T])
    else:
        rig.apply_scene_animation(arm, core.load_scene(obj.get("bz_source", "")), idx, frame_start=1, times=[T])
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()


def _image_of(mat):
    if mat is None or not mat.use_nodes:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
    return None


def _normals_to_sgm(N, M):
    """Blender-space normals of an object with world matrix M -> SGM-space unit normals."""
    R = np.array(M.to_3x3(), dtype=np.float64)
    Rn = np.linalg.inv(R).T
    out = N @ Rn.T
    out = core.blender_to_sgm(out)
    ln = np.linalg.norm(out, axis=1, keepdims=True); ln[ln == 0] = 1
    return out / ln


def dump_object(obj, depsgraph, mat_table):
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
        nv = len(me.vertices)
        co = np.empty(nv * 3); me.vertices.foreach_get("co", co); co = co.reshape(nv, 3)
        M = np.array(obj.matrix_world, dtype=np.float64)
        world = (np.concatenate([co, np.ones((nv, 1))], 1) @ M.T)[:, :3]
        pos_sgm = core.blender_to_sgm(world)
        nl = len(me.loops)
        loop_v = np.empty(nl, dtype=np.int64); me.loops.foreach_get("vertex_index", loop_v)
        cn = np.empty(nl * 3); me.corner_normals.foreach_get("vector", cn); cn = cn.reshape(nl, 3)
        n_sgm = _normals_to_sgm(cn, obj.matrix_world)
        uv = np.zeros((nl, 2))
        if me.uv_layers.active is not None:
            buf = np.empty(nl * 2); me.uv_layers.active.data.foreach_get("uv", buf); uv = buf.reshape(nl, 2)
        uv = uv.copy(); uv[:, 1] = 1.0 - uv[:, 1]
        pmat = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("material_index", pmat)
        loops, mats = [], []
        for lt in me.loop_triangles:
            loops.extend(lt.loops)
            mi = int(pmat[lt.polygon_index])
            mat = me.materials[mi] if mi < len(me.materials) else None
            key = mat.name if mat is not None else ""
            if key not in mat_table:
                img = _image_of(mat)
                mat_table[key] = (len(mat_table), img.name if img is not None else "", img)
            mats.extend([mat_table[key][0]] * 3)          # one entry per face-corner
        loops = np.asarray(loops, dtype=np.int64)
        return pos_sgm[loop_v[loops]], n_sgm[loops], uv[loops], np.asarray(mats, dtype=np.int64)
    finally:
        ev.to_mesh_clear()


def main():
    blender_native.register()
    res = bpy.ops.bz.import_sgm(filepath=SRC)
    assert res == {"FINISHED"}, res
    obj = bpy.context.active_object
    out = {}
    if store.is_native(obj):
        if ANIM:
            _pose(obj, True)
        bpy.context.scene.bz_density = DENSITY
        assert bpy.ops.bz.bake_surface(posed=bool(ANIM)) == {"FINISHED"}
        objs = [bpy.data.objects[obj.name + "_baked"]]
        out["kind"] = "creature"
        # the live overlay's surface (what the viewport shades), from the same evaluator draw.py uses;
        # posed: the evaluated (armature-deformed) control points, exactly as draw._current_verts reads them
        src_me = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data if ANIM else obj.data
        n = len(src_me.vertices)
        arr = np.empty(n * 3); src_me.vertices.foreach_get("co", arr)
        topo = fastsurf.TopoCache(store.patches(obj), DENSITY, store.patch_type(obj))
        rest = None
        if ANIM:
            rest = np.empty(len(obj.data.vertices) * 3); obj.data.vertices.foreach_get("co", rest); rest = rest.reshape(-1, 3)
        V, N = topo.eval_all(arr.reshape(n, 3), rest_verts=rest)
        out["overlay_pos"] = core.blender_to_sgm(V)
        out["overlay_normal"] = core.blender_to_sgm(N)
    else:
        from blender_native import scene as bzscene
        objs = bzscene.scene_objects()
        out["kind"] = "scene"
        if ANIM:
            _pose(next(o for o in objs if o.get("bz_rig")), False)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mat_table = {}
    P, N, UV, MI = [], [], [], []
    for o in objs:
        p, n_, uv, mi = dump_object(o, depsgraph, mat_table)
        P.append(p); N.append(n_); UV.append(uv); MI.append(mi)
    out["loop_pos"] = np.vstack(P); out["loop_normal"] = np.vstack(N); out["loop_uv"] = np.vstack(UV)
    out["loop_mat"] = np.concatenate(MI)
    order = sorted(mat_table.items(), key=lambda kv: kv[1][0])
    out["mat_names"] = np.array([k for k, _ in order], dtype=object).astype(str)
    out["mat_image"] = np.array([v[1] for _, v in order], dtype=object).astype(str)
    images = {}
    for _, (_, name, img) in order:
        if img is not None and name and name not in images:
            w, h = img.size
            buf = np.empty(w * h * 4, dtype=np.float32); img.pixels.foreach_get(buf)
            images[name] = np.rint(buf.reshape(h, w, 4)[::-1] * 255.0).astype(np.uint8)
    out["image_keys"] = np.array(list(images.keys()), dtype=object).astype(str)
    for i, (k, arr) in enumerate(images.items()):
        out[f"img_{i}"] = arr
    np.savez(OUT, **out)
    print(f"RENDER_DUMP_OK {out['kind']} loops={len(out['loop_pos'])} materials={len(order)} images={len(images)}"
          + (f" clip={ANIM} t={T:.4f}" if ANIM else "") + f" -> {OUT}")


main()
