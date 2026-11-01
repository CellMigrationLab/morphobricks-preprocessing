"""Check masks from their headers against their images (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

import tifffile

from ..axes import AxisMapping
from ..metadata import TiffMetadata, named_axes
from ..validation import ValidationIssue, agree, error, plural
from ..zarr_input import (
    NgffError,
    ZarrUnavailableError,
)
from .header import _known_axes, _mask_header
from .layout import _yx
from .model import PLANE_AXES, MaskFile, MaskInventory

logger = logging.getLogger(__name__)


def _header_issues(
    files: list[MaskFile],
    images: Mapping[str, TiffMetadata],
    folder: Path | None,
    root: Path,
    unreadable: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Read each mask's header (axes, shape, pixel type) and check it against its image.

    Also check that the masks of one image have the same depth slices and time points.
    ``unreadable`` are paths already known not to open (OME-Zarr ``labels`` groups).
    """
    problems: dict[str, list[str]] = {}
    if unreadable:
        problems["unreadable_mask"] = list(unreadable)
    for index, mask in enumerate(files):
        try:
            axes, shape, dtype = _mask_header(mask, folder, root)
        except (
            OSError,
            ValueError,
            tifffile.TiffFileError,
            NgffError,
            ZarrUnavailableError,
        ) as exc:
            logger.info("Mask %s could not be opened: %s", mask.path, exc)
            problems.setdefault("unreadable_mask", []).append(mask.path)
            continue
        if not _known_axes(axes):
            problems.setdefault("mask_not_2d", []).append(mask.path)
            continue
        files[index] = MaskFile(
            mask.image, mask.mask_type, mask.path, mask.source, shape, dtype, axes
        )
        expected = _image_yx(images[mask.image])
        if expected is not None and _yx(axes, shape) != expected:
            problems.setdefault("mask_size_mismatch", []).append(mask.path)
        if dtype.kind not in "biu":
            problems.setdefault("mask_pixel_type", []).append(mask.path)
    by_image: dict[str, list[MaskFile]] = {}
    for mask in files:
        if mask.axes:
            by_image.setdefault(mask.image, []).append(mask)
    for masks in by_image.values():
        if len({tuple(m.planes.items()) for m in masks}) > 1:
            problems.setdefault("mask_planes_differ", []).extend(m.path for m in masks)
    return [
        error(code, _TEXTS[code][0], paths, _TEXTS[code][1])
        for code, paths in sorted(problems.items())
    ]


_TEXTS = {
    "unreadable_mask": (
        "Some masks could not be opened.",
        "Check that they are TIFF files (or OME-Zarr label images, listed in the image's "
        "labels group) with one image each and not damaged; the technical details are in the "
        "log.",
    ),
    "mask_not_2d": (
        "Some masks have dimensions other than Y and X that their metadata does not name as "
        "depth (Z) or time (T), so their planes cannot be matched to the image's.",
        "Save each mask as a 2D label image, or with its depth and time axes named in its "
        "metadata (for example an ImageJ hyperstack or OME-TIFF with axes TYX or ZYX).",
    ),
    "mask_planes_differ": (
        "The masks of some images have different numbers of depth slices or time points, so "
        "they cannot all belong to the same planes of their image.",
        "Make every mask of an image with the image's depth slices and time points.",
    ),
    "mask_size_mismatch": (
        "Some masks do not have the size (Y and X) of their image.",
        "Check that each mask was made from its image, at full resolution.",
    ),
    "mask_pixel_type": (
        "Some masks do not hold whole numbers: mask pixels must be integers (labels) or "
        "true/false.",
        "Save the masks as integer label images.",
    ),
}


def mask_plane_issues(
    inventory: MaskInventory,
    images: Iterable[TiffMetadata],
    file_mappings: Mapping[str, AxisMapping],
    *,
    skip: Iterable[str] = (),
) -> list[ValidationIssue]:
    """Check that every mask has the depth slices and time points of its image (issue #36).

    An image's depth slices and time points come from its confirmed axis roles; a mask's
    from its own metadata (:attr:`MaskFile.planes`). They must be equal: a 2D mask belongs
    to an image with one plane, never to every plane of a Z-stack or time-lapse.

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
        Images not to check: those with an axis error, as in :func:`mask_size_issues`.

    Returns
    -------
    list of ValidationIssue
        The error ``mask_planes_mismatch`` naming the masks that do not fit, or nothing.
    """
    headers = {meta.relative_path: meta for meta in images}
    skip = set(skip)
    mismatched = []
    for mask in inventory.files:
        mapping = file_mappings.get(mask.image)
        if not mask.axes or mapping is None or mask.image not in headers or mask.image in skip:
            continue
        shape = headers[mask.image].shape
        planes = {}
        for axis in PLANE_AXES:
            index = mapping.index_of(axis)
            planes[axis] = 1 if index is None else int(shape[index])
        if planes != mask.planes:
            mismatched.append(mask.path)
    if not mismatched:
        return []
    return [
        error(
            "mask_planes_mismatch",
            f"{plural(len(mismatched), 'mask')} "
            f"{agree(mismatched, 'does', 'do')} not have the depth slices "
            f"and time points of "
            f"{agree(mismatched, 'its', 'their')} image: a 2D mask fits only an image with "
            "one plane, and a mask with depth (Z) or time (T) needs the same number of slices "
            "or time points as its image.",
            mismatched,
            "Save one mask plane for every depth slice and time point of the image, with the "
            "axes named in the mask's metadata, or check the image's axis roles.",
        )
    ]


def _image_yx(meta: TiffMetadata) -> tuple[int, int] | None:
    """Return the Y and X sizes the image's header names, or None when it does not name them.

    Used by :func:`find_masks`, before the axis roles are confirmed. A plain stack's ``Y``
    and ``X`` are only where its pages' rows and columns are stored
    (:func:`.metadata.named_axes`), so its masks are compared by the confirmed roles only
    (:func:`mask_size_issues`).
    """
    named = named_axes(meta)
    if "Y" not in named or "X" not in named:
        return None
    return (meta.shape[named.index("Y")], meta.shape[named.index("X")])
