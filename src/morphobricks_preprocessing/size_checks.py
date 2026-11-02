"""Image sizes and plane counts across the experiment (contract §11).

The warnings ``different_sizes`` and ``different_plane_counts`` compare, for every image,
its height and width and its numbers of depth slices, time points and planes along any
other dimension. :mod:`.metadata` measures each image by the letters its header names;
after the axis review, :meth:`.axes.AxisReview.combined_issues` measures each image by its
confirmed roles instead (issue #74): the header letters of a plain stack are only where its
pages, rows and columns are stored, so they are never used for these warnings.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence

from .validation import ValidationIssue, plural, warning

_PLANE_NAMES = {"Z": "depth slices (Z)", "T": "time points"}
"""What the planes along each dimension are called in messages."""

NOT_PLANE_AXES = frozenset("XYSC")
"""Dimensions that do not hold planes: the image itself, and channels (or RGB samples),
which are compared by ``different_channel_counts``."""


def lettered_plane_counts(axes: str, shape: Sequence[int]) -> dict[str, int]:
    """Return the planes along each header letter except X, Y, S and C; Z and T default to 1."""
    counts = {"Z": 1, "T": 1}
    counts.update(
        {axis: n for axis, n in zip(axes, shape, strict=True) if axis not in NOT_PLANE_AXES}
    )
    return counts


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
