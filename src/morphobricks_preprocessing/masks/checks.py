"""Check masks from their headers against their images (contract §15).

Moved from ``masks.py`` (PP-5).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

import tifffile

from ..axes import AxisMapping
from ..metadata import TiffMetadata, named_axes
from ..validation import ValidationIssue, agree, error, plural
from ..zarr_input import (
    NgffError,
    ZarrUnavailableError,
)
from .header import _mask_header
from .layout import _yx
from .model import FOLDER, PLANE_AXES, MaskFile, MaskInventory
from .stated import mask_named_axes, mask_roles, size_code

logger = logging.getLogger(__name__)


def _header_issues(
    files: list[MaskFile],
    images: Mapping[str, TiffMetadata],
    folder: Path | None,
    root: Path,
    unreadable: Iterable[str] = (),
    stated: Mapping[str, str] | None = None,
) -> list[ValidationIssue]:
    """Read each mask's header (axes, shape, pixel type) and check it against its image.

    The mask's roles are the axes its metadata names, or those stated for its mask type
    (``stated``) where the metadata names none (:func:`.stated.mask_roles`); ``files`` is
    updated in place with them. Also check that the masks of one image have the same depth
    slices and time points (a stated shared 2-D mask aside). ``unreadable`` are paths
    already known not to open (OME-Zarr ``labels`` groups).
    """
    found: list[tuple[str, str, str]] = [("unreadable_mask", path, "") for path in unreadable]
    for index, mask in enumerate(files):
        checked, problems = _check_header(mask, images[mask.image], folder, root, stated or {})
        found.extend(problems)
        if checked is not None:
            files[index] = checked
    by_image: dict[str, list[MaskFile]] = {}
    for mask in files:
        if mask.axes and not mask.shared:
            by_image.setdefault(mask.image, []).append(mask)
    for masks in by_image.values():
        if len({tuple(m.planes.items()) for m in masks}) > 1:
            found.extend(("mask_planes_differ", m.path, "") for m in masks)
    return _grouped(found)


def _check_header(
    mask: MaskFile,
    image: TiffMetadata,
    folder: Path | None,
    root: Path,
    stated: Mapping[str, str],
) -> tuple[MaskFile | None, list[tuple[str, str, str]]]:
    """Check one mask's header: its roles, its size against the image's named Y/X, its type.

    Returns the mask with its shape, pixel type and roles (None when its header cannot be
    used), and its problems as ``(code, path, detail)``.
    """
    try:
        axes, shape, dtype = _mask_header(mask, folder, root)
        named = mask_named_axes(axes, Path(folder) / mask.path if mask.source == FOLDER else None)
    except (OSError, ValueError, tifffile.TiffFileError, NgffError, ZarrUnavailableError) as exc:
        logger.info("Mask %s could not be opened: %s", mask.path, exc)
        return None, [("unreadable_mask", mask.path, "")]
    roles, code, detail = mask_roles(axes, named, stated.get(mask.mask_type))
    if code is not None:
        return None, [(code, mask.path, detail)]
    how = "stated" if mask.mask_type in stated else "header"
    checked = replace(mask, shape=shape, dtype=dtype, axes=roles, header_axes=axes, axes_from=how)
    problems = []
    expected = _image_yx(image)
    if expected is not None and _yx(roles, shape) != expected:
        problems.append((size_code(_yx(roles, shape), expected), mask.path, ""))
    if dtype.kind not in "biu":
        problems.append(("mask_pixel_type", mask.path, ""))
    return checked, problems


def _grouped(found: Iterable[tuple[str, str, str]]) -> list[ValidationIssue]:
    """Return one error per code, naming its masks and, where given, what is wrong."""
    paths: dict[str, list[str]] = {}
    details: dict[str, list[str]] = {}
    for code, path, detail in found:
        paths.setdefault(code, []).append(path)
        if detail:
            details.setdefault(code, []).append(f"{path}: {detail}")
    issues = []
    for code in sorted(paths):
        message = _TEXTS[code][0]
        listed = details.get(code, [])
        if listed:
            more = f"; and {len(listed) - 5} more" if len(listed) > 5 else ""
            message += " " + "; ".join(listed[:5]) + more + "."
        issues.append(error(code, message, paths[code], _TEXTS[code][1]))
    return issues


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
        "State the axes of the mask type once (for example --mask-axes Cells=TYX): one role, "
        "Y, X, Z or T, per axis of the mask. Or save each mask as a 2D label image, or with "
        "its axes named in its metadata (an ImageJ hyperstack or OME-TIFF with axes TYX or "
        "ZYX).",
    ),
    "invalid_mask_axes": (
        "The axes stated for some masks do not fit them.",
        "State one role per axis of the mask's header (length-1 axes left out): Y and X once "
        "each, and at most one Z and one T. Masks have no channels.",
    ),
    "mask_axes_contradict_header": (
        "The axes stated for some masks contradict the axes their metadata names.",
        "State the axes only for masks whose metadata does not name them, or state the named ones.",
    ),
    "mask_layout": (
        "Some masks are stored with their height and width (Y and X) the other way round from "
        "their image as it is read. Masks are never transposed.",
        "Save each such mask again with its last two axes swapped, for example "
        "numpy.swapaxes(mask, -1, -2) before saving it, or segment the image as it is read.",
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
        unchecked = not mask.axes or mask.shared or mask.image in skip
        if unchecked or mapping is None or mask.image not in headers:
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
