"""*Tag channels*: override one listed unit, starting from its row's tags (owner requirement).

A unit whose image headers disagree with its repeat's row is listed
(:mod:`.notebook_channels_repeat`). From the list the user can override that unit: the editor
starts from the row's tags, shows each header name under its dropdown, and highlights the
C indices where the header disagrees, so that only what is different is changed. Nothing is
saved while the user edits (the draft lives here, like ``RepeatRows.pending``); the unit counts
as tagged only after **Confirm these tags**. The confirmation is the tags and the disagreements
they were confirmed with (:class:`.repeat_tags.Override`); it lapses when either changes
(:func:`.repeat_tags.override_holds`). Channels are never guessed: a draft that is incomplete
or repeats a label is refused, and a refused save confirms nothing.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
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
from .repeat_tags import Override

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
        self.confirm_button: widgets.Button | None = None
        self.cancel_button: widgets.Button | None = None
        self.cells: dict[tuple[UnitKey, int], widgets.Dropdown] = {}

    def build(self, width: int) -> list[widgets.Widget]:
        """Return the widgets: a button per listed unit with a reference, and the open editor."""
        self.buttons, self.cells = {}, {}
        self.confirm_button = self.cancel_button = None
        rows = self.rows
        listed = rows.listed()
        current = {u.key: u for u in rows.grid.layouts}
        offered = [
            key
            for key in listed
            if key in current and key in rows.refused and rows.refused[key].lead is not None
        ]
        if self.editing not in offered:
            self.editing = None
        out: list[widgets.Widget] = []
        for key in offered:
            if key == self.editing:
                out.append(self._editor(key, current[key], width))
                continue
            button = ui.button(f" {START}", style="warning", icon="pencil")
            button.on_click(lambda _, k=key: self.start(k))
            self.buttons[key] = button
            out.append(
                widgets.VBox([widgets.HTML(f"<b>{html.escape(unit_name(key))}</b>"), button])
            )
        return out

    def start(self, key: UnitKey) -> None:
        """Open the editor on a unit with the row's tags; nothing is saved."""
        self.editing = key
        self.drafts[key] = list(self.rows.refused[key].tags)
        self.rows.grid.refresh()

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
        lead, draft = refusal.lead, self.drafts[key]
        found = {
            d.index: d
            for d in differences(layout, draft, lead, grid.labels, grid.store.answers().channels)
        }
        options = [(CHOOSE, None), *((label, label) for label in grid.labels)]
        info = (
            f"<b>{html.escape(unit_name(key))}</b>: the row's tags are the start; "
            "change only what is different"
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
        return f"{'as the row' if tag == row[index] else 'changed'} {shown}"

    def confirm(self) -> bool:
        """Save the draft as this unit's tags, and count the unit as tagged only if that worked."""
        key = self.editing
        if key is None:
            return False
        rows = self.rows
        grid, draft = rows.grid, self.drafts[key]
        if (why := rows._row_error(draft)) is not None:
            rows.notice = ui.status("error", "Not saved.", [why])
            grid.refresh()
            return False
        layout = next(u for u in grid.layouts if u.key == key)
        tags = tuple(t for t in draft if t)
        if not rows._commit_user({key: tags}, [(key, i) for i in range(layout.channels)]):
            return False  # refused: no confirmation, still listed, the editor stays open
        answers = grid.store.answers()
        found = differences(layout, tags, rows.refused[key].lead, grid.labels, answers.channels)
        self.confirmed[key] = Override(tags, found)
        self.drafts.pop(key, None)
        self.editing = None
        grid.refresh()
        return True


__all__ = ["START", "OverrideEditor"]
