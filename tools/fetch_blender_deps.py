"""Fetch the compiled dependency the combiner needs inside Blender (lupa) into the git-ignored _deps_bin/.

    .venv/bin/python3 blender_native/tools/fetch_blender_deps.py                    # `blender` on $BLENDER / PATH
    .venv/bin/python3 blender_native/tools/fetch_blender_deps.py --blender /snap/bin/blender --lupa 2.8

Blender ships its own Python (Blender 5.x: 3.13) without lupa, which combotest needs to run the game's
attrcombiner.lua (combined ratings, cost, rank). This tool asks Blender for its bundled interpreter and
platform tag, has THAT interpreter's pip *download* the matching binary wheel (``--only-binary=:all:
--no-deps``: nothing is installed into Blender or any venv) into a scratch directory, and unpacks the wheel
into ``blender_native/_deps_bin/<cpXY-platform>/`` (a wheel is a zip of the installed package tree). The
add-on's ``__init__._setup_paths()`` puts that directory on sys.path when lupa does not already import.
Finally it imports lupa in headless Blender from there and fails loudly if that does not work.

_deps_bin/ is git-ignored (compiled, per platform): never commit it.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPS_BIN = os.path.join(ADDON, "_deps_bin")

# Must match blender_native/__init__.py::_platform_tag().
_PROBE = ("import json, sys, sysconfig; plat = sysconfig.get_platform().replace('-', '_').replace('.', '_'); "
          "print('BZ_PY_PROBE ' + json.dumps({'executable': sys.executable, 'version': sys.version.split()[0], "
          "'tag': 'cp%d%d-%s' % (sys.version_info.major, sys.version_info.minor, plat)}))")


def _clean_env():
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)        # the snap leaks its runtime libs into children
    env.pop("PYTHONPATH", None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def _probe_line(out: str) -> dict:
    for line in out.splitlines():
        if line.startswith("BZ_PY_PROBE "):
            return json.loads(line[len("BZ_PY_PROBE "):])
    raise SystemExit("could not read the interpreter probe; output was:\n" + out[-2000:])


def blender_python(blender: str) -> dict:
    """{executable, version, tag} of the Python bundled with ``blender``."""
    r = subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "1",
                        "--python-expr", _PROBE], capture_output=True, text=True, timeout=180, env=_clean_env())
    if r.returncode != 0:
        raise SystemExit(f"{blender} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-2000:]}")
    return _probe_line(r.stdout)


def python_info(python: str) -> dict:
    r = subprocess.run([python, "-c", _PROBE], capture_output=True, text=True, timeout=60, env=_clean_env())
    if r.returncode != 0:
        raise SystemExit(f"{python} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-2000:]}")
    return _probe_line(r.stdout)


def download_wheel(python: str, requirement: str, scratch: str) -> str:
    cmd = [python, "-m", "pip", "download", requirement, "--only-binary=:all:", "--no-deps", "-d", scratch]
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=_clean_env())
    print((r.stdout + r.stderr).strip(), flush=True)
    if r.returncode != 0:
        raise SystemExit(f"pip download failed ({r.returncode})")
    name = requirement.split("=")[0].split("<")[0].split(">")[0].strip().lower()
    wheels = sorted(glob.glob(os.path.join(scratch, f"{name}-*.whl")))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one {name} wheel in {scratch}, found {wheels}")
    return wheels[0]


def unpack(wheel: str, dest: str, info: dict) -> dict:
    if os.path.isdir(dest) and os.listdir(dest):
        ours = (os.path.exists(os.path.join(dest, "FETCHED.json"))
                or os.path.commonpath([os.path.abspath(dest), DEPS_BIN]) == DEPS_BIN)
        if not ours:
            raise SystemExit(f"refusing to replace {dest}: not empty and not a directory this tool filled")
        shutil.rmtree(dest)                  # replace, never mix two versions
    os.makedirs(dest)
    with zipfile.ZipFile(wheel) as z:
        z.extractall(dest)
    digest = hashlib.sha256(open(wheel, "rb").read()).hexdigest()
    record = {"wheel": os.path.basename(wheel), "sha256": digest, "python": info["version"],
              "interpreter": info["executable"], "tag": info["tag"],
              "fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    with open(os.path.join(dest, "FETCHED.json"), "w") as f:
        json.dump(record, f, indent=2)
    return record


def verify_in_blender(blender: str, dest: str) -> str:
    expr = ("import sys; sys.path.insert(0, %r); import lupa; from lupa import lua54; "
            "assert lupa.__file__.startswith(%r), lupa.__file__; "
            "print('BZ_LUPA_OK', getattr(lupa, '__version__', '?'), lupa.__file__, "
            "lua54.LuaRuntime().lua_implementation)") % (dest, dest)
    r = subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "1",
                        "--python-expr", expr], capture_output=True, text=True, timeout=180, env=_clean_env())
    line = next((l for l in r.stdout.splitlines() if l.startswith("BZ_LUPA_OK")), None)
    if r.returncode != 0 or line is None:
        raise SystemExit(f"lupa does not import in Blender from {dest} ({r.returncode}):\n"
                         f"{(r.stdout + r.stderr)[-2000:]}")
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--blender", default=os.environ.get("BLENDER") or shutil.which("blender") or "blender",
                    help="Blender binary whose bundled Python the wheel is for (default: $BLENDER, else PATH)")
    ap.add_argument("--python", default=None,
                    help="use this interpreter instead of asking Blender (the wheel must match Blender's Python)")
    ap.add_argument("--lupa", default="2.8", help="lupa version to fetch (default 2.8)")
    ap.add_argument("--dest", default=None,
                    help="unpack into this directory (default: blender_native/_deps_bin/<cpXY-platform>)")
    ap.add_argument("--scratch", default=None, help="download directory (default: a temporary directory)")
    ap.add_argument("--no-verify", action="store_true", help="skip the import check in headless Blender")
    a = ap.parse_args(argv)

    info = python_info(a.python) if a.python else blender_python(a.blender)
    print(f"interpreter {info['executable']} (Python {info['version']}, {info['tag']})", flush=True)
    dest = os.path.abspath(a.dest or os.path.join(DEPS_BIN, info["tag"]))
    scratch = a.scratch or tempfile.mkdtemp(prefix="bz_fetch_deps_")
    os.makedirs(scratch, exist_ok=True)
    try:
        wheel = download_wheel(info["executable"], f"lupa=={a.lupa}", scratch)
        record = unpack(wheel, dest, info)
        print(f"unpacked {record['wheel']} (sha256 {record['sha256'][:16]}...) -> {dest}", flush=True)
    finally:
        if not a.scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    if not a.no_verify and not a.python:
        print(verify_in_blender(a.blender, dest), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
