"""*Tag channels*: **Tag this repeat folder (all conditions)**, one row per repeat folder.

Owner request of 20 November 2026: pick one biological-repeat folder (for example ``rep2``)
and set its channel list once; it is saved, as the user's tags, for every condition's imaging
units in that folder, technical repeats included (:mod:`.repeat_tags`). One row per repeat
folder name and channel count; a unit with another channel count is in its own row. The rows
are keyed by the **folder name** (*Tag channels* comes before the pairing on *Design*): when a
saved pairing puts that folder name of different conditions into different biological repeats,
the row is flagged with each condition's repeat and fills nothing.

Each unit's header is still checked against the row: a unit whose header contradicts it is
listed with the contradiction and not filled, never guessed. The list always shows the current
state: a listed unit leaves it once its current tags fit its header (the user tagged it), and
while any unit is listed the description is not published (:meth:`RepeatRows.listed`).

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
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_labels import tags_of
from .channel_matrix import REFUSED, difference_text, differences
from .draft_answers import ChannelAnswer
from .notebook_channels_override import OverrideEditor
from .notebook_channels_view import CHOOSE, columns, unit_name
from .repeat_review import RefusedUnit, TagReview
from .repeat_tags import (
    GroupKey,
    override_holds,
    pairing_collision,
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


class Refusal(NamedTuple):
    """A unit a row did not fill: the contradiction, the unit, the row's reference and tags."""

    text: str
    layout: UnitLayout
    lead: UnitLayout | None
    """The row's reference unit; None for a unit whose images disagree (:func:`repeat_apart`)."""
    tags: tuple[str, ...]
    """The row's tags at the click (what the header was compared with)."""
    channels: Mapping[str, ChannelAnswer]
    """The labels and aliases when the unit was first refused: what its header is read against.

    Aliases saved afterwards, by confirming or tagging other units, are not evidence for this
    unit: only the user confirming it (or a changed image header) clears the refusal.
    """


class RepeatRows:
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
        Only which units a click refused is remembered; whether one is still listed is read from
        its current tags against its header (:meth:`listed`), never from what happened since. A
        unit leaves this record after a save that fitted it succeeded (:meth:`apply`), or when
        the layouts change and it is gone or no longer contradicts its row (:meth:`reconcile`).
        """
        self.override = OverrideEditor(self)
        """The per-unit override of a listed unit (:mod:`.notebook_channels_override`)."""
        self.draft: object = None
        """Which draft the record above belongs to (:meth:`other_draft`); None before the first."""
        self.unsaved = False
        """True while the last save of the review was refused (retried at the next change)."""
        self.notice = ""
        """A one-shot message of the last click (shown once, then cleared)."""
        self.message = ""
        """What the rows say now: the last notice and every unit still listed."""

    def listed(self) -> dict[UnitKey, str]:
        """Return the units still listed: refused by a row, and not answered in agreement.

        Derived from the current answers on every call: a unit is listed while its tags are
        incomplete or contradict its header, and leaves the list when they fit it, however it got
        there (an image type alone changes nothing). A unit whose images disagree stays listed
        while they do: no tags make it one header. Nothing is listed from the memory of another
        draft (the notebook was pointed elsewhere and the grid is not open on it yet).
        """
        if self.other_draft():
            return {}
        answers = self.grid.store.answers()
        out = {}
        current = {u.key: u for u in self.grid.layouts}
        for key, (text, _, lead, _, _) in self.refused.items():
            layout = current.get(key)  # the unit as read now, never as at the click
            if layout is None:
                continue  # no longer in the experiment (:meth:`reconcile` drops it)
            if lead is None:
                if repeat_apart((layout,)):
                    out[key] = text
                continue
            tags = tags_of(layout, answers.units.get(key))
            confirmed = self.override.confirmed.get(key)
            if override_holds(confirmed, layout, tags):
                continue  # the user confirmed these tags (:mod:`.notebook_channels_override`)
            if None in tags or differences(layout, tags, lead, self.grid.labels, answers.channels):
                out[key] = text
        return out

    def reconcile(self) -> None:
        """Bring the refusals up to date with the grid's current layouts (on every opening).

        The same rules as the click, on the layouts as read now: a unit no longer in any offered
        row (removed from the folder, now colour or unusable) is dropped; a unit whose images
        disagree now is refused for that (:func:`.repeat_tags.repeat_apart`), and one whose
        images agree again is dropped; any other keeps its row's *current* reference
        (:func:`.repeat_tags.repeat_lead`) and is kept only while its header still contradicts
        the row's tags at the click, or its own tags (:func:`.channel_matrix.differences`), or
        its own tags are incomplete. The reference is taken from the whole row, not only from
        the units a mode offers. A
        surviving contradiction is never cleared because the screen reopened.
        """
        grid, kept = self.grid, {}
        offered = {u.key for g in self._offered().values() for u in g} if grid.labels else set()
        rows = self._rows()  # the whole row: its reference never depends on the mode
        answers = grid.store.answers()
        for key, old in self.refused.items():
            group, layout = self._placed(rows, key)
            if layout is None or key not in offered:
                continue
            why = repeat_apart(group).get(key)
            if why is not None:
                kept[key] = Refusal(html.escape(why), layout, None, old.tags, old.channels)
                continue
            lead = repeat_lead(group)
            if old.lead is None or lead is None:
                continue  # its images agree again: nothing to compare was ever clicked
            own = tags_of(layout, answers.units.get(key))
            found = self.contradictions(layout, old, lead)
            found = found or differences(layout, own, lead, grid.labels, answers.channels)
            if found:
                kept[key] = Refusal(_found_text(found), layout, lead, old.tags, old.channels)
            elif None in own:  # listed while its own tags are incomplete (:meth:`listed`)
                kept[key] = Refusal(
                    "its own tags are incomplete", layout, lead, old.tags, old.channels
                )
        self.refused = kept
        self.override.confirmed = {k: v for k, v in self.override.confirmed.items() if k in kept}
        self.persist()

    @staticmethod
    def _placed(rows: Mapping, key: UnitKey) -> tuple[tuple, UnitLayout | None]:
        """Return the row of units a unit is in, and the unit as read now (None when gone)."""
        group = next((g for g in rows.values() if any(u.key == key for u in g)), ())
        return group, next((u for u in group if u.key == key), None)

    def persist(self) -> bool:
        """Keep the refusals and confirmations in the draft file, when they changed.

        A new notebook on the draft restores them (:meth:`restore`): a refused unit counts only
        once that unit is confirmed, whatever the session. A refused save is not silent (the
        names card says "Not saved." and the grid shows a line, :attr:`unsaved`) and is retried
        at the next persist, because the file still differs from memory; the record stays in
        memory meanwhile, which is what this session reads. The record is only ever written into
        the draft it came from: with another draft open nothing is written. Returns False when
        the review could not be saved.
        """
        store, mine = self.grid.store, self._review()
        answers = store.answers()
        ok = True
        if self.other_draft():
            ok = False  # memory is another draft's: never written into this one
        elif (answers.tag_review or TagReview()) != mine:
            ok = store.update(replace(answers, tag_review=mine))
        self.unsaved = not ok
        return ok

    def other_draft(self) -> bool:
        """Return True when the draft open now is not the one the record in memory belongs to."""
        return self._draft_id() != self.draft

    def _draft_id(self) -> object:
        """Return which draft the store holds (None for a store that cannot tell)."""
        which = getattr(self.grid.store, "draft_id", None)
        return which() if which is not None else None

    def _forget(self) -> None:
        """Drop every refusal, confirmation and editor state: they belong to another draft."""
        self.refused, self.pending, self.notice, self.unsaved = {}, {}, "", False
        self.override.confirmed = {}
        self.override.editing, self.override.drafts = None, {}

    def _review(self) -> TagReview:
        """Return what is refused and confirmed now, as the draft file keeps it."""
        refused = {
            key: RefusedUnit(
                old.tags,
                None if old.lead is None else old.lead.key,
                {k: tuple(a.header_names) for k, a in old.channels.items() if a.header_names},
            )
            for key, old in self.refused.items()
        }
        return TagReview(refused, dict(self.override.confirmed))

    def restore(self, layouts: Sequence[UnitLayout]) -> None:
        """Take the refusals and confirmations of the draft file (a notebook opened on it).

        Run before the headers are trusted and the refusals reconciled (:meth:`reconcile`
        brings each up to date with the layouts, as after any re-check). The memory of the same
        draft is not replaced: this session's record is the draft's. Another draft (the notebook
        pointed at another folder or output) forgets the memory first and is restored from its
        own file, empty when it has no review. A unit that is gone is not restored; a reference
        that is gone is replaced by the current one at the next :meth:`reconcile`.
        """
        if self.other_draft():
            self._forget()
            self.draft = self._draft_id()
        review = self.grid.store.answers().tag_review
        if review is None or self.refused:
            return
        current = {u.key: u for u in layouts}
        for key, saved in review.refused.items():
            if (layout := current.get(key)) is not None:
                lead = layout if saved.reference is None else current.get(saved.reference, layout)
                aliases = {k: ChannelAnswer(None, v) for k, v in saved.aliases.items()}
                self.refused[key] = Refusal(
                    "", layout, None if saved.reference is None else lead, saved.tags, aliases
                )
        self.override.confirmed = {k: v for k, v in review.confirmed.items() if k in self.refused}

    def contradictions(self, layout: UnitLayout, refusal: Refusal, lead: UnitLayout) -> tuple:
        """Return where the unit's header contradicts the row's tags at the click.

        Read against the aliases of the refusal (:attr:`Refusal.channels`), never the ones
        saved since: the one rule of :func:`.channel_matrix.differences`, on that snapshot.
        """
        return differences(layout, refusal.tags, lead, self.grid.labels, refusal.channels)

    def standing(self) -> set[UnitKey]:
        """Return the units whose refusal still stands, whatever aliases were saved since.

        Fast mode does not tag these from their header names
        (:meth:`.notebook_channels_matrix.MatrixView.trust_headers`): a refused unit counts only
        once the user confirms it. A confirmed unit holds tags, so trusts nothing either way.
        """
        rows, out = self._rows(), set()
        if self.other_draft():
            return out
        for key, old in self.refused.items():
            group, layout = self._placed(rows, key)
            if layout is None or old.lead is None:
                continue
            lead = None if key in repeat_apart(group) else repeat_lead(group)  # the row as now
            if lead is None or self.contradictions(layout, old, lead):
                out.add(key)
        return out

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
        groups = self._offered() if self.grid.labels else {}
        lines = [f"{html.escape(unit_name(k))}: {text}" for k, text in self.listed().items()]
        listed = ui.status(
            "error",
            f"{len(lines)} imaging unit(s) not filled: their header contradicts their repeat "
            "folder's row, or their images do not agree with each other. Nothing is reordered "
            "or guessed; tag the first on their own, and correct or separate the images of the "
            "others. The description is not saved while they are listed.",
            lines,
            raw=True,
        )
        failed = (
            ui.status(
                "error",
                "Not saved: the refusals and confirmations could not be written to the draft.",
                ["They stay in this session and are saved again at the next change."],
            )
            if self.unsaved
            else ""
        )
        self.message = self.notice + (listed if lines else "") + failed
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
        """Save the row's tags for every offered unit its header does not contradict."""
        tags = self.pending.get(key, [])
        named = [t for t in tags if t]
        answers = self.grid.store.answers()
        members = repeat_members(self.grid.layouts, key[0])  # the whole folder name, not this row
        if pairing_collision(answers, members) is not None:
            return False
        if (why := self._row_error(tags)) is not None:
            self.notice = ui.status("error", "Not saved.", [why])
            self.grid.refresh()
            return False
        frozen = {k: old.channels for k, old in self.refused.items()}
        save, cells, refused = repeat_fill(answers, group, named, self.grid.labels, frozen)
        for unit, found in refused.items():
            layout = next(u for u in group if u.key == unit)
            snapshot = frozen.get(unit, answers.channels)  # kept from the first refusal
            self.refused[unit] = Refusal(
                _found_text(found), layout, repeat_lead(group), tuple(named), snapshot
            )
        for unit, why in repeat_apart(group).items():
            layout = next(u for u in group if u.key == unit)
            snapshot = frozen.get(unit, answers.channels)
            self.refused[unit] = Refusal(html.escape(why), layout, None, tuple(named), snapshot)
        if not save:
            self.persist()
            self.grid.refresh()
            return False
        ok = self._commit_user(save, cells)
        if ok:  # a refusal is dropped only once the save that fitted the unit succeeded
            for unit in save:
                self.refused.pop(unit, None)
                self.override.confirmed.pop(unit, None)
        if not self.persist() and ok:
            self.grid.refresh()  # shows the line that the review is not saved
        return ok

    @staticmethod
    def _row_error(tags: Sequence[str | None]) -> str | None:
        """Return why tags cannot be saved (a channel untagged or a label twice), else None."""
        named = [t for t in tags if t]
        if len(named) != len(tags) or len(set(named)) != len(named):
            return "Tag every channel of the row, each label once."
        return None

    def _commit_user(self, save: dict, cells: list) -> bool:
        """Save units' tags as the user's action; in fast mode they are then tagged on their own."""
        ok = self.grid.commit(save, cells)
        if ok and self.grid.same_channels.value:  # the user's action: tagged on their own
            self.grid.matrix.own.update(save)
            self.grid.matrix.sync_statement()
            self.grid.refresh()
        return ok


def _found_text(found: Sequence) -> str:
    """Return the refusal text of a unit's contradictions, one sentence each."""
    return "; ".join(html.escape(difference_text(d)) for d in found)


__all__ = ["ACTION", "RepeatRows"]
