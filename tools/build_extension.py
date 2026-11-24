"""Build the distributable Blender extension (.zip) for a modder who has none of this repo.

    .venv/bin/python3 blender_native/tools/build_extension.py                       # -> blender_native/dist/*.zip
    .venv/bin/python3 blender_native/tools/build_extension.py --blender /snap/bin/blender --lupa 2.8
    .venv/bin/python3 blender_native/tools/build_extension.py --no-lupa --no-combotest   # the smallest working add-on

In the repo the add-on finds its dependencies through ``__init__._setup_paths()`` (sibling directories and
``$BZ_*``). A modder has none of those, so the extension carries them as **wheels**, which Blender installs into the
extension's own ``site-packages`` when it is enabled. ``_setup_paths`` is import-first, so with the wheels in place
it adds nothing at all and the same code runs either way (``PATHS_STATUS`` says which happened).

What goes in:

* **ICParse** and **combotest** -- wheels built from this repo with ``uv build`` (both are hatchling packages).
  combotest's own ``icrender`` dependency is NOT bundled: nothing the add-on imports from combotest touches it
  (only ``preview.py`` / ``ui.py`` do), and Blender installs the wheels it is given without resolving dependencies.
  Both get a **local version label** naming their contents, ``icparse-0.1.0+h<12 hex digits>-py3-none-any.whl``
  (see ``relabel_wheel``): Blender keeps an installed wheel whose ``.dist-info`` name has not changed, so without the
  label a new build installed over an older one kept the older library.
* **construct** -- the pure-python wheel from PyPI (ICParse's byte-layout schemas). The committed ``_deps/construct``
  travels too, as the fallback ``_setup_paths`` uses if a wheel install ever fails.
* **lupa** -- compiled, one wheel per platform, for the Python of the Blender you build against (5.x = 3.13).
  PyPI has cp313 lupa for **linux x86_64, linux aarch64, macOS arm64 and Windows x64** and no others, so an Intel Mac
  or an ARM Windows gets the extension without lupa -- which is a *degrade*, not a break: the add-on registers, and
  the Combine Preview status box says combined ratings, cost and rank can't be computed. Blender installs only the
  wheels whose tags match the machine and skips the rest, so ONE zip serves every platform (``--split-platforms``
  makes per-platform zips instead, at the cost of a modder having to pick).

Wheels are fetched from PyPI's JSON API (no pip needed) and verified against the sha256 PyPI publishes.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.dirname(HERE)
REPO = os.path.dirname(ADDON)

# the add-on's own files: every module, the sceneobj package, the bundled pure-python construct, and the README a modder reads
COPY_DIRS = ("_deps", "sceneobj")
SKIP_DIRS = {"tests", "tools", "_deps_bin", "__pycache__", "dist", "docs", ".git"}
COPY_FILES = ("README.md",)

# lupa: the wheel tags to look for, in Blender-platform order. A tag missing from PyPI is reported, not fatal.
LUPA_TAGS = (("linux-x64", ("manylinux", "x86_64")),
             ("linux-arm64", ("manylinux", "aarch64")),
             ("macos-arm64", ("macosx", "arm64")),
             ("macos-x64", ("macosx", "x86_64")),
             ("windows-x64", ("win_amd64",)),
             ("windows-arm64", ("win_arm64",)))

MANIFEST = """schema_version = "1.0.0"

id = "sgm_native_patches"
version = "{version}"
name = "SGM Native Patches"
tagline = "Make and edit Impossible Creatures models in Blender"
maintainer = "akipina (github.com/akipina)"
type = "add-on"

blender_version_min = "{blender_min}"
license = ["SPDX:GPL-3.0-or-later"]
tags = ["Import-Export", "Mesh", "Modeling"]

# Built by blender_native/tools/build_extension.py -- edit that, not this file.
# The add-on's dependencies as wheels: Blender installs them into the extension's own site-packages when it is
# enabled, and __init__._setup_paths() (import-first) then adds nothing. lupa is compiled and per platform;
# Blender installs only the wheels whose tags match the machine and skips the rest, so this one zip serves every
# platform -- where lupa has no wheel, the add-on runs without the combiner's Lua ratings and says so.
wheels = [
{wheels}
]

[build]
paths_exclude_pattern = [
  "__pycache__/",
  "*.pyc",
  ".*",
]
"""


def _clean_env():
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)         # the snap leaks its runtime libs into children
    env.pop("PYTHONPATH", None)
    return env


def blender_python(blender: str) -> dict:
    """{executable, version, tag} of the Python bundled with ``blender`` (the same probe fetch_blender_deps uses)."""
    sys.path.insert(0, HERE)
    import fetch_blender_deps
    return fetch_blender_deps.blender_python(blender)


# ------------------------------------------------------------------------------------------- PyPI
def pypi_files(project: str, version: str) -> list:
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)["urls"]


def download(rec: dict, dest_dir: str, cache: str | None = None) -> str:
    """One PyPI file in ``dest_dir``, checked against the sha256 PyPI publishes. ``cache``: a directory kept between
    builds -- a file there with the right digest is used instead of downloading (so a rebuild needs no network)."""
    want = rec["digests"]["sha256"]
    cached = os.path.join(cache, rec["filename"]) if cache else None
    path = os.path.join(dest_dir, rec["filename"])
    if cached and os.path.exists(cached) and hashlib.sha256(open(cached, "rb").read()).hexdigest() == want:
        shutil.copy2(cached, path)
        return path
    with urllib.request.urlopen(rec["url"], timeout=300) as r:
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise SystemExit(f"{rec['filename']}: sha256 {got} but PyPI says {want}")
    with open(path, "wb") as f:
        f.write(data)
    if cached:
        os.makedirs(cache, exist_ok=True)
        shutil.copy2(path, cached)
    return path


def fetch_construct(version: str, dest: str, cache=None) -> str:
    recs = [u for u in pypi_files("construct", version) if u["filename"].endswith("-py3-none-any.whl")]
    if len(recs) != 1:
        raise SystemExit(f"expected one pure-python construct {version} wheel, found {[r['filename'] for r in recs]}")
    return download(recs[0], dest, cache)


def fetch_lupa(version: str, py_tag: str, dest: str, cache=None) -> tuple:
    """(paths, missing platforms) of the lupa wheels for ``py_tag`` (e.g. 'cp313')."""
    recs = [u for u in pypi_files("lupa", version)
            if u["filename"].endswith(".whl") and f"-{py_tag}-" in u["filename"]]
    out, missing = [], []
    for plat, needles in LUPA_TAGS:
        hit = next((r for r in recs if all(n in r["filename"] for n in needles)), None)
        if hit is None:
            missing.append(plat)
            continue
        out.append(download(hit, dest, cache))
    return out, missing


# ------------------------------------------------------------------------------------------- repo wheels
def build_local_wheel(package: str, dest: str) -> str:
    """Build ``package`` of this uv workspace into a wheel (hatchling, through ``uv build``)."""
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    if not os.path.exists(uv):
        raise SystemExit("uv is not installed, so the repo's ICParse / combotest wheels can't be built: install uv "
                         "(the repo's environment is a uv workspace) or pass --no-combotest and build ICParse by hand")
    before = set(os.listdir(dest)) if os.path.isdir(dest) else set()
    cmd = [uv, "build", "--package", package, "--wheel", "--out-dir", dest]
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=900, env=_clean_env())
    if r.returncode != 0:
        raise SystemExit(f"uv build {package} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    made = sorted(f for f in set(os.listdir(dest)) - before if f.endswith(".whl"))
    if len(made) != 1:
        raise SystemExit(f"expected one new wheel for {package}, got {made}")
    return os.path.join(dest, made[0])


# ------------------------------------------------------------------------------------------- wheel labels
# A wheel's file name (PEP 427): {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
WHEEL_NAME = re.compile(r"^(?P<dist>[^-]+)-(?P<version>[^-]+)(?:-(?P<build>\d[^-]*))?"
                        r"-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")


def wheel_content_label(path: str) -> str:
    """``h`` and 12 hex digits of a sha256 over every file in the wheel (its name and its bytes; RECORD, which only lists
    them, is left out). hatchling builds are reproducible, so the same library source gives the same label and any change
    to it a new one. The ``h`` keeps the label from ever being all digits, which packaging tools would rewrite (they drop
    the leading zeros of a numeric segment)."""
    h = hashlib.sha256()
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if name.endswith(".dist-info/RECORD"):
                continue
            h.update(name.encode("utf-8") + b"\0" + hashlib.sha256(z.read(name)).digest())
    return "h" + h.hexdigest()[:12]


def _zipinfo_like(zi: zipfile.ZipInfo, name: str) -> zipfile.ZipInfo:
    out = zipfile.ZipInfo(name, date_time=zi.date_time)
    out.compress_type, out.external_attr, out.create_system = zi.compress_type, zi.external_attr, zi.create_system
    return out


def _record_digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _metadata_with_version(data: bytes, old: str, new: str, wheel: str) -> bytes:
    """METADATA with its one ``Version:`` header changed (the headers only, never the description below them)."""
    head, sep, body = data.decode("utf-8").partition("\n\n")
    lines = head.split("\n")
    hits = [i for i, line in enumerate(lines) if line == f"Version: {old}"]
    if len(hits) != 1:
        raise SystemExit(f"{wheel}: METADATA has {len(hits)} 'Version: {old}' header lines, expected 1")
    lines[hits[0]] = f"Version: {new}"
    return ("\n".join(lines) + sep + body).encode("utf-8")


def relabel_wheel(path: str) -> str:
    """Give a wheel built from this repo the local version label of its contents (PEP 440 ``<version>+<label>``) and
    return its new path; the unlabelled file is removed.

    Why: Blender installs an extension's wheels with ``apply_action`` in
    ``scripts/modules/_bpy_internal/extensions/wheel_manager.py``. It names each wanted wheel by the ``*.dist-info``
    directory inside it (``_wheel_info_dir_from_zip``) and each installed one by its ``*.dist-info`` directory in the
    shared ``extensions/.local/lib/python3.X/site-packages`` (``_wheels_from_dir``); an installed name that is still
    wanted is kept as it is, one that is not is removed, and only the wanted names not installed yet are extracted.
    ICParse and combotest keep their versions (0.1.0, 0.0.1) from build to build, so every build carried
    ``icparse-0.1.0.dist-info``, and a new build installed over an older one kept the older library's files. A PEP 427
    build tag would not help: it is in the file name only, never in the ``.dist-info`` name. So the label goes into the
    version: the ``.dist-info`` (and any ``.data``) directory, METADATA's ``Version:``, RECORD and the file name carry
    it. The library's own files are not touched."""
    wheel = os.path.basename(path)
    m = WHEEL_NAME.match(wheel)
    if m is None:
        raise SystemExit(f"{wheel} is not a wheel file name")
    dist, version = m["dist"], m["version"]
    if "+" in version:
        raise SystemExit(f"{wheel} already has a local version label")
    new_version = f"{version}+{wheel_content_label(path)}"
    old_info, new_info = f"{dist}-{version}.dist-info/", f"{dist}-{new_version}.dist-info/"
    renames = ((old_info, new_info), (f"{dist}-{version}.data/", f"{dist}-{new_version}.data/"))
    out = os.path.join(os.path.dirname(path), "-".join(
        part for part in (dist, new_version, m["build"], m["py"], m["abi"], m["plat"]) if part) + ".whl")
    with zipfile.ZipFile(path) as src:
        members = src.infolist()
        if not {old_info + "METADATA", old_info + "RECORD"} <= {zi.filename for zi in members}:
            raise SystemExit(f"{wheel} has no {old_info} with METADATA and RECORD")
        rows, record_zi = [], None
        with zipfile.ZipFile(out, "w") as dst:
            for zi in members:
                name = zi.filename
                for old, new in renames:
                    if name.startswith(old):
                        name = new + name[len(old):]
                if name == new_info + "RECORD":
                    record_zi = zi                     # written last, listing everything before it
                    continue
                data = src.read(zi)
                if name == new_info + "METADATA":
                    data = _metadata_with_version(data, version, new_version, wheel)
                dst.writestr(_zipinfo_like(zi, name), data)
                if not zi.is_dir():
                    rows.append((name, _record_digest(data), str(len(data))))
            rows.append((new_info + "RECORD", "", ""))
            text = io.StringIO()
            csv.writer(text, lineterminator="\n").writerows(rows)
            dst.writestr(_zipinfo_like(record_zi, new_info + "RECORD"), text.getvalue().encode("utf-8"))
    os.remove(path)
    return out


# ------------------------------------------------------------------------------------------- staging
def stage_addon(staging: str) -> int:
    """Copy the add-on's own files into ``staging`` (no tests, no tools, no fetched binaries). Returns the count."""
    n = 0
    for name in sorted(os.listdir(ADDON)):
        src = os.path.join(ADDON, name)
        if name in SKIP_DIRS or name.startswith("."):
            continue
        if os.path.isdir(src):
            if name not in COPY_DIRS:
                continue
            shutil.copytree(src, os.path.join(staging, name),
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            n += sum(len(fs) for _d, _s, fs in os.walk(os.path.join(staging, name)))
        elif name.endswith(".py") or name in COPY_FILES:
            shutil.copy2(src, os.path.join(staging, name))
            n += 1
    return n


def addon_version() -> str:
    """The add-on's version, from ``bl_info`` in __init__.py (read as text: bpy is not importable here)."""
    import ast
    src = open(os.path.join(ADDON, "__init__.py")).read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "bl_info" for t in node.targets):
            info = ast.literal_eval(node.value)
            return ".".join(str(i) for i in info["version"])
    raise SystemExit("no bl_info version in __init__.py")


def blender_min() -> str:
    import ast
    src = open(os.path.join(ADDON, "__init__.py")).read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "bl_info" for t in node.targets):
            return ".".join(str(i) for i in ast.literal_eval(node.value)["blender"])
    return "4.2.0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--blender", default=os.environ.get("BLENDER") or shutil.which("blender") or "blender",
                    help="the Blender that builds the zip and whose Python the lupa wheels are for")
    ap.add_argument("--output-dir", default=os.path.join(ADDON, "dist"))
    ap.add_argument("--output-filepath", default=None, help="the .zip to write (default: <output-dir>/<id>-<version>.zip)")
    ap.add_argument("--lupa", default="2.8", help="lupa version to bundle (default 2.8)")
    ap.add_argument("--no-lupa", action="store_true", help="leave lupa out (the combiner then has no Lua ratings)")
    ap.add_argument("--construct", default="2.10.70", help="construct version to bundle")
    ap.add_argument("--no-combotest", action="store_true", help="leave the combiner out (no Combine Preview)")
    ap.add_argument("--version", default=None, help="override the extension version")
    ap.add_argument("--split-platforms", action="store_true", help="one zip per platform instead of one for all")
    ap.add_argument("--wheel-cache", default=os.path.join(ADDON, "dist", "_wheels"),
                    help="keep the PyPI wheels here between builds (a rebuild then needs no network); '' to disable")
    ap.add_argument("--keep-staging", action="store_true")
    a = ap.parse_args(argv)
    cache = os.path.abspath(a.wheel_cache) if a.wheel_cache else None

    info = blender_python(a.blender)
    py_tag = info["tag"].split("-", 1)[0]                  # cp313
    print(f"Blender {a.blender}: Python {info['version']} ({py_tag})", flush=True)
    version = a.version or addon_version()
    staging = tempfile.mkdtemp(prefix="bz_ext_")
    wheels_dir = os.path.join(staging, "wheels")
    os.makedirs(wheels_dir)
    try:
        n_files = stage_addon(staging)
        wheels, notes = [], []
        wheels.append(fetch_construct(a.construct, wheels_dir, cache))
        for pkg in ("ICParse",) + (() if a.no_combotest else ("combotest",)):
            wheels.append(relabel_wheel(build_local_wheel(pkg, wheels_dir)))
            print(f"  labelled {os.path.basename(wheels[-1])}", flush=True)
        if a.no_combotest:
            notes.append("combotest left out: Combine Preview and the combine checks are unavailable")
        if a.no_lupa:
            notes.append("lupa left out: combined ratings, cost and rank can't be computed")
        else:
            got, missing = fetch_lupa(a.lupa, py_tag, wheels_dir, cache)
            wheels += got
            notes.append(f"lupa {a.lupa} for {py_tag}: {len(got)} platform wheel(s)"
                         + (f"; none on PyPI for {', '.join(missing)} (those degrade: no Lua ratings)" if missing else ""))
        rel = "\n".join(f'  "./wheels/{os.path.basename(w)}",' for w in wheels)
        with open(os.path.join(staging, "blender_manifest.toml"), "w") as f:
            f.write(MANIFEST.format(version=version, blender_min=blender_min(), wheels=rel))

        out_dir = os.path.abspath(a.output_dir)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.abspath(a.output_filepath) if a.output_filepath \
            else os.path.join(out_dir, f"sgm_native_patches-{version}.zip")
        cmd = [a.blender, "--background", "--factory-startup", "--command", "extension", "build",
               "--source-dir", staging, "--output-filepath", out]
        if a.split_platforms:
            cmd.append("--split-platforms")
        print("+", " ".join(cmd), flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=_clean_env())
        print((r.stdout + r.stderr).strip(), flush=True)
        if r.returncode != 0:
            raise SystemExit(f"extension build failed ({r.returncode})")
        built = [out] if os.path.exists(out) else sorted(
            os.path.join(out_dir, f) for f in os.listdir(out_dir)
            if f.startswith(f"sgm_native_patches-{version}") and f.endswith(".zip"))
        for p in built:
            print(f"\n{p}  {os.path.getsize(p) / 1e6:.2f} MB", flush=True)
        print(f"  {n_files} add-on files, {len(wheels)} wheels:", flush=True)
        for w in wheels:
            print(f"    {os.path.basename(w)}  {os.path.getsize(w) / 1e6:.2f} MB", flush=True)
        for note in notes:
            print(f"  note: {note}", flush=True)
        return 0
    finally:
        if a.keep_staging:
            print(f"staging kept at {staging}", flush=True)
        else:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
