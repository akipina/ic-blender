# Roadmap (2026-11-19)

What comes next for blender_native, in order. It replaces the plan agreed on 2026-09-15, every increment of which is
finished (the list is at the end). [STATUS.md](STATUS.md) says what has been verified and lists the known gaps.

**How the work is done.** One piece at a time. Each piece ends with tests (an untouched file still exports
byte-identical; an edit changes only what it should), the documents brought up to date, and **a zip to try**
(`tools/build_extension.py --version <next>`), which the user installs and tries in Blender. Nothing is called working
in the game until someone has tried it there; the in-game checks of each piece are listed with it.

## Gatherers, characters, structures and props

1. **Blueprint editing** -- *done, release 0.3.0 (2026-11-17).* The Blueprint panel (`sceneobj/panels_blueprint.py`):
   name, kind (class), footprint, offset and preloaded sounds; the gameplay numbers read-only with the stats file they
   come from; Export Entity Folder writes the `.sgm`, the `.ebp` and a README into a `Data/art/ebps/<folder>/` tree.
   Still to try in the game (the in-game checklist of the blueprint panel's working notes): that the exported files
   load, that a footprint and a name change show, what a changed class does, and that two players with different
   files are refused.
2. **The three mesh-export bugs** -- *(a) and (b) done, (c) half done, release 0.4.0 (2026-11-18).* (a) A part over
   **12,288 vertex entries** after the seams are split is refused by `scene.build_part` and ICParse
   `write_scene_part` (the game's vertex-set limit); above the largest stock part the scene validator's warning
   (`ICParse.sgm.scene.validate`, e.g. "2142 verts exceeds any stock v1.2 mesh (~1169)") is among the export's notes.
   (b) **Bounding volumes** are rebuilt the way the game builds them: the part's own, one per palette bone from the
   vertices that name it, and the entity box from the part group (ICParse `sgm.scene.bounds`). (c) **Damage states**:
   an edited part with morphs is refused, or written without them with *Drop the damage states of edited parts*
   (done); **carrying the morph data over to the new vertices remains**. Still to check in the game: the 12,288
   limit and a changed entity box.
3. **Materials and textures**: retexture a part, team colour, and writing `.txr` textures (which texture type to
   write is still to be settled in the game: the preferences page's *Texture type* switch, type 1 by default).
4. **Skeleton and parts**: bone edits on scene objects (move, rename, add, delete) written back instead of being
   ignored; adding and removing parts.
5. **Attachment points and damage states**: show and edit the markers and the morphs.
6. **A checks panel, and installing into a game copy**: the entity rules (`ICParse.entity`) shown in a panel as
   they are broken, and an install into a copy of the game the user owns (the preferences' *Test game folder*), never
   into the Steam folder.

**Maps.** *Looking at maps is done, release 0.4.0 (2026-11-18):* File > Import opens a map file or one of the game's
own maps (`sceneobj/ops_map_import.py` over `ICParse.sgb.view`): the terrain with its ground textures, the water and
every placed entity. *Editing and saving done, release 0.5.0:* terrain heights, water level, ground textures, placed
entities and owners, saved through `ICParse.sgb.edit` (`ops_map_export.py`). *Release 0.6.0 (2026-11-18):* the map is drawn
the way the game draws it (`sceneobj/map_look.py`: sun, ambient, sky, fog, the 800 m open sea, decals, the water's own
colours), creatures are real models, stock and combined (`sceneobj/map_creatures.py`), the water is drawn again after a ground
edit and can be added to a dry map, and the pathfinding is baked with the game's own method (`ICParse.sgb.pathbake`: PRMP and
PASM byte-identical on 92 of 92 stock maps, IMPA on 91). *Release 0.7.0 (2026-11-19): the Mission Editor's tools.* Extension points
(`sceneobj/map_hooks.py`: import steps, save steps, checks, maps from memory) let each feature be its own module: File > New >
Impossible Creatures Map from the 15 templates, Map settings, the editable Terrain look, preview pictures and the Map problems list
(`map_settings.py`, `map_look.py`); painting of passability, overlays, terrain types, water at its own level, decals, blend tiles and
texture stamping (`map_paint.py`); groups, variables, triggers, dialogues and AI paths (`map_script.py`); NISlet animations
(`map_cinematics.py`); height map, `.ter` and `.ent` files, the Object List, the Blueprint List and Terrain Crop (`map_tools.py`,
`map_crop.py`). *Release 0.8.1 (2026-11-24): maps drawn by the game's own rules* (the ground picture with its detail grain, half-size
decals on the ground's own cells, the water as reflected sky, the border, the sky's shape, nothing drawn that the game never draws;
texture stamps no longer mirrored). *Release 0.8.0 (2026-11-19): the cinematic Replay.* A pick of one of a map's cinematics is acted out on Blender's
timeline the way the game plays it (`ICParse.sgb.cine_timeline`: the trigger script run with the game's tick, conditions and actions,
its path code `cine_pathanim`, an A* walk on the map's cell grid `cine_walk`; `ICParse.sgm.nis_scene` and `sceneobj/map_nis.py`: the cut-scene
(NIS) objects with their animation and camera; `sceneobj/map_replay.py` + `ops_map_replay.py` + `panels_map_replay.py`: the stand-ins, the
camera, the markers, the event list), every event marked exact, approximate or skipped, and the map never changed by it. Next, in order:
(0) **watch a replay next to the game** (nothing here has been compared with the game by eye: start with mission1's *New lab intro* and
*Camera Intro Rex Lucy*), then the replay's own gaps: the real pathfinder and unit spacing for walks, the length of a speech (the audio is in
no archive we have), the choice among several clips under a motion node, creatures that play their clips in a map (today a keyed number),
dialogue text as marker names, the game camera's blend start, the cut-scene models' lights / effects / morph channels, the clip bake's
(K+1)/fps timing in `actions.py` (the game plays a clip of K+1 frames over (K+1)/fps seconds; the bake spreads K intervals over K/fps), and
pickers for the cinematic's assumptions in the panel (the module takes `assume=`); (1) **try a saved map in the game** (the one thing no test here can do; start with a new map from a
template in the multiplayer list, a unit walking across a hill and a painted blocked area, and a stock map with one trigger
changed), (2) the pieces the Mission Editor has that are not here: pickers for the engine's named choices in triggers (keys,
research, decals, commands, stances, abilities, animations: their value lists were never decoded), the exact shape of a camera path
(tension, continuity, bias) and Orbit Target / Third person view, regenerating the `_decaled` ground pictures after texture stamping,
an entity file that carries a combined creature, a crop square that reaches outside the map, (3) copying a creature placed in the
map, (4) animating the placed creatures (idle clips; today the bind pose), (5) a line-by-line port of the sector-map generator (today
the older `ICParse.sgb.pasm`, which matches the game on every stock map but takes 3 s on a 512-cell map and over a minute on the
largest), (6) the two unexplained pathfinding exceptions (`mission1`'s 52 shore cells; placements under `locations/` and `nis/` that
leave their cells open), (7) the parts of the game's map drawing the add-on leaves out: its per-point ground light and cast
shadows and the moving water (`WCLR` / `OCLR` need no reader: the game never reads them), (8) the Mission Editor's Script Editor (single Lua commands in the running editor), which has no Blender
counterpart.

**Later, only on request:** an editor for the blueprint's motion tree and events; importing outside models (OBJ, FBX,
glTF; `ICTools/assetconverter` does this outside the add-on today).

The working plan behind these pieces (byte-level writer rules, the order of the export stages, the in-game check list)
was written on 2026-11-17 outside the repository; the parts that have landed are documented in DESIGN.md and
`docs/sceneobj/README.md`.

## Creatures: still open

Ordered by value for the effort; none of these has been started.

1. **Convert Mesh onto the fit's rig split.** `core.mesh_to_creature` still puts every point on one bone and every
   patch in limb 0; `fitops.rig_split` (Fit Patches to Mesh) already does the limb and skin split and is test-pinned.
2. **A session at a screen** for what no headless test can drive: the drag feel, box select with X-ray off, Pick Ring
   and Pick Bone, the drag macros, Sculpt Mode, texture paint, and the Blueprint panel's layout.
3. **Transfer Texture says what it costs.** Its atlas takes every patch from 16 to 25 display vertices and the report
   gives only coverage; `ICParse.validate.display_vertices` already computes the cost.
4. **Retry a ComboTest.exe row once before failing it** (the picker flake, STATUS.md gap 14), reported as a retry.
5. **Material ID per patch and Select by ID, then smoothing groups** (32 buttons, Select by Group, Auto Smooth by
   angle): the last two gmax authoring axes; both are per-patch tables the export already carries.
6. **Bevel, Add Tri, Detach, Reset Handles**, each a pure plan in the style of `topoops` plus an operator.
7. **A battle-skinning preview**: the game skins a battle creature by its patches' corner bones only; the viewport
   shows the army builder's draw, where every control point follows its own bone.
8. **Take one creature into the game**: build its `.ebp` / `.lua` / `.lsc` (ICParse and ICModManager do this outside
   the add-on), install it, and have the user play it.
9. **Validate's remaining rules**: the Auto Interior and repeated-index check on an imported file, the animation set
   with loop rules, the per-ID texture size table.
10. **The pillbug's legs**: geometry, not a better warp (extrude the missing leg pairs onto the retargeted body, or fit
    from scratch under about 430 patches and rig those legs).
11. **Joint limits as real limits**: the spec follows.

### Joint limits as real limits: the spec (the BONE joint-limit tail, decoded 2026-09-17)

Today the six limit floats of each BONE record are stamped on the rig bone at import, edited in Boner > Bones and
written back only when they differ (`topology.apply_bone_limits`). What is missing is behaving like the engine.

Source: `IC.exe` `Skeleton_ReadChunk` `0x00596da3` (`__FILE__` = `…\Cross\RenderAnim\Skeleton.cpp`) and
`Bone::SetJointLimits` `0x0059724d`, cross-checked against `ObjectEditor` `0x00591810` / `0x00561906` — the same
C++ function in both binaries, byte-for-byte the same behaviour. Also in `ICParse/FORMATS.md` (Skeleton).

1. **The tail is OPTIONAL and length-gated, not version-gated.** After the 12-float bind matrix the reader takes the
   limits only `if (IFF::ChunkRemain() >= 0x18)`: `float[3] min` then `float[3] max`. If those 24 bytes are absent
   *all nine* derived floats are zeroed. A separate `if (ChunkRemain() > 3)` then takes one u32 and keeps **bit 0**
   only (`bone+0x108`). So a record can legitimately carry: no tail, 24 bytes, or 28 bytes. `apply_bone_limits`
   currently refuses any list whose length differs from the record's, which is right for editing but means Boner
   cannot yet *add* limits to a bone whose record has no tail, or drop them — that is the first piece of this work.
2. **Radians on disk; degrees in memory.** Both readers multiply all six by `180/π` before storing. Boner already
   writes radians (keep it that way). A UI that shows degrees must convert for display only — and never compare a
   number scraped from a running game/editor against a file without the 57.3× factor.
3. **The engine normalises, so the UI should too.** `Bone::SetJointLimits` swaps each axis's pair when `min > max`
   before storing (`bone+0xe0` min, `+0xec` max). A file may therefore hold an unsorted pair; it will load as the
   sorted one. Boner should sort on edit rather than let the two disagree.
4. **The midpoint is precomputed by the caller**, not by the storer: `mid = min + (max - min) * 0.5`, stored at
   `bone+0xf8..+0x100`. Nothing needs to be written for it — it is derived — but a rest/neutral pose UI wants it.
5. **The widest-axis mask at `bone+0x104`** is derived from `range = max - min`: start at `7` and clear bit 0/1/2
   for any axis whose range is strictly smaller than another's, so the surviving bits mark the axis (or tied axes)
   with the largest rotation range; `Bone::Bone` `0x005973d1` defaults it to `7`, and a bone with no tail keeps `7`
   because all three ranges are zero. **Nothing reads it back** — it is an IK/DOF hint, so Boner may display it but
   must not treat it as authored data.
6. **Nothing enforces joint limits in play.** Across `0x00596000–0x0059a000` nothing reads `+0xe0..+0x104`; only
   `Bone_AssignFrom` copies them. Any clamping Boner does is *our* feature, not fidelity to the game — so it belongs
   in the IK solver and the pose UI, never in the export path, which must keep writing exactly what was authored.

## Platform support (stated, not fixed)

**Everything here is developed and tested on Linux only.** The add-on is pure Python on Blender's API and its
extension zip bundles wheels for Windows x64 and macOS arm64, so it is *expected* to work there, but nothing in this
repository has ever run on either and no test covers them. The **developer harness is genuinely Linux-shaped**:
`rdn-oe` / `rdn-combotest` / `rdn-compare` and the d3d8 capture proxy run the game's real `ObjectEditor.exe` and
`ComboTest.exe` under **Proton/Wine with Xvfb**; `tests/run_all.py` runs the Blender named by `$BLENDER`, else the
one on the PATH (the snap build), the tests use POSIX paths such as `/tmp`, and a few test and tool
options that render need `xvfb-run`. A Windows port would not need emulation at all (those executables are native there) but would need its
own launcher, display handling and path layer. `ICParse.data.default_root` also knows only the Linux Steam folders,
so on Windows or macOS the game is found only through `IC_INSTALL`. Open work, in the order it would be done:
1. audit the add-on for POSIX assumptions (paths, `/snap/bin/blender`, `/tmp`, separators, the Steam folders) --
   static, cheap;
2. smoke-test the extension zip in Blender for Windows (can be done from Linux under Proton, which exercises the real
   `win_amd64` wheel path) before asking anyone to try it;
3. port the harness launchers to native Windows execution;
4. only then claim Windows support.

## Done

The plan of 2026-09-15, every item finished (the per-increment progress notes, with every measured number, are in
git: `git show 08536c1:ROADMAP_NATIVE.md`):

* **Sculpt and fit**: Fit Patches to Mesh (A1, 2026-09-15); Sculpt Surface / Refit Patches (A2, 2026-09-15); Retarget
  Creature to Mesh (A3, 2026-09-15); landmark-guided retarget (A3b, 2026-09-16); Transfer Texture (A4, 2026-09-15);
  Trim Stray Surface (A5, 2026-09-16); fit-to-rig, the template's limb and skin split for a fitted creature
  (2026-09-16).
* **Patch editing feel**: corners carry handles and interiors follow Max's Auto Interior rule (B5); patch-level
  selection, hide, delete, Corner / Coplanar vertex types, Lock Handles (B6); the topology tools Weld, Attach, Mirror to
  Whole, Subdivide, Add Quad, Fill Hole, Extrude Region (B7); all 2026-09-15.
* **Rig**: deleting and re-parenting the creature's own bones, automatic adoption of chains Blender adds, joint limits
  and IK chains as editable data (C8, 2026-09-15).
* **Integration**: the surface as real geometry through Geometry Nodes, shipped as the *Evaluated Surface* option,
  default off (D9, 2026-09-16); the export's refusals shown at the moment of the edit, and the distributable zip with
  its libraries as wheels (D10, 2026-09-16).
* **Combining**: Optimise Ring Start (E1, 2026-09-16).

Since then, outside that plan: the correctness pass against the decompiled game and Blender 5.3 (2026-11-15); New
Project and gatherers / structures with any topology (2026-11-16); the `sceneobj` package and the Blueprint panel
(2026-11-17).

## Findings that shaped the plan
- Fit experiment (tools/fit_experiment.py, Suzanne skin, errors as % of the bounding diagonal): 322 patches
  rms 0.17 / p95 0.37 / max 1.5; 777 patches 0.10 / 0.13 / 1.6; 1988 patches 0.016 / 0.031 / 0.29. The layout
  is the limit (thin features need front/back patches), not the solver. Needed for robustness: fairness term,
  soft C1 seam term, normal-aware correspondence, CG solve (Blender's numpy has no fast BLAS).
- Blender's add-on API cannot add a data type; the mesh-plus-rules design keeps every native tool. The one
  hard ceiling was the surface not being evaluated geometry -- **the Evaluated Surface option answered it
  (2026-09-16)**: Geometry Nodes evaluates the
  exact same surface as real mesh (float32-exact against `core.tessellate`), so render, snapping, ray-cast
  selection and downstream modifiers see it. It is an option rather than the default only because Geometry
  Nodes re-evaluates everything for one moved control point (12.0 ms at 800 patches vs the overlay's 1.49 ms).

### Import contract: weld first, then judge (2026-09-16)
**An index buffer is not a surface.** glTF stores one vertex per position+UV+normal tuple, so a UV atlaser
(`xatlas`, which every image-to-3D pipeline runs -- Hunyuan3D's `Hy3DMeshUVWrap` does
`mesh.vertices = mesh.vertices[vmapping]`) duplicates every vertex on a chart boundary. Our own Bake Surface does
the same at every patch seam. Measured on `3DInputs/Pillbug.glb` (trimesh 5.1.0):

| | V | F | parts | boundary edges | edges with >2 faces | watertight | Euler |
|---|---|---|---|---|---|---|---|
| as shipped | 32,563 | 50,000 | **472** | **14,524** | **0** | no | 301 |
| welded by position | 24,994 | 50,000 | **1** | **0** | **0** | **yes** | **−6** (genus 4) |

Three rules follow, and `fitops.target_topology` / `fitops.parts_reason` are where they live (Fit to Mesh,
Retarget and Landmark Retarget all judge through them):

1. **Weld a throwaway copy by position (`bmesh.ops.remove_doubles`, 1e-5 of the target's diagonal) before judging
   it, and never modify the user's mesh.** Only parts that survive the weld are real floaters (eyes in sockets, a
   detached limb) -- those are still refused, by the same message, which now also names Merge by Distance.
2. **Boundary edges (one face) are not non-manifold edges (more than two faces)** -- report them as separate
   numbers. Blender's *Select Non-Manifold* counts boundary edges by default, which is exactly how 14,524 boundary
   edges became a "14,524 non-manifold edges" diagnosis.
3. **Never voxel-remesh to "repair" this.** The mesh above was a closed watertight solid all along; the remesh our
   pipeline ran on that diagnosis destroyed its UVs and cost the creature its own texture. (The real remaining
   problem there is genus 4 -- four leg/body loops -- which no weld and no remesh parameter fixes.)

The layouts follow the same rule: QuadriFlow wants a closed manifold and Own Quads would inherit the seams as
holes, so both are built from a welded copy -- but only when the target actually has duplicates, so a clean mesh's
layout is bit-for-bit what it always was (verified: the seam-split quad sphere gives the same 384-patch layout and
the same rms; seam-split Suzanne at budget 300 fits to 0.27 %, the clean one to 0.27 %).
The same finding in the converter: `assetconverter.intake.topology.mesh_health` (its `trimesh_loader` called
`merge_vertices()` believing it welded -- a measured no-op, and `merge_tex=True` would have rewritten 7,569
face-corner UVs by up to 1.23 in UV space).

### The patch ceiling is display VERTICES, and UVs set the price (2026-09-16)
**A patch is not a fixed cost.** The toolkit said 4095 patches ("65,535 display vertices / 16 per patch"); both
halves were wrong. The engine tessellates the whole patch mesh into **one** DirectX 8 vertex set and
`VertexSet::Create` refuses more than **12,288** (`dx8VertexSet.cpp:86`, `param_3 < 0x3001`), and
`PatchMesh::CountVertices` (ObjectEditor `FUN_0051b7c4`) counts

    V = patches x k^2 + owned_sides x k + owned_corners,  k = (1 << LOD) - 1,  LOD 2 (the 5x5 grid) is the finest
    V = 9*P + 3*E_owned + C_owned  <=  12288

A seam has **one** owner only when the two patches share a material, have overlapping smoothing-group masks and
both endpoint UVs within **1/16**; otherwise both patches own it. So a patch costs **16** vertices welded and
**25** with its own texture island -- which is exactly what Transfer Texture's per-patch atlas produces. Measured
with `rdn-oe` on creatures this repo built (`ICParse/PATCH_CEILING.md` has the full ladder): island UVs
**490 patches LOAD / 492 CRASH**; continuous UVs **754 LOAD / 758 CRASH**; and on ONE fixed 754-patch cage,
**V = 12,287 loads and V = 12,291 crashes** -- the geometry is identical, only the UVs differ. The pillbug fits:
518 patches / V = 12,135 LOADED, 616 / V = 14,644 CRASHED.
**The combined creature hits the same ceiling**, which is the budget that actually binds: ComboTest.exe exported a
combine needing 12,258 vertices and FAILED the same combine four patches bigger at 12,310, with
`86 dx8vertexset.c:Size too large for VertexSet::Create()`. The dearest stock limb set costs **5,704** (the moose's)
and the dearest stock torso side **4,409** (the scorpion's, keeping its mid legs), so an authored creature has **6,584**
display vertices for its torso and **7,879** for its limbs (`ICParse.validate.COMBINE_BUDGET_TORSO` /
`COMBINE_BUDGET_LIMBS`). Base-game creatures use at most 6,861 alone and 8,643 combined (scorpion + vampire bat), and
put nearly everything in their LIMBS (dearest torso 937); *(corrected 2026-11-17: this said 5,623 / 4,762 / 6,665 /
7,526 / 8,505, the numbers before ICParse re-derived them on 2026-11-15 with the corrected side rule and one smoothing
group on every combined patch)* the pillbug put **11,931 of its 12,135 in the Torso limb**, which is why a cheetah head
would not graft onto it. `ICParse.validate.display_vertices` / `combine_cost` measure all of this,
`check_model` blocks above the ceiling and warns above the combine budget, `combotest.validate.check_result`
checks the combined mesh, and `fitops.budget_advice` says it at the budget field.
