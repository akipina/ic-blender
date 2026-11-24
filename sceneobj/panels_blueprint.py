"""blueprint -- the blueprint (.ebp) of a gatherer, character, structure or prop, edited in Blender.  PROOF OF CONCEPT (slice POC-blueprint-panel).

In plain words. Every gatherer, character, structure and prop of Impossible Creatures is a PAIR of files: the model (``.sgm``: shape, skeleton,
animations) and the blueprint (``.ebp``: what kind of thing it is, the name the game shows, how many path cells it blocks, where its picture sits,
which sounds the game loads for it). The add-on already edits the model. This module adds the blueprint:

* a **Blueprint panel** (3D view, SGM tab) for any scene object: name, kind (class), footprint, offset and sounds, each with a plain explanation,
  editable; the gameplay numbers the game would give the thing are listed read-only;
* **Load / Reset / Add Sound / Remove Sound** operators (``bz.blueprint_load``, ``bz.blueprint_reset``, ``bz.blueprint_sound_add``,
  ``bz.blueprint_sound_remove``);
* **Export Entity Folder** (``bz.export_entity_folder``): writes ``<folder>/Data/art/ebps/<game folder>/<name>.sgm`` and ``<name>.ebp`` (all lower case) and a
  ``README.txt`` that says how to put them into a copy of the game that the user owns. The model goes through the add-on's existing scene export, exactly as
  ``bz.export_scene`` writes it; the blueprint goes through ICParse's ``SceneBlueprint``, which re-encodes only the chunks that were edited, so an untouched
  blueprint comes out byte for byte as it went in.

Where the values live. The edited values are a property group on ONE object of the entity (the rig when there is one, else the group empty the parts hang
under, else the first part), so every part of the entity shows and exports the same values. The file the values were read from is remembered with its
SHA-1: the export reads the file again, writes only what differs from it, and refuses when the file changed on disk since it was loaded.

What it needs from ICParse (contract C2, C4, C5 of the plan). ``ICParse.ebp.controllers`` (the class table) is used directly. ``ICParse.ebp.scene``
(``SceneBlueprint``) and ``ICParse.ebp.stats`` are looked up when needed: while one of them is missing the panel says so in plain words, and the export
copies the blueprint file unchanged (it cannot edit what it cannot read).

The game folder (the add-on's Game folder preference, else ``$IC_INSTALL``, else the usual Steam folders: ``sceneobj.prefs.resolve_game_dir``) is where
the gameplay numbers are read from and which stock model a project is a copy of is looked up; the panel says which folder that is, and no export writes
into it. The Game build preference decides which class numbers count as known to the game.

The module sits in ``sceneobj/`` and is registered by the sceneobj registry. It looks its sibling modules up through the add-on's package name and
declares ``CLASSES``, ``register()`` and ``unregister()`` (``register()`` registers whatever of its classes is not registered yet, then points
``Object.bz_blueprint`` at the property group).
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
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)

ORDER = 80                        # a sceneobj module's place in the slice order ("controller_meta"); ignored while this file sits in the add-on's folder

# ---------------------------------------------------------------------------------------------------------------- limits and words
NAME_LIMIT = 255                  # letters: the engine reads the name into a 256-letter buffer (decompiled: ebp-meta 5.2, BlueprintHeader::Load)
SOUND_LIMIT = 259                 # letters: a sound path of 260 or more is a fatal error when the game loads it (decompiled: ebp-meta 7, HandlePIDS)
FOOTPRINT_MAX = 255               # path cells (the brief's range; stock footprints are 0..43)
CLASS_MAX = 65535
STATS_ROWS_MAX = 500

STATES = ("NONE", "FILE", "NEW", "LEGACY", "EMBEDDED", "PROBLEM", "NO_LIBRARY")
EDITABLE = ("FILE", "NEW")        # the states in which the panel's values can be changed and written
FIELDS = ("name", "class", "footprint", "offset", "sounds")
FIELD_WORDS = {"name": "name", "class": "kind (class)", "footprint": "footprint", "offset": "offset", "sounds": "sounds"}

HINT = {
    "name": "The name the game shows for it, on its button and in messages. Text like $41545 is a code the game looks up in its language files; "
            "type plain words to give your own name. At most 255 letters.",
    "class": "Decides what kind of thing the game makes of it: a worker, a building, a plain prop. Changing it changes multiplayer, so every player "
             "needs the same files.",
    "footprint": "How many path cells the building blocks (one cell is 2 game units wide). 0 and 0 means: let the game measure the model. "
                 "Changing it changes multiplayer, so every player needs the same files.",
    "footprint_unused": "The game only reads the footprint of buildings (things with a building site). For this kind of thing it stays in the file "
                        "but is not used.",
    "offset": "Moves the picture of the model against its footprint (Y raises it). Changing it changes multiplayer, so every player needs the "
              "same files.",
    "sounds": "Sound files the game loads before play starts, so they play without a pause. A file the game cannot find is skipped quietly.",
    "folder": "Where the game looks for the files: Data/art/ebps/<this folder>/. Use the folder of the thing you started from.",
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
_FOLDER_GUESS = {"GATHERER": "gatherers", "CHARACTER": "characters", "STRUCTURE": "structures", "PROP": "nature"}
_KIND_OF_PROJECT = ("CHARACTER", "STRUCTURE")     # the scene kinds File > New > Impossible Creatures Project starts (bz_project)


class Refused(Exception):
    """A plain-words reason why something was not done. Raised before anything is written, so the message is all there is to say."""


# ---------------------------------------------------------------------------------------------------------------- reaching the add-on and ICParse
_PKG = __package__ or "blender_native"
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
                         description="A sound file of the game, written like DATA:AUDIO\\SFX\\NAME.PAT. At most 259 letters")


class BZ_PG_blueprint_stat(bpy.types.PropertyGroup):
    """One gameplay number the game would give the thing (read only: listed from the game's stats files)."""
    key: StringProperty()
    value: StringProperty()
    origin: StringProperty()


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
LEGACY_NOTE = ("This blueprint uses the game's oldest file layout (old scenery, map markers and cut-scene props; 34 of the game's 479 blueprints "
               "do). You can look at it here, but not change it yet.")
EMBEDDED_NOTE = ("This blueprint carries its model inside itself instead of naming a separate .sgm file (the creatures and 24 older things, such "
                 "as old scenery and map markers, do). You can look at it here, but not change it yet.")


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


def refresh_stats(bp, ent) -> None:
    """List the gameplay numbers the game would give this thing (read only). They come from the thing's own stats file when the game has one under
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
    for key in sorted(view.attrs)[:STATS_ROWS_MAX]:
        row = bp.stats.add()
        row.key = str(key)
        v = view.attrs[key]
        row.value = f"{v:g}" if isinstance(v, (int, float)) else str(v)
        row.origin = os.path.basename(str(view.origin.get(key, ref)).replace("\\", "/"))
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


def load_blueprint(ent, *, create_default: bool = False) -> str:
    """Read the entity's blueprint file into the panel's values (or start a new blueprint when there is none and ``create_default``). Returns one
    plain line for the user; raises :class:`Refused` when there is nothing to load."""
    bp = ent.holder.bz_blueprint
    path = blueprint_path(ent)
    if path is None and not create_default:
        raise Refused("There is no blueprint (.ebp) file beside the model. Use 'Start a New Blueprint' to make one.")
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
            refresh_stats(bp, ent)
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
    if state == "NEW":
        bad = problems_of(wanted)
        if bad:
            raise Refused("The blueprint cannot be written yet. " + " ".join(bad))
        sb = lib.new_scene_ebp(name=wanted.name, defstats_class=wanted.class_id, footprint=wanted.footprint, offset=wanted.offset,
                               sounds=list(wanted.sounds))
        return sb.to_bytes(), list(FIELDS), ("a new blueprint (it has no motion tree and no events, so the game will not play the model's "
                                             "animations with it)")
    data = source_bytes()
    try:
        sb = lib.SceneBlueprint.read(data)
        current = values_of(sb)
    except Exception as e:                                                # noqa: BLE001 -- the reader's words, after a plain lead-in
        raise Refused(f"The blueprint file could not be read: it is damaged or is not a blueprint file. (Details: {e})") from None
    changes = changed_fields(current, wanted)
    if not changes:
        return data, [], "copied unchanged from the original"
    bad = problems_of(wanted, changes)
    if bad:
        raise Refused("The blueprint cannot be written yet. " + " ".join(bad))
    try:
        if "name" in changes or "class" in changes:
            kw = {}
            if "name" in changes:
                kw["name"] = wanted.name
            if "class" in changes:
                kw["defstats_class"] = wanted.class_id
            sb.set_header(**kw)
        if "footprint" in changes or "offset" in changes:
            sb.set_build(footprint=wanted.footprint if "footprint" in changes else None, offset=wanted.offset if "offset" in changes else None)
        if "sounds" in changes:
            sb.set_sounds(list(wanted.sounds))
    except ValueError as e:                                               # the library says in plain words why a value cannot be stored
        raise Refused(f"The blueprint cannot be changed: {e}") from None
    out = sb.to_bytes()
    left = changed_fields(values_of(lib.SceneBlueprint.read(out)), wanted)       # read back what was written: no silent difference
    if left:
        raise Refused(f"The blueprint was built, but reading it back does not give the {words(left)} you set. Nothing was written.")
    return out, changes, f"changed: {words(changes)}; everything else copied unchanged from the original"


def scene_bytes(context, ent, legacy_lod: bool = True, drop_morphs: bool = False):
    """(the .sgm bytes, parts rebuilt, parts kept, notes): exactly what ``bz.export_scene`` writes (geometry of every edited part, plus the rig's clips
    when there is a rig; ``bz.export_scene_geometry`` when there is none). Raises :class:`Refused` with the exporter's own reason. ``drop_morphs``:
    an edited part with damage states is written without them instead of refused (the option "Drop the damage states of edited parts")."""
    scene, actions, core = _addon("scene"), _addon("actions"), _addon("core")
    from ICParse.sgm.scene import model as SM
    obj = ent.parts[0]
    try:
        geo = scene.export_scene_object(obj, legacy=legacy_lod, drop_morphs=drop_morphs)
        out = geo.data
        if ent.rig is not None:
            unchanged, encode, drop = actions.plan_export(ent.rig, renames=False)
            if encode or drop:
                data = core.load_scene(obj.get("bz_source", ""))
                clips = actions.encode_scene_actions(ent.rig, data, encode, context.scene.render.fps)
                out = SM.write_scene_animations(geo.data, clips, drop)
    except ValueError as e:
        raise Refused(str(e)) from None
    return out, list(geo.rebuilt), list(geo.kept), list(geo.notes)


@dataclass
class EntityExport:
    root: str                 # the folder written into
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


def readme_text(root: str, name: str, folder: str, rebuilt, kept, blueprint_note: str, notes, hard_bound: bool) -> str:
    """The README.txt written beside Data/: plain words, how to try the files in a copy of the game the user owns."""
    files = []
    for dirpath, _dirs, names in os.walk(os.path.join(root, "Data")):
        for fn in sorted(names):
            files.append(os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/"))
    rel = f"{folder + '/' if folder else ''}{name}"
    if hard_bound:
        naming = (f"The game looks up art/ebps/{rel} by name, so these files REPLACE the original of that name. That is how a changed {name} "
                  f"reaches the game.")
    else:
        naming = (f"'{rel}' is a name of your own: the game only shows it if a map or a script asks for it. To REPLACE a thing of the game instead, "
                  f"export with exactly its folder and name.")
    lines = [
        "HOW TO TRY THESE FILES IN THE GAME",
        "==================================",
        "",
        "This folder was written by the Impossible Creatures add-on for Blender (Export Entity Folder).",
        "A thing in the game is two files that belong together, and both are in the Data folder here:",
        "",
        f"  Data/art/ebps/{rel}.sgm   the model: its shape, skeleton and animations",
        f"  Data/art/ebps/{rel}.ebp   the blueprint: its name, kind, footprint, offset and sounds",
        "",
        "Everything in the Data folder of this folder:",
        *[f"  {f}" for f in files],
        "",
        "What to do",
        "1. Work in a copy of the game that you own. Do NOT put these files into the Steam copy of the game: Steam can overwrite or",
        "   delete files that are not its own.",
        "2. Copy the Data folder from here into the main folder of your copy of the game (the one that already has a Data folder) and let",
        "   your computer merge the two folders.",
        "3. Start the game from your copy. The game reads loose files in its Data folder before the files packed inside its .sga archives,",
        "   so these files are used instead of a packed file with the same path (the game's own Workshop installer delivers files this way).",
        "4. To undo it, delete the two files again.",
        "",
        "About this export",
        f"- {naming}",
        f"- Model: {len(rebuilt)} part(s) rebuilt from your edits, {len(kept)} part(s) left exactly as they were; the animations follow your Actions.",
        f"- Blueprint: {blueprint_note}.",
        "- Several players: the kind (class), the footprint, the offset and the size of the model are compared between players when a",
        "  multiplayer game starts. Every player needs these same files, or the game will not let them play together.",
        "- Not in this folder: textures (the model uses the ones the game already has) and the stats file (.lua). Without a .lua of the same",
        "  name beside the blueprint, the game uses the default numbers for its kind of thing.",
        "- None of this has been tried in the game yet. If something does not show up or the game complains, the file warnings.log in",
        "  the game folder says why.",
    ]
    if notes:
        lines += ["", "Notes from the export"] + [f"- {n}" for n in notes[:12]]
    return "\n".join(lines) + "\n"


def export_entity(context, obj, directory, *, name=None, folder=None, legacy_lod: bool = True, drop_morphs: bool = False) -> EntityExport:
    """Write ``<directory>/Data/art/ebps/<folder>/<name>.sgm`` and ``.ebp`` (lower case) and ``<directory>/README.txt``. Everything is worked out before
    the first file is written, so a refusal leaves the folder as it was. Raises :class:`Refused` with a plain reason."""
    ent = entity_of(obj)
    if ent is None:
        raise Refused("Select a gatherer, character, structure or prop that was imported or started with this add-on.")
    bp = getattr(ent.holder, "bz_blueprint", None)
    if bp is None:
        raise Refused("The blueprint tools are not switched on in this session.")
    root = check_target(directory)
    if not bp.loaded:
        load_blueprint(ent)                                               # an untouched export needs no visit to the panel; raises when there is no .ebp
    if bp.state == "NONE":
        raise Refused("There is no blueprint (.ebp) file for this model. Use 'Start a New Blueprint' in the Blueprint panel first.")
    out_name = clean_name(name if name else default_name(ent))
    out_folder = clean_folder(bp.folder if folder is None else folder)
    ebp_data, changes, bp_note = blueprint_bytes(ent, bp)
    sgm_data, rebuilt, kept, notes = scene_bytes(context, ent, legacy_lod, drop_morphs)
    ctl = controllers()
    hard_bound = bool(ctl.is_hard_bound(out_folder, out_name))
    data_dir = os.path.join(root, "Data", "art", "ebps", *([p for p in out_folder.split("/") if p]))
    sgm_path, ebp_path = os.path.join(data_dir, out_name + ".sgm"), os.path.join(data_dir, out_name + ".ebp")
    try:
        write_file(sgm_path, sgm_data)
        write_file(ebp_path, ebp_data)
        write_file(os.path.join(root, "README.txt"),
                   readme_text(root, out_name, out_folder, rebuilt, kept, bp_note, notes, hard_bound).encode("utf-8"))
    except OSError as e:
        raise Refused(f"The files could not be written into '{root}': {e.strerror or e}. Some of them may be there already.") from None
    return EntityExport(root, sgm_path, ebp_path, os.path.join(root, "README.txt"), out_name, out_folder, rebuilt, kept, changes, bp_note, notes)


# ---------------------------------------------------------------------------------------------------------------- operators
def _entity_poll(context):
    ent = entity_of(context.active_object)
    return ent is not None and getattr(ent.holder, "bz_blueprint", None) is not None


def _editable_poll(context):
    ent = entity_of(context.active_object)
    bp = getattr(ent.holder, "bz_blueprint", None) if ent is not None else None
    return bp is not None and bp.loaded and bp.state in EDITABLE


class BZ_OT_blueprint_load(bpy.types.Operator):
    """Read the blueprint (.ebp) beside the model into the panel, so its name, kind, footprint, offset and sounds can be seen and changed"""
    bl_idname = "bz.blueprint_load"
    bl_label = "Load Blueprint"
    bl_options = {"REGISTER", "UNDO"}

    create_default: BoolProperty(name="Start a new one", default=False,
                                 description="When there is no blueprint file beside the model, start a new one (it is written when you export)")

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
    """Throw away the changes in the panel and read the blueprint again from its file (a new blueprint goes back to its starting values)"""
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


class BZ_OT_export_entity_folder(bpy.types.Operator):
    """Write the model (.sgm) and the blueprint (.ebp) into a folder you choose, as Data/art/ebps/<folder>/<name>.sgm and .ebp, with a README that says
    how to put them into a copy of the game that you own"""
    bl_idname = "bz.export_entity_folder"
    bl_label = "Export Entity Folder"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="Folder", subtype="DIR_PATH", description="The folder to write into (not the game's own folder)")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})
    entity_name: StringProperty(name="File name", default="", options={"SKIP_SAVE"},
                                description="The name of the two files, without an ending. Empty: the project's name")
    folder: StringProperty(name="Folder in the game", default="", options={"SKIP_SAVE"}, description=HINT["folder"])
    drop_damage_states: BoolProperty(
        name="Drop the damage states of edited parts",
        description="A part with damage states (stored shapes the game blends in: a building's damage, a character's face) cannot be "
                    "edited yet without breaking them, so exporting an edited one is refused. ON: write such an edited part without its "
                    "damage states (the export says how many it dropped). Parts you did not edit keep theirs byte for byte",
        default=False)

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
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "entity_name")
        col.prop(self, "folder")
        _hint(col, HINT["folder"])
        name, folder = clean_name(self.entity_name or "name"), clean_folder(self.folder)
        col.label(text=f"Data/art/ebps/{folder + '/' if folder else ''}{name}.sgm / .ebp")
        try:
            if controllers().is_hard_bound(folder, name):
                _hint(col, "The game looks this one up by name: these files will REPLACE the original of that name.")
        except Exception:                                                 # noqa: BLE001
            pass
        col.separator()
        col.prop(self, "drop_damage_states")

    def execute(self, context):
        try:
            res = export_entity(context, context.active_object, self.directory, name=self.entity_name or None,
                                folder=self.folder if self.folder.strip() else None, drop_morphs=self.drop_damage_states)
        except Refused as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:                                            # noqa: BLE001 -- surface anything else as a message, like Export Scene Object
            self.report({"ERROR"}, f"Export failed: {e}")
            return {"CANCELLED"}
        for n in res.notes[:4]:
            self.report({"WARNING"}, n)
        self.report({"INFO"}, f"Wrote {os.path.basename(res.sgm)} and {os.path.basename(res.ebp)} under {os.path.join(res.root, 'Data')}; "
                              f"blueprint {res.blueprint_note}; model: {len(res.rebuilt)} part(s) rebuilt, {len(res.kept)} unchanged. README.txt says "
                              f"how to use them.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------------------------- the panel
def _hint(layout, text: str, width: int = 46) -> None:
    """A small grey explanation under a field."""
    col = layout.column(align=True)
    col.scale_y = 0.85
    for line in wrap(text, width):
        col.label(text=line)


def _alert(layout, text: str, icon: str = "ERROR", width: int = 46) -> None:
    col = layout.column(align=True)
    col.alert = True
    for i, line in enumerate(wrap(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


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
            box.label(text=f"Blueprint file: {os.path.basename(path)}", icon="FILE")
            _hint(box, "The blueprint says what kind of thing this is, its name, footprint, offset and sounds. Load it to see and change them.")
            box.operator("bz.blueprint_load", text="Load Blueprint", icon="FILE_REFRESH")
        else:
            box.label(text="No blueprint file beside the model.", icon="INFO")
            _hint(box, "The game needs a blueprint next to every model. You can start a new one here; it is written when you export.")
            box.operator("bz.blueprint_load", text="Start a New Blueprint", icon="ADD").create_default = True
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

    box = layout.box()
    box.label(text="Where the game finds it", icon="FILE_FOLDER")
    col = box.column()
    col.enabled = editable
    col.prop(bp, "folder", text="Folder")
    _hint(box, HINT["folder"] + " " + FOLDER_SOURCE_WORDS.get(bp.folder_source, ""))
    out_name = default_name(ent)
    try:
        if controllers().is_hard_bound(clean_folder(bp.folder), out_name):
            _hint(box, "The game looks this one up by name, so a changed copy has to keep this folder and name to replace the original.")
    except Exception:                                                     # noqa: BLE001
        pass

    box = layout.box()
    box.label(text="Export", icon="EXPORT")
    box.operator("bz.export_entity_folder", text="Export Entity Folder...", icon="EXPORT")
    _hint(box, "Writes the model (.sgm) and the blueprint (.ebp) into a folder you choose, ready to copy into a copy of the game that you own. "
               "A README comes with them.")


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
        _hint(box, "New blueprint: it is written when you export. It has no motion tree and no events, so the game will not play the model's "
                   "animations with it.")
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
    bl_category = "SGM"

    @classmethod
    def poll(cls, context):
        return entity_of(context.active_object) is not None

    def draw(self, context):
        draw_blueprint(self.layout, context.active_object)


class BZ_PT_blueprint_stats(bpy.types.Panel):
    bl_label = "Gameplay numbers (read only)"
    bl_idname = "BZ_PT_blueprint_stats"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"
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
    _hint(layout, "The numbers the game would give this thing: speed, health, cost and so on. They are listed from the game's stats files and "
                  "cannot be changed here. Changing them changes multiplayer.")
    if bp.stats_game and bp.stats_game != game_dir_words():      # the Blueprint panel above says which game folder is in use now
        then = bp.stats_game if bp.stats_game.endswith((".", "?", "!")) else bp.stats_game + "."
        _hint(layout, f"These numbers were read from another game folder than the one in use now. {then} They are read again when the "
                      f"kind or the folder is changed, or the blueprint is loaded again.")
    if bp.stats_source:
        _hint(layout, bp.stats_source)
    if bp.stats_note:
        _hint(layout, bp.stats_note)
    if not len(bp.stats):
        return
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=True)
    for row in bp.stats:
        grid.label(text=row.key)
        grid.label(text=row.value)


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
