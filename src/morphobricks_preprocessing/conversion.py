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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict, Unpack

from ._io import free_space
from .axes import AxisMapping, prefill_mapping
from .axis_check import ConfirmedAxes, confirmation_of
from .conversion_issues import (
    lock_issue,
    record_issue,
    space_issue,
)
from .conversion_plan import copy_folder_issues, plan_conversion
from .conversion_record import (
    CONVERTED,
    COPIED,
    PROJECTED,
    ConversionRecord,
    ConversionRecordError,
    CopyEntry,
    read_record,
    write_record,
)
from .conversion_reuse import compare
from .conversion_write import ConversionFailed, write_all
from .manifest import package_versions
from .ome_zarr_copy import OME_TIFF, STORES
from .planes import DEFAULT_MEMORY_LIMIT
from .planned_copy import PlannedCopy
from .projection import Projection
from .projection_issues import projection_differs_issue
from .run.lock import OutputLockedError, output_lock
from .validation import ValidationIssue, has_errors

logger = logging.getLogger(__name__)


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
    projected
        Images written as 2-D projections of their Z-stacks in this conversion, sorted.
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
    projection
        The method and slice range of a tree of projections (the record's), else None.
    """

    destination: Path
    written: bool
    issues: tuple[ValidationIssue, ...] = ()
    converted: tuple[str, ...] = ()
    copied: tuple[str, ...] = ()
    projected: tuple[str, ...] = ()
    reused: tuple[str, ...] = ()
    record_sha256: str | None = None
    suggested_axes: str | None = field(default=None)
    unknown_sizes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    projection: Projection | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "unknown_sizes", MappingProxyType(dict(self.unknown_sizes)))


class ConversionOptions(TypedDict, total=False):
    """The keyword options of :func:`convert_experiment` besides ``exclude``.

    They are keywords of the function, listed here because it takes them as one group (the
    code rules allow eight parameters). Any other keyword is a ``TypeError``.
    """

    missing_as_singleton: bool
    memory_limit: int
    progress: Callable[[int, int, str], None] | None
    masks_folder: str | os.PathLike | None
    projection: Projection | None
    store: str


def convert_experiment(
    experiment_root: str | os.PathLike,
    mapping: AxisMapping | ConfirmedAxes,
    destination: str | os.PathLike,
    *,
    exclude: Iterable[str] = (),
    **options: Unpack[ConversionOptions],
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
    **options
        The keywords of :class:`ConversionOptions`:

    missing_as_singleton
        The user confirmed that images lacking some axes have one plane along them.
    memory_limit
        Working-memory limit per plane while reading and writing, as the run's.
    progress
        Called as ``progress(done, total, relative_path)`` before each image is written.
    masks_folder
        The masks folder of the runs, if known (the CLI and the notebook give the one given
        or recorded by the run in the output folder): the copies may be neither inside it,
        where they would be read as masks, nor hold it (``copy_folder_overlaps_masks``).
        Unknown here, a run refuses a masks folder holding copies (``copies_inside_masks``).
        With ``projection`` it is also scanned, from the headers, for masks with depth
        slices (``projection_masks_3d``), which stop the projection.
    projection
        Write every image as a 2-D projection of its Z-stack (:class:`.projection.Projection`:
        a method, no default, and an optional slice range for the whole experiment) instead
        of a plain copy: the copy's axes are the confirmed roles without Z, its type is
        decided by the dtype table (:func:`.projection.projection_dtype`), and the record
        says how every copy was made (record version 4). Masks are never projected.
        Excluded images are still copied byte for byte. None converts as before.
    store
        ``"ome-zarr"`` writes every image that is read as an OME-Zarr copy (OME-NGFF 0.4,
        :mod:`.ome_zarr_copy`) named ``<position>.ome.zarr``, with or without a projection;
        the record keeps each source's own path. Default ``"ome-tiff"``: as before.

    Returns
    -------
    ConversionResult

    Raises
    ------
    ValueError
        If ``missing_as_singleton`` is not True or False, ``memory_limit`` is not a
        whole number of bytes above 0, or ``store`` is unknown (nothing is read or written
        then).
    """
    unknown = sorted(set(options) - set(ConversionOptions.__annotations__))
    if unknown:
        raise TypeError(f"convert_experiment() got unexpected keyword arguments {unknown}")
    missing_as_singleton = options.get("missing_as_singleton", False)
    memory_limit = options.get("memory_limit", DEFAULT_MEMORY_LIMIT)
    masks_folder, projection = options.get("masks_folder"), options.get("projection")
    progress, store = options.get("progress"), options.get("store", OME_TIFF)
    _check_arguments(missing_as_singleton, memory_limit, projection)
    if store not in STORES:
        raise ValueError(f"store must be one of {STORES}, found {store!r}")
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    refused = copy_folder_issues(root, dest, masks_folder)
    if refused:
        return ConversionResult(dest, False, tuple(refused))
    planned, issues = plan_conversion(
        root,
        mapping,
        dest,
        exclude,
        missing_as_singleton,
        memory_limit,
        projection=projection,
        masks_folder=masks_folder,
        store=store,
    )
    if has_errors(issues):
        return ConversionResult(dest, False, _sorted(issues))
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with output_lock(dest):
            options = _Options(
                confirmation_of(mapping)[0], missing_as_singleton, memory_limit, projection
            )
            return _locked_conversion(root, dest, planned, issues, options, progress)
    except OutputLockedError as exc:
        return ConversionResult(dest, False, (lock_issue(exc),))


def _check_arguments(
    missing_as_singleton: object, memory_limit: object, projection: object = None
) -> None:
    """Refuse arguments that are not allowed, before anything is read or written."""
    if projection is not None and not isinstance(projection, Projection):
        raise ValueError(f"projection must be a Projection or None, found {projection!r}")
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
    projection: Projection | None = None


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
    reused, to_write, refusal = compare(root, dest, planned, earlier)
    if refusal is not None and earlier is not None and earlier[0].projection != options.projection:
        refusal = projection_differs_issue(
            dest, refusal.paths, earlier[0].projection, options.projection
        )
    refusal = refusal or _space_issue(dest, to_write)
    if refusal is not None:
        return ConversionResult(dest, False, (refusal, *issues))
    try:
        written = write_all(root, dest, to_write, options.memory_limit, progress)
    except ConversionFailed as failed:
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
        projected=tuple(e.relative_path for e in written if e.action == PROJECTED),
        reused=tuple(e.relative_path for e in reused),
        record_sha256=sha256,
        suggested_axes=_suggested_axes(files),
        unknown_sizes={
            e.relative_path: tuple(sorted(e.unknown_sizes)) for e in files if e.unknown_sizes
        },
        projection=options.projection,
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
        same = (old.files, old.mapping, old.missing_as_singleton, old.projection) == (
            tuple(files),
            options.mapping,
            options.missing_as_singleton,
            options.projection,
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
        projection=options.projection,
    )
    return write_record(record, dest)


def _suggested_axes(files: list[CopyEntry]) -> str | None:
    """Return the roles for ``run --axes`` on the copies when they all have the same axes."""
    axes = {
        e.copy_axes if e.action in (CONVERTED, PROJECTED) else e.source_axes
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
