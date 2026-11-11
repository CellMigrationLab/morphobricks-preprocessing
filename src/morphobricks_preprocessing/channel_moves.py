"""Confirmed channels at new positions of their units, and their re-confirmations (pure).

Experiment-v1 section 4.6 (c): a confirmed channel ID that loses a unit position it held in the
published file (the images were re-exported with their channels in another order, and the user
re-tagged the unit) is a changed entity, ``R_CONFIRMED_ID_CHANGED``, unless the user's explicit,
recorded re-confirmation names it (old ID, ``new_id`` null, the date of the user's action). The
notebook lists each such move (:func:`channel_moves`) and records the re-confirmation only on the
user's click; :func:`move_confirmations` turns the clicks into the records publication is given,
for exactly the moves that were shown when the user clicked. Nothing is confirmed here.

Only a move inside units that are still in the candidate is offered, and only when the channel
keeps every header name it had: a removed folder has its own question (Yes, remove), and a lost
header name is not a move, so neither is waived by this confirmation.
"""

from __future__ import annotations

from collections.abc import Mapping
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


def channel_moves(
    published: Description, candidate: Description, accepted: tuple[Reconfirmation, ...] = ()
) -> tuple[ChannelMove, ...]:
    """Return the confirmed channels re-ordered within their units, each with its moves.

    A channel is listed when publication would refuse it as a changed entity
    (``R_CONFIRMED_ID_CHANGED`` with no new ID), no re-confirmation in ``accepted`` (a removal or
    a folder rename the user answered) already accepts it, it keeps every header name it had,
    and every position it lost is in a unit the candidate still has.
    """
    refused = {
        issue.old_id
        for issue in transition_issues(published, candidate)
        if issue.code == Refusal.R_CONFIRMED_ID_CHANGED
        and issue.kind == CHANNELS
        and issue.new_id is None
    }
    refused -= {c.old_id for c in accepted if c.kind == CHANNELS and c.new_id is None}
    saved, now = _units(published), _units(candidate)
    out = []
    for channel in sorted(refused):
        names = set(published.channels[channel].header_names or ())
        if not names <= set(candidate.channels[channel].header_names or ()):
            continue  # a header name lost: not a move
        lost = [u for u, held in saved.items() if channel in held and u not in now]
        move = _move(channel, saved, now)
        if move.moved and not lost:
            out.append(move)
    return tuple(out)


def move_confirmations(
    moves: tuple[ChannelMove, ...], clicked: Mapping[ChannelMove, str]
) -> tuple[Reconfirmation, ...]:
    """Return the re-confirmations of the moves the user confirmed, as they were shown.

    ``clicked`` maps each move the user confirmed to the date of the click. A move that changed
    since (another re-order) is not the one confirmed and gets no record.
    """
    return tuple(
        Reconfirmation(CHANNELS, move.channel, None, clicked[move])
        for move in moves
        if move in clicked
    )


__all__ = ["ChannelMove", "Moved", "Unit", "channel_moves", "move_confirmations"]
