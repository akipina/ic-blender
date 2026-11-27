<!-- Written by build_guide.py from chapters/blueprint.html: edit that file, not this one. -->

[Contents](README.md) · Previous: [Bones, limbs and connections (Boner)](boner.md) · Next: [Maps and scenarios](maps.md)

# The Blueprint panel

The blueprint is the file that tells the game how a model behaves: its name, its kind, its animations, sounds, effects and numbers. This chapter explains the **Blueprint** panel, which shows and changes the blueprint of a structure, a prop, a worker or a character. (A creature's blueprint is changed through the creature panels. See [A new creature from a game animal](newcreature.md).) The panel is in the **IC Game** tab. It appears when a part, the rig or the group empty of the model is selected.

## Load the blueprint

1. Select the model and press **Load Blueprint**. The status box then shows what kind of thing it is and lists the changes you have made. **Reset** reads the file again and throws away your changes.
2. If the panel says there is no blueprint file beside the model, start the project from one of the game's things (**File > New > Impossible Creatures Project...**). That brings its blueprint, with its animations.

Some of the game's blueprints open read-only, with their fields greyed out. The status box says why:

- a blueprint in the game's oldest file layout can be looked at, and **Export Entity Folder** copies it unchanged;
- a blueprint that holds its model inside itself can be looked at, but not exported yet.

## What the panel shows

| Box | What it is |
| --- | --- |
| **Name** | The name the game shows, up to 255 letters. A code like `$41545` stands for a text in the game's language files. |
| **Kind of thing** | **Kind** and **Class number**: what the game makes of the model (a building, a worker, scenery, a shot). Changing this changes multiplayer. A number the game gives no behaviour makes a plain prop that only plays animations. |
| **Footprint and offset** | **X** and **Z**: how many path cells a building blocks (buildings only; each cell is 2 game units; 0 and 0 lets the game measure the model). **Calc from model** puts the measured numbers in. Stock buildings differ from it by a cell or so. **Offset** moves the model over its footprint (Y raises it). Changing these changes multiplayer. |
| **Sounds** | Sounds the game loads before play starts. Press **Add Sound**, type the path into the new row, for example `DATA:AUDIO\SPECIALFX\STRUCTURES\WOODBIND.PAT`, and use a row's **X** to remove it. Missing files are skipped silently. (To add a sound of your own for an animation moment, use [Effects, events and sounds](effects.md).) |
| **Icon** | Frame the model in the 3D view and press **Icon from this view**: the middle of the view becomes the 64 by 64 picture the blueprint holds. It is written when you export. **Remove New Icon** (the X) drops it. The button works from the sidebar of a 3D view. |
| **Where the game finds it** | The folder under `Data/art/ebps/`. The panel says how it found the folder. If it says “only a guess”, check it. Keep the original thing's folder to replace the original. |
| **Export** | The button **Export Entity Folder...**. |

The part **Gameplay numbers** shows the numbers the game gives this thing and lets you change them: [Gameplay numbers](numbers.md).

Export and install are the same for every kind of thing: [Check, export, install, remove and share](ship.md). The choices of the export window (a new name of your own, or replace the original) are in [Structures and props](structures.md), section “Check, export, install”.

---

[Contents](README.md) · Previous: [Bones, limbs and connections (Boner)](boner.md) · Next: [Maps and scenarios](maps.md)
