[Contents](README.md) · Previous: [Structures and props](structures.md) · Next: [Effects, events and sounds](effects.md)

# Gatherers and characters

A **gatherer** is a worker, such as the henchmen who collect coal. A **character** is a person of the story, such as Rex. Their models are one skinned body on a skeleton, and it bends when they move. A building, by contrast, is made of rigid parts ([Structures and props](structures.md)). Almost everything works the same way for both. This chapter shows the walk-through for a worker and points to the structures chapter for the tools they share.

## 1. Start the project

1. Choose **File > New > Impossible Creatures Project...**. A file window opens. The choices are in the panel at the right of it. (If you do not see it, press the small gear button at the top right of the window.)
2. Set **Type** to **Gatherer / character**.
3. Open **Start from** and choose **Henchmen**. (The list holds the game's workers and characters. **Another .sgm file...** starts from a model file of your own.)
4. Keep **Animations** on **Keep its own**. A worker with no animations does not walk or work.
5. Go to an empty folder of your own. The file name starts as `NewCreature.sgm`: replace it with a name of your own, not the game's name (not `henchmen`). Then press **New Impossible Creatures Project** at the bottom right.

You should see the worker and its skeleton, and the message “Started the gatherer / character project ...”. In your folder are the model (`.sgm`) and the blueprint (`.ebp`).

## 2. Change it

- **The shape.** Select the body and press <kbd>Tab</kbd>. Use any mesh tool. Weights decide which bone moves which vertex. Fix them in Weight Paint. A vertex with no weight goes to the root bone, which usually looks wrong, so check the places you changed.
- **A new look for the whole body.** Press <kbd>Tab</kbd> to leave Edit Mode first: these buttons work only in Object Mode. Select your own mesh, then the worker's body. In the **Parts** panel press **Replace Part With Selected Mesh**, or press **Use This Mesh as the Model**. Leave **Copy the part's bone weights** (or **Copy the model's bone weights**) ticked, so that your mesh bends with the skeleton. Each button opens a small window: press **OK**.
- **The skin.** **IC Material**: repaint, replace the image, change opacity or team colour. See [Structures and props](structures.md), “Textures and materials”.
- **The skeleton.** Rename, move, add, re-parent and delete bones as in “The skeleton” of that chapter. The game looks up the head bone by its name, which Blender shows as `bip01 head` (the game does not tell capital and small letters apart), so keep that name.
- **Attachment points.** Markers are the points where things attach. On the henchmen, `Point_R` is the right hand, `Rays_heal` (where the heal effect appears) is on the right hand, and `Point_Coalbasket` (the basket they carry) is on the neck bone. Move them in the **Markers** panel ([Structures and props](structures.md), “Attachment points”). On an untouched henchman the panel lists `head` as a name the blueprint uses that the model lacks. That is the original's own state: leave it.
- **Numbers**: [Gameplay numbers](numbers.md). **Animations**: [Animations and motions](anim.md). **Effects and sounds**: [Effects, events and sounds](effects.md).

## 3. Check, export, install

1. Open the **IC Game** tab and the **Game** panel. In **1. Check** press **Check**.
2. In **2. Export for the Game** press **Export Entity Folder** (also under **File > Export**). Choose a folder, and then **Export as** (in the panel at the right of the file window). Choose **Replace the original ...** to change the game's own worker, or **A new name of my own** for a worker that appears only where a map or script places it. For a new name, type a file name that is not the game's (not `henchmen`): with the game's own name, the files take the place of the original even under this choice.
3. Leave **Install into the game after export** ticked in the file window, or press **Install into Game** in **3. Install into Game**. Start the game ([Start here](start.md), “Start the game”).

After changing a worker, check in the game that it still walks, gathers and carries correctly.

Replacing the henchmen changes every henchman in every army of that copy. In a game with other players, everyone needs the same files.

The step-by-step details of the export window, the new-name and replace choices, and what the files hold are in the chapter [Structures and props](structures.md), section “Check, export, install”. The refusal messages are in [Troubleshooting](trouble.md).

> [!IMPORTANT]
> **Limits**
>
> Models that other models include by file name (cut scenes include Rex, for example) are not updated when you rename bones. Rename bones only in a copy saved under a new name. A cut-scene model that is one skinned mesh cannot get or lose parts.

---

[Contents](README.md) · Previous: [Structures and props](structures.md) · Next: [Effects, events and sounds](effects.md)
