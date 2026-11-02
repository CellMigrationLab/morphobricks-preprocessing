"""``check``: the run's step stopped after the report; it writes only the report.

``check`` discovers the experiment and checks the headers like a run (design v0.4 §1),
then writes ``discovery_report.json`` and its text version to the output folder, holding
the folder's lock while it writes. Nothing else is written.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..axes import AxisReview
from ..discovery import check_output_folder, default_output_folder
from ..discovery_report import RunOutcome, build_report
from ..validation import ValidationIssue
from .lock import OutputLockedError, lock_issue, output_lock
from .preflight import Preflight
from .publish import write_report


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
    root = pre.discovery.root
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    output = output.resolve()
    if check_output_folder(root, output):
        return None, None
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(pre, output, pre.issues, review=review, outcome=RunOutcome("check"))
    try:
        with output_lock(output):
            return write_report(report, output), None
    except OutputLockedError as exc:
        return None, lock_issue(exc)
