"""Refuse an output folder inside the masks folder before anything is written (audit I1).

Every command that writes into the output folder (``check``, ``run``, ``masks``, ``design``,
``calibrate``) checks it against the masks folder, given or recorded by the run in the
output folder, before it creates the folder, takes its lock or writes a report
(:mod:`..folder_rules`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..folder_rules import output_inside_masks_issues
from ..validation import ValidationIssue
from .recorded import recorded_masks_folder, usable
from .reuse import read_previous


class OutputInsideMasksError(ValueError):
    """The output folder is inside the masks folder; nothing is written. The message says so."""


def output_masks_issues(
    output_folder: str | os.PathLike, masks_folder: str | os.PathLike | Literal[False] | None
) -> list[ValidationIssue]:
    """Return ``output_inside_masks`` when the output folder is inside the masks folder.

    Parameters
    ----------
    output_folder
        The output folder.
    masks_folder
        The masks folder given; None for the one the run in the output folder recorded
        (none without such a run), False for none.

    Returns
    -------
    list of ValidationIssue
    """
    output = Path(output_folder).resolve()
    if masks_folder is None:
        masks_folder = recorded_masks_of(output)
    return output_inside_masks_issues(output, masks_folder or None)


def recorded_masks_of(output_folder: str | os.PathLike) -> Path | None:
    """Return the masks folder the run in the output folder recorded, or None.

    Parameters
    ----------
    output_folder
        The output folder.

    Returns
    -------
    Path or None
        None without a usable (v0.4.0 or later) manifest or without a masks folder.
    """
    manifest = usable(read_previous(Path(output_folder).resolve())[0])
    return None if manifest is None else recorded_masks_folder(manifest)


def refuse_output_inside_masks(output_folder: str | os.PathLike) -> None:
    """Raise :class:`OutputInsideMasksError` for an output folder inside the recorded masks.

    Raises
    ------
    OutputInsideMasksError
        If the run in the output folder recorded a masks folder that holds the output folder.
    """
    issues = output_masks_issues(output_folder, None)
    if issues:
        raise OutputInsideMasksError(f"{issues[0].message} {issues[0].action}")
