"""The steps of :func:`.pipeline.run_experiment` before publishing (design v0.4 §1).

The run is one step in this order: discover, header checks, provisional report, stop on
errors, histograms with the pixel checks, dataset-wide checks, final report, publish. This
module holds the header checks (:func:`header_checks`), reading the rows
(:func:`compute_rows`) and what the run did with the earlier rows (:func:`outcome_of`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from morphobricks_normalization.records import PlaneRecord

from ..axes import AxisReview
from ..axis_review import review_axes
from ..dataset import check_dataset
from ..design import DesignTable, design_issues, reconcile_design
from ..discovery_report import RunOutcome
from ..fingerprint import unchanged_confirmation
from ..image_type import ImageTypeReview, image_type_issues, review_image_type
from ..manifest import FileEntry, NarrowRange, RunManifest, RunSettings
from ..mask_checks import mask_issues
from ..planes import check_planes
from ..source_content import with_content
from ..validation import ValidationIssue, error, has_errors, plural
from .entries import Comparison, compare_with_previous, file_entries
from .modes import FULL
from .preflight import Preflight
from .recorded import ResolvedInputs
from .resume import FileJob, changed_during_check, new_rows
from .reuse import _load_previous
from .rows import _bin_count


@dataclass(frozen=True)
class Checked:
    """The header checks of a run.

    Attributes
    ----------
    review, type_review
        The axis and image-type reviews; None when no axis roles were given or recorded.
    design
        The derived design brought up to date with the folders, or None.
    issues
        Every issue found so far.
    bins
        Bins per plane, or None when the pixel type is unknown.
    """

    review: AxisReview | None
    type_review: ImageTypeReview | None
    design: DesignTable | None
    issues: list[ValidationIssue] = field(default_factory=list)
    bins: int | None = None


def header_checks(
    pre: Preflight,
    inputs: ResolvedInputs,
    design: DesignTable | None,
    memory_limit: int,
) -> Checked:
    """Check everything that needs no pixel: folders, axes, image type, design, masks.

    Parameters
    ----------
    pre
        The run's preflight.
    inputs
        The run's resolved inputs.
    design
        The design derived from the published description, or None.
    memory_limit
        Working-memory limit per plane, in bytes.

    Returns
    -------
    Checked
    """
    if inputs.mapping is None:
        return Checked(None, None, design, [*pre.issues, _no_roles_issue()])
    root = pre.discovery.root
    # A plain stack changed since its roles were confirmed is not covered (audit 2 B-1).
    mapping = unchanged_confirmation(inputs.mapping, root)
    review = review_axes(pre.metadata, mapping, missing_as_singleton=inputs.missing_as_singleton)
    type_review = review_image_type(pre.metadata, review)
    dataset, design_problems = pre.dataset, []
    if design is not None:
        design = reconcile_design(design, pre.dataset.repeats)
        design_problems = design_issues(design)
        dataset = check_dataset(pre.discovery, design.repeat_groups(), excluded=pre.excluded)
    issues = [
        *pre.discovery.issues,
        *pre.exclusion_issues,
        *review.combined_issues(pre.metadata),
        *image_type_issues(inputs.image_type, type_review),
        *design_problems,
        *dataset.issues,
        *(mask_issues(pre.masks, pre.metadata, review) if pre.masks is not None else ()),
        *(pre.conversion.issues if pre.conversion is not None else ()),
    ]
    bins = _bins(pre, inputs, issues)
    if not has_errors(issues):
        issues.extend(
            check_planes(
                root,
                review,
                memory_limit=memory_limit,
                bin_count=bins,
                histogram_settings=inputs.histogram_settings,
            )
        )
    return Checked(review, type_review, design, issues, bins)


def _bins(pre: Preflight, inputs: ResolvedInputs, issues: list[ValidationIssue]) -> int | None:
    """Return the bins per plane, adding ``invalid_bin_count`` to ``issues`` when refused.

    A bin count recorded for another pixel type is not reused: the new type's default is.
    """
    dtype = pre.metadata.dtype
    if dtype is None or has_errors(issues):
        return None
    asked = inputs.bin_count
    if inputs.bins_recorded_for not in (None, dtype.name):
        asked = None
    bins, bin_issue = _bin_count(dtype, asked)
    issues.extend(bin_issue)
    return bins


def _no_roles_issue() -> ValidationIssue:
    """Return the error for a run given no axis roles, with none recorded."""
    return error(
        "axes_not_confirmed",
        "No axis roles were given, and the output folder holds no earlier run that records them.",
        action="Confirm the role of each axis (the CLI: give --axes; 'check' prints the "
        "suggested value).",
    )


@dataclass(frozen=True)
class Rows:
    """The rows of a run, and which files were read and why.

    Attributes
    ----------
    records, narrow
        The table's rows and the channel planes whose range check failed.
    found
        The files compared with the earlier run's.
    read
        The files whose pixels were read in this run.
    reason
        Why every row was recomputed, or None.
    failed
        Files whose pixels could not be read.
    entries
        The manifest entry of every analysed file, as it is now.
    resumed
        The files whose rows an interrupted run had staged, reused (design v0.4 §1).
    changed
        The files that changed while the run checked or read them (issue #90).
    discarded
        ``{file: why}`` for the staged rows not reused.
    """

    records: list[PlaneRecord]
    narrow: list[NarrowRange]
    found: Comparison
    read: tuple[str, ...]
    reason: str | None
    failed: tuple[str, ...] = ()
    entries: dict[str, FileEntry] = field(default_factory=dict)
    resumed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    discarded: Mapping[str, str] = field(default_factory=dict)


def compute_rows(
    pre: Preflight,
    checked: Checked,
    settings: RunSettings,
    output: Path,
    previous: tuple[RunManifest | None, str | None],
    mode: str,
    progress: Callable[[int, int, str], None] | None,
) -> Rows:
    """Reuse the earlier rows that can be reused, resume staged rows, and read the rest.

    Parameters
    ----------
    pre
        The run's preflight.
    checked
        Its header checks (without errors).
    settings
        The run's settings.
    output
        The output folder.
    previous
        :func:`.reuse.read_previous`'s answer.
    mode
        ``"restart"`` or ``"full"``.
    progress
        As :func:`.pipeline.run_experiment`.

    Returns
    -------
    Rows
    """
    review, root = checked.review, pre.discovery.root
    dtype = pre.metadata.dtype.name
    earlier, reason = _load_previous(output, previous, settings, review.mapping, dtype, mode)
    in_blocks = {p for i in checked.issues if i.code == "planes_read_in_blocks" for p in i.paths}
    entries = file_entries(pre, review, root, in_blocks)
    # Strict reuse: each image's content SHA-256 joins its row key; an unreadable one fails.
    entries, unhashed = with_content(entries, root) if settings.verify_content else (entries, [])
    found = compare_with_previous(entries, previous[0], earlier, reason is not None, settings, root)
    records: list[PlaneRecord] = []
    narrow: list[NarrowRange] = []
    for rel in found.unchanged if not reason else ():
        records.extend(earlier[rel])
        # Kept rows keep the range checks they were written with.
        narrow.extend(entry for entry in previous[0].narrow_ranges if entry.relative_path == rel)
    job = FileJob(pre, review, settings, entries, output, mode == FULL)
    changed = changed_during_check(job)
    to_read = [
        rel
        for rel in sorted([*found.new, *found.changed, *(found.unchanged if reason else ())])
        if rel not in changed and rel not in unhashed
    ]
    kept = set(found.unchanged) if not reason else set()
    new = new_rows(job, to_read, kept, progress)
    return Rows(
        [*records, *new.records],
        [*narrow, *new.narrow],
        found,
        tuple(new.read),
        reason,
        tuple(sorted({*unhashed, *new.failed})),
        entries,
        resumed=tuple(new.resumed),
        changed=tuple(sorted({*changed, *new.changed})),
        discarded=dict(sorted(new.discarded.items())),
    )


def unreadable_issue(failed: tuple[str, ...]) -> ValidationIssue:
    """Return the error naming the files whose pixels could not be read."""
    return error(
        "unreadable_planes",
        f"The pixel data of {plural(len(failed), 'image')} could not be read, although "
        "the file headers could. The files may be damaged or incomplete.",
        failed,
        "Export the images again, or exclude them from the run to continue without them.",
    )


def outcome_of(rows: Rows | None, base: RunOutcome) -> RunOutcome:
    """Return what the run did with the earlier rows: reused, recomputed and why, removed.

    Parameters
    ----------
    rows
        The run's rows, or None when it stopped before reading.
    base
        The outcome so far (step, mode, earlier root, input changes).

    Returns
    -------
    RunOutcome
    """
    if rows is None:
        return base
    found = rows.found
    why = {rel: "new since the earlier run" for rel in found.new}
    why.update({rel: "changed since the earlier run" for rel in found.changed})
    recomputed = {rel: rows.reason or why.get(rel, "") for rel in rows.read}
    reused = () if rows.reason else found.unchanged
    return replace(
        base,
        reused=reused,
        recomputed=recomputed,
        removed=found.removed,
        resumed=rows.resumed,
        staged_discarded=rows.discarded,
    )


def counts(rows: Rows | None) -> dict:
    """Return the :class:`.result.RunResult` fields that come from the rows."""
    if rows is None:
        return {}
    return {
        **asdict(rows.found),
        "rebuild_reason": rows.reason,
        "read": rows.read,
        "resumed": rows.resumed,
    }
