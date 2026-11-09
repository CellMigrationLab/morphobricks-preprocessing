"""The channel layouts a description declares, and the Stage 1 refusal of more than one.

A unit's layout is its ordered channel list and its image type (experiment-v1 section 8).
Stage 1 processes one layout per experiment: ``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED`` when the
units do not all declare the same one. This is decided from the description alone; nothing
here reads a folder or a header.
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
    """Return True when the units declare more than one layout."""
    return len(layouts) > 1


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
        f"This experiment has {len(layouts)} channel layouts ({'; '.join(parts)}). "
        "Your description is saved and valid, but this version processes one layout per "
        "experiment. Nothing was processed."
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
