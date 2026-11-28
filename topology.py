"""Bpy-free: delete or re-parent SOURCE skeleton bones in the export model (:func:`apply_skeleton`); added bones, clips
(:func:`retrack_clips`) and IK chains (:func:`fix_ik_chains`) follow.

Kept bones keep their record bytes; records are re-sorted only to keep parents first (stub bones stay last).
:func:`topology_refusals` protects the root, looked-up names, stub bones, connection hosts and limb integrity.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from . import structure
except ImportError:                      # imported top-level (pure tests put ic_blender/ on sys.path)
    import structure  # type: ignore[no-redef]


@dataclass(frozen=True)
class BoneTopology:
    """Edits to the source bones: ``deleted`` source indices; ``parents`` ``((source index, new parent key), ...)`` where
    the key is a source index (< n_src) or an added bone's owner key (>= n_src). A child of a deleted bone is in
    ``parents`` (Blender hangs it from the deleted bone's parent)."""
    deleted: Tuple[int, ...] = ()
    parents: Tuple[Tuple[int, int], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.deleted or self.parents)

    @property
    def parent_map(self) -> Dict[int, int]:
        return {int(i): int(p) for i, p in self.parents}


def source_parents(src) -> List[Optional[int]]:
    return [None if b.parent is None or int(b.parent) < 0 else int(b.parent) for b in src.bones]


def kept_ancestor(parents, i, deleted) -> int:
    """The nearest ancestor of source bone ``i`` that is not deleted (-1 when none: a deleted root)."""
    gone = set(int(d) for d in deleted)
    p = parents[int(i)]
    while p is not None and int(p) in gone:
        p = parents[int(p)]
    return -1 if p is None else int(p)


def move_deleted_owners(owners, src, deleted) -> Tuple[np.ndarray, List[Tuple[int, int, int]]]:
    """Owner keys with every point of a deleted source bone moved to its nearest kept ancestor (its parent in the file:
    what a modder expects of a deleted bone's skin). Returns ``(owners, [(deleted index, target index, count)])``."""
    own = np.asarray(owners, dtype=np.int64).reshape(-1).copy()
    parents = source_parents(src)
    moved = []
    for d in sorted(int(x) for x in deleted):
        sel = own == d
        n = int(sel.sum())
        if not n:
            continue
        target = kept_ancestor(parents, d, deleted)
        if target < 0:
            raise ValueError(f"bone '{src.bones[d].name}' is the root bone and was deleted: {n} control points would have "
                             "no bone. Undo the delete")
        own[sel] = target
        moved.append((d, target, n))
    return own, moved


# ------------------------------------------------------------------------------------------------ refusals
@dataclass
class Rules:
    """What the delete / re-parent rules are checked against: the limb rows as exported (``limb_lists``: bone names per
    limb, source bones by their FILE names, added bones by name), ``limb_ids`` / ``limb_names``, ``hosts``
    ``[(connection name, host bone name)]``, ``stubs`` (source indices of the export's stub bones), and ``added``
    ``{added bone name: (its owner key, its parent key)}`` (a parent key: a source index or an added key)."""
    limb_names: List[str] = field(default_factory=list)
    limb_ids: List[int] = field(default_factory=list)
    limb_lists: List[List[str]] = field(default_factory=list)
    hosts: List[Tuple[str, str]] = field(default_factory=list)
    stubs: set = field(default_factory=set)
    added: Dict[str, Tuple[int, int]] = field(default_factory=dict)


def rules_of(src, limb_lists=None, limb_names=None, limb_ids=None, hosts=None, stubs=None, added=None) -> Rules:
    """A :class:`Rules` from the source's records where nothing else is given."""
    return Rules(
        limb_names=[str(l.name) for l in src.limbs] if limb_names is None else [str(x) for x in limb_names],
        limb_ids=[int(l.limb_id) for l in src.limbs] if limb_ids is None else [int(x) for x in limb_ids],
        limb_lists=[[str(b) for b in l.bone_names] for l in src.limbs] if limb_lists is None
        else [[str(b) for b in bl] for bl in limb_lists],
        hosts=[(str(c.name), str(c.bone_name)) for c in src.limb_connections] if hosts is None
        else [(str(a), str(b)) for a, b in hosts],
        stubs=set(structure.stub_bone_indices(src)) if stubs is None else {int(i) for i in stubs},
        added={} if added is None else {str(k): (int(v[0]), int(v[1])) for k, v in dict(added).items()})


def _lookup_uses(name) -> List[str]:
    try:
        from . import boner
    except ImportError:
        import boner  # type: ignore[no-redef]
    return [u for u in boner.hard_coded_uses(name) if not u.startswith("its place among the 18 bones")]


def _branch_problems(src, topo, rules, deleted_set) -> List[str]:
    """Limbs other than the Torso that stop being one bone and its children under the new parents (and were one in the
    source): sentences naming the re-parented bones in that limb."""
    try:
        from . import boner
    except ImportError:
        import boner  # type: ignore[no-redef]
    n = len(src.bones)
    sp = source_parents(src)
    pm = topo.parent_map
    # parent name of every bone of the export skeleton: source bones (kept) and added bones
    key_name = {i: str(b.name) for i, b in enumerate(src.bones)}
    key_name.update({int(k): nm for nm, (k, _pk) in rules.added.items()})
    parent_of: Dict[str, Optional[str]] = {}
    for i in range(n):
        if i in deleted_set:
            continue
        p = pm.get(i, sp[i])
        if p is not None and p < n and p in deleted_set:
            p = kept_ancestor(sp, p, deleted_set)
            p = None if p < 0 else p
        parent_of[str(src.bones[i].name).lower()] = None if p is None or p < 0 else key_name.get(int(p), "").lower()
    for nm, (_k, pk) in rules.added.items():
        parent_of[str(nm).lower()] = key_name.get(int(pk), "").lower()
    src_parent_of = {str(b.name).lower(): (None if sp[i] is None else str(src.bones[sp[i]].name).lower())
                     for i, b in enumerate(src.bones)}
    moved = {str(src.bones[i].name).lower() for i in pm}
    out = []
    for j, bl in enumerate(rules.limb_lists):
        cls = boner.class_of(rules.limb_ids[j]) if j < len(rules.limb_ids) else 0
        if cls == boner.TORSO_CLASS:
            continue
        inside = {str(b).lower() for b in bl if str(b).lower() in parent_of}
        if not inside:
            continue

        def roots(par, members):
            return sorted(b for b in members if par.get(b) not in members)
        added_low = {str(nm).lower() for nm in rules.added}
        now, was = roots(parent_of, inside), roots(src_parent_of, inside - added_low)   # was: the source's bones only
        if len(now) == 1 or (len(was) != 1 and len(now) <= len(was)):
            continue
        who = sorted(b for b in inside if b in moved) or now
        pretty = {str(b).lower(): str(b) for b in bl}
        out.append(f"re-parenting {', '.join(repr(pretty.get(b, b)) for b in who)} splits limb '{rules.limb_names[j]}' into "
                   f"{len(now)} branches (roots "
                   f"{', '.join(pretty.get(b, b) for b in now[:4])}{', ...' if len(now) > 4 else ''}): re-parent within the "
                   "limb, move the bone into its new parent's limb, or Undo")
    return out


def topology_refusals(src, topo: BoneTopology, rules: Optional[Rules] = None) -> List[str]:
    """Why the deletes / re-parents of ``topo`` can't be exported (sentences; empty = fine). ``rules`` None: the
    source's own records."""
    if not topo:
        return []
    rules = rules_of(src) if rules is None else rules
    n = len(src.bones)
    sp = source_parents(src)
    deleted = {int(d) for d in topo.deleted}
    out = []
    low_lists = [[str(b).lower() for b in bl] for bl in rules.limb_lists]
    for d in sorted(deleted):
        if not 0 <= d < n:
            out.append(f"deleted bone index {d} is outside the {n}-bone skeleton: re-import the creature")
            continue
        name = str(src.bones[d].name)
        if sp[d] is None:
            out.append(f"bone '{name}' is the root bone and was deleted: a creature skeleton needs its root. Undo the delete")
            continue
        uses = _lookup_uses(name)
        if uses:
            out.append(f"bone '{name}' was deleted, but the game looks '{name}' up by name ({'; '.join(uses)}): deleting it "
                       "breaks that in game. Undo the delete (skin nothing to it if it should move nothing)")
        if d in rules.stubs:
            limb = next((rules.limb_names[j] for j, bl in enumerate(low_lists) if name.lower() in bl), None)
            out.append(f"bone '{name}' was deleted, but it is a stub bone" + (f", the only bone of stub limb '{limb}'"
                                                                             if limb else "")
                       + " (the socket needs it): Undo the delete, or delete that limb and its connection first (Boner > "
                         "Limbs / Connections)")
        hosts = [cn for cn, hb in rules.hosts if str(hb).lower() == name.lower()]
        if hosts:
            out.append(f"bone '{name}' was deleted, but connection{'s' if len(hosts) != 1 else ''} "
                       f"{', '.join(repr(h) for h in hosts)} attach{'es' if len(hosts) == 1 else ''} to it: Undo the "
                       "delete, or first move the connection (Boner > Connections: Set, by the host bone)")
        only = [rules.limb_names[j] for j, bl in enumerate(low_lists) if bl == [name.lower()]]
        if only:
            out.append(f"bone '{name}' was deleted, but it is the only bone of limb '{only[0]}' (a limb with no bones is an "
                       "error in game): Undo, give the limb another bone first, or delete the limb")
    added_keys = {int(k) for k, _pk in rules.added.values()}
    name_of_key = {int(k): nm for nm, (k, _pk) in rules.added.items()}
    for i, p in topo.parents:
        i, p = int(i), int(p)
        if not 0 <= i < n or i in deleted:
            continue
        name = str(src.bones[i].name)
        if sp[i] is None:
            out.append(f"bone '{name}' is the root bone and was re-parented under "
                       f"'{src.bones[p].name if 0 <= p < n else name_of_key.get(p, p)}': the root has no parent. Undo")
            continue
        if p < 0:
            out.append(f"bone '{name}' was made a root bone: a creature skeleton has one root (the source's). Give it a "
                       "parent, or Undo")
            continue
        if p < n:
            if p in deleted:
                out.append(f"bone '{name}' was re-parented under '{src.bones[p].name}', which was deleted: Undo")
                continue
            if p in rules.stubs:
                out.append(f"bone '{name}' was re-parented under '{src.bones[p].name}', a stub bone (a stub limb's only "
                           "bone, written after every other bone): re-parent it under the stub's host instead")
            # a cycle: the new parent is the bone itself or one of its descendants (through the new parents)
            pm = topo.parent_map
            q, seen = p, set()
            while q is not None and q >= 0 and q < n and q not in seen:
                if q == i:
                    out.append(f"bone '{name}' was re-parented under '{src.bones[p].name}', which hangs from it: a cycle. "
                               "Undo")
                    break
                seen.add(q)
                q = pm.get(q, sp[q])
        elif p not in added_keys:
            out.append(f"bone '{name}' was re-parented under a bone the export does not write (key {p}): Undo, or adopt "
                       "that bone first (Boner > Bones > Adopt Added Bones)")
    out += _branch_problems(src, topo, rules, deleted)
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------------------------------------ the skeleton
@dataclass
class SkeletonEdit(structure.BoneInsert):
    """:class:`structure.BoneInsert` plus what the topology did: ``deleted`` (names), ``reparented`` ``[(name, old parent
    name, new parent name)]``, ``resorted`` (how many records moved to keep parents before children), ``moved_points``
    ``[(deleted name, target name, count)]`` (filled by the builder)."""
    deleted: List[str] = field(default_factory=list)
    reparented: List[Tuple[str, str, str]] = field(default_factory=list)
    resorted: int = 0
    moved_points: List[Tuple[str, str, int]] = field(default_factory=list)
    limbs: List[Tuple[str, str]] = field(default_factory=list)   # (deleted bone, limb it was taken off)
    ik: object = None                                   # IKReport
    clips: object = None                                # RetrackReport

    def owners(self, keys) -> np.ndarray:
        k = np.asarray(keys, dtype=np.int64).reshape(-1)
        gone = {key for key, i in self.index.items() if i < 0}
        if len(k) and gone and np.isin(k, sorted(gone)).any():
            bad = sorted({int(x) for x in np.unique(k) if int(x) in gone})
            raise ValueError(f"control points are skinned to deleted bone(s) {bad}: the builder moves them to the parent "
                             "first (move_deleted_owners)")
        return super().owners(k)

    def keys(self, count) -> List[int]:
        back = {i: key for key, i in self.index.items() if i >= 0}
        return [int(back.get(i, -1)) for i in range(int(count))]


def _order_fix(parents, priority) -> Optional[List[int]]:
    """A permutation (new position -> old index) with every parent before its children, moving as little as possible:
    the bones are emitted in ``priority`` order among those whose parent is already emitted (Kahn's algorithm with a
    heap). None when the order is already right; raises ValueError on a cycle or a second root."""
    n = len(parents)
    if all(p is None or p < i for i, p in enumerate(parents)):
        return None
    children: Dict[Optional[int], List[int]] = {}
    for i, p in enumerate(parents):
        children.setdefault(p, []).append(i)
    heap = [(priority[i], i) for i in children.get(None, [])]
    heapq.heapify(heap)
    out = []
    while heap:
        _pr, i = heapq.heappop(heap)
        out.append(i)
        for c in children.get(i, []):
            heapq.heappush(heap, (priority[c], c))
    if len(out) != n:
        raise ValueError("the skeleton has a cycle (a bone hangs from one of its own descendants): Undo the re-parent")
    return out


def apply_skeleton(m, added, n_src, stubs=None, topo: Optional[BoneTopology] = None) -> SkeletonEdit:
    """:func:`structure.insert_added_bones` with the source bones' deletes and re-parents of ``topo`` applied to the
    export model ``m`` (its bones still the source's, rest edits applied -- a re-parented bone's rest is already relative
    to its new parent). Returns a :class:`SkeletonEdit` whose ``index`` maps owner keys to file indices (-1 = deleted).
    Raises ValueError for a deleted root, a kept bone whose parent was deleted and not re-parented, a parent that is no
    bone of the export, or a cycle."""
    n = len(m.bones)
    if n != int(n_src):
        raise ValueError(f"bone topology: the export model has {n} bones, the source {n_src} (structure applied twice?)")
    if not topo:
        ins = structure.insert_added_bones(m, added, n_src, stubs)
        return SkeletonEdit(ins.n_src, ins.at, ins.index, ins.added, ins.warnings)
    deleted = sorted({int(d) for d in topo.deleted})
    pm = topo.parent_map
    src_names = [str(b.name) for b in m.bones]
    src_parents = source_parents(m)
    for d in deleted:
        if not 0 <= d < n:
            raise ValueError(f"deleted bone index {d} is outside the {n}-bone skeleton")
        if src_parents[d] is None:
            raise ValueError(f"bone '{src_names[d]}' is the root bone and was deleted: a creature skeleton needs its root. "
                             "Undo the delete")
    gone = set(deleted)
    for i in range(n):
        if i in gone or i in pm:
            continue
        p = src_parents[i]
        if p is not None and p in gone:
            raise ValueError(f"bone '{src_names[i]}': its parent '{src_names[p]}' was deleted and it was not re-parented "
                             "(Blender hangs a deleted bone's children from its parent): re-parent it, or Undo")
    # 1. source bones under SOURCE parents (added parents after the insertion gives them indices)
    for i, p in pm.items():
        if not 0 <= i < n or i in gone:
            continue
        if p < 0:
            raise ValueError(f"bone '{src_names[i]}' was made a root bone: a creature skeleton has one root. Give it a "
                             "parent, or Undo")
        if p < n:
            if p in gone:
                raise ValueError(f"bone '{src_names[i]}' was re-parented under '{src_names[p]}', which was deleted: Undo")
            m.bones[i].parent = int(p)
    # 2. the added bones
    ins = structure.insert_added_bones(m, added, n_src, stubs)
    out = SkeletonEdit(ins.n_src, ins.at, dict(ins.index), list(ins.added), list(ins.warnings))
    key_names = {k: str(m.bones[i].name) for k, i in out.index.items()}
    for i, p in pm.items():
        if not 0 <= i < n or i in gone or p < n:
            continue
        if p not in out.index:
            raise ValueError(f"bone '{src_names[i]}' was re-parented under a bone the export does not write (key {p}): "
                             "adopt that bone first (Boner > Bones > Adopt Added Bones), or Undo")
        m.bones[out.index[i]].parent = int(out.index[p])
    # 3. drop the deleted records (children were re-parented above), remapping every parent index
    if deleted:
        drop = sorted(out.index[d] for d in deleted)
        keep = [j for j in range(len(m.bones)) if j not in set(drop)]
        new_pos = {old: new for new, old in enumerate(keep)}
        for j in keep:
            b = m.bones[j]
            if b.parent is not None and int(b.parent) >= 0:
                if int(b.parent) not in new_pos:
                    raise ValueError(f"bone '{b.name}' hangs from deleted bone '{m.bones[int(b.parent)].name}': Undo")
                b.parent = new_pos[int(b.parent)]
        m.bones = [m.bones[j] for j in keep]
        out.index = {k: (new_pos[i] if i in new_pos else -1) for k, i in out.index.items()}
        out.at = new_pos.get(out.at, len(m.bones))
        out.deleted = [src_names[d] for d in deleted]
    # 4. parents before children: the least re-sort that does it
    parents = source_parents(m)
    roots = [i for i, p in enumerate(parents) if p is None]
    if len(roots) != 1:
        raise ValueError(f"the skeleton has {len(roots)} root bones ({', '.join(m.bones[r].name for r in roots[:4])}): a "
                         "creature skeleton has one root. Undo the re-parent")
    stub_pos = {out.index[s] for s in (stubs or ()) if 0 <= int(s) < n and out.index.get(int(s), -1) >= 0}
    stub_pos |= {j for j, b in enumerate(m.bones) if (int(b.meta0) & 0xFF) == structure.STUB_BYTE}
    key_of_pos = {i: k for k, i in out.index.items() if i >= 0}
    priority = [float(j) + (10 ** 6 if j in stub_pos else 0.0) for j in range(len(m.bones))]
    for j in range(len(m.bones)):
        if key_of_pos.get(j, 0) >= n:                    # an added bone: right before the first source bone under it
            kids = [c for c, p in enumerate(parents) if p == j and key_of_pos.get(c, n) < n]
            if kids:
                priority[j] = min(priority[c] for c in kids) - 0.5
    order = _order_fix(parents, priority)
    if order is not None:
        new_pos = {old: new for new, old in enumerate(order)}
        for b in m.bones:
            if b.parent is not None and int(b.parent) >= 0:
                b.parent = new_pos[int(b.parent)]
        m.bones = [m.bones[old] for old in order]
        out.index = {k: (new_pos[i] if i >= 0 else -1) for k, i in out.index.items()}
        out.at = new_pos.get(out.at, len(m.bones)) if out.added else len(m.bones)
        # how many SOURCE records left the source's relative order (an added bone slipping in between counts none)
        src_seq = [key_of_pos[old] for old in order if key_of_pos.get(old, n) < n]
        out.resorted = sum(1 for a, b in zip(src_seq, src_seq[1:]) if b < a)
    if m.skel_info is not None and int(m.skel_info) != len(m.bones) and int(m.skel_info) == n + len(out.added):
        m.skel_info = len(m.bones)
    for i, p in sorted(pm.items()):
        if 0 <= i < n and i not in gone:
            was = src_names[src_parents[i]] if src_parents[i] is not None else "(root)"
            out.reparented.append((src_names[i], was, key_names.get(int(p), "?")))
    return out


def drop_from_limbs(m, deleted_names) -> List[Tuple[str, str]]:
    """Take the deleted bones off the export model's limb bone lists (a list that changes becomes a new record; the
    others keep their bytes). Returns ``[(bone, limb)]`` of the entries taken off."""
    from ICParse.sgm.creature.model import Limb
    gone = {str(d).lower() for d in deleted_names}
    out = []
    if not gone:
        return out
    for j, l in enumerate(m.limbs):
        taken = [str(b) for b in l.bone_names if str(b).lower() in gone]
        if not taken:
            continue
        keep = [str(b) for b in l.bone_names if str(b).lower() not in gone]
        m.limbs[j] = Limb(name=str(l.name), limb_id=int(l.limb_id), prims=list(l.prims), bone_names=keep)
        out += [(b, str(l.name)) for b in taken]
    return out


# ------------------------------------------------------------------------------------------------ IK chains
@dataclass
class IKReport:
    rebuilt: List[str] = field(default_factory=list)      # "LEar: ear_L taken out"
    completed: List[str] = field(default_factory=list)    # "Neck: neck4 inserted"
    dropped: List[str] = field(default_factory=list)      # "LFLeg: ..."
    warnings: List[str] = field(default_factory=list)

    def text(self) -> str:
        parts = []
        if self.rebuilt:
            parts.append(f"{len(self.rebuilt)} rebuilt ({', '.join(self.rebuilt[:3])}{', ...' if len(self.rebuilt) > 3 else ''})")
        if self.completed:
            parts.append(f"{len(self.completed)} completed ({', '.join(self.completed[:3])}"
                         f"{', ...' if len(self.completed) > 3 else ''})")
        if self.dropped:
            parts.append(f"{len(self.dropped)} dropped ({', '.join(self.dropped[:3])}{', ...' if len(self.dropped) > 3 else ''})")
        return "; ".join(parts)


def _depths(parents) -> List[int]:
    out = [0] * len(parents)
    for i in range(len(parents)):
        d, q = 0, parents[i]
        while q is not None and q >= 0:
            d += 1
            q = parents[q]
        out[i] = d
    return out


def chain_path_problem(names, parents, chain_bones) -> Optional[str]:
    """Why ``chain_bones`` are not one path of the hierarchy (a bone, its child, its child's child, ...), or None:
    a name the skeleton lacks, a repeated bone, or bones in separate branches. Fewer than two bones is a chain too
    (stock has empty and one-bone chains)."""
    low = {str(n).lower(): i for i, n in enumerate(names)}
    ids = []
    for b in chain_bones:
        i = low.get(str(b).lower())
        if i is None:
            return f"names bone '{b}', which the skeleton lacks"
        ids.append(i)
    if len(set(ids)) != len(ids):
        return "lists a bone twice"
    if len(ids) < 2:
        return None
    depth = _depths(parents)
    ids.sort(key=lambda i: depth[i])
    for k in range(1, len(ids)):
        if parents[ids[k]] != ids[k - 1]:
            return (f"its bones are not one path of the hierarchy ('{names[ids[k]]}' does not hang from "
                    f"'{names[ids[k - 1]]}')")
    return None


def _complete_path(names, parents, chain_bones) -> Optional[List[int]]:
    """The hierarchy path from the chain's topmost bone down to its deepest bone when every chain bone lies on it
    (indices root-to-tip), else None."""
    low = {str(n).lower(): i for i, n in enumerate(names)}
    ids = [low[str(b).lower()] for b in chain_bones if str(b).lower() in low]
    if len(ids) < 2:
        return None
    depth = _depths(parents)
    top = min(ids, key=lambda i: depth[i])
    bottom = max(ids, key=lambda i: depth[i])
    path, q = [], bottom
    while q is not None and q >= 0:
        path.append(q)
        if q == top:
            break
        q = parents[q]
    if not path or path[-1] != top or not set(ids) <= set(path):
        return None
    return list(reversed(path))


def fix_ik_chains(m, deleted_names=(), reparented_names=()) -> IKReport:
    """Keep the model's IK chains consistent with a deleted / re-parented bone: a deleted bone comes off every chain that
    lists it (the chain stays, shorter -- stock has 0- and 1-bone chains); a chain that is no longer one hierarchy path
    is completed along the path from its topmost to its deepest bone (an inserted bone joins it, spelt as the skeleton
    spells it, in the chain's order: alphabetical when it was, else root-to-tip) or, when its bones now sit in separate
    branches, dropped with a warning. The IKCH chunk is rebuilt only when a chain changed."""
    from ICParse.sgm.creature.model import ik_chains, set_ik_chains
    rep = IKReport()
    chains = ik_chains(m)
    if not chains:
        return rep
    names = [str(b.name) for b in m.bones]
    parents = source_parents(m)
    gone = {str(d).lower() for d in deleted_names}
    changed, keep = False, []
    for ch in chains:
        bones = list(ch.bones)
        taken = [b for b in bones if str(b).lower() in gone]
        if taken:
            bones = [b for b in bones if str(b).lower() not in gone]
            rep.rebuilt.append(f"{ch.name}: {', '.join(taken)} taken out"
                               + (f" ({len(bones)} bone{'s' if len(bones) != 1 else ''} left)" if len(bones) < 2 else ""))
            changed = True
        why = chain_path_problem(names, parents, bones)
        if why is not None and not why.startswith("names bone"):
            path = _complete_path(names, parents, bones)
            if path is None:
                rep.dropped.append(ch.name)
                rep.warnings.append(f"IK chain '{ch.name}' dropped: {why} after the re-parent; add it again with the bones "
                                    "you want (Boner > Bones > IK Chains)")
                changed = True
                continue
            have = {str(b).lower() for b in bones}
            new = [names[i] for i in path if names[i].lower() not in have]
            alphabetical = [str(b).lower() for b in bones] == sorted(str(b).lower() for b in bones)
            spelt = {names[i].lower(): names[i] for i in path}
            full = [b for b in bones] + new
            bones = sorted(full, key=lambda b: str(b).lower()) if alphabetical else [names[i] for i in path]
            if not alphabetical:
                bones = [next((b for b in full if str(b).lower() == x.lower()), x) for x in bones]
            rep.completed.append(f"{ch.name}: {', '.join(spelt.get(x.lower(), x) for x in new)} inserted")
            changed = True
        if bones != list(ch.bones):
            ch.bones = bones
        keep.append(ch)
    if changed:
        set_ik_chains(m, keep)
    return rep


def ik_chain_problems(m) -> List[str]:
    """Validation: every IK chain must be one path of the hierarchy (0 of 905 stock chains break it)."""
    from ICParse.sgm.creature.model import ik_chains
    chains = ik_chains(m) or []
    names = [str(b.name) for b in m.bones]
    parents = source_parents(m)
    out = []
    for ch in chains:
        why = chain_path_problem(names, parents, ch.bones)
        if why:
            out.append(f"IK chain '{ch.name}' {why}: a chain is a bone and its descendants in one line (every stock chain "
                       "is); fix it in Boner > Bones > IK Chains")
    return out


# ------------------------------------------------------------------------------------------------ clips
@dataclass
class RetrackReport:
    clips: List[str] = field(default_factory=list)          # clips rebuilt
    dropped_tracks: int = 0                                 # tracks of deleted bones dropped
    retracked: int = 0                                      # tracks of re-parented bones re-expressed
    warnings: List[str] = field(default_factory=list)

    def text(self) -> str:
        if not self.clips:
            return ""
        what = [w for w, k in ((f"{self.dropped_tracks} track(s) of deleted bones dropped", self.dropped_tracks),
                               (f"{self.retracked} track(s) re-expressed under the new parent", self.retracked)) if k]
        return f"{len(self.clips)} kept clip(s) rebuilt ({'; '.join(what)}; the other tracks keep their bytes)"


def _continuous(q, ref):
    q = np.asarray(q, dtype=np.float64)
    return -q if float(np.dot(q, np.asarray(ref, dtype=np.float64))) < 0 else q


def _retrack_v7(tr, T, B_old, B_new, eps=1e-6):
    from ICParse.codecs import anim as ICanim
    from ICParse.codecs.pose import trs_from_local, v7_local
    times = sorted({float(k[0]) for k in tr.rot_keys} | {float(k[0]) for k in tr.trans_keys})
    if not times:
        return ICanim.v7_track(tr.bone_name, [], [])
    rot, trans, prev = [], [], None
    bt = B_new[:3, 3]
    old_rot = {float(k[0]): np.asarray(k[1:5], dtype=np.float64) for k in tr.rot_keys}
    moves_t = bool(tr.trans_keys)
    for t in times:
        L = v7_local(tr.rot_keys, tr.trans_keys, t, B_old)
        t3, q4, _s = trs_from_local(T @ L)
        q = np.asarray(q4, dtype=np.float64)
        q = _continuous(q, prev if prev is not None else old_rot.get(t, q))
        prev = q
        rot.append((t, float(q[0]), float(q[1]), float(q[2]), float(q[3])))
        trans.append((t, float(t3[0]), float(t3[1]), float(t3[2])))
        if np.abs(np.asarray(t3) - bt).max() > eps:
            moves_t = True
    return ICanim.v7_track(tr.bone_name, rot, trans if moves_t else [])


def _retrack_v1(tr, T, B_old):
    from ICParse.codecs import anim as ICanim
    from ICParse.codecs.pose import trs_from_local, v1_local

    def times_of(ch):
        return [0.0 if k[0] is None else float(k[0]) for k in ch.keys] if ch is not None else []
    times = sorted(set(times_of(tr.v1_loc)) | set(times_of(tr.v1_rot)) | set(times_of(tr.v1_scale)) | {0.0})
    loc, rot, scale, prev = [], [], [], None
    for t in times:
        L = v1_local(tr.v1_loc.keys if tr.v1_loc else None, tr.v1_rot.keys if tr.v1_rot else None,
                     tr.v1_scale.keys if tr.v1_scale else None, t, B_old)
        t3, q4, s3 = trs_from_local(T @ L)
        q = _continuous(q4, prev if prev is not None else q4)
        prev = q
        loc.append((t, tuple(float(x) for x in t3)))
        rot.append((t, tuple(float(x) for x in q)))
        scale.append((t, tuple(float(x) for x in s3)))
    return ICanim.v1_track(tr.bone_name, loc, rot, scale)


def retrack_clips(m, src, ins: SkeletonEdit, topo: BoneTopology) -> RetrackReport:
    """Rewrite the export model's kept clips for the deleted and re-parented source bones: a deleted bone's track is
    dropped; a re-parented bone's keys become ``B_new @ inv(B_old) @ L_old`` (the pose relative to the rest under the
    new parent: what Blender plays for the same Action keys). Only clips with such a track are rebuilt
    (``ICParse.codecs.anim`` from the decoded keys: every other track byte-identical, the bounds kept). Raises
    ValueError when a clip is kept as undecoded bytes."""
    from ICParse.codecs import anim as ICanim
    from ICParse.codecs.pose import bind_local_matrix
    rep = RetrackReport()
    if not topo:
        return rep
    n = len(src.bones)
    gone = {str(src.bones[d].name).lower() for d in topo.deleted if 0 <= int(d) < n}
    xf = {}
    for i, _p in topo.parents:
        i = int(i)
        fi = ins.index.get(i, -1)
        if not 0 <= i < n or fi < 0:
            continue
        B_old = bind_local_matrix(src.bones[i].transform)
        B_new = bind_local_matrix(m.bones[fi].transform)
        xf[str(src.bones[i].name).lower()] = (B_new @ np.linalg.inv(B_old), B_old, B_new)
    if not gone and not xf:
        return rep
    if getattr(m, "anim_raw", None):
        raise ValueError(f"{len(m.anim_raw)} clip(s) are kept as undecoded bytes, so their tracks can't follow a deleted or "
                         "re-parented bone: re-encode them (edit their Actions) or undo the change")
    out = []
    for a in m.animations:
        hit = [t for t in a.tracks if str(t.bone_name).lower() in gone or str(t.bone_name).lower() in xf]
        if not hit:
            out.append(a)
            continue
        lay = ICanim.layout_of(a)
        tracks = []
        for t in a.tracks:
            low = str(t.bone_name).lower()
            if low in gone:
                rep.dropped_tracks += 1
                continue
            if low in xf:
                T, B_old, B_new = xf[low]
                tracks.append(_retrack_v7(t, T, B_old, B_new) if lay == "v7" else _retrack_v1(t, T, B_old))
                rep.retracked += 1
                continue
            tracks.append(ICanim.v7_track(t.bone_name, t.rot_keys, t.trans_keys) if lay == "v7"
                          else ICanim.v1_track(t.bone_name, t.v1_loc.keys, t.v1_rot.keys, t.v1_scale.keys))
        if lay == "v7":
            out.append(ICanim.build_animation(a.name, a.frame_count, a.fps, tracks, layout="v7", model_name=m.name,
                                              bounds=(a.bbox1, a.bbox2)))
        else:
            out.append(ICanim.build_animation(a.name, a.frame_count, a.fps, tracks, layout="v1", name_pad=a.name_pad))
        rep.clips.append(str(a.name))
    m.animations = out
    return rep


# ------------------------------------------------------------------------------------------------ joint limits
# The BONE record's tail: joint_limit_min (x, y, z) then joint_limit_max (x, y, z), Euler angles in RADIANS, then the
# optional u32 limit flag (bit 0; present when more than 3 bytes remain), which ICParse keeps as a float-viewed word at
# index 6. The tail itself is optional: the reader takes the 24 limit bytes only when the chunk has >= 0x18 left.
# Radians on disk: the game's reader (the game and the Object Editor alike) multiplies all six by 180/pi before storing them; the
# store does no conversion itself and only SWAPS a pair whose
# min > max, stores min/max/midpoint at bone+0xe0/+0xec/+0xf8 and derives an unread 3-bit widest-axis mask at +0x104.
# So: write radians (what we do); in-memory values in the game and the editor are degrees. Nothing reads the fields back.
def apply_bone_limits(m, ins, limits) -> List[str]:
    """Write ``limits`` -- {owner key: [min x, y, z, max x, y, z, (flag word)]} -- into the export model's bones (by key
    through ``ins.index``), only where the values differ from the record's (an untouched bone keeps its bytes). A list
    longer or shorter than the record's tail is refused (the layout is per file). Returns the names rewritten."""
    out = []
    for key, vals in sorted((limits or {}).items()):
        fi = ins.index.get(int(key), -1)
        if fi < 0:
            continue                                     # a deleted bone's limits: nothing to write
        b = m.bones[fi]
        want = [float(x) for x in vals]
        have = [float(x) for x in b.tail]
        if len(want) != len(have):
            raise ValueError(f"joint limits of bone '{b.name}': {len(want)} values for a record holding {len(have)} "
                             "(6 limit angles" + (" + the limit flag word" if len(have) > 6 else "") + ")")
        if not all(np.isfinite(want)):
            raise ValueError(f"joint limits of bone '{b.name}' are not finite")
        if any(np.float32(a) != np.float32(c) for a, c in zip(want, have)):
            b.tail = want
            out.append(str(b.name))
    return out


# ------------------------------------------------------------------------------------------------ IK chain rows
@dataclass(frozen=True)
class IKRow:
    """An IK chain as the add-on edits it: ``src`` the source chain index (-1 = made in Blender), ``name``, ``flags`` /
    ``enabled`` (u16 words), ``bones`` the bone names in file order (FILE names of source bones; the export renames)."""
    src: int
    name: str
    flags: int
    enabled: int
    bones: Tuple[str, ...]


def source_ik_rows(src) -> Optional[List[IKRow]]:
    """The rows of an untouched creature (None: the file has no IKCH chunk)."""
    from ICParse.sgm.creature.model import ik_chains
    chains = ik_chains(src)
    if chains is None:
        return None
    return [IKRow(i, str(c.name), int(c.flags), int(c.enabled), tuple(str(b) for b in c.bones)) for i, c in enumerate(chains)]


def apply_ik_rows(m, src, rows, missing_ok=()) -> List[str]:
    """Build the export model's IKCH chunk from ``rows`` (:class:`IKRow`; an empty list writes an empty table). The
    chunk is rebuilt only when the rows differ from the source's chains (then the whole chunk is rewritten: it is one
    record). ``missing_ok``: bone names a chain may still list although the skeleton lacks them (deleted on the rig:
    :func:`fix_ik_chains` takes them off afterwards). Returns the readable changes (renamed, flags, bones, added,
    deleted, order) for the summary. Raises ValueError for a chain naming a bone the skeleton lacks, one whose bones are
    not one path of the hierarchy, a name the file can't hold, or words that are no u16."""
    from ICParse.sgm.creature.model import IKChain, ik_chains, set_ik_chains
    rows = list(rows)
    have = ik_chains(m)                                  # the export's chains so far (delete / re-parent fixes applied)
    src_chains = ik_chains(src) or []
    names = [str(b.name) for b in m.bones]
    low = {n.lower() for n in names}
    out = []
    for r in rows:
        why = structure._name_problem(r.name)
        if why:
            raise ValueError(f"IK chain name '{r.name}' {why}")
        if not (0 <= int(r.flags) <= 0xFFFF and 0 <= int(r.enabled) <= 0xFFFF):
            raise ValueError(f"IK chain '{r.name}': flags {r.flags} / enabled {r.enabled} must be 16-bit words")
        ok_missing = {str(b).lower() for b in missing_ok}
        missing = [b for b in r.bones if str(b).lower() not in low and str(b).lower() not in ok_missing]
        if missing:
            raise ValueError(f"IK chain '{r.name}' names bone{'s' if len(missing) != 1 else ''} "
                             f"{', '.join(repr(b) for b in missing[:4])}, which the skeleton lacks: fix the chain (Boner > "
                             "Bones > IK Chains) or Undo")
        present = [str(b) for b in r.bones if str(b).lower() in low]
        parents = source_parents(m)
        why = chain_path_problem(names, parents, present)
        # a chain listing a deleted bone, or one the re-parent fix can complete along the hierarchy, is fixed afterwards
        # (fix_ik_chains); one whose bones sit in separate branches is refused here
        if (why is not None and not any(str(b).lower() in ok_missing for b in r.bones)
                and _complete_path(names, parents, present) is None):
            raise ValueError(f"IK chain '{r.name}' {why}: a chain is a bone and its descendants in one line (every stock "
                             "chain is); fix it in Boner > Bones > IK Chains")
        out.append(IKChain(str(r.name), int(r.flags), int(r.enabled), [str(b) for b in r.bones]))
    same = (have is not None and len(have) == len(out)
            and all((a.name, a.flags, a.enabled, list(a.bones)) == (b.name, b.flags, b.enabled, list(b.bones))
                    for a, b in zip(have, out)))
    if same:
        return []
    set_ik_chains(m, out)
    changes = []
    ren, cls, bones = [], [], []
    for r in rows:
        if 0 <= r.src < len(src_chains):
            s = src_chains[r.src]
            if str(r.name) != s.name:
                ren.append(f"{s.name} -> {r.name}")
            if (int(r.flags), int(r.enabled)) != (int(s.flags), int(s.enabled)):
                cls.append(f"{r.name} {s.flags}/{s.enabled} -> {r.flags}/{r.enabled}")
            if [str(b).lower() for b in r.bones] != [str(b).lower() for b in s.bones]:
                bones.append(r.name)
    added = [r.name for r in rows if r.src < 0]
    kept = {r.src for r in rows if r.src >= 0}
    deleted = [c.name for i, c in enumerate(src_chains) if i not in kept]
    order = [r.src for r in rows if r.src >= 0] != sorted(r.src for r in rows if r.src >= 0)

    def few(xs):
        return ", ".join(xs[:3]) + (", ..." if len(xs) > 3 else "")
    for word, xs in (("renamed", ren), ("words", cls), ("bones", bones), ("added", added), ("deleted", deleted)):
        if xs:
            changes.append(f"{word} {len(xs)} ({few(xs)})")
    if order:
        changes.append("order changed")
    if not changes and have is None:
        changes.append(f"table written ({len(out)} chains; the source had none)")
    return changes or ["rewritten"]
