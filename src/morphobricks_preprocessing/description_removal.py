"""The draft of a reconciliation and the removals the user must confirm (experiment-v1 4.6, 10).

A removed folder is removed only on the user's confirmation, which is the recorded
re-confirmation of section 4.6. One folder going away can make several confirmed IDs "changed":
the repeat that lost a member, each technical repeat and each channel that lost a position, the
condition that lost its folder. They are all one decision of the user, so they come back
**grouped** (:class:`RemovalQuestion`, one per condition): the notebook shows one question and
one answer (:class:`Removal`, with its date) accepts every confirmation in it.

Nothing is removed, renamed or defaulted without an answer, an ID that is removed stays in
``used_ids`` (section 4.9), and a ``reference`` to a removed condition is never cleared for the
user: the candidate refuses until the reference is dealt with.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import date

from .description import Condition, Description, Repeat
from .description_reconcile import (
    FolderRename,
    PublishedUnit,
    Reconciliation,
    Status,
    UnitFolders,
    published_units,
    renamed_key,
)
from .description_rules import Refusal
from .description_transition import (
    CHANNELS,
    CONDITIONS,
    DATE,
    REPEATS,
    TECHNICAL,
    Reconfirmation,
    TransitionIssue,
    transition_issues,
)

Change = tuple[str, str, str | None]
"""A changed confirmed ID as ``Reconfirmation`` names it: (kind, old ID, new ID or None)."""

POSITION = "position"
"""What a channel lost in a folder still there (:attr:`Reconfirmations.unseen`)."""


@dataclass(frozen=True)
class RemovalQuestion:
    """One question for the notebook, to remove these folders of one condition.

    ``needs`` are every re-confirmation the removal makes necessary (a repeat, technical repeats
    and channels, each by ID); ``last_member`` says the condition keeps no folder, so its ID is
    retired; ``referenced_by`` are the conditions whose ``reference`` names this one and must be
    changed first.
    """

    condition_id: str
    condition_folder: str
    units: tuple[UnitFolders, ...]
    needs: tuple[Change, ...]
    last_member: bool
    referenced_by: tuple[str, ...]

    def answer(self, confirmed_on: str) -> Removal:
        """Return the user's "Yes, remove" for this question, made on ``confirmed_on``."""
        return Removal(self.units, confirmed_on)


@dataclass(frozen=True)
class Removal:
    """The user's confirmation that these published units are removed (a date, never defaulted)."""

    units: tuple[UnitFolders, ...]
    confirmed_on: str

    def __post_init__(self) -> None:
        if not DATE.fullmatch(self.confirmed_on):
            raise ValueError(f"confirmed_on is YYYY-MM-DD, not {self.confirmed_on!r}")
        date.fromisoformat(self.confirmed_on)


@dataclass(frozen=True)
class Reconfirmations:
    """The records the candidate needs: those the user's answers cover, and those they do not.

    ``unseen`` are the channel changes in folders still there that keep an ID open although an
    answer touches it: each the channel ID, the folder and what it lost there (``POSITION``;
    issue #279). A lost header name is not among them: it is asked on Channels on its own
    (:func:`.channel_moves.name_changes`, issue #281) and keeps the ID open until confirmed.
    """

    accepted: tuple[Reconfirmation, ...]
    open: tuple[TransitionIssue, ...]
    unseen: tuple[tuple[str, UnitFolders, str], ...] = ()


def _moves(
    units: Iterable[UnitFolders], renames: Iterable[FolderRename]
) -> dict[UnitFolders, UnitFolders]:
    """Return each published unit's folders after the renames, in the order stated."""
    now = {key: key for key in units}
    for rename in renames:
        now = {key: renamed_key(current, rename) for key, current in now.items()}
    return now


def _build(
    published: Description, rec: Reconciliation, removed: Collection[UnitFolders]
) -> Description:
    """Return the published file reopened, renamed and with ``removed`` taken out (no checks)."""
    now = _moves(published_units(published), rec.renames)
    seen = {u.folders: u.image_count for u in rec.units if u.status == Status.UNCHANGED}
    repeats, folders = {}, {}
    for repeat_id, repeat in published.repeats.items():
        members = {}
        for condition_id, member in repeat.members.items():
            kept, counts, after = {}, [], None
            for tid, unit in member.technical_repeats.items():
                key = UnitFolders(
                    published.conditions[condition_id].source_folder,
                    member.source_repeat,
                    unit.source_folder,
                )
                if key not in removed:
                    after = now[key]
                    kept[tid] = replace(unit, source_folder=after.technical)
                    counts.append(seen.get(after))
            if after is None:
                continue
            folders[condition_id] = after.condition
            total = member.image_count if None in counts else sum(counts)
            members[condition_id] = replace(
                member, source_repeat=after.source_repeat, technical_repeats=kept, image_count=total
            )
        if members:
            confirmed = repeat.confirmed and repeat_id not in rec.reopened
            repeats[repeat_id] = replace(repeat, members=members, confirmed=confirmed)
    return replace(published, repeats=repeats, conditions=_conditions(published, repeats, folders))


def _conditions(
    published: Description, repeats: dict[str, Repeat], folders: dict[str, str]
) -> dict[str, Condition]:
    """Return the conditions that keep a member, with their renamed folders; others are retired."""
    held = {cond for repeat in repeats.values() for cond in repeat.members}
    had = {cond for repeat in published.repeats.values() for cond in repeat.members}
    return {
        key: replace(condition, source_folder=folders.get(key, condition.source_folder))
        for key, condition in published.conditions.items()
        if key in held or key not in had
    }


def _changes(published: Description, built: Description) -> tuple[TransitionIssue, ...]:
    """Return the changed-entity refusals (the ones a re-confirmation accepts) of a candidate."""
    return tuple(
        issue
        for issue in transition_issues(published, built)
        if issue.code == Refusal.R_CONFIRMED_ID_CHANGED
    )


def _affected(issue: TransitionIssue, units: dict[UnitFolders, PublishedUnit]) -> set[UnitFolders]:
    """Return the published units that hold the changed ID."""
    matches = {
        CONDITIONS: lambda u: u.condition_id == issue.old_id,
        REPEATS: lambda u: u.repeat_id == issue.old_id,
        TECHNICAL: lambda u: u.technical_id == issue.old_id,
        CHANNELS: lambda u: issue.old_id in u.unit.channels,
    }[issue.kind]
    return {key for key, unit in units.items() if matches(unit)}


def removal_questions(published: Description, rec: Reconciliation) -> tuple[RemovalQuestion, ...]:
    """Return one grouped question per condition that has removed units, in condition ID order.

    Each lists every re-confirmation its removal needs (each lost repeat, technical repeat and
    channel by ID, and the condition's own when it lost its folder), so the notebook asks once.
    A need that several conditions share belongs to the first of them.
    """
    units = published_units(published)
    removed = {u.folders for u in rec.removed}
    built = _build(published, rec, removed)
    issues = _changes(published, built)
    by_condition: dict[str, set[UnitFolders]] = {}
    for key in sorted(removed):
        by_condition.setdefault(units[key].condition_id, set()).add(key)
    assigned: set[Change] = set()
    out = []
    for condition_id, held in sorted(by_condition.items()):
        needs = []
        for issue in issues:
            if issue.change not in assigned and _affected(issue, units) & held:
                assigned.add(issue.change)
                needs.append(issue.change)
        everything = {k for k, u in units.items() if u.condition_id == condition_id}
        refs = sorted(k for k, c in published.conditions.items() if c.reference == condition_id)
        out.append(
            RemovalQuestion(
                condition_id,
                published.conditions[condition_id].source_folder,
                tuple(sorted(held)),
                tuple(sorted(needs, key=lambda n: (n[0], n[1], n[2] or ""))),
                everything <= held,
                tuple(refs),
            )
        )
    return tuple(out)


def candidate(
    published: Description, rec: Reconciliation, removals: Iterable[Removal] = ()
) -> Description:
    """Return the draft: the published file as the folder and the user's answers leave it.

    Repeats the reconciliation reopened have ``confirmed = False`` and keep the published
    ``confirmed_on``; the units the user confirmed removed (and a condition or repeat left
    without members) are gone and their IDs stay in ``used_ids``; stated renames are applied;
    pending new units are not in it. Nothing is published by this.

    Raises
    ------
    ValueError
        When a removal names a unit that is not a removed unit of the reconciliation, or removes
        a condition that another condition still references.
    """
    answers = tuple(removals)
    gone = {u.folders for u in rec.removed}
    asked = {key for removal in answers for key in removal.units}
    if asked - gone:
        names = ", ".join(
            f"{k.condition}/{k.source_repeat}/{k.technical}" for k in sorted(asked - gone)
        )
        raise ValueError(f"not a removed unit of this reconciliation: {names}")
    built = _build(published, rec, asked)
    dangling = sorted(
        f"{key} refers to {c.reference}"
        for key, c in built.conditions.items()
        if c.reference is not None and c.reference not in built.conditions
    )
    if dangling:
        raise ValueError("a removed condition is still referenced: " + "; ".join(dangling))
    return built


def _unanswered(
    issue: TransitionIssue,
    published: Description,
    built: Description,
    removed: set[UnitFolders],
    now: dict[UnitFolders, UnitFolders],
) -> set[tuple[UnitFolders, str]]:
    """Return the folders still there where a channel lost a position no answer covers.

    Section 4.6 keys a re-confirmation by ID only: a removal or rename answer that touches a
    channel would also accept its other changes. A position lost in a unit no answer names is a
    move, confirmed on its own (#258). A position is compared in the unit's folders after the
    renames (``now``), so a move inside a renamed folder is lost too; only a unit the user
    confirmed removed (``removed``) loses its positions by the answer. A lost header name is
    the caller's (:func:`_names_lost`).
    """
    if issue.kind != CHANNELS or issue.new_id is not None:
        return set()
    channel, units = issue.old_id, published_units(published)
    by_ids = {(u.condition_id, k.source_repeat, u.technical_id): k for k, u in units.items()}
    after = _positions_of(built, channel)
    lost = {
        key
        for cond, repeat, tid, index in _positions_of(published, channel)
        if (key := by_ids[cond, repeat, tid]) not in removed
        and (cond, now[key].source_repeat, tid, index) not in after
    }
    return {(now[key], POSITION) for key in lost}


def _names_lost(issue: TransitionIssue, published: Description, built: Description) -> bool:
    """Return whether a changed channel lost a saved header name (section 4.6 (c), #281).

    The published file keeps no header names per folder, so no removal or rename answer can
    show that the name went with its folder: only the user's own click on Channels confirms it.
    """
    if issue.kind != CHANNELS or issue.new_id is not None:
        return False
    saved = set(published.channels[issue.old_id].header_names or ())
    return not saved <= set(built.channels[issue.old_id].header_names or ())


def _positions_of(description: Description, channel: str) -> set[tuple[str, str, str, int]]:
    """Return the positions ``(condition, source repeat, technical, index)`` of one channel."""
    return {
        (cond, member.source_repeat, tid, unit.channels.index(channel))
        for repeat in description.repeats.values()
        for cond, member in repeat.members.items()
        for tid, unit in member.technical_repeats.items()
        if channel in unit.channels
    }


def reconfirmations(
    published: Description,
    built: Description,
    rec: Reconciliation,
    removals: Iterable[Removal] = (),
    names: Collection[str] = (),
) -> Reconfirmations:
    """Return the re-confirmations of ``built`` that the user's answers cover, and the rest.

    Each changed confirmed ID takes the date of the first answer that touches one of its units;
    an ID no answer touches is in ``open`` (the candidate would be refused for it). A channel
    that also lost a position in a folder still there is open as well, with those folders in
    ``unseen``: the record cannot say "this ID, only in that folder" (4.6). A channel that lost
    a saved header name is open unless it is in ``names``, the channels whose header-name change
    the user confirmed as shown (issue #281).
    """
    units = published_units(published)
    answers = [(set(r.units), r.confirmed_on) for r in removals]
    removed = {key for held, _ in answers for key in held}
    now = {key: key for key in units}
    for rename in rec.renames:
        after = {key: renamed_key(current, rename) for key, current in now.items()}
        answers.append(({key for key in units if after[key] != now[key]}, rename.confirmed_on))
        now = after
    accepted, left, unseen = set(), [], set()
    for issue in _changes(published, built):
        reached = _affected(issue, units)
        dates = [day for held, day in answers if held & reached]
        others = _unanswered(issue, published, built, removed, now) if dates else set()
        unconfirmed = _names_lost(issue, published, built) and issue.old_id not in names
        if dates and not others and not unconfirmed:
            accepted.add(Reconfirmation(issue.kind, issue.old_id, issue.new_id, dates[0]))
        else:
            left.append(issue)
            unseen |= {(issue.old_id, *other) for other in others}
    return Reconfirmations(tuple(sorted(accepted)), tuple(left), tuple(sorted(unseen)))
