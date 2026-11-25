"""Rigging: the creature skeleton as a Blender armature, with the control-point mesh rigid-skinned (one bone per point).

Each bone's rest matrix is its bind-pose world matrix in Blender axes, so posing it to ``B @ anim_world @ B^-1``
reproduces IC animation exactly. Editing and export use the rest mesh, untouched by the modifier (lossless).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

import bpy
from mathutils import Matrix

from ICParse.codecs import reconstruct as R

# SGM (x,y,z) -> Blender (x,-z,y) as a 4x4 change-of-basis B; a transform M maps as B @ M @ B^-1.
_B = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)
_BINV = np.linalg.inv(_B)


def _set_mode(context, obj, mode):
    """Switch `obj` into `mode` robustly. `bpy.ops.object.mode_set` polls the *context* active
    object and (in a GUI) a 3D-view area; calling it during an operator/startup script without an
    override silently fails its poll -> the armature ends up with no bones. A temp_override with the
    object (and a VIEW_3D area when one exists) makes it work in GUI, background, and at startup."""
    context.view_layer.objects.active = obj
    try:
        obj.select_set(True)
    except Exception:
        pass
    over = {"active_object": obj, "selected_objects": [obj], "object": obj}
    scr = getattr(context, "screen", None)
    if scr:
        for area in scr.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                over.update(area=area, region=region)
                break
    try:
        with context.temp_override(**over):
            bpy.ops.object.mode_set(mode=mode)
    except Exception:
        bpy.ops.object.mode_set(mode=mode)            # background / no screen


def to_blender_matrix(m_sgm) -> Matrix:
    """SGM-space 4x4 (or 3x4) transform -> Blender-space mathutils.Matrix."""
    m = np.asarray(m_sgm, dtype=np.float64)
    if m.shape == (3, 4):
        full = np.eye(4); full[:3, :4] = m; m = full
    return Matrix((_B @ m @ _BINV).tolist())


def from_blender_matrix(m_bl) -> np.ndarray:
    """Blender-space 4x4 (mathutils.Matrix) -> SGM-space numpy 4x4 (inverse of to_blender_matrix)."""
    return _BINV @ np.array(m_bl, dtype=np.float64) @ _B


KEY_BONE_SRC_NAMES = "bz_bone_src_names"      # on the armature: the Blender name of each SOURCE bone, in file order
KEY_BONE_SRC_PARENTS = "bz_bone_src_parents"  # ... and its parent's Blender name ("" = root)
KEY_BONE_REST = "bz_bone_rest"                # ... and its import-time rest matrix_local (16 floats per bone)
KEY_BONE_SRC_INDEX = "bz_src_index"           # on each Bone: its source index (identity through renames)
KEY_STUB_BONE = "bz_stub_bone"                # on a Bone Add Stub Socket created: the name it was created with
KEY_STUB_PARENT = "bz_stub_parent_src"        # ... and the source index of the bone it was created under (its host)
KEY_BONE_UID = "bz_uid"                       # on a Bone Add Bone made (or adopted): its identity (the store's new-bone table)
KEY_BONE_NEW_NAME = "bz_new_name"             # ... and the name it was stamped with (what limb rows list it by)
KEY_BONE_LIMITS = "bz_joint_limits"           # on a rig Bone (C8.4): the BONE record's joint limits -- min x, y, z then max
#                                               x, y, z, Euler angles in RADIANS on disk. The Skeleton.cpp READER converts
#                                               them to degrees (x180/pi) before storing -- in IC.exe (Skeleton_ReadChunk
#                                               0x00596da3) AND in the Object Editor (0x00591810), the same C++ function;
#                                               Bone::SetJointLimits (0x0059724d / OE 0x00561906) only SWAPS a pair whose
#                                               min > max. Then the record's limit flag word (bit 0; present when more than
#                                               3 bytes remain) as ICParse keeps it. Export writes a bone's limits only
#                                               when they differ from its record.
LIMITS_TOOLTIP = ("Joint limits (min x y z, max x y z in radians, then flags); exported if changed")


def stamp_joint_limits(arm_obj, model) -> int:
    """Put every source bone's joint limits (its BONE record's tail floats) on its rig bone as the :data:`KEY_BONE_LIMITS`
    custom property, with the tooltip; returns how many bones got one."""
    names = source_bone_names(arm_obj, model)
    k = 0
    for i, b in enumerate(model.bones):
        bb = arm_obj.data.bones.get(names[i]) if i < len(names) else None
        if bb is None or not len(b.tail):
            continue
        bb[KEY_BONE_LIMITS] = [float(x) for x in b.tail]
        try:
            bb.id_properties_ui(KEY_BONE_LIMITS).update(description=LIMITS_TOOLTIP, precision=4, step=1)
        except Exception:
            pass
        k += 1
    return k


def _stub_stamp_problem(bone, n_source, holders=None) -> "str | None":
    """Why a bone carrying the Add Stub Socket stamp is not (any more) a stub bone the export may add, or None: it must
    stay a leaf under the source bone it was created under, and be the one bone of its stamp -- a renamed one is fine
    (its limb row names it by the stamp: the export follows the rename), but a copy (Duplicate / Symmetrize copy the
    stamp) is not. ``holders``: the names of every rig bone carrying the same stamp."""
    stamp = str(bone.get(KEY_STUB_BONE))
    if bone.get(KEY_BONE_SRC_INDEX) is not None:
        return "it also carries a source bone's import stamp"
    others = [h for h in (holders or [bone.name]) if h != bone.name]
    if others and bone.name != stamp:
        return (f"it was created as '{stamp}'" + (" (a copy of a stub bone)" if stamp in others
                                                  else f" and bones {', '.join(repr(o) for o in others)} carry that "
                                                       "stamp too (copies): which one is the stub bone is ambiguous"))
    if len(bone.children):
        return "a stub bone is a leaf, and bones were added under it"
    par = bone.parent
    want = bone.get(KEY_STUB_PARENT)
    sid = par.get(KEY_BONE_SRC_INDEX) if par is not None else None
    if par is None or sid is None or want is None or int(sid) != int(want) or not 0 <= int(sid) < n_source:
        return "it was re-parented since it was created"
    return None


def created_stub_bones(arm_obj):
    """``(accepted, refused)``: the rig bones Add Stub Socket created that the export may add (leaves under their
    creation parent, one bone per creation stamp -- renamed or not), and ``[(bone name, why)]`` for stamped bones that
    no longer are."""
    n = len(arm_obj.get(KEY_BONE_SRC_NAMES, []) or [])
    holders = {}
    for b in arm_obj.data.bones:
        if b.get(KEY_STUB_BONE) is not None:
            holders.setdefault(str(b.get(KEY_STUB_BONE)), []).append(b.name)
    ok, bad = [], []
    for b in arm_obj.data.bones:
        if b.get(KEY_STUB_BONE) is None:
            continue
        why = _stub_stamp_problem(b, n if n else len(arm_obj.data.bones), holders[str(b.get(KEY_STUB_BONE))])
        if why:
            bad.append((b.name, why))
        else:
            ok.append(b)
    return ok, bad


def new_stub_bones(arm_obj, model=None):
    """The rig's created stub bones as ``structure.NewBone`` (its current name, the file name of the source bone it
    hangs from, its rest in SGM axes, and the name it was created with when renamed since) for the export."""
    from . import structure
    ok, _bad = created_stub_bones(arm_obj)
    if not ok:
        return []
    file_names = [b.name for b in model.bones] if model is not None else source_bone_names(arm_obj, model)
    out = []
    for b in ok:
        sid = int(b.parent.get(KEY_BONE_SRC_INDEX))
        W = from_blender_matrix(b.matrix_local)
        stamp = str(b.get(KEY_STUB_BONE))
        out.append(structure.NewBone(name=b.name, parent=str(file_names[sid]),
                                     world=tuple(float(x) for x in W.reshape(-1)),
                                     stamp=stamp if stamp != b.name else None))
    return out


def source_bone_names(arm_obj, model=None):
    """The Blender bone name of every source bone AT IMPORT, in file order: the import snapshot (it holds the names
    Blender actually created: ``bone{i}`` fallbacks, ``.001`` de-duplication); for an armature imported before the
    snapshot, the model's names. A bone renamed since is found by :func:`current_bone_names`."""
    names = arm_obj.get(KEY_BONE_SRC_NAMES)
    if names is not None:
        return [str(n) for n in names]
    if model is None:
        raise KeyError(f"rig {arm_obj.name!r} has no bone snapshot and no source model was given")
    return [b.name or f"bone{i}" for i, b in enumerate(model.bones)]


class RigSkeleton:
    """The rig read against its import snapshot: ``snapshot`` ``[(import name, import parent)]``, ``resolution``
    (:class:`structure.BoneResolution` over the rig's bones minus the accepted created stub bones), ``stubs`` /
    ``stub_bad`` (:func:`created_stub_bones`), ``note`` (set for a rig imported before the snapshot), and ``added`` /
    ``added_bad``: the added bones the export may insert (``structure.classify_added_bones`` rows, keys against an empty
    table) and ``[(name, why)]`` of those it refuses; ``added_renames``: ``[(stamp name, current name)]`` of accepted
    added bones renamed since they were stamped (``structure.added_bone_renames``)."""

    def __init__(self, snapshot, resolution, stubs, stub_bad, note, added=(), added_bad=(), added_renames=()):
        self.snapshot, self.resolution, self.stubs, self.stub_bad, self.note = snapshot, resolution, stubs, stub_bad, note
        self.added, self.added_bad, self.added_renames = list(added), list(added_bad), list(added_renames)

    @property
    def added_names(self):
        return [row[0].name for row in self.added]

    @property
    def stub_renames(self):
        """``[(creation name, current name)]`` of created stub bones renamed since."""
        return [(str(b.get(KEY_STUB_BONE)), b.name) for b in self.stubs if str(b.get(KEY_STUB_BONE)) != b.name]

    def renames(self):
        """``[(import name, current name)]`` of every renamed bone: source bones, then created stub bones."""
        return [(o, n) for _i, o, n in self.resolution.renames] + self.stub_renames


def bones_key(arm_obj) -> tuple:
    """What a cached read-out of the rig's skeleton depends on, for every bone: its name, its parent's, and the identity
    stamps (source index, Add Bone's uid and stamp name, Add Stub Socket's creation name and parent), so a read-out
    follows Adopt Added Bones (it writes stamps only) and an Undo of it as well as renames and re-parents."""
    return tuple((b.name, b.parent.name if b.parent else "", b.get(KEY_BONE_SRC_INDEX), b.get(KEY_BONE_UID),
                  b.get(KEY_BONE_NEW_NAME), b.get(KEY_STUB_BONE), b.get(KEY_STUB_PARENT)) for b in arm_obj.data.bones)


def rig_skeleton(arm_obj, model=None, src_stubs=None):
    """:class:`RigSkeleton` of ``arm_obj`` (None without a snapshot and without ``model``). ``src_stubs``: the source's
    stub bone indices when ``model`` is not given (``core.source_stub_bones``), so the add rule refuses a bone under one
    without the source being parsed."""
    from . import structure
    names = arm_obj.get(KEY_BONE_SRC_NAMES)
    note = ""
    if names is not None:
        snap = list(zip([str(n) for n in names], [str(p) for p in arm_obj.get(KEY_BONE_SRC_PARENTS, [""] * len(names))]))
    elif model is not None:
        bn = [b.name or f"bone{i}" for i, b in enumerate(model.bones)]
        snap = [(bn[i], bn[int(b.parent)] if b.parent is not None and 0 <= int(b.parent) < len(bn) else "")
                for i, b in enumerate(model.bones)]
        note = " (this rig was imported before the bone snapshot: compared with the source file's names)"
    else:
        return None
    stubs, stub_bad = created_stub_bones(arm_obj) if names is not None else ([], [])
    stub_ptrs = {b.name for b in stubs}
    rest_flat = list(arm_obj.get(KEY_BONE_REST, []))
    srest = None
    if len(rest_flat) == 16 * len(snap):
        srest = [tuple(float(x) for x in rest_flat[16 * i:16 * i + 16]) for i in range(len(snap))]
    bones = []
    for b in arm_obj.data.bones:
        if b.name in stub_ptrs:
            continue                                   # created stub bones: the export adds them
        sid = b.get(KEY_BONE_SRC_INDEX) if names is not None else None
        M = b.matrix_local
        bones.append(structure.RigBone(name=b.name, parent=b.parent.name if b.parent else "",
                                       src=None if sid is None else int(sid),
                                       rest=tuple(float(M[r][c]) for r in range(4) for c in range(4))))
    res = structure.resolve_bones(snap, bones, srest)
    added, added_bad = (classify_added(arm_obj, res, stubs, stub_bad, model=model, src_stubs=src_stubs)
                        if names is not None else ([], []))
    taken = [b.name for b in arm_obj.data.bones] + [n for n, _p in snap] + [str(b.get(KEY_STUB_BONE)) for b in stubs]
    return RigSkeleton(snap, res, stubs, stub_bad, note, added, added_bad, structure.added_bone_renames(added, taken))


def _rig_added(arm_obj, names, stub_bad):
    from . import structure
    bad = {n for n, _w in stub_bad}
    out = []
    for nm in names:
        b = arm_obj.data.bones.get(nm)
        if b is None:
            continue
        uid, stamp = b.get(KEY_BONE_UID), b.get(KEY_BONE_NEW_NAME)
        out.append(structure.RigAdded(name=b.name, parent=b.parent.name if b.parent else "", children=len(b.children),
                                      uid=None if uid is None else str(uid), stamp=None if stamp is None else str(stamp),
                                      src_stamp=b.get(KEY_BONE_SRC_INDEX) is not None,
                                      stub=b.get(KEY_STUB_BONE) is not None or b.name in bad))
    return out


def classify_added(arm_obj, res, stubs, stub_bad, table_uids=(), model=None, chains=False, src_stubs=None):
    """:func:`structure.classify_added_bones` over the rig bones ``res`` (a ``BoneResolution``) found to be no source
    bone. Stub bones: the created ones (accepted and refused) and the source's: the source indices ``src_stubs`` when
    given (``core.source_stub_bones``, or ``structure.export_stub_bones`` with the edited limbs), else with ``model``
    (the source) its stub bones (None and no model = not known)."""
    from . import structure
    n_src = len(res.current)
    source_of = {c: i for i, c in enumerate(res.current) if c is not None}
    stub_names = [b.name for b in stubs] + [n for n, _w in stub_bad]
    if src_stubs is None and model is not None and len(model.bones) == n_src:
        src_stubs = structure.stub_bone_indices(model)
    if src_stubs is not None:
        stub_names += [res.current[i] for i in sorted(src_stubs) if 0 <= i < n_src and res.current[i] is not None]
    rows = _rig_added(arm_obj, res.added, stub_bad)
    return structure.classify_added_bones(rows, source_of, list(table_uids), n_src, stub_names, chains)


def added_bones(arm_obj, table_uids=(), model=None):
    """The rig's added bones the export inserts, as ``structure.AddedBone`` (current name, owner key against the store's
    new-bone table ``table_uids``, parent key, rest world in SGM axes, the stamp name limb rows use, limb default), and
    ``[(name, why)]`` of those it refuses (the skeleton guard reports them first)."""
    from . import structure
    sk = rig_skeleton(arm_obj, model)
    if sk is None:
        return [], []
    rows, bad = classify_added(arm_obj, sk.resolution, sk.stubs, sk.stub_bad, table_uids, model)
    out = []
    for ra, key, pk, default in rows:
        b = arm_obj.data.bones[ra.name]
        W = from_blender_matrix(b.matrix_local)
        stamp = None if default or ra.stamp is None or ra.stamp == ra.name else str(ra.stamp)
        out.append(structure.AddedBone(name=ra.name, key=int(key), parent=int(pk),
                                       world=tuple(float(x) for x in W.reshape(-1)), stamp=stamp, limb_default=bool(default)))
    return out, bad


def current_bone_names(arm_obj, model=None, deleted=None):
    """The rig's current name of every source bone, in file order, by identity (the import stamps; renamed bones under
    their new names). Raises KeyError when a source bone has no rig bone (deleted, or copies that can't be told apart:
    the skeleton guard refuses those first) -- unless ``deleted`` is given: that string stands in for such a bone (C8: a
    deleted source bone has no rig bone and no vertex group to read)."""
    sk = rig_skeleton(arm_obj, model)
    if sk is None:
        return source_bone_names(arm_obj, model)
    cur = sk.resolution.current
    missing = [sk.snapshot[i][0] for i, c in enumerate(cur) if c is None]
    if missing and deleted is None:
        raise KeyError(f"bone {missing[0]!r} is not on rig {arm_obj.name!r}"
                       + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""))
    return [str(c) if c is not None else str(deleted) for c in cur]


def bone_renames(arm_obj, model=None):
    """{source bone index: current name} of the source bones renamed on the rig since import (empty: none)."""
    sk = rig_skeleton(arm_obj, model)
    return {} if sk is None or not sk.resolution.stamped else sk.resolution.rename_map()


def rename_canon(arm_obj):
    """{current name: import name} of every renamed bone of the rig (source bones by their stamps, created stub bones by
    their creation names, added bones by the name Add Bone / Adopt Added Bones stamped: ``RigSkeleton.added_renames``):
    what an Action's F-curve paths are read back to before its hash is compared with the import hash, so a rename alone
    keeps a clip, and the old names whose paths the export (and Rename Bone) point at the bone's current name -- Blender's
    own rename leaves an Action on no NLA strip alone. Empty when nothing is renamed or the skeleton can't be resolved."""
    try:
        sk = rig_skeleton(arm_obj)
    except Exception:
        return {}
    if sk is None or not sk.resolution.stamped:
        return {}
    return {n: o for o, n in sk.renames() + sk.added_renames}


def bone_topology(arm_obj, sk=None, model=None, src_stubs=None, added=None):
    """``(topology.BoneTopology, refusals)`` of the rig's SOURCE bones: the deleted ones and the re-parented ones with
    their new parent's key -- a source index, or an added bone's owner key (``added``: the ``structure.AddedBone`` rows
    the export inserts, keys against the store's table; None: the ``RigSkeleton``'s rows, keys past an empty table --
    the same names, so the panel's refusals read the same). A child of a deleted bone counts as re-parented (Blender
    hangs it from the deleted bone's parent). Refused in words: a bone made a root, one re-parented under a bone the
    export does not write (a copy, a created stub bone, a refused added bone)."""
    from . import topology
    sk = rig_skeleton(arm_obj, model, src_stubs) if sk is None else sk
    if sk is None:
        return topology.BoneTopology(), []
    res = sk.resolution
    snap_index = {nm: i for i, (nm, _p) in enumerate(sk.snapshot)}
    cur_index = {c: i for i, c in enumerate(res.current) if c is not None}
    if added is not None:
        added_key = {ab.name: int(ab.key) for ab in added}
    else:
        added_key = {ra.name: int(key) for ra, key, _pk, _d in sk.added}
    deleted = tuple(sorted(snap_index[n] for n in res.deleted if n in snap_index))
    parents, bad = [], []
    roots_gone = [n for n in res.deleted if n in snap_index and not sk.snapshot[snap_index[n]][1]]
    for n in roots_gone:
        bad.append(f"bone '{n}' is the root bone and was deleted: a creature skeleton needs its root. Undo the delete")
    for name, was, now in res.reparented:
        i = cur_index.get(name)
        if i is None:
            continue
        if not now and was in roots_gone:
            continue                                     # the deleted root's children: the root is the message
        if not now:
            bad.append(f"bone '{name}' was made a root bone (it was under '{was}'): a creature skeleton has one root, the "
                       "source's. Give it a parent, or Undo")
        elif now in cur_index:
            parents.append((i, cur_index[now]))
        elif now in added_key:
            parents.append((i, added_key[now]))
        else:
            stub = any(b.name == now for b in sk.stubs) or now in {n for n, _w in sk.stub_bad}
            why = dict(sk.added_bad).get(now)
            bad.append(f"bone '{name}' was re-parented from '{was}' under '{now}', " + (
                "a stub bone Add Stub Socket created (a stub limb's only bone): re-parent it under the stub's host" if stub
                else f"which the export refuses ({why})" if why else "which is no bone the export writes (a copy of a "
                "bone, or a bone the skeleton guard refuses)") + ", or Undo")
    return topology.BoneTopology(deleted=deleted, parents=tuple(parents)), bad


def skeleton_error(arm_obj, model=None, src_stubs=None, rules=None):
    """Why the armature's bones can't be exported as they are, or None. Renamed bones export under their new names
    (limb bone lists, connection hosts, IK chains and clip tracks follow); added bones export when the add rule takes
    them (``structure.classify_added_bones``: Add Bone's stamped bones, bones Blender added under a bone of the creature,
    the stub bones Add Stub Socket creates); deleted and re-parented source bones export (C8: the record dropped or its
    parent changed, points / limb lists / IK chains / clip tracks following) unless the delete / re-parent rules refuse
    them (``topology.topology_refusals`` against ``rules`` -- the export's limb lists, hosts and stub bones; None: the
    source's records); refused too: added bones the rule refuses (a copy of a source bone, a root, a bone under a stub
    bone), copies of a bone that can't be told apart, rename chains (a bone now called what another bone was called at
    import) and names the file can't hold. ``model``: the source (its stub bones take no added bone), or ``src_stubs``:
    just its stub bone indices (the panel's cheap stand-in)."""
    from . import structure, topology
    if arm_obj is None:
        return None
    sk = rig_skeleton(arm_obj, model, src_stubs)
    if sk is None:
        return None
    res = sk.resolution
    if res.overwritten:
        return "; ".join(res.overwritten_text())
    why = {n: w for n, w in sk.stub_bad}
    ok_added = set(sk.added_names)
    bad_added = dict(sk.added_bad)
    refused = [n for n in res.added if n not in ok_added]
    structural = [(f"bone '{n}' was added (a stub bone Add Stub Socket made, but {why[n]})" if n in why
                   else bad_added.get(n, f"bone '{n}' was added")) for n in refused]
    if structural:
        tail = [n for n in refused if n in why or n not in bad_added]
        return ("; ".join(structural) + sk.note + (
            " -- the .sgm export can rename, re-parent, delete and add bones (Boner > Bones), not take a copy or a stub "
            "bone's child; Undo the change or delete the bone" if tail else ""))
    topo, tbad = bone_topology(arm_obj, sk)
    if tbad:
        return "; ".join(tbad) + sk.note
    if topo:
        if model is None:
            return ("bones were deleted or re-parented on the rig, but the source creature is not at hand to check the "
                    "delete / re-parent rules (root, looked-up names, stub bones, hosts, one-bone limbs, limb branches)")
        probs = topology.topology_refusals(model, topo, rules if rules is not None else topology.rules_of(
            model, stubs=src_stubs if src_stubs is not None else None,
            added={ra.name: (int(k), int(pk)) for ra, k, pk, _d in sk.added}))
        if probs:
            return "; ".join(probs) + sk.note
    if res.ambiguous:
        return ("; ".join(res.changes()[-len(res.ambiguous):]) + ": delete the copies (or give the source bone back "
                "its import name), or Undo")
    if res.by_rest:
        pairs = [(o, n) for _i, o, n in res.by_rest]
        return ("; ".join(f"bone '{o}' was renamed to '{n}'" for o, n in pairs) + sk.note + " -- this rig was imported before "
                "bones carried identity stamps: rename it back and use Boner > Bones > Rename Bone, or re-import")
    extra = sk.stub_renames
    conflicts = structure.name_conflicts([n for n, _p in sk.snapshot], res, extra=extra)
    if conflicts:
        return "; ".join(conflicts)
    final = [c for c in res.current if c is not None] + [b.name for b in sk.stubs] + sk.added_names
    probs = structure.bone_name_problems(final, [n for _o, n in sk.renames()] + sk.added_names)
    if probs:
        return "; ".join(probs) + ": rename the bone (Boner > Bones > Rename Bone)"
    return None


def bone_transforms_from_armature(arm_obj, model, topo=None):
    """{bone_index: 12-float SGM bind-LOCAL transform} for ONLY the bones the user re-posed.

    A bone is 'edited' if its rest matrix differs from the import-time snapshot (Blender's
    head/tail/roll storage drifts vs the SGM bind, so we compare Blender-to-Blender). Untouched
    bones are omitted -> Export keeps their original bytes (byte-identical). Edited bones are
    re-encoded as bind-local relative to the parent's current rest; Export re-expresses owned
    vertices in the new bind pose so the creature's shape is preserved. Bones are found by identity (a renamed bone
    under its new name, :func:`current_bone_names`); a missing bone raises KeyError (never an identity stand-in: the
    skeleton guard refuses first). ``topo`` (``topology.BoneTopology``, :func:`bone_topology`): deleted bones are
    skipped, and a re-parented bone's transform is always written, relative to the rest of its CURRENT rig parent
    (whatever bone that is), so its bind world is unchanged under the new parent."""
    bones = model.bones
    gone = set(int(d) for d in topo.deleted) if topo else set()
    moved = topo.parent_map if topo else {}
    names = current_bone_names(arm_obj, model, deleted="" if gone else None)
    if len(names) != len(bones):
        raise KeyError(f"rig {arm_obj.name!r} snapshot has {len(names)} bones, the source has {len(bones)}")
    parents = [None if b.parent is None else int(b.parent) for b in bones]
    rest_flat = list(arm_obj.get(KEY_BONE_REST, []))
    rest = np.array(rest_flat, dtype=np.float64).reshape(-1, 4, 4) if rest_flat else None

    def bone(i):
        bb = arm_obj.data.bones.get(names[i])
        if bb is None:
            raise KeyError(f"bone {names[i]!r} is not on rig {arm_obj.name!r}")
        return bb

    out = {}
    for i in range(len(bones)):
        if i in gone:
            continue
        bb = bone(i)
        ml = np.array(bb.matrix_local, dtype=np.float64)
        if i in moved:                                        # re-parented: relative to the rig's current parent
            if bb.parent is None:
                raise KeyError(f"bone {names[i]!r} has no parent on rig {arm_obj.name!r}")
            local = np.linalg.inv(from_blender_matrix(bb.parent.matrix_local)) @ from_blender_matrix(bb.matrix_local)
            out[i] = [float(x) for x in (list(local[:3, 0]) + list(local[:3, 1]) + list(local[:3, 2])
                                         + list(local[:3, 3]))]
            continue
        if rest is not None and i < len(rest) and np.allclose(ml, rest[i], atol=1e-6):
            continue                                          # unedited -> keep source bytes
        p = parents[i]
        Wi = from_blender_matrix(bb.matrix_local)
        if p is None:
            local = Wi
        elif not 0 <= p < len(bones):
            raise KeyError(f"bone {names[i]!r} has parent index {p} outside the {len(bones)}-bone skeleton")
        else:
            local = np.linalg.inv(from_blender_matrix(bone(p).matrix_local)) @ Wi
        out[i] = [float(x) for x in (list(local[:3, 0]) + list(local[:3, 1])
                                     + list(local[:3, 2]) + list(local[:3, 3]))]
    return out


def encode_animation(model, name, fps, frame_times, world_sgm_per_frame, layout=None):
    """Build a new SGM clip FROM SCRATCH from per-frame, per-bone WORLD matrices (SGM space), with
    ICParse's clip builder -- no existing clip is copied for its headers. ``frame_times`` are the
    normalized sample times ``frame / frame_count`` (so ``frame_count = len(frame_times) - 1``). The
    layout defaults to that of the creature's existing clips (v7 for a creature that has none) so a
    file stays uniform; tracks, channel presence, bounds and the CANM trailer are all derived."""
    from ICParse.codecs import anim as ICanim
    from . import core
    if layout is None:
        layout = ICanim.layout_of(model.animations[0]) if model.animations else "v7"
    tracks = ICanim.tracks_from_world(core.clip_track_model(model, world_sgm_per_frame), frame_times,
                                      world_sgm_per_frame, layout)
    return ICanim.build_animation(name, len(frame_times) - 1, fps, tracks, layout=layout, model=model)


def _select_all_bones(arm_obj):
    """Select every bone (Blender 5 keeps bone selection on the pose bones; older builds on Bone)."""
    pose = getattr(arm_obj, "pose", None)
    if pose is not None and len(pose.bones):
        for pb in pose.bones:
            try:
                pb.select = True
            except Exception:
                break
        return
    for bb in arm_obj.data.bones:
        try:
            bb.select = True
        except Exception:
            break


def create_armature(context, name, bone_names, parents, bind_world_sgm):
    """Create an armature whose rest pose is the given SGM-space bind hierarchy (one bone per entry,
    rest matrix = bind WORLD converted to Blender axes). Shared by creatures and scene objects.
    Returns the armature object; raises if Blender refused the edit-mode switch."""
    mats = [to_blender_matrix(np.asarray(m, dtype=np.float64)) for m in bind_world_sgm]
    arm = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm)
    context.collection.objects.link(arm_obj)

    prev_active = context.view_layer.objects.active
    _set_mode(context, arm_obj, "EDIT")
    ebs = []
    for i, bname in enumerate(bone_names):
        eb = arm.edit_bones.new(bname or f"bone{i}")
        eb.head = (0.0, 0.0, 0.0); eb.tail = (0.0, 0.1, 0.0)     # temp non-zero length
        eb[KEY_BONE_SRC_INDEX] = i                               # identity: survives renames (skeleton guard)
        ebs.append(eb)
    for i in range(len(bone_names)):
        ebs[i].matrix = mats[i]                                  # rest = bind-pose world (Blender)
    for i, p in enumerate(parents):
        p = None if p is None else int(p)
        if p is not None and 0 <= p < len(ebs) and p != i:
            ebs[i].parent = ebs[p]
    # length toward nearest child (keeps the bind orientation; visual only, deform uses .matrix)
    heads = [np.array(m.translation) for m in mats]
    child_of = defaultdict(list)
    for i, p in enumerate(parents):
        if p is not None and 0 <= int(p) < len(bone_names):
            child_of[int(p)].append(i)
    for i in range(len(bone_names)):
        if child_of[i]:
            d = min(float(np.linalg.norm(heads[j] - heads[i])) for j in child_of[i])
            if d > 1e-4:
                ebs[i].length = d
    created = [eb.name for eb in ebs]                            # the names Blender really gave (dedup)
    created_parents = [eb.parent.name if eb.parent else "" for eb in ebs]
    _set_mode(context, arm_obj, "OBJECT")

    # Verify edit-mode actually took (a failed mode switch silently yields a boneless armature).
    if len(arm_obj.data.bones) != len(bone_names):
        bpy.data.objects.remove(arm_obj, do_unlink=True)
        raise RuntimeError(f"armature build failed: {len(arm_obj.data.bones)}/{len(bone_names)} bones "
                           "created (mode switch likely blocked by context)")

    # Stash each bone's rest matrix (Blender stores bones as lossy head/tail/roll, so we can't
    # detect edits by comparing to the SGM bind; instead compare to this import-time snapshot).
    rest = []
    for i, bname in enumerate(created):
        bb = arm.bones.get(bname)
        M = bb.matrix_local if bb is not None else Matrix.Identity(4)
        rest += [float(M[r][c]) for r in range(4) for c in range(4)]
    arm_obj[KEY_BONE_REST] = rest
    arm_obj[KEY_BONE_SRC_NAMES] = created
    arm_obj[KEY_BONE_SRC_PARENTS] = created_parents
    import uuid
    arm_obj["bz_uid"] = uuid.uuid4().hex
    # Select every bone: the Dope Sheet / Action Editor's default "Only Show Selected" filter hides the
    # channels of unselected bones, which would make a freshly imported clip look keyless.
    _select_all_bones(arm_obj)
    context.view_layer.objects.active = prev_active
    return arm_obj


def build_armature(context, mesh_obj, model):
    """Create the armature, rest-pose it at bind, rigid-skin `mesh_obj`, add the modifier.
    Returns the armature object (or None on failure)."""
    bones = model.bones
    if not bones:
        return None
    owners = R.vertex_bone_owners(model)
    bw = R.bind_world_matrices(model)
    arm_obj = create_armature(context, mesh_obj.name + "_rig", [b.name or f"bone{i}" for i, b in enumerate(bones)],
                              [None if b.parent is None else int(b.parent) for b in bones], bw)

    # rigid skin: one vertex group per bone, every owned control point at weight 1.0
    by_bone = defaultdict(list)
    for v, bi in enumerate(owners):
        if bi >= 0:
            by_bone[bi].append(v)
    created = source_bone_names(arm_obj, model)
    for bi, vs in by_bone.items():
        name = created[bi]
        vg = mesh_obj.vertex_groups.get(name) or mesh_obj.vertex_groups.new(name=name)
        vg.add(vs, 1.0, "REPLACE")
    stamp_joint_limits(arm_obj, model)                     # C8.4: the joint limits, editable per bone

    mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    mod.use_vertex_groups = True
    # NOT parented: armature + mesh both at origin, so the modifier deforms in world space and the
    # draw overlay (which multiplies obj.matrix_world) stays correct without a parent transform.
    context.view_layer.objects.active = mesh_obj
    return arm_obj


def bake_world_frames(arm_obj, bone_names, parents, bind_world_sgm, frames_world_sgm, frame_start=1):
    """Keyframe the armature from per-frame, per-bone SGM WORLD matrices (see apply_animation for
    the basis math). Returns the number of frames written."""
    Rm = [to_blender_matrix(np.asarray(m, dtype=np.float64)) for m in bind_world_sgm]
    Rinv = [m.inverted_safe() for m in Rm]
    pbones = arm_obj.pose.bones
    for pb in pbones:
        pb.rotation_mode = "QUATERNION"
    for f, Wsgm in enumerate(frames_world_sgm):
        A = [to_blender_matrix(np.asarray(Wsgm[i], dtype=np.float64)) for i in range(len(bone_names))]
        frame = frame_start + f
        for i, bname in enumerate(bone_names):
            pb = pbones.get(bname or f"bone{i}")
            if pb is None:
                continue
            p = None if parents[i] is None else int(parents[i])
            if p is None or not (0 <= p < len(bone_names)):
                basis = Rinv[i] @ A[i]
            else:
                basis = Rinv[i] @ Rm[p] @ A[p].inverted_safe() @ A[i]
            pb.matrix_basis = basis
            pb.keyframe_insert("location", frame=frame)
            pb.keyframe_insert("rotation_quaternion", frame=frame)
            pb.keyframe_insert("scale", frame=frame)
    return len(frames_world_sgm)


def encode_scene_animation(data, name, fps, frame_times, world_sgm_per_frame):
    """A scene clip (structure / character) from per-frame, per-bone SGM world matrices, built by
    ICParse's scene clip builder (tracks for moving bones, bounds from the posed geometry, the per-mesh
    CANM channels)."""
    from ICParse.sgm.scene import model as SM
    tracks = SM.scene_tracks_from_world(data.scene, frame_times, world_sgm_per_frame)
    return SM.build_scene_animation(data.scene, name, len(frame_times) - 1, fps, tracks)


def apply_scene_animation(arm_obj, data, anim_index: int, frame_start: int = 1, times=None):
    """Bake one scene clip (structure part motion / character skin animation) onto the armature: one
    Blender frame per clip frame (``t = f / frame_count``), or per entry of ``times`` (normalized)."""
    from . import core
    if not (0 <= anim_index < len(data.animations)):
        return 0
    an = data.animations[anim_index]
    K = max(1, int(getattr(an, "frame_count", 0)) or 1)
    ts = list(times) if times is not None else [f / K for f in range(K + 1)]
    frames = [core.scene_bone_world(data, anim_index, float(t)) for t in ts]
    return bake_world_frames(arm_obj, data.bone_names, data.bone_parents, data.bind_world, frames, frame_start)


def apply_animation(arm_obj, model, anim_index: int, frame_start: int = 1, times=None):
    """Bake one IC creature animation onto the armature as per-frame keyframes.

    Each bone's pose is set via ``matrix_basis`` computed analytically from the bind (rest) and
    animated world matrices, so it is independent of Blender's pose-resolution order:

        root:  basis_i = R_i^-1 @ A_i
        child: basis_i = R_i^-1 @ R_parent @ A_parent^-1 @ A_i

    with R = bind world, A = animated world (both Blender-space). Reproduces ``pose.matrix == A``.
    frame_count is the number of INTERVALS (key times = frame / frame_count, last key at 1.0), so a
    clip has frame_count + 1 frames and the file's keys land exactly on Blender frames."""
    if not (0 <= anim_index < len(model.animations)):
        return 0
    anim = model.animations[anim_index]
    bones = model.bones
    K = max(1, int(getattr(anim, "frame_count", 0)) or 11)
    ts = list(times) if times is not None else [f / K for f in range(K + 1)]
    frames = [R.animated_bone_world(model, anim, float(t)) for t in ts]
    try:
        names = current_bone_names(arm_obj, model)
    except KeyError:
        names = source_bone_names(arm_obj, model)       # a bone deleted on the rig: its keys are skipped (as before)
    return bake_world_frames(arm_obj, names,
                             [None if b.parent is None else int(b.parent) for b in bones],
                             R.bind_world_matrices(model), frames, frame_start)
