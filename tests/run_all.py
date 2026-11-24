#!/usr/bin/env python3
"""Run every blender_native test headlessly and summarise.

    .venv/bin/python3 blender_native/tests/run_all.py            # all tests
    .venv/bin/python3 blender_native/tests/run_all.py -k skin    # substring filter
    .venv/bin/python3 blender_native/tests/run_all.py -k so_     # the sceneobj suites (tests/so_*_test.py, found by name)
    .venv/bin/python3 blender_native/tests/run_all.py --list
    .venv/bin/python3 blender_native/tests/run_all.py --combine-full   # + every stock creature x both roles (offline)
    .venv/bin/python3 blender_native/tests/run_all.py --engine         # + Object Editor / ComboTest.exe (Proton)

Pure-python tests run under this interpreter (needs ICParse + construct + numpy: `uv sync`).
In-Blender tests run under `blender --background --factory-startup --python-exit-code 1 --python <test>`
(override the binary with $BLENDER). Each test is a script that asserts on failure and exits non-zero
(after writing its /tmp/bz_*_done.txt), so its exit code is the verdict.
The repo root is derived from this file's location (override with $PYIC_REPO).
"""
from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(HERE))
BLENDER = os.environ.get("BLENDER") or shutil.which("blender") or "blender"
sys.path.insert(0, HERE)
import _so_common  # noqa: E402  (stdlib only: the machine-wide Blender launch lock)
import so_suite  # noqa: E402  (finds tests/so_*_test.py)

PURE = ["headless_identity.py", "fastsurf_parity.py", "edittools_test.py", "extrude_test.py", "export_builder_test.py",
        "ring_test.py", "ring_start_test.py", "combine_offline_test.py", "boner_test.py", "limb_rows_test.py",
        "limb_bones_test.py",
        "conn_rows_test.py", "stub_socket_test.py", "bone_rename_test.py", "bone_add_test.py", "bone_topology_test.py",
        "fit_mesh_test.py",
        # builds the distributable extension and drives Blender itself (a clean BLENDER_USER_RESOURCES), so it runs
        # under this interpreter like the pure tests. First run needs the network for the PyPI wheels (cached in
        # dist/_wheels afterwards); with the zip already built it is ~8 s.
        "extension_install_test.py",
        "patchrules_test.py"]
IN_BLENDER = ["blender_smoke.py", "ebp_import_test.py", "scene_import_test.py", "blender_extrude_test.py", "convert_test.py",
              "anim_encode_test.py", "rig_anim_test.py", "anim_actions_test.py", "scene_anim_export_test.py", "skeleton_test.py", "skin_test.py",
              "limb_auth_test.py", "texture_test.py", "uv_test.py", "new_creature_test.py", "new_project_test.py", "scene_retopo_test.py", "scene_geometry_export_test.py", "blueprint_poc_test.py", "combine_preview_test.py",
              "guard_test.py", "skin_boner_test.py", "boner_limbs_test.py", "boner_limb_bones_test.py",
              "boner_connections_test.py", "boner_ring_start_test.py", "boner_stub_test.py", "boner_rename_test.py",
              "boner_add_bone_test.py", "boner_bone_topology_test.py",
              "sculpt_refit_test.py", "blender_fit_mesh_test.py", "retarget_test.py", "landmark_retarget_test.py",
              "patch_feel_test.py",
              "patch_select_test.py", "topology_ops_test.py", "texture_transfer_test.py", "trim_stray_test.py",
              "fit_to_rig_test.py", "action_guard_test.py", "gn_surface_test.py"]
# the sceneobj suites are found by name (tests/so_suite.py): a new slice's test needs no line here
PURE += so_suite.PURE
IN_BLENDER += so_suite.IN_BLENDER
# What the suites export (one file per edit path); --engine loads each in the Object Editor.
# Expected editor verdicts that are not LOADED: the extrude test deliberately exports one open
# (extruded, uncapped) patch, which the editor must reject as dangling edges.
ENGINE_EXPECT = {"/tmp/bz_ex1.sgm": "FATAL",
                 "/tmp/bz_boner_b5_extrude.sgm": "FATAL",      # the open snail + one extruded patch (skin_boner_test)
                 # the cheetah with 2 patches deleted (patch_select_test): 6 dangling edges, the Object Editor refuses
                 # an open mesh (measured 2026-09-15: FATAL in 6.3 s) and Validate says so before the export
                 "/tmp/bz_patch_delete_cheetah.sgm": "FATAL"}
# The real combiner (rdn-combotest): a creature built from nothing must combine both ways -- as the torso
# taking stock limbs, and as a limb donor on a stock torso. (torso, donor, parts from the donor)
COMBINE_CHECKS = [("/tmp/bz_new_creature_edit.sgm", "Assets/engine_stock/cheetah.sgm", ("head", "tail")),
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_new_creature_edit.sgm", ("head", "rear_legs")),
                  # a front-leg ring re-picked in scrambled order (limb_auth_test): it must weld like the stock order
                  ("/tmp/bz_ring_repick.sgm", "Assets/engine_stock/cheetah.sgm", ("front_legs",)),
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_ring_repick.sgm", ("front_legs",)),
                  # a renamed limb (boner_limbs_test: cheetah's Head limb called Cheetah_Noggin): limbs match by class
                  ("/tmp/bz_boner_b1_rename.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_boner_b1_rename.sgm", ("head",)),
                  # a limb with no bones (boner_limbs_test B: New Creature + a boneless LMLeg with torso patches): the
                  # engine's model loader says 'limbs are boneless!' and ComboTest.exe must NOT take it (COMBINE_MUST_FAIL)
                  ("/tmp/bz_boner_b1_boneless.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  # changed limb bone lists (boner_limb_bones_test C: New Creature's front legs re-rooted at front_leg1,
                  # front_leg_root in the Torso): the edited legs must still graft as a donor onto a stock torso
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_boner_b2_reroot.sgm", ("front_legs",)),
                  # a bone owning a leg's points moved to the Torso (boner_limb_bones_test B: cheetah's Front_leg1_l, 46
                  # LFLeg points; Validate: STOCK + COMBINE ERROR): as the front-legs donor ComboTest.exe must fail
                  # (COMBINE_MUST_FAIL). As the torso it exports two front_leg1_l bones with the leg 0.21 m off -- no
                  # pass/fail pair can pin that, the precheck's 'lists the standard root' ERROR judges it offline.
                  ("Assets/engine_stock/bull.sgm", "/tmp/bz_boner_b2_lf_torso.sgm", ("front_legs",)),
                  # a connection created in Blender (boner_connections_test C: New Creature's Head connection deleted,
                  # created again for the Head limb, its ring picked scrambled, default host): as the torso it must take
                  # a stock head at that socket
                  ("/tmp/bz_boner_b3_head.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_boner_b3_head.sgm", ("head",)),
                  # the two ways ComboTest.exe refuses a creature in either role (COMBINE_MUST_FAIL; the precheck's
                  # picker_refusals ERRORs): cheetah's LFLeg alone re-classed Mid, cheetah's LFArm stub socket deleted
                  ("/tmp/bz_boner_b3_onesided.sgm", "Assets/engine_stock/cheetah.sgm", ("front_legs",)),
                  ("/tmp/bz_boner_b3_noarm.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  # stub sockets made by Add Stub Socket (boner_stub_test C: cheetah without its wing and arm stubs --
                  # limbs, connections, bones -- given them back with CREATED bones): as the torso it must take a
                  # donor's wings and front arms at those sockets
                  ("/tmp/bz_boner_b4_created.sgm", "Assets/engine_stock/eagle.sgm", ("wings",)),
                  ("/tmp/bz_boner_b4_created.sgm", "Assets/engine_stock/praying_mantis.sgm", ("front_arms",)),
                  # (boner_stub_test B: cheetah's arm stubs re-added on their existing bones)
                  ("/tmp/bz_boner_b4_arms.sgm", "Assets/engine_stock/praying_mantis.sgm", ("front_arms",)),
                  # renamed bones (boner_rename_test D: eagle's feather2_L -> quill2_L in its side wings, Rear_endleg_L ->
                  # rear_heel_l in its rear legs, neither a name the game looks up): as the wings donor on a stock torso,
                  # and as the torso keeping its renamed wings and legs while it takes a stock head
                  ("Assets/engine_stock/cheetah.sgm", "/tmp/bz_boner_b6_eagle.sgm", ("wings",)),
                  ("/tmp/bz_boner_b6_eagle.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  # tail1 renamed (boner_rename_test F: cheetah tail1 -> tail_a, a name the combiner looks up): with
                  # a tail replaced, in either role, ComboTest.exe tears the tail seam (COMBINE_MUST_FLAG)
                  ("/tmp/bz_boner_b6_tail1.sgm", "Assets/engine_stock/bull.sgm", ("tail",)),
                  ("Assets/engine_stock/bull.sgm", "/tmp/bz_boner_b6_tail1.sgm", ("tail",)),
                  # an added bone (boner_add_bone_test B: horn_l under cheetah's head, in the Head limb, 8 head points
                  # skinned to it, inserted before the stub bones): as the torso keeping its horned head while it takes
                  # a stock tail, and as the head donor grafting the horned head onto a stock torso
                  ("/tmp/bz_boner_b7_horn.sgm", "Assets/engine_stock/bull.sgm", ("tail",)),
                  ("Assets/engine_stock/bull.sgm", "/tmp/bz_boner_b7_horn.sgm", ("head",)),
                  # a retargeted creature (retarget_test B: cheetah fitted to a bulged, longer-legged bake with its
                  # rings pinned and bones moved): as the torso it must take a stock head at its unchanged socket
                  ("/tmp/bz_retarget_cheetah.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  # a SOURCE bone deleted (boner_bone_topology_test A: cheetah's ear_L deleted with X in the rig's Edit
                  # Mode: its 50 points on the head, endear_l under head, 15 kept clips without its track) and neck4
                  # inserted between neck3 and head (C: Add Bone + Ctrl+P: the head's tracks re-expressed, the Neck IK
                  # chain completed): both as the torso taking a stock cheetah head and tail
                  ("/tmp/bz_boner_c8_delete.sgm", "Assets/engine_stock/cheetah.sgm", ("head", "tail")),
                  ("/tmp/bz_boner_c8_neck4.sgm", "Assets/engine_stock/cheetah.sgm", ("head", "tail")),
                  # a SUBDIVIDED creature (topology_ops_test B: a cheetah rear-leg patch split into 4 and the split
                  # propagated along both strips: 288 -> 314 patches, the surface unchanged): as the torso, with its
                  # subdivided legs kept, it must still take a stock head at its untouched socket
                  ("/tmp/bz_topo_subdiv_cheetah.sgm", "Assets/engine_stock/cheetah.sgm", ("head",)),
                  # Optimise Ring Start (boner_ring_start_test B): the cheetah's L Front Leg socket ring started 2
                  # corners out and rotated back. The mis-started file (bz_ring_start_bad.sgm) COMBINES too -- the
                  # engine welds it by index without complaint, 50 of the export's 1477 vertices up to 87 mm off and
                  # the worst triangle edge 428 -> 722 mm, measured 2026-09-16 -- so only the fixed one is pinned here;
                  # the seam evidence is in the Progress note.
                  ("/tmp/bz_ring_start_fixed.sgm", "Assets/engine_stock/bull.sgm", ("front_legs",))]
# COMBINE_CHECKS that the real ComboTest.exe must fail: rdn-combotest exits 1 and writes no export, and its record's log
# has, for each group of words, a line with all of them.
# * boneless: the model loader's 'limbs are boneless!' and a "... not taken by the picker" line (harness 2026-09-14: the
#   picker never takes a creature with a boneless limb, stock eagle with one stub's bones emptied included; the Object
#   Editor still loads it). Either slot: combomix.c logs 'Creature1' BEFORE the model loads, which rdnharness pick()
#   counts as taken; the tool then sits alive with no combination and its refusal unflushed, and the next pick (animal
#   2) ends it, writing 'limbs are boneless!' (probed 2026-09-15: 'animal 2' 5/5 runs; a pick that waited for
#   'Combination complete.' says 'animal 1' but loses the refusal line). A crash lacks the loader's line, a success
#   exits 0 with an export;
# * lf_torso: 'Assigning Vertex to the root bone, probably bad!!!' (56 x) and then 'dictionary.c: Assertion of (dbBreak)
#   failed' (harness 2026-09-15, twice, on these exact bytes; the unedited cheetah exports and bone_truth passes);
# * onesided: a Left limb (0x20021) without its Right mirror: 'combinerimp.c: Assertion of (LimbToAdd.m_pLimb) failed'
#   (CombinerImp.cpp:918, FUN_00449565); both front legs Mid exports;
# * noarm: a limb (the LFArm stub) whose class no connection carries: 'Assertion of (pDesLimb->m_pLimbConn) failed'
#   (CombinerImp.cpp:180, FUN_0044797f) -- the whole creature is refused, whatever the parts.
COMBINE_MUST_FAIL = {
    ("/tmp/bz_boner_b1_boneless.sgm", "Assets/engine_stock/cheetah.sgm"): (("modelload.c", "limbs are boneless!"),
                                                                             ("not taken by the picker",)),
    ("Assets/engine_stock/bull.sgm", "/tmp/bz_boner_b2_lf_torso.sgm"): (("Assigning Vertex to the root bone",),
                                                                          ("Assertion of (dbBreak) failed",)),
    ("/tmp/bz_boner_b3_onesided.sgm", "Assets/engine_stock/cheetah.sgm"): (("Assertion of (LimbToAdd.m_pLimb)",),),
    ("/tmp/bz_boner_b3_noarm.sgm", "Assets/engine_stock/cheetah.sgm"): (("Assertion of (pDesLimb->m_pLimbConn)",),),
}
# COMBINE_CHECKS that the real ComboTest.exe exports BROKEN, in a way our combiner does not reproduce: it must export
# (exit 0) with each log marker present, our pair check (bone_truth --pair) must FAIL on the engine vertices, and our
# offline check of that design (combotest.validate.run_design, what Validate's combine tier runs) must report the
# ERROR containing the words -- so the offline flag stays tied to what the engine really does.
# * tail1: the torso's tail-ring vertices name a bone the combined skeleton lacks (tail_a left with the replaced tail,
#   or the bull's tail1 with a tail_a donor tail): 252 'Assigning Vertex to the root bone' lines and 50 vertices up to
#   0.27 m off the stock control (harness 2026-09-15, both roles; engine vertices 0.25 / 0.23 m off our surface: the
#   engine's position for those vertices is not modelled, the design is an ERROR instead).
COMBINE_MUST_FLAG = {
    ("/tmp/bz_boner_b6_tail1.sgm", "Assets/engine_stock/bull.sgm"): (("Assigning Vertex to the root bone",),
                                                                       ("torso seam vertices", "'tail_a'", "root bone")),
    ("Assets/engine_stock/bull.sgm", "/tmp/bz_boner_b6_tail1.sgm"): (("Assigning Vertex to the root bone",),
                                                                       ("torso seam vertices", "'tail1'", "root bone")),
}
ENGINE_EXPORTS = ["/tmp/bz_snail_edit.sgm", "/tmp/bz_sn1.sgm", "/tmp/bz_sk1.sgm", "/tmp/bz_ex1.sgm",
                  # Transfer Texture (texture_transfer_test): a checker-sphere creature and the retargeted pillbug with
                  # their one atlas texture (per-patch cells, 256-colour TXMP)
                  "/tmp/bz_transfer_sphere.sgm", "/tmp/bz_pillbug_textured.sgm",
                  "/tmp/bz_la2.sgm", "/tmp/bz_uv1.sgm", "/tmp/bz_tx1.sgm", "/tmp/bz_anim_out.sgm",
                  "/tmp/bz_ebp_embedded_edit.sgm", "/tmp/bz_ebp_proxy_out.sgm", "/tmp/bz_converted_out.sgm",
                  "/tmp/bz_actions_out1.sgm", "/tmp/bz_new_creature_edit.sgm",
                  "/tmp/bz_edits_all.sgm",                   # every edit at once on the ungrown snail (blender_extrude_test)
                  "/tmp/bz_ring_repick.sgm",                 # a New Creature ring re-picked scrambled (limb_auth_test)
                  "/tmp/bz_combo_materialized.sgm",          # our combiner's output (combine_preview_test) must load too
                  # Boner skinning (skin_boner_test): Remove -> Auto Skin on New Creature, Assign through the active
                  # bone on the snail, a reskin keeping woolly_mammoth's unowned points, Extrude + Auto Skin (open)
                  "/tmp/bz_boner_b5_autoskin.sgm", "/tmp/bz_boner_b5_skin.sgm", "/tmp/bz_boner_b5_woolly.sgm",
                  "/tmp/bz_boner_b5_extrude.sgm",
                  # Boner limbs (boner_limbs_test): cheetah's head renamed; New Creature + a boneless LMLeg with torso
                  # patches (OE warns 'limbs are boneless!', loads); LFArm Non-Swappable; a cheetah without arms given
                  # stub arms back
                  "/tmp/bz_boner_b1_rename.sgm", "/tmp/bz_boner_b1_boneless.sgm", "/tmp/bz_boner_b1_noswap.sgm",
                  "/tmp/bz_boner_b1_arms.sgm",
                  # Boner limb bone lists (boner_limb_bones_test): cheetah's nose in no limb, nose moved to the Torso;
                  # New Creature's front legs re-rooted at front_leg1 (front_leg_root in the Torso)
                  "/tmp/bz_boner_b2_nose.sgm", "/tmp/bz_boner_b2_move.sgm", "/tmp/bz_boner_b2_reroot.sgm",
                  # cheetah's Front_leg1_l in the Torso (loads; ComboTest.exe fails it as a donor: COMBINE_MUST_FAIL)
                  "/tmp/bz_boner_b2_lf_torso.sgm",
                  # Boner connections (boner_connections_test): cheetah's LTWing re-classed Side with its connection
                  # (the OE warns: duplicate class); New Creature's Head connection re-created; its LFArm limb and
                  # connection re-created (stub ring picked)
                  "/tmp/bz_boner_b3_reclass.sgm", "/tmp/bz_boner_b3_head.sgm", "/tmp/bz_boner_b3_arm.sgm",
                  # the horse's antler connection re-created from the menu path (Auto = Head); cheetah's LFLeg alone
                  # re-classed Mid and its LFArm stub socket deleted (both load; ComboTest.exe refuses them)
                  "/tmp/bz_boner_b3_antler.sgm", "/tmp/bz_boner_b3_onesided.sgm", "/tmp/bz_boner_b3_noarm.sgm",
                  # Add Stub Socket (boner_stub_test): cheetah's arm stubs re-added on their existing bones; cheetah
                  # without wing / arm stubs given all six back with created stub bones
                  "/tmp/bz_boner_b4_arms.sgm", "/tmp/bz_boner_b4_created.sgm",
                  # Rename Bone (boner_rename_test): cheetah's ear_L -> ear_left (Head limb, LEar IK chain, clip tracks);
                  # eagle's Rear_endleg_L -> rear_heel_l and feather2_L -> quill2_L (limbs, LRLeg IK chain, tracks)
                  "/tmp/bz_boner_b6_rename.sgm", "/tmp/bz_boner_b6_eagle.sgm",
                  # cheetah's tail1 -> tail_a (loads; ComboTest.exe tears a replaced tail's seam: COMBINE_MUST_FLAG)
                  "/tmp/bz_boner_b6_tail1.sgm",
                  # Add Bone (boner_add_bone_test): cheetah + horn_l under head owning 8 head points (all clips kept);
                  # + Blender's extruded endtail.001 adopted and O_growl re-encoded with a horn_l track
                  "/tmp/bz_boner_b7_horn.sgm", "/tmp/bz_boner_b7_extrude.sgm",
                  # Sculpt Surface / Refit Patches (sculpt_refit_test): cheetah with a 6 cm bump grabbed on its back
                  "/tmp/bz_sculpt_cheetah.sgm",
                  # Patch feel (patch_feel_test): cheetah with a corner dragged 5 cm up, its handles carried and the
                  # touched patches' interiors on the Auto Interior rule
                  "/tmp/bz_patch_feel_cheetah.sgm",
                  # Patch selection (patch_select_test): cheetah with 2 patches deleted through Delete Patch (open:
                  # ENGINE_EXPECT FATAL)
                  "/tmp/bz_patch_delete_cheetah.sgm",
                  # Fit Patches to Mesh (blender_fit_mesh_test): Suzanne's skin fitted with a QuadriFlow layout at
                  # budget 300, and a quad sphere fitted with its own quads
                  "/tmp/bz_fit_suzanne.sgm", "/tmp/bz_fit_sphere.sgm",
                  # Retarget Creature to Mesh (retarget_test): cheetah fitted to its own bake with a back bulge and
                  # 10 % longer legs, rings pinned, bones moved into the new skin; and the landmark-guided retarget
                  # (landmark_retarget_test B: legs 25 % apart and the head 10 cm up, TPS pre-warp of net and bones)
                  "/tmp/bz_retarget_cheetah.sgm", "/tmp/bz_landmark_cheetah.sgm",
                  # Delete / re-parent source bones (boner_bone_topology_test): cheetah without ear_L (its points on the
                  # head, kept clips without its track), neck4 inserted between neck3 and head (kept clips re-tracked,
                  # Neck chain completed), nose re-parented under jaw1 (a genuine re-parent: its tracks re-expressed)
                  "/tmp/bz_boner_c8_delete.sgm", "/tmp/bz_boner_c8_neck4.sgm", "/tmp/bz_boner_c8_nose.sgm",
                  # B7 topology tools (topology_ops_test): the cheetah with 2 patches deleted and the hole FILLED
                  # (watertight again, unlike bz_patch_delete_cheetah), a rear-leg patch subdivided with the split
                  # propagated along its strips, a patch deleted / re-grown with Add Quad / welded back, a New Creature
                  # scaffold attached (two shells in one creature), and a half cheetah mirrored whole
                  "/tmp/bz_topo_fill_cheetah.sgm", "/tmp/bz_topo_subdiv_cheetah.sgm", "/tmp/bz_topo_weld_cheetah.sgm",
                  # and 2 patches extruded as a region (walls on the boundary, Max's Extrude & Bevel)
                  "/tmp/bz_topo_attach_cheetah.sgm", "/tmp/bz_topo_mirror_cheetah.sgm",
                  "/tmp/bz_topo_region_cheetah.sgm",
                  # A5 Trim Stray Surface (trim_stray_test): the cheetah against a target it has 14 tail patches more
                  # than -- those deleted and the hole filled (watertight), and the same 14 collapsed onto the target
                  "/tmp/bz_trim_delete_cheetah.sgm", "/tmp/bz_trim_collapse_cheetah.sgm",
                  # fit-to-rig (fit_to_rig_test): a quad sphere fitted on the scaffold template, split across its
                  # limbs and then rigged straight away -- horn_l added under head, ear_l deleted, the LFLeg re-rooted,
                  # and an LMLeg limb rooted at a bone C8 added (measured 2026-09-16: LOADED in 6.2 s)
                  "/tmp/bz_fitrig_out.sgm",
                  # Optimise Ring Start (boner_ring_start_test B): the cheetah with its L Front Leg socket ring started
                  # 2 corners out, and the same creature after the operator rotated it back (byte-identical to the
                  # stock cheetah) -- a mis-started ring is a valid model, so both must LOAD
                  "/tmp/bz_ring_start_bad.sgm", "/tmp/bz_ring_start_fixed.sgm"]
# --combine-full: the offline combine tier over every stock creature in both roles (combotest validate --donors all),
# on what the suites exported (a creature without a .lua gets default attributes)
COMBINE_CANDIDATES = ["/tmp/bz_new_creature_edit.sgm", "/tmp/bz_la2.sgm", "/tmp/bz_ring_repick.sgm"]
COMBINE_FULL_TIMEOUT = 300
# Expected results of the full tier, pinned on the JSON Lines report and not only the exit code, so a per-design check
# that stops firing turns the tier red. ``exit``: the CLI's exit code; ``lines``: text the report must print;
# ``issue_groups``: the (role, design) groups whose every row (one per stock donor) has issues -- no other row may;
# ``issue_kinds``: every issue must start with one of these. limb_auth_test moves the snail's tail patches into its
# head, which leaves the tail socket empty and uncapped and puts tail patches on the head limb: the precheck names the
# socket, and every design that replaces the head, grafts onto the tail socket or donates the head opens the mesh (the
# unedited stock snail passes the full tier with 0 issues).
COMBINE_DEFAULT_EXPECT = dict(exit=0, lines=(), issue_groups=frozenset(), issue_kinds=())
COMBINE_EXPECT = {"/tmp/bz_la2.sgm": dict(
    exit=1, lines=("ERROR precheck: empty socket tail has no torso cap patch",),
    issue_groups=frozenset({("torso", "head"), ("torso", "tail"), ("donor", "head"), ("donor", "all")}),
    issue_kinds=("ERROR check_model: mesh is not watertight", "ERROR check_model: mesh is not manifold"))}
DESIGNS_PER_DONOR = 2 * 7          # both roles x (6 part buttons + all)


def judge_combine_full(returncode: int, text: str, jsonl: str, expect: dict) -> list[str]:
    """Why a --combine-full run does not match ``expect`` (empty = PASS)."""
    import json
    bad = []
    if returncode != expect["exit"]:
        bad.append(f"exit {returncode}, expected {expect['exit']}")
    bad += [f"report lacks {m!r}" for m in expect["lines"] if m not in text]
    try:
        rows = [json.loads(line) for line in open(jsonl, encoding="utf-8") if line.strip()]
    except OSError as e:
        return bad + [f"no JSON report: {e}"]
    if not rows or not rows[-1].get("summary"):
        return bad + ["the JSON report has no summary row"]
    summary, rows = rows[-1], rows[:-1]
    donors, rest = divmod(summary["designs"] - 1, DESIGNS_PER_DONOR)
    if rest or donors < 1 or len(rows) != summary["designs"]:
        bad.append(f"{summary['designs']} designs / {len(rows)} rows is not 1 + {DESIGNS_PER_DONOR} per stock donor")
    groups = {}
    for r in rows:
        if r["issues"]:
            groups[(r["role"], r["design"])] = groups.get((r["role"], r["design"]), 0) + 1
            bad += [f"unexpected issue kind: {i[:160]}" for i in r["issues"] if not i.startswith(expect["issue_kinds"] or ("\0",))][:3]
    for g in sorted(set(groups) | set(expect["issue_groups"])):
        want = donors if g in expect["issue_groups"] else 0
        if groups.get(g, 0) != want:
            bad.append(f"{g[0]}/{g[1]}: {groups.get(g, 0)} designs with issues, expected {want}")
    if summary["designs_with_issues"] != sum(groups.values()):
        bad.append("summary designs_with_issues disagrees with the rows")
    return bad


def judge_must_fail(returncode: int, export_written: bool, log, markers) -> bool:
    """Whether a COMBINE_MUST_FAIL run failed the expected way: exit 1, no export, and for each group of ``markers`` a log
    line containing all of its words."""
    lines = [str(ln) for ln in (log or [])]
    return (returncode == 1 and not export_written
            and all(any(all(w in ln for w in group) for ln in lines) for group in markers))


def judge_must_flag(returncode: int, export_written: bool, log, markers, pair_returncode: int, pair_text: str,
                    offline_issues, words) -> list[str]:
    """Why a COMBINE_MUST_FLAG run does not show the expected engine breakage and offline flag (empty = PASS)."""
    lines = [str(ln) for ln in (log or [])]
    bad = []
    if returncode != 0 or not export_written:
        bad.append(f"ComboTest.exe did not export (exit {returncode})")
    bad += [f"log lacks {m!r}" for m in markers if not any(m in ln for ln in lines)]
    if pair_returncode == 0 or "engine vertices off our surface" not in (pair_text or ""):
        bad.append("our pair check no longer differs from the engine export (modelled now? update COMBINE_MUST_FLAG)")
    if not any(all(w in str(i) for w in words) for i in (offline_issues or [])):
        bad.append(f"the offline design check lacks an issue with {words}: {list(offline_issues or [])[:3]}")
    return bad


OFFLINE_DESIGN = """import json, sys, warnings
warnings.simplefilter("ignore")
from combotest import validate as V
from combotest.loader import load_parent
t, d = load_parent(sys.argv[1]), load_parent(sys.argv[2])
out = []
for design in sys.argv[3].split(","):
    out += V.run_design(t, d, design, role="torso", run_lua=False, roundtrip=False)["issues"]
print(json.dumps(out))
"""


def offline_design_issues(torso_path: str, donor_path: str, parts, timeout: int) -> list:
    """combotest.validate.run_design's issues for ``parts`` of the donor on the torso (in a fresh interpreter)."""
    import json
    r = subprocess.run([sys.executable, "-c", OFFLINE_DESIGN, torso_path, donor_path, ",".join(parts)],
                       capture_output=True, text=True, timeout=timeout, cwd="/tmp",
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"offline check failed: {r.stderr.strip()[-300:]}"]


def run(name: str, timeout: int) -> tuple[str, float, str]:
    path = os.path.join(HERE, name)
    if name in PURE:
        cmd = [sys.executable, path]
    else:
        # --python-exit-code must come BEFORE --python: Blender applies it only to scripts that follow
        # (placed after, an uncaught exception still exits 0 and a failing test would read as PASS)
        cmd = [BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", "--python", path]
    env = dict(os.environ, PYIC_REPO=REPO, PYTHONDONTWRITEBYTECODE="1")
    t = time.time()
    try:
        launch = subprocess.run if name in PURE else _so_common.run_locked      # Blender launches share a machine-wide lock
        r = launch(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=REPO)
        verdict = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
        out = (r.stdout + r.stderr)
        if verdict == "PASS" and "SO_SKIP:" in out:
            verdict = "SKIP"                       # it ran, but a check was skipped (say why: the reasons are listed at the end)
    except subprocess.TimeoutExpired as e:
        verdict, out = "TIMEOUT", (e.stdout or "") + (e.stderr or "")
    return verdict, time.time() - t, out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only tests whose name contains this")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("-v", action="store_true", help="print full output of failures")
    ap.add_argument("--engine", action="store_true",
                    help="afterwards, load the exports the suites wrote in the real Object Editor "
                         "(rdnharness rdn-oe: Proton + Xvfb, ~6 s per creature)")
    ap.add_argument("--combine-full", action="store_true",
                    help="afterwards, run the full offline combine tier (combotest validate --donors all: every stock "
                         "creature, both roles, every part button) on COMBINE_CANDIDATES; about 20-60 s each")
    ap.add_argument("--parity", action="store_true",
                    help="afterwards, check that what the add-on renders headlessly matches the Object "
                         "Editor's draw stream for the standard asset set (tests/engine_parity.py)")
    a = ap.parse_args()
    tests = [t for t in PURE + IN_BLENDER if a.k in t]
    if a.list:
        print("\n".join(tests)); return 0
    results = []
    for i, t in enumerate(tests):
        if i and t not in PURE:
            time.sleep(1.0)   # the snap Blender runtime degrades under rapid back-to-back launches
        verdict, secs, out = run(t, a.timeout)
        results.append((t, verdict, secs, out))
        print(f"{verdict:10s} {secs:6.1f}s  {t}", flush=True)
    if a.combine_full:
        jobs = max(2, (os.cpu_count() or 4) - 2)
        for cand in COMBINE_CANDIDATES:
            name = f"combine_full({os.path.basename(cand)[:-4]})"
            if not os.path.exists(cand):
                results.append((name, "MISSING", 0.0, f"{cand} not found: run the suites first"))
                print(f"{'MISSING':10s} {0.0:6.1f}s  {name}: {cand} not written (run the suites first)", flush=True)
                continue
            out = f"/tmp/bz_combine_full_{os.path.basename(cand)[:-4]}.jsonl"
            if os.path.exists(out):
                os.remove(out)                    # never judge a stale report from an earlier run
            cmd = [sys.executable, "-m", "combotest.cli", "validate", cand, "--donors", "all", "--jobs", str(jobs),
                   "--json", out, "--quiet"]
            t = time.time()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=COMBINE_FULL_TIMEOUT, cwd="/tmp",
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                text = r.stdout + r.stderr
                expect = COMBINE_EXPECT.get(cand, COMBINE_DEFAULT_EXPECT)
                why = judge_combine_full(r.returncode, text, out, expect)
                verdict = "PASS" if not why else f"FAIL({r.returncode})"
                if why:
                    text += "\nMISMATCH " + "\nMISMATCH ".join(why)
                    print("\n".join("MISMATCH " + w for w in why), flush=True)
                elif expect is not COMBINE_DEFAULT_EXPECT:
                    print(f"(as expected: exit {expect['exit']}, {sorted(expect['issue_groups'])} x every donor)", flush=True)
            except subprocess.TimeoutExpired as e:
                verdict, text = "TIMEOUT", str(e.stdout or "") + str(e.stderr or "")
            lines = text.strip().splitlines()
            print("\n".join(lines[-12:]), flush=True)
            results.append((name, verdict, time.time() - t, text))
            print(f"{verdict:10s} {time.time() - t:6.1f}s  {name} -> {out}", flush=True)
    if a.engine:
        # in-engine tier: every export the suites just wrote, loaded by the real Object Editor
        exports = [p for p in ENGINE_EXPORTS if os.path.exists(p)]
        tool = os.path.join(REPO, "ICParse", "tools", "oe_load_check.py")
        t = time.time()
        r = subprocess.run([sys.executable, tool, *exports, "--json", "/tmp/bz_engine_verdicts.json"],
                           capture_output=True, text=True, timeout=a.timeout * 3, cwd=REPO)
        verdict = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
        try:                                      # judge against the expected verdict per export
            import json
            recs = json.load(open("/tmp/bz_engine_verdicts.json"))
            bad = [rec["file"] for rec, path in zip(recs, exports)
                   if rec["verdict"] != ENGINE_EXPECT.get(path, "LOADED")]
            verdict = "PASS" if not bad else "FAIL(" + ",".join(bad) + ")"
        except Exception:
            pass
        print(r.stdout.rstrip(), flush=True)
        results.append(("object_editor_load", verdict, time.time() - t, r.stdout + r.stderr))
        print(f"{verdict:10s} {time.time() - t:6.1f}s  object_editor_load ({len(exports)} exports)", flush=True)
    if a.engine:
        # combiner tier: the game's own ComboTest.exe, driven headlessly, must export both combinations, and our
        # combiner must reproduce each export (combotest tools/bone_truth.py --pair: size, patches, skeleton, body and
        # limb scales, placement, engine vertices on our surface, skin -- the 53-fixture exactness bounds)
        script = os.path.join(os.path.dirname(sys.executable), "rdn-combotest")
        truth = os.path.join(REPO, "pyRDN", "combotest", "tools", "bone_truth.py")
        for torso, donor, parts in COMBINE_CHECKS:
            tp, dp = (p if os.path.isabs(p) else os.path.join(REPO, p) for p in (torso, donor))
            name = f"combotest({os.path.basename(torso)[:-4]}+{os.path.basename(donor)[:-4]})"
            if not (os.path.exists(tp) and os.path.exists(dp)):
                results.append((name, "MISSING", 0.0, f"{tp} or {dp} not found"))
                print(f"{'MISSING':10s} {0.0:6.1f}s  {name}: {tp if not os.path.exists(tp) else dp} not found", flush=True)
                continue
            t = time.time()
            out = f"/tmp/bz_combo_{os.path.basename(torso)[:-4]}_{os.path.basename(donor)[:-4]}.sgm"
            rec = out[:-4] + ".json"
            for f in (out, rec):
                if os.path.exists(f):
                    os.remove(f)                  # never judge a stale export from an earlier run
            cmd = [script] if os.path.exists(script) else [sys.executable, "-c", "from rdnharness.cli import combotest_main as m; import sys; sys.exit(m(sys.argv[1:]))"]
            cmd += [tp, dp, "-o", out, "--no-ebp", "--json", rec] + [f"--{p.replace('_', '-')}=2" for p in parts]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout * 2, cwd=REPO)
            if (torso, donor) in COMBINE_MUST_FAIL:
                try:                              # the failure must be the expected one (its log lines)
                    import json
                    log = json.load(open(rec)).get("log", []) if os.path.exists(rec) else []
                except (OSError, ValueError):
                    log = []
                markers = COMBINE_MUST_FAIL[(torso, donor)]
                refused = judge_must_fail(r.returncode, os.path.exists(out), log, markers)
                verdict = "PASS" if refused else f"FAIL(expected ComboTest.exe to fail with {markers}, exit {r.returncode})"
                name += " [must fail]"
            else:
                verdict = "PASS" if r.returncode == 0 and os.path.exists(out) else f"FAIL({r.returncode})"
            print(r.stdout.rstrip(), flush=True)
            results.append((name, verdict, time.time() - t, r.stdout + r.stderr))
            print(f"{verdict:10s} {time.time() - t:6.1f}s  {name}", flush=True)
            if (torso, donor) in COMBINE_MUST_FLAG and verdict == "PASS":
                t = time.time()
                pname = f"combotest_torn_flagged({os.path.basename(torso)[:-4]}+{os.path.basename(donor)[:-4]})"
                try:
                    import json
                    log = json.load(open(rec)).get("log", []) if os.path.exists(rec) else []
                except (OSError, ValueError):
                    log = []
                pcmd = [sys.executable, truth, "--pair", tp, dp, ",".join(parts), out, "--no-vfit"] + \
                    (["--record", rec] if os.path.exists(rec) else [])
                pr = subprocess.run(pcmd, capture_output=True, text=True, timeout=a.timeout, cwd="/tmp",
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                markers, words = COMBINE_MUST_FLAG[(torso, donor)]
                issues = offline_design_issues(tp, dp, parts, a.timeout)
                bad = judge_must_flag(r.returncode, os.path.exists(out), log, markers, pr.returncode,
                                      pr.stdout + pr.stderr, issues, words)
                verdict = "PASS" if not bad else "FAIL(" + "; ".join(bad) + ")"
                results.append((pname, verdict, time.time() - t, pr.stdout + pr.stderr + "\n" + "\n".join(map(str, issues))))
                print(f"{verdict[:200]:10s} {time.time() - t:6.1f}s  {pname}", flush=True)
                continue
            if verdict != "PASS" or (torso, donor) in COMBINE_MUST_FAIL:
                continue
            t = time.time()
            pname = f"ours_vs_combotest({os.path.basename(torso)[:-4]}+{os.path.basename(donor)[:-4]})"
            pcmd = [sys.executable, truth, "--pair", tp, dp, ",".join(parts), out, "--no-vfit",
                    "--json", out[:-4] + "_pair.json"] + (["--record", rec] if os.path.exists(rec) else [])
            try:
                r = subprocess.run(pcmd, capture_output=True, text=True, timeout=a.timeout, cwd="/tmp",
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
                verdict = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
                text = r.stdout + r.stderr
            except subprocess.TimeoutExpired as e:
                verdict, text = "TIMEOUT", str(e.stdout or "") + str(e.stderr or "")
            print("\n".join(l for l in text.splitlines() if l.startswith(("PAIR", "  MISMATCH"))), flush=True)
            results.append((pname, verdict, time.time() - t, text))
            print(f"{verdict:10s} {time.time() - t:6.1f}s  {pname}", flush=True)
    if a.parity:
        t = time.time()
        r = subprocess.run([sys.executable, os.path.join(HERE, "engine_parity.py")], capture_output=True, text=True,
                           timeout=a.timeout * 3, cwd=REPO)
        verdict = "PASS" if r.returncode == 0 else f"FAIL({r.returncode})"
        print(r.stdout.rstrip(), flush=True)
        results.append(("engine_parity", verdict, time.time() - t, r.stdout + r.stderr))
        print(f"{verdict:10s} {time.time() - t:6.1f}s  engine_parity", flush=True)
    skipped = [r for r in results if r[1] == "SKIP"]
    for t, _v, _s, out in skipped:
        for line in out.splitlines():
            if line.startswith("SO_SKIP:"):
                print(f"SKIPPED  {t}: {line[len('SO_SKIP:'):].strip()}")
    failed = [r for r in results if r[1] not in ("PASS", "SKIP")]
    for t, verdict, _, out in failed:
        tail = out.strip().splitlines()
        print(f"\n--- {t} [{verdict}] " + ("(full output)" if a.v else "(last 25 lines; -v for all)"))
        print("\n".join(tail if a.v else tail[-25:]))
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f" ({len(skipped)} with skipped checks)" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
