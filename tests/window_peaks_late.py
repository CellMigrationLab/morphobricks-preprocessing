"""Measure window peaks in a fresh process with every worker thread late to let go (#354).

zarr 3 reads a chunk's bytes and decodes them in worker threads (``asyncio.to_thread``). A
worker still refers to the call it ran (its arguments and result: a chunk's compressed and
decoded bytes) until it takes its next call. When the CPU is busy, the scheduler can hold a
worker there while the reads go on, which kept a chunk alive beyond the window estimate (#354).
Here every worker thread sleeps ``late`` seconds right after each call, still holding it, which
is what the busy CPU did by chance, every time. Then the peaks are measured as
``window_peaks`` measures them::

    python tests/window_peaks_late.py '{"late": 0.02, "windows": [[0, 64, 0, 64]], ...}'

The answer is ``window_peaks``' (``results``), plus ``late_calls``: how many calls a worker
thread was held after, so a test can check the delay was in force.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import thread as pool_threads

import window_peaks

LATE_CALLS = [0]


def make_workers_late(seconds):
    """Make every pool worker hold each call it ran for ``seconds`` before letting it go."""
    run = pool_threads._WorkItem.run

    def late_run(self):
        run(self)
        if threading.current_thread() is not threading.main_thread():
            LATE_CALLS[0] += 1
            time.sleep(seconds)

    pool_threads._WorkItem.run = late_run


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    make_workers_late(request.pop("late"))
    # In place: ``measure`` reads this list (or takes it as its default ``windows``).
    window_peaks.WINDOWS[:] = [tuple(w) for w in request.pop("windows")]
    found = window_peaks.measure(**request)
    answer = {"pid": os.getpid(), "results": found, "late_calls": LATE_CALLS[0]}
    print(json.dumps(answer))
