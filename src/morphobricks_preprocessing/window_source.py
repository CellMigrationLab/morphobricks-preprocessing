"""The types every window source shares: bounds, the cache hook, the source and its errors.

They live apart from :mod:`.windows` so that the TIFF window source (:mod:`.window_tiff`),
which :mod:`.windows` uses, can build them; :mod:`.windows` still exports them.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

import numpy as np

from .plane_errors import PlaneReadError
from .planes import PlaneLayout

Bounds = tuple[int, int, int, int]
"""``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame."""

Cached = Callable[[Hashable, Callable[[], "np.ndarray | None"]], "np.ndarray | None"]
"""``cached(key, decode)``: a decoded piece from a reader's cache, decoded only when missing.

Keys are ``("tiff", page, piece)`` or ``("zarr", chunk grid position)``; the reader adds the
image's identity, fingerprint and level (:class:`.window_reader.WindowReader`)."""


class WindowError(PlaneReadError):
    """Raised when a window cannot be read as asked."""


class WindowLayoutError(WindowError):
    """The image's pages are stored in a way that cannot be read in parts."""


@dataclass(frozen=True)
class _Source:
    """An open image: its layout (with how planes are stored) and a reader of stored boxes.

    ``groups(channels)`` splits the channels asked into the groups read together, as tuples
    of their places in ``channels``: channels that share stored pieces are one group.
    ``read(position, box, group, targets)`` fills ``targets``, one ``(rows, cols)`` array per
    channel of ``group`` in stored order, with the stored box of the plane at ``position``,
    reading each piece the box intersects once for the whole group. For TIFF,
    ``encoded(position, box, groups)`` is the largest stored piece those reads decode, from
    the headers of the very pages and pieces ``read`` reads (issue #356); with ``position``
    None, the largest piece of the series (a plan for any plane).
    """

    layout: PlaneLayout
    groups: Callable[[Sequence[int]], tuple[tuple[int, ...], ...]]
    read: Callable[[dict[int, int], Bounds, Sequence[int], Sequence[np.ndarray]], None]
    extra: int = 0  # the open header and the codec's state (window_memory)
    cached: bool = False
    encoded: Callable[[dict[int, int] | None, Bounds, Sequence[Sequence[int]]], int] | None = None
