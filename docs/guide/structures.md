[Contents](README.md) · Previous: [Animations and motions](anim.md) · Next: [Gatherers and characters](gatherers.md)

# Structures and props

A structure is a building, such as the Lab, the Radio Shed or the Anti-Air Tower. A prop is scenery or a resource spot, such as a coal bin or a barrel. Both are made of **parts**: separate meshes that sit on the bones of a skeleton. In this chapter you change one of them, try it in the game, and learn what each tool does. Workers and story characters work the same way and have their own chapter, [Gatherers and characters](gatherers.md). You need the setup from [Start here](start.md).

> [!NOTE]
> **Quick try:** do step 1, move one point of one part (step 3), then do step 9. Read the other sections when you need them. The blueprint's other fields (the footprint and so on) are in [The Blueprint panel](blueprint.md). Hit points and costs are in [Gameplay numbers](numbers.md). This chapter also needs more Blender than the first ones: Weight Paint, Shape Keys and Texture Paint. The Blender manual (docs.blender.org) explains them.

## 1. Start the project

The game cannot load a building built from nothing, so you always start from a copy of one of the game's own.

1. Choose **File > New > Impossible Creatures Project...**.
2. Set **Type** to **Structure / prop**.
3. Open **Start from** and choose a model, for example **Lab (structures)**. The list is long and sorted by file name. Each entry reads “Name (folder)”: `structures` are buildings and `nature` is scenery, for example **Coal Bin (nature)**. Type in the list's search box to find one. The last choice, **Another .sgm file...**, starts from a model file of your own. If the list shows only that choice, the add-on has not found your game: set the **Game folder** in the preferences. (These choices are in the panel at the right of the file window. If you do not see it, press the small gear button at the top right of the window.)
4. **Animations**: **Keep its own** or **None**. (**Borrow** works only for creatures. Borrowing clips later is in [Animations and motions](anim.md).)
5. Go to an empty folder of your own. The file name starts as `NewCreature.sgm`: replace it with a name of your own, not the game's name (not `lab`). Then press **New Impossible Creatures Project** at the bottom right. The model (`.sgm`) and its blueprint (`.ebp`) are copied there and opened.

You should see the building in the 3D Viewport, and the message “Started the structure / prop project ...”. If the message says the file is already there, choose another name. Nothing is overwritten.

## 2. What you see

- One mesh object for each part, at full detail, with the game's textures.
- An armature called `<name>_rig`, if the model has a skeleton. Its bones are also vertex groups on the parts. A model with no skeleton (the Radio Shed) has no rig box and no **Motions** panel. The IC Blender panel then says “scene object: 3 part(s)”.
- The animations, as Actions on the rig ([Animations and motions](anim.md)).
- Attachment points, shown as small arrows in a collection called **Markers**.
- Damage states, as shape keys on the parts that have them.
- In the **IC Model** tab: the **IC Blender** panel (model information, the rig box, and **Damage States**), **Parts**, **Markers** and **IC Material**. In the **IC Game** tab: **Blueprint**, **Motions**, **Effects and events** and **Game**.

## 3. Change the shape

Select a part, press <kbd>Tab</kbd> and use any Blender mesh tool. These are written when you export:

- moved, added or deleted geometry, including extrude and subdivide;
- UVs, seams and flat shading;
- bone weights (a vertex with no weight goes to the root bone);
- faces moved to another material slot, a new one included;
- moving, turning or scaling a whole part in Object Mode. The part is written where it is, relative to the rig. Moving the rig alone moves every part. Move the rig and its parts together to keep the model as it was.

The limits are the game's. A part holds at most **12,288 vertices**, counted after splitting at UV seams and hard edges, and 65,535 triangles. If the export refuses a part, reduce its detail (for example with a Decimate modifier, applied). Blender shading settings the game has no use for (roughness, extra nodes) are not saved. The export names them.

## 4. Parts: add, remove, replace

Open **Parts** in the **IC Model** tab (select a part first). It says how many parts the file has and how many are new. **Press Tab to leave Edit Mode first: the buttons below work only in Object Mode, and are grey in Edit Mode.** Each of them opens a small window with options. Press **OK** in it.

- **Add a part.** Make or import the mesh. Select it, then select the model's part last, so that it is active. Press **Make Part of Scene Object**. **Follows bone** makes the new part move with one bone (a fan blade, a door). Leave it on *none* and weight it yourself in Weight Paint: one vertex group at full weight follows that bone, several make it bend like a character, none keeps it still. **Shows like** makes it appear and disappear with another part in every animation (a build stage, a damage piece). Otherwise it shows in every animation. A mesh parented to the rig, or to the model's empty, counts as a new part too. To undo, select it and press **Leave Out**. **Give the new part its picture before you press Make Part:** a new part has no **IC Material** panel. Give the mesh a normal Blender material with an Image Texture (Material properties). The export turns it into a game shader (and warns “new shader(s): ...”). To repaint it later, use Blender's Texture Paint. A mesh with no material gets the model's own materials.
- **Remove a part.** Delete its object, and tick **Remove parts deleted in Blender** when you export. Without the tick, a deleted part keeps its original shape.
- **Replace a part.** Select your mesh, then the part last. Press **Replace Part With Selected Mesh**. The part keeps its name, its place in the model and its bone. Your mesh is used up (tick **Keep the selected mesh** to keep it). Bone weights are copied from the old mesh to yours. **Materials** chooses whether the new faces take the part's materials or keep your own.
- **Replace the whole model.** Select your mesh, then any part. Press **Use This Mesh as the Model**. The mesh is scaled to fit inside the model's size and put where the model stood. It becomes the largest part. The other parts are removed when you export, so moving parts (fan blades, doors) are gone. Turn your mesh to face the way the model faces first.

Rules: a new part's name must differ from every part's, ignoring capital letters. It follows only bones the model has, or bones you add. A model with a single mesh is turned into a group of parts when you export, so that it can hold more. A cut-scene model that is one skinned mesh cannot get parts, and neither can parts stored as separate blocks (projectiles and effect models). The mesh you use is taken as you see it, with its modifiers applied.

## 5. Textures and materials

Select a part of the original model (not a part you added: see step 4) and open **IC Material** (**IC Model** tab). It shows the material of the active slot: its game shader, its texture and its size.

- **Repaint a texture.** Paint on the part's image in Texture Paint, or in the Image Editor. When you export, the changed texture is written as a new texture file, and only that material changes in the model.
- **Use another picture.** Press **Replace Image** and choose a picture. If its sides are not powers of two (2, 4, 8 ... 1024), it is scaled to the nearest size the game takes. Untick “Scale to a size the game takes” (in the options of the file window, at the right) to be told instead. The game takes only sizes up to 1024.
- **Add a material.** Press **New IC Material**. It gets a plain 256 by 256 texture to paint or replace. In Edit Mode the selected faces get it at once. Otherwise select faces and press **Assign** in Blender's Material properties. The export takes the game settings from the model's first shader, but none of its textures, so the new material shows only its own texture.
- **Opacity.** The **Opacity** slider sets how solid the game draws the material (1 is solid). Cut-outs in the texture stay as they are. Glowing effects that add their colour may look different.
- **Team colour.** Tick **Team colour**, then press **Team Mask**. You get a mask image to paint: white where the player colour goes, black elsewhere. A material that already has team colour starts from the game's own mask. You paint the team area in player 1's green, and the export makes the other colours. The buttons **1** to **6** under **Preview** show a player's colours in Blender only. Press **1** to go back.
- A copy of a material gets its own new shader at export. The original keeps its own.

Where the files go: **Export Entity Folder** writes new textures into `data/art/textures` in the export folder. The game shares texture names between all models, so a changed texture always gets a new name made from your model and the material, in lower case (the player-colour copies add `_tc01` to `_tc06`). A game texture is never overwritten. If nothing changed, the model is written byte for byte as it was, with no texture files.

Not used by the game: two-sided materials, specular and reflection settings (they are kept from the original and cannot be edited), and Blender's roughness and metallic. The team colours the add-on makes can differ slightly from those of the game's own textures. Check them in the game.

## 6. The skeleton

Select the rig and press <kbd>Tab</kbd> for Edit Mode. You can:

- **rename** a bone (double-click its name in the Outliner, or <kbd>F2</kbd>);
- **move or turn** a bone (<kbd>G</kbd>, <kbd>R</kbd>);
- **add** a bone (<kbd>Shift</kbd>+<kbd>A</kbd>), parent it (select the new bone, then <kbd>Shift</kbd>-click its parent last, press <kbd>Ctrl</kbd>+<kbd>P</kbd> and choose **Keep Offset**) and give it weights in Weight Paint;
- **re-parent** a bone the same way;
- **delete** a bone with **Armature > Delete Bones, Merge Weights**. It hands the bone's weights to its parent first, so the model looks in Blender the way the game will show it. A plain delete (<kbd>X</kbd>) leaves the weights on a bone that no longer exists. If some vertices or a part then follow no bone, the export refuses and says which bone to merge.

What follows automatically: clips you did not edit keep playing as they did. Attachment points on a renamed bone keep pointing at it. A part follows the bone of its vertex group. To re-bind it to another bone, go to Edit Mode and select the part's vertices, choose that bone's group under Object Data Properties > Vertex Groups, then press <kbd>Ctrl</kbd>+<kbd>G</kbd> and choose **Assign to Active Group**. **Export Entity Folder** also updates the blueprint, so effects and sounds placed at a renamed bone follow the new name. The export report lists what happened to the skeleton.

What is refused: two bones with the same name ignoring capital letters, a name longer than 79 characters, deleting or parenting the first bone, and more than 255 bones weighted on one part. The game looks up a few bone names itself (`Bip01 Head`, `nose`, and the tail, neck and spine names). Blender shows bone names in lower case (`bip01 head`), and the game does not tell capital and small letters apart, so keep the name as Blender shows it. Renaming or deleting those is only warned about.

> [!IMPORTANT]
> **Limits**
>
> Some models are included by other models by their file name (cut scenes, and some buildings include the Lab, Rex or other models). Renaming bones in a model like that breaks the models that include it, and the add-on does not update them. Rename bones only in a copy saved under a new name. The export names the game files that include the model. A moved bone is written without scale or shear, because Blender bones cannot hold them.

## 7. Attachment points (markers)

A **marker** is a named point on the model: the tip of a worker's hand, a chimney on a lab, the muzzle of a gun. The game puts an animation's effect and sound at the marker the blueprint names. If the game asks for a marker the model does not have, nothing crashes and nothing warns. The effect plays at the model's feet.

Open **Markers** in the **IC Model** tab (select any part, the rig or a marker).

- The list shows each marker, the bone it is on (or “model space”) and how many events use it. Click a name to select that marker.
- **Move or turn** a marker with <kbd>G</kbd> and <kbd>R</kbd>. Scaling works, but the game then scales the effect too, and the panel warns you.
- **Add** puts a new marker at the 3D cursor. With the rig in Pose Mode it goes on the active bone. Otherwise it sits in model space. It gets a free name like *Point01*. **Rename it at once** to something that is not a bone's name: in the Lab, *Point01* is also a bone, and the check then warns that events naming it find the marker, not the bone.
- **Delete** removes the selected markers. The panel tells you if events still use them.
- **Rename**: at most 126 letters, plain Latin letters, not starting with the digit 0, and not the name of another marker (the game does not tell capital and small letters apart). The game never finds a marker whose name starts with 0: the list shows “not found” next to those.
- **Bone** moves the marker onto another bone, or to “None (model space)”, without moving it.
- **Events** lists the events that place something at the marker. The list is in the **Info** editor.
- A box with a warning sign lists names the blueprint uses that the model does not have. On an untouched model it may list a few (the henchmen list `head`). That is the original's own state: leave it.

Markers you did not touch come out as they were. **Export Entity Folder** also renames a marker in the blueprint's events, so they keep finding it, and tells you about any event that now names something the model no longer has.

## 8. Damage states

Some buildings carry shapes the game blends in as they are damaged: the Lab's tent sagging and collapsing, the coal bin caving in. The add-on calls them **damage states**.

- A part with damage states has **shape keys**: a `Basis` key and one key for each stored shape. Each key is named after a clip that uses it, for example `06 damage_03`. All keys start at 0, so the model looks undamaged. The clips that blend them are Actions on the part's shape keys, on muted NLA tracks.
- Open **Damage States** (a part of the **IC Blender** panel, in the **IC Model** tab, with a part selected). It shows how many damage states and clips the part has. Not every part has them. In the Lab, select the part `Lab_tent` (the tent): it has 11. The part that is selected when the project opens (`Lab_Engine`) says “no damage states on this part”.
- **Preview health**: drag it between 0 and 1. 1.000 is undamaged. Lower it and the panel says which damage clip the game plays at that health (“plays: damage_02”), the part's shape keys take that clip's values, and parts the clip hides are hidden. Set it back to 1.000 to undo the preview. The preview never reaches the export. It does not show the bone movements of the damage clips, or the game's lighting.
- **Change a shape**: select the key in Object Data Properties > Shape Keys, go to Edit Mode and move vertices, or sculpt it.
- **Edit the mesh** as usual. Blender carries the shape keys along, and the export writes every shape over the new vertices.
- **Delete a shape** by deleting its shape key. The clips that used it lose that part of their animation, and the later shapes are renumbered.
- **Add Damage Level** (in Object Mode) adds a shape key and a new clip `damage_NN` that shows it at full strength, as the most damaged level. Shape the key, then export with **Export Entity Folder**. **Adding a level moves the others**: the levels are spread evenly over the damage. Four levels show from 25, 50, 75 and 99.99 percent damage. Five show from 20, 40, 60, 80 and 99.99 percent. The newest level shows only when the building is all but destroyed.
- **Carry Over Damage States**: for a part whose mesh you replaced and that has no shape keys any more, this builds them again from the original file, matching each new vertex to the nearest point of the old surface. Vertices too far away do not move and are selected so you can see them.

A part with damage states is always written with a single level of detail (it does not get simpler at a distance). Check damaged buildings in the game. The export option **Leave damage states out of edited parts** writes edited parts without damage states.

## 9. Check, export, install

1. Open the **IC Game** tab and the **Game** panel. In **1. Check** press **Check**. It tests what the export would write, against the game's rules, and writes nothing. It also says if the model's file was changed on disk after you opened it. A project started from one of the game's things also has **Check as replacing ...**.
2. In **2. Export for the Game** press **Export Entity Folder**. A file window opens. Go to the folder where the export folder should be made (not the game's own folder, and not inside Steam's).
3. The options are in the panel at the right of the file window. (If you do not see it, press the small gear button at the top right.) Choose **Export as**:
   - **A new name of my own**: set **File name** (it starts as `NewCreature.sgm`: replace it) and **Folder in the game**. **Use a name the game does not have** (not `lab`): if the name is the game's own, the files take the place of the original even under this choice. The game shows a thing with a name of its own only where a map or a script asks for it by that name. With a name of your own, nothing in the game changes by itself.
   - **Replace the original ...** (shown with its name, when the project started from one of the game's things): the files take the place of the game's own. Everything that uses it (maps, the build menu, the computer players) gets your version. In a game with other players, every player needs the same files, because the kind, the footprint, the offset and the size of the model are compared when a game starts.
4. Also in the options: **Leave damage states out of edited parts**, **Remove parts deleted in Blender** and **Export anyway** (writes the files even if the check found errors; the game may refuse them).
5. Press **Export Entity Folder** in the file window.

You should see “Wrote the item ...” with the number of files, how many parts were rebuilt and how many were kept as they were. The export folder has a lower-case `data` folder (the model and the blueprint in `data/art/ebps/<folder>/`, new textures in `data/art/textures`) and a README beside it.

If the export says “it is this model's own blueprint ... Export under another name” (see [Troubleshooting](trouble.md)), choose a different folder and a different name. That happens when a change such as a renamed bone, a new part or a changed texture would be applied a second time.

6. In **3. Install into Test Game**, press **Install into Test Game** (see [Check, export, install, remove and share](ship.md)). Start the game from the test copy.

## Seeing a thing with a new name

A thing with a name of its own appears only where a map places it. To see it:

1. Set the add-on's **Game folder** (preferences) to your test copy, so the add-on sees the files you installed.
2. Open one of the game's maps (**File > Import > Impossible Creatures Map from the Game...**). In the **IC Map** tab use **Map > Placed objects > Add object at the 3D cursor...**.
3. Save with **Map > Save > Save ready-to-drop folder...** and copy that folder into your test copy.

Or export again with **Replace the original ...**, and the game's own thing looks like your model everywhere.

---

[Contents](README.md) · Previous: [Animations and motions](anim.md) · Next: [Gatherers and characters](gatherers.md)
