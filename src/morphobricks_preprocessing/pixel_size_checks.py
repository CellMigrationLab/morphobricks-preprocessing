"""The pixel-size checks of the dataset consistency (contract §11, decision D-a).

Pixel sizes and z-steps that differ between the images (``different_pixel_sizes``), are known
for only some of them (``missing_pixel_size``), or are given in a length unit that is not
converted (``unsupported_pixel_size_unit``, decision D-a2). Moved from :mod:`.metadata`
(PP-8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .physical_sizes import PIXEL_SIZE_TOLERANCE, _same_length, pixel_size_groups
from .validation import ValidationIssue, plural, warning

if TYPE_CHECKING:
    from .metadata import TiffMetadata


def _check_pixel_sizes(files: list[TiffMetadata]) -> list[ValidationIssue]:
    """Warn when pixel sizes (or z-steps) differ, by the shared rule of decision D-a.

    Sizes differ when, on some axis, the smallest and the largest are not the same size
    (:func:`.physical_sizes.same_pixel_size`): exactly when some two images differ. The
    images are grouped by :func:`.physical_sizes.pixel_size_groups`; those outside the
    largest group are listed. When every image is linked to the others by a chain of
    near-equal sizes, the sizes drift and no image stands out, so none is listed.
    """
    # The z-step only matters for images with more than one depth slice.
    checks = (
        ("pixel size (x by y)", files, ("X", "Y")),
        ("z-step", [f for f in files if f.length("Z") > 1], ("Z",)),
    )
    descriptions: list[str] = []
    differing: list[str] = []
    drift = False
    missing: dict[str, list[str]] = {}
    for name, members, roles in checks:
        sizes = {f.relative_path: _micrometers(f, roles) for f in members}
        known = {path: size for path, size in sizes.items() if size is not None}
        unknown = [path for path, size in sizes.items() if size is None]
        if known and unknown:
            missing[name] = unknown
        if not known:
            continue
        extremes = [(min(axis), max(axis)) for axis in zip(*known.values(), strict=True)]
        if all(_same_length(low, high) for low, high in extremes):
            continue
        groups = pixel_size_groups(known, roles=roles)
        if len(groups) == 1:
            drift = True
            descriptions.append(
                f"{name}: from {_format_range([low for low, _ in extremes])} to "
                f"{_format_range([high for _, high in extremes])} micrometres, changing by small "
                "steps from image to image"
            )
            continue
        summary = ", ".join(
            f"{_format_group([known[path] for path in group])} ({plural(len(group), 'image')})"
            for group in groups
        )
        descriptions.append(f"{name}: {summary}")
        differing.extend(path for group in groups[1:] for path in group)
    issues: list[ValidationIssue] = []
    if descriptions:
        message = (
            "The physical pixel sizes in the image metadata differ ("
            + "; ".join(descriptions)
            + f"; sizes within {PIXEL_SIZE_TOLERANCE:.1%} of each other count as the same). "
            "Objects then look larger or smaller in some images than in others."
        )
        if drift:
            message += (
                " Where the sizes change by small steps, each image has the size of another "
                "one, so no image stands out and none is listed for that size."
            )
        issues.append(
            warning(
                "different_pixel_sizes",
                message,
                sorted(set(differing)),
                "No action needed if the images were acquired with different objectives on "
                "purpose. Otherwise check the microscope settings or the metadata of these "
                "images.",
            )
        )
    if missing:
        issues.append(
            warning(
                "missing_pixel_size",
                "The image metadata give the "
                + " and the ".join(missing)
                + " for some images but not for others, so it cannot be checked that they "
                "match.",
                sorted({path for paths in missing.values() for path in paths}),
                "No action needed if the sizes are known to be the same. Otherwise export "
                "these images again with their metadata.",
            )
        )
    return issues


def _check_pixel_size_units(files: list[TiffMetadata]) -> list[ValidationIssue]:
    """Warn about pixel sizes given in a length unit that is not converted (decision D-a2).

    Such a size is never converted, so the axis is uncalibrated; without this warning
    nothing would say which unit caused it. Every image is named with its axes and unit.
    """
    found = [f for f in files if f.physical_sizes.unsupported_units]
    if not found:
        return []
    details = "; ".join(f"{f.relative_path} ({_describe_units(f)})" for f in found)
    return [
        warning(
            "unsupported_pixel_size_unit",
            f"{plural(len(found), 'image')} give a pixel size or z-step in a length unit "
            f"that is not supported: {details}. Only pm, angstrom, nm, micrometre (um, "
            "micron), mm, cm and m are converted. These sizes are not converted: the images "
            "are treated as uncalibrated on those axes.",
            [f.relative_path for f in found],
            "No action needed if the physical size does not matter. Otherwise export these "
            "images again with the pixel size in a supported unit.",
        )
    ]


def _describe_units(f: TiffMetadata) -> str:
    """Return e.g. ``"X and Y in 'inch'"`` for an image's unsupported units."""
    by_unit: dict[str, list[str]] = {}
    for axis, unit in f.physical_sizes.unsupported_units:
        by_unit.setdefault(unit, []).append(axis)
    return ", ".join(f"{' and '.join(axes)} in {unit!r}" for unit, axes in by_unit.items())


def _micrometers(f: TiffMetadata, roles: tuple[str, ...]) -> tuple[float, ...] | None:
    values = [f.physical_sizes.micrometers(role) for role in roles]
    if any(v is None for v in values):
        return None
    return tuple(values)


def _format_range(values: list[float]) -> str:
    return " x ".join(f"{v:.5g}" for v in values)


def _format_group(sizes: list[tuple[float, ...]]) -> str:
    """Describe a group's sizes per axis: one value, or the range when they differ."""
    parts = []
    for axis in zip(*sizes, strict=True):
        low, high = f"{min(axis):.5g}", f"{max(axis):.5g}"
        parts.append(low if low == high else f"{low} to {high}")
    return " x ".join(parts) + " micrometres"
