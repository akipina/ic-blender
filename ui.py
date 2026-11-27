"""N-panel UI in the 3D viewport sidebar."""
from __future__ import annotations

import bpy

from . import ops
from . import newcreature
from . import newproject
from . import actions
from . import edittools
from . import sculptops
from . import fitops
from . import hardedges
from . import retargetops
from . import landmarkops
from . import texops
from . import topoops
from . import trimops
from . import patchfeel
from . import patchsel
from . import guards
from . import store
from . import combocore
from . import enumitems
from . import combopreview
from . import startops
from .sceneobj import ui_kit


SHOW_IK_CHAINS = False   # the IK chain box of Boner > Bones is not drawn (its code and operators are kept)
_alert = ui_kit.alert     # a red, wrapped message in the panel (moved to sceneobj.ui_kit so every panel shares it; same name for the callers)


def _stub_set(obj):
    """``(stub bone indices, error)`` for the add rule: the export's stub bones (``store.export_stubs``: the flagged ones
    and the point-less bones of any LIVE limb with no patches), None without the creature; ``error`` is the exception when
    they can't be read (the caches below say so as their check failing, and key on the set so a limb edit re-runs them)."""
    if obj is None:
        return None, None
    try:
        return store.export_stubs(obj), None
    except Exception as e:
        return None, e


_SKEL_CACHE: dict = {}


def _skeleton_error_cached(arm, obj=None):
    """rig.skeleton_error against the export's stub bones of ``obj`` (:func:`_stub_set`), recomputed only when the bones
    (names, parents, identity stamps: ``rig.bones_key``) or the stub set change (panel draw runs every redraw)."""
    from . import rig
    stubs, fail = _stub_set(obj)
    tables = tuple(tuple(obj.get(k, [])) for k in (store.KEY_LIMB_BONES, store.KEY_LIMB_BONE_COUNTS, store.KEY_LIMB_NAMES,
                                                  store.KEY_CONN_BONES)) if obj is not None else ()
    key = (rig.bones_key(arm), store.source_path(obj) if obj is not None else None, stubs, repr(fail), tables)
    hit = _SKEL_CACHE.get(arm.name)
    if hit is None or hit[0] != key:
        try:
            if fail is not None:
                raise fail
            from . import boneops, core
            src = core.read_source(store.source_path(obj)) if obj is not None else None   # cached parse (C8 rules)
            err = rig.skeleton_error(arm, src, src_stubs=stubs,
                                     rules=boneops.topology_rules(obj, src) if src is not None else None)
        except Exception as e:
            err = f"skeleton check failed: {type(e).__name__}: {e}"
        hit = (key, err)
        _SKEL_CACHE[arm.name] = hit
    return hit[1]


_RENAME_CACHE: dict = {}


def _bone_renames_cached(arm):
    """``(renames, warnings)`` of the rig -- ``[(import name, current name)]`` and :func:`boner.bone_rename_warnings` --
    recomputed only when the bones (names, parents, identity stamps) change (panel draw runs every redraw)."""
    from . import rig, boner
    key = rig.bones_key(arm)
    hit = _RENAME_CACHE.get(arm.name)
    if hit is None or hit[0] != key:
        try:
            sk = rig.rig_skeleton(arm)
            ren = sk.renames() if sk is not None and sk.resolution.stamped else []
            hit = (key, ren, boner.bone_rename_warnings(ren))
        except Exception as e:
            hit = (key, [], [f"bone rename check failed: {type(e).__name__}: {e}"])
        _RENAME_CACHE[arm.name] = hit
    return hit[1], hit[2]


_ADDED_CACHE: dict = {}


def _added_bones_cached(arm, obj=None):
    """``(added bone names the export takes, refusal sentences)`` of the rig against the export's stub bones of ``obj``
    (:func:`_stub_set`), recomputed when its bones (names, parents, identity stamps) or the stub set change."""
    from . import rig
    stubs, fail = _stub_set(obj)
    key = (rig.bones_key(arm), store.source_path(obj) if obj is not None else None, stubs, repr(fail))
    hit = _ADDED_CACHE.get(arm.name)
    if hit is None or hit[0] != key:
        try:
            if fail is not None:
                raise fail
            sk = rig.rig_skeleton(arm, src_stubs=stubs)
            hit = (key, list(sk.added_names) if sk else [], [w for _n, w in sk.added_bad] if sk else [])
        except Exception as e:
            hit = (key, [], [f"added bone check failed: {type(e).__name__}: {e}"])
        _ADDED_CACHE[arm.name] = hit
    return hit[1], hit[2]


class BZ_PT_panel(bpy.types.Panel):
    bl_label = "IC Blender"
    bl_idname = "BZ_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_order = 0

    @staticmethod
    def _rig_box(layout, obj):
        """Rig / animation controls shared by creatures and scene objects."""
        rigbox = layout.box()
        arm = store.rig(obj)
        rigbox.label(text=f"Rig: {arm.name if arm else '?'} ({len(arm.data.bones) if arm else 0} bones)", icon="ARMATURE_DATA")
        err = store.rig_error(obj)
        if err is None and arm is not None and store.is_native(obj):
            err = _skeleton_error_cached(arm, obj)
        if err:
            _alert(rigbox, err)
        if obj.get("bz_anims"):
            nact = len(actions.actions_of(arm)) if arm is not None else 0     # the clip Actions the rig really has (a project may have borrowed or dropped some)
            if nact:
                rigbox.label(text=f"{nact} clips as Actions", icon="ACTION")
            else:
                rigbox.operator(ops.BZ_OT_import_clips.bl_idname, icon="ACTION")
            rigbox.operator_menu_enum(ops.BZ_OT_load_anim.bl_idname, "anim", text="Load Animation")
            active = arm.animation_data.action.name if (arm and arm.animation_data and arm.animation_data.action) else None
            rigbox.label(text=f"active: {active}" if active else "active: none (rest pose)", icon="PLAY")
        else:
            rigbox.label(text="No animations")
        row = rigbox.row(align=True)
        row.operator(ops.BZ_OT_select_rig.bl_idname, icon="RESTRICT_SELECT_OFF")
        return rigbox

    @staticmethod
    def _ring_rows(layout, context, obj):
        """One read-only row per connection ring: name, slot, owner class, host bone, corner count and status (the
        cached combocore.ring_status), with Pick Ring in edit mode."""
        status = ops.ring_status_cached(obj, context)
        conns = store.connections(obj)
        if status and isinstance(status[0], str):
            _alert(layout, status[0])
            status = []
        col = layout.column(align=True)
        icons = {"OK": "CHECKMARK", "WARN": "INFO", "ERROR": "ERROR"}
        for i, (name, bone, ring) in enumerate(conns):
            st = status[i] if i < len(status) else None
            row = col.row(align=True)
            row.alert = st is not None and st.level == "ERROR"
            size = st.size_text if st is not None else f"{len(ring)}"
            row.label(text=f"{name}: {size}", icon=icons.get(getattr(st, "level", None), "DOT"))
            if obj.mode == "EDIT":
                row.operator(ops.BZ_OT_pick_ring.bl_idname, text="", icon="EYEDROPPER").conn = str(i)
            if st is not None:
                col.label(text=f"    {st.slot_label} · attach {st.attach} · host {bone or '-'}")
                for pr in st.problems[:1]:
                    if not pr.startswith("OK"):
                        sub = col.row()
                        sub.alert = pr.startswith("ERROR")
                        sub.label(text="    " + pr.split(" ", 1)[1][:90])
            else:
                col.label(text=f"    host {bone or '-'} (ring status unavailable)")

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        scene_obj = ui_kit.is_scene_context(context)     # a gatherer / character / structure / prop: the creature tools do not apply to it

        col = layout.column(align=True)
        col.operator(ops.BZ_OT_import.bl_idname, icon="IMPORT")
        col.operator(newproject.BZ_OT_new_project.bl_idname, icon="FILE_NEW")
        col.operator(newcreature.BZ_OT_new_creature.bl_idname, icon="ADD")
        startops.draw_start_buttons(col)        # Extract Stock Creatures: the game's animals as files, no creature needed
        if (not scene_obj and context.active_object and context.active_object.type == "MESH"
                and not store.is_native(context.active_object)):
            # Convert Mesh is not offered any more: Fit Patches to Mesh with the Own Quads layout does the same job (the
            # operator bz.convert_mesh still exists; DESIGN.md says why)
            col.operator(fitops.BZ_OT_fit_mesh.bl_idname, icon="MOD_SHRINKWRAP")

        if not scene_obj:                       # the creature surface display settings
            box = layout.box()
            box.label(text="Display", icon="MOD_SUBSURF")
            box.prop(scene, "bz_surface_only", icon="HIDE_OFF")
            box.prop(scene, "bz_show_surface")
            box.prop(scene, "bz_gn_surface", icon="GEOMETRY_NODES")
            for o in context.scene.objects:
                if o.get("bz_gn_error"):
                    box.label(text=f"{o.name}: {o['bz_gn_error']}", icon="ERROR")
            box.prop(scene, "bz_show_controlnet")
            box.prop(scene, "bz_shade_mode", text="")
            box.prop(scene, "bz_density")
            box.prop(scene, "bz_show_handles")
            box.prop(scene, "bz_show_connections")
            box.prop(scene, "bz_edit_opacity", slider=True)
            box.prop(scene, "bz_patch_rules")
            box.prop(scene, "bz_show_interiors")
            box.prop(scene, "bz_lock_handles")
            box.prop(scene, "bz_live_checks")

        obj = context.active_object
        if obj is not None and obj.get("bz_scene") and not store.is_native(obj):
            ui_kit.draw_scene_pointer(layout, obj, rig_box=self._rig_box)     # the info box and export buttons, or a pointer to the Entity panel
        if store.is_native(obj):
            # D10: every refusal the state already earns, not just the pool guard's (the rig box draws 'skeleton')
            guards.draw_problems(layout, obj, skip=("skeleton",) if obj.get("bz_rig") else ())
            info = layout.box()
            info.label(text=obj.get(store.KEY_NAME, obj.name), icon="OUTLINER_OB_MESH")
            info.label(text=f"{len(obj.data.vertices)} control points")
            info.label(text=f"{int(obj.get(store.KEY_NPATCH, 0))} patches")

            if obj.get("bz_rig"):
                rigbox = self._rig_box(layout, obj)

            info.prop(obj.data, "use_mirror_x", text="X-Mirror Editing")

            if obj.mode == "EDIT":
                mb = layout.box()
                mb.label(text="Modeling", icon="MESH_DATA")
                mb.operator(ops.BZ_OT_extrude_patch.bl_idname, icon="MOD_SOLIDIFY")
                mb.operator(patchsel.BZ_OT_delete_patch.bl_idname, icon="TRASH")
                mb.operator(patchfeel.BZ_OT_reset_interiors.bl_idname, icon="MOD_SMOOTH")
                r = mb.row(align=True)
                r.operator(patchsel.BZ_OT_set_vertex_type.bl_idname, text="Set Corner").kind = "CORNER"
                r.operator(patchsel.BZ_OT_set_vertex_type.bl_idname, text="Set Coplanar").kind = "COPLANAR"

                tp = layout.box()                       # B7 topology tools (topoops)
                tp.label(text="Topology", icon="MOD_REMESH")
                r = tp.row(align=True)
                r.operator(topoops.BZ_OT_weld.bl_idname, text="Weld").mode = "SELECTED"
                r.operator(topoops.BZ_OT_weld.bl_idname, text="Weld Target").mode = "TARGET"
                r = tp.row(align=True)
                r.operator(topoops.BZ_OT_extrude_sides.bl_idname, icon="MOD_SOLIDIFY")
                r.operator(topoops.BZ_OT_fill_hole.bl_idname, icon="MESH_GRID")
                r = tp.row(align=True)
                r.operator(topoops.BZ_OT_subdivide.bl_idname, icon="MOD_SUBSURF")
                r.operator(topoops.BZ_OT_loop_cut.bl_idname, icon="MOD_MULTIRES")      # one direction: round a limb, never across a ring
                r = tp.row(align=True)
                r.operator(topoops.BZ_OT_extrude_region.bl_idname, icon="FULLSCREEN_ENTER")
                tp.operator(topoops.BZ_OT_mirror_whole.bl_idname, icon="MOD_MIRROR")

                sel = layout.box()
                sel.label(text="Select", icon="RESTRICT_SELECT_OFF")
                r = sel.row(align=True)
                r.operator(ops.BZ_OT_select_kind.bl_idname, text="Corners").kind = "CORNER"
                r.operator(ops.BZ_OT_select_kind.bl_idname, text="Handles").kind = "HANDLE"
                r = sel.row(align=True)
                r.operator(patchsel.BZ_OT_hide_patches.bl_idname, text="Hide Patches", icon="HIDE_ON")
                r.operator(patchsel.BZ_OT_unhide_patches.bl_idname, text="Unhide", icon="HIDE_OFF")

            hardedges.draw_box(layout, obj)               # Mark Sharp edges -> smoothing groups, material slots -> materials

            sb = layout.box()
            sb.label(text="Sculpt", icon="SCULPTMODE_HLT")
            sculpts = sculptops.sculpts_of(obj)
            if sculpts:
                sb.label(text=f"in progress: {sculpts[0].name}", icon="INFO")
                r = sb.row(align=True)
                r.operator(sculptops.BZ_OT_refit_patches.bl_idname, icon="CHECKMARK")
                r.operator(sculptops.BZ_OT_discard_sculpt.bl_idname, text="", icon="X")
            else:
                sb.operator(sculptops.BZ_OT_sculpt_surface.bl_idname, icon="SCULPTMODE_HLT")

            tools = layout.box()
            tools.label(text="Tools", icon="TOOL_SETTINGS")
            tools.operator(topoops.BZ_OT_attach.bl_idname, icon="AUTOMERGE_ON")
            tools.operator(edittools.BZ_OT_symmetrize.bl_idname, icon="MOD_MIRROR")
            tools.operator(edittools.BZ_OT_seam_smooth.bl_idname, icon="SMOOTHCURVE")
            tools.operator(retargetops.BZ_OT_retarget_mesh.bl_idname, icon="MOD_SHRINKWRAP")
            lrow = tools.row(align=True)
            lrow.operator(landmarkops.BZ_OT_landmarks_create.bl_idname, icon="EMPTY_DATA")
            lrow.operator(landmarkops.BZ_OT_landmark_retarget.bl_idname, icon="MOD_WARP")
            tools.operator(trimops.BZ_OT_trim_stray.bl_idname, icon="MOD_MASK")
            fitops.draw_fit_help(tools, obj)              # a Fit result: shape only, and Move Skeleton to the Body
            vrow = tools.row(align=True)
            vrow.operator(edittools.BZ_OT_validate.bl_idname, icon="CHECKMARK")
            if hasattr(scene, "bz_combine"):
                vrow.prop(scene.bz_combine, "validate_level", text="")

            row = layout.column(align=True)
            row.operator(ops.BZ_OT_bake.bl_idname, text="Bake Textured Mesh", icon="MESH_DATA")
            if obj.get(store.KEY_TEX_IMGS):
                row.operator(ops.BZ_OT_paint_skin.bl_idname, icon="BRUSH_DATA")
                row.operator(startops.BZ_OT_load_skin_image.bl_idname, icon="IMAGE_DATA")
                row.operator(texops.BZ_OT_transfer_texture.bl_idname, icon="TEXTURE")
            row.operator(ops.BZ_OT_edit_uvs.bl_idname, icon="UV")
            if obj.get("bz_uv_cage") or obj.get("bz_uv_for"):
                row.operator(ops.BZ_OT_apply_uvs.bl_idname, icon="CHECKMARK")
            row.operator(ops.BZ_OT_export.bl_idname, icon="EXPORT")
            layout.label(text="For the game: IC Game tab", icon="PACKAGE")
        elif sculptops.is_sculpt(obj):
            sb = layout.box()
            sb.label(text=f"Sculpt of {obj.get(sculptops.KEY_OF)}", icon="SCULPTMODE_HLT")
            sb.label(text=f"density {obj.get(sculptops.KEY_DENSITY)}, {len(obj.data.vertices)} vertices")
            r = sb.row(align=True)
            r.operator(sculptops.BZ_OT_refit_patches.bl_idname, icon="CHECKMARK")
            r.operator(sculptops.BZ_OT_discard_sculpt.bl_idname, text="", icon="X")
        elif not scene_obj:
            layout.label(text="Select a creature (its mesh)", icon="INFO")


class BZ_PT_boner(bpy.types.Panel):
    """Boner: skin control points to bones and edit limbs and connections"""
    bl_label = "Boner"
    bl_idname = "BZ_PT_boner"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_parent_id = "BZ_PT_panel"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return store.is_native(obj) and not obj.get("bz_scene")

    def draw(self, context):
        layout = self.layout
        s = getattr(context.scene, "bz_boner", None)
        obj = context.active_object
        if s is None:
            _alert(layout, "the Boner settings are not registered (re-enable the add-on)")
            return
        row = layout.row(align=True)
        row.prop(s, "mode", expand=True)
        if s.mode == "BONES":
            self.bones_tab(layout, context, obj)
        elif s.mode == "LIMBS":
            self.limbs_tab(layout, context, obj)
        else:
            self.connections_tab(layout, context, obj)

    @staticmethod
    def bones_tab(layout, context, obj):
        """Boner's skinning loop: the active bone (search or eyedropper), its points, Assign / Select / Remove, the
        unassigned count and Auto Skin."""
        scene = context.scene
        s = scene.bz_boner
        arm = store.rig(obj)
        if arm is None or not store.bone_names(obj):
            layout.label(text="No rig, so no skinning", icon="INFO")
            return
        from . import boneops
        row = layout.row(align=True)
        row.prop_search(s, "bone", arm.data, "bones", text="Bone")
        row.operator(ops.BZ_OT_pick_bone.bl_idname, text="", icon="EYEDROPPER")
        op = row.operator(boneops.BZ_OT_rename_bone.bl_idname, text="", icon="GREASEPENCIL")
        op.bone = s.bone
        renames, rwarns = _bone_renames_cached(arm)
        if renames:
            layout.label(text="pending: renamed " + ", ".join(f"{o} -> {n}" for o, n in renames[:3])
                              + (f" (+{len(renames) - 3})" if len(renames) > 3 else ""), icon="SORTALPHA")
        for w in rwarns[:2]:
            _alert(layout, w)
        row = layout.row(align=True)
        row.operator(boneops.BZ_OT_add_bone.bl_idname, text="Add Bone under Active", icon="BONE_DATA")
        added, bad = _added_bones_cached(arm, obj)
        if added:
            row.operator(boneops.BZ_OT_adopt_bones.bl_idname, text="", icon="IMPORT")
            layout.label(text="pending: added " + ", ".join(added[:3]) + (f" (+{len(added) - 3})" if len(added) > 3 else ""),
                         icon="ADD")
        for w in bad[:2]:
            _alert(layout, w)
        row = layout.row(align=True)
        row.prop(scene, "bz_shade_mode", text="Overlay")
        row.prop(s, "follow_shading", text="", icon="LINKED")
        row.prop(s, "show_points", text="", icon="VERTEXSEL")
        stats = ops.skin_stats_cached(obj)
        idx = store.bone_index(obj, s.bone) if s.bone else -1
        if s.bone and idx < 0:
            _alert(layout, ops.active_bone(context, obj)[1])     # why: not a bone, not adopted, refused, check failed
        elif idx >= 0:
            bone = arm.data.bones.get(s.bone)
            parent = bone.parent.name if bone is not None and bone.parent else "(root)"
            n = int(stats.counts[idx]) if idx < len(stats.counts) else 0
            layout.label(text=f"{s.bone} · {n} point{'s' if n != 1 else ''} · parent {parent}", icon="BONE_DATA")
        col = layout.column(align=True)
        if obj.mode == "EDIT":
            r = col.row(align=True)
            r.operator(ops.BZ_OT_skin_assign_active.bl_idname, text="Assign Selected")
            r.operator(ops.BZ_OT_skin_select_points.bl_idname, text="Select Points")
            col.operator(ops.BZ_OT_skin_remove.bl_idname, text="Remove from Bone")
        else:
            col.label(text="Enter Edit Mode to assign points")
        r = layout.row(align=True)
        r.alert = stats.unassigned_used > 0
        r.label(text=f"Unassigned: {stats.unassigned}"
                     + (f" ({stats.unassigned_used} on patches: Export refuses)" if stats.unassigned_used else ""),
                icon="ERROR" if stats.unassigned_used else "DOT")
        if obj.mode == "EDIT":
            r.operator(ops.BZ_OT_skin_select_unassigned.bl_idname, text="Select")
        if stats.blended:
            _alert(layout, f"{stats.blended} points have blended vertex-group weights: Export refuses, and skin edits "
                           "stop until they are Undone or assigned")
        layout.operator_menu_enum(ops.BZ_OT_skin_auto.bl_idname, "target", text="Auto Skin", icon="MOD_ARMATURE")
        if obj.mode == "EDIT":
            layout.operator_menu_enum(ops.BZ_OT_assign_bone.bl_idname, "bone", text="Skin Selected to Bone")
        from . import ikops                                 # C8.4 / C8.5: the active bone's joint limits, the IK chains
        ikops.draw_limits_box(layout, context, obj, s.bone)
        if SHOW_IK_CHAINS:                                  # hidden (round 2 tidy): nothing in the game reads a chain a modder edits here
            ikops.draw_ik_box(layout, context, obj)

    @staticmethod
    def limbs_tab(layout, context, obj):
        """Boner's Limb Options: the limb list (name, class, patches, bones, status), Rename / Add Standard Limb /
        Delete, the active limb's classification boxes (Boner's exclusive groups), and the patch tools."""
        from . import boner, limbops
        s = context.scene.bz_boner
        t = limbops.limb_table_cached(obj)
        if t.error:
            _alert(layout, t.error)
            return
        lb = layout.box()
        lb.label(text=f"{len(t.rows)} limbs", icon="GROUP_BONE")
        active = int(s.active_limb) if 0 <= int(s.active_limb) < len(t.rows) else -1
        bad = {"no class", "duplicate", "not allowed", "needs patches", "no bones"}
        col = lb.column(align=True)
        for i, r in enumerate(t.rows):
            row = col.row(align=True)
            row.alert = t.status[i] in bad
            op = row.operator(limbops.BZ_OT_limb_set_active.bl_idname, text=r.name, depress=(i == active))
            op.index = i
            row.label(text=boner.class_label(r.limb_id))
            row.label(text=f"{t.patches[i]}p {len(t.bones[i])}b · {t.status[i]}")
        boneless = [r.name for r, st in zip(t.rows, t.status) if st == "no bones"]
        if boneless:
            _alert(lb, f"{', '.join(boneless[:3])}{', ...' if len(boneless) > 3 else ''}: no bones -- the creature "
                       "cannot be combined")
        for note in t.notes[:3]:                            # added bones the stored rows list elsewhere (or at all)
            lb.label(text=note, icon="INFO")
        for w in t.warnings[:2]:
            _alert(lb, w)
        row = lb.row(align=True)
        row.operator(limbops.BZ_OT_rename_limb.bl_idname, text="Rename", icon="GREASEPENCIL")
        row.operator_menu_enum(limbops.BZ_OT_add_standard_limb.bl_idname, "slot", text="Add Standard", icon="ADD")
        row.operator(limbops.BZ_OT_delete_limb.bl_idname, text="Delete", icon="TRASH")
        if active >= 0:
            r = t.rows[active]
            cb = layout.box()
            cb.label(text=f"{r.name}: 0x{r.limb_id:x} = {boner.class_label(r.limb_id)}", icon="PROPERTIES")
            for group, label in boner.FLAG_GROUPS:
                gr = cb.row(align=True)
                gr.label(text=label)
                for f in boner.LIMB_FLAGS:
                    if f.group == group:
                        op = gr.operator(limbops.BZ_OT_limb_flag.bl_idname, text=f.label, depress=bool(r.limb_id & f.bit))
                        op.index, op.bit = active, f.bit
            op = cb.operator(limbops.BZ_OT_limb_flag.bl_idname, text="Non-Swappable",
                             depress=bool(r.limb_id & boner.NOSWAP))
            op.index, op.bit = active, boner.NOSWAP
            BZ_PT_boner.limb_bones_box(layout, context, obj, t, active)
        pb = layout.box()
        if obj.mode == "EDIT":
            pb.operator_menu_enum(ops.BZ_OT_select_limb.bl_idname, "limb", text="Select Limb")
            pb.operator_menu_enum(ops.BZ_OT_assign_limb.bl_idname, "limb", text="Assign Selected to Limb")
        else:
            pb.label(text="Enter Edit Mode to assign patches")

    @staticmethod
    def limb_bones_box(layout, context, obj, t, active):
        """The active limb's bones (which bones swap with it): every name, Select / Set Root, the active bone and Add /
        Remove Active Bone."""
        from . import limbops
        s = context.scene.bz_boner
        bones = t.bones[active]
        bb = layout.box()
        bb.label(text=f"Bones of {t.rows[active].name}: {len(bones)}", icon="GROUP_BONE")
        col = bb.column(align=True)
        per = 4
        for k in range(0, len(bones), per):
            col.label(text=", ".join(bones[k:k + per]) + ("," if k + per < len(bones) else ""))
        if not bones:
            _alert(col, "none: a limb with no bones means the creature cannot be combined")
        arm = store.rig(obj)
        if arm is None:
            bb.label(text="No rig", icon="INFO")
            return
        row = bb.row(align=True)
        row.operator(limbops.BZ_OT_limb_select_bones.bl_idname, text="Select Bones", icon="RESTRICT_SELECT_OFF")
        row.operator(limbops.BZ_OT_limb_set_root.bl_idname, text="Set Root Bone", icon="BONE_DATA")
        row = bb.row(align=True)
        row.prop_search(s, "bone", arm.data, "bones", text="Bone")
        row.operator(ops.BZ_OT_pick_bone.bl_idname, text="", icon="EYEDROPPER")
        if s.bone:
            fn = limbops.file_bone_name(obj, s.bone)
            if fn is None and s.bone in t.added:
                fn = s.bone                                 # an added bone: the lists name it as Export writes it
            low = str(fn).lower() if fn else None
            inside = [t.rows[j].name for j, bl in enumerate(t.bones) if low and low in {str(b).lower() for b in bl}]
            clash = limbops.stamp_clash(obj, s.bone) if not fn else []
            bb.label(text=(f"{s.bone}: in {', '.join(inside)}" if inside else f"{s.bone}: in no limb") if fn
                     else f"'{s.bone}' shares its import stamp with {clash[0]} (a duplicated bone: delete the copy)" if clash
                     else f"'{s.bone}' is not a bone of this skeleton", icon="BONE_DATA")
        row = bb.row(align=True)
        row.operator(limbops.BZ_OT_limb_add_bone.bl_idname, text="Add Active Bone", icon="ADD")
        row.operator(limbops.BZ_OT_limb_remove_bone.bl_idname, text="Remove Active Bone", icon="REMOVE")

    @staticmethod
    def connections_tab(layout, context, obj):
        """Boner's Connection Options: the connection list (name, Parent / Child class, host bone, corners, status),
        Create for a limb / Rename / Delete, the active connection's Parent / Child boxes (Boner's exclusive groups;
        Non-Swappable stays on the limb), its host bone (search + Set / Default) and its ring (Pick Ring)."""
        from . import boner, connops
        s = context.scene.bz_boner
        t = connops.conn_table_cached(obj)
        if t.error:
            _alert(layout, t.error)
            return
        status = ops.ring_status_cached(obj, context)
        if status and isinstance(status[0], str):
            _alert(layout, status[0])
            status = []
        lb = layout.box()
        lb.label(text=f"{len(t.rows)} connections", icon="LINKED")
        active = int(s.active_conn) if 0 <= int(s.active_conn) < len(t.rows) else -1
        icons = {"OK": "CHECKMARK", "WARN": "INFO", "ERROR": "ERROR"}
        col = lb.column(align=True)
        for i, r in enumerate(t.rows):
            st = status[i] if i < len(status) else None
            n = len(r.corners if r.raw is None else r.raw)
            want = boner.ring_corner_count(r.flags2)
            row = col.row(align=True)
            row.alert = t.status[i] != "ok" or (st is not None and st.level == "ERROR")
            op = row.operator(connops.BZ_OT_conn_set_active.bl_idname, text=r.name, depress=(i == active),
                              icon=icons.get(getattr(st, "level", None), "DOT"))
            op.index = i
            row.label(text=f"{boner.class_label(r.flags1 & boner.CLASS_MASK)} > {boner.class_label(r.flags2 & boner.CLASS_MASK)}")
            row.label(text=(f"{n}/{want}" if r.raw is None else f"{n} ints") + f" · {r.bone or '-'} · {t.status[i]}")
        row = lb.row(align=True)
        row.operator_menu_enum(connops.BZ_OT_create_connection.bl_idname, "limb", text="Create for Limb", icon="ADD")
        row.operator(connops.BZ_OT_rename_connection.bl_idname, text="Rename", icon="GREASEPENCIL")
        row.operator(connops.BZ_OT_delete_connection.bl_idname, text="Delete", icon="TRASH")
        row = lb.row(align=True)
        if obj.mode == "EDIT":
            row.operator_menu_enum(connops.BZ_OT_add_stub_socket.bl_idname, "slot", text="Add Stub Socket",
                                   icon="BONE_DATA")
        else:
            row.label(text="Add Stub Socket needs Edit Mode", icon="INFO")
        missing = boner.limbs_without_connection([x.limb_id for x in t.limbs.rows], [r.flags2 for r in t.rows])
        if missing:
            _alert(lb, f"no connection: {', '.join(t.limbs.rows[j].name for j in missing[:3])}"
                       f"{', ...' if len(missing) > 3 else ''} (the creature cannot be combined until each has one)",
                   icon="ERROR")
        unpaired = boner.unmirrored_left([x.limb_id for x in t.limbs.rows])
        if unpaired:
            _alert(lb, f"no Right partner: {', '.join(t.limbs.rows[j].name for j in unpaired[:3])}"
                       f"{', ...' if len(unpaired) > 3 else ''} (the creature cannot be combined: re-class the "
                       "partner limb too)", icon="ERROR")
        if active < 0:
            return
        r = t.rows[active]
        cb = layout.box()
        cb.label(text=f"{r.name}: attaches {t.child_limb[active] or 'no limb'}", icon="PROPERTIES")
        cb.row(align=True).prop(s, "conn_target", expand=True)
        word = r.flags1 if s.conn_target == "PARENT" else r.flags2
        cb.label(text=f"Parent 0x{r.flags1:x} {boner.class_label(r.flags1)} · Child 0x{r.flags2 & boner.CLASS_MASK:x} "
                      f"{boner.child_class_text(r.flags2)}")
        for group, label in boner.FLAG_GROUPS:
            gr = cb.row(align=True)
            gr.label(text=label)
            for f in boner.CONN_FLAGS:
                if f.group == group:
                    op = gr.operator(connops.BZ_OT_conn_flag.bl_idname, text=f.label, depress=bool(word & f.bit))
                    op.index, op.target, op.bit = active, s.conn_target, f.bit
        hb = layout.box()
        hb.label(text=f"Host bone: {r.bone or '(none)'}", icon="BONE_DATA")
        arm = store.rig(obj)
        row = hb.row(align=True)
        if arm is not None:
            row.prop_search(s, "conn_bone", arm.data, "bones", text="")
        else:
            row.prop(s, "conn_bone", text="")
        op = row.operator(connops.BZ_OT_conn_set_host.bl_idname, text="Set")
        op.index = active
        op = row.operator(connops.BZ_OT_conn_set_host.bl_idname, text="Default")
        op.index, op.default = active, True
        if t.status[active] in ("no host", "host not in parent"):
            _alert(hb, "the host bone must be a bone of the limb the ring is cut into (Default picks one)")
        rb = layout.box()
        n = len(r.corners if r.raw is None else r.raw)
        rb.label(text=f"Ring: {n} / {boner.ring_corner_count(r.flags2)} corners" if r.raw is None
                 else f"Ring: {n} ints kept verbatim", icon="MESH_CIRCLE")
        if obj.mode == "EDIT":
            row = rb.row(align=True)
            row.operator(ops.BZ_OT_pick_ring.bl_idname, text="Pick Ring", icon="EYEDROPPER").conn = str(active)
            row.operator(ops.BZ_OT_set_connection_ring.bl_idname, text="From Selection").conn = str(active)
        else:
            rb.label(text="Pick Ring needs Edit Mode")
        row = rb.row(align=True)
        op = row.operator(connops.BZ_OT_optimise_ring_start.bl_idname, text="Optimise Start", icon="FORCE_VORTEX")
        op.index, op.scope = active, "ACTIVE"
        op = row.operator(connops.BZ_OT_optimise_ring_start.bl_idname, text="All Rings", icon="FORCE_VORTEX")
        op.index, op.scope = active, "ALL"
        st = status[active] if active < len(status) else None
        if st is not None:
            for pr in st.problems[:2]:
                sub = rb.row()
                sub.alert = pr.startswith("ERROR")
                sub.label(text=pr[:110])

def _fmt(x) -> str:
    return f"{x:.3g}" if isinstance(x, float) else str(x)


class BZ_PT_combine(bpy.types.Panel):
    """Combine the creature with another one and preview the result"""
    bl_label = "Combine Preview"
    bl_idname = "BZ_PT_combine"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = ui_kit.TAB_MODEL
    bl_parent_id = "BZ_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return store.is_native(obj) and not obj.get("bz_scene")

    def draw(self, context):
        import importlib
        layout = self.layout
        scene = context.scene
        obj = context.active_object
        s = scene.bz_combine
        paths = getattr(importlib.import_module(__package__), "PATHS_STATUS", {})
        stock_setting, extra = bpy.path.abspath(s.stock_dir), [d for d in (combopreview.user_stock_dir(),) if d]
        st = enumitems.recent(("combine status", stock_setting, tuple(extra), id(paths)),            # lists the stock folders: not on every redraw
                              lambda: combocore.status(stock_setting, extra, paths))
        if st["problems"]:
            box = layout.box()
            for line in st["problems"]:
                box.label(text=line, icon="ERROR")
        if not combocore.available():
            return

        # 1. role
        row = layout.row(align=True)
        row.prop(s, "role", expand=True)
        row.operator(combopreview.BZ_OT_combine_swap.bl_idname, text="", icon="ARROW_LEFTRIGHT")

        # 2. the other creature
        col = layout.column(align=True)
        col.row(align=True).prop(s, "other_kind", expand=True)
        if s.other_kind == "STOCK":
            if st["stock_dir"]:
                col.prop(s, "other_stock", text="")
                if combopreview.indexing(s):
                    col.label(text=f"indexing {st['stock_count']} stock creatures...", icon="TIME")
            else:
                col.label(text="No stock creatures: Extract above", icon="INFO")
            col.prop(s, "stock_dir", text="")
        elif s.other_kind == "OBJECT":
            col.prop(s, "other_object", text="")
        else:
            col.prop(s, "other_file", text="")

        # 3. part buttons (disabled when the donor has no limb for the part; alert when the combiner dropped it)
        pr = combopreview.current_result(obj)
        avail = combopreview.part_availability(scene, obj, s)
        box = layout.box()
        head = box.row(align=True)
        head.label(text=("Parts from " + ("the other creature" if s.role == combocore.ROLE_TORSO else "this creature")))
        head.operator(combopreview.BZ_OT_combine_parts.bl_idname, text="All").action = "ALL"
        head.operator(combopreview.BZ_OT_combine_parts.bl_idname, text="None").action = "NONE"
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for g, label in combocore.PART_LABELS.items():
            cell = grid.row(align=True)
            cell.enabled = avail.get(g) is not False
            dropped = pr is not None and g in pr.dropped_groups and getattr(s, f"part_{g}")
            cell.alert = dropped
            cell.prop(s, f"part_{g}", toggle=True, text=label, icon="ERROR" if dropped else "NONE")
        if pr is not None:
            for g, words in pr.dropped_groups.items():
                box.label(text=f"{combocore.PART_LABELS.get(g, g)} not taken: {words}", icon="ERROR")

        # 4. buttons
        row = layout.row(align=True)
        row.operator(combopreview.BZ_OT_combine_preview.bl_idname, text="Refresh" if pr is not None else "Preview",
                     icon="FILE_REFRESH" if pr is not None else "HIDE_OFF")
        row.operator(combopreview.BZ_OT_combine_clear.bl_idname, text="Clear", icon="X")
        row = layout.row(align=True)
        row.operator(combopreview.BZ_OT_combine_materialize.bl_idname, text="Materialize…", icon="OUTLINER_OB_MESH")
        row.operator(combopreview.BZ_OT_combine_validate.bl_idname, icon="CHECKMARK")
        if bpy.data.collections.get(combopreview.COLLECTION) is not None:
            layout.operator(combopreview.BZ_OT_combine_clear_materialized.bl_idname, icon="TRASH")

        # 5. advanced
        adv = layout.box()
        adv.prop(s, "show_advanced", icon="TRIA_DOWN" if s.show_advanced else "TRIA_RIGHT", emboss=False)
        if s.show_advanced:
            c = adv.column(align=True)
            for name in ("run_lua", "body_scale", "as_spec", "use_rules", "auto_refresh"):
                c.prop(s, name)
            c.prop(s, "script_source")
            c.prop(s, "default_size")
            c.prop(s, "placement")
            c.prop(s, "shade")

        # 6. readout
        if pr is None:
            return
        box = layout.box()
        if combopreview.is_stale_drawn(scene, obj, s):
            box.label(text="stale: press Refresh", icon="ERROR")
        box.label(text=pr.header(), icon="MOD_ARRAY" if pr.ok else "ERROR")
        if not pr.ok:
            return
        box.label(text=pr.summary())
        tbl = box.column(align=True)
        for row_d in pr.slots:
            r = tbl.row(align=True)
            r.label(text=row_d["slot"])
            r.label(text=row_d["source"] + (" *" if row_d["grafted"] else ""))
            r.label(text=f"×{row_d['scale']:.3g}")
            r.label(text=row_d["mode"])
            r.label(text=row_d["rule_from"] or "-")
        for w in pr.warnings:
            box.label(text=w[:120], icon="ERROR")
        if pr.stats:
            grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
            for _k, label, v, d in pr.stats:
                grid.label(text=label)
                grid.label(text=_fmt(v))
                grid.label(text="" if d is None or abs(d) < 1e-9 else f"{d:+.3g}")
        for n in pr.notes:
            box.label(text=n, icon="INFO")


CLASSES = (BZ_PT_panel, BZ_PT_boner, BZ_PT_combine)
