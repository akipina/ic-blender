"""D10 Half A: the export's refusals, checked at ACTION time, in headless Blender.
    blender --background --python blender_native/tests/action_guard_test.py

Every part makes one mistake Blender allows and asserts the alert is there **before any export**: the issue the
add-on would refuse with is in ``guards.issues`` / the cache the panel and the viewport text read, and its words are
the export's own words (the same function produces both). Then the mistake is taken back and the issue goes.

A. a clean cheetah: no issue at all (shallow or deep), the viewport text is empty, the export is byte-identical;
B. an object transform (Object Mode G): `state`, caught by the depsgraph handler with no export run; the Export
   refusal is the SAME sentence; cleared -> gone;
C. shape keys -> `state`; D. an extra modifier (Subdivision Surface) -> `state`;
E. the Armature modifier switched to bone envelopes -> `state`;
F. a control point deleted in Edit Mode -> `pool` (Delete Patch named); restored -> gone;
G. the base .sgm changed on disk -> `source`;
H. a UV cage that no longer matches the patch table -> `uvcage` (and Export raises the same words);
I. a blended vertex weight -> `skin`, found by the deep pass only (the state key cannot see a weight edit);
J. a bone the game looks up deleted on the rig -> `skeleton`, which the panel leaves to the rig box and the viewport
   text still shows;
K. cost: the per-update state key, and a 200-step Edit-Mode scrub timed with the handler registered and without it;
L. a clean re-import fires nothing and still exports byte-identical.
"""
import os
import shutil
import sys
sys.dont_write_bytecode = True
import time
import traceback

import bmesh
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import guards, ops, store, ui  # noqa: E402

SRC = REPO + "/Assets/engine_stock/cheetah.sgm"
SRC_BYTES = open(SRC, "rb").read()
COPY = "/tmp/bz_guard_source.sgm"
OUT = "/tmp/bz_guard_out.sgm"
_log = open("/tmp/bz_action_guard_log.txt", "w")


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()
    print(*a, flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    try:
        blender_native.unregister()
    except RuntimeError:
        pass
    blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"


def imp(path=SRC):
    assert bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def tick():
    """What Blender does after an edit: the depsgraph updates and the handlers run (the add-on's included)."""
    bpy.context.view_layer.update()


def codes(obj, deep=True):
    return [i.code for i in guards.issues_cached(obj, deep=deep)]


def shown(obj):
    """What the viewport text would draw for ``obj`` (the cache only, never a check)."""
    return [t for t, _c in guards.text_lines() if t.startswith(obj.name + ":")]


def panel_codes(obj):
    """The codes the SGM panel's Problems box draws (the rig box keeps `skeleton` when it is drawn)."""
    skip = ("skeleton",) if obj.get("bz_rig") else ()
    return [i.code for i in guards.issues_cached(obj) if i.code not in skip]


def export(obj, path=OUT):
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
        msg = obj.get("bz_last_export", "")
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    return state, msg, (open(path, "rb").read() if os.path.exists(path) else None)


def one(obj, code):
    """The single issue with this code (asserting there is exactly one)."""
    got = [i for i in guards.issues_cached(obj) if i.code == code]
    assert len(got) == 1, [i.code for i in guards.issues_cached(obj)]
    return got[0]


def run():
    # ---------------------------------------------------------------- A. a clean creature fires nothing
    fresh()
    obj = imp()
    tick()
    assert guards.issues(obj, deep=True) == [], guards.issues(obj)
    assert codes(obj) == [] and codes(obj, deep=False) == []
    assert guards.cached(obj.name) == [] and shown(obj) == []
    assert panel_codes(obj) == []
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, msg[:200])
    say(f"A clean cheetah ({len(obj.data.vertices)} points, {int(obj.get(store.KEY_NPATCH))} patches): 0 issues "
        f"(shallow and deep), viewport text empty, export byte-identical ({len(data)} bytes)")

    # ---------------------------------------------------------------- B. an object transform
    obj.location = (0.25, 0.0, 0.0)
    tick()                                            # only the depsgraph ran: no export, no operator
    assert [i.code for i in guards.cached(obj.name)] == ["state"], guards.cached(obj.name)
    iss = one(obj, "state")
    assert iss.text == ops.object_state_error(obj)    # the export's own words, not a second wording
    assert "object transform" in iss.text and "Alt+G" in iss.text
    assert shown(obj) == [f"{obj.name}: {iss.text}"] and panel_codes(obj) == ["state"]
    state, msg, _d = export(obj)                      # the backstop is unchanged
    assert state == "CANCELLED" and iss.text in msg, (state, msg[:300])
    obj.location = (0.0, 0.0, 0.0)
    tick()
    assert codes(obj) == [] and shown(obj) == []
    say(f"B object moved 0.25 m in Object Mode -> the depsgraph handler alone put up: {iss.text[:96]}...; Export "
        f"refuses with the same sentence; cleared -> 0 issues")

    # ---------------------------------------------------------------- C. shape keys
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.shape_key_add(from_mix=False)
    tick()
    iss = one(obj, "state")
    assert "shape keys" in iss.text and iss.text == ops.object_state_error(obj)
    assert [i.code for i in guards.cached(obj.name)] == ["state"]
    bpy.ops.object.shape_key_remove(all=True)
    tick()
    assert codes(obj) == []
    say(f"C a shape key added -> {iss.text[:80]}...; removed -> 0 issues")

    # ---------------------------------------------------------------- D. an extra modifier
    mod = obj.modifiers.new("Subdivision", "SUBSURF")
    tick()
    iss = one(obj, "state")
    assert "Subdivision" in iss.text and "modifier" in iss.text and iss.text == ops.object_state_error(obj)
    assert [i.code for i in guards.cached(obj.name)] == ["state"]
    obj.modifiers.remove(mod)
    tick()
    assert codes(obj) == []
    say(f"D a Subdivision Surface modifier -> {iss.text[:80]}...; removed -> 0 issues")

    # ---------------------------------------------------------------- E. the Armature modifier's deform mode
    arm_mod = next(m for m in obj.modifiers if m.type == "ARMATURE")
    arm_mod.use_bone_envelopes, arm_mod.use_vertex_groups = True, False
    tick()
    iss = one(obj, "state")
    assert "bone envelopes" in iss.text and iss.text == ops.object_state_error(obj)
    arm_mod.use_bone_envelopes, arm_mod.use_vertex_groups = False, True
    tick()
    assert codes(obj) == []
    say(f"E the Armature modifier switched to bone envelopes -> {iss.text[:80]}...; back -> 0 issues")

    # ---------------------------------------------------------------- F. a control point deleted
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[len(bm.verts) - 1]], context="VERTS")
    bmesh.update_edit_mesh(obj.data, destructive=True)
    obj.update_from_editmode()
    tick()
    iss = one(obj, "pool")
    assert "1 point removed" in iss.text and "Undo" in iss.text, iss.text
    assert iss.text == store.pool_error(obj)
    assert shown(obj) == [f"{obj.name}: {iss.text}"]
    state, msg, _d = export(obj)
    assert state == "CANCELLED" and iss.text in msg, (state, msg[:200])
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"F one control point deleted in Edit Mode -> {iss.text[:96]}... (the pool guard's own words; Export "
        f"CANCELLED with them)")

    # ---------------------------------------------------------------- G. the base file changed on disk
    fresh()
    shutil.copyfile(SRC, COPY)
    obj = imp(COPY)
    tick()
    assert codes(obj) == []
    with open(COPY, "ab") as f:
        f.write(b"\0" * 16)
    tick()
    iss = one(obj, "source")
    assert "changed since import" in iss.text and iss.text == store.source_error(obj), iss.text
    say(f"G the base .sgm grew by 16 bytes on disk -> {iss.text[:96]}... (no export, no operator: the state key holds "
        f"the file's stat)")
    shutil.copyfile(SRC, COPY)
    tick()
    assert codes(obj) == [], codes(obj)
    say("G  the file restored -> 0 issues")

    # ---------------------------------------------------------------- H. a stale UV cage
    cage_me = bpy.data.meshes.new("bz_cage_probe")
    cage_me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    cage_me.update()
    cage = bpy.data.objects.new("bz_cage_probe", cage_me)
    bpy.context.scene.collection.objects.link(cage)
    cage["bz_uv_for_uid"] = store.uid(obj)
    obj["bz_uv_cage"] = cage.name
    tick()
    iss = one(obj, "uvcage")
    assert "1 faces for 288 patches" in iss.text and iss.text == ops.uv_cage_error(obj), iss.text
    state, msg, _d = export(obj)
    assert state == "CANCELLED" and iss.text in msg, (state, msg[:200])
    del obj["bz_uv_cage"]
    bpy.data.objects.remove(cage, do_unlink=True)
    tick()
    assert codes(obj) == []
    say(f"H a UV cage with 1 face for 288 patches -> {iss.text[:90]}... (Export raises the same words)")

    # ---------------------------------------------------------------- I. a blended vertex weight
    arm = store.rig(obj)
    names = [g.name for g in obj.vertex_groups if arm.data.bones.get(g.name) is not None]
    a, b = names[0], names[1]
    obj.vertex_groups[a].add([0], 0.5, "REPLACE")
    obj.vertex_groups[b].add([0], 0.5, "REPLACE")
    tick()
    assert "skin" not in codes(obj, deep=False), codes(obj, deep=False)     # the key cannot see a weight edit
    assert "skin" not in codes(obj), codes(obj)          # nor the deep pass until its TTL is up (it is a clock, not a key)
    time.sleep(guards._DEEP_TTL + 0.05)
    iss = one(obj, "skin")
    assert iss.text.startswith("the rig's vertex groups skin control points the .sgm can't store")
    assert "1 with blended or partial weights (point 0:" in iss.text and "0.50" in iss.text, iss.text
    assert "skin" in [i.code for i in guards.cached(obj.name)]              # and the viewport text has it now
    state, msg, _d = export(obj)
    assert state == "CANCELLED" and "blended or partial" in msg, (state, msg[:200])
    obj.vertex_groups[b].remove([0])
    obj.vertex_groups[a].add([0], 1.0, "REPLACE")
    guards.forget(obj.name)                       # the deep pass runs on a 2 s TTL; the panel would catch up by itself
    tick()
    assert codes(obj) == [], codes(obj)
    say(f"I point 0 given 0.5/0.5 in two bone groups -> the deep pass alone sees it: {iss.text[:90]}...; put back on "
        f"one bone at full weight -> 0 issues")

    # ---------------------------------------------------------------- J. a refused skeleton edit
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    arm.data.edit_bones.remove(arm.data.edit_bones["pelvis"])
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    tick()
    iss = one(obj, "skeleton")
    assert "pelvis" in iss.text and iss.text == ui._skeleton_error_cached(arm, obj), iss.text
    assert panel_codes(obj) == [], panel_codes(obj)              # the rig box draws it, so the Problems box does not
    assert shown(obj) == [f"{obj.name}: {iss.text}"]             # the viewport says it with the sidebar closed
    # the points that were skinned to 'pelvis' are now in a group no bone deforms -- a legitimate C8 state the
    # export resolves, so the skin check must NOT call it a weight edit
    assert "skin" not in codes(obj), codes(obj)
    state, msg, _d = export(obj)
    assert state == "CANCELLED" and "pelvis" in msg, (state, msg[:200])
    say(f"J 'pelvis' deleted on the rig -> {iss.text[:96]}...; the Problems box leaves it to the rig box, the "
        f"viewport text shows it; its stranded vertex group raises no skin false alarm")

    # ---------------------------------------------------------------- K. what it costs per update
    fresh()
    obj = imp()
    tick()
    guards.state_key(obj, fresh_bones=True)
    n = 400
    t0 = time.perf_counter()
    for _ in range(n):
        guards.state_key(obj)
    key_us = (time.perf_counter() - t0) / n * 1e6
    t0 = time.perf_counter()
    for _ in range(n):
        guards.issues_cached(obj, deep=False)
    hit_us = (time.perf_counter() - t0) / n * 1e6

    def scrub(steps=200):
        """An Edit-Mode drag: one control point moved and the depsgraph updated, over and over."""
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        v = bm.verts[7]
        home = tuple(v.co)
        t = time.perf_counter()
        for k in range(steps):
            v.co = (home[0] + 1e-4 * (k % 7), home[1], home[2])
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            tick()
        secs = time.perf_counter() - t
        v.co = home
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        bpy.ops.object.mode_set(mode="OBJECT")
        return secs / steps * 1e6
    scrub(40)                                                    # warm
    with_h = min(scrub(), scrub())
    guards.unregister_handlers()
    without = min(scrub(), scrub())
    guards.register_handlers()
    assert key_us < 60.0, key_us                     # ~15 us measured: the pool is never walked
    assert with_h - without < 500.0, (with_h, without)
    say(f"K cost: state key {key_us:.0f} us/update (the bone key cached: only an updated armature re-reads it), a "
        f"cache hit {hit_us:.0f} us; an Edit-Mode scrub {with_h:.0f} us/step with the handler vs {without:.0f} "
        f"without ({with_h - without:+.0f} us = {100 * (with_h - without) / max(without, 1e-9):+.1f} %)")

    # ---------------------------------------------------------------- L. still nothing on a clean creature
    tick()
    assert guards.issues(obj, deep=True) == [] and shown(obj) == []
    state, msg, data = export(obj)
    assert state == "FINISHED" and data == SRC_BYTES, (state, msg[:200])
    say("L after the scrub the cheetah is clean again: 0 issues, export byte-identical")
    blender_native.unregister()
    return True


try:
    ok = run()
    open("/tmp/bz_action_guard_done.txt", "w").write("PASS\n" if ok else "FAIL\n")
    if not ok:
        sys.exit(1)
except Exception:
    open("/tmp/bz_action_guard_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    say("ERROR", traceback.format_exc())
    sys.exit(1)
