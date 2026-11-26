"""Seeded synthetic inputs of the kernel benchmarks (issue #106): planes, label masks, summaries.

Every input is a pure function of its arguments and seed (``numpy.random.default_rng``), so
two runs with the same NumPy version, on any machine and any version of this package, measure
the same data; NumPy does not promise the same random streams across versions, so each input's
SHA-256 is recorded (:data:`INPUT_DIGESTS`, the ``inputs`` of the report) and must be equal
for two runs to be compared. Nothing is read from disk here; ``benchmark_reads.py`` writes
the planes into files.
"""

from __future__ import annotations

import numpy as np

from benchmark_harness import digest  # tools/, next to this script

INPUT_DIGESTS: dict[str, str] = {}
"""SHA-256 of every input generated in this process, by a name that holds its arguments."""

DTYPES = ("uint8", "uint16", "float32")
"""The pixel types of the histogram and read cases."""


def plane(side: int, dtype: str, seed: int = 0) -> np.ndarray:
    """Return the seeded plane (:func:`_plane`) and record its digest."""
    found = _plane(side, dtype, seed)
    INPUT_DIGESTS[f"plane_{dtype}_{side}_seed{seed}"] = digest(found)
    return found


def _plane(side: int, dtype: str, seed: int) -> np.ndarray:
    """Return a ``side x side`` plane: smooth structure plus seeded noise, never constant.

    ``uint8`` spans about 0 to 240, ``uint16`` about 100 to 3,400 (a 12-bit camera), and
    ``float32`` the same range as ``uint16`` with Gaussian noise (non-integer values).
    """
    rng = np.random.default_rng(seed)
    y, x = np.ogrid[:side, :side]
    smooth = np.sin(y / 37.0) * np.cos(x / 53.0) + 1.0  # 0 to 2
    if dtype == "uint8":
        noise = rng.integers(0, 40, (side, side), dtype=np.uint8)
        return (smooth * 100.0).astype(np.uint8) + noise
    if dtype == "uint16":
        noise = rng.integers(0, 300, (side, side), dtype=np.uint16)
        return (smooth * 1500.0 + 100.0).astype(np.uint16) + noise
    if dtype == "float32":
        noise = rng.normal(0.0, 30.0, (side, side))
        return (smooth * 1500.0 + 100.0 + noise).astype(np.float32)
    raise ValueError(f"no synthetic plane of type {dtype}")


def label_masks(side: int, objects: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return the seeded masks (:func:`_label_masks`) and record their digest."""
    found = _label_masks(side, objects, seed)
    INPUT_DIGESTS[f"label_masks_{side}_{objects}_seed{seed}"] = digest(found)
    return found


def _label_masks(side: int, objects: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return two int32 instance masks of one position: cells, and one nucleus inside each.

    The ``objects`` cells (rounded up to a square grid) are discs centred near the middle of
    the cells of a regular grid, with seeded jitter; every tenth cell also has a second,
    detached 3 x 3 px piece in its grid cell's corner (an object of two pieces). Each nucleus
    is a smaller disc inside its cell, with labels in a seeded order, so the nucleus and cell
    labels of one object differ (objects are never joined by label number).
    """
    rng = np.random.default_rng(seed)
    grid = int(np.ceil(np.sqrt(objects)))
    spacing = side // grid
    jitter = rng.uniform(-0.1, 0.1, (2, grid, grid)) * spacing
    centre_y = (np.arange(grid)[:, None] + 0.5) * spacing + jitter[0]
    centre_x = (np.arange(grid)[None, :] + 0.5) * spacing + jitter[1]
    y, x = np.ogrid[: grid * spacing, : grid * spacing]
    row, col = y // spacing, x // spacing
    distance = (y - centre_y[row, col]) ** 2 + (x - centre_x[row, col]) ** 2
    index = (row * grid + col).astype(np.int32)
    cells = np.zeros((side, side), dtype=np.int32)
    nuclei = np.zeros((side, side), dtype=np.int32)
    inner = cells[: grid * spacing, : grid * spacing]
    inner[distance < (0.4 * spacing) ** 2] = (index + 1)[distance < (0.4 * spacing) ** 2]
    corner = (y % spacing < 3) & (x % spacing < 3) & (index % 10 == 0)
    inner[corner] = (index + 1)[corner]
    order = rng.permutation(grid * grid).astype(np.int32) + 1
    small = distance < (0.18 * spacing) ** 2
    nuclei[: grid * spacing, : grid * spacing][small] = order[index[small]]
    return cells, nuclei


PIXEL_MASKS = ("checker_two_labels", "checker_one_label", "noise_four_labels")
"""The pixel-scale masks (:func:`pixel_mask`): about one run of one label per pixel or two."""


def pixel_mask(side: int, kind: str, seed: int = 0) -> np.ndarray:
    """Return the seeded pixel-scale mask (:func:`_pixel_mask`) and record its digest."""
    found = _pixel_mask(side, kind, seed)
    INPUT_DIGESTS[f"pixel_mask_{kind}_{side}_seed{seed}"] = digest(found)
    return found


def _pixel_mask(side: int, kind: str, seed: int) -> np.ndarray:
    """Return a ``side x side`` mask whose labels change at almost every pixel (issue #69).

    ``checker_two_labels``: labels 1 and 2 in a checkerboard (``uint16``, one run per pixel);
    ``checker_one_label``: label 1 on the black squares, background on the white (``int32``,
    every pixel of label 1 one piece through the corners); ``noise_four_labels``: half the
    pixels, seeded, carry a seeded label 1 to 4 (``int32``). The worst cases of the runs route
    of ``piece_counts``, which counts these by boxes.
    """
    board = np.add.outer(np.arange(side), np.arange(side)) % 2
    if kind == "checker_two_labels":
        return (board + 1).astype(np.uint16)
    if kind == "checker_one_label":
        return board.astype(np.int32)
    if kind == "noise_four_labels":
        rng = np.random.default_rng(seed)
        on = rng.random((side, side)) < 0.5
        return np.where(on, rng.integers(1, 5, (side, side)), 0).astype(np.int32)
    raise ValueError(f"no pixel-scale mask {kind}")


def summaries(units: int, images: int, seed: int = 0) -> list:
    """Return the seeded summaries (:func:`_summaries`) and record their digest."""
    found = _summaries(units, images, seed)
    INPUT_DIGESTS[f"summaries_{units}x{images}_seed{seed}"] = digest(found)
    return found


def _summaries(units: int, images: int, seed: int) -> list:
    """Return the image summaries of ``units`` units of three channels (``uint16``).

    Units are spread over four conditions (fewer when there are fewer units), each a
    technical repeat of its own biological repeat; each channel's median and percentiles
    vary from unit to unit by a seeded factor of about 10 % and from image to image by about
    4 %. One unit has two channels swapped, so the check has something to find.
    """
    from morphobricks_normalization.summaries import ImageSummary

    stains = {"Actin": (300.0, 50.0, 1500.0), "DAPI": (800.0, 100.0, 4000.0)}
    stains["GFP"] = (150.0, 80.0, 600.0)
    channels = tuple(sorted(stains))
    rng = np.random.default_rng(seed)
    found = []
    for unit in range(units):
        condition, repeat = f"cond{unit % 4}", f"rep{unit // 4:04d}"
        scale = {c: float(rng.lognormal(0.0, 0.1)) for c in channels}
        own = dict(stains)
        if unit == units // 2:
            own["DAPI"], own["GFP"] = stains["GFP"], stains["DAPI"]
        for image in range(images):
            jitter = float(rng.lognormal(0.0, 0.04))
            values = [tuple(jitter * scale[c] * v for v in own[c]) for c in channels]
            found.append(
                ImageSummary(
                    source_relative_path=f"{condition}/{repeat}/single/img{image:03d}.tif",
                    condition=condition,
                    biological_repeat=repeat,
                    technical_repeat="single",
                    dtype=np.dtype("uint16"),
                    percentile=(1.0, 99.8),
                    channels=channels,
                    median=tuple(v[0] for v in values),
                    p_low=tuple(v[1] for v in values),
                    p_high=tuple(v[2] for v in values),
                    saturated_fraction=tuple(0.0 for _ in channels),
                    exact=tuple(True for _ in channels),
                )
            )
    return found
