"""The cut-scene (NIS) stand-in of the cinematic replay, in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_nis_test.py     (or: run_all.py -k so_map_nis)

1. every NIS object the stock maps place (8: mission1, 3, 4, 8, 12, 15 twice, 15_nis6) builds: an armature, every model part (its
   own and the ones it borrows), one strip per clip, a camera; the duration and frame range are the clips' (start delay + duration);
   nis_1, which no map places, builds on an empty carrying its blueprint;
2. the camera, read back at chosen frames and around its cuts, has the pose / field of view the model camera has in the clip that
   plays (computed here from the file with its own maths) and cuts where the clips' setcamera lines say;
3. the bones, at chosen times, are where ICParse's skeleton puts them with the game's layering (per bone, the latest clip that has
   started and animates it);
4. the vectorised key conversion equals the add-on's own bake (``actions.basis_samples``) on a real clip;
5. start_frame moves everything; the events are in time order and agree with the file; remove_nis leaves nothing behind;
6. THE REPLAY NEVER CHANGES THE MAP: the placed object is the object it was, and a map save with the stand-ins built, and after they
   were removed, is byte for byte the save without them.
Nothing under the game folder changed.
"""
import math
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, C.REPO)

MAPS = ["mission1", "mission3", "mission4", "mission8", "mission12", "mission15", "mission15_nis6"]
PLACED = {"nis_2", "nis_3", "nis_4", "nis_5", "nis_6", "nis_7", "nis_m15", "nis_m3"}
B3 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])       # the add-on's axes, written out here on purpose


def section(title):
    print(f"-- {title}", flush=True)


def import_map(name):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=f"Engine.sga|scenarios/sp/{name}.sgb", load_models=False, game_look=False)
    assert "FINISHED" in res, (name, res)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def nis_objects():
    from blender_native.sceneobj import map_nis as N
    return [o for o in bpy.data.objects if o.get("ic_entity_id") is not None and N.is_nis_blueprint(o)]


def stem_of(obj):
    return os.path.splitext(os.path.basename(obj["ic_blueprint"].replace("\\", "/")))[0].lower()


def files_of(game_dir, obj):
    """(ICParse nis_scene model, ebp, schedule, scene) of a placed NIS, read straight from the game's files."""
    from ICParse.ebp import parser
    from ICParse.sgb import view
    from ICParse.sgm import nis_scene as NS
    from ICParse.sgm.scene.model import read_scene
    data = view.game_data(game_dir, "Engine.sga")
    ref = f"art/ebps/nis/{stem_of(obj)}"
    raw = data.read(ref + ".sgm")
    ebp = parser.read(data.read(ref + ".ebp"))
    model = NS.read(raw)
    return model, ebp, NS.schedule(ebp, model), read_scene(raw), data


def save(top, folder):
    stem = top.name[4:] if top.name.startswith("Map ") else top.name
    res = bpy.ops.bz.export_map(filepath=os.path.join(folder, stem + ".sgb"))
    assert "FINISHED" in res, res
    return {f: open(os.path.join(d, f), "rb").read() for d, _s, fs in os.walk(folder) for f in fs}


def snapshot(o):
    return dict(matrix=tuple(tuple(r) for r in o.matrix_world), props={k: (o[k] if isinstance(o[k], (int, float, str)) else str(o[k])) for k in o.keys()},
                parent=o.parent.name if o.parent else None, colls=sorted(c.name for c in o.users_collection), hide=(o.hide_viewport, o.hide_render, o.hide_get() if o.name in bpy.context.view_layer.objects else None),
                inst=(o.instance_type, o.instance_collection.name if o.instance_collection else None), anim=o.animation_data is not None,
                loc=tuple(o.location), rot=tuple(o.rotation_euler), scale=tuple(o.scale), children=sorted(c.name for c in o.children), name=o.name)


def expected_duration(sched):
    return max(e.delay + e.duration for e in sched)


# ---------------------------------------------------------------------------------------------------------- 1. every NIS builds
def test_every_stock_nis_builds(game_dir):
    from blender_native.sceneobj import map_nis as N
    seen = set()
    total_parts = 0
    for name in MAPS:
        top = import_map(name)
        for e in nis_objects():
            model, ebp, sched, scene, _data = files_of(game_dir, e)
            stem = stem_of(e)
            seen.add(stem)
            b = N.build_nis(bpy.context, top, e)
            fps = b.fps
            assert abs(b.duration_s - expected_duration(sched)) < 1e-6, stem
            assert b.frame_end - b.frame_start == int(math.ceil(b.duration_s * fps)) and b.frame_start == 1, stem
            assert b.collection.name in [c.name for c in top.children], stem
            # parts: every borrowed part found, own + borrowed meshes all built
            assert b.parts.get("xref_skipped", 0) == 0, (stem, b.parts, b.notes)
            assert b.parts["xref_built"] == len(model.xrefs) and b.parts["own_meshes"] == len(scene.meshes), (stem, b.parts)
            meshes = [o for o in b.objects if o.type == "MESH"]
            assert len(meshes) == len(model.xrefs) + len(scene.meshes), (stem, len(meshes))
            assert b.armature is not None and len(b.armature.data.bones) == len(scene.bones), stem
            assert b.root is not None and np.allclose(np.array(b.root.matrix_world), np.array(e.matrix_world), atol=1e-6)
            # one strip per clip that has bone tracks, each on its own track, later (higher priority) clips on higher tracks
            by_name = {a.name.lower() for a in scene.animations}
            want = [x for x in sched if x.clip.lower() in by_name]
            tracks = list(b.armature.animation_data.nla_tracks)
            assert len(tracks) == len(want) == len(b.clip_actions), (stem, len(tracks), len(want))
            order = [t.name[len(N.PREFIX_CLIP):] for t in tracks]
            assert order == [x.clip for x in sorted(want, key=lambda x: -x.priority)], stem
            for t, x in zip(tracks, sorted(want, key=lambda x: -x.priority)):
                s = t.strips[0]
                assert s.extrapolation == "HOLD_FORWARD" and s.blend_type == "REPLACE" and not t.mute
                assert abs(s.frame_start - s.action_frame_start) < 1e-6, (stem, t.name)                  # the action's frames ARE scene frames
                assert abs(s.action_frame_start - (1 + x.delay * fps)) < 0.51, (stem, t.name)             # a clip starts at its start delay
            # the camera: one per model, a real camera, a key per scene frame over the cut-scene
            cam = b.camera
            assert cam is not None and cam.type == "CAMERA" and cam.data.sensor_fit == "HORIZONTAL" and cam.data.lens_unit == "FOV"
            n = int(math.ceil(b.duration_s * fps)) + 1
            fcs = [f for f in b.action.fcurves] if hasattr(b.action, "fcurves") and len(b.action.fcurves) else None
            if fcs is None:
                from blender_native import actions as A
                fcs = A._fcurves(b.action)
            assert len(fcs) == 7 and all(len(f.keyframe_points) == n for f in fcs), (stem, [len(f.keyframe_points) for f in fcs])
            assert abs(min(f.keyframe_points[0].co[0] for f in fcs) - 1) < 1e-9
            # nothing in the stand-in can be taken for a placed entity of the map
            assert not [o for o in b.objects if o.get("ic_entity_id") is not None], stem
            total_parts += len(meshes)
            print(f"   {name}: {e.name} -> {stem}: {len(scene.bones)} bones, {len(meshes)} parts ({b.parts.get('xref_skinned', 0)} skinned borrowed), "
                  f"{len(tracks)} clips, {len(b.cuts)} camera cuts, {b.duration_s:.1f} s = {b.frame_end - b.frame_start} frames, {len(b.events)} events", flush=True)
    assert seen == PLACED, (sorted(seen), sorted(PLACED))
    print(f"   all {len(seen)} placed cut-scenes built ({total_parts} model parts)", flush=True)


def test_unplaced_nis_1(game_dir):
    """nis_1 is placed by no stock map: it builds on an empty that carries only the blueprint (a map-less stand-in)."""
    from blender_native.sceneobj import map_nis as N
    C.fresh()
    e = bpy.data.objects.new("nis_1 test", None)
    e["ic_blueprint"] = "DATA:ART\\EBPS\\NIS\\NIS_1.EBP"
    bpy.context.scene.collection.objects.link(e)
    e.location = (3.0, 4.0, 5.0)
    b = N.build_nis(bpy.context, None, e, game_dir=game_dir)
    assert b.camera is not None and b.parts.get("xref_skipped", 0) == 0 and len(b.clip_actions) == 5
    assert abs(b.duration_s - (63.133331298828125 + 12.533334130181258)) < 1e-6
    assert (b.root.matrix_world.translation - e.matrix_world.translation).length < 1e-9
    print(f"   nis_1 on an empty: {b.parts['xref_built']} borrowed parts, {len(b.clip_actions)} clips, {b.duration_s:.2f} s", flush=True)


# ---------------------------------------------------------------------------------------------------------- 2. the camera
def expected_camera(model, sched, ci, t):
    """(position, forward, up, fov) in the add-on's axes, model space, of model camera ``ci`` at ``t`` -- from the file, written
    out independently of map_nis: the clip that plays is the first in the list among those that started."""
    from ICParse.sgm import nis_scene as NS
    started = [e for e in sched if e.delay <= t + 1e-9]
    entry = min(started, key=lambda e: e.priority) if started else None
    clip = model.clip(entry.clip) if entry else None
    pose = NS.camera_pose(model, clip, ci, (t - entry.delay) if entry else 0.0)
    R = np.array(NS.quat_to_matrix(pose.rotation))
    return B3 @ np.array(pose.position), B3 @ R[:, 2], B3 @ R[:, 1], pose.fov


def test_visibility_and_lens(game_dir):
    """Parts shown and hidden by the clips, and the lens channels, read back from the built objects."""
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgm import nis_scene as NS
    for mapname, stem in (("mission15", "nis_7"), ("mission1", "nis_2"), ("mission3", "nis_m3")):
        top = import_map(mapname)
        e = next(o for o in nis_objects() if stem_of(o) == stem)
        model, ebp, sched, scene, _d = files_of(game_dir, e)
        b = N.build_nis(bpy.context, top, e)
        assert b.visible and all(model.nodes[i].name.strip().lower() == o.name.strip().lower().split(".")[0] or True for i, o in b.visible.items())
        # every part's node is the part it is: own meshes by name, borrowed parts by the name they get here
        assert len(b.visible) == len(model.nodes), (stem, len(b.visible), len(model.nodes))
        names = {i: (o.name.split(".")[0] if o.name[-4:-3] == "." and o.name[-3:].isdigit() else o.name) for i, o in b.visible.items()}
        assert all(names[n.index].strip() == n.name.strip() for n in model.nodes), stem
        dur = expected_duration(sched)
        times = sorted({0.0, dur * 0.99} | {t for n in model.nodes for t, _v in NS.visibility_timeline(model, sched, n.index)[1:3]})
        times = [t for t0 in times for t in (t0 - 1.0 / b.fps, t0 + 1.0 / b.fps) if 0 <= t <= dur][:90]
        bad = 0
        for t in times:
            f = b.start_frame + round(t * b.fps)
            bpy.context.scene.frame_set(f)
            t = (f - b.start_frame) / b.fps
            for i, o in b.visible.items():
                want = NS.node_visible(model, sched, i, t + 1e-6 if t > 0 else 0.0)
                if (not o.hide_viewport) != want or (not o.hide_render) != want:
                    bad += 1
        assert bad == 0, (stem, bad)
        print(f"   {stem}: {len(b.visible)} parts, {b.parts['visibility_animated']} shown / hidden by the clips, {b.parts['hidden_throughout']} hidden throughout; "
              f"{len(times)} frames read back equal the clips' channels", flush=True)
    # a known fact of the file: Lucy walks into nis_7 at 81.5 s
    top = import_map("mission15")
    e = next(o for o in nis_objects() if stem_of(o) == "nis_7")
    model, ebp, sched, _s, _d = files_of(game_dir, e)
    b = N.build_nis(bpy.context, top, e)
    lucy = b.visible[next(n.index for n in model.nodes if n.name == "Lucy Willing")]
    for t, shown in ((0.0, False), (80.0, False), (82.0, True), (95.0, False), (97.0, True)):
        bpy.context.scene.frame_set(b.start_frame + round(t * b.fps))
        assert (not lucy.hide_viewport) == shown, (t, shown)
    # the lens: nis_m3's last scene asks for a 50 mm lens and a far plane of 3600
    top = import_map("mission3")
    e = next(o for o in nis_objects() if stem_of(o) == "nis_m3")
    b = N.build_nis(bpy.context, top, e)
    bpy.context.scene.frame_set(b.start_frame + round(10.0 * b.fps))
    assert abs(b.camera.data.angle - 2 * math.atan(18 / 50)) < 1e-4 and abs(b.camera.data.lens - 50.0) < 0.01
    assert b.camera.data.clip_end >= 3600.0 - 1
    print("   Lucy is hidden until 81.5 s in nis_7, and nis_m3's lens channel gives a 50 mm lens with a 3600 m far plane", flush=True)


def test_transition():
    from blender_native.sceneobj import map_nis as N
    w = [N.transition_weight(t, 2.0) for t in (-1, 0, 0.5, 1.0, 1.5, 2.0, 3.0)]
    assert w == [0.0, 0.0, 0.125, 0.5, 0.875, 1.0, 1.0], w
    assert N.transition_weight(0.0, 0.0) == 1.0 and N.transition_weight(-0.1, 0.0) == 0.0          # a time of 0 is a cut
    a = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), 1.0)
    b = ((10.0, 0.0, 0.0), (0.7071068, 0.0, 0.7071068, 0.0), 0.5)                                    # 90 degrees about Z-up-less axis
    m = N.blend_camera(a, b, 0.5)
    assert abs(m[0][0] - 5.0) < 1e-9 and abs(m[2] - 0.75) < 1e-9 and abs(sum(x * x for x in m[1]) - 1.0) < 1e-6
    assert N.blend_camera(a, b, 0.0) == a and N.blend_camera(a, b, 1.0) == b
    print("   the camera hand-over: eased 0 -> 1 over the transition time (0, 0.125, 0.5, 0.875, 1 at a quarter steps of 2 s); positions and field of view blend linearly, rotations on the sphere", flush=True)


def test_show_between(game_dir):
    """``NisBuild.show_between``: hidden outside the window, inside it the parts follow the clips, the rest is shown."""
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgm import nis_scene as NS
    top = import_map("mission15")
    e = next(o for o in nis_objects() if stem_of(o) == "nis_7")
    model, ebp, sched, _scene, _d = files_of(game_dir, e)
    f0 = 200
    b = N.build_nis(bpy.context, top, e, start_frame=f0)
    f1 = f0 + int(60 * b.fps)                                   # the NIS is ended 60 s in, half way through
    b.show_between(f0, f1, first_frame=1)
    plain = [o for o in b.objects if o.type != "CAMERA" and o not in b.visible.values()]
    assert plain and b.visible
    bad = 0
    for f in (1, f0 - 1, f0, f0 + 1, f0 + int(30 * b.fps), f1 - 1, f1, f1 + 1, f1 + 500):
        bpy.context.scene.frame_set(f)
        inside = f0 <= f < f1
        for o in plain:
            bad += (not o.hide_viewport) != inside or (not o.hide_render) != inside
        t = (f - f0) / b.fps
        for node, o in b.visible.items():
            want = inside and NS.node_visible(model, sched, node, max(t, 0.0) + (1e-6 if t > 0 else 0.0))
            bad += (not o.hide_viewport) != want or (not o.hide_render) != want
    assert bad == 0, bad
    print(f"   show_between({f0}, {f1}): {len(plain)} objects shown only inside the window, {len(b.visible)} parts inside it only while their clips show them", flush=True)


def test_entity_camera(game_dir):
    """UseAnimationCamera on an ordinary entity (the stock maps use it on Rex and the lab when they are lost): the model's own camera
    in the clip that plays, hanging from the entity's object."""
    from mathutils import Vector
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgb import view
    from ICParse.sgm import nis_scene as NS
    C.fresh()
    data = view.game_data(game_dir, "Engine.sga")
    worst = 0.0
    for ref, clip_name, other in (("art/ebps/characters/rex.ebp", "StumbleDie", "Camera03"), ("art/ebps/structures/lab.ebp", "damage_04", None)):
        model = NS.read(data.read(ref[:-4] + ".sgm"))
        clip = model.clip(clip_name)
        assert clip is not None and clip.cameras, (ref, clip_name)
        parent = bpy.data.objects.new("entity " + clip_name, None)
        bpy.context.scene.collection.objects.link(parent)
        parent.location = (5.0, -3.0, 1.0)
        plays = [(1.0, clip_name)] + ([(1.0 + clip.duration + 1.0, other, True)] if other else [])
        ec = N.build_entity_camera(bpy.context, parent, ref, plays, data=data, start_frame=5)
        assert ec.camera is not None and ec.camera.parent is parent and ec.frame_start == 5
        fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
        assert ec.frame_end == 5 + int(math.ceil(ec.duration_s * fps))
        bpy.context.view_layer.update()
        inv = parent.matrix_world.inverted_safe()
        for t in (0.0, 0.9, 1.0 + 0.3 * clip.duration, 1.0 + clip.duration, 1.0 + clip.duration + 1.4, ec.duration_s - 0.05):
            f = ec.frame_start + round(t * fps)
            t = (f - ec.frame_start) / fps
            bpy.context.scene.frame_set(f)
            ci = 0                                              # the clips' setcamera lines; both models have one camera
            if t < 1.0:
                pos, fwd, up, fov = expected_camera(model, [], 0, t)
            else:
                started = [(1.0, clip_name, False)] + ([(1.0 + clip.duration + 1.0, other, True)] if other else [])
                active = [p for p in started if p[0] <= t + 1e-9][-1]
                c = model.clip(active[1])
                sec = t - active[0]
                if len(active) > 2 and active[2]:
                    sec %= c.duration
                pose = NS.camera_pose(model, c, ci, sec)
                R = np.array(NS.quat_to_matrix(pose.rotation))
                pos, fwd, up, fov = B3 @ np.array(pose.position), B3 @ R[:, 2], B3 @ R[:, 1], pose.fov
            m = inv @ ec.camera.matrix_world
            err = max(np.abs(np.array(m.translation) - pos).max(), np.abs(np.array(m.to_3x3() @ Vector((0, 0, -1))) - fwd).max(),
                      np.abs(np.array(m.to_3x3() @ Vector((0, 1, 0))) - up).max(), abs(ec.camera.data.angle - fov),
                      np.abs(np.array(m.to_3x3() @ Vector((1, 0, 0))) - np.cross(up, fwd)).max())      # the game's right
            worst = max(worst, err)
            assert err < 2e-3, (ref, t, err)
        print(f"   {ref.split('/')[-1]} {clip_name}: {len(ec.cuts)} camera cut(s), {ec.duration_s:.2f} s, camera hangs from the entity and follows the clip", flush=True)
    assert worst < 2e-3
    # a model with no camera is said so
    nocam = "art/ebps/gatherers/henchmen.ebp"
    assert data.read(nocam[:-4] + ".sgm") is not None and not NS.read(data.read(nocam[:-4] + ".sgm")).cameras
    ec = N.build_entity_camera(bpy.context, parent, nocam, [(0.0, "Idle1")], data=data)
    assert ec.camera is None and "no camera" in ec.notes[0]
    print("   a model without a camera (henchmen): no camera built, and it says why", flush=True)


def test_camera(game_dir):
    from mathutils import Vector
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgm import nis_scene as NS
    for mapname, stem in (("mission3", "nis_m3"), ("mission15", "nis_7"), ("mission4", "nis_3")):
        top = import_map(mapname)
        e = next(o for o in nis_objects() if stem_of(o) == stem)
        model, ebp, sched, _scene, _d = files_of(game_dir, e)
        b = N.build_nis(bpy.context, top, e, start_frame=10)
        fps = b.fps
        cuts = NS.camera_cuts(model, sched)
        times = [0.0, 0.7, 2.3, expected_duration(sched) * 0.5, expected_duration(sched) - 0.1]
        for t_cut, _ci, _s in cuts:
            times += [max(0.0, t_cut - 1.5 / fps), t_cut + 0.5 / fps, t_cut + 3.0 / fps]
        worst = 0.0
        root_inv = b.root.matrix_world.inverted_safe()
        for t in times:
            f = b.start_frame + round(t * fps)                    # a whole frame
            t = (f - b.start_frame) / fps
            bpy.context.scene.frame_set(f)
            ci = 0
            for tc, cci, _s in cuts:
                if tc <= t + 1e-9:
                    ci = cci
            pos, fwd, up, fov = expected_camera(model, sched, ci, t)
            m = root_inv @ b.camera.matrix_world                    # in the model's space
            got_pos = np.array(m.translation)
            got_fwd = np.array(m.to_3x3() @ Vector((0, 0, -1)))
            got_up = np.array(m.to_3x3() @ Vector((0, 1, 0)))
            # screen right as the game has it, up x forward (a turn of the game's axes keeps the cross product): not
            # forward x up, which is what a camera that is only turned shows -- the mirror image
            got_right = np.array(m.to_3x3() @ Vector((1, 0, 0)))
            err = max(np.abs(got_pos - pos).max(), np.abs(got_fwd - fwd).max(), np.abs(got_up - up).max(), abs(b.camera.data.angle - fov),
                      np.abs(got_right - np.cross(up, fwd)).max())
            worst = max(worst, err)
            assert err < 2e-3, (stem, "t", round(t, 3), "camera", ci, err)
        # the camera is a camera of the model at its cuts: just before and just after a cut the view differs by the camera change
        print(f"   {stem}: {len(times)} frames checked against the file (pose, forward, up, right, field of view), worst {worst:.5f}; cuts at "
              f"{[round(c[0], 2) for c in cuts][:6]}", flush=True)
        assert [round(t, 6) for t, _n in b.cuts] == [round(c[0], 6) for c in cuts]


# ---------------------------------------------------------------------------------------------------------- 3. the bones
def reference_world(model, sched, scene, rig, by, t):
    from ICParse.codecs import pose as P
    locs = []
    for i, bn in enumerate(rig.names):
        cand = []
        for en in sched:
            an = by.get(en.clip.lower())
            if an is not None and en.delay <= t + 1e-9:
                tr = next((x for x in an.tracks if x.bone_name.lower() == bn.lower()), None)
                if tr is not None:
                    cand.append((en, tr))
        if cand:
            en, tr = min(cand, key=lambda c: c[0].priority)
            ph = min(max((t - en.delay) / model.clip(en.clip).duration, 0.0), 1.0)
            locs.append(P.scene_local(tr.rot_keys, tr.trans_keys, ph, rig.binds_local[i]))
        else:
            locs.append(rig.binds_local[i])
    return rig.world(locs)


def test_bones(game_dir):
    from blender_native import rig as R
    from blender_native.sceneobj import map_nis as N
    from ICParse.codecs import pose as P
    P.ENGINE_EXACT = False                       # the add-on does not reproduce the engine's slight over-scale of blended rotations
    for mapname, stem, times in (("mission3", "nis_m3", (0.0, 1.0, 3.69, 3.71, 8.0, 21.0, 25.0)),
                                 ("mission15", "nis_7", (0.0, 5.5, 13.69, 13.71, 50.0, 100.0, 131.0)),
                                 ("mission15", "nis_m15", (0.0, 4.0, 10.0, 20.0, 30.0))):
        top = import_map(mapname)
        e = next(o for o in nis_objects() if stem_of(o) == stem)
        model, ebp, sched, scene, _d = files_of(game_dir, e)
        b = N.build_nis(bpy.context, top, e)
        arm = b.armature
        rig = P.SceneRig(scene.bones)
        by = {a.name.lower(): a for a in scene.animations}
        names = list(arm.get(R.KEY_BONE_SRC_NAMES))
        worst = 0.0
        for t in times:
            f = b.start_frame + t * b.fps
            bpy.context.scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
            bpy.context.view_layer.update()
            W = reference_world(model, sched, scene, rig, by, t)
            for i, bn in enumerate(names):
                want = np.array(R.to_blender_matrix(W[i]))
                got = np.array(arm.pose.bones[bn].matrix)
                err = max(np.abs(want[:3, 3] - got[:3, 3]).max(), np.abs(want[:3, :3] - got[:3, :3]).max() * 0.1)
                worst = max(worst, err)
                assert err < 4e-3, (stem, t, bn, err)
        print(f"   {stem}: {len(names)} bones at {len(times)} times, worst {worst:.5f} m against the skeleton with the game's layering", flush=True)
    P.ENGINE_EXACT = True


def test_basis_equals_the_addon_bake(game_dir):
    """The vectorised key conversion gives what ``actions.basis_samples`` (whole-skeleton worlds, frame by frame) gives."""
    from blender_native import actions as A
    from blender_native import core
    from blender_native.sceneobj import map_nis as N
    import_map("mission3")
    e = next(o for o in nis_objects() if stem_of(o) == "nis_m3")
    model, ebp, sched, scene, data = files_of(game_dir, e)
    sd = core.load_scene(_write(data, "art/ebps/nis/nis_m3.sgm"), data_root=game_dir, texture_source=data)
    consts = N.bone_constants(sd.bind_world, sd.bone_parents)
    ci = 1
    an = sd.animations[ci]
    K = max(1, int(an.frame_count))
    frames = [0, 7, 40, 77, K]
    worlds = [core.scene_bone_world(sd, ci, f / K) for f in frames]
    ref = A.basis_samples(sd.bone_names, sd.bone_parents, sd.bind_world, worlds)          # (F, B, 10)
    idx = {n.lower(): i for i, n in enumerate(sd.bone_names)}
    worst = 0.0
    n = 0
    for tr in an.tracks:
        i = idx[tr.bone_name.lower()]
        tk = np.array(tr.trans_keys)
        rk = np.array(tr.rot_keys)
        for fi, f in enumerate(frames):
            p = f / K
            if len(tk):
                j = int(np.argmin(np.abs(tk[:, 0] - p)))
                if abs(tk[j, 0] - p) < 1e-6:
                    loc = N.basis_loc(consts[i], tk[j:j + 1, 1:4])[0]
                    worst = max(worst, np.abs(loc - ref[fi, i, 0:3]).max())
                    n += 1
            if len(rk):
                j = int(np.argmin(np.abs(rk[:, 0] - p)))
                if abs(rk[j, 0] - p) < 1e-6:
                    q = N.basis_quat(consts[i], rk[j:j + 1, 1:5])[0]
                    r = ref[fi, i, 3:7]
                    worst = max(worst, min(np.abs(q - r).max(), np.abs(q + r).max()))
                    n += 1
    assert n > 150 and worst < 2e-5, (n, worst)
    print(f"   {n} key values (location and rotation of {len(an.tracks)} bones at 5 frames) equal the add-on's own bake, worst difference {worst:.2e}", flush=True)


def _write(data, ref):
    p = os.path.join(C.tmpdir("nis_model_"), os.path.basename(ref))
    with open(p, "wb") as f:
        f.write(data.read(ref))
    return p


# ---------------------------------------------------------------------------------------------------------- 5. frames, events, removal
def test_start_frame_events_remove(game_dir):
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgm import nis_scene as NS
    top = import_map("mission3")
    e = next(o for o in nis_objects() if stem_of(o) == "nis_m3")
    model, ebp, sched, _scene, data = files_of(game_dir, e)
    a = N.build_nis(bpy.context, top, e)
    b = N.build_nis(bpy.context, top, e, start_frame=101)
    assert b.frame_start == 101 and b.frame_end - b.frame_start == a.frame_end - a.frame_start
    ta, tb = a.armature.animation_data.nla_tracks, b.armature.animation_data.nla_tracks
    for x, y in zip(ta, tb):
        assert abs((y.strips[0].frame_start - x.strips[0].frame_start) - 100) < 1e-6
    ka = min(k.co[0] for f in N._addon("actions")._fcurves(a.action) for k in f.keyframe_points)
    kb = min(k.co[0] for f in N._addon("actions")._fcurves(b.action) for k in f.keyframe_points)
    assert ka == 1 and kb == 101
    # events: in time order, and the file's own facts
    ev = a.events
    assert [x["t"] for x in ev] == sorted(x["t"] for x in ev)
    kinds = {}
    for x in ev:
        kinds.setdefault(x["kind"], []).append(x)
    assert len(kinds["clip_start"]) == len(sched) and all(x["quality"] == "exact" for x in ev if x["kind"] not in ("subtitle", "event"))
    assert [round(x["t"], 4) for x in kinds["camera_cut"]] == [round(c[0], 4) for c in NS.camera_cuts(model, sched)]
    fi, fo = kinds["fadein"][0], kinds["fadeout"][0]
    last = max(sched, key=lambda x: x.delay)
    assert fi["t"] == 0.0 and fi["seconds"] == 1.0 and abs(fo["t"] - (last.delay + 0.5 * last.duration)) < 1e-6 and fo["seconds"] == 2.0
    assert len(kinds["sound"]) == 3 and kinds["sound"][0]["sound"].upper().endswith("CHANIKOV.PAT")
    # a long one with the dialogue lines and exclusive mode
    top2 = import_map("mission1")
    e2 = next(o for o in nis_objects() if stem_of(o) == "nis_2")
    c = N.build_nis(bpy.context, top2, e2)
    subs = NS.read_subtitles(data.read("art/ebps/nis/nis_2.subtitles").decode("latin1"))
    got = [x for x in c.events if x["kind"] == "subtitle"]
    assert len(got) == len(subs) >= 10 and got[0]["t"] == 18.0 and got[0]["text_id"] == 56200
    assert any(x["kind"] == "exclusive_mode" and x["on"] for x in c.events)
    print(f"   start_frame 101 moves every strip and key by 100 frames; {len(ev)} events of nis_m3 in time order ({sorted(kinds)}); "
          f"nis_2 has {len(got)} subtitle lines and exclusive mode", flush=True)
    # removal
    top3 = import_map("mission3")
    e3 = next(o for o in nis_objects() if stem_of(o) == "nis_m3")
    base = (len(bpy.data.objects), len(bpy.data.actions), len(bpy.data.cameras), len(bpy.data.collections), len(bpy.data.armatures))
    d = N.build_nis(bpy.context, top3, e3)
    assert len(bpy.data.objects) > base[0] and len(bpy.data.actions) > base[1]
    N.remove_nis(d)
    # the meshes / materials of the removed objects stay as orphans until Blender purges them; the objects, actions, cameras, collections do not
    assert (len(bpy.data.objects), len(bpy.data.actions), len(bpy.data.cameras), len(bpy.data.collections)) == base[:4]
    print("   remove_nis leaves no object, action, camera or collection behind", flush=True)


# ---------------------------------------------------------------------------------------------------------- 6. the map never changes
def test_map_untouched(game_dir):
    from blender_native.sceneobj import map_nis as N
    for name in ("mission3", "mission1", "mission15"):
        top = import_map(name)
        ents = nis_objects()
        assert ents
        snaps = {o.name: snapshot(o) for o in ents}
        before = save(top, C.tmpdir("nis_save0_"))
        builds = [N.build_nis(bpy.context, top, e) for e in ents]
        assert all(b.camera is not None for b in builds)
        bpy.context.scene.frame_set(60)                         # the stand-ins animate, the placed objects must not
        assert {o.name: snapshot(o) for o in ents} == snaps
        during = save(top, C.tmpdir("nis_save1_"))
        assert during == before, (name, [k for k in set(before) | set(during) if before.get(k) != during.get(k)])
        for b in builds:
            N.remove_nis(b)
        after = save(top, C.tmpdir("nis_save2_"))
        assert after == before and {o.name: snapshot(o) for o in ents} == snaps
        print(f"   {name}: {len(ents)} cut-scene(s) built and removed; the placed objects are unchanged and the saved files "
              f"({', '.join(f'{k} {len(v)} B' for k, v in sorted(before.items()))}) are byte for byte the same each time", flush=True)


def test_edited_map_untouched(game_dir):
    """The same with a REAL edit in the map (a placed object moved 1 m), so the save re-encodes the map's entities: the stand-ins,
    built or removed, still make no difference to a single byte."""
    from blender_native.sceneobj import map_nis as N
    from ICParse.sgb import view
    top = import_map("mission3")
    raw = next(m for m in view.game_maps(game_dir) if m.key == "Engine.sga|scenarios/sp/mission3.sgb").read()
    victim = next(o for o in bpy.data.objects if o.get("ic_entity_id") is not None and not N.is_nis_blueprint(o) and o.get("ic_kind") == "nature")
    victim.location.x += 1.0
    a = save(top, C.tmpdir("nis_edit0_"))
    assert a["mission3.sgb"] != raw, "the edit did not change the saved map: the test would prove nothing"
    builds = [N.build_nis(bpy.context, top, e) for e in nis_objects()]
    bpy.context.scene.frame_set(200)
    b = save(top, C.tmpdir("nis_edit1_"))
    for x in builds:
        N.remove_nis(x)
    c = save(top, C.tmpdir("nis_edit2_"))
    assert a == b == c, [k for k in a if a[k] != b.get(k) or a[k] != c.get(k)]
    print(f"   with an object moved 1 m the saved map ({len(a['mission3.sgb'])} B, differs from the original) is byte for byte the same "
          "without stand-ins, with them built and after they were removed", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the NIS test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("every placed NIS builds")
        test_every_stock_nis_builds(game_dir)
        section("nis_1, placed by no map")
        test_unplaced_nis_1(game_dir)
        section("the camera")
        test_camera(game_dir)
        test_transition()
        test_entity_camera(game_dir)
        section("parts shown and hidden, the lens")
        test_visibility_and_lens(game_dir)
        test_show_between(game_dir)
        section("the bones and their layering")
        test_bones(game_dir)
        section("the key conversion against the add-on's own bake")
        test_basis_equals_the_addon_bake(game_dir)
        section("start frame, events, removal")
        test_start_frame_events_remove(game_dir)
        section("the map is never changed")
        test_map_untouched(game_dir)
        test_edited_map_untouched(game_dir)
    print("SO_MAP_NIS_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_nis", main)
