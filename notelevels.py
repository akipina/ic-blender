"""Which lines of an export report are warnings (something the modder has to act on) and which are plain facts.

Without Blender, so a test can read it. The reports of Check and of the exports are lists of plain sentences. A line is a **warning** when it names
something that does nothing in the game or that the game would misread (:func:`is_warning`); it is a **fact** when it only tells what was written
(:func:`is_fact`: "3 events added", "lab.lua builds on defstats5.lua"). Check shows the warnings; an export reports a fact as information, not as a
warning, and a line that is neither (an unknown one) still as a warning, so nothing new is hidden.
"""
from __future__ import annotations

import re

PURE = True

#: pieces of a sentence that mark it as something to act on
_WARNING = (
    "is not played by any motion",                   # a new clip nothing plays
    "Nothing starts the motion",                     # a motion nothing starts
    "which neither the game nor the project has",    # an event names an effect nobody has
    "never runs",                                    # an event that cannot run (no clip, a time past the end of the clip)
    "when the clip starts, not at that time",        # an event time below 0
    "the game only uses",                            # a number outside the range the game keeps it in
    "is below zero",                                 # a number no thing of the game has
    "not a usable number",
    "start together", "plays none of them", "would stand still", "the speed is not used", "ignores the range", "nothing fits",
    "start each other", "Two motions are called",    # the Motions box's own checks of what was edited
    "now play nothing", "play at the model's origin", "so it plays nothing", "may play differently", "stops with an error",
    "Delete them, or", "delete them, or",
)

#: sentences that only say what was written
_FACT = re.compile(
    r"^(Events|Motions): |^blueprint: |effect file\(s\) in the item|effect or sound file\(s\) of the project|also repeats|builds on |holds the whole table"
    r"|file\(s\) of the earlier export are no longer part|the game makes the levels|were written from its Action|now name '|follow a renamed|"
    r"are not plain numbers and are not repeated|^motions: ", re.I)


def is_warning(note: str) -> bool:
    """Whether a report line is a warning (a motion, clip, event or number that does nothing in the game or that the game misreads)."""
    return any(m in note for m in _WARNING)


def is_fact(note: str) -> bool:
    """Whether a report line only says what was written (and is no warning)."""
    return bool(_FACT.search(note)) and not is_warning(note)


def level(note: str) -> str:
    """``"INFO"`` for a plain fact, ``"WARNING"`` for anything else (a warning, or a line nobody has classified: it is shown rather than hidden)."""
    return "INFO" if is_fact(note) else "WARNING"
