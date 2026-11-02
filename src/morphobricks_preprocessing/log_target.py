"""Where the command line's ``--log`` may write (issue #99; owner, 2 November 2026).

The log is checked before it is opened, and it is opened only once the command's arguments
are checked, so an invalid command writes nothing (design v0.4 §1: sources are never
touched). A log is refused:

- inside the experiment folder or the masks folder, given or recorded by the earlier run in
  the output folder (it would become a source, or change one);
- inside the folder of named-axis copies of ``convert`` (every file there is a copy, and
  the conversion refuses files it did not write);
- as, or inside, a file or folder the run writes or keeps in its output folder
  (:data:`PROTECTED_OUTPUTS`, the lock and the staging folders). Any other name in the
  output folder is fine;
- when it is a folder, or its folder does not exist.

Paths are compared resolved, so a link to a protected file is refused as the file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from .calibration import CALIBRATION_FILENAME
from .conversion_record import RECORD_FILENAME
from .design import DESIGN_FILENAME
from .discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from .links import is_inside
from .manifest import MANIFEST_FILENAME
from .mask_scan import MASK_REPORT_FILENAME
from .run.lock import LOCK_FILENAME
from .run.publish import STAGING_PREFIX
from .run.recorded import recorded_masks_folder, usable
from .run.reuse import read_previous

PROTECTED_OUTPUTS = frozenset(
    {
        OUTPUT_FILENAME,
        MANIFEST_FILENAME,
        REPORT_FILENAME,
        REPORT_TEXT_FILENAME,
        DESIGN_FILENAME,
        CALIBRATION_FILENAME,
        MASK_REPORT_FILENAME,
        LOCK_FILENAME,
        RECORD_FILENAME,
    }
)
"""Names in an output folder a log may not be written to, or inside."""


def log_refusal(
    log: str | os.PathLike,
    folders: Mapping[str, str | os.PathLike | None],
    output: str | os.PathLike | None = None,
) -> str | None:
    """Return why a log cannot be written at ``log``, or None when it can.

    Parameters
    ----------
    log
        The log file asked for.
    folders
        Folders the log may not be in, by what they are (``"experiment folder"``); None
        values are left out.
    output
        The command's output folder, where only :data:`PROTECTED_OUTPUTS` and the staging
        folders are refused; None when the command has none.

    Returns
    -------
    str or None
        The message for the user, naming the log and what it would touch.
    """
    target = Path(log).resolve()
    for what, folder in folders.items():
        if folder is not None and is_inside(target, Path(folder).resolve()):
            return _refused(log, f"it is inside the {what} ({Path(folder).resolve()})")
    if output is not None and is_inside(target, Path(output).resolve()):
        parts = target.relative_to(Path(output).resolve()).parts
        if not parts or parts[0] in PROTECTED_OUTPUTS or parts[0].startswith(STAGING_PREFIX):
            return _refused(log, f"it would write over the run's {parts[0] if parts else 'folder'}")
    if target.is_dir():
        return _refused(log, "it is a folder")
    if not target.parent.is_dir():
        return _refused(log, f"its folder {target.parent} does not exist")
    return None


def run_log_refusal(
    log: str | os.PathLike,
    experiment: str | os.PathLike,
    output: str | os.PathLike,
    masks: str | os.PathLike | None,
) -> str | None:
    """Return why the log of ``run`` or ``masks`` cannot be written, or None.

    The experiment and masks folders recorded by the earlier run in ``output`` are refused
    too, as the given ones (the manifest is only read).

    Parameters
    ----------
    log
        The log file asked for.
    experiment
        The experiment folder given (or the recorded one).
    output
        The output folder.
    masks
        The masks folder given, or None.

    Returns
    -------
    str or None
        As :func:`log_refusal`.
    """
    recorded = usable(read_previous(Path(output).resolve())[0])
    folders = {"experiment folder": experiment, "masks folder": masks}
    if recorded is not None:
        folders["experiment folder of the earlier run"] = recorded.experiment_root
        folders["masks folder of the earlier run"] = recorded_masks_folder(recorded)
    return log_refusal(log, folders, output)


def _refused(log: str | os.PathLike, why: str) -> str:
    """Say that the log is not opened, why, and what to do."""
    return (
        f"The log {log} was not opened: {why}. Nothing was written. Give --log another file, "
        "outside the experiment and masks folders, for example run.log in the output folder."
    )
