"""A bounded cache of decoded stored pieces, shared by successive window reads (issue #304).

Neighbouring windows of a grid share the stored pieces (TIFF strips or tiles, OME-Zarr chunks)
under their margins, and each read decodes them again. A :class:`BlockCache` keeps the most
recently used decoded pieces, up to a number of bytes, so that a piece is decoded about once
for a whole grid. It is only ever used through an explicit object
(:class:`.window_reader.WindowReader`, ``RunInputs.window_reader``): there is no hidden or
global cache, and reading without one is unchanged.

- **Bounded.** The decoded pieces held never exceed ``max_bytes``: the least recently used
  are dropped first, and a piece larger than the bound is used once and not kept.
- **Safe under threads.** One lock guards the pieces. A piece asked by several threads at
  once is decoded by one of them while the others wait for it, so it is decoded once.
- **Keys** are given by the reader: the image's identity and fingerprint, the level and the
  page and piece (TIFF) or chunk (OME-Zarr). A cached piece is read only, never changed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CacheStats:
    """What a :class:`BlockCache` did so far.

    Attributes
    ----------
    decodes
        Pieces decoded (every time a piece was not in the cache).
    hits
        Pieces taken from the cache instead of being decoded.
    evictions
        Pieces dropped to keep the cache under its bound.
    pieces
        Pieces held now.
    nbytes
        Bytes held now (decoded pieces).
    max_bytes
        The bound on ``nbytes``.
    """

    decodes: int
    hits: int
    evictions: int
    pieces: int
    nbytes: int
    max_bytes: int


class BlockCache:
    """A least-recently-used cache of decoded pieces, bounded in bytes and safe under threads.

    Parameters
    ----------
    max_bytes
        Largest number of bytes of decoded pieces held at once; a whole number above 0.

    Raises
    ------
    ValueError
        If ``max_bytes`` is not a whole number above 0.
    """

    def __init__(self, max_bytes: int) -> None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError(
                f"A cache's size is a whole number of bytes above 0; got {max_bytes!r}."
            )
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._pieces: OrderedDict[Hashable, np.ndarray] = OrderedDict()
        self._pending: dict[Hashable, threading.Event] = {}
        self._nbytes = 0
        self._decodes = self._hits = self._evictions = 0

    def get(self, key: Hashable, decode: Callable[[], np.ndarray | None]) -> np.ndarray | None:
        """Return the decoded piece of ``key``, decoding it with ``decode`` if it is not held.

        ``decode`` returns the piece, or None for a piece the file does not store (which is
        not kept). The piece returned is read only.
        """
        while True:
            with self._lock:
                piece = self._pieces.get(key)
                if piece is not None:
                    self._pieces.move_to_end(key)
                    self._hits += 1
                    return piece
                waiting = self._pending.get(key)
                if waiting is None:
                    self._pending[key] = threading.Event()
                    break
            waiting.wait()  # another thread decodes it; then look again
        piece = None
        try:
            piece = decode()
        finally:
            with self._lock:
                self._decodes += 1
                if piece is not None:
                    piece.setflags(write=False)
                    self._keep(key, piece)
                self._pending.pop(key).set()
        return piece

    def _keep(self, key: Hashable, piece: np.ndarray) -> None:
        """Hold a piece and drop the least recently used ones over the bound (lock held)."""
        if piece.nbytes > self.max_bytes:
            return
        self._pieces[key] = piece
        self._nbytes += piece.nbytes
        while self._nbytes > self.max_bytes:
            _, dropped = self._pieces.popitem(last=False)
            self._nbytes -= dropped.nbytes
            self._evictions += 1

    def clear(self) -> None:
        """Drop every piece held."""
        with self._lock:
            self._pieces.clear()
            self._nbytes = 0

    def stats(self) -> CacheStats:
        """Return what the cache did so far and what it holds now."""
        with self._lock:
            return CacheStats(
                decodes=self._decodes,
                hits=self._hits,
                evictions=self._evictions,
                pieces=len(self._pieces),
                nbytes=self._nbytes,
                max_bytes=self.max_bytes,
            )
