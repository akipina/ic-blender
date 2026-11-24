"""Blueprint panel, proof of concept (slice POC-blueprint-panel), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/blueprint_poc_test.py

What it proves (the module is ``blender_native/blueprint.py``; it registers itself here, not through ``__init__.py``):

1.  register / unregister: the classes and the ``Object.bz_blueprint`` pointer appear and leave nothing behind; registering twice is harmless; classes
    registered by a registry first (the way ``sceneobj`` does it) and ``register()`` as a hook afterwards work too (that is the move into sceneobj/);
2.  the helpers: names and folders, the value diff (offsets compared as 32-bit floats), the problems found in values, the class table in plain words;
3.  a new project from a gatherer donor and from structure donors (found with ``newproject.scene_donors``): the panel values load from the donor's
    blueprint and the donor's folder is found; an UNTOUCHED export is byte-identical for the .sgm and the .ebp; after editing footprint, name, class, offset
    and sounds the export re-reads with ``SceneBlueprint`` and ONLY the expected chunks differ (name / class: the header, footprint / offset: the footprint
    chunk, sounds: the sound chunks); the .sgm is exactly what ``bz.export_scene`` writes, edited geometry included; Reset restores the file's values; a
    blueprint file that changed on disk after loading is refused until it is read again;
4.  a model with no blueprint file: the panel offers a new one; the export writes it, and refuses without it;
5.  the read-only cases (old layout, model inside the blueprint, unreadable file, no reading library): shown read-only with a plain reason, exported as
    far as that is honest, refused otherwise;
6.  refusals with a plain reason and nothing written: the game's own folder, a Steam folder, a bad value, a path out of the folder;
7.  where the folder in the game comes from;
8.  every draw function runs against a layout that checks what Blender checks (the property exists, the operator exists and takes that argument, the icon
    exists); fields are greyed by class; no internal word shows;
9.  the game install is not touched (fingerprint before and after).

A check whose dependency has not landed is SKIPPED with the reason (``SO_SKIP``), never passed silently: the gameplay-numbers table needs
``ICParse.ebp.stats``; everything that reads or writes a blueprint needs ``ICParse.ebp.scene``.
"""
import hashlib
import os
import sys
import contextlib
import glob
import types
from collections import Counter

sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

C.addon()
import importlib  # noqa: E402
import importlib.util  # noqa: E402
import blender_native  # noqa: E402,F401
from blender_native import newproject  # noqa: E402

# The module sits in the add-on's folder (blender_native/blueprint.py) until the integrator moves it to sceneobj/panels_blueprint.py (hand-off,
# Requests). The test works with either: once it is in sceneobj/ the sceneobj registry registers it together with the add-on.
MOVED = importlib.util.find_spec("blender_native.blueprint") is None
BP = importlib.import_module("blender_native.sceneobj.panels_blueprint" if MOVED else "blender_native.blueprint")

HENCH = "art/ebps/gatherers/henchmen.sgm"
LAB = "art/ebps/structures/lab.sgm"
AVIARY = "art/ebps/structures/aviary.sgm"        # a donor that already lists 22 sounds
# words that must never reach the user (the names of chunks, classes and internal fields)
FORBIDDEN = ("EBPD", "BLDI", "PIDS", "EBPC", "EBPF", "SceneBlueprint", "PropertyGroup", "defstats", "bz_", "IFF", "chunk", "FORM ", "SHA-1", "sha1")


def section(title):
    print(f"-- {title}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ a layout that checks what Blender checks
class _Props:
    """What ``layout.operator(...)`` returns: setting an argument the operator does not have is a problem (real Blender raises)."""

    def __init__(self, idname, allowed, problems):
        object.__setattr__(self, "_idname", idname)
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_problems", problems)

    def __setattr__(self, name, value):
        if name not in self._allowed:
            self._problems.append(f"operator {self._idname} has no argument {name}")
        object.__setattr__(self, name, value)


class CheckedLayout(C.FakeLayout):
    """``FakeLayout`` plus what a real draw would reject: a property that is not on its data, an operator that is not registered or has no such argument,
    an icon that does not exist. It also records, with every property, whether its row was greyed (``enabled``)."""
    ICONS = None

    def __init__(self, calls=None, alert=False, enabled=True, problems=None):
        super().__init__(calls, alert)
        self.enabled = enabled
        self.problems = problems if problems is not None else []
        if CheckedLayout.ICONS is None:
            CheckedLayout.ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())

    def _sub(self, kind, **kw):
        self.calls.append((kind, kw))
        return CheckedLayout(self.calls, alert=self.alert, enabled=self.enabled, problems=self.problems)

    def grid_flow(self, **kw):
        return self._sub("grid_flow", **kw)

    def _icon(self, icon):
        if icon and icon != "NONE" and icon not in self.ICONS:
            self.problems.append(f"no icon called {icon}")

    def label(self, text="", icon="NONE", icon_value=0, **kw):
        self._icon(icon)
        super().label(text=text, icon=icon, icon_value=icon_value, **kw)

    def prop(self, data, name, **kw):
        if name not in data.bl_rna.properties:
            self.problems.append(f"no property {name} on {type(data).__name__}")
        self._icon(kw.get("icon", "NONE"))
        self.calls.append(("prop", dict(name=name, enabled=self.enabled, **kw)))

    def operator(self, idname, text="", icon="NONE", **kw):
        self._icon(icon)
        mod, _, fn = idname.partition(".")
        try:
            allowed = set(getattr(getattr(bpy.ops, mod), fn).get_rna_type().properties.keys())
        except (AttributeError, KeyError):
            self.problems.append(f"operator {idname} is not registered")
            allowed = set()
        self.calls.append(("operator", dict(idname=idname, text=text, icon=icon, enabled=self.enabled, alert=self.alert, **kw)))
        return _Props(idname, allowed, self.problems)

    def props(self):
        return [d for k, d in self.calls if k == "prop"]

    def said(self):
        """Everything the layout says, as one text (a wrapped message reads as one sentence again)."""
        out = [d["text"] for k, d in self.calls if k == "label"]
        out += [d["text"] for k, d in self.calls if k == "operator" and d.get("text")]
        out += [d["text"] for k, d in self.calls if k == "prop" and d.get("text")]
        return " ".join(out)


def draw(obj, which="blueprint"):
    lay = CheckedLayout()
    (BP.draw_blueprint if which == "blueprint" else BP.draw_stats)(lay, obj)
    assert not lay.problems, lay.problems
    return lay


def enabled_of(lay, prop_name):
    rows = [p for p in lay.props() if p["name"] == prop_name]
    assert rows, f"the panel does not draw {prop_name}"
    return rows[0]["enabled"]


def jargon(text):
    """The internal words in ``text``. A path (a folder the user chose, which may hold anything) and the name of a game file (``defstats5.lua``: a
    file the modder can open) are not looked at."""
    text = " ".join(w.strip(",.;:()") for w in str(text).split()
                    if "/" not in w and "\\" not in w and not w.strip(",.;:()").endswith((".lua", ".sgm", ".ebp")))
    return [w for w in FORBIDDEN if w in text]


# ------------------------------------------------------------------------------------------------------------------ bytes of a blueprint, chunk by chunk
def leaf_chunks(raw):
    """Every chunk of an .ebp in file order as (the forms above it, tag, bytes); a form is (the forms above it, 'FORM X', None)."""
    from ICParse.iff.chunk import FormChunk
    from ICParse.iff.document import IffDocument
    out = []

    def walk(chunks, path):
        for c in chunks:
            if isinstance(c, FormChunk):
                out.append((path, "FORM " + c.form_type, None))
                walk(c.children, path + (c.form_type,))
            else:
                out.append((path, c.tag, bytes(c.raw)))
    walk(IffDocument.from_bytes(raw).root_chunks, ())
    return out


EDITABLE_TAGS = ("EBPD", "BLDI", "PIDS")


def changed_tags(before, after):
    """The chunk tags whose bytes differ between two .ebp files. Every chunk of any other tag must be identical AND in the same order (that is
    asserted here): the model reference, the motion tree, the events, the legacy attributes, the version word."""
    a, b = leaf_chunks(before), leaf_chunks(after)
    ca, cb = Counter(a), Counter(b)
    tags = {k[1] for k in list((ca - cb).keys()) + list((cb - ca).keys())}
    ignore = EDITABLE_TAGS if any(x[1] == "FORM EBPC" for x in a) else EDITABLE_TAGS + ("FORM EBPC",)       # a file with no footprint gets the form that holds it
    ra, rb = [x for x in a if x[1] not in ignore], [x for x in b if x[1] not in ignore]
    if ra != rb:
        raise AssertionError("a chunk other than the header, the footprint and the sounds changed (or moved): new "
                             f"{[x[:2] for x in rb if x not in ra][:3]}, gone {[x[:2] for x in ra if x not in rb][:3]}")
    return sorted(tags)


# ------------------------------------------------------------------------------------------------------------------ a project on a stock donor
class Project:
    """File > New > Impossible Creatures Project from a stock donor, animations kept (so an untouched export is byte-identical to the project)."""

    def __init__(self, kind, member, name):
        C.fresh()
        self.kind, self.member, self.name = kind, member, name
        self.dir = C.tmpdir("poc_")
        self.sgm = os.path.join(self.dir, name + ".sgm")
        self.ebp = os.path.join(self.dir, name + ".ebp")
        res = bpy.ops.bz.new_project(filepath=self.sgm, project_type=kind, donor=member, anim_source="OWN")
        assert "FINISHED" in res, res
        self.obj = bpy.context.view_layer.objects.active
        self.sgm_bytes = open(self.sgm, "rb").read()
        self.ebp_bytes = open(self.ebp, "rb").read()

    def entity(self):
        ent = BP.entity_of(bpy.context.view_layer.objects.active)
        assert ent is not None
        return ent

    @property
    def bp(self):
        return self.entity().holder.bz_blueprint

    def export(self, name=None, folder=None):
        out = C.tmpdir("poc_out_")
        args = dict(directory=out)
        if name is not None:
            args["entity_name"] = name
        if folder is not None:
            args["folder"] = folder
        res = bpy.ops.bz.export_entity_folder(**args)
        assert "FINISHED" in res, res
        return out

    def files(self, out, name=None, folder=None):
        name = name or BP.clean_name(self.name)
        folder = self.bp.folder if folder is None else folder
        base = os.path.join(out, "Data", "art", "ebps", *[p for p in folder.split("/") if p])
        return os.path.join(base, name + ".sgm"), os.path.join(base, name + ".ebp")

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()


def donor(kind, member):
    return next(((a, m) for a, m in newproject.scene_donors(kind) if m == member), None)


PREFS = importlib.import_module(BP._PKG + ".sceneobj.prefs")


@contextlib.contextmanager
def live_prefs():
    """The add-on's live preferences, as when it is enabled (a background Blender does not add the add-on's entry itself)."""
    entry = bpy.context.preferences.addons.new()
    entry.module = PREFS.ADDON_ID
    try:
        live = PREFS.get()
        assert type(live).__name__ == "BZ_AP_prefs", type(live)
        yield live
    finally:
        bpy.context.preferences.addons.remove(entry)


def refused(fn, *words, plain=True):
    """``fn()`` must raise Refused whose plain reason holds every word (and, unless ``plain`` is False, no internal word); returns the reason."""
    try:
        fn()
    except BP.Refused as e:
        text = str(e)
        for w in words:
            assert w in text, (w, text)
        if plain:
            assert not jargon(text), (jargon(text), text)
        return text
    raise AssertionError("the export was accepted, a refusal was expected")


def listing(folder):
    out = []
    for dp, _dn, fn in os.walk(folder):
        out += [os.path.relpath(os.path.join(dp, f), folder).replace(os.sep, "/") for f in fn]
    return sorted(out)


def nowhere():
    return C.tmpdir("poc_nowhere_")


# ------------------------------------------------------------------------------------------------------------------ 1. registration
def present(cls):
    """Whether Blender knows the class now: an operator or a panel by name in bpy.types, a property group through its registry."""
    if issubclass(cls, bpy.types.PropertyGroup):
        return BP._is_registered(cls)
    return getattr(bpy.types, BP._rna_id(cls), None) is cls


def test_registration():
    section("1. register / unregister")
    assert len(BP.CLASSES) == len(set(BP.CLASSES)) == 10
    if MOVED:                                                              # sceneobj registered the module together with the add-on
        assert all(present(c) and BP._is_registered(c) for c in BP.CLASSES) and C.has_api(bpy.types.Object, "bz_blueprint")
        assert not BP._OWN, "the registry registered the classes; the module registered none itself"
        BP.unregister()                                                    # as a hook it removes the pointer only
        assert not C.has_api(bpy.types.Object, "bz_blueprint") and all(present(c) for c in BP.CLASSES)
        BP.register()
        assert C.has_api(bpy.types.Object, "bz_blueprint") and not BP._OWN
        print("   in sceneobj/: registered by the registry with the add-on; the hook adds and removes the pointer only", flush=True)
        return
    assert not any(present(c) for c in BP.CLASSES), "the blueprint classes must not exist before register()"
    assert not C.has_api(bpy.types.Object, "bz_blueprint")
    BP.register()
    BP.register()                                                          # twice is harmless
    assert all(present(c) and BP._is_registered(c) for c in BP.CLASSES) and C.has_api(bpy.types.Object, "bz_blueprint")
    BP.unregister()
    assert not C.has_api(bpy.types.Object, "bz_blueprint") and not any(present(c) for c in BP.CLASSES), "register() left something behind"
    BP.unregister()                                                        # and unregistering again is harmless
    # the way sceneobj registers a module: its classes first, then the module's register() hook
    for c in BP.CLASSES:
        bpy.utils.register_class(c)
    BP.register()
    assert not BP._OWN, "register() must leave classes that a registry already registered to the registry"
    assert C.has_api(bpy.types.Object, "bz_blueprint")
    BP.unregister()
    assert not C.has_api(bpy.types.Object, "bz_blueprint") and all(present(c) for c in BP.CLASSES), "the hook must remove the pointer only"
    for c in reversed(BP.CLASSES):
        bpy.utils.unregister_class(c)
    assert not any(present(c) for c in BP.CLASSES)
    BP.register()                                                          # registered for the rest of the test
    print("   10 classes + Object.bz_blueprint: register, register again, unregister, registry-then-hook: all clean", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 2. helpers
def test_helpers():
    section("2. names, diff, problems, class table")
    assert BP.clean_name("My Lab/../X") == "my lab_x" and BP.clean_name("  ") == "entity" and BP.clean_name("Dr_Ganglion's_Lab") == "dr_ganglion's_lab"
    assert BP.clean_name("T-Rex") == "t-rex" and BP.clean_name("Ünï.sgm") == "n_sgm"
    assert BP.clean_folder("Structures") == "structures" and BP.clean_folder("..\\../Nature/Swamp/") == "nature/swamp" and BP.clean_folder("") == ""
    assert BP.wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"] and BP.wrap("", 5) == []
    assert BP.words(["name"]) == "name" and BP.words(["name", "sounds"]) == "name and sounds" and BP.words(["name", "class", "sounds"]).count(",") == 1
    assert BP._rel_of_member("art/ebps/structures/lab.sgm") == "structures" and BP._rel_of_member("ART\\EBPS\\Nature\\Swamp\\x.sgm") == "nature/swamp"
    assert BP._rel_of_member("art/ebps/default.ebp") == "" and BP._rel_of_member("art/textures/x.txr") is None
    assert BP._folder_from_path("/home/u/mod/Data/Art/EBPs/Structures/x.sgm") == "structures" and BP._folder_from_path("/tmp/x/art/ebps_old/y.sgm") is None
    a = BP.Values("A", 5, (2, 3), (0.1, 0.0, -0.0), ("x",))
    assert BP.Values.from_json(a.to_json()) == a and BP.Values.from_json("not json") == BP.Values()
    assert BP.changed_fields(a, a) == []
    assert BP.changed_fields(a, BP.Values("B", 5, (2, 3), (0.1, 0.0, -0.0), ("x",))) == ["name"]
    assert BP.changed_fields(a, BP.Values("A", 6, (2, 4), (0.1, 0.0, -0.0), ("y",))) == ["class", "footprint", "sounds"]
    assert BP.changed_fields(a, BP.Values("A", 5, (2, 3), (0.1, 0.0, 0.0), ("x",))) == ["offset"], "-0.0 and 0.0 are different bytes"
    import numpy as np
    wobble = float(np.float32(0.1))
    assert BP.changed_fields(BP.Values(offset=(0.1, 0.0, 0.0)), BP.Values(offset=(wobble, 0.0, 0.0))) == [], "an offset that went through 32 bits is the same"
    assert BP.problems_of(BP.Values("ok", 3, (1, 1), (0.0, 0.0, 0.0), ("DATA:AUDIO\\X.PAT",))) == []
    bad = BP.problems_of(BP.Values("n" * 256, 70000, (256, 0), (float("nan"), 0.0, 0.0), ("", "p" * 260, "cafœ.pat")))
    assert len(bad) == 7, bad
    assert not BP.problems_of(BP.Values("n" * 255, 65535, (255, 255), (1e9, 0.0, 0.0), ("p" * 259,)))
    worst = BP.Values("n" * 256, 70000, (256, 0), (float("nan"), 0.0, 0.0), ("", "p" * 260))
    assert len(BP.problems_of(worst, ["footprint"])) == 1 and BP.problems_of(worst, []) == [] and len(BP.problems_of(worst, ["sounds"])) == 2, \
        "an edit is only held up by a problem in what is being edited"
    ci = BP.class_info                                                      # the class table in plain words
    assert ci(3).family == "unit" and not ci(3).buildable and ci(3).registered
    assert ci(5).buildable and ci(5).registered and ci(5).label.startswith("5 - ") and "building" in ci(5).label
    assert ci(0).registered is False and "Plain prop" in ci(0).label and not ci(0).buildable
    assert ci(10).registered is False and "Unused" in ci(10).label
    assert ci(33).expansion_only and "expansion" in ci(33).label and not ci(33).registered
    assert ci(777).family == "unknown" and not ci(777).registered and "not a class the game knows" in ci(777).label
    assert {c for c in range(40) if ci(c).buildable} == {4, 5, 6, 7, 8, 9, 13, 16, 17, 19, 23, 26, 28, 29, 30, 31, 32}, "the classes with a building site"
    for text in list(BP.HINT.values()) + list(BP.SUBTYPE_WORDS.values()) + list(BP.FAMILY_WORDS.values()) + [BP.LEGACY_NOTE, BP.EMBEDDED_NOTE]:
        assert not jargon(text), text
    # the preferences: Game build decides which class numbers the game knows; the Game folder is the one in use and is never written to
    assert BP.target_mod() == "icmod" and BP.game_dir() == PREFS.resolve_game_dir()
    with live_prefs() as live:
        live.target_mod = "tellurian"
        assert BP.target_mod() == "tellurian" and ci(33).registered and ci(33).expansion_only and ci(5).registered
        live.target_mod = "icmod"
        assert not ci(33).registered
        mine = C.tmpdir("poc_pref_game_")
        live.game_dir = mine
        assert BP.game_dir() == (mine, "preferences") and mine in BP.protected_roots()
        words = BP.game_dir_words()
        assert words.startswith(f"Game folder: {mine} (from the Game folder in the add-on's preferences)") and "none of the game's archives" in words, words
        refused(lambda: BP.check_target(os.path.join(mine, "out")), "separate folder")
    assert BP.target_mod() == "icmod" and not ci(33).registered and BP.game_dir() == PREFS.resolve_game_dir()
    print("   names, folders, wrapping, the value diff, the problems, 40 class numbers in plain words; Game build and Game folder followed", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 3. the donor flows
def test_flow(kind, member, name, lib, stats):
    section(f"3. a project from {member}")
    a_m = donor(kind, member)
    if a_m is None:
        C.skip(f"the donor {member} is not in the game archives: its flow is skipped")
        return
    P = Project(kind, member, name)
    ent = P.entity()
    parts = ent.parts
    assert len(parts) >= 1 and ent.source == P.sgm
    # the same entity from every object that belongs to it, and one holder
    objs = list(parts) + ([ent.rig] if ent.rig is not None else []) + ([parts[0].parent] if parts[0].parent is not None else [])
    assert all(BP.entity_of(o).holder is ent.holder for o in objs), "every object of the entity must reach the same holder"
    assert BP.entity_of(None) is None
    parent = parts[0].parent
    assert ent.holder is (ent.rig if ent.rig is not None else parent if parent is not None and parent.type == "EMPTY" else parts[0])
    bp = ent.holder.bz_blueprint
    assert not bp.loaded and bp.state == "NONE"
    assert BP.blueprint_path(ent) == P.ebp
    lay = draw(P.obj)
    assert lay.operators() == ["bz.blueprint_load"] and "Load Blueprint" in lay.said(), lay.operators()

    # ---- load: the panel values are the donor's blueprint
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    bp = P.bp
    assert bp.loaded and bp.state == "FILE" and bp.note == "" and bp.source_path == P.ebp, (bp.state, bp.note)
    want = BP.values_of(lib.SceneBlueprint.read(P.ebp_bytes))
    assert BP.values_from_pg(bp) == want, (BP.values_from_pg(bp), want)
    assert BP.Values.from_json(bp.baseline) == want
    expect_folder = BP._rel_of_member(a_m[1])
    # New Project stamps the donor (bz_donor), so the folder comes from that stamp; a project without the stamp is found by identity
    want_src = "STAMP" if any(o.get("bz_donor") for o in objs) else "IDENTITY"
    assert bp.folder == expect_folder and bp.folder_source == want_src, (bp.folder, bp.folder_source, want_src)
    # a project made before New Project stamped its donor (an older add-on) still finds the folder, by identity with the stock file
    stamped = [(o, o["bz_donor"]) for o in objs if o.get("bz_donor")]
    for o, _ in stamped:
        del o["bz_donor"]
    try:
        got = BP.determine_folder(ent, P.ebp, want.class_id, bp.has_skeleton, ent.rig is not None)
        assert got == (expect_folder, "IDENTITY"), got
    finally:
        for o, v in stamped:
            o["bz_donor"] = v
    key = BP.subtype_of(bp)
    assert key == {"CHARACTER": "GATHERER", "STRUCTURE": "STRUCTURE"}[kind], key
    assert bp.has_skeleton == (ent.rig is not None) and bp.class_choice == str(want.class_id)
    print(f"   loaded: name {want.name!r}, class {want.class_id}, footprint {want.footprint}, {len(want.sounds)} sound(s), folder {bp.folder} "
          f"({bp.folder_source}), {key}", flush=True)
    lay = draw(P.obj)
    assert set(lay.operators()) >= {"bz.blueprint_reset", "bz.export_entity_folder", "bz.blueprint_sound_add"} and BP.SUBTYPE_WORDS[key] in lay.said()
    assert "Not changed since it was loaded." in lay.said()
    info = BP.class_info(want.class_id)
    assert enabled_of(lay, "footprint_x") == info.buildable and enabled_of(lay, "footprint_z") == info.buildable, \
        "the footprint is greyed unless the class is a building"
    assert enabled_of(lay, "display_name") and enabled_of(lay, "offset") and enabled_of(lay, "class_id") and enabled_of(lay, "class_choice")
    assert ("path cells" in lay.said()) == info.buildable and ("is not used" in lay.said()) == (not info.buildable)
    assert "changes multiplayer" in lay.said()
    stats_lay = draw(P.obj, "stats")
    if stats is None:
        assert "cannot be listed yet" in bp.stats_note and not len(bp.stats)
        C.skip("ICParse.ebp.stats is not installed yet: the table of gameplay numbers is not checked")
    else:
        assert len(bp.stats) > 0 and bp.stats[0].key in stats_lay.said(), "the stats table is empty or not drawn"
        assert all(row.key and row.value != "" and row.origin.endswith(".lua") for row in bp.stats), [(r.key, r.value, r.origin) for r in bp.stats][:3]
        assert bp.stats_source.startswith("From defstats") and "default numbers of this kind of thing" in bp.stats_source, bp.stats_source
        assert bp.stats_source in stats_lay.said() and not jargon(stats_lay.said())
        print(f"   gameplay numbers: {len(bp.stats)} listed; {bp.stats_source}", flush=True)

    # ---- an untouched export is byte-identical, in a folder that is ready to drop
    out = P.export()
    sgm_path, ebp_path = P.files(out)
    assert P.read(sgm_path) == P.sgm_bytes, "an untouched export changed the .sgm"
    assert P.read(ebp_path) == P.ebp_bytes, "an untouched export changed the .ebp"
    rel = f"Data/art/ebps/{expect_folder}/{BP.clean_name(name)}"
    files = listing(out)
    assert files == sorted(["README.txt", rel + ".ebp", rel + ".sgm"]), files
    assert all(p == p.lower() for f in files if f.startswith("Data/") for p in f.split("/")[1:]), "paths under Data must be lower case"
    readme = open(os.path.join(out, "README.txt"), encoding="utf-8").read()
    for needle in ("Steam", "Data", rel + ".sgm", rel + ".ebp", "copy of the game that you own", "Several players", "copied unchanged"):
        assert needle in readme, (needle, readme)
    assert not jargon(readme), jargon(readme)
    assert "of your own" in readme and not BP.controllers().is_hard_bound(expect_folder, BP.clean_name(name))
    print(f"   untouched export: .sgm {len(P.sgm_bytes)} B and .ebp {len(P.ebp_bytes)} B byte-identical; {files}", flush=True)

    # ---- every edit alone changes only its own chunk
    new_class = 23 if want.class_id != 23 else 5

    def edit_and_check(label, edit, expect_tags, check):
        bpy.ops.bz.blueprint_reset()
        assert BP.changed_fields(BP.Values.from_json(P.bp.baseline), BP.values_from_pg(P.bp)) == []
        edit(P.bp)
        o = P.export(name=BP.clean_name(name))
        raw = P.read(P.files(o)[1])
        tags = changed_tags(P.ebp_bytes, raw)
        assert tags == expect_tags, (label, tags, expect_tags)
        assert P.read(P.files(o)[0]) == P.sgm_bytes, f"{label}: the .sgm changed"
        check(lib.SceneBlueprint.read(raw))
        print(f"   {label}: only {tags} changed", flush=True)

    def name_edit(b):
        b.display_name = "My Test Name"

    def name_check(s):
        assert s.header.name == "My Test Name" and s.header.defstats_class == want.class_id

    def class_edit(b):
        b.class_id = new_class

    def class_check(s):
        assert s.header.defstats_class == new_class and s.header.name == want.name

    def footprint_edit(b):
        b.footprint_x, b.footprint_z = 9, 4

    def footprint_check(s):
        assert (s.build.footprint_x, s.build.footprint_z) == (9, 4) and tuple(s.build.offset) == want.offset

    def offset_edit(b):
        b.offset = (0.5, 1.0, -0.25)

    def offset_check(s):
        assert tuple(s.build.offset) == (0.5, 1.0, -0.25) and (s.build.footprint_x, s.build.footprint_z) == want.footprint

    def sounds_edit(b):
        if len(b.sounds):
            assert "FINISHED" in bpy.ops.bz.blueprint_sound_remove(index=0)        # take the first away ...
        for path in ("DATA:AUDIO\\SFX\\NEW_ONE.PAT", "DATA:AUDIO\\SFX\\NEW_TWO.PAT"):        # ... and add two
            assert "FINISHED" in bpy.ops.bz.blueprint_sound_add()
            b.sounds[len(b.sounds) - 1].path = path

    def sounds_check(s):
        assert s.sounds == list(want.sounds[1:]) + ["DATA:AUDIO\\SFX\\NEW_ONE.PAT", "DATA:AUDIO\\SFX\\NEW_TWO.PAT"], s.sounds

    edit_and_check("name", name_edit, ["EBPD"], name_check)
    edit_and_check("class", class_edit, ["EBPD"], class_check)
    edit_and_check("footprint", footprint_edit, ["BLDI"], footprint_check)
    edit_and_check("offset", offset_edit, ["BLDI"], offset_check)
    edit_and_check("sounds", sounds_edit, ["PIDS"], sounds_check)

    # ---- everything at once, with edited geometry: the .sgm is exactly what Export Scene Object writes
    bpy.ops.bz.blueprint_reset()
    bp = P.bp
    bp.display_name, bp.class_id, bp.footprint_x, bp.footprint_z = "Everything", new_class, 3, 2
    bp.offset = (1.0, 2.0, 3.0)
    assert "FINISHED" in bpy.ops.bz.blueprint_sound_add()
    bp.sounds[len(bp.sounds) - 1].path = "DATA:AUDIO\\SFX\\LAST.PAT"
    edited = BP.changed_fields(BP.Values.from_json(bp.baseline), BP.values_from_pg(bp))
    assert edited == ["name", "class", "footprint", "offset", "sounds"], edited
    assert f"Edited: {BP.words(edited)}. Written when you export." in draw(P.obj).said()
    big = max(P.entity().parts, key=lambda o: len(o.data.vertices))
    big.data.vertices[0].co.x += 0.03
    big.data.update()
    folder = "structures" if kind == "STRUCTURE" else "gatherers"
    out = P.export(name="Edited Copy", folder=folder)
    sgm_path, ebp_path = P.files(out, "edited copy", folder)
    assert os.path.isfile(sgm_path) and os.path.isfile(ebp_path), listing(out)
    assert changed_tags(P.ebp_bytes, P.read(ebp_path)) == ["BLDI", "EBPD", "PIDS"]
    back = lib.SceneBlueprint.read(P.read(ebp_path))
    assert (back.header.name, back.header.defstats_class, (back.build.footprint_x, back.build.footprint_z), tuple(back.build.offset)) == \
        ("Everything", new_class, (3, 2), (1.0, 2.0, 3.0))
    assert back.sounds[-1] == "DATA:AUDIO\\SFX\\LAST.PAT" and back.sounds[:-1] == list(want.sounds)
    sgm_edit = P.read(sgm_path)
    assert sgm_edit != P.sgm_bytes, "the edited geometry was not exported"
    ref = os.path.join(C.tmpdir("poc_ref_"), "ref.sgm")
    if bpy.context.view_layer.objects.active.get("bz_rig"):
        assert "FINISHED" in bpy.ops.bz.export_scene(filepath=ref)
    else:
        assert "FINISHED" in bpy.ops.bz.export_scene_geometry(filepath=ref)
    assert sgm_edit == P.read(ref), "the .sgm is not what Export Scene Object writes"
    assert BP.changed_fields(BP.values_of(back), BP.values_from_pg(P.bp)) == []
    out2 = P.export(name="Edited Copy", folder=folder)                       # the same state writes the same bytes again
    assert P.read(P.files(out2, "edited copy", folder)[1]) == P.read(ebp_path) and P.read(P.files(out2, "edited copy", folder)[0]) == sgm_edit
    print("   all five edits + edited geometry: the .sgm equals Export Scene Object's, the .ebp re-reads with the edited values, the export is repeatable",
          flush=True)

    # ---- two things in one folder: the README lists both
    out_b = P.export(name="Second One", folder="structures")
    assert "FINISHED" in bpy.ops.bz.export_entity_folder(directory=out_b, entity_name="Third One", folder="nature/swamp")
    assert listing(out_b) == sorted(["README.txt", "Data/art/ebps/nature/swamp/third one.ebp", "Data/art/ebps/nature/swamp/third one.sgm",
                                     "Data/art/ebps/structures/second one.ebp", "Data/art/ebps/structures/second one.sgm"]), listing(out_b)
    assert "Data/art/ebps/nature/swamp/third one.sgm" in open(os.path.join(out_b, "README.txt")).read()

    # ---- Load never throws edits away; Reset does, on purpose
    P.bp.display_name = "unsaved edit"
    try:
        bpy.ops.bz.blueprint_load()
        raise AssertionError("Load must not replace edits that are not exported yet")
    except RuntimeError as e:
        assert "not exported yet" in str(e) and "Reset" in str(e), str(e)
    assert P.bp.display_name == "unsaved edit"
    # ---- Reset puts the file's values back; a file changed on disk is refused until it is read again
    assert "FINISHED" in bpy.ops.bz.blueprint_reset()
    assert BP.values_from_pg(P.bp) == want and BP.changed_fields(BP.Values.from_json(P.bp.baseline), BP.values_from_pg(P.bp)) == []
    sb2 = lib.SceneBlueprint.read(P.ebp_bytes)
    sb2.set_build(footprint=(11, 12))
    with open(P.ebp, "wb") as f:
        f.write(sb2.to_bytes())
    text = refused(lambda: BP.export_entity(bpy.context, P.obj, nowhere()), "changed on disk", "Reset")
    assert "FINISHED" in bpy.ops.bz.blueprint_reset() and (P.bp.footprint_x, P.bp.footprint_z) == (11, 12)
    with open(P.ebp, "wb") as f:
        f.write(P.ebp_bytes)
    assert "FINISHED" in bpy.ops.bz.blueprint_reset() and BP.values_from_pg(P.bp) == want
    print(f"   Reset restores the file's values; a file changed on disk is refused ('{text[:60]}...') until Reset reads it again", flush=True)

    # ---- the kind dropdown is a view of the class number; a number the game does not know is shown, never lost
    bp = P.bp
    bp.class_choice = "7"
    assert bp.class_id == 7 and bp.class_choice == "7"
    bp.class_id = 777
    assert bp.class_choice == "777" and not BP.class_info(777).registered
    assert "gives this number no behaviour" in draw(P.obj).said()
    if stats is not None:                                                       # the numbers follow the class
        assert len(bp.stats) == 0 and "no stats file for this class" in bp.stats_note, (len(bp.stats), bp.stats_note)
        bp.class_choice = "7"
        assert bp.stats_source.startswith("From defstats7.lua") and len(bp.stats) > 0, bp.stats_source
    bpy.ops.bz.blueprint_reset()
    if stats is not None:
        assert bp.stats_source.startswith(f"From defstats{want.class_id}.lua"), bp.stats_source


def test_replace(stats):
    section("3b. the name of a stock thing: the game looks it up by name")
    if donor("STRUCTURE", LAB) is None:
        C.skip(f"the donor {LAB} is not in the game archives: the replace-stock check is skipped")
        return
    P = Project("STRUCTURE", LAB, "lab")
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    assert "looks this one up by name" in draw(P.obj).said()
    out = P.export()
    sgm_path, ebp_path = P.files(out)
    assert sgm_path.endswith("Data/art/ebps/structures/lab.sgm") and P.read(sgm_path) == P.sgm_bytes and P.read(ebp_path) == P.ebp_bytes
    readme = open(os.path.join(out, "README.txt")).read()
    assert "so these files REPLACE the original of that name" in readme and "of your own" not in readme and not jargon(readme), readme
    if stats is not None:
        assert P.bp.stats_source.startswith("From lab.lua, which builds on defstats5.lua") and "default numbers" not in P.bp.stats_source, P.bp.stats_source
    print("   a project named like a stock thing: the panel and the README say the files replace the original; its own stats file is the one listed", flush=True)


def test_game_folder(stats):
    section("3c. the Game folder preference: the gameplay numbers are read from it, the panel says which folder is in use")
    P = Project("STRUCTURE", LAB, "PrefLab")
    with live_prefs() as live:
        empty = C.tmpdir("poc_nogame_")
        live.game_dir = empty
        assert "FINISHED" in bpy.ops.bz.blueprint_load()
        bp = P.bp
        assert bp.stats_game.startswith(f"Game folder: {empty} (from the Game folder") and "none of the game's archives" in bp.stats_game, bp.stats_game
        if stats is not None:
            assert len(bp.stats) == 0 and "no stats file for this class" in bp.stats_note, (len(bp.stats), bp.stats_note)
        lay = draw(P.obj)
        assert any(empty in t for t in lay.texts()) and not jargon(lay.said()), lay.texts()
        refused(lambda: BP.export_entity(bpy.context, P.obj, os.path.join(empty, "out")), "separate folder")
        mine = C.tmpdir("poc_game_")                     # links to the install's archives: read, never written
        for a in glob.glob(os.path.join(C.install_root(), "*.sga")):
            os.symlink(a, os.path.join(mine, os.path.basename(a)))
        live.game_dir = mine
        stale = draw(P.obj, "stats")
        assert "another game folder" in stale.said() and any(empty in t for t in stale.texts()), stale.texts()
        assert "FINISHED" in bpy.ops.bz.blueprint_reset()
        assert bp.stats_game == f"Game folder: {mine} (from the Game folder in the add-on's preferences)", bp.stats_game
        if stats is not None:
            assert len(bp.stats) > 0 and bp.stats_source.startswith("From defstats5.lua"), (len(bp.stats), bp.stats_source)
        assert "another game folder" not in draw(P.obj, "stats").said()
        assert any(mine in t for t in draw(P.obj).texts())
    print("   an empty Game folder: no numbers, the panel names the folder and asks whether it is the right one, no export into it; "
          "a folder of links to the archives: the numbers come back, read from there", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 4. no blueprint file
def test_no_file(lib):
    section("4. a model with no blueprint file")
    P = Project("CHARACTER", HENCH, "NoBlueprint")
    os.remove(P.ebp)
    assert BP.blueprint_path(P.entity()) is None
    lay = draw(P.obj)
    assert lay.operators() == ["bz.blueprint_load"] and "Start a New Blueprint" in lay.said() and "No blueprint file beside the model." in lay.said()
    try:
        bpy.ops.bz.blueprint_load()
        raise AssertionError("loading with no file must be refused")
    except RuntimeError as e:
        assert "no blueprint (.ebp) file beside the model" in str(e), str(e)
    refused(lambda: BP.export_entity(bpy.context, P.obj, nowhere()), "no blueprint", "Start a New Blueprint")
    assert "FINISHED" in bpy.ops.bz.blueprint_load(create_default=True)
    bp = P.bp
    assert bp.state == "NEW" and bp.loaded and bp.source_path == "" and BP.values_from_pg(bp) == BP.default_values(P.entity()), bp.state
    assert bp.display_name == "NoBlueprint" and bp.class_id == 0 and (bp.footprint_x, bp.footprint_z) == (1, 1) and not len(bp.sounds)
    lay = draw(P.obj)
    assert "New blueprint: it is written when you export." in lay.said() and enabled_of(lay, "display_name") and not enabled_of(lay, "footprint_x")
    bp.display_name = "A Brand New One"
    bp.class_id = 3
    out = P.export(name="brand_new", folder="gatherers")
    sgm_path, ebp_path = P.files(out, "brand_new", "gatherers")
    assert P.read(sgm_path) == P.sgm_bytes
    sb = lib.SceneBlueprint.read(P.read(ebp_path))
    assert sb.header.name == "A Brand New One" and sb.header.defstats_class == 3 and sb.model.kind == "proxy" and sb.header.layout == "stream"
    assert (sb.build.footprint_x, sb.build.footprint_z) == (1, 1) and sb.sounds == []
    readme = open(os.path.join(out, "README.txt")).read()
    assert "a new blueprint" in readme and "no motion tree" in readme
    assert "FINISHED" in bpy.ops.bz.blueprint_reset() and P.bp.display_name == "NoBlueprint" and P.bp.class_id == 0, "Reset of a new blueprint goes back to its start"
    print("   no file: the panel offers 'Start a New Blueprint'; the export refuses without it and writes the new blueprint with it", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 5. read-only and unreadable
def stock_ebp(lib, want):
    """The first stock blueprint (path, bytes) for which ``want(SceneBlueprint)`` is true."""
    for archive in newproject.game_archives():
        arc = newproject._archive(archive)
        for e in arc.entries:
            low = e.path.lower().replace("\\", "/")
            if low.startswith("art/ebps/") and low.endswith(".ebp"):
                raw = arc.read_path(low)
                try:
                    if want(lib.SceneBlueprint.read(raw)):
                        return low, raw
                except Exception:                                         # noqa: BLE001 -- a file the reader refuses is not the one wanted
                    continue
    return None


def attach(P, name, raw):
    """Make the project's objects point at another .ebp (as the importer's bz_ebp does) and forget what was loaded."""
    path = os.path.join(P.dir, name)
    with open(path, "wb") as f:
        f.write(raw)
    for o in bpy.data.objects:
        if o.get("bz_scene"):
            o["bz_ebp"] = path
    bp = P.entity().holder.bz_blueprint
    bp.loaded, bp.state = False, "NONE"
    return path


def test_read_only(lib):
    section("5. old layout, model inside, unreadable, no reading library")
    P = Project("CHARACTER", HENCH, "ReadOnly")
    cases = [
        ("LEGACY", lambda sb: sb.header.layout == "legacy" and sb.model.kind == "proxy", "oldest file layout", True),
        ("EMBEDDED", lambda sb: sb.model.kind == "embedded_sigm", "inside itself", False),
    ]
    for state, pred, phrase, exportable in cases:
        found = stock_ebp(lib, pred)
        if found is None:
            C.skip(f"no stock blueprint of kind {state} in this install: that read-only case is not checked")
            continue
        low, raw = found
        path = attach(P, f"stock_{state.lower()}.ebp", raw)
        assert "FINISHED" in bpy.ops.bz.blueprint_load()
        bp = P.bp
        assert bp.state == state and phrase in bp.note and bp.loaded and bp.source_path == path, (bp.state, bp.note)
        if state == "EMBEDDED":
            assert "oldest file layout" in bp.note, "a file that is both says both"
        lay = draw(P.obj)
        assert phrase in lay.said() and "LOCKED" in [d["icon"] for k, d in lay.calls if k == "label"]
        assert not any(enabled_of(lay, n) for n in ("display_name", "class_id", "class_choice", "offset", "footprint_x")), "a read-only blueprint is greyed"
        assert not bpy.ops.bz.blueprint_sound_add.poll() and not bpy.ops.bz.blueprint_sound_remove.poll(), "a read-only blueprint takes no sound edits"
        assert BP.values_from_pg(bp) == BP.values_of(lib.SceneBlueprint.read(raw)), "the values of a read-only blueprint are shown as they are"
        if exportable:
            out = P.export(name="legacy_copy", folder="locations")
            assert P.read(P.files(out, "legacy_copy", "locations")[1]) == raw, "a blueprint in the old layout is copied unchanged"
            assert P.read(P.files(out, "legacy_copy", "locations")[0]) == P.sgm_bytes
        else:
            refused(lambda: BP.export_entity(bpy.context, P.obj, nowhere()), "inside itself", "separate .sgm")
        print(f"   {state} ({low}): read-only with the reason; " + ("copied unchanged on export" if exportable else "export refused: no .sgm to write"), flush=True)
    # a file that is not a blueprint at all
    attach(P, "garbage.ebp", b"this is not a blueprint, not even close")
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    bp = P.bp
    assert bp.state == "PROBLEM" and "could not be read" in bp.note, (bp.state, bp.note)
    lay = draw(P.obj)
    assert "could not be read" in lay.said() and not [p for p in lay.props() if p["name"] == "display_name"], "an unreadable blueprint shows no values"
    text = refused(lambda: BP.export_entity(bpy.context, P.obj, nowhere()), "cannot be written", "could not be read", plain=False)   # the reader's own words follow
    print(f"   an unreadable file: shown with a reason, no values, export refused ('{text[:90]}')", flush=True)
    # the reading library is missing (as it is until ICParse.ebp.scene lands): the file is copied as it is, nothing can be edited
    attach(P, "plain.ebp", P.ebp_bytes)
    real = BP.scene_lib
    BP.scene_lib = lambda: None
    try:
        assert "FINISHED" in bpy.ops.bz.blueprint_load()
        bp = P.bp
        assert bp.state == "NO_LIBRARY" and "newer version" in bp.note and "copied unchanged" in bp.note
        lay = draw(P.obj)
        assert "newer version" in lay.said() and not [p for p in lay.props() if p["name"] == "display_name"]
        out = P.export(name="plain", folder="gatherers")
        assert P.read(P.files(out, "plain", "gatherers")[1]) == P.ebp_bytes and P.read(P.files(out, "plain", "gatherers")[0]) == P.sgm_bytes
        assert "FINISHED" in bpy.ops.bz.blueprint_load(create_default=True) and P.bp.state == "NO_LIBRARY"      # the file exists: it is loaded, not replaced
    finally:
        BP.scene_lib = real
    print("   with no reading library the blueprint file is copied unchanged and nothing can be edited", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 6. refusals
def test_refusals(lib):
    section("6. refusals leave nothing written")
    P = Project("STRUCTURE", LAB, "Refuse")
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    install = C.install_root()
    tmp = C.tmpdir("poc_ref_")
    steam = [os.path.join(tmp, "steamapps", "common", "x"), os.path.join(tmp, "Steam", "steamapps")]
    for t in steam + ([install, os.path.join(install, "my mod")] if install else []):
        refused(lambda t=t: BP.export_entity(bpy.context, P.obj, t), "separate folder")
    for t in steam:
        assert not os.path.exists(os.path.join(t, "Data")) and not os.path.exists(os.path.join(t, "README.txt")), f"{t} was written"
    try:
        bpy.ops.bz.export_entity_folder(directory=install or steam[0])
        raise AssertionError("the operator must refuse the game's own folder")
    except RuntimeError as e:
        assert "separate folder" in str(e), str(e)
    refused(lambda: BP.export_entity(bpy.context, P.obj, ""), "Choose a folder")
    f = os.path.join(tmp, "a_file")
    open(f, "w").write("x")
    refused(lambda: BP.export_entity(bpy.context, P.obj, f), "is a file")
    # bad values: nothing is written, the reason says which
    out = os.path.join(tmp, "out_bad")
    bp = P.bp
    bp.display_name = "x" * 256
    refused(lambda: BP.export_entity(bpy.context, P.obj, out), "256 letters", "at most 255")
    assert "at most 255" in draw(P.obj).said(), "the panel says so too"
    bp.display_name = "fine"
    assert "FINISHED" in bpy.ops.bz.blueprint_sound_add()
    refused(lambda: BP.export_entity(bpy.context, P.obj, out), "Sound 1 has no file name")
    bp.sounds[0].path = "DATA:AUDIO\\" + "L" * 260
    refused(lambda: BP.export_entity(bpy.context, P.obj, out), "Sound 1", "at most 259")
    bp.sounds[0].path = "DATA:AUDIO\\caféœ.PAT"
    refused(lambda: BP.export_entity(bpy.context, P.obj, out), "Sound 1", "cannot read")
    bp.sounds[0].path = "DATA:AUDIO\\SFX\\OK.PAT"
    assert not os.path.exists(out), "a refused export created the folder"
    try:
        bpy.ops.bz.blueprint_sound_remove(index=5)
        raise AssertionError("removing a sound that is not there must be refused")
    except RuntimeError as e:
        assert "no such sound" in str(e)
    assert "FINISHED" in bpy.ops.bz.blueprint_sound_remove(index=0) and len(bp.sounds) == 0
    # a name cannot climb out of the folder
    o = BP.export_entity(bpy.context, P.obj, out, name="../../evil name", folder="../../etc/x")
    assert os.path.realpath(o.sgm).startswith(os.path.realpath(out) + os.sep) and o.name == "evil name" and o.folder == "etc/x", (o.sgm, o.name, o.folder)
    # not an entity
    cube = bpy.data.objects.new("plain cube", bpy.data.meshes.new("plain cube"))
    bpy.context.collection.objects.link(cube)
    refused(lambda: BP.export_entity(bpy.context, cube, out), "imported or started with this add-on")
    print("   the game's folder, a Steam folder, no folder, a file, a long name, an empty / long / accented sound, a path out of the folder, a plain cube: "
          "all refused with a reason", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 7. where the folder comes from
def test_folders(lib):
    section("7. the folder in the game")
    P = Project("STRUCTURE", LAB, "Where")
    for o in bpy.data.objects:
        if o.get("bz_scene"):
            o["bz_donor"] = "art/ebps/nature/swamp/some_tree.sgm"            # what New Project is asked to stamp (see the hand-off)
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    assert (P.bp.folder, P.bp.folder_source) == ("nature/swamp", "STAMP"), (P.bp.folder, P.bp.folder_source)
    for o in bpy.data.objects:
        if o.get("bz_scene"):
            del o["bz_donor"]
    # files that lie in a Data/art/ebps tree: the tree says
    tree = os.path.join(C.tmpdir("poc_tree_"), "Data", "Art", "EBPs", "Structures")
    os.makedirs(tree)
    with open(os.path.join(tree, "treelab.sgm"), "wb") as f:
        f.write(P.sgm_bytes)
    with open(os.path.join(tree, "treelab.ebp"), "wb") as f:
        f.write(P.ebp_bytes)
    assert "FINISHED" in bpy.ops.bz.import_sgm(filepath=os.path.join(tree, "treelab.sgm"))
    ent = BP.entity_of(bpy.context.view_layer.objects.active)
    assert ent.source.endswith("treelab.sgm") and BP.blueprint_path(ent).endswith("treelab.ebp"), (ent.source, BP.blueprint_path(ent))
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    bp = ent.holder.bz_blueprint
    assert (bp.folder, bp.folder_source) == ("structures", "PATH"), (bp.folder, bp.folder_source)
    said = draw(bpy.context.view_layer.objects.active).said()
    assert "Where the game finds it" in said and "This is where the files lie." in said, said
    # no stamp, no tree, not a stock file: a guess from the kind, and the panel says it is a guess
    unknown = types.SimpleNamespace(parts=[{"bz_project": ""}], source="/nowhere/unknown.bin")
    for cls, expect in ((3, "gatherers"), (21, "characters"), (5, "structures"), (0, "structures")):
        assert BP.determine_folder(unknown, None, cls, False, False) == (expect, "GUESS"), cls
    stray = types.SimpleNamespace(parts=[{"bz_project": "CHARACTER"}], source="/nowhere/unknown.sgm")
    assert BP.determine_folder(stray, None, 0, True, True)[1] == "GUESS"
    print("   stamp (bz_donor) > where the files lie > the stock thing the file is a copy of (checked in section 3) > a guess from the kind", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 9. every stock blueprint
def all_stock_ebps():
    """(path, bytes) of every blueprint under art/ebps of every game archive, the first archive to hold a path winning (as the game's search does)."""
    seen = set()
    for archive in newproject.game_archives():
        arc = newproject._archive(archive)
        for e in arc.entries:
            low = e.path.lower().replace("\\", "/")
            if low.startswith("art/ebps/") and low.endswith(".ebp") and low not in seen:
                seen.add(low)
                yield low, arc.read_path(low)


def test_corpus(lib):
    section("9. every stock blueprint through the panel's values")
    obj = bpy.data.objects.new("sweep", None)
    bpy.context.collection.objects.link(obj)
    bp = obj.bz_blueprint
    path = os.path.join(C.tmpdir("poc_corpus_"), "x.ebp")
    n, problems = Counter(), []
    for low, raw in all_stock_ebps():
        try:
            sb = lib.SceneBlueprint.read(raw)
            state, _note = BP.classify(sb)
            values = BP.values_of(sb)
        except Exception as e:                                               # noqa: BLE001 -- counted and listed, never hidden
            n["the reader refuses"] += 1
            problems.append((low, f"the reader refuses: {str(e)[:60]}"))
            continue
        n[state] += 1
        try:
            left = BP.fill_pg(bp, values)
        except (ValueError, TypeError, OverflowError, UnicodeError) as e:
            left = [f"refused by Blender: {e}"]
        if left or BP.problems_of(values):
            problems.append((low, f"the panel cannot hold {left} / {BP.problems_of(values)}"))
            continue
        with open(path, "wb") as f:
            f.write(raw)
        bp.source_path, bp.source_sha1, bp.state = path, hashlib.sha1(raw).hexdigest(), state
        if state == "EMBEDDED":
            refused(lambda: BP.blueprint_bytes(None, bp), "inside itself")
            n["refused (no .sgm to write)"] += 1
            continue
        out, changes, _how = BP.blueprint_bytes(None, bp)                    # untouched: the file as it was
        if out != raw or changes:
            problems.append((low, "an untouched export changed the file"))
            continue
        n["untouched: byte-identical"] += 1
        if state != "FILE":
            continue
        bp.display_name = "Sweep Name"                                       # every field edited at once
        bp.class_id = (values.class_id + 1) % 40
        bp.footprint_x, bp.footprint_z = (values.footprint[0] + 1) % 256, (values.footprint[1] + 1) % 256
        bp.offset = tuple(v + 1.0 for v in values.offset)
        bp.sounds.add().path = "DATA:AUDIO\\SWEEP.PAT"
        out, changes, _how = BP.blueprint_bytes(None, bp)
        back = BP.values_of(lib.SceneBlueprint.read(out))
        if changes != list(BP.FIELDS) or BP.changed_fields(back, BP.values_from_pg(bp)):
            problems.append((low, f"the edited blueprint does not read back: {changes}"))
            continue
        try:
            tags = changed_tags(raw, out)                                    # every other chunk must be identical and in place
        except AssertionError as e:
            problems.append((low, str(e)))
            continue
        if not set(tags) <= {"EBPD", "BLDI", "PIDS", "FORM EBPC"}:                # (the form around a footprint chunk that the file did not have)
            problems.append((low, f"unexpected chunks changed: {tags}"))
            continue
        n["edited: only header, footprint, sounds changed"] += 1
    assert not problems, f"{len(problems)} of {sum(v for k, v in n.items() if k in ('FILE', 'LEGACY', 'EMBEDDED', 'the reader refuses'))} blueprints: {problems[:5]}"
    assert n["FILE"] > 300 and n["untouched: byte-identical"] == n["FILE"] + n["LEGACY"] and n["edited: only header, footprint, sounds changed"] == n["FILE"], dict(n)
    print(f"   {sum(n[k] for k in ('FILE', 'LEGACY', 'EMBEDDED'))} stock blueprints: {dict(n)}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 8. panels
def test_panels():
    section("8. panel polls and the draw of every state")
    P = Project("STRUCTURE", LAB, "Panels")

    def ctx(o):
        return types.SimpleNamespace(active_object=o)
    assert BP.BZ_PT_blueprint.poll(ctx(P.obj)) and BP.BZ_PT_blueprint.poll(ctx(P.entity().holder)) and not BP.BZ_PT_blueprint.poll(ctx(None))
    cube = bpy.data.objects.new("plain cube", bpy.data.meshes.new("plain cube"))
    assert not BP.BZ_PT_blueprint.poll(ctx(cube)) and not BP.BZ_PT_blueprint_stats.poll(ctx(cube))
    assert not BP.BZ_PT_blueprint_stats.poll(ctx(P.obj)), "no numbers panel before the blueprint is loaded"
    lay = CheckedLayout()
    BP.draw_blueprint(lay, cube)
    assert lay.said() == "Select a gatherer, character, structure or prop." and not lay.problems
    assert "FINISHED" in bpy.ops.bz.blueprint_load()
    assert BP.BZ_PT_blueprint_stats.poll(ctx(P.obj))
    for st in BP.STATES:                                              # whatever state a blueprint is in, drawing it must not raise
        P.bp.state = st
        lay = draw(P.obj)
        assert lay.calls and not jargon(lay.said()), (st, jargon(lay.said()))
        draw(P.obj, "stats")
    assert BP.BZ_PT_blueprint.bl_category == "SGM" and BP.BZ_PT_blueprint_stats.bl_parent_id == "BZ_PT_blueprint"
    # the panels' own draw methods (the glue between Blender's context and draw_blueprint / draw_stats)
    P.bp.state = "FILE"
    for cls in (BP.BZ_PT_blueprint, BP.BZ_PT_blueprint_stats):
        stub = types.SimpleNamespace(layout=CheckedLayout())
        cls.draw(stub, types.SimpleNamespace(active_object=P.obj))
        assert stub.layout.calls and not stub.layout.problems, (cls.__name__, stub.layout.problems)
    # opening the folder chooser: the name and folder offered belong to this entity (and the blueprint is loaded to know the folder)
    P2 = Project("STRUCTURE", LAB, "Chooser")
    asked = []
    wm = types.SimpleNamespace(fileselect_add=lambda op: asked.append(op))
    stub = types.SimpleNamespace(entity_name="left over from the last export", folder="elsewhere")
    assert not P2.bp.loaded
    assert BP.BZ_OT_export_entity_folder.invoke(stub, types.SimpleNamespace(active_object=P2.obj, window_manager=wm), None) == {"RUNNING_MODAL"}
    assert (stub.entity_name, stub.folder) == ("chooser", "structures") and asked == [stub] and P2.bp.loaded
    props = bpy.ops.bz.export_entity_folder.get_rna_type().properties
    assert props["entity_name"].is_skip_save and props["folder"].is_skip_save and not props["directory"].is_skip_save, "only the folder to write into is remembered"
    # the options of the folder chooser: drawn with the same checks
    for name, folder in (("lab", "structures"), ("anything", ""), ("", "nature/swamp")):
        stub = types.SimpleNamespace(layout=CheckedLayout(), entity_name=name, folder=folder, bl_rna=bpy.ops.bz.export_entity_folder.get_rna_type())
        BP.BZ_OT_export_entity_folder.draw(stub, bpy.context)
        assert not stub.layout.problems, stub.layout.problems
        said = stub.layout.said()
        assert (name == "lab" and folder == "structures") == ("REPLACE the original" in said), said
        assert not jargon(said), jargon(said)
    print(f"   polls for a part, the rig, nothing and a cube; all {len(BP.STATES)} states drawn without a problem and without an internal word", flush=True)


# ------------------------------------------------------------------------------------------------------------------ run
def run():
    lib, stats = BP.scene_lib(), BP.stats_lib()
    root = C.install_root()
    test_registration()
    test_helpers()
    if root is None:
        C.skip("no game install found (set IC_INSTALL): the projects from stock donors are not checked")
        BP.unregister()
        return
    if lib is None:
        C.skip("ICParse.ebp.scene (SceneBlueprint) is not installed yet: loading, editing and exporting a blueprint are not checked")
        BP.unregister()
        return
    with C.install_guard(root):
        test_flow("CHARACTER", HENCH, "Hench", lib, stats)
        test_flow("STRUCTURE", LAB, "NewLab", lib, stats)
        test_flow("STRUCTURE", AVIARY, "NewAviary", lib, stats)
        test_replace(stats)
        test_game_folder(stats)
        test_no_file(lib)
        test_read_only(lib)
        test_refusals(lib)
        test_folders(lib)
        test_corpus(lib)
        test_panels()
    BP.unregister()
    assert not C.has_api(bpy.types.Object, "bz_blueprint")
    section("PASS")


C.run_test("blueprint_poc", run)
