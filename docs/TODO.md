# Documentation and interface TODO

Open items after the 0.9.2 alpha. The add-on's behaviour is ahead of parts of its documentation.

## User guide

- **Animations and motions:** describe the **New Clip** dialog (start from a clip; play it while idle, instead of a motion, or decide
  later), the "nothing starts this motion" check, and the tick list in **Borrow Clips From** (bone match count per model).
- **Gameplay numbers, Blueprint, Motions, Events:** these panels now load their data when first shown; remove the "press Load" steps.
  Describe the range warnings for structure numbers and how a creature's land speed is the sum of its leg and body speeds.
- **Effects, events and sounds:** describe the checks for an unknown effect name and an event time outside the clip, the readable
  names in the pickers, and the list of project effects that no event uses.
- **Checking, exporting, installing and sharing:** describe the **Item folder name** field, the refusal of a second install under
  the same item name, and that **Remove** restores the game's cached model.
- **Troubleshooting:** re-collect the add-on's refusal messages; several were reworded.
- Review every chapter against the README for tone and length.

## Screenshots

- Most screenshots predate the three-tab layout (IC Model / IC Game / IC Map) and the mirror fix (creatures face the other way).
  Re-capture them with the scripts in `docs/guide/capture/` (they run on a virtual display) and rebuild the guide (the HTML page and
  the Markdown pages) with `docs/guide/build_guide.py`.
- Add a current screenshot to the README.

## Interface

- Reduce the explanatory text in panels: controls in panels, explanations in tooltips and the guide, at most one short status line per
  panel. A partial, unreviewed pass exists and needs finishing and a test that enforces the limit.

## Verification

- Review exported content visually in the game (model orientation, textures, animations, effects, sounds).
- Run the add-on on Windows and macOS, and test installed content with the retail Steam executable.
