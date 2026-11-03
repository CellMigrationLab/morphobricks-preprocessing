"""Pass 2: :func:`run_experiment`, which checks everything and writes the table.

The run is one step (design v0.4 §1): discover, header checks, provisional report, stop on
errors, histograms with the pixel checks, dataset-wide checks, final report, publish. It
holds the output folder's lock throughout (:mod:`.lock`), takes every input it is not
given from the earlier run (:mod:`.recorded`), and writes its report on every run,
including runs that stop (:mod:`..discovery_report`). Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.csv_io import OUTPUT_FILENAME, write_plane_histograms
from morphobricks_normalization.ranges import HistogramSettings

from ..axes import AxisMapping
from ..axis_check import ConfirmedAxes
from ..design import (
    DESIGN_FILENAME,
    ExperimentDesign,
    write_experiment_design,
)
from ..discovery import check_output_folder, default_output_folder
from ..discovery_report import (
    REPORT_FILENAME,
    REPORT_TEXT_FILENAME,
    WRITTEN,
    DiscoveryReport,
    RunOutcome,
    build_report,
)
from ..image_type import check_image_type
from ..intensity import IntensityReport, check_intensities
from ..manifest import (
    MANIFEST_FILENAME,
    RunManifest,
    RunSettings,
    file_sha256,
    package_versions,
    write_manifest,
)
from ..mask_record import mask_manifest
from ..mask_scan import MASK_REPORT_FILENAME
from ..planes import DEFAULT_MEMORY_LIMIT
from ..validation import ValidationIssue, has_errors, warning
from .design_file import design_for_run
from .generation import staged_generation
from .lock import OutputLockedError, lock_issue, output_lock
from .modes import FULL, RESTART, RunMode, run_mode
from .output_guard import output_masks_issues
from .preflight import Preflight, fit_calibration, preflight
from .publish import write_report
from .recorded import (
    GivenInputs,
    ResolvedInputs,
    calibration_change,
    resolve_inputs,
    usable,
)
from .result import RunResult
from .resume import changed_issue, changed_since
from .reuse import read_previous, recorded_experiment_root
from .rows import _pixel_issues, _range_issues, _row_order
from .staging import clear_staging
from .steps import Checked, Rows, compute_rows, counts, header_checks, outcome_of, unreadable_issue

logger = logging.getLogger(__name__)


def run_experiment(  # noqa: PLR0913
    experiment_root: str | os.PathLike | None = None,
    mapping: AxisMapping | ConfirmedAxes | None = None,
    *,
    output_folder: str | os.PathLike | None = None,
    bin_count: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    missing_as_singleton: bool | None = None,
    exclude: Iterable[str] | None = None,
    mode: str | RunMode = RESTART,
    progress: Callable[[int, int, str], None] | None = None,
    design: ExperimentDesign | None = None,
    masks_folder: str | os.PathLike | Literal[False] | None = None,
    mask_axes: Mapping[str, str] | None = None,
    image_type: str | None = None,
    intensity_settings: IntensityCheckSettings | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> RunResult:
    """Pass 2: write or update the histogram table, the run manifest and the report.

    Every input left as None is taken from the earlier run in the output folder (design
    v0.4 §1), or is the default without one. A value given that differs from the
    recorded one is used, and the report names the change and its effect.

    Parameters
    ----------
    experiment_root
        The experiment folder. It is never written to. None: the one the earlier run in
        ``output_folder`` recorded.
    mapping
        The experiment axis mapping the user confirmed: for every file the run finds, or,
        as a :class:`.axis_check.ConfirmedAxes`, only for the files it names (a plain stack
        outside them is the error ``plain_stack_not_confirmed``). None: the recorded
        mapping, confirmed only for the plain stacks the earlier run read, as they were
        then; without an earlier run, the error ``axes_not_confirmed``.
    output_folder
        Where to write; defaults to :func:`default_output_folder`. Created if
        needed; must not be inside the experiment.
    bin_count
        Bins per plane; the pixel type's default if None and none was recorded.
    memory_limit
        Working-memory limit per plane, in bytes.
    missing_as_singleton
        The user confirmed that files lacking some axes have one plane along them.
    exclude
        Relative paths of images to leave out. Their rows from earlier runs are
        removed.
    mode
        ``"restart"`` (default) reuses the rows of unchanged files; ``"full"`` recomputes
        every row. Both keep the user's confirmations and publish only on success. A file
        is unchanged when its fingerprint is (size and modification time; for OME-Zarr its
        inventory): a rewrite of the same size with the time set back is not seen. A
        :class:`.modes.RunMode` with ``verify_content=True`` also records each source's
        content SHA-256 and reuses rows only for the same content (``None``: as recorded;
        turning it on or off recomputes every row).
    progress
        Called as ``progress(done, total, relative_path)`` before each file is
        read, and as ``progress(total, total, "")`` when reading is finished.
    design
        The experiment design the user confirmed (None: the ``experiment_design.csv``
        of the earlier run). It is brought up to date with the folders
        (:func:`.design.reconcile_design`); the run stops with ``design_not_confirmed``
        while a repeat group is not confirmed, or with ``repeat_group_conflict``. It is
        written to ``experiment_design.csv`` with the table, and its SHA-256 recorded in
        the manifest. Without any design nothing is asked and the manifest records none.
    masks_folder, mask_axes
        As :func:`preflight`; the label images of OME-Zarr images are always used.
        ``masks_folder=False`` drops the recorded masks folder. ``mask_axes`` add to the
        recorded statements, replacing those of the same mask type. An error about the
        masks stops the run. Their pixels are not read: the mask step
        (:func:`.mask_scan.scan_masks`) does that.
    image_type
        ``"fluorescence"`` or ``"rgb"`` (:mod:`.image_type`), recorded in the manifest.
        ``"rgb"`` for images that are not three channels of an integer type is the error
        ``rgb_not_possible``. It changes no row.
    intensity_settings
        Limits of the intensity checks run on the written table
        (:func:`.intensity.check_intensities`). They change no row.
    histogram_settings
        How histograms that are not exact are binned, and the limits of the range check
        (normalization contract §1.4). Changing it recomputes every row.

    Returns
    -------
    RunResult
        If any error is found, only the report is written and ``written`` is False.

    Raises
    ------
    ValueError
        For an unknown mode or image type, a ``missing_as_singleton`` that is not True or
        False, or no experiment folder given or recorded.
    """
    _check_arguments(mode, image_type, missing_as_singleton)
    chosen = run_mode(mode)
    mode = chosen.name
    output, root = _folders(experiment_root, output_folder)
    inside_masks = output_masks_issues(output, masks_folder)
    if check_output_folder(root, output) or inside_masks:
        # Nothing is written inside the experiment or the masks: not the lock, not the report.
        pre = preflight(root, output, exclude=exclude or ())
        issues = _sorted([*pre.issues, *inside_masks])
        return RunResult(output, False, issues, excluded=pre.excluded)
    given = GivenInputs(
        experiment_root=root,
        mapping=mapping,
        exclude=None if exclude is None else tuple(exclude),
        masks_folder=_masks_argument(masks_folder),
        mask_axes=mask_axes,
        image_type=image_type,
        bin_count=bin_count,
        missing_as_singleton=missing_as_singleton,
        intensity_settings=intensity_settings,
        histogram_settings=histogram_settings,
        verify_content=chosen.verify_content,
    )
    output.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(output):
            return _locked_run(given, output, _Options(mode, memory_limit, progress, design))
    except OutputLockedError as exc:
        return RunResult(output, False, (lock_issue(exc),), mode=mode)


@dataclass(frozen=True)
class _Options:
    """The run's options that are not recorded inputs."""

    mode: str
    memory_limit: int
    progress: Callable[[int, int, str], None] | None
    design: ExperimentDesign | None


@dataclass(frozen=True)
class _Run:
    """What one run works with once its header checks are done."""

    output: Path
    pre: Preflight
    inputs: ResolvedInputs
    mode: str
    checked: Checked
    previous: tuple[RunManifest | None, str | None]
    found: frozenset[str]
    """The names in the output folder before the run wrote anything."""


def _locked_run(given: GivenInputs, output: Path, options: _Options) -> RunResult:
    """Run every step while holding the output folder's lock."""
    found = frozenset(path.name for path in output.iterdir())
    previous = read_previous(output)
    inputs = resolve_inputs(given, previous[0])
    # Each header bound to its image's fingerprint (issue #90): the rows must describe
    # the file whose header was checked.
    pre = preflight(
        inputs.experiment_root,
        output,
        exclude=inputs.exclude,
        masks_folder=inputs.masks_folder,
        mask_axes=inputs.mask_axes or None,
        bind_headers=True,
    )
    design, design_problems, design_changed = design_for_run(options.design, output, previous[0])
    checked = header_checks(pre, inputs, design, options.memory_limit)
    checked.issues.extend(design_problems)
    if checked.review is not None:
        pre, absent = fit_calibration(pre, checked.review)
        checked.issues.extend(absent)
    calibrated = calibration_change(usable(previous[0]), pre.calibration)
    inputs = replace(inputs, changes=(*inputs.changes, *design_changed, *calibrated))
    run = _Run(output, pre, inputs, options.mode, checked, previous, found)
    recorded = usable(previous[0])
    base = RunOutcome(
        step="run",
        mode=options.mode,
        previous_root=None if recorded is None else Path(recorded.experiment_root).as_posix(),
        changes=inputs.changes,
    )
    if has_errors(checked.issues):
        return _stop(run, base, None)
    write_report(replace(_report(run, base), provisional=True), output)
    settings = RunSettings(
        checked.bins,
        options.memory_limit,
        inputs.missing_as_singleton,
        pre.excluded,
        histogram=inputs.histogram_settings,
        verify_content=inputs.verify_content,
    )
    rows = compute_rows(pre, checked, settings, output, previous, options.mode, options.progress)
    if rows.changed:
        checked.issues.append(changed_issue(list(rows.changed)))
    if rows.failed:
        checked.issues.append(unreadable_issue(rows.failed))
    if rows.changed or rows.failed:
        return _stop(run, base, rows)
    checked.issues.extend(_pixel_issues(rows.records))
    rows.narrow.sort(key=lambda entry: (entry.plane_id, entry.channel))
    checked.issues.extend(_range_issues(rows.narrow))
    if has_errors(checked.issues):
        return _stop(run, base, rows)
    return _publish(run, base, rows, settings)


def _report(
    run: _Run, outcome: RunOutcome, intensity: IntensityReport | None = None
) -> DiscoveryReport:
    """Build the run's report, with the intensity checks once the table is checked."""
    found = [] if intensity is None else [_as_warning(issue) for issue in intensity.issues]
    return build_report(
        run.pre,
        run.output,
        [*run.checked.issues, *found],
        review=run.checked.review,
        design=run.checked.design,
        image_type=run.inputs.image_type,
        outcome=outcome,
    )


def _as_warning(issue: object) -> ValidationIssue:
    """Return an intensity check's issue as a warning of the report."""
    return warning(issue.code, issue.message, issue.items, issue.action)


def _stop(run: _Run, base: RunOutcome, rows: Rows | None) -> RunResult:
    """Write the stopped run's report and return its result; nothing else is written."""
    write_report(_report(run, outcome_of(rows, base)), run.output)
    return RunResult(
        run.output,
        False,
        _sorted(run.checked.issues),
        excluded=run.pre.excluded,
        mode=run.mode,
        input_changes=run.inputs.changes,
        report_written=True,
        **counts(rows),
    )


def _publish(run: _Run, base: RunOutcome, rows: Rows, settings: RunSettings) -> RunResult:
    """Publish the table, design, manifest and report as one generation; return the result.

    Every image is fingerprinted again just before the commit (issue #90): an image changed
    since the run checked it stops the run, and nothing is published.
    """
    output, pre, design = run.output, run.pre, run.checked.design
    records = sorted(rows.records, key=_row_order)
    # From the rows in memory: no image is read again.
    intensity = check_intensities(records, run.inputs.intensity_settings)
    masks = None if pre.masks is None else mask_manifest(pre.masks, pre.discovery.root)
    outcome = replace(outcome_of(rows, base), replaced=_replaced(run, masks))
    removes = [name for name in (MASK_REPORT_FILENAME,) if name in outcome.replaced]
    try:
        with staged_generation(output, removes) as staged:
            sha256 = _write_generation(run, rows, settings, records, (masks, intensity), staged)
            changed = changed_since(rows.entries, pre.discovery.root)
            if changed:
                raise _SourcesChangedError(changed)
            report = replace(_report(run, outcome, intensity), status=WRITTEN)
            write_report(replace(report, manifest_sha256=sha256), staged)
    except _SourcesChangedError as exc:
        run.checked.issues.append(changed_issue(exc.paths))
        return _stop(run, base, rows)
    clear_staging(output)
    logger.info(
        "Wrote %d planes (%d files read, %d resumed)",
        len(records),
        len(rows.read),
        len(rows.resumed),
    )
    return RunResult(
        output,
        True,
        _sorted(run.checked.issues),
        excluded=pre.excluded,
        plane_count=len(records),
        design=design,
        image_type=run.inputs.image_type,
        intensity=intensity,
        mode=run.mode,
        replaced=tuple(sorted(outcome.replaced)),
        input_changes=run.inputs.changes,
        report_written=True,
        **counts(rows),
    )


class _SourcesChangedError(Exception):
    """Raised inside the publication when images changed since the run checked them."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(", ".join(paths))
        self.paths = paths


def _write_generation(
    run: _Run,
    rows: Rows,
    settings: RunSettings,
    records: list,
    checks: tuple[dict | None, IntensityReport],
    staged: Path,
) -> str:
    """Write the table, design and manifest into the publication folder.

    ``checks`` are the masks entry and the intensity checks. Returns the manifest's
    SHA-256 (the report, which names it, is written once the images are checked again).
    """
    pre, design = run.pre, run.checked.design
    masks, intensity = checks
    per_file = Counter(record.source_relative_path for record in records)
    csv_path = write_plane_histograms(records, staged / OUTPUT_FILENAME)
    design_sha256 = None
    if design is not None:
        design_sha256 = file_sha256(write_experiment_design(design, staged / DESIGN_FILENAME))
    manifest = RunManifest(
        experiment_root=pre.discovery.root.as_posix(),
        dtype=pre.metadata.dtype.name,
        settings=settings,
        mapping=run.checked.review.mapping,
        files=tuple(
            replace(entry, plane_count=per_file[rel]) for rel, entry in sorted(rows.entries.items())
        ),
        csv_sha256=file_sha256(csv_path),
        versions=package_versions(),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
        experiment_design_sha256=design_sha256,
        masks=masks,
        image_type=run.inputs.image_type,
        channels_from_rgb_samples=run.checked.type_review.channels_from_rgb_samples,
        intensity_checks=intensity.settings,
        narrow_ranges=tuple(rows.narrow),
        calibration_sha256=pre.calibration.sha256,
        conversion_record_sha256=None if pre.conversion is None else pre.conversion.sha256,
    )
    manifest = _kept_time(manifest, run.previous[0])
    return file_sha256(write_manifest(manifest, staged / MANIFEST_FILENAME))


def _kept_time(manifest: RunManifest, earlier: RunManifest | None) -> RunManifest:
    """Return the manifest with the earlier run's ``written_at`` when nothing else changed.

    Later bricks pin the manifest's SHA-256 (CellPatcher, MorphoFeature): a rerun that
    changes nothing must leave its bytes as they were (cross-brick test F10). The report
    still records the rerun.
    """
    if earlier is None:
        return manifest
    same = replace(manifest, written_at=earlier.written_at)
    return same if same.to_dict() == earlier.to_dict() else manifest


def _replaced(run: _Run, masks: dict | None) -> dict[str, str]:
    """Return the earlier output files the run replaces or removes, and why.

    ``mask_relationships.json`` is removed when the masks changed since the earlier run
    (or nothing usable records them), and in ``full`` mode, which recomputes every check.
    """
    names = [OUTPUT_FILENAME, MANIFEST_FILENAME, REPORT_FILENAME, REPORT_TEXT_FILENAME]
    if run.checked.design is not None:
        names.append(DESIGN_FILENAME)
    replaced = {name: "replaced" for name in names if name in run.found}
    if MASK_REPORT_FILENAME in run.found:
        before = usable(run.previous[0])
        if run.mode == FULL:
            replaced[MASK_REPORT_FILENAME] = "removed: a full run recomputes every check"
        elif before is None or before.masks != masks:
            replaced[MASK_REPORT_FILENAME] = "removed: the masks changed since it was written"
    return replaced


def _check_arguments(mode: object, image_type: str | None, missing_as_singleton: object) -> None:
    """Refuse arguments that are not allowed, before anything is read or written."""
    run_mode(mode)
    if image_type is not None:
        check_image_type(image_type)
    # The manifest records it as a JSON boolean (contract §9).
    if missing_as_singleton is not None and not isinstance(missing_as_singleton, bool):
        raise ValueError(
            f"missing_as_singleton must be True or False, found {missing_as_singleton!r}"
        )


def _folders(
    experiment_root: str | os.PathLike | None, output_folder: str | os.PathLike | None
) -> tuple[Path, Path]:
    """Return the output folder and the experiment folder, given or recorded there."""
    if experiment_root is not None:
        root = Path(experiment_root).resolve()
        output = default_output_folder(root) if output_folder is None else output_folder
        return Path(output).resolve(), root
    output = None if output_folder is None else Path(output_folder).resolve()
    root, missing = recorded_experiment_root(output)
    if missing is not None:
        raise ValueError(f"{missing.message} {missing.action}")
    return output, root


def _masks_argument(
    masks_folder: str | os.PathLike | Literal[False] | None,
) -> Path | Literal[False] | None:
    """Return the masks folder resolved; False and None stay as they are."""
    if masks_folder is None or masks_folder is False:
        return masks_folder
    return Path(masks_folder).resolve()


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    """Return the issues with the errors first."""
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
