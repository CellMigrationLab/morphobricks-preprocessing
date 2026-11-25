"""*Tag channels*, guided (owner design of 25 November 2026): a question, a few rows, Submit.

The shipped notebook's channel step (the stepper turns it on, :attr:`.TagGrid.guided`). It
replaces the per-unit grid, where every dropdown change saved and redrew the page:

1. **Is the channel order the same for the whole dataset?** (*Yes* / *No*), said with the
   channel counts found when the images do not all have the same number of channels.
2. *Yes*: one row of dropdowns per channel count (one row when the count is the same
   everywhere). *No*: **Same order within each biological repeat folder** (one row per repeat folder
   and channel count) or **Tag every imaging unit** (one row per unit).
3. **Submit**: the only save. The dropdowns change this form only (no draft write, no redraw of
   the page); Submit checks every row (each channel tagged, each label once) and the image
   headers, and saves the tags, their review and the statement in one draft write
   (:mod:`.notebook_channels_submit`). A unit whose header contradicts its row is listed with
   the existing refusal flow (:mod:`.notebook_channels_override`), never reordered or guessed.

The rows (:mod:`.channel_scope`) keep what was chosen: switching the answer and back, or any
redraw, shows the same choices, and a narrower row starts from the broader row's choice.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_labels import tags_of
from .channel_scope import (
    FROM_BROADER,
    FROM_CHOICE,
    FROM_HEADER,
    FROM_SAVED,
    REPEAT,
    UNIT,
    WHOLE,
    FormRow,
    RowKey,
    apart,
    colour_unit,
    counts,
    form_rows,
    row_error,
    scope_of_draft,
    seed,
    taggable,
    untyped,
)
from .notebook_channels_submit import collisions, refused_text, submit
from .notebook_channels_view import CHOOSE, blank_chip, chip, columns, quoted, rgb_chip, unit_name

if TYPE_CHECKING:
    from .notebook_channels_grid import TagGrid

SAME_QUESTION = "Is the channel order the same for the whole dataset?"
YES, NO = "yes", "no"
NARROW_QUESTION = "Then how far does one channel order go?"
NARROW_OPTIONS = [
    ("Same order within each biological repeat folder", REPEAT),
    ("Tag every imaging unit", UNIT),
]
SUBMIT = "Submit"
SOURCE_TEXT = {
    FROM_CHOICE: "",
    FROM_BROADER: "from your choice for a broader row (its repeat folder or the whole "
    "dataset): check it before Submit",
    FROM_SAVED: "as saved",
    FROM_HEADER: "from the header names: check them before Submit",
}
UNITS_LISTED = 6
"""Units named in a row's line before the rest are counted."""


class ChannelGuide:
    """The guided channel step, drawn in the tag grid's card while the grid is guided."""

    def __init__(self, grid: TagGrid) -> None:
        self.grid = grid
        self.pending: dict[RowKey, list[str | None]] = {}
        """What the user chose in each row this session (never saved before Submit)."""
        self.drops: dict[RowKey, list[widgets.Dropdown]] = {}
        self.notes: dict[RowKey, widgets.HTML] = {}
        self.rows: list[FormRow] = []
        self.renders = 0
        """How many times the form was rebuilt (a dropdown change never rebuilds it)."""
        self._building = False
        self.same = widgets.ToggleButtons(options=[("Yes", YES), ("No", NO)], value=None)
        self.same.observe(lambda _: self._answered(), "value")
        self.narrow = widgets.ToggleButtons(options=NARROW_OPTIONS, value=None)
        self.narrow.observe(lambda _: self._answered(), "value")
        self.same_text = widgets.HTML()
        self.narrow_box = widgets.VBox([widgets.HTML(f"<b>{NARROW_QUESTION}</b>"), self.narrow])
        self.questions = widgets.VBox([self.same_text, self.same, self.narrow_box])
        self.rows_box = widgets.VBox()
        self.submit_button = ui.button(f" {SUBMIT}", style="success", icon="check", main=True)
        self.submit_button.on_click(lambda _: self.submit())
        self.message = widgets.HTML()
        self.saved_box = widgets.HTML()
        self.draft_note = widgets.HTML()
        self.widget = widgets.VBox(
            [
                self.draft_note,
                self.questions,
                self.rows_box,
                self.submit_button,
                self.message,
                self.saved_box,
            ]
        )

    # State -------------------------------------------------------------------------------

    def reset(self, layouts: Sequence = ()) -> None:
        """Forget the form (another draft or other units) and answer as the draft already does.

        A fast-mode draft answers *Yes*; a strict draft whose saved tags one answer reproduces
        exactly is pre-answered with it (:func:`.channel_scope.scope_of_draft`), its rows
        showing the saved tags. Otherwise a draft with saved tags says they are kept until the
        question is answered (review of #546, F1).
        """
        self.pending = {}
        self.message.value = ""
        answers = self.grid.store.answers()
        found = scope_of_draft(answers, layouts)
        tagged = any(u.image_type != "rgb" and any(u.channels) for u in answers.units.values())
        self._building = True
        try:
            self.narrow.value = found if found in (REPEAT, UNIT) else None
            self.same.value = None if found is None else (YES if found == WHOLE else NO)
        finally:
            self._building = False
        if found is not None and answers.channel_layout is None:
            self.draft_note.value = ui.status(
                "info",
                "Pre-answered from your saved tags: the rows show them.",
                ["Change the answer or a row and Submit to change them."],
            )
        elif found is None and tagged:
            self.draft_note.value = ui.status(
                "info", "Saved per-unit tags are kept; answer the question to change them."
            )
        else:
            self.draft_note.value = ""

    @property
    def scope(self) -> str | None:
        """Return the answer: :data:`WHOLE`, :data:`REPEAT`, :data:`UNIT`, or None (not yet)."""
        if self.same.value == YES:
            return WHOLE
        if self.same.value == NO:
            return self.narrow.value
        return None

    def _answered(self) -> None:
        """Open the rows of the new answer (the form only: nothing is saved)."""
        if self._building:
            return
        self.message.value = ""
        self.render()

    # Building ----------------------------------------------------------------------------

    def render(self) -> None:
        """Rebuild the questions, the rows of the answer and the saved tags (no save)."""
        self.renders += 1
        grid = self.grid
        answers = grid.store.answers()
        units = answers.units
        found = counts(grid.layouts, units)
        self.drops, self.notes = {}, {}
        waiting = self._waiting(found)
        ui.set_visible(self.questions, not waiting)
        ui.set_visible(self.submit_button, not waiting and self.scope is not None)
        if waiting:
            self.rows, self.rows_box.children = [], [widgets.HTML(waiting)]
            self.saved_box.value = self._saved_text()
            return
        self.same_text.value = self._same_text(found)
        ui.set_visible(self.narrow_box, self.same.value == NO)
        scope = self.scope
        self.rows = form_rows(grid.layouts, units, scope) if scope else []
        width = max((r.count for r in self.rows), default=1)
        built: list[widgets.Widget] = []
        if self.rows:
            built.append(widgets.HTML(self._rows_text(scope, len(self.rows))))
            built += [self._row(row, width) for row in self.rows]
        left = apart(taggable(grid.layouts, units))
        if left and scope:
            names = ", ".join(html.escape(unit_name(u.key)) for u in left)
            built.append(
                widgets.HTML(
                    ui.status(
                        "warn",
                        f"{len(left)} imaging unit(s) in no row: their images disagree.",
                        [f"{names}: listed at Submit; correct or separate their images."],
                        raw=True,
                    )
                )
            )
        self.rows_box.children = built
        self.saved_box.value = self._saved_text()

    def _waiting(self, found: Sequence[int]) -> str:
        """Return why no row can be shown yet (HTML), or ``""``."""
        grid = self.grid
        units = grid.store.answers().units
        if untyped(grid.layouts, units):
            return ui.status(
                "info",
                "Waiting for the image type.",
                ["Confirm the image type above first: colour folders have R, G and B."],
            )
        if not found:
            if any(colour_unit(u, units) for u in grid.layouts):
                return ui.status("ok", "Every folder is colour (RGB): R, G and B, nothing to tag.")
            return ui.status("info", "No folder can be tagged.", ["See the folders above."])
        if not grid.labels:
            return ui.status("info", "Waiting for the labels.", ["Confirm the label list first."])
        return ""

    @staticmethod
    def _same_text(found: Sequence[int]) -> str:
        """Return the first question, with the channel counts when they differ."""
        if len(found) == 1:
            return f"<b>{SAME_QUESTION}</b>"
        listed = " and ".join(str(c) for c in found)
        return (
            f"<b>{SAME_QUESTION}</b><div class='mb-help'>The images have {listed} channels. "
            "<i>Yes</i> opens one list per channel count (each number of channels has its own "
            "order).</div>"
        )

    @staticmethod
    def _rows_text(scope: str, count: int) -> str:
        """Return the line above the rows."""
        what = {
            WHOLE: "one list per channel count, for every imaging unit with that many channels",
            REPEAT: "one list per biological-repeat folder (and channel count), for every "
            "condition's imaging units in it",
            UNIT: "one list per imaging unit",
        }[scope]
        return (
            f"<div class='mb-help'><b>{count} row(s)</b>: {what}. Choosing changes only this "
            f"form; <b>{SUBMIT}</b> checks the image headers and saves.</div>"
        )

    def _row(self, row: FormRow, width: int) -> widgets.Widget:
        """Build one row: what it tags, a dropdown per C index with its header name, a note."""
        grid = self.grid
        tags, source = seed(row, self.pending, grid.store.answers(), grid.labels)
        options = [(CHOOSE, None), *((label, label) for label in grid.labels)]
        drops: list[widgets.Dropdown] = []
        cells: list[widgets.Widget] = [widgets.HTML(self._row_title(row))]
        for index in range(width):
            if index >= row.count:
                cells.append(widgets.HTML(""))
                continue
            drop = widgets.Dropdown(
                options=options, value=tags[index], layout=widgets.Layout(width="99%")
            )
            drop.observe(lambda c, k=row.key, i=index: self._picked(k, i, c["new"]), "value")
            drops.append(drop)
            cells.append(widgets.VBox([drop, widgets.HTML(self._header_text(row, index))]))
        note = widgets.HTML(self._note(tags, source))
        cells.append(note)
        self.drops[row.key], self.notes[row.key] = drops, note
        return widgets.GridBox(cells, layout=columns(width))

    @staticmethod
    def _row_title(row: FormRow) -> str:
        """Return the first cell of a row: what it tags."""
        names = [unit_name(u.key) for u in row.units]
        shown = ", ".join(names[:UNITS_LISTED])
        if len(names) > UNITS_LISTED:
            shown += f" and {len(names) - UNITS_LISTED} more"
        if row.scope == WHOLE:
            head = f"{row.count} channels"
        elif row.scope == REPEAT:
            head = f"{row.key[1]}, {row.count} channels"
        else:
            head = unit_name(row.key[1])
        return (
            f"<b>{html.escape(head)}</b><div class='mb-help'>{len(names)} imaging unit(s): "
            f"{html.escape(shown)}</div>"
        )

    @staticmethod
    def _header_text(row: FormRow, index: int) -> str:
        """Return the C index and the header name under a dropdown (evidence, never a tag)."""
        names = {u.name(index) for u in row.units}
        if len(names) == 1:
            return f"<b>C{index}</b> {quoted(next(iter(names)))}"
        return f"<b>C{index}</b> <i>header names differ</i>"

    def _note(self, tags: Sequence[str | None], source: str) -> str:
        """Return the note at the end of a row: what is still missing, or where it came from."""
        why = row_error(tags)
        if why is not None and any(tags):
            return f"<span class='mb-help' style='color:#92400E'>{html.escape(why)}</span>"
        return f"<span class='mb-help'>{SOURCE_TEXT.get(source, '')}</span>"

    def _picked(self, key: RowKey, index: int, value: str | None) -> None:
        """Note one dropdown of a row in the form; no draft write, no redraw of the page."""
        drops = self.drops.get(key, [])
        self.pending[key] = [d.value for d in drops]
        if key in self.notes:
            self.notes[key].value = self._note(self.pending[key], FROM_CHOICE)

    def _saved_text(self) -> str:
        """Return the saved tags of every imaging unit, read-only (empty before any save)."""
        grid = self.grid
        units = grid.store.answers().units
        lines = []
        for layout in sorted(grid.layouts, key=lambda u: u.key):
            answer = units.get(layout.key)
            if colour_unit(layout, units):
                shown = " ".join(rgb_chip(t) for t in ("R", "G", "B"))
            elif layout.usable and answer is not None and any(answer.channels):
                tags = tags_of(layout, answer)
                shown = " ".join(
                    chip(t, grid.labels) if t else blank_chip("not tagged") for t in tags
                )
            else:
                continue
            lines.append(f"<tr><td>{html.escape(unit_name(layout.key))}</td><td>{shown}</td></tr>")
        if not lines:
            return ""
        return (
            "<div class='mb-help' style='margin-top:10px'><b>Saved tags</b> (change them with "
            f"the rows above and {SUBMIT})</div><table>{''.join(lines)}</table>"
        )

    # Submit ------------------------------------------------------------------------------

    def values(self) -> dict[RowKey, list[str | None]]:
        """Return what every row of the form shows now."""
        return {row.key: [d.value for d in self.drops.get(row.key, [])] for row in self.rows}

    def submit(self) -> bool:
        """Check the form and save it in one draft write; nothing is saved when it is refused."""
        grid = self.grid
        scope = self.scope
        if scope is None:
            return self._refuse(["Answer the question above first."])
        shown = self.values()
        # one row per unit: a row left blank is no answer for that unit now (it keeps what it
        # holds), so the others can be saved while a refused unit waits (review F5)
        skipped = [r for r in self.rows if scope == UNIT and not any(shown[r.key])]
        todo = [r for r in self.rows if r not in skipped]
        if not todo:
            return self._refuse(["Tag at least one row."])
        problems = []
        for row in todo:
            why = row_error(shown[row.key])
            if why is not None:
                problems.append(f"{html.escape(self._plain_title(row))}: {why}")
        problems += collisions(grid, todo)
        if problems:
            return self._refuse(problems)
        rows = [(row, [t for t in shown[row.key] if t]) for row in todo]
        units = grid.store.answers().units
        left = apart(taggable(grid.layouts, units))
        fast = scope == WHOLE and len(counts(grid.layouts, units)) == 1
        ok = submit(grid, rows, left, fast=fast)
        if ok:
            self.pending.update({key: list(value) for key, value in shown.items()})
            text = refused_text(grid)
            if skipped:
                names = ", ".join(html.escape(self._plain_title(r)) for r in skipped)
                text += ui.status(
                    "info", f"Left as they were (their rows are blank): {names}.", raw=True
                )
            self.message.value = text or ui.status("ok", "Saved: every row's tags are saved.")
        else:
            self.message.value = (
                grid.message or grid.by_repeat.notice or ui.status("error", "Not saved.")
            )
        return ok

    @staticmethod
    def _plain_title(row: FormRow) -> str:
        if row.scope == WHOLE:
            return f"{row.count} channels"
        if row.scope == REPEAT:
            return f"{row.key[1]}, {row.count} channels"
        return unit_name(row.key[1])

    def _refuse(self, lines: Sequence[str]) -> bool:
        """Say why Submit saved nothing."""
        self.message.value = ui.status("error", "Not saved.", lines, raw=True)
        return False


__all__ = ["NARROW_QUESTION", "SAME_QUESTION", "SUBMIT", "ChannelGuide"]
