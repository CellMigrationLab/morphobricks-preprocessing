"""The brick's check families, for the discovery report (contract §18).

Every check of the brick belongs to one family (contract §1, §2, §4, §8, §11–§15); the
report lists each family with what it covered in a run. Also the normalization version
check: the run compares the installed normalization brick with the release this one pins.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, requires

import morphobricks_normalization

from .validation import ValidationIssue, plural, warning

CHECK_FAMILIES: Mapping[str, tuple[str, ...]] = {
    "folders_and_names": (
        "experiment_not_found",
        "unreadable_folder",
        "folder_too_deep",
        "reserved_folder_name",
        "case_conflict",
        "invalid_name",
        "duplicate_position_id",
        "tiff_outside_leaf",
        "image_named_as_mask",
        "mask_folder_in_experiment",
        "non_tiff_file",
        "empty_folder",
        "output_inside_experiment",
        "output_inside_masks",
        "no_images",
        "excluded_images",
        "excluded_path_not_found",
    ),
    "images": (
        "unreadable_tiff",
        "unreadable_ome_zarr",
        "unreadable_planes",
        "multiple_series",
        "unsupported_dtype",
        "mixed_dtypes",
        "zarr_not_installed",
        "plane_over_memory_limit",
        "unsupported_layout",
        "planes_read_in_blocks",
        "non_finite_pixels",
        "no_finite_pixels",
        "no_usable_planes",
        "invalid_bin_count",
    ),
    "axes": (
        "axes_not_named",
        "axes_yx_swapped",
        "axes_contradict_header",
        "missing_axes",
        "ambiguous_missing_axes",
        "inconsistent_dimensions",
        "inconsistent_mapping",
        "ome_zarr_axes_mismatch",
        "missing_xy_axis",
        "duplicate_axis_role",
        "unassigned_axis",
        "unsupported_axis",
    ),
    "dataset_consistency": (
        "unbalanced_images",
        "repeat_missing_conditions",
        "different_pixel_sizes",
        "missing_pixel_size",
        "unsupported_pixel_size_unit",
        "different_channel_counts",
        "channel_names_differ",
        "different_plane_counts",
        "different_sizes",
    ),
    "experiment_design": (
        "design_not_confirmed",
        "repeat_group_conflict",
        "invalid_experiment_design",
        "unreadable_experiment_design",
    ),
    "image_type": ("rgb_not_possible",),
    "histograms": ("narrow_histogram_range",),
    "intensity_checks": (
        "image_intensity_outlier",
        "repeat_intensity_shift",
        "saturated_image",
        "no_intensity_spread",
    ),
    "masks": (
        "masks_folder_not_found",
        "masks_inside_experiment",
        "mask_names_differ_in_case",
        "duplicate_mask",
        "ambiguous_mask",
        "mask_without_image",
        "mask_of_excluded_image",
        "mask_outside_type_folder",
        "mask_missing",
        "unreadable_mask",
        "mask_not_2d",
        "invalid_mask_axes",
        "mask_axes_contradict_header",
        "mask_layout",
        "mask_size_mismatch",
        "mask_pixel_type",
        "mask_planes_differ",
        "mask_planes_mismatch",
    ),
    "normalization_version": ("normalization_version_differs",),
}
"""Every check family of the brick and the codes it gives (contract §1, §2, §4, §8, §11–§15).
A code not listed falls under ``"other"``, so no issue is ever left out of the report."""

_SCOPES = {
    "folders_and_names": "every folder and file, again in every run",
    "images": "every image header, again in every run; pixels of the images read in this run",
    "axes": "every image, again in every run, against the confirmed roles",
    "dataset_consistency": "the whole experiment, again in every run (never only the new images)",
    "experiment_design": "the whole design, again in every run",
    "image_type": "every image, again in every run",
    "intensity_checks": "every row of the table, again in every run (never only the new images)",
    "masks": "every mask header and its pairing, again in every run",
    "normalization_version": "the installed normalization brick, again in every run",
}


def family_of(code: str) -> str:
    """Return the check family of an issue code.

    Parameters
    ----------
    code
        The issue code.

    Returns
    -------
    str
        The family's name, ``"other"`` for a code no family lists.
    """
    return next((name for name, codes in CHECK_FAMILIES.items() if code in codes), "other")


def family_scope(family: str, mode: str, read: int, reused: int) -> str:
    """Say what a check family covered in a run.

    Every check runs over the whole experiment on every run; only the per-plane rows of
    unchanged images, with their range checks, are reused.

    Parameters
    ----------
    family
        The family's name.
    mode
        ``"restart"``, ``"full"`` or ``"check"``.
    read, reused
        Numbers of images read, and of images whose rows were reused.

    Returns
    -------
    str
        The scope in plain words.
    """
    if mode == "check" and family in ("histograms", "intensity_checks"):
        return "not run: 'check' reads no pixels"
    if family == "histograms":
        text = f"computed for {plural(read, 'image')} read in this run"
        if reused:
            text += (
                f"; the rows of {plural(reused, 'unchanged image')} were reused with the "
                "range checks they were written with"
            )
        return text
    return _SCOPES.get(family, "every image")


def normalization_version_issues() -> list[ValidationIssue]:
    """Warn when the installed normalization brick is not the release this one pins.

    The notebook's Step 0 makes sure the brick is installed; the run records its version
    (``versions`` in the manifest). This compares that version with the tag in this
    package's requirements (``@v0.3.2``), so a mismatch is never silent.

    Returns
    -------
    list of ValidationIssue
        The warning ``normalization_version_differs``, or nothing.
    """
    try:
        requirements = requires("morphobricks-preprocessing") or []
    except PackageNotFoundError:  # a source checkout that is not installed
        return []
    pinned = None
    for requirement in requirements:
        found = re.search(r"morphobricks-normalization.*@v([0-9][0-9.]*)", requirement)
        if found:
            pinned = found.group(1)
    installed = morphobricks_normalization.__version__
    if pinned is None or installed == pinned:
        return []
    return [
        warning(
            "normalization_version_differs",
            f"The installed morphobricks-normalization is version {installed}, but this "
            f"brick is pinned to its release v{pinned}. Histograms and the table format come "
            "from it.",
            action=f'Reinstall the brick (pip install "morphobricks-preprocessing") so that '
            f"morphobricks-normalization v{pinned} is used.",
        )
    ]
