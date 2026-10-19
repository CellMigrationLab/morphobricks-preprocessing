"""Run the brick: check an experiment, then write or update its histogram table.

:func:`preflight` is pass 1: it walks the folder and inspects image headers
without reading pixels, and suggests an axis mapping for the user to confirm.
:func:`run_experiment` is pass 2: with the confirmed mapping it checks
everything again, reads only the images that are new or changed since the last
run, and writes ``plane_histograms.csv`` and ``run_manifest.json`` to the output
folder (``docs/plan.md`` §5–§7).

Each row depends only on its own plane, so rows of unchanged files are kept as
they are. Everything is recomputed when there is no usable earlier output, when
a rebuild is requested, or when a setting that affects every row (pixel type,
bin count, axis mapping) changed. Nothing is written unless the whole run
succeeds; the earlier output is then replaced.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .axes import AxisMapping, prefill_experiment_mapping, review_axes
from .csv_io import (
    OUTPUT_FILENAME,
    SCHEMA_VERSION,
    CsvFormatError,
    read_plane_histograms,
    write_plane_histograms,
)
from .discovery import DiscoveryResult, default_output_folder, discover_experiment
from .dtypes import default_bin_count, validate_bin_count
from .histogram import compute_plane_histogram
from .manifest import (
    MANIFEST_FILENAME,
    FileEntry,
    ManifestFormatError,
    RunManifest,
    RunSettings,
    file_entry_for,
    file_sha256,
    package_versions,
    read_manifest,
    write_manifest,
)
from .metadata import MetadataReport, inspect_images
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneReadError,
    check_planes,
    iter_planes,
    read_sample_plane,
)
from .records import PlaneRecord
from .validation import ValidationIssue, error, has_errors, plural, warning

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
    """

    discovery: DiscoveryResult
    metadata: MetadataReport
    excluded: tuple[str, ...] = ()
    exclusion_issues: tuple[ValidationIssue, ...] = ()
    suggested_mapping: AxisMapping | None = None

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """All issues found so far, errors first."""
        merged = [*self.discovery.issues, *self.exclusion_issues, *self.metadata.issues]
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

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

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
    """Read one preview plane per arrangement of axes, from the first file that has it.

    Files lacking some of the mapping's axes are previewed with those axes as
    length 1, so the user can see them before confirming that this is right.
    """
    samples = []
    seen: set[str] = set()
    for f in pre.metadata.files:
        if not f.readable or f.axes in seen:
            continue
        seen.add(f.axes)
        file_mapping = mapping.for_axes(f.axes)
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
    return Preflight(
        discovery=discovery,
        metadata=metadata,
        excluded=excluded,
        exclusion_issues=tuple(_exclusion_issues(discovery, excluded)),
        suggested_mapping=prefill_experiment_mapping(metadata),
    )


def run_experiment(
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

    Returns
    -------
    RunResult
        If any error is found, nothing is written and ``written`` is False.
    """
    root = Path(experiment_root).resolve()
    output = default_output_folder(root) if output_folder is None else Path(output_folder).resolve()
    pre = preflight(root, output, exclude=exclude)
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    issues = [*pre.discovery.issues, *pre.exclusion_issues, *review.combined_issues(pre.metadata)]
    dtype = pre.metadata.dtype
    bins = None
    if dtype is not None and not has_errors(issues):
        bins, bin_issue = _bin_count(dtype, bin_count)
        issues.extend(bin_issue)
    if not has_errors(issues):
        issues.extend(check_planes(root, review, memory_limit=memory_limit, bin_count=bins))

    def stop(**counts) -> RunResult:
        return RunResult(output, False, _sorted(issues), excluded=pre.excluded, **counts)

    if has_errors(issues):
        return stop()

    settings = RunSettings(bins, memory_limit, missing_as_singleton, pre.excluded)
    previous, previous_records, reason = _load_previous(
        output, settings, mapping, dtype.name, force_rebuild
    )
    labels = {image: leaf.labels for leaf in pre.discovery.leaves for image in leaf.images}
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
        )
        before = previous.file(rel) if previous is not None else None
        if before is None:
            new.append(rel)
        elif before.same_source(entries[rel]) and (
            # During a rebuild there are no rows to check; otherwise the rows must be complete.
            reason is not None or len(previous_records.get(rel, ())) == before.plane_count
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
    for rel in unchanged if not reason else ():
        records.extend(previous_records[rel])
    failed: list[str] = []
    for done, rel in enumerate(to_read):
        if progress is not None:
            progress(done, len(to_read), rel)
        try:
            records.extend(_read_file(root, rel, labels[rel], review.file_mappings[rel], settings))
        except PlaneReadError:
            logger.warning("Cannot read the planes of %s", rel, exc_info=True)
            failed.append(rel)
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
    if has_errors(issues):
        return stop(read=tuple(to_read), **counts)

    records.sort(key=_row_order)
    per_file: dict[str, int] = {}
    for record in records:
        per_file[record.source_relative_path] = per_file.get(record.source_relative_path, 0) + 1
    output.mkdir(parents=True, exist_ok=True)
    with _staged_outputs(output) as staged:
        csv_path = write_plane_histograms(records, staged / OUTPUT_FILENAME)
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
            written_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        **counts,
    )


class _OutputRollbackError(RuntimeError):
    """Raised when an output update fails and its earlier files cannot be restored."""


@contextmanager
def _staged_outputs(output: Path) -> Iterator[Path]:
    """Prepare both outputs before publishing, and retain backups if recovery fails."""
    staged = Path(tempfile.mkdtemp(prefix=".run-", dir=output))
    keep_backups = False
    try:
        yield staged
        _publish_outputs(output, staged)
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


def _publish_outputs(output: Path, staged: Path) -> None:
    """Replace the output pair, restoring its previous state on a publication failure."""
    filenames = (OUTPUT_FILENAME, MANIFEST_FILENAME)
    backup = staged / "previous"
    backup.mkdir()
    # Copy before replacing either file, so even a backup failure leaves the
    # earlier output intact. Keep backups on the same filesystem for atomic restores.
    for name in filenames:
        previous = output / name
        if previous.exists() or previous.is_symlink():
            shutil.copy2(previous, backup / name, follow_symlinks=False)
    published: list[str] = []
    try:
        for name in filenames:
            os.replace(staged / name, output / name)
            published.append(name)
    except BaseException:
        try:
            for name in reversed(published):
                previous = backup / name
                if previous.exists() or previous.is_symlink():
                    os.replace(previous, output / name)
                else:
                    (output / name).unlink()
        except BaseException as exc:
            raise _OutputRollbackError(
                "The output update failed and the earlier files could not be fully restored. "
                f"Backup files have been kept in {backup}. Close programs using the output "
                "files and restore the backups before running again."
            ) from exc
        raise


def _read_file(
    root: Path, rel: str, labels: tuple[str, str, str], mapping: AxisMapping, settings
) -> list[PlaneRecord]:
    return [
        PlaneRecord(
            rel,
            *labels,
            histogram=compute_plane_histogram(plane.pixels, settings.bin_count),
            z=plane.z,
            c=plane.c,
            t=plane.t,
        )
        for plane in iter_planes(
            root / rel, mapping, memory_limit=settings.memory_limit, bin_count=settings.bin_count
        )
    ]


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


def _bin_count(dtype, bin_count: int | None) -> tuple[int | None, list[ValidationIssue]]:
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


def _pixel_issues(records: list[PlaneRecord]) -> list[ValidationIssue]:
    issues = []
    with_non_finite = sorted(
        {r.source_relative_path for r in records if r.histogram.non_finite_pixel_count}
    )
    if with_non_finite:
        total = sum(r.histogram.non_finite_pixel_count for r in records)
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
    empty = [r for r in records if r.histogram.is_empty]
    if empty and len(empty) == len(records):
        issues.append(
            error(
                "no_usable_planes",
                "No plane has any finite pixel value, so no statistics can be calculated.",
                sorted({r.source_relative_path for r in empty}),
                "Check that these are the right images and that they were exported correctly.",
            )
        )
    elif empty:
        issues.append(
            warning(
                "no_finite_pixels",
                f"{plural(len(empty), 'plane')} have no finite pixel values. They are kept in "
                "the table with zero counts and left out of group statistics.",
                sorted({r.source_relative_path for r in empty}),
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
    def key(value: int | None) -> int:
        return -1 if value is None else value

    return (record.source_relative_path, key(record.z), key(record.c), key(record.t))


def _sorted(issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
