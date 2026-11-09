"""A thumbnail and, when it loses detail, a crop at native pixels (display only, PR 17c).

A thumbnail keeps every n-th pixel of a plane (:func:`.preview.read_plane`), so a large image
loses its fine structure. When the downscale factor is over :data:`CROP_FACTOR`, a second
picture shows one square of :data:`CROP_SIDE` native pixels, enlarged by repeating pixels
(nearest neighbour, never interpolated):

- **where:** the square of the native grid (squares of :data:`CROP_SIDE` pixels from the top
  left corner, whole squares only) whose thumbnail pixels have the highest standard deviation, on
  the displayed thumbnail (for colour, the mean of red, green and blue). A tie goes to the
  topmost square, then the leftmost. The caption gives its x and y range.
- **contrast:** the thumbnail's own black and white values, so both pictures agree (a flat
  thumbnail gives the crop its own contrast); non-finite pixels are magenta, as everywhere.

Nothing here reads a file, changes a source pixel or feeds an answer: the planes are only read.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from .preview_render import NORMAL, Panel, colour_composite, display_panel, png_bytes

CROP_SIDE = 128
"""Native pixels per side of the crop."""
CROP_FACTOR = 2
"""A crop is shown when the thumbnail keeps fewer than one pixel in this many per side."""
CROP_ZOOM = 2
"""Each native pixel of the crop is drawn as a square of this many pixels."""


@dataclass(frozen=True)
class TilePictures:
    """The pictures of one tile: the thumbnail and, when it loses detail, the native crop.

    ``factor`` is the downscale factor of the thumbnail (1 when it keeps every pixel);
    ``crop_box`` is ``(y0, y1, x0, x1)`` of the crop in native pixels (end exclusive), None
    without a crop; ``crop_text`` says where the crop is, or why there is none; ``note`` is what
    the panels say (flat or non-finite pixels).
    """

    png: bytes
    factor: int
    crop_png: bytes | None = None
    crop_box: tuple[int, int, int, int] | None = None
    crop_text: str = ""
    note: str = ""


def downscale_factor(shape: tuple[int, ...], side: int) -> int:
    """Return how many native pixels per side one thumbnail pixel stands for (at least 1)."""
    return max(1, math.ceil(max(shape[:2]) / side))


def busiest_square(shown: np.ndarray, factor: int, shape: tuple[int, int], side: int) -> int:
    """Return the index (row-major) of the native square whose thumbnail pixels vary most.

    Parameters
    ----------
    shown
        The displayed thumbnail, (y, x) or (y, x, 3) 8-bit pixels, every ``factor``-th
        native pixel from 0.
    factor
        The thumbnail's downscale factor.
    shape
        The native (Y, X) shape.
    side
        The native side of a square. Only whole squares count.

    Returns
    -------
    int
        ``row * columns + column`` of the square; -1 when no square holds two thumbnail pixels.
    """
    rows, cols = shape[0] // side, shape[1] // side
    if rows == 0 or cols == 0:
        return -1
    grey = shown.astype(np.float64)
    grey = grey.mean(axis=2) if grey.ndim == 3 else grey
    ys = np.arange(grey.shape[0]) * factor // side
    xs = np.arange(grey.shape[1]) * factor // side
    keep_y, keep_x = ys < rows, xs < cols
    grey = grey[keep_y][:, keep_x]
    index = (ys[keep_y][:, None] * cols + xs[keep_x][None, :]).ravel()
    values = grey.ravel()
    count = np.bincount(index, minlength=rows * cols)
    total = np.bincount(index, values, minlength=rows * cols)
    squares = np.bincount(index, values * values, minlength=rows * cols)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
        variance = np.where(count >= 2, squares / count - mean * mean, -1.0)
    best = int(np.argmax(variance))  # the first maximum: topmost, then leftmost
    return best if variance[best] >= 0 else -1


def _picture(panels: Sequence[Panel], colour: bool) -> Panel:
    return colour_composite(panels) if colour else panels[0]


def _crop(
    planes: Sequence[np.ndarray], panels: Sequence[Panel], box: tuple[int, int, int, int]
) -> Panel:
    """Return the native square of every plane with the thumbnail's contrast, as one panel."""
    y0, y1, x0, x1 = box
    out = []
    for plane, panel in zip(planes, panels, strict=True):
        limits = (panel.low, panel.high) if panel.kind == NORMAL else None
        out.append(display_panel(plane[y0:y1, x0:x1], limits))
    return _picture(out, len(planes) == 3)


def tile_pictures(
    planes: Sequence[np.ndarray], colour: bool, side: int, zoom: int = CROP_ZOOM
) -> TilePictures:
    """Return the thumbnail of native planes and, when it loses detail, a native crop.

    Parameters
    ----------
    planes
        The native (Y, X) planes: one, or three for a colour picture (red, green, blue). They
        are only read.
    colour
        True to draw the three planes as one colour picture.
    side
        The thumbnail's largest side in pixels.
    zoom
        How many times each native pixel of the crop is repeated.

    Raises
    ------
    ValueError
        When the planes cannot be drawn (see :func:`.preview_render.colour_composite`).
    """
    shape = planes[0].shape
    factor = downscale_factor(shape, side)
    panels = [display_panel(np.ascontiguousarray(p[::factor, ::factor])) for p in planes]
    shown = _picture(panels, colour)
    pictures = TilePictures(png_bytes(shown.pixels), factor, note=shown.note)
    if factor <= CROP_FACTOR:
        return pictures
    if min(shape[:2]) < CROP_SIDE:
        why = f"no 1:1 detail: the image is under {CROP_SIDE} pixels on a side"
        return replace(pictures, crop_text=why)
    best = busiest_square(shown.pixels, factor, shape, CROP_SIDE)
    if best < 0:
        why = f"no 1:1 detail: the thumbnail keeps too few pixels of a {CROP_SIDE}-pixel square"
        return replace(pictures, crop_text=why)
    columns = shape[1] // CROP_SIDE
    y0, x0 = (best // columns) * CROP_SIDE, (best % columns) * CROP_SIDE
    box = (y0, y0 + CROP_SIDE, x0, x0 + CROP_SIDE)
    crop = _crop(planes, panels, box)
    big = np.repeat(np.repeat(crop.pixels, zoom, axis=0), zoom, axis=1)
    where = _where(shown.pixels, factor, box)
    text = (
        f"1:1 detail: x {x0}-{box[3] - 1}, y {y0}-{box[1] - 1} ({where}), "
        f"each pixel drawn {zoom}x{zoom}"
    )
    return replace(pictures, crop_png=png_bytes(big), crop_box=box, crop_text=text)


def _where(shown: np.ndarray, factor: int, box: tuple[int, int, int, int]) -> str:
    """Say why the crop is where it is: it varies most, or nothing varies (the first square)."""
    y0, y1, x0, x1 = (-(-v // factor) for v in box)  # the thumbnail pixels inside the box
    square = shown[y0:y1, x0:x1].astype(np.float64)
    grey = square.mean(axis=2) if square.ndim == 3 else square  # as busiest_square measures
    if grey.min() == grey.max():
        return f"the thumbnail does not vary in any {CROP_SIDE}-pixel square: the first one"
    return f"the {CROP_SIDE}-pixel square that varies most"


def no_crop(pictures: TilePictures, reason: str) -> TilePictures:
    """Return the pictures without a crop, saying why (a reduced read has no native pixels)."""
    return replace(pictures, crop_png=None, crop_box=None, crop_text=reason)


__all__ = [
    "CROP_FACTOR",
    "CROP_SIDE",
    "CROP_ZOOM",
    "TilePictures",
    "busiest_square",
    "downscale_factor",
    "no_crop",
    "tile_pictures",
]
