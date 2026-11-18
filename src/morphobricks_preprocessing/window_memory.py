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
- **the codec's own state** (:data:`CODEC_STATE`): deflate's window, LZMA's dictionary;
- with a window cache, the immutable copy of a piece the cache keeps (:data:`FROZEN_COPIES`).

The constants are bounds checked by measuring peaks (``tracemalloc``) over a matrix of layouts,
pixel types, codecs and window geometries (``tests/test_window_memory.py``): uncompressed,
deflate with and without a predictor and LZMA (presets up to 9) TIFF, in tiles and strips, one
channel and interleaved RGB; OME-Zarr 0.4 and 0.5 with the default compressors. A codec outside
that matrix (JPEG, LZW, Zstandard, WebP... through imagecodecs) is refused before any pixel is
read (:mod:`.codec_bounds`, issue #356): no bound is invented for it. Memory a codec allocates
outside Python's allocators (a C library's own ``malloc``) is not seen by ``tracemalloc`` and
not counted.
"""

from __future__ import annotations

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
DEFLATE = (8, 32946)
"""TIFF compression codes of deflate (zlib): ``ADOBE_DEFLATE`` and ``DEFLATE``."""
LZMA = 34925
CODEC_STATE = {NO_COMPRESSION: 0, **dict.fromkeys(DEFLATE, 64 * KIB), LZMA: 66 * MIB}
"""A decoder's own state by TIFF compression code: deflate's window and tables, LZMA's
dictionary (at most 64 MiB for the presets 0 to 9) and its tables."""


def header_bytes(pages: int, pieces_per_page: int) -> int:
    """Return the bound of an open TIFF's parsed header: ``pages`` pages of ``pieces`` pieces."""
    return OPEN_BYTES + pages * PAGE_BYTES + pieces_per_page * PIECE_ENTRY_BYTES


def codec_state(compression: int) -> int:
    """Return the bound of a TIFF decoder's state (a codec not in the table is refused, #356)."""
    return CODEC_STATE[int(compression)]


def tiff_piece_work(piece_nbytes: int, encoded_nbytes: int, cached: bool) -> int:
    """Return the working memory of decoding compressed TIFF pieces one at a time."""
    copies = TIFF_DECODE_COPIES + (FROZEN_COPIES if cached else 0)
    return encoded_nbytes + copies * piece_nbytes


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
