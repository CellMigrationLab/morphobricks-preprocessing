"""Write the LZW and JPEG TIFFs of the codec memory tests (#356); they need imagecodecs.

JPEG pieces are written the way whole-slide scanners and libtiff write them: each strip or tile
an abbreviated JPEG stream, the quantisation and Huffman tables once in the TIFF header
(``JPEGTables``). ``kind`` is ``"ycc420"`` and ``"ycc422"`` (YCbCr, subsampled 2 × 2 and 2 × 1,
what tifffile writes for RGB), ``"rgb"`` (RGB-encoded, as Aperio SVS) or ``"grey"``. A
progressive (multi-scan) piece is made losslessly from the baseline one (``jpeg_transcode``,
imagecodecs 2026.11), or by Pillow with the same subsampling and colour space before it.
"""

from __future__ import annotations

import io

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


def _progressive(stream, piece, kind):
    """Return a progressive JPEG of ``piece``: transcoded from ``stream``, else by Pillow."""
    if hasattr(imagecodecs, "jpeg_transcode"):  # imagecodecs 2026.11 and later
        return bytes(imagecodecs.jpeg_transcode(stream, progressive=True))
    image = pytest.importorskip("PIL.Image")
    file = pytest.importorskip("PIL.ImageFile")
    file.MAXBLOCK = max(file.MAXBLOCK, 2**26)  # a progressive noisy piece is buffered whole
    options = {"subsampling": {"ycc420": 2, "ycc422": 1}.get(kind, 0)}
    if kind == "rgb":
        options["keep_rgb"] = True  # RGB-encoded, as the TIFF's photometric says
    out = io.BytesIO()
    image.fromarray(piece).save(out, "JPEG", quality=90, progressive=True, **options)
    return out.getvalue()


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
                pieces.append(_progressive(stream, piece, kind))
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


def _lossless_header(side):
    """DHT, SOF3 and SOS of a 16-bit, one-component lossless JPEG of ``side`` ² (no SOI).

    One Huffman code ``0`` means difference category 0: every zero bit decodes one pixel.
    """
    dht = b"\xff\xc4\x00\x15" + b"\x00" + b"\x01\x01" + bytes(14) + b"\x00\x10"
    sof3 = b"\xff\xc3\x00\x0b\x10" + side.to_bytes(2, "big") * 2 + b"\x01\x00\x11\x00"
    sos = b"\xff\xda\x00\x08\x01\x00\x00\x06\x00\x00"
    return dht + sof3 + sos


def ljpeg_bypass_piece(side, tile):
    """Return a JPEG piece whose libjpeg frame is ``tile`` ² but whose LJPEG frame is ``side`` ².

    The F1 stream of #357's second review. Walked as libjpeg walks it (``jpeg_stream``): SOI,
    a fill byte, an APP0 of length 0xFFFF and zero APP1 segments, a DHT, a SOF0 of ``tile`` ²
    and EOI, so ``check_jpeg_piece`` accepts it; libjpeg then refuses the DHT ("Bogus Huffman
    table definition"). liblj92 (``imagecodecs.ljpeg_decode``, the fallback of tifffile 2026.11
    and of imagecodecs' ``jpeg_decode`` before 2026.11) reads ``FF FF`` as a marker of length
    0xE0FF, lands inside the APP0 payload on a hidden SOF3 of ``side`` ² and decodes the zero
    bytes after it, one bit per pixel: ``side`` ² uint16 from a piece of about ``side`` ² / 8.
    """
    hidden = _lossless_header(side)
    out = bytearray(b"\xff\xd8")
    payload = bytearray(0xFFFF - 2)
    at = 4 + 0xE0FF - 6  # where liblj92 resumes, in the APP0 payload (which starts at 6)
    payload[at : at + len(hidden)] = hidden
    out += b"\xff\xff\xe0" + (0xFFFF).to_bytes(2, "big") + payload
    left = side * side // 8 + 4096 - (len(payload) - at - len(hidden))
    while left > 0:  # more zero entropy data for liblj92, APP1 segments for libjpeg
        n = min(left, 0xFFFF - 2)
        out += b"\xff\xe1" + (n + 2).to_bytes(2, "big") + bytes(n)
        left -= n + 4
    out += b"\xff\xc4\x00\x13\x00" + b"\x01" + bytes(15)  # a DHT libjpeg calls bogus
    out += b"\xff\xc0\x00\x0b\x08" + tile.to_bytes(2, "big") * 2 + b"\x01\x01\x11\x00"
    return bytes(out + b"\xff\xd9")


def write_ljpeg_bypass(path, side, tile):
    """Write a 2 × 2-tile greyscale JPEG TIFF whose last tile is :func:`ljpeg_bypass_piece`."""
    good = bytes(imagecodecs.jpeg8_encode(np.zeros((tile, tile), np.uint8), level=90))
    tifffile.imwrite(
        path,
        iter([good] * 3 + [ljpeg_bypass_piece(side, tile)]),
        shape=(2 * tile, 2 * tile),
        dtype=np.uint8,
        tile=(tile, tile),
        compression=7,
        photometric="minisblack",
    )
    return path
