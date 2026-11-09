"""Turn reduced planes into display images and PNG bytes, with no widgets and no matplotlib.

The order is NucleiSky's: the plane is reduced first (:mod:`.preview`), then converted to
float32, then given a percentile contrast. Unlike NucleiSky nothing is hidden:

- the contrast is computed on the finite values only, one range per channel, never shared
  between channels, units or images;
- an image whose finite values are all equal is **flat**: it is drawn mid-grey and labelled
  ``flat: every finite value is v``, never as zeros;
- an image with no finite value is drawn magenta and labelled ``no finite values``;
- a non-finite pixel in an ordinary image is drawn magenta, and counted in the label.

A colour (RGB) image is shown as one colour picture (:func:`colour_composite`): its three
channels, each with its own percentile contrast as above, become red, green and blue.

Contrast is for display only. Nothing here changes a source pixel, names a channel or decides
what a mask means: a mask is drawn only for a representation the caller declares.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

LOW_PERCENTILE = 0.5
HIGH_PERCENTILE = 99.5
MAGENTA = (255, 0, 255)
FLAT_GREY = 128
GAP = 4
"""Pixels of white between the panels of a strip."""
INSTANCES, BINARY = "instances", "binary"
REPRESENTATIONS = (INSTANCES, BINARY)
_OUTLINES = ((255, 200, 0), (0, 220, 255), (255, 80, 80), (120, 255, 120), (255, 140, 255))
_TINT = (255, 60, 60)
_TINT_SHARE = 0.4
FLAT, NO_FINITE, NORMAL = "flat", "no_finite", "normal"
COMPOSITE = "composite"
COLOURS = ("red", "green", "blue")


class OverlayRefused(ValueError):
    """Raised when a mask cannot be drawn: undeclared representation, wrong type or size."""


@dataclass(frozen=True)
class Panel:
    """One displayed image: 8-bit pixels, how they were scaled and what to say about them.

    ``pixels`` is (Y, X) grey or (Y, X, 3) colour. ``low`` and ``high`` are the finite values
    shown as black and white (None for a flat image or one without finite values). ``note`` is
    the sentence for the caption, empty for an ordinary image without non-finite pixels.
    """

    pixels: np.ndarray
    kind: str
    low: float | None
    high: float | None
    nonfinite: int
    note: str


def _float32(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return the plane as float32 and the mask of its finite values (reduced planes only)."""
    finite = np.isfinite(pixels) if pixels.dtype.kind == "f" else np.ones(pixels.shape, bool)
    with np.errstate(over="ignore", invalid="ignore"):
        wide = pixels.astype(np.float32)
    limit = np.finfo(np.float32).max
    return np.where(finite, np.clip(wide, -limit, limit), np.float32(0)), finite


def _number(value: float) -> str:
    return f"{value:.6g}"


def display_panel(pixels: np.ndarray) -> Panel:
    """Return a plane as a display panel: float32, percentile contrast of the finite values.

    Parameters
    ----------
    pixels
        An already reduced (Y, X) plane, in its own dtype.

    Raises
    ------
    ValueError
        When ``pixels`` is not two-dimensional or is empty.
    """
    if pixels.ndim != 2 or pixels.size == 0:
        raise ValueError(f"a display panel needs a non-empty (Y, X) plane, not {pixels.shape}")
    wide, finite = _float32(pixels)
    missing = int(finite.size - np.count_nonzero(finite))
    if missing == finite.size:
        magenta = np.empty((*pixels.shape, 3), np.uint8)
        magenta[:] = MAGENTA
        return Panel(magenta, NO_FINITE, None, None, missing, "no finite values")
    values = wide[finite]
    low, high = (float(v) for v in np.percentile(values, [LOW_PERCENTILE, HIGH_PERCENTILE]))
    if low == high and float(values.min()) == float(values.max()):
        grey = np.full(pixels.shape, FLAT_GREY, np.uint8)
        note = f"flat: every finite value is {_number(low)}"
        return _with_missing(Panel(grey, FLAT, None, None, missing, note), finite)
    if low == high:  # most values equal, a few others: show the full range instead
        low, high = float(values.min()), float(values.max())
    scaled = np.clip((wide - low) / (high - low), 0.0, 1.0) * 255.0
    note = f"{missing} non-finite pixels shown in magenta" if missing else ""
    return _with_missing(
        Panel(np.rint(scaled).astype(np.uint8), NORMAL, low, high, missing, note), finite
    )


def _with_missing(panel: Panel, finite: np.ndarray) -> Panel:
    """Return the panel with its non-finite pixels drawn magenta (colour only if there are any)."""
    if panel.nonfinite == 0:
        return panel
    colour = np.repeat(panel.pixels[..., None], 3, axis=2)
    colour[~finite] = MAGENTA
    note = f"{panel.nonfinite} non-finite pixels shown in magenta"
    note = f"{panel.note}; {note}" if panel.note else note
    return Panel(colour, panel.kind, panel.low, panel.high, panel.nonfinite, note)


def colour_composite(panels: Sequence[Panel]) -> Panel:
    """Return the three channels of a colour (RGB) image as one colour picture, for display.

    Channel 0 is red, 1 green and 2 blue, each keeping its own contrast (the panels of
    :func:`display_panel`); nothing is mixed between channels or changed in the source. A flat
    channel stays mid-grey in its colour, and the note names it.

    Parameters
    ----------
    panels
        The three grey (Y, X) panels of one plane, in channel order.

    Raises
    ------
    ValueError
        When there are not three panels, they differ in shape, or one is not a grey panel (a
        channel with non-finite values is shown channel by channel instead, never coloured).
    """
    if len(panels) != len(COLOURS):
        raise ValueError(f"a colour picture has three channels, not {len(panels)}")
    if any(p.pixels.ndim != 2 or p.kind == NO_FINITE or p.nonfinite for p in panels):
        raise ValueError("a channel has non-finite values: it is shown channel by channel")
    if len({p.pixels.shape for p in panels}) != 1:
        raise ValueError("the channels of a colour picture have one shape")
    flat = [name for name, p in zip(COLOURS, panels, strict=True) if p.kind == FLAT]
    note = f"flat: {', '.join(flat)}" if flat else ""
    pixels = np.stack([p.pixels for p in panels], axis=-1)
    return Panel(pixels, COMPOSITE, None, None, 0, note)


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def png_bytes(pixels: np.ndarray) -> bytes:
    """Encode (Y, X) grey or (Y, X, 3) colour 8-bit pixels as a PNG (stdlib only).

    Raises
    ------
    ValueError
        For any other dtype or shape.
    """
    grey = pixels.ndim == 2
    if pixels.dtype != np.uint8 or not (grey or (pixels.ndim == 3 and pixels.shape[2] == 3)):
        raise ValueError(f"a PNG is 8-bit (Y, X) or (Y, X, 3), not {pixels.dtype} {pixels.shape}")
    height, width = pixels.shape[:2]
    rows = np.ascontiguousarray(pixels).reshape(height, -1)
    raw = np.concatenate([np.zeros((height, 1), np.uint8), rows], axis=1).tobytes()
    header = struct.pack(">IIBBBBB", width, height, 8, 0 if grey else 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def _as_colour(pixels: np.ndarray) -> np.ndarray:
    return pixels if pixels.ndim == 3 else np.repeat(pixels[..., None], 3, axis=2)


def strip_png(panels: Sequence[Panel], gap: int = GAP) -> bytes:
    """Return the panels side by side as one PNG, separated by white gaps.

    Each panel keeps its own contrast. Panels of different heights are refused: a strip is the
    channels of one plane.
    """
    if not panels:
        raise ValueError("a strip needs at least one panel")
    heights = {p.pixels.shape[0] for p in panels}
    if len(heights) != 1:
        raise ValueError(f"the panels of a strip have one height, not {sorted(heights)}")
    colour = any(p.pixels.ndim == 3 for p in panels)
    pieces = []
    for index, panel in enumerate(panels):
        shown = _as_colour(panel.pixels) if colour else panel.pixels
        if index:
            pieces.append(np.full((shown.shape[0], gap, *shown.shape[2:]), 255, np.uint8))
        pieces.append(shown)
    return png_bytes(np.concatenate(pieces, axis=1))


def _outline(labels: np.ndarray) -> np.ndarray:
    """Return the pixels of an object that touch another value in a 4-neighbourhood."""
    edge = np.zeros(labels.shape, bool)
    for axis in (0, 1):
        different = np.diff(labels, axis=axis) != 0
        first = [slice(None)] * 2
        second = [slice(None)] * 2
        first[axis], second[axis] = slice(None, -1), slice(1, None)
        edge[tuple(first)] |= different
        edge[tuple(second)] |= different
    return edge & (labels != 0)


def mask_overlay_png(panel: Panel, labels: np.ndarray, representation: str | None) -> bytes:
    """Draw a mask on a panel for the representation the user declared, as a PNG.

    ``instances`` draws each object as an outline coloured from its label value (the labels are
    never interpolated or renumbered); ``binary`` tints every non-zero pixel. The mask must be an
    integer or boolean array of the panel's shape: it is never resampled to fit.

    Raises
    ------
    OverlayRefused
        When the representation is None or not one of ``instances``/``binary`` (never inferred
        from the values), the mask is not an integer array, or its shape is not the panel's.
    """
    if representation not in REPRESENTATIONS:
        raise OverlayRefused(
            f"the mask representation is {representation!r}: the user declares it, "
            f"{REPRESENTATIONS}; it is never inferred from the values"
        )
    if labels.dtype.kind not in "uib":
        raise OverlayRefused(f"a mask holds integer labels, not {labels.dtype}")
    if labels.shape != panel.pixels.shape[:2]:
        raise OverlayRefused(
            f"the mask is {labels.shape} and the image {panel.pixels.shape[:2]}: not resampled"
        )
    shown = _as_colour(panel.pixels).copy()
    if representation == BINARY:
        mark = labels != 0
        blend = (1 - _TINT_SHARE) * shown[mark] + _TINT_SHARE * np.array(_TINT)
        shown[mark] = np.rint(blend).astype(np.uint8)
    else:
        edge = _outline(labels)
        palette = np.array(_OUTLINES, np.uint8)
        shown[edge] = palette[labels[edge].astype(np.int64) % len(palette)]
    return png_bytes(shown)
