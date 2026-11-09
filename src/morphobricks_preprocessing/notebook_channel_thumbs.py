"""Safe thumbnails of the channels of each unit's representative image (PR 13 rules).

Every picture goes through :func:`.preview.channel_strip`: reduced first (every n-th pixel),
then float32, then its own percentile contrast, flat images grey and non-finite pixels magenta,
and only through the axis roles the user confirmed. A picture never names a channel. A picture
that cannot be shown is replaced by the reason, never by a guess.

A unit shown as colour (RGB) gets one colour picture instead of three grey ones
(:meth:`Thumbnails.colour`, :func:`.preview_render.colour_composite`): display only, from the same
panels, each channel with its own contrast.

When a thumbnail keeps fewer than one pixel in :data:`.preview_crop.CROP_FACTOR` per side,
:meth:`Thumbnails.tile` adds a crop at native pixels (:func:`.preview_crop.tile_pictures`),
read through the same confirmed roles and plane.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .axes import AxisReview
from .channel_labels import UnitLayout
from .preview import (
    DETAIL_SIDE,
    THUMBNAIL_SIDE,
    PreviewCache,
    PreviewRefused,
    StripOptions,
    channel_strip,
    confirmed_mapping,
    read_plane,
)
from .preview_crop import CROP_FACTOR, TilePictures, downscale_factor, no_crop, tile_pictures
from .preview_render import Panel, colour_composite, png_bytes

READ_ERRORS = (PreviewRefused, OSError, ValueError, IndexError)

CONTRAST_TEXT = (
    "Each picture is stretched on its own, from the 0.5th to the 99.5th percentile of its own "
    "pixels (display only: the images are not changed), so brightness cannot be compared between "
    "pictures. A flat picture is grey and a non-finite pixel is magenta. The pictures only help "
    "you check the labels you choose: nothing is guessed from them."
)
COLOUR_TEXT = (
    "in colour: channel 1 red, 2 green, 3 blue, each stretched on its own from the 0.5th "
    "to the 99.5th percentile (display only)"
)


@dataclass(frozen=True)
class Thumb:
    """One channel picture: PNG bytes (None when it cannot be shown) and a short note."""

    png: bytes | None
    note: str = ""


class Thumbnails:
    """Reads and keeps the channel pictures of the units' representative images."""

    def __init__(self, root: Path, review: AxisReview, memory_limit: int) -> None:
        self.root, self.review, self.memory_limit = root, review, memory_limit
        self._cache = PreviewCache()
        self._strips: dict[tuple[str, int], tuple[Panel, ...] | Thumb] = {}
        self._planes: dict[str, tuple[tuple[int, int], int | None, int | None]] = {}
        self._tiles: dict[tuple[str, bool], TilePictures | Thumb] = {}
        self.where: dict[str, str] = {}

    def _read(self, layout: UnitLayout, side: int) -> tuple[Panel, ...] | Thumb:
        """Read every channel of the unit's image once per size; a refusal becomes one note."""
        key = (layout.image or "", side)
        if key not in self._strips:
            options = StripOptions(side=side, cache=self._cache, memory_limit=self.memory_limit)
            try:
                strip = channel_strip(self.root / key[0], key[0], self.review, options)
            except READ_ERRORS as exc:
                self._strips[key] = Thumb(None, str(exc))
            else:
                self._planes[key[0]] = (strip.panels[0].plane.source_shape, strip.z, strip.t)
                z = f"depth slice {strip.z}" if strip.z is not None else "no depth axis"
                t = f"time point {strip.t}" if strip.t is not None else "no time axis"
                self.where[key[0]] = f"{z}, {t}"
                self._strips[key] = tuple(p.panel for p in strip.panels)
        return self._strips[key]

    def get(self, layout: UnitLayout, c: int, *, detail: bool = False) -> Thumb:
        """Return channel ``c`` of the unit's representative image, 256 px or 1024 px detail."""
        if not layout.image or not layout.usable:
            return Thumb(None, layout.problem)
        found = self._read(layout, DETAIL_SIDE if detail else THUMBNAIL_SIDE)
        if isinstance(found, Thumb):
            return found
        if c >= len(found):
            return Thumb(None, f"the image has no channel {c}")
        return Thumb(png_bytes(found[c].pixels), found[c].note)

    def colour(self, layout: UnitLayout, *, detail: bool = False) -> Thumb:
        """Return the unit's three channels as one colour picture (display only).

        The note says how it is drawn (:data:`COLOUR_TEXT`); a unit whose image cannot be drawn
        in colour gets no picture and the reason.
        """
        if not layout.image or not layout.usable:
            return Thumb(None, layout.problem)
        found = self._read(layout, DETAIL_SIDE if detail else THUMBNAIL_SIDE)
        if isinstance(found, Thumb):
            return found
        try:
            panel = colour_composite(found)
        except ValueError as exc:
            return Thumb(None, str(exc))
        note = f"{COLOUR_TEXT}; {panel.note}" if panel.note else COLOUR_TEXT
        return Thumb(png_bytes(panel.pixels), note)

    def picture(self, layout: UnitLayout, colour: bool) -> Thumb:
        """Return the unit's picture: the colour picture when shown as colour, else channel 1."""
        return self.colour(layout) if colour else self.get(layout, 0)

    def tile(self, layout: UnitLayout, colour: bool) -> TilePictures | Thumb:
        """Return the unit's picture with a crop at native pixels when the thumbnail loses detail.

        The crop is of the same plane and channel(s) as :meth:`picture`. A unit without a
        picture gives the :class:`Thumb` with its reason.
        """
        thumb = self.picture(layout, colour)
        if thumb.png is None:
            return thumb
        image = layout.image or ""
        shape, z, t = self._planes[image]
        if downscale_factor(shape, THUMBNAIL_SIDE) <= CROP_FACTOR:
            return TilePictures(thumb.png, downscale_factor(shape, THUMBNAIL_SIDE), note=thumb.note)
        if (image, colour) not in self._tiles:
            self._tiles[(image, colour)] = self._native_tile(image, colour, (z, t), thumb)
        return self._tiles[(image, colour)]

    def enlarged(self, layout: UnitLayout, colour: bool) -> TilePictures | Thumb:
        """Return :meth:`tile` with the 1024 px picture in place of the thumbnail."""
        found = self.tile(layout, colour)
        if isinstance(found, Thumb):
            return found
        big = self.colour(layout, detail=True) if colour else self.get(layout, 0, detail=True)
        return found if big.png is None else replace(found, png=big.png)

    def _native_tile(
        self, image: str, colour: bool, where: tuple[int | None, int | None], thumb: Thumb
    ) -> TilePictures:
        """Read the plane at native pixels (not cached) and return the thumbnail and crop."""
        mapping = confirmed_mapping(self.review, image)
        has_c = mapping.index_of("C") is not None
        planes = []
        try:
            for c in range(3 if colour else 1):
                plane = read_plane(
                    self.root / image,
                    mapping,
                    max_side=2**31,
                    z=where[0],
                    c=c if has_c else None,
                    t=where[1],
                    memory_limit=self.memory_limit,
                )
                if plane.reduced:
                    reason = "no 1:1 detail: a plane this large is read reduced"
                    return no_crop(TilePictures(thumb.png, plane.step, note=thumb.note), reason)
                planes.append(plane.pixels)
            return tile_pictures(planes, colour, THUMBNAIL_SIDE)
        except READ_ERRORS as exc:
            return no_crop(TilePictures(thumb.png, 1, note=thumb.note), f"no 1:1 detail: {exc}")

    def plane(self, layout: UnitLayout) -> str:
        """Return which plane the pictures of this unit show ("depth slice 3, time point 0")."""
        self.get(layout, 0)
        return self.where.get(layout.image or "", "")


__all__ = ["COLOUR_TEXT", "CONTRAST_TEXT", "READ_ERRORS", "Thumb", "Thumbnails"]
