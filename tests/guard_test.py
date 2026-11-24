"""Guard rails, in headless Blender: the add-on refuses (with the reason) instead of corrupting or dropping edits.
    blender --background --python blender_native/tests/guard_test.py

A. the control-point pool: native Delete / Merge by Distance / Sort / Duplicate / a raw new vertex block Export
   (CANCELLED, no file, "control-point pool" + "Undo"), Validate says EXPORT BLOCKED, the overlay is flagged and the
   pool-indexing operators are disabled; Undo recovers a byte-identical export;
A2. tables that disagree: a generation the journal lacks, the id attribute removed, a short UV / ring / encoding / name
   table and a ring index out of the pool all block Export; Apply UVs is disabled; the journal follows Undo Steps;
B. the skeleton: a renamed bone exports (11 kept clips, WARN for a name the game looks up); add / re-parent / delete a
   bone block Export; renaming back gives a byte-identical file; sampling a missing bone raises; two sibling bones
   swapping names are two renames (identity stamps) and a rename chain (refused); a legacy rig says so; Set Connection
   Bone offers the skeleton's names and writes a renamed host under its new name;
   an unknown host bone is refused; a Ctrl+G reassignment is exported (and said), an unskinned or blended point is
   refused; a deleted rig is named as the reason;
C. SGM operators in EDIT mode and Undo / Redo: Extrude, Assign Limb, Set Connection Bone and Set Connection Ring write
   object tables that edit-mesh undo does not restore -- the generation journal re-selects the tables of each undo /
   redo step, so the export after every Ctrl+Z / Ctrl+Shift+Z is exactly the export of that state, also across Tab
   (the session copy of the journal); an Undo of a migrating ring edit restores the pre-v2 tables; a UV cage built
   before Extrude refuses until rebuilt;
D. Actions by identity: the same creature imported twice, and deleted then re-imported, exports byte-identical;
   Clips to Actions twice is refused; an F-curve modifier re-encodes its clip;
E. linked data by identity: a renamed rig and a renamed image still export their edits; a deleted image refuses; a
   saved .blend reopens exportable with its paint;
F. Blender-side state the file can't hold refuses: an object transform, shape keys, another modifier;
G. the source file changing after import refuses; Symmetrize in edit mode keeps an unflushed move; an object stored
   before the guard is migrated and still exports byte-identical.
"""
import math
import os
import shutil
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import traceback

import numpy as np
import bpy
import bmesh
from mathutils import Matrix

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops, draw, rig, actions as bzactions  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = REPO + "/Assets/stock/snail.sgm"
SRC_BYTES = open(SRC, "rb").read()
OUT = "/tmp/bz_guard_out.sgm"
_log = open("/tmp/bz_guard_log.txt", "w")


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


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"


def imp(path=SRC, **kw):
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def export(obj, path=OUT):
    """('FINISHED' | 'CANCELLED', message, bytes or None)."""
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
        msg = obj.get("bz_last_export", "")
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
    except RuntimeError as e:                    # an ERROR report raises in background mode
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def blocked(obj, *words):
    state, msg, data = export(obj)
    assert state == "CANCELLED" and data is None, (state, msg[:200])
    for w in words:
        assert w in msg, (w, msg)
    return msg


def identical(obj, want=SRC_BYTES):
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == want, (state, msg[:300], data == want if data else None)


def set_selection(obj, idx):
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in set(idx)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def select_in_order(obj, idx):
    """Shift+click order: the bmesh select history."""
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = False
    bm.select_history.clear()
    for i in idx:
        bm.verts[i].select = True
        bm.select_history.add(bm.verts[i])
    bmesh.update_edit_mesh(obj.data)


def validate_verdict(obj):
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


# ----------------------------------------------------------------------------------------------------- A. pool
def part_a():
    fresh()
    obj = imp()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")

    def native(label, fn, words):
        nonlocal obj                                    # an Undo below replaces the object (Blender 5.3)
        set_selection(obj, [])
        bpy.ops.ed.undo_push(message=f"before {label}")
        fn()
        bpy.ops.ed.undo_push(message=f"after {label}")
        msg = blocked(obj, "control-point pool", "Undo", *words)        # exported from EDIT mode: flushed first
        val = validate_verdict(obj)
        assert val and val[0].startswith("EXPORT BLOCKED: a Blender topology tool"), val[:1]
        st = draw._sync(obj, 4, 1.0)                                      # no GPU work on the error path
        assert st.get("pool_error") and draw.overlay_error(obj.name), "the overlay was not flagged"
        for op in (bpy.ops.bz.extrude_patch, bpy.ops.bz.select_limb, bpy.ops.bz.select_kind, bpy.ops.bz.assign_bone,
                   bpy.ops.bz.set_connection_ring, bpy.ops.bz.pick_ring, bpy.ops.bz.seam_smooth,
                   bpy.ops.bz.bake_surface, bpy.ops.bz.paint_skin, bpy.ops.bz.edit_uvs, bpy.ops.bz.assign_limb):
            assert not op.poll(), f"{op} is enabled on a broken pool"
        assert store.pool_error(obj) and store.pool_error(obj) in (store.pool_error_cached(obj) or store.pool_error(obj))
        obj = _history(bpy.ops.ed.undo, obj)
        draw.invalidate()
        assert store.pool_error(obj) is None, store.pool_error(obj)
        identical(obj)
        say(f"A {label}: blocked ({msg.split('(')[1].split(')')[0]}), Validate EXPORT BLOCKED, overlay flagged, "
            "undo -> byte-identical")

    def delete():
        set_selection(obj, [100]); bpy.ops.mesh.delete(type="VERT")

    def merge():
        bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.remove_doubles(threshold=0.02)

    def sort():
        bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.sort_elements(type="RANDOMIZE", elements={"VERT"})

    def duplicate():
        set_selection(obj, [7]); bpy.ops.mesh.duplicate()

    def raw_vertex():
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.new((0.0, 0.0, 5.0)); bmesh.update_edit_mesh(obj.data)

    native("delete", delete, ["1 point removed"])
    native("merge by distance", merge, ["points removed"])
    native("sort", sort, ["points reordered"])
    native("duplicate", duplicate, ["1 point added"])
    native("new vertex", raw_vertex, ["1 point added"])
    bpy.ops.object.mode_set(mode="OBJECT")


# ------------------------------------------------------------------------------- A2. store-level table guards
def part_a2():
    """Tables that disagree with the mesh or with each other are refused at export (never written short or zeroed)."""
    def case(label, breaker, *words):
        fresh()
        obj = imp(clips_as_actions=False)
        breaker(obj)
        store.forget_cache()
        msg = blocked(obj, *words)
        val = validate_verdict(obj)
        assert val and val[0].startswith("EXPORT BLOCKED"), (label, val[:1])
        say(f"A2 {label}: blocked ({msg.strip()[:90]}...)")
        return obj

    def gen_not_in_journal(obj):
        store.set_point_ints(obj, store.ATTR_GEN, np.full(len(obj.data.vertices), 4242))

    def cp_removed(obj):
        obj.data.attributes.remove(obj.data.attributes[store.ATTR_CP])

    def uv_short(obj):
        obj[store.KEY_PATCH_UV] = list(obj[store.KEY_PATCH_UV])[:-8]

    def ring_out_of_pool(obj):
        v = list(obj[store.KEY_CONN_VERTS]); v[0] = 999999
        obj[store.KEY_CONN_VERTS] = v

    def enc_short(obj):
        obj[store.KEY_CONN_ENC] = list(obj[store.KEY_CONN_ENC])[:-1]

    def verts_short(obj):
        obj[store.KEY_CONN_VERTS] = list(obj[store.KEY_CONN_VERTS])[:-1]

    def names_short(obj):
        obj[store.KEY_CONN_NAMES] = list(obj[store.KEY_CONN_NAMES])[:-1]

    case("generation not in the journal", gen_not_in_journal, "journal no longer holds")
    case(".bz_cp removed", cp_removed, "control-point id attribute", "removed")
    case("UV table short by one row", uv_short, "per-patch UV table has")
    case("ring index out of the pool", ring_out_of_pool, "ring index out of")
    case("ring encoding table short", enc_short, "ring encoding table has")
    case("ring table short", verts_short, "connection rings table holds")
    case("connection names short", names_short, "entries for")

    # Apply UVs on a cage whose creature's pool is broken is disabled
    fresh()
    obj = imp(clips_as_actions=False)
    assert bpy.ops.bz.edit_uvs() == {"FINISHED"}
    cage = bpy.context.view_layer.objects.active
    assert cage is not obj and bpy.ops.bz.apply_uvs.poll()
    obj.data.attributes.remove(obj.data.attributes[store.ATTR_GEN])
    store.forget_cache()
    assert not bpy.ops.bz.apply_uvs.poll(), "Apply UVs is enabled on a broken pool"
    # the journal keeps as many generations as Blender keeps undo steps
    prefs = bpy.context.preferences.edit
    old_steps = prefs.undo_steps
    prefs.undo_steps = 200
    assert store.journal_max() >= 200
    prefs.undo_steps = old_steps
    say("A2 Apply UVs disabled on a broken pool; journal size follows Undo Steps")


# ------------------------------------------------------------------------------------------------- B. skeleton
def part_b():
    fresh()
    obj = imp()
    arm = store.rig(obj)

    def arm_edit(fn):
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        fn(arm.data.edit_bones)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.context.view_layer.objects.active = obj

    # a rename exports (B6): the skeleton, the limb list and host follow; the clips stay kept; Validate warns (neck1 is a
    # name the game looks up); renaming back is byte-identical
    arm_edit(lambda eb: setattr(eb["neck1"], "name", "neck1_x"))
    state, msg, data = export(obj)
    assert state == "FINISHED" and "bones: renamed 1 (neck1 -> neck1_x)" in msg, (state, msg[:300])
    assert "neck1_x" in [b.name for b in SP.read(OUT).bones] and "neck1" not in [b.name for b in SP.read(OUT).bones]
    plan = bzactions.plan_export(arm)
    assert len(plan.kept) == 11 and not plan.encode and not plan.drop, (plan.kept, plan.encode, plan.drop)
    val = validate_verdict(obj)
    assert not core.blocking(val) and any(v.startswith("WARN export: bone 'neck1' renamed to 'neck1_x'") for v in val), \
        core.blocking(val) or val
    arm_edit(lambda eb: setattr(eb["neck1_x"], "name", "neck1"))
    plan = bzactions.plan_export(arm)
    assert len(plan.kept) == 11 and not plan.encode and not plan.drop, (plan.kept, plan.encode, plan.drop)
    identical(obj)

    def add(eb):
        b = eb.new("ExtraBone"); b.head = (0, 0, 0); b.tail = (0, 0, 0.1)
    arm_edit(add)
    blocked(obj, "bone 'ExtraBone' was added")
    arm_edit(lambda eb: eb.remove(eb["ExtraBone"]))
    identical(obj)
    parent = {}

    def reparent(eb):
        parent["was"] = eb["tail1"].parent.name
        eb["tail1"].parent = eb["spine1"]
    arm_edit(reparent)
    # C8: a re-parented source bone exports (its bind world kept, the kept clips' tracks re-expressed under spine1)
    state, msg, data = export(obj)
    assert state == "FINISHED" and f"bones: re-parented 1 (tail1: {parent['was']} -> spine1)" in msg, (state, msg[:300])
    arm_edit(lambda eb: setattr(eb["tail1"], "parent", eb[parent["was"]]))
    identical(obj)
    # C8: a deleted leaf exports too (its record dropped) unless a rule refuses it (a looked-up name, a stub bone, a
    # host, a limb's only bone): the first leaf the rules allow
    from blender_native import topology, boner
    src_model = core.read_source(store.source_path(obj))
    leaf = next(b.name for i, b in enumerate(arm.data.bones) if not b.children
                and not topology.topology_refusals(src_model, topology.BoneTopology(deleted=(int(b["bz_src_index"]),))))
    arm_edit(lambda eb: eb.remove(eb[leaf]))
    state, msg, data = export(obj)
    assert state == "FINISHED" and f"bones: deleted 1 ({leaf}" in msg, (state, msg[:300])
    assert leaf not in [b.name for b in SP.read(OUT).bones]
    leaf = next(b.name for b in arm.data.bones if not b.children and (b["bz_src_index"] == 0 or boner.hard_coded_uses(b.name)))
    arm_edit(lambda eb: eb.remove(eb[leaf]))
    blocked(obj, f"bone '{leaf}' was deleted, but")
    act = bzactions.actions_of(arm)[0]
    try:
        bzactions.sample_world(arm, act, ["no_such_bone"], 1, 2)
        raise AssertionError("sampling a missing bone did not raise")
    except KeyError as e:
        assert "no_such_bone" in str(e)
    say("B skeleton: rename exported (11 kept, WARN), add blocked, re-parent and a leaf delete exported (C8), a looked-up "
        "leaf's delete blocked, restored -> identical, missing bone raises")

    # two sibling bones swap names: the bones keep their identity stamps, so it is two renames, not two rest edits that
    # would skin each one's points with the other's rest -- and a rename chain (each name would mean two bones): refused
    fresh()
    obj = imp()
    arm = store.rig(obj)
    a, b = next((x.name, y.name) for x in arm.data.bones for y in arm.data.bones
                if x.parent is not None and x.parent == y.parent and x.name < y.name and not x.children and not y.children)

    def swap(eb):
        eb[a].name = "__swap"; eb[b].name = a; eb["__swap"].name = b
    arm_edit(swap)
    blocked(obj, f"bone '{a}' was renamed to '{b}' and bone '{a}'", f"'{a}' would mean two bones",
            f"bone '{b}' was renamed to '{a}' and bone '{b}'")
    arm_edit(swap)
    identical(obj)
    # a rig imported before the bone snapshot: compared with the source file's names, and it says so
    names = arm[rig.KEY_BONE_SRC_NAMES]
    del arm[rig.KEY_BONE_SRC_NAMES]
    arm.data.bones[a].name = a + "_x"
    msg = blocked(obj, "imported before the bone snapshot")
    arm.data.bones[a + "_x"].name = a
    arm[rig.KEY_BONE_SRC_NAMES] = names
    identical(obj)
    say(f"B swap: '{a}' <-> '{b}' refused as a rename chain, swapped back -> identical; legacy rig note: {msg[:60]}")

    # Set Connection Bone offers the skeleton's import names; on a renamed skeleton it runs and the export writes the
    # host under the bone's new name
    arm.data.bones["neck1"].name = "neck1_tmp"
    items = [i[0] for i in ops._bone_items(None, bpy.context)]
    assert "neck1" in items and "neck1_tmp" not in items, items[:5]
    host0 = store.connections(obj)[0][1]
    r = bpy.ops.bz.set_connection_bone("EXEC_DEFAULT", True, conn="0", bone="neck1")
    assert r == {"FINISHED"}, r
    state, msg, data = export(obj)
    assert state == "FINISHED" and SP.read(OUT).limb_connections[0].bone_name == "neck1_tmp", (state, msg[:200])
    bpy.ops.bz.set_connection_bone("EXEC_DEFAULT", True, conn="0", bone=host0)
    arm.data.bones["neck1_tmp"].name = "neck1"
    identical(obj)
    # a host bone that is not in the skeleton is refused by the builder
    conns = store.connections(obj)
    conns[0] = (conns[0][0], "no_such_bone", conns[0][2])
    store.set_connections(obj, conns)
    blocked(obj, "host bone 'no_such_bone' is not in the skeleton")
    conns[0] = (conns[0][0], SP.read(SRC).limb_connections[0].bone_name, conns[0][2])
    store.set_connections(obj, conns)
    identical(obj)
    say("B connection bone: skeleton names offered, a renamed host exported under its new name, unknown host bone refused")

    # skinning with Blender's own vertex-group tools: a rigid reassignment is exported (and said); a point left in no
    # bone's group, or with blended weights, is refused (the .sgm skins each point to one bone)
    owners = store.vert_bone(obj)
    names = store.bone_names(obj)
    p = int(np.nonzero(owners == owners[0])[0][0])
    old_g = obj.vertex_groups[names[owners[p]]]
    new_bone = next(i for i, n in enumerate(names) if i != owners[p] and obj.vertex_groups.get(n) is not None)
    new_g = obj.vertex_groups[names[new_bone]]
    old_g.remove([p]); new_g.add([p], 1.0, "REPLACE")
    state_, msg, data = export(obj)
    assert state_ == "FINISHED" and "skinning:" in msg and "byte-identical" not in msg, (state_, msg)
    got = np.bincount(np.asarray(R.vertex_bone_owners(SP.read(OUT))), minlength=len(names))
    was = np.bincount(np.asarray(R.vertex_bone_owners(SP.read(SRC))), minlength=len(names))
    assert got[new_bone] == was[new_bone] + 1 and got[owners[p]] == was[owners[p]] - 1, (got[new_bone], was[new_bone])
    val = validate_verdict(obj)
    assert any(v.startswith("WARN export: 1 control point(s) re-skinned through the vertex groups") for v in val), val
    new_g.remove([p])
    blocked(obj, "1 in no bone's vertex group", f"points {p}", "Assign Selected to Bone")
    old_g.add([p], 0.5, "REPLACE"); new_g.add([p], 0.5, "REPLACE")
    blocked(obj, "1 with blended or partial weights")
    new_g.remove([p]); old_g.add([p], 1.0, "REPLACE")
    identical(obj)
    say(f"B vertex groups: Ctrl+G point {p} -> '{names[new_bone]}' exported (WARN said); unskinned and blended refused; "
        "restored -> identical")

    # the rig deleted: the reason is the rig, not its now-empty Armature modifier
    rig_obj = store.rig(obj)
    bpy.data.objects.remove(rig_obj, do_unlink=True)
    blocked(obj, "is gone")
    say("B rig deleted: refused naming the rig")


# --------------------------------------------------------------------------------------- C. edit-mode undo journal
def build_open_creature(path="/tmp/bz_guard_open.sgm"):
    d = core.load_creature(SRC)
    m = d.model
    keep = list(range(len(d.patches) - 1))
    data = core.export_grown(SRC, d.verts, d.patches[keep], np.array(R.vertex_bone_owners(m)), d.patch_mat[keep],
                             d.patch_uv[keep], np.array([p.type for p in m.patches])[keep], core._patch_limb_of(m)[keep])
    open(path, "wb").write(data)
    return path


def part_c():
    fresh()
    path = build_open_creature()
    obj = imp(path, clips_as_actions=False)
    ctx = bpy.context
    patches = store.patches(obj)
    users = core.side_users(patches)
    pi, side, _k = next((p, s, k) for p, s, k in core.patch_sides(patches) if len(users[k]) == 1)
    corners = [int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])]
    bpy.ops.object.mode_set(mode="EDIT")

    def snap():
        return ops.export_bytes(obj, ctx, animations=False)

    E0 = snap()
    assert E0 == open(path, "rb").read()
    set_selection(obj, corners)
    bpy.ops.ed.undo_push(message="select side")
    assert bpy.ops.bz.extrude_patch("EXEC_DEFAULT", True) == {"FINISHED"}
    E1 = snap()
    new_patch = store.patches(obj)[-1]
    set_selection(obj, [int(c) for c in new_patch[[0, 3, 12, 15]]])
    bpy.ops.ed.undo_push(message="select new patch")
    limb = next(i for i in range(len(store.limb_names(obj))) if i != int(store.patch_limb(obj)[-1]))
    assert bpy.ops.bz.assign_limb("EXEC_DEFAULT", True, limb=str(limb)) == {"FINISHED"}
    E2 = snap()
    assert bpy.ops.bz.set_connection_bone("EXEC_DEFAULT", True, conn="0", bone="pelvis") == {"FINISHED"}
    E3 = snap()
    head = store.connections(obj)[0][2]
    real_stock_dir = ops._stock_dir
    ops._stock_dir = lambda _ctx: ""                    # no stock partners: the picked start is kept, so the ring changes
    try:
        select_in_order(obj, head[2:] + head[:2])
        bpy.ops.ed.undo_push(message="select ring")
        assert bpy.ops.bz.set_connection_ring("EXEC_DEFAULT", True, conn="0") == {"FINISHED"}
    finally:
        ops._stock_dir = real_stock_dir
    assert store.connections(obj)[0][2] == head[2:] + head[:2]
    E4 = snap()
    assert len({E0, E1, E2, E3, E4}) == 5, "every step must change the export"
    m3 = SP.read_bytes(E3) if hasattr(SP, "read_bytes") else None

    def state():
        bm = bmesh.from_edit_mesh(obj.data)
        return len(bm.verts), int(obj[store.KEY_NPATCH])

    steps = []
    for want, label in ((E3, "undo Set Connection Ring"), (E3, "undo ring selection"), (E2, "undo Set Connection Bone"),
                        (E1, "undo Assign Limb"), (E1, "undo selection"), (E0, "undo Extrude")):
        obj = _history(bpy.ops.ed.undo, obj)
        assert obj.mode == "EDIT", obj.mode
        err = store.pool_error(obj)
        got = snap()
        steps.append((label, state(), got == want))
        assert err is None and got == want, (label, err, state())
    for want, label in ((E1, "redo Extrude"), (E1, "redo selection"), (E2, "redo Assign Limb"),
                        (E3, "redo Set Connection Bone"), (E3, "redo ring selection"), (E4, "redo Set Connection Ring")):
        obj = _history(bpy.ops.ed.redo, obj)
        err = store.pool_error(obj)
        got = snap()
        steps.append((label, state(), got == want))
        assert err is None and got == want, (label, err, state())
    say("C journal:", "; ".join(f"{l} {s} ok" for l, s, _ok in steps))

    # a UV cage built before Extrude can't be mapped onto the grown patch table: refused until rebuilt
    bpy.ops.object.mode_set(mode="OBJECT")
    assert bpy.ops.bz.edit_uvs() == {"FINISHED"}
    ctx.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    patches = store.patches(obj)
    users = core.side_users(patches)
    pi, side, _k = next((p, s, k) for p, s, k in core.patch_sides(patches) if len(users[k]) == 1)
    set_selection(obj, [int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])])
    assert bpy.ops.bz.extrude_patch() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    blocked(obj, "UV cage", "faces for", "Edit UVs")
    assert bpy.ops.bz.edit_uvs() == {"FINISHED"}                   # rebuilt for the grown table
    ctx.view_layer.objects.active = obj
    state_, msg, data = export(obj)
    assert state_ == "FINISHED", msg
    say("C UV cage: stale cage refused after Extrude, rebuilt -> export", msg[:80])
    part_c_tab()
    part_c_legacy_rings()


def part_c_tab():
    """The GUI's undo stack around Tab: an SGM operator in Edit Mode, Tab (a memfile step that restores the object's
    journal from before the edit session), Ctrl+Z / Ctrl+Shift+Z: every step exports a real state (the session copy of
    the journal), never a blocked one."""
    def tab(mode):
        bpy.ops.object.mode_set(mode=mode)
        bpy.ops.ed.undo_push(message="Toggle Edit Mode")

    for label in ("extrude", "connection bone"):
        fresh()
        obj = imp(build_open_creature(), clips_as_actions=False)
        ctx = bpy.context
        bpy.ops.ed.undo_push(message="imported")

        def snap():
            o = next(x for x in bpy.data.objects if store.is_native(x))
            try:
                return ops.export_bytes(o, ctx, animations=False)
            except ops.ExportGatherError as e:
                return f"BLOCKED: {e}"
        states = {"E0": snap()}
        tab("EDIT")
        if label == "extrude":
            patches = store.patches(obj)
            users = core.side_users(patches)
            pi, side, _k = next((p, s_, k) for p, s_, k in core.patch_sides(patches) if len(users[k]) == 1)
            set_selection(obj, [int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])])
            bpy.ops.ed.undo_push(message="Select")
            assert bpy.ops.bz.extrude_patch("EXEC_DEFAULT", True) == {"FINISHED"}
        else:
            assert bpy.ops.bz.set_connection_bone("EXEC_DEFAULT", True, conn="0", bone="pelvis") == {"FINISHED"}
        states["E1"] = snap()
        tab("OBJECT")
        obj.data.vertices[3].co.z += 0.1
        bpy.ops.ed.undo_push(message="Move")
        states["E2"] = snap()
        assert all(isinstance(v, bytes) for v in states.values()) and len(set(states.values())) == 3, label
        names = {v: k for k, v in states.items()}
        walk = []
        for _ in range(5):
            if not bpy.ops.ed.undo.poll():
                break                                  # the bottom of the undo stack
            obj = _history(bpy.ops.ed.undo, obj)
            got = snap()
            walk.append(names.get(got, got[:120] if isinstance(got, str) else "UNKNOWN"))
        assert all(w in states for w in walk) and walk[-1] == "E0", (label, walk)
        rwalk = []
        for _ in range(5):
            if not bpy.ops.ed.redo.poll():
                break
            obj = _history(bpy.ops.ed.redo, obj)
            got = snap()
            rwalk.append(names.get(got, got[:120] if isinstance(got, str) else "UNKNOWN"))
        assert all(w in states for w in rwalk) and rwalk[-1] == "E2", (label, rwalk)
        say(f"C Tab + undo ({label}): undo {walk}, redo {rwalk}")


def part_c_legacy_rings():
    """An object stored before ring store v2 (raw ints, no version / encoding): Set Connection Bone in EDIT mode
    migrates the ring table; Ctrl+Z gives the pre-v2 tables back (raw ints, no version) and the source bytes, Ctrl+Shift+Z
    the edit (the encoding and the version are journaled with the rings)."""
    fresh()
    obj = imp(clips_as_actions=False)
    m = SP.read(SRC)
    raw = [list(c.vertex_indices) for c in m.limb_connections]
    for k in (store.KEY_STORE_VERSION, store.KEY_CONN_ENC):
        del obj[k]
    obj[store.KEY_CONN_VERTS] = [v for r in raw for v in r]
    obj[store.KEY_CONN_COUNTS] = [len(r) for r in raw]
    for g in obj[store.KEY_JOURNAL].keys():
        snap_ = obj[store.KEY_JOURNAL][g]
        for k in (store.KEY_STORE_VERSION, store.KEY_CONN_ENC):
            if k in snap_:
                del snap_[k]
        snap_[store.KEY_CONN_VERTS] = obj[store.KEY_CONN_VERTS].to_list()
        snap_[store.KEY_CONN_COUNTS] = obj[store.KEY_CONN_COUNTS].to_list()
    store.forget_journal_cache()                       # the session copy holds the v2 import tables: forget them
    ctx = bpy.context
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="legacy")
    e0 = ops.export_bytes(obj, ctx)
    assert e0 == SRC_BYTES, "legacy store not identical"
    assert bpy.ops.bz.set_connection_bone("EXEC_DEFAULT", True, conn="0", bone="spine1") == {"FINISHED"}
    e1 = ops.export_bytes(obj, ctx)
    assert e1 != e0 and obj.get(store.KEY_STORE_VERSION) == 2
    obj = _history(bpy.ops.ed.undo, obj)
    e2 = ops.export_bytes(obj, ctx)
    assert obj.get(store.KEY_STORE_VERSION) is None and store.KEY_CONN_ENC not in obj, "the undo kept the v2 keys"
    assert list(obj[store.KEY_CONN_VERTS]) == [v for r in raw for v in r] and e2 == SRC_BYTES, \
        f"undo of the migrating edit: ring0 {store.connections(obj)[0][2][:12]}"
    obj = _history(bpy.ops.ed.redo, obj)
    assert ops.export_bytes(obj, ctx) == e1 and obj.get(store.KEY_STORE_VERSION) == 2
    bpy.ops.object.mode_set(mode="OBJECT")
    say("C legacy rings: migrating edit undone to raw ints without the version (source bytes), redone to v2")


# --------------------------------------------------------------------------------------------- D. Actions by identity
def part_d():
    fresh()
    o1 = imp()
    o2 = imp()
    arm2 = store.rig(o2)
    assert any(a.name.endswith(".001") for a in bzactions.actions_of(arm2))
    identical(o2)
    identical(o1)
    # delete the creature and its rig; the fake-user Actions stay, tagged with the rig's (reused) name
    fresh()
    o1 = imp(); a1 = store.rig(o1)
    bpy.data.objects.remove(o1, do_unlink=True); bpy.data.objects.remove(a1, do_unlink=True)
    o2 = imp(); arm2 = store.rig(o2)
    assert len(bzactions.actions_of(arm2)) == 11, len(bzactions.actions_of(arm2))
    assert len(bzactions.foreign_actions(arm2)) == 11
    identical(o2)
    try:
        r = bpy.ops.bz.import_clips()
        assert r != {"FINISHED"}, "Clips to Actions ran twice"
    except RuntimeError as e:
        assert "already has its clips as Actions" in str(e), e
    # an F-curve modifier changes what Blender plays: that clip is re-encoded (and said), not kept
    act = next(a for a in bzactions.actions_of(arm2) if a.get(bzactions.KEY_CLIP) == "walk")
    fc = bzactions._fcurves(act)[0]
    mod = fc.modifiers.new("NOISE"); mod.strength = 5.0
    plan = bzactions.plan_export(arm2)
    assert "walk" not in plan.kept and "walk" in [c for _a, c in plan.encode], plan
    assert any("NOISE" in w for w in plan.warnings), plan.warnings
    mod.active = False                                   # "active" only means its settings are shown: still evaluated
    assert "walk" in [c for _a, c in bzactions.plan_export(arm2).encode], "an inactive (but unmuted) modifier was ignored"
    fc.modifiers.remove(mod)
    assert "walk" in bzactions.plan_export(arm2).kept
    say("D actions: two imports identical, delete+re-import identical (11 foreign ignored), Clips to Actions twice "
        "refused, NOISE modifier re-encodes")


# ------------------------------------------------------------------------------------ E. linked data, F. object state
def part_ef():
    fresh()
    obj = imp()
    arm = store.rig(obj)
    next(a for a in bzactions.actions_of(arm) if a.name == "walk").name = "walk_renamed"
    arm.name = "MyRig"
    state_, msg, data = export(obj)
    assert state_ == "FINISHED" and obj["bz_rig"] == "MyRig", (state_, msg)
    names = [a.name for a in SP.read(OUT).animations]
    assert "walk_renamed" in names and "walk" not in names, names
    img, _ = store.texture_image(obj, 0)
    img.pixels[0] = 1.0 - img.pixels[0]
    img.name = "renamed_skin"
    state_, msg, data = export(obj)
    assert state_ == "FINISHED" and "textures: 1 edited" in msg, msg
    bpy.data.images.remove(img)
    blocked(obj, "texture 0's image", "missing")
    say("E linked data: renamed rig and renamed image exported, deleted image refused")

    # a saved .blend reopens exportable: the skin images are kept (fake user) and a painted texel is re-packed on save
    fresh()
    obj = imp()
    blend = "/tmp/bz_guard_reload.blend"
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    bpy.context.view_layer.objects.active = obj
    identical(obj)
    img, _ = store.texture_image(obj, 0)
    img.pixels[0] = 1.0 - img.pixels[0]
    state_, before, data_before = export(obj)
    assert state_ == "FINISHED" and "textures: 1 edited" in before, before
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    obj = next(o for o in bpy.data.objects if store.is_native(o))
    bpy.context.view_layer.objects.active = obj
    state_, after, data_after = export(obj)
    assert state_ == "FINISHED" and data_after == data_before and "textures: 1 edited" in after, (state_, after)
    say("E .blend save / reopen: untouched -> identical; a painted texel survives the save (", after[:60], ")")

    fresh()
    obj = imp()
    obj.location.x = 1.0
    blocked(obj, "object transform")
    obj.location.x = 0.0
    obj.shape_key_add(name="Basis")
    blocked(obj, "shape keys")
    obj.shape_key_clear()
    mod = obj.modifiers.new("Sub", "SUBSURF")
    blocked(obj, "modifier(s) Sub")
    obj.modifiers.remove(mod)
    identical(obj)
    say("F object state: transform, shape keys, extra modifier refused; cleared -> identical")


# ------------------------------------------------------------------------------------------------- G. misc
def part_g():
    fresh()
    tmp = "/tmp/bz_guard_src.sgm"
    shutil.copyfile(SRC, tmp)
    obj = imp(tmp)
    with open(tmp, "ab") as f:
        f.write(b"\0\0\0\0")
    blocked(obj, "changed since import")
    shutil.copyfile(SRC, tmp)
    identical(obj)

    # Symmetrize in edit mode keeps a move made in edit mode (no stale read)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    v = bm.verts[500]; v.co.z += 0.5; moved = v.co.z
    bmesh.update_edit_mesh(obj.data)
    assert bpy.ops.bz.symmetrize() == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    assert abs(obj.data.vertices[500].co.z - moved) < 1e-3 or abs(obj.data.vertices[500].co.z - moved) < 0.3, \
        (obj.data.vertices[500].co.z, moved)
    zs = obj.data.vertices[500].co.z
    assert abs(zs - (moved - 0.5)) > 0.2, "Symmetrize wrote the stale (pre-move) position back"

    # an object stored before the pool guard (no ids / generation, owners as an ID property) migrates on export
    fresh()
    obj = imp()
    vb = store.vert_bone(obj)
    for k in (store.KEY_GEN, store.KEY_JOURNAL, store.KEY_POOL_N):
        del obj[k]
    for a in (store.ATTR_CP, store.ATTR_GEN, store.ATTR_VERT_BONE):
        obj.data.attributes.remove(obj.data.attributes[a])
    obj[store.KEY_VERT_BONE] = [int(x) for x in vb]
    identical(obj)
    assert store.point_ints(obj, store.ATTR_CP) is not None and store.KEY_VERT_BONE not in obj
    assert np.array_equal(store.vert_bone(obj), vb)
    say("G source change refused; Symmetrize keeps edit-mode moves; legacy object migrated -> identical")

    # what import could not set up is recorded, reported by Import, and carried into Export / Validate as a warning
    fresh()
    real = store.set_limbs

    def broken(*_a, **_k):
        raise ValueError("forced for the test")
    store.set_limbs = broken
    try:
        obj = imp(clips_as_actions=False)
    finally:
        store.set_limbs = real
    errs = list(obj.get(store.KEY_BUILD_ERRORS, []))
    assert errs == ["limbs and connections: ValueError: forced for the test"], errs
    identical(obj)                                   # no limb / ring tables: the source's are kept, never cleared
    val = validate_verdict(obj)
    assert any(v.startswith("WARN export: import could not set up: limbs and connections") for v in val), val[:6]
    say("G import set-up failure recorded, export keeps the source records, Validate warns")


def main():
    blender_native.register()
    for part in (part_a, part_a2, part_b, part_c, part_d, part_ef, part_g):
        part()
    blender_native.unregister()


try:
    main()
    open("/tmp/bz_guard_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_guard_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
