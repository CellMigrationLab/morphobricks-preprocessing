"""Read many windows of one image through a bounded cache of decoded pieces (issue #304).

A grid of windows with margins reads the stored pieces under the margins again for each
neighbour: on a tiled whole-slide image, each tile was decoded about 26 times (#304). A
:class:`WindowReader` is opened on one image and reads its windows as :func:`.windows.read_window`
does, bitwise the same, but keeps the decoded compressed TIFF strips or tiles and OME-Zarr
chunks in a :class:`.block_cache.BlockCache` of ``cache_bytes``, shared by every window it reads
and safe under threads, so that each piece is decoded about once per plane. It is opt-in:
:func:`.windows.read_window` and ``RunInputs.read_window`` keep no cache.

- **Memory.** The cache counts in every window's estimate: a window is refused
  (:class:`.windows.WindowMemoryError`) when its own working memory and ``cache_bytes``
  together are over ``memory_limit``, and ``cache_bytes`` must leave room under it. The cache
  belongs to this reader in this process: another process, or another brick, opens its own
  reader and pays its own ``cache_bytes``; nothing is shared between them.
- **Identity.** The reader holds the image's fingerprint (size and modification time, and for
  OME-Zarr the inventory digest), the one the run recorded when opened through
  ``RunInputs.window_reader``. Pieces are kept under the image's path, that fingerprint, the
  level and the page and piece (TIFF) or chunk (OME-Zarr). The fingerprint is taken again
  before and after each window: an image changed or gone since the reader was opened is
  refused (:class:`.windows.WindowSourceChangedError`) and the cache emptied, never served
  from it.
- **Lifetime.** A reader holds no open file between windows (each window opens the image and
  closes it); it holds only its cache. :meth:`WindowReader.close` (or leaving its ``with``
  block) drops the cache at once; a window asked after is refused
  (:class:`WindowReaderClosedError`), and a window still being read finishes, bitwise, but
  leaves nothing in the cache.
- Uncompressed TIFF pieces are read straight from the file, only the rows needed, as without
  a cache; they are never cached.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Hashable, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np

from .axes import AxisMapping
from .block_cache import BlockCache, CacheStats
from .fingerprint import Fingerprint
from .planes import DEFAULT_MEMORY_LIMIT, format_bytes
from .windows import (
    LEVEL,
    Bounds,
    WindowError,
    WindowMemoryError,
    WindowPlan,
    WindowSourceChangedError,
    _memory_text,
    _plan,
    _read_source,
    _Source,
    _source,
    check_level,
)


class WindowReaderClosedError(WindowError):
    """A window was asked of a window reader after it was closed."""


class WindowReader:
    """Read windows of one image, sharing a bounded cache of decoded pieces between them.

    Parameters
    ----------
    path
        The TIFF file or OME-Zarr folder.
    mapping
        The axis mapping for this file.
    cache_bytes
        Largest number of bytes of decoded pieces kept; a whole number above 0, below
        ``memory_limit``.
    memory_limit
        Largest working memory allowed for one window read, the cache included, in bytes.
    fingerprint
        The fingerprint the image must have (the run's record); None takes it from the disk
        now.

    Raises
    ------
    ValueError
        If ``cache_bytes`` is not a whole number above 0.
    WindowMemoryError
        If ``cache_bytes`` leaves no room under ``memory_limit``.
    WindowSourceChangedError
        If the image is missing, or its fingerprint is not ``fingerprint``.
    """

    def __init__(
        self,
        path: str | os.PathLike,
        mapping: AxisMapping,
        *,
        cache_bytes: int,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
        fingerprint: Fingerprint | None = None,
    ) -> None:
        self.cache = BlockCache(cache_bytes)
        self._closed = False
        if cache_bytes >= memory_limit:
            raise WindowMemoryError(
                f"A window cache of {format_bytes(cache_bytes)} leaves no room for the windows "
                f"under the memory limit of {format_bytes(memory_limit)}. Choose a smaller "
                "cache or raise the limit."
            )
        self.path, self.mapping, self.memory_limit = Path(path), mapping, memory_limit
        self.fingerprint = self._now() if fingerprint is None else fingerprint
        self._check()
        self._identity = os.path.realpath(self.path)

    def window_plan(
        self, *, bounds: Bounds, channels: Sequence[int], level: int = LEVEL
    ) -> WindowPlan:
        """Work out from the header what reading a window touches, the cache included.

        As :func:`.windows.window_plan`, with ``memory_estimate`` counting ``cache_bytes``.
        """
        self._check_open()
        check_level(level)
        self._check()
        with _source(self.path, self.mapping, self.memory_limit, self._cached) as source:
            return self._plan(source, bounds, channels)

    def read_window(
        self,
        *,
        bounds: Bounds,
        channels: Sequence[int],
        z: int | None = None,
        t: int | None = None,
        level: int = LEVEL,
    ) -> np.ndarray:
        """Read an exact window of one plane, as :func:`.windows.read_window` reads it.

        The result is bitwise that of :func:`.windows.read_window`; compressed pieces already
        decoded by an earlier window are taken from the cache.

        Raises
        ------
        WindowSourceChangedError
            If the image changed or went missing since the reader was opened (the cache is
            emptied).
        WindowMemoryError
            If the window's working memory and the cache are together over the limit.
        WindowBoundsError, WindowLevelError, WindowChannelError, WindowLayoutError
            As :func:`.windows.read_window`.
        WindowReaderClosedError
            If the reader was closed.
        """
        self._check_open()
        check_level(level)
        self._check()
        with _source(self.path, self.mapping, self.memory_limit, self._cached) as source:
            self._plan(source, bounds, channels)  # refuses a window over the limit
            room = self.memory_limit - self.cache.max_bytes
            window = _read_source(source, self.mapping, self.path, bounds, channels, z, t, room)
        self._check()  # an image changed while it was read is refused, never kept
        return window

    def stats(self) -> CacheStats:
        """Return the cache's decodes, hits and evictions so far, and what it holds."""
        return self.cache.stats()

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` was called."""
        return self._closed

    def close(self) -> None:
        """Drop the cache now; refuse every later window. Closing twice does nothing more."""
        self._closed = True
        self.cache.close()

    def __enter__(self) -> WindowReader:
        """Return the reader, closed when the ``with`` block is left, also on an error."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the reader (:meth:`close`)."""
        self.close()

    def _check_open(self) -> None:
        """Refuse a window asked after :meth:`close`."""
        if self._closed:
            raise WindowReaderClosedError(
                f"The window reader of {self.path.name} was closed: no window of it is read. "
                "Open a new reader."
            )

    def _plan(self, source: _Source, bounds: object, channels: Sequence[int]) -> WindowPlan:
        """Return a window's plan with the cache in its estimate, refusing one over the limit."""
        plan = _plan(source, bounds, channels, self.memory_limit)
        plan = _with_cache(plan, self.cache.max_bytes)
        if plan.memory_estimate > self.memory_limit:
            raise WindowMemoryError(_memory_text(plan, self.path) + _CACHE_NOTE)
        return plan

    def _cached(self, key: Hashable, decode: Callable[[], np.ndarray | None]) -> np.ndarray | None:
        """Return a decoded piece of this image from the cache, decoding it when missing."""
        return self.cache.get((self._identity, self.fingerprint, LEVEL, *key), decode)

    def _now(self) -> Fingerprint:
        """Return the image's fingerprint on disk now; a missing image is refused."""
        try:
            return Fingerprint.of(self.path)
        except FileNotFoundError as exc:
            raise WindowSourceChangedError(
                f"{self.path.name} cannot be found: a window reader needs the image on disk."
            ) from exc

    def _check(self) -> None:
        """Refuse an image whose fingerprint is not the reader's; the cache is emptied."""
        try:
            now = self._now()
        except WindowSourceChangedError:
            self.cache.clear()
            raise
        if now != self.fingerprint:
            self.cache.clear()
            raise WindowSourceChangedError(
                f"{self.path.name} changed since its window reader was opened (its size or "
                "modification time is not the one recorded), so no window of it is read and "
                "no decoded piece of it is kept. Open a new reader; for a run's image, run "
                "preprocessing again."
            )


_CACHE_NOTE = " The estimate counts the window cache of this reader."


def _with_cache(plan: WindowPlan, cache_bytes: int) -> WindowPlan:
    """Return a plan whose memory estimate also counts a cache of ``cache_bytes``."""
    return replace(plan, memory_estimate=plan.memory_estimate + cache_bytes)
