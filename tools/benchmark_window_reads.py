"""Benchmark a grid of window reads on a synthetic tiled RGB TIFF (issues #294 and #304).

Usage::

    python tools/benchmark_window_reads.py [--folder DIR] [--side 8192] [--tile 512]
        [--window 512] [--margin 67] [--threads 4] [--cache-mib 0]

It writes (once, into ``--folder``, by default a new temporary folder) a synthetic
``side x side`` RGB uint8 TIFF stored in ``tile x tile`` zlib tiles with interleaved samples,
then reads a grid of ``window`` px windows, each with a ``margin`` px margin clipped to the
image, for the three samples, from a pool of ``threads`` threads (MorphoFeature's lazy windowed
route). ``--cache-mib`` above 0 reads through ``window_reader.WindowReader`` with a cache of
that many MiB; 0 reads with ``windows.read_window`` alone.

It prints, as JSON: the tiles decoded (every call of tifffile's decoder, counted whatever the
version of this package), the distinct tiles, the wall time of the reads and the SHA-256 of
every window in grid order. Runs of different versions are comparable only when their digests
are equal (no performance claim without identical outputs). Timings are indicative: they
depend on the computer, its disk cache and its load.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import tifffile

import morphobricks_preprocessing
from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.windows import read_window

MIB = 2**20


def write_image(folder: Path, side: int, tile: int) -> Path:
    """Write the synthetic tiled RGB image once and return its path (reused when present)."""
    path = folder / f"synthetic_rgb_{side}_tile{tile}.tif"
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    y, x = np.ogrid[:side, :side]
    data = np.empty((side, side, 3), dtype=np.uint8)
    for s in range(3):  # smooth gradients with noise: compressible, never constant
        noise = rng.integers(0, 24, (side, side), dtype=np.uint8)
        data[..., s] = ((y * (s + 1) + x * (3 - s)) // 64 % 200).astype(np.uint8) + noise
    tifffile.imwrite(path, data, photometric="rgb", tile=(tile, tile), compression="zlib")
    return path


def grid(side: int, window: int, margin: int) -> list[tuple[int, int, int, int]]:
    """Return the windows of a grid, each with its margin clipped to the image, row by row."""
    return [
        (
            max(y - margin, 0),
            min(y + window + margin, side),
            max(x - margin, 0),
            min(x + window + margin, side),
        )
        for y in range(0, side, window)
        for x in range(0, side, window)
    ]


def count_decodes() -> tuple[list[int], Callable[[], None]]:
    """Count every tile tifffile decodes; return the tile indices and a function that stops."""
    original = tifffile.TiffPage.__dict__["decode"]
    found: list[int] = []
    lock = threading.Lock()

    def decode(page: tifffile.TiffPage) -> Callable:
        inner = original.__get__(page, tifffile.TiffPage)

        def counted(data: bytes, index: int, *args: object, **kwargs: object) -> object:
            with lock:
                found.append(index)
            return inner(data, index, *args, **kwargs)

        return counted

    tifffile.TiffPage.decode = property(decode)
    return found, lambda: setattr(tifffile.TiffPage, "decode", original)


def run(path: Path, windows: list[tuple[int, int, int, int]], threads: int, cache_mib: int) -> dict:
    """Read every window from a pool of threads; return the counts, wall time and digest."""
    mapping = prefill_mapping("YXS")
    if cache_mib > 0:
        from morphobricks_preprocessing.window_reader import WindowReader

        reader = WindowReader(path, mapping, cache_bytes=cache_mib * MIB)

        def read(bounds: tuple[int, int, int, int]) -> bytes:
            return hashlib.sha256(reader.read_window(bounds=bounds, channels=[0, 1, 2])).digest()

    else:

        def read(bounds: tuple[int, int, int, int]) -> bytes:
            return hashlib.sha256(
                read_window(path, mapping, bounds=bounds, channels=[0, 1, 2])
            ).digest()

    decoded, stop = count_decodes()
    try:
        start = time.perf_counter()
        with ThreadPoolExecutor(threads) as pool:
            digests = list(pool.map(read, windows))
        wall = time.perf_counter() - start
    finally:
        stop()
    return {
        "tile_decodes": len(decoded),
        "distinct_tiles": len(set(decoded)),
        "wall_seconds": round(wall, 2),
        "windows_sha256": hashlib.sha256(b"".join(digests)).hexdigest(),
    }


def main() -> None:
    """Parse the arguments, write the image if needed, read the grid and print the result."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--folder", type=Path, default=None)
    parser.add_argument("--side", type=int, default=8192)
    parser.add_argument("--tile", type=int, default=512)
    parser.add_argument("--window", type=int, default=512)
    parser.add_argument("--margin", type=int, default=67)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--cache-mib", type=int, default=0)
    args = parser.parse_args()
    folder = args.folder or Path(tempfile.mkdtemp(prefix="window_reads_"))
    path = write_image(folder, args.side, args.tile)
    windows = grid(args.side, args.window, args.margin)
    result = {
        "package": str(Path(morphobricks_preprocessing.__file__).parent),
        "image": str(path),
        "windows": len(windows),
        "threads": args.threads,
        "cache_mib": args.cache_mib,
        **run(path, windows, args.threads, args.cache_mib),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
