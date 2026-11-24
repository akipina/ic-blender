# sceneobj: adding a module

`blender_native/sceneobj/` holds the add-on's support for gatherers, characters, structures and props (the scene
objects). Every new piece of that work is a new module in this folder: the package finds it by its name, registers it,
reloads it and ships it in the zip. This page is for whoever adds one. The design behind it is in
[DESIGN.md](../../DESIGN.md) (*The sceneobj package*, *The Blueprint panel*); what has been verified is in
[STATUS.md](../../STATUS.md).

## What is here (2026-11-18)

| module | what it is | how it is loaded |
|---|---|---|
| `__init__.py` | the registry: finds, orders, registers, unregisters and reloads the modules below | the add-on imports it |
| `model.py` | the words every module speaks, without Blender: `Issue` / `issue()`, `SourceBundle`, `OutBundle`, `ExportReport`, the `Slice` protocol, `refs`, `claim_edit` | core, `PURE = True` |
| `ui_kit.py` | panel, list and message helpers: `alert`, `issue_rows`, `BZ_UL_rows`, `SubPanel`, `draw_scene_pointer` | core |
| `prefs.py` | the add-on's preferences page (`BZ_AP_prefs`) and `get()` / `resolve_*()` | core |
| `ops_map_import.py` | the map import: File > Import > Impossible Creatures Map (.sgb) and Map from the Game, over `ICParse.sgb.view` | discovered, `ORDER = 60` |
| `ops_map_export.py` | edit and save maps: File > Export > Impossible Creatures Map (.sgb), Add object / Set owner / Drop to ground / ground textures, over `ICParse.sgb.edit` | discovered, `ORDER = 61` |
| `panels_map.py` | the Map panel (SGM tab) | discovered, `ORDER = 62` |
| `panels_blueprint.py` | the Blueprint panel: the `.ebp` of a scene object, and Export Entity Folder | discovered, `ORDER = 80` |

The core modules `subtype`, `entity`, `importer`, `exporter` and `compat` are planned but not written yet: the registry
lists them in `sceneobj.MISSING_CORE` and skips them. No module registers a slice yet, and the Entity panel
(`BZ_PT_entity`, which `ui_kit.SubPanel` sub-panels will hang under) does not exist yet. Working notes from finished
pieces of work, with what was measured and what is still open, are in working notes kept with the private repository.

## Adding a module, step by step

1. **Name it by what it is.** `panels_<thing>.py` (panels), `ops_<thing>.py` (operators), `props_<thing>.py` (property
   groups), `slice_<stage>.py` (an export stage), `guards_<thing>.py` (checks that run while the user works). Any other
   name is a plain helper: imported by whoever needs it, never registered, and it must not define `CLASSES`, `SLICE`,
   `register` or `unregister` (the contract test says so).
2. **Say what the registry should do with it**, all optional: `ORDER` (an int, default 50: the order modules are
   imported and their hooks run, not the export stage), `CLASSES` (a tuple of Blender classes), `register()` /
   `unregister()` hooks (pointer properties, menus, handlers), `SLICE` (an instance of the Slice protocol, or a tuple),
   `ABSTRACT` (names of classes it defines but never registers) and `PURE = True` (below).
3. **Edit nothing else.** Not `blender_native/__init__.py`, `ui.py`, `tools/build_extension.py`, `tests/run_all.py` or
   `tests/extension_install_test.py`: the registry finds the module, the zip carries the whole folder
   (`build_extension.COPY_DIRS`), and `run_all.py` finds `tests/so_<name>_test.py` and `tests/so_<name>_pure_test.py` by
   their names.
4. **Write its tests** with the helpers of `tests/_so_common.py` (below).
5. **Before every commit** run `run_all.py -k so_registry` and `-k so_contract`, your own tests, and the tests of
   anything you build on (`-k scene_`, `-k new_project`, `-k blueprint_poc`).
6. **Say it in plain words.** Every message a user can see says what is wrong and what to do, with no internal names.

## The registry rules

* Core modules are imported first, in `sceneobj.CORE` order (`model`, `subtype`, `entity`, `ui_kit`, `prefs`, `importer`,
  `exporter`, `compat`). Then every module whose name starts with `slice_`, `ops_`, `panels_`, `props_` or `guards_`,
  sorted by `(ORDER, module name)`.
* `sceneobj.register()` registers every class, then calls every module's `register()` hook, so a hook may point a
  property at a class. Classes are registered in a safe order whatever the module order is: **property groups first**
  (a group that points at another comes after it) and **a child panel after its `bl_parent_id` parent** (Blender 5.3
  refuses both other orders). List every class in the module's `CLASSES` and let the registry order them.
* A sub-panel whose parent panel does not exist is left out and reported in `sceneobj.ERRORS` (the rest registers); any
  other failed registration undoes everything that registration did. `unregister()` undoes exactly what `register()`
  did, in reverse.
* A module that cannot be imported is reported (`sceneobj.ERRORS`, the console, the add-on's preferences page under
  *Parts of the add-on that could not be loaded*) and the rest still load. The tests require `ERRORS == []`.
* `sceneobj.reload()` (called by the add-on's `_reload()` just before `ui`) reloads every module of the package, each
  after the siblings it imports, helpers included, and discovers again, so no module runs stale code after an in-place
  update.
* `blender_native.register()` calls `sceneobj.register()` last, and `blender_native.unregister()` calls
  `sceneobj.unregister()` first. `blender_native._CLASSES` does not list the package's classes.
* Reach the add-on's own modules through its package name, not `from .. import`, so a module works however the add-on
  is installed (`blender_native` from the repository, `bl_ext.<repository>.sgm_native_patches` as an extension). The
  Blueprint panel does it with `importlib.import_module(f"{_PKG}.{name}")`.

## Pure modules

`model.py` is bpy-free, and so is any module that declares `PURE = True`. The pure tests (the venv Python,
`tests/so_*_pure_test.py`) import the package with `bpy` blocked (`_so_common.import_pure()`); only PURE modules load
there. The contract test reads each PURE module's source: no top-level import of `bpy`, `bmesh`, `mathutils` and the
like, and no import of a sibling that is not PURE. A slice keeps `read`, `changed`, `apply`, `check` and `on_rename`
free of Blender, imports `bpy` inside `build` / `gather` only, and declares `PURE = True`.

## The Slice protocol (`model.Slice`; `model.BaseSlice` has do-nothing defaults)

No module registers a slice yet; this is the contract the first ones will be held to (`so_contract_test` checks every
registered slice against it).

```
name: str     unique, lower case; a stage name keeps its number (below)
order: int    export order; unique across slices
claims: tuple datablock kinds this slice owns, written KIND:what, e.g. ("EMPTY:bz_marker",), ("MESH:bz_part",); no two slices claim one
read(src: SourceBundle) -> Any             what the files hold (pure)
build(ctx, entity, data) -> None           create the Blender datablocks (import / new project)
gather(ctx, entity) -> Any                 Blender's current state as plain data
changed(data, state) -> bool               semantic compare with tolerances; False must mean "write nothing"
apply(out: OutBundle, src, data, state)    edit the bundle (bytes in, bytes out)
check(src, data, state) -> list[Finding]   the rules, for Validate and the checks that run while the user works
on_rename(kind, old, new, state) -> None   follow a rename
```

Export stages (`model.STAGE_ORDER`): materials 10, textures 12, skeleton 20, geometry 30, morphs 40, markers 50, bounds
60, anim 70, controller_meta 80, controller_motion 81, controller_events 82, files 90, validate 100. A slice named like
a stage must use its number.

`OutBundle.shared` hands values from a stage to the ones after it and **accepts only declared keys**
(`model.SHARED_KEYS`: skeleton, materials, textures, geometry, morphs, markers, bounds, clips). A stage that needs
another key calls `model.declare_shared(key, owner)` when its module is imported. The contract test scans every
sceneobj source for `.shared[...]` keys and fails on an undeclared one, so a misspelt key cannot silently hand nothing
over.

`model.issue(code, level, text, where="")` (or `model.Issue(...)`) returns an ICParse `Finding`; there is no other
problem record. `OutBundle.add_file(path, data)` takes only lower-case relative paths (the game's archive lookup is
case-sensitive).

`model.refs.rename(kind, old, new, states=None)` (kind: bone, clip, marker, material, part) tells every slice, in
export order, and returns one ERROR finding per slice that could not follow: show them to the user.
`model.claim_edit(code)` is called at import by a slice that now exports a kind of edit, so the safety checks stop
refusing it; the code must be one of `model.GUARD_CODES` (or declared with `declare_guard_code`).

## A worked example: the Blueprint panel

`panels_blueprint.py` is the first discovered module, and shows the rules in use:

* its name starts with `panels_`, so the registry finds it; `ORDER = 80` puts it with the controller stage;
* `CLASSES` lists three property groups, five operators and two panels; `BZ_PT_blueprint_stats` has
  `bl_parent_id = "BZ_PT_blueprint"`, and the registry registers the parent first whatever the order in the tuple;
* its `register()` hook only points `Object.bz_blueprint` at `BZ_PG_blueprint` (after the classes exist), and
  `unregister()` removes the pointer; the hook also registers any of its classes no registry did, which is how its
  test loads it on its own;
* it reaches `scene`, `core`, `store`, `actions` and `newproject` through the add-on's package name, and looks
  `ICParse.ebp.scene` and `ICParse.ebp.stats` up with `importlib.util.find_spec`, so a missing library is a plain
  sentence in the panel, not an import error (`ICParse.ebp.controllers`, the class table, it imports directly);
* every message is plain words, which its test checks.

Its test is `tests/blueprint_poc_test.py`; that name does not start with `so_`, so it is listed in `run_all.py` by hand.
Name a new module's test `so_<name>_test.py` instead and it needs no line there.

## UI kit (`ui_kit.py`)

* `alert(layout, text)` and `issue_rows(layout, findings, limit=None)`: wrapped red messages, worst first.
* `BZ_UL_rows`: **one** registered list. Draw it with
  `layout.template_list("BZ_UL_rows", "<list id>", data, "rows", active, "active")` and say how a row looks with
  `@ui_kit.row_drawer("<list id>")`; Blender filters and sorts it by name. No module registers a list class of its own.
* `SubPanel`: the base of the sub-panels of the Entity panel to come. Set `bl_idname`, `bl_label`, `bl_order` (the
  registration order of parallel modules is not the display order, so always set it), `capability` and `draw`; list the
  class in `CLASSES`. It is shown only when `entity.has(context, capability)` says so (never by subtype) and starts
  closed. Until an `entity` module exists, `has_capability` is False and such sub-panels stay hidden.
* `draw_scene_pointer(layout, obj)`: what the SGM panel shows under a scene object: its info box and export buttons
  today, a single pointer line as soon as a panel `BZ_PT_entity` is registered.

What the kit expects of the entity layer when it is written: `sceneobj.entity.has(context, capability)` returns a bool;
`sceneobj.entity.find_entity(obj)` returns the entity or None and does not raise for a creature object (the SGM panel
asks on every redraw); the Entity panel's `bl_idname` is exactly `BZ_PT_entity`; `exporter.export_entity` returns a
`model.ExportReport`.

## Preferences (`prefs.py`)

`prefs.get(context)` returns the live preferences, or an object with the same names holding the defaults (a background
Blender has no preferences entry for the add-on). Folders: `resolve_game_dir()` -> `(folder, source)` (the setting,
`$IC_INSTALL`, the Steam folders), `resolve_test_game_dir()`, `resolve_build_dir(project_dir)`, `resolve_mods_dir()`.
Switches the in-game checks may change: `morph_export` (V2_SINGLE_LOD | DROP), `txr_type_default` (stock1 | stock0),
`delivery_mode` (AUTO | NEW_NAME | REPLACE_STOCK), `bounds_mode` (AUTO | KEEP_ENTITY_BOX); `target_mod` (icmod |
tellurian | insectmod). Read so far (2026-11-18): `resolve_game_dir()` by New Project, the model import's texture
lookup, the map import and the Blueprint panel (whose Export Entity Folder also refuses that folder), and `target_mod`
(the Game build) by the Blueprint panel's class table; the other settings say "(not used yet)" on the preferences page.
A module that reads one does it through these functions, never through `bpy.context` directly.

## Tests

* `tests/so_<name>_test.py` runs in a headless Blender, `tests/so_<name>_pure_test.py` in the venv Python; `run_all.py`
  finds both by name (`run_all.py -k so_<name>`).
* `tests/_so_common.py`: `tmpdir()` (a fresh folder per call, never a fixed `/tmp/bz_*` path), `run_test(name, fn)`
  (its done file is named with the pid), `blender_lock()` / `run_locked()` / `run_blender()` (one Blender launch at a
  time, a second apart, machine-wide), `has_api(thing, name)` (never a class-level `hasattr`), `skip(reason)`,
  `install_guard()`, `FakeLayout` (panels cannot be drawn in background mode: draw into this and read `texts()` /
  `operators()`), `import_pure()`, `addon()`, `fresh()`, `start_project(kind, donor)`.
* **A test for something that has not been written yet skips with the reason**: `C.skip("...")` prints `SO_SKIP: ...`;
  `run_all.py` reports the test as SKIP and lists the reasons at the end. It never passes silently.
* No test asserts wall-clock time (assert counts, cache hits, call order). No test writes under `$IC_INSTALL`
  (`install_guard`).

`tools/so_sweep.py` runs the corpus sweeps: `--identity` (205 donors: 203 identical + 2 refused), `--all` (every
`art/ebps` scene row: 295 identical + 10 refused of 305), `--idempotent`, `--valid`, and `--noop SLICE`, `--edit`,
`--round`, which skip with a plain reason until the entity layer and the slices they need exist. Its docstring says how a
slice opts in (`population`, `sweep_edit`, `round_trip`).

Commands (from the repository root, with
`export IC_INSTALL="$HOME/.steam/debian-installation/steamapps/common/Impossible Creatures" SDL_AUDIODRIVER=dummy`):

```
.venv/bin/python3 blender_native/tests/run_all.py -k so_                 # every sceneobj suite; -k so_contract / so_registry / so_<name> for one
.venv/bin/python3 blender_native/tests/run_all.py -k scene_              # the four scene tests (+ -k new_project, -k blueprint_poc)
.venv/bin/python3 blender_native/tests/run_all.py -k extension_install   # the zip installs in a clean Blender and carries sceneobj/
.venv/bin/python3 blender_native/tools/so_sweep.py --identity            # 203 identical + 2 refused of 205 donors, about 90 s
.venv/bin/python3 blender_native/tools/so_sweep.py --all                 # 295 identical + 10 refused of 305 rows, about 100 s
```

## Blender 5.3 rules (each one was hit)

* Probe API existence on **instances or `bl_rna`**; a class-level `hasattr` reports real functions as missing.
* An **EditBone exposes only custom properties**; bone identity is the custom property `bz_src_index`. PropertyGroups are
  never read in Edit Mode.
* `Action.fcurves` / `Action.id_root` are gone (use the channelbag); `Bone.select` is gone (use `PoseBone.select`).
* Never `bmesh.ops.subdivide_edges(use_grid_fill=True)` (segfault); call `ensure_lookup_table()`.
* `foreach_get` / `foreach_set` with explicit dtypes (`model.foreach_get` / `foreach_set`).
* Denormals are flushed: copy unchanged chunks as bytes, compare floats with tolerances.
* Edit-Mode undo replaces Object and Scene: re-fetch by name; `PointerProperty` is safe.
* A bone-parented child sits at the bone TAIL: read `matrix_world`.
* Never `except: pass` around `normals_split_custom_set_from_vertices`: count and report.
* A property group must be registered before the classes that point at it, and a child panel after its parent: the
  registry orders them, but list them in the module's `CLASSES`. A refused class stays half registered, so a retry
  fails; a second panel with the same `bl_idname` silently replaces the first.
* `StringProperty(maxlen=N)` cuts a longer text silently and counts bytes, and `IntProperty(min, max)` clamps silently:
  check limits yourself and compare what the property holds with what you put in (the Blueprint panel does).
* A registered `PropertyGroup` is not reachable as `bpy.types.<Name>`: use
  `bpy.types.PropertyGroup.bl_rna_get_subclass_py(name)`. An operator's properties are on
  `bpy.ops.<mod>.<op>.get_rna_type().properties`, not on its class's `bl_rna`.
* In a background Blender `context.preferences.addons` has no entry for the add-on, even after `addon_enable`: read
  preferences through `prefs.get()`.
