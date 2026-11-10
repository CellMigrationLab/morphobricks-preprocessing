"""``check``: the run's step stopped after the report.

``check`` discovers the experiment and checks the headers like a run (design v0.4 §1),
then writes ``discovery_report.json`` and its text version to the output folder, holding
the folder's lock while it writes (:func:`write_check_report`). The command line's ``check``
refuses an output folder without a published description first (``A_DESCRIPTION_REQUIRED``).
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ..axes import AxisReview
from ..axis_review import review_axes
from ..discovery import check_output_folder, default_output_folder
from ..discovery_report import RunOutcome, build_report
from ..validation import ValidationIssue
from .lock import OutputLockedError, lock_issue, output_lock
from .output_guard import output_masks_issues
from .preflight import Preflight, fit_calibration
from .publish import write_report


def suggested_review(
    pre: Preflight, *, missing_as_singleton: bool = False
) -> tuple[Preflight, AxisReview | None]:
    """Review the suggested axis roles, and fit the pixel sizes to them, as ``check`` does.

    ``check`` runs before the user confirms the roles, so its report describes the images
    with the roles the headers suggest: no Z or T size for an image without that axis
    (:func:`.preflight.fit_calibration`). Images lacking some of the suggested axes are
    checked as the run checks them (:func:`.axis_review.review_axes`): the metadata's
    ``missing_axes`` error is replaced by the review's ``missing_axes`` or
    ``ambiguous_missing_axes``, which ``missing_as_singleton`` answers as it does for
    ``run`` (F10, matrix run of 3 November 2026). The review's other issues are about
    roles not confirmed yet, so ``check`` leaves them to ``run``.

    Parameters
    ----------
    pre
        The preflight.
    missing_as_singleton
        The user confirmed that images lacking some of the suggested mapping's axes have
        one plane along them (``check --missing-as-singleton``, the notebook's answer).

    Returns
    -------
    tuple
        The preflight with its sizes fitted and the arrangement issues of the review, and
        the review of the suggested roles; the preflight unchanged and None when no roles
        can be suggested.
    """
    if pre.suggested_mapping is None:
        return pre, None
    review = review_axes(
        pre.metadata, pre.suggested_mapping, missing_as_singleton=missing_as_singleton
    )
    fitted = fit_calibration(pre, review)[0]
    found = fitted.metadata.issues
    if not any(issue.code == MISSING_AXES for issue in found):
        return fitted, review
    kept = [issue for issue in found if issue.code != MISSING_AXES]
    missing = [issue for issue in review.issues if issue.code in _MISSING_CODES]
    issues = sorted([*kept, *missing], key=lambda issue: not issue.is_error)
    return replace(fitted, metadata=replace(fitted.metadata, issues=tuple(issues))), review


MISSING_AXES = "missing_axes"
"""The error of images lacking some of the experiment's axes, unless confirmed."""

_MISSING_CODES = frozenset({MISSING_AXES, "ambiguous_missing_axes"})
"""The review's answer to images lacking some axes (:func:`.axis_review.review_axes`)."""


def write_check_report(
    pre: Preflight,
    output_folder: str | os.PathLike | None = None,
    review: AxisReview | None = None,
) -> tuple[Path | None, ValidationIssue | None]:
    """Write the report of ``check`` (status ``"stopped"``) to the output folder.

    It is written while holding the output folder's lock. ``check`` writes no experiment
    design: ``experiment_design.csv`` is derived from the published description by the run.

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
        issue)`` when another run holds the folder's lock (``output_locked``) or a masks folder
        recorded by the run in the output folder holds it (``output_inside_masks``; the
        preflight does not know that folder).
    """
    root = pre.discovery.root
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    output = output.resolve()
    masks = pre.masks.folder if pre.masks is not None else None
    inside_masks = output_masks_issues(output, masks)
    if check_output_folder(root, output) or inside_masks:
        # Nothing is written; the refusal is returned unless the preflight reports it (a
        # masks folder recorded by the run in the output folder, not given to 'check').
        reported = {issue.code for issue in pre.issues}
        return None, next((i for i in inside_masks if i.code not in reported), None)
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(pre, output, pre.issues, review=review, outcome=RunOutcome("check"))
    try:
        with output_lock(output):
            return write_report(report, output), None
    except OutputLockedError as exc:
        return None, lock_issue(exc)
