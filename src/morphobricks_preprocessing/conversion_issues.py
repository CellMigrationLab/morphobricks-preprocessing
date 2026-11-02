"""The messages of :mod:`.conversion`: why a conversion was refused or failed (contract §17).

Each function builds one error, in plain language, naming the paths and an action. Nothing
here reads or writes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .conversion_record import RECORD_FILENAME
from .validation import ValidationIssue, error, plural

if TYPE_CHECKING:
    from .conversion_record import ConversionRecordError
    from .run.lock import OutputLockedError


def overlap_issue(root: Path, dest: Path) -> ValidationIssue:
    """Refuse a copy tree that is the experiment folder, inside it, or holds it."""
    return error(
        "copy_folder_overlaps_experiment",
        "The folder for the named-axis copies is the experiment folder, inside it, or "
        "holds it. The copies must be kept apart from the originals, which are never "
        "changed.",
        [dest.as_posix()],
        "Choose a new folder outside the experiment, for example "
        f"{root.with_name(root.name + '_named_axes')}.",
    )


def not_a_folder_issue(dest: Path) -> ValidationIssue:
    """Refuse a path for the copies that is a file."""
    return error(
        "copy_folder_not_a_folder",
        "The path given for the named-axis copies is a file, not a folder.",
        [dest.as_posix()],
        "Choose a new folder for the copies.",
    )


def space_issue(dest: Path, needed: int, free: int) -> ValidationIssue:
    """Refuse a conversion that would not fit on the drive of the copy tree."""
    return error(
        "not_enough_space",
        f"The copies need about {needed / 2**30:.1f} GiB, but the drive holding {dest} has "
        f"{free / 2**30:.1f} GiB free. Nothing was written.",
        [dest.as_posix()],
        "Free some space, or choose a folder on a drive with more space.",
    )


def pixel_type_issue(paths: list[str], dtype: str) -> ValidationIssue:
    """Refuse to convert images of a pixel type that OME-TIFF cannot hold."""
    return error(
        "cannot_convert_pixel_type",
        f"{plural(len(paths), 'image')} with axis problems have the pixel type {dtype}, which "
        "OME-TIFF cannot hold, and pixel values are never changed to fit. Nothing was "
        "written.",
        paths,
        "Save these images again with their axes named in their metadata (for example as "
        "OME-TIFF in a 32-bit type, if their values fit), or correct the roles.",
    )


def not_empty_issue(dest: Path, paths: list[str]) -> ValidationIssue:
    """Refuse a folder that holds files and no conversion record."""
    return error(
        "copy_folder_not_empty",
        f"The folder for the named-axis copies already holds {plural(len(paths), 'file')} "
        "and no conversion record, so it is not a copy tree this conversion can complete. "
        "Nothing was overwritten.",
        paths,
        f"Choose a new or empty folder for the copies, or empty {dest} first.",
    )


def differs_issue(dest: Path, paths: list[str]) -> ValidationIssue:
    """Refuse to overwrite copies that differ from the ones this conversion would write."""
    return error(
        "copy_differs",
        f"{plural(len(paths), 'image')} in {dest} differ from the copies this conversion "
        "would write: their source or roles changed, the copy was changed, or the image is no "
        "longer in the experiment. Nothing was overwritten.",
        paths,
        "Convert into a new, empty folder, or remove these copies first (with the conversion "
        f"record {RECORD_FILENAME} if you remove the whole folder's contents).",
    )


def other_experiment_issue(dest: Path, source_root: str) -> ValidationIssue:
    """Refuse a copy tree of another experiment."""
    return error(
        "copy_differs",
        f"{dest} holds named-axis copies of another experiment ({source_root}). Nothing was "
        "overwritten.",
        [RECORD_FILENAME],
        "Choose a new, empty folder for the copies of this experiment.",
    )


def record_issue(exc: ConversionRecordError, dest: Path) -> ValidationIssue:
    """Refuse a copy tree whose conversion record cannot be read."""
    return error(
        "conversion_record_unreadable",
        f"The conversion record in {dest} cannot be read ({exc}), so its copies cannot be "
        "checked. Nothing was overwritten.",
        [RECORD_FILENAME],
        "Choose a new, empty folder for the copies.",
    )


def failed_issue(path: str, exc: Exception) -> ValidationIssue:
    """Say that a copy could not be written, and that nothing was kept."""
    return error(
        "conversion_failed",
        f"{path} could not be copied ({exc}). The copies written so far in this conversion "
        "were removed; the folder is as it was.",
        [path],
        "Check that the image can be read and is not being changed, that the folder can be "
        "written, then convert again (or exclude the image).",
    )


def lock_issue(exc: OutputLockedError) -> ValidationIssue:
    """Refuse a conversion while another one writes into the same folder."""
    return error(
        "output_locked",
        f"Another conversion is writing into this folder: {exc.holder.describe()}. Nothing "
        "was done.",
        [exc.path.as_posix()],
        "Wait for it to finish. If none is going on (it was stopped or the computer "
        "restarted), give --remove-stale-lock to the command, or call "
        "remove_stale_lock(folder), then convert again.",
    )
