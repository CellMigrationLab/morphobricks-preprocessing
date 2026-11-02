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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from ._io import free_space
from .axes import AxisMapping, prefill_mapping
from .axis_check import ConfirmedAxes, confirmation_of
from .conversion_issues import (
    differs_issue,
    failed_issue,
    lock_issue,
    not_empty_issue,
    other_experiment_issue,
    record_issue,
    space_issue,
)
from .conversion_plan import PlannedCopy, copy_folder_issues, plan_conversion
from .conversion_record import (
    CONVERTED,
    COPIED,
    ConversionRecord,
    ConversionRecordError,
    CopyEntry,
    read_record,
    write_record,
)
from .fingerprint import Fingerprint, current_fingerprint
from .manifest import package_versions
from .ome_tiff_copy import CopyWriteError, write_named_copy
from .planes import DEFAULT_MEMORY_LIMIT, PlaneReadError
from .run.lock import LOCK_FILENAME, OutputLockedError, output_lock
from .validation import ValidationIssue, has_errors

logger = logging.getLogger(__name__)

PARTIAL_SUFFIX = ".partial"
"""A copy is written as ``<name>.partial`` and renamed once every copy is written."""


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
    unknown_sizes
        ``{image: roles}`` for each copy in the tree whose source header gives pixel sizes
        that the copy does not carry (a relabelled axis gets an unknown scale), sorted; the
        user states them (``calibration.csv``) if known.
    """

    destination: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    converted: tuple[str, ...] = ()
    copied: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    record_sha256: str | None = None
    suggested_axes: str | None = field(default=None)
    unknown_sizes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "unknown_sizes", MappingProxyType(dict(self.unknown_sizes)))


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

    Raises
    ------
    ValueError
        If ``missing_as_singleton`` is not True or False, or ``memory_limit`` is not a
        whole number of bytes above 0 (nothing is read or written then).
    """
    _check_arguments(missing_as_singleton, memory_limit)
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    refused = copy_folder_issues(root, dest)
    if refused:
        return ConversionResult(dest, False, tuple(refused))
    planned, issues = plan_conversion(
        root, mapping, dest, exclude, missing_as_singleton, memory_limit
    )
    if has_errors(issues):
        return ConversionResult(dest, False, _sorted(issues))
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(dest):
            options = _Options(confirmation_of(mapping)[0], missing_as_singleton, memory_limit)
            return _locked_conversion(root, dest, planned, issues, options, progress)
    except OutputLockedError as exc:
        return ConversionResult(dest, False, (lock_issue(exc),))


def _check_arguments(missing_as_singleton: object, memory_limit: object) -> None:
    """Refuse arguments that are not allowed, before anything is read or written."""
    if not isinstance(missing_as_singleton, bool):
        raise ValueError(
            f"missing_as_singleton must be True or False, found {missing_as_singleton!r}"
        )
    if isinstance(memory_limit, bool) or not isinstance(memory_limit, int) or memory_limit < 1:
        raise ValueError(
            f"memory_limit must be a whole number of bytes above 0, found {memory_limit!r}"
        )


@dataclass(frozen=True)
class _Options:
    """What the conversion records and how it reads, besides the plan."""

    mapping: AxisMapping
    missing_as_singleton: bool
    memory_limit: int


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
        return ConversionResult(dest, False, (record_issue(exc, dest),))
    reused, to_write, refusal = _compare(root, dest, planned, earlier)
    refusal = refusal or _space_issue(dest, to_write)
    if refusal is not None:
        return ConversionResult(dest, False, (refusal, *issues))
    try:
        written = _write_all(root, dest, to_write, options.memory_limit, progress)
    except _ConversionFailed as failed:
        return ConversionResult(dest, False, (failed.issue, *issues))
    files = sorted([*reused, *written], key=lambda entry: entry.relative_path)
    sha256 = _publish_record(root, dest, files, options, earlier)
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
        unknown_sizes={
            e.relative_path: tuple(sorted(e.unknown_sizes)) for e in files if e.unknown_sizes
        },
    )


def _publish_record(
    root: Path,
    dest: Path,
    files: list[CopyEntry],
    options: _Options,
    earlier: tuple[ConversionRecord, str] | None,
) -> str:
    """Write the conversion record; keep the earlier one when it would say the same.

    A conversion that reuses every copy leaves the record as it was, so a run on the
    copies, which recorded its SHA-256, stays valid (``RunInputs`` checks it).
    """
    if earlier is not None:
        old, sha256 = earlier
        same = (old.files, old.mapping, old.missing_as_singleton) == (
            tuple(files),
            options.mapping,
            options.missing_as_singleton,
        )
        if same:
            return sha256
    record = ConversionRecord(
        source_root=root.as_posix(),
        mapping=options.mapping,
        missing_as_singleton=options.missing_as_singleton,
        files=tuple(files),
        versions=package_versions(),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    return write_record(record, dest)


def _compare(
    root: Path,
    dest: Path,
    planned: tuple[PlannedCopy, ...],
    earlier: tuple[ConversionRecord, str] | None,
) -> tuple[list[CopyEntry], list[PlannedCopy], ValidationIssue | None]:
    """Split the plan into copies to reuse and to write; refuse anything else there."""
    if earlier is None:
        unknown = _unknown_content(dest, planned)
        return [], list(planned), not_empty_issue(dest, unknown) if unknown else None
    record = earlier[0]
    if record.source_root != root.as_posix():
        return [], [], other_experiment_issue(dest, record.source_root)
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
    return reused, to_write, differs_issue(dest, sorted(paths)) if paths else None


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


_FAILURES = (PlaneReadError, CopyWriteError, OSError, ValueError)
"""What reading a source or writing a copy raises when it fails (``tifffile`` raises
:class:`ValueError` for a file it cannot write); anything else is re-raised, after the same
clean-up."""


def _write_all(
    root: Path,
    dest: Path,
    to_write: list[PlannedCopy],
    memory_limit: int,
    progress: Callable[[int, int, str], None] | None,
) -> list[CopyEntry]:
    """Write every copy as ``.partial``, then move them all into place.

    On any failure, an interruption included, every ``.partial`` file of the conversion,
    every copy already moved into place (each where nothing was before) and every folder
    the conversion made are removed, so the copy tree is left as it was; a failure to read
    or write raises :class:`_ConversionFailed`, anything else is re-raised.
    """
    made = _missing_folders(dest, to_write)
    written: list[Path] = []
    current = to_write[0].relative_path if to_write else ""
    try:
        for done, item in enumerate(to_write):
            current = item.relative_path
            if progress is not None:
                progress(done, len(to_write), current)
            _write_partial(root, dest, item, memory_limit)
        if progress is not None:
            progress(len(to_write), len(to_write), "")
        entries = []
        for item in to_write:
            current, target = item.relative_path, dest / item.relative_path
            os.replace(_partial(dest, item), target)
            written.append(target)
            entries.append(replace(item.entry, copy=Fingerprint.of(target)))
    except BaseException as exc:
        logger.debug("Conversion of %s failed", current, exc_info=True)
        for path in [*written, *(_partial(dest, item) for item in to_write)]:
            _remove(path)
        _remove_empty(made)
        if isinstance(exc, (*_FAILURES, _SourceChanged)):
            raise _ConversionFailed(failed_issue(current, exc)) from exc
        raise
    return entries


def _missing_folders(dest: Path, to_write: list[PlannedCopy]) -> list[Path]:
    """Return the folders of the copy tree that writing these copies will make."""
    missing = set()
    for item in to_write:
        folder = (dest / item.relative_path).parent
        while folder != dest and not folder.exists():
            missing.add(folder)
            folder = folder.parent
    return sorted(missing, key=lambda folder: len(folder.parts), reverse=True)


def _remove_empty(folders: list[Path]) -> None:
    """Remove the folders this conversion made, deepest first, when they are empty."""
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            logger.debug("Folder %s kept: not empty or gone", folder, exc_info=True)


class _SourceChanged(Exception):
    """A source image changed since it was checked, or while it was copied."""


def _write_partial(root: Path, dest: Path, item: PlannedCopy, memory_limit: int) -> None:
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


def _space_issue(dest: Path, to_write: list[PlannedCopy]) -> ValidationIssue | None:
    """Refuse a conversion that would not fit on the drive of the copy tree."""
    needed, free = sum(item.nbytes for item in to_write), free_space(dest)
    if free is None or needed <= free:
        return None
    return space_issue(dest, needed, free)


def _sorted(issues: Iterable[ValidationIssue]) -> tuple[ValidationIssue, ...]:
    """Return the issues with the errors first."""
    return tuple(sorted(issues, key=lambda issue: not issue.is_error))
