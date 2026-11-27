"""Scene objects (structures, gatherers, characters, props): keeping the Blender session and the files it exports in step.

Two dangers, one for each way the file under the session can stop being the file the session was built on:

* **The file changed behind the session.** The export is built on the source ``.sgm`` as it is on disk: every part, bone, marker
  and clip the session does not touch keeps that file's bytes, and every stamp the session carries (bone indices, part numbers,
  clip hashes, shader records) is a statement about THAT file. At import the file's SHA-1 is stamped on every object of the model
  (:func:`stamp_source`); an export first checks the file still has it (:func:`source_error`).
* **The export writes onto the source itself.** Most of what the session holds is absolute (a moved vertex, an edited key,
  a new UV), so exporting it over the file it was built on gives a file the next export reproduces. The rest is a DIFFERENCE
  against the file ("this bone is renamed", "this part is new", "this clip is renamed", "this texture is replaced"): once the file
  holds it, the next export would apply it a second time. The creature side refuses the same case (``ops.in_place_refusal``). Here
  :func:`guard_write` names what is in the way and refuses before anything is written, and :func:`after_write` re-stamps the session
  on the file just written when nothing of that kind was involved, so it goes on matching the file.

Why these are the differences (each is read from the export's own report, so the rule cannot drift from the writer):
the skeleton edit reads the rig against the bone stamps of the IMPORT (``scene_skeleton.read_rig``); parts are told apart from new
ones by ``bz_scene_part`` and ``bz_new_part`` (``scene.part_objects``); markers by their stamped source names (``slice_markers``); a
clip is told from a renamed or new one by the Action's stamped source clip (``actions.plan_export``); a material by its stamped
shader record (``scene_materials``). None of those stamps is rewritten by an export.

Game rule behind the "source changed" check: the engine reads a loose file in ``Data\\`` before the archive's copy
(``App_SetupFilePathAliases`` IC.exe 0x004034cc, SigmaApp.cpp:2764, "Raw HD Data" priority 500 against ICData.sga 400), so a file written over the
source is what the game loads next time -- a wrong rebuild is not just a bad export, it is the game's model.
"""
from __future__ import annotations

import hashlib
import os

KEY_SHA1 = "bz_source_sha1"       # on every object of the model: SHA-1 of the source .sgm when the model was opened (or last written in place)
KEY_SIZE = "bz_source_size"       # ... and its size


class InPlaceRefused(ValueError):
    """The export would write onto the model's own source file in a way the session could not follow. The message is the plain reason."""


# ------------------------------------------------------------------------------------------------------------------ the stamp
def file_sha1(path: str):
    """``(size, hex SHA-1)`` of a file."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return os.path.getsize(path), h.hexdigest()


def same_file(a: str, b: str) -> bool:
    """Whether two paths name the same file (the same inode when both exist, else the same absolute path)."""
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        import bpy
        return os.path.abspath(bpy.path.abspath(a)) == os.path.abspath(bpy.path.abspath(b))


def _source_file(src: str):
    """The real file a ``bz_source`` names (a ``.sgm`` itself), or None when it is not a file on disk (a map import's archive reference)."""
    try:
        from . import core
        path = core.resolve_source(src)
    except Exception:                                                     # noqa: BLE001 -- a source that cannot be resolved has no stamp
        return None
    return path if path and os.path.isfile(path) else None


def objects_of(src: str) -> list:
    """Every object that carries ``bz_source`` = ``src``: the parts, their markers and the like."""
    import bpy
    return [o for o in bpy.data.objects if o.get("bz_source") == src]


def stamp_source(src: str, path: str = None) -> bool:
    """Stamp the SHA-1 and size of the source file on every object of the model (``path``: the file, when it is not ``src`` itself).
    False when there is no file to stamp (the model comes from an archive)."""
    path = path or _source_file(src)
    if not path or not os.path.isfile(path):
        return False
    size, sha = file_sha1(path)
    for o in objects_of(src):
        o[KEY_SHA1] = sha
        o[KEY_SIZE] = int(size)
    return True


def stamp_of(obj):
    """``(sha1, size)`` stamped on ``obj`` or, failing that, on another object of its model; None for a model opened before the stamp existed."""
    for o in [obj] + objects_of(obj.get("bz_source", "")):
        sha = o.get(KEY_SHA1)
        if sha:
            return str(sha), int(o.get(KEY_SIZE, -1))
    return None


def source_error(obj):
    """Why the source file can no longer be the base of an export (None when it is the file the model was opened from, or when no stamp was made).

    The sentence names the file and what to do; nothing is written or changed."""
    src = obj.get("bz_source", "")
    path = _source_file(src) if src else None
    if path is None:
        return None                                                       # an archive reference, or a file the import itself reports missing
    name = os.path.basename(src)
    stamp = stamp_of(obj)
    if stamp is None:
        return None
    size, sha = file_sha1(path)
    if sha != stamp[0]:
        return (f"The model file {name} was changed on disk after it was opened here (by another program, or by an export from "
                f"another session). The export is built on that file, so it would mix the new file with what this session knows of "
                f"the old one. Import {name} again to carry on from the file as it is now.")
    return None


# ------------------------------------------------------------------------------------------------------------------ what a write onto the source would apply twice
def _looks_bytes(data: bytes) -> list:
    """The shader forms, texture references and embedded textures of a model, as bytes (the part of the file the materials step writes)."""
    from ICParse.iff.chunk import FormChunk
    from ICParse.iff.document import IffDocument
    doc = IffDocument.from_bytes(data)
    sigm = next((c for c in doc.root_chunks if isinstance(c, FormChunk) and c.form_type == "SIGM"), None)
    if sigm is None:
        return []
    out = []
    for c in sigm.children:
        if (isinstance(c, FormChunk) and c.form_type in ("SHDR", "TXTR")) or (not isinstance(c, FormChunk) and c.tag == "TXRF"):
            out.append(bytes(c.serialize()))
    return out


def differences(geo, plan, source_raw: bytes, data: bytes = None) -> list:
    """What this export holds that is a DIFFERENCE against the source file (so cannot be written onto it), as short plain phrases.

    ``geo``: the ``scene.SceneExport``; ``plan``: the ``actions.ClipPlan`` (None when the clips are not exported); ``source_raw``: the source file;
    ``data``: the bytes the export would write (``geo.data`` is only the geometry step, before the clips)."""
    data = geo.data if data is None else data
    out = []
    rig_edit = getattr(geo, "rig_edit", None)
    if rig_edit is not None and (rig_edit.changes or rig_edit.deleted_groups):
        out.append("the bones you added, deleted, renamed, moved or re-parented")
    if geo.parts_added or geo.parts_removed:
        out.append("the parts you added or removed")
    if geo.markers_added or geo.markers_removed or geo.markers_renamed:
        out.append("the markers you added, deleted or renamed")
    if any(r.get("added") or r.get("removed") for r in getattr(geo, "morphs", {}).values()):
        out.append("the damage states you added or deleted")
    if _looks_bytes(data) != _looks_bytes(source_raw):
        out.append("the materials and textures you changed")
    if plan is not None:
        from . import actions
        odd = [t for act, t in plan.encode if act.get(actions.KEY_CLIP) is None or str(act.get(actions.KEY_CLIP)) != t]
        odd += [str(c) for c in plan.drop]
        if odd:
            out.append("the animations you renamed, added or deleted (" + ", ".join(repr(n) for n in sorted(set(odd))[:3])
                       + (", ..." if len(set(odd)) > 3 else "") + ")")
    return out


def in_place_refusal(geo, plan, source_raw: bytes, data: bytes = None):
    """The plain reason an export of ``geo`` must not be written onto the model's own source file, or None when it can be."""
    what = differences(geo, plan, source_raw, data)
    if not what:
        return None
    return (f"it is this model's own source file, and {_join(what)} would be applied a second time by the next export "
            f"(they are changes against the file, and the file would already hold them). Export under another name, or use "
            f"Export Entity Folder, then open the new file")


def _join(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def guard_write(obj, out_path: str, data: bytes, geo, plan) -> bool:
    """Check a write of ``data`` (the final bytes of the export) to ``out_path`` before it happens. Returns whether ``out_path`` is the model's source file;
    raises :class:`InPlaceRefused` (the plain reason, starting "Export to <file> refused: ") when that write would be unsafe."""
    src = obj.get("bz_source", "")
    path = _source_file(src) if src else None
    if path is None or not same_file(out_path, path):
        return False
    with open(path, "rb") as f:
        source_raw = f.read()
    if bytes(data) == source_raw:
        return True                                                       # nothing changes: the write is a no-op
    why = in_place_refusal(geo, plan, source_raw, data)
    if why:
        raise InPlaceRefused(f"Export to {os.path.basename(path)} refused: {why}.")
    return True


def after_write(obj, out_path: str, arm=None, plan=None) -> bool:
    """After a successful write onto the model's source file: make the session match the file. The file's new SHA-1 goes on every object
    of the model, and an edited Action that was written under its own clip name takes the hash of the keys just written (so the next export
    keeps that clip instead of encoding it again). Returns whether the session was re-stamped (False when ``out_path`` is not the source)."""
    src = obj.get("bz_source", "")
    path = _source_file(src) if src else None
    if path is None or not same_file(out_path, path):
        return False
    stamp_source(src, path)
    if arm is not None and plan is not None:
        from . import actions, rig
        canon = rig.rename_canon(arm)
        for act, target in plan.encode:
            if act.get(actions.KEY_CLIP) is not None and str(act.get(actions.KEY_CLIP)) == target:
                act[actions.KEY_HASH] = actions.action_hash(act, canon)
    return True


# ------------------------------------------------------------------------------------------------------------------ clips renamed or deleted: the blueprint follows
def clip_renames(plan):
    """``({source clip name: new name}, [deleted clip names])`` of a ``ClipPlan``: an Action written under another name than the clip it came
    from is a rename -- unless an Action still writes that source clip under its own name (a duplicated Action carries the original's
    stamp, but the original is still there and still plays: the copy is a new clip, not a rename); a clip no Action writes any more is
    deleted. All Actions of a swap (A to B and B to A) are renames: none of them writes the clip it came from under its own name."""
    from . import actions
    kept = {str(c).lower() for c in plan.kept}
    for act, target in plan.encode:
        if act.get(actions.KEY_CLIP) is not None and str(act.get(actions.KEY_CLIP)) == target:
            kept.add(target.lower())                              # an edited Action that still writes its own clip
    ren = {}
    for act, target in plan.encode:
        old = act.get(actions.KEY_CLIP)
        if old is None or str(old) == target or str(old).lower() in kept:
            continue
        ren.setdefault(str(old), target)                         # two Actions claim one source clip: the first is the rename, the rest are new clips
    return ren, [str(c) for c in plan.drop if str(c) not in ren]


def follow_clips_in_blueprint(ebp_bytes: bytes, plan):
    """``(the blueprint bytes, notes)``: every motion state and event that names a renamed clip points at its new name (ICParse
    ``ebp.clip_refs``); those naming a deleted clip are listed (they play nothing now). The same bytes when no clip was renamed or deleted.

    Game rule: the controller finds a clip by name ignoring case (``AnimList::FindAnim`` AnimList.cpp:113-122) and a motion node whose name
    finds none plays nothing (``MotionTree::SetAnimList`` MotionTree.cpp:283-288); an event belongs to the clip its ``anim`` names
    (``AnimEventList::AddEvent`` AnimEvent.cpp:621-640). A clip with a conventional name needs no state of its own (``MotionTree::Build``
    MotionTree.cpp:350-790 makes one), so renaming or deleting such a clip is reported although no blueprint field names it."""
    if plan is None or not ebp_bytes:
        return ebp_bytes, []
    ren, gone = clip_renames(plan)
    if not ren and not gone:
        return ebp_bytes, []
    from ICParse.ebp import clip_refs as R, parser as P
    e = P.read(ebp_bytes)
    auto = R.auto_only_clips(e, list(ren) + list(gone))      # before the rename: which clips only play through MotionTree::Build
    nodes, events, lost, unparsed = R.rename_clip_refs(e, ren, gone)
    notes = []
    if auto:
        notes.append("blueprint: the game makes motion states by itself from clips named like " + ", ".join(sorted(set(auto))[:4])
                     + (" ..." if len(set(auto)) > 4 else "") + " (build, add-on, damage, Idle, OAttack1, Fly, Soar and cut-scene clips); "
                     "no motion state of the blueprint names them, so the renamed or deleted clip takes that behaviour away")
    if nodes or events:
        notes.append(f"blueprint: {nodes} motion state(s) and {events} event(s) follow {len(ren)} renamed animation(s)")
    if lost:
        names = sorted(set(lost))
        notes.append(f"blueprint: {len(lost)} motion state(s) or event(s) name a deleted animation and now play nothing: "
                     + ", ".join(names[:4]) + (" ..." if len(names) > 4 else ""))
    if unparsed:
        notes.append(f"blueprint: {unparsed} motion state(s) could not be read exactly, so their animation names are left as they were")
    return (P.write(e) if (nodes or events) else ebp_bytes), notes


def clip_blueprint_notes(obj, plan) -> list:
    """For an export that writes only the ``.sgm``: what the source blueprint beside it holds that names a renamed or deleted clip, in words
    (Export Entity Folder writes the blueprint with them renamed)."""
    if plan is None:
        return []
    src = obj.get("bz_source", "")
    path = _source_file(src) if src else None
    if path is None:
        return []
    ebp = os.path.splitext(path)[0] + ".ebp"
    if not os.path.isfile(ebp):
        return []
    with open(ebp, "rb") as f:
        _new, notes = follow_clips_in_blueprint(f.read(), plan)
    return [n.replace("blueprint:", "the source blueprint (not written by this export; Export Entity Folder writes it):", 1) for n in notes]


def anyway_hint(err) -> str:
    """The sentence that tells where "Export anyway" is, when ``err`` is the load validation refusing the model (``scene.export_scene_object``)."""
    return (" Tick 'Export anyway' in the file window to write it regardless." if "would not load" in str(err) else "")
