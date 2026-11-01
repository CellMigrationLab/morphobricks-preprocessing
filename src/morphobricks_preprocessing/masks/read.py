"""Read one plane of a mask, as stored (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import tifffile

from ..zarr_input import (
    NgffError,
    ZarrUnavailableError,
    is_zarr_name,
    open_ngff_labels,
)
from .header import _axes_problem, _known_axes, _mask_header
from .labels import check_labels
from .layout import _plane_index, _tiff_axes, _yx
from .model import FOLDER, OME_ZARR, MaskFile


def read_mask(
    mask: MaskFile,
    masks_folder: Path | None,
    experiment_root: Path,
    *,
    z: int | None = None,
    t: int | None = None,
) -> np.ndarray:
    """Read one plane of a mask as a 2D array.

    Parameters
    ----------
    mask
        The mask, as found by :func:`find_masks`.
    masks_folder, experiment_root
        Where its file is (:attr:`MaskInventory.folder` for a TIFF mask, the experiment for
        a label image).
    z, t
        The depth slice and time point to read, zero-based, the image's ``z`` and ``t``. Each
        is required when the mask has that axis (``mask.axes``), and may only be None or 0
        otherwise: a mask's planes are never guessed (issue #36).

    Raises
    ------
    ValueError
        If the mask's dimensions are not the ones its header gave (``mask.axes``), or ``z``
        or ``t`` is missing or outside the mask, or the plane holds a label outside 0 to
        :data:`LARGEST_LABEL` (:func:`check_labels`).
    """
    wanted = {"Z": z, "T": t}
    for axis, index in wanted.items():
        length = mask.planes[axis] if mask.axes else 1
        if index is None and length > 1:
            raise ValueError(
                f"the mask {mask.path} has {length} planes along {axis}; say which one to read"
            )
        if index is not None and not 0 <= index < length:
            raise ValueError(f"the mask {mask.path} has no plane {axis}={index}")
    if mask.source == FOLDER:
        pixels, axes = _read_tiff_plane(Path(masks_folder) / mask.path, wanted)
    else:
        name = mask.path.rsplit("/labels/", 1)[1]
        image = experiment_root / mask.path.rsplit("/labels/", 1)[0]
        labels = open_ngff_labels(image, name)
        selection = _plane_index(labels.axes, labels.array.shape, wanted)
        pixels = np.asarray(labels.array[selection])
        axes = "".join(a for a, s in zip(labels.axes, selection, strict=True) if s == slice(None))
    if axes != "YX" or (mask.axes and pixels.shape != tuple(_yx(mask.axes, mask.shape))):
        raise ValueError(f"the mask {mask.path} is not the 2D plane its header gave ({axes})")
    check_labels(pixels, f"the mask {mask.path}")
    return pixels


def read_mask_file(
    path: str | os.PathLike, *, z: int | None = None, t: int | None = None
) -> np.ndarray:
    """Read one plane of a mask file by its own header, without :func:`find_masks`.

    For bricks given a mask path directly (MorphoBricks D86): the mask is read as
    :func:`read_mask` reads it, with the same rules. Its axes are those its metadata names
    (length-1 axes aside, except Y and X: a mask one pixel high or wide is read as a 2D
    array): Y and X, plus at most a depth axis Z and a time axis T; a
    dimension the metadata does not name (a plain stack, ``IYX``) is refused, never taken
    for depth, time or a channel. ``z`` and ``t`` select the plane and are required for a
    mask with that axis. The labels are checked with :func:`check_labels`.

    Parameters
    ----------
    path
        A TIFF mask, or a label image of an OME-Zarr image, ``<image>.zarr/labels/<name>``
        (needs the ``zarr`` extra).
    z, t
        The depth slice and time point to read, zero-based.

    Returns
    -------
    numpy.ndarray
        The 2D plane, in the mask's own pixel type.

    Raises
    ------
    ValueError
        If the mask cannot be opened or holds several series, has a dimension its metadata
        does not name as Z or T, is not of integers or booleans, ``z`` or ``t`` is missing
        or outside the mask, or a label is outside 0 to :data:`LARGEST_LABEL`.
    """
    path = Path(path).absolute()  # the reader joins the mask's path to its folder
    label_image = path.parent.name == "labels" and is_zarr_name(path.parent.parent.name)
    mask = MaskFile("", "", path.as_posix(), OME_ZARR if label_image else FOLDER)
    try:
        axes, shape, dtype = _mask_header(mask, path.parent, path.parent)
    except (OSError, ValueError, tifffile.TiffFileError, NgffError, ZarrUnavailableError) as exc:
        raise ValueError(f"the mask {path} could not be opened: {exc}") from exc
    if not _known_axes(axes):
        raise ValueError(
            f"the mask {path} has axes {axes!r} (shape {shape}): {_axes_problem(axes, shape)}"
        )
    if dtype.kind not in "biu":
        raise ValueError(
            f"the mask {path} is of {dtype}, not integer labels or true/false; save the mask "
            "as integers"
        )
    return read_mask(
        MaskFile("", "", mask.path, mask.source, shape, dtype, axes),
        path.parent,
        path.parent,
        z=z,
        t=t,
    )


def _read_tiff_plane(path: Path, wanted: Mapping[str, int | None]) -> tuple[np.ndarray, str]:
    """Read the plane at ``wanted`` Z and T of a TIFF mask; return it and its axes.

    One page is read when the planes are stored one per page (the usual layout); otherwise
    the whole mask is read and the plane taken from it.
    """
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        axes, shape = _tiff_axes(series)
        index = _plane_index(axes, shape, wanted)
        kept = "".join(a for a, i in zip(axes, index, strict=True) if i == slice(None))
        pages = len(series.pages)
        split = next(
            (k for k in range(len(shape) + 1) if math.prod(shape[:k]) == pages),
            None,
        )
        if (
            split is not None
            and all(isinstance(i, int) for i in index[:split])
            and all(n == 1 or a in "YX" for a, n in zip(axes[split:], shape[split:], strict=True))
            and pages > 1
        ):
            key = int(np.ravel_multi_index(index[:split], shape[:split]))
            pixels = tif.asarray(key=key, series=0).reshape(shape[split:])
            pixels = pixels[index[split:]]
        else:
            pixels = series.asarray().reshape(shape)[index]
    return pixels, kept
