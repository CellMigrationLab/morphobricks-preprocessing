"""What *Tag by repeat* keeps of its refusals and confirmations, so a reopened draft keeps it.

A unit whose image headers disagree with its repeat's row is refused, and counts only once the
user confirms that unit (:mod:`.notebook_channels_repeat`, :mod:`.notebook_channels_override`).
That record lives in the draft file (``tag_review`` of :mod:`.draft_format`, written only while
it is not empty), so a new notebook on the same draft does not tag a refused unit from the
aliases saved when another unit was confirmed. Pure data: no widget, no file.

A refusal keeps what its header was read against: the row's tags at the click, the reference
unit, and the aliases (label to header names) as they were when the unit was first refused.
A confirmation keeps the tags and the unit's own header names it was confirmed for
(:class:`Override`, used by :mod:`.repeat_tags`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from .draft_answers import UnitKey


class Override(NamedTuple):
    """A listed unit's confirmed tags, and the unit's own header names they were confirmed for."""

    tags: tuple[str, ...]
    names: tuple[str | None, ...]
    """The unit's own header name at each C index at the confirmation (read before the save)."""


@dataclass(frozen=True)
class RefusedUnit:
    """One refused unit: the row's tags, its reference unit and the aliases it is read against."""

    tags: tuple[str, ...]
    reference: UnitKey | None
    """The row's reference unit; None for a unit whose images disagree with each other."""
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """Label to the header names saved for it when this unit was first refused."""


@dataclass(frozen=True)
class TagReview:
    """The refused units and the confirmed ones of a draft."""

    refused: Mapping[UnitKey, RefusedUnit] = field(default_factory=dict)
    confirmed: Mapping[UnitKey, Override] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        """True when nothing is refused or confirmed (the file then has no ``tag_review``)."""
        return not self.refused and not self.confirmed


def review_to_json(review: TagReview) -> dict[str, list[dict[str, object]]]:
    """Return the JSON object of a review: units in key order, each with its own fields."""
    return {
        "refused": [
            {
                "unit": list(key),
                "tags": list(unit.tags),
                "reference": None if unit.reference is None else list(unit.reference),
                "aliases": {k: list(v) for k, v in sorted(unit.aliases.items())},
            }
            for key, unit in sorted(review.refused.items())
        ],
        "confirmed": [
            {"unit": list(key), "tags": list(unit.tags), "names": list(unit.names)}
            for key, unit in sorted(review.confirmed.items())
        ],
    }


__all__ = ["Override", "RefusedUnit", "TagReview", "review_to_json"]
