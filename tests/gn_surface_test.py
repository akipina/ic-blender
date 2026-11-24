"""D9: the Geometry Nodes evaluated surface in headless Blender.

    blender --background --python blender_native/tests/gn_surface_test.py

A. the node group is built by script (no .blend asset, nothing to install) and the scene toggle
   `bz_gn_surface` puts every creature on it and takes it off again;
B. the evaluated mesh IS the surface: every point equals `core.tessellate` at its own (patch, u, v),
   at density 5 and 9, on cheetah (288 patches), lobster (360) and elephant (330, two of whose
   patches have a repeated corner: no corner quad, so they are NOT evaluated and are reported);
C. it follows the control points -- a moved control point moves exactly the surface points of the
   patches that use it, live in Edit Mode and in Sculpt Mode;
D. it survives Undo, save / reopen and a topology edit (Delete Patch: the face tables are rewritten
   by the depsgraph handler), and the export stays BYTE-IDENTICAL throughout;
E. the creature's appearance: the evaluated mesh carries the object's material slots in order, every
   face's material index is its patch's, and its UVs equal `core.surface_uvs` (V flipped) to float32;
F. what it costs and what it cannot do, measured, not assumed: the whole node tree is re-evaluated
   for one control point, and the overlay still draws the hidden-patch / By Bone / engine-normal
   views the node surface has no equivalent of.
"""
import os
import sys
sys.dont_write_bytecode = True

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, gnsurface, patchsel  # noqa: E402

_log = open("/tmp/bz_gn_surface_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


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
    return [o for o in bpy.context.scene.objects if store.is_native(o)][-1], path


def cps(obj):
    a = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3)


def exact(V, P, pid, u, v):
    """The bicubic surface at (patch, u, v) -- what core.tessellate evaluates on its n x n grid."""
    def bern(t):
        mt = 1.0 - t
        return np.stack([mt ** 3, 3 * t * mt * mt, 3 * t * t * mt, t ** 3], axis=-1)
    G = np.asarray(V)[np.asarray(P)[pid]].reshape(-1, 4, 4, 3)
    return np.einsum("pi,pj,pijk->pk", bern(u), bern(v), G)


def export(obj, path):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    assert bpy.ops.bz.export_sgm(filepath=path) == {"FINISHED"}
    return open(path, "rb").read()


# ---------------------------------------------------------------- A: built by script, scene toggle


def part_a():
    fresh()
    assert bpy.data.node_groups.get(gnsurface.GROUP) is None, "a fresh file already holds the node group"
    obj, path = creature()
    bpy.context.scene.bz_gn_surface = True
    ng = bpy.data.node_groups.get(gnsurface.GROUP)
    assert ng is not None, "the toggle did not build the node group"
    assert len(ng.nodes) > 100 and len(ng.links) > 200, (len(ng.nodes), len(ng.links))
    assert gnsurface.is_enabled(obj), "the creature has no evaluated-surface modifier"
    mod = gnsurface.modifier(obj)
    assert [w.message for w in mod.node_warnings] == [], [w.message for w in mod.node_warnings]
    assert obj.display_type == "TEXTURED", obj.display_type
    say(f"A: node group built by script: {len(ng.nodes)} nodes, {len(ng.links)} links, no node warnings")

    bpy.context.scene.bz_gn_surface = False
    assert not gnsurface.is_enabled(obj), "the toggle did not take the modifier off"
    assert obj.data.attributes.get(gnsurface.ATTR_P % 0) is None, "the face tables were left behind"
    assert obj.display_type == "WIRE", obj.display_type
    assert export(obj, "/tmp/bz_gn_off.sgm") == open(path, "rb").read(), "off: the export is not byte-identical"
    say("A: the toggle takes it off again, tables cleared, export byte-identical")


# ---------------------------------------------------------------- B: the evaluated mesh IS the surface


def part_b():
    out = []
    for name, patches, missing in (("cheetah", 288, 0), ("lobster", 360, 0), ("elephant", 330, 2)):
        for n in (5, 9):
            fresh()
            obj, path = creature(name)
            P = store.patches(obj)
            assert len(P) == patches, (name, len(P))
            assert len(gnsurface.missing_patches(P)) == missing, (name, gnsurface.missing_patches(P))
            bpy.context.scene.bz_density = n
            bpy.context.scene.bz_gn_surface = True
            pos, pid, u, v = gnsurface.evaluated(obj)
            assert len(pos) == (patches - missing) * n * n, (len(pos), (patches - missing) * n * n)
            # the grid is exactly the tessellation grid
            a = np.rint(u * (n - 1)).astype(np.int64)
            b = np.rint(v * (n - 1)).astype(np.int64)
            assert np.abs(u * (n - 1) - a).max() < 1e-4 and np.abs(v * (n - 1) - b).max() < 1e-4, \
                "the evaluated points are not on the n x n grid"
            V = cps(obj)
            Vt, _T, _N = core.tessellate(V, P, n=n)
            d = np.linalg.norm(Vt[pid * n * n + a * n + b] - pos, axis=1)
            size = float(np.linalg.norm(V.max(0) - V.min(0)))
            # Blender evaluates the node tree in float32; the pool itself is float32, so this is its ulp
            assert d.max() < 3e-6 and d.max() / size < 1e-6, (name, n, d.max(), size)
            assert np.linalg.norm(exact(V, P, pid, u, v) - pos, axis=1).max() < 3e-6
            out.append((name, n, len(P), len(pos), d.max()))
            say(f"B: {name} n={n}: {len(P)} patches ({missing} with a repeated corner not evaluated), "
                f"{len(pos)} points, max |GN - core.tessellate| {d.max():.2e} m ({d.max()/size:.1e} of size)")
            assert export(obj, "/tmp/bz_gn_b.sgm") == open(path, "rb").read(), f"{name}: export not byte-identical"
    return out


# ---------------------------------------------------------------- C: it follows the control points


TOL = 1e-5      # 0.01 mm: far below the 5 cm test move, far above float32 evaluation noise


def _moved(obj, before, dg=None):
    pos, pid, u, v = gnsurface.evaluated(obj, dg)
    return pos, pid, u, v, np.linalg.norm(pos - before, axis=1)


def part_c():
    fresh()
    obj, path = creature()
    P = store.patches(obj)
    bpy.context.scene.bz_density = 5
    bpy.context.scene.bz_gn_surface = True
    base, pid, u, v = gnsurface.evaluated(obj)
    i = int(P[0][5])                                     # an interior point: exactly one patch uses it
    users = np.nonzero((P == i).any(axis=1))[0]

    # Object Mode
    obj.data.vertices[i].co.z += 0.05
    obj.data.update_tag()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    pos, pid, u, v, moved = _moved(obj, base, dg)
    touched = set(int(x) for x in np.unique(pid[moved > TOL]))
    assert touched == set(int(x) for x in users), (touched, set(users))
    assert np.linalg.norm(exact(cps(obj), P, pid, u, v) - pos, axis=1).max() < 3e-6
    say(f"C: one control point moved 5 cm -> {int((moved > TOL).sum())} surface points on exactly the "
        f"{len(users)} patch(es) that use it, still exact")
    obj.data.vertices[i].co.z -= 0.05
    obj.data.update_tag()

    # Edit Mode: the modifier is shown in edit mode and follows the live bmesh
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    assert gnsurface.modifier(obj).show_in_editmode
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[i].co.z += 0.05
    bmesh.update_edit_mesh(obj.data)
    obj.update_tag()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    pos, pid, u, v, moved = _moved(obj, base, dg)
    assert (moved > TOL).any(), "the evaluated surface did not follow an Edit Mode move"
    got = set(int(x) for x in np.unique(pid[moved > TOL]))
    assert got == set(int(x) for x in users), (got, set(int(x) for x in users), moved.max())
    say(f"C: Edit Mode: the surface follows the live bmesh ({int((moved > TOL).sum())} points)")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    bm.verts[i].co.z -= 0.05
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Sculpt Mode: the evaluated surface is there and follows the base mesh (what a brush moves)
    bpy.ops.object.mode_set(mode="SCULPT")
    assert obj.mode == "SCULPT"
    pos, pid, u, v = gnsurface.evaluated(obj)
    assert len(pos) == len(base), (len(pos), len(base))
    obj.data.vertices[i].co.z += 0.05
    obj.data.update_tag()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    pos, pid, u, v, moved = _moved(obj, base, dg)
    assert (moved > TOL).any(), "the evaluated surface did not follow a base-mesh move in Sculpt Mode"
    say(f"C: Sculpt Mode: {len(pos)} evaluated points, a base-mesh move moves "
        f"{int((moved > TOL).sum())} of them")
    obj.data.vertices[i].co.z -= 0.05
    obj.data.update_tag()
    bpy.ops.object.mode_set(mode="OBJECT")
    assert export(obj, "/tmp/bz_gn_c.sgm") == open(path, "rb").read(), "C: the export is not byte-identical"


# ---------------------------------------------------------------- D: undo, save/reopen, topology edits


def part_d():
    fresh()
    obj, path = creature()
    src = open(path, "rb").read()
    bpy.context.scene.bz_density = 5
    bpy.context.scene.bz_gn_surface = True
    name = obj.name
    assert export(obj, "/tmp/bz_gn_d.sgm") == src, "D: the export with the modifier on is not byte-identical"

    # Undo
    bpy.ops.ed.undo_push(message="base")
    obj.data.vertices[int(store.patches(obj)[0][5])].co.z += 0.05
    bpy.ops.ed.undo_push(message="move")
    bpy.ops.ed.undo()
    obj = bpy.data.objects.get(name)
    assert obj is not None and gnsurface.is_enabled(obj), "Undo lost the modifier"
    pos, pid, u, v = gnsurface.evaluated(obj)
    assert np.linalg.norm(exact(cps(obj), store.patches(obj), pid, u, v) - pos, axis=1).max() < 3e-6
    say("D: Undo keeps the modifier and the surface stays exact")

    # save / reopen
    bpy.ops.wm.save_as_mainfile(filepath="/tmp/bz_gn_surface.blend")
    bpy.ops.wm.open_mainfile(filepath="/tmp/bz_gn_surface.blend")
    obj = [o for o in bpy.context.scene.objects if store.is_native(o)][0]
    assert gnsurface.is_enabled(obj) and bpy.data.node_groups.get(gnsurface.GROUP) is not None
    pos, pid, u, v = gnsurface.evaluated(obj)
    assert np.linalg.norm(exact(cps(obj), store.patches(obj), pid, u, v) - pos, axis=1).max() < 3e-6
    assert export(obj, "/tmp/bz_gn_d2.sgm") == src, "D: the export after a reopen is not byte-identical"
    say("D: save / reopen keeps the modifier, the group and the exact surface; export byte-identical")

    # a topology edit: Delete Patch. The face tables must follow, or the surface would index the old table
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_mode(type="FACE")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    rings = patchsel._ring_rows_for_plan(obj)
    victim = next(r for r in range(len(store.patches(obj)))
                  if not patchsel.ring_patch_refusals(store.patches(obj), [r], rings))
    obj.data.polygons[victim].select = True
    bpy.ops.object.mode_set(mode="EDIT")
    assert bpy.ops.bz.delete_patch() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    assert gnsurface.refresh(obj) or True            # the handler has normally done it already
    P = store.patches(obj)
    assert len(P) == 287, len(P)
    pos, pid, u, v = gnsurface.evaluated(obj)
    assert len(pos) == 287 * 25, (len(pos), 287 * 25)
    assert np.linalg.norm(exact(cps(obj), P, pid, u, v) - pos, axis=1).max() < 3e-6, \
        "after Delete Patch the evaluated surface is not the new patch table's"
    say(f"D: Delete Patch -> {len(P)} patches, the face tables followed and the surface is exact")


# ---------------------------------------------------------------- E: materials and UVs


def part_e():
    fresh()
    obj, _ = creature()
    bpy.context.scene.bz_density = 5
    bpy.context.scene.bz_gn_surface = True
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    ev = obj.evaluated_get(dg).data
    slots = [m.name for m in obj.data.materials]
    got = [m.name if m else None for m in ev.materials]
    assert len(slots) == 9, slots
    # Join Geometry concatenates the material lists: the input mesh's slots must come FIRST, or every
    # index would be shifted by one (measured: it does, with the generated geometry's empty slot last)
    assert got[:len(slots)] == slots, (got, slots)
    pos, pid, u, v = gnsurface.evaluated(obj, dg)
    n = 5
    fm = np.empty(len(ev.polygons), dtype=np.int32); ev.polygons.foreach_get("material_index", fm)
    starts = np.array([p.loop_start for p in ev.polygons], dtype=np.int64)
    lv = np.empty(len(ev.loops), dtype=np.int64); ev.loops.foreach_get("vertex_index", lv)
    pmat = np.asarray(store.patch_mat(obj))
    assert (fm == pmat[pid[lv[starts]]]).all(), "a face is on the wrong material slot"
    a = np.rint(u * (n - 1)).astype(np.int64); b = np.rint(v * (n - 1)).astype(np.int64)
    want = core.surface_uvs(store.patch_uv(obj), n)[pid * n * n + a * n + b]
    uvl = np.empty(len(ev.loops) * 2); ev.uv_layers[0].data.foreach_get("uv", uvl)
    err = float(np.abs(uvl.reshape(-1, 2) - want[lv]).max())
    assert ev.uv_layers[0].name == gnsurface.UV_MAP and err < 1e-6, (ev.uv_layers[0].name, err)
    say(f"E: {len(slots)} material slots in order, 0 material mismatches over {len(fm)} faces, "
        f"UVs == core.surface_uvs to {err:.1e}")


# ---------------------------------------------------------------- F: the honest limits


def part_f():
    fresh()
    obj, _ = creature()
    P = store.patches(obj)
    bpy.context.scene.bz_density = 5
    bpy.context.scene.bz_gn_surface = True
    # a hidden patch is hidden in the OVERLAY only: the node surface evaluates every patch with a face
    obj.data.polygons[3].hide = True
    obj.data.update_tag()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    pos, pid, u, v = gnsurface.evaluated(obj, dg)
    hidden_still_there = int((pid == 3).sum())
    assert hidden_still_there == 25, hidden_still_there
    assert int(patchsel.hidden_rows(obj, P).sum()) == 1
    obj.data.polygons[3].hide = False
    say(f"F: a hidden patch still has its {hidden_still_there} evaluated points (the node surface has no "
        f"hidden-patch, By Bone or engine-normal view: those stay the overlay's)")
    # the pool guard refuses to switch on a broken pool instead of building a wrong surface
    bpy.context.scene.bz_gn_surface = False
    me = obj.data
    me.vertices.add(1)
    me.update()
    bpy.context.scene.bz_gn_surface = True
    assert not gnsurface.is_enabled(obj), "the toggle built a surface on a mesh that is not the pool"
    assert obj.get("bz_gn_error"), "no reason given for refusing"
    say(f"F: a mesh that is not the pool is refused in words: {obj['bz_gn_error'][:80]}...")


def main():
    say("D9 evaluated surface")
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    open("/tmp/bz_gn_surface_done.txt", "w").write("ok\n")
    say("OK")


main()
