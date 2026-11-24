"""The sceneobj tests, found by name, for run_all.py (PLAN_FINAL 7.4): no test of a later slice needs a line added to run_all.py.

    tests/so_<slice>_pure_test.py   runs under the venv python (no Blender)           -> PURE
    tests/so_<slice>_test.py        runs in a headless Blender (blender --background) -> IN_BLENDER

``run_all.py`` appends these to its own lists, so ``run_all.py -k so_`` runs them all and ``-k so_<slice>`` runs one.
"""
from __future__ import annotations

import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def names() -> list:
    """Every sceneobj test file name, sorted."""
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "so_*_test.py")))


def is_pure(name: str) -> bool:
    return name.endswith("_pure_test.py")


PURE = [n for n in names() if is_pure(n)]
IN_BLENDER = [n for n in names() if not is_pure(n)]
