"""Read a rectangle of one TIFF plane from the strips or tiles it is stored in.

The block reader of :mod:`.planes` (planes over the memory limit) and the window reader of
:mod:`.windows` read through :func:`_read_tiff_box`: only the strips or tiles the rectangle
intersects are read, uncompressed ones only for the rows it needs, compressed ones decoded
whole. A page stores a normalised array (separate samples, depth, rows, columns, contiguous
samples); the rectangle is taken at one separate sample and one depth, for any number of
contiguous samples. Each intersecting piece is read once for all the contiguous samples asked
(an RGB tile stored interleaved is decoded once, not once per sample), and its values are
copied as stored into the caller's array.

A caller may pass ``fetch``, which gives a compressed piece from a cache of decoded pieces
(:class:`.block_cache.BlockCache`, through :class:`.window_reader.WindowReader`) and decodes it
only when the cache does not hold it. Uncompressed pieces are read straight from the file, only
the rows needed, and are never cached.

How a series' pages are stored (:class:`_Blocking`, :func:`_tiff_blocking`) is worked out here
too, and :func:`_box_pieces` names the pieces a rectangle intersects, for the read and for a
window's plan alike (issue #356).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from . import jpeg_piece, jpeg_stream
from .plane_errors import PlaneReadError
from .window_memory import JPEG, PieceDecode

if TYPE_CHECKING:
    import os

    import tifffile

# TIFF compression codes that need the JPEG tables of the file to decode a piece.
_JPEG_COMPRESSIONS = {6, 7, 33007, 34892}


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
    decode: PieceDecode = PieceDecode(1)  # TIFF: how a piece is decoded (window_memory)


def _tiff_blocking(
    keyframe: tifffile.TiffPage,
    shape: tuple[int, ...],
    split: int,
    y: int,
    x: int,
    largest: int | None = None,
) -> _Blocking | None:
    """Work out how the pages of a series are stored in strips or tiles, if they can be split.

    Each page is a normalised array (separate samples, depth, rows, columns,
    contiguous samples). The page's own axes of the series must line up with
    that array, with the plane's two axes on its rows and columns. ``encoded_nbytes`` is
    ``largest`` (the largest stored piece of the pages read), or the keyframe's largest piece
    when it is None: a window's plan sizes the pieces of the pages it reads itself
    (``windows._plan``).
    """
    # Subsampled colour is upsampled by the JPEG decoder itself: a JPEG piece decodes whole.
    subsampled = keyframe.is_subsampled and keyframe.compression != JPEG
    if subsampled or keyframe.jpegheader is not None or keyframe.dtype is None:
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
        encoded_nbytes=_largest(keyframe) if largest is None else largest,
        row_nbytes=unit[1] * samples * itemsize,
        stored=tuple(stored.items()),
        decode=piece_decode(keyframe),
    )


def piece_decode(keyframe: tifffile.TiffPage) -> PieceDecode:
    """Return how one strip or tile of a page is decoded, from its header (window_memory)."""
    _, depth, length, width, samples = (int(n) for n in keyframe.shaped)
    if keyframe.is_tiled:
        rows, columns = depth * keyframe.tilelength, keyframe.tilewidth
    else:
        rows, columns = min(keyframe.rowsperstrip, length), width
    return PieceDecode(int(keyframe.compression), int(rows), int(columns), samples)


def _largest_piece(series: tifffile.TiffPageSeries) -> int:
    """Return the largest stored strip or tile of every page of a series, from the headers.

    Pages can compress very differently (a blank plane next to a noisy one, issue #356), so a
    plan that holds for any plane is sized from the largest piece of all of them. A page
    missing from the file counts as its keyframe (reading it is refused).
    """
    return max(_largest(series.keyframe if page is None else page) for page in series.pages)


def _largest(page: tifffile.TiffPage | tifffile.TiffFrame) -> int:
    """Return a page's largest stored strip or tile, in bytes (0 without any)."""
    return max((int(n) for n in page.databytecounts), default=0)


Fetch = Callable[[int, Callable[[], "np.ndarray | None"]], "np.ndarray | None"]
"""``fetch(piece_index, decode)``: a decoded piece of the page, from a cache or ``decode()``."""


def _read_tiff_box(
    page: tifffile.TiffPage | tifffile.TiffFrame,
    blocking: _Blocking,
    where: tuple[int, int],
    samples: Sequence[int],
    box: tuple[int, int, int, int],
    targets: Sequence[np.ndarray],
    path: str | os.PathLike,
    fetch: Fetch | None = None,
) -> None:
    """Fill ``targets`` with rows ``r0 .. r1`` and columns ``c0 .. c1`` of one plane of a page.

    ``where`` is the plane's ``(separate sample, depth)`` in the page and ``samples`` the
    contiguous samples wanted; ``targets`` holds one ``(r1 - r0, c1 - c0)`` array per sample
    (any strides, so a transposed view works). Each strip or tile the box intersects is read
    once for all ``samples``; an empty one fills the targets with the page's ``nodata``.
    ``fetch`` gives the compressed pieces from a cache (module docstring).
    """
    r0, r1, c0, c1 = box
    keyframe = page.keyframe
    _, _, length, width, _ = (int(n) for n in keyframe.shaped)
    unit_rows, unit_cols = blocking.unit
    for index, top, left, z_in_piece in _box_pieces(keyframe, blocking, where, box):
        a, b = max(r0, top) - top, min(r1, top + unit_rows, length) - top
        ca, cb = max(c0, left) - left, min(c1, left + unit_cols, width) - left
        piece = _read_segment(page, index, z_in_piece, (a, b), blocking, path, fetch)
        for target, s in zip(targets, samples, strict=True):
            region = target[top + a - r0 : top + b - r0, left + ca - c0 : left + cb - c0]
            region[...] = keyframe.nodata if piece is None else piece[:, ca:cb, s]


def _box_pieces(
    keyframe: tifffile.TiffPage,
    blocking: _Blocking,
    where: tuple[int, int],
    box: tuple[int, int, int, int],
) -> Iterator[tuple[int, int, int, int]]:
    """Yield each strip or tile a box of one plane intersects: ``(index, top, left, z)``.

    ``index`` is the piece's index in the page's offsets and byte counts, ``top`` and
    ``left`` its first row and column in the plane, ``z`` the plane's depth within the piece.
    The same pieces are read (:func:`_read_tiff_box`) and sized for the window's plan
    (``windows``, issue #356), so a plan and its read always name the same pieces.
    """
    r0, r1, c0, c1 = box
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
        for col in range(c0 // unit_cols, math.ceil(c1 / unit_cols)):
            yield first + row * across + col, row * unit_rows, col * unit_cols, z_in_piece


def _read_segment(
    page: tifffile.TiffPage | tifffile.TiffFrame,
    index: int,
    z: int,
    rows: tuple[int, int],
    blocking: _Blocking,
    path: str | os.PathLike,
    fetch: Fetch | None = None,
) -> np.ndarray | None:
    """Read rows of one strip or tile as (rows, columns, contiguous samples), or None if empty.

    Uncompressed pieces are read straight from the file, only the rows wanted;
    compressed pieces are decoded whole (or taken from ``fetch``'s cache).
    """
    keyframe = page.keyframe
    offset, count = int(page.dataoffsets[index]), int(page.databytecounts[index])
    if offset == 0 or count == 0:
        return None
    a, b = rows
    if blocking.partial_rows:
        fh = page.parent.filehandle
        unit_rows, unit_cols = blocking.unit
        per_row = blocking.row_nbytes // np.dtype(keyframe.dtype).itemsize
        stored = np.dtype(page.parent.byteorder + np.dtype(keyframe.dtype).char)
        try:
            with fh.lock:
                fh.seek(offset + (z * unit_rows + a) * blocking.row_nbytes)
                data = fh.read_array(stored, (b - a) * per_row)
        except Exception as exc:  # a short or unreadable file
            raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}") from exc
        return data.reshape(b - a, unit_cols, per_row // unit_cols)
    if fetch is None:
        segment = _decode_segment(page, index, path)
    else:
        segment = fetch(index, lambda: _decode_segment(page, index, path))
    return None if segment is None else segment[z, a:b]


def _decode_segment(
    page: tifffile.TiffPage | tifffile.TiffFrame,
    index: int,
    path: str | os.PathLike,
    lock: AbstractContextManager | None = None,
) -> np.ndarray | None:
    """Decode one compressed strip or tile whole, as (depth, rows, columns, samples).

    The piece is read under ``lock`` (default: the file handle's own). A JPEG piece is checked
    (:func:`check_jpeg_piece`), then decoded by libjpeg alone (:mod:`.jpeg_piece`): never by
    tifffile's decoder, which retries a stream libjpeg refuses with another decoder (#356, F1).
    """
    keyframe = page.keyframe
    fh = page.parent.filehandle
    jpeg = keyframe.compression == JPEG
    try:
        with fh.lock if lock is None else lock:
            fh.seek(int(page.dataoffsets[index]))
            data = fh.read(int(page.databytecounts[index]))
            # tifffile reads the JFIF marker from the file: under the same lock, as is the
            # decoder made (for a JPEG piece, only where it goes is asked of it, never pixels)
            jfif = jpeg and int(keyframe.photometric) == jpeg_piece.RGB and keyframe.is_jfif
            decode = keyframe.decode
        kwargs = {}
        if keyframe.compression in _JPEG_COMPRESSIONS:
            kwargs = {"jpegtables": page.jpegtables, "jpegheader": keyframe.jpegheader}
    except Exception as exc:  # a short or unreadable file
        raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}") from exc
    expected = int(page.databytecounts[index])
    if len(data) != expected:  # a self-terminating stream would decode without the rest (#377)
        raise PlaneReadError(
            f"Part {index} of a page of {path} is truncated: {len(data)} of {expected} bytes. "
            "The file is incomplete or its header is damaged: copy it again from the original."
        )
    if jpeg:
        check_jpeg_piece(data, page.jpegtables, piece_decode(keyframe), index, path)
        _, where, shape = decode(None, index)  # no data: nothing is decoded
        try:
            return jpeg_piece.decode_jpeg_piece(
                keyframe, data, (where, shape), page.jpegtables, jfif
            )
        except ValueError as exc:  # refused by libjpeg, with nothing else tried
            raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}") from exc
    try:
        segment, _, _ = decode(data, index, **kwargs)
    except Exception as exc:  # decoding errors come from many codecs
        raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}") from exc
    return segment


def check_jpeg_piece(
    data: bytes,
    tables: bytes | None,
    decode: PieceDecode,
    index: int,
    path: str | os.PathLike,
) -> None:
    """Refuse a JPEG piece whose stream declares more than the piece, before decoding it.

    libjpeg sizes its buffers and the decoded array from the stream's frame header, not from
    the TIFF header; the memory planned is the TIFF piece's (``window_memory``). The frame is
    the one libjpeg reads (:mod:`.jpeg_stream`); a stream it could read otherwise is refused.
    """
    try:
        if tables is not None:
            jpeg_stream.check_tables(bytes(tables))
        found = jpeg_stream.frame(data)
    except jpeg_stream.JpegStreamError as exc:
        raise PlaneReadError(f"Part {index} of a page of {path} cannot be read: {exc}.") from exc
    piece = (decode.rows, decode.columns, decode.samples)
    sizes = (found.rows, found.columns, found.components)
    if found.precision == 8 and all(0 < a <= b for a, b in zip(sizes, piece, strict=True)):
        return
    raise PlaneReadError(
        f"Part {index} of a page of {path} cannot be read: its JPEG stream declares "
        f"{found.precision}-bit samples, {found.rows} × {found.columns} pixels and "
        f"{found.components} components, not 8-bit samples within the piece's "
        f"{decode.rows} × {decode.columns} pixels and {decode.samples} samples."
    )


def page_array(page: tifffile.TiffPage | tifffile.TiffFrame, path: str | os.PathLike) -> np.ndarray:
    """Decode a whole page: a JPEG page with :func:`read_jpeg_page`, any other by tifffile."""
    if page.keyframe.compression == JPEG:
        return read_jpeg_page(page, path)
    return page.asarray()


def read_jpeg_page(
    page: tifffile.TiffPage | tifffile.TiffFrame, path: str | os.PathLike
) -> np.ndarray:
    """Decode a JPEG page piece by piece, each checked before it is decoded, as tifffile does.

    The same placement as ``TiffPage.asarray`` (its ``segments``): each strip or tile decoded
    whole, clipped to the page and written into the page array, in tifffile's decoding threads
    (``maxworkers``), a piece read under the file's lock; an empty piece is the fill value.
    Returns the page as ``keyframe.shaped``.
    """
    keyframe = page.keyframe
    result = np.empty(keyframe.shaped, dtype=keyframe.dtype)
    depth, length, width = keyframe.imagedepth, keyframe.imagelength, keyframe.imagewidth

    def place(index: int) -> None:
        empty = int(page.dataoffsets[index]) == 0 or int(page.databytecounts[index]) == 0
        segment = None if empty else _decode_segment(page, index, path, lock)
        _, (s, d, h, w, _), shape = keyframe.decode(None, index)  # where the piece goes
        where = (s, slice(d, d + shape[0]), slice(h, h + shape[1]), slice(w, w + shape[2]))
        if segment is None:
            result[where] = keyframe.nodata
        else:
            result[where] = segment[: depth - d, : length - h, : width - w]

    pieces, lock = range(len(page.dataoffsets)), threading.Lock()  # one read at a time
    with ThreadPoolExecutor(max(1, int(keyframe.maxworkers))) as pool:
        for _ in pool.map(place, pieces):
            pass
    return result
