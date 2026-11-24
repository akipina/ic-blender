#!/usr/bin/env python3
"""Engine parity: what the add-on renders headlessly must match the Object Editor's draw stream.

    .venv/bin/python3 blender_native/tests/engine_parity.py                 # the standard set (bind pose)
    .venv/bin/python3 blender_native/tests/engine_parity.py cheetah henchmen  # a subset
    .venv/bin/python3 blender_native/tests/engine_parity.py --anim           # animated: clips playing in the editor
    .venv/bin/python3 blender_native/tests/engine_parity.py --capture-root /tmp/parity -v

Animated mode (``--anim``): the editor plays a clip while frames are captured; for every captured frame
the clip time that explains the editor's pose is fitted with ICParse's decode, Blender bakes the clip at
that time onto the rig (creature: posed bake + posed overlay; scene object: the armature-deformed
meshes) and the posed render is compared like the bind pose: the editor's pose within half a millimetre,
and Blender's pose equal to ICParse's decode at the same time (both reported per frame).

For every asset: (1) capture the editor's Direct3D draws for it (``ICParse/tools/oe_load_check.py
--capture``, cached under the capture root), (2) import it in headless Blender through the add-on and
dump the evaluated render mesh (``blender_native/tools/render_dump.py``), (3) compare the two in data
(``ICParse/tools/oe_geometry_compare.py``): positions, normals, UVs, triangles, textures, per-draw
texture binding. Creatures also check the live gpu overlay's surface. Exit code 0 = every asset PASSes.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(HERE))
BLENDER = os.environ.get("BLENDER") or shutil.which("blender") or "blender"
sys.path.insert(0, os.path.join(REPO, "ICParse", "tools"))

# name -> (source for Blender, .sgm for the editor, .ebp for the editor or None)
STOCK = os.path.join(REPO, "Assets", "engine_stock")
SCENE = os.path.join(REPO, "Assets", "engine_scene")
SET = {
    "cheetah": (f"{STOCK}/cheetah.sgm", f"{STOCK}/cheetah.sgm", None),
    "scorpion": (f"{STOCK}/scorpion.sgm", f"{STOCK}/scorpion.sgm", None),
    "eagle": (f"{STOCK}/eagle.sgm", f"{STOCK}/eagle.sgm", None),
    "lobster": (f"{STOCK}/lobster.sgm", f"{STOCK}/lobster.sgm", None),
    "henchmen_lab": (f"{SCENE}/henchmen_lab.ebp", f"{SCENE}/henchmen_lab.sgm", f"{SCENE}/henchmen_lab.ebp"),
    "gyro_copter_henchman": (f"{SCENE}/gyro_copter_henchman.ebp", f"{SCENE}/gyro_copter_henchman.sgm", f"{SCENE}/gyro_copter_henchman.ebp"),
    "waterfall10m": (f"{SCENE}/10mwaterfall.sgm", f"{SCENE}/10mwaterfall.sgm", f"{SCENE}/10mwaterfall.ebp"),
    "henchmen": (f"{SCENE}/henchmen.ebp", f"{SCENE}/henchmen.sgm", f"{SCENE}/henchmen.ebp"),
    "gazelle": (f"{REPO}/Assets/stock/gazelle.sgm", f"{REPO}/Assets/stock/gazelle.sgm", None),   # Tellurian, v1 clips
}
# animated cases: (asset name in SET, clip in its Animation List): a base-game creature (v7 clips), a
# Tellurian creature (v1 clips), the skinned henchmen character, a rigid-part structure
ANIM_SET = [("cheetah", "leapattack"), ("gazelle", "leapattack"), ("henchmen", "Run"), ("henchmen_lab", "Damage_04")]
ANIM_TOL = 0.0005     # metres: ICParse's playback is engine-exact (float32 + the clip-time fit); half a millimetre
# Blender represents an animated bone as translation/rotation/scale. The engine's matrix between two keys
# is (1-|q|^2) I + |q|^2 R (its unnormalised quaternion blend), which carries a small shear an armature
# cannot hold, so the add-on's pose differs from the engine's by up to ~0.4% of a part's lever arm at
# mid-key (0.3 mm on a creature, centimetres on a collapsing 20 m building). That is the accepted
# armature representation, checked here as a fraction of the object's size.
ARMATURE_TOL = 2.5e-3


def capture(name, sgm, ebp, root, timeout, play=None, play_frames=6):
    """The editor's draws for an asset: cached under <root>/<name>/ (bind pose: frame 21; with ``play``
    every frame captured while the clip played)."""
    d = os.path.join(root, name)
    if play:
        have = sorted(f for f in os.listdir(d) if f.startswith("frame_") and f.endswith(".json")) if os.path.isdir(d) else []
        if len(have) >= 2:
            return [os.path.join(d, f) for f in have[1:]], "cached"   # frame 20 (bind) + the played frames
    else:
        frame = os.path.join(d, "frame_00021.json")
        if os.path.exists(frame):
            return [frame], "cached"
    os.makedirs(d, exist_ok=True)
    cmd = [sys.executable, os.path.join(REPO, "ICParse", "tools", "oe_load_check.py"), sgm,
           "--capture", d, "--capture-frames", "20" if play else "20,21", "--timeout", "60"]
    if ebp:
        cmd += ["--ebp", ebp]
    if play:
        cmd += ["--play", play, "--play-frames", str(play_frames), "--play-interval", "0.3"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO)
    verdict = next((ln.split()[0] for ln in r.stdout.splitlines() if ln[:1].isupper() and ln.split()[0] in ("LOADED", "FATAL", "CRASH", "TIMEOUT")), "?")
    have = sorted(f for f in os.listdir(d) if f.startswith("frame_") and f.endswith(".json")) if os.path.isdir(d) else []
    if play:
        frames = [os.path.join(d, f) for f in have[1:]]
    else:
        frames = [os.path.join(d, "frame_00021.json")] if "frame_00021.json" in have else []
    return frames, verdict


def dump(name, src, root, density, timeout, anim=None, t=None, tag=""):
    out = os.path.join(root, name, f"blender_dump_d{density}{tag}.npz")
    tool = os.path.join(REPO, "blender_native", "tools", "render_dump.py")
    cmd = [BLENDER, "--background", "--factory-startup", "--python", tool, "--", src, out, "--density", str(density)]
    if anim:
        cmd += ["--anim", anim, "--t", f"{t:.6f}"]
    env = dict(os.environ, PYIC_REPO=REPO)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO, env=env)
    ok = os.path.exists(out) and "RENDER_DUMP_OK" in r.stdout
    return (out if ok else None), r.stdout + r.stderr


def fit_time(frame_json, sgm, clip):
    """The clip time that explains a captured frame, from ICParse's own decode (creature or scene).
    Returns (t, editor-vs-ICParse residual, ICParse positions at t)."""
    import numpy as np
    import d3d8cap as C
    import oe_geometry_compare as G
    from ICParse.sgm import parser as SP
    fr = C.load(frame_json)
    draws = G.creature_draws(fr)
    E = np.vstack([d.world_positions() for d in draws])
    m = SP.read(sgm)
    an = G.find_clip(m, clip)
    if hasattr(m, "meshes"):
        pos_at = G.scene_positions_at(m, an)
    else:
        n_patch = len(m.patches)
        n = int(round(np.sqrt(sum(len(d.triangles) for d in draws) / n_patch / 2))) + 1
        pos_at = G.creature_positions_at(m, n)(an)
    t, resid = G.fit_clip_time(E, pos_at, an.frame_count)
    return t, resid, np.asarray(pos_at(t), dtype=np.float64).reshape(-1, 3)


def res_size(frame_json):
    import numpy as np
    import d3d8cap as C
    import oe_geometry_compare as G
    E = np.vstack([d.world_positions() for d in G.creature_draws(C.load(frame_json))])
    return float(np.linalg.norm(E.max(0) - E.min(0)))


def blender_vs_icparse(dump_path, icparse_pos):
    """Max distance between what Blender rendered at t and ICParse's decode at the same t (both ours):
    the add-on's rig/skin/tessellation pipeline against the library, independent of the engine."""
    import numpy as np
    from scipy.spatial import cKDTree
    import oe_geometry_compare as G
    P = np.asarray(G.load_dump(dump_path)["loop_pos"], dtype=np.float64)
    d1 = cKDTree(icparse_pos).query(P)[0].max()
    d2 = cKDTree(P).query(icparse_pos)[0].max()
    return float(max(d1, d2))


def run_animated(a):
    import d3d8cap as C
    import oe_geometry_compare as G
    rows = []
    for name, clip in ANIM_SET:
        if a.names and name not in a.names:
            continue
        src, sgm, ebp = SET[name]
        if not os.path.exists(src):
            rows.append((f"{name}:{clip}", "SKIP", 0.0, "asset missing")); continue
        t0 = time.time()
        cname = f"{name}_{clip}"
        frames, how = capture(cname, sgm, ebp, a.capture_root, a.timeout, play=clip, play_frames=a.frames)
        if not frames:
            rows.append((f"{name}:{clip}", "FAIL", time.time() - t0, f"editor capture failed ({how})")); continue
        worst = 0.0; worst_lib = 0.0; details = []; ok_all = True
        for fj in frames:
            t, resid, ipos = fit_time(fj, sgm, clip)
            fb = os.path.basename(fj)[:-5]
            dmp, log = dump(cname, src, a.capture_root, a.density, a.timeout, anim=clip, t=t, tag="_" + fb)
            if dmp is None:
                ok_all = False; details.append(f"{fb}: dump failed"); print(log[-800:]); continue
            if a.v:
                print(f"=== {name} {clip} {fb}: fitted t={t:.4f} (ICParse residual {resid:.2e})")
            res = G.compare_render(C.load(fj), G.load_dump(dmp), verbose=a.v, tol=ARMATURE_TOL * res_size(fj))
            lib = blender_vs_icparse(dmp, ipos)                  # Blender vs ICParse at the same t
            dev = max(res["geometry_e2o_max"], res.get("overlay_geometry_max", 0.0))
            worst = max(worst, resid); worst_lib = max(worst_lib, lib / res["size"])
            good = (resid <= ANIM_TOL and lib <= ARMATURE_TOL * res["size"] and res["textures_ok"]
                    and res["binding_ok"] == res["draws"])
            ok_all &= good
            details.append(f"{fb} t={t:.3f}: editor-vs-icparse {resid * 1000:.3f} mm, blender-vs-icparse {lib * 1000:.2f} mm "
                           f"({lib / res['size'] * 100:.2f}% of size), blender-vs-editor {dev * 1000:.1f} mm{'' if good else ' FAIL'}")
        verdict = "PASS" if ok_all else "FAIL"
        rows.append((f"{name}:{clip}", verdict, time.time() - t0,
                     f"engine-vs-ICParse worst {worst * 1000:.3f} mm (tol {ANIM_TOL * 1000:.1f}), Blender armature worst "
                     f"{worst_lib * 100:.2f}% of size (tol {ARMATURE_TOL * 100:.2f}%), {len(frames)} frames | " + "; ".join(details)))
        print(f"{verdict:5s} {rows[-1][2]:6.1f}s  {name}:{clip:12s} {rows[-1][3]}", flush=True)
    failed = [r for r in rows if r[1] == "FAIL"]
    print(f"\n{sum(1 for r in rows if r[1] == 'PASS')}/{len(rows)} animated cases render like the Object Editor")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="assets to check (default: all of the standard set)")
    ap.add_argument("--capture-root", default=os.environ.get("BZ_PARITY_ROOT", os.path.join(REPO, "Assets", "captures")),
                    help="where editor captures and Blender dumps are cached (git-ignored Assets/captures by default)")
    ap.add_argument("--density", type=int, default=5, help="creature bake density (editor default 5)")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("-v", action="store_true", help="print the full comparison for every asset")
    ap.add_argument("--anim", action="store_true", help="animated mode: clips playing in the editor (ANIM_SET)")
    ap.add_argument("--frames", type=int, default=6, help="animated mode: frames to capture per clip")
    a = ap.parse_args()
    import d3d8cap as C
    import oe_geometry_compare as G
    if a.anim:
        return run_animated(a)
    names = a.names or list(SET)
    rows = []
    for name in names:
        src, sgm, ebp = SET[name]
        if not os.path.exists(src):
            rows.append((name, "SKIP", 0.0, "asset missing (extract with blender_native/tools/extract_stock.py)")); continue
        t = time.time()
        frames, how = capture(name, sgm, ebp, a.capture_root, a.timeout)
        frame = frames[0] if frames else None
        if frame is None:
            rows.append((name, "FAIL", time.time() - t, f"editor capture failed ({how})")); continue
        dmp, log = dump(name, src, a.capture_root, a.density, a.timeout)
        if dmp is None:
            rows.append((name, "FAIL", time.time() - t, "blender dump failed: " + "\n".join(log.strip().splitlines()[-8:]))); continue
        if a.v:
            print(f"=== {name} (capture {how})")
        try:
            res = G.compare_render(C.load(frame), G.load_dump(dmp), verbose=a.v)
        except Exception as e:
            rows.append((name, "FAIL", time.time() - t, f"compare error: {e}")); continue
        detail = (f"pos {res['geometry_e2o_max']:.1e} normals {res['normal_max']:.2f}deg uv {res['uv_max']:.1e} "
                  f"tris {res['triangle_max']:.1e} textures {'ok' if res['textures_ok'] else 'DIFF'} "
                  f"binding {res['binding_ok']}/{res['draws']}")
        if "overlay_normal_max" in res:
            detail += f" overlay {res['overlay_geometry_max']:.1e}/{res['overlay_normal_max']:.2f}deg"
        if res["fail"]:
            detail += " | FAILED: " + ", ".join(res["fail"])
        rows.append((name, "PASS" if res["ok"] else "FAIL", time.time() - t, detail))
        print(f"{rows[-1][1]:5s} {rows[-1][2]:6.1f}s  {name:22s} {detail}", flush=True)
    failed = [r for r in rows if r[1] == "FAIL"]
    for name, verdict, _, detail in rows:
        if verdict != "PASS":
            print(f"{verdict:5s} {name}: {detail}")
    print(f"\n{sum(1 for r in rows if r[1] == 'PASS')}/{len(rows)} assets render like the Object Editor")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
