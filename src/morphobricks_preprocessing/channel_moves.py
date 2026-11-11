"""Confirmed channels at new positions or with lost header names, and their re-confirmations.

Experiment-v1 section 4.6 (c): a confirmed channel ID that loses a unit position it held in the
published file (the images were re-exported with their channels in another order, and the user
re-tagged the unit) is a changed entity, ``R_CONFIRMED_ID_CHANGED``, unless the user's explicit,
recorded re-confirmation names it (old ID, ``new_id`` null, the date of the user's action). The
notebook lists each such move (:func:`channel_moves`) and records the re-confirmation only on the
user's click; :func:`move_confirmations` turns the clicks into the records publication is given,
for exactly the moves that were shown when the user clicked. Nothing is confirmed here.

Only a position inside a unit that is still in the candidate is offered: a removed folder has
its own question (Yes, remove). A removal or rename answer that touches the channel elsewhere
does not confirm its move either: section 4.6 keys a re-confirmation by ID, so that answer would
accept the move unseen; the move is still listed and still needs its own click.

A saved header name the candidate no longer holds is the other half of a channel's entity
(section 4.6, AQ1): :func:`name_changes` lists each such channel with its saved names and its
names now, and only the user's own click confirms it (issue #281), whether the loss comes alone
or with a removal or rename. The published file keeps no header names per folder, so a removal
cannot tell a name only the removed folder gave from a name another folder no longer gives: no
removal or rename answer confirms a lost header name. A channel that both moved and lost a name
is in both lists and needs both clicks; publication gets one record per ID.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .description import Description
from .description_rules import Refusal
from .description_transition import CHANNELS, Reconfirmation, transition_issues

Unit = tuple[str, str, str]
"""A unit by its IDs as the description holds them: condition, source repeat, technical repeat."""


@dataclass(frozen=True, order=True)
class Moved:
    """One unit position a channel held: the unit, the index saved and its index now (or None)."""

    unit: Unit
    saved: int
    now: int | None


@dataclass(frozen=True, order=True)
class NameChange:
    """A confirmed channel that lost a saved header name: its names saved and its names now."""

    channel: str
    saved: tuple[str, ...]
    now: tuple[str, ...]


@dataclass(frozen=True, order=True)
class ChannelMove:
    """A confirmed channel whose saved positions are not all its positions in the candidate."""

    channel: str
    moved: tuple[Moved, ...]


def _units(description: Description) -> dict[Unit, tuple[str, ...]]:
    """Return each unit's ordered channel IDs, by its IDs."""
    return {
        (cond, member.source_repeat, tid): tuple(unit.channels)
        for repeat in description.repeats.values()
        for cond, member in repeat.members.items()
        for tid, unit in member.technical_repeats.items()
    }


def _move(channel: str, saved: Mapping[Unit, tuple], now: Mapping[Unit, tuple]) -> ChannelMove:
    """Return the positions ``channel`` held in ``saved`` that it no longer holds ``now``."""
    moved = []
    for unit, channels in sorted(saved.items()):
        current = now.get(unit)
        if current is None or channel not in channels:
            continue
        index = channels.index(channel)  # a unit holds a channel once (the reader's rule)
        if index >= len(current) or current[index] != channel:
            moved.append(Moved(unit, index, current.index(channel) if channel in current else None))
    return ChannelMove(channel, tuple(moved))


def channel_moves(published: Description, candidate: Description) -> tuple[ChannelMove, ...]:
    """Return the confirmed channels re-ordered within their units, each with its moves.

    A channel is listed when publication would refuse it as a changed entity without any
    re-confirmation (``R_CONFIRMED_ID_CHANGED`` with no new ID) and it lost a position in a unit
    the candidate still has (positions in removed units are not listed). A removal or rename
    answer does not take a channel off this list, and neither does a lost header name: that is
    asked on its own (:func:`name_changes`).
    """
    refused = {
        issue.old_id
        for issue in transition_issues(published, candidate)
        if issue.code == Refusal.R_CONFIRMED_ID_CHANGED
        and issue.kind == CHANNELS
        and issue.new_id is None
    }
    saved, now = _units(published), _units(candidate)
    out = []
    for channel in sorted(refused):
        move = _move(channel, saved, now)
        if move.moved:
            out.append(move)
    return tuple(out)


def name_changes(published: Description, candidate: Description) -> tuple[NameChange, ...]:
    """Return the confirmed channels that lost a saved header name, in channel ID order.

    The comparison is publication's own (section 4.6 (c): a saved ``header_names`` entry the
    candidate does not hold, compared as stored), so a channel is listed exactly when that loss
    alone makes publication refuse it as ``R_CONFIRMED_ID_CHANGED``. A name added is no change.
    """
    out = []
    for channel in sorted(published.channels.keys() & candidate.channels.keys()):
        saved = tuple(sorted(published.channels[channel].header_names or ()))
        now = tuple(sorted(candidate.channels[channel].header_names or ()))
        if not set(saved) <= set(now):
            out.append(NameChange(channel, saved, now))
    return tuple(out)


def move_confirmations(
    changes: Sequence[ChannelMove | NameChange], clicked: Mapping[ChannelMove | NameChange, str]
) -> tuple[Reconfirmation, ...]:
    """Return the re-confirmations of the changes the user confirmed, as they were shown.

    ``changes`` are moves or header-name changes; ``clicked`` maps each one the user confirmed
    to the date of the click. A change that differs since (another re-order, another name lost)
    is not the one confirmed and gets no record.
    """
    return tuple(
        Reconfirmation(CHANNELS, change.channel, None, clicked[change])
        for change in changes
        if change in clicked
    )


__all__ = [
    "ChannelMove",
    "Moved",
    "NameChange",
    "Unit",
    "channel_moves",
    "move_confirmations",
    "name_changes",
]
