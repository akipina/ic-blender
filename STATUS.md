# blender_native: status

What has been verified, with the numbers and the tests that hold them; what is knowingly missing; and a short dated
history. This is a developer document (it names modules, functions and tests). [README.md](README.md) is the user's
manual, [DESIGN.md](DESIGN.md) says how the add-on works, [ROADMAP_NATIVE.md](ROADMAP_NATIVE.md) says what comes next.

## 0. Where it stands: 2026-11-24, release 0.8.1 (0.8.0 plus the cut-scene camera and map drawing corrections in the history below)

`blender_native` edits Impossible Creatures models in Blender 5.3 as what they are, and writes back only what changed:

* **Creatures** (patch `.sgm`): the object's vertices are the file's control points, one to one and in file order, a GPU
  overlay draws the exact bicubic surface, and the export rewrites only what moved, so an untouched creature is
  byte-identical. The whole creature pipeline of gmax and Relic's Boner is there except the gaps listed below.
* **Gatherers, characters, structures and props** (scene `.sgm`): ordinary triangle meshes at full detail with the
  skeleton, skin or rigid part binding and clips; New Project starts one from a stock donor; the export rebuilds only
  the parts whose mesh changed and copies every other byte, refuses a part above the game's 12,288 vertex entries,
  gives a rebuilt part the bounding volumes the game builds, and refuses an edited part with damage states unless
  asked to drop them.
* **Blueprints** (`.ebp` of a scene object): the Blueprint panel shows and edits name, class, footprint, offset and
  preloaded sounds, lists the gameplay numbers read-only, and writes the model and the blueprint into a
  `Data/art/ebps/<folder>/` tree with a README; only edited chunks of the `.ebp` change.
* **Maps** (`.sgb`): File > Import opens a map file or one of the game's own maps as terrain with its ground
  textures, the water surface and every placed entity (an instance of its model, or an empty with the reason). Since
  0.5.0 the map can be edited and saved: the ground (heights, in the map's steps), the water level, ground textures per
  8 x 8 block, and the placed entities (move, delete, copy, add from the game's blueprint list, owner) through the Map
  panel and File > Export > Impossible Creatures Map, over `ICParse.sgb.edit`; a map saved with no edits is
  byte-identical and an edit changes only the records it needs. Since 0.6.0 the map is drawn like the game
  (`sceneobj/map_look.py`: the sun and ambient light from EFFC, fog, the sky picture on a dome, the 800 m open sea, the decals
  of FORM DECL, and since 0.8.1 the ground picture, the game's water and border; none of it is written back), the placed creatures are real models, stock
  and combined (`sceneobj/map_creatures.py`, bind pose, built once per kind), the water is drawn again after a ground edit
  (automatic up to 257 x 257 points, else the Redraw water button; Add water for a map without any), and the pathfinding
  is baked with the game's own method (`ICParse.sgb.pathbake`). Since 0.7.0 the Mission Editor's tools are in the Map panel, each
  as its own module plugged into `sceneobj/map_hooks.py`: File > New > Impossible Creatures Map from the 15 templates, map settings
  and the editable Terrain look, the Map problems list, painting (passability, terrain types, water levels, decals, blend tiles,
  texture stamping, overlays), scripting (groups, variables, triggers, dialogues, AI paths), cinematics (NISlets), height-map /
  `.ter` / `.ent` files, the Object and Blueprint lists and Terrain Crop; each saves only what changed, a map saved with no edits
  is still byte-identical with every file beside it (92 of the game's 93 maps; the 93rd differs with no feature step too). Not tried
  in the game. Since 0.8.0 a map's **cinematics can be replayed** (Cinematics > Replay): the map's trigger script is run the way the game
  runs it (`ICParse.sgb.cine_timeline`, `cine_pathanim`, `cine_walk`) and acted out on Blender's timeline by stand-ins and a camera
  (`sceneobj/map_replay.py`, with the cut-scene objects from `sceneobj/map_nis.py` over `ICParse.sgm.nis_scene`), every event listed as
  exact, approximate or skipped; the replay lives in its own collection and never changes what a save writes. Checked by numbers only,
  never by eye or against the game.

`ICParse` is the single source of truth for every format and `pyRDN/combotest` is the combiner; the add-on copies
neither.

**The release.** `sgm_native_patches-0.8.0.zip` was built on 2026-11-19 with
`tools/build_extension.py --version 0.8.0 --output-dir <folder>` (on the build machine; 0.8.1 is built the same way), from
blender_native `4787b61` and ICParse `1759f30`: 8,814,738 bytes (sha256
`c2cbec1108496574a61e999582bcee64b83be9deeda5d75c6e095b72f40d1e13`), 99 entries: the add-on's 72 modules (39 in the
add-on folder, 33 in `sceneobj/`; the 4 new ones are `map_nis`, `map_replay`, `ops_map_replay` and `panels_map_replay`; the new ICParse
modules `sgb/cine_timeline`, `cine_pathanim`, `cine_walk` and `sgm/nis_scene` come in the icparse wheel), `README.md`, the generated
manifest (version 0.8.0), the fallback copy of `construct` in `_deps/` and 7 wheels (construct 2.10.70, icparse
0.1.0+h954e35a4c846, combotest 0.0.1+h8eb8c35d0d42 unchanged, and lupa 2.8 for the four platforms). `bl_info` and the repository's
`blender_manifest.toml` say 0.8.0. A copy of the zip is on the laptop in the workflow's scratch folder (`lap/ext/`); the same sha256 was
checked there.

*The previous release.* `sgm_native_patches-0.7.0.zip` was built on 2026-11-19 with
`tools/build_extension.py --version 0.7.0 --output-dir <folder>` (on the build machine), from
blender_native `44e27e0` and ICParse `d152d10`: 8,721,036 bytes (sha256
`5c10b1d45aaf0bf95831b25cd95c31529294fb127e721dcdfc578a5d614a46d4`), 95 entries: the add-on's 68 modules (39 in the
add-on folder, 29 in `sceneobj/`; the 19 new ones are the map modules `map_hooks`, `map_settings`, `map_paint`, `map_overlays`,
`map_script`, `map_cinematics`, `map_tools`, `map_crop` and their `ops_map_*` / `panels_map_*` files), `README.md`, the
generated manifest (version 0.7.0), the fallback copy of `construct` in `_deps/` and 7 wheels (construct 2.10.70, icparse
0.1.0+hbc707e5903ed, combotest 0.0.1+h8eb8c35d0d42 unchanged, and lupa 2.8 for the four platforms). `bl_info` and the repository's
`blender_manifest.toml` say 0.7.0. A copy of the zip is on the laptop in the workflow's scratch folder (`lap/ext/`); the same
sha256 was checked there.

*The previous release.* `sgm_native_patches-0.6.0.zip` was built on 2026-11-18 with
`tools/build_extension.py --version 0.6.0 --output-dir <folder>` (on the build machine), from
blender_native `a9961dc` and ICParse `5cef2d9`: 8,526,335 bytes (sha256
`4804b7991bd376c01ae1dc89edb70638d89d73f809c33b5245572870fbf8e564`), 76 entries: the add-on's 49 modules (39 in the
add-on folder, 10 in `sceneobj/`, the two new ones being `map_look.py` and `map_creatures.py`), `README.md`, the generated
manifest (version 0.6.0), the fallback copy of `construct` in `_deps/` and 7 wheels (construct 2.10.70, icparse
0.1.0+h490241a757bd, combotest 0.0.1+h8eb8c35d0d42, unchanged, and lupa 2.8 for Linux x86_64 and aarch64, macOS arm64 and
Windows x64). `bl_info` and the repository's `blender_manifest.toml` say 0.6.0. A copy of the zip is on the laptop in the
workflow's scratch folder (`lap/ext/`); the same sha256 was checked there.

*The previous release.* `sgm_native_patches-0.5.0.zip` was built on 2026-11-18 with
`tools/build_extension.py --version 0.5.0 --output-dir <folder>` (on the build machine), from
blender_native `455bc94` and ICParse `593c641`: 8,489,776 bytes (sha256
`5335b47a1ae30a8fca7db68fb660c01e304e65574cb2e0f3719211f97193a7b4`), 74 entries: the add-on's 47 modules (39 in the
add-on folder, 8 in `sceneobj/`, the two new ones being `ops_map_export.py` and `panels_map.py`), `README.md`, the
generated manifest (version 0.5.0, `blender_version_min = "5.3.0"`), the fallback copy of `construct` in `_deps/` and 7
wheels (construct 2.10.70, icparse 0.1.0+h99e946259499, combotest 0.0.1+h8eb8c35d0d42, unchanged since 0.3.1, and lupa 2.8
for Linux x86_64 and aarch64, macOS arm64 and Windows x64). `bl_info` and the repository's `blender_manifest.toml` say
0.5.0, so `build_extension.py` without `--version` builds 0.5.0. A copy of the zip is on the laptop in the workflow's
scratch folder (`lap/ext/`); the same sha256 was checked there.

*The previous release.* `sgm_native_patches-0.4.0.zip` was built on 2026-11-18 with
`tools/build_extension.py --version 0.4.0 --output-dir <folder>` (on the build machine), from
blender_native `7530197` and ICParse `88b096e`: 8,447,679 bytes (sha256
`a14315c3d1435de0718bd483963ec324209fdee7743bb256add6f3b3bd5ba8bf`), 72 entries: the add-on's 45 modules (39 in the
add-on folder, 6 in `sceneobj/`, the new one being `ops_map_import.py`), `README.md`, the generated manifest (version
0.4.0, `blender_version_min = "5.3.0"`, the tagline "Make and edit Impossible Creatures models in Blender"), the
fallback copy of `construct` in `_deps/` and 7 wheels (construct 2.10.70, icparse 0.1.0+h081130d3c129, combotest
0.0.1+h8eb8c35d0d42, the same combotest as in 0.3.1, and lupa 2.8 for Linux x86_64 and aarch64, macOS arm64 and
Windows x64). A copy of the zip is on the laptop in the workflow's scratch folder (`lap/ext/`).

*The release before that.* `sgm_native_patches-0.3.1.zip` was built on 2026-11-17 with
`tools/build_extension.py --version 0.3.1` (on the build machine: the build output folder): 8,404,454 bytes
(sha256 `9ca3225627e8f463a2410304ff779e4f0109cd9d2e3770f8fc87d7fcf076f2f9`), 71 entries: the add-on's 44 modules (39
in the add-on folder, 5 in `sceneobj/`), `README.md`, the generated manifest (`blender_version_min = "5.3.0"`), the
fallback copy of `construct` in `_deps/` and 7 wheels (construct 2.10.70, icparse 0.1.0+h77a9ed3bb7e8, combotest
0.0.1+h8eb8c35d0d42, lupa 2.8 for Linux x86_64 and aarch64, macOS arm64 and Windows x64). Against
`sgm_native_patches-0.3.0.zip` (built the same evening, 8,429,101 bytes, the library wheels still named icparse 0.1.0
and combotest 0.0.1) it has the same 71 entries in the same order: only the manifest's version, the names of the two
library wheels and `README.md` differ, and every add-on file and every library file inside the wheels is
byte-identical (compared 2026-11-18). Its `README.md` is the one of commit `482065e`, older than the README fixes
made since; rebuild the zip to ship them. A release sets the zip's version with `--version` (without it,
`build_extension.py` takes `bl_info`'s, still 0.1.0); the repository's `blender_manifest.toml`, the development
manifest, says 0.3.1.

### Verified for 0.8.0 (2026-11-19)

Code under test: blender_native `4787b61`, ICParse `1759f30` (root pointers committed afterwards). Three builders worked in parallel (the
cinematic timeline in ICParse; the cut-scene (NIS) stand-in; the replay itself); this release is their integration. Nothing was run in the
game, the Object Editor or the Mission Editor, nothing under the game folder was written, no screenshot was taken or compared; everything is
checked by numbers (events, positions at given frames, counts, timings, bytes). The Event list and Look-through buttons were driven as
operators only; no window was opened.

| Where | What | Result |
|---|---|---|
| build machine, Blender 5.3.0 (snap) | the whole `run_all.py` | **79 / 79** (includes `so_map_nis_test` 84 s and `so_map_replay_test` 76 s, `extension_install_test`) |
| | `tools/so_sweep.py --identity` | **205** donors: **203 byte-identical**, 2 refused, 0 failed; baseline held; the game install unchanged; 94 s |
| | `tools/map_sweep.py` (every one of the 93 maps imported and saved untouched, side files compared) | **92 of 93 byte-identical** in 159 s; the exception is the known `zombies_hill_fort_spec` (differs with no step registered) |
| | the whole ICParse suite (`pytest ICParse/tests`) | **2,563 passed, 2 failed**: the two known failures (`test_sga_integrity[dinodawndata.sga]`, `test_every_archived_sgm_is_100pct` on `fe_lab`); 1,004 s |
| | `tools/replay_sweep.py` (new): a replay of every cinematic of every stock map: build, read counts, clear, check the scene is back, and the save with the longest replay shown and after clearing all must equal the save with none | **93 maps, 17 with cinematics, 76 replays built, 0 failed**, all 17 maps' saves identical shown and cleared (map file and every file beside it); build 0.01 to 2.9 s each (the NIS ones are the slow ones), all 76 timelines and builds 47 s together |
| | `extension_install_test.py --zip <0.8.0> --older-zip <0.7.0>` | **pass** (18 s) |
| the user's laptop, Blender 5.3.0 (snap), a throw-away `BLENDER_USER_RESOURCES` (deleted afterwards; `~/.config/blender` and the game folder fingerprinted before and after: unchanged) | the 0.8.0 zip installed with `extension install-file`, enabled, ICParse from the installed wheel; `mission1` imported with everything on; the panel's operators Find, Choose, Build for *New lab intro*; stand-ins read at three frames; the markers; Clear; saves compared | **pass**: 8 cinematics listed; 17 stand-ins (13 that move), 60 events (23 exact, 28 approximate, 9 skipped), 28 markers in time order, frames 1 to 3462 at 30 a second, built in 3.8 s; the save with the replay shown and after Clear are both the map and its 10 side files byte for byte; the frame rate, range, camera and frame are back |

**Replays of every stock cinematic** (`replay_sweep.py`, models off; events by the timeline's own quality):

| Map | Cinematics | Built | Failed | Exact | Approximate | Skipped |
|---|---|---|---|---|---|---|
| mission1 | 8 | 8 | 0 | 80 | 111 | 11 |
| mission2 | 6 | 6 | 0 | 33 | 144 | 16 |
| mission3 | 5 | 5 | 0 | 43 | 30 | 8 |
| mission4 | 5 | 5 | 0 | 46 | 121 | 7 |
| mission5 | 6 | 6 | 0 | 66 | 213 | 8 |
| mission6 | 6 | 6 | 0 | 63 | 115 | 18 |
| mission7 | 4 | 4 | 0 | 38 | 166 | 17 |
| mission8 | 6 | 6 | 0 | 54 | 142 | 22 |
| mission9 | 4 | 4 | 0 | 40 | 91 | 10 |
| mission10 | 3 | 3 | 0 | 33 | 79 | 7 |
| mission11 | 3 | 3 | 0 | 34 | 77 | 4 |
| mission12 | 3 | 3 | 0 | 21 | 39 | 8 |
| mission13 | 6 | 6 | 0 | 67 | 128 | 16 |
| mission14 | 4 | 4 | 0 | 33 | 99 | 18 |
| mission15 | 3 | 3 | 0 | 24 | 10 | 16 |
| mission15_nis6 | 2 | 2 | 0 | 29 | 0 | 4 |
| tutorial | 2 | 2 | 0 | 20 | 4 | 0 |
| **all** | **76** | **76** | **0** | **724 (29 %)** | **1,569 (63 %)** | **190 (8 %)** |

Mission1 one by one (duration is the timeline's; events exact / approximate / skipped): *Nis* 241.6 s, 3 events (3 / 0 / 0); *New lab intro*
109.4 s, 60 (24 / 30 / 6); *Tree smashing* 30.5 s, 6 (6 / 0 / 0); *Camera Intro Rex Lucy* 69.4 s, 38 (5 / 31 / 2); *Rex goes to the village*
82.9 s, 35 (5 / 30 / 0); *Village fire goes out NISlet and starts regular attack groups* 11.5 s, 33 (20 / 11 / 2); *Final NISlet* 58.4 s,
24 (14 / 9 / 1); *Win lose conditions* 8.1 s, 3 (3 / 0 / 0). (The cinematic builder's report gave 86, 37 and 5 events for three of these; the
numbers here are what `build` returns now, and the test's frozen per-kind counts for *Camera Intro Rex Lucy* add up to 38.) The replay's own report for a cinematic counts its quality a
little differently from the timeline's (it marks as approximate what it chooses, for instance a clip under a motion node): *New lab intro*
read 24 / 30 / 6 from the timeline and 23 / 28 / 9 from the replay of the map opened with its models.

By event kind (exact / approximate / skipped): camera 248 / 433 / 0, walk 0 / 312 / 28, anim 57 / 166 / 0, fade 107 / 86 / 0, face 16 / 106 / 0,
respawn 45 / 51 / 0, despawn 43 / 51 / 0, dialog 0 / 87 / 0, look_at 8 / 64 / 0, delete 27 / 35 / 0, teleport 29 / 29 / 0, cinematic_mode
68 / 30 / 0, move_anim 29 / 25 / 0, obj_anim 0 / 39 / 0, visible 14 / 15 / 0, anim_camera 16 / 10 / 0, decal 0 / 17 / 0, nis 13 / 1 / 0, focus
3 / 7 / 0, motion_var 1 / 5 / 0, skipped actions (`other`) 0 / 0 / 162.

**What works, what is partial, what is missing (the Replay).**

* *Works* (checked by numbers): the timeline is the game's tick (trigger passes at 1/8 s, folders in file order, conditions stopping at the
  first false one, one-shot and looping triggers, countdowns, the animation-position conditions), the camera and move paths use the game's
  own path code (the position spline, the speed curve, blend in, hold and blend out: the trigger data agree with it, e.g. mission1's MoveAnim 3
  is 540.49 m and a trigger waits for 539, 73 `AnimPos >=` thresholds across the stock maps all lie on their paths), the stand-ins stand
  where the timeline says at chosen frames, the cut-scene objects play their whole animation with their camera and the parts their clips
  hide stay hidden (camera error against the file 2e-5, bones against the layering reference 0.0008 m), and a replay never changes a save.
* *Partial:* walks are routes on the map's own cell grid at the unit's speed, not the game's pathfinder (312 walks approximate); every time
  after a speech is approximate (1,069 events) because the speech audio is in no archive we have, so a speech's length is estimated from its
  text; a PlayAnimation text is a motion node of the blueprint and which clip plays under it is inferred (the last non-idle leaf); clips play
  only on scene models of a map opened with its models, a creature placed in a map is still an unposed mesh (the clip number is keyed on a
  custom property only); the replay camera uses Blender's default lens; the game camera before and after a cinematic is unknown.
* *Missing:* sound and dialogue text (a marker only); the skipped actions (groups filled at run time, random wandering, command 17, push /
  pop camera setting, FX create); lights, effects and morph channels of cut-scene models; the camera of UseAnimationCamera on a model with no
  camera; choosing the starting assumptions in the panel (the module takes `assume=`).

### Verified for 0.7.0 (2026-11-19)

Code under test: blender_native `44e27e0`, ICParse `d152d10` (root pointers committed afterwards). Five builders worked in parallel on
the Mission Editor's features on top of the extension points (`sceneobj/map_hooks.py`, `ed73ff7`); this release is their
integration. Nothing was run in the game, the Object Editor or the Mission Editor, and no saved map has been loaded by the game; no
screenshot was taken or compared (everything is checked by numbers: counts, bytes, files read back, timings). No paint stroke, no
file browser and no panel was drawn in a window: the tests start the paint modes, write the pixels and colours a stroke writes, and
call the operators.

| Where | What | Result |
|---|---|---|
| build machine, Blender 5.3.0 (snap) | the whole `run_all.py`, 77 scripts | **77 / 77** (see the run log kept with the release; the first run of the integrated tree gave 76 / 77, the one failure being `so_map_settings_test` pinning the old "single player in the mp folder" warning, which the folder-follows-kind change removed) |
| | `run_all.py -k so_` (16 scripts: `so_model_pure`, `so_contract`, `so_registry` and the 13 `so_map_*` suites: import, edit, look, creatures, hooks, new, settings, paint, overlay, script, cinematics, tools, crop) | **16 / 16** |
| | `tools/so_sweep.py --identity` | **205** donors: **203 byte-identical**, 2 refused, 0 failed; baseline held; the game install unchanged; 106.7 s |
| | `extension_install_test.py --zip <the 0.7.0 zip> --older-zip <the real 0.6.0 zip>` | **pass** (19.1 s): install, use with no repository reachable, remove, upgrade over the real 0.6.0 zip |
| | the whole ICParse suite (`pytest ICParse/tests`, ICParse `d152d10`) | **2,525 passed, 2 failed**: the two known failures (`test_sga_integrity[dinodawndata.sga]`, `test_every_archived_sgm_is_100pct` on `art/ebps/ui/fe_lab.sgm`). One new failure on the first run, `test_triggers::test_every_arg_type_declares_its_evidence` (a builder's evidence strings did not start with the words the test requires), was fixed and the file re-run: 435 passed |
| | `tools/map_sweep.py` (new): every one of the game's 93 maps imported (models, creatures and look off) and saved untouched under its own name; the map file and every file a save copies beside it compared with the original | **92 of 93 byte-identical** in 176 s. The exception, `telluriandata.sga|scenarios/mp/zombies_hill_fort_spec.sgb`, differs with NO map step registered (6 placed objects, 1096, 1098, 1104, 1107, 1108, 1109, hold a hand-made matrix with a shear, which Blender's rotation and scale cannot carry, so the plan counts them as moved): a 0.6.0 limitation, not one of the features. The first sweep of the integrated tree found 15 template maps (`scenarios/mt/*`) rewritten by the Map settings step (a template's 0 players is shown as 1, which then looked like an edit): fixed (`map_settings.save_header` compares with what the panel was filled with) and the sweep repeated |
| | `tools/map_edits_07.py` (new), part 1: eight stock maps imported with everything on and saved untouched, every file beside them compared | **8 / 8 identical** (table below) |
| | part 2: one edit of each new kind on three maps (`mission1`, `2p_mystery_lake`, `6p_big_bog`), saved, the changed chunks and files listed, opened again and the edit read back, saved again (must equal the first save byte for byte) | **25 done, 2 skipped** (no NISlets in those two maps), **0 failed**. A look value changes only `TERR/EFFC`; a blocked area `TERR/TFAC` and the pathfinding record `PFDR/NRML`; a terrain type only `TERR/TFAC`; a decal moved only `DECL/ENTY`; a group only `EGRP/ESGP`; a variable name only `TVRS/TVAR`; a trigger only `<name>.trg` (the map file untouched); a NISlet key only `<name>.na` (the map file untouched); a crop makes a map of (points - 1) / 2 + 1 points that reads with no problem |
| | part 3: a new map from each of the 15 templates (2 to 6 players, starts on), saved ready to drop, read again, the problem list, `validate.check_board`, opened again and saved again | **15 / 15**: no error in the problem list, no board problem, 7 files each, the second save identical; 1.1 to 1.8 s to make, 0.7 to 1.1 s to save |
| the user's laptop, Blender 5.3.0 (snap), a throw-away `BLENDER_USER_RESOURCES` (deleted afterwards; `~/.config/blender` and the game folder fingerprinted before and after: unchanged) | the 0.7.0 zip installed with `extension install-file`, enabled, ICParse imported from the installed wheel, the repository not importable | **3 / 3**: a new map from the Savannah small template (name, look, fog, a blocked area, a group, a trigger; saved ready to drop, opened again, every edit read back, saved again identical); `blender_native` not importable; `mission1` imported with everything on and saved untouched: the map and its 10 side files byte for byte (import 7.9 s, save 0.8 s) |

**Untouched saves with everything on** (`map_edits_07.py` part 1; the first import of a run carries the start-up cost; "files" counts
the map and the files beside it):

| Map | Size | Placed | Files | Import (all on) | Save | Identical |
|---|---|---|---|---|---|---|
| des_s | 65 x 65 | 0 | 10 | 2.15 s | 0.61 s | yes |
| 2p_mystery_lake | 65 x 65 | 326 | 10 | 1.97 s | 0.63 s | yes |
| 2p_football_pool | 65 x 65 | 612 | 11 | 0.97 s | 0.41 s | yes |
| 4p_thin_ice | 129 x 129 | 358 | 2 | 1.02 s | 0.33 s | yes |
| mission1 (campaign) | 129 x 129 | 338 | 11 | 3.98 s | 0.65 s | yes |
| mission12 (campaign) | 129 x 129 | 731 | 11 | 5.83 s | 0.77 s | yes |
| 6p_big_bog | 129 x 129 | 2083 | 10 | 1.34 s | 0.51 s | yes |
| 6p_bikini_atoll | 257 x 257 | 1786 | 10 | 3.06 s | 0.89 s | yes |

Timings of edits (headless, the build machine): painting a blocked 3 x 3 block and saving 0.9 to 2.0 s (the pathfinding is patched); every
other edit saves in under 1 s; a **crop** takes 9 s on `mission1`, 15.7 s on `6p_big_bog` and 22 s on `2p_mystery_lake` (the crop, its
pathfinding rebuild and building the new map; not understood why the smallest map is the slowest: open).

**What works, what is partial, what is missing, per group.**

* *New map and settings* (`ops_map_new.py`, `map_settings.py`, `ICParse.sgb.scenario`, `look_edit`): works: 15 templates, name, kind, players,
  starts, the editable Terrain look (written only when different), preview pictures, the problem list; the save folder follows the kind
  (since the integration). Partial: the Blender-rendered minimap is an approximation of the editor's; the sea colour record is added to maps
  that have none on trust; a template's own 0 players shows as 1 until you set it (and is then not listed by the game for fewer than 2).
  Missing: AI Scripts and Armies beyond a path per slot, more than 6 players.
* *Painting* (`map_paint.py`, `map_overlays.py`): works: passability (pathfinding patched exactly as the game's builder rebuilds it), the 10
  overlays, terrain types, water levels, decal handles, blend tiles. Partial: texture stamping writes `<name>.tga`, but the game draws a stamp
  only at its highest ground detail level (the `_decaled` pictures are not regenerated: their relation to the base picture is unknown); terrain
  types 5 and 6 are kept, not named. Missing: stamping as a drag stroke with the eyedropper.
* *Scripting* (`map_script.py`): works: groups, variable names, triggers with typed arguments from the full vocabulary, dialogues, per-player
  AI paths, the text table and blueprint list, the script check. Partial: 48 opcodes' named choices (keys, research, commands, stances,
  abilities, animations) are plain numbers; 47 opcodes have no argument signature; a group cannot take an object added in this session until
  the map has been saved and opened again. Missing: the Script Editor.
* *Cinematics* (`map_cinematics.py`, `ICParse.sgb.nislet`): works: all 434 stock animations read and write byte for byte, key frames as empties,
  edit, add, delete, aim, preview, checks. Partial: the preview path is a plain Catmull-Rom (tension, continuity and bias are kept exactly but not
  drawn), roll sign unverified, a camera on a group does not aim at the group's centre in the preview. Missing: Orbit Target, Third person view,
  Path Placement Mode as such.
* *Files, lists and crop* (`map_tools.py`, `map_crop.py`): works: height map, `.ter`, `.ent`, the Object List with its filters, the Blueprint List,
  Terrain Crop with Validate (a cropped `6p_worldmap`, 1025 points and 4798 objects, comes out 257 points with 482 objects in 4.4 s). Partial: the
  Object List categories are inferred from blueprint folders; a crop or terrain import builds a new in-memory map (since the integration its
  `.trg`, `.dlg` and `.na` are read from the files kept for it). Missing: a combined creature in a `.ent`; a crop square outside the map;
  the editor's dropdown of stock creatures.

### Verified for 0.6.0 (2026-11-18)

Code under test: blender_native `a9961dc`, ICParse `5cef2d9`. Nothing was run in the game, the Object Editor or the Mission
Editor, and no saved map has been loaded by the game; no screenshot was taken or compared (the look is checked by numbers).

| Where | What | Result |
|---|---|---|
| build machine, Blender 5.3.0 (snap) | the whole `run_all.py`, 68 scripts | **68 / 68** (827.3 s of test time, about 13.8 min; `so_map_creatures_test` and `so_map_look_test` are new, `so_map_edit_test` 20.9 s, `so_map_import_test` 15.3 s) |
| | `tools/so_sweep.py --identity` | **205** donors: **203 byte-identical**, 2 refused, 0 failed; baseline held; the game install unchanged; 97.6 s |
| | `extension_install_test.py --zip <the 0.6.0 zip> --older-zip <the real 0.5.0 zip>` | **pass** (16.4 s): installed, used from a Blender that cannot see the repository, removed; the real 0.5.0 upgraded in place (ICParse 0.1.0+h99e946259499 -> 0.1.0+h490241a757bd, combotest unchanged) and reinstalled; in one session 0.5.0 enabled and the 0.6.0 zip installed over it with no stale reference, then a newer build with other library names |
| | the whole ICParse suite (`pytest ICParse/tests`, ICParse `5cef2d9` plus the one-line navgen docstring) | **2,445 passed, 2 failed**, 12 min 48 s: the same two known failures (`test_sga_integrity[dinodawndata.sga]`, `test_every_archived_sgm_is_100pct`); the reconstruct baseline was refreshed (see below) |
| | `tools/map_roundtrip.py`, eight stock maps of three sizes, 110 s | **8 / 8**: each map imported with everything off and with everything on, saved untouched (**byte-identical on all 8**), then one save holding every kind of edit (a 5 x 5 block raised by 8 steps, Redraw water, the water up 1 m, one block painted, an entity moved and turned, one deleted, one given to player 1, a savbush added, and **a creature moved 3 m** on the maps that have creatures), read back exactly and opened again with a fresh plan that finds nothing |
| the user's laptop, Blender 5.3.0 (snap), a throw-away `BLENDER_USER_RESOURCES` (deleted afterwards; `~/.config/blender` and the game folder fingerprinted before and after: unchanged) | the 0.6.0 zip (sha256 as above) installed with `blender --background --command extension install-file -r user_default -e`; ICParse and the add-on loaded from that profile | **4 / 4**: henchmen (383,819 B) and the Lab (366,534 B) projects exported untouched byte-identical; 2p_faceoff with everything on (164 placed, 155 with a model, 1 sun, 1 sea object, 211 decals, 1 sky dome, 1.96 s) saved untouched byte-identical (198,438 B); mission1 with everything on (21 of 21 creatures as models, 8 kinds built, 1 sun, 415 decals, 3.7 s) saved untouched byte-identical (768,134 B), then a 5 x 5 hill, Redraw water and one creature moved 3 m, saved (768,118 B), the heights and the creature's position read back exactly, the other 337 entities exactly as they were, and the saved map opened again with 21 creatures and no changes |

**Maps, everything on and off** (`tools/map_roundtrip.py`, one run on the build machine; "off" is Placed models, Creatures and Game look
off, water on, which is what 0.5.0's tool measured; the first import of a run carries the one-time start-up cost):

| Map | Size | Placed | Import off / on | Shown with a model (creatures) | Decals | Edited save |
|---|---|---|---|---|---|---|
| des_s | 65 x 65 | 0 | 0.86 / 0.45 s | 0 | 0 | 0.84 s |
| 2p_faceoff | 65 x 65 | 164 | 0.60 / 0.63 s | 155 (0) | 211 | 0.57 s |
| 2p_mystery_lake | 65 x 65 | 326 | 0.64 / 0.69 s | 288 (0) | 180 | 0.66 s |
| 4p_thin_ice | 129 x 129 | 358 | 0.38 / 0.63 s | 314 (0) | 303 | 2.01 s |
| mission1 | 129 x 129 | 338 | 0.67 / 1.86 s | 278 (21 of 21) | 415 | 2.87 s |
| mission12 | 129 x 129 | 731 | 0.71 / 2.88 s | 670 (52 of 52) | 592 | 2.03 s |
| 6p_big_bog | 129 x 129 | 2083 | 0.53 / 0.86 s | 1930 (0) | 888 | 1.82 s |
| 6p_bikini_atoll | 257 x 257 | 1786 | 0.91 / 1.26 s | 1700 (0) | 1903 | 7.61 s |

**Pathfinding, the 0.5.0 estimate against the game's method** (cells of the 2 m cell map that differ from the map's stored
one; the estimate is `navgen.generate_impa` from the terrain alone, which is what 0.5.0's "rebuild" started from before it
stamped the known footprints; the new column is `pathbake.bake_map`, which applies the painted layer and the scenery):

| Map | Cells | Old estimate differs | Game method differs | Painted cells applied | All three records identical |
|---|---|---|---|---|---|
| des_s | 16,384 | 0 | 0 | 0 | yes |
| 2p_faceoff | 16,384 | 1,361 | 0 | 1,461 | yes |
| 2p_mystery_lake | 16,384 | 1,406 | 0 | 938 | yes |
| 4p_thin_ice | 65,536 | 1,879 | 0 | 1,932 | yes |
| mission1 | 65,536 | 2,599 | 52 | 1,801 | no (52 steep shore cells) |
| mission12 | 65,536 | 1,993 | 0 | 987 | yes |
| 6p_big_bog | 65,536 | 3,220 | 0 | 2,036 | yes |
| 6p_bikini_atoll | 262,144 | 35,443 | 0 | 19,167 | yes |

Over the 92 stock maps that carry pathfinding (`ICParse/tests/test_sgb_pathbake.py`, 7 tests): precise map 92 of 92 byte-identical,
sector map from the stored records 92 of 92, cell map 91 of 92 (`mission1`: 52 of 65,536 cells, cause not found). The refreshed
reconstruct baseline (`tests/sgb_reconstruct_baseline.json`) is a terrain-only comparison, so with the game's method its PASM
difference rises on 21 maps while the cell-map difference falls; it is no longer a quality measure for the builder (it has no
painted layer and no scenery), `test_sgb_pathbake.py` is. `6p_worldmap` is not a game bake and is skipped.

Not run for 0.6.0: the engine tiers (`--engine`, `--parity`); the game; a look at the map (the sun's strength, the fog's thickness,
the sky dome's shape and the sea's opacity are the add-on's choices or inferences and need a human eye); the real file browser, drawing
the Map panel in a window, Sculpt Mode strokes and the automatic redraw while sculpting in a window (it is tested through a depsgraph
update, not a stroke).

### Verified for 0.5.0 (2026-11-18)

Code under test: blender_native `fb9c800` (the version bump; every later commit is a document), ICParse `593c641`. Nothing
was run in the game, the Object Editor or the Mission Editor, and no saved map has been loaded by the game.

| Where | What | Result |
|---|---|---|
| build machine, Blender 5.3.0 (snap) | the whole `run_all.py`, 66 scripts | **66 / 66** (863.7 s of test time, about 14.4 min; `so_map_edit_test` 21.0 s, `so_map_import_test` 11.6 s) |
| | `tools/so_sweep.py --identity` | **205** donors: **203 byte-identical**, 2 refused (the gyro copters: no mesh), 0 failed; baseline held; the game install unchanged; 98.2 s |
| | `extension_install_test.py --zip <the 0.5.0 zip> --older-zip <the real 0.4.0 zip>` | **pass** (17.2 s): installed, used from a Blender that cannot see the repository, removed; the real 0.4.0 upgraded in place (ICParse 0.1.0+h081130d3c129 -> 0.1.0+h99e946259499, combotest unchanged) and reinstalled; in one session 0.4.0 enabled, then 0.5.0 (the libraries in memory unloaded and loaded again, no stale reference), then a newer test build that needs a new library name while it loads, then that build again |
| | the whole ICParse suite (`pytest ICParse/tests`, ICParse `593c641`) | **2,427 passed, 2 failed**, 12 min 59 s: the two known failures, `test_sga_integrity[dinodawndata.sga]` (a zlib error in the install's archive) and `test_every_archived_sgm_is_100pct` (fe_lab's PAL8/IMAG chunks count as 63,488 unknown bytes; its round trip is exact). The third known failure of 0.4.0, `test_reconstruct_no_regression[6p_big_bog.sgb0]`, is gone: the baseline was keyed by file name, and the InsectData and telluriandata copies of 6p_big_bog differ (InsectData IMPA 3,220, PRMP 34,017, PASM 99,587 differing bytes; telluriandata 3,152 / 32,132 / 99,026), so `sgb_reconstruct_baseline.json` is now keyed per archive (82 keys) |
| | map round trips on seven stock maps of four sizes, one Blender run (`tools/map_roundtrip.py`, 90 s): import, save untouched, then one save holding every kind of edit (a 5 x 5 block of points raised by 8 steps, the water up 1 m, one 8 x 8 block painted with a ground texture the map did not have, an entity moved and turned, one deleted, one given to player 1, a savbush added at the 3D cursor), the saved file read back with `ICParse.sgb.view.read` and opened again | **7 / 7**: every untouched save byte-identical; every edit read back as made (heights, water level, the one painted block, the entities one by one; every other entity equal to the last bit; `problems == []`); the saved map opens again and a fresh plan finds no changes; chunks changed only `TERR/TFAC`, `ENTT`, `PLYR`, `BNDT` (only when a new blueprint joined the list) and `PFDR/NRML` (the pathfinding) |
| | what each of those seven did (the table below) | see below |
| the user's laptop, Blender 5.3.0 (snap), a throw-away `BLENDER_USER_RESOURCES` (deleted afterwards; `~/.config/blender` and the game folder fingerprinted before and after: unchanged) | the 0.5.0 zip (sha256 as above) installed with `blender --background --command extension install-file -r user_default -e`, then 12 checks in one headless Blender, the add-on and ICParse loaded from that profile | **12 / 12**: the nine of 0.4.0 again (henchmen and Lab untouched byte-identical, 383,819 and 366,534 bytes; the henchman subdivided 696 -> 2,142 vertices with skin, skeleton and clips identical; 37,824 vertex entries refused; the Lab tent refused, then written with the drop option; the Blueprint panel and Export Entity Folder for both; 2p_faceoff 164 placed, 155 with a model, 27 models, 1,330 water cells, 1.6 s; 6p_bikini_atoll 1,786 placed, 1,700 with a model, 47 models, 41,850 water cells, 1.7 s) and three new ones: 2p_faceoff imported with its 27 models and saved untouched is byte-identical (198,438 bytes, written with its 8 sibling files and the loading folder); a 7 x 7 hill (49 points, 6 m up), a painted block, one object moved, one deleted and a savbush added come back from `view.read` exactly as made (198,355 bytes, 1.85 s to save), the saved file opens again and a fresh plan finds nothing; the game's own folder is refused |

The seven maps, from `tools/map_roundtrip.py` (the timings are one machine, Blender 5.3 headless; the *rebuild* figures come
from `ICParse.sgb.edit.pathfinding_fidelity` on the untouched map, which is what the save report quotes next to a rebuild,
and say how close a from-scratch rebuild is to the game's own bake: IMPA from the terrain alone / with the footprints of
the known blocking placements, PRMP built from the stored IMPA, and whether PASM built from the stored records is
byte-identical):

| Map | Size | Untouched save | Edited save | What the default "update" changed in the pathfinding | IMPA rebuild: terrain / + footprints | PRMP | PASM |
|---|---|---|---|---|---|---|---|
| des_s (Engine.sga) | 65 x 65, 0.5 m steps, 0 placed | 166,253 B back byte-identical (0.4 s) | 167,682 B, 1.46 s | 316 IMPA and 3151 PRMP cells; sectors 197 -> 215 | 100.0% / 100.0% | 100.000% | byte-identical |
| 2p_mystery_lake (Engine.sga) | 65 x 65, 0.5 m steps, 326 placed | 216,543 B back byte-identical (0.42 s) | 218,482 B, 1.42 s | 707 IMPA and 1222 PRMP cells; sectors 299 -> 334 | 91.4% / 94.8% | 100.000% | byte-identical |
| 2p_football_pool (InsectData.sga) | 65 x 65, 0.5 m steps, 612 placed | 265,543 B back byte-identical (0.07 s) | 266,034 B, 1.05 s | 255 IMPA and 1849 PRMP cells; sectors 291 -> 298 | 77.2% / 79.3% | 99.988% | byte-identical |
| 4p_thin_ice (telluriandata.sga) | 129 x 129, 0.4 m steps, 358 placed | 752,621 B back byte-identical (0.08 s) | 753,808 B, 5.45 s | 794 IMPA and 2253 PRMP cells; sectors 924 -> 943 | 97.1% / 98.4% | 99.995% | byte-identical |
| mission12 (Engine.sga) | 129 x 129, 0.5 m steps, 731 placed | 821,651 B back byte-identical (0.45 s) | 823,230 B, 5.06 s | 749 IMPA and 2972 PRMP cells; sectors 1129 -> 1155 | 97.0% / 98.0% | 99.989% | byte-identical |
| 6p_big_bog (InsectData.sga) | 129 x 129, 0.5 m steps, 2083 placed | 944,756 B back byte-identical (0.22 s) | 945,519 B, 4.44 s | 574 IMPA and 2462 PRMP cells; sectors 989 -> 1006 | 95.1% / 97.9% | 99.995% | byte-identical |
| 6p_bikini_atoll (Engine.sga) | 257 x 257, 0.5 m steps, 1786 placed | 2,852,215 B back byte-identical (0.65 s) | 2,857,386 B, 20.76 s | 6113 IMPA and 11574 PRMP cells; sectors 4404 -> 4503 | 86.5% / 87.7% | 99.995% | byte-identical |

Reading the table: the default save patches only the cells the edit changes, so none of this loss applies to it; the
*rebuild* columns are what choosing "Rebuild from the terrain" would give on that map. Over the 92 distinct
stock maps (`6p_worldmap` excluded: its sector rebuild takes minutes) the library measured, on the 77 that place
entities, the IMPA with footprints at min 79.4% (`2p_football_pool`), median 97.1%, mean 95.1%, max 99.7%, and the 15
maps with no placements rebuilt byte for byte; the cells the stored map blocks and a rebuild leaves open are 3.9% of all
cells, the reverse 2.3% (`ICParse/tests/test_sgb_edit.py`, `pathfinding_fidelity`). The remaining difference is mostly cells that are not near any
placement, inferred to be what the map's author painted impassable and the shores; nothing can regenerate them.

Not run for 0.5.0: the engine tiers (`--engine`, `--parity`), which drive the game's own Object Editor and ComboTest.exe
under Proton and are run only on request; nothing was run in the game, so a saved map has not been loaded by it; the real
file browser, drawing the Map panel in a window and Sculpt Mode strokes (the code leaves Sculpt Mode before it reads, and
the panel was drawn only into a layout stand-in); the Install from Disk dialog (every install was from the command line).

### Verified for 0.4.0 (2026-11-18)

| Where | What | Result |
|---|---|---|
| build machine, Blender 5.3.0 (snap) | the whole `run_all.py`, 65 scripts, on blender_native `efde0df` | **65 / 65** (789 s of test time, about 14 min) |
| | after the last code change (`ba48aab`: the model import reads the Game folder): `-k scene_`, `-k so_`, `-k new_project`, `-k blueprint_poc`, `-k blender_smoke`, `-k ebp_import` | **4 / 4, 4 / 4, 1 / 1, 1 / 1, 1 / 1, 1 / 1** |
| | `tools/so_sweep.py --identity` | **205** donors: **203 byte-identical**, 2 refused (the gyro copters: no mesh), 0 failed; baseline held; the game install unchanged; 89.3 s |
| | `extension_install_test.py --zip <the 0.4.0 zip> --older-zip <the 0.3.1 zip>` | **pass** (16.2 s): installed, used from a Blender that cannot see the repository, removed; the real 0.3.1 upgraded in place and reinstalled (ICParse 0.1.0+h081130d3c129, combotest unchanged); in one session: 0.3.1 enabled, then 0.4.0 (the libraries in memory unloaded and loaded again, no stale reference), then a newer test build that needs a new library name while it loads, then that build again (kept in memory) |
| | the whole ICParse suite (`pytest ICParse/tests`, ICParse `88b096e`) | **2,399 passed, 3 failed**, 6 min 47 s: the three known failures, `test_sga_integrity[dinodawndata.sga]` (a zlib error in the install's archive, modified 2026-11-15), `test_reconstruct_no_regression[6p_big_bog.sgb0]` (the IMPA rebuild, 3,152 -> 3,220 differing bytes, failing before this release's work) and `test_every_archived_sgm_is_100pct` (fe_lab's PAL8/IMAG chunks count as 63,488 unknown bytes; its round trip is exact) |
| the user's laptop, Blender 5.3.0 (snap), a throw-away `BLENDER_USER_RESOURCES` (deleted afterwards; `~/.config/blender` and the game folder fingerprinted before and after: unchanged) | the 0.4.0 zip installed with `blender --background --command extension install-file -r user_default -e`, then 9 checks in one headless Blender, the add-on and ICParse loaded from that profile | **9 / 9**: the henchmen project (1 part, 696 vertices) and the Lab project (23 parts, 3,289 vertices) export untouched byte-identical (383,819 and 366,534 bytes); the henchman subdivided (696 -> 2,142 vertices, 788 -> 3,152 triangles) exports with its skin, the skeleton and clips byte-identical; subdivided three times and flat-shaded it comes to 37,824 vertex entries and is refused with nothing written; the Lab's edited tent (11 damage states) is refused, then written with the drop option (the other parts and the clips byte-identical), and the same through Export Entity Folder; the Blueprint panel loads the henchmen (`$25008`, class 3, 1 x 1, `gatherers`, 65 numbers) and the Lab (`$41508`, class 5, 7 x 5, `structures`, 47 numbers), Export Entity Folder writes both byte-identical with a README, and a renamed one reads back; maps from the game list (96 on the laptop): 2p_faceoff 65 x 65, 164 placed, 155 with their model (27 models), 9 sound emitters as empties, 1,330 water cells, 0.78 s; 6p_bikini_atoll 257 x 257 (1,024 m), 1,786 placed, 1,700 with their model (47 models), 86 sound emitters as empties, 41,850 water cells, 1.08 s; no model failed |

Not run for 0.4.0: the engine tiers (`--engine`, `--parity`), which drive the game's own Object Editor and
ComboTest.exe under Proton and are run only on request; nothing was run in the game, and the Install from Disk
dialog was not tried (every install was from the command line).

### Verified for 0.3.0 and 0.3.1 (2026-11-17 and 2026-11-18)

| Where | What | Result |
|---|---|---|
| the user's laptop, Blender 5.3, a throw-away profile (run by the workflow that built the release) | 0.3.0 installed from the zip, then 8 checks: a creature on the default skeleton; a gatherer (henchmen) and a structure (lab) project; the henchman subdivided (696 → 2,142 vertices) and exported with its skin and clips; the 23-part lab with 1 part edited (1 rebuilt, 22 kept byte-identical); the Blueprint panel's load, untouched export (byte-identical), edit, export and re-read, for both projects | **8 / 8** |
| build machine, Blender 5.3.0 (snap, built 2026-11-10) | `run_all.py -k so_`: so_model_pure_test, so_contract_test, so_registry_test | **3 / 3** (1.2, 1.1, 1.4 s) |
| | `run_all.py -k scene_`: scene_import_test, scene_anim_export_test, scene_retopo_test, scene_geometry_export_test | **4 / 4** (3.2, 3.9, 21.2, 1.2 s) |
| | new_project_test, blueprint_poc_test, blender_smoke | **pass** (17.0, 35.8, 4.7 s) |
| | blueprint_poc_test, its corpus part | **479** stock blueprints of the five archives (first archive wins): 383 editable, 10 in the old layout, 86 with the model inside (export refused); **393 untouched exports byte-identical**; the 383 editable ones edited in all five fields re-read with the new values and change only the header, footprint and sound chunks |
| | `tools/so_sweep.py --identity` | **205** stock donors started as projects and exported untouched: **203 byte-identical**, 2 refused (the gyro copters: no mesh), 0 failed; the game install unchanged; 96.8 s |
| | `run_all.py -k extension_install`, after the upgrade fix (00:04 on 2026-11-18) | **pass**: the zip installed into a throw-away profile, used from a Blender that cannot see the repository, removed; an older build upgraded in place and reinstalled, the new libraries loaded each time |
| build machine, 2026-11-18 (the documentation check) | `extension_install_test.py --zip <the 0.3.1 zip> --older-zip <the 0.3.0 zip>` | **pass** (11.4 s): 0.3.1 installed, used from a Blender that cannot see the repository and removed; then, in a second throw-away profile, the real 0.3.0 installed and enabled, 0.3.1 installed over it with `extension install-file -r user_default -e` and no remove (`Reinstalled`), and installed once more: each time enabled, ICParse 0.1.0+h77a9ed3bb7e8 and combotest 0.0.1+h8eb8c35d0d42 imported from the new wheels, the 0.3.0 library files gone, `BZ_PT_blueprint` registered |
| | `run_all.py -k so_`, `-k scene_`, `-k new_project`, `-k blueprint_poc`, `-k blender_smoke` | **3 / 3, 4 / 4, 1 / 1, 1 / 1, 1 / 1** |
| | `tools/so_sweep.py --identity`, then `--all` | **205** donors: 203 byte-identical, 2 refused (90.9 s); **305** rows: 295 byte-identical, 10 refused (95.3 s); the game install unchanged both times |
| | a headless New Project probe (add-on registered from the repository, throw-away profile) | the numbers README.md and the guide quote: the Lab 23 parts, 3,289 vertices (largest part 1,119), 19 bones, 9 clips, name `$41508`, class 5, footprint 7 x 5, no sounds; the henchmen 1 part, 696 vertices and 1,446 edges, 26 bones, 32 clips, `$25008`, class 3, 1 x 1; the aviary `$41503`, class 9, 3 x 3, 22 sounds; Export Entity Folder of the renamed, edited Lab wrote `Data/art/ebps/structures/mylab.sgm`, `mylab.ebp` and `README.txt` (1 part rebuilt, 22 unchanged) and refused the game folder |
| | `ICParse.ebp.scene.SceneBlueprint` over every `art/ebps` blueprint of the five archives (first archive wins) | **479** (511 rows with the duplicates): 383 editable; 10 in the old layout (the AI map markers `aichokepoint`, `aihint`, `aiwall1`, `aiwall2`, the cut-scene props `nis_chair_1`-`4` and `nis_enviro`, the menu object `ui/creaturescale`); 86 with the model inside: 62 creatures (`stock/`, 61, and the cut-scene `kong`) and 24 older things (20 in `nature_old/`, 3 map markers, `projectiles/stub`), all 24 also in the old layout |

From the working notes of the same day: `so_sweep.py --all` over every `art/ebps` scene
row of every archive, 305 rows: 295 byte-identical, 10 refused because they hold no mesh (the 2 gyro copters, the 4 AI
location markers `locations/aichokepoint`, `aihint`, `aiwall1`, `aiwall2`, the 4 cinematics `nis/nis_1`, `nis_6`,
`nis_m15`, `nis_m3`; the same on 2026-11-18, above); `--idempotent` (export twice, export → import → export) 203 ok +
2 refused of 205; `--valid` (ICParse's checks on the exported bytes) 295 ok + 10 refused of 305.

Not run for 0.3.0 and 0.3.1: the whole `run_all.py` (then 64 scripts) and the engine tiers (`--engine`, `--parity`),
which drive the game's own Object Editor and ComboTest.exe under Proton and are run only on request. The last full runs
before 0.4.0 are below.

### Earlier runs

* **2026-11-16**, Blender 5.3: `run_all.py` 58 / 58 (with New Project; recorded in commit `0a8df00`).
* **2026-11-15**, Blender 5.3.0, after the correctness pass against the decompiled game: `run_all.py` 57 / 57
  (12.2 min; 41 / 57 before the fixes); `run_all.py --engine` 108 / 108 in one run (44.8 min): the 57 suites, the
  Object Editor loading every export the suites wrote with its pinned verdict (60 LOADED, 3 FATAL: the three
  deliberately open meshes; 63 exports, the textured pillbug not written because `3DInputs/Pillbug_ic` was not on the
  machine) and ComboTest.exe 27 / 27 (21 combinations exported and matched by our combiner with ComboTest's own limb
  rules, 4 refused with the expected assertion, 2 torn seams flagged). Inside Blender 5.3 ICParse reads and writes
  118 / 119 stock creatures byte-identically (poison_frog is the known gap); ICParse 2078 passed, 1 failed (the
  6p_big_bog map rebuild, failing since 2026-10-09, unrelated); combotest 753 passed, 4 skipped. The Object Editor
  captures of scorpion, lobster, cheetah and eagle agree with the corrected corner-normal rule (0.00-0.02 degrees) and
  their vertex counts with `display_vertices`.
* **2026-09-16**, Blender 5.2.2 LTS: `run_all.py` 57 / 57 (25.3 min); `--combine-full` 3 / 3 (855 designs each; the
  deliberately broken snail flagged in exactly its 244 designs); `--engine`: 64 exports, 61 LOADED and 3 FATAL as
  pinned, ComboTest.exe 27 / 27 after one picker flake was re-run; `--parity`: 9 / 9 assets render like the Object
  Editor (cheetah, scorpion, eagle, lobster, gazelle, henchmen lab, gyro copter, waterfall, the henchmen character:
  positions within 1.4e-6 m, normals 0.12°, UVs 2.4e-7, textures pixel-exact) and 4 / 4 animated cases (the editor's
  vertices against ICParse's decode 0.002 mm, Blender's armature within 0.07 % of the asset's size); combotest 744
  passed, 4 skipped.

**Byte identity, the contract, is checked from several directions:** `headless_identity` rewrites every creature of
`Assets/stock` + `Assets/engine_stock` through the full export builder with untouched state (118; poison_frog is
named, not hidden); nearly every in-Blender test asserts that import → export is identical before its edit and that
one Undo after the edit gives the same bytes; `extension_install_test` does it from a Blender that cannot see the
repository; `so_sweep.py --identity` does it for the 205 stock scene donors and `blueprint_poc_test` for the stock
blueprints.

### Known gaps and caveats: the one list

1. **Linux only.** Developed and tested on Linux (the snap Blender 5.3.0 on the build machine; the user's Blender 5.3). The
   add-on is pure Python and the zip carries the Windows x64 and macOS arm64 `lupa` wheels, but nothing here has run
   on either. `ICParse.data.default_root` knows only the Linux Steam folders, so elsewhere `IC_INSTALL` must be set.
   `tests/run_all.py` runs the Blender named by `$BLENDER`, else the one on the PATH (the snap build), and the
   tests use POSIX paths such as `/tmp`; the engine tiers run the real
   `ObjectEditor.exe` / `ComboTest.exe` under Proton + Xvfb. `lupa` has no wheel for Intel macOS or ARM Windows (the
   add-on then runs without the combiner's Lua ratings and says so).
2. **Nothing the add-on exported has been played in the game yet.** The engine verdicts are the Object Editor (does
   it load) and ComboTest.exe (does it combine, and are the vertices ours), for creatures. The Linux port
   (`localre/linuxport`) can run the game natively, but its vertex sets take 65,535 vertices and it zero-fills the
   smoothing word retail leaves uninitialised, so a port test proves neither the 12,288 ceiling nor a combined
   creature's smoothing for retail. Scene exports with new topology and every blueprint export have been checked
   offline only; the blueprint panel's in-game checklist (working notes) lists what only the game can show.
   The add-on does not write a creature's `.ebp` / `.lua` / `.lsc` package (a separate step by decision, 2026-09-13).
3. **Interactive paths are untested.** Headless Blender never redraws and never sends a mouse event, so the tests
   drive the functions the handlers call: the drag feel, box select's occlusion, every modal operator (Pick Ring, Pick
   Bone, the drag macros), the overlay's pixels (`--parity` checks the arrays it draws), texture paint and the Sculpt
   Mode brushes have not been driven by a test. As of its proof of concept nobody had seen the Blueprint panel drawn
   on a screen: its test draws every state into a stand-in layout that rejects unknown properties, operators and
   icons.
4. **Evaluated Surface is off by default, and that is a real trade.** Geometry Nodes re-evaluates the whole surface
   for one moved control point (12.0 ms at 800 patches against the overlay's 1.49 ms). Its surface is all-smooth,
   which is wrong at the multi-group corners of the 82 of 119 stock files that carry more than one smoothing group;
   the overlay is right.
5. **Transfer Texture costs patches and does not say so.** A patch costs 16 display vertices welded and 25 with its
   own texture island, which is what the per-patch atlas gives every patch (measured: island UVs 490 patches LOAD /
   492 CRASH, continuous UVs 754 LOAD / 758 CRASH). Validate counts it; the transfer's own report gives coverage only.
6. **Convert Mesh to Creature is still whole-Torso.** `core.mesh_to_creature` puts every point on one bone and every
   patch in limb 0 with no connections, so the result loads but does not combine until re-rigged; Fit Patches to Mesh
   has `fitops.rig_split` and does not share this.
7. **The pillbug (the one real scan-to-creature subject) is half solved.** At budget 430 the fit gives 332 patches
   (0.76 % of size rms) that load in the Object Editor and combine in ComboTest.exe; at budget 600, 518 patches load
   but will not combine (4 dangling edges, and 11,931 of 12,135 display vertices in the Torso, over the 6,584 a torso
   may spend; *corrected 2026-11-17: this said 6,665, the budget before the 2026-11-15 re-derivation*). The retargeted
   pillbug still has buckled legs (3 donor leg pairs against the scan's 7). Write-up: `3DInputs/Pillbug_ic/REPORT.md`.
8. **Per-patch material IDs and smoothing groups are preserved but not editable**; everything new is smoothing group
   1, and group 0 is refused (the game's corner weld writes past a stack array with it).
9. **Missing modelling operators:** bevel (Extrude Region's Outline tapers, it does not bevel), Add Tri, Detach,
   Reset Handles, Max's View Steps presets.
10. **Validate does not check everything it could:** the Auto Interior rule and repeated indices on a file that
    arrives off them, loop rules for the required animation set, the per-ID texture size table and 8-bit depth.
11. **Re-encoded clips drift slightly.** A re-encoded clip carries one rigid key per frame, so where the source had
    sparse keys the engine's between-key blend differs by up to about 0.3 mm on a 1 m creature. Untouched Actions are
    never re-encoded.
12. **Float32 is the contract.** Blender meshes are float32, so the export rewrites only the control points that
    moved; a pool-wide rewrite would break byte identity everywhere.
13. **Operational.** The snap Blender gets flaky after many rapid `--background` launches and must never be killed
    with `-9`; `tests/_so_common.blender_lock` spaces launches machine-wide. The older tests write fixed
    `/tmp/bz_*` paths, so two runs of the same older test at once can collide; the `so_` tests use fresh temp folders.
14. **The engine harness is flaky, and that must be told apart from a defect.** ComboTest.exe's picker occasionally
    does not take a creature (the log stops at `combomix.c: Creature1`, the row fails in about 39 s instead of 62 s);
    re-run a single row before believing it. The same words are the expected marker of the boneless-limb must-fail
    row, so the timing and a re-run tell the two apart.
15. **Scene objects: what the export does not do yet.**
    * Material, texture and team-colour edits are not exported. A face may move to another imported material slot; a
      slot with no shader in the file is refused.
    * Bone edits (move, rename, add, delete) are not written back, and the export does not warn. On a skinned
      character a bone renamed in Blender renames its vertex group too, so the export no longer matches those weights
      to a file bone: a vertex left with no weight is bound to the root bone and counted in the report.
    * Attachment points (markers) and damage states (morphs) are not shown or edited; an edited part with damage
      states is refused, or written without them on request (gap 16).
    * There is no checks panel; `ICParse.sgm.scene.validate.validate_scene_sgm` runs at export and refuses errors.
    * There is no install into a game copy: Export Entity Folder writes a folder the user copies.
    * The 10 rows without a mesh (above) cannot be imported, and a part stored as a `GEOM` block (projectiles,
      effects) is refused for a rebuild.
16. **Scene mesh export: the three known bugs are fixed** (2026-11-18, ICParse `4920876`, blender_native `d998ba8`;
    release 0.4.0). *Vertex limit:* `scene.build_part` and ICParse `write_scene_part` refuse a part above **12,288**
    vertex entries (`MAX_PART_VERTICES`, decompiled byte-exact from spDx8 `dx8VertexSet.cpp:63`, never run in the
    game); 0.3.1 allowed 65,535. *Bounds:* a rebuilt part gets the game's volumes, its own, one per palette bone and the
    entity box (ICParse `sgm.scene.bounds`; the rule reproduces the stock files, `ICParse/tests/test_scene_bounds.py`,
    measured on 468 models, RE_LOG 2026-11-18); 0.3.1 flattened a one-part model's volumes into one box and left a
    multi-part model's group box at the donor's. *Damage states:* an edited part with morphs is refused, or written
    without them with *Drop the damage states of edited parts* (File > Export and Export Entity Folder); a part left
    alone keeps them byte for byte. Above the largest stock part the scene validator already warns (its note is in
    the export report). **Still open:** editing damage states (carrying the morphs over to the new vertices), and the
    in-game check of the 12,288 limit and of a changed entity box.
17. **The Blueprint panel's limits.** The values live on one holder object (the rig, else the group empty, else the
    first part); deleting it loses unexported edits. The donor's stats `.lua` is not copied under a new name, so a
    renamed copy gets its class's default numbers. The blueprint's picture (icon) is neither shown nor edited, and its
    timestamp is never changed. A new blueprint has no motion tree and no events. The class choices are the original
    game's, not filtered by family.
18. **The add-on's preferences: two are read, seven not yet.** *Fixed on 2026-11-18* (commits `400b62f`, `b9cadb4`,
    `30b412d`, in 0.4.0): **Game folder** (`prefs.resolve_game_dir`: the setting, then `$IC_INSTALL`, then the usual
    Steam folders) is read by New Project (its model list and files), the model import's texture lookup, the map import,
    the Blueprint panel (the gameplay numbers) and Export Entity Folder (which refuses to write into it); **Game build**
    decides which class numbers the Blueprint panel counts as known. The other seven (Test game folder, Build folder,
    Mods folder, Damage states, Texture type, Replace game files, Selection box) end their label with "(not used yet)".
    Combine Preview still finds the game through combotest (`installdata.install_root`: `$IC_INSTALL`, then Steam).
19. **Upgrading over an older install kept the old libraries, up to 0.3.0** (measured on the laptop, 2026-11-17):
    installing 0.3.0 over 0.1.0 with `extension install-file` reported "Reinstalled" but kept the old bundled ICParse,
    because every build named its wheels `icparse-0.1.0-...` and `combotest-0.0.1-...` and Blender keeps an installed
    wheel whose `.dist-info` name has not changed; removing the extension and installing the zip again worked.
    *Fixed on 2026-11-17* (commit `51eb26a`, released as 0.3.1): `build_extension.relabel_wheel` gives both wheels a
    local version label made from their contents (`icparse-0.1.0+h<12 hex digits>`), and `extension_install_test` now
    installs an older build, installs the new zip over it with the same command and then once more, and checks that
    the new libraries are the ones loaded (pass; on 2026-11-18 also from the real 0.3.0 zip to the 0.3.1 zip, above).
    Not tried on the laptop yet. README.md's *Upgrading* section now says to install over the older version and
    restart Blender, and to uninstall, restart and install again only if the add-on fails to load (gap 20).
20. **The installing session kept the older libraries in memory, up to 0.3.1.** *Fixed on 2026-11-18* (commit
    `78dddbf`, in 0.4.0): `__init__._unload_stale_libraries` unloads ICParse and combotest before the add-on imports
    anything when the copy in memory came from another folder or another version than the installed one.
    `extension_install_test` step 6 installs this zip, a newer build that needs a new library name while it loads, and
    that build again, all in one Blender session; it passes, also from the real 0.3.1 zip. With the unload removed, the
    same run leaves the old libraries in memory and the second install empties site-packages. Not tried through the
    Install from Disk dialog, nor on the laptop's own profile; a change of the bundled `construct` still needs a
    restart (it is not unloaded). The original account follows. When Blender disables the old add-on for an
    install, it clears only the `bl_ext.<repository>.sgm_native_patches` modules from `sys.modules`
    (`_preferences_ensure_disabled` in Blender 5.3's `bl_pkg/bl_extension_ops.py`, read 2026-11-18), so the ICParse
    and combotest the old add-on loaded stay in memory and the re-enabled add-on runs against them until Blender
    restarts. The upgrade fix's agent measured the consequence on 2026-11-17 (not re-run here) with a scratch build
    whose add-on needs one new ICParse name while it loads: the install printed "Reinstalled", then the scratch
    build's error, exited 1, and Blender's wheel sync emptied the shared `site-packages`, leaving the add-on
    installed but not enabled. 0.3.0 → 0.3.1 is not affected (byte-identical libraries); a later release whose add-on
    needs a library change while it loads would be.
21. **Maps can be edited and saved** (`sceneobj/ops_map_export.py`, `panels_map.py`, `tests/so_map_edit_test.py`, 2026-11-18; the import is `ops_map_import.py`, in 0.4.0). Not tried in the game: heights are stored in 0.5 m steps, and a rebuilt or patched pathfinding is made with the game's own method since 0.6.0 (`ICParse.sgb.pathbake`, 91 of 92 stock cell maps byte-identical, the 92nd 52 cells off; decompiled and measured, not game-tested). What a save cannot do: change the terrain's grid (points added or removed are refused); keep scale (ignored, noted); copy a creature combined inside the map (it has no blueprint file: left out, noted); update the `.trg` (it is not in the `.sgb`; the report lists removed ids, and a renamed map's `.trg` keeps the old name in its first comment line); change the lighting, sky, decals, players or title. The pathfinding: the default patches IMPA/PRMP cells where the ground or a blocking placement changed and rebuilds PASM from them; "rebuild" applies the editor's painted layer (TFAC `end_grid`) and blocks every blueprint whose own file says it collides (`simcollides == 1` and no controller); a `BLDI` of (0, 0), which would use the model's bounding box, is not implemented (those blueprints are treated as not blocking, status "unsized"); placements under `art/ebps/locations/` and `art/ebps/nis/` are left open because the stock cells say so (cause unknown); the sector map is still the older `ICParse.sgb.pasm`, not a port of the game's C++ (it matches the stored one on every stock map but takes about 3 s on a 512-cell map and over a minute on the 1025 map); the footprint rule was measured at tile scale 4 only (every stock map with placements). New ground textures are checked for plausibility only, not for existence in the game's data. `ops_map_export.py` reads each edit against the source file, read again at save time, so a source that moved or changed since the import is said in plain words; a very large save is slow (`6p_worldmap`, 1025 x 1025: 66 s for the library's sector rebuild alone, run it detached). Since 0.6.0 (`map_creatures.py`, `map_look.py`): creatures are built from their parts in the bind pose (no animation), a combined creature from the recipe the BNDT chunk holds, read as the limb list FORMATS.md describes (inferred, not seen in the game), and one that cannot be built stays an empty with the reason; the game look's sun strength (3.0) and fog density are the add-on's choices, the water-opacity reading of the EFFC water bytes and the sky dome's shape are inferred, WCLR / OCLR as the water's colours is measured with no decompiled reader, and the water colour exists only inside the map's own water; the water level of a redrawn or added surface is read from the level it was drawn at (`ic_look_level_m`). Map titles (stored as `$42001`-style
    text references) give way to the file name; the game's half-texel inset of the ground textures is left out (under
    one texel). The import raises each 3D view's draw distance to three times the map size (at least 1,000 m). Large
    loose maps cost memory: the 1025 x 1025 `Data/scenarios/mp/6p_worldmap.sgb` on the build machine (44 MB, not a map of the
    archives) imported in 2.3 s with 4,798 instances of 28 models and 1.8 GB at peak.
23. **The cinematic Replay of 0.8.0** (`sceneobj/map_replay.py`, `ops_map_replay.py`, `panels_map_replay.py`, `map_nis.py`; `ICParse.sgb.cine_timeline`,
    `cine_pathanim`, `cine_walk`, `ICParse.sgm.nis_scene`). *Never compared with the game by eye*, and the drawing in a real window was never
    looked at (the Look-through and Event list buttons ran as operators only). Honest about its quality: 29 % of the stock cinematics' events are
    exact, 63 % approximate, 8 % skipped; the approximate ones are mostly timings after a speech (the audio files are in no archive we have:
    14 characters a second plus 0.6 s is a guess), walking routes (the game's sector pathfinder and the spacing of units are not run; a unit
    whose recipe is in the map, or that has no stats script, walks at the 21 km/h of a henchman), random countdowns (the middle of the range), and
    which branch of a script's starting conditions holds (the one that plays an animation is assumed true and the rest of the game's conditions
    never true; keypress conditions never). The replay ends 2 s after the last thing that moves, never past 900 s (some scripts wait for a key).
    The clip bake in `actions.py` plays a clip of K+1 frames over K/fps seconds where the engine plays it over (K+1)/fps (0.9 % to 10 % fast): not
    changed in this release: it is shared by every clip import and export (`encode_actions` reads the frame times back), so a change needs its own
    round-trip check; the cut-scene builder places its clips on the engine's time. One thing in the stock data:
    `nis_whitey_small.sgm` has corrupt skeleton bind matrices (the cut-scene builder uses the meshes' own binds). Skinned parts of `nis_4` (9 parts)
    and Dr. Ganglion in `nis_5` get a vertex-weighted blend of the bone offsets and are flagged approximate.
22. **The Mission Editor tools of 0.7.0** (`sceneobj/map_*.py`, `ops_map_*.py`, `panels_map_*.py`, `ICParse.sgb.{scenario, look_edit,
    impass_edit, terrain_types, water_paint, decals_edit, overlays, ground_picture, script_edit, trigger_edit, dialog_edit, nislet,
    heightmap_io, terrain_io, entities_io, crop, objlist}`). Not tried in the game or in the Mission Editor: that the game lists and
    loads a new map, that it accepts a changed group, text table, `.trg`, `.dlg`, `.na`, passability, terrain type or crop, and that
    the Mission Editor reads our `.ter` and `.ent` are all unproven in-engine (the layouts come from the decompiled program and from the
    game's maps, and every file reads back as written). Known limits: a texture stamp shows only at the highest ground detail; the
    `_decaled` pictures are not regenerated; the sea colour record is added on trust; the camera path preview ignores tension, continuity
    and bias; named choices in triggers are plain numbers; the Script Editor, Orbit Target and Third person view are not there; a group
    cannot take an object added in the same session; a crop builds a new in-memory map and is slow on small maps (9 to 22 s); and the
    one stock map that is not byte-identical untouched (`zombies_hill_fort_spec`, sheared placement matrices) stays so. Findings of the
    builders are in the build folder for the recorder; none was written into `RE_LOG.md` or Ghidra
    by this integration.
24. **The 3D view is a mirror image of the game** (found 2026-11-24; not fixed). The game's coordinates are left-handed (its
    camera has right = up x forward) and the add-on's axes, (x, y, z) -> (x, -z, y), only turn them. So every map, scene model
    and creature is seen left-right mirrored in the 3D view and in renders through an ordinary camera: writing on a model reads
    backwards, and a mesh or layout made in Blender shows mirrored in the game. Imports, saves and exports keep every number, so
    round trips are not affected. Only the game cameras (cut-scene, entity, cinematic preview, replay) are flipped to show the
    game's picture. Undoing it everywhere means mirroring the axis change itself in every import and export (face winding, bone
    and animation transforms, existing .blend files); on stock creatures it would also put the `_l` bones (stored at +x, facing
    +z) on the creature's right, where the game's projection shows them.

What to do next, in order, is [ROADMAP_NATIVE.md](ROADMAP_NATIVE.md).

## 1. What each test proves

`.venv/bin/python3 blender_native/tests/run_all.py --list` is the authoritative list: **77 scripts**, 20 run under
the repository's Python (`PURE` in `run_all.py`, which includes `extension_install_test`: it drives Blender itself)
and 46 in `blender --background --factory-startup --python-exit-code 1 --python <test>`. The sceneobj suites
(`tests/so_*_test.py`, `tests/so_*_pure_test.py`) are found by name through `tests/so_suite.py`. Each script asserts
and exits non-zero on failure, so the exit code is the verdict; a test that prints `SO_SKIP: <reason>` is reported as
SKIP with the reason.

| Test | Proves |
|---|---|
| headless_identity | write(read(x)) == x; untouched edit pipeline byte-identical; one nudge changes only its region; the export builder with the full untouched state is byte-identical over every creature of Assets/stock + Assets/engine_stock (118; the one file ICParse's writer does not round-trip is listed) |
| fastsurf_parity | dirty-patch evaluator == ICParse reference evaluator, engine normals included (weld redone on dirty seams) |
| edittools_test | validate / symmetrize / seam-relax on stock creatures |
| extrude_test | extrude a patch, export the grown topology (the wrapper is the builder) |
| export_builder_test (pure) | pool guard clauses (delete / merge / duplicate / sort / index range / array sizes), skeleton changes (rename / add / delete / re-parent / case; with identity stamps a name swap is two renames, a duplicated bone is added, a stamped rename with a rest edit stays a rename), `skin_owners` (a rigid vertex-group reassignment adopted, unskinned / blended points refused), extrude orientation on every open side of a 2x2 sheet (no 3-user edge, no same-direction seam) and the side picker's refusals, palette-preserving texture edits (1 texel changed, not 34 863; a painted block reaches the lower mip; an exactly reused or lower-mip-only palette entry is never recycled, Squid6 0 of 20 478 untouched lower-mip texels changed), the kept-clip bounds warning, an unknown host bone refused, a grown snail with every record edited at once (rings remapped, shape kept under a rest edit, untouched points keep their bytes, the summary names exactly what changed) |
| ring_test (pure) | 730 base-game + 700 add-on rings are closed edge pairs over their corner loops (== `ring_loop`, every edge a patch side, every base-game ring a valid RingPicker sequence); winding into the torso on all 730 (reversed copies fail) and every New Creature ring; precheck 0 ERROR on the 61; `ring_fix` restores 6236 / 6296 scrambles with the misses pinned to 12 rings and keeps 724 / 726 unscrambled rings; RingPicker on the snail head (events, auto-close, refusals incl. an open chain, a corner outside the torso and a bad fix, reversed + rotated pick back to stock, winding-only without partners); `ring_status` levels (out-of-pool index ERROR); `ring_corners` refuses non-pair ints; the placement rule (at least half a ring's sides on the owner / slot-limb boundary) is silent on all 678 stock rings whose slot limb has patches and WARNs for a wrong socket or an interior torso loop |
| ring_start_test (pure) | Optimise Ring Start without Blender: the rotation search finds and undoes every rotation of a synthetic ring and refuses a stock start as already best; over the 726 base-game limb-socket rings **724 are already optimal** and the two that are not are both the raven's, which a second, independent rule confirms (300 of the 302 Left/Right ring pairs start at mirror corners, the exceptions being exactly those two); a partner ring that would rather weld reversed is *reported*, never reversed |
| combine_offline_test (pure) | combocore without Blender: the in-memory export model combines exactly like `combine_paths` on the written file (both roles), ghost arrays, the edited state (not the source) combines, the Quick tier, a 6-int ring caught by the precheck and by the preview's load check, the delta column and its cache, the donor index cache, Materialize's default path; a reversed cheetah head socket ring: precheck ERRORs 11 + 11a and a twist, `ring_fix` restores it (no ERROR, no twist, combine bit-equal to stock) |
| boner_test (pure) | the stock skinning layout pinned (patch corners first in every bone range, BONE aux_count = their number, unowned points before the ranges: 61/61 base-game, 55/58 add-on stock, off only the 3 template-made ChickenTests); a reskin on all 61 follows it on every bone (world kept, aux_count only on the touched bones); owner -1 (woolly_mammoth's unused points kept first with their bytes; patch points with no bone refused in the fast and grown builders; an unused new unowned point written at its position; open snail + Extrude + reskin on the layout); By Bone colours (stable palette clear of orange, -1 orange, dimming, the sample → control point map on ICParse's grid, per-bone counts); Pick Bone's rule and modal rule; Auto Skin (segments to the nearest child, leaves zero-length; eligibility; targets incl. the Object Mode and select-all refusals; 360 stub and 611 end-site bones own 0 points and get none; 52.1 % agreement with the stock skin); the live vertex-group rules (adopt, unskinned shown unowned, blended kept, edit refusal outside the edit); the empty-host STOCK line; the scaffold on the layout after a 1-point reskin |
| limb_rows_test (pure) | Boner > Limbs rules: every stock creature (118) byte-identical through `structure.apply_limbs` with source rows and with explicit bone lists, no export limb is a parsed-source object; on the snail a rename / class toggle / added stub / deleted patched limb (= the prototype's records) / deleted stub / reordered table / grown export give exactly those LIMB records and a summary naming them by identity; unwritable rows refused; a boneless row warns on export; the rulebook and `check_structure` key on the limb CLASS (renaming every limb and connection changes nothing on 61/61); `ICParse.validate.check_oe_structure` = an independent port of the Object Editor's limb check message for message (E 53/61 clean, T 44/49; the stub message in the OE's exact text), every message kind produced; Boner's checkbox table and exclusive groups, class labels, the 17 standard classes, the name rule and unique default names, the delete and class-change refusals (a connection class never left without a limb; a duplicate keeps it), no boneless standard stub, status "no bones" first; the limb-table migration: an object journaled before the rows is backfilled, Undo into its old generations keeps the names (byte-identical at import), without the backfill they are dropped |
| limb_bones_test (pure) | Boner > Limbs bone lists without Blender: Set Root Bone with a limb's own root changes nothing on 727 of the 728 base-game limbs (the scorpion's Head sweeps in its two unlimbed antlers, and that is reported), the torso remainder is the Torso's list on 60/61, and rows written from those lists are byte-identical |
| conn_rows_test (pure) | Boner > Connections rules: every stock creature (118) byte-identical through `structure.apply_connections` with "the source's" and explicit rows, unchanged rows keep the private copy's record; every connection deleted and re-created from its corner loop at its position byte-identical (word0 / word1 0 / 0); on the cheetah a rename / Parent + Child / host change only that record, Head deleted and re-created from a scrambled pick (ring fixed to stock, default host) = the original records appended, back in place = the source, an empty ring warned, the grown path remaps a created ring, six unwritable rows refused, the summary names each change by identity; the class mask (Mid / Mid 2 slots in `ring_slot` with and without combotest, RingPicker on bolas_spider's four mid-leg rings and a synthetic Mid 2 ant leg, `torso_centre`); `check_oe_structure`'s connection half = an independent port of the Object Editor's check on both corpora (only snowy_owl's antler rings warn), every message kind; the default host on the corpora (720/728 base-game hosts, always a parent-limb bone), names, the re-class plan, NoSwap not a connection box, status words; the connection-table migration with Undo into pre-upgrade generations (without the backfill the classes are dropped); Create's Auto Parent = the file's (1416/1416); the socket warnings (one-sided re-class, duplicate Child class, a stub socket deleted) and Combine Preview refusing such a creature (`picker_refusals`) |
| stub_socket_test (pure) | Add Stub Socket's rules: the 358 stub sockets of the 61 base-game creatures, each taken out and re-planned from its stock patch, never refused: quad = the stock ring's corners 358/358, root = the stock stub bone 348/358 (the other 10, on 5 creatures that name the bone after its limb, are created), host = stock 356/358 (giraffe's top wings), `ring_fix` = the stock ring 354/358 (4 pinned arm-stub starts the partners don't prefer), a side / top wing stub pair shares its patch 108/108; cheetah without its arm stubs (limbs, connections, bones) re-added through `core.build_export` = the stock LIMB / LCON / BONE records; its four wing stubs re-added with created bones = the stock records (bones last, flags word 1, host orientation, patch centre); no clip track for a created bone (kept or re-encoded); the refusals; `structure.apply_stub_bones` errors and warnings, the stub byte never set on a bone owning points; Validate does not call a stub limb naming a missing bone load-blocking (OE WARN line); `ICParse.validate.stub_bone_lines` silent on 111 stock creatures and each line produced |
| bone_rename_test (pure) | bone identity and renames: `resolve_bones` (a stamped rename, a Duplicate whose original keeps its name, a renamed original with a moved / unmoved copy: rest decides / ambiguous, a Symmetrize copy, a stamp-less rig's rename by rest, delete / add / re-parent), `name_conflicts` (A→B then C→A, a swap, a created stub bone; a case-only rename is fine), `bone_name_problems`; `respell` (exact / lower / upper / mixed, tracks lower case first, renaming back exact); every bone of the 118 stock creatures renamed through `core.build_export`: bones, limb lists, hosts, IK chains, tracks carry the new name (tracks naming no bone left alone), world shape, `check_model` and the OE's limb warnings unchanged, clip keys untouched, renamed back from the written file byte-identical (poison_frog skipped: ICParse's writer is not exact on it); IKCH kept as bytes when no chain names the bone, only the Neck chain changes for neck2; the hard-coded name warnings (spine1, tail1, pelvis, to front_arm1_l; none for ear_L or a case change) and the summary line; refusals (case clash, 64 characters, index outside the skeleton); Symmetrize onto an existing counterpart (its stamp overwritten: found by its import name, no change; renamed first: the `overwritten` sentence naming Symmetrize, not delete + add); the humpback whale's orphan `endtail` track: end_tail → endtail adopts it with a warning naming the clips, tail4 → endtail (two `endtail` tracks) refused; the measured tail1 / tail2 wording and the unobserved combiner names' wording |
| bone_add_test (pure) | added bones: `insert_added_bones` on cheetah (horn_l and a tip given out of order: before the first stub bone, parents first, stub parents kept, the key map, owners remapped and an unknown key refused orange, transform = inverse(parent bind) @ rest with a parent rest edit, joint limits / flags / colour rule, skel_info followed and woolly_mammoth's left alone, no stubs → appended, a stub flag before the parent → after the parent with every shifted parent exact, a 7-float sibling layout, 10 refusals); `added_limb_rows` (the parent's limb by identity: kept in place, renamed + a bone by its old name, stored by stamp, dropped, moved with a child, file / stub names never taken, the no-limb hint); the add rule `classify_added_bones` (adopted leaf, stamped chain, unstamped chain refused / accepted with chains, source-stamp copy, root, stub, unknown parent, refused stub, copies of an added bone, renamed holder, stale stamp, a copy of an added chain named as a copy); `core.build_export`: the engine probe's creature rebuilt, horn_l with 8 head points (3 corners) world 1e-9 / aux 3 / every bone on the corners-first rule / load gate + rulebook unchanged / summary, the STOCK line for a bone owning nothing, a hard-coded name warned, a clash with a renamed bone refused, renamed + added together, a grown cheetah; kept clips byte-identical, a missing track held bind-local, a re-encoded clip bind-local under its parent; a leaf under the head of 61/61 base-game creatures owning 5 of its points (2 corners: aux rule, world, parent's limb, gate and rulebook unchanged); the export's stub bones (`export_stub_bones`: a new patchless limb's point-less bone; the fast and grown paths insert at the same place and both refuse a bone under it); the last non-stub leaf of 61 creatures removed and added back = the stock record (whole file once the arbitrary flags / colour words and zero signs are stock); limb identity through `core.build_export` (renamed + re-added both in the Head, one and two deleted bones → the stock bytes with `limbs: dropped`, a chain's middle bone deleted, re-parented into the Tail / the Torso with the stray warning and STOCK line); `added_bone_renames` (the stamp names stale F-curve paths key renamed added bones by; left out when a bone or an import name has it, two renamed bones share it, or for a copy); `bone_name_problems` says each case-clashing pair once |
| bone_topology_test (pure) | deleting and re-parenting source bones: cheetah's endear_l deleted (record gone, 53 records word-identical, parents remapped, limb list + LEar chain without it, no clip rebuilt, pool bytes kept), ear_L deleted (50 points on head with every world kept, endear_l under head, 15 clips rebuilt with the ear_l track dropped and every other track byte-identical, kept bones' motion unchanged); nose under jaw1 (re-sorted after jaw1, its 13 clips re-tracked: nose plays jaw1(t) @ B_new @ inv(B_old) @ L_old(t) within 3.7e-7 at the keys, Head chain completed), neck4 under neck3 with head under it (record right before head, no source record moved, binds and every clip's motion unchanged 1.6e-8, Neck chain completed, 18 clips re-tracked), head under front_ankle_r re-sorted (stubs last) and under tail4 refused as a split Head limb; 12 refusals; IK path rule cases, 905 / 905 stock chains are paths, IK rows (source rows byte-identical; rename / words / add / delete; unknown bone refused); joint limits (float32-equal keep bytes, 8 bytes change, wrong length refused, an added bone's written); a point-less rule-free leaf deleted on 61 / 61 base-game creatures (reads back, points and motion unchanged, tracks dropped with the others byte-identical, load gate and limb lines unchanged) |
| fit_mesh_test (pure) | Fit Patches to Mesh's bpy-free pipeline on a synthetic quad cage: a 96-quad cube → ACC patches fitted to a known bicubic target lands at rms 0.058 % of the diagonal (45× better than the start), X-symmetry is kept, and the import contract is pinned — a target is judged on a positionally welded copy, so UV / normal splits are not floaters and boundary edges are not non-manifold |
| extension_install_test | the distributable extension: `tools/build_extension.py` builds the `.zip` (the add-on files with `sceneobj/`, plus 7 wheels: construct 2.10.70, ICParse, combotest and lupa 2.8 for Linux x86_64 / aarch64, macOS arm64 and Windows x64), `blender --command extension install-file` installs it into a throw-away `BLENDER_USER_RESOURCES`, and a second Blender with no `$BZ_*`, no `PYTHONPATH`, cwd `/tmp` and no repo path on `sys.path` enables it: every dependency imports from the installed wheels (`PATHS_STATUS` all `importable`), every module of the zip's `sceneobj/` loads with its classes registered, no errors and live preferences, and a stock cheetah imports, Validates (0 blocking) and exports byte-identical; `extension remove` then takes the add-on and its wheels away; and in a second throw-away profile an older build (ICParse and combotest named as every build before the content labels) is upgraded by this zip with the same command and no remove, then reinstalled: each time the add-on is enabled, `ICParse.ebp.scene` and `ICParse.sgm.creature.layout` import from the new wheels and `BZ_PT_blueprint` is registered; step 6, in ONE Blender with `package_install_files`: the older build enabled, then this zip, a newer build made by the test whose add-on imports a new ICParse and combotest name while it loads, and that build again: after each install the add-on is enabled, the libraries' versions are the ones just installed, they are reloaded after an upgrade and kept on a same-build reinstall, and no module of the add-on holds an object of the libraries loaded before |
| patchrules_test (pure) | the Max Editable Patch rules: the Auto Interior rule measured on the base game (`interior = row handle + column handle − corner`, obeyed by 99.977 % of 17,048 patches), the carry rules (a corner drags its handles, Coplanar / Lock Handles turn the siblings) and manual-interior detection |
| so_model_pure_test (pure) | the sceneobj package without Blender: it imports with `bpy` blocked and loads only modules that declare `PURE = True`; `Issue` / `issue()` give ICParse `Finding`s; `SourceBundle` / `OutBundle` / `ExportReport` (sha1 stamps, a non-UTF-8 `.lua` survives, `shared` accepts only declared keys, lower-case archive paths); the Slice protocol and registry problems named one by one; `refs.rename`; `claim_edit` and guard codes; `foreach_get` / `foreach_set` dtypes; `pick_game_dir`; the test helpers of `tests/_so_common.py` (temp folders, the Blender launch lock, the install guard, `FakeLayout`) and `so_suite` |
| blender_smoke | register → import → export identity → edit → bake |
| ebp_import_test | a proxy `.ebp` opens its sibling `.sgm`, an embedding one its `FORM BOBJ`; untouched exports byte-identical |
| scene_import_test | structures / the henchmen character / props import as meshes with stored normals, UVs, resolved textures, skeleton + skin, clips bake; stored normals Blender refuses are counted and reported (a real error raised) |
| blender_extrude_test | a closed creature refuses Extrude; every edit at once on the ungrown snail; the grown-export repro on an open creature (once: edits dropped): paint + Action rename + connection bone + ring + rest pose + Extrude all land in the grown export, the report names each, the rest-pose / kept-clip warning is there (also as Validate's `WARN export:` line), Validate's blocking set == check_model of the file |
| convert_test | a standard mesh → bicubic-patch creature via ICParse ACC, watertight + loadable |
| anim_encode_test | pose → SGM tracks → decode reproduces the pose (encode is decode's inverse) |
| rig_anim_test | baked animation on the armature == ICParse animated world vertices (6e-7) |
| anim_actions_test | every clip → a named Action (pose == ICParse at a clip frame); untouched export byte-identical; an edited key, a renamed, a deleted and a new Action export as re-encoded / renamed / dropped / appended clips while the other clips keep their bytes |
| scene_anim_export_test | clips of scene objects (lab structure, henchmen character) as Actions, through Export Scene Object: with the geometry untouched only the clip forms are rewritten; untouched export byte-identical, edits round-trip, other clips and all non-animation chunks verbatim |
| skeleton_test | edit a rest bone: bind transform written, creature shape preserved (8e-8) |
| skin_test | reassign control points to another bone: contiguous ranges rebuilt, shape preserved |
| limb_auth_test | assign patches to limbs, set connection bone, byte-identical when unedited; rings stored as corner loops; the snail head re-picked reversed + rotated (Set Connection Ring) and a New Creature front-leg ring re-picked scrambled both export byte-identical (`/tmp/bz_ring_repick.sgm`); unordered / short / non-side picks refused; the Pick Ring modal's event glue, the ring status cache, the panel rows and the overlay colours (headless stand-ins); Pick Ring started from the sidebar (the UI region) picks in the viewport's WINDOW region: corners projected there, clicks at window coordinates restore the stock head ring, a click over the sidebar passes through; a pre-v2 ring store migrates atomically on Set Connection Bone; a verbatim ring re-picked exports closed edge pairs, an untouched verbatim ring survives another connection's edit; the lion's R Front Leg ring set as its L Front Leg ring is WARN in the ring status and in Validate (Combine Checks Off); a ring cut by one corner gets a COMBINE ERROR |
| texture_test | unpainted textures stay verbatim; a repainted one is re-encoded to TXMP |
| uv_test | per-patch corner UVs edited in the UV editor round-trip; untouched patches verbatim |
| new_creature_test | New Creature: canonical skeleton, connected scaffold, stock rings, clips as Actions; rulebook clean, untouched export byte-identical; Validate runs the Quick combine tier (13 combines, 0 COMBINE ERROR) and, with Combine Checks Off, the precheck alone |
| new_project_test | File > New > Impossible Creatures Project: the menu entry; the henchmen and lab donors are found in the archives; a creature project has the 48-bone skeleton and its 9 clips (or none, and then exports with no clips); clips borrowed from a stock creature become new Actions under the donor's names, matched by bone name, rotations equal to the donor's within 1e-5, written with the donor's names and frame counts (all, or only the named ones); a gatherer project is a scene object with the donor's `.ebp` beside it, its skeleton and clips, and an untouched export byte-identical (with None: no clips, the export drops them); a structure project exports its geometry; refused projects (wrong kind of file and the other refusals) leave no file behind |
| scene_retopo_test | gatherers and structures with changed topology: New Project -> subdivide / delete faces / flat shading / UV seam / weights / material slots -> Export Scene Object; only the edited parts are rebuilt (21 of 23 structure parts byte-identical), skeleton + clips untouched, the export is re-importable, geometry and clips leave in one file; a rebuilt part's bounding volumes are the game's (exactly 1 + palette, each bone's box holding exactly its vertices, the entity box following); a part of 37,824 vertex entries is refused by both exporters with nothing written; the Lab's edited tent (11 damage states) is refused, then written without them with the drop option (File > Export and Export Entity Folder; the other parts and the clips byte-identical), and a tent left alone keeps them; the File > Export label |
| scene_geometry_export_test | a structure's vertices moved in Blender (the henchmen lab) exported through both writer layouts (v1.2, the engine rebuilds the detail levels; v2.0 with one full-detail record): the positions decode back to what Blender held (float32), UVs / triangles / materials / vertex count unchanged, every chunk other than the edited part's mesh byte-identical; an unedited export byte-identical |
| blueprint_poc_test | the Blueprint panel: registration and unregistration (also as a sceneobj module); the helpers (names, folders, the value diff with offsets compared as 32-bit floats, the class table in plain words); henchman, lab and aviary projects: the values load from the donor's blueprint and the donor's folder is found, an untouched Export Entity Folder is byte-identical for the `.sgm` and the `.ebp` (383,819 / 20,224 B; 366,534 / 19,348 B; 194,131 / 23,024 B) and writes only `Data/art/ebps/<folder>/<name>.{sgm,ebp}` and `README.txt`, each of name, class, footprint, offset and sounds edited alone changes only its own chunk, the `.sgm` equals what `bz.export_scene` writes after a vertex edit; Reset, a file changed on disk, Load refusing to replace edits; a model with no `.ebp`; the read-only states; refusals (the game folder, Steam paths, bad names and sounds) writing nothing; every panel state drawn into a layout that rejects unknown properties, operators and icons; all **479** stock blueprints of the five archives: 383 editable, 10 old layout, 86 with the model inside (refused), 393 untouched byte-identical, the 383 editable ones edited in all five fields change only the header, footprint and sound chunks; the game install unchanged |
| combine_preview_test | the panel in headless Blender: preview, stale on every input (control point, part button, stock creature, painted skin, a vertex-group reassignment, a broken pool, which also disables Materialize), refresh with TopoCache reuse, auto refresh through the depsgraph handler, swap, NO_SOCKET wording, interpreter parity with the repo venv (scales 1e-6, vertices 1e-5 m), Validate Combines, Materialize (refused into the stock folder) and Clear Materialized back to baseline, file load clears the ghost |
| guard_test | native Delete / Merge / Sort / Duplicate / new vertex → Export CANCELLED, Validate EXPORT BLOCKED, overlay flagged, 11 operators disabled, Undo → byte-identical; a generation the journal lacks, a removed id attribute, short UV / ring / encoding / name tables and an out-of-pool ring index refused, Apply UVs disabled, the journal follows Undo Steps; a bone rename exports (11 clips kept, Validate's WARN for a looked-up name) and add / re-parent / delete are refused, restored → identical; two sibling bones swapping names refused as a rename chain; a legacy rig says so; Set Connection Bone offers the skeleton's names and writes a renamed host under its new name; an unknown host bone refused; a Ctrl+G reassignment exported (and warned), unskinned / blended points refused; a deleted rig named as the reason; edit-mode Extrude / Assign Limb / Set Connection Bone / Set Connection Ring with 6 Undo + 6 Redo → each step exports exactly its state; SGM operator in Edit Mode, Tab, move, then Undo / Redo across the Tab → every step a real state (never blocked); a migrating ring edit on a pre-v2 store undone → raw ints without the version, redone → v2; stale UV cage refused; two imports and delete + re-import export identical; NOISE modifier re-encodes; renamed rig / image exported, deleted image refused; a saved .blend reopens byte-identical and keeps a painted texel; transform / shape keys / modifier refused; changed source refused; Symmetrize keeps edit-mode moves; a pre-guard object migrates; an import set-up failure is recorded and warned |
| skin_boner_test | Boner's skinning loop in Blender: the Bones sub-mode and its three tabs, By Bone colouring, Pick Bone, Assign / Select / Remove / Select Unassigned / Auto Skin, a no-op Assign that stays byte-identical, and the unassigned-point export refusal (→ four exports for the engine tier, one of them the deliberately open extruded snail) |
| boner_limbs_test | Boner > Limbs in Blender: New Creature list (13 rows, labels, status), Rename refusals (64 characters, non-ASCII, duplicate ignoring case) and a Head rename exporting only its name (Undo identical, Redo renamed); LFArm Right / untick Arm take its connection along and back is identical, untick Torso / Tail refused, NoSwap export and back identical; Add Standard offers the four mid legs and refuses each (no root bone); a boneless LMLeg (written through the store) with torso patches: status, panel alert, `WARN export`, `WARN oe` and one `COMBINE ERROR precheck` line, not blocking (`/tmp/bz_boner_b1_boneless.sgm`), Delete to the Torso identical; Delete of LFArm / Torso refused; cheetah Head renamed (`/tmp/bz_boner_b1_rename.sgm`, precheck and rulebook unchanged), LTWing as Side with its connection and back identical, untick Tail / Torso, Delete LFLeg and Add Standard RMLeg refused; a cheetah without arms: Add Standard LFArm (root front_arm1_l), boxes exclusive per group, a taken root refused, duplicate / not allowed status and the `WARN oe:` duplicate line, torso patches turn the front_arms part button on (rows, not source records), Delete to the Torso / to Head identical, a taken default name gets `_2`, stub arms equal to cheetah's own records (`/tmp/bz_boner_b1_arms.sgm`); Undo into generations journaled before the limb rows keeps the limb names |
| boner_limb_bones_test | limb bone lists in Blender: Select Limb Bones / Set Root Bone / Add / Remove Active Bone on a stock cheetah and a New Creature — journaled, undoable, written through ICParse and checked by Validate; the re-rooted front legs still graft as a donor and a leg bone moved into the Torso is refused by the real ComboTest.exe (COMBINE_MUST_FAIL) |
| boner_connections_test | Boner > Connections in Blender: cheetah rows = source, the tab and the active-ring overlay; Rename refusals and a Head rename (Undo identical, Redo); Child / Parent boxes and back identical, NoSwap refused; host Set / refusals / Default / from the field; LTWing re-classed Side with its connection in one generation (`/tmp/bz_boner_b3_reclass.sgm`), Undo / Redo both; Tail untick refused; Head connection deleted (precheck ERROR), created (host neck1, no ring) and its ring set from a scrambled selection = stock, Undo ring / Create / Delete step by step; New Creature: Head connection deleted (pinned precheck ERROR) and re-created (`/tmp/bz_boner_b3_head.sgm`, Quick tier 13 combines, 0 errors), LFArm connection + limb deleted and re-created with its stub ring picked (`/tmp/bz_boner_b3_arm.sgm`, the original records); the socket warnings + precheck ERRORs (duplicate Child class, LFLeg alone Mid `/tmp/bz_boner_b3_onesided.sgm`, both Mid clean, a renamed connection left behind, the LFArm stub socket deleted `/tmp/bz_boner_b3_noarm.sgm`); horse antler connection created from the menu path (Auto = Head, Torso refused with the hint), ring = the original (`/tmp/bz_boner_b3_antler.sgm`) |
| boner_ring_start_test | Optimise Ring Start in Blender: the operator and its Active Ring / All Rings buttons, the refusal on an already-optimal ring (so an untouched creature stays byte-identical), Undo, and All Rings as one undo step; the cheetah with its L Front Leg ring started 2 corners out is rotated back to **byte-identical to stock** (→ `/tmp/bz_ring_start_bad.sgm`, `/tmp/bz_ring_start_fixed.sgm`; both must LOAD, and the fixed one is a COMBINE_CHECKS row) |
| boner_stub_test | Add Stub Socket in Blender: Edit Mode only; cheetah without its arm stubs: refusals (no patch, two, a head patch, the wing stubs' patch), LFArm / RFArm on the existing bones in one generation each (`/tmp/bz_boner_b4_arms.sgm` = the stock records, Quick tier clean), Undo / Redo identical; cheetah without its wing and arm stubs (bones too, clips as Actions): six sockets create stamped leaf bones (top wings sharing), Undo x6 takes the bones off the rig one at a time and Redo x6 brings them back (each step's export identical), the skeleton guard refuses a renamed / parented-under / re-parented stub bone and a plain added bone, `/tmp/bz_boner_b4_created.sgm` = the stock LIMB / LCON records with the created bones last, kept clips byte-identical, Validate clean (no OE stub-class warning, Quick tier), a keyed stub bone's clip re-encoded without its track, a deleted socket leaves its bone unexported (warned) and a new Add Stub Socket reuses it; edges: a viewport-disabled rig / excluded collection refused in words with the creature kept in Edit Mode (eye-hidden and collection-disabled rigs work), a failing mode switch restores everything, Add Active Bone / Set Host name a created bone as such, a bone with points in a stub limb keeps its flags, a created bone deleted while its limb lists it: export warns, Validate not blocking (OE WARN + COMBINE ERROR) |
| boner_rename_test | Rename Bone and Blender's own rename in Blender: refusals (case clash, 64 characters, non-ASCII, empty, space, unknown bone, rename chain; a case change of the bone's own import name allowed); a stamp-less rig refuses Blender's rename and Rename Bone stamps then renames; cheetah (20 clips as Actions, Walk on no NLA strip) ear_L → ear_left: rig bone, vertex group, every Action's F-curves; `/tmp/bz_boner_b6_rename.sgm` = skeleton + Head limb + LEar IK chain + 15 tracks renamed, 20 clips kept (renamed back = the stock clip bytes), Validate Quick clean; Undo / Redo in Object and in the creature's Edit Mode exact; Walk's key edited → 19 kept, 1 written under the new name; Blender's rename leaves Walk's paths stale and exports the SAME bytes (kept and edited; the paths put back after sampling); Duplicate (unmoved copy ambiguous, moved copy added), Symmetrize (added), a rename chain and a case-only clash refused (Validate EXPORT BLOCKED); eagle Rear_endleg_L → rear_heel_l (Rename Bone: LRLeg limb + IK) and feather2_L → quill2_L (Blender's rename: LSWing limb) → `/tmp/bz_boner_b6_eagle.sgm`, 13 clips kept; spine1 → back1 warned by the operator, the Bones tab read-out and Validate; both exports re-imported and renamed back byte-identical to stock; F: Symmetrize of a rest-edited antler_l onto the existing antler_r → `rest pose: 2 bones` (as before renames existed) and Rename Bone restamps antler_r before renaming it; both antlers renamed then Symmetrize → refused naming Symmetrize (Validate EXPORT BLOCKED), renamed back → exports; a Rename Bone whose Action path fix would clash is refused before the rig bone or vertex group changes; tail1 → tail_a (`/tmp/bz_boner_b6_tail1.sgm`): the measured WARN, and the Quick tier's torn-seam COMBINE ERRORs in both roles |
| boner_add_bone_test | Add Bone in Blender: registration and the Bones tab button; cheetah (20 clips as Actions): Add Bone under Active horn_l (stamped deforming leaf, table entry in one generation, Head limb; stub parent / name clash / 70 characters / unknown parent refused), 8 head points skinned with Assign Selected → `/tmp/bz_boner_b7_horn.sgm` (bone 48 under head, 8 points, aux 3, world within 1e-5 of Blender's, the rest equal to the rig's, 20 clips kept byte-identical), Validate clean with the Quick tier, the export re-imported owns the points and exports byte-identical; Blender's extrude under endtail adopted at export (Tail limb, STOCK line), Adopt Added Bones, an Action keying horn_l re-encoded with a track playing like Blender and 19 kept → `/tmp/bz_boner_b7_extrude.sgm`; a root bone, an unstamped chain (then adopted), a moved Duplicate of ear_r, a bone under top_wing_r refused, a Duplicate of horn_l adopted and both renamed refused; Undo: Add Bone from the rig's Edit Mode + Ctrl+Z identical and Redo back, Blender's own add + Ctrl+Z in the rig's Edit Mode identical, Add Bone in the creature's Edit Mode + Ctrl+Z / Redo, the bone deleted under skinned points refused naming it (Select Unassigned selects them) and a vanished bone's limb entry dropped; limb identity (F): renamed (Rename Bone / Outliner / extrude) + a second bone by the old name both in the Head, Add Bone's report names the parent's limb after a delete, Add Bone + delete (plain, renamed, from the creature's Edit Mode) byte-identical with `limbs: dropped`, an adopted chain's middle bone deleted, re-parented under tail4 (Tail, moved) and spine2 (Torso, stray warning + Validate STOCK line), Limbs Add / Remove Active Bone on an added bone; clips (G): Melee1 not keying horn_l has no horn track and the same bytes after O_growl (keying it) was scrubbed and after a hand pose, the pose kept; a new Action keying only head identical with tail4 hand-posed; horn_l keyed in O_growl (on no NLA strip) and renamed with Blender's rename keeps its track (plays like Blender), Rename Bone then rewrites the stale path, keys naming no rig bone warn; UI and messages (H): the rig box alert gone after Adopt and back after Ctrl+Z, Add Bone with the journal sync broken or its begin refused leaves no bone, Adopt with an unreadable source refuses in words, a failing added-bone key check said at the active bone / Bones tab / Assign Selected, a bone under top_wing_r refused in the Bones tab / rig box / Assign Selected, a case clash said once and refused by Adopt, no delete / re-parent tail on a root bone's refusal (kept for a deleted bone), a deleted renamed added bone named `'horn_x' (made as 'horn_l')`, the disabled-rig refusal, the Limbs table showing a re-parented / deleted horn_l where Export puts it; stub bones, Auto Skin, Actions (I): a new patchless limb naming endtail (Add Bone under it refused, a Blender leaf under it refused by Export's guard, skinned endtail exports it), Auto Skin Reset All gives points to an added bone (not to an added end site, a non-deforming or an unadopted bone), a new Action held only by its fake user named by Export's warnings and exported once on the NLA; copies, stub set, Select Bones (J): Shift+D of an added chain refused as a copy (Adopt, then both export), Shift+D of a skinned horn_l with the original deleted refused naming vertex group 'horn_l' at Export and in a skin edit (renamed back: exports with 8 points), a new patchless limb naming endtail in the panel read-outs (no owner key under it, the active bone, the Bones tab and rig box refusals), Add Bone's warning and Adopt (the leaf under endtail not stamped), Limbs > Select Bones selecting the placed lists |
| boner_bone_topology_test | deleting and re-parenting bones in Blender: ear_L deleted with X → no rig alert, `/tmp/bz_boner_c8_delete.sgm` (53 bones, endear_l under head, 50 points on head, off the Head list and LEar chain, 15 kept clips rebuilt, summary / warnings / one plan warning, Validate clean, an edited Action re-encoded without ear_L, Undo byte-identical); pelvis / tail1 / front_arm1_l / neck1 refused in the rig box and at Export with the reasons, each Undo byte-identical; Add Bone neck4 + head Ctrl+P under it → `/tmp/bz_boner_c8_neck4.sgm` (record right before head, no source record moved, Neck chain completed, 18 clips re-tracked, Blender's O_growl playback of the head = the exported clip at its keys 6.1e-6), head under tail4 refused (split limb), re-parented back → the same file, Undo to the import byte-identical; nose under jaw1 → `/tmp/bz_boner_c8_nose.sgm` (Blender's playback at the file's 6 keys 3.6e-5; an Action edited after the re-parent plays the same), Undo byte-identical; joint limits (E): 54/54 bones stamped with their record's limits (export byte-identical), head's min/max x edited → 8 bytes change with the summary line, the Bones tab shows stock + the editable copy, Reset to Stock / Undo byte-identical, 7 values refused, an added bone's limits written; IK chains (F): the 15 source rows listed, Add IK Chain Horn (head > nose) exported + validated, not-a-path / taken name refused, Edit (NeckChain 2/0) and Remove (LEar) give exactly that IKCH, the stored table follows a deleted ear_r / nose under jaw1 (Head + Horn completed) / tail4 renamed, a hand-made non-path row refused at Export and blocking in Validate, Reset + Undo byte-identical |
| sculpt_refit_test | Sculpt Surface / Refit Patches: the bake is one connected mesh (seam samples merged) that remembers what it is a sculpt of; Export and Validate warn while a sculpt is open; after a Grab-like symmetric displacement, Refit changes only the patches with moved samples (and their neighbours through shared points) and every other patch's control points are **bit-identical** (→ `/tmp/bz_sculpt_cheetah.sgm`) |
| blender_fit_mesh_test | Fit Patches to Mesh in Blender: Suzanne's skin (eye shells removed, 2 Catmull-Clark levels) fitted with a QuadriFlow layout at budget 300 → a native creature with the SGM tables, rms below 0.3 % of the diagonal, symmetry kept, the error heat map on an untouched target; and a quad sphere fitted with its own quads (→ `/tmp/bz_fit_suzanne.sgm`, `/tmp/bz_fit_sphere.sgm`) |
| retarget_test | Retarget Creature to Mesh: retargeting the cheetah to its own bake moves not one control point and exports byte-identical (identity); to a bulged, 10 %-longer-legged bake it keeps every limb / connection / skeleton / skinning table, pins the ring control points exactly and moves the bones into the new skin (→ `/tmp/bz_retarget_cheetah.sgm`, and a COMBINE_CHECKS row: it still takes a stock head) |
| landmark_retarget_test | Landmark-guided retarget: with every Empty left where the creature put it the TPS is the identity and the export is byte-identical; with the legs 25 % apart and the head 10 cm up the warped-then-fitted surface is within 2 mm rms of the target and the leg and head bones land in the moved limbs (→ `/tmp/bz_landmark_cheetah.sgm`) |
| patch_feel_test | the patch rules in Blender: the interior points are hidden and unselectable and an untouched creature still exports byte-identical with the rules on; a corner moved 5 cm carries its handles by the same delta and puts its patches' interiors back on the rule; a hand-moved interior flags its patch manual and the rule leaves it (→ `/tmp/bz_patch_feel_cheetah.sgm`, engine tier) |
| patch_select_test | patch selection in Blender: one never-shaded corner quad per patch (288 on cheetah, wound with the engine surface normal; two patches over the same corners give two faces on hornbill), face select feeding Assign to Limb / Select Limb, Hide / Unhide driving the overlay mask, Delete Patch through the SGM tables with ring patches refused (→ `/tmp/bz_patch_delete_cheetah.sgm`, which the editor must reject as open: ENGINE_EXPECT FATAL), Corner / Coplanar vertex types |
| topology_ops_test | the topology tools in Blender: Fill Hole closes a 2-patch hole watertight; Subdivide propagates along the strips with no T-junction and an unchanged surface (288 → 314 patches); Weld, Add Quad, Attach (two shells in one creature) and Mirror to Whole; every one is a single Undo back to byte-identical, and five exports go to the engine tier (`bz_topo_fill / subdiv / weld / attach / mirror / region_cheetah.sgm`) |
| texture_transfer_test | Transfer Texture (Cycles CPU bake): a checker quad sphere fitted with Own Quads gets the per-patch atlas UVs and one atlas image on every slot, coverage above 95 %, and the atlas colour equals the source colour at the surface points; the retargeted pillbug wears its scan's skin (→ `/tmp/bz_transfer_sphere.sgm`, `/tmp/bz_pillbug_textured.sgm`) |
| trim_stray_test | Trim Stray Surface: against the cheetah's own bake with the outer half of the tail cut away, Retarget moves nothing and reports rms 0.00 % — a closest-point fit only measures target → surface — while Trim finds exactly the 14 tail patches the target does not have; Delete + Fill Hole leaves it watertight and Collapse folds them onto the target (→ `/tmp/bz_trim_delete_cheetah.sgm`, `/tmp/bz_trim_collapse_cheetah.sgm`) |
| fit_to_rig_test | a FITTED creature is riggable at once (2026-09-16): with the fit's limb / skin split off, every patch is in limb 0, every point on bone 0 and 43 of 48 bones count as stub bones, so Add Bone under `head` is refused -- with it on, exactly the 6 flagged socket bones are stubs, 31 bones own points, all 7 real limbs have patches, and Add Bone / a bone delete with X / Set Root Bone all work with no save and no re-import; the stub set follows the LIVE limb table both ways (emptying a limb and filling it again); a bone added in Blender roots a standard limb (`mid_leg1_l` under `spine1` -> an LMLeg limb that keeps it, pinned, through the export and the re-imported file) and Add / Remove Active Bone reach added bones; Export over the creature's own base file re-bases the object (the next export is byte-identical) and is refused by name while a skeleton edit is pending. Its export LOADED in the Object Editor |
| action_guard_test | the export's refusals at the moment of the edit: each mistake made in headless Blender puts its alert up **before any export** and with the export's own words -- an object transform, shape keys, a Subdivision Surface modifier, the Armature modifier switched to bone envelopes (`state`), a control point deleted in Edit Mode (`pool`), the base .sgm changed on disk (`source`), a UV cage with the wrong face count (`uvcage`), a blended vertex weight (`skin`, which only the deep pass and its clock can see) and a refused bone delete (`skeleton`, which the panel leaves to the rig box and the viewport text still shows); each is taken back and the issue goes; a bone's stranded vertex group raises no skin false alarm; a clean cheetah fires nothing (shallow, deep and viewport text) and exports byte-identical before and after; cost: 15 us per update for the state key, 20 us for a cache hit, an Edit-Mode scrub 211 us/step with the handler vs 123 us without |
| gn_surface_test | the surface as real evaluated geometry (the optional Evaluated Surface): the node group is built by script in a fresh file (168 nodes / 301 links, no warnings) and the scene toggle puts every creature on it and takes it (and the face tables) off again; the evaluated mesh equals `core.tessellate` at each point's own (patch, u, v) within 3.4e-7 m on cheetah, 1.2e-7 on lobster and 9.6e-7 on elephant at density 5 (3.7e-7 / 1.4e-7 / 1.4e-6 at 9) -- float32, the pool's own precision -- and elephant's 2 patches with a repeated corner have no corner quad to carry their row and are reported, not dropped silently; one control point moved moves exactly the surface points of the patches that use it, in Object Mode, live in Edit Mode (read through `new_from_object`: in Edit Mode `evaluated_get().data` is the edit cage's datablock) and in Sculpt Mode; it survives Undo, save / reopen and Delete Patch (the depsgraph handler rewrites the tables, so the surface is the NEW table's) with the export BYTE-IDENTICAL throughout; the evaluated mesh carries the object's 9 material slots in order with 0 material mismatches over 4,608 faces and UVs equal to `core.surface_uvs` to 8.2e-8; and the honest limits are pinned -- a hidden patch keeps its 25 evaluated points (Hide / By Bone / engine normals stay the overlay's) and a mesh that is no longer the pool is refused in words |
| so_map_import_test | File > Import > Impossible Creatures Map (.sgb) and Map from the Game: four stock maps of three sizes (2p_faceoff and mission1 of Engine.sga, 6p_big_bog of InsectData.sga, 6p_bikini_atoll): terrain at the map's size and heights, one material per ground texture, the water over the wet cells, every placed entity an instance of its model or an empty with a reason, the kind collections, the kept properties and the report |
| so_map_creatures_test | creatures on an imported map as real models (`map_creatures.py`): mission1's 21 creatures (4 stock kinds and 4 combined recipes read back from BNDT: parent animals and limb sources, each combined one differs in shape, each stands on its origin), placement matrices and the save properties intact, a save with no edits byte-identical, a moved stock and a moved combined creature, one deleted, the other 335 entities unchanged; with no game data, or a recipe naming a missing animal, the creatures stay empties with the reason; mission9, the Zombie hill fort and ice clash with per-creature and per-map time limits |
| so_map_look_test | the game look (`map_look.py`) on 2p_faceoff, ice clash and mission14: the sun's direction and colour, the ambient, fog only where the map has it, the sky picture, the sea's exact extent and opacity range, every decal in its place facing up, the water colours equal to the converted WCLR values; a save with the look on is byte-identical; Redraw water (cell counts against an independent numpy calculation, the level the save reads unchanged, +2 m writes +2 m, and on 6p_big_bog with its ponds a double redraw and a 1 m move read right); Add water on a map opened without water, and on a dry map; Remove game look; the automatic redraw and its switch |
| so_map_edit_test | editing and saving maps (`ops_map_export.py`, `panels_map.py` over `ICParse.sgb.edit`): four stock maps (2p_faceoff, mission1, 6p_big_bog, 6p_bikini_atoll) saved with no edits are byte-identical and their sibling files match the archive; one scenario with every edit (a block of heights raised, one that rounds, one that is clamped, water +1 m, two blocks painted and a tie left alone; an object moved and turned, deleted, copied with Shift+D and with Alt+D, added, given an owner, dropped to the ground) saved and read back with `view.read`: everything else equal entity by entity, and a fresh plan on the saved file finds no changes; only the edited chunks change; refusals (a vertex removed from the terrain, the game folder, a steamapps path, the original file) write nothing; Edit Mode edits, the ready-to-drop folder with a renamed map, the panel drawn into a layout stand-in that rejects unknown operators, arguments and icons, the File > Export entry, scale, a moved terrain, a moved combined creature; the game install is unchanged |
| so_contract_test | the sceneobj contract every later module is held to: the fixed names, constants and signatures; every registered slice fits the protocol (unique orders, stage numbers, disjoint claims; no slice is registered yet, so this part is empty for now); module hygiene read from the source (no stray module that registers things, PURE modules import no Blender, every `.shared` key and `claim_edit` code declared); the one-time edits of the shared files (minimum Blender 5.3.0, the zip carries `sceneobj/`, `run_all` finds the `so_` suites) |
| so_registry_test | the sceneobj registry, UI kit and preferences in Blender: what is discovered (core modules in order, no import errors, classes defined == classes listed); `blender_native.register()` / `unregister()` leave `bpy.types` clean; throw-away modules: `(ORDER, name)` order, property groups before what points at them, a child panel after its parent, hooks after classes, a broken module reported while the rest load, a failed registration rolled back, an orphaned sub-panel left out and reported, `reload()` picking up edits; `_reload()` twice leaves no stale module; the UI kit and the SGM panel drawn for a scene object, a plain mesh, nothing and a creature; the preferences' defaults and folder choices |

### The combine checks (the offline tiers)

| Tier | Where | What | Cost |
|---|---|---|---|
| Precheck | Validate with **Combine Checks: Off** | `combotest.validate.precheck` alone: ring sizes and formats, limb and connection classes, host and canonical bones, socket caps, attributes, ring winding into the torso | milliseconds |
| Quick (default) | Validate with **Combine Checks: Quick**, **Validate Combines**, `combine_offline_test` | the precheck, then 13 combines with 6 stock donors (as torso and as donor), checked one by one | about 1.5-2 s |
| Full | `run_all.py --combine-full`, `combotest validate X.sgm --donors all` | every stock creature x every part button x both roles (855 designs) | about 20-30 s with workers |
| Engine | `run_all.py --engine` (Proton, on request) | 27 pinned rows through the real ComboTest.exe (combinations that must succeed and match our combiner, `COMBINE_MUST_FAIL` refusals with the exact assertion, `COMBINE_MUST_FLAG` broken seams), plus the 64 exports that must load in the Object Editor, three of them deliberately FATAL | about 60 s a combine row, 6 s a load |

Findings go into Validate's list as `COMBINE ERROR / WARN / INFO` lines. They never block loading; the Quick run's
summary is stored on the object (`bz_combine_summary`). `run_all.py --parity` (and `tests/engine_parity.py`) compares
the arrays the overlay draws and the bake renders with the Object Editor's own Direct3D stream (`rdn-compare`):
positions, normals, UVs, triangles, textures and texture binding.

## 2. History, newest first

* **2026-11-24, maps are drawn by the game's rules (0.8.1).** A new read of the game's map drawing (pyIC `decompilation/RE_LOG.md`
  2026-11-24) showed the 0.6.0 game look had several rules wrong, now fixed in `ICParse.sgb` (`view`, `ground_picture`,
  `scenario`, new `game_look`) and `sceneobj/map_look.py`: (1) a decal's **full** size is its picture / 8 m x scale (it was
  drawn twice too large, and stamps were painted twice a decal's size); (2) the ground is `<map>.tga` with the detail textures
  blended over it near the view, not the detail textures alone; (3) the game reads loose pictures **rows as stored**: the
  stock ground pictures are bottom-origin, so the add-on's stamps had been painted **mirrored north to south**; detail, decal
  and sky pictures are now shown the game's way too; (4) `WCLR` / `OCLR` are editor data the game never reads: the water is the
  sky's reflection x a grey from the depth and distance x a ripple picture, added to the scene, on the map and past its edge,
  over the sloped border; (5) the sky is the game's flattened dome, following the scene camera at 0.9. Found by the user in
  the first test render and fixed: (6) a decal is built on the ground's own cells, as the game builds it (the coarse grid
  used since 0.6.0 let steep ground cut through the rock-face decals); (7) materials the game never draws
  (`Scene.material_hidden`, `ShaderImp::IsInvisible`: the water sparkles, which showed as pale triangles) are see-through on
  a map's models, faint in the Solid view. The save is unchanged and byte for byte with the look on. Not ported: the game's
  per-point ground light and cast shadows, the moving water. Cliffs and water compared by eye with the browser viewer
  (`ICjs`, the same rules): the same layout, darker in Blender. Not compared with the running game.

* **2026-11-24, the game cameras show the game's picture.** The NIS 1 render was the left-right mirror image of the game's (the
  wall map's lettering backwards, Australia left of Africa): see gap 24. The cut-scene / entity camera, the cinematic preview
  camera and the replay camera are now scaled -1 on their X (`ops_map_import.show_game_picture`; a flat render through such a
  camera is the exact left-right flip of the unflipped one in Workbench and EEVEE, back-face culling included). With the picture
  the right way round, a camera key's **roll** had to turn the other way: the engine turns the view by `RotateZ(roll)`, and the
  old sense only looked right in the mirror. A move animation's object keeps its sense (`MoveAnim::Blend`), and arrows saved by
  0.8.0 are read as they were drawn. Tests: the NIS and entity cameras' right axis against up x forward, the roll against the
  engine's matrices, copy view from a flipped camera, a 0.8.0 key's roll, the preview and replay cameras flipped
  (`so_map_nis_test`, `so_map_cinematics_test`, `so_map_replay_test`). The whole
  `run_all.py`: 79 / 79 (the build machine). Not compared with the running game. No release built.
* **2026-11-20, review of the Mission Editor features (branch `review-blender`).** Found by reading and checked by a test each: (1) the
  stamps painted into the ground picture were lost when the `.blend` was saved and reopened: the picture was packed once at creation and
  never again, and the handler that packs the passability picture before a save was not `@persistent`, so Blender dropped it as soon as any
  file was opened (it now packs the ground picture too; `so_map_paint_test` saves and reopens twice); (2) **Build replay** used the trigger and
  cinematic files read at the last **Find**, so later edits were not in the replay (it reads them again; `so_map_replay_test`);
  (3) Terrain Crop, Validate and the `.ter` / terrain import read only the core edits of the map, so paint, trigger, cinematic and settings
  edits not yet saved were missing from them (`map_tools.current_map` runs every feature's save step on a context that is never written;
  `so_map_crop_test` compares those bytes with a save's); (4) a file that could not be written during Save Map ended in a Python traceback
  with the map file already on disk (the map file is now written last and an `OSError` is a plain-words `MapSaveError`;
  `so_map_edit_test`). Not changed: the overlays (`map_overlays.current_bytes`) already ran the passability and water steps. Dead
  compatibility code in the replay builder (a retry without `start_frame`, an NLA shift) and a few unused assignments were removed.

* **2026-11-19, night.** Release 0.8.0: the cinematic Replay. Three builders worked in parallel (`ICParse.sgb.cine_timeline`, `cine_pathanim`,
  `cine_walk`: a cinematic as a timed list of scene events from the map's trigger script, with the game's tick, path code and an A* walk;
  `ICParse.sgm.nis_scene` + `sceneobj/map_nis.py`: the cut-scene (NIS) objects with their animation and camera, and the camera for
  UseAnimationCamera; `sceneobj/map_replay.py` + `ops_map_replay.py` + `panels_map_replay.py`: the stand-ins, the replay camera, markers,
  fade plane and event list). The integration found the replay builder's request already wired (the NIS builder's `show_between` is used),
  applied the NIS builder's CMRA comment correction in ICParse, corrected `map_replay`'s heading note (the timeline's headings are radians),
  added `tools/replay_sweep.py` (a replay of every cinematic of every stock map, with the save identity checked shown and cleared), ran
  everything again (table above), and wrote the README, guide, ROADMAP and this file. Not done on purpose: the clip bake's (K+1)/fps timing
  in `actions.py` (see the known gaps). Open: the RE findings in `re_pending_0.8` (the recorder writes them into RE_LOG and Ghidra), and
  looking at a replay next to the game.

* **2026-11-19.** Release 0.7.0: the Mission Editor's tools. Five builders (new map and look; painting; scripting; cinematics;
  files, lists and crop) each wrote their own `map_*` modules against the extension points of `map_hooks.py`; the integration applied
  their requests (the hooks test brackets the registry so features may register steps; the save folder follows the map's kind and the
  save context carries the core edits' report; the crop and terrain-import maps hand their old `.trg`, `.dlg` and `.na` to the
  import steps; the evidence strings of the trigger argument kinds 9, 11, 13, 29, 32 and the `read_ai_params` docstring were corrected in
  ICParse; DESIGN lists the map modules), found and fixed the one real bug the all-maps sweep showed (the Map settings step rewrote
  the 15 template maps: their 0 players was shown as 1), added `tools/map_sweep.py` and `tools/map_edits_07.py`, and wrote the
  README, guide, ROADMAP and this file. Open: the root repository's submodule pointers (committed with this release), the RE findings
  in `re_pending_0.7` still to be written into RE_LOG and Ghidra by the recorder, and the crop's slowness on small maps.
* **2026-11-18, evening.** Release 0.6.0: maps drawn like the game, creatures in maps as real models, Redraw water / Add water,
  and the pathfinding baked with the game's own method. Three builders worked in parallel (`ICParse.sgb.pathbake`;
  `sceneobj/map_look.py` + `ICParse.sgb.view` look; `sceneobj/map_creatures.py`); the integration wired their reports into
  `ops_map_import.py` (options Creatures and Game look, on by default; the operators of `map_look` are registered by
  `ops_map_import.register()`, because the contract test lets only `ops_*` / `panels_*` modules define classes), the Map panel
  (Redraw water, Add water, Show / Remove game look), the export (a dry map's added water is saved), `Sgb.generate_pathfinding` (now
  pathbake; a flat generated map is patched with pathbake too) and the reports' doc corrections (FORMATS.md, RE_LOG.md). One bug
  found by the roundtrip on 6p_big_bog: the saved water level was read from a median of the redrawn surface's points, which
  moves when the wet cells change; it is now read from the level the surface was drawn at. Three map tests that assumed
  creatures were empties, or imported with the defaults, were pointed at the options they test.
* **2026-11-18, later.** Release 0.5.0: maps can be edited and saved. `ICParse.sgb.edit` (`apply_edits`,
  `pathfinding_fidelity`, `EntityEdit`) and `ICParse.sgb.blockers` are the byte work; `sceneobj/ops_map_export.py` and
  `sceneobj/panels_map.py` read the changes from the scene (the Map panel, File > Export > Impossible Creatures Map,
  Check my changes, Add ground texture, Add object, Set owner, Drop to ground) and `tests/so_map_edit_test.py` holds
  them. `ops_map_import.load_model_collection` is the one place a model is built into a collection. The ICParse
  reconstruct baseline is keyed per archive. `run_all.py` 66 / 66.
* **2026-11-18.** Release 0.4.0. The scene export keeps to the game's rules: a part above 12,288 vertex entries is
  refused, a rebuilt part gets the bounding volumes the game builds (its own, one per palette bone, the entity box;
  ICParse `sgm.scene.bounds`, measured to reproduce the stock files), and an edited part with damage states is refused
  or, with *Drop the damage states of edited parts* (File > Export and Export Entity Folder), written without them.
  File > Export names `bz.export_scene` *Impossible Creatures Scene Object (.sgm)*; the importer reports stored normals
  Blender refuses and clip-baking errors. The map import (view only): File > Import > Impossible Creatures Map (.sgb)
  and Map from the Game, over `ICParse.sgb.view` (and the ICParse map parser fixed for Engine.sga's full terrain
  layer 1). The Game folder and Game build preferences are read; the seven others say "(not used yet)". Upgrading
  inside one Blender session runs the new libraries (`__init__._unload_stale_libraries`). Plainer words in New Project
  and the Blueprint panel. `run_all.py` 65 / 65.
* **2026-11-17.** The `sceneobj` package: a self-registering module registry (`sceneobj/__init__.py`), the bpy-free
  vocabulary (`model.py`), the UI kit, the preferences page, the test helpers (`tests/_so_common.py`, `so_*` tests) and
  the corpus sweeps (`tools/so_sweep.py`). The Blueprint panel (`sceneobj/panels_blueprint.py`), built as a proof of
  concept and moved into the package; New Project stamps the donor (`bz_donor`). Release 0.3.0, and the same night
  0.3.1: the same add-on with its library wheels labelled by their contents, so that it installs over an older
  version. The documents were cut
  down to the current state: README.md became the user's manual, and this file and ROADMAP_NATIVE.md lost their long
  per-increment write-ups. Those (every September increment with its measurements, review fixes and engine probes) and
  the old feature-by-feature README are in git: `git show 08536c1:STATUS.md`, `git show 08536c1:README.md`,
  `git show 08536c1:ROADMAP_NATIVE.md`. DESIGN.md keeps the design records.
* **2026-11-16.** New Project (**File > New > Impossible Creatures Project**). Gatherers and structures take any
  topology: Export Scene Object rebuilds the edited parts (seams split at UV and flat faces, per-material triangles,
  bone weights from the vertex groups, the part's own bounds) and writes the clips in the same file. The import shows
  the finest detail run (ICParse `Submesh.triangles`); it used to overlay every detail level, about 8 times too many
  triangles. Stock parts with NaN UVs or vertices no face uses come back untouched. `run_all.py` 58 / 58.
* **2026-11-15.** Correctness pass against the decompiled game (`decompilation/RE_LOG.md` of that day) and Blender
  5.3. Blender 5.3 flushes denormal floats inside Python, so ICParse writes every unedited chunk's own bytes; an
  Edit-Mode Undo replaces the Object and the Scene (the tests look them up again); Merge by Distance at 0 now merges
  coincident points (the Evaluated Surface welds only with Weld Distance > 0). The BONE record's third count is the
  number of corners at the start of the bone's range (the game poses only those at its coarsest level; New Creature
  had it wrong on 40 of 48 bones). A battle creature is skinned by its patches' corner bones, so Auto Skin gives each
  handle its nearest corner's bone (**Handles Follow Corners**) and Validate warns about a handle on another bone.
  Limits: at most 12 patch corners at a point (the Object Editor takes 16), 12,288 display vertices (a fatal error
  at High detail in retail), smoothing group 0 refused. Corner normals use the game's chained smoothing components;
  the combine budgets were re-derived (torso 6,584, limbs 7,879 display vertices; 263 combinable patches); Combine
  Preview follows the retail combiner (IC.exe's `CombinerImp`) where it differs from ComboTest.exe; the game's mesh
  cache keeps an edited creature's old mesh until its entry is deleted. 57 / 57; engine tier 108 / 108.
* **2026-09-17.** The BONE joint-limit tail decoded (both binaries convert to degrees at load; the implementation spec
  is in ROADMAP_NATIVE.md). A structure's moved vertices export (`scene_geometry_export_test`); a v2.0 part written
  with no detail record crashed the Object Editor, so a v2.0 part carries one full-detail record.
* **2026-09-16.** Fit Patches to Mesh, Sculpt Surface / Refit Patches, Retarget Creature to Mesh, Landmark Retarget,
  Transfer Texture and Trim Stray Surface (roadmap A1-A5); fit-to-rig (a fitted creature takes the template's limbs and
  skin); Evaluated Surface (D9); refusals at the moment of the edit and the distributable zip (D10); Optimise Ring
  Start (E1); the user guide with screenshots, captured in Blender 5.2.2. 57 / 57 and the engine tiers.
* **2026-09-15.** The patch feel (corners carry handles, automatic interiors), patch-level selection, hide and
  delete, the topology tools (patch roadmap B5-B7); Boner limbs, limb bone lists, connections, stub sockets, bone
  renames and Add Bone (Boner B1-B7); deleting and re-parenting source bones, joint limits and IK chains (C8).
* **2026-09-14.** The combiner wired in (Combine Preview, the Quick / Full / engine combine tiers); one export
  builder and the guard rails (the backlog defects D1-D6 and D8 closed); Boner's skinning loop; New Creature.
* **2026-09-13.** The lossless spine on every stock creature; Object Editor verdicts headless (`rdn-oe`); render
  parity with the editor's own draw calls.

## 3. Developing and testing without the GUI

```bash
cd ~/pyIC && uv sync                                                  # the venv (ICParse + construct + numpy); never pip-install into it
export IC_INSTALL="$HOME/.steam/debian-installation/steamapps/common/Impossible Creatures" SDL_AUDIODRIVER=dummy
.venv/bin/python3 blender_native/tests/run_all.py                     # all 64 scripts
.venv/bin/python3 blender_native/tests/run_all.py -k scene_           # the ones whose name contains scene_ (-v: full output of failures)
.venv/bin/python3 blender_native/tests/run_all.py --combine-full      # + every stock creature x both roles (offline)
.venv/bin/python3 blender_native/tests/run_all.py --engine            # + Object Editor / ComboTest.exe under Proton (on request)
.venv/bin/python3 blender_native/tools/so_sweep.py --identity         # the 205 stock scene donors, untouched export byte-identical
blender --background --factory-startup --python-exit-code 1 --python blender_native/tests/blender_smoke.py   # one test by hand
blender --python blender_native/tools/dev_launch.py -- Assets/stock/snail.sgm                             # a GUI session
.venv/bin/python3 blender_native/tools/build_extension.py --version X.Y.Z                                 # the distributable zip
```

* Tests derive the repository root from their own location (`$PYIC_REPO` overrides). Stock creatures come from
  `Assets/stock/` and `Assets/engine_stock/` (git-ignored; `tools/extract_stock.py` fills them from the game).
* The scene tests and the sweeps need the game install for their stock donors; `tests/_so_common.install_guard()`
  fingerprints it before and after, because no test may write under it.
* Pure tests run in the venv; in-Blender tests take the machine-wide launch lock (`tests/_so_common.run_locked`).
