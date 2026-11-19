"""Measure the peak memory of LZW and JPEG reads in a fresh Python process (#356).

LZW and JPEG are decoded by imagecodecs in C, whose ``malloc`` is not seen by ``tracemalloc``.
So ``tests/test_codec_memory.py`` measures each read three ways, here, in a child process
(``window_peaks`` explains why a fresh process)::

    python tests/codec_peaks.py '{"what": "windows", "path": ..., "axes": "YXS", ...}'

- **RSS** (Linux): the peak resident memory of the read over the resident memory before it.
  ``/proc/self/clear_refs`` resets the peak (``VmHWM``) to the current resident memory; freed
  memory is first returned to the system (``malloc_trim``), so the read's allocations are
  counted when they are touched, not hidden in memory the process already holds.
- **traced**: the peak ``tracemalloc`` sees, Python's allocations only (the read again).
- **heap** (``"what": "parts"``, glibc): the peak of the C heap (``mallinfo2``: bytes in use
  and mapped) while one piece is decoded, sampled from another thread while the decoder runs
  without the GIL; minus the traced peak of the same decode, it is the C library's own memory.

Every result is ``{"what", "bounds", "rss", "traced", "estimate", "native"}``; ``native`` is
the part of ``estimate`` the C library holds (``window_memory``), which ``traced`` cannot see.
``rss`` is None where it cannot be measured. The rebuild guard of ``window_peaks`` runs first.
"""

from __future__ import annotations

import ctypes
import gc
import itertools
import json
import os
import sys
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from window_peaks import force_intern_rebuild  # noqa: E402

try:
    _LIBC = ctypes.CDLL("libc.so.6")
except OSError:
    _LIBC = None


class _MallInfo2(ctypes.Structure):
    _fields_ = [  # noqa: RUF012
        (name, ctypes.c_size_t)
        for name in (
            "arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost".split()
        )
    ]


def _status(key):
    with open("/proc/self/status", encoding="ascii") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1]) * 1024
    raise OSError(f"no {key}")


def rss_works():
    """Return whether the peak resident memory can be reset and read (Linux)."""
    try:
        with open("/proc/self/clear_refs", "w", encoding="ascii") as f:
            f.write("5")
        return _status("VmHWM") > 0
    except OSError:
        return False


def _settle():
    gc.collect()
    if _LIBC is not None and hasattr(_LIBC, "malloc_trim"):
        _LIBC.malloc_trim(0)


def rss_peak(run, base=None):
    """Return the peak resident memory of ``run()`` over ``base`` (default: before it)."""
    if not RSS:
        return None
    _settle()
    base = _status("VmRSS") if base is None else base
    with open("/proc/self/clear_refs", "w", encoding="ascii") as f:
        f.write("5")  # VmHWM := VmRSS
    run()
    return _status("VmHWM") - base


def traced_peak(run):
    """Return the peak traced memory of ``run()`` above what was traced when it started."""
    _settle()
    tracemalloc.start()
    try:
        start = tracemalloc.get_traced_memory()[0]
        run()
        return tracemalloc.get_traced_memory()[1] - start
    finally:
        tracemalloc.stop()


def heap_peak(run):
    """Return the peak of the C heap in use while ``run()`` runs, sampled (glibc)."""
    info = _LIBC.mallinfo2
    info.restype = _MallInfo2

    def used():
        found = info()
        return found.uordblks + found.hblkhd

    _settle()
    base, peak, done = used(), [0], threading.Event()

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], used())

    sampler = threading.Thread(target=sample)
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)  # the sampler gets the GIL between the decoder's C calls
    sampler.start()
    try:
        run()
    finally:
        done.set()
        sampler.join()
        sys.setswitchinterval(interval)
    return max(peak[0], used()) - base


def _keyframe(path):
    import tifffile

    with tifffile.TiffFile(path) as tif:
        return tif.series[0].keyframe


def _native(path, threads=1, pieces=None):
    """Return the C library's part of a read's estimate: its state and its pieces' buffers.

    ``pieces`` is how many pieces' buffers are counted (``window_memory.JPEG_NATIVE_PIECES``
    in a read; one when a single piece is decoded alone).
    """
    from morphobricks_preprocessing import window_memory
    from morphobricks_preprocessing.tiff_pieces import piece_decode

    pieces = window_memory.JPEG_NATIVE_PIECES if pieces is None else pieces
    decode = piece_decode(_keyframe(path))
    work = pieces * window_memory.native_piece_work(decode)
    return threads * (window_memory.codec_state(decode.compression) + work)


def windows(path, axes, channels, plane, windows, cache, cached):
    """Measure window reads: uncached, or through one reader filled with the whole plane.

    Uncached and cached reads are measured in processes of their own: a cache's fill frees
    many pieces, after which glibc serves the next reads from where they were (its mmap
    threshold rises), a state of the process before the read, not of the read.
    """
    from morphobricks_preprocessing import prefill_mapping
    from morphobricks_preprocessing.windows import read_window, window_plan

    mapping, read = prefill_mapping(axes), {"channels": channels}
    read_window(path, mapping, bounds=(0, 1, 0, 1), **read)  # imports, first-use state
    force_intern_rebuild()
    native, results, kept = _native(path), [], []
    whole = (0, plane[0], 0, plane[1])
    if cached:
        rss = _cached_rss(path, mapping, read, cache, whole, windows) if RSS else None
        for i, bounds in enumerate(map(tuple, windows)):
            traced, estimate = _cached_traced(path, mapping, read, cache, whole, bounds)
            results.append(["cached", bounds, rss and rss[i], traced, estimate, native])
        return results
    for bounds in map(tuple, windows):
        estimate = window_plan(path, mapping, bounds=bounds, **read).memory_estimate

        def once(bounds=bounds):
            kept.append(read_window(path, mapping, bounds=bounds, **read))

        rss = rss_peak(once)
        kept.clear()
        traced = traced_peak(once)
        kept.clear()
        results.append(["read", bounds, rss, traced, estimate, native])
    return results


def _cached_rss(path, mapping, read, cache, whole, windows):
    """Return the RSS peaks of window reads through one reader filled with the whole plane."""
    from morphobricks_preprocessing.window_reader import WindowReader

    _settle()
    start = _status("VmRSS")  # before the reader: its cache counts
    peaks = []
    with WindowReader(path, mapping, cache_bytes=cache) as reader:
        reader.read_window(bounds=whole, **read)
        kept = []
        for bounds in map(tuple, windows):
            peaks.append(
                rss_peak(lambda b=bounds: kept.append(reader.read_window(bounds=b, **read)), start)
            )
            kept.clear()
    return peaks


def _cached_traced(path, mapping, read, cache, whole, bounds):
    """Return the traced peak (and the estimate) of a window read through a filled reader."""
    from morphobricks_preprocessing.window_reader import WindowReader

    _settle()
    tracemalloc.start()  # from before the cache is filled: its pieces are traced
    try:
        start = tracemalloc.get_traced_memory()[0]
        with WindowReader(path, mapping, cache_bytes=cache) as reader:
            reader.read_window(bounds=whole, **read)
            tracemalloc.reset_peak()
            kept = reader.read_window(bounds=bounds, **read)
            peak = tracemalloc.get_traced_memory()[1] - start
            del kept
            estimate = reader.window_plan(bounds=bounds, **read).memory_estimate
    finally:
        tracemalloc.stop()
    return peak, estimate


def plane(path, axes):
    """Measure every channel of a plane at once (``read_xyc_plane``), with tifffile's threads."""
    from morphobricks_preprocessing import prefill_mapping
    from morphobricks_preprocessing.plane_channels import read_xyc_plane, xyc_plane_memory

    mapping, kept = prefill_mapping(axes), []
    estimate = xyc_plane_memory(path, mapping)
    read_xyc_plane(path, mapping, memory_limit=2**40)  # imports, codecs, threads
    force_intern_rebuild()

    def once():
        kept.append(read_xyc_plane(path, mapping, memory_limit=2**40))

    rss = rss_peak(once)
    kept.clear()
    traced = traced_peak(once)
    threads = max(1, int(_keyframe(path).maxworkers))
    return [["plane", threads, rss, traced, estimate, _native(path, threads)]]


def blocks(path, axes, limit):
    """Measure reading the blocks of the first plane over ``limit`` (``iter_planes``)."""
    from morphobricks_preprocessing import iter_planes, plane_layout, prefill_mapping

    mapping = prefill_mapping(axes)
    layout = plane_layout(path, mapping, memory_limit=limit)
    assert layout.in_blocks, layout

    def once():
        for plane in iter_planes(path, mapping, memory_limit=limit):
            for block in plane.blocks():
                del block  # one block at a time, as the histograms are made
            break

    once()
    force_intern_rebuild()
    shape = layout.block_shape
    return [["blocks", shape, rss_peak(once), traced_peak(once), layout.block_memory_estimate, 0]]


def parts(path, repeats=20, seconds=0.5):
    """Measure decoding one piece: the C library's own memory against its terms alone.

    ``heap`` is the largest C heap sampled while the piece is decoded again and again, at
    least ``repeats`` times and for ``seconds`` (a small piece decodes in microseconds: many
    give the sampler its chance); ``traced`` the Python part of one decode.
    """
    import tifffile

    from morphobricks_preprocessing.tiff_pieces import _decode_segment

    with tifffile.TiffFile(path) as tif:
        page = tif.series[0].keyframe
        _decode_segment(page, 0, path)  # imports, first-use state

        def once():
            return _decode_segment(page, 0, path)

        def many():
            end = time.perf_counter() + seconds
            for count in itertools.count():
                once()
                if count >= repeats and time.perf_counter() > end:
                    break

        force_intern_rebuild()
        traced = traced_peak(once)
        heap = heap_peak(many) if _LIBC is not None and hasattr(_LIBC, "mallinfo2") else None
        rss = rss_peak(once)
    return [["parts", None, rss, traced, heap, _native(path, pieces=1)]]


RSS = False

if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    RSS = rss_works()
    what = request.pop("what")
    found = {"windows": windows, "plane": plane, "blocks": blocks, "parts": parts}[what](**request)
    print(json.dumps({"pid": os.getpid(), "rss": RSS, "results": found}))
