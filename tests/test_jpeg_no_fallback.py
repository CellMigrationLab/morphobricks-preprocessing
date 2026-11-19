"""A JPEG piece is decoded by libjpeg alone, never by LJPEG as a fallback (#356, F1).

Second review of #357, finding F1: tifffile 2026.11 (``TiffPage.decode``) and, before
imagecodecs 2026.11, imagecodecs' own ``jpeg_decode`` retry a stream libjpeg refuses with
``imagecodecs.ljpeg_decode`` (liblj92), which walks the markers otherwise. A crafted tile
(``codec_images.ljpeg_bypass_piece``) shows ``check_jpeg_piece`` a frame that fits its tile
while liblj92 decodes a hidden, far larger lossless frame: at ``a6bc438`` an 8.4 MB tile peaked
at 136.8 MiB against a window plan of 8.8 MiB (15.5×), and every channel of a plane and blocks
overran too. The bounded readers now decode a JPEG piece with imagecodecs' compiled libjpeg
decoder directly (``jpeg_piece``) and refuse what libjpeg refuses: ``ljpeg_decode`` is never
called on any path (a spy), and the piece is refused with libjpeg's own reason.
"""

import inspect
import re

import numpy as np
import pytest
import tifffile

from codec_images import imagecodecs, ljpeg_bypass_piece, write_ljpeg_bypass
from morphobricks_preprocessing import (
    iter_planes,
    jpeg_piece,
    jpeg_stream,
    plane_layout,
    prefill_mapping,
    read_plane,
)
from morphobricks_preprocessing.plane_channels import read_xyc_plane
from morphobricks_preprocessing.plane_errors import PlaneReadError
from morphobricks_preprocessing.window_reader import WindowReader
from morphobricks_preprocessing.windows import read_window

TILE, SIDE = 256, 2048  # the tile libjpeg's frame fits; liblj92's hidden frame, 64 × larger
LAST = (TILE, 2 * TILE, TILE, 2 * TILE)  # the crafted tile, bottom right
REFUSED = r"Part 3 of a page of .* libjpeg cannot decode it \(Bogus Huffman table definition\)"


def _smallest(path, mapping):
    return plane_layout(path, mapping, memory_limit=1).block_memory_estimate


def _blocks(path, mapping):
    for plane in iter_planes(path, mapping, memory_limit=_smallest(path, mapping)):
        assert plane.in_blocks
        for _ in plane.blocks():
            pass


READS = {  # every bounded path, and a whole plane read within the limit
    "window": lambda p, m: read_window(p, m, bounds=LAST, channels=[0]),
    "cached window": lambda p, m: WindowReader(p, m, cache_bytes=2**24).read_window(
        bounds=LAST, channels=[0]
    ),
    "every channel of a plane": read_xyc_plane,
    "blocks": _blocks,
    "reduced preview": lambda p, m: read_plane(p, m, memory_limit=_smallest(p, m), preview=True),
    "whole plane": lambda p, m: list(iter_planes(p, m)),
}


@pytest.fixture
def ljpeg_calls(monkeypatch):
    """Record every call of ``imagecodecs.ljpeg_decode`` (and refuse it: no memory spent)."""
    calls = []

    def spy(data, *args, **kwargs):
        calls.append(len(data))
        raise RuntimeError("ljpeg_decode was called")

    monkeypatch.setattr(imagecodecs, "ljpeg_decode", spy)
    return calls


def test_the_crafted_piece_hides_a_larger_lossless_frame():
    # what the check sees and what liblj92 decodes differ: the mismatch the fix must not reach
    stream = ljpeg_bypass_piece(SIDE, TILE)
    found = jpeg_stream.frame(stream)
    assert (found.precision, found.rows, found.columns, found.components) == (8, TILE, TILE, 1)
    hidden = imagecodecs.ljpeg_decode(stream)
    assert hidden.shape == (SIDE, SIDE) and hidden.dtype == np.uint16
    assert hidden.size == 64 * TILE * TILE  # 64 times the pixels of the frame checked
    assert hidden.nbytes > 10 * len(stream)  # 8 MiB from a piece of about 590 KB


def test_the_decoder_is_libjpeg_compiled_with_no_fallback():
    decoder = jpeg_piece.libjpeg_decoder()
    version = jpeg_piece.imagecodecs_version(imagecodecs.__version__)
    name = "jpeg_decode" if version >= (2026, 11) else "jpeg8_decode"
    assert jpeg_piece.decoder_name(imagecodecs.__version__) == name
    assert decoder is getattr(imagecodecs, name)
    assert not inspect.isfunction(decoder)  # compiled: not a Python wrapper with a fallback
    with pytest.raises(Exception, match="Bogus Huffman table definition"):
        decoder(ljpeg_bypass_piece(SIDE, TILE))  # refused, even with ljpeg_decode present


def test_tifffile_alone_would_fall_back_on_this_stack(tmp_path, ljpeg_calls):
    # why the bounded readers do not decode through tifffile: on the stacks tested, tifffile
    # 2026.11 or imagecodecs' jpeg_decode before 2026.11 retries the stream with liblj92
    path = write_ljpeg_bypass(tmp_path / "f1.tif", SIDE, TILE)
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        fh = tif.filehandle
        fh.seek(page.dataoffsets[3])
        data = fh.read(page.databytecounts[3])
        with pytest.raises(Exception, match="ljpeg_decode was called|Bogus Huffman"):
            page.decode(data, 3, jpegtables=page.jpegtables)
    if not ljpeg_calls:
        pytest.skip("neither tifffile nor imagecodecs falls back to ljpeg_decode here")
    assert ljpeg_calls == [len(data)]


@pytest.mark.parametrize("read", sorted(READS))
def test_the_crafted_piece_is_refused_with_no_fallback_decode(tmp_path, read, ljpeg_calls):
    path = write_ljpeg_bypass(tmp_path / "f1.tif", SIDE, TILE)
    with pytest.raises(PlaneReadError) as refused:
        READS[read](path, prefill_mapping("YX"))
    assert ljpeg_calls == []  # liblj92 never saw the piece (a6bc438: called once, every path)
    assert re.search(REFUSED, str(refused.value))  # refused with libjpeg's own reason


def test_the_valid_tiles_beside_it_still_read(tmp_path):
    # the crafted piece alone is refused: a window of the valid tiles reads as tifffile does
    path = write_ljpeg_bypass(tmp_path / "f1.tif", SIDE, TILE)
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        fh = tif.filehandle
        fh.seek(page.dataoffsets[0])
        expected = imagecodecs.jpeg8_decode(fh.read(page.databytecounts[0]))
    window = read_window(path, prefill_mapping("YX"), bounds=(0, TILE, 0, TILE), channels=[0])
    np.testing.assert_array_equal(window[0], expected)
