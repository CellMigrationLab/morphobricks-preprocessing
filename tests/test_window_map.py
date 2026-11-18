"""A function mapped over windows of one image in threads (``window_map``, lazy-windows §5).

Results come in window order for any number of workers, bitwise those of the reader alone;
memory is planned and checked against a budget before any pixel is read; an error or an early
stop cancels what is not started and leaves no thread and a usable reader.
"""

import gc
import hashlib
import threading
import time
import weakref

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
    finished, seventh_done, waited = [], threading.Event(), []
    lock = threading.Lock()

    def first_last(bounds, window):
        if bounds[0] == 0:  # windows 0 to 7 run together in 8 threads: 0 waits for 7
            waited.append(seventh_done.wait(10))
        with lock:
            finished.append(bounds[0])
        if bounds[0] == 7:
            seventh_done.set()
        return bounds[0]

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=8, max_in_flight=16)
        assert list(map_windows(plan, first_last, memory_budget=BUDGET)) == list(range(24))
    assert waited == [True]
    assert finished.index(7) < finished.index(0)  # completion order was not window order


def test_no_more_than_max_in_flight_windows_are_read_ahead(image, reads):
    path, mapping = image
    windows = [(i, i + 8, 0, 8) for i in range(30)]
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, windows, channels=[0], workers=3, max_in_flight=5)
        for k, _ in enumerate(map_windows(plan, lambda b, w: b, memory_budget=BUDGET)):
            # A slow consumer: wait until the workers have started every window they may.
            expected = min(k + 5, len(windows))  # the result in hand and four read ahead
            deadline = time.monotonic() + 10
            while len(reads) < expected and time.monotonic() < deadline:
                time.sleep(0.002)
            time.sleep(0.005)  # time to overshoot, were the bound not kept
            assert len(reads) == expected, k
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
        assert plan.held_bytes == 2 * H * W  # the whole plane, two uint8 channels
        assert plan.memory_estimate == plan.in_flight_bytes + plan.held_bytes + 3 * MIB
        wide = map_plan(reader, windows, channels=[0, 1], workers=2, max_in_flight=9)
        assert wide.in_flight_bytes == sum(alone)  # never more windows than there are
        assert (wide.workers, wide.max_in_flight) == (2, 9)


@pytest.mark.parametrize(("workers", "max_in_flight"), [(1, 1), (2, 2), (2, 3)])
def test_the_plan_counts_every_window_alive_with_the_one_the_consumer_holds(
    image, workers, max_in_flight
):
    """Reads in progress and window arrays alive never exceed max_in_flight + 1 (finding 2)."""
    path, mapping = image
    windows = [(0, 64, 0, 64)] * 12
    lock = threading.Lock()
    count = {"reading": 0, "alive": 0, "peak": 0}

    def note(**steps):
        with lock:
            for key, step in steps.items():
                count[key] += step
            count["peak"] = max(count["peak"], count["reading"] + count["alive"])

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        original = reader.read_window

        def tracked(**kwargs):
            note(reading=1)
            time.sleep(0.01)  # slow reads: every slot is reading while the consumer holds one
            window = original(**kwargs)
            note(reading=-1, alive=1)  # the read's slot becomes its window's
            weakref.finalize(window, note, alive=-1)
            return window

        reader.read_window = tracked
        plan = map_plan(
            reader, windows, channels=[0, 1, 2], workers=workers, max_in_flight=max_in_flight
        )
        for _, window in iter_windows(plan, memory_budget=BUDGET):
            assert window.shape == (3, 64, 64)  # the consumer keeps only the current window
        del window
    assert count["alive"] == 0
    assert count["peak"] <= max_in_flight + 1
    alone = plan.window_bytes  # each alive window or read is within it
    assert plan.held_bytes <= alone
    assert plan.memory_estimate >= (max_in_flight + 1) * plan.held_bytes + plan.cache_bytes
    assert plan.memory_estimate == max_in_flight * alone + plan.held_bytes + plan.cache_bytes


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


@pytest.fixture
def no_collector():
    """Run with the cycle collector off: cleanup must come from reference counts alone."""
    was = gc.isenabled()
    gc.disable()
    yield
    if was:
        gc.enable()


def started_map(reader):
    plan = map_plan(reader, [(i, i + 8, 0, 8) for i in range(50)], channels=[0], workers=4)
    results = map_windows(plan, lambda b, w: time.sleep(0.005), memory_budget=BUDGET)
    next(results)
    assert len(leftover_threads()) == 4
    return results


def test_a_dropped_map_leaves_no_thread_without_the_collector(image, no_collector):
    path, mapping = image
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        results = started_map(reader)
        del results  # no close(), no gc.collect(): the last reference ends the threads
        assert not leftover_threads()
        never = map_windows(
            map_plan(reader, [], channels=[0], workers=1), digest, memory_budget=BUDGET
        )
        never.close()  # never started: no thread was made
        assert not leftover_threads() and never.summary().delivered == 0


def test_a_break_in_a_function_leaves_no_thread_without_the_collector(image, no_collector):
    path, mapping = image

    def first(reader):
        plan = map_plan(reader, [(i, i + 8, 0, 8) for i in range(50)], channels=[0], workers=4)
        for result in map_windows(plan, lambda b, w: b, memory_budget=BUDGET):
            assert leftover_threads()
            return result
        return None

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        assert first(reader) == (0, 8, 0, 8)
        assert not leftover_threads()
        for _, kept in iter_windows(
            map_plan(reader, [(0, 8, 0, 8)] * 20, channels=[0], workers=3), memory_budget=BUDGET
        ):
            assert kept.shape == (1, 8, 8)
            break
        del kept
        assert not leftover_threads()


def test_a_map_closed_from_one_of_its_own_threads_does_not_wait_for_itself(image):
    path, mapping = image
    holder, errors, closed, delivered = [], [], threading.Event(), threading.Event()

    def closing(bounds, window):
        if bounds[0] == 2:
            assert delivered.wait(10)  # the consumer is between two results, not asking
            try:
                holder[0].close()  # in a pool thread: it cannot join itself
            except BaseException as exc:
                errors.append(exc)
            closed.set()
        return bounds

    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        plan = map_plan(reader, [(i, i + 8, 0, 8) for i in range(20)], channels=[0], workers=2)
        holder.append(map_windows(plan, closing, memory_budget=BUDGET))
        assert next(holder[0]) == (0, 8, 0, 8)
        delivered.set()
        assert closed.wait(10)
        assert errors == []
        assert list(holder[0]) == []
        deadline = time.monotonic() + 10
        while leftover_threads() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not leftover_threads()


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


def test_a_plane_outside_a_file_is_refused_when_planned(tmp_path, reads):
    path = write_data(tmp_path / "a.tif", stored((2, 3, 32, 40), seed=5), "TCYX", tile=(16, 16))
    mapping = prefill_mapping("TCYX")
    with WindowReader(path, mapping, cache_bytes=MIB) as reader:
        for plane in ({"t": 2}, {"z": 1}, {"t": -1}):
            with pytest.raises(IndexError):
                map_plan(reader, [(0, 8, 0, 8)], channels=[0], workers=1, **plane)
        plan = map_plan(reader, [(0, 8, 0, 8)], channels=[2], workers=1, t=1)
        (window,) = map_windows(plan, lambda b, w: w, memory_budget=np.int64(BUDGET))
        assert (
            window.tobytes()
            == read_window(path, mapping, bounds=(0, 8, 0, 8), channels=[2], t=1).tobytes()
        )
        with pytest.raises(ValueError, match="memory budget"):
            map_windows(plan, digest, memory_budget=np.float64(BUDGET))
    assert len(reads) == 1
