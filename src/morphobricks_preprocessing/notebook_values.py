"""Values the notebook shows, computed without widgets or files."""

from __future__ import annotations

import numpy as np


def display_range(pixels: np.ndarray, low: float = 0.5, high: float = 99.5) -> tuple[float, float]:
    """Return the brightness range used to show a plane (finite values only)."""
    finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
    if finite.size == 0:
        return 0.0, 1.0
    vmin, vmax = (float(v) for v in np.percentile(finite, [low, high]))
    if vmin == vmax:
        vmax = vmin + 1
    return vmin, vmax
