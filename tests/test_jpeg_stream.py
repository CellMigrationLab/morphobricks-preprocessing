"""A JPEG piece's frame is the one libjpeg reads, checked before it is decoded (#356, review B1).

libjpeg sizes what it allocates from the stream's frame header (SOF). The review found a piece
that passed a check reading markers unlike libjpeg: one ``FF`` fill byte before a SOF of
40000 × 40000 made the check skip it and accept a 16 × 16 SOF placed after the EOI, and
libjpeg allocated 4.5 GiB. ``jpeg_stream`` walks the markers as libjpeg does (fill bytes,
``FF 00``, lone markers, lengths, scans) and refuses whatever libjpeg could read otherwise;
every bounded reader (windows, blocks, every channel of a plane) refuses such a piece before
libjpeg sees it.
"""

import numpy as np
import pytest
import tifffile

from morphobricks_preprocessing import iter_planes, plane_layout, prefill_mapping
from morphobricks_preprocessing.jpeg_stream import JpegStreamError, check_tables, frame
from morphobricks_preprocessing.plane_channels import read_xyc_plane
from morphobricks_preprocessing.plane_errors import PlaneReadError
from morphobricks_preprocessing.windows import read_window

imagecodecs = pytest.importorskip("imagecodecs")

SIDE = 16
BIG = 40000


def _stream(side=SIDE, seed=0):
    pixels = np.random.default_rng(seed).integers(0, 256, (side, side), dtype=np.uint8)
    return bytes(imagecodecs.jpeg8_encode(pixels, level=90))


def _sof(stream):
    """Return where the SOF0 marker of an encoded stream starts."""
    return stream.index(b"\xff\xc0")


def _sized(stream, rows, columns):
    """Return ``stream`` with its frame header declaring ``rows`` × ``columns``."""
    at = _sof(stream) + 5  # FF C0, length (2), precision (1)
    size = rows.to_bytes(2, "big") + columns.to_bytes(2, "big")
    return stream[:at] + size + stream[at + 4 :]


def _review_piece(fake_after_eoi):
    """The review's piece: a 40000² frame behind one fill byte (and a 16² SOF after EOI)."""
    stream = _sized(_stream(), BIG, BIG)
    at = _sof(stream)
    piece = stream[:at] + b"\xff" + stream[at:]
    if fake_after_eoi:
        piece += _stream()[_sof(_stream()) : _sof(_stream()) + 13]
    return piece


@pytest.mark.parametrize("fake_after_eoi", [False, True], ids=["fill byte", "fake SOF after EOI"])
def test_the_frame_is_the_one_libjpeg_reads(fake_after_eoi):
    found = frame(_review_piece(fake_after_eoi))
    assert (found.rows, found.columns) == (BIG, BIG)


def _tiff(path, pieces):
    tifffile.imwrite(
        path,
        iter(pieces),
        shape=(2 * SIDE, 2 * SIDE),
        dtype=np.uint8,
        tile=(SIDE, SIDE),
        compression=7,
        photometric="minisblack",
    )
    return path


@pytest.mark.parametrize("fake_after_eoi", [False, True], ids=["fill byte", "fake SOF after EOI"])
def test_every_bounded_reader_refuses_the_review_piece_before_libjpeg(
    tmp_path, monkeypatch, fake_after_eoi
):
    pieces = [_stream(seed=i) for i in range(3)] + [_review_piece(fake_after_eoi)]
    path = _tiff(tmp_path / "a.tif", pieces)
    mapping = prefill_mapping("YX")
    real = tifffile.TiffPage.__dict__["decode"].func  # a cached property: the decoder

    def checked(page):
        decoder = real(page)

        def decode(data, index, **kwargs):
            # what libjpeg is given: never the oversized piece
            assert data is None or frame(bytes(data)).rows <= SIDE, "libjpeg got it"
            return decoder(data, index, **kwargs)

        return decode

    monkeypatch.setattr(tifffile.TiffPage, "decode", property(checked))
    match = rf"declares 8-bit samples, {BIG} × {BIG} pixels"
    with pytest.raises(PlaneReadError, match=match):
        read_window(path, mapping, bounds=(SIDE, 2 * SIDE, SIDE, 2 * SIDE), channels=[0])
    with pytest.raises(PlaneReadError, match=match):
        read_xyc_plane(path, mapping)
    limit = plane_layout(path, mapping, memory_limit=1).block_memory_estimate
    with pytest.raises(PlaneReadError, match=match):
        for plane in iter_planes(path, mapping, memory_limit=limit):
            for _ in plane.blocks():
                pass
    # the good pieces are read: a window of them reads as tifffile reads it
    good = read_window(path, mapping, bounds=(0, SIDE, 0, 2 * SIDE), channels=[0])
    monkeypatch.undo()
    reference = _tiff(tmp_path / "good.tif", [_stream(seed=i) for i in range(4)])
    np.testing.assert_array_equal(good[0], tifffile.imread(reference)[:SIDE])


REFUSED = {
    "FF 00 before a marker": lambda s: s[:2] + b"\xff\x00" + s[2:],
    "garbage before a marker": lambda s: s[:2] + b"\x12" + s[2:],
    "a second frame header": lambda s: s[: _sof(s)] + s[_sof(s) : _sof(s) + 13] + s[_sof(s) :],
    "no SOI": lambda s: s[2:],
    "a scan before the frame": lambda s: s[:2] + s[s.index(b"\xff\xda") :],
    "SOI again": lambda s: s[:2] + b"\xff\xd8" + s[2:],
    "a restart in the header": lambda s: s[:2] + b"\xff\xd0" + s[2:],
    "a lossless frame": lambda s: s.replace(b"\xff\xc0", b"\xff\xc3", 1),
    "an arithmetic frame": lambda s: s.replace(b"\xff\xc0", b"\xff\xc9", 1),
    "a DNL": lambda s: s[:2] + b"\xff\xdc\x00\x04\x00\x10" + s[2:],
    "a length past the end": lambda s: s[:2] + b"\xff\xe1\xff\xff",
    "a frame header of the wrong length": lambda s: s.replace(
        b"\xff\xc0\x00\x0b", b"\xff\xc0\x00\x0c", 1
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_a_stream_libjpeg_could_read_otherwise_is_refused(case):
    with pytest.raises(JpegStreamError):
        frame(REFUSED[case](_stream()))


def test_fill_bytes_stuffing_and_restarts_are_read_as_libjpeg_reads_them():
    pixels = np.random.default_rng(3).integers(0, 256, (64, 64), dtype=np.uint8)
    stream = bytes(imagecodecs.jpeg8_encode(pixels, level=95))
    assert b"\xff\x00" in stream[stream.index(b"\xff\xda") :]  # stuffed bytes in the scan
    padded = stream[:2] + b"\xff\xff\xff" + stream[2:]  # fill bytes before a marker
    for each in (stream, padded):
        found = frame(each)
        assert (found.precision, found.rows, found.columns, found.components) == (8, 64, 64, 1)
        np.testing.assert_array_equal(
            imagecodecs.jpeg_decode(each), imagecodecs.jpeg_decode(stream)
        )


def test_tables_hold_only_tables():
    stream = _stream()
    tables = stream[: stream.index(b"\xff\xc0")] + b"\xff\xd9"
    check_tables(tables)
    with pytest.raises(JpegStreamError):
        check_tables(tables[:-2] + stream[_sof(stream) : _sof(stream) + 13] + b"\xff\xd9")


def test_random_prefixes_never_hide_a_frame_from_the_check():
    # Bytes libjpeg handles specially, inserted before a decoy 2048² frame and the real 16²
    # one: whenever the check accepts a stream, libjpeg decodes exactly the frame it found.
    rng = np.random.default_rng(356)
    stream = _stream()
    sof = _sof(stream)
    decoy = _sized(stream, 2048, 2048)[sof : sof + 13]
    alphabet = [b"\xff", b"\x00", b"\xd8", b"\xd9", b"\xc0", b"\xd0", b"\x01", b"\x12", decoy]
    accepted = 0
    for _ in range(400):
        noise = b"".join(alphabet[i] for i in rng.integers(0, len(alphabet), rng.integers(0, 6)))
        at = int(rng.integers(2, sof + 1))
        candidate = stream[:at] + noise + stream[at:]
        try:
            found = frame(candidate)
        except JpegStreamError:
            continue
        accepted += 1
        try:
            decoded = imagecodecs.jpeg_decode(candidate)
        except Exception:  # noqa: BLE001  (libjpeg refusing a stream is fine)
            continue
        assert decoded.shape[:2] == (found.rows, found.columns), (noise, at)
    assert accepted > 0
