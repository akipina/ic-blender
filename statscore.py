"""What a creature's numbers come to in the game: rank, cost, build time, population. No Blender in here (the Creature Stats panel,
``statspanel.py``, draws it and ``tests/so_stats_pure_test.py`` checks it).

**How the game gets there**:

* ``BlueprintHeader::LoadAttributes`` reads a creature's ``<name>.lua`` as a ``limbattributes`` table
  (``{group, value}`` per key) and calls ``ProcessAttributes`` over it.
* ``ProcessAttributes`` is four steps: ``ApplyStatDefaults`` (the entries are summed per name, the part
  of a key after its ``-`` dropped: ``hitpoints-head`` + ``hitpoints-torso`` + ... = ``hitpoints``), ``ComputeAttributes`` (melee and
  range attacks), ``ReadMovementFlags`` (a creature with an air speed flies and no longer walks or swims) and ``RunAttrCombiner``
  which runs ``combine_creature`` of ``Data:attrCombiner.lua`` over the numbers.
* Steps 1 to 3 are ``combotest.attrcombine.game_attributes`` (the combiner's own port of them); step 4 is the
  game's own script, run here under Lua 5 with the few helpers Lua 4 had (``combotest.attrcombine.lua4_to_lua5``).

**Whose formula.** The script is not the same in every game build: the original game (the IC mod) runs the ``attrcombiner.lua`` of
``Engine.sga``; Insect Invasion and Tellurian ship their own, with other costs, ranks and build times (Tellurian derives them from a
"Power" number, ``ICParse.attrbalance``). :func:`rule_sets` lists the builds installed with the script each one runs; every result says
which one it was worked out with.

What the game does with the results: the cost, the electricity cost and the build time
are read from ``cost``, ``costrenew`` and ``constructionticks`` (clamped 0..100000, 0..100000, 1..1000),
the population from ``popsize`` (1..10) and the rank from ``creature_rank`` (1..5). The simulation runs 8
steps a second, so the build time in seconds is ``constructionticks / 8``.

Where the creature is used alone (a map, the campaign) these are its numbers. A creature combined with another is priced from the combination:
Combine Preview shows that.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

PURE = True

TICKS_PER_SECOND = 8
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}
SCRIPT = "attrcombiner.lua"
#: the add-on's Game build preference -> the module name its ``.module`` file gives
BUILD_TO_MODULE = {"icmod": "ic", "insectmod": "insect", "tellurian": "tellurian"}


@dataclass(frozen=True)
class RuleSet:
    """One game build whose attribute script can price a creature."""
    key: str                      # the module's name, lower case: 'ic', 'insect', 'tellurian', ...
    name: str                     # as a modder says it: 'The original game'
    archive: str                  # the archive file the script comes from: 'Engine.sga'
    chain: Tuple[str, ...]        # the archives searched, in order (the module's own, then Engine.sga)

    @property
    def where(self) -> str:
        return f"{SCRIPT} in {self.archive}"


@dataclass
class Derived:
    ok: bool
    rules: Optional[RuleSet] = None
    error: str = ""
    rank: int = 0
    cost: float = 0.0                       # coal
    cost_renew: float = 0.0                 # electricity
    build_ticks: float = 0.0
    population: float = 0.0
    hitpoints: float = 0.0                  # the sum over the parts, as the game works it out
    armour: float = 0.0
    ehp: float = 0.0                        # hit points the creature really has against an average hit: hitpoints / (1 - armour)
    power: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    adjusted: "OrderedDict[str, Tuple[float, float]]" = field(default_factory=OrderedDict)   # what the script changed: {name: (before, after)}
    game: "OrderedDict[str, float]" = field(default_factory=OrderedDict)
    ui: "OrderedDict[str, float]" = field(default_factory=OrderedDict)

    @property
    def build_seconds(self) -> float:
        return self.build_ticks / TICKS_PER_SECOND

    @property
    def rank_text(self) -> str:
        return ROMAN.get(int(self.rank), str(int(self.rank)))


# ---------------------------------------------------------------------------------------------------------------- the rule sets
class _Archive:
    """An archive of which only the table of contents is held (a few hundred kilobytes): a member is read from the file when it is asked for.
    ``ICParse.sga.read`` would load the whole file (Engine.sga is 656 MB), which a panel must not do."""

    def __init__(self, path: str):
        import struct
        import ICParse.sga as sga
        self.path = path
        with open(path, "rb") as f:
            head = f.read(0xB4)
            if len(head) < 0xB4:
                raise ValueError("not an archive")
            data_off = struct.unpack_from("<II", head, 0xAC)[1]          # where the data starts: everything before it is the table of contents
            f.seek(0)
            toc = f.read(data_off)
        self.sga = sga.read(toc)
        self.by_path = {e.path.lower(): e for e in self.sga.entries}

    def read(self, rel: str) -> Optional[bytes]:
        import zlib
        e = self.by_path.get(rel.replace("\\", "/").lower())
        if e is None:
            return None
        with open(self.path, "rb") as f:
            f.seek(e.payload_offset)
            blob = f.read(e.comp_len)
        return blob if e.stored else zlib.decompress(blob)


_ARCHIVES: Dict[str, _Archive] = {}


def _archive(path: str) -> _Archive:
    """The archive at ``path`` (its table of contents read once per path and time stamp)."""
    key = f"{path}|{os.path.getmtime(path):.0f}"
    arc = _ARCHIVES.get(key)
    if arc is None:
        arc = _ARCHIVES[key] = _Archive(path)
    return arc


def _read_member(arc: _Archive, rel: str) -> Optional[bytes]:
    return arc.read(rel)


def _find_file(game: str, name: str) -> Optional[str]:
    low = name.lower()
    try:
        for n in os.listdir(game):
            if n.lower() == low and os.path.isfile(os.path.join(game, n)):
                return os.path.join(game, n)
    except OSError:
        return None
    return None


def rule_sets(game: Optional[str]) -> List[RuleSet]:
    """The game builds installed in ``game`` whose archives hold an ``attrcombiner.lua``, the original game first. The original game's own
    script is in ``Engine.sga``; a mod's ``.module`` file names its archives, and the script of the first archive that has one is the mod's."""
    if not game or not os.path.isdir(game):
        return []
    from ICParse.hub.stock import installed_modules, read_module
    engine = _find_file(game, "Engine.sga")
    out: List[RuleSet] = []
    for mod_file in installed_modules(game):
        info = read_module(mod_file)
        if not info:
            continue
        name = str(info.get("name") or os.path.basename(mod_file)[:-7])
        chain = []
        for rel in info.get("archives", []):
            path = _find_file(game, os.path.basename(rel.replace("\\", "/")))
            if path:
                chain.append(path)
        if engine:
            chain.append(engine)
        found = None
        for path in chain:
            try:
                if _read_member(_archive(path), SCRIPT) is not None:
                    found = path
                    break
            except Exception:                                     # noqa: BLE001 -- an archive that cannot be read is not a rule set
                continue
        if found is None:
            continue
        key = name.lower()
        words = {"ic": "The original game"}.get(key, name)
        out.append(RuleSet(key, words, os.path.basename(found), tuple(chain)))
    out.sort(key=lambda r: (r.key != "ic", r.name.lower()))
    return out


_RULE_CACHE: Dict[str, List[RuleSet]] = {}


def rule_sets_cached(game: Optional[str]) -> List[RuleSet]:
    """:func:`rule_sets`, read once per game folder (the panel asks on every redraw). ``rule_sets_cached(None)`` is ``[]``;
    ``forget_rule_sets()`` makes the next call read the folder again."""
    if not game:
        return []
    hit = _RULE_CACHE.get(game)
    if hit is None:
        hit = _RULE_CACHE[game] = rule_sets(game)
    return hit


def forget_rule_sets() -> None:
    _RULE_CACHE.clear()


def pick_rules(sets: Sequence[RuleSet], build: str = "icmod") -> Optional[RuleSet]:
    """The rule set of the add-on's Game build preference (``icmod``, ``insectmod`` or ``tellurian``), else the original game's, else the first."""
    want = BUILD_TO_MODULE.get(build, build)
    for r in sets:
        if r.key == want:
            return r
    for r in sets:
        if r.key == "ic":
            return r
    return sets[0] if sets else None


# ---------------------------------------------------------------------------------------------------------------- the calculation
def lua_available() -> Tuple[bool, str]:
    """``(True, '')`` when the Lua stand-in is there, else ``(False, the reason)``."""
    try:
        from combotest.attrcombine import lua_runtime_class
        lua_runtime_class()
    except Exception as e:                                        # noqa: BLE001
        return False, f"the Lua library (lupa) is missing: {type(e).__name__}"
    return True, ""


def _script_texts(rules: RuleSet):
    """``(main script text, loader(path) -> text or None)``: ``dofilepath("data:x.lua")`` inside a script reads ``x.lua`` from the same archives."""
    arcs = [_archive(p) for p in rules.chain]

    def load(path: str) -> Optional[str]:
        rel = path.replace("\\", "/").split(":", 1)[-1].lstrip("/")
        for arc in arcs:
            raw = _read_member(arc, rel)
            if raw is not None:
                return raw.decode("latin-1")
        return None
    return load(SCRIPT), load


#: the numbers the game's price script may change before it prices a creature, with a plain name (``attrcombiner.lua`` of the original game:
#: ``AttributeData`` keeps each between a lowest and a highest value, the flyer block takes three abilities away, and a creature with no speed
#: at all gets a land speed). Another build's script may do other things: whatever it changes among these is reported from the run itself.
ADJUSTED_NAMES = OrderedDict([
    ("hitpoints", "Hit points"), ("armour", "Armour"), ("speed_max", "Land speed"), ("waterspeed_max", "Water speed"),
    ("airspeed_max", "Air speed"), ("sight_radius1", "Sight"), ("size", "Size"), ("melee_damage", "Melee damage"),
    ("range2_max", "Range of the first ranged attack"), ("range4_max", "Range of the second ranged attack"),
    ("can_dig", "Digging"), ("leap_attack", "Leap"), ("charge_attack", "Charge"),
])
_ON_OFF = {"can_dig", "leap_attack", "charge_attack"}


def _zone_total(values, name: str) -> float:
    """The sum of every number of one speed: ``speed_max-front`` + ``speed_max-back`` + ``speed_max-torso`` ... (the game adds the entries of a name
    after dropping the part from the ``-`` on)."""
    return sum(float(v) for k, v in values.items() if k == name or k.startswith(name + "-"))


def speed_lines(values) -> List[str]:
    """Which of a creature's speed numbers it moves with, as plain sentences. ``values`` maps a number's name to its value (``{"speed_max-front": 13.2, ...}``).

    What the game does: the numbers of one speed are ADDED, so a creature on land runs at
    land speed front legs + back legs + torso, not at one of them. A creature with any air speed flies: its water and
    land speeds are set to 0; otherwise a land speed above 0 makes it a land creature and a water speed above 0 a
    swimmer. A wolf with 13.2 + 13.2 + 6.6 runs at 33; with 8.4 for the torso it runs at 34."""
    land, water, air = (_zone_total(values, n) for n in ("speed_max", "waterspeed_max", "airspeed_max"))

    def parts(name, words):
        got = [(w, float(values[k])) for k, w in words if k in values]
        return " + ".join(f"{w} {v:g}" for w, v in got)
    out: List[str] = []
    if air > 0:
        out.append(f"This creature flies: it moves at {air:g}, the air speed numbers added ({parts('airspeed_max', [('airspeed_max-torso', 'torso'), ('airspeed_max-wings', 'wings')])}). "
                   "The game sets its land and water speed to 0, whatever is written there.")
        return out
    if land > 0:
        out.append(f"On land it moves at {land:g}: the Land speed numbers are added ({parts('speed_max', [('speed_max-front', 'front legs'), ('speed_max-back', 'back legs'), ('speed_max-torso', 'torso')])}). "
                   "Raising any one of them raises the total.")
    else:
        out.append("It has no land speed, so it cannot walk.")
    if water > 0:
        out.append(f"In water it moves at {water:g}, the Water speed numbers added.")
    return out


def _adjustments(before: dict, after: dict, where: str) -> "OrderedDict[str, Tuple[float, float]]":
    out: "OrderedDict[str, Tuple[float, float]]" = OrderedDict()
    for key in ADJUSTED_NAMES:
        if key in before and key in after and abs(float(before[key]) - float(after[key])) > 1e-9:
            out[key] = (float(before[key]), float(after[key]))
    return out


def adjustment_note(key: str, before: float, after: float, where: str) -> str:
    name = ADJUSTED_NAMES.get(key, key)
    if key in _ON_OFF:
        return f"{name} is switched off: the game's script ({where}) does not allow it with this creature's movement."
    if key == "armour" and before > after:
        return (f"The armour of all parts adds up to {before:g}; the game's script ({where}) cuts it to {after:g}, "
                f"so armour beyond that does nothing.")
    if key.endswith("speed_max") and before < after:
        return f"{name} {before:g} is raised to {after:g}: the game's script ({where}) gives every creature at least that."
    return f"{name} {before:g} becomes {after:g}: the game's script ({where}) keeps it in a range."


def derive(entries, rules: RuleSet) -> Derived:
    """Work out rank, cost, build time and population of a creature whose ``limbattributes`` are ``entries`` (``{key: [group, value]}``), with the
    script of ``rules``. Never raises: a failure is ``Derived(ok=False, error=<plain words>)``."""
    ok, why = lua_available()
    if not ok:
        return Derived(False, rules, why)
    try:
        from combotest import attrcombine as AC
        main, load = _script_texts(rules)
        if main is None:
            return Derived(False, rules, f"{SCRIPT} was not found in {rules.archive}")
        game = AC.game_attributes(OrderedDict((k, list(v)) for k, v in entries.items()))
        before = dict(game)                                      # the numbers as the sums leave them, before the script bounds them
        ui: "OrderedDict[str, float]" = OrderedDict()
        lua = AC.lua_runtime_class()(unpack_returned_tuples=True)
        g = lua.globals()
        g.getgameattribute = lambda k: game.get(k, 0.0)
        g.setgameattribute = lambda k, v: game.__setitem__(k, float(v))
        g.checkgameattribute = lambda k: 1 if k in game else 0
        g.setuiattribute = lambda k, v: ui.__setitem__(k, float(v))
        g.getuiattribute = lambda k: ui.get(k, 0.0)
        g.hasmeleedmgtype = lambda m: 1 if int(game.get("melee_dmgtype", 0)) & int(m) else 0
        g.hasrangedmgtype = lambda m: 1 if int(game.get("range_dmgtype", 0)) & int(m) else 0

        def dofilepath(path):                                    # Lua.dll's dofilepath: run another script of the same archives
            text = load(str(path))
            if text is None:
                raise RuntimeError(f"{path} is not in the game's archives")
            lua.execute(AC.lua4_to_lua5(text))
        g.dofilepath = dofilepath
        lua.execute(AC.lua4_to_lua5(main))
        g.combine_creature()
        power = g.power if g.power is not None else None         # the original game's and Insect's script leave it in a Lua global
        if power is None and "Power" in game:                    # Tellurian's sets it as an attribute
            power = game["Power"]
    except Exception as e:                                       # noqa: BLE001 -- the script or the numbers are at fault: say so, do not crash a panel
        return Derived(False, rules, f"the game's script could not run over these numbers: {type(e).__name__}: {str(e).splitlines()[0][:160]}")

    def pick(*names, default=0.0):
        for n in names:
            for table in (game, ui):
                if n in table:
                    return float(table[n])
        return default
    hp, armour = pick("hitpoints"), pick("armour")           # after the script (the original game's cuts armour to 0.9; the sum below never divides by zero)
    from ICParse import attrbalance
    adjusted = _adjustments(before, game, rules.where)
    d = Derived(True, rules, rank=int(round(pick("creature_rank"))), cost=float(int(pick("cost"))),
                cost_renew=float(int(pick("costrenew", "costRenew"))), build_ticks=pick("constructionticks"),
                population=pick("popsize"), hitpoints=hp, armour=min(armour, 0.9),
                ehp=attrbalance.ehp(hp, min(armour, 0.9)) if hp > 0 else 0.0,
                power=float(power) if power is not None else None, game=game, ui=ui, adjusted=adjusted)
    if rules.key == "tellurian":
        d.notes.append("Tellurian prices a creature from its Power (hit points and damage together) and its size")
    for key, (a, b) in adjusted.items():
        d.notes.append(adjustment_note(key, a, b, rules.where))
    return d


def summary_lines(d: Derived) -> List[str]:
    """The derived numbers as short plain lines, for the panel and the export report."""
    if not d.ok:
        return [d.error]
    lines = [f"Rank {d.rank_text}", f"Cost {d.cost:g} coal" + (f" and {d.cost_renew:g} electricity" if d.cost_renew else ""),
             f"Build time {d.build_ticks:g} ticks ({d.build_seconds:.1f} s)", f"Population {d.population:g}",
             f"Hit points counting armour {d.ehp:.0f}"]
    return lines
