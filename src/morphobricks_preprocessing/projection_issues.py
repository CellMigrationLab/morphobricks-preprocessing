"""The messages of a refused Z projection (issue #176, contract §17 "Projected copies").

Each function builds one error in plain language, naming the images and an action. Every one
of them stops the conversion before anything is written. Nothing here reads or writes
anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .projection import Projection, ProjectionRefused
from .validation import ValidationIssue, error, plural

_METHODS_HINT = "Use max or mean, or a shorter slice range."


def _few(items: Sequence[str], shown: int = 5) -> str:
    """Return the first few items for a message."""
    listed = ", ".join(items[:shown])
    return listed + (f", and {len(items) - shown} more" if len(items) > shown else "")


def nothing_to_project_issue(roles: str) -> ValidationIssue:
    """Refuse a projection when no image has a depth axis under the confirmed roles."""
    return error(
        "nothing_to_project",
        f"--project was given, but no image has a depth axis (Z) under the roles {roles}. "
        "Nothing was written.",
        [],
        "Check the roles: Z must be the axis of the slices of a stack, and every image is "
        "otherwise left as it is.",
    )


def z_range_issue(projection: Projection, sizes: Mapping[str, int]) -> ValidationIssue:
    """Refuse a slice range that does not fit every image (it is never shortened to fit)."""
    start, stop = projection.z_range
    paths = sorted(sizes)
    shorter = _few([f"{p}: {sizes[p]} slice{'' if sizes[p] == 1 else 's'}" for p in paths])
    return error(
        "projection_z_range_outside",
        f"The slice range {start}:{stop} does not fit {plural(len(paths), 'image')} "
        f"({shorter}). Nothing was written.",
        paths,
        "Give a range every image has, or leave it out to use all slices.",
    )


def pixel_type_issue(paths: Sequence[str], refused: ProjectionRefused) -> ValidationIssue:
    """Refuse images of a pixel type no projected copy can hold."""
    return error(
        refused.code,
        f"{plural(len(paths), 'image')} cannot be projected: {refused} Nothing was written.",
        paths,
        "Save these images again in a type OME-TIFF holds (for example 32-bit), if their "
        "values fit, or exclude them.",
    )


def inexact_issue(paths: Sequence[str], refused: ProjectionRefused) -> ValidationIssue:
    """Refuse a sum or mean that could not be held exactly."""
    return error(
        refused.code,
        f"{refused} Images: {_few(sorted(paths))}.",
        paths,
        _METHODS_HINT,
    )


def over_memory_issue(path: str, needed: str, limit: str, count: int) -> ValidationIssue:
    """Refuse a projection whose working planes do not fit the memory limit."""
    more = f" (and {count - 1} more image{'s' if count > 2 else ''})" if count > 1 else ""
    return error(
        "projection_over_memory_limit",
        f"Projecting {path}{more} needs {needed} for one plane and its running total; the "
        f"limit is {limit}. Nothing was written.",
        [path],
        "Raise the memory limit (--memory-limit).",
    )


def zarr_issue(paths: Sequence[str]) -> ValidationIssue:
    """Refuse OME-Zarr images in a projection (they are not projected yet)."""
    return error(
        "projection_zarr_not_supported",
        f"{plural(len(paths), 'OME-Zarr image')} cannot be projected yet: a projected copy is "
        "an OME-TIFF, and the copy tree keeps each image's path. Nothing was written.",
        paths,
        "Exclude these images, or project the TIFF images in a separate experiment folder.",
    )


def masks_3d_issue(by_type: Mapping[str, Sequence[str]]) -> ValidationIssue:
    """Refuse a projection while masks of the images have depth slices (Z)."""
    paths = sorted({p for found in by_type.values() for p in found})
    types = ", ".join(sorted(by_type))
    return error(
        "projection_masks_3d",
        f"Masks of type {types} for {plural(len(paths), 'mask')} have depth slices (Z). "
        "Masks are never projected: a projected label image merges different objects. "
        "Nothing was written.",
        paths,
        "Make 2-D masks on the projected images, or move these masks out of the masks folder.",
    )


def masks_axes_unknown_issue(paths: Sequence[str]) -> ValidationIssue:
    """Refuse a projection while masks have axes that are not known (they might be 3-D)."""
    return error(
        "projection_masks_axes_unknown",
        f"{plural(len(paths), 'mask')} of the images have more than Y and X, and their axes "
        "are not named, so it is not known whether they have depth slices (Z). Masks are "
        "never projected, and no axis is guessed. Nothing was written.",
        paths,
        "Name the axes in the masks' metadata, or move these masks out of the masks folder.",
    )


def projection_failed_issue(path: str, refused: ProjectionRefused) -> ValidationIssue:
    """Refuse a projection that the pixels made impossible (overflow, undefined sum)."""
    return error(
        refused.code,
        f"{path}: {refused} Nothing was written.",
        [path],
        "Use max, or mean if the values allow it; or exclude this image.",
    )


def _held(projection: Projection | None) -> str:
    """Return how a tree's copies were made, for a message."""
    if projection is None:
        return "no projections"
    bounds = projection.z_range
    where = "all slices" if bounds is None else f"slices {bounds[0]}:{bounds[1]}"
    return f"{projection.method} projections of {where}"


def projection_differs_issue(
    dest: Path, paths: Sequence[str], held: Projection | None, asked: Projection | None
) -> ValidationIssue:
    """Refuse another method or slice range into a tree already made with one."""
    return error(
        "copy_differs",
        f"{plural(len(paths), 'image')} in {dest} differ from the copies this conversion would "
        f"write: the folder holds {_held(held)}, and you asked for {_held(asked)}. One folder "
        "holds one method and one slice range. Nothing was overwritten.",
        list(paths),
        "Use a new, empty folder for this projection.",
    )
