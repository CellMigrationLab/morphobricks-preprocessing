"""The TIFF source of a window read: the strips or tiles a window decodes (issue #179).

:func:`_tiff_source` opens a TIFF series for :mod:`.windows`: its layout, how channels are
grouped, a reader of stored boxes of one plane and, for the window's plan, the largest stored
piece those reads decode, from the headers of the very pages and pieces read (issue #356).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import tifffile

from . import window_memory
from .axes import AxisMapping
from .codec_bounds import check_tiff_codec
from .planes import (
    PlaneLayout,
    PlaneReadError,
    _Binning,
    _largest_piece,
    _layout,
    _page_index,
    _tiff_blocking,
)
from .tiff_pieces import _box_pieces, _read_tiff_box
from .window_source import Bounds, Cached, WindowCompressionError, WindowLayoutError, _Source


def _tiff_source(
    series: tifffile.TiffPageSeries,
    mapping: AxisMapping,
    path: str | os.PathLike,
    memory_limit: int,
    cached: Cached | None = None,
) -> _Source:
    """Return the source of an open TIFF series: only the strips or tiles read are decoded.

    The file must stay open while the source is used. With ``cached``, compressed pieces are
    taken from a reader's cache (keyed by page and piece) and decoded only when missing.
    """
    layout = _tiff_layout(series, mapping, path, memory_limit)
    c_dim = mapping.index_of("C")
    # Channels stored as contiguous samples (interleaved RGB) share every strip or tile.
    interleaved = c_dim is not None and dict(layout.blocking.stored).get(c_dim) == 4

    def groups(channels: Sequence[int]) -> tuple[tuple[int, ...], ...]:
        if interleaved:
            return (tuple(range(len(channels))),)
        return tuple((i,) for i in range(len(channels)))

    def page_of(position: dict[int, int], channels: Sequence[int]) -> tuple[int, list[int]]:
        """Return the page holding ``channels`` of the plane and the plane's place in it."""
        if c_dim is not None:
            position = {**position, c_dim: channels[0]}
        # The plane's index along the separate-sample, depth and contiguous-sample axes.
        where = [0] * 5
        for dim, stored in layout.blocking.stored:
            if stored not in (2, 3):
                where[stored] = position[dim]
        return _page_index(layout, position), where

    def read(
        position: dict[int, int], box: Bounds, channels: Sequence[int], targets: Sequence
    ) -> None:
        index, where = page_of(position, channels)
        page = series.pages[index]
        if page is None:
            raise PlaneReadError(f"Page {index} of {path} is missing from the file.")
        samples = channels if interleaved else (where[4],)
        fetch = None
        if cached is not None:

            def fetch(piece: int, decode: Callable[[], np.ndarray | None]) -> np.ndarray | None:
                return cached(("tiff", index, piece), decode)

        _read_tiff_box(
            page, layout.blocking, (where[0], where[1]), samples, box, targets, path, fetch
        )

    def encoded(
        position: dict[int, int] | None, box: Bounds, groups: Sequence[Sequence[int]]
    ) -> int:
        """Return the largest stored piece the reads of ``groups`` decode, from the headers."""
        if position is None:
            return _largest_piece(series)
        largest = 0
        for channels in groups:
            index, where = page_of(position, channels)
            page = series.pages[index]
            if page is None:
                continue  # reading it is refused
            pieces = _box_pieces(page.keyframe, layout.blocking, (where[0], where[1]), box)
            counts = page.databytecounts
            largest = max([largest, *(int(counts[piece]) for piece, *_ in pieces)])
        return largest

    keyframe = series.keyframe
    extra = window_memory.header_bytes(len(series.pages), len(keyframe.dataoffsets))
    extra += window_memory.codec_state(keyframe.compression)
    return _Source(layout, groups, read, extra, cached is not None, encoded)


def _tiff_layout(
    series: tifffile.TiffPageSeries,
    mapping: AxisMapping,
    path: str | os.PathLike,
    memory_limit: int,
) -> PlaneLayout:
    """Return a TIFF series' layout with how its pages are stored, or refuse it."""
    layout = _layout(series, mapping, path, _Binning(histogram=False), memory_limit)
    # Only codecs with a measured bound are read in windows, refused before any pixel (#356).
    check_tiff_codec([series.keyframe], path, "in windows", WindowCompressionError)
    y, x = mapping.index_of("Y"), mapping.index_of("X")
    blocking = _tiff_blocking(series.keyframe, layout.shape, layout.page_ndim, y, x)
    if blocking is None:
        raise WindowLayoutError(
            f"A window of {Path(path).name} cannot be read: its pages are stored in a way that "
            "cannot be read in parts (subsampled colour, an NDPI JPEG header, or a plane whose "
            "axes are not the rows and columns of its pages). Save the image again as a tiled "
            "TIFF or as OME-Zarr, or read whole planes."
        )
    return replace(layout, blocking=blocking)
