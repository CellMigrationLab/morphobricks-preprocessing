"""The image QC table ``image_qc.csv``: one row per plane and channel (contract §19).

One row for each channel of each row of ``plane_histograms.csv``: the plane's identity (the
same columns and values as the histogram table), the canonical channel ID, whether the plane
was read in blocks, ``qc_status``, the metrics of :mod:`.image_qc` (blank where undefined,
never zero) and ``qc_version``. Rows are sorted by image, Z, T and channel ID; floats are
written with ``repr``, so they read back exactly. The reader is strict: another header, an
unknown status or version, or a value that is not a number refuses the whole table.
"""

from __future__ import annotations

import csv
import io
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from morphobricks_normalization.records import PlaneRecord

from ._io import write_text_atomic
from .image_qc import MEASURED, METRICS, NO_FINITE, NON_FINITE, QC_VERSION, ImageQc

IDENTITY = (
    "position_id",
    "image_file",
    "z",
    "t",
    "condition",
    "biological_repeat",
    "technical_repeat",
    "source_relative_path",
    "plane_id",
    "channel",
)
"""The identity columns of a row (the histogram table's, with the channel ID)."""
COLUMNS = (*IDENTITY, "read_in_blocks", "qc_status", *METRICS, "qc_version")
"""Every column, in order."""
_STATUSES = frozenset({MEASURED, NON_FINITE, NO_FINITE})
_FLAGS = {"true": True, "false": False}


class ImageQcFormatError(ValueError):
    """Raised when a file is not an image QC table this version reads."""


@dataclass(frozen=True)
class QcRow:
    """One row of the table: one channel of one plane.

    Attributes
    ----------
    identity
        The values of :data:`IDENTITY`, as text (``z`` and ``t`` as decimal numbers).
    read_in_blocks
        True when the plane was read in blocks (neighbour pairs crossing a block edge are
        left out of the neighbour metrics).
    qc
        The metrics.
    """

    identity: tuple[str, ...]
    read_in_blocks: bool
    qc: ImageQc

    @classmethod
    def of(cls, record: PlaneRecord, channel: str, in_blocks: bool, qc: ImageQc) -> QcRow:
        """Return the row of ``channel`` of the histogram table's row ``record``."""
        identity = (
            record.position_id,
            record.image_file,
            str(record.z),
            str(record.t),
            record.condition,
            record.biological_repeat,
            record.technical_repeat,
            record.source_relative_path,
            record.plane_id,
            channel,
        )
        return cls(identity, in_blocks, qc)

    @property
    def relative_path(self) -> str:
        """The image, relative to the experiment folder."""
        return self.identity[IDENTITY.index("source_relative_path")]

    @property
    def key(self) -> tuple[str, str]:
        """``(plane_id, channel)``: the row's key."""
        return self.identity[-2], self.identity[-1]

    def order(self) -> tuple:
        """Return the sort key: image, Z, T, channel ID."""
        z, t = (int(self.identity[IDENTITY.index(name)]) for name in ("z", "t"))
        return self.relative_path, z, t, self.identity[-1]


def table_text(rows: Iterable[QcRow]) -> str:
    """Return the table of ``rows`` as CSV text (sorted, LF line endings)."""
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in sorted(rows, key=QcRow.order):
        values = [getattr(row.qc, name) for name in METRICS]
        writer.writerow(
            [
                *row.identity,
                "true" if row.read_in_blocks else "false",
                row.qc.status,
                *("" if value is None else repr(float(value)) for value in values),
                QC_VERSION,
            ]
        )
    return stream.getvalue()


def write_image_qc(rows: Iterable[QcRow], path: str | os.PathLike) -> Path:
    """Write the table atomically.

    Parameters
    ----------
    rows
        Its rows, in any order.
    path
        Where to write.

    Returns
    -------
    Path
    """
    return write_text_atomic(path, table_text(rows))


def read_image_qc(path: str | os.PathLike) -> list[QcRow]:
    """Read a table written by :func:`write_image_qc`.

    Raises
    ------
    ImageQcFormatError
        If the file is not such a table (its header, a status, a flag, a number or the
        version).
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ImageQcFormatError(f"{path} is not UTF-8 text: {exc}") from exc
    lines = list(csv.reader(io.StringIO(text, newline="")))
    if not lines or tuple(lines[0]) != COLUMNS:
        raise ImageQcFormatError(f"{path} does not have the image QC table's columns.")
    return [_row(values, number, path) for number, values in enumerate(lines[1:], start=2)]


def _row(values: list[str], line: int, path: str | os.PathLike) -> QcRow:
    """Read one line of the table."""
    if len(values) != len(COLUMNS):
        raise ImageQcFormatError(f"{path}, line {line}: {len(values)} values, not {len(COLUMNS)}.")
    data = dict(zip(COLUMNS, values, strict=True))
    if data["qc_version"] != QC_VERSION or data["qc_status"] not in _STATUSES:
        raise ImageQcFormatError(f"{path}, line {line}: unknown qc_version or qc_status.")
    if data["read_in_blocks"] not in _FLAGS or not all(data[name].isdigit() for name in ("z", "t")):
        raise ImageQcFormatError(f"{path}, line {line}: read_in_blocks, z or t is malformed.")
    metrics = {name: _number(data[name], name, line, path) for name in METRICS}
    qc = ImageQc(data["qc_status"], **metrics)
    return QcRow(tuple(data[name] for name in IDENTITY), _FLAGS[data["read_in_blocks"]], qc)


def _number(text: str, name: str, line: int, path: str | os.PathLike) -> float | None:
    """Return a metric's value: blank is None; anything but a finite number is refused."""
    if text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise ImageQcFormatError(f"{path}, line {line}: {name} is not a finite number.")
    return value
