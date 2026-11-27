"""Patch selection: one quad face per patch over corner slots 0, 12, 15, 3 (winding matches the surface normal), so
Blender's Face mode selects and hides patches; the visible surface stays the GPU overlay.

:func:`face_rows` says which patch has a face (none if two corners share a point). The mesh must hold exactly one quad
per patch, in patch order (:func:`face_error`); delete only via :class:`BZ_OT_delete_patch`, which remaps every table.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

import bpy
from bpy.app.handlers import persistent

from . import patchrules as PR
from . import store

FACE_SLOTS = (0, 12, 15, 3)          # the corner slots in the winding that agrees with the surface normal
PATCH_SIDES = ((0, 3), (3, 15), (15, 12), (12, 0))   # a patch's four sides as corner-slot pairs


# ------------------------------------------------------------------------------------------- pure: faces
def face_rows(patches) -> np.ndarray:
    """The patch rows that have a corner quad: those whose four corners are distinct pool points, in patch order
    (face i of the mesh is patch ``face_rows[i]``)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    if not len(P):
        return np.zeros(0, dtype=np.int64)
    c = P[:, list(FACE_SLOTS)]
    ok = ((c[:, 0] != c[:, 1]) & (c[:, 0] != c[:, 2]) & (c[:, 0] != c[:, 3]) & (c[:, 1] != c[:, 2])
          & (c[:, 1] != c[:, 3]) & (c[:, 2] != c[:, 3]))
    return np.nonzero(ok)[0]


def face_quads(patches):
    """``(rows, quads)``: the rows with a face and their corner quads ((k, 4) pool indices, mesh winding)."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    rows = face_rows(P)
    return rows, P[rows][:, list(FACE_SLOTS)]


def face_error_pure(faces, patches):
    """Why ``faces`` (a list of vertex-index tuples, mesh order) are not the corner quads of ``patches`` (None = they
    are): the count, or a face whose vertices are not its patch's corners."""
    rows, quads = face_quads(patches)
    if len(faces) == 0 and len(rows):
        return None                       # an object built before B6 (no corner quads at all): ensure_faces adds them
    if len(faces) != len(rows):
        n, k = len(faces), len(rows)
        what = (f"{k - n} corner quad{'s' if k - n != 1 else ''} deleted" if n < k
                else f"{n - k} face{'s' if n - k != 1 else ''} added")
        return (f"the mesh holds {n} corner quads for {k} patches ({what}): remove patches only with Delete Patch (X > "
                "Patches). Undo it (Ctrl+Z)")
    for i, (f, q) in enumerate(zip(faces, quads)):
        if len(f) != 4 or sorted(int(v) for v in f) != sorted(int(v) for v in q):
            return (f"face {i} of the mesh is not the corner quad of patch {int(rows[i])} (corners {[int(v) for v in q]}, "
                    f"face {[int(v) for v in f]}): a topology tool changed it; Undo it (Ctrl+Z) or re-import the creature")
    return None


# ------------------------------------------------------------------------------------------- pure: delete
@dataclass
class DeletePlan:
    """What Delete Patch does: ``rows`` (deleted, sorted), ``keep`` ((Np,) bool), ``drop`` (pool points used by no
    kept patch that a deleted patch used), ``o2n`` (old pool index -> new, -1 for a dropped point), ``n_new``;
    ``refusal``: why it must not run (a patch on a connection ring), else None."""
    rows: np.ndarray
    keep: np.ndarray
    drop: np.ndarray
    o2n: np.ndarray
    n_new: int
    refusal: object = None
    ring_patches: list = field(default_factory=list)   # (row, ring name, side corners) of every refused patch


def ring_patch_refusals(patches, rows, rings):
    """The patches among ``rows`` that lie on a connection ring: a patch one of whose sides joins two consecutive
    corners of a ring's corner loop (``rings``: ``[(name, corners, enc)]``, a verbatim ring (enc 0) counting every
    int it holds as a corner and any patch using one of them). ``[(row, ring name, (a, b))]``."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    out = []
    edges, points = {}, {}
    for name, ring, enc in rings:
        ring = [int(v) for v in ring]
        if enc and len(ring) >= 2:
            for a, b in zip(ring, ring[1:] + ring[:1]):
                edges.setdefault(frozenset((a, b)), name)
        else:
            for v in ring:
                points.setdefault(v, name)
    for r in sorted({int(x) for x in rows}):
        cps = P[r]
        hit = None
        for s0, s1 in PATCH_SIDES:
            a, b = int(cps[s0]), int(cps[s1])
            nm = edges.get(frozenset((a, b)))
            if nm is not None:
                hit = (nm, (a, b)); break
        if hit is None:
            for s in PR.CORNER_SLOTS:
                nm = points.get(int(cps[s]))
                if nm is not None:
                    hit = (nm, (int(cps[s]),)); break
        if hit is not None:
            out.append((r, hit[0], hit[1]))
    return out


def delete_plan(patches, rows, rings=(), n_points=None) -> DeletePlan:
    """Plan the deletion of patch ``rows``: which control points go (those used only by deleted patches; a point no
    patch used before stays, as in the file) and the index remap. ``rings`` as for :func:`ring_patch_refusals`."""
    P = np.asarray(patches, dtype=np.int64).reshape(-1, 16)
    n = int(P.max()) + 1 if len(P) else 0
    n = max(n, int(n_points) if n_points is not None else 0)
    rows = np.array(sorted({int(x) for x in rows if 0 <= int(x) < len(P)}), dtype=np.int64)
    keep = np.ones(len(P), dtype=bool)
    keep[rows] = False
    used_before = np.zeros(n, dtype=bool)
    used_after = np.zeros(n, dtype=bool)
    if len(P):
        used_before[P.reshape(-1)] = True
        if keep.any():
            used_after[P[keep].reshape(-1)] = True
    kept_points, o2n = PR.compaction(n, used_before, used_after)          # the one compaction rule
    plan = DeletePlan(rows, keep, np.nonzero(~kept_points)[0], o2n, int(kept_points.sum()))
    hits = ring_patch_refusals(P, rows, rings)
    if hits:
        plan.ring_patches = hits
        r, nm, side = hits[0]
        where = (f"its side {side[0]}-{side[1]} is a ring edge" if len(side) == 2
                 else f"its corner {side[0]} is a ring point")
        plan.refusal = (f"patch {r} is on connection ring '{nm}' ({where}): deleting it would break the socket the "
                        f"combiner grafts at" + (f" ({len(hits)} of the selected patches are on rings)" if len(hits) > 1
                                                  else "") + ". Delete the connection first (Boner > Connections), "
                        "or leave the ring's patches")
    return plan


def remap_ring(ring, o2n):
    """A ring's ints (a corner loop or verbatim ints) through the remap; raises when one was dropped."""
    out = []
    for v in ring:
        j = int(o2n[int(v)]) if 0 <= int(v) < len(o2n) else -1
        if j < 0:
            raise ValueError(f"ring point {int(v)} was deleted")
        out.append(j)
    return out


# ------------------------------------------------------------------------------------------- mesh readers
def _bm(obj):
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        return bm
    return None


def mesh_faces(obj) -> list:
    """The mesh's faces as vertex-index tuples, mesh order (the live bmesh in Edit Mode)."""
    bm = _bm(obj)
    if bm is not None:
        bm.verts.index_update()                 # Edit Mode leaves them stale (-1) after some changes
        return [tuple(v.index for v in f.verts) for f in bm.faces]
    me = obj.data
    return [tuple(p.vertices) for p in me.polygons]


def face_count(obj) -> int:
    bm = _bm(obj)
    return len(bm.faces) if bm is not None else len(obj.data.polygons)


def face_error(obj, patches=None):
    """The pool guard's face rule on a native object: None when the mesh holds exactly the corner quads of the patch
    table (see :func:`face_error_pure`; a mesh with no faces at all is a pre-B6 object, tolerated)."""
    patches = store.patches(obj) if patches is None else patches
    return face_error_pure(mesh_faces(obj), patches)


def ensure_faces(obj) -> int:
    """Give a native object built before B6 (a mesh with no faces) its corner quads; returns how many were added (0
    when it has faces, is in Edit Mode, or its pool is not the patch table's). Run for every creature of a loaded
    .blend (``load_post``)."""
    if not store.is_native(obj) or obj.mode == "EDIT" or len(obj.data.polygons):
        return 0
    patches = store.patches(obj)
    if not len(patches) or store.pool_error(obj):
        return 0
    me = obj.data
    quads = face_quads(patches)[1]
    quads = quads[quads.max(axis=1) < len(me.vertices)]
    n = len(quads)
    if not n:
        return 0
    # through the mesh arrays (what from_pydata does): a bmesh refuses a second face over the same four vertices
    # (a front / back fin), which the mesh itself holds
    me.loops.add(4 * n)
    me.polygons.add(n)
    me.loops.foreach_set("vertex_index", quads.reshape(-1).astype(np.int32))
    me.polygons.foreach_set("loop_start", np.arange(0, 4 * n, 4, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(n, 4, dtype=np.int32))
    me.update(calc_edges=True)
    return n


@persistent
def _on_load_post(*_args):
    for obj in bpy.data.objects:
        try:
            k = ensure_faces(obj)
        except Exception as e:                            # never silent
            print(f"[bz] {obj.name}: corner quads could not be added: {type(e).__name__}: {e}")
            continue
        if k:
            print(f"[bz] {obj.name}: {k} corner quads added (an object saved before patch selection)")


def register_handlers() -> None:
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister_handlers() -> None:
    lst = bpy.app.handlers.load_post
    for h in [h for h in lst if getattr(h, "__name__", "") == "_on_load_post" and getattr(h, "__module__", "") == __name__]:
        lst.remove(h)


def hidden_rows(obj, patches=None) -> np.ndarray:
    """(Np,) bool: the patches whose corner quad is hidden (H on faces); a patch without a face is never hidden."""
    patches = store.patches(obj) if patches is None else np.asarray(patches, dtype=np.int64)
    out = np.zeros(len(patches), dtype=bool)
    rows = face_rows(patches)
    bm = _bm(obj)
    if bm is not None:
        hid = np.fromiter((f.hide for f in bm.faces), dtype=bool, count=len(bm.faces))
    else:
        hid = np.zeros(len(obj.data.polygons), dtype=bool)
        obj.data.polygons.foreach_get("hide", hid)
    k = min(len(hid), len(rows))
    out[rows[:k]] = hid[:k]
    return out


def selected_rows(obj, patches=None) -> list:
    """The patches whose corner quad is selected (Face select, or the 4 corners selected in Vertex select, which
    Blender flushes to the face); Edit Mode only."""
    patches = store.patches(obj) if patches is None else np.asarray(patches, dtype=np.int64)
    bm = _bm(obj)
    if bm is None:
        return []
    rows = face_rows(patches)
    return [int(rows[i]) for i, f in enumerate(bm.faces) if i < len(rows) and f.select and not f.hide]


def select_rows(bm, patches, rows, extend=False) -> int:
    """Select the corner quads of patch ``rows`` in the live bmesh (their corners with them); returns how many."""
    bm.faces.ensure_lookup_table()
    frows = face_rows(patches)
    want = {int(r) for r in rows}
    if not extend:                       # deselect first: deselecting a face deselects its corners, shared ones too
        for f in bm.faces:
            f.select = False
    n = 0
    for i, f in enumerate(bm.faces):
        if i < len(frows) and int(frows[i]) in want and not f.hide:
            f.select = True
            n += 1
    return n


def _rows_from_selection(obj, patches):
    """Face selection first, then the 4-corner rule (every patch whose corners are all selected)."""
    rows = selected_rows(obj, patches)
    if rows:
        return rows
    from . import ops
    sel = set(ops._selected_indices(obj))
    corners = patches[:, list(PR.CORNER_SLOTS)]
    return [p for p in range(len(patches)) if all(int(c) in sel for c in corners[p])]


def add_face(bm, patch_cp, material=0) -> bool:
    """Append the corner quad of a new patch (Extrude Patch), in material slot ``material``; False for a degenerate patch
    (no face, by the rule)."""
    q = [int(patch_cp[s]) for s in FACE_SLOTS]
    if len(set(q)) < 4:
        return False
    bm.verts.ensure_lookup_table()
    bm.faces.new([bm.verts[i] for i in q]).material_index = int(material)
    return True


# ------------------------------------------------------------------------------------------- vertex types
def coplanar_flags(obj) -> np.ndarray:
    """(N,) bool: the corners of the Coplanar vertex type (default Corner: every stock handle is free)."""
    a = store.point_ints(obj, store.ATTR_COPLANAR)
    n = store.point_count(obj)
    if a is None or len(a) != n:
        return np.zeros(n, dtype=bool)
    return a != 0


# ------------------------------------------------------------------------------------------- operators
def _edit_verts(obj) -> np.ndarray:
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return np.array([v.co[:] for v in bm.verts], dtype=np.float64)


def _face_mode(context) -> bool:
    ts = context.tool_settings if context is not None else None
    return bool(ts is not None and ts.mesh_select_mode[2])


class BZ_OT_hide_patches(bpy.types.Operator):
    """Hide the selected patches, or all the others with Unselected"""
    bl_idname = "bz.hide_patches"
    bl_label = "Hide Patches"
    bl_options = {"REGISTER", "UNDO"}

    unselected: bpy.props.BoolProperty(name="Unselected", default=False,
                                       description="Hide the patches that are NOT selected (Shift+H)")

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        from . import draw
        obj = context.active_object
        patches = store.patches(obj)
        rows = set(_rows_from_selection(obj, patches))
        if self.unselected:
            rows = set(range(len(patches))) - rows
        if not rows:
            self.report({"WARNING"}, "Select the patches to hide (Face select, or all 4 corners)")
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        frows = face_rows(patches)
        for i, f in enumerate(bm.faces):
            if i < len(frows) and int(frows[i]) in rows:
                f.hide = True
                f.select = False
        hidden = hidden_rows(obj, patches)
        # a corner or handle every user of which is hidden goes with them
        users_hidden = np.ones(len(bm.verts), dtype=bool)
        users_hidden[patches[~hidden].reshape(-1)] = False
        used = np.zeros(len(bm.verts), dtype=bool)
        used[patches.reshape(-1)] = True
        for i in np.nonzero(users_hidden & used)[0]:
            v = bm.verts[int(i)]
            v.hide = True
            v.select = False
        for e in bm.edges:
            if e.verts[0].hide or e.verts[1].hide:
                e.hide = True; e.select = False
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Hidden {len(rows)} patch(es), {int(hidden.sum())} hidden now")
        return {"FINISHED"}


class BZ_OT_unhide_patches(bpy.types.Operator):
    """Show every hidden patch again"""
    bl_idname = "bz.unhide_patches"
    bl_label = "Unhide Patches"
    bl_options = {"REGISTER", "UNDO"}

    select: bpy.props.BoolProperty(name="Select", default=True, description="Select the revealed patches")

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object) and context.active_object.mode == "EDIT"

    def execute(self, context):
        import bmesh
        from . import draw, patchfeel
        obj = context.active_object
        patches = store.patches(obj)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        n_faces = 0
        for f in bm.faces:
            if f.hide:
                f.hide = False
                n_faces += 1
                if self.select:
                    f.select = True
        interior = PR.roles(patches, len(bm.verts)).interior if len(patches) else np.zeros(len(bm.verts), bool)
        n_pts = 0
        for v in bm.verts:
            if v.hide and not interior[v.index]:
                v.hide = False
                n_pts += 1
        for e in bm.edges:
            if e.hide and not (e.verts[0].hide or e.verts[1].hide):
                e.hide = False
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        patchfeel.apply_interior_visibility(obj, patches=patches)     # the interiors as the scene toggle says
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"Revealed {n_faces} patch(es) and {n_pts} control point(s)"
                              + ("" if patchfeel.interiors_shown() else "; interior points stay hidden (Display > Show "
                                                                         "Interior Points)"))
        return {"FINISHED"}


def _ring_rows_for_plan(obj):
    """``[(name, corners or ints, enc)]`` of the stored connection table (empty without one)."""
    try:
        from . import store as _s
        rows = _s._ring_rows(obj)
    except Exception:
        return []
    return [(name, ring, enc) for name, _bone, ring, enc in rows]


class BZ_OT_delete_patch(bpy.types.Operator):
    """Delete the selected patches and the points no other patch uses"""
    bl_idname = "bz.delete_patch"
    bl_label = "Delete Patch"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        from . import draw, ops, patchfeel
        obj = context.active_object
        err = store.pool_error(obj)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        patches = store.patches(obj)
        rows = _rows_from_selection(obj, patches)
        if not rows:
            self.report({"WARNING"}, "Select the patches to delete (Face select, or all 4 corners of each)")
            return {"CANCELLED"}
        if len(rows) >= len(patches):
            self.report({"ERROR"}, f"Delete Patch: every patch is selected ({len(rows)}); a creature keeps at least "
                                   "one patch. Deselect the ones to keep")
            return {"CANCELLED"}
        n0 = store.point_count(obj)
        plan = delete_plan(patches, rows, _ring_rows_for_plan(obj), n0)
        if plan.refusal:
            self.report({"ERROR"}, f"Delete Patch: {plan.refusal}")
            return {"CANCELLED"}
        # the connection rows first (begin_edit upgrades the object so every row has its name and classes)
        if not ops._begin(self, obj):
            return {"CANCELLED"}
        try:
            conn = store.conn_rows(obj)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        g = int(obj[store.KEY_GEN])
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table(); bm.faces.ensure_lookup_table()
        frows = face_rows(patches)
        gone = {int(r) for r in plan.rows}
        faces = [bm.faces[i] for i in range(len(bm.faces)) if i < len(frows) and int(frows[i]) in gone]
        edges = {e for f in faces for e in f.edges}
        drop = [bm.verts[int(i)] for i in plan.drop]      # the elements first: removals outdate the lookup tables
        for f in faces:                                   # the quads, then their edges no kept quad uses (never a
            bm.faces.remove(f)                            # vertex: the plan says which points go)
        for e in edges:
            if e.is_valid and not e.link_faces:
                bm.edges.remove(e)
        for v in drop:
            bm.verts.remove(v)
        bm.verts.ensure_lookup_table(); bm.verts.index_update()
        bm.faces.ensure_lookup_table(); bm.faces.index_update()
        n1 = len(bm.verts)
        if n1 != plan.n_new:
            self.report({"ERROR"}, f"Delete Patch: the mesh has {n1} points after the delete, the plan expected "
                                   f"{plan.n_new}; Undo (Ctrl+Z)")
            return {"CANCELLED"}
        store.set_point_ints(obj, store.ATTR_CP, np.arange(n1))
        store.set_point_ints(obj, store.ATTR_GEN, np.full(n1, g))
        bmesh.update_edit_mesh(obj.data)
        keep = plan.keep
        new_patches = plan.o2n[patches[keep]]
        # the per-patch tables are read against the OLD count (before the patch table and its count change)
        tables = [(setter, getter(obj)) for key, getter, setter in
                  ((store.KEY_PATCH_UV, store.patch_uv, store.set_patch_uv),
                   (store.KEY_PATCH_MAT, store.patch_mat, store.set_patch_mat),
                   (store.KEY_PATCH_TYPE, store.patch_type, store.set_patch_type),
                   (store.KEY_PATCH_LIMB, store.patch_limb, store.set_patch_limb))
                  if obj.get(key) is not None]
        manual = store.manual_interior(obj, len(patches)) if obj.get(store.KEY_MANUAL_INTERIOR) is not None else None
        store.set_patches(obj, new_patches)
        store.set_pool_n(obj, n1)
        for setter, arr in tables:
            if len(arr) == len(patches):
                setter(obj, arr[keep])
        if manual is not None:
            store.set_manual_interior(obj, manual[keep])
        if conn is not None:
            import dataclasses
            out = []
            for r in conn:
                if r.raw is not None:
                    out.append(dataclasses.replace(r, raw=remap_ring(r.raw, plan.o2n)))
                else:
                    out.append(dataclasses.replace(r, corners=remap_ring(r.corners or [], plan.o2n)))
            store.set_conn_rows(obj, out)
        # BMesh reuses the removed vertices' pool slots for the next vertices made (Extrude Patch), which would put
        # them in the MIDDLE of the iteration order and shift every index; leaving and re-entering Edit Mode rebuilds
        # the bmesh from the mesh arrays (the compacted order), with no free slots
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        patchfeel.apply_interior_visibility(obj)
        ops.forget_skin_stats(obj)
        draw.invalidate(obj.name)
        holes = None
        try:
            from . import core
            holes = core.open_side_count(new_patches)
        except Exception:
            pass
        self.report({"INFO"}, f"Deleted {len(plan.rows)} patch(es) and {len(plan.drop)} control point(s): "
                              f"{len(new_patches)} patches, {n1} points left"
                              + (f"; {holes} open side(s) now (Validate lists them; the game refuses an open mesh)"
                                 if holes else ""))
        return {"FINISHED"}


class BZ_OT_set_vertex_type(bpy.types.Operator):
    """Set the selected corners to Corner (free handles) or Coplanar (flat handles)"""
    bl_idname = "bz.set_vertex_type"
    bl_label = "Set Vertex Type"
    bl_options = {"REGISTER", "UNDO"}

    kind: bpy.props.EnumProperty(name="Type", items=[("CORNER", "Corner", "Free handles (the stock default)"),
                                                     ("COPLANAR", "Coplanar", "Handles kept in one plane")],
                                 default="COPLANAR")

    @classmethod
    def poll(cls, context):
        from . import ops
        return ops._guard_poll(cls, context, edit=True)

    def execute(self, context):
        import bmesh
        from . import core, draw, ops
        obj = context.active_object
        patches = store.patches(obj)
        n = store.point_count(obj)
        sel = ops.point_selection(obj) & core.corner_mask(patches, n)
        corners = np.nonzero(sel)[0]
        if not len(corners):
            self.report({"WARNING"}, "Select the corners whose type to set")
            return {"CANCELLED"}
        flags = coplanar_flags(obj)
        want = self.kind == "COPLANAR"
        flags[corners] = want
        store.set_point_ints(obj, store.ATTR_COPLANAR, flags.astype(np.int64))
        moved = np.zeros(0, dtype=np.int64)
        if want:
            V = _edit_verts(obj)
            out, moved = PR.project_coplanar(V, patches, corners)
            if len(moved):
                rows = np.nonzero(np.isin(patches, moved).any(axis=1))[0]
                manual = store.manual_interior(obj, len(patches))
                out, _rewritten = PR.reset_interiors(out, patches, rows[~manual[rows]])
                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                out32 = out.astype(np.float32)
                for i in np.nonzero(np.any(out != V, axis=1))[0]:
                    bm.verts[int(i)].co = out32[i]
        import bmesh as _bmesh
        _bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        draw.invalidate(obj.name)
        self.report({"INFO"}, f"{len(corners)} corner(s) set {'Coplanar' if want else 'Corner'}"
                              + (f", {len(moved)} handle(s) flattened into their plane" if len(moved) else ""))
        return {"FINISHED"}


class BZ_MT_delete(bpy.types.Menu):
    """X / Delete on a creature in Edit Mode"""
    bl_idname = "BZ_MT_delete_patch"
    bl_label = "Delete"

    def draw(self, context):
        lay = self.layout
        lay.operator_context = "INVOKE_DEFAULT"
        lay.operator(BZ_OT_delete_patch.bl_idname, text="Patches", icon="MESH_PLANE")
        lay.separator()
        lay.label(text="Blender's own delete (the pool guard refuses the export until Undo):")
        lay.operator("mesh.delete", text="Vertices").type = "VERT"
        lay.operator("mesh.delete", text="Edges").type = "EDGE"


class BZ_OT_delete_menu(bpy.types.Operator):
    """Delete patches on a creature, or Blender's vertex / edge delete"""
    bl_idname = "bz.delete_menu"
    bl_label = "Delete (creature)"

    @classmethod
    def poll(cls, context):
        return store.is_native(context.active_object) and context.active_object.mode == "EDIT"

    def invoke(self, context, event):
        bpy.ops.wm.call_menu(name=BZ_MT_delete.bl_idname)
        return {"FINISHED"}

    def execute(self, context):
        return self.invoke(context, None)


class BZ_OT_hide_key(bpy.types.Operator):
    """Hide patches with H in Face select; otherwise Blender's own hide"""
    bl_idname = "bz.hide_key"
    bl_label = "Hide Patches (H)"
    bl_options = {"REGISTER", "UNDO"}

    unselected: bpy.props.BoolProperty(default=False, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return store.is_native(obj) and obj.mode == "EDIT" and _face_mode(context)

    def execute(self, context):
        return bpy.ops.bz.hide_patches(unselected=self.unselected)


# ------------------------------------------------------------------------------------------- keymap
_KEYMAP_ITEMS: list = []
KEYS = (("bz.delete_menu", "X", {}), ("bz.delete_menu", "DEL", {}),
        ("bz.hide_key", "H", {}), ("bz.hide_key", "H", {"shift": True, "unselected": True}),
        ("bz.unhide_patches", "H", {"alt": True}))


def register_keymap() -> int:
    """Add-on keymap items in the Mesh keymap (handled before Blender's own; each operator's poll passes only on a
    native creature in Edit Mode, so the keys fall through everywhere else). Returns how many were added (0 in
    background mode, where there is no add-on keyconfig)."""
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon if wm is not None else None
    if kc is None:
        return 0
    km = kc.keymaps.new(name="Mesh", space_type="EMPTY", region_type="WINDOW")
    for idname, key, opts in KEYS:
        mods = {k: v for k, v in opts.items() if k in ("shift", "ctrl", "alt")}
        kmi = km.keymap_items.new(idname, key, "PRESS", **mods)
        for k, v in opts.items():
            if k not in mods:
                setattr(kmi.properties, k, v)
        _KEYMAP_ITEMS.append((km, kmi))
    return len(_KEYMAP_ITEMS)


def unregister_keymap() -> None:
    for km, kmi in _KEYMAP_ITEMS:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _KEYMAP_ITEMS.clear()


CLASSES = (BZ_OT_hide_patches, BZ_OT_unhide_patches, BZ_OT_delete_patch, BZ_OT_set_vertex_type, BZ_MT_delete,
           BZ_OT_delete_menu, BZ_OT_hide_key)
