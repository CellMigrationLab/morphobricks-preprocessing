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
- as the design file given with ``--design`` (an input of the run);
- when it is a folder, or its folder does not exist.

Paths are compared resolved, so a link to a protected file is refused as the file, and by
device and inode where they exist, so another spelling of a folder (case on macOS and
Windows) is refused as the folder; output names are compared ignoring case. An existing file
with another name (a hard link) is refused: it may be a source or an output under that name.
The rule is the shared :func:`.write_target.write_target_refusal` (owner, 3 November 2026),
which other bricks apply to the files their users name; this module gives it the folders,
files and output names of a command and words the refusal for ``--log``.
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
from .manifest import MANIFEST_FILENAME
from .mask_scan import MASK_REPORT_FILENAME
from .run.generation import PUBLISH_FOLDER
from .run.lock import LOCK_FILENAME
from .run.recorded import recorded_masks_folder, usable
from .run.reuse import read_previous
from .run.staging import STAGING_FOLDER
from .write_target import write_target_refusal

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
        PUBLISH_FOLDER,
        STAGING_FOLDER,
    }
)
"""Names in an output folder a log may not be written to, or inside."""


def log_refusal(
    log: str | os.PathLike,
    folders: Mapping[str, str | os.PathLike | None],
    output: str | os.PathLike | None = None,
    *,
    files: Mapping[str, str | os.PathLike | None] | None = None,
) -> str | None:
    """Return why a log cannot be written at ``log``, or None when it can.

    The rule is :func:`.write_target.write_target_refusal`'s, with the output folder's
    :data:`PROTECTED_OUTPUTS`.

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
    files
        Files the log may not be, by what they are; None values are left out.

    Returns
    -------
    str or None
        The message for the user, naming the log and what it would touch.
    """
    why = write_target_refusal(
        log,
        protected_folders=folders,
        protected_files=files or {},
        output=output,
        output_names=PROTECTED_OUTPUTS,
    )
    return None if why is None else _refused(log, why)


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
