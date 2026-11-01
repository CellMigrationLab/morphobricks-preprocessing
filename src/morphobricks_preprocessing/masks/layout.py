"""Where a mask's planes are: its axes as stored, and the index of one plane (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import tifffile

from .model import _YX


def _tiff_axes(series: tifffile.TiffPageSeries) -> tuple[str, tuple[int, ...]]:
    """Return the axes and shape of a TIFF mask's series, as ``tifffile`` gives them.

    One exception: a series without Y or X stored in one 2D page, for example a 1D array
    written with ``tifffile``'s default metadata (``{"shape": [N]}``, axes ``X``), is that
    page: ``YX`` with the page's shape, one row of N pixels. The page's rows and columns are
    the TIFF's own (as for any plain 2D page, decision D-d), so nothing is guessed; any other
    series without Y or X keeps its axes and is refused.
    """
    axes, shape = str(series.axes), tuple(int(n) for n in series.shape)
    if "Y" in axes and "X" in axes:
        return axes, shape
    page = series.keyframe
    if len(series.pages) == 1 and page.axes == "YX" and math.prod(page.shape) == math.prod(shape):
        return "YX", tuple(int(n) for n in page.shape)
    return axes, shape


def _plane_index(
    axes: str, shape: tuple[int, ...], wanted: Mapping[str, int | None]
) -> tuple[int | slice, ...]:
    """Return the index of the plane at ``wanted`` Z and T: whole Y and X, 0 on other axes.

    Y and X are kept whatever their length, so a mask one pixel high (or wide) stays 2D;
    every other length-1 axis is dropped (index 0). The header check (:func:`_mask_header`,
    :func:`_known_axes`) has already refused any other axis longer than 1.
    """
    index: list[int | slice] = []
    for a, n in zip(axes, shape, strict=True):
        if a in _YX:
            index.append(slice(None))
        elif a in wanted and n > 1:
            index.append(wanted.get(a) or 0)
        else:
            index.append(0 if n == 1 else slice(None))
    return tuple(index)


def _yx(axes: str, shape: tuple[int, ...]) -> tuple[int, int]:
    return shape[axes.index("Y")], shape[axes.index("X")]
