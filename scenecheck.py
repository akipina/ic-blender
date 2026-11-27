"""Scene objects: the checks that say, before anything is written, whether the game would load what Export Entity Folder makes.

Three independent checkers, each over the BYTES the export would write (nothing is written to disk by any of them):

* **the model**: ``ICParse.sgm.scene.validate.validate_scene_sgm`` -- the engine's load rules for a scene ``.sgm`` (with the texture files
  the export delivers and the game's archives as the resolver, so a texture the model names must exist);
* **the blueprint**: the 14 rules of ``ICParse.entity`` (``EBP-V01`` .. ``EBP-V14``, area ``ebp_meta``): byte order, framing, header,
  the name, the model beside it, footprint, sounds, class, the stats script, the lower-case path;
* **the item**: ``ICParse.hub.check_files`` over the item folder as it would be written (the hub's own validator, the one the
  port's installer mirrors: paths, kinds, every file through ICParse's parsers, the manifest against the files).

A finding is a :class:`Problem` (severity ERROR / WARN / INFO). ERROR means the game would refuse or mis-load the file; the export
refuses on any ERROR unless "Export anyway" is ticked (``panels_blueprint.export_entity``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

try:
    from ICParse.plainwords import plain       # a checker's message without source files, function names and rule ids
except ImportError:                             # an older ICParse: messages are shown as they are
    def plain(text):
        return text

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
RANK = {INFO: 0, WARN: 1, ERROR: 2}


@dataclass(frozen=True)
class Problem:
    severity: str          # ERROR / WARN / INFO
    source: str            # which checker said it: "model", "blueprint", "item" or "file"
    where: str             # what it is about, in words
    text: str              # what is wrong
    fix: str = ""          # what to do about it
    id: str = ""           # the rule's id when it has one

    def line(self) -> str:
        return plain(f"{self.where}: {self.text}" if self.where else self.text)


def worst(problems: Iterable[Problem]) -> str:
    return max((p.severity for p in problems), key=lambda s: RANK[s], default="")


def errors(problems: Iterable[Problem]) -> List[Problem]:
    return [p for p in problems if p.severity == ERROR]


# ------------------------------------------------------------------------------------------------------------------ the model
def model_problems(sgm: bytes, files: Optional[Dict[str, bytes]] = None, resolver=None) -> List[Problem]:
    """``validate_scene_sgm`` over the model's bytes: ``files`` the texture files the export delivers ({path inside Data: bytes}), ``resolver``
    an ``ICParse.data.ICData`` over the game."""
    from ICParse.sgm.scene.validate import validate_scene_sgm
    out = []
    for line in validate_scene_sgm(sgm, files=files or None, resolver=resolver):
        sev = ERROR if line.startswith("ERROR") else WARN if line.startswith("WARN") else INFO
        text = line.split(":", 1)[1].strip() if ":" in line[:8] else line
        out.append(Problem(sev, "model", "the model", text))
    return out


# ------------------------------------------------------------------------------------------------------------------ the blueprint
def blueprint_problems(ebp: bytes, sgm: Optional[bytes], *, name_path: str, role: Optional[str], mod: str = "icmod", resolver=None,
                       textures: Optional[Dict[str, bytes]] = None, lua=None) -> List[Problem]:
    """The ``ebp_meta`` rules (``EBP-V01`` .. ``EBP-V14``) over the blueprint's bytes. ``name_path``: the entity's place as the game sees it
    (``art/ebps/structures/lab.ebp``); ``role``: GATHERER / CHARACTER / STRUCTURE / PROP; ``resolver``: the game's files."""
    from ICParse.entity import EntityContext, validate
    ctx = EntityContext(sgm=sgm, ebp=ebp, textures=textures or {}, resolver=resolver, role=role, mod=mod or "icmod", name=name_path, lua=lua)
    out = []
    for f in validate(ctx, areas=("ebp_meta",)):
        out.append(Problem(f.severity, "blueprint", f.where, f.text, f.fix or "", f.id))
    return out


# ------------------------------------------------------------------------------------------------------------------ the item folder
_STOCK_CACHE: dict = {}            # folder -> StockIndex (only a good one is kept for good)
_STOCK_FAILED: dict = {}           # folder -> stamp of the folder when the index could not be built


def _folder_stamp(game_dir: str):
    """What changes when the game folder is fixed: the dates and sizes of its folder and archives."""
    try:
        names = sorted(n for n in os.listdir(game_dir) if n.lower().endswith(".sga"))
        return (os.path.getmtime(game_dir),) + tuple((n, os.path.getmtime(os.path.join(game_dir, n)), os.path.getsize(os.path.join(game_dir, n)))
                                                     for n in names)
    except OSError:
        return None


def stock_index(game_dir: Optional[str]):
    """The hub's index of the game's archives (cached by folder), or None when there is no game folder or it cannot be read. A failure is
    remembered only until the folder changes, so putting the game right is noticed without restarting Blender."""
    if not game_dir:
        return None
    if game_dir in _STOCK_CACHE:
        return _STOCK_CACHE[game_dir]
    stamp = _folder_stamp(game_dir)
    if game_dir in _STOCK_FAILED and _STOCK_FAILED[game_dir] == stamp:
        return None
    try:
        from ICParse.hub import StockIndex
        _STOCK_CACHE[game_dir] = StockIndex(game_dir)
        _STOCK_FAILED.pop(game_dir, None)
        return _STOCK_CACHE[game_dir]
    except Exception:                                                     # noqa: BLE001 -- no index: item_problems says the checks were skipped
        _STOCK_FAILED[game_dir] = stamp
        return None


NO_STOCK_WORDS = ("The game folder could not be read, so the checks that compare this with the game's own files were skipped. "
                  "Set the Game folder in the add-on's preferences and check again.")


def item_problems(files: Dict[str, bytes], label: str, stock=None) -> List[Problem]:
    """The hub validator over the item folder as it would be written: ``files`` = {path inside the item: bytes}. The hub's refusals are
    ERRORs, its per-file warnings WARNs. The model's own validator findings are left out (:func:`model_problems` says them with more
    context: the texture files the export delivers)."""
    from ICParse.hub import check_files
    from ICParse.hub.check import ItemFile
    rep = check_files([ItemFile(p, len(d), (lambda d=d: d)) for p, d in sorted(files.items())], label, stock)
    out = []
    if stock is None:                                                     # never report "No problems" for a check that skipped part of its work
        out.append(Problem(WARN, "item", "the game folder", NO_STOCK_WORDS, id="stock.unreadable"))

    def add(where, rule, why):
        if rule == "format.scene" or any(p.where == where and p.id == rule for p in out):
            return
        if rule.startswith("kind.") or rule == "manifest.kind_mismatch":
            # which KIND of shared item the files make decides whether the hub takes them (a folder outside structures / nature / gatherers /
            # characters is no scenery item); the game itself loads the files wherever they lie, so it is a warning, not a refusal
            out.append(Problem(WARN, "item", where, "the Steam Workshop would not take this as an item to share (the game still loads the files): " + why, id=rule))
        else:
            out.append(Problem(ERROR, "item", where, why, id=rule))
    for where, rule, why in rep.problems:
        add(where, rule, why)
    for fr in rep.files:
        for rule, why in fr.warnings:
            if rule != "format.scene":
                out.append(Problem(WARN, "item", fr.rel, why, id=rule))
        for rule, why in fr.problems:                                     # refusals of a file that the report did not copy to rep.problems
            add(fr.rel, rule, why)
    return out


def effect_item_problems(files: Dict[str, bytes], label: str, stock=None) -> List[Problem]:
    """The hub validator over an EFFECT item folder, as :func:`item_problems`, with the one finding the hub always has for effects said once and
    plainly: the hub does not take effect items yet (``ICParse.hub.scenery.EFFECT_HUB_NOTE``: its lists of what an item may hold name the
    pictures under art/fx, not an effect's script or mesh, so the port's checker gives ``kind.not_allowed`` for them). Every other finding keeps
    its severity; the game itself loads the files wherever they lie."""
    from ICParse.hub.scenery import EFFECT_HUB_NOTE
    found = item_problems(files, label, stock)
    kept = [p for p in found if not (p.id.startswith("kind.") or p.id == "manifest.kind_mismatch")]
    if len(kept) != len(found):
        kept.insert(0, Problem(WARN, "item", "the Steam Workshop", EFFECT_HUB_NOTE, id="kind.not_allowed"))
    return kept


# ------------------------------------------------------------------------------------------------------------------ one list
def unique(problems: Iterable[Problem]) -> List[Problem]:
    """The same findings in the same order, a repeated (severity, where, text) once."""
    seen, out = set(), []
    for p in problems:
        key = (p.severity, p.where, p.text)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def to_json(problems: Iterable[Problem]) -> str:
    import json
    return json.dumps([[p.severity, p.source, p.where, p.text, p.fix, p.id] for p in problems])


def from_json(text: str) -> List[Problem]:
    import json
    try:
        return [Problem(*row) for row in json.loads(text)]
    except (ValueError, TypeError):
        return []
