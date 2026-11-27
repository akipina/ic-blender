"""sceneobj.map_crop -- the Mission Editor's Terrain Crop for the map in the scene (the work is ``ICParse.sgb.crop``).

In plain words. Crop cuts the map down to a square part of itself: 1/2, 1/4 or 1/8 of its side. Everything that depends on the map's size is
rewritten (ground, water, ground textures, the cells painted passable or blocked, decals, the objects, who owns what, the pathfinding), so the
cropped map is a complete map of its own. "Validate" first lists what would not survive (objects the crop deletes, and the triggers,
dialogue and NISlet animations that name them); "Crop" then builds the cropped map as a NEW map in the scene (the old one stays, hidden), so
nothing is lost, and the cropped map is saved like any other map. A crop that would break something is refused until "Crop anyway" is on.

The square is centred on the 3D cursor (or on a point you type) and moved to the nearest whole 8 x 8 cell ground-texture block, kept inside
the map: the editor's crop box does the same with its centre (decompiled: MissionEditor.exe 0x0042fbef, 0x004a0148).
"""
from __future__ import annotations

import numpy as np

import bpy

from . import map_tools as T
from . import ops_map_export as X
from . import ops_map_import as M

ORDER = 56

AREA_KEY = "ic_crop_area"
RATIOS = (2, 4, 8)
RESULTS: dict = {}               # map collection name -> the last validation: {"lines", "errors", "warnings", "region", "outside", "ratio", "centre"}
MAX_LINES = 14


def cursor_centre(context) -> tuple:
    """The 3D cursor as a game position (x, z) in metres (Blender's x, y are the game's -x, -z)."""
    c = context.scene.cursor.location
    return (float(-c.x), float(-c.y))


def _problem_lines(problems: list) -> list:
    out = []
    shown = 0
    for p in problems:
        if shown >= MAX_LINES:
            break
        out.append(("ERROR: " if p["severity"] == "error" else "Warning: ") + p["text"])
        shown += 1
    if len(problems) > shown:
        out.append(f"... and {len(problems) - shown} more.")
    return out


def _clear_area(top) -> None:
    for o in list(top.objects):
        if o.get(AREA_KEY):
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)


def draw_area(context, top, region) -> object:
    """A wire rectangle (``Crop area``) at the square that stays, in the map's collection; it is not a placed object and is never saved."""
    _clear_area(top)
    terrain = X.terrain_of(top)
    level = 0.0
    if terrain is not None:
        level = max(v[2] for v in terrain.bound_box) + 2.0 if len(terrain.data.vertices) else 0.0
    lo, hi = region.lo, region.hi
    pts = [(-lo[0], -lo[1], level), (-hi[0], -lo[1], level), (-hi[0], -hi[1], level), (-lo[0], -hi[1], level)]
    me = bpy.data.meshes.new(f"Crop area {X.map_name(top)}")
    me.from_pydata(pts, [(0, 1), (1, 2), (2, 3), (3, 0)], [])
    me.update()
    obj = bpy.data.objects.new(me.name, me)
    obj.display_type = "WIRE"
    obj.show_in_front = True
    obj[AREA_KEY] = True
    top.objects.link(obj)
    return obj


def validate(context, top, ratio: int, centre: tuple | None = None, *, select: bool = True, show_area: bool = True) -> dict:
    """Validate a crop of the scene's map without changing the map: returns the result dict (also kept in ``RESULTS``). ``select`` selects
    the placed objects the crop would delete. Raises ``map_tools.ToolError`` for a crop size that cannot be used."""
    C = T.crop_mod()
    data, sib, _plan, _rep = T.current_map(context, top)
    try:
        v = C.validate(data, ratio, centre, sib)
    except C.CropError as exc:
        raise T.ToolError(str(exc)) from None
    region = v["region"]
    rig = M._addon("rig")
    outside = []
    for o in X.entity_objects(top):
        x, _y, z = rig.from_blender_matrix(o.matrix_world)[:3, 3]
        if not region.contains(float(x), float(z)):
            outside.append(o)
    if show_area:
        draw_area(context, top, region)
    if select:
        for o in context.view_layer.objects:
            o.select_set(False)
        for o in outside:
            try:
                o.select_set(True)
            except RuntimeError:
                pass
    errors = [p for p in v["problems"] if p["severity"] == "error"]
    warns = [p for p in v["problems"] if p["severity"] != "error"]
    lines = [f"Crop to 1/{ratio}: keeps {region.words()}.",
             f"Placed objects: {len(X.entity_objects(top)) - len(outside)} stay, {len(outside)} lie outside and would be deleted"
             + (" (selected in the scene)." if outside and select else ".")]
    if v["problems"]:
        lines.append(f"{len(errors)} things the crop would break, {len(warns)} warnings:")
        lines += _problem_lines(v["problems"])
    else:
        lines.append("Nothing the crop would break: no trigger, dialogue, NISlet animation or player depends on what it deletes.")
    result = {"lines": lines, "errors": len(errors), "warnings": len(warns), "region": region, "outside": [o.name for o in outside], "ratio": ratio,
              "centre": centre, "problems": v["problems"]}
    RESULTS[top.name] = result
    return result


def crop(context, top, ratio: int, centre: tuple | None = None, *, force: bool = False) -> dict:
    """Crop the scene's map to 1/ratio of its side. Builds the cropped map as a new map in the scene (the old one is excluded from the view)
    and returns ``{"top": the new collection, "report": ICParse's report, "lines": the report in plain words, "name": ...}``. Refuses (``ToolError``)
    while a validation error stands, unless ``force``."""
    C = T.crop_mod()
    data, sib, _plan, _rep = T.current_map(context, top)
    try:
        out, files, rep = C.crop_map(data, ratio, centre, siblings=sib, force=force)
    except C.CropError as exc:
        extra = _problem_lines(exc.problems) if exc.problems else []
        raise T.ToolError("\n".join([str(exc)] + extra + (["Turn on 'Crop anyway' to go on and break them."] if extra else []))) from None
    region = rep["region"]
    base = X.map_name(top)
    name = f"{base}_crop{ratio}"
    kept = {k: v for k, v in files.items() if v is not None}
    origin = f"Cropped to 1/{ratio} from {base}: {region.words()}"
    new = T.reopen(context, top, out, name, kept, origin)
    _clear_area(top)
    lines = [f"Cropped {base} to 1/{ratio}: the new map {X.map_name(new)} is {region.new_cells} x {region.new_cells} cells "
             f"({region.new_cells * region.tile_scale:g} m) and has {rep['entities_kept']} placed objects ({rep['entities_deleted']} deleted).",
             f"The part kept: {region.words()}.",
             "Ground, textures, water, passability and decals were cropped, objects recentred and pathfinding rebuilt."]
    if rep.get("files_dropped"):
        lines.append("Pictures made for the old size were left out (they are not part of the map): " + ", ".join(rep["files_dropped"]) + ".")
    if rep.get("na_shifted"):
        lines.append("The NISlet animation file's camera and object paths were moved with the map.")
    if rep["problems"]:
        lines.append(f"{len(rep['problems'])} things were broken on purpose ('Crop anyway'):")
        lines += _problem_lines(rep["problems"])
    lines.append("The old map is still in the scene, hidden. The cropped map is saved like any other: File > Export or the Save Map button.")
    return {"top": new, "report": rep, "lines": lines, "name": name}
