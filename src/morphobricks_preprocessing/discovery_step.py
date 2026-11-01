"""Parts of the one discovery-and-validation step that are new in v0.4 (contract §18).

:func:`.run.run_experiment` is the one step: discover, check the headers, write a
provisional report, stop on errors, compute the histograms, run the dataset-wide checks,
then publish the table, manifest and final report together. This module holds its v0.4
pieces, so the run module does not grow:

- the folder checks that depend on the masks folder and the exclusions
  (:func:`output_in_masks_issue`, :func:`without_excluded_named_masks`);
- what a ``full`` run replaces (:func:`full_run_replaced`);
- building and writing the step's report (:func:`step_report`, :func:`write_step_report`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from .discovery import DiscoveryResult, default_output_folder
from .discovery_report import (
    DISCOVERY_REPORT_FILENAME,
    DiscoveryReport,
    build_discovery_report,
    write_discovery_report,
)
from .manifest import MANIFEST_FILENAME
from .mask_scan import MASK_REPORT_FILENAME
from .validation import ValidationIssue, error

REFUSED_OUTPUT_CODES = frozenset({"output_inside_experiment", "output_inside_masks"})
"""Errors about the output folder itself: nothing at all is written there, not even the
report."""


def output_in_masks_issue(discovery: DiscoveryResult, output: Path, masks: Path) -> DiscoveryResult:
    """Refuse an output folder that is the masks folder or inside it.

    The brick never changes a mask, and a run replaces its own files in the output folder.

    Parameters
    ----------
    discovery
        The experiment's discovery result.
    output
        The output folder.
    masks
        The masks folder, resolved.

    Returns
    -------
    DiscoveryResult
        ``discovery``, with the error ``output_inside_masks`` added when the folders clash.
    """
    output = output.resolve()
    if output != masks and masks not in output.parents:
        return discovery
    issue = error(
        "output_inside_masks",
        "The output folder is the masks folder or inside it. Results must be kept apart from "
        "the masks, which the brick never changes.",
        [output.as_posix()],
        "Choose an output folder outside the masks folder, for example "
        f"{default_output_folder(discovery.root)}.",
    )
    return replace(discovery, issues=(*discovery.issues, issue))


def without_excluded_named_masks(
    discovery: DiscoveryResult, excluded: Iterable[str]
) -> DiscoveryResult:
    """Leave excluded files out of ``image_named_as_mask``: an excluded file is no image.

    ``image_named_as_mask`` blocks files named like masks of other images; the user may
    rename them or exclude them.

    Parameters
    ----------
    discovery
        The experiment's discovery result.
    excluded
        The excluded relative paths.

    Returns
    -------
    DiscoveryResult
        ``discovery`` with the excluded files left out of that error (and the error left
        out when no file remains).
    """
    excluded = set(excluded)
    issues = []
    for issue in discovery.issues:
        if issue.code == "image_named_as_mask":
            paths = tuple(p for p in issue.paths if p not in excluded)
            if not paths:
                continue
            if paths != issue.paths:
                issue = replace(issue, paths=paths)
        issues.append(issue)
    return replace(discovery, issues=tuple(issues))


def full_run_replaced(output: Path, copies: Sequence[str]) -> list[str]:
    """List the brick's earlier results that a ``full`` run replaces or removes.

    Only the brick's own files: the table, manifest and report (replaced), the mask
    report (removed once the new results are published), and the OME-Zarr copies recorded
    in the earlier manifest. ``experiment_design.csv`` holds the user's answers and is
    kept. Never a source image or a mask: the output folder is outside the experiment and
    the masks folder.

    Parameters
    ----------
    output
        The output folder.
    copies
        The recorded copies to remove, relative to the output folder.

    Returns
    -------
    list of str
        The files and folders, relative to the output folder, sorted.
    """
    names = (OUTPUT_FILENAME, MANIFEST_FILENAME, DISCOVERY_REPORT_FILENAME, MASK_REPORT_FILENAME)
    replaced = [name for name in names if (output / name).is_file()]
    replaced.extend(copy for copy in copies if (output / copy).exists())
    return sorted(replaced)


def step_report(
    pre,
    *,
    output: Path,
    issues: Sequence[ValidationIssue],
    review,
    mode: str,
    versions: Mapping[str, str],
    status: str,
    read: Sequence[str] = (),
    reused: Sequence[str] = (),
    replaced: Sequence[str] = (),
    manifest_sha256: str | None = None,
) -> DiscoveryReport:
    """Build the report of one step from what the run found and did.

    Parameters
    ----------
    pre
        The step's :class:`.run.Preflight`.
    output
        The output folder.
    issues
        Every issue, the intensity checks included.
    review
        The axis review, or None.
    mode
        ``"restart"``, ``"full"`` or ``"check"``.
    versions
        Package versions.
    status
        ``"stopped"``, ``"written"`` or ``"checked"``.
    read, reused, replaced
        Images read, images whose rows were kept, and earlier files replaced.
    manifest_sha256
        The SHA-256 of the manifest the report describes (published runs only).

    Returns
    -------
    DiscoveryReport
    """
    found = build_discovery_report(
        pre,
        output_folder=output,
        issues=issues,
        review=review,
        mode=mode,
        reused=reused,
        recomputed=read,
        erased=replaced,
        versions=versions,
    )
    return replace(found, status=status, manifest_sha256=manifest_sha256)


def write_step_report(
    report: DiscoveryReport, folder: Path, issues: Iterable[ValidationIssue]
) -> bool:
    """Write the report to ``folder``, unless the output folder itself is refused.

    Parameters
    ----------
    report
        The report.
    folder
        Where to write it: the output folder, or the run's staging folder.
    issues
        The run's issues; an error about the output folder (inside the experiment or the
        masks folder) means nothing is written there.

    Returns
    -------
    bool
        True if the report was written.
    """
    if any(issue.code in REFUSED_OUTPUT_CODES for issue in issues):
        return False
    write_discovery_report(report, folder / DISCOVERY_REPORT_FILENAME)
    return True
