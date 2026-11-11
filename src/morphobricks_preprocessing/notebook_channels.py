"""The notebook's *Your channels* screen: step A (labels) and step B (tags) assembled.

A thin layout over three self-contained pieces, each with its own saving and resuming, so a later
layout can put them on separate steps without rewriting any logic:

- :class:`.notebook_channels_labels.LabelsCard` (step A, the label list),
- :class:`.notebook_channels_grid.TagGrid` (step B, the tag grid),
- :class:`.notebook_channels_type.ImageTypeColumn` (the per-unit image type, placed in the grid).

They read and save the draft answers through :class:`_NotebookStore`, the notebook's own
``_answer``. The screen needs the axis roles the user confirmed (the pictures are read only
through them, nothing is guessed from the shape of an image) and the checked folder's draft.
"""

from __future__ import annotations

import ipywidgets as widgets

from . import notebook_style as ui
from .axis_review import review_axes
from .channel_answers import all_colour
from .channel_labels import UnitLayout
from .channel_units import header_names, slot_count, unit_channel_counts, unit_layouts
from .draft_answers import DraftAnswers
from .notebook_channel_thumbs import Thumbnails
from .notebook_channels_grid import TagGrid
from .notebook_channels_labels import LabelsCard
from .notebook_channels_type import ImageTypeColumn

INTRO = (
    "<b>What happens here:</b> first name what you stained (the labels), then say, for every "
    "folder, which label each channel is, looking at its picture. Nothing is chosen for you: "
    "names found in the image headers are offered only as suggestions, and each one needs your "
    "click."
)


class _NotebookStore:
    """The cards' view of the notebook's draft answers (the notebook's own save)."""

    def __init__(self, notebook: object) -> None:
        self._nb = notebook

    def answers(self) -> DraftAnswers:
        """Return the current answers."""
        return self._nb.draft_answers

    def update(self, new: DraftAnswers) -> bool:
        """Save the answers as the description cards do; False when they are refused."""
        return self._nb._answer(new, rebuild=False)

    def refusal(self) -> str:
        """Return the reason the last answer was refused."""
        return str(self._nb._refusal or "the answers were refused")

    def used_channels(self) -> tuple[str, ...]:
        """Return the channel IDs the lineage retired."""
        base = self._nb._draft_base
        return tuple(base.used_ids.get("channels", ())) if base is not None else ()


class _ChannelsStep:
    """The *Your channels* screen of the notebook."""

    def _build_channels_step(self) -> None:
        """Build the label card, the tag grid and the image-type column (hidden until needed)."""
        store = _NotebookStore(self)
        self._channels_key: tuple | None = None
        self._channel_layouts: tuple[UnitLayout, ...] = ()
        self._channel_thumbs: Thumbnails | None = None
        self.channel_types = ImageTypeColumn(store, self._types_changed)
        self.channel_grid = TagGrid(store, self.channel_types, self._channels_changed)
        self.channel_labels = LabelsCard(store, self._channel_labels_changed)
        self.channel_intro = ui.intro(INTRO)  # hidden while the stains are not applicable
        self.channels_waiting = widgets.HTML()
        self.channels_box = widgets.VBox(
            [
                self.channels_waiting,
                self.channel_labels.widget,
                self.channel_grid.widget,
                self.moves_box,
                self.names_box,
            ]
        )

    def channels_step(self, title: str | None = None, purpose: str | None = None) -> widgets.Widget:
        """Return the channels screen: labels, then tags and image type per unit."""
        return ui.page(
            "Your channels" if title is None else title,
            "Name what you stained, then tag every channel of every unit."
            if purpose is None
            else purpose,
            [self.channel_intro, self.channels_box],
        )

    # Opening and closing -----------------------------------------------------------------

    def _hide_channels(self, why: str = "") -> None:
        self._channels_key = None
        self._channel_thumbs = None
        self.channel_labels.close()
        self.channel_grid.close()
        ui.set_visible(self.channel_intro, True)
        self.channels_waiting.value = why
        self._refresh_masks_screen()

    def _refresh_channels(self) -> None:
        """Open the screen for the checked folder once its draft and axis roles are known."""
        pre = self.preflight
        if pre is None or self.draft is None or self.draft_answers is None:
            self._hide_channels(
                ui.status("info", "Waiting for step 1.", ["Check the folder and answer its cards."])
            )
            return
        if self.confirmed_mapping is None:
            self._hide_channels(
                ui.status(
                    "info",
                    "Waiting for the axis roles.",
                    [
                        "Confirm the axis roles in step 2 first: the pictures are read only "
                        "through the roles you confirmed, never guessed from the shape."
                    ],
                )
            )
            return
        review = review_axes(
            pre.metadata,
            self.confirmed_mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        keys = [
            (r.condition, r.biological_repeat, r.technical_repeat) for r in self.draft.unit_rows()
        ]
        layouts = unit_layouts(keys, pre.discovery, pre.excluded, pre.metadata, review)
        slots, names = slot_count(pre.metadata, review), header_names(pre.metadata)
        # The pictures are of the images as this check read them: a new check, or another
        # revision of a representative image, is another key and its pictures are read again.
        bound = tuple(
            sorted((u.image, pre.header_fingerprints.get(u.image)) for u in layouts if u.image)
        )
        key = (str(pre.discovery.root), self.confirmed_mapping, layouts, slots, tuple(names), bound)
        if key == self._channels_key:
            self._refresh_masks_screen()
            return
        self._channels_key = key
        self._channel_layouts = layouts
        self._channel_thumbs = Thumbnails(
            pre.discovery.root, review, self._memory_limit(), pre.header_fingerprints
        )
        self.channels_waiting.value = ""
        self.channel_labels.open(slots, names, layouts)
        self._channel_labels_changed()

    def _channel_counts(self) -> dict[tuple[str, str, str], int]:
        """Return each unit's channel count, read through the confirmed axis roles, by key.

        Empty while the screen is not open for the current check and roles; a unit whose image
        cannot be tagged, or whose images disagree in channel count, has none
        (:func:`.channel_units.unit_channel_counts`). The save is gated on these counts.
        """
        if self._channels_key is None:
            return {}
        return unit_channel_counts(self._channel_layouts)

    @property
    def _stains_not_applicable(self) -> bool:
        """True when the user declared every folder colour (RGB): R, G, B need no label."""
        answers = self.draft_answers
        return (
            self._channels_key is not None
            and answers is not None
            and all_colour(self._channel_layouts, answers.units)
        )

    def _channel_labels_changed(self) -> None:
        """Open the tag grid for the confirmed labels, or close it while they are being changed.

        When every folder is colour the labels are not applicable and the grid opens without them.
        """
        na = self._stains_not_applicable
        self.channel_labels.set_not_applicable(na)
        ui.set_visible(self.channel_intro, not na)  # it says to name the stains first
        if (self.channel_labels.confirmed or na) and self._channels_key is not None:
            self.channel_grid.open(
                self._channel_layouts, self.channel_labels.labels, self._channel_thumbs
            )
        else:
            self.channel_grid.close()
        self._refresh_masks_screen()

    def _types_changed(self) -> None:
        """Redraw the grid after an image type (its rows hold the type cells)."""
        if self._stains_not_applicable != self.channel_labels.not_applicable:
            self._channel_labels_changed()  # every folder became colour, or no longer is
        else:
            self.channel_grid.refresh()
        self._channels_changed()

    def _channels_changed(self) -> None:
        """Take the notebook's later steps into account after a tag or an image type."""
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_masks_screen()


__all__ = ["_ChannelsStep"]
