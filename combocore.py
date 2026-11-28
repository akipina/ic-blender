"""Bpy-free glue between the add-on and the combiner (``pyRDN/combotest``, the only combiner).

Nothing here combines, validates or parses itself (combotest and ICParse do): it turns add-on state into the combiner's
inputs and its outputs into what the panel, ghost overlay and Validate show. A creature must be one cached Parent per
file version (the combiner tells animals apart by identity). Never import bpy: venv tests import this as a top-level module.
"""
from __future__ import annotations

import hashlib
import tempfile
import json
import os
import time
import traceback
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field, fields
from typing import Dict, List, Optional, Sequence

import numpy as np

try:
    from . import core, fastsurf, material
except ImportError:                      # imported top-level (tests put ic_blender/ on sys.path)
    import core  # type: ignore[no-redef]
    import fastsurf  # type: ignore[no-redef]
    import material  # type: ignore[no-redef]

# The combiner is optional: without it the add-on still registers and the panel says why nothing combines.
try:
    import combotest
    from combotest import slots as ct_slots
    from combotest.loader import Parent, load_parent
    IMPORT_ERROR: Optional[str] = None
except Exception as _e:                  # ImportError, or a broken checkout: reported by status(), never raised here
    combotest = None
    ct_slots = None
    Parent = None
    load_parent = None
    IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ComboTest.exe's part buttons, in the tool's order, and the slot keys each picks from the other creature
# (combotest.slots.PARTS is the single source of the mapping).
PARTS: Dict[str, List[str]] = OrderedDict((k, list(v)) for k, v in ct_slots.PARTS.items()) if ct_slots else OrderedDict()
# The combiner knows 16 limb slots, ComboTest.exe's six buttons reach 12 of them ("No button reaches the mid
# legs", combotest/slots.py). The game's combiner picks a limb for every open connection ring by its class, whatever the class
# (FindBestCandidateFrom per ring), and knows the mid legs: ComputeLimbScale scales a mid-leg graft
# (`class & 0x20000`) and takes the mid legs as the leg reference when a creature has no other legs (Find(0x20021)). BUT a candidate limb marked Non-Swappable is taken only from the torso creature itself
# (FindBestCandidateAny / FindBestCandidateFrom), and every stock mid leg is Non-Swappable (insects 0x820021/22, spiders 0x840021/22).
# So with the game's rules the buttons graft the mid legs of a donor whose mid legs are NOT flagged (a creature of your own), and
# for a stock donor they change nothing: the panel says so. Whether the game's own builder screen offers a mid-leg choice is not
# known (only the combiner and the saved-design path were read). Fins, stubs and antennae have no slot of their own in the
# combiner (a limb of another class is no slot: combotest.slots.slot_for_limb_id is None), so there is nothing to offer.
EXTRA_PARTS: Dict[str, List[str]] = OrderedDict([("mid_legs", ["lmleg", "rmleg"]), ("mid2_legs", ["lm2leg", "rm2leg"])])
_PART_WORDS = {"mid_legs": "Mid Legs", "mid2_legs": "Second Mid Legs"}
if ct_slots:
    for _k, _slots in EXTRA_PARTS.items():
        if _k not in PARTS and all(sk in ct_slots.BY_KEY for sk in _slots):
            PARTS[_k] = list(_slots)
PART_LABELS: Dict[str, str] = OrderedDict((k, _PART_WORDS.get(k) or k.replace("_", " ").title()) for k in PARTS)


def donor_has_part(donor, part: str) -> bool:
    """Whether ``donor`` has a limb with patches for the part button (else the combine equals the torso alone). The six
    ComboTest.exe buttons are answered by its own check, the extra ones by the same rule over their slots."""
    if ct_slots is not None and part in ct_slots.PARTS:
        from combotest.validate import donor_has_part as tool_has
        return tool_has(donor, part)
    return any(lm.prims for k in PARTS[part] for lm in donor.limbs_for(ct_slots.BY_KEY[k]))

ROLE_TORSO = "TORSO"      # this creature is the torso, the other donates the toggled parts
ROLE_DONOR = "DONOR"      # the other creature is the torso, this creature donates the toggled parts


def available() -> bool:
    """The combiner imports."""
    return combotest is not None


def _require():
    if combotest is None:
        raise CombinerUnavailable(f"the creature combiner could not be loaded: {IMPORT_ERROR}")


class CombinerUnavailable(RuntimeError):
    """combotest is not importable (see :func:`status`)."""


class SetupError(RuntimeError):
    """The combine tier cannot run here: no game install data, no stock creatures."""


# ------------------------------------------------------------------------------------------------------------- status
_LUA_INFO: dict = {}


def lua_info() -> dict:
    """``{"version": lupa version or None, "impl": "Lua 5.4" or None, "error": reason}`` (cached per process)."""
    if _LUA_INFO:
        return _LUA_INFO
    info = {"version": None, "impl": None, "error": ""}
    if combotest is None:
        info["error"] = "the creature combiner could not be loaded"
    else:
        try:
            from combotest.attrcombine import lua_runtime_class
            cls = lua_runtime_class()
            import lupa
            info["version"] = getattr(lupa, "__version__", "?")
            info["impl"] = str(cls().lua_implementation)
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
    _LUA_INFO.update(info)
    return _LUA_INFO


def set_game_dir_provider(provider) -> None:
    """Tell the combiner where the game is: ``provider()`` returns the folder, or None for its default search."""
    if combotest is not None:
        from combotest import installdata
        installdata.set_root_provider(provider)


def install_root() -> Optional[str]:
    """The game install the combiner reads its tables from, or None."""
    if combotest is None:
        return None
    from combotest import installdata
    return installdata.install_root()


def status(stock_setting: str = "", extra_stock_dirs: Sequence[str] = (), paths_status: Optional[dict] = None) -> dict:
    """What the combine preview and the combine tier can do here::

        {"combotest": path or None, "combotest_error": str, "lupa": version or None, "lua": "Lua 5.4" or None,
         "lua_error": str, "install": root or None, "stock_dir": dir or None, "stock_count": int,
         "problems": [readable lines], "paths": the add-on's PATHS_STATUS (when given)}

    ``problems`` is empty when everything is there; otherwise each line says what is missing and what that costs."""
    out = {"combotest": None, "combotest_error": IMPORT_ERROR or "", "lupa": None, "lua": None, "lua_error": "",
           "install": None, "stock_dir": None, "stock_count": 0, "problems": [], "paths": dict(paths_status or {})}
    probs = out["problems"]
    if combotest is None:
        note = (paths_status or {}).get("combotest", {}).get("note", "")
        probs.append(f"Combiner unavailable: {IMPORT_ERROR}" + (f" ({note})" if note else ""))
        return out
    out["combotest"] = os.path.dirname(os.path.abspath(combotest.__file__))
    li = lua_info()
    out["lupa"], out["lua"], out["lua_error"] = li["version"], li["impl"], li["error"]
    if not li["impl"]:
        note = (paths_status or {}).get("lupa", {}).get("note", "")
        probs.append("lupa missing: ratings, cost and rank are not computed" + (f" ({note})" if note else ""))
    out["install"] = install_root()
    if not out["install"]:
        probs.append("game not found (set the Game folder in the add-on's preferences): combines are unscaled and "
                     "the combine tier can't run")
    d = stock_dir(stock_setting, extra_stock_dirs)
    out["stock_dir"] = d
    if d:
        out["stock_count"] = len(_stock_files(d))
    else:
        probs.append("no stock creatures found: set the stock directory or press Extract Stock Creatures")
    return out


# ------------------------------------------------------------------------------------------------------ stock donors
_LISTED: dict = {}                       # folder -> (its modified time, the .sgm names it held then)


def _stock_files(d: str) -> List[str]:
    """The .sgm files of a stock folder, sorted. A panel asks on every redraw: the listing is kept until the folder's own modified time moves
    (a file added, removed or renamed there), so it is never out of date."""
    try:
        stamp = os.stat(d).st_mtime_ns
    except OSError:
        return []
    hit = _LISTED.get(d)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        names = sorted(f for f in os.listdir(d) if f.lower().endswith(".sgm"))
    except OSError:
        return []
    _LISTED[d] = (stamp, names)
    return names


def stock_dir_candidates(setting: str = "", extra: Sequence[str] = ()) -> List[str]:
    """Where stock creatures are looked for, in order: ``$BZ_STOCK_DIR``, the panel's setting, the combiner's default
    (``$COMBOTEST_ENGINE_STOCK`` or the repo's ``Assets/engine_stock``), then ``extra`` (the add-on's user cache
    dir that Extract Stock Creatures fills)."""
    cands = [os.environ.get("BZ_STOCK_DIR", ""), setting or ""]
    if combotest is not None:
        from combotest.validate import default_stock_dir
        cands.append(default_stock_dir())
    else:
        cands.append(os.path.join(REPO, "Assets", "engine_stock"))
    cands += list(extra)
    out = []
    for c in cands:
        if c and os.path.abspath(c) not in out:
            out.append(os.path.abspath(c))
    return out


def stock_dir(setting: str = "", extra: Sequence[str] = ()) -> Optional[str]:
    """The first candidate directory (:func:`stock_dir_candidates`) that holds ``.sgm`` creatures, or None."""
    for c in stock_dir_candidates(setting, extra):
        if os.path.isdir(c) and _stock_files(c):
            return c
    return None


def in_stock_pool(path: str, stock_dirs: Sequence[str]) -> bool:
    """Whether a file written at ``path`` would join a stock donor pool (its directory is one of ``stock_dirs``:
    the donor index and the Full tier list that directory's ``.sgm`` files as stock creatures)."""
    d = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(path))))
    return any(d == os.path.normcase(os.path.realpath(s)) for s in stock_dirs if s)


def materialize_path(stem: str, blend_path: str = "", source_path: str = "", stock_dirs: Sequence[str] = (),
                     fallback_dir: str = "") -> str:
    """Default ``.sgm`` path of a materialized combination: ``combos/`` beside the saved .blend, else ``combos/``
    beside the creature's source file, else ``combos/`` in ``fallback_dir`` (the add-on passes Blender's user data
    ``pyic`` folder: persistent, unlike Blender's session temp dir). Never in or under a stock donor directory (a
    combination written there would be listed as a stock creature and break the calibration pool,
    :func:`in_stock_pool`): a creature imported from the stock folder materializes into ``fallback_dir``."""
    bases = [os.path.dirname(os.path.abspath(p)) for p in (blend_path, source_path) if p]
    bases.append(fallback_dir or tempfile.gettempdir())
    for base in bases:
        path = os.path.join(base, "combos", stem + ".sgm")
        if not in_stock_pool(os.path.join(base, stem + ".sgm"), stock_dirs) and not in_stock_pool(path, stock_dirs):
            return path
    return os.path.join(tempfile.mkdtemp(prefix="bz_combos_"), stem + ".sgm")


def extract_stock(out_dir: str, root: Optional[str] = None) -> List[str]:
    """Extract the base game's stock creatures (``Engine.sga`` ``art/ebps/stock/``: ``.ebp``, the embedded ``.sgm``,
    ``.lua``, ``.lsc``) into ``out_dir`` with ``stockextract.py``'s :func:`extract`. Returns the paths written;
    raises when the install or the archive is missing."""
    import importlib.util
    root = root or install_root()
    if not root:
        raise SetupError("game not found: set the Game folder in the add-on's preferences")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockextract.py")
    spec = importlib.util.spec_from_file_location("bz_extract_stock", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    written, _kinds = mod.extract("Engine.sga", "art/ebps/stock/", out_dir, limit=True, exts=mod.STOCK_EXTS, root=root)
    return written


CATEGORIES = OrderedDict([("insect", "Insects & Arachnids"), ("flyer", "Flyers"), ("swimmer", "Swimmers"),
                          ("land", "Quadrupeds & Land")])


@dataclass
class DonorInfo:
    """One stock creature as the panel lists it."""
    name: str                   # file stem (what .lsc rules call it)
    path: str
    parts: List[str]            # part buttons it has a limb with patches for (the others would change nothing)
    sockets: List[str]          # slot keys it has a connection for
    noswap: List[str]           # slot keys of its NoSwap (0x800000) limbs with patches
    flyer: bool
    size: Optional[int]
    category: str               # a CATEGORIES key

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _file_stamp(path: str) -> list:
    try:
        st = os.stat(path)
        return [int(st.st_size), int(st.st_mtime_ns)]
    except OSError:
        return [None, None]


def _dir_stamp(d: str) -> list:
    out = []
    for f in _stock_files(d):
        stem = os.path.join(d, f[:-4])
        out.append([f, _file_stamp(stem + ".sgm"), _file_stamp(stem + ".lua"), _file_stamp(stem + ".lsc")])
    return out


def donor_info(p) -> DonorInfo:
    """The :class:`DonorInfo` of a loaded stock :class:`Parent`."""
    _require()
    by = ct_slots.BY_KEY
    parts = [g for g in PARTS if donor_has_part(p, g)]
    sockets = [s.key for s in ct_slots.LIMB_SLOTS if ct_slots.socket_for(p.model, s) is not None]
    noswap = sorted({s.key for s in ct_slots.LIMB_SLOTS for lm in p.limbs_for(s)
                     if lm.prims and int(lm.limb_id) & ct_slots.NO_SWAP})
    ents = p.attributes.entries if p.attributes is not None else {}

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    flyer = any(k.lower().startswith("airspeed_max-") and num(v[1]) > 0 for k, v in ents.items())
    swims = any(k.lower().startswith("waterspeed_max-") and num(v[1]) > 0 for k, v in ents.items())
    walks = any(k.lower() in ("speed_max-front", "speed_max-back") and num(v[1]) > 0 for k, v in ents.items())
    size = int(round(num(ents["size"][1]))) if "size" in ents else None
    if any(lm.prims for k in ("lmleg", "rmleg") for lm in p.limbs_for(by[k])):
        cat = "insect"
    elif flyer:
        cat = "flyer"
    elif swims and not walks:
        cat = "swimmer"
    else:
        cat = "land"
    stem = os.path.splitext(os.path.basename(p.path or p.name))[0].lower()
    return DonorInfo(name=stem, path=p.path or "", parts=parts, sockets=sockets, noswap=noswap, flyer=flyer, size=size,
                     category=cat)


_CODE_KEY: List[str] = []


class DonorIndex:
    """The stock creatures of one directory (:class:`DonorInfo` by name), rebuilt only when a ``.sgm`` / ``.lua`` /
    ``.lsc`` there changes. Loading 61 creatures takes seconds, so the index is also cached on disk (the combiner's
    cache dir, :func:`combotest.installdata.cache_dir`; ``COMBOTEST_DISK_CACHE=0`` turns it off)."""
    _MEMO: Dict[str, "DonorIndex"] = {}
    _STAMPS: Dict[str, tuple] = {}        # dir -> (monotonic time, stamp): peek() re-stats at most every STAMP_TTL s
    STAMP_TTL = 1.0
    VERSION = 2

    def __init__(self, directory: str, entries: "OrderedDict[str, DonorInfo]", stamp: list):
        self.directory = directory
        self.entries = entries
        self.stamp = stamp

    @classmethod
    def get(cls, directory: str, progress=None) -> "DonorIndex":
        """The index of ``directory``: from memory, else the disk cache, else built (seconds for 61 creatures)."""
        _require()
        d = os.path.abspath(directory)
        stamp = _dir_stamp(d)
        cls._STAMPS[d] = (time.monotonic(), stamp)
        idx = cls._cached(d, stamp)
        if idx is None:
            idx = cls._build(d, stamp, progress)
            cls._save_disk(idx)
            cls._MEMO[d] = idx
        return idx

    @classmethod
    def peek(cls, directory: str) -> Optional["DonorIndex"]:
        """The index when it is in memory or on disk and current, else None -- never builds (for UI draw callbacks:
        a build is an operator's or a timer's job). The directory is re-stat'ed at most every :attr:`STAMP_TTL` s."""
        _require()
        d = os.path.abspath(directory)
        seen = cls._STAMPS.get(d)
        if seen is not None and time.monotonic() - seen[0] < cls.STAMP_TTL:
            stamp = seen[1]
        else:
            stamp = _dir_stamp(d)
            cls._STAMPS[d] = (time.monotonic(), stamp)
        return cls._cached(d, stamp)

    @classmethod
    def _cached(cls, d: str, stamp: list) -> Optional["DonorIndex"]:
        hit = cls._MEMO.get(d)
        if hit is not None and hit.stamp == stamp:
            return hit
        idx = cls._load_disk(d, stamp)
        if idx is not None:
            cls._MEMO[d] = idx
        return idx

    @staticmethod
    def code_key() -> str:
        """What the cached entries were computed by: the DonorInfo fields, the part buttons, and the source of
        both ``donor_has_part`` and :func:`donor_info` (a code change rebuilds the cache)."""
        if not _CODE_KEY:
            import inspect
            h = hashlib.sha1(repr((DonorIndex.VERSION, [f.name for f in fields(DonorInfo)],
                                   [(g, list(k)) for g, k in PARTS.items()])).encode())
            from combotest.validate import donor_has_part as tool_has
            for fn in (donor_info, donor_has_part, tool_has):
                try:
                    h.update(inspect.getsource(fn).encode())
                except (OSError, TypeError):
                    h.update(getattr(fn, "__qualname__", "?").encode())
            _CODE_KEY.append(h.hexdigest())
        return _CODE_KEY[0]

    @staticmethod
    def _cache_file(d: str) -> Optional[str]:
        from combotest import installdata
        base = installdata.cache_dir()
        if not base:
            return None
        return os.path.join(base, "donor-index-" + hashlib.sha1(d.encode("utf-8")).hexdigest()[:16] + ".json")

    @classmethod
    def _load_disk(cls, d: str, stamp: list) -> Optional["DonorIndex"]:
        path = cls._cache_file(d)
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            warnings.warn(f"donor index cache {path} unreadable ({e!r}); rebuilding it")
            return None
        if doc.get("code") != cls.code_key() or doc.get("dir") != d or doc.get("stamp") != stamp:
            return None
        try:
            entries = OrderedDict((e["name"], DonorInfo(**e)) for e in doc.get("entries", []))
        except (TypeError, KeyError, AttributeError) as e:
            warnings.warn(f"donor index cache {path} does not match this code ({e!r}); rebuilding it")
            return None
        return cls(d, entries, stamp)

    @classmethod
    def _save_disk(cls, idx: "DonorIndex") -> None:
        path = cls._cache_file(idx.directory)
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"version": cls.VERSION, "code": cls.code_key(), "dir": idx.directory, "stamp": idx.stamp,
                           "entries": [e.as_dict() for e in idx.entries.values()]}, fh)
            os.replace(tmp, path)
        except OSError as e:
            warnings.warn(f"donor index cache not written to {path} ({e!r})")

    @classmethod
    def _build(cls, d: str, stamp: list, progress=None) -> "DonorIndex":
        files = [f for f, *_ in stamp]
        entries: "OrderedDict[str, DonorInfo]" = OrderedDict()
        failed = []
        for i, f in enumerate(files):
            path = os.path.join(d, f)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    info = donor_info(load_parent(path))
                entries[info.name] = info
            except Exception as e:           # a non-creature .sgm in the folder: left out, and said
                failed.append(f"{f}: {type(e).__name__}: {e}")
            if progress:
                progress(i + 1, len(files))
        if failed:
            warnings.warn(f"{len(failed)} file(s) in {d} are not combinable creatures and are not listed: "
                          + "; ".join(failed[:3]))
        return cls(d, entries, stamp)

    def names(self) -> List[str]:
        return list(self.entries)

    def grouped(self) -> "OrderedDict[str, List[DonorInfo]]":
        """The entries by :data:`CATEGORIES`, each group sorted by name."""
        out: "OrderedDict[str, List[DonorInfo]]" = OrderedDict((c, []) for c in CATEGORIES)
        for e in sorted(self.entries.values(), key=lambda e: e.name):
            out.setdefault(e.category, []).append(e)
        return out


_PARENTS: "OrderedDict[tuple, object]" = OrderedDict()
_PARENTS_MAX = 12


def parent_for_file(path: str):
    """One :class:`Parent` per creature file version (``.sgm``, or an ``.ebp`` that points at / embeds one), with its
    ``.lua`` / ``.lsc`` companions; the same object is returned while the files are unchanged (the combiner compares
    animals by identity). Its name and blueprint stem are the file stem."""
    _require()
    ap = os.path.abspath(path)
    stem = os.path.splitext(ap)[0]
    key = (ap, tuple(_file_stamp(ap)), tuple(_file_stamp(stem + ".sgm")), tuple(_file_stamp(stem + ".lua")),
           tuple(_file_stamp(stem + ".lsc")))
    hit = _PARENTS.get(key)
    if hit is not None:
        _PARENTS.move_to_end(key)
        return hit
    if not os.path.exists(ap):
        raise FileNotFoundError(f"creature file {path!r} not found")
    if ap.lower().endswith(".sgm") or os.path.exists(stem + ".sgm"):
        p = load_parent(ap)
    else:                                     # a blueprint that embeds its creature
        model = core.read_source(core.resolve_source(ap))
        comp = companions(ap)
        p = Parent.from_model(model, comp.attributes, comp.limbscale, name=os.path.basename(stem), path=ap)
    for k in [k for k in _PARENTS if k[0] == ap]:
        _PARENTS.pop(k)                       # an older version of the same file
    _PARENTS[key] = p
    while len(_PARENTS) > _PARENTS_MAX:
        _PARENTS.popitem(last=False)
    return p


# ------------------------------------------------------------------------------------------------ the edited creature
@dataclass
class Companions:
    attributes: object = None          # ICParse Attributes (None only when no .lua and no default_size)
    limbscale: object = None           # ICParse LimbScale or None
    is_default: bool = False           # True: no .lua was found, the attributes are Attributes.default(default_size)
    notes: List[str] = field(default_factory=list)   # what was read, what failed to parse


def companions(source_path: str, ebp_path: str = "", default_size: Optional[int] = None) -> Companions:
    """The ``.lua`` (limbattributes) and ``.lsc`` (limb-scale rules) beside a creature: by the stem of ``source_path``,
    then of ``ebp_path``. Without a ``.lua`` the attributes are ``Attributes.default(default_size)`` (``is_default``)
    when ``default_size`` is given, else None. A companion that exists but does not parse is left out with a note."""
    from ICParse.lua.attributes import Attributes
    from ICParse.lua.limbscale import LimbScale
    out = Companions()
    stems = []
    for p in (source_path, ebp_path):
        if p:
            s = os.path.splitext(os.path.abspath(p))[0]
            if s not in stems:
                stems.append(s)
    for ext, loader, attr in ((".lua", Attributes.load, "attributes"), (".lsc", LimbScale.load, "limbscale")):
        for s in stems:
            f = s + ext
            if not os.path.exists(f):
                continue
            try:
                setattr(out, attr, loader(f))
            except Exception as e:
                out.notes.append(f"{os.path.basename(f)} does not parse ({type(e).__name__}: {e}); left out")
                continue
            break
    if out.attributes is None and default_size is not None:
        out.attributes = Attributes.default(size=int(default_size))
        out.is_default = True
        where = os.path.basename(stems[0]) if stems else "the creature"
        out.notes.append(f"default attributes, size {int(default_size)} (no .lua next to {where})")
    return out


def blueprint_stem(props) -> str:
    """The blueprint file stem the combiner calls an add-on creature by (``.lsc`` rules match it): ``bz_ebp``'s, else
    ``bz_source``'s, else ``bz_name``. ``props`` is anything with ``.get`` (a Blender object, a dict)."""
    for key in ("bz_ebp", "bz_source"):
        v = props.get(key) if props is not None else None
        if v:
            return os.path.splitext(os.path.basename(str(v)))[0]
    return str((props.get("bz_name") if props is not None else None) or "Creature")


def edited_parent(model, props, default_size: int = 3):
    """``(Parent, Companions)`` of an add-on creature: ``model`` is its in-memory export model (``ops.edited_model``),
    ``props`` its object (``bz_source`` / ``bz_ebp`` / ``bz_name``). A fresh Parent per call: the edited creature is
    a new animal whenever its state changes."""
    _require()
    src = str(props.get("bz_source") or "")
    ebp = str(props.get("bz_ebp") or "")
    comp = companions(src, ebp, default_size=default_size)
    p = Parent.from_model(model, comp.attributes, comp.limbscale, name=blueprint_stem(props), path=(ebp or src) or None)
    return p, comp


# ------------------------------------------------------------------------------------------------------------ preview
@dataclass
class Options:
    """The panel's Advanced settings, as the combiner takes them."""
    run_lua: bool = True             # run attrcombiner.lua (ratings, cost, rank) when lupa is there
    body_scale: bool = True          # body-scale the result to its size (needs the install's size tables)
    as_spec: bool = False            # take the picks verbatim, like a saved design (no part-button rules)
    script_source: str = "game"      # "game" (what IC.exe runs) or "tool" (ComboTest.exe's copy)
    use_rules: bool = True           # the creatures' .lsc limb-scale rules
    default_size: int = 3            # size of the default attributes of a creature without a .lua

    def key(self) -> tuple:
        return (self.run_lua, self.body_scale, self.as_spec, self.script_source, self.use_rules, self.default_size)


# UI stats (attrcombiner.lua's ``ui`` table), in the readout's order
STAT_LABELS = OrderedDict([
    ("health_val", "Health"), ("armour_val", "Armour"), ("landspeed_val", "Land speed"), ("waterspeed_val", "Water speed"),
    ("airspeed_val", "Air speed"), ("sightradius_val", "Sight"), ("size_val", "Size"), ("damage_val", "Melee damage"),
    ("range2_max_val", "Range (2)"), ("range4_max_val", "Range (4)"), ("creature_rank", "Rank"), ("cost", "Cost"),
    ("costrenew", "Cost (renew)"), ("buildtime", "Build time"), ("popsize", "Pop size")])
# without Lua: the stage-2 game table's own entries
STAT_LABELS_NO_LUA = OrderedDict([
    ("hitpoints", "Health"), ("armour", "Armour"), ("speed_max", "Land speed"), ("waterspeed_max", "Water speed"),
    ("airspeed_max", "Air speed"), ("sight_radius", "Sight"), ("size", "Size"), ("melee_damage", "Melee damage")])

DROP_WORDS = {}
if ct_slots is not None:
    DROP_WORDS = {ct_slots.NO_SOCKET: "the torso creature has no socket for it",
                  ct_slots.FLAGGED: "marked Non-Swappable in Boner (fins, stubs, insect mid legs): the game never takes it from "
                                    "another creature, so it stays on its own creature. Take Picks Verbatim (Advanced) combines "
                                    "it anyway",
                  ct_slots.NO_LIMB: "the donor has no limb for it (the torso creature's own stays)"}


@dataclass
class PreviewResult:
    """One combine for the panel: the combiner's result plus the readout."""
    result: object = None                # combotest CombineResult (None when ``error``)
    torso: object = None                 # Parent
    donor: object = None                 # Parent
    role: str = ROLE_TORSO
    groups: List[str] = field(default_factory=list)          # part buttons toggled
    choices: Dict[str, str] = field(default_factory=dict)    # slot -> donor name, requested
    accepted: Dict[str, str] = field(default_factory=dict)   # slot -> donor name, taken
    dropped: List[dict] = field(default_factory=list)        # {slot, group, donor, reason, words}
    dropped_groups: Dict[str, str] = field(default_factory=dict)   # part button -> words (every slot of it dropped)
    warnings: List[str] = field(default_factory=list)        # combiner warnings besides the tool-semantics drop
    notes: List[str] = field(default_factory=list)
    slots: List[dict] = field(default_factory=list)          # {slot, label, source, grafted, scale, mode, rule_from}
    stats: List[tuple] = field(default_factory=list)         # (key, label, value, delta vs this creature alone)
    lua: bool = False
    scaled: bool = False
    load_errors: List[str] = field(default_factory=list)     # check_model errors of the combined creature
    error: str = ""
    timings: Dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.error

    def header(self) -> str:
        if not self.ok:
            return f"Combine failed: {self.error}"
        this = self.torso if self.role == ROLE_TORSO else self.donor
        other = self.donor if self.role == ROLE_TORSO else self.torso
        parts = ", ".join(PART_LABELS.get(g, g) for g in self.groups) or "no parts"
        if self.role == ROLE_TORSO:
            head = f"Combo: {creature_name(this)} torso + {creature_name(other)} {parts}"
        else:
            head = f"Combo: {creature_name(other)} torso + {creature_name(this)} {parts}"
        return head

    def summary(self) -> str:
        if not self.ok:
            return self.header()
        r = self.result
        return (f"size {r.size} · {'flyer' if r.is_flyer else 'ground'} · body scale ×{r.scale:.3f} · "
                f"{len(r.model.patches)} patches · t = {self.timings.get('combine', 0.0):.2f} s")


def size_tables():
    """The install's size tables (combotest memoizes and disk-caches them), or None without an install."""
    _require()
    from combotest import scale
    try:
        return scale.load_size_tables()
    except FileNotFoundError:
        return None


_IDENTITY: dict = {}


def creature_name(p) -> str:
    """How the readout names a creature: its blueprint file stem (what ``.lsc`` rules call it), else its name."""
    from combotest.limbscale import blueprint_name
    return blueprint_name(p)


def _stat_table(r) -> "OrderedDict[str, float]":
    a = r.attributes
    if a.ui:
        return OrderedDict((k, float(a.ui[k])) for k in STAT_LABELS if k in a.ui)
    out = OrderedDict()
    for k in STAT_LABELS_NO_LUA:
        if k in a.entries:
            try:
                out[k] = float(a.entries[k][1])
            except (TypeError, ValueError):
                pass
    return out


def _quiet_combine(torso, choices, **kw):
    from combotest.combine import combine
    with warnings.catch_warnings():            # CombineResult.warnings carries them
        warnings.simplefilter("ignore")
        return combine(torso, choices, **kw)


def identity_stats(edited, opts: Options, lua: bool, tables, state_key: str = "") -> "OrderedDict[str, float]":
    """The stats of ``edited`` combined with nothing (the creature alone). Cached for the same options and either the
    same Parent object or, when given, the same ``state_key`` (the add-on passes its object digest: every Refresh
    builds a fresh Parent of an unchanged creature, which must not cost a second combine)."""
    key = (state_key or ("id", id(edited)), opts.key(), lua, tables is not None)
    if _IDENTITY.get("key") == key and (state_key or _IDENTITY.get("parent") is edited):
        return _IDENTITY["stats"]
    r = _quiet_combine(edited, {}, name="Alone", animals=[edited], run_lua=lua, do_scale=tables is not None,
                       use_rules=opts.use_rules, script_source=opts.script_source, size_tables=tables)
    stats = _stat_table(r)
    _IDENTITY.clear()
    _IDENTITY.update(key=key, parent=edited, stats=stats)
    return stats


def picker_refusal_text(*parents) -> str:
    """Why the real ComboTest.exe refuses one of ``parents`` in either role (``combotest.validate.picker_refusals``: a
    non-torso limb, stubs included, whose class no connection carries; a Left limb without its Right mirror), as one
    line naming the creature; "" when it takes them all."""
    from combotest.validate import picker_refusals
    for p in parents:
        why = picker_refusals(p.model)
        if why:
            more = f" (and {len(why) - 1} more)" if len(why) > 1 else ""
            return f"Cannot combine {creature_name(p)}: {why[0]}{more}"
    return ""


def run_preview(edited, other, role: str, groups: Sequence[str], opts: Optional[Options] = None, *,
                name: str = "", notes: Sequence[str] = (), state_key: str = "") -> PreviewResult:
    """Combine the edited creature with ``other`` (both :class:`Parent`): ``role`` :data:`ROLE_TORSO` puts ``other``'s
    toggled ``groups`` (part buttons) on ``edited``, :data:`ROLE_DONOR` puts ``edited``'s on ``other``. Both are loaded
    animals (``animals=[torso, donor]``, as in the tool). ``notes`` (e.g. the companions' notes) head the readout.
    ``state_key`` identifies the edited creature's state for the creature-alone (delta) cache. A combined model that
    fails ``ICParse.validate.check_model`` (it would not load) is reported in ``warnings``. A combiner exception comes
    back as ``error`` (its traceback is printed to stderr; the caller reports the
    message)."""
    _require()
    opts = opts or Options()
    t0 = time.perf_counter()
    pr = PreviewResult(role=role, groups=[g for g in PARTS if g in set(groups)], notes=list(notes))
    torso, donor = (edited, other) if role == ROLE_TORSO else (other, edited)
    pr.torso, pr.donor = torso, donor
    unknown = [g for g in groups if g not in PARTS]
    if unknown:
        raise ValueError(f"unknown part(s) {unknown}; valid: {', '.join(PARTS)}")
    if torso is donor:
        raise ValueError("the other creature is this creature: pick a different one")
    choices = {k: donor for g in pr.groups for k in PARTS[g]}
    pr.choices = {k: creature_name(donor) for k in choices}
    refused = picker_refusal_text(torso, donor)
    if refused:                         # the real tool gives no combination at all: neither does the preview
        pr.error = refused
        pr.timings["total"] = time.perf_counter() - t0
        return pr
    lua = bool(opts.run_lua)
    if lua and not lua_info()["impl"]:
        lua = False
        pr.notes.append("lupa missing: ratings, cost and rank not computed")
    elif not opts.run_lua:
        pr.notes.append("attrcombiner.lua off: ratings, cost and rank not computed")
    tables = size_tables() if opts.body_scale else None
    if opts.body_scale and tables is None:
        pr.notes.append("unscaled: game not found (set the Game folder in the add-on's preferences)")
    elif not opts.body_scale:
        pr.notes.append("body scale off: the combine is not scaled to its size")
    pr.lua, pr.scaled = lua, tables is not None
    pr.notes.append("bind pose (the combiner builds no animations)")
    try:
        t1 = time.perf_counter()
        r = _quiet_combine(torso, choices, name=name or f"{creature_name(torso)}_{creature_name(donor)}", animals=[torso, donor],
                           run_lua=lua, do_scale=tables is not None, use_rules=opts.use_rules,
                           script_source=opts.script_source, as_spec=opts.as_spec, size_tables=tables)
        pr.timings["combine"] = time.perf_counter() - t1
    except Exception as e:
        pr.error = f"{type(e).__name__}: {e}"
        pr.timings["total"] = time.perf_counter() - t0
        traceback.print_exc()
        return pr
    pr.result = r
    from combotest.validate import EXPECTED_WARNINGS
    pr.warnings = [w for w in dict.fromkeys(r.warnings) if not w.startswith(EXPECTED_WARNINGS)]
    t_check = time.perf_counter()
    from ICParse import validate as icp_validate
    pr.load_errors = [p for p in icp_validate.check_model(r.model) if not p.startswith("WARN")]
    pr.warnings += [f"the combined creature won't load: {p}" for p in pr.load_errors]
    pr.timings["check_model"] = time.perf_counter() - t_check
    for p in (torso, donor):
        if p.attributes is None:
            where = os.path.basename(p.path) if getattr(p, "path", None) else creature_name(p)
            pr.notes.append(f"{creature_name(p)}: no .lua beside {where}: it contributes no attribute entries (stats "
                            f"differ from the game)")
    pr.accepted = {k: creature_name(d) for k, d in r.choices.items()}
    group_of = {k: g for g, keys in PARTS.items() for k in keys}
    by_group: Dict[str, list] = {}
    for k, d, why in (r.dropped or []):
        g = group_of.get(k, "")
        pr.dropped.append(dict(slot=k, group=g, donor=creature_name(d), reason=why, words=DROP_WORDS.get(why, why)))
        by_group.setdefault(g, []).append((k, DROP_WORDS.get(why, why)))
    for g, lst in by_group.items():
        if g and len(lst) == len(PARTS[g]):
            pr.dropped_groups[g] = "; ".join(dict.fromkeys(w for _k, w in lst))
    from combotest.limbscale import MODE_NAMES
    this = edited
    for k, v in (r.limb_scales or {}).items():
        src = r.sources.get(k)
        pr.slots.append(dict(slot=k, label=ct_slots.BY_KEY[k].label if k in ct_slots.BY_KEY else k,
                             source="this" if src is this else (creature_name(src) if src is not None else "?"),
                             grafted=bool(v.grafted), scale=float(v.scale), mode=MODE_NAMES.get(v.mode, str(v.mode)),
                             rule_from=v.rule_from or ""))
    t2 = time.perf_counter()
    try:
        base = identity_stats(edited, opts, lua, tables, state_key=state_key)
    except Exception as e:                     # the delta column goes blank, and says why
        base = {}
        pr.notes.append(f"no delta: combining this creature alone failed ({type(e).__name__}: {e})")
    pr.timings["identity"] = time.perf_counter() - t2
    labels = STAT_LABELS if r.attributes.ui else STAT_LABELS_NO_LUA
    for k, v in _stat_table(r).items():
        d = v - base[k] if k in base else None
        pr.stats.append((k, labels[k], v, d))
    pr.timings["total"] = time.perf_counter() - t0
    return pr


# -------------------------------------------------------------------------------------------------------- ghost arrays
_TEX_RGBA: "OrderedDict[tuple, tuple]" = OrderedDict()
_TEX_RGBA_MAX = 96


def texture_key(tex) -> tuple:
    """(name, width, height, digest of the palette + top mip): the same skin in any creature is one key."""
    h = hashlib.sha1(bytes(tex.palette or b""))
    h.update(bytes(tex.mips[0]) if tex.mips else b"")
    return (str(tex.name), int(tex.width), int(tex.height), h.hexdigest())


def texture_rgba(tex) -> tuple:
    """``(key, top-down RGBA8 bytes, w, h)`` of a creature texture with IC's magenta colour key made transparent
    (as the imported images are), decoded once per :func:`texture_key`."""
    from ICParse.codecs import texture as ICtex
    key = texture_key(tex)
    hit = _TEX_RGBA.get(key)
    if hit is not None:
        _TEX_RGBA.move_to_end(key)
        return hit
    w, h = int(tex.width), int(tex.height)
    rgba = np.frombuffer(ICtex.decode_texture(tex, 0), dtype=np.uint8)[:w * h * 4].copy().reshape(-1, 4)
    rgba[material.magenta_key_mask(rgba), 3] = 0
    out = (key, rgba.tobytes(), w, h)
    _TEX_RGBA[key] = out
    while len(_TEX_RGBA) > _TEX_RGBA_MAX:
        _TEX_RGBA.popitem(last=False)
    return out


def topology_digest(patches: np.ndarray, types) -> str:
    h = hashlib.sha1(np.ascontiguousarray(patches, dtype=np.int64).tobytes())
    h.update(np.asarray(list(types), dtype=np.int64).tobytes())
    return h.hexdigest()


def build_ghost_arrays(result, density: int, topo=None) -> dict:
    """The combined creature's overlay arrays, Blender space (numpy only; ``combopreview.make_ghost_batches`` turns
    them into GPU batches). ``result`` is a CombineResult (or a creature SgmModel). ``topo``: the previous call's
    ``arrays["topo"]``, reused when the patch table and density are unchanged (the usual refresh while reshaping).

    Keys: ``verts`` (Nv,3) control points; ``patches`` (Np,16); ``surfV``/``surfN`` (Np*density^2, 3) positions and
    engine normals; ``tris`` (T,3); ``density``; ``uvs`` (M,2); ``patch_mat`` (Np,); per material slot
    ``slot_tex_index`` (texture index or -1) and ``slot_tint`` (base colour, white when textured); ``textures``
    [(key, rgba, w, h) per texture index]; ``patch_slot`` (Np,) combiner slot key per patch ("" = none) and
    ``part_colors`` (Np,3) from ``combotest.slots.SLOT_COLORS``; ``bbox_min``/``bbox_max`` of the surface; ``topo``
    and ``topo_reused``."""
    from ICParse.codecs import reconstruct as RC
    m = getattr(result, "model", result)
    n = max(2, int(density))
    verts = core.sgm_to_blender(RC.world_vertices(m))
    patches = core.transpose_patches([list(p.control_points) for p in m.patches])      # Blender's grids
    types = [int(p.type) for p in m.patches]
    digest = topology_digest(patches, types)
    reused = topo is not None and getattr(topo, "bz_digest", None) == digest and topo.n == n
    if not reused:
        topo = fastsurf.TopoCache(patches, n, types)
        topo.bz_digest = digest
    surfV, surfN = topo.eval_all(verts, rest_verts=verts)
    patch_uv = core.transpose_patch_uv([list(p.uvs) for p in m.patches])
    uvs = core.surface_uvs(patch_uv, n)
    patch_mat = np.array([int(p.material_index) for p in m.patches], dtype=np.int64)
    table = material.material_table(m)
    first_tex = {}
    for i, t in enumerate(m.textures):
        first_tex.setdefault(t.name, i)
    slot_tex_index = [first_tex.get(s["tex"], -1) if s["tex"] else -1 for s in table]
    slot_tint = [(1.0, 1.0, 1.0) if ti >= 0 else tuple(float(c) for c in s["color"])
                 for ti, s in zip(slot_tex_index, table)]
    textures = []
    for t in m.textures:
        try:
            textures.append(texture_rgba(t))
        except Exception as e:                  # an undecodable skin draws flat, and says so
            textures.append((None, None, 0, 0))
            warnings.warn(f"texture {t.name!r} not decoded ({type(e).__name__}: {e}); drawn flat")
    patch_slot = np.array([""] * len(patches), dtype=object)
    gray = (0.55, 0.55, 0.58)
    part_colors = np.tile(np.array(gray, dtype=np.float64), (len(patches), 1))
    if ct_slots is not None:
        for lm in m.limbs:
            s = ct_slots.slot_for_limb_id(int(lm.limb_id))
            if s is None:
                continue
            col = ct_slots.SLOT_COLORS.get(s.key, gray)
            for q in lm.prims:
                if 0 <= int(q) < len(patches):
                    patch_slot[int(q)] = s.key
                    part_colors[int(q)] = col
    bmin = surfV.min(axis=0) if len(surfV) else np.zeros(3)
    bmax = surfV.max(axis=0) if len(surfV) else np.zeros(3)
    return dict(verts=verts, patches=patches, surfV=surfV, surfN=surfN, tris=topo.tris, density=n, uvs=uvs,
                patch_mat=patch_mat, slot_tex_index=slot_tex_index, slot_tint=slot_tint, textures=textures,
                patch_slot=patch_slot, part_colors=part_colors, bbox_min=bmin, bbox_max=bmax, topo=topo,
                topo_reused=reused)


# --------------------------------------------------------------------------------------------------- connection rings
_RING_GEO = None


def _ring_geo():
    """One ring-direction cache for the stock partners (their Parents come from :func:`parent_for_file`, stable per file
    version, and ``_Geo`` re-checks model identity)."""
    global _RING_GEO
    if _RING_GEO is None:
        from combotest import validate as V
        _RING_GEO = V._Geo()
    return _RING_GEO


def torso_centre(world, patches, patch_limb, limb_ids) -> Optional[np.ndarray]:
    """``combotest.validate.torso_centroid`` of an editor's tables: the control points of the first torso limb's
    patches (the limb the combiner takes as the torso, ``Parent.limb_for(TORSO)``). None without one."""
    _require()
    from combotest import validate as V
    ids = [int(x) for x in (limb_ids or [])]
    li = next((i for i, lid in enumerate(ids) if lid & core.CLASS_MASK == core.TORSO_CODE), None)
    if li is None or patch_limb is None:
        return None
    prims = np.nonzero(np.asarray(patch_limb, dtype=np.int64) == li)[0]
    return V.torso_centroid(world, np.asarray(patches, dtype=np.int64).reshape(-1, 16), prims)


WINDING_UNCLEAR_DEG = 5.0       # a ring this close to a right angle with the direction to the torso's centre cannot be judged by that direction


RING_SAME_DEG = 1.0     # mean by-index corner angle below which a ring counts as the very same ring as a stock partner's


def partners_weld_reversed(points, partner_dirs, same_only: bool = False) -> "tuple[int, int]":
    """``(reversed, n)``: how many of the ``n`` stock partner rings (``partner_dirs``: :func:`partner_ring_dirs`, rings of another corner
    count skipped) the by-index weld pairs ``points`` REVERSED against (``combotest.validate.best_alignment``: the reversed pairing beats the
    by-index one by more than the twist margin, the test ``ring_fix`` already uses). Unlike the torso-centroid rule this does not depend on the
    rest of the torso: it is what the combiner's weld (corner i to corner i) actually needs, so a pinned ring that is the stock
    ring stays judged the same however far the retarget moves the torso. ``same_only``: only a ring that IS a partner's ring (mean corner angle
    under :data:`RING_SAME_DEG`) counts as welded the stock way; the by-index measure is not trusted (a ring picked at any start corner, as Pick
    Ring gets it, makes it unreliable for stub rings)."""
    _require()
    from combotest import validate as V
    X = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    refs = [np.asarray(d, dtype=np.float64) for d in (partner_dirs or ()) if d is not None and len(d) == len(X)]
    if len(X) < 3 or not refs:
        return 0, 0
    dt = V._corner_dirs(X)
    flips = 0
    for dd in refs:
        (rev, _sh), a0, ab = V.best_alignment(dt, dd)
        if a0 < RING_SAME_DEG:
            return 0, len(refs)             # it IS a stock partner's ring (a retarget pins them): the strongest evidence there is
        flips += int(rev == 1 and a0 - ab > V.TWIST_MARGIN_DEG)
    return (len(refs) if same_only else flips), len(refs)


def winding_verdict(points, torso_centre_, partner_dirs=None, same_only: bool = False) -> str:
    """``"into"``, ``"out"`` or ``"unclear"``: which way the ring ``points`` (corners in ring order, IC axes) is wound against the torso.

    The combiner's own rule (``combotest.validate.winding_into_torso``: the ring's winding normal points from its centre toward the torso's
    centroid; true of every base-game ring) is a PROXY for what the game needs, the same corner order as the stock partner's ring
    (the by-index weld), and it is ill-conditioned for a ring that lies sideways to the centroid: a small move of the torso's
    centroid changes the sign of the dot product, and the rule then calls unchanged, pinned rings reversed.
    So a ring within :data:`WINDING_UNCLEAR_DEG` of a right angle is ``"unclear"``: it is neither reversed nor refused on that evidence.

    ``partner_dirs`` (optional, :func:`partner_ring_dirs`): with the stock partners at hand the ring the centroid rule calls ``"out"`` is judged
    by the weld itself as well (:func:`partners_weld_reversed`): ``"out"`` only when at least one partner pairs it reversed. A ring that every
    partner welds the stock way is ``"unclear"`` whatever the centroid says -- the band above is a margin (a torso moved more than about 2 cm
    turns a ring 5 to 10 degrees off a right angle too), the partners are the evidence. The cost:
    some base-game rings (snake stubs whose plane differs from the partners') are not caught when reversed by hand by this rule;
    the combiner's own by-index weld check still sees them. ``same_only`` (Pick Ring and the start plan, which get a ring picked from any start
    corner and exist to REVERSE one): only a ring that is a partner's very ring is spared, so what Pick Ring reverses is unchanged by partners."""
    _require()
    from combotest.frames import ring_centre, ring_normal
    X = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    d = np.asarray(torso_centre_, dtype=np.float64) - ring_centre(X)
    dist = float(np.linalg.norm(d))
    if dist < 1e-9:
        return "unclear"
    cos = float(ring_normal(X) @ d) / dist
    if abs(cos) < float(np.sin(np.radians(WINDING_UNCLEAR_DEG))):
        return "unclear"
    if cos > 0:
        return "into"
    flips, n = partners_weld_reversed(X, partner_dirs, same_only)
    if n and flips == 0:
        return "unclear"                    # the centroid rule says out, the partners weld it the stock way
    return "out"


_WOUND_LINE = None


def settle_winding_lines(lines: Sequence[str], model, stock_dir: Optional[str] = None, exclude_stem: str = "") -> List[str]:
    """``lines`` (``COMBINE ...`` messages of the combiner's precheck) with each ``ring <slot>: wound with its normal out of the torso`` ERROR
    whose ring is only ``"unclear"`` (:func:`winding_verdict`) turned into an INFO line that says so. A ring that is clearly wound out stays
    an ERROR. ``model``: the creature model the lines were made from; ``stock_dir`` / ``exclude_stem``: the stock creatures whose rings the
    weld is measured against (:func:`winding_verdict` ``partner_dirs``), when known."""
    import re
    global _WOUND_LINE
    if _WOUND_LINE is None:
        _WOUND_LINE = re.compile(r"^COMBINE ERROR precheck: ring (\w+): wound with its normal out of the torso")
    hits = [(i, _WOUND_LINE.match(str(l))) for i, l in enumerate(lines)]
    hits = [(i, m.group(1)) for i, m in hits if m]
    if not hits or combotest is None or model is None:
        return list(lines)
    from ICParse.codecs import reconstruct as R
    from ICParse.sgm.creature.model import ring_corners
    from combotest.slots import slot_for_flags2, TORSO
    W = np.asarray(R.world_vertices(model), dtype=np.float64)
    P = np.array([list(p.control_points) for p in model.patches], dtype=np.int64).reshape(-1, 16)
    tc = torso_centre(W, P, core._patch_limb_of(model), [int(lm.limb_id) for lm in model.limbs])
    out = list(lines)
    if tc is None:
        return out
    for i, key in hits:
        verdicts = []
        for lc in model.limb_connections:
            sl = slot_for_flags2(lc.flags2)
            cs = ring_corners(lc.vertex_indices) if sl is not None and sl is not TORSO and sl.key == key else None
            if cs is not None:
                dirs, _n = partner_ring_dirs(stock_dir, key, exclude_stem)
                verdicts.append(winding_verdict(W[cs], tc, dirs))
        if verdicts and all(v == "unclear" for v in verdicts):
            out[i] = (f"COMBINE INFO precheck: ring {key}: lies sideways to the torso's centre, so its direction cannot say which way it is "
                      f"wound. Not an error: the combines below weld it against stock partners")
    return out


def partner_ring_dirs(stock_dir: Optional[str], slot_key: Optional[str], exclude_stem: str = "",
                      donors_only: bool = False):
    """``(dirs, names)``: the ring corner directions (``combotest.validate._Geo.ring_dirs``) of the Quick donors that
    have a ring for ``slot_key``, from ``stock_dir`` -- the partners a picked ring's start is aligned to. The creature
    itself (``exclude_stem``) is left out. Empty without a stock dir.

    ``donors_only``: keep only the partners that really donate that part -- the ones whose limb for the slot has
    patches -- which is exactly the partner set ``combotest.validate.precheck`` measures the by-index weld against
    (a stub socket's ring is authored but never grafted from)."""
    _require()
    from combotest import validate as V
    from combotest.slots import BY_KEY
    dirs, names = [], []
    if not stock_dir or not slot_key:
        return dirs, names
    for q in V.QUICK_DONORS:
        if q == (exclude_stem or "").lower():
            continue
        path = os.path.join(stock_dir, q + ".sgm")
        if not os.path.exists(path):
            continue
        parent = parent_for_file(path)
        if donors_only and not any(lm.prims for lm in parent.limbs_for(BY_KEY[slot_key])):
            continue
        d = _ring_geo().ring_dirs(parent.model, slot_key)
        if d is not None:
            dirs.append(d)
            names.append(q)
    return dirs, names


NO_PARTNERS_NOTE = ("start corner kept as picked: no stock creatures to align with (Combine Preview > Extract Stock "
                    "Creatures)")


def ring_fix(points, corners, slot_key, torso_centre_, partner_dirs) -> "tuple[list, list]":
    """A picked ring in the stock order: ``(corners, notes)``. ``points``: the corners' world positions (IC axes) in
    pick order. First the winding (``winding_into_torso``: reversed when its normal points out of the torso), then the
    start (``align_ring_start`` against ``partner_dirs``: rotated only when the pick is more than the twist margin worse
    than the partners' best start). Without partners only the winding is fixed, and a note says so."""
    corners = [int(c) for c in corners]
    X = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(X) != len(corners):
        raise ValueError(f"{len(X)} positions for {len(corners)} corners")
    if combotest is None:
        return corners, [f"winding and start not checked: the creature combiner could not be loaded ({IMPORT_ERROR})"]
    from combotest import validate as V
    notes = []
    if torso_centre_ is None:
        notes.append("winding not checked: the creature has no torso patches")
    elif winding_verdict(X, torso_centre_, partner_dirs, same_only=True) == "out":
        corners, X = corners[::-1], X[::-1]
        notes.append("reversed to wind into the torso")
    elif winding_verdict(X, torso_centre_, partner_dirs, same_only=True) == "unclear":
        notes.append("winding kept as picked: the ring lies sideways to the torso's centre, which cannot tell its direction")
    refs = [d for d in (partner_dirs or ()) if d is not None and len(d) == len(corners)]
    if not slot_key:
        notes.append("start corner kept as picked: the connection is no combiner limb slot")
    elif not refs:
        notes.append(NO_PARTNERS_NOTE if not partner_dirs else
                     f"start corner kept as picked: no stock {slot_key} ring has {len(corners)} corners")
    else:
        al = V.align_ring_start(X, refs)
        if al.shift:
            corners, X = corners[al.shift:] + corners[:al.shift], np.roll(X, -al.shift, axis=0)
            notes.append(f"rotated by {al.shift} to match the stock start (mean corner angle to {al.n} stock {slot_key} "
                         f"rings {al.before_deg:.0f} deg -> {al.after_deg:.0f} deg)")
        if torso_centre_ is not None:
            dt = V._corner_dirs(X)
            flips = 0
            for dd in refs:
                (rev, _sh), a0, ab = V.best_alignment(dt, dd)
                flips += int(rev == 1 and a0 - ab > V.TWIST_MARGIN_DEG)
            if 2 * flips > len(refs):
                notes.append(f"winding rule and stock partners disagree ({flips}/{len(refs)} partners weld it reversed); "
                             "kept the winding rule")
    return corners, notes


# ------------------------------------------------------------------------------------- the ring-start optimiser
MIN_START_GAIN_DEG = 1.0        # a rotation must win at least this much of the precheck's measure to be worth writing


@dataclass
class RingStartPlan:
    """What rotating one connection ring's start corner would do to the by-index weld, measured the way
    ``combotest.validate.precheck`` measures it: per stock partner, the mean corner angle of the pairing the combiner
    really makes (it welds corner i to corner i) against the best pairing of the same two rings, and how
    many partners that leaves over :data:`combotest.validate.TWIST_MARGIN_DEG`.

    ``shift`` is the rotation to apply (0 = keep the start); ``corners`` the ring after it. ``ok`` says there is
    something to write, and ``reason`` says in words what the plan is or why there is nothing to do."""
    slot_key: Optional[str] = None
    corners: List[int] = field(default_factory=list)
    shift: int = 0
    k: int = 0
    n: int = 0                                     # partners compared
    partners: List[str] = field(default_factory=list)
    donors_only: bool = True
    margin: float = 0.0
    before_deg: float = 0.0                        # mean by-index corner angle over the partners, as the ring is now
    after_deg: float = 0.0                         # ... after ``shift``
    best_deg: float = 0.0                          # ... of the best rotation
    gap_before: float = 0.0                        # worst per-partner gap to the best pairing, as the ring is now
    gap_after: float = 0.0
    over_before: int = 0                           # partners whose gap is over ``margin`` (the precheck's ERROR)
    over_after: int = 0
    reversed_wins: int = 0                         # partners the REVERSED pairing beats every rotation for
    reversed_gain: float = 0.0                     # by how much, worst partner (degrees)
    move_mm: float = 0.0                           # how far the rotation moves the worst welded corner (mm)
    ok: bool = False
    reason: str = ""
    scores: List[float] = field(default_factory=list)      # mean by-index angle per rotation (index = shift)


def ring_start_plan(points, corners, slot_key, torso_centre_, partner_dirs, partner_names=(),
                    margin: Optional[float] = None, gain: float = MIN_START_GAIN_DEG) -> RingStartPlan:
    """:class:`RingStartPlan` for one ring: try every rotation of its corner list and keep the one that minimises the
    precheck's weld error (partners over the margin first, then the worst gap, then the mean by-index angle).

    ``points``: the corners' world positions (IC axes) in ring order; ``partner_dirs`` / ``partner_names``: the stock
    partners' ring directions (:func:`partner_ring_dirs`, ``donors_only=True`` for the precheck's own set). Rotations
    only: the other thing the file could express, reversing the corner list, flips the ring's winding normal, and every
    base-game ring winds into the torso (``winding_into_torso``) -- so a ring the partners would rather
    weld reversed is REPORTED (``reversed_wins`` / ``reversed_gain``), never reversed."""
    corners = [int(c) for c in corners]
    X = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(X) != len(corners):
        raise ValueError(f"{len(X)} positions for {len(corners)} corners")
    plan = RingStartPlan(slot_key=slot_key, corners=list(corners), k=len(corners))
    if combotest is None:
        plan.reason = f"the creature combiner could not be loaded ({IMPORT_ERROR}): the weld cannot be measured"
        return plan
    from combotest import validate as V
    plan.margin = float(V.TWIST_MARGIN_DEG if margin is None else margin)
    if not slot_key:
        plan.reason = "the connection is no combiner limb slot: nothing welds at it"
        return plan
    if len(corners) < 3:
        plan.reason = f"a ring needs at least 3 corners ({len(corners)}): pick it first (Pick Ring)"
        return plan
    pairs = [(n, d) for n, d in zip(list(partner_names) + [""] * len(list(partner_dirs)), partner_dirs)
             if d is not None and len(d) == len(corners)]
    plan.partners, refs = [str(n) for n, _d in pairs], [np.asarray(d, dtype=np.float64) for _n, d in pairs]
    plan.n = len(refs)
    if not refs:
        plan.reason = (NO_PARTNERS_NOTE if not list(partner_dirs) else
                       f"no stock {slot_key} ring has {len(corners)} corners to compare with")
        return plan
    if torso_centre_ is not None and winding_verdict(X, torso_centre_, refs, same_only=True) == "out":
        plan.reason = ("wound with its normal out of the torso (a ring must point into the torso): re-pick it "
                       "(Pick Ring reverses it) -- no rotation fixes a reversed ring")
        return plan
    best_pairing = [V.best_alignment(V._corner_dirs(X), dd)[2] for dd in refs]   # shift-invariant: the same two rings
    rows = []
    for s in range(len(corners)):
        by_index = [V.best_alignment(V._corner_dirs(np.roll(X, -s, axis=0)), dd)[1] for dd in refs]
        gaps = [a - b for a, b in zip(by_index, best_pairing)]
        # the mean by-index angle leads (``align_ring_start``'s measure, what Pick Ring aligns to), the precheck's
        # count over the margin and worst gap break ties: on a ring whose PLANE differs from the stock sockets the
        # gaps are dominated by the reversal the winding rule forbids, so they cannot order the rotations
        rows.append((float(np.mean(by_index)), sum(g > plan.margin for g in gaps), max(gaps), s))
    plan.scores = [r[0] for r in rows]
    now, best = rows[0], min(rows)
    plan.before_deg, plan.over_before, plan.gap_before = now[0], now[1], now[2]
    plan.best_deg = min(r[0] for r in rows)
    # the reversed pairing beats EVERY rotation exactly when a partner's best rotation still misses its best pairing
    per_partner_rot = [min(V.best_alignment(V._corner_dirs(np.roll(X, -s, axis=0)), dd)[1]
                           for s in range(len(corners))) for dd in refs]
    rev = [r - b for r, b in zip(per_partner_rot, best_pairing)]
    plan.reversed_wins = sum(1 for g in rev if g > 1e-9)
    plan.reversed_gain = max(rev) if rev else 0.0
    improves = now[0] - best[0] > float(gain)
    if not improves:
        plan.shift, plan.after_deg, plan.over_after, plan.gap_after = 0, now[0], now[1], now[2]
        plan.reason = (f"already the best of the {len(corners)} starts against {plan.n} stock "
                       f"{slot_key} ring(s) (mean corner angle {now[0]:.0f} deg, worst gap {now[2]:.0f} deg)"
                       if now == best else
                       f"kept: the best start would win only {now[0] - best[0]:.1f} deg of the mean corner angle "
                       f"(under {float(gain):.1f})")
        if plan.reversed_gain > plan.margin:
            plan.reason += (f"; partners would weld it better REVERSED ({plan.reversed_wins}/{plan.n}, by "
                            f"{plan.reversed_gain:.0f} deg), which the winding rule forbids: the ring's plane differs from "
                            "the stock sockets")
        return plan
    s = best[3]
    plan.ok, plan.shift = True, s
    plan.corners = corners[s:] + corners[:s]
    plan.after_deg, plan.over_after, plan.gap_after = best[0], best[1], best[2]
    # what the rotation is worth in metres: the weld pairs corner i to corner i, so rotating by s moves each welded
    # corner to where corner i + s was -- a seam displacement of up to the ring's own size
    plan.move_mm = float(max(np.linalg.norm(X[i] - X[(i + s) % len(corners)]) for i in range(len(corners)))) * 1000.0
    plan.reason = (f"rotate the start by {s} (corner {corners[s]} first): mean corner angle to {plan.n} stock {slot_key} "
                   f"ring(s) {now[0]:.0f} -> {best[0]:.0f} deg, worst gap {now[2]:.0f} -> {best[2]:.0f} deg, partners over "
                   f"the {plan.margin:.0f} deg margin {now[1]} -> {best[1]}; worst corner moves {plan.move_mm:.0f} mm along "
                   "the seam")
    if plan.over_after:
        plan.reason += (f"; {plan.over_after} partner(s) stay over the margin: reversed beats every rotation by "
                        f"{plan.reversed_gain:.0f} deg, so the ring's PLANE differs from the stock sockets")
    return plan


@dataclass
class RingStatus:
    """One connection ring for the panel readout and the overlay colour."""
    index: int
    name: str
    bone: str
    slot_key: Optional[str]
    slot_label: str
    flags1: int
    flags2: int
    ring: List[int]                   # corner loop (enc 1) or verbatim ints (enc 0)
    enc: int
    want: Optional[int]
    level: str = "OK"                 # OK / WARN / ERROR
    problems: List[str] = field(default_factory=list)

    @property
    def attach(self) -> str:
        return core.attach_text(self.flags1)

    @property
    def size_text(self) -> str:
        if not self.enc:
            return f"{len(self.ring)} ints (verbatim)"
        return f"{len(self.ring)}/{self.want if self.want is not None else '-'} corners"

    def _flag(self, level: str, text: str) -> None:
        order = {"OK": 0, "WARN": 1, "ERROR": 2}
        if order[level] > order[self.level]:
            self.level = level
        self.problems.append(f"{level} {text}")


def ring_status(world, patches, patch_limb, limb_ids, rows, stock_dir: Optional[str] = None,
                exclude_stem: str = "") -> List[RingStatus]:
    """Per connection ring: ERROR (a non-standard encoding kept verbatim, the wrong corner count, an edge that is no
    patch side, an index out of the pool, wound out of the torso), WARN (the start is off the stock partners' start,
    or no partners to check it against), else OK. ``rows``: ``(name, bone, ring, enc, flags1, flags2)`` per
    connection (the add-on store); ``world``: the control points in IC axes."""
    W = np.asarray(world, dtype=np.float64).reshape(-1, 3)
    sides = core.corner_sides(patches)
    tc = torso_centre(world, patches, patch_limb, limb_ids) if combotest is not None else None
    out = []
    for i, (name, bone, ring, enc, f1, f2) in enumerate(rows):
        key, label, want = core.ring_slot(f2)
        st = RingStatus(i, str(name), str(bone), key, label, int(f1) & 0xFFFFFFFF, int(f2) & 0xFFFFFFFF,
                        [int(v) for v in ring], int(enc), want)
        out.append(st)
        if not enc:
            st._flag("ERROR", f"non-standard ring encoding ({len(ring)} ints) kept as imported: re-pick it to make it a "
                              "stock ring")
            continue
        if any(not 0 <= v < len(W) for v in st.ring):
            st._flag("ERROR", "ring index out of the control-point pool")
            continue
        if want is not None and len(st.ring) != want:
            st._flag("ERROR", f"{len(st.ring)} corners, the {label} slot needs {want} ({2 * want} ints)")
        if len(st.ring) < 3:
            st._flag("ERROR", f"a ring needs at least 3 corners ({len(st.ring)})")
            continue
        why = core.ring_edge_error(st.ring, sides)
        if why:
            st._flag("ERROR", why)
        if key is None or st.level == "ERROR":
            continue                                        # the torso's own record / no combiner slot / already broken
        where = core.RingPicker(patches, patch_limb, limb_ids, f1, f2, name=name, sides=sides).placement_problem(st.ring)
        if where:
            st._flag("WARN", where)
        if st.flags1 & core.CLASS_MASK != core.TORSO_CODE:
            continue                                        # not a torso socket: the combiner does not graft at it
        if combotest is None:
            st._flag("WARN", f"winding and start not checked: the creature combiner could not be loaded ({IMPORT_ERROR})")
            continue
        from combotest import validate as V
        X = W[st.ring]
        dirs, _names = partner_ring_dirs(stock_dir, key, exclude_stem)
        refs = [d for d in dirs if len(d) == len(st.ring)]
        if tc is not None and winding_verdict(X, tc, refs) == "out":
            st._flag("ERROR", "wound with its normal out of the torso (a ring must point into the torso): Pick Ring "
                              "puts it in the stock order")
            continue
        if not refs:
            st._flag("WARN", "start not checked: no stock creatures to align with (Combine Preview > Extract Stock "
                             "Creatures)" if not dirs else f"start not checked: no stock {key} ring has "
                             f"{len(st.ring)} corners")
            continue
        al = V.align_ring_start(X, refs)
        if al.shift:
            st._flag("WARN", f"the start corner is {al.shift} off the stock start (mean angle to {al.n} stock rings "
                             f"{al.before_deg:.0f} deg, {al.after_deg:.0f} deg from corner {al.shift}), twisting the weld: "
                             "use Optimise Ring Start or Pick Ring")
    return out


# ---------------------------------------------------------------------------------------------------- the Quick tier
def quick_validate(parent, stock_dir: Optional[str], opts: Optional[Options] = None, progress=None):
    """The Quick combine tier (``combotest.validate.validate_candidate(donors="quick")``) for ``parent``: its static
    precheck, 13 combines with the Quick donors and the coverage over them. A parent without attributes gets
    ``Attributes.default(opts.default_size)`` (as ``combotest validate`` does). Raises :class:`SetupError` when the
    install data or the Quick donors are missing."""
    _require()
    from combotest import validate as V
    opts = opts or Options()
    if parent.attributes is None:
        from ICParse.lua.attributes import Attributes
        parent.attributes = Attributes.default(size=int(opts.default_size))
    try:
        with warnings.catch_warnings():            # the report rows carry every combine warning
            warnings.simplefilter("ignore")
            # run_lua None: the combiner runs the script when lupa is installed and notes it (INFO) when it is not
            rep = V.validate_candidate(parent, donors="quick", jobs=1, script_source=opts.script_source,
                                       stock_dir=stock_dir, run_lua=None if opts.run_lua else False, progress=progress)
    except V.SetupError as e:
        raise SetupError(str(e)) from e
    if not opts.run_lua:
        rep.notes.append("INFO attrcombiner.lua is off (Advanced): ratings, cost and rank are not checked")
    return rep


def ring_placement_messages(model) -> List[str]:
    """``COMBINE WARN precheck:`` lines for connection rings that are probably not where their slot's limb attaches
    (``core.RingPicker.placement_problem``, from the model's own patch / limb tables): the static check Validate runs
    with the combines, and alone when Combine Checks is Off."""
    from ICParse.sgm.creature.model import ring_corners
    P = np.array([list(p.control_points) for p in model.patches], dtype=np.int64).reshape(-1, 16)
    pl = core._patch_limb_of(model)
    ids = [int(lm.limb_id) for lm in model.limbs]
    sides = core.corner_sides(P)
    out = []
    for lc in model.limb_connections:
        c = ring_corners(lc.vertex_indices)
        if c is None:
            continue                                        # a non-standard ring: the precheck and ring status say so
        why = core.RingPicker(P, pl, ids, lc.flags1, lc.flags2, name=lc.name, sides=sides).placement_problem(c)
        if why:
            out.append(f"COMBINE WARN precheck: ring {lc.name}: {why}")
    return out


def precheck_messages(parent, opts: Optional[Options] = None) -> List[str]:
    """Only the static precheck (milliseconds, no donors, no combines) as ``COMBINE ...`` lines -- what Validate still
    runs when Combine Checks is Off, so ring and bone authoring checks never go silent. The ring winding into the torso
    is checked without partners; the start against stock partners is left to the Quick tier (said in an INFO line)."""
    _require()
    from combotest import validate as V
    opts = opts or Options()
    if parent.attributes is None:
        from ICParse.lua.attributes import Attributes
        parent.attributes = Attributes.default(size=int(opts.default_size))
    out = ["COMBINE INFO setup: combine checks off: static precheck only (no combines; ring start / winding against "
           "stock creatures not checked, ring winding into the torso checked)"]
    lines = out + ["COMBINE " + msg.split(" ", 1)[0] + " precheck: " + msg.split(" ", 1)[1] for msg in V.precheck(parent)]
    return settle_winding_lines(lines, parent.model)


def format_messages(report=None, notes: Sequence[str] = (), setup_error: str = "") -> List[str]:
    """``COMBINE <LEVEL> ...`` lines for Validate: ``notes`` as INFO, a setup failure as ERROR, then the report's
    lines (precheck, per-design issues, coverage; the summary INFO last)."""
    out = [f"COMBINE INFO setup: {n}" for n in notes]
    if setup_error:
        out.append(f"COMBINE ERROR setup: {setup_error}")
    if report is not None:
        out += ["COMBINE " + line for line in report.lines()]
    return out


def combine_issue_count(messages: Sequence[str]) -> int:
    """The ``COMBINE ERROR`` / ``COMBINE WARN`` lines among validation messages (INFO lines are not issues)."""
    return sum(1 for m in messages if m.startswith(("COMBINE ERROR", "COMBINE WARN")))
