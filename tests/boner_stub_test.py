"""Boner > Connections > Add Stub Socket in headless Blender (increment B4): a stub limb and its socket for a class the
creature does not model, in one click and one undo step.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/boner_stub_test.py

A. registration: bz.add_stub_socket; it needs Edit Mode; the Connections tab offers it there;
B. cheetah without its arm stubs (limbs and connections deleted, the bones kept): the menu offers LFArm / RFArm;
   refused with no patch, two patches, a head patch, the side-wing stub's patch; LFArm on its stock patch -> the limb
   row with the existing front_arm1_l, the connection (Torso, host neck1, the stock ring) in ONE journal generation, the
   one-sided warning; RFArm clears it; no rig bone added; export (/tmp/bz_boner_b4_arms.sgm) = the stock LIMB / LCON
   records (appended) and bytes once reordered; Validate clean (Quick combine tier: no COMBINE ERROR); Undo each step
   (identical to the deleted state), Redo both (identical);
C. cheetah without its wing AND arm stubs (limbs, connections, bones: a copy, with its clips as Actions): LSWing /
   RSWing / LTWing / RTWing / LFArm / RFArm added on the stock patches CREATE their bones (stamped leaves under the host,
   at the patch centre, deform off); the top wings share the side wings' patches; refusals (another stub's patch, the
   other side's partner patch); the skeleton guard accepts exactly those bones, a renamed one too (exported under its new
   name, its limb list following), and refuses copies of one, a bone added under one, a re-parented one and a bone
   added at the root (a leaf added under a bone of the creature exports since B7: boner_add_bone_test); an Action keying a created bone is re-encoded with no track for
   it (kept clips keep their bytes); export (/tmp/bz_boner_b4_created.sgm): the stock LIMB / LCON records, the created
   bones last with flags word 1, no range, identity rotation to the host and the patch centre; ICParse reads it back;
   Validate: no OE stub-class warning, no stub STOCK line, Quick combine tier clean; Undo all six (the rig loses the
   bones, export identical to the source) and Redo all six (identical); a socket deleted with its limb leaves its bone
   in no limb (export warns, not written) and Add Stub Socket again reuses it;
D. edges: a rig disabled in viewports or in an excluded collection is refused in plain words and the creature stays in
   Edit Mode (eye-hidden / monitor-disabled-collection rigs work); a mode switch failing inside the bone creation still
   restores the creature's mode and the active object; Add Active Bone / Set Host name a created stub bone as such; a
   bone owning points moved into a stub limb keeps its flags; a created bone deleted while its limb lists it: export
   warns and writes, Validate does not call it load-blocking (the OE's WARN line, the COMBINE ERROR precheck).
"""
import os
import sys
import traceback
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ui, boner, limbops, connops, structure, rig, actions  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.sgm.creature.model import ring_corners  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"
BARE = "/tmp/bz_boner_b4_bare_src.sgm"
OUT = "/tmp/bz_boner_stub_out.sgm"
M = 0xFF7FFFFF
_log = open("/tmp/bz_boner_stub_log.txt", "w")


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
    kw.setdefault("clips_as_actions", False)
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
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


def export_refused(obj, words):
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.export_sgm(filepath=OUT)
    except RuntimeError as e:
        assert all(w in str(e) for w in words), (words, str(e))
        return str(e)
    raise AssertionError(f"export not refused ({words})")


def read(data):
    with open("/tmp/bz_boner_stub_read.sgm", "wb") as f:
        f.write(data)
    return sgm_parser.read("/tmp/bz_boner_stub_read.sgm")


def validate_verdict(obj, level="OFF"):
    bpy.context.scene.bz_combine.validate_level = level
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    bpy.context.scene.bz_combine.validate_level = "OFF"
    return list(obj.get("bz_validation", []))


def select_patch_corners(obj, corners):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    bm.select_history.clear()
    for i in corners:
        bm.verts[int(i)].select = True
    bmesh.update_edit_mesh(obj.data)


def stock_quad(stock, cls):
    return ring_corners(next(c for c in stock.limb_connections if c.flags2 & M == cls).vertex_indices)


def conn_index(obj, cls):
    return next(i for i, x in enumerate(store.conn_flags(obj)[1]) if boner.class_of(x) == cls)


def limb_index(obj, cls):
    return next(i for i, x in enumerate(store.limb_ids(obj)) if boner.class_of(x) == cls)


def add(obj, suffix, corners, **kw):
    select_patch_corners(obj, corners)
    ok(bpy.ops.bz.add_stub_socket, slot=suffix, **kw)
    return str(obj.get("bz_last_stub_socket", ""))


def lrec(l, low=False):
    return (l.name, int(l.limb_id), sorted(int(x) for x in l.prims), [str(b).lower() if low else str(b) for b in l.bone_names])


def crec(c):
    return (c.name, int(c.flags1), int(c.flags2), list(c.vertex_indices), c.bone_name)


class _Rec:
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


def tab_texts(obj):
    log = []
    connops.forget_table(obj)
    ui.BZ_PT_boner.connections_tab(_Rec(log), bpy.context, obj)
    return [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    bpy.ops.bz.add_stub_socket.get_rna_type()
    obj = imp(CHEETAH)
    assert not bpy.ops.bz.add_stub_socket.poll(), "Add Stub Socket must need Edit Mode"
    assert any(t.startswith("Add Stub Socket: Edit Mode") for t in tab_texts(obj))
    bpy.ops.object.mode_set(mode="EDIT")
    assert bpy.ops.bz.add_stub_socket.poll() and "Add Stub Socket" in tab_texts(obj)
    assert [i[0] for i in connops._stub_items(None, bpy.context)] == ["NONE"]         # stock cheetah: all present
    assert call(bpy.ops.bz.add_stub_socket, slot="NONE")[0] == {"CANCELLED"}          # a WARNING report: no raise
    bpy.ops.object.mode_set(mode="OBJECT")
    say("A REGISTER: bz.add_stub_socket (Edit Mode only; the Connections tab offers it there; nothing on stock cheetah)")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    obj = imp(CHEETAH)
    raw = open(CHEETAH, "rb").read()
    src = sgm_parser.read(CHEETAH)
    arm = store.rig(obj)
    nbones = len(arm.data.bones)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    for cls in (0x15, 0x16):
        ok(bpy.ops.bz.delete_connection, index=conn_index(obj, cls))
        ok(bpy.ops.bz.delete_limb, index=limb_index(obj, cls))
    deleted, _msg = exported(obj)
    assert [i[0] for i in connops._stub_items(None, bpy.context)] == ["LFArm", "RFArm"]
    P = store.patches(obj)
    pl = store.patch_limb(obj)
    q_l, q_r, q_sw = stock_quad(src, 0x15), stock_quad(src, 0x16), stock_quad(src, 0x501)
    select_patch_corners(obj, [])
    refused(bpy.ops.bz.add_stub_socket, ("no patch is selected",), slot="LFArm")
    select_patch_corners(obj, q_l + q_r)
    refused(bpy.ops.bz.add_stub_socket, ("2 patches are selected",), slot="LFArm")
    hp = int(np.nonzero(pl == limb_index(obj, 0x40))[0][0])
    select_patch_corners(obj, [int(P[hp][k]) for k in (0, 3, 15, 12)])
    refused(bpy.ops.bz.add_stub_socket, (f"patch {hp} is in limb 'Cheetah_Head', not the Torso",), slot="LFArm")
    select_patch_corners(obj, q_sw)
    refused(bpy.ops.bz.add_stub_socket, ("already the ring of connection 'Cheetah_LTWing'", "another stub socket"),
            slot="LFArm")                                           # the side and top wing stubs' shared patch
    gen = obj[store.KEY_GEN]
    text = add(obj, "LFArm", q_l)
    j = obj[store.KEY_JOURNAL]
    nl, nc = len(src.limbs) - 2, len(src.limb_connections) - 2
    assert obj[store.KEY_GEN] != gen and str(gen) in j and len(list(j[str(gen)][store.KEY_LIMB_NAMES])) == nl
    assert len(list(j[str(gen)][store.KEY_CONN_NAMES])) == nc
    assert store.limb_rows(obj)[-1] == structure.LimbRow(-1, "Cheetah_LFArm", 0x15, ("front_arm1_l",))
    r = store.conn_rows(obj)[-1]
    want = next(c for c in src.limb_connections if c.flags2 & M == 0x15)
    assert (r.src, r.name, r.flags1, r.flags2, r.bone, r.corners) == (-1, "Cheetah_LFArm", 0x800, 0x15, "neck1",
                                                                       ring_corners(want.vertex_indices)), r
    assert "the existing bone 'front_arm1_l'" in text, text
    w = str(obj.get("bz_last_socket_warning", ""))
    assert w.startswith("limb 'Cheetah_LFArm' (L Front Arm) has no Right partner of class 0x16"), w
    assert bpy.context.scene.bz_boner.active_conn == len(store.connections(obj)) - 1
    add(obj, "RFArm", q_r)
    assert str(obj.get("bz_last_socket_warning", "x")) == ""
    assert len(arm.data.bones) == nbones and [i[0] for i in connops._stub_items(None, bpy.context)] == ["NONE"]
    data, msg = exported(obj, "/tmp/bz_boner_b4_arms.sgm")
    m = read(data)
    assert "limbs: added 2 (Cheetah_LFArm, Cheetah_RFArm)" in msg and "bones:" not in msg, msg
    assert sorted(map(lrec, m.limbs)) == sorted(map(lrec, src.limbs))
    assert sorted(map(crec, m.limb_connections)) == sorted(map(crec, src.limb_connections))
    m.limbs.sort(key=lambda l: [x.name for x in src.limbs].index(l.name))
    m.limb_connections.sort(key=lambda c: [x.name for x in src.limb_connections].index(c.name))
    assert sgm_parser.write(m) == raw, "the re-added arm stubs differ from the stock records"
    val = validate_verdict(obj, "QUICK")
    assert not core.blocking(val) and not [v for v in val if v.startswith("COMBINE ERROR")], \
        [v for v in val if v.startswith(("COMBINE ERROR", "ERROR"))] or core.blocking(val)
    assert not [v for v in val if "stub" in v.lower() and v.startswith("STOCK")], val
    assert str(obj.get("bz_combine_summary", "")), "the Quick tier did not run"
    obj = _history(bpy.ops.ed.undo, obj)
    assert len(store.limb_names(obj)) == nl + 1 and store.limb_rows(obj)[-1].name == "Cheetah_LFArm"
    obj = _history(bpy.ops.ed.undo, obj)
    assert exported(obj)[0] == deleted and len(store.connections(obj)) == nc
    obj = _history(bpy.ops.ed.redo, obj); obj = _history(bpy.ops.ed.redo, obj)
    assert exported(obj)[0] == data
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"B CHEETAH ARMS: refusals (none / two / head patch / the side wing stub's patch); LFArm + RFArm on the stock "
        f"patches with the existing front_arm1_l/r (one generation each, one-sided warning then clear, no rig bone "
        f"added) -> /tmp/bz_boner_b4_arms.sgm = the stock records (bytes once reordered); Quick tier "
        f"{obj.get('bz_combine_summary', '')}; Undo x2 identical, Redo x2 identical")


# ------------------------------------------------------------------------------------------------------------- C
def edit_bones(arm, fn):
    obj = bpy.context.view_layer.objects.active
    mode = obj.mode
    rig._set_mode(bpy.context, obj, "OBJECT")
    rig._set_mode(bpy.context, arm, "EDIT")
    try:
        fn(arm.data.edit_bones)
    finally:
        rig._set_mode(bpy.context, arm, "OBJECT")
        bpy.context.view_layer.objects.active = obj
        rig._set_mode(bpy.context, obj, mode)


def patch_of(obj, quad):
    P = store.patches(obj)
    return next(p for p in range(len(P)) if set(boner.stub_quad(P[p])) == set(quad))


def anim_bytes(path):
    from ICParse.iff.document import IffDocument
    from ICParse.codecs import anim as ICanim
    return {ICanim.read_anim(ch).name: ch.serialize() for ch in IffDocument.load(path).find_all("ANIM")}


def part_c():
    from blender_native import ops
    sys.path.insert(0, HERE)
    import stub_socket_test as ST                     # the pure test's strip helper (ICParse only)
    stock = sgm_parser.read(CHEETAH)
    classes = (0x501, 0x502, 0x301, 0x302, 0x15, 0x16)
    suffixes = ("LSWing", "RSWing", "LTWing", "RTWing", "LFArm", "RFArm")
    made = ["swing1_l", "swing1_r", "top_wing_l", "top_wing_r", "front_arm1_l", "front_arm1_r"]
    ST.strip_stubs(CHEETAH, set(classes), BARE)
    bare = open(BARE, "rb").read()
    bm0 = sgm_parser.read(BARE)
    fresh()
    obj = imp(BARE, clips_as_actions=True)
    arm = store.rig(obj)
    n_src = len(arm.data.bones)
    assert n_src == len(stock.bones) - 6 and exported(obj)[0] == bare
    val = validate_verdict(obj)
    assert sum("Required limb class not found (can be a stub limb)" in v for v in val) == 6, [v for v in val if "oe:" in v]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    q = {cls: stock_quad(stock, cls) for cls in classes}
    assert [i[0] for i in connops._stub_items(None, bpy.context)] == list(suffixes)

    t = add(obj, "LSWing", q[0x501])
    assert "the new bone 'swing1_l'" in t and "host bone spine3" in t, t
    b = arm.data.bones.get("swing1_l")
    assert b is not None and b.parent.name == "spine3" and not b.use_deform and not len(b.children)
    assert b.get(rig.KEY_STUB_BONE) == "swing1_l" and int(b.get(rig.KEY_STUB_PARENT)) == store.bone_index(obj, "spine3")
    assert b.get(rig.KEY_BONE_SRC_INDEX) is None
    W = np.array([v.co[:] for v in obj.data.vertices])
    centre = boner.patch_centre(W[store.patches(obj)[patch_of(obj, q[0x501])]])
    assert np.allclose(np.array(b.head_local), centre, atol=1e-4), (b.head_local, centre)
    select_patch_corners(obj, q[0x501])
    refused(bpy.ops.bz.add_stub_socket, ("already the ring of connection 'Cheetah_LSWing'",), slot="RTWing")
    refused(bpy.ops.bz.add_stub_socket, ("another stub socket",), slot="LFArm")
    datas = [exported(obj)[0]]
    for suffix, cls in zip(suffixes[1:], classes[1:]):
        t = add(obj, suffix, q[cls])
        assert "the new bone" in t, t
        if cls in (0x301, 0x302):
            assert f"shared with Cheetah_{'L' if cls == 0x301 else 'R'}SWing" in t, t
        datas.append(exported(obj)[0])
    data = datas[-1]
    assert [i[0] for i in connops._stub_items(None, bpy.context)] == ["NONE"]
    ok_b, bad_b = rig.created_stub_bones(arm)
    assert sorted(x.name for x in ok_b) == sorted(made) and not bad_b
    assert rig.skeleton_error(arm, core.read_source(BARE)) is None
    # Undo all six adds one step at a time (each takes its bone off the rig), Redo all six
    name = obj.name                                     # a memfile step restores new ID pointers: look them up again
    for k in range(6, 0, -1):
        obj = _history(bpy.ops.ed.undo, obj)
        obj = bpy.data.objects[name]
        arm = store.rig(obj)
        assert obj.mode == "EDIT" and sorted(x.name for x in rig.created_stub_bones(arm)[0]) == sorted(made[:k - 1]), k
        assert exported(obj)[0] == (datas[k - 2] if k >= 2 else bare), f"Undo to {k - 1} sockets"
    for k in range(1, 7):
        obj = _history(bpy.ops.ed.redo, obj)
        obj = bpy.data.objects[name]
        assert exported(obj)[0] == (datas[k - 1]), f"Redo to {k} sockets"
    arm = store.rig(obj)
    assert sorted(x.name for x in rig.created_stub_bones(arm)[0]) == sorted(made)

    # the guard: a renamed stub bone exports under its new name (its limb list follows, B6); a copy of one, a bone under
    # one, a re-parented one and a bone added at the root are refused
    edit_bones(arm, lambda eb: setattr(eb["swing1_l"], "name", "wing_x"))
    data_r, msg_r = exported(obj)
    m_r = read(data_r)
    assert [x.name for x in m_r.bones[n_src:]] == ["wing_x" if x == "swing1_l" else x for x in made], msg_r
    assert next(l for l in m_r.limbs if int(l.limb_id) & M == 0x501).bone_names == ["wing_x"]
    assert "swing1_l" not in {x.name for x in m_r.bones}

    def copy(eb):
        c = eb.new("wing_x.001"); c.head = eb["wing_x"].head; c.tail = eb["wing_x"].tail; c.parent = eb["spine3"]
        for k in (rig.KEY_STUB_BONE, rig.KEY_STUB_PARENT):
            c[k] = eb["wing_x"][k]
    edit_bones(arm, copy)
    export_refused(obj, ("carry that stamp too (copies)",))
    edit_bones(arm, lambda eb: eb.remove(eb["wing_x.001"]))
    edit_bones(arm, lambda eb: setattr(eb["wing_x"], "name", "swing1_l"))

    def child(eb):
        c = eb.new("wing_tip"); c.head = eb["swing1_l"].tail; c.tail = c.head + eb["swing1_l"].vector
        c.parent = eb["swing1_l"]
    edit_bones(arm, child)
    export_refused(obj, ("bone 'wing_tip' was added", "bone 'swing1_l' was added (a stub bone Add Stub Socket made, but "
                         "a stub bone is a leaf"))
    edit_bones(arm, lambda eb: eb.remove(eb["wing_tip"]))
    edit_bones(arm, lambda eb: setattr(eb["swing1_l"], "parent", eb["spine2"]))
    export_refused(obj, ("it was re-parented since it was created",))
    edit_bones(arm, lambda eb: setattr(eb["swing1_l"], "parent", eb["spine3"]))

    def plain(eb):
        c = eb.new("horn_l"); c.head = (0, 0, 1); c.tail = (0, 0.1, 1)
    edit_bones(arm, plain)
    export_refused(obj, ("bone 'horn_l' was added at the root",))
    edit_bones(arm, lambda eb: eb.remove(eb["horn_l"]))

    data, msg = exported(obj, "/tmp/bz_boner_b4_created.sgm")
    if data != datas[-1]:
        open("/tmp/bz_dbg_a.sgm", "wb").write(datas[-1]); open("/tmp/bz_dbg_b.sgm", "wb").write(data)
    assert data == datas[-1], "the guard round trip changed the export"
    assert "bones: added 6 stub bones (swing1_l, swing1_r, top_wing_l, top_wing_r, ...)" in msg, msg
    m = read(data)
    assert sorted(lrec(x, True) for x in m.limbs) == sorted(lrec(x, True) for x in stock.limbs), "LIMB records"
    assert sorted(map(crec, m.limb_connections)) == sorted(map(crec, stock.limb_connections)), "LCON records"
    assert [x.name for x in m.bones[n_src:]] == made and m.skel_info == len(m.bones)
    bw = R.bind_world_matrices(m)
    Wm = np.asarray(R.world_vertices(m), dtype=np.float64)
    Pm = np.array([list(p.control_points) for p in m.patches], dtype=np.int64)
    rot_err = pos_err = 0.0
    for k, name in enumerate(made):
        x = m.bones[n_src + k]
        assert m.bones[x.parent].name == ("neck1" if "arm" in name else "spine3") and int(x.meta0) == 1, name
        assert (x.start_vertex, x.vertex_count, x.meta5) == (0, 0, 0) and all(v == 0.0 for v in x.tail)
        local = np.array(x.transform, dtype=np.float64).reshape(4, 3).T
        rot_err = max(rot_err, float(np.abs(local[:, :3] - np.eye(3)).max()))
        patch = next(p for p in range(len(Pm)) if set(boner.stub_quad(Pm[p])) == set(q[classes[k]]))
        pos_err = max(pos_err, float(np.abs(bw[n_src + k][:3, 3] - boner.patch_centre(Wm[Pm[patch]])).max()))
    assert rot_err < 1e-4 and pos_err < 1e-4, (rot_err, pos_err)
    assert list(R.vertex_bone_owners(m)) == list(R.vertex_bone_owners(bm0))
    assert anim_bytes("/tmp/bz_boner_b4_created.sgm") == anim_bytes(BARE), "a kept clip changed"
    val = validate_verdict(obj, "QUICK")
    assert not [v for v in val if "can be a stub limb" in v], [v for v in val if "oe:" in v]
    assert not [v for v in val if v.startswith("STOCK") and "stub" in v], [v for v in val if v.startswith("STOCK")]
    assert not core.blocking(val) and not [v for v in val if v.startswith("COMBINE ERROR")], \
        [v for v in val if v.startswith("COMBINE ERROR")] or core.blocking(val)
    summary = str(obj.get("bz_combine_summary", ""))
    assert summary, "the Quick tier did not run"

    # an Action keying a created stub bone: re-encoded, with no track for it; the other clips keep their bytes
    acts = actions.actions_of(arm)
    act = next((a for a in acts if a.get(actions.KEY_CLIP) == "idle1"), acts[0])
    clip_name = str(act.get(actions.KEY_CLIP))
    ad = arm.animation_data
    prev = ad.action
    actions._assign(arm, act)
    pb = arm.pose.bones["swing1_l"]
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = (0.9, 0.3, 0.0, 0.0)
    pb.keyframe_insert("rotation_quaternion", frame=int(act.frame_range[0]))
    ad.action = prev
    plan = actions.plan_export(arm)
    assert [c for _a, c in plan.encode] == [clip_name], plan.encode
    data2, msg2 = exported(obj, OUT)
    m2 = read(data2)
    assert not any(tr.bone_name.lower() in set(made) for a in m2.animations for tr in a.tracks)
    assert next(a for a in m2.animations if a.name == clip_name).tracks
    assert f"clips: {len(bm0.animations) - 1} kept, 1 written" in msg2, msg2
    got, want = anim_bytes(OUT), anim_bytes(BARE)
    kept = [n for n in want if n != clip_name and got.get(n) == want[n]]
    assert len(kept) == len(want) - 1 and got[clip_name] != want[clip_name], (len(kept), len(want))

    # a socket deleted with its limb: its created bone stays in no limb (not written, warned); added again, it is reused
    ok(bpy.ops.bz.delete_connection, index=conn_index(obj, 0x501))
    ok(bpy.ops.bz.delete_limb, index=limb_index(obj, 0x501))
    info = {}
    m3 = ops.edited_model(obj, bpy.context, animations=False, info=info)
    assert any("swing1_l is on the rig but in no limb, so not exported" in w for w in info["warnings"]), info["warnings"]
    assert len(m3.bones) == len(m.bones) - 1 and "swing1_l" not in [x.name for x in m3.bones]
    t = add(obj, "LSWing", q[0x501])
    assert "the existing bone 'swing1_l'" in t and arm.data.bones.get("swing1_l.001") is None, t
    assert len(read(exported(obj)[0]).bones) == len(m.bones)
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"C CREATED BONES: cheetah without wing / arm stubs (bones too, clips as Actions): 6 sockets create stamped leaf "
        f"bones (top wings share the side wings' patches; refusals); Undo x6 one bone at a time (identical to each "
        f"earlier export, the source at the end), Redo x6 identical; a renamed stub bone exports; the guard refuses copy / child / re-parent / plain "
        f"add; /tmp/bz_boner_b4_created.sgm = stock LIMB / LCON records, bones last (flags 1, no range, rotation "
        f"{rot_err:.1e}, centre {pos_err:.1e}), kept clips byte-identical; Quick tier {summary}; a keyed stub bone's clip "
        f"re-encoded without its track ({len(kept)} kept); delete + re-add reuses the bone")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    stock = sgm_parser.read(CHEETAH)
    q = {cls: stock_quad(stock, cls) for cls in (0x501, 0x15)}

    def start(setup=None):
        fresh()
        obj = imp(BARE)
        arm = store.rig(obj)
        if setup:
            setup(obj, arm)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        select_patch_corners(obj, q[0x501])
        return obj, arm

    def untouched(obj, n_limbs):
        obj = bpy.data.objects[obj.name]
        assert obj.mode == "EDIT" and bpy.context.view_layer.objects.active == obj, (obj.mode,
                                                                                    bpy.context.view_layer.objects.active)
        assert not rig.created_stub_bones(store.rig(obj))[0] and len(store.limb_rows(obj)) == n_limbs
        return obj

    def own_collection(arm):
        c = bpy.data.collections.new("RigOnly")
        bpy.context.scene.collection.children.link(c)
        for uc in list(arm.users_collection):
            uc.objects.unlink(arm)
        c.objects.link(arm)
        return bpy.context.view_layer.layer_collection.children["RigOnly"]

    # a rig Blender won't edit: refused in plain words, the creature stays in Edit Mode and active, nothing written
    obj, arm = start(lambda o, a: setattr(a, "hide_viewport", True))
    n = len(store.limb_rows(obj))
    refused(bpy.ops.bz.add_stub_socket, ("the rig 'Cheetah_rig' is disabled in viewports (the monitor icon)",),
            slot="LSWing")
    untouched(obj, n)
    obj, arm = start(lambda o, a: setattr(own_collection(a), "exclude", True))
    refused(bpy.ops.bz.add_stub_socket, ("the rig 'Cheetah_rig' is not in the view layer (its collection is excluded)",),
            slot="LSWing")
    untouched(obj, n)
    for how in ("eye", "collection"):                   # hidden with the eye / a monitor-disabled collection: works
        obj, arm = start(lambda o, a: a.hide_set(True) if how == "eye" else setattr(own_collection(a).collection,
                                                                                    "hide_viewport", True))
        ok(bpy.ops.bz.add_stub_socket, slot="LSWing")
        assert [b.name for b in rig.created_stub_bones(store.rig(obj))[0]] == ["swing1_l"] and obj.mode == "EDIT", how
    # a mode switch failing inside the bone creation (whatever the reason): every restore step still runs
    obj, arm = start()
    real = rig._set_mode

    def failing(context, o, mode):
        if o == arm:                                    # like a hidden rig: every mode switch on it fails
            raise RuntimeError("Operator bpy.ops.object.mode_set.poll() simulated failure")
        return real(context, o, mode)
    rig._set_mode = failing
    try:
        refused(bpy.ops.bz.add_stub_socket, ("simulated failure",), slot="LSWing")
    finally:
        rig._set_mode = real
    untouched(obj, n)

    # created stub bones: other limbs and connection hosts are told what the bone is
    ok(bpy.ops.bz.add_stub_socket, slot="LSWing")
    select_patch_corners(obj, q[0x15])
    ok(bpy.ops.bz.add_stub_socket, slot="LFArm")
    torso = limb_index(obj, boner.TORSO_CLASS)
    words = ("'swing1_l' is a stub bone Add Stub Socket created: it belongs only to its stub limb",)
    refused(bpy.ops.bz.limb_add_bone, words, index=torso, bone="swing1_l")
    refused(bpy.ops.bz.conn_set_host, words, index=conn_index(obj, 0x15), bone="swing1_l")
    refused(bpy.ops.bz.limb_add_bone, ("'no_such_bone' is not a bone of this creature's skeleton",), index=torso,
            bone="no_such_bone")

    # a bone that owns points moved into a new stub limb keeps its flags (only point-less bones get the stub byte)
    ok(bpy.ops.bz.limb_add_bone, index=limb_index(obj, 0x15), bone="tail1")
    data, msg = exported(obj, "/tmp/bz_boner_stub_d.sgm")          # (OUT keeps part C's keyed export)
    m = read(data)
    t1 = next(b for b in m.bones if b.name.lower() == "tail1")
    src_t1 = next(b for b in sgm_parser.read(BARE).bones if b.name.lower() == "tail1")
    assert int(t1.meta0) == int(src_t1.meta0) and "stub flag set on" not in msg, (t1.meta0, msg)
    assert int(next(b for b in m.bones if b.name == "front_arm1_l").meta0) == 1
    ok(bpy.ops.bz.limb_add_bone, index=limb_index(obj, 0x80), bone="tail1")    # tail1 back in the Tail

    # a created stub bone deleted on the rig while its limb still lists it: the export warns and writes, and Validate
    # agrees -- the Object Editor LOADS it (not blocking: its own WARN line), ComboTest.exe fails on it (COMBINE ERROR)
    edit_bones(store.rig(obj), lambda eb: eb.remove(eb["front_arm1_l"]))
    info = {}
    from blender_native import ops
    ops.edited_model(obj, bpy.context, animations=False, info=info)
    assert any("limb 'Cheetah_LFArm' lists bone 'front_arm1_l', which is not in the skeleton" in w
               for w in info["warnings"]), info["warnings"]
    val = validate_verdict(obj)
    assert not core.blocking(val), core.blocking(val)
    assert "WARN oe: Bone 'front_arm1_l' referenced by limb 'Cheetah_LFArm' was not found in skeleton" in val, val
    assert any(v.startswith("COMBINE ERROR") and "front_arm1_l" in v for v in val), [v for v in val if "COMBINE" in v]
    bpy.ops.object.mode_set(mode="OBJECT")
    say("D EDGES: a viewport-disabled rig / excluded collection refused in plain words (creature kept in Edit Mode, "
        "nothing written); eye-hidden and collection-disabled rigs work; a failing mode switch restores everything; a "
        "created stub bone named in Add Active Bone / Set Host says what it is; a bone with points in a stub limb keeps "
        "its flags; a deleted created bone its limb lists: export warns, Validate not blocking (OE WARN + COMBINE ERROR)")


def main():
    blender_native.register()
    part_a()
    part_b()
    part_c()
    part_d()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_boner_stub_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_boner_stub_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
