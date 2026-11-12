"""One line about the pixel size of an experiment, and which imaging units differ.

Owner requirement (10 November 2026, item (f)): by default one pixel size for the whole
experiment, said in one line; when the images do not agree, a line that says how many images
have which size and where, never a silent choice. The sizes are the ones each image is
recorded with (:attr:`.calibration.CalibrationReview.sizes`): its header's, or a size stated in
``calibration.csv``. Nothing is computed, converted or guessed here; the texts only group what
is recorded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .recorded_sizes import HEADER, PIXELS, STATED, RecordedSize
from .validation import plural

UNIT_SYMBOLS = {"micrometer": "µm", "nanometer": "nm", "millimeter": "mm", "meter": "m"}
NAMED_FOLDERS = 2
"""Folders named for a group of images that differs; the others are counted."""


@dataclass(frozen=True)
class SizeGroup:
    """The images recorded with one X and Y size.

    Attributes
    ----------
    x, y
        The recorded sizes (value, unit and source).
    images
        The images with exactly these, sorted.
    """

    x: RecordedSize
    y: RecordedSize
    images: tuple[str, ...]

    @property
    def calibrated(self) -> bool:
        """True when both X and Y have a value."""
        return self.x.value is not None and self.y.value is not None

    @property
    def sources(self) -> frozenset[str | None]:
        """The sources of the X and Y sizes."""
        return frozenset((self.x.source, self.y.source))


def xy_groups(sizes: Mapping[str, Sequence[RecordedSize]]) -> list[SizeGroup]:
    """Group the images by the X and Y size they are recorded with, the largest group first."""
    groups: dict[tuple, list[str]] = {}
    found: dict[tuple, tuple[RecordedSize, RecordedSize]] = {}
    for path in sorted(sizes):
        by_role = {size.role: size for size in sizes[path]}
        x, y = by_role.get("X", RecordedSize("X")), by_role.get("Y", RecordedSize("Y"))
        key = (x.value, x.unit, x.source, y.value, y.unit, y.source)
        groups.setdefault(key, []).append(path)
        found[key] = (x, y)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[1][0]))
    return [SizeGroup(*found[key], tuple(paths)) for key, paths in ordered]


def _one(size: RecordedSize) -> str:
    """Return one size as written: ``0.325 µm``, ``pixels`` or ``none``."""
    if size.source == PIXELS:
        return "pixels"
    if size.value is None:
        return "none"
    unit = UNIT_SYMBOLS.get(size.unit or "", size.unit or "no unit")
    return f"{size.value:g} {unit}"


def size_words(group: SizeGroup) -> str:
    """Return a group's X and Y size: ``0.325 × 0.325 µm``, ``pixels`` or ``none``."""
    x, y = _one(group.x), _one(group.y)
    if x == y and not group.calibrated:
        return x
    if group.calibrated and group.x.unit == group.y.unit:
        return f"{group.x.value:g} × {y}"  # X × Y, the unit once
    return f"X {x}, Y {y}"


def source_words(group: SizeGroup) -> str:
    """Return where a group's sizes come from, in words."""
    sources = group.sources
    if sources == {HEADER}:
        return "from the headers"
    if sources == {STATED}:
        return "stated by you"
    if sources == {PIXELS}:
        return "stated by you as deliberately uncalibrated"
    if sources == {None}:
        return "no size in the headers and none stated: uncalibrated"
    names = {HEADER: "the header", STATED: "stated", PIXELS: "stated as pixels", None: "none"}
    return f"X: {names[group.x.source]}, Y: {names[group.y.source]}"


def folders_of(images: Sequence[str]) -> list[str]:
    """Return the folders holding ``images``, sorted (``.`` for the experiment folder)."""
    return sorted({image.rpartition("/")[0] or "." for image in images})


def where_words(group: SizeGroup) -> str:
    """Return the folders (imaging units) of a group, the first ones named."""
    folders = folders_of(group.images)
    named = ", ".join(folders[:NAMED_FOLDERS])
    more = len(folders) - NAMED_FOLDERS
    return named + (f" and {plural(more, 'more folder')}" if more > 0 else "")


def summary_line(groups: Sequence[SizeGroup]) -> tuple[str, str]:
    """Return the kind (``ok``, ``info`` or ``warn``) and the one line about the pixel size.

    One group is the whole experiment with one size; several are named with their image
    counts and, for all but the largest, their folders. Plain text.
    """
    total = sum(len(group.images) for group in groups)
    if not groups:
        return "info", "Pixel size: no image to read a size from."
    if len(groups) == 1:
        group = groups[0]
        count = plural(total, "image")
        if group.calibrated:
            return "ok", (
                f"Pixel size: {size_words(group)} ({source_words(group)}, same in all {count})."
            )
        return "info", f"Pixel size: {size_words(group)} ({source_words(group)}; {count})."
    spread = spread_line(groups)
    if spread is not None:
        return "warn", spread
    parts = [f"{size_words(groups[0])} in {plural(len(groups[0].images), 'image')}"]
    parts += [
        f"{size_words(group)} in {len(group.images):,} ({where_words(group)})"
        for group in groups[1:]
    ]
    headers = all(group.sources <= {HEADER, None} for group in groups)
    title = "Headers disagree" if headers else "Pixel sizes differ"
    return "warn", f"{title}: {', '.join(parts)}."


MANY_GROUPS = 3
"""From this many different sizes on, the line gives their range instead of naming each."""
SLIGHT_SPREAD = 0.01
"""A spread under 1 % of the mean size reads "differ slightly"."""


def spread_line(groups: Sequence[SizeGroup]) -> str | None:
    """Return the one line for many sizes in one unit: their range, spread and counts.

    ``Pixel sizes differ slightly: 0.10047–0.10054 µm (spread 0.07 %) across 138 images, 25
    combinations``: the smallest and largest recorded value over X and Y, the spread as
    (largest - smallest) / mean of the two, the images, and the different (X, Y) pairs. None
    when there are fewer than :data:`MANY_GROUPS` sizes, an image without a size, or more than
    one unit: those are named one by one (:func:`summary_line`). Nothing is rounded away: the
    values are the recorded ones, written with ``:g``.
    """
    if len(groups) < MANY_GROUPS or not all(group.calibrated for group in groups):
        return None
    units = {size.unit for group in groups for size in (group.x, group.y)}
    if len(units) != 1:
        return None
    values = [size.value for group in groups for size in (group.x, group.y)]
    low, high = min(values), max(values)
    spread = (high - low) / ((high + low) / 2)
    unit = next(iter(units))
    symbol = UNIT_SYMBOLS.get(unit or "", unit or "no unit")
    how = "differ slightly" if spread < SLIGHT_SPREAD else "differ"
    percent = f"{spread * 100:.0f}" if spread >= 0.1 else f"{spread * 100:.2g}"
    total = sum(len(group.images) for group in groups)
    return (
        f"Pixel sizes {how}: {low:g}–{high:g} {symbol} (spread {percent} %) across "
        f"{plural(total, 'image')}, {len(groups)} combinations."
    )


def combination_rows(groups: Sequence[SizeGroup]) -> list[tuple[str, str, str, str]]:
    """Return ``(size, images, folders, source)`` per different (X, Y) size, largest first."""
    return [
        (size_words(group), f"{len(group.images):,}", where_words(group), source_words(group))
        for group in groups
    ]


def unit_rows(sizes: Mapping[str, Sequence[RecordedSize]]) -> list[tuple[str, str, str]]:
    """Return ``(folder, size, source)`` per imaging unit (folder), for the per-unit table.

    A folder whose images are recorded with different sizes says so instead of naming one.
    """
    by_folder: dict[str, dict[str, list[RecordedSize]]] = {}
    for path, recorded in sizes.items():
        by_folder.setdefault(path.rpartition("/")[0] or ".", {})[path] = list(recorded)
    rows = []
    for folder in sorted(by_folder):
        groups = xy_groups(by_folder[folder])
        if len(groups) == 1:
            rows.append((folder, size_words(groups[0]), source_words(groups[0])))
        else:
            rows.append((folder, "differs between its images", summary_line(groups)[1]))
    return rows


__all__ = [
    "SizeGroup",
    "combination_rows",
    "folders_of",
    "size_words",
    "source_words",
    "spread_line",
    "summary_line",
    "unit_rows",
    "where_words",
    "xy_groups",
]
