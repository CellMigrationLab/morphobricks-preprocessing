"""Refuse an output folder inside the masks folder before anything is written (audit I1).

Every command that writes into the output folder (``check``, ``run``, ``masks``,
``calibrate``) checks it against the masks folder, given or recorded by the run in the
output folder, before it creates the folder, takes its lock or writes a report
(:mod:`..folder_rules`). ``calibrate`` also checks it against the experiment, given or
recorded (``output_inside_experiment``, :func:`..discovery.check_output_folder`; #486), as
``check`` and ``run`` do with the experiment they are given.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..discovery import check_output_folder
from ..folder_rules import output_inside_masks_issues
from ..validation import ValidationIssue
from .recorded import recorded_masks_folder, usable
from .reuse import read_previous


class OutputInsideMasksError(ValueError):
    """The output folder is inside the masks folder; nothing is written. The message says so."""


class OutputInsideExperimentError(ValueError):
    """The output folder is the experiment folder or inside it; nothing is written (#486).

    The message starts with :attr:`code` and says where to write instead.
    """

    code = "output_inside_experiment"


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


def output_experiment_issues(
    output_folder: str | os.PathLike, experiment_root: str | os.PathLike | None = None
) -> list[ValidationIssue]:
    """Return ``output_inside_experiment`` when the output folder is in the experiment.

    Parameters
    ----------
    output_folder
        The output folder.
    experiment_root
        The experiment folder given; None for the one the run in the output folder recorded.
        Without either, no experiment is known and nothing is returned.

    Returns
    -------
    list of ValidationIssue
    """
    output = Path(output_folder).resolve()
    if experiment_root is None:
        manifest = usable(read_previous(output)[0])
        experiment_root = None if manifest is None else manifest.experiment_root
    return [] if experiment_root is None else check_output_folder(experiment_root, output)


def refuse_output_inside_experiment(
    output_folder: str | os.PathLike, experiment_root: str | os.PathLike | None = None
) -> None:
    """Raise :class:`OutputInsideExperimentError` for an output folder inside the experiment.

    Parameters
    ----------
    output_folder
        The output folder.
    experiment_root
        The experiment folder given; None for the one the run in the output folder recorded.

    Raises
    ------
    OutputInsideExperimentError
        If the output folder is the experiment folder or inside it.
    """
    issues = output_experiment_issues(output_folder, experiment_root)
    if issues:
        raise OutputInsideExperimentError(
            f"{issues[0].code}: {issues[0].message} {issues[0].action}"
        )
