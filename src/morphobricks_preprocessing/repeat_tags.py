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
- a unit the channel screen does not count as eligible (:func:`.channel_matrix.eligible`: its
  images disagree in header or channel count) is never filled: the row names it with the
  existing reason (:func:`.channel_matrix.apart_reason`) and says its images must be corrected
  or separated (:func:`repeat_apart`);
- each unit's header names are checked against the row (:func:`.channel_matrix.differences`,
  the first unit of the row being the reference for names that say no label): a unit whose
  header contradicts it is listed with the contradiction, and not filled;
- in fast mode (Spec ADR 0006) setting the row is the user's action for every unit it fills:
  they are tagged on their own (``channels_from = "unit"``), which is how an experiment whose
  repeats have an extra channel is described (owner decision: strict tagging for them).

Nothing here reads a file or a widget.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from .channel_labels import UnitLayout, tags_of
from .channel_matrix import Difference, apart_reason, differences, eligible
from .dataset import canonical_repeat_groups
from .draft_answers import DraftAnswers, UnitKey
from .repeat_review import Override

GroupKey = tuple[str, int]
"""A row: the biological-repeat folder label and the channel count of its units."""


def header_names(layout: UnitLayout) -> tuple[str | None, ...]:
    """Return a unit's own header name at each C index (None where it has none)."""
    return tuple(layout.name(i) for i in range(layout.channels))


def override_holds(
    override: Override | None, layout: UnitLayout, tags: Sequence[str | None]
) -> bool:
    """Return True while the user's confirmation still covers the unit as it is read now.

    It covers the unit only while its tags are the confirmed ones and its own header names are
    the ones it was confirmed for. Nothing else voids it: aliases saved later for other units
    neither lapse it nor stand in for it. A changed header or tag lists the unit again, never
    counted unasked.
    """
    return (
        override is not None
        and tuple(tags) == override.tags
        and header_names(layout) == override.names
    )


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
    frozen: Mapping[UnitKey, Mapping] | None = None,
) -> tuple[dict[UnitKey, tuple[str, ...]], list[tuple[UnitKey, int]], dict]:
    """Return the tags to save for the row's units, the cells, and the units refused.

    A unit is refused (listed with its contradictions, never filled) when a header name says
    another label than the row's at its C index, or says none and is not the first unit's name
    there (:func:`.channel_matrix.differences`; the first unit is :func:`repeat_lead`). A unit
    whose images disagree (:func:`repeat_apart`) is neither filled nor listed here: the caller
    names it separately. ``frozen`` holds, for a unit already refused, the aliases of the time of
    its refusal: aliases saved since (by confirming or tagging other units) never clear it.
    """
    tags = tuple(tags)
    frozen = frozen or {}
    save: dict[UnitKey, tuple[str, ...]] = {}
    cells: list[tuple[UnitKey, int]] = []
    refused: dict[UnitKey, tuple[Difference, ...]] = {}
    apart = repeat_apart(group)
    lead = repeat_lead(group)
    for layout in group:
        if lead is None or layout.key in apart:
            continue
        found = differences(layout, tags, lead, labels, frozen.get(layout.key, answers.channels))
        if found:
            refused[layout.key] = found
            continue
        save[layout.key] = tags
        cells += [(layout.key, i) for i in range(layout.channels)]
    return save, cells, refused


def repeat_lead(group: Sequence[UnitLayout]) -> UnitLayout | None:
    """Return the row's reference unit: the first one that is not apart, None when all are.

    A unit whose images disagree has no single header, so it is never the reference the other
    units' header names are compared with (:func:`repeat_apart`).
    """
    apart = repeat_apart(group)
    return next((u for u in group if u.key not in apart), None)


def repeat_apart(group: Sequence[UnitLayout]) -> dict[UnitKey, str]:
    """Return why each unit of the row (a :func:`repeat_groups` row) cannot be filled by it.

    Eligibility and its reason are the channel screen's own (:func:`.channel_matrix.eligible`,
    :func:`.channel_matrix.apart_reason`): a unit whose images disagree (another header, or
    another channel count, within the unit) has no single header to check the row against.
    The row never fills it, names it with that reason, and says its images must be corrected
    or separated (the run would refuse it as not homogeneous).
    """
    return {
        u.key: f"{apart_reason(u)}; its images must be corrected or separated"
        for u in group
        if not eligible(u, ()) and apart_reason(u)  # a row holds no unusable or colour unit
    }


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


def repeat_members(layouts: Sequence[UnitLayout], repeat: str) -> tuple[UnitLayout, ...]:
    """Return every unit whose biological-repeat folder is ``repeat``, whatever its channel count.

    The full membership of the folder name: no grouping by channel count, no fast-mode filtering,
    colour and untaggable units included. A pairing collision is decided from this, never from
    the units one row happens to show.
    """
    return tuple(sorted((u for u in layouts if u.key[1] == repeat), key=lambda u: u.key))


def pairing_collision(answers: DraftAnswers, group: Sequence[UnitLayout]) -> dict[str, str] | None:
    """Return each condition's biological repeat when one folder name maps to several, else None.

    ``group`` must be the full membership of one repeat folder name (:func:`repeat_members`);
    the saved pairing (``answers.pairing``, a folder paired under another repeat's label) may
    put the folders of different conditions into different biological repeats. Then the folder
    is not one repeat: every row of it is flagged with each condition's repeat and never fills
    anything.
    """
    groups = canonical_repeat_groups((u.key[:2] for u in group), answers.pairing)
    found = {condition: label for (condition, _), label in groups.items()}
    return dict(sorted(found.items())) if len(set(found.values())) > 1 else None


__all__ = [
    "GroupKey",
    "Override",
    "header_names",
    "override_holds",
    "pairing_collision",
    "repeat_fill",
    "repeat_groups",
    "repeat_lead",
    "repeat_members",
    "repeat_apart",
    "repeat_shown",
]
