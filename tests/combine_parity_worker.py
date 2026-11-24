"""Venv side of combine_preview_test's interpreter parity check: the same combines, run by the repo venv's Python.

    .venv/bin/python3 blender_native/tests/combine_parity_worker.py JOB.json OUT_PREFIX

JOB.json: {"source": the creature's source path (companions / blueprint stem), "edited": the edited creature's
exported .sgm, "stock": stock dir, "default_size": 3, "runs": [{"other": stock name, "role": "TORSO"|"DONOR",
"groups": [part, ...]}, ...]}. Writes OUT_PREFIX.json (one record per run) and OUT_PREFIX.npz (world vertices per
run, key "run<i>"). Nothing is compared here: Blender compares, with tolerances (numpy's lapack-lite vs OpenBLAS
differ by a ULP in near-zero bone-matrix entries, so no byte hashes).
"""
import json
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree

import numpy as np


def record(pr) -> dict:
    """What the parity check compares, from a PreviewResult."""
    r = pr.result
    return dict(ok=pr.ok, error=pr.error, size=r.size, flyer=bool(r.is_flyer), patches=len(r.model.patches),
                dropped=[[k, w] for k, _d, w in r.dropped], grafted=sorted(k for k, v in r.limb_scales.items() if v.grafted),
                scale=float(r.scale), limb_scales={k: float(v.scale) for k, v in r.limb_scales.items()},
                entries={k: [v[0], v[1]] for k, v in r.attributes.entries.items()}, lua=pr.lua,
                python=sys.version.split()[0], numpy=np.__version__)


def world(pr) -> np.ndarray:
    from ICParse.codecs import reconstruct as RC
    return RC.world_vertices(pr.result.model)


def main(argv):
    # imported here, so Blender can import record() / world() from this file without a second copy of the add-on
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # blender_native/
    import combocore as CC
    job = json.load(open(argv[0]))
    from ICParse import read_sgm
    out, arrays = [], {}
    for i, run in enumerate(job["runs"]):
        edited, comp = CC.edited_parent(read_sgm(job["edited"]), {"bz_source": job["source"]},
                                        default_size=job.get("default_size", 3))
        other = CC.parent_for_file(os.path.join(job["stock"], run["other"] + ".sgm"))
        pr = CC.run_preview(edited, other, run["role"], run["groups"], CC.Options(default_size=job.get("default_size", 3)),
                            notes=comp.notes)
        if not pr.ok:
            out.append(dict(ok=False, error=pr.error))
            continue
        out.append(record(pr))
        arrays[f"run{i}"] = world(pr)
    with open(argv[1] + ".json", "w") as fh:
        json.dump(out, fh)
    np.savez(argv[1] + ".npz", **arrays)
    print("PARITY_WORKER_OK", len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
