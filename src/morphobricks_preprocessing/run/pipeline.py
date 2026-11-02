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
    DesignFormatError,
    ExperimentDesign,
    read_experiment_design,
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
from ..validation import ValidationIssue, error, has_errors, warning
from .lock import OutputLockedError, lock_issue, output_lock
from .modes import FULL, RESTART, check_mode
from .preflight import Preflight, fit_calibration, preflight
from .publish import _staged_outputs, write_report
from .recorded import (
    GivenInputs,
    InputChange,
    ResolvedInputs,
    calibration_change,
    design_change,
    resolve_inputs,
    usable,
)
from .result import RunResult
from .reuse import read_previous, recorded_experiment_root
from .rows import _pixel_issues, _range_issues, _row_order
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
    mode: str = RESTART,
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
        every row. Both keep the user's confirmations and publish only on success.
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
    output, root = _folders(experiment_root, output_folder)
    if check_output_folder(root, output):
        # Nothing is written inside the experiment: not the lock, not the report.
        pre = preflight(root, output, exclude=exclude or ())
        return RunResult(output, False, _sorted(list(pre.issues)), excluded=pre.excluded)
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
    pre = preflight(
        inputs.experiment_root,
        output,
        exclude=inputs.exclude,
        masks_folder=inputs.masks_folder,
        mask_axes=inputs.mask_axes or None,
    )
    design, design_problems, design_changed = _design(options.design, output, previous[0])
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
    )
    rows = compute_rows(pre, checked, settings, output, previous, options.mode, options.progress)
    if rows.failed:
        checked.issues.append(unreadable_issue(rows.failed))
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
    """Publish the table, design, manifest and report together, and return the result."""
    output, pre, design = run.output, run.pre, run.checked.design
    records = sorted(rows.records, key=_row_order)
    # From the rows in memory: no image is read again.
    intensity = check_intensities(records, run.inputs.intensity_settings)
    per_file = Counter(record.source_relative_path for record in records)
    root = pre.discovery.root
    masks = None if pre.masks is None else mask_manifest(pre.masks, root)
    outcome = replace(outcome_of(rows, base), replaced=_replaced(run, masks))
    with _staged_outputs(output, with_design=design is not None, with_report=True) as staged:
        csv_path = write_plane_histograms(records, staged / OUTPUT_FILENAME)
        design_sha256 = None
        if design is not None:
            design_sha256 = file_sha256(write_experiment_design(design, staged / DESIGN_FILENAME))
        manifest = RunManifest(
            experiment_root=root.as_posix(),
            dtype=pre.metadata.dtype.name,
            settings=settings,
            mapping=run.checked.review.mapping,
            files=tuple(
                replace(entry, plane_count=per_file[rel])
                for rel, entry in sorted(rows.entries.items())
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
        )
        manifest_sha256 = file_sha256(write_manifest(manifest, staged / MANIFEST_FILENAME))
        report = _report(run, outcome, intensity)
        write_report(replace(report, status=WRITTEN, manifest_sha256=manifest_sha256), staged)
    if MASK_REPORT_FILENAME in outcome.replaced:
        (output / MASK_REPORT_FILENAME).unlink(missing_ok=True)
    kept = len(records) - sum(per_file[rel] for rel in rows.read)
    logger.info("Wrote %d planes (%d files read, %d kept)", len(records), len(rows.read), kept)
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


def _design(
    given: ExperimentDesign | None, output: Path, previous: RunManifest | None
) -> tuple[ExperimentDesign | None, list[ValidationIssue], tuple[InputChange, ...]]:
    """Return the design given, else the earlier run's ``experiment_design.csv``.

    Also the change from the recorded design: a design given that differs from it, or the
    file edited since the earlier run wrote it.
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


def _check_arguments(mode: str, image_type: str | None, missing_as_singleton: object) -> None:
    """Refuse arguments that are not allowed, before anything is read or written."""
    check_mode(mode)
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
