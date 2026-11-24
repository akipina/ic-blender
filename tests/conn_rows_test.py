"""Boner > Connections without Blender (increment B3): connection rows, the class mask, the Object Editor's connection
check, the connection rules and the connection-table migration.
    python blender_native/tests/conn_rows_test.py

Needs the base-game creatures (Assets/engine_stock) and the add-on stock (Assets/stock).
1. identity: every stock creature is byte-identical through structure.apply_connections with rows whose fields are "the
   source's" (None) and with every field explicit; an unchanged row keeps the private copy's record (never a parsed-source
   object, the source never changes); EVERY connection deleted and re-created from its corner loop at the same position
   (all rows new) is byte-identical too;
2. edits through core.build_export on the cheetah: rename / Parent / Child / host change exactly that LCON record;
   Delete Head's connection and Create it again (appended, from the stock corners picked scrambled and put back in stock
   order) gives the original records in the new order; a new record writes word0 / word1 = 0 / 0; an empty ring exports
   with a warning; the grown path remaps a created ring; unwritable rows raise (repeated / unknown source, new without a
   name, an unknown host, a name the file can't hold, a ring index out of the pool); export_summary names each change;
3. the class mask (id & 0xFF7FFFFF): ring_slot and RingPicker keep Mid and Mid 2 legs apart -- Tellurian bolas_spider's
   LMLeg / LM2Leg rings and a synthetic Mid 2 leg on the engine ant -- also without combotest; torso_centre ignores a
   limb whose low 16 bits read 0x800;
4. ICParse.validate.check_oe_structure (connection half) equals an independent re-implementation of the Object Editor's
   connection check (FUN_00421569 / FUN_0059049e, the wf11 oracle) on both corpora: only the snowy owl's two antler rings
   warn; every message kind on an edited cheetah;
5. the rules: the default host (the parent of the child limb's root bone, else the stock host, always in the parent
   limb) on the stock corpora, names, limbs without a connection, the re-class plan (a stock limb's connection follows;
   no class / two connections / a parent class refused), NoSwap not a connection box, corner counts, status words;
6. the store: an object journaled before the connection rows (two generations without the class / identity keys) is
   upgraded on its first sync; Create Connection journals a new generation; Undo into each pre-upgrade generation keeps
   the classes (and exports byte-identical), Redo brings the new connection back, a memfile-restored pre-upgrade object
   finds it in the session copy; without the backfill the same Undo drops the classes (the guard is not vacuous).
"""
import copy
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import core  # noqa: E402
import boner  # noqa: E402
import store  # noqa: E402
import structure  # noqa: E402
import limb_rows_test as LR  # noqa: E402  (corpus helpers, FakeObj)
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse import validate as ICval  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.iff.document import IffDocument  # noqa: E402
from ICParse.sgm.creature import structs as CS  # noqa: E402
from ICParse.sgm.creature.model import LimbConnection, ring_corners, ring_pairs  # noqa: E402

REPO = LR.REPO
ENGINE, ADDON = LR.ENGINE, LR.ADDON
CHEETAH, SNAIL = LR.CHEETAH, LR.SNAIL
ANT = os.path.join(REPO, "Assets", "engine_stock", "ant.sgm")
BOLAS = os.path.join(REPO, "Assets", "stock", "bolas_spider.sgm")
OWL = os.path.join(REPO, "Assets", "engine_stock", "snowy_owl.sgm")
M = 0xFF7FFFFF
say = LR.say


def row_of(i, c, explicit=True, src=None):
    """A connection row as the add-on stores it (its corner loop, else the ints verbatim)."""
    corners = ring_corners(c.vertex_indices)
    kw = dict(index=i, bone=c.bone_name, src=i if src is None else src)
    if explicit:
        kw.update(name=c.name, flags1=int(c.flags1), flags2=int(c.flags2))
    return core.RingEdit(corners=corners, **kw) if corners is not None else core.RingEdit(raw=list(c.vertex_indices), **kw)


def lcon_words(data):
    """(word0, word1) of every LCON record in the bytes (the model does not carry them)."""
    doc = IffDocument.from_bytes(bytes(data))
    return [(int(c.word0), int(c.word1)) for c in CS.LCON.parse(doc.find("LCON").raw).connections]


# ------------------------------------------------------------------------------------------------------------ 1
def identity():
    n = e = 0
    for p, raw, src in LR.creatures(ENGINE + ADDON):
        e += p in ENGINE
        for rows in ([row_of(i, c, explicit=False) for i, c in enumerate(src.limb_connections)],
                     [row_of(i, c) for i, c in enumerate(src.limb_connections)]):
            m = SP.read(p)
            copies = list(m.limb_connections)
            srcs, warns = structure.apply_connections(m, src, rows)
            assert SP.write(m) == raw and srcs == list(range(len(rows))) and not warns, os.path.basename(p)
            assert all(a is b for a, b in zip(m.limb_connections, copies)), "an unchanged row did not keep the copy's record"
            assert not any(c is s for c in m.limb_connections for s in src.limb_connections)
        # every connection deleted and re-created from its corner loop at the same position
        m = SP.read(p)
        rows = [row_of(i, c, src=-1) for i, c in enumerate(src.limb_connections)]
        srcs, _w = structure.apply_connections(m, src, rows)
        out = SP.write(m)
        assert out == raw and srcs == [-1] * len(rows), os.path.basename(p)
        assert not any(c is s for c in m.limb_connections for s in copies_of(src)), "a re-created record is a copy"
        assert set(lcon_words(out)) <= {(0, 0)}
        # a renamed build changes the bytes and leaves the source alone
        m = SP.read(p)
        rows = [row_of(i, c) for i, c in enumerate(src.limb_connections)]
        if rows:
            rows[0].name = rows[0].name + "X"
            structure.apply_connections(m, src, rows)
            assert SP.write(m) != raw and SP.write(src) == raw
        n += 1
    assert n >= 110 and e == 61, (n, e)
    say(f"1 IDENTITY: {n} stock creatures ({e} base game) byte-identical through connection rows (None fields and "
        "explicit); unchanged rows keep the copy's record, never the source's; every connection re-created at its "
        "position byte-identical (word0/word1 0/0); a rename leaves the source alone")


def copies_of(src):
    return list(src.limb_connections)


# ------------------------------------------------------------------------------------------------------------ 2
def state(path, **kw):
    d = core.load_creature(path)
    return core.EditState(source_path=path, verts=d.verts, **kw)


def build(path, rows, **kw):
    b = core.build_export(state(path, connections=rows, **kw))
    return b, SP.write(b.model)


def rec(c):
    return (c.name, int(c.flags1), int(c.flags2), list(c.vertex_indices), c.bone_name)


def refused(path, rows, words, **kw):
    try:
        build(path, rows, **kw)
    except ValueError as ex:
        assert all(w in str(ex) for w in words), (words, str(ex))
        return
    raise AssertionError(f"not refused: {words}")


def edits():
    raw = open(CHEETAH, "rb").read()
    src = SP.read(CHEETAH)
    base = [row_of(i, c) for i, c in enumerate(src.limb_connections)]
    b, out = build(CHEETAH, base)
    assert out == raw and core.export_summary(b).parts == []
    head = next(i for i, c in enumerate(src.limb_connections) if c.flags2 & M == 0x40)
    lf = next(i for i, c in enumerate(src.limb_connections) if c.flags2 & M == 0x25)

    def one(i, **kw):
        rows = copy.deepcopy(base)
        for k, v in kw.items():
            setattr(rows[i], k, v)
        b, out = build(CHEETAH, rows)
        m = SP.read(out)
        want = [rec(c) for c in src.limb_connections]
        return b, out, m, want

    b, out, m, want = one(head, name="Cheetah_Noggin")
    want[head] = ("Cheetah_Noggin",) + want[head][1:]
    assert [rec(c) for c in m.limb_connections] == want
    assert core.export_summary(b).parts == ["connections: renamed 1 (Cheetah_Head -> Cheetah_Noggin)"], \
        core.export_summary(b).parts
    assert len(out) == len(raw) + len("Noggin") - len("Head") and SP.write(b.source) == raw
    b, out, m, want = one(lf, flags1=0x40, flags2=0x800025)
    want[lf] = (want[lf][0], 0x40, 0x800025) + want[lf][3:]
    assert [rec(c) for c in m.limb_connections] == want and len(out) == len(raw)
    assert core.export_summary(b).parts == ["connections: class 2 (Cheetah_LFLeg parent 0x800 -> 0x40, Cheetah_LFLeg "
                                            "child 0x25 -> 0x800025)"], core.export_summary(b).parts
    b, out, m, want = one(lf, bone="spine2")
    want[lf] = want[lf][:4] + ("spine2",)
    assert [rec(c) for c in m.limb_connections] == want
    assert core.export_summary(b).parts == ["rings: 1 (Cheetah_LFLeg bone)"], core.export_summary(b).parts

    # Delete Head's connection, Create it again (appended), its ring picked scrambled and put in the stock order
    rows = [r for i, r in enumerate(copy.deepcopy(base)) if i != head]
    b, out = build(CHEETAH, rows)
    assert [c.name for c in SP.read(out).limb_connections] == [c.name for i, c in enumerate(src.limb_connections) if i != head]
    assert core.export_summary(b).parts == ["connections: deleted 1 (Cheetah_Head)"], core.export_summary(b).parts
    stock = ring_corners(src.limb_connections[head].vertex_indices)
    d = core.load_creature(CHEETAH)
    pl = core._patch_limb_of(src)
    ids = [l.limb_id for l in src.limbs]
    pk = core.RingPicker(d.patches, pl, ids, 0x800, 0x40, name="Cheetah_Head")
    scrambled = stock[::-1]
    scrambled = scrambled[2:] + scrambled[:2]
    assert pk.set_sequence(scrambled) is None, pk.message
    import combocore
    if combocore.available():
        world = core.blender_to_sgm(d.verts)
        tc = combocore.torso_centre(world, d.patches, pl, ids)
        dirs, _names = combocore.partner_ring_dirs(os.path.join(REPO, "Assets", "engine_stock"), pk.slot_key, "cheetah")
        corners, notes = pk.finish(lambda c: combocore.ring_fix(world[list(c)], c, pk.slot_key, tc, dirs))
        assert corners == stock, (corners, stock, notes)
    else:
        corners = stock
    sk = [bb.name for bb in src.bones]
    parents = [-1 if bb.parent is None else bb.parent for bb in src.bones]
    host, how = boner.default_host(0x40, src.limbs[ids.index(0x40)].bone_names,
                                   src.limbs[ids.index(0x800)].bone_names, sk, parents)
    assert host == src.limb_connections[head].bone_name, (host, how)
    new = core.RingEdit(index=len(rows), bone=host, corners=corners, src=-1, name="Cheetah_Head", flags1=0x800, flags2=0x40)
    b, out = build(CHEETAH, rows + [new])
    m = SP.read(out)
    assert sorted(rec(c) for c in m.limb_connections) == sorted(rec(c) for c in src.limb_connections)
    assert rec(m.limb_connections[-1]) == rec(src.limb_connections[head]) and lcon_words(out)[-1] == (0, 0)
    assert core.export_summary(b).parts == ["connections: added 1 (Cheetah_Head); deleted 1 (Cheetah_Head)"], \
        core.export_summary(b).parts
    assert len(out) == len(raw)
    # moved back to its position through a source row: the original bytes
    b, out = build(CHEETAH, rows[:head] + [base[head]] + rows[head:])
    assert out == raw

    # an empty ring (Create before Pick Ring) exports with a warning
    new2 = core.RingEdit(index=len(base), bone="spine3", corners=[], src=-1, name="Cheetah_LMLeg", flags1=0x800,
                         flags2=0x20021)
    b, out = build(CHEETAH, base + [new2])
    assert SP.read(out).limb_connections[-1].vertex_indices == [] and any("has no ring" in w for w in b.warnings), b.warnings
    assert any("Invalid connection info for connection 'Cheetah_LMLeg'" in x and "is 0, and must be 4 or 6" in x
               for x in ICval.check_oe_structure(SP.read(out)))
    empty = [x for x in ICval.check_model(SP.read(out)) if "Cheetah_LMLeg" in x]   # the OE loads it: not blocking
    assert len(empty) == 1 and empty[0].startswith("WARN connection Cheetah_LMLeg: has no ring yet") and \
        not core.blocking(empty), empty

    # the grown path: one unused point more (sorted before every bone range) remaps a created ring
    d = core.load_creature(CHEETAH)
    owners = np.asarray(R.vertex_bone_owners(src), dtype=np.int64)
    gverts = np.concatenate([d.verts, d.verts[:1] + 0.5])
    kw = dict(patches=d.patches, vert_owner=np.concatenate([owners, [-1]]), patch_limb=pl, patch_uv=d.patch_uv,
              patch_material=d.patch_mat, patch_type=np.array([pp.type for pp in src.patches]))
    rows = [r for i, r in enumerate(copy.deepcopy(base)) if i != head] + [new]
    rows[0].name = "Renamed"
    bg = core.build_export(core.EditState(source_path=CHEETAH, verts=gverts, connections=rows, **kw))
    assert bg.grown
    mg = bg.model
    assert rec(mg.limb_connections[-1])[3] == [int(bg.o2n[v]) for v in ring_pairs(corners)]
    assert mg.limb_connections[0].name == "Renamed" and bg.conn_src[-1] == -1
    parts = core.export_summary(bg).parts
    assert "connections: renamed 1 (Cheetah_Torso -> Renamed); added 1 (Cheetah_Head); deleted 1 (Cheetah_Head)" in parts \
        or any(p.startswith("connections: renamed 1") and "added 1 (Cheetah_Head)" in p for p in parts), parts

    # unwritable rows
    rep = copy.deepcopy(base); rep[1].src = 0
    refused(CHEETAH, rep, ("both come from source connection 0",))
    unk = copy.deepcopy(base); unk[0].src = 99
    refused(CHEETAH, unk, ("comes from source connection 99",))
    nameless = core.RingEdit(index=0, bone="spine3", corners=[], src=-1)
    refused(CHEETAH, base + [nameless], ("is new but has no name or classification",))
    ghost = core.RingEdit(index=0, bone="no_such_bone", corners=[], src=-1, name="X", flags1=0x800, flags2=0x20021)
    refused(CHEETAH, base + [ghost], ("host bone 'no_such_bone' is not in the skeleton",))
    for bad, words in (("Kopf_ü", ("outside plain ASCII",)), ("A" * 64, ("is 64 characters",))):
        r2 = copy.deepcopy(base); r2[0].name = bad
        refused(CHEETAH, r2, words)
    far = copy.deepcopy(base); far[head] = core.RingEdit(index=head, bone=far[head].bone, corners=[10 ** 6, 1, 2, 3],
                                                         src=head)
    refused(CHEETAH, far, ("ring index out of the",))
    say("2 EDITS: rename / Parent+Child / host change only their record; Head deleted then created from a scrambled pick "
        "(ring fixed to the stock order, default host neck1) = the original records appended, back in place = the "
        "source; empty ring warned (OE 'is 0'); grown path remaps a created ring; 6 unwritable rows refused")


# ------------------------------------------------------------------------------------------------------------ 3
def class_mask():
    assert core.ring_slot(0x820021)[:2] == ("lmleg", "L Mid Leg") and core.ring_slot(0x20022)[0] == "rmleg"
    assert core.ring_slot(0x840021)[:2] == ("lm2leg", "L Mid 2 Leg") and core.ring_slot(0x40022)[2] == 4
    assert core.ring_slot(0x21)[0] is None and core.ring_slot(0x10001)[0] is None
    # without combotest: ICParse's rulebook, the same class keys
    saved = {k: sys.modules.get(k) for k in ("combotest.slots",)}
    sys.modules["combotest.slots"] = None
    try:
        assert core.ring_slot(0x840021)[0] == "lm2leg" and core.ring_slot(0x820021)[0] == "lmleg"
        assert core.ring_slot(0x40)[2] == 6 and core.ring_slot(0x21)[0] is None
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def picker_counts(path_or_model):
        m = SP.read(path_or_model) if isinstance(path_or_model, str) else path_or_model
        P = np.array([list(pp.control_points) for pp in m.patches], dtype=np.int64)
        pl = core._patch_limb_of(m)
        ids = [l.limb_id for l in m.limbs]
        out = {}
        for c in m.limb_connections:
            if c.flags2 & M in (0x20021, 0x40021, 0x20022, 0x40022):
                pk = core.RingPicker(P, pl, ids, c.flags1, c.flags2, name=c.name)
                want = sum(len(l.prims) for l in m.limbs if l.limb_id & M == c.flags2 & M)
                out[c.flags2 & M] = (pk.slot_key, pk.slot_patches, want, pk.placement_problem(ring_corners(c.vertex_indices)))
        return out

    if os.path.exists(BOLAS):
        got = picker_counts(BOLAS)
        assert set(got) == {0x20021, 0x40021, 0x20022, 0x40022}, got
        for cls, (key, n, want, where) in got.items():
            assert n == want and n > 0 and where is None, (hex(cls), key, n, want, where)
        assert got[0x20021][0] == "lmleg" and got[0x40021][0] == "lm2leg"
        assert got[0x20021][1] != got[0x20021][1] + got[0x40021][1]
    ant = SP.read(ANT)
    mids = [l for l in ant.limbs if l.limb_id & M == 0x20021]
    torso = next(l for l in ant.limbs if l.limb_id == 0x800)
    lend = sorted(torso.prims)[:3]                                  # three torso patches become a Mid 2 leg
    torso.prims = [q for q in torso.prims if q not in lend]
    from ICParse.sgm.creature.model import Limb
    ant.limbs.append(Limb(name="Ant_LM2Leg", limb_id=0x840021, prims=lend, bone_names=list(mids[0].bone_names[:1])))
    lc = next(c for c in ant.limb_connections if c.flags2 & M == 0x20021)
    ant.limb_connections.insert(0, LimbConnection("Ant_LM2Leg", 0x800, 0x40021, list(lc.vertex_indices), lc.bone_name))
    got = picker_counts(ant)
    assert got[0x40021][1] == 3 and got[0x20021][1] == len(mids[0].prims) and got[0x40021][0] == "lm2leg", got
    # under the old low-16 mask both would have counted every mid-leg patch
    assert got[0x40021][1] + got[0x20021][1] == 3 + len(mids[0].prims)
    import combocore
    if combocore.available():
        m = SP.read(CHEETAH)
        world = R.world_vertices(m)
        P = np.array([list(pp.control_points) for pp in m.patches], dtype=np.int64)
        pl = core._patch_limb_of(m)
        ids = [l.limb_id for l in m.limbs]
        tc = combocore.torso_centre(world, P, pl, ids)
        fake = [0x10800 if i != ids.index(0x800) else x for i, x in enumerate(ids)]
        fake.insert(0, 0x10800)                                     # a limb whose low 16 bits read 0x800, listed first
        tc2 = combocore.torso_centre(world, P, np.where(pl >= 0, pl + 1, pl), fake)
        assert np.allclose(tc, tc2), (tc, tc2)
    say("3 CLASS MASK: ring_slot Mid / Mid 2 distinct (and without combotest); bolas_spider's 4 mid-leg rings each see "
        "only their own limb's patches; a synthetic Mid 2 leg on the ant keeps its 3 patches apart; torso_centre skips "
        "a 0x10800 limb")


# ------------------------------------------------------------------------------------------------------------ 4
def oe_oracle(m):
    """The connection half of wf11 oecheck.py (FUN_00421569 + FUN_0059049e), independent of ICParse: message kinds."""
    out = []
    L = m.limbs
    bl = {bb.name.lower() for bb in m.bones}
    for c in m.limb_connections:
        n = len(c.vertex_indices) // 2
        if n not in (4, 6):
            out.append("edges")
        f2, f1 = c.flags2 & M, c.flags1 & M
        if f2 == 0:
            out.append("no child")
        elif not any(l.limb_id & M == f2 for l in L):
            out.append("child missing")
        if f1 == 0:
            out.append("no parent")
        elif not any(l.limb_id & M == f1 for l in L):
            out.append("parent missing")
        if not c.bone_name:
            out.append("no bone")
        elif c.bone_name.lower() not in bl:
            out.append("bone not in skeleton")
        else:
            for l in L:
                if l.limb_id & M == f1 and c.bone_name.lower() not in [x.lower() for x in l.bone_names]:
                    out.append("bone not in parent")
    return out


def _kind(line):
    for key, words in (("edges", "Invalid connection info"), ("no child", "no child limb classification"),
                       ("child missing", "Child limb of class"), ("no parent", "no parent limb classification"),
                       ("parent missing", "Parent limb of class"), ("no bone", "No parent bone specified"),
                       ("bone not in skeleton", "not found in skeleton"), ("bone not in parent", "not found in parent limb")):
        if words in line:
            return key
    raise AssertionError(line)


def oe_check():
    warned = {}
    for corpus, paths in (("E", ENGINE), ("T", ADDON)):
        for p, raw, m in LR.creatures(paths):
            if corpus == "T" and os.path.basename(p) in LR.TEST_FILES:
                continue
            got = [x for x in ICval.check_oe_structure(m) if "connection '" in x]
            assert [_kind(x) for x in got] == oe_oracle(m), (os.path.basename(p), got, oe_oracle(m))
            if got:
                warned[(corpus, os.path.basename(p))] = got
    assert set(warned) == {("E", "snowy_owl.sgm")}, warned
    assert warned[("E", "snowy_owl.sgm")] == [
        "WARN oe: Child limb of class '0x10202 (Right Top)' was not found for connection 'S_Owl_RAntler1'",
        "WARN oe: Child limb of class '0x10201 (Left Top)' was not found for connection 'S_Owl_LAntler1'"], warned
    m = SP.read(CHEETAH)
    C = m.limb_connections
    C[0].vertex_indices = C[0].vertex_indices[:10]
    C[1].flags2, C[2].flags2, C[3].flags1, C[4].flags1 = 0x800000, 0x301 | 0x40000, 0, 0x8000
    C[5].bone_name, C[6].bone_name, C[7].bone_name = "", "no_bone", "jaw1"
    got = [x for x in ICval.check_oe_structure(m) if "connection '" in x]
    assert [_kind(x) for x in got] == oe_oracle(m) and len(got) == 8, got
    for words in (f"Invalid connection info for connection '{C[0].name}', error: 'The number of edges in the connection "
                  "is 5, and must be 4 or 6.'",
                  f"There is no child limb classification for connection '{C[1].name}'",
                  f"Child limb of class '0x40301 (Left Middle2 Top Wing)' was not found for connection '{C[2].name}'",
                  f"There is no parent limb classification for connection '{C[3].name}'",
                  f"Parent limb of class '0x8000 (Fin)' was not found for connection '{C[4].name}'",
                  f"No parent bone specified for connection '{C[5].name}'",
                  f"Parent bone 'no_bone' for connection '{C[6].name}' not found in skeleton",
                  f"Parent bone 'jaw1' for connection '{C[7].name}' not found in parent limb"):
        assert f"WARN oe: {words}" in got, (words, got)
    assert not core.blocking(got)
    say("4 OE CONNECTION CHECK: = the oracle on both corpora (only snowy_owl's two antler rings warn, base game); every "
        "message kind on an edited cheetah, none blocking")


# ------------------------------------------------------------------------------------------------------------ 5
def rules():
    stats = {"E": [0, 0, 0], "T": [0, 0, 0]}
    dparent = [0, 0]
    for corpus, paths in (("E", ENGINE), ("T", ADDON)):
        for p, raw, m in LR.creatures(paths):
            sk = [bb.name for bb in m.bones]
            par = [-1 if bb.parent is None else bb.parent for bb in m.bones]
            ids = [l.limb_id for l in m.limbs]
            assert boner.limbs_without_connection(ids, [c.flags2 for c in m.limb_connections]) == [], os.path.basename(p)
            assert boner.unmirrored_left(ids) == [], os.path.basename(p)
            for c in m.limb_connections:
                child = [l for l in m.limbs if l.limb_id & M == c.flags2 & M]
                parent = [l for l in m.limbs if l.limb_id & M == c.flags1 & M]
                if child:
                    dparent[0] += 1
                    dparent[1] += boner.default_parent(c.flags2, child[0].bone_names, sk, par, ids,
                                                       [l.bone_names for l in m.limbs]) == c.flags1 & M
                if not child or not parent:
                    continue
                host, how = boner.default_host(c.flags2, child[0].bone_names, parent[0].bone_names, sk, par)
                st = stats[corpus]
                st[0] += 1
                if host is not None:
                    st[1] += host == c.bone_name
                    assert host in parent[0].bone_names, (os.path.basename(p), c.name, host)    # the list's spelling
                    assert boner.host_refusal(host, c.flags1, sk, ids, [l.bone_names for l in m.limbs]) is None
                else:
                    st[2] += 1
    assert dparent[0] >= 1400 and dparent[1] == dparent[0], dparent        # Create's Auto Parent = the file's, always
    # the root-parent rule 718/728 base game; the rest fall to the stock host or another parent-limb bone
    assert stats["E"][0] == 728 and stats["E"][1] >= 718 and stats["E"][2] == 0, stats
    assert stats["T"][2] == 0 and stats["T"][1] >= 600, stats
    ch = SP.read(CHEETAH)
    names = [c.name for c in ch.limb_connections]
    assert boner.default_conn_name(names, "Cheetah_LMLeg") == "Cheetah_LMLeg"
    assert boner.default_conn_name(names, "cheetah_head") == "cheetah_head_2"
    assert len(boner.default_conn_name(["A" * 63], "a" * 63)) == 63
    E = lambda n, o: boner.limb_name_error(n, o, "connection")
    assert "a connection needs a name" in E(" ", []) and "another connection is already called 'Cheetah_Head'" in \
        E("CHEETAH_HEAD", names) and "at most 63" in E("x" * 64, []) and E("Cheetah_Neck", names) is None
    ids = [l.limb_id for l in ch.limbs]
    f1 = [c.flags1 for c in ch.limb_connections]
    f2 = [c.flags2 for c in ch.limb_connections]
    lt = ids.index(0x301)
    ci = f2.index(0x301)
    assert boner.class_change_plan(lt, ids, 0x501, names, f1, f2) == (None, [(ci, 0x501)])     # LTWing -> Side: follows
    arm = ids.index(0x15)
    carm = f2.index(0x15)
    assert boner.class_change_plan(arm, ids, 0x16, names, f1, f2) == (None, [(carm, 0x16)])
    f2n = list(f2); f2n[carm] = 0x800015                                               # its NoSwap copy is kept
    assert boner.class_change_plan(arm, ids, 0x5, names, f1, f2n) == (None, [(carm, 0x800005)])
    why, fol = boner.class_change_plan(arm, ids, 0x0, names, f1, f2)
    assert fol == [] and "not to no class" in why, why
    why, fol = boner.class_change_plan(ids.index(0x800), ids, 0x0, names, f1, f2)
    assert fol == [] and "its parent" in why, why
    f2d = list(f2) + [0x15]
    why, fol = boner.class_change_plan(arm, ids, 0x16, names + ["Dup"], f1 + [0x800], f2d)
    assert fol == [] and "2 connections carry that class" in why, why
    assert boner.class_change_plan(arm, ids, 0x800015, names, f1, f2) == (None, [])    # NoSwap: same class
    dup = ids + [0x15]
    assert boner.class_change_plan(arm, dup, 0x16, names, f1, f2) == (None, [])        # another limb keeps 0x15
    assert boner.class_change_plan(arm, dup, 0x16, names, f1, f2, "cheetah_lfarm") == (None, [(carm, 0x16)])  # its own
    # tick Right, tick Left again: the identity, although the first step duplicated 0x16 (limbs and connections)
    lnames = [l.name for l in ch.limbs]
    ids1, f21 = list(ids), list(f2)
    for new_id in (0x16, 0x15, 0x5, 0x15):
        why, fol = boner.class_change_plan(arm, ids1, new_id, names, f1, f21, lnames[arm])
        assert why is None and len(fol) == 1 and fol[0][0] == carm, (hex(new_id), why, fol)
        ids1[arm], f21[carm] = new_id, fol[0][1]
    assert ids1 == ids and f21 == f2
    # a connection named otherwise, with another limb keeping the class: a plain change
    assert boner.class_change_plan(arm, dup, 0x16, ["X"] * len(names), f1, f2, lnames[arm]) == (None, [])
    horse = SP.read(LR.HORSE)
    hids = [l.limb_id for l in horse.limbs]
    why, fol = boner.class_change_plan(hids.index(0x40), hids, 0x80,
                                       [c.name for c in horse.limb_connections],
                                       [c.flags1 for c in horse.limb_connections], [c.flags2 for c in horse.limb_connections])
    assert fol == [] and why, why                                                      # the antler rings' parent
    try:
        boner.toggle_conn_flag(0x25, boner.NOSWAP)
    except ValueError as ex:
        assert "belongs on the limb" in str(ex)
    else:
        raise AssertionError("NoSwap toggled on a connection")
    assert boner.toggle_conn_flag(0x25, 0x8) == 0x29 and boner.toggle_conn_flag(0x820021, 0x40000) == 0x840021
    assert boner.NOSWAP not in {f.bit for f in boner.CONN_FLAGS} and len(boner.CONN_FLAGS) == 15
    assert boner.child_class_text(0x800025) == "L Front Leg (stored with Non-Swappable, ignored on load)"
    assert [boner.ring_corner_count(x) for x in (0x40, 0x80, 0x800080, 0x25, 0x40021, 0)] == [6, 6, 6, 4, 4, 4]
    sk = [bb.name for bb in ch.bones]
    bl = [l.bone_names for l in ch.limbs]
    brief = [(c.name, c.bone_name, len(c.vertex_indices) // 2, c.flags1, c.flags2) for c in ch.limb_connections]
    assert [boner.conn_status(i, brief, ids, bl, sk) for i in range(len(brief))] == ["ok"] * len(brief)
    b2 = list(brief)
    b2[0] = (b2[0][0], b2[0][1], 0, b2[0][3], b2[0][4])
    b2[1] = (b2[1][0], b2[1][1], 4, b2[1][3], 0x40021)
    b2[2] = (b2[2][0], b2[2][1], 4, 0x8000, b2[2][4])
    b2[3] = (b2[3][0], "nope", 4, b2[3][3], b2[3][4])
    b2[4] = (b2[4][0], "jaw1", 4, b2[4][3], b2[4][4])
    b2[5] = (b2[5][0], b2[5][1], 4, b2[5][3], b2[6][4])
    assert [boner.conn_status(i, b2, ids, bl, sk) for i in range(7)] == \
        ["no ring", "no child limb", "no parent limb", "no host", "host not in parent", "duplicate", "duplicate"]
    # Create for a limb without a connection: its default host
    nc = [l for l in ch.limbs]
    root = boner.limb_root_bone(ch.limbs[ids.index(0x25)].bone_names, sk, [-1 if bb.parent is None else bb.parent
                                                                            for bb in ch.bones])
    assert root.lower() == "front_leg1_l", root
    assert boner.limbs_without_connection(ids, [x for x in f2 if x != 0x25]) == [ids.index(0x25)]
    assert boner.limbs_without_connection(ids + [0x10800, 0], f2) == [len(ids)]
    # the two ways ComboTest.exe refuses a creature, as the operators warn about them
    ln = [l.name for l in ch.limbs]
    lf, rf = ids.index(0x25), ids.index(0x26)
    ids_mid = list(ids); ids_mid[lf] = 0x820021
    f2_mid = list(f2); f2_mid[f2.index(0x25)] = 0x20021
    assert boner.unmirrored_left(ids_mid) == [lf] and boner.unmirrored_left(ids) == []
    w = boner.socket_warnings(ids, ids_mid, f2, f2_mid, ln)
    assert w == [f"limb 'Cheetah_LFLeg' (L Mid Leg) has no Right partner of class 0x20022: "
                 f"{boner.UNMIRRORED_WHY} (re-class its partner limb the same way)"], w
    ids_both = list(ids_mid); ids_both[rf] = 0x20022
    f2_both = list(f2_mid); f2_both[f2.index(0x26)] = 0x20022
    assert boner.socket_warnings(ids_mid, ids_both, f2_mid, f2_both, ln) == []         # fixed: nothing new to say
    f2_dup = list(f2); f2_dup[f2.index(0x25)] = 0x29
    w = boner.socket_warnings(ids, ids, f2, f2_dup, ln)
    assert len(w) == 1 and w[0].startswith("limb 'Cheetah_LFLeg' (L Front Leg) has no connection now: ") and \
        boner.SOCKETLESS_WHY in w[0], w
    assert boner.socket_warnings(ids, ids, f2_dup, f2_dup, ln) == []                   # already so: not repeated
    f2_arm = [x for x in f2 if x != 0x15]                                              # a stub counts
    assert [x.split("'")[1] for x in boner.socket_warnings(ids, ids, f2, f2_arm, ln)] == ["Cheetah_LFArm"]
    try:
        from combotest.validate import picker_refusals
        from combotest.loader import Parent
    except ImportError:
        picker_refusals = None
    if picker_refusals is not None:
        import combocore
        mm = copy.deepcopy(ch)
        mm.limb_connections = [c for c in mm.limb_connections if c.flags2 & M != 0x15]
        why = picker_refusals(mm)
        assert len(why) == 1 and why[0].startswith("limb 'Cheetah_LFArm' (class 0x15) has no connection carrying its "
                                                   "class"), why
        other = Parent.from_model(SP.read(CHEETAH), name="cheetah", path=CHEETAH)
        pr = combocore.run_preview(Parent.from_model(mm, name="armless"), other, combocore.ROLE_TORSO, ["head"],
                                   combocore.Options(run_lua=False, body_scale=False))
        assert not pr.ok and pr.error.startswith("ComboTest.exe does not take ") and "Cheetah_LFArm" in pr.error, pr.error
        m2 = copy.deepcopy(ch)
        m2.limbs[lf].limb_id = 0x20021
        m2.limb_connections[f2.index(0x25)].flags2 = 0x20021
        pr = combocore.run_preview(other, Parent.from_model(m2, name="onesided"), combocore.ROLE_TORSO, ["front_legs"],
                                   combocore.Options(run_lua=False, body_scale=False))
        assert not pr.ok and "Right mirror class 0x20022" in pr.error, pr.error
    bmid = boner.default_parent(0x10001, ["antler_l"], ["head", "antler_l"], [-1, 0], [0x800, 0x40],
                                [["spine1"], ["head"]])
    assert bmid == boner.PARENT_HEAD and boner.default_parent(0x15, ["antler_l"], ["head", "antler_l"], [-1, 0],
                                                              [0x800], [["spine1"]]) == boner.PARENT_TORSO
    say(f"5 RULES: default host = the file's host on E {stats['E'][1]}/{stats['E'][0]}, T {stats['T'][1]}/{stats['T'][0]} "
        "(always a parent-limb bone, never none); names; re-class plan (follows / NoSwap copy kept / no class, two "
        "connections, a parent class refused); NoSwap no connection box; corners; status words; limbs without a "
        f"connection; Create's Auto Parent = the file's {dparent[1]}/{dparent[0]}; the socket warnings (one-sided "
        "re-class, duplicate Child class, a deleted stub socket) and Combine Preview refusing such a creature")


# ------------------------------------------------------------------------------------------------------------ 6
def migration():
    real = (store.point_ints, store.set_point_ints, store.point_count)
    store.point_ints = lambda obj, name: obj.attrs.get(name)
    store.set_point_ints = lambda obj, name, vals: obj.attrs.__setitem__(name, np.asarray(vals, dtype=np.int64).reshape(-1)
                                                                          if np.ndim(vals) else np.full(obj.n, vals))
    store.point_count = lambda obj: obj.n
    try:
        _migration()
    finally:
        store.point_ints, store.set_point_ints, store.point_count = real
        store.forget_journal_cache()


def _pre_upgrade_object(src):
    """An object as the previous add-on left it: limb rows upgraded, connection classes set directly (not journaled), no
    connection identities, two generations whose journal entries (and session copies) hold the rings but no class /
    identity keys; the second has the Head ring re-picked (rotated), the mesh at the second."""
    obj = LR.FakeObj(len(src.vertices))
    obj[store.MARK] = True
    obj[store.KEY_UID] = "fake-conn-uid"
    obj[store.KEY_SOURCE] = CHEETAH
    obj[store.KEY_NPATCH] = len(src.patches)
    obj[store.KEY_POOL_N] = len(src.vertices)
    n = len(src.limbs)
    limb = {store.KEY_LIMB_NAMES: [l.name for l in src.limbs], store.KEY_LIMB_IDS: [store._i32(l.limb_id) for l in src.limbs],
            store.KEY_LIMB_SRC: list(range(n)), store.KEY_LIMB_BONES: [], store.KEY_LIMB_BONE_COUNTS: [-1] * n}
    obj.update(copy.deepcopy(limb))
    rings = [ring_corners(c.vertex_indices) for c in src.limb_connections]
    head = next(i for i, c in enumerate(src.limb_connections) if c.flags2 & M == 0x40)
    rot = copy.deepcopy(rings)
    rot[head] = rot[head][1:] + rot[head][:1]

    def conn_tables(rs):
        return {store.KEY_CONN_NAMES: [c.name for c in src.limb_connections],
                store.KEY_CONN_BONES: [c.bone_name for c in src.limb_connections],
                store.KEY_CONN_VERTS: [v for r in rs for v in r], store.KEY_CONN_COUNTS: [len(r) for r in rs],
                store.KEY_CONN_ENC: [1] * len(rs), store.KEY_STORE_VERSION: store.STORE_VERSION}
    obj[store.KEY_CONN_FLAGS1] = [store._i32(c.flags1) for c in src.limb_connections]
    obj[store.KEY_CONN_FLAGS2] = [store._i32(c.flags2) for c in src.limb_connections]
    g0, g1 = 2001, 2002
    e0 = dict(copy.deepcopy(limb), **conn_tables(rings))
    e1 = dict(copy.deepcopy(limb), **conn_tables(rot))
    obj[store.KEY_JOURNAL] = {str(g0): e0, str(g1): e1}
    for g in (g0, g1):
        store._cache_put(obj, g, obj[store.KEY_JOURNAL][str(g)])
    obj.update(copy.deepcopy(conn_tables(rot)))
    obj[store.KEY_GEN] = g1
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
    return obj, g0, g1, head


def _export(obj):
    b = core.build_export(state(CHEETAH, connections=store.conn_rows(obj), limbs=store.limb_rows(obj)))
    return SP.write(b.model)


def _migration():
    raw = open(CHEETAH, "rb").read()
    src = SP.read(CHEETAH)
    f1_0 = [store._i32(c.flags1) for c in src.limb_connections]

    # without the backfill, an Undo into a pre-upgrade generation after a Create drops the classes
    store.forget_journal_cache()
    obj, g0, g1, head = _pre_upgrade_object(src)
    real_upgrade = store.upgrade_conn_tables
    store.upgrade_conn_tables = lambda o: False
    try:
        obj[store.KEY_CONN_SRC] = list(range(len(f1_0)))                    # what a naive first write would do
        store.begin_edit(obj)
        obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
        assert store.sync(obj) is None
        assert store.KEY_CONN_FLAGS1 not in obj, "the unguarded sync did not drop the classes: the guard would be vacuous"
    finally:
        store.upgrade_conn_tables = real_upgrade

    store.forget_journal_cache()
    obj, g0, g1, head = _pre_upgrade_object(src)
    pre = copy.deepcopy(dict(obj))
    rows0 = store.conn_rows(obj)
    assert [r.source() for r in rows0] == list(range(len(f1_0))) and [store._i32(r.flags1) for r in rows0] == f1_0
    at_g1 = _export(obj)
    assert at_g1 != raw and SP.read(at_g1).limb_connections[head].vertex_indices != src.limb_connections[head].vertex_indices
    # Create Connection (what bz.create_connection does): begin_edit syncs (the upgrade backfills), then the whole table
    g2 = store.begin_edit(obj)
    j = obj[store.KEY_JOURNAL]
    for g in (g0, g1):
        assert all(k in j[str(g)] for k in store.CONN_TABLE_KEYS), f"generation {g} was not backfilled"
        assert j[str(g)][store.KEY_CONN_FLAGS1] == f1_0 and j[str(g)][store.KEY_CONN_SRC] == list(range(len(f1_0)))
        assert all(k in store._cache_get(obj, g) for k in store.CONN_TABLE_KEYS)
    rows = store.conn_rows(obj)
    new = core.RingEdit(index=len(rows), bone="spine1", corners=[], src=-1, name="Cheetah_LMLeg", flags1=0x800,
                        flags2=0x20021)
    store.set_conn_rows(obj, rows + [new])
    created = _export(obj)
    mc = SP.read(created)
    assert mc.limb_connections[-1].name == "Cheetah_LMLeg" and len(mc.limb_connections) == len(src.limb_connections) + 1
    assert store.ring_store_error(obj) is None

    # Undo into g1 (pre-upgrade, re-picked ring), then g0 (import), Redo to g2
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g1)
    assert store.sync(obj) is None
    assert [store._i32(r.flags1) for r in store.conn_rows(obj)] == f1_0 and _export(obj) == at_g1
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g0)
    assert store.sync(obj) is None
    assert _export(obj) == raw, "Undo into the pre-upgrade import generation is not the source"
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g2)
    assert store.sync(obj) is None and store.conn_rows(obj)[-1].src == -1 and _export(obj) == created

    # a memfile Undo restores the pre-upgrade object (no identities anywhere on it); the edit mesh comes back at g2
    obj.clear()
    obj.update(copy.deepcopy(pre))
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g2)
    assert store.sync(obj) is None
    assert store.conn_rows(obj)[-1].name == "Cheetah_LMLeg" and _export(obj) == created, "the session copy lost the create"
    obj.attrs[store.ATTR_GEN] = np.full(obj.n, g0)
    assert store.sync(obj) is None and _export(obj) == raw
    # an inconsistent table is refused, not guessed
    obj[store.KEY_CONN_SRC] = [0]
    assert "re-import" in store.ring_store_error(obj)
    try:
        store.conn_rows(obj)
    except ValueError:
        pass
    else:
        raise AssertionError("an inconsistent connection table was read")
    say("6 MIGRATION: pre-upgrade journal (2 generations, no class / identity keys) backfilled on the first sync; Undo "
        "into each keeps the classes (g1 the re-picked ring, g0 byte-identical), Redo creates; memfile-restored object "
        "finds the create; the unguarded sync drops the classes")


if __name__ == "__main__":
    identity()
    edits()
    class_mask()
    oe_check()
    rules()
    migration()
    print("ALL PASS")
