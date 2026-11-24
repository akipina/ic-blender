"""Gatherers and structures with CHANGED topology: edit a scene object's mesh in Blender and export it. Run:
    blender --background --python blender_native/tests/scene_retopo_test.py
Needs the game install (IC_INSTALL, or the Steam default) for the stock donors.

Every case starts from File > New > Project (bz.new_project) and ends by reading the exported file back:
  * an unedited object exports byte-identical, geometry-only and with clips
  * a gatherer (skinned) with its faces subdivided / deleted / flat-shaded / UV-split: vertex, triangle, UV, normal and
    skin counts come out as the mesh says, the skeleton and the clips are byte-identical to the source, the validator is clean
  * unweighted vertices are bound to the root bone and reported; a material slot the file has no shader for is refused
  * a structure (rigid parts) with two parts edited: those two are rebuilt (own BVOL refreshed), the other 21 stay byte-identical
  * the exported file can be imported and edited again (v1.2 source); asking for v2.0 on it is refused with a reason
  * geometry and clips leave in one file (bz.export_scene)
  * a rebuilt part carries the bounding volumes the game builds: its own, one per palette bone (exactly 1 + palette; each
    holds the vertices that name its bone, the empty form for a bone no vertex names), and the entity box follows it
  * a part above the game's 12,288 vertex entries is refused with a plain message (both exporters)
  * an edited part with damage states (the Lab's tent) is refused; with "Drop the damage states of edited parts" it is
    written without them and the export says how many (File > Export and Export Entity Folder); a tent left alone keeps
    them byte for byte
  * File > Export lists bz.export_scene as "Impossible Creatures Scene Object (.sgm)"
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import numpy as np
import bpy
import bmesh

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "blender_native", "tests"))
import _so_common  # noqa: E402
import blender_native  # noqa: E402
from blender_native import core, scene as bzscene, store  # noqa: E402
from ICParse.iff.chunk import FormChunk  # noqa: E402
from ICParse.iff.document import IffDocument  # noqa: E402
from ICParse.sgm.scene import bounds as B  # noqa: E402
from ICParse.sgm.scene import model as SM  # noqa: E402
from ICParse.sgm.scene.structs import DATA_BVOL  # noqa: E402
from ICParse.sgm.scene.validate import validate_scene_sgm  # noqa: E402

D = _so_common.tmpdir("retopo_")
HENCH = "art/ebps/gatherers/henchmen.sgm"
LAB = "art/ebps/structures/lab.sgm"


def say(*a):
    print(" ".join(str(x) for x in a), flush=True)


def fresh():
    bpy.ops.wm.read_homefile(use_empty=True)
    os.makedirs(D, exist_ok=True)
    for f in os.listdir(D):
        os.remove(os.path.join(D, f))


def active():
    return bpy.context.view_layer.objects.active


def start(kind, donor, name):
    fresh()
    path = f"{D}/{name}.sgm"
    assert "FINISHED" in bpy.ops.bz.new_project(filepath=path, project_type=kind, donor=donor, anim_source="OWN")
    return active(), path


def edit(obj, fn):
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    fn(bm)
    bm.to_mesh(me)
    bm.free()
    me.update()


def forms(data, tag):
    doc = IffDocument.from_bytes(data)
    return [c.serialize() for c in doc.find_all(tag)]


def only_geometry_differs(a, b, edited):
    """Every chunk of two exports that is not an edited part's PMSH or one of the BVOLs is byte-identical."""
    for tag in ("SKEL", "ANIM", "SHDR", "TXRF"):
        assert forms(a, tag) == forms(b, tag), f"{tag} chunks differ"
    pa = SM.scene_pmsh_forms(IffDocument.from_bytes(a))
    pb = SM.scene_pmsh_forms(IffDocument.from_bytes(b))
    assert len(pa) == len(pb)
    for i, (x, y) in enumerate(zip(pa, pb)):
        if i not in edited:
            assert x.serialize() == y.serialize(), f"part {i} changed but was not edited"


def export(obj, op="export_scene_geometry", **kw):
    out = f"{D}/out_{op}_{len(os.listdir(D))}.sgm"
    res = getattr(bpy.ops.bz, op)(filepath=out, **kw)
    assert "FINISHED" in res, res
    return out, open(out, "rb").read()


def refused(fn, why):
    try:
        fn()
    except (RuntimeError, ValueError) as e:
        assert why in str(e), (why, str(e))
        return str(e)
    raise AssertionError(f"accepted; expected a refusal mentioning {why!r}")


def check_volumes(data, mesh_index=0):
    """The rebuilt part's bounding volumes are the game's: its own holds every vertex; one per palette bone, 1 + palette of
    them, each the box around exactly the vertices that name its bone (the empty form for a bone no vertex names); the entity
    box holds the part and is the union of the part group. Returns the number of bones whose volume holds vertices."""
    doc = IffDocument.from_bytes(data)
    m = next(x for x in B.mesh_parts(doc) if x.part == mesh_index)
    assert len(m.volumes) == 1 + len(m.palette), (len(m.volumes), len(m.palette))
    vols = [B.volume_of(f) for f in m.volumes]
    assert vols[0].contains(m.positions, tol=1e-5)
    full = 0
    for k, bone in enumerate(m.palette):
        pts = m.bone_points(k)
        if len(pts):
            assert vols[1 + k] is not None and vols[1 + k].contains(pts, tol=1e-5), bone
            assert vols[1 + k].box_error(B.build_bvol(pts)) == 0.0, bone
            full += 1
        else:
            assert vols[1 + k] is None, f"bone '{bone}' names no vertex: its volume must be the empty form"
    if full > 1:
        assert len({vols[1 + k].obb for k in range(len(m.palette)) if vols[1 + k] is not None}) > 1, \
            "every bone volume is the same box: the volumes were flattened"
    box = B.entity_obb(doc)
    assert box.contains(m.positions, tol=1e-5), "the entity box does not hold the rebuilt part"
    if m.group is not None:
        assert box.box_error(B.group_union(m.group)[0]) == 0.0, "the entity box is not the union of the part group"
    return full


def run():
    blender_native.register()

    # 1. nothing edited -> nothing rewritten
    obj, p = start("CHARACTER", HENCH, "Hench")
    src = open(p, "rb").read()
    assert export(obj)[1] == src and export(obj, "export_scene")[1] == src
    lobj, lp = start("STRUCTURE", LAB, "Lab")
    assert export(lobj)[1] == open(lp, "rb").read()
    say("unedited gatherer and structure export byte-identical, geometry-only and with clips")

    # stock parts that are not clean must still come back untouched: NaN UVs (m14_conwall), vertices no face uses (swamp stump)
    for donor in ("art/ebps/nature/m14_conwall.sgm", "art/ebps/nature/swamp/swp_stump_01.sgm"):
        o, pth = start("STRUCTURE", donor, "Odd")
        geo = bzscene.export_scene_object(o)
        assert not geo.rebuilt and geo.data == open(pth, "rb").read(), (donor, geo.rebuilt)
    say("stock parts with NaN UVs and with vertices no face uses export untouched, nothing rebuilt")

    # 2. a gatherer, every triangle subdivided: new vertices carry interpolated weights and UVs
    obj, p = start("CHARACTER", HENCH, "Hench")
    src = open(p, "rb").read()
    d0 = core.load_scene(p)
    nv0, nt0 = len(d0.parts[0].verts), len(d0.parts[0].tris)
    edit(obj, lambda bm: bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=False))
    out, data = export(obj)
    assert not [w for w in validate_scene_sgm(data) if w.startswith("ERROR")]
    d1 = core.load_scene(out)
    part = d1.parts[0]
    assert len(part.verts) > nv0 and len(part.tris) >= 3 * nt0, (len(part.verts), len(part.tris))
    assert len(part.skin) == len(part.verts) and all(len(s) >= 1 for s in part.skin), "a vertex lost its bone weights"
    assert len(part.uvs) == len(part.verts) and len(part.normals) == len(part.verts)
    assert np.allclose(part.verts[:nv0], d0.parts[0].verts, atol=1e-5), "the original vertices moved or reordered"
    assert d1.bone_names == d0.bone_names and len(d1.animations) == len(d0.animations)
    only_geometry_differs(src, data, edited={0})
    full = check_volumes(data)
    assert full >= 20, full
    say(f"gatherer subdivided: {nv0} -> {len(part.verts)} vertices, {nt0} -> {len(part.tris)} triangles; skin/UV/normal on every vertex; "
        f"skeleton, materials and all clips byte-identical; {full} bone volumes hold exactly their bones' vertices, the entity box follows")

    # 3. faces deleted
    obj, p = start("CHARACTER", HENCH, "Hench")
    d0 = core.load_scene(p)
    gone = []
    def drop_half(bm):
        faces = list(bm.faces)[::2]
        gone.append(len(faces))
        bmesh.ops.delete(bm, geom=faces, context="FACES")
    edit(obj, drop_half)
    out, data = export(obj)
    part = core.load_scene(out).parts[0]
    assert len(part.tris) == len(d0.parts[0].tris) - gone[0]
    assert len(part.verts) <= len(d0.parts[0].verts)
    assert not [w for w in validate_scene_sgm(data) if w.startswith("ERROR")]
    say(f"gatherer, {gone[0]} faces deleted: {len(d0.parts[0].tris)} -> {len(part.tris)} triangles, {len(d0.parts[0].verts)} -> {len(part.verts)} vertices")

    # 4. flat shading splits every corner off; the split copies keep the weights and take the face normal
    obj, p = start("CHARACTER", HENCH, "Hench")
    edit(obj, lambda bm: [setattr(f, "smooth", False) for f in bm.faces])
    out, data = export(obj)
    part = core.load_scene(out).parts[0]
    assert len(part.verts) == 3 * len(part.tris), (len(part.verts), len(part.tris))
    assert all(len(s) >= 1 for s in part.skin)
    tri = part.verts[part.tris]                                   # (T, 3, 3) Blender space
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ok = np.linalg.norm(fn, axis=1) > 1e-12
    fn = fn[ok] / np.linalg.norm(fn[ok], axis=1)[:, None]
    nrm = part.normals[part.tris[ok][:, 0]]
    assert (np.abs(np.einsum("ij,ij->i", fn, nrm)) > 0.999).all(), "a flat face's vertices do not carry its face normal"
    say(f"gatherer, flat shaded: {len(part.verts)} vertices = 3 x {len(part.tris)} triangles, every normal is its face's")

    # 5. one UV edit splits exactly one vertex
    obj, p = start("CHARACTER", HENCH, "Hench")
    nv0 = len(core.load_scene(p).parts[0].verts)
    def nudge(bm):
        uv = bm.loops.layers.uv.active
        v = next(v for v in bm.verts if len(v.link_loops) >= 2)
        v.link_loops[0][uv].uv.x += 0.02
    edit(obj, nudge)
    out, data = export(obj)
    part = core.load_scene(out).parts[0]
    assert len(part.verts) == nv0 + 1, (nv0, len(part.verts))
    say(f"gatherer, one corner's UV moved: {nv0} -> {len(part.verts)} vertices (the seam)")

    # 6. a vertex with no weights is bound to the root bone and the export says so; a missing shader is refused
    obj, p = start("CHARACTER", HENCH, "Hench")
    d0 = core.load_scene(p)
    obj.vertex_groups.clear()
    geo = bzscene.export_scene_object(obj, force=True)
    assert any("no bone weights" in n for n in geo.notes), geo.notes
    assert any("root bone" in n for n in geo.notes)
    obj, p = start("CHARACTER", HENCH, "Hench")
    obj.data.polygons[0].material_index = len(d0.materials)
    refused(lambda: bzscene.export_scene_object(obj), "has no shader in the file")
    say("no weights: bound to the root bone and reported; a material slot without a shader: refused")

    # 7. a structure: edit two parts of 23, the other 21 stay byte-identical, the edited ones' own bounds follow
    lobj, lp = start("STRUCTURE", LAB, "Lab")
    src = open(lp, "rb").read()
    parts = sorted(bzscene.part_objects(lobj), key=lambda o: -len(o.data.vertices))
    big, mid = parts[0], parts[1]
    pa, pb = int(big[bzscene.KEY_SCENE_PART]), int(mid[bzscene.KEY_SCENE_PART])
    d0 = core.load_scene(lp)
    edit(big, lambda bm: bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.5), verts=bm.verts[:]))
    edit(mid, lambda bm: bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=False))
    geo = bzscene.export_scene_object(lobj)
    assert sorted(geo.rebuilt) == sorted([pa, pb]) and len(geo.kept) == len(parts) - 2, (geo.rebuilt, len(geo.kept))
    only_geometry_differs(src, geo.data, edited={d0.parts[pa].mesh_index, d0.parts[pb].mesh_index})
    open(f"{D}/lab_two.sgm", "wb").write(geo.data)
    d1 = core.load_scene(f"{D}/lab_two.sgm")
    assert len(d1.parts[pb].verts) > len(d0.parts[pb].verts)
    assert np.allclose(d1.parts[pa].verts[:, 2] - d0.parts[pa].verts[:, 2], 0.5, atol=1e-5)
    doc = IffDocument.from_bytes(geo.data)
    pf = SM.scene_pmsh_forms(doc)
    for pi in (pa, pb):
        own = next(c for c in pf[d1.parts[pi].mesh_index].children if isinstance(c, FormChunk) and c.form_type == "BVOL")
        con = DATA_BVOL.parse(next(x for x in own.children if x.tag == "DATA").raw)
        P = core.blender_to_sgm(d1.parts[pi].verts)
        assert np.allclose(np.asarray(con.obb_half_extents), (P.max(0) - P.min(0)) / 2, atol=1e-4), "own BVOL did not follow the edit"
    assert not [w for w in validate_scene_sgm(geo.data) if w.startswith("ERROR")]
    for pi in (pa, pb):
        check_volumes(geo.data, d1.parts[pi].mesh_index)
    say(f"structure: parts {pa} (moved) and {pb} (subdivided {len(d0.parts[pb].verts)} -> {len(d1.parts[pb].verts)} vertices) rebuilt, own bounds refreshed, "
        f"{len(geo.kept)} others byte-identical")

    # 8. the rebuilt file is a v1.2 source: it imports, edits and exports again; v2.0 on it is refused with a reason
    bpy.ops.wm.read_homefile(use_empty=True)
    again = f"{D}/lab_two.sgm"
    assert "FINISHED" in bpy.ops.bz.import_sgm(filepath=again)
    o2 = active()
    big2 = max(bzscene.part_objects(o2), key=lambda o: len(o.data.vertices))
    edit(big2, lambda bm: bmesh.ops.translate(bm, vec=(0.25, 0.0, 0.0), verts=bm.verts[:]))
    geo2 = bzscene.export_scene_object(o2)
    assert geo2.rebuilt == [int(big2[bzscene.KEY_SCENE_PART])]
    assert not [w for w in validate_scene_sgm(geo2.data) if w.startswith("ERROR")]
    refused(lambda: bzscene.export_scene_object(o2, legacy=False, force=True), "already in the v1.2 layout")
    say("the exported file imports, edits and exports again; v2.0 asked of a v1.2 part is refused with a reason")

    # 9. geometry and clips in one file
    obj, p = start("CHARACTER", HENCH, "Hench")
    src = open(p, "rb").read()
    d0 = core.load_scene(p)
    edit(obj, lambda bm: bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=False))
    arm = store.rig(obj)
    from blender_native import actions as bza
    victim = bza.actions_of(arm)[0].name
    bpy.data.actions.remove(bpy.data.actions[victim])
    out, data = export(obj, "export_scene")
    d1 = core.load_scene(out)
    assert len(d1.parts[0].verts) > len(d0.parts[0].verts), "the geometry edit was not written"
    assert [a.name for a in d1.animations] == [a.name for a in d0.animations if a.name != victim], "the clip was not dropped"
    assert d1.bone_names == d0.bone_names
    say(f"bz.export_scene: subdivided mesh and the deleted clip '{victim}' leave together ({len(d0.animations)} -> {len(d1.animations)} clips)")

    # 10. more vertex entries than the game draws in one part: refused, by both exporters, with what to do
    obj, p = start("CHARACTER", HENCH, "Hench")
    edit(obj, lambda bm: bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=3, use_grid_fill=True))
    edit(obj, lambda bm: bmesh.ops.triangulate(bm, faces=bm.faces[:]))
    edit(obj, lambda bm: [setattr(f, "smooth", False) for f in bm.faces])     # flat: three entries per triangle
    msg = refused(lambda: bzscene.export_scene_object(obj), "the game draws at most 12,288 per part")
    assert f"'{obj.name}'" in msg and "Reduce its detail" in msg and "split it into two parts" in msg, msg
    n = int(msg.split(" comes to ", 1)[1].split(" ", 1)[0].replace(",", ""))
    assert n > SM.MAX_PART_VERTICES == 12288, n
    for op in ("export_scene_geometry", "export_scene"):
        out = f"{D}/too_dense_{op}.sgm"
        refused(lambda: getattr(bpy.ops.bz, op)(filepath=out), "12,288")
        assert not os.path.exists(out), "a refused export must not write a file"
    say(f"a part of {n:,} vertex entries is refused (the game's limit is 12,288 per part), by both exporters")

    # 11. damage states: the Lab's tent carries 11; editing it is refused, or it is written without them on request
    lobj, lp = start("STRUCTURE", LAB, "Lab")
    src = open(lp, "rb").read()
    d0 = core.load_scene(lp)
    f0 = SM.scene_pmsh_forms(IffDocument.from_bytes(src))
    held = [o for o in bzscene.part_objects(lobj) if SM.morph_target_count(f0[d0.parts[int(o[bzscene.KEY_SCENE_PART])].mesh_index])]
    assert len(held) == 1, [o.name for o in held]
    tent = held[0]
    ti = int(tent[bzscene.KEY_SCENE_PART])
    tm = d0.parts[ti].mesh_index
    n_states = SM.morph_target_count(f0[tm])
    edit(tent, lambda bm: bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.3), verts=bm.verts[:]))
    refused(lambda: bzscene.export_scene_object(lobj), "has damage states")
    for op in ("export_scene_geometry", "export_scene"):
        out = f"{D}/tent_{op}.sgm"
        refused(lambda: getattr(bpy.ops.bz, op)(filepath=out), "editing its mesh would break them")
        assert not os.path.exists(out)
    geo = bzscene.export_scene_object(lobj, drop_morphs=True)
    assert geo.rebuilt == [ti] and geo.damage_states_dropped == {ti: n_states}, (geo.rebuilt, geo.damage_states_dropped)
    assert any(f"its {n_states} damage states were left out" in t for t in geo.notes), geo.notes
    for op in ("export_scene_geometry", "export_scene"):
        out, data = export(lobj, op, drop_damage_states=True)
        f1 = SM.scene_pmsh_forms(IffDocument.from_bytes(data))
        assert SM.morph_target_count(f1[tm]) == 0
        assert all(f1[i].serialize() == f0[i].serialize() for i in range(len(f0)) if i != tm), "another part changed"
        assert forms(data, "ANIM") == forms(src, "ANIM") and forms(data, "SKEL") == forms(src, "SKEL")
        assert not [w for w in validate_scene_sgm(data) if w.startswith("ERROR")]
        check_volumes(data, tm)
    # the same choice in the Blueprint panel's Export Entity Folder, whose .sgm is what bz.export_scene writes
    from blender_native.sceneobj import panels_blueprint as BP
    ent = BP.entity_of(lobj)
    try:
        BP.scene_bytes(bpy.context, ent)
        raise AssertionError("Export Entity Folder accepted an edited part with damage states")
    except BP.Refused as e:
        assert "Drop the damage states of edited parts" in str(e) and "Export Entity Folder" in str(e), str(e)
    data, rebuilt, _kept, notes = BP.scene_bytes(bpy.context, ent, drop_morphs=True)
    assert rebuilt == [ti] and SM.morph_target_count(SM.scene_pmsh_forms(IffDocument.from_bytes(data))[tm]) == 0
    assert any(f"its {n_states} damage states were left out" in t for t in notes), notes
    assert "drop_damage_states" in BP.BZ_OT_export_entity_folder.__annotations__
    # a part edited next to the tent: the tent and its damage states keep their bytes
    lobj, lp = start("STRUCTURE", LAB, "Lab")
    other = next(o for o in sorted(bzscene.part_objects(lobj), key=lambda o: -len(o.data.vertices))
                 if int(o[bzscene.KEY_SCENE_PART]) != ti)
    edit(other, lambda bm: bmesh.ops.translate(bm, vec=(0.0, 0.0, 0.3), verts=bm.verts[:]))
    out, data = export(lobj)
    assert SM.scene_pmsh_forms(IffDocument.from_bytes(data))[tm].serialize() == f0[tm].serialize()
    say(f"the Lab's tent ({n_states} damage states): edited -> refused by both exporters; with the drop option written without them "
        f"(the other parts and the clips byte-identical); left alone next to an edited part -> byte-identical")

    # 12. File > Export names what bz.export_scene writes: the whole scene object
    from blender_native import ops as bzops
    menu = type("Menu", (), {"layout": _so_common.FakeLayout()})()
    bzops.menu_export(menu, bpy.context)
    labels = {kw["idname"]: kw["text"] for kind, kw in menu.layout.calls if kind == "operator"}
    assert labels.get("bz.export_scene") == "Impossible Creatures Scene Object (.sgm)", labels
    assert not any("Animations" in t for t in labels.values()), labels
    say("File > Export: bz.export_scene is 'Impossible Creatures Scene Object (.sgm)'")
    blender_native.unregister()


_so_common.run_test("scene_retopo", run)
say("PASS")
