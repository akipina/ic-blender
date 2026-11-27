"""The Motions box without Blender: how fast a clip moves its unit, how a motion reads in the list, how many motions were changed, and what the export
tells the modder about clips nothing plays. What ``motionspanel.py`` draws and writes.

The rules about what the game reads are in ``ICParse/ebp/motions.py`` (decompiled MotionTree.cpp and AnimPlayList.cpp).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ICParse.ebp import motions as MO

PURE = True


# ---------------------------------------------------------------------------------------------------------- how fast a clip moves its unit
@dataclass
class Speed:
    speed: float            # game units per second at the clip's own pace
    how: str                # "root" (the root bone travels) or "feet" (the feet on the ground travel back)
    samples: int = 0


def ground_speed(points, seconds: float, up: int = 2, root: int = 0) -> Optional[Speed]:
    """The speed a walking or running clip was made for, from the positions of the rig's bones in each frame of the clip.

    ``points``: (frames, bones, 3) positions, the first frame to the last (a looping clip repeats its first pose at its end), ``seconds`` the clip's
    length, ``up`` the axis that points up (Blender: 2), ``root`` the root bone. Two ways, the first that gives an answer:

    * the root bone travels (a clip that moves the unit along): the distance from the first frame to the last over the length;
    * the root stays (a clip played in place, as the game's own are): while a foot is on the ground it moves back under the body at the speed the unit
      would walk at. A bone counts as a foot when its lowest point is among the lowest of the whole clip and it lifts clear of it; it is on the ground
      in the frames where it is in the lowest 30% of its own height range. The answer is the middle of how fast the feet move against the root while
      they are on the ground, in the horizontal plane.

    Measured against the game's own animals (the Walk and Run motions of the cheetah, wolf, grizzly, horse, scorpion, gorilla: 11 clips): 8 come out
    within 20% of the reference value their blueprints hold, the others (a grizzly run, a scorpion walk and run) 30 to 40% off. It is a starting
    point: set it, then watch the feet in the game.

    None when neither way works (a clip with no feet down, a single frame).
    """
    P = np.asarray(points, dtype=np.float64)
    if P.ndim != 3 or P.shape[0] < 2 or P.shape[2] != 3 or seconds <= 0:
        return None
    F, B, _ = P.shape
    horiz = [i for i in range(3) if i != up]
    dt = seconds / (F - 1)
    size = float(np.ptp(P.reshape(-1, 3), axis=0).max()) or 1.0
    root_xy = P[:, root][:, horiz]
    moved = float(np.linalg.norm(root_xy[-1] - root_xy[0]))
    if moved > 0.02 * size:
        return Speed(moved / seconds, "root", F - 1)
    z = P[:, :, up]
    height = max(float(z[:, root].max() - z.min()), 1e-6)
    rel = P[:, :, horiz] - root_xy[:, None, :]
    vals = []
    for b in range(B):
        if b == root:
            continue
        lo, rng = float(z[:, b].min()), float(np.ptp(z[:, b]))
        if lo > float(z.min()) + 0.2 * height or rng < 0.02 * height:
            continue
        down = z[:, b] < lo + 0.3 * rng
        for f in range(F - 1):
            if down[f] and down[f + 1]:
                vals.append(float(np.linalg.norm(rel[f + 1, b] - rel[f, b])) / dt)
    if len(vals) < 2:
        return None
    return Speed(float(np.median(vals)), "feet", len(vals))


def follows_ground_speed(row) -> bool:
    """Whether a motion's speed rule is about how fast the unit walks, so a ground speed measured from its clip is the right reference value: the
    variable is ``Velocity``, the rule is not inverted (an inverted one fits a TIME, such as an attack's ``AttackTime``: reference / variable) and the
    variable is not the position in the clip (AnimPlayList.cpp:2209-2252, the unit's own speed reference only for ``Velocity``: :2247-2251)."""
    return (bool(row.clip) and row.speed_var.lower() == "velocity" and not row.invert_speed and not row.absolute_time and row.speed_ref > 0)


# ---------------------------------------------------------------------------------------------------------- reading a motion
def range_words(row) -> str:
    """The condition of a motion in a few words ('' when it has none)."""
    if not row.cond_var or (row.cond_min < 0 and row.cond_max < 0):
        return ""
    lo, hi = row.cond_min >= 0, row.cond_max >= 0
    if lo and hi:
        return f"{row.cond_var} {row.cond_min:g} to {row.cond_max:g}"
    if lo:
        return f"{row.cond_var} above {row.cond_min:g}"
    return f"{row.cond_var} up to {row.cond_max:g}"


def speed_words(row) -> str:
    if row.absolute_time:
        return f"{row.speed_var} is the position in the clip" if row.speed_var else ""
    if not row.speed_var or row.speed_ref <= 0:
        return ""
    return f"made for {row.speed_var} {row.speed_ref:g}"


def changes(baseline: MO.MotionTable, edited: MO.MotionTable) -> Tuple[int, int, int]:
    """``(changed, added, removed)``: motions of the baseline that differ now, motions that are new, motions that are gone (a changed list of children
    counts as a change of that motion)."""
    base = {r.src: r for r in baseline.rows if r.src is not None}
    changed = added = 0
    seen = set()
    for r in edited.rows:
        if r.src is None:
            added += 1
            continue
        seen.add(r.src)
        b = base.get(r.src)
        if b is None:
            continue
        if any(getattr(r, f) != getattr(b, f) for f in MO.FIELDS) or [c for c in r.children] != [c for c in b.children]:
            changed += 1
    removed = len([s for s in base if s not in seen])
    return changed, added, removed


def changes_words(baseline: MO.MotionTable, edited: MO.MotionTable) -> str:
    c, a, r = changes(baseline, edited)
    parts = []
    if c:
        parts.append(f"{c} changed")
    if a:
        parts.append(f"{a} added")
    if r:
        parts.append(f"{r} deleted")
    return ", ".join(parts)


# ---------------------------------------------------------------------------------------------------------- what the export says
def unplayed_note(clip: str) -> str:
    return (f"The clip '{clip}' is not played by any motion, so the game never plays it. Give it a motion: in the Motions box press Add Copy, "
            f"choose this clip and set when it plays (for a walk or an idle, a range of Velocity).")


def unstarted_note(name: str, clip: str = "") -> str:
    plays = f" Its clip '{clip}' does not play until something does." if clip else ""
    return (f"Nothing starts the motion '{name}': no motion that plays has it as a child, it is not the motion the game starts with, and the game "
            f"starts a motion by its own name only for Idle, Move, Swim, Fly, Attack, Melee1 and a few more.{plays} To play a clip, use New Clip "
            f"(Play it while idle) or press Add Copy on a motion that has a motion above it; a weapon or a map script that names this motion would "
            f"start it too.")


def unstarted_notes(table: MO.MotionTable, baseline: Optional[MO.MotionTable]) -> List[str]:
    """One note for each motion nothing starts (``ICParse.ebp.motions.unstarted_motions``) that the edits brought. A motion the blueprint already had
    with that name before the edit is left alone: Relic's own blueprints have motions that data (a weapon, a build step) starts, which a modder
    can neither cause nor fix. ``baseline`` None: nothing was edited, no notes."""
    if table is None or baseline is None:
        return []
    before = {baseline.rows[i].name.lower() for i in MO.unstarted_motions(baseline)}
    return [unstarted_note(table.rows[i].name, table.rows[i].clip) for i in MO.unstarted_motions(table) if table.rows[i].name.lower() not in before]


def start_words(table: MO.MotionTable, index: int) -> str:
    """Whether something starts motion ``index`` and what, in a sentence (for the report of Add Copy and New Clip). A motion above it counts only
    when the game starts that motion itself: the same test as the Check warning, so the two never disagree."""
    if not 0 <= index < len(table.rows):
        return ""
    name = table.rows[index].name
    got = MO.started_motions(table)
    parents = [table.rows[p].name for p in table.parents(index)]
    if index in got:
        live = [table.rows[p].name for p in table.parents(index) if p in got]
        if live:
            return f"'{live[0]}' starts it, so it plays whenever '{live[0]}' does" + (f" (and {len(live) - 1} more)" if len(live) > 1 else "") + "."
        return "The game starts it by its name or as the motion it starts with."
    if parents:
        return (f"Nothing starts '{name}' yet: the motion above it ('{parents[0]}') is not started by anything either, so it would never play. Put it "
                f"under a motion that is started, or use New Clip (Play it while idle).")
    return (f"Nothing starts '{name}' yet: it has no motion above it, so it would never play. Delete it and press Add Copy on a motion that has a "
            f"motion above it, or use New Clip (Play it while idle).")


def export_notes(table: Optional[MO.MotionTable], new_clips: Sequence[str], baseline: Optional[MO.MotionTable] = None) -> List[str]:
    """What an export tells the modder about the motions: each new clip nothing plays (the game makes a motion by itself only in the cases
    ``ICParse.ebp.motions.built_clips`` works out), and, when the motions were edited, what is wrong in them. A problem the blueprint already had
    before the edit is not repeated, and a blueprint that was not edited gets no such lines (Relic's own motion lists have hand-offs that look like
    problems). ``table`` is the motion list the export writes (None: the blueprint has none), ``baseline`` the list as it was before the edit (None:
    nothing was edited)."""
    out = [unplayed_note(c) for c in MO.clips_without_motion(table, new_clips)]
    if table is not None and baseline is not None:
        before = set(MO.motion_check(baseline))
        out += [p for p in MO.motion_check(table) if p not in before and "does not have" not in p]
        out += unstarted_notes(table, baseline)
    return out
