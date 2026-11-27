[Contents](README.md) · Previous: [Effects, events and sounds](effects.md) · Next: [Troubleshooting](trouble.md)

# Check, export, install, remove and share

Every kind of work ends the same way: in the **Game** panel of the **IC Game** tab. This chapter goes through its four boxes, in the order you use them. You need a test copy of the game ([Start here](start.md)).

## 1. Check

Check looks for what would stop the game loading your work. It writes nothing.

- **A creature**: press **Check the Creature**. It runs the model checks and also what **Export Creature for the Game** would refuse: the numbers, the motions and the events. A creature that passes Check is one the export accepts. A creature that replaces a game animal is checked as that replacement.
- **A structure, a prop, a worker, a character or an effect**: press **Check**. It tests what **Export Entity Folder** would write: the model against the game's loading rules, the blueprint against its rules, and the files as the Steam Workshop would see them. It also says if the model's file was changed on disk after you opened it. A project started from one of the game's things also has **Check as replacing ...**.

The box then says “No problems” or lists the problems. **Errors** (red) mean the game would refuse or mis-load the files. **Warnings** are advice. For a creature the box shows the first three problems, and the **Info** editor lists them all. After you change something, press Check again: the box shows the result of the last Check.

## 2. Export for the Game

Export writes a folder called an **item**. It holds the files the game reads, all with lower-case names, inside a folder called `data`.

- **A creature**: **Export Creature for the Game**. A window asks for a name, a display name, a description and a picture (see [A new creature from a game animal](newcreature.md) and [Replace one of the game's animals](replace.md)). The item is a folder named after the creature, beside your model (or in the **Folder** you choose), under `ic_items`. It holds the blueprint with your model inside it, the numbers, the limb scales, the creature creator's picture and a list of the files.
- **Anything else**: **Export Entity Folder**. A file window asks where to make the folder. It writes the model, the blueprint, new textures, your effects and sounds, a list of files and a README.

Export refuses a game folder or a folder inside Steam's, so it cannot damage your game. It also refuses to write into a folder that holds somebody's other files. It replaces only an earlier export of the same item. If export refuses, the message says why. See [Troubleshooting](trouble.md).

After a good export the box shows **Last export** with the item's name. The item is also the folder the next step uses.

## 3. Install into Test Game

1. Look at the box **3. Install into Test Game**. Near the top of the box it shows your test copy's folder, and either “A copy of the game made here on ...” (a copy this add-on made) or “A folder you set yourself”. If it shows a red message instead, fix that first: set **Test game folder** in **Edit > Preferences > Add-ons > IC Blender**, or press **Create Test Game Copy** (see [Start here](start.md)).
2. The line **Installs: ...** shows which item it will use: the one from your last export. To use another, set **Item folder** to an item folder (a folder with a `data` folder in it). The line **Last export** shows when the item was last written.
3. Press **Install into Test Game**.

Under box 3 is a separate box, **Installed here**. It lists what you have installed in the test copy. A fresh copy of the Steam game may also say, for example, “7 more installed by other tools (not touched here)”. Those are Steam Workshop items that are already in the game. Leave them.

First the add-on checks the item the way the game's own rules would. If it finds something the game would refuse, nothing is installed, and the box (red) lists the reasons in plain words, with a button **Install Anyway** that installs it as it is. The item may not work. Other findings are only advice. They appear as yellow lines that start with “Advice:” after the install message (for example “the Steam Workshop would not take this for sharing”). The game still loads those files, so they do not stop the install. Only the red box with **Install Anyway** is a refusal.

Then it copies the item's files into the copy's `Data` folder. Loose files there come before the files in the game's archives, so the game uses yours. It also:

- keeps every file it replaces, so that **Remove** can put it back, byte for byte;
- writes an uninstall list in the game folder (`<item>.lst`) and its own undo data in a folder called `ICBlender` there. Leave both alone;
- deletes the game's saved picture of every model you replaced, so the game draws your new model (“N saved model(s) of the game cleared”);
- for a creature with a new name, adds the name to the creature creator's list of every mod in the copy (see [A new creature from a game animal](newcreature.md)).

You should see “Installed '&lt;name>' into &lt;folder>: N files. Start the game from that folder ...”. If files were replaced, or saved models cleared, or a creature added to the creature creator, those facts come in the same sentence after the number of files. The list **Installed here** now shows the item. Items installed by other tools are counted but never touched.

Notes the message may add:

- “N file(s) of this item are also in the folder of the mod ... The game keeps loading that copy there, not the new file.” A file in a game mod's own folder, or in a folder called `DataMOD` in the game folder, comes before a loose file in `Data`. Play without that mod, or remove that file from the copy.

Ignore anything about `DataMOD` unless the install message mentions it. If it does, play without the mod that message names.

## 4. See it in the game

1. If the game is running, close it. It reads an object's files when it loads it.
2. Start the game from the test copy: run `IC.exe` in that folder. On Linux that needs Wine or Proton, and the Steam version starts only while Steam is running. [Start here](start.md) has the details, under “Start the game”.
3. Look for your work. The **Next:** line in the Game panel tells you what to look for when the item has a name of its own (a structure, a prop, a worker with a new name shows only where a map names it).
4. If something is missing or wrong, read `warnings.log` in the test copy's folder.

## 5. Remove it

1. In the list **Installed here**, press **Remove** next to the item.
2. A window asks “Remove '&lt;name>' from the Test game?” Confirm.

The message says, for example, “Removed 'wolf': 5 files deleted, 0 put back as they were”. “0 put back” is right when you replaced one of the game's own things. The game's original lives inside its archive and shows again as soon as your files are gone. “Put back” counts only loose files that were in the copy before the install.

Every file the install wrote is deleted, and every file it replaced is put back as it was. The creature lists go back to what they were, or are deleted with the folders the install made, if the mod had none. Folders that were there before, or that hold something else, stay. If you edited a creature list by hand after installing, **Remove** leaves it and keeps the original in `ICBlender/kept/roster/`. A file you changed by hand after the install is also left alone, with its original kept in `ICBlender/kept/<item>/`. If a mod's folder was deleted after the install, Remove still works and says the folder is gone.

## 6. Share your work: Pack for Sharing

1. Export, as above.
2. In the box **4. Share**, press **Pack for Sharing**. A file window opens. It proposes the item's name with `.zip`, beside the item's folder. When you pack again after a fix, choose the same name: an existing name shows in red, and the new zip replaces the old one. The add-on replaces only a zip file. It refuses to overwrite any other kind of file.
3. If the check found problems, the zip is not written. Tick **Pack anyway** in the options of the file window to pack it as it is. The options are in the panel at the right of the window. (If you do not see it, press the small gear button at the top right.)
4. Press the confirm button. You should see “Packed N files into ... Send that zip file to other players”.

Unzipping gives one folder named after the item. Inside it:

- **README.txt**: what the item is, how to install it with and without the add-on, what it replaces, and the list of files;
- **data**: the files the game reads, all in lower case. The game's archives are case sensitive, and every path the game asks for is lower case;
- for a creature with a new name only, **retail_roster**: a creature list for each mod you have, for players who do not use the add-on. See below.

## 7. Install something someone else made

1. Make a test copy if you have none.
2. In the box **4. Share** press **Install From Zip** and choose the zip. Or press **Install From Folder** and choose a folder that has a `data` folder in it. A folder you unzipped from a shared zip works too: choose the folder that holds `README.txt` and `data`. Its README and its `retail_roster` folder are left alone.

The add-on accepts only files inside `data`, makes names lower case, and refuses anything that tries to write outside its folder, a shortcut, a locked (password) zip and a zip that is far too big. Then it runs the same check as for your own work. If the check refuses it, nothing is installed, and the Share box says “The check refused ...” and lists why, with an **Install Anyway** button that reads the same zip again and installs it as it is. It may not work. The add-on keeps no copy of a refused item. **Remove** takes an installed item out.

## Players who do not use the add-on

The game finds a new creature's files by their place. But its creature creator shows only an animal whose name is on a list it reads when it starts. With the add-on, installing adds the name for you. For a player without the add-on, the zip's `retail_roster` folder has one list for each mod you have, made from that mod's own list plus the new creature. So a mod that hides animals on purpose keeps hiding them. The README in the zip says how to copy a list into place, and the one line to add if the mod's folder already has a list.

Players can install without the add-on like this: close the game, copy the whole game folder somewhere else (not the Steam folder), unzip the file, copy the files of its **data** folder into the game's `Data` folder (the folder that holds `IC.exe` also holds `Data`), and merge when asked. **On Linux the game's folder is called `Data` with a capital D.** A file manager does not merge a folder called `data` into `Data` for you: copy the files inside `data` instead, or you end up with a second folder the game never reads. Then start the game from there. To undo it, delete the files listed in the README.

> [!IMPORTANT]
> **Limits**
>
> A mod that keeps its own list in its own folder keeps using that list, so the new creature stays off the list in that mod unless the list is changed. The zip tells you when your game has such a mod. In the single-player campaign, the army builder offers only the animals the campaign has unlocked. A new creature shows in the free creature creator and in skirmish, not in the campaign. This is how the game decides it. The item cannot change it.

## Windows

Windows support is experimental. If something does not work on Windows, the message tells you what the add-on tried. Please report it.

---

[Contents](README.md) · Previous: [Effects, events and sounds](effects.md) · Next: [Troubleshooting](trouble.md)
