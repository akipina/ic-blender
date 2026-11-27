[Contents](README.md) · Previous: [A new creature from a game animal](newcreature.md) · Next: [Gameplay numbers](numbers.md)

# A creature from your own mesh

You have a model made somewhere else: a sculpt, a scan, a file from another program. You want it to be a creature in the game. There are two routes. They give different results, so choose before you start.

| Route | What you get | Can the game combine it? | Can you export it for the game? |
| --- | --- | --- | --- |
| **Retarget** (**Retarget Creature to Mesh**, **Landmark Retarget**) | One of the game's animals, with its surface reshaped to match your mesh. It keeps the animal's skeleton, limbs, sockets, skin and animations. | Yes | Yes |
| **Fit** (**Fit Patches to Mesh**) | A creature made of patches that follows your mesh, with a template's skeleton and limbs. The shape only. | No: it has no connection rings | No: the export refuses it |

If you want a creature that plays in the game, use **Retarget**. **Fit** is for when you only need your shape as a creature made of patches, for example to look at it, to measure it, or to save a model file. The rest of this chapter goes through Retarget from your mesh to the game, and then through Fit.

## The Retarget route

### 1. Bring your mesh into Blender

Use Blender's own import (**File > Import**) for glTF, OBJ, FBX or STL. For Retarget, your mesh must be one solid piece with no loose floating parts. Split seams are fine. Put it where you can see the animal you will start from. If the add-on refuses it later, select the mesh, press <kbd>Tab</kbd>, then choose **Mesh > Clean Up > Merge by Distance**. If your mesh is several pieces on purpose, use the Fit route below instead.

### 2. Start from the animal that is closest

1. Choose **File > New > Impossible Creatures Project...**. Keep **Type** on **Creature**. (The choices are in the panel at the right of the file window. If you do not see it, press the small gear button at the top right.)
2. In **Start from**, choose the animal whose body plan is nearest to your mesh: how many legs, whether it has wings, a tail, a long neck. Keep **A new creature of my own**, and give it a name (see [A new creature from a game animal](newcreature.md)).

This choice matters. Retarget changes the surface only. The animal's limbs, the sockets where parts join, its skeleton and its walk stay as they are.

### 3. Retarget

1. Select the creature (not your mesh). Open the **IC Model** tab and find the box **Tools**. (If you select your mesh first and the creature last, so that the creature is the active one, the next window already has your mesh filled in.)
2. Press **Retarget Creature to Mesh**. A small window opens.
3. In the window, set **Target Mesh** to your mesh. Leave the other settings as they are: **Pin Rings**, **Scale to Target**, **Keep Symmetry** and **Move Bones** are on. Press **OK**.

You should see the creature take your mesh's shape. If you do not like the result, press <kbd>Ctrl</kbd>+<kbd>Z</kbd> once. It undoes the points and the bones together. If the message says “a creature is one closed skin”, your mesh has loose parts: see step 1.

If the limbs land in the wrong places (they go to the nearest part of your mesh), use **Create Landmarks** and **Landmark Retarget**: move the little markers it makes onto the matching places of your mesh. If your mesh lacks a part the creature has, use **Trim Stray Surface**. The chapter [Retargeting](retarget.md) explains each.

The skin of the creature stays the animal's skin, stretched over the new shape. If your mesh has a picture of its own, **Transfer Texture** (see [Textures](texture.md)) copies it onto the creature.

### 4. Check, combine, export, install

1. In the **IC Game** tab, **Game** panel, press **Check the Creature**.
2. Try **Combine Preview** with another animal ([Finding your way around a creature](tour.md)). Press **Extract Stock Creatures** once first, at the top of the **IC Model** tab, so that the list has the game's animals. If the list is still empty, the add-on has not found your game: see [Start here](start.md).
3. Follow the steps in [A new creature from a game animal](newcreature.md) from “Export it for the game” on.

> [!NOTE]
> A note about a ring (a socket where parts join) pointing sideways is advice only. An error about a ring wound the wrong way is fixed with **Pick Ring** (in **Boner > Connections**).

## The Fit route

### 1. Bring your mesh into Blender and select it

Import it as above and select it in Object Mode. A mesh made of several separate pieces is fine: a body with eyes and teeth, for example. Press <kbd>Ctrl</kbd>+<kbd>A</kbd> and choose **All Transforms** first, or the fit ignores the mesh's place and size (the message says so). Save your `.blend` file first, or set **Output .sgm** (below), so that you know where the result goes. A fit of an unsaved file goes to Blender's own data folder, which is hard to find.

### 2. Fit

1. With the mesh selected, open the **IC Model** tab. At the top of the **IC Blender** panel, press **Fit Patches to Mesh**. (The button appears only while an ordinary mesh is selected.) **It runs at once, with the standard settings.**
2. To change a setting, open the panel at the bottom left of the 3D view (or press <kbd>F9</kbd>). Changing a setting fits again, and writes another file (`MyMesh_fit_2.sgm`, then `_fit_3.sgm`). The settings:
   - **Patch Budget** (300): how many patches. More follow the mesh closer and take longer.
   - **Layout**: *QuadriFlow* remeshes a copy. *Own Quads* uses your faces as the patches.
   - **Skeleton Template (.sgm)**: the creature to borrow the skeleton and the materials from. Empty uses the game's wolf.
   - **Output .sgm**: where to save. Empty means next to your `.blend` file.
   - **Fit Separate Pieces** (on): fits each separate piece on its own and joins them into one creature. The body gets the most patches. Each small piece gets a share by its size, at least 6. Untick it and a mesh of several pieces is refused.
   - **Split into Limbs** (on): shares the patches among the template's limbs and bones.
3. Wait. Half a minute is usual for 300 patches. If a warning says the patch budget is over 263, the creature will load but may not combine: lower **Patch Budget**.

You should see a patch creature with the template's skin. The message gives the fit error and says whether symmetry was kept. Inside the Tools box a note says “Made by Fit Patches to Mesh: shape only. No connection rings, so the game cannot combine it.”

### 3. Move the skeleton into the body

The bones stay where the template had them. Press **Move Skeleton to the Body** (it shows under **Tools** after a fit) to move them into your new shape. You can press it once per fit. Then select the rig, press <kbd>Tab</kbd> and nudge any bone that sits off. The export writes the new bone positions.

### 4. What you can do with it

Check it with **Check the Creature** (**IC Game** tab, **Game** panel; the same check is the button **Validate (will it load?)** in the Tools box of the **IC Model** tab), shape it further, and save the model with **Export Model**. **Export Creature for the Game** refuses it. The check says so in advance: “This creature has no connection rings, so the creature creator cannot use it and Export for the Game refuses it. Use Retarget Creature to Mesh (IC Model tab, Tools) to get a creature with rings.” If you press the export button anyway, you see lines such as “connection ring 'head' missing” and then “Export for the Game refused: nothing was written”. They mean the same. For a creature that plays in the game, go back to the Retarget route.

The chapter [Fitting a mesh](fit.md) has the numbers: how many patches a creature may have, and what happens above the limit.

---

[Contents](README.md) · Previous: [A new creature from a game animal](newcreature.md) · Next: [Gameplay numbers](numbers.md)
