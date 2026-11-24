"""In-Blender test: scene objects (structure / character / static prop) import through the add-on. Run:
    blender --background --python blender_native/tests/scene_import_test.py
Checks each part against ICParse's decode (vertex + triangle counts, stored normals as custom split
normals, UVs), the resolved textures, and the skeleton/skin (henchman: 26 bones, per-vertex groups;
structure parts rigidly bound to one bone each). Blender accepts every part's stored normals; a part whose normals it refuses
is marked and counted (the importer warns), never passed over.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "blender_native", "tests"))
import _so_common  # noqa: E402
import blender_native  # noqa: E402
from blender_native import core, scene as bzscene  # noqa: E402
from ICParse import read_scene  # noqa: E402

SCENE = os.path.join(REPO, "Assets", "engine_scene")
CASES = [("henchmen_lab.ebp", "structure"), ("henchmen.sgm", "character"), ("10mwaterfall.sgm", "prop")]

_log = open(os.path.join(_so_common.tmpdir("scene_import_"), "log.txt"), "w")
_op = print
def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def check(path, kind):
    before = set(o.name for o in bpy.data.objects)
    res = bpy.ops.bz.import_sgm(filepath=path)
    assert res == {"FINISHED"}, res
    objs = [o for o in bzscene.scene_objects() if o.name not in before]
    assert objs, "no scene part objects created"
    n_bad, first = bzscene.normals_errors(objs)
    assert n_bad == 0, f"Blender refused the stored normals of {n_bad} part(s): {first}"
    src = core.resolve_source(path)
    sc = read_scene(src)
    meshes = [m for m in sc.meshes if m.positions]
    assert len(objs) == len(meshes), (len(objs), len(meshes))
    by_name = {o.name: o for o in objs}
    parts = core.load_scene(src).parts
    for part, mm in zip(parts, meshes):
        o = next(o for o in objs if o.get("bz_scene_part") == parts.index(part))
        me = o.data
        assert len(me.vertices) == len(mm.positions), (o.name, len(me.vertices), len(mm.positions))
        ntri = sum(1 for s in mm.submeshes for t in s.triangles if len(set(t)) == 3)
        assert len(me.polygons) == ntri, (o.name, len(me.polygons), ntri)
        # positions: stored SGM -> Blender axis map, exactly
        co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
        assert np.allclose(co.reshape(-1, 3), core.sgm_to_blender(np.asarray(mm.positions)), atol=1e-6)
        if mm.normals:
            assert me.has_custom_normals, o.name + ": stored normals not applied"
        if mm.uvs:
            assert me.uv_layers.active is not None
        if mm.skin and any(mm.skin):
            assert o.vertex_groups and o.modifiers.get("Armature") is not None, o.name + ": skin missing"
        elif mm.bind is not None:
            assert len(o.vertex_groups) == 1 and o.modifiers.get("Armature") is not None, o.name + ": rigid bind missing"
    textured = sum(1 for o in objs for mt in o.data.materials if mt and any(n.type == "TEX_IMAGE" and n.image for n in mt.node_tree.nodes))
    arm = bpy.data.objects.get(objs[0].get("bz_rig", "")) if objs[0].get("bz_rig") else None
    nb = len(arm.data.bones) if arm else 0
    if sc.bones and (any(m.skinned for m in meshes) or any(m.bind is not None for m in meshes)):
        assert arm is not None and nb == len(sc.bones), (nb, len(sc.bones))
    print(f"SCENE_OK {kind:9s} {os.path.basename(path):24s} parts={len(objs)} verts={sum(len(o.data.vertices) for o in objs)} "
          f"textured_materials={textured} bones={nb} clips={len(objs[0].get('bz_anims', []))}")
    return objs, arm, sc


def main():
    blender_native.register()
    print("REGISTER_OK")
    for fn, kind in CASES:
        path = os.path.join(SCENE, fn)
        if not os.path.exists(path):
            print(f"SKIP {fn} (missing; run blender_native/tools/extract_stock.py --scene)"); continue
        objs, arm, sc = check(path, kind)
        if kind == "character" and arm is not None and sc.animations:
            bpy.context.view_layer.objects.active = objs[0]
            res = bpy.ops.bz.load_anim(anim="0")
            assert res == {"FINISHED"}, res
            assert arm.animation_data and arm.animation_data.action, "no action after load_anim"
            print(f"ANIM_OK {sc.animations[0].name} frames={bpy.context.scene.frame_end}")
    # normals Blender refuses (here: two numbers per vertex instead of three) are kept on the part as an error and counted
    path = os.path.join(SCENE, "10mwaterfall.sgm")
    if os.path.exists(path):
        data = core.load_scene(path)
        data.parts[0].normals = np.asarray(data.parts[0].normals)[:, :2]
        objs = bzscene.build_scene(bpy.context, data, source_path=path, clips_as_actions=False)
        n_bad, first = bzscene.normals_errors(objs)
        part0 = next(o for o in objs if o.get(bzscene.KEY_SCENE_PART) == 0)
        assert n_bad == 1 and first and part0.get(bzscene.KEY_NORMALS_ERROR) == first, (n_bad, first)
        print(f"NORMALS_ERROR_OK counted and kept on the part: {first[:80]}")
    print("SCENE_IMPORT_PASS")


_so_common.run_test("scene_import", main)
