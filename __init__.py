"""IC Blender -- make and edit Impossible Creatures models and maps in Blender.

Edit IC creature geometry as what it actually is: a control-point pool driving bicubic
Bezier patches. The editable object's vertices ARE the control points (1:1, lossless), so
Blender's full native edit-mode toolset applies directly, while a gpu overlay shows the
exact evaluated surface live. No cage, no Catmull-Clark, no information loss.

Reuses ICParse as the single source of truth for the .sgm format: in the repo the sibling ICParse
project, in a released .zip the ICParse wheel it carries (with combotest, construct and lupa). An ICParse
or combotest that an earlier load left in memory is unloaded first when it is not the copy this add-on
would load now (an upgrade in the same Blender session; see _unload_stale_libraries).
"""
from __future__ import annotations

import importlib
import os
import sys

import bpy

bl_info = {
    "name": "IC Blender",
    "author": "pyIC",
    "version": (0, 9, 2),
    "blender": (5, 3, 0),
    "location": "View3D > Sidebar > IC; File > New, Import and Export",
    "description": "Make and edit Impossible Creatures models and maps: creatures, gatherers, characters, buildings, props and maps",
    "category": "Import-Export",
}


# Where each external dependency came from, filled by _setup_paths() (read by combocore.status() and the
# tests): name -> {"source": "importable" | "added" | "missing", "path": dir or None, "note": str}.
PATHS_STATUS: dict = {}


def _platform_tag() -> str:
    """The compiled-deps directory name for this interpreter, e.g. ``cp313-linux_x86_64``."""
    import sysconfig
    plat = sysconfig.get_platform().replace("-", "_").replace(".", "_")
    return f"cp{sys.version_info.major}{sys.version_info.minor}-{plat}"


def _importable(name: str):
    """The sys.path directory ``name`` already imports from as a real package, or None (a namespace-package
    match, e.g. the repo's ``ICParse/`` project directory on sys.path, does not count). Nothing is imported."""
    import importlib.util
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin or not os.path.exists(spec.origin):
        return None
    return os.path.dirname(os.path.dirname(os.path.abspath(spec.origin)))


def _add_path(name: str, candidates, marker: str, note_missing: str) -> None:
    """Import-first: leave ``name`` alone when it already imports (an installed extension's wheels, a
    venv, or a path added on an earlier load); otherwise put the first candidate directory that holds
    ``marker`` at the front of sys.path."""
    found = _importable(name)
    if found:
        PATHS_STATUS[name] = {"source": "importable", "path": found, "note": ""}
        return
    for cand in candidates:
        if cand and os.path.exists(os.path.join(cand, marker)):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            PATHS_STATUS[name] = {"source": "added", "path": cand, "note": ""}
            return
    PATHS_STATUS[name] = {"source": "missing", "path": None,
                          "note": note_missing + " (looked in: " + ", ".join(c for c in candidates if c) + ")"}


def _setup_paths():
    """Make the add-on's dependencies importable in Blender's python, import-first (anything that
    already imports is left alone, so an installed extension's wheels win):

    1. ``construct`` from the bundled ``_deps/`` (pure python, committed);
    2. ``ICParse`` (the repo's format source of truth): ``$BZ_ICPARSE``, else the sibling ``../ICParse``;
    3. ``combotest`` (the combiner): ``$BZ_COMBOTEST``, else the sibling ``../pyRDN/combotest`` project
       dir (never ``../pyRDN``, which would make ``combotest`` a namespace package). An explicit
       ``$BZ_COMBOTEST`` is the only place looked in;
    4. ``lupa`` (compiled; runs the combiner's attrcombiner.lua): ``$BZ_DEPS_BIN``, else the git-ignored
       ``_deps_bin/<cpXY-platform>/`` that ``tools/fetch_blender_deps.py`` fills from PyPI.

    combotest and lupa are optional: the add-on registers without them, and what is missing is
    recorded in ``PATHS_STATUS`` (never raised here)."""
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    _add_path("construct", [os.path.join(here, "_deps")], os.path.join("construct", "__init__.py"),
              "A helper library (construct) was not found: reinstall the add-on")
    env = os.environ.get("BZ_ICPARSE")
    _add_path("ICParse", [env] if env else [os.path.join(parent, "ICParse")],
              os.path.join("ICParse", "__init__.py"), "The add-on's file-format library was not found: reinstall the add-on")
    env = os.environ.get("BZ_COMBOTEST")
    _add_path("combotest", [env] if env else [os.path.join(parent, "pyRDN", "combotest")],
              os.path.join("combotest", "__init__.py"),
              "The creature combiner was not found, so Combine Preview and the combine checks are unavailable: "
              "reinstall the add-on")
    env = os.environ.get("BZ_DEPS_BIN")
    _add_path("lupa", [env] if env else [os.path.join(here, "_deps_bin", _platform_tag())],
              os.path.join("lupa", "__init__.py"),
              "A helper library (lupa) was not found, so combined ratings, cost and rank cannot be worked out: "
              "reinstall the add-on")


# ---- libraries an earlier load left in memory ----------------------------------------------------------------------------------
# The libraries a release carries as wheels: the module name, the name their .dist-info folder starts with, and the libraries
# each one imports (combotest imports ICParse, so it is unloaded whenever ICParse is). ICParse comes first: it holds the record.
_LIBRARIES = (("ICParse", "icparse", ()), ("combotest", "combotest", ("ICParse",)))
# set on the ICParse package after this add-on loaded: {library: the .dist-info folders beside the copy it used, or would use for a
# library it imports only when needed (combotest)}
_LOADED_FILES = "_bz_loaded_files"
# name -> why it was unloaded, for every library that an earlier load left in memory and this load unloaded so that the installed
# files are read instead (empty after an ordinary start of Blender; the extension install test reads it)
UNLOADED: dict = {}


def _package_dir(module):
    """The folder a loaded package's files are in (its real path), or None (a namespace package has no file)."""
    f = getattr(module, "__file__", None)
    return os.path.dirname(os.path.realpath(f)) if f else None


def _package_dir_on_disk(name: str):
    """The folder a fresh ``import name`` would load the package from now: sys.path is searched again and sys.modules is ignored
    (``importlib.util.find_spec`` answers with the module already loaded). None when nothing on sys.path holds it."""
    import importlib.machinery
    try:
        spec = importlib.machinery.PathFinder.find_spec(name)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin or not os.path.isfile(spec.origin):
        return None
    return os.path.dirname(os.path.realpath(spec.origin))


def _dist_infos(package_dir, dist: str) -> tuple:
    """The ``.dist-info`` folders of ``dist`` beside ``package_dir``, which say which version was installed there. A release's
    ICParse and combotest wheels carry a label made from their contents (``icparse-0.1.0+h<12 hex digits>.dist-info``), so the
    names change whenever the files do. () when there is none (the repo's own copy)."""
    if not package_dir:
        return ()
    try:
        names = os.listdir(os.path.dirname(package_dir))
    except OSError:
        return ()
    return tuple(sorted(n for n in names if n.lower().startswith(dist + "-") and n.endswith(".dist-info")))


def _unload_stale_libraries() -> None:
    """Unload ICParse and combotest (each with its submodules) when the copy in memory is not the one this add-on would load now.

    Installing a new version over an enabled one, Blender disables the old add-on, removes only the add-on's own modules
    (``bl_ext.<repository>.ic_blender`` and below) from sys.modules, installs the new wheels and enables the new add-on
    in the same session (Blender 5.3: ``_preferences_ensure_disabled`` and ``exec_command_finish`` in
    ``scripts/addons_core/bl_pkg/bl_extension_ops.py``). Without this the new add-on ran against the old libraries until Blender
    was restarted, and when it needed a newer library name while it loaded, the enable failed and Blender's wheel sync then
    emptied the shared site-packages.

    A library is unloaded when it was loaded from another folder than the one sys.path gives now, or when that folder holds
    another version (other ``.dist-info`` folders) than the one this add-on recorded when it loaded (:func:`_record_loaded_libraries`),
    which includes a copy that an add-on version recording nothing (0.3.1 and older) loaded. A library that nothing on disk would
    replace is kept, and so is the repo's own copy (no ``.dist-info`` to compare: restart Blender after editing it).
    construct and lupa are left alone: their versions are pinned, and lupa is compiled, which Python cannot load twice."""
    if not any(name in sys.modules for name, _dist, _needs in _LIBRARIES):
        return
    importlib.invalidate_caches()                       # the wheel sync replaced files: look at the folders again
    record = getattr(sys.modules.get(_LIBRARIES[0][0]), _LOADED_FILES, None)
    record = record if isinstance(record, dict) else {}
    why = {}
    for name, dist, needs in _LIBRARIES:
        loaded = sys.modules.get(name)
        if loaded is None:
            continue
        on_disk = _package_dir_on_disk(name)
        if on_disk is None:
            continue
        here, installed = _package_dir(loaded), _dist_infos(on_disk, dist)
        if here != on_disk:
            why[name] = f"it was loaded from {here or 'no folder'}, and the add-on's copy is in {on_disk}"
        elif installed and record.get(name) != installed:
            why[name] = "the installed files are another version than the ones loaded earlier in this session"
        elif any(n in why for n in needs):
            why[name] = "it uses " + " and ".join(n for n in needs if n in why) + ", which is loaded again"
    for name, _dist, _needs in _LIBRARIES:
        if name not in why:
            continue
        for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
            sys.modules.pop(key, None)
        UNLOADED[name] = why[name]
        print(f"IC Blender: {name} from an earlier load was still in memory ({why[name]}); it is loaded again from the "
              f"installed files.", flush=True)


def _record_loaded_libraries() -> None:
    """Record on ICParse which version of each library this load used (the ``.dist-info`` folders beside it), for the next load to
    compare with (see :func:`_unload_stale_libraries`). A library not imported yet (combotest is imported when the combiner is first
    used) is recorded by the folder a first import would load it from: until the next install that is the copy it gets."""
    root = sys.modules.get(_LIBRARIES[0][0])
    if root is None:
        return
    record = {}
    for name, dist, _needs in _LIBRARIES:
        mod = sys.modules.get(name)
        record[name] = _dist_infos(_package_dir(mod) if mod is not None else _package_dir_on_disk(name), dist)
    try:
        setattr(root, _LOADED_FILES, record)
    except (AttributeError, TypeError):
        pass


_unload_stale_libraries()
_setup_paths()

from . import (core, boner, fastsurf, material, texdraw, rig, actions, limbs, store, combocore, patchrules,  # noqa: E402
               patchsel, patchfeel, build, scene, scene_damage, scenesafety, scenecheck, structure, topology, draw, props, ops, limbops, connops, boneops,
               ikops, newcreature, newproject, edittools, sculptops, fitops, retargetops, landmarkops, texops, topoops,
               trimops, gnsurface, guards, combopreview, startcheck, startops, gameexport, gamecopyops, gameshare, gameinstall, fxcore, fxbake, fxpanel, fxpreview, motionscore, motionspanel, clipborrow, sceneobj, ui)

from . import hardedges  # noqa: E402  (round 2, r2shape: Mark Sharp edges and material slots -> smoothing groups and materials)

# reload order = dependency order (every add-on module must be listed, or an in-place reinstall runs stale code).
# External packages (ICParse, combotest) are not reloaded: restart Blender after editing them.
# sceneobj is listed once: its own modules are found and reloaded by sceneobj.reload() (below), each after the modules it imports.
_MODULES = (structure, topology, core, boner, fastsurf, material, texdraw, rig, actions, limbs, store, combocore,
            patchrules, patchsel, patchfeel, build, scene, scene_damage, scenesafety, scenecheck, draw, gnsurface, props, ops, limbops, connops, boneops,
            ikops, newcreature, newproject, edittools, sculptops, fitops, retargetops, landmarkops, texops, topoops, trimops,
            guards, combopreview, startcheck, startops, gameexport, gamecopyops, gameshare, gameinstall, fxcore, fxbake, fxpanel, fxpreview, motionscore, motionspanel, clipborrow, sceneobj, ui)
_MODULES = (*_MODULES[:-2], hardedges, *_MODULES[-2:])      # r2shape: after the modules it builds on, before the scene objects and ui


def _reload():
    for m in _MODULES:
        importlib.reload(m)
        if m is sceneobj:
            sceneobj.reload()      # its modules (and the kit ui draws with) reload after the modules they build on, before ui


if "_BZ_LOADED" in globals():
    _reload()
_BZ_LOADED = True
_record_loaded_libraries()          # after the imports (and the reload): the libraries the add-on's modules now hold

# (the Combine Preview settings PropertyGroup is registered by props.register, before Scene.bz_combine)
# (the classes of the sceneobj package are not listed here: sceneobj.register() registers them itself, property groups first)
_CLASSES = (*ops.CLASSES, *limbops.CLASSES, *connops.CLASSES, *boneops.CLASSES, *ikops.CLASSES, *newcreature.CLASSES, *newproject.CLASSES, *edittools.CLASSES,
            *sculptops.CLASSES, *fitops.CLASSES, *retargetops.CLASSES, *landmarkops.CLASSES,
            *texops.CLASSES, *patchfeel.CLASSES, *patchsel.CLASSES, *topoops.CLASSES, *trimops.CLASSES,
            *gnsurface.CLASSES, *combopreview.CLASSES, *startops.CLASSES, *gameexport.CLASSES, *ui.CLASSES, *gameinstall.CLASSES, *scene_damage.CLASSES, *fxpanel.CLASSES, *fxpreview.CLASSES, *motionspanel.CLASSES, *clipborrow.CLASSES)

# --- r2stats: the Creature Stats panel (statspanel.py) -- its module, classes and Object.bz_stats
from . import statscore, statspanel  # noqa: E402
_MODULES = _MODULES[:-2] + (statscore, statspanel) + _MODULES[-2:]
_CLASSES = _CLASSES + tuple(statspanel.CLASSES)
# --- end r2stats
_CLASSES = (*_CLASSES, *hardedges.CLASSES)                # r2shape


def register():
    sceneobj.ui_kit.add_purposes()        # the first line of every panel says what the panel is for
    props.register()
    scene_damage.register()               # the damage preview's health slider (WindowManager)
    gameinstall.register()                # the Game panel's item-folder picker (Scene)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    fxpanel.register()                    # Object.bz_fx: the Effects panel's events and effect rows
    statspanel.register()                 # Object.bz_stats: the Creature Stats panel's rows (r2stats)
    motionspanel.register()               # Object.bz_motions: the Motions box's rows
    bpy.types.TOPBAR_MT_file_import.append(ops.menu_import)
    bpy.types.TOPBAR_MT_file_export.append(ops.menu_export)
    # (the Add menu entry newcreature.menu_add is not offered since the round 2 tidy: the IC Model tab has the same button)
    bpy.types.TOPBAR_MT_file_new.append(newproject.menu_new)
    draw.register()
    ops.register_handlers()
    guards.register_handlers()            # D10: the export refusals checked at action time (depsgraph_update_post)
    combopreview.register_handlers()
    topoops.register_macros()             # the drag macros' steps (the tool, then Blender's translate)
    patchsel.register_keymap()            # X / Delete, H, Shift+H, Alt+H on a creature in Edit Mode
    patchsel.register_handlers()          # corner quads for creatures saved before B6
    gnsurface.register_handlers()         # D9: keep the evaluated surface's face tables with the topology
    sceneobj.register()                   # last: its classes, then every module's own register() hook (sceneobj/__init__.py)


def unregister():
    try:
        sceneobj.unregister()
    finally:
        _unregister_creature()      # whatever sceneobj says, the rest of the add-on must come out too


def _unregister_creature():
    gnsurface.unregister_handlers()
    patchsel.unregister_handlers()
    patchsel.unregister_keymap()
    combopreview.unregister_handlers()
    guards.unregister_handlers()
    ops.unregister_handlers()
    draw.unregister()
    bpy.types.TOPBAR_MT_file_new.remove(newproject.menu_new)
    bpy.types.TOPBAR_MT_file_export.remove(ops.menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(ops.menu_import)
    motionspanel.unregister()
    fxpanel.unregister()
    statspanel.unregister()               # r2stats
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    gameinstall.unregister()
    scene_damage.unregister()
    props.unregister()


if __name__ == "__main__":
    register()
