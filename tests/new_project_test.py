"""New Project (File > New > Impossible Creatures Project...): every project type starts as what it is and exports as what it is.
    blender --background --python blender_native/tests/new_project_test.py
Needs the game install (IC_INSTALL, or the Steam default) for the gatherer and structure donors. The add-on's Game folder preference
comes first: with it set, the game's own models are listed and read from that folder (checked with a folder of links to the
install's archives, and with an empty one)."""
import glob
import os
import sys
import types
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import traceback

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _so_common as C  # noqa: E402  (temp folders)
import blender_native  # noqa: E402
from blender_native import actions as bzactions, core, newproject, store  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402

D = C.tmpdir("np_")
_log = open("/tmp/bz_new_project_log.txt", "w")
DONOR_CREATURE = os.environ.get("BZ_NP_DONOR") or os.path.join(REPO, "Assets", "engine_stock", "cheetah.sgm")
HENCH = "art/ebps/gatherers/henchmen.sgm"
LAB = "art/ebps/structures/lab.sgm"


def say(*a):
    _log.write(" ".join(str(x) for x in a) + "\n"); _log.flush()


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    if os.path.isdir(D):
        for f in os.listdir(D):
            os.remove(os.path.join(D, f))
    os.makedirs(D, exist_ok=True)


def active():
    return bpy.context.view_layer.objects.active


def new(name, **kw):
    path = f"{D}/{name}.sgm"
    return path, bpy.ops.bz.new_project(filepath=path, **kw)


def check_game_folder_preference():
    """The Game folder preference is where New Project lists and reads the game's own models from, before $IC_INSTALL."""
    from blender_native.sceneobj import prefs
    real, source = newproject.game_dir()                  # the preference is not set: $IC_INSTALL or the usual Steam folder
    assert real and source in ("IC_INSTALL", "steam default"), (real, source)
    archives = newproject.game_archives()
    assert archives and all(os.path.dirname(a) == real for a in archives), archives
    entry = bpy.context.preferences.addons.new()          # a background Blender does not add the add-on's entry itself
    entry.module = prefs.ADDON_ID
    try:
        live = prefs.get()
        assert type(live).__name__ == "BZ_AP_prefs", type(live)
        empty = C.tmpdir("np_nogame_")
        live.game_dir = empty
        assert newproject.game_dir() == (empty, "preferences") and newproject.game_archives() == []
        assert newproject.scene_donors("CHARACTER") == [], "the donors must come from the Game folder setting, not from IC_INSTALL"
        assert newproject.game_dir_line() == f"Game folder: {empty} (from the Game folder in the add-on's preferences)"
        mine = C.tmpdir("np_game_")                       # links to the install's archives: read, never written
        for a in archives:
            os.symlink(a, os.path.join(mine, os.path.basename(a)))
        os.symlink(archives[0], os.path.join(mine, "SonicMod_sonicgatherer.sga"))   # a mod's archive: never offered as a starting point
        live.game_dir = mine
        donors = newproject.scene_donors("CHARACTER")
        assert donors and all(os.path.dirname(a) == mine for a, _m in donors), donors[:3]
        assert not any("sonicmod" in a.lower() for a, _m in donors), "a mod archive's models must not be listed"
        assert not any("sonicmod" in a.lower() for a in newproject.game_archives())
        assert any("sonicmod" in a.lower() for a in newproject.game_archives(stock_only=False))
        stub = types.SimpleNamespace(layout=C.FakeLayout(), project_type="CHARACTER", project_name="x", donor=HENCH, anim_source="OWN")
        newproject.BZ_OT_new_project.draw(stub, bpy.context)               # the dialog says which folder the list comes from
        shown = " ".join(stub.layout.texts())
        assert f"Game folder: {mine} (from the Game folder in the add-on's preferences)" in shown, stub.layout.texts()
        hench = [d for d in donors if d[1] == HENCH]
        assert hench, "no henchmen in the Game folder's archives"
        sgm, ebp = newproject.read_donor(*hench[0])
        fresh()
        looked_in = []                                    # the opened project's textures are looked up in the Game folder too
        texture_source = core.scene_texture_source
        core.scene_texture_source = lambda sgm_dir, data_root=None: (looked_in.append(data_root), texture_source(sgm_dir, data_root))[1]
        try:
            p, res = new("PrefHench", project_type="CHARACTER", donor=HENCH, anim_source="OWN")
        finally:
            core.scene_texture_source = texture_source
        assert "FINISHED" in res, res
        assert open(p, "rb").read() == sgm and ebp is not None and open(p[:-4] + ".ebp", "rb").read() == ebp
        assert looked_in and all(d == mine for d in looked_in), looked_in
        live.game_dir = os.path.join(mine, "missing")     # a folder that does not exist is not used
        assert newproject.game_dir() == (real, source)
    finally:
        bpy.context.preferences.addons.remove(entry)
    assert newproject.game_dir() == (real, source)
    say(f"Game folder preference: an empty folder lists no donors; a folder of links lists {len(donors)} and the project reads its "
        f"donor and its textures from there; unset or missing, {source} ({real}) is used")


def run():
    blender_native.register()
    assert newproject.menu_new in bpy.types.TOPBAR_MT_file_new.draw._draw_funcs, "no File > New entry"
    check_game_folder_preference()
    assert any(x[0] == HENCH for x in [(m, a) for a, m in newproject.scene_donors("CHARACTER")]), "no henchmen donor found"
    assert any(m == LAB for _, m in newproject.scene_donors("STRUCTURE")), "no lab donor found"

    # 1. a creature on the default skeleton, with the template clips
    fresh()
    p, res = new("Newt", project_type="CREATURE", project_name="Newt", donor="DEFAULT", anim_source="OWN")
    assert "FINISHED" in res, res
    obj = active()
    arm = store.rig(obj)
    assert store.is_native(obj) and not obj.get("bz_scene") and obj["bz_project"] == "CREATURE"
    assert len(arm.data.bones) == 48 and len(arm["bz_clip_actions"]) == 9
    say("creature + own clips: 48 bones, 9 Actions")

    # 2. a creature with no clips at all, exported through the creature exporter
    fresh()
    p, res = new("Bare", project_type="CREATURE", project_name="Bare", donor="DEFAULT", anim_source="NONE")
    assert "FINISHED" in res
    obj = active(); arm = store.rig(obj)
    assert len(arm.data.bones) == 48 and not bzactions.actions_of(arm) and list(obj.get("bz_anims", [])) == []
    out = f"{D}/Bare_out.sgm"
    bpy.ops.bz.export_sgm(filepath=out)
    assert len(SP.read(out).animations) == 0 and open(out, "rb").read() == open(p, "rb").read()
    say("creature, no clips: exports byte-identical with 0 clips")

    # 3. a creature that borrows another creature's clips by bone name
    fresh()
    donor = core.read_source(DONOR_CREATURE)
    p, res = new("Borrower", project_type="CREATURE", project_name="Borrower", donor="DEFAULT", anim_source="BORROW",
                 borrow="FILE", borrow_file=DONOR_CREATURE)
    assert "FINISHED" in res, res
    obj = active(); arm = store.rig(obj)
    acts = {a.name: a for a in bzactions.actions_of(arm)}
    assert all(a.get(bzactions.KEY_CLIP) is None for a in acts.values()), "a borrowed clip must be a new Action, not a source clip"
    assert set(acts) == {a.name for a in donor.animations}, (sorted(acts), [a.name for a in donor.animations])
    assert list(obj["bz_anims"]) == [a.name for a in donor.animations]
    t_names = [b.name for b in arm.data.bones]
    d_names = [b.name or f"bone{i}" for i, b in enumerate(donor.bones)]
    shared = [n for n in t_names if n.lower() in {x.lower() for x in d_names}]
    say(f"borrowed {len(acts)} clips; {len(shared)} of 48 bones share a name with {os.path.basename(DONOR_CREATURE)}")
    assert len(shared) >= 20, "the donor shares too few bones for the test to mean anything"
    # a borrowed pose is the donor's pose in the bone's own frame: check one bone at the middle of the first clip
    clip = donor.animations[0]
    K = max(1, int(clip.frame_count))
    frames = [R.animated_bone_world(donor, clip, f / K) for f in range(K + 1)]
    sd = bzactions.basis_samples(d_names, [None if b.parent is None or int(b.parent) < 0 else int(b.parent) for b in donor.bones],
                                 R.bind_world_matrices(donor), frames)
    arm.animation_data.action = acts[clip.name]
    mid = 1 + K // 2
    bpy.context.scene.frame_set(mid)
    worst = 0.0
    for n in shared:
        di = [x.lower() for x in d_names].index(n.lower())
        want = sd[K // 2, di, 3:7]
        got = np.array(arm.pose.bones[n].rotation_quaternion[:])
        worst = max(worst, min(float(np.abs(got - want).max()), float(np.abs(got + want).max())))
    say(f"borrowed rotations vs the donor's, clip {clip.name} frame {mid}: worst {worst:.2e}")
    assert worst < 1e-5, worst
    out = f"{D}/Borrower_out.sgm"
    bpy.ops.bz.export_sgm(filepath=out)
    m = SP.read(out)
    assert [a.name for a in m.animations] == [a.name for a in donor.animations], [a.name for a in m.animations]
    assert [int(a.frame_count) for a in m.animations] == [int(a.frame_count) for a in donor.animations]
    say("borrowed clips export as clips of the same names and lengths")
    # asking for only some of them
    fresh()
    p, res = new("Some", project_type="CREATURE", donor="DEFAULT", anim_source="BORROW", borrow="FILE", borrow_file=DONOR_CREATURE,
                 borrow_names=f"{donor.animations[0].name}, nonesuch")
    assert "FINISHED" in res
    assert [a.name for a in bzactions.actions_of(store.rig(active()))] == [donor.animations[0].name]

    # 4. a gatherer from the stock henchman: a scene object with its own skeleton, skin and clips
    fresh()
    p, res = new("Hench", project_type="CHARACTER", donor=HENCH, anim_source="OWN")
    assert "FINISHED" in res, res
    obj = active(); arm = store.rig(obj)
    data = core.load_scene(p)
    assert obj.get("bz_scene") and not store.is_native(obj) and obj["bz_project"] == "CHARACTER"
    assert os.path.isfile(p[:-4] + ".ebp"), "the donor's controller was not written beside the project"
    assert len(arm.data.bones) == len(data.bone_names) and len(arm["bz_clip_actions"]) == len(data.animations) > 0
    out = f"{D}/Hench_out.sgm"
    bpy.ops.bz.export_scene(filepath=out)
    assert open(out, "rb").read() == open(p, "rb").read(), "untouched scene-animation export is not byte-identical"
    say(f"gatherer: {len(arm.data.bones)} bones, {len(data.animations)} clips, controller copied, export byte-identical")

    # 5. the same with no clips: the export drops them
    fresh()
    p, res = new("HenchBare", project_type="CHARACTER", donor=HENCH, anim_source="NONE")
    assert "FINISHED" in res
    obj = active(); arm = store.rig(obj)
    assert not bzactions.actions_of(arm) and list(obj.get("bz_anims", [])) == []
    out = f"{D}/HenchBare_out.sgm"
    bpy.ops.bz.export_scene(filepath=out)
    after = core.load_scene(out)
    assert len(after.animations) == 0 and len(after.bone_names) == len(core.load_scene(p).bone_names)
    say("gatherer, no clips: exported with 0 clips and the same skeleton")

    # 6. a structure: rigid parts on bones, another kind of scene object
    fresh()
    p, res = new("Lab", project_type="STRUCTURE", donor=LAB)
    assert "FINISHED" in res, res
    obj = active()
    assert obj.get("bz_scene") and obj["bz_project"] == "STRUCTURE" and obj.get("bz_scene_part") is not None
    parts = [o for o in bpy.data.objects if o.get("bz_scene")]
    assert len(parts) >= 1
    out = f"{D}/Lab_out.sgm"
    assert "FINISHED" in bpy.ops.bz.export_scene_geometry(filepath=out)
    say(f"structure: {len(parts)} parts; the geometry exporter works on the project")

    # 7. what must be refused, with a reason, and leave nothing behind
    fresh()
    scene_src, res = new("HenchSrc", project_type="CHARACTER", donor=HENCH)      # a real scene file, to offer as a "creature" to borrow from
    assert "FINISHED" in res

    def refused(name, why, **kw):
        path = f"{D}/{name}.sgm"
        try:
            bpy.ops.bz.new_project(filepath=path, **kw)
        except RuntimeError as e:
            assert why in str(e), (why, str(e))
            assert not os.path.exists(path) and not os.path.exists(path[:-4] + ".ebp"), f"{name}: files left behind"
            return
        raise AssertionError(f"{name}: the project was accepted")
    refused("Wrong", "is a creature, not a gatherer, character, structure or prop", project_type="STRUCTURE", donor="FILE",
            donor_file=DONOR_CREATURE)
    refused("NoBorrow", "Borrowing animations works only for creature projects", project_type="STRUCTURE", donor=LAB,
            anim_source="BORROW", borrow="FILE", borrow_file=DONOR_CREATURE)
    refused("BadBorrow", "Cannot read the creature to borrow from", project_type="CREATURE", donor="DEFAULT", anim_source="BORROW",
            borrow="FILE", borrow_file=DONOR_CREATURE + ".missing")
    refused("NoDonor", "Choose the .sgm file to start from", project_type="CHARACTER", donor="FILE", donor_file="")
    refused("SceneAsBorrow", "not a creature with animations", project_type="CREATURE", donor="DEFAULT", anim_source="BORROW",
            borrow="FILE", borrow_file=scene_src)
    refused("Meshless", "no geometry", project_type="STRUCTURE", donor="art/ebps/structures/gyro_copter_upton.sgm")
    say("a creature file as a structure, borrowing for a scene object, a missing donor, a scene file as a clip donor, a donor with no mesh: all refused with a reason, no file left")
    blender_native.unregister()


try:
    run()
    open("/tmp/bz_new_project_done.txt", "w").write("PASS\n")
    say("PASS")
    C.cleanup()
except Exception:
    say(traceback.format_exc())
    open("/tmp/bz_new_project_done.txt", "w").write("FAIL\n")
    print(traceback.format_exc())
    C.cleanup()
    sys.exit(1)
