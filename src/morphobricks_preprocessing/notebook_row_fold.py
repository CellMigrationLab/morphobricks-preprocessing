"""Folding the unchanged rows of a reopened experiment (owner decision, UX proposal 2026-11-09).

When a saved experiment is reopened, a row whose folders are found exactly as saved and whose
question is already answered folds behind one line ("4 unchanged folders, answered as saved")
with an **open** toggle. A row with a new folder, or a folder whose image count changed, stays
open and is marked orange. Without a saved experiment nothing folds. Display only: a folded row
keeps its widgets and answers, and opening it shows them as they are.

:func:`reopen_units` reads the status of every unit from the reconciliation of the published
file with the folders (units matched by their exact folder labels); :func:`row_status` gives a
row's status from the units it holds; :class:`RowFold` arranges a card's rows.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping, Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .description_reconcile import Reconciliation, Status
from .draft_answers import UnitKey
from .notebook_channels_view import ORANGE, ORANGE_FILL
from .validation import plural

UNCHANGED, NEW, CHANGED = "unchanged", "new", "changed"
WORDS = {NEW: "new since saved", CHANGED: "changed since saved"}


def reopen_units(rec: Reconciliation | None) -> Mapping[UnitKey, str] | None:
    """Return each unit's status against the saved file, or None without a saved file.

    ``unchanged`` for a unit found as saved, ``changed`` for one whose member's image count
    changed, ``new`` for a unit the saved file does not have.
    """
    if rec is None:
        return None
    counted = {(c.repeat_id, c.condition_id) for c in rec.counts}
    out: dict[UnitKey, str] = {}
    for unit in rec.units:
        key = (unit.folders.condition, unit.folders.source_repeat, unit.folders.technical)
        if unit.status == Status.NEW:
            out[key] = NEW
        elif unit.status == Status.UNCHANGED:
            changed = (unit.repeat_id, unit.condition_id) in counted
            out[key] = CHANGED if changed else UNCHANGED
    return out


def row_status(units: Mapping[UnitKey, str] | None, keys: Iterable[UnitKey], answered: bool) -> str:
    """Return a row's status: empty without a saved file, else unchanged, new or changed.

    A row is ``unchanged`` (it folds) only when every unit it holds is unchanged and its
    question is answered; an unknown unit counts as new.
    """
    if units is None:
        return ""
    found = [units.get(key, NEW) for key in keys]
    if not found:
        return ""
    if all(s == UNCHANGED for s in found):
        return UNCHANGED if answered else ""
    return NEW if NEW in found else CHANGED


def mark(widget: widgets.Widget, status: str) -> widgets.Widget:
    """Return a row marked orange with what changed, or the row itself."""
    if status not in WORDS:
        return widget
    word = widgets.HTML(
        f"<span style='display:inline-block;padding:0 8px;border-radius:999px;color:#9A3412;"
        f"font-weight:600;background:{ORANGE_FILL};border:1px solid {ORANGE}'>"
        f"{html.escape(WORDS[status])}</span>"
    )
    return widgets.VBox(
        [word, widget],
        layout=widgets.Layout(border_left=f"3px solid {ORANGE}", padding="0 0 0 8px"),
    )


class RowFold:
    """One card's fold: the unchanged rows behind a line and an open toggle.

    Once opened it stays open for the card's later rebuilds.
    """

    def __init__(self, what: str) -> None:
        # ``what`` is the singular noun of a row ("folder"); the line says "3 unchanged folders".
        self.what = what
        self.opened = False
        self.toggle = ui.button(" open", icon="chevron-down", tooltip="Show the unchanged rows")
        self.toggle.on_click(lambda _: self._flip())
        self.folded = widgets.VBox()
        self.line = widgets.HTML()
        self.bar = widgets.HBox(
            [self.line, self.toggle], layout=widgets.Layout(align_items="center")
        )
        self.count = 0

    def _flip(self) -> None:
        self.opened = not self.opened
        self._show()

    def _show(self) -> None:
        ui.set_visible(self.folded, self.opened)
        self.toggle.description = " close" if self.opened else " open"
        self.toggle.icon = "chevron-up" if self.opened else "chevron-down"

    def arrange(self, rows: Sequence[tuple[widgets.Widget, str]]) -> list[widgets.Widget]:
        """Return the rows to show, then the fold line and the unchanged rows under it.

        The other rows keep their order; new or changed ones are marked orange.
        """
        shown = [mark(row, status) for row, status in rows if status != UNCHANGED]
        folded = [row for row, status in rows if status == UNCHANGED]
        self.count = len(folded)
        if not folded:
            return shown
        self.folded.children = folded
        self.line.value = (
            f"<span style='color:#166534;font-weight:600'>✓ "
            f"{html.escape(plural(len(folded), 'unchanged ' + self.what))}</span> "
            "<span class='mb-help'>answered as saved</span>"
        )
        self._show()
        return [*shown, self.bar, self.folded]


__all__ = ["CHANGED", "NEW", "UNCHANGED", "RowFold", "mark", "reopen_units", "row_status"]
