"""IC Material panel (View3D > IC) for scene objects: the active material slot's game shader, its texture, opacity and team
colour. What it sets is written by the export's materials step (``ic_blender/scene_materials.py``)."""
from __future__ import annotations

import json
import os

import bpy
from . import ui_kit
from .. import enumitems
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

ORDER = 60

PLAYERS = [(str(i), f"Player {i + 1}", f"Show the colours of player {i + 1}") for i in range(6)]


def _sm():
    from .. import scene_materials
    return scene_materials


def _part(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" and obj.get("bz_scene") else None


def _mat(context):
    obj = _part(context)
    if obj is None or not obj.material_slots:
        return None
    return obj.material_slots[obj.active_material_index].material


def _pow2(n: int) -> bool:
    return n > 0 and not (n & (n - 1))


def _nearest_pow2(n: int) -> int:
    p = 1
    while p * 2 <= n:
        p *= 2
    return min(1024, p * 2 if n - p > p * 2 - n else p)


def _set_diffuse(mat, img) -> None:
    """Show ``img`` as the material's diffuse: on the image node feeding Base Color (one is made when there is none)."""
    sm = _sm()
    mat.use_nodes = True
    tree = mat.node_tree
    node = sm._base_node(mat)
    bsdf = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if node is None:
        node = tree.nodes.new("ShaderNodeTexImage")
        node.location = (-400, 200)
        node.interpolation = "Closest"
        if bsdf is not None:
            tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
            if "Alpha" in bsdf.inputs:
                tree.links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
    node.image = img
    tree.nodes.active = node
    mat[sm.KEY_IMAGE] = img.name
    if sm.KEY_PREVIEW in mat:
        del mat[sm.KEY_PREVIEW]


class BZ_OT_ic_material_replace_image(bpy.types.Operator, ImportHelper):
    """Use another picture as this material's texture"""
    bl_idname = "bz.ic_material_replace_image"
    bl_label = "Replace Image"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.png;*.tga;*.bmp;*.jpg;*.jpeg;*.tif;*.tiff", options={"HIDDEN"})
    scale: BoolProperty(name="Scale to a size the game takes", default=True,
                        description="Scale the picture to the nearest size the game takes (a power of two, at most 1024)")

    @classmethod
    def poll(cls, context):
        return _mat(context) is not None

    def execute(self, context):
        mat = _mat(context)
        try:
            img = bpy.data.images.load(self.filepath, check_existing=False)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Could not open the picture: {e}")
            return {"CANCELLED"}
        w, h = img.size
        if not (_pow2(w) and _pow2(h)) or w > 1024 or h > 1024:
            if not self.scale:
                bpy.data.images.remove(img)
                self.report({"ERROR"}, f"The picture is {w} x {h}; both sides must be a power of two, at most 1024. "
                                       f"Tick 'Scale to a size the game takes' or resize it.")
                return {"CANCELLED"}
            img.scale(_nearest_pow2(w), _nearest_pow2(h))
        img.pack()
        img.use_fake_user = True
        _set_diffuse(mat, img)
        self.report({"INFO"}, f"'{mat.name}' now shows {os.path.basename(self.filepath)} ({img.size[0]} x {img.size[1]}).")
        return {"FINISHED"}


class BZ_OT_ic_material_new(bpy.types.Operator):
    """Add a new material to this part (in Edit Mode, to the selected faces)"""
    bl_idname = "bz.ic_material_new"
    bl_label = "New IC Material"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Name", default="new_material")
    size: IntProperty(name="Texture size", default=256, min=2, max=1024)

    @classmethod
    def poll(cls, context):
        return _part(context) is not None

    def execute(self, context):
        obj = _part(context)
        size = _nearest_pow2(self.size)
        mat = bpy.data.materials.new(self.name or "new_material")
        mat.use_nodes = True
        img = bpy.data.images.new(f"SGM_{mat.name}", size, size, alpha=True)
        img.generated_color = (0.8, 0.8, 0.8, 1.0)
        img.pack()
        img.use_fake_user = True
        _set_diffuse(mat, img)
        obj.data.materials.append(mat)
        obj.active_material_index = len(obj.material_slots) - 1
        if obj.mode == "EDIT":
            bpy.ops.object.material_slot_assign()
        self.report({"INFO"}, f"Added '{mat.name}'. Assign faces to it in Edit Mode, then paint its {size} x {size} texture.")
        return {"FINISHED"}


class BZ_OT_ic_team_mask(bpy.types.Operator):
    """Make a team-colour mask to paint: white where the player colour goes"""
    bl_idname = "bz.ic_team_mask"
    bl_label = "Team Mask"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        mat = _mat(context)
        return mat is not None and _sm().diffuse_image(mat) is not None

    def execute(self, context):
        import numpy as np
        sm = _sm()
        mat = _mat(context)
        base = sm.diffuse_image(mat)
        w, h = base.size
        img = bpy.data.images.get(mat.get(sm.KEY_TEAM_MASK, ""))
        if img is None:
            img = bpy.data.images.new(f"SGM_{mat.name}_teammask", w, h, alpha=False)
            mask = None
            if mat.get(sm.KEY_SHADER):
                from ICParse.sgm.scene import shader as SH
                src = mat.get(sm.KEY_SHADER_SRC, "")
                try:
                    from .. import core
                    raw = open(core.resolve_source(src), "rb").read()
                    got = sm.stock_team_mask(SH.ShaderSpec.from_json(json.loads(mat[sm.KEY_SHADER])), raw)
                except (OSError, ValueError):
                    got = None
                if got is not None and got[:2] == (w, h):
                    mask = got[2]
            rgba = np.zeros((w * h, 4), dtype=np.uint8)
            rgba[:, 3] = 255
            if mask is not None:
                rgba[mask, :3] = 255
            sm.set_image_rgba(img, w, h, rgba.tobytes())
            img.pack()
            if mask is not None:                       # the game's own team area: unchanged until painted
                from .. import material as M
                img[sm.KEY_SCENE_HASH] = str(M.pixel_crc(img))
            img.use_fake_user = True
            mat[sm.KEY_TEAM_MASK] = img.name
            mat[sm.KEY_TEAM_ON] = True
        tree = mat.node_tree
        node = next((n for n in tree.nodes if n.bl_idname == "ShaderNodeTexImage" and n.image is img), None)
        if node is None:
            node = tree.nodes.new("ShaderNodeTexImage")
            node.image = img
            node.location = (-700, -100)
            node.label = "Team mask (paint white where the player colour goes)"
        tree.nodes.active = node                       # Texture Paint paints the active image node
        self.report({"INFO"}, f"Paint '{img.name}' in Texture Paint: white = player colour")
        return {"FINISHED"}


class BZ_OT_ic_team_preview(bpy.types.Operator):
    """Show the material in a player's colour (only in Blender; the export writes all of them)"""
    bl_idname = "bz.ic_team_preview"
    bl_label = "Preview Player Colour"
    bl_options = {"REGISTER", "UNDO"}

    player: EnumProperty(name="Player", items=PLAYERS)

    @classmethod
    def poll(cls, context):
        mat = _mat(context)
        return mat is not None and bool(mat.get(_sm().KEY_TEAM_ON))

    def execute(self, context):
        from ICParse.codecs import teamcolour as TC
        sm = _sm()
        mat = _mat(context)
        base = sm.diffuse_image(mat)
        if base is None:
            return {"CANCELLED"}
        index = int(self.player)
        node = sm._base_node(mat)
        if index == 0:
            if node is not None:
                node.image = base
            mat.pop(sm.KEY_PREVIEW, None)
            return {"FINISHED"}
        w, h, rgba = sm.image_rgba(base)
        mask_img = bpy.data.images.get(mat.get(sm.KEY_TEAM_MASK, ""))
        if mask_img is None:
            self.report({"ERROR"}, "Make the team mask first (Team Mask)")
            return {"CANCELLED"}
        try:
            mask = sm.mask_of(mask_img, (w, h))
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        name = f"SGM_{mat.name}_preview"
        img = bpy.data.images.get(name) or bpy.data.images.new(name, w, h, alpha=True)
        if tuple(img.size) != (w, h):
            img.scale(w, h)
        sm.set_image_rgba(img, w, h, TC.recolour(rgba, mask, index))
        mat[sm.KEY_IMAGE] = base.name
        mat[sm.KEY_PREVIEW] = img.name
        if node is not None:
            node.image = img
        return {"FINISHED"}


def draw_material(layout, context) -> None:
    sm = _sm()
    obj = _part(context)
    mat = _mat(context)
    if obj is None:
        return
    if mat is None:
        layout.label(text="This slot has no material.")
        layout.operator("bz.ic_material_new", icon="ADD")
        return
    box = layout.box()
    if mat.get(sm.KEY_SHADER):
        info = json.loads(mat[sm.KEY_SHADER])
        same = mat.get(sm.KEY_SHADER_SRC) == obj.get("bz_source")
        box.label(text=f"Game shader: {info['name']}" + ("" if same else ", from another model"), icon="MATERIAL")
    elif mat.get(sm.KEY_PLACEHOLDER):
        box.label(text=f"No shader in the file for '{mat[sm.KEY_PLACEHOLDER]}'", icon="INFO")
    else:
        box.label(text="New material", icon="ADD")
    img = sm.diffuse_image(mat)
    if img is not None:
        w, h = img.size
        # reading every pixel to compare with the import's stamp is slow for a big picture: the answer is kept for a moment, and asked again
        # at once when the picture's size, its stamp or its unsaved flag (which paint sets) changes
        changed = enumitems.recent(("material image", img.name_full, tuple(img.size), str(img.get(sm.KEY_SCENE_HASH)), bool(img.is_dirty)),
                                   lambda: sm.image_changed(img))
        state = "changed: written as a new texture" if changed or img.name != mat.get(sm.KEY_IMAGE_SRC) \
            else "as in the file"
        box.label(text=f"Texture: {img.name} ({w} x {h}), {state}", icon="IMAGE_DATA")
        if not (_pow2(w) and _pow2(h)) or w > 1024 or h > 1024:
            box.label(text="Sides must be a power of two, at most 1024", icon="ERROR")
    else:
        box.label(text="No texture: drawn in its colour", icon="COLOR")
    row = box.row(align=True)
    row.operator("bz.ic_material_replace_image", icon="FILEBROWSER")
    row.operator("bz.ic_material_new", icon="ADD")
    box.prop(mat, "bz_ic_opacity", slider=True)
    box.prop(mat, "bz_team_on")
    if mat.get(sm.KEY_TEAM_ON):
        row = box.row(align=True)
        row.operator("bz.ic_team_mask", icon="BRUSH_DATA")
        sub = box.row(align=True)
        sub.label(text="Preview:")
        for value, label, _d in PLAYERS:
            sub.operator("bz.ic_team_preview", text=label.split()[-1]).player = value
        box.label(text="Paint the team area in player 1's green.", icon="INFO")


class BZ_PT_ic_material(bpy.types.Panel):
    bl_label = "IC Material"
    bl_idname = "BZ_PT_ic_material"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_order = 30

    @classmethod
    def poll(cls, context):
        return _part(context) is not None

    def draw(self, context):
        draw_material(self.layout, context)


CLASSES = (BZ_OT_ic_material_replace_image, BZ_OT_ic_material_new, BZ_OT_ic_team_mask, BZ_OT_ic_team_preview,
           BZ_PT_ic_material)


def register():
    bpy.types.Material.bz_ic_opacity = FloatProperty(
        name="Opacity", min=0.0, max=1.0, default=1.0,
        description="How opaque the game draws this material (0 = not at all)")
    bpy.types.Material.bz_team_on = BoolProperty(
        name="Team colour", default=False,
        description="Draw this material in each player's colour")


def unregister():
    for name in ("bz_ic_opacity", "bz_team_on"):
        if hasattr(bpy.types.Material, name):
            delattr(bpy.types.Material, name)
