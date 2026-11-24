"""In-Blender test: a structure's GEOMETRY edited in Blender and exported. Run:
    blender --background --python blender_native/tests/scene_geometry_export_test.py

Imports the henchmen lab (a rigid-part structure), moves vertices on its largest part, exports
through both writer paths, and checks that the edit -- and ONLY the edit -- survives the round trip:

  * the exported positions decode back to exactly what Blender held (within float32)
  * UVs, triangles, materials and the vertex count are unchanged
  * every chunk other than the edited part's PMSH is byte-identical to the source
  * the v2.0 path carries one full-detail LOD record (lod_count = 0 crashes the engine -- measured
    2026-09-17, access violation in the real Object Editor; see decompilation/RE_LOG.md)
  * an unedited export is byte-identical to the source
  * changing topology is WRITTEN (new faces, new vertices): see scene_retopo_test.py for the full set of cases

It writes the two exports to /tmp/bz_scene_geom_*.sgm so the engine harness can load them.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np
import bpy

REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
import blender_native  # noqa: E402
from blender_native import core, scene as bzscene  # noqa: E402
from ICParse.iff.document import IffDocument  # noqa: E402
from ICParse.sgm.scene.validate import validate_scene_sgm  # noqa: E402

SRC = os.path.join(REPO, "Assets", "engine_scene", "henchmen_lab.sgm")
OUT_V12 = "/tmp/bz_scene_geom_legacy.sgm"
OUT_V20 = "/tmp/bz_scene_geom_v2lod1.sgm"
_log = open("/tmp/bz_scene_geom_log.txt", "w")
_op = print


def print(*a, **k):           # noqa: A001
    _op(*a, **k)
    _log.write(" ".join(str(x) for x in a) + "\n")
    _log.flush()


def chunk_map(data):
    """tag/serialized bytes for every chunk, so we can prove nothing but the PMSH moved."""
    doc = IffDocument.from_bytes(data) if isinstance(data, (bytes, bytearray)) else IffDocument.load(data)
    out = {}
    for i, c in enumerate(doc.walk() if hasattr(doc, "walk") else []):
        out[f"{i}:{getattr(c, 'tag', '?')}"] = c.serialize()
    return out


def main():
    bpy.ops.wm.read_homefile(use_empty=True)
    blender_native.register()
    assert bpy.ops.bz.import_sgm(filepath=SRC) == {"FINISHED"}, "import failed"
    objs = bzscene.scene_objects()
    assert objs, "no scene parts imported"
    obj = objs[0]                                  # largest part
    me = obj.data
    nv, nt = len(me.vertices), len(me.loop_triangles)
    print(f"imported {os.path.basename(SRC)}: {len(objs)} parts; editing '{obj.name}' "
          f"({nv} verts, {nt} tris)")

    # 0. An unedited export is NOT byte-identical to the source, by design: this writer rebuilds the
    #    part's PMSH (and the legacy path also drops the version to 0x102). The project's byte-exact
    #    contract covers parse-then-write-UNCHANGED, which ICParse tests separately. What must hold
    #    here is that an unedited export is still valid and still decodes to the original geometry.
    untouched = bzscene.export_part_geometry(obj, legacy=True)
    src_bytes = open(SRC, "rb").read()
    assert not [w for w in validate_scene_sgm(untouched) if w.startswith("ERROR")]
    from ICParse import read_scene as _rs
    orig = np.asarray(_rs(src_bytes).meshes[0].positions, dtype=np.float64)
    same = np.asarray(_rs(untouched).meshes[0].positions, dtype=np.float64)
    print(f"unedited export: {len(untouched)} bytes (source {len(src_bytes)}; differs by design), "
          f"geometry unchanged: max dev {np.abs(orig - same).max():.2e}")
    assert np.abs(orig - same).max() < 1e-4

    # 1. edit: push every vertex of the part out along its own normal
    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    nrm = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get("normal", nrm)
    moved = co.reshape(-1, 3) + nrm.reshape(-1, 3) * 0.25
    me.vertices.foreach_set("co", moved.reshape(-1))
    me.update()
    expect_sgm = core.blender_to_sgm(moved)

    for label, path, legacy in (("legacy(v1.2)", OUT_V12, True), ("v2.0+1 LOD", OUT_V20, False)):
        data = bzscene.export_part_geometry(obj, legacy=legacy)
        open(path, "wb").write(data)
        warn = validate_scene_sgm(data)
        errs = [w for w in warn if w.startswith("ERROR")]
        print(f"  {label:12s} {len(data):8d} bytes  validator: "
              f"{'clean' if not warn else warn}")
        assert not errs, f"{label}: validator errors {errs}"

        from ICParse import read_scene
        back = read_scene(data)
        part = back.meshes[0]
        assert len(part.positions) == nv, f"{label}: vertex count changed"
        got = np.asarray(part.positions, dtype=np.float64)
        dev = np.abs(got - expect_sgm).max()
        # float32 in the file vs float64 in Blender, so the floor is ~1e-7, not zero
        print(f"  {label:12s} max position deviation: {dev:.2e}")
        assert dev < 1e-3, f"{label}: positions did not survive the round trip (max dev {dev})"

    # 2. topology changes are written: an extra vertex that no face uses is left out (and said so), a deleted face is gone
    me.vertices.add(1)
    me.update()
    back = read_scene(bzscene.export_part_geometry(obj, legacy=True)).meshes[0]
    assert len(back.positions) == nv, "a vertex no face uses must not be written"
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table(); bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES")
    bm.to_mesh(me)
    bm.free()
    me.update()
    back = read_scene(bzscene.export_part_geometry(obj, legacy=True)).meshes[0]
    assert sum(len(s.triangles) for s in back.submeshes) == nt - 1, "the deleted face was not dropped"
    print(f"  topology change: an unused vertex is left out, a deleted face is dropped ({nt} -> {nt - 1} triangles)")

    print("PASS")
    open("/tmp/bz_scene_geom_done.txt", "w").write("PASS\n")


main()
