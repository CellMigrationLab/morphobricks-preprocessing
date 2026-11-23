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
from .repeat_review import Override, RefusedUnit, TagReview
from .repeat_tags import (
    GroupKey,
    header_names,
    override_holds,
    pairing_collision,
    recount,
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
        there. A unit in no row (colour, with channels R, G and B, or unusable) is not listed
        while it is so: there are no channel tags to confirm, and its refusal is kept
        (:meth:`reconcile`) to list it again once it is back in a row. A unit whose images
        disagree stays listed while they do: no tags make it one header. Nothing is listed from
        the memory of another draft (the notebook was pointed elsewhere and the grid is not open
        on it yet).
        """
        if self.other_draft():
            return {}
        answers = self.grid.store.answers()
        out = {}
        current = {u.key: u for g in self._rows().values() for u in g}
        for key, (text, _, lead, _, _) in self.refused.items():
            layout = current.get(key)  # the unit as read now, never as at the click
            if layout is None:
                continue  # gone (dropped by :meth:`reconcile`), or colour or unusable now
            if lead is None:
                if repeat_apart((layout,)):
                    out[key] = text
                continue
            tags = tags_of(layout, answers.units.get(key))
            confirmed = self.override.confirmed.get(key)
            if relapsed(confirmed, layout):  # its own header changed: confirmed for another one
                out[key] = text or RELAPSED
                continue
            if override_holds(confirmed, layout, tags):
                continue  # the user confirmed these tags (:mod:`.notebook_channels_override`)
            if None in tags or differences(layout, tags, lead, self.grid.labels, answers.channels):
                out[key] = text
        return out

    def reconcile(self) -> None:
        """Bring the refusals up to date with the grid's current layouts (on every opening).

        The same rules as the click, on the layouts as read now: only a unit removed from the
        folder is dropped; a unit in no row now (colour, unusable, or no labels yet) keeps its
        refusal as it is, to be read again once it is back in a row (never released by a
        detour), and a unit fast mode does not offer stays (only the user confirming it releases
        it); a unit whose channel count
        changed is kept, to have every channel chosen again (:func:`.repeat_tags.recount`); a
        unit whose images
        disagree now is refused for that (:func:`.repeat_tags.repeat_apart`), and one whose
        images agree again is dropped; any other keeps its row's *current* reference
        (:func:`.repeat_tags.repeat_lead`) and is kept only while its header still contradicts
        the row's tags at the click, or its own tags (:func:`.channel_matrix.differences`), or
        its own tags are incomplete. The reference is taken from the whole row, not only from
        the units a mode offers. A
        surviving contradiction is never cleared because the screen reopened.
        """
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
            lead = repeat_lead(group)
            if old.lead is None or lead is None:
                continue  # its images agree again: nothing to compare was ever clicked
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
            if override_holds(confirmed, layout, own):  # kept: the source of the one-click offer
                kept[key] = Refusal(_found_text(found), layout, lead, old.tags, old.channels)
            elif found:
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
        return self.draft_id() != self.draft

    def draft_id(self) -> object:
        """Return which draft the store holds (None for a store that cannot tell)."""
        which = getattr(self.grid.store, "draft_id", None)
        return which() if which is not None else None

    def _forget(self) -> None:
        """Drop every refusal, confirmation and editor state: they belong to another draft."""
        self.refused, self.pending, self.notice, self.unsaved = {}, {}, "", False
        self.override.confirmed = {}
        self.override.editing, self.override.drafts = None, {}

    def _review(self, confirmed: Mapping[UnitKey, Override] | None = None) -> TagReview:
        """Return what is refused and confirmed now, as the draft file keeps it.

        ``confirmed`` replaces the confirmations in memory (the record a confirmation is about to
        save together with its tags, :meth:`.OverrideEditor._confirm`).
        """
        refused = {
            key: RefusedUnit(
                old.tags,
                None if old.lead is None else old.lead.key,
                {k: tuple(a.header_names) for k, a in old.channels.items() if a.header_names},
            )
            for key, old in self.refused.items()
        }
        return TagReview(refused, dict(self.override.confirmed if confirmed is None else confirmed))

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
            self.draft = self.draft_id()
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
        (:func:`.repeat_tags.override_holds`), a row click that fills it, or the user tagging it.
        """
        if self.other_draft():
            return set()
        answers = self.grid.store.answers()
        current = {u.key: u for u in self.grid.layouts}
        out = set()
        for key in self.refused:
            layout = current.get(key)
            if layout is None:
                continue
            tags = tags_of(layout, answers.units.get(key))
            if not override_holds(self.override.confirmed.get(key), layout, tags):
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

    def _commit_user(self, save: dict, cells: list, review: TagReview | None = None) -> bool:
        """Save units' tags as the user's action; in fast mode they are then tagged on their own.

        With ``review`` (the whole tag review: memory and what this save confirms), the draft's
        tag review is set in the same change: the tags and the review are one draft write, saved
        together or not at all, and :attr:`unsaved` is that write's outcome. No earlier refused
        review is retried inside it (:meth:`.TagGrid.commit`): memory does not hold the new
        confirmations yet, so a retry would write the review without them over this one.
        """
        then = None if review is None else (lambda a: replace(a, tag_review=review))
        ok = self.grid.commit(save, cells, then)
        if ok and review is not None:
            self.unsaved = False  # this very write held the whole review
        if ok and self.grid.same_channels.value:  # the user's action: tagged on their own
            self.grid.matrix.own.update(save)
            self.grid.matrix.sync_statement()
            self.grid.refresh()
        return ok


RELAPSED = "its header changed since you confirmed it: confirm it again"


def relapsed(confirmed: Override | None, layout: UnitLayout) -> bool:
    """Return True when the unit was confirmed for other header names than its own now.

    The confirmation no longer covers it (:func:`.repeat_tags.override_holds`), and it is listed
    again even when its new header fits its row and its tags are complete: a changed header is
    reviewed by the user, never counted unasked.
    """
    return confirmed is not None and header_names(layout) != confirmed.names


def _found_text(found: Sequence) -> str:
    """Return the refusal text of a unit's contradictions, one sentence each."""
    return "; ".join(html.escape(difference_text(d)) for d in found)


__all__ = ["ACTION", "RepeatRows"]
