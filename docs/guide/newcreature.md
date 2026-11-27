[Contents](README.md) · Previous: [Replace one of the game's animals](replace.md) · Next: [A creature from your own mesh](mesh.md)

# A new creature from a game animal

You make a creature of your own, with its own name, from one of the game's animals. It appears in the game's creature creator next to the originals. The game's own animals stay as they are. You need the setup from [Start here](start.md). If you have not done [Replace one of the game's animals](replace.md), do that first. It shows every step on a shorter path.

This chapter uses the cheetah as the starting animal. Use any animal you like.

## 1. Start the project

1. Choose **File > New > Impossible Creatures Project...**. A file window opens. The choices are in the panel at the right of it. (If you do not see it, press the small gear button at the top right of the window.) Keep **Type** on **Creature**.
2. Open **Start from** and choose **Cheetah (Base game)**. The list holds the animals of the base game, of Insect Invasion, of Dino Dawn and of Tellurian, and of any mod you installed. The origin is in brackets.
3. Under it, keep **A new creature of my own**. (The other choice, **Replace the original ...**, is for animals of the base game and of Insect Invasion only. For the animals of Dino Dawn, Tellurian or a mod there is no choice to make.)
4. Set **Animations** (see below), then go to an empty folder of your own. Type a file name at the bottom of the window, and press **New Impossible Creatures Project** at the bottom right.

Ignore the **Name** box in the panel: the name comes only from the file name at the bottom. The file name starts as `NewCreature.sgm`. Replace it, or your creature is called “newcreature”. The file name is the creature's name in the game. It must be 3 to 24 letters (a to z), digits or underscores, in lower case, with no spaces. It cannot be the name of an animal the game already has. If it is, the message says “'cheetah' is the name of a creature the game already has: choose another name”. If a name is refused, the window has closed: open **File > New > Impossible Creatures Project...** again and type another name, for example `my_cheetah`.

You should see your creature in the 3D Viewport, with its skeleton, and the message “Started the creature project my_cheetah ... a copy of the game's cheetah with its blueprint, stats and rules beside it”. Your folder holds `my_cheetah.sgm`, `.ebp`, `.lua` and `.lsc`. Nothing already in the folder is overwritten.

## Animations at the start

The **Animations** choice in the window decides which animations your creature starts with.

- **Keep its own**: the animal's own animations. This is what you want most of the time.
- **None**: no animations. The copied blueprint still lists the animal's motions, so with no clips they play nothing until you add clips with the same names. The window says so.
- **Borrow from existing creatures**: copies the animations of another creature onto yours. Before you open the window, press **Extract Stock Creatures** (top of the **IC Model** tab) once, so that the list **Borrow from** can show the game's animals. You can also pick a creature file. **Only these animations** takes names separated by commas; empty copies all. Bones are matched by name. If few match, the clips will hardly move, and the add-on warns you. For borrowing later, see [Animations and motions](anim.md).

## 2. Change it

You can change any of these. Each has its own chapter.

- The shape: [Editing an existing animal](edit.md), [Topology](topology.md), [Sculpting](sculpt.md).
- The skin and its picture: [Textures](texture.md).
- Which bone moves which point: [Skinning and re-skinning](skin.md). Limbs and the sockets where parts join: [Bones, limbs and connections](boner.md).
- The gameplay numbers (hit points, speed, attacks): [Gameplay numbers](numbers.md).
- Animations and when they play: [Animations and motions](anim.md).
- Sounds and effects: [Effects, events and sounds](effects.md).

The creature keeps everything you do not change. Its animations, its sounds and its numbers are the cheetah's. A creature with no changes at all is the cheetah under another name.

## 3. See how it combines

The game's creature creator joins two animals. A creature that cannot be joined is not much use, so check it. Open **Combine Preview** in the **IC Model** tab, choose another animal, press **Preview**, and look at the result. The chapter [Finding your way around a creature](tour.md) shows how. (It needs **Extract Stock Creatures** once.)

## 4. Check it

1. Open the **IC Game** tab and the **Game** panel.
2. In **1. Check**, press **Check the Creature**.

A creature made from a game animal shows about a dozen warnings, for example “12 warning(s): see the Info editor”. They say that some of the animal's animations are never played, and that the creature creator still shows the original animal's picture. The original animal has the same ones, and they are harmless. Only a problem in red must be fixed first. See [Troubleshooting](trouble.md), which explains the common warnings.

## 5. Export it for the game

1. In **2. Export for the Game**, the box says “Built on cheetah's blueprint and stats” and “Export as: A new creature of my own”.
2. Press **Export Creature for the Game**. A window opens.
3. Fill it in:
   - **Name**: the creature's file name. It is your file name already. If it is wrong, a red line says what is wrong, as soon as you type.
   - **Display name**: the name the creature creator shows. Type it as you want to see it, for example `My Cheetah`.
   - **Description**: a text shown with the creature's numbers.
   - **Icon picture**: the picture the creature creator shows (it is resized to 64 by 64). Leave it empty and keep **Make the icon from the creature** ticked, and the export draws a picture from your creature. Untick it to keep the original animal's picture.
   - **Folder**: where the export folder goes. Empty means a folder called `ic_items` beside your model files.
4. Press **OK**.

You should see a line in the **Info** editor that starts with “Wrote” and ends with “Next: press Install into Test Game in the Game panel”. The box now shows “Last export: my_cheetah”.

The export folder holds the files the game reads. All their names are in lower case, because the game's archives look names up exactly as they are written. If the export folder already exists, the export replaces an earlier export of the same creature. It refuses to replace any other folder.

The picture drawn from the creature needs Blender's renderer. If Blender cannot draw it, you get a warning, and the creature keeps the original animal's picture. Choose an **Icon picture** or press **Icon from this View** and export again.

## 6. Install it into your test copy

1. In **3. Install into Test Game**, check the line **Installs: my_cheetah**.
2. Press **Install into Test Game**.

You should see “Installed 'my_cheetah' into ...” followed by the number of files and a part that starts “added to the creature creator (...)” with the mods that got the new name.

The game's creature creator shows only the animals whose name is on a list it reads when it starts. The install adds your creature's name to the list of **every mod** in your test copy: the base game, Insect Invasion, Tellurian and any mod you installed. A mod that hides some of its animals on purpose keeps hiding them. Its list gets your name and nothing else. **Remove** takes the name out again.

If the message says a mod's list could not be written, or that you edited a list by hand, it names the mod where your creature will not show. Add the name to that list yourself. The line to add is shown in the README of a zip made by **Pack for Sharing**.

## 7. Play it

1. Close the game if it is running. Start it from the test copy (run `IC.exe` there; [Start here](start.md) says how on Linux).
2. Open the creature creator or start a skirmish. Look for **My Cheetah** in the list of animals.
3. Choose it as the body (the “torso”), or add it to another creature as a part.

> [!IMPORTANT]
> **Limits**
>
> In the single-player campaign, the army builder offers only the animals the campaign has unlocked. A new creature does not show there. That is how the game works. It shows in the free creature creator and in skirmish.

To take your creature out, press **Remove** next to `my_cheetah` in **Installed here**.

---

[Contents](README.md) · Previous: [Replace one of the game's animals](replace.md) · Next: [A creature from your own mesh](mesh.md)
