# Architecture

IC Blender is a Blender extension written in Python. It follows one rule: **the editable model is the file's own data,
and export rewrites only what changed.** An untouched import and export produces an identical file.

## Layers

| Layer | Role |
|---|---|
| `ICParse` (library) | Reads and writes every game format. The add-on has no binary format code of its own. |
| `combotest` (library) | The game's creature combiner, used by Combine Preview and validation. |
| Core modules | Editing logic with no dependency on Blender, so it can be tested in plain Python: `core`, `boner`, `structure`, `topology`, `patchrules`, `fastsurf`, `limbs`, `combocore` and `stockextract`. `topoops`, `fitops`, `retargetops`, `landmarkops`, `trimops`, `texops`, `material` and `store` also import without Blender; their Blender parts exist only inside Blender. |
| Blender layer | Operators, panels and GPU drawing: every other module. |
| `sceneobj/` | Blueprints, maps, the add-on's preferences and the registry that loads this folder's modules. The meshes of structures, gatherers, characters and props are imported and exported by `scene.py` and `ops.py` (Export Scene Object, Export Scene Geometry). |

## Data model

**Creatures** are bicubic Bezier patch models. The Blender mesh's vertices are the file's control points, in file order.
One quad per patch exists for selection only; the true surface is drawn by a GPU overlay. The skeleton is an armature,
each control point follows one bone, and animation clips are Blender actions. On export the source file is read again and only
the edited data is replaced, so the source file must still exist.

**Scene objects** (structures, gatherers, props) are ordinary triangle meshes. Export rebuilds only the parts whose
mesh changed.

**Maps** are imported as a terrain mesh, a water surface and one object per placed entity. Each feature registers its
import and save steps through `sceneobj/map_hooks.py`.

## Creature modules

| Area | Modules |
|---|---|
| File input and output | `core`, `store`, `ops`, `build`, `stockextract` |
| Viewport | `draw`, `fastsurf`, `texdraw`, `gnsurface` (experimental), `material` |
| Modelling | `patchrules`, `patchfeel`, `patchsel`, `topoops`, `edittools`, `guards`, `sculptops` |
| Skeleton and skinning | `rig`, `boner`, `boneops`, `ikops`, `limbs`, `limbops`, `connops`, `structure`, `topology` |
| Creation and reshaping | `newproject`, `newcreature`, `fitops`, `retargetops`, `landmarkops`, `trimops` |
| Textures and animation | `texops`, `actions` |
| Combining | `combocore`, `combopreview` |
| Interface | `ui`, `props`, `gameinstall` (Install into Test Game) |

`scene` imports and exports scene object meshes.

## `sceneobj/`

| Area | Modules |
|---|---|
| Shared | `model`, `prefs`, `ui_kit` |
| Blueprints | `panels_blueprint` |
| Maps | `map_*` (logic), `ops_map_*` (operators), `panels_map*` (panels) |

## Dependencies and packaging

The release zip is a Blender extension that carries `ICParse`, `combotest`, `construct` and `lupa` as Python wheels.
When a new version is installed over a running one, `__init__.py` unloads an older `ICParse` and `combotest` before
importing; `construct` and `lupa` stay loaded (their versions are pinned, and `lupa` cannot be loaded twice).


The add-on reads the game's textures, stock models and tables from the user's own installation.
