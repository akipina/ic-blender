"""Structure rows, bpy-free: the creature's limb and connection tables as the add-on edits them, applied to the export
model.

The export stays "source + edits" (``core.build_export``). A limb is an identity-keyed row, :class:`LimbRow`: ``src`` is
the index of the source LIMB record it came from (-1 = a limb made in Blender), and ``bones`` None means "the source
record's bone list". :func:`apply_limbs` builds ``m.limbs`` in row order on the export model -- the parsed source's
PRIVATE copy -- and keeps that copy's record object (so its bytes) when name, id, bone list and patch set all equal the
source's; any other row becomes a new ICParse ``Limb`` value. The parsed source itself is never reused or changed.
ICParse's ``write_sgm`` writes the records: there is no second writer. Connections work the same way
(:func:`apply_connections`, rows are ``core.RingEdit``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import re
import numpy as np


@dataclass(frozen=True)
class LimbRow:
    src: int                            # source LIMB record index, -1 = new
    name: str
    limb_id: int                        # u32
    bones: Optional[Tuple[str, ...]] = None   # None = the source record's list (a new row needs a tuple)


def source_rows(model) -> List[LimbRow]:
    """The rows of an untouched creature: one per source record, bones None."""
    return [LimbRow(i, str(l.name), int(l.limb_id) & 0xFFFFFFFF, None) for i, l in enumerate(model.limbs)]


def row_bones(row: LimbRow, src) -> List[str]:
    """The bone names a row writes (its own list, else its source record's)."""
    if row.bones is not None:
        return [str(b) for b in row.bones]
    return [str(b) for b in src.limbs[row.src].bone_names] if row.src >= 0 else []


def check_rows(rows, src) -> None:
    """Refuse rows that can't be written: an unknown or repeated source index, a new row without a bone list."""
    seen = set()
    for i, r in enumerate(rows):
        if r.src >= 0:
            if r.src >= len(src.limbs):
                raise ValueError(f"limb row {i} ('{r.name}') comes from source limb {r.src}, but the source has "
                                 f"{len(src.limbs)} limbs: re-import the creature")
            if r.src in seen:
                raise ValueError(f"limb rows {i} ('{r.name}') and an earlier row both come from source limb {r.src}: "
                                 "re-import the creature")
            seen.add(r.src)
        elif r.src != -1:
            raise ValueError(f"limb row {i} ('{r.name}') has source index {r.src} (-1 means a new limb)")
        elif r.bones is None:
            raise ValueError(f"limb row {i} ('{r.name}') is new but has no bone list")
        if not 0 <= int(r.limb_id) <= 0xFFFFFFFF:
            raise ValueError(f"limb '{r.name}': id {r.limb_id} is not a u32")


def patch_limb_of_rows(src, rows) -> np.ndarray:
    """The source's patch membership expressed in row indices (-1 where the patch's source limb has no row)."""
    by_src = {r.src: i for i, r in enumerate(rows) if r.src >= 0}
    pl = np.full(len(src.patches), -1, dtype=np.int64)
    for li, limb in enumerate(src.limbs):
        for p in limb.prims:
            if 0 <= int(p) < len(pl):
                pl[int(p)] = by_src.get(li, -1)
    return pl


def apply_limbs(m, src, rows, patch_limb, n_patches=None) -> List[str]:
    """Build ``m.limbs`` from ``rows`` (None = the source's rows) and ``patch_limb`` (patch -> row index, -1 = no limb;
    None = the source's membership). ``m`` is the export model: a private copy whose limbs are still index-aligned with
    ``src.limbs``. Returns warnings (limbs with no bones, patches in no limb). Raises ValueError for rows that can't be written or a patch
    naming a row that does not exist."""
    from ICParse.sgm.creature.model import Limb
    rows = source_rows(src) if rows is None else list(rows)
    check_rows(rows, src)
    npat = len(m.patches) if n_patches is None else int(n_patches)
    pl = patch_limb_of_rows(src, rows) if patch_limb is None else np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    if len(pl) != npat:
        raise ValueError(f"limbs: {len(pl)} entries for {npat} patches")
    if len(pl) and (int(pl.max()) >= len(rows) or int(pl.min()) < -1):
        bad = int(pl.max()) if int(pl.max()) >= len(rows) else int(pl.min())
        raise ValueError(f"a patch is in limb {bad}, but the creature has {len(rows)} limbs: re-import the creature")
    copies = list(m.limbs)
    if len(copies) != len(src.limbs):
        raise ValueError("the export model's limbs are not the source's (structure applied twice?)")
    out = []
    for i, r in enumerate(rows):
        prims = [int(p) for p in np.nonzero(pl == i)[0]]
        bones = row_bones(r, src)
        if r.src >= 0:
            s, base = src.limbs[r.src], copies[r.src]
            same_prims = set(prims) == set(int(x) for x in s.prims)
            if (str(r.name) == s.name and int(r.limb_id) == int(s.limb_id) & 0xFFFFFFFF
                    and bones == list(s.bone_names) and same_prims):
                out.append(base)                        # the private copy's record: its bytes
                continue
            out.append(Limb(name=str(r.name), limb_id=int(r.limb_id), prims=list(base.prims) if same_prims else prims,
                            bone_names=bones))
        else:
            out.append(Limb(name=str(r.name), limb_id=int(r.limb_id), prims=prims, bone_names=bones))
    m.limbs = out
    warns = []
    boneless = [l.name for l in out if not l.bone_names]
    if boneless:
        warns.append(f"limb{'s' if len(boneless) != 1 else ''} "
                     f"{', '.join(boneless[:4])}{', ...' if len(boneless) > 4 else ''} "
                     f"{'have' if len(boneless) != 1 else 'has'} no bones: the engine reports 'limbs are boneless!' and "
                     "ComboTest.exe rejects it")
    orphans = int((pl < 0).sum())
    if orphans:
        warns.append(f"{orphans} patch{'es are' if orphans != 1 else ' is'} in no limb: the combiner and the Object "
                     "Editor expect every patch in exactly one limb (Boner > Limbs > Assign Selected to Limb)")
    return warns


def limb_changes(src, model, limb_src, unrename=None) -> List[str]:
    """What the export changed in the limb table, by identity (``limb_src``: source index per exported limb, -1 new):
    renamed, re-classed, bone lists, patches, added, deleted -- readable phrases, empty when nothing changed.
    ``unrename``: maps an exported bone name back to its source name (a bone rename is not a bone-list edit)."""
    back = unrename or (lambda x: str(x))
    out = []
    ren, cls, bones, pats = [], [], [], []
    for l, si in zip(model.limbs, limb_src):
        if si < 0:
            continue
        s = src.limbs[si]
        if l.name != s.name:
            ren.append(f"{s.name} -> {l.name}")
        if int(l.limb_id) != int(s.limb_id):
            cls.append(f"{l.name} 0x{int(s.limb_id):x} -> 0x{int(l.limb_id):x}")
        if [back(b) for b in l.bone_names] != [str(b) for b in s.bone_names]:
            bones.append(l.name)
        if set(l.prims) != set(s.prims):
            pats.append(l.name)
    added = [l.name for l, si in zip(model.limbs, limb_src) if si < 0]
    deleted = [src.limbs[i].name for i in range(len(src.limbs)) if i not in set(limb_src)]
    moved = [i for i, si in enumerate(limb_src) if si >= 0]
    order = [limb_src[i] for i in moved] != sorted(limb_src[i] for i in moved)

    def few(xs):
        return ", ".join(xs[:3]) + (", ..." if len(xs) > 3 else "")
    for word, xs in (("renamed", ren), ("class", cls), ("bones", bones), ("patches", pats), ("added", added),
                     ("deleted", deleted)):
        if xs:
            out.append(f"{word} {len(xs)} ({few(xs)})")
    if order:
        out.append("order changed")
    return out


# ------------------------------------------------------------------------------------------------ connections
# A connection row is ``core.RingEdit`` (duck-typed here: ``source()``, ``ints()``, ``name``, ``flags1``, ``flags2``,
# ``bone``): ``src`` the source LCON record (-1 = made in Blender), None fields = the source record's.
NAME_MAX = 63                           # the OE reader copies 0x3f bytes (strncpy); names are ASCII in the file


def _name_problem(name) -> "str | None":
    name = str(name)
    if not name.strip():
        return "is empty"
    if any(ord(ch) > 126 or ord(ch) < 32 for ch in name):
        return "has characters outside plain ASCII"
    if len(name) > NAME_MAX:
        return f"is {len(name)} characters (the Object Editor keeps {NAME_MAX})"
    return None


def apply_connections(m, src, rows, o2n=None) -> Tuple[List[int], List[str]]:
    """Build ``m.limb_connections`` from connection ``rows`` in row order (None = keep the export model's records).
    ``m`` is the export model -- the parsed source's private copy, its records still index-aligned with
    ``src.limb_connections`` (in the grown path already remapped by ``o2n``); ``o2n`` remaps a row's pool indices to
    file indices. A source row whose name, classes, ring and host all equal the copy's keeps the copy's record object
    (its bytes); any other row becomes a new ICParse ``LimbConnection`` (the writer emits word0 / word1 = 0 / 0,
    Boner's start edge and slot). The parsed source is never reused or changed.

    Returns (source index per exported connection, warnings). Raises ValueError for rows that can't be written: an
    unknown or repeated source index, a new row without a name or classes, a ring index out of the pool, a changed or
    new host bone that is not in the skeleton, a changed or new name the file can't hold."""
    from ICParse.sgm.creature.model import LimbConnection
    copies = list(m.limb_connections)
    if len(copies) != len(src.limb_connections):
        raise ValueError("the export model's connections are not the source's (structure applied twice?)")
    if rows is None:
        return list(range(len(copies))), []
    rows = list(rows)
    n_pool = len(o2n) if o2n is not None else len(m.vertices)
    skeleton = {str(b.name) for b in m.bones}
    out, srcs, warns, seen = [], [], [], set()
    for i, r in enumerate(rows):
        s = int(r.source())
        base = None
        if s >= 0:
            if s >= len(copies):
                raise ValueError(f"connection row {i} comes from source connection {s}, but the source has "
                                 f"{len(copies)} connections: re-import the creature")
            if s in seen:
                raise ValueError(f"connection rows {i} and an earlier row both come from source connection {s}: "
                                 "re-import the creature")
            seen.add(s)
            base = copies[s]
        elif s != -1:
            raise ValueError(f"connection row {i} has source index {s} (-1 means a new connection)")
        elif r.name is None or r.flags1 is None or r.flags2 is None:
            raise ValueError(f"connection row {i} is new but has no name or classification")
        name = str(base.name if r.name is None else r.name)
        f1 = int(base.flags1 if r.flags1 is None else r.flags1) & 0xFFFFFFFF
        f2 = int(base.flags2 if r.flags2 is None else r.flags2) & 0xFFFFFFFF
        ints = [int(v) for v in r.ints()]
        if any(not 0 <= v < n_pool for v in ints):
            raise ValueError(f"connection {name}: ring index out of the {n_pool}-point pool")
        if o2n is not None:
            ints = [int(o2n[v]) for v in ints]
        bone = str(r.bone)
        if base is not None and (name == base.name and f1 == int(base.flags1) & 0xFFFFFFFF
                                 and f2 == int(base.flags2) & 0xFFFFFFFF and ints == list(base.vertex_indices)
                                 and bone == base.bone_name):
            out.append(base)                                # the private copy's record: its bytes
        else:
            if (base is None or bone != base.bone_name) and bone not in skeleton:
                raise ValueError(f"connection {name}: host bone {bone!r} is not in the skeleton (a bone renamed since? "
                                 "the engine asserts on it): set the connection bone to a bone of the creature")
            if base is None or name != base.name:
                why = _name_problem(name)
                if why:
                    raise ValueError(f"connection name '{name}' {why}: rename it (Boner > Connections > Rename)")
            out.append(LimbConnection(name=name, flags1=f1, flags2=f2, vertex_indices=ints, bone_name=bone,
                                      zero_words=base.zero_words if base is not None else 2))
        if not ints:
            warns.append(f"connection '{name}' has no ring: pick one (Boner > Connections > Pick Ring); until then the "
                         "Object Editor warns about it and no limb grafts there")
        srcs.append(s)
    m.limb_connections = out
    return srcs, warns


def connection_changes(src, model, conn_src, o2n=None, unrename=None) -> Tuple[List[str], List[str]]:
    """What the export changed in the connection table, by identity (``conn_src``: source index per exported connection,
    -1 new): ``(rings, connections)`` -- ``rings`` per source record whose ring or host bone changed ("Head ring+bone"),
    ``connections`` the readable phrases for renamed, classification, added, deleted and order; both empty when nothing
    changed. ``unrename``: maps an exported host name back to its source name (a bone rename is not a host change)."""
    rings, ren, cls = [], [], []
    for c, si in zip(model.limb_connections, conn_src):
        if si < 0:
            continue
        s = src.limb_connections[si]
        ints = [int(o2n[x]) for x in s.vertex_indices] if o2n is not None else list(s.vertex_indices)
        host = unrename(c.bone_name) if unrename is not None else c.bone_name
        what = [w for w, d in (("ring", ints != list(c.vertex_indices)), ("bone", s.bone_name != host)) if d]
        if what:
            rings.append(f"{c.name} {'+'.join(what)}")
        if c.name != s.name:
            ren.append(f"{s.name} -> {c.name}")
        for word, a, b in (("parent", s.flags1, c.flags1), ("child", s.flags2, c.flags2)):
            if int(a) != int(b):
                cls.append(f"{c.name} {word} 0x{int(a):x} -> 0x{int(b):x}")
    added = [c.name for c, si in zip(model.limb_connections, conn_src) if si < 0]
    kept = set(int(x) for x in conn_src)
    deleted = [src.limb_connections[i].name for i in range(len(src.limb_connections)) if i not in kept]
    moved = [int(si) for si in conn_src if si >= 0]
    out = []

    def few(xs):
        return ", ".join(xs[:3]) + (", ..." if len(xs) > 3 else "")
    for word, xs in (("renamed", ren), ("class", cls), ("added", added), ("deleted", deleted)):
        if xs:
            out.append(f"{word} {len(xs)} ({few(xs)})")
    if moved != sorted(moved):
        out.append("order changed")
    return rings, out


def apply_structure(m, src, st, patch_limb, n_patches=None) -> Tuple[List[int], List[str]]:
    """The structure edits of an :class:`core.EditState` applied to the export model (limbs today; connections, bones and
    IK chains join here). Returns (source limb index per exported limb, warnings)."""
    rows = source_rows(src) if st.limbs is None else list(st.limbs)
    warns = apply_limbs(m, src, rows, patch_limb, n_patches)
    return [int(r.src) for r in rows], warns


# ------------------------------------------------------------------------------------------------ stub bones
# Add Stub Socket may CREATE its stub limb's bone (the only bone the export adds for now): a leaf under a source bone,
# owning no points. Boner's writer (Boner.dlm FUN_10002148) gives every bone of a limb with no patches the BONE flags
# low byte 1, writes those bones after all others and no clip track for them (61/61 base-game creatures).
STUB_BYTE = 0x01


@dataclass(frozen=True)
class NewBone:
    """A bone made in Blender for a stub limb: ``name`` (its current name); ``parent`` the file name of its (source)
    parent bone; ``world`` its rest as 16 floats, a row-major 4x4 in SGM axes (the bind world); ``stamp`` the name it
    was created with (what its limb row lists) when it was renamed since."""
    name: str
    parent: str
    world: Tuple[float, ...]
    stamp: Optional[str] = None         # the name Add Stub Socket created it with (its limb row's), when renamed since


def _flag_stub(b) -> bool:
    if (int(b.meta0) & 0xFF) == STUB_BYTE:
        return False
    b.meta0 = (int(b.meta0) & 0xFFFFFF00) | STUB_BYTE
    return True


def apply_stub_bones(m, limb_src, new_bones) -> Tuple[List[str], List[str], List[str]]:
    """The stub bones of the export model ``m`` (limbs, connections and points already built; ``limb_src`` the source
    limb index per exported limb, -1 = new).

    * Each :class:`NewBone` a limb lists is appended after every other bone (stub bones sort last), in the order the
      limbs list them (the rig's own bone order changes with every Edit Mode round trip): bind-local transform
      from its world relative to its parent's bind world in ``m`` (rest edits included), no vertex range (start 0, count
      0, aux 0), zero joint limits in the parent record's layout, flags word 1 (the stub byte; the engine keeps only the
      low byte, and 0x00000001 is the most common base-game stub word) and the parent's display colour. ``skel_info`` follows the bone count when it equalled it. A NewBone no limb lists is not written (a
      warning says so: Undo of Add Stub Socket in Edit Mode leaves it on the rig).
    * Every bone owning no points of a NEW limb row with no patches gets the stub byte (Boner's writer; stock bones
      already have it, so their records keep their bytes). A bone that owns points keeps its flags: it is not a stub
      bone (Validate's STOCK 'owns control points' line reports it).
    * A bone a limb WITH patches lists loses the stub byte again (a stub limb re-classified into a real one, or given
      patches): measured on the 61 base-game creatures, the 360 bones carrying the byte are all in limbs with no
      patches and the 2,709 bones of limbs with patches all have low byte 0, so the byte and "is a stub" are the same
      fact. Leaving it behind kept such a bone a stub bone for the add rule, which refuses adding a bone under one.

    Returns ``(added bone names, bones whose stub byte was set, warnings)`` -- a new stub limb naming a bone the skeleton
    lacks is a warning. Raises ValueError for a new bone whose name the skeleton already has (ignoring case) or whose
    parent is not in it."""
    from ICParse.codecs import reconstruct as R
    from ICParse.sgm.creature.model import Bone
    listed = {}
    for l in m.limbs:
        for b in l.bone_names:
            listed.setdefault(str(b).lower(), len(listed))
    added, warns = [], []
    news = list(new_bones or [])
    skipped = [nb.name for nb in news if str(nb.name).lower() not in listed]
    if skipped:
        warns.append(f"stub bone{'s' if len(skipped) != 1 else ''} {', '.join(skipped[:4])} "
                     f"{'are' if len(skipped) != 1 else 'is'} on the rig but in no limb, so not exported: Redo, run Add "
                     "Stub Socket again, or delete the bone")
    # in limb-row order (the order the sockets were added), whatever order the rig keeps its bones in
    news = sorted((nb for nb in news if str(nb.name).lower() in listed), key=lambda nb: listed[str(nb.name).lower()])
    if news:
        bw = R.bind_world_matrices(m)
        was_count = m.skel_info is not None and int(m.skel_info) == len(m.bones)
        names = {str(b.name).lower(): i for i, b in enumerate(m.bones)}
        for nb in news:
            why = _name_problem(nb.name)
            if why:
                raise ValueError(f"stub bone name '{nb.name}' {why}")
            if str(nb.name).lower() in names:
                raise ValueError(f"stub bone '{nb.name}': the skeleton already has a bone of that name (bone names differ "
                                 "ignoring case): rename or delete one of them")
            pi = next((i for i, b in enumerate(m.bones) if str(b.name) == str(nb.parent)), None)
            if pi is None or pi >= len(bw):
                raise ValueError(f"stub bone '{nb.name}': its parent '{nb.parent}' is not a bone of the skeleton")
            W = np.asarray(nb.world, dtype=np.float64).reshape(4, 4)
            if not np.isfinite(W).all():
                raise ValueError(f"stub bone '{nb.name}' has a non-finite rest matrix")
            local = np.linalg.inv(bw[pi]) @ W
            transform = [float(x) for x in (list(local[:3, 0]) + list(local[:3, 1]) + list(local[:3, 2])
                                            + list(local[:3, 3]))]
            par = m.bones[pi]
            m.bones.append(Bone(name=str(nb.name), parent=pi, start_vertex=0, vertex_count=0, transform=transform,
                                tail=[0.0] * len(par.tail), meta0=STUB_BYTE,
                                meta1=int(par.meta1), meta5=0))
            names[str(nb.name).lower()] = len(m.bones) - 1
            added.append(str(nb.name))
        if was_count:
            m.skel_info = len(m.bones)
    flagged = []
    index = {str(b.name).lower(): i for i, b in enumerate(m.bones)}
    for l, si in zip(m.limbs, limb_src):
        if int(si) >= 0 or l.prims:
            continue
        for bn in l.bone_names:
            i = index.get(str(bn).lower())
            if i is None:
                warns.append(f"limb '{l.name}' lists bone '{bn}', which is not in the skeleton (a stub bone deleted or "
                             "renamed?): Undo, or give the limb a bone of the skeleton")
                continue
            # only a bone owning no points is a stub bone (ICParse.validate.stub_bone_lines): a bone with points moved
            # into a stub limb keeps its flags, and Validate's 'owns control points' STOCK line speaks for it
            if str(bn) not in added and int(m.bones[i].vertex_count) == 0 and _flag_stub(m.bones[i]):
                flagged.append(m.bones[i].name)
    cleared = []
    for l in m.limbs:
        if not l.prims:
            continue
        for bn in l.bone_names:
            i = index.get(str(bn).lower())
            if i is not None and (int(m.bones[i].meta0) & 0xFF) == STUB_BYTE:
                m.bones[i].meta0 = int(m.bones[i].meta0) & 0xFFFFFF00
                cleared.append(str(m.bones[i].name))
    if cleared:
        warns.append(f"bone{'s' if len(cleared) != 1 else ''} {', '.join(cleared[:4])}{', ...' if len(cleared) > 4 else ''} "
                     f"lost the stub byte: a limb with patches lists {'them' if len(cleared) != 1 else 'it'}")
    return added, flagged, warns


# ------------------------------------------------------------------------------------------------ bone identity
# A rig bone IS a source bone by its import stamp (``rig.KEY_BONE_SRC_INDEX``), whatever it is called: renames keep the
# stamp. Blender's Duplicate and Symmetrize copy ID properties, so a copy carries its original's stamp too; among the
# bones sharing a stamp the one still called by the import name is the source bone, else the one whose rest still equals
# the import rest, else nobody can tell (refused as ambiguous). The other copies are added bones. A rig imported before
# the stamps (no bone stamped) matches by name, and a missing bone whose rest an added bone repeats counts as renamed.
@dataclass(frozen=True)
class RigBone:
    """A bone of the rig as the identity rule sees it: ``name``, ``parent`` (its parent's name, "" = root), ``src`` the
    import stamp (None = unstamped) and ``rest`` its rest matrix (16 floats, any consistent space; None = unknown)."""
    name: str
    parent: str = ""
    src: Optional[int] = None
    rest: Optional[Tuple[float, ...]] = None


@dataclass
class BoneResolution:
    """What :func:`resolve_bones` found. ``current``: per source bone, the name of the rig bone that is it (None =
    deleted or ambiguous); ``renames``: ``(source index, import name, current name)``; ``added``: rig bones that are no
    source bone; ``deleted``: import names with no rig bone; ``reparented``: ``(current name, import parent, current
    parent)``; ``ambiguous``: ``(import name, [copies])`` where no copy can be told to be the source bone; ``by_rest``:
    renames found by rest on a rig without stamps (legacy)."""
    current: List[Optional[str]]
    renames: List[Tuple[int, str, str]]
    added: List[str]
    deleted: List[str]
    reparented: List[Tuple[str, str, str]]
    ambiguous: List[Tuple[str, List[str]]]
    stamped: bool = True
    by_rest: List[Tuple[int, str, str]] = None
    overwritten: List[Tuple[str, List[str], List[str]]] = None

    def overwritten_text(self) -> List[str]:
        """Sentences for ``overwritten``: ``(import name of the stamp, [rig bones sharing it besides that bone], [source
        bones with no rig bone])`` -- the shape Symmetrize leaves when it mirrors onto a bone renamed since import."""
        out = []
        for idn, copies, lost in self.overwritten or []:
            who = ", ".join(repr(c) for c in copies)
            gone = ", ".join(repr(x) for x in lost[:3]) + (", ..." if len(lost) > 3 else "")
            out.append(f"bone(s) {who} carry the import stamp of '{idn}' while source bone(s) {gone} have no rig bone "
                       "(Blender's Symmetrize or Duplicate): Undo it, or rename the mirror bone back")
        return out

    def changes(self) -> List[str]:
        """The readable sentences (renames, deletes, adds, re-parents, ambiguous copies), empty when nothing changed."""
        out = self.overwritten_text() + [f"bone '{o}' was renamed to '{n}'" for _i, o, n in self.renames]
        out += [f"bone '{n}' was deleted" for n in self.deleted]
        out += [f"bone '{n}' was added" for n in self.added]
        out += [f"bone '{n}' was re-parented from '{was or '(root)'}' to '{now or '(root)'}'"
                for n, was, now in self.reparented]
        out += [f"bones {', '.join(repr(c) for c in copies)} all carry the import stamp of '{old}' (Duplicate and "
                "Symmetrize copy it) and none of them can be told to be that bone: none keeps its name or its rest"
                for old, copies in self.ambiguous]
        return out

    def rename_map(self) -> Dict[int, str]:
        """{source index: current name} of the renamed source bones."""
        return {i: n for i, _o, n in self.renames}


def _same_rest(a, b, tol) -> bool:
    if a is None or b is None:
        return False
    return bool(np.allclose(np.asarray(a, dtype=np.float64).reshape(4, 4), np.asarray(b, dtype=np.float64).reshape(4, 4),
                            atol=tol))


def resolve_bones(snapshot, current, snapshot_rest=None, tol=1e-5) -> BoneResolution:
    """Which rig bone is which source bone. ``snapshot``: ``[(import name, import parent name or "")]`` in source order;
    ``current``: the rig's bones (:class:`RigBone`, Blender's order); ``snapshot_rest``: per source bone its import
    rest (16 floats) or None. Names compare case-sensitively (the file stores the name, so do F-curve paths)."""
    snap = [(str(a), str(b or "")) for a, b in snapshot]
    n = len(snap)
    rest = list(snapshot_rest) if snapshot_rest is not None and len(snapshot_rest) == n else [None] * n
    cur = list(current)
    stamped = any(b.src is not None and 0 <= int(b.src) < n for b in cur)
    ident: List[Optional[str]] = [None] * n
    taken = set()
    ambiguous = []
    overwritten = []
    stamp_ids = set()
    if stamped:
        claims: Dict[int, List[RigBone]] = {}
        for b in cur:
            if b.src is not None and 0 <= int(b.src) < n:
                claims.setdefault(int(b.src), []).append(b)
        stamp_ids = set(claims)
        # Symmetrize onto a bone that already exists copies the selected bone's ID properties onto it, so the mirror
        # bone carries the other side's stamp: a copy holding exactly the import name of a source bone no rig bone is
        # stamped with IS that bone (as a name match was before the stamps)
        import_of = {}
        for i, (nm, _p) in enumerate(snap):
            import_of.setdefault(nm, i)
        for sid in sorted(claims):
            bones = claims[sid]
            if len(bones) < 2:
                continue
            keep = []
            for b in bones:
                j = import_of.get(b.name)
                if j is not None and j != sid and j not in stamp_ids and ident[j] is None:
                    ident[j] = b.name
                    taken.add(b.name)
                else:
                    keep.append(b)
            claims[sid] = keep
        for sid in sorted(claims):
            bones = claims[sid]
            if not bones:
                continue
            if len(bones) == 1:
                pick = bones[0]
            else:
                named = [b for b in bones if b.name == snap[sid][0]]
                same = [b for b in bones if _same_rest(b.rest, rest[sid], tol)]
                pick = named[0] if named else (same[0] if len(same) == 1 else None)
            if pick is None:
                ambiguous.append((snap[sid][0], [b.name for b in bones]))
                taken.update(b.name for b in bones)
                continue
            ident[sid] = pick.name
            taken.add(pick.name)
    # unstamped bones (and stamps out of range) match a still-unclaimed source bone by name
    free = {nm: i for i, (nm, _p) in enumerate(snap) if ident[i] is None and nm not in {a for a, _c in ambiguous}}
    unstamped = [b for b in cur if b.name not in taken and not (b.src is not None and 0 <= int(b.src) < n)]
    for b in unstamped:
        i = free.get(b.name)
        if i is not None and ident[i] is None:
            ident[i] = b.name
            taken.add(b.name)
    added = [b.name for b in cur if b.name not in taken]
    by_rest = []
    if not stamped:
        rest_of = {b.name: b.rest for b in cur}
        for i in range(n):
            if ident[i] is not None or rest[i] is None:
                continue
            for nm in added:
                if _same_rest(rest_of.get(nm), rest[i], tol):
                    ident[i] = nm
                    by_rest.append((i, snap[i][0], nm))
                    added.remove(nm)
                    break
    amb_names = {a for a, _c in ambiguous}
    deleted = [snap[i][0] for i in range(n) if ident[i] is None and snap[i][0] not in amb_names]
    if stamped and deleted:
        # copies left over from a shared stamp while a source bone no rig bone is stamped with has no rig bone: the
        # shape Symmetrize leaves when it mirrors onto a bone renamed since import (no import name to find it by).
        # Duplicate's copies (name.001) are no such case.
        lost = [snap[i][0] for i in range(n) if ident[i] is None and snap[i][0] not in amb_names and i not in stamp_ids]
        known = {c for c in ident if c is not None}
        for sid in sorted(stamp_ids):
            everyone = [b.name for b in cur if b.src is not None and int(b.src) == sid]
            left = [nm for nm in everyone if nm not in known and not re.search(r"\.\d{3,}$", nm)]
            if len(everyone) > 1 and left and lost:
                overwritten.append((snap[sid][0], left, lost))
    renames = [(i, snap[i][0], ident[i]) for i in range(n) if ident[i] is not None and ident[i] != snap[i][0]]
    of_name = {nm: snap[i][0] for i, nm in enumerate(ident) if nm is not None}
    for old, copies in ambiguous:                   # a child of an ambiguous bone's copies still hangs from that bone
        of_name.update({c: old for c in copies})
    parent_of = {b.name: str(b.parent or "") for b in cur}
    reparented = []
    for i, nm in enumerate(ident):
        if nm is None:
            continue
        now = parent_of.get(nm, "")
        now_src = "" if not now else of_name.get(now)
        if now_src != snap[i][1]:
            reparented.append((nm, snap[i][1], now))
    return BoneResolution(current=ident, renames=renames, added=added, deleted=deleted, reparented=reparented,
                          ambiguous=ambiguous, stamped=stamped, by_rest=by_rest, overwritten=overwritten)


def name_conflicts(snapshot_names, resolution, extra=()) -> List[str]:
    """Rename chains the export can't resolve: a rig bone now called (ignoring case) what ANOTHER bone was called at
    import -- ``ear_l`` renamed to ``ear_left`` and then ``ear_r`` renamed to ``ear_l``, or two bones swapping names.
    Limb bone lists, connection hosts and the F-curve paths of Actions Blender did not fix (an Action on no NLA strip)
    name bones by their import name, so such a name would mean two bones. ``extra``: ``[(identity name, current
    name)]`` of other stamped bones (created stub bones: the name they were created with). Returns sentences."""
    snap = [str(x) for x in snapshot_names]
    ids = [(snap[i], c) for i, c in enumerate(resolution.current) if c is not None] + [(str(a), str(b)) for a, b in extra]
    now = {}
    for who, (_idn, c) in enumerate(ids):
        now.setdefault(c.lower(), who)
    for c in resolution.added:
        now.setdefault(str(c).lower(), -1)
    out = []
    for who, (idn, c) in enumerate(ids):
        if idn == c:
            continue
        holder = now.get(idn.lower())
        if holder is not None and holder != who:
            other = ids[holder][1] if holder >= 0 else next(a for a in resolution.added if a.lower() == idn.lower())
            was = f" (it was '{ids[holder][0]}')" if holder >= 0 and ids[holder][0] != other else ""
            out.append(f"bone '{idn}' was renamed to '{c}' and bone '{other}'{was} now has its old name, so '{idn}' would "
                       "mean two bones: use a new name, export, re-import and rename again (or Undo)")
    return out


def bone_name_problems(names, renamed) -> List[str]:
    """The name rules for the export skeleton ``names`` (every rig bone the export writes), checked on the ``renamed``
    names: not empty, plain printable ASCII, at most 63 characters (the loaders copy 0x3f bytes; limb bone entries are
    0x40 bytes), no leading or trailing space, and unique ignoring case (Boner refuses "multiple bones called", the
    combiner and the Object Editor look names up lower-cased). Returns sentences."""
    out = []
    low: Dict[str, List[str]] = {}
    for nm in names:
        low.setdefault(str(nm).lower(), []).append(str(nm))
    pairs = set()                                   # each clashing pair is said once, whichever of the two is checked
    for nm in renamed:
        nm = str(nm)
        why = _name_problem(nm)
        if why is None and nm != nm.strip():
            why = "starts or ends with a space"
        if why:
            out.append(f"bone name '{nm}' {why}")
        for other in dict.fromkeys(x for x in low.get(nm.lower(), []) if x != nm):
            if frozenset((nm, other)) not in pairs:
                pairs.add(frozenset((nm, other)))
                out.append(f"bones '{nm}' and '{other}' differ only in case: bone names must differ ignoring case (the "
                           "game looks them up lower-cased)")
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------------------------------------ bone renames
def respell(name, old, new, lower_first=False) -> str:
    """``name`` -- a reference to the bone called ``old`` (equal ignoring case) -- spelt for the bone's new name ``new``.
    The exact spelling becomes ``new``; an all-lower-case reference (base-game clip tracks: 4969 of them) stays lower
    case, an all-upper-case one upper case; a reference with its own mixed case (lammergeier's 'Feather1_l' for bone
    'feather1_L') carries each differing letter over by its position from the start (in the common prefix of old and
    new) or from the end (in the common suffix). Renaming back restores the reference exactly, unless the respelt
    reference came out all lower or upper case (then it reads back as such: lammergeier's 'EndWing_l' renamed to
    'wingtip_L' is 'wingtip_l'). ``lower_first``: an all-lower-case reference stays lower case even when the old name
    is lower case too -- the clip-track convention (every base-game track is the lower-cased bone name), so a track
    renamed to a mixed-case name and back is its lower-case self again."""
    name, old, new = str(name), str(old), str(new)
    if lower_first and name == old.lower():
        return new.lower()
    if name == old:
        return new
    if name == old.lower():
        return new.lower()
    if name == old.upper():
        return new.upper()
    lo, ln = old.lower(), new.lower()
    p = 0
    while p < min(len(lo), len(ln)) and lo[p] == ln[p]:
        p += 1
    s = 0
    while s < min(len(lo), len(ln)) and lo[-1 - s] == ln[-1 - s]:
        s += 1
    out = list(new)
    for i, (a, b) in enumerate(zip(name, old)):
        if a == b:
            continue
        if i < p:
            j = i
        elif len(old) - i <= s:
            j = len(new) - (len(old) - i)
        else:
            continue
        if 0 <= j < len(out):
            out[j] = out[j].swapcase()
    return "".join(out)


class BoneRenamer:
    """Old name -> new name, matched ignoring case (the engine's own lookup) and spelt with :func:`respell`."""

    def __init__(self, pairs, lower_first=False):
        self.lower_first = bool(lower_first)
        self.pairs = [(str(o), str(n)) for o, n in pairs if str(o) != str(n)]
        self._by_low: Dict[str, Tuple[str, str]] = {}
        for o, n in self.pairs:
            if o.lower() in self._by_low:
                raise ValueError(f"two bones are called '{o}' ignoring case in the source: a rename can't tell which "
                                 "one other records name")
            self._by_low[o.lower()] = (o, n)

    def __bool__(self):
        return bool(self.pairs)

    def __call__(self, name) -> str:
        hit = self._by_low.get(str(name).lower())
        return respell(name, hit[0], hit[1], self.lower_first) if hit else str(name)

    def inverse(self) -> "BoneRenamer":
        return BoneRenamer([(n, o) for o, n in self.pairs], self.lower_first)


@dataclass
class RenameReport:
    renamed: List[Tuple[str, str]]
    limbs: int = 0            # limb bone lists rewritten
    connections: int = 0      # connection host names rewritten
    ik_chains: int = 0        # IK chains rewritten
    tracks: int = 0           # clip tracks rewritten
    clips: int = 0            # clips with a rewritten track
    ik_note: str = ""
    warnings: List[str] = field(default_factory=list)   # a renamed bone adopting clip tracks that named no bone

    def text(self) -> str:
        if not self.renamed:
            return ""
        few = ", ".join(f"{o} -> {n}" for o, n in self.renamed[:3]) + (", ..." if len(self.renamed) > 3 else "")
        follow = [f"{k} {w}" for k, w in ((self.limbs, "limb list(s)"), (self.connections, "host(s)"),
                                          (self.ik_chains, "IK chain(s)"), (self.tracks, "clip track(s)")) if k]
        return (f"renamed {len(self.renamed)} ({few})" + (f", followed by {', '.join(follow)}" if follow else ""))


def apply_bone_renames(m, renames, limb_aliases=None) -> RenameReport:
    """Rename source bones of the export model ``m`` (its bones still in source order; created stub bones not appended
    yet) and every record that names them: limb bone lists, connection hosts, IK chains (``ICParse ik_chains``; the
    chunk is rebuilt only when a chain changed) and the tracks of the clips in ``m`` (the source's; clips re-encoded
    later are built against the renamed bones). References match ignoring case and keep their spelling style
    (:func:`respell`). ``renames``: {source bone index: new name}; ``limb_aliases``: {name: new name} applied to limb
    bone lists only (a created stub bone renamed since its limb listed it). Raises ValueError for an index outside the
    skeleton, a name the file can't hold, or a name another bone has (ignoring case)."""
    from ICParse.sgm.creature.model import ik_chains, set_ik_chains
    pairs = []
    for i, new in sorted((renames or {}).items()):
        i = int(i)
        if not 0 <= i < len(m.bones):
            raise ValueError(f"rename of bone index {i}, but the skeleton has {len(m.bones)} bones")
        if str(m.bones[i].name) != str(new):
            pairs.append((i, str(m.bones[i].name), str(new)))
    rep = RenameReport(renamed=[(o, n) for _i, o, n in pairs])
    aliases = BoneRenamer((limb_aliases or {}).items())
    if not pairs and not aliases:
        return rep
    final = [str(b.name) for b in m.bones]
    src_names = list(final)
    for i, _o, n in pairs:
        final[i] = n
    probs = bone_name_problems(final, [n for _i, _o, n in pairs])
    if probs:
        raise ValueError("; ".join(probs))
    ren = BoneRenamer([(o, n) for _i, o, n in pairs])
    for i, _o, n in pairs:
        m.bones[i].name = n
    for limb in m.limbs:
        new_list = [aliases(ren(b)) if ren(b) == str(b) else ren(b) for b in limb.bone_names]
        if new_list != [str(b) for b in limb.bone_names]:
            limb.bone_names = new_list
            rep.limbs += 1
    for c in m.limb_connections:
        nb = ren(c.bone_name)
        if nb != str(c.bone_name):
            c.bone_name = nb
            rep.connections += 1
    chains = ik_chains(m) if ren else None
    if chains:
        changed = 0
        for ch in chains:
            nb = [ren(b) for b in ch.bones]
            if nb != list(ch.bones):
                ch.bones = nb
                changed += 1
        if changed:
            set_ik_chains(m, chains)
            rep.ik_chains = changed
    if ren:
        track = BoneRenamer(ren.pairs, lower_first=True)     # tracks: the lower-cased bone name, as the base game
        src_low = {str(o).lower() for o in src_names}
        new_low = {n.lower(): (o, n) for _i, o, n in pairs}
        dups: Dict[str, List[str]] = {}
        adopted: Dict[Tuple[str, str], List[str]] = {}
        for a in m.animations:
            now = [track(t.bone_name) for t in a.tracks]
            seen: Dict[str, bool] = {}
            for t, nb in zip(a.tracks, now):
                low, moved = nb.lower(), nb != str(t.bone_name)
                if low in seen and (moved or seen[low]):
                    dups.setdefault(nb, []).append(str(a.name))
                seen[low] = seen.get(low, False) or moved
                if nb == str(t.bone_name) and low not in src_low and low in new_low:
                    adopted.setdefault(new_low[low], []).append(str(a.name))    # a track that named no bone
        if dups:
            nb, clips = next(iter(dups.items()))
            raise ValueError(f"after the rename, clip(s) {', '.join(clips[:4])}{', ...' if len(clips) > 4 else ''} would "
                             f"have two '{nb}' tracks (the renamed bone's own and one already called that): pick a name "
                             "no clip track has")
        for (o, n), clips in adopted.items():
            rep.warnings.append(f"bone '{o}' renamed to '{n}': clip(s) {', '.join(clips[:4])}"
                                f"{', ...' if len(clips) > 4 else ''} already carry a '{n}' track that named no bone of "
                                f"the skeleton; the game now plays those keys on this bone")
        for a in m.animations:
            k = 0
            for t in a.tracks:
                nb = track(t.bone_name)
                if nb != str(t.bone_name):
                    t.bone_name = nb
                    k += 1
            rep.tracks += k
            rep.clips += bool(k)
        if getattr(m, "anim_raw", None):
            raise ValueError(f"{len(m.anim_raw)} clip(s) are kept as undecoded bytes, so their tracks can't follow a bone "
                             "rename: re-encode them (edit their Actions) or undo the rename")
    return rep


# ------------------------------------------------------------------------------------------------ added bones
# Add Bone (Boner > Bones), or a bone Blender's own Add / Extrude made that the skeleton guard adopts: an ordinary bone
# the export inserts into the skeleton. Its owner key (``.bz_vert_bone``) is ``n_src + j`` (j: its entry in the store's
# journaled new-bone table, or a key past the table for a bone no Boner operator has stamped yet), so points skinned to
# it keep their meaning whatever index the bone gets in the file. Engine fact (B7 probe, 2026-09-15): a clip with no
# track for a bone holds it at its bind-LOCAL transform -- it follows its parent -- in the Object Editor exactly as
# ICParse's decode (cheetah + horn_l under head owning 8 head points, O_growl played: 4 frames at max 8.7e-7 m, where a
# bind-WORLD hold would put those points 29-49 mm off), so kept clips need no track for an added bone.
@dataclass(frozen=True)
class AddedBone:
    """A bone added on the rig. ``name``: its current name; ``key``: its owner key (``>= n_src``); ``parent``: the key
    of its parent (a source bone index ``< n_src``, or another added bone's key); ``world``: its rest as 16 floats, a
    row-major 4x4 in SGM axes (the bind world); ``stamp``: the name its Add Bone stamp holds (what the stored limb rows
    list it by), when it was renamed since; ``limb_default``: no Boner operator has stamped it yet (a bone Blender
    made). Its limb is always its parent's (:func:`added_limb_rows`)."""
    name: str
    key: int
    parent: int
    world: Tuple[float, ...]
    stamp: Optional[str] = None
    limb_default: bool = False

    @property
    def listed_as(self) -> str:
        return str(self.stamp if self.stamp else self.name)


def stub_bone_indices(m) -> set:
    """The stub bones of ``m`` as ``ICParse.validate.stub_bone_lines`` counts them: a bone whose flags low byte is the
    stub byte, or the bone owning no points of a limb with no patches."""
    index = {}
    for i, b in enumerate(m.bones):
        index.setdefault(str(b.name).lower(), i)
    out = {i for i, b in enumerate(m.bones) if (int(b.meta0) & 0xFF) == STUB_BYTE}
    for l in m.limbs:
        if l.prims:
            continue
        for bn in l.bone_names:
            i = index.get(str(bn).lower())
            if i is not None and int(m.bones[i].vertex_count) == 0:
                out.add(i)
    return out


def export_stub_bones(src, rows=None, patch_limb=None, owners=None) -> set:
    """The source bone indices the export writes as stub bones: every bone carrying the stub FLAG, plus every bone owning
    no points that a limb with no patches lists -- :func:`stub_bone_indices`' rule, but read off the LIVE creature.
    ``rows``: the limb rows (None = the source's); ``patch_limb``: patch -> row index (None = the source's membership);
    ``owners``: the owner key of every point (None = the source's ranges). Both build paths insert the added bones against
    this set, and the skeleton guard and Add Bone refuse an added bone under one of its bones, so where a bone goes and
    whether it is refused never depend on whether the geometry grew.

    The rule is live in BOTH directions (fit-to-rig, 2026-09-16): a bone becomes a stub when its limb loses its patches,
    and -- what the native pillbug build had to save and re-open for -- it STOPS being one as soon as the live limb table
    gives that limb patches or the live skin gives the bone points. A flagged stub bone (the wing / arm sockets) stays a
    stub whatever the tables say: that flag is the file's own, and the writer keeps those bones last. For an unedited
    creature (source rows, source membership, source ranges) the answer is exactly :func:`stub_bone_indices`."""
    out = {i for i, b in enumerate(src.bones) if (int(b.meta0) & 0xFF) == STUB_BYTE}
    if rows is None and patch_limb is None and owners is None:
        return out | set(stub_bone_indices(src))
    rows = source_rows(src) if rows is None else list(rows)
    pl = patch_limb_of_rows(src, rows) if patch_limb is None else np.asarray(patch_limb, dtype=np.int64).reshape(-1)
    with_patches = {int(j) for j in np.unique(pl) if j >= 0}
    index = {}
    for i, b in enumerate(src.bones):
        index.setdefault(str(b.name).lower(), i)
    has_points = None
    if owners is not None:
        own = np.asarray(owners, dtype=np.int64).reshape(-1)
        has_points = {int(k) for k in np.unique(own[own >= 0])}
    busy, derived = set(), set()                         # bones a limb WITH patches lists: apply_stub_bones CLEARS
    for j, r in enumerate(rows):                         # their stub byte, so the export writes no stub bone there
        for bn in row_bones(r, src):
            i = index.get(str(bn).lower())
            if i is None:
                continue                                 # a created stub bone (appended last) or an added bone
            if j in with_patches:
                busy.add(i)
            elif not (i in has_points if has_points is not None else int(src.bones[i].vertex_count) > 0):
                derived.add(i)                           # a patch-less limb's point-less bone is one whatever the byte
    return (out - busy) | derived


@dataclass
class BoneInsert:
    """Where :func:`insert_added_bones` put the added bones: ``n_src`` source bones, ``at`` the file index of the first
    added bone (the bone count when none), ``index`` {owner key: file index} for every source and added bone, ``added``
    the added names in file order, ``warnings``."""
    n_src: int
    at: int
    index: Dict[int, int]
    added: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def owners(self, keys) -> np.ndarray:
        """Owner keys (-1 = no bone) as file bone indices. Raises ValueError for a key that is no bone of the export (a
        point skinned to an added bone that is not on the rig any more)."""
        k = np.asarray(keys, dtype=np.int64).reshape(-1)
        if not len(k):
            return k.copy()
        bad = sorted({int(x) for x in np.unique(k) if x >= 0 and int(x) not in self.index})
        if bad:
            cnt = int(np.isin(k, bad).sum())
            raise ValueError(f"{cnt} control point{'s are' if cnt != 1 else ' is'} skinned to an added bone that is not on "
                             "the rig any more (orange in By Bone): Undo, or assign another bone (Boner > Bones > Select "
                             "Unassigned)")
        top = int(k.max())
        lut = np.full(max(top + 1, 1), -1, dtype=np.int64)
        for key, i in self.index.items():
            if 0 <= key <= top:
                lut[key] = i
        return np.where(k >= 0, lut[np.clip(k, 0, None)], -1)

    def keys(self, count) -> List[int]:
        """The owner key of each of the first ``count`` file bones (a created stub bone appended later has none)."""
        back = {i: key for key, i in self.index.items()}
        return [int(back.get(i, -1)) for i in range(int(count))]


def _parent_first(added, n_src) -> List[AddedBone]:
    by_key = {}
    for ab in added:
        k = int(ab.key)
        if k < n_src:
            raise ValueError(f"added bone '{ab.name}' has owner key {k}, a source bone's (keys of added bones start at "
                             f"{n_src}): re-import the creature")
        if k in by_key:
            raise ValueError(f"added bones '{by_key[k].name}' and '{ab.name}' share the owner key {k}")
        by_key[k] = ab
    out, done = [], set()

    def visit(ab, trail):
        k = int(ab.key)
        if k in done:
            return
        if k in trail:
            raise ValueError(f"added bone '{ab.name}' is its own ancestor")
        p = int(ab.parent)
        if p >= n_src:
            if p not in by_key:
                raise ValueError(f"added bone '{ab.name}': its parent (key {p}) is no bone of the export")
            visit(by_key[p], trail | {k})
        elif p < 0:
            raise ValueError(f"added bone '{ab.name}' has no parent: a creature skeleton has one root (the source's)")
        done.add(k)
        out.append(ab)
    for ab in sorted(added, key=lambda a: int(a.key)):
        visit(ab, frozenset())
    return out


def insert_added_bones(m, added, n_src, stubs=None) -> BoneInsert:
    """Insert the :class:`AddedBone` list into the export model ``m`` (its bones still the source's, rest edits applied).

    * Position: before the first stub bone (``stubs``: source bone indices, :func:`export_stub_bones` -- the export's
      stub bones, whichever build path; None = :func:`stub_bone_indices` of ``m``; Boner writes stub bones last), else
      appended; parents before children, siblings in key (creation) order. A parent written after that position (a file
      off the stub rule, or a new stub limb's bone the source has earlier) moves the insertion after it. An added bone
      under a stub bone is refused (a stub bone is its limb's only bone and sorts last).
    * Record: bind-local transform = inverse(parent bind world) @ its rest world (the parent's rest edits included);
      no vertex range (start 0, count 0, aux_count 0 -- a reskin lays the ranges and aux counts out afterwards); joint
      limits all zero (the end-site bones' convention) in the layout of a sibling, else of the parent (6 or 7 floats);
      flags word and display colour word from the parent, the stub byte cleared.
    * Parent indices of the source bones after the position shift by the number added; ``skel_info`` follows the
      bone count when it equalled it. Names and limb lists are left to the caller (renames apply after; bone names are
      checked there with every final name).

    Returns the :class:`BoneInsert` (identity when nothing is added). Raises ValueError for a bad key or parent, a
    non-finite rest, a name the file can't hold or an added bone under a stub bone."""
    from ICParse.codecs import reconstruct as R
    from ICParse.sgm.creature.model import Bone
    n = len(m.bones)
    if n != int(n_src):
        raise ValueError(f"added bones: the export model has {n} bones, the source {n_src} (structure applied twice?)")
    added = list(added or [])
    if not added:
        return BoneInsert(n, n, {i: i for i in range(n)})
    order = _parent_first(added, n)
    stubs = stub_bone_indices(m) if stubs is None else {int(i) for i in stubs if 0 <= int(i) < n}
    need = 0
    for ab in order:
        p = int(ab.parent)
        if p < n:
            if p in stubs:
                raise ValueError(f"bone '{ab.name}' is added under '{m.bones[p].name}', a stub bone (a stub limb's only "
                                 "bone, written after every other bone): add it under the stub's host instead")
            need = max(need, p + 1)
    at = max(min(stubs) if stubs else n, need)
    bw = R.bind_world_matrices(m)
    world_of: Dict[int, np.ndarray] = {}
    rec_of: Dict[int, object] = {}
    pos_of: Dict[int, int] = {}
    new = []
    for j, ab in enumerate(order):
        why = _name_problem(ab.name)
        if why is None and str(ab.name) != str(ab.name).strip():
            why = "starts or ends with a space"
        if why:
            raise ValueError(f"bone name '{ab.name}' {why}: rename the bone (Boner > Bones > Rename Bone)")
        W = np.asarray(ab.world, dtype=np.float64).reshape(4, 4)
        if not np.isfinite(W).all():
            raise ValueError(f"added bone '{ab.name}' has a non-finite rest matrix")
        p = int(ab.parent)
        if p < n:
            P, par = bw[p], m.bones[p]
            sib = next((b for b in m.bones if b.parent is not None and int(b.parent) == p), None)
            pidx = p
        else:
            P, par = world_of[p], rec_of[p]
            sib = next((r for a, r in zip(order[:j], new) if int(a.parent) == p), None)
            pidx = at + pos_of[p]
        local = np.linalg.inv(P) @ W
        transform = [float(x) for x in (list(local[:3, 0]) + list(local[:3, 1]) + list(local[:3, 2])
                                        + list(local[:3, 3]))]
        ref = sib if sib is not None else par
        rec = Bone(name=str(ab.name), parent=pidx, start_vertex=0, vertex_count=0, transform=transform,
                   tail=[0.0] * len(ref.tail), meta0=int(par.meta0) & 0xFFFFFF00, meta1=int(par.meta1), meta5=0)
        world_of[int(ab.key)], rec_of[int(ab.key)], pos_of[int(ab.key)] = W, rec, j
        new.append(rec)
    k = len(new)
    for b in m.bones:
        if b.parent is not None and int(b.parent) >= at:
            b.parent = int(b.parent) + k
    was_count = m.skel_info is not None and int(m.skel_info) == n
    m.bones[at:at] = new
    if was_count:
        m.skel_info = len(m.bones)
    index = {i: (i if i < at else i + k) for i in range(n)}
    index.update({int(ab.key): at + j for j, ab in enumerate(order)})
    warns = []
    low = {str(ab.name).lower(): str(ab.name) for ab in order}
    orphan: Dict[str, List[str]] = {}
    for a in m.animations:
        for t in a.tracks:
            hit = low.get(str(t.bone_name).lower())
            if hit is not None:
                orphan.setdefault(hit, []).append(str(a.name))
    for nm, clips in orphan.items():
        warns.append(f"added bone '{nm}': clip(s) {', '.join(clips[:4])}{', ...' if len(clips) > 4 else ''} already carry "
                     f"a '{nm}' track that named no bone of the skeleton; the game now plays those keys on this bone")
    return BoneInsert(n, at, index, [str(ab.name) for ab in order], warns)


@dataclass
class AddedLimbs:
    """Where :func:`added_limb_rows` put the added bones: ``rows`` (the rows given -- the same object -- when nothing
    changed), ``limb_of`` {owner key: limb row index, -1 = no limb}, ``placed`` "bone -> limb" for a bone its parent's
    limb did not list yet, ``moved`` ``(bone, from row, to row or -1, parent)`` for a bone a limb listed that its parent
    is not in any more, ``dropped`` ``(entry, row)`` for entries of added bones that are not on the rig, ``warnings``."""
    rows: Optional[List[LimbRow]]
    limb_of: Dict[int, int] = field(default_factory=dict)
    placed: List[str] = field(default_factory=list)
    moved: List[Tuple[str, int, int, str]] = field(default_factory=list)
    dropped: List[Tuple[str, int]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def moved_text(self, limb_names) -> List[str]:
        return [f"{b} -> {limb_names[j] if j >= 0 else 'no limb'} (moved from {limb_names[jo]}: its parent '{p}' is "
                + ("there)" if j >= 0 else "in none)") for b, jo, j, p in self.moved]

    def dropped_text(self, limb_names) -> str:
        return (", ".join(f"{b} from {limb_names[j]}" for b, j in self.dropped)
                + (" (an added bone not on the rig any more)" if len(self.dropped) == 1
                   else " (added bones not on the rig any more)"))


def added_limb_rows(rows, src, added, table_names=(), keep=(), by_stamp=False, pinned=()) -> AddedLimbs:
    """Limb membership of the added bones, by identity: every :class:`AddedBone` is in the limb that lists its CURRENT
    parent (ignoring case; a source parent by its file name, an added parent where it was put), whatever a row said --
    unless a Boner limb operator PINNED it (``pinned``: the names the user put in a limb by hand, C8 + fit-to-rig
    2026-09-16), and then it is in the row that lists it and its own added children follow it there. A new limb rooted
    at an added bone needs that: nothing can be re-parented into a limb that has no bones yet, so the parent rule alone
    can never give such a limb its root (the native pillbug build had to re-class a stub limb and rename a source bone
    instead). A pinned bone no row lists falls back to its parent's limb, so a pin can only ever say where a listed
    bone stays.

    A limb entry is an added bone's when it names (ignoring case) an entry of the new-bone table (``table_names``: the
    names its bones were made with, on the rig or not), or an added bone's current or stamp name -- never a source bone's
    file name nor a ``keep`` name (created stub bones). Such entries are taken off every row; then, parents first, each
    added bone takes back an entry of its parent's limb naming it (its current name first, then its stamp name; its
    position is kept) or is appended to that limb's list. An entry left over names a bone that moved to another limb
    (its parent did) or is ``dropped``: its bone is not on the rig any more (deleted, or its Add Bone undone in the rig's
    Edit Mode), so the export writes the file as if it had never been added. A bone whose parent is in no limb stays in
    none (a warning says so). Entries are written as the bone's current name (the export), or with ``by_stamp`` as the
    name its stamp holds (the stored rows, which survive later renames). ``rows`` None = the source's rows; a changed row
    whose list becomes its source record's is stored as that record's (None)."""
    added = list(added or [])
    base = source_rows(src) if rows is None else list(rows)
    fixed = {str(b.name).lower() for b in src.bones} | {str(k).lower() for k in keep if k}
    cand = ({str(t).lower() for t in table_names if t} | {ab.name.lower() for ab in added}
            | {ab.listed_as.lower() for ab in added}) - fixed
    res = AddedLimbs(rows=rows)
    if not cand:
        return res
    lists = [row_bones(r, src) for r in base]
    n_src = len(src.bones)
    by_key = {int(ab.key): ab for ab in added}
    claimed: Dict[Tuple[int, int], str] = {}
    appended: List[List[str]] = [[] for _ in base]
    order = _parent_first(added, n_src)
    pins = {str(p).lower() for p in (pinned or ()) if p}
    unlisted = []                                   # (bone, its row or -1, parent name, parent is a source bone)
    for ab in order:
        p = int(ab.parent)
        if p < n_src:
            pname = str(src.bones[p].name)
            j = next((j for j, bl in enumerate(lists) if pname.lower() in {b.lower() for b in bl}), -1)
        else:
            pname = by_key[p].name
            j = res.limb_of.get(p, -1)
        mine = {ab.name.lower(), ab.listed_as.lower()}
        if pins & mine:                             # pinned by a Boner limb operator: the row that lists it wins
            jp = next((jj for jj, bl in enumerate(lists) if mine & {b.lower() for b in bl}), -1)
            if jp >= 0:
                j = jp
        res.limb_of[int(ab.key)] = j
        hit = None
        if j >= 0:
            hit = next(((j, i) for k in (ab.name.lower(), ab.listed_as.lower()) for i, b in enumerate(lists[j])
                        if b.lower() == k and (j, i) not in claimed), None)
        if hit is not None:
            claimed[hit] = ab.listed_as if by_stamp else ab.name
            continue
        if j >= 0:
            appended[j].append(ab.listed_as if by_stamp else ab.name)
        unlisted.append((ab, j, pname, p < n_src))
    # entries no bone took back: of a bone that moved with its parent, else of a bone that is not on the rig
    left = [(j, i) for j, bl in enumerate(lists) for i, b in enumerate(bl) if b.lower() in cand and (j, i) not in claimed]
    used = set()
    for ab, j, pname, src_parent in unlisted:
        was = next(((jo, i) for k in (ab.name.lower(), ab.listed_as.lower()) for jo, i in left
                    if (jo, i) not in used and lists[jo][i].lower() == k), None)
        if was is not None:
            used.add(was)
            res.moved.append((ab.name, was[0], j, pname))
        elif j >= 0:
            res.placed.append(f"{ab.name} -> {base[j].name}")
        if j < 0:
            res.warnings.append(f"added bone '{ab.name}' is in no limb (its parent '{pname}' is in none): the Object "
                                "Editor warns about a bone in no limb; " + (
                                    f"put '{pname}' into a limb (Boner > Limbs > Add Active Bone), or re-parent the "
                                    "bone under a bone of a limb" if src_parent else
                                    f"'{pname}' is an added bone in no limb too: re-parent it under a bone of a limb"))
    res.dropped = [(lists[j][i], j) for j, i in left if (j, i) not in used]
    out, changed = list(base), False
    for j, r in enumerate(base):
        new = [claimed.get((j, i), b) for i, b in enumerate(lists[j]) if (j, i) in claimed or b.lower() not in cand]
        new += appended[j]
        if new == lists[j]:
            continue
        changed = True
        src_list = [str(b) for b in src.limbs[r.src].bone_names] if 0 <= r.src < len(src.limbs) else None
        out[j] = LimbRow(r.src, r.name, r.limb_id, None if new == src_list else tuple(new))
    if changed:
        res.rows = out
    return res


def added_bone_stock_lines(m, added_names) -> List[str]:
    """``STOCK`` lines for added bones that own no control points and are in no stub limb: they move nothing in game
    (every other bone of that kind is an end site the creature was authored with)."""
    stub_limb = set()
    index = {str(b.name).lower(): i for i, b in enumerate(m.bones)}
    for l in m.limbs:
        if not l.prims:
            stub_limb.update(str(b).lower() for b in l.bone_names)
    out = []
    for nm in added_names:
        i = index.get(str(nm).lower())
        if i is None or int(m.bones[i].vertex_count) or str(nm).lower() in stub_limb:
            continue
        out.append(f"STOCK added bone '{m.bones[i].name}' owns no control points and is in no stub limb: it moves nothing "
                   "in game; skin points to it (Boner > Bones > Assign Selected) or delete it")
    return out


@dataclass(frozen=True)
class RigAdded:
    """A rig bone the identity rule matched to no source bone (``BoneResolution.added``), as the add rule sees it:
    ``name``, ``parent`` (its parent's name, "" = root), ``children`` (how many bones hang from it), ``uid`` / ``stamp``
    (Add Bone's identity stamp and the name it was stamped with; None = unstamped), ``src_stamp`` (it carries a source
    bone's import stamp: a Duplicate / Symmetrize copy of a source bone), ``stub`` (a created stub bone the stub rule
    refused: its own message says why)."""
    name: str
    parent: str = ""
    children: int = 0
    uid: Optional[str] = None
    stamp: Optional[str] = None
    src_stamp: bool = False
    stub: bool = False


ADOPT_HINT = "Boner > Bones > Adopt Added Bones gives bones Blender added their identity (so points can be skinned to them)"


def classify_added_bones(added, source_of, table_uids, n_src, stub_names=(), chains=False) -> Tuple[list, List[Tuple[str, str]]]:
    """Which added rig bones the export may insert (the add rule). ``added``: :class:`RigAdded`; ``source_of``: {rig
    name: source index} of the rig's source bones; ``table_uids``: the store's new-bone table (a bone whose uid is at
    position j has key ``n_src + j``); ``stub_names``: names of stub bones (source stub bones, created stub bones).

    * A bone stamped by Add Bone (one holder of its stamp; among copies the one still called by its stamp name) is a
      new bone, and may have children. Its key is ``n_src + j`` for its uid at table position j; a stamp the table no
      longer holds (the table's Undo went further back than the rig's) gets a key past the table, and the bone goes
      into its parent's limb like an unstamped one.
    * A bone with no stamp at all (Blender's own Add / Extrude / E, a leaf or a whole chain of new bones) is adopted
      automatically (C8.3): it gets a key past the table and goes into its parent's limb (Adopt Added Bones stamps such
      bones so points can be skinned to them before the export; the export needs no button).
    * Its parent must be a source bone that is no stub bone, or an accepted added bone.
    * Refused (genuinely ambiguous): a copy of a source bone (it carries that bone's import stamp), a root bone, a copy
      of a stamped added bone with bones under it (it carries that bone's stamp: a chain can't be told from a re-parent
      of the copies -- Adopt Added Bones gives them their own identity), copies of a stamped bone none of which keeps
      its stamp name, a bone under a stub bone or under a refused bone.

    ``chains``: a copy of a stamped bone with bones under it is accepted too (Adopt Added Bones stamps the copies).

    Returns ``(accepted [(RigAdded, key, parent key, limb_default)], refused [(name, why)])``, parents before children."""
    table = [str(u) for u in table_uids]
    pos = {u: j for j, u in enumerate(table)}
    stubs = {str(s).lower() for s in stub_names}
    holders: Dict[str, List[RigAdded]] = {}
    for b in added:
        if b.uid is not None and not b.stub and not b.src_stamp:
            holders.setdefault(str(b.uid), []).append(b)
    ident: Dict[str, str] = {}           # rig name -> uid it is (its identity)
    refused: Dict[str, str] = {}
    for uid, bones in holders.items():
        if len(bones) == 1:
            ident[bones[0].name] = uid
            continue
        named = [b for b in bones if b.stamp is not None and b.name == str(b.stamp)]
        if len(named) == 1:
            ident[named[0].name] = uid
        else:
            for b in bones:
                refused[b.name] = (f"bone '{b.name}' was added: bones {', '.join(repr(x.name) for x in bones)} all carry "
                                   f"the stamp of added bone '{bones[0].stamp}' and none of them keeps that name: rename "
                                   "one back, or delete the copies")
    by_name = {b.name: b for b in added}
    depth: Dict[str, int] = {}

    def d(b, seen=()):
        if b.name in depth:
            return depth[b.name]
        p = by_name.get(b.parent)
        depth[b.name] = 0 if p is None or b.name in seen else d(p, seen + (b.name,)) + 1
        return depth[b.name]
    for b in added:
        d(b)
    accepted, key_of = [], {}
    extra = len(table)
    for b in sorted(added, key=lambda x: (depth[x.name], x.name)):
        if b.stub or b.name in refused:
            continue
        if b.src_stamp:
            refused[b.name] = (f"bone '{b.name}' was added (it carries a source bone's import stamp, from Duplicate or "
                               "Symmetrize): use Boner > Bones > Add Bone or Blender's Add / Extrude")
            continue
        if not b.parent:
            refused[b.name] = (f"bone '{b.name}' was added at the root: a creature skeleton has one root bone (the "
                               "source's); give it a parent")
            continue
        uid = ident.get(b.name)
        if uid is None and b.children and not chains and b.uid is not None:
            # it carries the stamp of an added bone another bone is: a copy with bones under it (an unstamped chain --
            # Blender's Add / Extrude -- is plain new bones and is taken as it is, C8.3)
            orig = next((nm for nm, u in ident.items() if u == str(b.uid)), None) or b.stamp
            refused[b.name] = (f"bone '{b.name}' is a copy of the added bone '{orig}' (Duplicate / Symmetrize) with "
                               f"{b.children} bone(s) under it, so the export can't tell a chain from a re-parent: Boner > "
                               "Bones > Adopt Added Bones gives the copies their own identity")
            continue
        if str(b.parent).lower() in stubs:
            refused[b.name] = (f"bone '{b.name}' was added under '{b.parent}', a stub bone (a stub limb's only bone, "
                               "written after every other bone): add it under the stub's host instead")
            continue
        if b.parent in source_of:
            pk = int(source_of[b.parent])
        elif b.parent in key_of:
            pk = key_of[b.parent]
        else:
            why = refused.get(b.parent)
            refused[b.name] = (f"bone '{b.name}' was added under '{b.parent}', " + (
                "an added bone the export refuses" if why or b.parent in by_name else "which is no bone the export writes"))
            continue
        if uid is not None and uid in pos:
            key = int(n_src) + pos[uid]
        else:
            key = int(n_src) + extra
            extra += 1
        key_of[b.name] = key
        accepted.append((b, key, pk, uid is None or uid not in pos))
    order = {b.name: i for i, b in enumerate(added)}
    return accepted, sorted(refused.items(), key=lambda kv: order.get(kv[0], 0))


def added_bone_renames(accepted, taken=()) -> List[Tuple[str, str]]:
    """``[(stamp name, current name)]`` of the accepted added bones (:func:`classify_added_bones` rows) renamed since Add
    Bone or Adopt Added Bones stamped them: the name the F-curves of an Action Blender's own rename left alone (one on no
    NLA strip) still key them by. Left out, so a path by that name stays the bone Blender plays it on: a stamp name some
    rig bone has now or another rename reads back to (``taken``: every rig bone's current name, the import names and the
    created stub bones' creation names; ignoring case -- the bone's own new name aside, for a change of case), and a
    stamp name two renamed bones share."""
    busy: Dict[str, int] = {}
    for t in taken:
        busy[str(t).lower()] = busy.get(str(t).lower(), 0) + 1
    pairs = [(str(ra.stamp), ra.name) for ra, _k, _p, _d in accepted
             if ra.uid is not None and ra.stamp is not None and str(ra.stamp) != ra.name]
    count: Dict[str, int] = {}
    for stamp, _n in pairs:
        count[stamp.lower()] = count.get(stamp.lower(), 0) + 1
    return [(s, n) for s, n in pairs
            if busy.get(s.lower(), 0) - (s.lower() == n.lower()) <= 0 and count[s.lower()] == 1]
