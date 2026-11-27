"""New Project: File > New > Impossible Creatures Project...

One front door for starting work on every kind of .sgm the game has, so that what a project IS decides what the add-on does with it:

* **Creature** -- the default skeleton (ICParse's canonical 48 bones) with a connected scaffold body, as New Creature builds it,
  or a copy of one of **the game's own animals** (Start from: the base game's 61, the expansions' and the installed mods';
  ``ICParse.sgm.creature.stock_donor``): the model is written as ``<name>.sgm`` and the animal's blueprint, stats and combiner rules
  as ``<name>.ebp`` / ``.lua`` / ``.lsc`` beside it, and the creature is stamped ``bz_stock_donor`` / ``bz_stock_archive``.
  Animations: the template's own, none, or **borrowed from existing creatures** (:func:`borrow_clips`).
* **Gatherer / character** and **Structure / prop** -- SCENE objects: another file layout (triangle meshes with stored normals,
  materials, a bounding volume, rigid-part or skinned binding) and another controller (.ebp). The engine cannot load a scene object
  built from nothing, so a project starts from a stock DONOR picked from the game's own archives -- its skeleton, materials, bounding
  volume, controller and clips -- the same morph-a-real-asset rule ICTools/assetconverter follows. The donor's files are copied
  next to the project's name; geometry is then edited and exported through the scene exporters.

The project's .sgm is the object's *source*, exactly as for an imported file, so editing, Validate, animation Actions and Export all work
unchanged; the subtype is simply whatever the file is (scene objects carry ``bz_scene``, creatures are native patch objects).
"""
from __future__ import annotations

import glob
import os
import re
import textwrap

import numpy as np

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import actions as bzactions
from . import core, enumitems, rig, store

KINDS = (
    ("CREATURE", "Creature", "A creature: the default skeleton with a simple connected starting body made of patches", "OUTLINER_OB_ARMATURE", 0),
    ("CHARACTER", "Gatherer / character", "A skinned model on a skeleton, like the henchmen and story characters", "ARMATURE_DATA", 1),
    ("STRUCTURE", "Structure / prop", "A building, prop or scenery made of rigid parts on bones", "HOME", 2),
    ("PROJECTILE", "Projectile", "Something the game throws or fires (a rock, a quill, a spit): a small model with a blueprint, in art/ebps/projectiles",
     "FORCE_FORCE", 3),
    ("EFFECT", "Effect model", "A small mesh an effect draws (a rock, a shard, a ring): a model with no blueprint, in art/fx", "PARTICLES", 4),
)
_NAMES = {"CREATURE": "NewCreature", "CHARACTER": "NewGatherer", "STRUCTURE": "NewStructure", "PROJECTILE": "NewProjectile", "EFFECT": "NewEffectModel"}
# where the game keeps each kind of scene object, in its archives. A projectile is an ordinary small entity (art/ebps/projectiles: a model, a
# blueprint and a stats .lua); an effect mesh is only a model that an effect's "Mesh" names (art/fx: 145 of them, no blueprint).
_DONOR_DIRS = {"CHARACTER": ("art/ebps/gatherers/", "art/ebps/characters/"), "STRUCTURE": ("art/ebps/structures/", "art/ebps/nature/"),
               "PROJECTILE": ("art/ebps/projectiles/",), "EFFECT": ("art/fx/",)}

_BUILT: dict = {}                 # (kind, the list of donors) -> the dropdown's items for it, built once (enumitems)
_ARCHIVES: dict = {}              # path -> (mtime, Sga)
_DONORS: dict = {}                # kind -> (archive mtimes, [(archive, member)])


def _clean_name(text: str, fallback: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (text or "").strip()) or fallback


# ---- the game's own files ----------------------------------------------------------------------------------------------------------
def game_dir(context=None) -> tuple:
    """``(folder or None, source)`` of the game: the add-on's Game folder preference, else ``$IC_INSTALL``, else the usual Steam folders
    (``sceneobj.prefs.resolve_game_dir``). Should the preferences module be missing, ICParse's own choice ($IC_INSTALL, else Steam)."""
    try:
        from .sceneobj import prefs
        return prefs.resolve_game_dir(context)
    except Exception:                                                     # noqa: BLE001 -- the environment and Steam folders still count
        from ICParse.data import default_root
        root = default_root()
        return (root, "steam default") if root and os.path.isdir(root) else (None, "none")


def game_dir_line(context=None) -> str:
    """One plain line for the dialog: which game folder the models are listed from, and why that one."""
    try:
        from .sceneobj import prefs
        return prefs.game_dir_line(context)
    except Exception:                                                     # noqa: BLE001
        folder = game_dir(context)[0]
        return f"Game folder: {folder}" if folder else "No game folder found: set the IC_INSTALL environment variable."


# The game's own archives (the Steam release: base game, Insect Invasion, Tellurian, Dino Dawn). Every other .sga in the game folder
# belongs to a mod (a SonicMod_tails.sga, ...): its models are not the game's and are not offered as starting points.
STOCK_ARCHIVES = ("engine.sga", "icdata.sga", "insectdata.sga", "telluriandata.sga", "dinodawndata.sga")


def game_archives(context=None, stock_only: bool = True) -> list:
    """The game's own .sga archives in the game folder (:func:`game_dir`: the Game folder preference, else ``$IC_INSTALL``, else the usual
    Steam folders), base game first. ``stock_only=False`` adds every other .sga there (mods' archives)."""
    root = game_dir(context)[0]
    if not root:
        return []
    found = enumitems.recent(("sga", root), lambda: glob.glob(os.path.join(root, "*.sga")) if os.path.isdir(root) else [])
    if stock_only:
        found = [p for p in found if os.path.basename(p).lower() in STOCK_ARCHIVES]
    first = [p for p in found if os.path.basename(p).lower() in ("engine.sga", "icdata.sga")]
    rest = [p for p in found if p not in first]
    return sorted(first) + sorted(rest)


def _archive(path: str):
    import ICParse.sga as sga
    m = os.path.getmtime(path)
    hit = _ARCHIVES.get(path)
    if hit is None or hit[0] != m:
        _ARCHIVES[path] = (m, sga.read(path))
    return _ARCHIVES[path][1]


def scene_donors(kind: str, context=None) -> list:
    """[(archive path, member path)] of the stock scene objects of this kind in the game folder (:func:`game_dir`), sorted by name; the
    first archive to hold a path wins. The dialog asks on every redraw, so the answer is kept until the game folder or one of its archives
    changes."""
    archives = game_archives(context)
    stamp = enumitems.recent(("dates", tuple(archives)), lambda: tuple((a, os.path.getmtime(a)) for a in archives if os.path.isfile(a)))
    hit = _DONORS.get(kind)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    seen, out = set(), []
    dirs = _DONOR_DIRS.get(kind, ())
    for ap in archives:
        try:
            arc = _archive(ap)
            members = [getattr(e, "path", str(e)) for e in arc.entries]
        except Exception:
            continue
        for m in members:
            low = m.replace("\\", "/").lower()
            if low.endswith(".sgm") and any(low.startswith(d) for d in dirs) and low not in seen:
                seen.add(low)
                out.append((ap, low))
    out = sorted(out, key=lambda x: x[1].rsplit("/", 1)[-1])
    _DONORS[kind] = (stamp, out)
    return out


_CREATURE_DONORS: dict = {}       # game folder -> (archive stamps, [StockDonor])


def creature_donors(context=None) -> list:
    """The game's own animals a creature project can start from (:func:`ICParse.sgm.creature.stock_donor.list_donors`): the base
    game's, the expansions' and the installed mods', from the game folder (:func:`game_dir`). Kept until an archive changes."""
    from ICParse.sgm.creature import stock_donor
    root = game_dir(context)[0]
    if not root:
        return []

    def look():
        return tuple((a, os.path.getmtime(a)) for a in stock_donor.archives_in(root, mods=True) if os.path.isfile(a))
    stamp = enumitems.recent(("creatures", root), look)
    hit = _CREATURE_DONORS.get(root)
    if hit is None or hit[0] != stamp:
        hit = (stamp, stock_donor.list_donors(root, mods=True))
        _CREATURE_DONORS[root] = hit
    return hit[1]


def read_donor(archive: str, member: str):
    """(sgm bytes, its sibling .ebp bytes or None) of a stock scene object: ``archive`` is one of the game folder's archives, as
    :func:`scene_donors` lists them."""
    arc = _archive(archive)
    sgm = arc.read_path(member)
    ebp_member = member[:-4] + ".ebp"
    try:
        ebp = arc.read_path(ebp_member)
    except Exception:
        ebp = None
    return sgm, ebp


def _label(member: str) -> str:
    stem = member.rsplit("/", 1)[-1][:-4]
    where = member.split("/")[2] if member.count("/") >= 3 else ""
    if member.startswith("art/fx/"):                                       # an effect mesh: show the effect set it belongs to
        where = member[len("art/fx/"):].rpartition("/")[0] or "art/fx"
    return f"{stem.replace('_', ' ').title()}  ({where})"


def _donor_items(self, context):
    """The Start from dropdown. Blender asks for it once per entry of the open menu and several times per redraw, so the items are
    built once for a list of donors (:func:`creature_donors` and :func:`scene_donors` return the same list until the game folder
    changes) and that same list is returned from then on."""
    kind = self.project_type
    donors = creature_donors(context) if kind == "CREATURE" else scene_donors(kind, context)
    hit = _BUILT.get(kind)
    if hit is not None and hit[0] is donors:
        return hit[1]
    items = []
    if kind == "CREATURE":
        items.append(("DEFAULT", "Default skeleton", "The standard 48-bone creature skeleton with a simple connected starting body",
                      "OUTLINER_OB_ARMATURE", 0))
        for i, d in enumerate(donors):
            items.append((d.key, d.label, f"A copy of the game's {d.name.replace('_', ' ')} ({d.archive_name}): its model, blueprint, "
                                          "stats and combiner rules, under your creature's name", "", i + 1))
    else:
        for i, (ap, member) in enumerate(donors):
            items.append((member, _label(member), f"{os.path.basename(ap)}: {member}", "", i + 1))
        items.append(("FILE", "Another .sgm file...", "Start from a .sgm file on disk whose kind matches the Type above", "FILEBROWSER", 0))
        if len(items) == 1:
            items[0] = ("FILE", "Another .sgm file... (no game folder found: see the add-on's preferences)",
                        "No game folder found: set it in the add-on's preferences or IC_INSTALL", "FILEBROWSER", 0)
    _BUILT[kind] = (donors, enumitems.kept(items))
    return _BUILT[kind][1]


def _stock_creature_dir() -> str:
    try:
        from . import combocore, combopreview
        return combocore.stock_dir("", [d for d in (combopreview.user_stock_dir(),) if d]) or ""
    except Exception:
        return ""


def _borrow_items(self, context):
    def look():
        d = _stock_creature_dir()
        return d, tuple(sorted(x for x in os.listdir(d) if x.lower().endswith(".sgm"))) if d else ()
    d, files = enumitems.recent("borrow", look)
    items = [(os.path.join(d, f), f[:-4].replace("_", " ").title(), os.path.join(d, f), "", i + 1) for i, f in enumerate(files)]
    items.append(("FILE", "Another creature .sgm file...", "Pick any creature file on disk", "FILEBROWSER", 0))
    return enumitems.kept(items)


# ---- animations ---------------------------------------------------------------------------------------------------------------------
def _skeleton(model):
    names = [b.name or f"bone{i}" for i, b in enumerate(model.bones)]
    parents = [None if (b.parent is None or int(b.parent) < 0) else int(b.parent) for b in model.bones]
    return names, parents


def borrow_clips(arm, target_model, donor_model, only=None, as_name=None) -> dict:
    """Give the rig the donor creature's clips, as new Actions (and NLA strips), matched BY BONE NAME.

    Each bone the two skeletons share gets the donor's pose in the bone's own frame (the matrix_basis the donor's clip gives it,
    exactly what the Action Editor shows), its translation scaled by the ratio of the two bones' lengths; a bone the donor lacks
    stays at its bind pose. This is faithful when the skeletons share their bone names AND axes (every creature built on the
    standard skeleton does); a different skeleton moves in the donor's directions, not its own, and the report says how many bones
    matched. ``as_name``: for ONE clip, the name it gets on this rig (a donor's Walk next to a Walk of your own). Returns {clips, matched,
    unmatched_target, unmatched_donor, skipped}."""
    from ICParse.codecs import reconstruct as R
    t_names = rig.current_bone_names(arm, target_model)
    _, t_par = _skeleton(target_model)
    d_names, d_par = _skeleton(donor_model)
    d_index = {n.lower(): i for i, n in enumerate(d_names)}
    match = {ti: d_index[n.lower()] for ti, n in enumerate(t_names) if n.lower() in d_index}
    t_bw, d_bw = R.bind_world_matrices(target_model), R.bind_world_matrices(donor_model)

    def local_len(bw, par):
        Rm = [rig.to_blender_matrix(np.asarray(m, dtype=np.float64)) for m in bw]
        out = []
        for i, p in enumerate(par):
            L = Rm[i] if p is None else Rm[p].inverted_safe() @ Rm[i]
            out.append(float(L.translation.length))
        return out
    lt, ld = local_len(t_bw, t_par), local_len(d_bw, d_par)
    ratios = {ti: lt[ti] / ld[di] for ti, di in match.items() if lt[ti] > 1e-6 and ld[di] > 1e-6}
    overall = float(np.median(list(ratios.values()))) if ratios else 1.0
    scale = {ti: float(min(5.0, max(0.2, ratios.get(ti, overall)))) for ti in match}

    have = {str(a.get(bzactions.KEY_CLIP, "")).lower() for a in bzactions.actions_of(arm)} | {a.name.lower() for a in bzactions.actions_of(arm)}
    # (a clip borrowed earlier has no source-clip stamp: its Action's name is what says the rig has it already)
    wanted = {s.strip().lower() for s in (only or []) if s.strip()}
    made, skipped = [], []
    for ai, anim in enumerate(donor_model.animations):
        name = anim.name or f"clip{ai}"
        if wanted and name.lower() not in wanted:
            continue
        name = as_name or name
        if name.lower() in have:
            skipped.append(name)
            continue
        K = max(1, int(getattr(anim, "frame_count", 0) or 11))
        frames = [R.animated_bone_world(donor_model, anim, f / K) for f in range(K + 1)]
        sd = bzactions.basis_samples(d_names, d_par, d_bw, frames)
        st = np.zeros((K + 1, len(t_names), 10), dtype=np.float64)
        st[:, :, 3] = 1.0
        st[:, :, 7:10] = 1.0
        for ti, di in match.items():
            st[:, ti, :] = sd[:, di, :]
            st[:, ti, 0:3] *= scale[ti]
        # no clip_name: a borrowed clip is a NEW Action (written under its own name), not one of the source file's clips (which Export
        # would keep byte for byte -- the source has no such clip)
        act = bzactions.make_action(arm, name, t_names, st, 1, float(getattr(anim, "fps", 30.0) or 30.0), clip_name=None)
        bzactions.stash(arm, act, act.name, 1)
        made.append((name, act.name))
    current = next((list(o.get("bz_anims", [])) for o in bpy.data.objects if o.get("bz_rig") == arm.name), [])
    _set_anims(arm, current + [c for c, _ in made])        # the Load Animation menu; the source-clip table (KEY_CLIPS) stays the file's
    return {"clips": [c for c, _ in made], "matched": len(match), "unmatched_target": len(t_names) - len(match),
            "unmatched_donor": len(d_names) - len(set(match.values())), "skipped": skipped}


def _set_anims(arm, names) -> None:
    """The clip menu (Load Animation) of every object driven by this rig."""
    for o in bpy.data.objects:
        if o.get("bz_rig") == arm.name:
            o["bz_anims"] = list(names)


def drop_all_clips(arm) -> int:
    """Remove every clip Action of the rig (and its NLA strips): a project that wants no animations, or its own."""
    n = 0
    ad = arm.animation_data
    for act in list(bzactions.actions_of(arm)):
        if act.get(bzactions.KEY_CLIP) is None:
            continue
        if ad is not None:
            for trk in list(ad.nla_tracks):
                if any(s.action is act for s in trk.strips):
                    ad.nla_tracks.remove(trk)
            if ad.action is act:
                ad.action = None
        act.use_fake_user = False
        bpy.data.actions.remove(act)
        n += 1
    # KEY_CLIPS (which source clips had Actions) stays: it is how Export knows these source clips are now deleted, and drops them
    _set_anims(arm, [])
    return n


# ---- the operator ----------------------------------------------------------------------------------------------------------------------
_NOTE_WIDTH = 34                    # characters of a note line that fit the file window's options column
_OWN_ARCHIVES = ("engine.sga", "icdata.sga", "insectdata.sga")     # the archives the hub's stock index (and Export for the Game) knows


def _donor_of(key: str, context):
    """The game animal with this dropdown key, or None (asked on every redraw: a dictionary per list of donors)."""
    donors = creature_donors(context)
    hit = _BUILT.get("by key")
    if hit is None or hit[0] is not donors:
        hit = _BUILT["by key"] = (donors, {d.key: d for d in donors})
    return hit[1].get(key)


def replaceable(self, context):
    """The game animal this project would REPLACE (its name), or None: only for a creature started from an animal of the base game's own
    archives (``ICParse.hub.stock.STOCK_ARCHIVES``): the expansions' and the mods' animals are not offered."""
    if self.project_type != "CREATURE":
        return None
    key = self.donor
    if key == "DEFAULT":
        return None
    d = _donor_of(key, context)
    return d.name if d is not None and d.archive_name.lower() in _OWN_ARCHIVES else None


def _replace_items(self, context):
    """Start as a creature of its own (the default: nothing of the game changes unless you say so) or as the replacement of the animal."""
    items = [("NEW", "A new creature of my own", "A copy under a name you choose; the game's own animals stay as they are")]
    try:
        target = replaceable(self, context)
    except Exception:                                          # noqa: BLE001 -- a draw never fails for this
        target = None
    if target:
        items.append(("REPLACE", f"Replace the original {target}",
                      f"Change the game's own {target}: once installed, every game uses your version"))
    return enumitems.kept(items)


def _type_changed(self, context):
    if self.project_name in _NAMES.values():
        self.project_name = _NAMES[self.project_type]
    try:
        self.property_unset("donor")
    except Exception:
        pass


def _remove_files(paths) -> None:
    """Delete these files if they are there (the ones a run wrote itself, after a failure); a file that will not go is left."""
    for f in paths:
        try:
            if os.path.isfile(f):
                os.remove(f)
        except OSError:
            pass


class BZ_OT_new_project(bpy.types.Operator, ExportHelper):
    """Start a new creature, gatherer, character, structure or prop project"""
    bl_idname = "bz.new_project"
    bl_label = "New Impossible Creatures Project"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".sgm"
    filter_glob: StringProperty(default="*.sgm", options={"HIDDEN"})
    project_type: EnumProperty(name="Type", items=KINDS, default="CREATURE", update=_type_changed,
                               description="What kind of .sgm this is: the add-on writes, checks and exports it as that kind")
    project_name: StringProperty(name="Name", default="NewCreature", description="The creature's name: its parts and its texture are named after it")
    donor: EnumProperty(name="Start from", items=_donor_items,
                        description="Start from the default creature skeleton or a copy of a game model")
    donor_file: StringProperty(name="File to start from", subtype="FILE_PATH",
                               description="A model .sgm to start from; its .ebp is copied too")
    replace: EnumProperty(name="As", items=_replace_items, options={"SKIP_SAVE"},
                          description="A creature of your own under a new name, or the game's own animal changed in place")
    anim_source: EnumProperty(name="Animations", default="OWN", items=(
        ("OWN", "Keep its own", "The animations the default skeleton or the game's model already has"),
        ("NONE", "None", "No animations: add your own in the Action Editor"),
        ("BORROW", "Borrow from existing creatures", "Copy the animations of another creature, matched by bone name (creature projects only)")))
    borrow: EnumProperty(name="Borrow from", items=_borrow_items, description="The creature whose animations to copy")
    borrow_file: StringProperty(name="Creature file", subtype="FILE_PATH", description="A creature .sgm file to copy animations from")
    borrow_names: StringProperty(name="Only these animations", default="", description="Animation names, comma separated; empty copies them all")
    detail: IntProperty(name="Detail", default=1, min=1, max=3, description="How finely the starting body is divided: 1 is the simplest")
    with_ik: BoolProperty(name="IK Chains", default=True, description="Add the IK chains the original game's creatures have")

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = _clean_name(self.project_name, "NewProject") + self.filename_ext
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "project_type")
        if self.project_type == "CREATURE":
            col.prop(self, "project_name")
        col.prop(self, "donor")
        if self.donor == "FILE":
            col.prop(self, "donor_file")
        if self.project_type == "CREATURE" and self.donor != "DEFAULT":
            target = replaceable(self, context)
            if target:
                col.prop(self, "replace", expand=True)
            if target and self.replace == "REPLACE":
                sub = col.column(align=True)
                sub.scale_y = 0.85
                text = (f"The files keep the name {target}. Installed, your version replaces the game's {target} in every army; "
                        f"players without your files cannot join your multiplayer games.")
                for i, line in enumerate(textwrap.wrap(text, _NOTE_WIDTH)):
                    sub.label(text=line, icon="INFO" if i == 0 else "BLANK1")
        if self.project_type != "CREATURE" or self.donor != "DEFAULT":      # where the list of the game's own models comes from
            sub = col.column(align=True)
            sub.scale_y = 0.85
            lines = textwrap.wrap(game_dir_line(context), _NOTE_WIDTH, break_long_words=False, break_on_hyphens=False)
            for i, line in enumerate(lines):
                sub.label(text=line, icon="FILE_FOLDER" if i == 0 else "BLANK1")
        col.separator()
        col.prop(self, "anim_source")
        if self.anim_source == "NONE" and self.project_type == "CREATURE" and self.donor != "DEFAULT":
            sub = col.column(align=True)
            sub.scale_y = 0.85
            for i, line in enumerate(textwrap.wrap("The animal's motions play nothing until you add clips with the same names.", _NOTE_WIDTH)):
                sub.label(text=line, icon="ERROR" if i == 0 else "BLANK1")
        if self.anim_source == "BORROW":
            if self.project_type != "CREATURE":
                col.label(text="Borrowing animations works only for creature projects", icon="INFO")
            col.prop(self, "borrow")
            if self.borrow == "FILE":
                col.prop(self, "borrow_file")
            col.prop(self, "borrow_names")
        if self.project_type == "CREATURE" and self.donor == "DEFAULT":
            col.separator()
            col.prop(self, "detail")
            col.prop(self, "with_ik")

    # -- helpers --
    def _write_creature(self, path):
        from ICParse.sgm.creature import scaffold
        from ICParse.sgm import parser as sgm_parser
        name = _clean_name(self.project_name, "NewCreature")
        model = scaffold.build_scaffold_creature(name, detail=int(self.detail), clips=self.anim_source == "OWN", ik=self.with_ik)
        with open(path, "wb") as f:
            f.write(sgm_parser.write(model))
        return name

    def _creature_donor(self, context):
        found = _donor_of(self.donor, context)
        if found is None:
            raise ValueError(f"{self.donor.split('|')[-1]} is not in the game's archives. {game_dir_line(context)}")
        return found

    def _write_stock_creature(self, path, context):
        """A copy of one of the game's animals: ``<stem>.sgm`` (the model), ``.ebp``, ``.lua`` and ``.lsc`` beside it, under the
        file's own name. Returns (the donor, every path written, the .sgm first). Nothing is overwritten."""
        from ICParse.sgm.creature import stock_donor
        donor = self._creature_donor(context)
        folder, stem = os.path.split(path)
        stem = os.path.splitext(stem)[0].lower()
        if self.replace == "REPLACE":
            if replaceable(self, context) != donor.name:
                raise ValueError("Only an animal of the game's own archives can be replaced: choose 'A new creature of my own'")
            stem = donor.name                                  # the original's own name: the game finds it by that name
        else:
            why = stock_donor.name_problem(stem, creature_donors(context))
            if why:
                raise ValueError(why)
        cands = [os.path.join(folder, stem + e) for e in (".sgm", ".ebp", ".lua", ".lsc")]
        before = {c for c in cands if os.path.lexists(c)}      # not ours: whatever happens, these stay
        try:
            written = stock_donor.write_project(donor, folder, stem)
        except FileExistsError as e:
            raise ValueError(f"{os.path.basename(str(e))} is already there: choose another name (nothing was overwritten)") from None
        except Exception:
            _remove_files([c for c in cands if c not in before])    # a half-written project would make a retry say "already there"
            raise
        return donor, written

    def _write_scene(self, path):
        if self.donor == "FILE":
            src = bpy.path.abspath(self.donor_file)
            if not os.path.isfile(src):
                raise ValueError("Choose the .sgm file to start from")
            sgm = open(src, "rb").read()
            ebp = open(src[:-4] + ".ebp", "rb").read() if os.path.isfile(src[:-4] + ".ebp") else None
        else:
            found = [x for x in scene_donors(self.project_type, bpy.context) if x[1] == self.donor]
            if not found:
                raise ValueError(f"{self.donor} is not in the game's archives. {game_dir_line(bpy.context)}")
            sgm, ebp = read_donor(*found[0])
        with open(path, "wb") as f:
            f.write(sgm)
        if ebp is not None:                                   # the controller finds its geometry by its own basename
            with open(path[:-4] + ".ebp", "wb") as f:
                f.write(ebp)
        return os.path.basename(path)[:-4]

    def execute(self, context):
        kind = self.project_type
        if self.anim_source == "BORROW" and kind != "CREATURE":
            self.report({"ERROR"}, "Borrowing animations works only for creature projects: choose Animations 'Keep its own' or 'None'")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        if not path.lower().endswith(".sgm"):
            path += ".sgm"
        if not (kind == "CREATURE" and self.donor != "DEFAULT"):       # the game's-animal path refuses an existing file itself
            taken = [x for x in (path, path[:-4] + ".ebp" if kind != "CREATURE" else None) if x and os.path.exists(x)]
            if taken:                                                   # a new project never replaces the files of an earlier one
                self.report({"ERROR"}, f"{os.path.basename(taken[0])} is already there. Choose another name; nothing was overwritten.")
                return {"CANCELLED"}
        donor_model = None
        if self.anim_source == "BORROW":
            bpath = bpy.path.abspath(self.borrow_file) if self.borrow == "FILE" else self.borrow
            try:
                donor_model = core.read_source(core.resolve_source(bpath))
            except Exception as e:
                self.report({"ERROR"}, f"Cannot read the creature to borrow from: {e}")
                return {"CANCELLED"}
            if core.is_scene_model(donor_model) or not len(donor_model.animations):
                self.report({"ERROR"}, "That file is not a creature with animations")
                return {"CANCELLED"}
        stock = None                                          # the game's animal this creature is a copy of
        replacing = False                                     # ... and this project is the replacement of that animal
        created = []                                          # exactly the files this run wrote: the only ones it ever removes
        try:
            if kind == "CREATURE" and self.donor != "DEFAULT":
                stock, created = self._write_stock_creature(path, context)
                path = created[0]
                name = os.path.basename(path)[:-4]
                replacing = self.replace == "REPLACE"
            else:
                created = [path] + ([path[:-4] + ".ebp"] if kind != "CREATURE" else [])   # none of them existed (checked above)
                name = self._write_creature(path) if kind == "CREATURE" else self._write_scene(path)
            if kind != "CREATURE" and (not core.is_scene_model(core.read_source(path))):
                _remove_files(created)                             # nothing of a refused project stays on disk
                self.report({"ERROR"}, "That file is a creature. Choose Type 'Creature', or another file.")
                return {"CANCELLED"}
        except Exception as e:
            _remove_files(created)
            self.report({"ERROR"}, f"Could not start the project: {e}")
            return {"CANCELLED"}
        why = ""
        try:
            res = bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=True)
        except Exception as e:                              # the import reports its refusal as an error (a RuntimeError), or fails otherwise
            res, why = set(), str(e).replace("Error:", "").strip()
        if "FINISHED" not in res:
            _remove_files(created)                          # nothing of a refused project stays on disk
            self.report({"ERROR"}, f"Could not start the project: {why or 'the file could not be imported'}"
                                   + (" (a few of the game's models, like the gyro copters, hold only a skeleton and animations)"
                                      if "no geometry" in why else ""))
            return {"CANCELLED"}
        obj = context.view_layer.objects.active
        arm = store.rig(obj) if obj is not None else None
        note = ""
        if arm is not None:
            try:
                if self.anim_source == "NONE":
                    note = f"; {drop_all_clips(arm)} animations of the starting model removed"
                elif self.anim_source == "BORROW":
                    rep = borrow_clips(arm, core.read_source(path), donor_model, [s for s in self.borrow_names.split(",")])
                    note = (f"; borrowed {len(rep['clips'])} animations ({rep['matched']} bones matched by name, {rep['unmatched_target']} not in "
                            f"that creature" + (f", already there: {', '.join(rep['skipped'])}" if rep["skipped"] else "") + ")")
                    if rep["matched"] < 0.6 * max(1, rep["matched"] + rep["unmatched_target"]):
                        self.report({"WARNING"}, "Few bones share names with that creature: its animations will move little")
            except Exception as e:                          # noqa: BLE001 -- the project itself is fine: keep it
                import traceback
                traceback.print_exc()                       # the details are in Blender's system console
                if self.anim_source == "NONE":
                    self.report({"WARNING"}, "The project was started, but its animations could not be removed. Delete them in the Action Editor.")
                else:
                    self.report({"WARNING"}, "The project was started, but the animations could not be borrowed. Try Borrow Clips From... again.")
        for o in bpy.data.objects:
            if o is obj or (arm is not None and o.get("bz_rig") == arm.name):
                o["bz_project"] = kind                      # what this file is: exports and checks follow its subtype
        if kind != "CREATURE" and self.donor != "FILE":     # the game's own file this project was started from: the Blueprint panel exports into its folder
            for o in bpy.data.objects:
                if o.get("bz_scene") and o.get("bz_source") == path:
                    o["bz_donor"] = self.donor
        if stock is not None and obj is not None:             # a1package looks for the donor beside the .sgm first, then by this stamp
            obj["bz_stock_donor"] = stock.name
            obj["bz_stock_archive"] = stock.archive_name
            note = f" (a copy of the game's {stock.name} with its blueprint, stats and rules beside it)" + note
            if replacing:                                     # Export Creature for the Game offers "Replace the original" first
                obj["bz_game_mode"] = "REPLACE"
                note += f"; it replaces the game's own {stock.name} when you export it for the game"
        label = dict((k[0], k[1]) for k in KINDS)[kind]
        self.report({"INFO"}, f"Started the {label.lower()} project {name} in {os.path.basename(path)}{note}")
        return {"FINISHED"}


def menu_new(self, context):
    self.layout.separator()
    self.layout.operator(BZ_OT_new_project.bl_idname, text="Impossible Creatures Project...", icon="OUTLINER_OB_ARMATURE")


CLASSES = (BZ_OT_new_project,)
