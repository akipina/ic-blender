"""sceneobj.map_overlays -- the Mission Editor's Overlay menu in Blender: where land / water / amphibious units can go, what cannot be
built on, the pathfinding sectors and the precise map, drawn over the terrain from the game's own pathfinding records.

The pictures come from ``ICParse.sgb.overlays``, which reads IMPA / PRMP / PASM. They are made from the map as it would be SAVED right
now (the terrain you sculpted, the cells you painted, the objects you moved, the water you painted), so "Show" again after a change
brings the picture up to date, as the editor's "Update ... Map" entries do. Nothing here is ever saved: the picture and its object live in
``<map> paint (not saved)``, with no mark the save reads.
"""
from __future__ import annotations

import importlib
import time

import numpy as np

import bpy

from . import map_paint as P

KEY_SHOWN = "ic_paint_overlay"            # on the map's collection: the key of the overlay shown now ("" = none)


class _Ctx:
    """The little of a save context the paint steps use (they read ``top``, ``sgb``, ``original`` and add report lines)."""

    def __init__(self, context, top, original: bytes, sgb: bytes):
        self.context, self.top, self.original, self.sgb = context, top, original, sgb
        self.report = {"changes": [], "warnings": []}

    def add_change(self, line: str) -> None:
        self.report["changes"].append(line)

    def add_warning(self, line: str) -> None:
        self.report["warnings"].append(line)


def current_bytes(context, top) -> tuple:
    """``(the map's bytes as a save would write them (core edits + painted passability and water), the lines they would report)``.
    Nothing is written."""
    X = P._x()
    edit = importlib.import_module("ICParse.sgb.edit")
    plan = X.build_plan(context, top)
    data, _rep = edit.apply_edits(plan.raw, paths="auto", **plan.kwargs())
    ctx = _Ctx(context, top, plan.raw, data)
    P.save_passability(ctx)
    P.save_water(ctx)
    return ctx.sgb, ctx.report["changes"] + ctx.report["warnings"]


def shown(top) -> str:
    return str(top.get(KEY_SHOWN, ""))


def show(context, top, key: str) -> dict:
    """Draw the overlay ``key`` (one of ``ICParse.sgb.overlays.KEYS``) over the terrain, replacing the one shown. Returns
    ``{"key", "title", "cells", "size", "seconds", "note"}``."""
    from ICParse.sgb import overlays
    t0 = time.perf_counter()
    X = P._x()
    terrain = X.terrain_of(top)
    if terrain is None:
        raise ValueError("The terrain of this map is not in the scene, so nothing can be drawn over it.")
    if key not in overlays.KEYS:
        raise ValueError(f"There is no overlay called {key}.")
    data, _lines = current_bytes(context, top)
    layer = overlays.layers(data, keys=[key])[key]
    hide(top)
    P._ensure_uv(terrain)
    pic = layer.rgba.astype(np.float32) / 255.0
    n = pic.shape[0]
    img = P._new_image(f"{P.map_name_of(top)} overlay {key}", n)
    P.put_image_pixels(img, pic)
    img.pack()
    mat = P._overlay_material(f"{P.map_name_of(top)} overlay", image=img, opacity=P.OPACITY["overlay"])
    obj = P._make_object(top, terrain, "overlay", "overlay", mat, img)
    P._slot_material(obj, mat)
    top[KEY_SHOWN] = key
    return {"key": key, "title": layer.title, "cells": layer.cells_marked, "size": n, "note": layer.note,
            "seconds": round(time.perf_counter() - t0, 2)}


def hide(top) -> None:
    obj = P.paint_object(top, "overlay")
    img = P.paint_image(top, "overlay")
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    if img is not None:
        bpy.data.images.remove(img)
    top[KEY_SHOWN] = ""
