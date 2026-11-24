"""sceneobj.map_replay -- act a cinematic out on Blender's timeline (the "Replay").

What it does. The game plays a cinematic by running the map's trigger script (``<map>.trg``) together with the simulation that carries
out its orders. ``ICParse.sgb.cine_timeline`` reads that script and says, second by second, what happens (a :class:`Timeline` of
events, each marked exact, approximate or skipped). This module turns such a timeline into a collection ``<map> replay``:

* a **stand-in** for every object the cinematic touches: an instance of the very model the placed object uses, keyframed from the
  events (so the map's own objects are never moved: the save of a map reads those). While the replay is shown the originals it
  stands in for are hidden in the 3D view; clearing the replay shows them again;
* one **replay camera** that plays the cinematic's camera animations one after the other (play, cut, pause, resume, stop), and
  timeline markers bound to cameras so the scene camera switches by itself (also to an NIS camera);
* the frame rate and the frame range of the scene are set to the cinematic (and put back when the replay is cleared);
* a text ``Replay <cinematic>`` that lists every event with its time, what it does, and whether it is exact, approximate or skipped.

What each event does on the timeline (frames are ``1 + seconds x 30``):

========================  =====================================================================================================
teleport                  a step in position (constant keys); the heading too when it is given
walk                      location keys along the route (linear, a key per path point and at least every 3 m, on the ground),
                          the heading turns at each bend; the time is the event's ``t_end`` or the route over the speed
face / look_at            the stand-in turns at that time (at once: the game turns the creature over a moment)
despawn / delete          hidden from then on (``hide_viewport`` and ``hide_render`` keys, constant)
respawn / visible / create  shown again (a created object is hidden before it exists, at the given place)
anim                      the clip number is keyed on the custom property ``replay_anim`` and, where the model has a clip of that
                          name, the clip plays on a copy of the model (see ``clip_for``)
motion_var                a keyed custom property ``mv_<name>`` of the stand-in
move_anim / obj_anim      the stand-ins follow the 0.7.0 path of that animation (``map_cinematics``), at its speeds
nis start / end           ``map_nis.build_nis`` builds the animated stand-in and its camera; shown between start and end
camera                    the replay camera blends to the animation's first point (its blend-in time), then follows the path at the
                          animation's speeds; pause holds it, resume goes on, cut / a new play takes over from where it is
anim_camera use / done    a marker binds the scene camera to the NIS camera and back
fade                      a black plane in front of the replay camera whose opacity is keyed
cinematic_mode, focus,    listed with a marker, not drawn
dialog
========================  =====================================================================================================

Axes. The timeline speaks the game's axes (x, y up, z); Blender's are (x, -z, y), as for every placed object. The timeline's heading is
the game's own, in radians (0 for the identity matrix, the unit looks along (sin h, cos h) in x, z: ``cine_timeline.heading_of``); the
replay turns it into degrees about Blender's up axis (``-degrees(h)``) and keys that. The stand-in's own facing at the start is the
placed object's matrix applied to +z.

Where the rules come from (decompiled, IC.exe, decomp/ic/src/Simulation; the trigger tick of 1/8 s, the trigger passes, the walks and the NIS lengths are
``cine_timeline``'s and ``map_nis``'s, which cite their own code):

* the camera: ``CameraAnim::Play`` 0x004e418c / ``Restart`` 0x004e43ee (blend from where the camera is to the first point over the blend-in time, unless
  already there), ``PathAnim::Update`` 0x004f1c9e (the blend in moves nothing, then ``dist += dt * speed``), ``CameraAnim::CutFrom`` 0x004e4635 (a cut takes
  over from the pose the old animation left); how far along the path an animation is at each moment is the timeline's ``samples``;
* the fade: ``FadeAnim.cpp`` 98 / 126 / 181 (a black quad of alpha ``m_current``, linear from ``from`` to ``to`` over the duration; 1 is black);
* a move animation: ``MoveAnim_SetEntity`` 0x004edfb4 / ``WarpEntityTransform`` 0x004ee0d0 (one entity, the path's own positions) -- inferred;
* an animation text: ``PlayAnimation`` sets a motion NODE of the blueprint (``cine_timeline``: 0x004c85df); the node's clip is a leaf below it, the last that is not
  an idle (inferred: the conditions that pick among children are not run), looping when bit 0 of the node flags is set (as ``nis_scene.schedule`` reads it).

The replay never changes the map: its objects live in their own collection outside the map's, carry no ``ic_*`` key the save reads,
and the originals are only hidden in the view layer.
"""
from __future__ import annotations

import json
import math
import time
import types
from dataclasses import dataclass

import bpy
from mathutils import Matrix, Quaternion, Vector

from ICParse.sgb import nislet as N

from . import map_cinematics as C
from . import ops_map_export as X
from . import ops_map_import as M

ORDER = 55
TAIL = 2.0                        # s: how long the replay goes on after the last thing that happens
MAX_CLIP_STANDINS = 12            # the most stand-ins that get their model's clips (each is a copy of the model)

FPS = 30                          # the replay's frame rate (the game's cinematics are time based; 30 frames a second is smooth enough to look at)
FRAME0 = 1                        # the frame of time 0
WALK_STEP = 3.0                   # m: a walk gets a key at least this often (so the stand-in follows the ground)
WALK_KEYS_MAX = 600               # the most keys one walk gets
SUBSAMPLES = 4                    # camera keys per path segment (the 0.7.0 preview uses the same)
TURN_MIN = 2.0                    # degrees: a bend smaller than this gets no heading key
LINEAR, CONSTANT = "LINEAR", "CONSTANT"
K_REPLAY = "ic_replay"            # on the replay collection: the cinematic's key
K_TITLE = "ic_replay_title"
K_HIDDEN = "ic_replay_hidden"     # on the replay collection: JSON names of the originals this replay hid
K_TEXT = "ic_replay_text"         # on the replay collection: the name of the event list text
K_PART = "ic_replay_part"         # on every object of a replay: "standin", "camera", "fade", "nis", "model"
K_ENTITY = "ic_replay_entity"     # on a stand-in: the entity id it stands for
K_PREV = "ic_replay_prev"         # on the scene: JSON of its frame rate, frame range, camera and frame before the replay
K_ACTIVE = "ic_replay_active"     # on the map collection: the key of the cinematic the panel shows
QUALITY_WORD = {"exact": "exact", "approximate": "approximate", "skipped": "skipped"}


class ReplayError(Exception):
    """A plain-words reason why a replay could not be built."""


# ---------------------------------------------------------------------------------------------------- small helpers
def _g(obj, name, default=None):
    """An attribute of an event / cinematic, or a key of a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def frame_of(t: float) -> int:
    return FRAME0 + int(round(float(t) * FPS))


def time_of(frame: float) -> float:
    return (float(frame) - FRAME0) / FPS


def g2b(p) -> Vector:
    return Vector(C.g2b(p))


def b2g(p) -> tuple:
    return C.b2g(p)


def heading_of_dir(dx: float, dz: float) -> float:
    """The heading in degrees of a horizontal direction (game axes): 0 faces +z, 90 faces -x."""
    return math.degrees(math.atan2(-dx, dz))


def _wrap_near(h: float, ref: float) -> float:
    return ref + ((h - ref + 180.0) % 360.0) - 180.0


def heading_turn(delta_deg: float) -> Matrix:
    """The 3x3 in Blender's axes that turns a game heading by ``delta_deg`` (a game turn about +y is a Blender turn about -z)."""
    return Matrix.Rotation(-math.radians(delta_deg), 3, "Z")


def base_heading(m3: Matrix) -> float:
    """The heading a placed object faces at the start: its matrix applied to the game's +z (Blender -y)."""
    f = m3 @ Vector((0.0, -1.0, 0.0))
    gx, _gy, gz = b2g(f)
    if abs(gx) + abs(gz) < 1e-9:
        return 0.0
    return heading_of_dir(gx, gz)


def standin_quaternion(base3: Matrix, h0: float, h: float) -> Quaternion:
    return (heading_turn(h - h0) @ base3).to_quaternion()


def _clean_ref(blueprint: str) -> str:
    ref = str(blueprint or "").replace("\\", "/")
    if ref.upper().startswith("DATA:"):
        ref = ref[5:]
    return ref.lstrip("/").lower()


# ---------------------------------------------------------------------------------------------------- the ground
class Ground:
    """The terrain's height at a game position, read once (``ops_map_export.ground_height_at`` reads every point on each call)."""

    def __init__(self, terrain):
        self.ok = False
        if terrain is None:
            return
        try:
            import numpy as np
            self._np = np
            me = terrain.data
            self.w, self.h = int(terrain["ic_map_width"]), int(terrain["ic_map_height"])
            self.ts = float(terrain["ic_tile_scale"])
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            self.z = co.reshape(-1, 3)[:, 2].astype(np.float64).reshape(self.h, self.w)
            self.inv = terrain.matrix_world.inverted()
            self.mw = terrain.matrix_world.copy()
            self.ok = True
        except Exception:                                      # noqa: BLE001 -- no ground: stand-ins keep their height
            self.ok = False

    def height(self, gx: float, gz: float):
        """The ground's height (game y) at game x, z; None outside the map or without a ground."""
        if not self.ok:
            return None
        p = self.inv @ Vector((gx, -gz, 0.0))
        fx_, fz_ = p.x / self.ts + (self.w - 1) / 2.0, -p.y / self.ts + (self.h - 1) / 2.0
        if fx_ < 0 or fz_ < 0 or fx_ > self.w - 1 or fz_ > self.h - 1:
            return None
        i, j = min(int(fx_), self.w - 2), min(int(fz_), self.h - 2)
        fx, fz = fx_ - i, fz_ - j
        z = self.z
        local = (z[j, i] * (1 - fx) * (1 - fz) + z[j, i + 1] * fx * (1 - fz) + z[j + 1, i] * (1 - fx) * fz + z[j + 1, i + 1] * fx * fz)
        return float((self.mw @ Vector((p.x, p.y, float(local)))).z)


# ---------------------------------------------------------------------------------------------------- what one stand-in does
class Track:
    """Everything one stand-in does, in the game's axes and in seconds. Event application writes it; ``write_track`` turns it into keys."""

    def __init__(self, eid: int, label: str, pos, heading: float, dy: float, created: bool = False, h0: float | None = None):
        self.eid, self.label, self.dy = eid, label, dy
        self.h0 = float(heading if h0 is None else h0)         # the heading the placed object's own orientation has (0 for a new one)
        self.loc: list = [[0.0, tuple(pos), CONSTANT]]        # [t, (x, y, z), interpolation to the next key]
        self.rot: list = [[0.0, float(heading), CONSTANT]]    # [t, heading, interpolation to the next key]
        self.vis: list = [[0.0, not created]]
        self.props: dict = {}                                 # custom property name -> [[t, value]]
        self.anims: list = []                                 # [t, name, loop (None: the motion node says), the event's row]
        self.created = created

    # -- reading
    def pos_at(self, t: float) -> tuple:
        keys = self.loc
        if t <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t < b[0]:
                if a[2] == LINEAR and b[0] > a[0]:
                    u = (t - a[0]) / (b[0] - a[0])
                    return tuple(p + (q - p) * u for p, q in zip(a[1], b[1]))
                return a[1]
        return keys[-1][1]

    def heading_at(self, t: float) -> float:
        keys = self.rot
        if t <= keys[0][0]:
            return keys[0][1]
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t < b[0]:
                if a[2] == LINEAR and b[0] > a[0]:
                    return a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0])
                return a[1]
        return keys[-1][1]

    def visible_at(self, t: float) -> bool:
        v = self.vis[0][1]
        for k in self.vis:
            if k[0] <= t:
                v = k[1]
        return v

    # -- writing (always in time order; a new event cuts what an earlier one had planned beyond it)
    def cut(self, t: float) -> None:
        keys = self.loc
        if keys[-1][0] > t:
            p = self.pos_at(t)
            keys[:] = [k for k in keys if k[0] <= t]
            if keys[-1][0] == t:
                keys[-1][2] = CONSTANT
            else:
                keys.append([t, p, CONSTANT])
        self.cut_rot(t)

    def cut_rot(self, t: float) -> None:
        rot = self.rot
        if rot[-1][0] > t:
            h = self.heading_at(t)
            rot[:] = [k for k in rot if k[0] <= t]
            if not rot:
                rot.append([0.0, h, CONSTANT])
            elif rot[-1][0] == t:
                rot[-1][2] = CONSTANT
            else:
                rot.append([t, h, CONSTANT])

    def set_loc(self, t: float, pos, interp: str = CONSTANT) -> None:
        self.cut(t)
        pos = tuple(float(v) for v in pos)
        if self.loc[-1][0] == t:
            self.loc[-1][1], self.loc[-1][2] = pos, interp
        else:
            self.loc.append([t, pos, interp])

    def add_loc(self, t: float, pos, interp: str) -> None:
        """A key after ``set_loc`` / ``cut`` already made room (no cutting)."""
        pos = tuple(float(v) for v in pos)
        if self.loc[-1][0] == t:
            self.loc[-1][1], self.loc[-1][2] = pos, interp
        else:
            self.loc.append([t, pos, interp])

    def set_rot(self, t: float, heading: float, interp: str = CONSTANT) -> None:
        self.cut_rot(t)
        h = _wrap_near(float(heading), self.rot[-1][1])
        if self.rot[-1][0] == t:
            self.rot[-1][1], self.rot[-1][2] = h, interp
        else:
            self.rot.append([t, h, interp])

    def turn(self, t: float, heading: float, seconds: float = 0.0) -> None:
        """Turn to ``heading`` at time ``t`` (over ``seconds``, evenly, when that is more than a moment)."""
        if seconds > 0.05:
            self.set_rot(t, self.heading_at(t), LINEAR)
            self.set_rot(t + seconds, heading, CONSTANT)
        else:
            self.set_rot(t, heading)

    def set_vis(self, t: float, on: bool) -> None:
        if self.vis[-1][0] == t:
            self.vis[-1][1] = bool(on)
        elif self.vis[-1][1] != bool(on):
            self.vis.append([t, bool(on)])

    def set_prop(self, t: float, name: str, value) -> None:
        rows = self.props.setdefault(name, [])
        if rows and rows[-1][0] == t:
            rows[-1][1] = value
        else:
            rows.append([t, value])


def resample(points: list, step: float, limit: int) -> list:
    """``[(x, z, original index or None)]``: the polyline's points with extra points so that no gap is longer than ``step`` (and at most
    ``limit`` points in all)."""
    total = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    step = max(step, total / max(limit - len(points), 1))
    out = [(points[0][0], points[0][1], 0)]
    for i, (a, b) in enumerate(zip(points, points[1:])):
        d = math.dist(a, b)
        n = int(d // step)
        for k in range(1, n + 1):
            u = k * step / d
            if u < 1.0 - 1e-9:
                out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, None))
        out.append((b[0], b[1], i + 1))
    return out


# ---------------------------------------------------------------------------------------------------- the event list (text)
@dataclass
class Row:
    t: float
    kind: str
    text: str
    quality: str
    why: str = ""
    shown: str = ""               # how the replay shows it (plain words), when that differs from the event or fails
    inner: bool = False           # an event inside an NIS scene (listed, not one of the timeline's events)


def _who(eids, labels: dict, limit: int = 3) -> str:
    names = [labels.get(int(e), f"object {int(e)}") for e in eids]
    if not names:
        return "nobody"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


def describe(ev, labels: dict) -> str:
    """One event in plain words."""
    kind, d, who = _g(ev, "kind", ""), dict(_g(ev, "data", {}) or {}), _who(_g(ev, "entities", []) or [], labels)

    def xz(p):
        if p and isinstance(p[0], (list, tuple)):
            p = p[0]
        return f"({float(p[0]):.0f}, {float(p[-1]):.0f})" if p else "?"
    if kind == "teleport":
        return f"{who} jumps to {xz(d.get('pos'))}"
    if kind == "walk":
        path = d.get("path") or []
        if d.get("stop"):
            return f"{who} stops walking"
        if not path:
            return f"{who} is ordered to move (no route)"
        return f"{who} walks {len(path)} points to {xz(path[-1]) if path else '?'} at {float(d.get('speed', 0) or 0):.1f} m/s"
    if kind == "despawn":
        return f"{who} disappears (despawned)"
    if kind == "respawn":
        return f"{who} appears again (respawned)"
    if kind == "delete":
        return f"{who} is deleted"
    if kind == "visible":
        return f"{who} {'is shown' if d.get('on', True) else 'is hidden'}"
    if kind == "create":
        return f"{who} is created ({_clean_ref(d.get('blueprint', ''))}) at {xz(d.get('pos'))}"
    if kind == "face":
        tgt = d.get("toward") or d.get("target")
        h = d.get("heading")
        h = h[0] if isinstance(h, (list, tuple)) and h else h
        return f"{who} turns towards {_who(tgt, labels)}" if tgt else f"{who} turns to heading {-math.degrees(float(h or 0)):.0f}"
    if kind == "look_at":
        return f"{who} looks at {_who(d.get('toward') or d.get('target') or [], labels)}"
    if kind == "anim":
        return f"{who} plays the animation '{d.get('name', '?')}'" + (" (looping)" if d.get("loop") else "")
    if kind == "motion_var":
        return f"{who} gets the motion variable {d.get('name', '?')} = {d.get('value')}"
    if kind == "move_anim":
        return f"{who} follows move animation {d.get('anim', d.get('id', '?'))}"
    if kind == "obj_anim":
        return f"{who} follows object animation {d.get('anim', d.get('id', '?'))}"
    if kind == "camera":
        a = d.get("action", "play")
        n = d.get("anim", d.get("id"))
        return f"camera: {a}" + (f" animation {n}" if n is not None else "") + (f" from {float(d['start_m']):.0f} m" if d.get("start_m") else "")
    if kind == "nis":
        return f"NIS {d.get('action', 'start')}: {who}"
    if kind == "anim_camera":
        if d.get("action", "use") != "use":
            return "the animation camera is given back"
        return f"the camera is put on {who}'s animation camera"
    if kind == "fade":
        return f"fade from {float(d.get('from', 0) or 0):.1f} to {float(d.get('to', 0) or 0):.1f} over {float(d.get('duration_s', 0) or 0):.1f} s"
    if kind == "cinematic_mode":
        return f"cinematic mode {'on' if d.get('on', True) else 'off'}"
    if kind == "dialog":
        return f"dialog {d.get('id', '?')} (about {float(d.get('duration_s', 0) or 0):.0f} s)"
    if kind == "focus":
        return "the game's view moves to " + (xz(d["pos"]) if d.get("pos") else who)
    return f"{kind} {who}".strip()


def events_text(title: str, tl, rows: list, report: dict) -> str:
    """The text of the event list block: a header, the notes, the counts and one line per event."""
    lines = [f"Replay of the cinematic: {title}", f"Length {float(_g(tl, 'duration_s', 0) or 0):.1f} s; frames {report.get('frames', ['?', '?'])[0]} to "
             f"{report.get('frames', ['?', '?'])[1]} at {FPS} a second. Press Space in the 3D view to play it.", ""]
    q = report.get("quality", {})
    lines.append(f"{sum(q.values())} events: {q.get('exact', 0)} exact, {q.get('approximate', 0)} approximate, {q.get('skipped', 0)} skipped.")
    lines.append("exact = the outcome is fixed by the script; approximate = the simulation decides it, the replay follows a route / a length "
                 "of time it worked out; skipped = not drawn (the reason is on the line).")
    notes = list(_g(tl, "notes", []) or []) + list(report.get("notes", []))
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines += [f"  - {n}" for n in notes]
    lines += ["", "   time  how it is shown   what happens", "-" * 78]
    for r in rows:
        extra = "; ".join(x for x in (r.why, r.shown) if x)
        lines.append(f"{r.t:7.2f} s  {QUALITY_WORD.get(r.quality, r.quality):<12}  {r.text}" + (f"   [{extra}]" if extra else ""))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------- the timeline reader (made by another module)
def _cine():
    try:
        from ICParse.sgb import cine_timeline
    except ImportError as exc:
        raise ReplayError("The cinematic reader of the format library (ICParse.sgb.cine_timeline) is not installed, so the replay "
                          f"cannot read the map's scripts ({exc}).") from exc
    return cine_timeline


def _nis_builder():
    try:
        from . import map_nis
    except ImportError:
        return None
    return map_nis


# ---------------------------------------------------------------------------------------------------- the map's files
_INPUTS: dict = {}            # map collection name -> (identity, board, side_files)
_LISTS: dict = {}             # map collection name -> (identity, [Cinematic])


def gather_inputs(context, top):
    """``(board, side_files)`` for the timeline reader: the map's board as it was opened, and ``{name: bytes}`` of the files beside it
    (``.trg``, ``.na``, ``.dlg`` ...). The trigger and cinematics files are the ones the editor holds now, so a replay shows edits too."""
    raw, mv, gm = X.read_source(context, top)
    src = top.get(M.KEY_SOURCE, "")
    stem = X.map_name(top)
    side: dict = {}
    try:
        for sub, rest, data in X.sibling_files(types.SimpleNamespace(gm=gm), src):
            if not sub:
                side[stem + rest.lower() if rest.lower() in (".trg", ".na", ".dlg") else stem + rest] = bytes(data)
    except Exception:                                          # noqa: BLE001 -- a map with no readable neighbours has no files beside it
        pass
    try:                                                       # the triggers as the script editor holds them
        from . import map_script as S
        if top.get(S.KEY_LOADED) and S.fingerprints(top).get("triggers") != S.recorded_fingerprints(top).get("triggers"):
            side[stem + ".trg"] = S.triggers_doc(top).to_text().encode("latin1")
    except Exception:                                          # noqa: BLE001 -- the file's own text is used
        pass
    root = C.root_of(top)
    if root is not None and not root.get(C.K_BAD):
        try:                                                   # the cinematics as the scene holds them
            base = C.baseline_bytes(root)
            nf = N.NisFile.parse(base) if base is not None else N.NisFile.empty()
            C.scene_to_file(root, nf)
            side[stem + ".na"] = nf.to_bytes() if nf.is_dirty() else (base or b"")
        except Exception:                                      # noqa: BLE001
            pass
    return mv.board, {k: v for k, v in side.items() if v}


def _key(top) -> str:
    return f"{top.name}:{top.as_pointer()}"                    # a map imported again under the same name is another map


def inputs_of(top):
    """``(board, side_files)`` the last :func:`cinematics` call read for ``top`` (None before it)."""
    hit = _INPUTS.get(_key(top))
    return hit[1:] if hit is not None else None


def _identity(top) -> tuple:
    root = C.root_of(top)
    na = ""
    if root is not None:
        na = json.dumps([[c.get("nis_id"), len(C.key_objects(c)), tuple(round(v, 3) for o in C.key_objects(c) for v in o.location)]
                         for c in C.anim_collections(root)], default=str)
    return (top.name, hash(na))


def cinematics(context, top, refresh: bool = False) -> list:
    """The map's cinematics (``cine_timeline.list_cinematics``), kept until the key frames of the map's own cinematics move or ``refresh``.
    The kept list and the files it was read from (:func:`inputs_of`) do not follow other edits (a trigger, a key's speed), so a
    replay is built with ``refresh=True`` (:func:`build_replay`)."""
    ident = _identity(top)
    hit = _LISTS.get(_key(top))
    if hit is not None and hit[0] == ident and not refresh:
        return hit[1]
    board, side = gather_inputs(context, top)
    _INPUTS[_key(top)] = (ident, board, side)
    found = list(_cine().list_cinematics(board, side))
    _LISTS[_key(top)] = (ident, found)
    return found


def cached_cinematics(top):
    """What :func:`cinematics` found last, or None (a panel may not read files while it draws)."""
    hit = _LISTS.get(_key(top))
    return hit[1] if hit is not None else None


# ---------------------------------------------------------------------------------------------------- finding things
def replay_of(top):
    """The map's replay collection, or None."""
    for c in bpy.data.collections:
        if c.get(K_REPLAY) is not None and c.get("ic_replay_map") == top.name:
            return c
    return None


def placed_objects(top) -> dict:
    """``{entity id: the placed object}`` of the map."""
    out = {}
    for o in top.all_objects:
        eid = o.get(M.KEY_ENTITY)
        if eid is not None and "ic_blueprint" in o and M.KEY_MODEL not in o:
            out.setdefault(int(eid), o)
    return out


def standins(coll) -> dict:
    return {int(o[K_ENTITY]): o for o in coll.objects if o.get(K_PART) == "standin"}


def camera_of(coll):
    return next((o for o in coll.objects if o.get(K_PART) == "camera"), None)


# ---------------------------------------------------------------------------------------------------- writing keys
def _fcurves_of(obj):
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return []
    return list(M._addon("actions")._fcurves(ad.action))


def _key_rows(obj, prop: str, rows: list, set_value) -> int:
    """Key ``prop`` of ``obj`` at ``rows`` (``[(frame, value, interpolation)]``; the last row at a frame wins) and set each key's
    interpolation. Returns the number of keys."""
    by_frame = {}
    for f, v, interp in rows:
        by_frame[int(f)] = (v, interp)
    for f in sorted(by_frame):
        set_value(by_frame[f][0])
        obj.keyframe_insert(prop, frame=f)
    for fc in _fcurves_of(obj):
        if fc.data_path != prop:
            continue
        for kp in fc.keyframe_points:
            kp.interpolation = by_frame.get(int(round(kp.co[0])), (None, CONSTANT))[1]
    return len(by_frame)


def key_location(obj, rows) -> int:
    return _key_rows(obj, "location", rows, lambda v: setattr(obj, "location", v))


def key_rotation(obj, rows) -> int:
    return _key_rows(obj, "rotation_quaternion", rows, lambda v: setattr(obj, "rotation_quaternion", v))


def key_bool(obj, prop: str, rows) -> int:
    return _key_rows(obj, prop, [(f, v, CONSTANT) for f, v in rows], lambda v: setattr(obj, prop, bool(v)))


def key_custom(obj, name: str, rows) -> int:
    path = f'["{name}"]'
    return _key_rows(obj, path, [(f, v, CONSTANT) for f, v in rows], lambda v: obj.__setitem__(name, v))


# ---------------------------------------------------------------------------------------------------- the camera
class _Skip(Exception):
    """An event the replay cannot show; ``args[0]`` is the reason in plain words."""


class PathTimes:
    """A NISlet animation's path as the game moves along it (``ICParse.sgb.cine_pathanim``, a port of the decompiled path code: the Catmull-Rom
    position, the Kochanek-Bartels speed, ``dist += dt * speed``). The timeline says how far along the path an animation is at each tick
    (``samples``); this turns a distance in metres into a place on the path.

    ``at_dist(m)`` is ``(game position, segment index, position along the segment 0..1)``; ``length`` is the path's length in metres."""

    def __init__(self, coll, kind: str):
        try:
            from ICParse.sgb import cine_pathanim as P
        except ImportError as exc:
            raise ReplayError(f"The path code of the format library (ICParse.sgb.cine_pathanim) is not installed ({exc}).") from exc
        self.kind = kind
        objs = C.key_objects(coll)
        self.pos = [b2g(C._loc(o)) for o in objs]
        self.keys = [C.key_from_object(o, kind) for o in objs]
        self.n = len(self.pos)
        self.blend_in = float(coll.get("nis_blend_in", N.DEFAULT_BLEND))
        self.curve = P.PathCurve(C.anim_from_collection(coll))
        self.segs = max(self.curve.segments, 1)
        self.length = self.curve.length

    def at_dist(self, metres: float) -> tuple:
        t = self.curve.find_param(min(max(float(metres), 0.0), self.length))
        s = t * self.segs
        i = min(int(s), self.segs - 1)
        return tuple(self.curve.point(t)), i, min(max(s - i, 0.0), 1.0)

    def fallback_samples(self, start_m: float, scale: float = 1.0, blend_in: float | None = None) -> list:
        """``[[seconds, metres]]`` for an animation played from ``start_m`` with no timing from the timeline: the game's own stepping
        (``dist += dt * speed``) after the blend in."""
        blend = self.blend_in if blend_in is None else blend_in
        out = [[0.0, float(start_m)]]
        d, tau, sub = float(start_m), 0.0, 1.0 / 32.0
        step = 0
        while d < self.length and step < 400000:
            d += sub * max(self.curve.speed_at_dist(d) * scale, 0.1)
            tau += sub
            step += 1
            if step % 4 == 0:
                out.append([blend + tau, min(d, self.length)])
        out.append([blend + tau, self.length])
        return out


def _samples_of(d: dict, path: PathTimes, scale: float = 1.0) -> tuple:
    """``(samples, exact)``: the timeline's ``[[seconds since the event, metres]]`` of an animation, or the game's stepping worked out here."""
    smp = [[float(a), float(b)] for a, b in (d.get("samples") or [])]
    if smp:
        return smp, True
    return path.fallback_samples(float(d.get("start_m", 0) or 0), scale, d.get("blend_in_s")), False


def _thin(samples: list) -> list:
    """The samples with every run of an unchanged distance reduced to its first and last (a paused or held animation has hundreds)."""
    out = []
    for k, (t, d) in enumerate(samples):
        same_before = out and abs(out[-1][1] - d) < 1e-6
        same_after = k + 1 < len(samples) and abs(samples[k + 1][1] - d) < 1e-6
        if same_before and same_after and len(out) > 1 and abs(out[-2][1] - d) < 1e-6:
            out[-1] = [t, d]
        else:
            out.append([t, d])
    return out


def _densify(samples: list, per: int = 2) -> list:
    """Each gap between two samples split in ``per`` (the distance linear between them) so a curved path is not drawn by long chords."""
    out = []
    for (t0, d0), (t1, d1) in zip(samples, samples[1:]):
        for k in range(per):
            u = k / per
            out.append((t0 + (t1 - t0) * u, d0 + (d1 - d0) * u))
    if samples:
        out.append(tuple(samples[-1]))
    return out


class _Run:
    """One camera animation as the replay camera plays it: the path and a way to read the camera's pose at a distance."""

    def __init__(self, b, coll):
        self.coll = coll
        self.kind = coll.get("nis_kind", "camera")
        if len(C.key_objects(coll)) < 2:
            raise _Skip(f"camera animation {coll.get('nis_name', coll.name)} has fewer than 2 key frames")
        self.path = PathTimes(coll, self.kind)
        self.b = b

    def pose(self, metres: float, tg: float):
        """``(location in Blender's axes, orientation quaternion)`` ``metres`` along the path (global time ``tg``, for a key that looks at a moving object)."""
        p, i, u = self.path.at_dist(metres)
        n = self.path.n
        targets = self.b.targets_at(tg)
        a = C._shown_basis(self.kind, i, n, self.path.pos, self.path.keys[i], targets)
        c = C._shown_basis(self.kind, min(i + 1, n - 1), n, self.path.pos, self.path.keys[min(i + 1, n - 1)], targets)
        return g2b(p), a.to_quaternion().slerp(c.to_quaternion(), u)


class _Cam:
    """The replay camera's keys: ``[t, location, quaternion, interpolation]`` in time order."""

    def __init__(self):
        self.rows: list = []

    def pose_now(self, t: float):
        r = self.rows
        if not r:
            return None
        if t <= r[0][0]:
            return r[0][1], r[0][2]
        for a, b in zip(r, r[1:]):
            if a[0] <= t < b[0]:
                if a[3] == LINEAR and b[0] > a[0]:
                    u = (t - a[0]) / (b[0] - a[0])
                    return a[1].lerp(b[1], u), a[2].slerp(b[2], u)
                return a[1], a[2]
        return r[-1][1], r[-1][2]

    def key(self, t: float, loc: Vector, q: Quaternion, interp: str) -> None:
        q = q.copy()
        ref = self.rows[-2] if len(self.rows) > 1 and self.rows[-1][0] >= t - 1e-9 else (self.rows[-1] if self.rows else None)
        if ref is not None and ref[2].dot(q) < 0:
            q.negate()
        if self.rows and self.rows[-1][0] >= t - 1e-9:
            self.rows[-1][:] = [self.rows[-1][0], loc.copy(), q, interp]
        else:
            self.rows.append([t, loc.copy(), q, interp])

    def truncate(self, t: float) -> None:
        if self.rows and self.rows[-1][0] > t:
            p = self.pose_now(t)
            self.rows[:] = [r for r in self.rows if r[0] < t]
            if p is not None:
                self.key(t, p[0], p[1], LINEAR)

    def hold(self) -> None:
        if self.rows:
            self.rows[-1][3] = CONSTANT

    def last_change(self) -> float:
        """The time of the last key whose picture differs from the one before it (a camera held at the end changes nothing after that)."""
        r = self.rows
        for k in range(len(r) - 1, 0, -1):
            if (r[k][1] - r[k - 1][1]).length > 1e-4 or r[k][2].rotation_difference(r[k - 1][2]).angle > 1e-4:
                return r[k][0]
        return r[0][0] if r else 0.0


# ---------------------------------------------------------------------------------------------------- what a build made
_DATA_KINDS = ("actions", "meshes", "materials", "images", "cameras", "armatures")


def _snapshot() -> dict:
    return {k: {x.name for x in getattr(bpy.data, k)} for k in _DATA_KINDS}


def _made_since(before: dict) -> dict:
    """``{kind: [names]}`` of the data blocks made since ``before`` (the NIS builder leaves some Actions with no user)."""
    return {k: sorted(x.name for x in getattr(bpy.data, k) if x.name not in before[k]) for k in _DATA_KINDS}


# ---------------------------------------------------------------------------------------------------- the builder
class _Builder:
    """Builds one replay. ``apply_events`` reads the timeline into tracks, camera operations and rows (no Blender objects yet);
    the ``make_*`` steps then write the objects and keys."""

    def __init__(self, context, top, tl, title: str, key: str, game_dir=None):
        self.context, self.top, self.tl, self.title, self.key = context, top, tl, title, key
        self.game_dir = game_dir
        self.scene = context.scene
        self.placed = placed_objects(top)
        self.base_pos = {eid: b2g(o.matrix_world.translation) for eid, o in self.placed.items()}
        self.labels = {eid: o.name for eid, o in self.placed.items()}
        self.tracks: dict = {}
        self.created: dict = {}                    # entity id -> blueprint reference
        self.rows: list = []
        self.cam_ops: list = []                    # (t, event) of the camera events
        self.nis_ops: list = []                    # (t, action, entity id, data)
        self.marks: list = []                      # (t, name, kind, entity id)
        self.fades: list = []                      # (t, from, to, duration)
        self.notes: list = []
        self.clip_rows: list = []
        self.clip_jobs: list = []
        self.data = None
        self.clip_cache: dict = {}
        self.clip_seconds = 0.0
        self.cur_row = None
        self.hide_extra: set = set()
        self.camera = None
        self.camera_end = 0.0
        self.nis_cams: dict = {}
        self.marker_names: list = []
        self.standins: dict = {}
        self._holder = None                        # the hidden collection of models loaded for the replay (_holder_collection)
        self._models: dict = {}                    # model reference -> (collection, reason it has none)
        t_ground = X.terrain_of(top)
        self.ground = Ground(t_ground)
        root = C.root_of(top)
        self.anims = {(c.get("nis_kind"), int(c.get("nis_id", 0))): c for c in (C.anim_collections(root) if root is not None else [])}
        self.cam = _Cam()
        self.counts = {"keys": 0}
        self.duration = float(_g(tl, "duration_s", 0) or 0)

    # ---- tracks
    def track(self, eid: int):
        tr = self.tracks.get(eid)
        if tr is not None:
            return tr
        o = self.placed.get(eid)
        if o is None:
            return None
        loc, rot, _s = o.matrix_world.decompose()
        pos = b2g(loc)
        gy = self.ground.height(pos[0], pos[2])
        dy = (pos[1] - gy) if gy is not None else 0.0
        h0 = base_heading(rot.to_matrix())
        tr = Track(eid, o.name, pos, h0, dy)
        self.tracks[eid] = tr
        return tr

    def pos_of(self, eid: int, t: float):
        tr = self.tracks.get(eid)
        return tr.pos_at(t) if tr is not None else self.base_pos.get(eid)

    def targets_at(self, t: float) -> dict:
        out = dict(self.base_pos)
        for eid, tr in self.tracks.items():
            out[eid] = tr.pos_at(t)
        return out

    def ground_y(self, tr: Track, x: float, z: float, fallback: float) -> float:
        h = self.ground.height(x, z)
        return h + tr.dy if h is not None else fallback

    # ---- the events
    def apply_events(self) -> None:
        for eid, st in (_g(self.tl, "initial", {}) or {}).items():          # an object whose first event is a respawn is not there at the start
            if not st.get("present", True):
                tr = self.track(int(eid))
                if tr is not None:
                    tr.vis[0][1] = False
        events = sorted(enumerate(_g(self.tl, "events", []) or []), key=lambda p: (float(_g(p[1], "t", 0) or 0), p[0]))
        for _i, ev in events:
            t = float(_g(ev, "t", 0) or 0)
            kind = str(_g(ev, "kind", ""))
            for e in _g(ev, "entities", []) or []:
                self.labels.setdefault(int(e), f"object {int(e)}")
            row = Row(t, kind, describe(ev, self.labels), str(_g(ev, "quality", "exact")), str(_g(ev, "why", "") or ""))
            self.rows.append(row)
            self.cur_row = row
            if row.quality == "skipped":
                continue
            handler = getattr(self, f"ev_{kind}", None)
            try:
                if handler is None:
                    raise _Skip(f"the replay does not draw '{kind}' events")
                row.shown = handler(t, [int(e) for e in (_g(ev, "entities", []) or [])], dict(_g(ev, "data", {}) or {}), ev) or ""
            except _Skip as exc:
                row.quality, row.why = "skipped", "; ".join(x for x in (row.why, str(exc)) if x)

    @staticmethod
    def _each(value, i: int, default=None):
        """The ``i``-th of a per-entity list, or the value itself when it is a single one."""
        if isinstance(value, (list, tuple)):
            return value[i] if i < len(value) else default
        return default if value is None else value

    @staticmethod
    def _deg(h) -> float:
        """The game's heading (radians; the unit looks along (sin h, cos h) in x, z: ``cine_timeline.heading_of``) as the replay's heading."""
        return -math.degrees(float(h))

    def _pos3(self, tr: Track, t: float, p) -> tuple:
        p = list(p)
        if len(p) == 2:
            x, z = p
            return (float(x), self.ground_y(tr, float(x), float(z), tr.pos_at(t)[1]), float(z))
        return (float(p[0]), float(p[1]), float(p[2]))

    def _pairs(self, eids, why_none: str) -> list:
        """``[(index in the event's entity list, track)]`` of the entities the map has."""
        got = [(i, self.track(e)) for i, e in enumerate(eids)]
        have = [(i, t) for i, t in got if t is not None]
        if not have:
            missing = ", ".join(str(e) for e in eids[:4]) or "none named"
            raise _Skip(f"{why_none} (the map has no object {missing}; another script may create it)")
        return have

    def ev_teleport(self, t, eids, d, ev):
        pos = d.get("pos")
        if pos is None:
            raise _Skip("the event gives no place")
        for i, tr in self._pairs(eids, "nobody to move"):
            p = pos[i] if pos and isinstance(pos[0], (list, tuple)) and i < len(pos) else pos
            tr.set_loc(t, self._pos3(tr, t, p))
            if not d.get("keep_heading", False) and d.get("heading") is not None:
                tr.set_rot(t, self._deg(self._each(d["heading"], i, 0.0)))
        return ""

    def ev_walk(self, t, eids, d, ev):
        pairs = self._pairs(eids, "nobody to walk")
        speed = float(d.get("speed", 0) or 0)
        t_end = d.get("t_end")
        for _i, tr in pairs:
            if d.get("stop"):
                tr.cut(t)
                continue
            path = [(float(p[0]), float(p[-1])) for p in (d.get("path") or [])]
            if not path:
                raise _Skip("the walk has no route")
            cur = tr.pos_at(t)
            if math.dist((cur[0], cur[2]), path[0]) > 0.5:
                path.insert(0, (cur[0], cur[2]))
            length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
            if length < 1e-6:
                tr.cut(t)
                continue
            dur = (float(t_end) - t) if (t_end is not None and float(t_end) > t) else (length / speed if speed > 0 else None)
            if dur is None or dur <= 0:
                raise _Skip("the walk has neither a speed nor an end time")
            pts = resample(path, WALK_STEP, WALK_KEYS_MAX)
            tr.cut(t)
            tr.add_loc(t, (path[0][0], self.ground_y(tr, path[0][0], path[0][1], cur[1]), path[0][1]), LINEAR)
            done = 0.0
            prev = pts[0]
            last_h = tr.heading_at(t)
            for x, z, orig in pts[1:]:
                done += math.dist((prev[0], prev[1]), (x, z))
                tk = t + dur * done / length
                dx, dz = x - prev[0], z - prev[1]
                if dx or dz:
                    h = heading_of_dir(dx, dz)
                    if abs(((h - last_h + 180.0) % 360.0) - 180.0) >= TURN_MIN:
                        tr.set_rot(max(tk - dur * math.hypot(dx, dz) / length, t), h)
                        last_h = h
                tr.add_loc(tk, (x, self.ground_y(tr, x, z, cur[1]), z), LINEAR)
                prev = (x, z, orig)
            tr.loc[-1][2] = CONSTANT
        return "" if len(pairs) == len(eids) else "some of the walkers are not in the map"

    def _target_pos(self, tgt, t: float):
        return next((p for p in (self.pos_of(int(e), t) for e in (tgt or [])) if p is not None), None)

    def ev_face(self, t, eids, d, ev):
        tgt = d.get("toward") or d.get("target") or d.get("target_entities")
        for i, tr in self._pairs(eids, "nobody to turn"):
            h = self._each(d.get("heading"), i)
            if h is not None:
                hdeg = self._deg(h)
            elif tgt:
                tp = self._target_pos(tgt, t)
                cur = tr.pos_at(t)
                if tp is None or not (tp[0] - cur[0] or tp[2] - cur[2]):
                    continue
                hdeg = heading_of_dir(tp[0] - cur[0], tp[2] - cur[2])
            else:
                raise _Skip("the event gives no heading and nothing to turn towards")
            ts = float(self._each(d.get("turn_s"), i, 0.0) or 0.0)
            tr.turn(t, hdeg, ts)
        return "turns at the game's rate" if d.get("turn_s") else "the turn is made at once"

    def ev_look_at(self, t, eids, d, ev):
        tgt = d.get("target") or d.get("toward") or []
        limit = float(d.get("turn_if_over_deg", 0.0) or 0.0)
        if not tgt:
            return "it stops looking at anything (the head is not drawn)"
        shown = "the head is not drawn: only the body turns, and only when the target is more than %.0f degrees away" % limit if limit else ""
        for _i, tr in self._pairs(eids, "nobody to look"):
            tp = self._target_pos(tgt, t)
            cur = tr.pos_at(t)
            if tp is None or not (tp[0] - cur[0] or tp[2] - cur[2]):
                continue
            hdeg = heading_of_dir(tp[0] - cur[0], tp[2] - cur[2])
            delta = abs(((hdeg - tr.heading_at(t) + 180.0) % 360.0) - 180.0)
            if delta > limit:
                tr.turn(t, hdeg, delta / 180.0)
        return shown

    def ev_despawn(self, t, eids, d, ev):
        for _i, tr in self._pairs(eids, "nobody to remove"):
            tr.set_vis(t, False)
        return ""

    ev_delete = ev_despawn

    def ev_respawn(self, t, eids, d, ev):
        pos = d.get("pos")
        placed_where = False
        for i, tr in self._pairs(eids, "nobody to bring back"):
            p = pos[i] if pos and isinstance(pos[0], (list, tuple)) and i < len(pos) else pos
            if p is not None:
                tr.set_loc(t, self._pos3(tr, t, p))
                placed_where = True
            tr.set_vis(t, True)
            if d.get("heading") is not None:
                tr.set_rot(t, self._deg(self._each(d["heading"], i, 0.0)))
        return "" if placed_where else "it comes back where it stood when the replay began"

    def ev_visible(self, t, eids, d, ev):
        for _i, tr in self._pairs(eids, "nothing to show"):
            tr.set_vis(t, bool(d.get("on", True)))
        return ""

    def ev_create(self, t, eids, d, ev):
        eid = int(eids[0]) if eids else int(d.get("new_id", d.get("id", -1)))
        if eid < 0:
            raise _Skip("the new object has no number")
        pos = d.get("pos")
        if pos is None:
            raise _Skip("the new object has no place")
        p = list(pos)
        if len(p) == 2:
            gy = self.ground.height(float(p[0]), float(p[1])) or 0.0
            p = [p[0], gy, p[1]]
        ref = _clean_ref(d.get("blueprint", ""))
        tr = Track(eid, ref.rsplit("/", 1)[-1].rsplit(".", 1)[0] or f"new {eid}", p, self._deg(d.get("heading", 0.0) or 0.0), 0.0, created=True, h0=0.0)
        self.tracks[eid] = tr
        self.labels[eid] = tr.label
        self.created[eid] = ref
        tr.set_vis(t, True)
        return ""

    def ev_motion_var(self, t, eids, d, ev):
        name = str(d.get("name", "")).strip()
        try:
            value = float(d.get("value"))
        except (TypeError, ValueError):
            raise _Skip("the value is not a number")
        if not name:
            raise _Skip("the variable has no name")
        for _i, tr in self._pairs(eids, "nobody has the variable"):
            tr.set_prop(t, "mv_" + name, value)
        return "kept as the keyed property mv_" + name + " (the replay does not run the motion tree)"

    def ev_anim(self, t, eids, d, ev):
        name = str(d.get("name", "") or "").strip()
        if not name:
            raise _Skip("the animation's name is not known (its text is not in the map)")
        for _i, tr in self._pairs(eids, "nobody to animate"):
            tr.anims.append([t, name, d.get("loop"), self.cur_row])
        return "only the clip number is keyed (property replay_anim): the model's clip plays when the map has its model loaded"

    def _path_heading(self, kind, i, n, pos, keys, u):
        a = C._shown_basis(kind, i, n, pos, keys[i], {})
        b = C._shown_basis(kind, min(i + 1, n - 1), n, pos, keys[min(i + 1, n - 1)], {})
        f = -((a.to_quaternion().slerp(b.to_quaternion(), u)).to_matrix() @ Vector((0.0, 0.0, 1.0)))
        gx, _gy, gz = b2g(f)
        return heading_of_dir(gx, gz) if abs(gx) + abs(gz) > 1e-6 else None

    def _path_events(self, kind: str, t, eids, d):
        ident = d.get("anim", d.get("anim_id", d.get("id")))
        coll = self.anims.get((kind, int(ident))) if ident is not None else None
        if coll is None:
            raise _Skip(f"the map has no {C.LABEL[kind].lower()} animation {ident}")
        if len(C.key_objects(coll)) < 2:
            raise _Skip("the animation has fewer than 2 key frames")
        path = PathTimes(coll, kind)
        samples, exact = _samples_of(d, path, 1.0 / 3.6 if kind == "obj" else 1.0)
        samples = _thin(samples)
        blend = float(d.get("blend_in_s", path.blend_in) or 0.0) if kind == "move" else 0.0
        offsets = d.get("offsets") or []
        n = path.n
        for i, tr in self._pairs(eids, "nobody to move"):
            ox, oz = (offsets[i][0], offsets[i][1]) if i < len(offsets) else (0.0, 0.0)
            tr.cut(t)
            p0, _s0, _u0 = path.at_dist(samples[0][1])
            first = (p0[0] + ox, p0[1], p0[2] + oz)
            if blend > 0:
                tr.add_loc(t, tr.pos_at(t), LINEAR)
                tr.add_loc(t + blend, first, LINEAR)
            else:
                tr.add_loc(t, first, LINEAR)
            last_h = None
            for s_rel, dist in _densify(samples, 2):
                if s_rel < blend - 1e-9:
                    continue
                p, si, u = path.at_dist(dist)
                tr.add_loc(t + s_rel, (p[0] + ox, p[1], p[2] + oz), LINEAR)
                h = self._path_heading(kind, si, n, path.pos, path.keys, u)
                if h is not None and (last_h is None or abs(((h - last_h + 180.0) % 360.0) - 180.0) >= 1.5):
                    tr.set_rot(t + s_rel, h)
                    last_h = h
            tr.loc[-1][2] = CONSTANT
        return samples[-1][0], exact

    def ev_move_anim(self, t, eids, d, ev):
        if d.get("action") in ("pause", "resume"):
            return "the path stops / goes on: its distance in the timeline's samples already holds still"
        total, exact = self._path_events("move", t, eids, d)
        return f"follows the animation's path for {total:.1f} s" + ("" if exact else " (the game's stepping worked out here: the timeline gave no distances)")

    def ev_obj_anim(self, t, eids, d, ev):
        total, exact = self._path_events("obj", t, eids, d)
        return f"follows the animation's path for {total:.1f} s, keeping each one's place in the group" + (
            "" if exact else " (the game's stepping worked out here: the timeline gave no distances)")

    def ev_camera(self, t, eids, d, ev):
        action = str(d.get("action", "play"))
        ident = d.get("anim", d.get("anim_id", d.get("id")))
        mark = (t, f"camera {action}" + (f" {int(ident)}" if ident is not None else ""), "camera", None)
        if action in ("play", "cut"):
            coll = self.anims.get(("camera", int(ident))) if ident is not None else None
            if coll is None:
                raise _Skip(f"the map has no camera animation {ident}")
            if len(C.key_objects(coll)) < 2:
                raise _Skip(f"camera animation {ident} has fewer than 2 key frames, so it cannot play")
            self.marks.append(mark)
            self.cam_ops.append((t, ev))
            return ""
        if action in ("pause", "resume", "stop", "hold", "path_end", "end"):
            self.marks.append(mark)
            return "the camera's distance along its path is in the timeline's samples, so this only marks the time"
        raise _Skip(f"the camera action '{action}' is not known to the replay")

    def ev_nis(self, t, eids, d, ev):
        for e in eids:
            act = str(d.get("action", "start"))
            self.nis_ops.append((t, act, int(e), d, self.cur_row))
            self.marks.append((t, f"NIS {act} {int(e)}", "nis_camera_use" if act == "start" else "nis_camera_done", int(e)))
        if not eids:
            raise _Skip("the event names no NIS object")
        return ""

    def ev_anim_camera(self, t, eids, d, ev):
        action = str(d.get("action", "use"))
        for e in eids or [-1]:
            self.marks.append((t, f"NIS camera {action}", "nis_camera_use" if action == "use" else "nis_camera_done", e))
        return ""

    def ev_fade(self, t, eids, d, ev):
        self.fades.append((t, float(d.get("from", 0) or 0), float(d.get("to", 0) or 0), float(d.get("duration_s", 0) or 0)))
        self.marks.append((t, "fade", "plain", None))
        return "a black plane in front of the replay camera (partial fades show in Material Preview)"

    def ev_cinematic_mode(self, t, eids, d, ev):
        self.marks.append((t, "cinematic mode " + ("on" if d.get("on", True) else "off"), "plain", None))
        raise _Skip("not drawn: use the Cinematic 1.85 picture button for the film shape")

    def ev_dialog(self, t, eids, d, ev):
        self.marks.append((t, f"dialog {d.get('name') or d.get('id', '?')}", "plain", None))
        return "a marker only (no sound or text is played)"

    def ev_focus(self, t, eids, d, ev):
        self.marks.append((t, "focus", "plain", None))
        raise _Skip("not drawn: the game's own view is not part of the replay")

    def ev_decal(self, t, eids, d, ev):
        self.marks.append((t, "decal", "plain", None))
        raise _Skip("not drawn: decals of the script are not part of the replay")

    # ---------------------------------------------------------------------------------------------- making the objects
    def model_for(self, ref: str):
        """``(collection holding the model of blueprint ``ref``, reason)``: the map's own copy when it has one, else loaded for the replay."""
        hidden = X.hidden_collection(self.top)
        model_ref = ref.rsplit(".", 1)[0] + ".sgm"
        if hidden is not None:
            for c in hidden.children:
                if c.get(M.KEY_MODEL) == model_ref:
                    return c, ""
        holder = self._holder_collection()
        if model_ref in self._models:
            return self._models[model_ref]
        view = M._view()
        archive = self.top.get(M.KEY_ARCHIVE, "")
        data = view.game_data(self.game_dir, archive) if self.game_dir else None
        stub = view.PlacedEntity(instance_id=-1, blueprint_ref=-1, blueprint="DATA:" + ref.upper().replace("/", "\\"),
                                 name=model_ref.rsplit("/", 1)[-1][:-4], kind=view.entity_kind(ref), position=(0.0, 0.0, 0.0))
        info = view._classify(stub, data)
        if info.status != view.MODEL:
            res = (None, info.reason)
        else:
            import shutil
            import tempfile
            layer = M._layer_collection(self.context.view_layer.layer_collection, holder)
            if layer is not None:
                layer.exclude = False
            coll = M._new_collection(f"model {model_ref.rsplit('/', 1)[-1][:-4]}", holder)
            info.model_ref = model_ref
            tmp = tempfile.mkdtemp(prefix="ic_replay_")
            try:
                M.load_model_collection(self.context, coll, info, data, self.game_dir, tmp)
                res = (coll, "")
            except Exception as exc:                           # noqa: BLE001
                res = (None, f"the model {model_ref} could not be imported: {exc}")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
                if layer is not None:
                    layer.exclude = True
        self._models[model_ref] = res
        return res

    def _holder_collection(self):
        """The hidden collection that holds the models loaded for the replay (made the first time it is asked for)."""
        if self._holder is None:
            self._holder = M._new_collection(f"{X.map_name(self.top)} replay models (hidden)", self.coll)
        return self._holder

    def make_standins(self) -> None:
        for eid, tr in self.tracks.items():
            orig = self.placed.get(eid)
            o = bpy.data.objects.new(f"{tr.label} replay", None)
            o[K_PART] = "standin"
            o[K_ENTITY] = int(eid)
            if orig is not None:
                _loc, rot, scl = orig.matrix_world.decompose()
                base3 = rot.to_matrix()
                if orig.instance_type == "COLLECTION" and orig.instance_collection is not None:
                    o.instance_type, o.instance_collection = "COLLECTION", orig.instance_collection
                    o.empty_display_size = orig.empty_display_size
                else:
                    o.empty_display_type, o.empty_display_size = orig.empty_display_type, orig.empty_display_size
                    if "ic_not_shown" in orig:
                        o["ic_not_shown"] = orig["ic_not_shown"]
            else:
                base3, scl = Matrix.Identity(3), Vector((1.0, 1.0, 1.0))
                coll, why = self.model_for(self.created.get(eid, "")) if self.created.get(eid) else (None, "the blueprint is not known")
                if coll is not None:
                    o.instance_type, o.instance_collection, o.empty_display_size = "COLLECTION", coll, 0.5
                else:
                    o.empty_display_type, o.empty_display_size = "SPHERE", 1.5
                    o["ic_not_shown"] = why
                    self.notes.append(f"{tr.label}: the model is not shown ({why}); an empty stands in for it.")
            self.coll.objects.link(o)
            o.rotation_mode = "QUATERNION"
            o.scale = scl
            self.standins[eid] = o
            # location
            rows = [(frame_of(t), g2b(p), interp) for t, p, interp in tr.loc]
            o.location = rows[0][1]
            if len(rows) > 1:
                self.counts["keys"] += key_location(o, rows)
            # rotation
            qrows = []
            prev = None
            hrows = []                                                 # (t, heading, interpolation): a long linear turn gets a key every 45 degrees
            for k, (t, h, interp) in enumerate(tr.rot):
                hrows.append((t, h, interp))
                if interp == LINEAR and k + 1 < len(tr.rot):
                    t2, h2 = tr.rot[k + 1][0], tr.rot[k + 1][1]
                    for m in range(1, int(abs(h2 - h) // 45.0) + 1):
                        u = m * 45.0 / abs(h2 - h)
                        if u < 1.0:
                            hrows.append((t + (t2 - t) * u, h + (h2 - h) * u, LINEAR))
            for t, h, interp in hrows:
                q = standin_quaternion(base3, tr.h0, h)
                if prev is not None and prev.dot(q) < 0:
                    q.negate()
                qrows.append((frame_of(t), q, interp))
                prev = q
            o.rotation_quaternion = qrows[0][1]
            if len(qrows) > 1:
                self.counts["keys"] += key_rotation(o, qrows)
            # visibility
            self._key_vis(o, tr)
            for name, vals in tr.props.items():
                o[name] = vals[0][1]
                self.counts["keys"] += key_custom(o, name, [(frame_of(t), v) for t, v in vals])
            if tr.anims:
                names = []
                for _t, nm, _lp, _row in tr.anims:
                    if nm not in names:
                        names.append(nm)
                o["replay_anim_names"] = names
                o["replay_anim"] = 0
                self.counts["keys"] += key_custom(o, "replay_anim", [(FRAME0, 0)] + [(frame_of(t), names.index(nm) + 1) for t, nm, _lp, _row in tr.anims])
                self.clip_jobs.append((tr, o, base3))

    def _key_vis(self, part, tr: Track) -> None:
        """Key an object's visibility from the track's (a stand-in's parts all follow it)."""
        if len(tr.vis) > 1 or not tr.vis[0][1]:
            rows = [(frame_of(t), not v) for t, v in tr.vis]            # keyed as "hidden"
            part.hide_viewport = part.hide_render = rows[0][1]
            self.counts["keys"] += key_bool(part, "hide_viewport", rows) + key_bool(part, "hide_render", rows)

    def make_clips(self) -> None:
        """Play the model's own clips on the stand-ins that have ``anim`` events (a copy of the model with the clips the events name, baked
        and laid on the armature's NLA at the events' times); a stand-in whose model has no such clip keeps only the keyed clip number."""
        t0 = time.perf_counter()
        built = 0
        for tr, o, base3 in self.clip_jobs:
            if built >= MAX_CLIP_STANDINS:
                self.notes.append(f"{tr.label}: its clips are not played (only the first {MAX_CLIP_STANDINS} animated objects get their model's clips, "
                                  "to keep the replay quick to build); the clip number is keyed on replay_anim.")
                continue
            try:
                if self._play_clips(tr, o):
                    built += 1
            except Exception as exc:                          # noqa: BLE001 -- the clip number stays keyed
                self.notes.append(f"{tr.label}: its clips could not be played ({exc}).")
        self.clip_seconds = time.perf_counter() - t0
        self.counts["clip_standins"] = built

    def _clip_model(self, model_ref: str):
        """``(SceneData, path)`` of the model, decoded once."""
        hit = self.clip_cache.get(model_ref)
        if hit is not None:
            return hit
        import os
        import tempfile
        view = M._view()
        if self.data is None and self.game_dir:
            self.data = view.game_data(self.game_dir, self.top.get(M.KEY_ARCHIVE, ""))
        raw = self.data.read(model_ref) if self.data is not None else None
        if raw is None:
            raise RuntimeError(f"the model {model_ref} could not be read")
        self._clip_tmp = getattr(self, "_clip_tmp", None) or tempfile.mkdtemp(prefix="ic_replay_clips_")
        path = os.path.join(self._clip_tmp, *model_ref.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
        core = M._addon("core")
        sd = core.load_scene(path, data_root=self.game_dir, texture_source=self.data)
        self.clip_cache[model_ref] = (sd, path)
        return sd, path

    def _entity_ebp(self, tr: Track):
        """The blueprint of the entity (``ICParse.ebp``), or None."""
        orig = self.placed.get(tr.eid)
        ref = _clean_ref(orig.get("ic_blueprint", "")) if orig is not None else self.created.get(tr.eid, "")
        if not ref or self._game_data() is None:
            return None
        try:
            from ICParse.ebp import parser as ebp_parser
            raw = self._game_data().read(ref)
            return ebp_parser.read(raw) if raw else None
        except Exception:                                      # noqa: BLE001
            return None

    @staticmethod
    def _resolve_clip(ebp, name: str) -> tuple:
        """``(clip name, loops, how)``. The text of a PlayAnimation is a MOTION NODE of the blueprint (the lab's ``PowerDown`` is a node whose
        children are its idle pose and the clip ``PowerDown``; ``Fly`` plays the clip ``Flying``): the clip is the node's own, else the last
        clip below it that is not an idle (the motion tree layers an idle under the pose; the conditions that pick among children are not
        run). With no such node the text is taken as the clip's own name."""
        if ebp is None:
            return name, None, ""
        nodes = ebp.motion_nodes
        index = {n.state.lower(): i for i, n in enumerate(nodes)}
        i = index.get(name.lower())
        if i is None:
            return name, None, ""
        leaves, seen = [], set()

        def walk(k: int, depth: int = 0) -> None:
            if k in seen or depth > 16:
                return
            seen.add(k)
            n = nodes[k]
            if n.anim:
                leaves.append(n)
            for c in n.children:
                if 0 <= c < len(nodes):
                    walk(c, depth + 1)
        walk(i)
        if not leaves:
            return name, None, ""
        pick = ([n for n in leaves if not n.anim.lower().startswith("idle")] or leaves)[-1]
        how = f"the blueprint's motion node {nodes[i].state}" + ("" if len(leaves) == 1 else f", {len(leaves)} clips below it; the last that is not an idle")
        return pick.anim, bool(pick._c.flags & 1), how

    def _play_clips(self, tr: Track, o) -> bool:
        coll = o.instance_collection
        model_ref = coll.get(M.KEY_MODEL) if coll is not None else None
        if not model_ref:
            if coll is not None:
                why = "it is a creature, which the map shows as a still mesh (the add-on does not rig the map's creatures)"
            else:
                why = "its model is not loaded (the map was opened without models, or it has none of its own)"
            self.notes.append(f"{tr.label}: {why}, so its clips are not played; the clip number is keyed on replay_anim.")
            return False
        sd, path = self._clip_model(str(model_ref))
        by_name = {(a.name or "").lower(): i for i, a in enumerate(sd.animations)}
        if not sd.bone_names or not by_name:
            self.notes.append(f"{tr.label}: its model has no clips; the clip number is keyed on replay_anim.")
            return False
        ebp = self._entity_ebp(tr)
        resolved = {}                                          # event text -> (clip name, loops, how it was found)
        for _t, nm, _lp, _row in tr.anims:
            if nm not in resolved:
                resolved[nm] = self._resolve_clip(ebp, nm)
        need = []
        for _t, nm, _lp, _row in tr.anims:
            idx = by_name.get(resolved[nm][0].lower())
            if idx is not None and idx not in need:
                need.append(idx)
        missing = sorted({nm for _t, nm, _lp, _row in tr.anims if resolved[nm][0].lower() not in by_name})
        if missing:
            self.notes.append(f"{tr.label}: its model has no clip named {', '.join(missing)}; the clip number is keyed on replay_anim.")
        if not need:
            return False
        core, scene_mod, acts = M._addon("core"), M._addon("scene"), M._addon("actions")
        holder = self._holder_collection()
        layer = M._layer_collection(self.context.view_layer.layer_collection, holder)
        if layer is not None:
            layer.exclude = False
        sub = M._new_collection(f"clips {tr.label}", holder)
        try:
            scene_mod.build_scene(M._Into(self.context, sub), sd, source_path=path, clips_as_actions=False)
        finally:
            if layer is not None:
                layer.exclude = True
        arm = next((x for x in sub.all_objects if x.type == "ARMATURE"), None)
        if arm is None:
            for x in list(sub.all_objects):
                bpy.data.objects.remove(x, do_unlink=True)
            bpy.data.collections.remove(sub)
            self.notes.append(f"{tr.label}: its model has no skeleton; the clip number is keyed on replay_anim.")
            return False
        clips = [(sd.animations[i].name, getattr(sd.animations[i], "frame_count", 0) or 1, getattr(sd.animations[i], "fps", 30.0)) for i in need]
        made = acts.import_clips(arm, sd.bone_names, sd.bone_parents, sd.bind_world, clips, lambda ci, t: core.scene_bone_world(sd, need[ci], t))
        ad = arm.animation_data
        for track in list(ad.nla_tracks):                     # import_clips stashes each clip on a muted track; the replay lays its own
            ad.nla_tracks.remove(track)
        ad.action = None
        for a in made:
            a["ic_replay"] = True
            a.use_fake_user = False
        by_idx = {i: (made[k], clips[k][1], float(clips[k][2] or 30.0)) for k, i in enumerate(need)}
        # the model's objects become children of the stand-in
        roots = [x for x in sub.all_objects if x.parent is None]
        parts = list(sub.all_objects)
        for x in list(sub.all_objects):
            for c in list(x.users_collection):
                c.objects.unlink(x)
            self.coll.objects.link(x)
            x[K_PART] = "standin_part"
        for x in roots:
            x.parent = o
        bpy.data.collections.remove(sub)
        o.instance_type, o.instance_collection = "NONE", None
        o.empty_display_type = "PLAIN_AXES"
        for x in parts:
            self._key_vis(x, tr)
        # lay the clips on the NLA, one track a strip so that none overlaps another
        anims = sorted(tr.anims, key=lambda a: a[0])
        end_t = self.end_time()
        for k, (t, nm, loop, row) in enumerate(anims):
            clip_name, node_loop, how = resolved[nm]
            if loop is None:
                loop = node_loop
            hit = by_idx.get(by_name.get(clip_name.lower(), -1))
            if hit is None:
                continue
            if row is not None:
                row.shown = f"plays the clip {clip_name}" + (f" ({how})" if how else "") + (" in a loop" if loop else "")
            act, frames, fps = hit
            nxt = anims[k + 1][0] if k + 1 < len(anims) else max(end_t, t + 1.0)
            track = ad.nla_tracks.new()
            track.name = f"{nm} {t:.1f}"
            strip = track.strips.new(f"{nm}", int(frame_of(t)), act)
            try:
                if hasattr(strip, "action_slot") and len(act.slots):
                    strip.action_slot = act.slots[0]
            except Exception:                                  # noqa: BLE001
                pass
            strip.scale = FPS / fps
            if loop:
                length = max(strip.frame_end - strip.frame_start, 1.0)
                strip.repeat = max(1.0, round(((nxt - t) * FPS) / length + 0.4999, 2))
            strip.extrapolation = "HOLD_FORWARD"
            self.counts["clips"] = self.counts.get("clips", 0) + 1
        return True

    def make_nis(self) -> None:
        if not self.nis_ops:
            return
        mod = _nis_builder()
        opens: dict = {}
        spans: list = []                                       # (entity id, t0, t1 or None, row)
        for t, action, eid, d, row in self.nis_ops:
            if action == "start":
                if eid in opens:
                    t0, r0 = opens.pop(eid)
                    spans.append((eid, t0, t, r0))
                opens[eid] = (t, row)
            elif action == "end" and eid in opens:
                t0, r0 = opens.pop(eid)
                spans.append((eid, t0, t, r0))
        end_all = max(self.duration, max((op[0] for op in self.nis_ops), default=0.0))
        for eid, (t0, r0) in opens.items():
            spans.append((eid, t0, end_all, r0))
        for eid, t0, t1, row in sorted(spans, key=lambda s: s[1]):
            if mod is None:
                row.quality, row.why = "skipped", "the NIS builder (map_nis) is not installed"
                continue
            orig = self.placed.get(eid)
            if orig is None:
                row.quality, row.why = "skipped", f"the map has no NIS object {eid}"
                continue
            try:
                nb = mod.build_nis(self.context, None, orig, start_frame=frame_of(t0), game_dir=self.game_dir, data=self._game_data())
            except Exception as exc:                          # noqa: BLE001
                row.quality, row.why = "skipped", f"the NIS object could not be built: {exc}"
                continue
            objs = [o for o in (_g(nb, "objects", []) or []) if o is not None]
            cam = _g(nb, "camera")
            for n in _g(nb, "notes", []) or []:
                self.notes.append(f"NIS {orig.name}: {n}")
            for ev in _g(nb, "events", []) or []:                # what the scene does inside: listed with the cinematic's own events
                kind = str(ev.get("kind", ""))
                drawn = kind in ("camera_cut", "clip_start")
                bits = [f"{k} {v}" for k, v in ev.items() if k not in ("t", "kind", "quality", "why", "clip")]
                self.rows.append(Row(t0 + float(ev.get("t", 0.0)), "nis_" + kind, f"inside {orig.name}: {kind.replace('_', ' ')} " + " ".join(bits[:3]),
                                     "exact" if drawn else "skipped", str(ev.get("why", "")) if drawn else "not played by the replay", inner=True))
            f0, f1 = frame_of(t0), frame_of(t1)
            c = _g(nb, "collection")
            if c is not None:                                  # the builder made it a child of the scene: it belongs to the replay
                for parent in [x for x in bpy.data.collections if c.name in x.children] + ([self.scene.collection] if c.name in self.scene.collection.children else []):
                    parent.children.unlink(c)
                self.coll.children.link(c)
            for o in objs:
                o[K_PART] = "nis"
            if hasattr(nb, "show_between"):                    # the builder knows which parts its clips show and hide
                nb.show_between(f0, f1, FRAME0)
            else:
                for o in objs:
                    if o.type == "CAMERA":
                        continue
                    rows = [(FRAME0, True)] if f0 > FRAME0 else []
                    rows += [(f0, False), (f1, True)]
                    o.hide_viewport = o.hide_render = f0 > FRAME0
                    key_bool(o, "hide_viewport", rows)
                    key_bool(o, "hide_render", rows)
            if cam is not None:
                self.nis_cams[eid] = cam
            self.hide_extra.add(orig.name)
            self.counts["nis"] = self.counts.get("nis", 0) + 1

    def _game_data(self):
        if self.data is None and self.game_dir:
            self.data = M._view().game_data(self.game_dir, self.top.get(M.KEY_ARCHIVE, ""))
        return self.data

    # ---- the camera
    def make_camera(self) -> None:
        """The replay camera plays every camera animation the timeline starts, one after the other. Where along its path an animation is at each
        moment comes from the timeline (``samples``: seconds since the event, metres along the path), so a pause or a cut needs nothing more."""
        ops = sorted(self.cam_ops, key=lambda op: op[0])
        if not ops:
            return
        cam = self.cam
        for idx, (t, ev) in enumerate(ops):
            d = dict(_g(ev, "data", {}) or {})
            ident = d.get("anim", d.get("anim_id", d.get("id")))
            try:
                run = _Run(self, self.anims[("camera", int(ident))])
            except _Skip:
                continue
            samples, exact = _samples_of(d, run.path)
            samples = _thin(samples)
            if not exact:
                self.notes.append(f"camera animation {int(ident)} at {t:.1f} s: the timeline gave no distances, so the game's own stepping is used from the start.")
            blend_in = float(d.get("blend_in_s", run.path.blend_in) or 0.0)
            t_next = ops[idx + 1][0] if idx + 1 < len(ops) else float("inf")
            loc0, q0 = run.pose(samples[0][1], t + blend_in)
            cur = cam.pose_now(t)
            if cur is None:
                cam.key(0.0, loc0, q0, CONSTANT)
                if t > 0:
                    cam.key(t, loc0, q0, LINEAR)
                blend = 0.0
            else:
                cam.key(t, cur[0], cur[1], LINEAR)
                far = (cur[0] - loc0).length > 1e-3 or cur[1].rotation_difference(q0).angle > 1e-3
                blend = blend_in if far else 0.0
                cam.key(t + blend, loc0, q0, LINEAR)
            for s_rel, dist in _densify(samples, 2):
                tg = t + s_rel
                if s_rel < blend - 1e-9:
                    continue
                if tg >= t_next - 1e-9:
                    break
                loc, q = run.pose(dist, tg)
                cam.key(tg, loc, q, LINEAR)
            cam.hold()
        if not cam.rows:
            return
        data = bpy.data.cameras.new("Replay camera")
        data.clip_end = 5000.0
        o = bpy.data.objects.new("Replay camera", data)
        o[K_PART] = "camera"
        self.coll.objects.link(o)
        M.show_game_picture(o)
        o.rotation_mode = "QUATERNION"
        o.location, o.rotation_quaternion = cam.rows[0][1], cam.rows[0][2]
        self.counts["keys"] += key_location(o, [(frame_of(t), loc, i) for t, loc, _q, i in cam.rows])
        self.counts["keys"] += key_rotation(o, [(frame_of(t), q, i) for t, _l, q, i in cam.rows])
        self.camera = o
        self.camera_end = cam.last_change()

    # ---- the fade
    def make_fade(self) -> None:
        if not self.fades or self.camera is None:
            if self.fades:
                self.notes.append("The fades are not drawn: the cinematic has no camera animation to put them in front of.")
            return
        rows = []
        for t, a, b, dur in sorted(self.fades):
            rows.append([t, a, LINEAR if dur > 0 else CONSTANT])
            if dur > 0:
                rows.append([t + dur, b, CONSTANT])
        me = bpy.data.meshes.new("Replay fade")
        s = 0.6
        me.from_pydata([(-s, -s, -0.4), (s, -s, -0.4), (s, s, -0.4), (-s, s, -0.4)], [], [(0, 1, 2, 3)])
        o = bpy.data.objects.new("Replay fade", me)
        o[K_PART] = "fade"
        self.coll.objects.link(o)
        o.parent = self.camera
        o.hide_select = True
        o.color = (0.0, 0.0, 0.0, rows[0][1])
        mat = bpy.data.materials.new("Replay fade")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out, mix = nt.nodes.new("ShaderNodeOutputMaterial"), nt.nodes.new("ShaderNodeMixShader")
        emit, clear, info = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeBsdfTransparent"), nt.nodes.new("ShaderNodeObjectInfo")
        emit.inputs["Color"].default_value = (0, 0, 0, 1)
        nt.links.new(info.outputs["Alpha"], mix.inputs["Fac"])
        nt.links.new(clear.outputs["BSDF"], mix.inputs[1])
        nt.links.new(emit.outputs["Emission"], mix.inputs[2])
        nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            try:
                setattr(mat, attr, val)
            except (AttributeError, TypeError):
                pass
        me.materials.append(mat)
        by_frame = {}
        for t, a, interp in rows:
            by_frame[frame_of(t)] = (a, interp)
        for f in sorted(by_frame):
            o.color = (0.0, 0.0, 0.0, by_frame[f][0])
            o.keyframe_insert("color", index=3, frame=f)
        for fc in _fcurves_of(o):
            if fc.data_path == "color":
                for kp in fc.keyframe_points:
                    kp.interpolation = by_frame.get(int(round(kp.co[0])), (0, CONSTANT))[1]
        frames = sorted(by_frame)
        vis = []
        for i, f in enumerate(frames):
            nxt = by_frame[frames[i + 1]][0] if i + 1 < len(frames) else 0.0
            vis.append((f, not (by_frame[f][0] > 0 or nxt > 0)))
        o.hide_viewport = o.hide_render = vis[0][1]
        key_bool(o, "hide_viewport", vis)
        key_bool(o, "hide_render", vis)
        self.counts["fades"] = len(self.fades)

    # ---- markers
    def make_markers(self) -> None:
        sc = self.scene
        made = []

        def mark(t, name, cam=None):
            m = sc.timeline_markers.new(("Replay: " + name)[:63], frame=frame_of(t))
            if cam is not None:
                m.camera = cam
            made.append(m.name)
        if self.camera is not None:
            mark(0.0, "start", self.camera)
        for t, name, kind, eid in sorted(self.marks, key=lambda m: m[0]):
            if kind == "camera":
                mark(t, name, self.camera)
            elif kind == "nis_camera_use":
                mark(t, name, self.nis_cams.get(eid))
            elif kind == "nis_camera_done":
                mark(t, name, self.camera)
            else:
                mark(t, name)
        self.marker_names = made

    # ---- finishing
    def hide_originals(self) -> list:
        names = []
        for eid in list(self.tracks) + [e for e in (self.hide_extra or ())]:
            o = self.placed.get(eid) if isinstance(eid, int) else bpy.data.objects.get(eid)
            if o is None or o.name in names:
                continue
            try:
                if not o.hide_get():
                    o.hide_set(True)
                    names.append(o.name)
            except RuntimeError:                              # not in this view layer
                pass
        return names

    def end_time(self) -> float:
        """Where the replay ends: the timeline's length, but at most TAIL seconds after the last thing that happens (a script that waits for the player
        goes on for minutes in the timeline and nothing moves)."""
        act = max((r.t for r in self.rows if not r.inner), default=0.0)
        act = max(act, self.camera_end)
        for tr in self.tracks.values():
            for rows in (tr.loc, tr.rot, tr.vis):
                act = max(act, rows[-1][0])
            for vals in tr.props.values():
                act = max(act, vals[-1][0])
        for t, _a, _e, _d, _r in self.nis_ops:
            act = max(act, t)
        for t, _a, _b, dur in self.fades:
            act = max(act, t + dur)
        end = self.duration if self.duration > 0 else act + TAIL
        if end > act + TAIL:
            self.notes.append(f"The timeline lasts {self.duration:.0f} s, but nothing moves after {act:.0f} s (the script waits for something the replay "
                              f"cannot decide, such as a key press); the replay ends {TAIL:.0f} s after the last event.")
            end = act + TAIL
        return max(end, act)

    def run(self) -> dict:
        t0 = time.perf_counter()
        scene = self.scene
        self.coll = bpy.data.collections.new(f"{X.map_name(self.top)} replay")
        scene.collection.children.link(self.coll)
        self.coll[K_REPLAY] = self.key
        self.coll[K_TITLE] = self.title
        self.coll["ic_replay_map"] = self.top.name
        snap = _snapshot()
        try:
            prev = {"fps": scene.render.fps, "fps_base": scene.render.fps_base, "start": scene.frame_start, "end": scene.frame_end,
                    "camera": scene.camera.name if scene.camera is not None else "", "current": scene.frame_current}
            if K_PREV not in scene:
                scene[K_PREV] = json.dumps(prev)
            scene.render.fps, scene.render.fps_base = FPS, 1.0           # first: the NIS builder reads the frame rate
            self.apply_events()
            self.make_standins()
            self.make_nis()
            self.make_camera()
            self.make_fade()
            self.make_clips()
            self.make_markers()
            end_t = self.end_time()
            scene.frame_start, scene.frame_end = FRAME0, max(frame_of(end_t), FRAME0 + 1)
            if self.camera is not None:
                scene.camera = self.camera
            scene.frame_set(FRAME0)
            hidden = self.hide_originals()
            self.coll[K_HIDDEN] = json.dumps(hidden)
            self.coll["ic_replay_made"] = json.dumps(_made_since(snap))
        except Exception:
            clear_replay(self.context, self.top)
            raise
        quality = {"exact": 0, "approximate": 0, "skipped": 0}
        for r in self.rows:
            if not r.inner:
                quality[r.quality] = quality.get(r.quality, 0) + 1
        if self.camera is None:
            self.notes.append("This cinematic plays no camera animation: watch it in the 3D view (the game's own camera is not part of the replay).")
        report = {"key": self.key, "title": self.title, "stand_ins": len(self.standins), "events": sum(quality.values()), "quality": quality,
                  "inner_events": sum(1 for r in self.rows if r.inner),
                  "keys": self.counts["keys"], "camera": self.camera is not None, "frames": [scene.frame_start, scene.frame_end],
                  "fps": FPS, "hidden": len(hidden), "notes": list(self.notes), "markers": len(self.marker_names),
                  "nis": self.counts.get("nis", 0), "fades": self.counts.get("fades", 0), "clips": self.counts.get("clips", 0),
                  "clip_stand_ins": self.counts.get("clip_standins", 0), "clip_seconds": round(self.clip_seconds, 3)}
        text = bpy.data.texts.new(f"Replay {self.title}"[:63])
        self.rows.sort(key=lambda r: r.t)
        text.write(events_text(self.title, self.tl, self.rows, report))
        text.use_fake_user = False
        self.coll[K_TEXT] = text.name
        report["text"] = text.name
        report["seconds"] = round(time.perf_counter() - t0, 3)
        self.report_rows = self.rows
        return report


# ---------------------------------------------------------------------------------------------------- building and clearing
def _title_of(cines, key: str) -> str:
    c = next((c for c in cines if _g(c, "key") == key), None)
    return str(_g(c, "title", key) or key) if c is not None else str(key)


def prefs_game_dir(context):
    from . import prefs
    return prefs.resolve_game_dir(context)[0]


def build_replay(context, top, key: str, *, timeline=None, title: str | None = None, assume=None, game_dir=None) -> dict:
    """Build the replay of cinematic ``key`` of the map ``top`` (the previous replay of this map is cleared first). ``timeline`` is a
    ``cine_timeline.Timeline`` (read from the map's files when not given). Returns the report: counts, frames, notes, seconds."""
    clear_replay(context, top)
    if timeline is None:
        cines = cinematics(context, top, refresh=True)           # the files as the editors hold them NOW, not as they were at the last Find
        board, side = inputs_of(top)
        kw = {"assume": assume}
        try:
            archive = top.get(M.KEY_ARCHIVE, "")
            if prefs_game_dir(context):
                kw["data"] = M._view().game_data(prefs_game_dir(context), archive)
        except Exception:                                      # noqa: BLE001 -- the timeline reader finds the game's files itself
            kw.pop("data", None)
        timeline = _cine().build(board, side, key, **kw)
        title = title or _title_of(cines, key)
    if game_dir is None:
        from . import prefs
        game_dir = prefs.resolve_game_dir(context)[0]
    b = _Builder(context, top, timeline, title or key, key, game_dir=game_dir)
    rep = b.run()
    b.coll["ic_replay_report"] = json.dumps(rep)
    top[K_ACTIVE] = key
    return rep


def clear_replay(context, top) -> bool:
    """Remove the map's replay: its objects, camera, markers, text; show the originals again and put the scene's frame rate and range back.
    Returns whether there was one."""
    coll = replay_of(top)
    scene = context.scene
    if coll is None:
        return False
    for name in json.loads(coll.get(K_HIDDEN, "[]")):
        o = bpy.data.objects.get(name)
        if o is not None:
            try:
                o.hide_set(False)
            except RuntimeError:
                pass
    for m in [m for m in scene.timeline_markers if m.name.startswith("Replay: ")]:
        scene.timeline_markers.remove(m)
    try:
        made = json.loads(coll.get("ic_replay_made", "{}"))
    except ValueError:
        made = {}
    objs = list(coll.all_objects)
    datas, actions = [], []
    for o in objs:
        if o.data is not None:
            datas.append(o.data)
            dad = getattr(o.data, "animation_data", None)
            if dad is not None and dad.action is not None:
                actions.append(dad.action)
        ad = o.animation_data
        if ad is not None:
            if ad.action is not None:
                actions.append(ad.action)
            for tr in ad.nla_tracks:
                actions += [s.action for s in tr.strips if s.action is not None]
    mats = [m for o in objs if o.type == "MESH" for m in o.data.materials if m is not None]
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for c in sorted(coll.children_recursive, key=lambda c: -len(c.name)):
        bpy.data.collections.remove(c)
    text = bpy.data.texts.get(coll.get(K_TEXT, ""))
    bpy.data.collections.remove(coll)
    if text is not None:
        bpy.data.texts.remove(text)
    for d in datas:
        try:
            if d.users == 0:
                if isinstance(d, bpy.types.Camera):
                    bpy.data.cameras.remove(d)
                elif isinstance(d, bpy.types.Mesh):
                    bpy.data.meshes.remove(d)
                elif isinstance(d, bpy.types.Armature):
                    bpy.data.armatures.remove(d)
        except ReferenceError:
            pass
    images = []
    for m in dict.fromkeys(mats):                                 # the materials (and their pictures) only the replay's copies used
        try:
            if m.users == 0:
                if m.node_tree is not None:
                    images += [n.image for n in m.node_tree.nodes if getattr(n, "image", None) is not None]
                bpy.data.materials.remove(m)
        except ReferenceError:
            pass
    for im in dict.fromkeys(images):
        try:
            if im.users == 0:
                bpy.data.images.remove(im)
        except ReferenceError:
            pass
    for a in actions + [a for a in bpy.data.actions if a.get("ic_replay")]:
        try:
            if a.users == 0 and a.name in bpy.data.actions:
                bpy.data.actions.remove(a)
        except ReferenceError:
            pass
    for kind, names in made.items():                              # whatever else the build made that nothing uses now
        block = getattr(bpy.data, kind, None)
        for name in names:
            x = block.get(name) if block is not None else None
            if x is not None and x.users == 0:
                block.remove(x)
    prev = scene.get(K_PREV)
    if prev:
        try:
            p = json.loads(prev)
            scene.render.fps, scene.render.fps_base = int(p["fps"]), float(p["fps_base"])
            scene.frame_start, scene.frame_end = int(p["start"]), int(p["end"])
            scene.camera = bpy.data.objects.get(p.get("camera", ""))
            scene.frame_set(int(p["current"]))
        except (ValueError, KeyError, TypeError):
            pass
        del scene[K_PREV]
    return True


def event_text(top):
    """The text block of the map's replay, or None."""
    coll = replay_of(top)
    return bpy.data.texts.get(coll.get(K_TEXT, "")) if coll is not None else None
