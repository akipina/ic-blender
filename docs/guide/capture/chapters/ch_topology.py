"""Chapter `topology`: the tools that MAKE geometry (B7), on the stock cheetah.

Six short stories, each on its own fresh import (an undo chain across six tools is the fragile part of a
scripted session, an import is one second):

  A  patch select + the Topology box;
  B  Delete Patch on two neighbours -> Fill Hole closes the six-side hole;
  C  Delete Patch on one -> Add Quad grows a patch on an open side -> its free corners dragged onto the
     hole's far corners -> Weld closes the seam (corner pairs AND handle pairs);
  D  Subdivide one rear-leg patch: the split propagates along the strips, the surface does not move;
  E  Extrude Region on two back patches (Max's Extrude & Bevel: Amount + Outline, walls on the boundary);
  F  Mirror to Whole on a half cheetah, and Attach joining a second creature in.

Capture notes: a mode switch resets the sidebar to Tool, so _sidebar() after every one; patch selection is
Face select mode (the mesh carries one corner quad per patch); the Topology box only draws in Edit Mode, so
the sidebar is scrolled to it for the shots that are about the panel rather than the viewport.
"""
import os
import subprocess

import bmesh
import bpy
import numpy as np

from blender_native import core, patchsel, store, topoops


# --------------------------------------------------------------------------------------------------- helpers
def _view_axis(g, kind):
    with g.override():
        bpy.ops.view3d.view_axis(type=kind)


def _zoom(g, delta):
    with g.override():
        bpy.ops.view3d.zoom(delta=delta)


def _dist(g, d):
    """Set the view distance outright: view3d.zoom's delta steps are too small to pull back from a two-vertex
    view_selected, and a fixed distance makes the shots repeatable."""
    win, area, region = g.view3d()
    area.spaces.active.region_3d.view_distance = float(d)


def _clip(g, beyond=None, start=0.01):
    """View depth clipping: in perspective, clip_end a little beyond the view pivot hides the far side of the body,
    so a hole shows the background instead of the inside of the opposite flank. No argument: reset."""
    win, area, region = g.view3d()
    sp = area.spaces.active
    rv3d = sp.region_3d
    if beyond is None:
        sp.clip_end, sp.clip_start = 1000.0, 0.01
        return
    rv3d.view_perspective = "PERSP"
    sp.clip_start = start
    sp.clip_end = float(rv3d.view_distance) + beyond


def _mouse_park(g, delay=1.8):
    win, area, region = g.view3d()
    x = win.x + area.x + area.width // 2
    y = win.y + (win.height - (area.y + area.height)) + 120
    subprocess.Popen(["xdotool", "sleep", str(delay), "mousemove", str(x), str(y)])


def _sidebar(g):
    """Re-open the SGM tab (a mode switch resets it to Tool) and park the mouse. The tab switch is delivered as
    X events while Blender is idle, so a scroll must wait for the NEXT step -- never scroll in this one."""
    g.sidebar()
    _mouse_park(g)


def _scroll(g, n=11):
    """Scroll the sidebar down to the tool boxes (Modeling / Topology / Select / Sculpt / Tools). Only ever called
    in a step AFTER the one that opened the tab."""
    g.scroll_sidebar(n)


def _activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj)
    bpy.context.view_layer.objects.active = obj


def _wipe():
    """Every creature and rig out of the file (each story starts from a fresh import)."""
    for o in list(bpy.data.objects):
        if store.is_native(o) or o.type == "ARMATURE" or o.get("bz_rig"):
            bpy.data.objects.remove(o, do_unlink=True)


def _imp(g, path=None):
    _wipe()
    bpy.ops.bz.import_sgm(filepath=path or (g.STOCK + "/cheetah.sgm"), clips_as_actions=False)
    obj = g.active()
    _activate(obj)
    return obj


def _verts(obj):
    if obj.mode == "EDIT":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        return np.array([v.co[:] for v in bm.verts], dtype=np.float64)
    a = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", a)
    return a.reshape(-1, 3).astype(np.float64)


def _faces(g, obj, rows):
    """Face (patch) select mode with exactly ``rows`` selected."""
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = False
    n = patchsel.select_rows(bm, store.patches(obj), rows)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return n


def _select_edge(obj, a, b):
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    for f in bm.faces:
        f.select = False
    for v in bm.verts:
        v.select = False
    for e in bm.edges:
        e.select = {e.verts[0].index, e.verts[1].index} == {int(a), int(b)}
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _select_verts(obj, idx):
    bpy.context.tool_settings.mesh_select_mode = (True, False, False)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    want = {int(i) for i in idx}
    for f in bm.faces:
        f.select = False
    for e in bm.edges:
        e.select = False
    for v in bm.verts:
        v.select = v.index in want and not v.hide
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _set_pos(obj, i, pos):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[int(i)].co = np.asarray(pos, dtype=np.float32)
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def _side_map(patches):
    """{patch row: {side key}} and {side key: [rows]} -- two patches are neighbours when they share a key."""
    of_row, of_key = {}, {}
    for pi, s, k in core.patch_sides(patches):
        of_row.setdefault(pi, set()).add(k)
        of_key.setdefault(k, []).append(pi)
    return of_row, of_key


def _flank_rows(obj, n=2, x_sign=1.0):
    """``n`` torso patches that really share sides, high on one flank: the Right view then looks straight at the
    hole they leave. Patches on a connection ring are skipped (Delete Patch refuses them)."""
    patches = store.patches(obj)
    V = _verts(obj)
    C = patches.reshape(-1, 16)[:, [0, 3, 12, 15]]
    xm, zm = V[C, 0].mean(1), V[C, 2].mean(1)
    limb = store.patch_limb(obj)
    torso = [i for i, nm in enumerate(store.limb_names(obj)) if nm.endswith("_Torso")]
    rings = topoops._rings_for_plan(obj)
    ok = {r for r in range(len(C)) if limb[r] in torso and xm[r] * x_sign > 0.02
          and not patchsel.ring_patch_refusals(patches, [r], rings)}
    of_row, of_key = _side_map(patches)
    for start in sorted(ok, key=lambda r: -(abs(xm[r]) + 0.3 * zm[r])):
        out = [start]
        while len(out) < n:
            nb = [q for r in out for k in of_row[r] for q in of_key[k] if q in ok and q not in out]
            if not nb:
                break
            out.append(max(nb, key=lambda r: abs(xm[r]) + 0.3 * zm[r]))
        if len(out) == n:
            return out
    return [next(iter(ok))]


def _mirror_rows(obj, rows):
    """``rows`` plus their mirror partners across X. Deleting both means the Right view looks THROUGH the near hole
    and out of the far one, at the background -- otherwise the inside of the opposite flank fills the hole and the
    creature looks untouched (the trick ch_edit uses for its open rhino)."""
    patches = store.patches(obj).reshape(-1, 16)
    V = _verts(obj)
    C = patches[:, [0, 3, 12, 15]]
    cen = V[C].mean(1)
    out = list(rows)
    for r in rows:
        t = cen[r].copy()
        t[0] *= -1
        q = int(np.argmin(np.linalg.norm(cen - t, axis=1)))
        if q not in out:
            out.append(q)
    return out


def _hide_far(g, obj, sign=-1.0):
    """Hide the patches on the far side with Hide Patches (H), so a hole on the near side shows the BACKGROUND
    instead of the inside of the opposite flank -- which is textured and lit exactly like the near one, so a hole
    photographs as no hole at all."""
    V = _verts(obj)
    patches = store.patches(obj).reshape(-1, 16)
    rows = [r for r in range(len(patches)) if (V[patches[r], 0] * sign > 0).all()]
    _faces(g, obj, rows)
    g.call(bpy.ops.bz.hide_patches)
    bpy.ops.mesh.select_all(action="DESELECT")
    return len(rows)


def _open_sides(obj):
    patches = store.patches(obj)
    users = core.side_users(patches)
    return [(pi, s, k) for pi, s, k in core.patch_sides(patches) if len(users[k]) == 1]


def _report(obj, key="bz_last_topo"):
    return str(obj.get(key, ""))


# ----------------------------------------------------------------------------------------------------- steps
def steps(g):
    scene = bpy.context.scene
    bpy.context.preferences.view.smooth_view = 0
    scene.bz_combine.validate_level = "OFF"

    # ------------------------------------------------------------------ A. patch select and the Topology box
    obj = _imp(g)
    g.frame([obj])
    _zoom(g, 1)
    g.orbit(-25)
    g.mode(obj, "EDIT")
    _sidebar(g)
    rows = _flank_rows(obj, 1)
    n = _faces(g, obj, rows)
    g.frame()
    _zoom(g, -7)
    print("GUIDE A selected faces:", n, "rows", rows)
    yield "select", ("Face select mode (3) is patch select: one click takes a whole patch, and its outline is "
                     "drawn orange (the mesh carries one quad over each patch's four corners, never shaded).")

    _scroll(g, 11)
    yield "box", ("The same selection with the sidebar scrolled to the patch tools: Modeling (Extrude Patch, "
                  "Delete Patch, Reset Interiors, Set Corner / Set Coplanar), Topology and Select.")

    # ------------------------------------------------------------------ B. Delete Patch + Fill Hole
    rows = _mirror_rows(obj, _flank_rows(obj, 2))
    _faces(g, obj, rows)
    scene.bz_shade_mode = "LIMB"                   # flat limb colours: a hole in them reads as a hole
    arm = store.rig(obj)
    if arm is not None:
        arm.hide_set(True)
    _view_axis(g, "RIGHT")
    g.frame(all=True)
    _zoom(g, 4)
    g.orbit(18)
    p0 = int(obj[store.KEY_NPATCH])
    yield "delete_before", (f"Two neighbouring torso patches selected as patches, and their mirror partners on the "
                            f"far flank with them, so the Right view will look through both holes "
                            f"({p0} patches on the stock cheetah).")
    res, err = g.call(bpy.ops.bz.delete_patch)
    p1 = int(obj[store.KEY_NPATCH])
    opens = len(_open_sides(obj))
    nhid = _hide_far(g, obj)
    print("GUIDE B delete:", res, err, p0, "->", p1, "open sides", opens, "hid", nhid, "|", _report(obj))
    yield "hole", (f"Modeling > Delete Patch: {p0} -> {p1} patches and a hole. (The far half was then hidden with "
                   f"Select > Hide Patches, so the hole shows the background instead of the inside of the "
                   f"opposite flank.) The game refuses an open mesh, so the hole is yours to close -- Validate "
                   f"lists the {opens} dangling edges.")
    bpy.ops.mesh.select_all(action="DESELECT")
    res, err = g.call(bpy.ops.bz.fill_hole)
    p2 = int(obj[store.KEY_NPATCH])
    print("GUIDE B fill:", res, err, p1, "->", p2, "open", len(_open_sides(obj)), "|", _report(obj))
    yield "filled", (f"Topology > Fill Hole with nothing selected closes every hole it can, with a strip of patches "
                     f"built on the existing sides, so the fill is watertight by construction "
                     f"({p1} -> {p2} patches, {len(_open_sides(obj))} open sides).")
    scene.bz_shade_mode = "TEXTURED"

    # ------------------------------------------------------------------ C. Add Quad and Weld
    obj = _imp(g)
    g.mode(obj, "EDIT")
    _sidebar(g)
    rows = _flank_rows(obj, 1)
    _faces(g, obj, _mirror_rows(obj, rows))
    g.call(bpy.ops.bz.delete_patch)
    _hide_far(g, obj)
    patches = store.patches(obj)
    V = _verts(obj)
    # the NEAR hole's four sides (the mirror hole's are on -X); grow from the highest one, then weld onto the side
    # OPPOSITE it -- an adjacent side would ask a patch to put two corners on one point, which Weld refuses
    sides = [(pi, s, [int(patches.reshape(-1, 16)[pi][c]) for c in core._SIDE_CPS[s]])
             for pi, s, _k in _open_sides(obj)]
    sides = [t for t in sides if V[t[2], 0].mean() > 0]
    sides.sort(key=lambda t: -V[t[2][0], 2] - V[t[2][3], 2])
    pi, s, cps = sides[0]
    ends = {cps[0], cps[3]}
    far = next(t[2] for t in sides[1:] if not (ends & {t[2][0], t[2][3]}))
    scene.bz_shade_mode = "LIMB"
    arm = store.rig(obj)
    if arm is not None:
        arm.hide_set(True)
    _select_edge(obj, cps[0], cps[3])
    _view_axis(g, "RIGHT")
    g.frame()
    _dist(g, 0.26 * float(np.linalg.norm(V.max(0) - V.min(0))))
    g.orbit(18)
    yield "addquad_before", ("A one-patch hole with one of its open sides selected in Edge select mode (selecting "
                             "the side's two corners does the same job).")
    _scroll(g, 11)
    res, err = g.call(bpy.ops.bz.extrude_sides)
    new = store.patches(obj).reshape(-1, 16)[-1]
    print("GUIDE C add quad:", res, err, "|", _report(obj))
    yield "addquad", ("Topology > Add Quad: a patch grown on that side by the side's own step. Its other three "
                      "sides are open -- Add Quad continues a surface, it does not close one.")
    # drag the new patch's two free corners onto the hole's far corners, then Weld the pairs
    free = [int(new[c]) for c in (12, 15)] if int(new[0]) in cps or int(new[3]) in cps else [int(new[0]), int(new[3])]
    V = _verts(obj)
    order = far if np.linalg.norm(V[free[0]] - V[far[0]]) < np.linalg.norm(V[free[0]] - V[far[3]]) else far[::-1]
    _set_pos(obj, free[0], V[order[0]])
    _set_pos(obj, free[1], V[order[3]])
    _select_verts(obj, [free[0], free[1], order[0], order[3]])
    yield "weld_before", ("The new patch's two free corners dragged onto the hole's far corners, and all four "
                          "corners selected: they are two pairs of points at one place, not one surface.")
    res, err = g.call(bpy.ops.bz.weld, mode="SELECTED")
    print("GUIDE C weld:", res, err, "open", len(_open_sides(obj)), "|", _report(obj))
    yield "welded", (f"Topology > Weld merges each pair -- and, because both sides were open, their handle pairs "
                     f"with them, so the seam really closes: {len(_open_sides(obj))} open sides left.")
    scene.bz_shade_mode = "TEXTURED"

    # ------------------------------------------------------------------ D. Subdivide
    obj = _imp(g)
    _clip(g)
    g.mode(obj, "EDIT")
    _sidebar(g)
    patches = store.patches(obj)
    V = _verts(obj)
    C = patches.reshape(-1, 16)[:, [0, 3, 12, 15]]
    limb = store.patch_limb(obj)
    names = store.limb_names(obj)
    leg = next((i for i, nm in enumerate(names) if nm.endswith("RRLeg")),
               next(i for i, nm in enumerate(names) if "Leg" in nm))
    rings = topoops._rings_for_plan(obj)
    tables = topoops._tables_of(obj)
    zm = V[C, 2].mean(1)
    # every torso strip of the cheetah crosses one of its 12 sockets, and a leg strip may too, so the row is
    # chosen by PLANNING the split (which changes nothing): the first leg patch whose propagation is clean
    rows = sorted((r for r in range(len(C)) if limb[r] == leg), key=lambda r: zm[r])
    clean = [r for r in rows if not topoops.subdivide_plan(V, patches, [r], tables, rings=rings).refusal]
    row = clean[0] if clean else rows[0]
    print("GUIDE D subdividable leg patches:", len(clean), "of", len(rows))
    _faces(g, obj, [row])
    _view_axis(g, "RIGHT")
    g.frame()
    _zoom(g, -2)
    scene.bz_shade_mode = "LIMB"
    p0 = int(obj[store.KEY_NPATCH])
    yield "subdiv_before", f"One patch of a rear leg selected ({names[leg]}), the surface shaded By Limb."
    _scroll(g, 11)
    res, err = g.call(bpy.ops.bz.subdivide)
    p1 = int(obj[store.KEY_NPATCH])
    print("GUIDE D subdivide:", res, err, p0, "->", p1, "|", _report(obj))
    yield "subdiv_after", (f"Topology > Subdivide splits it at the middle by de Casteljau -- the surface does not "
                           f"move -- and propagates the split along the strips, because the format cannot express "
                           f"a T-junction: {p0} -> {p1} patches.")
    scene.bz_shade_mode = "TEXTURED"

    # ------------------------------------------------------------------ E. Extrude Region
    obj = _imp(g)
    _clip(g)
    g.mode(obj, "EDIT")
    _sidebar(g)
    rows = _flank_rows(obj, 2)
    _faces(g, obj, rows)
    _view_axis(g, "RIGHT")
    g.frame()
    _zoom(g, -2)
    V = _verts(obj)
    size = float(np.linalg.norm(V.max(0) - V.min(0)))
    p0 = int(obj[store.KEY_NPATCH])
    yield "region_before", "Two patches of the flank selected, ready for Extrude Region."
    _scroll(g, 11)
    res, err = g.call(bpy.ops.bz.extrude_region, amount=0.06 * size, outline=0.7)
    p1 = int(obj[store.KEY_NPATCH])
    print("GUIDE E extrude region:", res, err, p0, "->", p1, "open", len(_open_sides(obj)), "|", _report(obj))
    yield "region", (f"Topology > Extrude Region, Amount 6 % of the creature's size and Outline 0.7: the region "
                     f"lifts along its mean normal, tapers, and a wall patch spans every boundary side "
                     f"({p0} -> {p1} patches, still watertight).")

    # ------------------------------------------------------------------ F. Mirror to Whole
    obj = _imp(g)
    patches = store.patches(obj)
    V = _verts(obj)
    size = float(np.linalg.norm(V.max(0) - V.min(0)))
    thr = 0.001 * size
    x = V[patches.reshape(-1, 16)][:, :, 0]
    left = [r for r in range(len(x)) if (x[r] < thr).all()]
    rings = topoops._rings_for_plan(obj)
    conns = store.connections(obj) or []
    kill = [i for i in range(len(conns)) if patchsel.ring_patch_refusals(patches, left, [rings[i]])]
    for i in sorted(kill, reverse=True):
        g.call(bpy.ops.bz.delete_connection, index=i)
    g.mode(obj, "EDIT")
    _sidebar(g)
    _faces(g, obj, left)
    g.call(bpy.ops.bz.delete_patch)
    _view_axis(g, "FRONT")
    g.orbit(-30)
    g.orbit(15, axis="ORBITUP")
    bpy.ops.mesh.select_all(action="DESELECT")
    g.frame(all=True)
    _zoom(g, 1)
    ph = int(obj[store.KEY_NPATCH])
    print("GUIDE F half:", ph, "patches,", len(kill), "sockets deleted")
    yield "half", (f"A half creature: the sockets whose rings reach into the half that goes deleted first, then "
                   f"its patches deleted as patches ({ph} left of 288).")
    _scroll(g, 11)
    res, err = g.call(bpy.ops.bz.mirror_whole)
    pw = int(obj[store.KEY_NPATCH])
    print("GUIDE F mirror:", res, err, ph, "->", pw, "open", len(_open_sides(obj)), "|", _report(obj))
    yield "mirrored", (f"Topology > Mirror to Whole: {ph} -> {pw} patches, the points on X = 0 shared by both "
                       f"halves (the seam is welded, not doubled), the mirrored points skinned to the _l / _r "
                       f"partner bones and their patches in the mirror limbs.")

    # ------------------------------------------------------------------ G. Attach
    obj = _imp(g)
    bpy.ops.bz.import_sgm(filepath=g.STOCK + "/rhinoceros.sgm", clips_as_actions=False)
    other = g.active()
    arm2 = store.rig(other)
    for o in (other, arm2):
        if o is not None:
            o.location = (0.0, 2.5, 0.0)          # Attach reads matrix_world, so an object transform is honoured
    bpy.context.view_layer.update()
    g.mode(obj, "OBJECT")
    _sidebar(g)
    other.select_set(True)                        # Attach's poll: the other creature selected, this one active
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    g.frame(all=True)
    p0 = int(obj[store.KEY_NPATCH])
    yield "attach_before", ("Two native creatures in one file: the cheetah (active) and a rhinoceros behind it, "
                            "both selected. Tools > Attach joins the second into the first.")
    _scroll(g, 13)
    limbs = store.limb_names(obj)
    bones = store.bone_rig_names(obj)
    ti = next(i for i, nm in enumerate(limbs) if nm.endswith("_Torso"))
    bi = bones.index("spine1") if "spine1" in bones else 0
    torso, bone = limbs[ti], bones[bi]
    res, err = g.call(bpy.ops.bz.attach, limb=str(ti), bone=str(bi))
    p1 = int(obj[store.KEY_NPATCH])
    print("GUIDE G attach:", res, err, p0, "->", p1, "|", _report(obj))
    g.frame(all=True)
    yield "attached", (f"Attach: the pools are concatenated and every per-patch table appended, so the cheetah is "
                       f"now {p1} patches ({p0} plus the rhinoceros'), the joined patches in {torso} and its "
                       f"points on {bone}. The other object, its rig and its 12 sockets are gone -- a socket "
                       f"belongs to the creature it is cut into. Weld the seams next.")
    print("GUIDE topology out dir:", os.path.isdir(g.OUT))
