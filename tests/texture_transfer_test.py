"""Transfer Texture (roadmap A4), headless Blender (Cycles CPU bake).
    blender --background --python blender_native/tests/texture_transfer_test.py
    BZ_RENDER=1 xvfb-run -a blender --background --python ...   # + the pillbug before / after renders (needs GL)

A. a quad sphere (subdivided cube, 384 quads, the cube's own UV map) with an 8 x 8 checker image is fitted with
   Own Quads (bz.fit_mesh) -> the creature records its source; Transfer Texture at 512 (density 7): FINISHED, the
   patch UVs are the per-patch atlas, the creature's texture table is the one atlas image on every slot, coverage
   above 95 %; at 5 surface points per patch the atlas colour equals the source colour at the same world position
   (the nearest source face's UV, the checker looked up there) on at least 90 % of the samples; Export writes ONE
   256-colour texture named after the creature with every material's TXTR on it and the file's UVs = the atlas; a
   re-import shows the transferred texture (decoded atlas within the palette error); Undo -> byte-identical export.
   Refusals: a source with no UV layer, a source with no image, a sculpt in progress.
B. the pillbug (skipped with a SKIP line when 3DInputs/Pillbug_ic is not there): Pillbug.glb imported (the scan
   with its 2048^2 Image_0 and UVs), placed with the retarget's recorded alignment (rotation, scale, shift from
   results.json), the retargeted creature (pillbug_lm.sgm if A3b made one, else pillbug.sgm) loaded, Transfer
   Texture at 2048 -> exported to /tmp/bz_pillbug_textured.sgm (run_all --engine loads it in the Object Editor),
   re-imported (texture is the transferred one), Undo byte-identical; with BZ_RENDER=1 a before / after render.
"""
import json
import math
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, texops  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import texture as ICtex  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

SPHERE_FIT = "/tmp/bz_transfer_sphere_fit.sgm"
SPHERE_OUT = "/tmp/bz_transfer_sphere.sgm"       # run_all --engine loads it
SPHERE_UNDO = "/tmp/bz_transfer_sphere_undo.sgm"
PILL_DIR = os.path.join(REPO, "3DInputs", "Pillbug_ic")
PILL_GLB = os.path.join(REPO, "3DInputs", "Pillbug.glb")
PILL_OUT = "/tmp/bz_pillbug_textured.sgm"        # run_all --engine loads it
PILL_UNDO = "/tmp/bz_pillbug_undo.sgm"
RENDER = os.environ.get("BZ_RENDER") == "1"
PARTS = os.environ.get("BZ_PARTS", "AB").upper()     # BZ_PARTS=A runs the sphere only (iterating on the bake)
_log = open("/tmp/bz_texture_transfer_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"


def activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def refused(words, **kw) -> str:
    r, msg = call(bpy.ops.bz.transfer_texture, **kw)
    assert r == {"CANCELLED"}, f"not refused: {r} {msg}"
    for w in words:
        assert w in msg, (w, msg)
    return msg


def export(obj, path):
    if os.path.exists(path):
        os.remove(path)
    activate(obj)
    r, msg = call(bpy.ops.bz.export_sgm, filepath=path)
    assert r == {"FINISHED"}, msg
    return open(path, "rb").read()


def checker_image(name, size=256, squares=8):
    img = bpy.data.images.new(name, size, size, alpha=True)
    yy, xx = np.mgrid[0:size, 0:size]
    c = ((xx * squares // size + yy * squares // size) % 2).astype(np.float32)
    px = np.empty((size, size, 4), dtype=np.float32)
    px[..., 0] = 0.9 * c + 0.1 * (1 - c)
    px[..., 1] = 0.2 * c + 0.4 * (1 - c)
    px[..., 2] = 0.1 * c + 0.9 * (1 - c)
    px[..., 3] = 1.0
    img.pixels.foreach_set(px.ravel())
    img.pack()
    return img, px


def image_material(name, img):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    t = mat.node_tree.nodes.new("ShaderNodeTexImage"); t.image = img
    mat.node_tree.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def quad_sphere(name):
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    o = bpy.context.view_layer.objects.active
    o.name = name
    mod = o.modifiers.new("sub", "SUBSURF"); mod.levels = 3
    bpy.ops.object.modifier_apply(modifier="sub")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.tosphere(value=1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    assert len(o.data.polygons) == 384 and o.data.uv_layers, (len(o.data.polygons), list(o.data.uv_layers))
    return o


def pixels_of(img):
    w, h = img.size
    a = np.empty(w * h * 4, dtype=np.float32); img.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def sample(px, u, v):
    """Nearest texel of a bottom-up (h, w, 4) array at Blender UV (u, v)."""
    h, w = px.shape[:2]
    x = min(w - 1, max(0, int(u * w))); y = min(h - 1, max(0, int(v * h)))
    return px[y, x, :3]


def source_colour_at(src, deps, px, point):
    """The source's image colour at the world point's nearest surface point (face UVs interpolated)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform, closest_point_on_tri
    bvh = source_colour_at._bvh
    if bvh is None:
        bvh = source_colour_at._bvh = BVHTree.FromObject(src, deps)
    me = src.evaluated_get(deps).data
    uvl = me.uv_layers.active.data
    loc, _n, fi, _d = bvh.find_nearest(Vector(point))
    poly = me.polygons[fi]
    vs = [me.vertices[i].co for i in poly.vertices]
    uvs = [Vector((uvl[l].uv[0], uvl[l].uv[1], 0.0)) for l in poly.loop_indices]
    best = None
    for tri in ((0, 1, 2), (0, 2, 3)) if len(vs) == 4 else ((0, 1, 2),):
        cp = closest_point_on_tri(loc, vs[tri[0]], vs[tri[1]], vs[tri[2]])
        d = (cp - loc).length
        if best is None or d < best[0]:
            best = (d, tri)
    tri = best[1]
    uv = barycentric_transform(loc, vs[tri[0]], vs[tri[1]], vs[tri[2]], uvs[tri[0]], uvs[tri[1]], uvs[tri[2]])
    return sample(px, uv.x % 1.0, uv.y % 1.0)


source_colour_at._bvh = None


def colour_check(creature, src, src_px, atlas_px, uv, n_patches):
    """At the 4 quarter points + centre of every patch: the atlas colour at that surface point's atlas UV vs the
    source's colour at the same world position. Returns (matches, samples)."""
    deps = bpy.context.evaluated_depsgraph_get()
    source_colour_at._bvh = None
    from blender_native import ops as bzops
    V, _T, _N = core.tessellate(bzops._mesh_verts(creature), store.patches(creature), n=5)
    ts = np.linspace(0, 1, 5)
    ok = n = 0
    for p in range(n_patches):
        u0, v0, _u0, v1, u1, _v0, _u1, _v1 = uv[p]
        for a, b in ((2, 2), (1, 1), (1, 3), (3, 1), (3, 3)):
            pt = V[p * 25 + a * 5 + b]
            s, t = ts[a], ts[b]
            u = u0 + s * (u1 - u0); v = v0 + t * (v1 - v0)          # file v (top-down)
            got = sample(atlas_px, u, 1.0 - v)
            want = source_colour_at(src, deps, src_px, pt)
            n += 1
            ok += int(np.abs(got - want).max() < 0.2)
    return ok, n


def check_export_file(path, tex_name, size, atlas_px, n_patches, uv):
    m = SP.read(path)
    assert len(m.textures) == 1, [t.name for t in m.textures]
    t = m.textures[0]
    assert t.name == tex_name and (t.width, t.height) == (size, size), (t.name, t.width, t.height)
    assert t.palette_entries <= 256 and t.mip_count == int(math.log2(size)) + 1, (t.palette_entries, t.mip_count)
    for mat in m.materials:
        tx = [v for tag, v in mat.tags if tag == "TXTR"]
        assert tx and tx[0] == tex_name, (mat.name, mat.tags)
        # no stage may name a texture the new table dropped (the engine only has what is embedded)
        stray = [(tag, v) for tag, v in mat.tags if tag in ("TXTR", "TXRF", "TXSP") and v and v != tex_name]
        assert not stray, (mat.name, stray)
    blocking = [p for p in ICval.check_model(m) if not p.startswith(("WARN", "STOCK", "COMBINE"))]
    assert not blocking, blocking
    file_uv = np.array([p.uvs for p in m.patches], dtype=np.float64)
    assert len(m.patches) == n_patches and np.abs(file_uv - uv).max() < 1e-6, np.abs(file_uv - uv).max()
    dec = np.frombuffer(ICtex.decode_texture(t, 0), dtype=np.uint8).reshape(size, size, 4)[::-1, :, :3] / 255.0
    err = float(np.abs(dec - atlas_px[:, :, :3]).mean())
    return m, err


def load_json(path):
    with open(path) as f:
        return json.load(f)


# ------------------------------------------------------------------------------------------------ A. checker sphere
def part_a():
    fresh()
    for f in (SPHERE_FIT, SPHERE_OUT, SPHERE_UNDO):
        if os.path.exists(f):
            os.remove(f)
    src = quad_sphere("CheckerSphere")
    img, src_px = checker_image("checker")
    src.data.materials.append(image_material("checker_mat", img))
    activate(src)
    r, msg = call(bpy.ops.bz.fit_mesh, layout="OWN_QUADS", output_path=SPHERE_FIT)
    assert r == {"FINISHED"}, msg
    creature = bpy.context.view_layer.objects.active
    assert store.is_native(creature) and creature.get("bz_fit_source") == "CheckerSphere", creature.get("bz_fit_source")
    n_patches = int(creature[store.KEY_NPATCH])
    assert n_patches == 384
    fit_bytes = open(SPHERE_FIT, "rb").read()
    old_uv = store.patch_uv(creature).copy()
    old_imgs = store.texture_images(creature)
    say(f"A fitted {creature.name}: {n_patches} patches, source textures {old_imgs}")

    # refusals first (nothing written)
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    plane = bpy.context.view_layer.objects.active; plane.name = "NoUV"
    plane.data.uv_layers.remove(plane.data.uv_layers[0])
    bpy.ops.mesh.primitive_plane_add(size=1.0)
    bare = bpy.context.view_layer.objects.active; bare.name = "NoImage"
    activate(creature)
    say("refused (no UV):", refused(["NoUV has no UV layer"], source="NoUV"))
    say("refused (no image):", refused(["NoImage's materials show no image texture"], source="NoImage"))
    bpy.context.scene.bz_density = 5
    r, msg = call(bpy.ops.bz.sculpt_surface)
    assert r == {"FINISHED"}, msg
    activate(creature)
    say("refused (sculpt):", refused(["has a sculpt in progress", "Refit Patches or discard"], source="CheckerSphere"))
    sc = next(o for o in bpy.data.objects if o.get("bz_sculpt_of") == creature.name)
    activate(sc)
    r, msg = call(bpy.ops.bz.discard_sculpt)
    assert r == {"FINISHED"}, msg
    assert np.array_equal(store.patch_uv(creature), old_uv) and store.texture_images(creature) == old_imgs

    # the transfer (the default source = the recorded fit source)
    activate(creature)
    bpy.ops.ed.undo_push(message="fitted")
    t0 = time.perf_counter()
    r, msg = call(bpy.ops.bz.transfer_texture, size="512", density=7)
    secs = time.perf_counter() - t0
    assert r == {"FINISHED"}, msg
    line = str(creature.get(texops.KEY_LAST, ""))
    st = dict(creature["bz_last_transfer_stats"])
    say(f"A {line} ({secs:.1f} s wall; dilated {st['dilated']}, mean-filled {st['filled']})")
    assert line.startswith(f"Transferred checker onto {creature.name}: 512x512, 384 patches (20x20 cells of 26 px)"), line
    assert st["coverage"] > 0.95, st["coverage"]
    uv = store.patch_uv(creature)
    assert np.abs(uv - texops.atlas_uvs(384, 512, 4)).max() < 1e-9
    atlas = bpy.data.images[st["image"]]
    assert atlas.size[0] == 512 and store.texture_images(creature) == [atlas.name]
    slots, tex, _cols = store.material_slots(creature)
    assert tex == [atlas.name] * len(slots), (tex, slots)
    assert store.texture_names(creature) == [st["texture"]] and store.slot_texture_names(creature) == [st["texture"]] * len(slots)
    atlas_px = pixels_of(atlas)
    from blender_native import material
    key = material.magenta_key_mask((np.clip(atlas_px, 0, 1) * 255).round().astype(np.uint8).reshape(-1, 4))
    assert not key.any(), f"{int(key.sum())} magenta (colour-keyed) texels in the atlas"
    ok, n = colour_check(creature, src, src_px, atlas_px, uv, n_patches)
    say(f"A colour check: {ok} / {n} samples match the source at the same world position ({100 * ok / n:.1f} %)")
    assert ok >= 0.9 * n, (ok, n)

    # export: one texture, every material on it, the atlas UVs; re-import shows it
    data = export(creature, SPHERE_OUT)
    m, err = check_export_file(SPHERE_OUT, st["texture"], 512, atlas_px, n_patches, uv)
    say(f"A export {len(data)} bytes: 1 texture '{m.textures[0].name}' {m.textures[0].palette_entries} colours, "
        f"decoded vs atlas mean error {err:.4f}; summary: {creature.get('bz_last_export', '')[:200]}")
    assert err < 0.05, err

    # Undo -> byte-identical to the fitted creature (before the re-import, which pushes its own undo step)
    cname = creature.name
    activate(creature)
    bpy.ops.ed.undo()
    creature = bpy.data.objects[cname]
    assert np.array_equal(store.patch_uv(creature), old_uv), "undo did not restore the UVs"
    assert store.texture_images(creature) == old_imgs and store.texture_names(creature) is None
    undo_bytes = export(creature, SPHERE_UNDO)
    assert undo_bytes == fit_bytes, "undo export differs from the fitted creature"
    say("A undo: byte-identical")

    r, msg = call(bpy.ops.bz.import_sgm, filepath=SPHERE_OUT, clips_as_actions=False)
    assert r == {"FINISHED"}, msg
    re = bpy.context.view_layer.objects.active
    imgs = store.texture_images(re)
    assert len(imgs) == 1 and bpy.data.images[imgs[0]].size[0] == 512, imgs
    re_px = pixels_of(bpy.data.images[imgs[0]])
    err2 = float(np.abs(re_px[:, :, :3] - atlas_px[:, :, :3]).mean())
    assert err2 < 0.05, err2
    assert np.abs(store.patch_uv(re) - uv).max() < 1e-6
    say(f"A re-import: texture {imgs[0]} (mean error vs the atlas {err2:.4f}), UVs = atlas")
    return {"coverage": st["coverage"], "colour_ok": ok, "colour_n": n, "secs": st["seconds"], "palette_err": err}


# ------------------------------------------------------------------------------------------------ B. the pillbug
def render_creature(creature, path, label):
    """Workbench render (TEXTURE colour) of the creature's textured bake, 3/4 view; needs GL (xvfb-run)."""
    from mathutils import Vector
    scene = bpy.context.scene
    old = bpy.data.objects.get(creature.name + "_baked")
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    scene.bz_density = 7
    activate(creature)
    engine = scene.render.engine
    scene.render.engine = "BLENDER_EEVEE"
    r, msg = call(bpy.ops.bz.bake_surface)
    scene.render.engine = engine
    assert r == {"FINISHED"}, msg
    baked = bpy.data.objects[creature.name + "_baked"]
    for o in scene.objects:
        o.hide_render = True
    baked.hide_render = False
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = 1200, 800
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    sh = scene.display.shading
    sh.light = "STUDIO"; sh.show_shadows = False; sh.show_cavity = False; sh.color_type = "TEXTURE"
    sh.background_type = "WORLD"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.color = (0.92, 0.92, 0.92)
    scene.view_settings.view_transform = "Standard"
    bb = np.array([baked.matrix_world @ Vector(c) for c in baked.bound_box])
    c = Vector(((bb.min(0) + bb.max(0)) / 2).tolist())
    ortho = float(np.linalg.norm(bb.max(0) - bb.min(0))) * 1.05
    cam = bpy.data.objects.get("RenderCam")
    if cam is None:
        cam = bpy.data.objects.new("RenderCam", bpy.data.cameras.new("RenderCam")); scene.collection.objects.link(cam)
    cam.data.type = "ORTHO"; cam.data.ortho_scale = ortho; cam.data.clip_end = 100
    d = Vector((1.0, -1.0, 0.75)).normalized()
    cam.location = c + d * 10
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(baked, do_unlink=True)
    say(f"B render {label}: {path}")


def part_b():
    sgm = os.path.join(PILL_DIR, "pillbug_lm.sgm")
    if not os.path.exists(sgm):
        sgm = os.path.join(PILL_DIR, "pillbug.sgm")
    res_json = os.path.join(PILL_DIR, "results.json")
    if not (os.path.exists(PILL_GLB) and os.path.exists(sgm) and os.path.exists(res_json)):
        say("B SKIP: 3DInputs/Pillbug.glb + Pillbug_ic/pillbug.sgm + results.json not present")
        return None
    fresh()
    for f in (PILL_OUT, PILL_UNDO):
        if os.path.exists(f):
            os.remove(f)
    from mathutils import Matrix, Vector
    res = load_json(res_json)
    bpy.ops.import_scene.gltf(filepath=PILL_GLB)
    scan = [o for o in bpy.context.selected_objects if o.type == "MESH"][0]
    img = texops.source_image(scan)
    assert img is not None and scan.data.uv_layers, (img, list(scan.data.uv_layers))
    say(f"B scan '{scan.name}': {len(scan.data.vertices)} vertices, {len(scan.data.polygons)} faces, image "
        f"'{img.name}' {img.size[0]}x{img.size[1]}, UV layer '{scan.data.uv_layers[0].name}'")
    # the retarget's alignment: rotate the scan's head onto the donor's heading, then the fine pass's scale + shift
    # (make_pillbug rotated the CLEAN scan's mesh data about Z, then the retarget aligned it: clean * scale + shift)
    angle = math.radians(float(res["scan_rotation_deg_about_z"]))
    scale = float(res["retarget"]["scale"])
    shift = Vector([float(x) for x in res["retarget"]["shift"]])
    scan.matrix_world = Matrix.Translation(shift) @ Matrix.Scale(scale, 4) @ Matrix.Rotation(angle, 4, "Z")
    r, msg = call(bpy.ops.bz.import_sgm, filepath=sgm, clips_as_actions=False)
    assert r == {"FINISHED"}, msg
    creature = bpy.context.view_layer.objects.active
    src_bytes = open(sgm, "rb").read()
    n_patches = int(creature[store.KEY_NPATCH])
    old_uv = store.patch_uv(creature).copy()
    old_imgs = store.texture_images(creature)
    say(f"B creature {creature.name} from {os.path.basename(sgm)}: {n_patches} patches, textures {old_imgs}")
    if RENDER:
        render_creature(creature, "/tmp/bz_pillbug_before.png", "before (donor texture)")
    activate(creature)
    bpy.ops.ed.undo_push(message="imported")
    # the donor beetle's legs sit where the pillbug scan has none (A3b's finding: 28 % of the retargeted surface is
    # more than 2 % of the size from the scan), so the default 2 / 4 % cage covers only 51 %; a longer ray lets those
    # parts take the nearest scan colour instead of the mean fill (measured: 4/24 -> 84 %, 8/40 -> 89 %)
    t0 = time.perf_counter()
    r, msg = call(bpy.ops.bz.transfer_texture, source=scan.name, size="2048", density=7, extrusion=4.0, ray=24.0)
    secs = time.perf_counter() - t0
    assert r == {"FINISHED"}, msg
    line = str(creature.get(texops.KEY_LAST, ""))
    st = dict(creature["bz_last_transfer_stats"])
    say(f"B {line} ({secs:.1f} s wall; dilated {st['dilated']}, mean-filled {st['filled']})")
    assert st["coverage"] > 0.8, st["coverage"]
    uv = store.patch_uv(creature)
    assert np.abs(uv - texops.atlas_uvs(n_patches, 2048, 4)).max() < 1e-9
    atlas = bpy.data.images[st["image"]]
    atlas_px = pixels_of(atlas)
    per = None
    if RENDER:
        atlas.filepath_raw = "/tmp/bz_pillbug_atlas.png"; atlas.file_format = "PNG"; atlas.save()
        render_creature(creature, "/tmp/bz_pillbug_after.png", "after (transferred scan texture)")
    t0 = time.perf_counter()
    data = export(creature, PILL_OUT)
    exp_secs = time.perf_counter() - t0
    m, err = check_export_file(PILL_OUT, st["texture"], 2048, atlas_px, n_patches, uv)
    say(f"B export {len(data)} bytes in {exp_secs:.1f} s: 1 texture '{m.textures[0].name}' "
        f"{m.textures[0].palette_entries} colours, decoded vs atlas mean error {err:.4f}; "
        f"summary: {creature.get('bz_last_export', '')[:240]}")
    assert err < 0.08, err
    # Undo first (a re-import pushes its own undo step, which one Undo would step back through instead); with
    # BZ_RENDER the after-render's Bake Textured Mesh pushed a step of its own between the transfer and here
    cname = creature.name
    activate(creature)
    for _ in range(2 if RENDER else 1):
        bpy.ops.ed.undo()
    creature = bpy.data.objects[cname]
    assert np.array_equal(store.patch_uv(creature), old_uv) and store.texture_images(creature) == old_imgs
    assert export(creature, PILL_UNDO) == src_bytes, "undo export differs from the imported pillbug"
    say("B undo: byte-identical")
    r, msg = call(bpy.ops.bz.import_sgm, filepath=PILL_OUT, clips_as_actions=False)
    assert r == {"FINISHED"}, msg
    re = bpy.context.view_layer.objects.active
    imgs = store.texture_images(re)
    assert len(imgs) == 1 and bpy.data.images[imgs[0]].size[0] == 2048, imgs
    err2 = float(np.abs(pixels_of(bpy.data.images[imgs[0]])[:, :, :3] - atlas_px[:, :, :3]).mean())
    assert err2 < 0.08 and np.abs(store.patch_uv(re) - uv).max() < 1e-6, err2
    say(f"B re-import: texture {imgs[0]} (mean error vs the atlas {err2:.4f}), UVs = atlas")
    return {"coverage": st["coverage"], "low": st["low"], "secs": st["seconds"], "export_secs": exp_secs,
            "bytes": len(data), "palette_err": err, "colours": m.textures[0].palette_entries}


try:
    blender_native.register()
    a = part_a() if "A" in PARTS else None
    b = part_b() if "B" in PARTS else None
    open("/tmp/bz_texture_transfer_done.txt", "w").write(f"PASS A={a} B={b}\n")
    say("PASS", a, b)
except Exception:
    open("/tmp/bz_texture_transfer_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
