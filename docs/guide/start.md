[Contents](README.md) · Next: [Replace one of the game's animals](replace.md)

# Start here: install, set up, and how a project works

This chapter gets you ready. It takes about fifteen minutes, and you do it once. After it, every other chapter is a complete walk-through: from a new project to seeing your work in the game.

You should know how to play Impossible Creatures and have used Blender a little: moving around the 3D view, selecting points, pressing <kbd>Tab</kbd> for Edit Mode. You do not need to know anything about the game's files. Some chapters ask for more Blender: Weight Paint, Shape Keys, the Action Editor and Texture Paint (buildings, workers and animations). The Blender manual at docs.blender.org explains them.

## What you need

- Blender 5.3 or newer.
- Your own copy of Impossible Creatures. The add-on reads the files of the Steam version.
- Linux. Windows and macOS support is experimental. If something does not work for you there, the message tells you what the add-on tried. Please report it. On Linux, `IC.exe` is a Windows program, so playing needs Wine or Proton (see “Start the game” below).
- About half a gigabyte of free disk space for a test copy of the game (about 5 GB if your computer cannot make links; the window tells you before it starts).

## Install the add-on

1. Download `ic_blender-<version>.zip` from the add-on's release page. Do not download “Source code”.
2. In Blender, open **Edit > Preferences > Get Extensions**. Open the menu at the top right and choose **Install from Disk...**. Pick the zip.
3. Open **Edit > Preferences > Add-ons** and check that **IC Blender** is ticked.
4. Move the mouse over the 3D Viewport and press <kbd>N</kbd>. A panel opens at the right side. Along its edge you should see two tabs: **IC Model** and **IC Game**. A third tab, **IC Map**, appears only once you open or start a map.

If you do not see the two tabs, the add-on did not load. In **Edit > Preferences > Add-ons > IC Blender** a red box called **Add-on parts that failed to load** says why. Send that text with your report.

To upgrade, install the new zip the same way and restart Blender. Only if the add-on then fails to load, remove it, restart Blender and install the zip again.

## Tell the add-on where your game is

1. Open **Edit > Preferences > Add-ons** and expand **IC Blender**.
2. Look at the box **Game**. If it says “Using ...” with your game's folder and a tick, you are done. The add-on found the game in your Steam library by itself.
3. If it says “No game found”, set **Game folder** to the folder that holds `IC.exe`.

The add-on only reads this folder. It uses it to list the game's animals and buildings, and to read their files.

## Make a test copy of the game

You never try your work in the game you bought. Steam looks after that folder, and it may overwrite or remove anything it did not put there. The game also writes into its own folder while you play. So you try your work in a **test copy**: your own folder that holds a copy of the game. If something goes wrong, delete the folder and make a new one. The add-on never writes into the Steam folder or into your original game. (There is a box **Allow the Steam game folder** in the preferences. Leave it empty.)

1. Open the **IC Game** tab. Open the **Game** panel. It is the last panel in the tab. It has four numbered boxes (explained below, in “The Game panel: four steps”). You need box 3 now.
2. In the box **3. Install into Test Game**, press **Create Test Game Copy**. (The same button is in **Edit > Preferences > Add-ons > IC Blender**, under **Test game**.)
3. A window shows where the copy will go (a folder called **IC Test Game** in your home folder, unless you change it), how much disk space it takes and how much is free. Pick another folder if you like. It must be new or empty, and it must not be inside a Steam library.
4. Press **Create Copy**. The panel shows “Making the test copy” with a percentage. Blender stays usable. **Cancel** stops it and removes what it made.

You should see: when it is done, the panel shows the folder with the line “A copy of the game made here on ...”, and the **Next:** line tells you what to do next. The new folder is now your **Test game folder**.

> [!NOTE]
> The copy does not repeat the big files. It holds links to them (the game's archives and movies), so for the Steam version it takes about half a gigabyte. Only the small files, and everything the game writes, are real copies. So the original game must stay where it is. If your computer cannot make links (on Windows that needs Developer Mode or an administrator account), the window says so and tells you the whole game will be copied, with its size, before anything starts. You can also tick **Copy everything (no links)** for a copy that does not depend on the original.

If the panel says “This copy was not finished”, the copy was interrupted. Delete the folder and make it again.

## The tabs

Press <kbd>N</kbd> in the 3D Viewport. The add-on has three tabs. You see **IC Model** and **IC Game** at once. **IC Map** appears only after you open or start a map. A panel appears only when it makes sense for what you have selected, so a tab can show fewer panels than listed here.

| Tab | What it is for | Panels |
| --- | --- | --- |
| **IC Model** | The shape: the surface, the skeleton, the skin, the parts, the textures. What you see and move in the 3D view. | **IC Blender** (with **Boner** and **Combine Preview** inside it, and **Damage States**), **Parts**, **Markers**, **IC Material** |
| **IC Game** | What the game does with the model: its numbers, its animations, its effects and sounds, and the steps that take it into the game. | **Blueprint** (the file that tells the game how the model behaves; it holds **Gameplay numbers**), **Motions**, **Effects and events**, **Creature Stats**, **Game** |
| **IC Map** | The game's maps (shown once a map is open). | **Map** and its parts |

Every panel starts with a short line that says what the panel is for.

## How a project works

You do not edit the game's own files. You make a **project**: a copy of one of the game's things, in a folder of your own. You change the copy. The game's own files stay as they are in its archives.

To start a project, choose **File > New > Impossible Creatures Project...** (the same button is in the **IC Model** tab, **New Impossible Creatures Project**). Pick what you want to make in **Type** (a creature, a gatherer or character, a structure or prop, a projectile, or an effect model). Pick which of the game's things to start from in **Start from**. These choices are in the panel at the right of the file window. (If you do not see it, press the small gear button at the top right of the window.) The window is a file browser: go to an empty folder of your own, type a file name at the bottom and press **New Impossible Creatures Project** at the bottom right. The add-on writes the files there and opens them in Blender. If a creature project is refused (for example “Could not start the project: ...”), the window has closed. Open it again and choose another file name.

The files it writes are the original's files, under your name:

- the **model** (`.sgm`): the shape, the skeleton and the animations;
- the **blueprint** (`.ebp`): the file that tells the game how the model behaves, with its animations, sounds, effects and numbers;
- for a creature, also the **numbers** (`.lua`): hit points, speed, attacks, cost; and the **limb scales** (`.lsc`): how the creature is resized when the game combines it with another animal.

The add-on builds your export on these files. It writes only what you changed. Everything you did not touch comes out of those files byte for byte, so nothing is lost and a creature you did not change is the original again.

**Do not move, rename or delete these files while you work.** If another program changes one, the add-on says so (“The model file ... was changed on disk after it was opened here”) and asks you to import it again.

Your edits live in the Blender file (`.blend`). Save it as you always do. The project's own files are the starting point, not your saved work.

> [!NOTE]
> Starting a project from a game animal has two choices, in the same window. **A new creature of my own** makes a creature under a name you choose. The game's animals stay as they are. **Replace the original** (it shows the animal's name) makes your version take the place of the game's own animal. The chapter [Replace one of the game's animals](replace.md) says what that means.

## The Game panel: four steps

Every kind of project ends in the **Game** panel of the **IC Game** tab. It reads from top to bottom in the order you work:

1. **1. Check**: finds what would stop the game loading your work. Nothing is written.
2. **2. Export for the Game**: writes a folder with the files the game reads. For a creature the button is **Export Creature for the Game**. For a structure, a gatherer, a character, a prop or an effect it is **Export Entity Folder**. The folder is called an **item**.
3. **3. Install into Test Game**: copies the item into your test copy. **Remove** (in the list **Installed here**) takes it out again.
4. **4. Share**: **Pack for Sharing** makes a zip for other players. **Install From Zip** and **Install From Folder** install something someone else made.

At the bottom of step 3, a line that starts with **Next:** always tells you the one thing to do now: set the Game folder, make the test copy, export, install, or start the game. When you are lost, read that line.

The chapter [Check, export, install, remove and share](ship.md) goes through each step.

## Where to read messages

- The line at the bottom of the Blender window shows the last message.
- Change any area of the screen to the **Info** editor to read every message: click the editor-type button at the top left of the area and choose **Info**. (The Scripting tab at the top of Blender has one already.) Long reports, such as an export report, are there in full. A line you are told to look for, such as “Wrote ...”, is in this list and not always on the bottom bar.
- If a button is grey, hold the mouse over it. Blender says why. Usually you must select the creature or thing first, or leave Edit Mode.
- If the game does not show your work, read the file `warnings.log` in the folder of your test copy (the folder with `IC.exe`).

## Start the game

Every walk-through ends with “start the game from your test copy”. The test copy is a folder that holds `IC.exe`.

- **Windows:** run `IC.exe` in that folder.
- **Linux:** `IC.exe` is a Windows program, so it needs Wine or Proton. If you play through Steam, you can add it with **Games > Add a Non-Steam Game** and pick a Proton version in the game's **Properties > Compatibility**. If you already run the game on Linux some other way, start the test copy the same way.
- The Steam version starts only while Steam is running and you are signed in.
- Close the game before you install again. It reads a thing's files when it loads it.

## What next

Do the chapter [Replace one of the game's animals](replace.md) first. It is the shortest way to see your own work in the game. Then choose the chapter for what you want to make:

- a creature of your own: [A new creature from a game animal](newcreature.md), or [A creature from your own mesh](mesh.md);
- a building, a worker, a character or scenery: [Structures and props](structures.md), [Gatherers and characters](gatherers.md);
- how a thing plays: [Gameplay numbers](numbers.md), [Animations and motions](anim.md);
- smoke, sparks and sounds: [Effects, events and sounds](effects.md);
- when something goes wrong: [Troubleshooting](trouble.md).

---

[Contents](README.md) · Next: [Replace one of the game's animals](replace.md)
