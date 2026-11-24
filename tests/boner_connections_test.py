"""Boner > Connections in headless Blender (increment B3): the connection list, Create for a limb, Rename, Delete, the
Parent / Child classification boxes, the host bone (Set / Default), a stock limb re-classed together with its
connection, a connection for an added limb with its ring picked -- journaled, undoable, exported through ICParse's
writer, checked by Validate.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_connections_test.py

A. registration: the six connection operators and scene.bz_boner.active_conn / conn_target / conn_bone;
B. cheetah (stock): the rows are its source records (status ok, the tab lists every connection and its tools); Set
   Active fills the host field and the overlay draws that ring bright, the others dimmed; Rename refuses a 64-character, a non-ASCII and a duplicate name and renames the Head
   connection (export: only that name, Undo byte-identical, Redo renamed); the Child boxes (LFLeg as Rear: duplicate, back
   to Front: identical), Non-Swappable refused, the Parent boxes (Head, back to Torso: identical); the host bone Set
   spine2 (export: only that host) / refused jaw1 and an unknown bone / Default (identical) / Set from the panel field;
   LTWing re-classed Side takes its connection along in ONE journal generation (/tmp/bz_boner_b3_reclass.sgm: only
   that LIMB id and LCON child class; the OE duplicate-class warning), Undo restores both (identical), Redo both; Tail
   unticked is refused; Delete the Head connection (the lost-socket warning; Validate's precheck ERROR), Create for
   Head (only Head offered; default host neck1; no ring: status 'no ring', nothing offered for a second Create), its ring set from a
   scrambled selection: the original records (Head's appended); Undo the ring, the Create and the Delete one step at a
   time (identical), Redo all three;
C. New Creature: Delete Head's connection -> Validate pins the precheck ERROR for the missing socket; Create for Head +
   ring from a scrambled selection + Default host -> the original LCON records, Head's appended (its ring the same
   corner loop, started where the stock partners start) (/tmp/bz_boner_b3_head.sgm), no WARN oe connection line, no COMBINE ERROR in the Quick combine tier (real combines
   both roles); LFArm's connection and limb deleted, the limb added back (Add Standard), a connection created for it
   (default host spine3 = the parent of front_arm1_l, set to neck1 like the scaffold), its stub ring picked
   (/tmp/bz_boner_b3_arm.sgm: the original records in the new order, no OE connection warning, nothing blocking);
   the LFArm stub without its socket pins the precheck ERROR (ComboTest.exe refuses the creature);
B+ the socket warnings: a duplicate Child class and a deleted connection name the socketless limb (precheck ERROR
   'pDesLimb->m_pLimbConn'); LFLeg alone re-classed Mid warns about its Right partner (precheck ERROR 'LimbToAdd.m_pLimb',
   the must-fail export /tmp/bz_boner_b3_onesided.sgm), RFLeg Mid too clears it; a renamed connection left behind by a
   re-class warns and clears the stale follow message;
D. horse: the antler connection deleted and created from the menu path (Auto = Head, host head; Torso refused with the
   Head hint), its ring picked = the original record (/tmp/bz_boner_b3_antler.sgm), Undo identical.
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, limbops, connops, structure  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.sgm.creature.model import ring_corners  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
HORSE = REPO + "/Assets/engine_stock/horse.sgm"
NC_SRC = "/tmp/bz_boner_conn_nc_src.sgm"
OUT = "/tmp/bz_boner_conn_out.sgm"
M = 0xFF7FFFFF
_log = open("/tmp/bz_boner_conn_log.txt", "w")


def _history(op, obj):
    """``bpy.ops.ed.undo`` / ``redo``, then the live creature object. In Blender 5.3 an Undo or Redo in Edit Mode
    can replace the Object and the Scene, so a reference taken before it is dead ("StructRNA of type Object has been
    removed"), and writing through a dead Scene setting can crash Blender: look the object up again by name, and
    re-read ``bpy.context.scene`` settings after every call (Blender 5.2 kept both alive)."""
    name = obj.name
    op()
    return bpy.data.objects[name]


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level


def imp(path, **kw):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def ok(op, **kw):
    res, msg = call(op, **kw)
    assert res == {"FINISHED"}, (op, kw, msg)


def refused(op, words, **kw):
    res, msg = call(op, **kw)
    assert res == {"CANCELLED"} and all(w in msg for w in words), (kw, msg)
    return msg


def exported(obj, path=OUT):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
    except RuntimeError as e:
        raise AssertionError(f"export failed: {e}")
    assert r == {"FINISHED"} and os.path.exists(path), r
    return open(path, "rb").read(), str(obj.get("bz_last_export", ""))


def read(data):
    with open("/tmp/bz_boner_conn_read.sgm", "wb") as f:
        f.write(data)
    return sgm_parser.read("/tmp/bz_boner_conn_read.sgm")


def validate_verdict(obj):
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def boner_ct_refusal(limb, cls):
    from combotest.validate import picker_refusals
    from ICParse.sgm.creature.model import Limb
    return picker_refusals(type("M", (), {"limbs": [Limb(name=limb, limb_id=cls, prims=[], bone_names=[])],
                                          "limb_connections": []})())[0]


def rec(c):
    return (c.name, int(c.flags1), int(c.flags2), list(c.vertex_indices), c.bone_name)


def conn_index(obj, cls):
    return next(i for i, x in enumerate(store.conn_flags(obj)[1]) if boner.class_of(x) == cls)


def limb_index(obj, cls):
    return next(i for i, x in enumerate(store.limb_ids(obj)) if boner.class_of(x) == cls)


def select_in_order(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    bm.select_history.clear()
    for i in idx:
        bm.verts[i].select = True
        bm.select_history.add(bm.verts[i])
    bmesh.update_edit_mesh(obj.data)


def same_cycle(a, b):
    a, b = list(a), list(b)
    rots = [b[k:] + b[:k] for k in range(len(b))]
    return a in rots or a[::-1] in rots


class _Rec:
    """Records UILayout calls (the panel tab runs headless on it)."""
    def __init__(self, log, name=""):
        object.__setattr__(self, "log", log)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, name):
        return _Rec(self.log, name)

    def __call__(self, *a, **kw):
        self.log.append((self._name, a, kw))
        return _Rec(self.log)

    def __setattr__(self, k, v):
        self.log.append(("set", k, v))


def tab(obj):
    log = []
    connops.forget_table(obj)
    ui.BZ_PT_boner.connections_tab(_Rec(log), bpy.context, obj)
    return log, [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]


def pick_ring(obj, ci, corners):
    """Set Connection Ring from a selection made in click order (the scriptable twin of Pick Ring)."""
    select_in_order(obj, corners)
    ok(bpy.ops.bz.set_connection_ring, conn=str(ci))


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    for idn in ("conn_set_active", "create_connection", "rename_connection", "delete_connection", "conn_flag",
                "conn_set_host"):
        getattr(bpy.ops.bz, idn).get_rna_type()
    s = bpy.context.scene.bz_boner
    assert s.active_conn == 0 and s.conn_target == "CHILD" and s.conn_bone == ""
    say("A REGISTER: 6 connection operators + scene.bz_boner.active_conn / conn_target / conn_bone")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    assert exported(obj)[0] == raw
    rows = store.conn_rows(obj)
    assert [r.src for r in rows] == list(range(len(src.limb_connections)))
    assert [(r.name, r.flags1, r.flags2, r.bone) for r in rows] == \
        [(c.name, c.flags1, c.flags2, c.bone_name) for c in src.limb_connections]
    t = connops.conn_table(obj)
    assert not t.error and t.status == ["ok"] * len(rows), t.status
    s = bpy.context.scene.bz_boner
    s.mode = "CONNECTIONS"
    log, texts = tab(obj)
    assert all(c.name in texts for c in src.limb_connections), texts[:12]
    assert {"Create for Limb", "Rename", "Delete", "Set", "Default", "(Edit Mode: Pick Ring)"} <= set(texts), texts
    assert any("Non-Swappable: a limb box" in x for x in texts) and "Non-Swappable" not in texts
    assert any(name == "prop_search" and a[1] == "conn_bone" for name, a, kw in log), "no host bone search field"
    head, lf, lt_c = conn_index(obj, 0x40), conn_index(obj, 0x25), conn_index(obj, 0x301)
    ok(bpy.ops.bz.conn_set_active, index=lf)
    assert s.active_conn == lf and s.conn_bone == "spine3", s.conn_bone
    # the overlay in Boner > Connections: the active ring thick at its status colour, the others dimmed
    from blender_native import draw
    batches, widths = [], []
    real = (draw.gpu, draw.batch_for_shader, draw._pt_shader)
    rec_gpu = _Rec([])
    object.__setattr__(rec_gpu, "state", type("S", (), {"line_width_set": staticmethod(widths.append),
                                                         "point_size_set": staticmethod(lambda x: None),
                                                         "depth_test_set": staticmethod(lambda x: None)})())
    draw.gpu = rec_gpu
    draw.batch_for_shader = lambda sh, kind, attrs, **kw: batches.append((kind, attrs)) or _Rec([])
    draw._pt_shader = lambda: _Rec([])
    try:
        n = len(obj.data.vertices)
        v = np.empty(n * 3); obj.data.vertices.foreach_get("co", v)
        draw._draw_connections(obj, v.reshape(n, 3))
    finally:
        draw.gpu, draw.batch_for_shader, draw._pt_shader = real
    strips = [attrs["color"][0] for kind, attrs in batches if kind == "LINE_STRIP"]
    assert len(strips) == len(rows) and len({tuple(c) for i, c in enumerate(strips) if i != lf}) == 1, strips
    assert strips[lf][1] > strips[(lf + 1) % len(rows)][1] and 4.0 in widths, (strips[lf], widths)
    names0 = [r.name for r in rows]

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    _l, texts = tab(obj)
    assert "Pick Ring" in texts and "From Selection" in texts, texts
    # Rename
    for bad, words in (("X" * 64, "at most 63"), ("Kopf_ü", "ASCII"), ("CHEETAH_TAIL", "already called 'Cheetah_Tail'")):
        refused(bpy.ops.bz.rename_connection, (words,), index=head, name=bad)
    bpy.ops.ed.undo_push(message="before rename")
    ok(bpy.ops.bz.rename_connection, index=head, name="Cheetah_Noggin")
    data, msg = exported(obj)
    m = read(data)
    assert "connections: renamed 1 (Cheetah_Head -> Cheetah_Noggin)" in msg, msg
    m.limb_connections[head].name = "Cheetah_Head"
    assert sgm_parser.write(m) == raw, "Rename changed more than the connection name"
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert [n for n, _b, _v in store.connections(obj)] == names0 and exported(obj)[0] == raw, "Undo of Rename"
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert store.connections(obj)[head][0] == "Cheetah_Noggin"
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == raw

    # the Child / Parent boxes
    ok(bpy.ops.bz.conn_flag, index=lf, target="CHILD", bit=0x8)
    assert store.conn_flags(obj)[1][lf] == 0x29 and connops.conn_table(obj).status[lf] == "duplicate"
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w.startswith("limb 'Cheetah_LFLeg' (L Front Leg) has no connection now: ComboTest.exe refuses the whole "
                        "creature in either role"), w
    val = validate_verdict(obj)
    assert any(v.startswith("COMBINE ERROR precheck: limb 'Cheetah_LFLeg' (class 0x25) has no connection carrying its "
                            "class: ComboTest.exe fails 'Assertion of (pDesLimb->m_pLimbConn)'") for v in val), \
        [v for v in val if v.startswith("COMBINE")]
    ok(bpy.ops.bz.conn_flag, index=lf, target="CHILD", bit=0x4)
    assert store.conn_flags(obj)[1][lf] == 0x25 and exported(obj)[0] == raw
    refused(bpy.ops.bz.conn_flag, ("belongs on the limb",), index=lf, target="CHILD", bit=boner.NOSWAP)
    ok(bpy.ops.bz.conn_flag, index=lf, target="PARENT", bit=0x40)
    assert store.conn_flags(obj)[0][lf] == 0x40 and connops.conn_table(obj).status[lf] == "host not in parent"
    data, msg = exported(obj)
    assert "connections: class 1 (Cheetah_LFLeg parent 0x800 -> 0x40)" in msg, msg
    ok(bpy.ops.bz.conn_flag, index=lf, target="PARENT", bit=0x800)
    assert store.conn_flags(obj)[0][lf] == 0x800 and exported(obj)[0] == raw

    # the host bone
    ok(bpy.ops.bz.conn_set_host, index=lf, bone="spine2")
    data, msg = exported(obj)
    m = read(data)
    assert m.limb_connections[lf].bone_name == "spine2" and "rings: 1 (Cheetah_LFLeg bone)" in msg, msg
    m.limb_connections[lf].bone_name = "spine3"
    assert sgm_parser.write(m) == raw
    refused(bpy.ops.bz.conn_set_host, ("'jaw1' is not in the bones of the parent limb",), index=lf, bone="jaw1")
    refused(bpy.ops.bz.conn_set_host, ("not a bone of this creature's skeleton",), index=lf, bone="no_such_bone")
    ok(bpy.ops.bz.conn_set_host, index=lf, default=True)
    assert store.connections(obj)[lf][1] == "spine3" and exported(obj)[0] == raw
    s.active_conn, s.conn_bone = lf, "spine1"
    ok(bpy.ops.bz.conn_set_host)
    assert store.connections(obj)[lf][1] == "spine1"
    ok(bpy.ops.bz.conn_set_host, default=True)
    assert exported(obj)[0] == raw

    # a stock limb re-classed together with its connection: one journal generation, one undo step
    lt = limb_index(obj, 0x301)
    bpy.ops.ed.undo_push(message="before reclass")
    gen = obj[store.KEY_GEN]
    ok(bpy.ops.bz.limb_flag, index=lt, bit=0x400)                    # Side (clears Top)
    assert store.limb_ids(obj)[lt] == 0x501 and store.conn_flags(obj)[1][lt_c] == 0x501
    j = obj[store.KEY_JOURNAL]
    assert obj[store.KEY_GEN] != gen and str(gen) in j
    assert list(j[str(gen)][store.KEY_CONN_FLAGS2])[lt_c] == 0x301 and list(j[str(gen)][store.KEY_LIMB_IDS])[lt] == 0x301
    data, msg = exported(obj, "/tmp/bz_boner_b3_reclass.sgm")
    m = read(data)
    assert m.limbs[lt].limb_id == 0x501 and m.limb_connections[lt_c].flags2 == 0x501, msg
    assert "class 1 (Cheetah_LTWing 0x301 -> 0x501)" in msg and "Cheetah_LTWing child 0x301 -> 0x501" in msg, msg
    m.limbs[lt].limb_id, m.limb_connections[lt_c].flags2 = 0x301, 0x301
    assert sgm_parser.write(m) == raw, "the re-class changed more than the limb id and its connection's child class"
    val = validate_verdict(obj)
    assert "WARN oe: Limb 'Cheetah_LSWing' has same class as limb 'Cheetah_LTWing'" in val or \
        any("has same class as limb" in v for v in val), [v for v in val if "oe:" in v]
    assert not core.blocking(val), core.blocking(val)
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert store.limb_ids(obj)[lt] == 0x301 and store.conn_flags(obj)[1][lt_c] == 0x301 and exported(obj)[0] == raw
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert store.limb_ids(obj)[lt] == 0x501 and store.conn_flags(obj)[1][lt_c] == 0x501
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == raw
    refused(bpy.ops.bz.limb_flag, ("not to no class",), index=limb_index(obj, 0x80), bit=0x80)
    assert str(obj.get("bz_last_socket_warning", "x")) == ""

    # one side of a pair re-classed: allowed (the partner is the next click) but warned, and Validate's precheck says
    # ComboTest.exe refuses it (pinned must-fail export /tmp/bz_boner_b3_onesided.sgm); both sides: no warning
    lfl, rfl = limb_index(obj, 0x25), limb_index(obj, 0x26)
    bpy.ops.ed.undo_push(message="before one side")
    ok(bpy.ops.bz.limb_flag, index=lfl, bit=0x20000)                 # L Front Leg -> L Mid Leg
    assert store.limb_ids(obj)[lfl] == 0x20021 and store.conn_flags(obj)[1][lf] == 0x20021
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w == ("limb 'Cheetah_LFLeg' (L Mid Leg) has no Right partner of class 0x20022: ComboTest.exe fails 'Assertion "
                 "of (LimbToAdd.m_pLimb)' combining the creature, in either role (re-class its partner limb the same "
                 "way)"), w
    data, msg = exported(obj, "/tmp/bz_boner_b3_onesided.sgm")
    val = validate_verdict(obj)
    assert any(v.startswith("COMBINE ERROR precheck: limb 'Cheetah_LFLeg' (class 0x20021) has no limb of its Right "
                            "mirror class 0x20022") for v in val), [v for v in val if v.startswith("COMBINE")]
    ok(bpy.ops.bz.limb_flag, index=rfl, bit=0x20000)                 # R Front Leg -> R Mid Leg: the pair again
    assert str(obj.get("bz_last_socket_warning", "x")) == ""
    val = validate_verdict(obj)
    assert not any("mirror class" in v or "m_pLimbConn" in v for v in val), [v for v in val if v.startswith("COMBINE")]
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner; obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == raw

    # a renamed connection does not follow its limb: the re-class that leaves the limb socketless says so
    bpy.ops.ed.undo_push(message="before rename follow")
    ok(bpy.ops.bz.rename_connection, index=lf, name="Socket_A")
    ok(bpy.ops.bz.limb_flag, index=limb_index(obj, 0x29), bit=0x4)   # L Rear Leg -> Front: its connection follows
    assert str(obj.get("bz_last_conn_follow", "")) == "connection 'Cheetah_LRLeg' child 0x29 -> 0x25", obj.get("bz_last_conn_follow")
    ok(bpy.ops.bz.limb_flag, index=lfl, bit=0x8)                     # L Front Leg -> Rear: nothing carries 0x29 now
    assert str(obj.get("bz_last_conn_follow", "x")) == "", "a stale follow message"
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w.startswith("limb 'Cheetah_LFLeg' (L Rear Leg) has no connection now"), w
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner; obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner; obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == raw

    # a stub's socket deleted: the precheck says ComboTest.exe refuses the creature (must-fail export
    # /tmp/bz_boner_b3_noarm.sgm; harness 2026-09-15: 'pDesLimb->m_pLimbConn'), the Quick tier is not clean
    bpy.ops.ed.undo_push(message="before stub socket")
    ok(bpy.ops.bz.delete_connection, index=conn_index(obj, 0x15))
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w.startswith("limb 'Cheetah_LFArm' (L Front Arm) has no connection now: ComboTest.exe refuses"), w
    exported(obj, "/tmp/bz_boner_b3_noarm.sgm")
    val = validate_verdict(obj)
    assert [v for v in val if v.startswith("COMBINE ERROR")] == [
        "COMBINE ERROR precheck: " + boner_ct_refusal("Cheetah_LFArm", 0x15)], [v for v in val if v.startswith("COMBINE")]
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert exported(obj)[0] == raw

    # Delete the Head connection, then Create it for the Head and pick its ring
    bpy.ops.ed.undo_push(message="before delete")
    ok(bpy.ops.bz.delete_connection, index=head)
    assert obj.get("bz_last_conn_delete") == "Cheetah_Head" and len(store.connections(obj)) == len(names0) - 1
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w.startswith("limb 'Cheetah_Head' (Head) has no connection now: ComboTest.exe refuses the whole creature"), w
    val = validate_verdict(obj)
    assert any(v.startswith("COMBINE ERROR precheck: limb 'Cheetah_Head' (class 0x40) has no connection carrying its "
                            "class") for v in val), [v for v in val if v.startswith("COMBINE")]
    hl = limb_index(obj, 0x40)
    items = [i[0] for i in connops._limb_items(None, bpy.context)]
    assert items == [str(hl)], items
    ok(bpy.ops.bz.create_connection, limb=str(hl))
    r = store.conn_rows(obj)[-1]
    assert (r.src, r.name, r.flags1, r.flags2, r.bone, r.corners) == (-1, "Cheetah_Head", 0x800, 0x40, "neck1", []), r
    assert bpy.context.scene.bz_boner.active_conn == len(names0) - 1
    assert connops.conn_table(obj).status[-1] == "no ring"
    assert [i[0] for i in connops._limb_items(None, bpy.context)] == ["NONE"]     # a second Create: nothing offered
    assert call(bpy.ops.bz.create_connection, limb="NONE")[0] == {"CANCELLED"}
    stock = ring_corners(src.limb_connections[head].vertex_indices)
    scrambled = stock[::-1][2:] + stock[::-1][:2]
    pick_ring(obj, len(names0) - 1, scrambled)
    ring = store.connections(obj)[-1][2]
    assert ring == stock, (ring, stock)
    data, msg = exported(obj)
    m = read(data)
    assert sorted(rec(c) for c in m.limb_connections) == sorted(rec(c) for c in src.limb_connections)
    assert rec(m.limb_connections[-1]) == rec(src.limb_connections[head])
    assert "connections: added 1 (Cheetah_Head); deleted 1 (Cheetah_Head)" in msg, msg
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner                                               # the ring
    assert store.connections(obj)[-1][2] == [] and store.conn_rows(obj)[-1].src == -1
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner                                               # the Create
    assert len(store.connections(obj)) == len(names0) - 1
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner                                               # the Delete
    assert exported(obj)[0] == raw and [r.src for r in store.conn_rows(obj)] == list(range(len(names0)))
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner; obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner; obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert store.connections(obj)[-1][2] == stock and exported(obj)[0] == data
    bpy.ops.object.mode_set(mode="OBJECT")
    say("B CHEETAH: rows = source (ok); Rename refusals + Head renamed (only that name; Undo identical, Redo); Child "
        "Rear (duplicate) / Front identical, NoSwap refused, Parent Head (host not in parent) / Torso identical; host "
        "spine2 / jaw1 + unknown refused / Default identical / Set from the field; LTWing as Side with its connection "
        "in one generation (/tmp/bz_boner_b3_reclass.sgm, OE duplicate warning), Undo both identical, Redo both; Tail "
        "untick refused; socket warnings (duplicate Child class, LFLeg alone Mid -> /tmp/bz_boner_b3_onesided.sgm, "
        "a renamed connection left behind, the LFArm stub socket deleted -> /tmp/bz_boner_b3_noarm.sgm); Head "
        "connection deleted (precheck ERROR) and created (host neck1, no ring, second Create "
        "not offered), ring from a scrambled selection = stock; Undo ring / Create / Delete step by step (identical), Redo all")


# ------------------------------------------------------------------------------------------------------------- C
def part_c():
    fresh()
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath=NC_SRC, creature_name="BonerNC", detail=1,
                                                 clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    src_bytes = open(NC_SRC, "rb").read()
    src = sgm_parser.read(NC_SRC)
    assert exported(obj)[0] == src_bytes
    n0 = len(src.limb_connections)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")

    head = conn_index(obj, 0x40)
    ok(bpy.ops.bz.delete_connection, index=head)
    val = validate_verdict(obj)
    pinned = [v for v in val if v.startswith("COMBINE ERROR")]
    assert pinned == ["COMBINE ERROR precheck: limb 'BonerNC_Head' (class 0x40) has no connection carrying its class: "
                      "ComboTest.exe fails 'Assertion of (pDesLimb->m_pLimbConn)' (CombinerImp.cpp:180) as soon as the "
                      "creature is loaded, in either role, so no combination with it exists (every non-torso limb, "
                      "stubs included, needs its connection)"], pinned
    assert not core.blocking(val)
    ok(bpy.ops.bz.create_connection, limb=str(limb_index(obj, 0x40)))
    stock = ring_corners(src.limb_connections[head].vertex_indices)
    pick_ring(obj, n0 - 1, stock[::-1][3:] + stock[::-1][:3])
    assert call(bpy.ops.bz.conn_set_host, index=n0 - 1, default=True)[0] == {"CANCELLED"}   # already the default
    assert store.connections(obj)[-1][1] == "neck1"
    data, msg = exported(obj, "/tmp/bz_boner_b3_head.sgm")
    m = read(data)
    others = [rec(c) for i, c in enumerate(src.limb_connections) if i != head]
    assert [rec(c) for c in m.limb_connections[:-1]] == others, msg
    got, want = rec(m.limb_connections[-1]), rec(src.limb_connections[head])
    assert got[:3] + got[4:] == want[:3] + want[4:] and same_cycle(ring_corners(got[3]), stock), (got, want)
    head_start = "the scaffold's" if got == want else f"stock-aligned {ring_corners(got[3])} (scaffold {stock})"
    bpy.context.scene.bz_combine.validate_level = "QUICK"
    val = validate_verdict(obj)
    bpy.context.scene.bz_combine.validate_level = "OFF"
    assert not [v for v in val if v.startswith("COMBINE ERROR")], [v for v in val if v.startswith("COMBINE ERROR")]
    assert not [v for v in val if v.startswith("WARN oe") and "connection '" in v]
    assert not core.blocking(val) and str(obj.get("bz_combine_summary", "")), "the Quick tier did not run"
    say(f"C head re-created, start {head_start}: Quick tier {obj.get('bz_combine_summary', '')}")

    # LFArm: its connection and limb deleted, the limb added back, a connection created for it and its ring picked
    arm_c = conn_index(obj, 0x15)
    arm_rec = src.limb_connections[[c.flags2 & M for c in src.limb_connections].index(0x15)]
    ok(bpy.ops.bz.delete_connection, index=arm_c)
    assert obj.get("bz_last_conn_delete") == "BonerNC_LFArm"
    val = validate_verdict(obj)                                     # a stub without its socket: ComboTest.exe refuses
    assert [v for v in val if v.startswith("COMBINE ERROR")] == [
        "COMBINE ERROR precheck: " + boner_ct_refusal("BonerNC_LFArm", 0x15)], [v for v in val if v.startswith("COMBINE")]
    ok(bpy.ops.bz.delete_limb, index=limb_index(obj, 0x15))
    ok(bpy.ops.bz.add_standard_limb, slot="LFArm")
    new_limb = len(store.limb_names(obj)) - 1
    assert store.limb_rows(obj)[new_limb] == structure.LimbRow(-1, "BonerNC_LFArm", 0x15, ("front_arm1_l",))
    assert [i[0] for i in connops._limb_items(None, bpy.context)] == [str(new_limb)]
    ok(bpy.ops.bz.create_connection, limb=str(new_limb))
    r = store.conn_rows(obj)[-1]
    assert (r.name, r.flags1, r.flags2, r.bone) == ("BonerNC_LFArm", 0x800, 0x15, "spine3"), r
    ok(bpy.ops.bz.conn_set_host, bone="neck1")                     # the active (new) connection
    corners = ring_corners(arm_rec.vertex_indices)
    pick_ring(obj, len(store.connections(obj)) - 1, corners[1:] + corners[:1])
    ring = store.connections(obj)[-1][2]
    assert same_cycle(ring, corners), (ring, corners)
    data, msg = exported(obj, "/tmp/bz_boner_b3_arm.sgm")
    m = read(data)
    got = {c.name: rec(c) for c in m.limb_connections}
    want = {c.name: rec(c) for c in src.limb_connections}
    assert set(got) == set(want) and all(got[k] == want[k] for k in want if k not in ("BonerNC_LFArm", "BonerNC_Head")), msg
    for k in ("BonerNC_LFArm", "BonerNC_Head"):                    # re-picked: the same loop, the stock start
        assert got[k][:3] + got[k][4:] == want[k][:3] + want[k][4:], (got[k], want[k])
        assert same_cycle(ring_corners(got[k][3]), ring_corners(want[k][3])), (got[k], want[k])
    lsrc = {l.name: (l.limb_id, sorted(l.prims), list(l.bone_names)) for l in src.limbs}
    lgot = {l.name: (l.limb_id, sorted(l.prims), list(l.bone_names)) for l in m.limbs}
    assert lgot == lsrc, (lgot, lsrc)
    val = validate_verdict(obj)
    assert not [v for v in val if v.startswith("WARN oe") and "connection '" in v], [v for v in val if "oe:" in v]
    assert not [v for v in val if v.startswith("COMBINE ERROR")], [v for v in val if v.startswith("COMBINE ERROR")]
    assert not core.blocking(val), core.blocking(val)
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"C NEW CREATURE: Head connection deleted (precheck ERROR pinned), created + ring picked scrambled + Default host "
        f"= the original records, ring started like stock (/tmp/bz_boner_b3_head.sgm), Quick tier clean; LFArm connection + limb deleted, limb "
        f"added back, connection created (default host spine3, set neck1), stub ring picked "
        f"{'= the scaffold start' if ring == corners else f'(start {ring[0]}, scaffold {corners[0]})'} "
        "(/tmp/bz_boner_b3_arm.sgm): the original records, no OE connection warning, nothing blocking")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    """A head-mounted ring from the panel's menu (Create for Limb runs with the defaults): Auto cuts the horse's antler
    ring into the Head, whose bone hosts it; an explicit Torso is refused with the hint."""
    fresh()
    obj = imp(HORSE)
    raw = open(HORSE, "rb").read()
    src = sgm_parser.read(HORSE)
    ant = next(i for i, c in enumerate(src.limb_connections) if c.flags2 & M == 0x10001)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    ok(bpy.ops.bz.delete_connection, index=ant)
    al = limb_index(obj, 0x10001)
    assert [i[0] for i in connops._limb_items(None, bpy.context)] == [str(al)]
    refused(bpy.ops.bz.create_connection, ("no default host bone", "Cut Into: Head would host it on 'head'"),
            limb=str(al), parent="TORSO")
    ok(bpy.ops.bz.create_connection, limb=str(al))                 # Auto, as the menu runs it
    r = store.conn_rows(obj)[-1]
    want = src.limb_connections[ant]
    assert (r.name, r.flags1, r.flags2, r.bone) == (want.name, 0x40, 0x10001, "head"), r
    pick_ring(obj, len(store.connections(obj)) - 1, ring_corners(want.vertex_indices))
    data, msg = exported(obj, "/tmp/bz_boner_b3_antler.sgm")
    m = read(data)
    assert [rec(c) for c in m.limb_connections[:-1]] == [rec(c) for i, c in enumerate(src.limb_connections) if i != ant]
    got = rec(m.limb_connections[-1])
    assert got[:3] + got[4:] == rec(want)[:3] + rec(want)[4:] and same_cycle(ring_corners(got[3]),
                                                                              ring_corners(want.vertex_indices)), got
    m.limb_connections.insert(ant, m.limb_connections.pop())
    exact = sgm_parser.write(m) == raw
    obj = _history(bpy.ops.ed.undo, obj); obj = _history(bpy.ops.ed.undo, obj); obj = _history(bpy.ops.ed.undo, obj)
    assert exported(obj)[0] == raw
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"D HORSE: antler connection deleted; Create for Limb (Auto) cut it into the Head (0x40, host head), Torso refused "
        f"with the Head hint; ring picked: the original record{' (byte-exact at its old position)' if exact else ' (same corner loop)'} "
        "(/tmp/bz_boner_b3_antler.sgm); Undo identical")


def main():
    blender_native.register()
    part_a()
    part_b()
    part_c()
    part_d()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_conn_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_conn_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
