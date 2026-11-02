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
from .run.generation import PUBLISH_FOLDER
from .run.lock import LOCK_FILENAME
from .run.recorded import recorded_masks_folder, usable
from .run.reuse import read_previous
from .run.staging import STAGING_FOLDER

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
        if folder is not None and _parts_below(target, Path(folder)) is not None:
            return _refused(log, f"it is inside the {what} ({Path(folder).resolve()})")
    parts = None if output is None else _parts_below(target, Path(output))
    if parts is not None:
        first = parts[0].casefold() if parts else ""
        if not parts or first in _PROTECTED_FOLDED:
            return _refused(log, f"it would write over the run's {parts[0] if parts else 'folder'}")
    if target.is_dir():
        return _refused(log, "it is a folder")
    if not target.parent.is_dir():
        return _refused(log, f"its folder {target.parent} does not exist")
    if target.is_file() and target.stat().st_nlink > 1:
        return _refused(log, "it is a file that has another name too (a hard link)")
    return None


_PROTECTED_FOLDED = frozenset(name.casefold() for name in PROTECTED_OUTPUTS)


def _parts_below(target: Path, folder: Path) -> tuple[str, ...] | None:
    """Return the names of ``target`` (resolved) below ``folder``; None when it is not inside.

    Compared by resolved path, then, for each existing folder above ``target``, by device and
    inode, which catches another spelling of ``folder`` on a file system that ignores case.
    """
    real = folder.resolve()
    if is_inside(target, real):
        return target.relative_to(real).parts
    try:
        wanted = os.stat(real)
    except OSError:  # a folder that does not exist holds nothing
        return None
    chain = [target, *target.parents]
    for depth, above in enumerate(chain):
        try:
            if os.path.samestat(os.stat(above), wanted):
                return tuple(path.name for path in reversed(chain[:depth]))
        except OSError:  # not there (yet): nothing to compare
            continue
    return None


def run_log_refusal(
    log: str | os.PathLike,
    experiment: str | os.PathLike,
    output: str | os.PathLike,
    masks: str | os.PathLike | None,
    design: str | os.PathLike | None = None,
) -> str | None:
    """Return why the log of ``run`` or ``masks`` cannot be written, or None.

    The experiment and masks folders recorded by the earlier run in ``output`` are refused
    too, as the given ones (the manifest is only read), and so is the design file given.

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
    design
        The design file given with ``--design``, or None.

    Returns
    -------
    str or None
        As :func:`log_refusal`.
    """
    if design is not None and _same_file(Path(log), Path(design)):
        given = Path(design).resolve()
        return _refused(log, f"it is the design file given with --design ({given})")
    recorded = usable(read_previous(Path(output).resolve())[0])
    folders = {"experiment folder": experiment, "masks folder": masks}
    if recorded is not None:
        folders["experiment folder of the earlier run"] = recorded.experiment_root
        folders["masks folder of the earlier run"] = recorded_masks_folder(recorded)
    return log_refusal(log, folders, output)


def _same_file(one: Path, other: Path) -> bool:
    """Return True when two paths name one file: resolved, or by device and inode."""
    if one.resolve() == other.resolve():
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:  # one of them does not exist: not the same file
        return False


def _refused(log: str | os.PathLike, why: str) -> str:
    """Say that the log is not opened, why, and what to do."""
    return (
        f"The log {log} was not opened: {why}. Nothing was written. Give --log another file, "
        "outside the experiment and masks folders, for example run.log in the output folder."
    )
