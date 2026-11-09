"""The rules behind the notebook's *Your channels* screen: labels, look-alikes and usage.

A **label** is the ID of a channel and names *what was stained* (``DNA``, ``ACTIN``), never the
dye or the microscope's slot. Nothing here reads a file or a widget; the cards of
:mod:`.notebook_channels_labels` and :mod:`.notebook_channels_grid` only show what these
functions decide.

- :func:`is_generic` and :data:`GENERIC_PATTERNS`: header names that say nothing about the stain
  (``Ch1``, ``C0``, ``Channel 2``, ``AF488``, ``DAPI``, ``GFP``, ``488 nm``). They are never
  proposed as labels; the user may still type one.
- :func:`propose_labels`: the labels a set of raw header names can offer (valid IDs as they are,
  not generic, never repaired), and the **look-alike groups** (same letters ignoring case,
  spaces, ``-``/``_``, or one character different) that the user decides on. Nothing is merged.
- :func:`label_problems`: the refusals of a typed label list, a case collision included
  (``E_ID_CASE_COLLISION``), at once.
- :func:`usage`, :func:`positions`, :func:`aliases`, :func:`remaining`: the counts and the
  header evidence derived from the tags the user gave.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .description_identity import nfc
from .draft_answers import UnitAnswer, UnitKey
from .draft_ids import id_problems

RGB_IDS = ("R", "G", "B")
"""The fixed channel IDs of a colour (RGB) unit (experiment-v1 section 4.4b): never labels."""

GENERIC_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?:ch|chan|channel|c)[\s_\-=:.]*\d+", "a slot name of the microscope"),
    (r"(?:track|detector|laser|line|pmt|ex|em)[\s_\-=:.]*\d+(?:\s*nm)?", "a slot name"),
    (r"\d{3,4}\s*(?:nm)?(?:[\s_\-]*(?:ex|em|laser))?", "a wavelength"),
    (
        r"(?:af|alexa(?:[\s_\-]*fluor)?|cy|atto|dy|dylight|cf|ir[\s_\-]*dye)[\s_\-]*\d{1,3}\w*",
        "a dye",
    ),
    (
        r"dapi|hoechst(?:[\s_\-]*\d+)?|draq\d|fitc|tritc|texas[\s_\-]*red|"
        r"[ey]?gfp|[ecy]fp|rfp|mcherry|mscarlet|tdtomato",
        "a dye or fluorescent protein",
    ),
)
"""Small, documented list of header names that are not labels: slot names and dyes.

Each entry is a full-match, case-insensitive regular expression and the reason said to the user.
The list is deliberately short; a name it misses is only proposed, never adopted, and the user
confirms every label.
"""
_GENERIC = tuple((re.compile(p, re.IGNORECASE), why) for p, why in GENERIC_PATTERNS)
_SEPARATORS = re.compile(r"[\s\-_]+")
GENERIC = "generic"
NOT_VALID = "not_valid"


def generic_reason(name: str) -> str | None:
    """Return why ``name`` is a generic header name (a slot or a dye), or None if it is not."""
    text = nfc(name).strip()
    return next((why for pattern, why in _GENERIC if pattern.fullmatch(text)), None)


def is_generic(name: str) -> bool:
    """Return True for a header name that names a slot or a dye, not what was stained."""
    return generic_reason(name) is not None


def _fold(name: str) -> str:
    return nfc(name).casefold()


def _squashed(name: str) -> str:
    return _SEPARATORS.sub("", _fold(name))


def _one_edit(a: str, b: str) -> bool:
    """Return True when ``a`` and ``b`` differ by exactly one inserted, deleted or changed char."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1 :] == b[i + 1 :]
    return a[i:] == b[i + 1 :]


def look_alike(a: str, b: str) -> bool:
    """Return True when two different spellings could be one label.

    Same letters ignoring case, spaces, ``-`` and ``_``, or one character different (names of
    at least three characters, so ``A`` and ``B`` are not look-alikes). The user decides; the
    answer is never used to merge anything.
    """
    if a == b:
        return False
    if _fold(a) == _fold(b) or _squashed(a) == _squashed(b):
        return True
    return min(len(a.strip()), len(b.strip())) >= 3 and _one_edit(_fold(a), _fold(b))


def case_only(a: str, b: str) -> bool:
    """Return True when two different IDs are the same ignoring capital letters."""
    return a != b and _fold(a) == _fold(b)


@dataclass(frozen=True)
class LabelProposal:
    """What the header names can offer as labels.

    ``single`` are names with no look-alike, valid as an ID as they stand; ``groups`` are sets of
    look-alike spellings (two or more) for the user to decide, each member listed whether it
    could be an ID or not; ``skipped`` are ``(name, reason)`` for names not offered: reason
    :data:`GENERIC` (a slot or a dye) or :data:`NOT_VALID` (not an ID as it stands).
    """

    single: tuple[str, ...] = ()
    groups: tuple[tuple[str, ...], ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()


def _components(names: Sequence[str]) -> list[list[str]]:
    """Return the connected sets of look-alike names (union-find), in the order first seen."""
    parent = {name: name for name in names}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if look_alike(a, b):
                parent[find(a)] = find(b)
    found: dict[str, list[str]] = {}
    for name in names:
        found.setdefault(find(name), []).append(name)
    return list(found.values())


def usable_as_label(name: str, used: Collection[str] = ()) -> bool:
    """Return True when ``name`` is a valid channel ID as it stands, not an RGB token."""
    return not id_problems("channels", name, used) and name.casefold() not in {
        t.casefold() for t in RGB_IDS
    }


def propose_labels(header_names: Iterable[str | None], used: Collection[str] = ()) -> LabelProposal:
    """Return the labels the raw header names can offer, and the look-alikes to decide on.

    A name is offered only if it is a valid channel ID exactly as written and is not generic
    (:func:`is_generic`); nothing is trimmed, re-cased or repaired. Names that look alike
    (:func:`look_alike`) are not offered one by one: they come back as a group, so the user says
    whether they are one label (and which spelling is the ID) or different ones.
    """
    names = list(dict.fromkeys(n for n in header_names if n))
    skipped = [(n, GENERIC) for n in names if is_generic(n)]
    left = [n for n in names if not is_generic(n)]
    single: list[str] = []
    groups: list[tuple[str, ...]] = []
    for members in _components(left):
        if len(members) > 1:
            groups.append(tuple(sorted(members)))
        elif usable_as_label(members[0], used):
            single.append(members[0])
        else:
            skipped.append((members[0], NOT_VALID))
    return LabelProposal(tuple(single), tuple(groups), tuple(skipped))


def label_problems(labels: Sequence[str], used: Collection[str] = ()) -> dict[int, tuple[str, ...]]:
    """Return, by position in ``labels``, the codes that refuse that entry; empty ones omitted.

    The reader's channel-ID rules (:func:`.draft_ids.id_problems`), plus a label given twice
    (``E_DUPLICATE_VALUE``), two labels that differ only by capital letters
    (``E_ID_CASE_COLLISION``, reported on both) and ``R``, ``G``, ``B`` (``E_RGB_TOKEN_MISUSE``:
    the fixed names of colour images, not stains).
    """
    out: dict[int, tuple[str, ...]] = {}
    for index, label in enumerate(labels):
        codes = list(id_problems("channels", label, used))
        others = [o for i, o in enumerate(labels) if i != index]
        if label in others:
            codes.append("E_DUPLICATE_VALUE")
        if any(case_only(label, other) for other in others):
            codes.append("E_ID_CASE_COLLISION")
        if label.casefold() in {t.casefold() for t in RGB_IDS}:
            codes.append("E_RGB_TOKEN_MISUSE")
        if codes:
            out[index] = tuple(dict.fromkeys(codes))
    return out


@dataclass(frozen=True)
class UnitLayout:
    """One imaging unit as the channel screen needs it, read from its representative image.

    ``image`` is the first image of the unit by sorted path, whose header gives ``channels`` (the
    number of C indices, 3 for a colour image) and ``names`` (the raw header names by C index,
    None where it has none). ``problem`` is why the unit cannot be tagged (no readable first
    image, roles that do not fit it); then ``channels`` is 0. ``uniform`` is False when some
    other image of the unit has another header signature (channel count, raw names or colour):
    such a unit is never put in a block with others. ``counts_agree`` is False when some other
    image of the unit has another channel count or none known (unreadable, or the roles do not
    fit it): the unit's channel count is then not known
    (:func:`.channel_units.unit_channel_counts`).
    """

    key: UnitKey
    image: str | None
    channels: int = 0
    names: tuple[str | None, ...] = ()
    rgb: bool = False
    problem: str = ""
    images: int = 0
    uniform: bool = True
    counts_agree: bool = True

    @property
    def usable(self) -> bool:
        """True when the unit's channels are known and can be tagged."""
        return not self.problem and self.channels > 0

    @property
    def signature(self) -> tuple[int, tuple[str | None, ...], bool]:
        """What units must share to be one layout: count, raw names and colour."""
        return (self.channels, self.names, self.rgb)

    def name(self, index: int) -> str | None:
        """Return the raw header name at C index ``index``, None when the header has none."""
        return self.names[index] if index < len(self.names) and self.names[index] else None


def tags_of(layout: UnitLayout, answer: UnitAnswer | None) -> tuple[str | None, ...]:
    """Return the saved tags of a unit as one entry per C index; None where not tagged.

    A saved list whose length is not the image's channel count no longer fits it and is not
    used: nothing is stretched, cut or moved to another position.
    """
    saved = () if answer is None else tuple(answer.channels)
    return saved if len(saved) == layout.channels else (None,) * layout.channels


def fits(layout: UnitLayout, answer: UnitAnswer | None) -> bool:
    """Return True when there is no saved tag list, or it has this image's channel count."""
    return answer is None or not answer.channels or len(answer.channels) == layout.channels


def proposal_for(layout: UnitLayout, labels: Collection[str]) -> tuple[str, ...] | None:
    """Return the tags a unit's header names propose, or None when they do not all match.

    Only when **every** channel has a header name equal, exactly (NFC, no trimming, no case
    folding), to a different label of ``labels``. A colour image proposes nothing.
    """
    if not layout.usable or layout.rgb or len(layout.names) != layout.channels:
        return None
    names = [n for n in layout.names if n]
    if len(names) != layout.channels or len(set(names)) != len(names):
        return None
    known = {nfc(label): label for label in labels}
    found = [known.get(nfc(name)) for name in names]
    return None if None in found else tuple(f for f in found if f)


def usage(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer], labels: Iterable[str]
) -> dict[str, tuple[UnitKey, ...]]:
    """Return, for each label, the units that tag at least one channel with it (saved tags)."""
    found: dict[str, list[UnitKey]] = {label: [] for label in labels}
    for layout in layouts:
        tags = set(tags_of(layout, units.get(layout.key)))
        for label in found:
            if label in tags and units[layout.key].image_type != "rgb":
                found[label].append(layout.key)
    return {label: tuple(keys) for label, keys in found.items()}


def positions(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer], labels: Iterable[str]
) -> dict[str, Counter]:
    """Return, for each label, how many units tag it at each C index (saved tags)."""
    out: dict[str, Counter] = {label: Counter() for label in labels}
    for layout in layouts:
        for index, tag in enumerate(tags_of(layout, units.get(layout.key))):
            if tag in out:
                out[tag][index] += 1
    return out


def aliases(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer]
) -> dict[str, tuple[str, ...]]:
    """Return the raw header names of every cell tagged with a label, by label, sorted.

    The tag is the user's confirmation that the header name is evidence for the label
    (experiment-v1 section 4.3); names are kept exactly as read. A colour unit has none (``R``,
    ``G`` and ``B`` take no aliases), and a label whose cells have no header name is omitted.
    """
    found: dict[str, set[str]] = {}
    for layout in layouts:
        answer = units.get(layout.key)
        if answer is None or answer.image_type == "rgb":
            continue
        for index, tag in enumerate(tags_of(layout, answer)):
            name = layout.name(index)
            if tag and name:
                found.setdefault(tag, set()).add(name)
    return {label: tuple(sorted(names)) for label, names in found.items()}


@dataclass(frozen=True)
class Remaining:
    """What is still open on the tag grid: cells, image types and units that cannot be tagged."""

    cells: int = 0
    types: int = 0
    blocked: int = 0
    units: tuple[UnitKey, ...] = field(default=())

    @property
    def done(self) -> bool:
        """True when every position of every unit is tagged and every type answered."""
        return not (self.cells or self.types or self.blocked)


def remaining(
    layouts: Sequence[UnitLayout], units: Mapping[UnitKey, UnitAnswer], labels: Collection[str]
) -> Remaining:
    """Return what the user still has to answer: every position of every unit, every type."""
    cells = types = blocked = 0
    open_units: list[UnitKey] = []
    for layout in layouts:
        answer = units.get(layout.key)
        if not layout.usable:
            blocked += 1
            open_units.append(layout.key)
            continue
        left = 0
        if answer is None or answer.image_type is None:
            left += 1
            types += 1
        if answer is None or answer.image_type != "rgb":
            tags = tags_of(layout, answer)
            missing = sum(1 for t in tags if t is None or t not in labels)
            cells += missing
            left += missing
        if left:
            open_units.append(layout.key)
    return Remaining(cells, types, blocked, tuple(open_units))


__all__ = [
    "GENERIC",
    "GENERIC_PATTERNS",
    "NOT_VALID",
    "RGB_IDS",
    "LabelProposal",
    "Remaining",
    "UnitLayout",
    "aliases",
    "case_only",
    "fits",
    "generic_reason",
    "is_generic",
    "label_problems",
    "look_alike",
    "positions",
    "propose_labels",
    "proposal_for",
    "remaining",
    "tags_of",
    "usable_as_label",
    "usage",
]
