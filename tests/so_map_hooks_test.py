"""The extension points of the map tools (sceneobj/map_hooks.py), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_hooks_test.py     (or: run_all.py -k so_map_hooks)

1. the add-on loads map_hooks by itself (any map_*.py module is discovered), and the Map panel id it names is the real panel's;
2. steps run in order (lower order first, then registration order), registering a name twice replaces it, unregister removes it;
3. an import step sees the whole import context (collection, the source bytes, the parsed view and board, the options, the terrain,
   the kinds, the game folder); one that raises is reported and skipped, the import still finishes;
4. checks: problems come back with their check's name, errors first; a check that raises becomes a warning;
5. a save with no steps -- and with a step that touches nothing -- is byte for byte the original, with the files beside it unchanged;
6. a save step can replace the map's bytes, replace a file beside the map, add a file, drop a file, add lines to the report; the
   map file's bytes written are the replaced ones; the ready-to-drop README carries the lines;
7. refuse(reason), a step that raises and a file name that escapes the folder all stop the save with nothing written;
8. import_map_bytes opens a map from memory, saves it again byte for byte, and a save step sees source "" there;
nothing under the game folder changed.
"""
import json
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)

FACEOFF = "Engine.sga|scenarios/mp/2p_faceoff.sgb"


def section(title):
    print(f"-- {title}", flush=True)


def expect_error(words, call):
    try:
        res = call()
    except RuntimeError as exc:
        assert words in str(exc), exc
        return str(exc)
    raise AssertionError(f"no error reported (result {res})")


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def import_faceoff():
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=FACEOFF, load_models=False)
    assert "FINISHED" in res, res
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def folder_files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/") for d, _s, fs in os.walk(folder) for f in fs)


def test_loading():
    import blender_native
    from blender_native import sceneobj
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import panels_map as P
    names = [m.__name__.rsplit(".", 1)[-1] for m in sceneobj.MODULES]
    assert "map_hooks" in names and "map_look" in names, names
    assert not sceneobj.ERRORS, sceneobj.ERRORS
    assert names.index("map_hooks") < names.index("ops_map_import") and "map_" in sceneobj.PREFIXES
    assert H.MAP_PANEL_ID == P.BZ_PT_map.bl_idname == "BZ_PT_map"
    reg = H.registered()                                  # the map features (look, settings, paint, script, ...) register their own steps
    assert "paint passability" in reg["save"] and "script" in reg["save"], reg


def test_registry():
    from blender_native.sceneobj import map_hooks as H
    state = H.snapshot()
    H.restore(([], [], []))                               # the sections below test the registry on its own: start empty
    try:
        seen = []
        H.register_import_step("c", lambda i: seen.append("c"))
        H.register_import_step("a", lambda i: seen.append("a"), order=10)
        H.register_import_step("b", lambda i: seen.append("b"))
        H.register_import_step("c", lambda i: seen.append("c2"))          # replaces the first c, which keeps no place
        H.register_save_step("s1", lambda s: None, order=200)
        H.register_save_step("s2", lambda s: None, order=5)
        H.register_check("k", lambda c: [])
        reg = H.registered()
        assert reg == {"import": ["a", "b", "c"], "save": ["s2", "s1"], "checks": ["k"]}, reg
        H.unregister_step("b")
        assert H.registered()["import"] == ["a", "c"]
        H.restore(([], [], []))
        assert H.registered() == {"import": [], "save": [], "checks": []}
    finally:
        H.restore(state)


def test_import_step_and_checks(game_dir):
    from blender_native.sceneobj import map_hooks as H
    gm = game_map(game_dir, FACEOFF)
    raw = gm.read()
    state = H.snapshot()
    got = {}
    try:
        def step(ictx):
            got["ictx"] = ictx
            got["fields"] = {k: getattr(ictx, k) for k in ("context", "top", "name", "source_bytes", "view", "board", "options",
                                                            "terrain", "kinds", "game_dir", "data", "report", "tmp")}
            got["tmp_exists"] = os.path.isdir(ictx.tmp)
            ictx.warn("a note from a step")
            ictx.report["my_feature"] = {"x": 1}

        def broken(ictx):
            raise ValueError("on purpose")
        H.register_import_step("dummy", step)
        H.register_import_step("broken", broken, order=200)
        top = import_faceoff()
        f = got["fields"]
        assert f["top"] == top and f["top"] == f["context"].scene.collection.children[0] and f["name"] == "2p_faceoff"
        assert f["source_bytes"] == raw, "the step must get the map file's bytes as they were read"
        assert f["view"].board is f["board"] and f["view"].width > 0
        assert f["options"] == {"load_models": False, "show_water": True, "show_creatures": True, "game_look": True}, f["options"]
        assert f["terrain"].get("ic_map") and f["terrain"].name.endswith("terrain") and "ic_map_width" in f["terrain"]
        assert set(f["kinds"]) == {"terrain", "water", "structures", "nature", "units", "other"}
        assert f["game_dir"] and os.path.isdir(f["game_dir"]) and f["data"] is not None and got["tmp_exists"]
        rep = json.loads(top["ic_map_report"])
        assert rep["my_feature"] == {"x": 1} and "a note from a step" in rep["problems"], rep["problems"]
        assert any("'broken'" in p and "on purpose" in p for p in rep["problems"]), rep["problems"]
        assert rep["collection"] == top.name
        print(f"   the import step saw {len(raw)} source bytes and every context field; a broken step is a note, not a failure", flush=True)

        # checks
        H.register_check("two", lambda c: [H.problem("warning", "a warning", "somewhere"), H.problem("error", "an error", c.terrain.name)])
        H.register_check("none", lambda c: [])
        H.register_check("crash", lambda c: 1 / 0)
        out = H.run_checks(bpy.context, top)
        assert [p["severity"] for p in out] == ["error", "warning", "warning"], out
        assert out[0] == {"severity": "error", "text": "an error", "where": f["terrain"].name, "check": "two"}, out[0]
        assert out[2]["check"] == "crash" and "could not run" in out[2]["text"]
        try:
            H.problem("fatal", "x")
        except ValueError:
            pass
        else:
            raise AssertionError("an unknown severity must be refused")
    finally:
        H.restore(state)


def test_saves(game_dir):
    from blender_native.sceneobj import map_hooks as H
    gm = game_map(game_dir, FACEOFF)
    raw = gm.read()
    import ICParse.sga as sga
    arc = sga.read(gm.path)
    top = import_faceoff()
    state = H.snapshot()
    saw = {}

    def save(folder, **kw):
        path = os.path.join(folder, "2p_faceoff.sgb")
        return bpy.ops.bz.export_map(filepath=path, **kw)

    try:
        # --- no steps: identical, with the files beside it
        base = C.tmpdir("hooks_base_")
        assert "FINISHED" in save(base)
        base_files = folder_files(base)
        with open(os.path.join(base, "2p_faceoff.sgb"), "rb") as f:
            assert f.read() == raw
        assert "2p_faceoff.trg" in base_files, base_files

        # --- a step that touches nothing, and sees the context
        def idle(sctx):
            saw["sctx"] = sctx
            saw["fields"] = dict(name=sctx.name, group=sctx.group, source=sctx.source, original=sctx.original, sgb=sctx.sgb,
                                 files=dict(sctx.files), folder=sctx.folder, target=sctx.target, rtd=sctx.ready_to_drop,
                                 report=dict(sctx.report), view=sctx.view)
        H.register_save_step("idle", idle)
        out = C.tmpdir("hooks_idle_")
        assert "FINISHED" in save(out)
        assert folder_files(out) == base_files
        for n in base_files:
            with open(os.path.join(out, n), "rb") as a, open(os.path.join(base, n), "rb") as b:
                assert a.read() == b.read(), n
        f = saw["fields"]
        assert f["name"] == "2p_faceoff" and f["group"] == "mp" and f["source"] == FACEOFF and f["original"] == raw == f["sgb"]
        assert f["folder"] == out and f["target"] == os.path.join(out, "2p_faceoff.sgb") and f["rtd"] is False
        assert f["report"] == {"changes": [], "warnings": []} and f["view"].width > 0
        assert "2p_faceoff.trg" in f["files"] and f["files"]["2p_faceoff.trg"] == arc.read(arc.entry("scenarios/mp/2p_faceoff.trg"))
        assert set(f["files"]) == {n for n in base_files if n != "2p_faceoff.sgb"}, (sorted(f["files"]), base_files)
        print(f"   no steps and an idle step: {len(raw)} bytes back byte for byte, {len(base_files) - 1} files beside it", flush=True)
        H.unregister_step("idle")

        # --- replace the map's bytes, replace a file, add a file, drop a file, add lines
        dropped = next(n for n in base_files if n not in ("2p_faceoff.sgb", "2p_faceoff.trg"))

        def change(sctx):
            sctx.sgb = b"NEW-MAP-BYTES" + sctx.sgb[:50]
            sctx.files["2p_faceoff.trg"] = b"-- replaced by a step\n"
            sctx.files["extra/notes.txt"] = b"hello"
            sctx.files[dropped] = None
            sctx.add_change("Feature: something was written")
            sctx.add_warning("Feature: something to look at")

        def second(sctx):                                  # sees what the first one left
            assert sctx.sgb.startswith(b"NEW-MAP-BYTES") and sctx.sgb_changed
            sctx.files["extra/notes.txt"] += b" again"
        H.register_save_step("change", change, order=50)
        H.register_save_step("second", second, order=60)
        out = C.tmpdir("hooks_change_")
        assert "FINISHED" in save(out)
        with open(os.path.join(out, "2p_faceoff.sgb"), "rb") as fh:
            assert fh.read() == b"NEW-MAP-BYTES" + raw[:50]
        with open(os.path.join(out, "2p_faceoff.trg"), "rb") as fh:
            assert fh.read() == b"-- replaced by a step\n"
        with open(os.path.join(out, "extra", "notes.txt"), "rb") as fh:
            assert fh.read() == b"hello again"
        assert not os.path.exists(os.path.join(out, dropped))
        want = sorted((set(base_files) - {dropped}) | {"extra/notes.txt"})
        assert folder_files(out) == want, (folder_files(out), want)
        text = bpy.data.texts["Map save 2p_faceoff"].as_string()
        assert "Feature: something was written" in text and "Warning: Feature: something to look at" in text, text
        assert "Also written by map features:" in text and "2p_faceoff.trg" in text.split("Also written by map features:")[1], text
        print("   a step replaced the map bytes and a file, added one, dropped one and added report lines", flush=True)

        # --- the ready-to-drop folder carries the lines
        out = C.tmpdir("hooks_drop_")
        assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(out, "2p_faceoff.sgb"), ready_to_drop=True)
        readme = open(os.path.join(out, "README.txt"), encoding="utf-8").read()
        assert "Feature: something was written" in readme
        assert os.path.isfile(os.path.join(out, "Data", "scenarios", "mp", "extra", "notes.txt"))
        H.unregister_step("change")
        H.unregister_step("second")

        # --- refusals: nothing written
        def refuse(sctx):
            sctx.files["x.txt"] = b"x"
            sctx.refuse("The trigger script has a problem that must be fixed first.")
        H.register_save_step("refuse", refuse)
        out = C.tmpdir("hooks_refuse_")
        msg = expect_error("The trigger script has a problem that must be fixed first.", lambda: save(out))
        assert folder_files(out) == [], folder_files(out)
        H.unregister_step("refuse")

        def boom(sctx):
            sctx.files["y.txt"] = b"y"
            raise KeyError("oops")
        H.register_save_step("boom", boom)
        out = C.tmpdir("hooks_boom_")
        expect_error("'boom' could not be saved", lambda: save(out))
        assert folder_files(out) == []
        H.unregister_step("boom")

        for bad in ("../escape.txt", "/abs.txt", "a/../../b.txt", "2p_faceoff.sgb"):
            H.register_save_step("bad", lambda sctx, bad=bad: sctx.files.__setitem__(bad, b"z"))
            out = C.tmpdir("hooks_bad_")
            expect_error("not allowed", lambda: save(out))
            assert folder_files(out) == [] and not os.path.exists(os.path.join(os.path.dirname(out), "escape.txt")), bad
            H.unregister_step("bad")
        H.register_save_step("notbytes", lambda sctx: setattr(sctx, "sgb", "text"))
        out = C.tmpdir("hooks_nb_")
        expect_error("cannot be written", lambda: save(out))
        assert folder_files(out) == []
        H.unregister_step("notbytes")
        print("   refuse(), a step that raises, a bad file name and a map that is not bytes: refused, nothing written", flush=True)

        # --- after all of that, with no steps the save is identical again
        out = C.tmpdir("hooks_after_")
        assert "FINISHED" in save(out)
        with open(os.path.join(out, "2p_faceoff.sgb"), "rb") as fh:
            assert fh.read() == raw
        assert folder_files(out) == base_files
    finally:
        H.restore(state)


def test_from_memory(game_dir):
    from ICParse.sgb import model, parser
    from blender_native.sceneobj import map_hooks as H
    from blender_native.sceneobj import ops_map_export as X
    board = model.Sgb.blank(16, 16, name="Hooks test", players=2)
    data = parser.write(board)
    C.fresh()
    top = H.import_map_bytes(bpy.context, data, "hookmap", {"load_models": False, "game_look": False})
    assert top.get("ic_map") and top.name == "Map hookmap" and top.get(H.KEY_SOURCE_TEXT), dict(top.items())
    assert top.get("ic_map_source") == "" and X.terrain_of(top) is not None
    seen = {}
    state = H.snapshot()
    try:
        H.register_save_step("look", lambda s: seen.update(source=s.source, name=s.name, files=dict(s.files), original=s.original,
                                                           group=s.group))
        out = C.tmpdir("hooks_mem_")
        assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(out, "hookmap.sgb"))
        with open(os.path.join(out, "hookmap.sgb"), "rb") as fh:
            assert fh.read() == data, "a map opened from memory and saved with no edits must be byte for byte the bytes"
        assert folder_files(out) == ["hookmap.sgb"], folder_files(out)
        assert seen["source"] == "" and seen["name"] == "hookmap" and seen["files"] == {} and seen["original"] == data
        # a step can add the trigger script of a new map
        H.unregister_step("look")
        H.register_save_step("trg", lambda s: s.files.__setitem__(f"{s.name}.trg", b"-- new\nfolders = {}\n"))
        out = C.tmpdir("hooks_mem2_")
        assert "FINISHED" in bpy.ops.bz.export_map(filepath=os.path.join(out, "hookmap.sgb"))
        assert folder_files(out) == ["hookmap.sgb", "hookmap.trg"]
    finally:
        H.restore(state)
    print(f"   a map from {len(data)} bytes in memory: imported, saved byte for byte, a step added its .trg", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map hooks test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("loading and the panel id")
        test_loading()
        section("the registry")
        from blender_native.sceneobj import map_hooks as H
        features = H.snapshot()
        before = H.registered()
        try:
            test_registry()
            H.restore(([], [], []))                       # the hooks themselves are tested with no feature registered
            section("an import step and the checks")
            test_import_step_and_checks(game_dir)
            section("save steps")
            test_saves(game_dir)
            section("a map from memory")
            test_from_memory(game_dir)
        finally:
            H.restore(features)
    assert H.registered() == before, "the test left the map features' steps changed"
    print("SO_MAP_HOOKS_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_hooks", main)
