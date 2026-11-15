"""Exact windows of one plane (issue #179): bitwise slices of the whole plane, read by blocks."""

import re

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping, read_plane
from morphobricks_preprocessing import windows as windows_module
from morphobricks_preprocessing.planes import PlaneMemoryError, PlaneReadError
from morphobricks_preprocessing.windows import (
    WindowBoundsError,
    WindowChannelError,
    WindowLayoutError,
    WindowLevelError,
    WindowMemoryError,
    read_window,
    window_plan,
)
from tiff_tree import write_data
from zarr_tree import write_ngff, zarr_major

H, W = 50, 70
WINDOWS = [
    (0, H, 0, W),  # the whole plane
    (0, 1, 0, 1),  # top-left pixel
    (H - 1, H, W - 1, W),  # bottom-right pixel
    (0, 9, W - 13, W),  # top-right corner
    (H - 17, H, 0, 5),  # bottom-left corner
    (13, 37, 20, 61),  # inside, crossing piece boundaries
    (15, 16, 0, W),  # one row
    (0, H, 31, 32),  # one column
    (16, 32, 24, 48),  # aligned on 16 x 24 pieces
]


def stored(shape, dtype="uint16", seed=0):
    rng = np.random.default_rng(seed)
    native = np.dtype(dtype).newbyteorder("=")
    if native.kind == "f":
        return rng.normal(100, 30, shape).astype(dtype)
    info = np.iinfo(native)
    return rng.integers(info.min, info.max, shape, dtype=native, endpoint=True).astype(dtype)


def tiff(path, data, axes, **kwargs):
    return write_data(path, data, axes, **kwargs)


def rgb(path, data, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, data, photometric="rgb", **kwargs)
    return path


def tiled(data, axes, **kwargs):
    return lambda path: tiff(path, data, axes, tile=(16, 32), **kwargs)


# name: (writer, axes of the mapping, channel count, other coordinates)
LAYOUTS = {
    "tiled zlib, a page per channel": (
        tiled(stored((3, H, W)), "CYX", compression="zlib"),
        "CYX",
        3,
        {},
    ),
    "tiled uncompressed, big-endian float": (
        tiled(stored((2, H, W), ">f4"), "CYX", byteorder=">"),
        "CYX",
        2,
        {},
    ),
    "strips uncompressed, T and C pages": (
        lambda p: tiff(p, stored((2, 3, H, W)), "TCYX", rowsperstrip=7),
        "TCYX",
        3,
        {"t": 1},
    ),
    "strips zlib, a page per channel": (
        lambda p: tiff(p, stored((2, H, W)), "CYX", rowsperstrip=11, compression="zlib"),
        "CYX",
        2,
        {},
    ),
    "strips planar RGB uncompressed": (
        lambda p: rgb(p, stored((3, H, W), "uint8"), planarconfig="separate", rowsperstrip=9),
        "SYX",
        3,
        {},
    ),
    "strips planar RGB zlib": (
        lambda p: rgb(p, stored((3, H, W), "uint8"), planarconfig="separate", compression="zlib"),
        "SYX",
        3,
        {},
    ),
    "strips interleaved RGB uncompressed": (
        lambda p: rgb(p, stored((H, W, 3), "uint8"), rowsperstrip=5),
        "YXS",
        3,
        {},
    ),
    "strips interleaved RGB zlib": (
        lambda p: rgb(p, stored((H, W, 3), "uint8"), rowsperstrip=13, compression="zlib"),
        "YXS",
        3,
        {},
    ),
    "tiles interleaved RGB zlib": (
        lambda p: rgb(p, stored((H, W, 3), "uint8"), tile=(16, 16), compression="zlib"),
        "YXS",
        3,
        {},
    ),
    "one compressed strip, no channel axis": (
        lambda p: tiff(p, stored((H, W)), "YX", compression="zlib", rowsperstrip=H),
        "YX",
        1,
        {},
    ),
    "header naming X before Y": (
        lambda p: tiff(p, stored((2, W, H)), "CXY", tile=(16, 16)),
        "CXY",
        2,
        {},
    ),
    "OME-Zarr, one chunk": (
        lambda p: write_ngff(p, stored((3, H, W)), "cyx"),
        "CYX",
        3,
        {},
    ),
    "OME-Zarr, 16 x 24 chunks": (
        lambda p: write_ngff(p, stored((3, H, W)), "cyx", chunks=(1, 16, 24)),
        "CYX",
        3,
        {},
    ),
    "OME-Zarr, chunks across channels and Z": (
        lambda p: write_ngff(p, stored((2, 3, H, W)), "zcyx", chunks=(2, 2, 16, 24)),
        "ZCYX",
        3,
        {"z": 1},
    ),
    "OME-Zarr 0.5, 16 x 24 chunks": (
        lambda p: write_ngff(p, stored((2, H, W)), "cyx", version="0.5", chunks=(1, 16, 24)),
        "CYX",
        2,
        {},
    ),
}


def selections(count):
    every = list(range(count))
    return [[0], every, every[::-1], every[1:] + every[:1]] if count > 1 else [[0]]


def whole(path, mapping, channel, coordinates):
    c = None if mapping.index_of("C") is None else channel
    return read_plane(path, mapping, c=c, **coordinates).pixels


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_a_window_is_the_same_slice_of_the_whole_plane_bit_for_bit(tmp_path, name):
    write, axes, count, coordinates = LAYOUTS[name]
    path = write(tmp_path / ("a.zarr" if "Zarr" in name else "a.tif"))
    mapping = prefill_mapping(axes)
    planes = [whole(path, mapping, c, coordinates) for c in range(count)]
    for channels in selections(count):
        for bounds in WINDOWS:
            y0, y1, x0, x1 = bounds
            window = read_window(path, mapping, bounds=bounds, channels=channels, **coordinates)
            expected = np.stack([planes[c][y0:y1, x0:x1] for c in channels])
            assert window.dtype == expected.dtype and window.shape == expected.shape
            assert window.tobytes() == expected.tobytes(), (name, channels, bounds)


def test_the_plane_is_chosen_by_t_and_z(tmp_path):
    data = stored((2, 3, H, W))
    path = tiff(tmp_path / "a.tif", data, "TCYX", tile=(16, 16))
    mapping = prefill_mapping("TCYX")
    window = read_window(path, mapping, bounds=(3, 30, 4, 40), channels=[2, 0], t=1)
    np.testing.assert_array_equal(window, data[1, [2, 0], 3:30, 4:40])
    with pytest.raises(IndexError):
        read_window(path, mapping, bounds=(3, 30, 4, 40), channels=[0], t=2)
    with pytest.raises(IndexError):
        read_window(path, mapping, bounds=(3, 30, 4, 40), channels=[0], z=1)


@pytest.fixture
def small(tmp_path):
    return tiff(tmp_path / "a.tif", stored((2, H, W)), "CYX", tile=(16, 16)), prefill_mapping("CYX")


@pytest.mark.parametrize(
    "bounds",
    [
        (-1, 10, 0, 10),  # outside, above
        (0, H + 1, 0, 10),  # outside, below
        (0, 10, -3, 5),  # outside, left
        (0, 10, 60, W + 1),  # outside, right
        (5, 5, 0, 10),  # empty
        (0, 10, 7, 3),  # reversed
        (0.0, 10, 0, 10),  # not a whole number
        (0, 10.5, 0, 10),
        (np.float64(0), 10, 0, 10),
        (True, 10, 0, 10),
        (0, 10, 0),  # three numbers
        "0:10, 0:10",
        None,
    ],
)
def test_bounds_outside_empty_or_not_whole_are_refused(small, bounds):
    path, mapping = small
    with pytest.raises(WindowBoundsError):
        read_window(path, mapping, bounds=bounds, channels=[0])
    with pytest.raises(WindowBoundsError):
        window_plan(path, mapping, bounds=bounds, channels=[0])


def test_numpy_integer_bounds_are_whole_numbers(small):
    path, mapping = small
    bounds = tuple(np.int64(v) for v in (1, 9, 2, 20))
    assert read_window(path, mapping, bounds=bounds, channels=[1]).shape == (1, 8, 18)


@pytest.mark.parametrize("level", [1, -1, 2, True, 0.0, "0", None])
def test_only_level_0_is_read(small, level):
    path, mapping = small
    with pytest.raises(WindowLevelError, match="level 0"):
        read_window(path, mapping, bounds=(0, 4, 0, 4), channels=[0], level=level)


@pytest.mark.parametrize("channels", [[], [0, 0], [2], [-1], [True], [0.0], "0", 0, None])
def test_channels_are_distinct_c_indices(small, channels):
    path, mapping = small
    with pytest.raises(WindowChannelError):
        read_window(path, mapping, bounds=(0, 4, 0, 4), channels=channels)


def test_pages_that_cannot_be_read_in_parts_are_refused(small, monkeypatch):
    # tifffile cannot write a subsampled or NDPI page without imagecodecs; the block reader's
    # answer for them (no way to split the page) is what the window reader refuses.
    path, mapping = small
    monkeypatch.setattr(windows_module, "_tiff_blocking", lambda *args: None)
    with pytest.raises(WindowLayoutError, match="cannot be read in parts"):
        read_window(path, mapping, bounds=(0, 4, 0, 4), channels=[0])


def test_a_file_that_is_not_an_image_is_refused(tmp_path):
    path = tmp_path / "a.tif"
    path.write_bytes(b"not an image")
    with pytest.raises(PlaneReadError):
        read_window(path, prefill_mapping("YX"), bounds=(0, 1, 0, 1), channels=[0])


def test_one_compressed_strip_is_decoded_whole_and_its_cost_is_stated(tmp_path):
    data = stored((H, W))
    path = tiff(tmp_path / "a.tif", data, "YX", compression="zlib", rowsperstrip=H)
    mapping = prefill_mapping("YX")
    plan = window_plan(path, mapping, bounds=(0, 2, 0, 2), channels=[0])
    assert plan.pieces == 1 and plan.memory_estimate > data.nbytes  # the whole strip
    with pytest.raises(WindowMemoryError, match="more than the limit"):
        read_window(path, mapping, bounds=(0, 2, 0, 2), channels=[0], memory_limit=data.nbytes)
    window = read_window(path, mapping, bounds=(0, 2, 0, 2), channels=[0])
    np.testing.assert_array_equal(window[0], data[:2, :2])


# Bounded memory ------------------------------------------------------------------------

SIDE, TILE = 4096, 256
LIMIT = 8 * 2**20  # far below one 32 MiB plane


@pytest.fixture(scope="module")
def large():
    return stored((SIDE, SIDE), seed=3)


def intersecting(bounds, rows=SIDE, chunk=TILE):
    """Return the (row, column) of every piece a window intersects."""
    y0, y1, x0, x1 = bounds
    return {
        (r, c)
        for r in range(y0 // chunk, -(-y1 // chunk))
        for c in range(x0 // chunk, -(-x1 // chunk))
        if r * chunk < rows
    }


@pytest.mark.parametrize("compression", [None, "zlib"])
def test_a_large_tiled_tiff_window_reads_only_its_tiles(
    tmp_path, large, segment_reads, compression
):
    path = tiff(tmp_path / "a.tif", large, "YX", tile=(TILE, TILE), compression=compression)
    mapping = prefill_mapping("YX")
    with pytest.raises(PlaneMemoryError):
        read_plane(path, mapping, memory_limit=LIMIT)  # the whole plane is over the limit
    bounds = (1000, 1300, 2000, 2600)
    plan = window_plan(path, mapping, bounds=bounds, channels=[0], memory_limit=LIMIT)
    assert plan.pieces == 12 and plan.memory_estimate <= LIMIT
    window = read_window(path, mapping, bounds=bounds, channels=[0], memory_limit=LIMIT)
    np.testing.assert_array_equal(window[0], large[1000:1300, 2000:2600])
    across = SIDE // TILE
    assert sorted(segment_reads) == sorted(r * across + c for r, c in intersecting(bounds))
    with pytest.raises(WindowMemoryError):
        read_window(path, mapping, bounds=(0, SIDE, 0, SIDE), channels=[0], memory_limit=LIMIT)


def test_a_large_striped_tiff_window_reads_only_its_strips(tmp_path, large, segment_reads):
    path = tiff(tmp_path / "a.tif", large, "YX", rowsperstrip=64, compression="zlib")
    bounds = (1000, 1100, 5, 4000)
    window = read_window(
        path, prefill_mapping("YX"), bounds=bounds, channels=[0], memory_limit=LIMIT
    )
    np.testing.assert_array_equal(window[0], large[1000:1100, 5:4000])
    assert sorted(segment_reads) == [15, 16, 17]  # rows 960 to 1151


def chunk_files(array_folder):
    """Return {chunk grid position: file} for a zarr 2 or 3 array folder."""
    found = {}
    for file in array_folder.rglob("*"):
        parts = file.relative_to(array_folder).parts
        if file.is_dir() or parts[-1] in {".zarray", ".zattrs", "zarr.json"}:
            continue
        parts = parts[1:] if parts[0] == "c" else parts
        if len(parts) == 1:
            parts = parts[0].split(".")
        found[tuple(int(p) for p in parts)] = file
    return found


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_large_ome_zarr_window_decodes_only_its_chunks(tmp_path, large, version):
    if version == "0.5" and zarr_major() < 3:
        pytest.skip("OME-NGFF 0.5 needs zarr 3")
    path = write_ngff(
        tmp_path / "a.zarr", large[None], "cyx", version=version, chunks=(1, TILE, TILE)
    )
    files = chunk_files(path / "0")
    assert len(files) == (SIDE // TILE) ** 2
    bounds = (1000, 1300, 2000, 2600)
    needed = {(0, r, c) for r, c in intersecting(bounds)}
    for position, file in files.items():
        if position not in needed:
            file.write_bytes(b"not a chunk")  # decoding it would fail
    mapping = prefill_mapping("CYX")
    plan = window_plan(path, mapping, bounds=bounds, channels=[0], memory_limit=LIMIT)
    assert plan.pieces == len(needed) == 12 and plan.memory_estimate <= LIMIT
    window = read_window(path, mapping, bounds=bounds, channels=[0], memory_limit=LIMIT)
    np.testing.assert_array_equal(window[0], large[1000:1300, 2000:2600])
    with pytest.raises(PlaneReadError):  # a window over a damaged chunk does read it
        read_window(path, mapping, bounds=(0, 10, 0, 10), channels=[0], memory_limit=LIMIT)


def test_zarr_store_reads_are_only_the_window_chunks(tmp_path, monkeypatch):
    zarr = pytest.importorskip("zarr")
    if zarr_major() < 3:
        pytest.skip("the store spy is written for zarr 3's LocalStore")
    data = stored((1, 512, 512), seed=4)
    path = write_ngff(tmp_path / "a.zarr", data, "cyx", chunks=(1, 64, 64))
    keys = []
    original = zarr.storage.LocalStore.get

    async def get(self, key, *args, **kwargs):
        keys.append(key)
        return await original(self, key, *args, **kwargs)

    monkeypatch.setattr(zarr.storage.LocalStore, "get", get)
    window = read_window(path, prefill_mapping("CYX"), bounds=(100, 140, 60, 200), channels=[0])
    np.testing.assert_array_equal(window[0], data[0, 100:140, 60:200])
    chunks = [key for key in keys if re.fullmatch(r"0/\d+\.\d+\.\d+", key)]
    expected = {f"0/0.{r}.{c}" for r, c in intersecting((100, 140, 60, 200), 512, 64)}
    assert sorted(chunks) == sorted(expected)  # each intersecting chunk once, no other
