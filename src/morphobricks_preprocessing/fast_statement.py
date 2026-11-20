"""The fast-mode statement written into the draft answers (Spec ADR 0006, FC1 and FC2; pure).

While *Every imaging unit has the same channels in the same order* is ticked on *Tag channels*
(the default for a new description, FC1), the draft records the user's statement as
``channel_layout``: the list is the order of the one tagged unit (the layout's row of tags, read
from the first tagged unit), its image type is that unit's answered type, and its date is the
day of the user's action (given by the caller, kept byte for byte while the list and type stay
the same, experiment-v1 4.3a). Each unit says how its list was confirmed (``channels_from``):

- ``unit`` only by the user's action (ADR 0006, section 10: "the draft never sets ``unit`` on
  its own"): a unit the user switched to tagging on its own, one whose cells the user clicked
  to other tags or another type than the list's, or one already marked ``unit`` when the page
  opened (a saved marker is never turned back here);
- ``experiment`` otherwise, also for a unit tagged from its header names with no click whose
  tags differ from the list: it is published following the list, and its images are refused
  at the check (``R_FAST_LAYOUT_CONTRADICTION``, for example ``header_order``) until the user
  tags it on its own. A unit whose tags or image type are not all answered is left unmarked.

Unticked, the draft is strict (no statement, no marker). The mode of the published file is
kept unless the user changes the tick: :func:`with_statement` records ``switch_mode`` exactly
when the tick differs from the published mode (experiment-v1 section 10). Nothing is inferred
from header names, file names or pixels here: the list is the user's tags.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace

from .channel_labels import UnitLayout, tags_of
from .draft_answers import CHANNELS, EXPERIMENT, OWN, DraftAnswers, LayoutAnswer, UnitKey

RGB = "rgb"
RGB_LIST = ("R", "G", "B")


def published_fast(answers: DraftAnswers) -> bool | None:
    """Return the published file's mode (True: fast), or None when nothing is published.

    The draft holds the published mode as read, unless the user switched it (``switch_mode``).
    """
    if answers.base_sha256 == "absent":
        return None
    return (answers.channel_layout is not None) != answers.switch_mode


def _strict(answers: DraftAnswers) -> DraftAnswers:
    units = {k: replace(u, channels_from=None) for k, u in answers.units.items()}
    return replace(answers, channel_layout=None, units=units)


def _list(
    answers: DraftAnswers,
    lead: UnitLayout | None,
    template: Sequence[str | None],
    layouts: Sequence[UnitLayout],
) -> tuple[tuple[str, ...], str] | None:
    """Return the statement's list and type: the first tagged unit's, or RGB for colour only."""
    if lead is not None:
        given = answers.units.get(lead.key)
        kind = given.image_type if given is not None else None
        if None in template or not template or kind != CHANNELS:
            return None
        return tuple(t for t in template if t is not None), CHANNELS
    units = [answers.units.get(u.key) for u in layouts if u.usable]
    if units and all(u is not None and u.image_type == RGB for u in units):
        return RGB_LIST, RGB
    return None


def with_statement(
    answers: DraftAnswers,
    layouts: Sequence[UnitLayout],
    lead: UnitLayout | None,
    template: Sequence[str | None],
    own: Collection[UnitKey],
    clicked: Collection[UnitKey] = (),
    *,
    ticked: bool,
    day: str,
) -> DraftAnswers:
    """Return the answers with the statement and the markers the tick and the tags give.

    Parameters
    ----------
    answers
        The saved answers.
    layouts
        Every imaging unit of the page.
    lead
        The first tagged unit of the first layout (None when no unit is in a layout: colour
        units only, or none).
    template
        The first layout's row of tags (the list, in source order).
    own
        The units tagged on their own: switched so this session, or marked ``unit`` when the
        page opened.
    clicked
        The units whose cells the user clicked this session (a user's tag, never a no-click
        tag from header names).
    ticked
        Whether the statement is ticked.
    day
        The day of the user's action (``YYYY-MM-DD``), used only when the list or type changes.
    """
    published = published_fast(answers)
    switch = published is not None and published != ticked
    if not ticked:
        return replace(_strict(answers), switch_mode=switch)
    found = _list(answers, lead, template, layouts)
    if found is None:  # not stated yet: the row of tags or the lead's type is not answered
        return replace(answers, switch_mode=switch)
    channels, kind = found
    old = answers.channel_layout
    keep = old is not None and (tuple(old.channels), old.image_type) == (channels, kind)
    statement = LayoutAnswer(channels, kind, old.confirmed_on if keep else day)
    units = dict(answers.units)
    for layout in layouts:
        unit = units.get(layout.key)
        if unit is None or None in tags_of(layout, unit) or not unit.channels:
            continue
        if unit.image_type is None:
            units[layout.key] = replace(unit, channels_from=None)  # not answered yet
            continue
        same = tuple(unit.channels) == channels and unit.image_type == kind
        follows = layout.key not in own and (same or layout.key not in clicked)
        units[layout.key] = replace(unit, channels_from=EXPERIMENT if follows else OWN)
    return replace(answers, channel_layout=statement, units=units, switch_mode=switch)


def statement_changed(before: DraftAnswers, after: DraftAnswers) -> bool:
    """Return True when the statement, a marker or the switch differs between two answers."""
    marks: Mapping[UnitKey, str | None] = {k: u.channels_from for k, u in before.units.items()}
    now = {k: u.channels_from for k, u in after.units.items()}
    return (
        before.channel_layout != after.channel_layout
        or before.switch_mode != after.switch_mode
        or marks != now
    )


__all__ = ["published_fast", "statement_changed", "with_statement"]
