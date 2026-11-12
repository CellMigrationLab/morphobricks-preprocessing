"""One image read for looking: every channel of one plane, by the roles given.

The single reader behind the shipped notebook's viewer (:mod:`.notebook_viewer`; owner,
11 November 2026: one image reader and one display component for every step). It reads one
plane (one depth slice ``z`` and one time point ``t``) of one image through the roles it is
given and draws each channel on its own, at most :data:`CHANNELS_SHOWN` of them, each with its
own contrast (:func:`.preview_render.display_panel`). An image whose channels are the red, green
and blue of a colour picture is drawn as one colour picture.

The rules are those of :mod:`.preview`: every plane is reduced first and read within the
display budget, nothing is guessed (the roles are the caller's: the ones a header names, the
ones being edited, or the confirmed ones, and the caller says which), and with a fingerprint
the image is read only as that revision (checked before and after the read). Display only:
nothing read here feeds an answer, the draft or the run.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from .axes import AxisMapping
from .fingerprint import Fingerprint
from .header_preview import CHANNELS_SHOWN
from .planes import DEFAULT_MEMORY_LIMIT
from .preview import THUMBNAIL_SIDE, ReducedPlane, check_unchanged, read_plane
from .preview_render import colour_composite, display_panel, png_bytes

SLIDER_ROLES = ("T", "Z")
"""The roles a viewer steps through with a slider, in the order shown."""


@dataclass(frozen=True)
class ViewTile:
    """One picture of a view: a channel (``c`` None for a colour picture or no channel axis)."""

    c: int | None
    png: bytes
    note: str = ""


@dataclass(frozen=True)
class ImageView:
    """One plane of one image, drawn channel by channel.

    Attributes
    ----------
    tiles
        The pictures, in channel order.
    channels
        The length of the channel dimension (1 without one).
    colour
        True when the tiles are one colour picture of the three channels.
    z, t
        The depth slice and time point shown; None for an axis the image lacks.
    plane
        The whole plane's (height, width) in pixels.
    step
        Every ``step``-th pixel is drawn (1: every pixel).
    dtype
        The pixel type read.
    """

    tiles: tuple[ViewTile, ...]
    channels: int
    colour: bool
    z: int | None
    t: int | None
    plane: tuple[int, int]
    step: int
    dtype: str

    @property
    def shown_text(self) -> str:
        """How many channels are drawn: ``all 3 channels`` or ``6 of 9 channels shown``."""
        if self.colour:
            return "one colour picture of the 3 channels"
        if len(self.tiles) == self.channels:
            return "the only channel" if self.channels == 1 else f"all {self.channels} channels"
        return f"{len(self.tiles)} of {self.channels} channels shown"


def view_lengths(shape: Sequence[int], mapping: AxisMapping) -> dict[str, int]:
    """Return the length of the Z, C and T dimensions under ``mapping`` (1 without one)."""
    lengths = {}
    for role in ("Z", "C", "T"):
        dim = mapping.index_of(role)
        lengths[role] = 1 if dim is None else int(shape[dim])
    return lengths


def middle_slice(length: int) -> int:
    """Return the depth slice a view opens on: the middle one."""
    return length // 2


@dataclass(frozen=True)
class ViewRequest:
    """Which plane of an image to draw, and how.

    Attributes
    ----------
    z, t
        The depth slice and time point; None opens the middle slice and the first time point.
    side
        The largest side of a picture, in pixels.
    shown
        Channels drawn at most.
    colour
        Draw the three channels as one colour picture (the caller's statement that the channel
        dimension holds red, green and blue).
    memory_limit
        The run's memory limit, for a plane that cannot be read in blocks.
    expected
        The fingerprint the image must have before and after the read; None for no binding.
    """

    z: int | None = None
    t: int | None = None
    side: int = THUMBNAIL_SIDE
    shown: int = CHANNELS_SHOWN
    colour: bool = False
    memory_limit: int = DEFAULT_MEMORY_LIMIT
    expected: Fingerprint | None = None


def read_view(
    path: str | os.PathLike,
    mapping: AxisMapping,
    shape: Sequence[int],
    request: ViewRequest = ViewRequest(),  # noqa: B008 - frozen, the defaults
    *,
    name: str | None = None,
) -> ImageView:
    """Read one plane of an image through ``mapping`` and draw its channels.

    Parameters
    ----------
    path
        The image.
    mapping
        The roles to read it with, the image's own (one role per dimension of ``shape``).
    shape
        The image's shape, as its header gives it.
    request
        The plane and how to draw it.
    name
        The name a refusal gives the image (its path relative to the experiment).

    Returns
    -------
    ImageView

    Raises
    ------
    PreviewRefused
        When the image is not the revision expected, or a plane cannot be read or shown.
    ValueError, IndexError, OSError
        When the roles do not fit the image, a coordinate is outside it, or it is gone.
    """
    if mapping.index_of("Y") is None or mapping.index_of("X") is None:
        raise ValueError("no picture without a Y and an X dimension")
    lengths = view_lengths(shape, mapping)
    z = _coordinate(mapping, "Z", lengths, request.z)
    t = _coordinate(mapping, "T", lengths, request.t)
    has_c = mapping.index_of("C") is not None
    colour = request.colour and lengths["C"] == 3
    count = 3 if colour else min(lengths["C"], request.shown)
    if request.expected is not None:
        check_unchanged(path, request.expected, name)
    planes: list[ReducedPlane] = [
        read_plane(
            path,
            mapping,
            max_side=request.side,
            z=z,
            c=c if has_c else None,
            t=t,
            memory_limit=request.memory_limit,
        )
        for c in range(count)
    ]
    if request.expected is not None:
        check_unchanged(path, request.expected, name)  # a rewrite during the read
    panels = [display_panel(plane.pixels) for plane in planes]
    if colour:
        panel = colour_composite(panels)
        tiles: tuple[ViewTile, ...] = (ViewTile(None, png_bytes(panel.pixels), panel.note),)
    else:
        tiles = tuple(
            ViewTile(c if has_c else None, png_bytes(panel.pixels), panel.note)
            for c, panel in enumerate(panels)
        )
    first = planes[0]
    return ImageView(
        tiles, lengths["C"], colour, z, t, first.source_shape, first.step, str(first.pixels.dtype)
    )


def _coordinate(
    mapping: AxisMapping, role: str, lengths: dict[str, int], wanted: int | None
) -> int | None:
    """Return the coordinate to read along ``role``: None without that axis, else in range."""
    if mapping.index_of(role) is None:
        return None
    if wanted is None:
        return middle_slice(lengths[role]) if role == "Z" else 0
    return max(0, min(int(wanted), lengths[role] - 1))


__all__ = [
    "CHANNELS_SHOWN",
    "SLIDER_ROLES",
    "ImageView",
    "ViewRequest",
    "ViewTile",
    "middle_slice",
    "read_view",
    "view_lengths",
]
