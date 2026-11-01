"""Mask checks that need the confirmed axis roles (contract §15).

:func:`.masks.find_masks` checks masks from their headers before any role is confirmed.
With the confirmed roles, a mask's Y and X are compared with its image's Y and X *by role*
(:func:`mask_size_issues`) and its planes with the image's (:func:`.masks.mask_plane_issues`);
:func:`mask_issues` gives a run's mask issues. Masks are never converted or reordered; an
image with an axis error is never read, so its masks are not compared.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .axes import AxisMapping, AxisReview
from .masks import MaskInventory, mask_plane_issues
from .masks.checks import _TEXTS
from .masks.layout import _yx
from .metadata import MetadataReport, TiffMetadata
from .validation import ValidationIssue, error


def mask_size_issues(
    inventory: MaskInventory,
    images: Iterable[TiffMetadata],
    file_mappings: Mapping[str, AxisMapping],
    *,
    skip: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Check that every mask has the Y and X sizes its image has under the confirmed roles.

    The image's Y and X are the dimensions its confirmed roles call Y and X, not its
    header's letters. :func:`.run.run_experiment` uses this in place of :func:`find_masks`'s
    header comparison, which it supersedes.

    Parameters
    ----------
    inventory
        The masks found by :func:`find_masks`.
    images
        Header information of the analysed images.
    file_mappings
        Each image's own axis mapping (:attr:`.axes.AxisReview.file_mappings`); images
        without one are left to the axis review's errors.
    skip
        Images not to check: those with an axis error (:attr:`.axes.AxisReview.axis_error_files`),
        which are never read, so a mask size would only be a misleading second message. Their
        masks are checked when the images are saved as named-axis copies.

    Returns
    -------
    list of ValidationIssue
        The error ``mask_size_mismatch`` naming the masks that do not fit, or nothing.
    """
    headers = {meta.relative_path: meta for meta in images}
    skip = set(skip)
    mismatched = []
    for mask in inventory.files:
        mapping = file_mappings.get(mask.image)
        meta = headers.get(mask.image)
        if not mask.axes or mapping is None or meta is None or mask.image in skip:
            continue
        y, x = mapping.index_of("Y"), mapping.index_of("X")
        if y is None or x is None:
            continue
        if _yx(mask.axes, mask.shape) != (meta.shape[y], meta.shape[x]):
            mismatched.append(mask.path)
    if not mismatched:
        return []
    message, action = _TEXTS["mask_size_mismatch"]
    return [error("mask_size_mismatch", message, sorted(mismatched), action)]


def mask_issues(
    masks: MaskInventory, metadata: MetadataReport, review: AxisReview
) -> list[ValidationIssue]:
    """Return the mask issues of a run, with the masks' sizes and planes by the confirmed roles.

    :func:`.masks.find_masks` compares a mask's Y and X with those its image's header
    names; with confirmed roles that comparison is replaced by
    :func:`mask_size_issues`, and the planes are checked by
    :func:`.masks.mask_plane_issues`. Images with an axis error are never read, so their
    masks are left out of both: a size or plane message would only repeat the axis error
    in another form.

    Parameters
    ----------
    masks
        The masks found by :func:`.masks.find_masks`.
    metadata
        The image headers.
    review
        The axis review of the confirmed mapping.

    Returns
    -------
    list of ValidationIssue
        The inventory's issues, with the size issues by role, and the plane issues.
    """
    skip = review.axis_error_files
    return [
        *(i for i in masks.issues if i.code != "mask_size_mismatch"),
        *mask_size_issues(masks, metadata.files, review.file_mappings, skip=skip),
        *mask_plane_issues(masks, metadata.files, review.file_mappings, skip=skip),
    ]
