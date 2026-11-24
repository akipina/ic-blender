"""sceneobj.ui_kit -- the Blender 5.x interface pieces every slice reuses (PLAN_FINAL contract C21).

Panels, lists and messages for gatherers, characters, structures and props are built from these, so they look and behave alike:

* ``alert(layout, text)`` / ``issue_rows(layout, issues)`` -- a wrapped red message, and one row per problem (the exporter's and
  the checks' findings) with an icon by severity;
* ``BZ_UL_rows`` -- ONE registered list, ``layout.template_list("BZ_UL_rows", "<list id>", data, "rows", ...)``. Blender filters it by
  name and sorts it itself; a slice only says how a row looks with ``@row_drawer("<list id>")`` and registers no list class of its own;
* ``SubPanel`` -- the base of the sub-panels of the Entity panel: shown only where the entity has the capability (never by subtype),
  closed by default, ordered with ``bl_order``. A slice subclasses it, sets ``bl_idname``, ``bl_label``, ``bl_order``, ``capability``
  and ``draw``, and lists the class in its module's ``CLASSES`` (this base class is never registered);
* ``draw_scene_pointer(layout, obj)`` -- what the 3D-view "SGM" panel shows under a scene object (a gatherer, character, structure
  or prop): today the short info + export buttons, and as soon as the Entity panel exists, a one-line pointer to it.

This module is for Blender; the pure logic lives in ``model``.
"""
from __future__ import annotations

import importlib
import importlib.util

import bpy

ORDER = 5                 # imported early: panels of other slices subclass SubPanel
ABSTRACT = ("SubPanel",)  # defined here, never registered (the contract test skips these)

ICON_BY_LEVEL = {"ERROR": "ERROR", "WARN": "INFO", "INFO": "DOT"}


# ---------------------------------------------------------------------------------------------------- optional sibling modules
def _optional(name: str):
    """The sibling module ``name`` when it exists (a later slice may not have landed yet), else None. An error INSIDE it is not hidden."""
    if importlib.util.find_spec(f"{__package__}.{name}") is None:
        return None
    return importlib.import_module(f"{__package__}.{name}")


def has_capability(context, capability: str) -> bool:
    """Whether the active entity has ``capability`` (parts, skeleton, markers, morphs, ...): False until the entity layer exists."""
    entity = _optional("entity")
    return bool(entity is not None and capability and entity.has(context, capability))


def panel_registered(idname: str) -> bool:
    """Whether a panel with this ``bl_idname`` is registered now."""
    return bpy.types.Panel.bl_rna_get_subclass_py(idname) is not None


def is_scene_object(obj) -> bool:
    """Whether ``obj`` belongs to a scene entity (a gatherer, character, structure or prop) rather than to a creature."""
    if obj is None:
        return False
    if obj.get("bz_scene"):                       # a part object of an import (the stamp is kept for good)
        return True
    entity = _optional("entity")
    if entity is not None and entity.find_entity(obj) is not None:
        return True
    if obj.type == "ARMATURE":                    # the rig of an import made before the entity layer existed
        return any(o.get("bz_scene") and o.get("bz_rig") == obj.name for o in bpy.data.objects)
    return False


def is_scene_context(context) -> bool:
    return is_scene_object(context.active_object)


# ---------------------------------------------------------------------------------------------------- messages
def wrap_lines(text: str, width: int = 60) -> list:
    """``text`` cut into lines of about ``width`` characters at word breaks (a word longer than a line stays whole). This is the
    wrapping the creature panels have always used (it moved here from ``ui._alert``)."""
    lines, line = [], ""
    for w in str(text).split():
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


def alert(layout, text: str, icon: str = "ERROR", width: int = 60) -> None:
    """A red, wrapped message in the panel."""
    col = layout.column(align=True)
    col.alert = True
    for i, line in enumerate(wrap_lines(text, width)):
        col.label(text=line, icon=icon if i == 0 else "BLANK1")


def issue_rows(layout, issues, *, limit: int | None = None, width: int = 60) -> int:
    """One row per finding (worst first): a red row for an ERROR, an info row for a WARN, a dot for INFO. Returns how many were shown."""
    rank = {"ERROR": 0, "WARN": 1, "INFO": 2}
    ordered = sorted(issues, key=lambda i: rank.get(i.level, 3))
    shown = ordered if limit is None else ordered[:limit]
    for i in shown:
        text = f"{i.where}: {i.text}" if i.where and i.where not in i.text else i.text
        if i.level == "ERROR":
            alert(layout, text, width=width)
        else:
            col = layout.column(align=True)
            for n, line in enumerate(wrap_lines(text, width)):
                col.label(text=line, icon=ICON_BY_LEVEL.get(i.level, "DOT") if n == 0 else "BLANK1")
    if len(ordered) > len(shown):
        layout.label(text=f"... and {len(ordered) - len(shown)} more")
    return len(shown)


# ---------------------------------------------------------------------------------------------------- lists
ROW_DRAWERS: dict = {}    # list id -> fn(ui_list, context, layout, data, item, icon, active_data, active_propname, index)


def row_drawer(list_id: str):
    """Decorator: say how one row of the ``BZ_UL_rows`` list called ``list_id`` is drawn."""
    def register(fn):
        ROW_DRAWERS[list_id] = fn
        return fn
    return register


def _default_row(ui_list, layout, item, icon) -> None:
    name = str(getattr(item, "name", item))
    if ui_list.layout_type == "GRID":
        layout.alignment = "CENTER"
        layout.label(text="", icon_value=icon)
    else:
        layout.label(text=name, icon_value=icon)


class BZ_UL_rows(bpy.types.UIList):
    """The one list of the sceneobj panels. Blender filters it by name (the filter box) and sorts it by name; ``ROW_DRAWERS`` (set with
    ``@row_drawer``) draws each row, and a row with no drawer shows its name."""
    bl_idname = "BZ_UL_rows"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0, flt_flag=0):
        drawer = ROW_DRAWERS.get(self.list_id)
        if drawer is None:
            _default_row(self, layout, item, icon)
        else:
            drawer(self, context, layout, data, item, icon, active_data, active_propname, index)


# ---------------------------------------------------------------------------------------------------- sub-panels
class SubPanel(bpy.types.Panel):
    """Base of the sub-panels of the Entity panel (``BZ_PT_entity``). A subclass sets ``bl_idname``, ``bl_label``, ``bl_order`` and
    ``capability`` and draws itself; it is shown only when the active entity has that capability, and starts closed."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SGM"
    bl_parent_id = "BZ_PT_entity"
    bl_options = {"DEFAULT_CLOSED"}
    capability = ""

    @classmethod
    def poll(cls, context):
        return has_capability(context, cls.capability)


# ---------------------------------------------------------------------------------------------------- the 3D-view SGM panel
def draw_scene_pointer(layout, obj, *, rig_box=None) -> None:
    """What the "SGM" panel shows under a scene part object. Once the Entity panel exists this is a single pointer line; until then it
    is the short info box and the export buttons the panel always had. ``rig_box(layout, obj)`` draws the rig / animation box
    (the panel passes its own)."""
    if panel_registered("BZ_PT_entity"):
        layout.label(text="Parts, skeleton, materials, checks and export are in the Entity panel below", icon="OUTLINER_COLLECTION")
        return
    info = layout.box()
    info.label(text=str(obj.get("bz_name", obj.name)), icon="OUTLINER_OB_MESH")
    parts = [o for o in bpy.data.objects if o.get("bz_scene") and o.get("bz_source") == obj.get("bz_source")]
    kind = {"CHARACTER": "gatherer / character", "STRUCTURE": "structure / prop"}.get(obj.get("bz_project", ""), "scene object")
    info.label(text=f"{kind}: {len(parts)} part(s), {sum(len(o.data.vertices) for o in parts)} vertices")
    info.label(text="(triangle mesh: edit it freely -- faces, UVs, weights -- then Export)")
    if obj.get("bz_rig"):
        if rig_box is not None:
            rig_box(layout, obj)
        layout.operator("bz.export_scene", icon="EXPORT")
    layout.operator("bz.export_scene_geometry", icon="EXPORT")


CLASSES = (BZ_UL_rows,)
