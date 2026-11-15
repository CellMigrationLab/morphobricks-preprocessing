"""One decode per stored piece for all the channels of a window (#294, #304).

Channels that share stored pieces (TIFF samples stored interleaved, OME-Zarr chunks longer than
1 along C) are read together: each piece a window intersects is read once, whatever the number
of channels asked, and the window is bitwise the slice of the whole plane.
"""

import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping, read_plane
from morphobricks_preprocessing.windows import read_window, window_plan
from test_windows import WINDOWS, H, W, rgb, stored
from zarr_tree import write_ngff, zarr_major

# name: (writer, axes of the mapping, channel count)
LAYOUTS = {
    "tiles interleaved RGB uncompressed": (
        lambda p: rgb(p, stored((H, W, 3), "uint8"), tile=(16, 32)),
        "YXS",
        3,
    ),
    "tiles interleaved RGBA big-endian uint16 zlib": (
        lambda p: rgb(
            p,
            stored((H, W, 4), ">u2"),
            extrasamples=["unassalpha"],
            tile=(16, 16),
            compression="zlib",
            byteorder=">",
        ),
        "YXS",
        4,
    ),
    "tiles planar RGB zlib": (
        lambda p: rgb(p, stored((3, H, W), "uint8"), planarconfig="separate", tile=(16, 16)),
        "SYX",
        3,
    ),
    "tiles interleaved RGB, header naming X before Y": (
        lambda p: rgb(
            p,
            stored((W, H, 3), "uint8"),
            tile=(16, 16),
            compression="zlib",
            metadata={"axes": "XYS"},
        ),
        "XYS",
        3,
    ),
    "strips interleaved RGB zlib, one strip": (
        lambda p: rgb(p, stored((H, W, 3), "uint8"), rowsperstrip=H, compression="zlib"),
        "YXS",
        3,
    ),
    "OME-Zarr, chunks of 2 along C": (
        lambda p: write_ngff(p, stored((5, H, W)), "cyx", chunks=(2, 16, 24)),
        "CYX",
        5,
    ),
}


def selections(count):
    every = list(range(count))
    return [every, every[::-1], [count - 1, 0], every[1:] + every[:1]]


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_channels_read_together_are_the_slices_of_the_whole_planes(tmp_path, name):
    write, axes, count = LAYOUTS[name]
    path = write(tmp_path / ("a.zarr" if "Zarr" in name else "a.tif"))
    mapping = prefill_mapping(axes)
    planes = [read_plane(path, mapping, c=c).pixels for c in range(count)]
    for channels in selections(count):
        for bounds in WINDOWS:
            y0, y1, x0, x1 = bounds
            window = read_window(path, mapping, bounds=bounds, channels=channels)
            expected = np.stack([planes[c][y0:y1, x0:x1] for c in channels])
            assert window.dtype == expected.dtype and window.shape == expected.shape
            assert window.flags.c_contiguous
            assert window.tobytes() == expected.tobytes(), (name, channels, bounds)
            alone = [read_window(path, mapping, bounds=bounds, channels=[c]) for c in channels]
            assert np.concatenate(alone).tobytes() == window.tobytes()


def test_random_windows_of_a_tiled_rgb_image_match_the_whole_plane(tmp_path):
    data = stored((300, 260, 3), "uint8", seed=7)
    path = rgb(tmp_path / "a.tif", data, tile=(32, 48), compression="zlib")
    mapping = prefill_mapping("YXS")
    rng = np.random.default_rng(11)
    for _ in range(40):
        y0, x0 = int(rng.integers(0, 299)), int(rng.integers(0, 259))
        y1, x1 = int(rng.integers(y0 + 1, 301)), int(rng.integers(x0 + 1, 261))
        channels = [int(c) for c in rng.permutation(3)[: int(rng.integers(1, 4))]]
        window = read_window(path, mapping, bounds=(y0, y1, x0, x1), channels=channels)
        expected = np.moveaxis(data[y0:y1, x0:x1][..., channels], -1, 0)
        assert window.tobytes() == np.ascontiguousarray(expected).tobytes()


# Decode counts ----------------------------------------------------------------------------

SIDE, TILE = 1024, 128
BOUNDS = (100, 420, 250, 700)  # a 320 x 450 window: rows 0-3, columns 1-5 of 128 px tiles
TILES = {(r, c) for r in range(0, 4) for c in range(1, 6)}


def tile_indices(across=SIDE // TILE, first=0):
    return sorted(first + r * across + c for r, c in TILES)


@pytest.mark.parametrize("compression", [None, "zlib"])
def test_an_interleaved_rgb_window_reads_each_tile_once(tmp_path, segment_reads, compression):
    data = stored((SIDE, SIDE, 3), "uint8", seed=5)
    path = rgb(tmp_path / "a.tif", data, tile=(TILE, TILE), compression=compression)
    mapping = prefill_mapping("YXS")
    plan = window_plan(path, mapping, bounds=BOUNDS, channels=[0, 1, 2])
    assert plan.pieces == len(TILES) == plan.reads
    window = read_window(path, mapping, bounds=BOUNDS, channels=[2, 0, 1])
    y0, y1, x0, x1 = BOUNDS
    np.testing.assert_array_equal(window, np.moveaxis(data[y0:y1, x0:x1][..., [2, 0, 1]], -1, 0))
    # One read per intersecting tile for the three samples (it was three before, #294).
    assert sorted(segment_reads) == tile_indices()


def test_an_interleaved_rgb_window_reads_each_strip_once(tmp_path, segment_reads):
    data = stored((SIDE, 300, 3), "uint8", seed=6)
    path = rgb(tmp_path / "a.tif", data, rowsperstrip=64, compression="zlib")
    mapping = prefill_mapping("YXS")
    window = read_window(path, mapping, bounds=(100, 300, 5, 290), channels=[0, 1, 2])
    np.testing.assert_array_equal(window, np.moveaxis(data[100:300, 5:290], -1, 0))
    assert sorted(segment_reads) == [1, 2, 3, 4]  # rows 64 to 319, once each
    plan = window_plan(path, mapping, bounds=(100, 300, 5, 290), channels=[0, 1, 2])
    assert plan.reads == plan.pieces == 4


def test_planar_samples_are_read_once_per_channel_from_their_own_tiles(tmp_path, segment_reads):
    data = stored((3, SIDE, SIDE), "uint8", seed=8)
    path = rgb(tmp_path / "a.tif", data, planarconfig="separate", tile=(TILE, TILE))
    mapping = prefill_mapping("SYX")
    window = read_window(path, mapping, bounds=BOUNDS, channels=[2, 0])
    y0, y1, x0, x1 = BOUNDS
    np.testing.assert_array_equal(window, data[[2, 0], y0:y1, x0:x1])
    per_sample = (SIDE // TILE) ** 2
    expected = tile_indices(first=2 * per_sample) + tile_indices(first=0)
    assert sorted(segment_reads) == sorted(expected)  # each tile of samples 2 and 0, once
    plan = window_plan(path, mapping, bounds=BOUNDS, channels=[2, 0])
    assert plan.reads == 2 * plan.pieces == len(segment_reads)


def test_the_estimate_counts_the_result_and_one_tile_for_all_samples(tmp_path):
    data = stored((SIDE, SIDE, 3), "uint8", seed=5)
    path = rgb(tmp_path / "a.tif", data, tile=(TILE, TILE), compression="zlib")
    with tifffile.TiffFile(path) as tif:
        encoded = max(tif.pages[0].databytecounts)
    plan = window_plan(path, prefill_mapping("YXS"), bounds=BOUNDS, channels=[0, 1, 2])
    pixels = (BOUNDS[1] - BOUNDS[0]) * (BOUNDS[3] - BOUNDS[2])
    assert plan.memory_estimate == 3 * pixels + TILE * TILE * 3 + encoded


def test_zarr_chunks_across_channels_are_read_once_per_window(tmp_path, monkeypatch):
    zarr = pytest.importorskip("zarr")
    if zarr_major() < 3:
        pytest.skip("the store spy is written for zarr 3's LocalStore")
    data = stored((4, 512, 512), seed=4)
    path = write_ngff(tmp_path / "a.zarr", data, "cyx", chunks=(2, 64, 64))
    keys = []
    original = zarr.storage.LocalStore.get

    async def get(self, key, *args, **kwargs):
        keys.append(key)
        return await original(self, key, *args, **kwargs)

    monkeypatch.setattr(zarr.storage.LocalStore, "get", get)
    mapping = prefill_mapping("CYX")
    bounds = (100, 140, 60, 200)
    window = read_window(path, mapping, bounds=bounds, channels=[3, 0, 1])
    np.testing.assert_array_equal(window, data[[3, 0, 1], 100:140, 60:200])
    chunks = [key for key in keys if re.fullmatch(r"0/\d+\.\d+\.\d+", key)]
    tiles = [(r, c) for r in (1, 2) for c in (0, 1, 2, 3)]
    expected = [f"0/{g}.{r}.{c}" for g in (0, 1) for r, c in tiles]
    assert sorted(chunks) == sorted(expected)  # each chunk once: channels 0 and 1 share theirs
    plan = window_plan(path, mapping, bounds=bounds, channels=[3, 0, 1])
    assert (plan.pieces, plan.reads) == (len(tiles), len(expected))
