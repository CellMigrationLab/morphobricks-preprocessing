"""Tag a whole repeat folder at once, for every condition (owner, 20 November 2026; pure).

On *Tag channels*, one row of tags per repeat **folder** and channel count: the user sets it
once and it is saved, as the user's tags, for every imaging unit of that repeat in every
condition and every technical repeat (experiment-v1 4.4: each unit still holds its own list;
the file format does not change). It is a shortcut for tagging those units one by one, never a
guess:

- the units are grouped by the biological-repeat **folder label** (*Tag channels* comes before
  the pairing on *Design*) and their channel count, so a unit with another channel count is in
  another row, never filled from this one; when a saved pairing puts one folder name of
  different conditions into different biological repeats, the row is flagged and not filled
  (:func:`pairing_collision`);
- each unit's header names are checked against the row (:func:`.channel_matrix.differences`,
  the first unit of the row being the reference for names that say no label): a unit whose
  header contradicts it is listed with the contradiction, and not filled;
- in fast mode (Spec ADR 0006) setting the row is the user's action for every unit it fills:
  they are tagged on their own (``channels_from = "unit"``), which is how an experiment whose
  repeats have an extra channel is described (owner decision: strict tagging for them).

Nothing here reads a file or a widget.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from .channel_labels import UnitLayout, tags_of
from .channel_matrix import Difference, differences
from .draft_answers import DraftAnswers, UnitKey

GroupKey = tuple[str, int]
"""A row: the biological-repeat folder label and the channel count of its units."""


def repeat_groups(
    layouts: Sequence[UnitLayout], colour: Collection[UnitKey] = ()
) -> dict[GroupKey, tuple[UnitLayout, ...]]:
    """Return the units of each biological repeat and channel count, in folder order.

    Colour (RGB) and untaggable units are left out (their channels are R, G and B, or unknown).
    """
    out: dict[GroupKey, list[UnitLayout]] = {}
    for layout in sorted(layouts, key=lambda u: u.key):
        if layout.usable and not layout.rgb and layout.key not in colour:
            out.setdefault((layout.key[1], layout.channels), []).append(layout)
    return {key: tuple(units) for key, units in sorted(out.items())}


def repeat_fill(
    answers: DraftAnswers,
    group: Sequence[UnitLayout],
    tags: Sequence[str],
    labels: Collection[str],
) -> tuple[dict[UnitKey, tuple[str, ...]], list[tuple[UnitKey, int]], dict]:
    """Return the tags to save for the row's units, the cells, and the units refused.

    A unit is refused (listed with its contradictions, never filled) when a header name says
    another label than the row's at its C index, or says none and is not the first unit's name
    there (:func:`.channel_matrix.differences`).
    """
    tags = tuple(tags)
    save: dict[UnitKey, tuple[str, ...]] = {}
    cells: list[tuple[UnitKey, int]] = []
    refused: dict[UnitKey, tuple[Difference, ...]] = {}
    for layout in group:
        found = differences(layout, tags, group[0], labels, answers.channels)
        if found:
            refused[layout.key] = found
            continue
        save[layout.key] = tags
        cells += [(layout.key, i) for i in range(layout.channels)]
    return save, cells, refused


def repeat_shown(answers: DraftAnswers, group: Sequence[UnitLayout]) -> tuple[str, ...] | None:
    """Return the tags every unit of the row has saved, when they are all the same and complete.

    A convenience on reopening: the saved data are per unit; the row only shows that every unit
    of the repeat holds the same tags.
    """
    found = {tags_of(u, answers.units.get(u.key)) for u in group}
    if len(found) != 1:
        return None
    (tags,) = found
    return None if None in tags else tuple(t for t in tags if t is not None)


def pairing_collision(answers: DraftAnswers, group: Sequence[UnitLayout]) -> dict[str, str] | None:
    """Return each condition's biological repeat when one folder name maps to several, else None.

    The row's units share a repeat folder name; the saved pairing (``answers.pairing``, a
    folder paired under another repeat's label) may put the folders of different conditions
    into different biological repeats. Then the row is not one repeat: it is flagged with
    each condition's repeat and never fills anything.
    """
    found = {u.key[0]: answers.pairing.get((u.key[0], u.key[1]), u.key[1]) for u in group}
    return dict(sorted(found.items())) if len(set(found.values())) > 1 else None


__all__ = ["GroupKey", "pairing_collision", "repeat_fill", "repeat_groups", "repeat_shown"]
