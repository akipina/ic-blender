# SGM Native Patches: Impossible Creatures models in Blender

A Blender add-on that opens, edits and writes back the models of *Impossible Creatures* (Relic, 2003):

* **creatures**, the animals of the game's combiner, which are built from curved patches; and
* **gatherers, characters, structures and props** (the henchmen, the story characters, the buildings, the scenery),
  which are triangle meshes, together with their **blueprints** (the `.ebp` file that says what kind of thing a model is,
  the name the game shows, the ground it blocks and the sounds it loads); and
* **maps** (the game's scenario files, `.sgb`): the ground, the water and the things placed on them, which open in
  Blender and can be saved again.

What you did not change comes back byte for byte: an untouched model exports identical to the file it came from, and
an edit rewrites only the part of the file you edited.

Release **0.8.1** (2026-11-24). New in it: **maps are drawn by the game's own rules**, read again from the game's code. The
ground is the map's own ground picture with the detail texture as a fine grain near the view; **decals are half the size** they
were drawn before; the water is the sky's reflection, brighter or darker with depth and distance, over the map and out to the
border; the sky has the game's shape; rock-face decals lie on the cliffs instead of being cut through by them; the maps' water
sparkles (which the game never draws) no longer show as pale triangles; and **texture stamps now land where the game shows them** (before, they were painted
mirrored north to south, at twice a decal's size). The cut-scene cameras show the game's picture, not its mirror image. Not
compared with the running game.

0.8.0 (2026-11-19): the **Replay** in the Cinematics part of the Map panel: pick one of a map's cinematics
and Blender acts it out on the timeline the way the game plays it, with its trigger script (units walk, things appear and vanish,
a lab flies, a cut-scene plays, the camera cuts, the screen fades), and lists every event as exact, approximate or skipped. It is
checked by numbers only, never against the game, and it never changes the map. 0.7.0 (2026-11-19): the Mission Editor's tools for maps (see *Maps and scenarios*): a new map from the
editor's 15 terrain templates, map settings and an editable look (sunlight, shadows, time of day, sky, sea, fog), a list of
problems before you save, painting (passability, terrain types, water at its own level, decals, blend tiles, texture
stamping, the Overlay menu), scripting (groups, variables, triggers, dialogues, AI paths), the cinematics (NISlets), the
height-map, terrain and entity files, the Object and Blueprint lists and Terrain Crop. Not tried in the game or in the Mission
Editor. 0.6.0 (2026-11-18): a map is drawn the way the game draws it (the sun, the sky, the fog, the open
sea round it, its decals and its water colours), the creatures a map places show as their real models, stock and combined,
the water is drawn again after you change the ground (and a map without water can be given some), and the pathfinding is
baked the way the game's own editor bakes it. 0.5.0 made a map editable and savable: the ground (sculpt it), the water
level, the ground textures, and the placed things (move, turn, delete, copy, add one from the game's list, change its
owner). 0.4.0 added the map import, the game's rules for the export of gatherers, characters, structures
and props (at most 12,288 vertices a part, the bounding volumes the game builds, damage states protected), the
**Game folder** setting, and upgrades that take effect without restarting Blender. It needs **Blender 5.3.0 or
newer** and has been developed and tested on **Linux only**.
The [user guide](docs/guide/index.html) walks through the common jobs step by step.

**Contents:** [Install](#install) · [Upgrading](#upgrading) · [Your first project](#your-first-project) ·
[Creatures](#creatures) · [Gatherers, characters, structures and props](#gatherers-characters-structures-and-props) ·
[The Blueprint panel](#the-blueprint-panel) · [Maps and scenarios](#maps-and-scenarios) ·
[Trying your work in the game](#trying-your-work-in-the-game) ·
[What is not done yet](#what-is-not-done-yet) · [Reporting a problem](#reporting-a-problem) ·
[For developers](#for-developers)

## Install

You need Blender 5.3.0 or newer and the game. The add-on reads the game's own files (its textures, the stock models
New Project starts from, the maps, the combiner's tables) from the game folder. It looks first in the **Game folder**
set in the add-on's preferences (below), then in the folder named by the `IC_INSTALL` environment variable, then in the
usual Steam folders on Linux (`~/.steam/debian-installation/steamapps/common/Impossible Creatures`,
`~/.local/share/Steam/steamapps/common/Impossible Creatures`, `~/.steam/steam/steamapps/common/Impossible Creatures`).
On any other system, set the Game folder. Combine Preview (for creatures) reads only `IC_INSTALL` and the Steam folders,
so set `IC_INSTALL` before you start Blender if you combine creatures there. The add-on only reads the game folder and
never writes into it by itself: Export Entity Folder refuses to, and the other exports and New Project write only where
you point them, so give them a folder of your own too.

1. Get the zip, `sgm_native_patches-0.8.1.zip`, from this repository's
   [Releases page](https://github.com/akipina/ic-blender/releases/latest). Everything the add-on needs is inside it; there is nothing else to install.
2. In Blender: **Edit > Preferences > Get Extensions**, open the drop-down menu at the top right of that page and choose
   **Install from Disk...**. Pick the zip.
3. Make sure **SGM Native Patches** is ticked (enabled) in **Edit > Preferences > Add-ons**.
4. In the 3D Viewport, press **N** to open the sidebar and pick its **SGM** tab.

To remove the add-on: **Edit > Preferences > Get Extensions**, find SGM Native Patches among the installed extensions,
open its drop-down menu and choose **Uninstall**.

The add-on's own preferences (**Edit > Preferences > Add-ons**, expand SGM Native Patches) say which game folder is in
use and why. When **Game folder** is set, New Project, the model and map imports and the Blueprint panel read the game
from it before `IC_INSTALL` and the Steam folders, and Export Entity Folder will not write into it. **Game build**
chooses which class numbers the Blueprint panel counts as known. The other settings say "(not used yet)": nothing
reads them in this version.

The zip carries its libraries as wheels that Blender installs for you: the format library ICParse, the combiner
(`combotest`), `construct`, and `lupa` (which runs the game's own rating script for Combine Preview). `lupa` is
compiled, and the zip has it for Linux x86_64 and arm64, macOS on Apple chips and Windows x64. On other machines the
add-on still works; only the combined ratings, cost and rank in Combine Preview are missing, and the panel says so.

> **Platform support.** Everything was built and tested on Linux. The add-on is plain Python on Blender's own
> interface and the zip carries the Windows and macOS libraries, so it *should* work there, but nobody has tried it.
> If you do, please report what happens.

## Upgrading

Install the newer zip over the older one the same way (**Get Extensions > drop-down > Install from Disk...**); there is
no need to uninstall the older one first. From 0.4.0 on there is no need to restart Blender either: when the new
version starts, it puts away the libraries the older one left loaded and uses its own. This also holds for the step
from 0.3.1 or older to 0.4.0, because 0.4.0 does it when it starts. (The one exception: should a later version bring a
new `construct`, restart Blender after installing it.)

> **If the add-on fails to load after an upgrade,** uninstall it (**Get Extensions**, the add-on's drop-down menu,
> **Uninstall**), close and reopen Blender, and install the new zip again.

*(Corrected 2026-11-18: up to 0.3.0, a newer zip installed over an older version kept the older version's libraries,
so the old one had to be removed first. 0.3.1 replaced the library files, but the session that did the install still
ran the older libraries until Blender was restarted. 0.4.0 loads the new ones in the same session. Checked in
throw-away Blender profiles on Linux, from a test build and from the real 0.3.1 zip, with Blender started from the
command line; the Install from Disk dialog itself was not tried.)*

## Your first project

### Change the Lab (a structure), about ten minutes

1. **File > New > Impossible Creatures Project...** opens a file browser. In its side panel set **Type** to
   **Structure / prop**, **Start from** to **Lab (structures)** and **Animations** to *Keep its own*.
   Choose an empty folder of your own, type a file name such as `mylab.sgm`, and press **New Impossible Creatures Project**.
2. The Lab appears: 23 parts on a skeleton of 19 bones, with its 9 animations as Actions. The status bar says
   `Started the structure / prop project mylab in mylab.sgm`. The Lab's blueprint was copied beside it as `mylab.ebp`.
3. Click a part, press **Tab** for Edit Mode and change it the way you change any mesh: move vertices, extrude, delete
   or subdivide faces. Press **Tab** again when you are done.
4. In the **SGM** tab, find the **Blueprint** panel and press **Load Blueprint**. It shows the Lab's name (the code
   `$41508`, which the game looks up in its language files), its kind (**5 - Structure: a building**), its footprint
   (7 by 5 path cells) and the folder the game finds it in (`structures`). Type a name of your own, for example
   `My Lab`. The panel now says *Edited: name. Written when you export.*
5. Press **Export Entity Folder...**, choose an empty folder that is **not** inside the game's folder or Steam's, and
   export. The folder now holds `Data/art/ebps/structures/mylab.sgm`, `Data/art/ebps/structures/mylab.ebp` and a
   `README.txt` that says how to try the two files in the game.

A new name such as `mylab` only appears in the game where a map or a script asks for it. To change the game's own Lab
everywhere instead, export with the file name `lab` in the folder `structures`: the export dialog and the README then
say that these files replace the original. See [Trying your work in the game](#trying-your-work-in-the-game).

### Start a creature

1. **File > New > Impossible Creatures Project...**, **Type**: **Creature**, a **Name**, **Start from**: **Default
   skeleton**. Choose where its `.sgm` goes and press **New Impossible Creatures Project**. You get a simple body on the
   game's standard 48-bone skeleton, with limbs, connection rings, a texture and the standard clips.
2. Press **Tab** and drag control points with **G**: the curved surface follows as you drag.
3. In the SGM tab, **Tools > Validate (will it load?)** says whether the game's tools would accept the creature.
4. **Export SGM (Native Patches)** writes the `.sgm`.

## Creatures

A creature `.sgm` is a set of control points that shape curved four-sided patches. The add-on opens it as a Blender
object whose vertices **are** those control points, one for one, and draws the exact surface the game makes from them
over the top. Every Blender tool that moves vertices moves real control points, and nothing is converted on the way in
or out. What you can do:

* **Open** a creature: **File > Import > Impossible Creatures Model (.sgm / .ebp)**, or **Import SGM / EBP (Native
  Patches)** in the SGM tab. A blueprint (`.ebp`) opens the model it names or carries inside it. Every clip becomes a
  named Action on the creature's rig.
* **Edit as in gmax's Editable Patch.** A corner drags its tangent handles with it, and the inner points of each patch
  follow automatically (they are hidden; **Display > Show Interior Points** shows them). **Set Corner / Set Coplanar**,
  **Display > Lock Handles**, **X-Mirror Editing**, **Symmetrize (X)** and **Relax Seam Tangents** are there too.
  **Display > Patch Rules (Max feel)** turns the automatic rules off for raw control-point editing.
* **Select patches.** In Edit Mode, Face select (**3**) selects whole patches: click, box, lasso, Select Linked,
  Grow / Shrink. **H**, **Shift+H** and **Alt+H** hide and reveal patches; **X > Patches** deletes them (the holes are
  yours to close: Validate lists them).
* **Make new surface.** Edit Mode, **Topology** box: **Weld** / **Weld Target**, **Add Quad**, **Fill Hole**,
  **Subdivide** (the split runs on along the strip, so the surface stays closed), **Extrude Region**, **Mirror to
  Whole**; **Modeling > Extrude Patch**; **Tools > Attach** joins another creature in. Each is one Undo step and refuses,
  in words, anything the game would not load.
* **Start a creature from something else.** **New Creature (From Scratch)** (also **Add > Impossible Creatures Creature
  (From Scratch)** in the 3D Viewport) builds the same starting body as New Project. With an ordinary mesh active, the
  SGM tab adds **Fit Patches to Mesh** (any closed mesh becomes a patch creature at a patch budget you choose, split
  over the template's limbs and bones) and **Convert Mesh to Creature** (a finished quad mesh, every point on one bone:
  rig it by hand afterwards).
* **Fit a creature onto another mesh.** **Tools > Retarget Creature to Mesh** keeps the creature's layout, limbs,
  rings, skeleton, skin and clips and moves its surface onto a target mesh. **Create Landmarks** puts a marker on each
  limb tip and root, which you drag onto the matching places of the target, and **Landmark Retarget** bends the creature
  to them before fitting; **Trim Stray Surface** finds the patches the target has no surface under.
* **Sculpt.** **Sculpt Surface** turns the exact surface into one sculptable mesh for Blender's Sculpt Mode;
  **Refit Patches** writes the result back into only the patches you touched.
* **Rig: the Boner sub-panel**, named after Relic's tool, with three tabs.
  **Bones**: pick a bone (or click one with the eyedropper), rename it, **Add Bone under Active**, delete or re-parent
  bones with Blender's own **X** and **Ctrl+P** in the rig's Edit Mode, skin control points (**Assign Selected**,
  **Select Points**, **Remove from Bone**, **Select Unassigned**, **Auto Skin**), joint limits and IK chains.
  **Limbs**: rename, set the class boxes, add a standard limb, delete, list and set a limb's bones.
  **Connections**: the rings where limbs attach; create, rename, delete, set classes and the host bone, **Pick Ring**,
  **Add Stub Socket**, **Optimise Start**.
* **Animate.** Every clip is an Action: play it with **Load Animation**, edit its keys in the Dope Sheet or Graph Editor,
  rename, delete or make new Actions. Export keeps the clips you did not touch byte for byte and writes the rest.
* **Texture.** **Paint Skin** repaints the creature's own textures (only repainted ones are written again);
  **Edit UVs** / **Apply UVs** edit each patch's corner UVs; **Transfer Texture** bakes another mesh's image onto the
  creature.
* **Check.** **Validate (will it load?)** sorts what it finds into problems that stop the game's tools from loading the
  file, gaps against what every stock creature has, warnings, and combine findings from the game's combiner rules
  (**Combine Checks: Quick** or **Off**). A **Problems** box in the SGM panel, and red text in the viewport, show a
  mistake (a moved object, an added modifier, a deleted rig) the moment you make it, in the words Export would use.
  **Combine Preview** combines the creature with another one the way the game's combiner does and draws the result
  beside it; **Materialize...** writes that combination as a creature of its own.
* **Render.** The surface is drawn over the viewport, so for Eevee or Cycles use **Bake Textured Mesh**, or turn on
  **Display > Evaluated Surface**, which builds the same surface as real geometry (slower to edit with).
* **Export.** **Export SGM (Native Patches)** in the SGM tab, or **File > Export > Impossible Creatures Patch Mesh
  (.sgm)**. The report line says what changed, or `byte-identical to the source`.

### What the game does that the viewport does not show

* **Battle.** In a battle the game cuts a creature into triangles once, in its rest pose, and each triangle corner
  follows only the bones of its patch's four corner points. The add-on's animation (like the game's army builder) moves
  every control point with its own bone. Every stock creature gives each handle the bone of its nearest corner, which
  makes the two agree; Auto Skin does the same by default (**Handles Follow Corners**), and Validate warns about a
  handle on another bone.
* **Limits the game has.** At most 12 patch corners may meet at one point. A creature may have at most 12,288 display
  vertices, the points the game actually draws (16 to 25 for each patch; Validate counts them). Past that, a match at
  High detail ends with a fatal error. A combined creature is held to the same number, so to combine with every stock
  creature a torso should stay under 6,584 display vertices and the limbs under 7,879 (Validate warns above these).
  Validate refuses smoothing group 0, which makes the game write past the end of a buffer.

## Gatherers, characters, structures and props

These are triangle meshes, made of one or more parts, with an optional skeleton (a character's vertices carry bone
weights; a structure's parts each follow one bone), textures and clips.

* **Start a project from one of the game's own.** **File > New > Impossible Creatures Project...**, **Type**:
  **Gatherer / character** (9 on a Steam install: the henchmen and 8 characters) or **Structure / prop**
  (196: buildings and scenery), then pick it under **Start from**, or choose **Another .sgm file...** and pick it under
  **File to start from**. The list comes from the game folder (see [Install](#install)). The game cannot
  load a model of this kind built from nothing, so every project starts from an existing one: its skeleton, materials,
  blueprint and clips come with it. **Animations**: keep its own, or **None** (the export then drops them).
  The new `.sgm` and its blueprint (`.ebp`, same name) are written where you chose.
* **Or open one** with **File > Import > Impossible Creatures Model (.sgm / .ebp)**. Models show at full detail, with
  the game's textures read from its archives, the skeleton as an armature and the clips as Actions.
* **Edit the mesh any way you like** in Edit Mode: move, add and delete faces, subdivide, cut UV seams, shade faces
  flat, paint a character's bone weights, give faces another of the model's own materials. To add a piece, join it
  into an existing part (**Ctrl+J**); a separate new object is not exported, and a deleted part keeps its original
  shape in the file. Moving, turning or scaling a whole part in Object Mode is not exported.
* **Export** from the SGM tab: **Export Scene Object (.sgm)** writes the geometry and the clips (it needs the rig);
  **Export Scene Geometry (.sgm)** writes only the geometry. **File > Export > Impossible Creatures Scene Object
  (.sgm)** is the same as Export Scene Object. Only the parts you changed are rebuilt; every other part, the skeleton
  and the untouched clips keep their bytes, and the report says how many parts were rebuilt.
  The option **Let the engine rebuild the detail ladder** (on) writes rebuilt parts in the older layout, so the game
  makes the distance detail levels itself; off, a rebuilt part gets one full-detail level, which loads in the game's
  Object Editor but has not been tried in the game.
* **What the export checks.** A part may hold at most **12,288 vertices**, counted after the export splits it at UV
  seams and flat-shaded faces (the game draws no more in one part); a bigger part is refused, the message says how
  many it came to, and nothing is written. Reduce its detail (apply a Decimate modifier, or use fewer seams and flat
  faces). Every rebuilt part gets its bounding volumes the way the game builds them: its own box, one box per bone it
  is weighted to, and the model's overall box. A part with **damage states** (stored shapes the game blends into it,
  such as a building's damage) cannot be edited yet without breaking them, so an edited one is refused; tick **Drop
  the damage states of edited parts** in the export to write it without them (the report says how many were dropped).
  Parts you did not edit keep theirs byte for byte.
* **The blueprint** is edited in the Blueprint panel, below.

Under one of these models the SGM panel shows its name, kind, number of parts and vertices, the rig box when it has a
skeleton (clips, Load Animation, Select Rig) and the export buttons; the creature tools are not shown.

## The Blueprint panel

Every gatherer, character, structure and prop is a pair of files: the model (`.sgm`) and the blueprint (`.ebp`). The
**Blueprint** panel (3D Viewport sidebar, **SGM** tab, shown when a part, the rig or the group of such a model is
active) lets you see and change the blueprint and write both files into a folder that is ready to copy into the game.

1. **Load Blueprint** reads the `.ebp` beside the model. With no `.ebp` there, **Start a New Blueprint** makes one (it
   has no motion tree and no events, so the game will not play the model's animations with it; the panel says so).
2. The panel shows, each with a short explanation:
   * **Name**: the name the game shows. A stock name is a code such as `$25008` (the henchman) that the game looks up
     in its language files; type plain words to give your own name (at most 255 letters).
   * **Kind of thing**: the class number with its meaning, such as *3 - Henchman: a unit that walks, fights or
     gathers*. It decides what the game makes of the model.
   * **Footprint and offset**: how many path cells a building blocks (X and Z; 0 and 0 lets the game measure the
     model), and how far the picture of the model sits from its footprint. Only buildings use the footprint; for other
     kinds it is greyed out.
   * **Sounds**: sound files the game loads before play starts (**Add Sound**, the X button removes one).
   * **Where the game finds it**: the folder under `Data/art/ebps`, and how the panel knew it (the thing the project
     was started from, a stock model the project is still an exact copy of, where the files lie, or a guess you should
     check).
   * **Gameplay numbers (read only)**, a closed sub-panel: speed, health, cost and the rest, with the stats file they
     come from, read from the game folder (the panel's status box names it). A copy saved under a new name gets the
     default numbers of its kind, because the game looks for a stats file of the new name; the panel says which file
     it used.
3. **Reset** throws your changes away and reads the file again. **Load Blueprint** never throws away changes you made
   in the panel.
4. **Export Entity Folder...** writes `<folder>/Data/art/ebps/<folder in the game>/<name>.sgm` and `<name>.ebp` (all in
   lower case) and `<folder>/README.txt`. The file name starts as the project's name and the folder in the game as the
   one shown in the panel; both can be changed in the dialog. The model is written exactly as the export buttons write
   it (with the clips when it has a rig), and its dialog has the same **Drop the damage states of edited parts**
   option. In the blueprint only what you changed is written again: an untouched
   blueprint comes out byte for byte, and a changed name, kind, footprint, offset or sound list changes only its own
   part of the file. Nothing is written into the game's own folder or any folder inside Steam's: the export refuses,
   and says why.

A few blueprints can only be looked at, not changed yet: some map markers and cut-scene props use an older file layout
(an export copies them unchanged), and the oldest scenery and a few markers carry their model inside the blueprint
(that kind cannot be exported at all yet).

> **Several players.** The kind, the footprint, the offset, the gameplay numbers and the size of the model are
> compared between players when a multiplayer game starts. Every player needs the same files, or the game will not
> let them play together.

## Maps and scenarios

A map (the game calls a map file a scenario, `.sgb`) opens in Blender, and can be edited and saved again.

* **File > Import > Impossible Creatures Map from the Game...** lists the game's own maps, read straight from the game
  folder: those packed in its archives and any map file in its `Data/scenarios` folder. Each reads like
  *2p faceoff (multiplayer, Engine.sga)*: the map, its kind (multiplayer, campaign, empty map) and where it is kept. A
  map that several versions of the game carry (the original, the expansion) is listed once for each.
* **File > Import > Impossible Creatures Map (.sgb)** opens a map file from disk.

Both have four options, all on: **Placed models** (every placed building, plant and unit shows its model, each model
loaded once; off: every placed thing is an empty, which is quicker), **Water**, **Creatures** (the creatures the map
places show as their real models; off: they stay empties) and **Game look** (the map as the game draws it, see below).
With all four off the import is as quick as it was in 0.5.0.

What you get, in a collection named after the map, with one collection per kind (terrain, water, structures, nature,
units, other):

* the **terrain** as one mesh at the map's real size and heights, with the ground textures repeated block by block the
  way the game repeats them (the images are packed into the `.blend` file);
* the **water**, a see-through surface wherever the map's water lies above the ground;
* every **placed thing** at its position and rotation: a copy of its model when the model can be shown, otherwise an
  empty named after its blueprint that carries the reason. Sound emitters and markers that have no model are empties;
* the **creatures** as real models: a stock creature is built from its own file, and a creature combined inside the map is
  built from the recipe the map holds (which animal gave the head, the legs, the tail), with the add-on's own combiner.
  Each kind is built once (in the pose it is modelled in, no animation) and every placed creature is a copy of it; each
  combined creature's collection says which animal gave which limb. A creature that cannot be built (a parent animal that
  is not in the game's files) stays an empty with the reason;
* the **game look**, which the save never reads: the map's own **ground picture** (`<map>.tga`, the image texture
  stamping paints into) with each block's detail texture blended over it within 450 m of the view, darker under water and
  fading to the border colour at the map's edge; the **sun** (its colour and the way its light travels) and **ambient
  light**; the **sky** on the game's flattened dome, seen only by the camera and following the scene's camera as the game's
  follows the eye; the **water**, on the map and out past its edge (800 m at the water level, then a ring rising and fading
  out): the sky's reflection, with the brightness the game gives each point from the depth and distance, times a ripple
  picture; the sloped **border** under it; the map's **decals** (the marks laid on the ground); and the **fog** (only a few
  maps have any). The new objects are in a collection `<map> look`; the ground and the map's water get their game
  materials while the look is on, and the scene's view transform is Standard;
* a **report** in the status bar and in a text in the file, `Map import <name>` (open it in the Text Editor): the
  size, the ground textures, how many things were placed and how many show a model, how many creatures were built, what the
  game look holds, what is an empty and why, and how long it took. On the developer's machine, with everything on, 2p faceoff (164 placed things) takes 0.6 s, mission1 (21 creatures) 1.9 s, mission12 (52 creatures) 2.9 s, and the largest map of the archives, 6p bikini atoll (1,024 by 1,024 m, 1,786 placed things, 1,903 decals), 1.3 s.

The import also sets the 3D view to draw far enough to show the whole map. Each placed object remembers its blueprint,
its owner (player) and its groups; the save reads them back.

The game look can be shown or removed later with **Show game look** / **Remove game look** in the Map panel (removing it
shows each block's ground texture plainly again, which helps when you paint blocks). The rules are the game's, read from its
code; what differs: the Blender sun lights the ground (the game computes its own light and cast shadows per ground point),
the water does not move, colours are added in Blender's linear light so tones differ a little, and the sun's strength and the
fog's thickness are the add-on's choice. A viewport that is not a camera sees the sky centred on the map. A map's title in
the game comes from the language files, so the add-on names a map after its file.

### Editing and saving a map

Sculpt the terrain or move its points (the points stay in their grid), move the water surface to change the water level,
give faces a ground material (Add ground texture lists the game's own), and move, turn, delete or copy (Shift+D) the
placed objects. Add object puts a blueprint from the game's list at the 3D cursor; Set owner and Drop to ground work on
the selected ones. The **Map** panel (3D View sidebar, **SGM** tab, shown when the scene holds an imported map) holds
these. File > Export > Impossible Creatures Map writes the map into a folder you pick (never the game's own folder) with
the files that belong to it; "Ready-to-drop folder" writes `Data/scenarios/mp/<name>.sgb` and a README. The save report
says how many heights were rounded or cut to the map's steps and whether the pathfinding was updated. A map saved with
no changes is byte for byte the original. **Not tried in the game.**

In more detail:

* **The ground.** Sculpt it (Sculpt Mode) or move its points (Edit Mode, proportional editing works). The save reads the
  height of every point. The terrain must keep its grid: the map stores one height for each point, so points cannot be
  added, removed, joined or rearranged. If they were, the save is refused with the reason and nothing is written (Undo,
  or open the map again). A point pushed sideways keeps only its height. The map stores a height in steps (0.5 m in most
  of the game's maps, 0.4 m in `mission1`), so a sculpted height is rounded to the nearest step, and one above the
  highest the map can hold (255 steps, 127.5 m in a 0.5 m map) or below zero is cut to that end; the report counts both.
* **The water.** Move the water surface up or down and the map's water level follows (the sea moves with it; a pond at
  another height stays where it was). The water surface is drawn again by itself after you sculpt the ground, on maps up
  to 257 x 257 points; press **Redraw water** (Map panel, *Ground and water*) after changing a bigger map or the water
  level. A map opened without water can be given some: **Add water...** puts a surface at the level you pick, and saving
  writes that level as the map's water level (the water shows in the game wherever the ground is lower than that level).
* **Ground textures.** The panel's *Ground textures* part lists the map's ground materials. In Edit Mode select faces and
  press **Use on selected faces**; **Add ground texture...** searches the game's own ground textures and adds one. The
  game keeps one texture for each block of 8 by 8 cells, so each block takes the material most of its faces have, and a
  tie leaves the block as it was.
* **Placed things.** Move or turn an object and that placed thing moves. Delete one and it is removed from the map. Copy
  one (Shift+D or Alt+D) and the copy is a new placed thing with the same blueprint. **Add object at the 3D cursor...**
  searches the blueprints of the game (over 200) and puts the thing on the ground under the cursor, with its model when
  it has one. **Set owner of the selected...** gives the selected things to the world or to a player. **Drop selected to
  the ground** puts them on the terrain. A thing you did not touch is written back with the numbers it had.
* **Check my changes** lists what a save would change, without saving.
* **Saving.** File > Export > Impossible Creatures Map (or **Save Map...** in the panel) writes `<name>.sgb` into the
  folder you pick, and next to it the files that sit beside the map in the game and carry its name (its triggers,
  dialogue, preview pictures and loading-screen files), copied unchanged. **Save ready-to-drop folder...** writes
  `Data/scenarios/mp/<name>.sgb` (`sp` for a campaign map) with the same files and a `README.txt` that says how to put
  it into a copy of the game. The save is refused, with nothing written, for the game's own folder, for any Steam
  folder, and for the file the map was opened from unless **Replace the original file** is ticked. The status bar gives
  the result; the whole report is in the text `Map save <name>` (open it in the Text Editor): *Changes read from the
  scene*, and *Written into the map* (how many heights were rounded or cut, whether the pathfinding was updated, new
  ground textures, ids removed, and anything to be careful about).
* **Pathfinding.** The map holds where units can walk, baked from the ground, the cells the map's author painted passable
  or blocked in the game's editor, and the scenery that blocks. Since 0.6.0 the add-on bakes it **the way the game's own
  editor does** (the method was read from the game's program, and checked against the game's own maps). The save dialog's
  **Pathfinding** choice says what to do after the ground or the scenery changed. **Update when needed** (the default)
  changes only the cells where the ground or a blocking object changed, and the rest of the grid stays as the game baked it;
  the sector map the computer players path over is built again from the result. **Rebuild like the game** builds all of it
  again from the ground, the painted cells and the blueprints that block (their own files say which do). **Leave as it is**
  does not touch it: units then walk as on the old ground. Measured on the game's 92 maps that carry pathfinding, the add-on's
  rebuild gives the stored cell map byte for byte on 91, and on the 92nd (`mission1`) 52 of its 65,536 cells differ (steep
  shore cells); the precise map and the sector map from the stored cell map match the game's on all 92. The 0.5.0 estimate
  matched 79 to 99.7 of every 100 cells. A map that has no pathfinding at all is not a problem for the game (it bakes one when
  the map loads, without the painted cells), but the add-on never writes a partial one. **Not tried in the game.**

What a save does not do: the scale of an object is ignored (the map stores a position and a turn only); a copy of a
creature that was combined inside the map is left out and the report says so (such a creature has no blueprint file). A map saved under
a new name gets new file names; the triggers keep their old text (only a comment names the old map). Since 0.7.0 the map's
settings, look, groups, triggers and more can be edited: see the next section.

### Making a map and the Mission Editor's features (0.7.0)

0.7.0 brings most of the Mission Editor's tools to the Map panel (the 3D View sidebar, **SGM** tab). Every part saves only what
you changed: a map you only opened and saved still comes back byte for byte, with every file beside it. **None of this has been
tried in the game yet.** Where a value is stored is measured on the game's own maps; the notes under each heading say what is
proven and what is not.

**A new map.** **File > New > Impossible Creatures Map** starts from the Mission Editor's own 15 terrain templates (Arctic,
Desert, Savannah, Swamp, Tropical, each Small, Medium or Large: 64, 128 or 256 cells a side), with a name, 2 to 6 players and
the switch **Give each player a start** (a lab and three henchmen each, the same as every stock start). It needs the game folder
(the templates are the game's own files). A map made this way has no file on disk: its bytes live in the `.blend`, and saving
it writes the map and the files a playable map needs (ground picture, minimap, icon, dialogue, an empty trigger file and the
animation file). The game lists a multiplayer map only when it is made for 2 to 6 players, so a template's own 0 players is
never listed until you set the players.

**Map settings.** The map's name (the text the game's map list shows), its kind (multiplayer or single player: the save puts it
in `Data/scenarios/mp` or `sp` to match) and the number of players; a row per player with a button that puts a start down for
it. **Make preview picture** writes the icon (128 x 128) and minimap (256 x 256) the map list draws: the water as the editor
draws it on the ground (measured: 99.9 percent of the pixels of the stock minimaps agree on where the water is); a second
button renders the ground from Blender instead. **Terrain look** edits the sunlight and shadow colours, the time of day (the
sun's direction), the sky picture, the sea colour (kept for the Mission Editor: the game does not read it) and the three water
brightness values, the fog (on or off, start, end, colour) and the
detail texture repeat. The look in the 3D view follows at once, **Back to the map's values** undoes it, and only a value that
differs from the map's own is written. **Map problems** (**Check map**) lists what is wrong before you save: a player with no
start, a start on blocked ground, a trigger that names an object you deleted, and so on; **Locate** selects the object a
problem names. The problems are also listed in the save report; nothing is refused for them.

*Not proven:* that the game lists and loads a map made this way (the rules come from the game's program; the template boards are
stock). A missing trigger, dialogue or animation file is thought harmless (inferred). The sea colour record is added to a map
that has none (46 of the 93 stock maps have none) on trust: nothing in the game's program reads it that was found.

**Painting.** *Passability* (the Mission Editor's Edit Impass Map): paint blocked or open cells with Texture Paint (a picture
with one pixel per path cell), or select terrain faces and press Block, Open or Clear. It is saved into the map's passability
layer and the pathfinding is updated the way the game builds it (checked: the patched records equal the game's own rebuild
byte for byte). *Overlays* draws the pathfinding as the editor's Overlay menu does (land, water, amphibious, land and water,
placed objects, can't build, sectors, regions, the precise map); an overlay is made from the map as it would be saved now,
is never saved, and needs pressing again after you paint. *Terrain types* (grass, snow, dirt and sand, rock) are painted with
Vertex Paint or set on selected points; they decide the footstep sounds. *Water levels* puts water at its own height on selected
points (a lake on a mountain), dries points, and updates the pathfinding. *Decals*: **Edit decals** makes a handle for every
decal to move, turn, scale or delete, and **Add decal at the 3D cursor** adds one. *Blend tiles* lists the game's ready-made
edges between this map's ground textures. *Texture stamping* paints a decal picture into the map's ground picture (`<name>.tga`)
at the 3D cursor, with a size (a stamp is as big as a decal of the same size), a turn, a strength, a tint and a row of stamps.
A ground picture stamped with 0.8.0 or earlier has its stamps mirrored north to south in the game. **Limit:** the game draws a stamp only at its
highest ground detail setting, because at the lower ones it draws the `_decaled` pictures, which the add-on cannot regenerate
(how they are made is not known). Real paint strokes have not been exercised by the tests (they write pixels and colours as a
stroke does); terrain types 5 and 6, which stock maps hold, are kept and shown as numbers (the game names only 0 to 4).

**Scripting.** *Groups and variables*: groups are made from the selected objects, renamed, added to, removed from and deleted;
the 256 variable names are renamed (the count is fixed). A group made from an object you added in this session has to wait
until the map has been saved and opened again. *Triggers* (the Trigger Wizard): folders, triggers, and for each its who-runs-it
player, active and repeating switches, a list of conditions and a list of actions. **+** searches all 75 conditions and 206
actions by their plain names with the manual's one-line description, and each argument is a field of its own kind: a group, a
variable, a trigger or a dialogue picked by name; an object picked from the selected 3D object; a player; a comparison; yes or
no; a sound, music, movie or text line picked from the map's text table (or a new one added); a creature type from the map's
blueprint list or any blueprint of the game; plain numbers for the rest. *Dialog triggers* edits the `.dlg`. *AI scripts and
armies* sets a path for each player slot. **Check the scripts** finds a trigger that names a deleted object, group or text.
Groups, variable names, the text table and the blueprint list are saved into the map file, triggers into `<name>.trg`, dialogues
into `<name>.dlg`, each only when you changed it. *Not covered:* the Script Editor (single commands in the running editor);
options whose lists of named choices were never decoded (key codes, research, decal, unit commands, stances, abilities,
animations) are plain numbers.

**Cinematics.** The map's camera, object and move animations (NISlets, the `<map>.na` file beside the map) open as one
collection per animation with an empty for every key frame: move it, turn its arrows, change its speed, tension, continuity,
bias, blend times and look values in the panel; add, delete and reorder key frames; copy the view from a camera, aim a key at a
selected object (a **target**), **Preview** the camera, **Show speed**, and set the cinematic 1.85 picture. New animations take
the next free number, and the file is rewritten only for the animations you changed, in the game's own layout. The preview path
is a plain curve through the key frames: how tension, continuity and bias shape the game's path was not decoded, the values are
saved exactly (the **Replay** below uses the game's own path code for how far along the path a camera or object is at each moment, so its timing is the game's even though the drawn curve here is not). A camera that looks at a group does not aim at the group's centre in the preview. The Mission Editor's Orbit Target,
Third person view and Path Placement Mode are not there (edit the key empties and press **Update path**).

**Replay: watch a cinematic the way the game plays it (0.8.0).** The game plays a cinematic by running the map's trigger script
(`<name>.trg`) together with the simulation that carries out its orders: units walk, a lab flies, villagers vanish, the camera cuts
from one animation to the next, the screen fades. The Cinematics part above shows only the camera, object and move animations of the
`.na` file; the **Replay** part (inside Cinematics, closed until you open it) acts the whole cinematic out on Blender's timeline.
Press **Find the cinematics**: the list holds every trigger folder of the script that plays a camera, object or move animation, a
cut-scene (NIS) or a cut-scene snippet (NISlet), by the folder's name ("New lab intro", "Camera Intro Rex Lucy"). Click one,
press **Build replay** (it reads the triggers and the cinematics as the editors hold them at that moment, so an edit made after Find is in the replay), press **Look through the camera**, then **Space** in the 3D view. The scene's frame rate becomes 30 and its
frame range the length of the cinematic (frame = 1 + seconds x 30); **Clear replay** puts back the frame rate, the range, the scene
camera and the frame you were on. **Event list** opens the text *Replay <name>*: every event with its time, what it does, whether it
is exact, approximate or skipped, why, and how the replay shows it.

*What you see.* A collection `<map> replay` (outside the map's own collection) holds a stand-in for every object the cinematic
touches (an instance of the same model the placed object uses; the placed objects themselves are hidden in the view while the
replay is shown and are never moved), one **Replay camera**, timeline markers that switch the scene camera, a black plane for the
fades and, for cut-scenes, their own stand-ins and cameras. Open the map **with its models** if you want the labs, characters and
props to play their clips (Fly, walk, die ...); the creatures a map places are still unposed meshes, so they get the clip name keyed
as a custom property `replay_anim` and nothing more. **A replay never changes the map**: saving with a replay shown, or after
clearing it, writes exactly the bytes a save without one writes (the replay carries no `ic_*` property the save reads).

*Exact* (the script fixes the outcome): teleports and MoveGroup to a place, despawn, respawn and delete, entity visibility, which
way a unit is turned (face / look at a group, at the game's 180 degrees a second), entity animations (PlayAnimation names a motion
node of the blueprint; the replay plays the clip of its last non-idle leaf, looping as the node says: that choice is inferred),
motion variables (keyed as `mv_<name>`), move animations (an object following a `.na` path), the screen fade (a black plane, alpha
1 is black), and the cut-scene objects' own animation and camera (a cut-scene plays its whole animation, the length read from its
model, its cuts come from the model's `setcamera` texts). Every row of the event list says so for itself: an event whose time
depends on a speech, a random countdown or a walk is marked approximate even when what it does is fixed. *Approximate*
(the simulation decides): **walking** (an order to a place or "move / attack-move / guard / garrison" is drawn as a walk along a route
found on the map's own cell grid, around what blocks the unit's kind, at the unit's speed from its stats script, 21 km/h when
the unit has none or is combined inside the map; the real pathfinder and the spacing between units are not run), every time that
follows a **speech** (the length of a line is estimated from its text, 14 characters a second plus 0.6 s, unless the script gives
it), a **random countdown** (the middle of its range), the camera's first blend (where the game's own camera was is unknown), and
every turn of a unit that has walked (where it stood depends on the walk), and several units sent to one spot, which overlap. The camera paths use the game's own path code (the
position spline, the speed curve, blend in, hold and blend out), integrated at 1/32 s. *Skipped* (listed, not drawn): groups
filled at run time from the player's own units (Get entity group, UpdateProximityGroup), random wandering, command 17, push / pop
camera setting and effects (FX create). *Listed with a marker, not drawn:* the dialogue (no sound, no text), cinematic mode on and
off, the camera's focus on an entity and decals.

*Which branch of the script?* The replay starts at the first evaluation of the cinematic's starting triggers. Of those, the one
that plays an animation is assumed true (a flag nothing in its reach sets, a group near a place, a group's health, an objective)
and every other condition on the rest of the game is assumed never true; keypress conditions are never true, so a script that waits
for a key ends 2 s after the last thing that moves (never past 900 s). The assumptions are listed in the event list's header notes.
The panel cannot change them (the module function takes `assume=`).

Not covered: the sound, the dialogue text, the game camera before and after the cinematic, the lights, effects and morph channels of
cut-scene models, and a UseAnimationCamera on a model that has no camera (the game looks from behind its head bone). The Event list
and Look-through buttons were driven by the tests as operators only; no window was opened.

**Files and lists.** **File > Import / Export > Height Map (.tga)** (the editor's own kind of picture: grey is the height; the
picture is one pixel per ground point by default, so it comes back exactly; a picture of another size is stretched corner to
corner), **Terrain (.ter)** and **Entities (.ent)**: the Mission Editor's own file formats, read from its program. Importing a
terrain of the same size replaces the map's ground, sky, fog and water settings, rebuilds the pathfinding and reports the
objects that now float or sit under the ground; importing entities adds new placed objects (at the 3D cursor if you like). A
creature combined inside a map cannot go into a `.ent` (its recipe writer was not decoded) and is left out with a note. The
**Object list** filters the placed objects (by player, selected, nature, statics, stock, henchmen, resources, labs, sound
emitters, markers, a name) and selects, hides or deletes the ticked rows; **Unhide All** brings them back. Delete works only on
ticked rows. The categories are our guess from the blueprint folders: the editor's program has no such code. The **Blueprint
list** shows each blueprint's network number, how many objects use it and how many trigger and dialogue arguments name it.

**Terrain crop.** Crops the map to 1/2, 1/4 or 1/8 of its side around the 3D cursor or a typed centre (the size rule is the
editor's: `(points - 1) / N + 1`, at least 16 cells; the square snaps to whole 8 by 8 blocks and stays inside the map).
**Validate** draws the square, selects the objects that would be deleted and lists what would break (triggers and dialogues
that name a deleted object, groups that would be emptied, animation key frames outside the square, a player left without its
start); both work on the map as a save would write it now, so paint, trigger, cinematic and settings edits you have not saved yet are
in the crop, the validation and the `.ter` file; **Crop** is refused while an error stands unless **Crop anyway** is ticked. The cropped map is built as a new map in the
scene (the old one stays, hidden); the pathfinding is rebuilt, the decals, animations, groups and unit lists are moved or
trimmed, and the files beside it are kept (the `.trg`, `.dlg`, `.na`); pictures made for the old size are dropped and named in
the report.

*What the Mission Editor has that is still not here:* the Script Editor; pickers for the engine's named choices (keys, research,
abilities, stances, animations); the exact preview of a camera path and Orbit Target / Third person view; regenerating the
`_decaled` ground pictures after stamping; exporting a combined creature in an entity file; a crop square that reaches outside
the map; the editor's dropdown of stock creatures in the Object list; AI Scripts and Armies beyond a path for each slot; maps for
more than 6 players.

## Trying your work in the game

* **Use a copy of the game that you own, never the Steam folder.** Steam can overwrite or delete files that are not its
  own. Copy the exported `Data` folder into the main folder of your copy of the game (the one that already has a `Data`
  folder) and let the two folders merge. The game reads loose files in its `Data` folder before the files packed in its
  archives, so your files are used instead of a packed file of the same path. To undo it, delete them again.
* **New name or replacement.** A thing under a new name shows up only where a map or a script asks for it. To change a
  stock thing everywhere, export it under exactly its own folder and name (`structures/lab`).
* **When something does not show up,** the file `warnings.log` in the game folder says why.
* **Creatures:** the add-on writes the `.sgm` only. A playable creature also needs its `.ebp`, `.lua` and `.lsc`; those
  are made outside the add-on (the `ICModManager` tools of this project). After a creature has been in a game once, the
  game keeps drawing its old shape from its mesh cache (`<game folder>/Cache/...`) until that entry is deleted;
  ICModManager's installers delete it, and you must delete it yourself when you copy a file in by hand.
* A saved map goes the same way: copy the **ready-to-drop folder's** `Data` folder into a copy of the game you own.
  The game should list the map, since it reads loose files before its archives, but this has not been tried yet.
* Nothing the add-on exports has been played in the game yet. The developer tests load the creature exports in the
  game's own Object Editor; gatherer and structure exports with new topology, the blueprint exports and the saved
  maps have only been checked offline.

## What is not done yet

For gatherers, characters, structures and props:

* Material, texture and team-colour changes are not exported (faces may move to another of the model's own materials;
  nothing else about materials is written).
* Bone edits (moving, renaming, adding or deleting bones) are not written back, and the export does not warn about
  them. Renaming a bone of a character also renames its vertex group, which the export then no longer matches to a
  bone of the file: a vertex weighted only to that bone goes to the root bone.
* Attachment points and damage states are not shown, and damage states cannot be edited: an edited part with damage
  states is refused, or written without them when you ask (see Export above).
* A part cannot be added, so a part above 12,288 vertices has to lose detail; splitting it into two parts is not
  possible yet.
* There is no checks panel yet.
* There is no button that installs into the game: **Export Entity Folder** writes a folder you copy yourself into a
  copy of the game that you own.
* The three export problems of 0.3.1 are fixed in 0.4.0: a part above 12,288 vertices is refused (0.3.1 allowed
  65,535), the bounding volumes are rebuilt the way the game builds them (0.3.1 set them to one box, or left the
  model's overall box at the original's), and an edited part with damage states is refused or written without them
  (0.3.1 kept damage-state data that no longer fitted the mesh). None of this has been tried in the game yet.

For maps:

* A saved map has not been loaded in the game yet. Heights are stored in steps of 0.5 m, and the pathfinding the save
  updates or rebuilds is made the way the game's own editor makes it, which has been checked against the game's maps
  but not in the game (see *Editing and saving a map*).
* The terrain keeps its grid, so points cannot be added or removed.
* Scale is ignored and a copy of a creature combined inside the map is left out.
* The Mission Editor features of 0.7.0 (a new map, painting, scripting, cinematics, files, lists, crop) have only been checked
  by numbers: counts, bytes, files read back. Nothing has been tried in the game or in the Mission Editor, and no paint stroke
  has been made by hand in a test (the tests write the pixels a stroke writes). The Replay (0.8.0) is checked by numbers too: positions
  at given frames, counts, timings and the saved bytes; no one has compared it with the game by eye, and the Event list and
  Look-through buttons were driven as operators, never in a window. What is still not there is listed at the end of
  *Making a map and the Mission Editor's features*.
* Creatures on a map are shown in the pose they are modelled in (no animation), and a combined creature's recipe is read as
  the game's own limb list describes it, which was checked on the maps' data but not by looking at the game. The sun's
  strength and the fog's thickness in the game look are the add-on's choice. The game look follows the game's code but has
  not been compared with the running game; it leaves out the game's per-point ground light and cast shadows and the moving
  water.
* A new map made by New IC Project has the placeholder water colour of the older add-on, not the editor's default.

For creatures:

* Per-patch material IDs and smoothing groups are kept from the file but cannot be edited; new patches get smoothing
  group 1.
* There is no bevel, Add Tri, Detach or Reset Handles.
* **Convert Mesh to Creature** puts every point on one bone and every patch in one limb (**Fit Patches to Mesh** does not).
* **Transfer Texture** gives every patch its own texture area, which raises the patch's display-vertex cost from 16 to
  25; its report does not say so (Validate does).
* There is no preview of the battle skinning described above.

Everywhere: only Linux has been tested.

## Reporting a problem

Please include:

* the Blender version (the Blender menu at the top left, **About Blender**), the add-on version (**Edit > Preferences >
  Add-ons**, SGM Native Patches) and your operating system;
* what you started from (a stock model's name, or the `.sgm` / `.ebp` file itself) and what you did, step by step;
* the exact message from the status bar or the **Info** editor (the add-on writes every report there), and anything
  printed in Blender's console (start Blender from a terminal on Linux; **Window > Toggle System Console** on Windows);
* anything listed under *Parts of the add-on that could not be loaded* on the add-on's preferences page;
* for a problem in the game, the game's `warnings.log`.

Send it to the maintainer named in the add-on's details (**Edit > Preferences > Add-ons**, SGM Native Patches).

## For developers

The add-on lives in the `pyIC` repository next to the format library `ICParse`, the combiner `pyRDN/combotest` and the
mod tools `ICModManager`. Start with these:

* [STATUS.md](STATUS.md): what has been verified, with the numbers and the tests that hold them, and the known gaps;
* [DESIGN.md](DESIGN.md): how the add-on works inside, module by module;
* [ROADMAP_NATIVE.md](ROADMAP_NATIVE.md): what comes next, in order;
* [GMAX_PARITY.md](GMAX_PARITY.md): the creature tools scored feature by feature against gmax and Relic's Boner;
* [docs/sceneobj/README.md](docs/sceneobj/README.md): how to add a module to the package that holds the gatherer,
  structure and blueprint tools;
* [docs/guide/](docs/guide/index.html): the user guide (built from `docs/guide/chapters/` by `docs/guide/build_guide.py`).

**Working on the add-on itself.** From the repository root, after `uv sync`:

```bash
blender --python blender_native/tools/dev_launch.py -- Assets/stock/snail.sgm   # Blender with the add-on and a creature loaded
```

`dev_launch.py` registers the add-on straight from the repository for that one session (nothing is installed and no
preference is saved). Blender's **Install from Disk** takes only a `.zip` or a `.py`, so to try the packaged add-on
build the zip (below). `__init__._setup_paths()` finds the dependencies when they do not already import (an installed
extension's wheels always win):

| Dependency | Needed for | Looked for in |
|---|---|---|
| `construct` | everything (ICParse's byte layouts) | the bundled `_deps/` |
| `ICParse` | everything (the file formats) | `$BZ_ICPARSE`, else the sibling `../ICParse` |
| `combotest` | Combine Preview and the combine checks (optional) | `$BZ_COMBOTEST`, else `../pyRDN/combotest` |
| `lupa` | combined ratings, cost and rank (optional) | `$BZ_DEPS_BIN`, else `_deps_bin/<cpXY-platform>/` (filled by `tools/fetch_blender_deps.py`) |

ICParse and the combiner are not reloaded with the add-on: restart Blender after editing them.

**Building the zip.**

```bash
.venv/bin/python3 blender_native/tools/build_extension.py --version 0.8.1      # -> blender_native/dist/sgm_native_patches-0.8.1.zip
```

It builds ICParse and combotest into wheels with `uv build`, downloads `construct` and the `lupa` wheels from PyPI
(checked against the sha256 PyPI publishes and cached in `dist/_wheels`), writes the manifest (minimum Blender 5.3.0,
taken from `bl_info`) and lets Blender pack it. The ICParse and combotest wheels get a version label made from their
contents (`icparse-0.1.0+h490241a757bd` in 0.6.0), so that a newer zip installed over an older one replaces them. The
0.6.0 zip is 8.5 MB: the add-on's 49 Python modules, the README, the bundled copy of `construct` and 7 wheels.
`--no-lupa` / `--no-combotest` leave those out; `--output-dir` puts the zip elsewhere; without `--version` the zip
takes the version in `bl_info` (0.8.1). Installing it headlessly into a throw-away profile:

```bash
BLENDER_USER_RESOURCES=$(mktemp -d) blender --command extension install-file -r user_default -e sgm_native_patches-0.8.1.zip
```

**Tests.** `tests/run_all.py` runs 77 test scripts headlessly (the pure-Python ones in the repository's Python, the rest in a background
Blender); `-k <text>` picks some, `--list` lists them. The heavier tiers (`--combine-full`, and `--engine` / `--parity`,
which drive the game's own Object Editor and ComboTest.exe under Proton) are for the developer machine.
`tools/so_sweep.py --identity` starts a project from each of the 205 stock gatherers, characters, structures and props
and checks the untouched export is byte-identical. `tools/map_sweep.py` saves every stock map of the game untouched (the map file and every file beside it must come back byte for byte), `tools/map_edits_07.py` makes one edit of each new kind of the Map panel and a new map from each template, and `tools/map_roundtrip.py` does the older check for maps: eight stock maps (everything on and off) saved
untouched (byte-identical) and with every kind of edit, read back and opened again. STATUS.md says what each test proves
and what the last runs gave.

## Licence

GNU General Public License, version 3 or later (`LICENSE`), as Blender asks of add-ons. `_deps/construct` is a copy of
[construct](https://github.com/construct/construct) under its own MIT licence (`_deps/construct-2.10.70.dist-info/LICENSE`);
the released zip also carries construct and lupa (MIT) as wheels.

This is a fan-made modding tool for *Impossible Creatures* (Relic Entertainment, 2003). It contains none of the game's files:
you need your own copy of the game. It is not affiliated with or endorsed by the game's owners.

The repository holds the add-on only. Building the zip from source also needs two libraries that travel inside the released
zip and are not part of this repository.
