"""LZW and JPEG reads stay within their memory estimates, native memory included (#356).

Owner decision of 18 November 2026: JPEG (the usual H&E and brightfield whole-slide export)
and LZW are read by the bounded readers once their decoding memory is measured. They are
decoded by imagecodecs in C, whose ``malloc`` ``tracemalloc`` does not see, so each read is
measured in a fresh process (``codec_peaks``) as:

- its **peak resident memory** (RSS, Linux), which must not exceed the whole estimate;
- its **traced peak**, Python's part, which must not exceed the estimate less the C library's
  terms (``window_memory.codec_state`` and ``native_piece_work``);
- for one piece decoded alone, the **C heap's peak** less the traced peak: the C library's own
  memory, which must not exceed those terms alone (glibc).

The matrix: JPEG in tiles of 256 and 512 and in strips, YCbCr 4:2:0 (and 4:2:2), RGB-encoded
and greyscale, compressible and noisy, the tables in the TIFF header, baseline and
progressive; LZW ``uint8`` and ``uint16``, without and with the horizontal predictor, in
tiles of 256 and 512 and in strips, and interleaved RGB; windows inside one piece, across
pieces and the whole plane, uncached and through a cache holding the plane; every channel of a
plane with one and with four decoding threads; blocks of a plane over the memory limit. The
parts measured alone make each new term matter: lowering any one to zero fails a test.
Set ``CODEC_PEAKS_REPORT`` to a file to append every measurement to it (JSON lines).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("imagecodecs")

from codec_images import content, write_jpeg, write_lzw  # noqa: E402
from morphobricks_preprocessing import plane_layout, prefill_mapping  # noqa: E402

PEAKS = Path(__file__).resolve().parent / "codec_peaks.py"
SIDE = 1024
WINDOWS = [(0, 64, 0, 64), (200, 600, 200, 600), (0, SIDE, 0, SIDE)]
"""Inside one piece, across pieces (the previous one still held) and the whole plane."""
STORAGE = {"tiles 256": {"tile": (256, 256)}, "tiles 512": {"tile": (512, 512)}}
JPEG_STRIPS = {"ycc420": 16, "ycc422": 16, "rgb": 8, "grey": 8}
"""The rows of a JPEG strip: one MCU high, the most rows of a piece's width libjpeg buffers."""


def _child(request, env=None):
    done = subprocess.run(
        [sys.executable, str(PEAKS), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "PYTHONHASHSEED": "0", **(env or {})},
    )
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    report = os.environ.get("CODEC_PEAKS_REPORT")
    if report:
        with open(report, "a", encoding="utf-8") as f:
            for row in found["results"]:
                test = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]
                f.write(json.dumps({"test": test, "env": env, "row": row}) + "\n")
    return found


def _check(results, name):
    """Assert RSS within the estimate and the traced peak within its Python part."""
    for what, where, rss, traced, estimate, native in results:
        if rss is not None:
            assert rss <= estimate, (name, what, where, "rss", rss, estimate)
        assert traced <= estimate - native, (name, what, where, "traced", traced, estimate, native)


def _jpeg(tmp_path, kind, storage, noisy, progressive=False):
    shape = (SIDE, SIDE) if kind == "grey" else (SIDE, SIDE, 3)
    data = content(shape, noisy)
    if storage == "strips":
        layout = {"rows": JPEG_STRIPS[kind] * (4 if progressive else 1)}
    else:
        layout = {"tile": STORAGE[storage]["tile"]}
    path = tmp_path / "j.tif"
    write_jpeg(path, data, kind, progressive=progressive, **layout)
    return path, ("YX" if kind == "grey" else "YXS"), data.nbytes


def _lzw(tmp_path, dtype, storage, predictor, noisy, rgb=False):
    data = content((SIDE, SIDE, 3) if rgb else (SIDE, SIDE), noisy, dtype)
    layout = {"rowsperstrip": 64} if storage == "strips" else STORAGE[storage]
    path = write_lzw(tmp_path / "l.tif", data, predictor=predictor, **layout)
    return path, ("YXS" if rgb else "YX"), data.nbytes


def _windows(path, axes, nbytes):
    """Measure the windows uncached, then through a cache, each in a fresh process."""
    channels = [0, 1, 2] if axes == "YXS" else [0]
    request = {"what": "windows", "path": str(path), "axes": axes, "channels": channels}
    request |= {"plane": [SIDE, SIDE], "windows": WINDOWS, "cache": nbytes}
    return [row for c in (False, True) for row in _child({**request, "cached": c})["results"]]


JPEG_CASES = [
    (kind, storage, noisy)
    for kind in ("ycc420", "rgb", "grey")
    for storage in ("tiles 256", "tiles 512", "strips")
    for noisy in (False, True)
] + [("ycc422", "tiles 256", True), ("ycc422", "strips", True)]


@pytest.mark.parametrize(("kind", "storage", "noisy"), JPEG_CASES, ids=str)
def test_jpeg_windows_stay_within_the_estimate(tmp_path, kind, storage, noisy):
    path, axes, nbytes = _jpeg(tmp_path, kind, storage, noisy)
    _check(_windows(path, axes, nbytes), (kind, storage, noisy))


@pytest.mark.parametrize(
    ("kind", "storage"), [("rgb", "tiles 256"), ("ycc420", "tiles 512"), ("rgb", "strips")]
)
def test_progressive_jpeg_windows_stay_within_the_estimate(tmp_path, kind, storage):
    # A multi-scan JPEG keeps the coefficients of the whole piece while it decodes it.
    path, axes, nbytes = _jpeg(tmp_path, kind, storage, True, progressive=True)
    _check(_windows(path, axes, nbytes), (kind, storage, "progressive"))


LZW_CASES = [
    (dtype, storage, predictor, noisy)
    for dtype in ("uint8", "uint16")
    for storage in ("tiles 256", "tiles 512", "strips")
    for predictor in (False, True)
    for noisy in (False, True)
]


@pytest.mark.parametrize(("dtype", "storage", "predictor", "noisy"), LZW_CASES, ids=str)
def test_lzw_windows_stay_within_the_estimate(tmp_path, dtype, storage, predictor, noisy):
    path, axes, nbytes = _lzw(tmp_path, dtype, storage, predictor, noisy)
    _check(_windows(path, axes, nbytes), (dtype, storage, predictor, noisy))


def test_lzw_rgb_windows_stay_within_the_estimate(tmp_path):
    path, axes, nbytes = _lzw(tmp_path, "uint8", "tiles 256", True, True, rgb=True)
    _check(_windows(path, axes, nbytes), "lzw rgb")


PLANES = {
    "jpeg ycc420 tiles 256": lambda p: _jpeg(p, "ycc420", "tiles 256", True),
    "jpeg rgb tiles 512": lambda p: _jpeg(p, "rgb", "tiles 512", False),
    "jpeg grey strips": lambda p: _jpeg(p, "grey", "strips", True),
    "jpeg rgb progressive tiles 256": lambda p: _jpeg(p, "rgb", "tiles 256", True, True),
    "lzw uint16 predictor tiles 256": lambda p: _lzw(p, "uint16", "tiles 256", True, True),
    "lzw uint8 strips": lambda p: _lzw(p, "uint8", "strips", False, True),
    "lzw rgb tiles 256": lambda p: _lzw(p, "uint8", "tiles 256", True, False, rgb=True),
}


@pytest.mark.parametrize("threads", [1, 4])
@pytest.mark.parametrize("case", sorted(PLANES))
def test_every_channel_of_a_plane_stays_within_the_estimate(tmp_path, case, threads):
    # RunInputs.read_plane: tifffile decodes the page's pieces in its own threads, each with
    # its codec's state and buffers (the estimate counts tifffile's thread count).
    path, axes, _ = PLANES[case](tmp_path)
    found = _child(
        {"what": "plane", "path": str(path), "axes": axes},
        {"TIFFFILE_NUM_THREADS": str(threads)},
    )
    [(_, used, *_)] = found["results"]
    assert used == threads
    _check(found["results"], (case, threads))


BLOCKS = ["jpeg ycc420 tiles 256", "jpeg grey strips", "lzw uint16 predictor tiles 256"]


@pytest.mark.parametrize("case", BLOCKS)
def test_blocks_of_a_plane_stay_within_the_estimate(tmp_path, case):
    path, axes, _ = PLANES[case](tmp_path)
    mapping = prefill_mapping(axes)
    limit = plane_layout(path, mapping).memory_estimate // 2  # over it: read in blocks
    found = _child({"what": "blocks", "path": str(path), "axes": axes, "limit": limit})
    [(_, _, rss, traced, estimate, _)] = found["results"]
    assert traced <= estimate
    if rss is not None:
        assert rss <= estimate


PARTS = {  # each new term is most of what one of these decodes holds
    # libjpeg's state: a 16-px piece's buffers are a few KiB
    "jpeg grey 16 px": lambda p: write_jpeg(p, content((64, 64), True), "grey", tile=(16, 16)),
    "jpeg ycc420 16 px": lambda p: write_jpeg(
        p, content((64, 64, 3), True), "ycc420", tile=(16, 16)
    ),
    # LZW's code table
    "lzw 16 px": lambda p: write_lzw(p, content((64, 64), True), tile=(16, 16)),
    "lzw predictor 16 px": lambda p: write_lzw(
        p, content((64, 64), True), tile=(16, 16), predictor=True
    ),
    # libjpeg's coefficients of a progressive piece
    "jpeg progressive tile 1024": lambda p: write_jpeg(
        p, content((SIDE, SIDE, 3), True), "rgb", tile=(SIDE, SIDE), progressive=True
    ),
    "jpeg progressive ycc420 tile 512": lambda p: write_jpeg(
        p, content((SIDE, SIDE, 3), True), "ycc420", tile=(512, 512), progressive=True
    ),
    # libjpeg's rows: wide strips of whole MCUs
    "jpeg progressive strips 32 x 32768": lambda p: write_jpeg(
        p, content((64, 32768, 3), True), "rgb", rows=32, progressive=True
    ),
    "jpeg ycc420 strips 16 x 32768": lambda p: write_jpeg(
        p, content((64, 32768, 3), True), "ycc420", rows=16
    ),
    "jpeg ycc420 tile 256": lambda p: write_jpeg(
        p, content((512, 512, 3), True), "ycc420", tile=(256, 256)
    ),
}


def _glibc():
    try:
        import ctypes

        return hasattr(ctypes.CDLL("libc.so.6"), "mallinfo2")
    except OSError:
        return False


@pytest.mark.skipif(not _glibc(), reason="the C heap is sampled through glibc's mallinfo2")
@pytest.mark.parametrize("case", sorted(PARTS))
def test_the_c_library_holds_at_most_its_terms(tmp_path, case):
    path = PARTS[case](tmp_path / "p.tif")
    [(_, _, _, traced, heap, native)] = _child({"what": "parts", "path": str(path)})["results"]
    assert 0 < heap - traced <= native, (case, heap, traced, native)


DEFLATE_LZMA = {  # decoded by imagecodecs too since it is required (review of #357, B2)
    "deflate tiles 512": {"compression": "zlib", "tile": (512, 512)},
    "deflate predictor tiles 256": {"compression": "zlib", "predictor": True, "tile": (256, 256)},
    "lzma tiles 256": {"compression": "lzma", "tile": (256, 256)},
}


@pytest.mark.parametrize("case", sorted(DEFLATE_LZMA))
def test_deflate_and_lzma_windows_stay_within_the_estimate_in_rss(tmp_path, case):
    import tifffile

    data = content((SIDE, SIDE), True, "uint16")
    path = tmp_path / "d.tif"
    tifffile.imwrite(path, data, photometric="minisblack", **DEFLATE_LZMA[case])
    _check(_windows(path, "YX", data.nbytes), case)
