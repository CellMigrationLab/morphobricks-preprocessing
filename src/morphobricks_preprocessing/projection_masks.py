"""Masks stop a projection when they have depth slices (issue #176, contract §17).

Masks are never projected: a maximum, mean or sum of a label image merges different objects
that lie at the same Y, X, and a sum is not a label. So before anything is written, the
masks folder is scanned for the masks of the images to project, from their headers only
(:func:`.masks.find_masks`, no pixels). A mask with more than one depth slice (Z), or with
axes that are not named although it has more than Y and X (it might be 3-D, and no axis is
guessed), refuses the whole projection. 2-D masks are left where they are.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Iterable

from .discovery import DiscoveryResult
from .masks import find_masks
from .metadata import TiffMetadata
from .projection_issues import masks_3d_issue, masks_axes_unknown_issue
from .validation import ValidationIssue


def projection_mask_issues(
    masks_folder: str | os.PathLike | None,
    discovery: DiscoveryResult,
    headers: Iterable[TiffMetadata],
    images: Collection[str],
) -> list[ValidationIssue]:
    """Return the errors for masks of the projected images that are, or may be, 3-D.

    Parameters
    ----------
    masks_folder
        The masks folder, or None (nothing to scan).
    discovery
        The experiment's folders.
    headers
        The headers of the analysed images.
    images
        The images to be projected (relative paths); the masks of other images are left
        alone.

    Returns
    -------
    list of ValidationIssue
        ``projection_masks_3d`` and ``projection_masks_axes_unknown``, or nothing.
    """
    if masks_folder is None:
        return []
    inventory = find_masks(masks_folder, discovery, headers)
    deep: dict[str, list[str]] = {}
    unknown: list[str] = []
    for mask in inventory.files:
        if mask.image not in images or not mask.shape:
            continue
        if mask.axes and mask.planes["Z"] > 1:
            deep.setdefault(mask.mask_type, []).append(mask.path)
        elif not mask.axes and len(mask.shape) > 2:
            unknown.append(mask.path)
    issues = []
    if deep:
        issues.append(masks_3d_issue(deep))
    if unknown:
        issues.append(masks_axes_unknown_issue(sorted(unknown)))
    return issues
