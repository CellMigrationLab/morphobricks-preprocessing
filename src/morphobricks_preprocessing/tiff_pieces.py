"""Read a rectangle of one TIFF plane from the strips or tiles it is stored in.

The block reader of :mod:`.planes` (planes over the memory limit) and the window reader of
:mod:`.windows` read through :func:`_read_tiff_box`: only the strips or tiles the rectangle
intersects are read, uncompressed ones only for the rows it needs, compressed ones decoded
whole. A page stores a normalised array (separate samples, depth, rows, columns, contiguous
samples); the rectangle is taken at one separate sample and one depth, for any number of
contiguous samples. Each intersecting piece is read once for all the contiguous samples asked
(an RGB tile stored interleaved is decoded once, not once per sample), and its values are
copied as stored into the caller's array.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from .plane_errors import PlaneReadError

if TYPE_CHECKING:
    import os

    import tifffile

    from .planes import _Blocking

# TIFF compression codes that need the JPEG tables of the file to decode a piece.
_JPEG_COMPRESSIONS = {6, 7, 33007, 34892}


def _read_tiff_box(
    page: tifffile.TiffPage | tifffile.TiffFrame,
    blocking: _Blocking,
    where: tuple[int, int],
    samples: Sequence[int],
    box: tuple[int, int, int, int],
    targets: Sequence[np.ndarray],
    path: str | os.PathLike,
) -> None:
    """Fill ``targets`` with rows ``r0 .. r1`` and columns ``c0 .. c1`` of one plane of a page.

    ``where`` is the plane's ``(separate sample, depth)`` in the page and ``samples`` the
    contiguous samples wanted; ``targets`` holds one ``(r1 - r0, c1 - c0)`` array per sample
    (any strides, so a transposed view works). Each strip or tile the box intersects is read
    once for all ``samples``; an empty one fills the targets with the page's ``nodata``.
    """
    r0, r1, c0, c1 = box
    keyframe = page.keyframe
    _, depth, length, width, _ = (int(n) for n in keyframe.shaped)
    unit_rows, unit_cols = blocking.unit
    sample, z = where
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
            for target, s in zip(targets, samples, strict=True):
                region = target[top + a - r0 : top + b - r0, left + ca - c0 : left + cb - c0]
                region[...] = keyframe.nodata if piece is None else piece[:, ca:cb, s]


def _read_segment(
    page: tifffile.TiffPage | tifffile.TiffFrame,
    index: int,
    z: int,
    rows: tuple[int, int],
    blocking: _Blocking,
    path: str | os.PathLike,
) -> np.ndarray | None:
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
