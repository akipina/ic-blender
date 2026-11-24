"""The overlays of the map painting tools (the Mission Editor's Overlay menu), in a headless Blender:

    blender --background --factory-startup --python blender_native/tests/so_map_overlay_test.py     (or: run_all.py -k so_map_overlay)

Every overlay kind is drawn over the terrain of 2p_faceoff from the map as it would be saved, its picture has the size and the marked
cells the format module (ICParse.sgb.overlays) gives, a painted passability cell shows in the next land overlay, hiding removes the
object and the picture, none of it is saved (the map still saves byte for byte), and the precise map of the big bog is timed.
The same checks run inside so_map_paint_test.py; this file runs them alone so the overlays can be tried without the rest.
"""
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _so_common as C  # noqa: E402

import bpy  # noqa: E402

sys.path.insert(0, C.REPO)


def main():
    game_dir = C.install_root()
    if not game_dir:
        C.skip("no game install found: the overlay test needs the game's maps")
        return
    C.addon()
    import so_map_paint_test as T
    with C.install_guard(game_dir):
        T.test_overlays(game_dir)
        top = T.import_map(T.FACEOFF)
        raw = T.game_map(game_dir, T.FACEOFF).read()
        assert "FINISHED" in bpy.ops.bz.map_overlay_show(kind="cant_build")
        out, _ = T.save(top, "shown")
        assert out == raw, "a map with an overlay showing does not save byte for byte"
    print("SO_MAP_OVERLAY_PASS", flush=True)


if __name__ == "__main__":
    C.run_test("map_overlay", main)
