"""Markers: the attach points of a gatherer, character, structure or prop (FORM MRKR in the scene .sgm), as Blender empties.

The game places an event's effect and sound at a marker the .ebp names (``marker``, ``fx_othermarker``), spreads "random" damage
fire over the markers and bones, and puts a marker that is missing at the model's origin (its feet). A marker rides on a bone of
the skeleton (it follows the animation) or sits in model space (no bone).

Import (``build_markers``, called by ``scene.build_scene(..., markers=True)``): one ARROWS empty per marker, in a child collection
"Markers", parented to its bone (parent_type BONE) on the rig, else to the rig / group empty. The empty's place is set from the rig's
REST matrices, so a clip that is playing does not shift it. When the parts have no rig but a marker names a bone, a rig of its own is
made for the markers (stamped ``bz_marker_rig``; the parts are not bound to it and its clips are not imported, so no rig export
path sees it). A single part with no rig and no group empty carries the markers itself (they move with it in Blender; the
export reads them relative to it, as it ignores the part's own transform).

Export (``apply_to_export``, called by ``scene.export_scene_object``): every marker empty of the entity is read back at REST pose,
relative to the entity's root, and turned into the file's bone-relative matrix with the file's own bind matrices; a marker within
1e-5 of its source keeps the source's floats, an untouched set gives the source bytes (ICParse ``write_scene_markers``). Renamed
markers are followed into the .ebp's events by Export Entity Folder (``follow_renames_in_ebp``).

Names: the marker's name is the custom property ``bz_marker_name`` (Blender object names are unique and at most 63 bytes; a game
name is up to 126 bytes: the NAME chunk, NUL included, must be below 128). A name starting with '0' is refused: the game's
lookup (``RenderModel::GetMarkerIndex``) never finds one. Renaming the empty in the Outliner renames the marker too.

Hooks for the skeleton work: ``rename_marker_bone(arm, old, new)`` re-points markers from one bone to another keeping their place,
``markers_off_bone(arm, bone, to)`` moves a deleted bone's markers to another bone (or model space). Both are also reached through
``refs.rename("bone", old, new)`` (this module's slice follows it).
"""
from __future__ import annotations

import os

import numpy as np

import bpy
from mathutils import Matrix

from . import model as SO
from .. import core as _core
from .. import rig as _rig
from .. import store as _store

ORDER = 50

KEY_MARKER = "bz_marker"                  # truthy on a marker empty
KEY_INDEX = "bz_marker_index"             # its marker number in the source file; -1 = added in Blender
KEY_NAME = "bz_marker_name"               # the marker's name in the game (up to 126 bytes)
KEY_OBJ_NAME = "bz_marker_obj_name"       # the object's name when the add-on last named it (a different name = renamed in Blender)
KEY_SRC_NAME = "bz_marker_src_name"       # the name in the source file
KEY_SRC_BONE = "bz_marker_src_bone"       # the bone in the source file, as spelled there ("" = model space)
KEY_ENTITY = "bz_marker_entity_rig"       # the parts' bz_rig ("" when the parts have none): which import the marker belongs to
KEY_BUILT = "bz_markers_built"            # on each part: the add-on built this import's markers (absent: export keeps the file's)
KEY_MARKER_RIG = "bz_marker_rig"          # on a rig made only to carry markers
COLLECTION = "Markers"
DEFAULT_NAME = "Point%02d"


# ------------------------------------------------------------------------------------------------------------ small helpers
def is_marker(obj) -> bool:
    return obj is not None and obj.type == "EMPTY" and bool(obj.get(KEY_MARKER))


def marker_name(obj) -> str:
    """The game name of a marker empty: ``bz_marker_name``, or the object's name when it was renamed in Blender."""
    if obj.get(KEY_OBJ_NAME) is not None and obj.name != obj.get(KEY_OBJ_NAME):
        return obj.name
    return str(obj.get(KEY_NAME) or obj.name)


def set_marker_name(obj, name: str) -> None:
    obj[KEY_NAME] = name
    obj.name = name[:63]
    obj[KEY_OBJ_NAME] = obj.name


def entity_key(obj):
    """(source path, parts' rig name or "") of the import ``obj`` belongs to (a part, its rig, its group empty or a marker), or None."""
    if obj is None:
        return None
    if is_marker(obj):
        return obj.get("bz_source", ""), str(obj.get(KEY_ENTITY) or "")
    if obj.type == "MESH" and obj.get("bz_scene") and obj.get("bz_source"):
        return obj.get("bz_source", ""), str(obj.get("bz_rig") or "")
    if obj.type == "ARMATURE":
        part = next((o for o in bpy.data.objects if o.type == "MESH" and o.get("bz_scene") and o.get("bz_rig") == obj.name), None)
        if part is not None:
            return part.get("bz_source", ""), obj.name
        mk = next((o for o in obj.children if is_marker(o)), None)
        return entity_key(mk) if mk is not None else None
    if obj.type == "EMPTY":
        part = next((c for c in obj.children if c.type == "MESH" and c.get("bz_scene")), None)
        return entity_key(part) if part is not None else None
    return None


def parts_of(key) -> list:
    src, rig_name = key
    return [o for o in bpy.data.objects if o.type == "MESH" and o.get("bz_scene") and o.get("bz_source") == src
            and str(o.get("bz_rig") or "") == rig_name]


def markers_of(key) -> list:
    """Every marker empty of the import ``key``, by marker number (new ones last, by name)."""
    src, rig_name = key
    found = [o for o in bpy.data.objects if is_marker(o) and o.get("bz_source") == src and str(o.get(KEY_ENTITY) or "") == rig_name]
    return sorted(found, key=lambda o: (int(o.get(KEY_INDEX, -1)) < 0, int(o.get(KEY_INDEX, -1)), marker_name(o)))


def managed(key) -> bool:
    """Whether this import's markers are the add-on's to write (it built them): an object imported before markers existed keeps the
    file's markers on export."""
    return any(p.get(KEY_BUILT) is not None for p in parts_of(key))


def entity_root(key):
    """The object the entity's markers are placed relative to: the parts' rig, else their group empty, else the part when there is
    only one, else None (the world)."""
    parts = parts_of(key)
    if key[1]:
        arm = bpy.data.objects.get(key[1])
        if arm is not None:
            return arm
    if parts and parts[0].parent is not None and parts[0].parent.type == "EMPTY" and not is_marker(parts[0].parent):
        return parts[0].parent
    if len(parts) == 1 and not key[1]:
        return parts[0]
    return None


def marker_rig(key):
    """The rig markers of ``key`` ride on: the parts' rig, else a markers-only rig made at import, else None."""
    root = bpy.data.objects.get(key[1]) if key[1] else None
    if root is not None and root.type == "ARMATURE":
        return root
    for o in markers_of(key):
        if o.parent is not None and o.parent.type == "ARMATURE":
            return o.parent
    src = key[0]
    return next((o for o in bpy.data.objects if o.type == "ARMATURE" and o.get(KEY_MARKER_RIG) and o.get("bz_source") == src
                 and str(o.get(KEY_ENTITY) or "") == key[1]), None)


def _rest_tail(arm, bone_name) -> Matrix:
    """Armature-space rest matrix an object parented to ``bone_name`` hangs from (Blender parents to the bone's tail)."""
    b = arm.data.bones[bone_name]
    return b.matrix_local @ Matrix.Translation((0.0, b.length, 0.0))


def _mw(obj) -> Matrix:
    """World matrix of ``obj`` worked out from its parents (no depsgraph needed; bones at REST)."""
    local = obj.matrix_parent_inverse @ obj.matrix_basis
    p = obj.parent
    if p is None:
        return obj.matrix_basis.copy()
    if p.type == "ARMATURE" and obj.parent_type == "BONE" and obj.parent_bone in p.data.bones:
        return _mw(p) @ _rest_tail(p, obj.parent_bone) @ local
    return _mw(p) @ local


def rest_world(obj, root) -> Matrix:
    """The marker's matrix relative to ``root`` (None = the world), with every bone at rest."""
    w = _mw(obj)
    return _mw(root).inverted() @ w if root is not None else w


def place(obj, world_rel: Matrix, root) -> None:
    """Set ``obj``'s basis so that its rest-pose matrix relative to ``root`` is ``world_rel`` (whatever it is parented to)."""
    target = (_mw(root) @ world_rel) if root is not None else world_rel
    p = obj.parent
    if p is None:
        obj.matrix_basis = target
        return
    if p.type == "ARMATURE" and obj.parent_type == "BONE" and obj.parent_bone in p.data.bones:
        hang = _mw(p) @ _rest_tail(p, obj.parent_bone)
    else:
        hang = _mw(p)
    obj.matrix_basis = (hang @ obj.matrix_parent_inverse).inverted() @ target


def _marker_size(data) -> float:
    pts = [p.verts for p in data.parts if len(p.verts)]
    if not pts:
        return 0.1
    a = np.concatenate(pts)
    return float(np.clip(0.02 * np.linalg.norm(a.max(0) - a.min(0)), 0.05, 0.5))


def _collection(context):
    parent = context.collection
    coll = bpy.data.collections.new(COLLECTION)
    parent.children.link(coll)
    return coll


def _bl_bone_names(arm, data) -> list:
    names = list(arm.get(_rig.KEY_BONE_SRC_NAMES, [])) if arm is not None else []
    if len(names) == len(data.bone_names):
        return [str(n) for n in names]
    return [n or f"bone{i}" for i, n in enumerate(data.bone_names)]


def needs_rig(data) -> bool:
    return any(m["bone_index"] is not None for m in getattr(data, "markers", ()) or ())


def make_marker_rig(context, data, src, group=None):
    """A rig of its own for markers on bones of a model whose parts have none (not bound to the parts, no clips)."""
    arm = _rig.create_armature(context, data.name + "_markers", data.bone_names, data.bone_parents, data.bind_world)
    arm[KEY_MARKER_RIG] = True
    _store.stamp_axes(arm)
    arm["bz_source"] = src
    arm[KEY_ENTITY] = ""
    if group is not None:
        arm.parent = group
    return arm


# ------------------------------------------------------------------------------------------------------------ import
def build_markers(context, data, src, arm=None, group=None, parts=()) -> list:
    """One empty per marker of ``data`` (a ``core.SceneData``) for the import whose parts are ``parts``. ``arm``: the parts' rig
    (None: a markers-only rig is made when a marker is on a bone); ``group``: their group empty. Returns the empties."""
    markers = list(getattr(data, "markers", ()) or ())
    entity_rig = str(parts[0].get("bz_rig") or "") if parts else ""
    for p in parts:
        p[KEY_BUILT] = len(markers)
    if not markers:
        return []
    # what carries the markers when there is no rig: the group empty, else the one part (so they move with the model)
    holder = group if group is not None else (parts[0] if arm is None and len(parts) == 1 else None)
    marm = arm
    if marm is None and needs_rig(data):
        marm = make_marker_rig(context, data, src, holder)
    names_bl = _bl_bone_names(marm, data)
    coll = _collection(context)
    size = _marker_size(data)
    out = []
    for m in markers:
        e = bpy.data.objects.new(m["name"][:63] or "marker", None)
        coll.objects.link(e)
        e.empty_display_type = "ARROWS"
        e.empty_display_size = size
        e.show_name = True
        e[KEY_MARKER] = True
        _store.stamp_axes(e)
        e[KEY_INDEX] = int(m["index"])
        e[KEY_NAME] = m["name"]
        e[KEY_OBJ_NAME] = e.name
        e[KEY_SRC_NAME] = m["name"]
        e[KEY_SRC_BONE] = m["bone"]
        e[KEY_ENTITY] = entity_rig
        e["bz_source"] = src
        e["bz_name"] = data.name
        bi = m["bone_index"]
        if bi is not None and marm is not None and names_bl[bi] in marm.data.bones:
            e.parent, e.parent_type, e.parent_bone = marm, "BONE", names_bl[bi]
        elif marm is not None:
            e.parent = marm
        elif holder is not None:
            e.parent = holder
        e.matrix_parent_inverse = Matrix.Identity(4)
        root = arm if arm is not None else holder
        place(e, _rig.to_blender_matrix(m["world"]), root)
        out.append(e)
    return out


# ------------------------------------------------------------------------------------------------------------ export
def written_binds(raw) -> dict:
    """{lower-case bone name: SGM-space 4x4 bind world} of the skeleton in ``raw``, computed as ``core.load_scene`` does: the
    binds the export writes once the skeleton step (ICParse ``skeleton.write_skeleton``) has run."""
    from ICParse.codecs import pose as ICpose
    from ICParse.sgm.scene.model import read_scene
    sc = read_scene(bytes(raw))
    if not sc.bones:
        return {}
    out = {}
    for b, w in zip(sc.bones, ICpose.SceneRig(sc.bones).bind_world):
        out.setdefault(b.name.lower(), np.asarray(w, dtype=np.float64))
    return out


def dangling(obj) -> bool:
    """Whether the empty hangs from a bone its rig no longer has (deleted with plain X in Edit Mode)."""
    p = obj.parent
    return (p is not None and p.type == "ARMATURE" and obj.parent_type == "BONE" and bool(obj.parent_bone)
            and obj.parent_bone not in p.data.bones)


def _file_bone(obj, data) -> str:
    """The FILE's name of the bone the empty hangs from ("" for model space or a bone made in Blender)."""
    p = obj.parent
    if not (p is not None and p.type == "ARMATURE" and obj.parent_type == "BONE" and obj.parent_bone in p.data.bones):
        return ""
    srci = p.data.bones[obj.parent_bone].get(_rig.KEY_BONE_SRC_INDEX)
    if srci is None or not 0 <= int(srci) < len(data.bone_names):
        return ""
    return data.bone_names[int(srci)] or ""


def _bone_of(obj, data, written=None):
    """(the bone name to write, its bind matrix in SGM space or None) of a marker empty. ``written`` (:func:`written_binds`)
    gives the binds of the skeleton the export writes: a bone moved or made in Blender is measured against its written bind,
    a bone left where it was against the file's (so an untouched marker keeps its floats)."""
    p = obj.parent
    if not (p is not None and p.type == "ARMATURE" and obj.parent_type == "BONE" and obj.parent_bone in p.data.bones):
        return "", None
    b = p.data.bones[obj.parent_bone]
    now = (written or {}).get(b.name.lower())
    srci = b.get(_rig.KEY_BONE_SRC_INDEX)
    if srci is not None and 0 <= int(srci) < len(data.bone_names) and int(srci) < len(data.bind_world):
        srci = int(srci)
        bind = np.asarray(data.bind_world[srci], dtype=np.float64)
        if now is not None and not np.allclose(now, bind, atol=1e-5):
            bind = now                                          # the bone was moved: its marker hangs from the new bind
        imported = _bl_bone_names(p, data)[srci]
        if b.name == imported:                                  # the file's bone, not renamed: the file's spelling
            fname = data.bone_names[srci]
            sb = str(obj.get(KEY_SRC_BONE) or "")
            return (sb if sb.lower() == fname.lower() else fname), bind
        return b.name, bind
    if now is not None:
        return b.name, now
    return b.name, _rig.from_blender_matrix(b.matrix_local)    # a bone made in Blender: its rest is its bind


def gather_markers(key, data, raw=None):
    """The markers of the import ``key`` as ICParse Markers (in the order :func:`markers_of` gives), plus notes; None when the
    add-on did not build this import's markers (the file's are kept). ``raw``: the file being exported, after its skeleton was
    written: markers are then measured against the written binds, and a marker whose bone was deleted with plain X keeps
    what the skeleton step wrote for it (re-hung on the nearest surviving bone, same rest place)."""
    from ICParse.sgm.scene import markers as MK
    if not managed(key):
        return None, []
    root = entity_root(key)
    src_markers = {int(m["index"]): m["marker"] for m in data.markers}
    out, notes, used = [], [], set()
    objs = markers_of(key)
    written = written_binds(raw) if raw is not None else None
    in_raw = MK.read_markers(bytes(raw)) if raw is not None else []
    # a duplicated empty (Shift+D) carries its original's number: the one that kept its name keeps the number
    objs.sort(key=lambda o: (int(o.get(KEY_INDEX, -1)) < 0, o.name != o.get(KEY_OBJ_NAME), int(o.get(KEY_INDEX, -1))))
    for o in objs:
        name = marker_name(o)
        idx = int(o.get(KEY_INDEX, -1))
        if dangling(o) and 0 <= idx < len(in_raw) and idx not in used:
            used.add(idx)
            m = in_raw[idx]                                     # as the skeleton step re-hung it
            out.append(MK.Marker(name, m.bone, m.matrix, m.version, m.tail, idx, m.bone_raw, m.legacy))
            notes.append(f"marker '{name}' hung from deleted bone '{o.parent_bone}': it follows '{m.bone or 'model space'}' "
                         "now (use Delete Bones, Merge Weights to see that in Blender)")
            continue
        if dangling(o):
            notes.append(f"marker '{name}' hangs from deleted bone '{o.parent_bone}': written in model space")
        bone, bind = _bone_of(o, data, written)
        W = _rig.from_blender_matrix(rest_world(o, root))
        L = np.linalg.inv(bind) @ W if bind is not None else W
        src = src_markers.get(idx) if idx >= 0 and idx not in used else None
        if src is not None:
            used.add(idx)
            if not bone and src.bone and MK.bone_index(src.bone, data.bone_names) is None:
                # the file names a bone the skeleton lacks: imported in model space, where the game puts it too; keep its bytes
                bone = src.bone
            same_bone = bone.lower() == (src.bone or "").lower()
            # 1e-3 (model units): the float32 chain bone bind -> Blender -> back moves a stock marker by up to ~6e-4 (InsectData
            # antiairtower, Ground); an untouched marker must keep its bytes, a nudge below a thousandth of a unit is invisible
            if same_bone:
                bone = src.bone
            if same_bone and np.allclose(L, src.local(), atol=1e-3):
                out.append(MK.Marker(name, src.bone, src.matrix, src.version, src.tail, idx, src.bone_raw, src.legacy))
            elif (bone and _file_bone(o, data).lower() == (src.bone or "").lower()
                  and np.allclose(L, src.local(), atol=1e-3)):
                # its bone was renamed in Blender and it kept its place: the new name, the source's floats (as the skeleton
                # step wrote it)
                out.append(MK.Marker(name, bone, src.matrix, src.version, src.tail, idx, None, src.legacy))
            else:
                out.append(MK.Marker(name, bone, MK.matrix12(L), src.version, src.tail, idx, src.bone_raw if same_bone else None))
        else:
            out.append(MK.Marker(name, bone, MK.matrix12(L)))
        R = L[:3, :3]
        if np.isfinite(R).all() and np.abs(R.T @ R - np.eye(3)).max() > 1e-3:
            notes.append(f"marker '{name}' is scaled or skewed; the game applies that to its effect too (apply the empty's scale "
                         f"if that is not meant)")
    seen = {}
    for m in out:
        low = m.name.lower()
        if low in seen:
            notes.append(f"two markers are called '{m.name}' (ignoring case); the game always finds the first")
        seen.setdefault(low, m.name)
    return out, notes


def apply_to_export(obj, data, raw: bytes):
    """``raw`` with the markers of ``obj``'s import written in (ICParse ``write_scene_markers``): (bytes, report dict, notes).
    The report has ``added``, ``removed``, ``changed``, ``renamed`` and ``kept``; it is empty when the file's markers are kept."""
    from ICParse.sgm.scene import markers as MK
    key = entity_key(obj)
    markers, notes = gather_markers(key, data, raw) if key else (None, [])
    if markers is None:
        return raw, {}, []
    rep = {}
    try:
        out = MK.write_scene_markers(raw, markers, report=rep)
    except ValueError as e:
        raise ValueError(f"the markers cannot be saved: {e}") from None
    return out, rep, notes


def marker_bones(data_sgm: bytes) -> set:
    """Lower-case names an event can find in a model: its markers and its bones."""
    from ICParse.sgm.scene import markers as MK
    from ICParse.sgm.scene.model import read_scene
    sc = read_scene(data_sgm)
    return {m.name.lower() for m in MK.read_markers(data_sgm)} | {b.name.lower() for b in sc.bones}


def can_find(name: str) -> bool:
    """Whether any event can find a marker called ``name`` (ICParse ``event_can_find``: not when it starts with '0')."""
    from ICParse.sgm.scene import markers as MK
    return MK.event_can_find(name)


def finds(value: str, names) -> bool:
    """Whether an event naming ``value`` finds something among ``names`` (lower case, :func:`marker_bones`): "random" always
    does; a name starting with '0' never does (``RenderModel::GetMarkerIndex``)."""
    from ICParse.sgm.scene import markers as MK
    if value.lower() == "random":
        return True
    return MK.event_can_find(value) and value.lower() in names


def unresolved_refs(ebp_bytes, sgm_bytes) -> dict:
    """{event value: count} of the .ebp's marker references that find nothing in the model ("random" left out; a name starting
    with '0' never finds anything)."""
    from ICParse.ebp import parser as EP
    names = marker_bones(sgm_bytes)
    refs = EP.event_marker_refs(EP.read(bytes(ebp_bytes)))
    return {v: n for v, n in refs.items() if not finds(v, names)}


def follow_renames_in_ebp(ebp_bytes, renamed, sgm_before: bytes, sgm_after: bytes):
    """(.ebp bytes with every event value naming a renamed marker renamed too, notes). A rename is followed only when the old
    name no longer finds anything in the new model. Notes also list every event reference that finds nothing now and did before
    (the stock files already have unresolved ones; those are not listed)."""
    from ICParse.ebp import parser as EP
    notes = []
    if ebp_bytes is None:
        return ebp_bytes, notes
    after = marker_bones(sgm_after)
    todo = [(a, b) for a, b in renamed or () if a.lower() != b.lower() and not finds(a, after)]
    for a, b in renamed or ():
        if a.lower() == b.lower():
            continue
        if finds(a, after):
            notes.append(f"marker '{a}' was renamed to '{b}', but the model still has something called '{a}', so the .ebp events "
                         f"naming '{a}' were left alone")
    out = bytes(ebp_bytes)
    if todo:
        try:
            e = EP.read(out)
        except Exception as ex:                                      # noqa: BLE001 -- a blueprint this cannot read: say so
            return out, notes + [f"the .ebp could not be read, so its events still name the old marker names ({ex})"]
        n = 0
        for i, (a, _b) in enumerate(todo):              # two steps, so a swap (A->B, B->A) does not merge them
            EP.rename_event_values(e, a, f"\x01bz_marker_{i}\x01")
        for i, (a, b) in enumerate(todo):
            k = EP.rename_event_values(e, f"\x01bz_marker_{i}\x01", b)
            n += k
            if k:
                notes.append(f"{k} .ebp event(s) naming marker '{a}' now name '{b}'")
        if n:
            out = EP.write(e)
    try:
        before = unresolved_refs(ebp_bytes, sgm_before)
        now = unresolved_refs(out, sgm_after)
    except Exception as ex:                                          # noqa: BLE001 -- a blueprint this cannot read: say so
        notes.append(f"could not check the .ebp's marker references ({ex})")
        return out, notes
    low_before = {k.lower() for k in before}
    for v, c in sorted(now.items()):
        if v.lower() not in low_before:
            notes.append(f"{c} .ebp event(s) name '{v}', which is no longer a marker or bone of the model: their effect or sound "
                         f"plays at the model's feet")
    return out, notes


def renames_of(obj, data) -> list:
    """(source name, new name) of every marker of ``obj``'s import that was renamed in Blender."""
    key = entity_key(obj)
    markers, _notes = gather_markers(key, data) if key else (None, [])
    src = {int(m["index"]): m["name"] for m in data.markers}
    return [(src[m.index], m.name) for m in markers or () if m.index is not None and m.index in src and src[m.index] != m.name]


def export_entity_ebp(obj, ebp_bytes, sgm_bytes):
    """Export Entity Folder's step for markers: (the .ebp with renamed markers followed into its events, notes)."""
    data = _core.load_scene(obj.get("bz_source", ""))
    with open(_core.resolve_source(obj.get("bz_source", "")), "rb") as f:
        before = f.read()
    return follow_renames_in_ebp(ebp_bytes, renames_of(obj, data), before, sgm_bytes)


# ------------------------------------------------------------------------------------------------------------ editing
def unique_name(key, base: str = DEFAULT_NAME) -> str:
    taken = {marker_name(o).lower() for o in markers_of(key)}
    if "%" not in base:
        if base.lower() not in taken:
            return base
        base = base + "%02d"
    i = 1
    while (base % i).lower() in taken:
        i += 1
    return base % i


def name_problem(key, name: str, obj=None) -> str:
    """Why ``name`` cannot be a marker name of ``key`` ("" when it can)."""
    from ICParse.sgm.scene import markers as MK
    if not name:
        return "a marker needs a name"
    try:
        nb = name.encode("latin1")
    except UnicodeEncodeError:
        return "the game stores plain Latin letters only"
    if len(nb) > MK.NAME_LIMIT:
        return f"the name is {len(nb)} bytes; the game takes at most {MK.NAME_LIMIT}"
    if "\x00" in name:
        return "the name contains a NUL"
    if not MK.event_can_find(name):
        return "the game never finds a marker whose name starts with '0' (events naming it play at the model's feet)"
    for o in markers_of(key):
        if o is not obj and marker_name(o).lower() == name.lower():
            return f"'{marker_name(o)}' already has that name (the game ignores case, so only the first would be found)"
    return ""


def add_marker(context, key, name: str, world_bl: Matrix, bone: str = ""):
    """A new marker empty of ``key`` at the world matrix ``world_bl``, on ``bone`` of the entity's rig ("" = model space)."""
    root = entity_root(key)
    arm = marker_rig(key)
    ref = next(iter(markers_of(key)), None)
    coll = ref.users_collection[0] if ref is not None and ref.users_collection else _collection(context)
    e = bpy.data.objects.new(name[:63], None)
    coll.objects.link(e)
    e.empty_display_type = "ARROWS"
    e.empty_display_size = ref.empty_display_size if ref is not None else 0.1
    e.show_name = True
    e[KEY_MARKER] = True
    e[KEY_INDEX] = -1
    e[KEY_NAME] = name
    e[KEY_OBJ_NAME] = e.name
    e[KEY_SRC_NAME] = ""
    e[KEY_SRC_BONE] = ""
    e[KEY_ENTITY] = key[1]
    e["bz_source"] = key[0]
    if bone and arm is not None and bone in arm.data.bones:
        e.parent, e.parent_type, e.parent_bone = arm, "BONE", bone
    elif arm is not None:
        e.parent = arm
    elif root is not None:
        e.parent = root
    e.matrix_parent_inverse = Matrix.Identity(4)
    rel = _mw(root).inverted() @ world_bl if root is not None else world_bl
    place(e, rel, root)
    for p in parts_of(key):
        if p.get(KEY_BUILT) is None:
            p[KEY_BUILT] = 0
    return e


def set_marker_bone(obj, bone: str) -> None:
    """Put the marker on ``bone`` of its rig ("" = model space), keeping where it is."""
    key = entity_key(obj)
    root = entity_root(key)
    arm = marker_rig(key)
    keep = rest_world(obj, root)
    if bone:
        if arm is None or bone not in arm.data.bones:
            raise ValueError(f"the rig has no bone '{bone}'")
        obj.parent, obj.parent_type, obj.parent_bone = arm, "BONE", bone
    else:
        obj.parent = arm if arm is not None else root
        obj.parent_type = "OBJECT"
        obj.parent_bone = ""
    obj.matrix_parent_inverse = Matrix.Identity(4)
    place(obj, keep, root)


def rename_marker_bone(arm, old: str, new: str) -> int:
    """Hook for the skeleton work: every marker on ``arm`` that hangs from bone ``old`` (ignoring case) moves to bone ``new``,
    keeping its place; a marker whose bone was renamed in Blender itself (its parent_bone already says ``new``) needs nothing.
    Returns how many markers were moved."""
    n = 0
    for o in [o for o in bpy.data.objects if is_marker(o) and o.parent is arm and o.parent_type == "BONE"]:
        if o.parent_bone.lower() == old.lower() and o.parent_bone != new:
            if new not in arm.data.bones:
                raise ValueError(f"the rig has no bone '{new}'")
            set_marker_bone(o, new)
            n += 1
    return n


def markers_off_bone(arm, bone: str, to: str = "") -> list:
    """Hook for the skeleton work, before ``bone`` is deleted: its markers move to bone ``to`` ("" = model space), keeping their
    place. Returns the names of the markers moved (for the report)."""
    moved = []
    for o in [o for o in bpy.data.objects if is_marker(o) and o.parent is arm and o.parent_type == "BONE"]:
        if o.parent_bone == bone:
            set_marker_bone(o, to)
            moved.append(marker_name(o))
    return moved


def rename_marker(obj, new: str) -> str:
    """Rename a marker (the game name and the object); returns the old name. Raises ValueError when ``new`` cannot be used."""
    key = entity_key(obj)
    why = name_problem(key, new, obj)
    if why:
        raise ValueError(why)
    old = marker_name(obj)
    set_marker_name(obj, new)
    return old


# ------------------------------------------------------------------------------------------------------------ the slice
class MarkersSlice(SO.BaseSlice):
    """The markers stage of the entity exporter (when it lands): gather the empties, write FORM MRKR, follow renames into the
    .ebp. Until then ``scene.export_scene_object`` calls :func:`apply_to_export` itself."""
    name = "markers"
    order = 50
    claims = ("EMPTY:bz_marker",)

    def read(self, src):
        from ICParse.sgm.scene import markers as MK
        return MK.read_markers(src.sgm) if getattr(src, "sgm", None) else []

    def gather(self, ctx, entity):
        parts = getattr(entity, "parts", None) or []
        if not parts:
            return None
        key = entity_key(parts[0])
        data = _core.load_scene(parts[0].get("bz_source", ""))
        markers, notes = gather_markers(key, data)
        return {"markers": markers, "notes": notes} if markers is not None else None

    def changed(self, data, state) -> bool:
        if not state or state.get("markers") is None:
            return False
        from ICParse.sgm.scene import markers as MK
        src = data or []
        now = state["markers"]
        return len(now) != len(src) or any(m.index is None or not MK.same_marker(m, src[m.index]) for m in now)

    def apply(self, out, src, data, state) -> None:
        if not state or state.get("markers") is None:
            return
        from ICParse.sgm.scene import markers as MK
        rep = {}
        before = out.sgm
        out.sgm = MK.write_scene_markers(out.sgm, state["markers"], report=rep)
        out.notes.extend(state.get("notes", []))
        if out.ebp is not None and rep.get("renamed"):
            out.ebp, notes = follow_renames_in_ebp(out.ebp, rep["renamed"], before, out.sgm)
            out.notes.extend(notes)
        out.shared["markers"] = rep

    def check(self, src, data, state) -> list:
        from ICParse.sgm.scene import markers as MK
        if not state or state.get("markers") is None:
            return []
        return [SO.issue("marker_name", SO.ERROR, p, where=m.name) for m in state["markers"] for p in MK.check_marker(m)]

    def on_rename(self, kind, old, new, state) -> None:
        if kind == "marker":
            for o in [o for o in bpy.data.objects if is_marker(o) and marker_name(o) == old]:
                set_marker_name(o, new)
        elif kind == "bone":
            for arm in {o.parent for o in bpy.data.objects if is_marker(o) and o.parent is not None and o.parent.type == "ARMATURE"}:
                rename_marker_bone(arm, old, new)


SLICE = MarkersSlice()
