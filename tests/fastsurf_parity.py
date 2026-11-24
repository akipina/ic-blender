"""Validate fastsurf against the ICParse reference, and benchmark a dirty single-vert edit."""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
import core        # noqa: E402
import fastsurf    # noqa: E402


def main():
    paths = sorted(glob.glob(os.path.join(REPO, "Assets/stock/*.sgm")))
    paths = [p for p in paths if any(k in p for k in ("pistol_shrimp", "ChickenTest.", "snail", "albatross"))]
    for p in paths:
        d = core.load_creature(p)
        for n in (4, 8):
            types = [int(pp.type) for pp in d.model.patches]
            Vr, Tr, Nr = core.tessellate_engine(d.verts, d.patches, types, n=n)   # reference (engine normals)
            topo = fastsurf.TopoCache(d.patches, n, types)
            Vf, Nf = topo.eval_all(d.verts)
            assert np.array_equal(topo.tris, Tr), "triangle topology mismatch (engine cell split)"
            dv = np.abs(Vf - Vr).max(); dn = np.abs(Nf - Nr).max()
            assert dv < 1e-9, f"position mismatch {dv}"
            assert dn < 1e-9, f"normal mismatch {dn}"

            # dirty-edit timing: move 1 control point, update only affected patches
            surfV, surfN = topo.eval_all(d.verts)
            verts2 = d.verts.copy(); verts2[0] += np.array([0.2, 0.0, 0.0])
            dirty_patches = topo.patches_for_dirty_verts([0])
            t = time.perf_counter()
            for _ in range(50):
                topo.eval_dirty(verts2, surfV, surfN, dirty_patches)
            ms = (time.perf_counter() - t) / 50 * 1000
            # correctness: dirty update must equal a full re-eval
            Vfull, Nfull = topo.eval_all(verts2)
            assert np.abs(surfV - Vfull).max() < 1e-9, "dirty != full"
            assert np.abs(surfN - Nfull).max() < 1e-9, "dirty normals != full (weld not redone)"
            print(f"  {os.path.basename(p):16s} n={n} patches={topo.Np:4d}: "
                  f"parity OK (dV={dv:.1e}) | 1-vert edit touches {len(dirty_patches)} patches "
                  f"in {ms:.3f} ms")
    print("FASTSURF_PARITY_PASS")


if __name__ == "__main__":
    main()
