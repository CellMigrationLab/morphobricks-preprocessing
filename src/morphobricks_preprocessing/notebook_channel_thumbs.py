"""Safe thumbnails of the channels of each unit's representative image (PR 13 rules).

Every picture goes through :func:`.preview.channel_strip`: reduced first (every n-th pixel),
then float32, then its own percentile contrast, flat images grey and non-finite pixels magenta,
and only through the axis roles the user confirmed. A picture never names a channel. A picture
that cannot be shown is replaced by the reason, never by a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
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
)
from .preview_render import png_bytes

CONTRAST_TEXT = (
    "Each picture is stretched on its own, from the 0.5th to the 99.5th percentile of its own "
    "pixels (display only: the images are not changed), so brightness cannot be compared between "
    "pictures. A flat picture is grey and a non-finite pixel is magenta. The pictures only help "
    "you check the labels you choose: nothing is guessed from them."
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
        self._strips: dict[tuple[str, int], tuple[Thumb, ...] | Thumb] = {}
        self.where: dict[str, str] = {}

    def _read(self, layout: UnitLayout, side: int) -> tuple[Thumb, ...] | Thumb:
        """Read every channel of the unit's image once per size; a refusal becomes one note."""
        key = (layout.image or "", side)
        if key not in self._strips:
            options = StripOptions(side=side, cache=self._cache, memory_limit=self.memory_limit)
            try:
                strip = channel_strip(self.root / key[0], key[0], self.review, options)
            except (PreviewRefused, OSError, ValueError, IndexError) as exc:
                self._strips[key] = Thumb(None, str(exc))
            else:
                z = f"depth slice {strip.z}" if strip.z is not None else "no depth axis"
                t = f"time point {strip.t}" if strip.t is not None else "no time axis"
                self.where[key[0]] = f"{z}, {t}"
                self._strips[key] = tuple(
                    Thumb(png_bytes(p.panel.pixels), p.panel.note) for p in strip.panels
                )
        return self._strips[key]

    def get(self, layout: UnitLayout, c: int, *, detail: bool = False) -> Thumb:
        """Return channel ``c`` of the unit's representative image, 256 px or 1024 px detail."""
        if not layout.image or not layout.usable:
            return Thumb(None, layout.problem)
        found = self._read(layout, DETAIL_SIDE if detail else THUMBNAIL_SIDE)
        if isinstance(found, Thumb):
            return found
        return found[c] if c < len(found) else Thumb(None, f"the image has no channel {c}")

    def plane(self, layout: UnitLayout) -> str:
        """Return which plane the pictures of this unit show ("depth slice 3, time point 0")."""
        self.get(layout, 0)
        return self.where.get(layout.image or "", "")


__all__ = ["CONTRAST_TEXT", "Thumb", "Thumbnails"]
