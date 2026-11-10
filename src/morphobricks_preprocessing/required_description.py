"""Every run needs a published experiment description (Spec ADR 0002 AR10, AR11).

A run without a published ``morphobricks.toml`` in its output folder is refused on every route
(the notebook, the command line's ``check``, ``run`` and ``masks``, :func:`.run.run_experiment`,
:func:`.run.mask_step.prepare_mask_step` and :class:`.run_inputs.RunInputs`) with the one code
``A_DESCRIPTION_REQUIRED``, naming the fix: describe the experiment (the notebook's describe
steps; the command line's ``describe`` shows what is still open). Nothing is taken from folder
names in its place and there is no undescribed run.
"""

from __future__ import annotations

import os
from pathlib import Path

from .description_file import DESCRIPTION_FILENAME
from .validation import ValidationIssue, error

DESCRIPTION_REQUIRED = "A_DESCRIPTION_REQUIRED"
"""The refusal of a run without a published description (ADR 0002 AR11 ruling 1)."""

FIX = (
    "Describe the experiment first: run the notebook's describe steps and save the "
    "description (Check and save), which publishes morphobricks.toml in the output folder. "
    "The command line's 'describe' shows what is still to be answered."
)
"""What the user does about it: the notebook's describe steps or the command line's ``describe``."""


def is_published(output_folder: str | os.PathLike) -> bool:
    """Return True when the output folder holds a published ``morphobricks.toml``.

    Only whether the name exists is looked at; nothing is read. A symbolic link counts: the run
    refuses it when it reads the file (``R_FILE_SYMLINK``).
    """
    return os.path.lexists(Path(output_folder) / DESCRIPTION_FILENAME)


def required_message(output_folder: str | os.PathLike) -> str:
    """Return the refusal's message for an output folder without a published description."""
    return (
        f"No published experiment description ({DESCRIPTION_FILENAME}) was found in "
        f"{Path(output_folder).as_posix()}: every run needs one ({DESCRIPTION_REQUIRED})."
    )


def description_required_issue(output_folder: str | os.PathLike) -> ValidationIssue:
    """Return the ``A_DESCRIPTION_REQUIRED`` error of an output folder, naming the fix."""
    return error(
        DESCRIPTION_REQUIRED,
        required_message(output_folder),
        [Path(output_folder).as_posix()],
        FIX,
    )
