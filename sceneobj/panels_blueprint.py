"""Blueprint (.ebp) panel for gatherers, characters, structures and props (proof of concept): load, edit, export folder.

Values live on one object of the entity (rig, else group empty, else first part). Export re-reads the source file,
writes only changed chunks (untouched = byte-identical), refuses if it changed on disk (SHA-1) and never writes into
the game folder. Missing ICParse parts are reported and the blueprint is copied unchanged.
"""
from __future__ import annotations

import glob
import hashlib
import importlib
import importlib.util
import json
import math
import os
import re
import struct
from contextlib import contextmanager
from dataclasses import dataclass

import bpy
from . import ui_kit
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

ORDER = 80                        # a sceneobj module's place in the slice order ("controller_meta"); ignored while this file sits in the add-on's folder

# ---------------------------------------------------------------------------------------------------------------- limits and words
NAME_LIMIT = 255                  # letters: the engine reads the name into a 256-letter buffer (decompiled: ebp-meta 5.2, BlueprintHeader::Load)
SOUND_LIMIT = 259                 # letters: a sound path of 260 or more is a fatal error when the game loads it (decompiled: ebp-meta 7, HandlePIDS)
FOOTPRINT_MAX = 255               # path cells (the brief's range; stock footprints are 0..43)
CLASS_MAX = 65535
STATS_ROWS_MAX = 500

OFFER_NEW_BLUEPRINT = False   # hidden (round 2 tidy): "Start a New Blueprint" only makes a blueprint with no motion tree and no events, which plays no animation
STATES = ("NONE", "FILE", "NEW", "LEGACY", "EMBEDDED", "PROBLEM", "NO_LIBRARY")
EDITABLE = ("FILE", "NEW")        # the states in which the panel's values can be changed and written
FIELDS = ("name", "class", "footprint", "offset", "sounds")
FIELD_WORDS = {"name": "name", "class": "kind (class)", "footprint": "footprint", "offset": "offset", "sounds": "sounds", "icon": "icon"}

HINT = {
    "name": "Name shown in the game (or a language code like $41545), up to 255 letters.",
    "class": "What the game makes of it; changing this changes multiplayer.",
    "footprint": "Blocked path cells; 0 and 0 lets the game measure the model. Changing this changes multiplayer.",
    "footprint_unused": "Only buildings use a footprint; it is kept here but is not used.",
    "offset": "Moves the model over its footprint (Y raises it); changes multiplayer.",
    "sounds": "Sounds loaded before play starts; missing files are skipped.",
    "folder": "The game looks in Data/art/ebps/<folder>/; keep the original thing's folder.",
}
FOLDER_SOURCE_WORDS = {              # how the folder was found, as a sentence that follows HINT["folder"]
    "STAMP": "This is the folder of the thing the project was started from.",
    "IDENTITY": "This project is a copy of one of the game's own things, and this is its folder.",
    "PATH": "This is where the files lie.",
    "GUESS": "This is only a guess from the kind of thing: please check it.",
}
FAMILY_WORDS = {
    "unit": "a unit that walks, fights or gathers",
    "structure": "a building",
    "scenery": "scenery: no behaviour, it only plays animations",
    "resource": "a resource spot",
    "projectile": "a shot, a spray or a cloud",
    "creature": "an animal of the combiner (a different kind of file)",
    "unknown": "not known to the game",
}
SUBTYPE_WORDS = {
    "GATHERER": "Gatherer (a worker unit)", "CHARACTER": "Character (a unit)", "STRUCTURE": "Structure (a building)",
    "PROP": "Prop (scenery or a resource spot)", "PROJECTILE": "Projectile or effect", "LOCATION": "Map marker",
    "NIS": "Cut-scene object", "UI": "Menu object", "OTHER": "Something else",
}
SUBTYPE_ICONS = {"GATHERER": "ARMATURE_DATA", "CHARACTER": "ARMATURE_DATA", "STRUCTURE": "HOME", "PROP": "OBJECT_DATA"}
_FOLDER_GUESS = {"GATHERER": "gatherers", "CHARACTER": "characters", "STRUCTURE": "structures", "PROP": "nature", "PROJECTILE": "projectiles"}
_KIND_OF_PROJECT = ("CHARACTER", "STRUCTURE", "PROJECTILE")     # the scene kinds File > New > Impossible Creatures Project starts (bz_project)


class Refused(Exception):
    """A plain-words reason why something was not done. Raised before anything is written, so the message is all there is to say."""


# ---------------------------------------------------------------------------------------------------------------- reaching the add-on and ICParse
_PKG = __package__ or "ic_blender"
if _PKG.endswith(".sceneobj"):
    _PKG = _PKG[:-len(".sceneobj")]


def _addon(name: str):
    """A module of the add-on by name ('core', 'scene', 'store', ...). This file may sit in the add-on's own folder now and move into sceneobj/ later;
    looking siblings up through the add-on's package name makes the move need no edit."""
    return importlib.import_module(f"{_PKG}.{name}")


def _ebp_lib(name: str):
    """``ICParse.ebp.<name>`` when it exists, else None (a later slice has not landed yet). A real error INSIDE an existing module is not hidden."""
    full = f"ICParse.ebp.{name}"
    try:
        spec = importlib.util.find_spec(full)
    except (ImportError, ValueError):
        spec = None
    return importlib.import_module(full) if spec is not None else None


def scene_lib():
    """``ICParse.ebp.scene`` (``SceneBlueprint``, ``new_scene_ebp``) or None while it is not installed."""
    return _ebp_lib("scene")


def stats_lib():
    """``ICParse.ebp.stats`` (``read_stats``, ``stats_candidates``) or None while it is not installed."""
    return _ebp_lib("stats")


def controllers():
    return importlib.import_module("ICParse.ebp.controllers")


def _prefs():
    """The add-on's preferences module (``sceneobj.prefs``), or None when it could not be loaded (the defaults then apply)."""
    try:
        return _addon("sceneobj.prefs")
    except Exception:                                                     # noqa: BLE001 -- the panel works without it
        return None


def game_dir() -> tuple:
    """``(folder or None, source)``: the game folder the add-on reads the game's own files from (the Game folder preference, else
    ``$IC_INSTALL``, else the usual Steam folders)."""
    p = _prefs()
    if p is not None:
        try:
            return p.resolve_game_dir()
        except Exception:                                                 # noqa: BLE001
            pass
    try:
        from ICParse.data import default_root
        root = default_root()
    except Exception:                                                     # noqa: BLE001
        root = None
    return (root, "steam default") if root and os.path.isdir(root) else (None, "none")


def game_dir_words() -> str:
    """One plain line saying which game folder is in use and why (or that there is none)."""
    p = _prefs()
    folder = game_dir()[0]
    try:
        line = p.game_dir_line() if p is not None else (f"Game folder: {folder}" if folder else "No game folder found.")
    except Exception:                                                     # noqa: BLE001
        line = f"Game folder: {folder}" if folder else "No game folder found."
    if folder and not glob.glob(os.path.join(glob.escape(folder), "*.sga")):
        line += " It holds none of the game's archives (.sga files): is it the right folder?"
    return line


def target_mod() -> str:
    """The game build the work is made for (the Game build preference): 'icmod' (the original game), 'tellurian' or 'insectmod'."""
    p = _prefs()
    try:
        mod = str(p.get().target_mod) if p is not None else "icmod"
    except Exception:                                                     # noqa: BLE001
        mod = "icmod"
    return mod or "icmod"


# ---------------------------------------------------------------------------------------------------------------- plain text helpers
def wrap(text: str, width: int = 46) -> list:
    """``text`` cut into lines of about ``width`` letters at word breaks (a word longer than a line stays whole)."""
    lines, line = [], ""
    for w in str(text).split():
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


def words(fields) -> str:
    """'name, footprint and sounds' for a list of field keys."""
    names = [FIELD_WORDS.get(f, f) for f in fields]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


_NAME_BAD = re.compile(r"[^a-z0-9_ '\-]+")


def clean_name(text, fallback: str = "entity") -> str:
    """A file name the game can use: lower case, letters digits and _ (stock names also hold spaces, ' and -; those stay), nothing else."""
    s = _NAME_BAD.sub("_", str(text or "").strip().lower()).strip(" _")
    return s or fallback


def clean_folder(text) -> str:
    """A folder under art/ebps: lower case, '/' between levels ('nature/swamp'), the same letters as :func:`clean_name`; '' is art/ebps itself."""
    parts = []
    for p in re.split(r"[\\/]+", str(text or "")):
        p = p.strip()
        if p and p not in (".", ".."):
            parts.append(clean_name(p, "folder"))
    return "/".join(parts)


# ---------------------------------------------------------------------------------------------------------------- the values (pure)
def _f32(x) -> bytes:
    return struct.pack("<f", float(x))


@dataclass(frozen=True)
class Values:
    """What the panel edits, in plain Python (no Blender): the five things of a blueprint the proof of concept can change."""
    name: str = ""
    class_id: int = 0
    footprint: tuple = (0, 0)
    offset: tuple = (0.0, 0.0, 0.0)
    sounds: tuple = ()

    def to_json(self) -> str:
        return json.dumps({"name": self.name, "class": self.class_id, "footprint": list(self.footprint), "offset": list(self.offset),
                           "sounds": list(self.sounds)})

    @classmethod
    def from_json(cls, text: str) -> "Values":
        try:
            d = json.loads(text)
            return cls(str(d["name"]), int(d["class"]), tuple(int(v) for v in d["footprint"]), tuple(float(v) for v in d["offset"]),
                       tuple(str(s) for s in d["sounds"]))
        except (ValueError, KeyError, TypeError):
            return cls()


def values_of(sb) -> Values:
    """The values of a ``SceneBlueprint``. A file with no build chunk means footprint (0, 0) and offset 0: the game starts those at zero (decompiled:
    ``ResetBuildInfo``), and (0, 0) makes it measure the model."""
    b = sb.build
    return Values(
        name=str(sb.header.name or ""), class_id=int(sb.header.defstats_class),
        footprint=(int(b.footprint_x), int(b.footprint_z)) if b is not None else (0, 0),
        offset=tuple(float(v) for v in b.offset) if b is not None else (0.0, 0.0, 0.0),
        sounds=tuple(str(s) for s in sb.sounds))


def values_from_pg(bp) -> Values:
    return Values(name=str(bp.display_name), class_id=int(bp.class_id), footprint=(int(bp.footprint_x), int(bp.footprint_z)),
                  offset=tuple(float(v) for v in bp.offset), sounds=tuple(str(s.path) for s in bp.sounds))


def changed_fields(a: Values, b: Values) -> list:
    """The fields (keys of FIELDS, in that order) in which ``b`` differs from ``a``. Offsets are compared as the 32-bit floats the file holds."""
    out = []
    if a.name != b.name:
        out.append("name")
    if int(a.class_id) != int(b.class_id):
        out.append("class")
    if tuple(int(v) for v in a.footprint) != tuple(int(v) for v in b.footprint):
        out.append("footprint")
    if any(_f32(x) != _f32(y) for x, y in zip(a.offset, b.offset)):
        out.append("offset")
    if list(a.sounds) != list(b.sounds):
        out.append("sounds")
    return out


def problems_of(v: Values, fields=None) -> list:
    """Plain sentences for everything in ``v`` the game would not accept (empty when the values are fine to write). ``fields`` (keys of FIELDS) limits the
    look to those: an edit is only held up by a problem in what is being edited, never by a value the file already had."""
    out = []
    want = set(FIELDS if fields is None else fields)
    if "name" in want and len(v.name) > NAME_LIMIT:
        out.append(f"The name is {len(v.name)} letters long; the game reads at most {NAME_LIMIT}.")
    if "class" in want and not 0 <= int(v.class_id) <= CLASS_MAX:
        out.append(f"The class number must be between 0 and {CLASS_MAX}.")
    if "footprint" in want and not all(0 <= int(x) <= FOOTPRINT_MAX for x in v.footprint):
        out.append(f"The footprint must be between 0 and {FOOTPRINT_MAX} cells in each direction.")
    if "offset" in want and not all(math.isfinite(float(x)) for x in v.offset):
        out.append("The offset must be plain numbers.")
    if "sounds" in want:
        for i, s in enumerate(v.sounds, 1):
            if not str(s).strip():
                out.append(f"Sound {i} has no file name: fill it in or remove it.")
            elif len(s) > SOUND_LIMIT:
                out.append(f"Sound {i}: the path is {len(s)} letters long; the game reads at most {SOUND_LIMIT}.")
            else:
                try:
                    s.encode("latin-1")
                except UnicodeEncodeError:
                    out.append(f"Sound {i}: the path holds letters the game cannot read; use plain letters and digits.")
    return out


# ---------------------------------------------------------------------------------------------------------------- what a class means
@dataclass(frozen=True)
class ClassInfo:
    id: int
    name: str
    family: str
    registered: bool        # the game build of the preferences (Game build; the original game unless changed) makes a controller of this number
    buildable: bool         # the controller has a building site: the only kind that reads the footprint (decompiled: ebp-meta 6.2)
    expansion_only: bool
    label: str              # one plain line: "5 - Structure: a building"
    note: str


def class_info(class_id: int) -> ClassInfo:
    """What the class table (ICParse.ebp.controllers) says about a class number, in plain words."""
    c = controllers()
    cid = int(class_id)
    ct = c.CONTROLLER_TYPES.get(cid)
    if ct is None:
        return ClassInfo(cid, "Unknown", "unknown", False, False, False,
                         f"{cid} - not a class the game knows: it would make a plain prop that only plays animations", "")
    try:
        registered = bool(c.is_registered(cid, target_mod()))
    except ValueError:                                                    # a build name the class table does not know: the original game
        registered = bool(c.is_registered(cid, "icmod"))
    exts = tuple(ct.extensions) + tuple(getattr(ct, "expansion_extensions", ()) or ())
    buildable = "Site" in exts                          # only a controller with a building site reads the footprint (decompiled: ebp-meta 6.2)
    if cid == 0:
        name = "Plain prop"
    elif not ct.registry_name:
        name = "Unused number"
    else:
        name = ct.registry_name
    label = f"{cid} - {name}: {FAMILY_WORDS.get(ct.family, ct.family)}"
    if ct.expansion_only:
        label += " (only the expansion knows it)"
    return ClassInfo(cid, name, ct.family, registered, bool(buildable), bool(ct.expansion_only), label, ct.note or "")


def subtype_of(bp) -> str:
    """GATHERER / CHARACTER / STRUCTURE / PROP / ... for the values now in the panel (the class decides; the folder and the rig only when it cannot)."""
    try:
        return controllers().subtype_key(int(bp.class_id), bp.folder or None, has_skeleton=bool(bp.has_skeleton), skinned=bool(bp.skinned))
    except Exception:                                                     # noqa: BLE001 -- the label is decoration; it never stops the panel
        return "OTHER"


_CLASS_ITEMS: list = []           # enum items must stay referenced while Blender shows them


def _class_items(self, context):
    items = []
    try:
        c = controllers()
        for cid in sorted(c.CONTROLLER_TYPES):
            info = class_info(cid)
            if cid != 0 and not info.registered and not info.expansion_only:
                continue                                                  # the dead numbers (10, 20) are not offered
            items.append((str(cid), info.label, info.note or "", "NONE", cid))
        cur = int(self.class_id)
        if cur not in {it[4] for it in items}:                            # the number now in the file is always one of the choices
            items.append((str(cur), class_info(cur).label, "This number is not in the game's table", "ERROR", cur))
    except Exception:                                                     # noqa: BLE001
        items = [("0", "0 - Plain prop", "", "NONE", 0)]
    _CLASS_ITEMS[:] = items
    return _CLASS_ITEMS


def _class_get(self):
    return int(self.class_id)


def _class_set(self, value):
    self.class_id = int(value)


# ---------------------------------------------------------------------------------------------------------------- the property groups
_QUIET = [0]


@contextmanager
def quiet():
    """Changes made inside do not trigger the properties' update callbacks (a load fills many values and refreshes the stats once)."""
    _QUIET[0] += 1
    try:
        yield
    finally:
        _QUIET[0] -= 1


def _refresh_after_edit(self, context):
    """The gameplay numbers depend on the class and on the folder (a stats file of the thing's own name); list them again when either is edited."""
    if _QUIET[0]:
        return
    try:
        ent = entity_of(self.id_data)
        if ent is not None and self.loaded:
            refresh_stats(self, ent)
    except Exception as e:                                                # noqa: BLE001 -- an update callback must never break the property edit
        self.stats_note = f"The gameplay numbers could not be listed: {e}"


class BZ_PG_blueprint_sound(bpy.types.PropertyGroup):
    """One sound the blueprint asks the game to load."""
    path: StringProperty(name="Sound file", default="",
                         description="A game sound path such as DATA:AUDIO\\SFX\\NAME.PAT (up to 259 letters)")


class BZ_PG_blueprint_stat(bpy.types.PropertyGroup):
    """One gameplay number the game would give the thing, listed from the game's stats files, and the author's change to it (r2stats)."""
    key: StringProperty()
    value: StringProperty()
    origin: StringProperty()
    # r2stats (ops_entity_stats.py): the number can be edited
    orig: FloatProperty(description="The number as the game's files give it")
    new_value: FloatProperty(name="Value", precision=3, description="The number this thing will have")
    dflt: FloatProperty(description="What this kind of thing has by default")
    has_dflt: BoolProperty(default=False)
    added: BoolProperty(default=False, description="A number the game's files do not give this thing: it is added")


class BZ_PG_blueprint(bpy.types.PropertyGroup):
    """The edited blueprint values of one entity, and what is needed to write them back (the file they came from and its SHA-1)."""
    loaded: BoolProperty(default=False, description="The blueprint has been read (or a new one started)")
    state: StringProperty(default="NONE", description="NONE, FILE, NEW, LEGACY, EMBEDDED, PROBLEM or NO_LIBRARY")
    note: StringProperty(default="", description="Why the blueprint is read-only or could not be read")
    source_path: StringProperty(default="", subtype="FILE_PATH", description="The .ebp the values were read from ('' for a new blueprint)")
    source_sha1: StringProperty(default="", description="SHA-1 of that file when it was read")
    baseline: StringProperty(default="", description="The values as they were loaded (to show what was edited)")
    folder: StringProperty(default="", name="Folder in the game", description=HINT["folder"], update=_refresh_after_edit)
    folder_source: StringProperty(default="GUESS")
    has_skeleton: BoolProperty(default=False)
    skinned: BoolProperty(default=False)

    display_name: StringProperty(default="", name="Name", description=HINT["name"])
    class_id: IntProperty(default=0, min=0, max=CLASS_MAX, name="Class number", description=HINT["class"], update=_refresh_after_edit)
    class_choice: EnumProperty(items=_class_items, get=_class_get, set=_class_set, name="Kind", description=HINT["class"])
    footprint_x: IntProperty(default=0, min=0, max=FOOTPRINT_MAX, soft_max=64, name="Footprint X", description=HINT["footprint"])
    footprint_z: IntProperty(default=0, min=0, max=FOOTPRINT_MAX, soft_max=64, name="Footprint Z", description=HINT["footprint"])
    offset: FloatVectorProperty(size=3, subtype="XYZ", precision=3, name="Offset", description=HINT["offset"])
    sounds: CollectionProperty(type=BZ_PG_blueprint_sound)
    stats: CollectionProperty(type=BZ_PG_blueprint_stat)
    stats_source: StringProperty(default="")
    stats_note: StringProperty(default="")
    stats_game: StringProperty(default="", description="Which game folder the gameplay numbers were read from, in one plain line")
    # r2stats (ops_entity_stats.py)
    stats_own: BoolProperty(default=False, description="The numbers come from a stats file of this thing's own name (not from the default of its kind)")
    stats_filter: StringProperty(default="", name="Search", options={"TEXTEDIT_UPDATE"}, description="Part of a number's name")
    stats_base: BoolProperty(default=False, name="Use the original's numbers", update=_refresh_after_edit,
                             description="List and change the numbers of the thing this project was started from. A new name then starts from them too (its stats "
                                         "file is written even when you change nothing); unticked, a new name starts from the default numbers of its kind. "
                                         "Replace the original always starts from the original's numbers")
    icon_image: StringProperty(default="", description="The Blender image holding the new 64 x 64 icon made with Icon from this view ('' = keep the blueprint's own)")


# ---------------------------------------------------------------------------------------------------------------- finding the entity
@dataclass
class Entity:
    parts: list               # the part objects of one import, by part number
    holder: object            # the object that carries bz_blueprint
    source: str               # absolute path of the source .sgm (an .ebp when the model is embedded in it)
    rig: object               # the rig object or None


def _is_part(o) -> bool:
    return o.type == "MESH" and bool(o.get("bz_scene")) and o.get("bz_scene_part") is not None


def _abs(path) -> str:
    return os.path.abspath(bpy.path.abspath(str(path))) if path else ""


def entity_of(obj):
    """The :class:`Entity` a part object, its rig or its group empty belongs to; None for anything else. (Parts are matched by the add-on's own
    stamps: the same ``bz_source`` and ``bz_rig`` as in ``scene.part_objects``, but a double import is tolerated here, not raised on.)"""
    if obj is None:
        return None
    seed = None
    if _is_part(obj):
        seed = obj
    elif obj.type == "ARMATURE":
        seed = next((o for o in bpy.data.objects if _is_part(o) and o.get("bz_rig") == obj.name), None)
    elif obj.type == "EMPTY":
        seed = next((c for c in obj.children if _is_part(c)), None)
    if seed is None or not seed.get("bz_source"):
        return None
    src, rig_name = seed.get("bz_source"), seed.get("bz_rig")
    parts = sorted((o for o in bpy.data.objects if _is_part(o) and o.get("bz_source") == src and o.get("bz_rig") == rig_name),
                   key=lambda o: (int(o["bz_scene_part"]), o.name))
    rig = None
    try:
        rig = _addon("store").rig(parts[0])
    except Exception:                                                     # noqa: BLE001
        rig = None
    if rig is not None:
        holder = rig
    else:
        parent = parts[0].parent
        holder = parent if parent is not None and parent.type == "EMPTY" and all(p.parent is parent for p in parts) else parts[0]
    return Entity(parts, holder, _abs(src), rig)


def blueprint_path(ent) -> str | None:
    """The .ebp of the entity: the one the model was imported from (``bz_ebp``), the source itself when the model is embedded in an .ebp, else the
    file of the same name beside the .sgm. None when there is none."""
    ebp = ent.parts[0].get("bz_ebp")
    if ebp and os.path.isfile(_abs(ebp)):
        return _abs(ebp)
    if ent.source.lower().endswith(".ebp"):
        return ent.source if os.path.isfile(ent.source) else None
    stem = os.path.splitext(ent.source)[0]
    for ext in (".ebp", ".EBP", ".Ebp"):
        if os.path.isfile(stem + ext):
            return stem + ext
    return None


def default_name(ent) -> str:
    """The name the exported files get unless the user chooses another: the project's file name."""
    return clean_name(os.path.splitext(os.path.basename(ent.source))[0])


def _stamp(ent, key: str) -> str:
    """The first non-empty value of the custom property ``key`` on any part of the entity ('' when none has it)."""
    return next((str(v) for v in (p.get(key) for p in ent.parts) if v), "")


def _kind_of(ent) -> str:
    kind = _stamp(ent, "bz_project")
    return kind if kind in _KIND_OF_PROJECT else ""


# ---------------------------------------------------------------------------------------------------------------- the game folder of an entity
def _rel_of_member(member: str):
    """'art/ebps/structures/lab.sgm' -> 'structures' ('' for the root of art/ebps); None when it is not under art/ebps."""
    m = member.replace("\\", "/").lower()
    i = m.rfind("art/ebps/")
    if i < 0:
        return None
    return m[i + len("art/ebps/"):].rpartition("/")[0]


def _folder_from_path(path: str):
    return _rel_of_member(os.path.abspath(path).replace("\\", "/").lstrip("/")) if path else None


_DONOR_CACHE: dict = {}


def find_donor(src: str, kinds=_KIND_OF_PROJECT):
    """The game archive member (``art/ebps/structures/lab.sgm``) whose bytes are exactly the file ``src``, or None. A project started from a stock thing
    holds a byte copy of it until it is overwritten by an export, so the copy tells which stock thing it is. Nothing is written or changed."""
    try:
        st = os.stat(src)
    except OSError:
        return None
    key = (src, st.st_size, st.st_mtime_ns, tuple(kinds))
    if key in _DONOR_CACHE:
        return _DONOR_CACHE[key]
    found = None
    try:
        np = _addon("newproject")
        digest = None
        for kind in kinds:
            for archive, member in np.scene_donors(kind):
                arc = np._archive(archive)
                entry = arc.entry(member)
                if entry is None or entry.uncomp_len != st.st_size:
                    continue
                if digest is None:
                    with open(src, "rb") as f:
                        digest = hashlib.sha1(f.read()).digest()
                if hashlib.sha1(arc.read_path(member)).digest() == digest:
                    found = member
                    break
            if found:
                break
    except Exception:                                                     # noqa: BLE001 -- no game install, no donor list: the folder is guessed
        found = None
    if len(_DONOR_CACHE) > 64:
        _DONOR_CACHE.clear()
    _DONOR_CACHE[key] = found
    return found


def determine_folder(ent, ebp_path, class_id: int, has_skeleton: bool, skinned: bool):
    """(folder under art/ebps, how it was found: STAMP / IDENTITY / PATH / GUESS). In this order: the donor New Project stamped on the objects
    (``bz_donor``); where the files lie when that is a Data/art/ebps tree; the stock thing the .sgm is a byte copy of; a guess from the kind."""
    stamp = _stamp(ent, "bz_donor")
    if stamp:
        rel = _rel_of_member(stamp)
        if rel is not None:
            return clean_folder(rel), "STAMP"
    for p in (ebp_path, ent.source):
        rel = _folder_from_path(p) if p else None
        if rel is not None:
            return clean_folder(rel), "PATH"
    kind = _kind_of(ent)
    member = find_donor(ent.source, (kind,) if kind else _KIND_OF_PROJECT) if ent.source.lower().endswith(".sgm") else None
    if member:
        rel = _rel_of_member(member)
        if rel is not None:
            return clean_folder(rel), "IDENTITY"
    try:
        key = controllers().subtype_key(int(class_id), None, has_skeleton=has_skeleton, skinned=skinned)
    except Exception:                                                     # noqa: BLE001
        key = "OTHER"
    if key not in _FOLDER_GUESS:
        key = {"CHARACTER": "CHARACTER", "STRUCTURE": "STRUCTURE"}.get(kind, "STRUCTURE")
    return _FOLDER_GUESS[key], "GUESS"


# ---------------------------------------------------------------------------------------------------------------- loading
def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# the counts: every art/ebps blueprint of the five game archives read with ICParse on 2026-11-18 (measured; STATUS.md): 479 blueprints,
# 34 in the old layout (10 map markers, cut-scene props and a menu object, and the 24 below), 86 with the model inside (62 creatures, 24 older things)
LEGACY_NOTE = ("This blueprint uses the game's oldest file layout: you can view it, not change it.")
EMBEDDED_NOTE = ("This blueprint holds its model inside itself, not in a separate .sgm file: view only.")


def classify(sb):
    """(state, plain reason) of a blueprint that was read: FILE when it can be edited; LEGACY (the old file layout) or EMBEDDED (the model sits inside
    the blueprint, so there is no .sgm to write beside it) when it can only be looked at. A file that is both says both."""
    legacy = str(getattr(sb.header, "layout", "stream")) == "legacy"
    kind = str(getattr(getattr(sb, "model", None), "kind", "proxy"))
    if kind in ("embedded_sigm", "embedded_bobj"):
        return "EMBEDDED", EMBEDDED_NOTE + (" It also uses the game's oldest file layout." if legacy else "")
    if legacy:
        return "LEGACY", LEGACY_NOTE
    return "FILE", ""


def fill_pg(bp, values: Values) -> list:
    """Put ``values`` into the property group. Returns the fields the panel could not hold exactly (Blender clamps a number outside a property's range
    and cuts a text at a limit without a word; measured on 5.3.0), which is empty when everything fits. Raises ValueError / TypeError / OverflowError /
    UnicodeError for a value Blender refuses outright."""
    with quiet():
        bp.display_name = values.name
        bp.class_id = values.class_id
        bp.footprint_x, bp.footprint_z = values.footprint
        bp.offset = values.offset
        bp.sounds.clear()
        for s in values.sounds:
            bp.sounds.add().path = s
    return changed_fields(values, values_from_pg(bp))


def default_values(ent) -> Values:
    """A new blueprint: the entity's name, class 0 (a prop that only plays animations), a 1 x 1 footprint, no offset, no sounds."""
    return Values(name=str(ent.parts[0].get("bz_name") or default_name(ent)), class_id=0, footprint=(1, 1), offset=(0.0, 0.0, 0.0), sounds=())


def refresh_stats(bp, ent, keep: bool = True) -> None:
    """List the gameplay numbers again. The changes the author made to them are kept (``keep``: the kind or the folder was edited, which only
    changes what the numbers start from) unless the blueprint is read again from its file, which throws them away (r2stats: ``ops_entity_stats``)."""
    from . import ops_entity_stats as ES
    snap = ES.snapshot(bp) if keep else {}
    try:
        _list_stats(bp, ent)
    finally:
        ES.restore(bp, snap)


def _list_stats(bp, ent) -> None:
    """List the gameplay numbers the game would give this thing. They come from the thing's own stats file when the game has one under
    its name, else from the default file of its class (decompiled: ebp-meta 9.1; ``ICParse.ebp.stats`` does the lookup and the reading)."""
    bp.stats.clear()
    bp.stats_note = ""
    bp.stats_source = ""
    bp.stats_game = ""
    lib = stats_lib()
    if lib is None:
        bp.stats_note = "The gameplay numbers cannot be listed yet: the add-on's reading library is too old to read stats files."
        return
    folder = game_dir()[0]                     # the Game folder preference, else $IC_INSTALL, else the usual Steam folders
    bp.stats_game = game_dir_words()
    target = f"data:art/ebps/{bp.folder + '/' if bp.folder else ''}{default_name(ent)}.ebp"
    if bp.stats_base:                                                         # r2stats: the numbers of the thing the project was started from
        rt = replace_target(ent)
        if rt is not None:
            target = f"data:art/ebps/{rt.folder + '/' if rt.folder else ''}{rt.name}.ebp"
    resolver = _addon("core").scene_texture_source(os.path.dirname(ent.source), data_root=folder)
    ref = lib.resolve_stats_ref(target, int(bp.class_id), resolver)
    if ref is None:
        bp.stats_note = ("The game has no stats file for this class, so a thing of this kind gets no gameplay numbers." if folder else
                         "No stats file was found: with no game folder, only the files beside the model were looked at.")
        return
    view = lib.read_stats(ref, resolver)
    own = ref == lib.stats_candidates(target, int(bp.class_id))[0]
    names = [os.path.basename(c.replace("\\", "/")) for c in view.chain]
    bp.stats_source = (f"From {names[0]}" + (f", which builds on {', '.join(names[1:])}" if len(names) > 1 else "") + "."
                       + ("" if own else " The game finds no stats file under this name, so it uses the default numbers of this kind of thing."))
    from . import ops_entity_stats as ES                                  # r2stats: what the kind of thing has by default
    cdefault = ES.class_default(lib, target, int(bp.class_id), resolver)
    bp.stats_own = own
    for key in sorted(view.attrs)[:STATS_ROWS_MAX]:
        row = bp.stats.add()
        row.key = str(key)
        v = view.attrs[key]
        row.value = f"{v:g}" if isinstance(v, (int, float)) else str(v)
        row.origin = os.path.basename(str(view.origin.get(key, ref)).replace("\\", "/"))
        if isinstance(v, (int, float)):
            ES.number_row(row, v, cdefault)                                   # r2stats
    notes = []
    if len(view.attrs) > STATS_ROWS_MAX:
        notes.append(f"{len(view.attrs) - STATS_ROWS_MAX} more numbers are not listed.")
    if view.errors:
        notes.append("The stats script stops with an error, so the game would give this thing no numbers at all.")
    if view.missing:
        notes.append(f"A script it builds on was not found: {', '.join(os.path.basename(m) for m in view.missing)}.")
    if view.unsupported:
        notes.append(f"{len(view.unsupported)} lines of the stats files could not be understood and are left out.")
    bp.stats_note = " ".join(notes)


def load_blueprint(ent, *, create_default: bool = False, keep_numbers: bool = False) -> str:
    """Read the entity's blueprint file into the panel's values (or start a new blueprint when there is none and ``create_default``). Returns one
    plain line for the user; raises :class:`Refused` when there is nothing to load. The gameplay numbers are listed again and the changes made to
    them are thrown away (Reset), unless ``keep_numbers`` (an export just wrote the file: the numbers are not part of it, the panel still shows them)."""
    bp = ent.holder.bz_blueprint
    path = blueprint_path(ent)
    if path is None and not create_default:
        raise Refused("There is no blueprint (.ebp) file beside the model. Import the model from the game's own files, or start a project from one of the game's things (File > New > Impossible Creatures Project).")
    lib = scene_lib()
    data = b""
    if path is not None:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise Refused(f"The blueprint file could not be read: {e.strerror or e}") from None
    state, note, values = "FILE", "", Values()
    if path is None:
        values = default_values(ent)
        state, note = ("NEW", "") if lib is not None else ("NO_LIBRARY", "")
    elif lib is None:
        state = "NO_LIBRARY"
    else:
        try:
            sb = lib.SceneBlueprint.read(data)
            state, note = classify(sb)
            values = values_of(sb)
        except Exception as e:                                            # noqa: BLE001 -- any parse failure is a plain message, not a crash
            state, note = "PROBLEM", f"The blueprint file could not be read: it is damaged or is not a blueprint file. (Details: {e})"
    if state == "NO_LIBRARY":
        note = ("Editing the blueprint needs a newer version of the add-on's reading library. "
                + ("The blueprint file is copied unchanged when you export." if path else "A new blueprint cannot be written without it."))
    with quiet():
        bp.has_skeleton = ent.rig is not None
        bp.icon_image = ""
        bp.skinned = ent.rig is not None and any(len(p.vertex_groups) > 1 for p in ent.parts)
        bp.source_path = path or ""
        bp.source_sha1 = _sha1(data) if path else ""
        try:
            left = fill_pg(bp, values)
            if left:
                state, note = "PROBLEM", f"The file holds a {words(left)} the panel cannot show; nothing was changed."
        except (ValueError, TypeError, OverflowError, UnicodeError) as e:
            state, note = "PROBLEM", f"A value in the file does not fit the panel ({e})."
        bp.folder, bp.folder_source = determine_folder(ent, path, values.class_id, bp.has_skeleton, bp.skinned)
        bp.state, bp.note, bp.loaded = state, note, True
        bp.baseline = values.to_json()
        try:
            refresh_stats(bp, ent, keep=keep_numbers)
        except Exception as e:                                            # noqa: BLE001
            bp.stats.clear()
            bp.stats_note = f"The gameplay numbers could not be listed: {e}"
    what = os.path.basename(path) if path else "a new blueprint"
    return {"FILE": f"Loaded {what}.", "NEW": "Started a new blueprint (it is written when you export).",
            "LEGACY": f"Loaded {what}: old layout, read only.", "EMBEDDED": f"Loaded {what}: model inside, read only.",
            "PROBLEM": note, "NO_LIBRARY": note}[state]


# ---------------------------------------------------------------------------------------------------------------- writing
def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def protected_roots() -> list:
    """The game folders no export may write into: the one the add-on reads the game from (:func:`game_dir`: the Game folder preference, else
    ``$IC_INSTALL``, else the usual Steam folders), and ``$IC_INSTALL`` and ICParse's Steam folder whatever the preference says."""
    out = []
    folder = game_dir()[0]
    if folder:
        out.append(folder)
    env = os.environ.get("IC_INSTALL", "")
    if env:
        out.append(env)
    try:
        from ICParse.data import default_root
        root = default_root()
        if root:
            out.append(root)
    except Exception:                                                     # noqa: BLE001
        pass
    return out


def check_target(directory) -> str:
    """The absolute folder to write into, or :class:`Refused` with the reason. Never the game's own (Steam) folder."""
    if not directory or not str(directory).strip():
        raise Refused("Choose a folder to write the files into.")
    path = _abs(directory)
    if os.path.exists(path) and not os.path.isdir(path):
        raise Refused(f"'{path}' is a file, not a folder.")
    for root in protected_roots():
        if _inside(path, root):
            raise Refused("That is the game's own folder. Write into a separate folder, then copy the files into a copy of the game that you own: "
                          "Steam can overwrite or delete anything you add to its copy.")
    if "steamapps" in {p.lower() for p in os.path.realpath(path).replace("\\", "/").split("/")}:
        raise Refused("That folder is inside Steam's game folders. Write into a separate folder, then copy the files into a copy of the game "
                      "that you own: Steam can overwrite or delete anything you add to its copy.")
    return path


def earlier_export_files(item_dir: str, item: str):
    """The files the earlier export of ``item`` listed (``data/...``, '/'-separated), read from the manifest it left. Raises :class:`Refused`
    when ``item_dir`` exists and is not an earlier export of this item (no manifest of its own in it): nothing is written into a folder that
    holds somebody's other files. None when there is no folder yet."""
    if not os.path.lexists(item_dir):
        return None
    mine = os.path.join(item_dir, "data", "hub", f"{item}.item")
    if not os.path.isdir(item_dir):
        raise Refused(f"'{item_dir}' is a file, not a folder.")
    if not os.path.isfile(mine):
        if not os.listdir(item_dir):
            return []
        raise Refused(f"The folder {item_dir} exists and is not an earlier export of {item} (it has no data/hub/{item}.item), so nothing was "
                      "written and nothing of yours is lost. Choose another folder or another name.")
    try:
        man = importlib.import_module("ICParse.hub.manifest")
        with open(mine, "rb") as f:
            return [p for p, _h, _n in man.parse(f.read()).files] + [f"data/hub/{item}.item"]
    except Exception:                                                     # noqa: BLE001 -- a manifest that cannot be read: nothing is removed
        return []


def remove_stale(item_dir: str, earlier, written, keep=()) -> list:
    """Delete the files the earlier export listed that this export did not write (an effect or sound taken out, a texture renamed), so the
    folder is the new export and not the new export plus leftovers; empty folders go too. ``keep``: files never deleted (the model's own
    source). Returns the paths removed ('/'-separated)."""
    gone = []
    new = {w.lower() for w in written}
    protect = []
    for k in keep:
        try:
            protect.append(os.path.realpath(k))
        except (OSError, TypeError, ValueError):
            pass
    for rel in earlier or []:
        if rel.lower() in new:
            continue
        path = os.path.join(item_dir, *rel.split("/"))
        if not os.path.isfile(path) or os.path.realpath(path) in protect:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        gone.append(rel)
        d = os.path.dirname(path)
        while os.path.abspath(d) != os.path.abspath(item_dir):
            try:
                os.rmdir(d)
            except OSError:
                break
            d = os.path.dirname(d)
    return gone


def write_file(path: str, data: bytes) -> None:
    """Write through a temporary file and rename, so a failed write never leaves half a file under the real name."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".{os.path.basename(path)}.bz-{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def blueprint_bytes(ent, bp):
    """(the .ebp bytes to write, the fields that were edited, one plain note). Only the chunks of edited fields are re-encoded by ``SceneBlueprint``;
    with no edit the file comes out byte for byte. Raises :class:`Refused` when the blueprint cannot be written."""
    state = bp.state
    if state == "PROBLEM":
        raise Refused(f"The blueprint cannot be written: {bp.note}")
    if state == "EMBEDDED":
        raise Refused("This blueprint carries its model inside itself, so there is no separate .sgm file to write beside it. "
                      "Exporting these is not supported yet.")

    def source_bytes() -> bytes:
        try:
            with open(bp.source_path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise Refused(f"The blueprint file could not be read: {e.strerror or e}") from None
        if _sha1(data) != bp.source_sha1:
            raise Refused(f"The blueprint file '{os.path.basename(bp.source_path)}' changed on disk after it was loaded. Press Reset to read it "
                          f"again (the values in the panel are then replaced).")
        return data

    if state in ("LEGACY", "NO_LIBRARY"):
        if not bp.source_path:
            raise Refused(bp.note or "There is no blueprint to write.")
        return source_bytes(), [], "copied unchanged from the original"
    lib = scene_lib()
    if lib is None:
        raise Refused("The blueprint cannot be written: the add-on's reading library is too old or missing.")
    wanted = values_from_pg(bp)
    icon = _addon("sceneobj.ops_blueprint_extras").pending_icon(bp)          # the picture made with Icon from this view, or None
    if state == "NEW":
        bad = problems_of(wanted)
        if bad:
            raise Refused("The blueprint cannot be written yet. " + " ".join(bad))
        sb = lib.new_scene_ebp(name=wanted.name, defstats_class=wanted.class_id, footprint=wanted.footprint, offset=wanted.offset,
                               sounds=list(wanted.sounds), icon=icon)
        return sb.to_bytes(), list(FIELDS) + (["icon"] if icon is not None else []), (
            "a new blueprint (it has no motion tree and no events, so the game will not play the model's animations with it)")
    data = source_bytes()
    try:
        sb = lib.SceneBlueprint.read(data)
        current = values_of(sb)
    except Exception as e:                                                # noqa: BLE001 -- the reader's words, after a plain lead-in
        raise Refused(f"The blueprint file could not be read: it is damaged or is not a blueprint file. (Details: {e})") from None
    changes = changed_fields(current, wanted)
    if icon is not None and sb.header.icon != icon:
        changes.append("icon")
    if not changes:
        return data, [], "copied unchanged from the original"
    bad = problems_of(wanted, [c for c in changes if c != "icon"])
    if bad:
        raise Refused("The blueprint cannot be written yet. " + " ".join(bad))
    try:
        if "name" in changes or "class" in changes or "icon" in changes:
            kw = {}
            if "name" in changes:
                kw["name"] = wanted.name
            if "class" in changes:
                kw["defstats_class"] = wanted.class_id
            if "icon" in changes:
                kw["icon"] = icon
            sb.set_header(**kw)
        if "footprint" in changes or "offset" in changes:
            sb.set_build(footprint=wanted.footprint if "footprint" in changes else None, offset=wanted.offset if "offset" in changes else None)
        if "sounds" in changes:
            sb.set_sounds(list(wanted.sounds))
    except ValueError as e:                                               # the library says in plain words why a value cannot be stored
        raise Refused(f"The blueprint cannot be changed: {e}") from None
    out = sb.to_bytes()
    back = lib.SceneBlueprint.read(out)
    left = changed_fields(values_of(back), wanted)                       # read back what was written: no silent difference
    if "icon" in changes and back.header.icon != icon:
        left.append("icon")
    if left:
        raise Refused(f"The blueprint was built, but reading it back does not give the {words(left)} you set. Nothing was written.")
    return out, changes, f"changed: {words(changes)}; everything else copied unchanged from the original"


def scene_bytes(context, ent, legacy_lod: bool = True, drop_morphs: bool = False):
    """``scene_bytes_and_files`` without the texture files."""
    return scene_bytes_and_files(context, ent, legacy_lod, drop_morphs)[:4]


def scene_bytes_and_files(context, ent, legacy_lod: bool = True, drop_morphs: bool = False, entity: str = "",
                          remove_missing: bool = False, report: dict = None, refuse_errors: bool = True):
    """(the .sgm bytes, parts rebuilt, parts kept, notes, texture files {lower-case path inside Data: bytes}): exactly what ``bz.export_scene`` writes (geometry of every edited part, plus the rig's clips
    when there is a rig; ``bz.export_scene_geometry`` when there is none). Raises :class:`Refused` with the exporter's own reason. ``drop_morphs``:
    an edited part with damage states is written without them instead of refused (the option "Drop the damage states of edited parts").
    ``report`` (a dict) receives ``geo`` (the ``scene.SceneExport``) and ``plan`` (the ``actions.ClipPlan``, None without a rig).
    ``refuse_errors=False``: an error of the load validation becomes a note ("Export anyway")."""
    obj = ent.parts[0]
    try:
        out, geo, plan = _addon("scene_skeleton").export_with_clips(context, obj, ent.rig, legacy_lod, drop_morphs,
                                                                       entity=entity, remove_missing=remove_missing,
                                                                       refuse_errors=refuse_errors)
        if plan is not None:
            geo.notes += plan.warnings
    except ValueError as e:
        raise Refused(str(e)) from None
    if report is not None:
        report.update(geo=geo, plan=plan)
    return out, list(geo.rebuilt), list(geo.kept), list(geo.notes), dict(geo.files)


# ---------------------------------------------------------------------------------------------------------------- replace the original, or a new name
@dataclass(frozen=True)
class ReplaceTarget:
    """The game's own thing this project can replace: its file name (no extension) and its folder under art/ebps."""
    name: str
    folder: str
    how: str                  # STAMP (what the project was started from), IDENTITY (the model is a byte copy of it) or PATH (where the files lie)


REPLACE_HOW = {
    "STAMP": "the thing this project was started from",
    "IDENTITY": "the game's own file this model is a copy of",
    "PATH": "the file this model was opened from",
}

MODE_NEW, MODE_REPLACE = "NEW", "REPLACE"


def replace_target(ent):
    """The thing of the game the export can replace, or None when there is none. In this order: the donor New Project stamped on the objects
    (``bz_donor``); the stock thing the model is still a byte copy of (:func:`find_donor`); the file the model lies in when that is a
    Data/art/ebps tree. None for a model that came from nowhere the game has a name for."""
    kind = _kind_of(ent)
    stamp = _stamp(ent, "bz_donor")
    member, how = (stamp, "STAMP") if stamp else (None, "")
    if member is None and ent.source.lower().endswith(".sgm"):
        found = find_donor(ent.source, (kind,) if kind else _KIND_OF_PROJECT)
        member, how = (found, "IDENTITY") if found else (None, "")
    if member:
        rel = _rel_of_member(member)
        stem = os.path.splitext(member.replace("\\", "/").rsplit("/", 1)[-1])[0]
        if rel is not None and stem:
            return ReplaceTarget(clean_name(stem), clean_folder(rel), how)
    rel = _folder_from_path(ent.source) if ent.source else None
    if rel is not None:
        stem = os.path.splitext(os.path.basename(ent.source))[0]
        if stem:
            return ReplaceTarget(clean_name(stem), clean_folder(rel), "PATH")
    return None


def mode_words(mode: str, name: str, folder: str, hard_bound: bool = False) -> str:
    """What choosing this mode does to the game, in plain words (shown beside the choice and written into the README)."""
    where = f"{folder + '/' if folder else ''}{name}"
    if mode == MODE_REPLACE:
        return (f"These files take the place of the game's own '{where}'. Everything that uses it (maps, the build menu, the computer "
                f"players) gets your version. In a game with other players, every player needs the same files: the kind, the footprint, "
                f"the offset and the size of the model are compared when a game starts.")
    return (f"'{where}' is a name of your own: the game only shows it if a map or a script asks for it by that name, and nothing in the "
            f"game changes by itself. To change a thing of the game instead, choose Replace the original."
            + (" (This name is one the game looks up by itself, so these files would replace that thing.)" if hard_bound else ""))


@dataclass
class EntityExport:
    root: str                 # the item folder written into: <folder chosen>/<item id>
    sgm: str
    ebp: str
    readme: str
    name: str
    folder: str
    rebuilt: list             # parts rebuilt from their meshes
    kept: list                # parts that kept the source's bytes
    changes: list             # blueprint fields that were edited
    blueprint_note: str
    notes: list
    item: str = ""            # the item's id (its folder's name)
    mode: str = MODE_NEW
    manifest: str = ""
    files: list = None        # every file written, as paths inside the item folder
    problems: list = None     # what the checks found (scenecheck.Problem), errors included when "export anyway" was ticked
    in_place: bool = False    # the model's own source file was overwritten (the session was re-stamped on it)


@dataclass
class EntityBuild:
    """Everything an Export Entity Folder would write, worked out in memory (nothing is written)."""
    ent: object
    bp: object
    mode: str
    target: object            # the ReplaceTarget (None when the mode is NEW)
    name: str
    folder: str
    item: str
    sgm: bytes
    ebp: bytes
    textures: dict            # path inside Data (lower case) -> bytes
    rebuilt: list
    kept: list
    changes: list
    blueprint_note: str
    notes: list
    hard_bound: bool
    geo: object
    plan: object
    files: dict               # path inside the item folder -> bytes: every file that would be written, the manifest included
    sgm_rel: str
    ebp_rel: str
    title: str
    stats_lua: str = ""       # r2stats: the text of the stats script written beside the blueprint ('' = none)


def build_entity(context, obj, *, name=None, folder=None, mode: str = MODE_NEW, legacy_lod: bool = True, drop_morphs: bool = False,
                 remove_missing: bool = False, refuse_errors: bool = True) -> EntityBuild:
    """Work out the item folder for the entity ``obj`` belongs to, in memory. ``mode`` REPLACE takes the name and folder of
    :func:`replace_target` (so the files take the place of the game's own); NEW takes ``name`` / ``folder`` (the project's own name and the
    blueprint's folder when left out). Raises :class:`Refused` with a plain reason, before anything exists on disk."""
    ent = entity_of(obj)
    if ent is None:
        raise Refused("Select a gatherer, character, structure or prop that was imported or started with this add-on.")
    if _stamp(ent, "bz_project") == "EFFECT":                             # a1fx: an effect mesh has no blueprint (export_entity routes it to fxpanel)
        raise Refused("An effect model has no blueprint, so there is nothing to build or check here. Its effect files are checked when you export, "
                      "and in the Effects panel.")
    bp = getattr(ent.holder, "bz_blueprint", None)
    if bp is None:
        raise Refused("The blueprint tools are not switched on in this session.")
    why = _addon("store").axes_refusal(*ent.parts, ent.rig)
    if why:
        raise Refused(why)
    changed = _addon("scenesafety").source_error(ent.parts[0])
    if changed:
        raise Refused(changed)
    if not bp.loaded:
        load_blueprint(ent)                                               # an untouched export needs no visit to the panel; raises when there is no .ebp
    if bp.state == "NONE":
        raise Refused("There is no blueprint (.ebp) file for this model. Import it from the game's own files, or start a project from one of the game's things (File > New > Impossible Creatures Project).")
    target = None
    if mode == MODE_REPLACE:
        target = replace_target(ent)
        if target is None:
            raise Refused("There is no thing of the game to replace: this model was not started from one of the game's own things and does "
                          "not lie in a Data/art/ebps folder. Choose 'A new name of my own'.")
        out_name, out_folder = target.name, target.folder
    else:
        out_name = clean_name(name if name else default_name(ent))
        out_folder = clean_folder(bp.folder if folder is None else folder)
    hub = importlib.import_module("ICParse.hub.scenery")
    item = hub.item_id(out_name)
    ebp_data, changes, bp_note = blueprint_bytes(ent, bp)
    rep: dict = {}
    sgm_data, rebuilt, kept, notes, textures = scene_bytes_and_files(context, ent, legacy_lod, drop_morphs, entity=out_name,
                                                                     remove_missing=remove_missing, report=rep,
                                                                     refuse_errors=refuse_errors)
    ssk = _addon("scene_skeleton")
    ebp_data, bone_notes = ssk.follow_bones_in_blueprint(ebp_data, ent.parts[0])           # events naming bones
    notes = list(notes) + bone_notes
    ebp_data, clip_notes = _addon("scenesafety").follow_clips_in_blueprint(ebp_data, rep.get("plan"))   # motion states and events naming clips
    notes += clip_notes
    from . import slice_markers                                           # renamed markers: their .ebp events follow
    ebp_data, marker_notes = slice_markers.export_entity_ebp(ent.parts[0], ebp_data, sgm_data)
    notes += marker_notes
    ebp_data, level_notes = _addon("scene_damage").blueprint_with_added_levels(ent.parts, ebp_data)   # damage levels added in Blender
    notes += level_notes
    ebp_data, fx_notes = _addon("fxpanel").export_entity_ebp(ent, ebp_data)   # a1fx: events edited in the Effects box (refuses an effect the game would not load)
    notes += fx_notes
    ebp_data, motion_notes = _addon("motionspanel").export_entity_ebp(ent, ebp_data, rep.get("plan"))   # r2motions: edits of the Motions box, notes on clips nothing plays
    notes += motion_notes
    if bp_note.startswith("copied unchanged") and any(n.startswith("Motions:") for n in motion_notes):
        bp_note = "copied from the original with the motions you changed"
    ctl = controllers()
    hard_bound = bool(ctl.is_hard_bound(out_folder, out_name))
    sgm_rel, ebp_rel = hub.item_paths(out_folder, out_name)
    sgm_path = os.path.join(*sgm_rel.split("/"))
    notes += ssk.includer_notes(ent.parts[0], os.path.join("x", sgm_path))   # files including a replaced stock model
    files = {sgm_rel: sgm_data, ebp_rel: ebp_data}
    for rel, blob in sorted(textures.items()):                            # the model's new texture files (lower-case paths inside Data)
        files[f"{hub.ROOT}/{rel}"] = blob
    fx_files = _addon("fxpanel").effect_file_blobs(ent)                   # a1fx: the project's effect files, pictures and meshes (in the manifest)
    files.update(fx_files)
    if fx_files:
        notes += [f"{len(fx_files)} effect file(s) in the item's data folder (with the pictures and meshes they use)"]
    from . import ops_entity_stats as ES                                  # r2stats: the stats script (<name>.lua) of edited numbers
    stats_out = ES.export_files(ent, bp, mode, ebp_rel, MODE_NEW)
    files.update(stats_out.files)
    notes += stats_out.notes
    title = (bp.display_name or "").strip() if not str(bp.display_name or "").startswith("$") else ""
    files[f"{hub.ROOT}/hub/{item}.item"] = hub.manifest_bytes(item, title or out_name, files)
    return EntityBuild(ent, bp, mode, target, out_name, out_folder, item, sgm_data, ebp_data, dict(textures), list(rebuilt), list(kept),
                       list(changes), bp_note, list(notes), hard_bound, rep.get("geo"), rep.get("plan"), files, sgm_rel, ebp_rel,
                       title or out_name, stats_out.script or "")


def check_build(context, b: EntityBuild) -> list:
    """The checks over what ``b`` would write (``scenecheck``): the model's load rules, the blueprint's 14 rules and the hub's validator over
    the item folder. Nothing is written. Returns a list of ``scenecheck.Problem`` (errors first)."""
    sc = _addon("scenecheck")
    folder = game_dir()[0]
    resolver = None
    try:
        resolver = _addon("core").scene_texture_source(os.path.dirname(b.ent.source), data_root=folder)
    except Exception:                                                     # noqa: BLE001 -- no resolver: the rules that need the game's files say so
        resolver = None
    stock = sc.stock_index(folder)
    problems = sc.model_problems(b.sgm, files=b.textures, resolver=resolver)
    role = None
    try:
        role = subtype_of(b.bp)
        role = role if role in ("GATHERER", "CHARACTER", "STRUCTURE", "PROP") else None
    except Exception:                                                     # noqa: BLE001
        role = None
    problems += sc.blueprint_problems(b.ebp, b.sgm, name_path=f"art/ebps/{b.folder + '/' if b.folder else ''}{b.name}.ebp", role=role,
                                      mod=target_mod(), resolver=resolver, textures=dict(b.textures), lua=b.stats_lua or None)
    problems += sc.item_problems(b.files, b.item, stock)
    problems = sc.unique(problems)
    problems.sort(key=lambda p: -sc.RANK[p.severity])
    return problems


def lua_words(mode: str, written: bool = False) -> list:
    """The README's lines about the stats file (.lua), which an export does not write. The game finds it by the blueprint's own path with the
    extension changed, through the file alias, and falls back to the default numbers of the kind only when no such file exists
    (``BlueprintHeader::LoadAttributes``, EntityBlueprint.cpp:2196-2201). So it depends on the mode: files replacing a thing of the game
    sit at its path and keep its stock .lua; a new name has no .lua of its own."""
    if written:                                                           # r2stats: numbers were changed in the Gameplay numbers panel
        return ["- The stats file (.lua) is in this folder: it starts from the default numbers of its kind of thing and sets the numbers that",
                "  differ (the ones you changed, and the ones the original's own stats file sets). The game finds it by the blueprint's path.",
                "  Every player in a multiplayer game needs the same file."]
    if mode == MODE_REPLACE:
        return ["- Not in this folder: the stats file (.lua). The game still finds the original's .lua at the same path (it looks it up by",
                "  the blueprint's path), so the numbers stay the original's. To change them, put a .lua of the same name beside the .ebp."]
    return ["- Not in this folder: the stats file (.lua). With no .lua of the same name beside the blueprint, the game uses the default",
            "  numbers for its kind of thing. To give it numbers of its own, put a .lua of the same name beside the .ebp."]


def readme_text(b: EntityBuild, parent_name: str = "") -> str:
    """The README written in the item folder: plain words, how to try the files in a copy of the game the user owns."""
    files = sorted(b.files)
    rel = f"{b.folder + '/' if b.folder else ''}{b.name}"
    lines = [
        "HOW TO TRY THESE FILES IN THE GAME",
        "==================================",
        "",
        "This was written by the Impossible Creatures add-on for Blender (Export Entity Folder).",
        "The folder this file is in is the item. Besides this file it holds one folder, 'data', laid out like the game's own Data folder",
        "(every name in lower case). A thing in the game is two files that belong together:",
        "",
        f"  data/art/ebps/{rel}.sgm   the model: its shape, skeleton and animations",
        f"  data/art/ebps/{rel}.ebp   the blueprint: its name, kind, footprint, offset and sounds",
        *([f"  data/art/ebps/{rel}.lua   the stats: its numbers (hit points, cost, speed ...)"] if b.stats_lua else []),
        "",
        "Every file of the item:",
        *[f"  {f}" for f in files],
        "",
        "What this export does to the game",
        f"- {mode_words(b.mode, b.name, b.folder, b.hard_bound)}",
        "",
        "What to do",
        "1. Work in a copy of the game that you own. Do NOT put these files into the Steam copy of the game: Steam can overwrite or",
        "   delete files that are not its own.",
        "2. Copy the 'data' folder of this item into the main folder of your copy of the game (the one that already has a Data folder)",
        "   and let your computer merge the two folders. On Linux, copy the files inside 'data' into the game's 'Data' folder instead.",
        "3. Start the game from your copy. The game reads loose files in its Data folder before the files packed inside its .sga archives,",
        "   so these files are used instead of a packed file with the same path.",
        "4. To undo it, delete the files listed above from your copy again.",
        "",
        "About this export",
        f"- Model: {len(b.rebuilt)} part(s) rebuilt from your edits, {len(b.kept)} part(s) left exactly as they were; the animations follow your Actions.",
        f"- Blueprint: {b.blueprint_note}.",
        "- Several players: the kind (class), the footprint, the offset and the size of the model are compared between players when a",
        "  multiplayer game starts. Every player needs these same files, or the game will not let them play together.",
        ("- Textures: the new or changed ones are in data/art/textures (listed above); the others are the game's own." if any(
            f.startswith("data/art/textures/") for f in files) else
         "- Not in this folder: textures (the model uses the ones the game already has)."),
        f"- data/hub/{b.item}.item is the add-on's list of the item's files and their checksums. The game itself does not read it.",
        *lua_words(b.mode, bool(b.stats_lua)),
        "- If something does not show up or the game complains, the file warnings.log in the game folder says why.",
    ]
    if b.notes:
        lines += ["", "Notes from the export"] + [f"- {n}" for n in b.notes[:12]]
    return "\n".join(lines) + "\n"


def item_folder_name(ent, mode: str, item: str, chosen: str = "") -> str:
    """The name of the item's folder inside the chosen export folder. ``chosen`` (typed in the dialog) wins. Otherwise a thing with a name of its
    own is called by that name (its item id), and a replacement of the game's own thing by the PROJECT's name: the item id of a replacement is the
    original's ('lab'), so two projects that both replace the Lab would write into one folder and the second would replace the first."""
    hub = importlib.import_module("ICParse.hub.scenery")
    if chosen and chosen.strip():
        return hub.item_id(chosen)
    if mode == MODE_REPLACE and ent is not None:
        return hub.item_id(default_name(ent))
    return item


def export_entity(context, obj, directory, *, name=None, folder=None, mode: str = MODE_NEW, legacy_lod: bool = True,
                  drop_morphs: bool = False, remove_missing: bool = False, export_anyway: bool = False, item_folder: str = "") -> EntityExport:
    """Write the item folder ``<directory>/<item folder>/`` (its files under a lower-case ``data/``: the model and blueprint in
    ``data/art/ebps/<folder>/``, new textures in ``data/art/textures/``, the hub manifest in ``data/hub/``) and the ``README.txt`` in the same
    folder (the install and the checks leave a README at the folder's top alone), and remember the folder on the scene
    (``scene["bz_last_item_dir"]``). The item folder is called ``item_folder`` when given, else :func:`item_folder_name`. Everything is worked out
    and checked before the first file is written, so a refusal leaves the disk as it was. Raises :class:`Refused` with a plain reason.

    ``mode``: ``"NEW"`` (a name of your own: ``name`` / ``folder``) or ``"REPLACE"`` (the game's own thing the project came from:
    :func:`replace_target`). The checks (:func:`check_build`) refuse the export on any ERROR unless ``export_anyway``. A file about to be
    written onto the model's own source file (or its blueprint) is guarded like the File > Export of the same model
    (``scenesafety.guard_write``)."""
    root = check_target(directory)
    ent0 = entity_of(obj)
    if ent0 is not None and _stamp(ent0, "bz_project") == "EFFECT":       # a1fx: an effect mesh has no blueprint; it goes to data/art/fx of an item folder
        return _addon("fxpanel").export_effect_model(context, obj, directory, name=name, folder=folder, legacy_lod=legacy_lod,
                                                      drop_morphs=drop_morphs, remove_missing=remove_missing, export_anyway=export_anyway,
                                                      item_folder=item_folder)
    b = build_entity(context, obj, name=name, folder=folder, mode=mode, legacy_lod=legacy_lod, drop_morphs=drop_morphs,
                     remove_missing=remove_missing, refuse_errors=not export_anyway)
    problems = check_build(context, b)
    errs = [p for p in problems if p.severity == "ERROR"]
    if errs and not export_anyway:
        raise Refused(f"The game would not accept these files ({len(errs)} error{'s' if len(errs) > 1 else ''}), so nothing was written: "
                      + " ".join(f"({i}) {p.line()}" for i, p in enumerate(errs[:3], 1))
                      + (f" ... and {len(errs) - 3} more (press Check to see them all)." if len(errs) > 3 else "")
                      + " Tick 'Export anyway' to write them regardless.")
    SS = _addon("scenesafety")
    ent = b.ent
    item_dir = os.path.join(root, item_folder_name(ent, b.mode, b.item, item_folder))
    earlier = earlier_export_files(item_dir, b.item)                      # refuses a folder that is not an earlier export of this item
    sgm_path = os.path.join(item_dir, *b.sgm_rel.split("/"))
    ebp_path = os.path.join(item_dir, *b.ebp_rel.split("/"))
    in_place = False
    try:
        in_place = SS.guard_write(ent.parts[0], sgm_path, b.sgm, b.geo, b.plan)      # onto the model's own file: only what the session can follow
        if bp_in_place(ent, b, ebp_path):
            what = SS.differences(b.geo, b.plan, _read(ent.source), b.sgm)
            if what and not in_place:
                raise SS.InPlaceRefused(f"Export to {os.path.basename(ebp_path)} refused: it is this model's own blueprint, and "
                                        f"{what[0]} would be applied a second time by the next export. Export under another name")
    except SS.InPlaceRefused as e:
        raise Refused(str(e)) from None
    written = {}
    try:
        for rel, blob in sorted(b.files.items()):
            path = os.path.join(item_dir, *rel.split("/"))
            write_file(path, blob)
            written[rel] = path
        readme = os.path.join(item_dir, "README.txt")
        write_file(readme, readme_text(b).encode("utf-8"))
    except OSError as e:
        raise Refused(f"The files could not be written into '{item_dir}': {e.strerror or e}. Some of them may be there already.") from None
    if in_place:
        SS.after_write(ent.parts[0], sgm_path, ent.rig, b.plan)           # the session matches the file it was built on
    if bp_in_place(ent, b, ebp_path):
        try:
            load_blueprint(ent, keep_numbers=True)                        # the panel shows the file just written, and its new SHA-1
        except Refused:
            pass
    try:
        context.scene["bz_last_item_dir"] = item_dir                      # Game > Install and the hub check start from this folder
        ent.holder["bz_last_item_dir"] = item_dir                         # and the Game panel shows THIS project's own last export
        ent.holder["bz_last_item_mode"] = b.mode                          # ... and whether it replaced the game's own thing or has a new name
        context.scene.bz_item_dir = ""                                    # a folder picked in the Game panel no longer wins over this export
    except (AttributeError, TypeError):
        pass
    notes = list(b.notes)
    gone = remove_stale(item_dir, earlier, list(written), keep=[ent.source, getattr(b.bp, "source_path", "")])
    if gone:
        notes.append(f"{len(gone)} file(s) of the earlier export are no longer part of the item and were taken out of its folder "
                     f"({', '.join(gone[:3])}{' ...' if len(gone) > 3 else ''})")
    leftovers = _leftovers(item_dir, written)
    if leftovers:
        notes.append(f"the item folder holds {len(leftovers)} file(s) from an earlier export that this one did not write "
                     f"({', '.join(leftovers[:3])}{' ...' if len(leftovers) > 3 else ''}): delete them, or Install into Test Game refuses the item")
    return EntityExport(item_dir, sgm_path, ebp_path, readme, b.name, b.folder, b.rebuilt, b.kept, b.changes, b.blueprint_note, notes,
                        item=b.item, mode=b.mode, manifest=os.path.join(item_dir, *f"data/hub/{b.item}.item".split("/")),
                        files=sorted(written), problems=problems, in_place=in_place)


def bp_in_place(ent, b, ebp_path: str) -> bool:
    """Whether the blueprint about to be written is the file the panel loaded (the model's own blueprint)."""
    src = getattr(b.bp, "source_path", "")
    return bool(src) and _addon("scenesafety").same_file(ebp_path, src)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _leftovers(item_dir: str, written: dict) -> list:
    """Files of an earlier export in ``item_dir`` that this one did not write (paths inside the item, '/'-separated)."""
    out = []
    for dirpath, _dirs, names in os.walk(item_dir):
        for fn in names:
            rel = os.path.relpath(os.path.join(dirpath, fn), item_dir).replace(os.sep, "/")
            if rel not in written and not importlib.import_module("ICParse.hub.paths").left_alone(rel):     # (the README is written beside data/)
                out.append(rel)
    return sorted(out)


# ---------------------------------------------------------------------------------------------------------------- operators
def _entity_poll(context):
    ent = entity_of(context.active_object)
    return ent is not None and getattr(ent.holder, "bz_blueprint", None) is not None


def _editable_poll(context):
    ent = entity_of(context.active_object)
    bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
    return bp is not None and bp.loaded and bp.state in EDITABLE


class BZ_OT_blueprint_load(bpy.types.Operator):
    """Load the blueprint (.ebp) beside the model into this panel"""
    bl_idname = "bz.blueprint_load"
    bl_label = "Load Blueprint"
    bl_options = {"REGISTER", "UNDO"}

    create_default: BoolProperty(name="Start a new one", default=False,
                                 description="Start a new blueprint if there is none; it is written on export")

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def execute(self, context):
        ent = entity_of(context.active_object)
        bp = ent.holder.bz_blueprint
        if bp.loaded and bp.state in EDITABLE and changed_fields(Values.from_json(bp.baseline), values_from_pg(bp)):
            self.report({"ERROR"}, "The panel holds changes that are not exported yet. Use Reset if you want to throw them away and read the file again.")
            return {"CANCELLED"}
        try:
            msg = load_blueprint(ent, create_default=self.create_default)
        except Refused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"WARNING"} if bp.state in ("PROBLEM", "NO_LIBRARY") else {"INFO"}, msg)
        return {"FINISHED"}


class BZ_OT_blueprint_reset(bpy.types.Operator):
    """Discard changes and reload the blueprint from its file"""
    bl_idname = "bz.blueprint_reset"
    bl_label = "Reset Blueprint"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ent = entity_of(context.active_object)
        bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
        return bp is not None and bp.loaded

    def execute(self, context):
        ent = entity_of(context.active_object)
        bp = ent.holder.bz_blueprint
        try:
            msg = load_blueprint(ent, create_default=bp.state == "NEW" or not blueprint_path(ent))
        except Refused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Back to the original values. {msg}")
        return {"FINISHED"}


class BZ_OT_blueprint_sound_add(bpy.types.Operator):
    """Add an empty line to the sound list; type the sound file's path into it"""
    bl_idname = "bz.blueprint_sound_add"
    bl_label = "Add Sound"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _editable_poll(context)

    def execute(self, context):
        bp = entity_of(context.active_object).holder.bz_blueprint
        bp.sounds.add()
        return {"FINISHED"}


class BZ_OT_blueprint_sound_remove(bpy.types.Operator):
    """Take this sound out of the list"""
    bl_idname = "bz.blueprint_sound_remove"
    bl_label = "Remove Sound"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="Row", default=-1, min=-1, description="Which sound to remove (counted from 0)")

    @classmethod
    def poll(cls, context):
        return _editable_poll(context)

    def execute(self, context):
        bp = entity_of(context.active_object).holder.bz_blueprint
        if not 0 <= self.index < len(bp.sounds):
            self.report({"ERROR"}, "There is no such sound in the list.")
            return {"CANCELLED"}
        bp.sounds.remove(self.index)
        return {"FINISHED"}


_MODE_ITEMS: list = []             # enum items must stay referenced while Blender shows them


def _mode_items(self, context):
    """The two choices: a name of your own (the first, so it is the default: nothing of the game is replaced unless you say so) and, when the
    project has a thing of the game to replace, replacing it, with its name."""
    items = [(MODE_NEW, "A new name of my own", "Files under a name you choose: the game shows them only if a map or a script asks for them")]
    try:
        ent = entity_of(context.active_object) if context is not None else None
        target = replace_target(ent) if ent is not None else None
    except Exception:                                                     # noqa: BLE001 -- a draw never fails for this
        target = None
    if target is not None:
        shown = f"{target.folder + '/' if target.folder else ''}{target.name}"
        items.append((MODE_REPLACE, f"Replace the original {shown}",
                      f"The files take the place of the game's own {shown} ({REPLACE_HOW[target.how]})"))
    _MODE_ITEMS[:] = items
    return _MODE_ITEMS


class BZ_OT_export_entity_folder(bpy.types.Operator):
    """Export the model and blueprint into an item folder, ready to copy into the game"""
    bl_idname = "bz.export_entity_folder"
    bl_label = "Export Entity Folder"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="Folder", subtype="DIR_PATH",
                              description="The folder the item folder is made in (not the game's own folder): <folder>/<name>/data/art/ebps/...")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})
    mode: EnumProperty(name="Export as", items=_mode_items, options={"SKIP_SAVE"},
                       description="Replace the game's own thing, or make a thing under a name of your own")
    entity_name: StringProperty(name="File name", default="", options={"SKIP_SAVE"},
                                description="File name without extension; empty = the project's name")
    folder: StringProperty(name="Folder in the game", default="", options={"SKIP_SAVE"}, description=HINT["folder"])
    item_folder: StringProperty(name="Item folder name", default="", options={"SKIP_SAVE"},
                                description="The name of the folder made inside the export folder. Empty: the thing's name, or, when replacing "
                                            "the original, this project's name. Two exports with the same name replace each other")
    drop_damage_states: BoolProperty(
        name="Leave damage states out of edited parts",
        description="Write edited parts without their damage states (shape keys) instead of with them",
        default=False)
    remove_deleted_parts: BoolProperty(
        name="Remove parts deleted in Blender",
        description="A part whose object was deleted is removed from the model, with its clip channels, instead of keeping "
                    "the source's geometry",
        default=False)
    export_anyway: BoolProperty(
        name="Export anyway",
        description="Write the files even if the checks find errors (the game may refuse them). Press Check in the IC Game tab to see what they found",
        default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _entity_poll(context)

    def invoke(self, context, event):
        ent = entity_of(context.active_object)
        if ent is not None:                                               # the name and folder belong to THIS entity, never to the last export
            bp = ent.holder.bz_blueprint
            if not bp.loaded and blueprint_path(ent):
                try:
                    load_blueprint(ent)
                except Refused:
                    pass
            self.entity_name = default_name(ent)
            self.folder = bp.folder if bp.loaded else ""
            if _stamp(ent, "bz_project") == "EFFECT":                     # a1fx: the folder under art/fx of the model it was started from
                self.folder = _addon("fxpanel").effect_model_target(ent)[1]
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column()
        ent = entity_of(context.active_object)
        is_fx = ent is not None and _stamp(ent, "bz_project") == "EFFECT"      # a1fx: an effect model has no blueprint and nothing to replace
        replacing = self.mode == MODE_REPLACE and not is_fx
        target = replace_target(ent) if ent is not None and not is_fx else None
        if not is_fx:
            col.prop(self, "mode", expand=True)
        if replacing and target is not None:
            name, folder = target.name, target.folder
            col.label(text=f"Replaces art/ebps/{folder + '/' if folder else ''}{name}")
        else:
            col.prop(self, "entity_name")
            col.prop(self, "folder")
            _hint(col, HINT["folder"])
            name, folder = clean_name(self.entity_name or "name"), clean_folder(self.folder)
        try:
            hard = bool(controllers().is_hard_bound(folder, name))
        except Exception:                                                 # noqa: BLE001
            hard = False
        if not is_fx:
            _hint(col, mode_words(MODE_REPLACE if replacing else MODE_NEW, name, folder, hard))
        try:
            item = importlib.import_module("ICParse.hub.scenery").item_id(name)
            shown_folder = item_folder_name(ent, MODE_REPLACE if replacing else MODE_NEW, item, self.item_folder)
        except Exception:                                                 # noqa: BLE001
            item = shown_folder = name
        col.prop(self, "item_folder")
        _hint(col, "Two exports into the same folder with the same item folder name replace each other: give each project its own. "
                   "Empty: " + ("the project's name." if replacing else "the thing's name."))
        if is_fx:
            col.label(text=f"Item folder: {shown_folder}/data/art/fx/{folder + '/' if folder else ''}{name}.sgm")
        else:
            col.label(text=f"Item folder: {shown_folder}/data/art/ebps/{folder + '/' if folder else ''}{name}.sgm / .ebp")
        col.separator()
        col.prop(self, "drop_damage_states")
        col.prop(self, "remove_deleted_parts")
        col.separator()
        col.prop(self, "export_anyway")
        _hint(col, "The files are checked first; an error stops the export unless this is ticked.")

    def execute(self, context):
        try:
            res = export_entity(context, context.active_object, self.directory, name=self.entity_name or None,
                                folder=self.folder if self.folder.strip() else None, mode=self.mode,
                                drop_morphs=self.drop_damage_states, remove_missing=self.remove_deleted_parts,
                                export_anyway=self.export_anyway, item_folder=self.item_folder)
        except Refused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:                                            # noqa: BLE001 -- surface anything else as a message, like Export Scene Object
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}
        errs = [p for p in (res.problems or []) if p.severity == "ERROR"]
        if errs:
            self.report({"WARNING"}, f"Written anyway, with {len(errs)} error(s) the game may refuse: " + errs[0].line())
        from . import panels_check
        nl = _addon("notelevels")
        important = [n for n in res.notes if panels_check.is_warning_note(n)]          # a new clip nothing plays comes first: it does nothing in the game
        rest = sorted((n for n in res.notes if n not in important), key=lambda n: nl.level(n) != "WARNING")      # what needs action before what was only done
        for n in (important + rest)[:8]:
            self.report({nl.level(n)}, n)                                 # a plain fact is information; only what needs action is a warning
        if len(res.notes) > 8:
            self.report({"INFO"}, f"... and {len(res.notes) - 8} more notes (the README in the item folder lists them)")
        if not res.ebp:                                                   # a1fx: an effect model: a model and effect files, no blueprint
            self.report({"INFO"}, f"Wrote the item {res.item} ({len(res.files or [])} files) under {res.root}: the effect model "
                                  f"{os.path.basename(res.sgm)} in data/art/fx (an effect model has no blueprint). {os.path.basename(res.readme)} says how to use it.")
            return {"FINISHED"}
        if res.in_place:
            self.report({"INFO"}, "The model's own source file was overwritten; this session now matches it")
        self.report({"INFO"}, f"Wrote the item {res.item} ({len(res.files or [])} files) under {res.root}: "
                              f"{'replaces the original ' if res.mode == MODE_REPLACE else ''}{res.folder + '/' if res.folder else ''}{res.name}; "
                              f"blueprint {res.blueprint_note}; model: {len(res.rebuilt)} part(s) rebuilt, {len(res.kept)} unchanged. "
                              f"{os.path.basename(res.readme)} says how to use it.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------------- the panel
def _plain(text):
    """A message without source files, function names and rule ids (``ICParse.plainwords``): what the modder reads."""
    try:
        from ICParse.plainwords import plain
    except ImportError:
        return text
    return plain(text)


def _hint(layout, text: str, width: int = 46) -> None:
    """A small grey explanation under a field."""
    col = layout.column(align=True)
    col.scale_y = 0.85
    for line in wrap(_plain(text), width):
        col.label(text=line)


def _alert(layout, text: str, icon: str = "ERROR", width: int = 46) -> None:
    col = layout.column(align=True)
    col.alert = True
    for i, line in enumerate(wrap(_plain(text), width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def _load_by_name(name: str) -> None:
    """Read the blueprint of the thing the object called ``name`` belongs to (the panel's first drawing asks for it)."""
    obj = bpy.data.objects.get(name)
    ent = entity_of(obj) if obj is not None else None
    bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
    if bp is not None and not bp.loaded and blueprint_path(ent):
        load_blueprint(ent)


def draw_blueprint(layout, obj) -> None:
    """The Blueprint panel's body for ``obj`` (a part, rig or group empty of a scene object). It never changes anything; every button is an operator."""
    ent = entity_of(obj)
    if ent is None:
        layout.label(text="Select a gatherer, character, structure or prop.")
        return
    bp = getattr(ent.holder, "bz_blueprint", None)
    if bp is None:
        _alert(layout, "The blueprint tools are not switched on in this session.")
        return
    layout.use_property_split = True
    layout.use_property_decorate = False
    path = blueprint_path(ent)
    if not bp.loaded:
        box = layout.box()
        if path:
            name = obj.name
            _addon("autoload").once(("blueprint", obj.session_uid), lambda: _load_by_name(name))
            box.label(text=f"Blueprint file: {os.path.basename(path)}", icon="FILE")
            _hint(box, "The blueprint is read when this panel opens, to show the kind, name, footprint and sounds. If nothing shows, press "
                       "Load Blueprint to try again.")
            box.operator("bz.blueprint_load", text="Load Blueprint", icon="FILE_REFRESH")
        else:
            box.label(text="No blueprint file beside the model.", icon="INFO")
            if OFFER_NEW_BLUEPRINT:
                _hint(box, "Every model needs a blueprint: press Start a New Blueprint, and it is written when you export.")
                box.operator("bz.blueprint_load", text="Start a New Blueprint", icon="ADD").create_default = True
            else:                                                         # a blueprint made from nothing has no motions or events: not offered
                _hint(box, "Every model needs a blueprint. Import the model from the game's own files, or start a project from one of the "
                           "game's things (File > New > Impossible Creatures Project): that brings its blueprint, with its animations.")
        return
    _draw_status(layout, ent, bp, path)
    if bp.state in ("PROBLEM", "NO_LIBRARY"):
        return                                                            # nothing trustworthy to show; the status box says why
    editable = bp.state in EDITABLE
    box = layout.box()
    box.label(text="Name", icon="SORTALPHA")
    col = box.column()
    col.enabled = editable
    col.prop(bp, "display_name", text="")
    if len(bp.display_name) > NAME_LIMIT:
        _alert(box, f"The name is {len(bp.display_name)} letters long; the game reads at most {NAME_LIMIT}.")
    _hint(box, HINT["name"])

    info = class_info(bp.class_id)
    box = layout.box()
    box.label(text="Kind of thing", icon="OUTLINER_OB_ARMATURE")
    col = box.column()
    col.enabled = editable
    col.prop(bp, "class_choice", text="Kind")
    col.prop(bp, "class_id", text="Class number")
    if not info.registered:
        _alert(box, "The game gives this number no behaviour: the thing would be a plain prop that only plays animations."
               if not info.expansion_only else "Only the expansion builds of the game know this number (Game build in the add-on's "
                                               "preferences says which build your work is made for).", icon="INFO")
    _hint(box, HINT["class"])

    box = layout.box()
    box.label(text="Footprint and offset", icon="MESH_GRID")
    row = box.row(align=True)
    row.enabled = editable and info.buildable
    row.prop(bp, "footprint_x", text="X")
    row.prop(bp, "footprint_z", text="Z")
    _hint(box, HINT["footprint"] if info.buildable else HINT["footprint_unused"])
    row = box.row()
    row.enabled = editable and info.buildable
    row.operator("bz.blueprint_footprint_calc", text="Calc from model", icon="DRIVER_DISTANCE")
    _hint(box, "Measures the model the way the game does when the footprint is 0 and 0.")
    col = box.column()
    col.enabled = editable
    col.prop(bp, "offset", text="Offset")
    _hint(box, HINT["offset"])

    box = layout.box()
    box.label(text=f"Sounds ({len(bp.sounds)})", icon="SOUND")
    col = box.column(align=True)
    col.enabled = editable
    for i, s in enumerate(bp.sounds):
        row = col.row(align=True)
        row.prop(s, "path", text="")
        row.operator("bz.blueprint_sound_remove", text="", icon="X").index = i
    col.operator("bz.blueprint_sound_add", text="Add Sound", icon="ADD")
    _hint(box, HINT["sounds"])

    _draw_icon(layout, bp, editable)

    box = layout.box()
    box.label(text="Where the game finds it", icon="FILE_FOLDER")
    col = box.column()
    col.enabled = editable
    col.prop(bp, "folder", text="Folder")
    _hint(box, HINT["folder"] + " " + FOLDER_SOURCE_WORDS.get(bp.folder_source, ""))
    out_name = default_name(ent)
    try:
        if controllers().is_hard_bound(clean_folder(bp.folder), out_name):
            _hint(box, "The game looks this one up by name: keep this folder and name to replace the original.")
    except Exception:                                                     # noqa: BLE001
        pass

    box = layout.box()
    box.label(text="Export", icon="EXPORT")
    box.operator("bz.export_entity_folder", text="Export Entity Folder...", icon="EXPORT")
    _hint(box, "Makes an item folder with the model and blueprint (and a README), ready to copy into your game: either a new name of your own, "
               "or in place of the game's original. Press Check (below) first to test it.")


def _draw_icon(layout, bp, editable: bool) -> None:
    """The Icon box: the new picture waiting to be written (if any), and the buttons that make or drop it."""
    box = layout.box()
    box.label(text="Icon", icon="IMAGE_DATA")
    img = bpy.data.images.get(bp.icon_image) if bp.icon_image else None
    prev = getattr(img, "preview", None) if img is not None else None
    if img is not None and prev is not None:
        box.label(text="New icon (written when you export)", icon_value=prev.icon_id)
    elif img is not None:
        box.label(text="New icon (written when you export)", icon="IMAGE_DATA")
    else:
        _hint(box, "Not changed: the blueprint keeps the picture it has.")
    row = box.row(align=True)
    row.enabled = editable
    row.operator("bz.blueprint_icon_from_view", text="Icon from this view", icon="RESTRICT_RENDER_OFF")
    if img is not None:
        row.operator("bz.blueprint_icon_clear", text="", icon="X")
    _hint(box, "Frame the model in the 3D view, then press the button: the middle of the view becomes the 64 x 64 picture the blueprint "
               "holds (the game's lists and info boxes draw it).")


def _draw_status(layout, ent, bp, path) -> None:
    box = layout.box()
    sub = subtype_of(bp)
    if bp.state in ("FILE", "NEW", "LEGACY", "EMBEDDED"):
        box.label(text=SUBTYPE_WORDS.get(sub, sub), icon=SUBTYPE_ICONS.get(sub, "OBJECT_DATA"))
    box.label(text=f"File: {os.path.basename(bp.source_path)}" if bp.source_path else "A new blueprint, not written yet", icon="FILE")
    _hint(box, game_dir_words())                          # where the gameplay numbers and the game's own models are read from
    if bp.state in ("LEGACY", "EMBEDDED", "PROBLEM", "NO_LIBRARY"):
        _alert(box, bp.note or "This blueprint cannot be edited.", icon="LOCKED" if bp.state in ("LEGACY", "EMBEDDED") else "ERROR")
    elif bp.state == "NEW":
        _hint(box, "New blueprint: it is written when you export. It cannot play the model's animations.")
    else:
        edited = changed_fields(Values.from_json(bp.baseline), values_from_pg(bp))
        if edited:
            _hint(box, f"Edited: {words(edited)}. Written when you export.")
        else:
            _hint(box, "Not changed since it was loaded.")
    row = box.row(align=True)
    row.operator("bz.blueprint_reset", text="Reset", icon="LOOP_BACK")


class BZ_PT_blueprint(bpy.types.Panel):
    bl_label = "Blueprint"
    bl_idname = "BZ_PT_blueprint"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_order = 10

    @classmethod
    def poll(cls, context):
        ent = entity_of(context.active_object)
        return ent is not None and _stamp(ent, "bz_project") != "EFFECT"      # a1fx: an effect mesh has no blueprint

    def draw(self, context):
        draw_blueprint(self.layout, context.active_object)


class BZ_PT_blueprint_stats(bpy.types.Panel):
    bl_label = "Gameplay numbers"
    bl_idname = "BZ_PT_blueprint_stats"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_GAME
    bl_parent_id = "BZ_PT_blueprint"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        ent = entity_of(context.active_object)
        bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
        return bp is not None and bp.loaded and bp.state in ("FILE", "NEW", "LEGACY", "EMBEDDED")

    def draw(self, context):
        draw_stats(self.layout, context.active_object)


def draw_stats(layout, obj) -> None:
    ent = entity_of(obj)
    bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
    if bp is None or not bp.loaded:
        return
    _hint(layout, "The numbers the game gives this thing: hit points, speed, cost and so on. Changing them changes the game for every player.")
    if bp.stats_game and bp.stats_game != game_dir_words():      # the Blueprint panel above says which game folder is in use now
        then = bp.stats_game if bp.stats_game.endswith((".", "?", "!")) else bp.stats_game + "."
        _hint(layout, f"Read from another game folder. {then} Change the kind, or press Reset (this throws away unexported edits), to read them again.")
    if bp.stats_source:
        _hint(layout, bp.stats_source)
    if bp.stats_note:
        _hint(layout, bp.stats_note)
    from . import ops_entity_stats as ES                                  # r2stats: the numbers can be edited
    ES.draw_edit(layout, bp, bp.state in EDITABLE, ent)


# ---------------------------------------------------------------------------------------------------------------- registration
CLASSES = (BZ_PG_blueprint_sound, BZ_PG_blueprint_stat, BZ_PG_blueprint,
           BZ_OT_blueprint_load, BZ_OT_blueprint_reset, BZ_OT_blueprint_sound_add, BZ_OT_blueprint_sound_remove, BZ_OT_export_entity_folder,
           BZ_PT_blueprint, BZ_PT_blueprint_stats)

_OWN: list = []                   # the classes register() had to register itself (a registry that registered them already is left alone)


def _rna_id(cls) -> str:
    ident = getattr(cls, "bl_idname", "") or cls.__name__
    if issubclass(cls, bpy.types.Operator):
        head, _, tail = ident.partition(".")
        return f"{head.upper()}_OT_{tail}"
    if issubclass(cls, bpy.types.PropertyGroup):
        return cls.__name__
    return ident


def _is_registered(cls) -> bool:
    for base in (bpy.types.Operator, bpy.types.Panel, bpy.types.PropertyGroup):
        if issubclass(cls, base):
            return base.bl_rna_get_subclass_py(_rna_id(cls)) is cls
    return False


def register():
    """Register the classes that are not registered yet, then point ``Object.bz_blueprint`` at the property group."""
    for cls in CLASSES:
        if not _is_registered(cls):
            bpy.utils.register_class(cls)
            _OWN.append(cls)
    bpy.types.Object.bz_blueprint = PointerProperty(type=BZ_PG_blueprint)


def unregister():
    """Undo :func:`register`: the pointer, then the classes this module registered itself."""
    if hasattr(bpy.types.Object, "bz_blueprint"):
        del bpy.types.Object.bz_blueprint
    while _OWN:
        cls = _OWN.pop()
        if _is_registered(cls):
            bpy.utils.unregister_class(cls)
