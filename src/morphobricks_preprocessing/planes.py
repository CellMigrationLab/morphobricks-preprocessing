"""Read the 2D planes of an image one at a time under a confirmed axis mapping.

Images are TIFF files or OME-Zarr folders.

For TIFF files, the unit ``tifffile`` decodes is a TIFF page. Most microscopy
files store one plane per page; RGB pages hold one plane per sample, and
volumetric pages hold a whole stack. Each page is decoded once and the planes in
it are yielded before the next page is read, so no more than one page is ever in
memory, never a full Z/C/T volume (unless the file stores the volume as a single
page).

For OME-Zarr images, each plane is read on its own from the level-0 array; zarr
decodes only the chunks the plane touches. A chunk that spans several planes is
decoded again for each of them, which costs time but never memory.

A row of the histogram table is an XYC plane: every channel of an image at one
Z and T position (normalization contract §6.2). :func:`iter_xyc_planes` yields
them; their channels are read and binned one after another, so only one
channel's pixels are in memory at a time, next to the histograms of the channels
already done. A page that holds several channels is decoded once for all of
them; a page that holds planes of several Z or T positions is decoded again for
each XYC plane when the channels are stored in different pages.

Before anything is decoded, each plane's working memory is estimated from the
header: the decoded page (for OME-Zarr, every chunk the plane touches), its
encoded bytes, a copy of the plane, the temporary arrays of the histogram and
the finished histograms of every channel of the XYC plane. One decision, whole
or in blocks, applies to every channel of every plane of a file.

Planes above the memory limit (default 1 GiB) are read in blocks instead
(``docs/plan.md`` §5): rectangles made of the pieces the image is stored in
(TIFF strips or tiles, OME-Zarr chunks), as large as the limit allows. Such a
plane is never held in memory as a whole; :meth:`Plane.blocks` reads its blocks
again each time it is called, and :meth:`Plane.histogram` reads them twice to
get the same histogram as from the whole plane. Uncompressed TIFF pieces and
OME-Zarr chunks can also be read a few rows at a time. A compressed piece must
be decoded whole, so a plane stored compressed in one piece cannot be split; it
is reported by :func:`check_planes` and refused by the readers, like any plane
whose smallest block would still need more memory than the limit.

Only the first image (series) is read, at full-resolution level 0 for pyramidal
files (``docs/contract.md`` §2).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import tifffile
from morphobricks_normalization.dtypes import default_bin_count
from morphobricks_normalization.histogram import (
    PlaneHistogram,
    compute_block_histogram,
    estimate_histogram_memory,
    histogram_with_range_check,
)
from morphobricks_normalization.ranges import HistogramSettings, RangeCheck

from .axes import PLANE_ROLES, AxisMapping, AxisReview
from .validation import ValidationIssue, error, plural, warning
from .zarr_input import (
    NgffError,
    NgffImage,
    ZarrUnavailableError,
    is_zarr_store,
    open_ngff,
    read_selection,
)

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_LIMIT = 2**30
"""Default working-memory limit per plane: 1 GiB."""

PREVIEW_SIDE = 4096
"""A reduced preview of a plane read in blocks has at most this many pixels per side."""

# TIFF compression codes that need the JPEG tables of the file to decode a piece.
_JPEG_COMPRESSIONS = {6, 7, 33007, 34892}


class PlaneReadError(Exception):
    """Raised when a file's planes cannot be read under the given axis mapping."""


class PlaneMemoryError(PlaneReadError):
    """Raised when reading a plane would need more memory than the limit allows."""


@dataclass(frozen=True)
class Block:
    """A rectangle of a plane.

    Attributes
    ----------
    y, x
        Plane row and column of the block's first pixel.
    pixels
        The block as a (Y, X) array in native byte order.
    """

    y: int
    x: int
    pixels: np.ndarray


@dataclass(frozen=True)
class BlockReader:
    """Reads a plane that is too large to hold whole, block by block.

    Attributes
    ----------
    shape
        (Y, X) shape of the whole plane.
    dtype
        Pixel type, in native byte order.
    read
        Called to read the blocks; each call reads them again from the file.
    """

    shape: tuple[int, int]
    dtype: np.dtype
    read: Callable[[], Iterator[Block]] = field(repr=False, compare=False)


@dataclass(frozen=True)
class Plane:
    """One 2D plane of an image.

    Attributes
    ----------
    z, c, t
        Zero-based coordinates; None when the experiment mapping has no such axis.
    pixels
        The plane as a (Y, X) array in native byte order, with the source values
        unchanged. None for a plane read in blocks (see :meth:`blocks`).
    step
        1 for the plane itself. A reduced preview of a plane read in blocks keeps
        only every ``step``-th pixel along Y and X.
    reader
        For a plane read in blocks (or its reduced preview): reads its blocks
        from the open file. Use it before asking the reader for the next plane.
    """

    z: int | None
    c: int | None
    t: int | None
    pixels: np.ndarray | None
    step: int = 1
    reader: BlockReader | None = field(default=None, repr=False, compare=False)

    @property
    def coordinates(self) -> dict[str, int | None]:
        """The coordinates as ``{"z": ..., "c": ..., "t": ...}``."""
        return {"z": self.z, "c": self.c, "t": self.t}

    @property
    def in_blocks(self) -> bool:
        """True when the plane is read in blocks and not held in memory."""
        return self.pixels is None

    @property
    def shape(self) -> tuple[int, int]:
        """(Y, X) shape of the whole plane (not of a reduced preview)."""
        if self.reader is not None:
            return self.reader.shape
        return tuple(self.pixels.shape)

    @property
    def dtype(self) -> np.dtype:
        """Pixel type of the plane."""
        return self.pixels.dtype if self.pixels is not None else self.reader.dtype

    def blocks(self) -> Iterator[Block]:
        """Yield the plane in blocks that together hold every pixel exactly once.

        A plane held in memory is a single block. A plane read in blocks reads
        them from the file again on every call.

        Raises
        ------
        ValueError
            For a reduced preview, which no longer holds every pixel.
        PlaneReadError
            If a block cannot be read.
        """
        if self.step != 1:
            raise ValueError("A reduced preview does not hold every pixel of the plane.")
        if self.pixels is not None:
            yield Block(0, 0, self.pixels)
        else:
            yield from self.reader.read()

    def histogram(
        self, bin_count: int | None = None, settings: HistogramSettings | None = None
    ) -> PlaneHistogram:
        """Compute the plane's histogram; identical whether or not it is read in blocks.

        ``settings`` chooses the histogram range of histograms that are not
        exact (normalization contract §1.4); None uses the minimum to maximum.
        """
        return compute_block_histogram(
            lambda: (block.pixels for block in self.blocks()),
            self.dtype,
            bin_count,
            settings=settings,
        )

    def histogram_with_check(
        self, bin_count: int | None = None, settings: HistogramSettings | None = None
    ) -> tuple[PlaneHistogram, RangeCheck | None]:
        """Compute the plane's histogram and its range check, in the same passes over the plane.

        The check is None for an exact histogram (normalization brick
        ``histogram_with_range_check``). Both are identical whether or not the
        plane is read in blocks.
        """
        return histogram_with_range_check(
            lambda: (block.pixels for block in self.blocks()),
            self.dtype,
            bin_count,
            settings=settings,
        )


@dataclass(frozen=True)
class XycPlane:
    """Every channel of one image at one Z and T position: one row of the table.

    The channels are read on request, one at a time, from the open file. Use
    the plane before asking :func:`iter_xyc_planes` for the next one.

    Attributes
    ----------
    z, t
        Zero-based coordinates; None when the experiment mapping has no such axis.
    channel_count
        Number of channels; 1 for an image without a channel axis.
    read
        Called as ``read(c)`` to read channel ``c`` as a :class:`Plane`.
    """

    z: int | None
    t: int | None
    channel_count: int
    read: Callable[[int], Plane] = field(repr=False, compare=False)

    def channel(self, c: int) -> Plane:
        """Read channel ``c`` (zero-based)."""
        if not 0 <= c < self.channel_count:
            raise IndexError(f"The plane has {self.channel_count} channel(s); got c={c}.")
        return self.read(c)

    def histograms(
        self, bin_count: int | None = None, settings: HistogramSettings | None = None
    ) -> tuple[PlaneHistogram, ...]:
        """Compute the histogram of every channel, reading one channel at a time.

        Each histogram is identical to that of the channel's plane read whole.
        """
        return tuple(
            self.channel(c).histogram(bin_count, settings) for c in range(self.channel_count)
        )

    def histograms_with_checks(
        self, bin_count: int | None = None, settings: HistogramSettings | None = None
    ) -> tuple[tuple[PlaneHistogram, RangeCheck | None], ...]:
        """Compute every channel's histogram and range check, reading one channel at a time."""
        return tuple(
            self.channel(c).histogram_with_check(bin_count, settings)
            for c in range(self.channel_count)
        )


@dataclass(frozen=True)
class _Blocking:
    """How a plane can be split into blocks, in the order of its stored axes.

    ``rows`` and ``cols`` are the stored dimensions of the plane's two axes, in
    array order (``rows`` first); blocks are rectangles of rows and columns of
    those, aligned to the stored pieces (``unit`` rows and columns). Pieces that
    can be read a few rows at a time allow blocks of fewer rows than a piece.
    """

    kind: str  # "tiff" or "zarr"
    rows: int
    cols: int
    rows_are_y: bool
    unit: tuple[int, int]
    partial_rows: bool
    piece_nbytes: int
    encoded_nbytes: int = 0
    row_nbytes: int = 0
    stored: tuple[tuple[int, int], ...] = ()  # TIFF: (series dimension, stored dimension)


@dataclass(frozen=True)
class PlaneLayout:
    """How the planes of one file are read, worked out from its header.

    Attributes
    ----------
    shape
        Shape of the image series, one length per axis of the mapping.
    dtype
        Pixel type, in native byte order.
    plane_shape
        (Y, X) shape of each plane.
    plane_count
        Number of 2D planes in the file.
    channel_count
        Number of channels of each XYC plane; 1 without a channel axis.
    page_shape
        Shape of the unit decoded at once: one TIFF page, as a part of ``shape``,
        or the chunk shape of an OME-Zarr array.
    page_nbytes
        Size of a decoded page in bytes; for OME-Zarr, of all chunks one plane
        touches.
    encoded_nbytes
        Size of a page as stored in the file (compressed size, if compressed); 0
        for OME-Zarr, whose compressed chunk sizes are not known in advance.
    memory_estimate
        Estimated working memory to read one whole plane and compute its
        histogram, holding the finished histograms of every channel of its XYC
        plane, in bytes.
    memory_limit
        The memory limit the layout was worked out for, in bytes.
    block_shape
        (Y, X) size of the blocks when planes are over the memory limit and are
        read in blocks; None when planes are read whole or cannot be read.
    block_memory_estimate
        Estimated working memory for one block when planes are read in blocks;
        for planes that cannot be read within the limit, for the smallest block
        possible. None when planes fit whole or cannot be split.
    """

    shape: tuple[int, ...]
    dtype: np.dtype
    plane_shape: tuple[int, int]
    plane_count: int
    page_shape: tuple[int, ...]
    page_nbytes: int
    encoded_nbytes: int
    memory_estimate: int
    memory_limit: int = DEFAULT_MEMORY_LIMIT
    channel_count: int = 1
    block_shape: tuple[int, int] | None = None
    block_memory_estimate: int | None = None
    blocking: _Blocking | None = field(default=None, repr=False, compare=False)

    @property
    def page_ndim(self) -> int:
        """Number of leading axes that select a page; the others lie within a page."""
        return len(self.shape) - len(self.page_shape)

    @property
    def in_blocks(self) -> bool:
        """True when planes are over the memory limit and are read in blocks."""
        return self.block_shape is not None

    @property
    def fits(self) -> bool:
        """True when planes can be read within the memory limit, whole or in blocks."""
        return self.memory_estimate <= self.memory_limit or self.in_blocks

    @property
    def min_memory_estimate(self) -> int:
        """Working memory of the smallest part of a plane that can be read on its own."""
        if self.block_memory_estimate is None:
            return self.memory_estimate
        return min(self.memory_estimate, self.block_memory_estimate)


def plane_layout(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    bin_count: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    histogram_settings: HistogramSettings | None = None,
) -> PlaneLayout:
    """Work out from the header how the planes of a file are read.

    Parameters
    ----------
    path
        The TIFF file or OME-Zarr folder.
    mapping
        The axis mapping for this file (see :attr:`AxisReview.file_mappings`).
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.
    histogram_settings
        Histogram range of the run, for the memory estimate, which always
        includes the range check; the minimum to maximum if None.
    memory_limit
        Largest working memory allowed for one plane, in bytes; planes above it
        are read in blocks when possible.

    Raises
    ------
    PlaneReadError
        If the file does not open, the mapping does not fit it, or its planes
        cannot be read one at a time.
    """
    if is_zarr_store(path):
        return _zarr_layout(
            _open_zarr(path), mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )
    with _open(path) as tif:
        return _layout(
            tif.series[0], mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )


def iter_planes(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    bin_count: int | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> Iterator[Plane]:
    """Yield every 2D plane of a file, one page read at a time.

    Planes come in the file's storage order (the order of the axes). Each plane
    is checked against the memory limit before the file's pixel data is read.
    Planes over the limit are yielded without pixels and read in blocks (see
    :meth:`Plane.blocks`); use each such plane before asking for the next one,
    while the file is still open.

    Parameters
    ----------
    path
        The TIFF file or OME-Zarr folder.
    mapping
        The axis mapping for this file (see :attr:`AxisReview.file_mappings`).
    memory_limit
        Largest working memory allowed for one plane or block, in bytes.
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.
    histogram_settings
        Histogram range of the run, for the memory estimate, which always
        includes the range check; the minimum to maximum if None.

    Yields
    ------
    Plane

    Raises
    ------
    PlaneMemoryError
        If a plane cannot be read within ``memory_limit``, not even in blocks.
    PlaneReadError
        If the file cannot be read plane by plane under ``mapping``.
    """
    if is_zarr_store(path):
        image = _open_zarr(path)
        layout = _zarr_layout(
            image, mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )
        _check_limit(layout, path)
        for position in _positions(layout, mapping):
            yield _zarr_plane(image, layout, mapping, position, path)
        return
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(
            series, mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )
        _check_limit(layout, path)
        read = _tiff_reader(series, layout, mapping, path)
        for position in _positions(layout, mapping):
            yield read(position)


def iter_xyc_planes(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    bin_count: int | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> Iterator[XycPlane]:
    """Yield every XYC plane of a file: all channels at each Z and T position.

    Planes come in the file's storage order of the axes other than C. Their
    channels are read on request, one at a time (see :meth:`XycPlane.channel`),
    with the same memory check and block reading as :func:`iter_planes`; use
    each plane before asking for the next one, while the file is still open.

    Parameters
    ----------
    path
        The TIFF file or OME-Zarr folder.
    mapping
        The axis mapping for this file (see :attr:`AxisReview.file_mappings`).
    memory_limit
        Largest working memory allowed for one plane or block, in bytes.
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.
    histogram_settings
        Histogram range of the run, for the memory estimate, which always
        includes the range check; the minimum to maximum if None.

    Yields
    ------
    XycPlane

    Raises
    ------
    PlaneMemoryError
        If a plane cannot be read within ``memory_limit``, not even in blocks.
    PlaneReadError
        If the file cannot be read plane by plane under ``mapping``.
    """
    if is_zarr_store(path):
        image = _open_zarr(path)
        layout = _zarr_layout(
            image, mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )
        _check_limit(layout, path)
        yield from _xyc_planes(
            layout,
            mapping,
            lambda position: _zarr_plane(image, layout, mapping, position, path),
        )
        return
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(
            series, mapping, path, _Binning(bin_count, histogram_settings), memory_limit
        )
        _check_limit(layout, path)
        yield from _xyc_planes(layout, mapping, _tiff_reader(series, layout, mapping, path))


def read_plane(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    z: int | None = None,
    c: int | None = None,
    t: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    preview: bool = False,
) -> Plane:
    """Read one plane, decoding only the page that holds it.

    Coordinates left as None default to 0 for the axes the mapping has.

    Parameters
    ----------
    preview
        For a plane over the memory limit, return a reduced preview read in
        blocks (only every n-th pixel along Y and X, see :attr:`Plane.step`)
        instead of raising :class:`PlaneMemoryError`.

    Raises
    ------
    PlaneMemoryError
        If the plane needs more memory than ``memory_limit`` (with ``preview``,
        if not even a reduced preview can be read within it).
    PlaneReadError
        If the file cannot be read under ``mapping``.
    IndexError
        If a coordinate is outside the image, or given for an axis it lacks.
    """
    wanted = {"Z": z, "C": c, "T": t}
    if is_zarr_store(path):
        image = _open_zarr(path)
        layout = _zarr_layout(image, mapping, path, _Binning(), memory_limit)
        layout = _whole_or_preview(layout, path, preview, _zarr_reblock(image, mapping, path))
        position = _position(layout, mapping, wanted)
        return _whole_or_reduced(_zarr_plane(image, layout, mapping, position, path), layout)
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, _Binning(), memory_limit)
        layout = _whole_or_preview(layout, path, preview, _tiff_reblock(series, mapping, path))
        return _whole_or_reduced(_read_one(series, layout, mapping, path, wanted), layout)


def read_sample_plane(
    path: str | os.PathLike, mapping: AxisMapping, *, memory_limit: int = DEFAULT_MEMORY_LIMIT
) -> Plane:
    """Read one plane for the axis preview: the middle Z slice, first C and T.

    The middle slice is chosen because the first slice of a stack is often out
    of focus. Only the page (or chunks) holding that plane is decoded. A plane
    over the memory limit is returned as a reduced preview (see
    :func:`read_plane`).
    """
    if is_zarr_store(path):
        dim = mapping.index_of("Z")
        image = _open_zarr(path)
        z = None if dim is None else image.shape[dim] // 2
        return read_plane(path, mapping, z=z, memory_limit=memory_limit, preview=True)
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, _Binning(), memory_limit)
        layout = _whole_or_preview(layout, path, True, _tiff_reblock(series, mapping, path))
        dim = mapping.index_of("Z")
        z = None if dim is None else layout.shape[dim] // 2
        wanted = {"Z": z, "C": None, "T": None}
        return _whole_or_reduced(_read_one(series, layout, mapping, path, wanted), layout)


def check_planes(
    experiment_root: str | os.PathLike,
    review: AxisReview | Mapping[str, AxisMapping],
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    bin_count: int | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> list[ValidationIssue]:
    """Check from the headers that every file can be read plane by plane.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    review
        The axis review, or its per-file mappings keyed by relative path.
    memory_limit
        Largest working memory allowed for one plane, in bytes.
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.
    histogram_settings
        Histogram range of the run, for the memory estimate, which always
        includes the range check; the minimum to maximum if None.

    Returns
    -------
    list of ValidationIssue
        Errors for planes that cannot be read within the memory limit, not even
        in blocks (``plane_over_memory_limit``), and for files whose planes
        cannot be read one at a time (``unsupported_layout``); a warning naming
        the files whose planes are read in blocks (``planes_read_in_blocks``).
    """
    mappings = review.file_mappings if isinstance(review, AxisReview) else review
    root = Path(experiment_root)
    over: list[tuple[str, PlaneLayout]] = []
    blocked: list[tuple[str, PlaneLayout]] = []
    unsupported: list[str] = []
    for rel in sorted(mappings):
        try:
            layout = plane_layout(
                root / rel,
                mappings[rel],
                bin_count=bin_count,
                memory_limit=memory_limit,
                histogram_settings=histogram_settings,
            )
        except PlaneReadError:
            logger.debug("Cannot read %s plane by plane", rel, exc_info=True)
            unsupported.append(rel)
            continue
        if not layout.fits:
            over.append((rel, layout))
        elif layout.in_blocks:
            blocked.append((rel, layout))

    issues = []
    if over:
        largest = max(layout.min_memory_estimate for _, layout in over)
        issues.append(
            error(
                "plane_over_memory_limit",
                f"{plural(len(over), 'image')} cannot be read within the memory limit of "
                f"{format_bytes(memory_limit)}, not even in blocks: the smallest part of a "
                f"plane that can be read on its own needs up to {format_bytes(largest)}. A "
                "plane stored compressed in one piece has to be decoded whole.",
                [rel for rel, _ in over],
                "Raise the memory limit if this computer has enough memory, or save the "
                "images again as tiled or uncompressed TIFF files or as OME-Zarr with smaller "
                "chunks.",
            )
        )
    if blocked:
        largest = max(layout.memory_estimate for _, layout in blocked)
        issues.append(
            warning(
                "planes_read_in_blocks",
                f"The planes of {plural(len(blocked), 'image')} are too large to read whole "
                f"within the memory limit of {format_bytes(memory_limit)} (they would need up "
                f"to {format_bytes(largest)}), so they will be read in blocks. The histograms "
                "are exactly the same; reading takes longer.",
                [rel for rel, _ in blocked],
                "Nothing needs to be done. To read the planes whole, which is faster, raise "
                f"the memory limit to {format_bytes(largest)} or more if this computer has "
                "enough memory.",
            )
        )
    if unsupported:
        issues.append(
            error(
                "unsupported_layout",
                f"{plural(len(unsupported), 'image')} cannot be read one plane at a time: "
                "the way their pixel data is stored does not match the axis mapping.",
                unsupported,
                "Check the axis mapping, or export the images again as standard "
                "multi-page TIFF files or OME-Zarr.",
            )
        )
    return issues


def format_bytes(n: int) -> str:
    """Return a size such as ``"1.5 GiB"`` for messages."""
    if n < 1024:
        return f"{n:,} bytes"
    size = n / 1024
    for unit in ("KiB", "MiB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _open(path: str | os.PathLike) -> tifffile.TiffFile:
    try:
        tif = tifffile.TiffFile(path)
    except Exception as exc:  # any failure to parse means the file cannot be used
        raise PlaneReadError(f"{path} cannot be opened as a TIFF file: {exc}") from exc
    if not tif.series:
        tif.close()
        raise PlaneReadError(f"{path} contains no image data.")
    return tif


def _check_fit(shape: tuple[int, ...], axes: str, mapping: AxisMapping, path) -> tuple[int, int]:
    """Check that ``mapping`` fits an image; return the Y and X dimensions."""
    if axes != mapping.axes:
        raise PlaneReadError(
            f"The axis mapping is for axes {mapping.axes!r}, but {path} has axes {axes!r}."
        )
    duplicates = {r for r in mapping.roles if r is not None and mapping.roles.count(r) > 1}
    if duplicates:
        raise PlaneReadError(f"The axis mapping gives roles {sorted(duplicates)} to several axes.")
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    if x is None or y is None:
        raise PlaneReadError("The axis mapping needs one X and one Y axis.")
    for d, role in enumerate(mapping.roles):
        if role is None and shape[d] > 1:
            raise PlaneReadError(
                f"Axis {mapping.axes[d]} of {path} has {shape[d]} planes but no role."
            )
    return y, x


def _layout(
    series: tifffile.TiffPageSeries,
    mapping: AxisMapping,
    path,
    binning: _Binning,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> PlaneLayout:
    shape = tuple(int(n) for n in series.shape)
    y, x = _check_fit(shape, str(series.axes), mapping, path)
    keyframe = series.keyframe
    page_count = len(series.pages)
    page_size = int(keyframe.size)
    # The series is its pages stacked along the leading axes, so the shape splits
    # into page-selecting axes and the axes within a page. Length-1 axes could fall on
    # either side; any split that keeps X and Y within the page works.
    split = next(
        (
            k
            for k in range(min(x, y), -1, -1)
            if math.prod(shape[:k]) == page_count and math.prod(shape[k:]) == page_size
        ),
        None,
    )
    if split is None:
        raise PlaneReadError(
            f"The planes of {path} do not lie within single TIFF pages "
            f"(series shape {shape}, {page_count} pages of shape {tuple(keyframe.shape)})."
        )

    dtype = np.dtype(series.dtype).newbyteorder("=")
    plane_pixels = shape[y] * shape[x]
    page_nbytes = page_size * dtype.itemsize
    encoded_nbytes = int(sum(keyframe.databytecounts))
    channels = _channel_count(shape, mapping)
    memory = (
        page_nbytes
        + encoded_nbytes
        + plane_pixels * dtype.itemsize
        + binning.memory(plane_pixels, dtype)
        + binning.finished(dtype, channels)
    )
    layout = PlaneLayout(
        shape=shape,
        dtype=dtype,
        plane_shape=(shape[y], shape[x]),
        plane_count=math.prod(n for d, n in enumerate(shape) if d not in (x, y)),
        page_shape=shape[split:],
        page_nbytes=page_nbytes,
        encoded_nbytes=encoded_nbytes,
        memory_estimate=memory,
        memory_limit=memory_limit,
        channel_count=channels,
    )
    if memory <= memory_limit:
        return layout
    return _with_blocks(layout, _tiff_blocking(keyframe, shape, split, y, x), binning)


def _channel_count(shape: tuple[int, ...], mapping: AxisMapping) -> int:
    c = mapping.index_of("C")
    return 1 if c is None else shape[c]


@dataclass(frozen=True)
class _Binning:
    """What a plane's histogram costs in memory: its bin count and histogram range."""

    bin_count: int | None = None
    settings: HistogramSettings | None = None

    def memory(self, pixels: int, dtype: np.dtype) -> int:
        """Temporary memory of binning ``pixels`` pixels, with the range check every run does."""
        return estimate_histogram_memory(
            pixels, dtype, self.bin_count, settings=self.settings, range_check=True
        )

    def finished(self, dtype: np.dtype, channels: int) -> int:
        """Memory of the finished histograms of an XYC plane's channels."""
        return _finished_histograms_nbytes(dtype, self.bin_count, channels)


def _finished_histograms_nbytes(dtype: np.dtype, bin_count: int | None, channels: int) -> int:
    """Memory of the bin counts of every channel of an XYC plane, kept until its row is made."""
    return channels * 8 * (default_bin_count(dtype) if bin_count is None else bin_count)


def _check_limit(layout: PlaneLayout, path) -> None:
    if not layout.fits:
        raise PlaneMemoryError(
            f"Reading one plane of {Path(path).name} needs about "
            f"{format_bytes(layout.min_memory_estimate)} of working memory, more than the "
            f"limit of {format_bytes(layout.memory_limit)}, even in blocks. Raise the memory "
            "limit if this computer has enough memory, or save the image again as a tiled "
            "or uncompressed TIFF file or as OME-Zarr with smaller chunks."
        )


def _positions(
    layout: PlaneLayout, mapping: AxisMapping, roles: str = "XY"
) -> Iterator[dict[int, int]]:
    """Index per dimension other than those of ``roles``, for every plane in storage order."""
    skipped = {mapping.index_of(role) for role in roles}
    others = [d for d in range(len(layout.shape)) if d not in skipped]
    for index in np.ndindex(*(layout.shape[d] for d in others)):
        yield dict(zip(others, index, strict=True))


def _xyc_planes(
    layout: PlaneLayout, mapping: AxisMapping, read: Callable[[dict[int, int]], Plane]
) -> Iterator[XycPlane]:
    """Yield the XYC planes of a file, each reading its channels with ``read(position)``."""
    c = mapping.index_of("C")

    def at(position: dict[int, int], channel: int) -> dict[int, int]:
        return position if c is None else {**position, c: channel}

    for position in _positions(layout, mapping, "XYC"):
        coordinates = _coordinates(mapping, at(position, 0))
        yield XycPlane(
            coordinates["z"],
            coordinates["t"],
            layout.channel_count,
            lambda channel, p=position: read(at(p, channel)),
        )


def _tiff_reader(
    series: tifffile.TiffPageSeries, layout: PlaneLayout, mapping: AxisMapping, path
) -> Callable[[dict[int, int]], Plane]:
    """Return a function reading the plane at a position, keeping the last page decoded.

    Consecutive planes of one page decode it once; the previous page is let go
    before the next is decoded, so at most one page is in memory.
    """
    if layout.in_blocks:
        return lambda position: _tiff_block_plane(series, layout, mapping, position, path)
    current: dict[str, object] = {"index": -1, "page": None}

    def read(position: dict[int, int]) -> Plane:
        index = _page_index(layout, position)
        if index != current["index"]:
            current["page"] = None  # let the previous page go before decoding the next
            current["page"] = _read_page(series, index, layout, path)
            current["index"] = index
        return _plane(current["page"], layout, mapping, position)

    return read


def _read_one(
    series: tifffile.TiffPageSeries,
    layout: PlaneLayout,
    mapping: AxisMapping,
    path,
    wanted: Mapping[str, int | None],
) -> Plane:
    position = _position(layout, mapping, wanted)
    if layout.in_blocks:
        return _tiff_block_plane(series, layout, mapping, position, path)
    page = _read_page(series, _page_index(layout, position), layout, path)
    return _plane(page, layout, mapping, position)


def _position(
    layout: PlaneLayout, mapping: AxisMapping, wanted: Mapping[str, int | None]
) -> dict[int, int]:
    """Turn wanted z, c, t coordinates into an index per non-XY dimension."""
    position: dict[int, int] = {}
    for role, value in wanted.items():
        dim = mapping.index_of(role)
        if dim is None:
            allowed = (None, 0) if role in mapping.singleton_roles else (None,)
            if value not in allowed:
                raise IndexError(f"The image has no {role} axis; got {role.lower()}={value}.")
            continue
        value = 0 if value is None else value
        if not 0 <= value < layout.shape[dim]:
            raise IndexError(
                f"{role.lower()}={value} is outside the image, which has "
                f"{layout.shape[dim]} planes along {role}."
            )
        position[dim] = value
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    for d in range(len(layout.shape)):
        if d not in (y, x):
            position.setdefault(d, 0)
    return position


def _page_index(layout: PlaneLayout, position: Mapping[int, int]) -> int:
    k = layout.page_ndim
    if k == 0:
        return 0
    return int(np.ravel_multi_index([position[d] for d in range(k)], layout.shape[:k]))


def _read_page(series: tifffile.TiffPageSeries, index: int, layout: PlaneLayout, path):
    page = series.pages[index]
    if page is None:
        raise PlaneReadError(f"Page {index} of {path} is missing from the file.")
    try:
        data = page.asarray()
    except Exception as exc:  # decoding errors come from many codecs
        raise PlaneReadError(f"Page {index} of {path} cannot be decoded: {exc}") from exc
    return data.reshape(layout.page_shape)


def _plane(
    page: np.ndarray, layout: PlaneLayout, mapping: AxisMapping, position: Mapping[int, int]
) -> Plane:
    k = layout.page_ndim
    selection = tuple(position.get(d, slice(None)) for d in range(k, len(layout.shape)))
    return _finish(page[selection], layout, mapping, position)


def _finish(
    pixels: np.ndarray, layout: PlaneLayout, mapping: AxisMapping, position: Mapping[int, int]
) -> Plane:
    """Orient a selected (Y, X) or (X, Y) array and attach its coordinates."""
    if mapping.index_of("X") < mapping.index_of("Y"):
        pixels = pixels.T
    pixels = np.ascontiguousarray(pixels, dtype=layout.dtype)
    return Plane(pixels=pixels, **_coordinates(mapping, position))


def _coordinates(mapping: AxisMapping, position: Mapping[int, int]) -> dict[str, int | None]:
    coords = {}
    for role in PLANE_ROLES:
        dim = mapping.index_of(role)
        if dim is not None:
            coords[role.lower()] = position[dim]
        elif role in mapping.singleton_roles:
            coords[role.lower()] = 0
        else:
            coords[role.lower()] = None
    return coords


# Blocks --------------------------------------------------------------------------------


def _with_blocks(layout: PlaneLayout, blocking: _Blocking | None, binning: _Binning) -> PlaneLayout:
    """Add the block size that fits the memory limit to a layout over the limit."""
    if blocking is None:
        return layout
    rows, cols = layout.shape[blocking.rows], layout.shape[blocking.cols]
    unit_rows, unit_cols = min(blocking.unit[0], rows), min(blocking.unit[1], cols)
    if unit_rows >= rows and unit_cols >= cols and not blocking.partial_rows:
        return layout  # a single piece holds the whole plane: it cannot be split

    def cost(block_rows: int, block_cols: int) -> int:
        return _block_memory(layout, blocking, block_rows, block_cols, binning)

    size = _choose_block(
        rows, cols, unit_rows, unit_cols, blocking.partial_rows, cost, layout.memory_limit
    )
    if size is None:
        smallest = (1 if blocking.partial_rows else unit_rows, unit_cols)
        return replace(layout, block_memory_estimate=cost(*smallest), blocking=blocking)
    block_shape = size if blocking.rows_are_y else size[::-1]
    return replace(
        layout, block_shape=block_shape, block_memory_estimate=cost(*size), blocking=blocking
    )


def _choose_block(
    rows: int,
    cols: int,
    unit_rows: int,
    unit_cols: int,
    partial_rows: bool,
    cost: Callable[[int, int], int],
    limit: int,
) -> tuple[int, int] | None:
    """Return the largest block (stored rows, columns) whose estimate fits the limit.

    Full-width bands of whole pieces come first, then single bands of fewer
    pieces, then (when pieces can be read in parts) bands of fewer rows.
    """

    def largest(count: int, size: Callable[[int], tuple[int, int]]) -> int:
        low, high = 0, count  # largest k in 1..count with cost(size(k)) <= limit, else 0
        while low < high:
            middle = (low + high + 1) // 2
            if cost(*size(middle)) <= limit:
                low = middle
            else:
                high = middle - 1
        return low

    k = largest(math.ceil(rows / unit_rows), lambda k: (min(k * unit_rows, rows), cols))
    if k:
        return min(k * unit_rows, rows), cols
    if unit_cols < cols:
        j = largest(math.ceil(cols / unit_cols), lambda j: (unit_rows, min(j * unit_cols, cols)))
        if j:
            return unit_rows, min(j * unit_cols, cols)
    if partial_rows:
        for width in dict.fromkeys((cols, unit_cols)):
            r = largest(unit_rows - 1, lambda r, width=width: (r, width))
            if r:
                return r, width
    return None


def _block_memory(
    layout: PlaneLayout, blocking: _Blocking, rows: int, cols: int, binning: _Binning
) -> int:
    """Estimated working memory to read one block and add it to the histogram."""
    pixels = rows * cols
    memory = (
        pixels * layout.dtype.itemsize
        + binning.memory(pixels, layout.dtype)
        + binning.finished(layout.dtype, layout.channel_count)
    )
    unit_rows, unit_cols = blocking.unit
    if blocking.kind == "zarr":
        # zarr decodes every chunk the block touches and returns a copy of the block.
        bands = math.ceil(rows / unit_rows) if rows >= unit_rows else 1
        chunks = bands * math.ceil(cols / unit_cols)
        return memory + chunks * blocking.piece_nbytes + pixels * layout.dtype.itemsize
    if blocking.partial_rows:
        # Uncompressed rows are read straight from the file, then copied into the block.
        return memory + 2 * min(rows, unit_rows) * blocking.row_nbytes
    # One compressed piece at a time is read and decoded, then copied into the block.
    return memory + blocking.piece_nbytes + blocking.encoded_nbytes


def _ranges(length: int, unit: int, size: int) -> list[tuple[int, int]]:
    """Split ``0 .. length`` into blocks of ``size``, never across a piece when smaller."""
    if size >= unit:
        return [(a, min(a + size, length)) for a in range(0, length, size)]
    return [
        (a, min(a + size, band + unit, length))
        for band in range(0, length, unit)
        for a in range(band, min(band + unit, length), size)
    ]


def _iter_blocks(
    layout: PlaneLayout, read: Callable[[int, int, int, int], np.ndarray]
) -> Iterator[Block]:
    """Yield the blocks of a plane in plane orientation, reading stored rectangles."""
    blocking = layout.blocking
    rows, cols = layout.shape[blocking.rows], layout.shape[blocking.cols]
    block_rows, block_cols = layout.block_shape[:: 1 if blocking.rows_are_y else -1]
    unit_rows, unit_cols = blocking.unit
    for r0, r1 in _ranges(rows, unit_rows, block_rows):
        for c0, c1 in _ranges(cols, unit_cols, block_cols):
            pixels = read(r0, r1, c0, c1)
            yield Block(r0, c0, pixels) if blocking.rows_are_y else Block(c0, r0, pixels.T)


def _block_plane(
    layout: PlaneLayout,
    mapping: AxisMapping,
    position: Mapping[int, int],
    read: Callable[[int, int, int, int], np.ndarray],
) -> Plane:
    reader = BlockReader(layout.plane_shape, layout.dtype, lambda: _iter_blocks(layout, read))
    return Plane(pixels=None, reader=reader, **_coordinates(mapping, position))


def _whole_or_preview(
    layout: PlaneLayout,
    path,
    preview: bool,
    reblock: Callable[[int], PlaneLayout],
) -> PlaneLayout:
    """Check that a plane can be read whole, or (for a preview) reduced in blocks.

    A reduced preview is held in memory while the blocks are read, so the blocks
    get the memory limit minus the preview's size.
    """
    if not layout.in_blocks and layout.fits:
        return layout
    if not preview:
        if layout.fits:
            raise PlaneMemoryError(
                f"Reading one plane of {Path(path).name} whole needs about "
                f"{format_bytes(layout.memory_estimate)} of working memory, more than the "
                f"limit of {format_bytes(layout.memory_limit)}. It can only be read in blocks."
            )
        _check_limit(layout, path)
    step = _preview_step(layout)
    reduced = layout.dtype.itemsize * math.prod(math.ceil(n / step) for n in layout.plane_shape)
    smaller = reblock(layout.memory_limit - reduced)
    if not smaller.in_blocks:
        raise PlaneMemoryError(
            f"Not even a reduced preview of a plane of {Path(path).name} can be read within "
            f"the memory limit of {format_bytes(layout.memory_limit)}. Raise the memory limit "
            "if this computer has enough memory."
        )
    return replace(smaller, memory_limit=layout.memory_limit)


def _preview_step(layout: PlaneLayout) -> int:
    """Return the step of a reduced preview: every ``step``-th pixel is kept.

    The preview has at most ``PREVIEW_SIDE`` pixels per side and, with the float
    copies made to display it, fits in a quarter of the memory limit.
    """
    rows, cols = layout.plane_shape
    per_pixel = layout.dtype.itemsize + 16
    allowed = max(1, layout.memory_limit // 4 // per_pixel)
    step = max(2, math.ceil(max(rows, cols) / PREVIEW_SIDE), math.isqrt(rows * cols // allowed))
    while math.ceil(rows / step) * math.ceil(cols / step) > allowed:
        step += 1
    return step


def _whole_or_reduced(plane: Plane, layout: PlaneLayout) -> Plane:
    """Return a plane held whole as it is, or a reduced preview of a plane read in blocks."""
    if not plane.in_blocks:
        return plane
    step = _preview_step(layout)
    rows, cols = plane.shape
    reduced = np.empty((math.ceil(rows / step), math.ceil(cols / step)), dtype=plane.dtype)
    for block in plane.blocks():
        kept = block.pixels[(-block.y) % step :: step, (-block.x) % step :: step]
        y, x = math.ceil(block.y / step), math.ceil(block.x / step)
        reduced[y : y + kept.shape[0], x : x + kept.shape[1]] = kept
    return replace(plane, pixels=reduced, step=step)


# TIFF blocks ---------------------------------------------------------------------------


def _tiff_blocking(
    keyframe, shape: tuple[int, ...], split: int, y: int, x: int
) -> _Blocking | None:
    """Work out how the pages of a series are stored in strips or tiles, if they can be split.

    Each page is a normalised array (separate samples, depth, rows, columns,
    contiguous samples). The page's own axes of the series must line up with
    that array, with the plane's two axes on its rows and columns.
    """
    if keyframe.is_subsampled or keyframe.jpegheader is not None or keyframe.dtype is None:
        return None
    stored_shape = tuple(int(n) for n in keyframe.shaped)
    page_dims = [d for d in range(split, len(shape)) if shape[d] > 1]
    stored_dims = [i for i in range(5) if stored_shape[i] > 1]
    if [shape[d] for d in page_dims] != [stored_shape[i] for i in stored_dims]:
        return None
    stored = dict(zip(page_dims, stored_dims, strict=True))
    rows, cols = sorted((y, x))
    if stored.get(rows) != 2 or stored.get(cols) != 3:
        return None
    itemsize = np.dtype(keyframe.dtype).itemsize
    samples = stored_shape[4]
    if keyframe.is_tiled:
        depth, unit = keyframe.tiledepth, (keyframe.tilelength, keyframe.tilewidth)
    else:
        depth, unit = 1, (min(keyframe.rowsperstrip, stored_shape[2]), stored_shape[3])
    uncompressed = (
        keyframe.compression == 1
        and keyframe.predictor == 1
        and keyframe.fillorder == 1
        and keyframe.bitspersample == itemsize * 8
    )
    return _Blocking(
        kind="tiff",
        rows=rows,
        cols=cols,
        rows_are_y=rows == y,
        unit=unit,
        partial_rows=uncompressed,
        piece_nbytes=depth * unit[0] * unit[1] * samples * itemsize,
        encoded_nbytes=max((int(n) for n in keyframe.databytecounts), default=0),
        row_nbytes=unit[1] * samples * itemsize,
        stored=tuple(stored.items()),
    )


def _tiff_reblock(series, mapping: AxisMapping, path) -> Callable[[int], PlaneLayout]:
    return lambda limit: _layout(series, mapping, path, _Binning(), limit)


def _tiff_block_plane(
    series: tifffile.TiffPageSeries,
    layout: PlaneLayout,
    mapping: AxisMapping,
    position: Mapping[int, int],
    path,
) -> Plane:
    index = _page_index(layout, position)
    page = series.pages[index]
    if page is None:
        raise PlaneReadError(f"Page {index} of {path} is missing from the file.")
    # Index of the plane along the separate-sample, depth and contiguous-sample dimensions.
    where = [0] * 5
    for dim, stored in layout.blocking.stored:
        if stored not in (2, 3):
            where[stored] = position[dim]
    return _block_plane(
        layout,
        mapping,
        position,
        lambda r0, r1, c0, c1: _read_tiff_block(page, layout, where, (r0, r1, c0, c1), path),
    )


def _read_tiff_block(page, layout: PlaneLayout, where: list[int], box, path) -> np.ndarray:
    """Read rows ``r0 .. r1`` and columns ``c0 .. c1`` of one plane of a page, piece by piece."""
    r0, r1, c0, c1 = box
    keyframe = page.keyframe
    blocking = layout.blocking
    _, depth, length, width, samples = (int(n) for n in keyframe.shaped)
    unit_rows, unit_cols = blocking.unit
    sample, z, _, _, contiguous = where
    out = np.empty((r1 - r0, c1 - c0), dtype=layout.dtype)
    if keyframe.is_tiled:
        tile_depth = keyframe.tiledepth
        across, down = math.ceil(width / unit_cols), math.ceil(length / unit_rows)
        first = (sample * math.ceil(depth / tile_depth) + z // tile_depth) * down * across
        z_in_piece = z % tile_depth
    else:
        across, down = 1, math.ceil(length / unit_rows)
        first = (sample * depth + z) * down
        z_in_piece = 0
    for row in range(r0 // unit_rows, math.ceil(r1 / unit_rows)):
        top = row * unit_rows
        a, b = max(r0, top) - top, min(r1, top + unit_rows, length) - top
        for col in range(c0 // unit_cols, math.ceil(c1 / unit_cols)):
            left = col * unit_cols
            ca, cb = max(c0, left) - left, min(c1, left + unit_cols, width) - left
            index = first + row * across + col
            piece = _read_segment(page, index, z_in_piece, (a, b), blocking, path)
            target = out[top + a - r0 : top + b - r0, left + ca - c0 : left + cb - c0]
            if piece is None:
                target[...] = keyframe.nodata
            else:
                target[...] = piece[:, ca:cb, contiguous]
    return out


def _read_segment(page, index: int, z: int, rows: tuple[int, int], blocking: _Blocking, path):
    """Read rows of one strip or tile as (rows, columns, contiguous samples), or None if empty.

    Uncompressed pieces are read straight from the file, only the rows wanted;
    compressed pieces are decoded whole.
    """
    keyframe = page.keyframe
    offset, count = int(page.dataoffsets[index]), int(page.databytecounts[index])
    if offset == 0 or count == 0:
        return None
    fh = page.parent.filehandle
    a, b = rows
    try:
        if blocking.partial_rows:
            unit_rows, unit_cols = blocking.unit
            per_row = blocking.row_nbytes // np.dtype(keyframe.dtype).itemsize
            stored = np.dtype(page.parent.byteorder + np.dtype(keyframe.dtype).char)
            with fh.lock:
                fh.seek(offset + (z * unit_rows + a) * blocking.row_nbytes)
                data = fh.read_array(stored, (b - a) * per_row)
            return data.reshape(b - a, unit_cols, per_row // unit_cols)
        with fh.lock:
            fh.seek(offset)
            data = fh.read(count)
            decode = keyframe.decode
        kwargs = {}
        if keyframe.compression in _JPEG_COMPRESSIONS:
            kwargs = {"jpegtables": page.jpegtables, "jpegheader": keyframe.jpegheader}
        segment, _, _ = decode(data, index, **kwargs)
    except Exception as exc:  # decoding errors come from many codecs
        raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}") from exc
    if segment is None:
        return None
    return segment[z, a:b]


# OME-Zarr ------------------------------------------------------------------------------


def _open_zarr(path) -> NgffImage:
    try:
        return open_ngff(path)
    except (NgffError, ZarrUnavailableError) as exc:
        raise PlaneReadError(f"{path} cannot be opened as an OME-Zarr image: {exc}") from exc


def _zarr_layout(
    image: NgffImage,
    mapping: AxisMapping,
    path,
    binning: _Binning,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> PlaneLayout:
    shape = image.shape
    y, x = _check_fit(shape, image.axes, mapping, path)
    dtype = image.dtype
    chunks = image.chunks
    # zarr decodes every chunk a plane touches; each chunk may also hold other planes.
    chunks_per_plane = math.ceil(shape[y] / chunks[y]) * math.ceil(shape[x] / chunks[x])
    chunk_nbytes = math.prod(chunks) * dtype.itemsize
    page_nbytes = chunk_nbytes * chunks_per_plane
    plane_pixels = shape[y] * shape[x]
    channels = _channel_count(shape, mapping)
    memory = (
        page_nbytes
        + plane_pixels * dtype.itemsize
        + binning.memory(plane_pixels, dtype)
        + binning.finished(dtype, channels)
    )
    layout = PlaneLayout(
        shape=shape,
        dtype=dtype,
        plane_shape=(shape[y], shape[x]),
        plane_count=math.prod(n for d, n in enumerate(shape) if d not in (x, y)),
        page_shape=chunks,
        page_nbytes=page_nbytes,
        encoded_nbytes=0,
        memory_estimate=memory,
        memory_limit=memory_limit,
        channel_count=channels,
    )
    if memory <= memory_limit:
        return layout
    rows, cols = sorted((y, x))
    blocking = _Blocking(
        kind="zarr",
        rows=rows,
        cols=cols,
        rows_are_y=rows == y,
        unit=(chunks[rows], chunks[cols]),
        partial_rows=True,
        piece_nbytes=chunk_nbytes,
    )
    return _with_blocks(layout, blocking, binning)


def _zarr_reblock(image: NgffImage, mapping: AxisMapping, path) -> Callable[[int], PlaneLayout]:
    return lambda limit: _zarr_layout(image, mapping, path, _Binning(), limit)


def _zarr_plane(
    image: NgffImage, layout: PlaneLayout, mapping: AxisMapping, position: Mapping[int, int], path
) -> Plane:
    if layout.in_blocks:
        rows, cols = layout.blocking.rows, layout.blocking.cols

        def read(r0: int, r1: int, c0: int, c1: int) -> np.ndarray:
            box = {**position, rows: slice(r0, r1), cols: slice(c0, c1)}
            return _zarr_read(image, tuple(box[d] for d in range(len(layout.shape))), path)

        return _block_plane(layout, mapping, position, read)
    selection = tuple(position.get(d, slice(None)) for d in range(len(layout.shape)))
    return _finish(_zarr_read(image, selection, path), layout, mapping, position)


def _zarr_read(image: NgffImage, selection: tuple, path) -> np.ndarray:
    try:
        return read_selection(image.array, selection)
    except Exception as exc:  # decoding errors come from many codecs and stores
        raise PlaneReadError(f"A plane of {path} cannot be read: {exc}") from exc
