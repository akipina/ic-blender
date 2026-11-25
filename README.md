# IC Blender

A Blender add-on for *Impossible Creatures* (Relic, 2003). It opens, edits and saves the game's own files:

* **Creatures**: the combiner's animals, built from curved patches, with their skeletons, textures and animations.
* **Gatherers, characters, structures and props**: ordinary meshes, plus their blueprints (`.ebp`).
* **Maps** (`.sgb`): terrain, water, placed objects, scripts and cinematics.

What you don't change is written back byte for byte. You need your own copy of the game; the add-on contains none of
its files. It is a fan project, not affiliated with the game's owners.

**Nothing the add-on saves has been tested in the game yet.** Keep your originals.

## Install

You need Blender 5.3 or newer. Only Linux is tested; Windows and macOS should work.

1. Download `ic_blender-0.9.0.zip` from the [latest release](https://github.com/akipina/ic_blender/releases/latest).
2. In Blender: **Edit > Preferences > Get Extensions**, the arrow menu at the top right, **Install from Disk...**, and
   pick the zip.
3. In **Edit > Preferences > Add-ons**, check that **IC Blender** is ticked. If your game is not in the usual Steam
   folder, set its **Game folder** there.
4. In the 3D Viewport press **N** and open the **IC** tab.

**Coming from "SGM Native Patches" (0.8.x)?** Uninstall it first, then install this one and set the game folder again.

Later versions install over the older one the same way. If the add-on fails to load afterwards, uninstall it, restart
Blender and install again.

## First steps

The full guide with screenshots is [`docs/guide/index.html`](docs/guide/index.html): download the repository and open
the file in a browser.

### Creatures

1. **File > Import > Impossible Creatures Model (.sgm / .ebp)**, or start one with **File > New > Impossible Creatures
   Project...**.
2. Press **Tab** and drag control points with **G**. The surface follows.
3. In the **IC** tab, **Validate (will it load?)** lists anything the game's tools would refuse.
4. **Export Model** writes the `.sgm`.

### Structures, gatherers and props

1. **File > New > Impossible Creatures Project...**, set **Type** to **Structure / prop** or **Gatherer / character**,
   pick a stock model to start from, and choose an empty folder.
2. Edit the mesh with Blender's own tools.
3. In the **Blueprint** panel press **Load Blueprint** to change the name, kind, footprint or sounds.
4. **Export Entity Folder...** writes a `Data` folder you can copy into the game.

### Maps

1. **File > Import > Impossible Creatures Map from the Game...** (or **Map (.sgb)** for a file).
2. Sculpt the ground, move the water, paint, and move or add objects. The **Map** panel has the Mission Editor's tools:
   map settings, painting, triggers, cinematics and file lists.
3. **Save ready-to-drop folder...** writes the map and the files that go with it.

**Show game look** draws the map as the game does: ground, sky, water, border and decals. It leaves out the game's
ground lighting, shadows and moving water, and the free 3D view is the mirror image of the game's (cameras are not).
**Cinematics > Replay** plays a map's cut-scenes on the timeline.

## Using your files in the game

* Use a copy of the game, **not the Steam folder**: Steam can overwrite or delete files that are not its own.
* Copy the exported `Data` folder into your copy's main folder and let the folders merge. Loose files win over the
  game's archives. Delete them to undo.
* A new name appears in the game only where a map or script asks for it. To replace a stock object, export it under its
  exact folder and name (for example `structures/lab`).
* A creature needs its `.ebp`, `.lua` and `.lsc` as well as the `.sgm`; the add-on writes only the `.sgm`. After
  copying a creature in by hand, delete its entry under `<game folder>/Cache/`.
* If something does not show up, look at `warnings.log` in the game folder.

## Limits

**Creatures:** material IDs and smoothing groups are kept but not editable. There is no bevel, Add Tri, Detach or Reset
Handles. **Transfer Texture** raises a patch's vertex cost (Validate shows it).

**Structures, gatherers and props:** material, texture, team-colour and bone edits are not saved. Attachment points and
damage states are not shown; editing a part with damage states drops them, and only when you ask. Parts can't be added,
and a part holds at most 12,288 vertices.

**Maps:** the terrain grid can't change, scale is ignored, and creatures show without animation. Texture stamps show in
the game only at its highest ground detail. Replay timings are approximate in places, and each event says so.

## Reporting a problem

Open an [issue](https://github.com/akipina/ic_blender/issues) with:

* your Blender version, add-on version and operating system;
* the file you started from, and what you did;
* the exact message from Blender's status bar or **Info** editor;
* for a problem in the game, its `warnings.log`.

## Licence

GNU General Public License, version 3 or later (`LICENSE`). The bundled `construct` and `lupa` libraries are under the
MIT licence. The zip also carries two libraries (`ICParse` and `combotest`) whose source is inside it; building the zip
yourself needs them.
