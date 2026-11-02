"""Read and write ``calibration.csv``, the pixel sizes the user states (design v0.4 §3).

The file is in the output folder, one statement per line, with the columns
``target,axis,size,unit``::

    target,axis,size,unit
    .,X,0.65,um
    .,Y,0.65,um
    ctrl/rep2,Z,2,micrometre
    drug/rep1/tr1/img3.tif,X,,pixels

It is the user's: a run reads it and never writes, replaces or removes it, so it survives
``full`` and failed runs; :func:`write_calibration` (the ``calibrate`` command) writes it
only when asked. The reader is strict: anything it cannot read exactly is refused, naming
the line, and never read as no statement. A UTF-8 byte-order mark (a file saved by a
spreadsheet program) and Windows line endings are accepted.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path

from ._io import write_text_atomic
from .calibration import CALIBRATION_FILENAME, Calibration, CalibrationEntry, calibration_entry
from .validation import ValidationIssue, error, plural

CALIBRATION_COLUMNS = ("target", "axis", "size", "unit")


class CalibrationFormatError(ValueError):
    """``calibration.csv`` cannot be read; the message names the line and what is wrong."""


def read_calibration(path: str | os.PathLike) -> tuple[Calibration, str]:
    """Read ``calibration.csv``, refusing anything that is not exactly a statement.

    Parameters
    ----------
    path
        The file.

    Returns
    -------
    tuple
        The statements, and the SHA-256 of the bytes read (recorded in the manifest).

    Raises
    ------
    CalibrationFormatError
        Naming the line: not UTF-8 text or CSV, other columns, a value missing or extra,
        a target, axis, size or unit that is not allowed, or two statements for one
        target and axis.
    OSError
        If the file cannot be read.
    """
    path = Path(path)
    data = path.read_bytes()
    return _parse(data, path.name), hashlib.sha256(data).hexdigest()


def _parse(data: bytes, name: str) -> Calibration:
    """Parse the file's bytes into statements (see :func:`read_calibration`)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CalibrationFormatError(f"{name} is not UTF-8 text: {exc}") from None
    try:
        lines = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise CalibrationFormatError(f"{name} is not a well-formed CSV file: {exc}") from None
    if not lines or tuple(lines[0]) != CALIBRATION_COLUMNS:
        found = ", ".join(lines[0]) if lines else "nothing"
        raise CalibrationFormatError(
            f"{name} must have the columns {', '.join(CALIBRATION_COLUMNS)} in this order, "
            f"not {found}."
        )
    entries: dict[tuple[str, str], int] = {}
    statements: list[CalibrationEntry] = []
    for number, values in enumerate(lines[1:], start=2):
        if not values:
            continue  # a blank line
        entry = _entry(values, f"{name}, line {number}")
        key = (entry.target, entry.axis)
        if key in entries:
            raise CalibrationFormatError(
                f"{name}, line {number}: {entry.target} {entry.axis} is already stated on line "
                f"{entries[key]}; keep one statement per target and axis."
            )
        entries[key] = number
        statements.append(entry)
    return Calibration(tuple(statements))


def _entry(values: list[str], where: str) -> CalibrationEntry:
    """Read one line's statement, naming the line when it is refused."""
    if len(values) != len(CALIBRATION_COLUMNS):
        raise CalibrationFormatError(
            f"{where} has {plural(len(values), 'value')} for "
            f"{plural(len(CALIBRATION_COLUMNS), 'column')}."
        )
    target, axis, size, unit = values
    for column, value in zip(CALIBRATION_COLUMNS, values, strict=True):
        if value != value.strip():
            raise CalibrationFormatError(
                f"{where}: {column} {value!r} starts or ends with whitespace."
            )
    try:
        return calibration_entry(target, axis, size, unit)
    except ValueError as exc:
        raise CalibrationFormatError(f"{where}: {exc}.") from None


def calibration_text(calibration: Calibration) -> str:
    """Return the statements as the text of ``calibration.csv`` (LF line endings)."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CALIBRATION_COLUMNS)
    for entry in calibration.entries:
        size = "" if entry.size is None else repr(entry.size)
        writer.writerow([entry.target, entry.axis, size, entry.unit])
    return buffer.getvalue()


def write_calibration_file(calibration: Calibration, path: str | os.PathLike) -> Path:
    """Write the statements to ``path`` atomically (see :func:`.run.write_calibration`)."""
    return write_text_atomic(path, calibration_text(calibration), parents=True)


def load_calibration(
    output_folder: str | os.PathLike,
) -> tuple[Calibration | None, str | None, ValidationIssue | None]:
    """Read the output folder's ``calibration.csv``, if any, as a run reads it.

    Parameters
    ----------
    output_folder
        The output folder.

    Returns
    -------
    tuple
        ``(calibration, sha256, None)``; ``(None, None, None)`` without the file; and
        ``(None, None, issue)`` with the error ``calibration_unreadable`` when it cannot be
        read. The file is left as it is.
    """
    path = Path(output_folder) / CALIBRATION_FILENAME
    if not path.is_file():
        return None, None, None
    try:
        calibration, sha256 = read_calibration(path)
    except (CalibrationFormatError, OSError) as exc:
        issue = error(
            "calibration_unreadable",
            f"The stated pixel sizes cannot be read: {str(exc).rstrip('.')}. Nothing is "
            "guessed, so the run stops; the file is left as it is.",
            [path.as_posix()],
            "Correct calibration.csv (columns target, axis, size, unit; see the user guide), "
            "or remove it to use the sizes in the image metadata.",
        )
        return None, None, issue
    return calibration, sha256, None
