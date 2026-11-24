"""Plan a conversion: check the experiment and decide each image's copy (design v0.4 §5).

Moved from :mod:`.conversion` (PP-11c). :func:`plan_conversion` reads only the headers: the
axis errors and unnamed axes are what the conversion corrects; any other error stops it
before anything is written. :func:`preview_conversion` gives the same plan, with the folder
rule (:func:`copy_folder_issues`), the space the copies need and the free space, so the
notebook can show what will happen before the user starts the conversion (PP-11b); it
writes nothing.

One metadata epoch (issue #96): each image is fingerprinted before and after its header is
read (``preflight(bind_headers=True)``), and an image whose fingerprint differs is refused
(``source_changed_during_check``). The copy's axes, sizes, channel names and pixel type
come from that header, and the fingerprint taken with it is the one the copy's source must
still have before and after it is copied (:mod:`.conversion_write`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, Unpack

from ._io import free_space
from .axes import AxisMapping, AxisReview
from .axis_check import (
    AXES_NOT_NAMED,
    AXIS_ERROR_CODES,
    ConfirmedAxes,
    confirmation_of,
    header_axis_issue,
)
from .axis_review import review_axes
from .conversion_issues import (
    header_changed_issue,
    masks_overlap_issue,
    names_collide_issue,
    not_a_folder_issue,
    overlap_issue,
    path_too_long_issue,
    pixel_type_issue,
)
from .conversion_record import CONVERTED, COPIED, PROJECTED, RECORD_FILENAME, CopyEntry
from .conversion_write import WORK_FOLDER, partial_path
from .copy_axes import copy_layout
from .fingerprint import Fingerprint
from .links import relative_inside
from .metadata import TiffMetadata
from .ome_tiff_copy import copy_nbytes
from .ome_zarr_copy import OME_TIFF, OME_ZARR, STORES, as_zarr_copy
from .planes import DEFAULT_MEMORY_LIMIT, check_planes
from .planned_copy import PlannedCopy
from .projection import Projection
from .projection_masks import projection_mask_issues
from .projection_plan import plan_projection
from .run.preflight import Preflight, preflight
from .size_checks import Z_STACK_CODE
from .validation import ValidationIssue, has_errors

_NOT_IN_OME = frozenset({"int64", "uint64"})
"""Pixel types the brick reads that OME-TIFF cannot hold: they are never changed to fit."""


def plan_conversion(
    root: Path,
    mapping: AxisMapping | ConfirmedAxes,
    destination: Path,
    exclude: Iterable[str] = (),
    missing_as_singleton: bool = False,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    **how: Unpack[PlanOptions],
) -> tuple[tuple[PlannedCopy, ...], list[ValidationIssue]]:
    """Check the experiment with the confirmed roles and plan each image's copy.

    Only the headers are read, each between two fingerprints of its image (an image that
    changed meanwhile stops the conversion). The axis errors do not stop the conversion:
    they are what it corrects. Any other error does, and nothing is planned: so does an
    image to convert of a pixel type OME-TIFF cannot hold, or whose planes cannot be read
    within the memory limit, not even in blocks (checked as the run checks them, before
    anything is written).

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
    memory_limit
        As :func:`convert_experiment`.
    projection
        As :func:`convert_experiment`: every image is planned as a projected copy, and
        ``z_stack_not_supported`` no longer stops the plan (the projection is what it asks
        for); the refusals of :func:`.projection_plan.plan_projection` and of the masks
        (``masks_folder``, :func:`.projection_masks.projection_mask_issues`) do.
    masks_folder
        The masks folder, scanned for 3-D masks when ``projection`` is given.
    store
        ``"ome-zarr"`` plans every image that is read as an OME-Zarr copy
        (:mod:`.ome_zarr_copy`): also the images whose headers name their axes as confirmed,
        which are otherwise copied byte for byte. Two images that would get the same copy
        name stop the plan (``copy_names_collide``). Default ``"ome-tiff"``.

    On Windows, a copy any of whose files would have a path longer than
    :data:`WINDOWS_MAX_PATH` characters, or any of whose folders a path longer than
    :data:`WINDOWS_MAX_FOLDER`, while it is written in the work folder, stops the plan too
    (``copy_path_too_long``, :func:`long_path_issue`); not when Windows' long paths are
    enabled (``LongPathsEnabled``).

    Returns
    -------
    tuple
        The planned copies, sorted by path (empty when an error stops the conversion),
        and the issues: the errors that stop it, and the warnings.
    """
    planned, issues = _plan(
        root, mapping, destination, exclude, missing_as_singleton, memory_limit, **how
    )
    too_long = long_path_issue(root, destination, planned) if _windows() else None
    if too_long is not None:
        return (), [*issues, too_long]
    return planned, issues


def _plan(
    root: Path,
    mapping: AxisMapping | ConfirmedAxes,
    destination: Path,
    exclude: Iterable[str],
    missing_as_singleton: bool,
    memory_limit: int,
    **how: Unpack[PlanOptions],
) -> tuple[tuple[PlannedCopy, ...], list[ValidationIssue]]:
    """Plan each image's copy: :func:`plan_conversion` but the length of the copies' paths."""
    projection, masks_folder = how.get("projection"), how.get("masks_folder")
    as_zarr = how.get("store", OME_TIFF) == OME_ZARR
    pre = preflight(root, destination, exclude=exclude, bind_headers=True)
    unsettled = sorted(rel for rel, bound in pre.header_fingerprints.items() if bound is None)
    if unsettled:
        return (), [header_changed_issue(unsettled)]
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    others = [issue for issue in pre.issues if issue not in pre.metadata.issues]
    issues = [*others, *review.combined_issues(pre.metadata)]
    if projection is not None:
        issues = [i for i in issues if not (i.is_error and i.code == Z_STACK_CODE)]
    if any(i.is_error and i.code not in AXIS_ERROR_CODES for i in issues):
        return (), [i for i in issues if not (i.is_error and i.code in AXIS_ERROR_CODES)]
    # The axis errors and unnamed axes are what is corrected (each entry records its code).
    issues = [i for i in issues if not i.is_error and i.code != AXES_NOT_NAMED]
    to_convert = set(review.axis_error_files) | set(review.confirmed_files)
    headers = {meta.relative_path: meta for meta in pre.metadata.files}
    if projection is not None:
        planned, issues = _plan_projected(
            pre, review, mapping, projection, issues, (memory_limit, masks_folder)
        )
        return _stored(planned, issues, as_zarr)
    if as_zarr:  # every TIFF image that is read is written, not only those to correct
        to_convert = {
            rel
            for rel, meta in headers.items()
            if rel in review.file_mappings and not meta.is_ome_zarr
        }
    issues += _cannot_convert(root, to_convert, headers, review.file_mappings, memory_limit)
    if has_errors(issues):
        return (), issues
    planned = []
    for rel in pre.discovery.images:
        # An excluded image is never read: its fingerprint is taken now, for the byte copy.
        source = pre.header_fingerprints.get(rel) or Fingerprint.of(root / rel)
        if rel in to_convert:
            planned.append(_converted(headers[rel], review.file_mappings[rel], source))
        else:
            planned.append(_copied(rel, headers.get(rel), source, rel in pre.excluded))
    return _stored(tuple(planned), issues, as_zarr)


WINDOWS_MAX_PATH = 259
"""The longest path Windows handles without long-path support (``MAX_PATH`` 260, with the
terminating NUL)."""
WINDOWS_MAX_FOLDER = 247
"""The longest folder path Windows makes without long-path support (``MAX_PATH`` less 12,
room for an 8.3 file name): a longer one fails at ``mkdir`` (issue #488)."""
_FILE_SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"
"""The registry key (``HKEY_LOCAL_MACHINE``) holding Windows' ``LongPathsEnabled``."""


def _windows() -> bool:
    """Return True where Windows' path limits apply: Windows without long paths enabled."""
    return os.name == "nt" and not _long_paths_enabled()


def _long_paths_enabled() -> bool:
    """Return True when Windows' ``LongPathsEnabled`` setting is 1; False if it cannot be read.

    Called on Windows only (``winreg`` exists nowhere else). Python itself is long-path aware
    since 3.6, so with the setting on, neither limit applies to what the conversion writes.
    """
    try:
        import winreg  # Windows only
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FILE_SYSTEM_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return False
    return value == 1


def long_path_issue(
    root: Path, dest: Path, planned: tuple[PlannedCopy, ...]
) -> ValidationIssue | None:
    """Refuse copies with a path too long for Windows (issues #482 and #488).

    Each copy is written in the work folder (:func:`.conversion_write.partial_path`), whose
    path is longer than its place in the copy tree; its deepest file must fit
    :data:`WINDOWS_MAX_PATH` and its deepest folder :data:`WINDOWS_MAX_FOLDER`. An OME-Zarr
    copy's deepest file is a plane's chunk (``0/<index>/.../0/0``), and a folder copied byte
    for byte is read for its deepest file and folder.
    """
    work = dest / WORK_FOLDER
    lengths = {item.relative_path: _longest(root, work, item) for item in planned}
    too_long = sorted(
        rel
        for rel, (file, folder) in lengths.items()
        if file > WINDOWS_MAX_PATH or folder > WINDOWS_MAX_FOLDER
    )
    if not too_long:
        return None
    files = max(file for file, _ in lengths.values())
    folders = max(folder for _, folder in lengths.values())
    found = {"file": (files, WINDOWS_MAX_PATH), "folder": (folders, WINDOWS_MAX_FOLDER)}
    over = {kind: sizes for kind, sizes in found.items() if sizes[0] > sizes[1]}
    return path_too_long_issue(dest, too_long, over)


def _longest(root: Path, work: Path, item: PlannedCopy) -> tuple[int, int]:
    """Return how long a copy's deepest file path and deepest folder path are in ``work``."""
    path = partial_path(work, item)
    length = len(str(path))
    if item.layout is not None and item.store == OME_ZARR:
        chunk = ["0", *(str(size - 1) for size in item.layout.shape[:-2]), "0", "0"]
        file = max(len(os.sep.join(chunk)), len(os.sep.join(["0", ".zarray"]))) + 1
        return length + file, length + len(os.sep.join(chunk[:-1])) + 1
    source = root / item.source_path
    if item.layout is not None or not source.is_dir():
        return length, len(str(path.parent))
    file = folder = 0
    for where, _, names in os.walk(source):
        below = os.path.relpath(where, source)
        if below != os.curdir:
            folder = max(folder, len(below) + 1)
        for name in names:
            file = max(file, len(os.path.relpath(os.path.join(where, name), source)) + 1)
    return length + file, length + folder


class PlanOptions(TypedDict, total=False):
    """The keyword options of :func:`plan_conversion` besides the folders and the limit."""

    projection: Projection | None
    masks_folder: str | os.PathLike | None
    store: str


def _stored(
    planned: tuple[PlannedCopy, ...], issues: list[ValidationIssue], as_zarr: bool
) -> tuple[tuple[PlannedCopy, ...], list[ValidationIssue]]:
    """Return the plan with its copies as OME-Zarr when asked; refuse two with one name."""
    if not as_zarr or not planned:
        return planned, issues
    renamed = tuple(as_zarr_copy(item) for item in planned)
    names: dict[str, list[str]] = {}
    for item in renamed:
        names.setdefault(item.relative_path.casefold(), []).append(item.source_path)
    twice = sorted(path for paths in names.values() if len(paths) > 1 for path in paths)
    if twice:
        return (), [*issues, names_collide_issue(twice)]
    return tuple(sorted(renamed, key=lambda item: item.relative_path)), issues


def _plan_projected(
    pre: Preflight,
    review: AxisReview,
    mapping: AxisMapping | ConfirmedAxes,
    projection: Projection,
    issues: list[ValidationIssue],
    limits: tuple[int, str | os.PathLike | None],
) -> tuple[tuple[PlannedCopy, ...], list[ValidationIssue]]:
    """Plan every image that is read as a projected copy, the excluded ones as plain copies."""
    memory_limit, masks_folder = limits
    headers = {meta.relative_path: meta for meta in pre.metadata.files}
    issues = [
        *issues,
        *projection_mask_issues(masks_folder, pre.discovery, pre.metadata.files, set(headers)),
    ]
    sources = {rel: pre.header_fingerprints.get(rel) for rel in headers}
    projected, refused = plan_projection(
        pre.discovery.root,
        headers,
        review.file_mappings,
        sources,
        projection,
        roles=confirmation_of(mapping)[0].roles_string,
        memory_limit=memory_limit,
    )
    issues = [*issues, *refused]
    if has_errors(issues):
        return (), issues
    planned = []
    for rel in pre.discovery.images:
        if rel in projected:
            planned.append(projected[rel])
        else:  # excluded: never read, copied byte for byte
            planned.append(_copied(rel, None, Fingerprint.of(pre.discovery.root / rel), True))
    return tuple(planned), issues


def _cannot_convert(
    root: Path,
    to_convert: set[str],
    headers: dict[str, TiffMetadata],
    mappings: Mapping[str, AxisMapping],
    memory_limit: int,
) -> list[ValidationIssue]:
    """Return why images cannot be converted (errors) and the planes read in blocks."""
    wide = sorted(rel for rel in to_convert if headers[rel].dtype.name in _NOT_IN_OME)
    if wide:
        return [pixel_type_issue(wide, headers[wide[0]].dtype.name)]
    return check_planes(root, {rel: mappings[rel] for rel in to_convert}, memory_limit=memory_limit)


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
        channel_names=layout.channel_names,
        significant_bits=layout.significant_bits,
    )
    return PlannedCopy(entry, layout, mapping, copy_nbytes(layout, meta.dtype), meta.dtype)


def _copied(
    rel: str, meta: TiffMetadata | None, source: Fingerprint, excluded: bool
) -> PlannedCopy:
    """Plan the byte-for-byte copy of one image (its header as it is, or excluded unread)."""
    entry = CopyEntry(
        relative_path=rel,
        action=COPIED,
        reason="excluded" if excluded else None,
        source=source,
        source_axes=meta.axes if meta else "",
        channel_names=meta.channel_names if meta else (),
        significant_bits=meta.significant_bits if meta else None,
    )
    return PlannedCopy(entry, nbytes=source.size)


def copy_folder_issues(
    experiment_root: str | os.PathLike,
    destination: str | os.PathLike,
    masks_folder: str | os.PathLike | None = None,
) -> list[ValidationIssue]:
    """Refuse a folder for the copies overlapping the experiment or masks, or that is a file.

    The rule :func:`.conversion.convert_experiment` applies first, before anything is read.

    Parameters
    ----------
    experiment_root
        The experiment folder.
    destination
        The folder for the copies.
    masks_folder
        The masks folder of the runs, if known: the copies may not be inside it (they would
        be read as masks, audit B1) nor hold it.

    Returns
    -------
    list of ValidationIssue
        ``copy_folder_overlaps_experiment``, ``copy_folder_overlaps_masks`` or
        ``copy_folder_not_a_folder``; empty when the folder can hold the copies.
    """
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    if relative_inside(dest, root) is not None or relative_inside(root, dest) is not None:
        return [overlap_issue(root, dest)]
    masks = None if masks_folder is None else Path(masks_folder).resolve()
    if masks is not None and (
        relative_inside(dest, masks) is not None or relative_inside(masks, dest) is not None
    ):
        return [masks_overlap_issue(dest)]
    if dest.exists() and not dest.is_dir():
        return [not_a_folder_issue(dest)]
    return []


@dataclass(frozen=True)
class ConversionPreview:
    """What a conversion would write, worked out before anything is written.

    Attributes
    ----------
    destination
        The folder for the copies (absolute).
    issues
        The errors that would stop the conversion (the folder rule, then the checks of
        :func:`plan_conversion`), and the warnings.
    converted
        Images that would be written as named-axis copies (OME-TIFF, or OME-Zarr with
        ``store="ome-zarr"``), with their axis code, sorted.
    projected
        Images that would be written as projections (with ``projection``), sorted.
    copied
        Images that would be copied byte for byte, sorted (``"excluded"`` or None).
    needed_bytes
        The space all the copies take; less when unchanged copies of an earlier conversion
        into the folder are reused.
    free_bytes
        The free space on the drive of the folder, or None when unknown.
    earlier_conversion
        True when the folder already holds a conversion record: its unchanged copies are
        reused, and anything else that differs is refused.
    """

    destination: Path
    issues: tuple[ValidationIssue, ...] = ()
    converted: tuple[tuple[str, str], ...] = ()
    copied: tuple[tuple[str, str | None], ...] = ()
    projected: tuple[str, ...] = ()
    needed_bytes: int = 0
    free_bytes: int | None = None
    earlier_conversion: bool = False

    @property
    def ok(self) -> bool:
        """True when nothing found so far stops the conversion."""
        return not has_errors(self.issues)

    @property
    def fits(self) -> bool:
        """True when the copies fit in the free space, or the free space is unknown."""
        return self.free_bytes is None or self.needed_bytes <= self.free_bytes


def preview_conversion(
    experiment_root: str | os.PathLike,
    mapping: AxisMapping | ConfirmedAxes,
    destination: str | os.PathLike,
    *,
    exclude: Iterable[str] = (),
    missing_as_singleton: bool = False,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    **how: Unpack[PlanOptions],
) -> ConversionPreview:
    """Work out what :func:`.conversion.convert_experiment` would write; write nothing.

    The same folder rule and plan as the conversion, from the headers only. The conversion
    checks everything again under the folder's lock, which decides (a copy already in the
    folder that differs, the lock of another conversion, the space actually needed).

    Parameters
    ----------
    experiment_root
        The experiment folder.
    mapping
        The roles the user confirmed.
    destination
        The folder for the copies.
    exclude
        Images to leave unread (copied byte for byte).
    missing_as_singleton
        As :func:`.conversion.convert_experiment`.
    memory_limit
        As :func:`.conversion.convert_experiment`.
    **how
        The keywords of :class:`PlanOptions`, as :func:`.conversion.convert_experiment`:
        ``masks_folder``, ``projection`` and ``store``. The plan is the conversion's, so a
        projection or OME-Zarr copies are counted as they will be written (their names,
        their size, the length of their paths on Windows, issue #488).

    Returns
    -------
    ConversionPreview

    Raises
    ------
    TypeError
        For any other keyword.
    ValueError
        If ``store`` is unknown or ``projection`` is not a :class:`.projection.Projection`.
    """
    unknown = sorted(set(how) - set(PlanOptions.__annotations__))
    if unknown:
        raise TypeError(f"preview_conversion() got unexpected keyword arguments {unknown}")
    if how.get("store", OME_TIFF) not in STORES:
        raise ValueError(f"store must be one of {STORES}, found {how['store']!r}")
    if not isinstance(how.get("projection"), Projection | None):
        raise ValueError(f"projection must be a Projection or None, found {how['projection']!r}")
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    refused = copy_folder_issues(root, dest, how.get("masks_folder"))
    if refused:
        return ConversionPreview(dest, tuple(refused))
    planned, issues = plan_conversion(
        root, mapping, dest, exclude, missing_as_singleton, memory_limit, **how
    )
    entries = [item.entry for item in planned]
    return ConversionPreview(
        destination=dest,
        issues=tuple(sorted(issues, key=lambda issue: not issue.is_error)),
        converted=tuple((e.relative_path, e.reason) for e in entries if e.action == CONVERTED),
        copied=tuple((e.relative_path, e.reason) for e in entries if e.action == COPIED),
        projected=tuple(e.relative_path for e in entries if e.action == PROJECTED),
        needed_bytes=sum(item.nbytes for item in planned),
        free_bytes=free_space(dest),
        earlier_conversion=(dest / RECORD_FILENAME).is_file(),
    )
