"""Shared helpers of the sceneobj tests (PLAN_FINAL section 7.4). Every ``tests/so_*_test.py`` uses these.

Stdlib only, so it imports in the venv python and in Blender alike (the in-Blender helpers import ``bpy`` when called).

* ``tmpdir()`` -- a fresh temp folder per call (under ``$BZ_TMP`` when set), removed at exit; never a fixed ``/tmp/bz_*`` path, so two
  agents running one test at the same time cannot overwrite each other. ``done_file`` / ``run_test`` name their result file with the pid;
* ``blender_lock()`` / ``run_locked()`` -- the machine-wide lock around launching Blender (the snap Blender degrades when many are
  started at once): one launch at a time, at least a second after the last one;
* ``has_api(thing, name)`` -- does a Blender type or instance have the property / function ``name``? A class-level ``hasattr`` says no
  to real functions (measured on 5.3.0); this looks in ``bl_rna`` and on instances;
* ``skip(reason)`` -- say that a check was skipped and why (run_all.py prints these lines in its summary: a skip never passes silently);
* ``install_fingerprint()`` / ``install_guard()`` -- no test may write under the game install: (path, size, mtime) of every file, compared
  before and after;
* ``FakeLayout`` -- a stand-in for a Blender ``UILayout`` that records what a panel draws (panels cannot be drawn in background mode);
* ``addon()`` / ``fresh()`` / ``start_project()`` -- the add-on registered once, an empty file, a project started from a stock donor.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

try:
    import fcntl
except ImportError:           # not Unix: the launch lock is a convenience of the Linux build machine; launches are then only spaced
    fcntl = None

HERE = os.path.dirname(os.path.abspath(__file__))        # blender_native/tests
ADDON = os.path.dirname(HERE)                            # blender_native
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(ADDON)
BLENDER = os.environ.get("BLENDER") or shutil.which("blender") or "blender"

LOCK_PATH = os.environ.get("BZ_BLENDER_LOCK") or os.path.join("/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir(), "bz_blender_launch.lock")
LOCK_SPACING = 1.0                                       # seconds between two Blender launches, any process


# ---------------------------------------------------------------------------------------------------- temp folders, results
_TMP_DIRS: list = []


def tmpdir(prefix: str = "so_") -> str:
    """A new, empty temp folder (removed at exit, or by ``cleanup()``; ``$BZ_KEEP_TMP`` keeps them for a look)."""
    base = os.environ.get("BZ_TMP") or None
    if base:
        os.makedirs(base, exist_ok=True)
    path = tempfile.mkdtemp(prefix=f"bz_{prefix}", dir=base)
    _TMP_DIRS.append(path)
    return path


def cleanup() -> None:
    if os.environ.get("BZ_KEEP_TMP"):
        return
    while _TMP_DIRS:
        shutil.rmtree(_TMP_DIRS.pop(), ignore_errors=True)


atexit.register(cleanup)


def done_file(name: str) -> str:
    """Where the test called ``name`` writes its verdict: named with the pid, never a fixed path."""
    return os.path.join(tempfile.gettempdir(), f"bz_so_{name}_{os.getpid()}_done.txt")


def skip(reason: str) -> None:
    """Say that a check was skipped and why. run_all.py reports a test that printed this as SKIP and lists the reason."""
    print(f"SO_SKIP: {reason}", flush=True)


def run_test(name: str, fn) -> None:
    """Run ``fn()``; write the done file with PASS or the traceback; remove the temp folders; exit 0 or 1 (the verdict run_all.py reads)."""
    try:
        fn()
    except BaseException:
        text = traceback.format_exc()
        print(text, flush=True)
        with open(done_file(name), "w") as f:
            f.write("ERROR\n" + text)
        cleanup()
        sys.exit(1)
    with open(done_file(name), "w") as f:
        f.write("PASS\n")
    cleanup()


# ---------------------------------------------------------------------------------------------------- the Blender launch lock
def _read_stamp(fd: int) -> float:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        return float(os.read(fd, 64).decode() or 0.0)
    except ValueError:
        return 0.0


@contextlib.contextmanager
def blender_lock(spacing: float = LOCK_SPACING, path: str | None = None, *, clock=time.time, sleep=time.sleep):
    """Hold the machine-wide launch lock while a Blender is started: wait for the other launchers, and until ``spacing`` seconds have
    passed since the last launch. The time of this launch is written as the lock is released. (``clock`` and ``sleep`` can be replaced,
    so tests count the waits instead of timing them; a wait never exceeds ``spacing``, even if the stamp is from another boot.)"""
    fd = os.open(path or LOCK_PATH, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        wait = min(spacing, max(0.0, _read_stamp(fd) + spacing - clock()))
        if wait > 0:
            sleep(wait)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, repr(clock()).encode())
    finally:
        os.close(fd)                      # closing the descriptor releases the lock


def run_locked(cmd, *, timeout: float | None = None, capture_output: bool = False, lock_path: str | None = None,
               spacing: float = LOCK_SPACING, **popen_kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` with the launch lock held while the process is started (not while it runs)."""
    if capture_output:
        popen_kwargs.setdefault("stdout", subprocess.PIPE)
        popen_kwargs.setdefault("stderr", subprocess.PIPE)
    with blender_lock(spacing, lock_path):
        proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def run_blender(script: str, *args: str, timeout: float | None = 600, env: dict | None = None, extra: tuple = ()) -> subprocess.CompletedProcess:
    """``blender --background --factory-startup --python-exit-code 1 --python <script> -- <args>``, launched under the lock."""
    cmd = [BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", *extra, "--python", script]
    if args:
        cmd += ["--", *args]
    environ = dict(os.environ if env is None else env, PYIC_REPO=REPO, PYTHONDONTWRITEBYTECODE="1", SDL_AUDIODRIVER="dummy")
    return run_locked(cmd, capture_output=True, text=True, timeout=timeout, env=environ, cwd=REPO)


# ---------------------------------------------------------------------------------------------------- API probing
def has_api(target, name: str) -> bool:
    """Whether the Blender type or instance ``target`` has a property or function called ``name``. A class-level ``hasattr`` reports
    real functions as missing (``hasattr(bpy.types.Mesh, "normals_split_custom_set_from_vertices")`` is False although it exists), so
    this asks the RNA description, and an instance directly."""
    rna = getattr(target, "bl_rna", None)
    if rna is not None and (name in rna.properties or name in rna.functions):
        return True
    return False if isinstance(target, type) else hasattr(target, name)


# ---------------------------------------------------------------------------------------------------- the install guard
def install_root():
    """The game install the tests must never write to: ``$IC_INSTALL``, else ICParse's usual Steam folders, else None."""
    env = os.environ.get("IC_INSTALL", "")
    if env and os.path.isdir(env):
        return env
    try:
        from ICParse.data import default_root
    except ImportError:
        return None
    return default_root()


def install_fingerprint(root: str | None = None) -> dict:
    """``{relative path: (size, mtime_ns)}`` of every file under the install (about 14 ms for the 5,462 files with a warm cache)."""
    root = root or install_root()
    out: dict = {}
    if not root:
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def fingerprint_diff(before: dict, after: dict) -> list:
    """One line per file that was added, removed or changed between two fingerprints."""
    out = [f"added: {p}" for p in sorted(set(after) - set(before))]
    out += [f"removed: {p}" for p in sorted(set(before) - set(after))]
    out += [f"changed: {p}" for p in sorted(p for p in set(before) & set(after) if before[p] != after[p])]
    return out


@contextlib.contextmanager
def install_guard(root: str | None = None):
    """Fail if anything under the game install was added, removed or changed inside the block."""
    root = root or install_root()
    if not root:
        skip("no game install found: the install guard has nothing to watch")
        yield
        return
    before = install_fingerprint(root)
    yield
    diff = fingerprint_diff(before, install_fingerprint(root))
    assert not diff, f"the game install changed under {root}:\n  " + "\n  ".join(diff[:10])


# ---------------------------------------------------------------------------------------------------- a UILayout stand-in
class FakeLayout:
    """Records what a panel draws. ``calls`` is shared by every sub-layout, in drawing order: ``(kind, details)``."""

    def __init__(self, calls: list | None = None, alert: bool = False):
        self.calls = calls if calls is not None else []
        self.alert = alert
        self.use_property_split = False
        self.use_property_decorate = True

    def _sub(self, kind, **kw):
        self.calls.append((kind, kw))
        return FakeLayout(self.calls, alert=self.alert)

    def box(self, **kw):
        return self._sub("box", **kw)

    def column(self, **kw):
        return self._sub("column", **kw)

    def row(self, **kw):
        return self._sub("row", **kw)

    def split(self, **kw):
        return self._sub("split", **kw)

    def separator(self, **kw):
        self.calls.append(("separator", kw))

    def label(self, text="", icon="NONE", icon_value=0, **kw):
        self.calls.append(("label", dict(text=text, icon=icon, icon_value=icon_value, alert=self.alert, **kw)))

    def prop(self, data, name, **kw):
        self.calls.append(("prop", dict(name=name, **kw)))

    def operator(self, idname, text="", icon="NONE", **kw):
        self.calls.append(("operator", dict(idname=idname, text=text, icon=icon, alert=self.alert, **kw)))
        return type("Props", (), {})()

    def template_list(self, list_type, list_id, data, prop, active_data, active_prop, **kw):
        self.calls.append(("template_list", dict(list_type=list_type, list_id=list_id, prop=prop, **kw)))

    def __getattr__(self, name):                 # any other layout call (operator_menu_enum, prop_search, ...) is recorded and returns a sub-layout
        if name.startswith("_"):
            raise AttributeError(name)

        def call(*args, **kw):
            self.calls.append((name, dict(kw, args=args)))
            return FakeLayout(self.calls, alert=self.alert)
        return call

    # what the drawing so far contains
    def texts(self) -> list:
        return [d["text"] for k, d in self.calls if k == "label"]

    def operators(self) -> list:
        return [d["idname"] for k, d in self.calls if k == "operator"]

    def labels(self) -> list:
        return [d for k, d in self.calls if k == "label"]


# ---------------------------------------------------------------------------------------------------- pure import
def import_pure():
    """The ``sceneobj`` package imported WITHOUT bpy and without running ``blender_native/__init__.py`` (a stand-in parent package whose
    path is the add-on folder), for the pure tests that run in the venv python. Only modules that declare ``PURE = True`` are loaded."""
    import importlib
    import types
    if sys.modules.get("bpy") is not None:
        raise RuntimeError("import_pure() is for the venv python; inside Blender import blender_native.sceneobj normally")
    sys.modules["bpy"] = None                           # an import of bpy now fails, as in any interpreter that is not Blender
    if "blender_native" not in sys.modules:
        stub = types.ModuleType("blender_native")
        stub.__path__ = [ADDON]
        stub.__package__ = "blender_native"
        sys.modules["blender_native"] = stub
    return importlib.import_module("blender_native.sceneobj")


# ---------------------------------------------------------------------------------------------------- in-Blender helpers
def addon():
    """The ``blender_native`` package, imported from the repo and registered (once per Blender process)."""
    import bpy
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import blender_native
    if bpy.types.Panel.bl_rna_get_subclass_py("BZ_PT_panel") is None:
        blender_native.register()
    return blender_native


def fresh() -> None:
    """An empty Blender file."""
    import bpy
    bpy.ops.wm.read_homefile(use_empty=True)


def start_project(kind: str, donor: str, name: str | None = None, workdir: str | None = None):
    """File > New > Impossible Creatures Project from a stock donor: ``kind`` is CHARACTER or STRUCTURE, ``donor`` an archive member such
    as ``art/ebps/gatherers/henchmen.sgm``. Returns ``(the active object, the project's .sgm path)``."""
    import bpy
    workdir = workdir or tmpdir("project_")
    path = os.path.join(workdir, f"{name or os.path.basename(donor)[:-4]}.sgm")
    res = bpy.ops.bz.new_project(filepath=path, project_type=kind, donor=donor, anim_source="NONE")
    assert "FINISHED" in res, res
    return bpy.context.view_layer.objects.active, path
