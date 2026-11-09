"""Changes to the draft answers made by the *Your channels* cards, each returning new answers.

The label list (``channels``), the tags and the image type of each unit (``units``) are all in the
draft (:mod:`.draft_answers`); these functions change one slice and keep the rest exactly:

- :func:`with_labels` replaces the label list (the colour tokens ``R``, ``G``, ``B`` of declared
  colour units are kept) and takes back every tag of a label that is no longer in it;
- :func:`with_tags` and :func:`with_type` change one unit; a colour unit is tagged ``R``, ``G``,
  ``B`` and nothing else, an ordinary one starts untagged;
- :func:`synced` recomputes what is derived from the tags: the header names each label is
  evidence for (the tag is the user's confirmation of the alias) and the declared colour tokens.

Nothing is guessed: a position is tagged only by a tag the user gave.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace

from .channel_labels import RGB_IDS, UnitLayout, aliases
from .draft_answers import ChannelAnswer, DraftAnswers, UnitAnswer, UnitKey


def label_ids(answers: DraftAnswers) -> list[str]:
    """Return the declared labels, sorted: the channels that are not a colour token."""
    return sorted(k for k in answers.channels if k not in RGB_IDS)


def all_colour(layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer]) -> bool:
    """Return True when the user declared every unit colour (``rgb``): no label is needed then.

    Only a saved answer counts (experiment-v1 section 4.4b): the header's pre-tick, a missing
    answer or a folder that cannot be read keeps this False, so the labels stay required.
    """
    return bool(layouts) and all(_declared_colour(lay, units.get(lay.key)) for lay in layouts)


def _declared_colour(layout: UnitLayout, answer: UnitAnswer | None) -> bool:
    """Return True for a readable unit with a saved ``rgb`` answer that still fits its image."""
    return (
        layout.usable
        and answer is not None
        and answer.image_type == "rgb"
        and layout.channels == len(RGB_IDS) == len(answer.channels)  # a stale answer never counts
    )


def _unit(answers: DraftAnswers, layout: UnitLayout) -> UnitAnswer:
    saved = answers.units.get(layout.key)
    if saved is not None and len(saved.channels) == layout.channels:
        return saved
    return UnitAnswer(
        (None,) * layout.channels,
        None if saved is None else saved.image_type,
        None if saved is None else saved.display_name,
    )


def _store(answers: DraftAnswers, key: UnitKey, unit: UnitAnswer) -> DraftAnswers:
    units = dict(answers.units)
    if all(t is None for t in unit.channels) and unit.image_type is None and not unit.display_name:
        units.pop(key, None)
    else:
        units[key] = unit
    return replace(answers, units=units)


def with_tags(
    answers: DraftAnswers, layout: UnitLayout, tags: Sequence[str | None]
) -> DraftAnswers:
    """Return the answers with the tags of one unit set (one entry per C index)."""
    if len(tags) != layout.channels:
        raise ValueError(f"{layout.key} has {layout.channels} channels, not {len(tags)} tags")
    return _store(answers, layout.key, replace(_unit(answers, layout), channels=tuple(tags)))


def with_type(answers: DraftAnswers, layout: UnitLayout, image_type: str | None) -> DraftAnswers:
    """Return the answers with one unit's image type set.

    A colour unit's channels are ``R``, ``G``, ``B``; going back to ordinary channels (or to no
    answer) clears them, since nothing says which label each position then is.
    """
    unit = _unit(answers, layout)
    if image_type == "rgb":
        if layout.channels != len(RGB_IDS):
            raise ValueError(f"a colour image has 3 channels, {layout.key} has {layout.channels}")
        tags: tuple[str | None, ...] = RGB_IDS
    elif unit.image_type == "rgb":
        tags = (None,) * layout.channels
    else:
        tags = tuple(unit.channels)
    return _store(answers, layout.key, replace(unit, channels=tags, image_type=image_type))


def with_labels(answers: DraftAnswers, labels: Mapping[str, str | None]) -> DraftAnswers:
    """Return the answers with exactly these labels (ID -> shown-as name or None).

    A tag of a label that is gone becomes untagged (never moved to another label).
    The colour tokens of declared colour units stay.
    """
    kept = {k: v for k, v in answers.channels.items() if k in RGB_IDS}
    channels = {
        label: replace(answers.channels.get(label, ChannelAnswer()), display_name=name or None)
        for label, name in labels.items()
    }
    units = {}
    for key, unit in answers.units.items():
        if unit.image_type == "rgb":
            units[key] = unit
            continue
        tags = tuple(t if t in labels else None for t in unit.channels)
        units[key] = replace(unit, channels=tags)
    return replace(answers, channels={**kept, **channels}, units=units)


def untagged_by(answers: DraftAnswers, labels: Collection[str]) -> int:
    """Return how many saved tags name a label that is not in ``labels``."""
    return sum(
        1
        for unit in answers.units.values()
        if unit.image_type != "rgb"
        for t in unit.channels
        if t and t not in labels
    )


def synced(answers: DraftAnswers, layouts: Sequence[UnitLayout]) -> DraftAnswers:
    """Return the answers with the derived parts recomputed from the saved tags.

    Each label's ``header_names`` become the raw header names of every cell tagged with it (None
    when none has a name), and ``R``, ``G``, ``B`` are declared exactly when some unit is a
    colour unit.
    """
    found = aliases(layouts, answers.units)
    channels = {
        k: replace(v, header_names=found.get(k))
        for k, v in answers.channels.items()
        if k not in RGB_IDS
    }
    if any(u.image_type == "rgb" for u in answers.units.values()):
        channels.update({token: ChannelAnswer() for token in RGB_IDS})
    return replace(answers, channels=channels)


__all__ = [
    "all_colour",
    "label_ids",
    "synced",
    "untagged_by",
    "with_labels",
    "with_tags",
    "with_type",
]
