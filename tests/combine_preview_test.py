"""Combine Preview in headless Blender: New Creature -> preview with stock creatures -> stale / refresh -> swap ->
dropped parts -> Validate Combines -> Materialize -> Clear Materialized (no leaks) -> file load clears the ghost,
plus the interpreter parity of the same combines run by the repo venv's Python.

    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/combine_preview_test.py

Needs the game install (IC_INSTALL) and the stock creatures (Assets/engine_stock or $BZ_STOCK_DIR); the parity
check needs the repo venv ($BZ_VENV_PYTHON, else <repo>/.venv/bin/python3). Exits 1 on any failure.
"""
import json
import os
import subprocess
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import combocore, combopreview, core, ops, store  # noqa: E402
from ICParse import validate as V  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import reconstruct as RC  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import combine_parity_worker as PW  # noqa: E402  (record() / world(): the same measures on both sides)

SRC, EDIT = "/tmp/bz_combo_src.sgm", "/tmp/bz_combo_src_edit.sgm"
MAT = "/tmp/bz_combo_materialized.sgm"
SCRATCH = "/tmp/bz_combo_parity"
T0 = time.perf_counter()
_log = open("/tmp/bz_combo_preview_log.txt", "w")


def say(*a):
    line = f"[{time.perf_counter() - T0:5.1f}s] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    _log.write(line + "\n"); _log.flush()


def counts():
    return dict(objects=len(bpy.data.objects), armatures=len(bpy.data.armatures), meshes=len(bpy.data.meshes),
                actions=len(bpy.data.actions), images=len(bpy.data.images), collections=len(bpy.data.collections))


def preview(expect="FINISHED"):
    t = time.perf_counter()
    res = bpy.ops.bz.combine_preview()
    dt = time.perf_counter() - t
    assert expect in res, res
    pr = combopreview._ghost["preview"]
    return pr, dt


def close(a, b, rel=1e-6):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def parity(prs, runs, stock):
    """The venv's Python runs the same combines on the exported edited creature: same shape, scales, drops, stats
    within tolerance (no byte hashes across interpreters)."""
    venv = os.environ.get("BZ_VENV_PYTHON") or os.path.join(REPO, ".venv", "bin", "python3")
    assert os.path.exists(venv), f"repo venv python not found at {venv} (set BZ_VENV_PYTHON)"
    job = dict(source=SRC, edited=EDIT, stock=stock, default_size=3, runs=runs)
    with open(SCRATCH + "_job.json", "w") as fh:
        json.dump(job, fh)
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}      # the snap's libs are not the venv's
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    t = time.perf_counter()
    r = subprocess.run([venv, os.path.join(HERE, "combine_parity_worker.py"), SCRATCH + "_job.json", SCRATCH],
                       capture_output=True, text=True, timeout=180, env=env, cwd="/tmp")
    assert r.returncode == 0 and "PARITY_WORKER_OK" in r.stdout, (r.stdout + r.stderr)[-3000:]
    theirs = json.load(open(SCRATCH + ".json"))
    npz = np.load(SCRATCH + ".npz")
    for i, (pr, ref) in enumerate(zip(prs, theirs)):
        mine = PW.record(pr)
        assert ref["ok"], ref
        for k in ("size", "flyer", "patches", "dropped", "grafted", "lua"):
            assert mine[k] == ref[k], (i, k, mine[k], ref[k])
        assert close(mine["scale"], ref["scale"]), (i, mine["scale"], ref["scale"])
        assert mine["limb_scales"].keys() == ref["limb_scales"].keys()
        for k in mine["limb_scales"]:
            assert close(mine["limb_scales"][k], ref["limb_scales"][k]), (i, k, mine["limb_scales"][k], ref["limb_scales"][k])
        wa, wb = PW.world(pr), npz[f"run{i}"]
        assert wa.shape == wb.shape and float(np.abs(wa - wb).max()) < 1e-5, (i, wa.shape, wb.shape)
        assert list(mine["entries"]) == list(ref["entries"]), i
        nfloat = 0
        for k, (g, v) in mine["entries"].items():
            g2, v2 = ref["entries"][k]
            assert g == g2, (k, g, g2)
            if isinstance(v, int) and isinstance(v2, int):
                assert v == v2, (k, v, v2)
            elif isinstance(v, (int, float)) and isinstance(v2, (int, float)):
                assert close(float(v), float(v2)), (k, v, v2)
                nfloat += 1
            else:
                assert v == v2, (k, v, v2)
        say(f"parity run {i} ({runs[i]['role']} {runs[i]['groups']} {runs[i]['other']}): size {mine['size']}, "
            f"{mine['patches']} patches, scale {mine['scale']:.7f} vs {ref['scale']:.7f}, world max "
            f"{float(np.abs(wa - wb).max()):.2e} m, {len(mine['entries'])} entries ({nfloat} float) -- "
            f"Blender py{mine['python']} numpy {mine['numpy']} vs venv py{ref['python']} numpy {ref['numpy']}")
    say(f"parity worker: {time.perf_counter() - t:.1f} s")


def run():
    blender_native.register()
    bpy.ops.wm.read_homefile(use_empty=True)
    scene = bpy.context.scene
    s = scene.bz_combine
    st = combocore.status(paths_status=blender_native.PATHS_STATUS)
    say("status:", {k: st[k] for k in ("combotest", "lupa", "lua", "install", "stock_dir", "stock_count")}, st["problems"])
    assert st["combotest"] and st["install"] and st["stock_dir"], st["problems"]
    stock = st["stock_dir"]
    for f in (SRC, EDIT, MAT, MAT[:-4] + ".lua", MAT[:-4] + ".lsc"):
        if os.path.exists(f):
            os.remove(f)

    res = bpy.ops.bz.new_creature(filepath=SRC, creature_name="Combo", detail=1)
    assert "FINISHED" in res, res
    obj = bpy.context.view_layer.objects.active
    assert store.is_native(obj)

    # ---- the part buttons: what the donor can give
    s.other_kind, s.other_stock_name, s.role = "STOCK", "cheetah", combocore.ROLE_TORSO
    for g in combocore.PARTS:
        setattr(s, f"part_{g}", g in ("head", "tail"))
    # draw-time callbacks never index (seconds): they schedule it; the operators / this test build it
    assert combopreview.donor_index(s, build=True) is not None
    assert s.other_stock == "cheetah", s.other_stock
    s.other_stock_name = "no_such_creature"
    assert s.other_stock == combopreview.MISSING_ITEM, s.other_stock        # shown as missing, never as another one
    s.other_stock_name = "cheetah"
    avail = combopreview.part_availability(scene, obj, s)
    assert avail["wings"] is False and avail["front_arms"] is False and avail["head"] and avail["tail"], avail
    say("cheetah as donor, parts available:", [g for g, v in avail.items() if v])

    # ---- 1. preview: this creature as the torso, cheetah head + tail
    prA, t_first = preview()
    assert prA.ok and not prA.dropped, (prA.error, prA.dropped)
    errs = [p for p in V.check_model(prA.result.model) if not p.startswith("WARN")]
    assert not errs, errs
    a = combopreview._ghost["arrays"]
    Np = len(prA.result.model.patches)
    n = int(scene.bz_density)
    assert a is not None and a["surfV"].shape == (Np * n * n, 3) and len(a["tris"]) == Np * 2 * (n - 1) ** 2
    assert not combopreview.is_stale(scene, obj, s)
    say(f"preview: {prA.header()} | {prA.summary()} | first {t_first:.2f} s {prA.timings}")

    # ---- 2. an edit makes it stale; Refresh shows the edit (a torso-only control point: the torso is kept)
    m = core.read_source(SRC)
    torso = next(lm for lm in m.limbs if int(lm.limb_id) & core.CLASS_MASK == 0x800)
    others = {v for lm in m.limbs if lm is not torso for q in lm.prims for v in m.patches[q].control_points}
    rings = {v for c in m.limb_connections for v in c.vertex_indices}
    cand = sorted({v for q in torso.prims for v in m.patches[q].control_points} - others - rings)
    vi = cand[len(cand) // 2]
    wA = RC.world_vertices(prA.result.model)
    obj.data.vertices[vi].co.z += 0.05
    obj.data.update()
    assert combopreview.is_stale(scene, obj, s), "an edit did not mark the preview stale"
    prB, t_warm = preview()
    assert prB.ok and not combopreview.is_stale(scene, obj, s)
    assert combopreview._ghost["arrays"]["topo_reused"], "same topology: the TopoCache should be reused"
    wB = RC.world_vertices(prB.result.model)
    tv = sorted({v for lm in prB.result.model.limbs if int(lm.limb_id) & core.CLASS_MASK == 0x800
                 for q in lm.prims for v in prB.result.model.patches[q].control_points})
    moved = float(np.abs(wB[tv] - wA[tv]).max())
    assert moved > 1e-3, moved
    say(f"refresh after moving control point {vi}: stale -> current, torso moved up to {moved:.4f} m; warm {t_warm:.2f} s "
        f"{prB.timings}")
    # auto refresh (timers never fire in --background, so the tick is called by hand): an edit arms it through the
    # depsgraph handler, and the tick refreshes the stale preview once the edits are quiet
    s.auto_refresh = True
    combopreview._AUTO["pending"] = False
    obj.data.vertices[vi].co.z -= 0.05
    obj.data.update()
    bpy.context.view_layer.update()
    armed = combopreview._AUTO["pending"]
    assert armed, "the depsgraph handler did not arm the auto refresh"
    assert combopreview.is_stale(scene, obj, s)
    combopreview._AUTO["last"] = time.monotonic() - 2 * combopreview.AUTO_DELAY
    assert combopreview._auto_tick() is None and not combopreview.is_stale(scene, obj, s), "auto refresh did not refresh"
    assert combopreview._ghost["preview"] is not prB
    say(f"auto refresh: armed by the depsgraph handler {armed}; the tick refreshed the stale preview")
    s.auto_refresh = False
    obj.data.vertices[vi].co.z += 0.05             # back to the state prB combined
    obj.data.update()
    prB, _ = preview()
    # every combine input marks it stale: a part button, the other stock creature; a rename keeps the preview
    s.part_rear_legs = True
    assert combopreview.is_stale(scene, obj, s), "toggling a part did not mark the preview stale"
    s.part_rear_legs = False
    assert not combopreview.is_stale(scene, obj, s)
    s.other_stock_name = "horse"
    assert combopreview.is_stale(scene, obj, s), "another stock creature did not mark the preview stale"
    s.other_stock_name = "cheetah"
    img = bpy.data.images.get(next(n for n in store.texture_images(obj) if n))
    px0 = img.pixels[0]
    img.pixels[0] = 1.0 - px0                      # a paint stroke: the export writes painted skins
    assert combopreview.is_stale(scene, obj, s), "painting the skin did not mark the preview stale"
    img.pixels[0] = px0
    prB, _ = preview()
    # a Ctrl+G reassignment is exported (adopted from the vertex groups): it marks the preview stale too
    g0 = next(g.group for g in obj.data.vertices[0].groups)
    g1 = next(vg.index for vg in obj.vertex_groups if vg.index != g0)
    obj.vertex_groups[g0].remove([0]); obj.vertex_groups[g1].add([0], 1.0, "REPLACE")
    assert combopreview.is_stale(scene, obj, s), "a vertex-group reassignment did not mark the preview stale"
    obj.vertex_groups[g1].remove([0]); obj.vertex_groups[g0].add([0], 1.0, "REPLACE")
    assert not combopreview.is_stale(scene, obj, s)
    # a broken pool: the digest changes (an error, not the old preview) and Materialize is disabled
    assert bpy.ops.bz.combine_materialize.poll()
    gens = store.point_ints(obj, store.ATTR_GEN)
    store.set_point_ints(obj, store.ATTR_GEN, np.full(len(gens), 4242))
    store.forget_cache()
    assert combopreview.is_stale(scene, obj, s) and not bpy.ops.bz.combine_materialize.poll(), \
        "a broken pool left the preview current / Materialize enabled"
    store.set_point_ints(obj, store.ATTR_GEN, gens)
    store.forget_cache()
    assert not combopreview.is_stale(scene, obj, s) and bpy.ops.bz.combine_materialize.poll()
    old_name = obj.name
    obj.name = old_name + "_renamed"
    assert combopreview.current_result(obj) is prB and combopreview.ghost_object() == obj, "a rename lost the preview"
    obj.name = old_name
    t = time.perf_counter()
    for _ in range(10):
        combopreview.is_stale(scene, obj, s)
        combopreview.part_availability(scene, obj, s)
    say(f"stale on part toggle, stock change and a painted skin; rename keeps the preview; panel helpers "
        f"{(time.perf_counter() - t) * 100:.2f} ms per draw")
    # the exported edited creature, for the venv parity run (the same state as the in-memory export model)
    data = ops.export_bytes(obj, bpy.context, animations=False)
    with open(EDIT, "wb") as fh:
        fh.write(data)

    # ---- 3. swap: this creature donates head + rear legs to the cheetah
    s.part_tail, s.part_rear_legs = False, True
    res = bpy.ops.bz.combine_swap()
    assert "FINISHED" in res and s.role == combocore.ROLE_DONOR, (res, s.role)
    prC = combopreview._ghost["preview"]
    assert prC.ok and prC.role == combocore.ROLE_DONOR and not combopreview.is_stale(scene, obj, s), prC.error
    assert sorted(k for k, v in prC.result.limb_scales.items() if v.grafted) == ["head", "lrleg", "rrleg"]
    say(f"swap: {prC.header()} | {prC.summary()}")

    # ---- 4. parity with the repo venv on both combines
    parity([prB, prC], [dict(other="cheetah", role=combocore.ROLE_TORSO, groups=["head", "tail"]),
                        dict(other="cheetah", role=combocore.ROLE_DONOR, groups=["head", "rear_legs"])], stock)

    # ---- 5. a part the torso has no socket for: eagle torso + this creature's front legs
    s.other_stock_name = "eagle"
    for g in combocore.PARTS:
        setattr(s, f"part_{g}", g == "front_legs")
    prD, _ = preview()
    assert prD.ok and prD.dropped_groups.get("front_legs") == combocore.DROP_WORDS[combocore.ct_slots.NO_SOCKET], \
        prD.dropped_groups
    say("eagle torso + front legs:", prD.dropped_groups)

    # ---- 6. Validate Combines (the Quick tier) on the edited creature
    t = time.perf_counter()
    res = bpy.ops.bz.combine_validate()
    t_quick = time.perf_counter() - t
    assert "FINISHED" in res, res
    val = list(obj["bz_validation"])
    cerr = [x for x in val if x.startswith("COMBINE ERROR")]
    assert not cerr, cerr
    assert any(x.startswith("COMBINE INFO ") and " designs (" in x for x in val), val
    summary = json.loads(obj["bz_combine_summary"])
    assert summary["designs"] == 13 and summary["errors"] == 0, summary
    say(f"Validate Combines: {t_quick:.2f} s (budget 5 s); {val[-1]}")

    # ---- 7. Materialize and Clear Materialized leave nothing behind
    s.other_stock_name, s.role = "cheetah", combocore.ROLE_TORSO
    for g in combocore.PARTS:
        setattr(s, f"part_{g}", g in ("head", "tail"))
    prE, _ = preview()
    default = combopreview._default_materialize_path(obj, prE, s)
    pool = combopreview._stock_pool_dirs(s)
    assert not combocore.in_stock_pool(default, pool), default
    refused = os.path.join(stock, "bz_combo_must_not_exist.sgm")
    try:
        res = bpy.ops.bz.combine_materialize(filepath=refused)
    except RuntimeError as e:                      # the ERROR report
        res = {"CANCELLED", str(e).splitlines()[0]}
    assert "CANCELLED" in res and not os.path.exists(refused), res
    say(f"Materialize: default {default}; refused inside the stock pool")
    base = counts()
    res = bpy.ops.bz.combine_materialize(filepath=MAT)
    assert "FINISHED" in res, res
    new = bpy.context.view_layer.objects.active
    coll = bpy.data.collections.get(combopreview.COLLECTION)
    assert store.is_native(new) and new.get("bz_rig") and coll is not None and new.name in coll.objects, new
    arm = bpy.data.objects.get(new["bz_rig"])
    assert arm is not None and arm.name in coll.objects
    combo = json.loads(new["bz_combo"])
    assert combo["torso"] == "bz_combo_src" and combo["other"] == "cheetah" and combo["parts"] == ["head", "tail"], combo
    assert combo["this"] == "bz_combo_src" and combo["donor"] == "cheetah" and combo["load_errors"] == [], combo
    mm = SP.read(MAT)
    errs = [p for p in V.check_model(mm) if not p.startswith("WARN")]
    assert not errs and len(mm.patches) == len(prE.result.model.patches), errs
    assert os.path.exists(MAT[:-4] + ".lua") and os.path.exists(MAT[:-4] + ".lsc")
    say(f"materialized {MAT}: {len(mm.patches)} patches, check_model clean; created "
        f"{ {k: v - base[k] for k, v in counts().items()} }")
    # an Action of the materialized rig goes with it; a user's object linked into the collection too stays
    act = bpy.data.actions.new("bz_combo_test_action")
    act["bz_arm"] = arm.name
    keep_me = bpy.data.objects.new("bz_user_object", None)
    bpy.context.scene.collection.objects.link(keep_me)
    coll.objects.link(keep_me)
    base_with_user = dict(base, objects=base["objects"] + 1)
    bpy.context.view_layer.objects.active = obj
    res = bpy.ops.bz.combine_clear_materialized()
    assert "FINISHED" in res, res
    after = counts()
    assert after == base_with_user, (base_with_user, after)
    assert bpy.data.objects.get("bz_user_object") is keep_me and not bpy.data.actions.get("bz_combo_test_action")
    assert bpy.data.collections.get(combopreview.COLLECTION) is None
    bpy.data.objects.remove(keep_me)
    say("cleared materialized: datablock counts back to", counts(), "(a user object linked there kept)")

    # ---- 8. a file load clears the ghost
    assert combopreview._ghost
    bpy.ops.wm.read_homefile(use_empty=True)
    assert not combopreview._ghost, "load_post did not clear the combine preview"
    say(f"load_post cleared the ghost; timings: preview first {t_first:.2f} s, warm {t_warm:.2f} s, Quick {t_quick:.2f} s")
    blender_native.unregister()
    say(f"PASS in {time.perf_counter() - T0:.1f} s")


try:
    run()
    open("/tmp/bz_combo_preview_done.txt", "w").write("PASS\n")
except Exception:
    open("/tmp/bz_combo_preview_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    say("ERROR", traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
