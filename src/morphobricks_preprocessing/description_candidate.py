"""The checks behind *Check and save*: the candidate description on its exact bytes.

Before the user saves, the candidate is written with the canonical writer and those exact bytes
are read back with the strict reader (:func:`.description.description_from_bytes`); the accepted
description then goes through the run's own checks (:func:`.run.description_run.check_description`:
more than one channel layout is ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, then the physical ``R_*``
checks against the folder and the headers, which need the confirmed axis roles). On a reopen the
candidate is also compared with the published file by the same transition checks publication
runs (:func:`.run.description_file.transition_refusals`: experiment-v1 sections 4.4a, 4.6, 4.9
and 10), so Save is never offered for a candidate that publication would refuse. The derived
``experiment_design.csv`` (experiment-v1 section 11) is compared with the one the published file
derives (the file in the output folder is an output only and is not read back, Spec ADR 0002
AR11 (3)). Nothing is written: publication repeats the reader on the same bytes under the lock.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .description import Description, from_document
from .description_file import sha256_hex
from .description_read import read_description_bytes
from .description_report import DescriptionIssue
from .description_review import design_changes
from .description_rules import Refusal
from .description_run_text import reader_issue, reader_warning
from .description_transition import Reconfirmation
from .description_write import canonical_bytes
from .design import DesignFormatError
from .design_derive import derive_design
from .physical_check import HETEROGENEOUS_LAYOUT
from .run.description_file import transition_refusals
from .run.description_run import RunDescription, check_description
from .run.preflight import Preflight
from .validation import ValidationIssue, error

_TRANSITION_TEXT = {
    Refusal.R_CONFIRMED_ON_CHANGED: (
        "The “same way?” date of replicate {id} differs from the saved one, but its folders and "
        "channels are as saved, so its confirmation is not reopened and the saved date stays "
        "(experiment-v1 4.4a).",
        "Answer “same way?” of {id} again (Undo, then Yes): the saved date is kept.",
    ),
    Refusal.R_CONFIRMED_ID_CHANGED: (
        "The confirmed ID {id} would lose or move something it held in the saved description "
        "(a folder, a channel position or a header name; experiment-v1 4.6).",
        "Undo that change. A folder that is gone is removed with Yes, remove; a channel that "
        "moved is confirmed on Channels.",
    ),
}


@dataclass(frozen=True)
class CandidateCheck:
    """What the checks found; ``physical_run`` is False when the axis roles are not confirmed.

    ``warnings`` are the strict reader's warnings of the bytes (experiment-v1 4.3 and 5: valid,
    never blocking; shown on *Check and save*, issue #252).
    """

    description: Description | None
    refusals: tuple[ValidationIssue, ...] = ()
    run_refusals: tuple[ValidationIssue, ...] = ()
    physical_run: bool = False
    changes: tuple[str, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def can_save(self) -> bool:
        """True when the strict reader and publication's transition checks accept the bytes."""
        return self.description is not None and not self.refusals

    @property
    def heterogeneous(self) -> ValidationIssue | None:
        """The Stage 1 refusal of several channel layouts, if any (saved, but not run)."""
        return next((i for i in self.run_refusals if i.code == HETEROGENEOUS_LAYOUT), None)

    @property
    def can_run(self) -> bool:
        """True when nothing stops the run: valid, one layout, physical checks run and passed."""
        return self.can_save and self.physical_run and not self.run_refusals


def transition_issue(issue: DescriptionIssue) -> ValidationIssue:
    """Return one publication refusal of the candidate as an error naming its code and path."""
    code, path = issue.code.value, issue.path
    base, _, key = path.partition(".")
    key = str(issue.value) if base == "used_ids" else key.strip('"')
    message, action = _TRANSITION_TEXT.get(
        issue.code,
        ("Publication refuses this change.", "Undo the change it names."),
    )
    shown, todo = message.format(id=key, code=code, path=path), action.format(id=key)
    return error(code, f"Saving would be refused: {code} at {path}. {shown} {todo}", (), todo)


def check_candidate(
    candidate: Description,
    pre: Preflight,
    roles: Mapping | None,
    published: Description | None = None,
    confirmations: tuple[Reconfirmation, ...] = (),
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
    published
        The published description the candidate replaces (None when there is none): its
        transition refusals are refusals here, exactly as publication raises them, and its
        derived ``experiment_design.csv`` is the one the candidate's is compared with.
    confirmations
        The user's recorded re-confirmations that publication will be given.
    """
    data = canonical_bytes(candidate)
    report = read_description_bytes(data)
    if not report.ok:
        return CandidateCheck(None, tuple(reader_issue(i) for i in report.errors))
    parsed = from_document(report.document)
    warned = tuple(reader_warning(i) for i in report.warnings)
    refused = tuple(
        transition_issue(i) for i in transition_refusals(published, parsed, confirmations)
    )
    found = RunDescription(parsed, sha256_hex(data), ())
    checked, problems = check_description(found, pre, roles)
    try:
        before = None if published is None else derive_design(published)
        changes = design_changes(before, derive_design(parsed))
    except DesignFormatError as exc:
        changes = [f"cannot be derived: {exc}"]
    physical = checked.physical is not None
    return CandidateCheck(parsed, refused, tuple(problems), physical, tuple(changes), warned)


__all__ = ["CandidateCheck", "check_candidate", "transition_issue"]
