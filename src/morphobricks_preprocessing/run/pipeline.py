"""Pass 2: :func:`run_experiment`, which checks everything and writes the table.

The run is one step (design v0.4 §1): discover, header checks, provisional report, stop on
errors, histograms with the pixel checks, dataset-wide checks, final report, publish. It holds
the output folder's lock throughout (:mod:`.lock`), takes every input it is not given from the
earlier run (:mod:`.recorded`), and writes its report on every run, including runs that stop
(:mod:`..discovery_report`). Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Literal

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.ranges import HistogramSettings

from ..axes import AxisMapping
from ..axis_check import ConfirmedAxes
from ..channel_identity import ChannelIdentityReport, check_channel_identity
from ..discovery import check_output_folder, default_output_folder
from ..discovery_report import (
    WRITTEN,
    DiscoveryReport,
    RunOutcome,
    build_report,
)
from ..intensity import IntensityReport, check_intensities
from ..manifest import (
    RunSettings,
)
from ..mask_binding import (
    changed_masks,
    content_changed_masks,
    mask_changed_issue,
    mask_contents,
)
from ..mask_record import mask_manifest
from ..mask_scan import MASK_REPORT_FILENAME
from ..planes import DEFAULT_MEMORY_LIMIT
from ..required_description import is_published, undescribed_issues
from ..source_content import content_changed
from ..validation import ValidationIssue, has_errors, warning
from .arguments import check_arguments as _check_arguments
from .arguments import masks_argument as _masks_argument
from .description_run import (
    load_description,
    run_checks,
    unit_entries,
)
from .generation import staged_generation
from .lock import OutputLockedError, lock_issue, output_lock
from .modes import RESTART, RunMode, run_mode
from .output_guard import output_masks_issues
from .outputs import replaced_files, write_generation
from .preflight import fit_calibration, preflight
from .publish import write_report
from .recorded import (
    GivenInputs,
    calibration_change,
    resolve_inputs,
    usable,
)
from .result import RunResult
from .resume import changed_issue, changed_since
from .reuse import read_previous, recorded_experiment_root
from .rows import _pixel_issues, _range_issues, _row_order
from .staging import clear_staging
from .state import RunOptions, RunState
from .steps import (
    Reporting,
    Rows,
    compute_rows,
    counts,
    header_checks,
    outcome_of,
    unreadable_issue,
)
from .workers import Workers

logger = logging.getLogger(__name__)
_Checks = tuple[IntensityReport, ChannelIdentityReport]


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

    The output folder must hold a published experiment description (``morphobricks.toml``,
    Spec ADR 0002 AR10): without one the run is refused with ``A_DESCRIPTION_REQUIRED``
    before anything is read or written (:mod:`..required_description`). The run's
    ``experiment_design.csv`` is derived from the description (:func:`.design_derive.derive_design`)
    and written with the table: it is an output only, never read back.

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
        turning it on or off recomputes every row). Its ``lab_links``: manifest only (PP-2).
        Its ``workers`` and ``max_ram``: how many images are read at once, each whole in a
        worker thread (issue #107, :mod:`.workers`); the outputs are the same with any value.
        Its ``checking``: the progress of the run's own folder and header check, before any
        image is read (:attr:`.modes.RunMode.checking`).
        With more than one worker, ``progress`` is called in this thread as each image is done.
    progress
        Called as ``progress(done, total, relative_path)`` before each file is
        read, and as ``progress(total, total, "")`` when reading is finished.
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
        If any error is found, only the report is written and ``written`` is False. Without a
        published description nothing is written (``A_DESCRIPTION_REQUIRED``).

    Raises
    ------
    ValueError
        For an unknown mode or image type, a ``missing_as_singleton`` that is not True or
        False, or no experiment folder given or recorded.
    """
    _check_arguments(mode, image_type, missing_as_singleton)
    chosen = run_mode(mode)
    mode = chosen.name
    if output_folder is not None and not is_published(output_folder):
        # Before the recorded run or any image is read: only where the folders are is said.
        issues = undescribed_issues(experiment_root, Path(output_folder).resolve(), masks_folder)
        return RunResult(Path(output_folder).resolve(), False, _sorted(issues), mode=mode)
    output, root = _folders(experiment_root, output_folder)
    if not is_published(output):  # the default output folder next to the experiment
        issues = undescribed_issues(root, output, masks_folder)
        return RunResult(output, False, _sorted(issues), mode=mode)
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
            reading = Workers(chosen.workers, chosen.max_ram)
            options = RunOptions(
                mode, memory_limit, progress, chosen.lab_links, reading, chosen.checking
            )
            return _locked_run(given, output, options)
    except OutputLockedError as exc:
        return RunResult(output, False, (lock_issue(exc),), mode=mode)


def _locked_run(given: GivenInputs, output: Path, options: RunOptions) -> RunResult:
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
        progress=options.checking,  # minutes on a large experiment: it is reported (v0.9.0)
    )
    loaded = load_description(output)
    links = options.lab_links  # provenance only (experiment-v1 9.3): checked, never a key
    header = partial(header_checks, pre, inputs, memory_limit=options.memory_limit)
    checked, described = run_checks(output, pre, loaded, header, links)
    if checked.review is not None:
        pre, absent = fit_calibration(pre, checked.review)
        checked.issues.extend(absent)
    calibrated = calibration_change(usable(previous[0]), pre.calibration)
    inputs = replace(inputs, changes=(*inputs.changes, *calibrated))
    run = RunState(
        output, pre, inputs, options.mode, checked, previous, found, {}, described, links
    )
    recorded = usable(previous[0])
    base = RunOutcome(
        step="run",
        mode=options.mode,
        previous_root=None if recorded is None else Path(recorded.experiment_root).as_posix(),
        changes=inputs.changes,
    )
    if has_errors(checked.issues):
        return _stop(run, base, None)
    if inputs.verify_content and pre.masks is not None:
        run = _with_mask_content(run)
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
    reporting = Reporting(options.progress, options.checking)
    rows = compute_rows(
        pre, checked, settings, output, previous, options.mode, reporting, options.workers
    )
    checked.issues.extend(_read_issues(rows, settings))
    if rows.refused or rows.changed or rows.failed:
        return _stop(run, base, rows)
    checked.issues.extend(_pixel_issues(rows.records))
    rows.narrow.sort(key=lambda entry: (entry.plane_id, entry.channel))
    checked.issues.extend(_range_issues(rows.narrow))
    if has_errors(checked.issues):
        return _stop(run, base, rows)
    return _publish(run, base, rows, settings)


def _read_issues(rows: Rows, settings: RunSettings) -> list[ValidationIssue]:
    """Return the errors of reading the images: refused for ``max_ram``, changed, unreadable."""
    issues = [] if rows.refused is None else [rows.refused]
    if rows.changed:
        compared = "fingerprint or content" if settings.verify_content else "fingerprint"
        issues.append(changed_issue(list(rows.changed), compared))
    if rows.failed:
        issues.append(unreadable_issue(rows.failed, rows.failures))
    return issues


def _with_mask_content(run: RunState) -> RunState:
    """Return the run with each mask's content SHA-256 (``verify_content``, issue #160).

    Taken once the header checks pass, of the masks as fingerprinted with their headers: a
    mask that cannot be hashed, or no longer has that fingerprint, adds
    ``mask_changed_during_run`` to the run's issues.
    """
    masks, root = run.pre.masks, run.pre.discovery.root
    run = replace(run, mask_content=mask_contents(masks, root))
    unsettled = {path for path, sha in run.mask_content.items() if sha is None}
    unsettled.update(changed_masks(masks, root))
    if unsettled:
        run.checked.issues.append(mask_changed_issue(sorted(unsettled), "fingerprint or content"))
    return run


def _report(run: RunState, outcome: RunOutcome, checks: _Checks | None = None) -> DiscoveryReport:
    """Build the run's report, with the intensity and channel checks once the table is checked."""
    found = []
    if checks is not None:
        found = [*map(_as_warning, checks[0].issues), *checks[1].validation_issues()]
    return build_report(
        run.pre,
        run.output,
        [*run.checked.issues, *found],
        review=run.checked.review,
        design=run.checked.design,
        image_type=run.inputs.image_type,
        outcome=outcome,
        unit_records=None if run.description is None else unit_entries(run.description),
    )


def _as_warning(issue: object) -> ValidationIssue:
    """Return an intensity check's issue as a warning of the report."""
    return warning(issue.code, issue.message, issue.items, issue.action)


def _stop(run: RunState, base: RunOutcome, rows: Rows | None) -> RunResult:
    """Write the stopped run's report and return its result; no scientific output is published."""
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


def _publish(run: RunState, base: RunOutcome, rows: Rows, settings: RunSettings) -> RunResult:
    """Publish the table, design, manifest and report as one generation; return the result.

    Every image is fingerprinted again just before the commit (issue #90), and in a strict
    run hashed again as the last step before it (:func:`_final_content_check`): an image
    changed since the run checked it stops the run, and no scientific output is published.
    Every mask likewise (issue #160): ``mask_changed_during_run``.
    """
    output, pre, design = run.output, run.pre, run.checked.design
    records = sorted(rows.records, key=_row_order)
    # From the rows in memory: no image is read again.
    intensity = check_intensities(records, run.inputs.intensity_settings)
    identity = check_channel_identity(intensity.summaries)  # its default limits, recorded
    masks = None if pre.masks is None else mask_manifest(pre.masks, pre.discovery.root)
    outcome = replace(outcome_of(rows, base), replaced=replaced_files(run, masks))
    removes = [name for name in (MASK_REPORT_FILENAME,) if name in outcome.replaced]
    final_check = _final_content_check(run, rows, settings)
    try:
        with staged_generation(output, removes, final_check) as staged:
            sha256 = write_generation(
                run, rows, settings, records, (masks, intensity, identity), staged
            )
            changed = changed_since(rows.entries, pre.discovery.root)
            if changed:
                raise _SourcesChangedError(changed)
            # Every mask still the one whose header the run checked (issue #160).
            changed = [] if pre.masks is None else changed_masks(pre.masks, pre.discovery.root)
            if changed:
                raise _SourcesChangedError(changed, masks=True)
            report = replace(_report(run, outcome, (intensity, identity)), status=WRITTEN)
            write_report(replace(report, manifest_sha256=sha256), staged)
    except _SourcesChangedError as exc:
        issue = (mask_changed_issue if exc.masks else changed_issue)(exc.paths, exc.compared)
        run.checked.issues.append(issue)
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
        channel_identity=identity,
        mode=run.mode,
        replaced=tuple(sorted(outcome.replaced)),
        input_changes=run.inputs.changes,
        report_written=True,
        **counts(rows),
    )


def _final_content_check(
    run: RunState, rows: Rows, settings: RunSettings
) -> Callable[[], None] | None:
    """Return a run's last check before its commit, or None when there is nothing to check.

    Every run fingerprints each mask once more after its generation is staged, right before
    the manifest is replaced (:func:`.generation.staged_generation`; issue #198): a mask
    changed since the check in :func:`_publish` stops the run with
    ``mask_changed_during_run``, also when ``verify_content`` is off.

    A ``verify_content`` run also hashes every recorded image (:func:`content_changed`, the
    OME-Zarr store digest for a store) and every mask (``mask_changed_during_run``, issue
    #160): one rewritten since its last check, even with its size and time set back, stops
    the run with ``source_changed_during_run``, and the earlier generation is intact. It
    reads every analysed image and mask whole once more.
    """
    root, masks = run.pre.discovery.root, run.pre.masks
    if masks is None and not settings.verify_content:
        return None

    def check() -> None:
        """Raise when an image's or mask's fingerprint or content is not the one taken earlier."""
        if masks is not None:
            changed = changed_masks(masks, root)
            if changed:
                raise _SourcesChangedError(changed, masks=True)
        if not settings.verify_content:
            return
        changed = content_changed(rows.entries.values(), root)
        if changed:
            raise _SourcesChangedError(changed, "content")
        changed = [] if masks is None else content_changed_masks(masks, root, run.mask_content)
        if changed:
            raise _SourcesChangedError(changed, "content", masks=True)

    return check


class _SourcesChangedError(Exception):
    """Raised inside the publication when images or masks changed since the run checked them."""

    def __init__(
        self, paths: list[str], compared: str = "fingerprint", *, masks: bool = False
    ) -> None:
        super().__init__(", ".join(paths))
        self.paths = paths
        self.compared = compared  # a key of resume.CHANGE_TEXTS (mask_binding's for masks)
        self.masks = masks


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


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    """Return the issues with the errors first."""
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
