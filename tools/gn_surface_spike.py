"""D9 spike: measure the Geometry Nodes evaluated surface against the gpu overlay.

    blender --background --factory-startup --python-exit-code 1 \
        --python blender_native/tools/gn_surface_spike.py -- [--render] [--out /tmp/bz_gn_spike.json]

Every question the D9 brief asks is answered here by a MEASUREMENT, printed and written to JSON:

  parity   the evaluated mesh vs ``core.tessellate`` at the same (patch, u, v)
  (a)      Sculpt Mode: is the evaluated surface there, and does it follow the control net
  (b)      downstream modifiers (Shrinkwrap / Boolean / Mirror), ray-cast selection, snapping
  (c)      EEVEE / Cycles render with the creature's materials and UVs, no Bake Surface
  (d)      cost: node-tree evaluation at 300 and 800 patches vs the overlay's dirty-patch evaluator
  (e)      Edit Mode live update, Undo, save / reopen, the journal and export byte-identity
  (f)      shipping: the node group is built by script, so there is nothing for the user to install

``--render`` adds the EEVEE / Cycles renders (Cycles CPU works headless; EEVEE needs a GL context,
so run the whole thing under ``xvfb-run -a``).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.dont_write_bytecode = True

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import blender_native  # noqa: E402
from blender_native import core, store, gnsurface, fastsurf, patchsel  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DO_RENDER = "--render" in ARGS
OUT = "/tmp/bz_gn_spike.json"
if "--out" in ARGS:
    OUT = ARGS[ARGS.index("--out") + 1]

R = {}
_log = open("/tmp/bz_gn_spike_log.txt", "w")


def say(*a):
    s = " ".join(str(x) for x in a)
    _log.write(s + "\n"); _log.flush()
    print(s, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    try:
        blender_native.unregister()
    except RuntimeError:
        pass
    blender_native.register()


def creature(name="cheetah"):
    path = f"{REPO}/Assets/engine_stock/{name}.sgm"
    bpy.ops.bz.import_sgm(filepath=path)
    obj = [o for o in bpy.context.scene.objects if store.is_native(o)][-1]
    return obj, path


def cps(obj):
    me = obj.data
    a = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def ref_at(V, P, pid, u, v):
    """The exact bicubic surface at those (patch, u, v) -- the same evaluator core.tessellate uses."""
    def bern(t):
        mt = 1.0 - t
        return np.stack([mt ** 3, 3 * t * mt * mt, 3 * t * t * mt, t ** 3], axis=-1)
    G = np.asarray(V)[np.asarray(P)[pid]].reshape(-1, 4, 4, 3)
    return np.einsum("pi,pj,pijk->pk", bern(u), bern(v), G)


# ------------------------------------------------------------------ parity with core.tessellate


def part_parity():
    out = {}
    # elephant is the honest case: 2 of its 330 patches have a repeated corner, so they have no corner
    # quad to carry their table row and the node surface cannot evaluate them
    for name in ("cheetah", "lobster", "elephant"):
        for n in (5, 8):
            fresh()
            obj, _ = creature(name)
            P = store.patches(obj)
            gnsurface.enable(obj, density=n)
            pos, pid, u, v = gnsurface.evaluated(obj)
            V = cps(obj)
            # 1. against the exact evaluator at the point's own (u, v)
            d_exact = np.linalg.norm(ref_at(V, P, pid, u, v) - pos, axis=1)
            # 2. against core.tessellate's array, matched by (patch, a, b): u is the row (i) direction
            Vt, _T, _N = core.tessellate(V, P, n=n)
            a = np.rint(u * (n - 1)).astype(np.int64)
            b = np.rint(v * (n - 1)).astype(np.int64)
            assert np.allclose(a, u * (n - 1), atol=1e-4) and np.allclose(b, v * (n - 1), atol=1e-4), \
                "the evaluated points are not on the n x n grid"
            d_tess = np.linalg.norm(Vt[pid * n * n + a * n + b] - pos, axis=1)
            size = float(np.linalg.norm(V.max(0) - V.min(0)))
            out[f"{name}@{n}"] = {
                "patches": int(len(P)), "points": int(len(pos)), "expect_points": int(len(P) * n * n),
                "max_vs_tessellate_m": float(d_tess.max()), "mean_vs_tessellate_m": float(d_tess.mean()),
                "max_vs_exact_m": float(d_exact.max()),
                "rel_max_of_size": float(d_tess.max() / size), "size_m": size,
                "missing_patches": [int(x) for x in gnsurface.missing_patches(P)],
            }
            say(f"  parity {name} n={n}: {len(P)} patches, {len(pos)} points, "
                f"max |GN - core.tessellate| = {d_tess.max():.3e} m ({d_tess.max()/size:.2e} of size)")
    R["parity"] = out


# ------------------------------------------------------------------ (d) cost


def _synth(n_patches):
    """A control mesh with ``n_patches`` patches (the cheetah's patch table tiled), for timing only."""
    d = core.load_creature(f"{REPO}/Assets/engine_stock/cheetah.sgm")
    V, P = np.asarray(d.verts, dtype=np.float64), np.asarray(d.patches, dtype=np.int64)
    k = int(np.ceil(n_patches / len(P)))
    Vs = np.concatenate([V + np.array([i * 3.0, 0, 0]) for i in range(k)])
    Ps = np.concatenate([P + i * len(V) for i in range(k)])[:n_patches]
    me = bpy.data.meshes.new(f"synth{n_patches}")
    rows, quads = patchsel.face_quads(Ps)
    me.from_pydata([tuple(x) for x in Vs.tolist()], [], [tuple(int(i) for i in q) for q in quads])
    me.update()
    obj = bpy.data.objects.new(f"synth{n_patches}", me)
    bpy.context.collection.objects.link(obj)
    return obj, Vs, Ps


def _time(fn, repeat=5):
    ts = []
    for _ in range(repeat):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return float(np.median(ts))


def part_cost():
    out = {}
    try:                       # viewport FPS: there is no window in --background
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        out["redraw_timer"] = "available"
    except Exception as e:
        out["redraw_timer"] = f"not measurable headlessly: {type(e).__name__}: {e}"
    for np_target, n in ((300, 5), (800, 4), (800, 5), (800, 8)):
        fresh()
        obj, V, P = _synth(np_target)
        gnsurface.write_tables(obj, patches=P, uvs=np.zeros((len(P), 8)))
        ng = gnsurface.node_group()
        mod = obj.modifiers.new(gnsurface.MODIFIER, "NODES")
        mod.node_group = ng
        gnsurface.set_input(mod, "Density", n)
        gnsurface.set_input(mod, "Weld Distance", 0.0)

        dg = bpy.context.evaluated_depsgraph_get()

        def gn_full():
            obj.data.update_tag()
            dg.update()
            _ = len(obj.evaluated_get(dg).data.vertices)

        def gn_one_point():
            obj.data.vertices[7].co.z += 1e-4
            obj.data.update_tag()
            dg.update()
            _ = len(obj.evaluated_get(dg).data.vertices)

        gn_full()
        t_full = _time(gn_full)
        t_one = _time(gn_one_point)
        ev = obj.evaluated_get(dg).data
        exec_ms = float(mod.execution_time) * 1000.0

        # the overlay's evaluator, doing what a redraw does: everything once, then only the dirty patches
        topo = fastsurf.TopoCache(P, n)
        surfV, surfN = topo.eval_all(V)
        rows = sorted(topo.patches_for_dirty_verts([7]))
        t_ov_full = _time(lambda: topo.eval_all(V))
        t_ov_dirty = _time(lambda: topo.eval_dirty(V, surfV, surfN, rows), repeat=20)

        out[f"{np_target}@{n}"] = {
            "patches": int(len(P)), "density": n,
            "surface_points": int(len(ev.vertices)), "faces": int(len(ev.polygons)),
            "gn_full_eval_ms": t_full * 1000, "gn_one_point_move_ms": t_one * 1000,
            "gn_modifier_execution_ms": exec_ms,      # 0 in --background: Blender fills it from the UI timer
            "overlay_eval_all_ms": t_ov_full * 1000, "overlay_eval_dirty_ms": t_ov_dirty * 1000,
            "dirty_patches_for_one_point": len(rows),
        }
        say(f"  cost {len(P)} patches @ n={n}: GN whole-tree {t_full*1000:.1f} ms / one point moved "
            f"{t_one*1000:.1f} ms vs overlay all {t_ov_full*1000:.1f} ms / dirty {t_ov_dirty*1000:.2f} ms "
            f"({len(rows)} patches)")
    R["cost"] = out


# ------------------------------------------------------------------ (a) sculpt mode


def part_sculpt():
    fresh()
    obj, _ = creature()
    P = store.patches(obj)
    gnsurface.enable(obj, density=5)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    out = {}
    try:
        bpy.ops.object.mode_set(mode="SCULPT")
        out["entered_sculpt"] = obj.mode == "SCULPT"
    except Exception as e:
        out["entered_sculpt"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    if out.get("entered_sculpt"):
        pos0, pid, u, v = gnsurface.evaluated(obj)
        out["surface_points_in_sculpt"] = int(len(pos0))
        out["is_evaluated_surface"] = bool(len(pos0) == len(P) * 25)
        # a brush moves base vertices; move one the same way and see the evaluated surface follow
        i = int(P[0][0])
        obj.data.vertices[i].co.z += 0.05
        obj.data.update_tag()
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        pos1, pid1, u1, v1 = gnsurface.evaluated(obj, dg)
        moved = np.linalg.norm(pos1 - pos0, axis=1)
        out["surface_points_moved"] = int((moved > 1e-6).sum())
        out["max_surface_move_m"] = float(moved.max())
        V = cps(obj)
        out["still_exact_max_m"] = float(np.linalg.norm(ref_at(V, P, pid1, u1, v1) - pos1, axis=1).max())
        bpy.ops.object.mode_set(mode="OBJECT")
    # the honest limit: what IS the sculptable original mesh?
    out["original_faces"] = int(len(obj.data.polygons))
    out["original_verts"] = int(len(obj.data.vertices))
    say(f"  (a) sculpt: entered={out.get('entered_sculpt')} evaluated surface points "
        f"{out.get('surface_points_in_sculpt')} moved {out.get('surface_points_moved')} "
        f"max {out.get('max_surface_move_m', 0):.4f} m")
    R["sculpt"] = out


# ------------------------------------------------------------------ (b) modifiers / snapping / ray cast


class Probe:
    """Point-to-SURFACE distance, through a hidden dense triangle mesh of the exact bicubic surface
    (the Bake Surface geometry at n=13). Distances are point-to-triangle, not point-to-sample, so a
    hit that is really on the surface measures ~0 whatever the sampling."""

    def __init__(self, obj, n=13):
        V, P = cps(obj), store.patches(obj)
        Vt, T, _N = core.tessellate(V, P, n=n)
        me = bpy.data.meshes.new("bz_probe")
        me.from_pydata(Vt.tolist(), [], T.tolist())
        me.update()
        self.obj = bpy.data.objects.new("bz_probe", me)
        bpy.context.collection.objects.link(self.obj)
        self.obj.hide_set(True)
        self.obj.hide_render = True
        self.samples = Vt

    def dist(self, p):
        ok, loc, nor, idx = self.obj.closest_point_on_mesh(tuple(p))
        return float(np.linalg.norm(np.array(loc) - np.asarray(p, dtype=np.float64))) if ok else None

    def dists(self, pts):
        return np.array([self.dist(p) for p in np.asarray(pts, dtype=np.float64)])


def part_downstream():
    out = {}
    for on in (True, False):
        fresh()
        obj, _ = creature()
        if on:
            gnsurface.enable(obj, density=5)
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        probe = Probe(obj)
        V = cps(obj)
        key = "gn_on" if on else "gn_off"
        res = {}
        if on:      # how exact the metric itself is: the evaluated points against the reference mesh
            pos, _pid, _u, _v = gnsurface.evaluated(obj, dg)
            res["metric_noise_floor_m"] = float(probe.dists(pos[::37]).max())
            # and the other direction: the evaluated mesh is the n x n TESSELLATION, so its flat cells
            # are chords of the exact surface. This is the game's own 5x5 grid, not an approximation we
            # introduced -- but it is what a ray cast / snap / shrinkwrap actually lands on.
            ev0 = obj.evaluated_get(dg)
            cho = [float(np.linalg.norm(np.array(ev0.closest_point_on_mesh(tuple(p))[1]) - p))
                   for p in probe.samples[::53]]
            res["tessellation_chord_error_max_m"] = float(max(cho))
            res["tessellation_chord_error_mean_m"] = float(np.mean(cho))

        # ray-cast selection: what the viewport's click-select and Blender's own ray casts hit
        lo, hi = V.min(0), V.max(0)
        mid = (lo + hi) / 2
        hits, offs = 0, []
        rng = np.random.default_rng(7)
        for _ in range(60):
            o = np.array([mid[0] + rng.uniform(-0.4, 0.4), mid[1] + rng.uniform(-0.2, 0.2), hi[2] + 1.0])
            hit, loc, nor, idx, ob, mat = bpy.context.scene.ray_cast(dg, o.tolist(), (0, 0, -1))
            if hit and ob is not None and ob.name == obj.name:
                hits += 1
                offs.append(probe.dist(loc))
        res["ray_cast_hits_of_60"] = hits
        res["ray_cast_max_off_surface_m"] = float(max(offs)) if offs else None
        res["ray_cast_mean_off_surface_m"] = float(np.mean(offs)) if offs else None

        # snapping / closest point: Blender snaps to the EVALUATED mesh
        ev = obj.evaluated_get(dg)
        probes = [(mid[0], mid[1], hi[2] + 0.3), (lo[0] - 0.3, mid[1], mid[2]), (mid[0], hi[1] + 0.3, mid[2])]
        snapped = []
        for p in probes:
            ok, loc, nor, idx = ev.closest_point_on_mesh(p)
            snapped.append(probe.dist(loc) if ok else None)
        res["snap_off_surface_m"] = snapped
        res["evaluated_faces"] = int(len(ev.data.polygons))

        # Shrinkwrap: a grid pulled onto the creature
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=1.0,
                                        location=(mid[0], mid[1], hi[2] + 0.5))
        grid = bpy.context.active_object
        sw = grid.modifiers.new("sw", "SHRINKWRAP")
        sw.target = obj
        sw.wrap_method = "NEAREST_SURFACEPOINT"
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        gme = grid.evaluated_get(dg).data
        gv = np.empty(len(gme.vertices) * 3); gme.vertices.foreach_get("co", gv)
        gv = gv.reshape(-1, 3) + np.array(grid.matrix_world.translation)
        d = probe.dists(gv)
        res["shrinkwrap_max_off_surface_m"] = float(d.max())
        res["shrinkwrap_mean_off_surface_m"] = float(d.mean())

        # Boolean: a cube cut by the creature
        bpy.ops.mesh.primitive_cube_add(size=0.4, location=tuple(mid))
        cube = bpy.context.active_object
        n_before = len(cube.data.vertices)
        bo = cube.modifiers.new("bool", "BOOLEAN")
        bo.operation = "DIFFERENCE"
        bo.object = obj
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        res["boolean_verts_before"] = n_before
        res["boolean_verts_after"] = int(len(cube.evaluated_get(dg).data.vertices))

        # Mirror stacked after the surface, on the creature itself
        mi = obj.modifiers.new("mirror", "MIRROR")
        mi.use_axis = (False, True, False)
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        res["mirror_verts"] = int(len(obj.evaluated_get(dg).data.vertices))
        obj.modifiers.remove(mi)

        out[key] = res
        say(f"  (b) {key}: ray hits {res['ray_cast_hits_of_60']}/60 max off "
            f"{res['ray_cast_max_off_surface_m']}, snap {res['snap_off_surface_m']}, "
            f"shrinkwrap max {res['shrinkwrap_max_off_surface_m']:.4f}, boolean verts "
            f"{res['boolean_verts_before']} -> {res['boolean_verts_after']}, mirror {res['mirror_verts']}")
    R["downstream"] = out


# ------------------------------------------------------------------ (c) render


def _render(engine, path, res=256, samples=16):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.filepath = path
    sc.render.image_settings.file_format = "PNG"
    if engine == "CYCLES":
        sc.cycles.samples = samples
        sc.cycles.device = "CPU"
    else:
        try:
            sc.eevee.taa_render_samples = samples
        except Exception:
            pass
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:]).reshape(-1, 4)
    bpy.data.images.remove(img)
    return px


def _frame(obj):
    """Camera and light for a side-on view of the creature (its long axis is Y)."""
    V = cps(obj)
    mid, lo, hi = (V.min(0) + V.max(0)) / 2, V.min(0), V.max(0)
    size = float(np.linalg.norm(hi - lo))
    bpy.ops.object.camera_add(location=(mid[0] - size * 1.5, mid[1], mid[2]))
    cam = bpy.context.active_object
    cam.rotation_euler = (np.deg2rad(90), 0, np.deg2rad(-90))
    bpy.context.scene.camera = cam
    bpy.ops.object.light_add(type="SUN", location=(mid[0] - size, mid[1], mid[2] + size))
    bpy.context.active_object.rotation_euler = (np.deg2rad(50), 0, np.deg2rad(-60))


ENGINES = ("CYCLES", "BLENDER_EEVEE")


def part_render():
    out = {}
    images = {}
    # three renders from the same camera: the evaluated surface, the control mesh as it is today
    # (its corner quads DO render), and Bake Surface -- the workaround D9 is meant to replace.
    for key in ("gn_on", "gn_weld", "gn_off", "bake"):
        fresh()
        obj, _ = creature()
        if key.startswith("gn_") and key != "gn_off":
            V = cps(obj)
            size = float(np.linalg.norm(V.max(0) - V.min(0)))
            gnsurface.enable(obj, density=5, weld=(size * 1e-5 if key == "gn_weld" else 0.0))
        elif key == "bake":
            bpy.context.view_layer.objects.active = obj
            bpy.context.scene.bz_density = 5
            bpy.ops.bz.bake_surface()
            obj.hide_render = True
        _frame(obj)
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        shown = bpy.data.objects.get(obj.name + "_baked") or obj
        ev = shown.evaluated_get(dg).data
        rec = {"materials": len(ev.materials), "material_names": [m.name if m else None for m in ev.materials],
               "object_materials": [m.name for m in shown.data.materials],
               "uv_layers": [u.name for u in ev.uv_layers],
               "evaluated_faces": int(len(ev.polygons)), "evaluated_verts": int(len(ev.vertices))}
        if key == "gn_on":
            # the UVs and material of every evaluated point / face against the stored tables, so "with the
            # creature's materials and UVs" is a number, not an impression
            pos, pid, u, v = gnsurface.evaluated(obj, dg)
            n = 5
            a = np.rint(u * (n - 1)).astype(np.int64); b = np.rint(v * (n - 1)).astype(np.int64)
            want = core.surface_uvs(store.patch_uv(obj), n)[pid * n * n + a * n + b]
            uvl = np.empty(len(ev.loops) * 2, dtype=np.float64); ev.uv_layers[0].data.foreach_get("uv", uvl)
            lv = np.empty(len(ev.loops), dtype=np.int64); ev.loops.foreach_get("vertex_index", lv)
            rec["uv_max_error"] = float(np.abs(uvl.reshape(-1, 2) - want[lv]).max())
            fm = np.empty(len(ev.polygons), dtype=np.int32); ev.polygons.foreach_get("material_index", fm)
            fpid = pid[lv[np.array([p.loop_start for p in ev.polygons], dtype=np.int64)]]
            pmat = store.patch_mat(obj)
            rec["material_index_mismatches"] = int((fm != np.asarray(pmat)[fpid]).sum())
        for engine in ENGINES:
            path = f"/tmp/bz_gn_{key}_{engine.lower()}.png"
            try:
                px = _render(engine, path)
                bg = px[0]
                diff = np.abs(px[:, :3] - bg[:3]).max(axis=1)
                cov = float((diff > 0.02).mean())
                images[(key, engine)] = px
                rec[engine] = {"coverage": cov, "path": path,
                               "mean_rgb": [float(x) for x in px[diff > 0.02, :3].mean(0)] if cov else None}
                say(f"  (c) {key} {engine}: coverage {cov*100:.1f} %, {rec['evaluated_faces']} faces, "
                    f"{rec['materials']} materials, UVs {rec['uv_layers']} -> {path}")
            except Exception as e:
                rec[engine] = {"error": f"{type(e).__name__}: {e}"}
                say(f"  (c) {key} {engine}: FAILED {type(e).__name__}: {e}")
        out[key] = rec
    # how close each is to the Bake Surface render (the reference the add-on ships today)
    for engine in ENGINES:
        ref = images.get(("bake", engine))
        if ref is None:
            continue
        bg = ref[0, :3]
        ref_on = np.abs(ref[:, :3] - bg).max(axis=1) > 0.02
        for key in ("gn_on", "gn_weld", "gn_off"):
            px = images.get((key, engine))
            if px is None:
                continue
            here = np.abs(px[:, :3] - bg).max(axis=1) > 0.02
            mask = ref_on | here
            d = np.abs(px[:, :3] - ref[:, :3])
            out[key].setdefault("vs_bake", {})[engine] = {
                "rms_all_pixels": float(np.sqrt((d ** 2).mean())),
                "rms_on_the_creature": float(np.sqrt((d[mask] ** 2).mean())),
                "silhouette_xor_pixels": int((ref_on ^ here).sum()), "silhouette_pixels": int(ref_on.sum()),
                "pixels_differing_by_2pct": float((d.max(axis=1) > 0.02).mean())}
            r = out[key]["vs_bake"][engine]
            say(f"  (c) {key} vs bake ({engine}): rms on the creature {r['rms_on_the_creature']:.4f} "
                f"(all pixels {r['rms_all_pixels']:.4f}), silhouette differs by "
                f"{r['silhouette_xor_pixels']} of {r['silhouette_pixels']} pixels")
    R["render"] = out


# ------------------------------------------------------------------ (e) edit mode / undo / save / export


def part_survival():
    import bmesh
    fresh()
    obj, path = creature()
    src = open(path, "rb").read()
    P = store.patches(obj)
    gnsurface.enable(obj, density=5)
    out = {}

    # Export byte-identity with the modifier on
    tmp = "/tmp/bz_gn_export.sgm"
    res = bpy.ops.bz.export_sgm(filepath=tmp)
    out["export_result"] = list(res)
    out["export_byte_identical"] = bool(os.path.exists(tmp) and open(tmp, "rb").read() == src)

    # Edit Mode: does the evaluated surface follow a live bmesh move?
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    pos0, _, _, _ = gnsurface.evaluated(obj)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    i = int(P[0][0])
    bm.verts[i].co.z += 0.05
    bmesh.update_edit_mesh(obj.data)
    obj.update_tag()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    pos1, pid1, u1, v1 = gnsurface.evaluated(obj, dg)
    moved = np.linalg.norm(pos1 - pos0, axis=1)
    out["editmode_points_moved"] = int((moved > 1e-6).sum())
    out["editmode_max_move_m"] = float(moved.max())
    out["editmode_show_in_editmode"] = bool(gnsurface.modifier(obj).show_in_editmode)
    bpy.ops.object.mode_set(mode="OBJECT")
    # put it back
    obj.data.vertices[i].co.z -= 0.05
    obj.data.update_tag()

    # the pool guard and the journal are untouched by the tables and the modifier
    out["pool_error"] = store.pool_error(obj)

    # Undo (the object struct is replaced by an undo step: find it again by name)
    name = obj.name
    bpy.ops.ed.undo_push(message="gn spike")
    obj.data.vertices[i].co.z += 0.05
    bpy.ops.ed.undo_push(message="move")
    bpy.ops.ed.undo()
    obj2 = bpy.data.objects.get(name)
    out["undo_kept_modifier"] = bool(obj2 is not None and gnsurface.is_enabled(obj2))
    if obj2 is not None:
        dg = bpy.context.evaluated_depsgraph_get(); dg.update()
        pos, pid, u, v = gnsurface.evaluated(obj2, dg)
        out["undo_max_vs_exact_m"] = float(np.linalg.norm(
            ref_at(cps(obj2), store.patches(obj2), pid, u, v) - pos, axis=1).max())
    obj = obj2

    # save / reopen
    blend = "/tmp/bz_gn_spike.blend"
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    obj3 = [o for o in bpy.context.scene.objects if store.is_native(o)][0]
    out["reopen_has_modifier"] = bool(gnsurface.is_enabled(obj3))
    out["reopen_has_group"] = bool(bpy.data.node_groups.get(gnsurface.GROUP) is not None)
    pos, pid, u, v = gnsurface.evaluated(obj3)
    V = cps(obj3)
    out["reopen_max_vs_exact_m"] = float(np.linalg.norm(ref_at(V, store.patches(obj3), pid, u, v) - pos, axis=1).max())
    tmp2 = "/tmp/bz_gn_export2.sgm"
    bpy.ops.bz.export_sgm(filepath=tmp2)
    out["reopen_export_byte_identical"] = bool(os.path.exists(tmp2) and open(tmp2, "rb").read() == src)
    say(f"  (e) export identical {out['export_byte_identical']} / after reopen "
        f"{out['reopen_export_byte_identical']}; edit-mode points moved {out['editmode_points_moved']}; "
        f"undo kept {out['undo_kept_modifier']}; reopen {out['reopen_has_modifier']}/{out['reopen_has_group']} "
        f"exact {out['reopen_max_vs_exact_m']:.2e}")
    R["survival"] = out


# ------------------------------------------------------------------ (f) shipping


def part_shipping():
    fresh()
    out = {"group_in_fresh_file": bool(bpy.data.node_groups.get(gnsurface.GROUP) is not None)}
    obj, _ = creature()
    gnsurface.enable(obj, density=5)
    ng = bpy.data.node_groups.get(gnsurface.GROUP)
    out["built_by_script"] = ng is not None
    out["nodes"] = len(ng.nodes) if ng else 0
    out["links"] = len(ng.links) if ng else 0
    out["blend_assets_needed"] = 0
    out["node_warnings"] = [w.message for w in gnsurface.modifier(obj).node_warnings]
    say(f"  (f) group built by script: {out['built_by_script']} ({out['nodes']} nodes, {out['links']} links), "
        f"pre-existing group in a fresh file: {out['group_in_fresh_file']}, warnings {out['node_warnings']}")
    R["shipping"] = out


PARTS = {"parity": part_parity, "cost": part_cost, "sculpt": part_sculpt, "downstream": part_downstream,
         "render": part_render, "survival": part_survival, "shipping": part_shipping}


def main():
    say("D9 Geometry Nodes spike")
    only = ARGS[ARGS.index("--only") + 1].split(",") if "--only" in ARGS else None
    for name, fn in PARTS.items():
        if only is not None:
            if name not in only:
                continue
        elif name == "render" and not DO_RENDER:
            continue
        say(" " + name)
        fn()
    with open(OUT, "w") as f:
        json.dump(R, f, indent=1)
    say("wrote", OUT)


main()
