"""A mask's header: its axes, shape and pixel type, read without pixels (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from ..zarr_input import (
    open_ngff_labels,
)
from .layout import _tiff_axes
from .model import _YX, FOLDER, PLANE_AXES, MaskFile


def _mask_header(mask: MaskFile, folder: Path | None, root: Path) -> tuple:
    """Return a mask's axes and shape without length-1 axes other than Y and X, and its dtype.

    The axes are the mask's own metadata (``tifffile``'s series axes, or the OME-NGFF axes of
    the label image); no pixels are read. Y and X are kept whatever their length: a mask one
    pixel high is ``YX`` with shape ``(1, N)``, never the 1D ``X`` (``tifffile`` and OME-NGFF
    give both axes, so nothing is guessed). Only the other length-1 axes are dropped.
    """
    if mask.source == FOLDER:
        with tifffile.TiffFile(Path(folder) / mask.path) as tif:
            if len(tif.series) != 1:
                raise ValueError(f"{len(tif.series)} image series")
            axes, shape = _tiff_axes(tif.series[0])
            dtype = tif.series[0].dtype
    else:
        image, name = mask.path.rsplit("/labels/", 1)
        labels = open_ngff_labels(root / image, name)
        axes, shape, dtype = labels.axes, labels.array.shape, np.dtype(labels.array.dtype)
    kept = [(a, int(n)) for a, n in zip(axes, shape, strict=True) if n != 1 or a in _YX]
    return (
        "".join(a for a, _ in kept),
        tuple(n for _, n in kept),
        dtype.newbyteorder("="),
    )


def _axes_problem(axes: str, shape: tuple[int, ...]) -> str:
    """Say why axes that :func:`_known_axes` refuses are not a mask's.

    The axes are :func:`_mask_header`'s: length-1 axes other than Y and X already left out.
    """
    missing = [a for a in "YX" if a not in axes]
    if missing:
        return (
            f"its header gives no {' or '.join(missing)} axis, so its rows and columns are not "
            "known"
        )
    repeated = sorted({a for a in axes if axes.count(a) > 1})
    if repeated:
        return f"its header gives {', '.join(repeated)} more than once"
    other = [
        f"{a} (length {n})"
        for a, n in zip(axes, shape, strict=True)
        if a not in _YX and a not in PLANE_AXES
    ]
    several = len(other) > 1
    return (
        f"the dimension{'s' if several else ''} {', '.join(other)} {'are' if several else 'is'} "
        f"neither Y nor X, and its metadata does not name {'them' if several else 'it'} depth "
        "(Z) or time (T)"
    )


def _known_axes(axes: str) -> bool:
    """Return True if the axes are Y and X, each once, plus at most one Z and one T."""
    return (
        axes.count("Y") == 1
        and axes.count("X") == 1
        and all(axes.count(a) <= 1 for a in PLANE_AXES)
        and set(axes) <= {"Y", "X", *PLANE_AXES}
    )
