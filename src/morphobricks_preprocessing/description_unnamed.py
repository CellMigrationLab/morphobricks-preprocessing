"""Images of a described run whose header names no channel, or not every one (experiment-v1 §4.3).

Such an image passes the per-unit header checks: it gives no evidence against the user's
confirmed layout, which is used. It is not warned about; it is *listed*, so a reader can see
which images stand on the confirmation alone. The list is part of the unit's record in the
manifest's ``experiment_description`` entry (:mod:`.description_record`), under
:data:`FIELD`, and only for a unit that has such images: a unit whose images are all fully
named has no such key. Each item names the image (relative to the experiment root), whether
its header names no channel (``"none"``) or only some (``"partial"``), and the zero-based C
positions without a name.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .validation import plural

FIELD = "images_without_header_channel_names"
"""Key of a unit record that has images with unnamed channels."""
NONE = "none"
PARTIAL = "partial"
_KEYS = {"image", "header_names", "channel_positions"}


def unnamed_items(
    unnamed: Mapping[str, tuple[int, ...]], declared_channels: int
) -> list[dict[str, object]]:
    """Return the manifest items of one unit's unnamed images, sorted by path."""
    return [
        {
            "image": path,
            "header_names": NONE if len(positions) == declared_channels else PARTIAL,
            "channel_positions": list(positions),
        }
        for path, positions in sorted(unnamed.items())
    ]


def checked_items(value: object, where: str) -> None:
    """Refuse a malformed list of unnamed images of one unit record.

    Raises
    ------
    ValueError
        When it is not a non-empty list of items with exactly the recorded keys, a string
        image, ``none`` or ``partial``, and ascending distinct non-negative whole positions
        (all of the unit's positions are not known here, so only ``none`` and ``partial``
        are told apart by the writer).
    """
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}.{FIELD} must be a non-empty list")
    for item in value:
        if not isinstance(item, dict) or set(item) != _KEYS:
            raise ValueError(f"{where}.{FIELD} items must hold exactly {', '.join(sorted(_KEYS))}")
        if not isinstance(item["image"], str) or not item["image"]:
            raise ValueError(f"{where}.{FIELD} image is not a path")
        if item["header_names"] not in (NONE, PARTIAL):
            raise ValueError(f"{where}.{FIELD} header_names is not {NONE} or {PARTIAL}")
        positions = item["channel_positions"]
        whole = isinstance(positions, list) and all(
            isinstance(p, int) and not isinstance(p, bool) and p >= 0 for p in positions
        )
        if not whole or not positions or positions != sorted(set(positions)):
            raise ValueError(f"{where}.{FIELD} channel_positions must be ascending positions")
    paths = [item["image"] for item in value]
    if paths != sorted(set(paths)):
        raise ValueError(f"{where}.{FIELD} images must be sorted and distinct")


def listed_units(units: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """Return the unit records that list unnamed images."""
    return [unit for unit in units if unit.get(FIELD)]


def text_lines(units: Iterable[Mapping[str, object]]) -> list[str]:
    """Describe the listed images for the report text; empty when there are none."""
    listed = listed_units(units)
    if not listed:
        return []
    count = sum(len(unit[FIELD]) for unit in listed)  # type: ignore[arg-type]
    lines = [
        f"Images without header channel names: {plural(count, 'image')} in "
        f"{plural(len(listed), 'unit')} (checked as passing; your confirmed layout is used)"
    ]
    for unit in listed:
        lines.append(
            f"  repeat {unit['repeat']} / condition {unit['condition']} / "
            f"technical repeat {unit['technical_repeat']}"
        )
        for item in unit[FIELD]:  # type: ignore[union-attr]
            spots = ", ".join(str(p) for p in item["channel_positions"])
            what = "no channel named" if item["header_names"] == NONE else "unnamed positions"
            lines.append(f"    {item['image']}: {what} (channel positions {spots}, from 0)")
    return lines
