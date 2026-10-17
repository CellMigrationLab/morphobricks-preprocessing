"""Read the 2D planes of a TIFF one at a time under a confirmed axis mapping.

The unit ``tifffile`` decodes is a TIFF page. Most microscopy files store one
plane per page; RGB pages hold one plane per sample, and volumetric pages hold a
whole stack. Each page is decoded once and the planes in it are yielded before
the next page is read, so no more than one page is ever in memory, never a full
Z/C/T volume (unless the file stores the volume as a single page).

Before anything is decoded, each plane's working memory is estimated from the
header: the decoded page, its encoded bytes, a copy of the plane and the
temporary arrays of the histogram. Planes above the limit (default 1 GiB) are
reported by :func:`check_planes` and refused by the readers.

Only the first image series is read, at full-resolution level 0 for pyramidal
files (``docs/contract.md`` §2).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from .axes import PLANE_ROLES, AxisMapping, AxisReview
from .histogram import estimate_histogram_memory
from .validation import ValidationIssue, error, plural

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_LIMIT = 2**30
"""Default working-memory limit per plane: 1 GiB."""


class PlaneReadError(Exception):
    """Raised when a file's planes cannot be read under the given axis mapping."""


class PlaneMemoryError(PlaneReadError):
    """Raised when reading a plane would need more memory than the limit allows."""


@dataclass(frozen=True)
class Plane:
    """One 2D plane of an image.

    Attributes
    ----------
    z, c, t
        Zero-based coordinates; None when the experiment mapping has no such axis.
    pixels
        The plane as a (Y, X) array in native byte order, with the source values
        unchanged.
    """

    z: int | None
    c: int | None
    t: int | None
    pixels: np.ndarray

    @property
    def coordinates(self) -> dict[str, int | None]:
        """The coordinates as ``{"z": ..., "c": ..., "t": ...}``."""
        return {"z": self.z, "c": self.c, "t": self.t}


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
        Number of planes in the file.
    page_shape
        Shape of the unit decoded at once (one TIFF page), as a part of ``shape``.
    page_nbytes
        Size of a decoded page in bytes.
    encoded_nbytes
        Size of a page as stored in the file (compressed size, if compressed).
    memory_estimate
        Estimated working memory to read one plane and compute its histogram, in
        bytes.
    """

    shape: tuple[int, ...]
    dtype: np.dtype
    plane_shape: tuple[int, int]
    plane_count: int
    page_shape: tuple[int, ...]
    page_nbytes: int
    encoded_nbytes: int
    memory_estimate: int

    @property
    def page_ndim(self) -> int:
        """Number of leading axes that select a page; the others lie within a page."""
        return len(self.shape) - len(self.page_shape)


def plane_layout(
    path: str | os.PathLike, mapping: AxisMapping, *, bin_count: int | None = None
) -> PlaneLayout:
    """Work out from the header how the planes of a file are read.

    Parameters
    ----------
    path
        The TIFF file.
    mapping
        The axis mapping for this file (see :attr:`AxisReview.file_mappings`).
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.

    Raises
    ------
    PlaneReadError
        If the file does not open, the mapping does not fit it, or its planes
        cannot be read one at a time.
    """
    with _open(path) as tif:
        return _layout(tif.series[0], mapping, path, bin_count)


def iter_planes(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    bin_count: int | None = None,
) -> Iterator[Plane]:
    """Yield every 2D plane of a file, one page read at a time.

    Planes come in the file's storage order (the order of the axes). Each plane
    is checked against the memory limit before the file's pixel data is read.

    Parameters
    ----------
    path
        The TIFF file.
    mapping
        The axis mapping for this file (see :attr:`AxisReview.file_mappings`).
    memory_limit
        Largest working memory allowed for one plane, in bytes.
    bin_count
        Histogram bin count, for the memory estimate; the dtype's default if None.

    Yields
    ------
    Plane

    Raises
    ------
    PlaneMemoryError
        If a plane needs more memory than ``memory_limit``.
    PlaneReadError
        If the file cannot be read plane by plane under ``mapping``.
    """
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, bin_count)
        _check_limit(layout, path, memory_limit)
        y, x = mapping.index_of("Y"), mapping.index_of("X")
        others = [d for d in range(len(layout.shape)) if d not in (y, x)]
        current, page = -1, None
        for index in np.ndindex(*(layout.shape[d] for d in others)):
            position = dict(zip(others, index, strict=True))
            page_index = _page_index(layout, position)
            if page_index != current:
                page = None  # let the previous page go before decoding the next
                page = _read_page(series, page_index, layout, path)
                current = page_index
            yield _plane(page, layout, mapping, position)


def read_plane(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    z: int | None = None,
    c: int | None = None,
    t: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> Plane:
    """Read one plane, decoding only the page that holds it.

    Coordinates left as None default to 0 for the axes the mapping has.

    Raises
    ------
    PlaneMemoryError
        If the plane needs more memory than ``memory_limit``.
    PlaneReadError
        If the file cannot be read under ``mapping``.
    IndexError
        If a coordinate is outside the image, or given for an axis it lacks.
    """
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, None)
        _check_limit(layout, path, memory_limit)
        return _read_one(series, layout, mapping, path, {"Z": z, "C": c, "T": t})


def read_sample_plane(
    path: str | os.PathLike, mapping: AxisMapping, *, memory_limit: int = DEFAULT_MEMORY_LIMIT
) -> Plane:
    """Read one plane for the axis preview: the middle Z slice, first C and T.

    The middle slice is chosen because the first slice of a stack is often out
    of focus. Only the page holding that plane is decoded.
    """
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, None)
        _check_limit(layout, path, memory_limit)
        dim = mapping.index_of("Z")
        z = None if dim is None else layout.shape[dim] // 2
        return _read_one(series, layout, mapping, path, {"Z": z, "C": None, "T": None})


def check_planes(
    experiment_root: str | os.PathLike,
    review: AxisReview | Mapping[str, AxisMapping],
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    bin_count: int | None = None,
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

    Returns
    -------
    list of ValidationIssue
        Errors for planes over the memory limit (``plane_over_memory_limit``) and
        for files whose planes cannot be read one at a time
        (``unsupported_layout``).
    """
    mappings = review.file_mappings if isinstance(review, AxisReview) else review
    root = Path(experiment_root)
    over: list[tuple[str, PlaneLayout]] = []
    unsupported: list[str] = []
    for rel in sorted(mappings):
        try:
            layout = plane_layout(root / rel, mappings[rel], bin_count=bin_count)
        except PlaneReadError:
            logger.debug("Cannot read %s plane by plane", rel, exc_info=True)
            unsupported.append(rel)
            continue
        if layout.memory_estimate > memory_limit:
            over.append((rel, layout))

    issues = []
    if over:
        largest = max(layout.memory_estimate for _, layout in over)
        issues.append(
            error(
                "plane_over_memory_limit",
                f"Reading one plane of {plural(len(over), 'image')} needs more working memory "
                f"than the limit of {format_bytes(memory_limit)} (up to "
                f"{format_bytes(largest)}). Planes are never split or read with another "
                "method, so these images cannot be processed with this limit.",
                [rel for rel, _ in over],
                "Raise the memory limit if this computer has enough memory, or crop or "
                "split the images before processing.",
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
                "multi-page TIFF files.",
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


def _layout(
    series: tifffile.TiffPageSeries, mapping: AxisMapping, path, bin_count: int | None
) -> PlaneLayout:
    shape = tuple(int(n) for n in series.shape)
    if str(series.axes) != mapping.axes:
        raise PlaneReadError(
            f"The axis mapping is for axes {mapping.axes!r}, but {path} has axes {series.axes!r}."
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
    memory = (
        page_nbytes
        + encoded_nbytes
        + plane_pixels * dtype.itemsize
        + estimate_histogram_memory(plane_pixels, dtype, bin_count)
    )
    return PlaneLayout(
        shape=shape,
        dtype=dtype,
        plane_shape=(shape[y], shape[x]),
        plane_count=math.prod(n for d, n in enumerate(shape) if d not in (x, y)),
        page_shape=shape[split:],
        page_nbytes=page_nbytes,
        encoded_nbytes=encoded_nbytes,
        memory_estimate=memory,
    )


def _check_limit(layout: PlaneLayout, path, memory_limit: int) -> None:
    if layout.memory_estimate > memory_limit:
        raise PlaneMemoryError(
            f"Reading one plane of {Path(path).name} needs about "
            f"{format_bytes(layout.memory_estimate)} of working memory, more than the "
            f"limit of {format_bytes(memory_limit)}. Raise the memory limit if this "
            "computer has enough memory, or crop or split the image."
        )


def _read_one(
    series: tifffile.TiffPageSeries,
    layout: PlaneLayout,
    mapping: AxisMapping,
    path,
    wanted: Mapping[str, int | None],
) -> Plane:
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
    page = _read_page(series, _page_index(layout, position), layout, path)
    return _plane(page, layout, mapping, position)


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
    pixels = page[selection]
    if mapping.index_of("X") < mapping.index_of("Y"):
        pixels = pixels.T
    pixels = np.ascontiguousarray(pixels, dtype=layout.dtype)
    coords = {}
    for role in PLANE_ROLES:
        dim = mapping.index_of(role)
        if dim is not None:
            coords[role.lower()] = position[dim]
        elif role in mapping.singleton_roles:
            coords[role.lower()] = 0
        else:
            coords[role.lower()] = None
    return Plane(pixels=pixels, **coords)
