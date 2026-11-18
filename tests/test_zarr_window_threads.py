"""An OME-Zarr window's estimate bounds its peak whatever zarr's threads do (#354).

zarr 3 decodes in worker threads that can keep a chunk's buffers alive after a read has
returned, when the CPU is busy. With chunks of 1 MiB that went over the window estimate (13 of
32 runs with 4 test processes on 4 cores, up to 1.10×). Every OME-Zarr read now runs zarr's
read with threads of its own, joined before it returns (``zarr_input.read_selection``). The
peak is measured in a fresh process (``window_peaks``) as it is, and with every worker thread
held after each call (``window_peaks_late``), which is what the busy CPU did by chance.
"""

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.windows import read_window
from morphobricks_preprocessing.zarr_input import open_ngff
from zarr_tree import write_ngff, zarr_major

SIDE = 1024
CHUNKS = (1, 512, 512)  # float32: 1 MiB chunks
WINDOWS = [(0, 64, 0, 64), (200, 300, 400, 600), (0, SIDE, 0, SIDE)]
"""Inside one chunk, across two chunks (the case that went over) and the whole plane."""
LATE = 0.02
"""Seconds a worker thread holds each call it ran: far longer than a read takes to go on."""
TESTS = Path(__file__).resolve().parent


def _image(tmp_path, version):
    data = (np.random.default_rng(4).random((3, SIDE, SIDE)) * 200).astype("float32")
    path = write_ngff(tmp_path / "a.zarr", data, "cyx", version=version, chunks=CHUNKS)
    return path, data


def _child(script, request):
    done = subprocess.run(
        [sys.executable, str(TESTS / script), json.dumps(request)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _check(results):
    for what, bounds, cache, peak, estimate in results:
        assert peak <= estimate, (what, cache, bounds, peak, estimate, round(peak / estimate, 3))


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_the_estimate_bounds_the_peak_of_1_mib_ome_zarr_chunks(tmp_path, version):
    path, _ = _image(tmp_path, version)
    request = {"path": str(path), "axes": "CYX", "channels": [2, 0], "plane": SIDE}
    _check(_child("window_peaks_late.py", {**request, "late": 0, "windows": WINDOWS})["results"])


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_the_estimate_bounds_the_peak_when_zarr_worker_threads_let_go_late(tmp_path, version):
    path, _ = _image(tmp_path, version)
    request = {"path": str(path), "axes": "CYX", "channels": [2, 0], "plane": SIDE}
    answer = _child("window_peaks_late.py", {**request, "late": LATE, "windows": WINDOWS})
    if zarr_major() >= 3:  # zarr 2 has no worker threads: nothing to hold
        assert answer["late_calls"] > 0
    _check(answer["results"])


@pytest.mark.parametrize("version", ["0.4", "0.5"])
def test_a_window_read_leaves_no_thread_and_reads_zarr_s_values(tmp_path, version):
    path, data = _image(tmp_path, version)
    mapping = prefill_mapping("CYX")
    bounds = (200, 900, 300, 1000)
    array = open_ngff(path).array  # zarr's own read, through its own threads
    expected = np.stack([np.asarray(array[c, 200:900, 300:1000]) for c in (2, 0)])
    np.testing.assert_array_equal(expected, data[[2, 0], 200:900, 300:1000])

    def read():
        return read_window(path, mapping, bounds=bounds, channels=[2, 0])

    async def in_a_running_loop():  # a notebook: the read must not run on this loop
        return read()

    for window in (read(), asyncio.run(in_a_running_loop())):
        assert window.dtype == expected.dtype
        assert window.tobytes() == expected.tobytes()
        assert not [t for t in threading.enumerate() if t.name.startswith("morphobricks-zarr")]
