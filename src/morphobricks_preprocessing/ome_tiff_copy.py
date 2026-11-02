"""Write one image as a named-axis OME-TIFF copy, plane by plane (design v0.4 §5).

The planes are read with the run's reader under the confirmed roles
(:func:`.planes.iter_planes`), so each copy plane is the plane the run would read, value for
value: same pixel type, no rescaling, no rounding. Planes over the memory limit are read in
blocks, as the run reads them, and each block is written to its place in the file: no plane
is ever held whole when the run would not hold it. The copy is uncompressed, so every plane
has a fixed place in the file, and BigTIFF when it would not fit a classic TIFF.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

import numpy as np
import tifffile

from .axes import AxisMapping
from .copy_axes import CopyLayout
from .planes import DEFAULT_MEMORY_LIMIT, Block, iter_planes, plane_layout

BIGTIFF_FROM = 2**32 - 2**25
"""Copies whose file would take more bytes than this are written as BigTIFF (a classic
TIFF addresses at most 4 GiB; the margin leaves room for the header and the OME-XML)."""

PAGE_OVERHEAD = 1024
"""Bytes allowed per page for its directory and tags besides its pixels (``tifffile``
writes about 170 in an uncompressed OME-TIFF), so that many small pages cannot overflow a
classic TIFF whose pixels alone would fit."""


class CopyWriteError(OSError):
    """Raised when a copy cannot be written as planned."""


def copy_nbytes(layout: CopyLayout, dtype: np.dtype) -> int:
    """Return the bytes the pixels of a copy take.

    Parameters
    ----------
    layout
        The copy's layout.
    dtype
        Its pixel type.

    Returns
    -------
    int
    """
    count = 1
    for length in layout.shape:
        count *= length
    return count * np.dtype(dtype).itemsize


def needs_bigtiff(layout: CopyLayout, dtype: np.dtype) -> bool:
    """Return True when a copy would not fit a classic TIFF, its pages' directories counted.

    Parameters
    ----------
    layout
        The copy's layout.
    dtype
        Its pixel type.

    Returns
    -------
    bool
    """
    return copy_nbytes(layout, dtype) + layout.plane_count * PAGE_OVERHEAD > BIGTIFF_FROM


def write_named_copy(
    source: str | os.PathLike,
    target: str | os.PathLike,
    layout: CopyLayout,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> None:
    """Write ``source``, read under ``mapping``, to ``target`` as a named-axis OME-TIFF.

    Parameters
    ----------
    source
        The TIFF image.
    target
        The file to write (the caller's ``.partial`` file); replaced if it exists.
    layout
        The copy's axes, shape and metadata (:func:`.copy_axes.copy_layout`).
    mapping
        The image's own confirmed mapping.
    memory_limit
        Working-memory limit per plane, as the run's.

    Raises
    ------
    PlaneReadError
        If the source cannot be read under ``mapping`` within the memory limit.
    CopyWriteError
        If the planes do not lie where the file was laid out.
    OSError
        If the file cannot be written.
    """
    dtype = plane_layout(source, mapping, memory_limit=memory_limit).dtype
    offset, _ = tifffile.imwrite(
        target,
        shape=layout.shape,
        dtype=dtype,
        ome=True,
        photometric="minisblack",
        bigtiff=needs_bigtiff(layout, dtype),
        metadata=layout.ome_metadata(),
        returnoffset=True,
    )
    rows, cols = layout.plane_shape
    plane_nbytes = rows * cols * dtype.itemsize
    _check_contiguous(Path(target), offset, plane_nbytes, layout.plane_count)
    with open(target, "r+b") as stream:
        for plane in iter_planes(source, mapping, memory_limit=memory_limit):
            start = offset + layout.plane_index(plane.coordinates) * plane_nbytes
            for block in plane.blocks():
                _write_block(stream, start, block, cols, dtype)


def _check_contiguous(path: Path, offset: int, plane_nbytes: int, count: int) -> None:
    """Refuse a laid-out file whose pages do not follow each other from ``offset``."""
    with tifffile.TiffFile(path) as tif:
        pages = tif.pages
        if len(pages) != count:
            raise CopyWriteError(f"{path} was laid out with {len(pages)} pages, not {count}")
        for index in range(count):
            page = pages[index]
            start = offset + index * plane_nbytes
            if page.dataoffsets[0] != start or sum(page.databytecounts) != plane_nbytes:
                raise CopyWriteError(f"page {index} of {path} does not lie where it was planned")


def _write_block(stream: BinaryIO, start: int, block: Block, cols: int, dtype: np.dtype) -> None:
    """Write one block of a plane at its place; a block as wide as the plane in one write."""
    pixels = np.ascontiguousarray(block.pixels, dtype=dtype)
    row_nbytes = cols * dtype.itemsize
    first = start + block.y * row_nbytes + block.x * dtype.itemsize
    if pixels.shape[1] == cols:
        stream.seek(first)
        stream.write(memoryview(pixels).cast("B"))
        return
    for r in range(pixels.shape[0]):
        stream.seek(first + r * row_nbytes)
        stream.write(memoryview(pixels[r]).cast("B"))
