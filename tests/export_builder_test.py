"""The one export builder and the pure guard rails (no Blender).
    python blender_native/tests/export_builder_test.py

1. core.pool_error: every way a native topology tool breaks the pool gets its own first clause;
2. core.skeleton_changes: rename / rename+move / add / delete / re-parent / case-only;
3. Extrude Patch orientation: extruding every open side of a 2x2 sheet makes no edge with three users and no
   same-direction seam; pick_extrude_side refuses shared sides, nothing selected and ambiguous selections;
4. ICParse encode_texture_edit keeps every untouched texel (palette, indices, lower mips), a painted block reaches the
   lower mips, an exactly reused or lower-mip-only palette entry is never recycled; core.skin_owners adopts a rigid
   vertex-group reassignment and refuses unskinned / blended points;
5. build_export on a GROWN snail with every record edited at once (extrude + ring + bone + texture + rest pose +
   clips): all records are in the model, rings are remapped through o2n, the world shape is kept under the rest
   edit, untouched points keep their source bytes, and export_summary names exactly what changed.
"""
import os
import sys
sys.dont_write_bytecode = True   # never write __pycache__ into the repo tree
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.environ.get("PYIC_REPO") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
from ICParse.sgm import parser as SP  # noqa: E402
from ICParse.codecs import reconstruct as R  # noqa: E402
from ICParse.codecs import texture as ICtex  # noqa: E402
from ICParse import validate as ICval  # noqa: E402

SRC = os.path.join(REPO, "Assets", "stock", "snail.sgm")


def pool_cases():
    pt = np.arange(32).reshape(2, 16)
    n = 40
    P = core.pool_error
    assert P(np.arange(n), n, pt) is None
    assert P(np.arange(n), n, pt, n_vert_owner=n, per_patch={"UV": 2, "limb": None}) is None   # absent is not an edit
    cases = {
        "delete": (np.delete(np.arange(n), 7), "1 point removed"),
        "merge": (np.delete(np.arange(n), [3, 4, 5]), "3 points removed"),
        "duplicate": (np.append(np.arange(n), 11), "1 point added"),
        "new vertex": (np.append(np.arange(n), 0), "1 point added"),
        "delete one, duplicate another": (np.append(np.delete(np.arange(n), 4), 9), "points removed and others added"),
        "sort": (np.random.default_rng(3).permutation(n), "points reordered"),
    }
    for what, (ids, clause) in cases.items():
        msg = P(ids, n, pt)
        assert msg and clause in msg and "control-point pool" in msg and "Undo" in msg, (what, msg)
    msg = P(np.arange(20), 20, pt)
    assert msg and "indexes control point 31" in msg and "Undo" in msg, msg
    assert "bone owners have 39" in P(np.arange(n), n, pt, n_vert_owner=39)
    assert "UV table has 3 rows for 2 patches" in P(np.arange(n), n, pt, per_patch={"UV": 3})
    print("POOL_ERROR_OK", len(cases) + 4, "cases")


def skeleton_cases():
    snap = [("pelvis", ""), ("spine1", "pelvis"), ("neck1", "spine1"), ("head", "neck1"), ("tail1", "pelvis")]
    rest = {n: np.eye(4) + i for i, (n, _p) in enumerate(snap)}
    S = core.skeleton_changes
    assert S(snap, snap, rest, rest) == []
    ren = [("neck1_x" if n == "neck1" else n, "neck1_x" if p == "neck1" else p) for n, p in snap]
    rest_ren = {("neck1_x" if n == "neck1" else n): m for n, m in rest.items()}
    assert S(snap, ren, rest, rest_ren) == ["bone 'neck1' was renamed to 'neck1_x'"], S(snap, ren, rest, rest_ren)
    moved = dict(rest_ren); moved["neck1_x"] = moved["neck1_x"] + 0.5
    got = S(snap, ren, rest, moved)
    assert "bone 'neck1' was deleted" in got and "bone 'neck1_x' was added" in got, got
    assert S(snap, snap + [("ExtraBone", "")], rest, rest) == ["bone 'ExtraBone' was added"]
    got = S(snap, [x for x in snap if x[0] != "head"], rest, rest)
    assert got == ["bone 'head' was deleted"], got
    rep = [(n, "spine1" if n == "tail1" else p) for n, p in snap]
    assert S(snap, rep, rest, rest) == ["bone 'tail1' was re-parented from 'pelvis' to 'spine1'"], S(snap, rep, rest, rest)
    case = [("Neck1" if n == "neck1" else n, "Neck1" if p == "neck1" else p) for n, p in snap]
    rest_case = {("Neck1" if n == "neck1" else n): m for n, m in rest.items()}
    assert S(snap, case, rest, rest_case) == ["bone 'neck1' was renamed to 'Neck1'"]
    # identity stamps (the source index on each Blender bone): a name swap is two renames, not two rest edits
    ids = list(range(len(snap)))
    assert S(snap, snap, rest, rest, current_ids=ids) == []
    swap = {"neck1": "tail1", "tail1": "neck1"}
    sw = [(swap.get(n, n), swap.get(p, p)) for n, p in snap]
    rest_sw = {swap.get(n, n): m for n, m in rest.items()}
    got = S(snap, sw, rest, rest_sw, current_ids=ids)
    assert got == ["bone 'neck1' was renamed to 'tail1'", "bone 'tail1' was renamed to 'neck1'"], got
    # a stamped rename with a rest edit is still a rename (not deleted + added); its children follow it
    got = S(snap, ren, rest, moved, current_ids=ids)
    assert got == ["bone 'neck1' was renamed to 'neck1_x'"], got
    # a duplicated bone carries the stamp: the original keeps its identity, the copy is added
    dup = snap + [("head.001", "neck1")]
    assert S(snap, dup, rest, rest, current_ids=ids + [3]) == ["bone 'head.001' was added"], S(snap, dup, rest, rest, current_ids=ids + [3])
    # deleted + re-added under the same name (no stamp): the same bone by name
    readd = [x for x in snap if x[0] != "head"] + [("head", "neck1")]
    assert S(snap, readd, rest, rest, current_ids=[0, 1, 2, 4, None]) == []
    # re-parented onto an added bone
    onto = snap[:4] + [("tail1", "Extra"), ("Extra", "pelvis")]
    got = S(snap, onto, rest, rest, current_ids=ids + [None])
    assert got == ["bone 'Extra' was added", "bone 'tail1' was re-parented from 'pelvis' to 'Extra'"], got
    print("SKELETON_CHANGES_OK 13 cases")


def _sheet(nx=2, ny=2):
    """An open nx x ny sheet of bicubic patches on a (3nx+1) x (3ny+1) control grid, stock orientation."""
    W = 3 * nx + 1
    grid = np.array([[x, 0.0, y] for y in range(3 * ny + 1) for x in range(W)], dtype=np.float64)
    patches = []
    for py in range(ny):
        for px in range(nx):
            patches.append([(3 * py + r) * W + 3 * px + c for r in range(4) for c in range(4)])
    return grid, np.array(patches, dtype=np.int64)


def extrude_cases():
    verts, patches = _sheet()
    users = core.side_users(patches)
    assert sum(1 for u in users.values() if len(u) == 1) == 8 and sum(1 for u in users.values() if len(u) == 2) == 4
    tried = 0
    for pi, side, key in core.patch_sides(patches):
        if len(users[key]) != 1:
            continue
        nv, cp = core.extrude_patch(verts, patches[pi], side)
        v2 = np.vstack([verts, nv]); p2 = np.vstack([patches, [cp]])
        u2 = core.side_users(p2)
        assert max(len(u) for u in u2.values()) == 2, (pi, side)
        assert u2[key] == [pi, len(patches)], (pi, side, u2[key])            # shares exactly the chosen side
        m = types.SimpleNamespace(patches=[types.SimpleNamespace(control_points=list(map(int, r))) for r in p2])
        assert ICval._same_direction_edges(m) == 0, (pi, side)
        # the new patch continues outward (no fold back over the source patch)
        c_src = verts[patches[pi]].mean(0); c_new = v2[cp].mean(0)
        side_mid = verts[[patches[pi][i] for i in core._SIDE_CPS[side]]].mean(0)
        assert np.dot(c_new - side_mid, side_mid - c_src) > 0, (pi, side)
        # the selection rule picks exactly this side from its two corners
        a, b = int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])
        assert core.pick_extrude_side(patches, [a, b]) == (pi, side), (pi, side, core.pick_extrude_side(patches, [a, b]))
        tried += 1
    assert tried == 8
    # a shared side is refused, nothing selected is refused with the open-side count, two open sides are ambiguous
    shared_key = next(k for k, u in users.items() if len(u) == 2)
    pi, side = next((p, s) for p, s, k in core.patch_sides(patches) if k == shared_key)
    a, b = int(patches[pi][core._SIDE_CPS[side][0]]), int(patches[pi][core._SIDE_CPS[side][3]])
    msg = core.pick_extrude_side(patches, [a, b])
    assert isinstance(msg, str) and "shared by patches" in msg and "three" in msg, msg
    msg = core.pick_extrude_side(patches, [])
    assert isinstance(msg, str) and "8 open sides" in msg, msg
    msg = core.pick_extrude_side(patches, patches[0][[0, 3, 12, 15]])
    assert isinstance(msg, str) and "2 open sides selected" in msg, msg
    print("EXTRUDE_ORIENTATION_OK", tried, "open sides")


def texture_cases(m):
    for tex in m.textures[:3]:
        rgba = bytearray(ICtex.decode_texture(tex, 0))
        same, k = ICtex.encode_texture_edit(tex, bytes(rgba), tex.width, tex.height)
        assert same is tex and k == 0
        px = np.frombuffer(bytes(rgba), np.uint8).reshape(-1, 4)
        j = next(i for i in range(1, len(px)) if not np.array_equal(px[i, :3], px[0, :3]))
        rgba[0:3] = bytes(px[j, :3])                                        # paint texel 0 an existing colour
        new, k = ICtex.encode_texture_edit(tex, bytes(rgba), tex.width, tex.height)
        d_new = np.frombuffer(ICtex.decode_texture(new, 0), np.uint8).reshape(-1, 4)
        d_src = np.frombuffer(ICtex.decode_texture(tex, 0), np.uint8).reshape(-1, 4)
        diff = np.nonzero(np.any(d_new != d_src, axis=1))[0]
        assert k == 1 and list(diff) == [0], (tex.name, k, diff[:5])
        assert new.palette == tex.palette and new.mips[0][1:] == tex.mips[0][1:]
        assert all(len(a) == len(b) for a, b in zip(new.mips, tex.mips)) and new.mip_count == tex.mip_count
        # full re-quantisation would move most of the image
        full = ICtex.encode_texture(tex.name, bytes(rgba), tex.width, tex.height)
        moved = int(np.any(np.frombuffer(ICtex.decode_texture(full, 0), np.uint8).reshape(-1, 4) != d_src, axis=1).sum())
        print(f"  texture {tex.name}: palette-preserving edit changes 1 texel (full re-encode would change {moved})")
    texture_palette_cases()
    print("TEXTURE_EDIT_OK")


def texture_palette_cases():
    """encode_texture_edit on a full 256-entry palette: a painted block reaches the lower mip; a colour reused exactly
    keeps its entry out of the recycling (texel 7 takes the colour of entry 5 while entry 5's only texel is repainted);
    an entry only a LOWER mip uses is never recycled (Squid6: untouched lower-mip texels keep their colour)."""
    from ICParse.sgm.creature.model import Texture
    rng = np.random.default_rng(1)
    pal = np.zeros((256, 4), np.uint8)
    cols = rng.permutation(256 ** 3)[:256]
    pal[:, 2], pal[:, 1], pal[:, 0] = cols >> 16, (cols >> 8) & 255, cols & 255          # B,G,R,A
    rgb = lambda e: (int(pal[e, 2]), int(pal[e, 1]), int(pal[e, 0]))                     # noqa: E731
    idx0 = np.arange(256, dtype=np.uint8).reshape(16, 16)                                # every entry: one texel
    tex = Texture(name="t", palette_entries=256, format_code=0, width=16, height=16, mip_count=2,
                  palette=pal.tobytes(), mips=[idx0.tobytes(), idx0[::2, ::2].copy().tobytes()])
    img = np.frombuffer(ICtex.decode_texture(tex, 0), np.uint8).reshape(16, 16, 4).copy()
    a = img.copy(); a[0:2, 0:2, :3] = rgb(200)
    new, _k = ICtex.encode_texture_edit(tex, a.tobytes(), 16, 16)
    m1 = np.frombuffer(ICtex.decode_texture(new, 1), np.uint8).reshape(8, 8, 4)
    assert tuple(int(x) for x in m1[0, 0, :3]) == rgb(200), "the painted block did not reach mip 1"
    assert np.array_equal(m1[1:, 1:], np.frombuffer(ICtex.decode_texture(tex, 1), np.uint8).reshape(8, 8, 4)[1:, 1:])
    # an 8x8 image whose lower mips use entries mip 0 does not, and a full palette
    lo = Texture(name="lo", palette_entries=256, format_code=0, width=16, height=16, mip_count=2, palette=pal.tobytes(),
                 mips=[(idx0 % 128).astype(np.uint8).tobytes(), (128 + np.arange(64, dtype=np.uint8)).reshape(8, 8).tobytes()])
    b = img.copy()
    b[0, 5, :3] = rgb(9); b[0, 7, :3] = rgb(5)
    palset = {rgb(e) for e in range(256)}
    b[0, 8, :3] = next(c for c in [(1, 2, 3), (4, 5, 6), (7, 8, 9)] if c not in palset)
    new2, _k = ICtex.encode_texture_edit(tex, b.tobytes(), 16, 16)
    d = np.frombuffer(ICtex.decode_texture(new2, 0), np.uint8).reshape(16, 16, 4)
    bad = np.argwhere(np.any(d[:, :, :3] != b[:, :, :3], axis=2))
    assert not len(bad), f"full palette: texels {bad.tolist()[:4]} decode to the wrong colour (a reused entry recycled)"
    src_lo = np.frombuffer(ICtex.decode_texture(lo, 0), np.uint8).reshape(16, 16, 4).copy()
    c = src_lo.copy()
    newc = [x for x in ((i, 255 - i, (7 * i) % 256) for i in range(256)) if x not in palset][:64]
    for k, colour in enumerate(newc):                   # 64 new colours in the top-left 8x8 block: they need slots
        c[k // 8, k % 8, :3] = colour
    new3, _k = ICtex.encode_texture_edit(lo, c.tobytes(), 16, 16)
    l_src = np.frombuffer(ICtex.decode_texture(lo, 1), np.uint8).reshape(8, 8, 4)
    l_new = np.frombuffer(ICtex.decode_texture(new3, 1), np.uint8).reshape(8, 8, 4)
    clean = np.ones((8, 8), bool); clean[:4, :4] = False
    moved = int(np.any(l_src[:, :, :3] != l_new[:, :, :3], axis=2)[clean].sum())
    assert moved == 0, f"{moved} untouched lower-mip texels changed colour (an entry only mip 1 uses was recycled)"
    squid = SP.read(os.path.join(REPO, "Assets", "stock", "Squid6.sgm")).textures[0]
    w, h = squid.width, squid.height
    sq = np.frombuffer(ICtex.decode_texture(squid, 0), np.uint8).reshape(h, w, 4).copy()
    spal = {tuple(int(x) for x in row[:3][::-1]) for row in np.frombuffer(squid.palette, np.uint8).reshape(-1, 4)}
    fresh = (x for x in ((i % 256, (i // 256) % 256, (i * 31) % 256) for i in range(1, 1 << 20)) if x not in spal)
    for y in range(64):
        for x in range(64):
            sq[y, x, :3] = next(fresh)
    new4, _k = ICtex.encode_texture_edit(squid, sq.tobytes(), w, h)
    bad = total = 0
    for lvl in range(1, squid.mip_count):
        A = np.frombuffer(ICtex.decode_texture(squid, lvl), np.uint8).reshape(max(1, h >> lvl), max(1, w >> lvl), 4)
        B = np.frombuffer(ICtex.decode_texture(new4, lvl), np.uint8).reshape(A.shape)
        ys = np.arange(A.shape[0]) * (h // A.shape[0]); xs = np.arange(A.shape[1]) * (w // A.shape[1])
        ok = ~((ys[:, None] < 64) & (xs[None, :] < 64))
        bad += int((np.any(A[:, :, :3] != B[:, :, :3], axis=2) & ok).sum()); total += int(ok.sum())
    assert bad == 0, f"Squid6: {bad} of {total} untouched lower-mip texels changed colour"
    print(f"  full palette: painted block in mip 1, exact reuse kept, lower-mip-only entries kept (Squid6: 0 of {total} "
          "untouched lower-mip texels changed)")


def skin_owner_cases():
    S = core.skin_owners
    stored = np.array([0, 0, 1, 1, -1])
    rigid = [[(0, 1.0)], [(0, 1.0)], [(1, 1.0)], [(1, 1.0)], []]
    owners, adopted, why = S(rigid, stored)
    assert why is None and adopted == [] and list(owners) == list(stored), (owners, adopted, why)
    moved = [[(0, 1.0)], [(1, 1.0)], [(1, 1.0)], [(1, 1.0)], []]                 # Ctrl+G: point 1 to bone 1
    owners, adopted, why = S(moved, stored)
    assert why is None and adopted == [1] and list(owners) == [0, 1, 1, 1, -1], (owners, adopted, why)
    owners, adopted, why = S([[(0, 1.0)], [], [(1, 1.0)], [(1, 1.0)], []], stored)
    assert owners is None and "1 in no bone's vertex group" in why and "points 1" in why, why
    owners, adopted, why = S([[(0, 0.5), (1, 0.5)], [(0, 1.0)], [(1, 0.4)], [(1, 1.0)], []], stored, ["pelvis", "spine"])
    assert owners is None and "2 with blended or partial weights (point 0: pelvis 0.50, spine 0.50)" in why, why
    assert S([[(0, 1.0), (1, 0.0)], [(0, 1.0)], [(1, 1.0)], [(1, 1.0)], []], stored)[2] is None   # a zero weight is none
    assert "cover 2 points" in S([[], []], stored)[2]
    print("SKIN_OWNERS_OK 6 cases")


def grown_records_case():
    d = core.load_creature(SRC)
    src = d.model
    owners = np.array(R.vertex_bone_owners(src), dtype=np.int64)
    plimb = core._patch_limb_of(src)
    ptype = np.array([p.type for p in src.patches], dtype=np.int64)
    # the snail is closed: drop its last patch to open four sides (indices of the others stay), then extrude one
    k = len(d.patches) - 1
    keep = [i for i in range(len(d.patches)) if i != k]
    patches = d.patches[keep]
    users = core.side_users(patches)
    pi, side, key = next((p, s, kk) for p, s, kk in core.patch_sides(patches) if len(users[kk]) == 1)
    nv, cp = core.extrude_patch(d.verts, patches[pi], side)
    verts = np.vstack([d.verts, nv])
    shared_owner = int(owners[int(patches[pi][core._SIDE_CPS[side][0]])])
    st = core.EditState(
        source_path=SRC, verts=verts, patches=np.vstack([patches, [cp]]),
        vert_owner=np.concatenate([owners, [shared_owner] * 12]),
        patch_limb=np.append(plimb[keep], plimb[keep][pi]), patch_uv=np.vstack([d.patch_uv[keep], [d.patch_uv[keep][pi]]]),
        patch_material=np.append(d.patch_mat[keep], d.patch_mat[keep][pi]),
        patch_type=np.append(ptype[keep], ptype[keep][pi]))

    # 5a. grown, no records: untouched points keep their source bytes; the wrapper is the same builder
    b0 = core.build_export(st)
    assert b0.grown and b0.reencoded == 12, (b0.grown, b0.reencoded)
    o2n = b0.o2n
    assert all(tuple(b0.model.vertices[o2n[i]]) == tuple(src.vertices[i]) for i in range(len(src.vertices)))
    wrap = core.export_grown(SRC, st.verts, st.patches, st.vert_owner, st.patch_material, st.patch_uv, st.patch_type,
                             st.patch_limb)
    assert SP.write(b0.model) == wrap, "export_grown is not the builder"
    s0 = core.export_summary(b0)
    assert s0.line.startswith("geometry rebuilt: +12 control points, +0 patches (12 points re-encoded"), s0.line
    assert [p.split(":")[0] for p in s0.parts] == ["geometry rebuilt"], s0.parts

    # 5b. every record at once
    from ICParse.sgm.creature.model import ring_corners, ring_pairs
    ring0 = ring_pairs(ring_corners(src.limb_connections[0].vertex_indices)[2:]
                       + ring_corners(src.limb_connections[0].vertex_indices)[:2])       # the head ring, started 2 on
    # the store's form: every ring its decoded corner loop (re-encoded at export), one kept as verbatim ints
    st.connections = [core.RingEdit(index=i, bone=c.bone_name, corners=ring_corners(c.vertex_indices))
                      for i, c in enumerate(src.limb_connections)]
    st.connections[1] = core.RingEdit(index=1, bone=src.limb_connections[1].bone_name,
                                      raw=list(src.limb_connections[1].vertex_indices))
    st.connections[0] = core.RingEdit(index=0, bone="pelvis",                                # rotated ring + new host
                                      corners=ring_corners(src.limb_connections[0].vertex_indices)[2:]
                                      + ring_corners(src.limb_connections[0].vertex_indices)[:2])
    assert st.connections[0].ints() == ring0 and ring0 != list(src.limb_connections[0].vertex_indices)
    bi = next(i for i, b in enumerate(src.bones) if b.name == "spine2")
    t = np.eye(4); t[:3, :4] = np.array(src.bones[bi].transform, dtype=np.float64).reshape(4, 3).T
    c, s = np.cos(np.radians(10)), np.sin(np.radians(10))
    rot = np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    t2 = t @ rot
    st.bone_rest = {bi: [float(x) for x in list(t2[:3, 0]) + list(t2[:3, 1]) + list(t2[:3, 2]) + list(t2[:3, 3])]}
    tex = src.textures[0]
    rgba = bytearray(ICtex.decode_texture(tex, 0))
    px = np.frombuffer(bytes(rgba), np.uint8).reshape(-1, 4)
    j = next(i for i in range(1, len(px)) if not np.array_equal(px[i, :3], px[0, :3]))
    rgba[0:3] = bytes(px[j, :3])
    st.textures = {0: (bytes(rgba), tex.width, tex.height)}
    b = core.build_export(st)
    m = b.model
    o2n = b.o2n
    assert m.limb_connections[0].bone_name == "pelvis"
    assert list(m.limb_connections[0].vertex_indices) == [int(o2n[i]) for i in ring0]
    assert all(list(m.limb_connections[i].vertex_indices) == [int(o2n[x]) for x in src.limb_connections[i].vertex_indices]
               for i in range(1, len(src.limb_connections)))
    assert not np.allclose(m.bones[bi].transform, src.bones[bi].transform, atol=1e-4)
    world_new = R.world_vertices(m)
    want = core.blender_to_sgm(verts)
    scale = float(np.abs(want).max())
    err = float(np.abs(world_new[o2n] - want).max())
    assert err < max(1e-4, 1e-5 * scale), err                   # the shape is kept under the new rest pose
    assert b.tex_changed == {0: 1}
    # clips: drop walk, add a renamed copy of idle1 (encoded clips are appended after the build)
    import copy
    new_clip = copy.deepcopy(next(a for a in src.animations if a.name == "idle1")); new_clip.name = "idle1_renamed"
    core.apply_clip_plan(m, [new_clip], ["walk"])
    kept = [a.name for a in src.animations if a.name != "walk"]
    summ = core.export_summary(b, kept=kept, written=["idle1_renamed"], dropped=["walk"])
    heads = [p.split(":")[0] for p in summ.parts]
    assert heads == ["geometry rebuilt", "rest pose", "rings", "textures", "clips"], summ.parts
    assert "rest pose: 1 bone (spine2)" in summ.line and "rings: 1 (Snail_Head ring+bone)" in summ.line, summ.line
    assert f"clips: {len(kept)} kept, 1 written, 1 dropped" in summ.line, summ.line
    assert any(w.startswith("rest pose of 1 bone(s) changed") for w in summ.warnings), summ.warnings
    # a planned clip missing from the model is an error, not a silent summary
    try:
        core.export_summary(b, kept=kept, written=["not_there"])
        raise AssertionError("a planned clip missing from the model was not caught")
    except ValueError:
        pass
    # the file loads the way the grown geometry allows: only the open sides the dropped patch left
    probs = [p for p in ICval.check_model(m) if not p.startswith("WARN")]
    assert all("watertight" in p for p in probs), probs
    print("GROWN_RECORDS_OK", summ.line)
    for w in summ.warnings:
        print("  WARN", w)


def fast_path_case():
    d = core.load_creature(SRC)
    src = d.model
    raw = open(SRC, "rb").read()
    full = core.EditState(SRC, d.verts, patches=d.patches, vert_owner=np.array(R.vertex_bone_owners(src)),
                          patch_limb=core._patch_limb_of(src), patch_uv=d.patch_uv, patch_material=d.patch_mat,
                          patch_type=np.array([p.type for p in src.patches]),
                          connections=[core.RingEdit(i, c.bone_name, raw=list(c.vertex_indices))
                                       for i, c in enumerate(src.limb_connections)])
    b = core.build_export(full)
    assert not b.grown and SP.write(b.model) == raw and core.export_summary(b).parts == []
    # a patch type / material override is change-detected like the rest
    full.patch_type = full.patch_type.copy(); full.patch_type[3] ^= 2
    b = core.build_export(full)
    assert core.export_summary(b).parts == ["patch materials/types: 1"], core.export_summary(b).parts
    # kept clips carry bounds from the source geometry: an export whose extent changed says so, a small move does not
    clip = src.animations[0].name
    grown_extent = core.build_export(core.EditState(SRC, d.verts * 1.5))
    warns = core.export_summary(grown_extent, kept=[clip]).warnings
    assert any("have bounds from the source geometry, whose extent changed" in w for w in warns), warns
    nudged = d.verts.copy(); nudged[len(nudged) // 2] += 1e-6
    assert not core.export_summary(core.build_export(core.EditState(SRC, nudged)), kept=[clip]).warnings
    # a host bone that is not in the skeleton is refused (the engine asserts on it)
    ghost = core.EditState(SRC, d.verts, connections=[core.RingEdit(0, "no_such_bone", raw=list(src.limb_connections[0].vertex_indices))]
                           + [core.RingEdit(i, c.bone_name, raw=list(c.vertex_indices))
                              for i, c in enumerate(src.limb_connections) if i])
    try:
        core.build_export(ghost)
        raise AssertionError("an unknown host bone was accepted")
    except ValueError as e:
        assert "host bone 'no_such_bone' is not in the skeleton" in str(e), e
    # a wrongly sized array is refused by name (never truncated)
    bad = core.EditState(SRC, d.verts, patch_uv=d.patch_uv[:-1])
    try:
        core.build_export(bad)
        raise AssertionError("a short UV table was accepted")
    except ValueError as e:
        assert "UVs" in str(e), e
    print("FAST_PATH_OK")


def main():
    pool_cases()
    skeleton_cases()
    extrude_cases()
    texture_cases(SP.read(SRC))
    skin_owner_cases()
    fast_path_case()
    grown_records_case()
    print("EXPORT_BUILDER_PASS")


if __name__ == "__main__":
    main()
