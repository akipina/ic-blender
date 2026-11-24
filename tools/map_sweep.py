"""map_sweep -- every stock map of the game, imported and saved untouched, in a headless Blender (release 0.7.0).

    blender --background --factory-startup --python blender_native/tools/map_sweep.py -- OUT.json [--full] [--only TEXT] [--limit N]

Per map (every .sgb of every archive the game's map list shows): import with the placed models, creatures and game look off (``--full``:
all on), save it untouched under its own name into a temp folder, and compare the map file and every file beside it with the original
(``ops_map_export.sibling_files``: what a save copies). A map that differs is saved again with each save step on its own to name the
step that changed it. The exit is 0 only when every map came back byte for byte; the maps whose own core save differs from the original
with NO steps registered (a stock fault, not the features') are listed apart. Writes only to temp folders."""
import json, os, sys, time, shutil
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
import _so_common as C
import bpy
sys.path.insert(0, C.REPO)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv and not argv[0].startswith("--") else os.path.join(C.tmpdir("sweep_json_"), "map_sweep.json")
FULL = "--full" in argv
ONLY = argv[argv.index("--only") + 1] if "--only" in argv else ""
LIMIT = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0
OPTS = dict(load_models=FULL, show_creatures=FULL, game_look=FULL)


def saved_set(folder):
    out = {}
    for d, _s, fs in os.walk(folder):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder).replace(os.sep, "/")] = fh.read()
    return out


def run_one(gm, expected_cache):
    from blender_native.sceneobj import ops_map_export as X, map_hooks as H
    raw = gm.read()
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=gm.key, **OPTS)
    assert "FINISHED" in res, res
    top = [c for c in bpy.data.collections if c.get("ic_map")][0]
    plan = X.build_plan(bpy.context, top)
    stem = os.path.splitext(os.path.basename(gm.member))[0]
    want = {f"{stem}.sgb": raw}
    for sub, rest, data in X.sibling_files(plan, top.get("ic_map_source", "")):
        want[(f"{sub}/{stem}{rest}" if sub else f"{stem}{rest}")] = data

    def save_with(state):
        keep = H.snapshot()
        if state is not None:
            H.restore(state)
        out = C.tmpdir("sw_")
        try:
            r = bpy.ops.bz.export_map(filepath=os.path.join(out, f"{stem}.sgb"))
            if "FINISHED" not in r:
                return None, f"export {r}"
            return saved_set(out), None
        finally:
            H.restore(keep)
            shutil.rmtree(out, ignore_errors=True)

    got, err = save_with(None)
    row = {"map": gm.key, "bytes": len(raw), "files": len(want)}
    if err:
        row.update(ok=False, error=err)
        return row
    diff = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
    row["differs"] = diff
    row["ok"] = not diff
    if diff:
        snap = H.snapshot()
        bare, err = save_with(([], [], []))
        row["core_differs_without_steps"] = sorted(k for k in set(want) | set(bare or {}) if want.get(k) != (bare or {}).get(k)) if bare else err
        blame = {}
        for entry in snap[1]:                                 # one save step at a time
            name = entry[2]
            one, err = save_with(([], [entry], []))
            d = sorted(k for k in set(want) | set(one or {}) if want.get(k) != (one or {}).get(k)) if one else [err]
            if d and d != row["core_differs_without_steps"]:
                blame[name] = d
        row["blame"] = blame
    return row


def main():
    game_dir = C.install_root()
    assert game_dir
    C.addon()
    from ICParse.sgb import view
    maps = [m for m in view.game_maps(game_dir) if ONLY in m.key]
    if LIMIT:
        maps = maps[:LIMIT]
    rows = []
    t0 = time.perf_counter()
    with C.install_guard(game_dir):
        for gm in maps:
            t = time.perf_counter()
            try:
                row = run_one(gm, None)
            except Exception as exc:                       # noqa: BLE001
                import traceback
                row = {"map": gm.key, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
            row["s"] = round(time.perf_counter() - t, 2)
            rows.append(row)
            print(("OK   " if row.get("ok") else "FAIL ") + f"{gm.key} {row['s']} s" + ("" if row.get("ok") else " " + json.dumps({k: row.get(k) for k in ("differs", "core_differs_without_steps", "blame", "error")})), flush=True)
            with open(OUT, "w") as f:
                json.dump(rows, f, indent=1, default=str)
    bad = [r for r in rows if not r.get("ok")]
    stock = [r for r in bad if r.get("core_differs_without_steps")]
    print(f"MAP_SWEEP_DONE {len(rows) - len(bad)} identical, {len(bad)} differ ({len(stock)} of them differ with no steps registered), {time.perf_counter() - t0:.0f} s", flush=True)
    assert len(bad) == len(stock), "maps that differ only with the features' steps: " + ", ".join(r["map"] for r in bad if not r.get("core_differs_without_steps"))


if __name__ == "__main__":
    C.run_test("map_sweep", main)
