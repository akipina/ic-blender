# SGM Native Patches — Architecture

How the add-on works inside, as of 2026-11-17. It is a ground-up Blender add-on (not a fork) with three halves that
share one rule: **the editable model is the on-disk model, and an export rewrites only what changed.**

* **Creatures** (the patch `.sgm`): from *The core principle* to *Edit state and guard rails*, the larger part of this
  document; WYSIWYG bicubic-Bézier patch editing with no intermediate mesh and no control cage.
* **Gatherers, characters, structures and props** (the scene `.sgm`): *Scene objects* and *New Project*.
* **Blueprints and the sceneobj package**: *The sceneobj package* and *The Blueprint panel*.

*How exports stay byte-identical*, *Reuse / dependencies* (which ICParse modules are used where), the module map, the
tools and the tests close it. Each design record carries the date it was written. [STATUS.md](STATUS.md) says what is
verified, [ROADMAP_NATIVE.md](ROADMAP_NATIVE.md) what comes next, [GMAX_PARITY.md](GMAX_PARITY.md) how the creature
tools compare with gmax.

## Two kinds of `.sgm`, two paths

A creature `.sgm` (`FORM BOBJ`: a control-point pool, 16-index patches, limbs, connection rings, a skeleton, rigid
skinning, clips) and a scene `.sgm` (`NOBS` / `SIGM`: triangle meshes per part with stored normals and UVs,
per-material submeshes, bounding volumes, an optional skeleton with rigid part binding or per-vertex weights, clips)
are different layouts with different decoders in ICParse (`ICParse.sgm.creature`, `ICParse.sgm.scene`). The add-on
keeps them apart the same way: `bz.import_sgm` reads the file once (`core.read_source`) and sends a scene model
(`core.is_scene_model`: it has `meshes`) to `scene.build_scene`, everything else down the creature path. A creature
object is a *native* object (`store.is_native`); a scene part carries `bz_scene` and `bz_scene_part`. Neither path
touches the other's objects, and the creature tools hide under a scene object (`sceneobj.ui_kit.is_scene_context`).

## The core principle: the editable model *is* the on-disk model

A creature `.sgm` stores geometry as a **flat control-point pool** plus **patches**, each
patch holding **16 indices** into that pool (a 4×4 bicubic Bézier control net). Adjacent
patches share boundary control points as the *same pool index*.

We make a Blender mesh whose **vertices are exactly that pool, 1:1 and in file order**, with **one corner quad per
patch** (B6, below: never shaded, only for patch selection) and no other edges or faces -- the control net is a gpu
overlay. Consequences:

- **Untouched round-trip is the identity.** Verified: `write(read(x)) == x` and our
  world→local edit pipeline is byte-identical on every stock creature
  (`tests/headless_identity.py`).
- **Any edit is lossless** — you move a control point; we write that control point. No
  Catmull-Clark, no ACC regeneration, no 4-corner cage. (This is the key difference from
  the old `sgm_blender` cage import, which stored only 4 corners/patch and *regenerated*
  the other 12 on export.)
- **C0 continuity is structural, free.** Shared boundary points are shared verts; moving
  one moves both patches. You cannot tear a seam.

## Editing substrate — control-point mesh in native edit mode (Option A)

Because the control points are real mesh vertices, **Blender's entire native edit-mode
toolset applies directly**: box/lasso/circle select, G/R/S with axis locks, proportional
editing, mirror/symmetry, snapping, vertex slide, native undo. We reimplement none of it.
This maximises usability and minimises our surface area for bugs.

## Display — live gpu overlay (+ optional bake)

`draw.py` runs one `POST_VIEW` draw handler that, per native object, reads the control
points *as they are right now* (object mode or live edit-mode bmesh), tessellates the exact
bicubic surface via ICParse's Bernstein evaluator (`core.tessellate`), shades it, and draws
it depth-tested. Batches are cached and rebuilt only when control points or density change,
so dragging re-evaluates just what moved.

- **Editor-only by design** — the surface is a viewport overlay, invisible to Eevee/Cycles.
- **Bake to mesh** (`BZ_OT_bake`) produces a real mesh of the evaluated surface when you
  want material preview / final render. Read-only, regenerated from the control points.
- **Or real evaluated geometry** — Display > *Evaluated Surface* (D9, below) swaps the overlay for a
  Geometry Nodes modifier that builds the same surface as actual mesh. Default off.

## Evaluated surface — the same maths in Geometry Nodes (D9, 2026-09-16, optional)

`gnsurface.py`. The overlay is exact but it is a *drawing*: Sculpt Mode, downstream modifiers, snapping,
ray-cast selection and the renderer all read *geometry*, and a drawing is not geometry. Display >
**Evaluated Surface** (`scene.bz_gn_surface`, default **off**) gives every creature a Geometry Nodes
modifier that evaluates the same Bernstein surface, so the depsgraph-evaluated mesh **is** the surface.

- **The patch table travels as mesh attributes.** A node group cannot hold a table, but the mesh already
  has one face per patch (the B6 corner quads), so `write_tables` writes FACE attributes: `bz_p0..bz_p15`
  (the patch's 16 pool indices), `bz_uv0..3` (its stored corner UVs) and `bz_row` (which patch row the
  face carries — face *i* is patch `patchsel.face_rows[i]`, so a creature with a degenerate patch still
  names its rows correctly). The group builds its own output (an n×n `Grid` instanced on one point per
  face, realized), then per output point: `Sample Index` (FACE) for the 16 pool indices, `Sample Index`
  (POINT) for those control points' positions, and the bicubic sum at its own (u, v) — which it also
  writes back as `bz_pid` / `bz_u` / `bz_v`, so any tool (and the test) can match a surface point to its
  patch. 168 nodes / 301 links, **built by script**: no .blend asset, nothing to install.
- **Appearance.** `apply_appearance` puts the creature's material slots on the object (the same
  `material.build_blender_materials` the bake uses) and each corner quad on its patch's slot; the group
  samples that index and interpolates the 4 stored corner UVs bilinearly into a `UVMap` (V flipped, like
  `core.surface_uvs`). Two Blender facts the spike had to find: generated geometry carries **no material
  list**, so the input mesh is joined in for its list (and its own points deleted again in the same
  group) — the multi-input socket's *reverse* link order decides whose slots come first; and a UV map a
  node group creates from nothing is **not the active-render one**, so an Image Texture node with nothing
  on its Vector input samples one texel and the creature renders flat-coloured. The control mesh gets an
  empty `UVMap` layer so the output inherits the flags.
- **Nothing exported changes.** The exporter reads vertices (`ops._mesh_verts`) and the pool guard reads
  faces, so face attributes, material slots and a UV layer are invisible to it: every export in
  `tests/gn_surface_test.py` is byte-identical to the stock file. `ops.object_state_error` lets this one
  modifier through by name; Bake Surface > *Posed* reads the control points with it switched off
  (`gnsurface.posed_control_points`) instead of baking the surface of the surface; `draw._current_verts`
  refuses an evaluated mesh whose vertex count is not the pool's; and `draw` stops drawing the surface
  overlay for an object that has the modifier (they would z-fight).
- **The tables follow the topology.** A `depsgraph_update_post` handler (`gnsurface.refresh`) rewrites
  them whenever the journal generation, the patch count or the mesh size changes — Delete Patch, Extrude,
  Subdivide, Weld, Undo, a re-import — so the surface is always the *current* patch table's. Switching
  the option on over a broken pool is refused in words (`store.pool_error`), never built wrong.
- **Why it is not the default.** Geometry Nodes has no dirty-patch notion: one control point moved costs
  the whole tree. Measured (median of 5, headless depsgraph time; `wm.redraw_timer` needs a window):
  800 patches at density 5 — **12.0 ms** for the node tree against the overlay's **1.49 ms** for the two
  dirty patches (the overlay's *full* evaluation is 38.3 ms, so Geometry Nodes is 2–5× faster at doing
  everything and 8× slower at doing one patch). And the overlay still owns what the node surface has no
  equivalent of: By Bone / By Limb colouring, per-patch Hide, the engine-exact normal weld, the
  connection-ring overlay — and the 6 base-game patches whose four corners are not distinct, which have
  no corner quad to carry a table row and are not evaluated (`gnsurface.missing_patches` counts them).

## Continuity — free tangents + optional smooth (decided)

C0 is automatic (shared verts). Tangent handles stay **exactly as the file stores them** →
full fidelity, intentional creases preserved. A future "smooth seam" tool will symmetrise
tangents *only where asked*. We do **not** enforce G1 globally — that would erase the
deliberate creases stock creatures contain (lossy for a preservation tool).

## Patch feel — corners carry handles, interiors automatic (B5, 2026-09-15)

The pool has three strict roles (measured on the base game: no point has two): corners (slots 0, 3, 12, 15, shared
by 2-7 patches), edge handles (the 8 edge slots, shared by 2), interiors (5, 6, 9, 10, one patch each). Every stock
interior is Max's **Auto Interior**: `P11 = P01 + P10 - P00` in its corner's frame (interior = row handle + column
handle - corner), within 1e-5 of the patch's bounding diagonal on 99.977 % of the 17,048 base-game patches (the four
exceptions: lobster 98 / 251 at 1.5e-5, musk_ox 306 / 327 at 7e-4); the midpoint, Coons and bilinear-corner
candidates fit 0 %. The rule is measured, not assumed (`tests/patchrules_test.py` re-measures it).

Blender's transform moves exactly what is selected, so the Max feel is applied after the fact from a before / after
diff of the pool (`patchrules.carry_handles`, bpy-free): a corner that moved drags every handle attached to it (in
every patch using it) by its delta unless the handle moved itself (a tangent edit; a handle two moved corners share
gets their mean delta); then every patch with a changed corner or handle gets its interiors recomputed, unless it is
flagged manual (`store.KEY_MANUAL_INTERIOR`, a journaled per-patch table) or an interior was just moved directly to
somewhere the rule does not explain (which flags it). An interior already within the tolerance is never rewritten, so
an Undo that restores a patch writes nothing and an untouched creature stays byte-identical. Anchors make a cancelled
drag exact too: a point is anchored where it first moved or was first carried, a carried handle sits at its anchor plus
its corner's offset from the corner's anchor (not at last frame plus a float32 delta), and an interior whose whole
frame is back on its anchors goes back to its own pre-edit bytes; a handle edited by itself re-anchors itself and the
interiors it frames. So G, drag over any number of frames, Esc leaves every point bit-identical (`patch_feel_test` H);
the anchors live in `patchfeel._anchors` and go with the draw state (`draw.invalidate`). Live: `draw._sync`
already diffs the edit mesh against the last redraw; `patchfeel.settle` runs the rule on that diff and writes the
carried handles and recomputed interiors into the bmesh BEFORE the redraw's tessellation, so a G drag shows the
carried surface (only patches with changes are touched; X-mirror partners are moved by Blender and carried by us;
scene objects and the `bz_patch_rules` = off case are untouched). The interior points carry Blender's vertex hide
flag (set at import and by Extrude Patch; `bz_show_interiors` reveals them): not drawn, not selectable, not
transformed, kept by edit-mode Undo and Tab. A hidden interior follows its frame corner in the selection the skin
edits read (`ops.point_selection` -> `patchfeel.selection`) when it is skinned to the same bone as that corner --
which is the stock skin: 68,192 / 68,192 base-game interiors are owned by their frame corner's bone -- so a box
selection never leaves 44 % of the pool behind unseen; one skinned to another bone stays (the Assign operators warn,
naming it: `patchfeel.selection_note`; Show Interior Points selects it by hand). The scaffold of New Creature does
not follow that convention (820 / 992 at detail 1) and its interiors are not on the Auto Interior rule at all (0 %:
they are ACC-derived), so the first drag on a scaffold patch recomputes them. `BZ_OT_reset_interiors` (Modeling >
Auto Interiors) puts selected patches back on the rule and clears their flags. Show Handle Dots draws handle dots
only for the handles of selected corners.

## Patch-level selection -- corner quads, hide, delete, vertex types (B6, 2026-09-15)

- **Faces.** The mesh holds one quad per patch over its corners in the order (0, 12, 15, 3) -- the winding whose
  Newell normal agrees with the engine surface normal on every patch of the stock creatures probed (cheetah, snail,
  eagle: 820 / 820) -- built by `build.py` from `patchsel.face_quads` and kept by Extrude Patch (`patchsel.add_face`)
  and Delete Patch. A patch with two corner slots on one pool point (6 in the base game) gets no face; two patches
  over the same four corners (a front / back fin: hornbill, lammergeier) get two faces over the same vertices, which
  Blender tolerates. `patchsel.face_rows(patches)` is the one rule for which patch has a face and face i is patch
  `face_rows[i]`. The object stays `display_type = 'WIRE'`, so Solid shading draws no fill and Edit Mode draws only
  the quads' edges (the patch outline, orange when selected); the surface stays the overlay. What the faces buy is
  Blender's: Face select mode is patch select, and box / lasso select with X-ray off is occluded by the faces (the
  select engine's depth test; not testable headlessly, and not code here). Everything that reads the mesh for export
  reads vertices only (`ops._mesh_verts`, `edittools._verts`, the bake, the sculpt, the fits), so the faces never
  reach the file; untouched creatures stay byte-identical (the whole corpus, `headless_identity`).
- **Guard.** `store.pool_error` ends with `patchsel.face_error`: exactly one corner quad per patch, in patch order,
  each over its patch's corners; a native face delete or a hand-made face is refused with the count, Delete Patch and
  Undo named (Export blocked, the pool-indexing operators disabled). The cached guard keys on the face count too.
- **Selection.** `patchsel.selected_rows` (the selected, unhidden faces) is what `ops._selected_patches` returns
  first; a 4-corner vertex selection is flushed to its face by Blender anyway, and the old rule is the fallback.
  `select_rows` selects faces (deselecting first: deselecting a face deselects its corners, shared ones included).
- **Hide.** The overlay reads the per-patch hidden mask off the face hide flags every redraw
  (`patchsel.hidden_rows`, `draw._apply_hidden`): a change drops the index batches only (the surface is not
  re-evaluated); `_vis_tris` slices the tessellation per patch, the control net is rebuilt over the visible patches,
  handle dots skip points every patch of which is hidden. `BZ_OT_hide_patches` hides the quads and the corners /
  handles no visible patch uses; `BZ_OT_unhide_patches` reveals faces, corners and handles and re-applies the interior
  rule (Blender's Alt+H would reveal the interiors). The keymap (`patchsel.register_keymap`, the add-on keyconfig's
  Mesh keymap, handled before Blender's own; each operator's poll passes only on a native creature in Edit Mode, so
  every other object keeps Blender's keys): X and Delete → `bz.delete_menu` (Patches, or Blender's vertex / edge
  delete, which the pool guard then refuses as before), H / Shift+H → Hide Patches in Face select mode only, Alt+H →
  Unhide.
- **Delete Patch.** `patchsel.delete_plan` (pure): the kept rows, the points to drop (used by a deleted patch and by
  no kept one; a point no patch used before stays, as in the file), `o2n` (-1 for a dropped point), and the refusal
  for a patch with a side on a connection ring (`ring_patch_refusals`: a side joining two consecutive corners of a
  ring's loop; a verbatim ring counts every int it holds). The operator begins a generation, removes the quads, the
  edges no kept quad uses and the dropped vertices from the bmesh (per-point attributes -- owners, `.bz_coplanar`,
  `.bz_src`, deform weights -- travel with their vertex), restamps `.bz_cp` / `.bz_gen`, writes every per-patch table
  through the kept mask (read against the OLD count first), remaps every ring (corner loops and verbatim ints), and
  then leaves and re-enters Edit Mode: **BMesh reuses a removed vertex's pool slot for the next vertex made**, which
  would put Extrude Patch's new points in the middle of the iteration order and shift every index silently; the
  mode toggle rebuilds the bmesh from the mesh arrays with no free slots (Extrude Patch also refuses, naming Tab, when
  it finds its new points not at the tail). The export takes the grown path with the compacted pool; `EditState.pool_src`
  (`.bz_src`: each point's source index, -1 for a point made since; set at import, kept by Extrude and Delete) lets
  `_build_grown` keep an untouched point's source bytes by identity instead of by position and lets
  `export_summary` pair source patches and rings with the file's (`s2f`), so the summary does not misreport after a
  delete. Holes are the user's: Validate blocks on the dangling edges (`ICParse check_model`) and the Object Editor
  refuses the file (6 `Patch model has dangling edge` lines, FATAL, for the cheetah minus 2 patches).
- **Vertex types.** `.bz_coplanar` (per point; 1 = Coplanar, default Corner, every stock handle is free) and the
  scene's `bz_lock_handles` feed `patchrules.carry_handles(coplanar=, lock=)`. Set Coplanar projects the handles onto
  their least-squares plane through the corner (`project_coplanar`) and resets the framed interiors. In the rule
  (`_rotate_siblings`, step 1b, after the carry and before the interiors): the lowest-indexed handle of a corner moved
  by itself is the driver; Lock turns the corner's other handles (the ones the user did not move) by
  `rotation_between(home direction, current direction)`; Coplanar takes the least-squares normal of the home
  directions, tilts it by the least rotation that makes the plane contain the driver's current direction, and turns
  the siblings with it (a driver moved within the plane turns nothing). **Home anchors** (`rel` / `rel_act`: absolute
  positions of the corner and its handles at the start of a gesture) make a cancelled drag exact: a driver back at its
  home direction (within `REL_TOL`) puts the siblings on their homes bit-exactly, and a frame whose corner is at its
  anchor and whose handles are at their home directions puts its interior back on its own anchor. A **commit** is
  detected when a homed handle is not moving while its corner moves (or another handle of a Corner-type corner moves
  by itself) and is off its home direction: the corner's homes and carry anchors are re-anchored where they are and
  the framed interiors keep their current values, so a corner's out-and-back after a tangent edit or a turn returns
  bit-exactly (the old per-frame interior re-anchoring for tangent edits went away with this). Rules off, or a corner
  of the Corner type with Lock off: siblings stay, as before.

## Topology tools -- weld, attach, mirror, subdivide, add quad, fill hole (B7, 2026-09-15)

`topoops.py`. The watertightness the game wants is **shared pool indices along a side**: a side is 4 points (2
corners, 2 handles), and two patches share a side only when all four coincide in the loader's order
(`ICParse.validate._edge_key`; an edge used by one patch is a dangling edge and the Object Editor refuses the file,
pinned FATAL in `run_all`). Every tool here therefore works on indices first and positions second.

- **One plan, one applier.** Each tool is a pure function (bpy-free, numpy) returning a `TopoPlan`: `alias` (which
  old point merges into which), `new_points` / `new_from` (the points it makes and the old point each derives from),
  the new patch table in *virtual* indices (old index, or `n_old + j` for new point j), every per-patch table in full,
  `moved` (old point -> new position), `row_src` (which old row each new row continues, for the hidden mask), a
  `refusal` in words, warnings, and an info dict for the report line. `finalize` computes the compaction (`keep`,
  `o2n`, `patches_final`) through **`patchrules.compaction`**, the single copy of that rule (Delete Patch calls it
  too): a point stays when it is its own survivor and either a patch still uses it or no patch used it before (the
  file's own rule); a merged point maps onto its survivor's new index. `apply_plan` is the only writer: it drops the
  corner quads and edges, removes the merged / dropped vertices, **leaves and re-enters Edit Mode** (the B6 BMesh
  slot-reuse trap: a removed slot would be reused for the next vertex made and shift every index), appends the new
  points and the corner quads through the mesh arrays (`patchsel.face_quads`; two faces over the same corners are only
  possible there), carries every per-point attribute (owners, `.bz_src` = -1 for a made point, `.bz_coplanar`, deform
  weights through `store.set_vert_bone`), writes every per-patch table, remaps every connection ring
  (`patchsel.remap_ring`), keeps the hidden patches hidden through `row_src`, and ends in one undo step.
- **Weld** (`weld_plan`). Selected corners within the threshold cluster (survivor = the lowest index, position = the
  mean); Weld Target puts every selected corner on the active one, which does not move. Handles follow their corner
  by its delta (the B5 carry rule). Then the seam: two sides whose corner pairs have become the same pair are one
  side, so their **handle pairs merge too** (mean position, lowest index survives) -- without that the corners would
  touch and the side would still be dangling. Refused: fewer than two corners, nothing within the threshold, a patch
  that would get two corners on one point (named), a corner pair that would end up with three patches on one side, a
  connection ring that would lose a corner. Touched patches' interiors go back on the Auto Interior rule (manual ones
  stay). Non-corner points in the selection are ignored and counted.
- **Attach** (`attach_plan`). The other native creature's pool is appended (its object transform applied), its patch
  rows offset by the host's pool size, its per-patch tables appended (limb = the limb you choose, material = the slot
  with the same name else slot 0 with a warning, types and UVs as they are), its points skinned to the bone you
  choose, its connection rings dropped (a socket belongs to the creature it is cut into) and its object and rig
  removed. Refused: not a native creature, a pool-guard error on either, more than `MAX_PATCHES` (767 = the engine's
  12,288 display vertices / 16 per patch, and 16 is the BEST case -- a patch costs up to 25, so the real budget is
  measured per creature by `ICParse.validate.display_vertices`, see `ICParse/PATCH_CEILING.md`;
  the control-point pool itself has no ceiling -- PRIM stores 32-bit
  indices). The two shells are one creature but not yet one surface: the report says to weld the seams.
- **Mirror to Whole** (`mirror_plan`). The selected patches (all when none) must lie on one side of X = 0. A point
  within the threshold of the plane is **shared** by the original and its mirror (the weld; snapped exactly onto the
  plane when asked), every other point is mirrored to a new one. The mirrored patch is the **transposed** grid (rows
  <-> columns): the mirror flips the parametric normal once and the transpose flips it back, so the surface still
  faces outward and the seam is still traversed in opposite directions by its two patches (0 same-direction seams
  after the mirror, the stock rule). Owners go to the mirror bone (`mirror_name`: the `_l` / `_r`, `.l` / `.r`,
  `left` / `right` spellings the stock skeletons use), limbs to the mirror class (the Left / Right bits of the limb
  id). Connections are *not* mirrored (warned): a socket is authored in Boner. Refused: a selection straddling the
  plane, unselected patches already on the far side (delete them first), everything on the plane.
- **Subdivide** (`subdivide_plan`). De Casteljau at u = v = 1/2, so the surface does not change (measured: 3e-8 m on
  the cheetah = the float32 pool's own quantisation; exact in float64). A neighbour across a split side would be left
  with a **T-junction**, which the format cannot express (its side would be two sides of the neighbour's), so it is
  split in the matching direction and its neighbours along that strip in turn -- Max's *Propagate*, always on here: a
  loop cut around the body per direction (`propagate_splits`; one cheetah leg patch propagates to 21 neighbours,
  288 -> 314 patches). The new points along a shared side are computed once from that side's 4 old points and shared
  by both patches, so every seam stays 4 shared indices. A sub-patch's exact interiors are generally **off** the Auto
  Interior rule (the rule says zero twist at a corner, and a corner made mid-patch has twist), so such a sub-patch is
  flagged manual: the surface is what was asked for, and Modeling > Auto Interiors relaxes it. Refused: a split that
  would cut a **connection ring's side**, naming the ring (the combiner welds a ring by its corner count along patch
  sides, so a ring side cannot gain a corner) -- on the cheetah that leaves 80 of 288 patches subdividable and **none**
  of the torso's 32, because every torso strip crosses one of the 12 sockets.
- **Add Quad** (`extrude_sides_plan`) is Extrude Patch on every selected open side at once (a patch's open sides, an
  outline edge in Edge select, or a side's two corners), each new patch continuing the surface by that side's own
  outward step (`core.extrude_patch`'s rule and `_EXTRUDE_LAYOUT`, so the seam is wound the stock way); sides meeting
  at a corner share that corner's new column (mean step), so a loop of open sides extrudes into a watertight collar.
- **Fill Hole** (`fill_plan`). `hole_loops` walks the open sides into closed loops (a corner where more than two open
  sides meet gives none). A loop of 2n sides is filled with a strip of n - 1 patches, the chords chosen shortest; the
  existing sides are used **as they are** (their own 4 points), traversed against their patches, so the fill is
  watertight by construction; the new chords get handles at thirds, the corner UVs / limb / material / type come from
  the neighbours (majority) and the interiors from the rule. Refused: a closed creature, an odd or 2-side loop (what
  to do instead), a selection touching no hole or more than one.
- **Extrude Region** (`extrude_region_plan`) is Max's Extrude & Bevel over a patch selection: the region is lifted
  along its mean corner-quad normal by Amount and scaled about its centre by Outline, the points it shares with
  unselected patches are copied (the region takes the copies) and a wall patch spans each boundary side from the old
  4 points to the new 4. `bz.extrude_drag` / `bz.add_quad_drag` are `bpy.types.Macro`s: the tool at amount 0, then
  Blender's own translate on the points it leaves selected.
- **Export.** A source `LimbConnection` record holds SOURCE pool indices, so after a compaction (Delete Patch) they
  are not the current pool's: `core._build_grown` now maps them source -> current pool (`st.pool_src`) -> file, and a
  source ring whose points went with a deleted patch is left alone (the stored table carries its own, already-current
  ring, and `structure.apply_connections` checks that one). Before this, deleting a socket and then its patches --
  the very thing Delete Patch's refusal tells you to do -- made Export raise `ring index out of the N-point pool`.

## Refusals at action time (D10, 2026-09-16)

`guards.py`. Every refusal below was already there; what was missing is **when** you hear it. A modder could move the
object, add a Subdivision Surface, add shape keys or replace the base `.sgm` on disk and learn of it at **Export**,
an hour later. The refusals now run as a READ of the object's state the moment the edit lands, and the Export refusal
stays exactly as it was -- it is the backstop, and every message shown here IS its message, produced by its own
function (`store.pool_error`, `store.source_error`, `store.ring_store_error` / `limb_store_error`, `store.rig_error`,
`ops.object_state_error`, `ops.uv_cage_error`, `rig.skeleton_error`, `core.skin_owners`), never a second wording that
could drift.

- **Nothing is fought.** No edit is cancelled, fixed or undone. `guards.issues(obj)` reads and returns
  `Issue(code, level, text)`; the SGM panel draws them as the **Problems** box (the rig box keeps `skeleton`, so it is
  said once) and `draw._draw_text` draws them in the viewport, so a mistake shows with the sidebar closed too.
- **Off the hot path.** `state_key(obj)` is the cheap digest the checks depend on -- counts, mode, generation probe,
  `KEY_GEN`, patch count, **whether** each transform is identity (not 32 floats: an Object-Mode drag then changes the
  key once, on the step that leaves the origin, not 60 times a second), the modifier stack, the source file's `stat`,
  the rig's bone key and the UV cage's name. Measured on cheetah: **15 us** per update, and an Edit-Mode scrub costs
  **+88 us per step** with the handler (211 vs 123 us headless). Two things cost enough to be cached apart: the pool's
  generation is read from the FIRST point (`gen_probe`) instead of `store.mesh_generation`'s whole-pool pass, which
  through the edit bmesh is one python step per point (~400 us, four times the cost of the whole scrub step); and
  `rig.bones_key` (92 us on 54 bones) is re-read only for an armature the depsgraph actually updated.
- **What the key cannot see** gets a clock instead: a pool REORDER keeps every count, and a vertex-weight edit changes
  nothing at all, so the shallow checks re-run at most every 0.5 s and the vertex-group pass (4.8 ms) every 1 s, both
  only while something is drawing.
- **`store.pool_error`, not `pool_error_cached`.** That cache is what the operator polls read and its key cannot see a
  reorder, so warming it from here would hand a poll a stale "fine" for its TTL (caught by `guard_test` part A).
- **Two refusals stay at Export on purpose.** A point in NO bone group is not called a weight edit here: a bone deleted
  on the rig leaves its points in a group no bone deforms, which is a legitimate C8 edit the export resolves (the
  points go to the bone's parent) -- Boner's own `Unassigned: N (M on patches)` read-out says it instead. And the
  Actions plan (`actions.plan_export`, which hashes every Action) and the added-bone / stub-bone owner checks (which
  need the export's bone insert) are too dear per update; the Bones tab already shows the added-bone refusals from its
  own cached read-out.
- **Display > Live Checks** (`scene.bz_live_checks`, on) turns the handler and the read-outs off in one click.

## Edit state and guard rails (R2-R4, 2026-09-14)

Everything Blender can hold but the file can't, or that native tools can break, is either written, adopted from
Blender's own data, or refused with the reason and the fix. Nothing is dropped silently.

- **One export builder.** Export, Validate and the combine preview gather one `core.EditState` (`ops.gather_export`,
  guards first) and build with `core.build_export`: the fast path rewrites only what differs (byte-identical when
  nothing does), the grown path rebuilds geometry and remaps patches, limbs and rings. Clips are encoded afterwards
  against the built model; `core.export_summary` diffs the final model against the source for the report line.
- **Two kinds of storage, one journal.** Per-point data (`.bz_cp` pool ids, `.bz_vert_bone`, `.bz_gen`) lives in mesh
  attributes, which edit-mesh undo restores; per-patch / per-record tables are Object ID properties, which it does
  not. Every SGM operator starts a generation (stamped on the points) and every table write is mirrored into
  `bz_journal[generation]` and into a session-side copy outside the undo system, so an Undo / Redo (also across Tab,
  where a memfile step restores the object's journal from before the edit session) re-selects the tables of the mesh
  state. The journal keeps at least as many generations as Blender keeps undo steps.
- **Identity, not names.** The pool guard compares `.bz_cp` with `arange(N)` (Delete / Merge / Sort / Duplicate are
  named and refused). Bones carry their source index (`bz_src_index`), so a renamed bone is still that bone (B6: it
  exports under its new name), a deleted or re-parented bone exports with its record dropped / re-hung (C8, below); an added bone exports when
  the add rule takes it (B7, below; a stub bone Add Stub Socket created is its own case). Rigs, Actions, images and UV cages are found by the
  creature's `bz_uid` stamp.
- **Bone renames (B6, 2026-09-15).** `structure.resolve_bones` is the identity rule: a stamp held by one rig bone is
  that source bone; among copies (Duplicate and Symmetrize copy ID properties) a copy holding exactly the import name
  of a source bone no rig bone is stamped with is THAT bone (Symmetrize onto an existing mirror bone overwrites its
  stamp; Rename Bone restamps it), then the one keeping the import name wins, else the one keeping the import rest,
  else they are ambiguous and refused (a mirror bone renamed before the Symmetrize has no name left to go by:
  `BoneResolution.overwritten`, its own refusal); a rig without stamps matches by name (a
  rename found by rest is refused: Rename Bone stamps such a rig first). `rig.skeleton_error` refuses added / deleted /
  re-parented bones, ambiguous copies, rename chains (`structure.name_conflicts`: a bone now called what another bone
  was called at import or created as -- the stored rows and un-fixed F-curve paths name bones by their import names,
  so such a name would mean two bones) and names the file can't hold (`bone_name_problems`: 63 printable ASCII
  characters, unique ignoring case). (Since 2026-09-15 an added bone passes when the add rule below takes it, and a
  deleted or re-parented source bone when the C8 rules below allow it.) Limb rows and connection rows keep storing FILE names; the export maps them in one
  place, `structure.apply_bone_renames` (before the stub bones are appended), which renames the BONE records and every
  reference -- limb lists, hosts, IK chains through `ICParse ik_chains / set_ik_chains` (rebuilt only when a chain
  changed) and the kept clips' tracks -- matching ignoring case and keeping each reference's spelling style
  (`respell`; tracks stay lower case like the base game's); a respelt track duplicating another track of its clip is
  refused, a rename onto an orphan track's name (a track naming no bone) warns that its keys now play on the bone.
  A created stub bone renamed since is an alias for its limb list. Clips: `actions.action_hash(act, rig.rename_canon(arm))` reads renamed bones' F-curve paths back to their
  import names, so a rename alone hashes like the import (kept, no re-encode) whether or not Blender rewrote the paths;
  `encode_actions` samples source bones under their current names and points an Action's stale paths (Blender renames
  paths only in the Actions an object uses) at the new name while sampling, then puts them back (an added bone renamed
  since Add Bone / Adopt stamped it reads back to its stamp name, `RigSkeleton.added_renames`, unless another bone or
  rename has that name now; keys naming no rig bone at all are an export warning, `actions.unknown_bone_keys`). Sampling
  puts every transform channel the Action does not key at rest first (`actions._rest_unkeyed`, the rig's pose put back
  afterwards), so a bone a clip does not key is written bind-local whatever an earlier playback or a hand pose left on
  it (B7 fix F2). Rename Bone
  (`boneops`) is Blender's rename plus those stale paths fixed (the path clash check runs with the whole map before
  the rig changes, and a failing fix rolls the bone name back); with the creature in Edit Mode it renames through the
  rig's own Edit Mode, which Blender's undo step restores. `boner.bone_rename_warnings` names what a renamed
  hard-coded name loses (ProcAnim, tail clearance, combiner lookups, seam skinning, scaling, the universal set), and
  only what was measured in ComboTest.exe as an effect (`MEASURED_LOSS`; looked-up names with no observed effect say
  so): Export and Validate warn, the Bones tab shows it. What a warning cannot stop the combine tier judges: a torso
  creature's own seam vertex whose bone name the combined skeleton lacks goes to the root bone in ComboTest.exe
  (torn seam), so combotest counts it (`_merge_seams` `root_skinned`) and the design is a COMBINE ERROR; run_all's
  `COMBINE_MUST_FLAG` ties that offline flag to the engine output (export + root-bone log + pair check failing). Scene objects do not export renames (`plan_export(renames=False)`).
- **Added bones (B7, 2026-09-15).** Engine fact first: a clip with no track for a bone plays it at its bind-LOCAL
  transform under its moving parent -- the Object Editor drew cheetah + a horn owning 8 head points during O_growl at
  8.7e-7 m from ICParse's decode, where a bind-world hold would be 29-49 mm off -- so kept clips need no track for an
  added bone and Blender's rest-under-parent preview is what the game plays. Identity: Add Bone stamps its bone
  (`bz_uid` + `bz_new_name`) and appends the uid to the creature's journaled new-bone table (`store.KEY_BONE_NEW`);
  `.bz_vert_bone` value `n_src + j` names table entry j (append-only), so the skin keeps its meaning whatever file index
  the bone gets. `structure.classify_added_bones` is the add rule over the rig bones `resolve_bones` matched to no
  source bone: a stamped bone (one holder; among Duplicate copies the one keeping its stamp name) may have children,
  and so may a bone whose stamp the table no longer holds (the table's Undo went further back than the rig's: the stamp
  still gives it an identity; it gets a key past the table and its parent's limb); an unstamped bone (Blender's Add /
  Extrude, a copy of an added bone) is taken only when it is a leaf, with a key past the table (a copy with bones under
  it is refused as a copy of that added bone, which Adopt Added Bones gives its own identity; B7 fix F5); refused: a copy of a
  source bone, a root, copies none of which keeps the stamp name (an unstamped chain is plain new bones and is taken as
  it is since C8.3; Adopt Added Bones only stamps it), a
  bone under a stub bone of the export (`structure.export_stub_bones`, B7 fix F4 -- since fit-to-rig 2026-09-16 that set
  is read off the LIVE tables in both directions: every flagged bone, minus any a live limb WITH patches lists (which
  `apply_stub_bones` clears), plus the point-less bones of ANY live limb with no patches, the source's rows or a new
  one. So giving a limb patches in Blender takes its bones out of the stub set at once -- before, only a save and a
  re-import did, which is what the native pillbug build had to do -- and for an unedited creature the answer is exactly
  `stub_bone_indices`, so the corpus still exports byte-identical).
  Limb membership is by identity, not by name (`structure.added_limb_rows`, B7 fix F1): every
  added bone is in the limb of its CURRENT parent, whatever a stored row says -- **unless a Boner limb operator pinned
  it** (`store.limb_pins` -> `added_limb_rows(pinned=)`, fit-to-rig): a pinned bone is in the row that lists it and its
  own added children follow it there. A new limb rooted at an added bone needs that, because nothing can be re-parented
  into a limb that has no bones yet; a pinned bone no row lists falls back to its parent's limb, so a pin can only say
  where a LISTED bone stays. Entries naming a new-bone table name or
  an added bone's current / stamp name (never a source file name or a created stub bone's) come off the rows; each bone
  takes back an entry of its parent's limb (position kept) or is appended; a leftover entry is a bone that moved with
  its parent (summary: `moved from`) or one no longer on the rig, which is dropped so an added-then-deleted bone exports
  the never-added bytes (summary: `limbs: dropped`). So a stamp name can't be captured by a later bone of that name,
  and a split limb can't be written. Adopt / Add Bone store the same layout by stamp name (it survives later renames). Export (`structure.insert_added_bones`, inside both builders before the owners are laid out):
  parents first, inserted before the first of the export's stub bones (after its parent when a file breaks the stub
  rule or a new stub limb's bone comes earlier; both builders pass the same `export_stub_bones` set -- limb rows, patch
  limbs, final owners -- so where a bone goes and whether it is refused never depend on whether the geometry grew, and
  Export's guard in `ops.gather_export` and Add Bone refuse a bone under one first), source parent indices shifted, bind-local = inverse(parent bind, rest edits included) @ rig rest, joint limits zero in a sibling's
  layout, flags (stub byte cleared) and colour from the parent, `skel_info` followed; a `BoneInsert` maps owner keys to
  file indices for the reskin / grown layout (corners-first ranges, aux counts re-derived) and the renames; names are
  checked with every final name; `BuiltExport.bone_keys` gives `encode_actions` the rig bone per exported bone, so an
  Action keying the new bone is sampled in file order. Remove-then-add of each base-game creature's last non-stub leaf
  is the stock record (61/61; flags / colour words are arbitrary in stock). Undo: Add Bone ends with the rig out of its
  Edit Mode, so its undo step restores the rig and the creature's tables together (an armature Edit Mode step would
  hold the bones only); with the creature in Edit Mode the tables follow the mesh generation. A table entry whose bone
  is gone (deleted, or undone in the rig's own Edit Mode) is refused when points are skinned to it (they show orange;
  the refusal names the bone's last rig name, read from its vertex group) and its limb entry is dropped. When a
  Duplicate copy took a deleted skinned bone's identity (it holds the stamp alone), its points still sit in the deleted
  bone's vertex group, which Blender deforms by name: Export and skin edits name that group and the copy
  (`ops.stale_group_notes`, F5) instead of calling it a weight edit. Read-outs
  (B7 fix F3): the panel caches key on `rig.bones_key` (names, parents and identity stamps), classify against the
  export's stub set (`store.export_stubs`, fix F5: `structure.export_stub_bones` over the stored rows, patch membership
  and owners whenever the object has any of them, cached on the generation and the limb table -- so no parse per
  redraw; Add Bone's adoption and warning and Adopt Added Bones use the same set), and
  `store.new_bone_keys` raises `AddedBoneCheckError`, which the draw-safe readers turn into no key while
  `store.added_bone_check_error` says why at the active bone, in the Bones tab and in every skin edit; the Limbs table
  shows `core.added_limb_placement`, the placement Export uses. Auto Skin (F4) takes the table's bones on the rig as
  segments and targets like source bones (`ops.auto_skin_rig` over `store.owner_count`: the parent from the rig, end
  sites by name, Deform), so Reset All re-derives their points rather than moving them to source bones; a bone not
  adopted yet has no owner key, so it is no target and the report names it. Actions: which Actions export is unchanged
  (the rig's active Action, its NLA strips, Actions stamped for it), but one keying only the rig's bones that none of
  those rules takes -- a new Action held by its fake user -- is a `plan_export` warning (`actions.unclaimed_actions`),
  not a silent gap.
- **Deleted and re-parented source bones (C8, 2026-09-15).** `rig.bone_topology` reads them off the identity resolution
  (`BoneResolution.deleted` / `reparented`; a deleted bone's children count as re-parented -- Blender hangs them from
  its parent) into a bpy-free `topology.BoneTopology` (deleted source indices; (bone, new parent key) pairs, the key a
  source index or an added bone's owner key). `topology.apply_skeleton` replaces `insert_added_bones` in both builders:
  source parents set, added bones inserted (unchanged rule), deleted records dropped with every parent index remapped,
  and -- only when a bone sits before its parent -- the least re-sort that puts parents first (Kahn's algorithm keyed
  on record position; an added bone with a source child goes right before it; stub bones last), returning a
  `SkeletonEdit` (a `BoneInsert` whose index map holds -1 for deleted keys). The rest of a re-parented bone is written
  relative to its CURRENT rig parent (`rig.bone_transforms_from_armature`), so its bind world is unchanged; a deleted
  bone's points are moved to its nearest kept ancestor before the owners are laid out (`move_deleted_owners`; a
  reskin follows when it owned any; the vertex-group check treats those points as the deleted bone's skin, not a weight
  edit); stock bone ranges are not in record order (0 / 61), so a re-sort alone needs no reskin. After the build:
  `drop_from_limbs`, `fix_ik_chains` (a deleted bone off its chains; a chain that is no longer one hierarchy path
  completed along the path from its topmost to its deepest bone, else dropped with a warning) and `retrack_clips`: a
  kept clip's track for a deleted bone is dropped and a re-parented bone's keys become `B_new @ inv(B_old) @ L_old`
  (Blender plays an Action's keys as the pose relative to the rest under the current parent; quaternion sign kept
  continuous), the clip rebuilt from its decoded keys with its bounds (byte-exact for every other track). Rules
  (`topology_refusals`, run by the skeleton guard against the tables the export writes -- `boneops.topology_rules` --
  and again by the builder): no root delete, no looked-up name (`boner.hard_coded_uses` minus the universal-set line),
  no stub bone, no host, no limb's only bone; no bone made a root, none under a stub bone or a deleted bone, no cycle,
  no limb other than the Torso split into branches (its source roots counted without the added bones). Engine facts:
  a track naming no bone is ignored (probe: LOADED + played exactly), and the re-tracked neck4 file plays as decoded
  (7e-7 m). `plan_export(deleted=...)` says a deleted bone's dropped keys once.
- **Joint limits and IK chains (C8.4-5, 2026-09-15).** The BONE record's tail (6 limit floats + the optional flag word)
  is a per-bone custom property stamped at import (`rig.KEY_BONE_LIMITS`), edited in the Bones tab and written by
  `topology.apply_bone_limits` only where it differs from the record (float32 compare: untouched creatures byte-identical);
  `boneops.rig_bone_limits` maps the props to owner keys (source stamp / added key). The IKCH table is journaled rows
  (`store.IK_TABLE_KEYS` in `TABLE_KEYS`, so edit-mode Undo follows the mesh generation; `topology.IKRow` by source
  identity), applied by `topology.apply_ik_rows` right after the skeleton edit and BEFORE `fix_ik_chains` and
  `apply_bone_renames`, so a stored chain that still names a bone deleted since (allowed: `missing_ok`) is fixed by the
  delete rule and a renamed bone's name is rewritten by the rename rule exactly as for the source's chains; a row that
  is no hierarchy path is refused unless the re-parent fix can complete it. The panel (`ikops.draw_ik_box`) shows rows
  with rig names through the identity (`rig_names_of`) and a status from `topology.chain_path_problem` on the rig's
  current hierarchy. Validate: `core.validate_model` adds `topology.ik_chain_problems` (blocking; no stock chain fails).
- **Blender-side state.** A rigid vertex-group reassignment (Ctrl+G) is adopted as a skin edit and said; a point in no
  bone group or with blended weights is refused. Object transforms, shape keys, extra modifiers, envelope / no-group
  Armature deformation, F-curve modifiers and rig constraints / drivers are refused or re-encoded as Blender plays
  them. Skin images keep a fake user and painted ones are re-packed before a .blend save, so a reopened file exports
  its paint.
- **Skin: one owner table, read against the groups.** `.bz_vert_bone` is the owner table; the rig's bone vertex
  groups are what Blender deforms. Export adopts rigid group reassignments (`core.skin_owners`). The Boner tools use the
  same rule live (`store.live_skin` → `core.skin_group_state`): the read-outs, By Bone, the selections and Pick Bone
  show the adopted owners (a point in no bone group shows unowned), and every owner write (`ops.set_owners`, Auto Skin,
  Extrude Patch) first adopts reassignments into the table in the same journaled edit and refuses
  (`core.skin_edit_refusal`) while points outside the edit hold blended weights or lost their group, because
  `store.set_vert_bone` rewrites every point's groups from the table. The groups have no change counter, so the
  panel and the By Bone overlay read them on every draw (about 2-5 ms on the largest creature). Owner -1 (no bone) is
  legal in the table; the builders refuse it only on points a patch uses.
- **Auto Skin is a measured heuristic.** Nearest bone segment (`boner.skin_segments`: head to the nearest child's
  head, a leaf is its head alone, never the rig's drawn 0.1 leaf stub), restricted by the file's own conventions
  (`boner.auto_skin_mask`: no stub bone, no `end*` bone -- none of 360 / 611 on the base game owns a point) and the
  rig's Deform flags. It never uses a selection the modder can't see (Object Mode) and a selection of every point must
  be asked for as Reset All. Its agreement with Relic's hand skin (52 %) is pinned in `tests/boner_test.py`.
- **Skinning layout.** A reskin or grown export re-sorts the pool into the stock layout (`core.skin_order`: unowned
  first, then per bone its patch corners before its other points) and re-derives every bone's `aux_count` (meta5)
  from it (`core._layout_bones`); the fast path leaves an untouched file byte-identical. Since 2026-11-15 the rule
  lives in ICParse (`ICParse.sgm.creature.layout`, shared with `build_creature`, combotest's output and
  `check_model`) and is known to be the game's: IC.exe poses only the first `aux_count` points of each bone at its
  coarsest level. The same module holds the handle rule Auto Skin follows (each handle on its grid-nearest
  corner's bone: a battle creature skins every patch with its four corner bones only).
- **Structure rows (limbs).** A limb is an identity-keyed row (`structure.LimbRow`: source record index or -1, name,
  id, bone list or "the source's"), journaled like every table (`store.limb_rows` / `set_limb_rows`; a pre-row object
  and its old journal entries are backfilled on first sync). `structure.apply_limbs` builds the export's LIMB records on
  the parsed source's private copy, keeping that copy's record for a row equal to its source (bytes unchanged) and
  making a new ICParse `Limb` otherwise; ICParse writes them. The operators keep two invariants rather than letting
  Validate find the damage: every class a connection names keeps a limb (Delete and class changes are refused
  otherwise; `boner.orphaned_connections`), and no tool makes a limb with no bones (the engine's "limbs are boneless!"
  makes ComboTest.exe refuse the creature; Add Standard Limb needs its free root bone). Rules are bpy-free in `boner.py`;
  the Object Editor's limb warnings are `ICParse.validate.check_oe_structure`; a boneless limb is combotest's precheck
  ERROR (one rule, one place).
- **Structure rows (connections).** A connection is an identity-keyed row too: `core.RingEdit` gained `src` (source
  LCON record or -1; None = its index, the older ring edits), `name`, `flags1` (Parent class), `flags2` (Child class),
  None meaning "the source record's". The store journals the classes and identities with the rings
  (`store.CONN_TABLE_KEYS`, `conn_rows` / `set_conn_rows`; `upgrade_conn_tables` backfills a pre-row object and every
  old journal entry that holds rings). `structure.apply_connections` builds the LCON records in row order on the
  source's private copy (grown path: already remapped by `o2n`), keeps the copy's record for a row equal to it and makes
  a new ICParse `LimbConnection` otherwise; `connection_changes` diffs by identity for the export summary. The rows
  are the whole table (a partial list would delete the rest), so the compatibility wrapper `export_model` fills the
  table before applying its index overrides. A class is `id & 0xFF7FFFFF` everywhere (combotest `SLOT_MASK`, ring
  slots, RingPicker, torso detection): the low 16 bits folded Mid (0x20000) and Mid 2 (0x40000) legs together, and
  combotest now has `lm2leg` / `rm2leg` slots. The re-class plan (`boner.class_change_plan`) lets a limb's only
  connection follow a class change instead of refusing, but still refuses what would orphan a connection for good (a
  class emptied, the Torso as every connection's parent, a class two connections carry). What the real ComboTest.exe
  refuses outright -- a limb whose class no connection carries (stubs included), a Left limb without its Right mirror
  -- is one rule in one place, `combotest.validate.picker_refusals`: the precheck ERROR, and Combine Preview fails on it
  (`combocore.picker_refusal_text`) rather than drawing a combination the tool never makes. The operators do not refuse
  those states (a one-sided re-class is half of a two-click edit; a connection is deleted before it is re-created) but
  warn through `boner.socket_warnings`, only for what the edit newly caused. Create for Limb's Parent class is
  `boner.default_parent` (Torso when a Torso bone can host, else Head), so the panel's menu, which runs with the
  operator's defaults, can create a head-mounted antler ring.
- **Limb bone lists.** A row's bone list is "the source's" (None) until an edit makes it differ; an edit equal to the
  source record is stored as None again, so inverse edits are byte-identical. `boner` computes an edit
  (`set_root_edit` / `add_bone_edit` / `remove_bone_edit` -> `BoneListEdit`: every list and every moved bone) before
  anything is journaled, so a refusal writes nothing; order = the source record's order, then the current list, then
  new bones in skeleton order (`merge_order`), because the file's order follows vertex ranges that can't be re-derived.
  The combiner's needs split by cost: what ComboTest.exe fails on and no intermediate step needs is refused (a
  standard limb root left in the Torso by Set Root, a torso connection's host bone leaving the Torso by any edit);
  what a skinning step may fix next (a moved bone owning a limb's points) is allowed and reported as a WARNING
  (`boner.bone_edit_warnings`, new hits only). The rules themselves are data-level functions in `ICParse.validate`
  (`limb_stray_points`, `torso_standard_roots`, `torso_hosts_outside`, `STANDARD_LIMB_ROOTS`): its STOCK lines,
  combotest's precheck and the add-on's reports call the same code (one rule, one place). The operators feed them the
  live skin (`store.live_skin`) and file bone names (a rig rename maps back by import stamp; a stamp two rig bones
  share is refused).
- **Stub sockets and created bones (B4, 2026-09-15).** Add Stub Socket (`connops`) plans in `boner.stub_plan`
  (bpy-free: the missing stub-able class, the selected Torso patch's quad, the standard root bone or a new one, the
  host) and refuses before it writes anything. When the skeleton lacks the root, it adds a leaf bone to the rig and
  stamps it (`rig.KEY_STUB_BONE` = its creation name, `KEY_STUB_PARENT` = its host's source index); the limb row and
  the connection row then go into ONE journal generation, and Blender's undo step covers the rig edit too. The skeleton
  guard (`rig.skeleton_error` / `created_stub_bones`) accepts exactly the stamped bones still named, parented and
  leaf-shaped as created, and keeps refusing everything else; the Limbs / Connections operators say a created bone
  belongs only to its stub limb (`limbops.not_a_bone_message`) instead of calling it unknown. The export appends them
  (`structure.apply_stub_bones`: after every other bone, flags word 1, no range, no clip track via
  `core.clip_track_model`) and sets the stub byte on the point-less bones of a new patchless limb -- never on a bone
  that owns points (it is not a stub bone; Validate's STOCK line speaks). Blender state the bone edit can't pass
  through -- a rig disabled in viewports, or outside the view layer -- is refused in words before any mode switch
  (`connops.rig_edit_refusal`), and every restore step (rig mode, active object, creature mode, rig selection) runs on
  its own, so a failing switch never strands the creature outside Edit Mode. One verdict per state: a stub limb
  naming a bone the skeleton lacks (a created bone deleted on the rig) LOADS in the Object Editor (harness, 2026-09-15),
  so Export writes it with a warning, Validate shows the OE's own WARN line instead of ICParse `check_model`'s
  "references unknown bone" (`core._oe_loads_unknown_limb_bone`), and the combine precheck's COMBINE ERROR stays
  (ComboTest.exe fails on it).
- **Rings.** Stored as corner loops (store v2; a non-standard ring kept verbatim with its encoding flag), written as the
  stock closed edge pairs; picked along patch sides at the slot's size, wound into the torso and started at the stock
  partners' start (`combocore.ring_fix`); judged per ring by `combocore.ring_status` (size, patch sides, winding, start,
  and placement on the torso / slot-limb boundary). Pick Ring works in the viewport's WINDOW region whichever region
  started it.
- **Ring start.** The combiner welds the donor's ring to the socket ring BY INDEX (`FUN_005c5962`: corner i to corner i,
  then the two interior points of the patch row between corner i and corner i+1), so a ring's *start corner* decides
  which donor corner meets which socket corner, and starting it one corner out drags the whole seam around the ring —
  by up to the ring's own size (the cheetah's L Front Leg socket: 320 mm). Two things the file could express, only one
  of them legal: **rotating** the corner list keeps the ring, its winding and its patch sides, and is what
  `bz.optimise_ring_start` searches (`combocore.ring_start_plan`: every rotation scored the way
  `combotest.validate.precheck` scores the weld — the mean by-index corner angle over the partners leads, and the
  precheck's own "partners over `TWIST_MARGIN_DEG`" and worst gap to the best pairing of the two rings break ties,
  because on a ring whose plane differs those gaps are dominated by the forbidden reversal and cannot order the
  rotations); **reversing** it flips the winding normal, which all
  730 base-game rings point into the torso, so a ring the partners would rather weld reversed is reported
  (`reversed_wins` / `reversed_gain`) and left alone — its *plane*, not its start, is what differs from the stock
  sockets. The partner set is the precheck's (the Quick donors that really have that limb,
  `partner_ring_dirs(donors_only=True)`), falling back to every stock ring of the slot when no stock creature donates
  that part (top wings), which the report says.

## Scene objects: import and export (`scene.py`, 2026-11-16)

**Import.** `core.load_scene` decodes the file with ICParse into Blender space: every part at its stored positions
with its stored normals, its triangles the finest detail run (ICParse `Submesh.triangles`; the file holds every detail
level of a part in one index buffer), its textures resolved through `core.scene_texture_source` (the `.sgm`'s folder
and its ancestors as a loose `Data/` tree, then the game's archives). `scene.build_scene` makes one mesh object per part
(smooth faces, the stored normals as custom split normals, a `UVMap`, a material slot per scene material with its
image, magenta colour-keyed to alpha 0), an armature at bind when the file has a skeleton that anything is bound to, a
vertex group per bone from the skin weights (a skinned character) or one group at weight 1 for a rigidly bound part (a
structure), an Armature modifier, and the clips as Actions (`actions.import_scene_clips`). The parts of a model with no
rig hang under one group empty. Every part is stamped `bz_scene`, `bz_scene_part` (its part index), `bz_source`,
`bz_name`, and `bz_rig` when there is one.

**Which objects are the model.** `scene.part_objects(obj)`: the mesh objects with the same `bz_source` and `bz_rig`,
by part number; two objects claiming one part are refused by name. A part with no object keeps the source's geometry
(reported); an object that is not a part is not exported.

**Reading a part back** (`scene.build_part`). The file stores one vertex per position + UV + normal, so a Blender
vertex is split wherever its triangle corners disagree on UV (compared at 1e-5) or sit on a flat-shaded face (which
takes the face normal); otherwise it stays one vertex with Blender's smooth normal. Output vertices follow Blender's
vertex order, each copy a split makes right after the vertex it came from (`np.lexsort` by source vertex, then by
first use), so an unedited mesh maps back onto exactly the vertices it came from.
Triangles are grouped per material slot into the file's shaders in order of first use (a slot past the file's
materials is refused); V is flipped back; a vertex no face uses is dropped and counted. A skinned part takes its
weights from the vertex groups named after the file's bones (case-insensitive); a vertex with none is bound to the root
bone and counted. Positions go back to SGM space with `core.blender_to_sgm`.

**Only edited parts are rebuilt** (`scene.export_scene_object`). `part_unchanged` compares the rebuilt part with the
source part: positions (1e-5), UVs, the triangle lists per shader, and the skin (the top four weights, normalised,
2e-3); normals are derived and not compared. An unchanged part keeps the source's bytes. A changed part goes through
ICParse `sgm.scene.model.write_scene_part`, which rewrites that part's mesh data and keeps every other byte, in the v1.2
layout by default (`legacy=True`: the engine regenerates the detail levels) or v2.0 with one full-detail record. It
rebuilds the part's bounding volumes the way the game builds them (ICParse `sgm.scene.bounds`: the part's own box and
sphere, one box per palette bone from the vertices that name it, the empty form for a bone no vertex names, exactly 1 +
palette of them; and, when the part's box changed, the entity box: the part group's union, or the top-level mesh's own),
and only chunks whose bytes change are rewritten. `build_part` and `write_scene_part` refuse a part above 12,288 vertex
entries (`MAX_PART_VERTICES`, the game's vertex-set limit, decomp spDx8 `dx8VertexSet.cpp:63`). A part with morph
targets (damage states) keeps them only while its vertex positions and order are unchanged in the v2.0 layout; an edited
one is refused (`scene.damage_states_refusal`) unless `drop_morphs` (the export option *Drop the damage states of edited
parts*, in `bz.export_scene`, `bz.export_scene_geometry` and Export Entity Folder) writes it without them, counted in
`SceneExport.damage_states_dropped`. When anything was rebuilt the result is checked with
`ICParse.sgm.scene.validate.validate_scene_sgm`: an ERROR refuses the export, the rest become notes. A part stored as a
`GEOM` block (projectiles, effects) cannot be rebuilt and is refused with that reason.

**Clips.** `bz.export_scene` adds the clips to the geometry: `actions.plan_export(rig, renames=False)` sorts the
Actions into kept, to encode and dropped, `actions.encode_scene_actions` samples the edited ones and
`ICParse.sgm.scene.model.write_scene_animations` writes them, so untouched clips keep their bytes.
`bz.export_scene_geometry` writes the geometry alone. (The **File > Export** entry for `bz.export_scene` reads
*Impossible Creatures Scene Object (.sgm)*, like the SGM tab's button *Export Scene Object (.sgm)*.)

**Known gaps** (STATUS.md, gap 15): morph data is not remapped (an edited part with damage states is refused or written
without them); material, texture, team-colour and bone edits are not exported (the rig is read only for the clips).

## New Project (`newproject.py`, 2026-11-16)

`bz.new_project` (**File > New > Impossible Creatures Project...**, and a button in the SGM tab) is an `ExportHelper`:
the file chosen becomes the project's `.sgm` and the object's source, exactly as for an import, so editing, Validate,
Actions and every export work unchanged.

* **Creature**: `ICParse.sgm.creature.scaffold.build_scaffold_creature` (the canonical 48-bone skeleton, a connected
  scaffold body, stock-layout rings, a texture, optionally the 9 standard clips and the base-game IK chains), written
  with the creature writer.
* **Gatherer / character, Structure / prop**: the engine cannot load a scene object built from nothing, so a project is
  a byte copy of a stock donor. `game_archives()` lists the install's `.sga` files (`ICParse.data.default_root`; Engine
  and ICData first); `scene_donors(kind)` lists the `.sgm` members under `art/ebps/gatherers/` and `characters/`, or
  `structures/` and `nature/` (9 and 196 on a Steam install), the first archive to hold a path winning, sorted by name and
  cached until an archive's mtime changes (the dialog asks on every redraw). `read_donor` returns the `.sgm` and its
  sibling `.ebp`; both are written beside the project under its name (a blueprint finds its model by its own basename).
  A file that turns out to be a creature is refused and both files are removed, and so is a project whose import fails.
* **Animations**: *Keep*; *None* removes the clip Actions (`drop_all_clips`: the source-clip table stays, so Export
  drops those clips from the file); *Borrow* (creatures only, `borrow_clips`): the donor creature's clips become NEW
  Actions matched by bone name, each shared bone taking the donor's pose in its own frame with the translation scaled
  by the ratio of the two bones' lengths (clamped to 0.2-5), unmatched bones at bind; the report says how many bones
  matched.
* **Stamps**: `bz_project` (CREATURE, CHARACTER or STRUCTURE) on the new object and the objects its rig drives, and
  `bz_donor` (the archive member, e.g. `art/ebps/structures/lab.sgm`) on every part of a scene project started from
  the archives, so the Blueprint panel exports into the donor's folder.

## The sceneobj package (2026-11-17)

`blender_native/sceneobj/` is where the gatherer, character, structure and prop work goes from now on, so that new
tools add files instead of editing `__init__.py`, `ui.py` or the test runner. The working rules for someone adding a
module are in [docs/sceneobj/README.md](docs/sceneobj/README.md); this is the design.

* **How the add-on loads it.** `sceneobj` is listed once in the import tuple and in `_MODULES` (just before `ui`);
  `_reload()` calls `sceneobj.reload()` right after reloading the package; `register()` calls `sceneobj.register()`
  last, and `unregister()` calls `sceneobj.unregister()` first, inside a `try` so the creature half always comes out
  too. `blender_native._CLASSES` does not list the package's classes.
* **The registry** (`sceneobj/__init__.py`). Core modules first, in `CORE` order (`model`, `subtype`, `entity`,
  `ui_kit`, `prefs`, `importer`, `exporter`, `compat`); those that do not exist yet are in `MISSING_CORE` (today
  `subtype`, `entity`, `importer`, `exporter` and `compat`). Then every module whose name starts with `slice_`,
  `ops_`, `panels_`, `props_` or `guards_`, sorted by `(ORDER, name)` (`ORDER` defaults to 50). `CLASSES` is every
  module's `CLASSES` reordered so that property groups come first (a group after the groups it points at) and a child
  panel after its `bl_parent_id` parent, because Blender 5.3 refuses the other orders. `register()` registers them and
  then runs each module's own `register()` hook; `unregister()` undoes exactly that, in reverse; a failure rolls
  everything back, except that a sub-panel whose parent is missing is left out and recorded. A module that fails to
  import is recorded in `ERRORS` (shown on the preferences page) and the rest still load. `reload()` reloads every
  module of the package after the siblings it imports, and discovers again.
* **Pure mode.** Without `bpy` (the venv tests) the package still imports and loads only the modules that declare
  `PURE = True`; `CLASSES` is then empty and `register()` refuses.
* **`model.py`** (bpy-free): `Issue` / `issue()` return an ICParse `Finding` (`ICParse.entity.findings`), the one
  problem record; `SourceBundle` (an entity's files, the scene read with `ICParse.read_scene`), `OutBundle` (bytes
  out; `shared` accepts only the keys in `SHARED_KEYS`; archive paths lower case) and `ExportReport`; the `Slice`
  protocol with the export stages (`STAGE_ORDER`: materials 10, textures 12, skeleton 20, geometry 30, morphs 40,
  markers 50, bounds 60, anim 70, the controller 80-82, files 90, validate 100); `refs.rename`; `claim_edit` and
  `GUARD_CODES`; `foreach_get` / `foreach_set` with explicit dtypes; `pick_game_dir`.
* **`ui_kit.py`**: `alert`, `issue_rows`, the one list class `BZ_UL_rows` with per-list row drawers, the `SubPanel`
  base for the sub-panels of an Entity panel still to come, and `draw_scene_pointer`: what the SGM panel shows under a
  scene object (its info box and export buttons today; one pointer line once a panel `BZ_PT_entity` exists).
* **`prefs.py`**: `BZ_AP_prefs`, the add-on's preferences (Game folder, Test game folder, Build folder, Mods folder,
  Game build, and the switches Damage states, Texture type, Replace game files and Selection box), `get()` falling back
  to `DEFAULTS` when the add-on has no preferences entry (a background Blender), and `resolve_game_dir`,
  `resolve_test_game_dir`, `resolve_build_dir`, `resolve_mods_dir`. Nothing outside `prefs.py` reads them yet.
* **Not there yet**: the core modules in `MISSING_CORE`, any registered slice (`model.slices()` is empty, so the
  export stages are a contract for later work) and the Entity panel. The first discovered module is the Blueprint panel.

## The Blueprint panel (`sceneobj/panels_blueprint.py`, 2026-11-17)

Found by the registry through its `panels_` prefix (`ORDER = 80`, the controller stage's number; it has no `SLICE`,
because it writes through its own operator). `CLASSES` holds three property groups (`BZ_PG_blueprint_sound`,
`BZ_PG_blueprint_stat`, `BZ_PG_blueprint`), five operators (`bz.blueprint_load`, `bz.blueprint_reset`,
`bz.blueprint_sound_add`, `bz.blueprint_sound_remove`, `bz.export_entity_folder`) and two panels (`BZ_PT_blueprint` in
the SGM tab and its closed child `BZ_PT_blueprint_stats`); its `register()` hook points `Object.bz_blueprint` at the
property group, and registers whichever of its classes no registry did, so the module also works on its own (its test
loads it that way).

* **One holder per entity.** `entity_of(obj)` finds the parts (the same `bz_source` and `bz_rig` stamps as
  `scene.part_objects`, a double import tolerated) and the object that holds the values: the rig, else the group empty
  all parts hang under, else the first part. Every part, the rig and the empty show and export the same values.
* **Loading.** `blueprint_path`: the `.ebp` the model was imported from (`bz_ebp`), the source itself when it is an
  `.ebp`, else the same-named file beside the `.sgm`. `ICParse.ebp.scene.SceneBlueprint.read` parses it and
  `load_blueprint` sets the state (`classify` tells `FILE`, `LEGACY` and `EMBEDDED` apart): `FILE` (editable), `NEW`
  (no file; `new_scene_ebp` writes one at export), `LEGACY` (the old file layout: read-only, copied unchanged),
  `EMBEDDED` (the model inside the blueprint: read-only, export refused),
  `PROBLEM` (unreadable, or a value the panel cannot hold: Blender clamps numbers and cuts text silently, so `fill_pg`
  compares what the panel holds with what the file held) and `NO_LIBRARY` (`ICParse.ebp.scene` missing). The file's
  path and SHA-1 and the loaded values (`baseline`, as JSON) are kept; Load refuses while the panel differs from the
  baseline, Reset reloads.
* **The folder in the game** (`determine_folder`), the first that applies: the `bz_donor` stamp (STAMP); a
  `Data/art/ebps` tree around the files (PATH); a stock donor the `.sgm` is still a byte copy of, compared by size and
  then SHA-1 against `newproject.scene_donors` (IDENTITY); a guess from the kind (GUESS). The panel says which.
* **What a class means**: `class_info` reads `ICParse.ebp.controllers` (`CONTROLLER_TYPES`, `is_registered`) into a
  plain label (`5 - Structure: a building`) and whether the controller has a building site (`Site` among its
  extensions), the only kind that reads the footprint; `is_hard_bound(folder, name)` (the 26 blueprints the game looks
  up by path) gives the "replaces the original" wording.
* **Gameplay numbers**: `refresh_stats` asks `ICParse.ebp.stats.resolve_stats_ref` which stats file the game would use
  for `data:art/ebps/<folder>/<name>.ebp` and the class (the thing's own `.lua`, else the class default), reads it with
  `read_stats` through `core.scene_texture_source`, and lists the numbers and the chain of files they come from.
* **Writing the blueprint** (`blueprint_bytes`). The file is read again and refused if its SHA-1 changed since
  loading; `changed_fields` compares the panel with the file (offsets as 32-bit floats); with no difference the file's
  own bytes are returned. Otherwise only `set_header(name=, defstats_class=)`, `set_build(footprint=, offset=)` and
  `set_sounds()` run, for the fields that differ, then `to_bytes()`, and the result is read back and refused if a field
  differs. Only a problem in an edited field (`problems_of(values, fields)`: a name over 255 letters, a sound path
  empty, over 259 letters or not Latin-1, a footprint outside 0-255, a class outside 0-65535) holds the export up.
* **Writing the model** (`scene_bytes`): exactly what `bz.export_scene` writes when there is a rig, and
  `bz.export_scene_geometry` when there is none.
* **Export Entity Folder** (`export_entity`): `check_target` refuses no folder, a file, the game install
  (`$IC_INSTALL`, `ICParse.data.default_root`) and any path with a `steamapps` component; names are lower-cased and
  cleaned (`clean_name` / `clean_folder` keep letters, digits, `_`, space, `'` and `-`, so stock names such as
  `dr_ganglion's_lab` and `coal bin` work, and `..` cannot climb out); everything is computed before the first write,
  and each file is written through a temporary file and a rename (`write_file`):
  `<folder>/Data/art/ebps/<game folder>/<name>.sgm` and `.ebp`, and `<folder>/README.txt` (`readme_text`: how to try
  them in a copy of the game the user owns).

## How exports stay byte-identical

The rule everywhere: compare what Blender holds with what the file holds, and rewrite only what differs. ICParse
re-emits every clean chunk's own bytes, so a rewrite touches only the chunks that changed.

| Kind | Unchanged means | What is rewritten | Held by |
|---|---|---|---|
| Creature | every control point float32-equal to its source bytes; every table row equal to its source record | the fast path writes only the differing records; the grown path rebuilds geometry and keeps an untouched point's source bytes by identity (`.bz_src`); clips are kept by hash, edited ones re-encoded | `headless_identity` (118 stock creatures); every in-Blender test before its edit and after its Undo |
| Scene object | `scene.part_unchanged`: positions, UVs, triangles per shader and skin equal within tolerance | the changed parts (`write_scene_part`), and the clips through `actions.plan_export` | `tools/so_sweep.py --identity` (203 of 205 donors; the other 2 hold no mesh), `scene_retopo_test` |
| Blueprint | `changed_fields` finds no difference | the header, footprint and sound chunks of the edited fields (`SceneBlueprint`) | `blueprint_poc_test` (393 of 479 stock blueprints; the other 86 carry their model inside and are refused) |

Two rules keep the comparison honest: floats are compared in the file's own precision (float32 in both `.sgm`
formats, and Blender meshes are float32 too), and since Blender 5.3 flushes denormal floats inside Python, unedited
data is copied as bytes, never re-encoded from Blender's values.

## Reuse / dependencies

The **repo** ICParse is the single source of truth for every format (never vendored). `core.py` is the bridge for
the creature format (load, tessellate, export model, validate) and decodes scene files for `scene.py`; other modules
call ICParse directly only for what they own. The combiner is `pyRDN/combotest`, likewise imported, never copied:
only `combocore.py` (bpy-free) and `combopreview.py` / `edittools.py` (through combocore) touch it, and every
combine-validation rule lives in `combotest.validate`, not in the add-on. Blender's bundled Python has `numpy`;
`construct` is bundled in `_deps/`; `lupa` (compiled) is fetched into the git-ignored `_deps_bin/` by
`tools/fetch_blender_deps.py`. `__init__._setup_paths` is import-first (README, *For developers*; `$BZ_ICPARSE`,
`$BZ_COMBOTEST`, `$BZ_DEPS_BIN`).

Which ICParse modules the add-on uses (2026-11-17):

| ICParse module | What for | Imported by |
|---|---|---|
| `sgm.parser` | reading and writing both kinds of `.sgm` | `core`, `ops`, `newcreature`, `newproject`, `combopreview` |
| `sgm.creature.model` | creature records: bones, limbs, connections and their ring encodings, patches, textures, IK chains | `core`, `structure`, `topology`, `store`, `combocore` |
| `sgm.creature.scaffold`, `sgm.creature.layout` | the New Creature / New Project body; the corners-first skin layout and the handle rule | `newcreature`, `newproject`; `core`, `ops`, `fitops` |
| `sgm.scene.model`, `sgm.scene.validate` | writing scene parts and clips (`write_scene_part`, `write_scene_animations`); the scene validator | `scene`, `ops`, `rig`, `sceneobj/panels_blueprint` |
| `iff.document` | the chunk tree | `core` |
| `codecs.reconstruct`, `codecs.anim`, `codecs.pose`, `codecs.bezier` | bind and animated poses, clip tracks, scene rigs, the Bézier evaluator | `core`, `rig`, `actions`, `build`, `structure`, `topology`, `fastsurf`, `fitops`, `newproject`, `combocore` |
| `codecs.texture` | the texture codec (TXMP, palette-preserving edits) | `core`, `material`, `combocore` |
| `codecs.fit`, `codecs.acc` | the one fitting solver; quad cage to patches | `sculptops`, `retargetops`, `landmarkops`, `trimops`; `core`, `fitops` |
| `validate` | `check_model`, display vertices and combine budgets, the stock rulebook, the Object Editor's own checks | `core`, `boner`, `limbops`, `topoops`, `fitops`, `newcreature`, `combocore` |
| `ebp` | a blueprint's model (`resolve_sgm`, `sibling_sgm`); and, loaded when needed, `ebp.scene` (`SceneBlueprint`, `new_scene_ebp`), `ebp.stats` (`resolve_stats_ref`, `read_stats`) and `ebp.controllers` (the class table, `is_hard_bound`) | `core`; `sceneobj/panels_blueprint` |
| `entity.findings` | `Finding`, the one problem record | `sceneobj/model` |
| `read_scene` | the scene model of a `SourceBundle` | `sceneobj/model` |
| `lua.attributes`, `lua.limbscale` | the combined creature's `.lua` / `.lsc` | `combocore` |
| `sga`, `data` | the game's archives; where the game is (`default_root`) | `newproject`; `core`, `newproject`, `sceneobj/model`, `sceneobj/panels_blueprint` |

**Distribution (D10, 2026-09-16).** `tools/build_extension.py` turns that same tree into one Blender extension
`.zip` (8.2 MB then, 8.4 MB for 0.3.1 and 0.4.0) in which those four are **wheels** — ICParse and combotest built from this
repo with `uv build`, construct and the `lupa` platform wheels from PyPI (sha256-checked, cached in the git-ignored
`dist/_wheels`) — and the generated `blender_manifest.toml` lists them. While the extension is enabled, Blender
installs the wheels whose tags match the machine into the one `site-packages` it keeps for every extension of the
profile (`extensions/.local/lib/python3.X/site-packages`; corrected 2026-11-18, this said each extension had its
own), so **one zip serves every platform** and `_setup_paths`, being import-first, then adds nothing at all
(`PATHS_STATUS` all `importable`). What has no wheel degrades instead of
breaking: `lupa` cp313 exists for Linux x86_64 / arm64, macOS arm64 and Windows x64 and not for Intel macOS or ARM
Windows, where the add-on runs without the combiner's Lua ratings and the status box says so. The root
`blender_manifest.toml` stays as the development manifest: the id, the version (0.4.0) and the minimum Blender of
the working copy, with no wheels, so the add-on run from the repository takes its dependencies from the repository.
(Blender's Install from Disk takes only a `.zip` or a `.py`, so a working copy is tried through `dev_launch.py` or a
built zip.)
`tests/extension_install_test.py` is the proof, not the build log: it installs the zip into a throw-away
`BLENDER_USER_RESOURCES` and uses it from a Blender with no `$BZ_*`, no `PYTHONPATH` and no repo path on `sys.path`.

**Upgrades (2026-11-17/18).** Blender's wheel manager (`apply_action` in Blender 5.3's
`_bpy_internal/extensions/wheel_manager.py`) names a wheel by the `.dist-info` folder inside it and keeps an installed
one whose name is still wanted, so while ICParse and combotest kept their versions (0.1.0, 0.0.1) every build kept the
older library on an upgrade. `build_extension.relabel_wheel` gives both wheels a local version label made from their
contents (`icparse-0.1.0+h77a9ed3bb7e8` in 0.3.1), so a changed library has a new name and is replaced, and an
unchanged one keeps its name; `extension_install_test` upgrades an older build in place and checks the loaded files.
When Blender disables the old add-on for the install it clears only the `bl_ext.<repository>.sgm_native_patches`
modules from `sys.modules` (`_preferences_ensure_disabled` in Blender 5.3's `bl_pkg/bl_extension_ops.py`), so the old
ICParse and combotest stayed loaded in that session (STATUS.md gap 20, closed 2026-11-18 in 0.4.0).
`__init__._unload_stale_libraries()` runs before `_setup_paths()` and before anything is imported: it removes ICParse
and combotest, with their submodules, from `sys.modules` when the copy in memory came from another folder or another
version than the one a fresh import would load now (the version read from the `.dist-info` folder names beside the
package, which carry the content labels); a copy with no record (loaded by 0.3.1 or older) counts as another version,
combotest goes whenever ICParse does (it imports it), the repository's own copy (no `.dist-info`) is always kept, and
`construct` and `lupa` are left alone. `_record_loaded_libraries()` then stores on the ICParse package which version
each library came from, and `UNLOADED` lists what was removed and why. `extension_install_test` step 6 installs this
zip, a newer build whose add-on needs a new library name while it loads, and that build again, all in one Blender with
`package_install_files`.

## Map import (`sceneobj/ops_map_import.py`, 2026-11-18)

**File > Import > Impossible Creatures Map (.sgb)** (`bz.import_map`) and **Impossible Creatures Map from the Game...**
(`bz.import_game_map`, the game's maps from `ICParse.sgb.view.game_maps`: the loose `Data/scenarios` folder, then every
archive, a map listed once per archive that carries it) build a view of a map, which `ops_map_export.py` reads back (see Map export).
`ICParse.sgb.view` holds every meaning (it is bpy-free and says where each was read from): the terrain block read field
by field under its own TERR version, the vertex and face arrays, the ground texture of every cell (one id per 8 x 8
block) and how often it repeats, the water mesh over the cells where the water plane is above the ground, the placed
entities with their matrices (the stored rows applied to a row vector) and owners, which model each blueprint shows or
why none, and the archive search order taken from the install's `.module` files. The operator turns that into one
collection per map with a collection per kind (terrain, water, structures, nature, units, other): the terrain mesh in
the add-on's axes (`core.sgm_to_blender`, `rig.to_blender_matrix` for the placements), one material per ground texture
with its image packed, the water mesh, and per placed entity an instance of its model's collection (each model loaded
once by `core.load_scene(..., texture_source=<the map's ICData>)` and `scene.build_scene` into a hidden collection) or an
empty with the reason in `ic_not_shown`. The map's source, and each entity's id, blueprint, owner, sound id and groups,
are kept as custom properties, which the export reads; the report goes to the status bar, the console, a text
`Map import <name>` and JSON on the map's collection. Creatures are empties for now. `tests/so_map_import_test.py`
imports four stock maps of three sizes. `load_model_collection` builds one model into a collection; the import and the
export's Add object both use it.

## Map export (`sceneobj/ops_map_export.py`, `panels_map.py`, 2026-11-18)

**File > Export > Impossible Creatures Map (.sgb)** (`bz.export_map`) and the **Map** panel (3D View sidebar, SGM tab,
`panels_map.py`, `ORDER = 62`; `ops_map_export.py` is `ORDER = 61`) read the changes made to an imported map and hand them
to `ICParse.sgb.edit.apply_edits`, which does the byte work (a record is rewritten only when its edit needs it: the
library's own docstring and `ICParse/tests/test_sgb_edit.py` say which). The add-on's part is reading the scene:

* **Every edit is read against the map's source file**, which is read again at save time (`read_source`: the file on disk,
  or the archive member found through `prefs.resolve_game_dir`; kept per map until its size and time or the game folder
  change). A save never depends on an earlier one, and nothing is compared with a stored copy of the numbers.
* **`build_plan` makes a `Plan`** (heights, water level, per-block ground textures, the complete entity list, notes) without
  writing anything; `Plan.kwargs()` is exactly what `apply_edits` is called with, and `Plan.lines()` is what the user is told
  (also *Check my changes*). A value the scene did not change is not in the plan, so `apply_edits` is not given it: a map
  saved with no edits comes back byte-identical (`new_bytes == raw`).
* **Terrain** (`_read_terrain`): Edit Mode is flushed (`update_from_editmode`) and stays open; Sculpt and the paint modes are
  left (`_ensure_object_mode`). The grid must be the one the map was opened with: the vertex count, the face count and each
  face joining the same four points, else `MapSaveError`. Each vertex's world height is compared with the map's own height
  with a tolerance of `_tol_height` (2 % of a step, between 0.1 mm and 5 mm), and only points that moved are replaced, so
  an untouched point keeps the map's exact value. Sideways movement is noted and dropped.
* **Ground textures** (`_read_blocks`): the material of each face is counted per 8 x 8 block, the majority wins, a tie keeps
  the block's own (the lower-case texture file name is the key, so `Add ground texture` gives a new name the library adds
  to the map's list in sorted order).
* **Water** (`_read_water`): the water object's vertex heights give the move against the map's water surface; the level
  passed to `apply_edits` is in metres (`water_units="world"`), and the water mesh is not rebuilt.
* **Placed objects** (`_read_entities`): each object carries its entity id (`ic_entity_id`). An object whose id the map
  does not hold, or a second object with an id, is new. A copy carries the original's id; which one is the original is
  decided by position: the object nearest the id's own stored position (then the one with the original name) stays, the
  others are new rows. Ids with no object are removed. An object that did not move is given the map's own 4 x 4 matrix
  (`_same_place`), not the matrix read back from Blender, so float noise never rewrites a record. Scale is split off
  (`_rigid`) and reported, never written. A copy of a creature combined inside the map has no blueprint to name and is left
  out with a note. `Add object` makes an object marked `ic_map_new_object` with the blueprint spelled the way the map's own
  list spells it (`map_blueprint_spelling`), and loads its model into the map's hidden collection through
  `load_model_collection`.
* **Writing** (`save_map`): the target folder passes `panels_blueprint.check_target` (the game's own folder and any
  `steamapps` path are refused) and the write is `panels_blueprint.write_file`; the original file is refused unless
  `replace_original`. The files beside the map (`sibling_files`: those in the same folder, or in its `loading/` folder, that
  start with the map's name and are a known kind) are copied unchanged, renamed to the new name. "Ready-to-drop" writes
  `Data/scenarios/<mp|sp>/<name>.sgb` and `README.txt` with the plan's lines. The Pathfinding choice is passed as `paths`
  (`auto`, `always`, `never`); when the library says it touched the pathfinding, `pathfinding_fidelity` is run on the
  original and its summary goes into the report, because a rebuild is an estimate. The report (`report_lines`) is two parts,
  *Changes read from the scene* and *Written into the map*, shown in the status bar, printed, and kept in the text
  `Map save <name>`.
* **Not covered:** the water surface mesh is not rebuilt after a terrain edit; a map with no water cannot gain any; the
  library does not update the `.trg` (the report lists the removed ids). `tests/so_map_edit_test.py` is the test.

## Extension points for map features (`sceneobj/map_hooks.py`, 2026-11-18)

A map feature (triggers, regions, paths, a new map ...) lives in a module of its own and plugs into the map import and save
through `sceneobj/map_hooks.py`, so no feature edits `ops_map_import.py`, `ops_map_export.py` or `panels_map.py`. The module
holds no operators and no panels. With nothing registered the import and the save behave exactly as before (a map saved with no
edits is byte for byte the original; `tests/so_map_hooks_test.py` checks it).

**Loading.** Every module in `sceneobj/` whose file name starts with `map_` is discovered and imported when the add-on starts (`map_`
joined `slice_`, `ops_`, `panels_`, `props_`, `guards_` in `sceneobj.PREFIXES`). A feature file `map_triggers.py` registers its steps
at the top level of the module; operators and panels go in an `ops_*.py` / `panels_*.py` file (found the same way), which may import
the `map_` module. `map_hooks` has `ORDER = 10`, so it is (re)loaded before every feature module (default ORDER 50). Registering a
name twice replaces the first, so a reload never runs a step twice.

```python
from . import map_hooks as H

H.register_import_step(name: str, fn, order: int = 100)   # fn(ictx: ImportContext) -> None
H.register_save_step(name: str, fn, order: int = 100)     # fn(sctx: SaveContext) -> None
H.register_check(name: str, fn)                           # fn(cctx: CheckContext) -> list of H.problem(...)
H.run_checks(context, top) -> list[dict]                  # {"severity", "text", "where", "check"}, errors first
H.problem(severity: "error"|"warning", text: str, where: str | None = None) -> dict
H.MAP_PANEL_ID == "BZ_PT_map"                             # a sub-panel sets bl_parent_id = H.MAP_PANEL_ID
H.import_map_bytes(context, data: bytes, name: str, options: dict | None = None) -> map collection
H.unregister_step(name); H.registered(); H.snapshot() / H.restore(state)   # for tests
```

Lower `order` runs first, ties in registration order.

**Import step** `fn(ictx)` runs at the end of `import_map`, after the terrain, water, models, entities, creatures and game look. A step
that raises is skipped and reported as a note in the import report. `ictx` fields: `context`; `top` (the map's collection);
`name`; `source_bytes` (the map file as read); `view` (the `ICParse.sgb.view.MapView` the import used) and `board` (`view.board`, the
editable `Sgb`); `options` (`load_models`, `show_water`, `show_creatures`, `game_look`); `terrain` (the terrain object);
`kinds` (`{"terrain", "water", "structures", "nature", "units", "other": collection}`); `game_dir`; `data` (the game's file resolver,
or None); `report` (the import report dict: a step may add its own key); `tmp` (a temp folder that lives for the import);
`ictx.warn(text)` adds a note line to the report.

**Save step** `fn(sctx)` runs in `save_map` after `apply_edits` and before anything is written. `sctx` fields: `context`; `top`;
`name` (file name stem); `group` (`"mp"` or `"sp"`); `folder` (where the map file goes); `target` (the map file's path);
`ready_to_drop`; `source` (where the map was opened from, "" for a map made in memory); `original` (the map file's bytes as
opened, never changed) and `view` (its `MapView`); `sgb` (the bytes the save will write as the map file: the original with the core
edits; a step may replace it, later steps see the replacement; `sctx.sgb_changed` says whether it differs from `original`);
`report` (`report["changes"]` and `report["warnings"]` are lists of plain-words lines a step appends to, or `add_change(line)` /
`add_warning(line)`; they appear in the save report and the README of a ready-to-drop folder); `files` (`{file name: bytes}` written
beside the map, names relative to `folder`, `loading/<name>.<ext>` for the loading folder; it is pre-filled with what the save
copies today, renamed to `name`: `<name>.trg`, `.dlg`, `.na`, pictures ...; replace `files[name + ".trg"]`, add a new key, or set
a value to `None` to drop a file; a name that is absolute, has `..` or equals the map file is refused); `initial_files` (a copy
of the pre-filled dict); `refuse(reason)` (stops the save with nothing written; the reason is shown as it is). A step that raises
also refuses the save, naming the step. A step whose feature was not touched must change nothing: no `sgb` replacement, no
file entry changed.

**Check** `fn(cctx)` returns a list of `H.problem(...)` dicts (`[]` when all is well); `cctx` has `context`, `top`, `name`,
`terrain`. A check that raises becomes a warning. `run_checks` is the source for the user-facing error list.

**A map from memory.** `import_map_bytes` builds the same collections as File > Import from bytes (for "New map"). The map has no file:
its bytes are kept in the .blend as a text `Map source <collection name>` (named by the collection's `ic_map_source_text`
property, `ic_map_source` is ""), and the save reads the original from there, so saving works as for any map; nothing sits beside
it unless a save step adds it (a new map's `.trg` is one `files` entry). `import_map` itself gained `source_bytes=` and
`in_memory=`; the operators pass the bytes they read.

## Module map

Every top-level add-on module is in this table and in `__init__._MODULES` (the reload order; a module missing from
that tuple makes an in-place reinstall run stale code). The `sceneobj` package is listed there once and reloads its own
modules (`sceneobj.reload()`), so a new module in it needs no line anywhere. The table groups by concern rather than by
reload order.

| file | role | bpy? |
|---|---|---|
| `structure.py` | structure rows: `LimbRow`, `source_rows`, `check_rows`, `apply_limbs` / `apply_structure` (the export's LIMB records from the rows on the source's private copy), `apply_connections` / `connection_changes` (the LCON records from `core.RingEdit` rows), `NewBone` / `apply_stub_bones` (created stub bones appended last, the stub byte), bone identity and renames (`RigBone`, `resolve_bones`, `name_conflicts`, `bone_name_problems`, `respell` / `BoneRenamer`, `apply_bone_renames`), added bones (`AddedBone`, `stub_bone_indices`, `export_stub_bones`, `insert_added_bones` / `BoneInsert`, `added_limb_rows` / `AddedLimbs`, `RigAdded` / `classify_added_bones`, `added_bone_stock_lines`), `limb_changes` for the export summary | no |
| `topology.py` | bone topology edits of the SOURCE skeleton (C8), bpy-free: `BoneTopology` (deleted and re-parented source bones) with `topology_refusals`; `apply_skeleton` (re-parent, insert the added bones, drop the deleted records and — only when a bone now sits before its parent — re-sort with the least movement that keeps parents before children; a bone's vertex range is not tied to its record position, 0 of 61 stock creatures, so no reskin follows a re-sort), `drop_from_limbs`; `retrack_clips` (a deleted bone's track dropped, a re-parented bone's track re-expressed, v7 and v1 layouts); `fix_ik_chains` / `ik_chain_problems`; `apply_bone_limits`; the `IKCH` rows (`IKRow`, `source_ik_rows`, `apply_ik_rows`) | no |
| `core.py` | ICParse bridge: load (cached source models), tessellate, engine normals, the export builder (`EditState` → `build_export`: fast byte-exact path and grown path, `apply_clip_plan`, `export_summary`; `export_model` / `export_grown_model` are thin wrappers), the pure guards (`pool_error`, `skeleton_changes` with bone identity stamps, `skin_group_state` / `skin_owners` / `skin_edit_refusal` for vertex-group skinning, `pick_extrude_side`), the skinning layout (`skin_order`, `aux_counts`, `aux_rule_problems` -- thin names over `ICParse.sgm.creature.layout` -- and `unowned_patch_points`), `host_bone_problems`, the ring rules (`ring_slot`, `corner_sides`, `RingPicker`: guided picking, `placement_problem`, the Pick Ring modal's event rule), `validate_model`, `blocking`; axis xform | no |
| `boner.py` | Boner rules: Pick Bone's point-vs-segment rule and modal event rule, Auto Skin (`skin_segments`, `auto_skin_mask`: stub and end-site bones, `auto_skin`, targets and `auto_target_refusal`), `skin_stats`; the Limbs rules (the classification checkbox table and `toggle_flag`, `class_label`, `STANDARD_LIMBS` and `standard_root_refusal`, `default_limb_name`, `limb_name_error`, `orphaned_connections` / `delete_limb_refusal` / `class_change_refusal`, `default_patch_target`, `limb_status`); limb bone lists (`subtree_bones`, `merge_order`, `torso_remainder`, `set_root_edit` / `add_bone_edit` / `remove_bone_edit`, the standard-root and host-bone refusals, `bone_edit_warnings`) ; the Connections rules (`CONN_FLAGS` / `toggle_conn_flag`, `ring_corner_count`, `default_host` / `stock_host` / `host_refusal`, `default_conn_name`, `limbs_without_connection`, `class_change_plan`, `conn_status`, `default_parent`, `unmirrored_left` / `socket_warnings`); stub sockets (`STUB_SLOTS`, `stub_classes_missing`, `stub_quad`, `patch_centre`, `stub_patch_refusal`, `stub_plan` -> `StubPlan`, `stub_bone_world` / `stub_tail_length`); bone names (`HARD_CODED_USES`, `hard_coded_uses`, `bone_rename_warnings`, `bone_name_error`) | no |
| `fastsurf.py` | vectorised dirty-patch tessellator (`TopoCache`: engine cell split, welded normals) | no |
| `patchrules.py` | the Max Editable Patch rules on the pool: roles (corner / handle / interior), `auto_interiors` / `interior_error` (the measured Auto Interior rule), `handles_of` / `corner_handle_pairs` / `handles_of_selected`, `carry_handles` (corners drag handles, Coplanar / Lock Handles turn siblings, interiors recomputed, manual detection, carry + home anchors), `rotation_between` / `plane_normal` / `project_coplanar` / `coplanar_error`, `reset_interiors`, `follow_corners` (hidden interiors in a selection), `compaction` (the pool-compaction rule Delete Patch and the topology plans share) | no |
| `patchsel.py` | patch-level selection (B6): the corner quads (`face_rows` / `face_quads` / `add_face`, the pool guard's `face_error`), the selected patches (`selected_rows` / `select_rows`), the per-patch hidden mask (`hidden_rows`), `delete_plan` / `ring_patch_refusals` (pure) and `BZ_OT_delete_patch`, `BZ_OT_hide_patches` / `BZ_OT_unhide_patches`, `BZ_OT_set_vertex_type` (Corner / Coplanar, `coplanar_flags`), the X / Delete / H / Alt+H keymap (`register_keymap`) | yes |
| `topoops.py` | topology tools (B7) as pure plans (`TopoPlan` / `finalize`: the new pool, tables and refusal) plus one applier (`apply_plan`): `weld_plan`, `attach_plan`, `mirror_plan` (`mirror_name` / `mirror_owner_map` / `mirror_limb_map`), `subdivide_plan` (`propagate_splits`, `_split_curve`), `extrude_sides_plan` (Add Quad), `fill_plan` (`hole_loops`), `extrude_region_plan`; the side rules (`side_key` / `side_table` / `open_sides` / `traversal`), the operators and the drag macros | yes |
| `fitops.py` | Fit Patches to Mesh (A1): any mesh + a patch budget → a quad layout (a QuadriFlow remesh at about the budget, or the mesh's own quads) → `ICParse.codecs.acc` → `ICParse.codecs.fit.fit_points` → `core.patches_to_creature`, then the normal import path. The **import contract** (`Topology` / `target_topology` / `parts_reason`: a target is judged on a positionally welded copy of itself, never on its index buffer, so UV/normal splits are not damage and boundary edges are not non-manifold), `symmetry` / `is_x_symmetric` / `symmetrize_pairs`, `budget_advice`, `layout_score`, `FitStats` + `heat_colors` (the optional error heat map), `fit_cage`; and `rig_split` / `RigSplit` (fit-to-rig: the fitted net split across the TEMPLATE's own limbs and bones, so a bone whose limb has no geometry is not mistaken for a stub bone) | yes |
| `sculptops.py` | Sculpt Surface / Refit Patches (A2): bake the exact surface at density n into a sculptable mesh whose coincident seam samples are MERGED (`bake_sculpt_mesh` / `merge_samples`, so brushes see one skin and cannot tear a seam), stamped with what it is a sculpt of plus every vertex's bake position (`bz_rest`, float32, so an untouched vertex compares exactly equal); `moved_samples` → `ICParse.codecs.fit.refit_samples` (the one solver; patches with no moved sample and no point shared with a moved patch are pinned) → `write_pool` through the journaled edit, so untouched patches stay byte-identical and Undo restores | yes |
| `retargetops.py` | Retarget Creature to Mesh (A3), bpy-free: `retarget_pool` moves a creature's own control points onto a target mesh with `fit.fit_points` (its patch layout, limbs, rings, skeleton, skin and clips all left alone — only the pool is written, journaled). `ring_points` pinned by default (each socket keeps the ring geometry the combiner welds at), `align_target` (the target rigidly aligned into the creature's frame), `symmetrize_displacement`, `rotation_angle`, and `bone_moves` / `move_rig_bones` (the rest bones follow the new skin) | no |
| `landmarkops.py` | Landmark-guided retarget (A3b), bpy-free: named `LM_*` Empties on the target matched to the donor's limb tips / roots / head / tail / torso (`donor_landmarks`, `limb_chain`, `strip_name`, `resolve_landmarks`) drive a thin-plate-spline pre-warp (`TPS` / `tps_fit`, `landmarks_coplanar`) of the control net **and** the rest bones before A3's closest-point fit — which alone cannot carry a limb across the body, because it only sees displacement along the surface normal (the pillbug scan); `coverage_by_limb` / `coverage_text` report what the landmarks reach | no |
| `texops.py` | Transfer Texture (A4): give a creature a per-patch **atlas** UV layout (`atlas_grid` / `atlas_uvs`: one cell of an N×N grid per patch with a texel gutter — the engine interpolates the 4 corner UVs bilinearly over a patch, `core.surface_uvs`, so a cell per patch maps the patch exactly and no UV island has to cross a seam) and bake a source mesh's image onto it with Cycles EMIT selected-to-active (`bake_target_mesh`, `_emission_material`, `cycles_bake`, `align_matrix`, `dilate`, `coverage`); the creature's tables are written (journaled) only after the bake succeeded | yes |
| `trimops.py` | Trim Stray Surface (A5): what of a creature's surface the TARGET does not have -- `surface_samples` / `nearest_target` (`fit.evaluate` on `fit.sample_grid` and `fit._nearest`, the fit's own helpers) -> `stray_patches` (`Stray`: the per-patch far fraction and the stray rows) and `limb_counts` / `limb_text`; `collapse_pool` (`Collapse`: the stray patches' own points onto the target, shared and ring points pinned, A3's mirroring) -- all pure; `BZ_OT_trim_stray` (Select / Delete + Fill Hole / Collapse, `default_target` from the recorded fit / retarget source) and `validate_lines` (the non-blocking `WARN trim:` read-out Validate appends) | yes |
| `guards.py` | action-time refusals (D10): `state_key` (the cheap digest the checks depend on) / `gen_probe` / `bones_key`, `issues` (the export's own refusals as `Issue(code, level, text)`) and `issues_cached`, the `depsgraph_update_post` handler (`check`, `_candidates`), `text_lines` for the viewport overlay and `draw_problems` for the panel's Problems box | yes |
| `gnsurface.py` | D9, optional: the bicubic surface as REAL evaluated geometry -- the per-patch FACE tables (`write_tables` / `clear_tables` / `missing_patches`, `ATTR_P` / `ATTR_UV` / `ATTR_ROW`), the node group built by script (`node_group` / `_build_group`, 168 nodes: Sample Index over the table + the Bernstein sum + materials + UVs), the modifier (`enable` / `disable` / `is_enabled` / `set_input`: Blender 5.x keeps a Nodes modifier's values in `modifier.properties.inputs`), `apply_appearance`, `posed_control_points` (Bake Posed with the modifier off), the scene toggle (`apply_scene`, `BZ_OT_gn_surface`) and `refresh` + its `depsgraph_update_post` handler (the tables follow a topology edit), `evaluated` (positions + `bz_pid` / `bz_u` / `bz_v`, through `new_from_object` so it works in Edit Mode) | yes |
| `patchfeel.py` | the rules on the live edit mesh: `settle` (the per-redraw hook `draw._sync` calls, with the vertex types and Lock Handles), interior visibility (`apply_interior_visibility`, `hidden_points`), `selection` (the skin edits' selection), `BZ_OT_reset_interiors` | yes |
| `material.py` | textures → Blender images (fake user, import CRC stamp; `keep_skin_images` re-packs paint before a save), materials from DIFC/TXTR, `material_table`, the magenta colour key (bpy imported lazily) | lazy |
| `texdraw.py` | textured overlay shader + per-material draw lists (shared by the overlay, the ghost and the offscreen verifier) | yes |
| `rig.py` | armature import (bone snapshot names / parents / rests, a source-index stamp per bone) and rigid skinning; `rig_skeleton` / `current_bone_names` / `bone_renames` / `rename_canon` (bones by identity); `skeleton_error` (renames pass; added bones pass the add rule: `classify_added` / `added_bones`, Add Stub Socket's stamped bones: `created_stub_bones`, `new_stub_bones`); rest-pose edits back to the export | yes |
| `actions.py` | clips ↔ named Actions (import, export plan; the rename-canonical hash and stale F-curve paths; `unclaimed_actions`: Actions keying the rig that no rule exports, warned) | yes |
| `boneops.py` | Boner > Bones > Rename Bone (`rename_refusal`, `rename_bone`: the rig bone, vertex groups, every Action's F-curves; legacy rigs stamped first); Add Bone under Active (`add_bone_refusal`, `add_bone`: the rig left out of Edit Mode) and Adopt Added Bones (`adopt_pending`: stamps, the new-bone table, the parent's limb) | yes |
| `ikops.py` | Boner > Bones > IK Chains and Joint Limits (C8.4-5): the `IKCH` table as journaled rows (Add / Edit / Remove / Reset Chain, `chain_rows`, `flags_label`, `chain_bones_between`, `rig_path_problem` — every stock chain is one path of the hierarchy, 905/905; no shipped Relic binary reads the table, so its only job is to stay consistent), and the per-bone joint limits stamped at import (`source_limits`, `draw_limits_box`, Reset Joint Limits) | yes |
| `limbs.py` | limb colouring and selection helpers; the By Bone colour arrays (palette, colour table, per point / per surface sample) | no |
| `store.py` | per-object persistence: per-point mesh attributes (`.bz_cp` pool ids, `.bz_vert_bone`, `.bz_gen`) and ID-property tables journaled per generation, with a session-side copy (edit-mode Undo / Redo re-selects them, across Tab too); connection rings as corner loops with a per-ring encoding flag (store v2: verbatim ints for a non-standard ring; pre-v2 raw ints decoded on read, migrated atomically on the next ring-table write) plus the connection rows (classes and source identity, journaled) and limb rows; the pool guard, source stamp, rig / image resolution by identity; the journaled new-bone table (`new_bone_uids`, `add_new_bones`, `new_bone_keys`, `owner_count`: owner keys `n_src + j`); `live_skin` (the owner table read against the bone vertex groups) and `set_vert_bone` / `sync_bone_groups` (bpy imported lazily) | lazy |
| `combocore.py` | bpy-free combiner glue: status, stock dir and donor index, `Parent` caches, companions, `run_preview`, ghost arrays, the Quick tier and the precheck-only path, `COMBINE` messages; `ring_fix` (winding + stock start via `combotest.validate.winding_into_torso` / `align_ring_start`), `ring_start_plan` -> `RingStartPlan` (the rotation search scored the precheck's way, with the reversal it reports but never applies), `ring_status` and `ring_placement_messages` | no |
| `build.py` | build the control-point mesh object | yes |
| `scene.py` | scene objects (gatherers, characters, structures, props) as meshes: `build_scene` (import), `part_objects`, `build_part` (a part's mesh in the file's terms: split at UV seams and flat faces, per-material triangles, skin from the vertex groups), `part_unchanged`, `export_scene_object` (only edited parts rebuilt through ICParse `write_scene_part`, validated) and `export_part_geometry` | yes |
| `newproject.py` | New Project (**File > New > Impossible Creatures Project**): the creature scaffold or a stock scene donor (`game_archives`, `scene_donors`, `read_donor`), the animation choices (`drop_all_clips`, `borrow_clips` by bone name), the `bz_project` and `bz_donor` stamps | yes |
| `draw.py` | gpu surface overlay (cached, edit-mode-aware, each object drawn in its own guarded block with failures shown as text); By Bone batches from the live owners (`live_owners`, `bone_colors`; the colour arrays are GPU-free); connection rings coloured by status with a start marker and direction tick | yes |
| `props.py` | scene settings (density, show-surface, …); registers `BZCombineSettings` before `Scene.bz_combine` | yes |
| `ops.py` | operators + menus: import / export (creature and scene; an export over the creature's own base file re-bases the object through `store.rebase_source`, and `in_place_refusal` names the pending skeleton edit that would be applied twice), bake, Convert Mesh, Paint Skin, UV cage, limb / bone / connection authoring (Pick Ring modal in the viewport's WINDOW region, `view3d_window_region`), Extrude Patch, animations; `gather_export` (guards → `ExportJob`, vertex-group skin adoption), `build_export`, `export_bytes`, `edited_model`; `select_points` / `hidden_left_behind` / `hidden_selection_note` (a hidden interior a selection cannot reach is named, never dropped); the undo / redo / load / save handlers | yes |
| `limbops.py` | Boner > Limbs operators (set active, Rename, classification box, Add Standard Limb, Delete, Select Bones, Set Root Bone, Add / Remove Active Bone) and the list read-out (`limb_table`, cached for draw; `live_skeleton` -- the skeleton the export writes, which is what a bone list names -- over `source_skeleton`, `bone_edit_context`, `not_a_bone_message`, `_pin_added`) | yes |
| `connops.py` | Boner > Connections operators (set active, Create for Limb, Rename, Delete, Parent / Child box, Set Host Bone / Default, Optimise Ring Start over `ring_start_plans`, Add Stub Socket with `rig_edit_refusal` and the stamped bone creation) and the list read-out (`conn_table`: rows, status, child limb) | yes |
| `newcreature.py` | New Creature (From Scratch) through `ICParse.sgm.creature.scaffold` | yes |
| `edittools.py` | Validate (load checks, stock rulebook, combine checks on the export model), Symmetrize (X), Relax Seam Tangents | yes |
| `combopreview.py` | Combine Preview: settings, operators (preview, swap, Materialize, Clear Materialized, Validate Combines, Extract Stock), the ghost draw handler, state digest, auto refresh | yes |
| `ui.py` | N-panel (SGM tab, `BZ_PT_panel` "SGM Native Patches") and its child panels: Boner (Bones / Limbs / Connections tabs, state in `scene.bz_boner`) and Combine Preview; under a scene object it draws `sceneobj.ui_kit.draw_scene_pointer` and hides the creature tools | yes |
| `sceneobj/__init__.py` | the package's registry: `CORE`, `PREFIXES`, `discover`, `register` / `unregister` / `reload`, `MODULES`, `CLASSES` (registration-safe order), `MISSING_CORE`, `ERRORS`, `status` | optional (imports without it) |
| `sceneobj/model.py` | the bpy-free vocabulary: `Issue` / `issue` (ICParse `Finding`), `SourceBundle`, `OutBundle`, `ExportReport`, the `Slice` protocol and `STAGE_ORDER`, `SHARED_KEYS`, `refs`, `claim_edit` / `GUARD_CODES`, `foreach_get` / `foreach_set`, `pick_game_dir` | no |
| `sceneobj/ui_kit.py` | `alert`, `issue_rows`, `BZ_UL_rows` + `row_drawer`, `SubPanel`, `is_scene_object` / `is_scene_context`, `draw_scene_pointer` | yes |
| `sceneobj/prefs.py` | the add-on's preferences page (`BZ_AP_prefs`, `DEFAULTS`, `get`, `resolve_game_dir`, `resolve_test_game_dir`, `resolve_build_dir`, `resolve_mods_dir`); read by nothing else yet | yes |
| `sceneobj/panels_blueprint.py` | the Blueprint panel: `entity_of`, `blueprint_path`, `load_blueprint`, `classify`, `determine_folder`, `class_info`, `refresh_stats`, `blueprint_bytes`, `scene_bytes`, `export_entity`, `readme_text`, the five operators and the two panels | yes |
| `sceneobj/ops_map_import.py`, `ops_map_export.py`, `panels_map.py` | the Map core (0.5-0.6): File > Import > Impossible Creatures Map, the Map panel, `build_plan` / `save_map` (the save runs the `map_hooks` steps after the core edits and writes the map file and the files beside it) | yes |
| `sceneobj/map_hooks.py` | the extension points of the map tools (see "Extension points for map features"): import / save steps, checks, `import_map_bytes` (a map from memory), `ImportContext` / `SaveContext` | lazy |
| `sceneobj/map_look.py`, `map_creatures.py` | the game look (ground picture and detail, sun, sky, fog, water, border, decals, by the rules in `ICParse.sgb.game_look`; the Terrain look is editable through `Collection.ic_look`) and creatures shown as models | yes |
| `sceneobj/map_settings.py`, `ops_map_settings.py`, `panels_map_settings.py`, `ops_map_new.py` | File > New > Impossible Creatures Map (the 15 templates), Map settings (name, kind, players, starts, preview pictures), Terrain look, Map problems | yes |
| `sceneobj/map_paint.py`, `map_overlays.py`, `ops_map_paint.py`, `panels_map_paint.py` | painting: passability, the Overlay menu, terrain types, water levels, decal handles, texture stamping, blend tiles; one save step per layer | yes |
| `sceneobj/map_script.py`, `ops_map_script.py`, `panels_map_script.py` | scripting: groups, variables, triggers (`<name>.trg`), dialog triggers (`<name>.dlg`), AI scripts and armies, the script check | yes |
| `sceneobj/map_cinematics.py`, `ops_map_cinematics.py`, `panels_map_cinematics.py` | NISlet animations (`<map>.na`): import step, save step, checks; one collection per animation, a key frame empty each, path / speed line / preview | yes |
| `sceneobj/map_tools.py`, `map_crop.py`, `ops_map_tools.py`, `panels_map_tools.py` | height map, terrain (`.ter`) and entity (`.ent`) files, the Object List, the Blueprint List and Terrain Crop (a crop or a terrain import builds a new map from memory and keeps the old map's side files) | yes |

## Tools (`tools/`)

Not part of the add-on -- scripts you run by hand. None is imported by the add-on and none is in `_MODULES`.

| script | what it does |
|---|---|
| `build_extension.py` | builds the distributable extension `.zip`: the add-on's modules (`COPY_DIRS`: `_deps` and `sceneobj`), README.md, the `ICParse` / `combotest` / `construct` / `lupa` wheels and the generated manifest (minimum Blender from `bl_info`); `--version` sets the release number, `--output-dir` where the zip goes. Since 2026-11-17 the ICParse and combotest wheels carry a local version label made from their contents (`relabel_wheel`: `icparse-0.1.0+h<12 hex digits>`), because Blender keeps an installed wheel whose `.dist-info` name has not changed and an upgrade used to keep the older library. |
| `so_sweep.py` | the corpus sweeps over the stock scene objects: `--identity` (the 205 first-archive donors started as projects and exported untouched: 203 byte-identical, 2 refused), `--all` (every `art/ebps` scene row: 295 + 10 of 305), `--idempotent`, `--valid`, and `--noop` / `--edit` / `--round`, which skip until the slices they need exist. It fingerprints the game install before and after. |
| `map_roundtrip.py` | map editing and saving on seven stock maps of four sizes, in one headless Blender: save untouched (byte-identical), then every kind of edit in one save, read back with `ICParse.sgb.view.read`, opened again; the JSON carries the timings, the changed chunks and the pathfinding numbers. Run as `blender --background --factory-startup --python blender_native/tools/map_roundtrip.py -- OUT.json [MAP KEY ...]`. |
| `fetch_blender_deps.py` | fetches the compiled `lupa` for Blender's own Python into the git-ignored `_deps_bin/<cpXY-platform>/`, which is how the combiner's Lua ratings work in an editable checkout. |
| `dev_launch.py` | opens Blender's GUI with the add-on registered and a creature imported -- the fastest way to look at something by hand. |
| `extract_stock.py` | pulls stock creatures (and scene objects) out of the game's `.sga` archives with ICParse into `Assets/engine_stock` / `Assets/stock`. The add-on's *Extract Stock Creatures* button is the same thing from the panel. |
| `import_check.py` | batch headless import check: for every file given, geometry, rig, textures, limbs / connections and **every** animation (baked onto the rig and checked against ICParse at its middle frame). Useful over a whole stock directory. |
| `render_dump.py` | dumps what the add-on renders headlessly for an asset (positions, normals, UVs, triangles, textures) as an `.npz`, for `rdn-compare` against the Object Editor's Direct3D stream. This is what `run_all.py --parity` drives. |
| `offscreen_preview.py` | renders the live textured overlay through a real GPU offscreen buffer and exits -- the only way to see the overlay's own pixels without a session. Needs a display. |
| `demo_tour.py` | a short watchable tour of the Boner tools on the cheetah, ~10 captioned steps. Needs a display. |
| `fit_experiment.py` | the feasibility experiment A1 was built from: fit a patch network to Suzanne at several budgets and report the errors. Kept as the reference recipe. |
| `gn_surface_spike.py` | the D9 spike: measures the Geometry Nodes evaluated surface against the gpu overlay (parity, Sculpt Mode, modifiers / snapping / ray-cast, render, cost). Its numbers are quoted in ROADMAP_NATIVE.md. |

## Tests (`tests/`)

`tests/run_all.py`: **64 scripts, 20 under the repository's Python and 44 in headless Blender** (`run_all.py --list`
is the authoritative list; STATUS.md section 1 says what each one proves).

* pure (this repo's venv): `headless_identity`, `fastsurf_parity`, `edittools_test`, `extrude_test`,
  `export_builder_test`, `ring_test`, `ring_start_test`, `combine_offline_test`, `boner_test`, `limb_rows_test`,
  `limb_bones_test`, `conn_rows_test`, `stub_socket_test`, `bone_rename_test`, `bone_add_test`, `bone_topology_test`,
  `fit_mesh_test`, `extension_install_test` (builds the distributable zip and drives a clean Blender), `patchrules_test`,
  `so_model_pure_test`;
* in Blender (`--background --factory-startup --python-exit-code 1`): `blender_smoke`, `ebp_import_test`,
  `scene_import_test`, `blender_extrude_test`, `convert_test`, `anim_encode_test`, `rig_anim_test`,
  `anim_actions_test`, `scene_anim_export_test`, `skeleton_test`, `skin_test`, `limb_auth_test`, `texture_test`,
  `uv_test`, `new_creature_test`, `new_project_test`, `scene_retopo_test`, `scene_geometry_export_test`,
  `blueprint_poc_test`, `combine_preview_test`, `guard_test`, `skin_boner_test`, `boner_limbs_test`,
  `boner_limb_bones_test`, `boner_connections_test`, `boner_ring_start_test`, `boner_stub_test`, `boner_rename_test`,
  `boner_add_bone_test`, `boner_bone_topology_test`, `sculpt_refit_test`, `blender_fit_mesh_test`, `retarget_test`,
  `landmark_retarget_test`, `patch_feel_test`, `patch_select_test`, `topology_ops_test`, `texture_transfer_test`,
  `trim_stray_test`, `fit_to_rig_test`, `action_guard_test`, `gn_surface_test`, `so_contract_test`,
  `so_registry_test`;
* the sceneobj suites (`tests/so_*_test.py`, `tests/so_*_pure_test.py`) are found by name (`tests/so_suite.py`) and use
  the helpers of `tests/_so_common.py` (fresh temp folders, the machine-wide Blender launch lock, the game-install
  guard, a recording stand-in layout for panels);
* and the tiers on top: `--combine-full`, `--engine`, `--parity`.

## Status and roadmap

What is verified, and the known gaps, are in [STATUS.md](STATUS.md); what comes next is in
[ROADMAP_NATIVE.md](ROADMAP_NATIVE.md). The original build plan that used to close this document (lossless spine,
import and live surface, handle display, seam tools, the distributable extension) is finished; it is in git
(`git show 08536c1:DESIGN.md`).
