"""The output folder's ``experiment_design.csv``: its template, and changes made under the lock.

``check`` writes the template, prefilled from the folders (:func:`write_design_template`),
when the output folder holds no design; it never replaces one. The ``design`` command
changes the design with :func:`edit_design`, which reads, changes and writes the file while
holding the output folder's lock, with the reader and validator the run uses
(:func:`.design.read_experiment_design`, :func:`.design.design_issues`).
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from ..dataset import BiologicalRepeat
from ..design import (
    DESIGN_FILENAME,
    DesignFormatError,
    ExperimentDesign,
    default_design,
    read_experiment_design,
    write_experiment_design,
)
from ..design_edit import DesignEdit, DesignEditError, apply_design_edit
from ..manifest import RunManifest, file_sha256
from ..validation import ValidationIssue, error
from .lock import output_lock
from .recorded import InputChange, design_change, usable


def write_design_template(output: Path, repeats: tuple[BiologicalRepeat, ...]) -> Path | None:
    """Write the design prefilled from the folders, unless the output folder holds one.

    The caller holds the output folder's lock. Each condition's biological-repeat folders
    are rows; folders of the same name form one repeat group; nothing is confirmed and no
    condition is a control (:func:`.design.default_design`).

    Parameters
    ----------
    output
        The output folder.
    repeats
        The biological-repeat folders found (:attr:`.dataset.DatasetReport.repeats`).

    Returns
    -------
    Path or None
        The file written; None when a design is already there (it is never replaced, also
        when it cannot be read) or the experiment has no folders.
    """
    path = output / DESIGN_FILENAME
    if not repeats or path.exists() or path.is_symlink():
        return None
    return write_experiment_design(default_design(repeats), path)


def edit_design(
    output_folder: str | os.PathLike, edit: DesignEdit, *, on: str | None = None
) -> tuple[ExperimentDesign, Path | None]:
    """Change ``experiment_design.csv`` in the output folder, holding the folder's lock.

    The file is read with the run's reader, changed (:func:`.design_edit.apply_design_edit`)
    and written back only when something changed; a refused change leaves it as it is.

    Parameters
    ----------
    output_folder
        The output folder holding ``experiment_design.csv`` (written by ``check`` or ``run``).
    edit
        The changes; an empty one only reads and checks the design.
    on
        The date of the confirmations; today by default.

    Returns
    -------
    tuple
        The design after the change, and the file written, or None when nothing changed.

    Raises
    ------
    DesignEditError
        If the output folder holds no design, or a change is refused.
    DesignFormatError
        If the file cannot be read (the message names the line and column).
    OutputLockedError
        If a run holds the output folder's lock; nothing is read or written.
    """
    output = Path(output_folder).resolve()
    path = output / DESIGN_FILENAME
    if not output.is_dir():
        raise DesignEditError(f"the output folder {output} does not exist: run 'check' first")
    with output_lock(output):
        if not path.is_file():
            raise DesignEditError(
                f"{output} holds no {DESIGN_FILENAME}: 'check' or 'run' writes it, prefilled "
                "from the experiment's folders"
            )
        current = read_experiment_design(path)
        changed = apply_design_edit(current, edit, on or date.today().isoformat())
        if changed == current:
            return changed, None
        return changed, write_experiment_design(changed, path)


def design_for_run(
    given: ExperimentDesign | None, output: Path, previous: RunManifest | None
) -> tuple[ExperimentDesign | None, list[ValidationIssue], tuple[InputChange, ...]]:
    """Return the design given, else the earlier run's ``experiment_design.csv``.

    Also the change from the recorded design: a design given that differs from it, or the
    file edited since the earlier run wrote it. Moved from ``pipeline._design`` (PP-10) so
    that the notebook names the change before the run.

    Parameters
    ----------
    given
        The design given to the run, or None.
    output
        The output folder.
    previous
        The earlier run's manifest, or None.

    Returns
    -------
    tuple
        The design the run uses (or None), the error ``design_unreadable`` when the earlier
        run's design cannot be read and none is given, and the change of the design.
    """
    path = output / DESIGN_FILENAME
    recorded = usable(previous)
    if recorded is None or recorded.experiment_design_sha256 is None or not path.exists():
        return given, [], ()
    edited = file_sha256(path) != recorded.experiment_design_sha256
    try:
        on_file = read_experiment_design(path)
    except DesignFormatError as exc:
        if given is not None:
            return given, [], design_change(None, given, edited=True)
        return (
            None,
            [
                error(
                    "design_unreadable",
                    f"The experiment design of the earlier run cannot be read: {exc}",
                    [path.as_posix()],
                    "Correct the file, or delete it to confirm the design again.",
                )
            ],
            (),
        )
    used = on_file if given is None else given
    return used, [], design_change(on_file, used, edited=edited)
