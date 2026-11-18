"""Write the LZW and JPEG TIFFs of the codec memory tests (#356); they need imagecodecs.

JPEG pieces are written the way whole-slide scanners and libtiff write them: each strip or tile
an abbreviated JPEG stream, the quantisation and Huffman tables once in the TIFF header
(``JPEGTables``). ``kind`` is ``"ycc420"`` and ``"ycc422"`` (YCbCr, subsampled 2 × 2 and 2 × 1,
what tifffile writes for RGB), ``"rgb"`` (RGB-encoded, as Aperio SVS) or ``"grey"``. A
progressive (multi-scan) piece is made losslessly from the baseline one (``jpeg_transcode``).
"""

from __future__ import annotations

import numpy as np
import pytest
import tifffile

imagecodecs = pytest.importorskip("imagecodecs")

KINDS = {  # (encoder arguments, tifffile.imwrite arguments)
    "ycc420": (
        {"colorspace": "RGB", "outcolorspace": "YCBCR", "subsampling": (2, 2)},
        {"photometric": "rgb", "subsampling": (2, 2)},
    ),
    "ycc422": (
        {"colorspace": "RGB", "outcolorspace": "YCBCR", "subsampling": (2, 1)},
        {"photometric": "rgb", "subsampling": (2, 1)},
    ),
    "rgb": (
        {"colorspace": "RGB", "outcolorspace": "RGB", "subsampling": (1, 1)},
        {"photometric": "rgb", "subsampling": (1, 1), "compressionargs": {"outcolorspace": "rgb"}},
    ),
    "grey": ({}, {"photometric": "minisblack"}),
}


def content(shape, noisy, dtype="uint8", seed=0):
    """Return pixels of ``shape``: noise over the whole range, or smooth (compressible)."""
    top = np.iinfo(dtype).max
    if noisy:
        return np.random.default_rng(seed).integers(0, top, shape, dtype=dtype, endpoint=True)
    y, x = np.mgrid[0 : shape[0], 0 : shape[1]]
    a = ((0.5 + 0.4 * np.sin(x / 37.0) * np.cos(y / 53.0)) * top).astype(dtype)
    return np.stack([a, top - a, a // 2], -1) if len(shape) == 3 else a


def split_tables(stream):
    """Return a baseline JPEG stream's tables-only stream and its abbreviated stream."""
    i, tables, rest = 2, [b"\xff\xd8"], [b"\xff\xd8"]
    while stream[i : i + 2] != b"\xff\xda":  # up to the first scan
        length = int.from_bytes(stream[i + 2 : i + 4], "big")
        segment = stream[i : i + 2 + length]
        (tables if stream[i : i + 2] in (b"\xff\xdb", b"\xff\xc4") else rest).append(segment)
        i += 2 + length
    return b"".join([*tables, b"\xff\xd9"]), b"".join([*rest, stream[i:]])


def write_jpeg(path, data, kind, *, tile=None, rows=None, progressive=False):
    """Write ``data`` as JPEG tiles (``tile``) or strips of ``rows`` rows, tables in the header."""
    encode, write = KINDS[kind]
    height, width = data.shape[:2]
    step = tile or (rows, width)
    pieces, tables = [], None
    for top in range(0, height, step[0]):
        for left in range(0, width, step[1]):
            piece = data[top : top + step[0], left : left + step[1]]
            if tile is not None and piece.shape[:2] != tuple(tile):  # tiles are whole
                padded = np.zeros(tuple(tile) + data.shape[2:], np.uint8)
                padded[: piece.shape[0], : piece.shape[1]] = piece
                piece = padded
            stream = bytes(imagecodecs.jpeg8_encode(piece, level=90, **encode))
            if progressive:
                pieces.append(bytes(imagecodecs.jpeg_transcode(stream, progressive=True)))
                continue
            found, abbreviated = split_tables(stream)
            assert tables in (None, found)  # one set of tables for every piece
            tables = found
            pieces.append(abbreviated)
    storage = {"tile": tuple(tile)} if tile is not None else {"rowsperstrip": rows}
    tifffile.imwrite(
        path,
        iter(pieces),
        shape=data.shape,
        dtype=np.uint8,
        compression=7,
        jpegtables=tables,
        **storage,
        **write,
    )
    return path


def write_lzw(path, data, *, predictor=False, **storage):
    """Write ``data`` LZW-compressed (``storage``: ``tile`` or ``rowsperstrip``)."""
    photometric = "rgb" if data.ndim == 3 else "minisblack"
    tifffile.imwrite(
        path, data, compression="lzw", predictor=predictor, photometric=photometric, **storage
    )
    return path
