"""The Replay (a cinematic acted out on the timeline) in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_replay_test.py     (or: run_all.py -k so_map_replay)

1. hand-made timelines on mission1 (every event kind the replay draws): stand-ins stand where the timeline says at chosen frames (read back
   with frame_set), a walk is linear on the ground, a teleport is a step, despawn / respawn / delete / visible key the stand-in's
   visibility, face / look_at turn it, motion variables and animation numbers are keyed, a move animation is followed, the camera chain
   (play, pause, resume, cut, stop) and its markers come in order, a fade plane is keyed, the frame rate and range are set;
2. the originals are hidden while the replay is shown and shown again after Clear (and the scene's frame rate / range / camera are back);
3. every real cinematic of mission1 and of one more campaign map builds (when ICParse.sgb.cine_timeline is installed), with its timings;
4. THE REPLAY NEVER CHANGES THE MAP: a save with a replay built, and after clearing it, is byte for byte the save without one.
Nothing under the game folder changed.
"""
import math
import os
import sys
import time
from dataclasses import dataclass, field

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)

MISSION1 = "Engine.sga|scenarios/sp/mission1.sgb"
MISSION15 = "Engine.sga|scenarios/sp/mission15_nis6.sgb"
OTHER_CAMPAIGN = [f"Engine.sga|scenarios/sp/mission{n}.sgb" for n in (2, 3, 4, 8, 12, 13, 15)]


@dataclass
class Ev:
    t: float
    kind: str
    entities: list = field(default_factory=list)
    data: dict = field(default_factory=dict)
    quality: str = "exact"
    why: str = ""


@dataclass
class TL:
    duration_s: float
    events: list
    notes: list = field(default_factory=list)


def section(title):
    print(f"-- {title}", flush=True)


def import_map(key, load_models=False):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=load_models, show_creatures=False, game_look=False)
    assert "FINISHED" in res, (key, res)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def save(top, folder):
    stem = top.name[4:] if top.name.startswith("Map ") else top.name
    res = bpy.ops.bz.export_map(filepath=os.path.join(folder, stem + ".sgb"))
    assert "FINISHED" in res, res
    return {f: open(os.path.join(d, f), "rb").read() for d, _s, fs in os.walk(folder) for f in fs}


def at(frame):
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()


def near(a, b, tol=1e-3):
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


def gpos(obj):
    from blender_native.sceneobj import map_replay as R
    return R.b2g(obj.matrix_world.translation)


def pick_entities(top, n):
    from blender_native.sceneobj import map_replay as R
    placed = R.placed_objects(top)
    return sorted(placed)[:n], placed


def hand_timeline(top):
    from blender_native.sceneobj import map_cinematics as MC
    from blender_native.sceneobj import map_replay as R
    ids, placed = pick_entities(top, 6)
    a, b, c, d, e, f = ids
    root = MC.root_of(top)
    cams = [x for x in MC.anim_collections(root) if x.get("nis_kind") == "camera" and int(x["nis_id"]) < 100000 and len(MC.key_objects(x)) >= 2]
    moves = [x for x in MC.anim_collections(root) if x.get("nis_kind") == "move" and int(x["nis_id"]) < 100000 and len(MC.key_objects(x)) >= 2]
    assert len(cams) >= 2 and moves, (len(cams), len(moves))
    p0 = R.b2g(placed[a].matrix_world.translation)
    pc = R.b2g(placed[c].matrix_world.translation)
    # the camera animation's distances over time, as the timeline gives them: the game's own stepping, held still from 3 s to 4 s (a pause)
    pt = R.PathTimes(cams[0], "camera")
    base = pt.fallback_samples(0.0, blend_in=0.0)
    d3 = next(dd for ss, dd in base if ss >= 3.0)
    smp = [[ss, dd] for ss, dd in base if ss < 3.0] + [[3.0, d3], [4.0, d3]] + [[ss + 1.0, dd] for ss, dd in base if ss > 3.0]
    smp2 = [x for x in R.PathTimes(cams[1], "camera").fallback_samples(0.0) if x[0] <= 2.0]      # stopped 2 s after it started
    ev = [
        Ev(0.0, "camera", [], {"action": "play", "anim": int(cams[0]["nis_id"]), "start_m": 0, "samples": smp, "blend_in_s": 0.1}),
        Ev(1.0, "teleport", [a], {"pos": [p0[0] + 20, p0[1], p0[2] + 10], "heading": [-math.pi / 2], "keep_heading": False}),
        Ev(2.0, "walk", [b], {"path": [[100.0, 100.0], [100.0, 160.0], [140.0, 160.0]], "speed": 10.0, "t_end": 9.0}, "approximate", "the simulation decides"),
        Ev(3.0, "despawn", [c]),
        Ev(5.0, "respawn", [c], {"pos": [list(pc)], "heading": [0.0]}),
        Ev(6.0, "delete", [d]),
        Ev(4.0, "visible", [e], {"on": False}),
        Ev(7.0, "visible", [e], {"on": True}),
        Ev(3.5, "face", [f], {"heading": [math.pi], "toward": [], "turn_s": [0.0]}),
        Ev(4.5, "look_at", [f], {"target": [a], "turn_if_over_deg": 60.0}),
        Ev(2.5, "motion_var", [a], {"name": "moving", "value": 1.0}),
        Ev(2.6, "anim", [a], {"name": "walk", "text_id": 5, "loop": None}),
        Ev(3.0, "camera", [], {"action": "pause", "anim": int(cams[0]["nis_id"])}),
        Ev(4.0, "camera", [], {"action": "resume", "anim": int(cams[0]["nis_id"])}),
        Ev(6.0, "camera", [], {"action": "cut", "anim": int(cams[1]["nis_id"]), "start_m": 0, "samples": smp2, "blend_in_s": 0.1}),
        Ev(8.0, "camera", [], {"action": "stop", "anim": int(cams[1]["nis_id"])}),
        Ev(10.0, "move_anim", [f], {"anim": int(moves[0]["nis_id"]), "start_m": 0}),
        Ev(1.0, "fade", [], {"from": 1.0, "to": 0.0, "duration_s": 2.0}),
        Ev(4.0, "dialog", [], {"id": 7, "name": "hello", "duration_s": None}),
        Ev(4.2, "cinematic_mode", [], {"on": True, "transition_s": 0.0}),
        Ev(4.3, "focus", [], {"pos": [10, 0, 10]}),
        Ev(4.4, "teleport", [b], {}, "skipped", "nothing to show"),
        Ev(12.0, "create", [9000001], {"blueprint": "DATA:ART\\EBPS\\NOPE\\NOTHING.EBP", "pos": [30.0, 0.0, 30.0], "heading": -math.pi / 4, "new_id": 9000001}),
    ]
    return TL(14.0, ev), (a, b, c, d, e, f), (cams, moves)


def test_hand(game_dir):
    from blender_native.sceneobj import map_replay as R
    from blender_native.sceneobj import map_cinematics as MC
    top = import_map(MISSION1)
    tl, (a, b, c, d, e, f), (cams, moves) = hand_timeline(top)
    sc = bpy.context.scene
    before = (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera)
    t0 = time.perf_counter()
    rep = R.build_replay(bpy.context, top, "hand", timeline=tl, title="Hand made", game_dir=game_dir)
    dt = time.perf_counter() - t0
    print(f"   built: {rep['stand_ins']} stand-ins, {rep['events']} events {rep['quality']}, {rep['keys']} keys, {rep['markers']} markers, "
          f"frames {rep['frames']}, {dt:.2f} s", flush=True)
    coll = R.replay_of(top)
    assert coll is not None and coll.name not in {c.name for c in top.children_recursive}, "the replay lives outside the map"
    assert sc.render.fps == R.FPS and sc.frame_start == 1 and sc.frame_end >= R.frame_of(12.0), (sc.frame_end, rep["frames"])
    st = R.standins(coll)
    assert set(st) == {a, b, c, d, e, f, 9000001}, sorted(st)
    placed = R.placed_objects(top)
    # --- teleport: a step at t = 1 s
    p0 = R.b2g(placed[a].matrix_world.translation)
    at(R.frame_of(0.5))
    assert near(gpos(st[a]), p0), (gpos(st[a]), p0)
    at(R.frame_of(1.0))
    assert near(gpos(st[a]), (p0[0] + 20, p0[1], p0[2] + 10)), gpos(st[a])
    # heading 90 faces game -x
    at(R.frame_of(1.5))
    fwd = st[a].matrix_world.to_3x3() @ __import__("mathutils").Vector((0, -1, 0))
    gfx, _gy, gfz = R.b2g(fwd)
    assert abs(gfx + 1) < 1e-3 and abs(gfz) < 1e-3, (gfx, gfz)
    # the teleport is a step: no motion between the keys
    ctr = [fc for fc in R._fcurves_of(st[a]) if fc.data_path == "location"]
    assert ctr and all(kp.interpolation == "CONSTANT" for fc in ctr for kp in fc.keyframe_points)
    # --- walk: from t=2 to t=9 along the route, linear and on the ground
    at(R.frame_of(2.0))
    start = gpos(st[b])
    at(R.frame_of(9.0))
    end = gpos(st[b])
    assert near((end[0], end[2]), (140.0, 160.0), 1e-2), end
    at(R.frame_of(5.5))
    mid = gpos(st[b])
    # at half the time the walker has done half of the route
    pts = [(start[0], start[2]), (100.0, 100.0), (100.0, 160.0), (140.0, 160.0)]
    if math.dist(pts[0], pts[1]) <= 0.5:
        pts = pts[1:]
    total = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
    done = 0.0
    want = None
    for p, q in zip(pts, pts[1:]):
        L = math.dist(p, q)
        if done + L >= total / 2:
            u = (total / 2 - done) / L
            want = (p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u)
            break
        done += L
    assert math.dist((mid[0], mid[2]), want) < 1.5, (mid, want)          # the keys are every 3 m at most, on a straight line
    ground = R.Ground(__import__("blender_native.sceneobj.ops_map_export", fromlist=["x"]).terrain_of(top))
    gh = ground.height(mid[0], mid[2])
    assert gh is not None and abs(mid[1] - gh) < 20.0, (mid, gh)           # on the ground (plus the object's own height above it)
    # heading follows the route: the last leg points game +x -> heading -90
    at(R.frame_of(8.9))
    fwd = st[b].matrix_world.to_3x3() @ __import__("mathutils").Vector((0, -1, 0))
    gx, _gy, gz = R.b2g(fwd)
    assert gx > 0.99, (gx, gz)
    # --- visibility
    def hidden(o, t):
        at(R.frame_of(t))
        return bool(o.hide_viewport), bool(o.hide_render)
    assert hidden(st[c], 2.9) == (False, False) and hidden(st[c], 3.1) == (True, True) and hidden(st[c], 5.1) == (False, False)
    assert hidden(st[d], 5.9) == (False, False) and hidden(st[d], 6.1) == (True, True) and hidden(st[d], 13.0) == (True, True)
    assert hidden(st[e], 3.9) == (False, False) and hidden(st[e], 4.1) == (True, True) and hidden(st[e], 7.1) == (False, False)
    assert hidden(st[9000001], 11.9) == (True, True) and hidden(st[9000001], 12.1) == (False, False)
    at(R.frame_of(13.0))
    assert near(gpos(st[9000001]), (30.0, 0.0, 30.0), 1e-3)
    # --- turns
    def heading(o, t):
        at(R.frame_of(t))
        fwd = o.matrix_world.to_3x3() @ __import__("mathutils").Vector((0, -1, 0))
        gx, _gy, gz = R.b2g(fwd)
        return R.heading_of_dir(gx, gz)
    assert abs(((heading(st[f], 3.6) - 180.0 + 180) % 360) - 180) < 0.1
    at(R.frame_of(6.0))
    ta = gpos(st[a])
    pf = gpos(st[f])
    toward = R.heading_of_dir(ta[0] - pf[0], ta[2] - pf[2])
    h180 = heading(st[f], 4.4)
    delta = abs(((toward - h180 + 180) % 360) - 180)
    want = toward if delta > 60.0 else h180            # GroupLookAtGroup turns the body only when the target is more than 60 degrees away
    assert abs(((heading(st[f], 6.0) - want + 180) % 360) - 180) < 0.2, (heading(st[f], 6.0), want, delta)
    # --- motion variable and animation number
    at(R.frame_of(2.4))
    assert st[a]["mv_moving"] == 0 or st[a]["mv_moving"] == 1
    at(R.frame_of(2.7))
    assert st[a]["mv_moving"] == 1.0 and st[a]["replay_anim"] == 1 and st[a]["replay_anim_names"] == ["walk"]
    # --- move animation: the stand-in is on the path at t = 10 and at its end
    objs = MC.key_objects(moves[0])
    start_key = MC.b2g(MC._loc(objs[0]))
    end_key = MC.b2g(MC._loc(objs[-1]))
    at(R.frame_of(10.0))
    here = gpos(st[f])
    at(R.frame_of(10.1) + 1)                                   # after the blend in (0.1 s): at the path's start
    assert near(gpos(st[f]), start_key, 3.0), (gpos(st[f]), start_key, here)
    at(R.frame_of(10.0 + 0.0) + 100000)
    assert near(gpos(st[f]), end_key, 1e-2)
    # --- the camera chain and its markers
    cam = R.camera_of(coll)
    assert cam is not None and sc.camera is cam
    assert cam.matrix_world.determinant() < 0, "the replay camera shows the game's picture (flipped left to right)"
    at(R.frame_of(0.0))
    cobjs = MC.key_objects(cams[0])
    k0 = MC.b2g(MC._loc(cobjs[0]))
    assert near(R.b2g(cam.matrix_world.translation), k0, 1e-2), (R.b2g(cam.matrix_world.translation), k0)
    at(R.frame_of(3.0))
    p3 = cam.matrix_world.translation.copy()
    at(R.frame_of(3.9))
    assert (cam.matrix_world.translation - p3).length < 1e-3, "paused: the camera holds still"
    at(R.frame_of(5.0))
    assert (cam.matrix_world.translation - p3).length > 1e-3, "resumed: the camera moves on"
    marks = [(m.frame, m.name, m.camera) for m in sc.timeline_markers if m.name.startswith("Replay: ")]
    frames = [m[0] for m in marks]
    assert frames == sorted(frames), marks
    names = [m[1] for m in marks]
    cam_marks = [n for n in names if n.startswith("Replay: camera")]
    c0, c1 = int(cams[0]["nis_id"]), int(cams[1]["nis_id"])
    assert cam_marks == [f"Replay: camera play {c0}", f"Replay: camera pause {c0}", f"Replay: camera resume {c0}", f"Replay: camera cut {c1}",
                         f"Replay: camera stop {c1}"], cam_marks
    assert all(m[2] is cam for m in marks if m[1].startswith("Replay: camera") or m[1] == "Replay: start")
    # the scene camera is the replay camera at every frame (a single camera chains all the animations)
    # --- the fade plane
    fade = next(o for o in coll.objects if o.get(R.K_PART) == "fade")
    at(R.frame_of(1.0))
    a0 = fade.color[3]
    at(R.frame_of(2.0))
    a1 = fade.color[3]
    at(R.frame_of(3.0))
    a2 = fade.color[3]
    assert abs(a0 - 1.0) < 1e-3 and abs(a1 - 0.5) < 0.02 and abs(a2) < 1e-3, (a0, a1, a2)
    # --- rows
    rows = R.event_text(top).as_string()
    assert "skipped" in rows and "approximate" in rows and "exact" in rows and "Press Space" in rows
    q = rep["quality"]
    assert q["skipped"] == 3 and q["approximate"] == 1 and sum(q.values()) == len(tl.events), q
    # --- originals hidden while shown
    for eid in (a, b, c, d, e, f):
        assert placed[eid].hide_get(), eid
    other = [o for k, o in placed.items() if k not in (a, b, c, d, e, f)][0]
    assert not other.hide_get()
    # --- clear
    assert R.clear_replay(bpy.context, top) and R.replay_of(top) is None
    for eid in (a, b, c, d, e, f):
        assert not placed[eid].hide_get(), eid
    assert (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera) == before
    assert not [m for m in sc.timeline_markers if m.name.startswith("Replay: ")]
    assert not [o for o in bpy.data.objects if o.get(R.K_PART)], "no replay object is left"
    assert not R.clear_replay(bpy.context, top)
    print("   stand-ins, walk, step, visibility, turns, keyed variables, move animation, camera chain, markers, fade, hide / restore: all as the timeline says", flush=True)
    return top


def test_clips(game_dir):
    """An ``anim`` event plays the model's own clip on the stand-in: the lab (a scene model with PowerDown / PowerUp / Idle1)."""
    from blender_native.sceneobj import map_replay as R
    top = import_map(MISSION1, load_models=True)
    placed = R.placed_objects(top)
    lab = next(eid for eid, o in placed.items() if "LAB.EBP" in str(o.get("ic_blueprint", "")).upper())
    other = next(eid for eid, o in placed.items() if "ARCTIC_VILLAGERM" in str(o.get("ic_blueprint", "")).upper() and o.instance_collection is not None)
    tl = TL(8.0, [Ev(1.0, "anim", [lab], {"name": "PowerDown", "loop": False}), Ev(5.0, "anim", [lab], {"name": "idle1", "loop": True}),
                  Ev(2.0, "anim", [other], {"name": "NoSuchClip"}), Ev(3.0, "despawn", [lab]), Ev(4.0, "respawn", [lab])])
    t0 = time.perf_counter()
    rep = R.build_replay(bpy.context, top, "clips", timeline=tl, title="Clips", game_dir=game_dir)
    dt = time.perf_counter() - t0
    assert rep["clips"] == 2 and rep["clip_stand_ins"] == 1, rep
    assert any("NoSuchClip" in n for n in rep["notes"]), rep["notes"]
    coll = R.replay_of(top)
    st = R.standins(coll)[lab]
    parts = [o for o in coll.objects if o.get(R.K_PART) == "standin_part"]
    arm = next(o for o in parts if o.type == "ARMATURE")
    def under(o, anc):
        while o is not None:
            if o is anc:
                return True
            o = o.parent
        return False
    assert st.instance_collection is None and all(under(o, st) for o in parts)
    assert [t.name for t in arm.animation_data.nla_tracks] == ["PowerDown 1.0", "idle1 5.0"], [t.name for t in arm.animation_data.nla_tracks]
    # the pose changes while PowerDown plays (1 s .. 7 s of 180 frames) and the clip is the same one the model has
    def pose_sum():
        bpy.context.view_layer.update()
        return sum(sum(abs(v) for v in pb.matrix.to_translation()) + sum(abs(x) for x in pb.rotation_quaternion) for pb in arm.pose.bones)
    bpy.context.scene.frame_set(R.frame_of(0.5))
    p0 = pose_sum()
    bpy.context.scene.frame_set(R.frame_of(1.0) + 60)
    p1 = pose_sum()
    bpy.context.scene.frame_set(R.frame_of(1.0) + 61)
    p1b = pose_sum()
    assert abs(p1 - p0) > 1e-6 and abs(p1b - p1) > 1e-9, (p0, p1, p1b)
    # the parts follow the stand-in's visibility
    for t, want in ((2.5, False), (3.5, True), (4.5, False)):
        bpy.context.scene.frame_set(R.frame_of(t))
        assert all(bool(o.hide_viewport) == want for o in parts + [st]), (t, want)
    R.clear_replay(bpy.context, top)
    assert not [a for a in bpy.data.actions if a.get("ic_replay")], "the baked clips are removed with the replay"
    assert not [o for o in bpy.data.objects if o.get(R.K_PART)]
    print(f"   clips: the lab plays PowerDown then Idle1 on a copy of its model ({rep['clip_seconds']:.2f} s of the {dt:.2f} s build), the parts follow its visibility, "
          "a clip the model lacks is said so", flush=True)


def test_nis(game_dir):
    """NIS start / end: the animated stand-in and its camera are built by map_nis and laid at the event's time."""
    from blender_native.sceneobj import map_replay as R
    try:
        from blender_native.sceneobj import map_nis  # noqa: F401
    except ImportError:
        C.skip("sceneobj.map_nis is not installed: the NIS part of the replay was not tested")
        return
    top = import_map(MISSION1)
    placed = R.placed_objects(top)
    nis = next((eid for eid, o in placed.items() if "ART\\EBPS\\NIS\\" in str(o.get("ic_blueprint", "")).upper()), None)
    if nis is None:
        C.skip("mission1 places no NIS object")
        return
    a = sorted(placed)[0]
    census0 = {n: len(getattr(bpy.data, n)) for n in ("objects", "meshes", "materials", "images", "actions", "cameras", "collections", "texts", "armatures")}
    tl = TL(12.0, [Ev(2.0, "nis", [nis], {"action": "start", "transition_s": 0.0}), Ev(2.0, "anim_camera", [nis], {"action": "use"}),
                   Ev(6.0, "anim_camera", [nis], {"action": "done"}), Ev(6.0, "nis", [nis], {"action": "end"}),
                   Ev(1.0, "teleport", [a], {"pos": [10.0, 0.0, 10.0]})])
    t0 = time.perf_counter()
    rep = R.build_replay(bpy.context, top, "nis", timeline=tl, title="NIS", game_dir=game_dir)
    dt = time.perf_counter() - t0
    assert rep["nis"] == 1, (rep, R.event_text(top).as_string())
    coll = R.replay_of(top)
    parts = [o for o in coll.all_objects if o.get(R.K_PART) == "nis"]
    cam = next(o for o in parts if o.type == "CAMERA")
    body = [o for o in parts if o.type != "CAMERA"]
    assert body and cam.animation_data is not None
    sc = bpy.context.scene
    def hid(t):
        sc.frame_set(R.frame_of(t))
        return {bool(o.hide_viewport) for o in body}
    assert hid(1.0) == {True} and False in hid(3.0) and hid(6.5) == {True}        # (while it plays, a part the clips hide stays hidden)
    marks = {m.name: m for m in sc.timeline_markers if m.name.startswith("Replay: ")}
    use, done = marks["Replay: NIS start %d" % nis], marks["Replay: NIS end %d" % nis]
    assert use.camera is cam and use.frame == R.frame_of(2.0) and done.frame == R.frame_of(6.0)
    sc.frame_set(R.frame_of(3.0))
    assert sc.camera is cam, "the scene camera is the NIS camera while the NIS plays"
    fcs = R._fcurves_of(cam)
    first = min(kp.co[0] for fc in fcs for kp in fc.keyframe_points)
    assert first >= R.frame_of(2.0) - 1e-6, first
    assert placed[nis].hide_get(), "the placed NIS object is hidden while the replay shows"
    assert any(r.kind.startswith("nis_") for r in R_rows(top)), "the scene's own events are listed"
    R.clear_replay(bpy.context, top)
    assert not [o for o in bpy.data.objects if o.get(R.K_PART)] and not [c for c in bpy.data.collections if c.get("ic_nis_standin")], "no NIS object is left"
    assert census0 == {n: len(getattr(bpy.data, n)) for n in census0}, {n: (census0[n], len(getattr(bpy.data, n))) for n in census0 if census0[n] != len(getattr(bpy.data, n))}
    assert not placed[nis].hide_get()
    print(f"   NIS: {len(body)} parts + camera laid at 2.0 s, shown 2 s to 6 s, markers bound, {dt:.2f} s", flush=True)


def R_rows(top):
    from blender_native.sceneobj import map_replay as R
    text = R.event_text(top).as_string().splitlines()
    return [type("Row", (), {"kind": "nis_x" if "inside " in ln else "x"}) for ln in text]


def test_heading_convention(game_dir):
    """The timeline's heading (radians, ``cine_timeline.heading_of``: the unit looks along (sin h, cos h)) is the replay's heading of the
    placed object's own orientation: for every placed object of mission1, the replay's base heading is minus the timeline's, in degrees."""
    from ICParse.sgb import cine_timeline as CT
    from blender_native.sceneobj import map_replay as R
    top = import_map(MISSION1)
    board, _side = (lambda: R.inputs_of(top) or (R.cinematics(bpy.context, top) and R.inputs_of(top)))()
    placed = R.placed_objects(top)
    worst, n, off = 0.0, 0, 0
    for e in board.entities:
        o = placed.get(int(e.instance_id))
        if o is None or len(e.rotation) != 9:
            continue
        theirs = -math.degrees(CT.heading_of(e.rotation))
        _loc, rot, _s = o.matrix_world.decompose()
        mine = R.base_heading(rot.to_matrix())
        if abs(e.rotation[0]) + abs(e.rotation[2]) < 1e-6 or abs(math.sin(math.radians(mine))) + abs(math.cos(math.radians(mine))) < 1e-9:
            continue
        diff = abs(((theirs - mine + 180.0) % 360.0) - 180.0)
        worst = max(worst, diff)
        off += diff > 0.05
        n += 1
    # (a slightly tilted object differs a little: the timeline takes the first row of the matrix, the replay the direction the model faces)
    assert n > 100 and off <= 0.1 * n and worst < 5.0, (n, off, worst)
    print(f"   heading convention: the replay's heading of {n} placed objects is the timeline's ({n - off} within 0.05 degrees, worst {worst:.2f} degrees on a tilted one)", flush=True)


def test_identity(game_dir):
    from blender_native.sceneobj import map_replay as R
    for key in (MISSION1, MISSION15):
        top = import_map(key)
        base = save(top, C.tmpdir("rp_base_"))
        ids, _placed = pick_entities(top, 3)
        a, b, c = ids
        tl = TL(5.0, [Ev(0.5, "teleport", [a], {"pos": [50.0, 0.0, 60.0], "heading": 10}), Ev(1.0, "walk", [b], {"path": [[10, 10], [60, 60]], "speed": 5.0}),
                      Ev(2.0, "despawn", [c])])
        R.build_replay(bpy.context, top, "x", timeline=tl, title="x", game_dir=game_dir)
        shown = save(top, C.tmpdir("rp_shown_"))
        R.clear_replay(bpy.context, top)
        cleared = save(top, C.tmpdir("rp_clear_"))
        assert shown == base and cleared == base, (key, {k: (len(v), len(base.get(k, b''))) for k, v in shown.items()})
        print(f"   {key.split('/')[-1]}: the save with a replay shown, and after clearing it, is byte for byte the save without one "
              f"({len(base)} files, {sum(len(v) for v in base.values())} bytes)", flush=True)


def verify(top, tl, rep):
    """Read the built replay back against the timeline: positions at chosen frames, visibility keys, markers in order, originals hidden."""
    from blender_native.sceneobj import map_replay as R
    coll = R.replay_of(top)
    st = R.standins(coll)
    placed = R.placed_objects(top)
    sc = bpy.context.scene
    two = 2.0 / R.FPS
    evs = sorted(((float(e.t), i, e) for i, e in enumerate(tl.events) if e.quality != "skipped"), key=lambda x: x[:2])
    by_ent = {}
    for t, _i, e in evs:
        if e.kind in ("teleport", "respawn", "walk", "move_anim", "obj_anim", "create", "despawn", "delete", "visible"):
            for k, eid in enumerate(e.entities):
                by_ent.setdefault(eid, []).append((t, e, k))
    checked = {"position": 0, "visible": 0, "hidden": 0, "walk_end": 0}
    for eid, rows in by_ent.items():
        if eid not in st:
            continue
        for n, (t, e, k) in enumerate(rows):
            nxt = rows[n + 1][0] if n + 1 < len(rows) else 1e9
            f = R.frame_of(t)
            if nxt - t <= two + 1e-9 or R.frame_of(nxt) - f <= 2:
                continue
            sc.frame_set(f)
            bpy.context.view_layer.update()
            o = st[eid]
            if e.kind == "teleport" and e.data.get("pos") is not None:
                pos = e.data["pos"]
                want = pos[k] if isinstance(pos[0], (list, tuple)) else pos
                assert near(gpos(o), want, 2e-3), (eid, t, gpos(o), want)
                checked["position"] += 1
            if e.kind in ("despawn", "delete"):
                assert o.hide_viewport and o.hide_render, (eid, t, e.kind)
                checked["hidden"] += 1
            if e.kind in ("respawn", "create") or (e.kind == "visible" and e.data.get("on", True)):
                assert not o.hide_viewport, (eid, t, e.kind)
                checked["visible"] += 1
            if e.kind == "visible" and not e.data.get("on", True):
                assert o.hide_viewport, (eid, t)
                checked["hidden"] += 1
            if e.kind == "walk" and e.data.get("path") and not e.data.get("stop") and e.data.get("t_end") is not None:
                tf = float(e.data["t_end"])
                fe = R.frame_of(tf)
                if tf > t and (nxt - tf) > two + 1e-9 and R.frame_of(nxt) - fe > 2:
                    sc.frame_set(fe)
                    bpy.context.view_layer.update()
                    end = e.data["path"][-1]
                    g = gpos(o)
                    assert math.dist((g[0], g[2]), (end[0], end[1])) < 0.05, (eid, tf, g, end)
                    checked["walk_end"] += 1
    marks = [m for m in sc.timeline_markers if m.name.startswith("Replay: ")]
    assert [m.frame for m in marks] == sorted(m.frame for m in marks), "the markers are in time order"
    cam = R.camera_of(coll)
    from blender_native.sceneobj import map_cinematics as MC
    root = MC.root_of(top)
    good = {int(c["nis_id"]) for c in MC.anim_collections(root) if c["nis_kind"] == "camera" and len(MC.key_objects(c)) >= 2}
    cam_events = [e for e in sorted(tl.events, key=lambda e: e.t) if e.kind == "camera" and e.quality != "skipped"
                  and e.data.get("action") in ("play", "cut", "pause", "resume", "stop", "hold", "path_end", "end")
                  and not (e.data.get("action") in ("play", "cut") and int(e.data.get("anim", -1)) not in good)]
    names = [m.name for m in marks if m.name.startswith("Replay: camera")]
    want = ["Replay: camera %s" % e.data["action"] + (" %d" % int(e.data["anim"]) if e.data.get("anim") is not None else "") for e in cam_events]
    assert names == want, (names[:6], want[:6])
    played = [e for e in cam_events if e.data.get("action") in ("play", "cut")]
    assert (cam is not None) == bool(played), (cam, len(played))
    if played:
        assert all(m.camera is cam for m in marks if m.name.startswith("Replay: camera") or m.name == "Replay: start")
    for eid in st:
        if eid in placed:
            assert placed[eid].hide_get(), eid
    assert sc.frame_end >= sc.frame_start + 1
    return checked


def test_identity_real(game_dir):
    """The same, with a real cinematic that has clips, an NIS scene and a camera, on a map opened with its models."""
    from blender_native.sceneobj import map_replay as R
    try:
        from ICParse.sgb import cine_timeline  # noqa: F401
    except ImportError:
        C.skip("ICParse.sgb.cine_timeline is not installed")
        return
    top = import_map(MISSION1, load_models=True)
    base = save(top, C.tmpdir("rp_rbase_"))
    cines = R.cinematics(bpy.context, top)
    keys = [R._g(c, "key") for c in cines if R._g(c, "key") in ("New_lab_intro", "Camera_Intro_Rex_Lucy")]
    assert len(keys) == 2, [R._g(c, "key") for c in cines]
    def census():
        d = bpy.data
        return {n: len(getattr(d, n)) for n in ("objects", "meshes", "materials", "images", "actions", "cameras", "collections", "texts", "armatures")}
    for key in keys:
        before = census()
        rep = R.build_replay(bpy.context, top, key)
        shown = save(top, C.tmpdir("rp_rshown_"))
        assert shown == base, key
        R.clear_replay(bpy.context, top)
        cleared = save(top, C.tmpdir("rp_rclear_"))
        assert cleared == base, key
        assert census() == before, (key, {k: (before[k], v) for k, v in census().items() if v != before[k]})   # nothing of the replay is left in the file
        print(f"   {key}: {rep['stand_ins']} stand-ins, {rep['clips']} clips on {rep['clip_stand_ins']} models (the build took {rep['seconds']:.2f} s), replay shown / cleared: the save is byte for byte the one without a replay", flush=True)


def test_real(game_dir):
    from blender_native.sceneobj import map_replay as R
    try:
        from ICParse.sgb import cine_timeline as CT
    except ImportError:
        C.skip("ICParse.sgb.cine_timeline is not installed: the real cinematics were not built")
        return
    total = 0
    sums = {"position": 0, "visible": 0, "hidden": 0, "walk_end": 0}
    slow = []
    nis_built = 0
    for key in [MISSION1] + OTHER_CAMPAIGN:
        top = import_map(key)
        t0 = time.perf_counter()
        cines = R.cinematics(bpy.context, top, refresh=True)
        print(f"   {key.split('/')[-1]}: {len(cines)} cinematics listed in {time.perf_counter() - t0:.2f} s", flush=True)
        assert cines
        board, side = R.inputs_of(top)
        sc = bpy.context.scene
        before = (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera)
        placed = R.placed_objects(top)
        for cine in cines:
            tl = CT.build(board, side, R._g(cine, "key"))
            t0 = time.perf_counter()
            rep = R.build_replay(bpy.context, top, R._g(cine, "key"), timeline=tl, title=R._g(cine, "title"))
            dt = time.perf_counter() - t0
            total += 1
            if R._g(cine, "nis"):
                nis_built += rep["nis"]
            got = verify(top, tl, rep)
            for k, v in got.items():
                sums[k] += v
            print(f"     {R._g(cine, 'title')!r}: {rep['stand_ins']} stand-ins, {rep['events']} events {rep['quality']}, {rep['keys']} keys, "
                  f"{rep['markers']} markers, {rep['clips']} clips, frames {rep['frames']}, built in {dt:.2f} s; read back {got}", flush=True)
            slow.append((dt, key.split("/")[-1], R._g(cine, "title")))
            R.clear_replay(bpy.context, top)
            assert (sc.render.fps, sc.render.fps_base, sc.frame_start, sc.frame_end, sc.camera) == before
            assert not any(o.hide_get() for o in placed.values()), "the originals are shown again"
            assert not [o for o in bpy.data.objects if o.get(R.K_PART)]
    slow.sort(reverse=True)
    assert nis_built >= 4, nis_built
    print(f"   {nis_built} NIS scenes built inside cinematics; {total} cinematics built and read back ({sums}); slowest {slow[0][0]:.2f} s ({slow[0][2]!r}), the sum {sum(x[0] for x in slow):.1f} s", flush=True)


def test_panel(game_dir):
    from blender_native.sceneobj import map_replay as R
    from blender_native.sceneobj import panels_map_replay as P
    try:
        from ICParse.sgb import cine_timeline  # noqa: F401
    except ImportError:
        C.skip("ICParse.sgb.cine_timeline is not installed: the panel was not driven")
        return
    top = import_map(MISSION1)
    assert bpy.types.BZ_PT_map_replay.bl_parent_id == "BZ_PT_map_cinematics" == P.CLASSES[0].bl_parent_id
    lay = C.FakeLayout()
    P.draw_replay(lay, bpy.context, top)
    ops = [d["idname"] for k, d in lay.calls if k == "operator"]
    assert ops == ["bz.replay_find"], ops
    assert "FINISHED" in bpy.ops.bz.replay_find()
    lay = C.FakeLayout()
    P.draw_replay(lay, bpy.context, top)
    ops = [d["idname"] for k, d in lay.calls if k == "operator"]
    texts = [d["text"] for k, d in lay.calls if k == "operator" and d["idname"] == "bz.replay_select"]
    assert len(texts) == 8 and "New lab intro" in texts and "bz.replay_build" in ops and "bz.replay_look" not in ops, (texts, ops)
    assert "FINISHED" in bpy.ops.bz.replay_select(key="New_lab_intro")
    assert "FINISHED" in bpy.ops.bz.replay_build() and R.replay_of(top) is not None
    # an edit made after Find is in the next replay: the files the timeline is read from are read again when it is built
    from blender_native.sceneobj import map_cinematics as MC
    na_before = R.inputs_of(top)[1]["mission1.na"]
    key0 = MC.key_objects(MC.anim_collections(MC.root_of(top))[0])[0]
    key0["nis_speed"] = float(key0["nis_speed"]) + 1.0
    assert R.inputs_of(top)[1]["mission1.na"] == na_before, "the kept inputs are only read again by a build"
    assert "FINISHED" in bpy.ops.bz.replay_build()
    assert R.inputs_of(top)[1]["mission1.na"] != na_before, "the replay was built from the cinematics file as it was at the last Find"
    key0["nis_speed"] = float(key0["nis_speed"]) - 1.0
    lay = C.FakeLayout()
    P.draw_replay(lay, bpy.context, top)
    ops = [d["idname"] for k, d in lay.calls if k == "operator"]
    labels = [d["text"] for k, d in lay.calls if k == "label"]
    assert "bz.replay_look" in ops and "bz.replay_events" in ops and any("exact" in x and "skipped" in x for x in labels), (ops, labels)
    assert any("Space" in x for x in labels), labels
    for idname in set(ops):
        head, _, tail = idname.partition(".")
        assert hasattr(getattr(bpy.ops, head), tail), idname
    assert "FINISHED" in bpy.ops.bz.replay_look() and bpy.context.scene.camera is R.camera_of(R.replay_of(top))
    assert "FINISHED" in bpy.ops.bz.replay_events()
    text = R.event_text(top)
    assert text is not None and text.name == "Replay New lab intro" and "LAB" in text.as_string()
    assert "FINISHED" in bpy.ops.bz.replay_clear() and R.replay_of(top) is None and R.event_text(top) is None
    print("   the Replay panel: Find, choose, Build, Look through the camera, the event list, Clear; every button is a real operator", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the replay test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("hand-made timelines")
        test_hand(game_dir)
        section("clips on a stand-in")
        test_clips(game_dir)
        section("an NIS scene")
        test_nis(game_dir)
        section("the heading convention")
        test_heading_convention(game_dir)
        section("the map is never changed")
        test_identity(game_dir)
        section("the map is never changed: real cinematics, models loaded")
        test_identity_real(game_dir)
        section("the real cinematics")
        test_real(game_dir)
        section("the panel")
        test_panel(game_dir)
    print("SO_MAP_REPLAY_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_replay", main)
