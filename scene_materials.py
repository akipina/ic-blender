"""Scene objects' materials and textures: what the import records on each Blender material, and the export's materials step.

The game side (decompiled, see ICParse.sgm.scene.shader): a scene's shaders (``SHDR``) name their textures, textures come from
``TXRF`` paths (an external ``.txr``, loaded into ONE inventory shared by every model) or are embedded in the model (its own),
and both must be loaded before the shader that names them. A texture is filed under the NAME inside it, and names are shared,
so an edited external texture is written as a NEW ``.txr`` under a new lower-case name; an edited embedded one is rebuilt in
place under its own name. Team colour is a table of full recoloured copies, one per player colour.

What the import keeps on each Blender material (``remember``): the shader as JSON (``bz_shader``, keyed by its place among the
file's shaders: stock names are not unique), the source file, the image it showed, its opacity and whether it has team colour.
The export (``export_materials``) runs before the geometry: an untouched material keeps its shader's bytes; a painted or
replaced image, a new material, an opacity or team-colour change rebuilds that one shader, and writes the new texture files
into ``files`` (lower-case paths inside Data). ``slot_names`` maps every material slot to the shader its faces will use.
Textures the model loads that the game lacks (an earlier export's own) are copied from the source's Data folder into
``carried``; materials with no record (an older .blend) take their slot's shader (``adopt_legacy``).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import numpy as np

KEY_SHADER = "bz_shader"             # material: the source shader as ShaderSpec JSON (with its index)
KEY_SHADER_SRC = "bz_shader_src"     # material: the .sgm that shader is in
KEY_PLACEHOLDER = "bz_shader_placeholder"   # material: a face shader name the source has no SHDR for (kept as it is)
KEY_IMAGE = "bz_ic_image"            # material: the name of its diffuse image (Replace Image changes it)
KEY_IMAGE_SRC = "bz_ic_image_src"    # material: the image it showed at import
KEY_OPACITY = "bz_ic_opacity"        # material: opacity 0..1 (the opacity channel's alpha)
KEY_TEAM_ON = "bz_team_on"           # material: team colour on
KEY_TEAM_MASK = "bz_team_mask"       # material: the name of its team-area mask image (white = team colour)
KEY_PREVIEW = "bz_team_preview_image"  # material: the name of the player-colour preview image shown instead of the diffuse
KEY_TEX_REF = "bz_tex_ref"           # image: the texture name the file gave it
KEY_KEYED_ALPHA = "bz_keyed_alpha"   # image: the alpha its magenta (colour-keyed) texels had in the file
KEY_SCENE_HASH = "bz_scene_hash"     # image: its pixels at import (set by scene._remember_looks)
KEY_BSDF = "bz_ic_bsdf"              # material: its Principled BSDF settings at import (the game has no counterpart for them)

DEFAULT_TEAM_COUNT = 8


# ------------------------------------------------------------------------------------------------------- pure helpers
def stem(text: str) -> str:
    """A lower-case archive-safe name: Blender's ``.001`` suffix dropped, anything but a-z 0-9 _ turned into _."""
    text = re.sub(r"\.\d{3}$", "", str(text))
    out = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    return out or "material"


def shader_name(text: str) -> str:
    """A new shader's name from a Blender material name (its ``.001`` suffix dropped; at most 63 characters)."""
    return re.sub(r"\.\d{3}$", "", str(text)).strip()[:63] or "material"


def image_rgba(img):
    """(w, h, top-down RGBA bytes) of a Blender image; magenta texels Blender shows cut out get back the alpha the file had."""
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    a = np.clip(np.rint(px * 255.0), 0, 255).astype(np.uint8).reshape(h, w, 4)[::-1].copy()
    keyed = img.get(KEY_KEYED_ALPHA)
    if keyed is not None:
        m = (a[..., 0] >= 253) & (a[..., 1] <= 2) & (a[..., 2] >= 253) & (a[..., 3] == 0)
        a[m, 3] = int(keyed)
    return w, h, a.tobytes()


def mask_of(img, size):
    """(N,) bool from a mask image (white / bright = team area), N = w*h of ``size``; ValueError when the sizes differ."""
    w, h = img.size
    if (w, h) != tuple(size):
        raise ValueError(f"the team mask '{img.name}' is {w} x {h} but its texture is {size[0]} x {size[1]}; make them the same size")
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    a = px.reshape(h, w, 4)[::-1]
    return (a[..., :3].max(axis=2) > 0.5).reshape(-1)


def set_image_rgba(img, w, h, rgba) -> None:
    arr = np.frombuffer(bytes(rgba), dtype=np.uint8).astype(np.float32).reshape(h, w, 4) / 255.0
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1]).reshape(-1))


def image_changed(img) -> bool:
    """True when the image's pixels are not the ones the import stamped (a new image has no stamp: changed)."""
    from . import material
    stamp = img.get(KEY_SCENE_HASH)
    return stamp is None or str(material.pixel_crc(img)) != str(stamp)


def _base_node(mat):
    tree = mat.node_tree if mat.use_nodes else None
    if tree is None:
        return None
    bsdf = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is not None and bsdf.inputs["Base Color"].is_linked:
        node = bsdf.inputs["Base Color"].links[0].from_node
        if node.bl_idname == "ShaderNodeTexImage":
            return node
    return next((n for n in tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.image is not None
                 and n.image.name != mat.get(KEY_TEAM_MASK)), None)


def diffuse_image(mat):
    """The material's diffuse image: the image node feeding Base Color (or the first image node), unless that shows the
    player-colour preview, in which case the image recorded as the diffuse."""
    import bpy
    node = _base_node(mat)
    img = node.image if node is not None else None
    if img is not None and img.name == mat.get(KEY_PREVIEW):
        img = bpy.data.images.get(mat.get(KEY_IMAGE, ""))
    return img


def base_colour(mat) -> tuple:
    tree = mat.node_tree if mat.use_nodes else None
    bsdf = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None) if tree else None
    c = bsdf.inputs["Base Color"].default_value if bsdf is not None else mat.diffuse_color
    return tuple(float(x) for x in c[:3])


def _argb(rgb, alpha=255) -> int:
    r, g, b = (int(round(max(0.0, min(1.0, x)) * 255)) for x in rgb)
    return (int(alpha) << 24) | (r << 16) | (g << 8) | b


def opacity_channel(spec):
    """The channel whose colour alpha is the shader's opacity: 4 from VERS 5 on, else 0 (LoadShader copies it)."""
    return spec.channel(4 if spec.version >= 5 else 0)


def set_opacity(spec, oc, alpha: int, label: str = "", notes=None) -> None:
    """Set the shader's opacity to ``alpha`` (0..255) so that the game draws it (ShaderImp::IsOpaquePass / IsInvisible,
    ShaderImp.cpp): the opacity channel's colour alpha counts only in modes 0, 2 and 3 -- a texture-only channel
    (mode 1) becomes mode 3, texture x colour, which keeps the texture's own cut-outs -- and only when the blend pair is
    not (1, 0), which always draws opaque: that one becomes (6, 7), the alpha blend of almost every stock shader."""
    from ICParse.sgm.scene import shader as SH
    notes = notes if notes is not None else []
    oc.colour = (oc.colour & 0x00FFFFFF) | (int(alpha) << 24)
    if alpha == 255:
        return
    if oc.mode == 1:
        oc.mode = 3 if oc.ref else 0
        if oc.channel == 0:                              # VERS <= 4: channel 0 is also the colour; white keeps the texture's
            oc.colour = (int(alpha) << 24) | 0xFFFFFF
    blend = SH.blend_of(spec.data_raw)
    if blend == (1, 0):
        spec.data_raw = SH.with_blend(spec.data_raw, 6, 7)
        notes.append(f"material '{label}': blending switched from opaque (1/0) to alpha (6/7) for its opacity")
    elif blend is not None and blend != (6, 7):
        notes.append(f"material '{label}': the game blends it {blend[0]}/{blend[1]} (added, as effects are), not by alpha; "
                     f"its opacity may show differently")


def _has_alpha(img) -> bool:
    """True when some texel of the image is not fully opaque."""
    _w, _h, rgba = image_rgba(img)
    return bool((np.frombuffer(rgba, dtype=np.uint8)[3::4] < 255).any())


def _loose_file(root: str, rel: str):
    """The bytes of ``rel`` (a lower-case path inside Data) under the folder ``root``, matching each part ignoring case;
    None when there is no such file."""
    d = root
    parts = rel.split("/")
    for i, part in enumerate(parts):
        try:
            names = os.listdir(d)
        except OSError:
            return None
        hit = part if part in names else next((n for n in names if n.lower() == part), None)
        if hit is None:
            return None
        d = os.path.join(d, hit)
    if not os.path.isfile(d):
        return None
    with open(d, "rb") as f:
        return f.read()


# ------------------------------------------------------------------------------------------------------- import
def remember(mats, data, raw: bytes, images=None) -> None:
    """Record on each imported material (one per ``data.materials`` entry, in order) its source shader and image, and on
    each image (``images``: texture key -> image) the alpha its magenta texels had."""
    from ICParse.sgm.scene import shader as SH
    for key, img in (images or {}).items():
        tex = data.textures.get(key)
        ka = keyed_alpha(tex[2]) if tex else None
        if ka is not None:
            img[KEY_KEYED_ALPHA] = ka
    specs = SH.read_shaders(raw)
    by_name = {}
    for s in specs:
        by_name.setdefault(s.name, s)
    src = data.source_path
    for i, (mat, mt) in enumerate(zip(mats, data.materials)):
        _remember_one(mat, specs[i] if i < len(specs) and specs[i].name == mt["name"] else by_name.get(mt["name"]),
                      mt["name"], src)


def _remember_one(mat, spec, face_name, src) -> None:
    """Record on ``mat`` its source shader ``spec`` (None: ``face_name`` has no SHDR, kept as a placeholder)."""
    mat[KEY_SHADER_SRC] = src
    if spec is None:
        mat[KEY_PLACEHOLDER] = face_name
        return
    mat[KEY_SHADER] = json.dumps(spec.to_json())
    img = diffuse_image(mat)
    if img is not None:
        mat[KEY_IMAGE] = mat[KEY_IMAGE_SRC] = img.name
        ch0 = spec.channel(0)
        if ch0 is not None and ch0.ref and not img.get(KEY_TEX_REF):
            img[KEY_TEX_REF] = ch0.ref
    oc = opacity_channel(spec)
    if oc is not None:
        mat[KEY_OPACITY] = round((oc.colour >> 24) / 255.0, 4)
    mat[KEY_TEAM_ON] = spec.team is not None
    mat[KEY_BSDF] = _bsdf_look(mat)


def adopt_legacy(objs, data, specs) -> list:
    """Materials with no shader record (imported by an add-on from before these records, or one whose record failed:
    ``bz_shader_error``) get the record of the shader of the slot they sit in -- slot i is ``data.materials[i]``, the
    mapping the export used before -- when their name is that shader's name (Blender's ``.001`` suffix aside). Without
    this they would count as new materials and get duplicate shaders. Returns the names adopted."""
    src = data.source_path
    by_name = {}
    for s in specs:
        by_name.setdefault(s.name.lower(), s)
    out = []
    for obj in objs:
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            if mat is None or mat.name in out or mat.get(KEY_SHADER) or mat.get(KEY_PLACEHOLDER):
                continue
            if mat.get(KEY_SHADER_SRC) not in (None, src) or i >= len(data.materials):
                continue
            want = data.materials[i]["name"]
            if shader_name(mat.name).lower() != shader_name(want).lower():
                continue
            spec = specs[i] if i < len(specs) and specs[i].name == want else by_name.get(want.lower())
            _remember_one(mat, spec, want, src)
            out.append(mat.name)
    return out


def keyed_alpha(rgba) -> object:
    """The one alpha every magenta texel of ``rgba`` has (None when there are none or they differ)."""
    a = np.frombuffer(bytes(rgba), dtype=np.uint8).reshape(-1, 4)
    m = (a[:, 0] >= 253) & (a[:, 1] <= 2) & (a[:, 2] >= 253)
    vals = np.unique(a[m, 3])
    return int(vals[0]) if len(vals) == 1 else None


# ------------------------------------------------------------------------------------------------------- export
@dataclass
class MaterialResult:
    sgm: bytes
    files: dict = field(default_factory=dict)        # lower-case path inside Data -> bytes (art/textures/x.txr)
    slot_names: dict = field(default_factory=dict)   # object name -> [shader name per material slot]
    notes: list = field(default_factory=list)
    new_refs: list = field(default_factory=list)     # texture names this export made up
    new_shaders: list = field(default_factory=list)  # shader names this export added
    changed: list = field(default_factory=list)      # shader names rebuilt in place
    carried: dict = field(default_factory=dict)      # path inside Data -> bytes: textures the model loads that the game
    #                                                  lacks, copied from the source's own Data folder (an earlier export's)

    @property
    def touched(self) -> bool:
        return bool(self.files or self.new_shaders or self.changed)


def game_archives(data_root=None):
    """An ICParse resolver over the game's .sga archives only (loose files left out: a texture this add-on wrote earlier
    must not push a re-export to another name)."""
    import glob
    from ICParse.data import ICData, default_root
    root = data_root
    if not root:                                      # the Game folder setting counts, not only the default Steam folder
        try:
            from .sceneobj import prefs
            root = prefs.resolve_game_dir(None)[0]
        except Exception:                             # noqa: BLE001 -- no preferences (outside Blender): the usual folders
            root = None
    root = root or default_root()
    sgas = sorted(glob.glob(os.path.join(root, "*.sga"))) if root and os.path.isdir(root) else []
    return ICData(*sgas) if sgas else None


def _decode(ref, embedded_forms, resolver):
    from ICParse.codecs.texture import decode_txtr
    from ICParse.iff.document import IffDocument
    t = embedded_forms.get(ref.lower())
    if t is None and resolver is not None:
        raw = resolver.read(ref)
        t = next(iter(IffDocument.from_bytes(raw).find_all("TXTR")), None) if raw else None
    return decode_txtr(t) if t is not None else None


def stock_team_mask(spec, raw: bytes, resolver=None):
    """(w, h, (N,) bool) the team area of a stock team shader: where its player variants differ from entry 0; None if the
    textures cannot be read."""
    from ICParse.codecs import teamcolour as TC
    from ICParse.iff.document import IffDocument
    if spec.team is None or not spec.team.entries:
        return None
    emb = {}
    for t in IffDocument.from_bytes(raw).find_all("TXTR"):
        nm = next((c for c in t.children if c.tag == "NAME"), None)
        if nm is not None:
            emb[nm.raw.split(b"\x00", 1)[0].decode("latin1").lower()] = t
    if resolver is None:
        resolver = game_archives()
    refs = [r for _c, r in spec.team.entries]
    base = _decode(refs[0], emb, resolver) if refs[0] else None
    if base is None:
        return None
    variants = []
    for r in refs[1:6]:
        v = _decode(r, emb, resolver) if r else None
        if v is not None and v[:2] == base[:2]:
            variants.append(v[2])
    if not variants:
        return None
    return base[0], base[1], TC.derive_mask(base[2], *variants)


def export_materials(objs, data, raw: bytes, entity: str = "", resolver=None, loose_root=None) -> MaterialResult:
    """The materials step (stage 10 materials / 12 textures). ``objs``: the part objects; ``data``: ``core.load_scene`` of the
    source; ``raw``: the source bytes; ``entity``: the name new texture files start with (lower case); ``loose_root``: the
    Data folder the source sits in -- a texture the model loads that is in neither ``resolver`` nor the new files (an
    earlier export's own) is copied from there into ``carried``."""
    from ICParse.codecs import teamcolour as TC
    from ICParse.codecs.texture import check_texture_size, encode_txr, txtr_form
    from ICParse.sgm.scene import shader as SH
    from ICParse.sgm.scene.validate_materials import archive_path

    src = data.source_path
    entity = stem(entity or data.name)
    specs = SH.read_shaders(raw)
    adopted = adopt_legacy(objs, data, specs)
    sources = SH.texture_sources(raw)
    embedded_names = {s.key: s.name for s in sources if s.kind == "txtr"}
    if resolver is None:
        resolver = game_archives()
    res = MaterialResult(sgm=raw)
    taken_shaders = [s.name for s in specs]
    taken_refs = [s.name for s in sources]
    claimed, new_specs, new_txrf, embedded = {}, [], [], {}
    image_refs = {}                                      # image name -> the texture name it is written under
    donor = next((s for s in specs if not s.opaque and s.version >= SH.NEW_SHADER_VERSION and s.channel(0) is not None),
                 None)
    plans = {}                                           # material name -> shader name its faces use
    if adopted:
        res.notes.append(f"material(s) {', '.join(adopted)} had no shader record (an older import): each keeps the shader "
                         f"of its slot")

    def new_ref(wanted):
        ref = SH.unique_name(f"data:art/textures/{wanted}", [r[:-4] if r.endswith(".txr") else r for r in taken_refs]) + ".txr"
        while resolver is not None and resolver.read(ref) is not None:
            ref = SH.unique_name(ref[:-4] + "_", [ref[:-4] + "_"]) + ".txr"
        taken_refs.append(ref)
        res.new_refs.append(ref)
        return ref

    def write_txr(ref, w, h, rgba):
        why = check_texture_size(w, h)
        if why:
            raise ValueError(f"{why}. Scale it (Image > Resize) or use Replace Image in the IC Material panel")
        path = ref.split(":", 1)[1].lstrip("/")
        res.files[path] = encode_txr(w, h, rgba, name=ref)
        new_txrf.append(ref)

    source_keys = {s.key for s in sources}

    def texture_ref(img, spec_name):
        """The texture name ``img`` is drawn from in the new file, writing its texture when it must be."""
        if img.name in image_refs:
            return image_refs[img.name]
        old = img.get(KEY_TEX_REF, "")
        if old and not image_changed(img):
            if old.lower() in source_keys:               # loaded by this file already
                image_refs[img.name] = old
                return old
            if available(old):
                new_txrf.append(old)                     # a texture the game has (another model's): load it here too
                image_refs[img.name] = old
                return old
        w, h, rgba = image_rgba(img)
        if old and old.lower() in embedded_names:        # this model's own embedded texture: rebuilt under its own name
            why = check_texture_size(w, h)
            if why:
                raise ValueError(f"texture '{img.name}': {why}")
            name = embedded_names[old.lower()]
            embedded[name] = txtr_form(name, w, h, rgba, None)
            res.notes.append(f"texture '{img.name}' was rebuilt inside the model under its own name")
            image_refs[img.name] = old
            return old
        ref = new_ref(f"{entity}_{stem(spec_name)}")
        try:
            write_txr(ref, w, h, rgba)
        except ValueError as e:
            raise ValueError(f"texture '{img.name}': {e}") from None
        res.notes.append(f"texture '{img.name}' is written as {ref.split(':', 1)[1]}")
        image_refs[img.name] = ref
        return ref

    loose_cache = {}

    def loose(ref):
        path = archive_path(ref)
        if path not in loose_cache:
            loose_cache[path] = _loose_file(loose_root, path) if loose_root else None
        return loose_cache[path]

    def available(ref):
        """The game has the texture, or the source's own Data folder does."""
        return (resolver is not None and resolver.read(ref) is not None) or loose(ref) is not None

    seen = []
    for obj in objs:
        for slot in obj.material_slots:
            if slot.material is not None and slot.material not in seen:
                seen.append(slot.material)
    for mat in seen:
        info = None
        if mat.get(KEY_SHADER) and mat.get(KEY_SHADER_SRC) == src:
            info = json.loads(mat[KEY_SHADER])
        elif mat.get(KEY_PLACEHOLDER) and mat.get(KEY_SHADER_SRC) == src:
            plans[mat.name] = str(mat[KEY_PLACEHOLDER])
            continue
        elif mat.get(KEY_SHADER):
            info = json.loads(mat[KEY_SHADER])
            info["index"] = -1                           # a material of another model: a new shader here
        if info is not None and 0 <= info.get("index", -1) < len(specs) and info["index"] not in claimed \
                and specs[info["index"]].name == info["name"]:
            spec, kind = specs[info["index"]], "own"
            claimed[spec.index] = mat.name
        elif info is not None and not info.get("opaque"):
            spec, kind = SH.ShaderSpec.from_json(info), "copy"   # keeps its VERS: its channels are read by it
            spec.index = -1
            spec.data_raw = SH.clean_data(spec.data_raw)
            spec.name = SH.unique_name(shader_name(mat.name), taken_shaders)
            if info.get("index", -1) in claimed:
                res.notes.append(f"material '{mat.name}' is a copy of '{claimed[info['index']]}': it gets its own shader "
                                 f"'{spec.name}'")
        elif info is not None:
            raise ValueError(f"material '{mat.name}' comes from a shader this add-on cannot rebuild; give those faces "
                             f"another material")
        else:
            name = SH.unique_name(shader_name(mat.name), taken_shaders)
            if donor is not None:
                spec = SH.new_shader_like(donor, name)
            else:
                spec = SH.blank_shader(name)
                res.notes.append(f"material '{mat.name}' is new and the model has no VERS 6 shader to copy settings from: "
                                 f"it gets the game's ordinary settings")
            kind = "new"
            ch0 = spec.channel(0)
            ch0.retarget("")
            ch0.mode, ch0.colour = 0, _argb(base_colour(mat))
        if kind != "own":
            taken_shaders.append(spec.name)
        ours = kind == "own"
        ch0 = spec.channel(0)
        src_ref = ch0.ref if ch0 is not None else ""     # the texture the source shader drew
        img = diffuse_image(mat)
        img_edited = img is not None and (img.name != mat.get(KEY_IMAGE_SRC) or image_changed(img))
        if ch0 is not None and img is not None:
            if not ours or img_edited:
                old = ch0.ref
                ref = texture_ref(img, spec.name)
                if ref != old:
                    for ch in spec.channels:
                        if ch.channel != 0 and ch.ref and old and ch.ref.lower() == old.lower():
                            ch.retarget(ref)
                    ch0.retarget(ref)
                if not ch0.mode & 1:
                    ch0.mode |= 1
                    if kind == "new":
                        ch0.colour = 0xFFFFFFFF
                        ch0.tile, ch0.filter = 1, 4
                ch4 = spec.channel(4)
                if kind == "new" and ch4 is not None and _has_alpha(img):
                    ch4.mode, ch4.tile = 1, 1                # the texture's own cut-outs, as stock shaders with alpha do
                    ch4.retarget(ch0.ref)
        elif ch0 is not None and img is None and kind == "own" and ch0.ref:
            res.notes.append(f"material '{mat.name}' shows no image in Blender; its shader keeps the file's texture")
        op = mat.get(KEY_OPACITY)
        if op is not None:
            alpha = int(round(max(0.0, min(1.0, float(op))) * 255))
            oc = opacity_channel(spec)
            if oc is None and alpha != 255 and spec.version >= 5:
                oc = SH.standard_channel(4)
                spec.channels.append(oc)
            if oc is not None and oc.colour >> 24 != alpha:
                set_opacity(spec, oc, alpha, mat.name, res.notes)
        team_on = bool(mat.get(KEY_TEAM_ON))
        if team_on and ch0 is not None:
            spec_team_old = spec.team
            mask_img = mat.get(KEY_TEAM_MASK)
            import bpy
            mask_img = bpy.data.images.get(mask_img) if mask_img else None
            mask_edited = mask_img is not None and image_changed(mask_img)
            # redo the variants only for an edit: team colour newly on, the texture painted or replaced, the mask painted.
            # (Not when entry 0 differs from channel 0: three stock shaders of m6_gyro_copter are like that.)
            redo = spec_team_old is None or img_edited or ch0.ref.lower() != src_ref.lower() or mask_edited
            if redo:
                if img is None:
                    raise ValueError(f"material '{mat.name}': team colour needs an image")
                w, h, base = image_rgba(img)
                if mask_img is not None:
                    mask = mask_of(mask_img, (w, h))
                else:
                    got = stock_team_mask(SH.ShaderSpec.from_json(info), raw, resolver) if info is not None else None
                    if got is None or got[:2] != (w, h):
                        raise ValueError(f"material '{mat.name}': team colour is on but it has no team mask; use Team Mask in "
                                         f"the IC Material panel and paint where the player colour goes")
                    mask = got[2]
                # the fitted recolour maps player 1's green; a painted team area, or a texture whose stock entry 0 was a
                # separate green copy, is turned into that green first, and entry 0 is then a texture of its own
                old_entries = spec_team_old.entries if spec_team_old is not None else []
                tint = mask_edited or bool(old_entries and old_entries[0][1].lower() != src_ref.lower())
                base0 = TC.tint_player0(base, mask) if tint else base
                count = max(spec_team_old.count if spec_team_old is not None else DEFAULT_TEAM_COUNT, SH.MIN_TEAM_COUNT)
                textures = TC.team_variants(base0, mask, count)
                stem_ = f"{entity}_{stem(spec.name)}"
                refs = [new_ref(f"{stem_}_tc01") if tint else ch0.ref]
                refs += [new_ref(f"{stem_}_tc{k + 1:02d}") for k in range(1, 6)]
                for k in range(0 if tint else 1, 6):
                    write_txr(refs[k], w, h, textures[k])
                refs += [refs[0]] * (count - 6)
                colours = [c for c, _r in old_entries] if len(old_entries) == count else [ch0.colour] * count
                spec.team = SH.TeamSpec(channel=spec_team_old.channel if spec_team_old is not None else 0,
                                        entries=[(c, r) for c, r in zip(colours, refs)], ref_tail=[b""] * count)
                res.notes.append(f"material '{mat.name}': team colour written for {count} colours "
                                 f"({6 if tint else 5} new textures)")
            elif kind != "own":                          # a copy keeps the source's team textures: load them here too
                for _c, r in spec.team.entries:
                    if r and r.lower() not in source_keys and available(r):
                        new_txrf.append(r)
        elif not team_on and spec.team is not None:
            spec.team = None
            res.notes.append(f"material '{mat.name}': team colour switched off")
        if kind == "own" and spec.changed and sum(s.name.lower() == spec.name.lower() for s in specs) > 1:
            # the faces find their shader by name, and the file has another shader of this name: the edit gets a name of
            # its own so that the game cannot draw the other one in its place
            was = spec.name
            spec.name = SH.unique_name(spec.name, taken_shaders)
            taken_shaders.append(spec.name)
            res.notes.append(f"material '{mat.name}': its shader shared the name '{was}' with another shader of the file; "
                             f"the edited one is written as '{spec.name}'")
        plans[mat.name] = spec.name
        if kind == "own":
            if spec.changed:
                res.changed.append(spec.name)
        else:
            new_specs.append(spec)
            res.new_shaders.append(spec.name)
    for obj in objs:
        names = []
        for i, slot in enumerate(obj.material_slots):
            if slot.material is not None:
                names.append(plans[slot.material.name])
            else:
                names.append(data.materials[i]["name"] if i < len(data.materials) else None)
        res.slot_names[obj.name] = names
    for ref in [t.name for t in sources if t.kind == "txrf"] + new_txrf:
        path = archive_path(ref)
        if path in res.files or path in res.carried or (resolver is not None and resolver.read(ref) is not None):
            continue
        got = loose(ref)
        if got is not None:
            res.carried[path] = got
            res.notes.append(f"texture {path} is not in the game: it is copied along from {loose_root}")
    if res.changed or new_specs or embedded or new_txrf:
        res.sgm = SH.set_materials(raw, specs + new_specs, new_txrf=new_txrf, embedded=embedded)
    if res.new_shaders:
        res.notes.append(f"new shader(s): {', '.join(res.new_shaders)}")
    if res.changed:
        res.notes.append(f"shader(s) changed: {', '.join(res.changed)}")
    return res


def _bsdf_look(mat) -> str:
    tree = mat.node_tree if mat.use_nodes else None
    bsdf = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None) if tree else None
    if bsdf is None:
        return ""
    vals = []
    for sock in bsdf.inputs:
        if sock.name in ("Base Color", "Alpha") or sock.is_linked or not hasattr(sock, "default_value"):
            continue
        v = sock.default_value
        vals.append([sock.identifier, [round(float(x), 4) for x in v] if hasattr(v, "__len__") else round(float(v), 4)])
    return json.dumps(vals)


def other_edits(objs) -> list:
    """Material edits the game has no counterpart for (the Principled BSDF's settings), in words."""
    out, seen = [], set()
    for obj in objs:
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.name in seen or mat.get(KEY_BSDF) is None:
                continue
            seen.add(mat.name)
            if _bsdf_look(mat) != str(mat[KEY_BSDF]):
                out.append(f"material '{mat.name}': its Blender shading settings are not written (the game uses only the "
                           f"texture, opacity and team colour)")
    return out


def data_folder_of(path: str):
    """The ``Data`` folder ``path`` is inside (any case), or None."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.basename(d).lower() == "data":
            return d
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd


def write_files(data_dir: str, files: dict) -> list:
    """Write ``files`` (lower-case paths inside Data) under ``data_dir``; returns the paths written."""
    out = []
    for rel, blob in sorted(files.items()):
        path = os.path.join(data_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
        out.append(path)
    return out
