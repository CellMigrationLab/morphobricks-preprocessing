"""Read an exact window of one mask plane, as stored (one-route design section 5).

The mask companion of :mod:`..windows`: a window ``bounds = (y0, y1, x0, x1)`` of the plane at
one T and Z, half-open ``pixel_edge`` bounds in the plane frame (Spec ADR 0004 EW1), returned as
a ``(h, w)`` array that is bitwise the same as that slice of :func:`.read.read_mask`:

- **Integers as stored.** The window keeps the mask's own pixel type (native byte order) and
  its labels: nothing is relabelled, cast or made binary, whatever the representation
  (``instances``, ``binary``, ``parent_labels``). The window's pixels are checked with
  :func:`.labels.check_labels`, as the whole read checks the plane's.
- **The image window's rules.** Bounds, level and memory are checked, and the stored pieces
  read, by :mod:`..windows`: only the TIFF strips or tiles and OME-Zarr chunks the window
  intersects are decoded; bounds outside the plane, empty or not whole numbers
  (:class:`..windows.WindowBoundsError`), a level other than 0
  (:class:`..windows.WindowLevelError`), pages that cannot be read in parts
  (:class:`..windows.WindowLayoutError`) and a window over the memory limit
  (:class:`..windows.WindowMemoryError`) are refused, never read whole in their place.
- **The mask's planes as the whole read takes them.** Its axes are those its header names, or
  those stated for a plain stack (``mask.axes``); ``z`` and ``t`` are required for an axis the
  mask has and may only be None or 0 otherwise. A mask whose header names X before Y is
  refused, as the whole read refuses it.

Nothing here computes a digest of the file's content.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import tifffile

from ..axes import AxisMapping
from ..planes import DEFAULT_MEMORY_LIMIT, _open
from ..windows import (
    LEVEL,
    Bounds,
    WindowLayoutError,
    WindowPlan,
    _plan,
    _read_source,
    _Source,
    _tiff_source,
    _zarr_source,
    check_level,
)
from ..zarr_input import open_ngff_labels
from .header import refuse_lossy
from .labels import check_labels
from .layout import _stated_axes, _tiff_axes, _yx
from .model import _YX, FOLDER, PLANE_AXES, MaskFile
from .read import _stated, _wanted_plane


def read_mask_window(
    mask: MaskFile,
    masks_folder: Path | None,
    experiment_root: Path,
    *,
    bounds: Bounds,
    z: int | None = None,
    t: int | None = None,
    level: int = LEVEL,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> np.ndarray:
    """Read an exact window of one plane of a mask as a ``(h, w)`` array of its labels.

    Parameters
    ----------
    mask
        The mask, with its header's shape, pixel type and axes (as :func:`.read.read_mask`
        takes it).
    masks_folder, experiment_root
        Where its file is (the masks folder for a TIFF mask, the experiment for a label
        image).
    bounds
        ``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame, inside the
        plane.
    z, t
        The depth slice and time point, zero-based: required when the mask has that axis,
        None or 0 otherwise.
    level
        The resolution level; only the full-resolution level 0 is read.
    memory_limit
        Largest working memory allowed for the read, in bytes.

    Returns
    -------
    numpy.ndarray
        ``(y1 - y0, x1 - x0)``, in the mask's stored pixel type, bitwise that slice of
        :func:`.read.read_mask`.

    Raises
    ------
    WindowBoundsError, WindowLevelError, WindowLayoutError, WindowMemoryError
        As :func:`..windows.read_window`.
    PlaneReadError
        If the file cannot be read as an image.
    ValueError
        If ``z`` or ``t`` is missing or outside the mask, the mask is not the 2D plane its
        header gave, the window holds a label outside 0 to the int32 limit, or a TIFF mask is
        stored with a lossy codec (:class:`.header.MaskLossyCodecError`).
    """
    check_level(level)
    wanted = _wanted_plane(mask, z, t)
    with _mask_source(mask, masks_folder, experiment_root, memory_limit) as (source, mapping, path):
        # An axis the mask lacks has no coordinate (``_wanted_plane`` allowed only None or 0).
        z, t = (wanted[r] if mapping.index_of(r) is not None else None for r in ("Z", "T"))
        window = _read_source(source, mapping, path, bounds, (0,), z, t, memory_limit)[0]
    check_labels(window, f"the mask {mask.path}")
    return window


def mask_window_plan(
    mask: MaskFile,
    masks_folder: Path | None,
    experiment_root: Path,
    *,
    bounds: Bounds,
    level: int = LEVEL,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> WindowPlan:
    """Work out from the header what reading a mask window touches; no pixel is read.

    The arguments are those of :func:`read_mask_window`; the plane's T and Z are not needed.
    The plan is the image window's (:class:`..windows.WindowPlan`) with ``shape`` the
    ``(h, w)`` of the result, ``channels`` empty and ``dtype`` the mask's stored pixel type,
    so the memory estimate counts the mask's own bytes per pixel.

    Raises
    ------
    WindowBoundsError, WindowLevelError, WindowLayoutError
        As :func:`read_mask_window`.
    PlaneReadError
        If the file cannot be read as an image.
    ValueError
        If the mask is not the 2D plane its header gave.
    """
    check_level(level)
    with _mask_source(mask, masks_folder, experiment_root, memory_limit) as (source, _, _):
        plan = _plan(source, bounds, (0,), memory_limit)
    return replace(plan, channels=(), shape=plan.shape[1:])


@contextmanager
def _mask_source(
    mask: MaskFile, masks_folder: Path | None, experiment_root: Path, memory_limit: int
) -> Iterator[tuple[_Source, AxisMapping, Path]]:
    """Open a mask and yield its window source, the mapping of its stored axes and its path."""
    if mask.source == FOLDER:
        path = Path(masks_folder) / mask.path
        with _open(path) as tif:
            series = tif.series[0]
            refuse_lossy(series, path)
            mapping = _tiff_mapping(series, mask, path)
            yield _tiff_source(series, mapping, path, memory_limit), mapping, path
        return
    image, name = mask.path.rsplit("/labels/", 1)
    labels = open_ngff_labels(experiment_root / image, name)
    mapping = _mapping(labels.axes, labels.array.shape, labels.axes, mask)
    path = experiment_root / mask.path
    yield _zarr_source(labels, mapping, path, memory_limit), mapping, path


def _tiff_mapping(series: tifffile.TiffPageSeries, mask: MaskFile, path: Path) -> AxisMapping:
    """Return the mapping of a TIFF mask's series, refusing one whose pages hold no Y and X."""
    axes, shape = str(series.axes), tuple(int(n) for n in series.shape)
    if _tiff_axes(series)[0] != axes:
        raise WindowLayoutError(
            f"A window of {path.name} cannot be read: its header gives no Y and X axes (a 1D "
            "array stored as one page), so its pages cannot be read in parts. Save the mask "
            "again with its Y and X axes named, or read whole planes."
        )
    stated = _stated(mask)
    roles = _stated_axes(axes, shape, stated) if stated is not None else axes
    return _mapping(axes, shape, roles, mask)


def _mapping(axes: str, shape: tuple[int, ...], roles: str, mask: MaskFile) -> AxisMapping:
    """Return the mapping of a mask's stored axes: Y, X, Z and T roles, none for length 1.

    ``roles`` has one letter per stored axis (the header's own, or those stated for a plain
    stack, ``-`` for an axis left out). Y and X keep their role whatever their length; any
    other length-1 axis has none.

    Raises
    ------
    ValueError
        If the mask's plane is not the 2D ``YX`` plane its header gave (X before Y, an axis
        longer than 1 that is neither Y, X, Z nor T, or not the recorded shape), as
        :func:`.read.read_mask` refuses it.
    """
    kept = [r if n != 1 or r in _YX else "-" for r, n in zip(roles, shape, strict=True)]
    plane = "".join(r for r in kept if r not in ("-", *PLANE_AXES))
    if plane != "YX":
        raise ValueError(f"the mask {mask.path} is not the 2D plane its header gave ({plane})")
    mapping = AxisMapping(axes, tuple(kept))
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    if mask.axes and (shape[y], shape[x]) != tuple(_yx(mask.axes, mask.shape)):
        raise ValueError(f"the mask {mask.path} is not the 2D plane its header gave (YX)")
    return mapping
