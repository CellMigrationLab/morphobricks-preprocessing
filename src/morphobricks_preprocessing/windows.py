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
- **Channels** are C indices, chosen before anything is decoded. Channels that share stored
  pieces (TIFF samples stored interleaved, as in contiguous RGB strips or tiles, and OME-Zarr
  chunks longer than 1 along C) are read together: each piece the window intersects is
  decoded once and every channel asked is taken from it. Channels stored apart (a page per
  channel, planar samples) are read one after another, each from its own pieces. A channel
  not asked is never decoded unless it shares a stored piece with one that is.
- **Layouts.** TIFF pages stored in tiles or in strips (samples contiguous or planar, a page
  per channel or several channels in one page, with or without compression) are read through
  the block reader of :mod:`.planes`: only the strips or tiles the window intersects are
  read, and uncompressed ones only for the rows it needs. Pages that reader cannot split
  (subsampled colour, an NDPI JPEG header, or a plane whose axes are not the page's rows
  and columns) are refused (:class:`WindowLayoutError`), never read whole in their place.
  OME-Zarr images are read through zarr, which decodes only the chunks the window intersects.
- **Memory.** Before any pixel is read, the window's working memory is estimated from the
  header (:func:`window_plan`): the result, the stored pieces read into it (for TIFF, one
  piece at a time, written straight into the result; for OME-Zarr, every chunk the window
  intersects and zarr's copy of the channels read together), the decode's buffers, the
  codec's state and the open header (:mod:`.window_memory`, which states for which codecs it
  is a measured upper bound). A window over the limit is refused (:class:`WindowMemoryError`).

What a caller records with a window (it is never worked out again later): the source
fingerprint the run recorded for the image (size, modification time and, for OME-Zarr, the
inventory digest; checked again by :meth:`.run_inputs.RunInputs.read_window`), the level,
``bounds``, the channels and the T and Z of the plane. Nothing here computes a digest of a
file's content.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import window_memory
from .axes import AxisMapping
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    PlaneLayout,
    PlaneMemoryError,
    _Binning,
    _Blocking,
    _open,
    _open_zarr,
    _position,
    _tiff_piece_memory,
    _zarr_layout,
    _zarr_read,
    format_bytes,
)
from .window_source import Bounds, Cached, WindowError, _Source
from .window_source import WindowLayoutError as WindowLayoutError
from .window_tiff import _tiff_source
from .zarr_input import NgffImage, is_zarr_store

LEVEL = 0
"""The only resolution level read: full resolution (the declared level policy)."""


class WindowBoundsError(WindowError, ValueError):
    """The bounds are not whole numbers, empty, or not inside the plane."""


class WindowLevelError(WindowError, ValueError):
    """A resolution level other than the full-resolution level 0 was asked."""


class WindowChannelError(WindowError, ValueError):
    """The channels are not distinct C indices of the image, or none was asked."""


class WindowMemoryError(WindowError, PlaneMemoryError):
    """Reading the window would need more memory than the limit allows."""


class WindowSourceChangedError(WindowError):
    """The image changed (or went missing) since a window reader was opened on it."""


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
        Stored pieces (TIFF strips or tiles, OME-Zarr chunks) the window intersects in one
        plane.
    memory_estimate
        Estimated working memory of the read, in bytes.
    memory_limit
        The limit it is checked against, in bytes.
    reads
        Stored pieces read in all: ``pieces`` once for each group of channels that share
        them (one group for samples stored interleaved), once per channel otherwise.
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
    reads: int


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
    z: int | None = None,
    t: int | None = None,
    level: int = LEVEL,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> WindowPlan:
    """Work out from the header what reading a window touches; no pixel is read.

    The arguments are those of :func:`read_window`. With ``z`` or ``t``, the plan is that of
    the plane :func:`read_window` reads with them: a compressed TIFF's pieces are sized from
    the headers of the pages and pieces that read decodes (pages can compress very
    differently, issue #356). With neither, the plan holds for a window of any plane: they
    are sized from the largest stored piece of the series.

    Raises
    ------
    WindowBoundsError, WindowLevelError, WindowChannelError, WindowLayoutError
        As :func:`read_window`.
    PlaneReadError
        If the file cannot be read under ``mapping``.
    IndexError
        If ``z`` or ``t`` is outside the image, or given for an axis it lacks.
    """
    check_level(level)
    with _source(path, mapping, memory_limit) as source:
        position = plane_position(source, mapping, z, t)
        return _plan(source, bounds, channels, memory_limit, position)


def plane_position(
    source: _Source, mapping: AxisMapping, z: int | None, t: int | None
) -> dict[int, int] | None:
    """Return the plane a window plan is for: None (any plane) when neither z nor t is given.

    Otherwise the index per dimension other than Y and X that :func:`read_window` reads
    (C at 0; each group of channels is read at its own C).
    """
    if z is None and t is None:
        return None
    return _position(source.layout, mapping, {"Z": z, "T": t})


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
        return _read_source(source, mapping, path, bounds, channels, z, t, memory_limit)


def _read_source(
    source: _Source,
    mapping: AxisMapping,
    path: str | os.PathLike,
    bounds: object,
    channels: Sequence[int],
    z: int | None,
    t: int | None,
    memory_limit: int,
) -> np.ndarray:
    """Read a window of an open source as :func:`read_window` reads it (level already checked).

    For the readers of other arrays stored like images (the mask windows of
    :mod:`.masks.window`): the bounds, channels and memory are checked here, before any pixel
    is read.
    """
    # The plane read is the plane planned: the same pages and pieces (issue #356).
    position = _position(source.layout, mapping, {"Z": z, "T": t})
    plan = _plan(source, bounds, channels, memory_limit, position)
    if plan.memory_estimate > memory_limit:
        raise WindowMemoryError(_memory_text(plan, path))
    rows_are_y = source.layout.blocking.rows_are_y
    y0, y1, x0, x1 = plan.bounds
    box = (y0, y1, x0, x1) if rows_are_y else (x0, x1, y0, y1)
    out = np.empty(plan.shape, dtype=plan.dtype)
    # The pieces are written straight into the result, transposed for a plane stored X first.
    targets = out if rows_are_y else out.transpose(0, 2, 1)
    for group in source.groups(plan.channels):
        channels_read = [plan.channels[i] for i in group]
        source.read(position, box, channels_read, [targets[i] for i in group])
    return out


@contextmanager
def _source(
    path: str | os.PathLike, mapping: AxisMapping, memory_limit: int, cached: Cached | None = None
) -> Iterator[_Source]:
    """Open an image and yield its layout and a reader of stored boxes of one plane.

    With ``cached``, compressed TIFF pieces and OME-Zarr chunks are taken from a reader's cache.
    """
    if is_zarr_store(path):
        yield _zarr_source(_open_zarr(path), mapping, path, memory_limit, cached)
        return
    with _open(path) as tif:
        yield _tiff_source(tif.series[0], mapping, path, memory_limit, cached)


def _zarr_source(
    image: NgffImage,
    mapping: AxisMapping,
    path: str | os.PathLike,
    memory_limit: int,
    cached: Cached | None = None,
) -> _Source:
    """Return the source of an open OME-Zarr array: zarr decodes only the chunks read.

    With ``cached``, the window is read chunk by chunk, each chunk from the cache.
    """
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
    c_dim = mapping.index_of("C")

    def groups(channels: Sequence[int]) -> tuple[tuple[int, ...], ...]:
        # Channels in one chunk along C are read together: zarr decodes each chunk once.
        if c_dim is None:
            return (tuple(range(len(channels))),)
        found: dict[int, list[int]] = {}
        for i, c in enumerate(channels):
            found.setdefault(c // image.chunks[c_dim], []).append(i)
        return tuple(tuple(places) for places in found.values())

    def read(
        position: dict[int, int], box: Bounds, channels: Sequence[int], targets: Sequence
    ) -> None:
        if cached is not None:
            points = [position if c_dim is None else {**position, c_dim: c} for c in channels]
            _read_zarr_chunks(image, layout, points, box, targets, cached, path)
            return
        where = {**position, rows: slice(box[0], box[1]), cols: slice(box[2], box[3])}
        if c_dim is None:
            targets[0][...] = _zarr_read(image, tuple(where[d] for d in sorted(where)), path)
            return
        low = min(channels)
        where[c_dim] = slice(low, max(channels) + 1)
        pixels = _zarr_read(image, tuple(where[d] for d in sorted(where)), path)
        pixels = np.moveaxis(pixels, sorted((rows, cols, c_dim)).index(c_dim), 0)
        for target, c in zip(targets, channels, strict=True):
            target[...] = pixels[c - low]

    return _Source(layout, groups, read, window_memory.OPEN_BYTES, cached is not None)


def _read_zarr_chunks(
    image: NgffImage,
    layout: PlaneLayout,
    points: Sequence[dict[int, int]],
    box: Bounds,
    targets: Sequence[np.ndarray],
    cached: Cached,
    path: str | os.PathLike,
) -> None:
    """Fill ``targets`` with the stored box at each of ``points``, chunk by chunk from a cache.

    The points (one per channel) lie in the same chunks: they differ only along C, within one
    chunk. Each chunk the box intersects is read whole once (zarr decodes only it), through
    ``cached``, and every point's part of it is copied into its target.
    """
    rows, cols = layout.blocking.rows, layout.blocking.cols
    chunks, shape = image.chunks, layout.shape
    r0, r1, c0, c1 = box
    for row in range(r0 // chunks[rows], math.ceil(r1 / chunks[rows])):
        for col in range(c0 // chunks[cols], math.ceil(c1 / chunks[cols])):
            grid = [points[0].get(d, 0) // n for d, n in enumerate(chunks)]
            grid[rows], grid[cols] = row, col
            start = [g * n for g, n in zip(grid, chunks, strict=True)]
            selection = tuple(
                slice(a, min(a + n, size)) for a, n, size in zip(start, chunks, shape, strict=True)
            )
            chunk = cached(("zarr", tuple(grid)), lambda s=selection: _zarr_read(image, s, path))
            a, b = max(r0, start[rows]), min(r1, start[rows] + chunks[rows])
            ca, cb = max(c0, start[cols]), min(c1, start[cols] + chunks[cols])
            for target, point in zip(targets, points, strict=True):
                where = {d: point[d] - start[d] for d in point}
                where[rows] = slice(a - start[rows], b - start[rows])
                where[cols] = slice(ca - start[cols], cb - start[cols])
                target[a - r0 : b - r0, ca - c0 : cb - c0] = chunk[
                    tuple(where[d] for d in sorted(where))
                ]


def _plan(
    source: _Source,
    bounds: object,
    channels: Sequence[int],
    memory_limit: int,
    position: dict[int, int] | None = None,
) -> WindowPlan:
    """Check a window against an image's layout and estimate what reading it costs.

    ``position`` is the plane read (:func:`plane_position`); None plans for any plane.
    """
    layout = source.layout
    checked = check_bounds(bounds, layout.plane_shape)
    wanted = _check_channels(channels, layout.channel_count)
    groups = source.groups(wanted)
    y0, y1, x0, x1 = checked
    blocking = layout.blocking
    rows, cols = ((y0, y1), (x0, x1)) if blocking.rows_are_y else ((x0, x1), (y0, y1))
    pieces = _pieces(rows, blocking.unit[0]) * _pieces(cols, blocking.unit[1])
    pixels = (y1 - y0) * (x1 - x0)
    itemsize = layout.dtype.itemsize
    if blocking.kind == "zarr":
        # zarr decodes every chunk the window touches and returns a copy of the window for
        # every channel from the first to the last of a group read together.
        span = max(max(wanted[i] for i in g) - min(wanted[i] for i in g) + 1 for g in groups)
        work = window_memory.zarr_piece_work(pieces, blocking.piece_nbytes, source.cached)
        work += 2 * span * pixels * itemsize
    elif blocking.partial_rows:  # uncompressed rows read straight into the result
        work = _tiff_piece_memory(blocking, rows[1] - rows[0])
    else:  # one compressed strip or tile at a time, written straight into the result
        # Sized from the pieces the read decodes, not the keyframe's (issue #356).
        box = (*rows, *cols)
        read = [[wanted[i] for i in group] for group in groups]
        encoded = source.encoded(position, box, read)
        work = window_memory.tiff_piece_work(blocking.piece_nbytes, encoded, source.cached)
    return WindowPlan(
        bounds=checked,
        level=LEVEL,
        channels=wanted,
        shape=(len(wanted), y1 - y0, x1 - x0),
        dtype=layout.dtype,
        plane_shape=layout.plane_shape,
        pieces=pieces,
        memory_estimate=len(wanted) * pixels * itemsize + work + source.extra,
        memory_limit=memory_limit,
        reads=pieces * len(groups),
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
