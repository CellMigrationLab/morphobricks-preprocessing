"""The ``R_*`` refusals of a candidate description against the published one (experiment-v1).

Sections 4.4a, 4.6, 4.9 and 10: a candidate is compared with the file it replaces. The result is
the whole set of refusals that apply (never the first), each naming the kind of ID and the IDs.
A user's recorded re-confirmation (:class:`Reconfirmation`) is the only thing that accepts a
changed entity, and only the exact change it names; nothing else is waived, and no ID, entity or
date is repaired or guessed. Pure: it compares two models and touches no file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .description import USED_KINDS, Description
from .description_rules import Refusal

CONDITIONS, CHANNELS, REPEATS, TECHNICAL = USED_KINDS
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, order=True)
class Reconfirmation:
    """The user's recorded confirmation of one change of a confirmed ID (section 4.6).

    ``old_id`` is the ID the published file holds; ``new_id`` is the ID that replaces it (a
    renamed or corrected ID), or None when the same ID loses or is reassigned something it
    held (a removed member, folder or position). ``confirmed_on`` is the calendar date of the
    user's action: it is a user fact and has no default.
    """

    kind: str
    old_id: str
    new_id: str | None
    confirmed_on: str

    def __post_init__(self) -> None:
        if self.kind not in USED_KINDS:
            raise ValueError(f"a re-confirmation is of one of {USED_KINDS}, not {self.kind!r}")
        if not DATE.fullmatch(self.confirmed_on):
            raise ValueError(f"confirmed_on is YYYY-MM-DD, not {self.confirmed_on!r}")
        date.fromisoformat(self.confirmed_on)  # a real calendar day, else ValueError


@dataclass(frozen=True, order=True)
class TransitionIssue:
    """One refusal: its code, the kind of ID and the IDs concerned.

    For a changed ID ``old_id`` is the published ID and ``new_id`` the ID that replaces it (None
    when the same ID changed in place); for the ``used_ids`` refusals ``old_id`` is the ID.
    """

    code: Refusal
    kind: str
    old_id: str
    new_id: str | None = None

    @property
    def change(self) -> tuple[str, str, str | None]:
        """Return the (kind, old ID, new ID) a re-confirmation must name to accept it."""
        return (self.kind, self.old_id, self.new_id)


def _declared(description: Description) -> dict[str, set[str]]:
    """Return the IDs each kind declares in the file."""
    units = {
        unit
        for repeat in description.repeats.values()
        for member in repeat.members.values()
        for unit in member.technical_repeats
    }
    return {
        CONDITIONS: set(description.conditions),
        CHANNELS: set(description.channels),
        REPEATS: set(description.repeats),
        TECHNICAL: units,
    }


def _member_folders(description: Description) -> dict[str, frozenset[tuple[str, str]]]:
    """Return, per repeat ID, the member folders ``(condition ID, source repeat)``."""
    return {
        key: frozenset((cond, member.source_repeat) for cond, member in repeat.members.items())
        for key, repeat in description.repeats.items()
    }


def _repeat_folders_by_condition(description: Description) -> dict[str, set[str]]:
    """Return, per condition ID, the source repeat folders it has over all repeats."""
    out: dict[str, set[str]] = {key: set() for key in description.conditions}
    for repeat in description.repeats.values():
        for cond, member in repeat.members.items():
            out.setdefault(cond, set()).add(member.source_repeat)
    return out


def _technical(description: Description) -> dict[str, tuple[set[str], set[tuple[str, str]]]]:
    """Return, per technical repeat ID, its source folders and the member folders declaring it."""
    out: dict[str, tuple[set[str], set[tuple[str, str]]]] = {}
    for repeat in description.repeats.values():
        for cond, member in repeat.members.items():
            for tid, unit in member.technical_repeats.items():
                folders, members = out.setdefault(tid, (set(), set()))
                folders.add(unit.source_folder)
                members.add((cond, member.source_repeat))
    return out


def _positions(description: Description) -> dict[str, set[tuple[str, str, str, int]]]:
    """Return, per channel ID, its positions ``(condition, source repeat, technical, index)``."""
    out: dict[str, set[tuple[str, str, str, int]]] = {}
    for repeat in description.repeats.values():
        for cond, member in repeat.members.items():
            for tid, unit in member.technical_repeats.items():
                for index, channel in enumerate(unit.channels):
                    out.setdefault(channel, set()).add((cond, member.source_repeat, tid, index))
    return out


def _evidence(description: Description, channel: str) -> frozenset[str]:
    return frozenset(description.channels[channel].header_names or ())


def _used(old: Description, new: Description) -> list[TransitionIssue]:
    """Section 4.9: an ID dropped from ``used_ids``, or a retired ID declared again."""
    out = []
    old_declared, new_declared = _declared(old), _declared(new)
    for kind in USED_KINDS:
        old_used, new_used = set(old.used_ids[kind]), set(new.used_ids[kind])
        out += [TransitionIssue(Refusal.R_USED_ID_DROPPED, kind, i) for i in old_used - new_used]
        retired = old_used - old_declared[kind]
        out += [
            TransitionIssue(Refusal.R_USED_ID_REUSED, kind, i) for i in retired & new_declared[kind]
        ]
    return out


def _renamed(old: Description, new: Description) -> list[TransitionIssue]:
    """Section 4.6 (a), (b) and (b2): the same entity under another ID."""
    changed = Refusal.R_CONFIRMED_ID_CHANGED
    out = []
    old_by_folders = {folders: key for key, folders in _member_folders(old).items()}
    for key, folders in _member_folders(new).items():
        if folders in old_by_folders and old_by_folders[folders] != key:
            out.append(TransitionIssue(changed, REPEATS, old_by_folders[folders], key))
    old_positions, new_positions = _positions(old), _positions(new)
    for channel, positions in old_positions.items():
        if channel in new.channels:
            continue
        for other, other_positions in new_positions.items():
            same_evidence = _evidence(old, channel) == _evidence(new, other)
            if other not in old.channels and other_positions == positions and same_evidence:
                out.append(TransitionIssue(changed, CHANNELS, channel, other))
    old_folder = {c.source_folder: key for key, c in old.conditions.items()}
    for key, condition in new.conditions.items():
        folder = condition.source_folder
        if folder in old_folder and old_folder[folder] != key:
            out.append(TransitionIssue(changed, CONDITIONS, old_folder[folder], key))
    old_unit = {f: tid for tid, (folders, _) in _technical(old).items() for f in folders}
    for tid, (folders, _) in _technical(new).items():
        out += [
            TransitionIssue(changed, TECHNICAL, old_unit[f], tid)
            for f in sorted(folders)
            if f in old_unit and old_unit[f] != tid
        ]
    return out


def _lost(old: Description, new: Description) -> list[TransitionIssue]:
    """Section 4.6 (c): an ID declared in both files whose entity lost or reassigned something."""
    changed = Refusal.R_CONFIRMED_ID_CHANGED
    out = []
    old_members, new_members = _member_folders(old), _member_folders(new)
    out += [
        TransitionIssue(changed, REPEATS, key)
        for key in old_members.keys() & new_members.keys()
        if not old_members[key] <= new_members[key]
    ]
    old_by_condition = _repeat_folders_by_condition(old)
    new_by_condition = _repeat_folders_by_condition(new)
    for key in old.conditions.keys() & new.conditions.keys():
        moved = old.conditions[key].source_folder != new.conditions[key].source_folder
        if moved or not old_by_condition[key] <= new_by_condition[key]:
            out.append(TransitionIssue(changed, CONDITIONS, key))
    old_unit, new_unit = _technical(old), _technical(new)
    for tid in old_unit.keys() & new_unit.keys():
        if old_unit[tid][0] != new_unit[tid][0] or not old_unit[tid][1] <= new_unit[tid][1]:
            out.append(TransitionIssue(changed, TECHNICAL, tid))
    old_positions, new_positions = _positions(old), _positions(new)
    for key in old.channels.keys() & new.channels.keys():
        lost_position = not old_positions.get(key, set()) <= new_positions.get(key, set())
        if lost_position or not _evidence(old, key) <= _evidence(new, key):
            out.append(TransitionIssue(changed, CHANNELS, key))
    return out


def repeat_layout(description: Description, key: str) -> frozenset:
    """Return what reopens a repeat's confirmation (section 10): members, units, channels, type.

    A member is its condition's source folder and ID, its source repeat folder, and each unit's
    technical-repeat ID and folder, ordered channels, image type and ``rgb_header_override`` as
    written (added, removed or changed reopens it: the key is part of the confirmed layout,
    4.4b, owner answer of 14 November 2026). The image count is not part of it: new FOVs in an
    existing unit do not reopen it.
    """
    repeat = description.repeats[key]
    return frozenset(
        (
            cond,
            description.conditions[cond].source_folder,
            member.source_repeat,
            tid,
            unit.source_folder,
            unit.channels,
            unit.image_type,
            unit.rgb_header_override,
        )
        for cond, member in repeat.members.items()
        for tid, unit in member.technical_repeats.items()
    )


def _dates(old: Description, new: Description) -> list[TransitionIssue]:
    """Section 4.4a: ``confirmed_on`` of a repeat that was not reopened must not change."""
    return [
        TransitionIssue(Refusal.R_CONFIRMED_ON_CHANGED, REPEATS, key)
        for key in old.repeats.keys() & new.repeats.keys()
        if old.repeats[key].confirmed_on != new.repeats[key].confirmed_on
        and repeat_layout(old, key) == repeat_layout(new, key)
    ]


def transition_issues(old: Description, new: Description) -> tuple[TransitionIssue, ...]:
    """Return every refusal that applies to ``new`` offered over the published ``old``.

    The set is complete (section 4.6, "publication reports a set"), without duplicates and in
    a fixed order. An empty result means the candidate is accepted, before any re-confirmation
    is considered: the changed-entity refusals are accepted by :func:`unwaived`.
    """
    found = _used(old, new) + _renamed(old, new) + _lost(old, new) + _dates(old, new)
    return tuple(sorted(set(found)))


def unwaived(
    issues: tuple[TransitionIssue, ...], confirmations: tuple[Reconfirmation, ...]
) -> tuple[TransitionIssue, ...]:
    """Return the issues the re-confirmations do not accept.

    A re-confirmation accepts a changed entity (``R_CONFIRMED_ID_CHANGED``) only when it names
    the same kind, old ID and new ID. It accepts no other refusal.
    """
    named = {(c.kind, c.old_id, c.new_id) for c in confirmations}
    return tuple(
        i for i in issues if i.code != Refusal.R_CONFIRMED_ID_CHANGED or i.change not in named
    )


def unused(
    issues: tuple[TransitionIssue, ...], confirmations: tuple[Reconfirmation, ...]
) -> tuple[Reconfirmation, ...]:
    """Return the re-confirmations that accept no change of the candidate."""
    needed = {i.change for i in issues if i.code == Refusal.R_CONFIRMED_ID_CHANGED}
    return tuple(c for c in confirmations if (c.kind, c.old_id, c.new_id) not in needed)
