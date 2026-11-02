"""State pixel sizes: write the output folder's ``calibration.csv`` (design v0.4 §3).

A run reads ``calibration.csv`` and never writes it; the user states sizes with
:func:`write_calibration` (the ``calibrate`` command) or by editing the file. Writing holds
the output folder's lock, so a run never reads a half-stated calibration. The next run
records the new sizes and names the change in its report; no row is recomputed.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..calibration import CALIBRATION_FILENAME, Calibration
from ..calibration_io import write_calibration_file
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
