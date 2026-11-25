"""Shared measuring of the kernel benchmarks (issues #106 and #505): time, memory, digest.

Every case is measured the same way by :func:`measure`: ``warmup`` untimed calls, then
``repeats`` timed calls (``time.perf_counter``; the median, minimum and maximum are reported
with every value), then one more call under ``tracemalloc`` for the peak of the memory it
traced (Python objects and NumPy arrays; buffers a C library allocates outside NumPy, such as
a decoder's, are not traced). The timed calls are never traced. Every call's result is reduced
to a SHA-256 (:func:`digest`); a case whose calls disagree is reported (``digest_stable``
false), and two versions are comparable only when their digests are equal: no performance
claim without identical outputs.

:func:`machine` records where the numbers were taken: processor, CPUs this process may use,
memory, load, Python and the versions of the libraries the kernels run on, and the commit of
the checkout. Numbers from different machines are not comparable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import os
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np

from benchmark_memory import peak_rss  # tools/, next to this script

LIBRARIES = (
    "numpy",
    "scipy",
    "tifffile",
    "imagecodecs",
    "zarr",
    "morphobricks-normalization",
    "morphobricks-preprocessing",
)
"""The distributions whose versions are recorded with the numbers."""
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
"""Environment variables that change how many threads a native library uses."""


def digest(value: object) -> str:
    """Return the SHA-256 of a result: arrays by type, shape and bytes, the rest by value.

    Dataclasses are taken field by field, mappings and sequences in their own order (an order
    change is a different output), floats by their exact ``repr``.
    """
    sha = hashlib.sha256()
    _feed(sha, value)
    return sha.hexdigest()


def _feed(sha: hashlib._Hash, value: object) -> None:
    """Add one value to the hash, tagged by its kind so different kinds never collide."""
    if isinstance(value, np.ndarray):
        sha.update(f"array {value.dtype.str} {value.shape};".encode())
        sha.update(np.ascontiguousarray(value).tobytes())
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        sha.update(f"dataclass {type(value).__name__};".encode())
        for field in dataclasses.fields(value):
            _feed(sha, field.name)
            _feed(sha, getattr(value, field.name))
    elif isinstance(value, Mapping):
        sha.update(f"mapping {len(value)};".encode())
        for key, item in value.items():
            _feed(sha, key)
            _feed(sha, item)
    elif isinstance(value, (list, tuple)):
        sha.update(f"sequence {len(value)};".encode())
        for item in value:
            _feed(sha, item)
    elif isinstance(value, (set, frozenset)):
        _feed(sha, sorted(value, key=repr))
    elif isinstance(value, np.generic):
        sha.update(f"{value.dtype.str} {value.item()!r};".encode())
    else:
        sha.update(f"{type(value).__name__} {value!r};".encode())


def _spread(values: list[float]) -> dict[str, object]:
    """Return the median, minimum, maximum and every value, in seconds (rounded to 1 µs)."""
    return {
        "median": round(statistics.median(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "values": [round(v, 6) for v in values],
    }


def measure(
    run: Callable[[], object], repeats: int, *, warmup: int = 1, trace: bool = True
) -> dict[str, object]:
    """Time ``run`` and trace its peak memory; return the times, the peak and the digest.

    Parameters
    ----------
    run
        The kernel call, its inputs already built.
    repeats
        Timed calls (at least 1).
    warmup
        Untimed calls before them (imports, caches, first-touch of the inputs).
    trace
        Make the traced call; without it ``peak_traced_bytes`` is None.

    Returns
    -------
    dict
        ``wall_seconds`` (median, min, max, values), ``peak_traced_bytes``, ``digest`` and
        ``digest_stable`` (every call, the traced one included, gave the same digest).
    """
    if not trace:
        found = _timed(run, repeats, warmup)
        return {**found, "peak_traced_bytes": None}
    found = _timed(run, repeats, warmup)
    tracemalloc.start()
    try:
        result = run()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    traced = digest(result)
    found["digest_stable"] = found["digest_stable"] and traced == found["digest"]
    return {**found, "peak_traced_bytes": peak}


def _timed(run: Callable[[], object], repeats: int, warmup: int) -> dict[str, object]:
    """Make the warm-up and timed calls; return the times and the digest of the last call."""
    digests = {digest(run()) for _ in range(warmup)}
    walls = []
    for _ in range(max(repeats, 1)):
        start = time.perf_counter()
        result = run()
        walls.append(time.perf_counter() - start)
        digests.add(digest(result))
    last = digest(result)
    return {"wall_seconds": _spread(walls), "digest": last, "digest_stable": len(digests) == 1}


def _processor() -> str:
    """Return the processor's model name, as the system gives it."""
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="ascii").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, UnicodeDecodeError):
        pass
    return platform.processor() or platform.machine()


def _memory_bytes() -> int | None:
    """Return the machine's total memory in bytes (Linux ``MemTotal``), or None elsewhere."""
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, UnicodeDecodeError, ValueError, IndexError):
        pass
    return None


def _commit() -> str | None:
    """Return the commit of the checkout this script is in, or None outside Git."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def _version(name: str) -> str | None:
    """Return an installed distribution's version, or None when it is not installed."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def machine() -> dict[str, object]:
    """Return where the numbers are taken: hardware, load, Python, libraries and commit."""
    usable = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    load = list(os.getloadavg()) if hasattr(os, "getloadavg") else None
    return {
        "processor": _processor(),
        "cpus": os.cpu_count(),
        "cpus_usable": usable,
        "memory_bytes": _memory_bytes(),
        "load_average": load,
        "system": platform.platform(),
        "python": sys.version.split()[0],
        "libraries": {name: _version(name) for name in LIBRARIES},
        "thread_variables": {name: os.environ.get(name) for name in THREAD_VARIABLES},
        "commit": _commit(),
    }


def process_peak() -> dict[str, object]:
    """Return this process's own peak resident memory so far, and where it was read."""
    peak, source = peak_rss()
    return {"peak_rss_bytes": peak, "source": source}
