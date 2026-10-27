"""Dataset consistency: the balance of an experiment and how alike its images are.

The folder check (pass 1) gathers every consistency warning in one place
(``docs/contract.md`` §11, normalization plan N37). The image headers are
compared in :mod:`.metadata` (pixel types, axes, sizes, plane counts, channel
names, pixel sizes); this module looks at the folders: how many images each
condition and biological repeat has, and whether every biological repeat is
found in every condition. Biological repeats are matched across conditions by
their repeat group, by default their folder name. Warnings never block
processing. Nothing here reads pixels or image headers.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from morphobricks_normalization.records import SINGLE

from .discovery import ROOT, DiscoveryResult
from .validation import ValidationIssue, plural, warning

CONSISTENCY_CODES = (
    "mixed_dtypes",
    "inconsistent_dimensions",
    "missing_axes",
    "inconsistent_mapping",
    "different_sizes",
    "different_plane_counts",
    "channel_names_differ",
    "different_pixel_sizes",
    "missing_pixel_size",
    "unbalanced_images",
    "repeat_missing_conditions",
)
"""Codes of the issues shown together as the dataset consistency of an experiment."""

# A condition and biological repeat is unbalanced when its number of images is
# below LOW or above HIGH times the median of all of them (N37).
LOW = 0.5
HIGH = 2.0


@dataclass(frozen=True)
class BiologicalRepeat:
    """One biological-repeat folder of a condition, with its technical repeats merged.

    Attributes
    ----------
    condition, biological_repeat
        Labels from the folder names (``single`` when the level is missing).
    folder
        The biological-repeat folder relative to the experiment root (the
        condition folder, or ``"."``, when the levels are missing).
    image_count
        Images to analyse in it, over all its technical repeats.
    technical_repeat_count
        Technical-repeat folders in it; 0 when its images are directly in the
        biological-repeat folder.
    """

    condition: str
    biological_repeat: str
    folder: str
    image_count: int
    technical_repeat_count: int

    @property
    def key(self) -> tuple[str, str]:
        """``(condition, biological_repeat)``, the folder's labels."""
        return (self.condition, self.biological_repeat)


@dataclass(frozen=True)
class BalanceCell:
    """The images of one condition in one repeat group.

    Attributes
    ----------
    image_count
        Images, over all technical repeats (and all folders, if a repeat group
        holds more than one folder of the condition).
    technical_repeat_count
        Technical-repeat folders; 0 when the images are directly in the
        biological-repeat folder.
    folders
        The biological-repeat folders in the cell.
    unbalanced
        True when the image count is below half or above twice the median of
        all cells with images.
    """

    image_count: int
    technical_repeat_count: int
    folders: tuple[str, ...]
    unbalanced: bool = False


@dataclass(frozen=True)
class BalanceTable:
    """Images per condition (columns) and repeat group (rows).

    Attributes
    ----------
    conditions
        Condition labels, sorted.
    repeat_groups
        Repeat-group labels, sorted.
    cells
        ``{(repeat_group, condition): BalanceCell}``; a combination without
        images has no cell.
    median
        Median image count of the cells with images; None without images.
    """

    conditions: tuple[str, ...] = ()
    repeat_groups: tuple[str, ...] = ()
    cells: Mapping[tuple[str, str], BalanceCell] = field(
        default_factory=lambda: MappingProxyType({})
    )
    median: float | None = None

    def cell(self, repeat_group: str, condition: str) -> BalanceCell | None:
        """Return the cell of a repeat group in a condition, or None if it has no images."""
        return self.cells.get((repeat_group, condition))

    def missing_conditions(self, repeat_group: str) -> tuple[str, ...]:
        """Return the conditions in which a repeat group has no folder."""
        return tuple(c for c in self.conditions if (repeat_group, c) not in self.cells)


@dataclass(frozen=True)
class DatasetReport:
    """The balance of an experiment and the warnings about it.

    Attributes
    ----------
    repeats
        Every biological-repeat folder, sorted by condition and repeat.
    balance
        Images per condition and repeat group.
    issues
        ``unbalanced_images`` and ``repeat_missing_conditions`` warnings.
    """

    repeats: tuple[BiologicalRepeat, ...] = ()
    balance: BalanceTable = field(default_factory=BalanceTable)
    issues: tuple[ValidationIssue, ...] = ()


def biological_repeats(
    discovery: DiscoveryResult, excluded: Iterable[str] = ()
) -> list[BiologicalRepeat]:
    """List the biological-repeat folders of an experiment, technical repeats merged.

    Parameters
    ----------
    discovery
        The folder structure.
    excluded
        Images left out by the user; they are not counted.

    Returns
    -------
    list of BiologicalRepeat
        Sorted by condition, then biological repeat; folders without images
        to analyse are left out.
    """
    excluded = set(excluded)
    found: dict[tuple[str, str], list] = {}
    for leaf in discovery.leaves:
        entry = found.setdefault(
            (leaf.condition, leaf.biological_repeat), [_repeat_folder(leaf), 0, 0]
        )
        entry[1] += sum(image not in excluded for image in leaf.images)
        entry[2] += leaf.technical_repeat != SINGLE
    # A folder whose images are all excluded is not analysed, so it is not listed.
    return [
        BiologicalRepeat(condition, repeat, folder, images, technical)
        for (condition, repeat), (folder, images, technical) in sorted(found.items())
        if images
    ]


def default_repeat_groups(repeats: Iterable[BiologicalRepeat]) -> dict[tuple[str, str], str]:
    """Pair biological repeats with the same folder name across conditions.

    Returns
    -------
    dict
        ``{(condition, biological_repeat): repeat_group}``, where every repeat
        group is named after the folder.
    """
    return {repeat.key: repeat.biological_repeat for repeat in repeats}


def balance_table(
    repeats: Iterable[BiologicalRepeat], repeat_groups: Mapping[tuple[str, str], str] | None = None
) -> BalanceTable:
    """Count images per condition and repeat group, and mark unbalanced cells.

    Parameters
    ----------
    repeats
        The biological-repeat folders (:func:`biological_repeats`).
    repeat_groups
        ``{(condition, biological_repeat): repeat_group}``; folders not in it,
        or every folder when None, form groups named after the folder.
    """
    repeats = list(repeats)
    groups = default_repeat_groups(repeats) | dict(repeat_groups or {})
    merged: dict[tuple[str, str], list] = {}
    for repeat in repeats:
        cell = merged.setdefault((groups[repeat.key], repeat.condition), [0, 0, []])
        cell[0] += repeat.image_count
        cell[1] += repeat.technical_repeat_count
        cell[2].append(repeat.folder)
    counts = [images for images, _, _ in merged.values() if images]
    median = statistics.median(counts) if counts else None
    cells = {
        key: BalanceCell(
            images,
            technical,
            tuple(folders),
            unbalanced=bool(images) and median is not None and _unbalanced(images, median),
        )
        for key, (images, technical, folders) in sorted(merged.items())
    }
    return BalanceTable(
        conditions=tuple(sorted({r.condition for r in repeats})),
        repeat_groups=tuple(sorted({group for group, _ in cells})),
        cells=MappingProxyType(cells),
        median=median,
    )


def check_dataset(
    discovery: DiscoveryResult,
    repeat_groups: Mapping[tuple[str, str], str] | None = None,
    *,
    excluded: Iterable[str] = (),
) -> DatasetReport:
    """Check the balance of an experiment from its folders.

    Parameters
    ----------
    discovery
        The folder structure.
    repeat_groups
        ``{(condition, biological_repeat): repeat_group}`` pairing biological
        repeats across conditions; by default repeats with the same folder name
        are one group.
    excluded
        Images left out by the user; they are not counted.

    Returns
    -------
    DatasetReport
        With the warnings ``unbalanced_images`` (a condition and repeat group
        with fewer than half or more than twice the median number of images)
        and ``repeat_missing_conditions`` (a repeat group not found in every
        condition).
    """
    repeats = biological_repeats(discovery, excluded)
    balance = balance_table(repeats, repeat_groups)
    issues = [*_unbalanced_issues(balance), *_missing_issues(balance)]
    return DatasetReport(tuple(repeats), balance, tuple(issues))


def _repeat_folder(leaf) -> str:
    if leaf.condition == SINGLE:
        return ROOT
    parts = leaf.relative_path.split("/")
    return "/".join(parts[: 1 if leaf.biological_repeat == SINGLE else 2])


def _unbalanced(images: int, median: float) -> bool:
    return images < LOW * median or images > HIGH * median


def _unbalanced_issues(balance: BalanceTable) -> list[ValidationIssue]:
    flagged = [(key, cell) for key, cell in balance.cells.items() if cell.unbalanced]
    if not flagged:
        return []
    described = "; ".join(
        f"{condition} / {group}: {plural(cell.image_count, 'image')}"
        for (group, condition), cell in flagged
    )
    return [
        warning(
            "unbalanced_images",
            "The number of images differs strongly between conditions and biological "
            f"repeats: the median is {plural(_whole(balance.median), 'image')}, but "
            f"{described}. Groups with more images weigh more when their pixels are "
            "pooled.",
            [folder for _, cell in flagged for folder in cell.folders],
            "No action needed if this is expected. Otherwise check whether images are "
            "missing, misplaced or duplicated in these folders.",
        )
    ]


def _missing_issues(balance: BalanceTable) -> list[ValidationIssue]:
    if len(balance.conditions) < 2:
        return []
    missing = {
        group: balance.missing_conditions(group)
        for group in balance.repeat_groups
        if balance.missing_conditions(group)
    }
    if not missing:
        return []
    described = "; ".join(
        f"{group} (not in {', '.join(conditions)})" for group, conditions in missing.items()
    )
    return [
        warning(
            "repeat_missing_conditions",
            f"{plural(len(missing), 'biological repeat')} "
            f"{'is' if len(missing) == 1 else 'are'} not found in every condition: "
            f"{described}. "
            f"{'It is' if len(missing) == 1 else 'They are'} still used, with the "
            "conditions that have them.",
            [
                folder
                for (group, _), cell in balance.cells.items()
                if group in missing
                for folder in cell.folders
            ],
            "No action needed if a repeat was not acquired for every condition. If the "
            "same repeat has a different folder name in another condition, pair the "
            "folders in the experiment design (step 1 of the notebook, or the "
            "repeat_group column of experiment_design.csv).",
        )
    ]


def _whole(value: float | None) -> int | float:
    return int(value) if value is not None and float(value).is_integer() else value or 0
