"""Shared types of every slice: ``Issue``, bundles and reports, the ``Slice`` protocol and registry, ``refs.rename``,
``claim_edit`` and small helpers.

Never import bpy here (pure tests import it) and reach ICParse only inside functions. A slice's ``read`` / ``changed`` /
``apply`` / ``check`` are pure; only ``build`` and ``gather`` touch Blender. ``OutBundle.shared`` takes ``SHARED_KEYS``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

PURE = True              # promise read by tests/so_contract_test.py: importing this module never imports bpy
SLICE_API = 1            # bumped only by the owner of this file when the Slice protocol changes

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
LEVELS = (ERROR, WARN, INFO)

# The export stages, in the order they run (PLAN_FINAL section 5). A slice named like a stage keeps that stage's number.
STAGE_ORDER = {
    "materials": 10, "textures": 12, "skeleton": 20, "geometry": 30, "morphs": 40, "markers": 50, "bounds": 60,
    "anim": 70, "controller_meta": 80, "controller_motion": 81, "controller_events": 82, "files": 90, "validate": 100,
}

# The hand-over keys of OutBundle.shared and the stage that writes each one (PLAN_FINAL C22).
SHARED_OWNERS = {
    "skeleton": "skeleton",    # {"bone_map": {src_index: new_index | None}, "names": [...], "structural": bool}
    "materials": "materials",  # {"renames": {old: new}, "added": [...]}
    "textures": "textures",    # the textures the stage wrote / still has to write
    "geometry": "geometry",    # {"rebuilt", "source_vertex", "added_parts", "removed_parts", "renamed", "structural"}
    "morphs": "morphs",        # {"object_channels": {clip: [ObjectChannel, ...]}}
    "markers": "markers",
    "bounds": "bounds",
    "clips": "anim",
}
SHARED_KEYS = set(SHARED_OWNERS)      # a set that declare_shared() extends in place, so every importer sees the same object
BASE_SHARED_KEYS = frozenset(SHARED_OWNERS)

# The edits the safety guard refuses until the slice that exports them lands (PLAN_FINAL C22, slice B02).
GUARD_CODES = (
    "skeleton_edit", "skeleton_rename", "object_transform", "modifier", "shape_key", "unclaimed_object", "shader_map",
    "material_edit", "morph_part_edit", "source_changed", "part_name", "normals", "image_stamp", "bounds_stale",
)
_EXTRA_GUARD_CODES: dict = {}      # code -> who declared it (declare_guard_code)

REF_KINDS = ("bone", "clip", "marker", "material", "part")


# ============================================================================================================== problems
def _spec_exists(name: str) -> bool:
    """Whether the module ``name`` can be imported at all, without importing it (a missing parent package counts as no)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@dataclass(frozen=True)
class FallbackFinding:
    """Stand-in for ICParse's ``Finding`` while slice I00 (ICParse/entity/findings.py) has not landed: same field names, same
    read-only aliases, same text form. ``finding_class()`` returns the real one as soon as it exists."""
    id: str
    severity: str
    where: str
    text: str
    fix: str | None = None
    evidence: str = ""

    @property
    def code(self) -> str:
        return self.id

    @property
    def level(self) -> str:
        return self.severity

    def to_str(self) -> str:
        return f"{self.severity}: [{self.id}] {self.text}"


def finding_class():
    """ICParse's ``Finding`` when it exists, else :class:`FallbackFinding`. (An error inside ICParse's own module is not hidden.)"""
    if _spec_exists("ICParse.entity.findings"):
        from ICParse.entity.findings import Finding
        return Finding
    return FallbackFinding


def issue(code: str, level: str, text: str, where: str = "", *, fix: str | None = None, evidence: str = ""):
    """One problem, as an ICParse ``Finding``: ``code`` is the rule or guard id, ``level`` is ``ERROR`` (the export is refused),
    ``WARN`` or ``INFO``, ``text`` says in plain words what is wrong and what to do, ``where`` names the part / bone / file."""
    if level not in LEVELS:
        raise ValueError(f"an issue level is one of {', '.join(LEVELS)}, not {level!r}")
    return finding_class()(id=code, severity=level, where=where, text=text, fix=fix, evidence=evidence)


class _IssueMeta(type):
    def __call__(cls, *args, **kwargs):          # Issue(code, level, text, where="") builds a Finding, not an "Issue"
        return issue(*args, **kwargs)

    def __instancecheck__(cls, obj) -> bool:
        return isinstance(obj, (FallbackFinding, finding_class()))


class Issue(metaclass=_IssueMeta):
    """``Issue(code, level, text, where="")`` returns an ICParse ``Finding`` (there is no separate record type);
    ``isinstance(x, Issue)`` is true for any ``Finding``. Same as :func:`issue`."""


def blocking(issues) -> list:
    """The ERRORs among ``issues`` (they refuse the export)."""
    return [i for i in issues if i.level == ERROR]


def counts(issues) -> dict:
    """``{ERROR: n, WARN: n, INFO: n}``."""
    out = {ERROR: 0, WARN: 0, INFO: 0}
    for i in issues:
        out[i.level] = out.get(i.level, 0) + 1
    return out


# ============================================================================================================== bundles
def sha1_hex(data) -> str:
    """Hex SHA-1 of bytes (the source stamps the entity keeps: ``source_sha1_sgm`` and friends)."""
    return hashlib.sha1(bytes(data)).hexdigest()


def _check_shared_key(key) -> None:
    if key not in SHARED_KEYS:
        raise KeyError(f"{key!r} is not a declared hand-over key (declared: {', '.join(sorted(SHARED_KEYS))}). A stage that needs "
                       f"a new one calls model.declare_shared(key, owner) when its module is imported")


class SharedBag(dict):
    """``OutBundle.shared``: values one export stage hands to the stages after it. Only declared keys (``SHARED_KEYS``) are accepted."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.update(*args, **kwargs)

    def __setitem__(self, key, value):
        _check_shared_key(key)
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        _check_shared_key(key)
        return super().setdefault(key, default)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other):
        self.update(other)
        return self

    def copy(self):
        return SharedBag(self)


def declare_shared(key: str, owner: str) -> None:
    """Declare one more hand-over key written by the stage ``owner`` (a slice name). Declaring the same key again for the same
    owner is fine (a module reload); a different owner is refused, so two stages never write one key."""
    have = SHARED_OWNERS.get(key)
    if have is not None and have != owner:
        raise ValueError(f"the hand-over key {key!r} already belongs to the {have!r} stage, not {owner!r}")
    SHARED_OWNERS[key] = owner
    SHARED_KEYS.add(key)


@dataclass
class SourceBundle:
    """The files of an entity as they were when it was imported, and the ICParse reading of the model, done once."""
    sgm: bytes
    ebp: bytes | None = None
    lua: str | None = None
    paths: dict = field(default_factory=dict)       # {"sgm": path, "ebp": path, "lua": path, ...} where each came from
    scene: Any = None                               # ICParse Scene (``scene.doc`` / ``scene.graph()`` once I14a has landed)

    @classmethod
    def from_files(cls, sgm_path: str, ebp_path: str | None = None, lua_path: str | None = None) -> "SourceBundle":
        """Read the files (and the model, once). The .lua is text; its bytes survive a round trip (UTF-8 with surrogate escapes)."""
        from ICParse import read_scene
        with open(sgm_path, "rb") as f:
            sgm = f.read()
        ebp = lua = None
        if ebp_path:
            with open(ebp_path, "rb") as f:
                ebp = f.read()
        if lua_path:
            with open(lua_path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
                lua = f.read()
        paths = {k: v for k, v in (("sgm", sgm_path), ("ebp", ebp_path), ("lua", lua_path)) if v}
        return cls(sgm=sgm, ebp=ebp, lua=lua, paths=paths, scene=read_scene(sgm))

    def sha1(self) -> dict:
        """``{"sgm": hex, "ebp": hex | None, "lua": hex | None}``."""
        return {"sgm": sha1_hex(self.sgm),
                "ebp": sha1_hex(self.ebp) if self.ebp is not None else None,
                "lua": sha1_hex(self.lua.encode("utf-8", "surrogateescape")) if self.lua is not None else None}


def _clean_archive_path(path: str) -> str:
    """The lower-case, forward-slash, relative path the game's archives need (the lookup is case-sensitive), or ValueError."""
    p = str(path).replace("\\", "/")
    if not p or p.startswith("/") or ":" in p.split("/")[0] or any(part in ("", ".", "..") for part in p.split("/")):
        raise ValueError(f"{path!r} is not a relative archive path")
    if p != p.lower():
        raise ValueError(f"{path!r} must be lower case: the game looks files up by their exact lower-case path")
    return p


@dataclass
class OutBundle:
    """The files an export will write, filled stage by stage. It starts as a copy of the source (``from_source``)."""
    sgm: bytes
    ebp: bytes | None = None
    lua: str | None = None
    files: dict = field(default_factory=dict)       # lower-case relative path -> bytes (textures: art/textures/<name>.txr)
    notes: list = field(default_factory=list)       # plain-words lines for the export summary
    issues: list = field(default_factory=list)      # Findings raised while exporting
    shared: dict = field(default_factory=SharedBag) # hand-over between stages; only SHARED_KEYS may be written

    def __post_init__(self):
        if type(self.shared) is not SharedBag:
            self.shared = SharedBag(self.shared)

    @classmethod
    def from_source(cls, src: SourceBundle) -> "OutBundle":
        return cls(sgm=src.sgm, ebp=src.ebp, lua=src.lua)

    def add_file(self, path: str, data: bytes) -> str:
        """Add one extra file (a texture) under its lower-case relative path; a path the archive could not find is refused."""
        clean = _clean_archive_path(path)
        self.files[clean] = bytes(data)
        return clean

    def check_files(self) -> list:
        """One ERROR per entry of ``files`` whose path is not a lower-case relative path (the exporter's backstop)."""
        out = []
        for path in self.files:
            try:
                _clean_archive_path(path)
            except ValueError as e:
                out.append(issue("file_path", ERROR, f"{e}. Rename the file.", where=str(path)))
        return out

    def check_shared(self) -> list:
        """One ERROR per ``shared`` key that is not declared (only possible if the dict was filled behind SharedBag's back)."""
        return [issue("shared_key", ERROR, f"the hand-over key {k!r} is not declared", where=str(k))
                for k in self.shared if k not in SHARED_KEYS]


@dataclass
class ExportReport:
    """What ``exporter.export_entity`` returns (slice B01 fills it; the sweeps and the panels read it)."""
    out: OutBundle | None = None                    # the finished bundle; None when the export was refused
    written: dict = field(default_factory=dict)     # relative path -> absolute path actually written
    issues: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    changed: list = field(default_factory=list)     # names of the slices that changed something
    build_dir: str | None = None
    refused: bool = False                           # an ERROR stopped the export: nothing was written

    @property
    def ok(self) -> bool:
        return not self.refused and not blocking(self.issues)


# ============================================================================================================== slices
@runtime_checkable
class Slice(Protocol):
    """One concern of an entity (materials, skeleton, markers, ...). ``read`` / ``changed`` / ``apply`` / ``check`` /
    ``on_rename`` are pure; ``build`` and ``gather`` are the only ones that touch Blender."""
    name: str            # unique, lower case; a stage name keeps that stage's number (STAGE_ORDER)
    order: int           # export order; unique across slices
    claims: tuple        # datablock kinds this slice owns, e.g. ("EMPTY:bz_marker",); no two slices claim one

    def read(self, src: SourceBundle) -> Any: ...                                  # what the files hold
    def build(self, ctx, entity, data) -> None: ...                                # create the Blender datablocks (import / new project)
    def gather(self, ctx, entity) -> Any: ...                                      # Blender's current state as plain data
    def changed(self, data, state) -> bool: ...                                    # semantic compare, with tolerances
    def apply(self, out: OutBundle, src: SourceBundle, data, state) -> None: ...   # edit the bundle (bytes in, bytes out)
    def check(self, src: SourceBundle, data, state) -> list: ...                   # list of Finding
    def on_rename(self, kind: str, old: str, new: str, state) -> None: ...         # follow a rename


SLICE_MEMBERS = ("name", "order", "claims", "read", "build", "gather", "changed", "apply", "check", "on_rename")
_METHOD_ARGS = {"read": 1, "build": 3, "gather": 2, "changed": 2, "apply": 4, "check": 3, "on_rename": 4}


class BaseSlice:
    """Do-nothing defaults for every member of :class:`Slice`: a slice subclasses this and overrides what it needs
    (``changed`` stays False, so an untouched entity costs nothing)."""
    name = ""
    order = 50
    claims: tuple = ()

    def read(self, src):
        return None

    def build(self, ctx, entity, data) -> None:
        return None

    def gather(self, ctx, entity):
        return None

    def changed(self, data, state) -> bool:
        return False

    def apply(self, out, src, data, state) -> None:
        return None

    def check(self, src, data, state) -> list:
        return []

    def on_rename(self, kind, old, new, state) -> None:
        return None


def slice_problems(s) -> list:
    """Plain-words problems with ``s`` as a slice (empty list = it fits the protocol): missing members, wrong shapes, a method
    that cannot be called with the arguments the exporter passes."""
    out = []
    for member in SLICE_MEMBERS:
        if not hasattr(s, member):
            out.append(f"has no {member!r}")
    if out:
        return out
    if not isinstance(s.name, str) or not s.name or s.name != s.name.lower():
        out.append(f"its name {s.name!r} must be a non-empty lower-case string")
    if isinstance(s.order, bool) or not isinstance(s.order, int):
        out.append(f"its order {s.order!r} must be an integer")
    if not isinstance(s.claims, (tuple, list)) or not all(isinstance(c, str) for c in s.claims):
        out.append(f"its claims {s.claims!r} must be a tuple of strings")
    for method, n in _METHOD_ARGS.items():
        fn = getattr(s, method)
        if not callable(fn):
            out.append(f"{method} is not callable")
            continue
        try:
            inspect.signature(fn).bind(*([None] * n))
        except TypeError:
            out.append(f"{method} cannot be called with the {n} argument(s) the exporter passes")
        except ValueError:       # a builtin with no signature: nothing to check
            pass
    return out


_SLICES: dict = {}           # name -> slice
_OWNERS: dict = {}           # name -> the module that registered it
_DUPLICATES: list = []       # (name, first module, second module)


def register_slice(s, owner: str = "") -> list:
    """Add ``s`` to the registry (the same name from the same module replaces: a reload). Returns the problems found with it."""
    problems = slice_problems(s)
    name = getattr(s, "name", None)
    if isinstance(name, str) and name:        # a slice with problems is still listed, so registry_problems() can name them
        have = _OWNERS.get(name)
        if have is not None and have != owner:
            _DUPLICATES.append((name, have, owner))
        _SLICES[name] = s
        _OWNERS[name] = owner
    return problems


def unregister_slice(name: str) -> None:
    _SLICES.pop(name, None)
    _OWNERS.pop(name, None)


def clear_slices() -> None:
    _SLICES.clear()
    _OWNERS.clear()
    del _DUPLICATES[:]


def get_slice(name: str):
    """The registered slice called ``name``, or None."""
    return _SLICES.get(name)


def slices() -> list:
    """The registered slices in export order (ties by name)."""
    def key(s):
        order = getattr(s, "order", 0)
        return (order if isinstance(order, int) else 0, str(getattr(s, "name", "")))
    return sorted(_SLICES.values(), key=key)


def known_guard_codes() -> set:
    return set(GUARD_CODES) | set(_EXTRA_GUARD_CODES)


def declare_guard_code(code: str, owner: str = "") -> None:
    """The safety guard (slice B02) declares a guard code beyond the baseline of PLAN_FINAL C22."""
    if code not in GUARD_CODES:
        _EXTRA_GUARD_CODES[code] = owner


def registry_problems() -> list:
    """Everything wrong with the registered slices and claims, in plain words (empty = the contract holds): protocol members and
    call shapes, a shared export order, a stage name with the wrong number, a datablock kind claimed twice, a name registered by
    two modules, a guard code claimed that nobody declared, a guard code claimed by two modules."""
    out = []
    reg = slices()
    for s in reg:
        for p in slice_problems(s):
            out.append(f"slice {getattr(s, 'name', s)!r}: {p}")
    by_order: dict = {}
    for s in reg:
        by_order.setdefault(getattr(s, "order", None), []).append(getattr(s, "name", "?"))
    for order, names in sorted(by_order.items(), key=lambda kv: str(kv[0])):
        if len(names) > 1:
            out.append(f"slices {', '.join(sorted(names))} share the export order {order}: every slice needs its own number")
    for s in reg:
        name, order = getattr(s, "name", None), getattr(s, "order", None)
        if name in STAGE_ORDER and order != STAGE_ORDER[name]:
            out.append(f"slice {name!r} has order {order} but the {name!r} stage runs at {STAGE_ORDER[name]}")
    by_claim: dict = {}
    for s in reg:
        for c in getattr(s, "claims", ()) or ():
            by_claim.setdefault(c, []).append(getattr(s, "name", "?"))
    for claim, names in sorted(by_claim.items()):
        if len(names) > 1:
            out.append(f"slices {', '.join(sorted(names))} both claim {claim!r}: one slice owns a kind of datablock")
    for name, first, second in _DUPLICATES:
        out.append(f"the slice name {name!r} is registered by both {first} and {second}")
    for code, owner in sorted(_CLAIMS.items()):
        if code not in known_guard_codes():
            out.append(f"claim_edit({code!r}) by {owner or '?'}: no such guard code")
    for code, owner in sorted(_CLAIM_CONFLICTS):
        out.append(f"the guard code {code!r} is claimed by two modules ({owner})")
    return out


# ============================================================================================================== refs bus
class RefsBus:
    """``refs.rename(kind, old, new)``: something other data refers to by name was renamed; every slice follows it.

    What refers to what (PLAN_FINAL / addon-audit 5.4): a *bone* is named by vertex groups, clip curves, markers, the part's rigid
    bone, controller events and the skin palette; a *clip* by the controller's motion tree and events; a *marker* by controller events;
    a *material* by submesh shader names; a *part* by the visibility / morph channels of the clips."""

    def __init__(self):
        self.log: list = []      # (kind, old, new, [slice names told]) of every rename, newest last

    def rename(self, kind: str, old: str, new: str, states: dict | None = None) -> list:
        """Call ``on_rename(kind, old, new, state)`` on every registered slice, in export order. ``states`` maps a slice name to that
        slice's gathered state (the exporter passes it; an operator passes nothing and each slice then follows the rename in the
        live Blender data). A slice that cannot follow does not stop the others: the result lists one ERROR per such slice
        (an empty list means every slice followed)."""
        if kind not in REF_KINDS:
            raise ValueError(f"a rename is of one of {', '.join(REF_KINDS)}, not {kind!r}")
        if not old or not new:
            raise ValueError("a rename needs the old and the new name")
        problems, told = [], []
        for s in slices():
            state = (states or {}).get(getattr(s, "name", None))
            try:
                s.on_rename(kind, old, new, state)
            except Exception as e:           # reported, never dropped: the caller shows these to the user
                problems.append(issue("rename_followup", ERROR,
                                      f"the {kind} {old!r} was renamed to {new!r}, but {s.name} could not follow: {e}. "
                                      f"Undo the rename.", where=s.name))
            else:
                told.append(s.name)
        self.log.append((kind, old, new, told))
        return problems


refs = RefsBus()


# ============================================================================================================== edit claims
_CLAIMS: dict = {}            # guard code -> the module that claimed it
_CLAIM_CONFLICTS: list = []   # (code, "A and B")


def claim_edit(code: str, owner: str | None = None) -> None:
    """A slice that now exports a kind of edit calls this when its module is imported; the safety guard then stops refusing ``code``.
    ``owner`` defaults to the calling module's name. A code nobody declared is reported by ``registry_problems``, not here, so
    the order modules are imported in never matters."""
    if owner is None:
        frame = inspect.currentframe()
        owner = frame.f_back.f_globals.get("__name__", "") if frame is not None and frame.f_back is not None else ""
    have = _CLAIMS.get(code)
    if have is not None and have != owner:
        _CLAIM_CONFLICTS.append((code, f"{have} and {owner}"))
    _CLAIMS[code] = owner


def forget_claims(owner: str) -> None:
    """Drop every claim made by the module ``owner`` (a test that imported a throw-away module undoes its claims)."""
    for code in [c for c, o in _CLAIMS.items() if o == owner]:
        del _CLAIMS[code]
    _CLAIM_CONFLICTS[:] = [(c, who) for c, who in _CLAIM_CONFLICTS if owner not in who.split(" and ")]


def is_claimed(code: str) -> bool:
    return code in _CLAIMS


def claimed_by(code: str) -> str | None:
    return _CLAIMS.get(code)


def claimed_codes() -> dict:
    return dict(_CLAIMS)


# ============================================================================================================== small helpers
def foreach_get(coll, attr: str, dtype, per: int = 1):
    """``coll.foreach_get(attr, ...)`` into a fresh numpy array of the explicit ``dtype`` (Blender's bulk calls need it exact):
    shape ``(len, per)``, or ``(len,)`` when ``per`` is 1."""
    import numpy as np
    a = np.empty(len(coll) * per, dtype=dtype)
    coll.foreach_get(attr, a)
    return a.reshape(-1, per) if per > 1 else a


def foreach_set(coll, attr: str, values, dtype) -> None:
    """``coll.foreach_set(attr, values)`` from any array-like, as one contiguous flat array of the explicit ``dtype``."""
    import numpy as np
    coll.foreach_set(attr, np.ascontiguousarray(values, dtype=dtype).reshape(-1))


def pick_game_dir(preference: str = "", *, environ=None, default_root=None) -> tuple:
    """Which game folder to use, and why: ``(folder or None, source)`` with source ``preferences`` (the add-on's own setting),
    ``IC_INSTALL`` (the environment variable), ``steam default`` (ICParse's usual Steam places) or ``none``."""
    env = os.environ if environ is None else environ
    pref = (preference or "").strip()
    if pref:
        folder = os.path.expanduser(pref)
        if os.path.isdir(folder):
            return folder, "preferences"
    inst = (env.get("IC_INSTALL") or "").strip()
    if inst and os.path.isdir(inst):
        return inst, "IC_INSTALL"
    if default_root is None:
        from ICParse.data import default_root as _default_root
        default_root = _default_root
    root = default_root()
    if root and os.path.isdir(root):
        return root, "steam default"
    return None, "none"
