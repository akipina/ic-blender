#!/usr/bin/env python3
"""D10 Half B: the built extension installed into a CLEAN Blender and used, with none of this repo reachable.

    .venv/bin/python3 blender_native/tests/extension_install_test.py
    .venv/bin/python3 blender_native/tests/extension_install_test.py --zip dist/sgm_native_patches-0.4.0.zip
    .venv/bin/python3 blender_native/tests/extension_install_test.py --older-zip <an older sgm_native_patches-*.zip>

This is the proof that the .zip is a distributable add-on and not just an archive of the repo:

1. **build** the zip with ``tools/build_extension.py`` (reused when ``dist/`` already holds one newer than every
   ``.py`` of the add-on (its ``sceneobj`` package and ``tools`` too), ICParse and combotest; ``--rebuild`` forces it). The PyPI wheels are cached in ``dist/_wheels``, so only the first
   build needs the network;
2. **install** it into a throw-away ``BLENDER_USER_RESOURCES`` with ``blender --command extension install-file``
   -- a Blender that has never seen this add-on, this repo or these dependencies;
3. **use** it from a second Blender run whose environment has no ``$BZ_ICPARSE`` / ``$BZ_COMBOTEST`` /
   ``$BZ_DEPS_BIN`` / ``$PYTHONPATH``, whose working directory is ``/tmp``, and where the repo is nowhere on
   ``sys.path``: enable the extension, and assert that ICParse, construct, combotest and lupa all import **from
   the installed wheels** (``PATHS_STATUS`` says ``importable`` and the file lives under the temp resources), then
   import a stock creature, run Validate, and Export it byte-identical;
4. **remove** it with ``blender --command extension remove`` and assert the add-on and its wheels are gone;
5. **upgrade** in a second throw-away ``BLENDER_USER_RESOURCES``, the way a modder does it: an older build installed
   and enabled with ``blender --command extension install-file -r user_default -e <zip>`` (the profile's own
   preferences, no ``--factory-startup``), then this zip installed over it with the same command and no remove in between, then this
   zip once more. After each, a fresh Blender on that profile must have the add-on enabled, ``ICParse.ebp.scene``,
   ``ICParse.sgm.creature.layout`` and ``combotest`` imported from the NEW wheels (their ``.dist-info`` installed and
   every file their RECORD lists on disk as built, the older build's gone) and the Blueprint panel ``BZ_PT_blueprint``
   registered. The older build is made from this zip (``make_older_build``): ICParse and combotest named as every build
   before the content labels named them, each with a marker module the new build does not have; ``--older-zip``
   upgrades from a real older build instead. Without the labels Blender kept the older library files here (see
   ``relabel_wheel`` in ``tools/build_extension.py``);
6. **upgrade inside one Blender session** (``in_session_check``), the way Install from Disk does it while the older version is
   enabled: Blender disables the older add-on, removes only the add-on's own modules from ``sys.modules``, installs the new
   wheels and enables the new add-on in the same session. On a third throw-away profile with the older build installed and
   enabled, ONE Blender (``IN_SESSION``) calls ``bpy.ops.extensions.package_install_files`` three times: this zip, a newer
   build made from it (``make_newer_build``: ICParse and combotest with one name only they define, under a new content label,
   and an add-on that imports that name from both while it loads), and that newer build again. After each install, in that
   same session, the add-on must be enabled, ICParse and combotest must be the ones of the build just installed (their
   files, versions and marker names; none of the add-on's modules may hold an object of the libraries that were in memory
   before), the libraries must have been loaded again after an upgrade and kept after the same build again, and a fresh
   Blender afterwards must find the newer build's wheels all on disk. Before ``__init__._unload_stale_libraries`` the older
   libraries stayed in memory here, and the newer build's add-on failed to load, after which Blender's wheel sync emptied the
   shared site-packages (STATUS.md gap 20). The older build made here records nothing on the libraries it loads, as every
   release before 0.4.0.

The stock creature is copied out of the repo into the temp directory first: what must not be reachable is the
repo's CODE, not a .sgm the modder would have from their own game install.
"""
from __future__ import annotations

import argparse
import base64
import csv
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common  # noqa: E402  (the machine-wide Blender launch lock)
ADDON = os.path.dirname(HERE)
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(ADDON)
BLENDER = os.environ.get("BLENDER") or shutil.which("blender") or "blender"
DIST = os.path.join(ADDON, "dist")
EXT_ID = "sgm_native_patches"
MODULE = f"bl_ext.user_default.{EXT_ID}"
CREATURE = os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
OLDER_MARKER = "_bz_older_build.py"      # a module only the older build's ICParse and combotest have (make_older_build)
OLDER_NAME = "_BZ_OLDER_BUILD"           # a name only the older build's ICParse and combotest packages define
NEWER_NAME = "_BZ_NEWER_BUILD"           # a name only the newer build's packages define, and its add-on imports while it loads
# ICParse / combotest as tools/build_extension.py names them: <version>+h<12 hex digits naming their contents>
LABELLED = re.compile(r"^wheels/(?P<dist>icparse|combotest)-(?P<version>[^-+]+)\+(?P<label>h[0-9a-f]{12})-(?P<tags>[^/]+)\.whl$")

SMOKE = r'''
import json, os, sys
import bpy

MODULE = os.environ["BZ_SMOKE_MODULE"]
SRC = os.environ["BZ_SMOKE_SGM"]
OUT = os.environ["BZ_SMOKE_OUT"]
RES = os.path.abspath(os.environ["BLENDER_USER_RESOURCES"])
report = {"module": MODULE}

# nothing of the repo may be reachable: no path under it on sys.path, and no BZ_* pointing at it
report["repo_on_path"] = [p for p in sys.path if os.environ.get("BZ_SMOKE_REPO", "\0") in os.path.abspath(p or ".")]
report["bz_env"] = {k: v for k, v in os.environ.items() if k.startswith("BZ_") and not k.startswith("BZ_SMOKE")}

bpy.ops.preferences.addon_enable(module=MODULE)
addon = sys.modules[MODULE]
report["paths_status"] = {k: {"source": v["source"], "path": v["path"], "note": v["note"][:120]}
                          for k, v in addon.PATHS_STATUS.items()}
import ICParse, construct
report["deps"] = {}
for name in ("ICParse", "construct", "combotest", "lupa"):
    try:
        mod = __import__(name)
        f = os.path.abspath(getattr(mod, "__file__", "") or "")
        report["deps"][name] = {"file": f, "under_resources": f.startswith(RES)}
    except Exception as e:
        report["deps"][name] = {"error": f"{type(e).__name__}: {e}"}

# the combiner's own view of what it found (the Combine Preview status box reads this)
try:
    st = addon.combocore.status(paths_status=addon.PATHS_STATUS)
    report["combiner"] = {k: st.get(k) for k in ("combotest", "combotest_error", "lupa", "lua", "lua_error")}
except Exception as e:
    report["combiner"] = {"error": f"{type(e).__name__}: {e}"}

bpy.context.scene.bz_combine.validate_level = "OFF"
assert bpy.ops.bz.import_sgm(filepath=SRC, clips_as_actions=False) == {"FINISHED"}
obj = bpy.context.view_layer.objects.active
report["import"] = {"points": len(obj.data.vertices), "patches": int(obj.get("bz_npatch", 0)),
                    "name": obj.get("bz_name", obj.name)}
report["issues"] = [i.code for i in addon.guards.issues(obj, deep=True)]     # D10 Half A, through the installed add-on

assert bpy.ops.bz.validate() == {"FINISHED"}
report["validate"] = [str(x) for x in obj.get("bz_validation", [])][:40]

if os.path.exists(OUT):
    os.remove(OUT)
assert bpy.ops.bz.export_sgm(filepath=OUT) == {"FINISHED"}
report["export"] = {"bytes": os.path.getsize(OUT),
                    "identical": open(OUT, "rb").read() == open(SRC, "rb").read(),
                    "summary": str(obj.get("bz_last_export", ""))[:200]}

# the sceneobj package travels in the zip: every module of it imports from the installed copy, its classes are registered, and the
# add-on preferences are the live ones of this extension (bl_idname = the extension's own module name)
import importlib, pkgutil
so = importlib.import_module(MODULE + ".sceneobj")
on_disk = []
for info in pkgutil.iter_modules(so.__path__):
    importlib.import_module(f"{so.__name__}.{info.name}")
    on_disk.append(info.name)
adn = bpy.context.preferences.addons
entry = adn.get(MODULE) or adn.new()           # a background Blender does not add the add-on's entry itself
entry.module = MODULE
prefs = importlib.import_module(so.__name__ + ".prefs").get()
report["sceneobj"] = {"on_disk": sorted(on_disk), "core": list(so.CORE), "prefixes": list(so.PREFIXES),
                      "loaded": sorted(m.__name__.rsplit(".", 1)[-1] for m in so.MODULES),
                      "errors": [list(e) for e in so.ERRORS], "problems": so.status()["problems"],
                      "classes": [c.__name__ for c in so.CLASSES], "registered": [c.__name__ for c in so.registered_classes()],
                      "all_registered": all(so.is_registered(c) for c in so.CLASSES),
                      "prefs": {"type": type(prefs).__name__, "module": entry.module, "target_mod": prefs.target_mod}}
print("BZ_SMOKE_JSON " + json.dumps(report))
'''

# run by a fresh Blender on the upgrade profile, with its preferences (the add-on is enabled at startup, as for a modder)
UPGRADE_PROBE = r'''
import importlib, importlib.metadata, json, os
import addon_utils, bpy

MODULE = os.environ["BZ_SMOKE_MODULE"]
report = {"in_preferences": MODULE in bpy.context.preferences.addons, "enabled": bool(addon_utils.check(MODULE)[1]),
          "blueprint_panel": hasattr(bpy.types, "BZ_PT_blueprint"), "modules": {}, "versions": {}}
for name in ("ICParse.ebp.scene", "ICParse.sgm.creature.layout", "combotest"):
    try:
        report["modules"][name] = os.path.abspath(importlib.import_module(name).__file__)
    except Exception as e:
        report["modules"][name] = f"ERROR {type(e).__name__}: {e}"
for dist in ("ICParse", "combotest"):
    try:
        report["versions"][dist] = importlib.metadata.version(dist)
    except Exception as e:
        report["versions"][dist] = f"ERROR {type(e).__name__}: {e}"
print("BZ_UPGRADE_JSON " + json.dumps(report))
'''

# run by ONE Blender on the in-session profile (its preferences enable the older build at startup): installs every zip of
# BZ_SESSION_ZIPS in this same session, as Install from Disk does, and reports after each what the session runs on
IN_SESSION = r'''
import importlib, importlib.metadata, json, os, sys, types
import addon_utils, bpy

MODULE = os.environ["BZ_SMOKE_MODULE"]
ZIPS = json.loads(os.environ["BZ_SESSION_ZIPS"])
NAMES = ("ICParse", "combotest")
USED = ("ICParse.ebp.scene", "ICParse.sgm.creature.layout", "combotest")        # imported as a use of the add-on would


def libraries(report):
    importlib.invalidate_caches()
    for name in USED:
        try:
            importlib.import_module(name)
        except Exception as e:
            report.setdefault("import_errors", {})[name] = f"{type(e).__name__}: {e}"
    return {n: sys.modules.get(n) for n in NAMES}


def stale_references():
    """What the add-on's modules hold of ICParse and combotest that is not the object those libraries hold now."""
    out = []
    for key, mod in list(sys.modules.items()):
        if mod is None or not (key == MODULE or key.startswith(MODULE + ".")):
            continue
        for attr, value in list(vars(mod).items()):
            if isinstance(value, types.ModuleType):
                if value.__name__.split(".")[0] in NAMES and sys.modules.get(value.__name__) is not value:
                    out.append(f"{key}.{attr} (module {value.__name__})")
            elif isinstance(value, (type, types.FunctionType)):
                home, qual = getattr(value, "__module__", None) or "", getattr(value, "__qualname__", "")
                if home.split(".")[0] in NAMES and "." not in qual and "<" not in qual \
                        and getattr(sys.modules.get(home), qual, None) is not value:
                    out.append(f"{key}.{attr} ({home}.{qual})")
    return sorted(out)


def state():
    report = {}
    libs = libraries(report)
    addon = sys.modules.get(MODULE)
    report.update({
        "enabled": bool(addon_utils.check(MODULE)[1]),
        "files": {n: os.path.abspath(m.__file__) if m is not None and getattr(m, "__file__", None) else None for n, m in libs.items()},
        "older": {n: bool(getattr(m, "_BZ_OLDER_BUILD", False)) for n, m in libs.items()},
        "newer": {n: bool(getattr(m, "_BZ_NEWER_BUILD", False)) for n, m in libs.items()},
        "unloaded": sorted(getattr(addon, "UNLOADED", None) or {}),
        "stale_references": stale_references(),
        "blueprint_panel": hasattr(bpy.types, "BZ_PT_blueprint"),
        "versions": {},
    })
    for n in NAMES:
        try:
            report["versions"][n] = importlib.metadata.version(n)
        except Exception as e:
            report["versions"][n] = f"ERROR {type(e).__name__}: {e}"
    return libs, report


held, first = state()
out = {"before": first, "steps": []}
for z in ZIPS:
    try:
        result = sorted(bpy.ops.extensions.package_install_files(filepath=z, repo="user_default", enable_on_install=True))
    except Exception as e:
        result = [f"ERROR {type(e).__name__}: {e}"]
    libs, step = state()
    step.update(zip=os.path.basename(z), result=result, kept={n: libs[n] is not None and libs[n] is held[n] for n in NAMES})
    out["steps"].append(step)
    held = libs
bpy.ops.wm.save_userpref()
print("BZ_SESSION_JSON " + json.dumps(out))
'''


def say(*a):
    print(*a, flush=True)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("BZ_") and k not in ("PYTHONPATH", "LD_LIBRARY_PATH", "PYIC_REPO")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def newest_addon_mtime() -> float:
    """The newest .py the zip is built from: the add-on's own (its sceneobj package included), the build tools in tools/,
    and those of the libraries it ships as wheels (ICParse, combotest), which a zip newer than the add-on alone can still
    predate."""
    roots = [(ADDON, False), (os.path.join(ADDON, "sceneobj"), True), (os.path.join(ADDON, "tools"), False),
             (os.path.join(REPO, "ICParse", "ICParse"), True), (os.path.join(REPO, "pyRDN", "combotest", "combotest"), True)]
    newest = 0.0
    for root, deep in roots:
        if not os.path.isdir(root):
            continue
        if deep:
            files = glob.glob(os.path.join(root, "**", "*.py"), recursive=True)
        else:
            files = [os.path.join(root, f) for f in os.listdir(root) if f.endswith(".py")]
        newest = max([newest] + [os.path.getmtime(f) for f in files])
    return newest


def build_zip(rebuild: bool) -> str:
    existing = sorted(glob.glob(os.path.join(DIST, f"{EXT_ID}-*.zip")), key=os.path.getmtime)
    if existing and not rebuild and os.path.getmtime(existing[-1]) > newest_addon_mtime():
        say(f"reusing {existing[-1]} (newer than every add-on, ICParse and combotest .py)")
        return existing[-1]
    cmd = [sys.executable, os.path.join(ADDON, "tools", "build_extension.py"), "--blender", BLENDER]
    say("+ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=REPO, env=clean_env())
    say((r.stdout + r.stderr).strip()[-2500:])
    assert r.returncode == 0, f"build_extension failed ({r.returncode})"
    made = sorted(glob.glob(os.path.join(DIST, f"{EXT_ID}-*.zip")), key=os.path.getmtime)
    assert made, "no zip was built"
    return made[-1]


def blender_run(args, res: str, extra_env=None, timeout=600):
    env = {k: v for k, v in clean_env().items() if not k.startswith("BLENDER_USER_")}    # only the throw-away profile
    env.update(BLENDER_USER_RESOURCES=res, **(extra_env or {}))
    r = _so_common.run_locked([BLENDER, "--background", *args], capture_output=True, text=True, timeout=timeout,
                              cwd="/tmp", env=env)
    return r


# ------------------------------------------------------------------------------------------- 5. the upgrade case
def _record_row(name: str, payload: bytes) -> list:
    digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
    return [name, f"sha256={digest}", str(len(payload))]


def _rewrite_wheel(data: bytes, label, init_line: bytes, marker: bool = False) -> bytes:
    """A labelled ICParse / combotest wheel (``data``) rewritten for the upgrade checks. ``label`` None names it as every build
    before the content labels did (``<dist>-<version>.dist-info``, METADATA's bare ``Version:``), else ``<version>+<label>``;
    ``init_line`` is added to the end of the package's ``__init__.py``; ``marker`` adds the module OLDER_MARKER. RECORD is
    written anew, so every file is as its RECORD says."""
    src = zipfile.ZipFile(io.BytesIO(data))
    names = src.namelist()
    info = next(n.split("/")[0] for n in names if n.endswith(".dist-info/RECORD"))
    m = re.match(r"^(?P<dist>[^-]+)-(?P<version>[^-+]+)\+(?P<label>[^-]+)\.dist-info$", info)
    assert m, f"{info}: not a labelled wheel"
    version = m["version"] if label is None else f"{m['version']}+{label}"
    new_info = f"{m['dist']}-{version}.dist-info"
    packages = sorted({n.split("/")[0] for n in names if "/" in n and not n.startswith(info + "/")})
    assert len(packages) == 1, packages
    init = f"{packages[0]}/__init__.py"
    assert init in names, f"{init} is not in the wheel"
    out, rows = io.BytesIO(), []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        def put(name: str, payload: bytes):
            dst.writestr(name, payload)
            rows.append(_record_row(name, payload))
        if marker:
            put(f"{packages[0]}/{OLDER_MARKER}", b'"""Only the older build the upgrade check installs first has this module."""\n')
        for name in names:
            if name == info + "/RECORD":
                continue
            payload = src.read(name)
            if name == init:
                payload = payload + (b"" if payload.endswith(b"\n") else b"\n") + init_line
            elif name == info + "/METADATA":
                line = f"\nVersion: {m['version']}+{m['label']}\n".encode()
                assert payload.count(line) == 1, f"{name}: no single labelled Version line"
                payload = payload.replace(line, f"\nVersion: {version}\n".encode())
            put(new_info + name[len(info):] if name.startswith(info + "/") else name, payload)
        rows.append([new_info + "/RECORD", "", ""])
        text = io.StringIO()
        csv.writer(text, lineterminator="\n").writerows(rows)
        dst.writestr(new_info + "/RECORD", text.getvalue())
    return out.getvalue()


def _unlabel_wheel(data: bytes) -> bytes:
    """A labelled ICParse / combotest wheel (``data``) as every build made it before the content labels:
    ``<dist>-<version>.dist-info`` and METADATA's bare ``Version:``, plus a marker module and a marker name (OLDER_NAME, in the
    package's ``__init__.py``) the new build does not have."""
    return _rewrite_wheel(data, None, f"{OLDER_NAME} = True   # only the older build of the upgrade check has this name\n".encode(),
                          marker=True)


def make_older_build(new_zip: str, out_zip: str) -> dict:
    """The build to upgrade FROM, made out of the zip under test: version 0.0.1, with ICParse and combotest as every build
    before the content labels carried them (``wheels/icparse-0.1.0-py3-none-any.whl`` holding ``icparse-0.1.0.dist-info``)
    and each with a marker module and a marker name -- the older library files an upgrade has to replace -- and an add-on that
    records nothing on the libraries it loads (``_record_loaded_libraries`` is not called), as every release before 0.4.0.
    Returns {new wheel: older wheel}."""
    with zipfile.ZipFile(new_zip) as src:
        entries = [(zi, src.read(zi)) for zi in src.infolist()]
    renames = {}
    for zi, _data in entries:
        if (m := LABELLED.match(zi.filename)) is not None:
            renames[zi.filename] = f"wheels/{m['dist']}-{m['version']}-{m['tags']}.whl"
    assert sorted(LABELLED.match(k)["dist"] for k in renames) == ["combotest", "icparse"], renames
    with zipfile.ZipFile(out_zip, "w") as dst:
        for zi, data in entries:
            name = zi.filename
            if name in renames:
                name, data = renames[name], _unlabel_wheel(data)
            elif name == "blender_manifest.toml":
                text = data.decode("utf-8")
                for new, older in renames.items():
                    assert text.count(f'"./{new}"') == 1, new
                    text = text.replace(f'"./{new}"', f'"./{older}"')
                text, n = re.subn(r'(?m)^version = "[^"]*"$', 'version = "0.0.1"', text, count=1)
                assert n == 1, "no version line in the manifest"
                data = text.encode("utf-8")
            elif name == "__init__.py":                              # the add-on's own, at the top of the zip
                text = data.decode("utf-8")
                call = "\n_record_loaded_libraries()"
                assert text.count(call) == 1, "the add-on's __init__.py has no single _record_loaded_libraries() call"
                text = text.replace(call, "\npass  # the older build records nothing, as every release before 0.4.0")
                data = text.encode("utf-8")
            out = zipfile.ZipInfo(name, date_time=zi.date_time)
            out.compress_type, out.external_attr = zi.compress_type, zi.external_attr
            dst.writestr(out, data)
    return renames


def make_newer_build(new_zip: str, out_zip: str) -> str:
    """A build to upgrade TO from the zip under test, inside one session: the manifest's patch version one higher, ICParse and
    combotest each with one name only they define (NEWER_NAME, in the package's ``__init__.py``) under a new content label, and
    an add-on that imports that name from both while it loads (right after ``_setup_paths()``) -- the case in which an add-on
    loading against the libraries still in memory failed, and Blender then emptied the shared site-packages. Returns the version."""
    with zipfile.ZipFile(new_zip) as src:
        entries = [(zi, src.read(zi)) for zi in src.infolist()]
    line = f"{NEWER_NAME} = True   # only the newer build of the upgrade check has this name\n".encode()
    renames = {}
    for zi, _data in entries:
        if (m := LABELLED.match(zi.filename)) is not None:
            label = "h" + hashlib.sha256(m["label"].encode() + line).hexdigest()[:12]
            renames[zi.filename] = (f"wheels/{m['dist']}-{m['version']}+{label}-{m['tags']}.whl", label)
    assert sorted(LABELLED.match(k)["dist"] for k in renames) == ["combotest", "icparse"], renames
    version = None
    with zipfile.ZipFile(out_zip, "w") as dst:
        for zi, data in entries:
            name = zi.filename
            if name in renames:
                name, label = renames[name]
                data = _rewrite_wheel(data, label, line)
            elif name == "blender_manifest.toml":
                text = data.decode("utf-8")
                for old, (new, _label) in renames.items():
                    assert text.count(f'"./{old}"') == 1, old
                    text = text.replace(f'"./{old}"', f'"./{new}"')
                m = re.search(r'(?m)^version = "(\d+)\.(\d+)\.(\d+)"$', text)
                assert m, "no plain version line in the manifest"
                version = f"{m[1]}.{m[2]}.{int(m[3]) + 1}"
                text = text[:m.start()] + f'version = "{version}"' + text[m.end():]
                data = text.encode("utf-8")
            elif name == "__init__.py":
                text = data.decode("utf-8")
                call = "\n_setup_paths()\n"
                assert text.count(call) == 1, "the add-on's __init__.py has no single _setup_paths() call"
                text = text.replace(call, call + f"from ICParse import {NEWER_NAME}  # noqa: E402,F401  (needed while the newer build loads)\n"
                                                 f"from combotest import {NEWER_NAME} as _BZ_NEWER_COMBOTEST  # noqa: E402,F401\n")
                data = text.encode("utf-8")
            out = zipfile.ZipInfo(name, date_time=zi.date_time)
            out.compress_type, out.external_attr = zi.compress_type, zi.external_attr
            dst.writestr(out, data)
    assert version, "the manifest was not rewritten"
    return version


def labelled_wheels(zpath: str) -> dict:
    """{distribution: (.dist-info name, version)} of the zip's ICParse and combotest wheels, which must carry their content
    label in the file name AND in the ``.dist-info`` inside (the name Blender's wheel manager goes by)."""
    found = {}
    with zipfile.ZipFile(zpath) as z:
        listed = [w.removeprefix("./") for w in tomllib.loads(z.read("blender_manifest.toml").decode("utf-8"))["wheels"]]
        for w in listed:
            m = LABELLED.match(w)
            if m is None:
                assert not os.path.basename(w).startswith(("icparse-", "combotest-")), f"{w} carries no content label"
                continue
            version = f"{m['version']}+{m['label']}"
            inside = {n.split("/")[0] for n in zipfile.ZipFile(io.BytesIO(z.read(w))).namelist() if n.endswith(".dist-info/RECORD")}
            assert inside == {f"{m['dist']}-{version}.dist-info"}, (w, inside)
            found[m["dist"]] = (f"{m['dist']}-{version}.dist-info", version)
    assert sorted(found) == ["combotest", "icparse"], f"ICParse and combotest must both carry a content label: {listed}"
    return found


def site_packages(res: str):
    found = glob.glob(os.path.join(res, "extensions", ".local", "lib", "python*", "site-packages"))
    return found[0] if found else None


def ours_installed(site) -> dict:
    """{.dist-info: the files its RECORD lists that are missing or differ on disk} of ICParse and combotest in the shared
    site-packages every extension's wheels are extracted into."""
    out = {}
    if not site or not os.path.isdir(site):
        return out
    for info in sorted(os.listdir(site)):
        if not (info.endswith(".dist-info") and info.startswith(("icparse-", "combotest-"))):
            continue
        bad = []
        with open(os.path.join(site, info, "RECORD"), encoding="utf-8", newline="") as f:
            for row in csv.reader(f):
                if len(row) < 3 or not row[1]:
                    continue
                path = os.path.join(site, row[0])
                if not os.path.isfile(path) or _record_row(row[0], open(path, "rb").read())[1] != row[1]:
                    bad.append(row[0])
        out[info] = bad
    return out


def upgrade_probe(res: str, script: str) -> dict:
    r = blender_run(["--python-exit-code", "1", "--python", script], res, {"BZ_SMOKE_MODULE": MODULE})
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("BZ_UPGRADE_JSON ")), None)
    if r.returncode != 0 or line is None:
        say((r.stdout + r.stderr)[-4000:])
        raise AssertionError(f"the probe run failed ({r.returncode})")
    return json.loads(line[len("BZ_UPGRADE_JSON "):])


def check_upgraded(res: str, script: str, new: dict, step: str) -> dict:
    """A fresh Blender on the profile has the add-on enabled and running on the NEW wheels, whose files are all on disk."""
    rep = upgrade_probe(res, script)
    site = site_packages(res)
    assert rep["in_preferences"] and rep["enabled"], (step, "the add-on is not enabled", rep)
    for name, path in rep["modules"].items():
        assert site and path.startswith(os.path.join(site, "")), (step, name, path)     # imported from the installed wheels
    assert rep["versions"] == {"ICParse": new["icparse"][1], "combotest": new["combotest"][1]}, (step, rep["versions"])
    assert rep["blueprint_panel"], (step, "BZ_PT_blueprint is not registered")
    ours = ours_installed(site)
    assert sorted(ours) == sorted(info for info, _version in new.values()), (step, sorted(ours))    # the older ones are gone
    assert not any(ours.values()), (step, "files missing or not as built", ours)
    stale = glob.glob(os.path.join(site, "*", OLDER_MARKER))
    assert not stale, (step, "the older library files were kept", stale)
    return rep


def upgrade_check(zpath: str, work: str, older_zip, keep: bool) -> None:
    """A modder's upgrade in a throw-away profile: (a) an older build installed and enabled, (b) this zip installed over it
    with the same command and no remove in between, (c) checked from a fresh Blender, (d) this zip installed again."""
    new = labelled_wheels(zpath)
    made = older_zip is None
    if made:
        older_zip = os.path.join(work, f"{EXT_ID}-0.0.1.zip")
        make_older_build(zpath, older_zip)
    script = os.path.join(work, "upgrade_probe.py")
    open(script, "w").write(UPGRADE_PROBE)
    install = ["--command", "extension", "install-file", "-r", "user_default", "-e"]     # the modder's own command
    res = tempfile.mkdtemp(prefix="bz_upgrade_blender_")
    try:
        r = blender_run([*install, older_zip], res)                                       # (a)
        out = (r.stdout + r.stderr).strip()
        assert r.returncode == 0 and "Installed" in out, f"installing the older build failed ({r.returncode}):\n{out[-1500:]}"
        rep = upgrade_probe(res, script)
        assert rep["in_preferences"] and rep["enabled"], ("the older build is not enabled", rep)
        site = site_packages(res)
        older = ours_installed(site)
        assert older, ("the older build installed no ICParse / combotest", older)
        # a real older release may share a library with this zip that did not change between them (0.3.1 and 0.4.0 carry the same
        # combotest): Blender rightly keeps that one; the older build made here differs in both
        shared = sorted(set(older) & {info for info, _version in new.values()})
        assert not made or not shared, ("the older build made here must name both libraries differently", older)
        assert not made or len(glob.glob(os.path.join(site, "*", OLDER_MARKER))) == 2, "the older build's marker modules are not installed"
        say(f"upgrade: older build {os.path.basename(older_zip)} installed and enabled, with {', '.join(older)}"
            + (f" ({', '.join(shared)} unchanged in this zip)" if shared else ""))
        for step in ("upgrade", "reinstall"):                                            # (b) + (c), then (d)
            r = blender_run([*install, zpath], res)
            out = (r.stdout + r.stderr).strip()
            assert r.returncode == 0 and "Reinstalled" in out, f"the {step} failed ({r.returncode}):\n{out[-1500:]}"
            rep = check_upgraded(res, script, new, step)
            say(f"  {step} with the same command: enabled, ICParse {rep['versions']['ICParse']} and combotest "
                f"{rep['versions']['combotest']} from the new wheels (every file as built, the older ones gone), "
                f"ICParse.ebp.scene and ICParse.sgm.creature.layout import, BZ_PT_blueprint registered")
    finally:
        if keep:
            say(f"kept: {res}")
        else:
            shutil.rmtree(res, ignore_errors=True)


# ------------------------------------------------------------------------------------------- 6. the upgrade inside one session
def in_session_check(zpath: str, work: str, older_zip, keep: bool) -> None:
    """An upgrade inside one Blender session (see 6. in the module docstring): the older build installed and enabled on a
    throw-away profile, then ONE Blender installs this zip, the newer build made from it, and the newer build again with
    ``bpy.ops.extensions.package_install_files``; after each, the session must run on the libraries just installed."""
    new = labelled_wheels(zpath)
    made = older_zip is None
    if made:
        older_zip = os.path.join(work, f"{EXT_ID}-0.0.1-session.zip")
        make_older_build(zpath, older_zip)
    newer_version = make_newer_build(zpath, os.path.join(work, "newer.zip"))
    newer_zip = os.path.join(work, f"{EXT_ID}-{newer_version}.zip")
    os.replace(os.path.join(work, "newer.zip"), newer_zip)
    newer = labelled_wheels(newer_zip)
    assert not set(newer.values()) & set(new.values()), "the newer build must carry other library labels"
    script, probe = os.path.join(work, "in_session.py"), os.path.join(work, "upgrade_probe.py")
    open(script, "w").write(IN_SESSION)
    open(probe, "w").write(UPGRADE_PROBE)
    res = tempfile.mkdtemp(prefix="bz_session_blender_")
    try:
        r = blender_run(["--command", "extension", "install-file", "-r", "user_default", "-e", older_zip], res)
        out = (r.stdout + r.stderr).strip()
        assert r.returncode == 0 and "Installed" in out, f"installing the older build failed ({r.returncode}):\n{out[-1500:]}"
        steps = [("this zip over the older build", zpath, new, False),
                 (f"the newer build {newer_version}, whose add-on needs a new library name while it loads", newer_zip, newer, False),
                 ("the newer build again", newer_zip, newer, True)]
        r = blender_run(["--python-exit-code", "1", "--python", script], res,
                        {"BZ_SMOKE_MODULE": MODULE, "BZ_SESSION_ZIPS": json.dumps([z for _w, z, _l, _k in steps])}, timeout=900)
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("BZ_SESSION_JSON ")), None)
        if r.returncode != 0 or line is None:
            say((r.stdout + r.stderr)[-4000:])
            raise AssertionError(f"the in-session run failed ({r.returncode})")
        rep = json.loads(line[len("BZ_SESSION_JSON "):])
        site = site_packages(res)
        before = rep["before"]
        assert before["enabled"] and not before.get("import_errors"), ("the older build is not enabled and loaded", before)
        assert not made or before["older"] == {"ICParse": True, "combotest": True}, ("the older libraries are not the ones in memory", before)
        say(f"in-session: older build {os.path.basename(older_zip)} enabled, its ICParse {before['versions']['ICParse']} and combotest "
            f"{before['versions']['combotest']} in memory")
        assert len(rep["steps"]) == len(steps), rep["steps"]
        for (what, _zip, wheels, kept), step in zip(steps, rep["steps"]):
            ctx = (what, step)
            assert step["result"] == ["FINISHED"], ctx
            assert step["enabled"] and step["blueprint_panel"] and not step.get("import_errors"), ctx
            for name, path in step["files"].items():
                assert path and site and path.startswith(os.path.join(site, "")), (what, name, path)
            assert step["versions"] == {"ICParse": wheels["icparse"][1], "combotest": wheels["combotest"][1]}, ctx
            assert step["older"] == {"ICParse": False, "combotest": False}, ("the older library is still in use", ctx)
            is_newer = wheels is newer
            assert step["newer"] == {"ICParse": is_newer, "combotest": is_newer}, ("not the library just installed", ctx)
            assert step["kept"] == {"ICParse": kept, "combotest": kept}, ("loaded again" if kept else "kept in memory", ctx)
            assert step["unloaded"] == ([] if kept else ["ICParse", "combotest"]), ctx
            assert step["stale_references"] == [], ("the add-on holds objects of the libraries from before", ctx)
            say(f"  {what}: enabled in the same session, ICParse {step['versions']['ICParse']} and combotest "
                f"{step['versions']['combotest']} from the new wheels, "
                + ("kept in memory (same files)" if kept else "the ones in memory unloaded and loaded again") + ", no stale reference")
        rep2 = check_upgraded(res, probe, newer, "after the session")
        say(f"  a fresh Blender afterwards: enabled, ICParse {rep2['versions']['ICParse']} and combotest {rep2['versions']['combotest']}, "
            f"every file of the newer wheels on disk, nothing older left")
    finally:
        if keep:
            say(f"kept: {res}")
        else:
            shutil.rmtree(res, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=None, help="use this zip instead of building one")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temp Blender config")
    ap.add_argument("--older-zip", default=None, help="upgrade from this build instead of one made from the zip under test")
    a = ap.parse_args(argv)

    t0 = time.time()
    zpath = os.path.abspath(a.zip) if a.zip else build_zip(a.rebuild)
    size = os.path.getsize(zpath)
    say(f"zip {zpath} ({size / 1e6:.2f} MB)")
    assert size > 1e6, f"the zip is only {size} bytes: the wheels are missing"

    res = tempfile.mkdtemp(prefix="bz_clean_blender_")
    work = tempfile.mkdtemp(prefix="bz_clean_work_")
    try:
        # ---------------------------------------------------------------- 2. install into a clean config
        r = blender_run(["--factory-startup", "--command", "extension", "install-file",
                         "-r", "user_default", "-e", zpath], res)
        say((r.stdout + r.stderr).strip()[-1500:])
        assert r.returncode == 0 and "Installed" in (r.stdout + r.stderr), f"install failed ({r.returncode})"
        ext_dir = os.path.join(res, "extensions", "user_default", EXT_ID)
        site = glob.glob(os.path.join(res, "extensions", ".local", "lib", "python*", "site-packages"))
        assert os.path.isdir(ext_dir), ext_dir
        assert site, "no site-packages: the wheels were not installed"
        installed = sorted(d for d in os.listdir(site[0]) if not d.endswith(".dist-info"))
        say(f"installed into {ext_dir}\n  wheels -> {os.path.basename(site[0])}: {', '.join(installed)}")
        for want in ("ICParse", "construct", "combotest", "lupa"):
            assert want in installed, f"{want} is not in the installed wheels: {installed}"

        # ---------------------------------------------------------------- 3. use it, with no repo in reach
        sgm = os.path.join(work, "cheetah.sgm")
        shutil.copyfile(CREATURE, sgm)
        script = os.path.join(work, "smoke.py")
        open(script, "w").write(SMOKE)
        out = os.path.join(work, "out.sgm")
        r = blender_run(["--factory-startup", "--python-exit-code", "1", "--python", script], res,
                        {"BZ_SMOKE_MODULE": MODULE, "BZ_SMOKE_SGM": sgm, "BZ_SMOKE_OUT": out, "BZ_SMOKE_REPO": REPO})
        text = r.stdout + r.stderr
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("BZ_SMOKE_JSON ")), None)
        if r.returncode != 0 or line is None:
            say(text[-4000:])
            raise AssertionError(f"the smoke run failed ({r.returncode})")
        rep = json.loads(line[len("BZ_SMOKE_JSON "):])

        assert rep["repo_on_path"] == [], rep["repo_on_path"]
        assert rep["bz_env"] == {}, rep["bz_env"]
        for name in ("ICParse", "construct", "combotest", "lupa"):
            dep = rep["deps"][name]
            assert "error" not in dep, (name, dep)
            assert dep["under_resources"], (name, dep["file"])
        for name in ("ICParse", "construct", "combotest", "lupa"):
            st = rep["paths_status"][name]
            assert st["source"] == "importable", (name, st)     # the wheels won: _setup_paths added nothing
        assert rep["import"]["points"] == 2594 and rep["import"]["patches"] == 288, rep["import"]
        assert rep["issues"] == [], rep["issues"]
        blocking = [ln for ln in rep["validate"] if ln.startswith("ERROR") or "BLOCKED" in ln]
        assert not blocking, blocking
        assert rep["export"]["identical"], rep["export"]
        so = rep["sceneobj"]
        assert "model" in so["on_disk"] and "ui_kit" in so["on_disk"] and "prefs" in so["on_disk"], so       # the zip carries sceneobj/
        want = sorted(m for m in so["on_disk"] if m in so["core"] or m.startswith(tuple(so["prefixes"])))
        assert so["loaded"] == want, so                      # every core / discovered module of the zip loaded
        assert so["errors"] == [] and so["problems"] == [], so
        assert so["classes"] and so["registered"] == so["classes"] and so["all_registered"], so
        assert so["prefs"] == {"type": "BZ_AP_prefs", "module": MODULE, "target_mod": "icmod"}, so["prefs"]
        say(f"  sceneobj: {len(so['on_disk'])} module(s) in the zip, {len(so['classes'])} class(es) registered, preferences live as {so['prefs']['module']}")
        say(f"clean Blender: {rep['import']['name']} {rep['import']['points']} points / "
            f"{rep['import']['patches']} patches imported, Validate {len(rep['validate'])} line(s) 0 blocking "
            f"({'; '.join(ln[:70] for ln in rep['validate'])}), "
            f"Export {rep['export']['bytes']} bytes BYTE-IDENTICAL, 0 action-time issues")
        say("  every dependency imported from the installed wheels: "
            + ", ".join(f"{k} {os.path.basename(os.path.dirname(v['file']))}" for k, v in rep["deps"].items()))
        comb = rep.get("combiner", {})
        assert comb.get("lupa") and comb.get("lua"), comb
        say(f"  combiner: combotest ok, lupa {comb.get('lupa')} running {comb.get('lua')}")

        # ---------------------------------------------------------------- 4. remove it again
        r = blender_run(["--factory-startup", "--command", "extension", "remove", EXT_ID], res)
        say((r.stdout + r.stderr).strip()[-600:])
        assert r.returncode == 0, f"remove failed ({r.returncode})"
        assert not os.path.isdir(ext_dir), f"{ext_dir} is still there after remove"
        left = sorted(d for d in os.listdir(site[0]) if not d.endswith(".dist-info")) if os.path.isdir(site[0]) else []
        say(f"removed: the add-on directory is gone, site-packages left with {left or 'nothing'}")

        # ---------------------------------------------------------------- 5. upgrade over an older build, as a modder does
        upgrade_check(zpath, work, os.path.abspath(a.older_zip) if a.older_zip else None, a.keep)

        # ---------------------------------------------------------------- 6. upgrade inside one Blender session
        in_session_check(zpath, work, os.path.abspath(a.older_zip) if a.older_zip else None, a.keep)
        say(f"PASS in {time.time() - t0:.1f}s")
        return 0
    finally:
        if a.keep:
            say(f"kept: {res} and {work}")
        else:
            shutil.rmtree(res, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        open("/tmp/bz_extension_install_done.txt", "w").write("ERROR\n" + traceback.format_exc())
        sys.exit(1)
    open("/tmp/bz_extension_install_done.txt", "w").write("PASS\n" if code == 0 else "FAIL\n")
    sys.exit(code)
