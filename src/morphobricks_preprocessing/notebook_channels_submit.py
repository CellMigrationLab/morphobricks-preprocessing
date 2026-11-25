"""*Tag channels*, guided step: **Submit** checks the rows against the headers and saves them.

Owner design of 25 November 2026 (:mod:`.channel_scope`). The dropdowns of the guided step only
change its form; Submit is the one action that saves, and it saves everything in ONE draft
write: the units' tags and their aliases, the tag review (the units it refused, the refusals it
released) and the statement of the answer (``channel_layout``, or a strict file):

- every row is checked as a click on *Tag this repeat folder* is
  (:func:`.repeat_tags.repeat_fill`, the same rule and the same aliases snapshot of a unit
  already refused): a unit whose header names contradict the row's tags is listed with the
  contradiction and not filled, never reordered or guessed; a unit of one row is checked
  against its own header names only;
- a unit whose own images disagree is in no row and is listed (:func:`.repeat_tags.
  repeat_apart`); a unit a row fills leaves the list (the owner kept that a row whose tags fit
  the unit is an action on it); every other refusal and confirmation stays as it is;
- *the same order for the whole dataset* with one channel count is fast mode (Spec ADR 0006):
  ``channel_layout`` is the row's list and every unit it fills follows it
  (``channels_from = "experiment"``). Every other answer is a strict per-unit file: each unit
  holds its own list, its row's (several channel counts, a row per repeat or per unit: no
  single ``channel_layout`` can state them, and none is written).

The write goes through :meth:`.TagGrid.commit` with ``then`` (:func:`.channel_answers.tagged`):
the draft generation check (``R_DRAFT_CHANGED``) and every other refusal of a save apply, and a
refused write leaves the draft, the review in memory and the tick as they were.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from . import notebook_style as ui
from .channel_matrix import difference_text
from .notebook_channels_memory import Refusal
from .notebook_channels_override import OTHER_DRAFT
from .repeat_tags import pairing_collision, repeat_apart, repeat_fill, repeat_lead, repeat_members

if TYPE_CHECKING:
    from .channel_labels import UnitLayout
    from .channel_scope import FormRow
    from .draft_answers import DraftAnswers
    from .notebook_channels_grid import TagGrid


def collisions(grid: TagGrid, rows: Sequence[FormRow]) -> list[str]:
    """Return a line per repeat row whose folder name the pairing puts into several repeats."""
    answers, out = grid.store.answers(), []
    for row in rows:
        if row.scope != "repeat":
            continue
        found = pairing_collision(answers, repeat_members(grid.layouts, row.key[1]))
        if found:
            pairs = ", ".join(f"{c} → {r}" for c, r in found.items())
            out.append(
                f"{html.escape(row.key[1])}: paired with different biological repeats "
                f"({html.escape(pairs)}); tag every imaging unit instead"
            )
    return list(dict.fromkeys(out))


def submit(
    grid: TagGrid,
    rows: Sequence[tuple[FormRow, Sequence[str]]],
    apart: Sequence[UnitLayout],
    *,
    fast: bool,
) -> bool:
    """Check every row against the headers and save tags, review and statement in one write.

    ``rows`` are the rows with their complete tags (checked by the caller), ``apart`` the units
    whose images disagree, ``fast`` whether the answer is fast mode. Returns True when the
    write succeeded (refused units may then be listed); False when nothing was written.
    """
    memory = grid.by_repeat
    if memory.other_draft():  # the review in memory is another draft's: never written here
        memory.notice = OTHER_DRAFT
        grid.refresh()
        return False
    memory.sync()  # the review is built from the draft's, never from memory behind it
    answers, labels = grid.store.answers(), grid.labels
    frozen = {key: old.channels for key, old in memory.refused.items()}
    save: dict = {}
    cells: list = []
    staged = dict(memory.refused)
    by_count: dict[int, tuple[str, ...]] = {}
    for row, tags in rows:
        tags = tuple(tags)
        by_count.setdefault(row.count, tags)
        filled, clicked, refused = repeat_fill(answers, row.units, tags, labels, frozen)
        lead = repeat_lead(row.units)
        for key, found in refused.items():
            layout = next(u for u in row.units if u.key == key)
            text = "; ".join(html.escape(difference_text(d)) for d in found)
            staged[key] = Refusal(text, layout, lead, tags, frozen.get(key, answers.channels))
        save.update(filled)
        cells += clicked
    for layout in apart:  # its images disagree: listed, never filled
        why = repeat_apart((layout,)).get(layout.key, "its images disagree")
        tags = by_count.get(layout.channels, ())
        snapshot = frozen.get(layout.key, answers.channels)
        staged[layout.key] = Refusal(html.escape(why), layout, None, tags, snapshot)
    for key in save:  # filled by this Submit: its refusal goes with the same write
        staged.pop(key, None)
    kept = {k: v for k, v in memory.override.confirmed.items() if k in staged}
    review = memory._review(kept, staged)
    matrix = grid.matrix
    before = (bool(grid.same_channels.value), set(matrix.own), dict(matrix.templates))
    matrix.set_tick(fast)
    if fast and rows:
        matrix.own -= set(save)  # they follow the list again
        groups, _ = matrix.groups()
        if groups:
            matrix.templates[groups[0][0].key] = tuple(rows[0][1])

    def then(new: DraftAnswers) -> DraftAnswers:
        return matrix.stated(replace(new, tag_review=review), cells)

    memory.staged = staged  # memory takes it in ``adopt``, once the write succeeded
    try:
        ok = grid.commit(save, cells, then)
    finally:
        memory.staged = None
    if not ok:  # nothing was written: the tick and the rows of the matrix are as before
        matrix.set_tick(before[0])
        matrix.own, matrix.templates = before[1], before[2]
        grid.refresh()
    return ok


def refused_text(grid: TagGrid) -> str:
    """Return what Submit says about the units it listed (empty when none is listed)."""
    listed = grid.by_repeat.listed()
    if not listed:
        return ""
    return ui.status(
        "warn",
        f"Saved; {len(listed)} imaging unit(s) not tagged: their image headers contradict "
        "the tags you submitted, or their images disagree.",
        [
            "Nothing is reordered or guessed. Each is listed below: confirm its tags (Override "
            "this unit) or correct its images. The description is not saved while any is listed."
        ],
    )


__all__ = ["collisions", "refused_text", "submit"]
