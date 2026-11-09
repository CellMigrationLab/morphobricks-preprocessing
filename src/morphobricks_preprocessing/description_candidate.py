"""The checks behind *Check and save*: the candidate description on its exact bytes.

Before the user saves, the candidate is written with the canonical writer and those exact bytes
are read back with the strict reader (:func:`.description.description_from_bytes`); the accepted
description then goes through the run's own checks (:func:`.run.description_run.check_description`:
more than one channel layout is ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, then the physical ``R_*``
checks against the folder and the headers, which need the confirmed axis roles). The derived
``experiment_design.csv`` (experiment-v1 section 11) is compared with the file in the output
folder now. Nothing is written: publication repeats the reader on the same bytes under the lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_file import read_file_bytes, sha256_hex
from .description_review import design_changes
from .description_run_text import reader_issue
from .description_write import canonical_bytes
from .design import DESIGN_FILENAME, DesignFormatError, ExperimentDesign, parse_design_bytes
from .design_derive import derive_design
from .physical_check import HETEROGENEOUS_LAYOUT
from .run.description_run import RunDescription, check_description
from .run.preflight import Preflight
from .validation import ValidationIssue


@dataclass(frozen=True)
class CandidateCheck:
    """What the checks found; ``physical_run`` is False when the axis roles are not confirmed."""

    description: Description | None
    refusals: tuple[ValidationIssue, ...] = ()
    run_refusals: tuple[ValidationIssue, ...] = ()
    physical_run: bool = False
    changes: tuple[str, ...] = ()

    @property
    def can_save(self) -> bool:
        """True when the strict reader accepts the exact bytes (a valid file may be saved)."""
        return self.description is not None and not self.refusals

    @property
    def heterogeneous(self) -> ValidationIssue | None:
        """The Stage 1 refusal of several channel layouts, if any (saved, but not run)."""
        return next((i for i in self.run_refusals if i.code == HETEROGENEOUS_LAYOUT), None)

    @property
    def can_run(self) -> bool:
        """True when nothing stops the run: valid, one layout, physical checks run and passed."""
        return self.can_save and self.physical_run and not self.run_refusals


def _current_design(output: Path | None) -> tuple[ExperimentDesign | None, bool]:
    """Return the ``experiment_design.csv`` in the output folder now, and whether it was read."""
    if output is None:
        return None, True
    try:
        data = read_file_bytes(output / DESIGN_FILENAME)
        return (None if data is None else parse_design_bytes(data)), True
    except (DescriptionError, DesignFormatError, OSError):
        return None, False


def check_candidate(
    candidate: Description,
    pre: Preflight,
    roles: Mapping | None,
    output: Path | None,
) -> CandidateCheck:
    """Check the candidate on its exact canonical bytes, as publication and the run will.

    Parameters
    ----------
    candidate
        The description built from the draft and the answers.
    pre
        The folder check.
    roles
        The confirmed roles by relative path (``AxisReview.file_mappings``), or None when the
        axis roles are not confirmed (the physical checks then wait, never pass).
    output
        The output folder, whose ``experiment_design.csv`` is compared; None when unknown.
    """
    data = canonical_bytes(candidate)
    try:
        parsed = description_from_bytes(data)
    except DescriptionError as exc:
        return CandidateCheck(None, tuple(reader_issue(i) for i in exc.issues))
    found = RunDescription(parsed, sha256_hex(data), ())
    checked, problems = check_description(found, pre, roles)
    before, readable = _current_design(output)
    try:
        changes = design_changes(before, derive_design(parsed))
    except DesignFormatError as exc:
        changes = [f"cannot be derived: {exc}"]
    if not readable:
        changes = ["the file in the output folder cannot be read; it is replaced", *changes]
    return CandidateCheck(parsed, (), tuple(problems), checked.physical is not None, tuple(changes))


__all__ = ["CandidateCheck", "check_candidate"]
