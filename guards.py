"""Action-time refusals: what Export would refuse, shown the moment the edit happens (D10 Half A).

Blender lets a modder do things the `.sgm` cannot hold -- move the object, add a Subdivision Surface, add shape keys,
delete a control point, blend a vertex weight, replace the file the creature was imported from. Every one of those was
already refused, but only at **Export**, which can be a long session later. This module runs the same refusals as a
READ of the object's state and shows them where the edit happened.

Three rules, in this order:

1. **Never fight the edit.** Nothing here writes, fixes, undoes or cancels anything. It reads, and it says what it
   read. The Export refusal stays exactly as it was and is still the backstop -- every message below IS the export's
   own message, produced by the export's own function (`store.pool_error`, `store.source_error`,
   `ops.object_state_error`, `ops.uv_cage_error`, `rig.skeleton_error`, `core.skin_group_state`), never a second
   wording that could drift from it.
2. **Off the hot path.** :func:`state_key` is a cheap tuple of what the checks actually depend on (counts, mode,
   generation, whether the transforms are identity, the modifier stack, the source file's stat, the rig's bone key).
   The depsgraph handler computes only that; the checks run only when it changes, so a vertex drag in Edit Mode --
   where nothing in the key moves -- costs one tuple per update. The B5/B6 caches are the pattern.
3. **One place to look.** :func:`issues` is the whole list; the SGM panel draws it as the Problems box and the
   viewport overlay draws it as red text, so a mistake shows with the sidebar closed too.

What is NOT here, and why: the Actions plan (`actions.plan_export`) hashes every Action, and the added-bone /
stub-bone owner checks need the export's bone insert -- both too dear to run per update, so they stay Export-time
(the Bones tab already shows the added-bone refusals from its own cached read-out).
"""
from __future__ import annotations

import os
import time
from collections import namedtuple

import bpy
from bpy.app.handlers import persistent

from . import store

#: one thing the export would refuse. ``code`` groups them (the panel hides a code the rig box already draws).
Issue = namedtuple("Issue", "code level text")

# ------------------------------------------------------------------------------------------- the state key
_MISSING = "missing"


def _source_stat(obj):
    """(mtime, size) of the creature's base .sgm, ``"missing"`` when it is gone, None when none is recorded."""
    src = store.source_path(obj)
    if not src:
        return None
    try:
        st = os.stat(src)
    except OSError:
        return _MISSING
    return (st.st_mtime_ns, st.st_size)


_BONES: dict = {}                    # arm.name -> rig.bones_key(arm), refreshed only when the armature was updated


def bones_key(arm, fresh: bool = False) -> tuple:
    """``rig.bones_key`` memoised per armature. It is the dearest part of :func:`state_key` (92 us on cheetah's 54
    bones: 7 ID-property reads each), and a bone can only change when the armature itself was updated -- so the
    handler refreshes it for the armatures in the depsgraph's update list and reads the cache otherwise."""
    from . import rig
    if fresh or arm.name not in _BONES:
        _BONES[arm.name] = rig.bones_key(arm)
    return _BONES[arm.name]


def gen_probe(obj):
    """The generation stamp of the FIRST control point -- a cheap stand-in for ``store.mesh_generation``, which reads
    the whole pool (24 us through the mesh attribute, but ~400 us through the edit bmesh, one python step per point:
    that alone made an Edit-Mode drag four times dearer). Undo / Redo restore the whole attribute array at once, and
    every SGM operator stamps every point, so the first point moves with the rest."""
    import bmesh
    if obj.mode == "EDIT" and obj.data.is_editmode:
        bm = bmesh.from_edit_mesh(obj.data)
        lay = bm.verts.layers.int.get(store.ATTR_GEN)
        if lay is None or not len(bm.verts):
            return None
        bm.verts.ensure_lookup_table()
        return int(bm.verts[0][lay])
    at = obj.data.attributes.get(store.ATTR_GEN)
    if at is None or at.domain != "POINT" or at.data_type != "INT" or not len(at.data):
        return None
    return int(at.data[0].value)


def state_key(obj, fresh_bones: bool = False) -> tuple:
    """Everything :func:`issues` depends on, as a cheap tuple -- no numpy pass over the pool, no file read.

    The transforms enter as the BOOLEAN ``is identity`` (mathutils' own, not ``ops._is_identity``'s numpy compare:
    exact instead of 1e-9, which can only make the key change too often, never too seldom), not as 32 floats, so an
    Object-Mode drag changes the key once -- on the first step that leaves the origin -- instead of on every step."""
    me = obj.data
    arm = store.rig(obj)
    return (len(me.vertices), len(me.polygons), obj.mode,
            gen_probe(obj), obj.get(store.KEY_GEN), int(obj.get(store.KEY_NPATCH, 0) or 0),
            obj.matrix_world.is_identity,
            0 if me.shape_keys is None else len(me.shape_keys.key_blocks),
            tuple((m.type, m.name, getattr(getattr(m, "object", None), "name", ""),
                   getattr(m, "use_vertex_groups", None), getattr(m, "use_bone_envelopes", None))
                  for m in obj.modifiers),
            _source_stat(obj),
            None if arm is None else (arm.name, arm.matrix_world.is_identity, bones_key(arm, fresh_bones)),
            str(obj.get("bz_uv_cage") or ""))


# ------------------------------------------------------------------------------------------- the checks
def _guarded(code, fn, obj):
    """``fn(obj)`` as an Issue, with a failure of the check itself reported rather than swallowed."""
    try:
        msg = fn(obj)
    except Exception as e:                       # a check must never break a panel draw or a depsgraph handler
        return Issue(code, "ERROR", f"the {code} check failed: {type(e).__name__}: {e}")
    return None if not msg else Issue(code, "ERROR", str(msg))


def _skin_issue(obj):
    """Control points whose vertex groups hold a weight the owner table cannot say: **blended or partial** weights
    (weight paint, two groups at 0.5). Export refuses them (``core.skin_owners``) and so does every Boner skin edit,
    and nothing about the skeleton can excuse them -- so they can be judged here, from the groups alone.

    A point in NO bone group is deliberately left out: a bone deleted on the rig leaves its points in a group no bone
    deforms, and that is a legitimate C8 edit the export resolves (the points go to the bone's parent). Calling it a
    weight edit here would be a false alarm, so it stays with the export (which knows the deleted bones) and with
    Boner's own ``Unassigned: N (M on patches)`` read-out. The pass walks the deform layer, so only ``deep`` runs it.
    """
    from . import core
    try:
        weights = store.skin_group_weights(obj)
        if weights is None:
            return None
        _own, _ad, _unskinned, blended = core.skin_group_state(weights, store.vert_bone(obj))
        if not blended:
            return None
        bl, stored = set(int(i) for i in blended), store.vert_bone(obj)
        # the export's own sentence, asked about the blended points alone: every other point is handed to
        # skin_owners as the owner table already has it, so only 'blended' can come back
        probe = [weights[i] if i in bl else ([(int(stored[i]), 1.0)] if stored[i] >= 0 else [])
                 for i in range(len(stored))]
        _o, _a, why = core.skin_owners(probe, stored, store.bone_names(obj))
    except Exception as e:
        return Issue("skin", "ERROR", f"the skin check failed: {type(e).__name__}: {e}")
    return None if not why else Issue("skin", "ERROR", why)


def _skeleton_issue(obj):
    """``rig.skeleton_error`` through the panel's cache (keyed on the rig's bones and the export's stub set), so the
    handler and the panel pay for it once per skeleton state."""
    arm = store.rig(obj)
    if arm is None:
        return None
    from . import ui
    try:
        msg = ui._skeleton_error_cached(arm, obj)
    except Exception as e:
        return Issue("skeleton", "ERROR", f"the skeleton check failed: {type(e).__name__}: {e}")
    return None if not msg else Issue("skeleton", "ERROR", str(msg))


def issues(obj, deep: bool = True):
    """Every export refusal this object's state already earns, in the order ``ops.gather_export`` meets them.

    ``deep=False`` leaves out the vertex-group pass (:func:`_skin_issue`), which is the only check that walks the
    pool: the depsgraph handler uses it, the panel does not (a weight edit changes nothing in :func:`state_key`
    anyway, so only a redraw can catch it)."""
    from . import ops
    out = []
    if obj is None or not store.is_native(obj):
        return out
    err = None
    try:
        # store.pool_error, NOT pool_error_cached: that cache is what the operator polls read, and its key cannot see
        # a reorder (the counts do not move), so warming it from here would hand a poll a stale "fine" for its TTL.
        err = store.pool_error(obj)
    except Exception as e:
        err = f"the control-point pool check failed: {type(e).__name__}: {e}"
    if err:
        out.append(Issue("pool", "ERROR", str(err)))
    for code, fn in (("tables", store.ring_store_error), ("tables", store.limb_store_error),
                     ("source", store.source_error), ("rig", store.rig_error),
                     ("state", ops.object_state_error), ("uvcage", ops.uv_cage_error)):
        got = _guarded(code, fn, obj)
        if got is not None:
            out.append(got)
    got = _skeleton_issue(obj)
    if got is not None:
        out.append(got)
    if deep:
        got = _skin_issue(obj)
        if got is not None:
            out.append(got)
    return out


# ------------------------------------------------------------------------------------------- the cache
_CACHE: dict = {}                    # obj.name -> (state key, when, [Issue]): the checks the key can see
_DEEP: dict = {}                     # obj.name -> (state key, when, skin Issue or None): the vertex-group pass
_TTL = 0.5                           # a point REORDER moves nothing in the key, so the checks also run on a clock
_DEEP_TTL = 1.0                      # nor does a weight edit, and the deep pass is the dear one


def issues_cached(obj, deep: bool = True):
    """:func:`issues` memoised on :func:`state_key`, with a TTL on top for the two edits the key cannot see (a pool
    REORDER, which keeps every count, and a vertex-weight edit). ``deep`` adds the vertex-group pass and refreshes
    the rig's bone key -- a panel draw can afford both, the depsgraph handler cannot."""
    if obj is None or not store.is_native(obj):
        return []
    try:
        key = state_key(obj, fresh_bones=deep)
    except Exception as e:
        return [Issue("state", "ERROR", f"the state check failed: {type(e).__name__}: {e}")]
    hit, now = _CACHE.get(obj.name), time.monotonic()
    if hit is None or hit[0] != key or now - hit[1] > _TTL:
        hit = (key, now, issues(obj, deep=False))
        _CACHE[obj.name] = hit
    out = list(hit[2])
    got = _DEEP.get(obj.name)
    if deep and (got is None or got[0] != key or now - got[1] > _DEEP_TTL):
        got = (key, now, _skin_issue(obj))
        _DEEP[obj.name] = got
    if got is not None and got[0] == key and got[2] is not None:
        out.append(got[2])                               # what only the deep pass can see, while the key still holds
    return out


def cached(name: str):
    """The issues last computed for the object called ``name`` (no check is run): the viewport text reads this."""
    hit = _CACHE.get(name)
    out = list(hit[2]) if hit else []
    got = _DEEP.get(name)
    if hit and got and got[0] == hit[0] and got[2] is not None:
        out.append(got[2])
    return out


def forget(name: str | None = None) -> None:
    if name is None:
        _CACHE.clear()
        _DEEP.clear()
        _BONES.clear()
    else:
        _CACHE.pop(name, None)
        _DEEP.pop(name, None)


def enabled(context=None) -> bool:
    """The Live Checks switch (Display box). Missing scene / property = on."""
    scene = getattr(context or bpy.context, "scene", None)
    return bool(getattr(scene, "bz_live_checks", True)) if scene is not None else True


# ------------------------------------------------------------------------------------------- the handler
_LAST: dict = {}                     # obj.name -> the state key the handler last checked


def check(obj, deep: bool = False):
    """Run the checks for ``obj`` if its state key moved since the last time, and return ``(issues, new?)``.
    ``new`` is True when a code appeared that was not there before -- that is what earns a redraw."""
    before = {i.code for i in cached(obj.name)}
    try:
        key = state_key(obj, fresh_bones=deep)
    except Exception:
        return [], False
    if _LAST.get(obj.name) == key:
        return cached(obj.name), False
    _LAST[obj.name] = key
    got = issues_cached(obj, deep=deep)
    return got, bool({i.code for i in got} - before)


def _candidates(scene, depsgraph):
    """The native creatures an update can have changed, and for each whether its rig was updated too (only then is
    the rig's bone key re-read: :func:`bones_key`). The update list, never a walk of the whole scene."""
    out, rigs = {}, set()
    for upd in (getattr(depsgraph, "updates", ()) or ()):
        oid = getattr(upd, "id", None)
        oid = getattr(oid, "original", oid)
        if not isinstance(oid, bpy.types.Object):
            continue
        if oid.type == "ARMATURE":
            rigs.add(oid.name)
        elif store.is_native(oid):
            out.setdefault(oid.name, oid)
    if rigs:
        for o in bpy.data.objects:
            arm = store.rig(o) if store.is_native(o) else None
            if arm is not None and arm.name in rigs:
                out.setdefault(o.name, o)
    act = getattr(bpy.context, "active_object", None)
    if act is not None and store.is_native(act):
        out.setdefault(act.name, act)
    for name in rigs:                          # the bone key only when the armature itself was updated
        _BONES.pop(name, None)
    return list(out.values())


def tag_redraw() -> None:
    """Repaint the viewports and sidebars, so the alert appears without the user touching anything."""
    wm = getattr(bpy.context, "window_manager", None)
    for win in (getattr(wm, "windows", ()) or ()):
        for area in win.screen.areas:
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()


@persistent
def _on_depsgraph(scene, depsgraph=None):
    if not enabled():
        return
    fresh = False
    for obj in _candidates(scene, depsgraph):
        _got, new = check(obj)
        fresh = fresh or new
    if fresh:
        tag_redraw()


@persistent
def _on_load_post(_dummy=None):
    forget()
    _BONES.clear()
    _LAST.clear()


def text_lines():
    """``[(text, rgba)]`` for the viewport overlay (``draw._draw_text``): the CACHED issues of the visible native
    creatures. Nothing is computed here -- a draw handler must not run a check."""
    ctx = bpy.context
    vl = getattr(ctx, "view_layer", None)
    if vl is None or not enabled(ctx):
        return []
    lines = []
    for obj in vl.objects:
        if not store.is_native(obj):
            continue
        for iss in cached(obj.name)[:2]:
            lines.append((f"{obj.name}: {iss.text}", (1.0, 0.45, 0.35, 1.0)))
    return lines


# ------------------------------------------------------------------------------------------- the panel
def draw_problems(layout, obj, skip=()) -> int:
    """The Problems box of the SGM panel: every issue, in words, with nothing offered that would change the mesh.
    ``skip``: codes another box of the panel already draws (the rig box draws ``skeleton``). Returns how many were
    drawn."""
    from .ui import _alert
    got = [i for i in issues_cached(obj) if i.code not in skip]
    if not got:
        return 0
    box = layout.box()
    box.label(text=f"Problems ({len(got)}): Export refuses until they are gone", icon="ERROR")
    for iss in got:
        _alert(box, iss.text)
    return len(got)


def register_handlers() -> None:
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister_handlers() -> None:
    for lst, fn in ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph),
                    (bpy.app.handlers.load_post, _on_load_post)):
        if fn in lst:
            lst.remove(fn)
    forget()
    _LAST.clear()
