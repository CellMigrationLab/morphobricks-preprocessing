"""The pixel-size checks of the dataset consistency (contract §11, decision D-a).

Pixel sizes and z-steps that differ between the images (``different_pixel_sizes``), are known
for only some of them (``missing_pixel_size``), or are given in a length unit that is not
converted (``unsupported_pixel_size_unit``, decision D-a2). The checks run on the sizes an
image is recorded with: its header's, or those stated in ``calibration.csv`` (design v0.4 §3,
:mod:`.calibration`), so they check the resolved values. Moved from :mod:`.metadata` (PP-8).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .physical_sizes import PIXEL_SIZE_TOLERANCE, _same_length, pixel_size_groups
from .recorded_sizes import HEADER, PIXELS, RecordedSize, header_sizes
from .validation import ValidationIssue, agree, plural, warning

if TYPE_CHECKING:
    from .metadata import TiffMetadata

PIXEL_SIZE_CODES = ("different_pixel_sizes", "missing_pixel_size", "unsupported_pixel_size_unit")
"""The codes of these checks; :func:`.run.preflight.preflight` gives them on resolved sizes."""


@dataclass(frozen=True)
class _Axis:
    """One compared size: its name in messages, the images it applies to and their roles."""

    name: str
    members: tuple[str, ...]
    roles: tuple[str, ...]


def pixel_size_issues(
    files: Sequence[TiffMetadata],
    sizes: Mapping[str, Sequence[RecordedSize]] | None = None,
) -> list[ValidationIssue]:
    """Check the images' pixel sizes against each other (contract §11).

    Parameters
    ----------
    files
        The readable images' headers.
    sizes
        Each image's recorded sizes by relative path (:mod:`.calibration`); by default its
        header's (:func:`.recorded_sizes.header_sizes`).

    Returns
    -------
    list of ValidationIssue
        Warnings only: ``different_pixel_sizes``, ``missing_pixel_size`` and
        ``unsupported_pixel_size_unit``.
    """
    if sizes is None:
        sizes = {f.relative_path: header_sizes(f.physical_sizes) for f in files}
    by_role = {f.relative_path: {size.role: size for size in sizes[f.relative_path]} for f in files}
    # The z-step only matters for images with more than one depth slice.
    axes = (
        _Axis("pixel size (x by y)", tuple(f.relative_path for f in files), ("X", "Y")),
        _Axis("z-step", tuple(f.relative_path for f in files if f.length("Z") > 1), ("Z",)),
    )
    return [
        *_different_issue(axes, by_role),
        *_missing_issue(axes, by_role),
        *_unit_issue(files, by_role),
    ]


def _micrometers(
    sizes: Mapping[str, RecordedSize], roles: tuple[str, ...]
) -> tuple[float, ...] | None:
    """Return the sizes along ``roles`` in micrometres; None when any is unknown."""
    values = [sizes[role].micrometers() if role in sizes else None for role in roles]
    if any(value is None for value in values):
        return None
    return tuple(values)


def _different_issue(
    axes: Sequence[_Axis], by_role: Mapping[str, Mapping[str, RecordedSize]]
) -> list[ValidationIssue]:
    """Warn when pixel sizes (or z-steps) differ, by the shared rule of decision D-a.

    Sizes differ when, on some axis, the smallest and the largest are not the same size
    (:func:`.physical_sizes.same_pixel_size`): exactly when some two images differ. The
    images are grouped by :func:`.physical_sizes.pixel_size_groups`; those outside the
    largest group are listed. When every image is linked to the others by a chain of
    near-equal sizes, the sizes drift and no image stands out, so none is listed.
    """
    descriptions: list[str] = []
    differing: list[str] = []
    drift = False
    for axis in axes:
        known = {path: _micrometers(by_role[path], axis.roles) for path in axis.members}
        known = {path: size for path, size in known.items() if size is not None}
        found = _differences(axis.name, known, axis.roles)
        if found is not None:
            descriptions.append(found[0])
            differing.extend(found[1])
            drift = drift or not found[1]
    if not descriptions:
        return []
    stated = any(
        size.source != HEADER and size.value is not None
        for sizes in by_role.values()
        for size in sizes.values()
    )
    where = ", from the image metadata and calibration.csv," if stated else " in the image metadata"
    message = (
        f"The physical pixel sizes{where} differ ("
        + "; ".join(descriptions)
        + f"; sizes within {PIXEL_SIZE_TOLERANCE:.1%} of each other count as the same). "
        "Objects then look larger or smaller in some images than in others."
    )
    if drift:
        message += (
            " Where the sizes change by small steps, each image has the size of another "
            "one, so no image stands out and none is listed for that size."
        )
    action = (
        "No action needed if the images were acquired with different objectives on "
        "purpose. Otherwise check the microscope settings or the metadata of these "
        "images" + (", or the sizes stated in calibration.csv." if stated else ".")
    )
    return [warning("different_pixel_sizes", message, sorted(set(differing)), action)]


def _differences(
    name: str, known: Mapping[str, tuple[float, ...]], roles: tuple[str, ...]
) -> tuple[str, list[str]] | None:
    """Describe how one size differs: the text and the images outside the largest group.

    None when every size is the same; no image when the sizes only drift.
    """
    if not known:
        return None
    extremes = [(min(axis), max(axis)) for axis in zip(*known.values(), strict=True)]
    if all(_same_length(low, high) for low, high in extremes):
        return None
    groups = pixel_size_groups(known, roles=roles)
    if len(groups) == 1:
        text = (
            f"{name}: from {_format_range([low for low, _ in extremes])} to "
            f"{_format_range([high for _, high in extremes])} micrometres, changing by small "
            "steps from image to image"
        )
        return text, []
    summary = ", ".join(
        f"{_format_group([known[path] for path in group])} ({plural(len(group), 'image')})"
        for group in groups
    )
    return f"{name}: {summary}", [path for group in groups[1:] for path in group]


def _missing_issue(
    axes: Sequence[_Axis], by_role: Mapping[str, Mapping[str, RecordedSize]]
) -> list[ValidationIssue]:
    """Warn when a size is known for some images and not for others.

    An image stated as measured in pixels counts as not known (it is deliberately
    uncalibrated); the message says how many there are, so the source is named.
    """
    missing: dict[str, list[str]] = {}
    for axis in axes:
        sizes = {path: _micrometers(by_role[path], axis.roles) for path in axis.members}
        unknown = [path for path, size in sizes.items() if size is None]
        if unknown and len(unknown) < len(sizes):
            missing[axis.name] = unknown
    if not missing:
        return []
    paths = sorted({path for found in missing.values() for path in found})
    in_pixels = [path for path in paths if any(s.source == PIXELS for s in by_role[path].values())]
    message = (
        "The " + " and the ".join(missing) + f" {agree(missing, 'is', 'are')} known for some "
        "images but not for others, so it cannot be checked that they match."
    )
    if in_pixels:
        message += (
            f" {plural(len(in_pixels), 'of these images is', 'of these images are')} measured "
            "in pixels, as calibration.csv states."
        )
    return [
        warning(
            "missing_pixel_size",
            message,
            paths,
            "No action needed if the sizes are known to be the same. Otherwise export these "
            "images again with their metadata, or state their sizes in calibration.csv "
            "(the 'calibrate' command).",
        )
    ]


def _unit_issue(
    files: Sequence[TiffMetadata], by_role: Mapping[str, Mapping[str, RecordedSize]]
) -> list[ValidationIssue]:
    """Warn about header sizes in a length unit that is not converted (decision D-a2).

    Such a size is never converted, so the axis is uncalibrated; without this warning
    nothing would say which unit caused it. Every image is named with its axes and unit.
    An axis whose size is stated in ``calibration.csv`` does not use the header's.
    """
    found = {}
    for f in files:
        units = tuple(
            (axis, unit)
            for axis, unit in f.physical_sizes.unsupported_units
            if axis not in by_role[f.relative_path]
            or by_role[f.relative_path][axis].source in (None, HEADER)
        )
        if units:
            found[f.relative_path] = units
    if not found:
        return []
    details = "; ".join(f"{path} ({_describe_units(units)})" for path, units in found.items())
    return [
        warning(
            "unsupported_pixel_size_unit",
            f"{plural(len(found), 'image')} give a pixel size or z-step in a length unit "
            f"that is not supported: {details}. Only pm, angstrom, nm, micrometre (um, "
            "micron), mm, cm and m are converted. These sizes are not converted: the images "
            "are treated as uncalibrated on those axes.",
            list(found),
            "No action needed if the physical size does not matter. Otherwise export these "
            "images again with the pixel size in a supported unit, or state it in "
            "calibration.csv (the 'calibrate' command).",
        )
    ]


def _describe_units(units: Sequence[tuple[str, str]]) -> str:
    """Return e.g. ``"X and Y in 'inch'"`` for an image's unsupported units."""
    by_unit: dict[str, list[str]] = {}
    for axis, unit in units:
        by_unit.setdefault(unit, []).append(axis)
    return ", ".join(f"{' and '.join(axes)} in {unit!r}" for unit, axes in by_unit.items())


def _format_range(values: list[float]) -> str:
    """Return sizes per axis as ``"0.5 x 0.5"``."""
    return " x ".join(f"{v:.5g}" for v in values)


def _format_group(sizes: list[tuple[float, ...]]) -> str:
    """Describe a group's sizes per axis: one value, or the range when they differ."""
    parts = []
    for axis in zip(*sizes, strict=True):
        low, high = f"{min(axis):.5g}", f"{max(axis):.5g}"
        parts.append(low if low == high else f"{low} to {high}")
    return " x ".join(parts) + " micrometres"
