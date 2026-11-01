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

import warnings
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .axis_check import (
    AXES_FROM_CONFIRMED,
    AXIS_CHECK_CODES,
    AXIS_ERROR_CODES,
    POSITION_AXES,
    check_header_axes,
)
from .metadata import (
    OPEN_AXIS,
    MetadataReport,
    TiffMetadata,
    channel_count_issues,
    named_axes,
)
from .validation import ValidationIssue, error, has_errors, plural

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


def check_declared_axes(meta: TiffMetadata, declared: str) -> ValidationIssue | None:
    """Check the axes a brick or user declares for an image against its header.

    .. deprecated:: 0.4.0
        Bricks no longer take axis declarations: they read the files of a preprocessing
        run whose axes were checked (:func:`review_axes`, contract §2). This function will
        be removed in v0.5.0.

    One rule for every brick that is told an image's (or mask's) axes, for example
    ``image_axes="CYX"`` (MorphoBricks D86): a declaration can name what the header leaves
    open, never contradict it. Only what the metadata names binds
    (:func:`.metadata.named_axes`, the same rule as :func:`.metadata.header_axes`).
    Position by position:

    - an axis the metadata names (X, Y, Z, C, T) must be declared with that letter, except
      that the named Y and X may be declared swapped (header ``CYX`` declared ``CXY``): the
      image is then read transposed, which :func:`declared_axes_transposed` reports so the
      caller can record it (owner decision, 31 October 2026);
    - a dimension it leaves open may be declared any role: a placeholder axis (``I``,
      ``Q``), and in a plain stack of pages without axis metadata (``IYX``, ``QYX``,
      ``QQYX``) the ``Y`` and ``X`` too, which only say where the pages' rows and columns
      are stored (a (Y, X, C) array written without metadata reads as ``QYX``);
    - the RGB samples axis ``S`` may be declared C when the header has no C (as the axis
      review's prefill does);
    - any other header axis (tile ``R``, angle ``A``, ...) has no role and cannot be
      declared.

    The declaration must have one letter per header axis, each role at most once, and
    declare Y and X when the header has them (also where a plain stack leaves them open).

    Parameters
    ----------
    meta
        The image's header (:func:`.metadata.inspect_image`).
    declared
        The declared axes, one letter per dimension (any case), e.g. ``"ZYX"``.

    Returns
    -------
    ValidationIssue or None
        The error ``declared_axes_mismatch`` naming the image, the declared and the header
        axes (or ``unreadable_tiff`` / ``unreadable_ome_zarr`` when its header could not be
        read); None when the declaration fits the header.
    """
    _deprecated("check_declared_axes")
    return _check_declared_axes(meta, declared)


def _check_declared_axes(meta: TiffMetadata, declared: str) -> ValidationIssue | None:
    if not meta.readable:
        code = "unreadable_ome_zarr" if meta.is_ome_zarr else "unreadable_tiff"
        return error(
            code,
            "The image's header could not be read, so its axes cannot be checked.",
            [meta.relative_path],
            "Export the image again, or leave it out.",
        )
    text = declared.upper() if isinstance(declared, str) else ""
    problem = _declared_axes_problem(meta.axes, named_axes(meta), text)
    if problem is None:
        return None
    return error(
        "declared_axes_mismatch",
        f"The axes declared for {meta.relative_path} ({declared!r}) do not fit the axes in its "
        f"header ({meta.axes!r}): {problem}. Planes would be read along the wrong dimension.",
        [meta.relative_path],
        "Declare the axes the image's metadata gives, or export the image again with the "
        "right axes in its metadata.",
    )


def declared_axes_transposed(meta: TiffMetadata, declared: str) -> bool:
    """Return True when a declaration fits the header only with its named Y and X swapped.

    .. deprecated:: 0.4.0
        Nothing is read transposed any more: a confirmed Y/X swap is the error
        ``axes_yx_swapped`` (:func:`review_axes`). This function will be removed in v0.5.0.

    :func:`check_declared_axes` accepts a declaration that differs from the axes the
    metadata names only by swapping Y and X (header ``CYX`` declared ``CXY``): the image is
    read transposed. A brick that accepts such a declaration records it with this function.
    A plain stack's Y and X are not named (:func:`.metadata.named_axes`), so declaring them
    the other way round is never a swap.

    Parameters
    ----------
    meta
        The image's header (:func:`.metadata.inspect_image`).
    declared
        The declared axes, one letter per dimension (any case).

    Returns
    -------
    bool
        True when the named Y is declared X (and the named X declared Y).

    Raises
    ------
    ValueError
        When the declaration does not fit the header at all (:func:`check_declared_axes`
        gives an error), with that error's message.
    """
    _deprecated("declared_axes_transposed")
    issue = _check_declared_axes(meta, declared)
    if issue is not None:
        raise ValueError(issue.message)
    named = named_axes(meta)
    return "Y" in named and declared.upper()[named.index("Y")] == "X"


def _declared_axes_problem(header: str, named: str, declared: str) -> str | None:
    if not declared or len(declared) != len(header):
        return f"{len(declared)} declared for {len(header)} dimensions"
    unknown = sorted(set(declared) - set(ROLES))
    if unknown:
        return f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} not an axis role"
    repeated = sorted({axis for axis in declared if declared.count(axis) > 1})
    if repeated:
        return f"{', '.join(repeated)} declared more than once"
    # Even where a plain stack leaves its Y and X open, the image has rows and columns:
    # a declaration without them would read no plane at all.
    undeclared = [axis for axis in "YX" if axis in header and axis not in declared]
    if undeclared:
        return f"{' and '.join(undeclared)} {'is' if len(undeclared) == 1 else 'are'} not declared"
    for index, (have, said) in enumerate(zip(named, declared, strict=True)):
        if have in (said, OPEN_AXIS):
            continue
        # The named Y and X swapped: with one letter per role, the other position must then
        # be swapped too, so this accepts a swap of the two and nothing else.
        if have in "YX" and said in "YX":
            continue
        if have == "S" and said == "C" and "C" not in header:
            continue
        return f"dimension {index} is {_axis_label(have)} in the header, declared {said}"
    return None


def _deprecated(name: str) -> None:
    warnings.warn(
        f"morphobricks_preprocessing.{name} is deprecated and will be removed in v0.5.0: "
        "bricks read the files of a preprocessing run whose axes were checked, and take no "
        "axis declarations.",
        DeprecationWarning,
        stacklevel=3,
    )


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


def check_mapping(
    mapping: AxisMapping, files: Iterable[TiffMetadata] = ()
) -> list[ValidationIssue]:
    """Check a mapping against the axis rules of ``docs/contract.md`` §2.

    Parameters
    ----------
    mapping
        The experiment mapping.
    files
        Headers of the files it applies to. A dimension counts as length 1 only
        if it is length 1 in every one of these files that has it; with no files,
        every dimension is assumed to be longer than 1.

    Returns
    -------
    list of ValidationIssue
        Errors: X or Y missing (``missing_xy_axis``), a role on more than one
        dimension (``duplicate_axis_role``), a position-like axis with a role or
        with more than one plane (``unsupported_axis``), and a dimension longer
        than 1 without a role (``unassigned_axis``).
    """
    files = [f for f in files if f.readable]
    paths = [f.relative_path for f in files]
    lengths = _dimension_lengths(mapping, files)
    issues: list[ValidationIssue] = []

    missing = [role for role in ("X", "Y") if role not in mapping.roles]
    if missing:
        issues.append(
            error(
                "missing_xy_axis",
                f"The axis mapping has no {' or '.join(missing)} axis. Every image needs "
                "exactly one X (width) and one Y (height) axis to be split into planes.",
                paths,
                f"Assign the {' and '.join(missing)} role to the image's width and height axes.",
            )
        )

    duplicates = sorted(
        {r for r in mapping.roles if r is not None and mapping.roles.count(r) > 1},
        key=ROLES.index,
    )
    if duplicates:
        issues.append(
            error(
                "duplicate_axis_role",
                f"More than one axis has the role {', '.join(duplicates)}. Each role can "
                f"be given to one axis only ({mapping.describe()}).",
                paths,
                "Give each of these roles to a single axis.",
            )
        )

    positions = [i for i, axis in enumerate(mapping.axes) if axis in POSITION_AXES]
    several = [i for i in positions if lengths[i] > 1]
    if several:
        names = ", ".join(_axis_label(mapping.axes[i]) for i in several)
        issues.append(
            error(
                "unsupported_axis",
                f"The images have a position axis ({names}): they hold images of several "
                "positions or tiles. Images with several positions are not supported yet.",
                _paths_with_long(files, mapping, several),
                "Export each position as a separate image file, then run the check again.",
            )
        )
    with_role = [i for i in positions if i not in several and mapping.roles[i] is not None]
    if with_role:
        names = ", ".join(_axis_label(mapping.axes[i]) for i in with_role)
        issues.append(
            error(
                "unsupported_axis",
                f"The position axis {names} cannot be used as Z, C or T: it identifies "
                "where an image was taken, not a dimension of the image.",
                paths,
                "Remove the role from this axis in the axis review.",
            )
        )

    unassigned = [
        i
        for i, role in enumerate(mapping.roles)
        if role is None and lengths[i] > 1 and mapping.axes[i] not in POSITION_AXES
    ]
    if unassigned:
        names = ", ".join(_axis_label(mapping.axes[i]) for i in unassigned)
        issues.append(
            error(
                "unassigned_axis",
                f"No role is chosen for the axis {names}, which has more than one plane. "
                "The images cannot be split into planes until every such axis is Z "
                "(depth), C (channel) or T (time).",
                _paths_with_long(files, mapping, unassigned),
                "Choose Z, C or T for this axis in the axis review.",
            )
        )
    return issues


def review_axes(
    files: MetadataReport | Iterable[TiffMetadata],
    mapping: AxisMapping,
    *,
    missing_as_singleton: bool = False,
) -> AxisReview:
    """Check a confirmed experiment mapping and resolve it for every file.

    Parameters
    ----------
    files
        The metadata report or the file headers.
    mapping
        The mapping the user confirmed, defined on the experiment's full
        arrangement of axes (see :func:`reference_axes`).
    missing_as_singleton
        The user confirmed that files lacking some of the mapping's dimensions
        simply have one plane along them (for example one channel). Without this
        confirmation such files are an error.

    Returns
    -------
    AxisReview
        Per-file mappings for the files that fit and the issues found: those
        from :func:`check_mapping`, files that lack dimensions without
        confirmation (``missing_axes``) or in an ambiguous way
        (``ambiguous_missing_axes``), files the mapping does not fit
        (``inconsistent_mapping``), files with different numbers of channels
        along the C axis (``different_channel_counts``; 1 without one), and the
        axis check of every TIFF against its header (:func:`header_axis_issue`),
        each naming its files with their header axes and confirmed roles: the
        errors ``axes_yx_swapped`` and ``axes_contradict_header`` (with the likely
        cause; such an image is never read transposed or under a contradicted
        declaration) and the warning ``axes_not_named`` (a plain stack, read with
        the confirmed roles; :attr:`AxisReview.axes_from` records it).
    """
    if isinstance(files, MetadataReport):
        files = files.files
    readable = [f for f in files if f.readable]
    file_mappings: dict[str, AxisMapping] = {}
    unconfirmed: list[str] = []
    ambiguous: list[str] = []
    misfits: list[str] = []
    zarr_misfits: list[str] = []
    by_role: list[str] = []
    for f in readable:
        if f.axes == mapping.axes:
            file_mappings[f.relative_path] = mapping
            continue
        if f.is_ome_zarr:
            by_role.append(f.relative_path)
            resolved, problem = declared_mapping(f, mapping)
            if resolved is None:
                zarr_misfits.append(f.relative_path)
            elif problem == "missing" and not missing_as_singleton:
                unconfirmed.append(f.relative_path)
            else:
                file_mappings[f.relative_path] = resolved
            continue
        resolved = mapping.for_axes(f.axes)
        if resolved is None:
            if len(_embeddings(f.axes, mapping.axes)) > 1:
                ambiguous.append(f.relative_path)
            else:
                misfits.append(f.relative_path)
        elif not missing_as_singleton:
            unconfirmed.append(f.relative_path)
        else:
            file_mappings[f.relative_path] = resolved

    issues = check_mapping(
        mapping,
        [f for f in readable if f.relative_path not in misfits and f.relative_path not in by_role],
    )
    if unconfirmed:
        issues.append(
            error(
                "missing_axes",
                f"{plural(len(unconfirmed), 'image')} lack some of the dimensions of the "
                f"axis mapping ({mapping.axes}). They can be read as having one plane along "
                "each missing dimension, but only once this is confirmed.",
                unconfirmed,
                "If the missing dimensions simply have one plane in these files (for "
                "example a single channel), confirm this in the axis review. Otherwise "
                "export the images again with the same dimensions.",
            )
        )
    if ambiguous:
        issues.append(
            error(
                "ambiguous_missing_axes",
                f"{plural(len(ambiguous), 'image')} lack some of the dimensions of the "
                f"axis mapping ({mapping.axes}), and it is not clear which ones are "
                "missing.",
                ambiguous,
                "Export the images again with the same dimensions as the others, or "
                "process them as a separate experiment.",
            )
        )
    if zarr_misfits:
        issues.append(
            error(
                "ome_zarr_axes_mismatch",
                f"The axes declared in {plural(len(zarr_misfits), 'OME-Zarr image')} do not "
                f"match the axis roles of the experiment ({mapping.describe()}): they have an "
                "axis with several planes that the experiment has no role for, or no X or Y "
                "axis.",
                zarr_misfits,
                "Check the axis roles in the axis review, or process these images as a "
                "separate experiment.",
            )
        )
    if misfits:
        issues.append(
            error(
                "inconsistent_mapping",
                f"The axis mapping ({mapping.axes}) does not fit "
                f"{plural(len(misfits), 'image')}: their dimensions are arranged "
                "differently.",
                misfits,
                "Export the images again with the same dimension order, or process the "
                "differently arranged images as a separate experiment.",
            )
        )
    issues.extend(
        channel_count_issues(
            {
                f.relative_path: channel_count(f, file_mappings[f.relative_path])
                for f in readable
                if f.relative_path in file_mappings
            }
        )
    )
    # Each file's confirmed roles against the axes its own header names. Files the mapping
    # does not fit already have their error; OME-Zarr images take their roles from their
    # metadata.
    checked = [
        (f, file_mappings.get(f.relative_path) or mapping.for_axes(f.axes))
        for f in readable
        if f.relative_path in file_mappings or f.relative_path in unconfirmed
    ]
    axis_issues, axes_from = check_header_axes(
        [(f, m) for f, m in checked if m is not None], analysed=file_mappings
    )
    issues.extend(axis_issues)
    return AxisReview(
        mapping=mapping, file_mappings=file_mappings, issues=tuple(issues), axes_from=axes_from
    )


def channel_count(f: TiffMetadata, mapping: AxisMapping) -> int:
    """Return the number of channels of ``f`` under its own mapping; 1 without a C axis.

    ``mapping`` is the file's own mapping (:attr:`AxisReview.file_mappings`).
    """
    c = mapping.index_of("C")
    return 1 if c is None else f.shape[c]


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


def _dimension_lengths(mapping: AxisMapping, files: list[TiffMetadata]) -> list[int]:
    """Largest length of each mapping dimension across the files (2 if unknown)."""
    if not files:
        return [2] * mapping.ndim
    lengths = [1] * mapping.ndim
    for f in files:
        for dim, n in (_sizes(f, mapping) or {}).items():
            lengths[dim] = max(lengths[dim], n)
    return lengths


def _paths_with_long(files: list[TiffMetadata], mapping: AxisMapping, dims: list[int]) -> list[str]:
    """Paths of the files in which any of ``dims`` is longer than 1."""
    paths = []
    for f in files:
        sizes = _sizes(f, mapping) or {}
        if any(sizes.get(d, 1) > 1 for d in dims):
            paths.append(f.relative_path)
    return paths


def _axis_label(axis: str) -> str:
    name = _AXIS_NAMES.get(axis)
    return f"{axis} ({name})" if name else axis
