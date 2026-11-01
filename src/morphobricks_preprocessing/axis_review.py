"""Check a confirmed axis mapping and resolve it for every file (contract §2).

:func:`review_axes` applies the experiment mapping to each image, checks the mapping's rules
(:func:`check_mapping`) and runs the axis check (:mod:`.axis_check`). Moved from ``axes.py``
(PP-4).
"""

from __future__ import annotations

from collections.abc import Iterable

from .axes import (
    ROLES,
    AxisMapping,
    AxisReview,
    _axis_label,
    _embeddings,
    _sizes,
    declared_mapping,
)
from .axis_check import (
    POSITION_AXES,
    ConfirmedAxes,
    check_header_axes,
    confirmation_of,
)
from .metadata import (
    MetadataReport,
    TiffMetadata,
    channel_count_issues,
)
from .validation import ValidationIssue, error, plural


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
    mapping: AxisMapping | ConfirmedAxes,
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
        arrangement of axes (see :func:`reference_axes`). A plain mapping is a
        confirmation for every file given; a :class:`.axis_check.ConfirmedAxes`
        covers only the files it names, and a plain stack outside them is the
        error ``plain_stack_not_confirmed`` (design v0.4 §3, audit 2 B-1).
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
    mapping, confirmed = confirmation_of(mapping)
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
        [(f, m) for f, m in checked if m is not None], file_mappings, confirmed
    )
    return AxisReview(mapping, file_mappings, (*issues, *axis_issues), axes_from)


def channel_count(f: TiffMetadata, mapping: AxisMapping) -> int:
    """Return the number of channels of ``f`` under its own mapping; 1 without a C axis.

    ``mapping`` is the file's own mapping (:attr:`AxisReview.file_mappings`).
    """
    c = mapping.index_of("C")
    return 1 if c is None else f.shape[c]


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
