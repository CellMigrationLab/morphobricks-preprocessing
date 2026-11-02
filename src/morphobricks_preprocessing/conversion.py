"""Convert: save an experiment as named-axis copies, the exception path (design v0.4 §5).

For images whose axes are wrong or unnamed: :func:`convert_experiment` writes a copy tree,
a folder with the experiment's folders and file names, in which

- every image with an axis error (``axes_yx_swapped``, ``axes_contradict_header``,
  ``axes_x_before_y``) or a plain stack (``axes_not_named``) is written as a named-axis
  OME-TIFF with the roles the user confirmed (:mod:`.copy_axes`, :mod:`.ome_tiff_copy`):
  each plane is the plane read under those roles, bit for bit; pixel sizes follow the roles
  and a relabelled axis gets an unknown scale;
- every other image is copied byte for byte: its header names its axes as confirmed, it is
  an OME-Zarr image, or it is excluded (never read);
- masks are never converted (they stay where they are, checked as stored by the run).

Nothing is guessed: the roles are the user's, and an error other than the axis errors
stops the conversion before anything is written. The originals are only read. The copy
tree must be outside the experiment (and not hold it); the user names it. The copies keep
each image's relative path, so ``source_relative_path``, ``plane_id`` and the FOV key stay
the same, and the conversion record (:mod:`.conversion_record`) is written last.

Safe to repeat: the copy tree's lock is held throughout; every copy is first written as
``<name>.partial`` and moved into place only once every copy is written, so a failed
conversion leaves the folder as it was; a copy already there is reused when its source,
roles and copy are unchanged, and anything else already there is refused, never
overwritten. Then the user runs preprocessing on the copy tree, with the same output
folder: the recorded experiment root becomes the copy tree (the report says so) and every
row is recomputed.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from ._io import free_space
from .axes import AxisMapping, prefill_mapping
from .axis_check import AXIS_ERROR_CODES, ConfirmedAxes, confirmation_of, header_axis_issue
from .axis_review import review_axes
from .conversion_record import (
    CONVERTED,
    COPIED,
    RECORD_FILENAME,
    ConversionRecord,
    ConversionRecordError,
    CopyEntry,
    read_record,
    write_record,
)
from .copy_axes import CopyLayout, copy_layout
from .fingerprint import Fingerprint, current_fingerprint
from .manifest import package_versions
from .metadata import TiffMetadata
from .ome_tiff_copy import CopyWriteError, copy_nbytes, write_named_copy
from .planes import DEFAULT_MEMORY_LIMIT, PlaneReadError
from .run.lock import LOCK_FILENAME, OutputLockedError, output_lock
from .run.preflight import preflight
from .validation import ValidationIssue, error, has_errors, plural

logger = logging.getLogger(__name__)

_NOT_IN_OME = frozenset({"int64", "uint64"})
"""Pixel types the brick reads that OME-TIFF cannot hold: they are never changed to fit."""

PARTIAL_SUFFIX = ".partial"
"""A copy is written as ``<name>.partial`` and renamed once every copy is written."""


@dataclass(frozen=True)
class PlannedCopy:
    """One image of the copy tree, before it is written.

    Attributes
    ----------
    entry
        Its conversion-record entry, without the copy's fingerprint.
    layout
        The named-axis copy of a converted image, else None.
    mapping
        The roles a converted image is read with, else None.
    nbytes
        The bytes the copy takes (pixels only for a converted image).
    """

    entry: CopyEntry
    layout: CopyLayout | None = None
    mapping: AxisMapping | None = None
    nbytes: int = 0

    @property
    def relative_path(self) -> str:
        """The image, relative to the experiment and to the copy tree."""
        return self.entry.relative_path


@dataclass(frozen=True)
class ConversionResult:
    """What :func:`convert_experiment` did.

    Attributes
    ----------
    destination
        The copy tree.
    written
        True when the copy tree holds every image and its conversion record; False when
        the conversion was refused (nothing was written then).
    issues
        The errors that stopped it, and the warnings found.
    converted
        Images written as named-axis OME-TIFF copies in this conversion, sorted.
    copied
        Images copied byte for byte in this conversion, sorted.
    reused
        Images whose copy was already there, unchanged, sorted.
    record_sha256
        The SHA-256 of the conversion record written.
    suggested_axes
        The roles to give ``run`` on the copy tree (``--axes``), when every copy has the
        same axes; else None.
    """

    destination: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    converted: tuple[str, ...] = ()
    copied: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    record_sha256: str | None = None
    suggested_axes: str | None = field(default=None)


def convert_experiment(
    experiment_root: str | os.PathLike,
    mapping: AxisMapping | ConfirmedAxes,
    destination: str | os.PathLike,
    *,
    exclude: Iterable[str] = (),
    missing_as_singleton: bool = False,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    progress: Callable[[int, int, str], None] | None = None,
) -> ConversionResult:
    """Write named-axis copies of an experiment's images with the roles the user confirmed.

    Parameters
    ----------
    experiment_root
        The experiment folder. It is only read.
    mapping
        The experiment axis mapping the user confirmed (as for :func:`.run.run_experiment`):
        the roles the copies are written with. Never taken from the headers.
    destination
        The copy tree: a new or empty folder outside the experiment, or the copy tree of
        an earlier conversion of this experiment (its unchanged copies are reused).
    exclude
        Images to leave unread; they are copied byte for byte, so the copy tree mirrors
        the experiment, and stay excluded by the run.
    missing_as_singleton
        The user confirmed that images lacking some axes have one plane along them.
    memory_limit
        Working-memory limit per plane while reading and writing, as the run's.
    progress
        Called as ``progress(done, total, relative_path)`` before each image is written.

    Returns
    -------
    ConversionResult
    """
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    refused = _folder_issues(root, dest)
    if refused:
        return ConversionResult(dest, False, tuple(refused))
    planned, issues = plan_conversion(root, mapping, dest, exclude, missing_as_singleton)
    if has_errors(issues):
        return ConversionResult(dest, False, _sorted(issues))
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(dest):
            options = _Options(confirmation_of(mapping)[0], missing_as_singleton, memory_limit)
            return _locked_conversion(root, dest, planned, issues, options, progress)
    except OutputLockedError as exc:
        return ConversionResult(dest, False, (_lock_issue(exc),))


@dataclass(frozen=True)
class _Options:
    """What the conversion records and how it reads, besides the plan."""

    mapping: AxisMapping
    missing_as_singleton: bool
    memory_limit: int


def plan_conversion(
    root: Path,
    mapping: AxisMapping | ConfirmedAxes,
    destination: Path,
    exclude: Iterable[str] = (),
    missing_as_singleton: bool = False,
) -> tuple[tuple[PlannedCopy, ...], list[ValidationIssue]]:
    """Check the experiment with the confirmed roles and plan each image's copy.

    Only the headers are read. The axis errors do not stop the conversion: they are what
    it corrects. Any other error does, and nothing is planned.

    Parameters
    ----------
    root
        The experiment folder (absolute).
    mapping
        The confirmed mapping.
    destination
        The copy tree (absolute).
    exclude
        Images to leave unread.
    missing_as_singleton
        As :func:`convert_experiment`.

    Returns
    -------
    tuple
        The planned copies, sorted by path (empty when an error stops the conversion),
        and the issues: the errors that stop it, and the warnings.
    """
    pre = preflight(root, destination, exclude=exclude)
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    others = [issue for issue in pre.issues if issue not in pre.metadata.issues]
    issues = [*others, *review.combined_issues(pre.metadata)]
    if any(i.is_error and i.code not in AXIS_ERROR_CODES for i in issues):
        return (), [i for i in issues if not (i.is_error and i.code in AXIS_ERROR_CODES)]
    issues = [i for i in issues if not i.is_error]  # the axis errors are what is corrected
    to_convert = set(review.axis_error_files) | set(review.confirmed_files)
    headers = {meta.relative_path: meta for meta in pre.metadata.files}
    wide = sorted(rel for rel in to_convert if headers[rel].dtype.name in _NOT_IN_OME)
    if wide:
        return (), [_pixel_type_issue(wide, headers[wide[0]].dtype.name), *issues]
    planned = []
    for rel in pre.discovery.images:
        source = Fingerprint.of(root / rel)
        if rel in to_convert:
            planned.append(_converted(headers[rel], review.file_mappings[rel], source))
        else:
            reason = "excluded" if rel in pre.excluded else None
            meta = headers.get(rel)
            entry = CopyEntry(rel, COPIED, reason, source, source_axes=meta.axes if meta else "")
            planned.append(PlannedCopy(entry, nbytes=source.size))
    return tuple(planned), issues


def _converted(meta: TiffMetadata, mapping: AxisMapping, source: Fingerprint) -> PlannedCopy:
    """Plan the named-axis copy of one image read under its confirmed roles."""
    layout = copy_layout(meta, mapping)
    entry = CopyEntry(
        relative_path=meta.relative_path,
        action=CONVERTED,
        reason=header_axis_issue(meta, mapping),
        source=source,
        source_axes=meta.axes,
        roles=mapping.roles,
        copy_axes=layout.axes,
        pixel_sizes=layout.sizes,
        unknown_sizes=layout.unknown,
    )
    return PlannedCopy(entry, layout, mapping, copy_nbytes(layout, meta.dtype))


def _locked_conversion(
    root: Path,
    dest: Path,
    planned: tuple[PlannedCopy, ...],
    issues: list[ValidationIssue],
    options: _Options,
    progress: Callable[[int, int, str], None] | None,
) -> ConversionResult:
    """Compare with what the copy tree holds, write the new copies and the record."""
    try:
        earlier = read_record(dest)
    except ConversionRecordError as exc:
        return ConversionResult(dest, False, (_record_issue(exc, dest),))
    reused, to_write, refusal = _compare(root, dest, planned, earlier)
    refusal = refusal or _space_issue(dest, to_write)
    if refusal is not None:
        return ConversionResult(dest, False, (refusal, *issues))
    try:
        written = _write_all(root, dest, to_write, options.memory_limit, progress)
    except _ConversionFailed as failed:
        return ConversionResult(dest, False, (failed.issue, *issues))
    files = sorted([*reused, *written], key=lambda entry: entry.relative_path)
    record = ConversionRecord(
        source_root=root.as_posix(),
        mapping=options.mapping,
        missing_as_singleton=options.missing_as_singleton,
        files=tuple(files),
        versions=package_versions(),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    sha256 = write_record(record, dest)
    logger.info("Converted %d images into %s", len(written), dest)
    return ConversionResult(
        destination=dest,
        written=True,
        issues=_sorted(issues),
        converted=tuple(e.relative_path for e in written if e.action == CONVERTED),
        copied=tuple(e.relative_path for e in written if e.action == COPIED),
        reused=tuple(e.relative_path for e in reused),
        record_sha256=sha256,
        suggested_axes=_suggested_axes(files),
    )


def _compare(
    root: Path,
    dest: Path,
    planned: tuple[PlannedCopy, ...],
    earlier: tuple[ConversionRecord, str] | None,
) -> tuple[list[CopyEntry], list[PlannedCopy], ValidationIssue | None]:
    """Split the plan into copies to reuse and to write; refuse anything else there."""
    if earlier is None:
        unknown = _unknown_content(dest, planned)
        return [], list(planned), _not_empty_issue(dest, unknown) if unknown else None
    record = earlier[0]
    if record.source_root != root.as_posix():
        return [], [], _other_experiment_issue(dest, record.source_root)
    reused, to_write, differing = [], [], []
    for item in planned:
        old = record.entry(item.relative_path)
        target = dest / item.relative_path
        if old is None:
            (differing if os.path.lexists(target) else to_write).append(item)
        elif old.same_plan(item.entry) and current_fingerprint(target) == old.copy:
            reused.append(old)
        else:
            differing.append(item)
    paths = [item.relative_path for item in differing]
    names = {item.relative_path for item in planned}
    paths += [e.relative_path for e in record.files if e.relative_path not in names]
    return reused, to_write, _differs_issue(dest, sorted(paths)) if paths else None


def _unknown_content(dest: Path, planned: tuple[PlannedCopy, ...]) -> list[str]:
    """Return the files in a folder without a record that this conversion did not leave.

    Only the lock and the ``.partial`` files of the planned copies (left by an interrupted
    conversion, removed before they are written again) are allowed.
    """
    partials = {item.relative_path + PARTIAL_SUFFIX for item in planned}
    found = []
    for folder, subfolders, names in os.walk(dest):
        relative = Path(folder).relative_to(dest)
        subfolders[:] = [d for d in subfolders if (relative / d).as_posix() not in partials]
        for name in names:
            path = (relative / name).as_posix()
            if path != LOCK_FILENAME and path not in partials:
                found.append(path)
    return sorted(found)


class _ConversionFailed(Exception):
    """A copy could not be written; carries the error for the user."""

    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def _write_all(
    root: Path,
    dest: Path,
    to_write: list[PlannedCopy],
    memory_limit: int,
    progress: Callable[[int, int, str], None] | None,
) -> list[CopyEntry]:
    """Write every copy as ``.partial``, then move them all into place.

    On a failure the ``.partial`` files written so far are removed, so the copy tree is
    left as it was, and :class:`_ConversionFailed` is raised.
    """
    partials: list[Path] = []
    try:
        for done, item in enumerate(to_write):
            if progress is not None:
                progress(done, len(to_write), item.relative_path)
            partials.append(_write_partial(root, dest, item, memory_limit))
    except (PlaneReadError, CopyWriteError, OSError, _SourceChanged) as exc:
        logger.debug("Conversion of %s failed", item.relative_path, exc_info=True)
        for partial in [*partials, _partial(dest, item)]:
            _remove(partial)
        raise _ConversionFailed(_failed_issue(item.relative_path, exc)) from exc
    if progress is not None:
        progress(len(to_write), len(to_write), "")
    entries = []
    for item, partial in zip(to_write, partials, strict=True):
        target = dest / item.relative_path
        os.replace(partial, target)
        entries.append(replace(item.entry, copy=Fingerprint.of(target)))
    return entries


class _SourceChanged(Exception):
    """A source image changed since it was checked, or while it was copied."""


def _write_partial(root: Path, dest: Path, item: PlannedCopy, memory_limit: int) -> Path:
    """Write one copy as ``<name>.partial``, refusing a source that changes meanwhile."""
    source, partial = root / item.relative_path, _partial(dest, item)
    partial.parent.mkdir(parents=True, exist_ok=True)
    _remove(partial)  # left by an interrupted conversion: it is never reused
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed since it was checked")
    if item.layout is not None:
        write_named_copy(source, partial, item.layout, item.mapping, memory_limit=memory_limit)
    elif source.is_dir():
        shutil.copytree(source, partial, copy_function=shutil.copyfile)
    else:
        shutil.copyfile(source, partial)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed while it was copied")
    return partial


def _partial(dest: Path, item: PlannedCopy) -> Path:
    """Return the ``.partial`` path of a copy."""
    return dest / (item.relative_path + PARTIAL_SUFFIX)


def _remove(path: Path) -> None:
    """Remove a file or folder this conversion wrote (or left earlier) if it is there."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _suggested_axes(files: list[CopyEntry]) -> str | None:
    """Return the roles for ``run --axes`` on the copies when they all have the same axes."""
    axes = {
        e.copy_axes if e.action == CONVERTED else e.source_axes
        for e in files
        if e.reason != "excluded"
    }
    if len(axes) != 1 or not next(iter(axes)):
        return None
    try:
        return prefill_mapping(next(iter(axes))).roles_string
    except ValueError:
        return None


def _folder_issues(root: Path, dest: Path) -> list[ValidationIssue]:
    """Refuse a copy tree inside the experiment, holding it, or that is a file."""
    if dest == root or root in dest.parents or dest in root.parents:
        return [
            error(
                "copy_folder_overlaps_experiment",
                "The folder for the named-axis copies is the experiment folder, inside it, or "
                "holds it. The copies must be kept apart from the originals, which are never "
                "changed.",
                [dest.as_posix()],
                "Choose a new folder outside the experiment, for example "
                f"{root.with_name(root.name + '_named_axes')}.",
            )
        ]
    if dest.exists() and not dest.is_dir():
        return [
            error(
                "copy_folder_not_a_folder",
                "The path given for the named-axis copies is a file, not a folder.",
                [dest.as_posix()],
                "Choose a new folder for the copies.",
            )
        ]
    return []


def _space_issue(dest: Path, to_write: list[PlannedCopy]) -> ValidationIssue | None:
    """Refuse a conversion that would not fit on the drive of the copy tree."""
    needed, free = sum(item.nbytes for item in to_write), free_space(dest)
    if free is None or needed <= free:
        return None
    return error(
        "not_enough_space",
        f"The copies need about {needed / 2**30:.1f} GiB, but the drive holding {dest} has "
        f"{free / 2**30:.1f} GiB free. Nothing was written.",
        [dest.as_posix()],
        "Free some space, or choose a folder on a drive with more space.",
    )


def _pixel_type_issue(paths: list[str], dtype: str) -> ValidationIssue:
    """Refuse to convert images of a pixel type that OME-TIFF cannot hold."""
    return error(
        "cannot_convert_pixel_type",
        f"{plural(len(paths), 'image')} with axis problems have the pixel type {dtype}, which "
        "OME-TIFF cannot hold, and pixel values are never changed to fit. Nothing was "
        "written.",
        paths,
        "Save these images again with their axes named in their metadata (for example as "
        "OME-TIFF in a 32-bit type, if their values fit), or correct the roles.",
    )


def _not_empty_issue(dest: Path, paths: list[str]) -> ValidationIssue:
    """Refuse a folder that holds files and no conversion record."""
    return error(
        "copy_folder_not_empty",
        f"The folder for the named-axis copies already holds {plural(len(paths), 'file')} "
        "and no conversion record, so it is not a copy tree this conversion can complete. "
        "Nothing was overwritten.",
        paths,
        f"Choose a new or empty folder for the copies, or empty {dest} first.",
    )


def _differs_issue(dest: Path, paths: list[str]) -> ValidationIssue:
    """Refuse to overwrite copies that differ from the ones this conversion would write."""
    return error(
        "copy_differs",
        f"{plural(len(paths), 'image')} in {dest} differ from the copies this conversion "
        "would write: their source or roles changed, the copy was changed, or the image is no "
        "longer in the experiment. Nothing was overwritten.",
        paths,
        "Convert into a new, empty folder, or remove these copies first (with the conversion "
        f"record {RECORD_FILENAME} if you remove the whole folder's contents).",
    )


def _other_experiment_issue(dest: Path, source_root: str) -> ValidationIssue:
    """Refuse a copy tree of another experiment."""
    return error(
        "copy_differs",
        f"{dest} holds named-axis copies of another experiment ({source_root}). Nothing was "
        "overwritten.",
        [RECORD_FILENAME],
        "Choose a new, empty folder for the copies of this experiment.",
    )


def _record_issue(exc: ConversionRecordError, dest: Path) -> ValidationIssue:
    """Refuse a copy tree whose conversion record cannot be read."""
    return error(
        "conversion_record_unreadable",
        f"The conversion record in {dest} cannot be read ({exc}), so its copies cannot be "
        "checked. Nothing was overwritten.",
        [RECORD_FILENAME],
        "Choose a new, empty folder for the copies.",
    )


def _failed_issue(path: str, exc: Exception) -> ValidationIssue:
    """Say that a copy could not be written, and that nothing was kept."""
    return error(
        "conversion_failed",
        f"{path} could not be copied ({exc}). The copies written so far in this conversion "
        "were removed; the folder is as it was.",
        [path],
        "Check that the image can be read and is not being changed, that the folder can be "
        "written, then convert again (or exclude the image).",
    )


def _lock_issue(exc: OutputLockedError) -> ValidationIssue:
    """Refuse a conversion while another one writes into the same folder."""
    return error(
        "output_locked",
        f"Another conversion is writing into this folder: {exc.holder.describe()}. Nothing "
        "was done.",
        [exc.path.as_posix()],
        "Wait for it to finish. If none is going on (it was stopped or the computer "
        "restarted), give --remove-stale-lock to the command, or call "
        "remove_stale_lock(folder), then convert again.",
    )


def _sorted(issues: Iterable[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    """Return the issues with the errors first."""
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
