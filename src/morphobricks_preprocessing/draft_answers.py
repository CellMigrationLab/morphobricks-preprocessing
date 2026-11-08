"""The user's answers to a draft, and the description built from a draft that is fully answered.

A :class:`.draft.Draft` is what discovery can say; the answers are what the user said: the IDs
they confirmed, the date of each repeat's confirmation, the channel of every C index of every
imaging unit, the declared masks, optional references and display names. They are kept apart
from the draft because the draft is derived again from the folder (or from a legacy design) on
every resume, while the answers are the only thing the user typed or clicked. The answers are
keyed by the draft's own stable keys (source folder labels, the pairing label of a repeat), so
an answer for a folder the draft no longer has is refused (``unknown_key``), never carried over
to another folder.

:func:`description_from_draft` is pure: it reads no file and no clock, applies the answers to the
draft and returns a :class:`.description.Description`, or refuses with **every** confirmation
still missing (:class:`DraftIncomplete`). It confirms nothing on the user's behalf: no ID is
adopted from a proposal, no channel is guessed from a header name, no date is filled in. The
description it returns is not yet validated: :func:`.run.description_file.publish_description`
runs the strict reader on its exact bytes before anything is written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from types import MappingProxyType

from .description import USED_KINDS, Channel, Condition, Description, Mask, Member, Repeat, Unit
from .description_transition import DATE
from .draft import Draft, DraftError, OpenQuestion

UnitKey = tuple[str, str, str]
"""``(condition folder, source repeat folder, technical-repeat folder or "single")``."""


@dataclass(frozen=True)
class ConditionAnswer:
    """The answers about one condition folder; ``reference`` is the folder it is compared to."""

    id: str | None = None
    display_name: str | None = None
    reference: str | None = None
    target: str | None = None
    treatment_key: str | None = None


@dataclass(frozen=True)
class RepeatAnswer:
    """The answers about one repeat (by pairing label): its ID and the N35 confirmation date.

    ``confirmed_on`` is the calendar day of the user's confirmation, given by the caller.
    """

    id: str | None = None
    confirmed_on: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class UnitAnswer:
    """The channel ID of each C index of one imaging unit, in order, and its optional type."""

    channels: tuple[str, ...]
    image_type: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class ChannelAnswer:
    """One declared channel; ``header_names`` are the aliases the user confirmed."""

    display_name: str | None = None
    header_names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class MaskAnswer:
    """One declared mask: its source type and the representation the user confirmed."""

    source_type: str
    representation: str
    source_channels: tuple[str, ...] | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class DraftAnswers:
    """Everything the user has answered so far, plus what the file records about its base.

    ``base_sha256`` is the hex SHA-256 of the published ``morphobricks.toml`` the draft was
    derived from, or ``"absent"``; ``spec_commit`` the Spec commit the rules are pinned to.
    ``technical_repeats`` maps a technical-repeat folder to its confirmed ID.
    """

    base_sha256: str
    spec_commit: str
    conditions: Mapping[str, ConditionAnswer] = field(default_factory=dict)
    technical_repeats: Mapping[str, str] = field(default_factory=dict)
    repeats: Mapping[str, RepeatAnswer] = field(default_factory=dict)
    units: Mapping[UnitKey, UnitAnswer] = field(default_factory=dict)
    channels: Mapping[str, ChannelAnswer] = field(default_factory=dict)
    masks: Mapping[str, MaskAnswer] = field(default_factory=dict)


class DraftIncomplete(DraftError):
    """Raised when a draft cannot become a description; ``questions`` is every missing item."""

    def __init__(self, questions: tuple[OpenQuestion, ...]) -> None:
        self.questions = questions
        shown = "; ".join(f"{q.code}: {q.subject}" for q in questions)
        super().__init__(
            f"the draft is not fully confirmed ({len(questions)} missing): {shown}",
            tuple(dict.fromkeys(q.code for q in questions)),
        )


def valid_day(text: str) -> bool:
    """Return True when ``text`` is a real calendar day written ``YYYY-MM-DD``."""
    if not DATE.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _unit_keys(draft: Draft) -> set[UnitKey]:
    return {
        (member.condition, member.source_repeat, unit.source_folder)
        for repeat in draft.repeats.values()
        for member in repeat.members
        for unit in member.units
    }


def _unknown_keys(draft: Draft, answers: DraftAnswers) -> list[str]:
    """Name every answer whose key the draft does not have."""
    out = [f"condition {k!r}" for k in answers.conditions if k not in draft.conditions]
    out += [
        f"condition {a.reference!r} (reference)"
        for a in answers.conditions.values()
        if a.reference is not None and a.reference not in draft.conditions
    ]
    out += [
        f"technical repeat {k!r}"
        for k in answers.technical_repeats
        if k not in draft.technical_repeats
    ]
    out += [f"repeat {k!r}" for k in answers.repeats if k not in draft.repeats]
    known = _unit_keys(draft)
    out += [f"unit {' / '.join(k)}" for k in answers.units if k not in known]
    return out


def confirm_repeat(draft: Draft, label: str, confirmed_on: str) -> Draft:
    """Return the draft with the user's N35 confirmation of a repeat on the given day.

    ``confirmed_on`` is the day of the user's answer, supplied by the caller: this module never
    reads the clock.

    Raises
    ------
    DraftError
        ``unknown_key`` for a repeat the draft does not have, ``E_DATE_FORMAT`` for a day that is
        not a real ``YYYY-MM-DD`` date.
    """
    if label not in draft.repeats:
        raise DraftError(f"the draft has no repeat {label!r}", ("unknown_key",))
    if not valid_day(confirmed_on):
        raise DraftError(
            f"confirmed_on is a real day written YYYY-MM-DD, not {confirmed_on!r}",
            ("E_DATE_FORMAT",),
        )
    repeats = dict(draft.repeats)
    repeats[label] = replace(repeats[label], confirmed=True, confirmed_on=confirmed_on)
    return replace(draft, repeats=MappingProxyType(repeats))


def apply_answers(draft: Draft, answers: DraftAnswers) -> Draft:
    """Return the draft with the answers applied, or refuse the first one the rules refuse.

    IDs go through :meth:`.draft.Draft.confirm_id`, so each is checked as the reader would; an
    answer for a folder or repeat the draft does not have is refused. Applying the same answers
    twice gives the same draft. Channels, units and masks are not part of the draft and are not
    checked here (:func:`missing_confirmations`, then the strict reader at publication).

    Raises
    ------
    DraftError
        With the codes of the refusal (``unknown_key`` lists every unknown key at once).
    """
    unknown = _unknown_keys(draft, answers)
    if unknown:
        raise DraftError(f"the draft has no {', '.join(unknown)}", ("unknown_key",))
    for folder, answer in sorted(answers.conditions.items()):
        if answer.id is not None:
            draft = draft.confirm_id("conditions", folder, answer.id)
    for folder, value in sorted(answers.technical_repeats.items()):
        draft = draft.confirm_id("technical_repeats", folder, value)
    for label, repeat in sorted(answers.repeats.items()):
        if repeat.id is not None:
            draft = draft.confirm_id("repeats", label, repeat.id)
        if repeat.confirmed_on is not None:
            draft = confirm_repeat(draft, label, repeat.confirmed_on)
    references = dict(draft.references)
    references.update({f: a.reference for f, a in answers.conditions.items() if a.reference})
    return replace(draft, references=MappingProxyType(references))


def missing_confirmations(draft: Draft, answers: DraftAnswers) -> tuple[OpenQuestion, ...]:
    """Return every confirmation still missing for ``draft`` with ``answers`` applied.

    The draft's own open questions (IDs, repeats, controls, legacy folders), plus the channels
    of every imaging unit that has none (``unit_channels_missing``) and every channel a unit
    names that is not declared (``channel_undeclared``).
    """
    out = list(draft.open_questions())
    declared = set(answers.channels)
    undeclared: set[str] = set()
    for row in draft.unit_rows():
        key = (row.condition, row.biological_repeat, row.technical_repeat)
        answer = answers.units.get(key)
        if answer is None or not answer.channels:
            out.append(OpenQuestion("unit_channels_missing", " / ".join(key)))
        else:
            undeclared.update(c for c in answer.channels if c not in declared)
    return tuple(out + [OpenQuestion("channel_undeclared", c) for c in sorted(undeclared)])


def _used(draft: Draft, answers: DraftAnswers) -> dict[str, tuple[str, ...]]:
    """Return ``used_ids``: the IDs the lineage retired plus every ID confirmed now."""
    now = {
        "channels": list(answers.channels),
        "conditions": [s.confirmed for s in draft.conditions.values()],
        "technical_repeats": [s.confirmed for s in draft.technical_repeats.values()],
        "repeats": [r.id.confirmed for r in draft.repeats.values()],
    }
    return {
        kind: tuple(sorted(set(draft.used_ids.get(kind, ())) | set(now.get(kind, []))))
        for kind in USED_KINDS
    }


def _repeat(draft: Draft, label: str, answers: DraftAnswers) -> Repeat:
    """Build one repeat of the description from the draft and the unit answers."""
    repeat = draft.repeats[label]
    members: dict[str, Member] = {}
    for member in repeat.members:
        cid = draft.conditions[member.condition].confirmed
        if cid in members:
            raise DraftError(
                f"repeat {label!r} holds two folders of condition {member.condition!r}",
                ("repeat_group_conflict",),
            )
        units = {}
        for u in member.units:
            given = answers.units[(member.condition, member.source_repeat, u.source_folder)]
            units[draft.technical_repeats[u.source_folder].confirmed] = Unit(
                u.source_folder, given.channels, given.display_name, given.image_type
            )
        members[cid] = Member(member.source_repeat, member.image_count, units)
    shown = answers.repeats.get(label, RepeatAnswer()).display_name
    return Repeat(True, repeat.confirmed_on, members, shown)


def description_from_draft(draft: Draft, answers: DraftAnswers) -> Description:
    """Build the description of a draft whose every science decision the user confirmed.

    Pure: no file is read or written and no clock is read. Each ID and ``confirmed_on`` is the
    user's, from ``answers`` (or carried by a migrated ``draft``); ``used_ids`` is the draft's
    retired IDs plus every ID confirmed now.

    Raises
    ------
    DraftIncomplete
        Listing every missing confirmation, when any is missing: nothing is built.
    DraftError
        For an answer the draft does not have or the rules refuse (:func:`apply_answers`), or a
        repeat holding two folders of one condition.
    """
    applied = apply_answers(draft, answers)
    questions = missing_confirmations(applied, answers)
    if questions:
        raise DraftIncomplete(questions)
    conditions = {}
    for folder, slot in applied.conditions.items():
        answer = answers.conditions.get(folder, ConditionAnswer())
        reference = applied.references.get(folder)
        conditions[slot.confirmed] = Condition(
            folder,
            answer.display_name,
            applied.conditions[reference].confirmed if reference else None,
            answer.target,
            answer.treatment_key,
        )
    return Description(
        conditions,
        {
            key: Channel(
                a.display_name, None if a.header_names is None else tuple(sorted(a.header_names))
            )
            for key, a in answers.channels.items()
        },
        {applied.repeats[g].id.confirmed: _repeat(applied, g, answers) for g in applied.repeats},
        _used(applied, answers),
        {
            key: Mask(
                m.source_type,
                m.representation,
                None if m.source_channels is None else tuple(sorted(m.source_channels)),
                m.display_name,
            )
            for key, m in answers.masks.items()
        },
    )


__all__ = [
    "ChannelAnswer",
    "ConditionAnswer",
    "DraftAnswers",
    "DraftIncomplete",
    "MaskAnswer",
    "RepeatAnswer",
    "UnitAnswer",
    "UnitKey",
    "apply_answers",
    "confirm_repeat",
    "description_from_draft",
    "missing_confirmations",
    "valid_day",
]
