"""*Tag channels*: override one listed unit, starting from its row's tags (owner requirement).

A unit whose image headers disagree with its repeat's row is listed
(:mod:`.notebook_channels_repeat`). From the list the user can override that unit: the editor
starts from the row's tags, shows each header name under its dropdown, and highlights the
C indices where the header disagrees, so that only what is different is changed. Nothing is
saved while the user edits (the draft lives here, like ``RepeatRows.pending``); the unit counts
as tagged only after **Confirm these tags**. The confirmation is the tags and the unit's own
header names (:class:`.repeat_tags.Override`); it lapses when either changes
(:func:`.repeat_tags.override_holds`). Channels are never guessed: a draft that is incomplete
or repeats a label is refused, and a refused save confirms nothing.

Another listed unit with the same header names (at every C index) as a unit the user confirmed
stays listed and offers **Confirm the same tags** (and **Confirm all N like it** when several
do): the same save and record as the editor, one click per confirmation, never automatic.
Highlights are read against the aliases of the refusal (:attr:`.RepeatRows.refused`), so
confirming one unit never changes what another one shows.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_labels import tags_of
from .channel_matrix import difference_text, differences
from .notebook_channels_view import (
    CHOOSE,
    ORANGE,
    ORANGE_FILL,
    blank_chip,
    chip,
    columns,
    quoted,
    unit_name,
)
from .repeat_tags import Override, counted, header_names, override_holds, recount

if TYPE_CHECKING:
    from .channel_labels import UnitLayout
    from .draft_answers import UnitKey
    from .notebook_channels_repeat import RepeatRows

START = "Override this unit (start from the row's tags)"


class OverrideEditor:
    """The per-unit override of the listed units, drawn under the rows."""

    def __init__(self, rows: RepeatRows) -> None:
        self.rows = rows
        self.editing: UnitKey | None = None
        """The unit being edited; its tags are in :attr:`drafts` and nowhere else."""
        self.drafts: dict[UnitKey, list[str | None]] = {}
        self.confirmed: dict[UnitKey, Override] = {}
        """The units whose tags the user confirmed, with what the header said then."""
        self.buttons: dict[UnitKey, widgets.Button] = {}
        self.same_buttons: dict[UnitKey, widgets.Button] = {}
        """**Confirm the same tags**, per listed unit like a confirmed one."""
        self.all_buttons: dict[UnitKey, widgets.Button] = {}
        """**Confirm all N like it**, per confirmed unit that several listed units are like."""
        self.confirm_button: widgets.Button | None = None
        self.cancel_button: widgets.Button | None = None
        self.cells: dict[tuple[UnitKey, int], widgets.Dropdown] = {}

    def build(self, width: int) -> list[widgets.Widget]:
        """Return the widgets: a button per listed unit with a reference, and the open editor."""
        self.buttons, self.cells, self.same_buttons, self.all_buttons = {}, {}, {}, {}
        self.confirm_button = self.cancel_button = None
        current = {u.key: u for u in self.rows.grid.layouts}
        offered = self._offered(current)
        if self.editing not in offered:
            self.editing = None
        likes = self._likes(offered, current)
        out: list[widgets.Widget] = []
        for key in offered:
            if key == self.editing:
                out.append(self._editor(key, current[key], width))
                continue
            button = ui.button(f" {START}", style="warning", icon="pencil")
            button.on_click(lambda _, k=key: self.start(k))
            self.buttons[key] = button
            box: list[widgets.Widget] = [
                widgets.HTML(f"<b>{html.escape(unit_name(key))}</b>"),
                button,
            ]
            if key in likes:
                box += self._same(key, *likes[key])
            out.append(widgets.VBox(box))
        for source in dict.fromkeys(src for src, _ in likes.values()):  # first listed first
            keys = [k for k, (src, _) in likes.items() if src == source]
            if len(keys) > 1:
                out.append(self._all(source, keys, likes[keys[0]][1]))
        return out

    def _offered(self, current: dict[UnitKey, UnitLayout]) -> list[UnitKey]:
        """Return the listed units that have a reference (a unit whose images disagree has none)."""
        rows = self.rows
        return [
            key
            for key in rows.listed()
            if key in current and key in rows.refused and rows.refused[key].lead is not None
        ]

    def _likes(
        self, offered: list[UnitKey], current: dict[UnitKey, UnitLayout]
    ) -> dict[UnitKey, tuple[UnitKey, tuple[str, ...]]]:
        """Return, per listed unit, the confirmed unit with the same header names and its tags.

        Same header names at every C index (none missing), and the confirmation still holds. When
        confirmed units with those names hold different tags, none is offered (never a guess).
        """
        grid = self.rows.grid
        units = grid.store.answers().units
        held: dict[tuple, dict[tuple[str, ...], UnitKey]] = {}
        for key, over in self.confirmed.items():
            layout = current.get(key)
            if layout is None or None in over.names or not set(over.tags) <= set(grid.labels):
                continue
            if override_holds(over, layout, tags_of(layout, units.get(key))):
                held.setdefault(over.names, {}).setdefault(over.tags, key)
        out = {}
        for key in offered:
            same = held.get(header_names(current[key]), {})
            if len(same) == 1:
                tags, source = next(iter(same.items()))
                out[key] = (source, tags)
        return out

    def _same(self, key: UnitKey, source: UnitKey, tags: tuple[str, ...]) -> list[widgets.Widget]:
        """Return the sentence and button of one listed unit like a confirmed one."""
        text = (
            f"same headers as {html.escape(unit_name(source))}, which you confirmed as "
            f"{html.escape(', '.join(tags))}"
        )
        button = ui.button(" Confirm the same tags", style="success", icon="check")
        button.on_click(lambda _, k=key: self.confirm_same(k))
        self.same_buttons[key] = button
        return [widgets.HTML(text), button]

    def _all(self, source: UnitKey, keys: list[UnitKey], tags: tuple[str, ...]) -> widgets.Widget:
        """Return the button that confirms every listed unit like ``source`` in one save."""
        label = f" Confirm all {len(keys)} like {unit_name(source)}"
        button = ui.button(label, style="success", icon="check")
        button.on_click(lambda _, s=source: self.confirm_all(s))
        self.all_buttons[source] = button
        return button

    def confirm_same(self, key: UnitKey) -> bool:
        """Confirm one listed unit with the tags of the confirmed unit it is like."""
        likes = self._likes(*self._current())
        return key in likes and self._confirm({key: likes[key][1]})

    def confirm_all(self, source: UnitKey) -> bool:
        """Confirm every listed unit like ``source``; none if any would be refused."""
        likes = self._likes(*self._current())
        todo = {k: tags for k, (src, tags) in likes.items() if src == source}
        return bool(todo) and self._confirm(todo)

    def _current(self) -> tuple[list[UnitKey], dict[UnitKey, UnitLayout]]:
        """Return the listed units with a reference, and every unit as read now."""
        current = {u.key: u for u in self.rows.grid.layouts}
        return self._offered(current), current

    def start(self, key: UnitKey) -> None:
        """Open the editor on a unit with the row's tags; nothing is saved."""
        self.editing = key
        self.drafts[key] = list(self.rows.refused[key].tags)
        self.rows.grid.refresh()

    def _known(self, tags: Sequence[str | None], layout: UnitLayout) -> list[str | None]:
        """Return tags with a label that is no longer one left blank (renamed or removed).

        Tags of another channel count than the unit's now are never cut or padded: every
        channel is blank, to be chosen again (:func:`.repeat_tags.recount`).
        """
        if not counted(tags, layout):
            return [None] * layout.channels
        return [t if t in self.rows.grid.labels else None for t in tags]

    def cancel(self) -> None:
        """Close the editor and drop its draft; nothing was saved."""
        if self.editing is not None:
            self.drafts.pop(self.editing, None)
        self.editing = None
        self.rows.grid.refresh()

    def _picked(self, key: UnitKey, index: int, value: str | None) -> None:
        """Note one tag of the draft and redraw its highlights; nothing is saved."""
        self.drafts[key][index] = value
        self.rows.grid.refresh()

    def _editor(self, key: UnitKey, layout: UnitLayout, width: int) -> widgets.Widget:
        """Build the editor: a dropdown per C index, the header and its disagreement under it."""
        rows = self.rows
        grid, refusal = rows.grid, rows.refused[key]
        lead = refusal.lead
        draft = self.drafts[key] = self._known(self.drafts[key], layout)  # labels may change
        moved = recount(refusal.tags, layout)
        found = {  # read against the aliases of the refusal, as before any confirmation
            d.index: d
            for d in differences(layout, draft, lead, grid.labels, refusal.channels)
            if moved is None
        }
        options = [(CHOOSE, None), *((label, label) for label in grid.labels)]
        info = f"<b>{html.escape(unit_name(key))}</b>: " + (
            html.escape(moved)
            if moved
            else "the row's tags are the start; change only what is different"
        )
        cells: list[widgets.Widget] = [widgets.HTML(info)]
        for index in range(width):
            if index >= layout.channels:
                cells.append(widgets.HTML(""))
                continue
            drop = widgets.Dropdown(
                options=options, value=draft[index], layout=widgets.Layout(width="99%")
            )
            drop.observe(lambda c, k=key, i=index: self._picked(k, i, c["new"]), "value")
            self.cells[(key, index)] = drop
            cells.append(
                widgets.VBox(
                    [
                        drop,
                        widgets.HTML(f"<b>C{index}</b> {quoted(layout.name(index))}"),
                        widgets.HTML(self._note(index, draft, refusal.tags, found, grid.labels)),
                    ]
                )
            )
        self.confirm_button = ui.button(
            f" Confirm these tags for {unit_name(key)}", style="success", icon="check"
        )
        self.confirm_button.on_click(lambda _: self.confirm())
        self.cancel_button = ui.button(" Cancel", icon="times")
        self.cancel_button.on_click(lambda _: self.cancel())
        cells.append(widgets.VBox([self.confirm_button, self.cancel_button]))
        return widgets.GridBox(cells, layout=columns(width))

    @staticmethod
    def _note(index: int, draft: list, row: tuple, found: dict, labels: list) -> str:
        """Return what is under a cell: the header's disagreement, or as the row / changed."""
        if index in found:
            return (
                f"<div style='border:2px solid {ORANGE};background:{ORANGE_FILL};padding:2px 6px;"
                f"border-radius:4px'>{html.escape(difference_text(found[index]))}</div>"
            )
        tag = draft[index]
        shown = chip(tag, labels) if tag else blank_chip("not tagged")
        if len(row) != len(draft):  # another channel count: no row tag to be "as"
            return shown
        return f"{'as the row' if tag == row[index] else 'changed'} {shown}"

    def confirm(self) -> bool:
        """Save the draft as this unit's tags, and count the unit as tagged only if that worked."""
        key = self.editing
        if key is None or not self._confirm({key: self.drafts[key]}):
            return False  # refused: no confirmation, still listed, the editor stays open
        self.editing = None
        self.rows.grid.refresh()
        return True

    def _confirm(self, drafts: dict[UnitKey, Sequence[str | None]]) -> bool:
        """Save each unit's tags in one save and confirm them; nothing is saved if any is refused.

        The one path of the editor and of the one-click buttons: the same validation
        (``RepeatRows._row_error``) and save (``_commit_user``). What a confirmation needs (the
        unit's header names) is read before the save. The tags and the draft's tag review with
        the new confirmations are ONE draft write (``TagGrid.commit(..., then)``): a refused
        write saves neither, and nothing is counted as confirmed.
        """
        rows = self.rows
        grid, layouts = rows.grid, {u.key: u for u in rows.grid.layouts}
        save, cells, records = {}, [], {}
        if rows.other_draft():  # the record is another draft's: never written into this one
            rows.notice = ui.status(
                "error",
                "Not saved: these refusals and confirmations belong to another draft.",
                ["Open Channels again to read this draft's own."],
            )
            grid.refresh()
            return False
        for key, draft in drafts.items():
            why = rows._row_error(draft)
            if not counted(draft, layouts[key]):  # never cut or padded to the unit's count
                why = f"Tag each of the unit's {layouts[key].channels} channels, each label once."
            if why is not None:
                rows.notice = ui.status("error", "Not saved.", [why])
                grid.refresh()
                return False
            layout = layouts[key]
            save[key] = tuple(t for t in draft if t)
            cells += [(key, i) for i in range(layout.channels)]
            records[key] = Override(save[key], header_names(layout))
        review = rows._review({**self.confirmed, **records})
        if not rows._commit_user(save, cells, review):
            return False  # one write: neither the tags nor the confirmation were saved
        self.confirmed.update(records)
        for key in save:
            self.drafts.pop(key, None)
        grid.refresh()
        return True


__all__ = ["START", "OverrideEditor"]
