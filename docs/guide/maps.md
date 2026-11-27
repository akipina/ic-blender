<!-- Written by build_guide.py from chapters/maps.html: edit that file, not this one. -->

[Contents](README.md) · Previous: [The Blueprint panel](blueprint.md)

# Maps and scenarios

Open a map (`.sgb`), change its ground, water, textures and placed things, and save it. You can also start new maps, paint, script and replay cinematics.

## Opening a map

1. Use **File > Import > Impossible Creatures Map from the Game...** for the game's maps, or **Impossible Creatures Map (.sgb)** for a file.
2. Four options start on: **Placed models**, **Water**, **Creatures** and **Game look**. Turn them off for a quicker import.

If the game isn't found, set the **Game folder** in the add-on's preferences.

## What you get

- A collection named after the map, sorted into terrain, water, structures, nature, units and other.
- **Terrain**: one mesh at real size, with its ground textures.
- **Water**: a surface wherever water lies above the ground.
- **Placed things**: models, or named empties where no model can be shown. Each keeps its blueprint, owner and groups.
- **Creatures**: real models, including combined ones, without animation.
- **Game look**: sun, sky, ground picture, water, border, decals and fog. It is never saved. Toggle it with **Show game look** / **Remove game look**; remove it to see ground textures plainly.

## The report

The status bar sums up the import. The text *Map import &lt;name>* in the Text Editor lists what was placed and why anything is an empty.

## The Map panel

In the sidebar's **IC Map** tab (<kbd>N</kbd>), the **Map** panel shows the map's size, object counts and source, with a folding part for each tool.

## Changing the ground and the water

1. **Ground:** sculpt it, or move its points in Edit Mode. Only heights are saved. Don't add, remove, join or rearrange points: the save is refused. Undo or reopen the map.
2. **Steps:** heights are rounded to steps (usually 0.5 m, sometimes 0.4 m) and limited to 0-255 steps. The save report counts changes.
3. **Water:** move the water surface up or down; ponds with their own height stay. Use **Add water...** if there is none; saving then sets the map's water level. Press **Redraw water** after moving it, or after sculpting a map larger than 257 x 257 points.

## Painting ground textures

In **Ground textures**, select terrain faces in Edit Mode and press a material's **Use on selected faces**. **Add ground texture...** adds one from the game. Each block of 8 x 8 cells takes the texture most of its faces have; a tie changes nothing.

## Moving, copying, adding and deleting placed things

- Move (<kbd>G</kbd>), turn (<kbd>R</kbd>) or delete (<kbd>X</kbd>) objects as usual.
- Copy with <kbd>Shift</kbd>+<kbd>D</kbd> or <kbd>Alt</kbd>+<kbd>D</kbd>.
- **Add object at the 3D cursor...** places a game blueprint.
- Use **Set owner of the selected...** and **Drop selected to the ground** as needed.
- Scale is ignored.
- Copies of creatures combined inside the map are left out.

## Checking and saving

1. In **Save**, **Check my changes** lists what a save would change.
2. **Save Map...**, or **File > Export > Impossible Creatures Map (.sgb)**, writes the map and its companion files (triggers, dialogue, pictures) to a folder.
3. **Save ready-to-drop folder...** writes `Data/scenarios/mp` (or `sp`) with a `README.txt` on installing into a copy of the game. The file name is made lower case and every other character becomes an underscore (`My Map` becomes `my_map`).

- Game and Steam folders are refused. So is the original file, unless you tick **Replace the original file**.
- Read the text *Map save &lt;name>* for cautions.

## The pathfinding

The map stores where units can walk. Choose **Pathfinding** in the save dialog:

- **Update when needed** (default): updates where ground or blocking objects changed.
- **Rebuild like the game**: rebuilds all of it.
- **Leave as it is**: units walk as on the old ground.

First test in game: can a unit walk across what you changed?

## Making a new map

**File > New > Impossible Creatures Map** asks for a name, ground, size, kind and players. Keep **Give each player a start** on for a lab and three henchmen each. The game folder is required.

- **Map settings** changes name, kind and players. **Make preview picture** makes the map list's pictures.
- **Terrain look** sets light, sky, sea, water brightness, fog and the fine ground texture. The game ignores the sea colour.
- Press **Check map** before saving. **Map problems** lists missing or blocked starts and broken script references; **Locate** selects one.

Save with **Save ready-to-drop folder...**. The game lists a multiplayer map only for 2 to 6 players.

## Painting the map

- **Passability:** **Paint blocked** (red) or **Paint open** (green), or select faces in Edit Mode and press **Block**, **Open** or **Clear**.
- **Overlays:** shows where units can go. Press the entry again to refresh. Not saved.
- **Terrain types** set footstep sounds. Paint them, or set selected points.
- **Water levels:** select points, then **Water at a height...** or **Dry the selected points**.
- **Decals:** **Edit decals (make handles)**, change the handles, then **Redraw decals on the ground**. **Add decal at the 3D cursor...** adds one.
- **Blend tiles** add soft edges between ground textures.
- **Texture stamping:** **Stamp at the 3D cursor...** paints a decal into the ground picture. The game shows stamps only at its highest ground detail.

## Scripting the map

- **Groups and variables:** select objects and press **+** to make a group.
- **Triggers:** make a trigger, say who runs it, and add conditions and actions with **+** (type to search).
- **Dialog triggers** and **AI scripts and armies** cover the other Mission Editor tools.
- **Check the scripts** finds references to deleted objects, groups or text.
- Saving writes your changes into the map and its `.trg` and `.dlg` files.

## Cinematics

In **Cinematics**, each animation is a collection with an empty per key frame. Move keys and change their values, then press **Preview** and drag the timeline. Add animations with **New Camera**, **New Object** or **New Move** (**New Camera Animation** on a map without cinematics). Triggers use the animation's number. Saving writes the `.na` file. The game's path may bend slightly differently.

## Watching a cinematic (Replay)

Replay acts out a cinematic with the map's trigger script.

1. Open **Cinematics > Replay** and press **Find the cinematics**.
2. Pick one, press **Build replay**, then **Look through the camera**.
3. Press <kbd>Space</kbd> in the 3D view.

- **Event list** marks each event *exact*, *approximate* or *skipped*.
- **Clear replay** brings back your objects, camera and frame range. A replay never changes the map.
- Import with **Placed models** on to see labs, characters and props animate. Placed creatures stay still.
- Walking, timing after speech, random countdowns and the camera's first blend are approximate.
- Effects, random wandering and groups filled from your units during play are skipped. Dialogue isn't drawn, and there is no sound.
- Conditions that depend on play are guessed: the trigger that starts the cinematic is taken as true, other conditions on the rest of the game (a key press, a unit dying) as never true, and random countdowns use the middle of their range. You can't change these choices yet.
- Replays haven't been compared with the game.

## Height maps, terrain and entity files, lists and crop

- **File > Import** and **Export** handle **Height Map (.tga)**, **Terrain (.ter)** and **Entities (.ent)**. A same-size terrain replaces ground, sky and sea values; entities are added.
- **Object list** filters placed things to select, hide or delete. **Blueprint list** counts how often each blueprint is used.
- **Terrain crop:** pick a size, place the 3D cursor and press **Validate** to see what would be lost. **Crop!** builds a smaller map and hides the old one. **Crop anyway** overrides problems.

---

[Contents](README.md) · Previous: [The Blueprint panel](blueprint.md)
