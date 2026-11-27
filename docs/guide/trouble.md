[Contents](README.md) · Previous: [Check, export, install, remove and share](ship.md) · Next: [Finding your way around a creature](tour.md)

# Troubleshooting

When something does not work, read the message first. Blender shows the last message at the bottom of the window. The **Info** editor keeps all of them. To open it, click the editor-type button at the top left of any area and choose **Info**. (The Scripting tab at the top of Blender has one already.) The add-on's messages say what is wrong in plain words, and most say what to do. This chapter lists the ones you are most likely to meet, in the add-on's own words, and what to do. A word in angle brackets, such as &lt;folder>, stands for the name in your message. A dots mark (...) means more text that depends on your case.

## Most likely fixes

1. **A button is grey.** Hold the mouse over it. Blender says why. Usually you must select the creature or thing first, leave Edit Mode (press <kbd>Tab</kbd>), or choose an event in the list.
2. **The add-on says it has not found the game.** In **Edit > Preferences > Add-ons > IC Blender**, set **Game folder** to the folder that holds `IC.exe`.
3. **You have no test copy.** Make one: **IC Game** tab > **Game** panel > **Create Test Game Copy**.
4. **The game still shows the old thing.** Close the game and start it from the test copy, not from Steam (see “The game shows nothing, or the old thing” below).
5. **You cannot find a message.** Open the **Info** editor (see above).

If your message is not here, search this page (<kbd>Ctrl</kbd>+<kbd>F</kbd>) for the first few words. If it is still not there, send the whole message with your report (see the last section).

Messages in **red** in a panel, or that start with **Error**, stop something. Messages that start with **Warning**, or that are in a grey line, are advice. You can go on.

## The add-on does not show up or cannot find the game

| What you see | What to do |
| --- | --- |
| `No game found. Set the Game folder above, or set the IC_INSTALL environment variable.` | In **Edit > Preferences > Add-ons > IC Blender**, set **Game folder** to the folder that holds `IC.exe`. Ignore the part about IC_INSTALL. |
| `No game folder found: set the Game folder in the add-on's preferences (Edit > Preferences > Add-ons, IC Blender), or set the IC_INSTALL environment variable.` | The same. Until the game is found, the list **Start from** in the New Project window shows only **Default skeleton** (for a creature) or **Another .sgm file...** (for the other types). |
| `Add-on parts that failed to load (a red box in the preferences)` | A part of the add-on did not start. The box says which part and why. Restart Blender. If it stays, install the zip again, and send the text of the box with your report. |
| `This was made with an older version of IC Blender. Import the original file again.` | The `.blend` was saved with an add-on older than 0.9.2, which kept models flipped left to right. Open the model again with **File > Import**, or start the project again. |
| `The model file <name> was changed on disk after it was opened here ... Import <name> again to carry on from the file as it is now.` | Another program, or an export from another Blender session, changed the project's file. Import it again with **File > Import**. In the creature panels the box **Problems** offers **Relink Source** for a file that moved. |

## Making the test copy

These come from **Create Test Game Copy**. The window shows the first one before you press **Create Copy**.

| What you see | What to do |
| --- | --- |
| `<folder> is inside a Steam library. Steam owns that folder: it rewrites it and would wipe the copy. Choose a folder somewhere else (your Documents, for example).` | Choose a folder outside every Steam library. |
| `That is the game folder itself. Choose a new folder for the copy. / <folder> is inside the game folder it would copy. Choose a folder outside the game. / <folder> holds the game folder it would copy. Choose a new, empty folder.` | The copy must be a new folder of its own, not the game folder, not inside it and not around it. |
| `<folder> already has files in it. Choose a new or an empty folder: the copy must not mix with other files.` | Choose a new or empty folder. |
| `<folder> already is a test copy of the game. Set it as the Test game folder instead of making another (or delete the folder first to start again).` | Set that folder as **Test game folder** in the preferences, or delete it and make the copy again. |
| `<folder> is not a game folder (it has no Engine.sga). / The game folder does not exist. Set the Game folder in the add-on's preferences.` | The **Game folder** is wrong. It must be the folder that holds `IC.exe` and `Engine.sga`. |
| `A test copy is already being made. Wait for it, or press Cancel.` | Wait for the percentage in the Game panel to finish, or press **Cancel**. |
| `This copy was not finished (the copy was interrupted). Delete the folder and press Create Test Game Copy again.` | Do what it says. |
| `The window shows the free space in red` | There is not enough room. Choose another disk, or free some space. A copy with links is about half a gigabyte, a full copy about 5 GB. |

## Installing and removing

These come from the **Game** panel, box **3. Install into Test Game**.

| What you see | What to do |
| --- | --- |
| `No Test game folder is set. Make a copy of the game and set it in the add-on's preferences (Test game folder).` | Press **Create Test Game Copy**. It sets the folder for you. |
| `The Test game folder <folder> does not exist. Fix it in the add-on's preferences.` | The folder was moved or deleted. Set it again, or make a new copy. |
| `<folder> is the Game folder of the add-on, the original game. Install into a copy of the game (set it as the Test game folder), or tick the box in the preferences if you really mean this folder.` | Your **Test game folder** is the same as your original game. Make a test copy and use that. Do not tick the box. |
| `<folder> is the Steam game folder. Steam rewrites it and would wipe the item. Use a copy of the game as the Test game folder, or tick the box in the preferences if you really mean this folder.` | The same, for a folder inside a Steam library. Use a test copy. |
| `No item folder yet: export the project for the game first, or pick an item folder in the Game panel.` | Export first (step 2), or set **Item folder** to an item folder. |
| `The game would not take this item as it is, so nothing was installed. ... Fix this and export again, or press Install Anyway in the Game panel to install it as it is.` | The lines after it say what is wrong. Fix it and export again. **Install Anyway** installs the item as it is, and it may not work. |
| `<file> was changed after it was exported (it no longer matches the list in the manifest). Export the item again.` | A file in the item folder was edited after the export. Export again. |
| `<file> is left over from an earlier export: the item's list of files does not name it. Delete it from the item folder.` | Delete that file from the item folder. |
| `<file> is named in the item's list of files but is not in the folder. Export the item again.` | A file was deleted from the item folder. Export again. |
| `<file> has capital letters in its name; the game asks for lower-case names (rename it to lower case).` | Rename the file in lower case, or export again. |
| `<file> is a settings file; an item may not carry one. / ... sits next to the data folder; everything must be inside the data folder. / ... is a shortcut (a symbolic link), not a real file; items hold real files only.` | Take that file out of the item folder. An item holds only real files inside its `data` folder. |
| `This item holds program code (a .dll, compiled Lua or scripts that run). The add-on installs data items only: install it anyway only if you made it and trust it.` | Do not install something you did not make and do not trust. The add-on installs models, pictures, sounds and settings, not programs. |
| `<file> is a model the game would not load correctly. / ... is damaged or not in the format the game reads.` | Export the item again from Blender. If it happens again, send the message with your report. |
| `Advice: <file> differs from the game's own models (advice only; the game may still load it): ...` (a yellow line after the install message) | Nothing to do. It is advice, not a refusal. The install went ahead. Only the red box with **Install Anyway** is a refusal. |
| `Advice: ... is not something the Steam Workshop takes for sharing; the game still loads it.` | Ignore it. You are not using the Steam Workshop. The game loads the files. |
| `The install stopped: ... Anything already written was taken back; press Remove if the item is listed.` | Usually the disk is full, or a file is in use. Close the game, free some space and press **Install into Test Game** again. |
| `<item>.lst is in the game folder and was not made by this add-on (another tool's item): choose another item id` | Another tool installed an item with that name. Export under another name. |
| `<folder>: a symbolic link where a folder goes (a game copy made of links cannot be written to without changing the original)` | A folder in your copy is a link to the original. The install refuses so that it cannot change the original game. Make the test copy again with **Create Test Game Copy**. |
| `The creature list of <mod> was NOT changed: ... (or) ... is not a plain list of names, so it was NOT changed and the new creature is not offered in that mod.` | A mod's creature list could not be written, or you edited it by hand. Add your creature's name to that list yourself (the README of a zip made by **Pack for Sharing** shows the line). The creature shows in the other mods. |
| `N file(s) of this item are also in the folder of the mod ... The game keeps loading that copy there, not the new file.` | A file of that name in a mod's folder (or in `DataMOD`) comes first. Play without that mod, or remove that file from the copy. |
| `<item> is not installed / <item> was not installed by this add-on (its uninstall list is another tool's); it is left alone` | Nothing to remove here. Another tool owns that item. |
| `The removal stopped: ... Press Remove again once the file is free.` | Close the game and press **Remove** again. |

## Starting a project

These come from **File > New > Impossible Creatures Project...**. The file window has closed when you read them.

| What you see | What to do |
| --- | --- |
| `Could not start the project: 'cheetah' is the name of a creature the game already has: choose another name` | Open **File > New > Impossible Creatures Project...** again and type another file name (lower case, no spaces, 3 to 24 characters). |
| `Could not start the project: 'my cheetah' is not a usable creature name: use 3 to 24 letters (a-z), digits or underscores` | The same. Use letters, digits and underscores only, for example `my_cheetah`. |
| `Could not start the project: my_cheetah.sgm is already there: choose another name (nothing was overwritten)` | The folder holds an earlier project of that name. Choose another folder or another name. |

## What the common warnings mean

A warning is advice, not a stop sign. A creature made from a game animal shows about a dozen of these. The original animal has the same ones.

| What you see | What it means |
| --- | --- |
| `N warning(s): see the Info editor` | The check found advice, no problem. Read the lines in the Info editor. Only lines in red must be fixed. |
| `The clip '<clip>' is not played by any motion, so the game never plays it ...` | The animation is in the file, but no rule tells the game when to play it. Harmless for the game's own animals (they have such clips). For a clip of your own, see [Animations and motions](anim.md). |
| `the creature creator picture is still the original <animal>'s (give an icon picture to change it)` | The creature creator still shows the original animal's picture. Choose an **Icon picture** when you export, or keep **Make the icon from the creature** ticked. |
| `This REPLACES the game's own <name> ...` | This is the notice for a replacement. It is what you chose. See [Replace one of the game's animals](replace.md). |

## Changing parts of a model (buildings, workers, props)

| What you see | What to do |
| --- | --- |
| A button in the **Parts** panel is grey | Press <kbd>Tab</kbd> to leave Edit Mode. These buttons work only in Object Mode. |
| `Select the new mesh, then the part to replace (last)` | Click your mesh, then <kbd>Shift</kbd>-click the model's part last, so that it is the active one. Press the button again. |
| `Select ONE mesh to put in the part (join several with Ctrl+J first)` | Join your meshes into one (select them, press <kbd>Ctrl</kbd>+<kbd>J</kbd>), then try again. |
| `Select the meshes to add (not parts of a model) and the model's part last` | Select your new mesh, then a part of the model last. The mesh must not be a part already. |
| `Select the new mesh, then any part of the model (last)` | Click your mesh, then <kbd>Shift</kbd>-click any part of the model last. |

## A creature from your own mesh

| What you see | What to do |
| --- | --- |
| `Choose a target mesh (the shape the creature should take)` | The window opened with **Target Mesh** empty. Press the button again and choose your mesh in **Target Mesh** before you press OK. |
| `... a creature is one closed skin. Merge by Distance or split extra pieces off (Edit Mode: P > By Loose Parts)` | Your mesh has loose parts. Select it, press <kbd>Tab</kbd>, then **Mesh > Clean Up > Merge by Distance**. If it is several pieces on purpose, use **Fit Patches to Mesh** with **Fit Separate Pieces** ticked. |
| `<mesh>'s object transform was ignored. Apply the transform first ...` | Select the mesh, press <kbd>Ctrl</kbd>+<kbd>A</kbd> and choose **All Transforms**, then fit again. |
| `Patch budget 300 is over 263: it will load, but may not combine` | Lower **Patch Budget** (in the panel at the bottom left after the fit) if you want the creature to combine. |

## Animations and motions

| What you see | What to do |
| --- | --- |
| `There is already a clip or an Action called '<name>'. Choose another name.` | Press the button again and type a name the rig does not have. |
| `Give the clip a name of 1 to 32 characters.` | Type a name. |
| `The rig has no clip to start from. Use Borrow Clips... first.` | Press **Borrow Clips...** in the **Motions** panel. |
| `Few bones share names with that model: its clips will move little.` | The other model's bones have other names than yours, so the borrowed animation hardly moves. Borrow from a model with the same skeleton. |
| `Borrowing failed (...). Press Ctrl+Z to undo what it did ...` | Press <kbd>Ctrl</kbd>+<kbd>Z</kbd>, then try another model or file. |

## Effects, events and sounds

| What you see | What to do |
| --- | --- |
| `Name the clip first: type it in the Clip box above the list.` | The model has no clip yet. Type a clip name in the **Clip** box of the Events box. |
| `Choose an event first.` | Click an event in the list in **Events**, then try again. |
| `The clip '<clip>' has no event that starts an effect.` | Nothing to show for that clip. Choose an event of the clip, give it an **Effect**, and try again. |
| `Copy the effect into your project first (Copy to my project).` | A game effect cannot be edited in place. Press **Copy to my project** in **Effect editor**. |
| `There is an effect called '<name>' already ...: choose another name.` | Type another name. |
| `This creature has no clips yet. Import or make its clips first.` | Make or borrow clips first ([Animations and motions](anim.md)), then copy the events. |
| `The blueprint the events were read from has changed since ... Press Load events ...` | Press **Load events** again. It reads the file as it is now and drops the event changes you had not exported. |
| `The game cannot play ... sound ... Save the sound again as WAV with plain PCM, 16 bit.` | Open the sound in a sound program and save it again as a WAV file, 16 bit, plain PCM. |

## Exporting a creature

These come from **Export Creature for the Game** and **Check the Creature**.

| What you see | What to do |
| --- | --- |
| `... connection ring 'head' missing ... / Export for the Game refused: nothing was written` | The same problem as the next row, seen from the export button: the creature has no connection rings. See below. |
| `This creature has no connection rings, so Export refuses it. Use Retarget Creature to Mesh (IC Model tab, Tools).` | The creature came from **Fit Patches to Mesh**, which makes the shape only. Start from a game animal and use **Retarget Creature to Mesh** ([A creature from your own mesh](mesh.md)). |
| `the creature would not load: ... / Export for the Game refused: fix these first (Check the Creature lists them all)` | Press **Check the Creature** and read the red lines in the **Info** editor. Each says what to fix. If you deleted, merged or duplicated vertices, press <kbd>Ctrl</kbd>+<kbd>Z</kbd>. |
| `N control points used by patches have no bone (points ...): the game needs every patch point skinned to a bone. Boner > Bones > Select Unassigned selects them (orange in the By Bone overlay); assign them to a bone or run Auto Skin` | In Edit Mode, press the small **Select** button next to the line “Unassigned: N” in the **Boner** panel (**Bones**). Then press **Assign Selected** or **Auto Skin** ([Skinning and re-skinning](skin.md)). The button appears only in Edit Mode. |
| `The name '<name>': 3 to 24 characters of a-z, 0-9 and _ (lower case, no spaces)` | Change **Name** in the export window. A red line shows the problem as you type. |
| `<name> is a game animal's name: choose 'Replace the original <name>' to change that animal, or another name` | Pick **Replace the original ...** in the export window, or give the creature another name. |
| `There is no game animal to replace: this creature was not started from one of the game's own animals. Choose 'A new creature of my own'.` | Only a project started from an animal of the base game or of Insect Invasion can replace it. |
| `The folder <folder> exists and is not an earlier export of <name> ... so nothing was written and nothing of yours is lost. Choose another folder or another name.` | The export folder holds something else (maybe your project). Choose another **Folder**. |
| `That is the game's own folder. Write into a separate folder ... / That folder is inside Steam's game folders. Write into a separate folder ...` | Choose a folder of your own for the export. |
| `N numbers are outside what the export accepts: Export Creature for the Game will refuse them.` | In **Creature Stats** the numbers in red are too big or too small. Fix them or press their circle arrow ([Gameplay numbers](numbers.md)). |
| `The game would not load this effect: ... (fix it in Effects and events > Effect editor). / The file '<file>' has a space in its name, and an item cannot list a file like that.` | Open **IC Game > Effects and events > Effect editor**. Fix the effect named, or delete the file with the space and copy the effect from the game again. |
| `The event '<name>' names no clip, so the game never runs it.` | Give the event a **Clip** in the Events box, or delete it. |
| `The clip '<clip>' is not played by any motion, so the game never plays it. Give it a motion: in the Motions box press Add Copy, choose this clip and set when it plays ...` | See [Animations and motions](anim.md). |
| `The rig has no clip called '<clip>': this motion would do nothing.` | A motion names a clip the rig does not have (it was renamed or deleted). Choose a clip for the motion, or make the clip. |
| `No icon was made for the creature creator: ... It shows the original's picture; choose an icon picture or press Icon from this view in a 3D view, then export again` | Blender could not draw the picture (no graphics card or driver in that session). The export still worked. Choose an **Icon picture** if you want your own. |

## Exporting a structure, a prop, a worker, a character or an effect

These come from **Check** and **Export Entity Folder**.

| What you see | What to do |
| --- | --- |
| `The game would not accept these files (N errors), so nothing was written: (1) ... Tick 'Export anyway' to write them regardless.` | Press **Check** to see every error. Fix them. **Export anyway** writes the files regardless, and the game may refuse them. |
| `There is no blueprint (.ebp) file beside the model. Import the model from the game's own files, or start a project from one of the game's things.` | Start the project with **File > New > Impossible Creatures Project...**. That brings the blueprint. |
| `There is no thing of the game to replace: this model was not started from one of the game's own things ... Choose 'A new name of my own'.` | Only a project started from one of the game's things can replace it. |
| `Export to <file> refused: it is this model's own blueprint, and ... would be applied a second time by the next export. Export under another name` | You tried to write onto the project's own file with a change that cannot be repeated (a renamed bone, a new part, a changed texture). Choose a folder and name of your own. |
| `The blueprint file '<file>' changed on disk after it was loaded. Press Reset to read it again ...` | Press **Reset** in the **Blueprint** panel. This throws away blueprint changes you have not exported. |
| `This blueprint carries its model inside itself, so there is no separate .sgm file to write beside it.` | This kind of blueprint can be looked at but not exported. |
| `part '<name>' comes to N vertices once split at UV seams and flat faces; the game draws at most 12,288 per part. Reduce its detail (Decimate, fewer seams) or split it into two parts` | Do what it says. Apply a Decimate modifier to the part, or make it two parts. |
| `N vertices of '<part>' are weighted only to deleted bone(s) ..., so they follow no bone in Blender. Undo the delete and use Armature > Delete Bones, Merge Weights (it hands the weights to the parent), or weight those vertices to another bone` | Do what it says. Delete bones with **Armature > Delete Bones, Merge Weights**, not with <kbd>X</kbd>. |
| `bones '<a>' and '<b>' have the same name ignoring case ... / bone '<name>...' has a N-character name; the game reads at most ... / bone '<name>' (the first bone) cannot be deleted ...` | Rename the bone. Bone names are at most 79 characters. The first bone must stay. |
| `Sides must be a power of two, at most 1024 (a red line in the IC Material panel)` | Use **Replace Image** with **Scale to a size the game takes** ticked, or resize the picture. |

## Sharing and installing a zip

These come from the box **4. Share**.

| What you see | What to do |
| --- | --- |
| `The item has problems the game would refuse, so it was not packed for sharing: ...` | Fix them and export again, or tick **Pack anyway** in the options of the file window (the panel at the right). |
| `<zip> already exists. Choose another name, or replace it. / <zip> exists and is not a zip file, so it is not replaced.` | Choose another name, or pick the existing zip in the file window to replace it. Only a zip is ever replaced. |
| `The zip cannot be written inside the item's own folder / ... inside a Steam library` | Save the zip somewhere else. |
| `<folder> has no data folder with files in it: export the project for the game first.` | Export first. |
| `<file> is not a zip file the add-on can open ... / The zip is refused: ... is password protected / ... is a symbolic link / ... would unpack to more than ...` | The add-on refuses anything unsafe. Ask the author for a plain zip made with **Pack for Sharing**. |
| `The item is refused: it holds files outside the data folder (...). An item may only hold a data folder; the game reads nothing else from it.` | The zip is not an item. Ask the author, or copy its `data` folder in by hand. |
| `The item is refused: '<a>' and '<b>' differ only by capital letters. Windows cannot hold both.` | The zip is made badly. Ask the author. |
| `The check refused <name>: ... (in the Share box, with Install Anyway)` | Read the reasons. **Install Anyway** installs it as it is, and it may not work. |

## The game shows nothing, or the old thing

| What you see | What to do |
| --- | --- |
| `Nothing in the add-on says anything, and the game shows the old thing.` | 1\. Close the game and start it again from the test copy.<br>2\. Check that you started the game from the **test copy**, not from the Steam game.<br>3\. Look at the **Installed here** list: is the item there?<br>4\. For a creature with a new name, look in the creature creator of the right game (base game, Insect Invasion, Tellurian).<br>5\. A thing with a name of its own shows only where a map places it.<br>6\. A file of that name in a mod's folder or in `DataMOD` comes first (the install says so).<br>7\. Read `warnings.log` in the test copy's folder. |
| `The creature is not in the campaign.` | In the campaign the army builder offers only the animals the campaign has unlocked. It shows in skirmish and in the free creature creator. |
| `The game does not start from the test copy.` | The Steam version starts only while Steam is running. Make sure Steam is open and signed in. On Linux, `IC.exe` is a Windows program, so it must run through Wine or Proton (see [Start here](start.md), “Start the game”). |

## Making a report

Open an issue on the add-on's page and include:

- your operating system, your Blender version and the add-on's version (**Edit > Preferences > Add-ons > IC Blender**);
- what you did, step by step;
- the exact message, copied from the **Info** editor;
- the last lines of `warnings.log` in the test copy's folder, if the game was involved.

---

[Contents](README.md) · Previous: [Check, export, install, remove and share](ship.md) · Next: [Finding your way around a creature](tour.md)
