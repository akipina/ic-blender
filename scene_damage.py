"""Damage states of scene objects: the morph targets of a part (a building's damage shapes, a character's face shapes)
as Blender shape keys, the clips that weight them as shape-key Actions, and their way back into the ``.sgm``.

Import (:func:`build_shape_keys`, called by ``scene.build_scene``): a part with targets gets a ``Basis`` key and one
key per target in file order, named ``NN <clip>`` after a clip that drives it (``06 damage_03``, ``00 Tentunfold``;
``NN morph`` when none does), every value 0. Each clip with weights for the part becomes an Action on the part's shape
keys, keyed linearly as the game interpolates, on a muted NLA track named after the clip. The object keeps
``bz_morph_table``: each imported key's name, its target number and a digest of its offsets.

Export (:func:`plan_part` / :func:`write_part`, called by ``scene.export_scene_object``): the keys are read from their
own data (not the mix, so the preview slider never leaks into the file), relative to the reference key, and mapped to
the written vertices through ``BuiltPart.source_vertex``. A key whose offsets still match its digest over the same
vertex numbering keeps its stored bytes; an edited one is encoded; a new key is added; a deleted one is removed, and
the clips' weights follow the new numbering (ICParse ``renumber_morph_channels``). A part with targets is written in
the v2.0 layout (ICParse ``write_scene_part`` forces it). An edited shape-key Action replaces that clip's weights for
the part (ICParse ``set_morph_channels``); every other channel of the clip keeps its bytes.

The game moves positions only: a damaged shape is lit with the base mesh's normals, while Blender shades it with its own.
"""
from __future__ import annotations

import json
import os
import zlib
from dataclasses import dataclass, field

import numpy as np

import bpy
from .sceneobj import ui_kit
from bpy.props import FloatProperty

from . import core

KEY_TABLE = "bz_morph_table"     # on a part object: JSON [{"name", "index", "digest"}] of the keys made at import
KEY_MESH = "bz_morph_mesh"       # on a part object: the part's mesh name, the clips' address of its targets
KEY_CLIP = "bz_morph_clip"       # on a shape-key Action: the clip it is
KEY_HASH = "bz_morph_hash"       # on a shape-key Action: its content at import ("" for one made in Blender)
KEY_FRAMES = "bz_morph_frames"   # on a shape-key Action: the clip's frame count (time 0..1 = frames 1 .. 1 + count)
KEY_ADDED = "bz_damage_added"    # on a part object: damage clips added in Blender (JSON list)
KEY_HIDDEN = "bz_damage_hidden"  # on a part object: hidden by the damage preview


def _digest(off) -> str:
    return str(zlib.crc32(np.ascontiguousarray(np.asarray(off, dtype=np.float32)).tobytes()) & 0xFFFFFFFF)


def _key_path(name: str) -> str:
    return 'key_blocks["' + str(name).replace("\\", "\\\\").replace('"', '\\"') + '"].value'


def _key_of_path(path: str):
    p = str(path)
    if not (p.startswith('key_blocks["') and p.endswith('"].value')):
        return None
    import re
    return re.sub(r"\\(.)", r"\1", p[len('key_blocks["'):-len('"].value')])


def mesh_name(data, part) -> str:
    """The name the clips address the part by (its PMSH NAME, unstripped)."""
    sc = data.scene
    if sc is not None and 0 <= part.mesh_index < len(sc.meshes):
        return sc.meshes[part.mesh_index].name or part.name
    return part.name


def target_names(scene, mesh: str, count: int) -> list:
    """``NN <clip>`` per target: the first damage_NN clip that drives it, else its most animated clip, else ``morph``."""
    users = {k: [] for k in range(count)}
    for clip, _frames, tracks in (getattr(scene, "morph_animations", None) or []):
        for (m, sub), keys in tracks.items():
            if m.lower() == mesh.lower() and 0 <= sub < count:
                users[sub].append((clip, len(keys)))

    def rank(u):
        clip, nk = u
        return (0, clip.lower(), 0) if clip.lower().startswith("damage_") else (1, -nk, clip.lower())
    return [f"{k:02d} {sorted(users[k], key=rank)[0][0] if users[k] else 'morph'}" for k in range(count)]


def _co(coll, n):
    a = np.empty(n * 3, dtype=np.float32)
    coll.foreach_get("co", a)
    return a.reshape(-1, 3)


def table(obj):
    raw = obj.get(KEY_TABLE)
    if not raw:
        return None
    try:
        return json.loads(str(raw))
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------------------------ import
def build_shape_keys(obj, data, part, names=None, digests=None) -> list:
    """Shape keys for ``part.morphs`` (Blender-space offsets per vertex) on the part object ``obj``; returns the keys."""
    if not part.morphs:
        return []
    me = obj.data
    nv = len(me.vertices)
    mesh = mesh_name(data, part)
    names = names or target_names(data.scene, mesh, len(part.morphs))
    if me.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    base = _co(me.vertices, nv)
    made, rows = [], []
    for i, (nm, off) in enumerate(zip(names, part.morphs)):
        kb = obj.shape_key_add(name=nm, from_mix=False)
        kb.data.foreach_set("co", (base + np.asarray(off, dtype=np.float32).reshape(nv, 3)).ravel())
        kb.value = 0.0
        got = _co(kb.data, nv) - base
        rows.append({"name": kb.name, "index": i, "digest": digests[i] if digests is not None else _digest(got)})
        made.append(kb)
    obj[KEY_TABLE] = json.dumps(rows)
    obj[KEY_MESH] = mesh
    me.update()
    return made


def restore_stored_keys(obj, data, part) -> int:
    """Before keys are added to a part that has stored targets but no shape keys and no key table (imported by an older
    version): make the stored targets shape keys and their clips Actions, as an import does, so they are not counted as
    deleted on export. Refuses (ValueError) when the mesh is no longer the file's, since the stored targets then name
    other vertices. Returns how many keys were made (0: nothing to do)."""
    if not part.morphs or obj.data.shape_keys is not None or table(obj):
        return 0
    me = obj.data
    nv = len(me.vertices)
    ref = np.asarray(part.verts, dtype=np.float64)
    if nv != len(ref) or not np.allclose(_co(me.vertices, nv), ref, atol=1e-4):
        raise ValueError(damage_states_refusal(obj.name, len(part.morphs)))
    made = build_shape_keys(obj, data, part)
    import_key_clips(obj, data, part)
    return len(made)


def import_key_clips(obj, data, part) -> list:
    """One Action per clip that weights this part's targets, on muted NLA tracks of the part's shape keys."""
    from . import actions
    key = obj.data.shape_keys
    rows = table(obj) or []
    if key is None or not rows:
        return []
    by_index = {r["index"]: r["name"] for r in rows}
    mesh = mesh_name(data, part).lower()
    made = []
    for clip, frames, tracks in (getattr(data.scene, "morph_animations", None) or []):
        mine = {sub: keys for (m, sub), keys in tracks.items() if m.lower() == mesh and sub in by_index}
        if not mine:
            continue
        K = max(1, int(frames))
        act = make_key_action(key, clip, {by_index[s]: keys for s, keys in mine.items()}, K)
        act[KEY_HASH] = actions.action_hash(act)
        actions.stash(key, act, clip, 1)
        made.append(act)
    return made


def make_key_action(key, clip: str, weights: dict, frames: int):
    """An Action on the shape keys ``key``: ``weights`` {key name: [(time 0..1, weight), ...]}, linear keys at frame
    ``1 + time * frames``; tagged with its clip and frame count."""
    from . import actions
    act = bpy.data.actions.new(clip)
    act.use_fake_user = True
    ad = key.animation_data or key.animation_data_create()
    prev, prev_slot = ad.action, getattr(ad, "action_slot", None)
    actions._assign(key, act)
    for name, keys in weights.items():
        fc = actions._fcurve(act, key, _key_path(name), 0, "Shape keys")
        fc.keyframe_points.add(len(keys))
        co = np.asarray([(1.0 + float(t) * frames, float(v)) for t, v in keys], dtype=np.float64).ravel()
        fc.keyframe_points.foreach_set("co", co)
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
        fc.update()
    ad.action = prev
    try:
        if prev is not None and prev_slot is not None:
            ad.action_slot = prev_slot
    except Exception:                                          # noqa: BLE001 -- older Blender: no slots
        pass
    act[KEY_CLIP] = str(clip)
    act[KEY_FRAMES] = int(frames)
    act[KEY_HASH] = ""
    return act


def key_actions(obj) -> list:
    """The shape-key Actions of a part: on its keys' NLA tracks or active, and tagged with a clip."""
    key = obj.data.shape_keys if obj is not None and obj.type == "MESH" else None
    ad = key.animation_data if key is not None else None
    out, seen = [], set()
    if ad is None:
        return out
    cands = ([ad.action] if ad.action is not None else []) + [s.action for t in ad.nla_tracks for s in t.strips if s.action]
    for a in cands:
        if a.get(KEY_CLIP) is not None and a.as_pointer() not in seen:
            seen.add(a.as_pointer())
            out.append(a)
    return out


def clip_weights(action) -> dict:
    """``{key name: [(time 0..1, weight), ...]}`` of a shape-key Action (its keyframes; the game interpolates linearly)."""
    from . import actions
    K = max(1, int(action.get(KEY_FRAMES, 2) or 2))
    out = {}
    for fc in actions._fcurves(action):
        name = _key_of_path(fc.data_path)
        if name is None:
            continue
        n = len(fc.keyframe_points)
        co = np.empty(2 * n, dtype=np.float64)
        fc.keyframe_points.foreach_get("co", co)
        out[name] = [(max(0.0, (f - 1.0) / K), float(v)) for f, v in zip(co[0::2], co[1::2])]
    return out


# ------------------------------------------------------------------------------------------------------------ export
@dataclass
class MorphPlan:
    specs_built: object = None   # morph list over the written vertices (write_scene_part), or None: no morph work
    specs_file: object = None    # the same over the file's own vertices (write_scene_morphs), when the mesh is the file's
    untouched: bool = True       # nothing to write for the targets
    names: list = field(default_factory=list)    # the key name of every written target, in order
    notes: list = field(default_factory=list)
    transfer: object = None
    left_out: int = 0


def damage_states_refusal(name: str, count: int) -> str:
    return (f"'{name}' has damage states ({count} stored shapes) but no shape keys for them (it was imported before damage states "
            "became shape keys, or they were never made). Import it again to edit them, use 'Carry Over Damage States', or tick "
            "\"Leave damage states out of edited parts\" in File > Export or Export Entity Folder")


def key_offsets(obj) -> list:
    """``[(name, (nv, 3) float32 offsets from the reference key, digest)]`` for every key but the reference one."""
    me = obj.data
    sk = me.shape_keys
    if sk is None or len(sk.key_blocks) < 2:
        return []
    if not sk.use_relative:
        raise ValueError(f"'{obj.name}': its shape keys are absolute (Relative is off); damage states are relative to the Basis")
    ref = sk.reference_key
    nv = len(me.vertices)
    rco = _co(ref.data, nv)
    if not np.allclose(rco, _co(me.vertices, nv), atol=1e-5):
        raise ValueError(f"'{obj.name}': its mesh and its Basis shape key differ; leave Edit Mode on the Basis key or "
                         "apply the mix, then export again")
    out = []
    for kb in sk.key_blocks:
        if kb == ref:
            continue
        off = _co(kb.data, nv) - rco
        out.append((kb.name, off, _digest(off), kb.relative_key != ref))
    return out


def plan_part(obj, data, part, built, unchanged: bool, src_form, drop: bool = False, carry_over: bool = False) -> MorphPlan:
    """What to write for the targets of one part (see the module docstring)."""
    from ICParse.sgm.scene import morph as MO
    n_src = len(MO.part_morph_targets(src_form)) if src_form is not None else 0
    rows = table(obj)
    if n_src and not rows:
        rows = None                                    # an empty table names none of the stored targets: as if there were none
    keys = key_offsets(obj)
    nv_src, nv_bl = len(part.verts), len(obj.data.vertices)
    src_v = built.source_vertex
    same_numbering = len(src_v) == nv_src and np.array_equal(src_v, np.arange(nv_src))
    plan = MorphPlan()
    if not keys and not n_src:
        return plan
    if drop and not unchanged:
        plan.specs_built, plan.untouched, plan.left_out = [], False, n_src
        if n_src:
            plan.notes.append(f"{obj.name}: its {n_src} damage states were left out (the part was edited)")
        return plan
    if not keys:
        if rows is None and unchanged:
            if same_numbering:                                 # imported before shape keys: what the file has still fits
                plan.specs_built = [("keep", i) for i in range(n_src)]
            return plan
        if carry_over and not unchanged:
            keys, plan.transfer = carry_over_keys(obj, data, part, build=False)
            plan.notes.append(f"{obj.name}: {plan.transfer.summary()}")
            rows = [{"name": k[0], "index": i, "digest": ""} for i, k in enumerate(keys)]   # carried target i is stored target i
        elif rows is None:
            raise ValueError(damage_states_refusal(obj.name, n_src))
        else:
            plan.specs_built = plan.specs_file = []
            plan.untouched = False
            plan.notes.append(f"{obj.name}: its {n_src} damage states were removed (their shape keys were deleted)")
            return plan
    if n_src and rows is None and plan.transfer is None:
        raise ValueError(f"'{obj.name}' has shape keys but was imported before damage states became shape keys, so they cannot be "
                         "matched with the file's: import it again, or delete its shape keys")
    by_name = {r["name"]: r for r in (rows or [])}
    built_specs, file_specs, used = [], [], set()
    for name, off, dg, rel_other in keys:
        if rel_other:
            plan.notes.append(f"{obj.name}: shape key '{name}' is relative to another key; it is written relative to the Basis")
        r = by_name.get(name)
        idx = int(r["index"]) if r is not None and 0 <= int(r["index"]) < n_src and int(r["index"]) not in used else None
        out_b = core.blender_to_sgm(off[src_v].astype(np.float64))
        out_f = core.blender_to_sgm(off.astype(np.float64)) if nv_bl == nv_src else None
        if idx is None:
            built_specs.append(out_b)
            file_specs.append(out_f)
        else:
            used.add(idx)
            same = r.get("digest") == dg
            built_specs.append(("keep", idx) if same and same_numbering else ("set", idx, out_b))
            file_specs.append(("keep", idx) if same else ("set", idx, out_f))
        plan.names.append(name)
    plan.specs_built = built_specs
    plan.specs_file = file_specs if nv_bl == nv_src else None
    plan.untouched = unchanged and nv_bl == nv_src and len(file_specs) == n_src and all(
        isinstance(s, tuple) and s[0] == "keep" and s[1] == i for i, s in enumerate(file_specs))
    return plan


def write_part(raw: bytes, part, built, plan: MorphPlan, legacy: bool, unchanged: bool, force: bool, report: dict,
               skin=None) -> bytes:
    """Write one part: only its targets when its mesh is the file's (write_scene_morphs; a v1.2 part is rebuilt as v2.0
    instead), else the whole part with its targets over the new vertices. Fills ``report['morphs']``. ``skin``: the
    weights in the part's palette slots when the skeleton was written (``scene_skeleton.skin_slots``), else ``built.skin``."""
    from ICParse.sgm.scene import model as SM
    from ICParse.sgm.scene import morph as MO
    if unchanged and not force and plan.specs_file is not None:
        forms = SM.scene_pmsh_forms(SM.IffDocument.from_bytes(raw))
        if SM._pmsh_version(forms[part.mesh_index]) >= 0x200:
            return MO.write_scene_morphs(raw, part.mesh_index, plan.specs_file, report=report)
    return SM.write_scene_part(
        raw, part.mesh_index, [tuple(p) for p in built.positions.tolist()], [tuple(n) for n in built.normals.tolist()],
        [tuple(t) for t in built.uvs.tolist()], built.skin if skin is None else skin, built.submeshes, legacy=legacy,
        morphs=plan.specs_built if plan.specs_built is not None else "check", report=report)


def after_write(raw: bytes, mesh: str, mw, notes: list) -> bytes:
    """Make the clips follow a renumbering of the part's targets (a removed target's weights are dropped and named)."""
    from ICParse.sgm.scene import morph as MO
    if mw is not None and (mw.renumbered or mw.removed):
        raw = MO.renumber_morph_channels(raw, mesh, mw.index_map(), notes)
    return raw


def write_key_clips(raw: bytes, objs, data, written: dict, notes: list) -> bytes:
    """Write every edited shape-key Action into its clip (the part's weights only). ``written``: part number -> the key
    names of its written targets, in order (parts whose targets were rewritten); other parts use their import table."""
    from ICParse.sgm.scene import morph as MO
    from . import actions
    for o in objs:
        pi = int(o.get("bz_scene_part", -1))
        if pi < 0 or pi >= len(data.parts):
            continue
        part = data.parts[pi]
        names = written.get(pi)
        if names is None:
            names = [r["name"] for r in sorted(table(o) or [], key=lambda r: r["index"])]
        index = {n: i for i, n in enumerate(names)}
        for act in key_actions(o):
            if act.get(KEY_HASH) and str(act[KEY_HASH]) == actions.action_hash(act):
                continue                     # as imported: the clip keeps its bytes (a renumbering already moved them)
            weights, lost = {}, []
            for name, keys in clip_weights(act).items():
                if name in index:
                    weights[index[name]] = keys
                else:
                    lost.append(name)
            if lost:
                notes.append(f"clip '{act[KEY_CLIP]}': keys for {', '.join(lost)} were not written (no such shape key on '{o.name}')")
            raw = MO.set_morph_channels(raw, str(act[KEY_CLIP]), mesh_name(data, part), weights,
                                        frame_count=int(act.get(KEY_FRAMES, 2) or 2), fps=float(bpy.context.scene.render.fps))
            notes.append(f"clip '{act[KEY_CLIP]}': the damage-state weights of '{o.name}' were written from its Action")
    return raw


# ------------------------------------------------------------------------------------------------------------ carry over
def _vertex_uvs(me):
    uvl = me.uv_layers.active
    if uvl is None:
        return None
    nl = len(me.loops)
    lv = np.empty(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", lv)
    uv = np.empty(nl * 2, dtype=np.float64)
    uvl.data.foreach_get("uv", uv)
    out = np.zeros((len(me.vertices), 2))
    out[lv[::-1]] = uv.reshape(-1, 2)[::-1]                    # the first corner of each vertex
    return out


def carry_over_keys(obj, data, part, build: bool = True, max_dist=None):
    """The source part's damage states carried onto ``obj``'s current mesh by surface position (ICParse
    ``transfer_morphs``). ``build``: make them as shape keys (replacing any) and return ``(keys, report)``; otherwise
    return ``([(name, offsets, "", False)], report)`` in the form :func:`key_offsets` gives."""
    from ICParse.sgm.scene import morph as MO
    if not part.morphs:
        raise ValueError(f"'{obj.name}' had no damage states in its source file")
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    me = obj.data
    nv = len(me.vertices)
    co = _co(me.vertices, nv).astype(np.float64)
    nrm = np.empty(nv * 3)
    me.vertices.foreach_get("normal", nrm)
    new, rep = MO.transfer_morphs(part.verts, part.tris, part.morphs, co, nrm.reshape(-1, 3), max_dist=max_dist,
                                  old_normals=part.normals if len(part.normals) else None,
                                  old_uvs=part.uvs if len(part.uvs) else None, new_uvs=_vertex_uvs(me))
    names = target_names(data.scene, mesh_name(data, part), len(part.morphs))
    if not build:
        return [(n, d.astype(np.float32), "", False) for n, d in zip(names, new)], rep
    if me.shape_keys is not None:
        obj.shape_key_clear()
    tmp = core.ScenePart(name=part.name, verts=co, normals=np.zeros((0, 3)), uvs=np.zeros((0, 2)),
                         tris=part.tris, tri_mat=part.tri_mat, skin=[], mesh_index=part.mesh_index)
    tmp.morphs = new
    keys = build_shape_keys(obj, data, tmp, names=names, digests=["carried"] * len(names))
    return keys, rep


# ------------------------------------------------------------------------------------------------------------ levels
def blueprint_of(src: str):
    """The ``.ebp`` beside a scene model's source on disk (same name, any case), parsed; None when there is none."""
    from ICParse.ebp import parser as EP
    path = core.resolve_source(src or "")
    if not path:
        return None
    if path.lower().endswith(".ebp"):
        cand = path
    else:
        d, base = os.path.split(path)
        want = os.path.splitext(base)[0].lower() + ".ebp"
        cand = next((os.path.join(d, f) for f in (os.listdir(d) if os.path.isdir(d) else []) if f.lower() == want), None)
    if not cand or not os.path.isfile(cand):
        return None
    try:
        st = os.stat(cand)
        stamp = (st.st_mtime_ns, st.st_size)
        hit = _BLUEPRINTS.get(cand)
        if hit is not None and hit[0] == stamp:                # the health slider asks on every step; the file is read again when it changes
            return hit[1]
        bp = EP.read(cand)
        if len(_BLUEPRINTS) > 8:
            _BLUEPRINTS.clear()
        _BLUEPRINTS[cand] = (stamp, bp)
        return bp
    except Exception:                                          # noqa: BLE001 -- a broken blueprint: the generated rule
        return None


_BLUEPRINTS: dict = {}
_LOADED: dict = {}                                             # source -> (file stamp, the decoded scene) for the health slider


def loaded_scene(src: str):
    """``core.load_scene(src)``, kept while the source file is unchanged: every step of the health slider needs it, and decoding the textures
    takes a while. Only the preview reads it (it is never edited)."""
    try:
        stamp = core._source_stamp(core.resolve_source(src))
    except (OSError, ValueError):
        return core.load_scene(src)
    hit = _LOADED.get(src)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = core.load_scene(src)
    if len(_LOADED) > 4:
        _LOADED.clear()
    _LOADED[src] = (stamp, data)
    return data


def clip_names(data) -> list:
    sc = data.scene
    names = [a.name for a in data.animations] + [c[0] for c in (getattr(sc, "channel_animations", None) or [])]
    return list(dict.fromkeys(names))


def levels_of(data, src: str, objs=()) -> list:
    """The damage levels the game plays for this model (ICParse ``damage_levels``), plus levels added in Blender."""
    from ICParse.sgm.scene import morph as MO
    names = clip_names(data)
    for o in objs:
        names += json.loads(str(o.get(KEY_ADDED, "[]")))
    return MO.damage_levels(names, blueprint_of(src))


def _value_at(keys, t):
    if not keys:
        return 0.0
    ts = [k[0] for k in keys]
    vs = [k[1] for k in keys]
    return float(np.interp(t, ts, vs))


def preview(objs, data, src: str, health: float) -> list:
    """Show the damage the game shows at ``health`` (0..1): the shape-key values of the playing level's clip (its last
    frame for a level that does not loop, its first for one that loops) and the parts its visibility channels hide. Bone
    poses of the damage clips are not shown. Returns the clips that play."""
    from ICParse.sgm.scene.model import scene_visibility
    levels = levels_of(data, src, objs)
    damage = 1.0 - float(health)
    active = [lv for lv in levels if lv.active(damage)]
    by_mesh = {}
    for o in objs:
        sk = o.data.shape_keys if o.type == "MESH" else None
        if sk is not None:
            for kb in sk.key_blocks:
                if kb != sk.reference_key:
                    kb.value = 0.0
        if o.get(KEY_HIDDEN):
            o.hide_set(False)
            del o[KEY_HIDDEN]
        pi = int(o.get("bz_scene_part", -1))
        if 0 <= pi < len(data.parts):
            by_mesh[mesh_name(data, data.parts[pi]).lower()] = o
    for lv in active:
        t = 0.0 if lv.loop else 1.0
        for o in objs:
            for act in key_actions(o):
                if str(act[KEY_CLIP]).lower() != lv.clip.lower():
                    continue
                sk = o.data.shape_keys
                for name, keys in clip_weights(act).items():
                    kb = sk.key_blocks.get(name)
                    if kb is not None:
                        kb.slider_min = min(kb.slider_min, -10.0)
                        kb.slider_max = max(kb.slider_max, 10.0)
                        kb.value = kb.value + _value_at(keys, t)
        for mesh, keys in scene_visibility(data.scene, lv.clip).items():
            o = by_mesh.get(mesh.lower())
            if o is not None and _value_at(keys, t) < 0.5:
                o.hide_set(True)
                o[KEY_HIDDEN] = True
    return [lv.clip for lv in active]


# ------------------------------------------------------------------------------------------------------------ UI
def _part(context):
    obj = context.active_object
    if obj is None or obj.type != "MESH" or not obj.get("bz_scene") or obj.get("bz_scene_part") is None:
        return None
    return obj


def _health_changed(self, context):
    obj = _part(context)
    if obj is None:
        return
    from . import scene as bzscene
    try:
        data = loaded_scene(obj.get("bz_source", ""))
        playing = preview(bzscene.part_objects(obj), data, obj.get("bz_source", ""), self.bz_damage_health)
    except Exception as e:                                    # noqa: BLE001 -- a preview never raises into the UI
        print(f"IC Blender: damage preview: {e}")
        return
    context.window_manager["bz_damage_playing"] = ", ".join(playing) if playing else "none"


class BZ_OT_carry_over_damage(bpy.types.Operator):
    """Carry the source part's damage states over onto this mesh (by surface position), as shape keys"""
    bl_idname = "bz.carry_over_damage"
    bl_label = "Carry Over Damage States"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _part(context) is not None

    def execute(self, context):
        obj = _part(context)
        try:
            data = core.load_scene(obj.get("bz_source", ""))
            part = data.parts[int(obj["bz_scene_part"])]
            was_edit = obj.mode == "EDIT"
            if was_edit:
                bpy.ops.object.mode_set(mode="OBJECT")
            _keys, rep = carry_over_keys(obj, data, part)
            if rep.out_of_reach:
                sel = np.zeros(len(obj.data.vertices), dtype=bool)
                sel[rep.out_of_reach] = True
                obj.data.vertices.foreach_set("select", sel)
            if was_edit or rep.out_of_reach:
                bpy.ops.object.mode_set(mode="EDIT")
        except Exception as e:                                # noqa: BLE001
            self.report({"ERROR"}, f"Carry over failed: {e}")
            return {"CANCELLED"}
        self.report({"WARNING"} if rep.out_of_reach else {"INFO"},
                    rep.summary() + (" (those vertices are selected)" if rep.out_of_reach else ""))
        return {"FINISHED"}


SPACING_WORDS = ("The levels are spread evenly over the damage, so adding one moves the others: four levels show from 25, 50, 75 and "
                 "99.99 % damage, five from 20, 40, 60, 80 and 99.99 %. The newest level shows only when the building is all but destroyed. "
                 "This is the game's own spacing for the damage clips of a model without a Damage node; "
                 "a model that has one keeps whatever bands its Damage node holds, so Add Damage Level re-spaces them the same way.")


def spacing_text(clips) -> str:
    """The levels the game makes of the damage clips ``clips``, in plain words: ``5 levels, showing from 20, 40, 60, 80 and 99.99 % damage``."""
    from ICParse.sgm.scene.morph import generated_damage_levels
    lv = generated_damage_levels(clips)
    if not lv:
        return "no levels"
    starts = [f"{lv_.low * 100:g}" for lv_ in lv]
    return f"{len(lv)} level{'s' if len(lv) != 1 else ''}, showing from " + (", ".join(starts[:-1]) + " and " + starts[-1] if len(starts) > 1 else starts[0]) + " % damage"


class BZ_OT_add_damage_level(bpy.types.Operator):
    __doc__ = "Add a damage level: a new shape key on this part (shape it, then export) and the clip that shows it. " + SPACING_WORDS
    bl_idname = "bz.add_damage_level"
    bl_label = "Add Damage Level"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _part(context) is not None and context.active_object.mode == "OBJECT"

    def execute(self, context):
        obj = _part(context)
        from . import scene as bzscene
        try:
            data = core.load_scene(obj.get("bz_source", ""))
            part = data.parts[int(obj["bz_scene_part"])]
            objs = bzscene.part_objects(obj)
            names = {n.lower() for n in clip_names(data)}
            for o in objs:
                names |= {n.lower() for n in json.loads(str(o.get(KEY_ADDED, "[]")))}
            n = 1
            while f"damage_{n:02d}" in names:
                n += 1
            clip = f"damage_{n:02d}"
            restored = restore_stored_keys(obj, data, part)
            if obj.data.shape_keys is None:
                obj.shape_key_add(name="Basis", from_mix=False)
                obj[KEY_TABLE] = obj.get(KEY_TABLE) or "[]"
                obj[KEY_MESH] = mesh_name(data, part)
            count = len(obj.data.shape_keys.key_blocks) - 1
            kb = obj.shape_key_add(name=f"{count:02d} {clip}", from_mix=False)
            kb.value = 1.0
            obj.active_shape_key_index = len(obj.data.shape_keys.key_blocks) - 1
            act = make_key_action(obj.data.shape_keys, clip, {kb.name: [(0.0, 1.0)]}, 2)
            from . import actions
            actions.stash(obj.data.shape_keys, act, clip, 1)
            added = json.loads(str(obj.get(KEY_ADDED, "[]")))
            obj[KEY_ADDED] = json.dumps(added + [clip])
        except Exception as e:                                # noqa: BLE001
            self.report({"ERROR"}, f"Add damage level failed: {e}")
            return {"CANCELLED"}
        bp = blueprint_of(obj.get("bz_source", ""))
        from ICParse.sgm.scene import morph as MO
        tail = (" The blueprint has its own damage levels: Export Entity Folder adds this one to it."
                if bp is not None and MO.authored_damage_levels(bp) is not None else
                " The game makes the levels from the damage clips by itself.")
        if restored:
            tail += f" The part's {restored} stored damage states were made shape keys first."
        try:
            now = spacing_text(clip_names(data) + [c for o in objs for c in json.loads(str(o.get(KEY_ADDED, "[]")))])
            tail += f" The levels are spread evenly, so every level moved: now {now}."
        except Exception:                                     # noqa: BLE001 -- the words are a help, never a failure
            pass
        self.report({"INFO"}, f"Added shape key '{kb.name}' and clip '{clip}' (the most damaged level): shape the key, then "
                              f"export." + tail)
        return {"FINISHED"}


class BZ_PT_damage_states(bpy.types.Panel):
    bl_label = "Damage States"
    bl_idname = "BZ_PT_damage_states"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_parent_id = "BZ_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _part(context) is not None

    def draw(self, context):
        obj = _part(context)
        col = self.layout.column()
        sk = obj.data.shape_keys
        n = len(sk.key_blocks) - 1 if sk is not None else 0
        col.label(text=f"{n} damage states (shape keys)" if n else "no damage states on this part", icon="SHAPEKEY_DATA")
        col.label(text=f"{len(key_actions(obj))} clips use them", icon="ACTION")
        col.prop(context.window_manager, "bz_damage_health", slider=True)
        col.label(text=f"plays: {context.window_manager.get('bz_damage_playing', '-')}")
        col.operator(BZ_OT_carry_over_damage.bl_idname, icon="MOD_DATA_TRANSFER")
        col.operator(BZ_OT_add_damage_level.bl_idname, icon="ADD")


CLASSES = (BZ_OT_carry_over_damage, BZ_OT_add_damage_level, BZ_PT_damage_states)


def register():
    bpy.types.WindowManager.bz_damage_health = FloatProperty(
        name="Preview health", description="Show the damage the game shows at this health (1 = undamaged)",
        min=0.0, max=1.0, default=1.0, update=_health_changed)


def unregister():
    try:
        del bpy.types.WindowManager.bz_damage_health
    except AttributeError:
        pass


def blueprint_with_added_levels(objs, ebp_data: bytes):
    """The blueprint bytes with a motion node for every damage clip added in Blender (Add Damage Level), when the
    blueprint has its own Damage node (ICParse ``add_damage_level``); without one the game makes the levels from the
    clips itself and the bytes are returned as they are. Returns ``(bytes, notes)``."""
    from ICParse.ebp.damage import add_damage_level
    added = []
    for o in objs:
        added += [c for c in json.loads(str(o.get(KEY_ADDED, "[]"))) if c not in added]
    notes = []
    for clip in sorted(added):
        try:
            new, levels = add_damage_level(ebp_data, clip)
        except ValueError:
            continue                                           # already a level of this blueprint
        if levels is None:
            notes.append(f"damage level '{clip}': the game makes the levels from the damage clips by itself")
            break
        ebp_data = new
        band = next((lv for lv in levels if lv.clip == clip), None)
        notes.append(f"blueprint: damage level '{clip}' added (it plays below {1.0 - band.low:.2%} health)" if band else
                     f"blueprint: damage level '{clip}' added")
    return ebp_data, notes
