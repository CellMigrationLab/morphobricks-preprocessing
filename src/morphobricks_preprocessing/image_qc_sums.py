"""Sums over one block of pixels that the image QC metrics are built from (contract §19).

Every metric of :mod:`.image_qc` is a ratio of sums: of the pixels, of their squares and of
products of each pixel with a neighbour at a fixed offset (a *pair sum*). This module
computes those sums for one block, offset by a constant near its mean so that they are well
conditioned, and :mod:`.image_qc` combines the blocks of a plane.

A pair sum over a whole block is one dot product of the flattened block with itself shifted
by ``dy * width + dx`` places, minus the products that wrapped from the end of one row to the
next (a strip of ``|dx|`` columns). A pair sum over a smaller window subtracts the thin strips
between the window and the valid region. The dot products of every offset are taken in one
sweep over the block, a band of rows at a time (with the rows below it that the offsets
reach), so each band is converted to float64 once and stays in the processor's cache while
all the products are summed.

For an integer image every product and every partial sum is a whole number below ``2**53``
on planes of the usual sizes, so float64 holds them exactly and the result does not depend on
the order of summation. For a float image the sums are taken in a fixed order, so the same
machine gives the same bytes (another processor's vector width may change the last digits).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

BAND_ROWS = 64
"""Rows of a band of the sweep (a 2048-pixel-wide band of float64 is 1 MiB)."""
DOT_LENGTH = 8192
"""Products summed per row of :func:`_dot` (short rows: no threads, the band stays cached)."""


def _dot(a: np.ndarray, b: np.ndarray) -> float:
    """Return the dot product of two contiguous 1-D float64 arrays of one length.

    Summed as rows of :data:`DOT_LENGTH` (``numpy.vecdot``), then the rows in order: a fixed
    order, and no BLAS threads started for each call (measured: thread start-up cost more
    than the products on a loaded machine).
    """
    whole = a.size - a.size % DOT_LENGTH
    found = 0.0
    if whole:
        rows = np.vecdot(a[:whole].reshape(-1, DOT_LENGTH), b[:whole].reshape(-1, DOT_LENGTH))
        found = float(rows.sum())
    return found + float(np.einsum("i,i->", a[whole:], b[whole:]))


@dataclass(frozen=True)
class Window:
    """A rectangle of base pixels: rows ``y0`` to ``y1 - 1``, columns ``x0`` to ``x1 - 1``."""

    y0: int
    y1: int
    x0: int
    x1: int

    @property
    def size(self) -> int:
        """Number of pixels in the window (0 when it is empty)."""
        return max(0, self.y1 - self.y0) * max(0, self.x1 - self.x0)

    def shifted(self, dy: int, dx: int) -> Window:
        """Return the window moved by ``dy`` rows and ``dx`` columns."""
        return Window(self.y0 + dy, self.y1 + dy, self.x0 + dx, self.x1 + dx)


def valid_window(shape: tuple[int, int], dy: int, dx: int) -> Window:
    """Return the base pixels whose neighbour at ``(dy, dx)`` lies inside a block of ``shape``.

    Parameters
    ----------
    shape
        The block's (rows, columns).
    dy, dx
        The offset; ``dy`` is never negative.

    Returns
    -------
    Window
    """
    height, width = shape
    return Window(0, max(0, height - dy), max(0, -dx), max(0, width - max(0, dx)))


class BlockSums:
    """The sums of one block, offset by ``offset`` (its pixels minus a constant).

    Parameters
    ----------
    pixels
        The block, finite values only, as a 2-D array.
    offset
        A constant near the block's mean, subtracted before any sum is taken.
    offsets
        The ``(dy, dx)`` offsets whose pair sums will be asked for (``dy >= 0``, and
        ``dx > 0`` when ``dy == 0``).
    row_edges
        Rows of the block where a band of tiles starts (0 first); the column sums of each
        band are kept (:attr:`bands`: first row, end row, column sums, column sums of squares).
    """

    def __init__(
        self,
        pixels: np.ndarray,
        offset: float,
        offsets: Sequence[tuple[int, int]],
        row_edges: Sequence[int] = (0,),
    ) -> None:
        self.pixels = pixels
        self.offset = float(offset)
        self.shape = (int(pixels.shape[0]), int(pixels.shape[1]))
        self._flat = dict.fromkeys(offsets, 0.0)
        self._pairs: dict[tuple[int, int], float] = {}
        self.bands = self._sweep(list(row_edges))
        self.total = float(sum(band[2].sum() for band in self.bands))
        self.total_sq = float(sum(band[3].sum() for band in self.bands))

    def _sweep(self, row_edges: list[int]) -> list[tuple[int, int, np.ndarray, np.ndarray]]:
        """Take every offset's flat dot product and each band's column sums in one sweep."""
        height, width = self.shape
        reach = max((dy + (dx > 0) for dy, dx in self._flat), default=0)
        steps = {key: key[0] * width + key[1] for key in self._flat}
        buffer = np.empty((min(BAND_ROWS, height) + reach) * width)  # as qc_memory counts it
        edges = sorted({*row_edges, height})
        bands = []
        for start, stop in zip(edges[:-1], edges[1:], strict=True):
            column, column_sq = np.zeros(width), np.zeros(width)
            for top in range(start, stop, BAND_ROWS):
                base = min(BAND_ROWS, stop - top)
                rows = min(height, top + base + reach) - top
                chunk = buffer[: rows * width]
                np.subtract(self.pixels[top : top + rows].reshape(-1), self.offset, out=chunk)
                own = chunk[: base * width].reshape(base, width)
                column += own.sum(axis=0)
                column_sq += np.einsum("ij,ij->j", own, own)
                for key, step in steps.items():
                    count = min(base * width, chunk.size - step)
                    if count > 0:
                        self._flat[key] += _dot(chunk[:count], chunk[step : step + count])
            bands.append((start, stop, column, column_sq))
        return bands

    def window_sum(self, window: Window, squared: bool = False) -> float:
        """Return the sum of the offset pixels (or their squares) over ``window``.

        The total of the block minus the strips around the window, which are thin for the
        windows the metrics use.
        """
        total = self.total_sq if squared else self.total
        height, width = self.shape
        strips = (
            Window(0, window.y0, 0, width),
            Window(window.y1, height, 0, width),
            Window(window.y0, window.y1, 0, window.x0),
            Window(window.y0, window.y1, window.x1, width),
        )
        return total - sum(self._direct_sum(strip, squared) for strip in strips)

    def pair_sum(self, window: Window, dy: int, dx: int) -> float:
        """Return the sum of ``d[y, x] * d[y + dy, x + dx]`` over the base pixels of ``window``.

        ``window`` must lie inside :func:`valid_window` for the offset, which must be one of
        the block's ``offsets``.
        """
        valid = valid_window(self.shape, dy, dx)
        total = self._valid_pair_sum(dy, dx)
        strips = (
            Window(valid.y0, window.y0, valid.x0, valid.x1),
            Window(window.y1, valid.y1, valid.x0, valid.x1),
            Window(window.y0, window.y1, valid.x0, window.x0),
            Window(window.y0, window.y1, window.x1, valid.x1),
        )
        return total - sum(self._direct_pairs(strip, dy, dx) for strip in strips)

    def _valid_pair_sum(self, dy: int, dx: int) -> float:
        """Return the pair sum over every valid base pixel (the flat dot, wraps taken out)."""
        key = (dy, dx)
        if key not in self._pairs:
            height, width = self.shape
            found = self._flat[key]
            # The flat dot also pairs pixels across a row end: take those products back out.
            if dx > 0:
                wrapped = Window(0, height - dy - 1, width - dx, width)
                found -= self._direct_pairs(wrapped, dy + 1, dx - width)
            elif dx < 0:
                wrapped = Window(0, height - dy + 1, 0, -dx)
                found -= self._direct_pairs(wrapped, dy - 1, dx + width)
            self._pairs[key] = found
        return self._pairs[key]

    def _part(self, window: Window) -> np.ndarray:
        """Return the offset pixels of a small window as float64."""
        part = self.pixels[window.y0 : window.y1, window.x0 : window.x1]
        return np.subtract(part, self.offset, dtype=np.float64)

    def _direct_sum(self, window: Window, squared: bool) -> float:
        """Return the sum of the offset pixels (or their squares) of a small window directly."""
        if window.size == 0:
            return 0.0
        part = self._part(window)
        return float(np.einsum("ij,ij->", part, part)) if squared else float(part.sum())

    def _direct_pairs(self, window: Window, dy: int, dx: int) -> float:
        """Return a pair sum over a small window directly (its partners must be in the block)."""
        if window.size == 0:
            return 0.0
        return float(np.einsum("ij,ij->", self._part(window), self._part(window.shifted(dy, dx))))
