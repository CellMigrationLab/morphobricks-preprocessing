"""The image QC table ``image_qc.csv``: one row per plane and channel (contract §19).

One row for each channel of each row of ``plane_histograms.csv``: the plane's identity (the
same columns and values as the histogram table), the canonical channel ID, whether the plane
was read in blocks, ``qc_status``, the metrics of :mod:`.image_qc` (blank where undefined,
never zero), ``blank_reasons`` (why each blank metric is blank) and ``qc_version``. Rows are
sorted by image, Z, T and channel ID; floats are written with ``repr``, so they read back
exactly. The reader is strict: another header, an unknown status or version, a value that is
not a number, or a combination no writer makes (:func:`_consistent`: a status with metrics it
blanks, a blank metric without a reason, a value out of its range) refuses the whole table.
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
from .image_qc import (
    ENTROPY_BINS,
    HISTOGRAM_METRICS,
    MEASURED,
    METRICS,
    NO_FINITE,
    NON_FINITE,
    PIXEL_METRICS,
    QC_VERSION,
    RATIO_METRICS,
    REASONS,
    ImageQc,
)

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
COLUMNS = (*IDENTITY, "read_in_blocks", "qc_status", *METRICS, "blank_reasons", "qc_version")
"""Every column, in order."""
_STATUSES = frozenset({MEASURED, NON_FINITE, NO_FINITE})
_RANGES = {
    "otsu_separation": (0.0, 1.0),
    "foreground_fraction": (0.0, 1.0),
    "entropy": (0.0, math.log2(ENTROPY_BINS)),
    "correlation_1": (-1.0, 1.0),
    "correlation_4": (-1.0, 1.0),
    "correlation_16": (-1.0, 1.0),
}
"""The closed range of each bounded metric; the ratio metrics are only never negative."""
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
        return cls(cls.identity_of(record, channel), in_blocks, qc)

    @staticmethod
    def identity_of(record: PlaneRecord, channel: str) -> tuple[str, ...]:
        """Return the values of :data:`IDENTITY` of ``channel`` of the histogram row ``record``."""
        return (
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
        if problem := _consistent(row.qc):  # the writer refuses what the reader refuses
            raise ValueError(f"the QC row of {row.identity[-2]!r}, {row.identity[-1]!r}: {problem}")
        values = [getattr(row.qc, name) for name in METRICS]
        writer.writerow(
            [
                *row.identity,
                "true" if row.read_in_blocks else "false",
                row.qc.status,
                *("" if value is None else repr(float(value)) for value in values),
                ";".join(f"{name}={reason}" for name, reason in row.qc.reasons),
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
    qc = ImageQc(data["qc_status"], **metrics, reasons=_reasons(data["blank_reasons"], line, path))
    if problem := _consistent(qc):
        raise ImageQcFormatError(f"{path}, line {line}: {problem}.")
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


def _reasons(text: str, line: int, path: str | os.PathLike) -> tuple[tuple[str, str], ...]:
    """Return the ``metric=reason`` pairs of a ``blank_reasons`` cell, as written."""
    pairs = []
    for item in text.split(";") if text else ():
        name, _, reason = item.partition("=")
        if name not in METRICS or reason not in REASONS:
            raise ImageQcFormatError(f"{path}, line {line}: blank_reasons has {item!r}.")
        pairs.append((name, reason))
    return tuple(pairs)


def _consistent(qc: ImageQc) -> str | None:
    """Return what is wrong with a row's status, metrics and reasons together, or None.

    The combinations the writer never makes: a metric without a value and without a reason
    (or the reverse), reasons out of table order or repeated, a status that does not match
    what it blanks (``no_finite_pixels``: every metric, each with that reason;
    ``non_finite_pixels``: exactly the pixel metrics, each with that reason, and the
    histogram metrics of the finite pixels; ``measured``: the entropy at least, and no
    status reason), the three Otsu metrics not blank together, and a value outside its range.
    """
    values = {name: getattr(qc, name) for name in METRICS}
    blank = [name for name in METRICS if values[name] is None]
    why = dict(qc.reasons)
    if [name for name, _ in qc.reasons] != blank:
        return "the blank metrics and blank_reasons do not name the same metrics, in table order"
    if qc.status == NO_FINITE:
        if blank != list(METRICS) or set(why.values()) != {NO_FINITE}:
            return "qc_status no_finite_pixels with a metric present or another reason"
        return None
    if values["entropy"] is None:
        return f"qc_status {qc.status} without an entropy"
    if qc.status == NON_FINITE:
        if not set(PIXEL_METRICS) <= set(blank) or any(why[n] != NON_FINITE for n in PIXEL_METRICS):
            return "qc_status non_finite_pixels with a pixel metric present or another reason"
    elif {NON_FINITE, NO_FINITE} & set(why.values()):
        return "qc_status measured with a metric blank for pixels that are not finite"
    if len({values[n] is None for n in HISTOGRAM_METRICS[:3]}) != 1:
        return "the Otsu metrics are not blank together"
    for name, value in values.items():
        low, high = _RANGES.get(name, (0.0 if name in RATIO_METRICS else -math.inf, math.inf))
        if value is not None and not low <= value <= high:
            return f"{name} is outside its range"
    return None
