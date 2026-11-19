"""Read every channel of one plane into a single ``(C, Y, X)`` array (issue #344).

:meth:`.run_inputs.RunInputs.read_plane` returns all channels of a plane at once. Reading
them one :func:`.planes.read_plane` call at a time decoded a page holding several channels
(an interleaved RGB page, a planar page) once per channel, and checked each call's memory
alone, although the planes already read and their stacked copy were held next to it.

Here the result is allocated once and filled channel by channel, straight from the decoded
page: a page holding several channels is decoded once for all of them, and only one page is
held at a time. The memory checked before any pixel is read bounds that whole read
(:func:`xyc_plane_memory`):

- TIFF: the ``(C, Y, X)`` result, the decoded page, and what ``tifffile`` holds while it
  decodes the page: the encoded bytes it reads (twice: the bytes read and their slices per
  strip or tile; at most four read buffers when they are read in several buffers) and, per
  decoding thread, what a window read counts for one piece: one encoded and its decoded copies,
  the codec's own buffers for it and the codec's state (``window_memory.tiff_piece_work`` and
  ``codec_state``, one definition for windows, these reads and blocks). An uncompressed page
  stored in one run of bytes is read straight into the page array. Plus the parsed header.
- OME-Zarr: the result, every chunk one plane touches (:data:`ZARR_COPIES` times: stored,
  decompressed, decoded) and the plane zarr returns for each channel.

This bound is measured for deflate, LZMA and uncompressed TIFF pages decoded by ``tifffile``
itself and for LZW and JPEG pages decoded through imagecodecs (peak RSS); a page stored with
any other codec (:data:`.codec_bounds.TIFF_BOUNDED`) is refused from its header before any
pixel is read (:class:`.plane_errors.CompressionNotBoundedError`, issue #356). A JPEG page is
decoded piece by piece in tifffile's threads, each piece's stream checked before it is decoded
(``tiff_pieces.read_jpeg_page``, through ``planes._read_page``): libjpeg sizes what it
allocates from the stream, so a stream declaring more than its piece is refused, as window and
block reads refuse it. The estimate of
a single :func:`.planes.read_plane` call is unchanged.

The values are those of :func:`.planes.read_plane`, channel by channel: the same selection
of the same decoded page, oriented Y before X and converted to native byte order.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import tifffile

from . import window_memory
from .axes import AxisMapping
from .codec_bounds import check_tiff_codec
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    PlaneLayout,
    PlaneMemoryError,
    _Binning,
    _layout,
    _open,
    _open_zarr,
    _page_index,
    _position,
    _read_page,
    _zarr_layout,
    _zarr_read,
    format_bytes,
)
from .tiff_pieces import piece_decode
from .zarr_input import NgffImage, is_zarr_store

KIB = 2**10
MIB = 2**20
OPEN_BYTES = 2 * MIB
"""The open file and the reader's bookkeeping, beside the per-page and per-piece entries."""
PAGE_BYTES = 2 * KIB
"""tifffile's parsed page (or frame) and its tags, per page of the series."""
PIECE_ENTRY_BYTES = 80
"""One strip or tile's offset and byte count, as tifffile holds them (two Python integers)."""
ZARR_COPIES = 3
"""Copies of each chunk a plane touches held at once: stored, decompressed and decoded."""


def xyc_plane_memory(
    path: str | os.PathLike,
    mapping: AxisMapping,
    channel_count: int | None = None,
    *,
    z: int | None = None,
    t: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> int:
    """Return the working memory :func:`read_xyc_plane` checks, from the header only.

    ``channel_count`` defaults to the image's channel count (1 without a channel axis). For
    TIFF the bound is of the pages holding the plane at ``z`` and ``t``, sized from their own
    strip or tile byte counts (pages can compress very differently).
    """
    if is_zarr_store(path):
        layout = _zarr_layout(
            _open_zarr(path), mapping, path, _Binning(histogram=False), memory_limit
        )
        return _zarr_memory(layout, _count(layout, channel_count))
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, _Binning(histogram=False), memory_limit)
        count = _checked(layout, channel_count, path)
        pages = _page_indices(layout, mapping, {"Z": z, "T": t}, count)
        return _tiff_memory(series, layout, pages, count, path)


def read_xyc_plane(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    z: int | None = None,
    t: int | None = None,
    channel_count: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> np.ndarray:
    """Read channels ``0 .. channel_count - 1`` of one plane as a ``(C, Y, X)`` array.

    Each channel is bitwise :func:`.planes.read_plane` of that channel. Coordinates left as
    None default to 0 for the axes the mapping has; ``channel_count`` defaults to every
    channel (1 without a channel axis).

    Raises
    ------
    PlaneMemoryError
        If reading the channels whole needs more memory than ``memory_limit``
        (:func:`xyc_plane_memory`); nothing is decoded then.
    PlaneReadError
        If the file cannot be read under ``mapping``.
    IndexError
        If a coordinate is outside the image, or given for an axis it lacks.
    """
    wanted = {"Z": z, "T": t}
    if is_zarr_store(path):
        image = _open_zarr(path)
        layout = _zarr_layout(image, mapping, path, _Binning(histogram=False), memory_limit)
        count = _checked(layout, channel_count, path)
        _refuse_over(_zarr_memory(layout, count), layout, count, path)
        return _read_zarr(image, layout, mapping, path, wanted, count)
    with _open(path) as tif:
        series = tif.series[0]
        layout = _layout(series, mapping, path, _Binning(histogram=False), memory_limit)
        count = _checked(layout, channel_count, path)
        pages = _page_indices(layout, mapping, wanted, count)
        _refuse_over(_tiff_memory(series, layout, pages, count, path), layout, count, path)
        return _read_tiff(series, layout, mapping, path, wanted, count)


def _count(layout: PlaneLayout, channel_count: int | None) -> int:
    return layout.channel_count if channel_count is None else channel_count


def _plane_nbytes(layout: PlaneLayout) -> int:
    return layout.dtype.itemsize * layout.plane_shape[0] * layout.plane_shape[1]


def _page_indices(
    layout: PlaneLayout, mapping: AxisMapping, wanted: dict[str, int | None], count: int
) -> list[int]:
    """Return the indices of the pages holding channels ``0 .. count - 1`` of a plane."""
    positions = (_position(layout, mapping, _wanted(wanted, mapping, c)) for c in range(count))
    return sorted({_page_index(layout, position) for position in positions})


def _tiff_memory(
    series: tifffile.TiffPageSeries,
    layout: PlaneLayout,
    pages: list[int],
    count: int,
    path: str | os.PathLike,
) -> int:
    """Bound the memory of reading ``count`` channels of a plane of a TIFF series whole.

    The pages are decoded one at a time, so the largest decode of ``pages`` counts. A page
    stored with a codec whose memory is not measured is refused here, from its header,
    before any pixel is read (:mod:`.codec_bounds`, issue #356).
    """
    found = [_page(series, index) for index in pages]
    check_tiff_codec(found, path, "every channel at once (RunInputs.read_plane)")
    pieces = max(len(page.databytecounts) for page in found)
    header = OPEN_BYTES + PAGE_BYTES * len(series.pages) + PIECE_ENTRY_BYTES * pieces
    decode = max(_decode_memory(series.keyframe, page) for page in found)
    return count * _plane_nbytes(layout) + layout.page_nbytes + decode + header


def _page(series: tifffile.TiffPageSeries, index: int) -> tifffile.TiffPage | tifffile.TiffFrame:
    """Return a page's header (page or frame; the keyframe for a page missing from the file).

    A page's pieces can compress very differently from the first page's (a blank plane next
    to a noisy one), and ``tifffile`` reads the page it decodes with its own byte counts.
    """
    page = series.pages[index]
    return series.keyframe if page is None else page


def _decode_memory(
    keyframe: tifffile.TiffPage, page: tifffile.TiffPage | tifffile.TiffFrame
) -> int:
    """Bound what ``tifffile`` holds beside the page array while it decodes one page.

    It reads ``page``'s own strips or tiles (their own byte counts) in buffers of up to
    ``TIFF.BUFFERSIZE`` bytes and slices them into one ``bytes`` per piece, keeping both;
    while the next buffer is read the previous one and its slices can still be held. Each
    decoding thread holds one encoded piece, its decoded copies (decompressed, as an array,
    reshaped, unpredicted), the codec's own buffers for it and the codec's state, as a window
    read counts them (``window_memory``). The thread count is ``tifffile``'s for this machine.
    """
    if page.is_contiguous:
        return 0  # read straight into the page array
    counts = [int(n) for n in page.databytecounts]
    encoded, largest = sum(counts), max(counts, default=0)
    buffers = min(2 * encoded, 4 * (tifffile.TIFF.BUFFERSIZE + largest))
    piece = math.prod(int(n) for n in keyframe.chunks) * np.dtype(keyframe.dtype).itemsize
    threads = max(1, int(keyframe.maxworkers))
    decode = piece_decode(keyframe)
    work = window_memory.tiff_piece_work(piece, largest, cached=False, decode=decode)
    state = window_memory.codec_state(decode.compression)  # other codecs are refused (#356)
    return buffers + threads * (work + state)


def _zarr_memory(layout: PlaneLayout, count: int) -> int:
    """Bound the memory of reading ``count`` channels of an OME-Zarr plane whole."""
    return (count + 1) * _plane_nbytes(layout) + ZARR_COPIES * layout.page_nbytes + OPEN_BYTES


def _checked(layout: PlaneLayout, channel_count: int | None, path: str | os.PathLike) -> int:
    """Return the number of channels to read, checked against the image."""
    count = _count(layout, channel_count)
    if not 1 <= count <= layout.channel_count:
        raise IndexError(
            f"{Path(path).name} has {layout.channel_count} channel(s); cannot read {count}."
        )
    return count


def _refuse_over(memory: int, layout: PlaneLayout, count: int, path: str | os.PathLike) -> None:
    """Refuse a read whose working memory is over the limit, before any pixel is read."""
    if memory <= layout.memory_limit:
        return
    channels = "its channel" if count == 1 else f"all {count} channels"
    raise PlaneMemoryError(
        f"Reading one plane of {Path(path).name} whole, {channels}, needs about "
        f"{format_bytes(memory)} of working memory, more than the limit of "
        f"{format_bytes(layout.memory_limit)}. Raise the memory limit if this computer "
        "has enough memory, or read the plane in windows."
    )


def _wanted(wanted: dict[str, int | None], mapping: AxisMapping, c: int) -> dict[str, int | None]:
    return {**wanted, "C": None if mapping.index_of("C") is None else c}


def _target(out: np.ndarray, c: int, mapping: AxisMapping) -> np.ndarray:
    """Return channel ``c`` of the result in the stored order of the plane's axes."""
    return out[c].T if mapping.index_of("X") < mapping.index_of("Y") else out[c]


def _read_tiff(
    series: tifffile.TiffPageSeries,
    layout: PlaneLayout,
    mapping: AxisMapping,
    path: str | os.PathLike,
    wanted: dict[str, int | None],
    count: int,
) -> np.ndarray:
    """Fill the result from the pages, decoding each page holding a channel once."""
    positions = [_position(layout, mapping, _wanted(wanted, mapping, c)) for c in range(count)]
    out = np.empty((count, *layout.plane_shape), dtype=layout.dtype)
    k = layout.page_ndim
    index, page = -1, None
    for c, position in enumerate(positions):
        wanted_index = _page_index(layout, position)
        if wanted_index != index:
            page = None  # the previous page is let go before the next is decoded
            page = _read_page(series, wanted_index, layout, path)
            index = wanted_index
        selection = tuple(position.get(d, slice(None)) for d in range(k, len(layout.shape)))
        # Converted as read_plane converts (np.ascontiguousarray with the layout's dtype).
        np.copyto(_target(out, c, mapping), page[selection], casting="unsafe")
    return out


def _read_zarr(
    image: NgffImage,
    layout: PlaneLayout,
    mapping: AxisMapping,
    path: str | os.PathLike,
    wanted: dict[str, int | None],
    count: int,
) -> np.ndarray:
    """Fill the result one channel at a time; zarr decodes only the chunks each touches."""
    positions = [_position(layout, mapping, _wanted(wanted, mapping, c)) for c in range(count)]
    out = np.empty((count, *layout.plane_shape), dtype=layout.dtype)
    for c, position in enumerate(positions):
        selection = tuple(position.get(d, slice(None)) for d in range(len(layout.shape)))
        pixels = _zarr_read(image, selection, path)
        np.copyto(_target(out, c, mapping), pixels, casting="unsafe")
    return out
