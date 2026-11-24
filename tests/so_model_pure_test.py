"""sceneobj's bpy-free core (slice B00): the words every slice speaks, and the helpers every test uses. No Blender.

    .venv/bin/python3 blender_native/tests/so_model_pure_test.py

1. the package imports with bpy blocked, loads only modules that declare PURE = True, and starts clean;
2. Issue / issue(): a plain call returns an ICParse Finding (or the stand-in with the same fields), the level is checked;
3. SourceBundle / OutBundle / ExportReport: sha1 stamps, a .lua with bytes that are not UTF-8 survives, OutBundle.shared accepts only
   declared keys (item set, update, setdefault, |=), declare_shared, lower-case archive paths;
4. the Slice protocol and the registry: BaseSlice fits, each kind of mistake is named, a shared export order / stage number / claim /
   slice name is reported;
5. refs.rename reaches every slice in order with that slice's own state, and a slice that cannot follow is reported, not dropped;
6. claim_edit / declare_guard_code / forget_claims; foreach_get / foreach_set dtypes; pick_game_dir;
7. tests/_so_common.py: tmpdir, has_api, blender_lock (waits counted with a fake clock; one launcher at a time), run_locked,
   the install fingerprint and guard, FakeLayout; tests/so_suite.py.
"""
import contextlib
import io
import os
import subprocess
import sys
import threading

sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import numpy as np  # noqa: E402

so = C.import_pure()
model = so.model
SCENE = os.path.join(C.REPO, "Assets", "engine_scene")


def section(title):
    print(f"-- {title}", flush=True)


# ------------------------------------------------------------------------------------------------------------------ 1. pure import
def test_pure_import():
    section("1. the package imports without bpy")
    assert sys.modules.get("bpy") is None, "bpy must stay blocked in the pure test"
    st = so.status()
    assert st["pure"] is True and st["errors"] == [] and st["problems"] == [], st
    assert so.CLASSES == () and so.registered_classes() == (), "no Blender classes without Blender"
    names = [m.__name__.rsplit(".", 1)[-1] for m in so.MODULES]
    assert names[0] == "model", names
    for n in names:                                      # every loaded module promised it needs no Blender
        assert so.declares_pure(n), f"{n} was loaded without bpy but does not declare PURE = True"
    assert not so.declares_pure("ui_kit") and not so.declares_pure("prefs"), "the Blender modules must not claim PURE"
    assert "ui_kit" not in names and "prefs" not in names, names
    assert set(so.MISSING_CORE) <= {"subtype", "entity", "importer", "exporter", "compat"}, so.MISSING_CORE
    try:
        so.register()
    except RuntimeError as e:
        assert "needs Blender" in str(e)
    else:
        raise AssertionError("register() must refuse to run without Blender")
    assert so.SLICE_API == 1 and so.refs is model.refs and so.claim_edit is model.claim_edit      # the names C21 lists, via the package
    try:
        so.not_a_name
    except AttributeError:
        pass
    else:
        raise AssertionError("an unknown package attribute must raise AttributeError")


# ------------------------------------------------------------------------------------------------------------------ 2. issues
def test_issue():
    section("2. Issue / issue()")
    i = model.issue("skeleton_edit", "WARN", "3 bone edits are not exported yet", "rig")
    assert (i.code, i.level, i.text, i.where) == ("skeleton_edit", "WARN", "3 bone edits are not exported yet", "rig")
    assert (i.id, i.severity) == ("skeleton_edit", "WARN")
    assert isinstance(i, model.Issue) and not isinstance(object(), model.Issue)
    j = model.Issue("x", "ERROR", "boom")                    # calling Issue builds a Finding, the same thing
    assert type(j) is type(i) and j.where == ""
    for bad in ("error", "ERRROR", "", None):
        try:
            model.issue("x", bad, "t")
        except ValueError as e:
            assert "ERROR, WARN, INFO" in str(e)
        else:
            raise AssertionError(f"level {bad!r} must be refused")
    real = model.finding_class()
    if real is model.FallbackFinding:
        C.skip("ICParse.entity.findings (slice I00) has not landed: Issue returns the stand-in Finding with the same fields")
    else:
        assert type(i) is real and real.__module__ == "ICParse.entity.findings" and real.__name__ == "Finding", real
        assert i.to_str() == "WARN: [skeleton_edit] 3 bone edits are not exported yet", i.to_str()
    fb = model.FallbackFinding("a", "ERROR", "w", "t")        # the stand-in has the same shape whether or not the real one exists
    assert (fb.code, fb.level, fb.to_str()) == ("a", "ERROR", "ERROR: [a] t")
    assert isinstance(fb, model.Issue)
    mixed = [model.issue("a", "ERROR", "t"), model.issue("b", "WARN", "t"), model.issue("c", "WARN", "t"), model.issue("d", "INFO", "t")]
    assert [x.code for x in model.blocking(mixed)] == ["a"]
    assert model.counts(mixed) == {"ERROR": 1, "WARN": 2, "INFO": 1}


# ------------------------------------------------------------------------------------------------------------------ 3. bundles
def test_bundles():
    section("3. SourceBundle / OutBundle / ExportReport")
    src = model.SourceBundle(sgm=b"abc", ebp=b"\x01\x02", lua="x = 1\r\n")
    h = src.sha1()
    assert h == {"sgm": model.sha1_hex(b"abc"), "ebp": model.sha1_hex(b"\x01\x02"), "lua": model.sha1_hex(b"x = 1\r\n")}, h
    assert model.SourceBundle(sgm=b"").sha1()["ebp"] is None and model.SourceBundle(sgm=b"").paths == {}

    # from_files: a real scene .sgm and a .lua that is not valid UTF-8 and uses CRLF
    sgm_path = os.path.join(SCENE, "henchmen.sgm")
    if os.path.isfile(sgm_path):
        d = C.tmpdir("bundle_")
        lua_bytes = b"-- caf\xe9 \r\nx = 1\r\n"
        lua_path = os.path.join(d, "x.lua")
        with open(lua_path, "wb") as f:
            f.write(lua_bytes)
        b = model.SourceBundle.from_files(sgm_path, None, lua_path)
        assert b.sgm == open(sgm_path, "rb").read() and b.ebp is None and b.scene is not None
        assert b.lua.encode("utf-8", "surrogateescape") == lua_bytes, "the .lua text must give its exact bytes back"
        assert b.paths == {"sgm": sgm_path, "lua": lua_path} and b.sha1()["lua"] == model.sha1_hex(lua_bytes)
    else:
        C.skip(f"{sgm_path} not found (run blender_native/tools/extract_stock.py --scene): SourceBundle.from_files not exercised")

    out = model.OutBundle.from_source(src)
    assert (out.sgm, out.ebp, out.lua) == (b"abc", b"\x01\x02", "x = 1\r\n") and out.files == {} and out.notes == [] and out.issues == []
    assert type(out.shared) is model.SharedBag and out.shared == {}

    # the hand-over bag accepts only declared keys, however it is written to
    assert model.SHARED_KEYS >= model.BASE_SHARED_KEYS == {"skeleton", "materials", "textures", "geometry", "morphs", "markers", "bounds", "clips"}
    out.shared["geometry"] = {"rebuilt": []}
    out.shared.update({"skeleton": {"structural": False}}, markers={})
    assert out.shared.setdefault("bounds", {}) == {} and set(out.shared) == {"geometry", "skeleton", "markers", "bounds"}
    out.shared |= {"clips": {}}
    for write in (lambda: out.shared.__setitem__("geometery", 1), lambda: out.shared.update(typo=1),
                  lambda: out.shared.setdefault("typo", 1), lambda: out.shared.__ior__({"typo": 1}),
                  lambda: model.OutBundle(sgm=b"", shared={"typo": 1})):
        try:
            write()
        except KeyError as e:
            assert "declared hand-over key" in str(e) and "geometry" in str(e)
        else:
            raise AssertionError("an undeclared hand-over key must be refused")
    assert type(out.shared.copy()) is model.SharedBag
    assert type(model.OutBundle(sgm=b"", shared={"geometry": 1}).shared) is model.SharedBag
    plain = model.OutBundle(sgm=b"")
    dict.__setitem__(plain.shared, "sneaky", 1)              # filled behind the bag's back: the backstop names it
    assert [f.code for f in plain.check_shared()] == ["shared_key"]

    model.declare_shared("controller_state", "controller_meta")
    try:
        model.declare_shared("controller_state", "controller_meta")        # again, same owner: a reload, fine
        out.shared["controller_state"] = {"x": 1}
        try:
            model.declare_shared("controller_state", "controller_events")
        except ValueError as e:
            assert "already belongs" in str(e)
        else:
            raise AssertionError("one hand-over key must not have two owners")
        try:
            model.declare_shared("geometry", "morphs")
        except ValueError:
            pass
        else:
            raise AssertionError("a declared base key keeps its owner")
    finally:
        model.SHARED_KEYS.discard("controller_state")
        model.SHARED_OWNERS.pop("controller_state", None)
    assert "controller_state" not in model.SHARED_KEYS

    # extra files: the archive lookup is case-sensitive, so paths are lower case
    assert out.add_file("art/textures/lab_01.txr", bytearray(b"t")) == "art/textures/lab_01.txr" and out.files["art/textures/lab_01.txr"] == b"t"
    assert out.add_file("art\\textures\\b.txr", b"t") == "art/textures/b.txr"
    for bad in ("Art/Textures/X.txr", "/art/x.txr", "../x.txr", "art//x.txr", "c:/x.txr", ""):
        try:
            out.add_file(bad, b"")
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} is not a usable archive path")
    out.files["Art/X.txr"] = b""
    assert [f.code for f in out.check_files()] == ["file_path"] and out.check_files()[0].where == "Art/X.txr"

    rep = model.ExportReport()
    assert rep.ok and rep.out is None and rep.written == {} and rep.build_dir is None
    rep.issues.append(model.issue("w", "WARN", "t"))
    assert rep.ok
    rep.issues.append(model.issue("e", "ERROR", "t"))
    assert not rep.ok
    assert not model.ExportReport(refused=True).ok


# ------------------------------------------------------------------------------------------------------------------ 4. slices
class Good(model.BaseSlice):
    def __init__(self, name, order, claims=()):
        self.name, self.order, self.claims = name, order, tuple(claims)


def test_slices():
    section("4. the Slice protocol and the registry")
    model.clear_slices()
    g = Good("markers", 50, ("EMPTY:bz_marker",))
    assert isinstance(g, model.Slice) and model.slice_problems(g) == []
    assert g.read(None) is None and g.changed(None, None) is False and g.check(None, None, None) == [] and g.on_rename("bone", "a", "b", None) is None

    class Missing:
        name, order, claims = "x", 1, ()
        def read(self, src): ...
    probs = model.slice_problems(Missing())
    assert probs == [f"has no {m!r}" for m in ("build", "gather", "changed", "apply", "check", "on_rename")], probs

    bad = Good("Markers", 50)                                 # not lower case
    assert any("lower-case" in p for p in model.slice_problems(bad))
    for order in (True, "50", 50.0, None):
        b = Good("x", 50)
        b.order = order
        assert any("integer" in p for p in model.slice_problems(b)), order
    b = Good("x", 50)
    b.claims = "EMPTY:bz_marker"
    assert any("tuple of strings" in p for p in model.slice_problems(b))

    class WrongArity(Good):
        def read(self):
            return None
        def apply(self, out, src, data):
            return None
    probs = model.slice_problems(WrongArity("x", 1))
    assert any(p.startswith("read cannot be called with the 1 argument") for p in probs), probs
    assert any(p.startswith("apply cannot be called with the 4 argument") for p in probs), probs

    class NotCallable(Good):
        read = 5
    assert any("read is not callable" in p for p in model.slice_problems(NotCallable("x", 1)))

    # the registry: order, one name, one claim per kind, stage numbers
    for s in (Good("geometry", 30, ("MESH:bz_part",)), Good("skeleton", 20, ("BONE",)), Good("zz_extra", 777)):
        assert model.register_slice(s, owner="mod_" + s.name) == []
    assert [s.name for s in model.slices()] == ["skeleton", "geometry", "zz_extra"] and model.get_slice("geometry").order == 30
    assert model.registry_problems() == [], model.registry_problems()

    model.register_slice(Good("morphs", 30, ("MESH:bz_part",)), owner="mod_morphs")        # shares geometry's order AND claim
    probs = model.registry_problems()
    assert any("share the export order 30" in p and "geometry" in p and "morphs" in p for p in probs), probs
    assert any("both claim 'MESH:bz_part'" in p for p in probs), probs
    assert any("slice 'morphs' has order 30 but the 'morphs' stage runs at 40" in p for p in probs), probs
    model.unregister_slice("morphs")
    assert model.registry_problems() == []

    model.register_slice(Good("geometry", 30, ("MESH:bz_part",)), owner="mod_geometry")    # the same module again: a reload, fine
    assert model.registry_problems() == []
    model.register_slice(Good("geometry", 30), owner="another_module")                     # a second module with the same name
    assert any("'geometry' is registered by both mod_geometry and another_module" in p for p in model.registry_problems())

    model.clear_slices()
    assert model.slices() == [] and model.registry_problems() == []
    assert model.register_slice(Missing(), owner="m") != [] and [s.name for s in model.slices()] == ["x"]
    assert any("has no 'build'" in p for p in model.registry_problems())
    model.clear_slices()
    assert model.STAGE_ORDER == {"materials": 10, "textures": 12, "skeleton": 20, "geometry": 30, "morphs": 40, "markers": 50, "bounds": 60,
                                 "anim": 70, "controller_meta": 80, "controller_motion": 81, "controller_events": 82, "files": 90, "validate": 100}


# ------------------------------------------------------------------------------------------------------------------ 5. refs bus
class Recorder(Good):
    def __init__(self, name, order, log, boom=False):
        super().__init__(name, order)
        self.log, self.boom = log, boom

    def on_rename(self, kind, old, new, state):
        self.log.append((self.name, kind, old, new, state))
        if self.boom:
            raise RuntimeError("no such marker")


def test_refs():
    section("5. refs.rename")
    model.clear_slices()
    log = []
    model.register_slice(Recorder("markers", 50, log), owner="m1")
    model.register_slice(Recorder("geometry", 30, log), owner="m2")
    assert model.refs.rename("bone", "bip01 spine1", "spine_1") == []
    assert log == [("geometry", "bone", "bip01 spine1", "spine_1", None), ("markers", "bone", "bip01 spine1", "spine_1", None)], log   # export order
    del log[:]
    assert model.refs.rename("clip", "walk", "run", states={"markers": {"a": 1}}) == []
    assert log == [("geometry", "clip", "walk", "run", None), ("markers", "clip", "walk", "run", {"a": 1})], log       # each gets its own state
    model.register_slice(Recorder("skeleton", 20, log, boom=True), owner="m3")
    del log[:]
    probs = model.refs.rename("marker", "Point_L", "Point_Left")
    assert len(probs) == 1 and probs[0].level == "ERROR" and probs[0].code == "rename_followup" and probs[0].where == "skeleton", probs
    assert "skeleton could not follow" in probs[0].text and "no such marker" in probs[0].text and "Undo" in probs[0].text
    assert [e[0] for e in log] == ["skeleton", "geometry", "markers"], "a slice that fails must not stop the others"
    assert model.refs.log[-1] == ("marker", "Point_L", "Point_Left", ["geometry", "markers"])
    for kind, old, new in (("texture", "a", "b"), ("bone", "", "b"), ("bone", "a", "")):
        try:
            model.refs.rename(kind, old, new)
        except ValueError:
            pass
        else:
            raise AssertionError((kind, old, new))
    assert model.REF_KINDS == ("bone", "clip", "marker", "material", "part")
    model.clear_slices()


# ------------------------------------------------------------------------------------------------------------------ 6. claims, helpers
def test_claims_and_helpers():
    section("6. claim_edit, foreach_get / foreach_set, pick_game_dir")
    me = __name__
    model.forget_claims(me)
    model.claim_edit("skeleton_edit")
    assert model.is_claimed("skeleton_edit") and model.claimed_by("skeleton_edit") == me and model.claimed_codes() == {"skeleton_edit": me}
    assert not model.is_claimed("shape_key") and model.registry_problems() == []
    model.claim_edit("skeleton_edit")                          # the same module twice (a reload): fine
    model.claim_edit("made_up_code", owner="some_module")
    probs = model.registry_problems()
    assert probs == ["claim_edit('made_up_code') by some_module: no such guard code"], probs
    model.declare_guard_code("made_up_code", "guards_scene")   # the guard module declares it: now it is a code
    assert model.registry_problems() == [] and "made_up_code" in model.known_guard_codes()
    model.claim_edit("shape_key", owner="slice_morphs")
    model.claim_edit("shape_key", owner="slice_other")
    probs = model.registry_problems()
    assert len(probs) == 1 and "'shape_key' is claimed by two modules (slice_morphs and slice_other)" in probs[0], probs
    model.forget_claims("slice_other")
    assert model.registry_problems() == []
    for owner in (me, "some_module", "slice_morphs", "slice_other"):
        model.forget_claims(owner)
    model._EXTRA_GUARD_CODES.pop("made_up_code", None)
    assert model.claimed_codes() == {} and model.registry_problems() == []
    assert len(model.GUARD_CODES) == 14 and "bounds_stale" in model.GUARD_CODES and len(set(model.GUARD_CODES)) == 14

    class Coll:                                                # what Blender's bpy_prop_collection.foreach_get / foreach_set do
        def __init__(self, n, per):
            self.n, self.per, self.store, self.dtypes = n, per, np.arange(n * per, dtype=np.float64) * 0.5, []

        def __len__(self):
            return self.n

        def foreach_get(self, attr, arr):
            self.dtypes.append(arr.dtype)
            arr[:] = self.store

        def foreach_set(self, attr, arr):
            assert arr.flags["C_CONTIGUOUS"] and arr.ndim == 1
            self.dtypes.append(arr.dtype)
            self.store = np.array(arr, dtype=np.float64)

    c = Coll(4, 3)
    a = model.foreach_get(c, "co", np.float32, 3)
    assert a.shape == (4, 3) and a.dtype == np.float32 and c.dtypes == [np.dtype(np.float32)] and np.allclose(a.ravel(), c.store)
    b = model.foreach_get(Coll(5, 1), "x", np.int64)
    assert b.shape == (5,) and b.dtype == np.int64
    model.foreach_set(c, "co", np.arange(12).reshape(4, 3)[:, ::-1], np.float32)       # a non-contiguous view is made contiguous first
    assert c.dtypes[-1] == np.dtype(np.float32) and c.store.tolist() == [2, 1, 0, 5, 4, 3, 8, 7, 6, 11, 10, 9]

    d = C.tmpdir("game_")
    other = C.tmpdir("game2_")
    nothing = lambda: None  # noqa: E731
    assert model.pick_game_dir(d, environ={"IC_INSTALL": other}, default_root=nothing) == (d, "preferences")
    assert model.pick_game_dir(os.path.join(d, "missing"), environ={"IC_INSTALL": other}, default_root=nothing) == (other, "IC_INSTALL")
    assert model.pick_game_dir("", environ={"IC_INSTALL": os.path.join(d, "missing")}, default_root=lambda: d) == (d, "steam default")
    assert model.pick_game_dir("  ", environ={}, default_root=nothing) == (None, "none")
    assert model.pick_game_dir("~", environ={}, default_root=nothing) == (os.path.expanduser("~"), "preferences")
    folder, source = model.pick_game_dir("", environ=os.environ)         # the real one: this machine's $IC_INSTALL or Steam folders
    assert (folder is None) == (source == "none") and source in ("IC_INSTALL", "steam default", "none")


# ------------------------------------------------------------------------------------------------------------------ 7. test helpers
def test_common():
    section("7. tests/_so_common.py and tests/so_suite.py")
    a, b = C.tmpdir("x_"), C.tmpdir("x_")
    assert a != b and os.path.isdir(a) and os.path.isdir(b) and os.listdir(a) == [] and os.path.basename(a).startswith("bz_x_")
    assert str(os.getpid()) in C.done_file("t") and C.done_file("t").endswith("_done.txt") and "bz_so_t_" in C.done_file("t")

    # has_api: the RNA description, not a class-level hasattr
    class Rna:
        properties = {"vertices": 1}
        functions = {"normals_split_custom_set_from_vertices": 1}
    class Mesh:
        bl_rna = Rna()
    class Inst(Mesh):
        extra = 1
    assert C.has_api(Mesh, "vertices") and C.has_api(Mesh, "normals_split_custom_set_from_vertices") and not C.has_api(Mesh, "nothing")
    assert C.has_api(Inst(), "extra") and not C.has_api(Inst, "extra") and not C.has_api(Inst(), "nothing") and not C.has_api(object(), "x")

    # the launch lock: the waits requested of a fake clock (nothing here is timed)
    lock = os.path.join(C.tmpdir("lock_"), "launch.lock")
    sleeps, now = [], [1000.0]

    def clock():
        return now[0]

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):
        pass
    assert sleeps == [], "the first launch does not wait"
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):                  # right after the last one: wait the full spacing
        pass
    assert sleeps == [1.0], sleeps
    now[0] += 0.4
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):                  # 0.4 s after the last: wait the rest
        pass
    assert len(sleeps) == 2 and abs(sleeps[1] - 0.6) < 1e-9, sleeps
    now[0] += 5.0
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):                  # long ago: no wait
        pass
    assert len(sleeps) == 2, sleeps
    open(lock, "w").write("9999999999.0")                                      # a stamp from the future (another boot): the wait is capped
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):
        pass
    assert sleeps[-1] == 1.0 and len(sleeps) == 3, sleeps
    open(lock, "w").write("not a number")                                      # a garbled stamp counts as long ago
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):
        pass
    assert len(sleeps) == 3, sleeps
    try:
        with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):
            raise ValueError("inside")
    except ValueError:
        pass
    with C.blender_lock(1.0, lock, clock=clock, sleep=sleep):                  # an error inside still released the lock
        pass

    # one launcher at a time: B tries while A holds the lock; the order of events says B waited
    events, a_in, b_try = [], threading.Event(), threading.Event()

    def launcher_a():
        with C.blender_lock(0.0, lock):
            events.append("A in")
            a_in.set()
            b_try.wait(5)                                                      # B has now asked for the lock (and is blocked)
            events.append("A out")

    def launcher_b():
        a_in.wait(5)
        b_try.set()
        with C.blender_lock(0.0, lock):
            events.append("B in")

    ta, tb = threading.Thread(target=launcher_a), threading.Thread(target=launcher_b)
    ta.start(); tb.start(); ta.join(10); tb.join(10)
    assert events == ["A in", "A out", "B in"], events

    # run_locked: output captured, a timeout kills the process and keeps what it printed
    r = C.run_locked([sys.executable, "-c", "print('hello'); import sys; sys.stderr.write('oops'); sys.exit(3)"], capture_output=True, text=True,
                     lock_path=lock, spacing=0.0)
    assert (r.returncode, r.stdout.strip(), r.stderr.strip()) == (3, "hello", "oops"), r
    try:
        C.run_locked([sys.executable, "-c", "print('before', flush=True); import time; time.sleep(60)"], capture_output=True, text=True, timeout=1.0,
                     lock_path=lock, spacing=0.0)
    except subprocess.TimeoutExpired as e:
        assert "before" in (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")), e.stdout
    else:
        raise AssertionError("a process past its timeout must raise TimeoutExpired")

    # the install guard
    root = C.tmpdir("install_")
    os.makedirs(os.path.join(root, "Data", "art"))
    for rel in ("IC.exe", "Data/art/a.txt"):
        open(os.path.join(root, rel), "w").write("x")
    fp = C.install_fingerprint(root)
    assert sorted(fp) == [os.path.join("Data", "art", "a.txt"), "IC.exe"] and fp == C.install_fingerprint(root)
    with C.install_guard(root):
        pass
    for change, expect in ((lambda: open(os.path.join(root, "new.txt"), "w").write("n"), "added: new.txt"),
                           (lambda: open(os.path.join(root, "IC.exe"), "w").write("longer"), "changed: IC.exe"),
                           (lambda: os.remove(os.path.join(root, "Data", "art", "a.txt")), "removed: " + os.path.join("Data", "art", "a.txt"))):
        try:
            with C.install_guard(root):
                change()
        except AssertionError as e:
            assert expect in str(e), (expect, str(e))
        else:
            raise AssertionError(f"the guard missed: {expect}")
    assert C.fingerprint_diff({"a": (1, 1)}, {"a": (1, 2), "b": (1, 1)}) == ["added: b", "changed: a"]

    # FakeLayout records what a panel draws
    lay = C.FakeLayout()
    box = lay.box()
    box.label(text="hello", icon="INFO")
    col = lay.column(align=True)
    col.alert = True
    col.label(text="bad")
    props = lay.operator("bz.export_scene", icon="EXPORT")
    props.thing = 1
    lay.template_list("BZ_UL_rows", "parts", None, "rows", None, "active")
    assert lay.texts() == ["hello", "bad"] and lay.operators() == ["bz.export_scene"] and lay.labels()[1]["alert"] is True
    assert [k for k, _ in lay.calls] == ["box", "label", "column", "label", "operator", "template_list"]

    # so_suite: found by name, split by the _pure_ marker
    import so_suite
    assert so_suite.is_pure("so_model_pure_test.py") and not so_suite.is_pure("so_registry_test.py")
    assert "so_model_pure_test.py" in so_suite.PURE and "so_registry_test.py" in so_suite.IN_BLENDER and "so_contract_test.py" in so_suite.IN_BLENDER
    assert not set(so_suite.PURE) & set(so_suite.IN_BLENDER) and all(n.startswith("so_") and n.endswith("_test.py") for n in so_suite.names())

    # run_all.py: a test that printed SO_SKIP and exited 0 is SKIP (listed with its reason, not a failure); an exit code is FAIL
    import run_all
    d = C.tmpdir("runall_")
    scripts = {"ok_x.py": "print('fine')\n", "skp_x.py": "print('SO_SKIP: the entity layer has not landed')\n", "bad_x.py": "import sys; sys.exit(2)\n"}
    paths = {}
    for name, text in scripts.items():
        paths[name] = os.path.join(d, name)
        with open(paths[name], "w") as f:
            f.write(text)
    run_all.PURE.extend(paths.values())
    try:
        assert run_all.run(paths["ok_x.py"], 30)[0] == "PASS" and run_all.run(paths["skp_x.py"], 30)[0] == "SKIP" and run_all.run(paths["bad_x.py"], 30)[0] == "FAIL(2)"
        saved_argv = sys.argv

        def main_of(key):
            sys.argv = ["run_all.py", "-k", key]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = run_all.main()
            finally:
                sys.argv = saved_argv
            return code, buf.getvalue()

        code, text = main_of("skp_x.py")
        assert code == 0 and "SKIPPED" in text and "the entity layer has not landed" in text and "1/1 passed (1 with skipped checks)" in text, text
        code, text = main_of("bad_x.py")
        assert code == 1 and "0/1 passed" in text and "FAIL(2)" in text, text
        code, text = main_of("ok_x.py")
        assert code == 0 and "1/1 passed\n" in text and "SKIPPED" not in text, text
    finally:
        for p in paths.values():
            run_all.PURE.remove(p)
    assert os.path.basename(run_all.so_suite.__file__) == "so_suite.py" and run_all._so_common is C
    assert sys.modules.get("bpy") is None, "nothing in this test may have imported bpy"


def main():
    test_pure_import()
    test_issue()
    test_bundles()
    test_slices()
    test_refs()
    test_claims_and_helpers()
    test_common()
    print("SO_MODEL_PURE_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("model_pure", main)
