"""Fit Patches to Mesh in Blender (roadmap A1): Suzanne's skin -> a fitted native creature.
    blender --background --python blender_native/tests/blender_fit_mesh_test.py

A. Suzanne with its two eye shells removed and two Catmull-Clark levels applied (the recipe of
   tools/fit_experiment.py; one closed X-symmetric skin, ~7k vertices), fitted with the QuadriFlow layout at budget
   300: FINISHED; the active object is a native creature with the SGM tables (patches, pool, limbs, bones) and
   ``bz_last_fit``; rms below 0.3 % of the diagonal (0.17 % in the experiment); symmetry kept; the ``fit_error``
   colour attribute is on the untouched target (vertex count and positions unchanged); Validate has no blocking
   line; Export re-exports byte-identical (the written .sgm is the creature's source) and the export passes
   ICParse's model check;
B. refusals: Suzanne WITH its eye shells (3 real shells, which a positional weld does not change) names the count,
   the boundary and true non-manifold edge counts apart, and what to do; a budget over the display-vertex ceiling
   (``fitops.MAX_PATCHES`` = 767, the best case of ICParse/PATCH_CEILING.md), and the two WARNINGS below it (over
   ``SPLIT_UV_PATCHES`` = 491 it may not load, over ``COMBINABLE_PATCHES`` = 263 it may not combine); a non-mesh active
   object (poll). Then the other half of the import contract: a SEAM-SPLIT Suzanne (its +X / -X vertices duplicated,
   as a UV atlaser leaves every glTF) is many parts as modelled, one surface welded, and is ACCEPTED and fits --
   leaving the target untouched;
C. the Own Quads layout on a quad sphere (a subdivided cube, all quads, no remesh): FINISHED, one patch per quad,
   rms below 0.5 % of the diagonal; the same sphere seam-split still gives the same 384-patch layout (the layout is
   built from a welded copy); and a triangle-bearing mesh (a UV sphere's poles) is refused with the count;
D. a target AT CREATURE SCALE (Suzanne's skin at 1/32, diagonal 0.114 m): Blender's QuadriFlow CANCELS on it at
   that size (asserted, 3/3 seeds) -- before the fix the operator refused with "QuadriFlow did not remesh
   SuzanneSmall (CANCELLED): it needs a closed, manifold mesh", which is what the native pillbug build hit -- and
   the fit now remeshes a copy scaled up by a power of two and scales the layout back exactly, giving the
   full-size Suzanne's own 286-patch layout and the same rms in % of the size.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time
import traceback

import numpy as np
import bpy
import bmesh
from mathutils import Matrix

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, fitops  # noqa: E402
from ICParse import validate as V  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

OUT = "/tmp/bz_fit_suzanne.sgm"                 # the Suzanne creature (run_all --engine loads it in the Object Editor)
REEXPORT = "/tmp/bz_fit_suzanne_out.sgm"
SPHERE = "/tmp/bz_fit_sphere.sgm"
SEAMS = "/tmp/bz_fit_seams.sgm"                 # the seam-split (UV-atlas) target's fit
SPHERE_SEAMS = "/tmp/bz_fit_sphere_seams.sgm"
SMALL = "/tmp/bz_fit_small.sgm"                 # the creature-scale target (D: QuadriFlow cancels at that size)
_log = open("/tmp/bz_fit_mesh_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def keep_largest_part(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data)
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
    return len(parts)


def seam_split(obj, name):
    """A copy of ``obj`` whose vertices are duplicated along the boundary between its +X and -X faces -- what a UV
    atlaser (xatlas, in every image-to-3D pipeline) does to every chart boundary, and what Bake Surface does to
    every patch seam. Same surface, same positions, more index-buffer parts."""
    me = obj.data.copy()
    new = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(new)
    bm = bmesh.new(); bm.from_mesh(me)
    right = {f for f in bm.faces if f.calc_center_median().x > 0.0}
    bmesh.ops.split_edges(bm, edges=[e for e in bm.edges if len(e.link_faces) == 2
                                     and (e.link_faces[0] in right) != (e.link_faces[1] in right)])
    bm.to_mesh(me); bm.free()
    me.update()
    return new


def make_suzanne(name, skin_only=True):
    bpy.ops.mesh.primitive_monkey_add(size=2.0)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    if skin_only:
        assert keep_largest_part(obj) == 3
    mod = obj.modifiers.new("subsurf", "SUBSURF"); mod.levels = 2; mod.render_levels = 2
    bpy.ops.object.modifier_apply(modifier="subsurf")
    return obj


def verts_of(obj):
    n = len(obj.data.vertices)
    arr = np.empty(n * 3); obj.data.vertices.foreach_get("co", arr)
    return arr.reshape(n, 3)


def activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def refused(words, **kw) -> str:
    """Run bz.fit_mesh expecting a refusal: an ERROR report raises RuntimeError in background mode; its message
    must contain every word. Nothing may be written."""
    try:
        res = bpy.ops.bz.fit_mesh(**kw)
    except RuntimeError as e:
        msg = str(e)
    else:
        raise AssertionError(f"not refused: {res}")
    for w in words:
        assert w in msg, (w, msg)
    assert not os.path.exists("/tmp/bz_fit_never.sgm")
    return msg


def run():
    blender_native.register()
    bpy.ops.wm.read_homefile(use_empty=True)
    for f in (OUT, REEXPORT, SPHERE, SEAMS, SPHERE_SEAMS, SMALL):
        if os.path.exists(f):
            os.remove(f)

    # --- A. Suzanne's skin, QuadriFlow at 300 ------------------------------------------------------------------
    target = make_suzanne("Suzanne")
    before = verts_of(target)
    diag = fitops.bounding_diagonal(before)
    assert fitops.is_x_symmetric(before, diag), fitops.symmetry(before, diag)
    say(f"target {target.name}: {len(before)} vertices, diag {diag:.3f}, X-symmetric")
    t = time.perf_counter()
    res = bpy.ops.bz.fit_mesh(budget=300, layout="QUADRIFLOW", output_path=OUT)
    secs = time.perf_counter() - t
    assert "FINISHED" in res, res
    creature = bpy.context.view_layer.objects.active
    assert creature is not target and store.is_native(creature), creature.name
    line = str(creature.get("bz_last_fit", ""))
    say(f"{line}\n  ({secs:.1f} s wall)")
    npat = int(creature[store.KEY_NPATCH])
    assert line.startswith(f"Fit {npat} patches to Suzanne: rms ") and "QuadriFlow" in line, line
    assert 200 <= npat <= 400, npat                          # QuadriFlow lands near the budget, not on it
    assert len(store.patches(creature)) == npat and len(creature.data.vertices) == int(creature[store.KEY_POOL_N])
    assert store.limb_names(creature) and store.bone_names(creature), "no limb / bone tables"
    assert store.source_path(creature) == OUT and os.path.exists(OUT)
    rms_pct = float(line.split("; ")[1].split(" / ")[0])     # "...; 0.17 / 0.37 / 1.53 % of size)..."
    max_pct = float(line.split(" / ")[2].split(" %")[0])
    assert rms_pct < 0.3, line
    assert max_pct < 3.0, line
    assert "symmetry kept (" in line, line
    # the target is untouched but for the heat map
    after = verts_of(target)
    assert len(after) == len(before) and np.array_equal(after, before)
    heat = target.data.color_attributes.get("fit_error")
    assert heat is not None and heat.domain == "POINT" and heat.data_type == "FLOAT_COLOR"
    cols = np.empty(len(heat.data) * 4); heat.data.foreach_get("color", cols)
    cols = cols.reshape(-1, 4)
    assert len(cols) == len(before) and cols[:, 3].min() == 1.0
    assert cols[:, 2].max() > 0.9 and cols[:, 2].min() < 0.1, "heat map is flat"       # some blue, some not
    # the creature's pool is exactly symmetric (the Suzanne fit is symmetrized)
    pool = verts_of(creature)
    pairs, plane = core.mirror_pairs(pool, 0, eps=fitops.SYMMETRY_EPS * diag)
    assert len(pairs) + len(plane) == len(pool), (len(pairs), len(plane), len(pool))
    # Validate: nothing blocks loading (the combine tier is off: this creature has no rings to combine with)
    bpy.context.scene.bz_combine.validate_level = "OFF"
    activate(creature)
    assert "FINISHED" in bpy.ops.bz.validate()
    problems = list(creature["bz_validation"])
    say("validate:", "\n  ".join(problems) if problems else "clean")
    assert not core.blocking(problems), core.blocking(problems)
    # Export re-exports byte-identical, and the file is a valid model
    assert "FINISHED" in bpy.ops.bz.export_sgm(filepath=REEXPORT)
    assert open(OUT, "rb").read() == open(REEXPORT, "rb").read(), "re-export differs from the written creature"
    m = SP.read(OUT)
    errs = [x for x in V.check_model(m) if not x.startswith("WARN")]
    assert len(m.patches) == npat and not errs, errs
    say(f"export byte-identical; {len(m.patches)} patches, {len(m.vertices)} points, check_model clean")

    # --- B. refusals --------------------------------------------------------------------------------------------
    eyes = make_suzanne("SuzanneEyes", skin_only=False)
    activate(eyes)
    etopo = fitops.target_topology(eyes.data, fitops.bounding_diagonal(verts_of(eyes)))
    assert (etopo.parts_raw, etopo.parts, etopo.welded) == (3, 3, 0), etopo   # real shells: the weld changes nothing
    say("eyes refused:", refused(["SuzanneEyes has 3 loose parts", "eyes inside sockets", "By Loose Parts",
                                  f"{etopo.boundary} boundary edges and {etopo.nonmanifold} true non-manifold edges"],
                                 budget=300, output_path="/tmp/bz_fit_never.sgm"))
    activate(target)
    # the ceiling is display VERTICES: 12288 in one DirectX 8 vertex set, 16 per patch at best
    say("budget refused:", refused([f"Patch budget 5000 cannot load", "12288 display vertices",
                                    f"{fitops.MAX_PATCHES} patches is the ceiling",
                                    "Size too large for VertexSet::Create()",
                                    f"About {fitops.SPLIT_UV_PATCHES} patches load",
                                    f"about {fitops.COMBINABLE_PATCHES} still combine"],
                                   budget=5000, output_path="/tmp/bz_fit_never.sgm"))
    # ...and the two warnings BELOW it, which do not stop the fit (checked on the operator, not a run)
    for budget, words in ((600, [f"over {fitops.SPLIT_UV_PATCHES}", "25 display vertices a patch", "12288"]),
                          (300, [f"over {fitops.COMBINABLE_PATCHES}", "the combiner adds a donor's limbs",
                                 str(fitops.ICV.WORST_STOCK_LIMB_SET)])):
        refusal, msg = fitops.budget_advice(budget)
        assert refusal is None and msg, (budget, refusal, msg)
        for w in words:
            assert w in msg, (budget, w, msg)
        say(f"budget {budget} warned:", msg)
    assert fitops.budget_advice(200) == (None, None), "a budget inside the combinable budget must not warn"
    activate(creature)
    assert not bpy.ops.bz.fit_mesh.poll(), "poll accepts a native creature"
    say("refusals: 3 loose parts, budget 5000, native creature (poll); warnings: budgets 600 and 300")

    # --- B2. the import contract: a seam-split target is ACCEPTED (weld first, then judge) ------------------------
    split = seam_split(target, "SuzanneSeams")
    before_split = verts_of(split).copy()
    stopo = fitops.target_topology(split.data, diag)
    assert fitops._loose_parts(split.data) == stopo.parts_raw > 1, (fitops._loose_parts(split.data), stopo)
    assert stopo.parts == 1 and stopo.welded > 0, stopo      # one surface once welded: UV seams, not floaters
    assert stopo.nonmanifold == 0, stopo                     # and the seams were never non-manifold
    activate(split)
    t = time.perf_counter()
    res = bpy.ops.bz.fit_mesh(budget=300, layout="QUADRIFLOW", output_path=SEAMS)
    assert "FINISHED" in res, res
    seamed = bpy.context.view_layer.objects.active
    assert store.is_native(seamed) and os.path.exists(SEAMS)
    line = str(seamed["bz_last_fit"])
    assert line.startswith(f"Fit {int(seamed[store.KEY_NPATCH])} patches to SuzanneSeams: rms "), line
    assert float(line.split("; ")[1].split(" / ")[0]) < 0.6, line
    assert np.array_equal(verts_of(split), before_split), "the seam-split target was modified by the fit"
    say(f"B2 accepted: SuzanneSeams is {stopo.parts_raw} parts as modelled ({stopo.welded} seam-duplicate "
        f"vertices, {stopo.boundary} boundary / {stopo.nonmanifold} true non-manifold edges after welding) and "
        f"fits in {time.perf_counter() - t:.1f} s\n  {line}")

    # --- C. Own Quads on a quad sphere, a triangle-bearing mesh refused ----------------------------------------
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    sphere = bpy.context.view_layer.objects.active
    sphere.name = "QuadSphere"
    mod = sphere.modifiers.new("sub", "SUBSURF"); mod.levels = 3
    bpy.ops.object.modifier_apply(modifier="sub")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.tosphere(value=1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    nq = len(sphere.data.polygons)
    assert nq == 384 and all(len(p.vertices) == 4 for p in sphere.data.polygons), nq
    activate(sphere)
    res = bpy.ops.bz.fit_mesh(layout="OWN_QUADS", output_path=SPHERE)
    assert "FINISHED" in res, res
    ball = bpy.context.view_layer.objects.active
    assert store.is_native(ball) and int(ball[store.KEY_NPATCH]) == nq
    line = str(ball["bz_last_fit"])
    say(line)
    assert line.startswith(f"Fit {nq} patches to QuadSphere: ") and "Own Quads" in line
    rms_ball = float(line.split("; ")[1].split(" / ")[0])
    assert rms_ball < 0.5, line
    # the same sphere split along its +X / -X seam: Own Quads must weld the copy it builds the layout from, so the
    # layout is the same 384 quads (a layout built on the seams would have holes where the duplicates are)
    ballseams = seam_split(sphere, "QuadSphereSeams")
    btopo = fitops.target_topology(ballseams.data, fitops.bounding_diagonal(verts_of(ballseams)))
    assert btopo.parts_raw == 2 and btopo.parts == 1 and btopo.welded == 32, btopo
    assert btopo.boundary == 0 and btopo.nonmanifold == 0, btopo
    activate(ballseams)
    res = bpy.ops.bz.fit_mesh(layout="OWN_QUADS", output_path=SPHERE_SEAMS)
    assert "FINISHED" in res, res
    ball2 = bpy.context.view_layer.objects.active
    line2 = str(ball2["bz_last_fit"])
    assert int(ball2[store.KEY_NPATCH]) == nq, (int(ball2[store.KEY_NPATCH]), nq)
    assert abs(float(line2.split("; ")[1].split(" / ")[0]) - rms_ball) < 0.05, (line2, line)
    say(f"C seam-split sphere: {btopo.parts_raw} parts as modelled, {btopo.welded} duplicate vertices welded -> "
        f"the same {nq}-patch layout\n  {line2}")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
    uv = bpy.context.view_layer.objects.active
    uv.name = "UVSphere"
    activate(uv)
    # tris-to-quads pairs most of the 32 pole triangles; 6 stay triangles, and that is the refusal
    say("UV sphere refused for Own Quads:", refused(["6 of UVSphere's 115 faces are not quads after tris-to-quads",
                                                     "or use the QuadriFlow layout"],
                                                    layout="OWN_QUADS", output_path="/tmp/bz_fit_never.sgm"))

    # --- D. a mesh AT CREATURE SCALE: QuadriFlow cancels on it, the fit normalises the size away -----------------
    # Measured 2026-09-16: the very same mesh finishes at its full size and cancels at 1/32 of it (diagonal 0.114 m,
    # what a real creature measures), which is what the native pillbug build hit -- and QuadriFlow's layout is
    # scale-invariant once it runs, so the scaled-up remesh gives the full-size layout back exactly.
    small = make_suzanne("SuzanneSmall")
    small.data.transform(Matrix.Diagonal((1 / 32.0, 1 / 32.0, 1 / 32.0, 1.0)))
    small.data.update()
    small_pts = verts_of(small)
    small_diag = fitops.bounding_diagonal(small_pts)
    assert 0.10 < small_diag < 0.13, small_diag
    activate(small)
    # the symptom, in this test: every seed CANCELS at the mesh's own size
    cancels = [fitops._remesh_at(bpy.context, small, small.data, 300, True, s, small_diag, None, 1.0) is None
               for s in range(3)]
    assert all(cancels), cancels
    ladder = fitops._scale_ladder(small_diag)
    assert ladder[0] == 1.0 and ladder[1] == 64.0, ladder            # powers of two: x * k / k is exact
    t = time.perf_counter()
    res = bpy.ops.bz.fit_mesh(budget=300, layout="QUADRIFLOW", output_path=SMALL)
    assert "FINISHED" in res, res
    tiny = bpy.context.view_layer.objects.active
    assert store.is_native(tiny) and os.path.exists(SMALL)
    line_small = str(tiny["bz_last_fit"])
    assert "remeshed on a copy scaled x64 and scaled back exactly" in line_small, line_small
    n_small = int(tiny[store.KEY_NPATCH])
    assert n_small == npat, (n_small, npat)                          # the full-size Suzanne's own layout, at 1/32
    rms_small = float(line_small.split("; ")[1].split(" / ")[0])
    assert abs(rms_small - rms_pct) < 0.05, (line_small, line)       # and the same fit quality, in % of the size
    pool_small = verts_of(tiny)
    assert abs(fitops.bounding_diagonal(pool_small) * 32.0 / fitops.bounding_diagonal(pool)) - 1.0 < 0.05
    assert np.array_equal(verts_of(small), small_pts), "the small target was modified by the fit"
    say(f"D creature-scale target: diagonal {small_diag:.3f} (QuadriFlow CANCELS at that size, 3/3 seeds), "
        f"fitted in {time.perf_counter() - t:.1f} s on a copy scaled x64\n  {line_small}")
    blender_native.unregister()


try:
    run()
    open("/tmp/bz_fit_mesh_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_fit_mesh_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
