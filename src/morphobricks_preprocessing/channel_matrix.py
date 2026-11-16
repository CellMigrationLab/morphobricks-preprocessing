"""Fast mode of *Tag channels*: one answer for every imaging unit, headers trusted (pure).

Owner decisions of 16 November 2026. **Fast mode is the default**: the user's statement
*Every imaging unit has the same channels in the same order* (a checkbox, ticked by default,
that they can untick for the strict per-unit page) is a declared assumption, never a guess:

- **Headers are trusted.** An untagged unit whose header names every channel with a confirmed
  label, exactly (NFC, no trimming, the :func:`.channel_labels.proposal_for` rule) or as a
  header name already saved for that label (an alias), takes those labels with no confirmation
  click when the page opens (:func:`header_tags`, :func:`trusted`). Aliases saved by the row's
  own filling in this session are not trusted on the spot: a unit the row refused stays refused.
- **Units without header names take the same order as the one tagged unit**: the layout's row
  of tags (read from the first unit in folder order, the *first tagged unit*) fills them
  (:func:`set_template`); the user may then tune any cell of any unit (that unit is *edited*).
- **A contradiction is refused, never prefilled.** A unit with another channel count, or whose
  header names are in another order or are other names (a name saying another label than the
  row's, or a name that is not the first unit's at that C index), is not filled and stops the
  step: it is listed with the fix, tag it on its own (:func:`contradictions`). Nothing is
  reordered or guessed.

Every unit's tags are saved in the draft exactly as tagging that unit by hand writes them
(:func:`.channel_answers.with_tags`); nothing here reads a file or a widget.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from .channel_answers import with_tags
from .channel_labels import RGB_IDS, UnitLayout, aliases, header_label, tags_of
from .draft_answers import ChannelAnswer, DraftAnswers, UnitKey

SAME = "same"
"""A unit whose saved tags are the layout's row ("same as the first tagged unit")."""
HEADER = "header"
"""A unit whose saved tags are what its header names say (trusted), not the layout's row."""
EDITED = "edited"
"""A unit whose saved tags differ from the layout's row and from its header (tuned by hand)."""
NEW = "new"
"""A unit with no tag while the layout has some (a reopen): shown filled, not saved."""
REFUSED = "refused"
"""A unit with no tag whose header contradicts the layout's row: not filled, tag it on its own."""

Cells = list[tuple[UnitKey, int]]


@dataclass(frozen=True)
class Difference:
    """One C index where a unit's header name contradicts the layout's row."""

    index: int
    name: str
    said: str
    """What contradicts: the label the name says, or the first unit's name at that index."""
    tag: str | None


@dataclass(frozen=True)
class MatrixRow:
    """One unit of a layout: its state, the tags shown, and its header differences."""

    layout: UnitLayout
    state: str
    shown: tuple[str | None, ...]
    differences: tuple[Difference, ...] = ()


def apart_reason(layout: UnitLayout) -> str:
    """Return why a tag-able, non-colour unit cannot be in a layout at all ("" when it can)."""
    if not layout.uniform:
        return "its images do not all have one header"
    return ""


def eligible(layout: UnitLayout, colour: Collection[UnitKey]) -> bool:
    """Return True when a unit can be filled from another: tag-able, not colour, one header."""
    return (
        layout.usable and not layout.rgb and layout.key not in colour and not apart_reason(layout)
    )


def layout_groups(
    ordered: Sequence[UnitLayout],
    colour: Collection[UnitKey],
    extra: Sequence[int] = (),
    own: Collection[UnitKey] = (),
) -> tuple[list[tuple[UnitLayout, ...]], list[UnitLayout]]:
    """Return the layouts (each led by its first unit) and the non-colour units tagged on their own.

    ``ordered`` are the units in folder order. The first layout has the channel count of the
    first eligible unit; ``extra`` are the counts the user added a layout for (**Add another
    layout**), in the order added. A unit of no layout's count, one that is not eligible, and
    one the user switched to tagging on its own (``own``) are apart. Colour and untaggable units
    are in neither.
    """
    units = [u for u in ordered if eligible(u, colour)]
    counts = list(dict.fromkeys([units[0].channels, *extra])) if units else []
    groups = [
        tuple(u for u in units if u.channels == count and u.key not in own) for count in counts
    ]
    apart = [
        u
        for u in ordered
        if u.usable
        and not u.rgb
        and u.key not in colour
        and (bool(apart_reason(u)) or u.channels not in counts or u.key in own)
    ]
    return [g for g in groups if g], apart


def apart_text(layout: UnitLayout, counts: Collection[int], own: Collection[UnitKey]) -> str:
    """Return why a unit is tagged on its own."""
    reason = apart_reason(layout)
    if reason:
        return f"{reason}: tag on its own"
    if layout.key in own:
        return "switched to tagging on its own"
    shown = ", ".join(str(c) for c in sorted(counts))
    return f"different channel count ({layout.channels}, not {shown}): tag on its own"


def name_label(
    name: str | None, labels: Collection[str], channels: Mapping[str, ChannelAnswer]
) -> str | None:
    """Return the label a raw header name says: equal to it exactly, or a saved alias of it.

    The exact rule is :func:`.channel_labels.header_label`; an alias is a header name saved for
    a label (its ``header_names``). None when the name says no label, or says two.
    """
    if not name:
        return None
    exact = header_label(name, labels)
    if exact is not None:
        return exact
    found = [k for k in labels if name in (channels.get(k, ChannelAnswer()).header_names or ())]
    return found[0] if len(found) == 1 else None


def header_tags(
    layout: UnitLayout, labels: Collection[str], channels: Mapping[str, ChannelAnswer]
) -> tuple[str, ...] | None:
    """Return the labels a unit's header names say, when they name every channel; else None.

    Every C index must have a header name saying a confirmed label (:func:`name_label`), each a
    different one; a colour unit, or one whose images do not all have one header, says nothing.
    """
    if not layout.usable or layout.rgb or not layout.uniform:
        return None
    found = [name_label(layout.name(i), labels, channels) for i in range(layout.channels)]
    if None in found or len(set(found)) != len(found):
        return None
    return tuple(f for f in found if f)


def trusted(
    answers: DraftAnswers,
    layouts: Sequence[UnitLayout],
    labels: Collection[str],
    saved: Mapping[str, ChannelAnswer] | None = None,
) -> tuple[DraftAnswers, Cells]:
    """Return the answers with every untagged unit whose header names its channels tagged so.

    Fast mode's trust in the headers: no confirmation click (owner, 16 November 2026). A unit
    with any saved tag keeps it, even one saved for another channel count: the user tags that
    unit again (nothing saved is replaced without a click). ``saved`` are the labels whose
    header names (aliases) are trusted: those saved when the page opened, so that an alias the
    row's own filling saved in this session trusts nothing on the spot (default: the answers').
    Of those, only the ones the answers still hold are trusted: an alias removed since (its
    label removed from the list) is no longer saved.
    """
    known = answers.channels if saved is None else _still_saved(saved, answers.channels)
    cells: Cells = []
    for layout in layouts:
        tags = header_tags(layout, labels, known)
        saved = answers.units.get(layout.key)
        if tags is None or (saved is not None and any(saved.channels)):
            continue
        answers = with_tags(answers, layout, tags)
        cells += [(layout.key, i) for i in range(layout.channels)]
    return answers, cells


def _still_saved(
    saved: Mapping[str, ChannelAnswer], now: Mapping[str, ChannelAnswer]
) -> dict[str, ChannelAnswer]:
    """Return the labels of ``saved`` with only the aliases ``now`` still holds."""
    out = {}
    for label, channel in saved.items():
        kept = set((now.get(label) or ChannelAnswer()).header_names or ())
        names = tuple(n for n in channel.header_names or () if n in kept)
        out[label] = ChannelAnswer(header_names=names or None)
    return out


def untrusted(
    answers: DraftAnswers,
    layouts: Sequence[UnitLayout],
    given: Mapping[UnitKey, tuple[str | None, ...]],
) -> DraftAnswers:
    """Return the answers with the units :func:`trusted` tagged (``given``) untagged again.

    The statement unticked: those tags rested on it alone, with no click, so the strict page
    shows the header as a proposal to confirm, as before. ``given`` holds only units the user
    has not clicked on since (the caller forgets a unit at its first click). A unit whose saved
    tags are some of the given ones, the others untagged since without a click on it (their
    label removed from the list), is untagged too; one with any other tag keeps it.
    """
    for layout in layouts:
        tags = given.get(layout.key)
        saved = tags_of(layout, answers.units.get(layout.key))
        if tags is None or len(tags) != len(saved):
            continue
        if any(saved) and all(s is None or s == t for s, t in zip(saved, tags, strict=True)):
            answers = with_tags(answers, layout, (None,) * layout.channels)
    return answers


def with_aliases(
    answers: DraftAnswers, names: Mapping[str, tuple[str, ...] | None]
) -> DraftAnswers:
    """Return the answers with ``names`` added back to the aliases of the labels still listed.

    Unticking restores the aliases the draft held before the no-click tags (the ones only those
    units held, which :func:`.channel_answers.synced` drops with them): opening the page
    unticked keeps them until the user's first click. A label no longer listed gets none.
    """
    channels = dict(answers.channels)
    for label, channel in answers.channels.items():
        if label in RGB_IDS or not names.get(label):
            continue
        merged = set(channel.header_names or ()) | set(names[label] or ())
        channels[label] = replace(channel, header_names=tuple(sorted(merged)))
    return replace(answers, channels=channels)


def differences(
    layout: UnitLayout,
    tags: Sequence[str | None],
    lead: UnitLayout,
    labels: Collection[str],
    channels: Mapping[str, ChannelAnswer],
    only: Collection[int] | None = None,
) -> tuple[Difference, ...]:
    """Return the C indices where a unit's header contradicts the layout's row ``tags``.

    A header name contradicts when it says another label than the row's (:func:`name_label`),
    or says none and is not the first unit's (``lead``) header name at that C index: other
    names, or the same names in another order. A C index without a name, or not tagged in the
    row, contradicts nothing; with ``only``, just those C indices are checked.
    """
    out = []
    for index, tag in enumerate(tags):
        name = layout.name(index)
        if not name or tag is None or (only is not None and index not in only):
            continue
        said = name_label(name, labels, channels)
        if said is not None:
            if said != tag:
                out.append(Difference(index, name, f"is {said}", tag))
        elif name != lead.name(index):
            first = lead.name(index)
            what = f"the first unit's is ‘{first}’" if first else "the first unit's has no name"
            out.append(Difference(index, name, what, tag))
    return tuple(out)


def matrix_rows(
    group: Sequence[UnitLayout],
    answers: DraftAnswers,
    labels: Collection[str],
    template: Sequence[str | None],
) -> list[MatrixRow]:
    """Return the rows of one layout: each unit's state against the layout's row of tags.

    A unit whose saved tags are the row is *same*; those its header says, *header*; other saved
    tags, *edited*. A unit with none, while the row has some, is shown filled (*new*), or is
    *refused* when its header contradicts the row.
    """
    template = tuple(template)
    rows = []
    for layout in group:
        saved = tags_of(layout, answers.units.get(layout.key))
        if saved == template:
            rows.append(MatrixRow(layout, SAME, saved))
        elif any(saved) and saved == header_tags(layout, labels, answers.channels):
            rows.append(MatrixRow(layout, HEADER, saved))
        elif any(saved):
            rows.append(MatrixRow(layout, EDITED, saved))
        else:
            found = differences(layout, template, group[0], labels, answers.channels)
            rows.append(MatrixRow(layout, REFUSED if found else NEW, template, found))
    return rows


def set_template(
    answers: DraftAnswers,
    layouts: Sequence[UnitLayout],
    group: Sequence[UnitLayout],
    before: Sequence[str | None],
    tags: Sequence[str | None],
    labels: Collection[str],
) -> tuple[DraftAnswers, Cells]:
    """Return the answers with the row's new tags saved for the units that follow it, and cells.

    A follower is a unit whose saved tags were the row's (``before``; untagged units too while
    the row was untagged), unless its header names its channels with labels exactly (it keeps
    what its header says). The C indices the row changed are checked against each follower's
    header (:func:`differences`): on a contradiction its tags are taken back (it was only
    following) and it is refused, to be tagged on its own. The aliases checked are those of the
    other units of ``layouts`` (every unit): a follower's own header names were saved only
    because it followed, and say nothing about the cells decided again.
    """
    before, tags = tuple(before), tuple(tags)
    changed = [i for i, (a, b) in enumerate(zip(before, tags, strict=True)) if a != b]
    follows = [
        u
        for u in group
        if tags_of(u, answers.units.get(u.key)) == before and header_tags(u, labels, {}) is None
    ]
    keys = {u.key for u in follows}
    others = aliases([u for u in layouts if u.key not in keys], answers.units)
    known = {label: ChannelAnswer(header_names=others.get(label)) for label in labels}
    out, cells = answers, []
    for layout in follows:
        if differences(layout, tags, group[0], labels, known, changed):
            out = with_tags(out, layout, (None,) * layout.channels)
            continue
        out = with_tags(out, layout, tags)
        cells += [(layout.key, i) for i in changed]
    return out, cells


def fill(answers: DraftAnswers, rows: Sequence[MatrixRow]) -> tuple[DraftAnswers, Cells]:
    """Return the answers with these rows' shown tags saved (the user's click), and the cells."""
    cells: Cells = []
    for row in rows:
        answers = with_tags(answers, row.layout, row.shown)
        cells += [(row.layout.key, i) for i, t in enumerate(row.shown) if t]
    return answers, cells


def difference_text(found: Difference) -> str:
    """Return one contradiction in words (plain text)."""
    row = found.tag or "untagged"
    return f"C{found.index}: header ‘{found.name}’ {found.said}, the row says {row}"


__all__ = [
    "EDITED",
    "HEADER",
    "NEW",
    "REFUSED",
    "SAME",
    "Difference",
    "MatrixRow",
    "apart_reason",
    "apart_text",
    "difference_text",
    "differences",
    "eligible",
    "fill",
    "header_tags",
    "layout_groups",
    "matrix_rows",
    "name_label",
    "set_template",
    "trusted",
    "untrusted",
    "with_aliases",
]
