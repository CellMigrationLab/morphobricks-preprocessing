"""The notebook's one image viewer: choose an image, a time point and a slice; see its channels.

Owner (11 November 2026): one image reader and one display component, used by every step that
shows images (Images, Axes, Channels, Check and save), with dropdowns that narrow each other
(condition, biological repeat, technical repeat, image; :mod:`.image_tree`) instead of long
lists of pictures, a time slider and a depth slider when the image has those axes, and every
channel side by side (at most :data:`.image_view.CHANNELS_SHOWN`, then "6 of N channels
shown").

The viewer decides nothing. The step that owns it says, for each image, which roles to read it
with and where they come from (:class:`ViewSource`: the header's, the ones being edited, the
confirmed ones); :func:`.image_view.read_view` reads the plane. A picture that cannot be drawn
is a sentence in its place. The dropdowns and sliders are plain rows of the page, never inside
a folded panel (the folded "Another image, or other planes" could not be used in Google Colab).
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisMapping
from .fingerprint import Fingerprint
from .image_tree import LEVELS, ImageEntry, choices, entry_of, resolve
from .image_view import (
    SLIDER_ROLES,
    ImageView,
    ViewRequest,
    middle_slice,
    read_view,
    view_lengths,
)
from .planes import DEFAULT_MEMORY_LIMIT, PlaneReadError
from .preview import DETAIL_SIDE, THUMBNAIL_SIDE, PreviewRefused

READ_ERRORS = (PreviewRefused, PlaneReadError, ValueError, IndexError, OSError, MemoryError)
SLIDER_NAMES = {"T": "t (time point)", "Z": "z (depth slice)"}
LARGER_WIDTH = 512
"""The width of a picture on the page with **Larger pictures**, in pixels."""


@dataclass(frozen=True)
class ViewSource:
    """How one image is read for the viewer, as the step that owns the viewer says.

    Attributes
    ----------
    path
        The image.
    mapping
        The roles to read it with (the image's own); None when it cannot be drawn.
    shape
        The image's shape, as its header gives it.
    note
        Where the roles come from, for the caption (plain text).
    problem
        Why there is no picture, when ``mapping`` is None (plain text).
    expected
        The fingerprint the image must have (the folder check's); None for no binding.
    colour
        True when the channel dimension holds the red, green and blue of a colour picture.
    labels
        What to say under each channel besides its index (HTML), by channel.
    memory_limit
        The run's memory limit.
    """

    path: Path
    mapping: AxisMapping | None = None
    shape: tuple[int, ...] = ()
    note: str = ""
    problem: str = ""
    expected: Fingerprint | None = None
    colour: bool = False
    labels: tuple[str, ...] = ()
    memory_limit: int = DEFAULT_MEMORY_LIMIT


def caption_html(image: str, view: ImageView, lengths: dict[str, int], note: str) -> str:
    """Return the line under the pictures: the image, the plane, its size and how it is drawn."""
    parts = [f"<b>{html.escape(image)}</b>"]
    if view.t is not None and lengths["T"] > 1:
        parts.append(f"time point t={view.t} of {lengths['T']}")
    if view.z is not None and lengths["Z"] > 1:
        parts.append(f"depth slice z={view.z} of {lengths['Z']}")
    size = f"{view.plane[0]} × {view.plane[1]} px"
    if view.step > 1:
        size += f" (drawn reduced {view.step}×)"
    parts += [size, html.escape(view.dtype)]
    own = "" if view.colour or len(view.tiles) == 1 else ", each with its own contrast"
    shown = view.shown_text
    if len(view.tiles) < view.channels and not view.colour:
        shown = f"<b>{shown}</b>"
    parts.append(shown + own)
    if note:
        parts.append(html.escape(note))
    return f"<div class='mb-help'>{' · '.join(parts)}</div>"


class ImageViewer:
    """The dropdowns, the sliders and the channel pictures of one image.

    Parameters
    ----------
    source
        Called with an image's relative path; returns how to read it (:class:`ViewSource`).
    """

    def __init__(self, source: Callable[[str], ViewSource]) -> None:
        self._source = source
        self.entries: tuple[ImageEntry, ...] = ()
        self.view: ImageView | None = None
        self.drawn: AxisMapping | None = None
        self._filling = False
        self._callbacks: list[Callable[[], None]] = []
        self.pickers = [
            widgets.Dropdown(
                description=name,
                style={"description_width": "initial"},
                layout=widgets.Layout(width="auto", min_width="180px", max_width="99%"),
            )
            for name in LEVELS
        ]
        for picker in self.pickers:
            picker.observe(lambda _: self._picked(), names="value")
        self.sliders = {
            role: widgets.IntSlider(
                description=SLIDER_NAMES[role],
                min=0,
                max=0,
                continuous_update=False,
                style={"description_width": "initial"},
                layout=widgets.Layout(width="340px"),
            )
            for role in SLIDER_ROLES
        }
        for slider in self.sliders.values():
            slider.observe(lambda _: self._moved(), names="value")
            ui.set_visible(slider, False)
        self.larger = widgets.ToggleButton(
            description="Larger pictures",
            icon="search-plus",
            tooltip="Draw the pictures larger (read again at up to 1024 pixels per side)",
            layout=widgets.Layout(width="auto"),
        )
        self.larger.observe(lambda _: self._moved(), names="value")
        self.tiles = widgets.HBox(layout=widgets.Layout(width="99%"))
        self.tiles.add_class("mb-channel-row")
        self.caption = widgets.HTML()
        wrap = widgets.Layout(flex_flow="row wrap", align_items="center", width="99%")
        self.widget = widgets.VBox(
            [
                widgets.HBox(self.pickers, layout=wrap),
                widgets.HBox([*self.sliders.values(), self.larger], layout=wrap),
                self.tiles,
                self.caption,
            ],
            layout=widgets.Layout(width="99%"),
        )
        self.widget.add_class("mb-viewer")
        ui.set_visible(self.widget, False)

    # What is shown -------------------------------------------------------------------------

    @property
    def image(self) -> str | None:
        """The image chosen (its relative path), None without one."""
        entry = resolve(self.entries, [picker.value for picker in self.pickers])
        return None if entry is None else entry.image

    def on_draw(self, callback: Callable[[], None]) -> None:
        """Call ``callback()`` after every drawing (also one that shows no picture)."""
        self._callbacks.append(callback)

    def set_entries(
        self, entries: Sequence[ImageEntry], image: str | None = None, *, draw: bool = True
    ) -> None:
        """List these images and show ``image`` (the one shown before, else the first).

        With ``draw`` False the caller draws (:meth:`refresh`) once its roles are set.
        """
        wanted = image or self.image
        self.entries = tuple(entries)
        entry = entry_of(self.entries, wanted) if wanted else None
        self._fill(entry.path if entry is not None else (None,) * len(LEVELS))
        ui.set_visible(self.widget, bool(self.entries))
        self._reset_sliders()
        if draw:
            self.draw()

    def select(self, image: str) -> None:
        """Show ``image`` (a relative path among the entries); another one is ignored."""
        entry = entry_of(self.entries, image)
        if entry is None:
            return
        self._fill(entry.path)
        self._reset_sliders()
        self.draw()

    def clear(self) -> None:
        """Forget the images (another folder)."""
        self.set_entries(())

    def _fill(self, wanted: Sequence[str | None]) -> None:
        """Offer, at each level, the values under the levels above; keep ``wanted`` if offered."""
        self._filling = True
        try:
            offered = choices(self.entries, wanted)
            for level, (picker, values) in enumerate(zip(self.pickers, offered, strict=True)):
                if level == len(LEVELS) - 1:
                    names = [value.rpartition("/")[2] for value in values]
                    picker.options = list(zip(names, values, strict=True))
                else:
                    picker.options = values
                value = wanted[level] if level < len(wanted) else None
                picker.value = value if value in values else (values[0] if values else None)
                # A level the whole experiment has one value of is not a choice: not shown.
                last = level == len(LEVELS) - 1
                several = last or len({e.path[level] for e in self.entries}) > 1
                ui.set_visible(picker, several and bool(values))
        finally:
            self._filling = False

    def _picked(self) -> None:
        """Narrow the levels below a changed dropdown and draw the image now chosen."""
        if self._filling:
            return
        self._fill([picker.value for picker in self.pickers])
        self._reset_sliders()
        self.draw()

    def _moved(self) -> None:
        if not self._filling:
            self.draw()

    def _reset_sliders(self) -> None:
        """Set the sliders for the image chosen: the middle slice, the first time point."""
        image = self.image
        source = None if image is None else self._source(image)
        self._filling = True
        try:
            for role, slider in self.sliders.items():
                length = 1
                if source is not None and source.mapping is not None:
                    length = self._lengths(source).get(role, 1)
                slider.max = max(length - 1, 0)
                slider.value = middle_slice(length) if role == "Z" else 0
                ui.set_visible(slider, length > 1)
        finally:
            self._filling = False

    @staticmethod
    def _lengths(source: ViewSource) -> dict[str, int]:
        try:
            return view_lengths(source.shape, source.mapping)
        except (IndexError, ValueError):
            return {}

    # Drawing -------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Draw the image chosen again (its roles or labels changed), keeping the plane."""
        image = self.image
        source = None if image is None else self._source(image)
        if source is not None and source.mapping is not None:
            lengths = self._lengths(source)
            self._filling = True
            try:
                for role, slider in self.sliders.items():
                    length = lengths.get(role, 1)
                    if slider.max != max(length - 1, 0):
                        slider.max = max(length - 1, 0)
                        slider.value = middle_slice(length) if role == "Z" else 0
                    ui.set_visible(slider, length > 1)
            finally:
                self._filling = False
        self.draw()

    def relabel(self) -> None:
        """Write the channel captions again from the source, without reading the image."""
        image = self.image
        if image is None or self.view is None:
            return
        source = self._source(image)
        if source.mapping is not None:
            self.tiles.children = self._tile_widgets(self.view, source)

    def draw(self) -> None:
        """Read and draw the image chosen at the sliders' plane; say why when it cannot be."""
        self.view, self.drawn = None, None
        image = self.image
        try:
            if image is None:
                self.tiles.children, self.caption.value = [], ""
                return
            source = self._source(image)
            if source.mapping is None:
                self._no_picture(image, source.problem or "no picture")
                return
            request = ViewRequest(
                z=self.sliders["Z"].value,
                t=self.sliders["T"].value,
                side=DETAIL_SIDE if self.larger.value else THUMBNAIL_SIDE,
                colour=source.colour,
                memory_limit=source.memory_limit,
                expected=source.expected,
            )
            try:
                view = read_view(source.path, source.mapping, source.shape, request, name=image)
            except READ_ERRORS as exc:
                self._no_picture(image, str(exc))
                return
            self.view, self.drawn = view, source.mapping
            self.tiles.children = self._tile_widgets(view, source)
            self.caption.value = caption_html(image, view, self._lengths(source), source.note)
        finally:
            for callback in self._callbacks:
                callback()

    def _no_picture(self, image: str, why: str) -> None:
        self.tiles.children = []
        self.caption.value = ui.status("warn", f"No picture of {image}.", [why])

    def _tile_widgets(self, view: ImageView, source: ViewSource) -> list[widgets.Widget]:
        """Return one box per picture: what it is, then the picture."""
        width = LARGER_WIDTH if self.larger.value else THUMBNAIL_SIDE
        self.tiles.layout.flex_flow = "row wrap" if self.larger.value else "row nowrap"
        boxes = []
        for tile in view.tiles:
            label = "colour" if view.colour else "" if tile.c is None else f"<b>c={tile.c}</b>"
            named = tile.c is not None and tile.c < len(source.labels)
            extra = source.labels[tile.c] if named else ""
            note = f" · {html.escape(tile.note)}" if tile.note else ""
            text = " ".join(part for part in (label, extra) if part) + note
            image = widgets.Image(
                value=tile.png, format="png", layout=widgets.Layout(border="1px solid #CBD5E1")
            )
            image.add_class("mb-colour" if view.colour else "mb-grey")
            box = widgets.VBox(
                [widgets.HTML(f"<div class='mb-help'>{text or '&nbsp;'}</div>"), image],
                layout=widgets.Layout(flex="1 1 0", min_width="0", max_width=f"{width}px"),
            )
            box.add_class("mb-channel")
            boxes.append(box)
        return boxes


__all__ = ["LARGER_WIDTH", "READ_ERRORS", "ImageViewer", "ViewSource", "caption_html"]
