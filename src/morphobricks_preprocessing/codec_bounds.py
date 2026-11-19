"""The codecs whose read memory is measured: the only ones the bounded readers decode (#356).

Owner decision of 18 November 2026 (issue #356, P2): a reader that plans its working memory
before any pixel is read, from stated upper bounds, decodes only codecs for which that bound
is **measured**. No bound is invented for any other codec. The bounded readers are:

- window reads (:mod:`.windows`, :mod:`.window_reader`, :mod:`.window_map`, the mask windows of
  :mod:`.masks.window`), and their plans;
- every channel of a plane at once (:mod:`.plane_channels`, ``RunInputs.read_plane``);
- planes over the memory limit read in blocks (:mod:`.planes`: ``iter_planes``,
  ``iter_xyc_planes``, ``read_plane``'s reduced preview and ``check_planes``).

A TIFF stored with a codec not in :data:`TIFF_BOUNDED` is refused there when the read is
planned, from the header, before any pixel is read
(:class:`.plane_errors.CompressionNotBoundedError`, code ``compression_not_bounded``), with
the fix named: save the image again uncompressed or with a listed codec, or as OME-Zarr.

**Measured evidence.** :data:`TIFF_BOUNDED` lists the (compression, predictor) pairs whose
peak is measured in a fresh process against the estimate: ``tests/test_window_memory.py``
(windows: ``uint8``, ``uint16``, ``float32`` in tiles and strips, interleaved RGB, OME stacks,
uncached and cached) and ``tests/test_plane_channels.py`` (every channel of a plane, peak
``tracemalloc`` and RSS). TIFF code 32946 (``DEFLATE``) is decoded by the very function that
decodes code 8 (``ADOBE_DEFLATE``, what tifffile writes for ``compression="zlib"``), which a
test asserts. The horizontal predictor (2) is measured with deflate and LZW; the
floating-point predictor (3) is not in the matrix.

LZW and JPEG are decoded by imagecodecs, in C: ``tests/test_codec_memory.py`` measures their
reads' peak resident memory (RSS) in a fresh process, and libjpeg's and LZW's own memory alone
as the peak of the C heap during a decode (``tests/codec_peaks.py``), against the terms of
:mod:`.window_memory`: windows (uncached and cached), every channel of a plane with one and with
several decoding threads, and blocks; ``uint8`` and ``uint16`` LZW with and without the
horizontal predictor; 8-bit JPEG in tiles of 256 and 512 and in strips, greyscale, RGB and
YCbCr 4:2:0 and 4:2:2, with the JPEG tables in the TIFF header, baseline and progressive,
compressible and noisy; on Python 3.13 with imagecodecs 2026.11 and on Python 3.11 with
2026.3 (whose LZW table is larger). A JPEG page is read only in that measured form
(:func:`jpeg_unmeasured`): 8-bit samples, one (greyscale) or three (RGB or YCbCr) interleaved
samples, subsampled at most 2 × 2, and no NDPI header. Old-style JPEG (code 6) is not measured.

A JPEG piece is decoded by libjpeg alone (:mod:`.jpeg_piece`; issue #356, finding F1 of the
second review): imagecodecs' compiled libjpeg decoder is called directly (``jpeg_decode`` from
imagecodecs 2026.11, ``jpeg8_decode`` before), with tifffile's arguments and reshape, never
through tifffile's ``TiffPage.decode`` (tifffile 2026.11) or imagecodecs' ``jpeg_decode``
before 2026.11: both retry a stream libjpeg refuses with ``ljpeg_decode`` (liblj92), which
reads the markers otherwise and can decode a hidden frame far larger than the one checked
(``tiff_pieces.check_jpeg_piece``). A stream libjpeg refuses is refused (``PlaneReadError``);
LJPEG is never measured and never decoded here. :func:`decoder_unmeasured` refuses an
imagecodecs whose libjpeg decoder is not compiled (a Python wrapper may fall back).

:data:`ZARR_MEASURED` lists the OME-Zarr codecs the same tests measure: the default
compressors of the zarr versions the tests run under (``tests/zarr_tree.write_ngff``). It is
the record of the evidence; OME-Zarr reads are not refused by codec here.

Not covered: whole planes read within the memory limit (``planes.read_plane`` of one plane,
``iter_planes`` and the run's histograms, the conversion), whose estimate (``planes._layout``)
is not stated as an upper bound for any codec (it counts no codec state or decoding threads),
so the owner's rule does not apply to it and nothing changes there.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

import tifffile

from . import jpeg_piece
from .plane_errors import CompressionNotBoundedError

TIFF_BOUNDED: dict[tuple[int, int], str] = {
    (1, 1): "uncompressed",
    (5, 1): "LZW (imagecodecs)",
    (5, 2): "LZW (imagecodecs) with the horizontal predictor",
    (7, 1): "JPEG (libjpeg-turbo through imagecodecs), 8-bit, in the form jpeg_unmeasured names",
    (8, 1): "deflate (zlib, TIFF code 8)",
    (8, 2): "deflate (zlib, TIFF code 8) with the horizontal predictor",
    (32946, 1): "deflate (TIFF code 32946, the same decoder as code 8)",
    (32946, 2): "deflate (TIFF code 32946) with the horizontal predictor",
    (34925, 1): "LZMA (presets 0 to 9)",
}
"""(TIFF compression, predictor) pairs whose decoding memory is measured (module docstring)."""

ZARR_MEASURED: dict[str, str] = {
    "blosc lz4": (
        "Blosc with LZ4 (clevel 5, byte shuffle): the default compressor of a zarr format 2 "
        "array (OME-NGFF 0.4), under zarr 2.18 and zarr 3"
    ),
    "zstd": (
        "bytes then Zstandard (level 0): the default codecs of a zarr format 3 array "
        "(OME-NGFF 0.5), under zarr 3"
    ),
}
"""OME-Zarr codecs measured by the same tests (the record of the evidence; not enforced)."""

FIX = (
    "Save the image again as a TIFF that is uncompressed or compressed with deflate (zlib), LZW "
    "or LZMA (tiled, for a large image), or as OME-Zarr."
)
"""What to do with a refused image, named in every refusal."""
READ = (
    "Only uncompressed, deflate (zlib) and LZW (also with the horizontal predictor), LZMA and "
    "8-bit greyscale, RGB or YCbCr JPEG TIFF images are read this way"
)
"""Which TIFF images the bounded readers read, named in every refusal."""
MINISBLACK, RGB, YCBCR = 1, 2, 6
"""The photometric interpretations of the JPEG pages measured."""
JPEG_SUBSAMPLING = {None, (1, 1), (2, 1), (2, 2)}
"""The YCbCr subsamplings of the JPEG pages measured (None: not subsampled)."""
IMAGECODECS_MEASURED = (2024, 12, 30)
"""The oldest imagecodecs whose decoders are measured (``tests/test_codec_memory.py``)."""
LIBJPEG_MEASURED = "libjpeg_turbo 3."
"""The libjpeg imagecodecs must be built with (``imagecodecs.jpeg8_version()``)."""
DECODERS = {5: ("lzw_decode",), 34925: ("lzma_decode",)}
DECODERS |= dict.fromkeys((8, 32946), ("deflate_decode", "zlib_decode"))
"""The imagecodecs functions tifffile must decode each compressed codec with: the measured
decoders. imagecodecs is a required dependency; without it tifffile would decode deflate and
LZMA with the standard library's ``zlib`` and ``lzma`` (and not LZW or JPEG at all). JPEG (7)
is not decoded by tifffile in the bounded readers: its pieces go to imagecodecs' libjpeg
decoder directly (:func:`.jpeg_piece.libjpeg_decoder`), never to tifffile's ``TiffPage.decode``
or imagecodecs' ``jpeg_decode`` before 2026.11, which both fall back to ``ljpeg_decode``."""


def tiff_codec(page: tifffile.TiffPage | tifffile.TiffFrame) -> tuple[int, int]:
    """Return a page's ``(compression, predictor)``, from its header (frames: their keyframe)."""
    return int(page.keyframe.compression), int(page.keyframe.predictor)


def is_bounded(compression: int, predictor: int) -> bool:
    """Return whether a TIFF codec's decoding memory is measured (:data:`TIFF_BOUNDED`)."""
    return (int(compression), int(predictor)) in TIFF_BOUNDED


def describe(compression: int, predictor: int) -> str:
    """Name a TIFF codec for a message, e.g. ``"LZW compression (TIFF code 5)"``."""
    try:
        name = tifffile.COMPRESSION(compression).name
    except ValueError:
        name = "an unknown"
    text = f"{name} compression (TIFF code {compression})"
    if predictor != 1:
        try:
            predicted = tifffile.PREDICTOR(predictor).name
        except ValueError:
            predicted = "an unknown"
        text += f" with the {predicted} predictor ({predictor})"
    return text


def jpeg_unmeasured(page: tifffile.TiffPage | tifffile.TiffFrame) -> str | None:
    """Return how a JPEG page differs from the JPEG pages measured, or None if it does not."""
    key = page.keyframe
    samples, photometric = int(key.samplesperpixel), int(key.photometric)
    if key.jpegheader is not None:
        return "with an NDPI JPEG header"
    if int(key.bitspersample) != 8:
        return f"of {int(key.bitspersample)}-bit samples"
    if (samples, photometric) not in {(1, MINISBLACK), (3, RGB), (3, YCBCR)}:
        try:
            space = tifffile.PHOTOMETRIC(photometric).name
        except ValueError:
            space = f"photometric {photometric}"
        return f"of {samples} sample(s) per pixel in {space}"
    if samples > 1 and int(key.planarconfig) != 1:
        return "with its colour samples stored in separate planes"
    subsampling = key.subsampling if photometric == YCBCR else None
    if subsampling not in JPEG_SUBSAMPLING:
        return f"with YCbCr subsampling {subsampling}"
    return None


def unbounded(page: tifffile.TiffPage | tifffile.TiffFrame) -> str | None:
    """Return the description of a page's codec when its memory is not measured, else None."""
    codec = tiff_codec(page)
    if not is_bounded(*codec):
        return describe(*codec)
    if codec[0] == 7 and (unmeasured := jpeg_unmeasured(page)) is not None:
        return f"{describe(*codec)} {unmeasured}"
    if (decoder := decoder_unmeasured(codec[0])) is not None:
        return f"{describe(*codec)} {decoder}"
    return None


def decoder_unmeasured(compression: int) -> str | None:
    """Return how the decoder tifffile would use differs from the measured one, or None.

    A compressed codec is measured as decoded by imagecodecs (:data:`IMAGECODECS_MEASURED` or
    later), through the very functions of :data:`DECODERS`; JPEG by imagecodecs' compiled
    libjpeg decoder alone, built on libjpeg-turbo 3 (:data:`LIBJPEG_MEASURED`), with no
    fallback to another decoder (:func:`_jpeg_decoder_unmeasured`). Anything else is refused,
    never decoded by another library instead.
    """
    if compression not in DECODERS and compression != 7:
        return None  # uncompressed: nothing decodes it
    need = "imagecodecs 2024.12.30 or later, a required dependency: install or upgrade it"
    try:
        import imagecodecs
    except ImportError:
        return f"without imagecodecs installed (decoded only by {need})"
    found = jpeg_piece.imagecodecs_version(imagecodecs.__version__)
    if found < IMAGECODECS_MEASURED:
        return f"with imagecodecs {imagecodecs.__version__} (decoded only by {need})"
    if compression == 7:
        return _jpeg_decoder_unmeasured(imagecodecs)
    decoder = tifffile.TIFF.DECOMPRESSORS[compression]
    if all(decoder is not getattr(imagecodecs, name, None) for name in DECODERS[compression]):
        name = f"{getattr(decoder, '__module__', '?')}.{getattr(decoder, '__name__', '?')}"
        return f"decoded by {name} (decoded only by {need})"
    return None


def _jpeg_decoder_unmeasured(imagecodecs: ModuleType) -> str | None:
    """Return how imagecodecs' libjpeg decoder differs from the measured one, or None.

    JPEG pieces are decoded by libjpeg-turbo 3 alone (:mod:`.jpeg_piece`): a build on another
    libjpeg, or a ``jpeg_decode``/``jpeg8_decode`` that is a Python function (which may fall
    back to ``ljpeg_decode``, as imagecodecs' ``jpeg_decode`` did before 2026.11), is refused.
    """
    built = imagecodecs.jpeg8_version()
    if not built.startswith(LIBJPEG_MEASURED):
        return f"with imagecodecs built on {built} (decoded only with libjpeg-turbo 3)"
    if jpeg_piece.libjpeg_decoder() is None:
        name = jpeg_piece.decoder_name(imagecodecs.__version__)
        return (
            f"with imagecodecs {imagecodecs.__version__}, whose {name} is not libjpeg's "
            "compiled decoder (decoded only by libjpeg-turbo 3, never with another decoder "
            "as a fallback)"
        )
    return None


def refusal_text(path: str | os.PathLike, codec: str, reading: str, also: str = "") -> str:
    """Word the refusal of an image stored with ``codec`` (:func:`unbounded`), read as ``reading``.

    ``also`` adds another way out after the fix (a sentence, or nothing).
    """
    return (
        f"{Path(path).name} cannot be read {reading}: it is stored with {codec}, whose decoding "
        "memory has not been measured, so the memory of the read cannot be planned before any "
        f"pixel is read. {READ} (code compression_not_bounded). {FIX}{also}"
    )


def check_tiff_codec(
    pages: Iterable[tifffile.TiffPage | tifffile.TiffFrame],
    path: str | os.PathLike,
    reading: str,
    error: type[CompressionNotBoundedError] = CompressionNotBoundedError,
) -> None:
    """Refuse, from the headers only, pages stored with a codec whose memory is not measured.

    Raises
    ------
    CompressionNotBoundedError
        (or ``error``) naming the codec and the fix, before any pixel is read.
    """
    for page in pages:
        codec = unbounded(page)
        if codec is not None:
            raise error(refusal_text(path, codec, reading))
