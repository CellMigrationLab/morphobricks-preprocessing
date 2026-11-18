"""Exact windows of one mask plane: bitwise slices of the whole read, labels as stored."""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing.masks import FOLDER, OME_ZARR, MaskFile, read_mask
from morphobricks_preprocessing.masks.header import _mask_header
from morphobricks_preprocessing.masks.window import mask_window_plan, read_mask_window
from morphobricks_preprocessing.planes import PlaneReadError
from morphobricks_preprocessing.windows import (
    WindowBoundsError,
    WindowLayoutError,
    WindowLevelError,
    WindowMemoryError,
)
from test_windows import WINDOWS, H, W, chunk_files, intersecting
from tiff_tree import write_data, write_plain
from zarr_tree import write_labels, write_ngff, zarr_major


def instances(shape, dtype, seed=0):
    """Labels 0 to a large value, so a relabelling or cast would show."""
    info = np.iinfo(dtype)
    top = min(int(info.max), 2**31 - 1)
    rng = np.random.default_rng(seed)
    return rng.integers(0, top, shape, dtype=np.int64, endpoint=True).astype(dtype)


def binary(shape, dtype, seed=0):
    return (np.random.default_rng(seed).random(shape) < 0.4).astype(dtype)


def parent_labels(shape, dtype, seed=0):
    """Groups 1 to 3 of separate squares, background 0."""
    out = np.zeros(shape, dtype)
    rng = np.random.default_rng(seed)
    for _ in range(12):
        y, x = rng.integers(0, H - 6), rng.integers(0, W - 6)
        out[..., y : y + 5, x : x + 5] = rng.integers(1, 4)
    return out


# name: (values, dtype)
REPRESENTATIONS = {
    "instances uint16": (instances, np.uint16),
    "instances int32": (instances, np.int32),
    "instances uint32": (instances, np.uint32),
    "binary uint8": (binary, np.uint8),
    "binary bool": (binary, np.bool_),
    "parent_labels uint8": (parent_labels, np.uint8),
}


def tiff(data, axes, **kwargs):
    return lambda folder: (write_data(folder / "m.tif", data, axes, **kwargs), None)


def labels(data, axes, version="0.4", **kwargs):
    def write(folder):
        image = np.zeros((1, H, W), np.uint8)
        path = write_ngff(folder / "a.zarr", image, "cyx", version=version)
        write_labels(path, {"cells": data}, axes, version=version, **kwargs)
        return path / "labels" / "cells", None

    return write


def plain(data, roles):
    return lambda folder: (write_plain(folder / "m.tif", data), roles)


# name: (writer of (values, dtype), the plane, its z and t)
LAYOUTS = {
    "tiled zlib, a page per time point": (
        lambda v: tiff(v((2, H, W)), "TYX", tile=(16, 16), compression="zlib"),
        {"t": 1},
    ),
    "tiled uncompressed, a page per depth slice": (
        lambda v: tiff(v((3, H, W)), "ZYX", tile=(16, 32)),
        {"z": 2},
    ),
    "strips uncompressed, 2-D": (lambda v: tiff(v((H, W)), "YX", rowsperstrip=7), {}),
    "strips zlib, T and Z pages": (
        lambda v: tiff(v((2, 3, H, W)), "TZYX", rowsperstrip=11, compression="zlib"),
        {"t": 1, "z": 2},
    ),
    "one compressed strip": (
        lambda v: tiff(v((H, W)), "YX", rowsperstrip=H, compression="zlib"),
        {},
    ),
    "strips uncompressed, big-endian": (
        lambda v: tiff(v((H, W)), "YX", rowsperstrip=9, byteorder=">"),
        {},
    ),
    "plain stack with stated TYX": (lambda v: plain(v((3, H, W)), "TYX"), {"t": 2}),
    "OME-Zarr 0.4 labels, one chunk": (lambda v: labels(v((2, H, W)), "tyx"), {"t": 1}),
    "OME-Zarr 0.4 labels, 16 x 24 chunks": (
        lambda v: labels(v((2, H, W)), "tyx", chunks=(1, 16, 24)),
        {"t": 0},
    ),
    "OME-Zarr 0.5 labels, chunks across T and Z": (
        lambda v: labels(v((2, 3, H, W)), "tzyx", version="0.5", chunks=(2, 2, 16, 24)),
        {"t": 1, "z": 1},
    ),
}


def mask_of(path, root, roles=None):
    """Return the MaskFile of a written mask, with its header, as RunInputs passes it."""
    zarr = path.parent.name == "labels"
    relative = path.relative_to(root).as_posix()
    mask = MaskFile("", "", relative, OME_ZARR if zarr else FOLDER)
    axes, shape, dtype = _mask_header(mask, None if zarr else root, root)
    return MaskFile("", "", relative, mask.source, shape, dtype, roles or axes, axes)


def written(tmp_path, layout, representation):
    write, coordinates = LAYOUTS[layout]
    values, dtype = REPRESENTATIONS[representation]
    path, roles = write(lambda shape: values(shape, dtype))(tmp_path)
    return mask_of(path, tmp_path, roles), coordinates


@pytest.mark.parametrize("representation", sorted(REPRESENTATIONS))
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_mask_window_is_the_same_slice_of_the_whole_read_bit_for_bit(
    tmp_path, layout, representation
):
    mask, coordinates = written(tmp_path, layout, representation)
    plane = read_mask(mask, tmp_path, tmp_path, **coordinates)
    assert plane.dtype.isnative
    for bounds in WINDOWS:
        y0, y1, x0, x1 = bounds
        window = read_mask_window(mask, tmp_path, tmp_path, bounds=bounds, **coordinates)
        expected = plane[y0:y1, x0:x1]
        assert window.dtype == expected.dtype and window.shape == expected.shape
        assert window.tobytes() == expected.tobytes(), (layout, representation, bounds)
        plan = mask_window_plan(mask, tmp_path, tmp_path, bounds=bounds)
        assert (plan.shape, plan.dtype, plan.channels) == (expected.shape, expected.dtype, ())


def test_the_stored_dtype_and_labels_are_kept(tmp_path):
    data = np.zeros((H, W), np.int32)
    data[3:9, 4:20] = 2**31 - 1  # the largest label, never wrapped or cast
    data[20:30, 5:6] = 70000
    mask = mask_of(write_data(tmp_path / "m.tif", data, "YX", tile=(16, 16)), tmp_path)
    window = read_mask_window(mask, tmp_path, tmp_path, bounds=(2, 31, 0, 21))
    assert window.dtype == np.int32
    np.testing.assert_array_equal(window, data[2:31, 0:21])
    assert set(np.unique(window)) == {0, 70000, 2**31 - 1}


@pytest.fixture
def small(tmp_path):
    data = instances((2, H, W), np.uint16)
    path = write_data(tmp_path / "m.tif", data, "TYX", tile=(16, 16))
    return mask_of(path, tmp_path), data


@pytest.mark.parametrize(
    "bounds",
    [(-1, 10, 0, 10), (0, H + 1, 0, 10), (0, 10, 60, W + 1), (5, 5, 0, 10), (0.0, 10, 0, 10)],
)
def test_bounds_are_refused_as_the_image_window_refuses_them(small, tmp_path, bounds):
    mask, _ = small
    with pytest.raises(WindowBoundsError):
        read_mask_window(mask, tmp_path, tmp_path, bounds=bounds, t=0)
    with pytest.raises(WindowBoundsError):
        mask_window_plan(mask, tmp_path, tmp_path, bounds=bounds)


@pytest.mark.parametrize("level", [1, True, "0", None])
def test_only_level_0_is_read(small, tmp_path, level):
    mask, _ = small
    with pytest.raises(WindowLevelError):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), t=0, level=level)
    with pytest.raises(WindowLevelError):
        mask_window_plan(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), level=level)


def test_the_plane_is_never_guessed(small, tmp_path):
    mask, data = small
    with pytest.raises(ValueError, match="say which one"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4))
    with pytest.raises(ValueError, match="no plane T=2"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), t=2)
    with pytest.raises(ValueError, match="no plane Z=1"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), t=0, z=1)
    window = read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), t=1, z=0)
    np.testing.assert_array_equal(window, data[1, :4, :4])


def test_labels_outside_the_int32_range_in_the_window_are_refused(tmp_path):
    data = np.zeros((H, W), np.int32)
    data[40, 60] = -5
    mask = mask_of(write_data(tmp_path / "m.tif", data, "YX", tile=(16, 16)), tmp_path)
    with pytest.raises(ValueError, match="negative"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(32, 48, 48, 64))
    with pytest.raises(ValueError, match="negative"):
        read_mask(mask, tmp_path, tmp_path)


def test_a_mask_whose_header_names_x_before_y_is_refused_as_the_whole_read_refuses_it(tmp_path):
    path = write_data(tmp_path / "m.tif", np.zeros((W, H), np.uint8), "XY")
    mask = mask_of(path, tmp_path)
    with pytest.raises(ValueError, match="not the 2D plane its header gave"):
        read_mask(mask, tmp_path, tmp_path)
    with pytest.raises(ValueError, match="not the 2D plane its header gave"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4))


def test_pages_that_cannot_be_read_in_parts_are_refused(small, tmp_path, monkeypatch):
    from morphobricks_preprocessing import windows as windows_module

    mask, _ = small
    monkeypatch.setattr(windows_module, "_tiff_blocking", lambda *args: None)
    with pytest.raises(WindowLayoutError, match="cannot be read in parts"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 4, 0, 4), t=0)


def test_a_1d_mask_stored_as_one_page_is_refused_not_read_whole(tmp_path):
    path = tmp_path / "m.tif"
    tifffile.imwrite(path, np.arange(W, dtype=np.uint8))  # tifffile's default 1-D metadata
    mask = mask_of(path, tmp_path)
    assert mask.axes == "YX"
    np.testing.assert_array_equal(read_mask(mask, tmp_path, tmp_path)[0], np.arange(W))
    with pytest.raises(WindowLayoutError, match="no Y and X"):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 1, 0, 4))


def test_a_file_that_is_not_an_image_is_refused(tmp_path):
    (tmp_path / "m.tif").write_bytes(b"not an image")
    mask = MaskFile("", "", "m.tif", FOLDER, (H, W), np.dtype(np.uint8), "YX", "YX")
    with pytest.raises(PlaneReadError):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, 1, 0, 1))


# Only the pieces a window intersects are read ------------------------------------------

SIDE, TILE = 2048, 256
LIMIT = 8 * 2**20  # far below one 16 MiB uint32 plane (whose whole read needs more than twice it)


@pytest.fixture(scope="module")
def large():
    """Square objects of 64 pixels, labels up to 1023, as uint32."""
    y, x = np.indices((SIDE, SIDE), dtype=np.uint32)
    return (y // 64) * 32 + x // 64 + 1


@pytest.mark.parametrize("compression", [None, "zlib"])
def test_a_large_tiled_mask_window_reads_only_its_tiles(
    tmp_path, large, segment_reads, compression
):
    path = write_data(tmp_path / "m.tif", large, "YX", tile=(TILE, TILE), compression=compression)
    mask = mask_of(path, tmp_path)
    bounds = (500, 650, 1000, 1300)
    plan = mask_window_plan(mask, tmp_path, tmp_path, bounds=bounds, memory_limit=LIMIT)
    assert plan.pieces == 6 and plan.memory_estimate <= LIMIT and plan.dtype == np.uint32
    window = read_mask_window(mask, tmp_path, tmp_path, bounds=bounds, memory_limit=LIMIT)
    assert window.dtype == np.uint32
    np.testing.assert_array_equal(window, large[500:650, 1000:1300])
    across = SIDE // TILE
    expected = intersecting(bounds, SIDE, TILE)
    assert sorted(segment_reads) == sorted(r * across + c for r, c in expected)
    with pytest.raises(WindowMemoryError):
        read_mask_window(mask, tmp_path, tmp_path, bounds=(0, SIDE, 0, SIDE), memory_limit=LIMIT)


def test_a_large_striped_mask_window_reads_only_its_strips(tmp_path, large, segment_reads):
    path = write_data(tmp_path / "m.tif", large, "YX", rowsperstrip=64, compression="zlib")
    mask = mask_of(path, tmp_path)
    window = read_mask_window(
        mask, tmp_path, tmp_path, bounds=(1000, 1100, 5, 2000), memory_limit=LIMIT
    )
    np.testing.assert_array_equal(window, large[1000:1100, 5:2000])
    assert sorted(segment_reads) == [15, 16, 17]  # rows 960 to 1151


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_large_label_image_window_decodes_only_its_chunks(tmp_path, large, version):
    if version == "0.5" and zarr_major() < 3:
        pytest.skip("OME-NGFF 0.5 needs zarr 3")
    image = write_ngff(tmp_path / "a.zarr", np.zeros((1, 8, 8), np.uint8), "cyx", version=version)
    write_labels(image, {"cells": large}, "yx", version=version, chunks=(TILE, TILE))
    files = chunk_files(image / "labels" / "cells" / "0")
    assert len(files) == (SIDE // TILE) ** 2
    bounds = (500, 650, 1000, 1300)
    needed = intersecting(bounds, SIDE, TILE)
    for position, file in files.items():
        if position not in needed:
            file.write_bytes(b"not a chunk")  # decoding it would fail
    mask = mask_of(image / "labels" / "cells", tmp_path)
    plan = mask_window_plan(mask, None, tmp_path, bounds=bounds, memory_limit=LIMIT)
    assert plan.pieces == len(needed) == 6 and plan.memory_estimate <= LIMIT
    window = read_mask_window(mask, None, tmp_path, bounds=bounds, memory_limit=LIMIT)
    assert window.dtype == np.uint32
    np.testing.assert_array_equal(window, large[500:650, 1000:1300])
    with pytest.raises(PlaneReadError):  # a window over a damaged chunk does read it
        read_mask_window(mask, None, tmp_path, bounds=(0, 10, 0, 10), memory_limit=LIMIT)
