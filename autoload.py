"""A panel reads its own data the first time it is shown.

Creature Stats, the Blueprint, the Motions box and the Events box each start from a file of the donor. They used to wait for a Load button. Now the
first time a panel is drawn for a thing it asks for the file to be read (:func:`once`), and the read runs a moment later in Blender's timer (a panel
cannot change data while it is drawn). The panel's own button stays as a way to read the file again, and as the second try when the first one
failed: a failure is not tried again by itself, so a panel never loops on a missing game folder.
"""
from __future__ import annotations

import bpy

_TRIED: set = set()
_QUEUE: list = []                                                        # the reads asked for and not yet done (the timer, or :func:`flush`, does them)


def once(key, work) -> bool:
    """Run ``work()`` soon, in Blender's timer, the first time ``key`` is seen in this session (True then, False afterwards). A failing ``work`` is
    only dropped: the panel's Load button is still there and says why."""
    if key in _TRIED:
        return False
    _TRIED.add(key)
    done = []

    def run():
        if done:
            return None
        done.append(True)
        if run in _QUEUE:
            _QUEUE.remove(run)
        try:
            work()
        except Exception:                                                 # noqa: BLE001 -- the panel's own button reports the reason
            pass
        wm = bpy.context.window_manager
        for win in getattr(wm, "windows", []):
            for area in win.screen.areas:
                area.tag_redraw()
        return None
    _QUEUE.append(run)
    bpy.app.timers.register(run, first_interval=0.0)
    return True


def flush() -> int:
    """Do the reads that are asked for now, without waiting for the timer (a headless session has no timer loop). Returns how many."""
    todo = list(_QUEUE)
    for run in todo:
        run()
    return len(todo)


def forget() -> None:
    """Forget what was tried (tests, and a new file)."""
    _TRIED.clear()
    _QUEUE.clear()
