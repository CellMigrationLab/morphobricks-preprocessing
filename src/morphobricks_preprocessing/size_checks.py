"""Image sizes and plane counts across the experiment (contract §11).

The warnings ``different_sizes`` and ``different_plane_counts`` compare, for every image,
its height and width and its numbers of depth slices, time points and planes along any
other dimension. :mod:`.metadata` measures each image by the letters its header names;
after the axis review, :meth:`.axes.AxisReview.combined_issues` measures each image by its
confirmed roles instead (issue #74): the header letters of a plain stack are only where its
pages, rows and columns are stored, so they are never used for these warnings.

The error ``z_stack_not_supported`` (:func:`z_stack_issues`, owner decision of 5 November
2026) refuses every image with more than one depth slice, measured the same two ways:
MorphoBricks v1 analyses 2-D planes, and a projection is an explicit, recorded step
(``convert --project``), never made implicitly when an image is read.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence

from .validation import ValidationIssue, agree, error, plural, warning

_PLANE_NAMES = {"Z": "depth slices (Z)", "T": "time points"}
"""What the planes along each dimension are called in messages."""

NOT_PLANE_AXES = frozenset("XYSC")
"""Dimensions that do not hold planes: the image itself, and channels (or RGB samples),
which are compared by ``different_channel_counts``."""

CONVERT_KEEPS_ORDER = (
    "Export the images again with the same dimension order, or process the differently "
    "arranged images as a separate experiment. Named-axis copies ('convert') keep each "
    "image's stored order, so they do not solve this."
)
"""What to do about images arranged differently (cross-brick test F8, plan question 73)."""


def lettered_plane_counts(axes: str, shape: Sequence[int]) -> dict[str, int]:
    """Return the planes along each header letter except X, Y, S and C; Z and T default to 1."""
    counts = {"Z": 1, "T": 1}
    counts.update(
        {axis: n for axis, n in zip(axes, shape, strict=True) if axis not in NOT_PLANE_AXES}
    )
    return counts


Z_STACK_CODE = "z_stack_not_supported"
"""The error of an image with more than one depth slice (owner decision of 5 November 2026)."""


def z_stack_issues(slices: Mapping[str, int]) -> list[ValidationIssue]:
    """Refuse every image with more than one depth slice (Z): v1 analyses 2-D planes.

    A Z axis of one slice, or none, is accepted as it is. Nothing is projected or squeezed:
    the user makes recorded 2-D projected copies first with ``convert --project`` (owner
    decisions of 5 and 7 November 2026; contract §2, §17).

    Parameters
    ----------
    slices
        The depth slices of each image, by relative path (its header's named Z, or its
        confirmed Z role; 1 without one).

    Returns
    -------
    list of ValidationIssue
        The error :data:`Z_STACK_CODE` naming every image with more than one slice, with
        its number of slices, or nothing.
    """
    stacks = sorted((path, n) for path, n in slices.items() if n > 1)
    if not stacks:
        return []
    listed = "; ".join(f"{path}: {n} slices" for path, n in stacks[:5])
    more = f"; and {len(stacks) - 5} more" if len(stacks) > 5 else ""
    return [
        error(
            Z_STACK_CODE,
            f"{plural(len(stacks), 'image')} {agree(stacks, 'is a Z-stack', 'are Z-stacks')} "
            f"(more than one depth slice, Z): {listed}{more}. MorphoBricks v1 analyses 2-D "
            "planes, so Z-stacks are not analysed.",
            [path for path, _ in stacks],
            "Make 2-D projected copies first: convert EXPERIMENT --to FOLDER --axes ROLES "
            "--project max|mean|sum (the originals are only read), then run on the copies; "
            "or exclude these images.",
        )
    ]


def channel_count_issues(counts: Mapping[str, int]) -> list[ValidationIssue]:
    """Report images of one imaging unit with different numbers of channels.

    From Stage 3 (Spec roadmap S3.3, owner decision of 11 November 2026) the units of one
    experiment may have different channels: each unit lists its own by canonical ID and the
    table names every channel by ID (``plane_histograms.csv`` schema version 5). Inside one
    unit (a leaf folder: condition, biological repeat and technical repeat) every image has the
    same channels, so a different count there is still refused; the run names it with the
    Spec's ``R_UNIT_NOT_HOMOGENEOUS``. Images of different folders are never compared here.

    Parameters
    ----------
    counts
        Number of channels of each image, by relative path.

    Returns
    -------
    list of ValidationIssue
        One error ``different_channel_counts`` per folder whose images differ, naming the images
        not having that folder's most common number, or nothing.
    """
    folders: dict[str, dict[str, int]] = {}
    for path, count in sorted(counts.items()):
        folders.setdefault(path.rpartition("/")[0] or ".", {})[path] = count
    return [
        found for folder in sorted(folders) for found in _folder_counts(folder, folders[folder])
    ]


def _folder_counts(folder: str, counts: Mapping[str, int]) -> list[ValidationIssue]:
    """Return ``different_channel_counts`` for one folder's images, or nothing."""
    groups: dict[int, list[str]] = {}
    for path, count in sorted(counts.items()):
        groups.setdefault(count, []).append(path)
    if len(groups) < 2:
        return []
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    summary = ", ".join(
        f"{plural(count, 'channel')} ({plural(len(paths), 'image')})" for count, paths in ordered
    )
    return [
        error(
            "different_channel_counts",
            f"The images of the folder {folder} have different numbers of channels: {summary}. "
            "The images of one acquisition must all have the same channels (other folders may "
            "have other channels). The images listed are those without the folder's most "
            "common number of channels.",
            sorted(path for _, paths in ordered[1:] for path in paths),
            "Move the images with other channels into a folder of their own (an acquisition "
            "of its own), or exclude them. If a channel axis was assigned the wrong role, "
            "correct the axis roles.",
        )
    ]


def role_size(shape: Sequence[int], roles: Sequence[str | None]) -> tuple[int, int]:
    """Return the height and width of an image under its confirmed roles (one per axis)."""
    return shape[list(roles).index("Y")], shape[list(roles).index("X")]


def role_plane_counts(shape: Sequence[int], roles: Sequence[str | None]) -> dict[str, int]:
    """Return the depth slices and time points under the confirmed roles (1 without the role)."""
    lengths = dict(zip(roles, shape, strict=True))
    return {role: lengths.get(role, 1) for role in ("Z", "T")}


def image_size_issues(sizes: Mapping[str, tuple[int, int]]) -> list[ValidationIssue]:
    """Report images of different heights or widths.

    Parameters
    ----------
    sizes
        Height and width of each image, by relative path, in the order to report them.

    Returns
    -------
    list of ValidationIssue
        The warning ``different_sizes`` naming the images not of the most common size, or
        nothing.
    """
    if len(set(sizes.values())) < 2:
        return []
    summary, minority = breakdown(sizes, lambda shape: " x ".join(str(n) for n in shape))
    return [
        warning(
            "different_sizes",
            "The images have different widths or heights (height x width in pixels): "
            f"{summary}. They can still be processed together.",
            minority,
            "No action needed if the sizes are expected, for example images cropped "
            "differently. Otherwise check that these images were acquired with the same "
            "camera settings.",
        )
    ]


def plane_count_issues(counts: Mapping[str, Mapping[str, int]]) -> list[ValidationIssue]:
    """Report images with different numbers of planes along a dimension.

    Parameters
    ----------
    counts
        Planes along each dimension of each image, by relative path; a dimension an image
        lacks counts as 1.

    Returns
    -------
    list of ValidationIssue
        The warning ``different_plane_counts``, or nothing.
    """
    axes = sorted({axis for c in counts.values() for axis in c}, key=lambda a: ("ZT".find(a), a))
    descriptions: list[str] = []
    differing: list[str] = []
    for axis in axes:
        along = {path: c.get(axis, 1) for path, c in counts.items()}
        if len(set(along.values())) < 2:
            continue
        summary, minority = breakdown(along, str)
        descriptions.append(f"{_PLANE_NAMES.get(axis, f'planes along {axis}')}: {summary}")
        differing.extend(minority)
    if not differing:
        return []
    return [
        warning(
            "different_plane_counts",
            "The images have different numbers of planes ("
            + "; ".join(descriptions)
            + "). Every plane is still analysed, but images with more planes weigh more "
            "in pooled statistics.",
            sorted(set(differing)),
            "No action needed if this is expected, for example time series of different "
            "lengths. Otherwise check that these images were acquired with the same "
            "depth range and time points.",
        )
    ]


def breakdown(
    values: Mapping[str, Hashable], describe: Callable[[Hashable], str]
) -> tuple[str, list[str]]:
    """Summarise images by value, most common first.

    Parameters
    ----------
    values
        The value of each image, by relative path.
    describe
        How a value is written in the summary; groups of equal size are ordered by it.

    Returns
    -------
    tuple of (str, list of str)
        The summary, e.g. ``"ZCYX (10 images), ZYX (2 images)"``, and the paths of the
        images outside the most common group.
    """
    groups: dict[Hashable, list[str]] = {}
    for path, value in values.items():
        groups.setdefault(value, []).append(path)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), describe(item[0])))
    summary = ", ".join(f"{describe(k)} ({plural(len(paths), 'image')})" for k, paths in ordered)
    return summary, [path for _, paths in ordered[1:] for path in paths]
