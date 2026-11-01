"""A mask's roles: the axes its header names, or those stated for its mask type (§15).

Masks are never converted or reordered; they are checked as stored against their image as
read (design v0.4 §3). The roles are the axes the mask's metadata names (the rule of
:func:`.axis_names.named_letters`, the one images use); where it names none (a plain stack),
the user states them once per mask type. A statement may not contradict a named axis. A
2-D mask stated ``YX`` for an image with several planes is one plane shared by all of them.
A mask stored with Y and X the other way round from its image is refused, with a
``numpy.swapaxes`` hint, never transposed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import tifffile

from ..axis_names import PLACEHOLDER_AXES, named_letters
from ..metadata import series_axes_from_metadata
from .header import _known_axes

MASK_ROLES = frozenset("YXZT")
"""The roles of a mask's axes: its plane (Y, X) and at most a depth (Z) and time (T) axis."""


def mask_named_axes(axes: str, tiff: Path | None = None) -> str:
    """Return a mask's header axes with ``?`` where its metadata does not name them.

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
    from_metadata = True
    if tiff is not None and set(axes) & PLACEHOLDER_AXES:
        with tifffile.TiffFile(tiff) as tif:
            from_metadata = series_axes_from_metadata(tif.series[0])
    return named_letters(axes, from_metadata)


def mask_roles(header: str, named: str, stated: str | None) -> tuple[str, str | None, str]:
    """Decide a mask's roles from its header and the axes stated for its mask type.

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
        if "?" in named or not _known_axes(header):
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
            if letter not in ("?", role)
        ]
        if contradicted:
            detail = f"header axes {header}, stated {stated}: {', '.join(contradicted)}"
            return "", "mask_axes_contradict_header", detail
    if roles.index("X") < roles.index("Y"):
        return "", "mask_layout", f"axes {roles}: X is stored before Y"
    return roles, None, ""


def _stated_problem(roles: str, count: int) -> str:
    """Say what is wrong with roles stated for a mask's ``count`` axes, or ``""``."""
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


def mask_axes_option(statements: Iterable[str]) -> dict[str, str]:
    """Read ``--mask-axes TYPE=ROLES`` statements into ``{type: roles}``.

    Parameters
    ----------
    statements
        The statements as given (``"Cells=TYX"``); the roles are checked against each mask
        later (:func:`mask_roles`).

    Returns
    -------
    dict
        The roles by mask type.

    Raises
    ------
    ValueError
        For a statement without a type or roles, or two different statements for one type.
    """
    stated: dict[str, str] = {}
    for statement in statements:
        mask_type, _, roles = statement.partition("=")
        mask_type, roles = mask_type.strip(), roles.strip()
        if not mask_type or not roles:
            raise ValueError(
                f"--mask-axes {statement}: give the mask type and its roles, for example "
                "--mask-axes Cells=TYX."
            )
        if stated.get(mask_type, roles) != roles:
            raise ValueError(
                f"--mask-axes gives two different roles for {mask_type}: "
                f"{stated[mask_type]} and {roles}."
            )
        stated[mask_type] = roles
    return stated
