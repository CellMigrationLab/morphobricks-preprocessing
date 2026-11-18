"""A window's memory estimate bounds its measured peak; the cache plan of a grid (#339 review).

``window_plan`` (and a reader's, with its cache) is meant as an upper bound of a window read's
peak working memory (``window_memory``). Here the peak is measured with ``tracemalloc`` over
layouts, pixel types, codecs, cached and uncached reads and window geometries, and must never
be over the estimate. It is measured in a fresh Python process (``window_peaks``): in the
pytest process, the interpreter's own allocations, left by whichever tests ran before, can land
in the measured read (#348). The 1024-px cases, the many pages and pieces and the parts
measured alone make each term of the estimate matter: lowering any one to zero fails a test
(#351). ``cache_plan`` gives the cache a row-by-row grid with margins needs: with it every
piece is decoded once; one piece less and pieces are decoded again.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.window_reader import WindowReader, cache_plan
from zarr_tree import write_ngff

SIDE = 256
PEAKS = Path(__file__).resolve().parent / "window_peaks.py"
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
LARGE = 1024
LARGE_WINDOWS = [(0, 64, 0, 64), (200, 300, 400, 600), (0, LARGE, 0, LARGE)]
"""Inside one piece, across pieces (the previous piece still held) and the whole plane."""
LARGE_PIECES = {  # pieces of 1 MiB (float32) and 512 KiB (uint16)
    "float32 zlib strips 256": ("float32", {"compression": "zlib", "rowsperstrip": 256}),
    "float32 zlib tiles 512": ("float32", {"compression": "zlib", "tile": (512, 512)}),
    "uint16 zlib predictor tiles 512": (
        "uint16",
        {"compression": "zlib", "predictor": True, "tile": (512, 512)},
    ),
}


def _child(request):
    """Return what ``window_peaks`` measures for ``request`` in a fresh process."""
    done = subprocess.run(
        [sys.executable, str(PEAKS), json.dumps(request)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _measured(path, axes, channels, plane, coordinates=None, **more):
    """Return the peaks and estimates of ``window_peaks``, measured in a fresh process."""
    request = {"path": str(path), "axes": axes, "channels": channels, "plane": plane}
    return _child({**request, "coordinates": coordinates, **more})


def _check(path, axes, channels, plane, coordinates=None, **more):
    """Assert the estimate bounds the peak of every window, uncached and through a cache."""
    results = _measured(path, axes, channels, plane, coordinates, **more)["results"]
    for what, bounds, cache, peak, estimate in results:
        assert peak <= estimate, (path.name, what, cache, bounds, peak, estimate)


def test_the_peaks_are_measured_in_a_fresh_process(tmp_path):
    data = np.random.default_rng(1).integers(0, 200, (SIDE, SIDE), dtype=np.uint8)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, metadata={"axes": "YX"}, tile=(128, 128))
    measured = _measured(path, "YX", [0], SIDE)
    assert measured["pid"] != os.getpid() and not measured["pytest"]
    assert len(measured["results"]) == 9  # three windows, uncached and through two caches


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


@pytest.mark.parametrize("case", sorted(LARGE_PIECES))
def test_the_estimate_bounds_the_peak_of_a_tiff_window_of_large_pieces(tmp_path, case):
    # At 256 px the pieces are too small for the decode's buffers to matter: here they are
    # most of the peak, so each lowered decode term is over it (#351).
    dtype, storage = LARGE_PIECES[case]
    rng = np.random.default_rng(7)
    if dtype == "uint16":  # the whole range: the pieces barely compress, held encoded too
        data = rng.integers(0, 2**16, (LARGE, LARGE), dtype=dtype)
    else:
        data = (rng.random((LARGE, LARGE)) * 200).astype(dtype)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, metadata={"axes": "YX"}, **storage)
    _check(path, "YX", [0], LARGE, windows=LARGE_WINDOWS)


def test_the_estimate_bounds_the_peak_of_many_pages_and_many_pieces(tmp_path):
    # The open header grows with the pages and with the pieces of a page: 1000 pages and
    # 8192 strips make it most of the peak (#351).
    pages = np.random.default_rng(8).integers(0, 9, (1000, 16, 16), dtype=np.uint8)
    path = tmp_path / "pages.ome.tif"
    tifffile.imwrite(path, pages, ome=True, metadata={"axes": "TYX"}, compression="zlib")
    _check(path, "TYX", [0], 16, {"t": 999}, windows=[(0, 16, 0, 16)], caches=[2**16])
    strips = np.random.default_rng(9).integers(0, 9, (8192, 16), dtype=np.uint8)
    path = tmp_path / "strips.tif"
    tifffile.imwrite(path, strips, metadata={"axes": "YX"}, compression="zlib", rowsperstrip=1)
    _check(path, "YX", [0], [8192, 16], windows=[(0, 1, 0, 16), (100, 300, 0, 16)], caches=[])


@pytest.mark.parametrize(
    ("dtype", "side", "storage"),
    [
        ("uint8", 16, {"compression": "zlib"}),
        ("uint8", 16, {"compression": "lzma"}),
        ("float32", LARGE, {"compression": "zlib", "rowsperstrip": 256}),
    ],
)
def test_each_part_of_a_tiff_piece_estimate_bounds_what_it_stands_for(
    tmp_path, dtype, side, storage
):
    # Parts measured alone (window_peaks.parts): a codec's state is a few tens of KiB, under
    # the header's margin in a whole read, and the frozen copy never coincides with the
    # decode's peak, so only alone does lowering them show (#351).
    data = (np.random.default_rng(10).random((side, side)) * 200).astype(dtype)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, metadata={"axes": "YX"}, **storage)
    found = _child({"path": str(path), "parts": True})
    assert [part for part, _, _ in found["results"]] == ["decode", "frozen"]
    for part, peak, bound in found["results"]:
        assert peak <= bound, (path.name, part, peak, bound)


@pytest.mark.parametrize("chunks", [(1, 64, 64), (3, 64, 64), (2, 128, 128)])
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
