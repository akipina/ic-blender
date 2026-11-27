"""Scene objects (structures, characters, props): the rig's bone edits written into the ``.sgm``.

A bone renamed, moved, added, deleted or re-parented in Edit Mode reaches the file through ICParse's skeleton editor
(``ICParse.sgm.scene.skeleton``): the bones, the part group's transforms, the rigid parts' bindings, every part's skin
palette, the markers and the clip tracks are kept consistent, and only the chunks that change are rewritten. The rig is
read against its import snapshot (the bone stamps ``bz_src_index`` and ``bz_bone_src_names`` / ``_parents`` / ``_rest``
on the armature), with the scene rules, not the creature ones: any new bone anywhere, several roots, any new parent.

What Blender shows is what is written: a vertex group named after a deleted bone deforms nothing in Blender, so it is
not exported either (use Delete Bones, Merge Weights to hand its weights to the parent first); a rigid part follows the
bone of its vertex group. Clips: an Action left as imported keeps its keys, re-based on the new rest where a bone moved
(Blender keeps the pose relative to the rest); an edited Action is sampled from Blender against the new skeleton.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional

import numpy as np

import bpy

from . import rig as _rig

REST_TOL = 1e-5            # Blender stores a bone as head/tail/roll: a rest within this of the import one is unmoved


def rig_of(obj):
    """The armature that deforms the scene part ``obj`` (its modifier, else the recorded name), or None."""
    arm = next((m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object), None) \
        or bpy.data.objects.get(obj.get("bz_rig", "") or "")
    return arm if arm is not None and arm.type == "ARMATURE" else None


@dataclass
class RigEdit:
    """The rig read as an ICParse ``SkeletonEdit`` against the source file. Targets are the source bones in source order
    (deleted ones left out), then the added bones in Blender's order. ``rig_names[t]``: the rig bone of target ``t``."""
    sk: object                         # ICParse skeleton.Skeleton of the source
    edit: object                       # ICParse skeleton.SkeletonEdit
    plan: object                       # ICParse skeleton.Plan
    rig_names: List[str]
    target_of_group: Dict[str, int]    # vertex group (= rig bone) name -> target
    deleted_groups: Dict[str, str]     # rig/import name of a deleted bone -> its file name
    changes: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def target_of(self, name: str) -> Optional[int]:
        t = self.target_of_group.get(name)
        if t is None:
            low = name.lower()
            t = next((v for k, v in self.target_of_group.items() if k.lower() == low), None)
        return t

    def src_of(self, t: int) -> Optional[int]:
        return self.edit.bones[t].src

    def new_index(self, t: int) -> int:
        return self.plan.new_of_target[t]

    @property
    def new_rig_names(self) -> List[str]:
        """The rig bone of every bone of the WRITTEN skeleton, in its order (what a clip is sampled with)."""
        return [self.rig_names[t] for t in self.plan.order]


def read_rig(obj, raw: bytes) -> Optional[RigEdit]:
    """The skeleton edit the rig of part ``obj`` makes to the source bytes ``raw`` (an unchanged rig gives an edit that
    changes nothing), or None when the file has no skeleton. Raises ValueError, in words, for what cannot be written:
    Duplicate/Symmetrize copies that cannot be told apart, and everything ICParse's ``plan_edit`` refuses."""
    from ICParse.sgm.scene import skeleton as K
    from . import structure
    sk = K.read_skeleton(raw)
    if sk is None:
        return None
    arm = rig_of(obj)
    names = list(arm.get(_rig.KEY_BONE_SRC_NAMES, [])) if arm is not None else []
    if arm is None or len(names) != len(sk.bones):
        edit = K.identity_edit(sk)
        rn = [str(n) for n in names] if len(names) == len(sk.bones) else sk.names
        return RigEdit(sk, edit, K.plan_edit(sk, edit), rn, {n: i for i, n in enumerate(rn)}, {}, [])
    if arm.mode == "EDIT":
        arm.update_from_editmode()
    snap = list(zip([str(n) for n in names], [str(p) for p in arm.get(_rig.KEY_BONE_SRC_PARENTS, [""] * len(names))]))
    flat = list(arm.get(_rig.KEY_BONE_REST, []))
    srest = [tuple(float(x) for x in flat[16 * i:16 * i + 16]) for i in range(len(snap))] if len(flat) == 16 * len(snap) \
        else None
    bones = []
    for b in arm.data.bones:
        sid = b.get(_rig.KEY_BONE_SRC_INDEX)
        M = b.matrix_local
        bones.append(structure.RigBone(name=b.name, parent=b.parent.name if b.parent else "",
                                       src=None if sid is None else int(sid),
                                       rest=tuple(float(M[r][c]) for r in range(4) for c in range(4))))
    res = structure.resolve_bones(snap, bones, srest)
    if res.ambiguous or res.overwritten:
        raise ValueError("the rig's bones cannot be matched to the file's: " + "; ".join(res.changes()[:3]))
    targets, rig_names, changes = [], [], list(res.changes())
    deleted = {}
    for i, cur in enumerate(res.current):
        if cur is None:
            deleted[snap[i][0]] = sk.bones[i].name
            continue
        targets.append(K.TargetBone(sk.bones[i].name if cur == snap[i][0] else cur, -1, i, None))
        rig_names.append(cur)
    for name in res.added:
        targets.append(K.TargetBone(name, -1, None, None))
        rig_names.append(name)
    of = {n: t for t, n in enumerate(rig_names)}
    for t, name in enumerate(rig_names):
        b = arm.data.bones[name]
        tb = targets[t]
        tb.parent = of[b.parent.name] if b.parent is not None and b.parent.name in of else -1
        M = b.matrix_local
        flat16 = [float(M[r][c]) for r in range(4) for c in range(4)]
        if tb.src is None or srest is None or not np.allclose(flat16, srest[tb.src], atol=REST_TOL):
            tb.world = _rig.from_blender_matrix(M)
            if tb.src is not None:
                changes.append(f"bone '{name}' was moved")
    edit = K.SkeletonEdit(targets)
    return RigEdit(sk, edit, K.plan_edit(sk, edit), rig_names, of, deleted, changes)


def skin_slots(raw: bytes, part: int, rig_edit: RigEdit, skin):
    """``skin`` (per vertex ``[(target, weight)]``) in the palette slots that part number ``part`` of the WRITTEN file
    ``raw`` stores: by slot number for the model mesh of a file without a part group, by name for every other mesh."""
    from ICParse.sgm.scene import skeleton as K
    sk = K.read_skeleton(raw)
    u = next((m for m in sk.meshes if m.part == part), None) if sk is not None else None
    if u is None or not u.binds:
        raise ValueError(f"part {part} has no skin palette in the written file")
    plan = rig_edit.plan
    slot = {}
    for t in {b for infl in skin for b, _w in infl}:
        n = plan.new_of_target[t]
        if u.by_index:
            slot[t] = n
            continue
        low = plan.names[n].lower()
        k = next((k for k, nm in enumerate(u.names or []) if nm.lower() == low), None)
        if k is None:
            raise ValueError(f"bone '{plan.names[n]}' is not in the palette of part {part} (an add-on error)")
        slot[t] = k
    return [[(slot[b], w) for b, w in infl] for infl in skin]


def follow_bones_in_blueprint(ebp_bytes: bytes, obj) -> "tuple[bytes, list]":
    """The blueprint bytes with every event that names a renamed bone pointed at its new name (ICParse
    ``ebp.bone_refs``), and notes in words; events naming a deleted bone are listed (they now play at the model's
    origin). Unchanged bytes when the rig renames and deletes nothing."""
    from ICParse.ebp import parser as P, bone_refs as R
    from . import core
    src = obj.get("bz_source", "")
    if not src or not ebp_bytes:
        return ebp_bytes, []
    raw = open(core.resolve_source(src), "rb").read()
    try:
        re_ = read_rig(obj, raw)
    except ValueError:
        return ebp_bytes, []
    if re_ is None:
        return ebp_bytes, []
    ren, gone = _renamed_and_deleted(re_)
    if not ren and not gone:
        return ebp_bytes, []
    marks = [_name(m) for m, _d, _p in re_.sk.markers]
    e = P.read(ebp_bytes)
    n, lost = R.rename_event_bones(e, ren, gone, marks)
    notes = []
    if n:
        notes.append(f"blueprint: {n} event(s) follow a renamed bone")
    if lost:
        notes.append(f"blueprint: {len(lost)} event(s) name a deleted bone and now play at the model's origin: "
                     + ", ".join(f"{a or '?'} at '{b}'" for a, b in lost[:4]))
    return (P.write(e) if n else ebp_bytes), notes


def _renamed_and_deleted(re_) -> "tuple[dict, list]":
    """``({source name: new name}, [deleted source names])`` of a RigEdit."""
    ren = {}
    for tb in re_.edit.bones:
        if tb.src is not None and tb.name != re_.sk.bones[tb.src].name:
            ren[re_.sk.bones[tb.src].name] = tb.name
    return ren, list(re_.deleted_groups.values())


def sgm_only_notes(obj, out_path: str, notes: list) -> list:
    """The notes of an export that writes only the ``.sgm`` (File > Export), the ones about OTHER files first (the
    blueprint events and the including files that this export does not rewrite), so a report that shows only the first
    few lines never cuts them."""
    return blueprint_notes(obj) + includer_notes(obj, out_path) + list(notes)


def includer_notes(obj, out_path: str) -> list:
    """When the export goes under a game path (``.../art/...``, so it replaces the model there) and the rig renames or
    deletes bones: the stock files that include the model at that path (XREF nodes: cut scenes, structures such as the
    gyro copters), which are not rewritten and may name those bones. Empty otherwise."""
    from ICParse.sgm.scene import skeleton as K
    from . import core
    src = obj.get("bz_source", "")
    there = K.model_data_path(out_path) if out_path else None
    if not src or there is None:
        return []
    path = core.resolve_source(src)
    try:
        re_ = read_rig(obj, open(path, "rb").read())
    except (ValueError, OSError):
        return []
    if re_ is None:
        return []
    ren, gone = _renamed_and_deleted(re_)
    if not ren and not gone:
        return []
    files = K.including_files(there)
    if not files:
        return []
    names = [f.split(":", 1)[-1].rsplit("/", 1)[-1] for f in files]
    return [f"{len(files)} game file(s) include this model by its path and are not rewritten: {', '.join(names[:5])}"
            f"{' ...' if len(names) > 5 else ''}. If they name the renamed or deleted bone(s) "
            f"({', '.join(repr(n) for n in (list(ren) + gone)[:3])}), those parts stop following them"]


def _name(form):
    from ICParse.sgm.scene import skeleton as K
    return K._name(form)


def export_with_clips(context, obj, arm, legacy: bool = True, drop_morphs: bool = False, entity: str = "",
                      carry_over: bool = False, remove_missing: bool = False, refuse_errors: bool = True):
    """``(bytes, SceneExport, ClipPlan)``: the scene object with its geometry and skeleton edits
    (``scene.export_scene_object``), then its clips: Actions left as imported keep their bytes (re-based by the skeleton
    edit), edited and new ones are sampled from Blender against the written skeleton, deleted ones dropped. Renamed
    bones' F-curves count as unchanged (the clip follows the rename). ``entity``: the name new texture files take,
    ``remove_missing``: remove the parts whose object was deleted (``scene.export_scene_object``). A clip encoded here
    names every mesh of the written model, new parts included, with the visible key."""
    from . import actions, scene as _scene
    from ICParse.sgm.scene import model as SM
    geo = _scene.export_scene_object(obj, legacy=legacy, drop_morphs=drop_morphs, entity=entity, carry_over=carry_over,
                                     remove_missing=remove_missing, refuse_errors=refuse_errors)
    out = geo.data
    if arm is None:
        return out, geo, None
    gone = list((geo.rig_edit.deleted_groups if geo.rig_edit is not None else {}).keys())
    renames = {o: n for n, o in _rig.rename_canon(arm).items()}
    for act in actions.actions_of(arm):          # an Action Blender did not rename the paths of (a fake-user one)
        try:
            actions.fix_stale_bone_paths(act, renames)
        except ValueError as e:
            geo.notes.append(str(e))
    plan = actions.plan_export(arm, renames=True, deleted=gone)
    unchanged, encode, drop = plan
    if encode or drop:
        new_scene = SM.read_scene(out)
        names = geo.rig_edit.new_rig_names if geo.rig_edit is not None else [b.name for b in new_scene.bones]
        data = SimpleNamespace(bone_names=names, scene=new_scene)
        clips = actions.encode_scene_actions(arm, data, encode, context.scene.render.fps)
        out = SM.write_scene_animations(out, clips, drop)
    return out, geo, plan


# ------------------------------------------------------------------------------------------------ Delete Bones, Merge Weights
def merge_weights_into_parent(arm, bone_names) -> int:
    """Add every listed bone's vertex-group weights into its parent's group (created when missing) on every mesh the
    armature deforms, and remove the bone's group: what the game does with the weights of a deleted bone, made visible
    in Blender. Returns the number of groups merged."""
    merged = 0
    meshes = [o for o in bpy.data.objects if o.type == "MESH"
              and any(m.type == "ARMATURE" and m.object is arm for m in o.modifiers)]
    for name in bone_names:
        b = arm.data.bones.get(name)
        if b is None:
            continue
        parent = b.parent.name if b.parent is not None else None
        if parent is None:                       # a deleted root: the game hands its weights to the first bone
            first = next((x for x in arm.data.bones if x.get(_rig.KEY_BONE_SRC_INDEX) == 0), None)
            parent = first.name if first is not None and first.name not in bone_names else None
        for o in meshes:
            g = o.vertex_groups.get(name)
            if g is None:
                continue
            if parent is not None:
                pg = o.vertex_groups.get(parent) or o.vertex_groups.new(name=parent)
                for v in o.data.vertices:
                    w = next((x.weight for x in v.groups if x.group == g.index), 0.0)
                    if w > 0.0:
                        have = next((x.weight for x in v.groups if x.group == pg.index), 0.0)
                        pg.add([v.index], have + w, "REPLACE")
            o.vertex_groups.remove(g)
            merged += 1
    return merged


def blueprint_notes(obj) -> list:
    """For an export that writes only the ``.sgm``: the source blueprint's events that name a bone the rig renamed or
    deleted, in words (Export Entity Folder writes the blueprint with them renamed). Empty when there are none."""
    from . import core
    src = obj.get("bz_source", "")
    if not src:
        return []
    path = core.resolve_source(src)
    ebp = os.path.splitext(path)[0] + ".ebp"
    if not os.path.isfile(ebp):
        return []
    raw = open(ebp, "rb").read()
    _new, notes = follow_bones_in_blueprint(raw, obj)
    if not notes:
        return []
    return [n.replace("blueprint:", "the source blueprint (not written by this export; Export Entity Folder writes it):", 1)
            for n in notes]
