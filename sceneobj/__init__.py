"""Gatherers, characters, structures and props. Modules here register themselves, so ``ic_blender/__init__.py``,
``ui.py`` and the test runner need no edits.

``CORE`` modules import first; ``slice_``, ``ops_``, ``panels_``, ``props_``, ``guards_`` and ``map_`` modules are
discovered, sorted by ``(ORDER, name)``. ``CLASSES`` is in Blender-safe order (property groups, then parent panels),
and ``reload()`` keeps that order. Without Blender only modules declaring ``PURE = True`` load.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import pkgutil
import re
import sys
import traceback

try:
    import bpy
except ImportError:           # the pure tests run without Blender
    bpy = None

CORE = ("model", "subtype", "entity", "ui_kit", "prefs", "importer", "exporter", "compat")
PREFIXES = ("slice_", "ops_", "panels_", "props_", "guards_", "map_")
DEFAULT_ORDER = 50

MODULES: list = []        # the core modules that exist, then the discovered ones in (ORDER, name) order
CLASSES: tuple = ()       # every module's classes in a registration-safe order (empty without Blender)
MISSING_CORE: tuple = ()  # core modules a later slice has not added yet
ERRORS: list = []         # (module, reason) for every module or slice that could not be loaded; empty when all is well

_REGISTERED: list = globals().get("_REGISTERED", [])    # the classes register() registered (unregister() undoes exactly these);
_HOOKED: list = globals().get("_HOOKED", [])            # the modules whose register() hook ran (both survive a reload of this file)
_PURE_CACHE: dict = {}    # source path -> (mtime, declares PURE = True)

# names of model.py that are also reachable as sceneobj.<name> (looked up at use, so a reload of model is never stale)
_FORWARD = frozenset({
    "SLICE_API", "SLICE_MEMBERS", "STAGE_ORDER", "SHARED_KEYS", "SHARED_OWNERS", "GUARD_CODES", "REF_KINDS", "ERROR", "WARN", "INFO",
    "Issue", "issue", "blocking", "counts", "SourceBundle", "OutBundle", "ExportReport", "Slice", "BaseSlice", "refs", "claim_edit",
    "is_claimed", "claimed_by", "declare_shared", "declare_guard_code", "register_slice", "slices", "get_slice", "registry_problems",
    "foreach_get", "foreach_set", "pick_game_dir",
})


def __getattr__(name):
    if name in _FORWARD:
        mod = sys.modules.get(f"{__name__}.model")
        if mod is not None:
            return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------------------------------- finding and loading modules
def _exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(f"{__name__}.{name}") is not None
    except (ImportError, ValueError):
        return False


def declares_pure(name: str) -> bool:
    """Whether the module source declares ``PURE = True`` at its top level (read from the text: the module is not imported)."""
    try:
        spec = importlib.util.find_spec(f"{__name__}.{name}")
    except (ImportError, ValueError):
        return False
    path = spec.origin if spec is not None else None
    if not path or not path.endswith(".py") or not os.path.isfile(path):
        return False
    mtime = os.path.getmtime(path)
    hit = _PURE_CACHE.get(path)
    if hit is None or hit[0] != mtime:
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
        except SyntaxError:
            return True                   # cannot tell: let the import try, so the error is reported with the module's name
        pure = False
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(t, ast.Name) and t.id == "PURE" for t in targets) \
                    and isinstance(getattr(node, "value", None), ast.Constant) and node.value.value is True:
                pure = True
        hit = (mtime, pure)
        _PURE_CACHE[path] = hit
    return hit[1]


def _fail(name: str, reason: str | None = None) -> None:
    """Record that a module could not be loaded and say so on the console; the rest of the add-on keeps working."""
    text = traceback.format_exc()
    reason = reason or (text.strip().splitlines() or ["unknown error"])[-1]
    ERRORS.append((name, reason))
    print(f"sceneobj: the part {name!r} of the add-on could not be loaded:\n{text}", file=sys.stderr)


def _import(name: str):
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except Exception:
        _fail(name)
        return None


def _order_of(mod) -> int:
    order = getattr(mod, "ORDER", DEFAULT_ORDER)
    return order if isinstance(order, int) and not isinstance(order, bool) else DEFAULT_ORDER


def _slices_of(mod) -> list:
    s = getattr(mod, "SLICE", None)
    if s is None:
        return []
    return list(s) if isinstance(s, (list, tuple)) else [s]


# ---------------------------------------------------------------------------------------------------- registration order
def _is_property_group(cls) -> bool:
    return bpy is not None and isinstance(cls, type) and issubclass(cls, bpy.types.PropertyGroup)


def _property_group_targets(cls, by_name: dict) -> list:
    """The property groups that ``cls`` points at through ``PointerProperty(type=...)`` / ``CollectionProperty(type=...)``."""
    out = []
    for klass in cls.__mro__:
        for ann in klass.__dict__.get("__annotations__", {}).values():
            if isinstance(ann, str):                         # `from __future__ import annotations`: the call is still text
                out.extend(by_name.get(n.rsplit(".", 1)[-1]) for n in re.findall(r"\btype\s*=\s*([A-Za-z_][\w.]*)", ann))
            else:
                kw = getattr(ann, "keywords", None)
                target = kw.get("type") if isinstance(kw, dict) else None
                if isinstance(target, type):
                    out.append(target)
    return [t for t in out if t is not None and t is not cls]


def _sort_property_groups(groups: list) -> list:
    """Stable dependency order: a group comes after every group it points at. A cycle cannot register; it is reported."""
    by_name = {g.__name__: g for g in groups}
    deps = {g: [t for t in _property_group_targets(g, by_name) if t in groups] for g in groups}
    out, left = [], list(groups)
    while left:
        ready = next((g for g in left if all(d in out for d in deps[g])), None)
        if ready is None:
            ERRORS.append(("sceneobj", "property groups point at each other: " + ", ".join(g.__name__ for g in left)))
            out.extend(left)
            break
        out.append(ready)
        left.remove(ready)
    return out


def _sort_child_panels(classes: list) -> list:
    """Stable order in which a panel with a ``bl_parent_id`` always follows its parent (Blender refuses a child first)."""
    by_id = {getattr(c, "bl_idname", None): c for c in classes if getattr(c, "bl_idname", None)}
    out, state = [], {}

    def emit(c):
        if state.get(c) == 2:
            return
        if state.get(c) == 1:
            ERRORS.append(("sceneobj", f"panel {getattr(c, 'bl_idname', c.__name__)} is its own ancestor"))
            return
        state[c] = 1
        parent = by_id.get(getattr(c, "bl_parent_id", None) or None)
        if parent is not None and parent is not c:
            emit(parent)
        state[c] = 2
        out.append(c)

    for c in classes:
        emit(c)
    return out


def order_classes(classes) -> list:
    """``classes`` without duplicates, in the order Blender can register them: property groups (dependencies first), then the rest
    with every child panel after its parent. Everything else keeps its given order."""
    seen = []
    for c in classes:
        if c not in seen:
            seen.append(c)
    groups = [c for c in seen if _is_property_group(c)]
    others = [c for c in seen if not _is_property_group(c)]
    return _sort_property_groups(groups) + _sort_child_panels(others)


# ---------------------------------------------------------------------------------------------------- discovery
def discover() -> None:
    """(Re)build ``MODULES``, ``CLASSES``, ``MISSING_CORE``, ``ERRORS`` and the slice registry from the modules on disk now."""
    global MODULES, CLASSES, MISSING_CORE
    del ERRORS[:]
    model = _import("model")
    if model is None:
        MODULES, CLASSES, MISSING_CORE = [], (), CORE
        return
    model.clear_slices()
    core, missing = [model], []
    for name in CORE[1:]:
        if not _exists(name):
            missing.append(name)
        elif bpy is not None or declares_pure(name):
            mod = _import(name)
            if mod is not None:
                core.append(mod)
    extra = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name in CORE or not info.name.startswith(PREFIXES):
            continue
        if bpy is not None or declares_pure(info.name):
            mod = _import(info.name)
            if mod is not None:
                extra.append(mod)
    extra.sort(key=lambda m: (_order_of(m), m.__name__))
    MODULES = [*core, *extra]
    MISSING_CORE = tuple(missing)
    for mod in MODULES:
        for s in _slices_of(mod):
            for problem in model.register_slice(s, owner=mod.__name__):
                ERRORS.append((mod.__name__.rsplit(".", 1)[-1], f"slice {getattr(s, 'name', '?')!r}: {problem}"))
    classes = [c for mod in MODULES for c in getattr(mod, "CLASSES", ())]
    CLASSES = tuple(order_classes(classes)) if bpy is not None else ()


# ---------------------------------------------------------------------------------------------------- register / unregister
_BASES = ("Operator", "Macro", "Panel", "Menu", "Header", "UIList", "PropertyGroup", "AddonPreferences", "FileHandler")


def _base_type(cls):
    for n in _BASES:
        base = getattr(bpy.types, n, None)
        if base is not None and isinstance(cls, type) and issubclass(cls, base):
            return base
    return None


def rna_identifier(cls) -> str:
    """The name Blender registers ``cls`` under: an operator ``a.b`` is ``A_OT_b``, a property group or add-on preferences class is
    its class name, every other kind its ``bl_idname``."""
    idname = getattr(cls, "bl_idname", "") or ""
    base = _base_type(cls)
    if base is not None and base.__name__ in ("Operator", "Macro") and idname:
        head, _, tail = idname.partition(".")
        return f"{head.upper()}_OT_{tail}"
    if base is not None and base.__name__ in ("PropertyGroup", "AddonPreferences"):
        return cls.__name__
    return idname or cls.__name__


def is_registered(cls) -> bool:
    """Whether Blender currently has exactly this class object registered."""
    base = _base_type(cls)
    return base is not None and base.bl_rna_get_subclass_py(rna_identifier(cls)) is cls


def registered_classes() -> tuple:
    return tuple(_REGISTERED)


def _rollback() -> list:
    """Undo what ``register()`` did (hooks, then classes, in reverse) and return one line per step that failed."""
    problems = []
    for mod in reversed(_HOOKED):
        hook = getattr(mod, "unregister", None)
        if callable(hook):
            try:
                hook()
            except Exception as e:
                problems.append(f"{mod.__name__}.unregister(): {type(e).__name__}: {e}")
    del _HOOKED[:]
    for cls in reversed(_REGISTERED):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            problems.append(f"unregister {cls.__name__}: {type(e).__name__}: {e}")
    del _REGISTERED[:]
    return problems


def register() -> None:
    """Register every class (property groups first), then call every module's ``register()`` hook. A failure undoes what was done."""
    if bpy is None:
        raise RuntimeError("sceneobj.register() needs Blender")
    if _REGISTERED or _HOOKED:
        problems = _rollback()          # registered twice without unregistering: start clean
        if problems:
            print("sceneobj: while undoing the earlier registration:\n  " + "\n  ".join(problems), file=sys.stderr)
    stray = []
    try:
        for cls in CLASSES:
            parent = getattr(cls, "bl_parent_id", "") or ""
            if parent and issubclass(cls, bpy.types.Panel) and bpy.types.Panel.bl_rna_get_subclass_py(parent) is None:
                # a sub-panel whose parent does not exist cannot register: say so, leave that one panel out, keep the rest of the add-on
                reason = f"the panel {cls.bl_idname} needs the panel {parent}, which is not registered"
                ERRORS.append((cls.__module__.rsplit(".", 1)[-1], reason))
                print(f"sceneobj: {reason}; it is left out", file=sys.stderr)
                continue
            try:
                bpy.utils.register_class(cls)
            except Exception:
                # Blender can leave a refused class half registered; take it out again (its own error is the one that matters)
                try:
                    bpy.utils.unregister_class(cls)
                except Exception as e:
                    stray.append(f"unregister {cls.__name__} after it was refused: {type(e).__name__}: {e}")
                raise
            _REGISTERED.append(cls)
        for mod in MODULES:
            hook = getattr(mod, "register", None)
            if callable(hook):
                hook()
                _HOOKED.append(mod)
    except Exception:
        problems = stray + _rollback()
        if problems:
            print("sceneobj: while undoing a failed registration:\n  " + "\n  ".join(problems), file=sys.stderr)
        raise


def unregister() -> None:
    """Undo ``register()`` completely; every step is tried, and a step that failed is reported at the end."""
    problems = _rollback()
    if problems:
        raise RuntimeError("sceneobj could not unregister cleanly:\n  " + "\n  ".join(problems))


def _package_names() -> list:
    """The name of every module file of the package that is on disk now."""
    return sorted(info.name for info in pkgutil.iter_modules(__path__))


def _sibling_imports(name: str) -> set:
    """The sibling modules that module ``name`` imports when it is imported (``from . import a`` / ``from .a import b``)."""
    spec = importlib.util.find_spec(f"{__name__}.{name}")
    path = spec.origin if spec is not None else None
    if not path or not path.endswith(".py") or not os.path.isfile(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    out = set()

    def walk(body):
        for node in body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                out.update([node.module.split(".")[0]] if node.module else [a.name for a in node.names])
            elif isinstance(node, ast.Try):
                walk(node.body)
                for h in node.handlers:
                    walk(h.body)
                walk(node.orelse)
                walk(node.finalbody)
            elif isinstance(node, ast.If):
                walk(node.body)
                walk(node.orelse)

    walk(tree.body)
    return out


def _reload_order() -> list:
    """Every imported module of the package, each after the siblings it imports (so a name it took from one is the reloaded one),
    ties in registry order. This covers helper modules that are not registered (a module missing from a reload list runs stale code)."""
    present = [n for n in _package_names() if f"{__name__}.{n}" in sys.modules]
    deps = {n: _sibling_imports(n) & set(present) - {n} for n in present}
    rank = {m.__name__.rsplit(".", 1)[-1]: i for i, m in enumerate(MODULES)}
    out, left = [], list(present)
    while left:
        ready = [n for n in left if deps[n] <= set(out)]
        if not ready:                     # an import cycle: break it in registry order
            ready = left
        first = min(ready, key=lambda n: (rank.get(n, len(rank)), n))
        out.append(first)
        left.remove(first)
    return out


def reload() -> None:
    """Reload every module of the package, each after the modules it imports, then discover again (so a new, renamed or removed module
    is picked up too)."""
    before = len(ERRORS)
    on_disk = set(_package_names())
    for key in [k for k in sys.modules if k.startswith(__name__ + ".") and k[len(__name__) + 1:].split(".")[0] not in on_disk]:
        del sys.modules[key]              # a file that was removed or renamed: that module is simply gone
    for name in _reload_order():
        try:
            importlib.reload(sys.modules[f"{__name__}.{name}"])
        except Exception:
            _fail(name)
    failed = ERRORS[before:]              # a module whose reload failed (a stale copy stays in sys.modules: say so)
    discover()
    ERRORS.extend(e for e in failed if e not in ERRORS)


def status() -> dict:
    """A plain summary (the preferences panel and the tests read it)."""
    model = sys.modules.get(f"{__name__}.model")
    return {
        "modules": [m.__name__.rsplit(".", 1)[-1] for m in MODULES],
        "missing_core": list(MISSING_CORE),
        "errors": list(ERRORS),
        "slices": [s.name for s in model.slices()] if model is not None else [],
        "problems": model.registry_problems() if model is not None else [],
        "classes": len(CLASSES),
        "registered": len(_REGISTERED),
        "pure": bpy is None,
    }


discover()
