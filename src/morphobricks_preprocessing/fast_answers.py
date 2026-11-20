"""The answers of the one-command fast path of ``describe`` (Spec ADR 0006, FC1; pure).

Fast mode is the default of the command line for a **new** description (owner decision FC1,
experiment-v1 4.3a): ``describe EXPERIMENT --channels DNA,ACTIN --image-type channels
--accept-ids --comparable --confirmed-on YYYY-MM-DD --publish``. Every answer is the user's,
stated in the command:

- ``--channels`` is the ordered channel list of the **one tagged unit**, the first imaging unit
  in folder order (named in the output); its header names are confirmed as the channels'
  ``header_names`` (FC3 evidence), never matched by similarity;
- ``--image-type`` is the list's image type, never inferred;
- ``--accept-ids`` accepts every proposed ID (a folder with no proposal stops publication);
- ``--comparable`` is the N35 answer for every biological repeat;
- ``--confirmed-on`` is the day of those statements, never the clock's.

Every unit is drafted with ``channels_from = "experiment"`` and the list materialised; a unit
that contradicts the list is refused at publication with ``R_FAST_LAYOUT_CONTRADICTION``, never
switched to ``unit`` here. Nothing here reads a file or the clock.
"""

from __future__ import annotations

from collections.abc import Sequence

from .draft import Draft
from .draft_answers import (
    CHANNELS,
    ChannelAnswer,
    ConditionAnswer,
    DraftAnswers,
    LayoutAnswer,
    RepeatAnswer,
    UnitKey,
    valid_day,
)
from .draft_format import SPEC_COMMIT

RGB_LIST = ("R", "G", "B")


def tagged_unit(draft: Draft) -> UnitKey | None:
    """Return the one tagged unit: the first imaging unit in folder order (None when none)."""
    rows = sorted(
        (row.condition, row.biological_repeat, row.technical_repeat) for row in draft.unit_rows()
    )
    return rows[0] if rows else None


def _ids(draft: Draft) -> tuple[dict, dict, dict, list[str]]:
    """Return the proposed IDs as answers, and every folder that has no proposal."""
    missing: list[str] = []
    conditions = {}
    for folder, slot in sorted(draft.conditions.items()):
        if slot.proposed is None:
            missing.append(
                f"condition folder {folder!r} has no proposed ID: name it in the notebook"
            )
        conditions[folder] = ConditionAnswer(slot.proposed)
    technical = {}
    for folder, slot in sorted(draft.technical_repeats.items()):
        if slot.proposed is None:
            missing.append(f"technical repeat folder {folder!r} has no proposed ID")
        else:
            technical[folder] = slot.proposed
    repeats = {}
    for label, repeat in sorted(draft.repeats.items()):
        if repeat.id.proposed is None:
            missing.append(f"repeat {label!r} has no proposed ID")
        repeats[label] = RepeatAnswer(repeat.id.proposed)
    return conditions, technical, repeats, missing


def fast_answers(
    draft: Draft,
    channels: Sequence[str],
    image_type: str,
    header_names: Sequence[str | None],
    *,
    accept_ids: bool,
    comparable: bool,
    confirmed_on: str | None,
) -> tuple[DraftAnswers | None, list[str]]:
    """Return the fast-mode answers the command states, or None with everything missing.

    ``header_names`` are the tagged unit's header channel names as read (None or ``""`` where
    the header names none); a non-empty one is confirmed for the channel at its index.
    """
    blockers = []
    if not accept_ids:
        blockers.append("accept the proposed IDs (--accept-ids), or name them in the notebook")
    if not comparable:
        blockers.append(
            "say that every biological repeat's folders were prepared and acquired under the "
            "same circumstances (--comparable)"
        )
    if confirmed_on is None or not valid_day(confirmed_on):
        blockers.append("give the day of your statements as YYYY-MM-DD (--confirmed-on)")
    if image_type == "rgb" and tuple(channels) != RGB_LIST:
        blockers.append("a colour (RGB) list is exactly R,G,B (--channels R,G,B)")
    if len(set(channels)) != len(channels) or not channels:
        blockers.append("--channels lists each channel ID once")
    if len(header_names) not in (0, len(channels)):
        blockers.append(
            f"the tagged unit's image has {len(header_names)} named channels, --channels "
            f"lists {len(channels)}: tag it again"
        )
    conditions, technical, repeats, missing = _ids(draft)
    blockers += missing
    if blockers:
        return None, blockers
    day = str(confirmed_on)
    named = dict(zip(channels, header_names, strict=False)) if image_type != "rgb" else {}
    declared = {
        cid: ChannelAnswer(header_names=(named[cid],) if named.get(cid) else None)
        for cid in channels
    }
    return (
        DraftAnswers(
            "absent",
            SPEC_COMMIT,
            conditions=conditions,
            technical_repeats=technical,
            repeats={k: RepeatAnswer(v.id, day) for k, v in repeats.items()},
            channels=declared,
            channel_layout=LayoutAnswer(
                tuple(channels), "rgb" if image_type == "rgb" else CHANNELS, day
            ),
        ),
        [],
    )


__all__ = ["fast_answers", "tagged_unit"]
