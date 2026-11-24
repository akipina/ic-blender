"""Fit to rig: a freshly fitted creature is riggable at once, and a bone C8 added can root a limb.
    blender --background --python blender_native/tests/fit_to_rig_test.py

The four things the native pillbug build (3DInputs/Pillbug_ic) had to work around, each with the symptom it had:

A. **A fitted creature could not be rigged** (gap 2). ``core.patches_to_creature`` put every patch in limb 0 and every
   point on bone 0, so every OTHER limb of the template had no patches and its bones owned no points -- the file's own
   definition of a stub bone -- and 43 of the scaffold's 48 bones counted as stubs: every C8 operator refused them
   ("'head' is a stub bone (a stub limb's only bone, written after every other bone)"). The fit now hands back the
   TEMPLATE's body plan (``fitops.rig_split``), and the test asserts both halves: with ``split_limbs=False`` the 43
   stubs and the refusal are still there; with the split there are exactly the 6 flagged socket bones, and Add Bone,
   a bone delete and Set Root Bone all work with no save and no re-import.
B. **The live stub set** (gap 2, the other half): giving a limb patches in Blender took its bones out of the stub set
   only after a save and a re-import (the pillbug build's "stub bones 43 -> 4 ... so C8 can now author bones").
   ``store.export_stubs`` reads the live tables now: the test empties a limb and fills it again and watches the set.
C. **A C8-added bone could not root a limb** (gap 3): Add Standard Limb refused a bone the template never had ("the
   skeleton has no bone 'mid_leg1_l' ... (bone authoring is a later increment)"), and Set Root Bone / Add Active Bone /
   Remove Active Bone read the SOURCE skeleton, so an added bone was "not a bone of this creature's skeleton". All four
   read the live skeleton now, and an added bone a limb operator placed is PINNED there (an added bone otherwise follows
   its parent, which can never give a new limb its first bone). The export and the re-imported file carry it.
D. **Export to the creature's own base file** (gap 9): Fit writes its output as the creature's source, so exporting to
   that same path silently overwrote the file the object is built on and every later export refused with "the source
   file changed since import". It is now an in-place save that re-bases the object -- proven by exporting again
   elsewhere and getting the same bytes -- and is refused up front, by name, while a skeleton edit is pending.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, fitops, limbops, rig, store, structure  # noqa: E402
from ICParse import validate as V  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

TPL = "/tmp/bz_fitrig_template.sgm"
NOSPLIT = "/tmp/bz_fitrig_nosplit.sgm"
OUT = "/tmp/bz_fitrig.sgm"                      # the fitted + rigged creature (run_all --engine loads it)
REEXPORT = "/tmp/bz_fitrig_out.sgm"
INPLACE = "/tmp/bz_fitrig_inplace.sgm"
_log = open("/tmp/bz_fit_to_rig_log.txt", "w")
T0 = time.perf_counter()


def say(*a):
    line = f"[{time.perf_counter() - T0:5.1f}s] " + " ".join(str(x) for x in a)
    _log.write(line + "\n"); _log.flush()
    print(line, flush=True)


def activate(obj):
    bpy.context.view_layer.update()                 # the view layer lists a removed object until updated
    for o in list(bpy.context.view_layer.objects):
        if o is not None:
            o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def refused(fn, words, **kw) -> str:
    """Run an operator expecting an ERROR report (which raises in background mode); every word must be in it."""
    try:
        res = fn(**kw)
    except RuntimeError as e:
        msg = str(e)
    else:
        raise AssertionError(f"not refused ({res}): {words}")
    for w in words:
        assert w in msg, (w, msg)
    return msg


def ok(fn, **kw):
    res = fn(**kw)
    assert "FINISHED" in res, (fn, res)
    return res


def quad_sphere(name, size=0.30):
    """A closed all-quad sphere (a subdivided cube pushed out), at creature size."""
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    mod = obj.modifiers.new("sub", "SUBSURF"); mod.levels = 3
    bpy.ops.object.modifier_apply(modifier="sub")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.transform.tosphere(value=1.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    assert len(obj.data.polygons) == 384 and all(len(p.vertices) == 4 for p in obj.data.polygons)
    return obj


def stub_names(path):
    m = SP.read(path)
    return sorted(str(m.bones[i].name) for i in structure.stub_bone_indices(m))


def flagged_names(path):
    m = SP.read(path)
    return sorted(str(b.name) for b in m.bones if (int(b.meta0) & 0xFF) == structure.STUB_BYTE)


def limb_of(path, bone):
    """The limb records of ``path`` that list ``bone`` (ignoring case)."""
    m = SP.read(path)
    return [str(l.name) for l in m.limbs if str(bone).lower() in {str(b).lower() for b in l.bone_names}]


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


def run():
    blender_native.register()
    bpy.ops.wm.read_homefile(use_empty=True)
    for f in (TPL, NOSPLIT, OUT, REEXPORT, INPLACE):
        if os.path.exists(f):
            os.remove(f)
    from ICParse.sgm.creature import scaffold
    model = scaffold.build_scaffold_creature("FitRig", detail=1, clips=True, ik=True)
    with open(TPL, "wb") as f:
        f.write(SP.write(model))
    n_bones, n_limbs = len(model.bones), len(model.limbs)
    flagged = flagged_names(TPL)
    say(f"template {TPL}: {n_bones} bones, {n_limbs} limbs, {len(model.patches)} patches; "
        f"{len(flagged)} flagged stub bones {flagged}")
    assert len(flagged) == 6, flagged                     # the wing / arm sockets, the only true stub bones

    # ---------------------------------------------------------------- A. the fit's limb and skin split ----------
    target = quad_sphere("Ball")
    activate(target)
    ok(bpy.ops.bz.fit_mesh, layout="OWN_QUADS", template_path=TPL, output_path=NOSPLIT, split_limbs=False,
       heat_map=False)
    flat = bpy.context.view_layer.objects.active
    before = stub_names(NOSPLIT)
    m0 = SP.read(NOSPLIT)
    assert [l.name for l in m0.limbs if l.prims] == [str(m0.limbs[0].name)], "no-split: not everything in limb 0"
    assert sum(1 for b in m0.bones if b.vertex_count) == 1, "no-split: more than one bone owns points"
    assert len(before) == 43, (len(before), before)
    say(f"A (before) split off: every patch in '{m0.limbs[0].name}', every point on '{m0.bones[0].name}' -> "
        f"{len(before)} of {n_bones} bones are stub bones")
    bpy.context.scene.bz_boner.bone = "head"
    say("A (before) Add Bone under 'head':",
        refused(bpy.ops.bz.add_bone, ["'head' is a stub bone"], name="horn_l", parent="head"))
    refused(bpy.ops.bz.add_standard_limb, ["the skeleton has no bone 'mid_leg1_l'", "Add Bone"], slot="LMLeg")
    for o in (flat, store.rig(flat)):
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)

    activate(target)
    t = time.perf_counter()
    ok(bpy.ops.bz.fit_mesh, layout="OWN_QUADS", template_path=TPL, output_path=OUT, heat_map=False)
    cre = bpy.context.view_layer.objects.active
    arm = store.rig(cre)
    after = stub_names(OUT)
    assert after == flagged, (after, flagged)              # ONLY the flagged socket bones are stub bones now
    m1 = SP.read(OUT)
    with_patches = [(str(l.name), len(l.prims)) for l in m1.limbs if l.prims]
    owning = sum(1 for b in m1.bones if b.vertex_count)
    assert len(with_patches) == n_limbs - 6, with_patches  # every limb but the six stub sockets got geometry
    assert sum(n for _n, n in with_patches) == len(m1.patches) == 384
    assert owning > 1, owning
    assert sorted(store.export_stubs(cre)) == sorted(i for i, b in enumerate(m1.bones)
                                                     if (int(b.meta0) & 0xFF) == structure.STUB_BYTE)
    line = str(cre["bz_last_fit"])
    assert "; split into " in line, line
    say(f"A (after) split on ({time.perf_counter() - t:.1f}s): {len(after)} stub bones {after}; {owning} bones own "
        f"points; limbs {with_patches}")

    # ... and C8 works straight away, with no save and no re-import
    bpy.context.scene.bz_boner.bone = "head"
    ok(bpy.ops.bz.add_bone, name="horn_l", parent="head")
    assert arm.data.bones.get("horn_l") is not None
    bpy.ops.ed.undo_push(message="horn")
    edit_bones(arm, lambda eb: eb.remove(eb["ear_l"]))      # a source bone deleted (C8.1), no alert
    assert arm.data.bones.get("ear_l") is None
    roots = [i[0] for i in limbops._root_items(None, bpy.context)]      # the live skeleton, minus the deleted bone
    assert "tail1" in roots and "horn_l" in roots and "ear_l" not in roots, roots[:6]
    lf = _limb_index(cre, "LFLeg")
    ok(bpy.ops.bz.limb_set_root, index=lf, root="front_leg1_l")        # front_leg_root_l goes back to the Torso
    assert "front_leg_root_l" not in [b.lower() for b in limbops.limb_table(cre).bones[lf]]
    ok(bpy.ops.bz.export_sgm, filepath=REEXPORT)
    m2 = SP.read(REEXPORT)
    assert "horn_l" in {str(b.name) for b in m2.bones} and "ear_l" not in {str(b.name).lower() for b in m2.bones}
    assert not [x for x in V.check_model(m2) if not x.startswith("WARN")], V.check_model(m2)
    say(f"A (after) rigged with no save: Add Bone horn_l under head, ear_l deleted with X, LFLeg re-rooted at "
        f"front_leg1_l -> {len(m2.bones)} bones, check_model clean")

    # ---------------------------------------------------------------- B. the stub set follows the live tables ---
    tail = _limb_index(cre, "Tail")
    pl = store.patch_limb(cre).copy()
    torso = _limb_index(cre, "Torso")
    tail_bones = {str(b).lower() for b in limbops.limb_table(cre).bones[tail]}
    tail_idx = {i for i, b in enumerate(SP.read(OUT).bones) if str(b.name).lower() in tail_bones}
    assert not (tail_idx & store.export_stubs(cre)), "a Tail bone is a stub bone while the Tail has patches"
    store.begin_edit(cre)
    store.set_patch_limb(cre, np.where(pl == tail, torso, pl))      # the Tail loses its patches, live
    live = store.export_stubs(cre)
    gained = sorted(str(SP.read(OUT).bones[i].name) for i in (live - set(store.source_stubs(cre))))
    assert gained, "emptying a limb made no bone a stub bone"
    store.begin_edit(cre)
    store.set_patch_limb(cre, pl)                                   # ... and gets them back
    assert not (tail_idx & store.export_stubs(cre)), "the bones stayed stubs after the limb got its patches back"
    say(f"B live stub set: emptying the Tail limb makes {len(gained)} of its bones stub bones ({', '.join(gained[:4])}"
        f"{', ...' if len(gained) > 4 else ''}) and filling it again takes them out -- with no save or re-import")

    # ---------------------------------------------------------------- C. an added bone roots a standard limb ----
    bpy.context.scene.bz_boner.bone = "spine1"
    ok(bpy.ops.bz.add_bone, name="mid_leg1_l", parent="spine1")
    ok(bpy.ops.bz.add_bone, name="mid_leg2_l", parent="mid_leg1_l")
    sk = limbops.live_skeleton(cre)
    assert "mid_leg1_l" in sk.names and "mid_leg1_l" in sk.added, sk.added
    ok(bpy.ops.bz.add_standard_limb, slot="LMLeg")
    lm = _limb_index(cre, "LMLeg")
    t3 = limbops.limb_table(cre, placed=True)
    assert [b.lower() for b in t3.bones[lm]] == ["mid_leg1_l", "mid_leg2_l"], t3.bones[lm]   # the child follows it
    assert "mid_leg1_l" in [p.lower() for p in store.limb_pins(cre)], store.limb_pins(cre)
    # Add / Remove Active Bone reach an added bone too (they read the live skeleton now)
    assert [b.lower() for b in limbops.limb_table(cre).bones[lm]] == ["mid_leg1_l"]   # the STORED row: the root only
    ok(bpy.ops.bz.limb_add_bone, index=lm, bone="mid_leg2_l")
    assert [b.lower() for b in limbops.limb_table(cre).bones[lm]] == ["mid_leg1_l", "mid_leg2_l"]
    assert sorted(p.lower() for p in store.limb_pins(cre)) == ["mid_leg1_l", "mid_leg2_l"], store.limb_pins(cre)
    ok(bpy.ops.bz.limb_remove_bone, index=lm, bone="mid_leg2_l")     # the pin goes with it ...
    assert [b.lower() for b in limbops.limb_table(cre).bones[lm]] == ["mid_leg1_l"]
    assert [p.lower() for p in store.limb_pins(cre)] == ["mid_leg1_l"], store.limb_pins(cre)
    # ... and the export puts it back in its parent's limb, which is this one (the warning says so)
    assert [b.lower() for b in limbops.limb_table(cre, placed=True).bones[lm]] == ["mid_leg1_l", "mid_leg2_l"]
    ok(bpy.ops.bz.limb_add_bone, index=lm, bone="mid_leg2_l")
    ok(bpy.ops.bz.export_sgm, filepath=REEXPORT)
    assert [n for n in limb_of(REEXPORT, "mid_leg1_l")] == [SP.read(REEXPORT).limbs[lm].name], \
        limb_of(REEXPORT, "mid_leg1_l")
    assert limb_of(REEXPORT, "mid_leg2_l") == limb_of(REEXPORT, "mid_leg1_l"), limb_of(REEXPORT, "mid_leg2_l")
    mm = SP.read(REEXPORT)
    assert not [x for x in V.check_model(mm) if not x.startswith("WARN")], V.check_model(mm)
    say(f"C added bone roots a limb: mid_leg1_l + mid_leg2_l added under spine1, Add Standard Limb LMLeg -> "
        f"'{mm.limbs[lm].name}' lists them (pinned, not pulled back into the Torso); Remove / Add Active Bone work; "
        f"export {len(mm.bones)} bones, check_model clean")

    # ---------------------------------------------------------------- D. export in place ------------------------
    # with the skeleton edits pending it is refused, by name
    refused(bpy.ops.bz.export_sgm, ["is this creature's own base file", "the bones added on the rig",
                                    "Export under another name"], filepath=OUT)
    # a creature with no skeleton edit pending: the fit's own output IS its base, and saving over it works
    activate(target)
    ok(bpy.ops.bz.fit_mesh, layout="OWN_QUADS", template_path=TPL, output_path=INPLACE, heat_map=False)
    plain = bpy.context.view_layer.objects.active
    assert store.source_path(plain) == INPLACE
    base = open(INPLACE, "rb").read()
    v = np.empty(len(plain.data.vertices) * 3); plain.data.vertices.foreach_get("co", v)
    v = v.reshape(-1, 3); v[0] += (0.01, 0.0, 0.0)
    plain.data.vertices.foreach_set("co", v.ravel()); plain.data.update()
    ok(bpy.ops.bz.export_sgm, filepath=INPLACE)
    saved = open(INPLACE, "rb").read()
    assert saved != base, "the in-place export wrote the same bytes"
    assert str(plain["bz_last_export"]).endswith("updated in place"), plain["bz_last_export"]
    assert store.source_error(plain) is None, store.source_error(plain)     # the object is in sync with the new base
    ok(bpy.ops.bz.export_sgm, filepath=REEXPORT)                            # and exporting again gives the same file
    assert open(REEXPORT, "rb").read() == saved, "the re-export after an in-place save differs"
    say(f"D in place: {len(base)} -> {len(saved)} bytes over the creature's own base, the object re-based (the next "
        f"export is byte-identical); refused by name while a skeleton edit is pending")
    blender_native.unregister()


def _limb_index(obj, suffix):
    names = [str(n).lower() for n in store.limb_names(obj)]
    return next(i for i, n in enumerate(names) if n.endswith("_" + suffix.lower()) or n == suffix.lower())


try:
    run()
    open("/tmp/bz_fit_to_rig_done.txt", "w").write("PASS\n")
    say("PASS")
except Exception:
    open("/tmp/bz_fit_to_rig_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
