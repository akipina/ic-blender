"""From-scratch geometry: extrude a patch and export the grown topology (pure Python, no Blender).
    python blender_native/tests/extrude_test.py
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
import core  # noqa: E402
from ICParse.sgm import parser as sgm_parser  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402

SRC = os.path.join(REPO, "Assets/stock/snail.sgm")


def main():
    d = core.load_creature(SRC)
    model = d.model
    owner = np.array(R.vertex_bone_owners(model))
    plimb = core._patch_limb_of(model)
    ptype = np.array([p.type for p in model.patches])

    pi = 0
    new_verts, new_cp = core.extrude_patch(d.verts, d.patches[pi], side=3)
    assert len(new_verts) == 12 and len(new_cp) == 16
    # shared edge: new patch's row0 == source patch's bottom row (watertight on that side)
    assert new_cp[:4] == [int(d.patches[pi][c]) for c in (12, 13, 14, 15)]

    # grow the addon state
    verts = np.vstack([d.verts, new_verts])
    patches = np.vstack([d.patches, [new_cp]])
    inh = int(owner[int(d.patches[pi][12])])               # inherit a shared-corner's bone
    vert_owner = np.concatenate([owner, [inh] * 12])
    patch_mat = np.concatenate([d.patch_mat, [d.patch_mat[pi]]])
    patch_uv = np.vstack([d.patch_uv, [d.patch_uv[pi]]])
    patch_type = np.concatenate([ptype, [ptype[pi]]])
    patch_limb = np.concatenate([plimb, [plimb[pi]]])

    out = core.export_grown(SRC, verts, patches, vert_owner, patch_mat, patch_uv, patch_type, patch_limb)
    open("/tmp/snail_extrude.sgm", "wb").write(out)
    m2 = sgm_parser.read("/tmp/snail_extrude.sgm")

    grew = len(m2.vertices) == len(model.vertices) + 12 and len(m2.patches) == len(model.patches) + 1
    valid = all(0 <= i < len(m2.vertices) for p in m2.patches for i in p.control_points)
    # existing geometry preserved: world of the new model's first 1910 logical points == original
    W_orig = R.world_vertices(model)
    W2 = R.world_vertices(m2)
    ws = np.array(sorted(map(tuple, np.round(W_orig, 4))))
    w2s = np.array(sorted(map(tuple, np.round(W2, 4))))
    # every original world point still present (subset check via the sorted original)
    preserved = all(tuple(np.round(p, 4)) in set(map(tuple, np.round(W2, 4))) for p in W_orig)

    print(f"EXTRUDE verts {len(model.vertices)}->{len(m2.vertices)} patches "
          f"{len(model.patches)}->{len(m2.patches)} grew={grew} cps_valid={valid} "
          f"orig_geometry_preserved={preserved}")
    assert grew and valid and preserved
    # Validate's in-memory model of the grown creature: the same bytes Export writes, and the same load gates
    gm = core.export_grown_model(SRC, verts, patches, vert_owner, patch_mat, patch_uv, patch_type, patch_limb)
    assert sgm_parser.write(gm) == out, "export_grown is not write(export_grown_model)"
    from ICParse import validate as ICval
    vm = core.validate_model(gm, verts)
    assert not [x for x in vm if "count changed" in x], vm
    assert core.blocking(vm) == [x for x in ICval.check_model(m2) if not x.startswith("WARN")], vm
    print("EXTRUDE_PASS")


if __name__ == "__main__":
    main()
