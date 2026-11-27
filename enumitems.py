"""The items of a dynamic enum, kept.

Blender asks the ``items`` callback of an EnumProperty again and again -- once per entry of an open menu, several times per redraw of
the panel that shows it -- and keeps only POINTERS into the strings the callback returned. Two things follow:

* the strings must outlive the call. A callback that builds new strings each time frees the ones Blender still points at, and a button
  then shows whatever took their place in memory (the New Project dialog showed other animals' names where "Replace the original
  Camel" belonged). :func:`kept` returns, for equal items, the very list that was returned the first time, and never lets go of it;
* the callback must be cheap. Anything it reads from disk belongs behind :func:`recent`, which answers from memory and looks again at
  most every few seconds.
"""
from __future__ import annotations

import functools
import time

_LISTS: dict = {}                 # tuple of item tuples -> the list given to Blender for them (never dropped: Blender holds its strings)
_RECENT: dict = {}                # key -> (when looked, value)


def kept(items) -> list:
    """The list to return from an ``items`` callback: the one first returned for these same items, strings and all."""
    key = tuple(i if i is None or isinstance(i, tuple) else tuple(i) for i in items)        # None is a separator line
    hit = _LISTS.get(key)
    if hit is None:
        hit = _LISTS[key] = list(key)
    return hit


def stable(callback):
    """Decorator for an ``items`` callback: what it returns goes through :func:`kept`."""
    @functools.wraps(callback)
    def items(self, context):
        return kept(callback(self, context))
    return items


def recent(key, look, seconds: float = 2.0):
    """``look()``, asked at most every ``seconds`` for this key: in between, the last answer. For what a draw-time callback reads from
    disk (a folder listing, file dates): a change shows within ``seconds``, and a redraw costs a dictionary lookup."""
    now = time.monotonic()
    hit = _RECENT.get(key)
    if hit is not None and now - hit[0] < seconds:
        return hit[1]
    value = look()
    _RECENT[key] = (now, value)
    return value


def forget() -> None:
    """Look at the disk again on the next call (tests, and after the add-on itself changed the files)."""
    _RECENT.clear()
