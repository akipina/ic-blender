"""Combine glue without Blender (combocore over combotest), on a creature built from nothing.

    .venv/bin/python3 blender_native/tests/combine_offline_test.py

Needs the game install (IC_INSTALL; the size tables and attrcombiner.lua) and the stock creatures
(Assets/engine_stock, or $BZ_STOCK_DIR). Asserts:
  1. the scaffold's in-memory export model is the written file (identity), and as a Parent (edited_parent) it
     combines exactly like combotest.combine_paths on that file: as the torso (cheetah head + tail) and as the donor
     (cheetah torso + its head + rear legs) -- patch count, size, flyer, body and limb scales, vertices, stats, drops;
  2. companions: a missing .lua gives flagged default attributes, a broken one a note; parent_for_file is one
     Parent per file version; the donor index lists what each stock creature can donate;
  3. build_ghost_arrays: Np x density^2 surface vertices, triangles, UVs, part colours, TopoCache reuse;
  4. a moved head control point (the EDITED state, not the source) moves the combined head;
  5. the Quick tier on the scaffold: 0 COMBINE ERROR, a COMBINE INFO summary (5 s budget printed; asserted only
     against a 30 s ceiling that catches a missing size-table cache, not machine load);
  6. a head ring written as 6 corner ints (hand-picked ring, gmax backlog D8) -> a COMBINE ERROR precheck line, the
     same line from the precheck-only path (Combine Checks Off), and a preview of it as the torso says it won't load;
  7. the readout: the delta column is the combined stat minus the creature alone, the creature-alone combine is
     cached per state key (and redone for a new one), a donor without a .lua is named in the notes, TopoCache is not
     reused across a density change;
  8. the donor index: peek never builds, a disk cache written by other code is rebuilt;
  9. Materialize's default path is never inside a stock donor directory;
 10. a reversed head socket ring on the cheetah torso: precheck ERRORs 11 (by-index weld) and 11a (wound out of the
     torso), a combine twist > 20 deg with no issue; combocore.ring_fix on the editor's tables restores the stock ring,
     after which there is no ring ERROR, no twist and the combine is bit-equal to the stock torso's.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time
import warnings

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # blender_native/
import core  # noqa: E402
import combocore as CC  # noqa: E402

SRC = "/tmp/bz_combo_offline_src.sgm"
NOLUA = "/tmp/bz_combo_offline_nolua.sgm"
T0 = time.perf_counter()


def say(*a):
    print(f"[{time.perf_counter() - T0:5.1f}s]", *a, flush=True)


def world(m):
    from ICParse.codecs import reconstruct as RC
    return RC.world_vertices(m)


def same_combine(pr, ref, what):
    """Same interpreter, same inputs: the preview's combine equals combine_paths bit for bit."""
    r = pr.result
    assert pr.ok, (what, pr.error)
    assert len(r.model.patches) == len(ref.model.patches), (what, len(r.model.patches), len(ref.model.patches))
    assert (r.size, r.is_flyer, r.scale) == (ref.size, ref.is_flyer, ref.scale), (what, r.size, ref.size, r.scale, ref.scale)
    assert {k: v.scale for k, v in r.limb_scales.items()} == {k: v.scale for k, v in ref.limb_scales.items()}, what
    assert {k: v.grafted for k, v in r.limb_scales.items()} == {k: v.grafted for k, v in ref.limb_scales.items()}, what
    assert np.array_equal(world(r.model), world(ref.model)), what
    assert dict(r.attributes.ui) == dict(ref.attributes.ui), what
    assert [(k, w) for k, _d, w in r.dropped] == [(k, w) for k, _d, w in ref.dropped], what
    say(f"{what}: {len(r.model.patches)} patches, size {r.size}, scale {r.scale:.6f}, grafted "
        f"{sorted(k for k, v in r.limb_scales.items() if v.grafted)}, cost {r.attributes.ui.get('cost')} "
        f"== combine_paths; preview {pr.timings['total']:.2f} s")


def main():
    assert CC.available(), CC.IMPORT_ERROR
    st = CC.status()
    say("status:", {k: st[k] for k in ("combotest", "lupa", "lua", "install", "stock_dir", "stock_count")}, st["problems"])
    assert st["install"] and st["stock_dir"], st["problems"]
    stock = st["stock_dir"]
    cheetah_path = os.path.join(stock, "cheetah.sgm")

    # ---- a creature from nothing, written as the source (New Creature does the same), with a .lua beside it
    from ICParse.sgm.creature import scaffold
    from ICParse.sgm import parser as SP
    from ICParse.lua.attributes import Attributes
    from combotest.combine import combine_paths
    model = scaffold.build_scaffold_creature("Offline", detail=1, clips=False, ik=False)
    raw = SP.write(model)
    for p in (SRC, NOLUA):
        with open(p, "wb") as fh:
            fh.write(raw)
    Attributes.default(size=3).save(SRC[:-4] + ".lua")
    for ext in (".lua", ".lsc"):
        if os.path.exists(NOLUA[:-4] + ext):
            os.remove(NOLUA[:-4] + ext)
    say(f"scaffold: {len(model.patches)} patches, {len(model.limb_connections)} rings")

    d = core.load_creature(SRC)
    em = core.export_model(SRC, d.verts)
    assert SP.write(em) == raw, "export_model(untouched) is not the source"
    props = {"bz_source": SRC, "bz_name": "Offline"}
    edited, comp = CC.edited_parent(em, props, default_size=3)
    assert not comp.is_default and comp.attributes is not None, comp
    assert CC.blueprint_stem(props) == "bz_combo_offline_src" and edited.name == "bz_combo_offline_src"

    # ---- companions and the donor pool
    c2 = CC.companions(NOLUA, default_size=4)
    assert c2.is_default and int(c2.attributes.entries["size"][1]) == 4 and "default attributes" in c2.notes[0], c2
    assert CC.companions(NOLUA).attributes is None
    broken = "/tmp/bz_combo_offline_broken"
    with open(broken + ".sgm", "wb") as fh:
        fh.write(raw)
    with open(broken + ".lua", "w") as fh:
        fh.write("limbattributes = { this is not lua")
    c3 = CC.companions(broken + ".sgm", default_size=3)
    assert c3.is_default and any("does not parse" in n for n in c3.notes), c3.notes
    cheetah = CC.parent_for_file(cheetah_path)
    assert CC.parent_for_file(cheetah_path) is cheetah, "parent_for_file must hand out one Parent per file version"
    idx = CC.DonorIndex.get(stock)
    ci, ei, si = idx.entries["cheetah"], idx.entries["eagle"], idx.entries["great_white_shark"]
    assert "wings" not in ci.parts and {"head", "tail", "front_legs", "rear_legs"} <= set(ci.parts), ci
    assert ei.flyer and "lfleg" not in ei.sockets and "wings" in ei.parts, ei
    assert si.noswap == ["lfleg", "rfleg"], si
    say(f"donor index: {len(idx.entries)} stock creatures; groups "
        f"{ {c: len(v) for c, v in idx.grouped().items()} }")

    # ---- 1. as the torso and as the donor, equal to combine_paths on the written file
    opts = CC.Options()
    pr = CC.run_preview(edited, cheetah, CC.ROLE_TORSO, ["head", "tail"], opts)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref = combine_paths(SRC, {"head": cheetah_path, "tail": cheetah_path}, name=pr.result.name)
    same_combine(pr, ref, "scaffold torso + cheetah head, tail")
    assert not pr.dropped and pr.stats and pr.stats[0][3] is not None, (pr.dropped, pr.stats[:2])
    assert [s["source"] for s in pr.slots if s["slot"] == "head"] == ["cheetah"], pr.slots
    say("readout:", pr.header(), "|", pr.summary())

    pr_d = CC.run_preview(edited, cheetah, CC.ROLE_DONOR, ["head", "rear_legs"], opts)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref_d = combine_paths(cheetah_path, {"head": SRC, "lrleg": SRC, "rrleg": SRC}, name=pr_d.result.name)
    same_combine(pr_d, ref_d, "cheetah torso + scaffold head, rear legs")
    assert [s["source"] for s in pr_d.slots if s["slot"] == "head"] == ["this"], pr_d.slots

    # the tool drops what it can't take, in words: eagle front legs onto ... the eagle has none; eagle torso + ours
    eagle = CC.parent_for_file(os.path.join(stock, "eagle.sgm"))
    pr_e = CC.run_preview(edited, eagle, CC.ROLE_DONOR, ["front_legs"], opts)
    assert pr_e.ok and pr_e.dropped_groups.get("front_legs") == CC.DROP_WORDS[CC.ct_slots.NO_SOCKET], pr_e.dropped
    say("eagle torso + scaffold front legs:", pr_e.dropped_groups)

    # ---- 3. ghost arrays
    for n in (4, 5):
        g = CC.build_ghost_arrays(pr.result, n)
        Np = len(pr.result.model.patches)
        assert g["surfV"].shape == (Np * n * n, 3) and g["surfN"].shape == g["surfV"].shape, g["surfV"].shape
        assert g["tris"].shape == (Np * 2 * (n - 1) ** 2, 3) and g["uvs"].shape == (Np * n * n, 2)
        assert g["part_colors"].shape == (Np, 3) and len(g["patch_mat"]) == Np and not g["topo_reused"]
        assert len(g["slot_tex_index"]) == len(g["slot_tint"]) == len(pr.result.model.material_index)
        assert all(t[1] is not None for t in g["textures"]), "a combined texture did not decode"
        assert set(g["patch_slot"]) >= {"torso", "head", "tail"}, set(g["patch_slot"])
        assert np.isfinite(g["surfV"]).all() and (g["bbox_max"] > g["bbox_min"]).all()
    g2 = CC.build_ghost_arrays(pr.result, 5, topo=g["topo"])
    assert g2["topo_reused"] and np.array_equal(g2["surfV"], g["surfV"])
    g4 = CC.build_ghost_arrays(pr.result, 4, topo=g["topo"])            # density 5 topology offered for density 4
    assert not g4["topo_reused"] and g4["surfV"].shape == (Np * 16, 3), "TopoCache reused across a density change"
    say(f"ghost arrays: {Np} patches x 5^2 = {len(g['surfV'])} verts, {len(g['tris'])} tris, "
        f"{len(g['textures'])} textures")

    # ---- 4. the edited state, not the source: move a head-only control point
    head = next(lm for lm in em.limbs if CC.ct_slots.slot_for_limb_id(lm.limb_id).key == "head")
    others = {v for lm in em.limbs if lm is not head for q in lm.prims for v in em.patches[q].control_points}
    rings = {v for c in em.limb_connections for v in c.vertex_indices}
    cand = sorted({v for q in head.prims for v in em.patches[q].control_points} - others - rings)
    assert cand, "no head-only control point"
    vi = cand[len(cand) // 2]
    verts2 = d.verts.copy()
    verts2[vi, 2] += 0.05
    em2 = core.export_model(SRC, verts2)
    edited2, _ = CC.edited_parent(em2, props, default_size=3)
    pr2 = CC.run_preview(edited2, cheetah, CC.ROLE_DONOR, ["head", "rear_legs"], opts)
    assert pr2.ok, pr2.error
    r1, r2 = pr_d.result.model, pr2.result.model
    w1, w2 = world(r1), world(r2)
    hd = next(lm for lm in r2.limbs if CC.ct_slots.slot_for_limb_id(lm.limb_id).key == "head")
    hv = sorted({v for q in hd.prims for v in r2.patches[q].control_points})
    moved = float(np.abs(w2[hv] - w1[hv]).max())
    assert moved > 1e-3, moved
    say(f"moved head control point {vi} by 0.05: the combined head moved up to {moved:.4f} m")

    # ---- 5. Quick tier
    t = time.perf_counter()
    rep = CC.quick_validate(edited, stock, opts)
    dt = time.perf_counter() - t
    lines = CC.format_messages(rep, notes=comp.notes)
    errs = [x for x in lines if x.startswith("COMBINE ERROR")]
    say(f"Quick: {dt:.2f} s (budget 5 s on an idle machine; load {os.getloadavg()[0]:.1f}), {len(rep.rows)} designs; "
        f"last line: {lines[-1]}")
    for x in lines:
        if not x.startswith("COMBINE INFO"):
            say("  ", x)
    assert not errs, errs
    assert lines[-1].startswith("COMBINE INFO ") and len(rep.rows) == 13, (lines[-1], len(rep.rows))
    assert CC.combine_issue_count(lines) == len([x for x in lines if x.startswith(("COMBINE ERROR", "COMBINE WARN"))])
    assert dt < 30.0, f"Quick tier took {dt:.2f} s: the size tables / attrcombiner.lua are not cached"
    # without lupa the tier says so (INFO), it does not just skip the script's checks
    from combotest import validate as ct_validate
    real_lua = ct_validate.lua_available
    ct_validate.lua_available = lambda: False
    try:
        nolupa = CC.format_messages(CC.quick_validate(edited, stock, opts))
    finally:
        ct_validate.lua_available = real_lua
    assert any(x.startswith("COMBINE INFO setup: lupa is not installed") for x in nolupa), nolupa[:3]
    assert not [x for x in nolupa if x.startswith("COMBINE ERROR")], nolupa

    # ---- 6. a head ring written as its 6 corners (not 12 edge-pair ints)
    em3 = core.export_model(SRC, d.verts)
    ring = next(c for c in em3.limb_connections if int(c.flags2) & core.CLASS_MASK == 0x40)
    ring.vertex_indices = list(dict.fromkeys(ring.vertex_indices))
    bad, _ = CC.edited_parent(em3, props, default_size=3)
    rep3 = CC.quick_validate(bad, stock, opts)
    lines3 = CC.format_messages(rep3)
    pre = [x for x in lines3 if x.startswith("COMBINE ERROR precheck:") and "ring has 6 corners / 6 ints" in x]
    say("6-int head ring:", pre[:1], f"({len([x for x in lines3 if x.startswith('COMBINE ERROR')])} COMBINE ERROR lines)")
    assert pre, lines3[:10]
    off = CC.precheck_messages(bad, opts)
    assert off[0].startswith("COMBINE INFO setup: combine checks off") and any(
        x.startswith("COMBINE ERROR precheck:") and "ring has 6 corners / 6 ints" in x for x in off), off
    good_off = CC.precheck_messages(edited, opts)
    assert not [x for x in good_off if x.startswith("COMBINE ERROR")], good_off
    pr_bad = CC.run_preview(bad, cheetah, CC.ROLE_TORSO, ["tail"], opts)        # the cut head ring stays on the torso
    assert pr_bad.ok and pr_bad.load_errors and any(w.startswith("the combined creature won't load: ")
                                                    for w in pr_bad.warnings), (pr_bad.load_errors, pr_bad.warnings)
    say("6-int head ring kept on the torso: preview warns", pr_bad.warnings[-1][:100])
    assert not pr.load_errors, pr.load_errors

    # ---- 7. the readout: delta, creature-alone cache, a donor without .lua
    from combotest.combine import combine
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        alone = combine(edited, {}, name="Alone", animals=[edited])
    base = CC._stat_table(alone)
    for k, _label, v, d in pr.stats:
        assert d is not None and abs(d - (v - base[k])) < 1e-9, (k, v, d, base.get(k))
    assert any(abs(d) > 1e-6 for _k, _l, _v, d in pr.stats), "a cheetah head + tail changes no stat"
    calls = []
    real = CC._quiet_combine

    def counting(*a, **kw):
        calls.append(kw.get("name"))
        return real(*a, **kw)
    CC._quiet_combine = counting
    try:
        p1, _ = CC.edited_parent(em, props, default_size=3)
        p2, _ = CC.edited_parent(em, props, default_size=3)          # a fresh Parent of the same state (a Refresh)
        CC.run_preview(p1, cheetah, CC.ROLE_TORSO, ["head"], opts, state_key="state-1")
        n1 = len(calls)
        CC.run_preview(p2, cheetah, CC.ROLE_TORSO, ["head"], opts, state_key="state-1")
        n2 = len(calls) - n1
        CC.run_preview(p2, cheetah, CC.ROLE_TORSO, ["head"], opts, state_key="state-2")
        n3 = len(calls) - n1 - n2
    finally:
        CC._quiet_combine = real
    assert (n1, n2, n3) == (2, 1, 2), (n1, n2, n3, calls)
    say(f"creature-alone combine: cached for one state key across fresh Parents, redone for a new key {(n1, n2, n3)}")
    nolua_parent = CC.parent_for_file(NOLUA)
    pr_n = CC.run_preview(edited, nolua_parent, CC.ROLE_TORSO, ["head"], opts)
    assert any("no .lua beside" in n and "bz_combo_offline_nolua" in n for n in pr_n.notes), pr_n.notes

    # ---- 8. the donor index: peek never builds; a cache from other code is rebuilt
    assert CC.DonorIndex.peek(stock) is idx
    empty = "/tmp/bz_combo_offline_pool"
    os.makedirs(empty, exist_ok=True)
    with open(os.path.join(empty, "offline.sgm"), "wb") as fh:
        fh.write(raw)
    CC.DonorIndex._MEMO.pop(os.path.abspath(empty), None)
    CC.DonorIndex._STAMPS.pop(os.path.abspath(empty), None)
    cache = CC.DonorIndex._cache_file(os.path.abspath(empty))
    if cache and os.path.exists(cache):
        os.remove(cache)
    assert CC.DonorIndex.peek(empty) is None, "peek built an index"
    built = CC.DonorIndex.get(empty)
    assert list(built.entries) == ["offline"]
    if cache:
        import json
        doc = json.load(open(cache))
        doc["entries"][0]["obsolete_field"] = 1                     # a DonorInfo from other code, same code key
        json.dump(doc, open(cache, "w"))
        CC.DonorIndex._MEMO.clear()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert CC.DonorIndex._load_disk(os.path.abspath(empty), built.stamp) is None
        assert any("does not match this code" in str(w.message) for w in caught), [str(w.message) for w in caught]
        doc["entries"][0].pop("obsolete_field")
        doc["code"] = "other code"
        json.dump(doc, open(cache, "w"))
        assert CC.DonorIndex._load_disk(os.path.abspath(empty), built.stamp) is None
        doc["code"] = CC.DonorIndex.code_key()
        json.dump(doc, open(cache, "w"))
        assert CC.DonorIndex._load_disk(os.path.abspath(empty), built.stamp) is not None
    say("donor index: peek does not build; stale-code caches rebuilt")

    # ---- 9. Materialize's default path is never a stock donor directory
    pool = [stock, empty]
    p_src = CC.materialize_path("a_b", "", cheetah_path, pool, "/tmp")
    assert p_src == os.path.join("/tmp", "combos", "a_b.sgm"), p_src           # a stock source: the temp dir
    assert CC.materialize_path("a_b", "/x/y/proj.blend", cheetah_path, pool, "/tmp") == "/x/y/combos/a_b.sgm"
    assert CC.materialize_path("a_b", "", SRC, pool, "/var/tmp") == "/tmp/combos/a_b.sgm"
    assert CC.in_stock_pool(os.path.join(stock, "a_b.sgm"), pool) and not CC.in_stock_pool(SRC, pool)
    p_tmp = CC.materialize_path("a_b", "", "", [os.path.join("/tmp", "combos")], "/tmp")
    assert not CC.in_stock_pool(p_tmp, [os.path.join("/tmp", "combos")]) and p_tmp.endswith("a_b.sgm"), p_tmp
    say("materialize path:", p_src)

    reversed_socket_ring(stock, cheetah)
    say(f"PASS in {time.perf_counter() - T0:.1f} s")


def reversed_socket_ring(stock, cheetah):
    """10. A hand-picked socket ring wound the wrong way, what the tier says about it, and the add-on's fix."""
    import copy
    from combotest import validate as CV
    from combotest.combine import combine
    from combotest.loader import Parent
    from combotest.slots import BY_KEY, socket_for
    from ICParse.sgm.creature.model import ring_corners, ring_pairs
    wolf = CC.parent_for_file(os.path.join(stock, "wolf.sgm"))
    partners = [CC.parent_for_file(os.path.join(stock, q + ".sgm")) for q in CV.QUICK_DONORS if q != "cheetah"]
    stock_head = ring_corners(socket_for(cheetah.model, BY_KEY["head"]).vertex_indices)

    def variant(corners):
        m = copy.deepcopy(cheetah.model)
        socket_for(m, BY_KEY["head"]).vertex_indices = ring_pairs(corners)
        return Parent.from_model(m, cheetah.attributes, cheetah.limbscale, name="cheetah", path=cheetah.path)

    picked = stock_head[::-1]
    rev = variant(picked)
    pre = [x for x in CV.precheck(rev, partners) if x.startswith("ERROR")]
    assert any(x.startswith("ERROR ring head: the by-index weld") for x in pre), pre
    assert any(x.startswith("ERROR ring head: wound with its normal out of the torso") for x in pre), pre
    alone = [x for x in CV.precheck(rev) if x.startswith("ERROR")]
    assert len(alone) == 1 and "wound with its normal out of the torso" in alone[0], alone
    row = CV.run_design(rev, wolf, "head", role="torso", run_lua=False)
    assert row["combined"] and row["issues"] == [] and row["info"]["twist"].get("head", 0) > 20, (row["issues"],
                                                                                                   row.get("info"))
    # the add-on's fix, from an editor's tables (IC-axis world points, patch table, patch limb, limb ids)
    m = rev.model
    W = np.asarray(world(m), dtype=np.float64)
    P = np.array([list(p.control_points) for p in m.patches], dtype=np.int64)
    tc = CC.torso_centre(W, P, core._patch_limb_of(m), [int(lm.limb_id) for lm in m.limbs])
    dirs, names = CC.partner_ring_dirs(stock, "head", exclude_stem="cheetah")
    fixed, notes = CC.ring_fix(W[picked], picked, "head", tc, dirs)
    assert fixed == stock_head and notes and notes[0] == "reversed to wind into the torso", (fixed, stock_head, notes)
    good = variant(fixed)
    assert not [x for x in CV.precheck(good, partners) if x.startswith("ERROR")], CV.precheck(good, partners)
    row2 = CV.run_design(good, wolf, "head", role="torso", run_lua=False)
    assert row2["combined"] and row2["issues"] == [] and row2["info"]["twist"] == {}, (row2["issues"], row2.get("info"))
    ident = variant(stock_head)

    def combined(t):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return combine(t, {"head": wolf}, name="T2", animals=[t, wolf], run_lua=False)
    r_fix, r_id = combined(good), combined(ident)
    assert len(r_fix.model.patches) == len(r_id.model.patches)
    assert np.array_equal(world(r_fix.model), world(r_id.model)), "the fixed ring does not combine like the stock one"
    say(f"reversed cheetah head ring: precheck {len(pre)} ERROR (weld + winding), twist {row['info']['twist']['head']} "
        f"deg; ring_fix ({'; '.join(notes)}) -> no ERROR, twist {{}}, combine bit-equal to stock")


if __name__ == "__main__":
    main()
