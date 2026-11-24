"""The map's cinematics (NISlets) in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_cinematics_test.py     (or: run_all.py -k so_map_cinematics)

1. the angle maths: a key's rotation / declination / roll become an orientation and come back (round trip);
2. a map with cinematics (mission1) imports: one collection per animation, one empty per key frame with the file's values, a path, a
   speed line and a preview that follows it, all counted against the file;
3. NO EDIT: a save of the stock maps (2p_faceoff without a cinematics file, mission1 with 21 animations and 120 key frames, 6p_big_bog, mission15's
   nine-kilobyte one) is byte for byte the map and the .na that were opened, with the files beside them unchanged;
4. edits: move a key, change a value, add and delete key frames, new and deleted animations, turn a key's arrows: the saved .na reads
   back with exactly those changes and every other animation is the text it was;
5. Copy view from a camera, key to the 3D cursor, key to an object, look-at target, the 1.85 picture, the preview;
6. checks: a key looking at a deleted object, an animation with one key frame, two animations with one number;
7. a map with no cinematics file gets one when an animation is added; a map made in memory too.
Nothing under the game folder changed.
"""
import math
import os
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)

FACEOFF = "Engine.sga|scenarios/mp/2p_faceoff.sgb"
MISSION1 = "Engine.sga|scenarios/sp/mission1.sgb"
MISSION15 = "Engine.sga|scenarios/sp/mission15_nis6.sgb"
BIG_BOG = "InsectData.sga|scenarios/mp/6p_big_bog.sgb"


def section(title):
    print(f"-- {title}", flush=True)


def import_map(key):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=False, game_look=False)
    assert "FINISHED" in res, (key, res)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    return top


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def na_of(game_dir, key):
    import ICParse.sga as sga
    gm = game_map(game_dir, key)
    arc = sga.read(gm.path)
    want = gm.member[:-4] + ".na"
    for e in arc.entries:
        if e.path.replace("\\", "/").lower() == want:
            return arc.read(e)
    return None


def save(top, folder, name=None):
    stem = name or (top.name[4:] if top.name.startswith("Map ") else top.name)
    res = bpy.ops.bz.export_map(filepath=os.path.join(folder, stem + ".sgb"))
    assert "FINISHED" in res, res
    return {f: open(os.path.join(d, f), "rb").read() for d, _s, fs in os.walk(folder) for f in fs}, stem


def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def test_angles():
    from blender_native.sceneobj import map_cinematics as MC
    for rot, dec, roll in ((0, 0, 0), (45, 20, 5), (-120, -30, -15), (170, 60, 40), (300, 10, 0), (90, 85, 20), (152.38585, 9.34728, 0)):
        m = MC.basis_for(rot, dec, roll)
        r2, d2, ro2 = MC.angles_of(m)
        assert abs(((r2 - rot + 180) % 360) - 180) < 1e-4 and abs(d2 - dec) < 1e-4 and abs(ro2 - roll) < 1e-4, (rot, dec, roll, r2, d2, ro2)
        assert abs(m.determinant() - 1) < 1e-6
    from mathutils import Matrix, Vector
    # game axes: rotation 0 looks along +z (Blender -y), declination 90 looks straight down
    f = -(MC.basis_for(0, 0, 0) @ Vector((0, 0, 1)))
    assert (f - Vector((0, -1, 0))).length < 1e-6
    f = -(MC.basis_for(0, 90, 0) @ Vector((0, 0, 1)))
    assert (f - Vector((0, 0, -1))).length < 1e-6
    # roll, in the game's axes, against the engine's matrices. r0 = u0 x f is the game camera's right when level
    for rot, dec, roll in ((0, 0, 30), (45, 20, -50), (-120, -30, 15)):
        level = MC.basis_for(rot, dec, 0)
        fwd = Vector(MC.b2g(-level.col[2]))
        u0 = Vector(MC.b2g(level.col[1]))
        r0 = u0.cross(fwd)
        c, s = math.cos(math.radians(roll)), math.sin(math.radians(roll))
        # a camera: the view is turned by RotateZ(roll), so the picture's right goes down. The picture's right is the
        # flipped camera's, the other way from the basis's X
        m = MC.basis_for(rot, dec, roll)
        assert (Vector(MC.b2g(-m.col[0])) - (r0 * c - u0 * s)).length < 1e-5, (rot, dec, roll)
        assert (Vector(MC.b2g(m.col[1])) - (u0 * c + r0 * s)).length < 1e-5, (rot, dec, roll)
        # a move animation's object: RotateX(roll) about the path, a plain turn of +roll about the forward direction
        obj = MC.basis_for(rot, dec, roll, camera=False)
        assert (Vector(MC.b2g(obj.col[1])) - (u0 * c + fwd.cross(u0) * s)).length < 1e-5, (rot, dec, roll)
        # a flipped camera reads like an unflipped one, and a level view reads 0, not -0
        flipped = m @ Matrix.Diagonal((-1.0, 1.0, 1.0))
        for camera, basis in ((True, flipped), (False, obj)):
            r2, d2, ro2 = MC.angles_of(basis, camera=camera)
            assert abs(((r2 - rot + 180) % 360) - 180) < 1e-4 and abs(d2 - dec) < 1e-4 and abs(ro2 - roll) < 1e-4, (camera, r2, d2, ro2)
    assert math.copysign(1.0, MC.angles_of(MC.basis_for(0, 0, 0))[2]) == 1.0
    print("   angles: 7 orientations round trip (rotation, declination, roll) to 1e-4 degrees; camera and object roll as the engine's", flush=True)


def test_import(game_dir):
    from ICParse.sgb import nislet as N
    from blender_native.sceneobj import map_cinematics as MC
    raw = na_of(game_dir, MISSION1)
    nf = N.NisFile.parse(raw)
    t0 = time.perf_counter()
    top = import_map(MISSION1)
    dt = time.perf_counter() - t0
    root = MC.root_of(top)
    colls = MC.anim_collections(root)
    assert len(colls) == len(nf.anims), (len(colls), len(nf.anims))
    keys = 0
    for c, a in zip(sorted(colls, key=lambda c: int(c["nis_src"])), nf.anims):
        assert (c["nis_kind"], c["nis_id"], c["nis_name"], c["nis_src"]) == (a.kind, a.id, a.name, nf.anims.index(a))
        objs = MC.key_objects(c)
        assert len(objs) == len(a.keys), a.name
        keys += len(objs)
        for o, k in zip(objs, a.keys):
            x, y, z = MC.b2g(o.location)
            assert max(abs(x - k.x), abs(y - k.y), abs(z - k.z)) < 1e-3, a.name
            assert o["nis_speed"] == k.speed and o["nis_src"] == k.src
            if a.kind == "camera":
                assert o["nis_rotation"] == k.rotation and o["nis_zoom"] == k.zoom and o["nis_target_entity"] == k.target_entity
        if len(objs) >= 2:
            assert MC.part_object(c, "path") is not None and MC.part_object(c, "speed") is not None
            pv = MC.part_object(c, "preview")
            assert pv is not None and pv.animation_data is not None and (pv.type == "CAMERA") == (a.kind == "camera")
            assert len(c["nis_frames"]) == 2 and c["nis_frames"][1] > c["nis_frames"][0]
            assert len(MC.part_object(c, "path").data.splines[0].points) == (len(objs) - 1) * MC.SAMPLES + 1
    rep = __import__("json").loads(top["ic_map_report"])
    assert rep["cinematics"]["animations"] == len(nf.anims) and rep["cinematics"]["keys"] == keys
    print(f"   mission1: {len(colls)} animations, {keys} key frames, built in {dt:.1f} s (the whole map import); every value read back", flush=True)
    return top


def test_no_edit(game_dir):
    for key in (FACEOFF, MISSION1, MISSION15, BIG_BOG):
        raw = game_map(game_dir, key).read()
        na = na_of(game_dir, key)
        top = import_map(key)
        out = C.tmpdir("cin_noedit_")
        files, stem = save(top, out)
        assert files[stem + ".sgb"] == raw, key
        if na is not None:
            assert files[stem + ".na"] == na, key
        else:
            assert stem + ".na" not in files, (key, sorted(files))
        print(f"   {key.split('|')[1]}: map {len(raw)} bytes and .na {str(len(na)) + ' bytes' if na else '(none)'} byte for byte", flush=True)


def test_edits(game_dir):
    from ICParse.sgb import nislet as N
    from blender_native.sceneobj import map_cinematics as MC
    raw = na_of(game_dir, MISSION1)
    old = N.NisFile.parse(raw)
    top = import_map(MISSION1)
    root = MC.root_of(top)
    colls = {int(c["nis_src"]): c for c in MC.anim_collections(root)}
    # -- 1. move one key, change one value, edit a blend time
    cam_i = next(i for i, a in enumerate(old.anims) if a.kind == "camera" and len(a.keys) >= 3)
    c = colls[cam_i]
    keys = MC.key_objects(c)
    keys[1].location.x += 5.0
    keys[1]["nis_tension"] = 0.5
    c["nis_blend_out"] = 2.25
    out = C.tmpdir("cin_edit_")
    files, stem = save(top, out)
    new = N.NisFile.parse(files[stem + ".na"])
    assert new.canonical() and len(new.anims) == len(old.anims)
    for i, (a, b) in enumerate(zip(old.anims, new.anims)):
        if i != cam_i:
            assert N.emit_lua([("x", a._src)]) == N.emit_lua([("x", b._src)]), (i, a.name)      # untouched = the text it was
    a, b = old.anims[cam_i], new.anims[cam_i]
    assert abs(b.keys[1].x - (a.keys[1].x + 5.0)) < 1e-3 and b.keys[1].tension == 0.5 and b.blend_out == 2.25
    assert b.keys[0].x == a.keys[0].x and b.keys[1].y == a.keys[1].y and b.keys[2].speed == a.keys[2].speed
    changed = [i for i in range(len(a.keys)) if any(getattr(a.keys[i], f) != getattr(b.keys[i], f) for f in ("x", "tension", "y", "z", "speed", "rotation"))]
    assert changed == [1], changed
    lines_a, lines_b = raw.decode("latin1").split("\r\n"), files[stem + ".na"].decode("latin1").split("\r\n")
    assert len(lines_a) == len(lines_b)
    diff = [1 for x, y in zip(lines_a, lines_b) if x != y]
    assert 2 <= len(diff) <= 8, len(diff)           # the moved x (+ its end entry when first), tension, blendOutTime
    print(f"   one key moved, one value and a blend time changed: {len(diff)} lines of {len(lines_a)} differ, the other animations are the text they were", flush=True)

    # -- 2. turn a key's arrows: the angles are read from the empty
    top = import_map(MISSION1)
    root = MC.root_of(top)
    c = {int(x["nis_src"]): x for x in MC.anim_collections(root)}[cam_i]
    k = MC.key_objects(c)[1]
    import mathutils
    k.rotation_euler = mathutils.Euler((0.0, 0.0, 0.0)).to_matrix().to_4x4().to_euler()
    k.rotation_euler = MC.basis_for(33.0, 12.0, 4.0).to_euler()
    files, stem = save(top, C.tmpdir("cin_turn_"))
    b = N.NisFile.parse(files[stem + ".na"]).anims[cam_i].keys[1]
    assert abs(b.rotation - 33.0) < 1e-3 and abs(b.declination - 12.0) < 1e-3 and abs(b.roll - 4.0) < 1e-3, (b.rotation, b.declination, b.roll)
    print("   a key's arrows turned in the 3D view are saved as rotation 33, declination 12, roll 4", flush=True)
    # a .blend saved by 0.8.0: arrows without the mark were drawn with the roll the other way round and are read so
    top = import_map(MISSION1)
    root = MC.root_of(top)
    c = {int(x["nis_src"]): x for x in MC.anim_collections(root)}[cam_i]
    k = MC.key_objects(c)[1]
    del k[MC.K_ROLL]
    k.rotation_euler = MC.basis_for(33.0, 12.0, 4.0, camera=False).to_euler()
    files, stem = save(top, C.tmpdir("cin_old_"))
    b = N.NisFile.parse(files[stem + ".na"]).anims[cam_i].keys[1]
    assert abs(b.roll - 4.0) < 1e-3, b.roll
    print("   arrows drawn by 0.8.0 keep their roll", flush=True)

    # -- 3. operators: new animation, add / move / delete keys, delete an animation
    top = import_map(MISSION1)
    root = MC.root_of(top)
    n0 = len(MC.anim_collections(root))
    bpy.context.scene.cursor.location = (10.0, -20.0, 5.0)
    assert "FINISHED" in bpy.ops.bz.nis_new_anim(kind="camera")
    colls = MC.anim_collections(root)
    assert len(colls) == n0 + 1
    new_c = next(c for c in colls if int(c["nis_src"]) == -1)
    assert new_c["nis_kind"] == "camera" and new_c["nis_name"].startswith("mission1_CameraAnim_") and len(MC.key_objects(new_c)) == 2
    ids = sorted(int(c["nis_id"]) for c in colls if c["nis_kind"] == "camera" and c is not new_c)
    assert int(new_c["nis_id"]) == max(i for i in ids if i < 100000) + 1 and len(set(ids)) == len(ids), (ids, new_c["nis_id"])
    first = MC.key_objects(new_c)[0]
    assert tuple(round(v, 4) for v in MC.b2g(first.location)) == (10.0, 5.0, 20.0)
    select_only([MC.key_objects(new_c)[1]])
    assert "FINISHED" in bpy.ops.bz.nis_add_key(where="BEFORE")            # between key 1 and key 2
    assert len(MC.key_objects(new_c)) == 3
    mid = MC.key_objects(new_c)[1]
    assert abs(MC.b2g(mid.location)[0] - 25.0) < 1.0, MC.b2g(mid.location)
    select_only([mid])
    assert "FINISHED" in bpy.ops.bz.nis_move_key(step=1)
    assert MC.key_objects(new_c)[2] is mid and MC.key_objects(new_c)[2].name.endswith("key 3")
    assert "FINISHED" in bpy.ops.bz.nis_move_key(step=-1)
    select_only([MC.key_objects(new_c)[1]])
    bpy.context.scene.cursor.location = (50.0, -60.0, 7.0)
    assert "FINISHED" in bpy.ops.bz.nis_key_to_cursor()
    assert "FINISHED" in bpy.ops.bz.nis_add_key(where="CURSOR")
    assert len(MC.key_objects(new_c)) == 4
    last = MC.key_objects(new_c)[2]
    assert tuple(round(v, 3) for v in MC.b2g(last.location)) == (50.0, 7.0, 60.0)
    select_only([MC.key_objects(new_c)[3]])
    assert "FINISHED" in bpy.ops.bz.nis_delete_key()
    assert len(MC.key_objects(new_c)) == 3
    # delete the first old animation
    victim = next(c for c in MC.anim_collections(root) if int(c["nis_src"]) == 0)
    root[MC.K_ACTIVE] = victim.name
    victim_name, victim_coll = victim["nis_name"], victim.name
    select_only([MC.key_objects(victim)[0]])
    assert "FINISHED" in bpy.ops.bz.nis_delete_anim()
    assert bpy.data.collections.get(victim_coll) is None and len(MC.anim_collections(root)) == n0
    files, stem = save(top, C.tmpdir("cin_ops_"))
    g = N.NisFile.parse(files[stem + ".na"])
    assert g.canonical() and len(g.anims) == n0
    assert victim_name not in [a.name for a in g.anims]
    mine = next(a for a in g.anims if a.name == new_c["nis_name"])
    assert len(mine.keys) == 3 and mine.kind == "camera" and mine.id == int(new_c["nis_id"])
    assert max(abs(a - b) for a, b in zip((mine.keys[1].x, mine.keys[1].y, mine.keys[1].z), (50.0, 7.0, 60.0))) < 1e-3, mine.keys[1]
    old = N.NisFile.parse(na_of(game_dir, MISSION1))
    survivors = [a for a in old.anims if a.name != victim_name]
    for a in survivors:
        b = next(x for x in g.anims if x.name == a.name)
        assert (b.kind, b.id, [k.x for k in b.keys]) == (a.kind, a.id, [k.x for k in a.keys])
    assert b"num_anims = %d," % n0 in files[stem + ".na"]
    print(f"   new camera animation (3 keys by add / move / delete), one animation deleted: .na has {n0} animations and reads back", flush=True)
    return top


def test_aim_and_view(game_dir):
    from blender_native.sceneobj import map_cinematics as MC
    top = import_map(MISSION1)
    root = MC.root_of(top)
    bpy.context.scene.cursor.location = (0, 0, 3)
    bpy.ops.bz.nis_new_anim(kind="camera")
    c = next(c for c in MC.anim_collections(root) if int(c["nis_src"]) == -1)
    k = MC.key_objects(c)[0]
    select_only([k])
    cam = bpy.data.objects.new("viewcam", bpy.data.cameras.new("viewcam"))
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (30.0, -40.0, 12.0)
    cam.rotation_euler = MC.basis_for(75.0, 25.0, 6.0).to_euler()
    bpy.context.scene.camera = cam
    bpy.context.view_layer.update()
    assert "FINISHED" in bpy.ops.bz.nis_key_from_view(place=True)
    assert abs(k["nis_rotation"] - 75.0) < 1e-3 and abs(k["nis_declination"] - 25.0) < 1e-3 and abs(k["nis_roll"] - 6.0) < 1e-3
    assert tuple(round(v, 3) for v in MC.b2g(k.location)) == (30.0, 12.0, 40.0)
    cam.scale = (-1.0, 1.0, 1.0)                                  # a game camera is flipped: the same values come out
    k["nis_roll"] = 0.0
    assert "FINISHED" in bpy.ops.bz.nis_key_from_view(place=True)
    assert abs(k["nis_rotation"] - 75.0) < 1e-3 and abs(k["nis_declination"] - 25.0) < 1e-3 and abs(k["nis_roll"] - 6.0) < 1e-3
    # look at a placed object
    ents = [o for o in top.all_objects if o.get("ic_entity_id") is not None]
    target = ents[0]
    select_only([k, target])
    bpy.context.view_layer.objects.active = k
    assert "FINISHED" in bpy.ops.bz.nis_key_target()
    assert k["nis_target_entity"] == int(target["ic_entity_id"])
    select_only([k])
    assert "FINISHED" in bpy.ops.bz.nis_key_target(clear=True) and k["nis_target_entity"] == -1
    select_only([k, target])
    bpy.context.view_layer.objects.active = k
    assert "FINISHED" in bpy.ops.bz.nis_key_to_object()
    assert (k.location - target.matrix_world.translation).length < 1e-4
    select_only([k])
    assert "FINISHED" in bpy.ops.bz.nis_key_auto(prop="nis_rotation") and k["nis_rotation"] == 36000.0
    # picture shape and preview
    r = bpy.context.scene.render
    x0, y0 = r.resolution_x, r.resolution_y
    assert "FINISHED" in bpy.ops.bz.nis_aspect()
    assert abs(r.resolution_x / r.resolution_y - 1.85) < 0.01 and r.resolution_x == x0
    assert "FINISHED" in bpy.ops.bz.nis_aspect() and (r.resolution_x, r.resolution_y) == (x0, y0)
    assert "FINISHED" in bpy.ops.bz.nis_preview()
    pv = MC.part_object(c, "preview")
    assert bpy.context.scene.camera == pv and bpy.context.scene.frame_end > bpy.context.scene.frame_start
    bpy.context.scene.frame_set(bpy.context.scene.frame_end)
    assert (pv.matrix_world.translation - MC.key_objects(c)[-1].location).length < 0.01, "the preview ends on the last key"
    assert pv.matrix_world.determinant() < 0, "the preview camera shows the game's picture (flipped left to right)"
    assert "FINISHED" in bpy.ops.bz.nis_toggle_display(which="speed")
    assert MC.part_object(c, "speed").hide_viewport is False
    print("   copy view (75, 25, 6) -> key, look-at target, key to object, 1.85 picture and back, preview ends on the last key", flush=True)


def test_checks(game_dir):
    from blender_native.sceneobj import map_cinematics as MC
    from blender_native.sceneobj import map_hooks as H
    top = import_map(MISSION1)
    root = MC.root_of(top)
    base = [p for p in H.run_checks(bpy.context, top) if p["check"] == "cinematics"]
    stock_warns = len(base)
    bpy.ops.bz.nis_new_anim(kind="move")
    c = next(c for c in MC.anim_collections(root) if int(c["nis_src"]) == -1)
    k = MC.key_objects(c)
    select_only([k[1]])
    bpy.ops.bz.nis_delete_key()
    out = [p for p in H.run_checks(bpy.context, top) if p["check"] == "cinematics"]
    assert any("at least 2" in p["text"] for p in out), out
    bpy.ops.bz.nis_new_anim(kind="camera")
    c2 = next(c for c in MC.anim_collections(root) if int(c["nis_src"]) == -1 and c["nis_kind"] == "camera")
    MC.key_objects(c2)[0]["nis_target_entity"] = 4242424
    out = [p for p in H.run_checks(bpy.context, top) if p["check"] == "cinematics"]
    assert any("entity 4242424" in p["text"] and p["severity"] == "warning" for p in out), out
    c2["nis_id"] = 0
    other = next(c for c in MC.anim_collections(root) if c["nis_kind"] == "camera" and c is not c2)
    other["nis_id"] = 0
    out = H.run_checks(bpy.context, top)
    assert any(p["severity"] == "error" and "share the number" in p["text"] for p in out) and out[0]["severity"] == "error"
    # deleting an entity the stock cameras look at: mission9 targets entity 65161
    top = import_map("Engine.sga|scenarios/sp/mission9.sgb")
    out = [p for p in H.run_checks(bpy.context, top) if p["check"] == "cinematics"]
    before = len([p for p in out if "entity" in p["text"]])
    target = next(o for o in top.all_objects if o.get("ic_entity_id") == 65161)
    bpy.data.objects.remove(target, do_unlink=True)
    out = [p for p in H.run_checks(bpy.context, top) if p["check"] == "cinematics"]
    assert len([p for p in out if "entity 65161" in p["text"]]) >= 1 and len([p for p in out if "entity" in p["text"]]) > before
    print(f"   checks: too few keys, a key looking at a missing object (and at one deleted from mission9), shared numbers", flush=True)


def test_panel(game_dir):
    from blender_native.sceneobj import map_cinematics as MC
    from blender_native.sceneobj import panels_map_cinematics as P
    top = import_map(MISSION1)
    root = MC.root_of(top)
    assert bpy.types.BZ_PT_map_cinematics.bl_parent_id == "BZ_PT_map" == P.CLASSES[0].bl_parent_id
    for kind in ("camera", "obj", "move"):
        c = next(c for c in MC.anim_collections(root) if c["nis_kind"] == kind and len(MC.key_objects(c)) >= 2)
        select_only([MC.key_objects(c)[1]])
        lay = C.FakeLayout()
        P.draw_cinematics(lay, bpy.context, top)
        ops = [d["idname"] for k, d in lay.calls if k == "operator"]
        props = [d["name"] for k, d in lay.calls if k == "prop"]
        assert "bz.nis_select_anim" in ops and "bz.nis_add_key" in ops and "bz.nis_check" in ops and "bz.nis_aspect" in ops, ops
        assert ("bz.nis_key_from_view" in ops) == (kind != "obj") and ("bz.nis_key_target" in ops) == (kind == "camera"), (kind, ops)
        assert ('["nis_zoom"]' in props) == (kind == "camera") and '["nis_speed"]' in props and '["nis_name"]' in props, props
        assert ('["nis_blend_in"]' in props) == (kind != "obj"), props
        for idname in set(ops):
            head, _, tail = idname.partition(".")
            assert hasattr(getattr(bpy.ops, head), tail), idname
        for name in props:
            assert name.strip('[]"') in c or name.strip('[]"') in bpy.context.active_object, name
    print("   the Cinematics panel draws for a camera, an object and a move animation, every button is a real operator", flush=True)


def test_loose_files(game_dir):
    """A map opened from a folder: its .na is read from beside it; a .na the add-on cannot understand is left alone, never rewritten."""
    from ICParse.sgb import nislet as N
    from blender_native.sceneobj import map_cinematics as MC
    src = C.tmpdir("cin_src_")
    raw = game_map(game_dir, MISSION15).read()
    na = na_of(game_dir, MISSION15)
    with open(os.path.join(src, "nis.sgb"), "wb") as f:
        f.write(raw)
    with open(os.path.join(src, "NIS.NA"), "wb") as f:
        f.write(na)
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_map(filepath=os.path.join(src, "nis.sgb"), load_models=False, game_look=False)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    root = MC.root_of(top)
    assert len(MC.anim_collections(root)) == 3 and not root.get(MC.K_BAD)
    files, stem = save(top, C.tmpdir("cin_loose_"), "nis")
    assert files["nis.sgb"] == raw and files["nis.NA"] == na and "nis.na" not in files          # untouched: the file as it was, spelled as it was
    bpy.ops.bz.nis_new_anim(kind="obj")
    files, stem = save(top, C.tmpdir("cin_loose2_"), "nis")
    assert "nis.NA" not in files and N.NisFile.parse(files["nis.na"]).anims[3].kind == "obj" and len(N.NisFile.parse(files["nis.na"]).anims) == 4
    # an unreadable file
    junk = b"nislet_anim = { this is not what the game writes"
    with open(os.path.join(src, "NIS.NA"), "wb") as f:
        f.write(junk)
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_map(filepath=os.path.join(src, "nis.sgb"), load_models=False, game_look=False)
    [top] = [c for c in bpy.data.collections if c.get("ic_map")]
    root = MC.root_of(top)
    assert root.get(MC.K_BAD) and not MC.anim_collections(root)
    files, stem = save(top, C.tmpdir("cin_junk_"), "nis")
    assert files["nis.sgb"] == raw and files["nis.NA"] == junk
    # a map without the cinematics collection (opened before the feature existed): the panel and the operators cope
    from blender_native.sceneobj import panels_map_cinematics as P
    top = import_map(FACEOFF)
    bpy.data.collections.remove(MC.root_of(top))
    lay = C.FakeLayout()
    P.draw_cinematics(lay, bpy.context, top)
    assert [d["idname"] for k, d in lay.calls if k == "operator"] == ["bz.nis_new_anim"]
    assert "FINISHED" in bpy.ops.bz.nis_new_anim(kind="move") and MC.root_of(top) is not None
    print("   a map opened from a folder reads its .NA beside it; an unreadable .na is kept untouched; a map without the collection still works", flush=True)


def test_new_file_and_memory(game_dir):
    from ICParse.sgb import nislet as N
    from blender_native.sceneobj import map_cinematics as MC
    top = import_map(FACEOFF)
    assert na_of(game_dir, FACEOFF) is not None and len(MC.anim_collections(MC.root_of(top))) == 0
    bpy.ops.bz.nis_new_anim(kind="obj")
    files, stem = save(top, C.tmpdir("cin_new_"))
    g = N.NisFile.parse(files[stem + ".na"])
    assert [(a.kind, a.id, len(a.keys)) for a in g.anims] == [("obj", 0, 2)] and g.canonical()
    # a map made in memory
    from blender_native.sceneobj import map_hooks as H
    data = game_map(game_dir, FACEOFF).read()
    C.fresh()
    top = H.import_map_bytes(bpy.context, data, "cinmap", {"load_models": False, "game_look": False})
    assert MC.root_of(top) is not None and not MC.anim_collections(MC.root_of(top))
    bpy.ops.bz.nis_new_anim(kind="camera")
    files, stem = save(top, C.tmpdir("cin_mem_"), "cinmap")
    assert stem + ".na" in files and N.NisFile.parse(files[stem + ".na"]).anims[0].name.startswith("cinmap_CameraAnim_")
    print("   a map whose .na is the game's empty one, and a map made in memory, get a cinematics file when an animation is added", flush=True)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the cinematics test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("angles")
        test_angles()
        section("import")
        test_import(game_dir)
        section("no edit")
        test_no_edit(game_dir)
        section("edits")
        test_edits(game_dir)
        section("aim, view, picture, preview")
        test_aim_and_view(game_dir)
        section("checks")
        test_checks(game_dir)
        section("the panel")
        test_panel(game_dir)
        section("loose files")
        test_loose_files(game_dir)
        section("a new file, a map in memory")
        test_new_file_and_memory(game_dir)
    print("SO_MAP_CINEMATICS_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_cinematics", main)
