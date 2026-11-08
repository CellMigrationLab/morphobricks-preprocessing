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

from collections.abc import Callable, Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_type
from .channel_labels import UnitLayout
from .draft_answers import UnitKey
from .notebook_channels_view import AnswerStore, blank_chip

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
                "run refuses a unit declared otherwise.</span>"
            )
        if saved == "rgb" and not layout.rgb:
            return (
                "<span class='mb-help' style='color:#92400E'>The header does not say colour: the "
                "run refuses this unit unless its header does.</span>"
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


__all__ = ["OPTIONS", "ImageTypeColumn"]
