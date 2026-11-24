"""Map editing and saving (File > Export > Impossible Creatures Map), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_edit_test.py     (or: run_all.py -k so_map_edit)

Stock maps, read straight from the game's archives and imported with the map import:

1. a map saved with no edits is byte for byte the map that was opened (2p_faceoff, mission1 with the creatures combined in it,
   6p_big_bog from InsectData, 6p_bikini_atoll the largest), and the files that sit beside it come out unchanged;
2. every edit made in Blender reaches the saved map and nothing else changes: an entity moved, one deleted, one copied (Shift+D), one
   added at the 3D cursor with "Add object", one given to another player, one dropped to the ground, a block of the terrain raised
   (with a height that has to be rounded and one that has to be clamped), the water moved up, blocks painted with a ground texture
   added with "Add ground texture" (a tie keeps the block's own texture); the saved file is read again and compared with the original;
   the saved file opens again in Blender with the edits in it;
3. what is NOT an edit is left alone: a map with only its water moved differs from the original in the terrain chunk alone, one with
   only an owner changed in the player records alone;
4. refusals in plain words, writing nothing: a terrain whose points were removed, a folder inside the game's folder or any
   steamapps folder, the original file;
5. the ready-to-drop folder (Data/scenarios/mp/<name>.sgb, the .trg, a README), the lists of the game's ground textures and
   blueprints, the Map panel (drawn into a stand-in layout), the File > Export entry; nothing under the game folder changed.
"""
import collections
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, C.REPO)

FACEOFF = "Engine.sga|scenarios/mp/2p_faceoff.sgb"
NO_EDIT_MAPS = (FACEOFF, "Engine.sga|scenarios/sp/mission1.sgb", "InsectData.sga|scenarios/mp/6p_big_bog.sgb",
                "Engine.sga|scenarios/mp/6p_bikini_atoll.sgb")
NEW_GROUND = "desert_02.tga"
NEW_BLUEPRINT = "art/ebps/nature/savbush_01.ebp"


def section(title):
    print(f"-- {title}", flush=True)


def expect_error(words, call):
    """An operator that reports an error raises it when called from a script: the message must say ``words``."""
    try:
        res = call()
    except RuntimeError as exc:
        assert words in str(exc), exc
        return str(exc)
    raise AssertionError(f"no error reported (result {res})")


def game_map(game_dir, key):
    from ICParse.sgb import view
    return next(m for m in view.game_maps(game_dir) if m.key == key)


def import_map(key, *, models=False):
    C.fresh()
    res = bpy.ops.bz.import_game_map(game_map=key, load_models=models)
    assert "FINISHED" in res, (key, res)
    top = [c for c in bpy.data.collections if c.get("ic_map")]
    assert len(top) == 1
    return top[0]


def entity_by_id(top):
    from blender_native.sceneobj import ops_map_export as X
    return {int(o["ic_entity_id"]): o for o in X.entity_objects(top) if "ic_entity_id" in o}


def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def terrain_z(terrain):
    me = terrain.data
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def set_terrain_z(terrain, w, edits):
    """``edits`` = {(x, z): new height or ("add", delta)} on the grid."""
    co = terrain_z(terrain)
    for (x, z), v in edits.items():
        i = z * w + x
        co[i, 2] = co[i, 2] + v[1] if isinstance(v, tuple) else v
    terrain.data.vertices.foreach_set("co", co.ravel())
    terrain.data.update()


def leaves(doc):
    """Every leaf chunk of an IFF document as a multiset of (innermost FORM, tag, bytes)."""
    out = collections.Counter()

    def walk(chunks, parent):
        for c in chunks:
            kids = getattr(c, "children", None)
            if kids:
                walk(kids, c.form_type)
            else:
                out[(parent, c.tag, bytes(c.raw))] += 1
    walk(doc.root_chunks, "")
    return out


def changed_tags(a: bytes, b: bytes) -> set:
    """``FORM/TAG`` of the leaf chunks that are in one file and not in the other."""
    from ICParse.iff.document import IffDocument
    la, lb = leaves(IffDocument.from_bytes(a)), leaves(IffDocument.from_bytes(b))
    return {f"{k[0]}/{k[1]}" for k in (la - lb)} | {f"{k[0]}/{k[1]}" for k in (lb - la)}


class _Props:
    """What ``layout.operator()`` returns: setting an argument the operator does not have is recorded, as a real draw would raise it."""

    def __init__(self, idname, allowed, problems, record):
        object.__setattr__(self, "_idname", idname)
        object.__setattr__(self, "_allowed", allowed)
        object.__setattr__(self, "_problems", problems)
        object.__setattr__(self, "_record", record)

    def __setattr__(self, name, value):
        if name not in self._allowed:
            self._problems.append(f"operator {self._idname} has no argument {name}")
        self._record[name] = value


class CheckedLayout(C.FakeLayout):
    """``FakeLayout`` plus what a real draw would reject: an operator that is not registered or has no such argument, an icon that does
    not exist (panels cannot be drawn in background mode)."""
    ICONS = None

    def __init__(self, calls=None, alert=False, problems=None):
        super().__init__(calls, alert)
        self.problems = problems if problems is not None else []
        if CheckedLayout.ICONS is None:
            CheckedLayout.ICONS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())

    def _sub(self, kind, **kw):
        self.calls.append((kind, kw))
        return CheckedLayout(self.calls, alert=self.alert, problems=self.problems)

    def _icon(self, icon):
        if icon and icon != "NONE" and icon not in self.ICONS:
            self.problems.append(f"no icon called {icon}")

    def label(self, text="", icon="NONE", icon_value=0, **kw):
        self._icon(icon)
        super().label(text=text, icon=icon, icon_value=icon_value, **kw)

    def operator(self, idname, text="", icon="NONE", **kw):
        self._icon(icon)
        mod, _, fn = idname.partition(".")
        try:
            allowed = set(getattr(getattr(bpy.ops, mod), fn).get_rna_type().properties.keys())
        except (AttributeError, KeyError):
            self.problems.append(f"operator {idname} is not registered")
            allowed = set()
        record = dict(idname=idname, text=text, icon=icon, alert=self.alert, **kw)
        self.calls.append(("operator", record))
        return _Props(idname, allowed, self.problems, record)


# ---------------------------------------------------------------------------------------------------- 1. no edits
def test_no_edit(game_dir):
    from blender_native.sceneobj import ops_map_export as X
    for key in NO_EDIT_MAPS:
        gm = game_map(game_dir, key)
        raw = gm.read()
        top = import_map(key)
        plan = X.build_plan(bpy.context, top)
        assert not plan.changed, (key, plan.lines())
        out = C.tmpdir("mapsave_")
        stem = os.path.splitext(os.path.basename(gm.member))[0]
        path = os.path.join(out, f"{stem}.sgb")
        res = bpy.ops.bz.export_map(filepath=path)
        assert "FINISHED" in res, res
        with open(path, "rb") as f:
            saved = f.read()
        assert saved == raw, f"{key}: a map saved with no edits differs from the original ({len(saved)} vs {len(raw)} bytes)"
        text = bpy.data.texts.get(f"Map save {stem}")
        assert text is not None and "byte for byte" in text.as_string(), text.as_string() if text else None
        # the files beside it come out unchanged
        import ICParse.sga as sga
        arc = sga.read(gm.path)
        beside = sorted(os.path.relpath(os.path.join(d, f), out).replace(os.sep, "/") for d, _s, fs in os.walk(out) for f in fs
                        if f != f"{stem}.sgb")
        assert f"{stem}.trg" in beside, (key, beside)
        for name in beside:
            entry = arc.entry(os.path.dirname(gm.member) + "/" + name)
            assert entry is not None, (key, name)
            with open(os.path.join(out, name), "rb") as f:
                assert f.read() == arc.read(entry), (key, name)
        print(f"   {key}: {len(raw)} bytes back byte for byte, with {len(beside)} files beside it", flush=True)


# ---------------------------------------------------------------------------------------------------- 2. every edit
def test_edits(game_dir):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    gm = game_map(game_dir, FACEOFF)
    raw = gm.read()
    src = view.read(raw)
    top = import_map(FACEOFF)
    ents = entity_by_id(top)
    w, h = src.width, src.height
    assert len(ents) == len(src.entities) == 164
    unowned = [e.instance_id for e in src.entities if e.owner < 0 and e.kind == "nature"]
    a_id, b_id, c_id, d_id, g_id, k_id = unowned[0], unowned[1], unowned[2], unowned[3], unowned[4], unowned[5]

    # ---- terrain: a raised block, a height that rounds, one that is clamped
    terrain = X.terrain_of(top)
    region = {(x, z): ("add", 3.0) for x in range(10, 15) for z in range(10, 15)}
    region[(30, 30)] = ("add", 0.2)               # under half a step: rounds back
    region[(40, 40)] = ("add", 0.3)               # rounds to one step up
    region[(50, 50)] = 500.0                      # above what the map can store: clamped
    set_terrain_z(terrain, w, region)

    # ---- water
    water = X.water_of(top)
    assert water is not None
    water.location.z += 1.0

    # ---- ground textures
    res = bpy.ops.bz.map_add_ground(texture=NEW_GROUND)
    assert "FINISHED" in res, res
    slot = len(terrain.data.materials) - 1
    assert terrain.data.materials[slot]["ic_ground_texture"] == NEW_GROUND
    res = bpy.ops.bz.map_add_ground(texture=NEW_GROUND)           # already there: said, not added twice
    assert "CANCELLED" in res and len(terrain.data.materials) == slot + 1, res
    mi = np.empty(len(terrain.data.polygons), np.int32)
    terrain.data.polygons.foreach_get("material_index", mi)
    fb = src.face_blocks()
    nbx = src.blocks[0]
    blk = lambda bx, bz: bz * nbx + bx                             # noqa: E731
    whole = np.flatnonzero(fb == blk(3, 3))
    mi[whole] = slot                                               # a whole block
    half = np.flatnonzero(fb == blk(5, 5))
    mi[half[:len(half) // 2]] = slot                               # exactly half: a tie keeps the block's own
    most = np.flatnonzero(fb == blk(6, 6))
    mi[most[:len(most) // 2 + 1]] = slot                           # one more than half: the block takes it
    terrain.data.polygons.foreach_set("material_index", mi)
    terrain.data.update()

    # ---- entities
    src_e = {e.instance_id: e for e in src.entities}
    ents[a_id].location.x += 5.0                                   # moved
    ents[a_id].rotation_euler.z += 0.5                             # and turned
    bpy.data.objects.remove(ents[b_id], do_unlink=True)            # deleted
    select_only([ents[c_id]])
    res = bpy.ops.object.duplicate()                               # Shift+D
    assert "FINISHED" in res, res
    dup = bpy.context.view_layer.objects.active
    assert dup is not ents[c_id] and dup["ic_entity_id"] == c_id and dup.name != ents[c_id].name
    dup.location.x += 6.0
    select_only([ents[k_id]])
    res = bpy.ops.object.duplicate(linked=True)                    # Alt+D
    assert "FINISHED" in res, res
    dup_k = bpy.context.view_layer.objects.active
    assert dup_k is not ents[k_id] and dup_k["ic_entity_id"] == k_id
    dup_k.location.y -= 7.0
    select_only([ents[d_id]])
    res = bpy.ops.bz.map_set_owner(owner="1")
    assert "FINISHED" in res and ents[d_id]["ic_owner"] == 1, res
    ents[g_id].location.z += 10.0
    select_only([ents[g_id]])
    res = bpy.ops.bz.map_drop_to_ground()
    assert "FINISHED" in res, res
    gz = ents[g_id].matrix_world.translation
    co = terrain_z(terrain)
    gx, gy = gz.x / src.tile_scale + (w - 1) / 2, -gz.y / src.tile_scale + (h - 1) / 2
    i, j = int(gx), int(gy)
    fx, fy = gx - i, gy - j
    zs = co[:, 2].reshape(h, w)
    want = zs[j, i] * (1 - fx) * (1 - fy) + zs[j, i + 1] * fx * (1 - fy) + zs[j + 1, i] * (1 - fx) * fy + zs[j + 1, i + 1] * fx * fy
    assert abs(gz.z - want) < 1e-3, (gz.z, want)
    bpy.context.scene.cursor.location = (20.0, -20.0, 0.0)
    res = bpy.ops.bz.map_add_object(blueprint=NEW_BLUEPRINT)
    assert "FINISHED" in res, res
    added = [o for o in X.entity_objects(top) if o.get(X.KEY_NEW)]
    assert len(added) == 1 and added[0]["ic_blueprint"].upper().endswith("NATURE\\SAVBUSH_01.EBP"), [o.name for o in added]
    assert added[0].instance_type == "COLLECTION" and added[0].instance_collection["ic_map_model"] == "art/ebps/nature/savbush_01.sgm", \
        added[0].get("ic_not_shown")                               # the model of the new object was loaded, like the map's own
    hidden = X.hidden_collection(top)
    assert added[0].instance_collection.name in hidden.children
    layer = bpy.context.view_layer.layer_collection.children[top.name].children[hidden.name]
    assert layer.exclude, "the loaded model must stay out of the view"
    res = bpy.ops.bz.map_add_object(blueprint=NEW_BLUEPRINT)       # the second one of the same blueprint reuses the loaded model
    assert "FINISHED" in res
    again = [o for o in X.entity_objects(top) if o.get(X.KEY_NEW) and o is not added[0]]
    assert len(again) == 1 and again[0].instance_collection is added[0].instance_collection and len(hidden.children) == 1
    bpy.data.objects.remove(again[0], do_unlink=True)
    assert abs(added[0].location.z - X.ground_height_at(terrain, 20.0, -20.0)) < 1e-4

    plan = X.build_plan(bpy.context, top)
    assert (plan.moved, plan.added, plan.removed, plan.owners) == (1, 3, 1, 1), plan.lines()
    assert plan.height_points == 25 + 3 and plan.block_count == 2, plan.lines()
    assert abs(plan.water_move - 1.0) < 1e-3
    print("   " + "\n   ".join(plan.lines()), flush=True)

    out = C.tmpdir("mapsave_")
    path = os.path.join(out, "faceoff_edited.sgb")
    res = bpy.ops.bz.export_map(filepath=path)
    assert "FINISHED" in res, res
    text = bpy.data.texts["Map save faceoff_edited"].as_string()
    print("   " + text.replace("\n", "\n   "), flush=True)
    assert "Pathfinding: updated" in text and "How close a pathfinding rebuilt like the game's is to the game's own" in text, text
    assert "rounded to the map's steps" in text and "cut to the lowest or highest height the map can store" in text, text
    with open(path, "rb") as f:
        saved = f.read()

    # ---- the saved file, read again
    new = view.read(saved)
    step = src.height_scale
    exp = src.heights.astype(np.int32).copy()
    for x in range(10, 15):
        for z in range(10, 15):
            exp[z, x] += int(round(3.0 / step))
    exp[40, 40] += 1
    exp[50, 50] = 255
    assert np.array_equal(new.heights.astype(np.int32), exp), "the ground differs from what was edited"
    assert new.water_level == src.water_level + int(round(1.0 / step)), (new.water_level, src.water_level)
    tex_new = next((t for t in new.textures if t.name.lower() == NEW_GROUND), None)
    assert tex_new is not None, [t.name for t in new.textures]
    blocks = src.block_ids.copy()
    blocks[3, 3] = tex_new.texid
    blocks[6, 6] = tex_new.texid
    assert np.array_equal(new.block_ids, blocks), "the ground textures differ from what was painted"
    new_e = {e.instance_id: e for e in new.entities}
    assert b_id not in new_e and len(new.entities) == len(src.entities) - 1 + 3
    for eid, e in src_e.items():
        if eid in (a_id, b_id):
            continue
        n = new_e[eid]
        if eid == g_id:
            assert abs(n.position[1] - gz.z) < 1e-3
            assert np.allclose([n.position[0], n.position[2]], [e.position[0], e.position[2]], atol=1e-3)
            continue
        assert n.position == e.position and n.rotation == e.rotation and n.blueprint == e.blueprint, eid
        assert n.owner == (1 if eid == d_id else e.owner) and n.groups == e.groups, eid
    a_old, a_new = src_e[a_id], new_e[a_id]
    assert abs(a_new.position[0] - (a_old.position[0] + 5.0)) < 1e-3 and abs(a_new.position[1] - a_old.position[1]) < 1e-3
    assert a_new.blueprint == a_old.blueprint and a_new.rotation != a_old.rotation
    fresh = [e for e in new.entities if e.instance_id not in src_e]
    assert len(fresh) == 3
    copy = next(e for e in fresh if e.blueprint == src_e[c_id].blueprint)
    assert abs(copy.position[0] - (src_e[c_id].position[0] + 6.0)) < 1e-3 and abs(copy.position[2] - src_e[c_id].position[2]) < 1e-3
    alt = next(e for e in fresh if e.blueprint == src_e[k_id].blueprint and e is not copy)
    assert abs(alt.position[0] - src_e[k_id].position[0]) < 1e-3 and abs(alt.position[2] - (src_e[k_id].position[2] + 7.0)) < 1e-3
    bush = next(e for e in fresh if e.blueprint.upper().endswith("NATURE\\SAVBUSH_01.EBP"))
    assert abs(bush.position[0] - 20.0) < 1e-3 and abs(bush.position[2] - 20.0) < 1e-3
    print(f"   saved {len(saved)} bytes; heights, water, {int((new.block_ids != src.block_ids).sum())} blocks and entities as edited", flush=True)

    # ---- the saved file opens again with the edits in it
    C.fresh()
    res = bpy.ops.bz.import_map(filepath=path, load_models=False)
    assert "FINISHED" in res, res
    top2 = [c for c in bpy.data.collections if c.get("ic_map")][0]
    assert len(entity_by_id(top2)) == len(new.entities)
    co2 = terrain_z(X.terrain_of(top2))
    assert abs(co2[50 * w + 50, 2] - 255 * step) < 1e-3 and abs(co2[12 * w + 12, 2] - (src.heights[12, 12] * step + 3.0)) < 1e-3
    plan2 = X.build_plan(bpy.context, top2)
    assert not plan2.changed, plan2.lines()                       # the saved map is its own original now


# ---------------------------------------------------------------------------------------------------- 3. only what was edited
def test_nothing_else(game_dir):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    raw = game_map(game_dir, FACEOFF).read()
    out = C.tmpdir("mapsave_")

    top = import_map(FACEOFF)
    X.water_of(top).location.z += 1.0
    p = os.path.join(out, "water_only.sgb")                      # the water's edge decides where units walk: the pathfinding follows
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=p)
    with open(p, "rb") as f:
        saved = f.read()
    tags = changed_tags(raw, saved)
    assert tags and all(t.endswith("/TFAC") or t == "PFDR/NRML" for t in tags) and any(t.endswith("/TFAC") for t in tags), sorted(tags)
    p = os.path.join(out, "water_only_kept_paths.sgb")           # asked to leave the pathfinding alone: the terrain chunk alone changes
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=p, pathfinding="never")
    with open(p, "rb") as f:
        saved = f.read()
    tags = changed_tags(raw, saved)
    assert tags and all(t.endswith("/TFAC") for t in tags), f"a map with only its water moved and its pathfinding left alone changed more than the terrain chunk: {sorted(tags)}"
    print(f"   water only: the terrain chunk {sorted(tags)} and, unless left alone, the pathfinding (PFDR/NRML)", flush=True)

    top = import_map(FACEOFF)
    ents = entity_by_id(top)
    victim = next(e.instance_id for e in view.read(raw).entities if e.owner < 0)
    select_only([ents[victim]])
    assert "FINISHED" in bpy.ops.bz.map_set_owner(owner="0")
    p = os.path.join(out, "owner_only.sgb")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=p)
    with open(p, "rb") as f:
        saved = f.read()
    tags = changed_tags(raw, saved)
    print(f"   owner only: changed chunks {sorted(tags)}", flush=True)
    assert tags == {"PLYR/NRML"}, f"an owner change must change one player's record and nothing else: {sorted(tags)}"

    top = import_map(FACEOFF)                                   # a small move: the terrain and the pathfinding stay as they were
    ents = entity_by_id(top)
    ents[victim].location.x += 2.0
    p = os.path.join(out, "move_only.sgb")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=p)
    with open(p, "rb") as f:
        saved = f.read()
    tags = changed_tags(raw, saved)
    print(f"   one move: changed chunks {sorted(tags)}", flush=True)
    assert tags and "ENTT/NRML" in tags and not any(t.endswith("/TFAC") for t in tags), sorted(tags)


# ---------------------------------------------------------------------------------------------------- 4. refusals
def test_refusals(game_dir):
    from blender_native.sceneobj import ops_map_export as X
    out = C.tmpdir("mapsave_")
    top = import_map(FACEOFF)
    # the game's own folder, and any steamapps folder
    target = os.path.join(game_dir, "Data", "scenarios", "mp", "mine.sgb")
    msg = expect_error("game's own folder", lambda: bpy.ops.bz.export_map(filepath=target))
    assert not os.path.exists(target)
    fake = os.path.join(out, "steamapps", "common", "Somewhere", "mine.sgb")
    expect_error("Steam", lambda: bpy.ops.bz.export_map(filepath=fake))
    assert not os.path.exists(os.path.dirname(fake)), "a refused save must write nothing"
    # the file the map was opened from
    gm = game_map(game_dir, FACEOFF)
    own = os.path.join(out, "own.sgb")
    with open(own, "wb") as f:
        f.write(gm.read())
    C.fresh()
    assert "FINISHED" in bpy.ops.bz.import_map(filepath=own, load_models=False)
    expect_error("replace the map file this map was opened from", lambda: bpy.ops.bz.export_map(filepath=own))
    with open(own, "rb") as f:
        assert f.read() == gm.read()
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=own, replace_original=True)
    # points removed from the terrain
    top = import_map(FACEOFF)
    terrain = X.terrain_of(top)
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(terrain.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.verts[5]], context="VERTS")
    bm.to_mesh(terrain.data)
    bm.free()
    msg = expect_error("no longer has the grid", lambda: bpy.ops.bz.export_map(filepath=os.path.join(out, "broken.sgb")))
    assert "points" in msg and not os.path.exists(os.path.join(out, "broken.sgb"))
    expect_error("no longer has the grid", lambda: bpy.ops.bz.map_check_changes())
    # a file that cannot be written is said in words, and the map file (written last) is not there without its files
    top = import_map(FACEOFF)
    blocked = C.tmpdir("mapsave_blocked_")
    os.mkdir(os.path.join(blocked, "mine.dlg"))                     # a folder where the dialogue file has to go
    msg = expect_error("could not be written", lambda: bpy.ops.bz.export_map(filepath=os.path.join(blocked, "mine.sgb")))
    assert not os.path.exists(os.path.join(blocked, "mine.sgb")), "the map file must not be written when a file beside it failed"
    assert not [f for f in os.listdir(blocked) if f.endswith(".tmp")], "a failed write leaves no temporary file"
    # a copy of a creature combined in the map is left out and said so
    top = import_map("Engine.sga|scenarios/sp/mission1.sgb")
    combined = [o for o in X.entity_objects(top) if o.get("ic_combined_creature")]
    assert combined, "mission1 places creatures combined in the map"
    dup = combined[0].copy()
    for coll in combined[0].users_collection:
        coll.objects.link(dup)
    dup.location.x += 3.0
    plan = X.build_plan(bpy.context, top)
    assert any("combined inside the map were left out" in n for n in plan.notes), plan.lines()
    assert plan.added == 0 and not plan.changed, plan.lines()


# ---------------------------------------------------------------------------------------------------- 3b. in Edit Mode
def test_edit_mode_and_check(game_dir):
    """Changes made in Edit Mode are read without leaving it; "Check my changes" says what changed and saves nothing."""
    import bmesh
    from blender_native.sceneobj import ops_map_export as X
    top = import_map(FACEOFF)
    terrain = X.terrain_of(top)
    mv = X.read_source(bpy.context, top)[1]
    select_only([terrain])
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(terrain.data)
    bm.verts.ensure_lookup_table()
    bm.verts[100].co.z += 4.0                                       # in Edit Mode, not yet written to the mesh
    for f in bm.faces:
        f.select = False
    bm.faces.ensure_lookup_table()
    block = np.flatnonzero(mv.face_blocks() == 0)                   # the first 8 x 8 block of cells
    for i in block:
        bm.faces[int(i)].select = True
    bmesh.update_edit_mesh(terrain.data)
    res = bpy.ops.bz.map_add_ground(texture=NEW_GROUND)
    assert "FINISHED" in res, res
    slot = len(terrain.data.materials) - 1
    res = bpy.ops.bz.map_use_ground(slot=slot)
    assert "FINISHED" in res, res
    res = bpy.ops.bz.map_check_changes()
    assert "FINISHED" in res and terrain.mode == "EDIT", "Check my changes must not take the user out of Edit Mode"
    lines = X.LAST_CHECK[top.name]
    print("   " + "\n   ".join(lines), flush=True)
    assert any(l.startswith("Ground: 1 height points") for l in lines) and any("1 blocks" in l for l in lines), lines
    assert not any("Left " in l for l in lines)
    bpy.ops.object.mode_set(mode="OBJECT")
    mi = np.empty(len(terrain.data.polygons), np.int32)
    terrain.data.polygons.foreach_get("material_index", mi)
    assert (mi[block] == slot).all() and (mi != slot).sum() == len(mi) - len(block)
    # without Edit Mode there is nothing to flush, and a check changes nothing in the scene
    before = terrain_z(terrain).copy()
    bpy.ops.bz.map_check_changes()
    assert np.array_equal(before, terrain_z(terrain))
    # no changes: said so
    top = import_map(FACEOFF)
    bpy.ops.bz.map_check_changes()
    assert any("byte for byte" in l for l in X.LAST_CHECK[top.name]), X.LAST_CHECK[top.name]


# ---------------------------------------------------------------------------------------------------- 3c. small cases
def test_small_cases(game_dir):
    from ICParse.sgb import view
    from blender_native.sceneobj import ops_map_export as X
    # a scaled object is not moved, and the scale is said to be ignored
    top = import_map(FACEOFF)
    ents = entity_by_id(top)
    oid = next(iter(ents))
    ents[oid].scale = (2.0, 2.0, 2.0)
    plan = X.build_plan(bpy.context, top)
    assert plan.moved == 0 and not plan.changed and any("scaled" in n and "scale is ignored" in n for n in plan.notes), plan.lines()
    # the terrain object moved sideways: the heights are still read, and it is said
    top = import_map(FACEOFF)
    terrain = X.terrain_of(top)
    terrain.location.x += 40.0
    plan = X.build_plan(bpy.context, top)
    assert not plan.changed and any("moved sideways" in n for n in plan.notes), plan.lines()
    terrain.location.z += 3.0                                       # moved up: every height is 3 m higher
    plan = X.build_plan(bpy.context, top)
    assert plan.height_points == terrain.data.vertices.__len__() and abs(plan.height_largest - 3.0) < 1e-3, plan.lines()
    # a creature combined in the map (no blueprint file) keeps what it is when it is moved and given to another player
    raw = game_map(game_dir, "Engine.sga|scenarios/sp/mission1.sgb").read()
    src = view.read(raw)
    top = import_map("Engine.sga|scenarios/sp/mission1.sgb")
    combined = [o for o in X.entity_objects(top) if o.get("ic_combined_creature")]
    stock = [o for o in X.entity_objects(top) if o["ic_kind"] == "units" and not o.get("ic_combined_creature")]
    assert combined and stock
    cid, sid = combined[0]["ic_entity_id"], stock[0]["ic_entity_id"]
    combined[0].location.x += 2.0
    stock[0].location.y += 3.0
    out = C.tmpdir("mapsave_")
    path = os.path.join(out, "m1.sgb")
    assert "FINISHED" in bpy.ops.bz.export_map(filepath=path)
    with open(path, "rb") as f:
        new = view.read(f.read())
    old_e = {e.instance_id: e for e in src.entities}
    new_e = {e.instance_id: e for e in new.entities}
    assert len(new_e) == len(old_e) and new_e[cid].combined and new_e[cid].name == old_e[cid].name
    assert abs(new_e[cid].position[0] - (old_e[cid].position[0] + 2.0)) < 1e-3 and new_e[cid].owner == old_e[cid].owner
    assert abs(new_e[sid].position[2] - (old_e[sid].position[2] - 3.0)) < 1e-3 and new_e[sid].blueprint == old_e[sid].blueprint
    assert all(new_e[i].position == old_e[i].position for i in old_e if i not in (cid, sid))
    print("   a creature combined in mission1 and a stock one moved: nothing else of the 768 kB map changed", flush=True)


# ---------------------------------------------------------------------------------------------------- 5. the rest
def test_drop_folder_and_lists(game_dir):
    from blender_native.sceneobj import ops_map_export as X
    from blender_native.sceneobj import panels_map as P
    gm = game_map(game_dir, FACEOFF)
    top = import_map(FACEOFF)
    out = C.tmpdir("mapdrop_")
    res = bpy.ops.bz.export_map(filepath=os.path.join(out, "My Map.sgb"), ready_to_drop=True)
    assert "FINISHED" in res, res
    folder = os.path.join(out, "Data", "scenarios", "mp")
    assert {"my_map.sgb", "my_map.trg"} <= set(os.listdir(folder)), os.listdir(folder)
    with open(os.path.join(folder, "my_map.sgb"), "rb") as f:
        assert f.read() == gm.read()
    readme = open(os.path.join(out, "README.txt")).read()
    assert "Data/scenarios/mp/" in readme and "copy of the game" in readme and "Steam" in readme
    assert "renamed from 2p_faceoff to my_map" in readme, readme
    # the lists
    listing = X.game_listing(game_dir)
    assert NEW_GROUND in listing["ground"] and "arctic_01.tga" in listing["ground"] and len(listing["ground"]) >= 8, listing["ground"]
    refs = [r for r, _k in listing["blueprints"]]
    assert NEW_BLUEPRINT in refs and "art/ebps/structures/lab.ebp" in refs and len(refs) > 200
    assert X.blueprint_words(NEW_BLUEPRINT) == "nature / savbush_01"
    # the panel, drawn into a stand-in layout that rejects what a real draw would reject
    said = {}
    for name, draw in (("summary", P.draw_summary), ("terrain", P.draw_terrain), ("ground", P.draw_ground), ("objects", P.draw_objects),
                       ("save", P.draw_save)):
        lay = CheckedLayout()
        draw(lay, bpy.context, top)
        assert lay.calls and not lay.problems, (name, lay.problems)
        said[name] = lay
    texts = " | ".join(said["summary"].texts())
    assert texts.startswith("2p_faceoff") and "65 x 65 height points, 256 x 256 m" in texts, texts
    placed = next(t for t in said["summary"].texts() if t.startswith("Placed: "))
    assert sum(int(part.split()[0]) for part in placed[len("Placed: "):].split(", ")) == 164, placed
    ground = [d for k, d in said["ground"].calls if k == "operator" and d["idname"] == "bz.map_use_ground"]
    assert len(ground) == 3 and sorted(d["slot"] for d in ground) == [0, 1, 2], "one 'Use on selected faces' button per ground material"
    assert said["save"].operators().count("bz.export_map") == 2 and "bz.map_check_changes" in said["save"].operators()
    assert {"bz.map_add_object", "bz.map_set_owner", "bz.map_drop_to_ground"} <= set(said["objects"].operators())
    assert any("Water: the surface is at 15 m" in t for t in said["terrain"].texts()) or any("15 m" in t for t in said["terrain"].texts()), said["terrain"].texts()
    assert P.BZ_PT_map.poll(bpy.context) and not X.library_problem()
    C.fresh()
    assert not P.BZ_PT_map.poll(bpy.context), "the Map panel must stay away when the scene holds no map"
    funcs = bpy.types.TOPBAR_MT_file_export._dyn_ui_initialize()
    assert X.menu_export in funcs, "File > Export has no map entry"


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the map edit test needs the game's maps")
        return
    C.addon()
    with C.install_guard(game_dir):
        section("a map saved with no edits comes back byte for byte")
        test_no_edit(game_dir)
        section("every edit reaches the saved map")
        test_edits(game_dir)
        section("only what was edited changes")
        test_nothing_else(game_dir)
        section("Edit Mode and Check my changes")
        test_edit_mode_and_check(game_dir)
        section("small cases: scale, a moved terrain, creatures")
        test_small_cases(game_dir)
        section("refusals")
        test_refusals(game_dir)
        section("the ready-to-drop folder, the lists, the panel")
        test_drop_folder_and_lists(game_dir)
    print("SO_MAP_EDIT_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_edit", main)
