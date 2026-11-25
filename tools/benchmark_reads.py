"""The read cases of ``benchmark_kernels.py``: planes and windows from TIFF and OME-Zarr (#106).

One seeded ``uint16`` plane per side (``benchmark_inputs.plane``) is written, untimed, in each
storage layout:

- TIFF tiled in 512 px tiles (256 px in quick mode), zlib or uncompressed;
- TIFF stripped, 64 rows per strip, zlib or uncompressed;
- OME-Zarr (NGFF 0.4, one level, ``TCYX`` of shape ``(1, 1, side, side)``) in 1024 px chunks,
  written by ``benchmark_run_workers.write_zarr`` (zarr's default compressor), when the
  ``zarr`` extra is installed; otherwise the case is reported as skipped, with the reason.

Each layout is read with ``planes.read_plane`` (the whole plane) and ``windows.read_window``
(the windows of a diagonal: 512 px windows, 64 px in quick mode). Every read must give the
written pixels (checked once before timing), so the layouts' digests are equal.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import tifffile

import benchmark_inputs as inputs  # tools/, next to this script

TIFF_LAYOUTS = (
    ("tiled", "zlib"),
    ("tiled", None),
    ("stripped", "zlib"),
    ("stripped", None),
)
"""(storage, compression) of the TIFF files."""
ROWS_PER_STRIP = 64


def write_tiff(path: Path, pixels: np.ndarray, storage: str, compression: str | None) -> Path:
    """Write one plane as a TIFF in the given storage and compression; return its path."""
    side = pixels.shape[0]
    pieces = {"tile": (min(512, side), min(512, side))} if storage == "tiled" else {}
    if storage == "stripped":
        pieces["rowsperstrip"] = ROWS_PER_STRIP
    tifffile.imwrite(path, pixels, photometric="minisblack", compression=compression, **pieces)
    return path


def _diagonal(side: int, window: int) -> list[tuple[int, int, int, int]]:
    """Return the ``window`` px windows along the plane's diagonal, as (y0, y1, x0, x1)."""
    return [(k, k + window, k, k + window) for k in range(0, side - window + 1, window)]


def _readers(path: Path, axes: str, side: int, window: int) -> tuple[Callable, Callable]:
    """Return the whole-plane read and the windows read of one file."""
    from morphobricks_preprocessing import prefill_mapping
    from morphobricks_preprocessing.planes import read_plane
    from morphobricks_preprocessing.windows import read_window

    mapping = prefill_mapping(axes)
    bounds = _diagonal(side, window)

    def whole() -> object:
        return read_plane(path, mapping).pixels

    def windows() -> object:
        return [read_window(path, mapping, bounds=b, channels=(0,)) for b in bounds]

    return whole, windows


def _check(whole: Callable, windows: Callable, pixels: np.ndarray, window: int) -> None:
    """Refuse a layout whose reads do not give the written pixels."""
    if not np.array_equal(whole(), pixels):
        raise AssertionError("the whole-plane read differs from the written plane")
    for (y0, y1, x0, x1), found in zip(_diagonal(pixels.shape[0], window), windows(), strict=True):
        if not np.array_equal(found[0], pixels[y0:y1, x0:x1]):
            raise AssertionError("a window read differs from the written plane")


def _zarr_store(folder: Path, pixels: np.ndarray) -> Path | str:
    """Write the OME-Zarr store; return its path, or why it cannot be written here."""
    if importlib.util.find_spec("zarr") is None:
        return "zarr is not installed (the zarr extra)"
    from benchmark_run_workers import write_zarr

    path = folder / f"plane_{pixels.shape[0]}.ome.zarr"
    write_zarr(path, pixels[None, None])
    return path


def read_cases(sides: tuple[int, ...], quick: bool, folder: Path) -> Iterator[tuple]:
    """Yield ``(group, name, params, run)`` per layout, side and read (``run`` may be a reason).

    The files are written into ``folder`` as the cases are made; a skipped layout's ``run``
    is the reason it cannot run here.
    """
    window = 64 if quick else 512
    folder.mkdir(parents=True, exist_ok=True)
    for side in sides:
        pixels = inputs.plane(side, "uint16")
        files: list[tuple[str, object, str]] = []
        for storage, compression in TIFF_LAYOUTS:
            name = f"tiff_{storage}_{compression or 'none'}"
            path = folder / f"plane_{side}_{storage}_{compression or 'none'}.tif"
            files.append((name, write_tiff(path, pixels, storage, compression), "YX"))
        files.append(("zarr_ngff04", _zarr_store(folder, pixels), "TCYX"))
        for name, path, axes in files:
            params = {"side": side, "dtype": "uint16", "window": window, "layout": name}
            if isinstance(path, str):
                yield "reads", f"{name}_{side}", params, path
                continue
            whole, windows = _readers(path, axes, side, window)
            _check(whole, windows, pixels, window)
            params["file_bytes"] = _size(path)
            yield "reads", f"plane_{name}_{side}", params, whole
            count = len(_diagonal(side, window))
            yield "reads", f"windows_{name}_{side}", {**params, "windows": count}, windows


def _size(path: Path) -> int:
    """Return the bytes of a file, or of every file in a store folder."""
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
