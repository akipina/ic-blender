"""Stand-in for a placed cut-scene (NIS) object, for the replay (how the game reads it: ``ICParse.sgm.nis_scene``).

:func:`build_nis` makes a separate collection (armature, meshes, NLA-layered clips, camera, timed events);
times are seconds after "NIS start", and unplayed channels are listed in ``notes``. Never change the placed object or
write ``ic_*`` keys: the map save must not see any of this.
"""
from __future__ import annotations

import bisect
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field

import numpy as np

import bpy
from mathutils import Quaternion

from ICParse.sgm import nis_scene as NS

from . import ops_map_import as M
from . import prefs

K_NIS = "ic_nis_standin"            # on the stand-in collection and its root: the blueprint it was built from
PREFIX_CLIP = "NIS clip "

# SGM (x, y, z) -> Blender (-x, -z, y): the add-on's axes (rig._B, a reflection); a transform M maps as B M B^-1
_B3 = np.array([[-1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])
_B4 = np.eye(4)
_B4[:3, :3] = _B3


class NisError(Exception):
    """A plain-words reason why an NIS object could not be built."""


@dataclass
class NisBuild:
    """What :func:`build_nis` made. ``events`` are dicts ``{"t", "kind", ...}``, ``t`` in seconds after "NIS start"."""
    entity: object = None                      # the placed object (never changed)
    blueprint: str = ""                        # art/ebps/nis/nis_2.ebp
    collection: object = None                  # the stand-in collection
    root: object = None                        # the empty at the placed object's transform
    objects: list = field(default_factory=list)    # every object made (root, armature, parts, camera)
    armature: object = None
    camera: object = None                      # the NIS camera (None when the model has none)
    action: object = None                      # the camera's Action (transform keys, one per frame)
    clip_actions: list = field(default_factory=list)   # the bone Actions, one per clip, in start order
    frame_start: int = 1
    frame_end: int = 1
    fps: float = 30.0
    duration_s: float = 0.0
    start_frame: int = 1
    schedule: list = field(default_factory=list)       # [{"clip", "start_s", "duration_s", "priority"}]
    cuts: list = field(default_factory=list)           # [(seconds, camera name)] the camera cuts
    events: list = field(default_factory=list)
    parts: dict = field(default_factory=dict)          # counts: own meshes, borrowed parts built / skipped
    visible: dict = field(default_factory=dict)        # {node index: the part object} -- shown / hidden by the clips
    notes: list = field(default_factory=list)
    _model: object = field(default=None, repr=False)
    _sched: list = field(default_factory=list, repr=False)

    def show_between(self, first_shown: int, last_shown: int, first_frame: int = 1) -> None:
        """Show the stand-in from frame ``first_shown`` to ``last_shown`` (hidden before and after): every object except the camera
        gets constant keys on ``hide_viewport`` / ``hide_render``. A part the clips show and hide (``self.visible``) is shown
        there only while its clip visibility says so, so the replay can lay the whole cut-scene between "NIS start" and "NIS end"
        with this one call instead of keying the objects itself. ``first_frame`` is the frame of time 0 (the first one hidden)."""
        show_between(self, first_shown, last_shown, first_frame)


# ---------------------------------------------------------------------------------------------------- small pure helpers
def scene_fps(context) -> float:
    r = context.scene.render
    return float(r.fps) / float(r.fps_base or 1.0)


def _mat_to_quat(R: np.ndarray) -> np.ndarray:
    """3x3 rotation -> (w, x, y, z)."""
    t = np.trace(R)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    q = np.array(q, dtype=np.float64)
    return q / np.linalg.norm(q)


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product of (w, x, y, z) quaternions, broadcast over leading dimensions."""
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw], axis=-1)


_QB = _mat_to_quat(-_B3)                                  # -B is a rotation, and conjugating by it is conjugating by B
_QB_CONJ = _QB * np.array([1.0, -1.0, -1.0, -1.0])


def bone_constants(bind_world, parents):
    """Per bone ``(C_R 3x3, C_t 3, P quaternion wxyz)`` for turning an absolute-local SGM key into the pose bone's ``matrix_basis``:
    ``basis = Rb_i^-1 @ Rb_parent @ (B L B^-1)`` where ``Rb`` is a bind world in Blender's axes (the algebra of
    ``actions.basis_samples``, which does it through whole-skeleton worlds frame by frame; the parent's animation cancels, so a
    bone's basis depends on its own keys only). ``P = q(C_R) * qB`` so that a key quaternion ``q`` gives ``P * q * qB^-1``."""
    Rb = [_B4 @ np.asarray(w, dtype=np.float64) @ _B4.T for w in bind_world]           # B^-1 = B^T (orthogonal)
    out = []
    for i in range(len(Rb)):
        p = parents[i] if parents[i] is not None else -1
        C = np.linalg.inv(Rb[i]) @ (Rb[int(p)] if 0 <= int(p) < len(Rb) and int(p) != i else np.eye(4))
        R = C[:3, :3]
        n = np.linalg.norm(R, axis=0)
        qc = _mat_to_quat(R / np.where(n < 1e-12, 1.0, n))
        out.append((R, C[:3, 3].copy(), _qmul(qc, _QB)))
    return out


def densify_rotations(keys, wide_dot: float = 0.95, step_deg: float = 18.0) -> np.ndarray:
    """Rotation keys ``(n, 5)`` (time, x, y, z, w) with extra keys inside every wide gap. The game blends two keys on the sphere
    (``Slerp``, ``AnimStream.h:91``); Blender blends the components linearly and normalises, the same for keys that are close
    and a visible shortcut for keys far apart (a tumbling piece of debris, 100 degrees between two keys). A pair whose
    quaternion dot product is below ``wide_dot`` gets slerped keys every ``step_deg`` degrees or less, and Blender's own blend
    between those is then within a fraction of a degree of the sphere."""
    k = np.asarray(keys, dtype=np.float64)
    if len(k) < 2:
        return k
    out = [k[0]]
    for a, b in zip(k[:-1], k[1:]):
        qa, qb = a[1:5], b[1:5]
        d = float(np.dot(qa, qb))
        sign = -1.0 if d < 0 else 1.0
        d = abs(d)
        if d < wide_dot:
            ang = 2.0 * math.acos(min(1.0, d))
            n = int(math.ceil(math.degrees(ang) / step_deg))
            th = math.acos(min(1.0, d))
            for j in range(1, n):
                t = j / n
                q = (math.sin((1 - t) * th) * qa + math.sin(t * th) * sign * qb) / math.sin(th)
                out.append(np.concatenate([[a[0] + (b[0] - a[0]) * t], q / np.linalg.norm(q)]))
        out.append(b)
    return np.array(out)


def basis_loc(consts, xyz) -> np.ndarray:
    """(n, 3) Blender ``location`` keys of one bone from its SGM translation keys (n, 3)."""
    R, t, _p = consts
    return (np.asarray(xyz, dtype=np.float64) @ _B3.T) @ R.T + t


def basis_quat(consts, xyzw) -> np.ndarray:
    """(n, 4) Blender ``rotation_quaternion`` keys (w, x, y, z; sign kept continuous) of one bone from its SGM rotation keys
    (n, 4) x y z w."""
    _R, _t, P = consts
    k = np.asarray(xyzw, dtype=np.float64)
    q = np.stack([k[:, 3], k[:, 0], k[:, 1], k[:, 2]], axis=1)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    out = _qmul(_qmul(np.broadcast_to(P, q.shape), q), np.broadcast_to(_QB_CONJ, q.shape))
    for i in range(1, len(out)):                         # Blender interpolates the raw components: stay on one side
        if float(np.dot(out[i], out[i - 1])) < 0.0:
            out[i] = -out[i]
    return out


def camera_matrix(position, rotation_xyzw):
    """The Blender transform ``(location (3,), quaternion wxyz)`` of a model camera at an SGM position and rotation: the camera
    looks down its +Z with +Y up (``Camera::GetLookAt`` / ``GetUp``); Blender's looks down -Z, so the axes become (right, up, back)
    with right = forward x up in Blender, which is the game's right (up x forward in its left-handed axes) after the reflection."""
    R = np.array(NS.quat_to_matrix(rotation_xyzw))
    f, u = _B3 @ R[:, 2], _B3 @ R[:, 1]
    r = np.cross(f, u)
    m = np.column_stack([r, u, -f])
    return _B3 @ np.asarray(position, dtype=np.float64), _mat_to_quat(m)


# ---------------------------------------------------------------------------------------------------- the camera hand-over
def transition_weight(seconds_since: float, transition_s: float) -> float:
    """How much of the cut-scene camera the view has taken ``seconds_since`` after "NIS start" / "UseAnimationCamera" with a
    transition of ``transition_s`` seconds: the blend runs 0 to 1 at 1 / ``transition_s`` per second (``CameraMan::SetEntityFocus``
    ``UI/CameraMan.cpp:451``; a time of 0 is a cut) and is eased in and out, ``2b^2`` below the middle and ``1 - 2(1-b)^2`` above
    (``CameraMan::UpdateMatrix`` ``CameraMan.cpp:1383-1400``)."""
    if transition_s <= 0.0:
        return 1.0 if seconds_since >= 0.0 else 0.0
    b = min(max(seconds_since / transition_s, 0.0), 1.0)
    if b < 0.5:
        x = b + b
        return x * x * 0.5
    x = (1.0 - b) * 2.0
    return 1.0 - x * x * 0.5


def blend_camera(view, nis, weight: float):
    """The camera the game shows while the hand-over is ``weight`` (0..1) done: ``view`` and ``nis`` are
    ``(location (3), quaternion wxyz, fov)`` of the normal view and of the cut-scene camera, in one space. Position and
    field of view blend linearly, the rotation on the sphere (``AnimPlayList::SetupMatrix`` ``AnimPlayList.cpp:895``)."""
    if weight <= 0.0:
        return view
    if weight >= 1.0:
        return nis
    (l0, q0, f0), (l1, q1, f1) = view, nis
    a, b = Quaternion(q0), Quaternion(q1)
    q = a.slerp(b, weight)
    return (tuple(np.asarray(l0) + (np.asarray(l1) - np.asarray(l0)) * weight), tuple(q), f0 + (f1 - f0) * weight)


# ---------------------------------------------------------------------------------------------------- the files
def _addon(name):
    return M._addon(name)


def blueprint_of(entity_object) -> str:
    ref = entity_object.get("ic_blueprint", "") if entity_object is not None else ""
    if not ref:
        raise NisError("the object is not a placed entity of a map (it has no blueprint)")
    from ICParse.data import ICData
    return ICData.normalize(ref)


def _data_for(context, map_collection, game_dir, data):
    if data is not None:
        return data, game_dir
    game_dir = game_dir or prefs.resolve_game_dir(context)[0]
    if not game_dir:
        raise NisError("the game's files were not found (set the Game folder in the add-on's preferences)")
    from ICParse.sgb import view
    archive = (map_collection.get(M.KEY_ARCHIVE, "") if map_collection is not None else "") or ""
    return view.game_data(game_dir, archive), game_dir


def is_nis_blueprint(entity_object) -> bool:
    """Whether a placed object is a cut-scene (its blueprint is under ``art/ebps/nis/``)."""
    try:
        return blueprint_of(entity_object).startswith("art/ebps/nis/")
    except NisError:
        return False


# ---------------------------------------------------------------------------------------------------- geometry
class _Loader:
    """Loads scene models once each through the add-on's own reader (``core.load_scene``), from the game's files."""

    def __init__(self, data, game_dir, tmp):
        self.data, self.game_dir, self.tmp = data, game_dir, tmp
        self.cache = {}
        self.raw = {}
        self._mb = {}
        self.core = _addon("core")

    def mesh_bones(self, ref: str) -> dict:
        """``{mesh name: NS.MeshBones}`` of a loaded model (its skinned meshes' own bone lists)."""
        ref = ref.lower()
        if ref not in self._mb:
            self._mb[ref] = NS.read_mesh_bones(self.raw[ref]) if ref in self.raw else {}
        return self._mb[ref]

    def load(self, ref: str):
        ref = ref.lower()
        if ref in self.cache:
            return self.cache[ref]
        raw = self.data.read(ref)
        if raw is None:
            self.cache[ref] = None
            return None
        self.raw[ref] = raw
        path = os.path.join(self.tmp, "models", *ref.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
        try:
            sd = self.core.load_scene(path, data_root=self.game_dir, texture_source=self.data)
        except Exception as exc:                          # a creature model, a damaged file: one part is skipped
            self.cache[ref] = str(exc)
            return self.cache[ref]
        self.cache[ref] = sd
        return sd


def _merge_parts(sd, model, loader, notes, counts, part_nodes):
    """Add to the NIS model's own ``SceneData`` the parts it borrows (``XREF`` nodes), placed and bound as ``GeoGroup::LoadMesh`` /
    ``HandleNODE`` (``GeoGroup.cpp:267, 1046``) do:

    * the borrowed mesh is the node of that name in the other model, its vertices as stored there;
    * the node's own MTRX places it; when the node it is taken from is bound to a bone there, the part is first brought into that
      bone's space (``* inverse bind``) and then onto this model's bone (``* bind``), so it follows this model's bone;
    * a skinned mesh binds each of its bones to the bone of THIS model with the same name after the node's find / replace pair
      (``LinkMeshBonesToNodes`` ``GeoGroup.cpp:896``: every occurrence of the find text is replaced, then a case-insensitive match).
    """
    core = loader.core
    rigmod = _addon("rig")
    from ICParse.codecs import pose as posemod
    own = {n.lower(): i for i, n in enumerate(sd.bone_names)}
    counts.setdefault("own_meshes", len(sd.parts))
    # which node (mesh index) each part is: the model's own meshes by name, in order
    pool = {}
    for nd in model.nodes:
        if not nd.xref:
            pool.setdefault(nd.name.strip().lower(), []).append(nd.index)
    part_nodes.clear()
    for p in sd.parts:
        lst = pool.get(p.name.strip().lower())
        part_nodes.append(lst.pop(0) if lst else None)
    for xr in model.xrefs:
        src = loader.load(xr.file.replace("\\", "/").split(":", 1)[-1].strip("/").lower()) if xr.file else None
        if src is None or isinstance(src, str):
            why = "its file is not in the game's files" if src is None else f"it could not be read ({src})"
            notes.append(f"borrowed part '{xr.obj}' of {xr.file.replace(chr(92), '/').rsplit('/', 1)[-1]} left out: {why}")
            counts["xref_skipped"] = counts.get("xref_skipped", 0) + 1
            continue
        part = next((p for p in src.parts if p.name.strip().lower() == xr.obj.strip().lower()), None)
        if part is None:
            notes.append(f"borrowed part '{xr.obj}' not found in {xr.file.replace(chr(92), '/').rsplit('/', 1)[-1]}")
            counts["xref_skipped"] = counts.get("xref_skipped", 0) + 1
            continue
        # the placement, in the add-on's axes
        M_sgm = np.eye(4)
        f = xr.matrix or (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        M_sgm[:3, 0], M_sgm[:3, 1], M_sgm[:3, 2], M_sgm[:3, 3] = f[0:3], f[3:6], f[6:9], f[9:12]
        place = np.array(rigmod.to_blender_matrix(M_sgm))
        borrowed_bound = part.bind is not None and 0 <= int(part.bind) < len(src.bind_world)
        if borrowed_bound:
            inv_src = np.linalg.inv(np.array(rigmod.to_blender_matrix(np.asarray(src.bind_world[int(part.bind)], dtype=np.float64))))
            place = inv_src @ place                      # MTRX first, then the inverse bind of the bone it hangs from there
        bind = xr.bind
        if bind is not None and not (0 <= bind < len(sd.bone_names)):
            bind = None
        if bind is not None and borrowed_bound:
            place = np.array(rigmod.to_blender_matrix(np.asarray(sd.bind_world[bind], dtype=np.float64))) @ place
        verts = (part.verts @ place[:3, :3].T) + place[:3, 3]
        normals = part.normals
        if len(normals):
            normals = normals @ place[:3, :3].T
            ln = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = normals / np.where(ln == 0, 1.0, ln)
        # materials and textures: appended, the part's table shifted
        mat_off = len(sd.materials)
        sd.materials.extend(dict(m) for m in src.materials)
        for k, v in src.textures.items():
            sd.textures.setdefault(k, v)
        skin = []
        if part.skin:
            # the mesh's own bone list (names + bind matrices) is what its vertex bone indices refer to
            srcref = xr.file.replace("\\", "/").split(":", 1)[-1].strip("/").lower()
            mb = loader.mesh_bones(srcref).get(part.name.strip().lower())
            if mb is not None:
                src_names, src_binds = mb.names, [np.asarray(posemod.bind_local_matrix(b12), dtype=np.float64) for b12 in mb.binds]
            else:
                src_names, src_binds = list(src.bone_names), [np.asarray(w, dtype=np.float64) for w in src.bind_world]
            names = []
            for bn in src_names:
                nm = bn.replace(xr.find, xr.replace) if xr.find else bn      # every occurrence, as LinkMeshBonesToNodes does
                names.append(own.get(nm.lower()))
            n_src = len(names)
            miss = {src_names[int(bi)] for infl in part.skin for bi, _w in infl if 0 <= int(bi) < n_src and names[int(bi)] is None}
            if miss:
                notes.append(f"'{xr.obj}': {len(miss)} of its bones have no bone of that name in the cut-scene's skeleton "
                             f"({', '.join(sorted(miss)[:3])}{'...' if len(miss) > 3 else ''}): their weights are left out")
            skin = [[(names[int(bi)], w) for bi, w in infl if 0 <= int(bi) < n_src and names[int(bi)] is not None] for infl in part.skin]
            # The engine skins a borrowed mesh with ITS OWN inverse bind matrices and THIS model's animated bone matrices
            # (GeoGroup::Draw hands each mesh bone the group transform of the same name), so where this skeleton sits elsewhere
            # than the borrowed one (a character moved into the scene) the part is carried along. Blender deforms by
            # (pose * inverse of THIS skeleton's rest), so the rest shape is moved first by each bone's offset
            # D = (this bind) * (the mesh's bind)^-1, blended by the vertex's weights: exact when the offset is the same for every
            # bone the part uses (a whole character placed in the scene), a blend otherwise.
            used = sorted({int(bi) for infl in part.skin for bi, _w in infl if 0 <= int(bi) < n_src and names[int(bi)] is not None})
            D = {}
            degenerate = set()
            for bi in used:
                if bi < len(src_binds):
                    Rs = np.array(rigmod.to_blender_matrix(src_binds[bi]))
                    Rn = np.array(rigmod.to_blender_matrix(np.asarray(sd.bind_world[names[bi]], dtype=np.float64)))
                    if not (0.5 < np.linalg.det(Rs[:3, :3]) < 2.0 and 0.5 < np.linalg.det(Rn[:3, :3]) < 2.0):
                        degenerate.add(src_names[bi])                          # a bind matrix that is not a transform (stock quirk)
                        continue
                    D[bi] = Rn @ np.linalg.inv(Rs)
            if degenerate:
                notes.append(f"'{xr.obj}': {len(degenerate)} bone(s) have a bind matrix that is not a proper transform "
                             f"({', '.join(sorted(degenerate)[:3])}): the part is not moved by them")
            if D:
                mean = np.mean([d for d in D.values()], axis=0)
                spread = max(float(np.abs(d - mean).max()) for d in D.values())
                if max(float(np.abs(d - np.eye(4)).max()) for d in D.values()) > 1e-5:
                    base_v, base_n = part.verts, part.normals
                    out_v = base_v.copy()
                    out_n = base_n.copy() if len(base_n) else base_n
                    if spread < 1e-4:
                        out_v = base_v @ mean[:3, :3].T + mean[:3, 3]
                        if len(out_n):
                            out_n = base_n @ mean[:3, :3].T
                    else:
                        for vi, infl in enumerate(part.skin):
                            ws = [(int(bi), float(w)) for bi, w in infl if int(bi) in D]
                            tot = sum(w for _b, w in ws)
                            if tot <= 0:
                                continue
                            out_v[vi] = sum(w * (D[bi][:3, :3] @ base_v[vi] + D[bi][:3, 3]) for bi, w in ws) / tot
                            if len(out_n):
                                out_n[vi] = sum(w * (D[bi][:3, :3] @ base_n[vi]) for bi, w in ws) / tot
                    if len(out_n):
                        ln = np.linalg.norm(out_n, axis=1, keepdims=True)
                        out_n = out_n / np.where(ln == 0, 1.0, ln)
                    verts = out_v @ place[:3, :3].T + place[:3, 3]                 # the node's own placement after (identity in the stock files)
                    normals = out_n @ place[:3, :3].T if len(out_n) else out_n
                    counts["skin_moved"] = counts.get("skin_moved", 0) + 1
                    if spread >= 1e-4:
                        counts["skin_blended"] = counts.get("skin_blended", 0) + 1
                        notes.append(f"'{xr.obj}': the bones it uses sit at different offsets ({spread:.2f} at most) from the "
                                     "mesh's own, so its rest shape is a blend of them: approximate")
        nodename = (xr.name or xr.obj).strip() or xr.obj
        sd.parts.append(core.ScenePart(
            name=nodename, verts=verts, normals=normals, uvs=part.uvs, tris=part.tris,
            tri_mat=np.asarray(part.tri_mat, dtype=np.int64) + mat_off, skin=skin,
            bind=bind if not part.skin else None, mesh_index=-1))
        part_nodes.append(xr.node)
        counts["xref_built"] = counts.get("xref_built", 0) + 1
        if part.skin:
            counts["xref_skinned"] = counts.get("xref_skinned", 0) + 1
    return sd


def _merge_parts_nodes_only(sd, model, part_nodes):
    pool = {}
    for nd in model.nodes:
        if not nd.xref:
            pool.setdefault(nd.name.strip().lower(), []).append(nd.index)
    part_nodes.clear()
    for p in sd.parts:
        lst = pool.get(p.name.strip().lower())
        part_nodes.append(lst.pop(0) if lst else None)


# ---------------------------------------------------------------------------------------------------- Blender pieces
def _slot(action_owner_ad, action):
    try:
        if action is not None and hasattr(action_owner_ad, "action_slot") and len(action.slots):
            action_owner_ad.action_slot = action.slots[0]
    except Exception:
        pass


def _fc(action, idblock, path, index, group=""):
    """An F-curve of ``action`` for ``idblock``'s ``path[index]`` (slotted Actions of Blender 4.4+, or the legacy API)."""
    try:
        return action.fcurve_ensure_for_datablock(idblock, path, index=index)
    except (AttributeError, TypeError):
        fc = action.fcurves.find(path, index=index)
        return fc or action.fcurves.new(path, index=index, action_group=group or "")


def _set_keys(fc, frames, values, interpolation=None):
    n = len(frames)
    fc.keyframe_points.add(n)
    co = np.empty(2 * n, dtype=np.float64)
    co[0::2], co[1::2] = frames, values
    fc.keyframe_points.foreach_set("co", co)
    lin = np.full(n, 1, dtype=np.int32)                 # 1 = LINEAR (the order of Blender's interpolation enum)
    if interpolation is not None:
        lin = np.asarray(interpolation, dtype=np.int32)
    try:
        fc.keyframe_points.foreach_set("interpolation", lin)
    except Exception:
        for kp, v in zip(fc.keyframe_points, lin):
            kp.interpolation = "CONSTANT" if v == 0 else "LINEAR"
    fc.update()


def _clip_action(arm, name, clip_tracks, bone_idx, consts, bone_bl, clip, entry, start_frame, fps, scene_clip):
    """The Action of one clip: only the bones it animates, each channel keyed at the clip's own key times placed on the scene's
    timeline (``start_frame + (start delay + phase * duration) * fps``)."""
    act = bpy.data.actions.new(PREFIX_CLIP + name)
    ad = arm.animation_data or arm.animation_data_create()
    prev = ad.action
    ad.action = act
    _slot(ad, act)
    scale = clip.duration * fps
    base = start_frame + entry.delay * fps
    first = None
    last = None
    animated = 0
    for tr in clip_tracks:
        i = bone_idx.get(tr.bone_name.lower())
        if i is None:
            continue
        animated += 1
        grp = bone_bl[i]
        # A clip that animates a bone owns the whole bone: a stream it has no keys for is the bind pose (Anim::GetBonePosRot,
        # Anim.cpp:1707), not what a lower clip left there. So the missing stream is keyed once, as the bind pose.
        if not len(tr.trans_keys) and len(tr.rot_keys):
            for c in range(3):
                _set_keys(_fc(act, arm, f'pose.bones["{grp}"].location', c, grp), np.array([base + tr.rot_keys[0][0] * scale]), np.zeros(1))
        if not len(tr.rot_keys) and len(tr.trans_keys):
            for c, v in enumerate((1.0, 0.0, 0.0, 0.0)):
                _set_keys(_fc(act, arm, f'pose.bones["{grp}"].rotation_quaternion', c, grp), np.array([base + tr.trans_keys[0][0] * scale]), np.full(1, v))
        if len(tr.trans_keys):
            k = np.asarray(tr.trans_keys, dtype=np.float64)
            fr = base + k[:, 0] * scale
            loc = basis_loc(consts[i], k[:, 1:4])
            for c in range(3):
                _set_keys(_fc(act, arm, f'pose.bones["{grp}"].location', c, grp), fr, loc[:, c])
            first = fr[0] if first is None else min(first, fr[0])
            last = fr[-1] if last is None else max(last, fr[-1])
        if len(tr.rot_keys):
            k = densify_rotations(tr.rot_keys)
            fr = base + k[:, 0] * scale
            q = basis_quat(consts[i], k[:, 1:5])
            for c in range(4):
                _set_keys(_fc(act, arm, f'pose.bones["{grp}"].rotation_quaternion', c, grp), fr, q[:, c])
            first = fr[0] if first is None else min(first, fr[0])
            last = fr[-1] if last is None else max(last, fr[-1])
    ad.action = prev
    if prev is not None:
        _slot(ad, prev)
    act["ic_nis_clip"] = clip.name
    act["ic_nis_start_s"] = float(entry.delay)
    act["ic_nis_duration_s"] = float(clip.duration)
    act["ic_nis_bones"] = animated
    act.use_fake_user = False
    return act, first, last


def _nla_strip(arm, action, name, frame_first, frame_last):
    ad = arm.animation_data or arm.animation_data_create()
    trk = ad.nla_tracks.new()
    trk.name = name
    strip = trk.strips.new(name, int(math.floor(frame_first)), action)
    try:
        if hasattr(strip, "action_slot") and len(action.slots):
            strip.action_slot = action.slots[0]
    except Exception:
        pass
    # the strip starts where the action's first key is (so the action's frames ARE the scene's frames)
    if abs(strip.frame_start - strip.action_frame_start) > 1e-6:
        try:
            strip.frame_start_ui = strip.action_frame_start
        except Exception:
            strip.frame_start = strip.action_frame_start
    strip.extrapolation = "HOLD_FORWARD"
    strip.blend_type = "REPLACE"
    return trk, strip


def _make_camera(context, coll, root, model, sched, cuts, build, fps, start_frame, duration, notes):
    """The Blender camera: one key per scene frame of the active model camera's pose (the clip that plays at that time, or the
    camera's rest pose before the first clip), constant across a cut."""
    if not model.cameras:
        return None
    label = root.name if (root is not None and not root.get(K_NIS)) else coll.name
    cam_data = bpy.data.cameras.new(f"{label} camera")
    cam_data.sensor_fit = "HORIZONTAL"
    cam_data.lens_unit = "FOV"
    cam_data.clip_start = 0.1
    cam_data.clip_end = 1000.0
    cam = bpy.data.objects.new(f"{label} camera", cam_data)
    cam.rotation_mode = "QUATERNION"
    coll.objects.link(cam)
    cam.parent = root
    # which clip plays at a time: the first in the list (highest priority) among those started
    starts = sorted(sched, key=lambda e: e.delay)
    n = int(math.ceil(duration * fps)) + 1
    times = np.arange(n) / fps
    cut_t = [c[0] for c in cuts]
    cut_i = [c[1] for c in cuts]
    loc = np.zeros((n, 3))
    quat = np.zeros((n, 4))
    ang = np.zeros(n)
    far = np.zeros(n)
    which = np.zeros(n, dtype=np.int32)
    lost = 0
    for k, t in enumerate(times):
        ci = 0
        j = bisect.bisect_right(cut_t, t + 1e-9) - 1
        if j >= 0:
            ci = cut_i[j]
        started = [e for e in starts if e.delay <= t + 1e-9]
        entry = min(started, key=lambda e: e.priority) if started else None
        clip = model.clip(entry.clip) if entry else None
        into_clip = (t - entry.delay) if entry else 0.0
        if entry is not None and entry.loop and clip is not None and clip.duration > 0:
            into_clip = into_clip % clip.duration                       # a looping clip starts over (PlayingAnim::Update)
        pose = NS.camera_pose(model, clip, ci, into_clip)
        if not pose.animated and clip is not None:
            lost += 1
        l, q = camera_matrix(pose.position, pose.rotation)
        if k and float(np.dot(q, quat[k - 1])) < 0.0:
            q = -q
        loc[k], quat[k], ang[k], which[k], far[k] = l, q, pose.fov, ci, pose.far
    frames = start_frame + np.arange(n, dtype=np.float64)
    # a key whose next key looks through another camera holds its value (a cut, not a one-frame blend)
    interp = np.ones(n, dtype=np.int32)
    interp[:-1][which[:-1] != which[1:]] = 0
    act = bpy.data.actions.new(f"{label} camera action")
    ad = cam.animation_data_create()
    ad.action = act
    _slot(ad, act)
    for c in range(3):
        _set_keys(_fc(act, cam, "location", c), frames, loc[:, c], interp)
    for c in range(4):
        _set_keys(_fc(act, cam, "rotation_quaternion", c), frames, quat[:, c], interp)
    act2 = bpy.data.actions.new(f"{label} camera lens")
    ad2 = cam_data.animation_data_create()
    ad2.action = act2
    _slot(ad2, act2)
    # the lens is animated (Blender computes the field of view from it: "angle" itself is not played back); with the sensor
    # fitted to the horizontal, lens = sensor width / (2 tan(fov / 2)), the horizontal field of view the game's camera has
    lens = cam_data.sensor_width / (2.0 * np.tan(ang / 2.0))
    _set_keys(_fc(act2, cam_data, "lens", 0), frames, lens, interp)
    cam_data.clip_end = float(max(far.max(), 10.0))                  # the far plane the clips' lens channels ask for, at most
    if lost:
        notes.append(f"the camera has no track in the clip that plays for {lost} frame(s): the game places it at position 0 "
                     "with no rotation, and so does this one")
    return cam


def _make_visibility(coll, objs, part_nodes, model, sched, start_frame, fps, notes, counts):
    """Show and hide the parts the way the clips do (``NS.visibility_timeline``): constant keys on ``hide_viewport`` and
    ``hide_render``. A part hidden for the whole cut-scene is just hidden."""
    by_node = {}
    for o in objs:
        pi = o.get("bz_scene_part")
        if pi is not None and 0 <= int(pi) < len(part_nodes) and part_nodes[int(pi)] is not None:
            by_node[part_nodes[int(pi)]] = o
    animated = hidden = 0
    for node, o in by_node.items():
        tl = NS.visibility_timeline(model, sched, node)
        o.hide_viewport = o.hide_render = not tl[0][1]
        if len(tl) == 1:
            hidden += 0 if tl[0][1] else 1
            continue
        frames = np.array([start_frame + t * fps for t, _v in tl])
        vals = np.array([0.0 if v else 1.0 for _t, v in tl])
        act = bpy.data.actions.new(f"{coll.name} visibility {o.name}")
        ad = o.animation_data_create()
        ad.action = act
        _slot(ad, act)
        for path in ("hide_viewport", "hide_render"):
            _set_keys(_fc(act, o, path, 0), frames, vals, np.zeros(len(frames), dtype=np.int32))
        animated += 1
    counts["visibility_animated"] = animated
    counts["hidden_throughout"] = hidden
    return by_node


def _key_hide(o, coll_name, rows):
    """Replace the visibility keys of ``o``: ``rows`` = ``[(frame, hidden)]``, constant interpolation."""
    ad = o.animation_data
    if ad is not None and ad.action is not None and ad.action.get("ic_nis_visibility"):
        old = ad.action
        ad.action = None
        bpy.data.actions.remove(old)
    rows = sorted({int(f): bool(h) for f, h in rows}.items())
    o.hide_viewport = o.hide_render = rows[0][1]
    act = bpy.data.actions.new(f"{coll_name} visibility {o.name}")
    act["ic_nis_visibility"] = True
    ad = o.animation_data or o.animation_data_create()
    ad.action = act
    _slot(ad, act)
    frames = np.array([f for f, _h in rows], dtype=np.float64)
    vals = np.array([1.0 if h else 0.0 for _f, h in rows])
    for path in ("hide_viewport", "hide_render"):
        _set_keys(_fc(act, o, path, 0), frames, vals, np.zeros(len(frames), dtype=np.int32))


def show_between(build: "NisBuild", first_shown: int, last_shown: int, first_frame: int = 1) -> None:
    """See :meth:`NisBuild.show_between`."""
    name = build.collection.name if build.collection is not None else "NIS"
    parts = {o.name: (node, o) for node, o in build.visible.items()}
    for o in build.objects:
        if o.type == "CAMERA":
            continue
        rows = [(first_frame, True)] if first_shown > first_frame else []
        if o.name in parts and build._model is not None:
            tl = NS.visibility_timeline(build._model, build._sched, parts[o.name][0])
            for t, vis in tl:
                f = int(round(build.start_frame + t * build.fps))
                if f >= last_shown:
                    break
                rows.append((max(f, first_shown), not vis))
        else:
            rows.append((first_shown, False))
        rows.append((last_shown, True))
        _key_hide(o, name, rows)


def _events(model, ebp, sched, subtitles):
    ev = []
    for r in NS.script_events(model, sched):
        if r["kind"] == "camera":
            ev.append({"t": r["t"], "kind": "camera_cut", "camera": r["name"], "quality": "exact", "clip": r["clip"]})
        elif r["kind"] == "exclusive":
            ev.append({"t": r["t"], "kind": "exclusive_mode", "on": r["on"], "quality": "exact", "clip": r["clip"],
                       "why": "while on, the game draws only the cut-scene's own object (it hides everything else)"})
        else:
            ev.append({"t": r["t"], "kind": "lighting", "value": r["value"], "quality": "exact", "clip": r["clip"]})
    for r in NS.model_events(ebp, model, sched):
        row = {k: v for k, v in r.items()}
        row["quality"] = "exact"
        if row["kind"] == "event":
            row["quality"], row["why"] = "skipped", "an effect or marker event of the model (its particle effects): listed with its time, not played"
        ev.append(row)
    for s in subtitles:
        ev.append({"t": s["start"], "kind": "subtitle", "text_id": s["locid"], "until": s["finish"], "quality": "approximate",
                   "why": "dialogue timing comes from the scene's .subtitles file; the game may not display "
                          "these lines in cut-scenes"})
    for e in sched:
        ev.append({"t": e.delay, "kind": "clip_start", "clip": e.clip, "duration_s": e.duration, "quality": "exact"})
    ev.sort(key=lambda r: (r["t"], r["kind"]))
    return ev


# ---------------------------------------------------------------------------------------------------- the entry point
def build_nis(context, map_collection, entity_object, *, start_frame: int = 1, game_dir: str | None = None, data=None,
              with_parts: bool = True) -> NisBuild:
    """Build the stand-in of the placed NIS object ``entity_object`` (see the module text). ``map_collection`` is the map's
    collection (its archive decides which game build's files are read; the stand-in collection becomes its child; None: a
    top-level collection). ``start_frame`` is the scene frame of "NIS start". Never changes ``entity_object``."""
    ref = blueprint_of(entity_object)
    data, game_dir = _data_for(context, map_collection, game_dir, data)
    sgm_ref = os.path.splitext(ref)[0] + ".sgm"
    raw_sgm, raw_ebp = data.read(sgm_ref), data.read(ref)
    if raw_sgm is None or raw_ebp is None:
        raise NisError(f"{ref} or its model {sgm_ref} is not in the game's files")
    from ICParse.ebp import parser as ebp_parser
    model = NS.read(raw_sgm)
    ebp = ebp_parser.read(raw_ebp)
    sched = NS.schedule(ebp, model)
    out = NisBuild(entity=entity_object, blueprint=ref, start_frame=int(start_frame), _model=model, _sched=sched)
    notes = out.notes
    if not sched:
        notes.append("the blueprint's motion tree has no 'Master' node that plays clips: nothing to animate")
    fps = scene_fps(context)
    out.fps = fps
    duration = max([e.delay + e.duration for e in sched] or [0.0])
    out.duration_s = duration
    out.frame_start = int(start_frame)
    out.frame_end = int(start_frame) + int(math.ceil(duration * fps))
    out.schedule = [{"clip": e.clip, "start_s": e.delay, "duration_s": e.duration, "priority": e.priority} for e in sched]
    cuts = [(t, model.cameras[i].name) for t, i, _s in NS.camera_cuts(model, sched)]
    out.cuts = cuts
    subs = []
    sub_raw = data.read(os.path.splitext(ref)[0] + ".subtitles")
    if sub_raw:
        subs = NS.read_subtitles(sub_raw.decode("latin1", "ignore"))
    out.events = _events(model, ebp, sched, subs)

    name = entity_object.name
    top_name = (map_collection.name[4:] if map_collection is not None and map_collection.name.startswith("Map ") else
                (map_collection.name if map_collection is not None else "map"))
    coll = bpy.data.collections.new(f"{top_name} NIS {name}")
    coll[K_NIS] = ref
    if map_collection is not None:
        map_collection.children.link(coll)
    else:
        context.scene.collection.children.link(coll)
    out.collection = coll
    tmp = tempfile.mkdtemp(prefix="ic_nis_")
    try:
        loader = _Loader(data, game_dir, tmp)
        sd = loader.load(sgm_ref)
        if sd is None or isinstance(sd, str):
            raise NisError(f"the model {sgm_ref} could not be read" + (f" ({sd})" if sd else ""))
        part_nodes = []
        missing = []
        if with_parts:
            _merge_parts(sd, model, loader, notes, out.parts, part_nodes)
        else:
            out.parts["own_meshes"] = len(sd.parts)
            _merge_parts_nodes_only(sd, model, part_nodes)
        for v in loader.cache.values():
            if v is not None and not isinstance(v, str):
                missing += [m for m in (getattr(v, "missing_textures", None) or []) if m not in missing]
        if missing:
            notes.append(f"{len(missing)} texture(s) were not found in the game's files and are left plain: "
                         + ", ".join(sorted({str(m).replace(chr(92), '/').rsplit('/', 1)[-1] for m in missing})[:6]))
            out.parts["textures_missing"] = len(missing)
        scene_mod = _addon("scene")
        rig_mod = _addon("rig")
        into = M._Into(context, coll)
        if sd.parts:
            scene_mod.build_scene(into, sd, source_path=sgm_ref, clips_as_actions=False)
        arm = next((o for o in coll.objects if o.type == "ARMATURE"), None)
        if arm is None and sd.bone_names:
            arm = rig_mod.create_armature(into, f"{coll.name} rig", sd.bone_names, sd.bone_parents, sd.bind_world)
        for o in coll.all_objects:                        # parts of the map's replay, not a project the add-on exports
            for key in ("bz_scene", "bz_source", "bz_anims"):
                if key in o:
                    del o[key]
        out.armature = arm
        root = bpy.data.objects.new(f"{coll.name} root", None)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 1.0
        coll.objects.link(root)
        root.matrix_world = entity_object.matrix_world.copy()
        root[K_NIS] = ref
        out.root = root
        for o in list(coll.objects):
            if o is not root and o.parent is None:
                o.parent = root
        # ---- parts shown and hidden
        out.visible = _make_visibility(coll, list(coll.all_objects), part_nodes, model, sched, int(start_frame), fps, notes, out.parts)
        # ---- the bone clips, one strip each
        if arm is not None and sched:
            bl_names = list(arm.get(rig_mod.KEY_BONE_SRC_NAMES) or sd.bone_names)
            bone_idx = {n.lower(): i for i, n in enumerate(sd.bone_names)}
            consts = bone_constants(sd.bind_world, sd.bone_parents)
            ad = arm.animation_data or arm.animation_data_create()
            for trk in list(ad.nla_tracks):
                ad.nla_tracks.remove(trk)
            by_name = {a.name.lower(): a for a in sd.animations}
            # lowest priority first, so higher tracks (evaluated later) win
            for entry in sorted(sched, key=lambda e: -e.priority):
                clip = model.clip(entry.clip)
                an = by_name.get(entry.clip.lower())
                if clip is None or an is None:
                    notes.append(f"clip {entry.clip} has no bone animation in the model: skipped")
                    continue
                act, first, last = _clip_action(arm, entry.clip, an.tracks, bone_idx, consts, bl_names, clip, entry,
                                                int(start_frame), fps, an)
                if first is None:
                    bpy.data.actions.remove(act)
                    continue
                _nla_strip(arm, act, f"{PREFIX_CLIP}{entry.clip}", first, last)
                out.clip_actions.append(act)
        # ---- the camera
        out.camera = _make_camera(context, coll, root, model, sched, [(t, model.camera_index(n)) for t, n in cuts], out, fps,
                                  int(start_frame), duration, notes)
        if out.camera is not None and out.camera.animation_data is not None:
            out.action = out.camera.animation_data.action
        out.objects = list(coll.all_objects)
    except Exception:
        remove_nis(out)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---- what is not played
    if model.lights:
        notes.append(f"the model's {len(model.lights)} light(s) are not built")
    if model.markers:
        notes.append(f"the model's {len(model.markers)} effect marker(s) (particle effects) are not built")
    notes.append("the clips' morph weights and shader / light value channels are not played (the lens and the parts' visibility are)")
    if out.parts.get("xref_skipped"):
        notes.append(f"{out.parts['xref_skipped']} borrowed part(s) could not be built (listed above)")
    return out


@dataclass
class EntityCamera:
    camera: object = None                      # the Blender camera (None when the model has none)
    action: object = None
    frame_start: int = 1
    frame_end: int = 1
    duration_s: float = 0.0
    cuts: list = field(default_factory=list)   # [(seconds, camera name)]
    notes: list = field(default_factory=list)


def build_entity_camera(context, parent, blueprint_ref: str, plays, *, collection=None, start_frame: int = 1,
                        duration: float | None = None, game_dir: str | None = None, data=None) -> EntityCamera:
    """The camera "UseAnimationCamera" puts the view on, for an ordinary entity: the stock maps use it only on Rex and the lab in
    their "Win Lose Conditions" triggers, so the death cinematic follows the dying model's own camera (``rex.sgm`` Camera01 in
    ``StumbleDie``, ``lab.sgm`` Camera01 in ``damage_04``).

    ``parent`` is the replay's object for that entity (the camera hangs from it, in the model's space); ``blueprint_ref`` its blueprint
    (``art/ebps/characters/rex.ebp``); ``plays`` the clips it plays, ``[(seconds, clip name)]`` or ``[(seconds, clip name, loops)]``
    (a later play is on top of an earlier one, as ``AnimPlayList`` puts a new motion at the front). The camera is the model camera
    the clips' ``setcamera`` lines pick (the first at the start), its pose that of the top clip (``AnimPlayList::GetCameraState``);
    with no clip playing yet, the camera's rest pose. A play's fade-in is not blended (a clip is at full weight from its start).
    A model with no camera is seen from behind its head bone by the game (``AnimPlayList::SetupMatrix``): not built, said in ``notes``."""
    from ICParse.data import ICData
    data, _gd = _data_for(context, None, game_dir, data)
    ref = ICData.normalize(blueprint_ref)
    raw = data.read(os.path.splitext(ref)[0] + ".sgm")
    out = EntityCamera(frame_start=int(start_frame))
    if raw is None:
        out.notes.append(f"the model of {ref} is not in the game's files")
        return out
    model = NS.read(raw)
    if not model.cameras:
        out.notes.append("the model has no camera: the game then looks from behind its head bone, which is not built")
        return out
    entries = []
    for k, p in enumerate(sorted(plays, key=lambda p: p[0])):
        clip = model.clip(p[1])
        if clip is None:
            out.notes.append(f"clip {p[1]} is not in the model: skipped")
            continue
        entries.append(NS.ScheduleEntry(node=clip.name, clip=clip.name, delay=float(p[0]), duration=clip.duration, priority=-k,
                                        loop=bool(p[2]) if len(p) > 2 else False))
    fps = scene_fps(context)
    end = duration if duration is not None else max([e.delay + e.duration for e in entries] or [0.0])
    cuts = [(t, i) for t, i, _s in NS.camera_cuts(model, entries)]
    out.cuts = [(t, model.cameras[i].name) for t, i in cuts]
    coll = collection or (parent.users_collection[0] if parent is not None and parent.users_collection else context.scene.collection)
    out.camera = _make_camera(context, coll, parent, model, entries, cuts, None, fps, int(start_frame), end, out.notes)
    out.duration_s = end
    out.frame_end = int(start_frame) + int(math.ceil(end * fps))
    if out.camera is not None and out.camera.animation_data is not None:
        out.action = out.camera.animation_data.action
    return out


def remove_nis(build: NisBuild) -> None:
    """Delete everything :func:`build_nis` made (the collection, its objects, the Actions and camera data)."""
    coll = build.collection
    if coll is None:
        return
    cams = []
    acts = set(a for a in build.clip_actions if a is not None)
    for o in list(coll.all_objects):
        if o.animation_data is not None:
            if o.animation_data.action:
                acts.add(o.animation_data.action)
            for trk in o.animation_data.nla_tracks:
                acts.update(st.action for st in trk.strips if st.action)
        if o.type == "CAMERA":
            cams.append(o.data)
            if o.data.animation_data and o.data.animation_data.action:
                acts.add(o.data.animation_data.action)
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(coll.children):
        bpy.data.collections.remove(c)
    bpy.data.collections.remove(coll)
    for cd in cams:
        if cd.users == 0:
            bpy.data.cameras.remove(cd)
    for a in acts:
        try:
            if a.users == 0:
                bpy.data.actions.remove(a)
        except ReferenceError:
            pass
    build.collection = None
