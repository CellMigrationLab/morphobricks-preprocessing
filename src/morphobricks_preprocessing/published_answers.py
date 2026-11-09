"""The answers a published description holds, for reopening it in the notebook (pure).

On reopening, the published ``morphobricks.toml`` is the user's answers: every ID, shown-as name,
reference, pairing, comparability date, channel tag, image type and mask. :func:`published_answers`
turns it back into :class:`.draft_answers.DraftAnswers` keyed by the source folders (never by an
ID), so the same cards show them and the folders found now decide what still applies
(:func:`.draft_answers.carry_over`). Nothing is confirmed by this module: every value is copied
from the published file, and a comparability date is dropped (the repeat asked again) only when
the folders of that repeat changed (:func:`reopened`, experiment-v1 section 10).

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
    out = {}
    for repeat in published.repeats.values():
        for cid, member in repeat.members.items():
            folder = published.conditions[cid].source_folder
            for unit in member.technical_repeats.values():
                kind = "rgb" if unit.image_type == "rgb" else CHANNELS_TYPE
                key = (folder, member.source_repeat, unit.source_folder)
                out[key] = UnitAnswer(tuple(unit.channels), kind, unit.display_name)
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


def _members(draft: Draft, label: str) -> frozenset[UnitKey]:
    repeat = draft.repeats.get(label)
    if repeat is None:
        return frozenset()
    return frozenset(
        (m.condition, m.source_repeat, u.source_folder) for m in repeat.members for u in m.units
    )


def _published_members(published: Description) -> Mapping[str, frozenset[UnitKey]]:
    out = {}
    for rid, repeat in published.repeats.items():
        out[rid] = frozenset(
            (published.conditions[cid].source_folder, member.source_repeat, unit.source_folder)
            for cid, member in repeat.members.items()
            for unit in member.technical_repeats.values()
        )
    return out


def reopened(
    draft: Draft, answers: DraftAnswers, published: Description
) -> tuple[DraftAnswers, tuple[str, ...]]:
    """Return the answers with the comparability of every changed repeat taken back, and which.

    ``draft`` is the draft of the folders now (without answers). A repeat whose folders differ
    from the published ones (a folder joined or left it) is asked again: its ``confirmed_on`` is
    dropped, its ID and shown-as name are kept. A repeat whose folders are the same keeps its
    date byte for byte (section 4.4a), even if image counts changed.
    """
    paired = draft.paired(answers.pairing)
    before = _published_members(published)
    changed = tuple(
        sorted(
            label
            for label, answer in answers.repeats.items()
            if answer.confirmed_on is not None and _members(paired, label) != before.get(label)
        )
    )
    repeats = dict(answers.repeats)
    for label in changed:
        repeats[label] = replace(repeats[label], confirmed_on=None)
    return replace(answers, repeats=repeats), changed


__all__ = ["CHANNELS_TYPE", "published_answers", "reopened", "retired_ids"]
