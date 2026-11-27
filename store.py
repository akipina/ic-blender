"""Persistence of creature data on the object. Mesh vertices ARE the file's control-point pool; export re-reads the
source file and rewrites only edited data.

Per-point data lives in hidden mesh attributes (undone with the mesh). Per-patch tables are ID properties, which
edit-mode undo does NOT restore: write them only through the generation journal (:func:`begin_edit`, :func:`sync`).
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid

import numpy as np

MARK = "bz_native"          # truthy on objects this addon owns
KEY_SOURCE = "bz_source"    # original .sgm path (for lossless re-emit)
KEY_PATCHES = "bz_patches"  # flat int list, 16 per patch, indices into mesh verts
KEY_NPATCH = "bz_npatch"    # patch count
KEY_NAME = "bz_name"        # original creature name
KEY_PATCH_UV = "bz_patch_uv"    # flat float list, 8 per patch (4 corner UVs)
KEY_PATCH_MAT = "bz_patch_mat"  # material slot index per patch
KEY_MAT_TEX = "bz_mat_tex"      # texture image name per material slot ("" if none)
KEY_MAT_COL = "bz_mat_col"      # flat r,g,b per material slot
KEY_SLOTS = "bz_slot_names"     # material slot names
KEY_PATCH_TYPE = "bz_patch_type"      # per-patch smoothing-group bitmask (needed to re-emit grown geom)
KEY_MANUAL_INTERIOR = "bz_manual_interior"   # per patch: 1 = its interior points are hand-placed (the Auto Interior rule
#                                              leaves them; patchrules); absent / short = automatic
KEY_SRC_NVERTS = "bz_src_nverts"      # import-time counts, to detect topology growth
KEY_SRC_NPATCH = "bz_src_npatch"
KEY_POOL_N = "bz_pool_n"              # pool size the tables index (only SGM operators change it)
KEY_TEX_IMGS = "bz_tex_imgs"          # Blender image name per SOURCE texture index ("" if undecoded)
KEY_VERT_BONE = "bz_vert_bone"        # legacy: bone index per control point as an ID property (now ATTR_VERT_BONE)
KEY_BONE_NAMES = "bz_bone_names"      # bone names (index -> name), for the assign-to-bone menu
KEY_PATCH_LIMB = "bz_patch_limb"      # limb index per patch (-1 = none)
KEY_LIMB_NAMES = "bz_limb_names"      # limb names (exported: the LIMB records' names)
KEY_LIMB_SRC = "bz_limb_src"          # per limb: the source LIMB record it came from, -1 = made in Blender
KEY_LIMB_BONES = "bz_limb_bones"      # flat bone names of the limbs whose count is >= 0
KEY_LIMB_BONE_COUNTS = "bz_limb_bone_counts"   # per limb: bone-name count in bz_limb_bones, -1 = the source record's
KEY_CONN_NAMES = "bz_conn_names"      # connection names
KEY_CONN_BONES = "bz_conn_bones"      # bone each connection attaches to
KEY_CONN_VERTS = "bz_conn_verts"      # flattened control-point indices of all connection rings
KEY_CONN_COUNTS = "bz_conn_counts"    # ring size per connection
KEY_CONN_ENC = "bz_conn_enc"          # per connection: 1 = bz_conn_verts holds its corner loop, 0 = ints kept verbatim
KEY_CONN_FLAGS1 = "bz_conn_flags1"    # LCON flags1 per connection (exported: the Parent class, the limb the ring is cut into)
KEY_CONN_FLAGS2 = "bz_conn_flags2"    # LCON flags2 per connection (exported: the Child class, the limb attached there)
KEY_CONN_SRC = "bz_conn_src"          # per connection: the source LCON record it came from, -1 = made in Blender
KEY_LIMB_IDS = "bz_limb_ids"          # limb_id per limb (exported: the classification; i32-stored u32)
KEY_LIMB_PINS = "bz_limb_pins"        # names of the ADDED bones a Boner limb operator put in a limb by hand: those stay
                                      # in the row that lists them instead of following their parent (C8 + fit-to-rig)
KEY_STORE_VERSION = "bz_store_version"  # 2 = rings stored as corner loops (absent: raw ints, decoded on read)
STORE_VERSION = 2
KEY_GEN = "bz_gen"                    # generation of the tables currently on the object
KEY_JOURNAL = "bz_journal"            # {generation: {table key: value}}
KEY_UID = "bz_uid"                    # identity of this creature object (stamped on its images and cage)
KEY_SRC_SHA1 = "bz_source_sha1"       # identity of the source file at import
KEY_SRC_SIZE = "bz_source_size"
KEY_BUILD_ERRORS = "bz_build_errors"  # what import could not set up (reported, never swallowed)
KEY_RIG = "bz_rig"
KEY_BONE_NEW = "bz_bone_new"          # the new-bone table: per bone Add Bone made (or adopted), its uid (rig.KEY_BONE_UID);
                                      # owner value n_src + j in .bz_vert_bone names entry j (append-only)
KEY_BONE_NEW_NAMES = "bz_bone_new_names"   # ... and the name it was made with (messages, a bone missing from the rig)

ATTR_CP = ".bz_cp"
ATTR_GEN = ".bz_gen"
ATTR_VERT_BONE = ".bz_vert_bone"
ATTR_COPLANAR = ".bz_coplanar"        # per corner: 1 = the Coplanar vertex type (patchsel), absent / 0 = Corner
ATTR_SRC = ".bz_src"                  # per point: its index in the SOURCE pool, -1 = made since (Extrude); travels with
#                                       the vertex through Delete Patch's compaction, so the export can pair points

# the tables that describe the pool's topology and records: journaled per generation
# (the ring encoding and the store version travel with the rings: an Undo back to a pre-v2 state restores raw ints
# AND drops the version, so they are never read as corner loops)
# The limb table (names, ids, source identity, bone lists) joined in Boner Limbs: an object journaled before that has
# entries without those keys, which :func:`upgrade_limb_tables` backfills (with the values that never changed then)
# before any sync can select one.
# The connection classes and source identity joined in Boner Connections the same way (:func:`upgrade_conn_tables`).
LIMB_TABLE_KEYS = (KEY_LIMB_NAMES, KEY_LIMB_IDS, KEY_LIMB_SRC, KEY_LIMB_BONES, KEY_LIMB_BONE_COUNTS)
CONN_TABLE_KEYS = (KEY_CONN_FLAGS1, KEY_CONN_FLAGS2, KEY_CONN_SRC)
# The new-bone table joined in Boner Add Bone: an entry journaled before it has no such key, which means no added bone
# (sync then removes the key, the state that generation had).
BONE_TABLE_KEYS = (KEY_BONE_NEW, KEY_BONE_NEW_NAMES)
# The IK chain table (Boner > Bones > IK Chains, C8): absent = the source's IKCH chunk as it is (an entry journaled
# before the table has no such key, which means just that).
KEY_IK_NAMES = "bz_ik_names"          # per chain: its name
KEY_IK_SRC = "bz_ik_src"              # per chain: the source IKCH chain it came from, -1 = made in Blender
KEY_IK_FLAGS = "bz_ik_flags"          # per chain: the u16 flags word
KEY_IK_ENABLED = "bz_ik_enabled"      # per chain: the u16 enabled word
KEY_IK_BONES = "bz_ik_bones"          # flat bone names of every chain (FILE names of source bones)
KEY_IK_COUNTS = "bz_ik_counts"        # per chain: how many of bz_ik_bones are its
IK_TABLE_KEYS = (KEY_IK_NAMES, KEY_IK_SRC, KEY_IK_FLAGS, KEY_IK_ENABLED, KEY_IK_BONES, KEY_IK_COUNTS)
# The added-bone limb pins (fit-to-rig) are journaled on their own, like the IK table: an entry from before
# them has no such key, which means no pin -- there is nothing to backfill.
TABLE_KEYS = (KEY_PATCHES, KEY_NPATCH, KEY_PATCH_UV, KEY_PATCH_MAT, KEY_PATCH_TYPE, KEY_PATCH_LIMB, KEY_CONN_NAMES,
              KEY_CONN_BONES, KEY_CONN_VERTS, KEY_CONN_COUNTS, KEY_CONN_ENC, KEY_STORE_VERSION, KEY_POOL_N,
              KEY_MANUAL_INTERIOR, KEY_LIMB_PINS) + LIMB_TABLE_KEYS + CONN_TABLE_KEYS + BONE_TABLE_KEYS + IK_TABLE_KEYS
# The axes an IC object was made in (core.sgm_to_blender): 2 = game (x, y, z) -> Blender (-x, -z, y). Every import and
# every IC object made in Blender carries it; every export refuses an object without it.
KEY_AXES = "ic_axes"
AXES = 2
OLD_AXES_MESSAGE = "This was made with an older version of IC Blender. Import the original file again."

JOURNAL_MIN = 64          # journal entries kept per object: at least this, and Blender's undo steps plus a margin


def journal_max() -> int:
    """How many generations the journal keeps: enough for every undo step Blender keeps (Edit > Preferences > System >
    Undo Steps), so an Undo back to the import state never finds its tables trimmed."""
    try:
        import bpy
        steps = int(bpy.context.preferences.edit.undo_steps)
    except Exception:
        steps = 0
    return max(JOURNAL_MIN, steps + 16)


# ------------------------------------------------------------------------------------------- point attributes
def _edit_bmesh(obj):
    if obj.mode == "EDIT":
        import bmesh
        return bmesh.from_edit_mesh(obj.data)
    return None


def point_count(obj) -> int:
    bm = _edit_bmesh(obj)
    return len(bm.verts) if bm is not None else len(obj.data.vertices)


def point_ints(obj, name):
    """A per-point INT attribute as an int64 array (the live bmesh layer in edit mode), or None when absent."""
    bm = _edit_bmesh(obj)
    if bm is not None:
        lay = bm.verts.layers.int.get(name)
        if lay is None:
            return None
        return np.fromiter((v[lay] for v in bm.verts), dtype=np.int64, count=len(bm.verts))
    me = obj.data
    a = me.attributes.get(name)
    if a is None or a.domain != "POINT" or a.data_type != "INT":
        return None
    arr = np.empty(len(me.vertices), dtype=np.int32)
    a.data.foreach_get("value", arr)
    return arr.astype(np.int64)


def set_point_ints(obj, name, values) -> None:
    vals = np.asarray(values, dtype=np.int64).reshape(-1)
    bm = _edit_bmesh(obj)
    if bm is not None:
        if len(vals) != len(bm.verts):
            raise ValueError(f"{name}: {len(vals)} values for {len(bm.verts)} points")
        lay = bm.verts.layers.int.get(name) or bm.verts.layers.int.new(name)
        for v, x in zip(bm.verts, vals.tolist()):
            v[lay] = x
        return
    me = obj.data
    if len(vals) != len(me.vertices):
        raise ValueError(f"{name}: {len(vals)} values for {len(me.vertices)} points")
    a = me.attributes.get(name)
    if a is not None and (a.domain != "POINT" or a.data_type != "INT"):
        me.attributes.remove(a)
        a = None
    if a is None:
        a = me.attributes.new(name, "INT", "POINT")
    a.data.foreach_set("value", vals.astype(np.int32))


# ------------------------------------------------------------------------------------------- the journal
def _plain(v):
    return v.to_list() if hasattr(v, "to_list") else (v.to_dict() if hasattr(v, "to_dict") else v)


def _tables(obj) -> dict:
    return {k: _plain(obj[k]) for k in TABLE_KEYS if k in obj}


# A session-side copy of every journal entry, keyed by (object identity, generation). The object's journal is an ID
# property, and a memfile (object-mode) undo step restores ID properties as they were when that step was pushed: after
# an SGM operator in Edit Mode, Tab back to Object Mode and Ctrl+Z, the edit mesh comes back with the operator's
# generation while the object's journal is the one from before the edit session, which never held it. This copy lives
# outside the undo system, so :func:`sync` finds the tables there (and puts them back into the object's journal).
_JOURNAL_CACHE: "dict" = {}


def _identity(obj) -> str:
    return str(obj.get(KEY_UID, "") or "") or f"name:{obj.name}"


def _copy_table(value):
    v = _plain(value)
    if isinstance(v, dict):
        return {k: _copy_table(x) for k, x in v.items()}
    return list(v) if isinstance(v, (list, tuple)) else v


def _cache_put(obj, gen, tables) -> None:
    key = (_identity(obj), int(gen))
    _JOURNAL_CACHE.pop(key, None)
    _JOURNAL_CACHE[key] = {k: _copy_table(v) for k, v in tables.items()}
    limit = 4 * journal_max()
    while len(_JOURNAL_CACHE) > limit:                    # oldest first (dicts keep insertion order)
        _JOURNAL_CACHE.pop(next(iter(_JOURNAL_CACHE)))


def _cache_get(obj, gen):
    return _JOURNAL_CACHE.get((_identity(obj), int(gen)))


def forget_journal_cache() -> None:
    _JOURNAL_CACHE.clear()


def _set(obj, key, value) -> None:
    """Write a table and mirror it into the journal entry of the current generation (and its session copy)."""
    obj[key] = value
    g = obj.get(KEY_GEN)
    j = obj.get(KEY_JOURNAL)
    if g is not None and j is not None and str(g) in j:
        j[str(g)][key] = value
    if g is not None:
        hit = _cache_get(obj, g)
        if hit is not None:
            hit[key] = _copy_table(value)


def _new_gen(obj) -> int:
    j = obj.get(KEY_JOURNAL) or {}
    while True:
        # below 2**23: Blender interpolates INT attributes through float32 weights (Merge by Distance), which keeps
        # equal values exactly only while they fit a float32 mantissa
        g = int.from_bytes(os.urandom(4), "little") & 0x7FFFFF
        if g and str(g) not in j:
            return g


def _journal_put(obj, gen, tables) -> None:
    if obj.get(KEY_JOURNAL) is None:
        obj[KEY_JOURNAL] = {}
    j = obj[KEY_JOURNAL]
    j[str(gen)] = tables
    _cache_put(obj, gen, tables)
    keys = list(j.keys())
    limit = journal_max()
    while len(keys) > limit:
        k0 = keys.pop(0)
        if k0 != str(gen) and k0 != str(obj.get(KEY_GEN)):
            del j[k0]


def mesh_generation(obj):
    """The generation stamped on the mesh's points (the stamp most points carry), or None when unstamped."""
    g = point_ints(obj, ATTR_GEN)
    if g is None or not len(g):
        return None
    vals, counts = np.unique(g, return_counts=True)            # the most common stamp: a new vertex gets 0
    return int(vals[int(np.argmax(counts))])


def init_generation(obj) -> int:
    """Stamp a fresh object: pool ids = arange, a first generation and its journal entry."""
    n = point_count(obj)
    g = _new_gen(obj)
    obj[KEY_GEN] = g
    obj[KEY_JOURNAL] = {}
    _journal_put(obj, g, _tables(obj))
    set_point_ints(obj, ATTR_CP, np.arange(n))
    set_point_ints(obj, ATTR_GEN, np.full(n, g))
    if point_ints(obj, ATTR_SRC) is None:
        set_point_ints(obj, ATTR_SRC, np.arange(n))       # a fresh object: the pool IS the source pool
    return g


def pool_src(obj):
    """(N,) the source pool index of each point (-1 = made since import), or None when the object predates the
    attribute (its pool is then the source's, or a prefix of it)."""
    a = point_ints(obj, ATTR_SRC)
    return None if a is None or len(a) != point_count(obj) else a


def migrate(obj):
    """Bring an object stored before the pool guard up to date (ids, generation, per-point owners as an attribute).
    Only when its counts are consistent; returns a readable reason when they are not (nothing is written then)."""
    if obj.get(KEY_GEN) is not None:
        return None
    from . import core
    n = point_count(obj)
    legacy_vb = obj.get(KEY_VERT_BONE)
    err = core.pool_error(None, n, patches(obj), len(legacy_vb) if legacy_vb is not None else None,
                          per_patch_lengths(obj))
    if err:
        return f"cannot verify this object's control-point pool (stored before the pool guard): {err}"
    if KEY_POOL_N not in obj:
        obj[KEY_POOL_N] = n
    upgrade_limb_tables(obj)
    upgrade_conn_tables(obj)
    if legacy_vb is not None and point_ints(obj, ATTR_VERT_BONE) is None:
        set_point_ints(obj, ATTR_VERT_BONE, np.asarray(list(legacy_vb), dtype=np.int64))
        del obj[KEY_VERT_BONE]
    init_generation(obj)
    return None


def generation_error(obj):
    """Read-only: why the tables on the object can't be matched to the mesh (None when they match, or when a sync
    would restore them)."""
    mg, pg = mesh_generation(obj), obj.get(KEY_GEN)
    if pg is not None and mg is None:
        return (f"the generation attribute '{ATTR_GEN}' was removed from the mesh, so the model's data can't be matched to "
                "it. Undo its removal or re-import the creature")
    if mg is None or pg is None or mg == pg:
        return None
    j = obj.get(KEY_JOURNAL)
    if (j is not None and str(mg) in j) or _cache_get(obj, mg) is not None:
        return None
    return (f"the model's data on this object belongs to another state of the mesh (generation {pg}, mesh {mg}, which the "
            "journal no longer holds): Redo (Ctrl+Shift+Z) back to it, or re-import")


def sync(obj):
    """Re-select the tables recorded for the mesh's generation (after an edit-mode Undo / Redo). Needs a context
    that may write ID properties. Returns an error string when it can't."""
    mg, pg = mesh_generation(obj), obj.get(KEY_GEN)
    if pg is None:
        return migrate(obj)
    upgrade_limb_tables(obj)
    upgrade_conn_tables(obj)
    if mg is None:
        return (f"the generation attribute '{ATTR_GEN}' was removed from the mesh, so the model's data can't be matched to "
                "it. Undo its removal or re-import the creature")
    if mg == pg:
        return None
    j = obj.get(KEY_JOURNAL)
    snap = j.get(str(mg)) if j is not None else None
    from_cache = snap is None
    if from_cache:
        snap = _cache_get(obj, mg)                     # an entry a memfile undo step rolled the object's journal past
    if snap is None:
        return generation_error(obj)
    snap = {k: _copy_table(snap[k]) for k in snap.keys()}
    for k in TABLE_KEYS:
        if k in snap:
            obj[k] = snap[k]
        elif k in obj:
            del obj[k]
    obj[KEY_GEN] = mg
    if from_cache:
        _journal_put(obj, mg, {k: _copy_table(v) for k, v in snap.items()})
    return None


def begin_edit(obj) -> int:
    """Start a new generation before an SGM operator changes tables or adds points: the current tables are kept
    under the old generation, the mesh's points are stamped with the new one. Raises RuntimeError when the object is
    out of sync."""
    err = sync(obj)
    if err:
        raise RuntimeError(err)
    g = _new_gen(obj)
    _journal_put(obj, g, _tables(obj))
    obj[KEY_GEN] = g
    set_point_ints(obj, ATTR_GEN, np.full(point_count(obj), g))
    return g


# ------------------------------------------------------------------------------------------- pool guard
def per_patch_lengths(obj) -> dict:
    """Row counts of the per-patch tables AS STORED (None = absent, never synthesized)."""
    def rows(key, width=1):
        v = obj.get(key)
        return None if v is None else len(v) // width
    return {"patch table": rows(KEY_PATCHES, 16), "UV": rows(KEY_PATCH_UV, 8), "material": rows(KEY_PATCH_MAT),
            "type": rows(KEY_PATCH_TYPE), "limb": rows(KEY_PATCH_LIMB)}


def pool_error(obj):
    """Read-only: why the mesh is not the pool the SGM tables index (None = consistent). See core.pool_error."""
    from . import core
    if not is_native(obj):
        return None
    if not axes_current(obj):                  # made before the axis flip: its tables are in the old axes (core.sgm_to_blender)
        return OLD_AXES_MESSAGE
    gerr = generation_error(obj)
    if gerr:
        return gerr
    flat = obj.get(KEY_PATCHES)
    npatch = int(obj.get(KEY_NPATCH, 0))
    if flat is not None and len(flat) != 16 * npatch:
        return f"the patch table has {len(flat)} indices for {npatch} patches: re-import the creature"
    ids = point_ints(obj, ATTR_CP)
    pn = obj.get(KEY_POOL_N)
    lengths = per_patch_lengths(obj)
    lengths.pop("patch table")
    if ids is None:
        if pn is not None:
            n = point_count(obj)
            base = core.pool_error(None, n, patches(obj), None, lengths)
            return base or (f"the control-point id attribute '{ATTR_CP}' was removed from the mesh, so a reorder can't "
                            "be detected: Undo its removal or re-import the creature")
        legacy_vb = obj.get(KEY_VERT_BONE)
        return core.pool_error(None, point_count(obj), patches(obj), len(legacy_vb) if legacy_vb is not None else None,
                               lengths)
    vb = point_ints(obj, ATTR_VERT_BONE)
    err = core.pool_error(ids, int(pn) if pn is not None else None, patches(obj),
                          None if vb is None else len(vb), lengths)
    if err:
        return err
    from . import patchsel                     # the faces: exactly one corner quad per patch (a native face delete)
    return patchsel.face_error(obj, patches(obj))


def pool_ids_error(obj):
    """The per-point part of :func:`pool_error` alone (ids against the pool size and the patch table): one pass over
    the points, for a redraw while points move."""
    from . import core
    ids = point_ints(obj, ATTR_CP)
    if ids is None:
        return pool_error(obj)
    pn = obj.get(KEY_POOL_N)
    return core.pool_error(ids, int(pn) if pn is not None else None, patches(obj))


_POOL_CACHE: dict = {}
_POOL_TTL = 0.5


def pool_error_cached(obj):
    """:func:`pool_error` memoised for a moment per object state (for poll / panel draw, called every redraw)."""
    from . import patchsel
    key = (obj.name, obj.mode, point_count(obj), patchsel.face_count(obj), obj.get(KEY_GEN),
           int(obj.get(KEY_NPATCH, 0)))
    hit = _POOL_CACHE.get(obj.name)
    now = time.monotonic()
    if hit is not None and hit[0] == key and now - hit[1] < _POOL_TTL:
        return hit[2]
    err = pool_error(obj)
    _POOL_CACHE[obj.name] = (key, now, err)
    return err


def forget_cache() -> None:
    _POOL_CACHE.clear()


# ------------------------------------------------------------------------------------------- tagging
def stamp_axes(*ids) -> None:
    """Mark objects (or other ID blocks) as made in the current axes (:data:`AXES`)."""
    for i in ids:
        if i is not None:
            i[KEY_AXES] = AXES


def axes_current(i) -> bool:
    """True when ``i`` carries the current axes stamp."""
    try:
        return i is not None and int(i.get(KEY_AXES, 0)) == AXES
    except (TypeError, ValueError):
        return False


def axes_refusal(*ids):
    """:data:`OLD_AXES_MESSAGE` when any of ``ids`` (None skipped) lacks the current axes stamp, else None."""
    if any(i is not None and not axes_current(i) for i in ids):
        return OLD_AXES_MESSAGE
    return None


def tag(obj, data) -> None:
    obj[MARK] = True
    obj[KEY_AXES] = AXES
    obj[KEY_SOURCE] = data.source_path
    obj[KEY_NAME] = data.name
    obj[KEY_UID] = uuid.uuid4().hex
    obj[KEY_NPATCH] = int(len(data.patches))
    obj[KEY_PATCHES] = [int(i) for i in data.patches.reshape(-1)]
    obj[KEY_PATCH_UV] = [float(x) for x in data.patch_uv.reshape(-1)]
    obj[KEY_PATCH_MAT] = [int(i) for i in data.patch_mat]
    obj[KEY_SRC_NVERTS] = int(len(data.verts))
    obj[KEY_SRC_NPATCH] = int(len(data.patches))
    obj[KEY_POOL_N] = int(len(data.verts))
    if data.model is not None:
        obj[KEY_PATCH_TYPE] = [int(p.type) for p in data.model.patches]
    if data.source_path and os.path.exists(data.source_path):
        stamp_source(obj, data.source_path)
    init_generation(obj)


def record_build_error(obj, what, exc) -> None:
    errs = list(obj.get(KEY_BUILD_ERRORS, []))
    errs.append(f"{what}: {type(exc).__name__}: {exc}")
    obj[KEY_BUILD_ERRORS] = errs


def uid(obj) -> str:
    return str(obj.get(KEY_UID, ""))


# ------------------------------------------------------------------------------------------- source identity
_SHA_CACHE: dict = {}


def file_sha1(path: str):
    """(size, sha1 hex) of a file, cached per (path, mtime, size, inode)."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _SHA_CACHE.get(key[0])
    if hit is not None and hit[0] == key:
        return hit[1]
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    val = (st.st_size, h.hexdigest())
    _SHA_CACHE[key[0]] = (key, val)
    return val


def rebase_source(obj, path) -> None:
    """Re-stamp the object on its own base file after Export wrote that file (an in-place save: the file the creature
    was built on is now the file the creature IS). The identity stamp follows, and so do the two facts that were about
    the OLD base: its counts (:func:`grew`) and the per-point source index (which the grown build path reads), both of
    which the new base matches exactly, point for point.

    Only safe while no skeleton edit is pending -- those are a diff against the base and would be applied twice
    (``ops.in_place_refusal``, which the caller checks first)."""
    stamp_source(obj, path)
    n = point_count(obj)
    obj[KEY_SRC_NVERTS] = int(n)
    obj[KEY_SRC_NPATCH] = int(obj.get(KEY_NPATCH, 0))
    if point_ints(obj, ATTR_SRC) is not None:
        set_point_ints(obj, ATTR_SRC, np.arange(n))       # the pool IS the new base's pool, point for point


def stamp_source(obj, path) -> None:
    size, sha = file_sha1(path)
    obj[KEY_SRC_SHA1] = sha
    obj[KEY_SRC_SIZE] = int(size)


def source_error(obj):
    """Why the recorded source can't be the base of an export (None = it is the file imported)."""
    src = source_path(obj)
    if not src:
        return "No source .sgm recorded on this object"
    if not os.path.exists(src):
        return f"the source file {src} is missing: the export is built on it (restore it or re-import the creature)"
    want = obj.get(KEY_SRC_SHA1)
    if want is None:
        return None
    size, sha = file_sha1(src)
    if sha != str(want):
        return (f"the source file {src} changed since import ({int(obj.get(KEY_SRC_SIZE, 0))} -> {size} bytes), so Export "
                "would build on a different creature: restore it or re-import the creature")
    return None


# ------------------------------------------------------------------------------------------- linked objects
def rig(obj):
    """The creature's armature: the object of its Armature modifier (follows renames), else by recorded name."""
    import bpy
    if obj is None:
        return None
    for mod in getattr(obj, "modifiers", []):
        if mod.type == "ARMATURE" and mod.object is not None and mod.object.type == "ARMATURE":
            return mod.object
    name = obj.get(KEY_RIG)
    arm = bpy.data.objects.get(str(name)) if name else None
    return arm if arm is not None and arm.type == "ARMATURE" else None


def rig_error(obj):
    if obj.get(KEY_RIG) and rig(obj) is None:
        return (f"the rig '{obj[KEY_RIG]}' is gone (no Armature modifier object, no object of that name): its rest "
                "pose and Actions can't be read. Undo its deletion or re-import the creature")
    return None


def texture_image(obj, index):
    """(image or None, error or None) for source texture ``index``: by recorded name, else by the image's owner
    stamp (a renamed image)."""
    import bpy
    names = texture_images(obj)
    name = names[index] if 0 <= index < len(names) else ""
    if not name:
        return None, None
    me = uid(obj)
    img = bpy.data.images.get(name)
    if img is not None and img.get("bz_tex_for") in (None, me):
        return img, None
    if me:
        for im in bpy.data.images:
            if im.get("bz_tex_for") == me and int(im.get("bz_tex_index", -1)) == index:
                return im, None
    return None, (f"texture {index}'s image '{name}' is missing (renamed without its stamp, or deleted): its paint "
                  "can't be read. Rename it back, undo the deletion or re-import the creature")


def stamp_images(obj, images) -> None:
    me = uid(obj)
    for i, img in enumerate(images):
        if img is not None:
            img["bz_tex_for"] = me
            img["bz_tex_index"] = i


# ------------------------------------------------------------------------------------------- appearance
def set_appearance(obj, table, image_names, slot_names) -> None:
    """Store the material table (texture per slot + base colour) and slot names."""
    obj[KEY_MAT_TEX] = [str(image_names.get(s["tex"], "")) for s in table]
    obj[KEY_MAT_COL] = [float(c) for s in table for c in s["color"]]
    obj[KEY_SLOTS] = [str(n) for n in slot_names]


def set_texture_images(obj, image_names_by_texindex) -> None:
    """image name per source-texture index (aligned to model.textures order), for export."""
    obj[KEY_TEX_IMGS] = [str(n) for n in image_names_by_texindex]


def texture_images(obj):
    return list(obj.get(KEY_TEX_IMGS, []))


KEY_TEX_NAMES = "bz_tex_names"        # texture FILE name per texture index when the table was replaced (Transfer
                                      # Texture); absent = the source's textures in the source's order


def set_texture_table(obj, image_names, tex_names) -> None:
    """Replace the creature's texture table: ``image_names[i]`` is written as texture ``tex_names[i]`` (a name not in
    the source is a new texture, encoded from its image), and EVERY material slot shows the first image (positional
    slots kept). Export follows through ``texture_names`` / ``slot_texture_names``."""
    obj[KEY_TEX_IMGS] = [str(n) for n in image_names]
    obj[KEY_TEX_NAMES] = [str(n) for n in tex_names]
    obj[KEY_MAT_TEX] = [str(image_names[0]) if image_names else ""] * len(obj.get(KEY_SLOTS, []))


def texture_names(obj):
    """The replaced texture table's file names (aligned to :func:`texture_images`), or None (the source's)."""
    v = obj.get(KEY_TEX_NAMES)
    return None if v is None else [str(n) for n in v]


def slot_texture_names(obj):
    """Texture file name per material slot ('' = none) when the table was replaced, else None (the source's tags)."""
    names = texture_names(obj)
    if names is None:
        return None
    imgs = texture_images(obj)
    by_img = {img: names[i] for i, img in enumerate(imgs) if i < len(names)}
    return [by_img.get(str(t), "") for t in obj.get(KEY_MAT_TEX, [])]


def patch_uv(obj) -> np.ndarray:
    """(Np, 8) corner UVs as stored; a missing table reads as zeros (use :func:`has` to tell)."""
    n = int(obj.get(KEY_NPATCH, 0))
    flat = obj.get(KEY_PATCH_UV)
    if flat is None or len(flat) != 8 * n:
        return np.zeros((n, 8))
    return np.array(flat, dtype=np.float64).reshape(n, 8)


def set_patch_uv(obj, uv) -> None:
    _set(obj, KEY_PATCH_UV, [float(x) for x in np.asarray(uv, dtype=np.float64).reshape(-1)])


def has(obj, key) -> bool:
    return obj.get(key) is not None


# --- skinning ownership (which bone owns each control point) ---------------------------
def set_vert_bone(obj, arr) -> None:
    """Write the owner table and keep the rig's bone vertex groups in step with it: the groups are what Blender
    deforms, and Export adopts a rigid group reassignment made with Blender's own tools (``core.skin_owners``), so every
    owner the add-on writes goes into the groups too (one bone group per point at weight 1, -1 = none)."""
    set_point_ints(obj, ATTR_VERT_BONE, arr)
    if KEY_VERT_BONE in obj:
        del obj[KEY_VERT_BONE]
    sync_bone_groups(obj, arr)


def sync_bone_groups(obj, owners) -> int:
    """Make each point's bone vertex groups say ``owners`` (weight 1 in its owner's group, in no other bone's group);
    groups not named after a bone are left alone. Only on a rigged object (one that already has bone groups). Returns
    how many points were rewritten."""
    names = bone_rig_names(obj)
    if not names or not any(vg.name in names for vg in obj.vertex_groups):
        return 0
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    index = {n: i for i, n in enumerate(names)}
    bone_of = {vg.index: index[vg.name] for vg in obj.vertex_groups if vg.name in index}
    want_groups = {}
    for b in sorted({int(x) for x in own.tolist() if 0 <= x < len(names) and names[x]}):
        vg = obj.vertex_groups.get(names[b])
        if vg is None:
            vg = obj.vertex_groups.new(name=names[b])
            bone_of[vg.index] = b
        want_groups[b] = vg.index

    def target(i):
        b = int(own[i])
        return {want_groups[b]: 1.0} if b in want_groups else {}
    bm = _edit_bmesh(obj)
    n = 0
    if bm is not None:
        bm.verts.ensure_lookup_table()
        dl = bm.verts.layers.deform.verify()
        if len(bm.verts) != len(own):
            raise ValueError(f"bone groups: {len(own)} owners for {len(bm.verts)} points")
        for i, v in enumerate(bm.verts):
            dv = v[dl]
            cur = {g: w for g, w in dv.items() if g in bone_of}
            want = target(i)
            if cur != want:
                for g in cur:
                    del dv[g]
                for g, w in want.items():
                    dv[g] = w
                n += 1
        return n
    me = obj.data
    if len(me.vertices) != len(own):
        raise ValueError(f"bone groups: {len(own)} owners for {len(me.vertices)} points")
    fix = []
    for i, v in enumerate(me.vertices):
        cur = {g.group: g.weight for g in v.groups if g.group in bone_of}
        if cur != target(i):
            fix.append(i)
    if fix:
        groups = {vg.index: vg for vg in obj.vertex_groups}
        for g in bone_of:
            groups[g].remove(fix)
        by_group = {}
        for i in fix:
            for g in target(i):
                by_group.setdefault(g, []).append(i)
        for g, idx in by_group.items():
            groups[g].add(idx, 1.0, "REPLACE")
    return len(fix)


def skin_group_weights(obj, names=None):
    """Per control point, ``[(source bone index, weight)]`` over the vertex groups the Armature modifier deforms with:
    groups named after a DEFORMING bone of the rig (the live bmesh in Edit Mode). ``names``: the rig's bone names in
    source order (Export passes the import snapshot); default: each bone's source-index stamp (:func:`bone_index`,
    which follows a renamed bone and its renamed group). None when the creature has no rig."""
    arm = rig(obj)
    if arm is None:
        return None
    index = {n: i for i, n in enumerate(names)} if names is not None else None
    gmap = {}
    for vg in obj.vertex_groups:
        bone = arm.data.bones.get(vg.name)
        if bone is None or not bone.use_deform:
            continue
        bi = index.get(vg.name) if index is not None else bone_index(obj, vg.name)
        if bi is not None and bi >= 0:
            gmap[vg.index] = bi
    bm = _edit_bmesh(obj)
    if bm is not None:
        dl = bm.verts.layers.deform.active
        if dl is None:
            return [[] for _v in bm.verts]
        return [[(gmap[g], w) for g, w in v[dl].items() if g in gmap] for v in bm.verts]
    return [[(gmap[g.group], g.weight) for g in v.groups if g.group in gmap] for v in obj.data.vertices]


class LiveSkin:
    """The skin as Blender deforms it right now (``live_skin``): ``owners`` (adopted rigid group reassignments, a point
    no bone group deforms = -1: what By Bone, the Boner read-outs and selections show), ``base`` (the owner table a skin
    edit starts from: adopted, but unskinned / blended points keep their stored owner), ``adopted``, ``unskinned``,
    ``blended`` (point indices) and ``weights`` (per point, for messages)."""

    def __init__(self, stored, weights):
        from . import core
        self.stored = np.asarray(stored, dtype=np.int64)
        self.weights = weights
        if weights is None:
            self.base = self.stored.copy()
            self.adopted, self.unskinned, self.blended = [], [], []
        else:
            self.base, self.adopted, self.unskinned, self.blended = core.skin_group_state(weights, self.stored)
        self.owners = core.live_owners(self.base, self.unskinned)


def live_skin(obj):
    """:class:`LiveSkin` of a native creature: its owner table read against the rig's bone vertex groups (Ctrl+G and
    weight paint edit those, and Export adopts them). Without a rig, the table alone. Raises ValueError when the groups
    and the table disagree on the point count (the pool guard reports that)."""
    return LiveSkin(vert_bone(obj), skin_group_weights(obj))


def vert_bone(obj) -> np.ndarray:
    a = point_ints(obj, ATTR_VERT_BONE)
    if a is not None:
        return a
    return np.array(obj.get(KEY_VERT_BONE, []), dtype=np.int64)


def has_vert_bone(obj) -> bool:
    return point_ints(obj, ATTR_VERT_BONE) is not None or obj.get(KEY_VERT_BONE) is not None


def bone_names(obj):
    return list(obj.get(KEY_BONE_NAMES, []))


def new_bone_uids(obj) -> list:
    """The new-bone table (uids, in key order: entry j is owner key ``n_src + j``)."""
    return [str(u) for u in obj.get(KEY_BONE_NEW, []) or []]


def new_bone_table_names(obj) -> list:
    names = [str(u) for u in obj.get(KEY_BONE_NEW_NAMES, []) or []]
    uids = new_bone_uids(obj)
    return (names + [""] * len(uids))[:len(uids)]


def add_new_bones(obj, uids, names) -> list:
    """Append bones to the new-bone table (journaled: the caller has begun the edit); returns their owner keys."""
    have, hn = new_bone_uids(obj), new_bone_table_names(obj)
    base = len(bone_names(obj)) + len(have)
    _set(obj, KEY_BONE_NEW, have + [str(u) for u in uids])
    _set(obj, KEY_BONE_NEW_NAMES, hn + [str(n) for n in names])
    return list(range(base, base + len(uids)))


class AddedBoneCheckError(RuntimeError):
    """The add rule could not be run over the rig's added bones, so they have no owner keys (:func:`new_bone_keys`)."""


def source_stubs(obj) -> frozenset:
    """The source's stub bone indices (``core.source_stub_bones``, parsed once per file version). Raises when the
    source can't be read."""
    try:
        from . import core
    except ImportError:                  # imported top-level (pure tests)
        import core  # type: ignore[no-redef]
    return core.source_stub_bones(source_path(obj))


_EXPORT_STUB_CACHE: dict = {}


def export_stubs(obj) -> frozenset:
    """The source bone indices Export writes as stub bones (``structure.export_stub_bones``), which take no added bone:
    every flagged one, plus the point-less bones of a limb with no patches -- read off the LIVE limb rows, the stored
    patch membership and the owner table (a Ctrl+G reassignment counts once a Boner skin edit stores it), not off the
    source file. Giving a limb patches in Blender therefore takes its bones OUT of the stub set at once; before
    2026-09-16 only a save and re-import did that (the native pillbug build: 43 stub bones live, 4 after re-opening the
    very same creature, and C8 refused every bone in between). What the panel read-outs, Add Bone and Adopt Added Bones
    classify added bones against, so they say what Export's skeleton guard says. Worked out again only when the
    generation (every owner / patch-limb write starts one) or the limb table changes (panel draw runs every redraw).
    Raises when the source or the limb table can't be read."""
    base = source_stubs(obj)
    srcs = obj.get(KEY_LIMB_SRC)
    if srcs is None and obj.get(KEY_PATCH_LIMB) is None and not has_vert_bone(obj):
        return base                                  # nothing live to read: the source's own answer
    key = (source_path(obj), base, obj.get(KEY_GEN), int(obj.get(KEY_NPATCH, 0)), tuple(obj.get(KEY_LIMB_NAMES, [])),
           tuple(obj.get(KEY_LIMB_IDS, [])), tuple(int(x) for x in (srcs or ())), tuple(obj.get(KEY_LIMB_BONE_COUNTS, [])),
           tuple(obj.get(KEY_LIMB_BONES, [])))     # every owner / patch-limb write is a new generation (begin_edit)
    hit = _EXPORT_STUB_CACHE.get(obj.name)
    if hit is None or hit[0] != key:
        try:
            from . import core, structure
        except ImportError:              # imported top-level (pure tests)
            import core  # type: ignore[no-redef]
            import structure  # type: ignore[no-redef]
        src = core.read_source(source_path(obj))
        pl = patch_limb(obj) if obj.get(KEY_PATCH_LIMB) is not None else None
        vb = vert_bone(obj) if has_vert_bone(obj) else None
        hit = (key, frozenset(int(i) for i in structure.export_stub_bones(src, limb_rows(obj), pl, vb)))
        _EXPORT_STUB_CACHE[obj.name] = hit
    return hit[1]


def new_bone_keys(obj) -> dict:
    """{rig bone name: owner key} of the new-bone table's bones on the rig (by the add rule's identity: a copy of an
    added bone is not it; a bone under a stub bone of the export -- :func:`export_stubs` -- is none); empty without a rig
    or table. Raises :class:`AddedBoneCheckError` when the rule can't run (the rig's stamps, the stub bones)."""
    uids = new_bone_uids(obj)
    arm = rig(obj)
    if not uids or arm is None:
        return {}
    try:
        from . import rig as _rig
    except ImportError as e:             # imported top-level (pure tests): the rule needs the add-on's rig module
        raise AddedBoneCheckError(f"added bone check failed: the rig module can't be imported ({e})") from e
    try:
        stubs = export_stubs(obj)
        sk = _rig.rig_skeleton(arm, src_stubs=stubs)
        if sk is None:
            return {}
        rows, _bad = _rig.classify_added(arm, sk.resolution, sk.stubs, sk.stub_bad, uids, src_stubs=stubs)
    except Exception as e:
        raise AddedBoneCheckError(f"added bone check failed: {type(e).__name__}: {e}") from e
    n = len(bone_names(obj))
    return {ra.name: int(key) for ra, key, _pk, _d in rows if n <= int(key) < n + len(uids)}


def _added_keys(obj) -> dict:
    """:func:`new_bone_keys` for the read-outs that must not raise (panel draw, By Bone, the vertex-group reader): empty
    when the check fails, which :func:`added_bone_check_error` says wherever an added bone is used."""
    try:
        return new_bone_keys(obj)
    except AddedBoneCheckError:
        return {}


def added_bone_check_error(obj):
    """Why the added bones of ``obj`` have no owner keys because the add rule can't run, or None (it runs)."""
    try:
        new_bone_keys(obj)
    except AddedBoneCheckError as e:
        return str(e)
    return None


def bone_index(obj, name) -> int:
    """The owner-table index of the rig bone called ``name``: a source bone's import stamp (``bz_src_index``, which
    survives a rename), an added bone's key in the new-bone table (``n_src + j``), else the stored bone names; -1 when
    the creature has no such bone (an added bone not in the table yet, too, or while :func:`added_bone_check_error`)."""
    if not name:
        return -1
    names = bone_names(obj)
    arm = rig(obj)
    bone = arm.data.bones.get(str(name)) if arm is not None else None
    if bone is not None:
        sid = bone.get("bz_src_index")
        if sid is not None and 0 <= int(sid) < len(names):
            return int(sid)
        if bone.get("bz_uid") is not None:
            key = _added_keys(obj).get(bone.name)
            if key is not None:
                return int(key)
    try:
        return names.index(str(name))
    except ValueError:
        return -1


def bone_rig_names(obj) -> list:
    """The rig's current name of every owner key: the source bones (by identity: a renamed bone under its new name, a
    copy -- Duplicate / Symmetrize copy the stamp -- never taken for its original; where the rig can't say, the stored
    name), then the new-bone table's bones ("" for an entry whose bone is not on the rig: its points show orange)."""
    names = bone_names(obj)
    arm = rig(obj)
    if arm is None or not names:
        return names
    try:
        from . import rig as _rig
    except ImportError:                  # imported top-level (pure tests)
        return names
    try:
        sk = _rig.rig_skeleton(arm)
    except Exception:
        sk = None
    if sk is None or len(sk.resolution.current) != len(names):
        return names + [""] * len(new_bone_uids(obj))
    out = [str(c) if c is not None else names[i] for i, c in enumerate(sk.resolution.current)]
    uids = new_bone_uids(obj)
    if uids:
        by_key = {k: nm for nm, k in _added_keys(obj).items()}
        out += [by_key.get(len(names) + j, "") for j in range(len(uids))]
    return out


def owner_count(obj) -> int:
    """How many owner keys the skin table can name: the source bones and the new-bone table's entries."""
    return len(bone_names(obj)) + len(new_bone_uids(obj))


def bone_display_name(obj, index) -> str:
    """The rig's current name of source bone ``index`` (by identity, :func:`bone_rig_names`), else the stored name."""
    names = bone_rig_names(obj)
    return names[index] if 0 <= int(index) < len(names) else f"bone{index}"


def patch_mat(obj) -> np.ndarray:
    return np.array(obj.get(KEY_PATCH_MAT, []), dtype=np.int64)


def set_patch_mat(obj, arr) -> None:
    _set(obj, KEY_PATCH_MAT, [int(x) for x in arr])


def material_slots(obj):
    """(slot_names, tex_image_names, colors) as stored; tex name '' means flat colour."""
    names = list(obj.get(KEY_SLOTS, []))
    tex = list(obj.get(KEY_MAT_TEX, []))
    col = np.array(obj.get(KEY_MAT_COL, []), dtype=np.float64).reshape(-1, 3)
    return names, tex, col


# --- limbs & connections --------------------------------------------------------------
def _i32(x) -> int:
    """A u32 record word as an ID-property int (32-bit signed)."""
    x = int(x) & 0xFFFFFFFF
    return x - (1 << 32) if x >= (1 << 31) else x


def set_limbs(obj, model, npatches) -> None:
    patch_limb = [-1] * npatches
    for li, limb in enumerate(model.limbs):
        for p in limb.prims:
            if 0 <= int(p) < npatches:
                patch_limb[int(p)] = li
    _set(obj, KEY_PATCH_LIMB, patch_limb)
    n = len(model.limbs)
    _set(obj, KEY_LIMB_NAMES, [str(l.name) for l in model.limbs])
    _set(obj, KEY_LIMB_IDS, [_i32(l.limb_id) for l in model.limbs])
    _set(obj, KEY_LIMB_SRC, list(range(n)))
    _set(obj, KEY_LIMB_BONES, [])
    _set(obj, KEY_LIMB_BONE_COUNTS, [-1] * n)
    _set(obj, KEY_CONN_FLAGS1, [_i32(c.flags1) for c in model.limb_connections])
    _set(obj, KEY_CONN_FLAGS2, [_i32(c.flags2) for c in model.limb_connections])
    _set(obj, KEY_CONN_SRC, list(range(len(model.limb_connections))))
    rings = [decode_ring(c.vertex_indices) for c in model.limb_connections]
    set_connections(obj, [(c.name, c.bone_name or "", rings[i][0])
                          for i, c in enumerate(model.limb_connections)], enc=[e for _r, e in rings])


def patch_limb(obj) -> np.ndarray:
    return np.array(obj.get(KEY_PATCH_LIMB, []), dtype=np.int64)


def set_patch_limb(obj, arr) -> None:
    _set(obj, KEY_PATCH_LIMB, [int(x) for x in arr])


def decode_ring(ints):
    """(ring, enc) of a connection's stored ints: its corner loop and 1 when they are the closed edge pairs every stock
    ring uses (``ICParse ring_corners``: re-encoding gives the same ints), else the ints themselves and 0 (verbatim)."""
    from ICParse.sgm.creature.model import ring_corners
    vi = [int(v) for v in ints]
    c = ring_corners(vi)
    return (c, 1) if c is not None else (vi, 0)


def _ring_rows(obj):
    """[(name, bone, ring, enc)] as stored. A store written before v2 holds raw ints: decoded here on read."""
    names = list(obj.get(KEY_CONN_NAMES, []))
    bones = list(obj.get(KEY_CONN_BONES, []))
    verts = list(obj.get(KEY_CONN_VERTS, []))
    counts = list(obj.get(KEY_CONN_COUNTS, []))
    v2 = int(obj.get(KEY_STORE_VERSION, 0)) >= STORE_VERSION
    encs = list(obj.get(KEY_CONN_ENC, [])) if v2 else []
    out, off = [], 0
    for i, n in enumerate(counts):
        vs = [int(v) for v in verts[off:off + n]]
        off += n
        if v2:
            enc = int(encs[i]) if i < len(encs) else 0          # a short enc array is reported by ring_store_error
        else:
            vs, enc = decode_ring(vs)
        out.append((names[i] if i < len(names) else f"conn{i}", bones[i] if i < len(bones) else "", vs, enc))
    return out


def ring_store_error(obj):
    """Why the stored rings can't be exported as they are (None = consistent)."""
    counts = obj.get(KEY_CONN_COUNTS)
    if counts is None:
        return None
    n = len(counts)
    verts = obj.get(KEY_CONN_VERTS)
    if verts is None or len(verts) != sum(int(c) for c in counts):
        return (f"the connection rings table holds {0 if verts is None else len(verts)} indices for ring sizes summing to "
                f"{sum(int(c) for c in counts)}: re-import the creature")
    if int(obj.get(KEY_STORE_VERSION, 0)) >= STORE_VERSION:
        enc = obj.get(KEY_CONN_ENC)
        if enc is None or len(enc) != n:
            return (f"the ring encoding table has {0 if enc is None else len(enc)} entries for {n} connections, so it is "
                    "unknown which rings are corner loops: re-import the creature")
    for k in (KEY_CONN_NAMES, KEY_CONN_BONES, KEY_CONN_FLAGS1, KEY_CONN_FLAGS2, KEY_CONN_SRC):
        v = obj.get(k)
        if v is not None and len(v) != n:
            return f"'{k}' has {len(v)} entries for {n} connections: re-import the creature"
    if obj.get(KEY_CONN_SRC) is not None and (obj.get(KEY_CONN_FLAGS1) is None or obj.get(KEY_CONN_FLAGS2) is None
                                               or obj.get(KEY_CONN_NAMES) is None):
        return "the connection table has source identities but no names or classes: re-import the creature"
    return None


def set_connections(obj, conns, enc=None) -> None:
    """conns: (name, bone, ring) per connection, ``ring`` a corner loop (encoded as closed edge pairs at export) or,
    for ``enc`` 0, ints kept verbatim. ``enc`` None: a ring equal to the one stored at that index keeps its encoding
    (an untouched verbatim ring stays verbatim), any other ring is a corner loop. Always writes the whole table with
    the store version and the encoding of every ring, so no reader can mistake one form for the other."""
    conns = [(str(n), str(b), [int(v) for v in vs]) for n, b, vs in conns]
    if enc is None:
        old = _ring_rows(obj) if obj.get(KEY_CONN_COUNTS) is not None else []
        enc = [old[i][3] if i < len(old) and vs == old[i][2] else 1 for i, (_n, _b, vs) in enumerate(conns)]
    enc = [int(e) for e in enc]
    if len(enc) != len(conns):
        raise ValueError(f"{len(enc)} ring encodings for {len(conns)} connections")
    _set(obj, KEY_CONN_NAMES, [n for n, _, _ in conns])
    _set(obj, KEY_CONN_BONES, [b for _, b, _ in conns])
    _set(obj, KEY_CONN_VERTS, [v for _, _, vs in conns for v in vs])
    _set(obj, KEY_CONN_COUNTS, [len(vs) for _, _, vs in conns])
    _set(obj, KEY_CONN_ENC, enc)
    _set(obj, KEY_STORE_VERSION, STORE_VERSION)


def limb_names(obj):
    return list(obj.get(KEY_LIMB_NAMES, []))


def _limb_source_ids(obj, n):
    """The source's limb ids when the object predates stored ids (None when the source can't give ``n`` of them)."""
    try:
        from . import core
        m = core.read_source(source_path(obj))
    except Exception:
        return None
    return [_i32(l.limb_id) for l in m.limbs] if len(m.limbs) == n else None


def upgrade_limb_tables(obj) -> bool:
    """Bring an object stored before the limb rows up to date: every limb comes from its source record (identity
    ``arange``, bone lists "the source's"), and EVERY journal entry (the object's and the session copy) lacking a limb
    table key gets the value it had then -- before this upgrade no operator could change a limb's name, id or bones, so
    the current values are the values of every earlier generation. Without it, an Undo into such a generation would
    select tables without limb names and drop them. Returns True when it wrote anything."""
    names = obj.get(KEY_LIMB_NAMES)
    if names is None or obj.get(KEY_LIMB_SRC) is not None:
        return False
    names = [str(x) for x in names]
    n = len(names)
    ids = obj.get(KEY_LIMB_IDS)
    ids = [int(x) for x in ids] if ids is not None and len(ids) == n else _limb_source_ids(obj, n)
    if ids is None:
        return False                     # rows can't be formed: readers keep the source's limbs (limb_rows is None)
    fill = {KEY_LIMB_NAMES: names, KEY_LIMB_IDS: ids, KEY_LIMB_SRC: list(range(n)), KEY_LIMB_BONES: [],
            KEY_LIMB_BONE_COUNTS: [-1] * n}
    for k, v in fill.items():
        obj[k] = v
    j = obj.get(KEY_JOURNAL)
    if j is not None:
        for g in list(j.keys()):
            entry = j[g]
            for k, v in fill.items():
                if k not in entry:
                    entry[k] = v
    me = _identity(obj)
    for (ident, _g), entry in _JOURNAL_CACHE.items():
        if ident == me:
            for k, v in fill.items():
                entry.setdefault(k, _copy_table(v))
    return True


def limb_store_error(obj):
    """Why the stored limb table can't be exported as it is (None = consistent, or no table: the source's)."""
    names = obj.get(KEY_LIMB_NAMES)
    if names is None or obj.get(KEY_LIMB_SRC) is None:
        return None
    n = len(names)
    for k in (KEY_LIMB_IDS, KEY_LIMB_SRC, KEY_LIMB_BONE_COUNTS):
        v = obj.get(k)
        if v is None or len(v) != n:
            return f"'{k}' has {0 if v is None else len(v)} entries for {n} limbs: re-import the creature"
    want = sum(max(int(c), 0) for c in obj[KEY_LIMB_BONE_COUNTS])
    have = len(obj.get(KEY_LIMB_BONES, []))
    if want != have:
        return f"the limb bone table holds {have} names for counts summing to {want}: re-import the creature"
    return None


def limb_rows(obj):
    """The limb table as ``structure.LimbRow`` values (row order = export order; ``patch_limb`` indexes it), or None when
    the object stores no limb table (its source's limbs are exported). Raises ValueError when the table is inconsistent
    (see :func:`limb_store_error`)."""
    try:
        from . import structure
    except ImportError:                  # imported top-level (pure tests put ic_blender/ on sys.path)
        import structure  # type: ignore[no-redef]
    if obj.get(KEY_LIMB_NAMES) is None:
        return None
    if obj.get(KEY_LIMB_SRC) is None:                  # not upgraded yet (read-only callers): what the upgrade gives
        names, ids = [str(x) for x in obj[KEY_LIMB_NAMES]], limb_ids(obj)
        if ids is None or len(ids) != len(names):
            return None
        return [structure.LimbRow(i, n, ids[i], None) for i, n in enumerate(names)]
    err = limb_store_error(obj)
    if err:
        raise ValueError(err)
    names = [str(x) for x in obj[KEY_LIMB_NAMES]]
    ids = limb_ids(obj)
    srcs = [int(x) for x in obj[KEY_LIMB_SRC]]
    flat = [str(x) for x in obj.get(KEY_LIMB_BONES, [])]
    rows, off = [], 0
    for i, c in enumerate(int(x) for x in obj[KEY_LIMB_BONE_COUNTS]):
        bones = None
        if c >= 0:
            bones = tuple(flat[off:off + c])
            off += c
        rows.append(structure.LimbRow(srcs[i], names[i], ids[i], bones))
    return rows


def set_limb_rows(obj, rows, patch_limb=None) -> None:
    """Write the whole limb table (and, when given, the patch membership that indexes it) through the journal."""
    rows = list(rows)
    _set(obj, KEY_LIMB_NAMES, [str(r.name) for r in rows])
    _set(obj, KEY_LIMB_IDS, [_i32(r.limb_id) for r in rows])
    _set(obj, KEY_LIMB_SRC, [int(r.src) for r in rows])
    _set(obj, KEY_LIMB_BONES, [str(b) for r in rows if r.bones is not None for b in r.bones])
    _set(obj, KEY_LIMB_BONE_COUNTS, [-1 if r.bones is None else len(r.bones) for r in rows])
    if patch_limb is not None:
        set_patch_limb(obj, patch_limb)


def limb_pins(obj) -> list:
    """The names of the added bones a Boner limb operator put in a limb by hand (``KEY_LIMB_PINS``): those stay in the
    row that lists them instead of following their parent (``structure.added_limb_rows``). Empty for every creature that
    never had one, which is every creature before fit-to-rig."""
    return [str(x) for x in (obj.get(KEY_LIMB_PINS) or ())]


def set_limb_pins(obj, names) -> None:
    """Write the pinned added-bone names through the journal (empty clears the key's meaning, never the key)."""
    seen, out = set(), []
    for n in names:
        k = str(n).lower()
        if n and k not in seen:
            seen.add(k)
            out.append(str(n))
    _set(obj, KEY_LIMB_PINS, out)


def limb_ids(obj):
    """limb_id per limb, or None when the object predates the stored ids (read them from the source)."""
    v = obj.get(KEY_LIMB_IDS)
    return None if v is None else [int(x) & 0xFFFFFFFF for x in v]


def conn_flags(obj):
    """(flags1 list, flags2 list) per connection, or None when the object predates them (read the source)."""
    f1, f2 = obj.get(KEY_CONN_FLAGS1), obj.get(KEY_CONN_FLAGS2)
    if f1 is None or f2 is None:
        return None
    return [int(x) & 0xFFFFFFFF for x in f1], [int(x) & 0xFFFFFFFF for x in f2]


def connections(obj):
    """List of (name, bone, ring) per connection: ``ring`` is the corner loop (or, for a non-standard ring kept
    verbatim, its ints: see :func:`conn_encodings`)."""
    return [(n, b, vs) for n, b, vs, _e in _ring_rows(obj)]


def conn_encodings(obj):
    """Per connection: 1 = corner loop, 0 = non-standard ints kept verbatim."""
    return [e for _n, _b, _v, e in _ring_rows(obj)]


def _conn_source_flags(obj, n):
    """(flags1, flags2) of the source's connections when the object predates stored flags (None when the source can't
    give ``n`` of them)."""
    try:
        from . import core
        m = core.read_source(source_path(obj))
    except Exception:
        return None
    if len(m.limb_connections) != n:
        return None
    return [_i32(c.flags1) for c in m.limb_connections], [_i32(c.flags2) for c in m.limb_connections]


def upgrade_conn_tables(obj) -> bool:
    """Bring an object stored before the connection rows up to date: every connection comes from its source record
    (identity ``arange``), its classes are stored (kept from import, else read from the source) and journaled, and
    EVERY journal entry (the object's and the session copy) that holds the connection table but lacks a connection
    row key gets the value it had then -- before this upgrade no operator could add, delete or re-class a connection,
    so the current classes and identities are those of every earlier generation. Without it an Undo into such a
    generation would select tables without the classes and drop them. Returns True when it wrote anything."""
    counts = obj.get(KEY_CONN_COUNTS)
    if counts is None or obj.get(KEY_CONN_SRC) is not None:
        return False
    n = len(counts)
    f1, f2 = obj.get(KEY_CONN_FLAGS1), obj.get(KEY_CONN_FLAGS2)
    if f1 is not None and f2 is not None and len(f1) == n and len(f2) == n:
        f1, f2 = [int(x) for x in f1], [int(x) for x in f2]
    else:
        got = _conn_source_flags(obj, n)
        if got is None:
            return False                 # rows can't be formed: readers keep the source's classes (flags None)
        f1, f2 = got
    fill = {KEY_CONN_FLAGS1: f1, KEY_CONN_FLAGS2: f2, KEY_CONN_SRC: list(range(n))}
    for k, v in fill.items():
        obj[k] = v
    j = obj.get(KEY_JOURNAL)
    if j is not None:
        for g in list(j.keys()):
            entry = j[g]
            if KEY_CONN_COUNTS not in entry:
                continue
            for k, v in fill.items():
                if k not in entry:
                    entry[k] = v
    me = _identity(obj)
    for (ident, _g), entry in _JOURNAL_CACHE.items():
        if ident == me and KEY_CONN_COUNTS in entry:
            for k, v in fill.items():
                entry.setdefault(k, _copy_table(v))
    return True


def _core():
    try:
        from . import core
    except ImportError:                  # imported top-level (pure tests put ic_blender/ on sys.path)
        import core  # type: ignore[no-redef]
    return core


def conn_rows(obj):
    """The connection table as ``core.RingEdit`` rows (row order = export order: ``src`` identity, name, Parent / Child
    classes, host bone, and the corner loop or verbatim ints), or None when the object stores no connection table (its
    source's are exported). An object not upgraded yet (read-only callers) reads as its upgrade would give it; without
    stored classes ``flags1`` / ``flags2`` are None (the source's). Raises ValueError when the table is inconsistent
    (see :func:`ring_store_error`)."""
    core = _core()
    if obj.get(KEY_CONN_COUNTS) is None:
        return None
    err = ring_store_error(obj)
    if err:
        raise ValueError(err)
    rows = _ring_rows(obj)
    n = len(rows)
    flags = conn_flags(obj)
    if flags is not None and (len(flags[0]) != n or len(flags[1]) != n):
        flags = None
    srcs = obj.get(KEY_CONN_SRC)
    srcs = [int(x) for x in srcs] if srcs is not None else list(range(n))
    out = []
    for i, (name, bone, ring, enc) in enumerate(rows):
        kw = dict(index=i, bone=bone, src=srcs[i], name=name if obj.get(KEY_CONN_NAMES) is not None else None,
                  flags1=flags[0][i] if flags else None, flags2=flags[1][i] if flags else None)
        out.append(core.RingEdit(corners=list(ring), **kw) if enc else core.RingEdit(raw=list(ring), **kw))
    return out


def set_conn_rows(obj, rows) -> None:
    """Write the whole connection table (names, classes, identities, hosts, rings with their encodings) through the
    journal. Every row needs its name and both classes (read rows with :func:`conn_rows` after ``begin_edit``, which
    upgrades the object)."""
    rows = list(rows)
    for i, r in enumerate(rows):
        if r.name is None or r.flags1 is None or r.flags2 is None:
            raise ValueError(f"connection row {i} has no name or classification to store")
    _set(obj, KEY_CONN_NAMES, [str(r.name) for r in rows])
    _set(obj, KEY_CONN_BONES, [str(r.bone) for r in rows])
    _set(obj, KEY_CONN_VERTS, [int(v) for r in rows for v in (r.corners if r.raw is None else r.raw) or []])
    _set(obj, KEY_CONN_COUNTS, [len((r.corners if r.raw is None else r.raw) or []) for r in rows])
    _set(obj, KEY_CONN_ENC, [0 if r.raw is not None else 1 for r in rows])
    _set(obj, KEY_CONN_FLAGS1, [_i32(r.flags1) for r in rows])
    _set(obj, KEY_CONN_FLAGS2, [_i32(r.flags2) for r in rows])
    _set(obj, KEY_CONN_SRC, [int(r.source()) for r in rows])
    _set(obj, KEY_STORE_VERSION, STORE_VERSION)


def ring_edits(obj):
    """The connection rows (:func:`conn_rows`): what Export writes."""
    return conn_rows(obj)


def ik_rows(obj):
    """The IK chain table as ``topology.IKRow`` values (export order), or None when the object stores none (the
    source's IKCH chunk is exported as it is). Raises ValueError when the table is inconsistent."""
    try:
        from . import topology
    except ImportError:                  # imported top-level (pure tests)
        import topology  # type: ignore[no-redef]
    names = obj.get(KEY_IK_NAMES)
    if names is None:
        return None
    names = [str(x) for x in names]
    n = len(names)
    srcs, flags, enabled = obj.get(KEY_IK_SRC), obj.get(KEY_IK_FLAGS), obj.get(KEY_IK_ENABLED)
    counts, flat = obj.get(KEY_IK_COUNTS), obj.get(KEY_IK_BONES)
    if any(v is None or len(v) != n for v in (srcs, flags, enabled, counts)) or flat is None:
        raise ValueError(f"the IK chain table is inconsistent ({n} names): Undo, or re-import the creature")
    flat = [str(x) for x in flat]
    if sum(int(c) for c in counts) != len(flat):
        raise ValueError("the IK chain table's bone lists do not add up: Undo, or re-import the creature")
    rows, off = [], 0
    for i in range(n):
        c = int(counts[i])
        rows.append(topology.IKRow(int(srcs[i]), names[i], int(flags[i]) & 0xFFFF, int(enabled[i]) & 0xFFFF,
                                   tuple(flat[off:off + c])))
        off += c
    return rows


def set_ik_rows(obj, rows) -> None:
    """Write the whole IK chain table through the journal (``rows``: ``topology.IKRow``; None removes the table, so
    the source's chunk is exported again)."""
    if rows is None:
        for k in IK_TABLE_KEYS:
            if k in obj:
                del obj[k]
        g = obj.get(KEY_GEN)
        j = obj.get(KEY_JOURNAL)
        if g is not None and j is not None and str(g) in j:
            for k in IK_TABLE_KEYS:
                if k in j[str(g)]:
                    del j[str(g)][k]
        hit = _cache_get(obj, g) if g is not None else None
        if hit is not None:
            for k in IK_TABLE_KEYS:
                hit.pop(k, None)
        return
    rows = list(rows)
    _set(obj, KEY_IK_NAMES, [str(r.name) for r in rows])
    _set(obj, KEY_IK_SRC, [int(r.src) for r in rows])
    _set(obj, KEY_IK_FLAGS, [int(r.flags) for r in rows])
    _set(obj, KEY_IK_ENABLED, [int(r.enabled) for r in rows])
    _set(obj, KEY_IK_BONES, [str(b) for r in rows for b in r.bones])
    _set(obj, KEY_IK_COUNTS, [len(r.bones) for r in rows])


def is_native(obj) -> bool:
    return bool(obj and obj.type == "MESH" and obj.get(MARK))


def creature_of(obj):
    """The creature (the mesh the add-on made, not a scene object) that ``obj`` is, or the one whose rig ``obj`` is; None for anything else.
    The panels that act on a creature (Check, Export, Creature Stats) work while the rig is the active object, as the Motions box does."""
    if obj is None:
        return None
    if is_native(obj) and not obj.get("bz_scene"):
        return obj
    if obj.type == "ARMATURE":
        import bpy
        owners = [o for o in bpy.data.objects if is_native(o) and not o.get("bz_scene") and rig(o) == obj]
        if len(owners) == 1:
            return owners[0]
    return None


def active_creature(context):
    """:func:`creature_of` the active object of ``context``."""
    return creature_of(getattr(context, "active_object", None))


def source_path(obj) -> str:
    return str(obj.get(KEY_SOURCE, ""))


def patches(obj) -> np.ndarray:
    """(Np, 16) int patch index array stored on the object."""
    flat = obj.get(KEY_PATCHES, [])
    n = int(obj.get(KEY_NPATCH, 0))
    if not n or len(flat) != 16 * n:
        return np.zeros((0, 16), dtype=np.int64)
    return np.array(flat, dtype=np.int64).reshape(n, 16)


def set_patches(obj, p) -> None:
    p = np.asarray(p, dtype=np.int64)
    _set(obj, KEY_PATCHES, [int(i) for i in p.reshape(-1)])
    _set(obj, KEY_NPATCH, int(len(p)))


def set_pool_n(obj, n) -> None:
    _set(obj, KEY_POOL_N, int(n))


def patch_type(obj) -> np.ndarray:
    return np.array(obj.get(KEY_PATCH_TYPE, []), dtype=np.int64)


def set_patch_type(obj, arr) -> None:
    _set(obj, KEY_PATCH_TYPE, [int(x) for x in arr])


def manual_interior(obj, npatch=None) -> np.ndarray:
    """(Np,) bool: the patches whose interior points are hand-placed (the Auto Interior rule leaves them). Absent or
    short (a patch added since) reads as automatic."""
    n = int(obj.get(KEY_NPATCH, 0)) if npatch is None else int(npatch)
    out = np.zeros(n, dtype=bool)
    v = obj.get(KEY_MANUAL_INTERIOR)
    if v is not None and n:
        a = np.asarray(list(v), dtype=np.int64)[:n]
        out[:len(a)] = a != 0
    return out


def set_manual_interior(obj, arr) -> None:
    """Journaled like every per-patch table; an all-automatic table is stored empty (reads like the import state)."""
    a = np.asarray(arr, dtype=bool)
    _set(obj, KEY_MANUAL_INTERIOR, [int(x) for x in a] if a.any() else [])


def pool_n(obj) -> int:
    v = obj.get(KEY_POOL_N)
    return int(v) if v is not None else point_count(obj)


def grew(obj) -> bool:
    """True when SGM operators (Extrude Patch) added control points or patches since import. Native Blender topology
    tools do not count: they break the pool (see :func:`pool_error`)."""
    return (pool_n(obj) != int(obj.get(KEY_SRC_NVERTS, pool_n(obj)))
            or int(obj.get(KEY_NPATCH, 0)) != int(obj.get(KEY_SRC_NPATCH, int(obj.get(KEY_NPATCH, 0)))))
