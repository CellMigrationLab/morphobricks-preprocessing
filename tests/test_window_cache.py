"""A bounded cache of decoded pieces shared by the windows of one image (#304).

``WindowReader`` and ``RunInputs.window_reader`` read windows bitwise as ``read_window`` does,
decoding each compressed piece about once for a whole grid; the cache stays under its bound,
counts in the memory estimate and never serves an image that changed.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

import test_window_decodes
import test_windows
from described_tree import run_described
from morphobricks_preprocessing import prefill_mapping, run_inputs_window, tiff_pieces
from morphobricks_preprocessing.block_cache import BlockCache
from morphobricks_preprocessing.run_inputs import RunInputs, RunInputsError
from morphobricks_preprocessing.testing import make_clean_run
from morphobricks_preprocessing.window_reader import WindowReader, WindowReaderClosedError
from morphobricks_preprocessing.windows import (
    WindowMemoryError,
    WindowSourceChangedError,
    read_window,
    window_plan,
)
from test_windows import rgb, stored
from tiff_tree import write_data
from zarr_tree import write_ngff

MIB = 2**20
LAYOUTS = {
    **{k: (w, a, n, c) for k, (w, a, n, c) in test_windows.LAYOUTS.items()},
    **{k: (w, a, n, {}) for k, (w, a, n) in test_window_decodes.LAYOUTS.items()},
}


@pytest.fixture
def decodes(monkeypatch):
    """Count the compressed TIFF pieces decoded."""
    found = []
    original = tiff_pieces._decode_segment

    def counting(page, index, path):
        found.append(index)
        return original(page, index, path)

    monkeypatch.setattr(tiff_pieces, "_decode_segment", counting)
    return found


@pytest.mark.parametrize("name", sorted(LAYOUTS))
def test_windows_through_the_cache_are_bitwise_those_without_it(tmp_path, name):
    write, axes, count, coordinates = LAYOUTS[name]
    path = write(tmp_path / ("a.zarr" if "Zarr" in name else "a.tif"))
    mapping = prefill_mapping(axes)
    reader = WindowReader(path, mapping, cache_bytes=4 * MIB)
    every = list(range(count))
    asks = [
        (bounds, channels)
        for bounds in test_windows.WINDOWS
        for channels in ([0], every, every[::-1], every[1:] + every[:1])
    ]
    order = np.random.default_rng(3).permutation(len(asks))
    for i in [*order, *order[::-1]]:  # twice: the second time from the cache
        bounds, channels = asks[i]
        cached = reader.read_window(bounds=bounds, channels=channels, **coordinates)
        plain = read_window(path, mapping, bounds=bounds, channels=channels, **coordinates)
        assert cached.dtype == plain.dtype and cached.shape == plain.shape
        assert cached.tobytes() == plain.tobytes(), (name, bounds, channels)
    stats = reader.stats()
    assert stats.nbytes <= stats.max_bytes


# A grid of windows with margins ------------------------------------------------------------

SIDE, TILE, STEP, MARGIN = 1024, 128, 128, 20


def grid(side=SIDE, step=STEP, margin=MARGIN):
    return [
        (
            max(y - margin, 0),
            min(y + step + margin, side),
            max(x - margin, 0),
            min(x + step + margin, side),
        )
        for y in range(0, side, step)
        for x in range(0, side, step)
    ]


@pytest.fixture(scope="module")
def rgb_tiles(tmp_path_factory):
    data = stored((SIDE, SIDE, 3), "uint8", seed=9)
    path = rgb(
        tmp_path_factory.mktemp("tiles") / "a.tif", data, tile=(TILE, TILE), compression="zlib"
    )
    return path, data


def test_a_grid_read_by_threads_decodes_each_tile_once(rgb_tiles, decodes):
    path, data = rgb_tiles
    mapping = prefill_mapping("YXS")
    windows = grid()
    order = list(np.random.default_rng(5).permutation(len(windows)))
    for bounds in windows:
        read_window(path, mapping, bounds=bounds, channels=[0, 1, 2])
    without = len(decodes)
    decodes.clear()
    reader = WindowReader(path, mapping, cache_bytes=64 * MIB)

    def read(i):
        return i, reader.read_window(bounds=windows[i], channels=[2, 0, 1])

    with ThreadPoolExecutor(4) as pool:
        results = dict(pool.map(read, order))
    for i, (y0, y1, x0, x1) in enumerate(windows):
        expected = np.moveaxis(data[y0:y1, x0:x1][..., [2, 0, 1]], -1, 0)
        assert results[i].tobytes() == np.ascontiguousarray(expected).tobytes()
    tiles = (SIDE // TILE) ** 2
    assert sorted(decodes) == list(range(tiles))  # each tile once, whatever the threads
    stats = reader.stats()
    assert (stats.decodes, stats.pieces, stats.evictions) == (tiles, tiles, 0)
    assert without > 2 * tiles  # without the cache, the margins decode tiles again


def test_the_cache_stays_under_its_bound_and_drops_the_least_recently_used(rgb_tiles):
    path, data = rgb_tiles
    tile_bytes = TILE * TILE * 3
    reader = WindowReader(path, prefill_mapping("YXS"), cache_bytes=5 * tile_bytes)
    for y0, y1, x0, x1 in grid():
        window = reader.read_window(bounds=(y0, y1, x0, x1), channels=[0, 1, 2])
        assert (
            window.tobytes()
            == np.ascontiguousarray(np.moveaxis(data[y0:y1, x0:x1], -1, 0)).tobytes()
        )
        stats = reader.stats()
        assert stats.nbytes <= 5 * tile_bytes and stats.pieces <= 5
    assert stats.evictions > 0 and stats.decodes > (SIDE // TILE) ** 2


def test_block_cache_bound_and_waiting_threads():
    cache = BlockCache(100)
    piece = cache.get("a", lambda: np.zeros(60, np.uint8))
    assert not piece.flags.writeable
    cache.get("b", lambda: np.zeros(30, np.uint8))
    cache.get("a", lambda: pytest.fail("a is held"))  # a is now the most recently used
    cache.get("c", lambda: np.zeros(30, np.uint8))  # 120 bytes: b is dropped, not a
    assert cache.get("a", lambda: pytest.fail("a is held")) is piece
    assert cache.stats().evictions == 1 and cache.stats().nbytes == 90
    assert cache.get("big", lambda: np.zeros(101, np.uint8)).nbytes == 101
    assert cache.stats().nbytes == 90  # a piece over the bound is used, never kept
    started, release, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        started.set()
        release.wait(5)
        return np.ones(10, np.uint8)

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(cache.get, "slow", slow)
        started.wait(5)
        others = [pool.submit(cache.get, "slow", slow) for _ in range(2)]
        release.set()
        pieces = [first.result(), *(f.result() for f in others)]
    assert len(calls) == 1 and all(p is pieces[0] for p in pieces)
    for bad in (0, -1, 1.5, True, None):
        with pytest.raises(ValueError):
            BlockCache(bad)


def test_a_zarr_grid_decodes_each_chunk_once(tmp_path, monkeypatch):
    from morphobricks_preprocessing import windows as windows_module

    data = stored((4, 256, 256), seed=10)
    path = write_ngff(tmp_path / "a.zarr", data, "cyx", chunks=(2, 32, 32))
    mapping = prefill_mapping("CYX")
    reads = []
    original = windows_module._zarr_read

    def spy(*a):
        # Slices are hashable only from Python 3.12: record each selection as plain tuples.
        reads.append(tuple((s.start, s.stop, s.step) if isinstance(s, slice) else s for s in a[1]))
        return original(*a)

    monkeypatch.setattr(windows_module, "_zarr_read", spy)
    reader = WindowReader(path, mapping, cache_bytes=8 * MIB)
    for y0, y1, x0, x1 in grid(256, 32, 5):
        window = reader.read_window(bounds=(y0, y1, x0, x1), channels=[3, 0, 1])
        assert window.tobytes() == data[[3, 0, 1], y0:y1, x0:x1].tobytes()
    assert len(reads) == len(set(reads)) == 2 * (256 // 32) ** 2  # every chunk once
    assert reader.stats().decodes == len(reads)


# Memory and identity ------------------------------------------------------------------------


def test_the_cache_counts_in_the_memory_estimate(rgb_tiles):
    path, _ = rgb_tiles
    mapping = prefill_mapping("YXS")
    bounds = (0, 300, 0, 300)
    plain = window_plan(path, mapping, bounds=bounds, channels=[0, 1, 2])
    reader = WindowReader(path, mapping, cache_bytes=MIB, memory_limit=2 * MIB)
    plan = reader.window_plan(bounds=bounds, channels=[0, 1, 2])
    frozen = TILE * TILE * 3  # the cache's immutable copy of the piece being decoded
    assert plan.memory_estimate == plain.memory_estimate + MIB + frozen
    small = WindowReader(
        path, mapping, cache_bytes=MIB, memory_limit=MIB + plain.memory_estimate + frozen - 1
    )
    with pytest.raises(WindowMemoryError, match="window cache"):
        small.read_window(bounds=bounds, channels=[0, 1, 2])
    with pytest.raises(WindowMemoryError, match="no room"):
        WindowReader(path, mapping, cache_bytes=MIB, memory_limit=MIB)


def test_an_image_changed_since_the_reader_opened_is_refused_and_dropped(tmp_path):
    data = stored((256, 256, 3), "uint8", seed=1)
    path = rgb(tmp_path / "a.tif", data, tile=(64, 64), compression="zlib")
    reader = WindowReader(path, prefill_mapping("YXS"), cache_bytes=4 * MIB)
    reader.read_window(bounds=(0, 100, 0, 100), channels=[0, 1, 2])
    assert reader.stats().pieces == 4
    rgb(path, data[::-1].copy(), tile=(64, 64), compression="zlib")  # rewritten
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(WindowSourceChangedError, match="changed"):
        reader.read_window(bounds=(0, 100, 0, 100), channels=[0, 1, 2])
    assert reader.stats().pieces == 0  # nothing of the old image is kept
    path.unlink()
    with pytest.raises(WindowSourceChangedError, match="cannot be found"):
        reader.read_window(bounds=(0, 100, 0, 100), channels=[0])


def test_an_image_changed_during_a_read_is_refused_not_returned(tmp_path, monkeypatch):
    data = stored((256, 256, 3), "uint8", seed=2)
    path = rgb(tmp_path / "a.tif", data, tile=(64, 64), compression="zlib")
    reader = WindowReader(path, prefill_mapping("YXS"), cache_bytes=4 * MIB)
    original = tiff_pieces._decode_segment

    def touching(page, index, where):
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
        return original(page, index, where)

    monkeypatch.setattr(tiff_pieces, "_decode_segment", touching)
    with pytest.raises(WindowSourceChangedError):
        reader.read_window(bounds=(0, 100, 0, 100), channels=[0, 1, 2])
    assert reader.stats().pieces == 0


@pytest.fixture
def clean(tmp_path):
    return make_clean_run(tmp_path)


def test_a_run_window_reader_gives_the_run_windows(clean):
    inputs = RunInputs.open(clean.output)
    for fov in inputs.fovs():
        ids = list(fov.channel_ids)
        reader = inputs.window_reader(fov, cache_bytes=MIB)
        for channels in ([ids[0]], ids, ids[::-1]):
            for bounds in ((3, 11, 0, 17), (0, 16, 0, 20), (5, 6, 7, 8)):
                cached = reader.read_window(t=0, z=0, bounds=bounds, channels=channels)
                plain = inputs.read_window(fov, t=0, z=0, bounds=bounds, channels=channels)
                assert cached.tobytes() == plain.tobytes()


def test_a_run_window_reader_refuses_a_changed_image(clean):
    inputs = RunInputs.open(clean.output)
    fov = inputs.fovs()[0]
    reader = inputs.window_reader(fov, cache_bytes=MIB)
    reader.read_window(t=0, z=0, bounds=(0, 4, 0, 4), channels=fov.channel_ids)
    path = clean.experiment / fov.relative_path
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(RunInputsError, match="changed"):
        reader.read_window(t=0, z=0, bounds=(0, 4, 0, 4), channels=fov.channel_ids)
    with pytest.raises(RunInputsError, match="changed"):
        inputs.window_reader(fov, cache_bytes=MIB)


@pytest.mark.parametrize("kind", ["tiled tiff", "ome-zarr"])
def test_a_run_window_reader_of_chunked_images(tmp_path, kind):
    data = np.random.default_rng(2).integers(0, 4000, (2, 3, 40, 52), dtype=np.uint16)
    if kind == "ome-zarr":
        write_ngff(tmp_path / "exp/ctrl/a.zarr", data, "tcyx", chunks=(1, 2, 16, 16))
    else:
        write_data(tmp_path / "exp/ctrl/a.tif", data, "TCYX", tile=(16, 16), compression="zlib")
    result = run_described(tmp_path / "exp", prefill_mapping("TCYX"))
    assert result.written, result.issues
    inputs = RunInputs.open(result.output_folder)
    (fov,) = inputs.fovs()
    ids = fov.channel_ids
    reader = inputs.window_reader(fov, cache_bytes=MIB)
    for t in (1, 0, 1):
        for bounds in ((10, 40, 7, 33), (0, 40, 0, 52), (3, 20, 30, 50)):
            window = reader.read_window(t=t, z=0, bounds=bounds, channels=[ids[2], ids[0], ids[1]])
            y0, y1, x0, x1 = bounds
            assert window.tobytes() == data[t, [2, 0, 1], y0:y1, x0:x1].tobytes()
    stats = reader.stats()
    assert stats.hits > 0 and stats.decodes <= stats.pieces + stats.evictions


def test_a_closed_reader_drops_its_cache_and_refuses_every_later_window(tmp_path):
    path = tmp_path / "a.tif"
    write_data(
        path,
        np.arange(64 * 64, dtype=np.uint16).reshape(64, 64),
        "YX",
        tile=(16, 16),
        compression="zlib",
    )
    with WindowReader(path, prefill_mapping("YX"), cache_bytes=MIB) as reader:
        reader.read_window(bounds=(0, 32, 0, 32), channels=[0])
        assert reader.stats().pieces == 4 and not reader.closed
    assert reader.closed and reader.stats().pieces == reader.stats().nbytes == 0
    for ask in (reader.read_window, reader.window_plan):
        with pytest.raises(WindowReaderClosedError, match="closed"):
            ask(bounds=(0, 4, 0, 4), channels=[0])
    reader.close()  # closing twice does nothing more
    assert reader.closed


def test_a_reader_is_closed_when_its_with_block_fails(tmp_path):
    path = tmp_path / "a.tif"
    write_data(path, np.ones((32, 32), dtype=np.uint8), "YX", tile=(16, 16), compression="zlib")
    with pytest.raises(RuntimeError, match="stop"):
        with WindowReader(path, prefill_mapping("YX"), cache_bytes=MIB) as reader:
            reader.read_window(bounds=(0, 32, 0, 32), channels=[0])
            raise RuntimeError("stop")
    assert reader.closed and reader.stats().pieces == 0


def test_a_cache_closed_during_a_decode_returns_the_piece_and_keeps_nothing():
    cache = BlockCache(MIB)
    piece = np.arange(10)

    def decode():
        cache.close()  # the reader is closed while a thread decodes
        return piece

    got = cache.get("k", decode)
    assert got.tobytes() == piece.tobytes() and not got.flags.writeable
    assert cache.stats().pieces == 0 and cache.stats().decodes == 1
    assert cache.get("k", lambda: piece.copy()).tobytes() == piece.tobytes()
    assert cache.stats().pieces == 0


def test_a_run_window_reader_plans_closes_and_refuses_after(clean):
    inputs = RunInputs.open(clean.output)
    fov = inputs.fovs()[0]
    ids = list(fov.channel_ids)
    with inputs.window_reader(fov, cache_bytes=MIB) as reader:
        plan = reader.window_plan(t=0, z=0, bounds=(3, 11, 0, 17), channels=ids[::-1])
        alone = window_plan(
            clean.experiment / fov.relative_path,
            run_inputs_window._mapping(inputs.manifest.file(fov.relative_path)),
            bounds=(3, 11, 0, 17),
            channels=[inputs.channel_index(fov, c) for c in ids[::-1]],
        )
        assert plan.shape == (len(ids), 8, 17) and plan.memory_estimate == (
            alone.memory_estimate + MIB
        )
        with pytest.raises(RunInputsError):
            reader.window_plan(t=0, z=0, bounds=(0, 4, 0, 4), channels=["nope"])
        reader.read_window(t=0, z=0, bounds=(0, 4, 0, 4), channels=ids)
    assert reader.closed and reader.stats().pieces == 0
    with pytest.raises(WindowReaderClosedError):
        reader.read_window(t=0, z=0, bounds=(0, 4, 0, 4), channels=ids)
    with pytest.raises(WindowReaderClosedError):
        reader.window_plan(t=0, z=0, bounds=(0, 4, 0, 4), channels=ids)


class _CountingEvent(threading.Event):
    """An event that counts the threads waiting on it."""

    def __init__(self):
        super().__init__()
        self.waiting = 0

    def wait(self, timeout=None):
        self.waiting += 1
        return super().wait(timeout)


def _overlapping(monkeypatch, cache, key, decode, waiters=2):
    """Ask ``key`` from 1 + ``waiters`` threads while the first decode is in flight."""
    from morphobricks_preprocessing import block_cache

    flights = []

    class Flight(block_cache._Flight):
        def __init__(self):
            super().__init__(done=_CountingEvent())
            flights.append(self)

    monkeypatch.setattr(block_cache, "_Flight", Flight)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return decode()

    with ThreadPoolExecutor(1 + waiters) as pool:
        first = pool.submit(cache.get, key, slow)
        started.wait(5)
        others = [pool.submit(cache.get, key, slow) for _ in range(waiters)]
        while flights[0].done.waiting < waiters:  # every other request waits on the decode
            threading.Event().wait(0.01)
        release.set()
        return [first, *others]


@pytest.mark.parametrize("size", [101, 1000])
def test_waiters_share_a_decode_the_cache_does_not_keep(monkeypatch, size):
    """Single-flight covers a piece over the bound: overlapping requests decode it once."""
    cache, calls = BlockCache(100), []

    def decode():
        calls.append(1)
        return np.arange(size, dtype=np.uint8)

    futures = _overlapping(monkeypatch, cache, "big", decode)
    pieces = [f.result() for f in futures]
    assert len(calls) == 1 and all(p is pieces[0] for p in pieces)
    assert not pieces[0].flags.writeable and cache.stats().pieces == 0
    with pytest.raises(ValueError):  # shared but not kept: immutable too
        pieces[0].setflags(write=True)
    assert cache.stats().decodes == 1 and cache.stats().hits == 2
    cache.get("big", decode)  # a later request: not held, so decoded again
    assert len(calls) == 2


def test_waiters_share_a_decode_dropped_by_a_close(monkeypatch):
    cache, calls = BlockCache(MIB), []

    def decode():
        calls.append(1)
        cache.close()  # dropped at once: nothing is kept
        return np.ones(10, np.uint8)

    pieces = [f.result() for f in _overlapping(monkeypatch, cache, "k", decode)]
    assert len(calls) == 1 and all(p is pieces[0] for p in pieces)
    with pytest.raises(ValueError):  # dropped by the close but shared: immutable too
        pieces[0].setflags(write=True)


def test_a_failed_decode_is_not_shared_a_waiter_decodes_again(monkeypatch):
    cache, calls = BlockCache(MIB), []

    def decode():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("first decode fails")
        return np.ones(10, np.uint8)

    first, *others = _overlapping(monkeypatch, cache, "k", decode)
    with pytest.raises(OSError, match="first"):
        first.result()
    assert all(f.result().tobytes() == b"\x01" * 10 for f in others)
    assert len(calls) == 2  # one waiter decoded again, the other shared its decode


def test_a_kept_piece_and_its_storage_can_never_be_written():
    cache = BlockCache(MIB)
    decoded = np.arange(12, dtype=np.uint16).reshape(3, 4)[:, ::2]  # not contiguous
    piece = cache.get("k", lambda: decoded)
    assert piece.tobytes() == decoded.tobytes() and piece.shape == decoded.shape
    base = piece
    while isinstance(base, np.ndarray):
        base = base.base
    assert isinstance(base, bytes)  # immutable storage
    with pytest.raises(ValueError):
        piece.setflags(write=True)
    with pytest.raises(ValueError):
        piece[0, 0] = 99
    decoded[0, 0] = 99  # the decoder's own array is not the cached one
    assert cache.get("k", lambda: pytest.fail("held"))[0, 0] == 0


def test_a_window_is_a_new_array_changing_it_changes_no_cached_piece(rgb_tiles):
    path, data = rgb_tiles
    reader = WindowReader(path, prefill_mapping("YXS"), cache_bytes=4 * MIB)
    window = reader.read_window(bounds=(0, 20, 0, 20), channels=[0, 1, 2])
    assert window.flags.writeable
    window[...] = 0
    again = reader.read_window(bounds=(0, 20, 0, 20), channels=[0, 1, 2])
    assert reader.stats().hits > 0
    assert again.tobytes() == np.moveaxis(data[:20, :20], -1, 0).tobytes()


def test_a_piece_is_frozen_outside_the_lock_other_pieces_are_served_meanwhile(monkeypatch):
    """Freezing a large piece never holds the cache's lock: other threads keep reading."""
    from morphobricks_preprocessing import block_cache

    cache = BlockCache(MIB)
    held = cache.get("held", lambda: np.ones(10, np.uint8))
    freezing, release = threading.Event(), threading.Event()
    original = block_cache._frozen

    def slow(piece):
        if piece.size == 1000:
            freezing.set()
            assert release.wait(5)
        return original(piece)

    monkeypatch.setattr(block_cache, "_frozen", slow)
    with ThreadPoolExecutor(2) as pool:
        big = pool.submit(cache.get, "big", lambda: np.zeros(1000, np.uint8))
        assert freezing.wait(5)
        other = pool.submit(cache.get, "held", lambda: pytest.fail("held"))
        assert other.result(timeout=5) is held  # served while "big" is being frozen
        assert cache.stats().pieces == 1  # "big" is not kept before it is frozen
        release.set()
        assert big.result(timeout=5).tobytes() == bytes(1000)
    assert cache.stats().pieces == 2 and cache.stats().nbytes <= cache.max_bytes


def test_the_pieces_held_never_exceed_the_bound_even_while_one_is_added():
    cache = BlockCache(100)
    for key in range(20):
        cache.get(key, lambda: np.zeros(30, np.uint8))
        assert cache.stats().nbytes <= 100 and cache._nbytes == sum(
            p.nbytes for p in cache._pieces.values()
        )
    assert cache.stats().pieces == 3 and cache.stats().evictions == 17
