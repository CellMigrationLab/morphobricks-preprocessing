"""What the notebook shows about the masks, and which mask types need their axes stated.

The masks are paired and checked by :func:`.masks.find_masks` and, once the axis roles are
confirmed, :func:`.mask_checks.mask_issues`: the same functions as ``check --masks`` and
``run``. This module only reads their results (contract §15, design v0.4 §2-§3):

- :func:`mask_type_rows`: per mask type, its masks, their axes and the images without one;
- :func:`mask_axes_questions`: the mask types whose axes must be stated (the notebook's
  counterpart of ``--mask-axes TYPE=ROLES``), with the statements that fit every mask of
  the type, each checked by :func:`.masks.stated.mask_roles` (the rule ``--mask-axes`` is
  checked by). A statement is asked for only where it is needed: masks whose files do not
  name their axes (``mask_not_2d``) or whose statement does not fit, and 2-D masks of
  images with several planes, which are one plane shared by all of them only when stated
  ``YX`` (``mask_planes_mismatch``). Nothing is suggested or applied: the user chooses.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from itertools import permutations

from .masks import MaskInventory
from .masks.stated import mask_roles
from .validation import ValidationIssue

UNNAMED_CODES = frozenset({"mask_not_2d", "invalid_mask_axes", "mask_axes_contradict_header"})
"""Errors of masks whose axes their files do not name, or whose stated axes do not fit."""

SHARED_CODE = "mask_planes_mismatch"
"""The error of a 2-D mask of an image with several planes, unless stated ``YX`` (shared)."""


@dataclass(frozen=True)
class MaskTypeRow:
    """One mask type as found.

    Attributes
    ----------
    mask_type
        The type's name.
    masks
        Number of masks of the type.
    sources
        Where they come from: ``"folder"`` and/or ``"ome-zarr"``, sorted.
    axes
        The roles of its masks (e.g. ``("TYX",)``), sorted; empty while unknown.
    stated
        True when the roles are stated for the type rather than named by the files.
    without
        Analysed images without a mask of the type, sorted.
    """

    mask_type: str
    masks: int
    sources: tuple[str, ...]
    axes: tuple[str, ...]
    stated: bool
    without: tuple[str, ...]


@dataclass(frozen=True)
class MaskAxesQuestion:
    """A mask type whose axes the user states, and the statements that fit it.

    Attributes
    ----------
    mask_type
        The type's name.
    header_axes
        The axes its masks' headers give (length-1 axes other than Y and X left out),
        sorted, e.g. ``("IYX",)``.
    choices
        The statements (one role per header axis) that fit every mask of the type; empty
        when none does (masks of one type stored differently, or an axis named as a
        channel): the masks must then be saved again.
    stated
        The statement in use for the type (given or recorded), or None.
    shared
        True when its masks are 2-D masks of images with several planes: the only
        statement is ``YX``, one plane shared by every plane of the image.
    """

    mask_type: str
    header_axes: tuple[str, ...]
    choices: tuple[str, ...]
    stated: str | None
    shared: bool


def mask_type_rows(inventory: MaskInventory, images: Collection[str]) -> list[MaskTypeRow]:
    """Describe each mask type: its masks, their roles and the images without one.

    Parameters
    ----------
    inventory
        The masks found (:func:`.masks.find_masks`).
    images
        The analysed images, relative to the experiment folder.

    Returns
    -------
    list of MaskTypeRow
        One per mask type, by name.
    """
    rows = []
    for mask_type in inventory.types:
        masks = [f for f in inventory.files if f.mask_type == mask_type]
        have = {f.image for f in masks}
        rows.append(
            MaskTypeRow(
                mask_type=mask_type,
                masks=len(masks),
                sources=tuple(sorted({f.source for f in masks})),
                axes=tuple(sorted({f.axes for f in masks if f.axes})),
                stated=any(f.axes_from == "stated" for f in masks),
                without=tuple(sorted(image for image in images if image not in have)),
            )
        )
    return rows


def mask_axes_questions(
    inventory: MaskInventory,
    issues: Iterable[ValidationIssue],
    stated: Mapping[str, str],
) -> list[MaskAxesQuestion]:
    """Return the mask types whose axes need (or have) a statement, with the ones that fit.

    Parameters
    ----------
    inventory
        The masks found, paired with the statements in ``stated``.
    issues
        The masks' issues: the inventory's own before the axis roles are confirmed, then
        :func:`.mask_checks.mask_issues` (which adds ``mask_planes_mismatch``).
    stated
        The statements in use, ``{type: roles}`` (given in this session or recorded by the
        earlier run); their types are asked again, so a statement can be changed.

    Returns
    -------
    list of MaskAxesQuestion
        By mask type; empty when every mask names its axes and fits its image.
    """
    issues = list(issues)
    by_path = {f.path: f for f in inventory.files}
    unnamed = _types_of(issues, UNNAMED_CODES, by_path)
    flat = {
        by_path[path].mask_type
        for issue in issues
        if issue.code == SHARED_CODE
        for path in issue.paths
        if path in by_path and by_path[path].axes == "YX"
    }
    questions = []
    for mask_type in sorted((unnamed | flat | set(stated)) & set(inventory.types)):
        masks = [f for f in inventory.files if f.mask_type == mask_type and f.header_axes]
        headers = tuple(sorted({f.header_axes for f in masks}))
        roles = stated.get(mask_type)
        questions.append(
            MaskAxesQuestion(
                mask_type=mask_type,
                header_axes=headers,
                choices=_fitting(masks),
                stated=roles,
                shared=(mask_type in flat or roles == "YX") and mask_type not in unnamed,
            )
        )
    return questions


def _types_of(
    issues: Iterable[ValidationIssue], codes: Collection[str], by_path: Mapping
) -> set[str]:
    """Return the mask types of the masks named by the issues with these codes."""
    return {
        by_path[path].mask_type
        for issue in issues
        if issue.code in codes
        for path in issue.paths
        if path in by_path
    }


def _fitting(masks: list) -> tuple[str, ...]:
    """Return the statements that fit every one of the masks, those ending in YX first."""
    lengths = {len(f.header_axes) for f in masks}
    if len(lengths) != 1:
        return ()  # one statement covers the whole type: masks of other ranks cannot share it
    candidates = _statements(lengths.pop())
    return tuple(
        roles
        for roles in candidates
        if all(mask_roles(f.header_axes, f.named_axes, roles)[1] is None for f in masks)
    )


def _statements(count: int) -> list[str]:
    """Return every statement for ``count`` axes: Y and X once, Z and T at most once each."""
    found = set()
    for extra in ("", "Z", "T", "ZT"):
        if len(extra) + 2 == count:
            found.update("".join(order) for order in permutations("YX" + extra))
    usable = [roles for roles in found if roles.index("Y") < roles.index("X")]
    return sorted(usable, key=lambda roles: (not roles.endswith("YX"), roles))
