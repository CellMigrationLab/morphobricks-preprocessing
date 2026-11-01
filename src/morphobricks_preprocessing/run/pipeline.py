"""Pass 2: :func:`run_experiment`, which checks everything and writes the table.

Moved from ``run.py`` (PP-3).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.csv_io import (
    OUTPUT_FILENAME,
    write_plane_histograms,
)
from morphobricks_normalization.ranges import HistogramSettings
from morphobricks_normalization.records import PlaneRecord

from ..axes import (
    AxisMapping,
)
from ..axis_review import review_axes
from ..dataset import check_dataset
from ..design import (
    DESIGN_FILENAME,
    ExperimentDesign,
    design_issues,
    reconcile_design,
    write_experiment_design,
)
from ..discovery import default_output_folder
from ..image_type import check_image_type, image_type_issues, review_image_type
from ..intensity import check_intensities
from ..manifest import (
    MANIFEST_FILENAME,
    FileEntry,
    NarrowRange,
    RunManifest,
    RunSettings,
    file_entry_for,
    file_sha256,
    package_versions,
    write_manifest,
)
from ..mask_checks import mask_issues
from ..planes import (
    DEFAULT_MEMORY_LIMIT,
    PlaneReadError,
    check_planes,
)
from ..validation import ValidationIssue, error, has_errors, plural
from .preflight import preflight
from .publish import _staged_outputs
from .result import RunResult
from .reuse import _load_previous
from .rows import _bin_count, _pixel_issues, _range_issues, _read_file, _row_order

logger = logging.getLogger(__name__)


def run_experiment(  # noqa: C901, PLR0913, PLR0915
    experiment_root: str | os.PathLike,
    mapping: AxisMapping,
    *,
    output_folder: str | os.PathLike | None = None,
    bin_count: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    missing_as_singleton: bool = False,
    exclude: Iterable[str] = (),
    force_rebuild: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
    design: ExperimentDesign | None = None,
    masks_folder: str | os.PathLike | None = None,
    ome_zarr_labels: bool = False,
    image_type: str | None = None,
    intensity_settings: IntensityCheckSettings | None = None,
    histogram_settings: HistogramSettings | None = None,
) -> RunResult:
    """Pass 2: write or update the histogram table and the run manifest.

    Parameters
    ----------
    experiment_root
        The experiment folder. It is never written to.
    mapping
        The experiment axis mapping the user confirmed.
    output_folder
        Where to write; defaults to :func:`default_output_folder`. Created if
        needed; must not be inside the experiment.
    bin_count
        Bins per plane; the pixel type's default if None.
    memory_limit
        Working-memory limit per plane, in bytes.
    missing_as_singleton
        The user confirmed that files lacking some axes have one plane along them.
    exclude
        Relative paths of images to leave out. Their rows from earlier runs are
        removed.
    force_rebuild
        Recompute every row even if the earlier output is up to date.
    progress
        Called as ``progress(done, total, relative_path)`` before each file is
        read, and as ``progress(total, total, "")`` when reading is finished.
    design
        The experiment design the user confirmed: how biological repeats pair
        across conditions, and the controls. It is brought up to date with the
        folders (:func:`.design.reconcile_design`); the run stops with
        ``design_not_confirmed`` while a repeat group is not confirmed, or with
        ``repeat_group_conflict``. It is written to ``experiment_design.csv``
        with the table, and its SHA-256 recorded in the manifest. Without a
        design nothing is asked, the manifest records none, and an existing
        ``experiment_design.csv`` is left as it is.
    masks_folder, ome_zarr_labels
        As :func:`preflight`. The masks are recorded in the manifest (``masks``,
        contract §9); an error about them stops the run, including a mask whose
        depth slices and time points are not its image's under the confirmed
        roles (``mask_planes_mismatch``). Their pixels are not read: the mask
        step (:func:`.mask_scan.scan_masks`) does that.
    image_type
        The image type the user confirmed, ``"fluorescence"`` or ``"rgb"``
        (:mod:`.image_type`), recorded in the manifest. ``"rgb"`` for images that
        are not three channels of an integer type is the error
        ``rgb_not_possible``. It changes no row, so changing it recomputes
        nothing. Without it the manifest records none.
    intensity_settings
        Limits of the intensity checks run on the written table
        (:func:`.intensity.check_intensities`); None uses the defaults. They are
        recorded in the manifest and change no row.
    histogram_settings
        How histograms that are not exact are binned: the minimum to maximum
        (default) or the robust range, and the limits of the range check
        (normalization contract §1.4). Exact 8- and 16-bit histograms are the
        same either way. Recorded in the manifest; changing it recomputes every
        row. After the histograms, a ``narrow_histogram_range`` warning names
        each channel whose central range (1st to 99.8th percentile) covers too
        few bins in some planes.

    Returns
    -------
    RunResult
        If any error is found, nothing is written and ``written`` is False.

    Raises
    ------
    ValueError
        For an image type other than ``"fluorescence"`` and ``"rgb"``, or for
        ``missing_as_singleton`` that is not True or False
        (the manifest records them as JSON booleans, contract §9).
    """
    if image_type is not None:
        check_image_type(image_type)
    for name, value in (("missing_as_singleton", missing_as_singleton),):
        # Refused before anything is read rather than when the manifest is written.
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be True or False, found {value!r}")
    root = Path(experiment_root).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder).resolve()
    pre = preflight(
        root, output, exclude=exclude, masks_folder=masks_folder, ome_zarr_labels=ome_zarr_labels
    )
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    type_review = review_image_type(pre.metadata, review)
    dataset = pre.dataset
    design_problems: list[ValidationIssue] = []
    if design is not None:
        design, _ = reconcile_design(design, pre.dataset.repeats)
        design = design.dated(datetime.now().date().isoformat())
        design_problems = design_issues(design)
        dataset = check_dataset(pre.discovery, design.repeat_groups(), excluded=pre.excluded)
    issues = [
        *pre.discovery.issues,
        *pre.exclusion_issues,
        *review.combined_issues(pre.metadata),
        *image_type_issues(image_type, type_review),
        *design_problems,
        *dataset.issues,
        *(mask_issues(pre.masks, pre.metadata, review) if pre.masks is not None else ()),
    ]
    dtype = pre.metadata.dtype
    bins = None
    if dtype is not None and not has_errors(issues):
        bins, bin_issue = _bin_count(dtype, bin_count)
        issues.extend(bin_issue)
    if not has_errors(issues):
        issues.extend(
            check_planes(
                root,
                review,
                memory_limit=memory_limit,
                bin_count=bins,
                histogram_settings=histogram_settings,
            )
        )

    def stop(**counts) -> RunResult:  # noqa: ANN003
        return RunResult(output, False, _sorted(issues), excluded=pre.excluded, **counts)

    if has_errors(issues):
        return stop()

    settings = RunSettings(
        bins,
        memory_limit,
        missing_as_singleton,
        pre.excluded,
        histogram=histogram_settings or HistogramSettings(),
    )
    previous, previous_records, reason = _load_previous(
        output, settings, mapping, dtype.name, force_rebuild
    )
    labels = {image: leaf.labels for leaf in pre.discovery.leaves for image in leaf.images}
    in_blocks = {path for i in issues if i.code == "planes_read_in_blocks" for path in i.paths}
    headers = {f.relative_path: f for f in pre.metadata.files}
    entries: dict[str, FileEntry] = {}
    new, changed, unchanged = [], [], []
    for rel, file_mapping in sorted(review.file_mappings.items()):
        header = headers[rel]
        entries[rel] = file_entry_for(
            root,
            rel,
            labels[rel],
            file_mapping,
            channel_names=header.channel_names,
            significant_bits=header.significant_bits,
            read_in_blocks=rel in in_blocks,
            channel_names_trimmed=header.channel_names_trimmed,
        )
        if header.channel_names_trimmed:
            logger.info(
                "Leading or trailing whitespace was removed from the channel names of %s", rel
            )
        before = previous.file(rel) if previous is not None else None
        if before is None:
            new.append(rel)
        elif (
            before.same_source(entries[rel])
            # During a rebuild there are no rows to check; otherwise the rows must be complete.
            and (reason is not None or len(previous_records.get(rel, ())) == before.plane_count)
        ):
            unchanged.append(rel)
        else:
            changed.append(rel)
    removed = sorted(
        f.relative_path
        for f in (previous.files if previous else ())
        if f.relative_path not in entries
    )
    to_read = sorted([*new, *changed, *(unchanged if reason else ())])
    counts = {
        "new": tuple(new),
        "changed": tuple(changed),
        "unchanged": tuple(unchanged),
        "removed": tuple(removed),
        "rebuild_reason": reason,
    }

    records: list[PlaneRecord] = []
    narrow: list[NarrowRange] = []
    for rel in unchanged if not reason else ():
        # Tables written before position identifiers were recorded have them blank;
        # the identifier depends only on the path, so it is filled without reading.
        records.extend(
            replace(record, position_id=entries[rel].position_id)
            for record in previous_records[rel]
        )
        # Kept rows keep the range checks they were written with.
        narrow.extend(entry for entry in previous.narrow_ranges if entry.relative_path == rel)
    failed: list[str] = []
    for done, rel in enumerate(to_read):
        if progress is not None:
            progress(done, len(to_read), rel)
        try:
            rows, ranges = _read_file(
                root / rel,
                rel,
                labels[rel],
                review.file_mappings[rel],
                settings,
                entries[rel].position_id,
            )
        except PlaneReadError:
            logger.warning("Cannot read the planes of %s", rel, exc_info=True)
            failed.append(rel)
            continue
        records.extend(rows)
        narrow.extend(ranges)
    if progress is not None:
        progress(len(to_read), len(to_read), "")
    if failed:
        issues.append(
            error(
                "unreadable_planes",
                f"The pixel data of {plural(len(failed), 'image')} could not be read, although "
                "the file headers could. The files may be damaged or incomplete.",
                failed,
                "Export the images again, or exclude them from the run to continue without them.",
            )
        )
        return stop(read=tuple(to_read), **counts)

    issues.extend(_pixel_issues(records))
    narrow.sort(key=lambda entry: (entry.plane_id, entry.channel))
    issues.extend(_range_issues(narrow))
    if has_errors(issues):
        return stop(read=tuple(to_read), **counts)

    records.sort(key=_row_order)
    # From the rows in memory: no image is read again.
    intensity = check_intensities(records, intensity_settings)
    per_file: dict[str, int] = {}
    for record in records:
        per_file[record.source_relative_path] = per_file.get(record.source_relative_path, 0) + 1
    output.mkdir(parents=True, exist_ok=True)
    with _staged_outputs(output, with_design=design is not None) as staged:
        csv_path = write_plane_histograms(records, staged / OUTPUT_FILENAME)
        design_sha256 = None
        if design is not None:
            design_sha256 = file_sha256(write_experiment_design(design, staged / DESIGN_FILENAME))
        manifest = RunManifest(
            experiment_root=root.as_posix(),
            dtype=dtype.name,
            settings=settings,
            mapping=mapping,
            files=tuple(
                replace(entry, plane_count=per_file[rel]) for rel, entry in sorted(entries.items())
            ),
            csv_sha256=file_sha256(csv_path),
            versions=package_versions(),
            written_at=datetime.now(UTC).isoformat(timespec="seconds"),
            experiment_design_sha256=design_sha256,
            masks=None if pre.masks is None else pre.masks.to_manifest(),
            image_type=image_type,
            channels_from_rgb_samples=type_review.channels_from_rgb_samples,
            intensity_checks=intensity.settings,
            narrow_ranges=tuple(narrow),
        )
        write_manifest(manifest, staged / MANIFEST_FILENAME)
    logger.info(
        "Wrote %d planes (%d files read, %d kept) to %s",
        len(records),
        len(to_read),
        len(records) - sum(per_file[rel] for rel in to_read),
        output,
    )
    return RunResult(
        output,
        True,
        _sorted(issues),
        read=tuple(to_read),
        excluded=pre.excluded,
        plane_count=len(records),
        design=design,
        image_type=image_type,
        intensity=intensity,
        **counts,
    )


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
