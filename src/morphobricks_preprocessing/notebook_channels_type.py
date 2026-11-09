"""The image-type column of *Your channels* (a self-contained piece).

One answer per unit, **nothing preselected**: *separate channels* (an ordinary channel image) or
*colour (RGB)*. "Colour (RGB)" is pre-ticked only for a unit whose image header says it is a
colour image (``rgb_samples``); that pre-tick is a proposal, saved only when the user confirms the
layout (:meth:`ImageTypeColumn.confirm_header`) or ticks the box. The answer is declared by the
user and never inferred from pixels or channel counts (experiment-v1 section 4.4b); a choice that
contradicts the header is allowed here and said in words, since the run refuses it
(``R_IMAGE_TYPE_CONTRADICTS_HEADER``). Saved to ``units.*.image_type`` of the draft.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_type
from .channel_labels import UnitLayout
from .draft_answers import UnitKey
from .notebook_channels_view import AnswerStore, blank_chip, quoted, unit_name

OPTIONS = [("separate channels", "channels"), ("colour (RGB)", "rgb")]


class ImageTypeColumn:
    """The per-unit image-type choice; the owner places the cells where it likes."""

    def __init__(self, store: AnswerStore, on_change: Callable[[], None]) -> None:
        self.store, self._on_change = store, on_change
        self.layouts: Sequence[UnitLayout] = ()
        self.radios: dict[UnitKey, widgets.RadioButtons] = {}
        self.notes: dict[UnitKey, widgets.HTML] = {}
        self.message = ""
        self._building = False

    def open(self, layouts: Sequence[UnitLayout]) -> None:
        """Take the units; cells are built by :meth:`cell`."""
        self.layouts = layouts
        self.radios, self.notes = {}, {}

    def saved(self, key: UnitKey) -> str | None:
        """Return the type the user answered for a unit (``channels``, ``rgb``), else None."""
        unit = self.store.answers().units.get(key)
        return None if unit is None else unit.image_type

    def shown(self, layout: UnitLayout) -> str | None:
        """Return what the cell shows: the answer, else ``rgb`` when the header says colour."""
        return self.saved(layout.key) or ("rgb" if layout.usable and layout.rgb else None)

    def proposed(self, layout: UnitLayout) -> bool:
        """Return True while the cell shows the header's colour pre-tick, not saved yet."""
        return self.saved(layout.key) is None and self.shown(layout) == "rgb"

    def cell(self, layout: UnitLayout) -> widgets.Widget:
        """Build the radio buttons and the note of one unit."""
        radio = widgets.RadioButtons(
            options=OPTIONS, value=self.shown(layout), layout=widgets.Layout(width="99%")
        )
        radio.disabled = not layout.usable
        note = widgets.HTML(self._note(layout))
        radio.observe(lambda change, u=layout: self._chosen(u, change["new"]), names="value")
        self.radios[layout.key], self.notes[layout.key] = radio, note
        return widgets.VBox([radio, note])

    def _note(self, layout: UnitLayout) -> str:
        """Return what is said under a unit's radio buttons."""
        saved, shown = self.saved(layout.key), self.shown(layout)
        if not layout.usable:
            return ""
        if saved is None and shown == "rgb":
            return (
                "<span class='mb-help'>Pre-ticked: the image header says colour. Not saved "
                "until you confirm it.</span>"
            )
        if saved is None:
            return blank_chip("choose one")
        if saved == "channels" and layout.rgb:
            return (
                "<span class='mb-help' style='color:#92400E'>The header says colour (RGB): the "
                "run refuses a folder declared otherwise.</span>"
            )
        if saved == "rgb" and not layout.rgb:
            return (
                "<span class='mb-help' style='color:#92400E'>The header does not say colour: the "
                "run refuses this folder unless its header does.</span>"
            )
        return ""

    def _chosen(self, layout: UnitLayout, value: str | None) -> None:
        """Save the type the user ticked; a refusal is said and the tick is taken back."""
        if self._building or value is None or value == self.saved(layout.key):
            return
        try:
            new = with_type(self.store.answers(), layout, value)
        except ValueError as exc:
            self.message = ui.status("error", "Not taken.", [str(exc)])
            self._revert(layout)
        else:
            if self.store.update(synced(new, self.layouts)):
                self.message = ""
            else:
                self.message = ui.status("error", "Not saved.", [self.store.refusal()])
                self._revert(layout)
        self._on_change()

    def _revert(self, layout: UnitLayout) -> None:
        radio = self.radios.get(layout.key)
        if radio is not None:
            self._building = True
            radio.value = self.shown(layout)
            self._building = False

    def confirm_header(self, keys: Sequence[UnitKey]) -> bool:
        """Save the header's colour pre-tick of these units as the answer; False if refused."""
        answers = self.store.answers()
        by_key = {u.key: u for u in self.layouts}
        for key in keys:
            layout = by_key[key]
            if self.proposed(layout):
                answers = with_type(answers, layout, "rgb")
        ok = self.store.update(synced(answers, self.layouts))
        if not ok:
            self.message = ui.status("error", "Not saved.", [self.store.refusal()])
        self._on_change()
        return ok


class ImageTypeRows:
    """The image type on a page of its own: one row per folder, its header and its choice.

    The cells are the :class:`ImageTypeColumn`'s own, so the answers, notes and refusals are
    exactly those of the column in the tag grid; only the place differs.
    """

    def __init__(self, types: ImageTypeColumn) -> None:
        self.types = types
        self.rows = widgets.VBox()
        self.status = widgets.HTML()
        self.problem = widgets.HTML()
        self.widget = ui.card(
            "Image type of each folder",
            [
                ui.help_text(
                    "<i>Separate channels</i>: one stain or marker per channel. <i>Colour "
                    "(RGB)</i>: a colour picture whose red, green and blue are kept together. "
                    "Nothing is chosen for you; colour is pre-ticked only where the image "
                    "header says colour, and needs your click."
                ),
                self.rows,
                self.problem,
                self.status,
            ],
            visible=False,
        )

    def open(self, layouts: Sequence[UnitLayout]) -> None:
        """Show one row per folder of these layouts."""
        self.types.open(layouts)
        self.refresh()
        ui.set_visible(self.widget, True)

    def close(self) -> None:
        """Hide the rows."""
        ui.set_visible(self.widget, False)

    @property
    def answered(self) -> int:
        """Return how many usable folders have a saved image type."""
        return sum(1 for u in self.types.layouts if u.usable and self.types.saved(u.key))

    def refresh(self) -> None:
        """Rebuild the rows from the saved answers."""
        self.types.open(self.types.layouts)  # a fresh set of cells, built from the answers
        rows = [self._row(u) for u in sorted(self.types.layouts, key=lambda u: u.key)]
        proposed = [u.key for u in self.types.layouts if self.types.proposed(u)]
        if proposed:
            button = ui.button(
                f" Yes, colour (RGB) is right for these {len(proposed)} folder(s)",
                style="success",
                icon="check",
            )
            button.on_click(lambda _, keys=tuple(proposed): self._confirm(keys))
            rows.append(button)
        self.rows.children = rows
        self.problem.value = self.types.message
        usable = sum(1 for u in self.types.layouts if u.usable)
        done = self.answered
        self.status.value = (
            ui.status("ok", f"The image type of every one of the {usable} folders is answered.")
            if done == usable
            else ui.status("warn", f"Not finished: {usable - done} image type(s) to choose.")
        )

    def _confirm(self, keys: Sequence[UnitKey]) -> None:
        self.types.confirm_header(keys)

    def _row(self, layout: UnitLayout) -> widgets.Widget:
        """Build one folder's row: its name, what its header says, and its choice."""
        info = f"<b>{html.escape(unit_name(layout.key))}</b>"
        if not layout.usable:
            info += ui.status(
                "warn", "Cannot be answered.", [html.escape(layout.problem)], raw=True
            )
            return widgets.HTML(info)
        said = (
            "header says: colour (RGB)"
            if layout.rgb
            else "header names: "
            + " · ".join(quoted(layout.name(i)) for i in range(layout.channels))
        )
        info += f"<div class='mb-help'>{said}</div>"
        return widgets.GridBox(
            [widgets.HTML(info), self.types.cell(layout)],
            layout=widgets.Layout(
                grid_template_columns="minmax(200px, 2fr) minmax(200px, 3fr)",
                grid_gap="4px 12px",
                width="99%",
                margin="0 0 8px 0",
            ),
        )


__all__ = ["OPTIONS", "ImageTypeColumn", "ImageTypeRows"]
