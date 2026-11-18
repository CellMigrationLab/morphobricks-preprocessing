"""The working memory of a window read beyond its pixels and pieces (review of #339, finding 1).

:func:`.windows.window_plan` estimates a window read from the header before any pixel is read.
The estimate is meant as an **upper bound** of the read's peak working memory, so a consumer can
plan with it. Besides the result and the stored pieces, a read holds:

- **the open file's header** (:func:`header_bytes`): tifffile's parsed pages and tags, and the
  offsets and byte counts of every strip or tile of the page (Python integers); zarr's metadata;
- **the decode's buffers** (:func:`tiff_piece_work`, :func:`zarr_piece_work`): a compressed TIFF
  piece is held encoded, decompressed, as an array, reshaped and, with a predictor, undone,
  and the previous piece is still referenced while the next one is decoded; a zarr chunk is
  held compressed and decoded;
- **the codec's own state** (:data:`CODEC_STATE`): deflate's window, LZMA's dictionary, LZW's
  code table, libjpeg's decompressor; and, for JPEG, libjpeg's buffers for the piece
  (:func:`native_piece_work`);
- with a window cache, the immutable copy of a piece the cache keeps (:data:`FROZEN_COPIES`).

A zarr 3 read runs with threads of its own, joined before it returns
(``zarr_input.read_selection``, #354): zarr's shared worker threads could keep a chunk's
buffers alive after its read had returned when the CPU was busy, which no estimate made before
the read bounds. So the zarr buffers counted here are all a read holds, and none outlives it.

The constants are bounds checked by measuring peaks (``tracemalloc``) over a matrix of layouts,
pixel types, codecs and window geometries (``tests/test_window_memory.py``): uncompressed,
deflate with and without a predictor and LZMA (presets up to 9) TIFF, in tiles and strips, one
channel and interleaved RGB; OME-Zarr 0.4 and 0.5 with the default compressors.

**LZW and JPEG** (#356, owner decision of 18 November 2026) are decoded by imagecodecs, whose
own memory is allocated outside Python's allocators (a C library's ``malloc``), unseen by
``tracemalloc``; so their terms are measured as the peak resident memory (RSS) of the read in a
fresh process, and their native parts alone as the peak of the C heap during one decode
(``tests/test_codec_memory.py``, ``tests/codec_peaks.py``):

- LZW (``imagecodecs.lzw_decode``) decodes into a buffer of the piece's size given by tifffile,
  as deflate does, so the same :data:`TIFF_DECODE_COPIES` hold; its own state, in
  :data:`CODEC_STATE`, is its code table: 4096 entries of a pointer and a length (40 KiB) from
  imagecodecs 2026.11; up to 2026.3 (the last for Python 3.11) 5120 entries and a string buffer
  of 64 KiB, which grows only when tifffile gives no output size (about 150 KiB measured, the
  same for blank, smooth and noisy pieces).
- JPEG (libjpeg-turbo through ``imagecodecs.jpeg_decode``) decodes a piece into one new array
  (:data:`JPEG_DECODE_COPIES`: it, and the previous piece still referenced). libjpeg's own
  memory is its state (pools, Huffman and quantisation tables: :data:`CODEC_STATE`) and, per
  piece (:func:`native_piece_work`), its row buffers (:data:`JPEG_ROWS` rows of the piece's
  width: the main buffer's row groups with the upsampler's context rows, and the upsampler's
  colour rows, at libjpeg's largest vertical sampling factor, whatever the header says) and, for
  a multi-scan (progressive) JPEG, the coefficients of the whole piece, two bytes per 8-bit
  sample (:data:`JPEG_COEFFICIENT_BYTES`). Which a piece is cannot be told from the TIFF
  header, so every JPEG piece is charged for both, and for the previous piece's too
  (:data:`JPEG_NATIVE_PIECES`), which the C allocator may still hold. libjpeg sizes
  all of it from the piece's own JPEG stream, so every read refuses a piece whose stream declares
  more than the piece before libjpeg sees it, its markers walked as libjpeg walks them
  (:mod:`.jpeg_stream`, ``tiff_pieces.check_jpeg_piece``).

A codec outside that matrix (Zstandard, WebP, JPEG 2000... through imagecodecs) is refused
before any pixel is read (:mod:`.codec_bounds`, issue #356): no bound is invented for it.
"""

from __future__ import annotations

from dataclasses import dataclass

KIB = 2**10
MIB = 2**20
OPEN_BYTES = 256 * KIB
"""The parsed header of an open file and the reader's bookkeeping, whatever its size."""
PAGE_BYTES = 2 * KIB
"""tifffile's parsed page (or frame) and its tags, per page of the series."""
PIECE_ENTRY_BYTES = 80
"""One strip or tile's offset and byte count, as tifffile holds them (two Python integers)."""
TIFF_DECODE_COPIES = 5
"""Buffers of a decoded compressed TIFF piece held at once (module docstring)."""
ZARR_DECODE_COPIES = 2
"""Buffers of a zarr chunk held at once besides the chunk itself: compressed and decoded."""
FROZEN_COPIES = 1
"""The immutable copy a window cache makes of a piece it keeps (``block_cache``)."""
NO_COMPRESSION = 1
LZW = 5
"""TIFF compression code of LZW."""
JPEG = 7
"""TIFF compression code of JPEG ("new-style", TIFF 6.0 technical note 2)."""
IMAGECODECS = (LZW, JPEG)
"""The bounded TIFF codecs decoded in C by imagecodecs, measured by RSS (#356)."""
DEFLATE = (8, 32946)
"""TIFF compression codes of deflate (zlib): ``ADOBE_DEFLATE`` and ``DEFLATE``."""
LZMA = 34925
CODEC_STATE = {
    NO_COMPRESSION: 0,
    LZW: 192 * KIB,
    JPEG: 64 * KIB,
    **dict.fromkeys(DEFLATE, 64 * KIB),
    LZMA: 66 * MIB,
}
"""A decoder's own state by TIFF compression code: LZW's code table and string buffer (40 KiB
from imagecodecs 2026.11, about 150 KiB before: 5120 entries and a 64 KiB buffer), libjpeg's
decompressor, memory pools and tables (about 27 KiB measured), deflate's window and tables,
LZMA's dictionary (at most 64 MiB for the presets 0 to 9) and its tables."""
JPEG_DECODE_COPIES = 2
"""Decoded JPEG pieces held at once: the one decoded and the previous one, still referenced."""
JPEG_NATIVE_PIECES = 2
"""libjpeg's buffers of pieces counted at once: the piece decoded and the previous one's, which
the C allocator (glibc) may keep in its heap, where the next piece's buffers of other sizes need
not fit (peak RSS up to 1.005 of an estimate counting one, on Python 3.11's imagecodecs)."""
JPEG_COEFFICIENT_BYTES = 2
"""libjpeg's coefficients of a multi-scan (progressive) JPEG piece, per 8-bit sample."""
JPEG_ROWS = 44
"""libjpeg's rows of a piece's width (times its samples): (8 + 2 context + 1 colour) row groups
of up to 4 rows (libjpeg's largest vertical sampling factor)."""
JPEG_MCU = 32
"""libjpeg's largest MCU side (4 × 8 pixels), to which it pads a piece's rows and columns."""


@dataclass(frozen=True)
class PieceDecode:
    """How one stored strip or tile of a TIFF page is decoded, from the page's header.

    ``rows`` and ``columns`` are the piece's, ``samples`` its decoded samples per pixel.
    """

    compression: int
    rows: int = 0
    columns: int = 0
    samples: int = 1


def header_bytes(pages: int, pieces_per_page: int) -> int:
    """Return the bound of an open TIFF's parsed header: ``pages`` pages of ``pieces`` pieces."""
    return OPEN_BYTES + pages * PAGE_BYTES + pieces_per_page * PIECE_ENTRY_BYTES


def codec_state(compression: int) -> int:
    """Return the bound of a TIFF decoder's state (a codec not in the table is refused, #356)."""
    return CODEC_STATE[int(compression)]


def decode_copies(compression: int) -> int:
    """Return the decoded copies of a compressed TIFF piece held at once by its decode."""
    return JPEG_DECODE_COPIES if int(compression) == JPEG else TIFF_DECODE_COPIES


def native_piece_work(decode: PieceDecode) -> int:
    """Return what a codec's C library holds for one piece beside its state (JPEG only).

    libjpeg pads the piece to whole MCUs (:data:`JPEG_MCU`); a multi-scan JPEG keeps the
    coefficients of all of it (:data:`JPEG_COEFFICIENT_BYTES`), and every JPEG its row buffers
    (:data:`JPEG_ROWS`).
    """
    if decode.compression != JPEG:
        return 0
    columns = -(-decode.columns // JPEG_MCU) * JPEG_MCU * decode.samples
    rows = -(-decode.rows // JPEG_MCU) * JPEG_MCU
    return JPEG_COEFFICIENT_BYTES * rows * columns + JPEG_ROWS * columns


def tiff_piece_work(
    piece_nbytes: int, encoded_nbytes: int, cached: bool, decode: PieceDecode
) -> int:
    """Return the working memory of decoding compressed TIFF pieces one at a time.

    The piece held encoded, its decoded copies (:func:`decode_copies`, and the frozen copy of
    a cache) and the codec's own buffers for it (:func:`native_piece_work`); the codec's
    state is counted apart (:func:`codec_state`), once per decoding thread.
    """
    copies = decode_copies(decode.compression) + (FROZEN_COPIES if cached else 0)
    return encoded_nbytes + copies * piece_nbytes + JPEG_NATIVE_PIECES * native_piece_work(decode)


def block_piece_work(piece_nbytes: int, encoded_nbytes: int, decode: PieceDecode) -> int:
    """Return the working memory of decoding the compressed pieces of a block, one at a time.

    LZW and JPEG (:data:`IMAGECODECS`, #356) count what a window read does: the decode's
    buffers, the C library's and the codec's state. The other codecs count the piece decoded
    and its encoded bytes, the block reader's estimate before #356 (its bound is #342's).
    """
    if decode.compression not in IMAGECODECS:
        return piece_nbytes + encoded_nbytes
    work = tiff_piece_work(piece_nbytes, encoded_nbytes, cached=False, decode=decode)
    return work + codec_state(decode.compression)


def zarr_piece_work(pieces: int, piece_nbytes: int, cached: bool) -> int:
    """Return the working memory of ``pieces`` zarr chunks decoded for one window."""
    if cached:  # chunk by chunk through the cache: one chunk in flight, and its frozen copy
        return (1 + ZARR_DECODE_COPIES + FROZEN_COPIES) * piece_nbytes
    return pieces * (1 + ZARR_DECODE_COPIES) * piece_nbytes


def grid_cache_bytes(
    unit: tuple[int, int],
    stored_shape: tuple[int, int],
    span: int,
    piece_nbytes: int,
    groups: int,
) -> tuple[int, int, int]:
    """Return the cache a grid of windows read row after row needs: bytes, piece rows, per row.

    ``unit`` is a piece's ``(rows, columns)`` and ``stored_shape`` the plane's, both in stored
    order; ``span`` is one window's extent along the stored rows, its margins included. A
    window touches at most ``ceil(span / unit_rows) + 1`` rows of pieces (one more when it is
    not aligned on them), and the next row of windows reuses them, so the cache must hold that
    many rows of pieces across the whole plane, for every group of channels stored apart.
    """
    piece_rows = min(-(-span // unit[0]) + 1, -(-stored_shape[0] // unit[0]))
    per_row = -(-stored_shape[1] // unit[1])
    return piece_rows * per_row * piece_nbytes * groups, piece_rows, per_row
