# IC Blender

IC Blender is a Blender add-on for modding *Impossible Creatures* (Relic Entertainment, 2003). It imports the game's creatures,
structures, units, props, effects and maps into Blender, lets you edit them with purpose-built tools, and exports them in the game's own
formats, ready to install.

This is an unofficial fan project. It is not affiliated with or endorsed by the owners of the game.

> **Status: alpha (0.9.2).** See [Known limitations](#known-limitations) before relying on it.

## Features

| Content | What you can do |
|---|---|
| **Creatures** | Start from any stock animal or from the built-in template. Reshape the body, repaint the skin, edit the skeleton, limbs and connection points, edit animations, and change gameplay numbers (hit points, speed, attacks, abilities) with rank and cost shown as you edit. Preview combinations with other animals. Export as a new creature or as a replacement for the original. |
| **Structures, gatherers, characters and props** | Edit meshes, add and remove parts, materials and textures (including team colour), bones, attachment points, damage states, animations, footprint and gameplay numbers. Export as a replacement for the original or under a new name. |
| **Animations** | Edit clips in Blender's Action Editor, add new clips, connect them to the game's motion states, set walk and run speeds, and borrow clips from another model with a matching skeleton. |
| **Effects, events and sounds** | Copy and retune the game's effects, attach effects and sounds to moments of an animation, create a sound from a WAV file, and preview effects in the viewport. |
| **Install and share** | Create a separate test copy of the game, install and remove content with full undo, pack an item as a zip for other players, and install zips made by others. |
| **Maps** | Open, edit and save maps, including terrain painting, placed objects, triggers and cut-scenes. Experimental. |

Only what you change is rewritten. Everything else stays exactly as it is in the game's files.

## Requirements

- Blender 5.3 or newer.
- A copy of *Impossible Creatures* (the Steam release). The add-on reads the game's files; it does not include any.
- About 0.5 GB of free disk space for the test copy of the game.
- Linux. Windows and macOS support is experimental.

## Installation

1. Download `ic_blender-<version>.zip` from the [latest release](https://github.com/akipina/ic_blender/releases/latest)
   (not the "Source code" archive).
2. In Blender, open **Edit > Preferences > Get Extensions**, open the menu at the top right and choose **Install from Disk...**.
   Select the zip.
3. In **Edit > Preferences > Add-ons**, confirm that **IC Blender** is enabled.
4. In the 3D Viewport, press **N** to open the sidebar. The add-on adds the tabs **IC Model** and **IC Game**, and **IC Map** when a
   map is open.

If the tabs do not appear, open **Edit > Preferences > Add-ons > IC Blender**; a box there lists any part of the add-on that failed
to load.

## Setup

1. **Game folder.** In **Edit > Preferences > Add-ons > IC Blender**, check **Game folder**. A Steam installation is usually found
   automatically; otherwise select the folder that contains `IC.exe`.
2. **Test copy.** In the sidebar, open **IC Game > Game > 3. Install into Test Game** and press **Create Test Game Copy**.
   The add-on installs content only into this copy, never into the original game, because Steam may overwrite files it did not install.
   The copy links to the original's large archives, so the original must stay in place (a full copy of about 5 GB is available as an
   option).

## Quick start

This replaces the stock wolf with an edited one. The [guide](docs/guide/replace.md) covers each step in detail.

1. **File > New > Impossible Creatures Project...** Choose **Creature**, start from **Wolf**, select **Replace the original wolf**,
   pick an empty folder and create the project.
2. Edit the creature. For example, enter Edit Mode and move some points.
3. In **IC Game > Game**, work through the numbered steps: **1. Check**, **2. Export for the Game**, **3. Install into Test Game**.
4. Start the game from the test copy and look at the wolf in the creature creator or a skirmish.
5. To undo, press **Remove** next to the item under **Installed here**.

To make a new creature instead of a replacement, choose **A new creature of my own** in step 1. It appears in the creature creator
alongside the stock animals.

## Documentation

Start with the [user guide](docs/guide/README.md). Its main chapters:

- [Getting started](docs/guide/start.md)
- [Replacing a stock animal](docs/guide/replace.md)
- [A new creature from a stock animal](docs/guide/newcreature.md)
- [A creature from your own mesh](docs/guide/mesh.md)
- [Gameplay numbers](docs/guide/numbers.md)
- [Animations and motions](docs/guide/anim.md)
- [Structures and props](docs/guide/structures.md)
- [Gatherers and characters](docs/guide/gatherers.md)
- [Effects, events and sounds](docs/guide/effects.md)
- [Checking, exporting, installing and sharing](docs/guide/ship.md)
- [Troubleshooting](docs/guide/trouble.md)
- [Maps](docs/guide/maps.md)

The same guide is also a single page, `docs/guide/index.html`, to open in a browser from a downloaded copy.

## Sharing

**IC Game > Game > 4. Share > Pack for Sharing** writes a zip of an exported item with installation instructions.
**Install From Zip** installs an item made by someone else into your test copy; it is checked before installation and can be removed
again.

## Known limitations

**Imposed by the game**

- New kinds of structure or unit, new abilities and build-menu entries require changes to the game's code and are out of scope.
  Existing structures and units can be replaced.
- A structure, unit or prop exported under a new name appears only where a map or script places it.
- New creatures appear in the creature creator and in skirmish, not in the campaign's army builder.
- Replaced or edited content must be installed by every player in a multiplayer game.

**Creatures**

- A creature built from an arbitrary mesh with **Fit Patches to Mesh** has no connection points and cannot be combined or exported
  for the game. Use **Retarget** onto a stock animal to obtain a combinable creature.
- Only animals of the base game and of *Insect Invasion* can be replaced. Animals from other mods can be used as the starting point
  for a new creature.
- Limb scaling rules used when combining are copied from the original animal and cannot be edited.
- A connection ring on a wing that is picked in the reverse direction is reported but not corrected automatically.

**Effects and sounds**

- The effect preview is approximate, and sounds cannot be auditioned in Blender.
- Effect items and replaced animals cannot be published to the Steam Workshop; share them as a zip.

**Projects**

- Projects saved with versions before 0.9.2 store models mirrored and are refused with a message; import the original file again.

**Maps**

- Map editing is experimental. Maps cannot be enlarged.

## Support

Blender shows the add-on's messages in the status bar and keeps them in the **Info** editor. A greyed-out button explains why in its
tooltip. The [troubleshooting chapter](docs/guide/trouble.md) lists common messages. If the game does not start or does not
show your content, see `warnings.log` in the test copy's folder.

To report a problem, open an [issue](https://github.com/akipina/ic_blender/issues) with your operating system, Blender version and
add-on version, the steps to reproduce it, the exact message, and the end of `warnings.log` if the game was involved.

## Licence

GNU General Public License v3 or later; see `LICENSE`. The bundled `construct` and `lupa` libraries are MIT licensed.
