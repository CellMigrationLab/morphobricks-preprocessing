"""The axis check: confirmed roles against the axes an image's header names (contract §2).

Every TIFF's confirmed roles are compared, position by position, with the axes its metadata
names (:func:`.metadata.named_axes`). Roles that swap the named Y and X, or give a named
axis another role, are errors: such an image is never read, not transposed and not under a
contradicted declaration. An axis the metadata does not name (a plain stack) takes the
role the user confirmed, with a warning, and is recorded as ``axes_from: "confirmed"``.
:func:`.axes.review_axes` runs this check for every file.

Nothing here reads pixels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .axis_names import OPEN_AXIS
from .metadata import TiffMetadata, named_axes
from .validation import ValidationIssue, error, plural, warning

if TYPE_CHECKING:  # the mapping class lives in .axes, which imports this module
    from .axes import AxisMapping

POSITION_AXES = frozenset("RMKJ")
"""``tifffile`` letters for positions in a sample (tile, mosaic, row, column): they identify
where an image was taken, not a dimension of one image, so they cannot be given a role."""

AXES_NOT_NAMED = "axes_not_named"
"""A warning: a TIFF whose metadata does not name an axis longer than 1 (a plain stack). It
is read with the roles the user confirmed, recorded per file as ``axes_from: "confirmed"``."""

AXES_YX_SWAPPED = "axes_yx_swapped"
"""An error: confirmed roles that give the header's named Y and X the other way round."""

AXES_CONTRADICT_HEADER = "axes_contradict_header"
"""An error: confirmed roles that give a named axis another role than the header's."""

AXIS_ERROR_CODES = (AXES_YX_SWAPPED, AXES_CONTRADICT_HEADER)
"""The errors of an image whose confirmed roles disagree with the axes its header names
(contract §2). Such an image is never read, not transposed and not under a contradicted
declaration: the user corrects the roles, or saves the image as a named-axis copy."""

AXIS_CHECK_CODES = (AXES_NOT_NAMED, *AXIS_ERROR_CODES)
"""Every code of the axis check, shown together as one card."""

AXES_FROM_HEADER = "header"
"""Every axis of the image is named in its metadata (or OME-NGFF), and the roles agree."""

AXES_FROM_CONFIRMED = "confirmed"
"""Some axis of the image is named only by the user's confirmation (a plain stack)."""


def header_axis_issue(meta: TiffMetadata, mapping: AxisMapping) -> str | None:
    """Compare an image's confirmed roles with the axes its header names (contract §2).

    The one rule of the axis check: a TIFF is read only when the confirmed roles agree with
    the axes its metadata names; an axis it does not name takes the role the user confirmed,
    with a warning. Position by position (:func:`.metadata.named_axes`):

    - an axis the metadata leaves open (a placeholder ``I``/``Q``, and the ``Y`` and ``X``
      of a plain stack of pages) with more than one plane: ``axes_not_named``, a warning
      (the file is read with the confirmed role, recorded as ``axes_from: "confirmed"``);
      an open axis of length 1 is no problem;
    - a named axis with its own letter as role, the RGB samples ``S`` as C when the header
      has no C, and a length-1 axis without a role agree;
    - the named Y given role X and the named X role Y: ``axes_yx_swapped``;
    - any other role for a named axis: ``axes_contradict_header``. That includes a Z read
      as T, and a named axis that is not a role (angle ``A``, lambda ``E``, ...) given one.
      A named axis longer than 1 without a role is :func:`check_mapping`'s
      ``unassigned_axis``, and a position axis (``R``, ``M``, ``J``, ``K``) its
      ``unsupported_axis``, so they are left to those.

    OME-Zarr images name their axes in their OME-NGFF metadata, which gives their roles
    (:func:`declared_mapping`): they never have one of these problems.

    Parameters
    ----------
    meta
        The image's header.
    mapping
        The image's own mapping (:attr:`AxisReview.file_mappings`, :func:`file_mapping_for`).

    Returns
    -------
    str or None
        ``"axes_contradict_header"``, ``"axes_yx_swapped"`` or ``"axes_not_named"`` (the
        first of these when several apply), or None when the roles agree with the header.
    """
    found = set(axis_disagreements(meta, mapping).values())
    # Errors first (a contradiction is more than a swap); then the plain stack's warning.
    for code in (AXES_CONTRADICT_HEADER, AXES_YX_SWAPPED, AXES_NOT_NAMED):
        if code in found:
            return code
    return None


def axis_disagreements(meta: TiffMetadata, mapping: AxisMapping) -> dict[int, str]:
    """Return, for each dimension whose role disagrees with the header, the axis error code.

    The position-by-position rule of :func:`header_axis_issue`, for showing which axis is
    wrong (the notebook marks its role dropdown before the roles are confirmed).

    Parameters
    ----------
    meta
        The image's header.
    mapping
        The image's own mapping.

    Returns
    -------
    dict
        ``{dimension: code}``, with ``"axes_not_named"``, ``"axes_yx_swapped"`` or
        ``"axes_contradict_header"``; empty for an OME-Zarr image, an unreadable one, or
        roles that agree with the header.
    """
    if not meta.readable or meta.is_ome_zarr or len(mapping.roles) != len(meta.axes):
        return {}
    found: dict[int, str] = {}
    named = named_axes(meta)
    for index, (letter, role, length) in enumerate(
        zip(named, mapping.roles, meta.shape, strict=True)
    ):
        if letter == OPEN_AXIS:
            if length > 1:
                found[index] = AXES_NOT_NAMED
            continue
        if role == letter or (letter == "S" and role == "C" and "C" not in meta.axes):
            continue
        if role is None or letter in POSITION_AXES:
            continue
        found[index] = (
            AXES_YX_SWAPPED if letter in "YX" and role in "YX" else AXES_CONTRADICT_HEADER
        )
    return found


def axis_check_issues(
    found: Mapping[str, list[tuple[TiffMetadata, AxisMapping]]],
) -> list[ValidationIssue]:
    """Build one issue per axis-check code, naming every file with its header and roles.

    The errors name the likely cause for each arrangement (:func:`likely_axis_cause`) and the
    two fixes: correct the roles, or save named-axis copies with them.

    Parameters
    ----------
    found
        The files with each code, as ``(header, own mapping)`` pairs.

    Returns
    -------
    list of ValidationIssue
        The ``axes_not_named`` warning and the two errors, in :data:`AXIS_CHECK_CODES` order.
    """
    issues = []
    for code in AXIS_CHECK_CODES:
        files = found.get(code)
        if not files:
            continue
        groups: dict[tuple[str, str], list[tuple[TiffMetadata, AxisMapping]]] = {}
        for meta, mapping in files:
            groups.setdefault((meta.axes, mapping.roles_string), []).append((meta, mapping))
        parts = []
        for (axes, roles), members in sorted(groups.items()):
            text = (
                f"header {axes}, confirmed {roles}: {_some([m.relative_path for m, _ in members])}"
            )
            if code != AXES_NOT_NAMED:
                text += f"; likely cause: {likely_axis_cause(*members[0], code)}"
            parts.append(text)
        details = "; ".join(parts) if code == AXES_NOT_NAMED else " | ".join(parts)
        n = plural(len(files), "image")
        paths = sorted(meta.relative_path for meta, _ in files)
        if code == AXES_NOT_NAMED:
            issues.append(
                warning(
                    code,
                    f"{n} do not name their axes in their metadata ({details}). They are read "
                    "with the roles you confirmed, and recorded as confirmed by you.",
                    paths,
                    "Check the roles with the preview. Before segmenting these images in "
                    "other tools, consider saving named-axis copies (OME-TIFF), so every tool "
                    "reads the same axes.",
                )
            )
            continue
        if code == AXES_YX_SWAPPED:
            message = (
                f"The axes confirmed for {n} swap the Y and X their header names ({details}). "
                "They are not read transposed."
            )
        else:
            message = (
                f"The axes confirmed for {n} contradict their header ({details}). They are "
                "not read under a contradicted declaration."
            )
        issues.append(error(code, message, paths, _TWO_FIXES))
    return issues


def likely_axis_cause(meta: TiffMetadata, mapping: AxisMapping, code: str) -> str:
    """Name the likely cause of a header the confirmed roles contradict, in plain words.

    Most contradictions are headers written by the saving tool, not user mistakes: tifffile
    labels any 3-D array saved with ``imagej=True`` or ``ome=True`` ``CYX``, so a depth stack
    or time-lapse saved that way reads as channels; an ImageJ stack of slices reads as depth
    even for a time-lapse.

    Parameters
    ----------
    meta
        The image's header.
    mapping
        Its confirmed mapping.
    code
        ``"axes_yx_swapped"`` or ``"axes_contradict_header"``.

    Returns
    -------
    str
        The cause, as a phrase that follows "likely cause:".
    """
    pairs = {
        (letter, role)
        for letter, role in zip(named_axes(meta), mapping.roles, strict=True)
        if role is not None and letter not in (role, OPEN_AXIS)
    }
    if code == AXES_YX_SWAPPED or pairs <= {("Y", "X"), ("X", "Y")}:
        return (
            "the image was saved with its rows and columns exchanged (for example a "
            "transposed array), or the confirmed X and Y are the wrong way round"
        )
    if any(letter == "C" and role in ("Z", "T") for letter, role in pairs):
        return (
            "tifffile labels a 3-D stack saved with imagej=True or ome=True as CYX (channels) "
            "by default, so a depth stack or time-lapse saved that way reads as channels"
        )
    if ("Z", "T") in pairs:
        return "an ImageJ stack of slices reads as depth (Z) even when it is a time-lapse"
    return "the tool that saved the file wrote a default or wrong axis order in its metadata"


_TWO_FIXES = (
    "Two fixes. Correct the axes: if the header is right, choose its roles. Or convert: if "
    "your roles are right, save these images as named-axis copies (OME-TIFF) with them, "
    "then run preprocessing on the copies."
)


def _some(paths: Iterable[str], shown: int = 3) -> str:
    """Name up to ``shown`` paths, sorted, then how many more there are.

    Parameters
    ----------
    paths
        The paths.
    shown
        How many to name.

    Returns
    -------
    str
        For example ``"a.tif, b.tif, c.tif and 2 more"``.
    """
    paths = sorted(paths)
    text = ", ".join(paths[:shown])
    return text if len(paths) <= shown else f"{text} and {len(paths) - shown} more"


def check_header_axes(
    files: Iterable[tuple[TiffMetadata, AxisMapping]],
    analysed: Mapping[str, AxisMapping],
    confirmed: frozenset[str] | None = None,
) -> tuple[list[ValidationIssue], dict[str, str]]:
    """Run the axis check on every file and say where each analysed file's roles come from.

    Parameters
    ----------
    files
        Each file's header with its own mapping (files the mapping does not fit left out).
    analysed
        The files that are read (:attr:`.axes.AxisReview.file_mappings`); only these get an
        ``axes_from`` entry.
    confirmed
        The files the user's confirmation covers (:class:`ConfirmedAxes`), or None for every
        file given. A plain stack outside them is the error ``plain_stack_not_confirmed``.

    Returns
    -------
    tuple
        The axis-check issues (:func:`axis_check_issues`), and ``{path: axes_from}`` with
        ``"header"`` or ``"confirmed"``.
    """
    found: dict[str, list[tuple[TiffMetadata, AxisMapping]]] = {}
    axes_from: dict[str, str] = {}
    for meta, mapping in files:
        code = header_axis_issue(meta, mapping)
        if code is not None:
            found.setdefault(code, []).append((meta, mapping))
        if meta.relative_path in analysed:
            unnamed = AXES_NOT_NAMED in axis_disagreements(meta, mapping).values()
            axes_from[meta.relative_path] = AXES_FROM_CONFIRMED if unnamed else AXES_FROM_HEADER
    unconfirmed = [
        path
        for path, how in sorted(axes_from.items())
        if how == AXES_FROM_CONFIRMED and confirmed is not None and path not in confirmed
    ]
    return [*axis_check_issues(found), *_unconfirmed_issues(unconfirmed)], axes_from


@dataclass(frozen=True)
class ConfirmedAxes:
    """The user's confirmation of an axis mapping, for the files it was given for.

    A plain stack names no axes, so its roles are only the user's statement about the files
    the user looked at (audit 2 B-1): a plain stack added or changed later is not covered,
    and must be confirmed again, never read with roles confirmed for other files.

    Attributes
    ----------
    mapping
        The experiment mapping confirmed.
    files
        The images it was confirmed for, relative to the experiment root.
    """

    mapping: AxisMapping
    files: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", frozenset(self.files))


def confirmation_of(
    mapping: AxisMapping | ConfirmedAxes,
) -> tuple[AxisMapping, frozenset[str] | None]:
    """Split a confirmation into its mapping and the files it covers (None for every file).

    Parameters
    ----------
    mapping
        An :class:`.axes.AxisMapping`, confirmed for every file given, or a
        :class:`ConfirmedAxes`.

    Returns
    -------
    tuple
        ``(mapping, files)``.
    """
    if isinstance(mapping, ConfirmedAxes):
        return mapping.mapping, mapping.files
    return mapping, None


def _unconfirmed_issues(paths: list[str]) -> list[ValidationIssue]:
    """Return the error naming plain stacks the user's confirmation does not cover."""
    if not paths:
        return []
    return [
        error(
            "plain_stack_not_confirmed",
            f"{plural(len(paths), 'image')} do not name their axes, and the roles were not "
            f"confirmed for them ({_some(paths)}): they are new or changed since the roles "
            "were confirmed. A plain stack's roles cover only the files they were confirmed "
            "for, so they are not read with roles confirmed for other images.",
            paths,
            "Check their roles with the preview and confirm them again (the CLI: give --axes).",
        )
    ]
