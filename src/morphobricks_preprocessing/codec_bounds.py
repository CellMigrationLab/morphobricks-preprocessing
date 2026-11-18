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
test asserts. The predictor measured is the horizontal one (2), with deflate only; the
floating-point predictor (3) needs imagecodecs to encode and is not in the matrix.

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

import tifffile

from .plane_errors import CompressionNotBoundedError

TIFF_BOUNDED: dict[tuple[int, int], str] = {
    (1, 1): "uncompressed",
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
    "Save the image again as a TIFF that is uncompressed or compressed with deflate (zlib) or "
    "LZMA (tiled, for a large image), or as OME-Zarr."
)
"""What to do with a refused image, named in every refusal."""


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


def refusal_text(
    path: str | os.PathLike, codec: tuple[int, int], reading: str, also: str = ""
) -> str:
    """Word the refusal of an image whose codec has no measured bound, read as ``reading``.

    ``also`` adds another way out after the fix (a sentence, or nothing).
    """
    return (
        f"{Path(path).name} cannot be read {reading}: it is stored with "
        f"{describe(*codec)}, whose decoding memory has not been measured, so the memory of "
        "the read cannot be planned before any pixel is read. Only uncompressed, deflate "
        "(zlib, also with the horizontal predictor) and LZMA TIFF images are read this way "
        f"(code compression_not_bounded). {FIX}{also}"
    )


def check_tiff_codec(
    pages: Iterable[tifffile.TiffPage | tifffile.TiffFrame],
    path: str | os.PathLike,
    reading: str,
    error: type[CompressionNotBoundedError] = CompressionNotBoundedError,
) -> None:
    """Refuse, from the headers only, pages stored with a codec not in :data:`TIFF_BOUNDED`.

    Raises
    ------
    CompressionNotBoundedError
        (or ``error``) naming the codec and the fix, before any pixel is read.
    """
    for page in pages:
        codec = tiff_codec(page)
        if not is_bounded(*codec):
            raise error(refusal_text(path, codec, reading))
