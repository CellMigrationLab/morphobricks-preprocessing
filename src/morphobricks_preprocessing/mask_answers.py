"""The answers about the masks of a draft description (experiment-v1 section 4.5), pure.

One answer per mask type found: **separate objects** (``instances``), **regions, inside or
outside** (``binary``) or **grouped labels** (``parent_labels``: each non-zero value one group,
for example the mitochondria of one cell, in any number of pieces; section 4.5b), never defaulted
and never inferred from the dtype, the values, the name or the mask step's ``grouped`` kind
(section 4.5). The mask ID is the mask type's folder name when it is a valid mask ID as it stands
(Token rule, reserved list with the object-set part ``in``); otherwise there is no proposal and the
user types one (nothing is repaired). The optional *made from* channels are provenance only and
must be declared channel labels.

The answers live in :attr:`.draft_answers.DraftAnswers.masks`, keyed by mask ID, each with its
``source_type`` (the folder name). Every function here returns new answers or refuses with
:class:`MaskAnswerRefused` and the reader's codes; nothing reads a file or the clock.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import replace

from .description_identity import nfc
from .description_rules import REPRESENTATIONS
from .draft_answers import DraftAnswers, MaskAnswer, UnitKey
from .draft_ids import id_problems, propose_id

INSTANCES, BINARY, PARENT_LABELS = REPRESENTATIONS
"""``instances``: separate objects, each with its own number; ``binary``: inside or outside;
``parent_labels``: each non-zero value one group, in any number of pieces (section 4.5b)."""


class MaskAnswerRefused(ValueError):
    """Raised when a mask answer is not taken; ``codes`` are the reasons (Spec codes if any)."""

    def __init__(self, message: str, codes: tuple[str, ...] = ()) -> None:
        self.codes = codes
        super().__init__(message)


def proposed_mask_id(source_type: str, answers: DraftAnswers) -> str | None:
    """Return the folder name as the mask ID when it is valid and free, else None."""
    taken = [key for key, mask in answers.masks.items() if mask.source_type != source_type]
    proposal = propose_id("masks", source_type)
    if proposal is None or any(nfc(t).casefold() == nfc(proposal).casefold() for t in taken):
        return None
    return proposal


def mask_of_type(answers: DraftAnswers, source_type: str) -> tuple[str, MaskAnswer] | None:
    """Return ``(mask ID, answer)`` of the mask declared for a mask type, or None."""
    for key, mask in answers.masks.items():
        if mask.source_type == source_type:
            return key, mask
    return None


KIND_WORDS = {INSTANCES: "objects", BINARY: "regions", PARENT_LABELS: "grouped labels"}
"""The words the notebook uses for the answers: separate objects, regions, or grouped labels."""


def mask_kinds(answers: DraftAnswers) -> dict[str, str]:
    """Return what the user declared each mask type to be: objects, regions or grouped labels."""
    return {mask.source_type: KIND_WORDS[mask.representation] for mask in answers.masks.values()}


def mask_ids(answers: DraftAnswers) -> dict[str, str]:
    """Return the mask ID of every answered mask type, by mask type."""
    return {mask.source_type: key for key, mask in answers.masks.items()}


def _id_refusal(mask_id: str, others: Iterable[str]) -> MaskAnswerRefused | None:
    """Return the refusal of a mask ID (the reader's codes), or None when it is allowed."""
    codes = list(id_problems("masks", mask_id))
    folded = nfc(mask_id).casefold()
    clash = [o for o in others if nfc(o).casefold() == folded]
    if any(o == mask_id for o in clash):
        codes.append("id_in_use_in_draft")
    elif clash:
        codes.append("E_ID_CASE_COLLISION")
    if not codes:
        return None
    return MaskAnswerRefused(
        f"{mask_id!r} cannot be a mask ID: {', '.join(dict.fromkeys(codes))}",
        tuple(dict.fromkeys(codes)),
    )


def _channels_refusal(
    source_channels: tuple[str, ...], declared: Collection[str]
) -> MaskAnswerRefused | None:
    """Return the refusal of *made from* channels that are not declared or repeated."""
    unknown = sorted(set(source_channels) - set(declared))
    if unknown:
        return MaskAnswerRefused(
            f"{', '.join(unknown)} is not one of your channel labels", ("E_UNKNOWN_CHANNEL",)
        )
    if len(set(source_channels)) != len(source_channels):
        return MaskAnswerRefused("a channel is listed twice", ("E_DUPLICATE_VALUE",))
    return None


def with_mask(
    answers: DraftAnswers,
    source_type: str,
    mask_id: str,
    representation: str,
    source_channels: tuple[str, ...] = (),
) -> DraftAnswers:
    """Return the answers with the mask of ``source_type`` declared, replacing an earlier one.

    Parameters
    ----------
    answers
        The draft answers so far.
    source_type
        The mask type as found (its folder name), kept exactly.
    mask_id
        The ID the user accepted or typed; refused with the reader's codes, never repaired.
    representation
        ``instances``, ``binary`` or ``parent_labels``, the user's answer (there is no default).
    source_channels
        The *made from* channels, declared labels only (empty: not said).

    Raises
    ------
    MaskAnswerRefused
        ``E_ENUM`` for another representation, the ID's codes (``E_ID_SYNTAX``,
        ``E_ID_RESERVED``, ``E_ID_CASE_COLLISION``, ...), ``E_UNKNOWN_CHANNEL`` or
        ``E_DUPLICATE_VALUE`` for the channels.
    """
    if representation not in REPRESENTATIONS:
        raise MaskAnswerRefused(
            f"a mask is {' or '.join(REPRESENTATIONS)}, not {representation!r}", ("E_ENUM",)
        )
    kept = {k: v for k, v in answers.masks.items() if v.source_type != source_type}
    problem = _id_refusal(mask_id, kept) or _channels_refusal(source_channels, answers.channels)
    if problem is not None:
        raise problem
    earlier = mask_of_type(answers, source_type)
    shown = earlier[1].display_name if earlier else None
    kept[mask_id] = MaskAnswer(
        source_type, representation, tuple(sorted(source_channels)) or None, shown
    )
    return replace(answers, masks=dict(sorted(kept.items())))


def without_mask(answers: DraftAnswers, source_type: str) -> DraftAnswers:
    """Return the answers without the mask declared for ``source_type`` (if any)."""
    masks = {k: v for k, v in answers.masks.items() if v.source_type != source_type}
    return replace(answers, masks=masks)


def masks_unanswered(answers: DraftAnswers, mask_types: Iterable[str]) -> tuple[str, ...]:
    """Return the mask types found that have no answer yet, sorted."""
    answered = {mask.source_type for mask in answers.masks.values()}
    return tuple(sorted(set(mask_types) - answered))


def masks_without_files(answers: DraftAnswers, mask_types: Iterable[str]) -> tuple[str, ...]:
    """Return the declared mask types that the masks folder no longer has, sorted.

    A declared mask with files for none of the images is valid (section 4.5); the user may keep
    it or take it out.
    """
    found = set(mask_types)
    return tuple(
        sorted(m.source_type for m in answers.masks.values() if m.source_type not in found)
    )


def dropped_channels(answers: DraftAnswers) -> Mapping[str, tuple[str, ...]]:
    """Return, per mask ID, the *made from* channels that are no longer declared labels."""
    declared = set(answers.channels)
    out = {}
    for key, mask in answers.masks.items():
        gone = tuple(c for c in mask.source_channels or () if c not in declared)
        if gone:
            out[key] = gone
    return out


def drawn_on(answers: DraftAnswers, source_type: str, unit: UnitKey | None) -> int | None:
    """Return the C index of the unit tagged with the mask's first *made from* label, or None.

    The mask is drawn on that channel; None (no *made from* answer, or the unit has no such
    tag) means the first channel, which the screen says.
    """
    found = mask_of_type(answers, source_type)
    tags = answers.units.get(unit) if unit is not None else None
    if found is None or tags is None:
        return None
    for label in sorted(found[1].source_channels or ()):
        if label in tags.channels:
            return tags.channels.index(label)
    return None


__all__ = [
    "BINARY",
    "INSTANCES",
    "KIND_WORDS",
    "PARENT_LABELS",
    "MaskAnswerRefused",
    "drawn_on",
    "dropped_channels",
    "mask_ids",
    "mask_kinds",
    "mask_of_type",
    "masks_unanswered",
    "masks_without_files",
    "proposed_mask_id",
    "with_mask",
    "without_mask",
]
