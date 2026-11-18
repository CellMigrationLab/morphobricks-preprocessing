"""Windows of a run's images for :class:`.run_inputs.RunInputs` (issue #179).

:meth:`WindowInputs.plane_shape` gives a position's ``(height, width)`` from the header, and
:meth:`WindowInputs.read_window` reads an exact rectangle of one plane of a position, for the
channels asked by their canonical IDs, through :func:`.windows.read_window`. It does not
replace :meth:`.run_inputs.RunInputs.read_plane`, which still reads whole planes.
:meth:`WindowInputs.window_reader` opens a :class:`RunWindowReader` on one position, which
reads its windows the same way through a bounded cache of decoded pieces shared between them
(:mod:`.window_reader`, issue #304).

The plane-coordinate checks shared with ``read_plane`` and ``read_mask`` (moved from
``run_inputs``) live here too.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .axes import AxisMapping
from .block_cache import CacheStats
from .fingerprint import Fingerprint
from .manifest import FileEntry
from .planes import DEFAULT_MEMORY_LIMIT, plane_layout
from .validation import plural
from .window_reader import CachePlan, WindowReader, cache_plan
from .windows import LEVEL, WindowChannelError, WindowPlan, check_level
from .windows import read_window as read_source_window

if TYPE_CHECKING:
    from .run_inputs import Fov


class WindowInputs:
    """The ``plane_shape`` and ``read_window`` methods of ``RunInputs``.

    It uses ``RunInputs``'s ``experiment_root``, ``_checked``, ``_fov`` and
    ``channel_index``.
    """

    experiment_root: Path

    def plane_shape(self, fov: Fov | str) -> tuple[int, int]:
        """Return the ``(height, width)`` of one plane of a position, from its header only.

        The sizes along Y and X of the image as the run recorded its axes (Y before X
        whatever the stored order), the frame of :meth:`read_window`'s bounds and the last
        two sizes of :meth:`read_plane`. No pixel is read and no digest of the file's content
        is computed; the image's fingerprint is checked as for every read.

        Raises
        ------
        RunInputsError
            If the image changed since the run, or is not one of the run's.
        PlaneReadError
            If the header no longer fits the recorded axes.
        """
        entry = self._checked(fov)
        layout = plane_layout(self.experiment_root / entry.relative_path, _mapping(entry))
        return layout.plane_shape

    def read_window(
        self,
        fov: Fov | str,
        *,
        t: int,
        z: int,
        bounds: tuple[int, int, int, int],
        channels: Sequence[str],
        level: int = LEVEL,
        memory_limit: int = DEFAULT_MEMORY_LIMIT,
    ) -> np.ndarray:
        """Read an exact window of one plane as a ``(C, h, w)`` array of the stored values.

        Only the stored pieces the window intersects are read (TIFF strips or tiles, OME-Zarr
        chunks), and only for the channels asked; the result is bitwise the same as that
        slice of :meth:`read_plane` (:mod:`.windows`).

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        t, z
            The plane's zero-based coordinates (0 for an axis the image lacks).
        bounds
            ``(y0, y1, x0, x1)``: half-open ``pixel_edge`` bounds in the plane frame (Spec
            ADR 0004), inside the plane. Nothing is padded or clipped.
        channels
            Canonical channel IDs, in the order of the result.
        level
            The resolution level; only the full-resolution level 0 is read.
        memory_limit
            Largest working memory allowed for the read, in bytes.

        Returns
        -------
        numpy.ndarray
            ``(len(channels), y1 - y0, x1 - x0)``, in the stored pixel type. A caller
            records, with what it computes from it, the image's fingerprint as the run
            recorded it (``RunInputs.manifest.file(path)``: ``size``, ``mtime_ns`` and, for
            OME-Zarr, ``inventory_sha256``), ``level``, ``bounds``, ``channels``, ``t`` and
            ``z``. No digest of the file's content is computed here.

        Raises
        ------
        TypeError
            If ``t`` or ``z`` is not a whole number.
        IndexError
            If ``t`` or ``z`` is outside the image.
        RunInputsError
            If the image changed since the run, or is not one of the run's.
        ChannelNotFoundError
            ``A_SCOPE_INCOMPLETE``: a channel ID the image does not have.
        WindowChannelError
            If no channel, or a channel more than once, is asked.
        WindowBoundsError, WindowLevelError, WindowLayoutError, WindowMemoryError
            See :func:`.windows.read_window`.
        """
        entry, indices, plane = self._window_request(fov, t, z, channels, level)
        return read_source_window(
            self.experiment_root / entry.relative_path,
            _mapping(entry),
            bounds=bounds,
            channels=indices,
            level=level,
            memory_limit=memory_limit,
            **plane,
        )

    def window_reader(
        self, fov: Fov | str, *, cache_bytes: int, memory_limit: int = DEFAULT_MEMORY_LIMIT
    ) -> RunWindowReader:
        """Open a reader of a position's windows that shares decoded pieces between them.

        Its :meth:`RunWindowReader.read_window` takes the arguments of :meth:`read_window` and
        gives bitwise the same windows, but keeps the decoded compressed TIFF strips or tiles
        and OME-Zarr chunks in a cache of at most ``cache_bytes``, shared by every window it
        reads and safe under threads (:mod:`.window_reader`). Nothing is cached otherwise:
        :meth:`read_window` is unchanged.

        Parameters
        ----------
        fov
            The position, as a :class:`.run_inputs.Fov` or its image path.
        cache_bytes
            Largest number of bytes of decoded pieces kept; a whole number above 0, below
            ``memory_limit``.
        memory_limit
            Largest working memory of one window read, the cache included, in bytes.

        Raises
        ------
        RunInputsError
            If the image changed since the run, or is not one of the run's.
        ValueError
            If ``cache_bytes`` is not a whole number above 0.
        WindowMemoryError
            If ``cache_bytes`` leaves no room under ``memory_limit``.
        """
        entry = self._checked(fov)
        reader = WindowReader(
            self.experiment_root / entry.relative_path,
            _mapping(entry),
            cache_bytes=cache_bytes,
            memory_limit=memory_limit,
            fingerprint=Fingerprint.recorded(entry),
        )
        return RunWindowReader(self, fov, reader)

    def window_cache_plan(
        self,
        fov: Fov | str,
        *,
        window: tuple[int, int],
        margin: tuple[int, int],
        channels: Sequence[str],
    ) -> CachePlan:
        """Return the cache a grid of windows of a position needs to decode each piece once.

        :func:`.window_reader.cache_plan` for the position's image, its channels by canonical
        ID: ``cache_bytes`` is the figure to open :meth:`window_reader` with, which the caller
        caps with its own memory budget (below it, most pieces are decoded again for each row
        of windows). From the header only; the image's fingerprint is checked.

        Raises
        ------
        RunInputsError
            If the image changed since the run, or is not one of the run's.
        ChannelNotFoundError
            ``A_SCOPE_INCOMPLETE``: a channel ID the image does not have.
        ValueError
            For a window or margin that is not whole numbers (window above 0, margin 0 or more).
        """
        entry = self._checked(fov)
        indices = [self.channel_index(entry.relative_path, c) for c in channels]
        return cache_plan(
            self.experiment_root / entry.relative_path,
            _mapping(entry),
            window=window,
            margin=margin,
            channels=indices,
        )

    def _window_request(
        self, fov: Fov | str, t: int, z: int, channels: Sequence[str], level: int
    ) -> tuple[FileEntry, list[int], dict[str, int]]:
        """Check a window's plane, level and channel IDs: its entry, C indices and plane."""
        call = "read_window(fov, t=, z=, ...)"
        z, t = (_coordinate(v, k, call) for k, v in (("z", z), ("t", t)))
        check_level(level)
        if isinstance(channels, str) or not isinstance(channels, Sequence) or not channels:
            raise WindowChannelError(
                f"The channels of a window are a non-empty list of channel IDs; got {channels!r}."
            )
        entry = self._checked(fov)
        _check_planes(entry.relative_path, self._fov(entry), z, t)
        mapping = _mapping(entry)
        indices = [self.channel_index(entry.relative_path, c) for c in channels]
        plane = {r.lower(): i for r, i in (("Z", z), ("T", t)) if mapping.index_of(r) is not None}
        return entry, indices, plane


class RunWindowReader:
    """Windows of one position of a run, through a shared cache (``RunInputs.window_reader``).

    Safe to share between threads: open one per image and give it to every thread that reads
    the image's windows. Close it (:meth:`close`, or a ``with`` block) when they are done: its
    cache is dropped at once and later windows are refused
    (:class:`.window_reader.WindowReaderClosedError`). Each window is checked as
    :meth:`WindowInputs.read_window` checks it (the image unchanged since the run, the plane,
    level and channel IDs); the image's fingerprint is also taken again after each window, and
    an image changed meanwhile is refused (:class:`.windows.WindowSourceChangedError`), its
    cache emptied.
    """

    def __init__(self, inputs: WindowInputs, fov: Fov | str, reader: WindowReader) -> None:
        self._inputs, self._fov, self._reader = inputs, fov, reader

    def read_window(
        self,
        *,
        t: int,
        z: int,
        bounds: tuple[int, int, int, int],
        channels: Sequence[str],
        level: int = LEVEL,
    ) -> np.ndarray:
        """Read an exact window of one plane, as :meth:`WindowInputs.read_window` reads it.

        The arguments, result and refusals are those of :meth:`WindowInputs.read_window`
        (the memory limit is the reader's, the cache included),
        :class:`.windows.WindowSourceChangedError` for an image changed during the read and
        :class:`.window_reader.WindowReaderClosedError` after :meth:`close`.
        """
        self._reader._check_open()  # a closed reader is refused before anything is read
        _, indices, plane = self._inputs._window_request(self._fov, t, z, channels, level)
        return self._reader.read_window(bounds=bounds, channels=indices, level=level, **plane)

    def window_plan(
        self,
        *,
        t: int,
        z: int,
        bounds: tuple[int, int, int, int],
        channels: Sequence[str],
        level: int = LEVEL,
    ) -> WindowPlan:
        """Work out from the header what reading a window costs, the cache included; no pixel.

        The checks of :meth:`read_window` (the image unchanged since the run, the plane, level,
        channel IDs, bounds) and its plan: ``shape``, ``dtype``, ``pieces`` and
        ``memory_estimate``, which counts this reader's ``cache_bytes`` (a window whose estimate
        is over the reader's memory limit is :class:`.windows.WindowMemoryError`).
        """
        self._reader._check_open()  # a closed reader is refused before anything is read
        _, indices, plane = self._inputs._window_request(self._fov, t, z, channels, level)
        # The plan of the plane read: its own pages and pieces (issue #356).
        return self._reader._plane_plan(
            bounds=bounds, channels=indices, level=level, z=plane.get("z"), t=plane.get("t")
        )

    def stats(self) -> CacheStats:
        """Return the cache's decodes, hits and evictions so far, and what it holds."""
        return self._reader.stats()

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` was called."""
        return self._reader.closed

    def close(self) -> None:
        """Drop the cache now; refuse every later window (``WindowReader.close``)."""
        self._reader.close()

    def __enter__(self) -> RunWindowReader:
        """Return the reader, closed when the ``with`` block is left, also on an error."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the reader (:meth:`close`)."""
        self.close()


def _check_planes(relative_path: str, fov: Fov, z: int | None, t: int | None) -> None:
    """Refuse a ``z`` or ``t`` outside the image (audit I5)."""
    for name, index, count in (("z", z, fov.z_count), ("t", t, fov.t_count)):
        if index is not None and not 0 <= index < count:
            raise IndexError(
                f"{relative_path} has {plural(count, 'plane')} along "
                f"{name.upper()} ({name} 0 to {count - 1}); {name}={index} is outside it"
            )


def _coordinate(value: object, name: str, call: str) -> int:
    """Return a plane coordinate as an int, refusing anything but a whole number."""
    if isinstance(value, bool) or not isinstance(value, int | np.integer):
        raise TypeError(
            f"{call} takes the plane's zero-based coordinates z and t as whole numbers; "
            f"{name} is {value!r} ({type(value).__name__}). A row's plane_id is not a "
            "coordinate: give the row's z and t (PlaneRecord.z, PlaneRecord.t)."
        )
    return int(value)


def _mapping(entry: FileEntry) -> AxisMapping:
    """Return an image's own mapping as the run recorded it."""
    return AxisMapping(entry.axes, tuple(entry.roles or ()), entry.singleton_roles)
