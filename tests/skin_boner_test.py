"""Boner's skinning loop in headless Blender (increment B5): the Bones sub-mode, By Bone, Pick Bone, Assign / Select /
Remove / Select Unassigned / Auto Skin, the unassigned-point export refusal and the stock skinning layout.
    blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/skin_boner_test.py

A. registration: scene.bz_boner (sub-mode tabs follow the overlay), the Boner panel's three tabs draw headless;
B. New Creature: an untouched export and one after a no-op Assign Selected are byte-identical; Select Points selects the
   active bone's points; Remove from Bone gives 5 patch points no bone (owner -1, vertex groups gone, By Bone orange):
   Export and Validate refuse with the count and the Select Unassigned hint; Undo restores them (byte-identical),
   Redo refuses again; Pick Bone (execute and background invoke refuse; from the sidebar a click on a control point
   picks its bone, on a bone segment that bone, an unassigned point is refused, Esc cancels); Select Unassigned
   selects exactly the 5; Auto Skin assigns them: Export OK (/tmp/bz_boner_b5_autoskin.sgm), every patch point has a
   bone, world kept, groups in sync, every bone follows the stock layout (the scaffold's words re-derived), no blocking
   Validate line;
C. snail (stock): By Bone point colours count each bone's source range; a native Delete disables every skinning
   operator until Undo; Assign through the active bone exports the same
   bytes as Assign Selected to Bone (/tmp/bz_boner_b5_skin.sgm) and the export follows the stock layout on every bone;
D. woolly_mammoth: its 2 unused unowned points are Select Unassigned's 2, allowed by Export (byte-identical); a reskin
   keeps them first with their bytes and follows the layout (/tmp/bz_boner_b5_woolly.sgm); Remove of a patch point is
   refused; Auto Skin (nothing selected) skins every unassigned point and exports;
E. an open snail + Extrude Patch + Remove + Auto Skin of the new points: the grown export follows the layout
   (/tmp/bz_boner_b5_extrude.sgm, open sides: the Object Editor rejects it like /tmp/bz_ex1.sgm);
F. the stock layout pinned on both corpora (61/61 base-game; add-on stock off only for the 3 template-made ChickenTests);
G. cheetah Auto Skin guards: just imported (every point selected), Object Mode Auto skins nothing and Selected is refused;
   Edit Mode with every point selected is refused (Reset All named); Reset All equals the pure rule of boner_test, gives
   no stub / end-site bone a point and matches the stock skin on >= 55 % (/tmp/bz_boner_b5_resetall.sgm); a bone with
   Deform off gets none, and Assign to it is refused;
H. snail vertex groups: Ctrl+G of removed points shows in the panel, By Bone and the selections and exports; a Ctrl+G
   elsewhere survives a Boner Assign (/tmp/bz_boner_b5_ctrlg.sgm); a blended weight stops Assign / Auto Skin elsewhere
   (named, kept) and an Assign including it makes it rigid; the By Bone Points toggle and the selection ring;
I. hidden interiors (fit-to-rig, gap 6): a scripted assign whose points are hidden interiors used to cancel saying only
   "Select the control points to assign" -- it now REFUSES naming Display > Show Interior Points, the same points with
   their frame corners are assigned through the corners (the B5 rule), and Select Points reports the points it cannot
   reach.
"""
import glob
import os
import sys
import traceback
import types
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import store, core, ops, draw, limbs, ui, boner  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

SNAIL = REPO + "/Assets/stock/snail.sgm"
WOOLLY = REPO + "/Assets/engine_stock/woolly_mammoth.sgm"
NC_SRC = "/tmp/bz_boner_nc_src.sgm"
OPEN = "/tmp/bz_boner_open.sgm"
OUT = "/tmp/bz_boner_out.sgm"
OFF_RULE = {"ChickenTest.sgm", "ChickenTest2.sgm", "ChickenTest2_oneSided.sgm"}
_log = open("/tmp/bz_boner_skin_log.txt", "w")


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


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.unregister(); blender_native.register()
    bpy.context.scene.bz_combine.validate_level = "OFF"
    # this suite picks exact point sets by pool index (corners included): with the interior points shown, nothing is
    # hidden and no interior follows a selected corner (patch_feel_test and skin_test cover that rule, B5)
    bpy.context.scene.bz_show_interiors = True


def imp(path, **kw):
    assert bpy.ops.bz.import_sgm(filepath=path, **kw) == {"FINISHED"}
    return bpy.context.view_layer.objects.active


def call(op, **kw):
    """(result, message): an ERROR report raises in background mode."""
    try:
        return op("EXEC_DEFAULT", True, **kw), ""
    except RuntimeError as e:
        return {"CANCELLED"}, str(e)


def export(obj, path=OUT):
    """('FINISHED' | 'CANCELLED', message, bytes or None)."""
    if os.path.exists(path):
        os.remove(path)
    bpy.context.view_layer.objects.active = obj
    try:
        r = bpy.ops.bz.export_sgm(filepath=path)
        msg = obj.get("bz_last_export", "")
        state = "FINISHED" if r == {"FINISHED"} else "CANCELLED"
    except RuntimeError as e:
        state, msg = "CANCELLED", str(e)
    data = open(path, "rb").read() if os.path.exists(path) else None
    return state, msg, data


def validate_verdict(obj):
    try:
        bpy.ops.bz.validate()
    except RuntimeError:
        pass
    return list(obj.get("bz_validation", []))


def set_selection(obj, idx):
    idx = set(int(i) for i in idx)
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index in idx
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def groups_of(obj, i):
    """{vertex group name: weight} of point i among the bone groups (the live bmesh in edit mode)."""
    names = set(store.bone_names(obj))
    gname = {vg.index: vg.name for vg in obj.vertex_groups}
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
        dl = bm.verts.layers.deform.active
        items = dict(bm.verts[i][dl]).items() if dl is not None else []
    else:
        items = [(g.group, g.weight) for g in obj.data.vertices[i].groups]
    return {gname[g]: round(w, 6) for g, w in items if gname.get(g) in names}


class _Rec:
    """Records UILayout / gpu calls (the panel tabs and the overlay run headless on it)."""
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


def panel_texts(fn, obj):
    log = []
    fn(_Rec(log), bpy.context, obj)
    return log, [kw.get("text", "") for name, a, kw in log if name in ("label", "operator", "operator_menu_enum")]


def record_overlay(obj, density=4, alpha=0.6):
    """Draw one frame of the By Bone overlay on a recording gpu: the ``(kind, attrs)`` of every batch it built."""
    batches = []
    real = (draw.gpu, draw.batch_for_shader, draw._pt_shader, draw._tri_shader, draw.texdraw.build_drawlist)
    draw.texdraw.build_drawlist = lambda *a, **kw: (None, [])        # GPU textures crash headless Blender
    draw.gpu = _Rec([])
    draw.batch_for_shader = lambda sh, kind, attrs, **kw: batches.append((kind, attrs)) or _Rec([])
    draw._pt_shader = lambda: _Rec([])
    draw._tri_shader = lambda: _Rec([])
    try:
        draw.invalidate(obj.name)
        draw._draw_object(obj, density=density, show_surface=True, show_handles=False, show_conn=False,
                          show_net=False, mode="BONE", edit_alpha=alpha)
    finally:
        draw.gpu, draw.batch_for_shader, draw._pt_shader, draw._tri_shader, draw.texdraw.build_drawlist = real
    return batches


def overlay_arrays(obj, density=4, alpha=0.6):
    """Draw one frame of the By Bone overlay on a recording gpu: (surface colours, point colours) the batches got."""
    batches = record_overlay(obj, density, alpha)
    st = draw._state[obj.name]
    assert not st.get("pool_error"), st.get("pool_error")
    tris = [attrs["color"] for kind, attrs in batches if kind == "TRIS"]
    pts = [attrs["color"] for kind, attrs in batches if kind == "POINTS"]
    active = draw.active_bone(obj)
    cols = draw.bone_colors(obj, st, density, active, alpha if obj.mode == "EDIT" else 1.0)
    assert cols is not None
    assert any(len(t) == len(cols[0]) and np.array_equal(np.asarray(t), cols[0]) for t in tris), "no By Bone surface batch"
    assert any(len(p) == len(cols[1]) and np.array_equal(np.asarray(p), cols[1]) for p in pts), "no By Bone point batch"
    return cols


def rows_of(colors, n_bones, active):
    """Per point / sample, which colour-table row it shows (n_bones = orange)."""
    tab = limbs.bone_color_table(n_bones, active)
    c = np.asarray(colors)[:, :3].astype(np.float64)
    d = np.linalg.norm(c[:, None, :].astype(np.float32) - tab[None, :, :].astype(np.float32), axis=2)
    return np.argmin(d, axis=1)


def layout_problems_for(model, bones):
    names = {model.bones[b].name for b in bones}
    return [p for p in core.aux_rule_problems(model) if p.split(":")[0][len("bone "):] in names]


def world_sorted(m):
    return np.array(sorted(map(tuple, np.round(R.world_vertices(m), 4))))


# ------------------------------------------------------------------------------------------------------------- A
def part_a():
    fresh()
    s = bpy.context.scene.bz_boner
    assert s.mode == "BONES" and s.bone == "" and s.follow_shading
    s.mode = "LIMBS"
    assert bpy.context.scene.bz_shade_mode == "LIMB"
    s.mode = "BONES"
    assert bpy.context.scene.bz_shade_mode == "BONE"
    s.follow_shading = False
    s.mode = "CONNECTIONS"; s.mode = "LIMBS"
    assert bpy.context.scene.bz_shade_mode == "BONE", "Follow Shading off still switched the overlay"
    s.follow_shading = True
    s.mode = "BONES"
    assert ui.BZ_PT_boner.bl_parent_id == "BZ_PT_panel" and hasattr(bpy.types, "BZ_PT_boner")
    for idn in ("skin_assign_active", "skin_select_points", "skin_remove", "skin_select_unassigned", "skin_auto",
                "pick_bone"):
        getattr(bpy.ops.bz, idn).get_rna_type()
    # a reload (in-place reinstall) keeps registration consistent: unregister + register twice
    blender_native.unregister(); blender_native.register()
    assert hasattr(bpy.types.Scene, "bz_boner") and bpy.context.scene.bz_boner.mode == "BONES"
    say("A REGISTER: scene.bz_boner + 6 operators + BZ_PT_boner under BZ_PT_panel; tabs switch the overlay while "
        "Follow Shading is on")


# ------------------------------------------------------------------------------------------------------------- B
def part_b():
    fresh()
    assert "FINISHED" in bpy.ops.bz.new_creature(filepath=NC_SRC, creature_name="BonerNC", detail=1,
                                                 clips_as_actions=False)
    obj = bpy.context.view_layer.objects.active
    src_bytes = open(NC_SRC, "rb").read()
    st_, msg, data = export(obj)
    assert st_ == "FINISHED" and data == src_bytes, (st_, msg[:200])
    s = bpy.context.scene.bz_boner
    names = store.bone_names(obj)
    owners0 = store.vert_bone(obj).copy()
    counts = np.bincount(owners0[owners0 >= 0], minlength=len(names))
    b = int(np.argmax(counts))
    other = int(np.argsort(-counts)[1])
    assert ui.BZ_PT_boner.poll(bpy.context)
    log, texts = panel_texts(ui.BZ_PT_boner.bones_tab, obj)
    assert "Unassigned: 0" in texts and "(Edit Mode: assign, select and remove points)" in texts, texts
    assert any(name == "prop_search" for name, a, kw in log), "no bone search field"
    assert not bpy.ops.bz.skin_assign_active.poll(), "Assign Selected enabled with no active bone (object mode)"
    s.bone = names[b]
    assert draw.active_bone(obj) == b and store.bone_index(obj, names[b]) == b

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    log, texts = panel_texts(ui.BZ_PT_boner.bones_tab, obj)
    par = store.rig(obj).data.bones[names[b]].parent
    assert f"{names[b]} · {counts[b]} points · parent {par.name if par else '(root)'}" in texts, texts
    assert {"Assign Selected", "Select Points", "Remove from Bone", "Auto Skin"} <= set(texts), texts
    _l, lt = panel_texts(ui.BZ_PT_boner.limbs_tab, obj)
    assert "Select Limb" in lt and "Assign Selected to Limb" in lt, lt
    _l, ct = panel_texts(ui.BZ_PT_boner.connections_tab, obj)
    assert {"Pick Ring", "From Selection", "Set", "Default", "Create for Limb", "Rename", "Delete"} <= set(ct), ct
    for mode in ("BONES", "LIMBS", "CONNECTIONS"):                   # the whole sub-panel and the main panel draw
        s.mode = mode
        log = []
        ui.BZ_PT_boner.draw(types.SimpleNamespace(layout=_Rec(log), bones_tab=ui.BZ_PT_boner.bones_tab,
                                                  limbs_tab=ui.BZ_PT_boner.limbs_tab,
                                                  connections_tab=ui.BZ_PT_boner.connections_tab), bpy.context)
        assert any(name == "prop" and a and a[1] == "mode" for name, a, kw in log), log[:3]
    s.mode = "BONES"
    log = []
    ui.BZ_PT_panel.draw(types.SimpleNamespace(layout=_Rec(log), _rig_box=ui.BZ_PT_panel._rig_box,
                                              _ring_rows=ui.BZ_PT_panel._ring_rows), bpy.context)
    assert any(kw.get("text") == "Limbs, connections and skinning: Boner (below)" for name, a, kw in log)

    assert call(bpy.ops.bz.skin_select_points)[0] == {"FINISHED"}
    sel = ops.point_selection(obj)
    assert np.array_equal(sel, owners0 == b), "Select Points did not select the bone's points"
    bpy.ops.ed.undo_push(message="select")
    gen = obj[store.KEY_GEN]
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}                     # a no-op assign
    assert obj[store.KEY_GEN] != gen and np.array_equal(store.vert_bone(obj), owners0)
    st_, msg, data = export(obj)
    assert st_ == "FINISHED" and data == src_bytes, ("no-op assign not byte-identical", st_, msg[:200])

    # Remove from Bone: 5 patch points of the active bone
    used = np.unique(store.patches(obj).reshape(-1))
    pts = [int(i) for i in np.nonzero(owners0 == b)[0] if i in set(used.tolist())][:5]
    set_selection(obj, pts + [int(np.nonzero(owners0 == other)[0][0])])      # one point of another bone: left alone
    bpy.ops.ed.undo_push(message="select 5")
    res, msg = call(bpy.ops.bz.skin_remove)
    assert res == {"FINISHED"}, msg
    vb = store.vert_bone(obj)
    assert all(vb[pts] == -1) and int((vb == -1).sum()) == 5 and vb[int(np.nonzero(owners0 == other)[0][0])] == other
    assert all(groups_of(obj, i) == {} for i in pts), [groups_of(obj, i) for i in pts[:2]]
    stats = ops.skin_stats_cached(obj)
    assert stats.unassigned == 5 and stats.unassigned_used == 5, stats
    _l, texts = panel_texts(ui.BZ_PT_boner.bones_tab, obj)
    assert "Unassigned: 5 (5 on patches: Export refuses)" in texts, texts
    st_, msg, data = export(obj)
    assert st_ == "CANCELLED" and data is None, (st_, msg[:200])
    assert "5 control points used by patches have no bone" in msg and "Select Unassigned" in msg, msg
    verdict = validate_verdict(obj)
    assert verdict and verdict[0].startswith("EXPORT BLOCKED: 5 control points used by patches have no bone"), verdict[:2]
    # By Bone: the 5 points orange, the active bone bright, the others dimmed
    surf, pcols, own = overlay_arrays(obj)
    rows = rows_of(pcols, len(names), b)
    assert np.array_equal(rows, np.where(vb >= 0, vb, len(names))), "point colours do not follow the owners"
    assert np.allclose(pcols[pts, :3], limbs.UNASSIGNED_COLOR)
    assert np.allclose(pcols[vb == b][:, :3], limbs.bone_palette(len(names))[b].astype(np.float32))
    base = limbs.vertex_bone_colors(vb, store.patches(obj), 4, len(names), b)
    orange_samples = int(np.all(np.isclose(base, limbs.UNASSIGNED_COLOR), axis=1).sum())
    mp = limbs.sample_cp_map(4)
    assert orange_samples == int(np.isin(store.patches(obj)[:, mp], pts).sum()) and orange_samples > 0

    # Undo restores the owners (byte-identical), Redo refuses again
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert np.array_equal(store.vert_bone(obj), owners0), "Undo did not restore the owners"
    st_, msg, data = export(obj)
    assert st_ == "FINISHED" and data == src_bytes, (st_, msg[:200])
    obj = _history(bpy.ops.ed.redo, obj); s = bpy.context.scene.bz_boner
    assert int((store.vert_bone(obj) == -1).sum()) == 5
    assert export(obj)[0] == "CANCELLED"

    pick_bone(obj, pts)

    # Select Unassigned -> Auto Skin -> Export OK
    set_selection(obj, [])
    assert call(bpy.ops.bz.skin_select_unassigned)[0] == {"FINISHED"}
    assert sorted(np.nonzero(ops.point_selection(obj))[0].tolist()) == sorted(pts)
    res, msg = call(bpy.ops.bz.skin_auto, target="AUTO")
    assert res == {"FINISHED"}, msg
    vb2 = store.vert_bone(obj)
    assert int((vb2 < 0).sum()) == 0 and np.array_equal(np.delete(vb2, pts), np.delete(owners0, pts))
    assert all(groups_of(obj, i) == {names[int(vb2[i])]: 1.0} for i in pts)
    path = "/tmp/bz_boner_b5_autoskin.sgm"
    st_, msg, data = export(obj, path)
    assert st_ == "FINISHED" and data is not None, (st_, msg[:300])
    m = sgm_parser.read(path)
    src = sgm_parser.read(NC_SRC)
    mo = np.array(R.vertex_bone_owners(m))
    used_m = np.unique([i for p in m.patches for i in p.control_points])
    assert (mo[used_m] >= 0).all(), "a patch point has no bone in the export"
    assert np.abs(world_sorted(m) - world_sorted(src)).max() < 2e-3
    touched = {b} | {int(vb2[i]) for i in pts}
    src_probs = len(core.aux_rule_problems(src))
    # the scaffold follows the corner-first layout since 2026-11-15 (ICParse build_creature), and so does the reskin
    assert not src_probs and not core.aux_rule_problems(m), (src_probs, core.aux_rule_problems(m)[:3])
    changed_bones = {i for i, (x, y) in enumerate(zip(src.bones, m.bones)) if x.meta5 != y.meta5}
    bpy.ops.object.mode_set(mode="OBJECT")
    verdict = validate_verdict(obj)
    assert not core.blocking(verdict), core.blocking(verdict)[:3]
    assert not [p for p in ICval.check_creature_rulebook(m) if p.startswith("ERROR")]
    say(f"B NEW CREATURE: untouched + no-op Assign byte-identical; Select Points {counts[b]} = '{names[b]}'; Remove 5 -> "
        f"owners -1, groups gone, orange in By Bone ({orange_samples} surface samples), Export + Validate refused "
        f"'5 control points ... Select Unassigned', Undo identical / Redo refused; Select Unassigned 5 -> Auto Skin -> "
        f"{path} OK (world kept, touched bones {sorted(touched)}; the scaffold's {src_probs} layout problems stay 0: "
        f"every bone re-derived, aux changed on {len(changed_bones)}), "
        "no blocking Validate line, rulebook ERROR-free)")
    return True


def pick_bone(obj, unassigned):
    """Pick Bone: execute / background invoke refuse; from the sidebar (the UI region), a click on a control point
    picks its owner, a click on a bone segment picks that bone, an unassigned point is refused, Esc cancels."""
    s = bpy.context.scene.bz_boner
    res, msg = call(bpy.ops.bz.pick_bone)
    assert res == {"CANCELLED"} and "interactive" in msg and "bz_boner.bone" in msg, (res, msg)
    try:
        r = bpy.ops.bz.pick_bone("INVOKE_DEFAULT")
        rmsg = ""
    except RuntimeError as e:
        r, rmsg = {"CANCELLED"}, str(e)
    assert r == {"CANCELLED"} and "3D Viewport" in rmsg, (r, rmsg)
    win = bpy.context.window_manager.windows[0]
    scr = win.screen
    area = next(a for a in scr.areas if a.type == "VIEW_3D")
    uireg = next(r for r in area.regions if r.type == "UI")
    rv3d = area.spaces.active.region_3d
    from mathutils import Vector
    V = ops._live_blender_verts(obj)
    centre = Vector(V.mean(axis=0))
    rv3d.view_location = centre
    rv3d.view_distance = 2.5 * float(np.linalg.norm(V - np.asarray(centre), axis=1).max())

    class P:
        invoke = ops.BZ_OT_pick_bone.invoke
        modal = ops.BZ_OT_pick_bone.modal
        status_text = ops.BZ_OT_pick_bone.status_text
        _update_screen = ops.BZ_OT_pick_bone._update_screen
        _mouse = ops.BZ_OT_pick_bone._mouse
        _mine = ops.BZ_OT_pick_bone._mine
        _cleanup = ops.BZ_OT_pick_bone._cleanup
        _draw_view = ops.BZ_OT_pick_bone._draw_view

        def __init__(self):
            self.reports, self.started = [], False

        def _start_modal(self, _context):
            self.started = True
            return True

        def report(self, level, text):
            self.reports.append((set(level), text))

    names = store.bone_names(obj)
    owners = store.vert_bone(obj)
    before = s.bone
    with bpy.context.temp_override(window=win, screen=scr, area=area, region=uireg):
        def start():
            op = P()
            ev = types.SimpleNamespace(type="LEFTMOUSE", value="PRESS", mouse_x=uireg.x, mouse_y=uireg.y)
            assert op.invoke(bpy.context, ev) == {"RUNNING_MODAL"} and op.started, op.reports
            assert op._region.type == "WINDOW" and op._rv3d is not None
            op._draw_view(bpy.context)                     # the UI region is not the picking region: nothing drawn
            return op

        def at(op, xy, t="MOUSEMOVE", v="NOTHING"):
            reg = op._region
            return types.SimpleNamespace(type=t, value=v, mouse_x=reg.x + xy[0], mouse_y=reg.y + xy[1])

        # 1. a control point with a bone: the one projected nearest the region centre among the owned
        op = start()
        reg = op._region
        xy = op._pt_xy
        ok = np.isfinite(xy).all(axis=1) & (owners >= 0)
        inside = ok & (xy[:, 0] > 20) & (xy[:, 0] < reg.width - 20) & (xy[:, 1] > 20) & (xy[:, 1] < reg.height - 20)
        cand = np.nonzero(inside)[0]
        assert len(cand), "no control point projects into the viewport"
        k = int(cand[0])
        hit = boner.pick_target(xy, owners, op._head_xy, op._tail_xy, xy[k])
        want_bone = hit.bone
        assert hit.kind in ("POINT", "BONE") and want_bone is not None
        assert op.modal(bpy.context, at(op, xy[k])) == {"RUNNING_MODAL"} and op._hit.bone == want_bone
        assert op.modal(bpy.context, at(op, xy[k], "MIDDLEMOUSE", "PRESS")) == {"PASS_THROUGH"}
        assert op.modal(bpy.context, at(op, xy[k], "LEFTMOUSE", "PRESS")) == {"FINISHED"}, op.reports
        assert s.bone == names[want_bone], (s.bone, names[want_bone])
        point_kind = hit.kind

        # 2. a bone segment: its projected midpoint (a bone whose midpoint no nearer point or segment claims)
        op = start()
        mids = (op._head_xy + op._tail_xy) / 2.0
        seg_bone = None
        for bi in range(len(names)):
            if not np.isfinite(mids[bi]).all() or not op._found[bi] or names[bi] == s.bone:
                continue
            if np.linalg.norm(op._head_xy[bi] - op._tail_xy[bi]) < 6:
                continue
            h = boner.pick_target(op._pt_xy, owners, op._head_xy, op._tail_xy, mids[bi])
            if h.kind == "BONE" and h.bone == bi:
                seg_bone = bi
                break
        assert seg_bone is not None, "no bone segment is pickable in this view"
        op.modal(bpy.context, at(op, mids[seg_bone]))
        assert op.modal(bpy.context, at(op, mids[seg_bone], "LEFTMOUSE", "PRESS")) == {"FINISHED"}
        assert s.bone == names[seg_bone]

        # 3. an unassigned point is refused (the modal keeps running); Esc cancels and keeps the bone
        op = start()
        xy = op._pt_xy
        u = next((int(i) for i in unassigned if np.isfinite(xy[i]).all()
                  and boner.pick_target(xy, owners, op._head_xy, op._tail_xy, xy[i]).point == int(i)), None)
        if u is not None:
            op.modal(bpy.context, at(op, xy[u]))
            assert op.modal(bpy.context, at(op, xy[u], "LEFTMOUSE", "PRESS")) == {"RUNNING_MODAL"}
            assert "no bone" in op._rule.message, op._rule.message
        assert op.modal(bpy.context, at(op, (5, 5), "ESC", "PRESS")) == {"CANCELLED"}
        assert s.bone == names[seg_bone]
        op._cleanup(bpy.context)
    s.bone = before
    say(f"B PICK BONE: execute + background invoke refused; from the sidebar a click on a control point picked "
        f"'{names[want_bone]}' ({point_kind}), on a bone segment '{names[seg_bone]}'; "
        + (f"unassigned point {u} refused; " if u is not None else "no unassigned point projects alone (not clicked); ")
        + "Esc keeps the bone")


# ------------------------------------------------------------------------------------------------------------- C
def part_c():
    fresh()
    obj = imp(SNAIL, clips_as_actions=False)
    src = sgm_parser.read(SNAIL)
    s = bpy.context.scene.bz_boner
    names = store.bone_names(obj)
    owners = store.vert_bone(obj).copy()
    have = [b for b in sorted(set(owners.tolist())) if b >= 0]
    s.bone = names[have[4]]
    bpy.context.scene.bz_shade_mode = "BONE"
    _surf, pcols, _own = overlay_arrays(obj)
    rows = rows_of(pcols, len(names), have[4])
    got = np.bincount(rows, minlength=len(names) + 1)
    assert [int(got[i]) for i in range(len(names))] == [b.vertex_count for b in src.bones], "By Bone counts != ranges"
    assert got[len(names)] == 0

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    # a native Delete breaks the pool: every skinning operator is disabled until Undo
    set_selection(obj, [100])
    bpy.ops.ed.undo_push(message="select 100")
    bpy.ops.mesh.delete(type="VERT")
    bpy.ops.ed.undo_push(message="deleted")
    store.forget_cache()
    skin_ops = (bpy.ops.bz.skin_assign_active, bpy.ops.bz.skin_select_points, bpy.ops.bz.skin_remove,
                bpy.ops.bz.skin_select_unassigned, bpy.ops.bz.skin_auto, bpy.ops.bz.pick_bone)
    assert not any(op.poll() for op in skin_ops), [str(op) for op in skin_ops if op.poll()]
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    store.forget_cache()
    assert store.pool_error(obj) is None and all(op.poll() for op in skin_ops), store.pool_error(obj)
    pick = [int(i) for i in np.nonzero(owners == have[4])[0][:6]]
    set_selection(obj, pick)
    bpy.ops.ed.undo_push(message="select")
    s.bone = names[have[5]]
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}
    x = ops.export_bytes(obj, bpy.context)
    obj = _history(bpy.ops.ed.undo, obj); s = bpy.context.scene.bz_boner
    assert np.array_equal(store.vert_bone(obj), owners)
    assert call(bpy.ops.bz.assign_bone, bone=str(have[5]))[0] == {"FINISHED"}
    y = ops.export_bytes(obj, bpy.context)
    assert x == y, "Assign through the active bone differs from Assign Selected to Bone"
    path = "/tmp/bz_boner_b5_skin.sgm"
    st_, msg, data = export(obj, path)
    assert st_ == "FINISHED", msg
    m = sgm_parser.read(path)
    assert not core.aux_rule_problems(m), core.aux_rule_problems(m)[:3]
    mo = np.array(R.vertex_bone_owners(m))
    assert (mo == have[5]).sum() == (owners == have[5]).sum() + 6
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"C SNAIL: By Bone point colours count every bone's source range; a broken pool disables the 6 skinning "
        f"operators (Undo re-enables); Assign through the active bone == Assign "
        f"Selected to Bone bytes ({len(x)}); {path} follows the stock layout on all {len(m.bones)} bones")


# ------------------------------------------------------------------------------------------------------------- D
def part_d():
    fresh()
    obj = imp(WOOLLY, clips_as_actions=False)
    src_bytes = open(WOOLLY, "rb").read()
    src = sgm_parser.read(WOOLLY)
    s = bpy.context.scene.bz_boner
    names = store.bone_names(obj)
    owners = store.vert_bone(obj).copy()
    assert sorted(np.nonzero(owners < 0)[0].tolist()) == [0, 1]
    stats = ops.skin_stats_cached(obj)
    assert stats.unassigned == 2 and stats.unassigned_used == 0, stats
    st_, msg, data = export(obj)
    assert st_ == "FINISHED" and data == src_bytes, (st_, msg[:200])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    set_selection(obj, [])
    assert call(bpy.ops.bz.skin_select_unassigned)[0] == {"FINISHED"}
    assert sorted(np.nonzero(ops.point_selection(obj))[0].tolist()) == [0, 1]
    counts = np.bincount(owners[owners >= 0], minlength=len(names))
    a, b = [int(i) for i in np.argsort(-counts)[:2]]
    pts = [int(i) for i in np.nonzero(owners == a)[0][:4]]
    set_selection(obj, pts)
    s.bone = names[b]
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}
    path = "/tmp/bz_boner_b5_woolly.sgm"
    st_, msg, data = export(obj, path)
    assert st_ == "FINISHED", msg
    m = sgm_parser.read(path)
    mo = np.array(R.vertex_bone_owners(m))
    assert list(np.nonzero(mo < 0)[0]) == [0, 1] and m.vertices[0] == src.vertices[0] and m.vertices[1] == src.vertices[1]
    assert not core.aux_rule_problems(m), core.aux_rule_problems(m)[:3]
    assert np.abs(world_sorted(m) - world_sorted(src)).max() < 2e-3
    # Remove of a patch point: refused at export
    bpy.ops.ed.undo_push(message="reskinned")
    s.bone = names[b]
    set_selection(obj, pts[:1])
    assert call(bpy.ops.bz.skin_remove)[0] == {"FINISHED"}
    st_, msg, _d = export(obj)
    assert st_ == "CANCELLED" and "1 control point used by patches has no bone" in msg, msg[:200]
    # Auto Skin with nothing selected: every unassigned point (the unused two as well)
    set_selection(obj, [])
    res, msg = call(bpy.ops.bz.skin_auto, target="AUTO")
    assert res == {"FINISHED"}, msg
    assert int((store.vert_bone(obj) < 0).sum()) == 0
    st_, msg, data = export(obj, "/tmp/bz_boner_woolly_auto.sgm")
    assert st_ == "FINISHED", msg
    m2 = sgm_parser.read("/tmp/bz_boner_woolly_auto.sgm")
    assert (np.array(R.vertex_bone_owners(m2)) >= 0).all() and not core.aux_rule_problems(m2)
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"D WOOLLY: unused unowned [0, 1] = Select Unassigned, untouched export identical; a 4-point reskin keeps them "
        f"first with their bytes ({path}, stock layout on all bones, world kept); Remove of a patch point refused; Auto "
        "Skin (nothing selected) skins every unassigned point and exports on the layout")


# ------------------------------------------------------------------------------------------------------------- E
def part_e():
    d = core.load_creature(SNAIL)
    mm = d.model
    keep = list(range(len(d.patches) - 1))
    data = core.export_grown(SNAIL, d.verts, d.patches[keep], np.array(R.vertex_bone_owners(mm)), d.patch_mat[keep],
                             d.patch_uv[keep], np.array([p.type for p in mm.patches])[keep],
                             core._patch_limb_of(mm)[keep])
    open(OPEN, "wb").write(data)
    fresh()
    obj = imp(OPEN, clips_as_actions=False)
    src = sgm_parser.read(OPEN)
    assert not core.aux_rule_problems(src), core.aux_rule_problems(src)[:3]
    s = bpy.context.scene.bz_boner
    names = store.bone_names(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    patches = store.patches(obj)
    users = core.side_users(patches)
    pi, side, _k = next((p, s_, kk) for p, s_, kk in core.patch_sides(patches) if len(users[kk]) == 1)
    ends = [int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])]
    set_selection(obj, ends)
    n0 = len(obj.data.vertices)
    assert call(bpy.ops.bz.extrude_patch)[0] == {"FINISHED"}
    n1 = store.point_count(obj)
    new = list(range(n0, n1))
    vb = store.vert_bone(obj)
    s.bone = names[int(vb[new[0]])]
    set_selection(obj, new)
    assert call(bpy.ops.bz.skin_remove)[0] == {"FINISHED"}
    st_, msg, _d = export(obj)
    assert st_ == "CANCELLED" and "have no bone" in msg, msg[:200]
    assert call(bpy.ops.bz.skin_auto, target="SELECTED")[0] == {"FINISHED"}
    path = "/tmp/bz_boner_b5_extrude.sgm"
    st_, msg, data = export(obj, path)
    assert st_ == "FINISHED", msg
    m = sgm_parser.read(path)
    assert len(m.vertices) == n1 and len(m.patches) == len(src.patches) + 1
    assert not core.aux_rule_problems(m), core.aux_rule_problems(m)[:3]
    assert (np.array(R.vertex_bone_owners(m)) >= 0).all()
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"E EXTRUDE: open snail + Extrude (+{n1 - n0} points) + Remove them -> refused -> Auto Skin -> {path} (grown, "
        f"{len(m.patches)} patches) follows the stock layout on every bone, every point skinned")


# ------------------------------------------------------------------------------------------------------------- G
CHEETAH = REPO + "/Assets/engine_stock/cheetah.sgm"


def part_g():
    """Auto Skin guards on a stock creature: Object Mode never uses the unshown selection, a selection of every point is
    refused (Reset All is asked for by name); Reset All gives no stub / end-site / non-deforming bone a point and
    equals the pure rule measured in boner_test (segments to the nearest child, leaves zero-length)."""
    fresh()
    obj = imp(CHEETAH, clips_as_actions=False)
    src = sgm_parser.read(CHEETAH)
    names = store.bone_names(obj)
    owners0 = store.vert_bone(obj).copy()
    assert (owners0 >= 0).all()
    assert ops.point_selection(obj).all(), "import no longer selects every point (the guard's scenario)"
    res, msg = call(bpy.ops.bz.skin_auto, target="AUTO")          # Object Mode: Unassigned, and there are none
    assert res == {"CANCELLED"} and np.array_equal(store.vert_bone(obj), owners0), (res, msg)
    res, msg = call(bpy.ops.bz.skin_auto, target="SELECTED")
    assert res == {"CANCELLED"} and "Edit Mode" in msg, (res, msg)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.ed.undo_push(message="edit")
    assert ops.point_selection(obj).all()
    for t in ("AUTO", "SELECTED"):
        res, msg = call(bpy.ops.bz.skin_auto, target=t)
        assert res == {"CANCELLED"} and "Reset All" in msg, (t, res, msg)
    assert np.array_equal(store.vert_bone(obj), owners0)
    bpy.ops.object.mode_set(mode="OBJECT")

    # Reset All = the pure rule on the file's bind pose
    mask = boner.auto_skin_mask(src)
    hd = np.array([x[:3, 3] for x in R.bind_world_matrices(src)])
    tl = boner.skin_segments(hd, [b.parent for b in src.bones])
    W = R.world_vertices(src)
    res, msg = call(bpy.ops.bz.skin_auto, target="ALL")
    assert res == {"FINISHED"}, msg
    vb = store.vert_bone(obj)
    # the pure rule: every point's nearest bone, then every handle its nearest corner's bone (Auto Skin's default since
    # 2026-11-15: a battle creature skins each patch with its corner bones only, and every stock creature does this)
    from ICParse.sgm.creature import layout as LAYOUT
    pure = LAYOUT.handles_follow_corners(boner.auto_skin(W, hd, tl, mask),
                                         [list(p.control_points) for p in src.patches])
    same_pure = float((vb == pure).mean())
    match = float((vb == owners0).mean())
    ends = boner.end_site_bones(names)
    stubs = boner.stub_bones(src)
    assert not ends[vb].any() and not stubs[vb].any(), [names[i] for i in np.unique(vb) if not mask[i]]
    assert same_pure >= 0.999, same_pure
    assert match >= 0.55, match                                  # measured 59.8% (boner_test: 52.1% mean on 61)
    assert all(groups_of(obj, i) == {names[int(vb[i])]: 1.0} for i in range(0, len(vb), 97))
    st_, msg, data = export(obj, "/tmp/bz_boner_b5_resetall.sgm")
    assert st_ == "FINISHED", msg[:300]
    assert not core.aux_rule_problems(sgm_parser.read("/tmp/bz_boner_b5_resetall.sgm"))

    # a bone whose Deform is off gets no point from Reset All, and Assign Selected to it is refused
    fresh()
    obj = imp(CHEETAH, clips_as_actions=False)
    assert np.array_equal(store.vert_bone(obj), owners0)
    counts = np.bincount(vb, minlength=len(names))
    nd = int(np.argmax(np.where(mask, counts, -1)))
    store.rig(obj).data.bones[store.bone_display_name(obj, nd)].use_deform = False
    res, msg = call(bpy.ops.bz.skin_auto, target="ALL")
    assert res == {"FINISHED"}, msg
    vb3 = store.vert_bone(obj)
    assert not (vb3 == nd).any() and not ends[vb3].any() and not stubs[vb3].any()
    bpy.ops.object.mode_set(mode="EDIT")
    set_selection(obj, [0, 1])
    bpy.context.scene.bz_boner.bone = store.bone_display_name(obj, nd)
    before = store.vert_bone(obj).copy()
    res, msg = call(bpy.ops.bz.skin_assign_active)
    assert res == {"CANCELLED"} and "does not deform" in msg, (res, msg)
    res, msg = call(bpy.ops.bz.assign_bone, bone=str(nd))
    assert res == {"CANCELLED"} and "does not deform" in msg, (res, msg)
    assert np.array_equal(store.vert_bone(obj), before)
    bpy.ops.object.mode_set(mode="OBJECT")
    store.rig(obj).data.bones[store.bone_display_name(obj, nd)].use_deform = True
    say(f"G AUTO SKIN GUARDS: cheetah just imported (every point selected): Object Mode Auto skins nothing, Selected "
        f"refused; Edit Mode with all selected refused (Reset All named); Reset All == the pure rule on "
        f"{same_pure:.1%} of points, matches Relic's skin on {match:.1%}, no stub / end-site bone gets a point "
        f"(/tmp/bz_boner_b5_resetall.sgm on the layout); Deform off on '{names[nd]}': Reset All gives it none, Assign "
        "to it refused")


# ------------------------------------------------------------------------------------------------------------- H
def part_h():
    """Boner tools and Blender's own vertex-group edits (Ctrl+G, weight paint) agree: the read-outs, By Bone and the
    selections show what Export adopts, a Boner edit keeps a rigid group reassignment made elsewhere, and refuses
    rather than wipe blended weights outside the edit. Plus the By Bone Points toggle and the selection ring."""
    def ctrl_g(obj, pts, bone_name, weight=1.0, remove_from=None):
        set_selection(obj, pts)
        if remove_from is not None:
            obj.vertex_groups.active_index = obj.vertex_groups[remove_from].index
            bpy.ops.object.vertex_group_remove_from()
        obj.vertex_groups.active_index = obj.vertex_groups[bone_name].index
        bpy.context.scene.tool_settings.vertex_group_weight = weight
        bpy.ops.object.vertex_group_assign()
        bpy.context.scene.tool_settings.vertex_group_weight = 1.0
        bpy.ops.ed.undo_push(message="ctrl+g")

    def start():
        fresh()
        obj = imp(SNAIL, clips_as_actions=False)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.ed.undo_push(message="edit")
        return obj

    obj = start()
    s = bpy.context.scene.bz_boner
    names = store.bone_names(obj)
    own0 = store.vert_bone(obj).copy()
    counts = np.bincount(own0[own0 >= 0], minlength=len(names))
    a, b, c = [int(i) for i in np.argsort(-counts)[:3]]
    used = set(store.patches(obj).reshape(-1).tolist())
    pa = [int(i) for i in np.nonzero(own0 == a)[0] if int(i) in used][:4]
    X, Y = [int(i) for i in np.nonzero(own0 == a)[0][[10, 20]]]

    # (a) Remove 4 points, then Ctrl+G them into bone c's group: read-outs, By Bone and Select Unassigned follow Export
    s.bone = names[a]
    set_selection(obj, pa)
    assert call(bpy.ops.bz.skin_remove)[0] == {"FINISHED"}
    assert ops.skin_stats_cached(obj).unassigned_used == 4
    ctrl_g(obj, pa, names[c])
    stats = ops.skin_stats_cached(obj)
    assert stats.unassigned == 0 and stats.unassigned_used == 0, stats
    assert all(draw.live_owners(obj)[pa] == c)
    _surf, pcols, _own = overlay_arrays(obj)
    assert not np.allclose(pcols[pa, :3], limbs.UNASSIGNED_COLOR)
    set_selection(obj, [])
    assert call(bpy.ops.bz.skin_select_unassigned)[0] == {"FINISHED"} and not ops.point_selection(obj).any()
    s.bone = names[c]
    assert call(bpy.ops.bz.skin_select_points)[0] == {"FINISHED"} and ops.point_selection(obj)[pa].all()
    assert export(obj)[0] == "FINISHED"

    # (b) Ctrl+G X from a to b, then a Boner Assign of Y: X keeps b (adopted into the table), Export writes it
    obj = start()
    s = bpy.context.scene.bz_boner
    ctrl_g(obj, [X], names[b], remove_from=names[a])
    assert int(store.vert_bone(obj)[X]) == a and int(draw.live_owners(obj)[X]) == b
    set_selection(obj, [Y])
    s.bone = names[c]
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}
    assert groups_of(obj, X) == {names[b]: 1.0}, groups_of(obj, X)
    assert int(store.vert_bone(obj)[X]) == b and int(store.vert_bone(obj)[Y]) == c
    st_, msg, data = export(obj, "/tmp/bz_boner_b5_ctrlg.sgm")
    assert st_ == "FINISHED", msg[:200]
    m = sgm_parser.read("/tmp/bz_boner_b5_ctrlg.sgm")
    wm, ws = np.round(R.world_vertices(m), 3), np.round(R.world_vertices(sgm_parser.read(SNAIL)), 3)
    mo = np.array(R.vertex_bone_owners(m))
    kx = [k for k in range(len(wm)) if np.allclose(wm[k], ws[X], atol=2e-3)]
    assert kx and any(int(mo[k]) == b for k in kx), (kx, [int(mo[k]) for k in kx])

    # (c) a blended weight on X: a Boner edit elsewhere (Assign, Auto Skin) refuses and leaves it; one that includes X
    # makes it rigid
    obj = start()
    s = bpy.context.scene.bz_boner
    ctrl_g(obj, [X], names[b], weight=0.5)
    g0 = groups_of(obj, X)
    assert len(g0) == 2, g0
    assert "blended" in export(obj)[1]
    assert ops.skin_stats_cached(obj).blended == 1
    _l, texts = panel_texts(ui.BZ_PT_boner.bones_tab, obj)
    assert "blended" in " ".join(texts), texts
    set_selection(obj, [Y])
    s.bone = names[c]
    res, msg = call(bpy.ops.bz.skin_assign_active)
    assert res == {"CANCELLED"} and "blended" in msg and f"point {X}" in msg, (res, msg)
    res, msg = call(bpy.ops.bz.skin_auto, target="SELECTED")
    assert res == {"CANCELLED"} and "blended" in msg, (res, msg)
    assert groups_of(obj, X) == g0 and "blended" in export(obj)[1]
    set_selection(obj, [X, Y])
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}
    assert groups_of(obj, X) == {names[c]: 1.0} and export(obj)[0] == "FINISHED"

    # (d) By Bone points: the Points toggle hides them; a selected point gets a white ring in Edit Mode
    set_selection(obj, [X, Y, 5])
    bat = record_overlay(obj)
    pts_b = [attrs for kind, attrs in bat if kind == "POINTS"]
    ring = [p for p in pts_b if len(p["pos"]) == 3]
    assert ring and np.allclose(np.asarray(ring[0]["color"]), draw._SELECT_RING), [len(p["pos"]) for p in pts_b]
    assert any(len(p["pos"]) == len(store.vert_bone(obj)) for p in pts_b)
    s.show_points = False
    bat = record_overlay(obj)
    assert not [1 for kind, attrs in bat if kind == "POINTS"], "Points toggle off still draws the bone points"
    assert [1 for kind, attrs in bat if kind == "TRIS"]
    s.show_points = True
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"H GROUPS: Remove 4 + Ctrl+G to '{names[c]}' -> panel 0 unassigned, By Bone not orange, Select Unassigned "
        f"none, Select Points has them, Export OK; Ctrl+G of point {X} survives a Boner Assign elsewhere (adopted, "
        "written with its new bone); a blended weight stops Assign / Auto Skin elsewhere (named, kept) and an Assign "
        "including it makes it rigid; Points toggle + selection ring")


# ------------------------------------------------------------------------------------------------------------- F
def part_f():
    eng = sorted(glob.glob(os.path.join(REPO, "Assets", "engine_stock", "*.sgm")))
    add = sorted(glob.glob(os.path.join(REPO, "Assets", "stock", "*.sgm")))
    assert len(eng) == 61, len(eng)
    off = {os.path.basename(f) for f in eng + add if core.aux_rule_problems(core.read_source(f))}
    assert not (off & {os.path.basename(f) for f in eng}), off
    assert off == OFF_RULE, off
    say(f"F LAYOUT PIN: corners first + aux_count == corners on 61/61 base-game, {len(add) - len(off)}/{len(add)} "
        f"add-on stock (off: {sorted(off)}, template-made)")


# ------------------------------------------------------------------------------------------------------------- I
def part_i():
    """A scripted skin assign whose points are HIDDEN interiors must not cancel silently (fit-to-rig, gap 6).

    Interiors are hidden by default (B5), and ``ops.select_points`` skips a hidden vertex as Blender's own selection
    does. A script that picked points by position -- the native pillbug build's limb-skinning passes -- then ran
    ``bz.skin_assign_active`` got a WARNING and a CANCEL that said only "Select the control points to assign", so the
    run went on with those points unskinned (the script had to guard with `if not point_selection(obj).any(): continue`
    and to turn Show Interior Points on for a whole pass). Now: a hidden interior whose frame corner is in the mask
    follows its corner and IS assigned, and one left behind is named, with Show Interior Points, in an ERROR."""
    fresh()
    bpy.context.scene.bz_show_interiors = False           # the default: interiors hidden
    obj = imp(CHEETAH)
    from blender_native import patchfeel
    bpy.ops.object.mode_set(mode="EDIT")
    hid = patchfeel.hidden_points(obj)
    assert hid.any(), "no interior point is hidden"
    patches = store.patches(obj)
    own = store.live_skin(obj).owners
    corners = np.zeros(len(hid), dtype=bool)
    corners[np.unique(patches[:, [0, 3, 12, 15]])] = True
    # a patch whose 4 interiors are hidden and whose corners are not, all on one bone
    inter = patches[:, [5, 6, 9, 10]]
    pi = next(i for i in range(len(patches)) if hid[inter[i]].all() and not hid[patches[i][[0, 3, 12, 15]]].any()
              and len(set(own[patches[i]])) == 1)
    only_hidden = np.zeros(len(hid), dtype=bool)
    only_hidden[inter[pi]] = True
    bone_from = int(own[inter[pi][0]])
    other = next(int(b) for b in np.unique(own) if int(b) != bone_from and b >= 0)
    name = store.bone_display_name(obj, other)
    bpy.context.scene.bz_boner.bone = name
    # 1. a mask of hidden interiors ALONE: nothing can be selected, and the refusal says why
    k = ops.select_points(obj, only_hidden)
    assert k == 4 and not ops.point_selection(obj).any(), (k, int(ops.point_selection(obj).sum()))
    assert ops.hidden_left_behind(obj, only_hidden) == 4
    res, msg = call(bpy.ops.bz.skin_assign_active)
    assert res == {"CANCELLED"} and "Show Interior Points" in msg and "hidden interior points" in msg, (res, msg)
    assert all(int(store.live_skin(obj).owners[i]) == bone_from for i in inter[pi]), "the assign ran anyway"
    # 2. the same four with their frame corners: the corners select, the interiors follow them (the B5 rule)
    with_corners = only_hidden.copy()
    with_corners[patches[pi][[0, 3, 12, 15]]] = True
    ops.select_points(obj, with_corners)
    assert ops.hidden_left_behind(obj, with_corners) == 0
    assert ops.point_selection(obj)[inter[pi]].all(), "the hidden interiors did not follow their corners"
    assert call(bpy.ops.bz.skin_assign_active)[0] == {"FINISHED"}
    assert all(int(store.live_skin(obj).owners[i]) == other for i in inter[pi]), "the interiors were not assigned"
    # 3. Select Points says how many of the bone's points it could not reach
    bpy.context.scene.bz_boner.bone = store.bone_display_name(obj, bone_from)
    mask = store.live_skin(obj).owners == bone_from
    left = ops.hidden_left_behind(obj, mask)
    assert left > 0, left
    assert call(bpy.ops.bz.skin_select_points)[0] == {"FINISHED"}
    bpy.ops.object.mode_set(mode="OBJECT")
    say(f"I HIDDEN INTERIORS: a mask of 4 hidden interiors alone selects 0 points and Assign REFUSES naming Show "
        f"Interior Points (it used to cancel with 'Select the control points to assign'); the same 4 with their "
        f"corners follow them and are assigned; Select Points reports {left} points of "
        f"'{store.bone_display_name(obj, bone_from)}' it cannot reach")


try:
    blender_native.register()
    part_a()
    part_b()
    part_c()
    part_d()
    part_e()
    part_f()
    part_g()
    part_h()
    part_i()
    blender_native.unregister()
    open("/tmp/bz_boner_skin_done.txt", "w").write("PASS\n")
    say("SKIN_BONER_PASS")
except Exception:
    open("/tmp/bz_boner_skin_done.txt", "w").write("ERROR\n" + traceback.format_exc())
    traceback.print_exc()
    sys.exit(1)
