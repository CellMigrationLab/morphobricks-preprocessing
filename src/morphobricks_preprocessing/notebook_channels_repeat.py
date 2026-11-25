"""*Tag channels*: **Tag this repeat folder (all conditions)**, one row per repeat folder.

Owner request of 20 November 2026: pick one biological-repeat folder (for example ``rep2``)
and set its channel list once; it is saved, as the user's tags, for every condition's imaging
units in that folder, technical repeats included (:mod:`.repeat_tags`). One row per repeat
folder name and channel count; a unit with another channel count is in its own row. The rows
are keyed by the **folder name** (*Tag channels* comes before the pairing on *Design*): when a
saved pairing puts that folder name of different conditions into different biological repeats,
the row is flagged with each condition's repeat and fills nothing.

Each unit's header is still checked against the row: a unit whose header contradicts it is
listed with the contradiction and not filled, never guessed. A listed unit leaves the list only
by an explicit action on that unit: its editor's Confirm, a one-click confirmation, a hand-tag
of it (each recorded as its confirmation) or a row click that fills it. A header or aliases that
fit later never release it, and only a unit removed from the folder drops its record. While any
unit is listed the description is not published (:meth:`RepeatRows.listed`).

- **Fast mode** (Spec ADR 0006): a row offers only the units outside the layout (another
  channel count, a refused header, or switched to their own); units that fit the layout are
  never touched by it. Setting the row is the user's action for each unit it fills, which is
  then tagged on its own (``channels_from = "unit"``), once the save succeeds.
- **Strict mode** (the statement unticked): a row for every repeat folder, a shortcut that
  fills every unit of it across conditions, each unit saved with its own tags.

On reopening, a row shows the tags every unit of its repeat folder already holds, as a
convenience only: the saved data are per unit (the description format does not change).
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_labels import tags_of
from .channel_matrix import REFUSED, difference_text, differences
from .draft_answers import DraftAnswers
from .notebook_channels_memory import Refusal, ReviewMemory
from .notebook_channels_override import OTHER_DRAFT, OverrideEditor
from .notebook_channels_view import CHOOSE, columns, unit_name
from .repeat_review import Override, TagReview
from .repeat_tags import (
    GroupKey,
    header_names,
    override_holds,
    pairing_collision,
    recount,
    relapsed,
    repeat_apart,
    repeat_fill,
    repeat_groups,
    repeat_lead,
    repeat_members,
    repeat_shown,
)

if TYPE_CHECKING:
    from .channel_labels import UnitLayout
    from .draft_answers import UnitKey
    from .notebook_channels_grid import TagGrid

ACTION = "Tag this repeat folder (all conditions)"


class RepeatRows(ReviewMemory):
    """The rows of tags per repeat folder, drawn under the tag grid."""

    def __init__(self, grid: TagGrid) -> None:
        self.grid = grid
        self.box = widgets.VBox()
        self.pending: dict[GroupKey, list[str | None]] = {}
        self.drops: dict[GroupKey, list[widgets.Dropdown]] = {}
        self.buttons: dict[GroupKey, widgets.Button] = {}
        self.refused: dict[UnitKey, Refusal] = {}
        """Units a row did not fill: the contradiction, the unit, its row's first unit and tags.

        The first unit is None for a unit whose images disagree (:func:`.repeat_tags.repeat_apart`).
        Whether one is still listed is read from its persisted confirmation only
        (:meth:`_released`), never from aliases or headers that fit later. A unit leaves this
        record after a row's save that fitted it succeeded (:meth:`apply`), or when it is removed
        from the folder (:meth:`reconcile`).
        """
        self.override = OverrideEditor(self)
        """The per-unit override of a listed unit (:mod:`.notebook_channels_override`)."""
        self.draft: object = None
        """Which draft the record above belongs to (:meth:`other_draft`); None before the first."""
        self.unsaved = False
        """True while the last save of the review was refused (retried at the next change)."""
        self.staged: dict[UnitKey, Refusal] | None = None
        """A row click's refusals while its one write is made; memory only once it succeeded."""
        self.seen: TagReview | None = None
        """The draft's review as memory last took or wrote it (:meth:`sync`, review as)."""
        self.notice = ""
        """A one-shot message of the last click (shown once, then cleared)."""
        self.message = ""
        """What the rows say now: the last notice and every unit still listed."""

    def listed(self) -> dict[UnitKey, str]:
        """Return the units still listed: refused by a row, and not released by an action on them.

        A refused unit is listed until the user acts on THAT unit: its editor's Confirm,
        **Confirm the same tags** or **Confirm all N like it**, or a deliberate hand-tag of it
        (or a strict-mode block click on it), each recorded as its confirmation in the draft
        (:meth:`_released`, the same rule as :meth:`standing` and the save). Tags that fit its
        header only because another unit saved matching aliases, or because its header changed,
        never release it; a confirmation lapses when its tags or its own header names change. A
        unit in no row (colour, with channels R, G and B, or unusable) is not listed while it is
        so: there are no channel tags to confirm, and its refusal is kept (:meth:`reconcile`) to
        list it again once it is back in a row. A unit whose images disagree stays listed while
        they do: no tags make it one header. Nothing is listed from the memory of another draft
        (the notebook was pointed elsewhere and the grid is not open on it yet). Read from the
        draft's review (:meth:`sync`), never from memory behind it.
        """
        if self.other_draft():
            return {}
        self.sync()
        answers = self.grid.store.answers()
        out = {}
        current = {u.key: u for g in self._rows().values() for u in g}
        for key, refusal in self.refused.items():
            layout = current.get(key)  # the unit as read now, never as at the click
            if layout is None:
                continue  # gone (dropped by :meth:`reconcile`), or colour or unusable now
            if not self._released(key, layout, answers):
                lapsed = relapsed(self.override.confirmed.get(key), layout)
                out[key] = refusal.text or (RELAPSED if lapsed else UNCONFIRMED)
        return out

    def _released(self, key: UnitKey, layout: UnitLayout, answers: DraftAnswers) -> bool:
        """Return True when the user's explicit action on this unit releases its refusal.

        The one authority of :meth:`listed`, :meth:`standing` and so the save: the unit's
        persisted confirmation (``tag_review.confirmed``) still holds for its tags and its own
        header names (:func:`.repeat_tags.override_holds`). A confirmation is written only by an
        action on that unit, in the same draft write as its tags: its editor's Confirm, a
        one-click confirmation (:mod:`.notebook_channels_override`) or a hand-tag of it
        (:meth:`hand_tagged`). A unit whose images disagree has no header to confirm.
        """
        if self.refused[key].lead is None or repeat_apart((layout,)):
            return False
        tags = tags_of(layout, answers.units.get(key))
        return override_holds(self.override.confirmed.get(key), layout, tags)

    def hand_tagged(self, units: Sequence[UnitLayout]) -> Callable | None:
        """Return what a hand-tag of these units saves with their tags (one write), else None.

        A deliberate hand-tag of one unit, or a click on a block of the strict page (the block
        shows its units), is an action on each refused unit it tags. When that unit's tags are
        then complete and fit its own header (read with the aliases this very click saves, as
        :func:`.channel_matrix.differences` reads any tag), the click is recorded as its
        confirmation, in the same draft write as the tags (``TagGrid.commit(..., then)``).
        A fast-mode block of several units confirms nothing. None when no refused unit is
        tagged (the click is then a plain tag change).
        """
        self.sync()
        keys = {u.key for u in units} & set(self.refused)
        if not keys or self.other_draft() or (len(units) > 1 and self.grid.same_channels.value):
            return None
        by_key, labels = {u.key: u for u in units}, self.grid.labels

        def then(answers: DraftAnswers) -> DraftAnswers:
            records = {}
            for key in keys:
                layout, lead = by_key[key], self.refused[key].lead
                tags = tags_of(layout, answers.units.get(key))
                if lead is None or repeat_apart((layout,)) or None in tags:
                    continue
                if not differences(layout, tags, lead, labels, answers.channels):
                    records[key] = Override(tuple(tags), header_names(layout))
            review = self._review({**self.override.confirmed, **records})
            return replace(answers, tag_review=review)

        return then

    def reconcile(self) -> None:
        """Bring the refusals up to date with the grid's current layouts (on every opening).

        Only a unit removed from the folder is dropped: every other refusal is kept, whatever
        the layouts say now, and only an action on the unit releases it (:meth:`_released`).
        What is brought up to date is the reference and the reason shown: a unit in no row now
        (colour, unusable, or no labels yet) keeps its refusal as it is, to be read again once
        it is back in a row; a unit whose channel count changed has every channel chosen again
        (:func:`.repeat_tags.recount`); a unit whose images disagree now is refused for that
        (:func:`.repeat_tags.repeat_apart`), and one whose images agree again takes its row's
        reference and asks for its tags to be confirmed (never read from its new header); any
        other takes its row's *current* reference (:func:`.repeat_tags.repeat_lead`) and says
        where its header contradicts the row's tags at the click or its own tags
        (:func:`.channel_matrix.differences`), or that its tags are incomplete or unconfirmed.
        The reference is taken from the whole row, not only from the units a mode offers.
        Memory behind the draft takes the draft's review first (:meth:`sync`, review as).
        """
        self.sync()
        grid, kept = self.grid, {}
        rows = self._rows()  # the whole row: its reference never depends on the mode
        answers = grid.store.answers()
        present = {u.key: u for u in grid.layouts}
        for key, old in self.refused.items():
            group, layout = self._placed(rows, key)
            if layout is None or not grid.labels:
                if key in present:  # colour, unusable or no labels now: never a release
                    kept[key] = old._replace(layout=present[key])
                continue  # gone from the folder: the only way a refusal is dropped here
            why = repeat_apart(group).get(key)
            if why is not None:
                kept[key] = Refusal(html.escape(why), layout, None, old.tags, old.channels)
                continue
            lead = repeat_lead(group) or layout  # the unit itself is not apart
            if old.lead is None:  # its images agree again: no confirmation came with that
                kept[key] = Refusal(AGREED, layout, lead, old.tags, old.channels)
                continue  # before the count: it was refused with no row's tags to recount
            if (moved := recount(old.tags, layout)) is not None:
                kept[key] = Refusal(moved, layout, lead, old.tags, old.channels)
                continue  # never compared, cut or padded: every channel is chosen again
            own = tags_of(layout, answers.units.get(key))
            confirmed = self.override.confirmed.get(key)
            if relapsed(confirmed, layout):  # checked before anything may drop the refusal
                kept[key] = Refusal(RELAPSED, layout, lead, old.tags, old.channels)
                continue
            found = self.contradictions(layout, old, lead)
            found = found or differences(layout, own, lead, grid.labels, answers.channels)
            why = UNTRUSTED if grid.same_channels.value else "its own tags are incomplete"
            why = _found_text(found) if found else (why if None in own else UNCONFIRMED)
            # a confirmed one is kept too: the source of the one-click offer
            kept[key] = Refusal(why, layout, lead, old.tags, old.channels)
        self.refused = kept
        self.override.confirmed = {k: v for k, v in self.override.confirmed.items() if k in kept}
        self.persist()

    @staticmethod
    def _placed(rows: Mapping, key: UnitKey) -> tuple[tuple, UnitLayout | None]:
        """Return the row of units a unit is in, and the unit as read now (None when gone)."""
        group = next((g for g in rows.values() if any(u.key == key for u in g)), ())
        return group, next((u for u in group if u.key == key), None)

    def contradictions(self, layout: UnitLayout, refusal: Refusal, lead: UnitLayout) -> tuple:
        """Return where the unit's header contradicts the row's tags at the click.

        Read against the aliases of the refusal (:attr:`Refusal.channels`), never the ones
        saved since: the one rule of :func:`.channel_matrix.differences`, on that snapshot.
        Nothing when the unit's channel count changed since (:func:`.repeat_tags.recount`).
        """
        if recount(refusal.tags, layout) is not None:
            return ()
        return differences(layout, refusal.tags, lead, self.grid.labels, refusal.channels)

    def standing(self) -> set[UnitKey]:
        """Return the refused units the user has not confirmed: no unattended path may tag them.

        The refused units are kept in the draft file (``tag_review``) and so are the
        confirmations, so this set is the same in every session. It does not depend on the row's
        current reference or on the aliases saved since: a reference change (the earlier units
        of the row removed, the refused unit or one with its very header names now first) never
        lets fast mode tag a refused unit from its header names
        (:meth:`.notebook_channels_matrix.MatrixView.trust_headers`), and an untagged refused
        unit is never cleared (:meth:`reconcile` keeps it while its own tags are incomplete). It
        leaves this set only by an explicit action on it: its confirmation
        (:func:`.repeat_tags.override_holds`; a hand-tag of it is recorded as one,
        :meth:`hand_tagged`), or a row click that fills it.
        """
        if self.other_draft():
            return set()
        self.sync()  # memory behind the draft never empties this set (review as)
        answers = self.grid.store.answers()
        current = {u.key: u for u in self.grid.layouts}
        return {
            key
            for key in self.refused
            if key in current and not self._released(key, current[key], answers)
        }

    def _rows(self) -> dict:
        """Return every repeat folder and channel count with all its units, in any mode."""
        grid = self.grid
        colour = {u.key for u in grid.layouts if grid.colour(u)}
        return repeat_groups(grid.layouts, colour)

    def _offered(self) -> dict:
        """Return the rows: every repeat unticked; in fast mode only units outside the layout."""
        grid = self.grid
        groups = self._rows()
        if not grid.same_channels.value:
            return groups
        _, apart = grid.matrix.groups()
        outside = {u.key for u in apart} | grid.matrix.own
        outside |= {k for k, row in grid.matrix.rows.items() if row.state == REFUSED}
        kept = {k: tuple(u for u in g if u.key in outside) for k, g in groups.items()}
        return {k: g for k, g in kept.items() if g}

    def build(self, width: int) -> None:
        """Draw one row per offered repeat folder and channel count, and the current state."""
        self.drops, self.buttons = {}, {}
        # the guided step has its own rows (one per repeat when asked) and Submit
        groups = self._offered() if self.grid.labels and not self.grid.guided else {}
        lines = [f"{html.escape(unit_name(k))}: {text}" for k, text in self.listed().items()]
        listed = ui.status(
            "error",
            f"{len(lines)} imaging unit(s) not filled: their header contradicts their repeat "
            "folder's row, or their images do not agree with each other. Nothing is reordered "
            "or guessed. Each leaves this list only by an action on it: confirm its tags "
            "(override it) or tag it by hand; a header or alias that fits later never releases "
            "it. Correct or separate the images of a unit whose images disagree. The "
            "description is not saved while they are listed.",
            lines,
            raw=True,
        )
        failed = ui.status(
            "error",
            "Not saved: the refusals and confirmations could not be written to the draft.",
            ["They stay in this session and are saved again at the next change."],
        )
        self.message = self.notice + (listed if lines else "") + (failed if self.unsaved else "")
        self.notice = ""
        override = self.override.build(width)
        if not groups:
            self.box.children = [*([widgets.HTML(self.message)] if self.message else []), *override]
            return
        rows: list[widgets.Widget] = [
            ui.help_text(
                f"<b>{ACTION}</b>: set the channels of one repeat folder once; every "
                "condition's imaging units in it take these tags (each unit keeps its own list). "
                "A unit whose header contradicts the row, or whose images disagree, is listed, "
                "not filled."
                + (
                    " In fast mode only the units outside the layout are offered."
                    if self.grid.same_channels.value
                    else ""
                )
            )
        ]
        rows += [self._row(key, group, width) for key, group in groups.items()]
        if self.message:
            rows.append(widgets.HTML(self.message))
        rows += override
        box = widgets.VBox(rows)
        box.add_class("mb-tag-block")
        self.box.children = [box]

    def _row(self, key: GroupKey, group: tuple, width: int) -> widgets.Widget:
        """Build one row: the folder and the units it sets, one dropdown per C index, the button."""
        repeat, count = key
        answers = self.grid.store.answers()
        shown = repeat_shown(answers, group)
        tags = self.pending.get(key) or list(shown or (None,) * count)
        self.pending[key] = tags
        collision = pairing_collision(answers, repeat_members(self.grid.layouts, repeat))
        note = "every unit already holds these tags" if shown else "not set for every unit"
        if collision:
            note = (
                "paired with different biological repeats: "
                + ", ".join(f"{c} → {r}" for c, r in collision.items())
                + "; tag each condition's units on their own"
            )
        apart = repeat_apart(group)
        fills = [u for u in group if u.key not in apart]
        units = ", ".join(unit_name(u.key) for u in fills)
        if apart:
            note += (
                f"; {len(apart)} unit(s) with disagreeing images will not be filled: "
                + ", ".join(unit_name(k) for k in apart)
            )
        info = widgets.HTML(
            f"<b>{html.escape(repeat)}</b>: {count} channel(s); {len(fills)} unit(s) eligible "
            f"for this row: {html.escape(units)}<div class='mb-help'>{html.escape(note)}</div>"
        )
        options = [(CHOOSE, None), *((label, label) for label in self.grid.labels)]
        drops = []
        cells: list[widgets.Widget] = [info]
        for index in range(width):
            if index >= count:
                cells.append(widgets.HTML(""))
                continue
            value = tags[index] if tags[index] in self.grid.labels else None
            drop = widgets.Dropdown(
                options=options, value=value, layout=widgets.Layout(width="99%")
            )
            drop.observe(lambda c, k=key, i=index: self._picked(k, i, c["new"]), "value")
            drops.append(drop)
            cells.append(widgets.VBox([drop, widgets.HTML(f"<b>C{index}</b>")]))
        button = ui.button(f" {ACTION}", style="info", icon="check")
        button.layout = widgets.Layout(width="auto")
        button.disabled = collision is not None  # no silent fill across biological repeats
        button.on_click(lambda _, k=key, g=group: self.apply(k, g))
        self.drops[key], self.buttons[key] = drops, button
        cells.append(button)
        return widgets.GridBox(cells, layout=columns(width))

    def _picked(self, key: GroupKey, index: int, value: str | None) -> None:
        """Note one tag of a row; nothing is saved before the row's button (one action)."""
        self.pending[key][index] = value

    def apply(self, key: GroupKey, group: tuple) -> bool:
        """Save the row's tags for every offered unit its header does not contradict.

        The tags, their aliases and the review the click leaves (its new refusals; the filled
        units' refusals dropped) are ONE draft write (``TagGrid.commit(..., then)``), or one
        review-only write when it fills nothing. Memory takes it once saved (:meth:`adopt`): a
        failed write leaves the draft and memory as before, never tags without a refusal.
        """
        self.sync()  # the click's review is built from the draft's, never from stale memory
        tags = self.pending.get(key, [])
        named = [t for t in tags if t]
        answers = self.grid.store.answers()
        members = repeat_members(self.grid.layouts, key[0])  # the whole folder name, not this row
        if pairing_collision(answers, members) is not None:
            return False
        if (why := self._row_error(tags)) is not None or self.other_draft():
            self.notice = ui.status("error", "Not saved.", [why]) if why else OTHER_DRAFT
            self.grid.refresh()
            return False
        frozen = {k: old.channels for k, old in self.refused.items()}
        save, cells, refused = repeat_fill(answers, group, named, self.grid.labels, frozen)
        staged, lead = dict(self.refused), repeat_lead(group)
        found = {u: (_found_text(f), lead) for u, f in refused.items()}
        found |= {u: (html.escape(why), None) for u, why in repeat_apart(group).items()}
        for unit, (text, ref) in found.items():
            layout = next(u for u in group if u.key == unit)
            snapshot = frozen.get(unit, answers.channels)  # kept from the first refusal
            staged[unit] = Refusal(text, layout, ref, tuple(named), snapshot)
        for unit in save:  # filled by this click: its refusal goes with the same write
            staged.pop(unit, None)
        kept = {k: v for k, v in self.override.confirmed.items() if k in staged}
        review = self._review(kept, staged)
        self.staged = staged  # memory takes it in :meth:`adopt`, after the write succeeded
        try:
            ok = self._commit_user(save, cells, review) if save else self._save_review(review)
        finally:
            self.staged = None
        return ok and bool(save)

    def _save_review(self, review: TagReview) -> bool:
        """Write a click's whole tag review alone (it filled no unit); memory takes it if saved."""
        ok = self._write(review)
        if ok:
            self.adopt()
        else:
            self.notice = ui.status("error", "Not saved.", [self.grid.store.refusal()])
        self.grid.refresh()
        return ok

    @staticmethod
    def _row_error(tags: Sequence[str | None]) -> str | None:
        """Return why tags cannot be saved (a channel untagged or a label twice), else None."""
        named = [t for t in tags if t]
        if len(named) != len(tags) or len(set(named)) != len(named):
            return "Tag every channel of the row, each label once."
        return None

    def _commit_user(self, save: dict, cells: list, review: TagReview | None = None) -> bool:
        """Save units' tags as the user's action; in fast mode they are tagged on their own.

        In fast mode they are on their own before the save, so its one statement save and redraw
        (:meth:`.TagGrid.commit`) mark them; a refused save takes that back. ``review`` (the whole
        tag review) is set in the same change: one draft write, whose outcome is :attr:`unsaved`;
        no earlier refused review is retried inside it (it would lack the new confirmations).
        """
        then = None if review is None else (lambda a: replace(a, tag_review=review))
        own = set(self.grid.matrix.own)
        self.grid.same_channels.value and self.grid.matrix.own.update(save)  # marked by commit
        ok = self.grid.commit(save, cells, then)  # the review is adopted there (:meth:`adopt`)
        if not ok and self.grid.matrix.own != own:  # not saved: not on their own either
            self.grid.matrix.own = own
            self.grid.refresh()
        return ok


RELAPSED = "its header changed since you confirmed it: confirm it again"
AGREED = (
    "its images agree again since the refusal: its tags are never read from the new header; "
    "confirm them"
)
UNCONFIRMED = (
    "refused by its repeat folder's row and not confirmed since: a header or aliases that fit "
    "now never release it; confirm its tags"
)
UNTRUSTED = (
    "its own tags are incomplete: a refused unit is never tagged from its header, even one that "
    "fits now; confirm its tags"
)
"""Fast mode: a refused unit is left out of the header trust until confirmed (:meth:`standing`)."""


def _found_text(found: Sequence) -> str:
    """Return the refusal text of a unit's contradictions, one sentence each."""
    return "; ".join(html.escape(difference_text(d)) for d in found)


__all__ = ["ACTION", "RepeatRows"]
