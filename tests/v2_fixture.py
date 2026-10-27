"""The experiments of the schema-version-2 fixture in ``tests/data`` (follow-up P3b).

``tests/data/make_v2_fixture.py`` wrote their ``plane_histograms.csv`` with the
code of version 2 (one row per plane and channel); ``test_schema_v2_equivalence.py``
writes the same images again and compares the current table with it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from tiff_tree import write_data

DATA = Path(__file__).parent / "data"
PIXELS = DATA / "v2_pixels.npz"

# Per pixel type: the bin count, the experiment axis roles and its images as
# (relative path, axes, writer options). TIFF images share one arrangement of axes;
# the OME-Zarr image stores its axes in another order, matched by role.
EXPERIMENTS = {
    "uint16": (
        500,  # fewer bins than values in most channels: inexact binning too
        "ZCYX",
        [
            ("ctrl/rep1/a.tif", "ZCYX", {}),
            ("ctrl/rep1/b.tif", "ZCYX", {"compression": "zlib", "tile": (16, 16)}),
            ("drug/rep1/a.tif", "ZCYX", {"imagej": True}),
            ("drug/rep1/c.ome.zarr", "czyx", {}),
        ],
    ),
    "float32": (
        64,
        "TCYX",
        [
            ("ctrl/rep1/a.tif", "TCYX", {}),
            ("drug/rep1/a.tif", "TCYX", {"compression": "zlib", "rowsperstrip": 5}),
        ],
    ),
}


def make_pixels(seed: int = 0) -> dict[str, np.ndarray]:
    """Simulate the pixels of every image, keyed ``"<dtype>/<relative path>"``."""
    rng = np.random.default_rng(seed)
    pixels = {}
    for rel, axes, _ in EXPERIMENTS["uint16"][2]:
        # Channels with narrow (exact) and wide (binned) ranges of values.
        shape = {"Z": 2, "C": 3, "Y": 20, "X": 18}
        data = np.stack(
            [
                rng.integers(100, 100 + spread, (shape["Z"], shape["Y"], shape["X"]))
                for spread in (300, 5_000, 60_000)
            ],
            axis=1,
        ).astype(np.uint16)
        if axes == "czyx":
            data = np.ascontiguousarray(data.transpose(1, 0, 2, 3))
        pixels[f"uint16/{rel}"] = data
    for i, (rel, _, _) in enumerate(EXPERIMENTS["float32"][2]):
        data = rng.normal(50, 20, (2, 2, 15, 13)).astype(np.float32)  # T, C, Y, X
        data[0, 0, 1, :3] = [np.nan, np.inf, -np.inf]
        if i == 1:
            data[1, 1] = np.nan  # a channel without finite pixels at one time point
            data[0, 1] = 7.5  # a constant channel
        pixels[f"float32/{rel}"] = data
    return pixels


def write_experiment(root: Path, dtype: str, pixels: dict[str, np.ndarray]) -> tuple[int, str]:
    """Write the images of one experiment under ``root``; return its bin count and axes."""
    bins, axes, images = EXPERIMENTS[dtype]
    for rel, image_axes, kwargs in images:
        data = pixels[f"{dtype}/{rel}"]
        if rel.endswith(".ome.zarr"):
            from zarr_tree import write_ngff

            write_ngff(root / rel, data, image_axes, chunks=(1, 1, 8, 8))
        else:
            write_data(root / rel, data, axes=image_axes, **kwargs)
    return bins, axes
