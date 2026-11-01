"""Run the brick: check an experiment, then write or update its histogram table.

:func:`preflight` is pass 1: it walks the folder and inspects image headers
without reading pixels, and suggests an axis mapping for the user to confirm.
:func:`run_experiment` is pass 2: with the confirmed mapping it checks
everything again, reads only the images that are new or changed since the last
run, and writes ``plane_histograms.csv`` and ``run_manifest.json`` to the output
folder (``docs/plan.md`` §5–§7). Given the confirmed experiment design, it also
writes ``experiment_design.csv`` and records its hash in the manifest; given the
confirmed image type, it records it there too. After writing, it checks the
images' intensities from the table's rows (:mod:`.intensity`) and records the
limits used. Every plane is binned with the chosen histogram range, and its
range check reports channels whose central intensity range covers too few bins
(``narrow_histogram_range``, follow-up P6).

Each row depends only on its own plane, so rows of unchanged files are kept as
they are. Everything is recomputed when there is no usable earlier output, when
a rebuild is requested, or when a setting that affects every row (pixel type,
bin count, histogram range, axis mapping) changed. Nothing is written unless the whole run
succeeds; the earlier output is then replaced.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.csv_io import (
    OUTPUT_FILENAME,
    SCHEMA_VERSION,
    CsvFormatError,
    read_plane_histograms,
    write_plane_histograms,
)
from morphobricks_normalization.dtypes import default_bin_count, validate_bin_count
from morphobricks_normalization.ranges import HistogramSettings, range_check_issues
from morphobricks_normalization.records import PlaneRecord

from .axes import (
    AxisMapping,
    file_mapping_for,
    prefill_experiment_mapping,
    review_axes,
)
from .dataset import DatasetReport, check_dataset
from .design import (
    DESIGN_FILENAME,
    ExperimentDesign,
    design_issues,
    reconcile_design,
    write_experiment_design,
)
from .discovery import DiscoveryResult, default_output_folder, discover_experiment
from .image_type import check_image_type, image_type_issues, review_image_type
from .intensity import IntensityReport, check_intensities
from .manifest import (
    MANIFEST_FILENAME,
    FileEntry,
    ManifestFormatError,
    NarrowRange,
    RunManifest,
    RunSettings,
    file_entry_for,
    file_sha256,
    package_versions,
    read_manifest,
    write_manifest,
)
from .mask_checks import mask_issues
from .masks import MaskInventory, find_masks
from .metadata import MetadataReport, TiffMetadata, inspect_images
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneReadError,
    check_planes,
    iter_planes,
    iter_xyc_planes,
    plane_layout,
    read_sample_plane,
)
from .validation import ValidationIssue, error, has_errors, plural, warning
from .zarr_export import (
    ConversionSettings,
    OmeZarrWriter,
    copy_channels_current,
    copy_units_current,
    is_copy_path,
    ome_zarr_relative_path,
    read_physical_sizes,
    zarr_available,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Preflight:
    """Pass 1: what was found in the experiment, before any pixel is read.

    Attributes
    ----------
    discovery
        Folder structure and labels.
    metadata
        Image headers of the images to analyse (excluded images left out).
    excluded
        Images the user excluded, sorted.
    exclusion_issues
        Warnings about excluded images, and an error if nothing is left.
    suggested_mapping
        Axis mapping prefilled from the headers, or None if the images'
        dimensions conflict.
    dataset
        Images per condition and biological repeat, and the warnings about
        their balance.
    masks
        The masks of the images (contract §15), found from their headers, when
        masks were asked for; None otherwise.
    """

    discovery: DiscoveryResult
    metadata: MetadataReport
    excluded: tuple[str, ...] = ()
    exclusion_issues: tuple[ValidationIssue, ...] = ()
    suggested_mapping: AxisMapping | None = None
    dataset: DatasetReport = field(default_factory=DatasetReport)
    masks: MaskInventory | None = None

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """All issues found so far, errors first."""
        merged = [
            *self.discovery.issues,
            *self.exclusion_issues,
            *self.metadata.issues,
            *self.dataset.issues,
            *(self.masks.issues if self.masks is not None else ()),
        ]
        return tuple(sorted(merged, key=lambda issue: not issue.is_error))

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)


@dataclass(frozen=True)
class RunResult:
    """What a run did.

    Attributes
    ----------
    output_folder
        Where the table and manifest are (or would have been) written.
    written
        True if the run succeeded and the output was written.
    issues
        Errors that stopped the run, and warnings, errors first.
    new, changed, unchanged, removed
        Files compared with the earlier run: not in it, modified since, as they
        were, and in it but no longer analysed (deleted, renamed or excluded).
        Without a usable earlier manifest, every file is new.
    read
        Files whose pixels were read in this run.
    excluded
        Images the user excluded.
    rebuild_reason
        Why every row was recomputed, or None when the earlier table was updated.
    plane_count
        Number of rows in the written table.
    converted
        Files whose OME-Zarr copy was written in this run.
    design
        The experiment design written with the table, or None when the run was
        given none.
    image_type
        The image type recorded in the manifest, or None when the run was given
        none.
    intensity
        The intensity checks of the written table (warnings, never errors), or
        None when nothing was written.
    """

    output_folder: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    rebuild_reason: str | None = None
    plane_count: int = 0
    converted: tuple[str, ...] = ()
    design: ExperimentDesign | None = None
    image_type: str | None = None
    intensity: IntensityReport | None = None

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

    @property
    def design_path(self) -> Path | None:
        """Location of ``experiment_design.csv``, or None when the run had no design."""
        return self.output_folder / DESIGN_FILENAME if self.design is not None else None

    @property
    def csv_path(self) -> Path:
        """Location of ``plane_histograms.csv``."""
        return self.output_folder / OUTPUT_FILENAME

    @property
    def manifest_path(self) -> Path:
        """Location of ``run_manifest.json``."""
        return self.output_folder / MANIFEST_FILENAME


@dataclass(frozen=True)
class SamplePlane:
    """A plane shown in the axis preview.

    Attributes
    ----------
    relative_path
        The file the plane comes from.
    axes
        The file's axis labels; one sample is taken per arrangement of axes.
    plane
        The plane, or None if it could not be read.
    error
        Why the plane could not be read, in plain language.
    """

    relative_path: str
    axes: str
    plane: Plane | None = None
    error: str | None = None


def sample_planes(
    pre: Preflight,
    mapping: AxisMapping,
    *,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> list[SamplePlane]:
    """Read one preview plane per format and arrangement of axes, from the first image.

    Images lacking some of the mapping's axes are previewed with those axes as
    length 1, so the user can see them before confirming that this is right.
    """
    samples = []
    seen: set[tuple[str, str]] = set()
    for f in pre.metadata.files:
        if not f.readable or (f.kind, f.axes) in seen:
            continue
        seen.add((f.kind, f.axes))
        file_mapping = file_mapping_for(f, mapping)
        if file_mapping is None:
            samples.append(
                SamplePlane(f.relative_path, f.axes, error="The axis mapping does not fit it.")
            )
            continue
        try:
            plane = read_sample_plane(
                pre.discovery.root / f.relative_path, file_mapping, memory_limit=memory_limit
            )
        except PlaneReadError as exc:
            logger.debug("Cannot preview %s", f.relative_path, exc_info=True)
            samples.append(SamplePlane(f.relative_path, f.axes, error=str(exc)))
            continue
        samples.append(SamplePlane(f.relative_path, f.axes, plane))
    return samples


def preflight(
    experiment_root: str | os.PathLike,
    output_folder: str | os.PathLike | None = None,
    *,
    exclude: Iterable[str] = (),
    masks_folder: str | os.PathLike | None = None,
    ome_zarr_labels: bool = False,
) -> Preflight:
    """Pass 1: check the folder and the image headers without reading pixels.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    output_folder
        The output folder; defaults to :func:`default_output_folder`.
    exclude
        Relative paths of images to leave out (``/`` or backslash separators),
        for example damaged files.
    masks_folder
        The masks folder (contract §15): one subfolder per mask type, each
        mirroring the experiment's folders. The masks are paired with the images
        and checked from their headers; the label images of OME-Zarr images are
        included.
    ome_zarr_labels
        Use the label images of OME-Zarr images as masks, also without a masks
        folder.

    Returns
    -------
    Preflight
    """
    root = Path(experiment_root).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder)
    discovery = discover_experiment(root, output)
    excluded = tuple(sorted({_normalise(path) for path in exclude}))
    images = [path for path in discovery.images if path not in excluded]
    metadata = inspect_images(root, images)
    masks = None
    if masks_folder is not None or ome_zarr_labels:
        masks = find_masks(masks_folder, discovery, metadata.files)
    return Preflight(
        discovery=discovery,
        metadata=metadata,
        excluded=excluded,
        exclusion_issues=tuple(_exclusion_issues(discovery, excluded)),
        suggested_mapping=prefill_experiment_mapping(metadata),
        dataset=check_dataset(discovery, excluded=excluded),
        masks=masks,
    )


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
    convert_tiffs: bool = False,
    conversion: ConversionSettings | None = None,
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
    convert_tiffs
        Convert every TIFF image to OME-Zarr in ``<output>/ome_zarr`` and compute
        its histograms from the converted copy, as for any OME-Zarr image (needs
        the ``zarr`` extra). Copies of unchanged images are kept; copies of
        images no longer converted are removed. Switching this recomputes the
        rows of every TIFF image.
    conversion
        How converted copies are written (chunk size, compression, smaller
        levels); defaults to :class:`ConversionSettings`. Changing it rewrites
        the copies of every TIFF image.
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
        ``missing_as_singleton`` or ``convert_tiffs`` that is not True or False
        (the manifest records them as JSON booleans, contract §9).
    """
    if image_type is not None:
        check_image_type(image_type)
    for name, value in (
        ("missing_as_singleton", missing_as_singleton),
        ("convert_tiffs", convert_tiffs),
    ):
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
    if convert_tiffs and not zarr_available():
        issues.append(
            error(
                "zarr_not_installed",
                "Converting the TIFF images to OME-Zarr was requested, but the zarr package "
                "is not installed.",
                action='Install it with pip install "morphobricks-preprocessing[zarr]", or '
                "read the TIFF images directly.",
            )
        )

    def stop(**counts) -> RunResult:  # noqa: ANN003
        return RunResult(output, False, _sorted(issues), excluded=pre.excluded, **counts)

    if has_errors(issues):
        return stop()

    conversion = conversion or ConversionSettings()
    settings = RunSettings(
        bins,
        memory_limit,
        missing_as_singleton,
        pre.excluded,
        convert_tiffs,
        conversion,
        histogram=histogram_settings or HistogramSettings(),
    )
    previous, previous_records, reason = _load_previous(
        output, settings, mapping, dtype.name, force_rebuild
    )
    # Copies written with other conversion settings are rewritten (their rows are the same).
    same_conversion = previous is None or previous.settings.conversion == conversion
    labels = {image: leaf.labels for leaf in pre.discovery.leaves for image in leaf.images}
    in_blocks = {path for i in issues if i.code == "planes_read_in_blocks" for path in i.paths}
    headers = {f.relative_path: f for f in pre.metadata.files}
    entries: dict[str, FileEntry] = {}
    new, changed, unchanged = [], [], []
    for rel, file_mapping in sorted(review.file_mappings.items()):
        header = headers[rel]
        copy = ome_zarr_relative_path(rel) if convert_tiffs and not header.is_ome_zarr else None
        entries[rel] = file_entry_for(
            root,
            rel,
            labels[rel],
            file_mapping,
            channel_names=header.channel_names,
            significant_bits=header.significant_bits,
            ome_zarr_path=copy,
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
            # Rows of a converted TIFF are only kept while its copy is still there, as
            # written with the same settings and with the axis units read now (copies
            # written before each axis had its own unit are written again).
            and (
                copy is None
                or (
                    (output / copy).is_dir()
                    and same_conversion
                    and copy_units_current(output / copy, header.physical_sizes)
                    and copy_channels_current(output / copy, header.channel_names)
                )
            )
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

    to_convert = [rel for rel in to_read if entries[rel].ome_zarr_path]
    staging = _CopyStaging(output, root, conversion) if to_convert else None

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
    try:
        for done, rel in enumerate(to_read):
            if progress is not None:
                progress(done, len(to_read), rel)
            file_mapping = review.file_mappings[rel]
            position = entries[rel].position_id
            try:
                if rel in to_convert:
                    # Convert first, then read the copy like any other OME-Zarr image.
                    copy, copy_mapping = staging.convert(rel, file_mapping, headers[rel], settings)
                    rows, ranges = _read_file(
                        copy, rel, labels[rel], copy_mapping, settings, position
                    )
                else:
                    rows, ranges = _read_file(
                        root / rel, rel, labels[rel], file_mapping, settings, position
                    )
                records.extend(rows)
                narrow.extend(ranges)
            except PlaneReadError:
                logger.warning("Cannot read the planes of %s", rel, exc_info=True)
                failed.append(rel)
    except BaseException:
        if staging is not None:
            staging.discard()
        raise
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
        if staging is not None:
            staging.discard()
        return stop(read=tuple(to_read), **counts)

    issues.extend(_pixel_issues(records))
    narrow.sort(key=lambda entry: (entry.plane_id, entry.channel))
    issues.extend(_range_issues(narrow))
    if has_errors(issues):
        if staging is not None:
            staging.discard()
        return stop(read=tuple(to_read), **counts)

    records.sort(key=_row_order)
    # From the rows in memory: no image is read again.
    intensity = check_intensities(records, intensity_settings)
    per_file: dict[str, int] = {}
    for record in records:
        per_file[record.source_relative_path] = per_file.get(record.source_relative_path, 0) + 1
    output.mkdir(parents=True, exist_ok=True)
    # Copies recorded for images no longer analysed, or no longer converted, are
    # removed; a damaged manifest cannot point the removal outside this brick's own
    # ome_zarr folder.
    removed_copies = [
        f.ome_zarr_path
        for f in (previous.files if previous else ())
        if f.ome_zarr_path
        and (f.relative_path not in entries or entries[f.relative_path].ome_zarr_path is None)
        and is_copy_path(output, f.ome_zarr_path)
    ]
    with _staged_outputs(
        output,
        staging.root if staging is not None else None,
        copies=[ome_zarr_relative_path(rel) for rel in to_convert],
        removed_copies=removed_copies,
        with_design=design is not None,
    ) as staged:
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
        converted=tuple(to_convert),
        design=design,
        image_type=image_type,
        intensity=intensity,
        **counts,
    )


class _OutputRollbackError(RuntimeError):
    """Raised when an output update fails and its earlier files cannot be restored."""


@contextmanager
def _staged_outputs(
    output: Path,
    staged: Path | None = None,
    *,
    copies: Sequence[str] = (),
    removed_copies: Sequence[str] = (),
    with_design: bool = False,
) -> Iterator[Path]:
    """Prepare all outputs before publishing, and retain backups if recovery fails.

    ``staged`` is a temporary folder inside ``output`` that may already hold the
    OME-Zarr ``copies`` written while reading; one is created if None.
    ``with_design`` publishes ``experiment_design.csv`` too.
    """
    staged = staged or Path(tempfile.mkdtemp(prefix=".run-", dir=output))
    keep_backups = False
    try:
        yield staged
        _publish_outputs(output, staged, copies, removed_copies, with_design)
    except _OutputRollbackError:
        keep_backups = True
        raise
    finally:
        if not keep_backups:
            try:
                shutil.rmtree(staged)
            except OSError:
                # A leftover temporary directory does not invalidate completed output.
                logger.warning("Cannot remove temporary output folder %s", staged, exc_info=True)


def _publish_outputs(
    output: Path,
    staged: Path,
    copies: Sequence[str] = (),
    removed_copies: Sequence[str] = (),
    with_design: bool = False,
) -> None:
    """Replace the outputs, restoring their previous state on a publication failure.

    OME-Zarr copies are published first and the manifest last, so a manifest never
    names a copy or design that is not in place. Earlier copies are moved into the
    backup folder rather than copied, because they can be large.
    """
    filenames = (OUTPUT_FILENAME, *((DESIGN_FILENAME,) if with_design else ()), MANIFEST_FILENAME)
    backup = staged / "previous"
    backup.mkdir()
    # Copy before replacing either file, so even a backup failure leaves the
    # earlier output intact. Keep backups on the same filesystem for atomic restores.
    for name in filenames:
        previous = output / name
        if previous.exists() or previous.is_symlink():
            shutil.copy2(previous, backup / name, follow_symlinks=False)
    # Undo steps in order: ("moved", copy) went to the backup, ("placed", copy) is new,
    # ("file", name) is a published table or manifest.
    done: list[tuple[str, str]] = []
    try:
        for target in removed_copies:
            if (output / target).exists():
                _move(output / target, backup / target)
                done.append(("moved", target))
        for target in copies:
            if (output / target).exists():
                _move(output / target, backup / target)
                done.append(("moved", target))
            _move(staged / target, output / target)
            done.append(("placed", target))
        for name in filenames:
            os.replace(staged / name, output / name)
            done.append(("file", name))
    except BaseException:
        try:
            for kind, item in reversed(done):
                if kind == "placed":
                    shutil.rmtree(output / item)
                elif kind == "moved":
                    _move(backup / item, output / item)
                elif (backup / item).exists() or (backup / item).is_symlink():
                    os.replace(backup / item, output / item)
                else:
                    (output / item).unlink()
        except BaseException as exc:
            raise _OutputRollbackError(
                "The output update failed and the earlier files could not be fully restored. "
                f"Backup files have been kept in {backup}. Close programs using the output "
                "files and restore the backups before running again."
            ) from exc
        raise


def _read_file(
    path: Path,
    rel: str,
    labels: tuple[str, str, str],
    mapping: AxisMapping,
    settings: RunSettings,
    position: str | None,
) -> tuple[list[PlaneRecord], list[NarrowRange]]:
    """Histogram every XYC plane of ``path``, recording the rows under the source ``rel``.

    One row per plane holds the histograms of all its channels (schema version 4),
    binned with the run's histogram range. Also returns the channel planes whose
    range check found the central range too narrow.
    """
    records, narrow = [], []
    for plane in iter_xyc_planes(
        path,
        mapping,
        memory_limit=settings.memory_limit,
        bin_count=settings.bin_count,
        histogram_settings=settings.histogram,
    ):
        measured = plane.histograms_with_checks(settings.bin_count, settings.histogram)
        record = PlaneRecord(
            rel,
            *labels,
            histograms=tuple(histogram for histogram, _ in measured),
            z=plane.z,
            t=plane.t,
            position_id=position,
        )
        records.append(record)
        narrow.extend(
            NarrowRange(rel, record.plane_id, channel, check)
            for channel, (_, check) in enumerate(measured)
            if check is not None and check.narrow
        )
    return records, narrow


def _move(source: Path, destination: Path) -> None:
    """Rename a file or folder, creating the destination's parent folders."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


class _CopyStaging:
    """The run's temporary folder while TIFF images are converted to OME-Zarr.

    The copies are published with the table and manifest by :func:`_staged_outputs`,
    which reuses this folder; :meth:`discard` removes it when the run stops early.
    """

    def __init__(self, output: Path, experiment_root: Path, settings: ConversionSettings):  # noqa: ANN204
        self.output = output
        self.experiment_root = experiment_root
        self.settings = settings
        self.created_output = not output.exists()
        output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=".run-", dir=output))

    def writer(
        self, rel: str, mapping: AxisMapping, header: TiffMetadata, memory_limit: int
    ) -> OmeZarrWriter:
        """Create the writer of one image's copy inside the temporary folder."""
        source = self.experiment_root / rel
        return OmeZarrWriter.create(
            self.root / ome_zarr_relative_path(rel),
            mapping,
            plane_layout(source, mapping),
            sizes=read_physical_sizes(source),
            channel_names=header.channel_names,
            name=rel,
            settings=self.settings,
            memory_limit=memory_limit,
        )

    def convert(
        self, rel: str, mapping: AxisMapping, header: TiffMetadata, settings: RunSettings
    ) -> tuple[Path, AxisMapping]:
        """Convert one TIFF plane by plane; return the copy and its own axis mapping."""
        writer = self.writer(rel, mapping, header, settings.memory_limit)
        for plane in iter_planes(
            self.experiment_root / rel,
            mapping,
            memory_limit=settings.memory_limit,
            bin_count=settings.bin_count,
        ):
            writer.write(plane)
        return writer.finish(), writer.mapping

    def discard(self) -> None:
        """Remove the temporary folder, and the output folder if this run created it."""
        shutil.rmtree(self.root, ignore_errors=True)
        if self.created_output and not any(self.output.iterdir()):
            self.output.rmdir()


def _load_previous(
    output: Path, settings: RunSettings, mapping: AxisMapping, dtype: str, force: bool
) -> tuple[RunManifest | None, dict[str, list[PlaneRecord]], str | None]:
    """Load the earlier manifest and its rows by file, and say why they cannot be reused.

    The manifest is returned whenever it can be read, so files can still be
    reported as new, changed or unchanged during a rebuild.
    """
    manifest_path, csv_path = output / MANIFEST_FILENAME, output / OUTPUT_FILENAME
    if not manifest_path.exists():
        return None, {}, "there is no earlier run in the output folder"
    try:
        manifest = read_manifest(manifest_path)
    except ManifestFormatError:
        logger.warning("Cannot read %s", manifest_path, exc_info=True)
        return None, {}, "the earlier run manifest could not be read"
    if force:
        return manifest, {}, "a full rebuild was requested"
    if manifest.schema_version != SCHEMA_VERSION:
        return manifest, {}, "the earlier table uses a different format version"
    if manifest.dtype != dtype:
        return manifest, {}, f"the pixel type changed from {manifest.dtype} to {dtype}"
    if manifest.settings.bin_count != settings.bin_count:
        return (
            manifest,
            {},
            f"the bin count changed from {manifest.settings.bin_count:,} to {settings.bin_count:,}",
        )
    if manifest.settings.histogram != settings.histogram:
        before = _describe_histogram(manifest.settings.histogram)
        now = _describe_histogram(settings.histogram)
        if before == now:
            return manifest, {}, "the limit of the histogram range check changed"
        return manifest, {}, f"the histogram range changed from {before} to {now}"
    if (manifest.mapping.axes, manifest.mapping.roles) != (mapping.axes, mapping.roles):
        return manifest, {}, "the axis mapping changed"
    if not csv_path.exists() or file_sha256(csv_path) != manifest.csv_sha256:
        return manifest, {}, "the histogram table was changed or replaced after the earlier run"
    try:
        rows = read_plane_histograms(csv_path)
    except CsvFormatError:
        logger.warning("Cannot read %s", csv_path, exc_info=True)
        return manifest, {}, "the earlier histogram table could not be read"
    by_file: dict[str, list[PlaneRecord]] = {}
    for record in rows:
        by_file.setdefault(record.source_relative_path, []).append(record)
    return manifest, by_file, None


def _bin_count(dtype, bin_count: int | None) -> tuple[int | None, list[ValidationIssue]]:  # noqa: ANN001
    if bin_count is None:
        return default_bin_count(dtype), []
    try:
        return validate_bin_count(dtype, bin_count), []
    except ValueError as exc:
        return None, [
            error(
                "invalid_bin_count",
                str(exc),
                action="Choose a bin count in the allowed range, or use the default.",
            )
        ]


def _describe_histogram(settings: HistogramSettings) -> str:
    """Describe the histogram range in words, e.g. ``"robust (0.01-99.99 %)"``."""
    if settings.robust:
        low, high = settings.robust_quantiles
        return f"robust ({low:g}-{high:g} %)"
    return "minimum to maximum"


def _range_issues(narrow: list[NarrowRange]) -> list[ValidationIssue]:
    """Return the brick's ``narrow_histogram_range`` warnings, one per channel (N60)."""
    found = range_check_issues((entry.plane_id, entry.channel, entry.check) for entry in narrow)
    return [warning(issue.code, issue.message, issue.items, issue.action) for issue in found]


def _pixel_issues(records: list[PlaneRecord]) -> list[ValidationIssue]:
    # Every channel of every row is checked.
    channels = [(r.source_relative_path, h) for r in records for h in r.histograms]
    issues = []
    with_non_finite = sorted({rel for rel, h in channels if h.non_finite_pixel_count})
    if with_non_finite:
        total = sum(h.non_finite_pixel_count for _, h in channels)
        issues.append(
            warning(
                "non_finite_pixels",
                f"{plural(len(with_non_finite), 'image')} contain {plural(total, 'pixel')} that "
                "are not numbers or infinite (NaN, ±infinity). They are left out of the "
                "histograms and counted separately.",
                with_non_finite,
                "No action needed if these values mark missing data.",
            )
        )
    empty = [rel for rel, h in channels if h.is_empty]
    if empty and len(empty) == len(channels):
        issues.append(
            error(
                "no_usable_planes",
                "No plane has any finite pixel value, so no statistics can be calculated.",
                sorted(set(empty)),
                "Check that these are the right images and that they were exported correctly.",
            )
        )
    elif empty:
        planes = {r.plane_id for r in records if any(h.is_empty for h in r.histograms)}
        issues.append(
            warning(
                "no_finite_pixels",
                f"{plural(len(planes), 'plane')} have no finite pixel values in at least one "
                "channel. These channels are kept in the table with zero counts and left out "
                "of group statistics.",
                sorted(set(empty)),
                "No action needed if these planes are expected to be empty.",
            )
        )
    return issues


def _exclusion_issues(discovery: DiscoveryResult, excluded: tuple[str, ...]) -> list:
    issues = []
    found = [path for path in excluded if path in discovery.images]
    unknown = [path for path in excluded if path not in discovery.images]
    if found:
        issues.append(
            warning(
                "excluded_images",
                f"{plural(len(found), 'image')} {'is' if len(found) == 1 else 'are'} excluded "
                "as requested and will not be analysed. Their rows from earlier runs are "
                "removed.",
                found,
                "Remove them from the exclusion list to analyse them again.",
            )
        )
    if unknown:
        issues.append(
            warning(
                "excluded_path_not_found",
                f"{plural(len(unknown), 'excluded path')} {'does' if len(unknown) == 1 else 'do'} "
                "not match any analysed image of the experiment.",
                unknown,
                "Check the spelling of the paths; they are relative to the experiment folder.",
            )
        )
    if discovery.images and not set(discovery.images) - set(excluded):
        issues.append(
            error(
                "no_images",
                "Every image of the experiment is excluded, so there is nothing to analyse.",
                ["."],
                "Remove images from the exclusion list.",
            )
        )
    return issues


def _normalise(path: str) -> str:
    path = str(path).replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _row_order(record: PlaneRecord) -> tuple:
    return (record.source_relative_path, record.z, record.t)


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
