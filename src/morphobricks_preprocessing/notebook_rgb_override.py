"""The RGB header override on the *Image type* card of the Channels step (Spec RH1).

Some scanners save colour pictures as three interleaved samples per pixel under a TIFF header
that does not say colour (photometric ``minisblack``, ``SamplesPerPixel`` 3, contiguous). A
unit declared colour (RGB) with such headers is refused by the run
(``R_IMAGE_TYPE_CONTRADICTS_HEADER``) unless it declares ``rgb_header_override = true``
(experiment-v1 section 4.4b). This part of the card offers that key:

- it is shown only when some folder's headers have three interleaved samples but do not say
  colour (:attr:`.channel_labels.UnitLayout.three_samples`);
- one checkbox, **never pre-ticked**: the key is recorded only by the user's click, for those
  folders answered colour (RGB), through :func:`.channel_answers.with_override`; unticking
  removes it;
- while no such folder is answered colour (RGB) the checkbox is disabled and a line says why;
- the box is ticked only when every such colour folder has the key; when only some have it (a
  folder answered colour after the tick, or a hand-written file), a line names them: the key is
  never extended to the others without a click, and never shown as absent while recorded.

The run records what the override did per image (``rgb_header_override_images``) as for a
hand-written description; nothing here reads a pixel.
"""

from __future__ import annotations

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_override
from .channel_labels import UnitLayout
from .draft_answers import DraftAnswers
from .notebook_channels_type import ImageTypeColumn
from .notebook_channels_view import save_change, unit_name
from .notebook_render import blocked_line
from .validation import plural

LABEL = "Read these folders as colour (RGB) although their header does not say colour"
EXPLAIN = (
    "Their images store three values per pixel side by side, as a colour picture does, but "
    "the image header does not say colour (some scanners save colour this way). If they are "
    "colour pictures, tick the box: the first, second and third values are then read as red, "
    "green and blue, and the saved experiment says so (<b>rgb_header_override = "
    "true</b>). Leave it unticked if they are not colour pictures. The run checks that "
    "every image has exactly three values per pixel and records which images used it."
)
CHOOSE_COLOUR = "choose colour (RGB) for these folders first, above or under Set per folder…"


class RgbOverrideOffer:
    """The override's explanation, its checkbox and why it is disabled, in one column."""

    def __init__(self, types: ImageTypeColumn) -> None:
        self.types = types
        self._building = False
        self.text = widgets.HTML()
        self.box = widgets.Checkbox(
            value=False, description=LABEL, indent=False, layout=widgets.Layout(width="99%")
        )
        self.blocker = widgets.HTML()
        self.partial = widgets.HTML()
        self.message = widgets.HTML()
        self.widget = widgets.VBox([self.text, self.box, self.blocker, self.partial, self.message])
        self.box.observe(lambda change: self._clicked(change["new"]), names="value")
        ui.set_visible(self.widget, False)

    def offered(self) -> list[UnitLayout]:
        """Return the usable folders whose headers allow the override."""
        return [u for u in self.types.layouts if u.usable and u.three_samples]

    def colour(self) -> list[UnitLayout]:
        """Return the offered folders the user answered colour (RGB): the box applies to them."""
        return [u for u in self.offered() if self.types.saved(u.key) == "rgb"]

    def recorded(self) -> list[UnitLayout]:
        """Return the folders the box applies to that have the override recorded."""
        units = self.types.store.answers().units
        return [u for u in self.colour() if units[u.key].rgb_header_override is True]

    def ticked(self) -> bool:
        """Return True when every folder the box applies to has the override recorded."""
        colour = self.colour()
        return bool(colour) and len(self.recorded()) == len(colour)

    def refresh(self, shown: bool) -> None:
        """Show the offer (when ``shown`` and some folder allows it) as the answers are now."""
        offered = self.offered()
        ui.set_visible(self.widget, shown and bool(offered))
        if not (shown and offered):
            return
        colour = self.colour()
        self.text.value = ui.status(
            "info",
            f"{plural(len(offered), 'folder')}: three values per pixel, header not colour.",
            [EXPLAIN],
            raw=True,
        )
        self._building = True
        self.box.value = self.ticked()
        self._building = False
        self.box.disabled = not colour
        self.blocker.value = blocked_line([] if colour else [CHOOSE_COLOUR])
        self.partial.value = self._partial(colour)

    def _partial(self, colour: list[UnitLayout]) -> str:
        """Return the line naming the folders that have the key while others do not, or ""."""
        recorded = self.recorded()
        if not recorded or len(recorded) == len(colour):
            return ""
        names = ", ".join(unit_name(u.key) for u in recorded)
        return ui.status(
            "warn",
            f"rgb_header_override = true is recorded for {len(recorded)} of {len(colour)} of "
            f"these folders ({names}).",
            [
                "The box is unticked because the other folders do not have it. Tick it to "
                "record it for all of them, or untick it then to take it back from all."
            ],
        )

    def _clicked(self, value: bool) -> None:
        """Record or take back the override of the colour folders; say a refusal."""
        if self._building:
            return
        store, colour, layouts = self.types.store, self.colour(), self.types.layouts

        def change(answers: DraftAnswers) -> DraftAnswers:
            for layout in colour:
                answers = with_override(answers, layout, value)
            return synced(answers, layouts)

        try:
            ok = save_change(store, change)
        except ValueError as exc:
            self.message.value = ui.status("error", "Nothing was changed.", [str(exc)])
        else:
            if ok:
                self.message.value = ""
            else:
                self.message.value = ui.status("error", "Not saved.", [store.refusal()])
        self.types._on_change()


__all__ = ["CHOOSE_COLOUR", "EXPLAIN", "LABEL", "RgbOverrideOffer"]
