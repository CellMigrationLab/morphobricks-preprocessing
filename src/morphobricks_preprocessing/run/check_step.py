"""``check``: the run's step stopped after the report.

``check`` discovers the experiment and checks the headers like a run (design v0.4 §1),
then writes ``discovery_report.json`` and its text version to the output folder, holding
the folder's lock while it writes. The command also writes the experiment design template,
prefilled from the folders, when the output folder holds no design
(:func:`write_check_outputs`, plan §12 question 57); it never replaces one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..axes import AxisReview
from ..axis_review import review_axes
from ..discovery import check_output_folder, default_output_folder
from ..discovery_report import RunOutcome, build_report
from ..validation import ValidationIssue, has_errors
from .design_file import write_design_template
from .lock import OutputLockedError, lock_issue, output_lock
from .preflight import Preflight, fit_calibration
from .publish import write_report


def suggested_review(pre: Preflight) -> tuple[Preflight, AxisReview | None]:
    """Review the suggested axis roles, and fit the pixel sizes to them, as ``check`` does.

    ``check`` runs before the user confirms the roles, so its report describes the images
    with the roles the headers suggest: no Z or T size for an image without that axis
    (:func:`.preflight.fit_calibration`).

    Parameters
    ----------
    pre
        The preflight.

    Returns
    -------
    tuple
        The preflight with its sizes fitted, and the review of the suggested roles; the
        preflight unchanged and None when no roles can be suggested.
    """
    if pre.suggested_mapping is None:
        return pre, None
    review = review_axes(pre.metadata, pre.suggested_mapping)
    return fit_calibration(pre, review)[0], review


def write_check_report(
    pre: Preflight,
    output_folder: str | os.PathLike | None = None,
    review: AxisReview | None = None,
) -> tuple[Path | None, ValidationIssue | None]:
    """Write the report of ``check`` (status ``"stopped"``) to the output folder.

    Parameters
    ----------
    pre
        The preflight.
    output_folder
        The output folder; defaults to :func:`.discovery.default_output_folder`.
    review
        The axis review of the suggested roles, or None.

    Returns
    -------
    tuple
        ``(path, None)`` when written; ``(None, None)`` when the output folder is inside
        the experiment (nothing is written there; the preflight reports it); ``(None,
        issue)`` when another run holds the folder's lock.
    """
    written = _write(pre, output_folder, review, with_design=False)
    return written.report, written.refusal


@dataclass(frozen=True)
class CheckOutputs:
    """What ``check`` wrote to the output folder.

    Attributes
    ----------
    report
        ``discovery_report.json``, or None when nothing was written.
    design
        ``experiment_design.csv`` when the template was written; None when the folder holds
        a design already, the folders have errors, or nothing was written.
    refusal
        ``output_locked`` when another run holds the folder's lock, else None.
    """

    report: Path | None = None
    design: Path | None = None
    refusal: ValidationIssue | None = None


def write_check_outputs(
    pre: Preflight,
    output_folder: str | os.PathLike | None = None,
    review: AxisReview | None = None,
) -> CheckOutputs:
    """Write the report of ``check``, and the design template when there is no design.

    Both are written while holding the output folder's lock. The template is prefilled
    from the folders (:func:`.design_file.write_design_template`): nothing confirmed, no
    control. It is written only when the folders have no error (as ``run`` asks for the
    design only then), and never over a design already in the folder.

    Parameters
    ----------
    pre
        The preflight.
    output_folder
        The output folder; defaults to :func:`.discovery.default_output_folder`.
    review
        The axis review of the suggested roles, or None.

    Returns
    -------
    CheckOutputs
        Nothing written when the output folder is inside the experiment (the preflight
        reports it) or another run holds its lock.
    """
    return _write(pre, output_folder, review, with_design=True)


def _write(
    pre: Preflight,
    output_folder: str | os.PathLike | None,
    review: AxisReview | None,
    *,
    with_design: bool,
) -> CheckOutputs:
    """Write the report, and the design template ``with_design``, under the lock."""
    root = pre.discovery.root
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    output = output.resolve()
    if check_output_folder(root, output):
        return CheckOutputs()
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(pre, output, pre.issues, review=review, outcome=RunOutcome("check"))
    folders_ok = not has_errors([*pre.discovery.issues, *pre.exclusion_issues])
    try:
        with output_lock(output):
            design = None
            if with_design and folders_ok:
                design = write_design_template(output, pre.dataset.repeats)
            return CheckOutputs(write_report(report, output), design)
    except OutputLockedError as exc:
        return CheckOutputs(refusal=lock_issue(exc))
