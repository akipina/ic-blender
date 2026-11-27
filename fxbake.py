"""The effect preview without Blender: what an event's effect looks like on the timeline, as arrays. ``fxpreview.py`` turns them into meshes.

An event (``anim`` clip, ``animtime``, ``fx``, ``marker``) starts its effect at a marker of the model when the clip reaches that time. The
game runs the effect through ``FXManager`` (the style classes under ``decomp/ic/src/Shared/Cross/RenderAnim/Fx``); this module runs the same
effect through :mod:`ICParse.fx.fxsim`, the simulator written from those classes, and returns the pictures it would draw, frame by frame.

**What is the game's rule and what is approximate**
* An event's time is a fraction of its clip (``AnimPlayList.cpp:1669-1672``): frame ``first + animtime * frames`` (:func:`ICParse.ebp.events`
  and ``fxcore.clip_time`` hold the inverse); ``animtime`` -1 is "when the clip starts".
* A Combo effect starts all its children at once (``FXCombo.cpp:253-257``: ``CreateChildEffect( name, 0, self, 0.0f )`` for each name) and a
  Conditional effect starts BOTH of its children too (``FXConditional.cpp:199-210`` and ``Simulation/FXLandWater.cpp``: the land / water flags
  the second one computes in ``SetTransform`` are not read in that file, so no branch is known): both are drawn here.
* The group ends when the FIRST of its children has ended (``FXCombo.cpp:318-341`` ``Obj::Update``, ``FXConditional.cpp`` ``Obj::Update`` and
  ``FXLandWater.cpp`` ``Obj::Update``): it then stops every child (``FXManager::StopEffect``). Stopping ends a looping child; a child that does
  not loop plays out as it was (a ring or beam ignores Stop), and a dead group is freed only when its children are gone
  (``FXManager.cpp`` ``FreeDeadEffects``). So a looping child, a spray or a trail beside a shorter child is shown only until that one ends
  (:attr:`Leaf.stop_at`). A stopped looping effect or trail vanishes at that time (a trail really fades out faster); a stopped spray only makes
  no more particles, and the ones it made live out their time.
* An effect ends at its ``Duration`` (``Emitter_Duration`` for a spray) unless it loops. A looping one with nothing to stop it is shown for
  :data:`LOOP_SECONDS`.
* What is approximate: the simulator draws a ring as one camera facing picture, a trail from the marker's own path, a spray's particles by
  closed form physics (no ground, no wind, no water), and mesh rings as flat pictures. The panel says so.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ICParse.fx import fxsim

try:
    from . import core
except ImportError:                                  # loaded as a plain module (no package)
    import core                                      # type: ignore[no-redef]

PURE = True

LOOP_SECONDS = 2.0          # how long an effect that loops is shown (it would run until stopped)
MAX_SECONDS = 4.0           # the longest an effect is baked (a trail's default duration is 100 seconds)
MAX_FRAMES = 240            # and never more frames than this for one effect


# ---------------------------------------------------------------------------------------------------------- the game's axes
def sgm_to_blender(a):
    """The add-on's own coordinate change: ``core.sgm_to_blender`` (the only place the axes are defined) for any number of points."""
    return core.sgm_to_blender(np.asarray(a, dtype=np.float64).reshape(-1, 3))


def blender_to_sgm(a):
    return core.blender_to_sgm(np.asarray(a, dtype=np.float64).reshape(-1, 3))


# ---------------------------------------------------------------------------------------------------------- what to start, and when
@dataclass
class Spawn:
    """One effect an event starts: the event, the effect's name, the marker it is placed at and the frame it starts on."""
    event: int                     # index in the item list
    fx: str
    marker: str
    clip: str
    frame: int                     # the timeline frame it starts on
    time: Optional[float] = None   # the event's animtime as written (None: none; -1: when the clip starts)


def plan_spawns(items, clip_ranges: Dict[str, Tuple[float, float]], clip: str = "", event: Optional[int] = None) -> Tuple[List[Spawn], List[str]]:
    """The effects the events start. ``items`` are :class:`fxcore.EventItem`; ``clip_ranges`` maps a lower case clip name to its (first, last)
    key frames. With ``clip`` only that clip's events; with ``event`` (an index in ``items``) only that one, at the start of its clip (a
    ``clip_ranges`` entry for ``""`` is the frame range to use for an event whose clip is not known). Returns ``(spawns, notes)``."""
    out: List[Spawn] = []
    notes: List[str] = []
    for i, it in enumerate(items):
        if event is not None and i != event:
            continue
        fx = it.get("fx").strip()
        if not fx:
            continue
        name = it.anim
        if clip and name.lower() != clip.lower() and event is None:
            continue
        if fx.startswith("%"):
            notes.append(f"Event '{it.name}' plays {fx}, a name the game fills in while it runs: it is not shown here.")
            continue
        rng = clip_ranges.get(name.lower()) or (clip_ranges.get("") if event is not None else None)
        if rng is None:
            notes.append(f"Event '{it.name}' belongs to the clip '{name}', which this model does not have: it is not shown here.")
            continue
        first, last = rng
        at = it.animtime
        frames = max(1.0, last - first + 1.0)
        frame = first if (at is None or at < 0) else first + min(max(at, 0.0), 0.999) * frames
        out.append(Spawn(i, fx, it.get("marker").strip(), name, int(round(frame)), at))
    out.sort(key=lambda s: (s.frame, s.event))
    return out, notes


# ---------------------------------------------------------------------------------------------------------- effects as leaves
@dataclass
class Leaf:
    """An effect that draws something (ring, beam, trail, spray) with the effect a spray makes its particles from."""
    name: str
    fx: object
    particle: object = None
    duration: float = 2.0
    loops: bool = False
    stop_at: Optional[float] = None       # seconds: a group it belongs to ended then and stopped it (see _stoppable for what a Stop ends)


_LOOP_KEYS = ("Loop", "Emitter_Loop")


def _loops(fx) -> bool:
    for k in _LOOP_KEYS:
        try:
            if fx.has(k) and int(float(fx.get(k) or 0)):
                return True
        except (TypeError, ValueError):
            pass
    return False


_INF = float("inf")


def _node(lookup, name: str, notes: List[str], seen: set, depth: int):
    """``Leaf`` for an effect that draws, ``list of nodes`` for a Combo or Conditional, None for one that is missing or met before."""
    key = name.lower()
    if key in seen or depth > 8:
        return None
    seen.add(key)
    fx = lookup(name)
    if fx is None:
        notes.append(f"The effect '{name}' is in neither the project nor the game: it is not shown.")
        return None
    style = (fx.style or "").upper()
    if "COMBO" in style or "CONDITIONAL" in style:
        return [n for n in (_node(lookup, c, notes, seen, depth + 1) for c in fx.children()) if n is not None]
    particle = None
    if "SPRAY" in style:
        pn = fx.get("Particle_Fx")
        if pn:
            particle = lookup(str(pn))
            if particle is None:
                notes.append(f"The effect '{name}' makes its particles from '{pn}', which is not found: small dots are shown instead.")
    try:
        dur = fxsim.duration_of(fx)
    except Exception:                                   # noqa: BLE001 -- a value the simulator cannot read
        dur = 2.0
    return Leaf(name, fx, particle, dur, _loops(fx))


def _style(leaf: Leaf) -> str:
    return (leaf.fx.style or "").upper()


def _stoppable(leaf: Leaf) -> bool:
    """Whether the game's Stop ends this kind of effect: a spray's Update returns at once when stopped (FXSpray.cpp:364), a looping ring or beam ends
    (FXRing.cpp:256, FXBeam.cpp:300) and a trail runs out (FXTrail.cpp:369-412); a ring or beam that does not loop ignores it."""
    return leaf.loops or "SPRAY" in _style(leaf) or "TRAIL" in _style(leaf)


def _cut_by_stop(leaf: Leaf) -> bool:
    """Whether a stop makes the picture vanish at ``stop_at``: a looping effect and a trail (which really fades out faster). A spray that is stopped
    only stops emitting: the particles it made are effects of their own and live out their time (``emit_until`` in :func:`ICParse.fx.fxsim.simulate`)."""
    return leaf.stop_at is not None and (leaf.loops or "TRAIL" in _style(leaf))


def _gone_at(leaf: Leaf) -> float:
    """Seconds until the effect has been freed when nothing stops it: never when it loops; a spray's emitter is dead at its duration but is freed
    only when the particles it made are gone (``FXManager.cpp`` ``FreeDeadEffects``), so its particles' lifetime counts."""
    if leaf.loops:
        return _INF
    if "SPRAY" in _style(leaf):
        return leaf.duration + (fxsim.duration_of(leaf.particle, 0.4) if leaf.particle is not None else 0.4)
    return leaf.duration


def _ends(node, cap: float = _INF) -> float:
    """Seconds until the node is gone, when its group stops it at ``cap``: a leaf when it has been freed (a stoppable one no later than ``cap`` for
    a loop or a trail; a stopped spray is dead at once, but its particles live out); a group when its first child has gone (it stops the others
    then) and every other child has played out."""
    if isinstance(node, Leaf):
        end = _gone_at(node)
        if _stoppable(node) and cap != _INF:
            if node.loops or "TRAIL" in _style(node):
                return min(end, cap)
            return min(end, max(cap, 0.0) + (end - node.duration))          # a spray: the particles outlive the stop
        return end
    if not node:
        return 0.0
    first = min(_ends(n, cap) for n in node)
    return _INF if first == _INF else max(_ends(n, min(cap, first)) for n in node)


def _collect(node, cap: float, out: List[Leaf]) -> None:
    if isinstance(node, Leaf):
        if cap != _INF and _stoppable(node):
            node.stop_at = cap
        out.append(node)
        return
    here = min([cap] + [_ends(n, cap) for n in node]) if node else cap
    for n in node:
        _collect(n, here, out)


def flatten(lookup: Callable[[str], object], name: str, notes: List[str], _seen=None, _depth=0) -> List[Leaf]:
    """The effects below ``name`` that draw something: a Combo's and a Conditional's children (all of them, see the module note), recursively.
    ``lookup(name)`` returns an :class:`ICParse.fx.fxlua.FxEffect` or None. A looping effect in a group with a finite one gets the time the
    group's first child has gone as its ``stop_at`` (so does a spray or a trail, which Stop also ends)."""
    node = _node(lookup, name, notes, _seen if _seen is not None else set(), _depth)
    out: List[Leaf] = []
    if node is not None:
        _collect(node, _INF, out)
    return out


def shown_seconds(leaf: Leaf) -> float:
    """How long a leaf is shown: its duration (:data:`LOOP_SECONDS` for a looping one), or less when its group stops it."""
    secs = LOOP_SECONDS if leaf.loops else leaf.duration
    return min(secs, leaf.stop_at) if _cut_by_stop(leaf) else secs


def length_frames(leaves: Sequence[Leaf], fps: float) -> int:
    """How many frames the effect is shown for (at least 1)."""
    if not leaves:
        return 0
    secs = max(shown_seconds(l) for l in leaves)
    return max(1, min(MAX_FRAMES, int(math.ceil(min(secs, MAX_SECONDS) * fps)) + 1))


# ---------------------------------------------------------------------------------------------------------- primitives -> quads
@dataclass
class Geom:
    """Quads of one effect on one frame, in the game's axes relative to the marker (before :func:`place`): ``verts`` (4n, 3) four to a quad in
    order, ``uvs`` (4n, 2) and ``cols`` (4n, 4) per corner."""
    verts: np.ndarray
    uvs: np.ndarray
    cols: np.ndarray

    @property
    def quads(self) -> int:
        return len(self.verts) // 4


_NO_GEOM = Geom(np.zeros((0, 3)), np.zeros((0, 2)), np.zeros((0, 4)))


def quads_of(prim: dict, right=(1.0, 0.0, 0.0), up=(0.0, 1.0, 0.0)) -> Geom:
    """The primitive bundle :func:`fxsim.simulate` returns as quads: a billboard faces the ``right`` / ``up`` axes (the camera's, in the
    effect's axes) rolled by its roll; a ribbon is a quad strip."""
    right = np.asarray(right, dtype=np.float64)
    up = np.asarray(up, dtype=np.float64)
    v: List[np.ndarray] = []
    uv: List[Tuple[float, float]] = []
    col: List[Sequence[float]] = []
    corner_uv = ((0, 0), (1, 0), (1, 1), (0, 1))
    for center, size, rgba, roll in prim.get("billboards", []):
        c = np.asarray(center, dtype=np.float64)
        hr = (right * math.cos(roll) + up * math.sin(roll)) * (size * 0.5)
        hu = (-right * math.sin(roll) + up * math.cos(roll)) * (size * 0.5)
        v += [c - hr - hu, c + hr - hu, c + hr + hu, c - hr + hu]
        uv += corner_uv
        col += [rgba] * 4
    for strip in prim.get("ribbons", []):
        for k in range(len(strip) - 1):
            a0, b0, c0, u0 = strip[k]
            a1, b1, c1, u1 = strip[k + 1]
            v += [np.asarray(a0, dtype=np.float64), np.asarray(b0, dtype=np.float64), np.asarray(b1, dtype=np.float64), np.asarray(a1, dtype=np.float64)]
            uv += [(u0, 0), (u0, 1), (u1, 1), (u1, 0)]
            col += [c0, c0, c1, c1]
    if not v:
        return _NO_GEOM
    return Geom(np.array(v), np.array(uv, dtype=np.float64), np.array([list(c)[:4] for c in col], dtype=np.float64))


# ---------------------------------------------------------------------------------------------------------- baking
@dataclass
class Layer:
    """What one drawing effect looks like: its picture and blending (for the material); ``geoms[k]`` is the quads on baked frame ``k``."""
    name: str
    texture: str
    blend: str
    geoms: List[Geom] = field(default_factory=list)


@dataclass
class Baked:
    start: int                                   # the timeline frame of baked frame 0
    frames: int
    layers: List[Layer]
    notes: List[str] = field(default_factory=list)

    def quad_count(self) -> int:
        return sum(g.quads for l in self.layers for g in l.geoms)


def rotation_of(m) -> np.ndarray:
    """The rotation part of a 4x4 world matrix with its scale taken out (a marker's size must not scale the picture)."""
    r = np.asarray(m, dtype=np.float64)[:3, :3].copy()
    for i in range(3):
        n = np.linalg.norm(r[:, i])
        if n > 1e-9:
            r[:, i] /= n
    return r


def place(verts: np.ndarray, m) -> np.ndarray:
    """Quad corners in the effect's axes -> Blender world: the game axes become the add-on's, then the marker's world matrix ``m`` (4x4) moves them."""
    if len(verts) == 0:
        return np.zeros((0, 3))
    m = np.asarray(m, dtype=np.float64)
    return sgm_to_blender(verts) @ rotation_of(m).T + m[:3, 3]


def bake(leaves: Sequence[Leaf], fps: float, frames: int, matrices: Sequence, cameras: Optional[Sequence] = None, start: int = 0) -> Baked:
    """Run the effects for ``frames`` frames. ``matrices[k]`` is the marker's 4x4 world matrix on baked frame k (Blender axes; None = identity);
    ``cameras[k]`` is ``(right, up)`` the camera's world axes on that frame (None: a camera looking along the world's +Y, the front view).
    Returns the quads in WORLD coordinates (the marker's matrix of each frame applied), so a moving marker drags the picture along and a trail
    follows it. A style the simulator cannot run is left out and noted."""
    out = Baked(start, frames, [])
    ident = np.eye(4)
    mats = [np.asarray(m if m is not None else ident, dtype=np.float64) for m in (list(matrices) + [None] * frames)[:frames]]
    cams = list(cameras or [])
    cams += [None] * (frames - len(cams))
    # the marker's own path in world space, for a trail
    world_pos = [m[:3, 3] for m in mats]
    for leaf in leaves:
        layer = Layer(leaf.name, "", "alpha")
        failed = ""
        for k in range(frames):
            secs = k / fps
            if leaf.loops:
                if _cut_by_stop(leaf) and secs > leaf.stop_at + 1e-9:              # its group ended and stopped it
                    layer.geoms.append(_NO_GEOM)
                    continue
                t = (secs % leaf.duration) / leaf.duration if leaf.duration > 0 else 0.0
            elif secs > leaf.duration + 1e-9 or (_cut_by_stop(leaf) and secs > leaf.stop_at + 1e-9):
                layer.geoms.append(_NO_GEOM)
                continue
            else:
                t = min(1.0, secs / leaf.duration) if leaf.duration > 0 else 0.0
            m = mats[k]
            r = rotation_of(m)
            cam = cams[k]
            if cam is None:
                cam = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
            # the camera's axes seen from the effect: world -> the marker's own axes -> the game's axes
            right = blender_to_sgm(r.T @ np.asarray(cam[0], dtype=np.float64))[0]
            up = blender_to_sgm(r.T @ np.asarray(cam[1], dtype=np.float64))[0]
            # the path a trail follows: where the marker has been, in the effect's axes as seen from now
            path = []
            for j in range(k + 1):
                rel = r.T @ (world_pos[j] - m[:3, 3])
                path.append(((j / fps) / leaf.duration if leaf.duration > 0 else 0.0, tuple(blender_to_sgm(rel)[0])))
            try:
                emit_until = None
                if leaf.stop_at is not None and "SPRAY" in _style(leaf) and not leaf.loops and leaf.duration > 0:
                    emit_until = leaf.stop_at / leaf.duration                  # a stopped spray makes no more particles
                prim = fxsim.simulate(leaf.fx, t, fps=fps, duration=leaf.duration, emitter_path=path, particle_fx=leaf.particle,
                                      emit_until=emit_until)
            except Exception as e:                                         # noqa: BLE001 -- the simulator raises on a few stock effects
                failed = f"{type(e).__name__}: {e}"
                break
            if k == 0 or not layer.texture:
                layer.texture = str(prim.get("texture") or "")
                layer.blend = str(prim.get("blend") or "alpha")
            g = quads_of(prim, right, up)
            layer.geoms.append(Geom(place(g.verts, m), g.uvs, g.cols) if g.quads else g)
        if failed:
            out.notes.append(f"The effect '{leaf.name}' could not be shown here ({failed}). The game plays it as usual.")
            continue
        out.layers.append(layer)
    return out


# ---------------------------------------------------------------------------------------------------------- textures
def texture_path(ref: str) -> str:
    """The data-relative path (lower case, ``/``) of a picture an effect names (``Data:art/fx/point.tga``)."""
    p = (ref or "").replace("\\", "/")
    if p.lower().startswith("data:"):
        p = p[5:]
    return p.lstrip("/").lower()
