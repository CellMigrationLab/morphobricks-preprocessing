"""The experiment, masks and output folders may not hold one another (contract §15).

Every file in the masks folder is read as a mask, and every folder in the experiment as a
condition or repeat, so (audit of 3 November 2026, B1 and I1):

- the masks folder may not be inside the experiment (``masks_inside_experiment``), and the
  experiment may not be inside the masks folder (``experiment_inside_masks``): its images
  would be read as masks of a type named after its folder;
- the masks folder may not hold a tree of named-axis copies, found by its conversion record
  (:data:`.conversion_record.RECORD_FILENAME`) anywhere in it (``copies_inside_masks``):
  the copies would be read as masks too;
- the output folder may not be inside the masks folder (``output_inside_masks``), checked
  before anything is written to it: not the folder, the lock or the report.

An output folder that holds both the experiment and the masks folder (their parent) is
allowed: the brick writes only named files at its top level and walks neither them nor the
output folder when reading the experiment and the masks (contract §15).

Every path is compared resolved (every link followed), then by device and inode for the
folders that exist (another spelling of a folder on a file system that ignores case), with
:func:`.links.relative_inside`.
"""

from __future__ import annotations

import os
from pathlib import Path

from .conversion_record import RECORD_FILENAME
from .links import relative_inside
from .validation import ValidationIssue, error

EXPERIMENT_INSIDE_MASKS = "experiment_inside_masks"
COPIES_INSIDE_MASKS = "copies_inside_masks"
MASKS_INSIDE_EXPERIMENT = "masks_inside_experiment"
OUTPUT_INSIDE_MASKS = "output_inside_masks"
FOLDER_RULE_CODES = frozenset(
    {EXPERIMENT_INSIDE_MASKS, COPIES_INSIDE_MASKS, MASKS_INSIDE_EXPERIMENT, OUTPUT_INSIDE_MASKS}
)
"""The errors about folders that hold one another."""


def masks_folder_issues(
    masks_folder: str | os.PathLike,
    experiment_root: str | os.PathLike,
    output_folder: str | os.PathLike | None = None,
) -> list[ValidationIssue]:
    """Refuse a masks folder that holds, or is held by, the experiment, copies or output.

    Parameters
    ----------
    masks_folder
        The masks folder (an existing folder).
    experiment_root
        The experiment folder.
    output_folder
        The output folder, if known.

    Returns
    -------
    list of ValidationIssue
        The first error of :data:`FOLDER_RULE_CODES` that applies; empty when none does.
    """
    folder, root = Path(masks_folder).resolve(), Path(experiment_root).resolve()
    below = relative_inside(folder, root)
    if below is not None:
        return [
            error(
                MASKS_INSIDE_EXPERIMENT,
                "The masks folder is inside the experiment folder, where its folders would be "
                "read as conditions and repeats.",
                [below],
                "Move the masks folder next to the experiment folder and select it there.",
            )
        ]
    below = relative_inside(root, folder)
    if below is not None:
        return [
            error(
                EXPERIMENT_INSIDE_MASKS,
                "The experiment folder is inside the masks folder, where its images would be "
                "read as masks.",
                [below],
                "Select the folder that holds only the masks, one subfolder per mask type, "
                "next to the experiment folder.",
            )
        ]
    record = conversion_record_in(folder)
    if record is not None:
        return [
            error(
                COPIES_INSIDE_MASKS,
                "The masks folder holds named-axis copies of images (a conversion record), "
                "which would be read as masks.",
                [record],
                "Move the copies out of the masks folder, or select the folder that holds "
                "only the masks.",
            )
        ]
    return output_inside_masks_issues(output_folder, folder)


def output_inside_masks_issues(
    output_folder: str | os.PathLike | None, masks_folder: str | os.PathLike | None
) -> list[ValidationIssue]:
    """Refuse an output folder that is the masks folder or inside it.

    Parameters
    ----------
    output_folder
        The output folder, or None when not known.
    masks_folder
        The masks folder, given or recorded, or None without one.

    Returns
    -------
    list of ValidationIssue
        ``output_inside_masks``, or empty.
    """
    if output_folder is None or masks_folder is None:
        return []
    below = relative_inside(Path(output_folder).resolve(), Path(masks_folder))
    if below is None:
        return []
    return [
        error(
            OUTPUT_INSIDE_MASKS,
            "The output folder is inside the masks folder, where the run's own files would "
            "be read as masks.",
            [below],
            "Choose an output folder outside the masks folder.",
        )
    ]


def conversion_record_in(folder: str | os.PathLike) -> str | None:
    """Return the first conversion record in ``folder``'s tree, relative and sorted, or None.

    Links to folders are not followed (a link inside the folder points into the tree walked
    anyway, and one leaving it is refused by the walk of the masks, :mod:`.links`), and
    OME-Zarr stores are not entered: a conversion record is at the top of a copy tree.
    """
    top = Path(folder)
    for current, folders, files in os.walk(top):
        folders[:] = sorted(name for name in folders if not name.lower().endswith(".zarr"))
        if RECORD_FILENAME in files:
            return (Path(current) / RECORD_FILENAME).relative_to(top).as_posix()
    return None
