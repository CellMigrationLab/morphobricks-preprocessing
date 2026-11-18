"""A window's plan sizes the pieces of the pages its read decodes (issue #356, P1).

The plan of a compressed TIFF window charged every page the first page's (the keyframe's)
largest encoded strip or tile, while the read decodes the requested channel, T and Z's own
page: a later page that compresses less was under-planned. Here a tiled deflate TCYX image
has a blank t=0 (pieces of a few bytes) and a noisy t=1 (pieces that barely compress): the
plan of t=1 must be higher, sized from the headers of the pages and pieces read; its peak,
measured in a fresh process (``window_peaks``, with its interned-string rebuild guard), must
stay under it; and a limit below it must refuse before any piece is decoded, uncached and
through a cache. Blocks of planes over the memory limit, read with one layout for every
plane, are sized from the series' largest piece.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping, tiff_pieces
from morphobricks_preprocessing.planes import plane_layout
from morphobricks_preprocessing.window_map import map_plan
from morphobricks_preprocessing.window_reader import WindowReader
from morphobricks_preprocessing.windows import WindowMemoryError, read_window, window_plan

SIDE, TILE = 256, 128
AXES = "TCYX"
PEAKS = Path(__file__).resolve().parent / "window_peaks.py"
WINDOW = (37, 190, 45, 256)  # across four tiles
CHANNELS = [1, 0]


@pytest.fixture
def blank_then_noisy(tmp_path):
    data = np.zeros((2, 2, SIDE, SIDE), dtype=np.uint16)
    data[1] = np.random.default_rng(356).integers(0, 2**16, (2, SIDE, SIDE), dtype=np.uint16)
    path = tmp_path / "tc.ome.tif"
    tifffile.imwrite(
        path, data, ome=True, metadata={"axes": AXES}, tile=(TILE, TILE), compression="zlib"
    )
    return path, data


def _largest(path, t):
    """The largest stored tile of the pages (one per channel) of time point ``t``."""
    with tifffile.TiffFile(path) as tif:
        pages = tif.series[0].pages
        return max(max(int(n) for n in pages[t * 2 + c].databytecounts) for c in range(2))


@pytest.fixture
def no_decode(monkeypatch):
    """Fail the test if any compressed piece is decoded."""

    def refuse(*args, **kwargs):
        raise AssertionError("a piece was decoded")

    monkeypatch.setattr(tiff_pieces, "_decode_segment", refuse)
    return monkeypatch


def test_a_later_page_that_compresses_less_gets_a_higher_plan(blank_then_noisy):
    path, _ = blank_then_noisy
    mapping = prefill_mapping(AXES)
    blank, noisy = _largest(path, 0), _largest(path, 1)
    assert noisy > 100 * blank  # the keyframe's pieces are tiny
    plan = {t: window_plan(path, mapping, bounds=WINDOW, channels=CHANNELS, t=t) for t in (0, 1)}
    assert plan[1].memory_estimate - plan[0].memory_estimate == noisy - blank
    # without a plane, the plan holds for any plane: the series' largest piece
    anywhere = window_plan(path, mapping, bounds=WINDOW, channels=CHANNELS)
    assert anywhere.memory_estimate == plan[1].memory_estimate
    with WindowReader(path, mapping, cache_bytes=2**20) as reader:
        cached = {t: reader.window_plan(bounds=WINDOW, channels=CHANNELS, t=t) for t in (0, 1)}
        assert cached[1].memory_estimate - cached[0].memory_estimate == noisy - blank
        assert cached[1].memory_estimate > plan[1].memory_estimate  # the cache counts
        # a window map plans the plane it reads
        mapped = {t: map_plan(reader, [WINDOW], channels=CHANNELS, workers=1, t=t) for t in (0, 1)}
        assert mapped[1].window_bytes - mapped[0].window_bytes == noisy - blank


@pytest.mark.parametrize("t", [0, 1])
def test_the_peak_of_each_page_stays_under_its_own_plan(blank_then_noisy, t):
    path, _ = blank_then_noisy
    request = {"path": str(path), "axes": AXES, "channels": CHANNELS, "plane": SIDE}
    done = subprocess.run(
        [sys.executable, str(PEAKS), json.dumps({**request, "coordinates": {"t": t}})],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    measured = json.loads(done.stdout)
    assert len(measured["rebuilds"]) == 1  # the interned-string rebuild guard ran (#351)
    results = measured["results"]
    assert {what for what, *_ in results} == {"read", "cached"}
    for what, bounds, cache, peak, estimate in results:
        assert peak <= estimate, (t, what, cache, bounds, peak, estimate)


def test_a_limit_below_the_later_page_refuses_before_any_decode(blank_then_noisy, no_decode):
    path, data = blank_then_noisy
    mapping = prefill_mapping(AXES)
    needed = window_plan(path, mapping, bounds=WINDOW, channels=CHANNELS, t=1).memory_estimate
    with pytest.raises(WindowMemoryError, match="needs about"):
        read_window(path, mapping, bounds=WINDOW, channels=CHANNELS, t=1, memory_limit=needed - 1)
    no_decode.undo()
    # the blank page fits that limit: the first page's plan would have let t=1 through
    window = read_window(
        path, mapping, bounds=WINDOW, channels=CHANNELS, t=0, memory_limit=needed - 1
    )
    np.testing.assert_array_equal(window, data[0][CHANNELS, 37:190, 45:256])
    window = read_window(path, mapping, bounds=WINDOW, channels=CHANNELS, t=1, memory_limit=needed)
    np.testing.assert_array_equal(window, data[1][CHANNELS, 37:190, 45:256])


def test_a_cached_limit_below_the_later_page_refuses_before_any_decode(blank_then_noisy, no_decode):
    path, data = blank_then_noisy
    mapping = prefill_mapping(AXES)
    cache = 2**20
    with WindowReader(path, mapping, cache_bytes=cache) as reader:
        needed = reader.window_plan(bounds=WINDOW, channels=CHANNELS, t=1).memory_estimate
    with WindowReader(path, mapping, cache_bytes=cache, memory_limit=needed - 1) as reader:
        with pytest.raises(WindowMemoryError, match="needs about"):
            reader.read_window(bounds=WINDOW, channels=CHANNELS, t=1)
        assert reader.stats().decodes == 0
        no_decode.undo()
        window = reader.read_window(bounds=WINDOW, channels=CHANNELS, t=0)
        np.testing.assert_array_equal(window, data[0][CHANNELS, 37:190, 45:256])
    with WindowReader(path, mapping, cache_bytes=cache, memory_limit=needed) as reader:
        window = reader.read_window(bounds=WINDOW, channels=CHANNELS, t=1)
        np.testing.assert_array_equal(window, data[1][CHANNELS, 37:190, 45:256])


def test_blocks_of_any_plane_are_sized_from_the_series_largest_piece(blank_then_noisy):
    path, _ = blank_then_noisy
    layout = plane_layout(path, prefill_mapping(AXES), memory_limit=6 * 2**20)
    assert layout.in_blocks
    assert layout.blocking.encoded_nbytes == max(_largest(path, 0), _largest(path, 1))
