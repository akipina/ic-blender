"""Shared Blender UI pieces: ``alert`` / ``issue_rows``, the one list ``BZ_UL_rows``, ``SubPanel`` and
``draw_scene_pointer``. Pure logic lives in ``model``.

Add list rows with ``@row_drawer`` instead of registering list classes. ``SubPanel`` is never registered itself;
subclasses go in their module's ``CLASSES``.
"""
from __future__ import annotations

import importlib
import importlib.util

import bpy

ORDER = 5                 # imported early: panels of other slices subclass SubPanel
ABSTRACT = ("SubPanel",)  # defined here, never registered (the contract test skips these)

# The three tabs of the 3D view sidebar: every panel's ``bl_category`` is one of these.
TAB_MODEL = "IC Model"    # shape, rig, skin, parts, markers, damage states, materials, combine preview
TAB_GAME = "IC Game"      # blueprint, motions, events, effects, gameplay numbers, check, export, install, share
TAB_MAP = "IC Map"        # every map panel

try:
    from ICParse.plainwords import plain          # a checker's message without source files, function names and rule ids
except ImportError:                                # an older ICParse: messages are shown as they are
    def plain(text):
        return text

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
    for w in str(plain(text)).split():
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines


# The first line of every panel: what the panel is for, in a sentence a player can read. ``add_purposes`` puts it in front of each panel's own
# ``draw`` when the add-on registers (so a panel's code does not have to repeat it). A panel not listed here draws its own first hint.
PURPOSE = {
    "BZ_PT_panel": "Open, start and reshape a model. What the game does with it is in the IC Game tab.",
    "BZ_PT_boner": "Limbs, bones and connections. They decide which points follow which bone, and where parts of other animals can join.",
    "BZ_PT_combine": "See your creature joined with another animal, the way the game's creature creator would join them.",
    "BZ_PT_damage_states": "How a building looks as it is hurt. Each damage state is a shape the game blends in.",
    "BZ_PT_scene_parts": "The separate pieces the model is made of. Add, replace or remove a piece.",
    "BZ_PT_scene_markers": "Named points on the model where the game puts effects and sounds.",
    "BZ_PT_ic_material": "The picture, opacity and team colour of the selected material.",
    "BZ_PT_blueprint": "The blueprint is the file that tells the game how this model behaves: its name, kind, size, sounds and numbers.",
    "BZ_PT_motions": "A clip (an animation) plays in the game only through a motion. Here you choose which clip plays when.",
    "BZ_PT_fx_events": "An event says: at this moment of this animation, start this effect and play this sound, at this point of the model.",
    "BZ_PT_fx_used": "The effects your events start. Copy a game effect into your project to change it.",
    "BZ_PT_fx_editor": "Every setting of one effect. Each change is saved to the effect's file at once.",
    "BZ_PT_stats": "The numbers of your creature: hit points, speed, attacks, abilities. They start as the numbers of the animal you began with.",
    "BZ_PT_game": "The steps that take your work into the game: check it, export it, install it into your test copy, and share it.",
    "BZ_PT_map": "Change a game map: its ground, water, textures and the things placed on it. Save a folder to copy into the game.",
    "BZ_PT_map_terrain": "Raise or lower the ground, and move the water.",
    "BZ_PT_map_look": "Show the map the way the game draws it: sun, sky, ground picture, water and fog. This is never saved.",
    "BZ_PT_map_ground": "Choose which picture covers which part of the ground.",
    "BZ_PT_map_objects": "The things placed on the map. Move, copy, add and delete them, and say who owns them.",
    "BZ_PT_map_save": "Check what you changed, then save the map into a folder of your own.",
    "BZ_PT_map_cinematics": "The map's cut-scene animations: cameras and moving objects.",
    "BZ_PT_map_replay": "Play a cut-scene back on Blender's timeline, acted out by stand-ins.",
    "BZ_PT_map_script_groups": "Groups of objects, and variables, that triggers use.",
    "BZ_PT_map_script_triggers": "Triggers: when something happens on the map, do something.",
    "BZ_PT_map_script_dialogs": "Dialog triggers: the speech and text the map shows.",
    "BZ_PT_map_script_ai": "The computer players' scripts and armies.",
    "BZ_PT_map_settings": "The map's name, kind and number of players.",
    "BZ_PT_map_terrain_look": "Light, sky, sea, fog and the fine ground texture.",
    "BZ_PT_map_problems": "What is wrong with the map: missing or blocked starts, broken script references.",
    "BZ_PT_map_tools_files": "Import and export the height map, the terrain and the placed things as files.",
    "BZ_PT_map_tools_objects": "A list of the placed objects. Filter it to select, hide or delete.",
    "BZ_PT_map_tools_blueprints": "How often each kind of thing is used on the map.",
    "BZ_PT_map_tools_crop": "Make the map smaller: cut it down to a smaller square.",
}


def panel_purpose(layout, idname: str) -> None:
    """The panel's purpose line (a small grey sentence at the top of the panel); nothing for a panel with no entry in :data:`PURPOSE`."""
    text = PURPOSE.get(idname)
    if not text:
        return
    col = layout.column(align=True)
    for line in wrap_lines(text, 44):
        col.label(text=line)


def add_purposes() -> int:
    """Put :func:`panel_purpose` in front of the ``draw`` of every panel class listed in :data:`PURPOSE` (called when the add-on registers, before
    its classes are). Safe to call twice. Returns how many panels were changed."""
    done, seen, stack = 0, set(), list(bpy.types.Panel.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        ident = cls.__dict__.get("bl_idname", "")
        orig = cls.__dict__.get("draw")
        if ident not in PURPOSE or orig is None or getattr(orig, "_bz_purpose", False) or id(cls) in seen:
            continue
        seen.add(id(cls))
        cls.draw = _with_purpose(orig, ident)
        done += 1
    return done


def _with_purpose(orig, ident: str):
    """``orig`` (a panel's ``draw``) with the purpose line first. Blender wants a ``draw`` of exactly ``(self, context)``, so the two values it
    needs are in the closure, not in default arguments."""
    def draw(self, context):
        panel_purpose(self.layout, ident)
        return orig(self, context)
    draw._bz_purpose = True
    return draw


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
    bl_category = TAB_MODEL
    bl_parent_id = "BZ_PT_entity"
    bl_options = {"DEFAULT_CLOSED"}
    capability = ""

    @classmethod
    def poll(cls, context):
        return has_capability(context, cls.capability)


# ---------------------------------------------------------------------------------------------------- the 3D-view IC panel
def draw_scene_pointer(layout, obj, *, rig_box=None) -> None:
    """What the "IC" panel shows under a scene part object. Once the Entity panel exists this is a single pointer line; until then it
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
    info.label(text="(edit faces, UVs, weights, bones, textures, markers, damage keys and parts, then Export)")
    if obj.get("bz_rig"):
        if rig_box is not None:
            rig_box(layout, obj)
        layout.operator("bz.export_scene", icon="EXPORT")
    layout.operator("bz.export_scene_geometry", icon="EXPORT")


CLASSES = (BZ_UL_rows,)
