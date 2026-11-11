"""The channel layouts a description declares, and the refusal of mixed image types.

A unit's layout is its ordered channel list and its image type (experiment-v1 section 8).
Stages 1-2 processed one layout per experiment. Stage 3 (S3.3, owner decision of 11 November
2026: different channel counts across repeats now) accepts units with **different channel
lists**: each unit's channels are tagged with canonical IDs, the same ID is the same stain, and
each image's channel count is checked against its own unit (``R_CHANNEL_COUNT_MISMATCH``,
``R_UNIT_NOT_HOMOGENEOUS``, :mod:`.physical_check`). Units of **different image types** (RGB
beside ordinary channel units, fixture ``v29``) are still refused with
``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``: the owner enabled different channel lists, not mixed
image types, which stay refused until decided (one pixel type and one table per run). This is
decided from the description alone; nothing here reads a folder or a header.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import ascii_uppercase

from .description import Description


@dataclass(frozen=True)
class UnitName:
    """A declared unit by canonical IDs: repeat, condition and technical repeat."""

    repeat: str
    condition: str
    technical_repeat: str

    def __str__(self) -> str:
        return f"{self.repeat}/{self.condition}/{self.technical_repeat}"


@dataclass(frozen=True)
class DeclaredLayout:
    """One layout and the units that declare it, in repeat, condition, unit ID order."""

    channels: tuple[str, ...]
    image_type: str | None
    units: tuple[UnitName, ...]


def declared_layouts(description: Description) -> tuple[DeclaredLayout, ...]:
    """Group the description's units by ``(channels, image_type)``.

    Groups are ordered by their first unit in ID order, so the same description always gives
    the same groups in the same order.
    """
    found: dict[tuple[tuple[str, ...], str | None], list[UnitName]] = {}
    for repeat_id, repeat in sorted(description.repeats.items()):
        for condition_id, member in sorted(repeat.members.items()):
            for unit_id, unit in sorted(member.technical_repeats.items()):
                key = (unit.channels, unit.image_type)
                found.setdefault(key, []).append(UnitName(repeat_id, condition_id, unit_id))
    return tuple(DeclaredLayout(c, t, tuple(u)) for (c, t), u in found.items())


def is_heterogeneous(layouts: tuple[DeclaredLayout, ...]) -> bool:
    """Return True when the units declare more than one image type (still refused).

    Different channel lists of one image type are accepted from Stage 3 (S3.3).
    """
    return len({layout.image_type for layout in layouts}) > 1


def _label(index: int) -> str:
    """Return A, B, ... Z, then AA, AB, ... for the layout at ``index``."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, len(ascii_uppercase))
        letters = ascii_uppercase[rest] + letters
    return letters


def _acquisitions(count: int) -> str:
    return f"{count} acquisition" + ("" if count == 1 else "s")


def heterogeneous_message(layouts: tuple[DeclaredLayout, ...]) -> str:
    """Say in plain words which layouts the description declares and why the run stops."""
    parts = []
    for index, layout in enumerate(layouts):
        kind = "" if layout.image_type is None else f", {layout.image_type}"
        names = ", ".join(layout.channels)
        parts.append(f"{_label(index)}: {names}{kind} in {_acquisitions(len(layout.units))}")
    return (
        f"This experiment has {len(layouts)} channel layouts of different image types "
        f"({'; '.join(parts)}). Your description is saved and valid, but this version processes "
        "one image type per experiment (RGB and separate channels are not mixed). Nothing was "
        "processed."
    )


def heterogeneous_units(layouts: tuple[DeclaredLayout, ...]) -> tuple[str, ...]:
    """Return every unit as ``"<layout letter>: repeat/condition/technical repeat"``."""
    return tuple(
        f"{_label(index)}: {unit}" for index, layout in enumerate(layouts) for unit in layout.units
    )


def layout_kinds(layouts: tuple[DeclaredLayout, ...]) -> str:
    """Name the kinds of the layouts in words, e.g. ``"fluorescence and RGB"``."""
    kinds = {"RGB" if layout.image_type == "rgb" else "fluorescence" for layout in layouts}
    if len(kinds) == 1:
        return f"{kinds.pop()}, with different channel lists"
    return " and ".join(sorted(kinds, key=lambda kind: kind == "RGB"))
