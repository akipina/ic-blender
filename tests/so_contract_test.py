"""sceneobj contract (slice B00), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_contract_test.py     (or: run_all.py -k so_contract)

Every slice that lands later is held to this file (an agent runs it before each commit):

1. the names, constants and signatures PLAN_FINAL C21 fixes are there and unchanged;
2. every registered slice fits the Slice protocol; orders are unique, stage names keep their numbers, claims are disjoint, guard codes
   claimed are known and claimed once;
3. module hygiene, read from the source text: no stray module that would never be loaded; a module that declares PURE = True imports no
   Blender at its top level and no sibling that needs it; every hand-over key written or read through ``.shared`` is declared; every
   ``claim_edit`` code is a guard code;
4. the one-time edits of the shared files are in place: minimum Blender 5.3.0, the zip carries sceneobj/, run_all finds the so_ suites.
"""
import ast
import dataclasses
import importlib.util
import inspect
import os
import sys

sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402,F401

sys.path.insert(0, C.REPO)
import blender_native  # noqa: E402
from blender_native import sceneobj as so  # noqa: E402
from blender_native.sceneobj import model, prefs, ui_kit  # noqa: E402

PKG = os.path.join(C.ADDON, "sceneobj")
BLENDERISH = ("bpy", "bmesh", "mathutils", "bpy_extras", "gpu", "blf", "gpu_extras", "bl_ui", "bl_operators")


def section(title):
    print(f"-- {title}", flush=True)


def short(m):
    return m.__name__.rsplit(".", 1)[-1]


# ------------------------------------------------------------------------------------------------------------------ source scanners
def module_files():
    return sorted(f[:-3] for f in os.listdir(PKG) if f.endswith(".py") and f != "__init__.py")


def parse(name):
    with open(os.path.join(PKG, name + ".py"), encoding="utf-8") as f:
        return ast.parse(f.read(), name)


def declares_pure(tree):
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "PURE" for t in targets) and isinstance(getattr(node, "value", None), ast.Constant) and node.value.value is True:
            return True
    return False


def top_level_imports(tree):
    """``(guarded, statement)`` for every import that runs when the module is imported: directly in its body, or inside a top-level
    ``try`` / ``if`` (guarded = inside a try with an ImportError handler)."""
    out = []

    def walk(body, guarded):
        for node in body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                out.append((guarded, node))
            elif isinstance(node, ast.Try):
                handles = any("ImportError" in ast.dump(h.type) or "ModuleNotFoundError" in ast.dump(h.type) for h in node.handlers if h.type is not None)
                walk(node.body, guarded or handles)
                for h in node.handlers:
                    walk(h.body, guarded)
                walk(node.orelse, guarded)
                walk(node.finalbody, guarded)
            elif isinstance(node, ast.If):
                walk(node.body, guarded)
                walk(node.orelse, guarded)

    walk(tree.body, False)
    return out


def imported_roots(node):
    """The top-level package names an absolute import statement pulls in."""
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def pure_violations(name, tree, pure_names):
    """What stops module ``name`` (which declares PURE) from importing without Blender."""
    problems = []
    siblings = set(module_files())
    for guarded, node in top_level_imports(tree):
        if guarded:
            continue
        for root in imported_roots(node):
            if root in BLENDERISH:
                problems.append(f"line {node.lineno}: imports {root} at the top level")
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        named = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
        for target in named:
            if node.level == 1 and target in siblings and target not in pure_names:
                problems.append(f"line {node.lineno}: imports the sibling {target}, which does not declare PURE")
            elif node.level == 2:                  # one of the add-on's own modules, e.g. `from .. import core`
                path = os.path.join(C.ADDON, target + ".py")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as f:
                        sub = ast.parse(f.read(), path)
                    if any(r in BLENDERISH for g, n in top_level_imports(sub) if not g for r in imported_roots(n)):
                        problems.append(f"line {node.lineno}: imports ..{target}, which imports Blender")
    return problems


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def shared_keys_used(tree):
    """The string keys used on anything called ``shared``: ``x.shared["k"]``, ``x.shared.get("k")`` / ``setdefault`` / ``pop``,
    ``"k" in x.shared``, and the keys of ``x.shared.update(k=...)`` / ``x.shared.update({"k": ...})``."""
    def is_shared(node):
        return isinstance(node, ast.Attribute) and node.attr == "shared"

    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and is_shared(node.value):
            k = _const_str(node.slice)
            if k is not None:
                keys.append((node.lineno, k))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_shared(node.func.value):
            if node.func.attr in ("get", "setdefault", "pop") and node.args and _const_str(node.args[0]) is not None:
                keys.append((node.lineno, _const_str(node.args[0])))
            elif node.func.attr == "update":
                keys += [(node.lineno, kw.arg) for kw in node.keywords if kw.arg]
                for a in node.args:
                    if isinstance(a, ast.Dict):
                        keys += [(node.lineno, _const_str(k)) for k in a.keys if k is not None and _const_str(k) is not None]
        elif isinstance(node, ast.Compare) and any(is_shared(c) for c in node.comparators) and _const_str(node.left) is not None:
            keys.append((node.lineno, _const_str(node.left)))
    return keys


def called_literals(tree, func_name):
    """The first string argument of every call ``func_name("...")`` / ``x.func_name("...")``."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and _const_str(node.args[0]) is not None:
            f = node.func
            if (isinstance(f, ast.Name) and f.id == func_name) or (isinstance(f, ast.Attribute) and f.attr == func_name):
                out.append((node.lineno, _const_str(node.args[0])))
    return out


def test_scanners():
    section("0. the source scanners find what they should (checked on a sample)")
    sample = ast.parse('''
def f(out):
    out.shared["geometry"] = 1
    out.shared.get("skeleton")
    out.shared.setdefault("markers", {})
    out.shared.update({"bounds": 1}, clips=2)
    if "morphs" in out.shared: pass
    x = other["notshared"]
    claim_edit("shape_key"); model.claim_edit("normals"); declare_shared("zzz", "q"); m.declare_shared("yyy", "q")
''')
    assert sorted(k for _l, k in shared_keys_used(sample)) == ["bounds", "clips", "geometry", "markers", "morphs", "skeleton"]
    assert [k for _l, k in called_literals(sample, "claim_edit")] == ["shape_key", "normals"]
    assert [k for _l, k in called_literals(sample, "declare_shared")] == ["zzz", "yyy"]
    imp = ast.parse("import os\ntry:\n    import bpy\nexcept ImportError:\n    bpy = None\nfrom . import ui_kit\nimport bmesh\nif x:\n    import mathutils\n")
    got = [(g, getattr(n, "names", [None])[0].name if isinstance(n, ast.Import) else n.module or n.names[0].name) for g, n in top_level_imports(imp)]
    assert got == [(False, "os"), (True, "bpy"), (False, "ui_kit"), (False, "bmesh"), (False, "mathutils")], got
    assert declares_pure(ast.parse("PURE = True\n")) and declares_pure(ast.parse("PURE: bool = True\n")) and not declares_pure(ast.parse("PURE = False\n"))
    assert not declares_pure(ast.parse("def f():\n    PURE = True\n"))
    pv = pure_violations("x", ast.parse("import bpy\nfrom . import ui_kit\nimport os\n"), {"model"})
    assert len(pv) == 2 and "imports bpy" in pv[0] and "ui_kit" in pv[1], pv
    assert pure_violations("x", ast.parse("try:\n    import bpy\nexcept ImportError:\n    bpy = None\nfrom . import model\n"), {"model"}) == []


# ------------------------------------------------------------------------------------------------------------------ 1. C21 surface
def test_surface():
    section("1. the names, constants and signatures C21 fixes")
    assert model.SLICE_API == 1
    assert model.STAGE_ORDER == {"materials": 10, "textures": 12, "skeleton": 20, "geometry": 30, "morphs": 40, "markers": 50, "bounds": 60,
                                 "anim": 70, "controller_meta": 80, "controller_motion": 81, "controller_events": 82, "files": 90, "validate": 100}
    assert len(set(model.STAGE_ORDER.values())) == len(model.STAGE_ORDER)
    assert model.SLICE_MEMBERS == ("name", "order", "claims", "read", "build", "gather", "changed", "apply", "check", "on_rename")
    assert model.BASE_SHARED_KEYS == {"skeleton", "materials", "textures", "geometry", "morphs", "markers", "bounds", "clips"}
    assert set(model.SHARED_OWNERS) == model.SHARED_KEYS and model.SHARED_OWNERS["clips"] == "anim"
    assert model.GUARD_CODES == ("skeleton_edit", "skeleton_rename", "object_transform", "modifier", "shape_key", "unclaimed_object", "shader_map",
                                 "material_edit", "morph_part_edit", "source_changed", "part_name", "normals", "image_stamp", "bounds_stale")
    assert [f.name for f in dataclasses.fields(model.SourceBundle)] == ["sgm", "ebp", "lua", "paths", "scene"]
    assert [f.name for f in dataclasses.fields(model.OutBundle)] == ["sgm", "ebp", "lua", "files", "notes", "issues", "shared"]
    assert [p for p in inspect.signature(model.issue).parameters][:4] == ["code", "level", "text", "where"]
    assert [p for p in inspect.signature(model.RefsBus.rename).parameters][:4] == ["self", "kind", "old", "new"]
    assert [p for p in inspect.signature(model.claim_edit).parameters][:1] == ["code"]
    for name in ("read", "build", "gather", "changed", "apply", "check", "on_rename"):
        assert callable(getattr(model.BaseSlice, name)), name
    assert isinstance(model.BaseSlice(), model.Slice)

    assert so.CORE == ("model", "subtype", "entity", "ui_kit", "prefs", "importer", "exporter", "compat")
    assert so.PREFIXES == ("slice_", "ops_", "panels_", "props_", "guards_", "map_")
    assert isinstance(so.MODULES, list) and isinstance(so.CLASSES, tuple) and so.DEFAULT_ORDER == 50
    for name in ("discover", "register", "unregister", "reload"):
        assert callable(getattr(so, name)) and not inspect.signature(getattr(so, name)).parameters, name
    for name in ("refs", "claim_edit", "Issue", "issue", "SourceBundle", "OutBundle", "Slice", "BaseSlice", "slices", "SLICE_API"):
        assert getattr(so, name) is getattr(model, name), name

    assert [p for p in inspect.signature(ui_kit.alert).parameters] == ["layout", "text", "icon", "width"]
    assert [p for p in inspect.signature(ui_kit.draw_scene_pointer).parameters][:2] == ["layout", "obj"]
    assert [p for p in inspect.signature(ui_kit.issue_rows).parameters][:2] == ["layout", "issues"]
    assert issubclass(ui_kit.BZ_UL_rows, bpy.types.UIList) and issubclass(ui_kit.SubPanel, bpy.types.Panel)
    assert ui_kit.SubPanel.bl_options == {"DEFAULT_CLOSED"} and ui_kit.SubPanel.bl_parent_id == "BZ_PT_entity"
    assert issubclass(prefs.BZ_AP_prefs, bpy.types.AddonPreferences)
    assert set(prefs.DEFAULTS) == {"game_dir", "test_game_dir", "build_dir", "mods_dir", "target_mod", "morph_export", "txr_type_default", "delivery_mode", "bounds_mode"}


# ------------------------------------------------------------------------------------------------------------------ 2. registered slices
def test_slices():
    section("2. the registered slices")
    reg = so.slices()
    for s in reg:
        assert model.slice_problems(s) == [], (s.name, model.slice_problems(s))
        assert isinstance(s, model.Slice), s.name
    orders = [s.order for s in reg]
    assert len(set(orders)) == len(orders), f"two slices share an export order: {sorted((s.order, s.name) for s in reg)}"
    for s in reg:
        if s.name in model.STAGE_ORDER:
            assert s.order == model.STAGE_ORDER[s.name], (s.name, s.order)
    claims = [c for s in reg for c in s.claims]
    assert len(set(claims)) == len(claims), f"a datablock kind is claimed by two slices: {sorted(claims)}"
    assert model.registry_problems() == [], model.registry_problems()
    for m in so.MODULES:
        order = getattr(m, "ORDER", None)
        assert order is None or (isinstance(order, int) and not isinstance(order, bool)), (m.__name__, order)
        classes = getattr(m, "CLASSES", ())
        assert isinstance(classes, tuple) and all(isinstance(c, type) for c in classes), (m.__name__, classes)
        for s in so._slices_of(m):
            assert isinstance(s, model.Slice), (m.__name__, s)
    print(f"   {len(reg)} slice(s): {[s.name for s in reg]}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 3. hygiene
def test_hygiene():
    section("3. module hygiene (from the source text)")
    files = module_files()
    loaded = {short(m) for m in so.MODULES}
    assert loaded == {n for n in files if n in so.CORE or n.startswith(so.PREFIXES)}, (sorted(loaded), files)
    for name in files:
        if name in so.CORE or name.startswith(so.PREFIXES):
            continue
        # not discovered: fine for a plain helper library (source.py, ...), but not for a module that registers things
        defined = {t.id for node in parse(name).body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
        defined |= {node.name for node in parse(name).body if isinstance(node, ast.FunctionDef)}
        stray = defined & {"CLASSES", "SLICE", "register", "unregister"}
        assert not stray, f"sceneobj/{name}.py defines {sorted(stray)} but is neither a core module nor named slice_ / ops_ / panels_ / props_ / guards_: it would never be registered"
    assert "model" in files and declares_pure(parse("model")), "model.py must declare PURE = True"

    trees = {n: parse(n) for n in files}
    pure = {n for n, t in trees.items() if declares_pure(t)}
    for n in sorted(pure):
        assert pure_violations(n, trees[n], pure) == [], (n, pure_violations(n, trees[n], pure))
    declared_keys, declared_codes = set(model.SHARED_KEYS), set(model.known_guard_codes())
    for n, t in trees.items():
        declared_keys |= {k for _l, k in called_literals(t, "declare_shared")}
        declared_codes |= {k for _l, k in called_literals(t, "declare_guard_code")}
    for n, t in trees.items():
        for line, key in shared_keys_used(t):
            assert key in declared_keys, f"sceneobj/{n}.py:{line} uses the hand-over key {key!r}, which is not declared (SHARED_KEYS / declare_shared)"
        for line, code in called_literals(t, "claim_edit"):
            assert code in declared_codes, f"sceneobj/{n}.py:{line} claims {code!r}, which is not a guard code (GUARD_CODES / declare_guard_code)"
    print(f"   {len(files)} module file(s), {len(pure)} declaring PURE: {sorted(pure)}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 4. shared edits
def test_shared_edits():
    section("4. the one-time edits of the shared files")
    assert blender_native.bl_info["blender"] == (5, 3, 0), blender_native.bl_info["blender"]
    with open(os.path.join(C.ADDON, "blender_manifest.toml"), encoding="utf-8") as f:
        assert 'blender_version_min = "5.3.0"' in f.read()
    assert blender_native.sceneobj is so and "sceneobj" in [short(m) for m in blender_native._MODULES], "sceneobj is reloaded with the add-on"
    assert [short(m) for m in blender_native._MODULES][-2:] == ["sceneobj", "ui"], "sceneobj reloads just before ui, which draws with its kit"
    assert so.ERRORS == []

    spec = importlib.util.spec_from_file_location("bz_build_extension", os.path.join(C.ADDON, "tools", "build_extension.py"))
    be = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(be)
    assert "sceneobj" in be.COPY_DIRS and "_deps" in be.COPY_DIRS and "sceneobj" not in be.SKIP_DIRS
    assert be.blender_min() == "5.3.0", be.blender_min()
    staging = C.tmpdir("stage_")
    be.stage_addon(staging)
    want = sorted(f for f in os.listdir(PKG) if f != "__pycache__" and not f.endswith(".pyc"))
    got = sorted(os.listdir(os.path.join(staging, "sceneobj")))
    assert got == want and "__pycache__" not in got, (got, want)       # the zip carries every module of the package, and no cache
    assert not os.path.exists(os.path.join(staging, "tests")) and not os.path.exists(os.path.join(staging, "docs"))

    import run_all
    assert "so_model_pure_test.py" in run_all.PURE and "so_registry_test.py" in run_all.IN_BLENDER and "so_contract_test.py" in run_all.IN_BLENDER
    assert run_all.so_suite.PURE == [n for n in run_all.PURE if n.startswith("so_")] and len(set(run_all.PURE)) == len(run_all.PURE) and len(set(run_all.IN_BLENDER)) == len(run_all.IN_BLENDER)
    for scene_test in ("scene_import_test.py", "scene_retopo_test.py", "scene_geometry_export_test.py", "scene_anim_export_test.py", "new_project_test.py"):
        assert scene_test in run_all.IN_BLENDER, f"{scene_test} must stay in the suite (the gate of the sceneobj build)"
    assert "extension_install_test.py" in run_all.PURE


def main():
    test_scanners()
    test_surface()
    test_slices()
    test_hygiene()
    test_shared_edits()
    print("SO_CONTRACT_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("contract", main)
