"""The folder check's pixel-type and dimension checks, applied per unit of a described run.

An undescribed (v0.4) run compares the pixel type and the arrangement of dimensions of every
image of the experiment (``mixed_dtypes``, ``inconsistent_dimensions``, ``missing_axes``;
:func:`.metadata.check_metadata`). A description declares an ``image_type`` per unit, so an
experiment may hold RGB colour units next to fluorescence units. Comparing those across the
whole experiment gives folder errors that mislead: the Spec refuses such an experiment in
Stages 1-2 with ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` (experiment-v1 sections 4.4b and 8),
and that refusal is not lifted here. In a described run the same checks apply:

- on the notebook's folder check, inside each unit (its image type is not declared yet), so a
  unit mixed inside itself is refused and units of different kinds do not hide the screens
  (:func:`unit_checked`);
- on the run and on *Check and save*, the pixel types of the units that declare the same
  ``image_type`` together (:func:`declared_type_issues`); the arrangement of dimensions is
  the axis review's (``inconsistent_mapping``).

An experiment whose units all declare one image type gets the same pixel-type refusal as before.
The invariant is that a previously blocking input stays blocked; the diagnostic codes may change
(the per-type checks and the Spec's ``R_*`` codes replace the global ``mixed_dtypes`` and
``inconsistent_dimensions``).
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import replace
from typing import TYPE_CHECKING

from .description import Description
from .image_consistency import dtype_issues
from .metadata import TiffMetadata, check_metadata
from .physical_check import declared_units, discovered_units
from .validation import ValidationIssue

if TYPE_CHECKING:
    from .run.preflight import Preflight

WHOLE_EXPERIMENT_CODES = frozenset(
    {
        "mixed_dtypes",
        "inconsistent_dimensions",
        "missing_axes",
        "different_channel_counts",
        "channel_names_differ",
    }
)
"""The codes :func:`.metadata.check_metadata` raises by comparing every image of the experiment.

The channel codes are included: a description replaces them with its per-unit checks, which the
notebook's folder check cannot run before the description exists."""

UNDECLARED = "undeclared"
"""The group of images in a folder the description does not declare as a unit."""


def unit_checked(pre: Preflight) -> Preflight:
    """Return the folder check with its whole-experiment comparisons made inside each unit.

    Used by the notebook's described run, where the folders are checked before any image type
    is declared: each unit (a leaf folder with its condition and repeat) is checked inside
    itself, so a unit mixed inside itself is refused as before and units of different kinds do
    not stop the screens. The units are compared with each other once their image types are
    declared (:func:`declared_type_issues`, the axis review, the physical checks).

    Parameters
    ----------
    pre
        The folder check of the experiment.

    Returns
    -------
    Preflight
        ``pre`` with ``mixed_dtypes``, ``inconsistent_dimensions``, ``missing_axes``,
        ``different_channel_counts`` and ``channel_names_differ`` found per unit.
    """
    kept = [i for i in pre.metadata.issues if i.code not in WHOLE_EXPERIMENT_CODES]
    folders = {
        path: key
        for key, paths in discovered_units(pre.discovery, pre.excluded).items()
        for path in paths
    }
    members: dict[Hashable, list[TiffMetadata]] = {}
    for f in pre.metadata.files:
        members.setdefault(folders.get(f.relative_path), []).append(f)
    per_unit = [
        issue
        for key in sorted(members, key=repr)
        for issue in check_metadata(members[key])
        if issue.code in WHOLE_EXPERIMENT_CODES
    ]
    return replace(pre, metadata=replace(pre.metadata, issues=(*kept, *per_unit)))


def _type_groups(description: Description, pre: Preflight) -> dict[str, Hashable]:
    """Return each image's declared image type; ``UNDECLARED`` for a folder not declared."""
    declared = declared_units(description)
    groups: dict[str, Hashable] = {}
    for key, paths in discovered_units(pre.discovery, pre.excluded).items():
        entry = declared.get(key)
        for path in paths:
            groups[path] = UNDECLARED if entry is None else entry[1].image_type
    return groups


def declared_type_issues(description: Description, pre: Preflight) -> list[ValidationIssue]:
    """Check pixel types among the units that declare the same image type.

    Parameters
    ----------
    description
        The description the strict reader accepted.
    pre
        The run's folder check (its image headers and folder walk).

    Returns
    -------
    list of ValidationIssue
        ``mixed_dtypes`` as the undescribed folder check finds it, with the RGB units compared
        only with each other and the other units only with each other. The arrangement of
        dimensions is not examined here: the axis review checks it against the confirmed
        mapping, which replaces the folder check's ``inconsistent_dimensions``.
    """
    groups = _type_groups(description, pre)
    members: dict[Hashable, list[TiffMetadata]] = {}
    for f in pre.metadata.files:
        if f.readable:
            members.setdefault(groups.get(f.relative_path), []).append(f)
    return [issue for key in sorted(members, key=repr) for issue in dtype_issues(members[key])]
