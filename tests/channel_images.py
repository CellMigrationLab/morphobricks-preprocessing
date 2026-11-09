"""Small experiment folders with named channels and pictures that have visible structure.

``blobs`` draws nucleus-like discs, ``filaments`` a tangle of line segments and ``cells`` a
smooth cell-shaped haze, so a thumbnail shows something recognisable (screenshots, tests), never
blank or noise-only arrays.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

SIZE = 64


def blobs(seed: int, size: int = SIZE) -> np.ndarray:
    """Return nucleus-like bright discs of varied size on a dim background (uint16)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    img = rng.normal(300, 20, (size, size))
    for _ in range(7):
        cy, cx, r = rng.uniform(8, size - 8), rng.uniform(8, size - 8), rng.uniform(4, 8)
        img += 3000 * np.exp(-((((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * (r / 1.6) ** 2)) ** 1.5))
    return np.clip(img, 0, 65535).astype(np.uint16)


def filaments(seed: int, size: int = SIZE) -> np.ndarray:
    """Return a tangle of bright line segments (uint16)."""
    rng = np.random.default_rng(seed)
    img = rng.normal(250, 15, (size, size))
    for _ in range(14):
        y0, x0 = rng.uniform(0, size, 2)
        angle, length = rng.uniform(0, np.pi), rng.uniform(18, 40)
        for t in np.linspace(0, length, int(length * 3)):
            y, x = int(y0 + t * np.sin(angle)), int(x0 + t * np.cos(angle))
            if 0 <= y < size and 0 <= x < size:
                img[max(y - 1, 0) : y + 1, max(x - 1, 0) : x + 1] += 1800
    return np.clip(img, 0, 65535).astype(np.uint16)


def cells(seed: int, size: int = SIZE) -> np.ndarray:
    """Return a smooth cell-shaped haze with a few brighter regions (uint16)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    img = rng.normal(400, 20, (size, size))
    for _ in range(4):
        cy, cx = rng.uniform(12, size - 12, 2)
        r = rng.uniform(10, 16)
        img += 1500 * np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r**2)))
    return np.clip(img, 0, 65535).astype(np.uint16)


KINDS = {"blobs": blobs, "filaments": filaments, "cells": cells}


def blob_labels(seed: int, size: int = SIZE) -> np.ndarray:
    """Return the discs of ``blobs(seed)`` as numbered objects 1..7 (uint16), as a segmentation."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    rng.normal(300, 20, (size, size))  # the same draws as blobs, so the discs line up
    labels = np.zeros((size, size), np.uint16)
    for number in range(1, 8):
        cy, cx, r = rng.uniform(8, size - 8), rng.uniform(8, size - 8), rng.uniform(4, 8)
        labels[(yy - cy) ** 2 + (xx - cx) ** 2 <= (r * 0.9) ** 2] = number
    return labels


def region(seed: int, size: int = SIZE) -> np.ndarray:
    """Return the bright part of ``cells(seed)`` as a 0/1 region (uint8)."""
    haze = cells(seed, size)
    return (haze > np.percentile(haze, 60)).astype(np.uint8)


def write_masks(masks: str | Path, images: dict[str, int], kinds: dict[str, str]):
    """Write one mask per image and kind: ``kinds`` maps a mask type to ``labels`` or ``region``.

    ``images`` maps an image path (relative, as in the experiment) to the seed it was written with.
    """
    masks = Path(masks)
    for rel, seed in images.items():
        for mask_type, kind in kinds.items():
            data = blob_labels(seed) if kind == "labels" else region(seed + 2)
            path = masks / mask_type / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            tifffile.imwrite(path, data, metadata={"axes": "YX"}, photometric="minisblack")
    return masks


def write_channels(
    path: str | Path,
    names: list[str | None] | None,
    kinds: list[str],
    seed: int = 1,
    size: int = SIZE,
):
    """Write a CYX OME-TIFF whose channel ``i`` shows ``kinds[i]`` and has header ``names[i]``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack([KINDS[k](seed + i, size) for i, k in enumerate(kinds)])
    metadata: dict = {"axes": "CYX"}
    if names is not None:
        metadata["Channel"] = {"Name": ["" if n is None else n for n in names]}
    tifffile.imwrite(path, data, ome=True, photometric="minisblack", metadata=metadata)
    return path


def write_rgb(path: str | Path, seed: int = 1):
    """Write a colour (RGB) TIFF: nuclei in blue, filaments in green, haze in red."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.stack([cells(seed) // 256, filaments(seed) // 256, blobs(seed) // 256], axis=-1)
    tifffile.imwrite(path, data.astype(np.uint8), photometric="rgb")
    return path
