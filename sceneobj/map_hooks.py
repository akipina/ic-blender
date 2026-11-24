"""sceneobj.map_hooks -- the extension points of the map tools (no operators and no panels in this module).

In plain words. The map import (``ops_map_import``) and the map save (``ops_map_export``) do the core work: the ground, the water,
the ground textures and the placed objects. A feature that adds more to a map (triggers, regions, paths, a new map ...) lives in
a module of its own and plugs into them here, so no feature has to edit the files of another:

* ``register_import_step(name, fn, order=100)`` -- ``fn(ictx)`` runs after the core import of a map, in ``order`` (then in the
  order registered). ``ictx`` is an :class:`ImportContext`.
* ``register_save_step(name, fn, order=100)`` -- ``fn(sctx)`` runs during a save, after the core edits are made and before
  anything is written. ``sctx`` is a :class:`SaveContext`: a step may replace the map's bytes, add or replace a file beside the
  map, add lines to the save report, or refuse the save. A step whose feature was not touched must change nothing.
* ``register_check(name, fn)`` -- ``fn(cctx)`` returns a list of problems (``problem(...)`` dicts); ``run_checks(context, top)``
  runs every check and returns all the problems, for the user-facing list.
* ``MAP_PANEL_ID`` -- the ``bl_idname`` of the Map panel: a sub-panel names it as its ``bl_parent_id``.
* ``import_map_bytes(context, data, name, options)`` -- the map import from bytes in memory.

Registering the same ``name`` twice replaces the first (so a reloaded module never runs twice). A step that raises is reported and
skipped on import; on save it refuses the save (nothing is written).

How a module gets loaded: any module in ``sceneobj`` whose file name starts with ``map_`` is imported when the add-on starts (the
package registry discovers it, like ``ops_`` and ``panels_`` modules), so a feature file ``map_triggers.py`` registers its steps at
the top level of the module, no other file needs to name it. This module's ORDER is 10, so it is (re)loaded before every feature
module (their default ORDER is 50).
"""
from __future__ import annotations

import base64
import os
import sys
import traceback
import zlib
from dataclasses import dataclass, field

ORDER = 10

MAP_PANEL_ID = "BZ_PT_map"            # panels_map.BZ_PT_map: a feature's sub-panel sets bl_parent_id = MAP_PANEL_ID
KEY_SOURCE_TEXT = "ic_map_source_text"  # on a map collection opened from memory: the name of the text that holds its bytes

ERROR = "error"
WARNING = "warning"

_IMPORT_STEPS: list = []             # [(order, seq, name, fn)]
_SAVE_STEPS: list = []
_CHECKS: list = []                   # [(seq, name, fn)]
_SEQ = [0]


class Refused(Exception):
    """Raised by ``SaveContext.refuse``: the save stops, nothing is written. ``args[0]`` is the reason in plain words."""


# ---------------------------------------------------------------------------------------------------- registering
def _add(table: list, name: str, fn, order: int | None) -> None:
    if not callable(fn):
        raise TypeError(f"the step {name!r} must be a function")
    _SEQ[0] += 1
    table[:] = [row for row in table if row[2 if order is not None else 1] != name]
    table.append((order, _SEQ[0], name, fn) if order is not None else (_SEQ[0], name, fn))


def register_import_step(name: str, fn, order: int = 100) -> None:
    """Run ``fn(ictx)`` after the core import of every map (lower ``order`` first)."""
    _add(_IMPORT_STEPS, name, fn, int(order))


def register_save_step(name: str, fn, order: int = 100) -> None:
    """Run ``fn(sctx)`` during every save, after the core edits and before anything is written (lower ``order`` first)."""
    _add(_SAVE_STEPS, name, fn, int(order))


def register_check(name: str, fn) -> None:
    """Add a check: ``fn(cctx)`` returns a list of :func:`problem` dicts (an empty list: all well)."""
    _add(_CHECKS, name, fn, None)


def unregister_step(name: str) -> None:
    """Remove every import step, save step and check called ``name`` (for tests and a feature that unloads)."""
    _IMPORT_STEPS[:] = [r for r in _IMPORT_STEPS if r[2] != name]
    _SAVE_STEPS[:] = [r for r in _SAVE_STEPS if r[2] != name]
    _CHECKS[:] = [r for r in _CHECKS if r[1] != name]


def registered() -> dict:
    """``{"import": [names in run order], "save": [...], "checks": [...]}``."""
    return {"import": [r[2] for r in sorted(_IMPORT_STEPS, key=lambda r: r[:2])],
            "save": [r[2] for r in sorted(_SAVE_STEPS, key=lambda r: r[:2])],
            "checks": [r[1] for r in sorted(_CHECKS)]}


def snapshot() -> tuple:
    """The registered steps, to hand back to :func:`restore` (a test brackets itself with these)."""
    return list(_IMPORT_STEPS), list(_SAVE_STEPS), list(_CHECKS)


def restore(state: tuple) -> None:
    _IMPORT_STEPS[:] = state[0]
    _SAVE_STEPS[:] = state[1]
    _CHECKS[:] = state[2]


# ---------------------------------------------------------------------------------------------------- the import
@dataclass
class ImportContext:
    """What an import step gets (``ictx``).

    * ``context`` -- the Blender context;
    * ``top`` -- the map's collection (``Map <name>``; its custom properties say where the map came from);
    * ``name`` -- the map's name as shown;
    * ``source_bytes`` -- the map file's bytes as they were read (or, when only the parsed board was given, the board written back);
    * ``view`` -- the ``ICParse.sgb.view.MapView`` the import used; ``board`` -- its editable ``ICParse.sgb.Sgb`` (``view.board``);
    * ``options`` -- the import options as a dict: ``load_models``, ``show_water``, ``show_creatures``, ``game_look``;
    * ``terrain`` -- the terrain object; ``kinds`` -- ``{kind: collection}`` for terrain, water, structures, nature, units, other;
    * ``game_dir`` -- the game folder or None; ``data`` -- the game's file resolver (``view.game_data``) or None;
    * ``report`` -- the import report dict (a step may add its own key; ``warn`` adds a note line to it);
    * ``tmp`` -- a temp folder that exists for the duration of the import;
    * ``siblings`` -- ``{(folder, text after the map's name): bytes}``: the files of a map made in memory that should sit beside it
      (a cropped map keeps the old map's ``.trg``, ``.dlg``, ``.na``); empty for a map opened from a file."""
    context: object
    top: object
    name: str
    source_bytes: bytes
    view: object
    board: object
    options: dict
    terrain: object
    kinds: dict
    game_dir: str | None
    data: object
    report: dict
    tmp: str
    siblings: dict = field(default_factory=dict)

    def warn(self, text: str) -> None:
        """Add a note (plain words) to the import report."""
        self.report.setdefault("problems", []).append(text)


def run_import_steps(ictx: ImportContext) -> list:
    """Run every import step in order. A step that raises is reported (``ictx.warn`` + the console) and skipped; returns the names
    of the steps that failed."""
    failed = []
    for _order, _seq, name, fn in sorted(_IMPORT_STEPS, key=lambda r: r[:2]):
        try:
            fn(ictx)
        except Exception as exc:                          # noqa: BLE001
            failed.append(name)
            ictx.warn(f"The map feature '{name}' could not be added to the import: {exc}")
            print(f"map_hooks: import step {name!r} failed:\n{traceback.format_exc()}", file=sys.stderr)
    return failed


# ---------------------------------------------------------------------------------------------------- the save
@dataclass
class SaveContext:
    """What a save step gets (``sctx``). Nothing has been written yet.

    * ``context``, ``top`` -- the Blender context and the map's collection;
    * ``name`` -- the file name stem the map is saved under; ``group`` -- ``"mp"`` or ``"sp"``; ``folder`` -- the folder the map file
      goes into; ``target`` -- the map file's path; ``ready_to_drop`` -- whether the save makes a ``Data/scenarios/<group>`` folder;
    * ``source`` -- where the map was opened from (a path, ``<archive>|<member>``, or "" for a map made in memory);
    * ``original`` -- the map file's bytes as it was opened (never changed); ``view`` -- the ``MapView`` of them;
    * ``sgb`` -- the bytes the save will write as the map file: the original with the core edits applied. A step may replace it with
      another ``bytes``; later steps see the replacement;
    * ``report`` -- the save report as far as steps are concerned: ``report["changes"]`` and ``report["warnings"]`` are lists of
      plain-words lines a step appends to (they are shown in the save report and the README of a ready-to-drop folder);
    * ``files`` -- ``{file name: bytes}`` of the files written beside the map, the names relative to ``folder`` (a file in the
      ``loading`` folder is ``loading/<name>.<ext>``). It is pre-filled with the files the save copies today (the map's ``.trg``,
      dialogue, pictures, renamed to ``name``), so a step can replace ``files[name + ".trg"]`` or add a new file. A value of ``None``
      drops a file; the map file itself is ``sgb``, not an entry here;
    * ``library`` -- the report ``edit.apply_edits`` returned for the core edits (the numbers given to the objects it added, ...);
    * ``refuse(reason)`` -- stop the save with nothing written; ``reason`` is shown as it is."""
    context: object
    top: object
    name: str
    group: str
    folder: str
    target: str
    ready_to_drop: bool
    source: str
    original: bytes
    view: object
    sgb: bytes
    report: dict
    files: dict
    initial_files: dict = field(default_factory=dict, repr=False)
    library: object = None                                # the report of the core edits (``edit.apply_edits``); None when not known

    def refuse(self, reason: str):
        raise Refused(reason)

    @property
    def sgb_changed(self) -> bool:
        return self.sgb != self.original

    def add_change(self, line: str) -> None:
        self.report.setdefault("changes", []).append(line)

    def add_warning(self, line: str) -> None:
        self.report.setdefault("warnings", []).append(line)


def run_save_steps(sctx: SaveContext) -> None:
    """Run every save step in order. Raises :class:`Refused` (a step refused, or one raised: its reason names the step)."""
    for _order, _seq, name, fn in sorted(_SAVE_STEPS, key=lambda r: r[:2]):
        try:
            fn(sctx)
        except Refused:
            raise
        except Exception as exc:                          # noqa: BLE001
            print(f"map_hooks: save step {name!r} failed:\n{traceback.format_exc()}", file=sys.stderr)
            raise Refused(f"The map feature '{name}' could not be saved: {exc}. Nothing was written.") from exc
        if not isinstance(sctx.sgb, (bytes, bytearray)):
            raise Refused(f"The map feature '{name}' left the map in a state that cannot be written. Nothing was written.")
        sctx.sgb = bytes(sctx.sgb)
    for fname, data in sctx.files.items():
        norm = str(fname).replace("\\", "/")
        if (not norm or norm.startswith("/") or ".." in norm.split("/") or os.path.isabs(norm) or ":" in norm
                or norm.lower() == f"{sctx.name}.sgb".lower()):
            raise Refused(f"A map feature asked for the file name '{fname}' beside the map, which is not allowed. Nothing was written.")
        if data is not None and not isinstance(data, (bytes, bytearray)):
            raise Refused(f"The file '{fname}' beside the map is not bytes. Nothing was written.")


# ---------------------------------------------------------------------------------------------------- the checks
@dataclass
class CheckContext:
    """What a check gets (``cctx``): ``context``, ``top`` (the map's collection), ``name`` and ``terrain`` (the terrain object or None).
    A check reads the scene and returns problems; it changes nothing."""
    context: object
    top: object
    name: str
    terrain: object


def problem(severity: str, text: str, where: str | None = None) -> dict:
    """One problem: ``severity`` is ``"error"`` (the map would not work or cannot be saved) or ``"warning"``; ``text`` plain words;
    ``where`` an object's name or a place in words, or None."""
    if severity not in (ERROR, WARNING):
        raise ValueError(f"severity must be 'error' or 'warning', not {severity!r}")
    return {"severity": severity, "text": str(text), "where": where}


def run_checks(context, top) -> list:
    """Run every check on the map collection ``top``; returns every problem as ``{"severity", "text", "where", "check"}``, errors
    first. A check that raises is itself a warning ("could not be checked")."""
    cctx = CheckContext(context=context, top=top, name=top.name, terrain=_terrain(top))
    out = []
    for _seq, name, fn in sorted(_CHECKS):
        try:
            found = fn(cctx) or []
        except Exception as exc:                          # noqa: BLE001
            print(f"map_hooks: check {name!r} failed:\n{traceback.format_exc()}", file=sys.stderr)
            out.append({"severity": WARNING, "text": f"The check '{name}' could not run: {exc}", "where": None, "check": name})
            continue
        for p in found:
            out.append({"severity": p.get("severity", WARNING), "text": str(p.get("text", "")), "where": p.get("where"), "check": name})
    out.sort(key=lambda p: p["severity"] != ERROR)
    return out


def _terrain(top):
    for o in top.all_objects:
        if o.get("ic_map") and "ic_map_width" in o:
            return o
    return None


# ---------------------------------------------------------------------------------------------------- a map from memory
def pack_source(data: bytes) -> str:
    return base64.b64encode(zlib.compress(bytes(data), 6)).decode("ascii")


def unpack_source(text: str) -> bytes:
    return zlib.decompress(base64.b64decode(text.encode("ascii")))


def import_map_bytes(context, data: bytes, name: str, options: dict | None = None):
    """The map import, from the map file's bytes in memory (for "New map"): builds the same collections as File > Import and returns the
    map's collection. ``options``: ``load_models``, ``show_water``, ``show_creatures``, ``game_look`` (all default True) and
    ``game_dir`` (default: the add-on's game folder) and ``siblings`` (``{(folder, text after the map's name): bytes}``, the files that
    belong beside the map; import steps read them as ``ictx.siblings``). The map has no file on disk: its bytes are kept in the .blend (a text named
    ``Map source <collection name>``), so saving works the same, and ``SaveContext.source`` is "". Raises ``MapImportError``."""
    from . import ops_map_import as M
    from . import prefs
    opts = dict(options or {})
    game_dir = opts.pop("game_dir", None) or prefs.resolve_game_dir(context)[0]
    try:
        mv = M._view().read(bytes(data))
    except Exception as exc:                              # noqa: BLE001
        raise M.MapImportError(f"The map could not be read: {exc}") from exc
    mv.name = name
    mv.source = ""
    report = M.import_map(context, mv, game_dir=game_dir, load_models=opts.get("load_models", True),
                          show_water=opts.get("show_water", True), show_creatures=opts.get("show_creatures", True),
                          game_look=opts.get("game_look", True), source_bytes=bytes(data), in_memory=True,
                          siblings=opts.get("siblings"))
    return next(c for c in bpy_collections() if c.name == report["collection"])


def bpy_collections():
    import bpy
    return bpy.data.collections
