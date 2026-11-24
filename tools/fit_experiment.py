"""Feasibility experiment: fit a bicubic Bezier patch network to an arbitrary mesh.

    xvfb-run -a blender --background --python blender_native/tools/fit_experiment.py -- [--out DIR] [--quads 300,800,2000]

Target: Suzanne with two levels of Catmull-Clark applied (a dense smooth mesh, ~8k vertices). For each quad
count: QuadriFlow remesh -> ICParse.codecs.acc.cage_to_patches (the Catmull-Clark-style initial surface)
-> ICParse.codecs.fit.fit_points against the target's vertices. Reports the target-to-surface distance
before and after the fit (rms / p95 / max, absolute and as % of the bounding-box diagonal) and the surface-
to-target coverage, writes a JSON, and renders target / initial / fitted side by side plus an error heat
map on the target mesh. Nothing is written outside --out.
"""
import json
import functools
print = functools.partial(print, flush=True)
import os
import sys
import time

import bpy
import bmesh
import numpy as np

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "ICParse"))
sys.path.insert(0, os.path.join(REPO, "blender_native", "_deps"))
from ICParse.codecs import acc, bezier as BE, fit  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[argv.index("--out") + 1] if "--out" in argv else "/tmp/bz_fit"
QUADS = [int(q) for q in (argv[argv.index("--quads") + 1] if "--quads" in argv else "300,800,2000").split(",")]
SKIN_ONLY = "--with-eyes" not in argv
SMOOTH = float(argv[argv.index("--smooth") + 1]) if "--smooth" in argv else 0.02
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_homefile(use_empty=True)
scene = bpy.context.scene


def mesh_arrays(obj):
    me = obj.data
    V = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", V)
    return V.reshape(-1, 3)


def make_target(skin_only=True):
    """Suzanne, two Catmull-Clark levels applied. With ``skin_only`` the two eye shells (separate closed parts
    inside the sockets) are removed, leaving one closed skin -- what a creature conversion actually fits."""
    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    obj = bpy.context.view_layer.objects.active
    obj.name = "target"
    if skin_only:
        bm = bmesh.new(); bm.from_mesh(obj.data)
        # keep the largest connected part
        seen = set(); parts = []
        for f in bm.faces:
            if f in seen:
                continue
            stack = [f]; part = []
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x); part.append(x)
                for e in x.edges:
                    stack.extend(g for g in e.link_faces if g not in seen)
            parts.append(part)
        parts.sort(key=len, reverse=True)
        bmesh.ops.delete(bm, geom=[f for part in parts[1:] for f in part], context="FACES")
        bm.to_mesh(obj.data); bm.free()
    mod = obj.modifiers.new("subsurf", "SUBSURF"); mod.levels = 2; mod.render_levels = 2
    bpy.ops.object.modifier_apply(modifier="subsurf")
    bpy.ops.object.shade_smooth()
    return obj


def mesh_normals(obj):
    me = obj.data
    N = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("normal", N)
    return N.reshape(-1, 3)


def remesh(target, quads):
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True); bpy.context.view_layer.objects.active = target
    bpy.ops.object.duplicate()
    cage = bpy.context.view_layer.objects.active
    cage.name = f"cage_{quads}"
    bpy.ops.object.quadriflow_remesh(target_faces=quads, use_mesh_symmetry=False, use_preserve_sharp=False, seed=0)
    bm = bmesh.new(); bm.from_mesh(cage.data)
    coords = np.array([tuple(v.co) for v in bm.verts])
    faces = [[v.index for v in f.verts] for f in bm.faces]
    nonquad = sum(1 for f in faces if len(f) != 4)
    bm.free()
    return cage, coords, [f for f in faces if len(f) == 4], nonquad


def stats(d, diag):
    d = np.asarray(d)
    return {"rms": float(np.sqrt(np.mean(d * d))), "p95": float(np.percentile(d, 95)), "max": float(d.max()),
            "rms_pct": 100 * float(np.sqrt(np.mean(d * d))) / diag, "p95_pct": 100 * float(np.percentile(d, 95)) / diag,
            "max_pct": 100 * float(d.max()) / diag}


def bake_object(name, pool, patches, n=7):
    V, T, _ = BE.tessellate(pool, patches, n=n, split="main")
    me = bpy.data.meshes.new(name); me.from_pydata(V.tolist(), [], T.tolist())
    for p in me.polygons:
        p.use_smooth = True
    ob = bpy.data.objects.new(name, me); scene.collection.objects.link(ob)
    return ob


def heat_material(name):
    mat = bpy.data.materials.new(name); mat.use_nodes = True
    nt = mat.node_tree
    attr = nt.nodes.new("ShaderNodeVertexColor"); attr.layer_name = "heat"
    bsdf = nt.nodes["Principled BSDF"]
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def paint_heat(obj, d, dmax):
    me = obj.data
    col = me.color_attributes.new("heat", "FLOAT_COLOR", "POINT")
    t = np.clip(np.asarray(d) / dmax, 0, 1)
    # blue (0) -> green -> red (dmax)
    rgba = np.stack([np.clip(2 * t - 1, 0, 1), 1 - np.abs(2 * t - 1), np.clip(1 - 2 * t, 0, 1), np.ones_like(t)], 1)
    col.data.foreach_set("color", rgba.reshape(-1))
    obj.data.materials.append(heat_material(obj.name + "_heat"))


def render(objs, path, dx=2.6):
    for o in bpy.data.objects:
        o.hide_render = True
    for i, o in enumerate(objs):
        o.hide_render = False
        o.location = ((i - (len(objs) - 1) / 2) * dx, 0, 0)
        o.rotation_euler = (0.35, 0, 0.5)
    cam = bpy.data.objects.get("cam")
    if cam is None:
        cd = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cd); scene.collection.objects.link(cam)
        cam.location = (0, -12, 3.5); cam.rotation_euler = (1.32, 0, 0)
        cd.lens = 50
        scene.camera = cam
        light = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN")); scene.collection.objects.link(light)
        light.rotation_euler = (0.6, 0.3, 0.8); light.data.energy = 3.0
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"; scene.display.shading.color_type = "VERTEX" if any(o.name.startswith("heat") for o in objs) else "MATERIAL"
    scene.render.resolution_x = 1800; scene.render.resolution_y = 700
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


target = make_target(SKIN_ONLY)
P = mesh_arrays(target)
NRM = mesh_normals(target)
diag = float(np.linalg.norm(P.max(0) - P.min(0)))
report = {"target_vertices": int(len(P)), "bbox_diag": diag, "skin_only": SKIN_ONLY, "smooth": SMOOTH, "runs": []}
print(f"FIT target {len(P)} vertices, diag {diag:.3f}")

for quads in QUADS:
    t0 = time.time()
    cage, coords, faces, nonquad = remesh(target, quads)
    pool0, patches = acc.cage_to_patches(len(coords), [tuple(map(float, c)) for c in coords], faces, None)
    pool0 = np.asarray(pool0, dtype=np.float64); patches = np.asarray(patches, dtype=np.int64)
    t1 = time.time()
    _, _, _, d0 = fit.project(P, pool0, patches, n=9)
    r = fit.fit_points(pool0, patches, P, iters=10, smooth=SMOOTH, normals=NRM)
    t2 = time.time()
    run = {"quads": quads, "patches": int(len(patches)), "pool": int(len(pool0)), "nonquad_faces": nonquad,
           "before": stats(d0, diag), "after": stats(r.residual, diag), "coverage": stats(r.coverage, diag),
           "history": r.history, "seconds_cage": t1 - t0, "seconds_fit": t2 - t1}
    report["runs"].append(run)
    print(f"FIT {quads} quads -> {len(patches)} patches / {len(pool0)} pool points; before rms {run['before']['rms_pct']:.3f}% "
          f"max {run['before']['max_pct']:.2f}% | after rms {run['after']['rms_pct']:.3f}% p95 {run['after']['p95_pct']:.3f}% "
          f"max {run['after']['max_pct']:.2f}% | coverage max {run['coverage']['max_pct']:.2f}% | {t2 - t1:.1f}s")
    init = bake_object(f"initial_{quads}", pool0, patches)
    fitted = bake_object(f"fitted_{quads}", r.pool, patches)
    heat = target.copy(); heat.data = target.data.copy(); heat.name = f"heat_{quads}"; scene.collection.objects.link(heat)
    paint_heat(heat, r.residual, 0.01 * diag)                      # red = 1 % of the diagonal or more
    cage.hide_render = True
    render([target, init, fitted], os.path.join(OUT, f"fit_{quads}.png"))
    render([heat], os.path.join(OUT, f"heat_{quads}.png"))
    np.save(os.path.join(OUT, f"pool_{quads}.npy"), r.pool); np.save(os.path.join(OUT, f"patches_{quads}.npy"), patches)

json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
print("FIT DONE", os.path.join(OUT, "report.json"))
