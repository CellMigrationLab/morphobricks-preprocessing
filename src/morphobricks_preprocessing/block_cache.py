"""A bounded cache of decoded stored pieces, shared by successive window reads (issue #304).

Neighbouring windows of a grid share the stored pieces (TIFF strips or tiles, OME-Zarr chunks)
under their margins, and each read decodes them again. A :class:`BlockCache` keeps the most
recently used decoded pieces, up to a number of bytes, so that a piece is decoded about once
for a whole grid. It is only ever used through an explicit object
(:class:`.window_reader.WindowReader`, ``RunInputs.window_reader``): there is no hidden or
global cache, and reading without one is unchanged.

- **Bounded.** The decoded pieces held never exceed ``max_bytes``: the least recently used
  are dropped first, and a piece larger than the bound is used once and not kept.
- **Safe under threads, single-flight.** One lock guards the pieces. A piece asked by several
  threads at once is decoded by one of them while the others wait for it, and every thread
  that waited gets that decode, also when the piece is not kept (larger than the bound, or
  dropped at once by the eviction or a :meth:`BlockCache.close`): a piece is never decoded
  twice for requests that overlap in time. A request that starts after the decode finished
  decodes again when the piece is not held. A decode that fails is not shared: one waiting
  thread decodes again (and gets its own error).
- **Read only.** Every decoded piece the cache hands out is frozen, kept or not (one too large
  to keep, or dropped by a close, is still shared with the threads that waited for it): its
  bytes are copied once into an immutable buffer, outside the lock, so neither the piece nor
  its storage can be made writable again. The windows built from the pieces are new arrays;
  changing one changes no cached piece.
- **Per process.** A cache lives in the process that made it and is never shared with
  another process; its memory counts in that process's plan (``cache_bytes`` in every window
  estimate of its reader). Worker processes each open their own reader and cache.
- **Closed** (:meth:`BlockCache.close`): every piece is dropped and none is kept after, so a
  read still running when its reader is closed finishes, bitwise, but leaves nothing held.
- **Keys** are given by the reader: the image's identity and fingerprint, the level and the
  page and piece (TIFF) or chunk (OME-Zarr). A cached piece is read only, never changed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

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
        self._pending: dict[Hashable, _Flight] = {}
        self._nbytes = 0
        self._decodes = self._hits = self._evictions = 0
        self._closed = False

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
                flight = self._pending.get(key)
                if flight is None:
                    flight = self._pending[key] = _Flight()
                    break
            flight.done.wait()  # another thread decodes it
            with self._lock:
                if flight.decoded:  # its decode, whether or not the piece was kept (F4)
                    self._hits += 1
                    return flight.piece
            # that decode failed: look again (one waiting thread decodes it)
        decoded, piece = False, None
        try:
            piece = decode()
            if piece is not None:  # outside the lock: other pieces are served meanwhile
                piece = _frozen(piece)
            decoded = True
        finally:
            with self._lock:
                self._decodes += 1
                if decoded and piece is not None:
                    self._keep(key, piece)
                flight.decoded, flight.piece = decoded, piece
                self._pending.pop(key)
                flight.done.set()
        return piece

    def _keep(self, key: Hashable, piece: np.ndarray) -> None:
        """Hold a frozen piece, first dropping the least recently used to make room (lock held).

        The pieces held never exceed the bound, not even for a moment.
        """
        if self._closed or piece.nbytes > self.max_bytes:
            return
        while self._nbytes + piece.nbytes > self.max_bytes:
            _, dropped = self._pieces.popitem(last=False)
            self._nbytes -= dropped.nbytes
            self._evictions += 1
        self._pieces[key] = piece
        self._nbytes += piece.nbytes

    def clear(self) -> None:
        """Drop every piece held."""
        with self._lock:
            self._pieces.clear()
            self._nbytes = 0

    def close(self) -> None:
        """Drop every piece held and keep none from now on (decoding still works)."""
        with self._lock:
            self._closed = True
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


@dataclass
class _Flight:
    """One decode in progress: the threads waiting for it get its piece (single-flight)."""

    done: threading.Event = field(default_factory=threading.Event)
    decoded: bool = False
    piece: np.ndarray | None = None


def _frozen(piece: np.ndarray) -> np.ndarray:
    """Return a copy of ``piece`` over an immutable buffer: it can never be made writable."""
    return np.frombuffer(np.ascontiguousarray(piece).tobytes(), dtype=piece.dtype).reshape(
        piece.shape
    )
