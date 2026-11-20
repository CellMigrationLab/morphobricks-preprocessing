"""*Tag channels*: **Tag this biological repeat (all conditions)**, one row per repeat.

Owner request of 20 November 2026: pick one biological repeat (for example ``rep2``) and set
its channel list once; it is saved, as the user's tags, for every condition's imaging units in
that repeat, technical repeats included (:mod:`.repeat_tags`). One row per biological-repeat
folder and channel count; a unit with another channel count is in its own row. Each unit's
header is still checked against the row: a unit whose header contradicts it is listed with the
contradiction and not filled, never guessed.

- **Fast mode** (Spec ADR 0006): the rows are shown for the repeats that have units outside the
  layout (another channel count, a refused header, or switched to their own); setting a row is
  the user's action for each unit it fills, which is then tagged on its own
  (``channels_from = "unit"``). This is how repeats with an extra channel are described.
- **Strict mode** (the statement unticked): a row for every repeat, a shortcut that fills every
  unit of the repeat across conditions, each unit saved with its own tags.

On reopening, a row shows the tags every unit of its repeat already holds, as a convenience
only: the saved data are per unit (the description format does not change).
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_matrix import REFUSED, difference_text
from .notebook_channels_view import CHOOSE, columns, unit_name
from .repeat_tags import GroupKey, repeat_fill, repeat_groups, repeat_shown

if TYPE_CHECKING:
    from .notebook_channels_grid import TagGrid

ACTION = "Tag this biological repeat (all conditions)"


class RepeatRows:
    """The rows of tags per biological repeat, drawn under the tag grid."""

    def __init__(self, grid: TagGrid) -> None:
        self.grid = grid
        self.box = widgets.VBox()
        self.pending: dict[GroupKey, list[str | None]] = {}
        self.drops: dict[GroupKey, list[widgets.Dropdown]] = {}
        self.buttons: dict[GroupKey, widgets.Button] = {}
        self.message = ""

    def _offered(self) -> dict:
        """Return the rows to show: every repeat unticked; in fast mode those outside the layout."""
        grid = self.grid
        colour = {u.key for u in grid.layouts if grid.colour(u)}
        groups = repeat_groups(grid.layouts, colour)
        if not grid.same_channels.value:
            return groups
        _, apart = grid.matrix.groups()
        outside = {u.key for u in apart} | grid.matrix.own
        outside |= {k for k, row in grid.matrix.rows.items() if row.state == REFUSED}
        return {k: g for k, g in groups.items() if any(u.key in outside for u in g)}

    def build(self, width: int) -> None:
        """Draw one row per offered repeat and channel count (nothing without labels)."""
        self.drops, self.buttons = {}, {}
        groups = self._offered() if self.grid.labels else {}
        if not groups:
            self.box.children = []
            return
        rows: list[widgets.Widget] = [
            ui.help_text(
                f"<b>{ACTION}</b>: set the channels of one biological repeat once; every "
                "condition's imaging units in it take these tags (each unit keeps its own list). "
                "A unit whose header contradicts the row is listed, not filled."
            )
        ]
        rows += [self._row(key, group, width) for key, group in groups.items()]
        if self.message:
            rows.append(widgets.HTML(self.message))
        box = widgets.VBox(rows)
        box.add_class("mb-tag-block")
        self.box.children = [box]

    def _row(self, key: GroupKey, group: tuple, width: int) -> widgets.Widget:
        """Build one row: the repeat and its units, one dropdown per C index, the button."""
        repeat, count = key
        conditions = sorted({u.key[0] for u in group})
        shown = repeat_shown(self.grid.store.answers(), group)
        tags = self.pending.get(key) or list(shown or (None,) * count)
        self.pending[key] = tags
        note = "every unit already holds these tags" if shown else "not set for every unit"
        info = widgets.HTML(
            f"<b>{html.escape(repeat)}</b>: {count} channel(s), {len(group)} imaging unit(s) in "
            f"{html.escape(', '.join(conditions))}<div class='mb-help'>{note}</div>"
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
        button.on_click(lambda _, k=key, g=group: self.apply(k, g))
        self.drops[key], self.buttons[key] = drops, button
        cells.append(button)
        return widgets.GridBox(cells, layout=columns(width))

    def _picked(self, key: GroupKey, index: int, value: str | None) -> None:
        """Note one tag of a row; nothing is saved before the row's button (one action)."""
        self.pending[key][index] = value

    def apply(self, key: GroupKey, group: tuple) -> bool:
        """Save the row's tags for every unit of the repeat that its header does not contradict."""
        tags = self.pending.get(key, [])
        named = [t for t in tags if t]
        if len(named) != len(tags) or len(set(named)) != len(named):
            self.message = ui.status(
                "error", "Not saved.", ["Tag every channel of the row, each label once."]
            )
            self.grid.refresh()
            return False
        answers = self.grid.store.answers()
        save, cells, refused = repeat_fill(answers, group, named, self.grid.labels)
        lines = [
            f"{html.escape(unit_name(k))}: "
            + "; ".join(html.escape(difference_text(d)) for d in found)
            for k, found in refused.items()
        ]
        self.message = (
            ui.status(
                "error",
                f"{len(refused)} imaging unit(s) of {html.escape(key[0])} not filled: their "
                "header contradicts the row. Nothing is reordered or guessed; tag them on "
                "their own.",
                lines,
                raw=True,
            )
            if refused
            else ""
        )
        if not save:
            self.grid.refresh()
            return False
        if self.grid.same_channels.value:  # the user's action: tagged on their own (ADR 0006)
            self.grid.matrix.own.update(save)
        return self.grid.commit(save, cells)


__all__ = ["ACTION", "RepeatRows"]
