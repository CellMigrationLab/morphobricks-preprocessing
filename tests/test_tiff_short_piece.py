"""A compressed TIFF piece shorter than its byte count is refused (#377)."""

import struct

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import prefill_mapping
from morphobricks_preprocessing.plane_channels import read_xyc_plane
from morphobricks_preprocessing.planes import PlaneReadError, read_plane
from morphobricks_preprocessing.windows import read_window


def _inflate_byte_count(path, extra):
    """Rewrite the first strip's byte count to ``extra`` bytes past the end of the file."""
    with tifffile.TiffFile(path) as tif:
        tag = tif.pages[0].tags["StripByteCounts"]
        assert tag.count == 1  # stored in the IFD entry itself
        where, dtype, (count,) = tag.valueoffset, tag.dtype, tag.value
        order = tif.byteorder
    fmt = order + {3: "H", 4: "I", 16: "Q"}[int(dtype)]
    with open(path, "r+b") as fh:
        fh.seek(where)
        fh.write(struct.pack(fmt, count + extra))


def test_a_compressed_piece_past_the_end_of_the_file_is_refused(tmp_path):
    data = np.arange(100, dtype="uint16").reshape(10, 10)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, compression="zlib", rowsperstrip=10)
    mapping = prefill_mapping("YX")
    window = read_window(path, mapping, bounds=(0, 10, 0, 10), channels=[0])
    np.testing.assert_array_equal(window[0], data)  # the valid file reads as before
    _inflate_byte_count(path, 1024)
    with pytest.raises(PlaneReadError, match=r"Part 0 .* is truncated: \d+ of \d+ bytes"):
        read_window(path, mapping, bounds=(0, 10, 0, 10), channels=[0])


def test_every_read_path_refuses_a_piece_past_the_end_of_the_file(tmp_path):
    """Whole-page reads (planes, every channel of a plane) check the header too, not only
    the windows (#377 review): offset + byte count must lie within the file."""
    data = np.arange(100, dtype="uint16").reshape(10, 10)
    path = tmp_path / "a.tif"
    tifffile.imwrite(path, data, compression="zlib", rowsperstrip=10)
    mapping = prefill_mapping("YX")
    np.testing.assert_array_equal(read_plane(path, mapping).pixels, data)
    np.testing.assert_array_equal(read_xyc_plane(path, mapping)[0], data)
    _inflate_byte_count(path, 1024)
    match = r"Part 0 of a page of .* is truncated: \d+ of \d+ bytes"
    with pytest.raises(PlaneReadError, match=match):
        read_window(path, mapping, bounds=(0, 10, 0, 10), channels=[0])
    with pytest.raises(PlaneReadError, match=match):
        read_plane(path, mapping)
    with pytest.raises(PlaneReadError, match=match):
        read_xyc_plane(path, mapping)
