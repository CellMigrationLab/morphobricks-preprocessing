"""Measure the peak memory of window reads in a fresh Python process (tests, #348).

``tests/test_window_memory.py`` checks that a window's memory estimate bounds the peak of its
read. Measured inside the pytest process, ``tracemalloc`` also sees what the process does for
itself during the read: when earlier tests have filled the interpreter's table of interned
strings, the path parts a read interns (``pathlib`` interns them, and a reader's fingerprint
check makes a path of every file of an OME-Zarr store) can make Python rebuild that table, one
allocation of several MiB that belongs to the process, not to the read, and that lands in
whichever read happens to be measured at that moment (#348). A fresh process holds only what
its imports hold, the same in any test order, so the peaks are measured here, in a child
process, and returned as JSON::

    python tests/window_peaks.py '{"path": ..., "axes": "CYX", "channels": [2, 0], ...}'

Every result is ``[what, bounds, cache, peak, estimate]``; the test asserts ``peak <=
estimate`` for each one. A request may also give ``windows`` and ``caches`` (the defaults are
:data:`WINDOWS` and :data:`CACHES`) and ``plane`` as ``[rows, columns]``. With ``"parts":
true`` the parts of a TIFF piece's estimate are measured alone instead (:func:`parts`).

"""

from __future__ import annotations

import functools
import json
import os
import sys
import tracemalloc
from pathlib import Path

WINDOWS = [(0, 64, 0, 64), (37, 190, 45, 256), (0, 256, 0, 256)]
CACHES = (2**16, 2**20)
BOOKKEEPING = 16 * 2**10
"""The Python objects a cache makes around a frozen piece (the reader's bookkeeping, counted in
``window_memory.OPEN_BYTES``), allowed over the frozen copy itself in :func:`parts`."""


def _peak(read):
    """Return the peak traced memory of ``read()`` above what was held when it started."""
    tracemalloc.start()
    try:
        start = tracemalloc.get_traced_memory()[0]
        read()
        return tracemalloc.get_traced_memory()[1] - start
    finally:
        tracemalloc.stop()


def _filled(path, mapping, cache, plane, read):
    """Open a reader of ``cache`` bytes and fill it; return it and the bytes it holds."""
    from morphobricks_preprocessing.window_reader import WindowReader

    reader = WindowReader(path, mapping, cache_bytes=cache)
    reader.read_window(bounds=(0, plane[0], 0, plane[1]), **read)
    return reader, reader.stats().nbytes


def _cached_peak(path, mapping, cache, plane, bounds, read):
    """Return a filled reader's estimate and the peak of reading ``bounds`` through it."""
    tracemalloc.start()  # from before the cache is filled: its pieces are traced
    try:
        reader, held = _filled(path, mapping, cache, plane, read)
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        reader.read_window(bounds=bounds, **read)
        peak = tracemalloc.get_traced_memory()[1] - start + held
    finally:
        tracemalloc.stop()
    return reader.window_plan(bounds=bounds, channels=read["channels"]).memory_estimate, peak


def measure(path, axes, channels, plane, coordinates=None, windows=WINDOWS, caches=CACHES):
    """Return the peak and the estimate of every window, uncached and through a cache."""
    from morphobricks_preprocessing import prefill_mapping
    from morphobricks_preprocessing.windows import read_window, window_plan

    path = Path(path)
    mapping, read = prefill_mapping(axes), {"channels": channels, **(coordinates or {})}
    rows, cols = (plane, plane) if isinstance(plane, int) else plane
    read_window(path, mapping, bounds=(0, 1, 0, 1), **read)  # imports and first-use state
    results = []
    for y0, y1, x0, x1 in windows:
        bounds = (min(y0, rows - 1), min(y1, rows), min(x0, cols - 1), min(x1, cols))
        plan = window_plan(path, mapping, bounds=bounds, channels=channels)
        peak = _peak(functools.partial(read_window, path, mapping, bounds=bounds, **read))
        results.append(["read", bounds, 0, peak, plan.memory_estimate])
        for cache in caches:
            estimate, peak = _cached_peak(path, mapping, cache, (rows, cols), bounds, read)
            results.append(["cached", bounds, cache, peak, estimate])
    return results


def parts(path):
    """Return the parts of a TIFF piece's estimate measured alone, as ``[part, peak, bound]``.

    ``decode``: decoding the first piece, held encoded, against the codec's state and the
    piece work (with a tiny piece, the codec's state alone); ``frozen``: what a window cache
    adds over a decoded piece it keeps, against the frozen copies (and :data:`BOOKKEEPING`).
    """
    import tifffile

    from morphobricks_preprocessing import window_memory
    from morphobricks_preprocessing.block_cache import BlockCache
    from morphobricks_preprocessing.tiff_pieces import _decode_segment

    with tifffile.TiffFile(path) as tif:
        page = tif.series[0].keyframe
        _decode_segment(page, 0, path)  # first-use state
        held = []
        decode = _peak(lambda: held.append(_decode_segment(page, 0, path)))
        piece, encoded = held[0].nbytes, int(page.databytecounts[0])
        work = window_memory.tiff_piece_work(piece, encoded, cached=False)
        found = [["decode", decode, window_memory.codec_state(page.compression) + work]]
        cache = BlockCache(2 * piece)
        frozen = _peak(lambda: cache.get("piece", lambda: held[0]))
        found.append(["frozen", frozen, window_memory.FROZEN_COPIES * piece + BOOKKEEPING])
    return found


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    if request.pop("parts", False):
        found = parts(request["path"])
    else:
        found = measure(**request)
    answer = {"pid": os.getpid(), "pytest": "pytest" in sys.modules, "results": found}
    print(json.dumps(answer))
