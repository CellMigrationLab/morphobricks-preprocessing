"""The axes of a mask: named by its header, or stated for its type (contract §15).

Masks are never converted or reordered; they are checked as stored against their image as
read (owner decision of 1 November 2026). This module decides a mask's roles from its
header and the user's statement for its mask type, and names the layout error of a mask
stored with Y and X the other way round. :mod:`.masks` pairs and checks the masks.
"""

from __future__ import annotations

from pathlib import Path

import tifffile

from .metadata import PLACEHOLDER_AXES, series_axes_from_metadata

PLANE_AXES = ("Z", "T")
"""The dimensions a mask may have besides Y and X: depth slices and time points."""

MASK_ROLES = frozenset("YXZT")
"""The roles of a mask's axes: its plane (Y, X) and at most a depth (Z) and time (T) axis."""


def mask_roles(header: str, named: str, stated: str | None) -> tuple[str, str | None, str]:
    """Decide a mask's roles from its header and the axes stated for its mask type.

    Masks are never reordered or converted, only checked as stored (owner decision of 1
    November 2026). The roles are the axes the header names; where it names none (a plain
    stack, ``?`` in ``named``), the user's statement for the mask type gives them. A
    statement may not contradict a named axis. The plane must be stored Y before X: a mask
    stored with X first is refused with a ``numpy.swapaxes`` hint, never transposed.

    Parameters
    ----------
    header
        The mask's header axes, length-1 axes other than Y and X left out.
    named
        The same axes with ``?`` for each one the metadata does not name.
    stated
        The roles stated for the mask's type (one per axis of ``header``), or None.

    Returns
    -------
    tuple
        ``(roles, None, "")`` when the roles are known, or ``("", code, detail)`` with the
        error code (``mask_not_2d``, ``invalid_mask_axes``, ``mask_axes_contradict_header``,
        ``mask_layout``) and what is wrong.
    """
    if stated is None:
        if "?" in named or not known_mask_axes(header):
            return "", "mask_not_2d", f"header axes {header}"
        roles = header
    else:
        roles = stated.upper()
        problem = _stated_problem(roles, len(header))
        if problem:
            return "", "invalid_mask_axes", f"header axes {header}, stated {stated}: {problem}"
        contradicted = [
            f"{letter} stated {role}"
            for letter, role in zip(named, roles, strict=True)
            if letter != "?" and letter != role
        ]
        if contradicted:
            return (
                "",
                "mask_axes_contradict_header",
                f"header axes {header}, stated {stated}: {', '.join(contradicted)}",
            )
    if roles.index("X") < roles.index("Y"):
        return "", "mask_layout", f"axes {roles}: X is stored before Y"
    return roles, None, ""


def _stated_problem(roles: str, count: int) -> str:
    """Say what is wrong with roles stated for a mask's axes, or ``""`` when nothing.

    Parameters
    ----------
    roles
        The stated roles, upper case.
    count
        The number of axes of the mask's header (length-1 axes other than Y and X left out).

    Returns
    -------
    str
        The problem in plain words.
    """
    if len(roles) != count:
        return f"{len(roles)} roles for {count} axes"
    others = sorted(set(roles) - MASK_ROLES)
    if others:
        return f"{', '.join(others)} is not a mask role (Y, X, Z or T; masks have no channels)"
    if roles.count("Y") != 1 or roles.count("X") != 1:
        return "Y and X are needed once each"
    if roles.count("Z") > 1 or roles.count("T") > 1:
        return "Z and T may appear once each"
    return ""


def size_code(mask_yx: tuple[int, int], image_yx: tuple[int, int]) -> str:
    """Name the error of a mask whose height and width are not its image's.

    Parameters
    ----------
    mask_yx, image_yx
        The (Y, X) sizes of the mask and of its image as read.

    Returns
    -------
    str
        ``"mask_layout"`` when they are the image's swapped (never transposed), otherwise
        ``"mask_size_mismatch"``.
    """
    return "mask_layout" if mask_yx == image_yx[::-1] else "mask_size_mismatch"


def mask_named_axes(axes: str, tiff: Path | None = None) -> str:
    """Return a mask's header axes with ``?`` where its metadata does not name them.

    The rule of :func:`.metadata.named_axes` for masks: placeholders (``I``, ``Q``) are
    open, and in a plain TIFF stack without axis metadata, its Y and X too.

    Parameters
    ----------
    axes
        The mask's header axes, length-1 axes other than Y and X left out.
    tiff
        The mask's TIFF file, read for its metadata kind only when ``axes`` has a
        placeholder; None for an OME-Zarr label image, whose axes are its OME-NGFF axes.

    Returns
    -------
    str
        One character per axis, e.g. ``"TYX"``, ``"?YX"`` or ``"???"``.
    """
    plain = False
    if tiff is not None and set(axes) & PLACEHOLDER_AXES:
        with tifffile.TiffFile(tiff) as tif:
            plain = not series_axes_from_metadata(tif.series[0])
    return "".join("?" if a in PLACEHOLDER_AXES or (plain and a in "YX") else a for a in axes)


def known_mask_axes(axes: str) -> bool:
    """Return True if the axes are Y and X, each once, plus at most one Z and one T.

    Parameters
    ----------
    axes
        A mask's axes, length-1 axes other than Y and X left out.

    Returns
    -------
    bool
        Whether a mask with these axes needs no statement.
    """
    return (
        axes.count("Y") == 1
        and axes.count("X") == 1
        and all(axes.count(a) <= 1 for a in PLANE_AXES)
        and set(axes) <= {"Y", "X", *PLANE_AXES}
    )
