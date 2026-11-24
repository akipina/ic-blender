"""Limb/connection authoring + lossless write-back, headless Blender.
    blender --background --python blender_native/tests/limb_auth_test.py

1. untouched export byte-identical; the store holds every ring as its corner loop (store v2);
2. re-picking the snail head ring through Set Connection Ring in reversed + rotated click order puts it back into the
   stock winding and start: the whole file stays byte-identical; an unordered selection, a short ring and a corner
   not joined by a patch side are refused (CANCELLED, nothing stored);
3. limb reassignment and a connection bone edit are written; an object stored before v2 (raw ring ints) migrates on
   its first ring-table write (Set Connection Bone) and exports the same bytes as a v2 object with that edit;
4. a New Creature's front-leg ring re-picked in scrambled order exports byte-identical to the unedited creature
   (/tmp/bz_ring_repick.sgm, loaded by --engine and combined by rdn-combotest);
5. a ring that lost a corner gets a COMBINE ERROR from Validate (Quick tier and Combine Checks Off);
6. Pick Ring started from the sidebar picks in the viewport's WINDOW region (projection and cursor relative to it);
   a verbatim ring re-picked exports closed edge pairs, an untouched verbatim ring survives another connection's
   edit; a ring on the wrong socket is WARN in the ring status and in Validate with Combine Checks Off.
"""
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

import os
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.sgm.creature.model import ring_corners, ring_pairs  # noqa: E402
import bmesh  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
_log = open("/tmp/bz_limb_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def select_in_order(obj, idx):
    """Select control points as Shift+clicks would (bmesh select history in click order)."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    bm.select_history.clear()
    for i in idx:
        bm.verts[i].select = True
        bm.select_history.add(bm.verts[i])
    bmesh.update_edit_mesh(obj.data)


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def export_bytes_of(obj, path):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    assert bpy.ops.bz.export_sgm(filepath=path) == {"FINISHED"}
    return open(path, "rb").read()


def ring_repick(obj, src_bytes):
    """Re-pick the head ring in reversed + rotated click order: back to the stock order, file byte-identical; bad
    selections refused."""
    head = store.connections(obj)[0][2]
    assert head == ring_corners(sgm_parser.read(SRC).limb_connections[0].vertex_indices), head
    bpy.ops.object.mode_set(mode="EDIT")
    scrambled = head[::-1][3:] + head[::-1][:3]
    select_in_order(obj, scrambled)
    res, msg = call(bpy.ops.bz.set_connection_ring, conn="0")
    assert res == {"FINISHED"}, msg
    assert store.connections(obj)[0][2] == head and store.conn_encodings(obj)[0] == 1, store.connections(obj)[0]
    gen = obj[store.KEY_GEN]
    # refusals change nothing: an unordered selection, 5 corners, a corner not joined by a patch side
    bm = bmesh.from_edit_mesh(obj.data)
    select_in_order(obj, scrambled)
    bm.select_history.clear()
    res, msg = call(bpy.ops.bz.set_connection_ring, conn="0")
    assert res == {"CANCELLED"} and "in order" in msg, msg
    select_in_order(obj, scrambled[:5])
    res, msg = call(bpy.ops.bz.set_connection_ring, conn="0")
    assert res == {"CANCELLED"} and "needs 6 corners, 5 picked" in msg, msg
    select_in_order(obj, [head[0], head[2], head[1], head[3], head[4], head[5]])
    res, msg = call(bpy.ops.bz.set_connection_ring, conn="0")
    assert res == {"CANCELLED"} and "patch side" in msg, msg
    assert obj[store.KEY_GEN] == gen and store.connections(obj)[0][2] == head
    bpy.ops.object.mode_set(mode="OBJECT")
    same = export_bytes_of(obj, "/tmp/bz_la_repick.sgm") == src_bytes
    say(f"RING_REPICK head clicked {scrambled} -> stored {store.connections(obj)[0][2]}; file identical {same}; "
        f"unordered / 5 corners / non-side refused")
    return same


class _Rec:
    """Records UILayout / gpu calls (the panel rows and the ring overlay run headless on it)."""
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


def ring_ui_glue(obj):
    """The Pick Ring modal's glue (events -> RingPicker -> the stored ring, one journaled edit), the cached ring status,
    the panel rows and the overlay colours, headless: the modal runs on a stand-in operator (no viewport in
    background), the panel on a recording layout, the overlay on a recording gpu."""
    import types
    ctx = bpy.context
    head = store.connections(obj)[0][2]
    st = ops.ring_status_cached(obj, ctx)
    assert [x.level for x in st] == ["OK"] * len(st), [(x.name, x.problems) for x in st if x.level != "OK"]
    assert ops.ring_status_cached(obj, ctx) is st, "the ring status is not cached"
    bpy.ops.object.mode_set(mode="EDIT")
    # the modal needs a 3D viewport: from a script (no window in background) it is refused with the way to do it
    res, msg = call(bpy.ops.bz.pick_ring, conn="0")
    assert res == {"CANCELLED"} and "3D Viewport" in msg and "set_connection_ring" in msg, (res, msg)

    class FakePick:
        modal = ops.BZ_OT_pick_ring.modal
        _cleanup = ops.BZ_OT_pick_ring._cleanup
        _mouse = ops.BZ_OT_pick_ring._mouse
        _area = None
        _region = types.SimpleNamespace(x=0, y=0, width=10 ** 6, height=10 ** 6)

        def __init__(self):
            self.reports = []

        def _update_screen(self, _context):
            pass

        def report(self, level, text):
            self.reports.append((set(level), text))

    op = FakePick()
    op._picker = ops.ring_picker(obj, 0)
    op._obj_name, op._ci, op._hover, op._h3d, op._h2d = obj.name, 0, None, None, None
    clicks = head[::-1][2:] + head[::-1][:2]                  # reversed, from its 3rd corner
    op._screen = {c: (float(10 * k), 0.0) for k, c in enumerate(sorted(op._picker.candidates))}
    xy = {c: op._screen[c] for c in op._screen}

    def ev(t, v="PRESS", at=None):
        x, y = xy[at] if at is not None else (-1000.0, -1000.0)
        return types.SimpleNamespace(type=t, value=v, mouse_x=x, mouse_y=y)
    gen = obj[store.KEY_GEN]
    assert op.modal(ctx, ev("MIDDLEMOUSE")) == {"PASS_THROUGH"}
    for c in clicks[:3]:
        assert op.modal(ctx, ev("MOUSEMOVE", "NOTHING", c)) == {"RUNNING_MODAL"} and op._hover == c, (c, op._hover)
        assert op.modal(ctx, ev("LEFTMOUSE", "PRESS", c)) == {"RUNNING_MODAL"}, op._picker.message
    assert op.modal(ctx, ev("BACK_SPACE")) == {"RUNNING_MODAL"} and op._picker.picked == clicks[:2]
    assert obj[store.KEY_GEN] == gen and store.connections(obj)[0][2] == head, "a pick in progress wrote the store"
    for c in clicks[2:5]:
        op.modal(ctx, ev("MOUSEMOVE", "NOTHING", c))
        assert op.modal(ctx, ev("LEFTMOUSE", "PRESS", c)) == {"RUNNING_MODAL"}, op._picker.message
    op.modal(ctx, ev("MOUSEMOVE", "NOTHING", clicks[5]))
    assert op.modal(ctx, ev("LEFTMOUSE", "PRESS", clicks[5])) == {"FINISHED"}, (op._picker.message, op.reports)
    assert store.connections(obj)[0][2] == head and obj[store.KEY_GEN] != gen, store.connections(obj)[0]
    assert any("reversed to wind into the torso" in t and "rotated by" in t for _l, t in op.reports), op.reports
    op2 = FakePick()
    op2._picker, op2._obj_name, op2._ci, op2._hover, op2._h3d, op2._h2d, op2._screen = \
        ops.ring_picker(obj, 0), obj.name, 0, None, None, None, {}
    assert op2.modal(ctx, ev("ESC")) == {"CANCELLED"} and store.connections(obj)[0][2] == head
    bpy.ops.object.mode_set(mode="OBJECT")

    # the panel rows (a recording layout) and the overlay colours (a recording gpu)
    from blender_native import ui, draw
    log = []
    ui.BZ_PT_panel._ring_rows(_Rec(log), ctx, obj)
    labels = [kw.get("text", a[0] if a else "") for name, a, kw in log if name == "label"]
    assert any(t.startswith("Snail_Head: 6/6 corners") for t in labels), labels[:4]
    assert any("Head · attach torso · host neck1" in t for t in labels), labels[:4]
    conns = store.connections(obj)
    conns[0] = (conns[0][0], conns[0][1], head[::-1])
    store.set_connections(obj, conns)
    st2 = ops.ring_status_cached(obj, ctx)
    assert st2 is not st and st2[0].level == "ERROR", st2[0]
    log = []
    ui.BZ_PT_panel._ring_rows(_Rec(log), ctx, obj)
    assert ("set", "alert", True) in log and any("wound with its normal out of the torso" in (kw.get("text") or "")
                                                 for name, a, kw in log if name == "label"), log[:12]
    batches = []
    real = (draw.gpu, draw.batch_for_shader, draw._pt_shader)
    draw.gpu = _Rec([])
    draw.batch_for_shader = lambda sh, kind, attrs, **kw: batches.append((kind, attrs)) or _Rec([])
    draw._pt_shader = lambda: _Rec([])
    try:
        n = len(obj.data.vertices)
        v = np.empty(n * 3); obj.data.vertices.foreach_get("co", v)
        draw._draw_connections(obj, v.reshape(n, 3))
    finally:
        draw.gpu, draw.batch_for_shader, draw._pt_shader = real
    strips = [attrs["color"][0] for kind, attrs in batches if kind == "LINE_STRIP"]
    assert len(strips) == len(conns) and strips[0] == draw._RING_LEVEL_COLOR["ERROR"] \
        and all(c == draw._RING_LEVEL_COLOR["OK"] for c in strips[1:]), strips
    ticks = [attrs for kind, attrs in batches if kind == "LINES"]
    assert len(ticks) == len(conns), len(ticks)
    conns[0] = (conns[0][0], conns[0][1], head)
    store.set_connections(obj, conns)
    say(f"RING_UI modal glue: clicks {clicks} (with a Backspace) -> {head}, one journaled edit, Esc leaves it; ring "
        f"status cached, panel rows and overlay colours follow it (reversed head red, others green)")
    return True


def pick_ring_from_sidebar(obj):
    """Pick Ring started from its only button, in the 3D Viewport's sidebar (context.region is the UI region, which has
    no view): it picks in the viewport's WINDOW region, projects the corners there and takes the cursor relative to
    it. A reversed + rotated pick clicked at the projected corners (window coordinates) restores the stock head ring;
    a click over the sidebar passes through; the overlay draws only in the picking region."""
    import types
    from mathutils import Vector
    win = bpy.context.window_manager.windows[0]
    scr = win.screen
    area = next(a for a in scr.areas if a.type == "VIEW_3D")
    ui = next(r for r in area.regions if r.type == "UI")
    head = store.connections(obj)[0][2]
    rv3d = area.spaces.active.region_3d
    pts = [obj.matrix_world @ obj.data.vertices[c].co for c in head]
    centre = sum(pts, Vector()) / len(pts)
    rv3d.view_location = centre
    rv3d.view_distance = 8.0 * max((p - centre).length for p in pts)

    class P:
        invoke = ops.BZ_OT_pick_ring.invoke
        modal = ops.BZ_OT_pick_ring.modal
        _update_screen = ops.BZ_OT_pick_ring._update_screen
        _mouse = ops.BZ_OT_pick_ring._mouse
        _mine = ops.BZ_OT_pick_ring._mine
        _cleanup = ops.BZ_OT_pick_ring._cleanup
        _draw_view = ops.BZ_OT_pick_ring._draw_view
        _draw_pixel = ops.BZ_OT_pick_ring._draw_pixel
        conn = "0"
        properties = types.SimpleNamespace(is_property_set=lambda _n: True)

        def __init__(self):
            self.reports, self.started = [], False

        def _start_modal(self, _context):
            self.started = True

        def report(self, level, text):
            self.reports.append((set(level), text))

    op = P()
    bpy.ops.object.mode_set(mode="EDIT")
    gen = obj[store.KEY_GEN]
    with bpy.context.temp_override(window=win, screen=scr, area=area, region=ui):
        ev = types.SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=ui.x, mouse_y=ui.y)
        assert op.invoke(bpy.context, ev) == {"RUNNING_MODAL"} and op.started, op.reports
        reg = op._region
        assert reg.type == "WINDOW" and op._area == area and op._rv3d is not None, reg.type
        assert all(op._screen.get(c) is not None for c in head), "head corners not projected into the viewport"
        op._draw_view(bpy.context)                             # the UI region: not the picking region, nothing drawn
        op._draw_pixel(bpy.context)
        clicks = head[::-1][4:] + head[::-1][:4]

        def at(c, t="MOUSEMOVE", v="NOTHING"):
            sx, sy = op._screen[c]
            return types.SimpleNamespace(type=t, value=v, mouse_x=reg.x + sx, mouse_y=reg.y + sy)
        over_sidebar = types.SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=reg.x + reg.width + 5,
                                             mouse_y=reg.y + 10)
        assert op.modal(bpy.context, over_sidebar) == {"PASS_THROUGH"}
        result = None
        for c in clicks:
            assert op.modal(bpy.context, at(c)) == {"RUNNING_MODAL"} and op._hover == c, (c, op._hover)
            result = op.modal(bpy.context, at(c, "LEFTMOUSE", "PRESS"))
        assert result == {"FINISHED"}, (result, op._picker.message, op.reports)
    op._cleanup(bpy.context)
    assert store.connections(obj)[0][2] == head and obj[store.KEY_GEN] != gen, store.connections(obj)[0]
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"PICK_RING_SIDEBAR invoked from the UI region -> picks in the WINDOW region; clicks {clicks} at projected "
        f"window coordinates -> {head}; a click over the sidebar passes through")
    return True


def verbatim_rings():
    """A connection kept verbatim (enc 0: ints that are not closed edge pairs) is re-picked: it becomes a corner loop
    and exports as closed edge pairs; an untouched verbatim ring stays verbatim when another connection is edited."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bz.import_sgm(filepath=SRC, clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    src = sgm_parser.read(SRC)
    conns, enc = store.connections(obj), store.conn_encodings(obj)
    head = conns[0][2]
    raw1 = list(src.limb_connections[1].vertex_indices)[:4]              # 4 ints: not closed edge pairs
    conns[0] = (conns[0][0], conns[0][1], head[:4]); enc[0] = 0                    # 4 ints: not closed edge pairs
    conns[1] = (conns[1][0], conns[1][1], raw1); enc[1] = 0
    store.set_connections(obj, conns, enc=enc)
    assert store.conn_encodings(obj)[:2] == [0, 0]
    export_bytes_of(obj, "/tmp/bz_la_verbatim0.sgm")
    got = sgm_parser.read("/tmp/bz_la_verbatim0.sgm")
    assert list(got.limb_connections[0].vertex_indices) == head[:4] and list(got.limb_connections[1].vertex_indices) == raw1
    assert call(bpy.ops.bz.set_connection_bone, conn="2", bone="spine1")[0] == {"FINISHED"}
    assert store.conn_encodings(obj)[:2] == [0, 0], store.conn_encodings(obj)
    real = ops._stock_dir
    ops._stock_dir = lambda _ctx: ""
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        select_in_order(obj, head)
        res, msg = call(bpy.ops.bz.set_connection_ring, conn="0")
        assert res == {"FINISHED"}, msg
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        ops._stock_dir = real
    assert store.conn_encodings(obj)[:2] == [1, 0], store.conn_encodings(obj)
    export_bytes_of(obj, "/tmp/bz_la_verbatim1.sgm")
    m = sgm_parser.read("/tmp/bz_la_verbatim1.sgm")
    assert list(m.limb_connections[0].vertex_indices) == list(src.limb_connections[0].vertex_indices), \
        list(m.limb_connections[0].vertex_indices)
    assert list(m.limb_connections[1].vertex_indices) == raw1 and m.limb_connections[2].bone_name == "spine1"
    say("VERBATIM_RINGS re-picked verbatim head -> closed edge pairs (12 ints); untouched verbatim ring (4 ints) kept "
        "through another connection's edit")
    return True


def ring_placement_warning():
    """A ring set on the wrong socket (the lion's R Front Leg ring as its L Front Leg ring) is accepted, but the ring
    status says WARN (not OK) and Validate with Combine Checks Off names it."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bz.import_sgm(filepath=REPO + "/Assets/stock/lion.sgm", clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    conns = store.connections(obj)
    li = next(i for i, (n, _b, _v) in enumerate(conns) if n.lower().endswith("_lfleg"))
    ri = next(i for i, (n, _b, _v) in enumerate(conns) if n.lower().endswith("_rfleg"))
    assert ops.ring_status_cached(obj, bpy.context)[li].level == "OK"
    bpy.ops.object.mode_set(mode="EDIT")
    select_in_order(obj, conns[ri][2])
    res, msg = call(bpy.ops.bz.set_connection_ring, conn=str(li))
    assert res == {"FINISHED"}, msg
    bpy.ops.object.mode_set(mode="OBJECT")
    ops.forget_ring_status()
    st = ops.ring_status_cached(obj, bpy.context)[li]
    assert st.level == "WARN" and any("sides border the L Front Leg limb" in p for p in st.problems), st.problems
    scene = bpy.context.scene
    old = scene.bz_combine.validate_level
    scene.bz_combine.validate_level = "OFF"
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    finally:
        scene.bz_combine.validate_level = old
    lines = [p for p in obj["bz_validation"] if p.startswith("COMBINE WARN precheck: ring") and conns[li][0] in p]
    assert lines, [p for p in obj["bz_validation"] if p.startswith("COMBINE")][:5]
    say(f"RING_PLACEMENT R Front Leg ring as L Front Leg: status WARN, Validate (Combine Checks Off): {lines[0][:100]}")
    return True


def legacy_ring_store(obj_v2_bytes_with_bone):
    """An object stored before v2 (raw edge-pair ints, no version / encoding): Set Connection Bone migrates the whole
    ring table atomically and the export equals the v2 object's with the same edit."""
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.view_layer.objects.active
    m = sgm_parser.read(SRC)
    raw = [list(c.vertex_indices) for c in m.limb_connections]
    for k in (store.KEY_STORE_VERSION, store.KEY_CONN_ENC):
        del obj[k]
    obj[store.KEY_CONN_VERTS] = [v for r in raw for v in r]
    obj[store.KEY_CONN_COUNTS] = [len(r) for r in raw]
    for k in (store.KEY_STORE_VERSION, store.KEY_CONN_ENC, store.KEY_CONN_VERTS, store.KEY_CONN_COUNTS):
        for g in obj[store.KEY_JOURNAL].keys():                # the journal holds the same pre-v2 tables
            snap = obj[store.KEY_JOURNAL][g]
            if k in snap:
                del snap[k]
            if k in obj:
                snap[k] = obj[k].to_list()
    assert store.connections(obj)[0][2] == ring_corners(raw[0]) and store.conn_encodings(obj) == [1] * len(raw)
    assert export_bytes_of(obj, "/tmp/bz_la_legacy0.sgm") == open(SRC, "rb").read(), "legacy store not identical"
    res, msg = call(bpy.ops.bz.set_connection_bone, conn="0", bone="spine1")
    assert res == {"FINISHED"}, msg
    assert obj[store.KEY_STORE_VERSION] == store.STORE_VERSION and list(obj[store.KEY_CONN_ENC]) == [1] * len(raw)
    assert list(obj[store.KEY_CONN_VERTS]) == [c for r in raw for c in ring_corners(r)]
    got = export_bytes_of(obj, "/tmp/bz_la_legacy1.sgm")
    same = got == obj_v2_bytes_with_bone
    say(f"LEGACY_RING_STORE migrated on Set Connection Bone: v{obj[store.KEY_STORE_VERSION]}, export == v2 edit {same}")
    return same


def run():
    blender_native.register()
    bpy.ops.bz.import_sgm(filepath=SRC)
    obj = bpy.context.active_object
    bpy.context.view_layer.objects.active = obj

    # 1) unedited export passes limb+connection data but must stay byte-identical
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_la0.sgm")
    ident = open("/tmp/bz_la0.sgm", "rb").read() == open(SRC, "rb").read()
    src_m = sgm_parser.read(SRC)
    v2 = (obj.get(store.KEY_STORE_VERSION) == 2 and store.conn_encodings(obj) == [1] * len(src_m.limb_connections)
          and [ring_pairs(v) for _n, _b, v in store.connections(obj)] == [list(c.vertex_indices) for c in src_m.limb_connections])
    say("UNEDITED_IDENTICAL", ident, "store v2 corner loops", v2)
    ident = ident and v2 and ring_repick(obj, open(SRC, "rb").read())
    ident = ident and ring_ui_glue(obj) and export_bytes_of(obj, "/tmp/bz_la_ui.sgm") == open(SRC, "rb").read()
    ident = ident and pick_ring_from_sidebar(obj) and export_bytes_of(obj, "/tmp/bz_la_ui2.sgm") == open(SRC, "rb").read()

    # 2) reassign limb-2 patches to limb-1, export, re-read, verify membership moved
    pl = store.patch_limb(obj)
    moving = np.nonzero(pl == 2)[0]
    pl[moving] = 1
    store.set_patch_limb(obj, pl)
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_la1.sgm")
    m = sgm_parser.read("/tmp/bz_la1.sgm")
    pl_out = core._patch_limb_of(m)
    moved_ok = bool(np.all(pl_out[moving] == 1))
    say(f"LIMB_REASSIGN moved={len(moving)} now_limb1={moved_ok} "
        f"limb1={m.limbs[1].name} limb2_prims={len(m.limbs[2].prims)}")

    # 3) edit connection 0's bone, export, re-read, verify it changed and conn1 untouched
    conns = store.connections(obj)
    n0, b0, v0 = conns[0]
    conns[0] = (n0, "spine1", v0)
    store.set_connections(obj, conns)
    bpy.ops.bz.export_sgm(filepath="/tmp/bz_la2.sgm")
    m2 = sgm_parser.read("/tmp/bz_la2.sgm")
    bone_ok = m2.limb_connections[0].bone_name == "spine1"
    conn1_same = (list(m2.limb_connections[1].vertex_indices)
                  == list(sgm_parser.read(SRC).limb_connections[1].vertex_indices))
    say(f"CONN_EDIT conn0_bone={m2.limb_connections[0].bone_name} ok={bone_ok} conn1_same={conn1_same}")

    # the v2 object with only the bone edit, for the legacy-store comparison
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.bz.import_sgm(filepath=SRC)
    o2 = bpy.context.view_layer.objects.active
    assert call(bpy.ops.bz.set_connection_bone, conn="0", bone="spine1")[0] == {"FINISHED"}
    legacy_ok = legacy_ring_store(export_bytes_of(o2, "/tmp/bz_la_v2bone.sgm"))
    repick_ok = new_creature_repick() and verbatim_rings() and ring_placement_warning()
    ring_ok = combine_ring_check()
    blender_native.unregister()
    return ident, moved_ok, bone_ok, conn1_same and legacy_ok and repick_ok, ring_ok


def new_creature_repick():
    """A New Creature's front-leg ring re-picked in scrambled order: winding and start restored, so the export is the
    unedited creature byte for byte (/tmp/bz_ring_repick.sgm: --engine loads it, rdn-combotest combines it)."""
    bpy.ops.wm.read_homefile(use_empty=True)
    src = "/tmp/bz_ring_repick_src.sgm"
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath=src, creature_name="RePick", detail=1)
    obj = bpy.context.view_layer.objects.active
    conns = store.connections(obj)
    ci = next(i for i, (n, _b, _v) in enumerate(conns) if n.lower().endswith("_lfleg"))
    ring = conns[ci][2]
    scrambled = ring[::-1][1:] + ring[::-1][:1]
    assert scrambled != ring
    bpy.ops.object.mode_set(mode="EDIT")
    select_in_order(obj, scrambled)
    res, msg = call(bpy.ops.bz.set_connection_ring, conn=str(ci))
    assert res == {"FINISHED"}, msg
    bpy.ops.object.mode_set(mode="OBJECT")
    out = export_bytes_of(obj, "/tmp/bz_ring_repick.sgm")
    src_bytes = open(src, "rb").read()
    rings_same = ([list(c.vertex_indices) for c in sgm_parser.read("/tmp/bz_ring_repick.sgm").limb_connections]
                  == [list(c.vertex_indices) for c in sgm_parser.read(src).limb_connections])
    say(f"NEW_CREATURE_REPICK {conns[ci][0]} clicked {scrambled} -> {store.connections(obj)[ci][2]} (was {ring}); rings "
        f"== unedited {rings_same}; file identical {out == src_bytes}")
    return rings_same and out == src_bytes


def combine_ring_check():
    """A creature whose front-leg ring lost points (an edited connection) gets a COMBINE ERROR from Validate's Quick
    combine tier, which judges the in-memory export model (the snail above has no legs, so a New Creature)."""
    bpy.ops.wm.read_homefile(use_empty=True)
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath="/tmp/bz_la_ring.sgm", creature_name="RingCut", detail=1)
    obj = bpy.context.view_layer.objects.active
    assert "FINISHED" in bpy.ops.bz.validate()
    before = [p for p in obj["bz_validation"] if p.startswith("COMBINE ERROR")]
    conns = store.connections(obj)
    ci = next(i for i, (n, _b, _v) in enumerate(conns) if n.lower().endswith("_lfleg"))
    n0, b0, v0 = conns[ci]
    conns[ci] = (n0, b0, v0[:len(v0) - 1])                      # delete one ring corner
    store.set_connections(obj, conns)
    try:
        bpy.ops.bz.validate()
    except RuntimeError as e:            # the cut ring also breaks loading (check_model): Validate reports ERRORs
        say("VALIDATE_REPORTED", str(e).splitlines()[0])
    after = [p for p in obj["bz_validation"] if p.startswith("COMBINE ERROR")]
    say(f"RING_CUT {n0}: {len(v0)} -> {len(v0) - 1} corners; COMBINE ERROR before {len(before)}, after {len(after)}: "
        f"{after[:2]}")
    # Combine Checks Off still runs the static precheck: the ring-size rule is not lost with the combines
    bpy.context.scene.bz_combine.validate_level = "OFF"
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    off = [p for p in obj["bz_validation"] if p.startswith("COMBINE ERROR precheck:")]
    say(f"RING_CUT with Combine Checks Off: {len(off)} precheck ERROR(s): {off[:1]}")
    bpy.context.scene.bz_combine.validate_level = "QUICK"
    return not before and any("ring has" in p and n0 in p for p in after) and any("ring has" in p and n0 in p for p in off)


import traceback
try:
    ident, moved_ok, bone_ok, conn1_same, ring_ok = run()
    ok = ident and moved_ok and bone_ok and conn1_same and ring_ok
    open("/tmp/bz_limb_done.txt", "w").write(("PASS" if ok else "FAIL")
        + f" ident={ident} moved={moved_ok} bone={bone_ok} conn1_same={conn1_same} ring_combine_error={ring_ok}\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_limb_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
