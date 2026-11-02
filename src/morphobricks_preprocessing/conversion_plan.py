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

import numpy as np

from ._io import free_space
from .axes import AxisMapping
from .axis_check import (
    AXES_NOT_NAMED,
    AXIS_ERROR_CODES,
    ConfirmedAxes,
    header_axis_issue,
)
from .axis_review import review_axes
from .conversion_issues import (
    header_changed_issue,
    masks_overlap_issue,
    not_a_folder_issue,
    overlap_issue,
    pixel_type_issue,
)
from .conversion_record import CONVERTED, COPIED, RECORD_FILENAME, CopyEntry
from .copy_axes import CopyLayout, copy_layout
from .fingerprint import Fingerprint
from .folder_rules import relative_inside
from .links import is_inside
from .metadata import TiffMetadata
from .ome_tiff_copy import copy_nbytes
from .planes import DEFAULT_MEMORY_LIMIT, check_planes
from .run.preflight import preflight
from .validation import ValidationIssue, has_errors

_NOT_IN_OME = frozenset({"int64", "uint64"})
"""Pixel types the brick reads that OME-TIFF cannot hold: they are never changed to fit."""


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
    dtype
        The pixel type a converted image's header gives (native byte order), else None.
    """

    entry: CopyEntry
    layout: CopyLayout | None = None
    mapping: AxisMapping | None = None
    nbytes: int = 0
    dtype: np.dtype | None = None

    @property
    def relative_path(self) -> str:
        """The image, relative to the experiment and to the copy tree."""
        return self.entry.relative_path


def plan_conversion(
    root: Path,
    mapping: AxisMapping | ConfirmedAxes,
    destination: Path,
    exclude: Iterable[str] = (),
    missing_as_singleton: bool = False,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
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

    Returns
    -------
    tuple
        The planned copies, sorted by path (empty when an error stops the conversion),
        and the issues: the errors that stop it, and the warnings.
    """
    pre = preflight(root, destination, exclude=exclude, bind_headers=True)
    unsettled = sorted(rel for rel, bound in pre.header_fingerprints.items() if bound is None)
    if unsettled:
        return (), [header_changed_issue(unsettled)]
    review = review_axes(pre.metadata, mapping, missing_as_singleton=missing_as_singleton)
    others = [issue for issue in pre.issues if issue not in pre.metadata.issues]
    issues = [*others, *review.combined_issues(pre.metadata)]
    if any(i.is_error and i.code not in AXIS_ERROR_CODES for i in issues):
        return (), [i for i in issues if not (i.is_error and i.code in AXIS_ERROR_CODES)]
    # The axis errors and unnamed axes are what is corrected (each entry records its code).
    issues = [i for i in issues if not i.is_error and i.code != AXES_NOT_NAMED]
    to_convert = set(review.axis_error_files) | set(review.confirmed_files)
    headers = {meta.relative_path: meta for meta in pre.metadata.files}
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
    if is_inside(dest, root) or is_inside(root, dest):
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
        Images that would be written as named-axis OME-TIFF, with their axis code, sorted.
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
    masks_folder: str | os.PathLike | None = None,
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
    masks_folder
        As :func:`.conversion.convert_experiment`.

    Returns
    -------
    ConversionPreview
    """
    root, dest = Path(experiment_root).resolve(), Path(destination).resolve()
    refused = copy_folder_issues(root, dest, masks_folder)
    if refused:
        return ConversionPreview(dest, tuple(refused))
    planned, issues = plan_conversion(
        root, mapping, dest, exclude, missing_as_singleton, memory_limit
    )
    entries = [item.entry for item in planned]
    return ConversionPreview(
        destination=dest,
        issues=tuple(sorted(issues, key=lambda issue: not issue.is_error)),
        converted=tuple((e.relative_path, e.reason) for e in entries if e.action == CONVERTED),
        copied=tuple((e.relative_path, e.reason) for e in entries if e.action == COPIED),
        needed_bytes=sum(item.nbytes for item in planned),
        free_bytes=free_space(dest),
        earlier_conversion=(dest / RECORD_FILENAME).is_file(),
    )
