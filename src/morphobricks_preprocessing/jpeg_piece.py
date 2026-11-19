"""Decode one JPEG strip or tile with libjpeg alone, never with another decoder (#356, F1).

tifffile's ``TiffPage.decode`` (2026.11 and later) and, before 2026.11, imagecodecs' own
``jpeg_decode`` retry a stream libjpeg rejects ("Bogus Huffman table definition", "SOF type",
"Unsupported JPEG data precision", "Unsupported color conversion") with
``imagecodecs.ljpeg_decode`` (liblj92). liblj92 walks the markers its own way (it reads a fill
byte ``FF FF`` as a marker with a length), so a crafted piece can show
``tiff_pieces.check_jpeg_piece`` a frame that fits the piece while liblj92 decodes a hidden,
far larger lossless frame: the memory planned would not hold. The bounded readers therefore
decode a JPEG piece here, through imagecodecs' libjpeg decoder called directly
(:func:`libjpeg_decoder`), with the arguments and the reshape tifffile uses; whatever libjpeg
refuses is refused, and nothing else is tried.

The decoder is chosen by imagecodecs' version:

- from 2026.11 (:data:`JPEG_DECODE_NO_FALLBACK`), ``jpeg_decode`` is libjpeg's decoder itself,
  compiled, with no fallback (``jpeg8_decode`` is its deprecated alias);
- before, ``jpeg_decode`` is a Python wrapper that falls back to ``ljpeg_decode``, and
  ``jpeg8_decode`` is libjpeg's compiled decoder.

Either way the decoder must be compiled (not a Python function, where a fallback could live);
anything else is refused by ``codec_bounds.decoder_unmeasured`` before any pixel is read.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import tifffile

JPEG_DECODE_NO_FALLBACK = (2026, 11)
"""The first imagecodecs whose ``jpeg_decode`` has no LJPEG fallback (its 2026.11.11 notes)."""
MINISBLACK, RGB, YCBCR = 1, 2, 6


def imagecodecs_version(version: str) -> tuple[int, ...]:
    """Return imagecodecs' ``__version__`` as numbers, e.g. ``(2026, 11, 11)``."""
    return tuple(int(n) for n in version.split(".")[:3] if n.isdigit())


def decoder_name(version: str) -> str:
    """Return the name of imagecodecs' libjpeg decoder without fallback for that version."""
    if imagecodecs_version(version) >= JPEG_DECODE_NO_FALLBACK:
        return "jpeg_decode"
    return "jpeg8_decode"


def libjpeg_decoder() -> Callable[..., np.ndarray] | None:
    """Return imagecodecs' compiled libjpeg decoder, or None without one (module docstring)."""
    try:
        import imagecodecs
    except ImportError:
        return None
    decoder = getattr(imagecodecs, decoder_name(imagecodecs.__version__), None)
    if decoder is None or inspect.isfunction(decoder) or not callable(decoder):
        return None  # a Python function may wrap libjpeg with a fallback
    return decoder


def colorspaces(keyframe: tifffile.TiffPage, jfif: bool) -> tuple[int | None, int | None]:
    """Return the JPEG and output colour spaces tifffile decodes a measured page with.

    As ``tifffile.jpeg_decode_colorspace`` for the forms ``codec_bounds.jpeg_unmeasured``
    lets through (no extra samples, samples interleaved): YCbCr and RGB are decoded to RGB, an
    RGB stream without a JFIF marker (Aperio SVS) declared RGB; greyscale left as it is.
    ``jfif``: the page's first piece carries a JFIF marker (``TiffPage.is_jfif``).
    """
    photometric = int(keyframe.photometric)
    if photometric == YCBCR:
        return None, RGB
    if photometric == RGB:
        return (None if jfif else RGB), RGB
    return None, None


def decode_jpeg_piece(
    keyframe: tifffile.TiffPage,
    data: bytes,
    place: tuple[tuple[int, ...], tuple[int, ...]],
    tables: bytes | None,
    jfif: bool,
) -> np.ndarray:
    """Decode one strip or tile of a page with libjpeg alone, as (depth, rows, cols, samples).

    The same call and reshape as tifffile's own JPEG decoder (``TiffPage.decode``), without
    its fallback. ``place`` is where the piece goes in the page and its shape, as tifffile's
    decoder gives them for no data (``decode(None, index)[1:]``). Raises ValueError for a
    stream libjpeg refuses or a piece that does not fit.
    """
    decoder = libjpeg_decoder()
    if decoder is None:  # codec_bounds.decoder_unmeasured refuses this before any read
        raise ValueError("imagecodecs' compiled libjpeg decoder is not available")
    where, shape = place
    colorspace, outcolorspace = colorspaces(keyframe, jfif)
    try:
        array = decoder(
            data,
            tables=tables,
            colorspace=colorspace,
            outcolorspace=outcolorspace,
            shape=shape[1:3],
        )
    except Exception as exc:  # imagecodecs.JpegError (Jpeg8Error before 2026.11) and others
        raise ValueError(f"libjpeg cannot decode it ({exc})") from exc
    if keyframe.is_tiled:
        return _tile(keyframe, np.asarray(array), where, shape)
    return _strip(np.asarray(array), shape)


def _tile(
    keyframe: tifffile.TiffPage, data: np.ndarray, where: tuple[int, ...], shape: tuple[int, ...]
) -> np.ndarray:
    """Reshape a decoded tile as tifffile does: whole, or the part inside the page."""
    _, depth, length, width, samples = (int(n) for n in keyframe.shaped)
    if data.size == math.prod(shape):
        return data.reshape(shape)
    clipped = (min(depth - where[1], shape[0]), min(length - where[2], shape[1]))
    for columns in (min(width - where[3], shape[2]), shape[2]):
        try:  # the part of an edge tile inside the page, then whole rows of it
            return data.reshape((*clipped, columns, samples))
        except ValueError:
            pass
    raise ValueError(f"its decoded tile {data.shape} does not fit the tile {tuple(shape)}")


def _strip(data: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Reshape a decoded strip as tifffile does: whole, or its first rows."""
    if data.size == math.prod(shape):
        return data.reshape(shape)
    try:  # more rows than the strip holds (the last strip coded as a whole one)
        rows = data.reshape((shape[0], -1, shape[2], shape[3]))
        return rows[:, : shape[1]].reshape(shape)
    except ValueError:
        pass
    raise ValueError(f"its decoded strip {data.shape} does not fit the strip {tuple(shape)}")
