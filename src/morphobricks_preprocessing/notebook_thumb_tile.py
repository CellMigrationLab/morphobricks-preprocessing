"""One thumbnail with its native crop, the shared picture of a folder (PR 17c).

:class:`ThumbTile` shows a folder's thumbnail and, when the thumbnail loses detail, the crop at
native pixels beside it (:mod:`.preview_crop`), with the crop's x/y range and a caption.
**Enlarge** shows the larger view in a detail box of the page (:func:`show_detail`), as the tag
grid's Enlarge does. It is used on *Images found* (the header picture of each folder), on
*Replicates* and on *Check and save*. Display only: a tile holds no answer.
"""

from __future__ import annotations

import html
from collections.abc import Callable

import ipywidgets as widgets

from . import notebook_style as ui
from .preview_crop import TilePictures


def zoom_button(action: Callable[[], None], tooltip: str = "Show it larger") -> widgets.Button:
    """Return the Enlarge button, which calls ``action``."""
    zoom = ui.button(" Enlarge", icon="search-plus", tooltip=tooltip)
    zoom.layout = widgets.Layout(width="99%")
    zoom.on_click(lambda _: action())
    return zoom


def _image(png: bytes, width: str, kind: str) -> widgets.Image:
    image = widgets.Image(
        value=png, format="png", layout=widgets.Layout(width=width, border="1px solid #CBD5E1")
    )
    image.add_class(kind)
    return image


def _note(text: str) -> str:
    return f"<div class='mb-help'>{html.escape(text)}</div>" if text else ""


class ThumbTile:
    """A thumbnail, its native crop when it loses detail, a caption and Enlarge.

    Parameters
    ----------
    pictures
        The pictures; None when there is none, and ``problem`` says why.
    caption
        A sentence under the pictures (escaped here).
    problem
        Why there is no picture, shown in a dashed box instead.
    colour
        True for a colour picture (the class of the image, for the page's styles).
    enlarge
        Called by **Enlarge**; None for no button.
    width
        The thumbnail's width on the page.
    """

    def __init__(
        self,
        pictures: TilePictures | None,
        caption: str = "",
        problem: str = "",
        colour: bool = False,
        enlarge: Callable[[], None] | None = None,
        width: str = "160px",
    ) -> None:
        self.pictures = pictures
        if pictures is None:
            self.widget: widgets.Widget = widgets.HTML(
                "<div class='mb-help' style='border:1px dashed #94A3B8;padding:6px;"
                f"max-width:{width}'>{html.escape(problem)}</div>"
            )
            return
        shown = [_image(pictures.png, width, "mb-colour" if colour else "mb-grey")]
        if pictures.crop_png is not None:
            shown.append(_image(pictures.crop_png, width, "mb-crop"))
        texts = _note(pictures.crop_text) + _note(pictures.note) + _note(caption)
        parts: list[widgets.Widget] = [widgets.HBox(shown), widgets.HTML(texts)]
        if enlarge is not None:
            parts.append(zoom_button(enlarge))
        # One or two pictures wide, so captions wrap under them and the tile never stretches.
        pictures_wide = 2 if pictures.crop_png is not None else 1
        wide = f"{pictures_wide * int(width.removesuffix('px')) + 8 * pictures_wide}px"
        self.widget = widgets.VBox(parts, layout=widgets.Layout(width=wide, flex="0 0 auto"))


def show_detail(detail: widgets.VBox, title: str, pictures: TilePictures | None, why: str) -> None:
    """Show the larger view of a tile in ``detail``, with **Close** (``title`` is HTML)."""
    close = ui.button(" Close", icon="times")
    close.on_click(lambda _: setattr(detail, "children", []))
    if pictures is None:
        shown: list[widgets.Widget] = [
            widgets.HTML(ui.status("warn", "No picture.", [html.escape(why)], raw=True))
        ]
    else:
        big = widgets.Image(
            value=pictures.png, format="png", layout=widgets.Layout(max_width="1024px")
        )
        shown = [big]
        if pictures.crop_png is not None:
            crop = widgets.Image(
                value=pictures.crop_png, format="png", layout=widgets.Layout(width="512px")
            )
            crop.add_class("mb-crop")
            shown.append(crop)
        shown.append(widgets.HTML(_note(pictures.crop_text) + _note(pictures.note)))
    detail.children = [widgets.HTML(title), *shown, close]


__all__ = ["ThumbTile", "show_detail", "zoom_button"]
