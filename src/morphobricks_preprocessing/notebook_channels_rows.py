"""The folder rows of the tag grid: each folder's pictures, its C indices and header names.

A row shows one folder of a block (:mod:`.notebook_channels_grid`): its name, the first image by
path and the plane shown, then one picture per channel (about 160 px, **Enlarge** for the
1024 px view) with the C index and the header name quoted exactly as read. A folder shown as
colour (RGB) has one colour picture instead (:meth:`.notebook_channel_thumbs.Thumbnails.colour`)
and the fixed channels R, G and B. The rows hold no answer: the block above them does.
"""

from __future__ import annotations

import html
from collections.abc import Callable

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_labels import RGB_IDS, UnitLayout
from .notebook_channel_thumbs import COLOUR_TEXT, Thumb, Thumbnails
from .notebook_channels_view import columns, quoted, rgb_chip, unit_name

THUMB_PX = "160px"


def picture(thumb: Thumb | None, colour: bool = False) -> widgets.Widget:
    """Return a thumbnail, or the reason there is none (a dashed box)."""
    if thumb is None or thumb.png is None:
        note = "" if thumb is None else html.escape(thumb.note)
        return widgets.HTML(
            f"<div class='mb-help' style='border:1px dashed #94A3B8;padding:6px'>"
            f"no picture: {note}</div>"
        )
    image = widgets.Image(
        value=thumb.png,
        format="png",
        layout=widgets.Layout(width="99%", max_width=THUMB_PX, border="1px solid #CBD5E1"),
    )
    image.add_class("mb-colour" if colour else "mb-grey")
    return image


def folder_info(layout: UnitLayout, thumbs: Thumbnails | None) -> str:
    """Return the first cell of a folder row: its name, first image and plane (HTML)."""
    info = f"<b>{html.escape(unit_name(layout.key))}</b>"
    if layout.usable and thumbs is not None:
        info += (
            f"<div class='mb-help'>first image by path: {html.escape(layout.image or '')}"
            f"<br>1 of {layout.images} image(s); {html.escape(thumbs.plane(layout))}</div>"
        )
    if not layout.usable:
        info += ui.status("warn", "Cannot be tagged.", [html.escape(layout.problem)], raw=True)
    return info


class FolderRows:
    """Builds the folder rows of the grid and the enlarged view under it."""

    def __init__(self, detail: widgets.VBox) -> None:
        self.detail = detail
        self.thumbs: Thumbnails | None = None

    def row(
        self,
        layout: UnitLayout,
        width: int,
        colour: bool,
        extra: list[widgets.Widget],
        last: widgets.Widget,
    ) -> widgets.Widget:
        """Build one folder's row: its name (and ``extra`` under it), its pictures, ``last``."""
        info = widgets.HTML(folder_info(layout, self.thumbs))
        cells: list[widgets.Widget] = [widgets.VBox([info, *extra])]
        for index in range(width):
            if not layout.usable or (colour and index > 0):
                cells.append(widgets.HTML(""))
            elif colour:
                cells.append(self._colour_cell(layout))
            elif index >= layout.channels:
                cells.append(
                    widgets.HTML(
                        f"<span class='mb-help'>no channel {index + 1} in this image</span>"
                    )
                )
            else:
                cells.append(self._cell(layout, index))
        cells.append(last)
        return widgets.GridBox(cells, layout=columns(width))

    def _cell(self, layout: UnitLayout, index: int) -> widgets.Widget:
        """Build one channel's picture, its C index and header name, and Enlarge."""
        thumb = self.thumbs.get(layout, index) if self.thumbs is not None else None
        header = f"<b>C{index}</b> {quoted(layout.name(index))}"
        if thumb is not None and thumb.note:
            header += f"<div class='mb-help'>{html.escape(thumb.note)}</div>"
        return widgets.VBox(
            [picture(thumb), widgets.HTML(header), self._zoom(lambda: self._enlarge(layout, index))]
        )

    def _colour_cell(self, layout: UnitLayout) -> widgets.Widget:
        """Build a colour folder's one picture and its fixed channels R, G, B."""
        thumb = self.thumbs.colour(layout) if self.thumbs is not None else None
        chips = " ".join(rgb_chip(t) for t in RGB_IDS)
        extra = "" if thumb is None else thumb.note.removeprefix(COLOUR_TEXT).lstrip("; ")
        note = f"<div class='mb-help'>{html.escape(extra)}</div>" if extra else ""
        return widgets.VBox(
            [
                picture(thumb, colour=True),
                widgets.HTML(f"{chips}{note}"),
                self._zoom(lambda: self._enlarge(layout, None)),
            ]
        )

    @staticmethod
    def _zoom(action: Callable[[], None]) -> widgets.Button:
        zoom = ui.button(" Enlarge", icon="search-plus", tooltip="Show the 1024 px detail view")
        zoom.layout = widgets.Layout(width="99%")
        zoom.on_click(lambda _: action())
        return zoom

    def _enlarge(self, layout: UnitLayout, index: int | None) -> None:
        """Show the 1024 px picture of one channel (or the colour picture) under the grid."""
        if index is None:
            thumb, what = self.thumbs.colour(layout, detail=True), "colour picture"
        else:
            thumb = self.thumbs.get(layout, index, detail=True)
            what = f"C{index} {quoted(layout.name(index))}"
        close = ui.button(" Close", icon="times")
        close.on_click(lambda _: setattr(self.detail, "children", []))
        title = widgets.HTML(
            f"<b>{html.escape(unit_name(layout.key))}</b> · {what} · "
            f"{html.escape(layout.image or '')}"
        )
        shown: widgets.Widget = (
            widgets.Image(value=thumb.png, format="png", layout=widgets.Layout(max_width="1024px"))
            if thumb.png
            else widgets.HTML(ui.status("warn", "No picture.", [html.escape(thumb.note)], raw=True))
        )
        self.detail.children = [title, shown, close]


__all__ = ["THUMB_PX", "FolderRows", "folder_info", "picture"]
