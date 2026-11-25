"""Combine Preview: combine the edited creature with another and draw the result beside it as a transient GPU ghost
(no datablocks, nothing saved, no undo steps). The bpy layer over ``combocore`` and ``combotest``.

``BZCombineSettings`` is registered by ``props.register`` before its pointer. The ghost has its own draw handler and state
(never ``draw._state``), is cleared on Clear, file load and unregister, and auto refresh runs only in Object mode.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import types

import numpy as np

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper

from . import combocore
from . import draw
from . import store
from . import texdraw

COLLECTION = "IC Combine Preview"
AUTO_DELAY = 0.8                   # seconds of quiet after the last edit before an auto refresh

_ghost: dict = {}                  # the current preview: see _set_ghost
_GPU_TEX: dict = {}                # combocore.texture_key -> GPUTexture (stock skins uploaded once)
_ENUM_ITEMS: list = []             # dynamic enum items must stay referenced (Blender keeps only the pointers)
_handler = None
_AUTO = {"pending": False, "last": 0.0}
_SAID: set = set()                 # messages printed once (draw callbacks run per redraw)
_INDEXING: set = set()             # stock dirs a donor-index build is scheduled or running for


def _say_once(msg: str) -> None:
    """Print a failure of a draw-time helper once per distinct message (never silently, never every redraw)."""
    if msg not in _SAID:
        _SAID.add(msg)
        print(f"[bz combine] {msg}")


# ------------------------------------------------------------------------------------------------------------ settings
def _redraw(context=None):
    wm = bpy.context.window_manager if bpy.context else None
    for win in (wm.windows if wm else []):
        for area in (win.screen.areas if win.screen else []):
            if area.type in ("VIEW_3D", "PROPERTIES"):
                area.tag_redraw()


def _on_setting(self, context):
    if getattr(self, "auto_refresh", False):
        _mark_dirty()
    _redraw(context)


def _on_display(self, context):
    if _ghost:
        _ghost["batches"] = None
    _redraw(context)


def user_stock_dir(create: bool = False) -> str:
    """Where Extract Stock Creatures writes: Blender's user datafiles ``pyic/engine_stock``."""
    try:
        return bpy.utils.user_resource("DATAFILES", path=os.path.join("pyic", "engine_stock"), create=create)
    except Exception as e:
        _say_once(f"no user data directory for extracted stock creatures: {type(e).__name__}: {e}")
        return ""


def _user_data_dir() -> str:
    """Blender's user data ``pyic`` folder (persistent), or '' ."""
    d = user_stock_dir()
    return os.path.dirname(d) if d else ""


def resolve_stock_dir(settings) -> str:
    """The stock creature directory for the panel's settings (``combocore.stock_dir``), or ''."""
    return combocore.stock_dir(bpy.path.abspath(settings.stock_dir) if settings else "",
                               [d for d in (user_stock_dir(),) if d]) or ""


def donor_index(settings, build: bool = False):
    """The DonorIndex of the resolved stock dir, or None (no combiner, no stock dir, or not indexed yet).

    ``build=False`` (every draw-time caller: enum items, part buttons) never indexes -- loading 61 creatures takes
    seconds -- it schedules :func:`_index_job` on a timer and returns None until that is done (the panel says
    "indexing"). Operators and tests pass ``build=True``."""
    if not combocore.available():
        return None
    d = resolve_stock_dir(settings)
    if not d:
        return None
    try:
        if build:
            return combocore.DonorIndex.get(d)
        idx = combocore.DonorIndex.peek(d)
    except Exception as e:
        _say_once(f"donor index of {d} failed: {type(e).__name__}: {e}")
        return None
    if idx is None and d not in _INDEXING:
        _INDEXING.add(d)
        bpy.app.timers.register(lambda: _index_job(d), first_interval=0.1)
    return idx


def indexing(settings) -> bool:
    """Whether the stock donor index of the settings' directory is being built."""
    d = resolve_stock_dir(settings) if combocore.available() else ""
    return bool(d) and d in _INDEXING


def _index_job(d: str):
    """Timer: build the donor index of ``d`` with a progress cursor, then redraw the panel."""
    wm = bpy.context.window_manager if bpy.context else None
    n = len(combocore._stock_files(d))
    print(f"[bz combine] indexing {n} stock creatures in {d} (once per change of that folder)")
    try:
        if wm:
            wm.progress_begin(0, max(n, 1))
        combocore.DonorIndex.get(d, progress=(lambda i, _n: wm.progress_update(i)) if wm else None)
    except Exception as e:
        _say_once(f"donor index of {d} failed: {type(e).__name__}: {e}")
    finally:
        if wm:
            wm.progress_end()
        _INDEXING.discard(d)
    _redraw()
    return None


def _stock_items(self, context):
    idx = donor_index(self)
    items = []
    if idx is not None and self.other_stock_name and self.other_stock_name not in idx.entries:
        items.append((MISSING_ITEM, f"(missing: {self.other_stock_name})", f"{self.other_stock_name!r} is not in "
                      f"{idx.directory}: pick a stock creature", "ERROR", MISSING_NUMBER))
    if idx is not None:
        numbers = {n: i for i, n in enumerate(sorted(idx.entries))}
        for cat, entries in idx.grouped().items():
            if not entries:
                continue
            if items:
                items.append(None)
            for e in entries:
                desc = (f"{combocore.CATEGORIES[cat]}; size {e.size}; parts: "
                        + ", ".join(combocore.PART_LABELS[p] for p in e.parts))
                items.append((e.name, e.name.replace("_", " ").title(), desc, "", numbers[e.name]))
    if not items:
        if resolve_stock_dir(self) and idx is None:
            items = [("", "(indexing stock creatures...)", "The stock creatures are being indexed", "", 0)]
        else:
            items = [("", "(no stock creatures)", "Set the stock directory or press Extract Stock Creatures", "", 0)]
    _ENUM_ITEMS[:] = items
    return _ENUM_ITEMS


MISSING_ITEM = "__missing__"
MISSING_NUMBER = 1 << 20                # an enum number no stock creature uses


def _stock_get(self):
    idx = donor_index(self)
    if idx is None:
        return 0
    names = sorted(idx.entries)
    # a name that is not in the folder shows as "(missing: name)" -- never as another creature than the one combined
    return names.index(self.other_stock_name) if self.other_stock_name in names else MISSING_NUMBER


def _stock_set(self, value):
    idx = donor_index(self)
    if idx is None:
        return
    names = sorted(idx.entries)
    if 0 <= value < len(names):
        self.other_stock_name = names[value]


def _native_poll(self, obj):
    return store.is_native(obj)


class BZCombineSettings(bpy.types.PropertyGroup):
    """Combine Preview settings (Scene.bz_combine)."""
    role: bpy.props.EnumProperty(
        name="Role", items=[(combocore.ROLE_TORSO, "This Creature Is the Torso", "The other creature donates the "
                             "toggled parts onto this creature"),
                            (combocore.ROLE_DONOR, "This Creature Donates Parts", "This creature donates the toggled "
                             "parts onto the other creature's torso")],
        default=combocore.ROLE_TORSO, update=_on_setting)
    other_kind: bpy.props.EnumProperty(
        name="Other Creature", items=[("STOCK", "Stock", "A base-game creature from the stock directory"),
                                      ("OBJECT", "Object", "Another creature in this scene (including its edits)"),
                                      ("FILE", "File", "A creature .sgm / .ebp on disk (with its .lua / .lsc)")],
        default="STOCK", update=_on_setting)
    other_stock_name: bpy.props.StringProperty(name="Stock Creature", default="cheetah", update=_on_setting)
    other_stock: bpy.props.EnumProperty(name="Stock Creature", description="The other creature (type to search)",
                                        items=_stock_items, get=_stock_get, set=_stock_set)
    other_object: bpy.props.PointerProperty(name="Object", type=bpy.types.Object, poll=_native_poll,
                                            update=_on_setting)
    other_file: bpy.props.StringProperty(name="File", subtype="FILE_PATH", update=_on_setting)
    stock_dir: bpy.props.StringProperty(
        name="Stock Directory", subtype="DIR_PATH", update=_on_setting,
        description="Folder of stock creatures; empty = $BZ_STOCK_DIR, the repo, or the extracted copy")
    # Advanced
    show_advanced: bpy.props.BoolProperty(name="Advanced", default=False)
    run_lua: bpy.props.BoolProperty(name="Run attrcombiner.lua", default=True, update=_on_setting,
                                    description="Compute ratings, cost and rank with the game's script (needs lupa)")
    body_scale: bpy.props.BoolProperty(name="Body Scale", default=True, update=_on_setting,
                                       description="Scale the combination to its size, as the game does")
    as_spec: bpy.props.BoolProperty(name="Take Picks Verbatim", default=False, update=_on_setting,
                                    description="Combine like a saved design, without the part-button rules")
    script_source: bpy.props.EnumProperty(
        name="Script", items=[("game", "Game", "The archives' attrcombiner.lua (what IC.exe runs)"),
                              ("tool", "ComboTest.exe", "ComboTest.exe's built-in copy (its exports' costs)")],
        default="game", update=_on_setting)
    use_rules: bpy.props.BoolProperty(name="Limb-Scale Rules", default=True, update=_on_setting,
                                      description="Apply the creatures' .lsc limb-scale rules")
    default_size: bpy.props.IntProperty(name="Default Size", default=3, min=1, max=10, update=_on_setting,
                                        description="Size of the default attributes of a creature without a .lua")
    placement: bpy.props.EnumProperty(
        name="Placement", items=[("BESIDE", "Beside", "Draw the combination beside the creature (+X)"),
                                 ("IN_PLACE", "In Place", "Draw it over the creature, translucent")],
        default="BESIDE", update=_on_display)
    shade: bpy.props.EnumProperty(
        name="Shading", items=[("TEXTURED", "Textured", "The combined creature's textures"),
                               ("PART", "By Part", "Colour each part by its slot"),
                               ("PLAIN", "Plain", "Flat shaded grey")],
        default="TEXTURED", update=_on_display)
    auto_refresh: bpy.props.BoolProperty(
        name="Auto Refresh", default=False, update=_on_setting,
        description=f"Refresh the preview {AUTO_DELAY} s after the last edit (Object mode only)")
    validate_level: bpy.props.EnumProperty(
        name="Combine Checks", items=[("QUICK", "Quick", "Validate also runs the Quick combine tier (13 combines "
                                                         "with 6 stock donors, a few seconds)"),
                                      ("OFF", "Off", "Validate does not combine")],
        default="QUICK", description="Whether Validate runs the offline combine checks")


def _add_part_props():
    for key, label in combocore.PART_LABELS.items():
        BZCombineSettings.__annotations__[f"part_{key}"] = bpy.props.BoolProperty(
            name=label, default=key in ("head", "tail"), update=_on_setting,
            description=f"Take the {label.lower()} from the donor (ComboTest.exe's {label} button)")


_add_part_props()


def settings_groups(settings) -> list:
    return [k for k in combocore.PARTS if getattr(settings, f"part_{k}", False)]


def options(settings) -> "combocore.Options":
    return combocore.Options(run_lua=settings.run_lua, body_scale=settings.body_scale, as_spec=settings.as_spec,
                             script_source=settings.script_source, use_rules=settings.use_rules,
                             default_size=settings.default_size)


# ----------------------------------------------------------------------------------------------------- state digest
def _verts(obj) -> np.ndarray:
    """The control points as they are now (the live bmesh in edit mode: obj.data is stale there, and a draw-time
    digest may not flush it)."""
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        return np.array([v.co[:] for v in bm.verts], dtype=np.float32).reshape(-1)
    me = obj.data
    arr = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", arr)
    return arr


def _group_signature(obj) -> bytes:
    """The vertex-group weights of every point (the live bmesh deform layer in edit mode)."""
    h = hashlib.sha1()
    h.update("|".join(vg.name for vg in obj.vertex_groups).encode())
    if obj.mode == "EDIT":
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        dl = bm.verts.layers.deform.active
        if dl is not None:
            for v in bm.verts:
                h.update(repr(sorted(v[dl].items())).encode())
        return h.digest()
    for v in obj.data.vertices:
        h.update(repr([(g.group, g.weight) for g in v.groups]).encode())
    return h.digest()


_DIGEST_KEYS = (store.KEY_PATCHES, store.KEY_PATCH_LIMB, store.KEY_CONN_VERTS, store.KEY_CONN_COUNTS,
                store.KEY_CONN_ENC, store.KEY_STORE_VERSION, store.KEY_CONN_BONES, store.KEY_PATCH_UV, store.KEY_PATCH_MAT, store.KEY_VERT_BONE, store.KEY_PATCH_TYPE,
                store.KEY_SOURCE, "bz_ebp", store.KEY_GEN, store.KEY_POOL_N, "bz_uv_cage")


def _plain(v):
    """An ID property value as plain Python (for hashing)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "to_list"):
        return v.to_list()
    try:
        return [_plain(x) for x in v]
    except TypeError:
        return str(v)


def _hash_prop(h, v) -> None:
    """Feed an ID property value to ``h``: numeric arrays by their buffer (hundreds of times faster than a repr of
    ``to_list()``, which matters in a panel draw), anything else by repr."""
    if hasattr(v, "to_list"):
        try:
            mv = memoryview(v)
            h.update(f"{mv.format}{mv.shape}".encode())
            h.update(mv)
            return
        except TypeError:
            pass
    h.update(repr(_plain(v)).encode())


def object_digest(obj) -> str:
    """What a combine of ``obj`` depends on: control points, the patch / limb / ring / skin / UV tables, its source,
    the rest pose of its rig and its companions' file stamps."""
    h = hashlib.sha1()
    h.update(_verts(obj).tobytes())
    for key in _DIGEST_KEYS:
        h.update(key.encode())
        _hash_prop(h, obj.get(key))
    for name in (store.ATTR_VERT_BONE, store.ATTR_CP):          # per-point tables live in mesh attributes
        a = store.point_ints(obj, name)
        h.update(name.encode() + (a.astype(np.int32).tobytes() if a is not None else b"-"))
    h.update(_group_signature(obj))                             # Export adopts a rigid vertex-group reassignment
    h.update(str(store.pool_error_cached(obj)).encode())        # a blocked export is an error, not a stale preview
    # painted skins (export writes them): an image painted since load is dirty; its pixels decide
    from . import material
    for name in store.texture_images(obj):
        img = bpy.data.images.get(name) if name else None
        if img is None:
            continue
        h.update(f"{name}{tuple(img.size)}{img.is_dirty}".encode())
        if img.is_dirty and img.size[0]:
            h.update(str(material._pixel_crc(img)).encode())
    arm = store.rig(obj)
    if arm is not None and arm.type == "ARMATURE":
        for b in arm.data.bones:
            h.update(b.name.encode())
            h.update(np.array(b.matrix_local, dtype=np.float32).tobytes())
    for p in (obj.get(store.KEY_SOURCE), obj.get("bz_ebp")):
        if p:
            stem = os.path.splitext(str(p))[0]
            for ext in (".lua", ".lsc", os.path.splitext(str(p))[1]):
                try:
                    st = os.stat(stem + ext)
                    h.update(f"{ext}{st.st_size}:{st.st_mtime_ns}".encode())
                except OSError:
                    h.update(f"{ext}-".encode())
    return h.hexdigest()


def state_digest(scene, obj, settings) -> str:
    """The digest a preview result is current for: the edited object, the other creature and every setting that
    changes the combine (display-only settings excluded)."""
    h = hashlib.sha1(object_digest(obj).encode())
    h.update(repr((settings.role, settings.other_kind, settings_groups(settings), options(settings).key(),
                   int(getattr(scene, "bz_density", 4)))).encode())
    if settings.other_kind == "STOCK":
        d = resolve_stock_dir(settings)
        h.update(f"{d}/{settings.other_stock_name}".encode())
        p = os.path.join(d, settings.other_stock_name) if d else ""
        for ext in (".sgm", ".lua", ".lsc"):
            try:
                st = os.stat(p + ext)
                h.update(f"{ext}{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                h.update(f"{ext}-".encode())
    elif settings.other_kind == "OBJECT":
        o = settings.other_object
        h.update((object_digest(o) if o is not None and store.is_native(o) else "-").encode())
    else:
        p = bpy.path.abspath(settings.other_file)
        try:
            st = os.stat(p)
            h.update(f"{p}{st.st_size}:{st.st_mtime_ns}".encode())
        except OSError:
            h.update(f"{p}-".encode())
    return h.hexdigest()


def _uid(obj) -> int:
    return int(getattr(obj, "session_uid", 0) or 0)


def ghost_object():
    """The object the preview belongs to (found by its session id, so a rename keeps the preview), or None."""
    if not _ghost:
        return None
    obj = bpy.data.objects.get(_ghost.get("obj", ""))
    if obj is not None and _uid(obj) == _ghost.get("uid"):
        return obj
    obj = next((o for o in bpy.data.objects if _uid(o) == _ghost.get("uid")), None)
    if obj is not None:
        _ghost["obj"] = obj.name
    return obj


def current_result(obj):
    """The PreviewResult showing for ``obj`` (None when the preview belongs to another object or none exists)."""
    if not _ghost or obj is None or _ghost.get("uid") != _uid(obj):
        return None
    return _ghost.get("preview")


def is_stale(scene, obj, settings) -> bool:
    if current_result(obj) is None:
        return False
    try:
        return state_digest(scene, obj, settings) != _ghost.get("digest")
    except Exception as e:                     # can't tell: say stale, and why (once)
        _say_once(f"state digest failed (preview shown as stale): {type(e).__name__}: {e}")
        return True


# --------------------------------------------------------------------------------------------------- the other animal
def _ctx(scene):
    return types.SimpleNamespace(scene=scene)


def _edited(scene, obj, settings, info):
    from . import ops
    model = ops.edited_model(obj, _ctx(scene), animations=False, info=info)
    return combocore.edited_parent(model, obj, default_size=settings.default_size)


def other_parent(scene, obj, settings, notes: list):
    """The other creature as a combiner Parent, per ``other_kind``."""
    kind = settings.other_kind
    if kind == "STOCK":
        d = resolve_stock_dir(settings)
        if not d:
            raise combocore.SetupError("no stock creatures found: set the stock directory or press Extract Stock "
                                       "Creatures")
        path = os.path.join(d, settings.other_stock_name + ".sgm")
        if not os.path.exists(path):
            raise FileNotFoundError(f"stock creature {settings.other_stock_name!r} is not in {d}")
        return combocore.parent_for_file(path)
    if kind == "OBJECT":
        o = settings.other_object
        if o is None or not store.is_native(o):
            raise ValueError("pick another creature object as the other creature")
        if o is obj:
            raise ValueError("the other creature is this creature: pick a different object")
        info = {}
        p, comp = _edited(scene, o, settings, info)
        notes += [f"{o.name}: {n}" for n in comp.notes + info.get("warnings", [])]
        return p
    path = bpy.path.abspath(settings.other_file)
    if not path:
        raise ValueError("choose the other creature's .sgm / .ebp file")
    return combocore.parent_for_file(path)


_AVAIL: dict = {}


def part_availability(scene, obj, settings) -> dict:
    """{part: True/False/None} -- whether the DONOR of the current role has a limb with patches for each part button
    (None: not known yet). A part the donor lacks is drawn disabled (the combine would not change)."""
    out = {g: None for g in combocore.PARTS}
    if not combocore.available():
        return out
    try:
        from combotest.validate import donor_has_part
        if settings.role == combocore.ROLE_TORSO:
            if settings.other_kind == "STOCK":
                idx = donor_index(settings)
                e = idx.entries.get(settings.other_stock_name) if idx else None
                if e is None:
                    return out
                return {g: g in e.parts for g in combocore.PARTS}
            if settings.other_kind == "FILE":
                p = combocore.parent_for_file(bpy.path.abspath(settings.other_file))
                return {g: donor_has_part(p, g) for g in combocore.PARTS}
            o = settings.other_object
            src = store.source_path(o) if o is not None and store.is_native(o) else ""
        else:
            o, src = obj, store.source_path(obj)
        if not src:
            return out
        # this / the other object: its CURRENT limb rows (names, classes, bones; added / deleted limbs) and patch
        # membership, applied to a private copy of the source the way Export applies them
        from . import core, structure
        from combotest.loader import Parent
        pl = store.patch_limb(o)
        rows = store.limb_rows(o)                      # None: the source's limbs
        st = os.stat(src)
        key = (src, st.st_mtime_ns, st.st_size, hashlib.sha1(pl.tobytes()).hexdigest(),
               None if rows is None else tuple(rows))
        if key not in _AVAIL:
            m = core.read_source(src)                  # a private copy: its limbs are rebuilt
            structure.apply_limbs(m, core.read_source(src), rows, pl if len(pl) else None,
                                  n_patches=len(pl) if len(pl) else None)
            p = Parent.from_model(m, name="probe", path=src)
            _AVAIL.clear()
            _AVAIL[key] = {g: donor_has_part(p, g) for g in combocore.PARTS}
        return dict(_AVAIL[key])
    except Exception as e:                     # every button enabled, and the reason printed once
        _say_once(f"part availability unknown ({settings.other_kind} {settings.role}): {type(e).__name__}: {e}")
        return out


# ------------------------------------------------------------------------------------------------------------ preview
def compute_preview(scene, obj, settings, progress=None):
    """Combine ``obj`` per ``settings`` and make it the ghost. Returns the PreviewResult (``error`` set when the
    combine failed); raises for missing inputs (no combiner, no stock creature, an export that can't be gathered)."""
    if not combocore.available():
        raise combocore.CombinerUnavailable(f"the combiner (combotest) is not importable: {combocore.IMPORT_ERROR}")
    t0 = time.perf_counter()
    step = (lambda i: progress(i)) if progress else (lambda i: None)
    info = {}
    obj_key = object_digest(obj)
    edited, comp = _edited(scene, obj, settings, info)
    t_gather = time.perf_counter() - t0
    step(1)
    notes = list(comp.notes)
    notes += [f"export: {w}" for w in info.get("warnings", [])]
    t1 = time.perf_counter()
    other = other_parent(scene, obj, settings, notes)
    t_other = time.perf_counter() - t1
    step(2)
    pr = combocore.run_preview(edited, other, settings.role, settings_groups(settings), options(settings), notes=notes,
                               state_key=obj_key)
    step(3)
    arrays = None
    t2 = time.perf_counter()
    if pr.ok:
        density = int(getattr(scene, "bz_density", 4))
        prev = _ghost.get("arrays") or {}
        arrays = combocore.build_ghost_arrays(pr.result, density, topo=prev.get("topo"))
    pr.timings.update(gather=t_gather, other=t_other, arrays=time.perf_counter() - t2)
    pr.timings["wall"] = time.perf_counter() - t0
    edited_x = _verts(obj).reshape(-1, 3)[:, 0] if len(obj.data.vertices) else np.zeros(1)
    _set_ghost(obj=obj.name, uid=_uid(obj), scene=scene.name, preview=pr, arrays=arrays, digest=state_digest(scene, obj, settings),
               edited_width=float(edited_x.max() - edited_x.min()) if len(edited_x) else 0.0)
    step(4)
    return pr


def _set_ghost(**kw):
    arrays = kw.get("arrays")
    _ghost.clear()
    _ghost.update(kw)
    _ghost["batches"] = None
    _ghost["batch_key"] = None
    if arrays is None:
        _ghost["arrays"] = None
    keep = {e[0] for e in (arrays or {}).get("textures", []) if e and e[0] is not None}
    for key in [k for k in _GPU_TEX if k not in keep]:
        del _GPU_TEX[key]                      # skins of earlier previews: released
    _redraw()


def clear_ghost():
    """Drop the preview and its GPU textures (released by dereference)."""
    _ghost.clear()
    _GPU_TEX.clear()
    _redraw()


def report_preview(op, pr):
    """Operator reports for a preview: the error, dropped parts, warnings, then the header + summary."""
    if not pr.ok:
        op.report({"ERROR"}, pr.header())
        return
    for d in pr.dropped:
        op.report({"WARNING"}, f"{combocore.PART_LABELS.get(d['group'], d['slot'])} ({d['slot']}) not taken from "
                               f"{d['donor']}: {d['words']}")
    for w in pr.warnings:
        op.report({"WARNING"}, w)
    op.report({"INFO"}, f"{pr.header()}: {pr.summary()}")


def _active_creature(context):
    obj = context.active_object
    return obj if store.is_native(obj) else None


class BZ_OT_combine_preview(bpy.types.Operator):
    """Combine this creature with another one and show the result beside it"""
    bl_idname = "bz.combine_preview"
    bl_label = "Preview Combination"

    @classmethod
    def poll(cls, context):
        return _active_creature(context) is not None and combocore.available()

    def execute(self, context):
        obj = _active_creature(context)
        wm = context.window_manager
        wm.progress_begin(0, 4)
        try:
            pr = compute_preview(context.scene, obj, context.scene.bz_combine, progress=wm.progress_update)
        except Exception as e:
            self.report({"ERROR"}, f"Combine preview failed: {e}")
            return {"CANCELLED"}
        finally:
            wm.progress_end()
        report_preview(self, pr)
        return {"FINISHED"} if pr.ok else {"CANCELLED"}


class BZ_OT_combine_clear(bpy.types.Operator):
    """Remove the combine preview from the viewport"""
    bl_idname = "bz.combine_clear"
    bl_label = "Clear Preview"

    def execute(self, context):
        clear_ghost()
        return {"FINISHED"}


class BZ_OT_combine_swap(bpy.types.Operator):
    """Swap which creature is the torso and which is the donor"""
    bl_idname = "bz.combine_swap"
    bl_label = "Swap"

    def execute(self, context):
        s = context.scene.bz_combine
        s.role = combocore.ROLE_DONOR if s.role == combocore.ROLE_TORSO else combocore.ROLE_TORSO
        obj = _active_creature(context)
        if obj is not None and current_result(obj) is not None:
            return bpy.ops.bz.combine_preview()
        return {"FINISHED"}


class BZ_OT_combine_parts(bpy.types.Operator):
    """Toggle every part button on or off"""
    bl_idname = "bz.combine_parts"
    bl_label = "Parts"

    action: bpy.props.EnumProperty(items=[("ALL", "All", "Every part"), ("NONE", "None", "No part")])

    def execute(self, context):
        s = context.scene.bz_combine
        for g in combocore.PARTS:
            setattr(s, f"part_{g}", self.action == "ALL")
        return {"FINISHED"}


def _stock_pool_dirs(settings) -> list:
    """Every directory the donor index or the offline tiers may read stock creatures from."""
    return combocore.stock_dir_candidates(bpy.path.abspath(settings.stock_dir) if settings else "",
                                          [d for d in (user_stock_dir(),) if d])


def _default_materialize_path(obj, pr, settings=None) -> str:
    """``combos/<torso>_<other>.sgm`` beside the saved .blend, else beside the creature's source, else in Blender's
    user data (``pyic/combos``) -- never inside a stock donor directory (combocore.materialize_path)."""
    stem = f"{combocore.creature_name(pr.torso)}_{combocore.creature_name(pr.donor)}"
    if settings is None:
        settings = getattr(bpy.context.scene, "bz_combine", None)
    return combocore.materialize_path(stem, bpy.data.filepath, store.source_path(obj) or "",
                                      _stock_pool_dirs(settings), _user_data_dir())


def _preview_collection(context, create=True):
    coll = bpy.data.collections.get(COLLECTION)
    if coll is None and create:
        coll = bpy.data.collections.new(COLLECTION)
        context.scene.collection.children.link(coll)
    return coll


class BZ_OT_combine_materialize(bpy.types.Operator, ExportHelper):
    """Save the combined creature to .sgm and import it for editing"""
    bl_idname = "bz.combine_materialize"
    bl_label = "Materialize Combination"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sgm"
    filter_glob: bpy.props.StringProperty(default="*.sgm", options={"HIDDEN"})
    companions: bpy.props.BoolProperty(name="Write .lua / .lsc", default=True,
                                       description="Also write the combined attributes (.lua) and limb scales (.lsc) "
                                                   "beside the .sgm")

    @classmethod
    def poll(cls, context):
        obj = _active_creature(context)
        pr = current_result(obj)
        if pr is None or not pr.ok:
            return False
        err = store.pool_error_cached(obj)
        if err:
            cls.poll_message_set(err)
            return False
        return True

    def invoke(self, context, event):
        obj = _active_creature(context)
        self.filepath = _default_materialize_path(obj, current_result(obj), context.scene.bz_combine)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        obj = _active_creature(context)
        pr = current_result(obj)
        if pr is None or not pr.ok:
            self.report({"ERROR"}, "No combine preview to materialize: press Preview first")
            return {"CANCELLED"}
        if is_stale(context.scene, obj, context.scene.bz_combine):
            self.report({"WARNING"}, "The preview is stale (edits since): materializing the combination as previewed")
        for e in pr.load_errors:
            self.report({"WARNING"}, f"The combined creature won't load in the game: {e}")
        from ICParse.sgm import parser as sgm_parser
        path = bpy.path.abspath(self.filepath)
        if not path.lower().endswith(".sgm"):
            path += ".sgm"
        if combocore.in_stock_pool(path, _stock_pool_dirs(context.scene.bz_combine)):
            self.report({"ERROR"}, f"Not writing into the stock creature directory {os.path.dirname(path)}: the "
                                   "combination would be listed as a stock donor. Choose another folder")
            return {"CANCELLED"}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            data = sgm_parser.write(pr.result.model)          # bytes only: the result model is never edited
            with open(path, "wb") as fh:
                fh.write(data)
            written = [path]
            if self.companions:
                from combotest.combine import write_companions
                written += write_companions(pr.result, path)
        except Exception as e:
            self.report({"ERROR"}, f"Materialize failed: {e}")
            return {"CANCELLED"}
        coll = _preview_collection(context)
        res = bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False)
        if "FINISHED" not in res:
            self.report({"ERROR"}, f"Materialized {path}, but importing it failed")
            return {"CANCELLED"}
        new = context.view_layer.objects.active
        arm = bpy.data.objects.get(new.get("bz_rig", ""))
        for o in (new, arm):
            if o is None:
                continue
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
        import combotest
        this, other = (pr.torso, pr.donor) if pr.role == combocore.ROLE_TORSO else (pr.donor, pr.torso)
        new["bz_combo"] = json.dumps(dict(
            torso=combocore.creature_name(pr.torso), donor=combocore.creature_name(pr.donor),
            this=combocore.creature_name(this), other=combocore.creature_name(other), load_errors=pr.load_errors,
            role=pr.role, parts=pr.groups, accepted=pr.accepted, dropped=[[d["slot"], d["reason"]] for d in pr.dropped],
            options=list(options(context.scene.bz_combine).key()), size=pr.result.size, scale=pr.result.scale,
            files=written, combotest=getattr(combotest, "__version__", "?")))
        context.view_layer.objects.active = new
        self.report({"WARNING"} if pr.load_errors else {"INFO"},
                    f"Materialized {pr.header()} -> {os.path.basename(path)} ({len(pr.result.model.patches)} patches)"
                    + (", with .lua / .lsc" if self.companions else "")
                    + (f"; it won't load: {len(pr.load_errors)} check_model error(s)" if pr.load_errors else ""))
        return {"FINISHED"}


def clear_materialized(context) -> dict:
    """Remove the materialized combinations of the ``IC Combine Preview`` collection (objects stamped ``bz_combo``,
    and their rigs) with their armature, mesh, Actions and the ``SGM_*`` images they left with no users; then the
    collection when it is empty. Anything else in the collection (a user's object linked there too) is only
    unlinked from it, never deleted. Returns counts removed (``kept``: objects unlinked, not deleted)."""
    coll = bpy.data.collections.get(COLLECTION)
    out = dict(objects=0, meshes=0, armatures=0, actions=0, images=0, kept=0)
    if coll is None:
        return out
    combos = [o for o in coll.all_objects if o.get("bz_combo")]
    rigs = {str(o.get("bz_rig", "")) for o in combos}
    objs = combos + [o for o in bpy.data.objects if o.name in rigs and o not in combos]
    for o in [o for o in coll.all_objects if o not in objs]:
        for c in [c for c in o.users_collection if c == coll or c.name in {x.name for x in coll.children_recursive}]:
            c.objects.unlink(o)
        if not o.users_collection:
            context.scene.collection.objects.link(o)
        out["kept"] += 1
    image_names = set()
    uids = {store.uid(o) for o in objs if store.uid(o)}
    for o in objs:
        image_names.update(str(n) for n in o.get(store.KEY_TEX_IMGS, []) if n)
        image_names.update(str(n) for n in o.get(store.KEY_MAT_TEX, []) if n)
    arm_names = {o.name for o in objs if o.type == "ARMATURE"}
    datas = [(o.data, o.type) for o in objs if o.data is not None]
    for o in objs:
        draw.invalidate(o.name)
        bpy.data.objects.remove(o, do_unlink=True)
        out["objects"] += 1
    for data, kind in datas:
        if data.users == 0:
            if kind == "MESH":
                bpy.data.meshes.remove(data)
                out["meshes"] += 1
            elif kind == "ARMATURE":
                bpy.data.armatures.remove(data)
                out["armatures"] += 1
    for act in list(bpy.data.actions):
        if act.get("bz_arm") in arm_names and act.users <= int(act.use_fake_user):
            bpy.data.actions.remove(act)
            out["actions"] += 1
    for name in image_names:
        img = bpy.data.images.get(name)
        # skin images keep a fake user (material.decode_textures): unused = no user besides it, and not another
        # creature's image that took the name
        if img is not None and img.users <= int(img.use_fake_user) and img.get("bz_tex_for") in ({None} | uids):
            texdraw._TEX_CACHE.pop(name, None)
            bpy.data.images.remove(img)
            out["images"] += 1
    if not coll.all_objects and not coll.children:
        bpy.data.collections.remove(coll)
    return out


class BZ_OT_combine_clear_materialized(bpy.types.Operator):
    """Delete the saved combinations from the scene with their rigs, Actions and images"""
    bl_idname = "bz.combine_clear_materialized"
    bl_label = "Clear Materialized"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bpy.data.collections.get(COLLECTION) is not None

    def execute(self, context):
        n = clear_materialized(context)
        kept = n.pop("kept")
        self.report({"INFO"}, "Removed " + ", ".join(f"{v} {k}" for k, v in n.items())
                    + (f"; {kept} other object(s) unlinked from the collection, not deleted" if kept else ""))
        return {"FINISHED"}


def validate_combines(scene, obj, model=None, progress=None) -> list:
    """The Quick combine tier on ``obj``'s in-memory export model (``model``, else gathered without Actions):
    ``COMBINE ...`` lines; the summary goes on the object (``bz_combine_summary``)."""
    settings = scene.bz_combine
    if not combocore.available():
        return [f"COMBINE INFO setup: combine checks skipped: the combiner (combotest) is not importable "
                f"({combocore.IMPORT_ERROR})"]
    info = {}
    if model is None:
        from . import ops
        model = ops.edited_model(obj, _ctx(scene), animations=False, info=info)
    edited, comp = combocore.edited_parent(model, obj, default_size=settings.default_size)
    notes = comp.notes + info.get("warnings", [])
    try:
        rep = combocore.quick_validate(edited, resolve_stock_dir(settings) or None, options(settings), progress=progress)
    except combocore.SetupError as e:
        obj["bz_combine_summary"] = json.dumps({"setup_error": str(e)})
        return combocore.format_messages(None, notes, setup_error=str(e))
    summary = rep.summary()
    obj["bz_combine_summary"] = json.dumps(summary, default=str)
    lines = combocore.format_messages(rep, notes)
    lines[len(notes):len(notes)] = combocore.ring_placement_messages(model)   # with the precheck; the summary stays last
    return lines


def merge_validation(obj, combine_lines) -> list:
    """``bz_validation`` with its COMBINE lines replaced by ``combine_lines``."""
    kept = [str(p) for p in obj.get("bz_validation", []) if not str(p).startswith("COMBINE")]
    obj["bz_validation"] = kept + list(combine_lines)
    return list(obj["bz_validation"])


class BZ_OT_combine_validate(bpy.types.Operator):
    """Test how this creature combines with stock creatures; see the Validate list"""
    bl_idname = "bz.combine_validate"
    bl_label = "Validate Combines"

    @classmethod
    def poll(cls, context):
        return _active_creature(context) is not None and combocore.available()

    def execute(self, context):
        obj = _active_creature(context)
        wm = context.window_manager
        wm.progress_begin(0, 13)
        t = time.perf_counter()
        try:
            lines = validate_combines(context.scene, obj, progress=lambda i, n: wm.progress_update(i))
        except Exception as e:
            self.report({"ERROR"}, f"Combine validation failed: {e}")
            return {"CANCELLED"}
        finally:
            wm.progress_end()
        merge_validation(obj, lines)
        issues = [x for x in lines if x.startswith(("COMBINE ERROR", "COMBINE WARN"))]
        for x in issues:
            self.report({"ERROR"} if x.startswith("COMBINE ERROR") else {"WARNING"}, x)
        tail = lines[-1] if lines else "no result"
        self.report({"INFO"}, f"{len(issues)} combine issue(s) in {time.perf_counter() - t:.1f} s; {tail}")
        return {"FINISHED"}


class BZ_OT_combine_extract_stock(bpy.types.Operator):
    """Extract the game's 61 stock creatures to use as combine donors"""
    bl_idname = "bz.combine_extract_stock"
    bl_label = "Extract Stock Creatures"

    def execute(self, context):
        out = user_stock_dir(create=True)
        if not out:
            self.report({"ERROR"}, "No user data directory to extract into")
            return {"CANCELLED"}
        try:
            from .sceneobj import prefs
            written = combocore.extract_stock(out, root=prefs.resolve_game_dir(context)[0])
        except Exception as e:
            self.report({"ERROR"}, f"Extraction failed: {e}")
            return {"CANCELLED"}
        context.scene.bz_combine.stock_dir = out
        try:                                   # index them now (with progress), not in the next panel draw
            wm = context.window_manager
            wm.progress_begin(0, 61)
            idx = combocore.DonorIndex.get(out, progress=lambda i, _n: wm.progress_update(i))
        except Exception as e:
            self.report({"WARNING"}, f"Extracted {len(written)} files to {out}, but indexing them failed: {e}")
            return {"FINISHED"}
        finally:
            wm.progress_end()
        self.report({"INFO"}, f"Extracted {len(written)} files to {out}; {len(idx.entries)} stock creatures indexed")
        return {"FINISHED"}


CLASSES = (BZ_OT_combine_preview, BZ_OT_combine_clear, BZ_OT_combine_swap, BZ_OT_combine_parts,
           BZ_OT_combine_materialize, BZ_OT_combine_clear_materialized, BZ_OT_combine_validate,
           BZ_OT_combine_extract_stock)


# ------------------------------------------------------------------------------------------------------------- ghost
def _gpu_texture(entry):
    key, rgba, w, h = entry
    if key is None:
        return texdraw.white_texture()
    tex = _GPU_TEX.get(key)
    if tex is None:
        tex = texdraw.gputex_from_rgba(rgba, w, h)
        _GPU_TEX[key] = tex
    return tex


def make_ghost_batches(arrays, shade: str, alpha: float):
    """GPU batches for ``combocore.build_ghost_arrays`` output: ("TEX", [(texture, batch)]) through texdraw's shader,
    or ("COLOR", batch) through the SMOOTH_COLOR shader (By Part / Plain, or when no slot draws textured)."""
    from gpu_extras.batch import batch_for_shader
    surfV, surfN, tris = arrays["surfV"], arrays["surfN"], np.asarray(arrays["tris"])
    if shade == "TEXTURED":
        slot_textures = [_gpu_texture(arrays["textures"][ti]) if ti >= 0 else texdraw.white_texture()
                         for ti in arrays["slot_tex_index"]]
        vbuf, dl = texdraw.build_drawlist_raw(surfV, surfN, tris, arrays["density"], arrays["uvs"], arrays["patch_mat"],
                                              slot_textures, arrays["slot_tint"], alpha)
        if dl:
            return ("TEX", texdraw.make_batches(vbuf, dl))
    if shade == "PART":
        nn = arrays["density"] ** 2
        base = np.asarray(arrays["part_colors"])[np.arange(len(surfV)) // nn]
        sh = (np.clip(surfN @ draw._L1, 0, 1) * 0.7 + np.clip(surfN @ draw._L2, 0, 1) * 0.3)
        col = np.empty((len(surfV), 4), dtype=np.float32)
        col[:, :3] = (base * (0.25 + 0.75 * sh)[:, None]).astype(np.float32)
        col[:, 3] = alpha
    else:
        col = draw._shade(surfN, alpha)
    return ("COLOR", batch_for_shader(draw._tri_shader(), "TRIS", {"pos": surfV.astype(np.float32), "color": col},
                                      indices=tris.astype(np.int32)))


def placement_matrix(obj, placement: str):
    """World matrix of the ghost: the object's, moved +X (object space) beside it for ``BESIDE``."""
    import mathutils
    mw = obj.matrix_world.copy()
    if placement != "BESIDE" or not _ghost.get("arrays"):
        return mw
    a = _ghost["arrays"]
    combo_w = float(a["bbox_max"][0] - a["bbox_min"][0])
    dx = 1.25 * (float(_ghost.get("edited_width", 0.0)) + combo_w) / 2.0
    return mw @ mathutils.Matrix.Translation((dx, 0.0, 0.0))


def _draw_ghost():
    import gpu
    if not _ghost or not _ghost.get("arrays"):
        return
    obj = ghost_object()
    scene = bpy.context.scene
    if obj is None or scene is None or not hasattr(scene, "bz_combine"):
        return
    s = scene.bz_combine
    alpha = 0.5 if s.placement == "IN_PLACE" else 1.0
    key = (s.shade, alpha)
    try:
        if _ghost.get("batches") is None or _ghost.get("batch_key") != key:
            _ghost["batches"] = make_ghost_batches(_ghost["arrays"], s.shade, alpha)
            _ghost["batch_key"] = key
        kind, batches = _ghost["batches"]
        with gpu.matrix.push_pop():            # popped on an exception too: the viewport's matrix stack stays balanced
            gpu.matrix.multiply_matrix(placement_matrix(obj, s.placement))
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(alpha >= 1.0)
            gpu.state.blend_set("ALPHA" if alpha < 1.0 else "NONE")
            gpu.state.face_culling_set("NONE")
            if kind == "TEX":
                mvp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
                texdraw.draw_batches(batches, mvp)
            else:
                sh = draw._tri_shader()
                sh.bind()
                batches.draw(sh)
    except Exception as e:                      # never a traceback per frame: say it once and stop drawing
        print(f"[bz combine] ghost draw failed, preview cleared: {e}")
        clear_ghost()
    finally:
        try:
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("NONE")
        except Exception:
            pass


# ------------------------------------------------------------------------------------------------------ auto refresh
def _mark_dirty():
    _AUTO["last"] = time.monotonic()
    if not _AUTO["pending"]:
        _AUTO["pending"] = True
        bpy.app.timers.register(_auto_tick, first_interval=AUTO_DELAY)


def _modal_running() -> bool:
    wm = bpy.context.window_manager if bpy.context else None
    return any(getattr(w, "modal_operators", None) for w in (wm.windows if wm else []))


def _auto_tick():
    wait = AUTO_DELAY - (time.monotonic() - _AUTO["last"])
    if wait > 0.05:
        return wait
    if _modal_running():
        return 0.3
    _AUTO["pending"] = False
    if not _ghost:
        return None
    scene = bpy.data.scenes.get(_ghost.get("scene", ""))
    obj = ghost_object()
    if scene is None or obj is None or not hasattr(scene, "bz_combine") or not scene.bz_combine.auto_refresh:
        return None
    if obj.mode != "OBJECT":                     # edit-mode data is not in the mesh yet; leaving edit mode re-arms
        return None
    try:
        if state_digest(scene, obj, scene.bz_combine) == _ghost.get("digest"):
            return None
        pr = compute_preview(scene, obj, scene.bz_combine)
        if not pr.ok:
            print(f"[bz combine] auto refresh: {pr.header()}")
    except Exception as e:
        print(f"[bz combine] auto refresh failed: {e}")
    return None


@persistent
def _on_depsgraph(scene, depsgraph):
    if not _ghost or not hasattr(scene, "bz_combine") or not scene.bz_combine.auto_refresh:
        return
    objs = [ghost_object()]
    if scene.bz_combine.other_kind == "OBJECT":
        objs.append(scene.bz_combine.other_object)
    watch = set()
    for ob in objs:
        if ob is not None:
            watch.add(ob.name)
            if ob.data is not None:
                watch.add(ob.data.name)
            if ob.get("bz_rig"):
                watch.add(str(ob["bz_rig"]))
            watch.update(n for n in store.texture_images(ob) if n)     # painted skins
    for u in depsgraph.updates:
        idb = getattr(u.id, "original", u.id)
        if isinstance(idb, (bpy.types.Object, bpy.types.Mesh, bpy.types.Armature, bpy.types.Image)) and idb.name in watch:
            _mark_dirty()
            return


@persistent
def _on_load_post(*_args):
    clear_ghost()


def register_handlers():
    global _handler
    if _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw_ghost, (), "WINDOW", "POST_VIEW")
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister_handlers():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        _handler = None
    for lst, fn in ((bpy.app.handlers.load_post, _on_load_post),
                    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph)):
        for h in [h for h in lst if getattr(h, "__name__", "") == fn.__name__ and getattr(h, "__module__", "") == fn.__module__]:
            lst.remove(h)
    if bpy.app.timers.is_registered(_auto_tick):
        bpy.app.timers.unregister(_auto_tick)
    _AUTO["pending"] = False
    _ghost.clear()
    _GPU_TEX.clear()
    _INDEXING.clear()
