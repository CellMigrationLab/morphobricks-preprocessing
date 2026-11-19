"""Read a JPEG stream's markers as libjpeg reads them, before any of it is decoded (#356).

libjpeg sizes the decoded array and its own buffers from the stream's frame header (SOF), not
from the TIFF header, so a JPEG strip or tile is checked before it is decoded: its frame must
fit the TIFF piece the memory was planned for (``window_memory``). The check must see the frame
libjpeg will use, so the markers are walked the way libjpeg's marker reader walks them
(``jdmarker.c``: ``first_marker``, ``next_marker``; the entropy decoder's ``FF`` handling):

- the stream starts with SOI (``FF D8``) exactly;
- a marker is one or more ``FF`` fill bytes and a code; anything else where a marker is
  expected (a byte other than ``FF``, or ``FF 00``) libjpeg skips as garbage, so it is refused
  here: what libjpeg would find after it is not guessed;
- before the first scan (SOS) only the segments a header holds are accepted: APPn, COM, DQT,
  DHT, DRI, DAC and **one** frame header of a measured type (baseline, extended or progressive
  Huffman, ``C0``-``C2``); another SOI, an EOI, a restart or any other marker is refused;
- inside a scan's entropy-coded data, ``FF 00`` is a data byte, ``FF D0``-``FF D7`` restart
  markers and ``FF`` runs fill; the next other code ends the scan;
- between scans: APPn, COM, DQT, DHT, DRI, DAC, another SOS or EOI; a second frame header (which
  libjpeg refuses only after it has allocated for the first) or anything else is refused;
- the walk stops at EOI (libjpeg reads nothing after it) or at the end of the data.

The JPEG tables of the TIFF header (``JPEGTables``), which libjpeg reads first, must hold only
tables (APPn, COM, DQT, DHT, DRI, DAC) between SOI and EOI.
"""

from __future__ import annotations

from typing import NamedTuple

SOI, EOI, SOS, DNL = 0xD8, 0xD9, 0xDA, 0xDC
FRAMES = {0xC0 + n for n in range(16)} - {0xC4, 0xC8, 0xCC}
"""Every frame header code (SOF0-SOF15; ``C4``, ``C8`` and ``CC`` are DHT, JPG and DAC)."""
MEASURED_FRAMES = {0xC0, 0xC1, 0xC2}
"""Huffman-coded DCT frames, baseline, extended and progressive: the ones measured."""
TABLES = {0xC4, 0xCC, 0xDB, 0xDD, 0xFE} | set(range(0xE0, 0xF0))
"""DHT, DAC, DQT, DRI, COM and APP0-APP15: segments a header or the gap between scans holds."""
RESTARTS = set(range(0xD0, 0xD8))


class JpegStreamError(ValueError):
    """A JPEG stream whose markers are not read the one way libjpeg reads them."""


class Frame(NamedTuple):
    """A JPEG frame header: what libjpeg allocates for."""

    code: int
    precision: int
    rows: int
    columns: int
    components: int


def check_tables(tables: bytes) -> None:
    """Refuse a JPEG tables stream holding anything but tables between SOI and EOI."""
    position = _soi(tables)
    while True:
        code, start, end = _marker(tables, position)
        if code == EOI:
            return
        if code not in TABLES:
            raise JpegStreamError(f"its JPEG tables hold the marker FF {code:02X}")
        position = end


def frame(data: bytes) -> Frame:
    """Return the one frame header libjpeg reads in ``data``, or refuse the stream.

    Raises
    ------
    JpegStreamError
        Naming what libjpeg would read differently, or would not read.
    """
    position, found, scanning = _soi(data), None, False
    while position < len(data):
        if scanning:
            position = _after_scan(data, position)
            if position >= len(data):
                break
        code, start, end = _marker(data, position)
        if code == EOI:
            break
        if code in FRAMES:
            if found is not None:
                raise JpegStreamError("its JPEG stream holds a second frame header")
            found = _frame(data, code, start, end)
        elif code == SOS:
            if found is None:
                raise JpegStreamError("its JPEG stream starts a scan before its frame header")
            scanning = True
        elif code not in TABLES:
            raise JpegStreamError(f"its JPEG stream holds the marker FF {code:02X} out of place")
        position = end
    if found is None:
        raise JpegStreamError("its JPEG stream has no frame header")
    return found


def _soi(data: bytes) -> int:
    if data[:2] != b"\xff\xd8":
        raise JpegStreamError("its JPEG stream does not start with SOI")
    return 2


def _marker(data: bytes, position: int) -> tuple[int, int, int]:
    """Return the marker at ``position`` (fill bytes skipped): code, payload start and end."""
    if position >= len(data) or data[position] != 0xFF:
        raise JpegStreamError("its JPEG stream has bytes where a marker is expected")
    while position < len(data) and data[position] == 0xFF:
        position += 1  # fill bytes, as libjpeg's next_marker skips them
    if position >= len(data):
        raise JpegStreamError("its JPEG stream ends inside a marker")
    code = data[position]
    position += 1
    if code == 0x00:
        raise JpegStreamError("its JPEG stream has FF 00 where a marker is expected")
    if code in RESTARTS or code in (SOI, EOI, 0x01):
        if code != EOI:  # a lone marker outside a scan: libjpeg skips it; never needed here
            raise JpegStreamError(f"its JPEG stream holds the marker FF {code:02X} out of place")
        return code, position, position
    if position + 2 > len(data):
        raise JpegStreamError("its JPEG stream ends inside a marker")
    length = int.from_bytes(data[position : position + 2], "big")
    if length < 2 or position + length > len(data):
        raise JpegStreamError(f"its JPEG stream has a marker FF {code:02X} of length {length}")
    return code, position + 2, position + length


def _frame(data: bytes, code: int, start: int, end: int) -> Frame:
    """Return the frame header whose payload is ``data[start:end]``, if of a measured type."""
    if code not in MEASURED_FRAMES:
        raise JpegStreamError(f"its JPEG frame (FF {code:02X}) is not Huffman-coded DCT")
    if end - start < 6:
        raise JpegStreamError("its JPEG frame header is too short")
    found = Frame(
        code,
        data[start],
        int.from_bytes(data[start + 1 : start + 3], "big"),
        int.from_bytes(data[start + 3 : start + 5], "big"),
        data[start + 5],
    )
    if end - start != 6 + 3 * found.components:
        raise JpegStreamError("its JPEG frame header's length does not match its components")
    return found


def _after_scan(data: bytes, position: int) -> int:
    """Return where the marker ending the entropy-coded data at ``position`` starts."""
    while True:
        position = data.find(b"\xff", position)
        if position < 0:
            return len(data)
        following = position + 1
        while following < len(data) and data[following] == 0xFF:
            following += 1  # fill bytes
        if following >= len(data):
            return len(data)
        code = data[following]
        if code == 0x00 or code in RESTARTS:
            position = following + 1  # a data byte FF, or a restart inside the scan
            continue
        return position
