"""Pure helpers of the start lane (no bpy, so the tests and the validator can use them): where a project lives, what its
blueprint wires, whether a relinked source file is the one the object was built on.

* clip wiring: ``ICParse.sgm.creature.clips`` says which clip names the game plays; :func:`clip_lines` applies it to a creature and the ``.ebp`` beside its
  ``.sgm`` (the donor blueprint a project started from a stock animal carries).
* project folder: work is never written into the temp folder. :func:`project_dir` is the folder of the saved ``.blend``, else
  a folder of Blender's user data; :func:`unique_path` never reuses a name.
* relinking a source: :func:`relink_verdict`.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence, Tuple


def _mods():
    """core and store, as a package member (in Blender) or as top-level modules (the pure tests put this folder on sys.path)."""
    try:
        from . import core, store
    except ImportError:
        import core
        import store
    return core, store


# ---- clips the game will play -----------------------------------------------------------------------------------------
def ebp_beside(source_path: str) -> Optional[str]:
    """The blueprint beside a creature source: the ``.ebp`` of the same base name, or the source itself when it is a ``.ebp``."""
    if not source_path:
        return None
    if source_path.lower().endswith(".ebp"):
        return source_path if os.path.isfile(source_path) else None
    p = os.path.splitext(source_path)[0] + ".ebp"
    return p if os.path.isfile(p) else None


def blueprint_wiring(source_path: str):
    """``(refs, nodes)`` of the blueprint beside ``source_path``: ``refs`` the clip names its motion nodes play (stripped), ``nodes``
    ``{node name: clip}`` of every named node (``MotionTree::Build`` adds a node only when the tree has none of that name). None when
    there is no blueprint (or it cannot be read: a project made from nothing has no blueprint yet; the export builds its tree from the
    clip names)."""
    p = ebp_beside(source_path)
    if p is None:
        return None
    try:
        from ICParse import ebp as EBP
        bp = EBP.read(open(p, "rb").read())
        refs = [r.strip() for r in bp.animation_refs()]
        nodes = {}
        for mn in bp.motion_nodes:
            st = mn.state.strip()
            if st and st.lower() not in {k.lower() for k in nodes}:
                nodes[st] = mn.anim.strip()
        return refs, nodes
    except Exception:                                                     # noqa: BLE001 -- an unreadable blueprint is not a clip problem
        return None


def blueprint_refs(source_path: str) -> Optional[List[str]]:
    """The clip names the blueprint beside ``source_path`` wires to motion nodes (stripped), or None when there is none."""
    w = blueprint_wiring(source_path)
    return None if w is None else w[0]


def clip_lines(model, source_path: str = "") -> List[str]:
    """``WARN`` lines about clips the game will not play (``clips.clip_warnings``: the gates of MotionTree::Build, the blueprint's
    own nodes, a trailing ``.max`` ignored as the loader does), and about nodes of the blueprint beside the source whose clip the
    creature no longer has (the node then plays nothing)."""
    from ICParse.sgm.creature import clips
    names = [a.name for a in model.animations]
    wiring = blueprint_wiring(source_path) if source_path else None
    refs, nodes = wiring if wiring else (None, None)
    out = clips.clip_warnings(names, refs, nodes)
    if refs:
        have = {clips.clip_key(n) for n in names if not clips.is_skipped_by_loader(n)}
        missing = sorted({clips.clip_key(r) for r in refs if clips.clip_key(r) not in have and not r.lower().startswith("proc:")})
        shown = {clips.clip_key(r): r for r in refs}
        for k in missing:
            out.append(f"WARN the blueprint {os.path.basename(ebp_beside(source_path))} wires a motion node to clip '{shown[k]}', but "
                       "this creature has no clip of that name (renamed or deleted?): "
                       "that node plays nothing. Rename the clip back, or give the creature a clip of that name")
    return out


# ---- where a project lives ----------------------------------------------------------------------------------------------
def project_dir(blend_path: str, user_dir: str) -> str:
    """The folder work goes to: beside the saved ``.blend``; else ``user_dir`` (Blender's user data ``pyic/projects``), created.
    Never the temp folder: a reboot or a cleanup would take the work with it."""
    if blend_path:
        d = os.path.dirname(os.path.abspath(blend_path))
        if os.path.isdir(d):
            return d
    if not user_dir:
        raise OSError("no folder to keep the file in: save the .blend first")
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def safe_stem(text: str, fallback: str = "creature") -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", (text or "").strip()).strip("_") or fallback


def unique_path(folder: str, stem: str, ext: str) -> str:
    """``folder/stem.ext``, else ``folder/stem_2.ext`` ...: the first name nothing holds."""
    p = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(folder, f"{stem}_{n}{ext}")
        n += 1
    return p


# ---- stock copies ----------------------------------------------------------------------------------------------------------
def in_dirs(path: str, dirs: Sequence[str]) -> Optional[str]:
    """The folder of ``dirs`` that ``path`` lies directly in (the extracted originals are one flat folder), else None."""
    d = os.path.normcase(os.path.realpath(os.path.dirname(os.path.abspath(path))))
    for s in dirs:
        if s and d == os.path.normcase(os.path.realpath(s)):
            return s
    return None


def stock_copy_refusal(path: str, dirs: Sequence[str]) -> Optional[str]:
    """Why a write to ``path`` must not happen silently: it is one of the extracted stock creatures (a combine donor and a
    starting point for others), or None."""
    where = in_dirs(path, dirs)
    if where and os.path.splitext(path)[1].lower() in (".sgm", ".ebp", ".lua", ".lsc"):
        return (f"{os.path.basename(path)} is one of the extracted stock creatures in {where}: the combiner and New Project read "
                "it as the game's own animal, so writing over it would change them all. Export into your project folder instead")
    return None


# ---- relinking a source ---------------------------------------------------------------------------------------------------------
def relink_verdict(want_sha: Optional[str], want_nverts: int, want_npatch: int, new_path: str) -> Tuple[str, str]:
    """(``"same"`` | ``"changed"`` | ``"refuse"``, plain words) for pointing a creature at ``new_path`` as its source.

    ``same``: the file is byte-identical to the one imported (SHA-1). ``changed``: another file with the same control-point and
    patch counts, which the object's tables can still index -- relinking re-stamps the object on it, and the export builds on
    that file's skeleton, materials and clips. ``refuse``: different counts (the tables would point into the wrong file), or
    the file cannot be read as a creature."""
    core, store = _mods()
    try:
        src = core.resolve_source(new_path)
        model = core.read_source(src)
    except Exception as e:                                                  # noqa: BLE001
        return "refuse", f"cannot read {os.path.basename(new_path)} as a creature: {type(e).__name__}: {e}"
    if not len(model.vertices) or not len(model.patches):
        return "refuse", f"{os.path.basename(new_path)} holds no creature (no control points or patches)"
    if core.is_scene_model(model):
        return "refuse", f"{os.path.basename(new_path)} is a scene model (gatherer, structure or prop), not a creature"
    if want_sha is not None:
        try:
            if store.file_sha1(src)[1] == str(want_sha):
                return "same", "the file is the one this creature was imported from"
        except OSError as e:
            return "refuse", f"cannot read {new_path}: {e}"
    nv, npch = len(model.vertices), len(model.patches)
    if (nv, npch) != (int(want_nverts), int(want_npatch)):
        return "refuse", (f"{os.path.basename(new_path)} has {nv} control points and {npch} patches; this creature was built on a "
                          f"file with {int(want_nverts)} and {int(want_npatch)}, so its tables would point into the wrong places")
    return "changed", (f"{os.path.basename(new_path)} is not byte-identical to the file you imported (same control points and "
                       "patches): the export will build on this file's skeleton, materials and clips")
