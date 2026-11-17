"""A window's memory estimate bounds its measured peak; the cache plan of a grid (#339 review).

``window_plan`` (and a reader's, with its cache) is meant as an upper bound of a window read's
peak working memory (``window_memory``). Here the peak is measured with ``tracemalloc`` over
layouts, pixel types, codecs, cached and uncached reads and window geometries, and must never
be over the estimate. ``cache_plan`` gives the cache a row-by-row grid with margins needs: with
it every piece is decoded once; one piece less and pieces are decoded again.
"""

import functools
import gc
import tracemalloc

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.window_reader import WindowReader, cache_plan
from morphobricks_preprocessing.windows import read_window, window_plan
from zarr_tree import write_ngff

SIDE = 512
WINDOWS = [(0, 64, 0, 64), (0, 128, 0, 128), (67, 390, 45, 512), (0, 512, 0, 512)]
CODECS = {
    "none": {},
    "zlib": {"compression": "zlib"},
    "zlib predictor": {"compression": "zlib", "predictor": True},
    "lzma": {"compression": "lzma"},
}
STORAGE = {
    "tiles 128": {"tile": (128, 128)},
    "tiles 64": {"tile": (64, 64)},
    "strips 32": {"rowsperstrip": 32},
}


def _peak(read):
    """Return the peak traced memory of ``read()`` above what was held when it started."""
    gc.collect()
    tracemalloc.start()
    try:
        start = tracemalloc.get_traced_memory()[0]
        read()
        return tracemalloc.get_traced_memory()[1] - start
    finally:
        tracemalloc.stop()


def _filled(path, mapping, cache, plane, read):
    """Open a reader of ``cache`` bytes and fill it; return it and the bytes it holds."""
    reader = WindowReader(path, mapping, cache_bytes=cache)
    reader.read_window(bounds=(0, plane, 0, plane), **read)
    return reader, reader.stats().nbytes


def _cached_peak(path, mapping, cache, plane, bounds, read):
    """Return a filled reader's estimate and the peak of reading ``bounds`` through it."""
    gc.collect()
    tracemalloc.start()  # from before the cache is filled: its pieces are traced
    try:
        reader, held = _filled(path, mapping, cache, plane, read)
        gc.collect()
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        reader.read_window(bounds=bounds, **read)
        peak = tracemalloc.get_traced_memory()[1] - start + held
    finally:
        tracemalloc.stop()
    return reader.window_plan(bounds=bounds, channels=read["channels"]).memory_estimate, peak


def _check(path, axes, channels, plane, coordinates=None):
    """Assert the estimate bounds the peak of every window, uncached and through a cache."""
    mapping, read = prefill_mapping(axes), {"channels": channels, **(coordinates or {})}
    read_window(path, mapping, bounds=(0, 1, 0, 1), **read)  # imports and first-use state
    for y0, y1, x0, x1 in WINDOWS:
        bounds = (min(y0, plane - 1), min(y1, plane), min(x0, plane - 1), min(x1, plane))
        plan = window_plan(path, mapping, bounds=bounds, channels=channels)
        peak = _peak(functools.partial(read_window, path, mapping, bounds=bounds, **read))
        assert peak <= plan.memory_estimate, (path.name, bounds, peak, plan.memory_estimate)
        for cache in (2**17, 2**21):
            estimate, peak = _cached_peak(path, mapping, cache, plane, bounds, read)
            assert peak <= estimate, (path.name, cache, bounds, peak, estimate)


@pytest.mark.parametrize("storage", sorted(STORAGE))
@pytest.mark.parametrize(
    ("dtype", "codec"),
    # a floating-point predictor needs imagecodecs (not a dependency): not in the matrix
    [
        (d, c)
        for d in ("uint8", "uint16", "float32")
        for c in sorted(CODECS)
        if (d, c) != ("float32", "zlib predictor")
    ],
)
def test_the_estimate_bounds_the_peak_of_a_tiff_window(tmp_path, dtype, codec, storage):
    data = (np.random.default_rng(1).random((SIDE, SIDE)) * 200).astype(dtype)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, metadata={"axes": "YX"}, **CODECS[codec], **STORAGE[storage])
    _check(path, "YX", [0], SIDE)


def test_the_estimate_bounds_the_peak_of_rgb_and_ome_stacks(tmp_path):
    rgb = np.random.default_rng(2).integers(0, 200, (SIDE, SIDE, 3), dtype=np.uint8)
    path = tmp_path / "rgb.tif"
    tifffile.imwrite(path, rgb, photometric="rgb", tile=(128, 128), compression="zlib")
    _check(path, "YXS", [0, 1, 2], SIDE)
    stack = np.random.default_rng(3).integers(0, 4000, (30, 3, 128, 128), dtype=np.uint16)
    path = tmp_path / "stack.ome.tif"
    tifffile.imwrite(
        path, stack, ome=True, metadata={"axes": "TCYX"}, tile=(64, 64), compression="zlib"
    )
    _check(path, "TCYX", [2, 0], 128, {"t": 29})


@pytest.mark.parametrize("chunks", [(1, 64, 64), (3, 128, 128), (2, 256, 256)])
@pytest.mark.parametrize("version", ["0.4", "0.5"])
@pytest.mark.parametrize("dtype", ["uint8", "uint16", "float32"])
def test_the_estimate_bounds_the_peak_of_an_ome_zarr_window(tmp_path, dtype, version, chunks):
    data = (np.random.default_rng(4).random((3, SIDE, SIDE)) * 200).astype(dtype)
    path = write_ngff(tmp_path / "a.zarr", data, "cyx", version=version, chunks=chunks)
    _check(path, "CYX", [2, 0], SIDE)


def _decodes(path, mapping, cache_bytes, step, margin, channels):
    """Return the pieces decoded reading a row-by-row grid of windows through one reader."""
    with tifffile.TiffFile(path) as tif:
        side = tif.series[0].shape[0]
    reader = WindowReader(path, mapping, cache_bytes=cache_bytes)
    for y in range(0, side, step):
        for x in range(0, side, step):
            bounds = (
                max(y - margin, 0),
                min(y + step + margin, side),
                max(x - margin, 0),
                min(x + step + margin, side),
            )
            reader.read_window(bounds=bounds, channels=channels)
    return reader.stats().decodes


@pytest.mark.parametrize(("tile", "step", "margin"), [(128, 128, 20), (64, 128, 30), (128, 96, 7)])
def test_the_cache_plan_is_the_threshold_of_one_decode_per_piece(tmp_path, tile, step, margin):
    data = np.random.default_rng(5).integers(0, 200, (1024, 1024, 3), dtype=np.uint8)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, photometric="rgb", tile=(tile, tile), compression="zlib")
    mapping = prefill_mapping("YXS")
    plan = cache_plan(
        path, mapping, window=(step, step), margin=(margin, margin), channels=[0, 1, 2]
    )
    piece = tile * tile * 3
    rows = -(-(step + 2 * margin) // tile) + 1
    assert (plan.piece_nbytes, plan.groups, plan.per_row) == (piece, 1, 1024 // tile)
    assert plan.piece_rows == rows and plan.cache_bytes == rows * (1024 // tile) * piece
    every = (1024 // tile) ** 2
    assert _decodes(path, mapping, plan.cache_bytes, step, margin, [0, 1, 2]) == every
    less = plan.cache_bytes - plan.per_row * piece  # a row of pieces less: they thrash
    assert _decodes(path, mapping, less, step, margin, [0, 1, 2]) > every


def test_the_cache_plan_of_channels_stored_apart_and_of_uncompressed_pieces(tmp_path):
    data = np.random.default_rng(6).integers(0, 200, (3, 512, 512), dtype=np.uint16)
    path = tmp_path / "a.tif"
    tifffile.imwrite(
        path,
        data,
        photometric="minisblack",
        metadata={"axes": "CYX"},
        tile=(128, 128),
        compression="zlib",
    )
    mapping = prefill_mapping("CYX")
    plan = cache_plan(path, mapping, window=(128, 128), margin=(10, 10), channels=[2, 0])
    assert plan.groups == 2 and plan.cache_bytes == 3 * 4 * 128 * 128 * 2 * 2
    raw = tmp_path / "raw.tif"
    tifffile.imwrite(raw, data, photometric="minisblack", metadata={"axes": "CYX"}, tile=(128, 128))
    assert (
        cache_plan(raw, mapping, window=(128, 128), margin=(10, 10), channels=[0]).cache_bytes == 0
    )
    for window, margin in (((0, 4), (0, 0)), ((4, 4), (-1, 0)), ((4.0, 4), (0, 0))):
        with pytest.raises(ValueError, match="whole"):
            cache_plan(path, mapping, window=window, margin=margin, channels=[0])
