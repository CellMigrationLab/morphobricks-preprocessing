"""The image type in one line of the axes step: what the channel dimension holds.

Owner decision (10 November 2026, item (p)): the image type says what the C dimension means
(one stain or marker per channel, or the red, green and blue of a colour picture), so it is
asked with the axes, in the pattern of the pixel size:

- **one line for all folders**, its choice pre-filled from what the image headers say (colour
  only when every folder's header says colour, separate channels only when none does). The
  pre-fill is a proposal: nothing is saved until the user clicks **Use for all folders**;
- **Set per folder…**, collapsed, holds the row of each folder
  (:class:`.notebook_channels_type.ImageTypeRows`: the same cells, answers and refusals);
- when the headers **differ**, nothing is pre-filled, one line says how many folders say what,
  and the per-folder part opens.

The answers are the per-unit ``image_type`` of the draft, saved through
:func:`.channel_answers.with_type` exactly as the per-folder radio buttons save them: clicking
**Use for all folders** records what choosing that type in every row records. A choice that
contradicts a header is still allowed and said in words (the run refuses it,
``R_IMAGE_TYPE_CONTRADICTS_HEADER``); folders answered differently stay refused by the save and
the run (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, Spec Stages 1-2), and the line says so at once.
Replacing answers already given asks first, as the pixel size does.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_type
from .channel_labels import UnitLayout
from .notebook_channels_type import ImageTypeColumn, ImageTypeRows
from .notebook_render import blocked_line
from .validation import plural

OPTIONS = [("separate stains or markers, one per channel", "channels"), ("colour (RGB)", "rgb")]
WORDS = {"channels": "separate channels", "rgb": "colour (RGB)"}
WAITING = (
    "Asked once the axis roles are confirmed: the channels of each folder are read through them."
)
MIXED = (
    "This version runs one image type per experiment: Check and save refuses folders of "
    "different types (R_HETEROGENEOUS_LAYOUT_NOT_ENABLED)."
)


def counted(values: Sequence[str]) -> str:
    """Return ``colour (RGB) in 2 folders, separate channels in 10`` for these types."""
    found = Counter(values)
    return ", ".join(
        f"{WORDS[value]} in {plural(found[value], 'folder')}" for value in WORDS if found[value]
    )


class ImageTypeLine:
    """One image type for all folders, confirmed by a click; each folder on demand."""

    def __init__(self, types: ImageTypeColumn, rows: ImageTypeRows) -> None:
        self.types, self.rows = types, rows
        self._pending: str | None = None
        self._shown_for: object = None
        self.line = widgets.HTML()
        self.choice = widgets.Dropdown(
            options=OPTIONS, value=None, layout=widgets.Layout(width="330px")
        )
        self.apply = ui.button(
            " Use for all folders",
            style="success",
            icon="check",
            tooltip="Record this image type for every folder of the experiment",
        )
        self.form = widgets.HBox(
            [widgets.HTML("<b>The channels are</b>&nbsp;"), self.choice, self.apply],
            layout=widgets.Layout(align_items="center", flex_flow="row wrap"),
        )
        self.blocker = widgets.HTML()
        self.replace_text = widgets.HTML()
        self.replace_yes = ui.button(" Replace them", style="warning", icon="check")
        self.replace_no = ui.button(" Cancel", icon="times")
        self.replace_box = widgets.VBox(
            [self.replace_text, widgets.HBox([self.replace_yes, self.replace_no])]
        )
        ui.set_visible(self.replace_box, False)
        self.message = widgets.HTML()
        self.per_folder = ui.accordion("Set per folder…", [rows.widget])
        self.widget = ui.card(
            "Image type",
            [self.line, self.form, self.blocker, self.replace_box, self.message, self.per_folder],
        )
        self.apply.on_click(lambda _: self._use_for_all())
        self.replace_yes.on_click(lambda _: self._replace())
        self.replace_no.on_click(lambda _: self._cancel())
        self.choice.observe(lambda _: self._choice_changed(), names="value")
        self.refresh()

    # What there is -----------------------------------------------------------------------

    def _usable(self) -> list[UnitLayout]:
        return [u for u in self.types.layouts if u.usable]

    @property
    def opened(self) -> bool:
        """True while the folders' channels are known (the axis roles are confirmed)."""
        return ui.is_visible(self.rows.widget)

    def suggestion(self) -> str | None:
        """Return the type every header suggests, None when they differ or there is none."""
        said = {"rgb" if u.rgb else "channels" for u in self._usable()}
        return said.pop() if len(said) == 1 else None

    def saved(self) -> list[str | None]:
        """Return the type the user answered for each usable folder (None where not yet)."""
        return [self.types.saved(u.key) for u in self._usable()]

    @property
    def complete(self) -> bool:
        """True when every usable folder has an answer (the folders may still differ)."""
        saved = self.saved()
        return bool(saved) and None not in saved

    # Showing -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the line for the folders as they are answered now."""
        self._cancel()
        usable = self._usable()
        shown = self.opened and bool(usable)
        for widget in (self.form, self.per_folder):
            ui.set_visible(widget, shown)
        if not shown:
            self.line.value = ui.status("info", WAITING) if not self.opened else ""
            self.blocker.value = self.message.value = ""
            self._shown_for = None
            return
        saved, suggestion = self.saved(), self.suggestion()
        answered = [value for value in saved if value is not None]
        one = answered[0] if len(set(answered)) == 1 and len(answered) == len(saved) else None
        key = (tuple(u.key for u in usable), tuple(saved), suggestion)
        if key != self._shown_for:  # the user's own choice in the box is kept otherwise
            self._shown_for = key
            self.choice.value = one or (suggestion if not answered else None)
        self.line.value = self._line(saved, answered, one, suggestion)
        if suggestion is None or len(set(answered)) > 1 or self.types.message:
            self.per_folder.selected_index = 0  # they differ: each folder is shown
        self._choice_changed()

    def _line(
        self, saved: list, answered: list[str], one: str | None, suggestion: str | None
    ) -> str:
        """Return the line: the answer, the proposal from the headers, or how they differ."""
        folders = plural(len(saved), "folder")
        said = [("rgb" if u.rgb else "channels") for u in self._usable()]
        if one is not None:
            lines = [] if one == suggestion else [f"The image headers say: {counted(said)}."]
            return ui.status("ok", f"Image type: {WORDS[one]} in all {folders}.", lines)
        if len(set(answered)) > 1:
            left = len(saved) - len(answered)
            more = f", {left} not answered" if left else ""
            return ui.status("warn", f"Image types differ: {counted(answered)}{more}.", [MIXED])
        if answered:
            return ui.status(
                "warn",
                f"Not finished: {counted(answered)}, {len(saved) - len(answered)} not answered.",
            )
        if suggestion is None:
            return ui.status(
                "warn",
                f"The image headers differ: {counted(said)}.",
                ["Nothing is proposed: choose the type of each folder below. " + MIXED],
            )
        return ui.status(
            "info",
            f"Proposed from the image headers: {WORDS[suggestion]} in all {folders}.",
            ["Not saved until you click Use for all folders."],
        )

    def _choice_changed(self) -> None:
        """Offer the click for a type that is not already every folder's answer."""
        self._cancel()
        value, saved = self.choice.value, self.saved()
        everywhere = value is not None and all(s == value for s in saved)
        ui.set_visible(self.apply, not everywhere)
        self.apply.disabled = value is None
        self.blocker.value = blocked_line(
            ["choose a type in the box, or set each folder below"] if value is None else []
        )

    # The click ---------------------------------------------------------------------------

    def _use_for_all(self) -> None:
        """Record the chosen type for every folder, asking first when it replaces answers."""
        value = self.choice.value
        if value is None:
            return
        others = [u for u in self._usable() if self.types.saved(u.key) not in (None, value)]
        if not others:
            self._write(value)
            return
        self._pending = value
        self.replace_text.value = ui.status(
            "warn",
            f"Use {WORDS[value]} for all folders? {plural(len(others), 'folder')} answered "
            "otherwise; nothing is written until you click Replace them.",
            ["Their channel tags are cleared: a channel is tagged again after its type changes."],
        )
        ui.set_visible(self.replace_box, True)

    def _replace(self) -> None:
        pending = self._pending
        self._cancel()
        if pending is not None:
            self._write(pending)

    def _cancel(self) -> None:
        self._pending = None
        ui.set_visible(self.replace_box, False)

    def _write(self, value: str) -> bool:
        """Save ``value`` as the type of every usable folder, all or none; say a refusal."""
        store = self.types.store
        answers = store.answers()
        try:
            for layout in self._usable():
                answers = with_type(answers, layout, value)
        except ValueError as exc:
            self.message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return False
        if not store.update(synced(answers, self.types.layouts)):
            self.message.value = ui.status("error", "Not saved.", [store.refusal()])
            return False
        self.message.value = ""
        self.types.message = ""
        self.types._on_change()
        return True


__all__ = ["MIXED", "OPTIONS", "WAITING", "ImageTypeLine", "counted"]
