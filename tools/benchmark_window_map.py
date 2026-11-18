"""Benchmark ``window_map.map_windows`` on a synthetic tiled RGB TIFF (lazy-windows §5).

Usage::

    python tools/benchmark_window_map.py [--folder DIR] [--side 8192] [--tile 512]
        [--window 512] [--margin 67] [--workers 1 2 4] [--repeats 2] [--budget-mib 1024]

It writes (once, into ``--folder``) the synthetic image of ``benchmark_window_reads.py``, then
maps a NumPy-heavy reduction (per channel: a float64 copy, mean, standard deviation, squared
gradients summed and a 256-bin histogram) over the grid of ``window`` px windows with ``margin``
px margins, through one ``WindowReader`` per run whose cache is ``window_reader.cache_plan``'s
figure, for each worker count. It prints, as JSON: the plan, the wall time of each run, the
tiles decoded, the SHA-256 of the results in window order (with the windows' bytes) and the
load average before and after. Runs are comparable only when their digests are equal.
Timings are indicative: they depend on the computer, its disk cache and its load.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np

import morphobricks_preprocessing
from benchmark_window_reads import grid, write_image
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.window_map import map_plan, map_windows
from morphobricks_preprocessing.window_reader import WindowReader, cache_plan

MIB = 2**20
CHANNELS = [0, 1, 2]


def reduce_window(bounds: tuple[int, int, int, int], window: np.ndarray) -> bytes:
    """Return a NumPy-heavy reduction of one window and its bytes' digest, as bytes."""
    values = window.astype(np.float64)
    rows = []
    for plane in values:
        gy, gx = np.diff(plane, axis=0), np.diff(plane, axis=1)
        hist = np.histogram(plane, bins=256, range=(0, 256))[0]
        rows.append([plane.mean(), plane.std(), (gy**2).sum(), (gx**2).sum(), *hist])
    return hashlib.sha256(window.tobytes()).digest() + np.asarray(rows).tobytes()


def run(path: Path, windows: list, workers: int, cache_bytes: int, budget: int) -> dict:
    """Map the reduction over every window with ``workers`` threads; return time and digest."""
    with WindowReader(path, prefill_mapping("YXS"), cache_bytes=cache_bytes) as reader:
        plan = map_plan(reader, windows, channels=CHANNELS, workers=workers)
        start = time.perf_counter()
        digest = hashlib.sha256()
        with map_windows(plan, reduce_window, memory_budget=budget) as results:
            for result in results:
                digest.update(result)
        wall = time.perf_counter() - start
        summary = results.summary()
        return {
            "workers": workers,
            "max_in_flight": plan.max_in_flight,
            "memory_estimate_mib": round(plan.memory_estimate / MIB, 1),
            "wall_seconds": round(wall, 2),
            "tile_decodes": reader.stats().decodes,
            "finished": summary.finished,
            "results_sha256": digest.hexdigest(),
        }


def main() -> None:
    """Parse the arguments, write the image if needed, run every worker count and print."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--folder", type=Path, default=None)
    parser.add_argument("--side", type=int, default=8192)
    parser.add_argument("--tile", type=int, default=512)
    parser.add_argument("--window", type=int, default=512)
    parser.add_argument("--margin", type=int, default=67)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--budget-mib", type=int, default=1024)
    args = parser.parse_args()
    folder = args.folder or Path(tempfile.mkdtemp(prefix="window_map_"))
    path = write_image(folder, args.side, args.tile)
    windows = grid(args.side, args.window, args.margin)
    needed = cache_plan(
        path,
        prefill_mapping("YXS"),
        window=(args.window, args.window),
        margin=(args.margin, args.margin),
        channels=CHANNELS,
    )
    load_before = os.getloadavg()
    runs = [
        run(path, windows, workers, needed.cache_bytes, args.budget_mib * MIB)
        for _ in range(args.repeats)
        for workers in args.workers
    ]
    result = {
        "package": str(Path(morphobricks_preprocessing.__file__).parent),
        "image": str(path),
        "windows": len(windows),
        "cpu_count": os.cpu_count(),
        "cache_mib": round(needed.cache_bytes / MIB, 1),
        "load_average_before": [round(v, 2) for v in load_before],
        "load_average_after": [round(v, 2) for v in os.getloadavg()],
        "identical": len({r["results_sha256"] for r in runs}) == 1,
        "runs": runs,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
