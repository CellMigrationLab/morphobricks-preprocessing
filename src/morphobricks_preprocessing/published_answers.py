"""The answers a published description holds, for reopening it in the notebook (pure).

On reopening, the published ``morphobricks.toml`` is the user's answers: every ID, shown-as name,
reference, pairing, comparability date, channel tag, image type and mask. :func:`published_answers`
turns it back into :class:`.draft_answers.DraftAnswers` keyed by the source folders (never by an
ID), so the same cards show them and the folders found now decide what still applies
(:func:`.draft_answers.carry_over`). Nothing is confirmed by this module: every value is copied
from the published file, and a comparability date is dropped (the repeat asked again) only when
the candidate's layout of that repeat differs from the published one: its folders, or a unit's
declared ordered channel list or image type (:func:`reopened`, :func:`reasked`, experiment-v1
section 10). Headers that no longer fit the declared channels are the run's refusal, not a
reopening: the user fixes the description (for example a header name) and the date stays.

The draft of the folders is derived with the IDs the lineage **retired** (:func:`retired_ids`):
the IDs still in use are the published answers themselves, so they are not "used" twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from .description import USED_KINDS, Description
from .draft import Draft
from .draft_answers import (
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    MaskAnswer,
    RepeatAnswer,
    UnitAnswer,
    UnitKey,
)
from .draft_format import SPEC_COMMIT

CHANNELS_TYPE = "channels"
"""The draft's word for an ordinary channel image (the description's absent ``image_type``)."""


def _in_use(published: Description) -> dict[str, set[str]]:
    """Return the IDs the published file uses now, by ``used_ids`` kind."""
    technical = {
        tid
        for repeat in published.repeats.values()
        for member in repeat.members.values()
        for tid in member.technical_repeats
    }
    return {
        "conditions": set(published.conditions),
        "channels": set(published.channels),
        "repeats": set(published.repeats),
        "technical_repeats": technical,
    }


def retired_ids(published: Description) -> dict[str, tuple[str, ...]]:
    """Return the IDs ``used_ids`` lists that the file no longer uses (never reusable)."""
    now = _in_use(published)
    return {
        kind: tuple(sorted(set(published.used_ids.get(kind, ())) - now.get(kind, set())))
        for kind in USED_KINDS
    }


def _units(published: Description) -> dict[UnitKey, UnitAnswer]:
    """Return each published unit's answer by its folders, ``rgb_header_override`` kept."""
    out = {}
    for repeat in published.repeats.values():
        for cid, member in repeat.members.items():
            folder = published.conditions[cid].source_folder
            for unit in member.technical_repeats.values():
                kind = "rgb" if unit.image_type == "rgb" else CHANNELS_TYPE
                key = (folder, member.source_repeat, unit.source_folder)
                out[key] = UnitAnswer(
                    tuple(unit.channels), kind, unit.display_name, unit.rgb_header_override
                )
    return out


def _conditions(published: Description) -> dict[str, ConditionAnswer]:
    """Return one answer per condition folder; no reference is the answer "no comparison"."""
    out = {}
    for cid, condition in published.conditions.items():
        reference = condition.reference
        out[condition.source_folder] = ConditionAnswer(
            cid,
            condition.display_name,
            published.conditions[reference].source_folder if reference else "",
            condition.target,
            condition.treatment_key,
        )
    return out


def published_answers(published: Description, base_sha256: str) -> DraftAnswers:
    """Return the published file's answers, keyed by source folders, based on its SHA-256.

    A repeat's pairing label is its ID: a folder whose own name is the repeat ID needs no
    pairing entry, every other member is paired under the ID (the reader's
    ``E_REPEAT_ID_SHADOWS_SOURCE`` rule means no folder of that name belongs elsewhere).
    """
    pairing, repeats, technical = {}, {}, {}
    for rid, repeat in published.repeats.items():
        repeats[rid] = RepeatAnswer(rid, repeat.confirmed_on, repeat.display_name)
        for cid, member in repeat.members.items():
            key = (published.conditions[cid].source_folder, member.source_repeat)
            if member.source_repeat != rid:
                pairing[key] = rid
            for tid, unit in member.technical_repeats.items():
                technical[unit.source_folder] = tid
    return DraftAnswers(
        base_sha256,
        SPEC_COMMIT,
        conditions=_conditions(published),
        technical_repeats=technical,
        repeats=repeats,
        units=_units(published),
        channels={
            key: ChannelAnswer(c.display_name, c.header_names)
            for key, c in published.channels.items()
        },
        masks={
            key: MaskAnswer(m.source_type, m.representation, m.source_channels, m.display_name)
            for key, m in published.masks.items()
        },
        pairing=pairing,
    )


Layout = frozenset[tuple[UnitKey, tuple[str | None, ...] | None, str | None]]
"""A repeat's layout as section 10 reads it: each unit's folders, ordered channels and type."""


def _layout(answers: DraftAnswers, keys: frozenset[UnitKey]) -> Layout:
    units = answers.units
    return frozenset(
        (key, tuple(units[key].channels), units[key].image_type)
        if key in units
        else (key, None, None)
        for key in keys
    )


def layouts(draft: Draft, answers: DraftAnswers) -> dict[str, Layout]:
    """Return, per repeat label of the paired draft, its units and their declared channels.

    A unit is its folders ``(condition, source repeat, technical)`` with the ordered channel
    labels and the image type the answers declare for it: the candidate's own layout, never the
    image headers.
    """
    paired = draft.paired(answers.pairing)
    return {
        label: _layout(
            answers,
            frozenset(
                (m.condition, m.source_repeat, u.source_folder)
                for m in repeat.members
                for u in m.units
            ),
        )
        for label, repeat in paired.repeats.items()
    }


def published_layouts(published: Description) -> dict[str, Layout]:
    """Return, per published repeat ID, its units and their declared channels (as answers)."""
    answers = published_answers(published, "")
    return {
        rid: _layout(
            answers,
            frozenset(
                (published.conditions[cid].source_folder, member.source_repeat, unit.source_folder)
                for cid, member in repeat.members.items()
                for unit in member.technical_repeats.values()
            ),
        )
        for rid, repeat in published.repeats.items()
    }


def reopened(
    draft: Draft, answers: DraftAnswers, published: Description
) -> tuple[DraftAnswers, tuple[str, ...]]:
    """Return the answers with the comparability of every reopened repeat taken back, and which.

    Experiment-v1 section 10 (N35 reopening): "The confirmation of a repeat is reopened (draft
    ``confirmed = false``) only when channels, layout or grouping change: a new or removed unit
    or member, a unit's ordered channel list, or the pairing of folders into repeats."

    ``draft`` is the draft of the folders now (without answers). The candidate these answers
    make is compared with ``published``: a repeat is asked again when its layout differs
    (:func:`layouts`: a folder joined or left it, or a unit's declared ordered channel list or
    image type differs). Its ``confirmed_on`` is dropped, its ID and shown-as name are kept.
    Every other repeat keeps its date byte for byte (section 4.4a), even if image counts
    changed, and even if the image headers no longer fit its channels: that is the run's
    refusal (``R_HEADER_CONTRADICTION``), shown at once, and the declared list did not change.
    """
    now, before = layouts(draft, answers), published_layouts(published)
    changed = tuple(
        sorted(
            label
            for label, answer in answers.repeats.items()
            if answer.confirmed_on is not None and now.get(label) != before.get(label)
        )
    )
    repeats = dict(answers.repeats)
    for label in changed:
        repeats[label] = replace(repeats[label], confirmed_on=None)
    return replace(answers, repeats=repeats), changed


def _declared(layout: Layout | None) -> bool:
    """Return whether every unit of a layout has its whole ordered channel list and its type."""
    return layout is not None and all(
        channels is not None and None not in channels and kind is not None
        for _, channels, kind in layout
    )


def confirmed_layouts(draft: Draft, answers: DraftAnswers) -> dict[str, Layout]:
    """Return the layout of every repeat that has its date and declares every unit's channels.

    The layout a "same way?" answer stands for, as long as the answers hold both.
    """
    return {
        label: layout
        for label, layout in layouts(draft, answers).items()
        if _declared(layout)
        and label in answers.repeats
        and answers.repeats[label].confirmed_on is not None
    }


def reasked(
    draft: Draft,
    before: DraftAnswers,
    after: DraftAnswers,
    published: Description | None,
    given: Mapping[str, Layout] | None = None,
) -> tuple[DraftAnswers, tuple[str, ...]]:
    """Return ``after`` with the date of every repeat an answer just reopened taken back.

    A repeat is reopened by this answer when its layout (:func:`layouts`) differs between
    ``before`` and ``after``, ``after`` declares every unit's whole channel list and type, and
    that layout is not the published one (section 10): the user changed a unit's declared
    channels, its type or the grouping. Its ``confirmed_on`` is dropped, so its "same way?" is
    asked again; nothing is ever ticked here. A layout with an untagged channel declares no
    list yet (a tag being changed passes through it), so it reopens nothing by itself: the
    candidate cannot be saved while a tag is missing.

    ``given`` are the layouts the user answered "same way?" for in the draft
    (:func:`confirmed_layouts` of the answers that last held a whole layout with its date): a
    repeat that comes back to that layout keeps its date, as one that comes back to the
    published layout does. Without a published file (``published`` None) no layout is the
    published one: a "same way?" answered in the draft stands for the layout it was given for,
    and for no other.
    """
    old, new = layouts(draft, before), layouts(draft, after)
    saved = {} if published is None else published_layouts(published)
    answered = given or {}
    changed = tuple(
        sorted(
            label
            for label, answer in after.repeats.items()
            if answer.confirmed_on is not None
            and _declared(new.get(label))
            and new.get(label) != old.get(label)
            and new.get(label) != saved.get(label)
            and new.get(label) != answered.get(label)
        )
    )
    repeats = dict(after.repeats)
    for label in changed:
        repeats[label] = replace(repeats[label], confirmed_on=None)
    return replace(after, repeats=repeats), changed


def resumed(
    draft: Draft, saved: DraftAnswers, kept: DraftAnswers
) -> tuple[DraftAnswers, tuple[str, ...]]:
    """Return ``kept`` with the "same way?" taken back where the folders are not those answered.

    ``saved`` are the answers as the draft file holds them and ``kept`` those that still apply
    to ``draft``, the draft of the folders now (:func:`.draft_answers.carry_over`). The file
    does not hold the folders a "same way?" was answered for, only the answers of each unit.
    A repeat's date is kept only when every unit it has now has its whole saved channel list
    and type, and no unit with a saved answer has left it (section 10: a new or removed unit or
    member reopens the confirmation). A folder that joined the repeat has no saved answer, so
    the repeat is asked again; so is a repeat confirmed before its channels were all tagged
    (tagging them reopens it anyway, :func:`reasked`). Nothing is ticked here.
    """
    now = layouts(draft, kept)
    left = {saved.pairing.get(key[:2], key[1]) for key in saved.units if key not in kept.units}
    changed = tuple(
        sorted(
            label
            for label, answer in kept.repeats.items()
            if answer.confirmed_on is not None and (label in left or not _declared(now.get(label)))
        )
    )
    repeats = dict(kept.repeats)
    for label in changed:
        repeats[label] = replace(repeats[label], confirmed_on=None)
    return replace(kept, repeats=repeats), changed


def kept_date(
    draft: Draft, answers: DraftAnswers, published: Description, label: str
) -> str | None:
    """Return the published ``confirmed_on`` of a repeat whose confirmation is not reopened.

    Section 10: "``confirmed_on`` is kept byte-identical for every repeat whose confirmation is
    not reopened (section 4.4a); the draft copies it from the published file, never from the
    clock." None when the repeat is not published or its layout differs from the published one
    (the answer then takes the day of the click).
    """
    repeat = published.repeats.get(label)
    if repeat is None or layouts(draft, answers).get(label) != published_layouts(published).get(
        label
    ):
        return None
    return repeat.confirmed_on


__all__ = [
    "CHANNELS_TYPE",
    "confirmed_layouts",
    "kept_date",
    "layouts",
    "published_answers",
    "published_layouts",
    "reasked",
    "reopened",
    "resumed",
    "retired_ids",
]
