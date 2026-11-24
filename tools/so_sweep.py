#!/usr/bin/env python3
"""so_sweep -- the corpus sweeps of the sceneobj add-on (PLAN_FINAL section 10.2).

    .venv/bin/python3 blender_native/tools/so_sweep.py --identity      # the 205 first-archive donors: start a project, export untouched, bytes identical
    .venv/bin/python3 blender_native/tools/so_sweep.py --all           # every art/ebps scene .sgm ROW: 303 + the 2 models of enableminilabs.sga
    .venv/bin/python3 blender_native/tools/so_sweep.py --idempotent    # export twice = same bytes; export -> import -> export = same bytes
    .venv/bin/python3 blender_native/tools/so_sweep.py --valid         # the exported bytes pass ICParse's checks (and the rulebook, once it has rules)
    .venv/bin/python3 blender_native/tools/so_sweep.py --noop markers  # S-NOOP(slice): gather -> changed is False, the export is untouched
    .venv/bin/python3 blender_native/tools/so_sweep.py --edit          # S-EDIT: a slice's sample edit changes only its own chunks
    .venv/bin/python3 blender_native/tools/so_sweep.py --round         # S-ROUND(slice): the slice's decode re-encoded equals the source

Run from the venv python it starts a headless Blender (under the machine-wide launch lock) running this same file; run by Blender it does
the work. Baselines held: --identity 203 identical + 2 refused of 205; --all 295 identical + 10 refused of 305. Common options: ``--out FILE`` (JSON report, default in the temp folder), ``--limit N`` (first N rows), ``--only TEXT`` (rows
whose path contains TEXT), ``--population identity|all`` (rows for --idempotent / --valid / --noop / --edit / --round), ``--quiet``.

Rows are (archive, member path). A row ends as ``identical`` / ``ok``, as an EXPECTED ``refused`` (the file holds no geometry: a skeleton and
clips only, which cannot be imported until slice B13b -- ICParse's own reading must agree that there is no mesh), or as a failure:
``rebuilt`` (a part was rewritten although nothing was edited), ``differs`` (the bytes changed), ``unexpected_refusal``, ``error``,
``not_idempotent``, ``invalid``, ``changed``. The report is JSON; the exit code is 0 when every row passed, 1 when any row failed or the
identity baseline (203 identical) regressed, 3 when the mode cannot run yet (the slice or the entity layer it needs has not landed: said in
plain words, never a silent pass). The game install is fingerprinted before and after: a sweep must not write under it.

A slice can opt in to the modes that need its knowledge, with optional attributes (nothing here edits a slice):
``population`` (a key of POPULATIONS, or a function of the IFF document: which rows exercise it), ``sweep_edit(ctx, entity) -> set of chunk
tags it may change`` (S-EDIT) and ``round_trip(src) -> list of problems`` (S-ROUND).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))        # blender_native/tools
ADDON = os.path.dirname(HERE)
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(ADDON)
TESTS = os.path.join(ADDON, "tests")

EXIT_OK, EXIT_FAIL, EXIT_SKIP = 0, 1, 3

# what a sweep of the 205 first-archive donors gave when it was recorded (2026-11-17, PLAN_FINAL 10.2): 203 byte-identical, 2 skeleton-only refused
BASELINE_IDENTITY = {"checked": 205, "identical": 203, "refused": 2}
# the all-rows sweep when it was first run (2026-11-17): 305 rows (the plan's 303 + the 2 enableminilabs models), 295 identical; the 10 refused hold no mesh
# (4 AI location markers, 4 cinematics nis_1 / nis_6 / nis_m15 / nis_m3, the 2 gyro copters): they become importable with slice B13b
BASELINE_ALL = {"checked": 305, "identical": 295, "refused": 10}
# which rows exercise a slice (S-NOOP populations of PLAN_FINAL 10.2): rows whose IFF tree holds one of these chunk / form tags
POPULATIONS = {"markers": ("MRKR",), "morphs": ("MRPH",), "team": ("TEAM",), "skeleton": ("SKEL",), "shaders": ("SHDR",), "clips": ("ANIM",)}
PASS_STATUSES = {"identical", "ok", "refused"}
SCENE_DIR = "art/ebps/"


# ================================================================================================================ arguments
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="so_sweep.py", description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--identity", action="store_true", help="the 205 first-archive donors, started as projects, exported untouched")
    mode.add_argument("--all", "--all-rows", dest="all_rows", action="store_true", help="every art/ebps scene .sgm row of every archive (+ enableminilabs)")
    mode.add_argument("--idempotent", action="store_true", help="export twice, and export -> import -> export, give the same bytes")
    mode.add_argument("--valid", action="store_true", help="the exported bytes pass ICParse's checks")
    mode.add_argument("--noop", metavar="SLICE", help="S-NOOP of one slice: gather -> changed is False and the export is untouched")
    mode.add_argument("--edit", action="store_true", help="S-EDIT: a slice's sample edit changes only the chunks it declares")
    mode.add_argument("--round", nargs="?", const="*", metavar="SLICE", help="S-ROUND: the slice's decode re-encoded equals the source")
    ap.add_argument("--population", choices=("identity", "all"), default="identity", help="rows for --idempotent / --valid / --noop / --edit / --round")
    ap.add_argument("--out", help="the JSON report (default: a file in the temp folder)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows")
    ap.add_argument("--only", default="", help="only rows whose member path contains this text")
    ap.add_argument("--workdir", help="scratch folder (default: a fresh temp folder, removed afterwards)")
    ap.add_argument("--blender", help="the Blender binary (default: $BLENDER or the one on PATH)")
    ap.add_argument("--quiet", action="store_true", help="print only failures and the summary")
    a = ap.parse_args(argv)
    a.mode = ("identity" if a.identity else "all" if a.all_rows else "idempotent" if a.idempotent else "valid" if a.valid
              else "noop" if a.noop else "edit" if a.edit else "round")
    return a


# ================================================================================================================ launcher (venv python)
def launch(argv) -> int:
    """Start a headless Blender on this file, under the launch lock, and return its exit code."""
    sys.path.insert(0, TESTS)
    import _so_common as C
    a = parse_args(argv)
    if "--out" not in argv:
        argv = [*argv, "--out", os.path.join(os.environ.get("BZ_TMP") or "/tmp", f"so_sweep_{a.mode}_{os.getpid()}.json")]
    blender = a.blender or C.BLENDER
    cmd = [blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python", os.path.abspath(__file__), "--", *argv]
    env = dict(os.environ, PYIC_REPO=REPO, PYTHONDONTWRITEBYTECODE="1", SDL_AUDIODRIVER="dummy")
    return C.run_locked(cmd, env=env, cwd=REPO).returncode


# ================================================================================================================ populations
def sgm_has_geometry(data: bytes) -> bool:
    """ICParse's own reading: does this scene .sgm hold a mesh with vertices? (A file without one cannot be imported.)"""
    from ICParse import read_scene
    return any(m.positions for m in read_scene(data).meshes)


def identity_rows(newproject) -> list:
    """The 205 donors File > New > Impossible Creatures Project offers: ``{"kind", "archive", "member"}``, characters then structures."""
    rows = []
    for kind in ("CHARACTER", "STRUCTURE"):
        for archive, member in newproject.scene_donors(kind):
            rows.append({"kind": kind, "archive": archive, "member": member})
    return rows


def all_rows(newproject) -> list:
    """Every art/ebps scene-layout .sgm of the base game, the expansions (one row per archive that holds the path) and the datamod
    ``enableminilabs.sga``: 303 + 2 rows (273 unique paths) on the install the plan was written against."""
    from ICParse.iff.document import IffDocument
    from ICParse.sgm.parser import is_scene
    root = os.path.dirname(newproject.game_archives()[0]) if newproject.game_archives() else None
    archives = list(newproject.game_archives())
    if root:
        archives += sorted(p for p in _glob(os.path.join(root, "*", "datamods", "enableminilabs.sga")))
    rows = []
    for archive in archives:
        arc = newproject._archive(archive)
        members = sorted({getattr(e, "path", str(e)).replace("\\", "/").lower() for e in arc.entries})
        datamod = os.path.basename(os.path.dirname(archive)).lower() == "datamods"
        for member in members:
            if not member.endswith(".sgm") or not (member.startswith(SCENE_DIR) or datamod):
                continue
            if is_scene(IffDocument.from_bytes(arc.read_path(member))):         # creature (patch) models share these folders
                rows.append({"kind": "ROW", "archive": archive, "member": member})
    return rows


def _glob(pattern):
    import glob
    return glob.glob(pattern)


def read_row(newproject, row) -> bytes:
    return newproject._archive(row["archive"]).read_path(row["member"])


def has_tags(data: bytes, tags) -> bool:
    from ICParse.iff.document import IffDocument
    doc = IffDocument.from_bytes(data)
    return any(doc.find(t) is not None for t in tags)


def chunk_table(data: bytes) -> dict:
    """``{path: payload bytes}`` of every leaf chunk of an IFF file; a path is the tags down from the root, with an index for repeats."""
    from ICParse.iff.chunk import FormChunk
    from ICParse.iff.document import IffDocument
    out = {}

    def walk(chunks, prefix):
        seen = {}
        for c in chunks:
            label = f"FORM {c.form_type}" if isinstance(c, FormChunk) else c.tag
            n = seen[label] = seen.get(label, -1) + 1
            path = f"{prefix}/{label}[{n}]"
            if isinstance(c, FormChunk):
                walk(c.children, path)
            else:
                out[path] = bytes(c.raw)

    walk(IffDocument.from_bytes(data).root_chunks, "")
    return out


def chunk_diff(a: bytes, b: bytes) -> set:
    """The tags (``MRKR``, ``FORM SKEL`` ...) of the leaf chunks that differ, were added or were removed between two IFF files."""
    ta, tb = chunk_table(a), chunk_table(b)
    paths = {p for p in set(ta) | set(tb) if ta.get(p) != tb.get(p)}
    return {p.rsplit("/", 1)[-1].split("[")[0] for p in paths}


# ================================================================================================================ the work (in Blender)
class Sweep:
    def __init__(self, a, work):
        import bpy
        sys.path.insert(0, REPO)
        sys.path.insert(0, TESTS)
        import _so_common as C
        import blender_native
        from blender_native import newproject, scene as bzscene
        self.bpy, self.C, self.a, self.work = bpy, C, a, work
        self.bn, self.newproject, self.bzscene = blender_native, newproject, bzscene
        self.so = blender_native.sceneobj

    # ---- one row -----------------------------------------------------------------------------------------------
    def fresh(self):
        self.bpy.ops.wm.read_homefile(use_empty=True)

    def refusal(self, row, data, text) -> tuple:
        """(status, detail) for an import the add-on refused: expected only if ICParse itself finds no mesh in the file."""
        if "no geometry" in text and not sgm_has_geometry(data):
            return "refused", "no geometry in the file (a skeleton and clips only)"
        if "no geometry" in text:
            return "unexpected_refusal", f"refused as having no geometry, but ICParse finds meshes in it: {text[:160]}"
        return "unexpected_refusal", text[:200]

    def extract(self, row, data) -> str:
        path = os.path.join(self.work, os.path.basename(row["archive"]).lower(), *row["member"].split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def import_row(self, row, data):
        """Import one row as the add-on does for a user. Returns ``(path of the source, None)`` or ``(None, (status, detail))``."""
        path = self.extract(row, data)
        self.fresh()
        try:
            res = self.bpy.ops.bz.import_sgm(filepath=path, clips_as_actions=False)
        except RuntimeError as e:
            return None, self.refusal(row, data, str(e).replace("Error:", "").strip())
        if "FINISHED" not in res:
            return None, ("error", f"the import did not finish: {res}")
        return path, None

    def export_bytes(self, tag="out") -> bytes:
        out = os.path.join(self.work, f"{tag}.sgm")
        if os.path.exists(out):
            os.remove(out)
        res = self.bpy.ops.bz.export_scene_geometry(filepath=out)
        assert "FINISHED" in res, f"export refused: {res}"
        with open(out, "rb") as f:
            return f.read()

    def active(self):
        return self.bpy.context.view_layer.objects.active

    # ---- the modes ----------------------------------------------------------------------------------------------
    def do_identity(self, row):
        """The sweep the plan calls S-IDENT: File > New > Project from the donor, export untouched, compare the bytes."""
        kind, member = row["kind"], row["member"]
        name = member.rsplit("/", 1)[-1][:-4]
        data, _ebp = self.newproject.read_donor(row["archive"], member)
        self.fresh()
        p = os.path.join(self.work, f"{kind}_{name}.sgm")
        try:
            try:
                res = self.bpy.ops.bz.new_project(filepath=p, project_type=kind, donor=member, anim_source="NONE")
            except RuntimeError as e:
                return self.refusal(row, data, str(e).replace("Error:", "").strip())
            if "FINISHED" not in res:
                return "error", f"the project did not start: {res}"
            geo = self.bzscene.export_scene_object(self.active())
            if geo.rebuilt:
                return "rebuilt", f"parts {geo.rebuilt} were rewritten although nothing was edited: {'; '.join(geo.notes[:2])}"
            with open(p, "rb") as f:
                return ("identical", "") if geo.data == f.read() else ("differs", "nothing was rebuilt but the bytes differ")
        finally:
            for f in (p, p[:-4] + ".ebp"):
                if os.path.exists(f):
                    os.remove(f)

    def do_all(self, row):
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        geo = self.bzscene.export_scene_object(self.active())
        if geo.rebuilt:
            return "rebuilt", f"parts {geo.rebuilt} were rewritten although nothing was edited: {'; '.join(geo.notes[:2])}"
        return ("identical", "") if geo.data == data else ("differs", "nothing was rebuilt but the bytes differ")

    def do_idempotent(self, row):
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        one, two = self.export_bytes("exp1"), self.export_bytes("exp2")
        if one != two:
            return "not_idempotent", "two exports of the same entity differ"
        if one != data:
            return "differs", "the untouched export is not the source"
        again = os.path.join(self.work, "again", os.path.basename(path))
        os.makedirs(os.path.dirname(again), exist_ok=True)
        with open(again, "wb") as f:
            f.write(one)
        self.fresh()
        res = self.bpy.ops.bz.import_sgm(filepath=again, clips_as_actions=False)
        if "FINISHED" not in res:
            return "error", f"the exported file could not be imported again: {res}"
        return ("ok", "") if self.export_bytes("exp3") == one else ("not_idempotent", "export -> import -> export is not a fixed point")

    def do_valid(self, row):
        from ICParse.validate import validate_scene_sgm
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        out = self.export_bytes("valid")
        problems = [str(p) for p in validate_scene_sgm(out) if str(p).startswith("ERROR")]
        rulebook = self.rulebook()
        if rulebook is not None:
            ctx = rulebook.EntityContext(sgm=out, name=os.path.basename(path)[:-4])
            problems += [f.to_str() for f in rulebook.validate(ctx) if f.level == "ERROR"]
        return ("invalid", "; ".join(problems[:3])) if problems else ("ok", "")

    def rulebook(self):
        import importlib.util
        if importlib.util.find_spec("ICParse.entity.rulebook") is None:
            return None
        from ICParse.entity import rulebook
        rulebook.discover()
        return rulebook

    # ---- modes that need a slice and the entity layer -------------------------------------------------------
    def entity_api(self) -> tuple:
        """``(modules, missing)``: the C22 entry points the entity layer (slice B01) provides, and the names that are not there yet."""
        import importlib
        import importlib.util
        mods, missing = {}, []
        for module, fn in (("entity", "find_entity"), ("source", "load_source")):
            full = f"{self.so.__name__}.{module}"
            if importlib.util.find_spec(full) is None or not hasattr(importlib.import_module(full), fn):
                missing.append(f"sceneobj.{module}.{fn}")
            else:
                mods[module] = importlib.import_module(full)
        return mods, missing

    def slices_to_run(self, names) -> tuple:
        reg = self.so.slices()
        if names == "*":
            return reg, None
        picked = [s for s in reg if s.name == names]
        if not picked:
            return [], f"no slice named {names!r} is registered (registered: {[s.name for s in reg] or 'none'})"
        return picked, None

    def in_population(self, s, data) -> bool:
        pop = getattr(s, "population", None)
        if pop is None:
            return True
        if callable(pop):
            from ICParse.iff.document import IffDocument
            return bool(pop(IffDocument.from_bytes(data)))
        return has_tags(data, POPULATIONS[pop])

    def do_noop(self, row, picked):
        mods, _missing = self.entity_api()
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        entity = mods["entity"].find_entity(self.active())
        if entity is None:
            return "error", "the import produced no entity"
        src = mods["source"].load_source(entity)
        busy = []
        for s in picked:
            if not self.in_population(s, data):
                continue
            changed = s.changed(s.read(src), s.gather(self.bpy.context, entity))
            if changed:
                busy.append(s.name)
        if busy:
            return "changed", f"an untouched entity reports a change in: {', '.join(busy)}"
        return ("ok", "") if self.export_bytes("noop") == data else ("differs", "the untouched export is not the source")

    def do_edit(self, row, picked):
        mods, _missing = self.entity_api()
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        entity = mods["entity"].find_entity(self.active())
        allowed = set()
        for s in picked:
            if self.in_population(s, data):
                allowed |= set(s.sweep_edit(self.bpy.context, entity))
        diff = chunk_diff(data, self.export_bytes("edit"))
        stray = diff - allowed
        if stray:
            return "changed", f"chunks changed that no edit declared: {sorted(stray)}"
        return ("ok", "") if diff or not allowed else ("changed", "the sample edit changed nothing")

    def do_round(self, row, picked):
        mods, _missing = self.entity_api()
        data = read_row(self.newproject, row)
        path, refused = self.import_row(row, data)
        if refused:
            return refused
        entity = mods["entity"].find_entity(self.active())
        src = mods["source"].load_source(entity)
        problems = []
        for s in picked:
            if self.in_population(s, data):
                problems += [f"{s.name}: {p}" for p in s.round_trip(src)]
        return ("invalid", "; ".join(problems[:3])) if problems else ("ok", "")


def population_rows(sw, a) -> list:
    use_all = a.mode == "all" or (a.mode != "identity" and a.population == "all")
    rows = all_rows(sw.newproject) if use_all else identity_rows(sw.newproject)
    if a.only:
        rows = [r for r in rows if a.only in r["member"]]
    if a.limit:
        rows = rows[:a.limit]
    return rows


def run_mode(sw, a) -> tuple:
    """``(rows with their outcome, skip reason or None)``."""
    picked = None
    if a.mode in ("noop", "edit", "round"):
        _mods, missing = sw.entity_api()
        if missing:
            return [], f"the entity layer (slice B01) has not landed: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing"
        wanted = {"noop": a.noop, "edit": "*", "round": a.round}[a.mode]
        picked, why = sw.slices_to_run(wanted)
        if why:
            return [], why
        hook = {"edit": "sweep_edit", "round": "round_trip"}.get(a.mode)
        if hook:
            picked = [s for s in picked if callable(getattr(s, hook, None))]
            if not picked:
                return [], f"no registered slice defines {hook}() yet: a slice opts in with it (see the docstring of so_sweep.py)"
    rows = population_rows(sw, a)
    work = {"identity": sw.do_identity, "all": sw.do_all, "idempotent": sw.do_idempotent, "valid": sw.do_valid,
            "noop": lambda r: sw.do_noop(r, picked), "edit": lambda r: sw.do_edit(r, picked), "round": lambda r: sw.do_round(r, picked)}[a.mode]
    out = []
    for i, row in enumerate(rows):
        t0 = time.perf_counter()
        try:
            status, detail = work(row)
        except Exception as e:                       # one row's failure is its own result, never the sweep's
            status, detail = "error", f"{type(e).__name__}: {str(e)[:200]}" + ("" if isinstance(e, (AssertionError, RuntimeError)) else "\n" + traceback.format_exc()[-400:])
        finally:                                     # the row's extracted files are not needed any more
            shutil.rmtree(os.path.join(sw.work, os.path.basename(row["archive"]).lower()), ignore_errors=True)
        rec = {**{k: (os.path.basename(v) if k == "archive" else v) for k, v in row.items()}, "status": status, "detail": detail,
               "secs": round(time.perf_counter() - t0, 3)}
        out.append(rec)
        if not a.quiet or status not in PASS_STATUSES:
            print(f"[{i + 1}/{len(rows)}] {status:18s} {rec['archive']}:{rec['member']}" + (f"  -- {detail}" if detail and status not in ("identical", "ok") else ""), flush=True)
    return out, None


def summarize(a, rows) -> dict:
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    failed = [r for r in rows if r["status"] not in PASS_STATUSES]
    summary = {"mode": a.mode, "checked": len(rows), "counts": counts, "failed": len(failed)}
    base = {"identity": BASELINE_IDENTITY, "all": BASELINE_ALL}.get(a.mode)
    if base and not a.only and not a.limit:
        summary["baseline"] = {"expected": base, "identical_now": counts.get("identical", 0), "refused_now": counts.get("refused", 0),
                               "regressed": counts.get("identical", 0) < base["identical"] or len(rows) < base["checked"]}
    return summary


def run_in_blender(argv) -> int:
    a = parse_args(argv)
    sys.dont_write_bytecode = True
    work = a.workdir or tempfile.mkdtemp(prefix="bz_so_sweep_")
    os.makedirs(work, exist_ok=True)
    sw = Sweep(a, work)
    sw.bn.register()
    t0 = time.perf_counter()
    rows, skip, guard_note = [], None, ""
    try:
        try:
            with sw.C.install_guard():                      # nothing under the game install may be written
                rows, skip = run_mode(sw, a)
            guard_note = "the game install is unchanged" if sw.C.install_root() else "no game install was found to watch"
        except AssertionError as e:
            guard_note = str(e)                             # rows keep what the sweep found before the guard spoke
            print("FAIL " + guard_note, flush=True)
    finally:
        sw.bn.unregister()
        if not a.workdir:
            shutil.rmtree(work, ignore_errors=True)
    if skip:
        report = {"mode": a.mode, "skipped": skip}
        print(f"SO_SWEEP {a.mode}: SKIPPED -- {skip}", flush=True)
        code = EXIT_SKIP
    else:
        summary = summarize(a, rows)
        report = {"mode": a.mode, "population": "all" if a.mode == "all" else a.population, "summary": summary, "install": guard_note,
                  "secs": round(time.perf_counter() - t0, 1), "rows": rows}
        regressed = bool(summary.get("baseline", {}).get("regressed"))
        code = EXIT_FAIL if (summary["failed"] or regressed or guard_note.startswith("the game install changed")) else EXIT_OK
        counts = " ".join(f"{k}={v}" for k, v in sorted(summary["counts"].items()))
        print(f"SO_SWEEP {a.mode}: checked={summary['checked']} {counts} failed={summary['failed']}"
              + (f" baseline={'REGRESSED' if regressed else 'held'} ({summary['baseline']['expected']['identical']} identical + "
                 f"{summary['baseline']['expected']['refused']} refused)" if "baseline" in summary else "")
              + f" install: {guard_note} -- {'PASS' if code == EXIT_OK else 'FAIL'} in {report['secs']} s", flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(report, f, indent=1)
        print(f"report: {a.out}", flush=True)
    return code


def main() -> int:
    try:
        import bpy  # noqa: F401
    except ImportError:
        return launch(sys.argv[1:])
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return run_in_blender(argv)


if __name__ == "__main__":
    sys.exit(main())
