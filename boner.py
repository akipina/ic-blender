"""Boner rules, bpy-free: the skinning loop's picking, Auto Skin and target rules (the Bones sub-mode).

Relic's Boner (``docs/historical/rdn-official/IC_Boner_Documentation.md``) skins by sub-mode: in Bone mode you click a
bone, in Vertex mode you select points and Assign / Remove them, Get Selection from Bone selects a bone's points, and
Reset re-derives every point's bone from the skeleton. The Blender operators (``ops.BZ_OT_pick_bone``,
``bz.skin_*``) are thin shells over these functions, which the tests run without Blender.

Owner indices are SOURCE bone indices (``store.bone_names`` order, the file's bone order); -1 means no bone. The
engine asserts that every patch control point has a bone, so Export refuses unowned points a patch uses
(``core.unowned_patch_points``); unused unowned points (woolly_mammoth has two) are allowed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import numpy as np

UNASSIGNED = -1
_TOKENS = re.compile(r"[_\s.\-]+")
PICK_RADIUS = 15.0                      # pixels

# viewport navigation passes through a picking modal (the same set Pick Ring uses)
NAV_EVENTS = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE",
              "NDOF_MOTION"}


# --------------------------------------------------------------------------------------------- screen picking
def nearest_point(xy, mouse, radius=PICK_RADIUS, allowed=None):
    """``(index, pixel distance)`` of the projected point nearest ``mouse`` within ``radius``, or ``(None, None)``.
    ``xy``: (N, 2) region pixels, NaN for a point that does not project (behind the view); ``allowed``: optional (N,)
    bool mask of the points that may be picked."""
    p = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if not len(p):
        return None, None
    d = np.hypot(p[:, 0] - float(mouse[0]), p[:, 1] - float(mouse[1]))
    d[~np.isfinite(d)] = np.inf
    if allowed is not None:
        d[~np.asarray(allowed, dtype=bool)] = np.inf
    i = int(np.argmin(d))
    return (i, float(d[i])) if d[i] <= radius else (None, None)


def segment_distances(a, b, mouse) -> np.ndarray:
    """Pixel distance from ``mouse`` to each 2D segment ``a[k]``-``b[k]`` (NaN endpoints -> inf)."""
    a = np.asarray(a, dtype=np.float64).reshape(-1, 2)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 2)
    m = np.array([float(mouse[0]), float(mouse[1])])
    ab = b - a
    ll = (ab * ab).sum(1)
    t = np.where(ll > 1e-12, ((m - a) * ab).sum(1) / np.where(ll > 1e-12, ll, 1.0), 0.0)
    q = a + np.clip(t, 0.0, 1.0)[:, None] * ab
    d = np.hypot(q[:, 0] - m[0], q[:, 1] - m[1])
    d[~np.isfinite(d)] = np.inf
    return d


def nearest_segment(a, b, mouse, radius=PICK_RADIUS, allowed=None):
    """``(index, pixel distance)`` of the segment nearest ``mouse`` within ``radius``, or ``(None, None)``."""
    d = segment_distances(a, b, mouse)
    if not len(d):
        return None, None
    if allowed is not None:
        d[~np.asarray(allowed, dtype=bool)] = np.inf
    i = int(np.argmin(d))
    return (i, float(d[i])) if d[i] <= radius else (None, None)


@dataclass
class BoneHit:
    """What is under the cursor for Pick Bone: ``bone`` (source index, or None), ``kind`` ("POINT" / "BONE" / None),
    the control point when it was one, and a readable ``note`` when nothing pickable is there."""
    bone: Optional[int] = None
    kind: Optional[str] = None
    point: Optional[int] = None
    note: str = ""


def pick_target(point_xy, point_owner, seg_a, seg_b, mouse, radius=PICK_RADIUS, bone_names=None) -> BoneHit:
    """Pick Bone's rule: the nearer of the nearest projected control point (it gives its owner bone) and the nearest
    projected bone segment (that bone), each within ``radius`` pixels; a tie goes to the point. An unassigned point
    (owner -1) is reported, not picked: it has no bone."""
    pi, pd = nearest_point(point_xy, mouse, radius)
    bi, bd = nearest_segment(seg_a, seg_b, mouse, radius)
    if pi is not None and (bi is None or pd <= bd):
        own = int(np.asarray(point_owner).reshape(-1)[pi])
        if own < 0:
            return BoneHit(None, "POINT", pi, f"control point {pi} has no bone (orange): nothing to pick")
        return BoneHit(own, "POINT", pi)
    if bi is not None:
        return BoneHit(int(bi), "BONE")
    return BoneHit(note="no control point or bone under the cursor")


class BonePickRule:
    """The Pick Bone modal's event rule, bpy-free: ``handle(event_type, value, hit)`` returns "PASS_THROUGH" (viewport
    navigation), "RUNNING", "FINISH" (``picked`` holds the bone) or "CANCEL"; a refused click leaves ``message``."""

    def __init__(self):
        self.picked: Optional[int] = None
        self.message = ""

    def handle(self, event_type: str, value: str, hit: Optional[BoneHit]) -> str:
        self.message = ""
        if event_type in NAV_EVENTS or (event_type.startswith("NUMPAD_") and event_type != "NUMPAD_ENTER"):
            return "PASS_THROUGH"
        if value != "PRESS":
            return "RUNNING"
        if event_type in ("ESC", "RIGHTMOUSE"):
            return "CANCEL"
        if event_type == "LEFTMOUSE":
            if hit is None or hit.bone is None:
                self.message = (hit.note if hit is not None and hit.note else "no control point or bone under the cursor")
                return "RUNNING"
            self.picked = int(hit.bone)
            return "FINISH"
        return "RUNNING"


# --------------------------------------------------------------------------------------------- Auto Skin (Reset)
def segment_distances_3d(points, heads, tails, chunk=4096) -> np.ndarray:
    """(N, B) distance from each point to each bone segment head-tail."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    A = np.asarray(heads, dtype=np.float64).reshape(-1, 3)
    B = np.asarray(tails, dtype=np.float64).reshape(-1, 3)
    AB = B - A
    LL = (AB * AB).sum(1)
    safe = np.where(LL > 1e-18, LL, 1.0)
    out = np.empty((len(P), len(A)))
    for s in range(0, len(P), chunk):
        p = P[s:s + chunk]
        ap = p[:, None, :] - A[None]
        t = np.where(LL[None] > 1e-18, (ap * AB[None]).sum(2) / safe[None], 0.0)
        q = A[None] + np.clip(t, 0.0, 1.0)[..., None] * AB[None]
        out[s:s + chunk] = np.linalg.norm(p[:, None, :] - q, axis=2)
    return out


def auto_skin(points, heads, tails, eligible=None) -> np.ndarray:
    """The nearest eligible bone segment of every point (Boner's Reset / a first skin): (N,) source bone indices, the
    lowest index on a tie; all -1 when no bone is eligible. Segments come from :func:`skin_segments`, eligibility from
    :func:`auto_skin_mask` (plus the rig's Deform flags)."""
    D = segment_distances_3d(points, heads, tails)
    if eligible is not None:
        D[:, ~np.asarray(eligible, dtype=bool)] = np.inf
    if not D.shape[1] or not np.isfinite(D).any():
        return np.full(len(D), UNASSIGNED, dtype=np.int64)
    return np.argmin(D, axis=1).astype(np.int64)


def skin_segments(heads, parents) -> np.ndarray:
    """(B, 3) tails for Auto Skin's bone segments: each bone runs from its head to its nearest child's head, and a leaf
    bone is its head alone (zero length). The rig draws a leaf 0.1 long along its bind axis (``rig.create_armature``,
    visual only); that stub reaches into jaws, ears and feet, so Auto Skin never uses the rig's tails. ``parents``: per
    bone, the parent's index or None / -1."""
    H = np.asarray(heads, dtype=np.float64).reshape(-1, 3)
    T = H.copy()
    kids = [[] for _ in range(len(H))]
    for i, p in enumerate(parents):
        if p is not None and 0 <= int(p) < len(H) and int(p) != i:
            kids[int(p)].append(i)
    for i, ks in enumerate(kids):
        if ks:
            T[i] = H[min(ks, key=lambda j: float(np.linalg.norm(H[j] - H[i])))]
    return T


AUTO_TARGETS = (("AUTO", "Selected, else Unassigned", "Edit Mode: selected points (or all unassigned); Object Mode: all "
                                                      "unassigned"),
                ("SELECTED", "Selected", "The selected control points (Edit Mode)"),
                ("UNASSIGNED", "Unassigned", "Every control point with no bone"),
                ("ALL", "Reset All", "Replace the whole skin, derived again from the skeleton (Boner's Reset)"))


def skin_targets(owners, selected, target="AUTO") -> np.ndarray:
    """The points Auto Skin assigns: ``target`` one of :data:`AUTO_TARGETS`; ``selected`` a (N,) bool mask, or None
    when the selection is not shown (Object Mode: AUTO means Unassigned there, SELECTED is refused by the operator)."""
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    sel = np.zeros(len(own), dtype=bool) if selected is None else np.asarray(selected, dtype=bool).reshape(-1)
    if target == "AUTO":
        target = "SELECTED" if sel.any() else "UNASSIGNED"
    if target == "SELECTED":
        return np.nonzero(sel)[0]
    if target == "UNASSIGNED":
        return np.nonzero(own < 0)[0]
    if target == "ALL":
        return np.arange(len(own))
    raise ValueError(f"unknown Auto Skin target {target!r}")


def auto_target_refusal(target, selected, in_edit_mode) -> Optional[str]:
    """Why Auto Skin must not run with this target, or None. The selection only means something where the modder sees
    it (Edit Mode), and a selection of every point would silently replace the creature's whole skin (import leaves
    every point selected): that is Reset All's job, asked for by name."""
    if not in_edit_mode:
        if target == "SELECTED":
            return ("Auto Skin Selected works in Edit Mode, where the selection is shown: enter Edit Mode, or use "
                    "Unassigned or Reset All")
        return None
    sel = np.asarray(selected, dtype=bool).reshape(-1)
    if target in ("AUTO", "SELECTED") and len(sel) and sel.all():
        return (f"every control point is selected ({len(sel)}), so Auto Skin would replace the whole skin: use Reset All, "
                "or select only the points to skin (Alt+A clears the selection)")
    return None


def stub_bones(model) -> np.ndarray:
    """(B,) True for stub bones (``meta0`` low byte 1: a wing / arm socket's bone, which owns no points on every stock
    creature): Auto Skin never gives them points."""
    return np.array([(int(b.meta0) & 0xFF) == 1 for b in model.bones], dtype=bool)


def end_site_bones(names) -> np.ndarray:
    """(B,) True for end-site bones: a name token (split at ``_``, space, ``.``, ``-``) starting with ``end`` -- Relic's
    names for the end of a chain (endjaw, endtail, endear_l, front_endleg_r, rear_endfoot_l, endwing_r). Measured: the
    611 such bones of the 61 base-game creatures own no control points, so Auto Skin never gives them any. (Tellurian
    add-on creatures do skin to them and load: it is Relic's convention, not an engine rule.)"""
    return np.array([any(t.startswith("end") for t in _TOKENS.split(str(n).lower())) for n in names], dtype=bool)


def auto_skin_mask(model) -> np.ndarray:
    """(B,) the source bones Auto Skin may skin to, from the file alone: not a stub bone, not an end site. (The
    operator also drops bones whose Deform is off in the rig.)"""
    names = [b.name for b in model.bones]
    return ~stub_bones(model) & ~end_site_bones(names)


# --------------------------------------------------------------------------------------------- read-outs
@dataclass
class SkinStats:
    counts: np.ndarray                  # (B,) points per bone
    unassigned: int                     # points with no bone
    unassigned_used: int                # ... of which a patch uses (Export refuses these)
    blended: int = 0                    # points whose vertex groups hold blended / partial weights (Export refuses)


def skin_stats(owners, n_bones, patches=None, blended=0) -> SkinStats:
    """Per-bone point counts and the unassigned read-outs of ``owners`` (the live owners: ``core.live_owners``)."""
    own = np.asarray(owners, dtype=np.int64).reshape(-1)
    ok = (own >= 0) & (own < n_bones)
    counts = np.bincount(own[ok], minlength=int(n_bones))[:int(n_bones)] if n_bones else np.zeros(0, np.int64)
    used = 0
    if patches is not None:
        p = np.asarray(patches, dtype=np.int64).reshape(-1)
        p = np.unique(p[(p >= 0) & (p < len(own))])
        used = int((~ok[p]).sum()) if len(p) else 0
    return SkinStats(counts=counts, unassigned=int((~ok).sum()), unassigned_used=used, blended=int(blended))


# --------------------------------------------------------------------------------------------- Limbs sub-mode
# Boner's Limb Options checkboxes (Boner.dlm table 0x10032138: {control id, bits to set, bits to clear}). Ticking a box
# sets its bit and clears the other boxes of its group, so each group holds at most one bit; Non-Swappable stands alone.
# The Connection Options dialog has the same boxes (B3).
NOSWAP = 0x800000
CLASS_MASK = 0xFF7FFFFF                 # the class the OE and the combiner match: the id without Non-Swappable


@dataclass(frozen=True)
class LimbFlag:
    key: str
    bit: int
    label: str
    group: str                          # SIDE / POSITION / PLANE / TYPE, or "" (Non-Swappable)


LIMB_FLAGS = (
    LimbFlag("LEFT", 0x1, "Left", "SIDE"), LimbFlag("RIGHT", 0x2, "Right", "SIDE"),
    LimbFlag("FRONT", 0x4, "Front", "POSITION"), LimbFlag("REAR", 0x8, "Rear", "POSITION"),
    LimbFlag("MID", 0x20000, "Mid", "POSITION"), LimbFlag("MID2", 0x40000, "Mid 2", "POSITION"),
    LimbFlag("TOP", 0x200, "Top", "PLANE"), LimbFlag("SIDE", 0x400, "Side", "PLANE"),
    LimbFlag("TORSO", 0x800, "Torso", "TYPE"), LimbFlag("HEAD", 0x40, "Head", "TYPE"),
    LimbFlag("TAIL", 0x80, "Tail", "TYPE"), LimbFlag("ARM", 0x10, "Arm", "TYPE"), LimbFlag("LEG", 0x20, "Leg", "TYPE"),
    LimbFlag("WING", 0x100, "Wing", "TYPE"), LimbFlag("FIN", 0x8000, "Fin", "TYPE"),
    LimbFlag("NOSWAP", NOSWAP, "Non-Swappable", ""),
)
FLAG_GROUPS = (("SIDE", "Side"), ("POSITION", "Position"), ("PLANE", "Plane"), ("TYPE", "Type"))
_FLAG_BY_BIT = {f.bit: f for f in LIMB_FLAGS}
_KNOWN_BITS = 0
for _f in LIMB_FLAGS:
    _KNOWN_BITS |= _f.bit


def class_of(limb_id) -> int:
    """The limb's class: its id without Non-Swappable (``id & 0xFF7FFFFF``), what the OE and the combiner match."""
    return int(limb_id) & CLASS_MASK


def toggle_flag(limb_id, bit) -> int:
    """Boner's checkbox rule: a ticked box is unticked; an unticked one sets its bit and clears the rest of its group.
    Bits no checkbox owns (the horse antlers' 0x10000) are kept."""
    bit = int(bit)
    f = _FLAG_BY_BIT.get(bit)
    if f is None:
        raise ValueError(f"0x{bit:x} is no Boner classification checkbox")
    v = int(limb_id) & 0xFFFFFFFF
    if v & bit:
        return v & ~bit
    if f.group:
        for g in LIMB_FLAGS:
            if g.group == f.group:
                v &= ~g.bit
    return v | bit


_SIDE_ABBR = {0x1: "L", 0x2: "R"}


def class_label(limb_id) -> str:
    """A readable class: "Torso", "L Front Leg", "R Side Wing", "L Mid 2 Leg · Non-Swappable"; "no class" for 0; bits
    no checkbox owns are shown in hex."""
    v = int(limb_id) & 0xFFFFFFFF
    words = []
    for group in ("SIDE", "POSITION", "PLANE", "TYPE"):
        for f in LIMB_FLAGS:
            if f.group == group and v & f.bit:
                words.append(_SIDE_ABBR.get(f.bit, f.label))
    extra = v & ~_KNOWN_BITS
    if extra:
        words.append(f"0x{extra:x}")
    text = " ".join(words) if words else "no class"
    return text + (" · Non-Swappable" if v & NOSWAP else "")


@dataclass(frozen=True)
class StandardLimb:
    suffix: str
    limb_id: int                # the id Add Standard Limb gives (stock mid legs also carry Non-Swappable)
    root: str                   # the typical root bone (RULES.md 2.1)
    need: str                   # "patches" (OE: required with patches), "stub" (required, may be a stub), "optional"


# The Object Editor's 17 limb classes, in the stock limb order (Boner's list, then arms, then mid legs)
STANDARD_LIMBS = (
    StandardLimb("Torso", 0x800, "pelvis", "patches"), StandardLimb("Head", 0x40, "neck2", "patches"),
    StandardLimb("Tail", 0x80, "tail1", "patches"),
    StandardLimb("LFLeg", 0x25, "front_leg1_l", "stub"), StandardLimb("RFLeg", 0x26, "front_leg1_r", "stub"),
    StandardLimb("LRLeg", 0x29, "rear_leg1_l", "stub"), StandardLimb("RRLeg", 0x2A, "rear_leg1_r", "stub"),
    StandardLimb("LSWing", 0x501, "swing1_l", "stub"), StandardLimb("RSWing", 0x502, "swing1_r", "stub"),
    StandardLimb("LTWing", 0x301, "top_wing_l", "stub"), StandardLimb("RTWing", 0x302, "top_wing_r", "stub"),
    StandardLimb("LFArm", 0x15, "front_arm1_l", "stub"), StandardLimb("RFArm", 0x16, "front_arm1_r", "stub"),
    StandardLimb("LMLeg", 0x820021, "mid_leg1_l", "optional"), StandardLimb("RMLeg", 0x820022, "mid_leg1_r", "optional"),
    StandardLimb("LM2Leg", 0x840021, "midrear_leg1_l", "optional"),
    StandardLimb("RM2Leg", 0x840022, "midrear_leg1_r", "optional"),
)
STANDARD_BY_SUFFIX = {s.suffix: s for s in STANDARD_LIMBS}


def missing_standard(limb_ids):
    """The standard limbs whose class no limb has, in the stock order."""
    have = {class_of(i) for i in limb_ids}
    return [s for s in STANDARD_LIMBS if class_of(s.limb_id) not in have]


def standard_root_refusal(std, root, owner) -> Optional[str]:
    """Why Add Standard Limb can't add ``std`` (a :class:`StandardLimb`), or None. ``root``: the skeleton's bone matching
    ``std.root`` ignoring case (None when absent); ``owner``: the limb already listing it (None when free). A limb needs a
    bone: with none the engine's model loader reports 'limbs are boneless!' and ComboTest.exe does not take the creature
    (harness-verified), so a boneless stub is never made."""
    tail = ("a limb with no bones makes the engine's model loader report 'limbs are boneless!' and ComboTest.exe won't "
            "take the creature")
    if root is None:
        return (f"the skeleton has no bone '{std.root}' for the {class_label(std.limb_id)} limb's root: add it first (Boner "
                f"> Bones > Add Bone), or give an existing limb this class; {tail}")
    if owner is not None:
        return (f"bone '{root}', the {class_label(std.limb_id)} limb's root, already belongs to limb '{owner}': delete "
                f"that limb or take the bone out of it first; {tail}")
    return None


LIMB_NAME_MAX = 63                      # the OE reader copies 0x3f bytes (strncpy)


def limb_name_prefix(names, creature="") -> str:
    """The creature prefix Boner puts before a limb suffix (``<object name>_<suffix>``): the most common prefix of the
    existing limb names (raven's limbs say Eagle_), else the creature's name."""
    counts = {}
    for n in names:
        if "_" in str(n):
            p = str(n).rsplit("_", 1)[0]
            counts[p] = counts.get(p, 0) + 1
    if counts:
        return max(counts.items(), key=lambda kv: (kv[1], -list(counts).index(kv[0])))[0]
    return str(creature or "Creature")


def default_limb_name(names, creature, suffix) -> str:
    """``<prefix>_<suffix>``, made unique ignoring case (``_2``, ``_3``, ...) when a limb already has that name."""
    base = f"{limb_name_prefix(names, creature)}_{suffix}"
    low = {str(n).lower() for n in names}
    name, k = base, 2
    while name.lower() in low:
        name, k = f"{base}_{k}", k + 1
    return name


def limb_name_error(name, others, what="limb") -> Optional[str]:
    """Why ``name`` can't name a limb (``what="connection"``: a connection, the same rule), or None. The OE keeps 63
    bytes of a name; names are ASCII (the file's strings); two limbs (two connections) may not share a name, ignoring
    case (the OE's messages and every tool tell them apart by it)."""
    name = str(name)
    if not name.strip():
        return f"a {what} needs a name"
    if name != name.strip():
        return f"'{name}' starts or ends with a space"
    if any(ord(ch) > 126 or ord(ch) < 32 for ch in name):
        return f"'{name}' has characters outside plain ASCII: {what} names are stored as ASCII"
    if len(name.encode("ascii")) > LIMB_NAME_MAX:
        return f"'{name}' is {len(name)} characters: the Object Editor keeps at most {LIMB_NAME_MAX}"
    low = name.lower()
    clash = next((o for o in others if str(o).lower() == low), None)
    if clash is not None:
        return f"another {what} is already called '{clash}' (names must differ, ignoring case)"
    return None


def orphaned_connections(ids_before, ids_after, conn_names, conn_flags1, conn_flags2):
    """``[(connection name, "child" | "parent", class)]``: the connections whose child class (flags2) or parent class
    (flags1) some limb had in ``ids_before`` and no limb has in ``ids_after``. The OE and the combiner look for a limb of
    that class (ComboTest.exe asserts 'pWasLimb' when a limb is grafted onto such a socket)."""
    have = {class_of(i) for i in ids_after}
    lost = {class_of(i) for i in ids_before} - have - {0}
    out = []
    for n, f1, f2 in zip(conn_names, conn_flags1, conn_flags2):
        if class_of(f2) in lost:
            out.append((str(n), "child", class_of(f2)))
        elif class_of(f1) in lost:
            out.append((str(n), "parent", class_of(f1)))
    return out


def _orphan_reason(orphans, what) -> Optional[str]:
    if not orphans:
        return None
    n, role, cls = orphans[0]
    more = f" (and {len(orphans) - 1} more)" if len(orphans) > 1 else ""
    if role == "child":
        head = f"connection '{n}'{more} attaches a limb of {what} ({class_label(cls)})"
    else:
        head = f"connection '{n}'{more} is cut into a limb of {what} ({class_label(cls)}, its parent)"
    return head + " and no other limb has that class: delete or re-point its connection first"


def delete_limb_refusal(index, limb_ids, conn_names, conn_flags1, conn_flags2) -> Optional[str]:
    """Why limb ``index`` can't be deleted, or None: it is the last limb of a class a connection names as its child
    (flags2) or its parent (flags1). Another limb of the same class keeps the connection resolved, so that delete is
    allowed."""
    after = [x for i, x in enumerate(limb_ids) if i != index]
    return _orphan_reason(orphaned_connections(limb_ids, after, conn_names, conn_flags1, conn_flags2), "this class")


def class_change_refusal(index, limb_ids, new_id, conn_names, conn_flags1, conn_flags2) -> Optional[str]:
    """Why limb ``index`` can't take id ``new_id``, or None: the change would leave a connection's child or parent class
    with no limb (the same state :func:`delete_limb_refusal` refuses; give another limb the class first, or re-point
    the connection). Non-Swappable never changes the class."""
    after = list(limb_ids)
    after[index] = int(new_id)
    return _orphan_reason(orphaned_connections(limb_ids, after, conn_names, conn_flags1, conn_flags2),
                          f"class 0x{class_of(limb_ids[index]):x}")


def default_patch_target(index, limb_ids) -> int:
    """Where a deleted limb's patches go by default: the Torso (class 0x800), else the first other limb; -1 when there
    is no other limb."""
    others = [i for i in range(len(limb_ids)) if i != index]
    torso = next((i for i in others if class_of(limb_ids[i]) == 0x800), None)
    return torso if torso is not None else (others[0] if others else -1)


def limb_status(index, limb_ids, patch_count, bone_count) -> str:
    """The Limbs list's status word, the worst of the row's checks: "no bones" (the engine's model loader reports
    'limbs are boneless!' and ComboTest.exe does not take the creature), then the OE's limb checks "no class",
    "duplicate", "not allowed", "needs patches" (Torso / Head / Tail as a stub); else "stub" (0 patches, fine for the
    other classes) or "ok"."""
    from ICParse import validate as ICval
    cls = class_of(limb_ids[index])
    if not bone_count:
        return "no bones"                   # first: ComboTest.exe won't take the creature ('limbs are boneless!')
    if cls == 0:
        return "no class"
    if any(class_of(x) == cls for i, x in enumerate(limb_ids) if i != index):
        return "duplicate"
    if cls not in set(ICval.OE_CLASSES_REQUIRED) | set(ICval.OE_CLASSES_STUB_OK) | set(ICval.OE_CLASSES_OPTIONAL):
        return "not allowed"
    if not patch_count and cls in ICval.OE_CLASSES_REQUIRED:
        return "needs patches"
    return "stub" if not patch_count else "ok"


# --------------------------------------------------------------------------------------------- limb bone lists
# Which bones swap with each limb (Boner's "select the bones of each limb"). Measured on the base-game creatures
# (wf11 RULES.md 3): every non-torso limb is ONE subtree -- its root plus every descendant no other limb owns (727/728;
# scorpion's antlers are in no limb) -- and the Torso is every bone no other limb owns (60/61). A limb's bone ORDER is the
# order of the bones' vertex ranges and can't be re-derived (bones owning no points all start at 0), so an edit keeps the
# source record's order for the bones it listed and appends the others in skeleton order: re-deriving a stock limb is
# byte-identical, and moving a bone out and back restores the record. Names match ignoring case (the OE's dictionary
# lower-cases them); a list keeps the spelling it has, a new bone takes the skeleton's.
TORSO_CLASS = 0x800


def _low(name) -> str:
    return str(name).lower()


def is_torso(limb_id) -> bool:
    return class_of(limb_id) == TORSO_CLASS


def skeleton_children(parents):
    """Per bone, its children's indices in skeleton order (``parents``: index or None / -1 per bone)."""
    kids = [[] for _ in parents]
    for i, p in enumerate(parents):
        if p is not None and 0 <= int(p) < len(parents) and int(p) != i:
            kids[int(p)].append(i)
    return kids


def bone_lookup(names, bone) -> Optional[int]:
    """The skeleton index of ``bone`` (exact name first, else ignoring case), or None."""
    names = [str(n) for n in names]
    if str(bone) in names:
        return names.index(str(bone))
    low = _low(bone)
    return next((i for i, n in enumerate(names) if n.lower() == low), None)


def subtree_bones(names, parents, root, skip=()):
    """Boner's "X and its children": ``root`` plus every descendant, in skeleton order. A bone whose lower-cased name is
    in ``skip`` (bones another limb owns, other classes' standard roots) is not entered: its whole branch stays out.
    ``root`` itself is always taken. Raises ValueError for a root the skeleton lacks."""
    r = bone_lookup(names, root)
    if r is None:
        raise ValueError(f"the skeleton has no bone '{root}'")
    skip = {_low(s) for s in skip}
    kids = skeleton_children(parents)
    out, stack = [], [r]
    while stack:
        i = stack.pop()
        if i != r and _low(names[i]) in skip:
            continue
        out.append(i)
        stack.extend(kids[i])
    return [str(names[i]) for i in sorted(out)]


def merge_order(current, new_set, skeleton_names, source=None):
    """The bone list of ``new_set`` (names) for a limb whose list is ``current``: the bones the SOURCE record listed in its
    order (``source``: the record's list, None for a limb made in Blender), then the rest of ``current`` in its order, then
    the new bones in skeleton order. A set equal to the source record's gives that record's list."""
    want = {_low(b) for b in new_set}
    ref = [str(b) for b in (source or [])]
    have = {_low(b) for b in ref}
    ref += [str(b) for b in current if _low(b) not in have]
    out, seen = [], set()
    for b in ref:
        k = _low(b)
        if k in want and k not in seen:
            out.append(b)
            seen.add(k)
    for n in skeleton_names:
        k = _low(n)
        if k in want and k not in seen:
            out.append(str(n))
            seen.add(k)
    return tuple(out)


def bone_owners(limb_lists):
    """Lower-cased bone name -> the indices of the limbs listing it."""
    own = {}
    for j, bl in enumerate(limb_lists):
        for b in bl:
            ls = own.setdefault(_low(b), [])
            if j not in ls:
                ls.append(j)
    return own


def torso_remainder(skeleton_names, limb_lists, limb_ids):
    """The Torso's bones by the rule: every skeleton bone no non-torso limb lists, in skeleton order."""
    owned = {_low(b) for j, bl in enumerate(limb_lists) if not is_torso(limb_ids[j]) for b in bl}
    return [str(n) for n in skeleton_names if _low(n) not in owned]


def standard_root_skips(limb_id):
    """Lower-cased standard root bones of the OTHER classes (:data:`STANDARD_LIMBS`): Set Root Bone never sweeps one into
    this limb (piranha's arm stubs hang under neck3, a Head re-rooted there must not take them)."""
    cls = class_of(limb_id)
    return {s.root.lower() for s in STANDARD_LIMBS if class_of(s.limb_id) != cls}


def standard_root_names() -> dict:
    """Lower-cased standard limb root -> its class (``ICParse.validate.STANDARD_LIMB_ROOTS``; the Torso's pelvis is not
    one): no base-game Torso lists one."""
    from ICParse import validate as ICval
    return {v: k for k, v in ICval.STANDARD_LIMB_ROOTS.items()}


def torso_connection_hosts(connections) -> dict:
    """Lower-cased host bone -> the names of the torso-owned connections on it (``connections``: ``(name, host bone,
    flags1)``; flags1's class 0x800)."""
    out = {}
    for n, b, f1 in connections:
        if b and class_of(f1) == TORSO_CLASS:
            out.setdefault(_low(b), []).append(str(n))
    return out


def _host_refusal(verb, lists, new, limb_ids, limb_names, hosts):
    """Why an edit that takes a torso connection's host bone out of every Torso limb is refused (None when it doesn't).
    Every torso connection's host bone is a Torso bone (726/726 base game, 698/698 add-on); with it in another limb (or
    none) ComboTest.exe fails 'combinerimp.c:1949 Assertion of (Index >= 0)' when the creature is the torso."""
    if not hosts:
        return None
    def torso_set(ls):
        return {_low(b) for j, bl in enumerate(ls) if is_torso(limb_ids[j]) for b in bl}
    before, after = torso_set(lists), torso_set(new)
    gone = [k for k in hosts if k in before and k not in after]
    if not gone:
        return None
    where = {}
    for j, bl in enumerate(new):
        for b in bl:
            where.setdefault(_low(b), (str(b), limb_names[j]))
    parts = [f"{where.get(k, (k, None))[0]} (hosts {', '.join(hosts[k][:3])}{', ...' if len(hosts[k]) > 3 else ''})"
             for k in gone]
    return (f"{verb} would take {'; '.join(parts)} out of the Torso: a torso connection's host bone stays a Torso bone, or "
            "ComboTest.exe fails 'Index >= 0'")


@dataclass
class BoneListEdit:
    """A limb bone-list edit: the new list of every limb and exactly which bones moved, ``(bone, from limb, to limb)``
    with -1 for "no limb"."""
    lists: list
    moved: list
    changed: list

    def describe(self, limb_names) -> str:
        def nm(j):
            return "no limb" if j < 0 else f"'{limb_names[j]}'"
        if not self.moved:
            return "no bone moved"
        groups = {}
        for b, f, t in self.moved:
            groups.setdefault((f, t), []).append(b)
        parts = [f"{', '.join(bs)} ({nm(f)} -> {nm(t)})" for (f, t), bs in groups.items()]
        return f"moved {len(self.moved)} bone{'s' if len(self.moved) != 1 else ''}: " + "; ".join(parts)


def _finish_edit(limb_lists, new_lists):
    changed = [j for j, (a, b) in enumerate(zip(limb_lists, new_lists)) if tuple(a) != tuple(b)]
    return changed


def _boneless_refusal(j, limb_names, bones) -> str:
    return (f"limb '{limb_names[j]}' would have no bones left (it has only {', '.join(bones)}): the engine reports 'limbs "
            "are boneless!'; give it another bone first, or delete that limb")


def _check_index(index, limb_lists):
    if not 0 <= int(index) < len(limb_lists):
        raise ValueError(f"no limb {index}: the creature has {len(limb_lists)} limbs")


def set_root_edit(index, root, skeleton_names, parents, limb_lists, limb_ids, limb_names, source_lists=None,
                  hosts=None):
    """Set Root Bone: limb ``index`` becomes ``root`` plus its descendants that no other non-torso limb owns, never
    sweeping in another class's standard root it did not already list. A root another non-torso limb owns comes with
    that limb's bones below it (the other limb loses a whole branch, never splits in two). Bones it takes leave the limb
    that had them (the Torso too: the Torso is the remainder); bones it had and no longer covers go back to the Torso (the
    first Torso-class limb) unless another limb still lists them, else to no limb. Raises ValueError (the refusal) for
    the Torso itself, an unknown root, a take that would leave another limb with no bones, a drop that would put a
    standard limb root (front_leg1_l, neck2, ...) in the Torso or in no limb (no base-game Torso lists one: with this
    creature as the torso the combined skeleton gets two bones of that name), and -- ``hosts``, from
    :func:`torso_connection_hosts` -- a take of a torso connection's host bone out of the Torso."""
    _check_index(index, limb_lists)
    index = int(index)
    lists = [tuple(str(b) for b in bl) for bl in limb_lists]
    src = list(source_lists) if source_lists is not None else [None] * len(lists)
    if is_torso(limb_ids[index]):
        raise ValueError(f"'{limb_names[index]}' is the Torso: it takes every bone no other limb owns, so it has no root "
                         "to pick (use Add / Remove Active Bone, or set the other limbs' roots)")
    r = bone_lookup(skeleton_names, root)
    if r is None:
        raise ValueError(f"the skeleton has no bone '{root}'")
    own = bone_owners(lists)
    mine = {_low(b) for b in lists[index]}
    rk = _low(skeleton_names[r])
    donor = {j for j in own.get(rk, []) if j != index and not is_torso(limb_ids[j])}     # the root's non-torso owner
    skip = {k for k, ls in own.items() if any(j != index and not is_torso(limb_ids[j]) and j not in donor for j in ls)}
    skip |= standard_root_skips(limb_ids[index]) - mine
    skip.discard(_low(skeleton_names[r]))
    new_set = subtree_bones(skeleton_names, parents, skeleton_names[r], skip)
    take = {_low(b) for b in new_set}
    new = list(lists)
    new[index] = merge_order(lists[index], new_set, skeleton_names, src[index])
    moved = []
    for j, bl in enumerate(lists):
        if j == index:
            continue
        gone = [b for b in bl if _low(b) in take]
        if gone:
            kept = tuple(b for b in bl if _low(b) not in take)
            if not kept:
                raise ValueError(f"Set Root Bone '{skeleton_names[r]}' would take {', '.join(gone)}: "
                                 + _boneless_refusal(j, limb_names, bl))
            new[j] = kept
            moved += [(b, j, index) for b in gone]
    for b in new_set:
        if _low(b) not in mine and not own.get(_low(b)):
            moved.append((b, -1, index))                 # swept in from no limb
    dropped = [b for b in lists[index] if _low(b) not in take]
    if dropped:
        still = {}
        for j, bl in enumerate(new):
            if j != index:
                for b in bl:
                    still.setdefault(_low(b), j)
        torso = next((j for j in range(len(lists)) if j != index and is_torso(limb_ids[j])), -1)
        home = [b for b in dropped if _low(b) not in still]
        std = standard_root_names()
        roots = [b for b in home if _low(b) in std]
        if roots:
            dest = f"'{limb_names[torso]}'" if torso >= 0 else "no limb"
            raise ValueError(
                f"Set Root Bone '{skeleton_names[r]}' would leave {', '.join(roots)} (the standard root of "
                f"{', '.join(class_label(std[_low(b)]) for b in roots)} limbs) to {dest}: combining then gives two bones of "
                f"that name. Keep the root at {roots[0]} or above, or use Add / Remove Active Bone")
        if torso >= 0 and home:
            new[torso] = merge_order(new[torso], list(new[torso]) + home, skeleton_names, src[torso])
        moved += [(b, index, still[_low(b)] if _low(b) in still else torso) for b in dropped]
    why = _host_refusal(f"Set Root Bone '{skeleton_names[r]}'", lists, new, limb_ids, limb_names, hosts)
    if why:
        raise ValueError(why)
    return BoneListEdit(lists=new, moved=moved, changed=_finish_edit(lists, new))


def add_bone_edit(index, bone, skeleton_names, limb_lists, limb_ids, limb_names, source_lists=None, hosts=None):
    """Add Active Bone: ``bone`` joins limb ``index`` (source position when its record listed it, else appended) and
    leaves every other limb that listed it. Refused when the limb already has it, the skeleton lacks it, it is another
    limb's only bone, or (``hosts``) it is a torso connection's host bone leaving the Torso."""
    _check_index(index, limb_lists)
    index = int(index)
    lists = [tuple(str(b) for b in bl) for bl in limb_lists]
    src = list(source_lists) if source_lists is not None else [None] * len(lists)
    r = bone_lookup(skeleton_names, bone)
    if r is None:
        raise ValueError(f"the skeleton has no bone '{bone}'")
    k = _low(skeleton_names[r])
    if any(_low(b) == k for b in lists[index]):
        raise ValueError(f"'{skeleton_names[r]}' is already a bone of '{limb_names[index]}'")
    new, moved = list(lists), []
    for j, bl in enumerate(lists):
        if j != index and any(_low(b) == k for b in bl):
            kept = tuple(b for b in bl if _low(b) != k)
            if not kept:
                raise ValueError(f"Add Active Bone '{skeleton_names[r]}': " + _boneless_refusal(j, limb_names, bl))
            new[j] = kept
            moved.append((str(skeleton_names[r]), j, index))
    if not moved:
        moved.append((str(skeleton_names[r]), -1, index))
    new[index] = merge_order(lists[index], list(lists[index]) + [skeleton_names[r]], skeleton_names, src[index])
    why = _host_refusal(f"Add Active Bone '{skeleton_names[r]}'", lists, new, limb_ids, limb_names, hosts)
    if why:
        raise ValueError(why)
    return BoneListEdit(lists=new, moved=moved, changed=_finish_edit(lists, new))


def remove_bone_edit(index, bone, limb_lists, limb_names, limb_ids=None, hosts=None):
    """Remove Active Bone: ``bone`` leaves limb ``index`` and is in no limb afterwards (Validate lists it; Add Active Bone
    gives it to another limb). Refused when the limb does not list it, it is the limb's only bone, or (``limb_ids`` and
    ``hosts``) it is a torso connection's host bone leaving the Torso."""
    _check_index(index, limb_lists)
    index = int(index)
    lists = [tuple(str(b) for b in bl) for bl in limb_lists]
    k = _low(bone)
    gone = [b for b in lists[index] if _low(b) == k]
    if not gone:
        raise ValueError(f"'{bone}' is not a bone of '{limb_names[index]}'")
    kept = tuple(b for b in lists[index] if _low(b) != k)
    if not kept:
        raise ValueError("Remove Active Bone: " + _boneless_refusal(index, limb_names, lists[index]))
    new = list(lists)
    new[index] = kept
    if limb_ids is not None:
        why = _host_refusal(f"Remove Active Bone '{gone[0]}'", lists, new, limb_ids, limb_names, hosts)
        if why:
            raise ValueError(why)
    return BoneListEdit(lists=new, moved=[(gone[0], index, -1)], changed=[index])


def bone_edit_warnings(edit, before, limb_ids, limb_names, owner_counts, limb_hosts) -> list:
    """What a bone-list edit newly breaks for the combiner (the operators report these as a WARNING; Validate's STOCK
    lines say the same about the whole creature): per limb whose patches now use control points of a bone that is in the
    Torso or in no limb, and per standard limb root the Torso now lists. ``before``: the lists before ``edit``;
    ``owner_counts``: per limb, bone name -> its patches' control points that bone owns; ``limb_hosts``: per limb, its
    connections' host bones (``ICParse.validate.limb_connection_hosts``)."""
    from ICParse import validate as ICval
    def strays(ls):
        return {(j, _low(b)): (b, n) for j, bad in ICval.limb_stray_points(limb_ids, ls, owner_counts, limb_hosts)
                for b, n in bad}
    old, now = strays(before), strays(edit.lists)
    where = {}
    for j, bl in enumerate(edit.lists):
        if is_torso(limb_ids[j]):
            for b in bl:
                where.setdefault(_low(b), limb_names[j])
    out = []
    for (j, k), (b, n) in now.items():
        if (j, k) in old:
            continue
        leg = class_of(limb_ids[j]) & 0x20 and not int(limb_ids[j]) & NOSWAP
        out.append(f"{b} owns {n} control point{'s' if n != 1 else ''} of '{limb_names[j]}' and is now in "
                   + (f"'{where[k]}'" if k in where else "no limb")
                   + f": grafted as a donor, ComboTest.exe puts them on the root bone"
                   + (" and fails an assertion (no export)" if leg else "")
                   + f" (give {b} back to '{limb_names[j]}', or those points to one of its bones)")
    had = {(j, _low(b)) for j, b, _c in ICval.torso_standard_roots(limb_ids, before)}
    for j, b, cls in ICval.torso_standard_roots(limb_ids, edit.lists):
        if (j, _low(b)) not in had:
            out.append(f"'{limb_names[j]}' now lists {b}, the standard root of {class_label(cls)} limbs: with this "
                       f"creature as the torso, a grafted {class_label(cls)} limb brings a second '{_low(b)}' bone")
    return out


# --------------------------------------------------------------------------------------------- Connections sub-mode
# Boner's Connection Options (dialog 113; wf11 RULES.md 4): the same classification boxes as a limb, edited for the
# Parent (flags1: the limb the ring is cut into -- the Torso 0x800 for every limb socket, the Head 0x40 for the horse /
# owl antler rings) or the Child (flags2: the class of the limb attached through it). The loader masks NoSwap off both
# words (0xFF7FFFFF), so Non-Swappable belongs on the limb: Boner copies a limb's full id into flags2 when it is picked
# from the Child Limb list, but 22 of the 730 base-game records lack the bit their limb has, with no effect.
PARENT_TORSO = 0x800
PARENT_HEAD = 0x40
CONN_FLAGS = tuple(f for f in LIMB_FLAGS if f.bit != NOSWAP)
NOSWAP_CONN_NOTE = ("Non-Swappable is not a connection box: the loader masks it off the connection's classes, so it "
                    "belongs on the limb (Boner > Limbs)")
# the stock host bone by child class (combotest.validate.STOCK_HOSTS, used when combotest does not import)
_STOCK_HOSTS = {0x40: "neck1", 0x80: "pelvis", 0x25: "spine3", 0x26: "spine3", 0x29: "pelvis", 0x2A: "pelvis",
                0x501: "spine3", 0x502: "spine3", 0x301: "spine3", 0x302: "spine3", 0x15: "neck1", 0x16: "neck1",
                0x20021: "spine1", 0x20022: "spine1", 0x40021: "spine1", 0x40022: "spine1"}


def ring_corner_count(child_class) -> int:
    """The corners a connection's ring needs: 6 for Head / Tail (0x40 / 0x80), 4 for every other class (730/730)."""
    return 6 if class_of(child_class) in (0x40, 0x80) else 4


def toggle_conn_flag(flags, bit) -> int:
    """Boner's checkbox rule on a connection class word; Non-Swappable is refused (:data:`NOSWAP_CONN_NOTE`)."""
    if int(bit) == NOSWAP:
        raise ValueError(NOSWAP_CONN_NOTE)
    return toggle_flag(flags, bit)


def child_class_text(flags2) -> str:
    """The Child classification for a readout: its class label, and a note when the stored word carries the NoSwap bit
    (ignored on load)."""
    v = int(flags2) & 0xFFFFFFFF
    text = class_label(v & ~NOSWAP)
    return text + (" (stored with Non-Swappable, ignored on load)" if v & NOSWAP else "")


def connected_classes(conn_flags2) -> set:
    return {class_of(f) for f in conn_flags2}


def limbs_without_connection(limb_ids, conn_flags2):
    """The indices of the non-torso limbs whose class no connection attaches (Create Connection's list): every base-game
    non-torso limb has a connection with its class (the combiner never grafts a limb without one)."""
    have = connected_classes(conn_flags2)
    return [i for i, lid in enumerate(limb_ids) if class_of(lid) and not is_torso(lid) and class_of(lid) not in have]


def unmirrored_left(limb_ids):
    """The indices of the Left limbs (class bit 0x1) no limb of the Right mirror class ``(class & ~1) | 2`` pairs:
    ComboTest.exe asserts 'LimbToAdd.m_pLimb' (CombinerImp.cpp:918) combining such a creature, in either role
    (``combotest.validate.picker_refusals``; every base-game and add-on Left limb has its mirror, 595/595)."""
    have = {class_of(x) for x in limb_ids}
    return [i for i, x in enumerate(limb_ids) if class_of(x) & 1 and (class_of(x) & ~1) | 2 not in have]


SOCKETLESS_WHY = ("ComboTest.exe refuses the whole creature in either role ('Assertion of (pDesLimb->m_pLimbConn)') "
                  "until a connection carries that class again")
UNMIRRORED_WHY = "ComboTest.exe fails 'Assertion of (LimbToAdd.m_pLimb)' combining the creature, in either role"


def socket_warnings(ids_before, ids_after, f2_before, f2_after, limb_names) -> list:
    """Warnings for an edit that leaves a limb without its socket or a Left limb without its Right mirror that was not
    so before (the two ways ComboTest.exe refuses a creature; Validate's precheck reports them as COMBINE ERRORs).
    ``ids_*``: limb ids; ``f2_*``: connection Child classes; ``limb_names`` after the edit."""
    out = []
    before = {class_of(ids_before[i]) for i in limbs_without_connection(ids_before, f2_before)}
    for i in limbs_without_connection(ids_after, f2_after):
        if class_of(ids_after[i]) not in before:
            out.append(f"limb '{limb_names[i]}' ({class_label(class_of(ids_after[i]))}) has no connection now: "
                       f"{SOCKETLESS_WHY} (Create Connection, or a connection's Child class, Boner > Connections)")
    was = {class_of(ids_before[i]) for i in unmirrored_left(ids_before)}
    for i in unmirrored_left(ids_after):
        cls = class_of(ids_after[i])
        if cls not in was:
            out.append(f"limb '{limb_names[i]}' ({class_label(cls)}) has no Right partner of class 0x{(cls & ~1) | 2:x}: "
                       f"{UNMIRRORED_WHY} (re-class its partner limb the same way)")
    return out


def default_parent(child_class, child_bones, skeleton_names, parents, limb_ids, limb_bone_lists) -> int:
    """The Parent class Create Connection cuts a limb's ring into: :data:`PARENT_TORSO` when the Torso limb gives the
    connection a default host (:func:`default_host`), else :data:`PARENT_HEAD` when a Head limb does (the horse's antler
    rings: their root bones hang from ``head`` and their classes have no stock host), else Torso (refused with the
    Torso's reason). Base game + add-ons: equals every connection's Parent class whose child limb exists (1428/1428;
    a piranha's arm stubs hang from neck3, a head bone, but take the stock Torso host neck1)."""
    def lists(cls):
        return [b for lid, b in zip(limb_ids, limb_bone_lists) if class_of(lid) == cls]
    for cls in (PARENT_TORSO, PARENT_HEAD):
        pb = lists(cls)
        if pb and default_host(child_class, child_bones, pb[0], skeleton_names, parents)[0] is not None:
            return cls
    return PARENT_TORSO


def default_conn_name(conn_names, limb_name) -> str:
    """A new connection's name: its limb's name (every stock connection is named like its limb), made unique among the
    connections ignoring case (``_2``, ``_3``, ...)."""
    base = str(limb_name)[:LIMB_NAME_MAX] or "Conn"
    low = {str(n).lower() for n in conn_names}
    name, k = base, 2
    while name.lower() in low:
        tail = f"_{k}"
        name, k = base[:LIMB_NAME_MAX - len(tail)] + tail, k + 1
    return name


def limb_root_bone(limb_bones, skeleton_names, parents) -> Optional[str]:
    """The root of a limb's bone list: the first listed bone (in list order) whose skeleton parent is not listed, in the
    skeleton's spelling; None when no listed bone is in the skeleton."""
    listed = {_low(b) for b in limb_bones}
    for b in limb_bones:
        i = bone_lookup(skeleton_names, b)
        if i is None:
            continue
        p = parents[i]
        if p is None or int(p) < 0 or _low(skeleton_names[int(p)]) not in listed:
            return str(skeleton_names[i])
    return None


def stock_host(child_class) -> Optional[str]:
    """The stock host bone of a child class (``combotest.validate.STOCK_HOSTS`` by slot, else the same table here)."""
    cls = class_of(child_class)
    try:
        from combotest.slots import slot_for_flags2
        from combotest.validate import STOCK_HOSTS
        sl = slot_for_flags2(cls)
        if sl is not None and sl.key in STOCK_HOSTS:
            return STOCK_HOSTS[sl.key]
    except ImportError:
        pass
    return _STOCK_HOSTS.get(cls)


def default_host(child_class, child_bones, parent_bones, skeleton_names, parents):
    """``(host, how)`` for a connection: the parent of the child limb's root bone when the parent limb lists that bone
    (base game 718/728), else the stock host of the class when the parent limb lists it; the host is spelled as the
    parent limb's list spells it (730/730 stock hosts match it exactly). ``(None, why)`` when neither is in the parent
    limb (the Object Editor warns 'not found in parent limb' and ComboTest.exe asserts 'Index >= 0')."""
    in_parent = {_low(b): str(b) for b in parent_bones}
    root = limb_root_bone(child_bones, skeleton_names, parents)
    tried = []
    if root is not None:
        i = bone_lookup(skeleton_names, root)
        p = parents[i] if i is not None else None
        if p is not None and int(p) >= 0:
            pn = str(skeleton_names[int(p)])
            if _low(pn) in in_parent:
                return in_parent[_low(pn)], f"the parent of the limb's root bone {root}"
            tried.append(f"{pn} (the parent of its root bone {root})")
    sh = stock_host(child_class)
    if sh is not None:
        if _low(sh) in in_parent:
            return in_parent[_low(sh)], f"the stock host of {class_label(child_class)} limbs"
        tried.append(f"{sh} (the stock host)")
    return None, ("no default host bone: " + (" and ".join(tried) if tried else "the limb has no root bone and its "
                                              "class no stock host") + " is not in the parent limb's bones")


def host_refusal(host, parent_class, skeleton_names, limb_ids, limb_bone_lists) -> Optional[str]:
    """Why ``host`` can't be a connection's host bone, or None: it must be a skeleton bone listed by every limb of the
    parent class (the Object Editor's 'Parent bone ... not found in skeleton / in parent limb'; ComboTest.exe asserts
    'Index >= 0' otherwise). A parent class no limb has is not checked here (Validate reports that connection)."""
    if not str(host):
        return "no host bone given"
    if bone_lookup(skeleton_names, host) is None:
        return f"'{host}' is not a bone of this skeleton"
    for lid, bones in zip(limb_ids, limb_bone_lists):
        if class_of(lid) == class_of(parent_class) and _low(host) not in {_low(b) for b in bones}:
            return (f"'{host}' is not in the bones of the parent limb ({class_label(parent_class)}): a host bone is a bone "
                    "of the limb the ring is cut into (the Object Editor warns, ComboTest.exe asserts 'Index >= 0')")
    return None


def class_change_plan(index, limb_ids, new_id, conn_names, conn_flags1, conn_flags2, limb_name=None):
    """``(refusal, [(connection index, new flags2)])`` for giving limb ``index`` the id ``new_id``. The limb's own
    connection -- the only connection attaching the old class, or else the one named like the limb (every stock
    connection is named like its limb) -- takes the new class as its Child class (its stored NoSwap bit kept) in the same
    edit, so re-classing a stock limb keeps it connected, and ticking a box and ticking it back is the identity even
    when the first step made a duplicate class. Refused, writing nothing, when the change would still leave a connection
    without its limb: the limb is a connection's Parent (the Torso, a head with antler rings), the new class is empty
    (the 'untick, then Delete' bypass), or several connections carry the orphaned class."""
    old, new = class_of(limb_ids[index]), class_of(new_id)
    if old == new:
        return None, []
    after = list(limb_ids)
    after[index] = int(new_id)
    orphans = orphaned_connections(limb_ids, after, conn_names, conn_flags1, conn_flags2)
    parents = [o for o in orphans if o[1] == "parent"]
    child = [i for i, f2 in enumerate(conn_flags2) if class_of(f2) == old]
    named = [i for i in child if limb_name is not None and str(conn_names[i]).lower() == str(limb_name).lower()]
    follow = None
    if not parents and new:
        if orphans and len(child) == 1:
            follow = child[0]
        elif not orphans and len(named) == 1:
            follow = named[0]
    if follow is not None:
        return None, [(follow, (int(conn_flags2[follow]) & NOSWAP) | new)]
    if not orphans:
        return None, []
    why = _orphan_reason(orphans, f"class 0x{old:x}")
    if not new and not parents and len(child) == 1:
        why += (", or tick another box instead (a connection's Child class follows its limb's class, but not to no "
                "class)")
    elif len(child) > 1 and not parents:
        why += f" ({len(child)} connections carry that class, so none can follow the limb; or give another limb the class)"
    else:
        why += " (or give another limb the class first)"
    return why, []


def conn_status(index, conn_rows, limb_ids, limb_bone_lists, skeleton_names) -> str:
    """The Connections list's status word, the worst first: "no ring", "no child limb", "no parent limb", "no host",
    "host not in parent", "duplicate" (another connection has the same Child class), else "ok". ``conn_rows``:
    ``(name, host, n corners, flags1, flags2)`` per connection."""
    _name, host, corners, f1, f2 = conn_rows[index]
    classes = {class_of(x) for x in limb_ids}
    if not corners:
        return "no ring"
    if not class_of(f2) or class_of(f2) not in classes:
        return "no child limb"
    if not class_of(f1) or class_of(f1) not in classes:
        return "no parent limb"
    if not host or bone_lookup(skeleton_names, host) is None:
        return "no host"
    if host_refusal(host, f1, skeleton_names, limb_ids, limb_bone_lists):
        return "host not in parent"
    if any(class_of(r[4]) == class_of(f2) for j, r in enumerate(conn_rows) if j != index):
        return "duplicate"
    return "ok"


# --------------------------------------------------------------------------------------------- Add Stub Socket
# Relic's stub bones (IC_Boner_Documentation: "stub bones ... must be defined in the limbing and connection sections";
# wf11 RULES.md 2.2 / 3 / 4.4 / 5.1): a creature that models no wings or arms still carries a limb and a socket for each
# of the Object Editor's required-but-stub-able classes, so it can take those parts from other creatures. Measured on
# the 61 base-game creatures: a stub limb has 0 patches and exactly 1 bone, which owns no control points (61/61); its
# ring is the 4-corner loop of ONE patch of the parent limb (358 torso + 4 head, 362/362), wound into the torso and
# started like the stock partners; a stub side wing and a stub top wing of one side share the same torso quad (54/54);
# a stub quad is never also a patched limb's ring. The exporter (Boner.dlm FUN_10002148) sets such a bone's BONE flags
# low byte to 1, writes it after every other bone and gives it no clip track.
STUB_SLOTS = tuple(s for s in STANDARD_LIMBS if s.need == "stub")
STUB_BY_SUFFIX = {s.suffix: s for s in STUB_SLOTS}
# the side / top wing of one side share one torso quad when both are stubs (54/54 base game, 46/46 add-on)
WING_PARTNER = {0x501: 0x301, 0x301: 0x501, 0x502: 0x302, 0x302: 0x502}
STUB_BYTE = 0x01
STUB_TAIL_FRACTION = 0.25       # a created stub bone's (display) length: this share of the patch's corner distance
_BEZ = np.array([1.0, 3.0, 3.0, 1.0]) / 8.0


def stub_classes_missing(limb_ids, conn_flags2):
    """The stub-able standard slots (:data:`STUB_SLOTS`, stock order) no limb and no connection has the class of: what Add
    Stub Socket offers."""
    have = {class_of(x) for x in limb_ids} | {class_of(f) for f in conn_flags2}
    return [s for s in STUB_SLOTS if class_of(s.limb_id) not in have]


def stub_quad(patch_cps):
    """The 4-corner loop of a patch (control points 0, 3, 15, 12: once around the row-major 4x4 grid)."""
    c = [int(x) for x in patch_cps]
    return [c[0], c[3], c[15], c[12]]


def patch_centre(cps16_xyz) -> np.ndarray:
    """The surface point of a bicubic Bezier patch at (u, v) = (0.5, 0.5) from its 16 control points (row-major 4x4,
    any axes)."""
    P = np.asarray(cps16_xyz, dtype=np.float64).reshape(4, 4, 3)
    return np.einsum("i,j,ijk->k", _BEZ, _BEZ, P)


def stub_patch_refusal(selected_patches) -> Optional[str]:
    """Why a selection can't give Add Stub Socket its patch (it needs exactly one: all 4 corners of one torso patch
    selected), or None."""
    n = len(list(selected_patches))
    if n == 0:
        return ("no patch is selected: select the 4 corners of the torso patch the socket goes on (Edit Mode; a stub "
                "socket is the ring of one torso patch)")
    if n > 1:
        return (f"{n} patches are selected (all 4 corners of each): select the corners of exactly one torso patch (a stub "
                "socket is the ring of one patch)")
    return None


@dataclass(frozen=True)
class StubConn:
    """An existing connection as :func:`stub_plan` sees it: its classes, its ring (a corner loop, or verbatim ints) and
    whether a limb with patches carries its Child class."""
    name: str
    flags1: int
    flags2: int
    ring: tuple = ()
    child_patched: bool = False


@dataclass(frozen=True)
class StubPlan:
    """What Add Stub Socket creates: the limb row (``limb_name``, ``limb_id``, bone ``root``, no patches), the connection
    row (``conn_name``, Parent ``parent_class``, Child the limb's class, host ``host``, ring ``quad`` before the stock
    winding / start fix) and, with ``create``, a new leaf bone ``root`` under ``host`` at the patch centre."""
    suffix: str
    limb_id: int
    limb_name: str
    conn_name: str
    patch: int
    quad: tuple
    root: str
    create: bool
    host: str
    host_how: str
    parent_class: int = TORSO_CLASS
    shares: Optional[str] = None                # the wing partner's connection whose quad this socket shares


def stub_plan(suffix, patch, patches, patch_limb, limb_ids, limb_names, limb_bones, conns, skeleton_names, parents,
              point_counts, creature=""):
    """``(refusal, StubPlan)`` -- exactly one is None -- for Add Stub Socket of the stub-able slot ``suffix`` on patch
    ``patch``.

    ``patches``: (Np, 16) pool indices; ``patch_limb``: row index per patch; ``limb_ids`` / ``limb_names`` /
    ``limb_bones``: the limb rows and their effective bone lists; ``conns``: :class:`StubConn` per connection;
    ``skeleton_names`` / ``parents``: the export skeleton (index or -1); ``point_counts``: lower-cased bone name -> the
    control points it owns (the live skin).

    Refused: a slot that is not stub-able or already has a limb or a connection; a patch outside the table or not in the
    Torso; a quad a connection already uses (a limb with patches grafts there, or another stub socket -- only a side and
    top wing stub of one side share a quad); a standard root bone that another limb owns or that owns control points;
    no host bone in the Torso. The root bone is the skeleton's bone of the standard name (:data:`STANDARD_LIMBS`) when it
    is free, else a new bone of that name (``create``); the host is the parent of an existing root when the Torso lists it,
    else the class's stock host (``default_host``), and a created root hangs from it."""
    std = STUB_BY_SUFFIX.get(str(suffix))
    if std is None:
        return (f"'{suffix}' is not a stub-able limb class (Add Stub Socket makes "
                f"{', '.join(s.suffix for s in STUB_SLOTS)})"), None
    cls = class_of(std.limb_id)
    label = class_label(std.limb_id)
    have = next((n for n, i in zip(limb_names, limb_ids) if class_of(i) == cls), None)
    if have is not None:
        return (f"limb '{have}' already has the {label} class (Add Stub Socket adds a class the creature has no limb "
                "of; Create Connection gives an existing limb its socket)"), None
    conn = next((c for c in conns if class_of(c.flags2) == cls), None)
    if conn is not None:
        return (f"connection '{conn.name}' already attaches {label} limbs, but no limb has that class: delete that "
                "connection first, or give a limb the class (Boner > Limbs)"), None
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    patch = int(patch)
    if not 0 <= patch < len(P):
        return f"patch {patch} is not in the {len(P)}-patch table", None
    pl = np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    owner = int(pl[patch]) if patch < len(pl) else -1
    torso = [j for j, i in enumerate(limb_ids) if is_torso(i)]
    if not torso:
        return "the creature has no Torso limb: a stub socket is cut into the Torso", None
    if owner not in torso:
        where = f"limb '{limb_names[owner]}'" if 0 <= owner < len(limb_names) else "no limb"
        return (f"patch {patch} is in {where}, not the Torso: a stub socket is the ring of one Torso patch (every "
                "base-game stub ring is)"), None
    quad = stub_quad(P[patch])
    if len(set(quad)) != 4:
        return f"patch {patch} has only {len(set(quad))} distinct corners: a stub ring needs 4", None
    shares = None
    for c in conns:
        if set(int(x) for x in c.ring) != set(quad):
            continue
        partner = WING_PARTNER.get(cls)
        if partner is not None and class_of(c.flags2) == partner and not c.child_patched:
            shares = c.name
            continue
        why = ("a limb with patches grafts there" if c.child_patched else
               "another stub socket; only a side and a top wing stub of one side share a quad")
        return (f"patch {patch}'s corners are already the ring of connection '{c.name}' ({class_label(c.flags2)}): "
                f"{why} (select another torso patch)"), None
    names = [str(n) for n in skeleton_names]
    owners = bone_owners(limb_bones)
    ri = bone_lookup(names, std.root)
    torso_bones = limb_bones[owner]
    if ri is not None:
        root = names[ri]
        own = owners.get(_low(root))
        if own:
            return (f"bone '{root}', the {label} limb's root, already belongs to limb '{limb_names[own[0]]}': take it out "
                    "of that limb first (Boner > Limbs), or delete that limb; a stub bone is in no other limb"), None
        k = int(point_counts.get(_low(root), 0))
        if k:
            return (f"bone '{root}', the {label} limb's root, owns {k} control point{'s' if k != 1 else ''}: a stub "
                    "bone owns none (Boner > Bones: skin those points to another bone first)"), None
        create = False
    else:
        root, create = std.root, True
    host, how = default_host(cls, [root], torso_bones, names, parents)
    if host is None:
        return f"no host bone for the {label} socket: {how}", None
    limb_name = default_limb_name(limb_names, creature, std.suffix)
    why = limb_name_error(limb_name, limb_names)
    if why:
        return why, None
    conn_name = default_conn_name([c.name for c in conns], limb_name)
    return None, StubPlan(suffix=std.suffix, limb_id=int(std.limb_id), limb_name=limb_name, conn_name=conn_name,
                          patch=patch, quad=tuple(quad), root=root, create=create, host=host,
                          host_how=("a new bone under it: " if create else "") + how, shares=shares)


def stub_bone_world(host_world, centre) -> np.ndarray:
    """A created stub bone's rest (4x4, the axes of ``host_world``): the host bone's orientation at ``centre`` -- its
    bind-local rotation is then the identity."""
    W = np.array(np.asarray(host_world, dtype=np.float64).reshape(4, 4))
    W[:3, 3] = np.asarray(centre, dtype=np.float64).reshape(3)
    return W


def stub_tail_length(quad_xyz, centre) -> float:
    """A created stub bone's display length: :data:`STUB_TAIL_FRACTION` of the mean corner distance from the centre."""
    X = np.asarray(quad_xyz, dtype=np.float64).reshape(-1, 3)
    d = float(np.linalg.norm(X - np.asarray(centre, dtype=np.float64).reshape(1, 3), axis=1).mean()) if len(X) else 0.0
    return max(STUB_TAIL_FRACTION * d, 1e-3)


# ------------------------------------------------------------------------------------------------ bone names
# Bone names the game binaries look up by string (``strings`` of IC.exe / ComboTest.exe / ObjectEditor.exe and the
# combiner's Lua; wf11 RULES §5.2-5.3). Renaming one is allowed (Relic's rule "do not change any bone names" is advisory
# in the tools) but silently loses what the lookup did in game, so Validate and Export warn, naming it.
BONE_NAME_MAX = 63
_SIDES = ("_l", "_r")
PROC_ANIM_BONES = ("tail1", "tail2", "tail3", "tail4", "endtail", "neck1", "neck2", "neck3", "head", "spine1", "spine2",
                   "spine3")
# names ComboTest.exe looks up (strings in the exe). Only some have a measured effect on an export: the canonical
# bones (combotest.validate CANONICAL_BONES: pelvis drop, sockets, seam re-skin). The rest -- renamed on a creature
# combined in both roles (cheetah Front_ankle_l -> front_hock_l: bull + its front legs, it + bull head) -- changed
# nothing in the exports, so their warning says so instead of naming an effect nobody has seen
COMBINER_BONES = ("pelvis", "spine1", "spine2", "spine3", "neck1", "tail1")
COMBINER_UNOBSERVED_BONES = (tuple(f"{b}{s}" for b in ("front_leg1", "rear_leg1", "front_arm1", "front_leg2",
                                                         "front_leg3", "front_ankle", "front_foot", "rear_leg2",
                                                         "rear_leg3", "rear_ankle", "rear_foot") for s in _SIDES)
                             + ("endtop_wing_r", "endwing_r"))
TAIL_CLEARANCE_BONES = ("pelvis", "tail1")
SCALING_BONES = (("spine1", "spine2", "spine3", "pelvis", "tail1", "jaw1")
                 + tuple(f"front_arm{k}{s}" for k in range(1, 6) for s in _SIDES)
                 + tuple(f"{p}_leg{k}{s}" for p in ("front", "mid", "rear") for k in range(1, 4) for s in _SIDES))
# the bones owning the stock ring corners: the combiner re-skins merged seam vertices by (lower-case) bone name
SEAM_BONES = ("neck1", "tail1", "pelvis", "spine2", "spine3", "front_leg1_l", "front_leg1_r")
UNIVERSAL_BONES = ("pelvis", "spine1", "spine2", "spine3", "neck1", "neck2", "neck3", "head", "jaw1", "endjaw", "nose",
                   "tail1", "tail2", "tail3", "tail4", "endtail", "rear_leg1_l", "rear_leg1_r")
HARD_CODED_USES = (
    (PROC_ANIM_BONES, "procedural animation (IC.exe ProcAnim: swimming and flying adjustments)"),
    (TAIL_CLEARANCE_BONES, "tail ground clearance"),
    (COMBINER_BONES, "the combiner's lookups (pelvis drop, sockets, seams)"),
    (COMBINER_UNOBSERVED_BONES, "a name ComboTest.exe looks up (no effect observed in its exports)"),
    (SEAM_BONES, "seam skinning (the combiner re-skins seam vertices by bone name: a design replacing the limb "
                 "whose bone this was tears the seam, vertices on the root bone)"),
    (SCALING_BONES, "creature scaling (CreatureScalingInfo ShrunkenBoneList)"),
    (UNIVERSAL_BONES, "its place among the 18 bones every base-game creature has (Validate's STOCK bone line)"),
)


# what a rename away from the name was MEASURED to do in the game's tools (the generic uses above say where it is read)
MEASURED_LOSS = {
    "tail1": ("measured in ComboTest.exe: with another creature's tail the tail seam tears, and with a kept tail the "
              "combined creature loses its TailWag clip and tail ground clearance"),
    "tail2": ("measured in ComboTest.exe (cheetah tail2 renamed, as the torso + bull head): the combined creature loses "
              "its TailWag procedural clip"),
}


def hard_coded_uses(name) -> list:
    """What the game does with a bone called ``name`` (ignoring case), or [] for a name nothing looks up."""
    low = str(name).lower()
    return [why for names, why in HARD_CODED_USES if low in names]


def bone_rename_warnings(renames) -> list:
    """Warnings for ``[(old name, new name)]`` bone renames that touch a name the game looks up: the old name loses what
    the lookup did, a new name gains it (a bone of another role now treated as that one)."""
    out = []
    for old, new in renames:
        if str(old).lower() == str(new).lower():
            continue
        lost, got = hard_coded_uses(old), hard_coded_uses(new)
        if lost:
            seen = MEASURED_LOSS.get(str(old).lower())
            out.append(f"bone '{old}' renamed to '{new}': the game looks '{old}' up by name, so this creature loses "
                       f"{'; '.join(lost)}" + (f" -- {seen}" if seen else ""))
        if got:
            out.append(f"bone '{old}' renamed to '{new}': the game treats a bone called '{new}' specially "
                       f"({'; '.join(got)})")
    return out


def bone_add_warnings(names) -> list:
    """Warnings for added bones called a name the game looks up (ignoring case): the new bone is treated as that one."""
    out = []
    for nm in names:
        got = hard_coded_uses(nm)
        if got:
            out.append(f"added bone '{nm}': the game treats a bone called '{nm}' specially ({'; '.join(got)})")
    return out


def bone_name_error(name, others) -> Optional[str]:
    """Why ``name`` can't name a bone (Rename Bone), or None: not empty, no leading / trailing space, printable ASCII, at
    most 63 characters, and no other bone (``others``) called that ignoring case."""
    return limb_name_error(name, others, what="bone")
