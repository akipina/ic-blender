<!-- Written by build_guide.py from chapters/numbers.html: edit that file, not this one. -->

[Contents](README.md) · Previous: [A creature from your own mesh](mesh.md) · Next: [Animations and motions](anim.md)

# Gameplay numbers

How tough a creature is, how fast it runs, how far it sees, how hard it bites, what it costs: none of these are in the model. They are in a small file of numbers beside it. A creature or building that you export without touching them plays exactly like the one you started from. This chapter shows how to change them, for a creature and then for a building, a worker, a character or a prop.

> [!IMPORTANT]
> **Limits**
>
> Numbers must be the same for every player of a multiplayer game. Changing a number changes the game for everyone who plays with your files. A replaced numbers file taking the place of the game's own has not been tried in the game yet.

## Creatures: the Creature Stats panel

You need a creature project ([A new creature from a game animal](newcreature.md) or [Replace one of the game's animals](replace.md)).

1. Select the creature. Open the **IC Game** tab and click the header of **Creature Stats** to open it. (It starts closed.)
2. Press **Load the Original's Numbers**. The panel fills with the numbers of the animal you started from, never made-up ones. A creature with no original animal (for example one fitted to your own mesh) shows a box **Start from animal** first: type the name of an animal, for example `cheetah`, then press the button.

You should see the line “From ...” with the file the numbers were read from, and then these boxes. (If you see nothing, click the header of **Creature Stats** to open it.)

- **What the game works out**: the creature's **Rank**, **Population**, **Cost** (coal and electricity), **Build** time, **Hit points** and hit points **With armour**. Choose the **Game build** you will play: the original game, Insect Invasion or Tellurian. Each prices a creature in its own way, and the panel says which file it used. If that price script changes a number before it prices (armour above 0.9 is cut, a flyer cannot leap, a creature with no speed gets some), the panel says so under the prices. A creature joined with another animal is priced from both: look at **Combine Preview** for that.
- **Health and armour**, **Speed**, **Sight and size**: the common numbers.
- **Attacks**: damage, time between hits, when in the swing the hit lands, reach.
- **Abilities**: buttons you switch on or off, for example Pack hunter, Stealth, Regeneration, Leap, Charge. An ability the creature you started from does not have is added to its file when you switch it on.
- **All numbers**: every other number. Type part of a name in the search box to find it, for example `speed_max` or `armour`. When one number is left, the panel says what it does and what the game's animals use.

3. Change a number by typing in its box. The circle arrow next to a changed number puts that number back. The circle arrow at the bottom, next to the count of changed numbers, puts all of them back.
4. A number in red is outside what the export accepts. For most numbers that means more than four times the highest value any of the game's animals has. The message under the number also says what the animals use. Fix it, or the export refuses.
5. The bottom line says “N numbers changed”, or “No number changed: the original's file is copied as it is”.
6. Export with **Export Creature for the Game** (**Game** panel). The numbers go into your creature's numbers file. The export report lists what you changed, and what the creature on its own comes to with these numbers.

**Load Numbers Again** reads the original's file again. If you changed numbers, it asks first, because they are replaced.

## Try it: a faster creature

1. Start a project from the cheetah ([A new creature from a game animal](newcreature.md)) and open **Creature Stats**.
2. Press **Load the Original's Numbers**.
3. In the box **Speed**, raise the **Land speed** numbers. (A four-legged animal shows the front and the back legs. Their speeds add up, so raise both. You can also type `speed_max` in the search box of **All numbers**.)
4. You should see the circle arrow appear next to the number you changed, and at the bottom the line “1 number changed” (or 2). The box **What the game works out** shows what the creature now costs.
5. Check, export and install it as in [A new creature from a game animal](newcreature.md), then play it. We have not seen the faster creature in the game. If the number is too big, it shows in red and the export refuses it.

## Limits for creatures

- The rules that say how your creature is resized when it is combined (its limb scales) are copied from the animal you started from. You cannot edit them yet.
- When two creatures are combined in the game, the new creature's numbers come from both of them. This panel is only about your creature on its own.

## Structures, gatherers, characters and props: Gameplay numbers

1. Select the model. Open the **IC Game** tab. In the **Blueprint** panel press **Load Blueprint** if the panel is empty.
2. Open the part **Gameplay numbers** (it starts closed; click its header). Each row shows a number's name and a box to change it. Under a number you have changed, a grey note says “Was X; the usual for this kind of thing is Y”, and a note appears when the number is far from usual or the game would cut it.
3. **Tick the box first** if your project started from one of the game's things. It reads **Use the numbers of &lt;the original's name>** and it starts unticked. Ticked, the list shows the original's own numbers, and you change those. Unticked, the list shows the usual numbers of its kind (what the game gives a thing with a name of its own), which are not the original's.
4. Change a number. The circle arrow next to it puts that number back. Next to the line “N numbers changed” there is one more circle arrow (it has no text) that puts all of them back. **Add a Number** adds a number by name, for one the list does not show.
5. Press **Export Entity Folder**. It writes a numbers file that holds only the numbers that differ from its base.
   - **Replace the original**: the file takes the place of the original's numbers file, and starts from the original's numbers.
   - **A new name of my own**: starts from what the box says. With the box ticked the file is written even if you change nothing. Unticked it is written only when you change a number.

The file builds on the same base file as the original's numbers file does, so nothing is added that the original did not have. The export report says what was repeated from the original.

### Try it: a tougher Lab

1. Start a **Structure / prop** project from **Lab (structures)** ([Structures and props](structures.md)).
2. In **IC Game > Blueprint** open **Gameplay numbers**. Tick **Use the numbers of Lab**.
3. In the list, change **Hit points** (or type `hit` in the search box) to a higher number. You should see a circle arrow and “1 number changed”.
4. Export, install and play as in [Structures and props](structures.md), “Check, export, install”. Choose **Replace the original Lab** to see the new number in the game. We have not seen this in the game.

**Reset** (in the status box of the **Blueprint** panel) reads the files again and throws away the numbers you changed. Changing the kind of thing keeps them.

---

[Contents](README.md) · Previous: [A creature from your own mesh](mesh.md) · Next: [Animations and motions](anim.md)
