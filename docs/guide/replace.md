[Contents](README.md) · Previous: [Start here: install, set up, and how a project works](start.md) · Next: [A new creature from a game animal](newcreature.md)

# Replace one of the game's animals

This is the shortest way to see your own work in the game. You change one of the game's own animals, here the wolf, and the game then uses your wolf. It takes about ten minutes. You need the setup from [Start here](start.md), including a test copy of the game.

> [!IMPORTANT]
> **Limits**
>
> Replacing changes the animal for **every army and every skirmish** in the copy of the game you install it into. In a game with other players, everyone needs the same files. When a match starts, the game compares the numbers and the size of the creatures in the armies. A match with a wolf in it, or with a creature built from wolf parts, will not agree if the other player has the original wolf. A match without a wolf is not affected. Replacing is for your own test game. If you want an animal of your own next to the originals, use [A new creature from a game animal](newcreature.md) instead.

## What you can and cannot replace

- You can replace the animals of the base game and of Insect Invasion.
- You cannot replace an animal of Dino Dawn or Tellurian, an animal of a mod, or a creature built from nothing. For those, choose **A new creature of my own**.
- The animal keeps its own name and texts. What changes is the model, the picture of its skin and its numbers.
- The Steam Workshop does not take a replacement. It takes only creatures with new names.

## 1. Start the project

1. Choose **File > New > Impossible Creatures Project...**. A file window opens.
2. The choices are in the panel at the right of that window. (If you do not see it, press the small gear button at the top right of the window.) Keep **Type** on **Creature**.
3. Open **Start from**. The first choice is **Default skeleton**. After it come the game's animals, each with its origin in brackets. Choose **Wolf (Base game)**.
4. Under it, two buttons appear: **A new creature of my own** and **Replace the original wolf**. Choose **Replace the original wolf**. A small text says what it does.
5. The window is a file browser. Go to an empty folder of your own. Ignore the **Name** box and the file name at the bottom: the files are called `wolf` whatever you type, because the game finds the wolf by that name. Press the **New Impossible Creatures Project** button at the bottom right.

You should see a wolf in the 3D Viewport: a wolf shape with its points, and its skeleton. The message at the bottom of the window says “Started the creature project wolf ... it replaces the game's own wolf when you export it for the game”. In your folder you now have `wolf.sgm` (the model), `wolf.ebp` (the blueprint), `wolf.lua` (the numbers) and `wolf.lsc` (the limb scales).

If you do not see **Replace the original wolf**, you picked an animal that cannot be replaced (see above), or **Default skeleton**, or the add-on did not find the game. In that last case **Start from** holds only **Default skeleton**: set the **Game folder** in the preferences ([Start here](start.md)). If the message says `wolf.sgm` is already there, the folder holds an earlier wolf project. Choose another folder. Nothing is overwritten.

## 2. Change the wolf

Anything you can do to a creature works here. For a first try, give it a hump.

1. Make sure the wolf is selected, and press <kbd>Tab</kbd> to enter Edit Mode.
2. Click a point at the top of the back. Press <kbd>O</kbd> to turn on proportional editing.
3. Press <kbd>G</kbd>, <kbd>Z</kbd>, <kbd>0.25</kbd>, <kbd>Enter</kbd>. Scroll the mouse wheel while you move to change how wide the lift is.
4. Press <kbd>O</kbd> again to turn proportional editing off, and <kbd>Tab</kbd> to leave Edit Mode.

You should see a bump on the wolf's back. The surface follows the points. The chapter [Editing an existing animal](edit.md) has more you can do.

## 3. Check it

1. Open the **IC Game** tab and the **Game** panel.
2. In the box **1. Check**, press **Check the Creature**.

For the wolf you should see “2 warning(s): the Info editor lists them”. That is expected. Read them in the **Info** editor ([Start here](start.md) says how to open it). One says the clip “Crouch” is never played; the game's own wolf has the same clip. The other is the notice that this replaces the game's wolf. Both are harmless. A warning is advice, not a stop sign. A problem in red must be fixed first: the game would not load the creature, and the export refuses it. See [Troubleshooting](trouble.md).

## 4. Export it for the game

1. In the box **2. Export for the Game**, read the lines under the title. They say what the creature is built on, then “Export as: Replace the original wolf” and what that means.
2. Press **Export Creature for the Game**. A window opens. At the top, **Replace the original wolf** is chosen. Under it is the warning from the box at the top of this chapter.
3. You can leave the rest as it is. **Icon picture** lets you choose a picture for the creature creator. **Make the icon from the creature** (ticked) draws one from your wolf. **Folder** is where the export folder goes. Empty means a folder called `ic_items` beside your wolf files.
4. Press **OK**.

You should see, in the **Info** editor, a line that starts with “Wrote” and says how many files were written and that they replace the game's own wolf. The box now shows “Last export: wolf”.

The creature creator's picture: if you chose no picture, the export draws one for you. It shows the wolf from the front, from the side and a little from above, on the creature creator's green-brown background. That needs Blender's renderer. If it cannot draw it, the original wolf's picture stays, and a warning says so. Choose **Icon picture** or press **Icon from this View** (a button next to the export button; it takes the picture from your 3D view as it is now) and export again.

## 5. Install it into your test copy

1. In the box **3. Install into Test Game**, check that your test copy's folder is shown. If it shows a red message, see the **Next:** line.
2. Check the line **Installs: wolf**.
3. Press **Install into Test Game**.

The add-on first checks the item the way the game's own rules would. Then it copies the files into the copy's `Data` folder. Loose files there come before the files in the game's archives, so the game uses yours. The game also keeps a saved copy of each model it has drawn. The install deletes the saved copy of the wolf, so the game draws your new one. Everything it replaces is kept, so that **Remove** can put it back.

You should see “Installed 'wolf' into ...: 5 files”. (If your copy already had loose files for the wolf, the message also says how many were replaced.) Two yellow lines follow. They say the Steam Workshop would not take these files for sharing, and that the game still loads them. Ignore them: you are not using the Workshop. A third line says the install replaces 4 files the game ships. That is the point. The list **Installed here** (a box under box 3) now shows `wolf`. The **Next:** line says to start the game from your test copy.

## 6. Play it

1. Close the game if it is running. It reads a creature's files when it loads it.
2. Start the game from the test copy: run `IC.exe` in that folder. On Linux that needs Wine or Proton, and the Steam version starts only while Steam is running. [Start here](start.md) has the details, under “Start the game”.
3. Start a skirmish and build a creature that uses the wolf, or look at the wolf in the creature creator.

You should see your wolf, with the hump. If you see the old wolf, read [Troubleshooting](trouble.md): the usual reasons are that the game was still running, or that a game mod in your copy has its own wolf file that comes first.

## 7. Go back

In the list **Installed here**, press **Remove** next to `wolf`. A window asks “Remove 'wolf' from the Test game?” Confirm. The message says “Removed 'wolf': 5 files deleted, 0 put back as they were.” **“0 put back” is right the first time.** The original wolf lives inside the game's archive and shows again as soon as your files are gone. “Put back” counts only loose files you had before. Your project stays in Blender, so you can install it again.

---

[Contents](README.md) · Previous: [Start here: install, set up, and how a project works](start.md) · Next: [A new creature from a game animal](newcreature.md)
