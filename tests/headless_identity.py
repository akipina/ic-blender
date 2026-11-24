"""Headless regression: prove the lossless spine without Blender.

Run from the repo (with the project venv that has ICParse + construct + numpy)::

    python blender_native/tests/headless_identity.py [creature.sgm ...]

Asserts, for each creature:
  1. writer identity                -- write(read(x)) == x
  2. edit-pipeline identity         -- untouched world->local round-trip is byte-identical, both as
                                       bytes (export_bytes) and as the in-memory export model
                                       (write(export_model(untouched)) == x)
  3. a real edit round-trips        -- nudging one control point changes ONLY that region
  4. the parsed-source cache hands out private copies -- editing an export model never leaks into
                                       the next read; creature_arrays_from_model == load_creature
  5. the export builder's fast path -- build_export_model with the FULL untouched add-on state (patch table,
                                       owners, limb rows with their bone lists, limbs, UVs, materials, types, every ring as its decoded
                                       corner loop) is the source, and its
                                       summary is empty; over every creature of Assets/stock and Assets/engine_stock
                                       (``--corpus``, the default with no arguments)
"""
import glob
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # blender_native/
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (blender_native/../)
import core  # noqa: E402
import structure  # noqa: E402


def check(path: str) -> None:
    raw = open(path, "rb").read()
    from ICParse.sgm import parser as sgm_parser
    m = sgm_parser.read(path)

    assert sgm_parser.write(m) == raw, "writer is not identity"

    data = core.load_creature(path)
    out = core.export_bytes(path, data.verts)            # untouched edit pipeline
    assert out == raw, "world->local round-trip not byte-identical"
    em = core.export_model(path, data.verts)             # the same, as the in-memory export model
    assert sgm_parser.write(em) == raw, "write(export_model(untouched)) not byte-identical"

    moved = data.verts.copy()
    moved[0] += np.array([0.123, 0.0, 0.0])              # nudge one control point
    out2 = core.export_bytes(path, moved)
    assert out2 != raw, "edit produced no change"
    assert len(out2) == len(raw), "edit changed file size (topology must be fixed)"
    em2 = core.export_model(path, moved)
    assert sgm_parser.write(em2) == out2, "export_bytes is not write(export_model)"

    # the cached source is a private copy per read: the edited export models above must not leak
    em2.vertices[0] = (9.0, 9.0, 9.0); em2.patches.pop(); em2.name = "leak"
    assert sgm_parser.write(core.read_source(path)) == raw, "an edit to an export model leaked into the source cache"
    d2 = core.creature_arrays_from_model(sgm_parser.read(path), path)
    assert np.array_equal(d2.verts, data.verts) and np.array_equal(d2.patches, data.patches), \
        "creature_arrays_from_model differs from load_creature"
    assert np.array_equal(d2.patch_uv, data.patch_uv) and np.array_equal(d2.patch_mat, data.patch_mat)
    full_state_identity(path, raw, m, data)
    print(f"  OK  {os.path.basename(path):32s}  verts={data.n_verts:5d} patches={len(data.patches):4d}")


def full_state(path, m, data):
    from ICParse.codecs import reconstruct as R
    return core.EditState(
        source_path=path, verts=data.verts, patches=data.patches, vert_owner=np.array(R.vertex_bone_owners(m)),
        patch_limb=core._patch_limb_of(m), patch_uv=data.patch_uv, patch_material=data.patch_mat,
        patch_type=np.array([p.type for p in m.patches]),
        connections=[ring_edit(i, c) for i, c in enumerate(m.limb_connections)],
        limbs=[structure.LimbRow(i, l.name, int(l.limb_id), tuple(l.bone_names)) for i, l in enumerate(m.limbs)])


def ring_edit(i, c):
    """A ring as the add-on stores it: its corner loop (re-encoded as closed edge pairs at export), or the ints
    verbatim when they are not that encoding."""
    from ICParse.sgm.creature.model import ring_corners
    corners = ring_corners(c.vertex_indices)
    if corners is not None:
        return core.RingEdit(index=i, bone=c.bone_name, corners=corners)
    return core.RingEdit(index=i, bone=c.bone_name, raw=list(c.vertex_indices))


def full_state_identity(path, raw, m, data):
    from ICParse.sgm import parser as sgm_parser
    assert sgm_parser.write(core.build_export_model(core.EditState(path, data.verts))) == raw, \
        "build_export_model(verts only) not byte-identical"
    built = core.build_export(full_state(path, m, data))
    assert not built.grown, "the untouched patch table took the grown path"
    assert sgm_parser.write(built.model) == raw, "build_export_model(full untouched state) not byte-identical"
    assert core.export_summary(built).parts == [], core.export_summary(built).parts
    src_ids = {id(l) for l in built.source.limbs}
    assert not any(id(l) in src_ids for l in built.model.limbs), "an export limb record is the parsed source's object"
    assert sgm_parser.write(built.source) == raw, "the parsed source was changed by the build"


def corpus() -> int:
    """Check 5 over every creature of the two stock corpora (files whose writer is not the identity are listed, not
    hidden: that is an ICParse gap, not the add-on's)."""
    from ICParse.sgm import parser as sgm_parser
    paths = sorted(glob.glob(os.path.join(REPO, "Assets/stock/*.sgm"))) + \
        sorted(glob.glob(os.path.join(REPO, "Assets/engine_stock/*.sgm")))
    n, skipped = 0, []
    for p in paths:
        raw = open(p, "rb").read()
        m = sgm_parser.read(p)
        if core.is_scene_model(m) or not getattr(m, "patches", None):
            continue
        if sgm_parser.write(m) != raw:
            skipped.append(os.path.relpath(p, REPO))
            continue
        full_state_identity(p, raw, m, core.creature_arrays_from_model(m, p))
        n += 1
    print(f"  corpus: {n} creatures byte-identical through the export builder's full untouched state"
          + (f"; NOT CHECKED (ICParse writer is not the identity on them): {skipped}" if skipped else ""))
    assert n >= 100, n
    return n


def main() -> int:
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob(os.path.join(REPO, "Assets/stock/*.sgm")))[:8]
    print(f"checking {len(args)} creature(s)...")
    for p in args:
        check(p)
    if not sys.argv[1:]:
        corpus()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
