"""The guided *Tag channels* step: which rows of dropdowns the user's answers open (pure).

Owner design of 25 November 2026. Instead of one row of dropdowns per imaging unit, the step
asks how far one channel order goes, then opens only the rows that answer needs:

- **Is the channel order the same for the whole dataset?** *Yes* opens one row (:data:`WHOLE`)
  per channel count: one row when every image has the same number of channels, else one row
  per count (owner decision: for example one for the 3-channel and one for the 4-channel
  images).
- *No* asks whether the order is the same **within each biological repeat** (:data:`REPEAT`,
  one row per repeat folder and channel count, as *Tag this repeat folder*) or whether
  **every imaging unit** is tagged (:data:`UNIT`, one row per unit).

The rows are a form: choosing in a dropdown changes the form only. **Submit** checks every row
against the image headers and saves the tags, with the tag review, in one draft write
(:mod:`.notebook_channels_submit`). Nothing here reads a file or a widget, and nothing is
guessed: a row is pre-filled only from what the user chose this session (:func:`seed`), from
the tags already saved for every unit of the row, or from header names that equal the labels
exactly (:func:`.channel_labels.proposal_for`, a suggestion shown as such).

A unit whose own images disagree (another header or channel count within the unit,
:func:`.repeat_tags.repeat_apart`) is in no row: it is listed at Submit, never filled.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .channel_labels import UnitLayout, proposal_for, tags_of
from .channel_matrix import apart_reason
from .draft_answers import DraftAnswers, UnitAnswer, UnitKey
from .notebook_values import natural_key

WHOLE = "whole"
"""The order is the same for the whole dataset: one row per channel count."""
REPEAT = "repeat"
"""The order is the same within each biological repeat: one row per repeat folder and count."""
UNIT = "unit"
"""Every imaging unit is tagged on its own: one row per unit."""
SCOPES = (WHOLE, REPEAT, UNIT)

RowKey = tuple
"""A row: ``(WHOLE, count)``, ``(REPEAT, repeat folder, count)`` or ``(UNIT, unit key)``."""

FROM_CHOICE = "choice"
FROM_SAVED = "saved"
FROM_HEADER = "header"
FROM_NONE = ""


@dataclass(frozen=True)
class FormRow:
    """One row of dropdowns: the units it tags, their channel count, and its title."""

    key: RowKey
    units: tuple[UnitLayout, ...]
    count: int

    @property
    def scope(self) -> str:
        """Return the scope of the row (:data:`WHOLE`, :data:`REPEAT` or :data:`UNIT`)."""
        return str(self.key[0])


def unit_order(layout: UnitLayout) -> tuple:
    """Return the display order of a unit: its labels, numbers as numbers (rep2 before rep10)."""
    return tuple(natural_key(part) for part in layout.key)


def colour_unit(layout: UnitLayout, units: Mapping[UnitKey, UnitAnswer]) -> bool:
    """Return True for a unit the user answered colour (RGB): R, G and B, nothing to tag."""
    answer = units.get(layout.key)
    return answer is not None and answer.image_type == "rgb"


def taggable(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer]
) -> list[UnitLayout]:
    """Return the units whose channels are tagged with labels: readable and not colour."""
    found = [u for u in layouts if u.usable and not colour_unit(u, units)]
    return sorted(found, key=unit_order)


def apart(layouts: Sequence[UnitLayout]) -> list[UnitLayout]:
    """Return the units whose own images disagree: in no row, listed at Submit."""
    return [u for u in layouts if apart_reason(u)]


def untyped(layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer]) -> list[UnitKey]:
    """Return the readable units whose image type is not answered yet (asked before tagging)."""
    out = []
    for layout in layouts:
        answer = units.get(layout.key)
        if layout.usable and (answer is None or answer.image_type is None):
            out.append(layout.key)
    return out


def counts(layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer]) -> list[int]:
    """Return the channel counts of the units the rows tag, sorted (one entry: all the same)."""
    rowed = [u for u in taggable(layouts, units) if not apart_reason(u)]
    return sorted({u.channels for u in rowed})


def form_rows(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer], scope: str
) -> list[FormRow]:
    """Return the rows of dropdowns the scope opens, in display order.

    Only taggable units whose images agree are in a row (:func:`taggable`, :func:`apart`).
    """
    rowed = [u for u in taggable(layouts, units) if not apart_reason(u)]
    if scope == WHOLE:
        by_count: dict[int, list[UnitLayout]] = {}
        for layout in rowed:
            by_count.setdefault(layout.channels, []).append(layout)
        return [FormRow((WHOLE, c), tuple(us), c) for c, us in sorted(by_count.items())]
    if scope == REPEAT:
        groups: dict[tuple[str, int], list[UnitLayout]] = {}
        for layout in rowed:  # every taggable unit, a header-colour one answered channels too
            groups.setdefault((layout.key[1], layout.channels), []).append(layout)
        ordered = sorted(groups.items(), key=lambda item: (natural_key(item[0][0]), item[0][1]))
        return [FormRow((REPEAT, rep, c), tuple(g), c) for (rep, c), g in ordered]
    if scope == UNIT:
        return [FormRow((UNIT, u.key), (u,), u.channels) for u in rowed]
    raise ValueError(f"unknown scope {scope!r}")


def broader(row: FormRow) -> list[RowKey]:
    """Return the rows whose choice pre-fills this one when it has none: its repeat, the whole."""
    if row.scope == UNIT:
        key = row.key[1]
        return [(REPEAT, key[1], row.count), (WHOLE, row.count)]
    if row.scope == REPEAT:
        return [(WHOLE, row.count)]
    return []


def shared_saved(row: FormRow, answers: DraftAnswers) -> tuple[str, ...] | None:
    """Return the tags every unit of the row has saved, when they are the same and complete."""
    found = {tags_of(u, answers.units.get(u.key)) for u in row.units}
    if len(found) != 1:
        return None
    (tags,) = found
    return None if None in tags else tuple(t for t in tags if t is not None)


def shared_proposal(row: FormRow, labels: Collection[str]) -> tuple[str, ...] | None:
    """Return the tags every unit's header names propose, when exactly the same for all of them.

    The exact rule of :func:`.channel_labels.proposal_for` (NFC, no trimming): a suggestion the
    user checks and submits, never saved on its own.
    """
    found = {proposal_for(u, labels) for u in row.units}
    if len(found) != 1:
        return None
    (tags,) = found
    return tags


def seed(
    row: FormRow,
    pending: Mapping[RowKey, Sequence[str | None]],
    answers: DraftAnswers,
    labels: Collection[str],
) -> tuple[list[str | None], str]:
    """Return what a row shows, and where it comes from (:data:`FROM_CHOICE` and so on).

    In order: what the user chose in this row; what the user chose in a broader row this
    session (its repeat, the whole dataset), so that switching the answer keeps the choices that
    still apply; the tags every unit of the row has saved; the labels its header names equal
    exactly; nothing. A label no longer in the list is blank.
    """

    def known(tags: Sequence[str | None]) -> list[str | None]:
        if len(tags) != row.count:
            return [None] * row.count
        return [t if t in labels else None for t in tags]

    if row.key in pending:
        return known(pending[row.key]), FROM_CHOICE
    for key in broader(row):
        if key in pending and any(pending[key]):
            return known(pending[key]), FROM_CHOICE
    saved = shared_saved(row, answers)
    if saved is not None:
        return known(saved), FROM_SAVED
    proposal = shared_proposal(row, labels)
    if proposal is not None:
        return known(proposal), FROM_HEADER
    return [None] * row.count, FROM_NONE


def row_error(tags: Sequence[str | None]) -> str | None:
    """Return why a row cannot be submitted (a channel untagged, a label twice), else None."""
    named = [t for t in tags if t]
    if len(named) != len(tags):
        return "tag every channel"
    if len(set(named)) != len(named):
        return "each label once"
    return None


def scope_of_draft(answers: DraftAnswers) -> str | None:
    """Return the answer a draft already holds: :data:`WHOLE` for a fast-mode draft, else None.

    Only ``channel_layout`` (the user's statement, ADR 0006) says the order is the same for
    the whole dataset; a strict draft does not say how far its lists go, so it is asked.
    """
    return WHOLE if answers.channel_layout is not None else None


__all__ = [
    "FROM_CHOICE",
    "FROM_HEADER",
    "FROM_NONE",
    "FROM_SAVED",
    "REPEAT",
    "SCOPES",
    "UNIT",
    "WHOLE",
    "FormRow",
    "RowKey",
    "apart",
    "broader",
    "colour_unit",
    "counts",
    "form_rows",
    "row_error",
    "scope_of_draft",
    "seed",
    "shared_proposal",
    "shared_saved",
    "taggable",
    "unit_order",
    "untyped",
]
