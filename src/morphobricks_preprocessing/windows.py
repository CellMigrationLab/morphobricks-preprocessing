"""Read an exact window of one plane, decoding only the stored pieces it covers (issue #179).

A window is a half-open rectangle ``bounds = (y0, y1, x0, x1)`` in ``pixel_edge`` coordinates
of the plane frame (Spec ADR 0004 EW1): rows ``y0`` to ``y1 - 1`` and columns ``x0`` to
``x1 - 1`` of the plane at one T and Z, Y before X whatever the stored order. The result is a
``(C, h, w)`` array of the stored values, in the stored pixel type (native byte order, as
:func:`.planes.read_plane` gives it), with the channels asked in the order asked. It is
bitwise the same as that slice of the whole plane read by :func:`.planes.read_plane`.

- **Exact window.** Reading is aligned on the pieces the image is stored in (TIFF strips or
  tiles, OME-Zarr chunks), but only the window is returned. Nothing is padded, filled or
  clipped: bounds outside the plane, empty bounds and bounds that are not whole numbers are
  refused (:class:`WindowBoundsError`). A caller that wants context asks for a larger window.
- **Level.** Only the full-resolution level 0 is read: it is the declared level policy (Spec
  one-route design 4.2). ``level`` is an argument so that it is always stated; any other
  level is refused (:class:`WindowLevelError`) until levels and their calibration are
  recorded.
- **Channels** are C indices, chosen before anything is decoded. Each channel is read on its
  own, so a channel not asked is never decoded unless it shares a stored piece with one that
  is (TIFF samples stored interleaved, OME-Zarr chunks longer than 1 along C); such a piece
  is decoded once per channel asked.
- **Layouts.** TIFF pages stored in tiles or in strips (samples contiguous or planar, a page
  per channel or several channels in one page, with or without compression) are read through
  the block reader of :mod:`.planes`: only the strips or tiles the window intersects are
  read, and uncompressed ones only for the rows it needs. Pages that reader cannot split
  (subsampled colour, an NDPI JPEG header, or a plane whose axes are not the page's rows
  and columns) are refused (:class:`WindowLayoutError`), never read whole in their place.
  OME-Zarr images are read through zarr, which decodes only the chunks the window intersects.
- **Memory.** Before any pixel is read, the window's working memory is estimated from the
  header (:func:`window_plan`): the result, one channel's copy and the stored pieces it
  needs. A window over the limit is refused (:class:`WindowMemoryError`).

What a caller records with a window (it is never worked out again later): the source
fingerprint the run recorded for the image (size, modification time and, for OME-Zarr, the
inventory digest; checked again by :meth:`.run_inputs.RunInputs.read_window`), the level,
``bounds``, the channels and the T and Z of the plane. Nothing here computes a digest of a
file's content.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import tifffile

from .axes import AxisMapping
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    PlaneLayout,
    PlaneMemoryError,
    PlaneReadError,
    _Binning,
    _block_memory,
    _Blocking,
    _layout,
    _open,
    _open_zarr,
    _page_index,
    _position,
    _read_tiff_block,
    _tiff_blocking,
    _zarr_layout,
    _zarr_read,
    format_bytes,
)
from .zarr_input import is_zarr_store

LEVEL = 0
"""The only resolution level read: full resolution (the declared level policy)."""

Bounds = tuple[int, int, int, int]
"""``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame."""


class WindowError(PlaneReadError):
    """Raised when a window cannot be read as asked."""


class WindowBoundsError(WindowError, ValueError):
    """The bounds are not whole numbers, empty, or not inside the plane."""


class WindowLevelError(WindowError, ValueError):
    """A resolution level other than the full-resolution level 0 was asked."""


class WindowChannelError(WindowError, ValueError):
    """The channels are not distinct C indices of the image, or none was asked."""


class WindowLayoutError(WindowError):
    """The image's pages are stored in a way that cannot be read in parts."""


class WindowMemoryError(WindowError, PlaneMemoryError):
    """Reading the window would need more memory than the limit allows."""


@dataclass(frozen=True)
class WindowPlan:
    """What a window read touches, worked out from the header before any pixel is read.

    Attributes
    ----------
    bounds
        ``(y0, y1, x0, x1)``, half-open ``pixel_edge`` bounds in the plane frame.
    level
        The resolution level read (always 0).
    channels
        The C indices read, in the order of the result.
    shape
        ``(C, h, w)`` of the result.
    dtype
        The stored pixel type, in native byte order.
    plane_shape
        ``(Y, X)`` of the whole plane.
    pieces
        Stored pieces (TIFF strips or tiles, OME-Zarr chunks) read for each channel.
    memory_estimate
        Estimated working memory of the read, in bytes.
    memory_limit
        The limit it is checked against, in bytes.
    """

    bounds: Bounds
    level: int
    channels: tuple[int, ...]
    shape: tuple[int, int, int]
    dtype: np.dtype
    plane_shape: tuple[int, int]
    pieces: int
    memory_estimate: int
    memory_limit: int


@dataclass(frozen=True)
class _Source:
    """An open image: its layout (with how planes are stored) and a reader of stored boxes."""

    layout: PlaneLayout
    read: Callable[[dict[int, int], Bounds], np.ndarray]


def check_bounds(bounds: object, plane_shape: tuple[int, int]) -> Bounds:
    """Return ``bounds`` as four ints after checking they are a window inside the plane.

    Raises
    ------
    WindowBoundsError
        If ``bounds`` is not four whole numbers ``(y0, y1, x0, x1)``, is empty along an axis,
        or reaches outside the ``(Y, X)`` plane. Nothing is clipped or padded.
    """
    if not isinstance(bounds, tuple | list) or len(bounds) != 4:  # noqa: PLR2004
        raise WindowBoundsError(
            f"A window's bounds are (y0, y1, x0, x1), four whole numbers; got {bounds!r}."
        )
    if any(isinstance(v, bool) or not isinstance(v, int | np.integer) for v in bounds):
        raise WindowBoundsError(
            f"A window's bounds are whole numbers (pixel edges); got {tuple(bounds)!r}."
        )
    y0, y1, x0, x1 = (int(v) for v in bounds)
    for name, low, high, size in (("y", y0, y1, plane_shape[0]), ("x", x0, x1, plane_shape[1])):
        if low >= high:
            raise WindowBoundsError(
                f"The window {name}0={low}, {name}1={high} is empty: bounds are half-open "
                f"[{name}0, {name}1), so {name}1 must be larger than {name}0."
            )
        if low < 0 or high > size:
            raise WindowBoundsError(
                f"The window [{low}, {high}) along {name.upper()} reaches outside the plane, "
                f"which has {size} pixels along {name.upper()} (0 to {size}). Nothing is "
                "padded or filled: ask for a window inside the plane."
            )
    return y0, y1, x0, x1


def check_level(level: object) -> int:
    """Return the level after checking it is the full-resolution level 0.

    Raises
    ------
    WindowLevelError
        For any other level, or a level that is not a whole number.
    """
    if isinstance(level, bool) or not isinstance(level, int | np.integer) or level != LEVEL:
        raise WindowLevelError(
            f"Only the full-resolution level {LEVEL} is read (the declared level policy); "
            f"got level={level!r}. Other levels are not supported yet."
        )
    return LEVEL


def window_plan(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    bounds: Bounds,
    channels: Sequence[int],
    level: int = LEVEL,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> WindowPlan:
    """Work out from the header what reading a window touches; no pixel is read.

    The arguments are those of :func:`read_window`; the plane's T and Z are not needed.

    Raises
    ------
    WindowBoundsError, WindowLevelError, WindowChannelError, WindowLayoutError
        As :func:`read_window`.
    PlaneReadError
        If the file cannot be read under ``mapping``.
    """
    check_level(level)
    with _source(path, mapping, memory_limit) as source:
        return _plan(source.layout, bounds, channels, memory_limit)


def read_window(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    bounds: Bounds,
    channels: Sequence[int],
    z: int | None = None,
    t: int | None = None,
    level: int = LEVEL,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> np.ndarray:
    """Read an exact window of one plane as a ``(C, h, w)`` array of the stored values.

    Parameters
    ----------
    path
        The TIFF file or OME-Zarr folder.
    mapping
        The axis mapping for this file (see :attr:`.axes.AxisReview.file_mappings`).
    bounds
        ``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame.
    channels
        The C indices to read, in the order of the result; ``(0,)`` for an image without a
        channel axis.
    z, t
        The plane; None is 0 for an axis the mapping has, and is required for one it lacks
        (as :func:`.planes.read_plane`).
    level
        The resolution level; only the full-resolution level 0 is read.
    memory_limit
        Largest working memory allowed for the read, in bytes.

    Returns
    -------
    numpy.ndarray
        ``(len(channels), y1 - y0, x1 - x0)``, in the stored pixel type (native byte order).

    Raises
    ------
    WindowBoundsError
        If the bounds are not whole numbers, empty, or not inside the plane.
    WindowLevelError
        For a level other than 0.
    WindowChannelError
        If no channel, a repeated channel or one outside the image is asked.
    WindowLayoutError
        If the image's pages cannot be read in parts.
    WindowMemoryError
        If the window needs more memory than ``memory_limit``.
    PlaneReadError
        If the file cannot be read under ``mapping``.
    IndexError
        If ``z`` or ``t`` is outside the image, or given for an axis it lacks.
    """
    check_level(level)
    with _source(path, mapping, memory_limit) as source:
        plan = _plan(source.layout, bounds, channels, memory_limit)
        if plan.memory_estimate > memory_limit:
            raise WindowMemoryError(_memory_text(plan, path))
        c_dim = mapping.index_of("C")
        rows_are_y = source.layout.blocking.rows_are_y
        y0, y1, x0, x1 = plan.bounds
        box = (y0, y1, x0, x1) if rows_are_y else (x0, x1, y0, y1)
        out = np.empty(plan.shape, dtype=plan.dtype)
        for i, c in enumerate(plan.channels):
            wanted = {"Z": z, "C": None if c_dim is None else c, "T": t}
            pixels = source.read(_position(source.layout, mapping, wanted), box)
            out[i] = pixels if rows_are_y else pixels.T
        return out


@contextmanager
def _source(path: str | os.PathLike, mapping: AxisMapping, memory_limit: int) -> Iterator[_Source]:
    """Open an image and yield its layout and a reader of stored boxes of one plane."""
    if is_zarr_store(path):
        image = _open_zarr(path)
        layout = _zarr_layout(image, mapping, path, _Binning(histogram=False), memory_limit)
        y, x = mapping.index_of("Y"), mapping.index_of("X")
        rows, cols = sorted((y, x))
        blocking = _Blocking(
            kind="zarr",
            rows=rows,
            cols=cols,
            rows_are_y=rows == y,
            unit=(image.chunks[rows], image.chunks[cols]),
            partial_rows=True,
            piece_nbytes=math.prod(image.chunks) * layout.dtype.itemsize,
        )
        layout = replace(layout, blocking=blocking)

        def read(position: dict[int, int], box: Bounds) -> np.ndarray:
            where = {**position, rows: slice(box[0], box[1]), cols: slice(box[2], box[3])}
            selection = tuple(where[d] for d in range(len(layout.shape)))
            return _zarr_read(image, selection, path)

        yield _Source(layout, read)
        return
    with _open(path) as tif:
        series = tif.series[0]
        layout = _tiff_layout(series, mapping, path, memory_limit)

        def read_tiff(position: dict[int, int], box: Bounds) -> np.ndarray:
            index = _page_index(layout, position)
            page = series.pages[index]
            if page is None:
                raise PlaneReadError(f"Page {index} of {path} is missing from the file.")
            # The plane's index along the separate-sample, depth and contiguous-sample axes.
            where = [0] * 5
            for dim, stored in layout.blocking.stored:
                if stored not in (2, 3):
                    where[stored] = position[dim]
            return _read_tiff_block(page, layout, where, box, path)

        yield _Source(layout, read_tiff)


def _tiff_layout(
    series: tifffile.TiffPageSeries,
    mapping: AxisMapping,
    path: str | os.PathLike,
    memory_limit: int,
) -> PlaneLayout:
    """Return a TIFF series' layout with how its pages are stored, or refuse it."""
    layout = _layout(series, mapping, path, _Binning(histogram=False), memory_limit)
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    blocking = _tiff_blocking(series.keyframe, layout.shape, layout.page_ndim, y, x)
    if blocking is None:
        raise WindowLayoutError(
            f"A window of {Path(path).name} cannot be read: its pages are stored in a way that "
            "cannot be read in parts (subsampled colour, an NDPI JPEG header, or a plane whose "
            "axes are not the rows and columns of its pages). Save the image again as a tiled "
            "TIFF or as OME-Zarr, or read whole planes."
        )
    return replace(layout, blocking=blocking)


def _plan(
    layout: PlaneLayout, bounds: object, channels: Sequence[int], memory_limit: int
) -> WindowPlan:
    """Check a window against an image's layout and estimate what reading it costs."""
    checked = check_bounds(bounds, layout.plane_shape)
    wanted = _check_channels(channels, layout.channel_count)
    y0, y1, x0, x1 = checked
    blocking = layout.blocking
    rows, cols = ((y0, y1), (x0, x1)) if blocking.rows_are_y else ((x0, x1), (y0, y1))
    pieces = _pieces(rows, blocking.unit[0]) * _pieces(cols, blocking.unit[1])
    pixels = (y1 - y0) * (x1 - x0)
    itemsize = layout.dtype.itemsize
    if blocking.kind == "zarr":
        # zarr decodes every chunk the window touches and returns a copy of the window.
        channel = pieces * blocking.piece_nbytes + 2 * pixels * itemsize
    else:
        size = (rows[1] - rows[0], cols[1] - cols[0])
        channel = _block_memory(layout, blocking, *size, _Binning(histogram=False))
    return WindowPlan(
        bounds=checked,
        level=LEVEL,
        channels=wanted,
        shape=(len(wanted), y1 - y0, x1 - x0),
        dtype=layout.dtype,
        plane_shape=layout.plane_shape,
        pieces=pieces,
        memory_estimate=len(wanted) * pixels * itemsize + channel,
        memory_limit=memory_limit,
    )


def _pieces(span: tuple[int, int], unit: int) -> int:
    """Return how many stored pieces of ``unit`` pixels the half-open ``span`` touches."""
    return math.ceil(span[1] / unit) - span[0] // unit


def _check_channels(channels: Sequence[int], count: int) -> tuple[int, ...]:
    """Return the C indices asked after checking they are distinct indices of the image."""
    if isinstance(channels, str | bytes) or not isinstance(channels, Sequence):
        raise WindowChannelError(
            f"The channels of a window are a list of C indices; got {channels!r}."
        )
    if not channels:
        raise WindowChannelError("A window needs at least one channel; none was asked.")
    for c in channels:
        if isinstance(c, bool) or not isinstance(c, int | np.integer) or not 0 <= c < count:
            raise WindowChannelError(
                f"Channel {c!r} is not a C index of the image, which has {count} "
                f"channel(s) (0 to {count - 1})."
            )
    if len(set(channels)) != len(channels):
        raise WindowChannelError(f"A channel is asked more than once: {tuple(channels)!r}.")
    return tuple(int(c) for c in channels)


def _memory_text(plan: WindowPlan, path: str | os.PathLike) -> str:
    """Word the refusal of a window over the memory limit."""
    return (
        f"Reading the window {plan.bounds} of {Path(path).name} needs about "
        f"{format_bytes(plan.memory_estimate)} of working memory, more than the limit of "
        f"{format_bytes(plan.memory_limit)}. Ask for a smaller window or fewer channels, or "
        "raise the memory limit if this computer has enough memory."
    )
