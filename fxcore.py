"""Effects and events of a project, without Blender: what ``fxpanel.py`` draws and writes.

**Where a project's effects live.** Next to the project's ``.sgm`` there is a folder ``<name>_fx/data/`` that is laid out like the game's own
``Data`` folder: ``data/art/fx/<effect>.lua`` and the pictures and meshes an effect needs under the paths the effect names. Copying a game effect
into the project writes its files there (:func:`copy_into_project`), editing an effect rewrites its file (:func:`save_effect`), and an export copies
the whole ``data/`` folder into the export folder (:func:`write_effects`). A project effect outranks the game's effect of the same name
(loose files outrank the archives: IC.exe ``App_SetupFilePathAliases`` 0x004034cc).

**Events** are the blueprint's ``EVNT`` list (:mod:`ICParse.ebp.events`). The panel keeps the edits as a list of :class:`EventItem` and
:func:`apply_events` writes them into the ``.ebp`` at export, changing only the events that changed.

The rules about what the game reads are in ``ICParse/fx/fxschema.py`` (the effect files) and ``ICParse/ebp/events.py`` (the events).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ICParse.ebp import events as EV
from ICParse.fx import fxfiles, fxlua, fxschema as S

PURE = True

PROJECT_SUFFIX = "_fx"
FX_DIR = "art/fx"


# ---------------------------------------------------------------------------------------------------------- the project's folder
def project_data_root(source: str) -> str:
    """``<project folder>/<name>_fx/data``: the project's own mini game ``Data`` folder (made when a first file is written)."""
    base = os.path.splitext(os.path.abspath(source))[0]
    return os.path.join(base + PROJECT_SUFFIX, "data")


def effect_path(source: str, name: str) -> str:
    return os.path.join(project_data_root(source), *fxlua.fx_path_of_name(name).split("/"))


def project_effect_names(source: str) -> List[str]:
    """The effects the project holds, as the events name them (sorted)."""
    root = os.path.join(project_data_root(source), "art", "fx")
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".lua"):
                rel = os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, "/")
                out.append(rel[:-4])
    return sorted(out, key=str.lower)


# ---------------------------------------------------------------------------------------------------------- the game's files
_GAME: Dict[str, tuple] = {}


def _stamp(root: str) -> tuple:
    try:
        return tuple((n, os.path.getmtime(os.path.join(root, n))) for n in sorted(os.listdir(root)) if n.lower().endswith(".sga"))
    except OSError:
        return ()


def game_data(game_dir: Optional[str]):
    """An :class:`ICParse.data.ICData` over the game folder (its loose ``Data`` folder and every archive), kept until an archive changes; None
    without a game folder."""
    if not game_dir or not os.path.isdir(game_dir):
        return None
    hit = _GAME.get(game_dir)
    st = _stamp(game_dir)
    if hit is not None and hit[0] == st:
        return hit[1]
    from ICParse.data import install_data
    data = install_data(game_dir)
    _GAME[game_dir] = (st, data, None, None)
    return data


def game_effect_names(game_dir: Optional[str]) -> List[str]:
    """The names of the game's effects (``art/fx/<name>.lua``), sorted; cached with the game data."""
    data = game_data(game_dir)
    if data is None:
        return []
    st, d, names, _snd = _GAME[game_dir]
    if names is None:
        names = fxfiles.effect_names_in(data)
        _GAME[game_dir] = (st, d, names, _snd)
    return names


def game_sound_names(game_dir: Optional[str]) -> List[str]:
    """The game's sound patches as an event writes them (``data:audio\\combat\\bite\\bite1.pat``), sorted."""
    data = game_data(game_dir)
    if data is None:
        return []
    st, d, names, snd = _GAME[game_dir]
    if snd is None:
        seen = set()
        for arc in getattr(data, "_archives", []):
            for p in arc.paths():
                low = p.replace("\\", "/").lower()
                if low.startswith("audio/") and low.endswith(".pat"):
                    seen.add("data:" + low.replace("/", "\\"))
        snd = sorted(seen)
        _GAME[game_dir] = (st, d, names, snd)
    return snd


_RAW: Dict[tuple, Optional[bytes]] = {}


def _game_raw(game_dir: Optional[str], path: str) -> Optional[bytes]:
    """The bytes of a game file, kept (the panel asks for the same few hundred effects every time it is drawn) until the game's archives change."""
    data = game_data(game_dir)
    if data is None:
        return None
    key = (game_dir, _GAME[game_dir][0], path)
    if key not in _RAW:
        if len(_RAW) > 4000:
            _RAW.clear()
        _RAW[key] = data.read(path)
    return _RAW[key]


def effect_style_of_game(game_dir: Optional[str], name: str) -> str:
    eff = fxfiles.read_effect(game_data(game_dir), name) if game_data(game_dir) is not None else None
    return eff.style if eff is not None else ""


def style_note(style: str) -> str:
    """What a modder should know about an effect type the game treats differently from what its name suggests ('' for the others).

    ``STYLE_CONDITIONAL`` (FXConditional.cpp:199-210 ``Obj::Obj``, Simulation/FXLandWater.cpp): the game starts BOTH of its effects (``Fx1`` and
    ``Fx2``) when the effect starts and follows them together; as soon as the first of them has ended it stops the other (``Obj::Update`` of
    FXConditional.cpp, FXLandWater.cpp and FXCombo.cpp:318-341; a stop ends a spray, a looping effect or a trail, and a ring or beam that does not
    loop plays out). The map's own ``land_and_water`` effect (the only one built with a terrain, ``FX_CreateLandAndWaterType``) also asks the terrain
    whether it is over land or over water on every move (FXLandWater.cpp ``Obj::SetTransform``) and keeps the answer in two flags -- but nothing in
    the effect code reads them (no reader was found in the decompiled source), so no choice between ``Fx1`` and ``Fx2`` is known. The editor
    therefore shows it as an ordinary pair of effects."""
    if style == "STYLE_CONDITIONAL":
        return ("A conditional effect starts both of its effects together, and the game has no test that picks one of them. (The map's "
                "'land_and_water' effect also checks whether it is over land or water, but we found nothing in the game that uses the answer.) "
                "It is edited here as a pair of effects, and the preview plays both. When the first of them has ended, the game stops the other.")
    if style == "STYLE_COMBO":
        return ("A combo starts all of its effects together, at the same moment. When the first of them has ended, the game stops the others "
                "(a spray, a looping effect or a trail ends then; a ring or beam that does not loop plays out).")
    return ""


# ---------------------------------------------------------------------------------------------------------- reading and writing effects
def effect_where(name: str, source: str, game_dir: Optional[str]) -> str:
    """``"project"`` (the project's file, which outranks the game's), ``"game"`` or ``"missing"`` (also for a name that is not a path below art/fx)."""
    if not name or fxlua.name_problem(name):
        return "missing"
    if name and os.path.isfile(effect_path(source, name)):
        return "project"
    data = game_data(game_dir)
    if name and data is not None and data.exists(fxlua.fx_path_of_name(name)):
        return "game"
    return "missing"


def load_effect(name: str, source: str, game_dir: Optional[str]):
    """``(FxEffect or None, where)``: the project's effect of that name, else the game's."""
    where = effect_where(name, source, game_dir)
    if where == "project":
        try:
            return fxlua.read_fx(effect_path(source, name)), where
        except (OSError, ValueError):
            return None, "missing"
    if where == "game":
        path = fxlua.fx_path_of_name(name)
        raw = _game_raw(game_dir, path)
        try:
            return (fxlua.read_fx_bytes(raw, path) if raw is not None else None), where
        except (ValueError, KeyError):
            return None, where
    return None, where


def save_effect(source: str, name: str, fx) -> str:
    """Write ``fx`` as the project's effect ``name`` (a temporary file and a rename, so a failure leaves the old file). Returns the path."""
    path = effect_path(source, name)
    fx.path = fxlua.fx_path_of_name(name)
    write_file(path, fxlua.dump_fx(fx).encode("latin1"))
    return path


def _inside(root: str, path: str) -> bool:
    """Whether ``path`` is below ``root`` once ``..`` parts and links are resolved."""
    r, p = os.path.realpath(root), os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


GENERATION = [0]                 # counts the files this module wrote: a cache of what the project holds is good while it is unchanged


def write_file(path: str, data: bytes) -> None:
    GENERATION[0] += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


@dataclass
class CopyResult:
    top: str
    written: List[str] = field(default_factory=list)          # game paths written into the project
    kept: List[str] = field(default_factory=list)             # the project had them already: left as they were
    missing: List[str] = field(default_factory=list)
    renamed: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


def copy_into_project(source: str, game_dir: Optional[str], name: str, prefix: str = "", new_name: str = "") -> CopyResult:
    """Copy the game's effect ``name`` and everything it names (the effects it starts, their pictures and meshes) into the project's ``data``
    folder. With a ``prefix`` every copied effect is renamed (``<folder>/<prefix><name>``) and the references between them follow, so the
    copies can be edited without changing the game's own effects; with no prefix the copies keep the names and REPLACE the game's effects
    wherever the project's files are used. A file the project already holds is not overwritten (it may hold your edits)."""
    data = game_data(game_dir)
    if data is None:
        raise ValueError("There is no game folder to copy from. Set it in the add-on's preferences.")
    cp = fxfiles.copy_chain(data, name, prefix=prefix, new_name=new_name)
    res = CopyResult(top=cp.top, missing=list(cp.missing), renamed=dict(cp.renamed), notes=list(cp.notes))
    if not cp.effects:
        return res
    root = project_data_root(source)
    for rel, blob in sorted(cp.files.items()):
        dest = os.path.join(root, *rel.split("/"))
        if not _inside(root, dest):                                   # a path the game's effect names that climbs out of the folder
            res.notes.append(f"'{rel}' is outside the effect folder and was not copied.")
            continue
        if os.path.isfile(dest):
            res.kept.append(rel)
            continue
        write_file(dest, blob)
        res.written.append(rel)
    return res


def chain(source: str, game_dir: Optional[str], names: Sequence[str]) -> List[Tuple[str, str, object]]:
    """``[(name, where, FxEffect or None)]`` for ``names`` and every effect they start, depth first, each once; ``where`` is project / game / missing."""
    out, seen = [], set()
    stack = [fxlua.normalise_name(n) for n in reversed(list(names)) if n and n.strip() and not n.startswith("%")]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        fx, where = load_effect(n, source, game_dir)
        out.append((n, where, fx))
        if fx is not None:
            for c in reversed(fx.children()):
                stack.append(fxlua.normalise_name(c))
    return out


def unused_project_effects(source: str, game_dir: Optional[str], names: Sequence[str]) -> List[str]:
    """The effects the project holds that nothing plays: not named by ``names`` (the effects the creature's events name), not started by an effect
    that is, and not a replacement of a game effect (a project effect with a game effect's name is played wherever the game names that one, so
    it is used even when no event of this creature names it). Typically a prefixed copy (``my_...``) that no event was pointed at."""
    reached = {n.lower() for n, _w, _f in chain(source, game_dir, names)}
    theirs = {fxlua.normalise_name(n).lower() for n in game_effect_names(game_dir)} if game_dir else set()
    return [n for n in project_effect_names(source) if fxlua.normalise_name(n).lower() not in reached and fxlua.normalise_name(n).lower() not in theirs]


_CHAINS: Dict[tuple, list] = {}


def chain_cached(source: str, game_dir: Optional[str], names: Sequence[str]) -> list:
    """:func:`chain`, kept while the names, the game's files and the project's effect files (those this module wrote: ``GENERATION``) are the same,
    so a panel can ask on every redraw."""
    key = (source, game_dir, tuple(names), GENERATION[0], _GAME.get(game_dir, (None,))[0])
    hit = _CHAINS.get(key)
    if hit is None:
        if len(_CHAINS) > 16:
            _CHAINS.clear()
        hit = _CHAINS[key] = chain(source, game_dir, names)
    return hit


# ---------------------------------------------------------------------------------------------------------- rows of the editor
@dataclass
class Row:
    name: str
    vartype: str
    kind: str                 # float / int / bool / enum / colour / vector / text / texture / mesh / effect
    keyed: bool
    present: bool             # the file holds it (False: the game's default is in use)
    value: object             # the value at the chosen key (or the one value)
    lo: Optional[float] = None
    hi: Optional[float] = None
    choices: Tuple[Tuple[int, str], ...] = ()
    text: str = ""
    volume: bool = False


def _kind(ps: "S.PropSpec") -> str:
    if ps.role in ("texture", "mesh", "effect"):
        return ps.role
    if ps.choices:
        return "enum"
    return {"VARTYPE_BOOL": "bool", "VARTYPE_INT": "int", "VARTYPE_FLOAT": "float", "VARTYPE_ARRAY_FLOAT": "float",
            "VARTYPE_VECTOR3": "vector", "VARTYPE_ARRAY_VECTOR3": "vector", "VARTYPE_COLOUR": "colour", "VARTYPE_ARRAY_COLOUR": "colour",
            "VARTYPE_STRING": "text"}[ps.vartype]


def rows(fx, key: int = 0) -> List[Row]:
    """The property rows of an effect in the order the game's FX tool lists them, each with the value at key ``key`` (an unkeyed property has one
    value). A property the file lacks shows the game's default."""
    out = []
    sp = fx.spec
    if sp is None:
        return out
    n = len(fx.frame_times or [0, 1])
    for ps in sp.props:
        present = fx.has(ps.name)
        val = fx.effective(ps.name)
        if ps.keyed:
            ent = fxlua.FxEffect._entries(val)
            val = ent[min(max(key, 0), len(ent) - 1)] if ent else None
        out.append(Row(ps.name, ps.vartype, _kind(ps), ps.keyed, present, val, ps.lo, ps.hi, ps.choices, ps.text, ps.volume))
    return out


def apply_row(fx, name: str, key: int, value) -> None:
    """Write one row's value (:meth:`FxEffect.set_property` / :meth:`set_key_value`: refuses what the game would ignore)."""
    ps = S.prop_spec(fx.style, name)
    if ps is not None and ps.keyed:
        fx.set_key_value(name, key, value)
    else:
        fx.set_property(name, value)


def effect_problems(fx) -> List[str]:
    return [str(p) for p in fx.check()]


# ---------------------------------------------------------------------------------------------------------- events
@dataclass
class EventItem:
    src: Optional[int]                       # the event's index in the file as read, None for one added here
    name: str
    params: List[Tuple[str, str]]

    def get(self, key: str, default: str = "") -> str:
        return next((v for k, v in self.params if k == key), default)

    def set(self, key: str, value: str, drop_empty: bool = False) -> None:
        """Set the FIRST pair with this key (the one the game reads), or add one at the end; an empty value drops the pair when ``drop_empty``."""
        for i, (k, _v) in enumerate(self.params):
            if k == key:
                if drop_empty and value == "":
                    del self.params[i]
                else:
                    self.params[i] = (k, value)
                return
        if not (drop_empty and value == ""):
            self.params.append((key, value))

    @property
    def anim(self) -> str:
        return self.get("anim")

    @property
    def animtime(self) -> Optional[float]:
        v = self.get("animtime")
        return EV.atod(v) if v != "" else None

    def key(self):
        return (self.name, list(self.params))


def read_events(ebp_bytes: bytes) -> List[EventItem]:
    """The events of a blueprint file, in file order."""
    from ICParse.ebp.scene import SceneBlueprint
    return [EventItem(r.index, r.name, list(r.params)) for r in EV.event_rows(SceneBlueprint.read(ebp_bytes))]


def apply_events(ebp_bytes: bytes, items: Sequence[EventItem], baseline: Optional[Sequence[EventItem]] = None) -> Tuple[bytes, List[str]]:
    """The blueprint with its event list made equal to ``items``: events not in ``items`` are deleted, changed ones rewritten, new ones (``src`` is
    None) added at the end, in order. Events that did not change keep their bytes, and with nothing changed the file is returned as it was.

    A text with an end mark inside (the stock ``volcano_fx`` event ends two of its fields with a NUL) cannot live in a Blender text box, which keeps
    the text up to the mark: such a field that comes back cut at the mark is the field as it was (:func:`restore_nul`), so reading and
    exporting an event nobody edited never counts as an edit, and editing another field of it keeps the mark.

    ``baseline`` is the list as it was when the editing began: an event counts as changed when it differs from ITS baseline, not from the file,
    so an earlier export step that followed a renamed marker or bone into an event's values is not undone for an event nobody edited.
    Returns ``(bytes, notes)``; raises :class:`ICParse.ebp.events.EventError` for an event the game could not read."""
    from ICParse.ebp.scene import SceneBlueprint
    sb = SceneBlueprint.read(ebp_bytes)
    cur = EV.event_rows(sb)
    ref = {b.src: (b.name, list(b.params)) for b in baseline} if baseline is not None else {r.index: (r.name, r.params) for r in cur}
    items = [restore_nul(it, ref[it.src]) if it.src in ref else it for it in items]
    keep = {it.src for it in items if it.src is not None}
    notes: List[str] = []
    gone = [r.index for r in cur if r.index not in keep]
    changed = [it for it in items if it.src is not None and it.src < len(cur) and ref.get(it.src) != (it.name, list(it.params))]
    added = [it for it in items if it.src is None]
    if not (gone or changed or added):
        return ebp_bytes, notes
    for it in changed:
        EV.set_event(sb, it.src, name=it.name, params=it.params)
    for i in sorted(gone, reverse=True):
        EV.delete_event(sb, i)
    for it in added:
        EV.add_event(sb, it.name, it.params)
    if gone:
        notes.append(f"{len(gone)} event(s) deleted")
    if changed:
        notes.append(f"{len(changed)} event(s) changed")
    if added:
        notes.append(f"{len(added)} event(s) added")
    return sb.to_bytes(), notes


def _cut(text: str) -> str:
    return text.split("\0", 1)[0]


def restore_nul(item: EventItem, base) -> EventItem:
    """``item`` with every field that is its baseline's field cut at an end mark put back as the baseline had it. ``base`` is the baseline's
    ``(name, params)`` (or an :class:`EventItem`). Pairs are matched by position and key; anything else in ``item`` is kept as it is."""
    bname, bparams = (base.name, base.params) if isinstance(base, EventItem) else base
    back = lambda cur, old: old if (old != cur and "\0" in old and _cut(old) == cur) else cur
    params = []
    for i, (k, v) in enumerate(item.params):
        if i < len(bparams) and _cut(bparams[i][0]) == k:
            params.append((back(k, bparams[i][0]), back(v, bparams[i][1])))
        else:
            params.append((k, v))
    return EventItem(item.src, back(item.name, bname), params)


def event_changed(base, item: EventItem) -> bool:
    """Whether ``item`` differs from its baseline ``(name, params)``, apart from the fields a text box cut at an end mark."""
    r = restore_nul(item, base)
    bname, bparams = (base.name, base.params) if isinstance(base, EventItem) else base
    return (r.name, list(r.params)) != (bname, list(bparams))


def event_list(items: Sequence[EventItem]) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """``items`` as the plain ``[(name, [(key, value), ...])]`` list ``ICParse.hub.package.build_creature_item(events=...)`` takes."""
    return [(it.name, [(k, v) for k, v in it.params]) for it in items]


def events_of_animal(ebp_bytes: bytes, clips: Sequence[str], bones: Optional[Sequence[str]] = None, keep_index: bool = False) -> List[EventItem]:
    """The events of another animal's blueprint that fit a creature with these ``clips`` (and ``bones``): Relic's Import Events step
    (relic_workflow.md 4.4). An event is taken when its clip is one of the creature's (an event of a clip the creature lacks can never run,
    AnimEvent.cpp:616-640) and, when ``bones`` is given, when the marker and hint fields it names resolve against them (the same filter
    ``ICParse.ebp.package.copy_events`` applies). The items are new ones (``src`` is None, or the event's number in that blueprint with ``keep_index``), so exporting
    writes them as added events."""
    from ICParse import ebp as EBP
    from ICParse.ebp import package as EP
    return [EventItem(i if keep_index else None, name, list(pairs)) for i, name, pairs in EP.events_for(EBP.read(ebp_bytes), clips, bones)]


def count_edited(items: Sequence[EventItem], baseline: Sequence[EventItem]) -> int:
    """How many events differ from the baseline (changed, added or deleted)."""
    base = {b.src: b for b in baseline}
    n = sum(1 for it in items if it.src is None or it.src not in base or event_changed(base[it.src], it))
    return n + sum(1 for s in base if s not in {it.src for it in items})


def creature_events(donor_ebp: Optional[bytes], items: Sequence[EventItem], baseline: Sequence[EventItem]):
    """What an Export Creature for the Game does with the events the modder edited: ``(donor blueprint bytes or None, event list or None,
    notes)``.

    * With a donor, the edits are made in the DONOR's blueprint (:func:`apply_events`), and the export then puts the new model into that
      blueprint, which follows renamed bones and clips in every event and drops the events of a clip the model lost. So the edits
      are made against the names the donor's model had, and the export brings every event onto the new model's names.
    * With no donor the blueprint is generated; the list shown in the box is the whole list the blueprint gets (``ICParse.hub.package.
      build_creature_item(events=...)``), sent only when it was edited (an untouched list is the default copy from the stock animal, byte for byte).
    Nothing edited gives ``(None, None, [])``."""
    if not count_edited(items, baseline):
        return None, None, []
    if donor_ebp is not None:
        out, notes = apply_events(donor_ebp, items, baseline=baseline)
        return out, None, notes
    return None, event_list(items), [f"{len(items)} event(s) from the Events box"]


def items_to_json(items: Sequence[EventItem]) -> str:
    return json.dumps([[it.src, it.name, [list(p) for p in it.params]] for it in items])


def items_from_json(text: str) -> List[EventItem]:
    try:
        return [EventItem(s, n, [(str(k), str(v)) for k, v in ps]) for s, n, ps in json.loads(text)]
    except (ValueError, TypeError):
        return []


def fmt_time(t: float) -> str:
    """An ``animtime`` the way the stock files write it: two decimals (``0.50``, ``-1.00``), three when two would lose more than half a thousandth."""
    s = "%.2f" % t
    return s if abs(float(s) - t) <= 0.0005 else "%.3f" % t


def clip_time(frame: float, start: float, end: float) -> float:
    """The event time of a playhead frame in a clip whose keys run from frame ``start`` to ``end`` (both included).

    ``animtime`` is a fraction of the clip's DURATION (AnimPlayList.cpp:1669-1672: ``time * GetInvDuration()``), and the game loads a clip with
    ``frames = last - first + 1`` and ``duration = frames / fps`` (Anim.cpp:686-693), so the first frame is at 0 and frame ``f`` at
    ``(f - first) / (last - first + 1)``: the last frame is at ``(N - 1) / N``, never at 1 (the same step ``duration / frames`` per frame is
    what Anim.cpp:1618-1626 advances a clip by)."""
    n = max(1.0, float(end) - float(start) + 1.0)
    return min(1.0, max(0.0, (float(frame) - start) / n))


def frame_of_time(t: float, start: float, end: float) -> float:
    """The playhead frame of an event time in a clip whose keys run from frame ``start`` to ``end`` (the inverse of :func:`clip_time`)."""
    return float(start) + float(t) * max(1.0, float(end) - float(start) + 1.0)


def event_warnings(items: Sequence["EventItem"], baseline: Sequence["EventItem"], source: str, game_dir: Optional[str]) -> List[str]:
    """One plain warning for each event the modder added or changed that the game cannot play as meant: it names an effect neither the game nor the
    project has, or its time is outside the clip (a time of 1 or more is never reached, AnimEvent.cpp:601-611; a time below 0 runs when the clip
    starts, :TriggerStartEvents, whatever its size: only -1, the game's own 'when the clip starts', is left alone). Events as the blueprint had them
    are not reported: a stock event's quirk is not the modder's to fix."""
    base = {b.src: b for b in baseline}
    out = []
    for it in items:
        if not (it.src is None or it.src not in base or event_changed(base[it.src], it)):
            continue
        fx = it.get("fx").strip()
        if fx and not fx.startswith("%") and game_dir and effect_where(fx, source, game_dir) == "missing":
            out.append(f"The event '{it.name}' plays the effect '{fx}', which neither the game nor the project has: it shows nothing. Choose another "
                       f"effect, or copy one into the project.")
        t = it.animtime
        raw = it.get("animtime").strip()
        if t is not None and t >= 1.0:
            out.append(f"The event '{it.name}' is at time {raw}, at or past the end of the clip: the game runs an event only before the end, so it "
                       f"never runs. Use a time from 0 to just below 1.")
        elif t is not None and t < 0.0 and abs(t + 1.0) > 1e-6:
            out.append(f"The event '{it.name}' is at time {raw}, below 0: the game runs it when the clip starts, not at that time. Use -1 for "
                       f"'when the clip starts', or a time from 0 to just below 1.")
    return out


def pick_label(value: str) -> tuple:
    """``(readable name, folder)`` of a name a picker lists: a sound's or effect's file name without the folder and the ending, and the folder
    in the game's own spelling with ``/``. ``data:audio\\combat\\attacker\\swipe\\swipe2.pat`` gives ``("swipe2", "combat/attacker/swipe")``;
    ``combat_impact_combo`` gives ``("combat_impact_combo", "")``."""
    v = value.replace("\\", "/")
    if ":" in v and not v.startswith("%"):
        v = v.split(":", 1)[1]
    if v.lower().startswith("audio/"):
        v = v[len("audio/"):]
    folder, _, leaf = v.rpartition("/")
    if leaf.lower().endswith((".pat", ".lua")):
        leaf = leaf.rsplit(".", 1)[0]
    return leaf or v, folder


def event_time(frame: float, start: float, end: float) -> float:
    """:func:`clip_time`, held below 1 (a frame past the clip's end): the game runs the events whose time is below the end of each step
    (AnimEvent.cpp:601-611), so an event at exactly 1 never runs."""
    return min(0.999, clip_time(frame, start, end))


def event_summary(it: EventItem) -> str:
    bits = []
    if it.get("fx"):
        bits.append("fx " + it.get("fx"))
    if it.get("sound"):
        s = it.get("sound")
        bits.append("sound " + (s.replace("\\", "/").rsplit("/", 1)[-1] if not s.startswith("%") else s))
    if it.get("marker"):
        bits.append("@ " + it.get("marker"))
    return ", ".join(bits)


def effects_named(items: Sequence[EventItem]) -> List[str]:
    """Every effect name the events use, once each, in order (a ``%variable`` is a name the game fills in at run time: left out)."""
    seen, out = set(), []
    for it in items:
        n = it.get("fx").strip()
        if n and not n.startswith("%") and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


# ---------------------------------------------------------------------------------------------------------- sounds
STOCK_TEMPLATE = "audio/animals/cheetah/cheetahhit1"          # the game's sound a new one starts from (a clone: .pat and .smp)


def sound_folder(creature: bool, stem: str) -> str:
    """The folder (inside data) a new sound is written to: ``audio/animals/<creature>`` for a creature (the hub's rule for a creature's sounds,
    ``ICParse.hub.check._ANIMAL_AUDIO``), ``audio/sfx/<name>`` for a scene object."""
    return f"audio/animals/{stem}" if creature else f"audio/sfx/{stem}"


def project_sounds(source: str) -> List[Tuple[str, str]]:
    """``[(sound name, the value an event's sound field takes)]`` for every patch (.pat) the project holds, sorted by name."""
    from ICParse.sound import alias_path
    out = []
    for rel in effect_files(source):
        if rel.startswith("audio/") and rel.endswith(".pat"):
            out.append((os.path.basename(rel)[:-4], alias_path(rel)))
    return sorted(out, key=lambda t: t[0].lower())


def sound_where(value: str, source: str, game_dir: Optional[str]) -> str:
    """``"project"``, ``"game"`` or ``"missing"`` for a sound an event names (a ``%variable`` is a name the game fills in: ``"game"``)."""
    from ICParse.sound import path_of_alias
    if not value or value.startswith("%"):
        return "game"
    rel = path_of_alias(value)
    if rel in effect_files(source):
        return "project"
    data = game_data(game_dir)
    if data is not None and data.exists(rel):
        return "game"
    return "missing"


def create_sound(source: str, game_dir: Optional[str], wave: bytes, name: str, folder: str, settings=None):
    """Make a new sound from the bytes of a WAV file in the project: ``<name>_fx/data/<folder>/<name>.pat``, ``.smp`` and ``.wav`` (the project's
    files go into the export with the effects). It starts from the game's cheetah bite sound when the game folder is known. Returns the
    :class:`ICParse.sound.NewSound`; raises ValueError with a plain message for a wave the game could not play, a name or a setting that is
    wrong, or a sound of that name the project already has."""
    from ICParse import sound
    data = game_data(game_dir)
    tp = data.read(STOCK_TEMPLATE + ".pat") if data is not None else None
    ts = data.read(STOCK_TEMPLATE + ".smp") if data is not None else None
    ns = sound.new_sound(wave, name, folder, settings, template_patch=tp, template_sample=ts)
    have = effect_files(source)
    clash = [p for p in ns.files if p in have]
    if clash:
        raise ValueError(f"The project already has a sound file '{clash[0]}'. Choose another name for the new sound (or delete those files).")
    root = project_data_root(source)
    for rel, blob in sorted(ns.files.items()):
        dest = os.path.join(root, *rel.split("/"))
        if not _inside(root, dest):
            raise ValueError("The sound would be written outside the project's folder.")
        write_file(dest, blob)
    return ns


def sound_problems(source: str, game_dir: Optional[str]) -> List[str]:
    """Plain notes about the project's own sounds: a patch that names a sample, or a sample that names a wave, that neither the project nor the game
    has (the game would be silent), and a file the game could not read."""
    from ICParse import sound
    notes = []
    files = effect_files(source)
    data = game_data(game_dir)
    have = lambda rel: rel in files or (data is not None and data.exists(rel))
    for rel, full in sorted(files.items()):
        if not rel.startswith("audio/"):
            continue
        try:
            if rel.endswith(".pat"):
                with open(full, "rb") as f:
                    for ref in sound.read_patch(f.read()).sample_names():
                        if not have(sound.path_of_alias(ref)):
                            notes.append(f"The sound {rel} names the sample {ref}, which neither the project nor the game has: it would play nothing.")
            elif rel.endswith(".smp"):
                with open(full, "rb") as f:
                    wav = sound.read_sample(f.read()).wave
                if wav and not have(sound.path_of_alias(wav)):
                    notes.append(f"The sample {rel} names the wave {wav}, which neither the project nor the game has: it would play nothing.")
            elif rel.endswith(".wav"):
                with open(full, "rb") as f:
                    sound.read_wave_header(f.read())
        except (ValueError, OSError) as e:
            notes.append(f"{rel}: {e}")
    return notes


def delete_sound(source: str, value: str) -> List[str]:
    """Delete the project's sound ``value`` (an event's sound field, ``data:audio\\...x.pat``): its patch, the samples the patch names and the waves those
    name -- but a sample or wave that ANOTHER sound of the project (another patch, or another sample) still names stays. Returns the paths deleted
    (inside data). Raises ``ValueError`` (plain words) for a sound the project does not hold. The files are gone for good: Blender's Undo does not
    bring them back."""
    from ICParse import sound
    files = effect_files(source)
    rel = sound.path_of_alias(value)
    if rel not in files or not rel.endswith(".pat") or not rel.startswith("audio/"):
        raise ValueError("That sound is not one of the project's.")

    def read(path):
        with open(files[path], "rb") as f:
            return f.read()
    try:
        samples = [sound.path_of_alias(r) for r in sound.read_patch(read(rel)).sample_names()]
    except (ValueError, OSError):
        samples = []
    used_samples = set()                                       # the samples another patch of the project names
    for other in files:
        if other != rel and other.startswith("audio/") and other.endswith(".pat"):
            try:
                used_samples.update(sound.path_of_alias(r) for r in sound.read_patch(read(other)).sample_names())
            except (ValueError, OSError):
                pass
    doomed = [rel] + [p for p in dict.fromkeys(samples) if p in files and p not in used_samples]
    waves = []
    for smp in doomed[1:]:
        try:
            waves.append(sound.path_of_alias(sound.read_sample(read(smp)).wave))
        except (ValueError, OSError):
            pass
    used_waves = set()                                         # the waves the samples that stay name
    for other in files:
        if other.startswith("audio/") and other.endswith(".smp") and other not in doomed:
            try:
                used_waves.add(sound.path_of_alias(sound.read_sample(read(other)).wave))
            except (ValueError, OSError):
                pass
    doomed += [w for w in dict.fromkeys(waves) if w in files and w not in used_waves]
    gone = []
    for p in doomed:
        if p.startswith("audio/"):
            try:
                os.remove(files[p])
                gone.append(p)
            except OSError:
                pass
    GENERATION[0] += 1
    return gone


def sound_timing_note(item: "EventItem") -> str:
    """A plain note for an event that plays a sound with no time (or a negative one) -- '' for any other. The game starts such a sound as a looping
    sound in the world that stays until the clip is over (RenderModelEventSink.cpp:720-731: ``animtime`` below 0, which is also what a missing
    ``animtime`` reads as, AnimEvent.cpp:626-632); only a time of 0 or more plays it once (:687-690)."""
    sv = item.get("sound").strip()
    if not sv or sv.startswith("%"):
        return ""
    t = item.animtime
    if t is not None and t >= 0.0:
        return ""
    return (f"The event '{item.name}' plays a sound with no time, so the game starts it as a looping sound that stays until the clip is over. "
            "Give the event a time (0 up to just below 1) for a sound that plays once.")


def project_sound_timing_notes(source: str, items: Sequence["EventItem"]) -> List[str]:
    """:func:`sound_timing_note` for the events that play one of the PROJECT's own sounds (a game event with such a timing is the game's own choice)."""
    mine = {v.lower() for _n, v in project_sounds(source)} if source else set()
    from ICParse.sound import alias_path, path_of_alias
    return [n for n in (sound_timing_note(it) for it in items if alias_path(path_of_alias(it.get("sound"))) in mine) if n]


def retarget_sounds(files: Dict[str, bytes], folder: str, items: Sequence[EventItem]) -> Tuple[Dict[str, bytes], List[EventItem], List[str]]:
    """For an item whose creature has a name that is not the folder its sounds were made under: move the project's sounds under ``audio/animals/``
    to ``folder`` (``audio/animals/<the item's name>``: the hub's rule) and make the events that name them follow. Returns ``(files, items, notes)``;
    ``items`` are new ones where an event changed. Raises ``ValueError`` (plain words) when two sounds would end up as the same file."""
    from ICParse import sound
    notes: List[str] = []
    olds = sorted({"/".join(p.split("/")[:4]) for p in files if p.startswith("data/audio/animals/") and p.count("/") >= 4})
    olds = [o[len("data/"):] for o in olds if o[len("data/"):] != folder]
    if not olds:
        return files, list(items), notes
    inner = {p[len("data/"):]: b for p, b in files.items() if p.startswith("data/")}
    rest = {p: b for p, b in files.items() if not p.startswith("data/")}
    event_map: Dict[str, str] = {}
    for old in olds:                                               # each move sees the files the earlier ones put under ``folder``: a name taken twice is refused
        inner, em = sound.make.move_sound_folder(inner, old, folder)
        event_map.update(em)
        notes.append(f"The sounds in {old} went under {folder} (a creature's sounds belong in the folder of its own name).")
    out = []
    for it in items:
        sv = it.get("sound")
        if sv and sound.alias_path(sound.path_of_alias(sv)) in event_map:
            it = EventItem(it.src, it.name, [(k, (event_map[sound.alias_path(sound.path_of_alias(v))] if k == "sound" and v == sv else v)) for k, v in it.params])
        out.append(it)
    return {**rest, **{"data/" + p: b for p, b in inner.items()}}, out, notes


# ---------------------------------------------------------------------------------------------------------- export
def effect_files(source: str) -> Dict[str, str]:
    """``{path under data (lower case, '/'): the file on disk}`` of everything in the project's ``data`` folder."""
    root = project_data_root(source)
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, f)
            out[os.path.relpath(full, root).replace(os.sep, "/").lower()] = full
    return out


def spaced_paths(source: str) -> List[str]:
    """The project's files whose path has a space. An item's manifest lists a file as ``<path> sha256:<hex> <size>`` (ICParse.hub.manifest), so
    a path with a space cannot be listed and the hub checker refuses the item. (Copying a game effect already writes ``_`` for each space in the
    paths of its pictures and meshes, :func:`ICParse.fx.fxfiles.copy_chain`.)"""
    return sorted(rel for rel in effect_files(source) if " " in rel)


def export_problems(source: str) -> Tuple[List[str], List[str]]:
    """``(refusals, notes)`` for the project's effect files: a refusal is an effect the game would not load at all; a note is a property it would
    ignore."""
    refused, notes = [], []
    for rel, full in sorted(effect_files(source).items()):
        if not (rel.startswith("art/fx/") and rel.endswith(".lua")):
            continue
        try:
            fx = fxlua.read_fx(full)
        except (OSError, ValueError) as e:
            refused.append(f"{rel}: cannot be read ({e})")
            continue
        for p in fx.check():
            (refused if p.level == "refused" else notes).append(f"{rel}: {p}")
    return refused, notes


def write_effects(source: str, out_root: str, data_dir: str = "data") -> List[str]:
    """Copy the project's ``data`` folder into ``<out_root>/<data_dir>`` (lower-case paths below it; a file is replaced). Returns the paths written,
    each as ``data/...``."""
    written = []
    for rel, full in sorted(effect_files(source).items()):
        with open(full, "rb") as f:
            blob = f.read()
        write_file(os.path.join(out_root, data_dir, *rel.split("/")), blob)
        written.append("data/" + rel)
    return written


def fx_folder_of_member(member: str) -> Optional[str]:
    """``art/fx/avalanche/object/rock.sgm`` -> ``avalanche/object`` ('' for art/fx itself); None when it is not under art/fx."""
    m = member.replace("\\", "/").lower()
    i = m.rfind("art/fx/")
    if i < 0:
        return None
    return m[i + len("art/fx/"):].rpartition("/")[0]
