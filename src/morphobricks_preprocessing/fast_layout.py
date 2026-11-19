"""Fast-mode checks of one image against the experiment channel list (Spec ADR 0006).

In a fast-mode description (``channel_layout``, experiment-v1 section 4.3a) every image of a
unit with ``channels_from = "experiment"`` is compared with the one experiment list, from its
**header** only (the channel count, the channel names the format states, its colour
interpretation and samples per pixel), before any pixel of any image is read (owner decisions
FC3 and FC4, experiment-v1 section 8, "Fast-mode checks"). Each image is checked for every kind
that can be checked on it, and all that apply are reported, in this order:

- ``image_type``: the image's effective type contradicts the list's (the test of
  ``R_IMAGE_TYPE_CONTRADICTS_HEADER``; a format that states no colour interpretation cannot);
- ``channel_count``: its channel count differs from the list's length;
- ``header_order``: a non-empty header name at C*i* is not evidence for the list's channel at
  *i*, and every such name is evidence for another channel of the list;
- ``header_names``: some such name is evidence for no channel of the list.

The two header kinds exclude each other and are checked at every index both the image and the
list have. A name is evidence for a channel when it equals one of the channel's
``header_names`` after NFC of both sides, code point by code point, with no trimming (section
4.3, AQ7). A missing or empty name needs no evidence: it is accepted under the user's statement
(FC3). Nothing is reordered, dropped or guessed here: an image either matches or is listed.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .description import ChannelLayout, Description

FAST_LAYOUT_CONTRADICTION = "R_FAST_LAYOUT_CONTRADICTION"
IMAGE_TYPE = "image_type"
CHANNEL_COUNT = "channel_count"
HEADER_ORDER = "header_order"
HEADER_NAMES = "header_names"
KINDS = (IMAGE_TYPE, CHANNEL_COUNT, HEADER_ORDER, HEADER_NAMES)
"""The kinds of contradiction, in the order an image's entry lists them (ADR 0006)."""
HEADER = "header"
STATEMENT = "statement"
"""The ``evidence`` of one channel of a header-checked image: its header name is a confirmed
name of the channel (``header``), or the header states no name there and the channel is
accepted under the user's statement (``statement``, FC2/FC3)."""
FIX = (
    'Tag the units of these images on their own (channels_from = "unit", confirmed per unit), '
    "then run again. Nothing was reordered, dropped or guessed."
)


@dataclass(frozen=True)
class Contradiction:
    """One contradiction of one image with the experiment list.

    ``c_indices`` are the source channel indices concerned (empty for ``image_type`` and
    ``channel_count``); ``expected`` and ``found`` say what the list states and what the header
    states, in plain words (for several indices, one part per index, in that order, joined by
    ``"; "``).
    """

    kind: str
    c_indices: tuple[int, ...]
    expected: str
    found: str

    def text(self) -> str:
        """Return the contradiction as one line.

        For example ``header_names C0: expected DNA (DAPI, Hoechst 33342), found 'GFP'``.
        """
        where = ", ".join(f"C{i}" for i in self.c_indices)
        prefix = f"{self.kind} {where}: " if where else f"{self.kind}: "
        return f"{prefix}expected {self.expected}, found {self.found}"


@dataclass(frozen=True)
class ChannelEvidence:
    """What supports one channel of an accepted image (the manifest's ``fast_layout_images``).

    ``header_name`` is the name as read (never normalised): ``""`` when the header states an
    empty name, None when the format states none.
    """

    c: int
    channel: str
    header_name: str | None
    evidence: str

    def as_record(self) -> dict[str, object]:
        """Return the manifest item of this channel."""
        return {
            "c": self.c,
            "channel": self.channel,
            "header_name": self.header_name,
            "evidence": self.evidence,
        }


def nfc(text: str) -> str:
    """Return the NFC form of ``text``: a comparison key only, never stored."""
    return unicodedata.normalize("NFC", text)


def is_evidence(name: str, channel: str, description: Description) -> bool:
    """Return whether a header name is a confirmed name of ``channel`` (section 4.3 rule)."""
    known = description.channels[channel].header_names or ()
    return nfc(name) in {nfc(entry) for entry in known}


def _expected(channel: str, description: Description) -> str:
    """Name a channel of the list with its confirmed header names, e.g. ``DNA (DAPI, ...)``."""
    known = description.channels[channel].header_names or ()
    return f"{channel} ({', '.join(known)})" if known else f"{channel} (no confirmed header name)"


def _header_contradiction(
    layout: ChannelLayout, names: Sequence[str | None], description: Description
) -> Contradiction | None:
    """Return the ``header_order`` or ``header_names`` contradiction of one image, or None."""
    if layout.image_type is not None:  # R, G and B take no header names (section 4.4b)
        return None
    failing = [
        (index, name)
        for index, (channel, name) in enumerate(zip(layout.channels, names, strict=False))
        if name and not is_evidence(name, channel, description)
    ]
    if not failing:
        return None
    other = [
        any(is_evidence(name, channel, description) for channel in layout.channels)
        for _, name in failing
    ]
    kind = HEADER_ORDER if all(other) else HEADER_NAMES
    expected = "; ".join(_expected(layout.channels[i], description) for i, _ in failing)
    found = "; ".join(repr(name) for _, name in failing)
    return Contradiction(kind, tuple(i for i, _ in failing), expected, found)


def contradictions(
    layout: ChannelLayout,
    description: Description,
    *,
    count: int | None,
    names: Sequence[str | None],
    type_found: str | None,
) -> tuple[Contradiction, ...]:
    """Return every contradiction of one image with the experiment list, in the kinds' order.

    Parameters
    ----------
    layout
        The fast-mode statement (``channel_layout``).
    description
        The description, whose channels hold the confirmed header names.
    count
        The image's channel count as its header (or the confirmed axis roles) states it; None
        when it is not known, which never passes as a match (the caller lists it unchecked).
    names
        The header channel names as read, by C index (None or ``""`` where there is none).
    type_found
        The image-type contradiction's description (what the header says), or None when the
        image's effective type agrees with the list's or its format states no colour
        interpretation.
    """
    found: list[Contradiction] = []
    if type_found is not None:
        expected = layout.image_type or "not rgb (an ordinary channel image)"
        found.append(Contradiction(IMAGE_TYPE, (), expected, type_found))
    if count is not None and count != len(layout.channels):
        listed = ", ".join(layout.channels)
        expected = f"{len(layout.channels)} channels ({listed})"
        found.append(Contradiction(CHANNEL_COUNT, (), expected, f"{count} channels"))
    header = _header_contradiction(layout, names, description)
    if header is not None:
        found.append(header)
    return tuple(found)


def channel_evidence(
    layout: ChannelLayout, names: Sequence[str | None]
) -> tuple[ChannelEvidence, ...]:
    """Return the evidence of each channel of an image without contradiction, in C order.

    Call it only for an image :func:`contradictions` found nothing in: a name the header
    states (non-empty) is then a confirmed name of that channel, ``header`` evidence. A
    missing or empty name is accepted under the statement. An RGB list takes no header names,
    so its channels are always under the statement.
    """
    out = []
    for index, channel in enumerate(layout.channels):
        name = names[index] if index < len(names) else None
        named = bool(name) and layout.image_type is None
        out.append(ChannelEvidence(index, channel, name, HEADER if named else STATEMENT))
    return tuple(out)


def image_record(path: str, fov_key: str, evidence: Sequence[ChannelEvidence]) -> dict[str, object]:
    """Return one item of a unit's ``fast_layout_images`` (ADR 0006, the verification status).

    ``header_verified`` is true exactly when every channel's evidence is ``header``.
    """
    return {
        "path": path,
        "fov_key": fov_key,
        "header_verified": all(item.evidence == HEADER for item in evidence),
        "channels": [item.as_record() for item in evidence],
    }


def contradiction_message(images: Mapping[tuple[str, str], Sequence[Contradiction]]) -> str:
    """Return the one refusal's message: every image (FOV key, path) and every contradiction.

    ``images`` maps ``(FOV key, path)`` to the image's contradictions, in the kinds' order.
    """
    lines = [
        "Some images contradict the experiment channel list (every imaging unit has the same "
        "channels in the same order). They were found from the image headers only; no pixel "
        f"was read and nothing was processed. {len(images)} contradicting image(s):"
    ]
    for (fov_key, path), found in sorted(images.items(), key=lambda item: item[0][1]):
        lines.append(f"- {fov_key} ({path}): " + "; ".join(item.text() for item in found))
    return "\n".join(lines)


__all__ = [
    "CHANNEL_COUNT",
    "FAST_LAYOUT_CONTRADICTION",
    "FIX",
    "HEADER",
    "HEADER_NAMES",
    "HEADER_ORDER",
    "IMAGE_TYPE",
    "KINDS",
    "STATEMENT",
    "ChannelEvidence",
    "Contradiction",
    "channel_evidence",
    "contradiction_message",
    "contradictions",
    "image_record",
    "is_evidence",
]
