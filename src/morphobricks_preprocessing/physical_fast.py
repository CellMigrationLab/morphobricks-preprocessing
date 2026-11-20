"""Fast-mode checks of the images of one unit that follows the experiment list (ADR 0006).

Every image of a unit with ``channels_from = "experiment"`` is compared with ``channel_layout``
from its header (:mod:`.fast_layout`), not with the strict count, header and type codes;
``R_UNIT_NOT_HOMOGENEOUS`` stays for the interpreted axes other than the channel axis, and
``R_RGB_OVERRIDE_NOT_THREE_SAMPLES`` keeps its priority (experiment-v1 section 8).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .description import Description, Unit
from .fast_layout import FAST_LAYOUT_CONTRADICTION, channel_evidence, contradictions, image_record
from .image_names import position_id
from .metadata import TiffMetadata
from .physical_unit import (
    UNIT_NOT_HOMOGENEOUS,
    Layout,
    PhysicalIssue,
    UnitKey,
    type_issue,
    unit_images,
    unnamed_positions,
)
from .rgb_override import header_overridden


def fov_key(key: UnitKey, path: str) -> dict[str, str]:
    """Return the FOV key of an image (experiment-v1 3.4): its folder labels and position ID."""
    return {
        "condition": key.condition,
        "biological_repeat": key.biological_repeat,
        "technical_repeat": key.technical_repeat,
        "position_id": position_id(path),
    }


def _spatial(layout: Layout) -> str:
    """Return a layout's interpreted axes without the channel axis (``C``, or RGB ``S``)."""
    return layout.axes.replace("C", "").replace("S", "")


def _axes_issues(key: UnitKey, layouts: Mapping[str, Layout]) -> list[PhysicalIssue]:
    """Return ``R_UNIT_NOT_HOMOGENEOUS`` for interpreted axes only (a fast unit, ADR 0006).

    The channel count, image type and header names of a fast unit are compared with the
    experiment list instead (``R_FAST_LAYOUT_CONTRADICTION``), so the channel axis (``C``, or
    the samples ``S`` of an RGB image) is left out here: an RGB image in a channel unit is an
    ``image_type`` contradiction, not also an axes one. The other axes stay a homogeneity rule;
    the unit's axes are the most frequent ones (the first in path order on a tie).
    """
    counts = Counter(_spatial(layout) for layout in layouts.values())
    top = max(counts.values())
    reference = next(_spatial(lay) for lay in layouts.values() if counts[_spatial(lay)] == top)
    return [
        PhysicalIssue(
            UNIT_NOT_HOMOGENEOUS,
            key,
            path,
            position_id(path),
            None,
            f"axes {layout.axes!r} (without the channel axis {_spatial(layout)!r}), "
            f"the unit has {reference!r}",
        )
        for path, layout in layouts.items()
        if _spatial(layout) != reference
    ]


def _fast_issue(
    key: UnitKey,
    unit: Unit,
    description: Description,
    path: str,
    layout: Layout,
    meta: TiffMetadata,
) -> PhysicalIssue | None:
    """Return the fast-mode contradiction of one image, with every kind found, or None."""
    typed = type_issue(unit, layout, meta)
    found = contradictions(
        description.channel_layout,
        description,
        count=layout.channels,
        names=layout.names,
        type_found=None if typed is None else typed[1],
    )
    if not found:
        return None
    detail = "; ".join(item.text() for item in found)
    return PhysicalIssue(
        FAST_LAYOUT_CONTRADICTION, key, path, position_id(path), None, detail, found
    )


def _check_fast_unit(
    key: UnitKey,
    unit: Unit,
    description: Description,
    layouts: Mapping[str, Layout],
    metas: Mapping[str, TiffMetadata],
) -> tuple[list[PhysicalIssue], tuple[dict, ...]]:
    """Check every image of a unit that follows the experiment list (ADR 0006, FC3/FC4).

    ``layouts`` are the effective layouts of the images that are not refused by
    ``R_RGB_OVERRIDE_NOT_THREE_SAMPLES`` (which keeps its priority and is not listed twice).
    Returns the axes and fast-mode issues, and for a unit without them the record of each image
    whose channel count is known: an image whose count is not stated is listed unchecked by the
    caller and gets no record, so no per-index statement implies a count nobody checked.
    """
    issues = _axes_issues(key, layouts)
    for path, layout in layouts.items():
        found = _fast_issue(key, unit, description, path, layout, metas[path])
        if found is not None:
            issues.append(found)
    if issues:
        return issues, ()
    statement = description.channel_layout
    records = tuple(
        image_record(path, fov_key(key, path), channel_evidence(statement, layout.names))
        for path, layout in sorted(layouts.items())
        if layout.channels is not None
    )
    return [], records


def check_fast_unit(
    key: UnitKey,
    unit: Unit,
    description: Description,
    paths: Sequence[str],
    metas: Mapping[str, TiffMetadata],
    roles: Mapping[str, Sequence[str | None]],
) -> tuple[list[PhysicalIssue], bool, list[str], dict[str, tuple[int, ...]], tuple[dict, ...]]:
    """Check one unit with ``channels_from = "experiment"`` against the experiment list.

    Returns the issues (``R_RGB_OVERRIDE_NOT_THREE_SAMPLES`` first, then the axes and the
    fast-mode contradictions of the other images), verified, the unchecked images (also every
    image whose channel count is not stated: never a guessed count), the unnamed images and the
    per-image records of a unit without issues.
    """
    found = unit_images(key, unit, paths, metas, roles)
    issues, records = (
        _check_fast_unit(key, unit, description, found.layouts, metas)
        if found.layouts
        else ([], ())
    )
    unchecked = found.unchecked + [p for p, lay in found.layouts.items() if lay.channels is None]
    issues = found.samples + issues
    if issues:
        return issues, found.verified, unchecked, {}, ()
    verified = found.verified and header_overridden(unit, found.layouts, metas) is None
    return issues, verified, unchecked, unnamed_positions(unit, found.layouts), records
