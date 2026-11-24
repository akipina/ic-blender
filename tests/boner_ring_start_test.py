"""Boner > Connections: Optimise Ring Start in headless Blender (increment B8).
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_ring_start_test.py

The combiner welds a donor's ring to a socket ring BY INDEX (``FUN_005c5962``: corner i to corner i), so which corner
a ring's list starts at decides which donor corner meets which socket corner. Validate measures it (the precheck's
"the by-index weld is N deg worse than the best rotation"); this operator fixes it in place.

A. registration: bz.optimise_ring_start and its Active Ring / All Rings buttons in the Connections tab;
B. cheetah with its L Front Leg socket ring started 2 corners out (what a fit / a hand pick leaves behind):
   Validate's ring read-out flags it, Optimise Ring Start puts it back -- the export is BYTE-IDENTICAL to the stock
   cheetah -- and the report gives the before / after angles, the partners over the margin and the millimetres the
   worst welded corner moves; Undo is byte-identical to the mis-started file (exported for the engine tier as
   /tmp/bz_ring_start_bad.sgm, the fixed one as /tmp/bz_ring_start_fixed.sgm);
C. refusals and no-ops: a second run on the same ring is refused ("already the best"), All Rings on the untouched
   cheetah writes nothing and leaves the export byte-identical, a ring picked with Set Connection Ring (which fixes
   the start as it picks) is already optimal, a ring reversed by hand is refused naming the winding, the horse's
   antler ring is refused (its Parent is the Head, so nothing welds at it);
D. All Rings on a creature with three mis-started rings: all three are rotated in ONE journal generation / one undo
   step, the other nine are untouched, and the export is the stock file again.
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, connops, combocore, ops  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
HORSE = REPO + "/Assets/engine_stock/horse.sgm"
OUT = "/tmp/bz_ring_start_out.sgm"
BAD = "/tmp/bz_ring_start_bad.sgm"
FIXED = "/tmp/bz_ring_start_fixed.sgm"
_log = open("/tmp/bz_ring_start_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh(level="OFF"):
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = level
    bpy.ops.ed.undo_push(message="start")      # background mode pushes no undo step of its own


def imp(path):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False) == {"FINISHED"}
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


def refused(op, obj=None, **kw):
    """The refusal's words: an ERROR report raises, a WARNING one leaves its summary on the object."""
    res, msg = call(op, **kw)
    assert res == {"CANCELLED"}, (kw, "not refused")
    return msg or str((obj or bpy.context.active_object).get("bz_last_ring_start", ""))


def step(op, message, **kw):
    """Run an operator with a savepoint in front of it: in background mode ``undo_push`` stores the CURRENT state as
    the step Undo comes back to (measured), so the savepoint goes before the change, not after."""
    bpy.ops.ed.undo_push(message=message)
    ok(op, **kw)


def exported(obj, path=OUT):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    r = bpy.ops.bz.export_sgm(filepath=path)
    assert r == {"FINISHED"} and os.path.exists(path), r
    return open(path, "rb").read()


def conn_index(obj, cls):
    return next(i for i, x in enumerate(store.conn_flags(obj)[1]) if boner.class_of(x) == cls)


def rotate_ring(obj, ci, shift, message="mis-started ring"):
    """Start connection ``ci``'s ring ``shift`` corners later -- the same ring, the same winding, a different start
    (what a fit, an import of hand-made geometry or a pick without stock partners leaves behind)."""
    bpy.ops.ed.undo_push(message=message)
    store.begin_edit(obj)
    rows = store.conn_rows(obj)
    c = list(rows[ci].corners)
    rows[ci] = core.RingEdit(index=rows[ci].index, bone=rows[ci].bone, corners=c[shift:] + c[:shift],
                             raw=rows[ci].raw, src=rows[ci].src, name=rows[ci].name, flags1=rows[ci].flags1,
                             flags2=rows[ci].flags2)
    store.set_conn_rows(obj, rows)
    connops.forget_table(obj)
    ops.forget_ring_status(obj)
    return c


def status_of(obj, ci):
    ops.forget_ring_status(obj)
    st = ops.ring_status_cached(obj, bpy.context)
    assert st and not isinstance(st[0], str), st
    return st[ci]


def undo(n=1):
    for _ in range(n):
        bpy.ops.ed.undo()
    return bpy.context.view_layer.objects.active


def tab(obj):
    log = []
    connops.forget_table(obj)
    ui.BZ_PT_boner.connections_tab(_Rec(log), bpy.context, obj)
    return log, [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]


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


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    bpy.ops.bz.optimise_ring_start.get_rna_type()
    obj = imp(CHEETAH)
    bpy.context.scene.bz_boner.mode = "CONNECTIONS"
    log, texts = tab(obj)
    assert "Optimise Start" in texts and "All Rings" in texts, texts
    assert combocore.available(), combocore.IMPORT_ERROR
    say("A REGISTER: bz.optimise_ring_start + the Optimise Start / All Rings buttons in Boner > Connections")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    assert exported(obj) == raw
    bpy.ops.ed.undo_push(message="imported")
    lf = conn_index(obj, 0x25)                                     # L Front Leg
    stock_ring = rotate_ring(obj, lf, 2)
    assert store.conn_rows(obj)[lf].corners == stock_ring[2:] + stock_ring[:2]
    bad = exported(obj, BAD)
    assert bad != raw and len(bad) == len(raw), (len(bad), len(raw))
    st = status_of(obj, lf)
    assert st.level == "WARN" and any("start corner is 2 off the stock start" in p for p in st.problems), st.problems
    assert any("Optimise Ring Start" in p for p in st.problems), st.problems
    # the plan, before it is applied
    rows = connops.conn_table(obj).rows
    plans = connops.ring_start_plans(obj, bpy.context, rows, [lf])
    (_i, plan), = plans
    assert plan.ok and plan.shift == 2 and plan.corners == stock_ring, plan
    assert plan.over_before == 4 and plan.over_after == 0 and plan.n == 4, plan
    assert plan.before_deg > 150 and plan.after_deg < 30 and 300 < plan.move_mm < 340, plan
    step(bpy.ops.bz.optimise_ring_start, "optimise", index=lf, scope="ACTIVE")
    text = str(obj.get("bz_last_ring_start", ""))
    for w in ("rotate the start by 2", "154 -> 28 deg", "over the 20 deg margin 4 -> 0", "320 mm along the seam"):
        assert w in text, (w, text)
    assert store.conn_rows(obj)[lf].corners == stock_ring, store.conn_rows(obj)[lf].corners
    fixed = exported(obj, FIXED)
    assert fixed == raw, "the optimised ring did not restore the stock file byte-for-byte"
    assert status_of(obj, lf).level == "OK", status_of(obj, lf).problems
    obj = undo()
    assert store.conn_rows(obj)[lf].corners == stock_ring[2:] + stock_ring[:2]
    assert exported(obj) == bad, "Undo is not byte-identical to the mis-started file"
    obj = undo()
    assert exported(obj) == raw
    say(f"B CHEETAH: its L Front Leg socket ring started 2 corners out -> ring_status WARN ('start corner is 2 off "
        f"the stock start', naming Optimise Ring Start); the operator rotates it back (mean corner angle "
        f"{plan.before_deg:.0f} -> {plan.after_deg:.0f} deg against {plan.n} stock front-leg donors, partners over "
        f"the 20 deg margin {plan.over_before} -> {plan.over_after}, the worst welded corner moves "
        f"{plan.move_mm:.0f} mm) and the export is BYTE-IDENTICAL to the stock cheetah; Undo byte-identical to the "
        f"mis-started file ({BAD} / {FIXED} for the engine tier)")


# ------------------------------------------------------------------------------------------------------------- C
def part_c():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    bpy.ops.ed.undo_push(message="imported")
    lf = conn_index(obj, 0x25)
    # an untouched stock creature: nothing to do, in either scope, and nothing is written
    msg = refused(bpy.ops.bz.optimise_ring_start, obj, index=lf, scope="ACTIVE")
    assert "already started where the stock creatures start theirs" in msg, msg
    assert "already the best of the 4 starts" in msg, msg
    msg = refused(bpy.ops.bz.optimise_ring_start, obj, index=lf, scope="ALL")
    assert "every one of the 12 rings is already started" in msg, msg
    assert exported(obj) == raw
    # a ring picked in scrambled order: Set Connection Ring already puts it on the stock start, so this is a no-op
    head = conn_index(obj, 0x40)
    ring = list(store.conn_rows(obj)[head].corners)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    bm.select_history.clear()
    scr = ring[3:] + ring[:3]
    for i in scr:
        bm.verts[i].select = True
        bm.select_history.add(bm.verts[i])
    bmesh.update_edit_mesh(obj.data)
    step(bpy.ops.bz.set_connection_ring, "re-pick", conn=str(head))
    assert store.conn_rows(obj)[head].corners == ring, store.conn_rows(obj)[head].corners
    msg = refused(bpy.ops.bz.optimise_ring_start, obj, index=head, scope="ACTIVE")
    assert "already the best" in msg, msg
    bpy.ops.object.mode_set(mode="OBJECT")
    obj = undo()
    # a ring reversed by hand: the winding rule, not the start, and no rotation fixes it
    bpy.ops.ed.undo_push(message="before reverse")
    store.begin_edit(obj)
    rows = store.conn_rows(obj)
    rows[lf] = core.RingEdit(index=rows[lf].index, bone=rows[lf].bone, corners=list(rows[lf].corners)[::-1],
                             raw=None, src=rows[lf].src, name=rows[lf].name, flags1=rows[lf].flags1,
                             flags2=rows[lf].flags2)
    store.set_conn_rows(obj, rows)
    connops.forget_table(obj); ops.forget_ring_status(obj)
    msg = refused(bpy.ops.bz.optimise_ring_start, obj, index=lf, scope="ACTIVE")
    assert "out of the torso" in msg and "no rotation fixes a reversed ring" in msg, msg
    assert status_of(obj, lf).level == "ERROR", status_of(obj, lf).problems
    obj = undo()
    assert exported(obj) == raw
    # the horse's antler ring: cut into the HEAD, so the combiner never welds at it
    fresh()
    obj = imp(HORSE)
    src = sgm_parser.read(HORSE)
    ant = next(i for i, c in enumerate(src.limb_connections) if int(c.flags1) & core.CLASS_MASK != core.TORSO_CODE)
    msg = refused(bpy.ops.bz.optimise_ring_start, obj, index=ant, scope="ACTIVE")
    assert "not the Torso" in msg and "nothing welds at this ring" in msg, msg
    say("C REFUSALS: the untouched cheetah is already optimal in both scopes (nothing written, export byte-identical); "
        "a head ring re-picked scrambled comes back on the stock start, so Optimise refuses it; a reversed ring is "
        "refused naming the winding (ring_status ERROR); the horse's antler ring is refused (Parent = Head)")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    bpy.ops.ed.undo_push(message="imported")
    picked = [conn_index(obj, c) for c in (0x40, 0x26, 0x2A)]       # Head, R Front Leg, R Rear Leg
    want = {}
    for ci, sh in zip(picked, (1, 2, 3)):
        want[ci] = rotate_ring(obj, ci, sh, message=f"mis-started {ci}")
    before = [list(r.corners) for r in store.conn_rows(obj)]
    gen = obj.get(store.KEY_GEN)
    step(bpy.ops.bz.optimise_ring_start, "optimise all", index=picked[0], scope="ALL")
    rows = store.conn_rows(obj)
    assert [list(r.corners) for i, r in enumerate(rows)] == [want.get(i, before[i]) for i in range(len(rows))]
    assert obj.get(store.KEY_GEN) != gen
    text = str(obj.get("bz_last_ring_start", ""))
    assert text.count("rotate the start by") == 3, text
    assert text.count("already the best") == 9, text
    assert exported(obj) == raw, "three rings optimised at once did not restore the stock file"
    obj = undo()                                                    # one step for the three rings
    assert [list(r.corners) for r in store.conn_rows(obj)] == before
    say("D ALL RINGS: the cheetah's Head / R Front Leg / R Rear Leg rings started 1 / 2 / 3 corners out -> All Rings "
        "rotates exactly those three (the other nine 'already the best') in one journal generation, the export is the "
        "stock file byte-for-byte again, and one Undo puts all three back")


def main():
    blender_native.register()
    part_a()
    part_b()
    part_c()
    part_d()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_ring_start_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_ring_start_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
