"""Safe display of planes: reduced first, bounded in memory, never guessing (design section 2).

A preview is evidence for the user and nothing else: it never sets a channel, an image type or
a mask representation. The rules, after NucleiSky's ``imshow_safe`` but without its weak points:

- **Reduce before anything else.** :func:`read_plane` returns at most ``max_side`` pixels per
  side (:data:`THUMBNAIL_SIDE` 256, :data:`DETAIL_SIDE` 1024), taking every n-th pixel (nearest,
  never interpolated). The plane is read through the existing block readers of :mod:`.planes`
  with a small read budget, so a 20,000 x 20,000 plane is never held whole for display, whatever
  the run's memory limit. Only a plane the file cannot be split for is read whole, and only under
  the run's memory limit; over it the preview is refused.
- **Never guess an axis or a channel.** :func:`channel_strip` reads only through the roles the
  user confirmed for that file (:class:`.axes.AxisReview`); the channels are the file's ``C``
  axis, and a file without one is one channel, whatever its first dimension is. A file that
  changed since it was fingerprinted is refused.
- **Flat, non-finite and constant images are shown as what they are** (:mod:`.preview_render`).

A refusal is a :class:`PreviewRefused` with a code and a sentence for a status card, never a
traceback to the user.
"""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .axes import AxisMapping, AxisReview
from .fingerprint import Fingerprint
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    PlaneMemoryError,
    PlaneReadError,
    plane_layout,
)
from .planes import read_plane as read_source_plane
from .preview_render import Panel, display_panel, strip_png

THUMBNAIL_SIDE = 256
"""Pixels per side of a card thumbnail."""
DETAIL_SIDE = 1024
"""Pixels per side of an enlarged view."""
READ_BUDGET = 64 * 2**20
"""Working memory a display read may use before the plane is read in blocks: 64 MiB."""
CACHE_SIZE = 64

ROLES_NOT_CONFIRMED = "axis_roles_not_confirmed"
FILE_CHANGED = "file_changed_while_shown"
PLANE_TOO_LARGE = "plane_too_large_to_show"
PLANE_UNREADABLE = "plane_unreadable"
MASK_SIZE_DIFFERS = "mask_size_differs"


class PreviewRefused(Exception):
    """Raised when a preview is not shown; ``code`` is stable and the message is for the user."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ReducedPlane:
    """One plane reduced for display.

    ``pixels`` keeps the source dtype and values: every ``step``-th pixel of the source along Y
    and X, starting at 0. ``source_shape`` is the whole plane's (Y, X). The coordinates are the
    plane's, None for an axis the file lacks.
    """

    pixels: np.ndarray
    step: int
    source_shape: tuple[int, int]
    z: int | None
    c: int | None
    t: int | None

    @property
    def reduced(self) -> bool:
        """True when the preview keeps fewer pixels than the source plane has."""
        return self.step > 1

    @property
    def caption(self) -> str:
        """Which plane this is and how it was reduced, for a label."""
        shown = ", ".join(
            f"{n}={v}" for n, v in (("z", self.z), ("c", self.c), ("t", self.t)) if v is not None
        )
        size = f"{self.source_shape[0]} x {self.source_shape[1]}"
        cut = f", reduced: every {self.step}th pixel of {size}" if self.reduced else f", {size}"
        return (shown or "single plane") + cut


def _stride(pixels: np.ndarray, step: int, max_side: int) -> tuple[np.ndarray, int]:
    """Return ``pixels`` strided to at most ``max_side`` per side, and the total step."""
    extra = math.ceil(max(pixels.shape) / max_side)
    if extra <= 1:
        return pixels, step
    return np.ascontiguousarray(pixels[::extra, ::extra]), step * extra


def read_plane(
    path: str | os.PathLike,
    mapping: AxisMapping,
    *,
    max_side: int,
    z: int | None = None,
    c: int | None = None,
    t: int | None = None,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
) -> ReducedPlane:
    """Read one plane reduced to at most ``max_side`` pixels per side.

    Coordinates left as None default to 0 for the axes the mapping has (as
    :func:`.planes.read_plane`). The read is in blocks whenever the plane is over
    :data:`READ_BUDGET`; the roles are those of ``mapping``, whoever confirmed them (use
    :func:`channel_strip` for confirmed roles).

    Raises
    ------
    PreviewRefused
        ``plane_too_large_to_show`` when a plane that cannot be read in blocks does not fit the
        memory limit, ``plane_unreadable`` when the file cannot be read under ``mapping``.
    ValueError
        When ``max_side`` is below 1.
    IndexError
        When a coordinate is outside the image.
    """
    if max_side < 1:
        raise ValueError(f"max_side is at least 1, not {max_side}")
    limit = min(memory_limit, READ_BUDGET)
    try:
        try:
            plane = read_source_plane(
                path, mapping, z=z, c=c, t=t, memory_limit=limit, preview=True
            )
        except PlaneMemoryError:  # not splittable: whole, but only within the run's limit
            plane = read_source_plane(
                path, mapping, z=z, c=c, t=t, memory_limit=memory_limit, preview=True
            )
    except PlaneMemoryError as error:
        raise PreviewRefused(PLANE_TOO_LARGE, str(error)) from error
    except PlaneReadError as error:
        raise PreviewRefused(PLANE_UNREADABLE, str(error)) from error
    source = tuple(plane.shape)
    pixels, step = _stride(plane.pixels, plane.step, max_side)
    return ReducedPlane(pixels, step, source, plane.z, plane.c, plane.t)


class PreviewCache:
    """A small least-recently-used cache of reduced planes, keyed by the file's fingerprint.

    The key holds the fingerprint, so a file that changed is a different key and is read again.
    """

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self.size = size
        self._items: OrderedDict[Hashable, ReducedPlane] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: Hashable) -> ReducedPlane | None:
        """Return the cached plane and mark it as just used, or None."""
        found = self._items.get(key)
        if found is not None:
            self._items.move_to_end(key)
        return found

    def put(self, key: Hashable, plane: ReducedPlane) -> None:
        """Keep the plane, dropping the least recently used one beyond the size."""
        self._items[key] = plane
        self._items.move_to_end(key)
        while len(self._items) > self.size:
            self._items.popitem(last=False)


@dataclass(frozen=True)
class ChannelPanel:
    """One channel of the strip: the plane shown, its display panel and its label.

    ``header_name`` is the file's raw name for this channel, quoted in the label so outer
    spaces are visible. It is shown, never used to decide what the channel is.
    """

    c: int
    plane: ReducedPlane
    panel: Panel
    label: str


@dataclass(frozen=True)
class ChannelStrip:
    """The channels of one plane of one file, each with its own contrast, and the PNG of them."""

    path: str
    z: int | None
    t: int | None
    panels: tuple[ChannelPanel, ...]
    png: bytes


def confirmed_mapping(review: AxisReview | None, relative_path: str) -> AxisMapping:
    """Return the roles the user confirmed for the file, or refuse.

    Raises
    ------
    PreviewRefused
        ``axis_roles_not_confirmed`` without a review, for a file the review does not cover, or
        for a file with an axis error.
    """
    mapping = None if review is None else review.file_mappings.get(relative_path)
    if mapping is None or relative_path in review.axis_error_files:
        raise PreviewRefused(
            ROLES_NOT_CONFIRMED,
            f"Axis roles are not confirmed for {relative_path}: confirm them before looking at "
            "channels. Nothing is guessed from the shape of the image.",
        )
    return mapping


def _check_fingerprint(
    path: str | os.PathLike, expected: Fingerprint | None, shown: str | None = None
) -> Fingerprint:
    now = Fingerprint.of(path)
    if expected is not None and now != expected:
        name = os.fspath(path) if shown is None else shown
        raise PreviewRefused(
            FILE_CHANGED, f"{name} changed since the folder check read it: check the folder again."
        )
    return now


def check_unchanged(
    path: str | os.PathLike, expected: Fingerprint, shown: str | None = None
) -> None:
    """Refuse when the file is not, now, the revision whose header was read (``expected``).

    ``shown`` is the name the refusal gives the file (its path relative to the experiment);
    the full path otherwise.

    Raises
    ------
    PreviewRefused
        ``file_changed_while_shown``.
    OSError
        When the file is gone.
    """
    _check_fingerprint(path, expected, shown)


def checked_fingerprint(bound: Mapping[str, Fingerprint | None], relative_path: str) -> Fingerprint:
    """Return the fingerprint the folder check bound to an image's header, or refuse.

    A picture is evidence about the image as the check read its header. An image that the
    check did not bind (it changed while its header was read, or it was not fingerprinted) has
    no such revision, so no picture is drawn for it: the user checks the folder again.

    Raises
    ------
    PreviewRefused
        ``file_changed_while_shown``.
    """
    found = bound.get(relative_path)
    if found is None:
        raise PreviewRefused(
            FILE_CHANGED,
            f"{relative_path} is not bound to the header the folder check read (it changed "
            "while it was read): check the folder again.",
        )
    return found


def _label(c: int, names: Sequence[str | None] | None) -> str:
    name = names[c] if names is not None and c < len(names) else None
    return f"C{c}" if not name else f"C{c} '{name}'"


@dataclass(frozen=True)
class StripOptions:
    """What :func:`channel_strip` may be told besides the file and the confirmed roles.

    ``z`` and ``t`` override the middle Z and the first T. ``header_names`` are the raw channel
    names the header gave, for the labels only. ``expected`` is the fingerprint the file had when
    its header was read; a different one is refused. ``cache`` holds reduced planes already read.
    """

    side: int = THUMBNAIL_SIDE
    z: int | None = None
    t: int | None = None
    header_names: Sequence[str | None] | None = None
    expected: Fingerprint | None = None
    cache: PreviewCache | None = None
    memory_limit: int = DEFAULT_MEMORY_LIMIT


def _cached_plane(
    path: str | os.PathLike,
    mapping: AxisMapping,
    where: tuple[Fingerprint, int, int | None, int | None],
    options: StripOptions,
) -> ReducedPlane:
    """Return channel ``c`` reduced, from the cache when the same file content was read."""
    fingerprint, c, z, t = where
    key = (os.fspath(path), fingerprint, c, z, t, options.side)
    plane = options.cache.get(key) if options.cache is not None else None
    if plane is None:
        channel = c if mapping.index_of("C") is not None else None
        plane = read_plane(
            path,
            mapping,
            max_side=options.side,
            z=z,
            c=channel,
            t=t,
            memory_limit=options.memory_limit,
        )
        if options.cache is not None:
            options.cache.put(key, plane)
    return plane


def channel_strip(
    path: str | os.PathLike,
    relative_path: str,
    review: AxisReview | None,
    options: StripOptions = StripOptions(),  # noqa: B008
) -> ChannelStrip:
    """Return one reduced panel per channel of one plane, through the confirmed roles only.

    The plane is the middle Z when the file has a Z axis and the first T when it has a T axis
    (``options`` can override); the strip says which. The channels are the ``C`` axis of the
    confirmed roles; a file without one is a single channel ``C0``, whatever its shape. Each
    channel has its own contrast. ``options.expected`` is checked before the read and the
    fingerprint then taken is checked after it: a file rewritten during the read is refused.

    Parameters
    ----------
    path
        The image file or OME-Zarr folder.
    relative_path
        Its path relative to the experiment root, the key of ``review.file_mappings``.
    review
        The confirmed axis roles; None is refused.
    options
        Size, plane, labels, expected fingerprint, cache and memory limit.

    Raises
    ------
    PreviewRefused
        ``axis_roles_not_confirmed``, ``file_changed_while_shown``, ``plane_too_large_to_show``
        or ``plane_unreadable``.
    """
    mapping = confirmed_mapping(review, relative_path)
    fingerprint = _check_fingerprint(path, options.expected)
    try:
        layout = plane_layout(path, mapping, memory_limit=options.memory_limit)
    except PlaneReadError as error:
        raise PreviewRefused(PLANE_UNREADABLE, str(error)) from error
    dim = mapping.index_of("Z")
    z = options.z if options.z is not None else (None if dim is None else layout.shape[dim] // 2)
    has_t = mapping.index_of("T") is not None
    t = options.t if options.t is not None else (0 if has_t else None)
    panels = []
    for c in range(layout.channel_count):
        plane = _cached_plane(path, mapping, (fingerprint, c, z, t), options)
        label = _label(c, options.header_names)
        panels.append(ChannelPanel(c, plane, display_panel(plane.pixels), label))
    check_unchanged(path, fingerprint)  # a file rewritten during the read is not shown
    return ChannelStrip(os.fspath(path), z, t, tuple(panels), strip_png([p.panel for p in panels]))


def mask_thumbnail(
    path: str | os.PathLike,
    mapping: AxisMapping,
    image: ReducedPlane,
    *,
    side: int = THUMBNAIL_SIDE,
    memory_limit: int = DEFAULT_MEMORY_LIMIT,
    **coordinates: int | None,
) -> ReducedPlane:
    """Read a mask plane reduced with the same step as the image it is drawn on.

    Every n-th pixel is taken, so labels are never interpolated or renumbered. A mask of another
    size than the image is refused, not resampled.

    Raises
    ------
    PreviewRefused
        ``mask_size_differs``, or the refusals of :func:`read_plane`.
    """
    mask = read_plane(path, mapping, max_side=side, memory_limit=memory_limit, **coordinates)
    if mask.source_shape != image.source_shape or mask.step != image.step:
        raise _size_differs(mask.source_shape, image.source_shape)
    return mask


def _size_differs(mask: tuple[int, ...], image: tuple[int, ...]) -> PreviewRefused:
    return PreviewRefused(
        MASK_SIZE_DIFFERS,
        f"The mask is {mask[0]} x {mask[1]} and the image {image[0]} x {image[1]}: they are not "
        "resampled to fit.",
    )


def labels_thumbnail(labels: np.ndarray, image: ReducedPlane) -> ReducedPlane:
    """Reduce a mask plane already read whole with the step of the image it is drawn on.

    Every n-th pixel is taken, as :func:`mask_thumbnail` does, so labels are never interpolated
    or renumbered. A mask of another size than the image is refused, not resampled.

    Raises
    ------
    PreviewRefused
        ``mask_size_differs``.
    """
    if labels.ndim != 2 or tuple(labels.shape) != tuple(image.source_shape):
        raise _size_differs(tuple(labels.shape), image.source_shape)
    pixels = np.ascontiguousarray(labels[:: image.step, :: image.step])
    return ReducedPlane(pixels, image.step, image.source_shape, image.z, None, image.t)


__all__ = [
    "CACHE_SIZE",
    "DETAIL_SIDE",
    "THUMBNAIL_SIDE",
    "ChannelPanel",
    "ChannelStrip",
    "PreviewCache",
    "PreviewRefused",
    "StripOptions",
    "ReducedPlane",
    "channel_strip",
    "check_unchanged",
    "checked_fingerprint",
    "confirmed_mapping",
    "labels_thumbnail",
    "mask_thumbnail",
    "read_plane",
]
