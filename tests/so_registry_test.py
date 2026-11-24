"""sceneobj registry, UI kit and preferences (slice B00), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_registry_test.py     (or: run_all.py -k so_registry)

1. what is discovered now: core modules in order, no import errors, no contract problems, CLASSES = the modules' classes;
2. classes registered == classes defined: every Blender class a sceneobj module defines is in CLASSES (or the module says it is abstract);
3. blender_native.register() registers them and unregister() leaves bpy.types clean (the creature panel too);
4. discovery on throw-away modules written to a temp folder: (ORDER, name) order, a property group before the group that points at it,
   a child panel after its parent, hooks after the classes and reversed on unregister, a module that fails to import is reported and the
   rest load, a failed registration and a failed hook are rolled back completely, reload() picks up an edit and a removed module;
5. blender_native._reload() twice leaves no stale module (fresh classes, ui draws with the reloaded kit);
6. ui_kit: wrap_lines is the creature panels' old wrapping, alert / issue_rows / BZ_UL_rows / SubPanel / draw_scene_pointer drawn into a
   FakeLayout, and the edited BZ_PT_panel.draw for a scene object, a plain object and nothing selected;
7. prefs: defaults agree with the registered class, get() falls back to the defaults, the folder choices, draw().
"""
import importlib
import importlib.machinery
import os
import random
import sys
import types
from types import SimpleNamespace

sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree (a fixture edited twice in a second must not hit a stale .pyc)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)
import blender_native  # noqa: E402  (imported, not registered yet)
from blender_native import sceneobj as so  # noqa: E402
from blender_native.sceneobj import model, prefs, ui_kit  # noqa: E402

CORE_ALL = ("model", "subtype", "entity", "ui_kit", "prefs", "importer", "exporter", "compat")


def section(title):
    print(f"-- {title}", flush=True)


def short(m):
    return m.__name__.rsplit(".", 1)[-1]


def creature_panel_registered():
    return bpy.types.Panel.bl_rna_get_subclass_py("BZ_PT_panel") is not None


# ------------------------------------------------------------------------------------------------------------------ 1. discovery
def test_discovered():
    section("1. what is discovered now")
    names = [short(m) for m in so.MODULES]
    assert names[0] == "model" and {"model", "ui_kit", "prefs"} <= set(names), names
    assert so.CORE == CORE_ALL and so.PREFIXES == ("slice_", "ops_", "panels_", "props_", "guards_", "map_"), (so.CORE, so.PREFIXES)
    assert set(so.MISSING_CORE) <= {"subtype", "entity", "importer", "exporter", "compat"}, f"a core module of an earlier slice vanished: {so.MISSING_CORE}"
    core_present = [n for n in CORE_ALL if n in names]
    assert [n for n in names if n in CORE_ALL] == core_present, f"core modules out of dependency order: {names}"
    assert names[:len(core_present)] == core_present, "the discovered modules must come after the core ones"
    extra = [m for m in so.MODULES if short(m) not in CORE_ALL]
    assert [(getattr(m, "ORDER", 50), m.__name__) for m in extra] == sorted((getattr(m, "ORDER", 50), m.__name__) for m in extra), "discovered modules not in (ORDER, name) order"
    assert all(short(m).startswith(so.PREFIXES) for m in extra), [short(m) for m in extra]
    assert so.ERRORS == [] and so.status()["problems"] == [], (so.ERRORS, so.status()["problems"])
    assert isinstance(so.CLASSES, tuple) and len(set(so.CLASSES)) == len(so.CLASSES)
    # CLASSES is exactly the modules' classes (reordered, never added to or dropped from)
    listed = [c for m in so.MODULES for c in getattr(m, "CLASSES", ())]
    assert sorted(c.__name__ for c in so.CLASSES) == sorted(c.__name__ for c in listed)
    assert callable(so.discover) and callable(so.register) and callable(so.unregister) and callable(so.reload)
    assert so.registered_classes() == (), "nothing may be registered before register() is called"
    print(f"   {len(so.MODULES)} modules, {len(so.CLASSES)} classes, missing core: {list(so.MISSING_CORE)}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 2. defined == registered
def defined_classes(mod):
    """The Blender classes ``mod`` defines that must be in CLASSES; a class that cannot be registered (no bl_idname) must say so."""
    abstract = tuple(getattr(mod, "ABSTRACT", ()))
    out, problems = [], []
    for name, obj in vars(mod).items():
        if not isinstance(obj, type) or obj.__module__ != mod.__name__ or so._base_type(obj) is None:
            continue
        if name in abstract:
            continue
        base = so._base_type(obj)
        if base.__name__ != "PropertyGroup" and not getattr(obj, "bl_idname", ""):
            problems.append(f"{mod.__name__}.{name} has no bl_idname: list it in the module's ABSTRACT tuple, or give it one")
        else:
            out.append(obj)
    return out, problems


def test_defined_equals_listed():
    section("2. classes registered == classes defined")
    defined, problems = [], []
    for m in so.MODULES:
        d, p = defined_classes(m)
        defined += d
        problems += p
        for c in getattr(m, "CLASSES", ()):
            assert c.__module__ == m.__name__, f"{c.__name__} is listed by {m.__name__} but defined in {c.__module__}"
    assert not problems, problems
    assert set(defined) == set(so.CLASSES), (sorted(c.__name__ for c in set(defined) ^ set(so.CLASSES)))
    assert "SubPanel" in ui_kit.ABSTRACT and ui_kit.SubPanel not in so.CLASSES, "the SubPanel base class is never registered"
    assert ui_kit.BZ_UL_rows in so.CLASSES and prefs.BZ_AP_prefs in so.CLASSES


# ------------------------------------------------------------------------------------------------------------------ 3. register / unregister
def test_register_unregister():
    section("3. blender_native.register() / unregister()")
    assert not creature_panel_registered()
    blender_native.register()
    try:
        assert creature_panel_registered(), "the creature panel must still register"
        assert so.registered_classes() == so.CLASSES, "register() registers exactly CLASSES, in order"
        for c in so.CLASSES:
            assert so.is_registered(c), c.__name__
            assert c.bl_rna.identifier == so.rna_identifier(c), (c.__name__, c.bl_rna.identifier, so.rna_identifier(c))
        idents = [(so._base_type(c), so.rna_identifier(c)) for c in so.CLASSES]
    finally:
        blender_native.unregister()
    assert not creature_panel_registered() and so.registered_classes() == ()
    for base, ident in idents:
        assert base.bl_rna_get_subclass_py(ident) is None, f"{ident} is still registered after unregister()"
        assert getattr(bpy.types, ident, None) is None, f"bpy.types.{ident} is still there after unregister()"
    assert not any(so.is_registered(c) for c in so.CLASSES)
    so.unregister()                                           # unregistering when nothing is registered is fine
    blender_native.register()                                  # and the add-on can come back
    assert so.registered_classes() == so.CLASSES
    blender_native.unregister()
    assert so.registered_classes() == ()

    # a failure inside sceneobj.unregister() must not strand the creature half of the add-on
    blender_native.register()
    real = so.unregister

    def boom():
        raise RuntimeError("fixture unregister failure")

    so.unregister = boom
    try:
        try:
            blender_native.unregister()
        except RuntimeError as e:
            assert "fixture unregister failure" in str(e)
        else:
            raise AssertionError("the failure of sceneobj.unregister() must reach the caller")
    finally:
        so.unregister = real
    assert not creature_panel_registered(), "the creature panel must be unregistered even though sceneobj failed"
    real()                                                     # now really undo what sceneobj registered
    assert so.registered_classes() == () and not any(so.is_registered(c) for c in so.CLASSES)


# ------------------------------------------------------------------------------------------------------------------ 4. fixtures
FIX_PGS = '''
import sys
import bpy
from bpy.props import CollectionProperty, PointerProperty, IntProperty

ORDER = 10
VALUE = 1


class BZ_PG_zz_inner(bpy.types.PropertyGroup):
    v: IntProperty()


class BZ_PG_zz_outer(bpy.types.PropertyGroup):
    inner: PointerProperty(type=BZ_PG_zz_inner)


CLASSES = (BZ_PG_zz_outer, BZ_PG_zz_inner)          # the group that points comes first: the registry must reorder


def register():
    sys._so_fixture_calls.append("props.register")
    bpy.types.Scene.bz_zz_outer = PointerProperty(type=BZ_PG_zz_outer)      # needs the classes registered before the hook runs


def unregister():
    sys._so_fixture_calls.append("props.unregister")
    del bpy.types.Scene.bz_zz_outer
'''

FIX_PGS2 = '''
from __future__ import annotations
import bpy
from bpy.props import CollectionProperty, IntProperty

ORDER = 11


class BZ_PG_zz_inner2(bpy.types.PropertyGroup):
    v: IntProperty()


class BZ_PG_zz_outer2(bpy.types.PropertyGroup):
    rows: CollectionProperty(type=BZ_PG_zz_inner2)          # text annotations (from __future__ import annotations)


CLASSES = (BZ_PG_zz_outer2, BZ_PG_zz_inner2)
'''

FIX_CHILD = '''
import bpy
from .ui_kit import SubPanel


class BZ_PT_zz_child(SubPanel):
    bl_idname = "BZ_PT_zz_child"
    bl_label = "Child"
    bl_order = 5
    bl_parent_id = "BZ_PT_zz_parent"
    capability = "zz"

    def draw(self, context):
        self.layout.label(text="child")


CLASSES = (BZ_PT_zz_child,)
'''

FIX_PARENT = '''
import bpy


class BZ_PT_zz_parent(bpy.types.Panel):
    bl_idname = "BZ_PT_zz_parent"
    bl_label = "Parent"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"

    def draw(self, context):
        self.layout.label(text="parent")


CLASSES = (BZ_PT_zz_parent,)
'''

FIX_SLICE = '''
import sys
from .model import BaseSlice, claim_edit

ORDER = 60


class ZzSlice(BaseSlice):
    name = "zz_fixture"
    order = 777
    claims = ("DATABLOCK:zz",)


SLICE = ZzSlice()
claim_edit("bounds_stale")


def register():
    sys._so_fixture_calls.append("slice.register")


def unregister():
    sys._so_fixture_calls.append("slice.unregister")
'''

FIX_BROKEN = '''
raise RuntimeError("fixture import failure")
'''

FIX_HELPER = '''
HELPER_VALUE = 1                                  # a helper module: not registered, no prefix, imported by a registered module
'''

FIX_USER = '''
from .zz_helper import HELPER_VALUE                # a name taken from the helper: it is only fresh if the helper reloads first

ORDER = 70
SEEN = HELPER_VALUE
'''

FIX_BAD_PG = '''
import bpy

ORDER = 20


class BZ_PG_zz_target(bpy.types.PropertyGroup):
    v: bpy.props.IntProperty()


class BZ_PG_zz_bad(bpy.types.PropertyGroup):
    t: bpy.props.PointerProperty(type=BZ_PG_zz_target)


CLASSES = (BZ_PG_zz_bad,)          # the target is not listed, so Blender refuses to register this one
'''

FIX_ORPHAN = '''
from .ui_kit import SubPanel


class BZ_PT_zz_orphan(SubPanel):
    bl_idname = "BZ_PT_zz_orphan"
    bl_label = "Orphan"
    bl_order = 9
    bl_parent_id = "BZ_PT_zz_nobody"                 # there is no such panel
    capability = "zz"

    def draw(self, context):
        pass


CLASSES = (BZ_PT_zz_orphan,)
'''

FIX_HOOK_FAIL = '''
import sys

ORDER = 90


def register():
    sys._so_fixture_calls.append("hookfail.register")
    raise RuntimeError("fixture hook failure")


def unregister():
    sys._so_fixture_calls.append("hookfail.unregister")
'''

FIXTURES = {"props_zz_pgs": FIX_PGS, "props_zz_pgs2": FIX_PGS2, "panels_aa_child": FIX_CHILD, "panels_zz_parent": FIX_PARENT,
            "slice_zz_fixture": FIX_SLICE, "ops_zz_broken": FIX_BROKEN, "zz_helper": FIX_HELPER, "guards_zz_user": FIX_USER}


def write_fixture(folder, name, text):
    with open(os.path.join(folder, name + ".py"), "w") as f:
        f.write(text)
    importlib.invalidate_caches()


def drop_fixtures(folder, names, forget=True):
    """Delete the fixture files; ``forget`` also drops the imported modules (False: only the files go, as when a file is renamed)."""
    for n in names:
        path = os.path.join(folder, n + ".py")
        if os.path.exists(path):
            os.remove(path)
        if forget:
            sys.modules.pop(f"{so.__name__}.{n}", None)
            if hasattr(so, n):
                delattr(so, n)
    importlib.invalidate_caches()


def test_discovery_fixtures():
    section("4. discovery, ordering and rollback on throw-away modules")
    sys._so_fixture_calls = calls = []
    slices_before, claims_before, errors_before = [s.name for s in model.slices()], model.claimed_codes(), list(so.ERRORS)
    folder = C.tmpdir("fixtures_")
    so.__path__.append(folder)                                  # the package now also finds modules in this folder
    try:
        for n, text in FIXTURES.items():
            write_fixture(folder, n, text)
        print("   (the tracebacks and the PointerProperty message below come from the deliberately broken fixture modules)", flush=True)
        so.discover()
        names = [short(m) for m in so.MODULES]
        fx = [n for n in names if n in FIXTURES]
        assert fx == ["props_zz_pgs", "props_zz_pgs2", "panels_aa_child", "panels_zz_parent", "slice_zz_fixture", "guards_zz_user"], fx      # (ORDER, name) order
        assert "zz_helper" not in names, "a helper module without a prefix is imported by its user, not registered"
        assert sys.modules[f"{so.__name__}.guards_zz_user"].SEEN == 1
        broken = ("ops_zz_broken", "RuntimeError: fixture import failure")
        assert "ops_zz_broken" not in names and so.ERRORS == errors_before + [broken], so.ERRORS     # reported, and nothing else went wrong
        assert [s.name for s in model.slices()] == sorted(slices_before + ["zz_fixture"], key=lambda n: model.get_slice(n).order)
        assert model.claimed_by("bounds_stale").endswith("slice_zz_fixture")
        assert model.registry_problems() == [], model.registry_problems()

        # registration order: groups after what they point at, a child panel after its parent, whatever the module order says
        order = [c.__name__ for c in so.CLASSES]
        assert order.index("BZ_PG_zz_inner") < order.index("BZ_PG_zz_outer"), order
        assert order.index("BZ_PG_zz_inner2") < order.index("BZ_PG_zz_outer2"), order
        assert order.index("BZ_PT_zz_parent") < order.index("BZ_PT_zz_child"), order
        pgs = [i for i, c in enumerate(so.CLASSES) if issubclass(c, bpy.types.PropertyGroup)]
        assert pgs == list(range(len(pgs))), "every property group comes before every other class"
        # (the modules themselves sort the other way: panels_aa_child comes before panels_zz_parent)
        assert names.index("panels_aa_child") < names.index("panels_zz_parent")

        # everything defined in the fixtures is listed (and the abstract SubPanel base is not)
        for m in so.MODULES:
            if "zz" in short(m) or "aa" in short(m):
                d, p = defined_classes(m)
                assert not p and set(d) == set(getattr(m, "CLASSES", ())), (m.__name__, d, p)

        so.register()
        assert calls == ["props.register", "slice.register"], calls              # hooks run after all classes, in module order
        assert so.registered_classes() == so.CLASSES and all(so.is_registered(c) for c in so.CLASSES)
        assert C.has_api(bpy.types.Scene, "bz_zz_outer"), "the hook could point a property at its class"
        outer = sys.modules[f"{so.__name__}.props_zz_pgs"].BZ_PG_zz_outer
        assert so.is_registered(outer) and not so.is_registered(ui_kit.SubPanel)
        so.register()                                                              # again without unregistering: starts clean, same end state
        assert calls == ["props.register", "slice.register", "slice.unregister", "props.unregister", "props.register", "slice.register"], calls
        del calls[:]
        so.unregister()
        assert calls == ["slice.unregister", "props.unregister"], calls            # reversed
        assert so.registered_classes() == () and not any(so.is_registered(c) for c in so.CLASSES)
        assert not C.has_api(bpy.types.Scene, "bz_zz_outer")
        for c in so.CLASSES:
            assert so._base_type(c).bl_rna_get_subclass_py(so.rna_identifier(c)) is None

        # a class Blender refuses: nothing stays registered, the refused class leaves no residue, the add-on can register again
        write_fixture(folder, "props_zz_bad", FIX_BAD_PG)
        so.discover()
        del calls[:]
        try:
            so.register()
        except ValueError as e:
            assert "BZ_PG_zz_bad" in str(e) or "PointerProperty" in str(e), e
        else:
            raise AssertionError("a property group that points at an unregistered group must not register")
        assert so.registered_classes() == () and calls == [], calls                  # and no hook ran
        assert not any(so.is_registered(c) for c in so.CLASSES), "a failed registration must roll back every class"
        drop_fixtures(folder, ["props_zz_bad"])
        so.discover()
        so.register()
        so.unregister()

        # a sub-panel whose parent panel does not exist: that one panel is left out and reported, the rest registers
        write_fixture(folder, "panels_zz_orphan", FIX_ORPHAN)
        so.discover()
        orphan = sys.modules[f"{so.__name__}.panels_zz_orphan"].BZ_PT_zz_orphan
        assert orphan in so.CLASSES and so.ERRORS == errors_before + [broken], so.ERRORS     # discovery alone does not mind
        print("   (the line below is the deliberately orphaned sub-panel)", flush=True)
        so.register()
        assert so.ERRORS == errors_before + [broken, ("panels_zz_orphan", "the panel BZ_PT_zz_orphan needs the panel BZ_PT_zz_nobody, which is not registered")], so.ERRORS
        assert not so.is_registered(orphan) and orphan not in so.registered_classes()
        assert [c for c in so.CLASSES if c is not orphan] == list(so.registered_classes()), "everything else registered, in order"
        so.unregister()
        assert so.registered_classes() == ()
        drop_fixtures(folder, ["panels_zz_orphan"])
        so.discover()

        # a hook that fails: the classes and the hooks that already ran are undone
        write_fixture(folder, "props_zz_hookfail", FIX_HOOK_FAIL)
        so.discover()
        del calls[:]
        try:
            so.register()
        except RuntimeError as e:
            assert "fixture hook failure" in str(e)
        else:
            raise AssertionError("a failing register() hook must make register() fail")
        assert calls == ["props.register", "slice.register", "hookfail.register", "slice.unregister", "props.unregister"], calls
        assert so.registered_classes() == () and not any(so.is_registered(c) for c in so.CLASSES)
        assert not C.has_api(bpy.types.Scene, "bz_zz_outer")
        drop_fixtures(folder, ["props_zz_hookfail"])

        # reload(): an edited module runs its new code, a removed module is gone, a new one appears
        so.discover()
        mod = sys.modules[f"{so.__name__}.props_zz_pgs"]
        assert mod.VALUE == 1
        write_fixture(folder, "props_zz_pgs", FIX_PGS.replace("VALUE = 1", "VALUE = 2"))
        write_fixture(folder, "zz_helper", FIX_HELPER.replace("= 1", "= 5"))                 # an unregistered helper, edited
        drop_fixtures(folder, ["panels_zz_parent", "panels_aa_child"], forget=False)       # only the files go: reload() must cope
        write_fixture(folder, "panels_zz_later", FIX_PARENT.replace("BZ_PT_zz_parent", "BZ_PT_zz_later").replace("Parent", "Later"))
        so.reload()
        names = [short(m) for m in so.MODULES]
        assert sys.modules[f"{so.__name__}.props_zz_pgs"].VALUE == 2 and "panels_zz_later" in names, names
        assert sys.modules[f"{so.__name__}.guards_zz_user"].SEEN == 5, "a helper must reload before the module that took a name from it"
        assert "panels_zz_parent" not in names and "panels_aa_child" not in names, names
        assert "zz_fixture" in [s.name for s in model.slices()], "the slice registry is rebuilt on reload"
        assert so.ERRORS == errors_before + [broken], so.ERRORS      # the broken module is reported again; the removed ones are not
        assert f"{so.__name__}.panels_zz_parent" not in sys.modules
        so.register()
        so.unregister()
    finally:
        if so.registered_classes():
            so.unregister()
        so.__path__.remove(folder)
        drop_fixtures(folder, [n[:-3] for n in os.listdir(folder) if n.endswith(".py")])
        model.forget_claims(f"{so.__name__}.slice_zz_fixture")
        so.discover()
        del sys._so_fixture_calls
    assert not any(short(m) in FIXTURES or "zz" in short(m) for m in so.MODULES) and so.ERRORS == errors_before and so.status()["problems"] == [], so.status()
    assert model.claimed_codes() == claims_before and [s.name for s in model.slices()] == slices_before


# ------------------------------------------------------------------------------------------------------------------ 5. reload
def test_reload():
    section("5. reloading the add-on twice (what Blender does when it is enabled again in place)")
    ui = importlib.import_module("blender_native.ui")
    for n in (1, 2):
        snap = {"OutBundle": model.OutBundle, "BZ_UL_rows": ui_kit.BZ_UL_rows, "BZ_AP_prefs": prefs.BZ_AP_prefs, "alert": ui_kit.alert,
                "classes": so.CLASSES}
        importlib.reload(blender_native)             # re-runs __init__: every module is reloaded (sceneobj's just before ui), _CLASSES is rebuilt
        assert so.ERRORS == [] and so.status()["problems"] == [], (n, so.status())
        assert model.OutBundle is not snap["OutBundle"] and ui_kit.BZ_UL_rows is not snap["BZ_UL_rows"] and prefs.BZ_AP_prefs is not snap["BZ_AP_prefs"], \
            f"reload {n}: the sceneobj modules were not run again"
        assert ui_kit.alert is not snap["alert"]
        for m in so.MODULES:
            assert sys.modules[m.__name__] is m, f"{m.__name__}: the registry holds a module that is not the imported one"
        for c in so.CLASSES:
            assert c is getattr(sys.modules[c.__module__], c.__name__), f"{c.__name__} in CLASSES is a stale class object"
        assert [c.__name__ for c in so.CLASSES] == [c.__name__ for c in snap["classes"]] and all(a is not b for a, b in zip(so.CLASSES, snap["classes"]))
        assert ui._alert is ui_kit.alert, "ui must draw with the reloaded kit (sceneobj reloads before ui)"
    blender_native.register()                        # the add-on registers with the reloaded classes ...
    assert so.registered_classes() == so.CLASSES and all(so.is_registered(c) for c in so.CLASSES)
    blender_native.unregister()                      # ... and leaves nothing behind
    assert so.registered_classes() == () and not any(so.is_registered(c) for c in so.CLASSES)
    assert not creature_panel_registered()


# ------------------------------------------------------------------------------------------------------------------ 6. ui_kit
def old_alert_lines(text, width=60):
    """The wrapping ui._alert had before it moved to the kit (copied from the old source, for comparison)."""
    lines, first, line = [], True, ""
    for w in text.split():
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            first, line = False, w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


class FakeEntity(types.ModuleType):
    """A stand-in for sceneobj.entity (slice B01): ``has`` says yes to the capabilities given, ``find_entity`` to the objects given."""

    def __init__(self, caps=(), objs=()):
        super().__init__(f"{so.__name__}.entity")
        self.__spec__ = importlib.machinery.ModuleSpec(self.__name__, None)
        self.caps, self.objs = set(caps), list(objs)

    def has(self, context, capability):
        return capability in self.caps

    def find_entity(self, obj):
        return obj if obj in self.objs else None


class with_entity:
    """``with with_entity(FakeEntity(...)):`` puts the stand-in where ``sceneobj.entity`` would be, and puts the real one back."""

    def __init__(self, fake):
        self.fake, self.key = fake, f"{so.__name__}.entity"

    def __enter__(self):
        self.had = sys.modules.get(self.key)
        sys.modules[self.key] = self.fake

    def __exit__(self, *exc):
        if self.had is None:
            del sys.modules[self.key]
        else:
            sys.modules[self.key] = self.had


def scene_part(name, verts=3, rig=None, project="STRUCTURE", source="/x/lab.sgm"):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(i, 0, 0) for i in range(verts)], [], [])
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    obj["bz_scene"], obj["bz_source"], obj["bz_name"], obj["bz_project"] = True, source, "lab", project
    if rig:
        obj["bz_rig"] = rig
    return obj


def test_ui_kit():
    section("6. ui_kit")
    C.fresh()
    rnd = random.Random(7)
    words = ["a", "bb", "ccc", "dddd", "eeeee", "x" * 59, "y" * 61, "z" * 130, "word", "longerwordhere"]
    for _ in range(300):
        text = " ".join(rnd.choice(words) for _ in range(rnd.randint(0, 40)))
        assert ui_kit.wrap_lines(text) == old_alert_lines(text), text
        assert ui_kit.wrap_lines(text, 25) == old_alert_lines(text, 25)
    assert ui_kit.wrap_lines("") == [] and ui_kit.wrap_lines("   \n ") == []

    lay = C.FakeLayout()
    ui_kit.alert(lay, "the first words of an alert " * 6)
    labs = lay.labels()
    assert len(labs) > 1 and labs[0]["icon"] == "ERROR" and all(d["icon"] == "BLANK1" for d in labs[1:]) and all(d["alert"] for d in labs)
    assert [d["text"] for d in labs] == old_alert_lines("the first words of an alert " * 6)
    lay = C.FakeLayout()
    ui_kit.alert(lay, "short", icon="INFO", width=60)
    assert lay.labels() == [dict(text="short", icon="INFO", icon_value=0, alert=True)]

    fs = [model.issue("w1", "WARN", "a warning", "part 3"), model.issue("e1", "ERROR", "an error that is long " * 5, "bone x"),
          model.issue("i1", "INFO", "note"), model.issue("e2", "ERROR", "second error mentioning bone y", "bone y")]
    lay = C.FakeLayout()
    assert ui_kit.issue_rows(lay, fs) == 4
    labs = lay.labels()
    assert labs[0]["alert"] and labs[0]["text"].startswith("bone x: an error") and labs[0]["icon"] == "ERROR", labs[0]
    levels = [("alert" if d["alert"] else d["icon"]) for d in labs if d["icon"] != "BLANK1"]
    assert levels == ["alert", "alert", "INFO", "DOT"], levels               # worst first: errors, the warning, the info
    assert [d["text"] for d in labs if d["alert"]][-1] == "second error mentioning bone y"       # `where` is not repeated when the text has it
    assert any(d["text"] == "part 3: a warning" for d in labs)
    lay = C.FakeLayout()
    assert ui_kit.issue_rows(lay, fs, limit=1) == 1 and lay.texts()[-1] == "... and 3 more"
    lay = C.FakeLayout()
    assert ui_kit.issue_rows(lay, []) == 0 and lay.calls == []

    # the list: one registered class, drawers by list id, a default row
    seen = []

    @ui_kit.row_drawer("zz_list")
    def draw_zz(ui_list, context, layout, data, item, icon, active_data, active_propname, index):
        seen.append((ui_list.list_id, item, index))
        layout.label(text="zz " + item.name)

    item = SimpleNamespace(name="Part A")
    lay = C.FakeLayout()
    ui_kit.BZ_UL_rows.draw_item(SimpleNamespace(list_id="zz_list", layout_type="DEFAULT"), None, lay, None, item, 5, None, "active", 2)
    assert seen == [("zz_list", item, 2)] and lay.texts() == ["zz Part A"]
    lay = C.FakeLayout()
    ui_kit.BZ_UL_rows.draw_item(SimpleNamespace(list_id="other", layout_type="DEFAULT"), None, lay, None, item, 7, None, "active")
    assert lay.labels() == [dict(text="Part A", icon="NONE", icon_value=7, alert=False)]
    lay = C.FakeLayout()
    ui_kit.BZ_UL_rows.draw_item(SimpleNamespace(list_id="other", layout_type="GRID"), None, lay, None, item, 7, None, "active")
    assert lay.labels() == [dict(text="", icon="NONE", icon_value=7, alert=False)] and lay.alignment == "CENTER"
    del ui_kit.ROW_DRAWERS["zz_list"]

    # the sub-panel base: capability decides, never the subtype
    sp = ui_kit.SubPanel
    assert (sp.bl_parent_id, sp.bl_space_type, sp.bl_region_type, sp.bl_category) == ("BZ_PT_entity", "VIEW_3D", "UI", "SGM") and sp.bl_options == {"DEFAULT_CLOSED"}
    sub = type("BZ_PT_zz_sub", (sp,), {"bl_idname": "BZ_PT_zz_sub", "bl_label": "x", "capability": "markers", "bl_order": 3})
    with with_entity(FakeEntity(caps=("markers",))):
        assert ui_kit.has_capability(None, "markers") and not ui_kit.has_capability(None, "morphs") and not ui_kit.has_capability(None, "")
        assert sub.poll(bpy.context) is True
        assert type("BZ_PT_zz_sub2", (sp,), {"bl_idname": "BZ_PT_zz_sub2", "bl_label": "x", "capability": "morphs"}).poll(bpy.context) is False
    if importlib.util.find_spec(f"{so.__name__}.entity") is None:
        assert ui_kit.has_capability(None, "markers") is False and sub.poll(bpy.context) is False, "no entity layer yet: no capability"
    else:
        C.skip("the entity layer (slice B01) has landed: has_capability delegating to the real entity.has is covered by its own tests")

    # the scene pointer: the info box + export buttons until the Entity panel exists
    part = scene_part("LabMain")
    scene_part("LabRoof", verts=4)                                              # a second part of the same import (same source)
    scene_part("Other", verts=9, source="/x/other.sgm")                         # another import: not counted
    lay = C.FakeLayout()
    ui_kit.draw_scene_pointer(lay, part)
    assert lay.texts()[0] == "lab" and lay.texts()[1] == "structure / prop: 2 part(s), 7 vertices", lay.texts()
    assert "(triangle mesh: edit it freely" in lay.texts()[2] and lay.operators() == ["bz.export_scene_geometry"], (lay.texts(), lay.operators())
    part["bz_project"] = "CHARACTER"
    part["bz_rig"] = "LabMain_rig"
    drawn = []
    lay = C.FakeLayout()
    ui_kit.draw_scene_pointer(lay, part, rig_box=lambda layout, obj: drawn.append(obj.name))
    assert drawn == ["LabMain"] and lay.operators() == ["bz.export_scene", "bz.export_scene_geometry"] and "gatherer / character: 2 part(s)" in lay.texts()[1]
    lay = C.FakeLayout()
    ui_kit.draw_scene_pointer(lay, part)                                        # no rig_box given: the export buttons still draw
    assert lay.operators() == ["bz.export_scene", "bz.export_scene_geometry"]

    class BZ_PT_entity(bpy.types.Panel):                                         # as soon as the Entity panel is registered: one pointer line
        bl_idname = "BZ_PT_entity"
        bl_label = "Entity"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "SGM"

        def draw(self, context):
            pass

    assert not ui_kit.panel_registered("BZ_PT_entity")
    bpy.utils.register_class(BZ_PT_entity)
    try:
        assert ui_kit.panel_registered("BZ_PT_entity")
        lay = C.FakeLayout()
        ui_kit.draw_scene_pointer(lay, part, rig_box=lambda layout, obj: drawn.append("again"))
        assert len(lay.texts()) == 1 and "Entity panel" in lay.texts()[0] and lay.operators() == [] and drawn == ["LabMain"], (lay.texts(), lay.operators())
    finally:
        bpy.utils.unregister_class(BZ_PT_entity)

    # which objects count as scene objects
    plain = bpy.data.objects.new("Plain", bpy.data.meshes.new("Plain"))
    bpy.context.scene.collection.objects.link(plain)
    rig = bpy.data.objects.new("LabMain_rig", bpy.data.armatures.new("LabMain_rig"))
    other_rig = bpy.data.objects.new("Unrelated_rig", bpy.data.armatures.new("Unrelated_rig"))
    for o in (rig, other_rig):
        bpy.context.scene.collection.objects.link(o)
    assert ui_kit.is_scene_object(part) and not ui_kit.is_scene_object(plain) and not ui_kit.is_scene_object(None)
    assert ui_kit.is_scene_object(rig), "the rig of an import is part of the scene object"
    assert not ui_kit.is_scene_object(other_rig)
    assert ui_kit.is_scene_context(SimpleNamespace(active_object=part)) and not ui_kit.is_scene_context(SimpleNamespace(active_object=None))
    with with_entity(FakeEntity(objs=[plain])):
        assert ui_kit.is_scene_object(plain) and not ui_kit.is_scene_object(other_rig), "an object the entity layer knows is a scene object"

    # the edited BZ_PT_panel.draw: a scene object gets no creature Display box, buttons or hint; a plain object and no selection still do
    from blender_native import ui as bzui
    assert bzui._alert is ui_kit.alert and bzui.ui_kit is ui_kit
    sc = bpy.context.scene

    def draw_panel(active):
        lay = C.FakeLayout()
        ctx = SimpleNamespace(scene=sc, active_object=active)
        bzui.BZ_PT_panel.draw(SimpleNamespace(layout=lay, _rig_box=bzui.BZ_PT_panel._rig_box), ctx)
        return lay

    lay = draw_panel(part)
    assert "Display" not in lay.texts() and "Select a native patch object" not in lay.texts(), lay.texts()
    assert lay.operators()[:3] == ["bz.import_sgm", "bz.new_project", "bz.new_creature"] and "bz.convert_mesh" not in lay.operators() and "bz.fit_mesh" not in lay.operators()
    assert "bz.export_scene_geometry" in lay.operators() and "gatherer / character" in " ".join(lay.texts()), (lay.operators(), lay.texts())
    lay = draw_panel(plain)
    assert "Display" in lay.texts() and "Select a native patch object" in lay.texts() and "bz.convert_mesh" in lay.operators() and "bz.export_scene_geometry" not in lay.operators(), (lay.texts(), lay.operators())
    lay = draw_panel(None)
    assert "Display" in lay.texts() and "Select a native patch object" in lay.texts() and lay.operators()[:3] == ["bz.import_sgm", "bz.new_project", "bz.new_creature"]

    cheetah = os.path.join(C.REPO, "Assets", "engine_stock", "cheetah.sgm")                 # a creature keeps its whole panel
    if os.path.isfile(cheetah):
        C.fresh()
        blender_native.register()                                                           # the import operator needs the add-on registered
        try:
            assert bpy.ops.bz.import_sgm(filepath=cheetah, clips_as_actions=False) == {"FINISHED"}
            creature = bpy.context.view_layer.objects.active
            sc = bpy.context.scene
            assert not ui_kit.is_scene_object(creature) and not ui_kit.is_scene_object(creature.parent)
            lay = draw_panel(creature)
            assert "Display" in lay.texts() and "Select a native patch object" not in lay.texts() and "bz.export_sgm" in lay.operators(), (lay.texts(), lay.operators())
            assert "bz.export_scene_geometry" not in lay.operators() and not any("gatherer / character" in t or "structure / prop" in t for t in lay.texts())
        finally:
            blender_native.unregister()
    else:
        C.skip(f"{cheetah} not found: the creature half of BZ_PT_panel.draw was not exercised")
    C.fresh()


# ------------------------------------------------------------------------------------------------------------------ 7. prefs
def fake_context(**values):
    """A context whose preferences hold this add-on's preferences with the given values (as when the add-on is enabled)."""
    p = SimpleNamespace(bl_idname=prefs.ADDON_ID, **{**prefs.DEFAULTS, **values})
    addons = {prefs.ADDON_ID: SimpleNamespace(preferences=p)}
    return SimpleNamespace(preferences=SimpleNamespace(addons=addons))


def test_prefs():
    section("7. prefs")
    assert prefs.ADDON_ID == "blender_native" and prefs.BZ_AP_prefs.bl_idname == "blender_native"
    blender_native.register()
    try:
        # the live preferences: a background Blender does not add the add-on's entry itself, so make it (as enabling the add-on does)
        entry = bpy.context.preferences.addons.new()
        entry.module = prefs.ADDON_ID
        try:
            live = prefs.get()
            assert type(live).__name__ == "BZ_AP_prefs" and live is not None and live.target_mod == "icmod", type(live)
            live.target_mod = "tellurian"
            live.build_dir = "//builds"
            assert prefs.get().target_mod == "tellurian" and prefs.resolve_build_dir("/work/proj") == bpy.path.abspath("//builds")
            live.game_dir = C.tmpdir("live_game_")
            assert prefs.resolve_game_dir() == (live.game_dir, "preferences")
        finally:
            bpy.context.preferences.addons.remove(entry)
        assert type(prefs.get()).__name__ == "_Defaults"
        props = prefs.BZ_AP_prefs.bl_rna.properties
        assert set(prefs.DEFAULTS) <= set(props.keys()), set(prefs.DEFAULTS) - set(props.keys())
        for name, default in prefs.DEFAULTS.items():
            assert props[name].default == default, (name, props[name].default, default)
        assert {i.identifier for i in props["morph_export"].enum_items} == {"V2_SINGLE_LOD", "DROP"}
        assert {i.identifier for i in props["target_mod"].enum_items} == {"icmod", "tellurian", "insectmod"}
        assert {i.identifier for i in props["delivery_mode"].enum_items} == {"AUTO", "NEW_NAME", "REPLACE_STOCK"}
        assert {i.identifier for i in props["bounds_mode"].enum_items} == {"AUTO", "KEEP_ENTITY_BOX"}
        assert {i.identifier for i in props["txr_type_default"].enum_items} == {"stock1", "stock0"}
        assert all(props[n].subtype == "DIR_PATH" for n in ("game_dir", "test_game_dir", "build_dir", "mods_dir"))
        # the page promises only what the add-on does: every setting nothing reads says so in its label and its description
        assert set(prefs.NOT_USED) == set(prefs.DEFAULTS) - {"game_dir", "target_mod"}, prefs.NOT_USED
        for name in prefs.DEFAULTS:
            label, words = props[name].name, props[name].description
            if name in prefs.NOT_USED:
                assert label.endswith("(not used yet)") and words.startswith("Not used yet"), (name, label, words)
            else:
                assert "not used" not in (label + " " + words).lower(), (name, label, words)
    finally:
        blender_native.unregister()

    p = prefs.get()                                              # the add-on is not enabled in this Blender: the defaults, same names
    assert type(p).__name__ == "_Defaults" and all(getattr(p, k) == v for k, v in prefs.DEFAULTS.items()) and p.bl_idname == prefs.ADDON_ID
    assert prefs.get(fake_context(target_mod="tellurian")).target_mod == "tellurian"
    assert type(prefs.get(SimpleNamespace(preferences=SimpleNamespace(addons={prefs.ADDON_ID: SimpleNamespace(preferences=None)})))).__name__ == "_Defaults"
    assert type(prefs.get(SimpleNamespace(preferences=SimpleNamespace(addons={prefs.ADDON_ID: SimpleNamespace(
        preferences=SimpleNamespace(bl_idname="somebody_else"))})))).__name__ == "_Defaults", "preferences without our settings are not ours"

    d, e = C.tmpdir("pref_game_"), C.tmpdir("pref_test_")
    assert prefs.resolve_game_dir() == model.pick_game_dir("")
    assert prefs.resolve_game_dir(fake_context(game_dir=d)) == (d, "preferences")
    assert prefs.game_dir_line(fake_context(game_dir=d)) == f"Game folder: {d} (from the Game folder in the add-on's preferences)"
    assert prefs.resolve_game_dir(fake_context(game_dir=os.path.join(d, "nope")))[1] != "preferences", "a folder that does not exist is not used"
    assert prefs.resolve_test_game_dir(fake_context(test_game_dir=e)) == e and prefs.resolve_test_game_dir(fake_context()) is None
    assert prefs.resolve_test_game_dir(fake_context(test_game_dir=os.path.join(e, "nope"))) is None
    assert prefs.resolve_mods_dir(fake_context(mods_dir=e)) == e and prefs.resolve_mods_dir() is None
    assert prefs.resolve_build_dir("/work/proj") == "/work/proj/build" and prefs.resolve_build_dir("/work/proj", fake_context(build_dir="/out/b")) == "/out/b"

    def draw(ctx, **kw):
        lay = C.FakeLayout()
        values = {**prefs.DEFAULTS, **kw}
        prefs.BZ_AP_prefs.draw(SimpleNamespace(layout=lay, **values), ctx)
        return lay

    lay = draw(fake_context(game_dir=d))
    assert lay.use_property_split and not lay.use_property_decorate
    assert any(t.startswith(f"Using {d} (from the setting above)") for t in lay.texts()), lay.texts()
    shown = [dict_["name"] for k, dict_ in lay.calls if k == "prop"]
    assert shown == ["game_dir", "test_game_dir", "build_dir", "mods_dir", "target_mod", "morph_export", "txr_type_default", "delivery_mode", "bounds_mode"], shown
    saved = os.environ.pop("IC_INSTALL", None)
    try:
        lay = draw(fake_context(game_dir="/does/not/exist"), game_dir="/does/not/exist")
        found = prefs.resolve_game_dir(fake_context(game_dir="/does/not/exist"))
        if found[0] is None:
            assert any(l["alert"] and "No game found" in l["text"] for l in lay.labels()), lay.texts()
            assert prefs.game_dir_line(fake_context(game_dir="/does/not/exist")) == prefs.NO_GAME
        else:
            assert any(t.startswith("Using ") for t in lay.texts()), lay.texts()
    finally:
        if saved is not None:
            os.environ["IC_INSTALL"] = saved
    so.ERRORS.append(("slice_zz_broken", "SyntaxError: boom"))
    try:
        lay = draw(fake_context())
        assert "Parts of the add-on that could not be loaded" in lay.texts() and any(t == "slice_zz_broken: SyntaxError: boom" for t in lay.texts()), lay.texts()
    finally:
        so.ERRORS.pop()
    assert "Parts of the add-on that could not be loaded" not in draw(fake_context()).texts()


# ------------------------------------------------------------------------------------------------------------------ misc
def test_has_api_in_blender():
    section("8. has_api against real Blender types")
    me = bpy.data.meshes.new("probe")
    assert C.has_api(bpy.types.Mesh, "normals_split_custom_set_from_vertices") and C.has_api(me, "normals_split_custom_set_from_vertices")
    assert C.has_api(bpy.types.Mesh, "vertices") and C.has_api(bpy.types.Object, "vertex_groups") and C.has_api(me, "polygons")
    assert not C.has_api(bpy.types.Mesh, "definitely_not_there") and not C.has_api(me, "definitely_not_there")
    assert not C.has_api(bpy.types.Bone, "select"), "Bone.select was removed in Blender 5"
    bpy.data.meshes.remove(me)


def main():
    test_discovered()
    test_defined_equals_listed()
    test_register_unregister()
    test_discovery_fixtures()
    test_reload()
    test_ui_kit()
    test_prefs()
    test_has_api_in_blender()
    print("SO_REGISTRY_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("registry", main)
