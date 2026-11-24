"""Pull creatures (and, optionally, scene objects) out of the game's .sga archives with ICParse.

    .venv/bin/python3 blender_native/tools/extract_stock.py                 # Engine.sga stock -> Assets/engine_stock (+ .lua/.lsc)
    .venv/bin/python3 blender_native/tools/extract_stock.py --scene         # + henchmen / structures / props -> Assets/engine_scene
    .venv/bin/python3 blender_native/tools/extract_stock.py --archive telluriandata.sga --prefix art/ebps/stock/ --out Assets/tellurian_stock

Engine.sga's 61 base-game creatures are blueprints that EMBED their model (FORM BOBJ); the tool writes
the .ebp as shipped plus the embedded creature as <name>.sgm (extracted by ICParse.ebp, byte-exact).
telluriandata's creatures are proxy blueprints beside their .sgm, so both files are copied as-is.
The stock creatures' .lua (limbattributes) and .lsc (limb-scale rules) companions come too: the combiner needs them
(combotest.loader, the add-on's combine preview, whose Extract Stock Creatures button calls :func:`extract`).
Assets/ is git-ignored: never commit what this writes.
"""
import argparse
import os
import sys

from ICParse import sga as SGA
from ICParse import ebp as EBP

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IC = os.environ.get("IC_INSTALL", os.path.expanduser("~/.local/share/Steam/steamapps/common/Impossible Creatures"))

SCENE_PICKS = (            # a henchman, two structures, a prop, a character: one of each scene-object flavour
    "art/ebps/gatherers/henchmen", "art/ebps/structures/henchmen_lab", "art/ebps/structures/gyro_copter_henchman",
    "art/ebps/nature/", "art/ebps/characters/",
)


STOCK_EXTS = (".ebp", ".sgm", ".lua", ".lsc")


def extract(archive, prefix, out_dir, limit=None, exts=(".ebp", ".sgm"), root=None):
    """Copy the ``exts`` entries under ``prefix`` of ``archive`` (in ``root``, default $IC_INSTALL or the Steam
    install) to ``out_dir``, writing each embedded creature of a model blueprint as its ``.sgm``.
    Returns (written paths, {blueprint kind: count})."""
    a = SGA.read(os.path.join(root or IC, archive))
    by_path = {e.path.lower(): e for e in a.entries}
    os.makedirs(out_dir, exist_ok=True)
    written, kinds = [], {}
    for e in sorted(a.entries, key=lambda e: e.path.lower()):
        p = e.path.lower()
        if not p.startswith(prefix.lower()) or not p.endswith(tuple(exts)):
            continue
        if limit and p.count("/") > prefix.count("/"):          # no recursion into sub-dirs
            continue
        data = a.read(e)
        name = os.path.basename(e.path)
        out = os.path.join(out_dir, name)
        with open(out, "wb") as f:
            f.write(data)
        written.append(out)
        if p.endswith(".ebp"):
            bp = EBP.read(data)
            kinds[bp.kind] = kinds.get(bp.kind, 0) + 1
            if bp.kind == "model":                              # embedded creature -> its own .sgm
                sgm_out = os.path.splitext(out)[0] + ".sgm"
                if os.path.splitext(p)[0] + ".sgm" not in by_path:   # don't shadow a shipped sibling
                    with open(sgm_out, "wb") as f:
                        f.write(bp.embedded_sgm_bytes())
                    written.append(sgm_out)
    return written, kinds


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="Engine.sga")
    ap.add_argument("--prefix", default="art/ebps/stock/")
    ap.add_argument("--out", default=os.path.join(REPO, "Assets", "engine_stock"))
    ap.add_argument("--scene", action="store_true", help="also extract henchmen / structures / props / characters")
    args = ap.parse_args(argv)
    written, kinds = extract(args.archive, args.prefix, args.out, limit=True, exts=STOCK_EXTS)
    print(f"{args.archive} {args.prefix}: wrote {len(written)} files to {args.out}; blueprint kinds {kinds}")
    if args.scene:
        out = os.path.join(REPO, "Assets", "engine_scene")
        a = SGA.read(os.path.join(IC, args.archive))
        os.makedirs(out, exist_ok=True)
        n = 0
        picked = set()
        for e in sorted(a.entries, key=lambda e: e.path.lower()):
            p = e.path.lower()
            if not p.endswith((".ebp", ".sgm")):
                continue
            for pick in SCENE_PICKS:
                if p.startswith(pick.lower()) and (not pick.endswith("/") or os.path.splitext(p)[0] in picked
                                                   or len([q for q in picked if q.startswith(pick.lower())]) < 2):
                    picked.add(os.path.splitext(p)[0])
                    with open(os.path.join(out, os.path.basename(e.path)), "wb") as f:
                        f.write(a.read(e))
                    n += 1
                    break
        print(f"scene objects: wrote {n} files to {out}: {sorted(os.listdir(out))}")


if __name__ == "__main__":
    main(sys.argv[1:])
