"""The map's cinematics (``<map>.na``): an empty per key frame (``nis_*`` properties) plus path, speed line and an
approximate preview (the game plays the key values).

Saving writes the ``.na`` only if changed: unchanged values keep the file's text and untouched maps stay byte-identical.
The original is kept in the .blend and edits are read against it. Game (x, y up, z) is Blender (-x, -z, y).
"""
from __future__ import annotations

import json
import math
import os

import bpy
from mathutils import Matrix, Vector

from ICParse.sgb import nislet as N

from . import map_hooks as H
from . import ops_map_import as M

K_ROOT = "ic_nis_root"            # on the ``<map> cinematics`` collection
K_ANIM = "ic_nis_anim"            # on an animation's collection
K_KEY = "ic_nis_key"              # on a key frame's empty
K_PART = "ic_nis_part"            # on the path / speed line / preview objects: "path", "speed", "preview"
K_TEXT = "ic_nis_text"            # on the root: the name of the text that holds the original .na bytes ("" when there was no file)
K_BAD = "ic_nis_unreadable"       # on the root: the file beside the map could not be read, so it is never rewritten
K_GROUPS = "ic_nis_groups"        # on the root: the group ids the map had when it was opened (JSON)
K_ACTIVE = "ic_nis_active"        # on the root: the collection of the animation the panel shows
K_ROLL = "nis_engine_roll"        # on a key's empty: its arrows take a camera's roll the engine's way (up to 0.8.0 they did not)
K_SHOW_PATH = "nis_always"        # on an animation's collection: show the path and the speed line
K_SHOW_SPEED = "nis_show_speed"
TEXT_PREFIX = "Map cinematics "
KEY_SIZE = 3.0                    # metres: the size of a key's arrows
SAMPLES = 8                       # path curve points per segment
TIME_MIN = 0.05                   # s: the shortest time the preview gives a segment
SPEED_SCALE = 0.25                # metres of speed line height per unit of speed
TOLERANCE = 2e-4                  # a position this close to the file's value is the file's value (a Blender location is a 32-bit float)
KINDS = N.KINDS
LABEL = N.KIND_LABEL

# per-key values kept as custom properties of the empty: property name -> (nislet.Key field, default)
KEY_PROPS = {
    "nis_name": "name", "nis_speed": "speed", "nis_tension": "tension", "nis_continuity": "continuity", "nis_bias": "bias",
    "nis_look_ahead": "look_ahead", "nis_declination": "declination", "nis_rotation": "rotation", "nis_roll": "roll",
    "nis_zoom": "zoom", "nis_target_entity": "target_entity", "nis_target_group": "target_group",
}
PROP_LABELS = {
    "nis_name": "Name", "nis_speed": "Speed", "nis_tension": "Tension", "nis_continuity": "Continuity", "nis_bias": "Bias",
    "nis_look_ahead": "Look distance", "nis_declination": "Declination", "nis_rotation": "Rotation", "nis_roll": "Roll",
    "nis_zoom": "Zoom distance", "nis_target_entity": "Target entity", "nis_target_group": "Target group",
}


# ---------------------------------------------------------------------------------------------------- axes and angles
def g2b(p) -> tuple:
    """A point in the game's axes (x, y up, z) in Blender's."""
    return (-float(p[0]), -float(p[2]), float(p[1]))


def b2g(p) -> tuple:
    return (-float(p[0]), float(p[2]), -float(p[1]))


def _world(o) -> Matrix:
    """An object's matrix in the world without waiting for the depsgraph (keys have no parent unless the user gave them one)."""
    return o.matrix_world if o.parent else o.matrix_basis


def _loc(o) -> Vector:
    return _world(o).translation


def _norm(v: Vector) -> Vector:
    return v.normalized() if v.length > 1e-12 else v


def basis_for(rotation: float, declination: float, roll: float = 0.0, camera: bool = True) -> Matrix:
    """The 3x3 orientation (Blender's camera convention: the camera looks down -Z, +Y is up) of a view with the engine's rotation and
    declination (degrees; ``nislet.look_direction``) and roll (degrees). A camera and a moving object take the roll the opposite way
    round: a positive roll banks the game's camera clockwise seen from behind (the view is turned by ``RotateZ(roll)``,
    ``Camera_OrbitTarget::UpdateMatrix`` Util.dll 0x404e4deb), +roll about the look direction here; an object of a move animation
    turns by ``RotateX(roll)`` before the heading (``MoveAnim::Blend`` IC.exe 0x004ee25b), a turn in the game's left-handed axes,
    so -roll about its path here. ``camera`` says which."""
    f = Vector(g2b(N.look_direction(rotation, declination)))
    yaw = math.radians(rotation)
    h = Vector((math.sin(yaw), -math.cos(yaw), 0.0))                       # the horizontal part of the look direction, in Blender
    right = _norm(h.cross(Vector((0.0, 0.0, 1.0))))
    up = _norm(right.cross(f))
    if roll:
        rot = Matrix.Rotation(math.radians(roll if camera else -roll), 3, f)
        right, up = rot @ right, rot @ up
    m = Matrix((right, up, -f)).transposed()                                # columns: right, up, back
    return m


def angles_of(m3: Matrix, camera: bool = True) -> tuple:
    """``(rotation, declination, roll)`` in degrees of a Blender 3x3 orientation: the inverse of :func:`basis_for` (the roll is read
    from the up axis)."""
    f = -(m3 @ Vector((0.0, 0.0, 1.0)))
    rotation, declination = N.direction_angles(b2g(f))
    level = basis_for(rotation, declination, 0.0)
    up = m3 @ Vector((0.0, 1.0, 0.0))
    turn = math.degrees(math.atan2((level @ Vector((1.0, 0.0, 0.0))).dot(up), (level @ Vector((0.0, 1.0, 0.0))).dot(up)))
    return rotation, declination, (turn if camera else -turn) + 0.0        # + 0.0: never a minus zero in the file


# ---------------------------------------------------------------------------------------------------- finding things
def root_of(top, create: bool = False):
    """The map's ``<map> cinematics`` collection, or None (made when ``create``)."""
    found = next((c for c in top.children if c.get(K_ROOT)), None)
    if found is None and create:
        name = top.name[4:] if top.name.startswith("Map ") else top.name
        found = M._new_collection(f"{name} cinematics", top)
        found[K_ROOT] = True
        found[K_TEXT] = ""
    return found


def anim_collections(root) -> list:
    return [c for c in root.children if c.get(K_ANIM)]


def key_objects(coll) -> list:
    """The key frame empties of an animation's collection in key order."""
    keys = [o for o in coll.objects if o.get(K_KEY)]
    keys.sort(key=lambda o: (float(o.get("nis_index", 0.0)), o.name))
    return keys


def part_object(coll, part: str):
    return next((o for o in coll.objects if o.get(K_PART) == part), None)


def anim_of_object(obj):
    """The animation collection an object (a key, the path ...) belongs to, or None."""
    for c in obj.users_collection:
        if c.get(K_ANIM):
            return c
    return None


def base_text(root):
    return bpy.data.texts.get(root.get(K_TEXT, "") or "")


def baseline_bytes(root):
    t = base_text(root)
    return H.unpack_source(t.as_string()) if t is not None else None


# ---------------------------------------------------------------------------------------------------- reading the file beside the map
def read_na_beside(top, game_dir, siblings: dict | None = None):
    """The ``.na`` bytes that sit beside the map in the game's files (or on disk), or None. A map made in memory (a crop, a terrain
    import) has no file: its ``.na`` comes from ``siblings`` (``ictx.siblings``)."""
    src = top.get(M.KEY_SOURCE, "")
    archive = top.get(M.KEY_ARCHIVE, "")
    if not src:
        for (sub, rest), data in (siblings or {}).items():
            if not sub and rest.lower() == ".na" and data:
                return bytes(data)
        return None
    if archive:
        if not game_dir:
            return None
        gm = next((m for m in M.game_maps(game_dir) if m.key == src), None)
        if gm is None:
            return None
        import ICParse.sga as sga
        arc = sga.read(gm.path)
        want = gm.member.rsplit(".", 1)[0].lower() + ".na"
        for entry in arc.entries:
            if entry.path.replace("\\", "/").lower() == want:
                return arc.read(entry)
        return None
    base = os.path.splitext(src)[0]
    folder, stem = os.path.split(base)
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return None
    for n in names:
        if n.lower() == (stem + ".na").lower() and os.path.isfile(os.path.join(folder, n)):
            with open(os.path.join(folder, n), "rb") as f:
                return f.read()
    return None


# ---------------------------------------------------------------------------------------------------- building the scene
def _empty(name: str, coll, display: str = "ARROWS", size: float = KEY_SIZE):
    o = bpy.data.objects.new(name, None)
    M.stamp(o)
    o.empty_display_type = display
    o.empty_display_size = size
    coll.objects.link(o)
    return o


def _entity_positions(top) -> dict:
    """``{entity id: game position}`` of the map's placed objects as they stand now."""
    out = {}
    for o in top.all_objects:
        eid = o.get(M.KEY_ENTITY)
        if eid is not None:
            out[int(eid)] = b2g(o.matrix_world.translation)
    return out


def key_positions(coll) -> list:
    return [b2g(_loc(o)) for o in key_objects(coll)]


def _shown_basis(kind: str, i: int, n: int, pos: list, k: N.Key, targets: dict) -> Matrix:
    """The orientation the arrows of key ``i`` of ``n`` show."""
    rot, dec, roll = k.rotation, k.declination, k.roll
    if kind == "obj":
        rot, dec, roll = N.AUTO, N.AUTO, 0.0
    target = None
    if kind == "camera" and k.target_group == N.NO_TARGET and k.target_entity != N.NO_TARGET:   # a group's centre is not looked up here
        target = targets.get(k.target_entity)
    if target is not None:
        d = tuple(t - p for t, p in zip(target, pos[i]))
        rot, dec = N.direction_angles(d)
    elif rot == N.AUTO or dec == N.AUTO:
        a, b = (pos[i], pos[i + 1]) if i + 1 < n else (pos[i - 1], pos[i]) if i > 0 else (None, None)
        if a is None:
            d = (0.0, 0.0, 1.0)
        else:
            d = tuple(q - p for p, q in zip(a, b))
            if not any(d):
                d = (0.0, 0.0, 1.0)
        yaw, pitch = N.direction_angles(d)
        rot = yaw if rot == N.AUTO else rot
        dec = pitch if dec == N.AUTO else dec
    return basis_for(rot, dec, roll, camera=kind == "camera")


def set_key_orientation(o, basis: Matrix) -> None:
    """Turn a key's empty to ``basis`` and remember what was shown, so a later turn by the user can be told from none."""
    o.rotation_mode = "XYZ"
    o.rotation_euler = basis.to_euler()
    o["nis_shown_rot"] = list(o.rotation_euler.to_quaternion())
    o[K_ROLL] = True


def set_key_values(o, k: N.Key, kind: str) -> None:
    """Write a key's values into its empty: the location and the custom properties."""
    o.location = g2b((k.x, k.y, k.z))
    for prop, attr in KEY_PROPS.items():
        if N.kind_uses(kind, attr):
            v = getattr(k, attr)
            o[prop] = v if attr == "name" else (int(v) if attr in N.ID_FIELDS else float(v))
    o["nis_src"] = -1 if k.src is None else int(k.src)
    o["nis_orig"] = [k.x, k.y, k.z] if k.src is not None else []


def refresh_shapes(top, coll) -> None:
    """Redraw an animation's key arrows, path, speed line and preview from its keys as they stand now."""
    kind = coll.get("nis_kind", "camera")
    objs = key_objects(coll)
    pos = [b2g(_loc(o)) for o in objs]
    keys = [key_from_object(o, kind) for o in objs]
    targets = _entity_positions(top) if kind == "camera" else {}
    for i, o in enumerate(objs):
        set_key_orientation(o, _shown_basis(kind, i, len(objs), pos, keys[i], targets))
    _draw_path(coll, kind, pos, keys)
    _draw_preview(coll, kind, pos, keys, targets)


def _curve_object(coll, part: str, name: str):
    o = part_object(coll, part)
    if o is None:
        cu = bpy.data.curves.new(name, "CURVE")
        cu.dimensions = "3D"
        o = bpy.data.objects.new(name, cu)
        M.stamp(o)
        o[K_PART] = part
        coll.objects.link(o)
        o.hide_select = True
    return o


def _set_poly(o, points: list) -> None:
    cu = o.data
    cu.splines.clear()
    if len(points) < 2:
        return
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, q in zip(sp.points, points):
        p.co = (q[0], q[1], q[2], 1.0)


def _draw_path(coll, kind: str, pos: list, keys: list) -> None:
    name = coll.get("nis_name", "anim")
    path = _curve_object(coll, "path", f"{name} path")
    pts = []
    if len(pos) >= 2:
        for i in range(len(pos) - 1):
            for s in range(SAMPLES):
                pts.append(g2b(N.spline_point_segment(pos, i, s / SAMPLES)))
        pts.append(g2b(pos[-1]))
    _set_poly(path, pts)
    speed = _curve_object(coll, "speed", f"{name} speed")
    sp = []
    if len(pos) >= 2:
        for i in range(len(pos) - 1):
            for s in range(SAMPLES):
                u = s / SAMPLES
                x, y, z = N.spline_point_segment(pos, i, u)
                v = keys[i].speed + (keys[i + 1].speed - keys[i].speed) * u
                sp.append(g2b((x, y + v * SPEED_SCALE, z)))
        sp.append(g2b((pos[-1][0], pos[-1][1] + keys[-1].speed * SPEED_SCALE, pos[-1][2])))
    _set_poly(speed, sp)
    always, show = bool(coll.get(K_SHOW_PATH, True)), bool(coll.get(K_SHOW_SPEED, False))
    path.hide_viewport = not always
    speed.hide_viewport = not (always and show)


def segment_times(pos: list, keys: list) -> list:
    """The time in seconds the preview takes for each segment: its length over the mean speed of its two keys."""
    out = []
    for i in range(len(pos) - 1):
        length = math.dist(pos[i], pos[i + 1])
        v = max((keys[i].speed + keys[i + 1].speed) / 2.0, 0.1)
        out.append(max(length / v, TIME_MIN))
    return out


def _draw_preview(coll, kind: str, pos: list, keys: list, targets: dict) -> None:
    name = coll.get("nis_name", "anim")
    pv = part_object(coll, "preview")
    if pv is None:
        if kind == "camera":
            data = bpy.data.cameras.new(f"{name} preview")
            data.clip_end = 5000.0
            pv = bpy.data.objects.new(f"{name} preview", data)
        else:
            pv = bpy.data.objects.new(f"{name} preview", None)
            pv.empty_display_type = "CUBE"
            pv.empty_display_size = 1.5
        pv[K_PART] = "preview"
        M.stamp(pv)
        coll.objects.link(pv)
    pv.animation_data_clear()
    if len(pos) < 2:
        return
    fps = max(bpy.context.scene.render.fps, 1) / max(bpy.context.scene.render.fps_base, 1e-6)
    times = segment_times(pos, keys)
    t = 0.0
    frame0 = 1
    samples = []                                                  # (frame, game position, key index at or before)
    for i in range(len(pos) - 1):
        for s in range(4):
            u = s / 4
            samples.append((frame0 + (t + times[i] * u) * fps, N.spline_point_segment(pos, i, u), i, u))
        t += times[i]
    samples.append((frame0 + t * fps, tuple(pos[-1]), len(pos) - 2, 1.0))
    n = len(pos)
    for frame, p, i, u in samples:
        a = _shown_basis(kind, i, n, pos, keys[i], targets)
        b = _shown_basis(kind, min(i + 1, n - 1), n, pos, keys[min(i + 1, n - 1)], targets)
        q = a.to_quaternion().slerp(b.to_quaternion(), u)
        pv.rotation_mode = "QUATERNION"
        pv.location = g2b(p)
        pv.rotation_quaternion = q
        pv.keyframe_insert("location", frame=frame)
        pv.keyframe_insert("rotation_quaternion", frame=frame)
    coll["nis_frames"] = [int(round(samples[0][0])), int(round(samples[-1][0]))]


def build_anim(context, top, root, anim: N.Anim, index: int) -> object:
    """The collection of one animation (``index`` is its place in the file, -1 for a new one)."""
    coll = M._new_collection(f"{LABEL[anim.kind]} {anim.name}", root)
    coll[K_ANIM] = True
    coll["nis_kind"] = anim.kind
    coll["nis_id"] = int(anim.id)
    coll["nis_name"] = anim.name
    coll["nis_blend_in"] = float(anim.blend_in)
    coll["nis_blend_out"] = float(anim.blend_out)
    coll["nis_src"] = int(index)
    coll[K_SHOW_PATH] = True
    coll[K_SHOW_SPEED] = False
    for i, k in enumerate(anim.keys):
        o = _empty(f"{anim.name} key {i + 1}", coll)
        o[K_KEY] = True
        o["nis_index"] = float(i)
        set_key_values(o, k, anim.kind)
    context.view_layer.update()
    refresh_shapes(top, coll)
    return coll


def import_step(ictx) -> None:
    top = ictx.top
    root = root_of(top, create=True)
    raw = None
    try:
        raw = read_na_beside(top, ictx.game_dir, ictx.siblings)
    except Exception as exc:                                   # noqa: BLE001
        ictx.warn(f"The cinematics file beside the map could not be read: {exc}")
        root[K_BAD] = True
    try:
        root[K_GROUPS] = json.dumps(sorted({int(g.gid) for g in getattr(ictx.board, "groups", [])}))
    except Exception:                                          # noqa: BLE001
        root[K_GROUPS] = "[]"
    if raw is None:
        ictx.report["cinematics"] = {"file": False, "animations": 0}
        return
    text = bpy.data.texts.new(TEXT_PREFIX + top.name)
    text.write(H.pack_source(raw))
    root[K_TEXT] = text.name
    try:
        nf = N.NisFile.parse(raw)
    except N.NisError as exc:
        root[K_BAD] = True
        ictx.warn(f"The cinematics file beside the map could not be understood ({exc}). It is left as it is and not saved again.")
        ictx.report["cinematics"] = {"file": True, "animations": 0, "error": str(exc)}
        return
    for i, a in enumerate(nf.anims):
        build_anim(ictx.context, top, root, a, i)
    colls = anim_collections(root)
    if colls:
        root[K_ACTIVE] = colls[0].name
    ictx.report["cinematics"] = {"file": True, "animations": len(nf.anims),
                                 "keys": sum(len(a.keys) for a in nf.anims), "bytes": len(raw)}


# ---------------------------------------------------------------------------------------------------- reading the scene back
def key_from_object(o, kind: str) -> N.Key:
    """The :class:`nislet.Key` of a key's empty. The position is the empty's location unless it still equals the file's value."""
    gx, gy, gz = b2g(_loc(o))
    orig = list(o.get("nis_orig", []))
    if len(orig) == 3 and max(abs(gx - orig[0]), abs(gy - orig[1]), abs(gz - orig[2])) <= TOLERANCE:
        gx, gy, gz = orig
    k = N.Key(x=gx, y=gy, z=gz)
    src = int(o.get("nis_src", -1))
    k.src = src if src >= 0 else None
    for prop, attr in KEY_PROPS.items():
        if prop in o and N.kind_uses(kind, attr):
            v = o[prop]
            setattr(k, attr, str(v) if attr == "name" else int(round(v)) if attr in N.ID_FIELDS else float(v))
    if kind != "obj":
        shown = o.get("nis_shown_rot")
        q = o.rotation_euler.to_quaternion() if o.rotation_mode == "XYZ" else o.rotation_quaternion
        if shown is not None and _turned(shown, q):                                   # the user turned the key's arrows
            # arrows a 0.8.0 file drew (no K_ROLL) are read the way they were drawn
            rot, dec, roll = angles_of(_world(o).to_3x3().normalized(), camera=kind == "camera" and bool(o.get(K_ROLL)))
            k.rotation, k.declination, k.roll = round(rot, 5), round(dec, 5), round(roll, 5)
    return k


def _turned(shown, q) -> bool:
    """Whether the orientation ``q`` differs from the ``shown`` one (four numbers) by more than float noise (about 0.06 degrees)."""
    a = [float(v) for v in shown]
    b = [float(v) for v in q]
    same = max(abs(x - y) for x, y in zip(a, b))
    flip = max(abs(x + y) for x, y in zip(a, b))
    return min(same, flip) > 1e-3 * 0.5


def anim_from_collection(coll) -> N.Anim:
    kind = coll.get("nis_kind", "camera")
    a = N.Anim(kind=kind, id=int(coll.get("nis_id", 0)), name=str(coll.get("nis_name", "")), blend_in=float(coll.get("nis_blend_in", N.DEFAULT_BLEND)),
               blend_out=float(coll.get("nis_blend_out", N.DEFAULT_BLEND)))
    a.keys = [key_from_object(o, kind) for o in key_objects(coll)]
    return a


def scene_to_file(root, nf: N.NisFile) -> N.NisFile:
    """Apply the scene's animations to ``nf`` (read from the original file): edited ones are changed, deleted ones removed, new ones added."""
    seen = set()
    fresh = []
    for coll in anim_collections(root):
        cur = anim_from_collection(coll)
        src = int(coll.get("nis_src", -1))
        if 0 <= src < len(nf.anims) and src not in seen:
            seen.add(src)
            old = nf.anims[src]
            old.id, old.name, old.blend_in, old.blend_out, old.keys = cur.id, cur.name, cur.blend_in, cur.blend_out, cur.keys
        else:
            fresh.append(cur)
    for i in reversed(range(len(nf.anims))):
        if i not in seen:
            nf.anims.pop(i)
    for cur in fresh:
        nf.anims.append(cur)
    return nf


def save_step(sctx) -> None:
    root = root_of(sctx.top)
    if root is None or root.get(K_BAD):
        return
    raw = baseline_bytes(root)
    try:
        nf = N.NisFile.parse(raw) if raw is not None else N.NisFile.empty()
    except N.NisError as exc:
        sctx.add_warning(f"The cinematics could not be compared with the original file ({exc}), so they were not saved.")
        return
    scene_to_file(root, nf)
    if not nf.is_dirty():
        return
    new = nf.to_bytes()
    if raw is not None and new == raw:
        return
    for w in problems_for_save(sctx, nf):
        sctx.add_warning(w)
    target = f"{sctx.name}.na"
    for fname in list(sctx.files):                          # a file beside a loose map may be spelled .NA: one file, spelled the game's way
        if fname != target and fname.lower() == target.lower():
            sctx.files[fname] = None
    sctx.files[target] = new
    kinds = {k: len(nf.of_kind(k)) for k in KINDS}
    sctx.add_change(f"Cinematics: {len(nf.anims)} animations written to {sctx.name}.na ("
                    + ", ".join(f"{n} {LABEL[k].lower()}" for k, n in kinds.items() if n) + ")")


def problems_for_save(sctx, nf: N.NisFile) -> list:
    """Plain-words warnings for the save report: triggers that name an animation the map no longer has."""
    out = []
    trg = sctx.files.get(f"{sctx.name}.trg")
    if not trg:
        return out
    try:
        from ICParse.sgb import triggers as T
        script = T.TriggerScript.from_lua(bytes(trg).decode("latin1"))
    except Exception:                                          # noqa: BLE001
        return out
    wanted = {15: "camera", 16: "obj", 17: "move"}
    missing = set()
    for triggers in script.folders.values():
        for t in triggers:
            for item in list(t.conditions) + list(t.actions):
                for arg in item.args:
                    kind = wanted.get(arg.type)
                    if kind and nf.find(kind, int(arg.val)) is None:
                        missing.add((kind, int(arg.val)))
    for kind, ident in sorted(missing):
        out.append(f"The triggers use {LABEL[kind].lower()} animation {ident}, which the map no longer has.")
    return out


# ---------------------------------------------------------------------------------------------------- checks
def check(cctx) -> list:
    root = root_of(cctx.top)
    if root is None:
        return []
    out = []
    entities = {int(o[M.KEY_ENTITY]) for o in cctx.top.all_objects if o.get(M.KEY_ENTITY) is not None}
    try:
        groups = set(json.loads(root.get(K_GROUPS, "[]")))
    except ValueError:
        groups = set()
    ids = {}
    for coll in anim_collections(root):
        kind = coll.get("nis_kind", "camera")
        label = f"{LABEL[kind]} animation {coll.get('nis_name', coll.name)}"
        ids.setdefault((kind, int(coll.get("nis_id", 0))), []).append(label)
        keys = key_objects(coll)
        if len(keys) < 2:
            out.append(H.problem("warning", f"{label} has {len(keys)} key frames: it needs at least 2 to play.", coll.name))
        for o in keys:
            e, g = int(o.get("nis_target_entity", -1)), int(o.get("nis_target_group", -1))
            if kind == "camera" and e != -1 and e not in entities:
                out.append(H.problem("warning", f"{label}: a key looks at entity {e}, which is not in the map (deleted?). The game then "
                                                "looks towards the map's corner instead.", o.name))
            if kind == "camera" and g != -1 and groups and g not in groups:
                out.append(H.problem("warning", f"{label}: a key looks at group {g}, which the map did not have when it was opened.", o.name))
    for (kind, ident), labels in ids.items():
        if len(labels) > 1:
            out.append(H.problem("error", f"{LABEL[kind]} animations {', '.join(labels)} share the number {ident}: the triggers could not tell "
                                          "them apart.", None))
    return out


H.register_import_step("cinematics", import_step, order=120)
H.register_save_step("cinematics", save_step, order=120)
H.register_check("cinematics", check)
