"""A mask's header: its axes, shape and pixel type, read without pixels (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import numpy as np
import tifffile

from ..zarr_input import (
    open_ngff_labels,
)
from .layout import _tiff_axes
from .model import _YX, FOLDER, PLANE_AXES, MaskFile

MASK_LOSSY_CODEC = "mask_lossy_codec"
"""The error of a TIFF mask stored with a lossy codec (#360, owner decision of 24 November 2026)."""

LOSSY_CODECS: Mapping[int, str] = MappingProxyType(
    {
        6: "old-style JPEG",
        7: "JPEG",
        22610: "JPEG XR",
        33003: "JPEG 2000",
        33004: "JPEG 2000",
        33005: "JPEG 2000",
        33007: "JPEG (ALT_JPEG)",
        34712: "JPEG 2000",
        34892: "lossy JPEG (DNG)",
        34934: "JPEG XR",
        50001: "WebP",
        50002: "JPEG XL",
        52546: "JPEG XL",
    }
)
"""TIFF compression codes of lossy codecs, by name: a mask stored with one is refused."""


class MaskLossyCodecError(ValueError):
    """Raised when a TIFF mask is stored with a lossy codec (``mask_lossy_codec``, #360).

    A lossy codec changes pixel values, so the labels read may not be the labels written:
    such a mask is refused, never read. The message starts with the code and names the file
    and the codec.

    Attributes
    ----------
    path
        The mask, as given.
    codec
        The codec's name (:data:`LOSSY_CODECS`).
    """

    def __init__(self, path: str | Path, codec: str, code: int) -> None:
        self.path = str(path)
        self.codec = codec
        super().__init__(
            f"{MASK_LOSSY_CODEC}: the mask {path} is stored with the lossy codec {codec} (TIFF "
            f"compression {code}), so its labels may not be the ones written. Save the mask "
            "again uncompressed or with a lossless codec (zlib, LZW or Zstandard)."
        )


def refuse_lossy(series: tifffile.TiffPageSeries, path: str | Path) -> None:
    """Refuse a TIFF mask whose pages are stored with a lossy codec (:data:`LOSSY_CODECS`).

    The codec is the series' key page's: tifffile decodes every page of a series with it.

    Raises
    ------
    MaskLossyCodecError
        If the codec is lossy.
    """
    code = int(series.keyframe.compression)
    if code in LOSSY_CODECS:
        raise MaskLossyCodecError(path, LOSSY_CODECS[code], code)


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
            refuse_lossy(tif.series[0], mask.path)
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
