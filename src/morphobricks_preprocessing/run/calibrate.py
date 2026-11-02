"""State pixel sizes: write the output folder's ``calibration.csv`` (design v0.4 §3).

A run reads ``calibration.csv`` and never writes it; the user states sizes with
:func:`write_calibration`, :func:`update_calibration` (the ``calibrate`` command) or by
editing the file. Writing holds the output folder's lock, so a run never reads a
half-stated calibration; :func:`update_calibration` also reads the file under the lock, so
two changes never overwrite each other. The next run
records the new sizes and names the change in its report; no row is recomputed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..calibration import CALIBRATION_FILENAME, Calibration
from ..calibration_io import read_calibration, write_calibration_file
from .lock import output_lock


def write_calibration(calibration: Calibration, output_folder: str | os.PathLike) -> Path:
    """Write the statements to ``calibration.csv`` in the output folder, holding its lock.

    Parameters
    ----------
    calibration
        Every statement the file is to hold (:class:`.calibration.Calibration`); an empty
        one leaves a file with only its header, which states nothing.
    output_folder
        The output folder; created if needed.

    Returns
    -------
    Path
        The file written.

    Raises
    ------
    OutputLockedError
        If a run holds the output folder's lock; nothing is written.
    """
    output = Path(output_folder).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with output_lock(output):
        return write_calibration_file(calibration, output / CALIBRATION_FILENAME)


def update_calibration(
    output_folder: str | os.PathLike, change: Callable[[Calibration], Calibration]
) -> tuple[Calibration, Path | None]:
    """Change ``calibration.csv`` in the output folder, reading and writing it under its lock.

    Parameters
    ----------
    output_folder
        The output folder; created if needed.
    change
        Given the statements in the file (none without one), returns every statement the
        file is to hold.

    Returns
    -------
    tuple
        The statements after the change, and the file written, or None when nothing changed
        (the file is then left as it is).

    Raises
    ------
    OutputLockedError
        If a run holds the output folder's lock; nothing is read or written.
    CalibrationFormatError
        If the file cannot be read; it is left as it is.
    """
    output = Path(output_folder).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with output_lock(output):
        path = output / CALIBRATION_FILENAME
        current = read_calibration(path)[0] if path.is_file() else Calibration()
        changed = change(current)
        if changed == current:
            return changed, None
        return changed, write_calibration_file(changed, path)
