"""A function mapped over windows of one image in threads (``window_map``, lazy-windows §5).

Results come in window order for any number of workers, bitwise those of the reader alone;
memory is planned and checked against a budget before any pixel is read; an error or an early
stop cancels what is not started and leaves no thread and a usable reader.
"""

import gc
import hashlib
import threading
import time

import numpy as np
import pytest

from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, tiff_pieces, window_reader
from morphobricks_preprocessing.run_inputs import RunInputs
from morphobricks_preprocessing.window_map import (
    THREAD_PREFIX,
    iter_windows,
    map_plan,
    map_windows,
)
from morphobricks_preprocessing.window_reader import WindowReader, WindowReaderClosedError
from morphobricks_preprocessing.windows import (
    WindowBoundsError,
    WindowMemoryError,
    read_window,
)
from test_windows import rgb, stored
from tiff_tree import write_data
from zarr_tree import write_ngff

MIB = 2**20
BUDGET = 2**31
H, W = 192, 160


def tiles(path):
    return rgb(path, stored((H, W, 3), "uint8", seed=1), tile=(32, 32), compression="zlib")


def strips(path):
    data = stored((3, H, W), "uint16", seed=2)
    return write_data(path, data, "CYX", rowsperstrip=7, compression="zlib")


def plain_strips(path):
    data = stored((2, H, W), "uint16", seed=3)
    return write_data(path, data, "CYX", rowsperstrip=11)


def ome_zarr(path):
    return write_ngff(path, stored((3, H, W), "uint16", seed=4), "cyx", chunks=(2, 24, 40))


# name: (writer, axes, channels read)
IMAGES = {
    "tiff zlib tiles, interleaved RGB": (tiles, "YXS", [2, 0, 1]),
    "tiff zlib strips, a page per channel": (strips, "CYX", [1, 2]),
    "tiff uncompressed strips": (plain_strips, "CYX", [1, 0]),
    "ome-zarr chunks": (ome_zarr, "CYX", [0, 2, 1]),
}


def random_windows(seed, count=60):
    """Random windows, overlapping and repeated, with the whole plane and single pixels."""
    rng = np.random.default_rng(seed)
    found = [(0, H, 0, W), (5, 6, 7, 8)]
    for _ in range(count):
        y0, x0 = int(rng.integers(0, H - 1)), int(rng.integers(0, W - 1))
        h, w = int(rng.integers(1, 90)), int(rng.integers(1, 90))
        found.append((y0, min(y0 + h, H), x0, min(x0 + w, W)))
    return [*found, found[5], found[2]]


def digest(bounds, window):
    return bounds, window.dtype.str, window.shape, hashlib.sha256(window.tobytes()).hexdigest()


def leftover_threads():
    return [t for t in threading.enumerate() if t.name.startswith(THREAD_PREFIX)]


@pytest.fixture
def image(tmp_path):
    path = tiles(tmp_path / "a.tif")
    return path, prefill_mapping("YXS")


@pytest.fixture
def reads(monkeypatch):
    """Count every window read through a reader (the pixels read, not the plans)."""
    found = []
    original = window_reader._read_source

    def counting(source, mapping, path, bounds, *rest):
        found.append(bounds)
        return original(source, mapping, path, bounds, *rest)

    monkeypatch.setattr(window_reader, "_read_source", counting)
    return found


# Identical results for any number of workers --------------------------------------------------


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_results_are_identical_for_any_number_of_workers(tmp_path, name):
    write, axes, channels = IMAGES[name]
    path = write(tmp_path / ("a.zarr" if "zarr" in name else "a.tif"))
    mapping = prefill_mapping(axes)
    windows = random_windows(seed=len(name))
    expected = [digest(b, read_window(path, mapping, bounds=b, channels=channels)) for b in windows]
    for workers in (1, 2, 4, 8):
        with WindowReader(path, mapping, cache_bytes=MIB) as reader:
            plan = map_plan(reader, windows, channels=channels, workers=workers)
            with map_windows(plan, digest, memory_budget=BUDGET) as results:
                assert list(results) == expected, workers
            assert results.summary().finished
            prefetched = [digest(b, w) for b, w in iter_windows(plan, memory_budget=BUDGET)]
            assert prefetched == expected, workers
            assert reader.stats().nbytes <= MIB
    assert not leftover_threads()


def test_a_run_window_reader_maps_its_windows_by_channel_ids(tmp_path):
    data = np.random.default_rng(2).integers(0, 4000, (2, 3, 40, 52), dtype=np.uint16)
    write_data(tmp_path / "exp/ctrl/a.tif", data, "TCYX", tile=(16, 16), compression="zlib")
    result = run_described(tmp_path / "exp", prefill_mapping("TCYX"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    ids = [fov.channel_ids[2], fov.channel_ids[0]]
    windows = [(10, 40, 7, 33), (0, 40, 0, 52), (3, 20, 30, 50), (10, 40, 7, 33)]
    with inputs.window_reader(fov, cache_bytes=MIB) as reader:
        with pytest.raises(TypeError, match="t and z"):
            map_plan(reader, windows, channels=ids, workers=2)
        plan = map_plan(reader, windows, channels=ids, workers=3, t=1, z=0)
        got = list(iter_windows(plan, memory_budget=BUDGET))
    for (bounds, window), expected in zip(got, windows, strict=True):
        y0, y1, x0, x1 = expected
        assert bounds == expected
        assert window.tobytes() == data[1, [2, 0], y0:y1, x0:x1].tobytes()


# Order and bounded look-ahead -----------------------------------------------------------------


def test_results_come_in_window_order_whatever_finishes_first(image):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(24)]
    finished = []
    lock = threading.Lock()

    def slow_first(bounds, window):
        time.sleep(0.004 * (len(windows) - bounds[0]))  # the earliest windows finish last
        with lock:
            finished.append(bounds[0])
        return bounds[0]

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=8, max_in_flight=16)
        assert list(map_windows(plan, slow_first, memory_budget=BUDGET)) == list(range(24))
    assert finished != sorted(finished)  # completion order was not window order


def test_no_more_than_max_in_flight_windows_are_read_ahead(image, reads):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(30)]
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=3, max_in_flight=5)
        ahead = []
        for k, _ in enumerate(map_windows(plan, lambda b, w: b, memory_budget=BUDGET)):
            time.sleep(0.02)  # a slow consumer: the workers fill every free slot meanwhile
            ahead.append(len(reads) - k)
    assert max(ahead) == 5  # the result in hand and four read ahead, never more
    assert len(reads) == len(windows)


# Memory plan -----------------------------------------------------------------------------------


def test_the_plan_counts_the_largest_windows_in_flight_and_the_cache_once(image):
    path, mapping = image
    windows = [(0, 8, 0, 8), (0, H, 0, W), (0, 64, 0, 64), (10, 20, 10, 20), (0, 100, 0, 100)]
    with WindowReader(path, mapping, cache_bytes=3 * MIB) as reader:
        alone = [
            reader.window_plan(bounds=b, channels=[0, 1]).memory_estimate - 3 * MIB for b in windows
        ]
        plan = map_plan(reader, windows, channels=[0, 1], workers=1)
        assert plan.max_in_flight == 2  # 2 × workers by default
        assert plan.window_bytes == max(alone)
        assert plan.in_flight_bytes == sum(sorted(alone)[-2:])
        assert plan.memory_estimate == plan.in_flight_bytes + 3 * MIB
        wide = map_plan(reader, windows, channels=[0, 1], workers=2, max_in_flight=9)
        assert wide.in_flight_bytes == sum(alone)  # never more windows than there are
        assert (wide.workers, wide.max_in_flight) == (2, 9)


def test_a_plan_over_the_budget_is_refused_before_any_pixel_is_read(image, reads, monkeypatch):
    path, mapping = image
    decoded = []
    original = tiff_pieces._decode_segment
    monkeypatch.setattr(
        tiff_pieces, "_decode_segment", lambda *a: decoded.append(a[1]) or original(*a)
    )
    windows = [(0, H, 0, W)] * 6
    called = []
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0, 1, 2], workers=4)
        for budget in (plan.memory_estimate - 1, plan.cache_bytes, 0):
            with pytest.raises(WindowMemoryError, match="no window was read"):
                map_windows(plan, lambda b, w: called.append(b), memory_budget=budget)
            with pytest.raises(WindowMemoryError):
                iter_windows(plan, memory_budget=budget)
        assert (reads, decoded, called) == ([], [], [])
        assert reader.stats().decodes == 0
        assert len(list(map_windows(plan, lambda b, w: 0, memory_budget=plan.memory_estimate)))
    assert len(reads) == 6


@pytest.mark.parametrize(
    ("workers", "max_in_flight"),
    [(0, None), (-1, None), (True, None), (1.5, None), ("2", None), (4, 3), (2, 0), (2, 2.0)],
)
def test_an_invalid_worker_count_or_bound_is_refused_never_adjusted(image, workers, max_in_flight):
    path, mapping = image
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        with pytest.raises(ValueError, match="whole number"):
            map_plan(
                reader, [(0, 8, 0, 8)], channels=[0], workers=workers, max_in_flight=max_in_flight
            )


def test_a_window_the_plan_refuses_is_named_and_nothing_is_read(image, reads):
    path, mapping = image
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        with pytest.raises(WindowBoundsError) as error:
            map_plan(
                reader, [(0, 8, 0, 8), (0, 8, 0, 8), (0, H + 1, 0, 8)], channels=[0], workers=2
            )
        assert "window 2, bounds (0, 193, 0, 8)" in "".join(error.value.__notes__)
        with pytest.raises(TypeError, match="WindowReader or RunWindowReader"):
            map_plan(object(), [(0, 8, 0, 8)], channels=[0], workers=1)
        with pytest.raises(ValueError, match="memory budget"):
            map_windows(map_plan(reader, [], channels=[0], workers=1), digest, memory_budget=1.0)
    assert reads == []


# Errors, early stop and threads -------------------------------------------------------------


def test_the_first_error_in_window_order_is_raised_naming_its_window(image):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(12)]

    def failing(bounds, window):
        if bounds[0] == 3:
            time.sleep(0.2)  # the later failure finishes first
            raise ZeroDivisionError("three")
        if bounds[0] == 7:
            raise KeyError("seven")
        return bounds[0]

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=4)
        results = map_windows(plan, failing, memory_budget=BUDGET)
        got = []
        with pytest.raises(ZeroDivisionError, match="three") as error:
            got.extend(results)
        assert got == [0, 1, 2]
        assert error.value.__notes__ == ["(window 3, bounds (3, 11, 0, 8), in a window map)"]
        assert list(results) == []  # nothing after the error
        summary = results.summary()
        assert (summary.delivered, summary.finished, summary.workers) == (3, False, 4)
        assert not leftover_threads()
        assert not reader.closed  # the reader is the caller's, usable after the error
        window = reader.read_window(bounds=(0, H, 0, W), channels=[1])
        assert (
            window.tobytes()
            == read_window(path, mapping, bounds=(0, H, 0, W), channels=[1]).tobytes()
        )
        assert reader.stats().nbytes <= MIB


def test_an_early_stop_cancels_the_windows_not_started(image, reads):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(180)]
    started = []

    def slow(bounds, window):
        started.append(bounds)
        time.sleep(0.01)
        return bounds

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=4, max_in_flight=6)
        with map_windows(plan, slow, memory_budget=BUDGET) as results:
            for k, bounds in enumerate(results):
                assert bounds == windows[k]
                if k == 4:
                    break
        assert not leftover_threads()  # leaving the with block waited for the running ones
        assert len(reads) == len(started) <= 5 + 6
        assert results.summary().delivered == 5 and not results.summary().finished
        assert list(results) == []
        assert not reader.closed and reader.stats().nbytes <= MIB


def test_an_abandoned_map_leaves_no_thread(image):
    path, mapping = image
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, [(i, i + 8, 0, 8) for i in range(50)], channels=[0], workers=4)
        results = map_windows(plan, lambda b, w: time.sleep(0.005), memory_budget=BUDGET)
        next(results)
        assert leftover_threads()
        del results
        gc.collect()
        assert not leftover_threads()
        never = map_windows(plan, digest, memory_budget=BUDGET)
        never.close()  # never started: no thread was made
        assert not leftover_threads() and never.summary().delivered == 0


def test_a_reader_closed_meanwhile_stops_the_map_at_a_named_window(image):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(40)]
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=2, max_in_flight=2)
        results = map_windows(plan, lambda b, w: b, memory_budget=BUDGET)
        assert next(results) == windows[0]
        reader.close()
        with pytest.raises(WindowReaderClosedError) as error:
            list(results)
        assert "in a window map" in error.value.__notes__[0]
        assert reader.stats().pieces == 0
        with pytest.raises(WindowReaderClosedError):
            map_plan(reader, windows, channels=[0], workers=1)
    assert not leftover_threads()
