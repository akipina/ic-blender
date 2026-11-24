"""Transfer Texture (roadmap A4): give a native creature a per-patch atlas UV layout and bake a source mesh's
image onto it, so a creature fitted / retargeted from a textured scan wears the scan's skin instead of the
template's or donor's.

How it works
  1. **Atlas UVs.** Every patch gets its own square cell of an N x N grid (N = ceil(sqrt(patches))) with a gutter
     of a few texels, written as the 4 corner UVs the file stores (``store.set_patch_uv``). The engine interpolates
     the 4 corner UVs bilinearly over the patch (``core.surface_uvs`` is that rule, verified against the Object
     Editor's vertex stream), so a cell per patch maps the patch exactly, and a seam between two patches is a seam
     between two cells: no UV island has to be continuous across patches.
  2. **Bake.** The creature's exact surface is tessellated (density D, the atlas UVs per sample, the engine's
     normals) into a temporary target mesh; the source mesh gets a temporary copy with one Emission material
     showing its image on its own UVs; Cycles (CPU) bakes EMIT "selected to active" from the source onto the target's
     atlas image (no lighting; rays from a cage pushed out by ``extrusion`` of the creature's size, up to
     ``ray distance``). Texels no ray hit stay unhit: they are measured (the coverage), filled from their hit
     neighbours (a dilation of ``gutter + 2`` texels, so the gutters carry the cell's edge colour and bilinear /
     mip sampling never reads black), the rest with the mean hit colour (never magenta: IC keys (255, 0, 255) to
     transparent).
  3. **Materials.** The image becomes the creature's ONE texture: every material slot's texture is the atlas
     (positions kept, so ``patch.material_index`` still holds), the texture table is [atlas]. Export re-encodes it
     as the creature's TXMP (``ICParse.codecs.texture.encode_texture``: the creature format is a 256-colour
     paletted texture with a mip chain, as every stock creature; the type-1 ``.txr`` is the scene-object format)
     and points every material's TXTR tag at it.

Sizes (MEASURED, 2026-09-16): the 61 base-game creatures (``Assets/engine_stock``, 444 textures) top out at
**256 x 256** -- nothing bigger ships with the game. The wider stock folder (``Assets/stock``: expansion and
community creatures, 512 textures) goes to 512 x 512 (8) and 256 x 1024 (8, squid suckers); the only 1024 x 1024
textures are on three hand-made creatures (ChickenTest*, a scan's ``Image_0``). So 1024 is the default here (a scan
deserves more than 256) and 2048 is the most this tool writes: the Object Editor loaded a 2048 one (the pillbug,
rdn-oe 6.9 s), but nothing in the game ships that big, so the in-game side is the modder's to test. A power of two
is required by the halving mip chain. A sculpt in progress refuses (its refit would move the surface the bake was
made on).
"""
from __future__ import annotations

import math
import time

import numpy as np

try:
    import bpy
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
except ImportError:                                    # the venv tests import the bpy-free helpers
    bpy = None

from . import core, material, store

MAX_SIZE = 2048            # the most this tool writes (the pillbug's 2048 atlas loaded in the Object Editor)
BASE_GAME_MAX = 256        # the largest texture on the 61 base-game creatures (444 measured)
STOCK_MAX = 1024           # the largest in the wider stock folder (3 hand-made creatures; 512 x 512 otherwise)
MIN_SIZE = 64
SIZES = [("256", "256 (base-game max)", ""), ("512", "512", ""), ("1024", "1024", ""), ("2048", "2048", "")]
KEY_LAST = "bz_last_transfer"
TMP_SUFFIX = "_texbake"


class TransferError(RuntimeError):
    """A readable reason the transfer can't run (reported as is)."""


# ------------------------------------------------------------------------------------------- atlas (bpy-free)
def atlas_grid(n_patches: int) -> int:
    """Cells per side of the square atlas grid: the smallest N with N * N >= patches."""
    return max(1, int(math.ceil(math.sqrt(max(1, int(n_patches))))))


def atlas_uvs(n_patches: int, size: int, gutter_px: int) -> np.ndarray:
    """(Np, 8) corner UVs in the FILE convention (v top-down; slot order = control-point corners 0, 3, 12, 15 =
    grid (0,0), (0,1), (1,0), (1,1) as ``core.surface_uvs`` reads them): patch p in cell (p % N, p // N), inset by
    ``gutter_px`` texels on every side. Rows go down the image (v grows with the row), as the file's v does."""
    n = int(n_patches)
    N = atlas_grid(n)
    cell = float(size) / N
    g = min(float(gutter_px), cell / 4.0)              # a tiny cell keeps at least half its width
    out = np.zeros((n, 8), dtype=np.float64)
    p = np.arange(n)
    col, row = p % N, p // N
    u0 = (col * cell + g) / size
    u1 = ((col + 1) * cell - g) / size
    v0 = (row * cell + g) / size
    v1 = ((row + 1) * cell - g) / size
    out[:, 0], out[:, 1] = u0, v0                      # corner 0   (0,0)
    out[:, 2], out[:, 3] = u0, v1                      # corner 3   (0,1)
    out[:, 4], out[:, 5] = u1, v0                      # corner 12  (1,0)
    out[:, 6], out[:, 7] = u1, v1                      # corner 15  (1,1)
    return out


def cell_mask(n_patches: int, size: int, gutter_px: int) -> np.ndarray:
    """(size, size) int32 TOP-DOWN: the patch whose inset cell a texel lies in, -1 in the gutters / unused strip."""
    n = int(n_patches)
    N = atlas_grid(n)
    cell = float(size) / N
    g = min(float(gutter_px), cell / 4.0)
    out = np.full((size, size), -1, dtype=np.int32)
    for p in range(n):
        col, row = p % N, p // N
        x0, x1 = int(math.ceil(col * cell + g)), int(math.floor((col + 1) * cell - g))
        y0, y1 = int(math.ceil(row * cell + g)), int(math.floor((row + 1) * cell - g))
        out[y0:y1, x0:x1] = p
    return out


def dilate(rgb: np.ndarray, hit: np.ndarray, steps: int):
    """Fill unhit texels from the mean of their hit 4-neighbours, ``steps`` times (the bake margin, in numpy so the
    coverage was measured on the raw bake first); texels still unhit get the mean hit colour. Returns (rgb, filled
    by dilation, filled with the mean)."""
    rgb = np.array(rgb, dtype=np.float32, copy=True)
    hit = np.array(hit, dtype=bool, copy=True)
    n_dil = 0
    for _ in range(int(steps)):
        if hit.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(hit.shape, dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh = np.roll(hit, (dy, dx), axis=(0, 1))
            sc = np.roll(rgb, (dy, dx), axis=(0, 1))
            if dy == 1:
                sh[0, :] = False
            elif dy == -1:
                sh[-1, :] = False
            if dx == 1:
                sh[:, 0] = False
            elif dx == -1:
                sh[:, -1] = False
            acc += sc * sh[:, :, None]
            cnt += sh
        new = (~hit) & (cnt > 0)
        if not new.any():
            break
        rgb[new] = acc[new] / cnt[new][:, None]
        hit |= new
        n_dil += int(new.sum())
    rest = ~hit
    n_rest = int(rest.sum())
    if n_rest:
        base = rgb[hit].mean(0) if hit.any() else np.array([0.5, 0.5, 0.5], dtype=np.float32)
        rgb[rest] = base
    return rgb, n_dil, n_rest


def coverage(hit: np.ndarray, cells: np.ndarray, n_patches: int):
    """(fraction of cell texels hit, per-patch fraction (Np,)) on the raw bake."""
    inside = cells >= 0
    total = int(inside.sum())
    frac = float((hit & inside).sum()) / total if total else 0.0
    per = np.zeros(int(n_patches), dtype=np.float64)
    if total:
        counts = np.bincount(cells[inside], minlength=int(n_patches)).astype(np.float64)
        hits = np.bincount(cells[inside & hit], minlength=int(n_patches)).astype(np.float64)
        per = np.divide(hits, counts, out=np.zeros_like(hits), where=counts > 0)
    return frac, per


def report_line(image: str, creature: str, size: int, n_patches: int, secs: float, cov: float, low: int,
                cell_px: float) -> str:
    return (f"Transferred {image} onto {creature}: {size}x{size}, {n_patches} patches ({atlas_grid(n_patches)}x"
            f"{atlas_grid(n_patches)} cells of {cell_px:.0f} px), bake {secs:.1f} s; source coverage {100 * cov:.1f} %"
            + (f" ({low} patch{'es' if low != 1 else ''} under half covered)" if low else ""))


# ------------------------------------------------------------------------------------------- bpy side
def source_image(obj):
    """The first image an Image Texture node of the object's materials shows, or None."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None and node.image.size[0] > 0:
                return node.image
    return None


def recorded_source(obj):
    """The mesh the creature was fitted (``bz_fit_source``) or retargeted (``bz_retarget_source``) from, when the
    name is recorded and the object still exists."""
    for key in ("bz_retarget_source", "bz_fit_source"):
        name = obj.get(key)
        if name and bpy.data.objects.get(str(name)) is not None:
            return str(name)
    return ""


def _mesh_from_object(context, obj, name):
    """A plain copy of the evaluated mesh (modifiers applied, UV layers kept)."""
    deps = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=deps)
    me.name = name
    return me


def _emission_material(name, image):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Linear"
    tex.extension = "REPEAT"
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    nt.nodes.active = tex
    return mat


def _target_material(name, image):
    """The bake target's material: its active Image Texture node names the image Cycles bakes into."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    nt.nodes.active = tex
    return mat


def bake_target_mesh(context, obj, patch_uv, density: int, image):
    """The creature's exact surface at ``density`` as a mesh object with the atlas UVs per sample and the engine's
    normals; one material whose active image node is ``image``."""
    from . import ops as bzops
    pool = bzops._mesh_verts(obj)
    patches = store.patches(obj)
    V, T, _N = core.tessellate(pool, patches, n=density)
    if len(V) == 0:
        raise TransferError("the creature has no surface to bake onto")
    me = bpy.data.meshes.new(obj.name + TMP_SUFFIX)
    me.from_pydata(V.tolist(), [], T.tolist())
    for p in me.polygons:
        p.use_smooth = True
    me.update()
    uvarr = core.surface_uvs(patch_uv, density)
    loop_v = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_v)
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", uvarr[loop_v].astype("f4").ravel())
    try:
        bzops.BZ_OT_bake._apply_engine_normals(obj, me, patches, density, pool)
    except Exception:
        pass                                            # the tessellation's own normals then
    me.materials.append(_target_material(obj.name + TMP_SUFFIX, image))
    me.update()
    tgt = bpy.data.objects.new(obj.name + TMP_SUFFIX, me)
    tgt.matrix_world = obj.matrix_world.copy()
    context.collection.objects.link(tgt)
    return tgt


def creature_size(obj) -> float:
    """The bounding diagonal of the creature's surface (world units)."""
    from . import ops as bzops
    V, _T, _N = core.tessellate(bzops._mesh_verts(obj), store.patches(obj), n=3)
    if len(V) == 0:
        return 0.0
    return float(np.linalg.norm(V.max(0) - V.min(0)))


def align_matrix(context, src, obj):
    """The world matrix that puts ``src``'s evaluated shape centred on the creature's surface bounding box and scaled
    by the ratio of the bounding diagonals (Retarget's Scale to Target rule, ``retargetops.align_target``)."""
    from mathutils import Matrix, Vector
    from . import ops as bzops, retargetops
    deps = context.evaluated_depsgraph_get()
    me = src.evaluated_get(deps).data
    n = len(me.vertices)
    P = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", P)
    P = P.reshape(n, 3)
    M = np.array(src.matrix_world, dtype=np.float64)
    Pw = P @ M[:3, :3].T + M[:3, 3]
    Mc = np.array(obj.matrix_world, dtype=np.float64)
    Pc = (Pw - Mc[:3, 3]) @ np.linalg.inv(Mc[:3, :3]).T           # into the creature's local frame
    _al, scale, shift = retargetops.align_target(Pc, bzops._mesh_verts(obj), store.patches(obj))
    A = Matrix.Translation(Vector(shift.tolist())) @ Matrix.Scale(float(scale), 4)
    return obj.matrix_world @ A @ obj.matrix_world.inverted() @ src.matrix_world, float(scale)


class _BakeSettings:
    """Save / restore what the bake changes on the scene."""

    def __init__(self, scene):
        self.scene = scene
        r, b = scene.render, scene.render.bake
        self.saved = {"engine": r.engine, "samples": getattr(scene.cycles, "samples", None),
                      "device": getattr(scene.cycles, "device", None), "s2a": b.use_selected_to_active,
                      "cage": b.cage_extrusion, "ray": b.max_ray_distance, "margin": b.margin, "clear": b.use_clear}

    def apply(self, extrusion, ray):
        s, r, b = self.scene, self.scene.render, self.scene.render.bake
        r.engine = "CYCLES"
        try:
            s.cycles.device = "CPU"
            s.cycles.samples = 1
        except AttributeError:
            pass
        b.use_selected_to_active = True
        b.cage_extrusion = float(extrusion)
        b.max_ray_distance = float(ray)
        b.margin = 0
        b.use_clear = False                             # the image is pre-filled with alpha 0: unhit texels keep it

    def restore(self):
        s, r, b = self.scene, self.scene.render, self.scene.render.bake
        sv = self.saved
        try:
            r.engine = sv["engine"]
        except TypeError:
            pass
        if sv["samples"] is not None:
            s.cycles.samples = sv["samples"]
        if sv["device"] is not None:
            s.cycles.device = sv["device"]
        b.use_selected_to_active, b.cage_extrusion = sv["s2a"], sv["cage"]
        b.max_ray_distance, b.margin, b.use_clear = sv["ray"], sv["margin"], sv["clear"]


def cycles_bake(context, src_tmp, tgt, extrusion, ray) -> float:
    """Cycles EMIT bake, selected (the source copy) to active (the target). Returns the seconds it took."""
    settings = _BakeSettings(context.scene)
    settings.apply(extrusion, ray)
    # (Cycles' own clear paints every texel OUTSIDE a UV face opaque black, which would read as a hit: the atlas is
    # pre-filled with alpha 0 instead, a missed ray writes alpha 0, so alpha > 0.5 == a texel that found the source)
    for o in context.view_layer.objects:
        o.select_set(False)
    src_tmp.select_set(True)
    tgt.select_set(True)
    context.view_layer.objects.active = tgt
    t = time.perf_counter()
    try:
        over = {"active_object": tgt, "selected_objects": [src_tmp, tgt], "selected_editable_objects": [src_tmp, tgt],
                "object": tgt}
        with context.temp_override(**over):
            res = bpy.ops.object.bake(type="EMIT", use_selected_to_active=True, cage_extrusion=float(extrusion),
                                      max_ray_distance=float(ray), margin=0, use_clear=False)
        if "FINISHED" not in res:
            raise TransferError(f"the Cycles bake did not finish ({res})")
    finally:
        settings.restore()
    return time.perf_counter() - t


def image_pixels(img):
    """(h, w, 4) float32 bottom-up."""
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)


def set_image_pixels(img, arr):
    img.pixels.foreach_set(np.ascontiguousarray(arr, dtype=np.float32).ravel())
    img.update()


def _remove(obj):
    me = obj.data if obj is not None else None
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        for m in list(me.materials):
            if m is not None and m.users <= 1:
                bpy.data.materials.remove(m)
        bpy.data.meshes.remove(me)


def transfer(context, obj, src, image, size: int, density: int, gutter_px: int, extrusion_pct: float,
             ray_pct: float, scale_to_creature: bool, tex_name: str):
    """Run the transfer on creature ``obj`` from mesh ``src`` showing ``image``. Returns a dict of numbers; the
    creature's tables are written (journaled) only after the bake succeeded."""
    n_patches = int(obj.get(store.KEY_NPATCH, 0))
    if n_patches <= 0:
        raise TransferError(f"{obj.name} has no patches")
    diag = creature_size(obj)
    if diag <= 0:
        raise TransferError(f"{obj.name} has no surface extent")
    puv = atlas_uvs(n_patches, size, gutter_px)
    atlas = bpy.data.images.new(f"{obj.name}_skin", size, size, alpha=True)
    atlas.generated_color = (0.0, 0.0, 0.0, 0.0)
    set_image_pixels(atlas, np.zeros((size, size, 4), dtype=np.float32))
    tgt = src_tmp = None
    try:
        tgt = bake_target_mesh(context, obj, puv, density, atlas)
        me = _mesh_from_object(context, src, src.name + TMP_SUFFIX)
        if not me.uv_layers:
            bpy.data.meshes.remove(me)
            raise TransferError(f"{src.name} has no UV layer: the bake needs the source's own UVs to look its image up")
        me.materials.clear()
        me.materials.append(_emission_material(src.name + TMP_SUFFIX, image))
        src_tmp = bpy.data.objects.new(src.name + TMP_SUFFIX, me)
        context.collection.objects.link(src_tmp)
        scale = 1.0
        if scale_to_creature:
            src_tmp.matrix_world, scale = align_matrix(context, src, obj)
        else:
            src_tmp.matrix_world = src.matrix_world.copy()
        secs = cycles_bake(context, src_tmp, tgt, extrusion=diag * extrusion_pct / 100.0, ray=diag * ray_pct / 100.0)
        px = image_pixels(atlas)                               # bottom-up
        hit = px[:, :, 3] > 0.5
        cells = cell_mask(n_patches, size, gutter_px)[::-1]    # top-down -> bottom-up like the pixels
        cov, per = coverage(hit, cells, n_patches)
        if not hit.any():
            raise TransferError(f"no ray from {obj.name}'s surface hit {src.name} within {ray_pct:.1f} % of the "
                                "creature's size: the source is elsewhere or at another scale (Scale to Creature "
                                "aligns it by bounding box; a source in other units needs it)")
        rgb, n_dil, n_rest = dilate(px[:, :, :3], hit, steps=int(gutter_px) + 2)
        out = np.empty_like(px)
        out[:, :, :3] = rgb
        out[:, :, 3] = 1.0
        # IC keys pure magenta to transparent: a baked texel must never be exactly (255, 0, 255)
        key = material.magenta_key_mask((np.clip(out, 0, 1) * 255.0).round().astype(np.uint8).reshape(-1, 4))
        if key.any():
            flat = out.reshape(-1, 4)
            flat[key, 1] = 3.0 / 255.0
        set_image_pixels(atlas, out)
    except Exception:
        _remove(tgt); _remove(src_tmp)
        bpy.data.images.remove(atlas)
        raise
    _remove(tgt); _remove(src_tmp)
    atlas.pack()
    atlas.use_fake_user = True
    # the creature's tables: atlas UVs (journaled), the image as its one texture, every slot showing it
    store.begin_edit(obj)
    store.set_patch_uv(obj, puv)
    store.set_texture_table(obj, [atlas.name], [tex_name])
    store.stamp_images(obj, [atlas])
    low = int((per < 0.5).sum())
    return {"image": atlas, "coverage": cov, "per_patch": per, "low": low, "seconds": secs, "dilated": n_dil,
            "filled": n_rest, "scale": scale, "size": size, "patches": n_patches,
            "cell_px": float(size) / atlas_grid(n_patches), "uv": puv}


if bpy is not None:
    class BZ_OT_transfer_texture(bpy.types.Operator):
        """Give the creature a per-patch atlas UV layout and bake a source mesh's image onto it (Cycles, no
        lighting): a creature fitted or retargeted from a textured scan gets the scan's skin as its one texture"""
        bl_idname = "bz.transfer_texture"
        bl_label = "Transfer Texture"
        bl_options = {"REGISTER", "UNDO"}

        source: StringProperty(name="Source Mesh", description="The textured mesh (with UVs and an image) to bake "
                                                               "from; empty = the mesh the creature was fitted or "
                                                               "retargeted from, when recorded")
        image: StringProperty(name="Image", description="The image to bake; empty = the first image texture of the "
                                                        "source's materials")
        size: EnumProperty(name="Size", items=SIZES, default="1024",
                           description=f"Texels per side of the atlas (a power of two). Measured: no base-game "
                                       f"creature's texture is bigger than {BASE_GAME_MAX} x {BASE_GAME_MAX}, the "
                                       f"wider stock folder reaches {STOCK_MAX}; {MAX_SIZE} is the most this tool "
                                       f"writes and only the Object Editor has been seen to load one")
        density: IntProperty(name="Bake Density", default=7, min=3, max=17,
                             description="Surface samples per patch side the bake target is tessellated at")
        gutter: IntProperty(name="Gutter (texels)", default=4, min=1, max=32,
                            description="Empty texels around every patch cell, filled from the cell's edge after the "
                                        "bake so bilinear / mip sampling never crosses into a neighbour")
        extrusion: FloatProperty(name="Cage Extrusion (% of size)", default=2.0, min=0.0, max=20.0,
                                 description="How far out of the creature's surface the bake rays start")
        ray: FloatProperty(name="Ray Distance (% of size)", default=4.0, min=0.1, max=50.0,
                           description="How far a ray looks for the source; texels whose ray finds nothing are "
                                       "filled from their neighbours and counted against the coverage")
        scale_to_creature: BoolProperty(name="Scale to Creature", default=False,
                                        description="Centre the source on the creature's surface bounding box and "
                                                    "scale it by the ratio of the bounding diagonals first (Retarget's "
                                                    "Scale to Target rule; for a source in other units)")

        @classmethod
        def poll(cls, context):
            from . import ops as bzops
            return bzops._guard_poll(cls, context)

        def execute(self, context):
            from . import draw, ops as bzops, sculptops
            obj = bzops._active_native(context)
            if obj is None:
                self.report({"ERROR"}, "Select a native patch creature")
                return {"CANCELLED"}
            if obj.mode == "EDIT":
                obj.update_from_editmode()
            err = store.sync(obj) or store.pool_error(obj)
            if err:
                self.report({"ERROR"}, err)
                return {"CANCELLED"}
            sculpts = sculptops.sculpts_of(obj)
            if sculpts:
                self.report({"ERROR"}, f"{obj.name} has a sculpt in progress ({sculpts[0].name}): Refit Patches or "
                                       "discard it first, the bake must be made on the final surface")
                return {"CANCELLED"}
            name = self.source or recorded_source(obj)
            if not name:
                self.report({"ERROR"}, "Choose a source mesh (the textured mesh to bake from); the creature records "
                                       "none it was fitted or retargeted from")
                return {"CANCELLED"}
            src = bpy.data.objects.get(name)
            if src is None:
                self.report({"ERROR"}, f"No object called '{name}' in the file")
                return {"CANCELLED"}
            if src is obj:
                self.report({"ERROR"}, f"{obj.name} is the creature itself: choose the textured source mesh")
                return {"CANCELLED"}
            if src.type != "MESH":
                self.report({"ERROR"}, f"{src.name} is a {src.type.lower()} object, not a mesh")
                return {"CANCELLED"}
            if store.is_native(src):
                self.report({"ERROR"}, f"{src.name} is a patch creature: Bake Textured Mesh it and transfer from the bake")
                return {"CANCELLED"}
            if not src.data.uv_layers:
                self.report({"ERROR"}, f"{src.name} has no UV layer: nothing maps its image onto its surface")
                return {"CANCELLED"}
            image = bpy.data.images.get(self.image) if self.image else source_image(src)
            if image is None:
                self.report({"ERROR"}, (f"no image called '{self.image}' in the file" if self.image else
                                        f"{src.name}'s materials show no image texture: name the Image to bake"))
                return {"CANCELLED"}
            if image.size[0] == 0 or image.size[1] == 0:
                self.report({"ERROR"}, f"image '{image.name}' has no pixels (its file is missing)")
                return {"CANCELLED"}
            size = int(self.size)
            if size & (size - 1) or not MIN_SIZE <= size <= MAX_SIZE:
                self.report({"ERROR"}, f"size {size} is not a power of two between {MIN_SIZE} and {MAX_SIZE} (the mip "
                                       f"chain halves, so a power of two is required; for scale, no base-game "
                                       f"creature's texture is bigger than {BASE_GAME_MAX} x {BASE_GAME_MAX})")
                return {"CANCELLED"}
            if obj.get(store.KEY_TEX_IMGS) is None:
                self.report({"ERROR"}, f"{obj.name} has no material table (import set none up): the texture has no "
                                       "slots to go into")
                return {"CANCELLED"}
            cage = bpy.data.objects.get(str(obj.get("bz_uv_cage", ""))) if obj.get("bz_uv_cage") else None
            tex_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in obj.name.lower()) + "_skin.bmp"
            mode = obj.mode
            if mode == "EDIT":
                bpy.ops.object.mode_set(mode="OBJECT")
            try:
                r = transfer(context, obj, src, image, size, self.density, self.gutter, self.extrusion, self.ray,
                             self.scale_to_creature, tex_name)
            except TransferError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            except Exception as e:
                self.report({"ERROR"}, f"Transfer failed: {type(e).__name__}: {e}")
                return {"CANCELLED"}
            finally:
                if mode == "EDIT":
                    context.view_layer.objects.active = obj
                    bpy.ops.object.mode_set(mode="EDIT")
            if cage is not None:                       # a UV cage holds the OLD UVs and Export would prefer it
                bpy.data.objects.remove(cage, do_unlink=True)
                del obj["bz_uv_cage"]
                self.report({"WARNING"}, "the old UV cage was removed (it held the previous UVs); Edit UVs rebuilds one")
            draw.invalidate(obj.name)
            line = report_line(image.name, obj.name, size, r["patches"], r["seconds"], r["coverage"], r["low"],
                               r["cell_px"])
            obj[KEY_LAST] = line
            obj["bz_last_transfer_stats"] = {"coverage": float(r["coverage"]), "low": int(r["low"]),
                                             "seconds": float(r["seconds"]), "dilated": int(r["dilated"]),
                                             "filled": int(r["filled"]), "scale": float(r["scale"]), "size": size,
                                             "image": r["image"].name, "texture": tex_name}
            if r["coverage"] < 0.5:
                self.report({"WARNING"}, f"only {100 * r['coverage']:.0f} % of the creature's texels found the source "
                                         f"within {self.ray:.1f} % of its size: check the source is aligned with the "
                                         "creature (Scale to Creature for other units) or raise the ray distance")
            elif r["coverage"] < 0.9:
                self.report({"WARNING"}, f"{100 * (1 - r['coverage']):.0f} % of the creature's texels found no source "
                                         f"within {self.ray:.1f} % of its size and were filled from their neighbours "
                                         f"({r['low']} patch{'es' if r['low'] != 1 else ''} under half covered): those "
                                         "parts of the creature are not on the source (a limb the source has not), so "
                                         "raise Ray Distance to let them take the nearest source colour")
            if r["scale"] != 1.0:
                self.report({"INFO"}, f"{src.name} scaled x{r['scale']:.4f} into the creature's frame for the bake")
            self.report({"INFO"}, line + f"; the creature's texture is now '{r['image'].name}' ({tex_name}); Export "
                                         "writes it")
            return {"FINISHED"}

    CLASSES = (BZ_OT_transfer_texture,)
else:
    CLASSES = ()
