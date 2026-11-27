[Contents](README.md) · Previous: [Gatherers and characters](gatherers.md) · Next: [Check, export, install, remove and share](ship.md)

# Effects, events and sounds

Smoke, sparks, beams and the sounds that go with them are not part of a model. The game keeps each **effect** in its own file. An **event** starts it. An event says: “when this animation reaches this moment, start this effect and play this sound, at this point of the model”. The game runs it by itself while the thing animates. A bite with a spark, a footstep with a puff of dust and the glow at a worker's hand when he heals are all events.

This chapter shows how to change an effect, hook it to an animation, add your own sound, and see the result in the game. It works for creatures, workers, characters, structures and props. It also covers a thing the game throws or fires (a projectile) and the small mesh an effect draws (an effect model). You need the setup from [Start here](start.md).

> [!IMPORTANT]
> **Limits**
>
> You cannot hear a sound in Blender, and the effect preview is only a guide. Look at and listen to the real thing in the game.

## Words used here

- **Blueprint**: the file that tells the game how the model behaves: its animations, sounds, effects and numbers.
- **Clip**: one animation of the model (a walk, a bite).
- **Marker**: a named point on the model where an effect or sound happens (a hand, a muzzle).
- **Key frame**: one stage of an effect's life (its start, its middle, its end).
- **Item**: the folder the export makes. It holds the files the game reads.
- **Projectile**: something the game throws or fires. **Effect model**: a small mesh an effect draws.
- **Mono**: a sound with one channel. **16 bit**: a common sound quality. A sound that comes “from a place in the world” must be mono.

## Try this first

1. Start a project and select it (see step 1 below). Open **IC Game > Effects and events** and press **Load events** in **Events**.
2. Press **Copy a game effect**, search for `blue sparks_ring` and choose it. It is copied into your project with `my_` in front of its name (the new name is shown in the list **Effects used**).
3. Choose an event in the list. In its **Effect** box, type the new name (or search for it with the magnifier: your own effects come first).
4. Export, install and play ([Check, export, install, remove and share](ship.md)).

## 1. Open the Effects and events panel

1. Start a project for the thing (a creature: [A new creature from a game animal](newcreature.md); a worker, structure or prop: [Gatherers and characters](gatherers.md), [Structures and props](structures.md)). Select it.
2. Open the **IC Game** tab and the panel **Effects and events** (it starts closed; click its header). It has the parts **Events**, **Sounds**, **Effects used**, **Effect editor** and **Effect preview**.

If you do not see **Effects and events**, click the model's mesh or skeleton in the 3D Viewport. The panel is hidden when nothing is selected, or when the selected object is a marker or a camera. For an Effect model project only **Effects used** and **Effect editor** show.

An effect on its own has no animation to hook to. For a projectile or an effect model, start the project with **Type** set to **Projectile** or **Effect model** (see the end of this chapter).

## 2. Look at the events

1. Open **Events** and press **Load events**. A creature that you started from a game animal shows that animal's events. A creature you started from **Default skeleton** starts with the cheetah's events for the clips it has.
2. Choose a clip in **Clip** to see only its events.
3. **Add** makes an event for that clip at the playhead. Choose the clip first: if you do not, Add uses the model's first clip. The new event is called `NewEvent`; rename it in **Name**. A model with no clip needs a clip name typed in the **Clip** box first, because an event runs only while the clip of its name plays. **Copy** and **Delete** do what they say.
4. Choose an event and fill in its box:
   - **Name**: a label.
   - **Clip**: the animation it belongs to.
   - **Time**: how far into the clip the event happens, from 0 to just under 1. 0 is the first frame and 0.5 is halfway. An event at 1 never runs, so the panel does not offer it. The clock button sets the time from the playhead. **Do not press the rewind button on an event that plays a sound**: it makes the event run when the clip starts, and a sound on such an event loops.
   - **Marker**: where on the model it happens. Type the marker's name, or press the magnifier and choose it from the list of the model's markers and bones. (The small axes button does not work.)
   - **Effect** and **Sound**: the magnifier searches the game's effects and sound files. Your own effects and sounds come first.
5. Fields the box has no row for stay under **Other fields**, as they are. **Add field...** adds one.

An event with no clip never runs. The box shows a red note, and the export lists it.

The edits are written into the blueprint when you export: **Export Creature for the Game** for a creature, **Export Entity Folder** for anything else. Only the events you changed are different in the file. If you never opened the box, the thing exports exactly as before.

## Copy events from another animal

To use another animal's events instead of the ones you started with, press **Copy events from another animal**, choose a game animal, and its events are copied for the clips your creature has. An event of a clip you do not have could never run, so it is left out. Edit them afterwards as you like.

## 3. Copy an effect and change it

A game effect cannot be edited in place. You copy it into your project first.

1. Open **Effects used**. It lists the effects your events name and the effects those start. Each is marked *project* (your copy), *game* (the game's own) or *missing*. A game effect can be looked at in the editor, but only copied until it is yours: press **Copy to my project** in **Effect editor**, or **Copy a game effect** above.
2. Press **Copy a game effect** and search for one, or press the copy button next to a game effect in the list. The copy brings the effect, the effects it starts, and their pictures and meshes into your project's folder `<your project>_fx/data`. That is a folder named after your project's `.sgm` file, with `_fx` added, next to it (for example `my_lab_fx/data`). The first grey line of the panel shows the path.
3. Look at **Name prefix** (`my_` unless you change it). It goes in front of every copied name, so the game's own effects stay as they are. **If you empty it, the copies keep the game's names and replace the game's effects.**
4. Give an event your copy's name in its **Effect** box. The game plays your copy only where an event names it.

**New** makes an effect of a type (spray, ring, beam, trail, combo or conditional) at the game's own defaults, or from a game effect of the same type.

A game effect whose name has a space (for example “blue sparks_ring”) is copied with an underscore instead, because an item cannot hold a file name with a space. The effects that start it follow. A name you type for a new effect gets the same change.

Two special kinds: a **combo** effect starts all the effects it names at once. When the first of them has ended, the game stops the others: a spray stops making particles, a looping effect or a trail ends. A ring or a beam that does not loop plays out. A **conditional** effect also starts both of its effects, so edit it as a pair of effects.

## 4. Retune it: the Effect editor

1. Press the pencil next to an effect. It opens in **Effect editor**.
2. Change the rows. Every property has the control of its kind: a number (with the range the game accepts in the label), a tick box, a colour, a picture or mesh (type its name, or press the folder button to copy a file from your disk into the project; the small cube button beside **Mesh** uses your own model), the blend mode, or the name of another effect (type it, or press the magnifier to choose).
3. Each change is written to the effect's file at once. A value the game would silently ignore (out of range, wrong count) is refused, and the reason is shown.
4. The **x** next to a row takes the property out of the file, so the game uses its default.

Spray, ring and beam effects change over their life. **Key frame** chooses which key the rows show. **Add** inserts one between its neighbours, **Remove** deletes one and **Move** changes its time (0 is the start of the effect, 1 its end; the first and last keys stay). **Duplicate under a new name** copies the effect shown. **Write effect files to a folder** writes the project's effect files, pictures, meshes and sounds into `<your folder>/data/`, without the model.

The pictures inside a game's 3D effect mesh stay the game's own. You cannot change them here.

## 5. Your own sound from a WAV file

1. Open **Sounds** (it starts closed; click its header) and press **New Sound From WAV**. A file window opens. Pick a `.wav` file.
2. Set the options in the panel at the right of the file window (open it with the small arrow, or press <kbd>T</kbd>):
   - **Sound name**: letters, digits and underscores, up to 32. Empty uses the file's name.
   - **Volume**: 1 plays the wave as it is. It plays at exactly this volume and at the wave's own pitch every time. (The game's own animal sounds vary a little. Yours does not.)
   - **Has a place in the world**: the sound comes from where the thing is and gets quieter with distance. Use a mono wave for this. Then set **Full volume up to** and **Not heard beyond**. The far distance must be larger than the near one.
   - **Use it in the chosen event**: puts the sound into the event you have chosen.
3. Press the blue **New Sound From WAV** button. The add-on makes the three small files the game needs (a sound, a sample and the wave) in your project's folder.

Your sounds are listed in **Sounds** with **Use** (puts it into the chosen event) and a bin button (delete it). In an event's **Sound** search they come first.

Save your sound as a WAV file, 16 bit, plain PCM, mono. The game also reads three kinds of ADPCM. Floating-point WAV files and “extensible” WAV files (many 24 bit files are) are refused, and the message tells you to save the sound again as plain 16 bit. A plain 24 bit file only gets a warning.

**Give the event a time.** An event that plays a sound plays it once only when the event has a time (0 up to just below 1). With no time the game starts it as a looping sound that stays until the clip is over. The Events box and the export tell you when an event of yours has a sound and no time.

- When you export, the sound goes into the item with the event. For a creature the sound lives in a folder of the creature's own name. If you export under another name, the add-on moves it and the events follow. A worker, a character, a structure or a prop keeps its sounds in `audio/sfx/<its name>` inside the item.
- For a creature, two sounds with the same name in two folders cannot both go into one item, because they would land on one file. The export stops and says so. Delete one, or make it again under another name.
- The bin button asks first, deletes the sound's files (a file another of your sounds still uses stays) and **cannot be undone**.
- Blender's Undo does not undo a file written into your project (an effect or a sound).

## 6. See the effect in Blender: the preview and its limits

1. Open **Effect preview**. Choose a clip in the Events box.
2. Press **Show the clip's effects**, then press Play on the timeline. Every event of that clip that starts an effect shows it at its marker, at the moment the event gives. **Show this event's effect** does the same for the event chosen in the list. **Remove** takes the preview away.

The pictures are kept in a collection named after your model and are never exported. The effect follows the marker (or bone) while the animation moves it. It faces the scene camera, or your 3D view when there is no camera.

It is a **preview, not the game's own picture**:

- Particles that use a 3D mesh show as flat pictures. A spray has no ground or wind. A trail follows the marker's own path.
- An effect that lasts for ever is shown for a couple of seconds, or until the other effects of its combo have ended.
- An effect the preview cannot draw is left out, with a message. The game plays it as usual.
- If the effects run past the end of your timeline, the message says up to which frame.
- It does not show other event fields, such as a second marker an effect uses.

## 7. Check, export, install

1. In the **Game** panel, **1. Check**: for a structure, a worker, a character or a prop, **Check** lists an effect the game would not load as an error. For a creature, **Check the Creature** does not test your effects. The export does. A project of the type Effect model also shows one note: “This effect installs as loose files ... The Steam Workshop does not take effect items yet, so it cannot be shared there”. It does not mean anything is wrong with your effect.
2. Export (**Export Creature for the Game** or **Export Entity Folder**). It writes the model, the blueprint with your events and the project's effect files in one go. It stops, before writing anything, if an effect is one the game would not load at all. The message names the file and why.
3. Press **Install into Test Game**. The effect files install as loose files, and the game reads them.

## Projectiles and effect models

**File > New > Impossible Creatures Project...** has two types for this chapter:

- **Projectile**: something the game throws or fires (a rock, a quill, a spit). It is a small model with a blueprint, started from one of the game's projectiles, and exported like any other thing with **Export Entity Folder**.
- **Effect model**: a small mesh that an effect draws instead of a flat picture (a chunk, a shard, a ring). It has *no blueprint* and no events. **Export Entity Folder** writes an item folder named after the model, with the model in `data/art/fx/`, the project's effect files and a list of files. An effect names the model in its **Mesh** box. **Use my model** beside **Mesh** points a ring at your own model.

---

[Contents](README.md) · Previous: [Gatherers and characters](gatherers.md) · Next: [Check, export, install, remove and share](ship.md)
