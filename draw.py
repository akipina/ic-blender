"""Live gpu overlay of the exact bicubic surface, drawn from the current control points.

A single POST_VIEW handler walks every native object, reads its control points *as they are
right now* (object mode or live edit-mode bmesh), and draws the exact Bezier surface. It uses
the dirty-patch evaluator (fastsurf): only patches whose control points moved are re-evaluated,
so dragging a handle is sub-millisecond. In edit mode the surface is drawn translucent so the
control points behind it stay grabbable.

Optionally overlays the control points coloured by kind (on-surface corners vs off-surface
tangent handles). This is the WYSIWYG payoff: grab a handle, the real surface follows.
"""
from __future__ import annotations

import numpy as np

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import core
from . import fastsurf
from . import limbs
from . import patchfeel
from . import patchrules
from . import patchsel
from . import store
from . import texdraw

_handler = None
_state: dict = {}                       # obj.name -> per-object draw state
_TRI_SHADER = None
_PT_SHADER = None

# fixed key+fill lights (world space) for stable hemi-ish shading
_L1 = np.array([0.4, 0.3, 0.86]); _L1 /= np.linalg.norm(_L1)
_L2 = np.array([-0.5, -0.4, 0.3]); _L2 /= np.linalg.norm(_L2)
_BASE = np.array([0.55, 0.60, 0.72])

_CORNER_COLOR = (1.0, 0.85, 0.25, 1.0)   # on-surface points
_SELECT_RING = (1.0, 1.0, 1.0, 1.0)      # By Bone: the ring under a selected control point
_HANDLE_COLOR = (0.30, 0.65, 1.0, 1.0)   # off-surface tangent/interior handles


def _tri_shader():
    global _TRI_SHADER
    if _TRI_SHADER is None:
        _TRI_SHADER = gpu.shader.from_builtin("SMOOTH_COLOR")
    return _TRI_SHADER


def _pt_shader():
    global _PT_SHADER
    if _PT_SHADER is None:
        _PT_SHADER = gpu.shader.from_builtin("FLAT_COLOR")
    return _PT_SHADER


def _current_verts(obj) -> np.ndarray:
    """Control-point positions to tessellate from. Edit mode = the rest control net (bmesh);
    object mode = the EVALUATED mesh, so armature posing/animation deforms the surface live."""
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        return np.array([bm.verts[i].co[:] for i in range(len(bm.verts))], dtype=np.float64)
    me = obj.data
    if obj.visible_get():
        try:                                                 # posed/animated positions
            ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
            # a modifier that CHANGES the geometry (D9's evaluated surface) is not the pool: keep the
            # control points, or the patch table would index someone else's vertices
            me = ev if len(ev.vertices) == len(obj.data.vertices) else obj.data
            if str(_notes.get(obj.name, "")).startswith("posed surface"):
                _notes.pop(obj.name, None)
        except Exception as e:
            _note(obj.name, f"posed surface unavailable (rest pose shown): {type(e).__name__}: {e}")
            me = obj.data
    n = len(me.vertices)
    arr = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", arr)
    return arr.reshape(n, 3)


def _shade(normals: np.ndarray, alpha: float) -> np.ndarray:
    ndl = np.clip(normals @ _L1, 0, 1) * 0.7 + np.clip(normals @ _L2, 0, 1) * 0.3
    s = 0.22 + 0.78 * ndl
    col = np.empty((len(normals), 4), dtype=np.float32)
    col[:, :3] = (_BASE[None, :] * s[:, None]).astype(np.float32)
    col[:, 3] = alpha
    return col


def _rest_verts(obj, verts):
    """The un-posed control points (the base mesh): the game splits cells from the bind pose, so a
    posed object keeps the split of its rest shape. None when the object is not posed."""
    me = obj.data
    if len(me.vertices) != len(verts) or not obj.modifiers:
        return None
    arr = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", arr)
    return arr.reshape(-1, 3)


_GREY = (0.5, 0.5, 0.52, 0.25)


def _vis_tris(st) -> np.ndarray:
    """The surface triangles of the patches that are not hidden (H on a patch's corner quad: ``st["hidden"]``, a
    (Np,) mask read every redraw)."""
    tris = st["topo"].tris
    hid = st.get("hidden")
    if hid is None or not hid.any() or len(hid) != st["topo"].Np:
        return tris
    per = len(tris) // max(st["topo"].Np, 1)
    return tris.reshape(st["topo"].Np, per, 3)[~hid].reshape(-1, 3)


def _apply_hidden(obj, st, patches) -> None:
    """Read the per-patch hidden mask off the corner quads; a change drops every index batch (not the surface)."""
    hid = patchsel.hidden_rows(obj, patches)
    old = st.get("hidden")
    if old is None or len(old) != len(hid) or not np.array_equal(old, hid):
        st["hidden"] = hid
        st["batch"] = None
        st["bone_batch"] = None
        st.pop("grey_batch", None)
        st["net_edges"] = np.asarray(core._patch_grid_edges(patches[~hid] if hid.any() else patches), dtype=np.int32)


def _sync(obj, density: int, alpha: float):
    """Ensure the cached batch matches the current control points; returns the state dict.

    Before evaluating, the pool guard runs whenever the points changed (a Delete / Merge / Sort shows up as a count
    or position change): while the mesh is not the pool the patch table indexes, nothing is evaluated -- the last
    good surface stays, drawn flat grey, and ``st["pool_error"]`` holds the reason (drawn as text)."""
    verts = _current_verts(obj)
    st = _state.get(obj.name)
    patches = store.patches(obj)
    key = (obj.get(store.KEY_GEN), len(patches), obj.get(store.KEY_POOL_N))

    rebuild_all = (
        st is None
        or st.get("topo") is None
        or st["density"] != density
        or st["nverts"] != len(verts)
        or st["topo"].Np != len(patches)
        or st.get("key") != key
    )
    changed = None
    if not rebuild_all:
        changed = np.any(st["prev"] != verts, axis=1)
    if rebuild_all or changed.any() or (st is not None and st.get("pool_error")):
        # the full guard on a rebuild; while points move, only the per-point ids (one pass): the tables change only
        # through SGM operators and Undo, which invalidate this state
        err = store.pool_error(obj) if (rebuild_all or st.get("pool_error")) else store.pool_ids_error(obj)
        if err:
            if st is None:
                st = {"topo": None, "batch": None, "surfV": np.zeros((0, 3)), "tex_batches": None, "limb_batch": None,
                      "density": density, "nverts": -1, "prev": verts, "alpha": None, "key": None}
                _state[obj.name] = st
            st["pool_error"] = err
            st["cur"] = verts
            return st
    if st is not None:
        st.pop("pool_error", None)
        st.pop("grey_batch", None)
    if not rebuild_all and changed.any() and obj.mode == "EDIT":
        # the Max feel (patchfeel.settle): corners that moved since the last redraw drag their handles and the touched
        # patches' interiors follow, written into the live bmesh BEFORE this redraw's tessellation; the diff below
        # then covers the user's moves and the rule's together (an Undo restoring the mesh writes nothing)
        settled = patchfeel.settle(obj, st["prev"], verts, patches)
        if settled is not None:
            verts = settled
            changed = np.any(st["prev"] != verts, axis=1)
    if rebuild_all:
        topo = fastsurf.TopoCache(patches, density, store.patch_type(obj))
        surfV, surfN = topo.eval_all(verts, rest_verts=_rest_verts(obj, verts))
        st = {"topo": topo, "density": density, "nverts": len(verts), "key": key,
              "prev": verts.copy(), "cur": verts, "surfV": surfV, "surfN": surfN,
              "alpha": None, "batch": None, "tex_batches": None, "limb_batch": None,
              "net_edges": np.asarray(core._patch_grid_edges(patches), dtype=np.int32)}
        _state[obj.name] = st
    else:
        st["cur"] = verts
        if changed.any():
            dirty = st["topo"].patches_for_dirty_verts(np.nonzero(changed)[0])
            st["topo"].eval_dirty(verts, st["surfV"], st["surfN"], dirty, rest_verts=_rest_verts(obj, verts))
            st["prev"] = verts.copy()
            st["batch"] = None
        if st["alpha"] != alpha:
            st["batch"] = None
    _apply_hidden(obj, st, patches)

    if st["batch"] is None and len(st["surfV"]):
        st["alpha"] = alpha
        tris = _vis_tris(st)
        st["batch"] = batch_for_shader(
            _tri_shader(), "TRIS",
            {"pos": st["surfV"].astype(np.float32), "color": _shade(st["surfN"], alpha)},
            indices=tris.astype(np.int32),
        )
        # textured batches (real skin); a failure falls back to the gray batch above, and is said once
        st["tex_batches"] = None
        try:
            vbuf, dl = texdraw.build_drawlist(obj, st["surfV"], st["surfN"],
                                              tris, density, alpha)
            if dl:
                st["tex_batches"] = texdraw.make_batches(vbuf, dl)
            if str(_notes.get(obj.name, "")).startswith("textured surface"):
                _notes.pop(obj.name, None)                   # recovered
        except Exception as e:
            st["tex_batches"] = None
            _note(obj.name, f"textured surface unavailable (grey shown): {type(e).__name__}: {e}")
        # by-limb colouring (by-bone colouring is built on demand: it follows the active bone, see _bone_batches)
        st["limb_batch"] = None
        st["bone_batch"] = None
        pl = store.patch_limb(obj)
        if len(pl):
            base = limbs.vertex_limb_colors(pl, len(store.limb_names(obj)), density, len(st["surfV"]))
            col = limbs.lit_colors(base, st["surfN"], alpha)
            st["limb_batch"] = batch_for_shader(_tri_shader(), "TRIS",
                                                {"pos": st["surfV"].astype(np.float32), "color": col},
                                                indices=tris.astype(np.int32))
    return st


def active_bone(obj, scene=None) -> int:
    """The Boner panel's active bone as this creature's source bone index (-1: none, or not a bone of its rig)."""
    scene = scene if scene is not None else bpy.context.scene
    s = getattr(scene, "bz_boner", None)
    return store.bone_index(obj, s.bone) if s is not None and s.bone else -1


def live_owners(obj) -> np.ndarray:
    """The owners By Bone shows: the live skin (``store.live_skin``: the owner table read against the bone vertex
    groups, so a Ctrl+G or weight-paint edit shows as Export will write it); the table alone when the groups can't be
    read against it (the pool guard reports that)."""
    try:
        return store.live_skin(obj).owners
    except ValueError:
        return store.vert_bone(obj)


def bone_colors(obj, st, density, active, alpha, owners=None):
    """The By Bone colour arrays for a synced draw state, bpy-light and GPU-free (tests call it headless):
    ``(surface RGBA (M, 4), control-point RGBA (N, 4), owners)`` or None when the owner table does not match the
    points (the pool guard reports that). ``owners``: default :func:`live_owners`."""
    owners = live_owners(obj) if owners is None else np.asarray(owners, dtype=np.int64)
    n_bones = store.owner_count(obj)
    if len(owners) != len(st["cur"]) or st.get("topo") is None:
        return None
    base = limbs.vertex_bone_colors(owners, st["topo"].patches, density, n_bones, active)
    if len(base) != len(st["surfV"]):
        return None
    return limbs.lit_colors(base, st["surfN"], alpha), limbs.point_bone_colors(owners, n_bones, active), owners


def bone_points_shown(scene=None) -> bool:
    """By Bone draws the control points in bone colours unless the Boner panel's Points toggle is off."""
    scene = scene if scene is not None else bpy.context.scene
    s = getattr(scene, "bz_boner", None)
    return bool(getattr(s, "show_points", True)) if s is not None else True


def selected_points(obj):
    """(N,) bool: the selected control points of the live edit bmesh, or None outside Edit Mode."""
    if obj.mode != "EDIT":
        return None
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    return np.fromiter((v.select for v in bm.verts), dtype=bool, count=len(bm.verts))


def _bone_batches(obj, st, density, alpha):
    """(surface batch or None, control-point colours or None) for By Bone, rebuilt when the owners, the active bone or
    the surface change."""
    active = active_bone(obj)
    owners = live_owners(obj)
    key = (active, alpha, len(owners), hash(owners.tobytes()))
    if st.get("bone_batch") is None or st.get("bone_key") != key:
        cols = bone_colors(obj, st, density, active, alpha, owners)
        st["bone_key"] = key
        if cols is None:
            st["bone_batch"], st["bone_pt_colors"] = None, None
        else:
            st["bone_batch"] = batch_for_shader(_tri_shader(), "TRIS", {"pos": st["surfV"].astype(np.float32),
                                                "color": cols[0]}, indices=_vis_tris(st).astype(np.int32))
            st["bone_pt_colors"] = cols[1]
    return st.get("bone_batch"), st.get("bone_pt_colors")


_errors: dict = {}                      # obj.name -> why its overlay could not be drawn (shown as text)
_notes: dict = {}                       # obj.name -> non-fatal overlay note (shown as text)


def _note(name, msg):
    _notes[name] = msg


def overlay_error(name):
    """The overlay's current problem for an object (pool guard or a draw failure), or None (for tests / the panel)."""
    st = _state.get(name)
    return (st or {}).get("pool_error") or _errors.get(name)


def _point_batch(obj, verts):
    """Per-vertex points coloured corner (on-surface) vs handle (off-surface). In Edit Mode the handle dots are
    drawn only for the handles of SELECTED corners (Max shows a vertex's vectors when it is selected); hidden
    interior points get no dot."""
    patches = store.patches(obj)
    mask = core.corner_mask(patches, len(verts))
    cols = np.empty((len(verts), 4), dtype=np.float32)
    cols[mask] = _CORNER_COLOR
    cols[~mask] = _HANDLE_COLOR
    keep = np.ones(len(verts), dtype=bool)
    if obj.mode == "EDIT":
        sel = selected_points(obj)
        if sel is not None and len(sel) == len(verts):
            keep = mask | patchrules.handles_of_selected(sel, patches, len(verts))
        hid = patchfeel.hidden_points(obj)
        if len(hid) == len(verts):
            keep &= ~hid
    hp = patchsel.hidden_rows(obj, patches)
    if hp.any() and len(patches):                         # a point every patch of which is hidden gets no dot
        shown = np.zeros(len(verts), dtype=bool)
        shown[patches[~hp].reshape(-1)] = True
        used = np.zeros(len(verts), dtype=bool)
        used[patches.reshape(-1)] = True
        keep &= shown | ~used
    return batch_for_shader(_pt_shader(), "POINTS",
                            {"pos": verts[keep].astype(np.float32), "color": cols[keep]})


_CONN_COLOR = (0.1, 1.0, 0.9, 1.0)   # graft rings
_NET_COLOR = (0.45, 0.45, 0.52, 1.0)  # control net


def _draw_controlnet(st, verts):
    """Draw the 4x4 control net as gpu lines (toggleable, since the mesh has no edges)."""
    edges = st.get("net_edges")
    if edges is None or len(edges) == 0:
        return
    gpu.state.depth_test_set("LESS_EQUAL")
    ps = _pt_shader()
    batch = batch_for_shader(ps, "LINES", {"pos": verts.astype(np.float32),
                             "color": [_NET_COLOR] * len(verts)}, indices=edges)
    ps.bind(); batch.draw(ps)
    gpu.state.depth_test_set("NONE")


_RING_LEVEL_COLOR = {"OK": (0.2, 1.0, 0.35, 1.0), "WARN": (1.0, 0.6, 0.1, 1.0), "ERROR": (1.0, 0.22, 0.18, 1.0)}
_TICK_COLOR = (1.0, 1.0, 1.0, 1.0)


def _draw_connections(obj, verts):
    """Each connection's graft ring: a closed loop through its corners coloured by its status (green OK, orange WARN:
    start off the stock start or not checked, red ERROR: size, not along patch sides, wound out of the torso, a
    verbatim non-standard ring; cyan when it can't be judged), its start corner as a larger point and a direction tick
    from corner 0 toward corner 1. In Boner > Connections the active connection's ring is drawn thick and the others
    dimmed."""
    conns = store.connections(obj)
    if not conns:
        return
    from . import ops, combocore
    status = ops.ring_status_cached(obj, bpy.context)
    levels = [getattr(x, "level", None) for x in status] if len(status) == len(conns) else [None] * len(conns)
    if status and len(status) != len(conns):
        _note(obj.name, f"ring status unavailable: {status[0] if isinstance(status[0], str) else len(status)}")
    elif not status and not combocore.available():
        _note(obj.name, f"ring status unavailable (rings drawn cyan): the creature combiner could not be loaded "
                        f"({combocore.IMPORT_ERROR})")
    elif str(_notes.get(obj.name, "")).startswith("ring status unavailable"):
        _notes.pop(obj.name, None)                            # recovered
    gpu.state.depth_test_set("NONE")
    ps = _pt_shader()
    bs = getattr(bpy.context.scene, "bz_boner", None)
    active = int(bs.active_conn) if bs is not None and bs.mode == "CONNECTIONS" else -1   # Boner > Connections: bright
    for ci, ((_name, _bone, idx), level) in enumerate(zip(conns, levels)):
        ring = [i for i in dict.fromkeys(idx) if 0 <= i < len(verts)]
        if not ring:
            continue
        col = _RING_LEVEL_COLOR.get(level, _CONN_COLOR)
        if active >= 0 and ci != active:
            col = (col[0] * 0.55, col[1] * 0.55, col[2] * 0.55, col[3] * 0.6)     # the others dimmed
        pos = verts[ring].astype(np.float32)
        loop = np.concatenate([pos, pos[:1]], axis=0)        # close the ring
        gpu.state.line_width_set(4.0 if ci == active else 2.0)
        lb = batch_for_shader(ps, "LINE_STRIP", {"pos": loop, "color": [col] * len(loop)})
        ps.bind(); lb.draw(ps)
        gpu.state.point_size_set(7.0)
        pb = batch_for_shader(ps, "POINTS", {"pos": pos, "color": [col] * len(pos)})
        ps.bind(); pb.draw(ps)
        gpu.state.point_size_set(13.0)                        # the start corner
        sb = batch_for_shader(ps, "POINTS", {"pos": pos[:1], "color": [col]})
        ps.bind(); sb.draw(ps)
        if len(pos) > 1:                                      # direction: corner 0 -> corner 1
            tip = pos[0] + 0.35 * (pos[1] - pos[0])
            gpu.state.line_width_set(4.0)
            tb = batch_for_shader(ps, "LINES", {"pos": np.array([pos[0], tip], np.float32),
                                                "color": [_TICK_COLOR, _TICK_COLOR]})
            ps.bind(); tb.draw(ps)
    gpu.state.line_width_set(1.0)


def _draw():
    scene = bpy.context.scene
    if scene is None:
        return
    opts = dict(density=int(getattr(scene, "bz_density", 4)),
                show_surface=getattr(scene, "bz_show_surface", True),
                show_handles=getattr(scene, "bz_show_handles", False),
                show_conn=getattr(scene, "bz_show_connections", False),
                show_net=getattr(scene, "bz_show_controlnet", True),
                mode=getattr(scene, "bz_shade_mode", "TEXTURED"),
                edit_alpha=float(getattr(scene, "bz_edit_opacity", 0.6)))

    for obj in bpy.context.view_layer.objects:
        if not store.is_native(obj):
            continue
        # every object draws in its own guarded block: a failure is recorded (drawn as text by _draw_text), the gpu
        # matrix stack and state are always restored, and the other creatures still draw
        gpu.matrix.push()
        try:
            from . import gnsurface          # D9: with the evaluated surface on, the overlay would z-fight it
            _draw_object(obj, **dict(opts, show_surface=opts["show_surface"] and not gnsurface.is_enabled(obj)))
        except Exception as e:
            _errors[obj.name] = f"{type(e).__name__}: {e}"
        finally:
            gpu.matrix.pop()
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("NONE")
            gpu.state.line_width_set(1.0)


def _draw_object(obj, density, show_surface, show_handles, show_conn, show_net, mode, edit_alpha):
    # The surface draws even when the editing mesh is hidden (so "Surface Only" shows just the
    # creature); the control net / handles / connections only draw when the mesh is visible.
    tri_shader = _tri_shader()
    visible = obj.visible_get()
    is_edit = obj.mode == "EDIT"
    alpha = edit_alpha if is_edit else 1.0
    st = _sync(obj, density, alpha)
    _errors.pop(obj.name, None)
    gpu.matrix.multiply_matrix(obj.matrix_world)

    if st.get("pool_error"):
        # the patch table indexes a pool the mesh no longer is: the last good surface, flat grey, and the points
        if show_surface and st.get("topo") is not None and len(st["surfV"]):
            if st.get("grey_batch") is None:
                col = np.tile(np.array(_GREY, dtype=np.float32), (len(st["surfV"]), 1))
                st["grey_batch"] = batch_for_shader(_tri_shader(), "TRIS", {"pos": st["surfV"].astype(np.float32),
                                                    "color": col}, indices=_vis_tris(st).astype(np.int32))
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            tri_shader.bind(); st["grey_batch"].draw(tri_shader)
            gpu.state.blend_set("NONE")
        if visible and len(st["cur"]):
            gpu.state.depth_test_set("NONE")
            gpu.state.point_size_set(5.0)
            ps = _pt_shader()
            pb = batch_for_shader(ps, "POINTS", {"pos": st["cur"].astype(np.float32),
                                                 "color": np.tile(np.array(_HANDLE_COLOR, np.float32), (len(st["cur"]), 1))})
            ps.bind(); pb.draw(ps)
        return

    if show_surface and st["batch"] is not None:
        # depth state is set per-draw so object count / the handle pass can't leave it dirty;
        # translucent in edit mode (no depth write) so control points behind stay visible.
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(not is_edit)
        gpu.state.blend_set("ALPHA" if is_edit else "NONE")
        gpu.state.face_culling_set("NONE")
        if mode == "TEXTURED" and st.get("tex_batches"):
            mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
            try:
                texdraw.draw_batches(st["tex_batches"], mvp)
                if str(_notes.get(obj.name, "")).startswith("textured surface"):
                    _notes.pop(obj.name, None)                   # recovered
            except Exception as e:
                _note(obj.name, f"textured surface could not draw (grey shown): {type(e).__name__}: {e}")
                tri_shader.bind(); st["batch"].draw(tri_shader)   # fallback to gray
        elif mode == "LIMB" and st.get("limb_batch"):
            tri_shader.bind(); st["limb_batch"].draw(tri_shader)
        elif mode == "BONE" and _bone_batches(obj, st, density, alpha)[0] is not None:
            tri_shader.bind(); st["bone_batch"].draw(tri_shader)
        else:
            tri_shader.bind(); st["batch"].draw(tri_shader)
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set("NONE")
        gpu.state.depth_test_set("NONE")

    if mode == "BONE" and visible and len(st["cur"]) and bone_points_shown():
        # the control points in their bone's colour (orange: no bone), over the handle dots; in Edit Mode a selected
        # point gets a white ring, so "selected" never reads as the unassigned orange (Blender's selection colour)
        pcols = _bone_batches(obj, st, density, alpha)[1]
        if pcols is not None and len(pcols) == len(st["cur"]):
            gpu.state.depth_test_set("NONE")
            ps = _pt_shader()
            pos = st["cur"].astype(np.float32)
            sel = selected_points(obj) if is_edit else None
            if sel is not None and len(sel) == len(pos) and sel.any():
                gpu.state.point_size_set(10.0)
                rb = batch_for_shader(ps, "POINTS", {"pos": pos[sel], "color": np.tile(np.array(_SELECT_RING,
                                                     np.float32), (int(sel.sum()), 1))})
                ps.bind(); rb.draw(ps)
            gpu.state.point_size_set(6.0 if is_edit else 4.0)
            pb = batch_for_shader(ps, "POINTS", {"pos": pos, "color": pcols})
            ps.bind(); pb.draw(ps)
    elif show_handles and visible:
        gpu.state.depth_test_set("NONE")
        gpu.state.point_size_set(6.0)
        pb = _point_batch(obj, st["cur"])
        ps = _pt_shader(); ps.bind(); pb.draw(ps)

    if show_net and visible:
        _draw_controlnet(st, st["prev"])

    if show_conn and visible:
        _draw_connections(obj, st["prev"])


def _draw_text():
    """POST_PIXEL: the reason an overlay is greyed out or missing, per object, in the viewport's corner."""
    import blf
    ctx = bpy.context
    region = getattr(ctx, "region", None)
    if region is None or ctx.view_layer is None:
        return
    lines = []
    for obj in ctx.view_layer.objects:
        if not store.is_native(obj):
            continue
        err = overlay_error(obj.name)
        if err:
            lines.append((f"{obj.name}: {err}", (1.0, 0.45, 0.35, 1.0)))
        elif _notes.get(obj.name):
            lines.append((f"{obj.name}: {_notes[obj.name]}", (1.0, 0.85, 0.4, 1.0)))
    from . import guards                  # D10: the export refusals the state already earns (cached; nothing is run here)
    lines += guards.text_lines()
    if not lines:
        return
    font = 0
    blf.size(font, 13)
    y = region.height - 60
    width = max(200, region.width - 40)
    for text, col in lines:
        blf.color(font, *col)
        # wrap by characters (the messages are long sentences)
        per = max(40, int(width / 7.5))
        for k in range(0, len(text), per):
            blf.position(font, 20, y, 0)
            blf.draw(font, text[k:k + per])
            y -= 18
        y -= 6


_text_handler = None


def invalidate(name: str | None = None):
    patchfeel.forget(name)                  # the rule's anchors go with the draw state (Undo, operators)
    if name is None:
        _state.clear()
        _errors.clear()
        _notes.clear()
    else:
        _state.pop(name, None)
        _errors.pop(name, None)
        _notes.pop(name, None)


def register():
    global _handler, _text_handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")
    if _text_handler is None:
        _text_handler = bpy.types.SpaceView3D.draw_handler_add(_draw_text, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handler, _text_handler, _TRI_SHADER, _PT_SHADER
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        _handler = None
    if _text_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_text_handler, "WINDOW")
        _text_handler = None
    _state.clear()
    _errors.clear()
    _notes.clear()
    _TRI_SHADER = _PT_SHADER = None
    texdraw.invalidate()
