"""Calibration: the pixel sizes the user states, and the size each image is recorded with.

Design v0.4 §3. For each space axis (Y, X, Z) an image's size comes from its header, or is
stated by the user in ``calibration.csv`` in the output folder, for the whole experiment, a
folder (a condition or a repeat) or one image. A statement gives a size in a supported
length unit (:func:`.physical_sizes.space_unit`), or ``"pixels"``: deliberately
uncalibrated. Nothing is guessed: an axis with neither is uncalibrated, and the report says
so. Each axis is recorded with its source, ``"header"``, ``"stated"`` or ``"pixels"``
(:class:`.recorded_sizes.RecordedSize`).

- **Targets.** ``"."`` is the whole experiment; any other target is a folder or an image,
  relative to the experiment folder, with ``/`` separators, compared exactly. The most
  specific statement that covers an image applies: an image's own over its folders', a
  deeper folder's over a higher one's, any folder's over the whole experiment's.
- **Roles, never positions.** A statement names the role it sizes; the header's sizes are
  by the axes the header names, which are the roles of every image that passes the axis
  check (contract §2). Nothing exchanges the sizes of Y and X.
- **Conflicts.** A statement that differs from the header is the warning
  ``calibration_differs_from_header`` naming the images, and the statement is used.
- **Unknown targets.** A statement whose target is neither a folder nor an image of the
  experiment is the error ``calibration_target_not_found``: it is never dropped silently.
- **Only axes the image has.** A Z or T size is recorded only for an image with that role
  (:func:`fit_to_roles`): a max projection keeps the z-step of its stack in its metadata,
  which is not a size of the projection. A statement for an axis an image lacks is the
  warning ``calibration_axis_absent`` naming the images, and is not recorded for them.
- **No row depends on it.** Changing the calibration recomputes no row.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from .physical_sizes import UnsupportedUnitError, same_pixel_size, space_unit, to_micrometers
from .recorded_sizes import PIXELS, SIZE_ROLES, STATED, RecordedSize, header_sizes
from .validation import ValidationIssue, error, plural, warning

if TYPE_CHECKING:
    from .metadata import TiffMetadata

CALIBRATION_FILENAME = "calibration.csv"
CALIBRATION_AXES = ("X", "Y", "Z")
"""The axes a size can be stated for (the time step comes from the header only)."""
EXPERIMENT = "."
"""The target of a statement for the whole experiment."""
CALIBRATION_CODES = ("calibration_unreadable", "calibration_target_not_found")
"""The errors about ``calibration.csv`` itself."""

_SHOWN = 5
"""Images described one by one in a message; every one is in its paths."""
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
"""A size as text: plain ASCII decimal notation (no ``_`` separators, no other digits)."""


@dataclass(frozen=True)
class CalibrationEntry:
    """One statement: the size of one step along one axis, for a target.

    Use :func:`calibration_entry` to build one from what the user wrote.

    Attributes
    ----------
    target
        ``"."`` (the whole experiment), or a folder or image relative to the experiment
        folder, with ``/`` separators.
    axis
        ``"X"``, ``"Y"`` or ``"Z"``.
    size
        A positive, finite size, or None for ``"pixels"``.
    unit
        The OME-NGFF name of the unit (``"micrometer"``), or ``"pixels"``.
    """

    target: str
    axis: str
    size: float | None
    unit: str

    @property
    def in_pixels(self) -> bool:
        """True for a statement that the axis is measured in pixels (uncalibrated)."""
        return self.unit == PIXELS

    def recorded(self) -> RecordedSize:
        """Return the size this statement records."""
        if self.in_pixels:
            return RecordedSize(self.axis, source=PIXELS)
        return RecordedSize(self.axis, self.size, self.unit, STATED)

    def covers(self, path: str) -> bool:
        """Return True when the statement applies to the image at ``path``."""
        return (
            self.target == EXPERIMENT or path == self.target or path.startswith(self.target + "/")
        )

    def describe(self) -> str:
        """Return the statement in words, e.g. ``"0.5 micrometer"`` or ``"pixels"``."""
        return PIXELS if self.in_pixels else f"{self.size:g} {self.unit}"


def calibration_entry(target: str, axis: str, size: object, unit: str) -> CalibrationEntry:
    """Build one statement from what the user wrote, refusing anything not exact.

    Parameters
    ----------
    target
        ``"."`` or a relative folder or image path (backslashes are read as ``/``;
        a leading ``./`` and a trailing ``/`` are dropped).
    axis
        ``"X"``, ``"Y"`` or ``"Z"`` (upper case).
    size
        A positive, finite number (or its text in plain decimal notation, such as ``"0.65"``
        or ``"1e-3"``) that is also one in micrometres (issue #93), or None/``""`` with the
        unit ``"pixels"``.
    unit
        A supported length unit in any spelling :func:`.physical_sizes.space_unit` reads,
        or ``"pixels"``.

    Returns
    -------
    CalibrationEntry
        With the target normalised and the unit's OME-NGFF name.

    Raises
    ------
    ValueError
        Saying what is wrong: the target, axis, size or unit.
    """
    if axis not in CALIBRATION_AXES:
        raise ValueError(f"the axis {axis!r} is not X, Y or Z")
    target = _target(target)
    if unit == PIXELS:
        if size not in (None, ""):
            raise ValueError(
                f"a statement in pixels has no size (found {size!r}): leave the size empty"
            )
        return CalibrationEntry(target, axis, None, PIXELS)
    name, size = _unit(unit), _size(size)
    to_micrometers(size, name)  # refuses one beyond micrometres: 1e308 m is inf (issue #93)
    return CalibrationEntry(target, axis, size, name)


def _target(text: str) -> str:
    """Return a statement's target normalised, refusing one outside the experiment."""
    if not isinstance(text, str) or text != text.strip() or not text:
        raise ValueError(f"the target {text!r} is empty or has spaces around it")
    target = text.replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    target = target.rstrip("/") or EXPERIMENT
    if target == EXPERIMENT:
        return EXPERIMENT
    parts = target.split("/")
    if target.startswith("/") or ":" in parts[0] or any(p in ("", ".", "..") for p in parts):
        raise ValueError(
            f"the target {text!r} is not a folder or image inside the experiment folder"
        )
    return target


def _unit(text: str) -> str:
    """Return the OME-NGFF name of a stated length unit, refusing any other."""
    try:
        name = space_unit(text)
    except UnsupportedUnitError as exc:
        raise ValueError(f"{exc}, or pixels") from None
    if name is None:
        raise ValueError("no unit is given: give a length unit, or pixels")
    return name


def _size(value: object) -> float:
    """Return a stated size as a positive finite float, refusing anything else."""
    if isinstance(value, bool) or (
        isinstance(value, str) and not _DECIMAL.fullmatch(value.strip())
    ):
        raise ValueError(f"the size {value!r} is not a number")
    try:
        size = float(value.strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise ValueError(f"the size {value!r} is not a number") from None
    except OverflowError:  # an int beyond the float range
        raise ValueError(f"the size {value!r} is not a positive finite number") from None
    if not math.isfinite(size) or size <= 0:
        raise ValueError(f"the size {value!r} is not a positive finite number")
    return size


@dataclass(frozen=True)
class Calibration:
    """The statements of ``calibration.csv``: at most one per target and axis.

    Attributes
    ----------
    entries
        The statements, sorted by target and axis.
    """

    entries: tuple[CalibrationEntry, ...] = ()

    def __post_init__(self) -> None:
        keys = [(entry.target, entry.axis) for entry in self.entries]
        if len(set(keys)) != len(keys):
            twice = sorted({key for key in keys if keys.count(key) > 1})
            raise ValueError(f"more than one statement for {', '.join(map(str, twice))}")
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=_order)))

    def stating(self, entries: Iterable[CalibrationEntry]) -> Calibration:
        """Return the calibration with ``entries`` added, replacing the same target and axis."""
        new = list(entries)
        replaced = {(entry.target, entry.axis) for entry in new}
        kept = [entry for entry in self.entries if (entry.target, entry.axis) not in replaced]
        return Calibration((*kept, *new))

    def without(self, target: str, axes: Iterable[str]) -> Calibration:
        """Return the calibration without the statements for ``target`` along ``axes``."""
        target, axes = _target(target), set(axes)
        kept = [e for e in self.entries if not (e.target == target and e.axis in axes)]
        return Calibration(tuple(kept))

    def statement(self, path: str, axis: str) -> CalibrationEntry | None:
        """Return the most specific statement for an image along ``axis``, or None."""
        found = [e for e in self.entries if e.axis == axis and e.covers(path)]
        return max(found, key=_specificity, default=None)


def _order(entry: CalibrationEntry) -> tuple:
    """Sort statements by target (the whole experiment first), then axis."""
    return (entry.target != EXPERIMENT, entry.target, entry.axis)


def _specificity(entry: CalibrationEntry) -> int:
    """Return how deep a statement's target is: the whole experiment is 0."""
    return 0 if entry.target == EXPERIMENT else entry.target.count("/") + 1


@dataclass(frozen=True)
class CalibrationReview:
    """The size every image is recorded with, and what was found about the calibration.

    Attributes
    ----------
    sizes
        ``{relative path: sizes by role}`` (:data:`.recorded_sizes.SIZE_ROLES`), read-only.
    calibration
        The statements read, or None without ``calibration.csv`` (or when it is refused).
    sha256
        The SHA-256 of the file's bytes as read, or None without one.
    issues
        ``calibration_unreadable``, ``calibration_target_not_found`` and
        ``calibration_differs_from_header``; after :func:`fit_to_roles`, also
        ``calibration_axis_absent``.
    """

    sizes: Mapping[str, tuple[RecordedSize, ...]] = field(default_factory=dict)
    calibration: Calibration | None = None
    sha256: str | None = None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sizes", MappingProxyType(dict(self.sizes)))


def review_calibration(
    files: Sequence[TiffMetadata],
    images: Iterable[str],
    calibration: Calibration | None,
    *,
    sha256: str | None = None,
    refusal: ValidationIssue | None = None,
) -> CalibrationReview:
    """Resolve every image's sizes from its header and the statements, and check them.

    Parameters
    ----------
    files
        The analysed images' headers.
    images
        Every image of the experiment, excluded ones included (a statement for an excluded
        image is not unknown).
    calibration
        The statements of ``calibration.csv``, or None without one.
    sha256
        The SHA-256 of ``calibration.csv`` as read.
    refusal
        The error refusing ``calibration.csv``, when it cannot be read; the images then
        keep their headers' sizes and the run stops on the error.

    Returns
    -------
    CalibrationReview
    """
    statements = calibration or Calibration()
    sizes = {f.relative_path: resolve_sizes(f, statements) for f in files}
    issues = [] if refusal is None else [refusal]
    issues.extend(_unknown_targets(statements, list(images)))
    issues.extend(_header_conflicts(files, statements))
    return CalibrationReview(sizes, calibration, sha256, tuple(issues))


def resolve_sizes(meta: TiffMetadata, calibration: Calibration) -> tuple[RecordedSize, ...]:
    """Return one image's sizes by role: stated where a statement covers it, else its header's.

    Parameters
    ----------
    meta
        The image's header.
    calibration
        The statements.

    Returns
    -------
    tuple of RecordedSize
        One per role of :data:`.recorded_sizes.SIZE_ROLES`; the time step is the header's.
    """
    header = {size.role: size for size in header_sizes(meta.physical_sizes)}
    resolved = []
    for role in SIZE_ROLES:
        entry = calibration.statement(meta.relative_path, role) if role != "T" else None
        resolved.append(header[role] if entry is None else entry.recorded())
    return tuple(resolved)


def fit_to_roles(
    review: CalibrationReview, roles: Mapping[str, Collection[str | None]]
) -> tuple[CalibrationReview, tuple[ValidationIssue, ...]]:
    """Keep a Z or T size only for the images that have that role.

    The header of a max projection still gives the z-step of its stack, and a statement for
    the whole experiment covers every image: neither is a size of an axis the image does not
    have. Such an axis is recorded with no value and no source; a statement for it is the
    warning ``calibration_axis_absent`` naming the images (the statement still sizes the
    images that have the axis).

    Parameters
    ----------
    review
        The calibration resolved from the headers and ``calibration.csv``.
    roles
        ``{relative path: the roles the image is read with}`` (its axes' roles, without the
        singleton roles of axes it lacks); images left out keep their sizes.

    Returns
    -------
    tuple
        The review with those sizes removed and the warning among its issues, and the new
        issues alone (the warning, or nothing).
    """
    statements = review.calibration or Calibration()
    sizes, absent = dict(review.sizes), {}
    for path, found in review.sizes.items():
        if path not in roles:
            continue
        lacking = [role for role in ("Z", "T") if role not in roles[path]]
        sizes[path] = tuple(RecordedSize(s.role) if s.role in lacking else s for s in found)
        for axis in lacking:
            if axis in CALIBRATION_AXES and statements.statement(path, axis) is not None:
                absent.setdefault(path, []).append(axis)
    issues = _absent_axis_issues(absent)
    fitted = replace(review, sizes=sizes, issues=(*review.issues, *issues))
    return fitted, issues


def _absent_axis_issues(absent: Mapping[str, list[str]]) -> tuple[ValidationIssue, ...]:
    """Warn about statements for an axis the images they cover do not have."""
    if not absent:
        return ()
    paths = sorted(absent)
    shown = [f"{path} (no {' or '.join(absent[path])})" for path in paths[:_SHOWN]]
    more = len(paths) - len(shown)
    axes = " and ".join(sorted({axis for found in absent.values() for axis in found}))
    return (
        warning(
            "calibration_axis_absent",
            f"calibration.csv states a {axes} size for {plural(len(paths), 'image')} that "
            "have no such axis: "
            + "; ".join(shown)
            + (f"; and {more} more" if more else "")
            + ". No size is recorded for an axis an image does not have.",
            paths,
            "No action needed if the statement is meant for other images (for example a "
            "statement for the whole experiment). Otherwise give it a narrower target, or "
            "remove it.",
        ),
    )


def _unknown_targets(calibration: Calibration, images: list[str]) -> list[ValidationIssue]:
    """Refuse statements whose target is neither a folder nor an image of the experiment."""
    unknown = sorted(
        {
            entry.target
            for entry in calibration.entries
            if entry.target != EXPERIMENT and not any(entry.covers(path) for path in images)
        }
    )
    if not unknown:
        return []
    return [
        error(
            "calibration_target_not_found",
            f"calibration.csv states pixel sizes for {plural(len(unknown), 'target')} that "
            "are neither a folder nor an image of the experiment: "
            + ", ".join(repr(target) for target in unknown)
            + ". Nothing is guessed, so the run stops.",
            unknown,
            "Correct the target (relative to the experiment folder, with / between folders, "
            "spelled exactly as on disk, or . for the whole experiment), or remove the line.",
        )
    ]


def _header_conflicts(
    files: Sequence[TiffMetadata], calibration: Calibration
) -> list[ValidationIssue]:
    """Warn about statements that differ from the header; the statement is used."""
    details: dict[str, list[str]] = {}
    for meta in files:
        header = {size.role: size for size in header_sizes(meta.physical_sizes)}
        for axis in CALIBRATION_AXES:
            entry = calibration.statement(meta.relative_path, axis)
            if entry is not None and _differs(entry, header[axis], meta.length("Z")):
                details.setdefault(meta.relative_path, []).append(
                    f"{axis} stated {entry.describe()}, header {header[axis].value:g} "
                    f"{header[axis].unit or 'without a unit'}"
                )
    if not details:
        return []
    shown = [f"{path} ({'; '.join(found)})" for path, found in list(details.items())[:_SHOWN]]
    more = len(details) - len(shown)
    return [
        warning(
            "calibration_differs_from_header",
            f"calibration.csv states pixel sizes that differ from the image metadata of "
            f"{plural(len(details), 'image')}: "
            + "; ".join(shown)
            + (f"; and {more} more" if more else "")
            + ". The stated sizes are used.",
            list(details),
            "No action needed if the image metadata are wrong. Otherwise correct or remove "
            "the statements in calibration.csv.",
        )
    ]


def _differs(entry: CalibrationEntry, header: RecordedSize, z_length: int) -> bool:
    """Return True when a statement differs from a header size it replaces.

    A header without a size, or with a size in a unit that cannot be compared, is not
    contradicted; a z-step is compared only for images with more than one depth slice.
    """
    if header.value is None or (entry.axis == "Z" and z_length <= 1):
        return False
    if entry.in_pixels:
        return True
    stated = to_micrometers(entry.size, entry.unit)
    known = header.micrometers()
    if known is None:
        return False
    return not same_pixel_size([stated], [known], roles=(entry.axis,))
