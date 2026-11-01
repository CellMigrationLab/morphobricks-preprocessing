"""Axis review: which dimension of each image is X, Y, Z, C or T.

The axis labels ``tifffile`` reports from a file's metadata prefill an
:class:`AxisMapping`, which the user edits and confirms once for the whole
experiment. :func:`review_axes` checks the confirmed mapping against the rules of
``docs/contract.md`` §2 and resolves it for every file, including files that lack
a dimension the others have (the ``missing_axes`` case of
:mod:`~morphobricks_preprocessing.metadata`). It also checks every TIFF's confirmed roles
against the axes its header names (:func:`header_axis_issue`): confirmed roles that swap or
contradict the named axes are an error, and such an image is never read (the user corrects
the roles, or saves named-axis copies); an image whose metadata does not name its axes (a
plain stack) is read with the confirmed roles, with a warning, and recorded as confirmed.

Nothing here reads pixels; the preview plane is read by
:func:`~morphobricks_preprocessing.planes.read_sample_plane`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .axis_check import (
    AXES_FROM_CONFIRMED,
    AXIS_CHECK_CODES,
    AXIS_ERROR_CODES,
)
from .metadata import (
    MetadataReport,
    TiffMetadata,
)
from .validation import ValidationIssue, has_errors

ROLES = ("X", "Y", "Z", "C", "T")
"""Supported axis roles."""

PLANE_ROLES = ("Z", "C", "T")
"""Roles that index planes; X and Y span each plane."""


_AXIS_NAMES = {
    "X": "width",
    "Y": "height",
    "Z": "depth",
    "S": "sample",
    "I": "sequence",
    "T": "time",
    "C": "channel",
    "A": "angle",
    "P": "phase",
    "R": "tile",
    "H": "lifetime",
    "E": "lambda",
    "L": "exposure",
    "V": "event",
    "M": "mosaic",
    "Q": "other",
    "J": "column",
    "K": "row",
}

# Metadata issues that the axis review checks again against the confirmed mapping.
_SUPERSEDED_METADATA_CODES = frozenset(
    {"missing_axes", "inconsistent_dimensions", "different_channel_counts"}
)


@dataclass(frozen=True)
class AxisMapping:
    """The role of every dimension of one arrangement of axes.

    Attributes
    ----------
    axes
        One ``tifffile`` axis label per dimension, e.g. ``"TZCYX"``.
    roles
        One entry per dimension: ``"X"``, ``"Y"``, ``"Z"``, ``"C"``, ``"T"``, or
        None for a dimension without a role (allowed only for length-1
        dimensions). A string such as ``"TZCYX"`` is accepted, with ``"-"`` for
        no role.
    singleton_roles
        Roles of the experiment mapping that this file lacks. They are read as
        length-1 dimensions: their coordinate is 0. Empty for the experiment
        mapping itself; set by :meth:`for_axes`.

    Raises
    ------
    ValueError
        If ``roles`` does not have one entry per axis or contains an unknown role.
    """

    axes: str
    roles: tuple[str | None, ...]
    singleton_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        roles = tuple(None if r in (None, "-", "") else str(r).upper() for r in self.roles)
        if len(roles) != len(self.axes):
            raise ValueError(
                f"The mapping has {len(roles)} roles for {len(self.axes)} axes ({self.axes!r})."
            )
        unknown = [r for r in (*roles, *self.singleton_roles) if r is not None and r not in ROLES]
        if unknown:
            raise ValueError(f"Unknown axis roles {unknown}; expected one of {ROLES}.")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "singleton_roles", tuple(self.singleton_roles))

    @property
    def ndim(self) -> int:
        """Number of dimensions."""
        return len(self.axes)

    @property
    def plane_roles(self) -> tuple[str, ...]:
        """The Z, C and T roles that give plane coordinates, in ``PLANE_ROLES`` order."""
        present = set(self.roles) | set(self.singleton_roles)
        return tuple(r for r in PLANE_ROLES if r in present)

    def index_of(self, role: str) -> int | None:
        """Return the dimension that has ``role``, or None if no dimension has it.

        Raises
        ------
        ValueError
            If more than one dimension has the role.
        """
        found = [i for i, r in enumerate(self.roles) if r == role]
        if len(found) > 1:
            raise ValueError(f"Role {role} is assigned to more than one axis of {self.axes!r}.")
        return found[0] if found else None

    def with_role(self, index: int, role: str | None) -> AxisMapping:
        """Return a copy with dimension ``index`` given ``role`` (None for no role)."""
        roles = list(self.roles)
        roles[index] = role
        return AxisMapping(self.axes, tuple(roles), self.singleton_roles)

    def for_axes(self, axes: str) -> AxisMapping | None:
        """Apply this mapping to a file whose axes are ``axes``.

        A file with the same axes gets this mapping. A file lacking some of the
        dimensions gets the roles of the dimensions it has; the roles of the
        missing ones become :attr:`singleton_roles`.

        Returns
        -------
        AxisMapping or None
            None if ``axes`` does not fit: it is not the mapping's axes with some
            dimensions left out, it fits in more than one way (so it is not clear
            which dimension is missing), or it lacks the X or Y dimension.
        """
        if axes == self.axes:
            return self
        matches = _embeddings(axes, self.axes)
        if len(matches) != 1:
            return None
        (kept,) = matches
        dropped = [self.roles[i] for i in range(self.ndim) if i not in kept]
        if "X" in dropped or "Y" in dropped:
            return None
        return AxisMapping(
            axes,
            tuple(self.roles[i] for i in kept),
            tuple(r for r in dropped if r is not None),
        )

    @property
    def roles_string(self) -> str:
        """The roles as text, ``"-"`` for no role, e.g. ``"T-YX"``.

        ``AxisMapping(axes, mapping.roles_string)`` gives the mapping back.
        """
        return "".join(role or "-" for role in self.roles)

    def describe(self) -> str:
        """Plain-language summary, e.g. ``"T (time) -> Z, C (channel) -> C, ..."``."""
        parts = []
        for axis, role in zip(self.axes, self.roles, strict=True):
            name = _AXIS_NAMES.get(axis)
            label = f"{axis} ({name})" if name else axis
            parts.append(f"{label} -> {role or 'no role'}")
        return ", ".join(parts)


@dataclass(frozen=True)
class AxisReview:
    """A confirmed experiment mapping, resolved for every file.

    Attributes
    ----------
    mapping
        The experiment mapping the user confirmed.
    file_mappings
        Mapping for each readable file that fits, keyed by relative path. Files
        lacking dimensions have :attr:`AxisMapping.singleton_roles`.
    issues
        Errors about the mapping and about files it does not fit, and the axis check's
        ``axes_not_named`` warning.
    axes_from
        For each file of ``file_mappings``, where its roles come from: ``"header"`` when its
        metadata names every axis longer than 1 (and they agree), ``"confirmed"`` when
        some axis is named only by the user's confirmation (a plain stack).
    """

    mapping: AxisMapping
    file_mappings: Mapping[str, AxisMapping] = field(default_factory=dict)
    issues: tuple[ValidationIssue, ...] = ()
    axes_from: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "file_mappings", MappingProxyType(dict(self.file_mappings)))
        object.__setattr__(self, "axes_from", MappingProxyType(dict(self.axes_from)))

    @property
    def ok(self) -> bool:
        """True if no issue blocks processing."""
        return not has_errors(self.issues)

    @property
    def axis_errors(self) -> tuple[ValidationIssue, ...]:
        """The ``axes_yx_swapped`` and ``axes_contradict_header`` errors."""
        return tuple(i for i in self.issues if i.code in AXIS_ERROR_CODES)

    @property
    def axis_check(self) -> tuple[ValidationIssue, ...]:
        """Every issue of the axis check, the ``axes_not_named`` warning included."""
        return tuple(i for i in self.issues if i.code in AXIS_CHECK_CODES)

    @property
    def confirmed_files(self) -> tuple[str, ...]:
        """Images whose roles are named only by the user's confirmation, sorted."""
        return tuple(sorted(p for p, how in self.axes_from.items() if how == AXES_FROM_CONFIRMED))

    @property
    def axis_error_files(self) -> tuple[str, ...]:
        """Images with one of the three axis errors (:data:`AXIS_ERROR_CODES`), sorted."""
        return tuple(sorted({path for issue in self.axis_errors for path in issue.paths}))

    @property
    def only_axis_errors(self) -> bool:
        """True if there are axis errors and no other error.

        The confirmed mapping then fits every file, so the images can be saved again as
        named-axis copies with it.
        """
        errors = [i for i in self.issues if i.is_error]
        return bool(errors) and all(i.code in AXIS_ERROR_CODES for i in errors)

    def combined_issues(self, metadata: MetadataReport) -> tuple[ValidationIssue, ...]:
        """Merge the metadata issues with this review's, errors first.

        The review checks the arrangement of dimensions and the numbers of
        channels again against the confirmed mapping, so the metadata's
        ``missing_axes``, ``inconsistent_dimensions`` and
        ``different_channel_counts`` issues are replaced by the review's own.
        """
        kept = [i for i in metadata.issues if i.code not in _SUPERSEDED_METADATA_CODES]
        merged = [*kept, *self.issues]
        return tuple(sorted(merged, key=lambda issue: not issue.is_error))


def prefill_mapping(axes: str) -> AxisMapping:
    """Suggest roles from ``tifffile`` axis labels, for the user to confirm.

    ``X``, ``Y``, ``Z``, ``C`` and ``T`` keep their role. The RGB samples axis
    ``S`` becomes C when there is no other ``C``. Every other label (a generic
    sequence ``I``, ``Q`` for unknown, ...) is left without a role: it is never
    guessed whether such a dimension is depth or time.
    """
    roles: list[str | None] = []
    for axis in axes:
        if axis in ROLES and axes.count(axis) == 1:
            roles.append(axis)
        elif axis == "S" and "C" not in axes and axes.count("S") == 1:
            roles.append("C")
        else:
            roles.append(None)
    return AxisMapping(axes, tuple(roles))


def reference_axes(files: Iterable[TiffMetadata]) -> str | None:
    """Return the arrangement the experiment mapping is defined on.

    The TIFF files decide it when there are any, because their axes need the
    user's confirmation: it is the TIFF arrangement with the most dimensions,
    provided every other TIFF's arrangement is it with some dimensions left out.
    OME-Zarr images declare their axes and are matched by role
    (:func:`file_mapping_for`). In an experiment of OME-Zarr images only, it is
    their arrangement with the most dimensions (then the most common one).
    Returns None if no image is readable or the TIFF arrangements conflict.
    """
    readable = [f for f in files if f.readable]
    tiffs = [f for f in readable if not f.is_ome_zarr]
    if not tiffs:
        counts = Counter(f.axes for f in readable)
        if not counts:
            return None
        return max(counts, key=lambda axes: (len(axes), counts[axes], axes))
    arrangements = {f.axes for f in tiffs}
    longest = max(arrangements, key=lambda axes: (len(axes), axes))
    if sum(len(axes) == len(longest) for axes in arrangements) > 1:
        return None
    if all(_embeddings(axes, longest) for axes in arrangements):
        return longest
    return None


def prefill_experiment_mapping(metadata: MetadataReport) -> AxisMapping | None:
    """Suggest the experiment mapping from the files' axis labels.

    Returns None when the files' arrangements conflict, so no single mapping
    can describe them (see :func:`reference_axes`).
    """
    axes = reference_axes(metadata.files)
    return None if axes is None else prefill_mapping(axes)


def file_mapping_for(
    f: TiffMetadata, mapping: AxisMapping, *, missing_as_singleton: bool = True
) -> AxisMapping | None:
    """Resolve the experiment mapping for one image, or None if it does not fit.

    An image with the mapping's arrangement gets the mapping itself. A TIFF with
    some of its dimensions left out gets :meth:`AxisMapping.for_axes`. An OME-Zarr
    image with another arrangement is matched by the roles its metadata declares
    (see :func:`declared_mapping`). Experiment roles an image lacks are read as
    length 1 only if ``missing_as_singleton``.
    """
    if f.axes == mapping.axes:
        return mapping
    if f.is_ome_zarr:
        resolved, problem = declared_mapping(f, mapping)
    else:
        resolved = mapping.for_axes(f.axes)
        problem = "" if resolved is not None else "mismatch"
    if resolved is None or problem == "mismatch":
        return None
    if resolved.singleton_roles and not missing_as_singleton:
        return None
    return resolved


def declared_mapping(f: TiffMetadata, mapping: AxisMapping) -> tuple[AxisMapping | None, str]:
    """Match an OME-Zarr image to the experiment mapping by the roles it declares.

    OME-Zarr metadata says which axis is time, channel and space, so these roles
    are taken as stated rather than by position. A declared Z, C or T axis the
    experiment does not have is dropped if it has length 1. Experiment roles the
    image lacks become :attr:`AxisMapping.singleton_roles`.

    Returns
    -------
    tuple
        ``(mapping, "")`` when it fits, ``(mapping, "missing")`` when it fits only
        with experiment roles read as length 1, and ``(None, "mismatch")`` when
        an axis with more than one plane has no matching role or X/Y is missing.
    """
    roles: list[str | None] = [axis if axis in ROLES else None for axis in f.axes]
    experiment = {r for r in mapping.roles if r in PLANE_ROLES}
    for i, (role, length) in enumerate(zip(roles, f.shape, strict=True)):
        if role in PLANE_ROLES and role not in experiment:
            if length > 1:
                return None, "mismatch"
            roles[i] = None
        elif role is None and length > 1:
            return None, "mismatch"
    if roles.count("X") != 1 or roles.count("Y") != 1:
        return None, "mismatch"
    missing = tuple(r for r in PLANE_ROLES if r in experiment and r not in roles)
    return AxisMapping(f.axes, tuple(roles), missing), "missing" if missing else ""


def axis_length_ranges(
    mapping: AxisMapping, files: Iterable[TiffMetadata]
) -> list[tuple[int, int]]:
    """Smallest and largest length of each mapping axis across the files.

    Files lacking an axis count as length 1 along it; files that do not fit the
    mapping are left out. Returns ``(0, 0)`` for every axis if no file fits.
    """
    sizes = [
        _sizes(f, mapping)
        for f in files
        if f.readable and (f.axes == mapping.axes or len(_embeddings(f.axes, mapping.axes)) == 1)
    ]
    if not sizes:
        return [(0, 0)] * mapping.ndim
    return [
        (min(s.get(d, 1) for s in sizes), max(s.get(d, 1) for s in sizes))
        for d in range(mapping.ndim)
    ]


def axis_name(axis: str) -> str:
    """Plain-language label of a ``tifffile`` axis code, e.g. ``"Z (depth)"``."""
    return _axis_label(axis)


def _embeddings(short: str, long: str) -> list[tuple[int, ...]]:
    """Every way ``short`` is ``long`` with some characters left out (as kept indices)."""
    results: list[tuple[int, ...]] = []

    def walk(i: int, start: int, kept: tuple[int, ...]) -> None:
        if len(results) > 1:  # two ways already means ambiguous; stop searching
            return
        if i == len(short):
            results.append(kept)
            return
        for j in range(start, len(long) - (len(short) - i) + 1):
            if long[j] == short[i]:
                walk(i + 1, j + 1, (*kept, j))

    walk(0, 0, ())
    return results


def _sizes(f: TiffMetadata, mapping: AxisMapping) -> dict[int, int] | None:
    """Length of each mapping dimension in ``f``; None if ``f`` does not fit unambiguously.

    Dimensions the file lacks are left out (they count as length 1).
    """
    if f.axes == mapping.axes:
        return dict(enumerate(f.shape))
    matches = _embeddings(f.axes, mapping.axes)
    if len(matches) != 1:
        return None
    return dict(zip(matches[0], f.shape, strict=True))


def _axis_label(axis: str) -> str:
    name = _AXIS_NAMES.get(axis)
    return f"{axis} ({name})" if name else axis
