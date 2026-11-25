# Architecture

IC Blender is a Blender extension written in Python. It follows one rule: **the editable model is the file's own data,
and export rewrites only what changed.** An untouched import and export produces an identical file.

## Layers

| Layer | Role |
|---|---|
| `ICParse` (library) | Reads and writes every game format. The add-on has no binary format code of its own. |
| `combotest` (library) | The game's creature combiner, used by Combine Preview and validation. |
| Core modules | Editing logic with no dependency on Blender, so it can be tested in plain Python. |
| Blender layer | Operators, panels and GPU drawing. |
| `sceneobj/` | Structures, gatherers, props, blueprints and maps. |

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
| Interface | `ui`, `props` |

`scene` imports and exports scene object meshes.

## `sceneobj/`

| Area | Modules |
|---|---|
| Shared | `model`, `prefs`, `ui_kit` |
| Blueprints | `panels_blueprint` |
| Maps | `map_*` (logic), `ops_map_*` (operators), `panels_map*` (panels) |

## Dependencies and packaging

The release zip is a Blender extension that carries `ICParse`, `combotest`, `construct` and `lupa` as Python wheels.
When a new version is installed over a running one, `__init__.py` unloads the older libraries before importing.

The add-on reads the game's textures, stock models and tables from the user's own installation.
