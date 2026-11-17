"""The run's ``plane_histograms.csv``, put together from its files' staged rows (D7).

Each file a run reads is staged by the worker that read it (:func:`.staging.stage_file`): its
rows in table order, written by the normalization brick's writer with the table's channel
IDs, so the staged file is the table's header followed by that file's rows of the table. The
table is therefore the header followed by every file's staged rows, file after file in path
order (rows are in ``(source_relative_path, z, t)`` order): the calling thread copies them,
and computes the table's SHA-256 as it writes, instead of formatting every row again.

The bytes are always those of ``write_plane_histograms(records, path, channel_ids=...)``
with the run's rows: rows that have no staged file usable as it is are written here by that
writer, in runs of consecutive files. That is the case of the rows kept from the earlier
table, and of a staged file whose header, rows (their plane IDs and order) or SHA-256 is not
the one staged (another run's channel IDs, or a file changed since: it is never copied). The
writer's refusals are made first, on every row, in the writer's order.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import logging
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol

from morphobricks_normalization.channels import check_channel_ids
from morphobricks_normalization.csv_io import table_header, write_plane_histograms
from morphobricks_normalization.records import (
    PlaneRecord,
    check_universe,
    duplicate_planes,
    same_channels_per_image,
)

from .staging import StagedRows

logger = logging.getLogger(__name__)

_BLOCK = 1 << 20
"""Bytes copied at a time from a staged rows file."""
_SUBJECT = "The planes to write"


class _Digest(Protocol):
    """A ``hashlib`` hash: what the table's SHA-256 is taken with."""

    def update(self, data: bytes, /) -> None:
        """Add ``data``."""


class _HeaderNotTheWriters(Exception):
    """The writer's header is not the one expected: the table is written whole instead."""


def write_table(
    records: Sequence[PlaneRecord],
    path: Path,
    channel_ids: tuple[str, ...],
    staged: Mapping[str, StagedRows],
) -> tuple[Path, str]:
    """Write the table of ``records`` to ``path``; return it and its SHA-256.

    Parameters
    ----------
    records
        Every row of the table, in table order (``_row_order``): each file's rows together.
    path
        The table file, replaced only once it is whole.
    channel_ids
        The table's channel IDs (the run's).
    staged
        ``{file: StagedRows}`` of the files whose rows were staged in this run (read, or
        resumed): their rows are copied when the staged file is still the one staged.

    Returns
    -------
    tuple
        ``(path, SHA-256 of the bytes written)``: the bytes of
        ``write_plane_histograms(records, path, channel_ids=channel_ids)``.

    Raises
    ------
    ValueError
        As :func:`morphobricks_normalization.csv_io.write_plane_histograms`, before anything
        is written.
    """
    bin_counts = {record.histograms[0].bin_count for record in records}
    if not channel_ids or len(bin_counts) != 1:  # no rows, or no ids: the writer's refusal
        return _whole(records, path, channel_ids)
    check_channel_ids(channel_ids, "The table's channel IDs")
    check_universe(list(records), set(channel_ids))
    duplicate_planes(records, _SUBJECT)
    same_channels_per_image(records, _SUBJECT)
    header = _header_line(channel_ids, bin_counts.pop())
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            sha256 = _write_rows(records, out, header, (channel_ids, staged, path.parent))
        os.replace(temporary, path)
    except _HeaderNotTheWriters:
        logger.warning("The table's header is not the expected one; the table is written whole")
        return _whole(records, path, channel_ids)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path, sha256


def _whole(records: Sequence[PlaneRecord], path: Path, ids: tuple[str, ...]) -> tuple[Path, str]:
    """Write the table with the normalization brick's writer alone; return it and its SHA-256."""
    written = write_plane_histograms(records, path, channel_ids=ids)
    digest = hashlib.sha256()
    with open(written, "rb") as fh:
        for block in iter(lambda: fh.read(_BLOCK), b""):
            digest.update(block)
    return written, digest.hexdigest()


def _header_line(channel_ids: tuple[str, ...], bin_count: int) -> bytes:
    """Return the table's header line as the writer writes it (``csv`` defaults, UTF-8)."""
    text = io.StringIO(newline="")
    csv.writer(text).writerow(table_header(channel_ids, bin_count))
    return text.getvalue().encode("utf-8")


def _write_rows(
    records: Sequence[PlaneRecord],
    out: BinaryIO,
    header: bytes,
    how: tuple[tuple[str, ...], Mapping[str, StagedRows], Path],
) -> str:
    """Write the header and every file's rows to ``out``; return the SHA-256 of the bytes.

    ``how`` is the table's channel IDs, the staged rows and the folder for the writer's files.
    """
    ids, staged, folder = how
    table = (header, ids, folder)
    digest = hashlib.sha256(header)
    out.write(header)
    pending: list[PlaneRecord] = []  # consecutive rows the writer writes here
    for rel, rows in itertools.groupby(records, key=lambda record: record.source_relative_path):
        rows = list(rows)
        found = staged.get(rel)
        if found is None or found.plane_ids != tuple(row.plane_id for row in rows):
            pending.extend(rows)
            continue
        _write_pending(pending, out, digest, table)
        start, before = out.tell(), digest.copy()
        if not _copy_staged(found, out, digest, header):
            logger.warning("The staged rows of %s are not the ones staged; written again", rel)
            out.seek(start)
            out.truncate()
            digest = before
            pending.extend(rows)
    _write_pending(pending, out, digest, table)
    return digest.hexdigest()


def _copy_staged(found: StagedRows, out: BinaryIO, digest: _Digest, header: bytes) -> bool:
    """Copy a staged file's rows to ``out`` (and ``digest``); False when it is not as staged.

    False when its header is not the table's (staged with other channel IDs) or its SHA-256
    is not the one taken when it was staged; what was copied must then be taken back.
    """
    check = hashlib.sha256()
    try:
        with open(found.path, "rb") as fh:
            head = fh.read(len(header))
            check.update(head)
            if head != header:
                return False
            for block in iter(lambda: fh.read(_BLOCK), b""):
                check.update(block)
                digest.update(block)
                out.write(block)
    except OSError:
        logger.warning("Cannot read the staged rows %s", found.path, exc_info=True)
        return False
    return check.hexdigest() == found.sha256


def _write_pending(
    pending: list[PlaneRecord],
    out: BinaryIO,
    digest: _Digest,
    table: tuple[bytes, tuple[str, ...], Path],
) -> None:
    """Write ``pending`` rows with the normalization brick's writer, then empty the list.

    ``table`` is the header line, the channel IDs and the folder for the writer's file.
    """
    if not pending:
        return
    header, ids, folder = table
    handle, temporary = tempfile.mkstemp(prefix=".rows.", suffix=".csv", dir=folder)
    os.close(handle)
    try:
        written = write_plane_histograms(pending, Path(temporary), channel_ids=ids)
        with open(written, "rb") as fh:
            if fh.read(len(header)) != header:
                raise _HeaderNotTheWriters
            for block in iter(lambda: fh.read(_BLOCK), b""):
                digest.update(block)
                out.write(block)
    finally:
        Path(temporary).unlink(missing_ok=True)
    pending.clear()
