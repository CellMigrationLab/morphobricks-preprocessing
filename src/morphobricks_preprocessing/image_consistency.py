"""The pixel-type and dimension-arrangement checks of a set of image headers.

These are the folder check's ``mixed_dtypes`` and ``inconsistent_dimensions`` (or
``missing_axes``) errors. :func:`.metadata.check_metadata` applies them to every image of the
experiment (the folder check); the run applies them per image type of the description
(:mod:`.unit_consistency`). The checks themselves are the same in both.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .size_checks import CONVERT_KEEPS_ORDER, breakdown
from .validation import ValidationIssue, error

if TYPE_CHECKING:
    from .metadata import TiffMetadata


def dtype_issues(files: Sequence[TiffMetadata]) -> list[ValidationIssue]:
    """Report images of more than one pixel type (``mixed_dtypes``).

    Parameters
    ----------
    files
        The readable image headers to compare.

    Returns
    -------
    list of ValidationIssue
        The error ``mixed_dtypes`` naming the files not of the most common type, or nothing.
    """
    if len({f.dtype for f in files}) < 2:
        return []
    summary, minority = breakdown({f.relative_path: f.dtype.name for f in files}, str)
    return [
        error(
            "mixed_dtypes",
            f"The images use different pixel types: {summary}. One experiment must "
            "use a single pixel type so that its histograms can be combined. The "
            "files listed are those not of the most common type.",
            minority,
            "Process each pixel type as a separate experiment, or convert the images "
            "to one pixel type.",
        )
    ]


def _is_subsequence(short: str, long: str) -> bool:
    remaining = iter(long)
    return all(char in remaining for char in short)


def arrangement_issues(files: Sequence[TiffMetadata]) -> list[ValidationIssue]:
    """Report images whose dimensions are arranged differently.

    Parameters
    ----------
    files
        The readable TIFF headers to compare.

    Returns
    -------
    list of ValidationIssue
        ``missing_axes`` when the only difference is one dimension that ``tifffile`` drops
        (a single channel or z-slice), else ``inconsistent_dimensions``; nothing when every
        image has the same arrangement.
    """
    arrangements = {f.axes for f in files}
    if len(arrangements) < 2:
        return []
    summary, minority = breakdown({f.relative_path: f.axes for f in files}, str)
    longest = max(arrangements, key=len)
    if all(_is_subsequence(axes, longest) for axes in arrangements) and (
        sum(len(axes) == len(longest) for axes in arrangements) == 1
    ):
        # The common case: one channel or one z-slice, which tifffile drops.
        return [
            error(
                "missing_axes",
                f"Some images lack dimensions that the others have: {summary}. "
                "Processing is blocked until it is confirmed how the missing "
                "dimensions should be read.",
                minority,
                "If the missing dimensions simply have one plane in these files (for "
                "example a single channel), confirm this during axis review. Otherwise "
                "export the images again with the same dimensions.",
            )
        ]
    return [
        error(
            "inconsistent_dimensions",
            f"The images have their dimensions arranged differently: {summary}. One "
            "axis mapping cannot describe all of them.",
            minority,
            CONVERT_KEEPS_ORDER,
        )
    ]
