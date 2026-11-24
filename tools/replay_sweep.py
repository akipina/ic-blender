"""replay_sweep -- a replay of every cinematic of every stock map, in a headless Blender (release 0.8.0).

    blender --background --factory-startup --python blender_native/tools/replay_sweep.py -- OUT.json [--only TEXT] [--limit N]

Per stock map: import it (no models), save it untouched (the reference), list its cinematics, build each one's replay (the timeline
from ``ICParse.sgb.cine_timeline``, the stand-ins and camera from ``sceneobj.map_replay``), count built / failed, the build time and the
events by quality, clear it again and check the scene (frame rate, range, camera, originals shown, no replay object left). When the map
has cinematics, the save with the longest replay shown and the save after all replays were cleared must be byte for byte the
reference (the map file and every file beside it). Writes only to temp folders; the exit is 0 only when nothing failed and every
save is identical."""
import json, os, sys, time, shutil, traceback
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
import _so_common as C
import bpy
sys.path.insert(0, C.REPO)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0] if argv and not argv[0].startswith("--") else os.path.join(C.tmpdir("sweep_json_"), "replay_sweep.json")
ONLY = argv[argv.index("--only") + 1] if "--only" in argv else ""
LIMIT = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 0


def saved_set(folder):
    out = {}
    for d, _s, fs in os.walk(folder):
        for f in fs:
            with open(os.path.join(d, f), "rb") as fh:
                out[os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/")] = fh.read()
    return out


def save(top):
    stem = top.name[4:] if top.name.startswith("Map ") else top.name
    out = C.tmpdir("rs_")
    try:
        r = bpy.ops.bz.export_map(filepath=os.path.join(out, f"{stem}.sgb"))
        assert "FINISHED" in r, r
        return saved_set(out)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def run_one(gm):
    from blender_native.sceneobj import map_replay as R
    from ICParse.sgb import cine_timeline as CT
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=gm.key, load_models=False, show_creatures=False, game_look=False)
    assert "FINISHED" in res, res
    top = [c for c in bpy.data.collections if c.get("ic_map")][0]
    row = {"map": gm.key, "cinematics": [], "built": 0, "failed": 0}
    t0 = time.perf_counter()
    cines = R.cinematics(bpy.context, top, refresh=True)
    row["list_s"] = round(time.perf_counter() - t0, 2)
    if not cines:
        row["ok"] = True
        return row
    base = save(top)
    board, side = R.inputs_of(top)
    sc = bpy.context.scene
    before = (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera)
    placed = R.placed_objects(top)
    longest = None
    for cine in cines:
        key, title = R._g(cine, "key"), R._g(cine, "title")
        c = {"key": key, "title": title}
        try:
            t0 = time.perf_counter()
            tl = CT.build(board, side, key)
            c["timeline_s"] = round(time.perf_counter() - t0, 2)
            c["duration_s"] = round(float(tl.duration_s), 1)
            q = {"exact": 0, "approximate": 0, "skipped": 0}
            kinds = {}
            for e in tl.events:
                q[e.quality] = q.get(e.quality, 0) + 1
                kinds.setdefault(e.kind, {"exact": 0, "approximate": 0, "skipped": 0})
                kinds[e.kind][e.quality] = kinds[e.kind].get(e.quality, 0) + 1
            c["events"] = len(tl.events)
            c["quality"] = q
            c["kinds"] = kinds
            t0 = time.perf_counter()
            rep = R.build_replay(bpy.context, top, key, timeline=tl, title=title)
            c["build_s"] = round(time.perf_counter() - t0, 2)
            c.update(stand_ins=rep["stand_ins"], keys=rep["keys"], markers=rep["markers"], clips=rep["clips"], nis=rep.get("nis", 0), frames=rep["frames"])
            if longest is None or tl.duration_s > longest[0]:
                longest = (tl.duration_s, key, title, tl)
            R.clear_replay(bpy.context, top)
            assert (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera) == before, "scene not restored"
            assert not any(o.hide_get() for o in placed.values()), "originals not shown again"
            assert not [o for o in bpy.data.objects if o.get(R.K_PART)], "replay object left"
            c["ok"] = True
            row["built"] += 1
        except Exception as exc:                                # noqa: BLE001
            c.update(ok=False, error=f"{type(exc).__name__}: {exc}", trace=traceback.format_exc())
            row["failed"] += 1
            try:
                R.clear_replay(bpy.context, top)
            except Exception:                                   # noqa: BLE001
                pass
        row["cinematics"].append(c)
    row["save_cleared_identical"] = save(top) == base
    shown = True
    if longest is not None:
        R.build_replay(bpy.context, top, longest[1], timeline=longest[3], title=longest[2])
        shown = save(top) == base
        R.clear_replay(bpy.context, top)
    row["save_shown_identical"] = shown
    row["ok"] = row["failed"] == 0 and row["save_cleared_identical"] and shown
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
    with C.install_guard(game_dir):
        for gm in maps:
            t = time.perf_counter()
            try:
                row = run_one(gm)
            except Exception as exc:                            # noqa: BLE001
                row = {"map": gm.key, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(), "cinematics": [], "built": 0, "failed": 0}
            row["s"] = round(time.perf_counter() - t, 2)
            rows.append(row)
            print(("OK   " if row.get("ok") else "FAIL ") + f"{gm.key}: {len(row['cinematics'])} cinematics, built {row['built']}, failed {row['failed']}, {row['s']} s", flush=True)
            with open(OUT, "w") as f:
                json.dump(rows, f, indent=1, default=str)
    with_c = [r for r in rows if r["cinematics"]]
    bad = [r for r in rows if not r.get("ok")]
    print(f"{len(rows)} maps, {len(with_c)} with cinematics, {sum(r['built'] for r in rows)} replays built, {sum(r['failed'] for r in rows)} failed, {len(bad)} maps with a problem")
    sys.exit(1 if bad else 0)


main()
