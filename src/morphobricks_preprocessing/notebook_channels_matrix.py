"""*Tag channels* in fast mode: **Every imaging unit has the same channels in the same order**.

Owner decisions of 16 November 2026 (the rules are :mod:`.channel_matrix`, pure; this module
shows them and turns clicks into answers). The statement is ticked by default and is the user's
declared assumption; unticked, the page is the strict per-unit blocks of
:class:`.notebook_channels_grid.TagGrid`, exactly as before.

- A unit whose header names every channel with a confirmed label (exactly, or a saved alias) is
  tagged so on opening, with no click (*from its header*).
- The layout's row **Tags of every imaging unit** (read from the first unit in folder order,
  the *first tagged unit*) is tagged once and fills every unit without header names: one row per
  imaging unit (grouped by condition / biological repeat, then technical repeat), one column per
  C index. Then go on, or tune any cell of any unit with its own dropdown: that unit is *edited*,
  the others *same as the first tagged unit*. A unit's dropdowns show only what is saved.
- A contradiction is refused, never prefilled: a unit with another channel count, or whose
  header names are in another order or are other names, is not filled; the step lists every
  such unit with the fix, **Tag this unit on its own** (or all of them at once), which moves it
  under *Tagged on their own* with its own dropdowns. **Add another layout** tags several units
  of one other channel count with a row of their own.
- On a reopen, a new unit without header names is shown filled and saved only by **Tag the new
  units like the first tagged unit**; colour (RGB) and untaggable units keep the blocks.
- Unticked, the draft is the strict page's draft after the same user actions, by construction:
  the tags saved with no click go to the draft only, every user save goes to both
  (:mod:`.strict_draft`), and unticking writes the strict page's draft back.

Each saved unit gets exactly the tags tagging it by hand gives (:func:`.channel_answers.
with_tags`). While ticked, the statement itself is in the draft (``channel_layout`` and each
unit's ``channels_from``, Spec ADR 0006, :mod:`.fast_statement`), so the published description
is a fast-mode file; unticked, the draft is strict. A published file reopens in its own mode.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced
from .channel_labels import UnitLayout, tags_of
from .channel_matrix import (
    EDITED,
    HEADER,
    NEW,
    REFUSED,
    SAME,
    MatrixRow,
    apart_text,
    difference_text,
    fill,
    layout_groups,
    matrix_rows,
    set_template,
    trusted,
)
from .draft_answers import ChannelAnswer, UnitKey
from .fast_statement import published_fast, statement_changed, with_statement
from .notebook_channels_view import (
    CHOOSE,
    blank_chip,
    chip,
    columns,
    quoted,
    restore_strict,
    save_unattended,
    unit_name,
    user_units,
)
from .notebook_values import natural_key

if TYPE_CHECKING:
    from .notebook_channels_grid import TagGrid

STATEMENT = "Every imaging unit has the same channels in the same order"
STATE_TEXT = {
    SAME: "same as the first tagged unit",
    EDITED: "<b>edited</b>",
    HEADER: "from its header names",
    NEW: "new: not tagged yet, shown as the first tagged unit",
    REFUSED: "refused: its header contradicts the row; tag it on its own",
}
ORANGE_TEXT = "color:#92400E"
Template = tuple[str | None, ...]


class TagMatrix:
    """The matrix of the *same channels* statement, drawn inside the tag grid's card."""

    def __init__(self, grid: TagGrid) -> None:
        self.grid = grid
        self.extra: list[int] = []
        """Channel counts the user added a layout for (this session; never saved)."""
        self.templates: dict[UnitKey, Template] = {}
        """Each layout's row of tags, by its first unit (this session; never saved)."""
        self.rows: dict[UnitKey, MatrixRow] = {}
        self.template_cells: dict[tuple[UnitKey, int], widgets.Dropdown] = {}
        self.own: set[UnitKey] = set()
        """Units the user switched to tagging on their own (this session; never saved)."""
        self.fill_buttons: list[widgets.Button] = []
        self.own_buttons: dict[UnitKey, widgets.Button] = {}
        self.all_own: list[widgets.Button] = []
        self.layout_buttons: dict[int, widgets.Button] = {}
        self.stopped = widgets.HTML()
        self.clicked: set[UnitKey] = set()
        """Units whose cells the user clicked this session: only they may become ``unit``."""
        self.marked_own: set[UnitKey] = set()
        """Units marked ``unit`` when the page opened (ADR 0006): kept, never turned back."""
        self.setting_tick = False
        """True while the tick is set to the draft's own mode (not the user's switch)."""
        self.saved: dict[str, ChannelAnswer] = {}
        """The labels and their aliases saved when the page opened: the only aliases trusted."""

    def reset(self) -> None:
        """Forget the session's layouts, rows of tags and switches (new units, or a toggle)."""
        self.extra, self.templates, self.own = [], {}, set()

    def opening(self) -> None:
        """Note the aliases saved when the page opens for new units (the ones trusted)."""
        answers = self.grid.store.answers()
        self.saved = dict(answers.channels)
        self.marked_own = {k for k, u in answers.units.items() if u.channels_from == "unit"}

    def trust_headers(self) -> bool:
        """Tag every untagged unit whose header names its channels (no click); True if saved.

        A save with no click: the strict page's draft does not take it
        (:func:`.notebook_channels_view.save_unattended`).
        """
        if not self.grid.same_channels.value or not self.grid.labels:
            return False
        units = [u for u in self.grid.layouts if not self.grid.colour(u)]
        answers, cells = trusted(self.grid.store.answers(), units, self.grid.labels, self.saved)
        if not cells:
            return False
        return save_unattended(self.grid.store, synced(answers, self.grid.layouts, cells))

    def tick_for_mode(self) -> None:
        """Tick the statement as a published file's mode is, or the user's switch of it.

        ADR 0006, section 10: a published file reopens in its own mode and is never switched
        here. A new description keeps the page's tick (ticked by default, FC1).
        """
        answers = self.grid.store.answers()
        published = published_fast(answers)
        if published is None:
            return
        mode = published != answers.switch_mode
        if self.grid.same_channels.value != mode:
            self.setting_tick = True
            try:
                self.grid.same_channels.value = mode
            finally:
                self.setting_tick = False

    def sync_statement(self, cells: Sequence[tuple[UnitKey, int]] = ()) -> bool:
        """Save the statement and the units' markers the tick and the tags give (ADR 0006).

        A save with no click (the draft only, :func:`.fast_statement.with_statement`): it
        follows the user's tags and tick, and dates a new or changed list with the day of that
        action. ``cells`` are the cells the user just clicked: their units are the user's
        tags (only such a unit may become ``unit``). True when something changed.
        """
        self.clicked.update(key for key, _ in cells)
        answers = self.grid.store.answers()
        groups, _ = self.groups()
        lead = groups[0][0] if groups else None
        new = with_statement(
            answers,
            self.grid.layouts,
            lead,
            self.template(groups[0]) if groups else (),
            (
                self.own | self.marked_own,
                self.clicked | user_units(self.grid.store, "tags"),
                user_units(self.grid.store, "type"),
            ),
            ticked=bool(self.grid.same_channels.value),
            day=date.today().isoformat(),
        )
        if not statement_changed(answers, new):
            return False
        return save_unattended(self.grid.store, new)

    def take_back(self) -> None:
        """Write the strict page's draft back (the statement unticked).

        Every user save since the first no-click tags was applied to it as to the draft
        (:mod:`.strict_draft`): the units tagged with no click are untagged, a unit the user
        clicked on keeps the tags of the click, and the aliases are those the strict page holds
        after the same saves.
        """
        restore_strict(self.grid.store)

    # What is in the matrix -----------------------------------------------------------------

    def groups(self) -> tuple[list[tuple[UnitLayout, ...]], list[UnitLayout]]:
        """Return the layouts (each led by its first unit) and the units tagged on their own."""
        ordered = sorted(self.grid.layouts, key=lambda u: tuple(map(natural_key, u.key)))
        colour = {u.key for u in ordered if self.grid.colour(u)}
        return layout_groups(ordered, colour, self.extra, self.own)

    def members(self) -> set[UnitKey]:
        """Return the keys of the units the matrix shows (in a layout or on their own)."""
        groups, apart = self.groups()
        return {u.key for g in groups for u in g} | {u.key for u in apart}

    def template(self, group: Sequence[UnitLayout]) -> Template:
        """Return a layout's row of tags: as set this session, else its first unit's saved tags.

        The first unit's tags are kept as the row once every channel of it is tagged (a reopen,
        or the unit tagged by its own dropdowns), so tuning that unit later marks it edited.
        """
        lead = group[0]
        labels = self.grid.labels  # a label taken out of the list is untagged everywhere
        tags = self.templates.get(lead.key)
        if tags is None:
            tags = tags_of(lead, self.grid.store.answers().units.get(lead.key))
            if None not in tags:
                self.templates[lead.key] = tags
        return tuple(t if t in labels else None for t in tags)

    def tag_on_own(self, keys: Sequence[UnitKey]) -> None:
        """Switch these units to tagging on their own (the user's click; nothing is saved)."""
        self.own.update(keys)
        self.grid.refresh()

    def add_layout(self, count: int) -> None:
        """Make the units with ``count`` channels another layout (the user's click)."""
        if count not in self.extra:
            self.extra.append(count)
        self.grid.refresh()

    # Building ------------------------------------------------------------------------------

    def build(self, width: int) -> list[tuple[widgets.Widget, tuple[UnitLayout, ...]]]:
        """Return each layout's table, then the units tagged on their own, with their units.

        Empty while the statement is unticked: the page is then the blocks alone.
        """
        self.rows, self.template_cells, self.fill_buttons, self.own_buttons = {}, {}, [], {}
        self.all_own, self.layout_buttons, self.stopped.value = [], {}, ""
        if not self.grid.same_channels.value:
            return []
        groups, apart = self.groups()
        out = [(self._layout(group, width, n), group) for n, group in enumerate(groups)]
        counts = [g[0].channels for g in groups]
        if apart:
            out.append((self._apart(apart, width, counts), tuple(apart)))
        self.stopped.value = self._stopped(apart, counts)
        return out

    def _stopped(self, apart: Sequence[UnitLayout], counts: list[int]) -> str:
        """Say that the step stops while units contradict the statement, listing every one."""
        answers = self.grid.store.answers()
        lines = [
            f"{html.escape(unit_name(r.layout.key))}: "
            + "; ".join(html.escape(difference_text(d)) for d in r.differences)
            for r in self.rows.values()
            if r.state == REFUSED
        ]
        lines += [
            f"{html.escape(unit_name(u.key))}: {html.escape(apart_text(u, counts, self.own))}"
            for u in apart
            if u.key not in self.own and None in tags_of(u, answers.units.get(u.key))
        ]
        if not lines:
            return ""
        return ui.status(
            "error",
            f"Stopped: {len(lines)} imaging unit(s) contradict “{STATEMENT.lower()}”.",
            [
                *lines,
                "Nothing is reordered or guessed. Fix: tag those units on their own (the "
                "button beside each, or all of them at once).",
            ],
            raw=True,
        )

    def _layout(self, group: Sequence[UnitLayout], width: int, number: int) -> widgets.Widget:
        """Build one layout: what it is, its row of tags, its table and its buttons."""
        lead = group[0]
        template = self.template(group)
        rows = matrix_rows(group, self.grid.store.answers(), self.grid.labels, template)
        name = "The layout" if number == 0 else "Another layout"
        out: list[widgets.Widget] = [
            ui.help_text(
                f"<b>{name}: {lead.channels} channel(s), {len(group)} imaging unit(s).</b> Tag "
                "the row <i>Tags of every imaging unit</i> once, looking at the first tagged "
                f"unit, <b>{html.escape(unit_name(lead.key))}</b>: every unit takes the same "
                "tags. Then go on, or change any cell of any unit to tune it."
            ),
            self._template_row(group, template, width),
        ]
        last: tuple[str, ...] | None = None
        for row in rows:
            self.rows[row.layout.key] = row
            if row.layout.key[:2] != last:
                last = row.layout.key[:2]
                heading = html.escape(" / ".join(p for p in last if p != "single") or "single")
                out.append(widgets.HTML(f"<div style='margin-top:8px'><b>{heading}</b></div>"))
            out.append(self._row(row, template, width, row.layout is lead))
        out += self._buttons(rows)
        if self.grid.rows.pictures and self.grid.rows.thumbs is not None:
            pictures = self.grid.rows.row(lead, width, False, [], widgets.HTML(""))
            out.append(ui.accordion("Pictures of the first tagged unit", [pictures]))
        box = widgets.VBox(out)
        box.add_class("mb-tag-block")
        return box

    def _template_row(
        self, group: Sequence[UnitLayout], template: Template, width: int
    ) -> widgets.Widget:
        """Build the layout's row of tags: one dropdown per C index, for every unit."""
        lead = group[0]
        cells: list[widgets.Widget] = [widgets.HTML("<b>Tags of every imaging unit</b>")]
        for index in range(width):
            if index >= lead.channels:
                cells.append(widgets.HTML(""))
                continue
            cells.append(self._template_cell(group, index, template[index]))
        cells.append(widgets.HTML(""))
        row = widgets.GridBox(cells, layout=columns(width))
        row.add_class("mb-tag-template")
        return row

    def _template_cell(
        self, group: Sequence[UnitLayout], index: int, tag: str | None
    ) -> widgets.Widget:
        """Build one dropdown of the layout's row of tags."""
        options = [(CHOOSE, None), *((label, label) for label in self.grid.labels)]
        tag = tag if tag in self.grid.labels else None
        drop = widgets.Dropdown(options=options, value=tag, layout=widgets.Layout(width="99%"))
        drop.observe(
            lambda change, g=tuple(group), i=index: self._template_changed(g, i, change["new"]),
            "value",
        )
        self.template_cells[(group[0].key, index)] = drop
        mark = blank_chip("not tagged") if tag is None else self._chip(tag)
        return widgets.VBox([drop, widgets.HTML(f"{mark}<br><b>C{index}</b>")])

    def _buttons(self, rows: Sequence[MatrixRow]) -> list[widgets.Widget]:
        """Build the buttons that save the new rows and switch the refused ones to their own."""
        out: list[widgets.Widget] = []
        new = tuple(r for r in rows if r.state == NEW)
        if new:
            text = f" Tag the {len(new)} new unit(s) like the first tagged unit"
            out.append(self._button(text, new, self.fill_buttons))
        refused = tuple(r.layout.key for r in rows if r.state == REFUSED)
        if len(refused) > 1:
            button = ui.button(
                f" Tag the {len(refused)} refused unit(s) on their own", icon="scissors"
            )
            button.layout = widgets.Layout(width="auto")
            button.on_click(lambda _, k=refused: self.tag_on_own(k))
            self.all_own.append(button)
            out.append(button)
        return out

    def _button(
        self, text: str, rows: tuple[MatrixRow, ...], kept: list[widgets.Button]
    ) -> widgets.Button:
        """Return a button that saves the tags shown for ``rows``."""
        button = ui.button(text, style="info", icon="check")
        button.layout = widgets.Layout(width="auto")
        button.on_click(lambda _, r=rows: self._fill(r))
        kept.append(button)
        return button

    def _row(self, row: MatrixRow, template: Template, width: int, first: bool) -> widgets.Widget:
        """Build one unit's row: its name and state, its own dropdowns, its image type."""
        layout = row.layout
        tech = layout.key[2]
        name = "the folder" if tech == "single" else html.escape(tech)
        style = ORANGE_TEXT if row.state in (NEW, REFUSED) else ""
        state = STATE_TEXT[row.state]
        if first:
            state = f"first tagged unit; {state}"
        info: list[widgets.Widget] = [
            widgets.HTML(f"{name}<div class='mb-help' style='{style}'>{state}</div>")
        ]
        if row.state == REFUSED:
            button = ui.button(" Tag this unit on its own", icon="scissors")
            button.on_click(lambda _, k=(layout.key,): self.tag_on_own(k))
            self.own_buttons[layout.key] = button
            info.append(button)
        saved = tags_of(layout, self.grid.store.answers().units.get(layout.key))
        flagged = {d.index for d in row.differences}
        cells: list[widgets.Widget] = [widgets.VBox(info)]
        for index in range(width):
            if index >= layout.channels:
                cells.append(widgets.HTML(""))
            elif row.state == REFUSED:  # never prefilled: its header names only
                cells.append(widgets.HTML(self._refused_mark(row, index, index in flagged)))
            else:
                mark = self._mark(row, index, template)
                cells.append(self.grid.dropdown([layout], index, saved[index], False, mark))
        typed = self.grid.place_types
        cells.append(self.grid.types.cell(layout) if typed else widgets.HTML(""))
        return widgets.GridBox(cells, layout=columns(width))

    def _chip(self, tag: str) -> str:
        return chip(tag, self.grid.labels, self.grid.shown_as(tag))

    def _mark(self, row: MatrixRow, index: int, template: Template) -> str:
        """Return what is said under one cell: the tag and how it came, and the header name."""
        name = f"<b>C{index}</b> {quoted(row.layout.name(index))}"
        tag = row.shown[index]
        if tag is None:
            return f"{blank_chip('not tagged')}<br>{name}"
        mark = self._chip(tag)
        if row.state == NEW:
            mark += f" <span class='mb-help' style='{ORANGE_TEXT}'>shown, not saved</span>"
        elif row.state == EDITED and tag != template[index]:
            mark += " <span class='mb-help'>edited</span>"
        return f"{mark}<br>{name}"

    @staticmethod
    def _refused_mark(row: MatrixRow, index: int, contradicts: bool) -> str:
        """Return a refused unit's cell: its header name, marked where it contradicts the row."""
        name = f"<b>C{index}</b> {quoted(row.layout.name(index))}"
        if not contradicts:
            return f"{blank_chip('not tagged')}<br>{name}"
        return (
            f"{blank_chip('refused')}<br>{name} <span class='mb-help' style='{ORANGE_TEXT}'>"
            "contradicts the row</span>"
        )

    def _apart(self, units: Sequence[UnitLayout], width: int, counts: list[int]) -> widgets.Widget:
        """Build the units tagged on their own, and the buttons that add another layout."""
        out: list[widgets.Widget] = [
            ui.help_text(
                "<b>Tagged on their own</b>: these units do not fit the layout above. Each "
                "needs its own tags before this page is complete."
            )
        ]
        for layout in units:
            info = (
                f"<b>{html.escape(unit_name(layout.key))}</b><div class='mb-help' "
                f"style='{ORANGE_TEXT}'>{html.escape(apart_text(layout, counts, self.own))}</div>"
            )
            tags, proposed = self.grid.effective(layout)
            cells: list[widgets.Widget] = [widgets.HTML(info)]
            for index in range(width):
                cells.append(
                    widgets.HTML("")
                    if index >= layout.channels
                    else self.grid.dropdown([layout], index, tags[index], index in proposed)
                )
            typed = self.grid.place_types
            cells.append(self.grid.types.cell(layout) if typed else widgets.HTML(""))
            out.append(widgets.GridBox(cells, layout=columns(width)))
        by_count: dict[int, list[UnitLayout]] = {}
        for layout in units:
            if layout.uniform:
                by_count.setdefault(layout.channels, []).append(layout)
        out += [self._layout_button(c, same) for c, same in by_count.items() if len(same) > 1]
        box = widgets.VBox(out)
        box.add_class("mb-tag-block")
        return box

    def _layout_button(self, count: int, units: Sequence[UnitLayout]) -> widgets.Button:
        """Return **Add another layout** for the units with ``count`` channels."""
        button = ui.button(
            f" Add another layout: the {len(units)} unit(s) with {count} channels",
            style="info",
            icon="plus",
            tooltip="Say that these units have the same channels in the same order as each "
            "other, and tag them once",
        )
        button.layout = widgets.Layout(width="auto")
        button.on_click(lambda _, c=count: self.add_layout(c))
        self.layout_buttons[count] = button
        return button

    # Answers -------------------------------------------------------------------------------

    def _template_changed(self, group: Sequence[UnitLayout], index: int, value: str | None) -> None:
        """Save one C index of the layout's row of tags for every unit that follows it."""
        if self.grid.building:
            return
        tags = list(self.template(group))
        tags[index] = value
        if value and value in [t for i, t in enumerate(tags) if i != index]:
            self.grid.refuse_twice(value, tags.index(value) + 1)
            return
        self._set(group, tuple(tags))

    def _set(self, group: Sequence[UnitLayout], tags: Template) -> None:
        """Set the layout's row of tags and save it for the units that follow it."""
        before = self.template(group)
        units, cells = set_template(
            self.grid.store.answers(), self.grid.layouts, group, before, tags, self.grid.labels
        )
        self.templates[group[0].key] = tags
        if not self.grid.commit(units, cells):
            self.templates[group[0].key] = before  # refused: the row shows what is saved
            self.grid.refresh()

    def _fill(self, rows: Sequence[MatrixRow]) -> None:
        """Save the tags shown for these rows (the user's click)."""
        self.grid.commit(*fill(rows))


__all__ = ["STATEMENT", "TagMatrix"]
