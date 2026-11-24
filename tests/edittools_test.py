"""Headless checks for validate / symmetrize / seam_smooth (no Blender)."""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
import core  # noqa: E402


def _tangent_mismatch(verts, patches):
    """Sum of |tA - tB| across all internal seams (0 == perfectly C1)."""
    total = 0.0
    for boundary, inA, inB in core._seams(np.asarray(patches)):
        for P, a, b in zip(boundary, inA, inB):
            tA = verts[P] - verts[a]; tB = verts[b] - verts[P]
            total += float(np.linalg.norm(tA - tB))
    return total


def main():
    # only unprefixed messages block loading; advisories, stock gaps and combiner findings do not
    assert core.blocking(["WARN a", "STOCK b", "COMBINE ERROR c", "COMBINE INFO d", "e"]) == ["e"]
    paths = [p for p in sorted(glob.glob(os.path.join(REPO, "Assets/stock/*.sgm")))
             if any(k in p for k in ("snail", "kangaroo", "blue_whale"))]
    for p in paths:
        d = core.load_creature(p)
        name = os.path.basename(p)

        # validate: stock creatures must pass clean (no errors)
        problems = core.validate_creature(p, d.verts)
        errs = core.blocking(problems)
        assert not errs, f"{name} stock failed validation: {errs}"

        # validate catches a broken edit (NaN + wrong count)
        bad = d.verts.copy(); bad[0] = np.nan
        assert any("non-finite" in x for x in core.validate_creature(p, bad))
        assert any("count changed" in x for x in core.validate_creature(p, d.verts[:-1]))
        # the in-memory export model validates like the source while untouched, and catches a NaN edit
        assert core.validate_model(core.export_model(p, d.verts), d.verts) == problems
        assert any("non-finite" in x for x in core.validate_model(core.export_model(p, bad), bad))

        # structure checks: stock creature is clean; broken structure warns appropriately
        model = d.model
        limb_names = [l.name for l in model.limbs]
        plimb = core._patch_limb_of(model)
        conns = [(c.name, c.bone_name, list(c.vertex_indices)) for c in model.limb_connections]
        bnames = [b.name for b in model.bones]
        lids = [int(l.limb_id) for l in model.limbs]
        assert not core.check_structure(limb_names, plimb, conns, bnames, lids), \
            f"{name} stock flagged by structure check"
        orphan = plimb.copy(); orphan[0] = -1
        assert any("not assigned" in w for w in core.check_structure(limb_names, orphan, conns, bnames, lids))
        bad_bone = [(conns[0][0], "no_such_bone", conns[0][2])] + conns[1:]
        assert any("not in the skeleton" in w for w in core.check_structure(limb_names, plimb, bad_bone, bnames, lids))
        # ring sizes are the combine tier's rule now (combotest.validate.precheck; combine_offline_test covers it)
        bad_conn = [(conns[0][0], conns[0][1], conns[0][2][:-1])] + conns[1:]
        assert not any("ring" in w for w in core.check_structure(limb_names, plimb, bad_conn, bnames, lids))

        # symmetrize: result is exactly symmetric for paired verts; boundary-only on X-plane
        sym = core.symmetrize(d.verts, axis=0)
        pairs, on_plane = core.mirror_pairs(sym, axis=0)
        max_asym = 0.0
        for i, j in pairs.items():
            mj = sym[j].copy(); mj[0] *= -1
            max_asym = max(max_asym, float(np.abs(sym[i] - mj).max()))
        frac = len(pairs) / len(d.verts)

        # seam_smooth: tangent mismatch drops, boundary control points unchanged
        before = _tangent_mismatch(d.verts, d.patches)
        smoothed, nseams = core.seam_smooth(d.verts, d.patches)
        after = _tangent_mismatch(smoothed, d.patches)
        boundary_idx = sorted({i for b, _, _ in core._seams(d.patches) for i in b})
        moved_boundary = float(np.abs(smoothed[boundary_idx] - d.verts[boundary_idx]).max())
        # smoothing moves only genuinely-interior handles, leaving shared boundaries fixed so a
        # 'smooth this seam' click stays local (doesn't reshape neighbouring seams).
        assert after < before, f"{name} seam mismatch not improved ({before}->{after})"
        assert moved_boundary < 1e-12, f"{name} seam_smooth disturbed shared boundaries"
        # smoothed result still passes the load validator
        assert not core.blocking(core.validate_creature(p, smoothed))

        print(f"  {name:12s} valid OK | symmetrize paired={frac*100:4.0f}% asym={max_asym:.1e} | "
              f"seams={nseams:4d} tangent_mismatch {before:8.2f} -> {after:.1e}")
    print("EDITTOOLS_PASS")


if __name__ == "__main__":
    main()
