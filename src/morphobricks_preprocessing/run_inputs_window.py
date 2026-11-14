"""Windows of a run's images for :class:`.run_inputs.RunInputs` (issue #179).

:meth:`WindowInputs.read_window` reads an exact rectangle of one plane of a position, for the
channels asked by their canonical IDs, through :func:`.windows.read_window`. It does not
replace :meth:`.run_inputs.RunInputs.read_plane`, which still reads whole planes.

The plane-coordinate checks shared with ``read_plane`` and ``read_mask`` (moved from
``run_inputs``) live here too.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .axes import AxisMapping
from .manifest import FileEntry
from .planes import DEFAULT_MEMORY_LIMIT
from .validation import plural
from .windows import LEVEL, WindowChannelError, check_level
from .windows import read_window as read_source_window

if TYPE_CHECKING:
    from .run_inputs import Fov


class WindowInputs:
    """The ``read_window`` method of ``RunInputs``.

    It uses ``RunInputs``'s ``experiment_root``, ``_checked``, ``_fov`` and
    ``channel_index``.
    """

    experiment_root: Path

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
        return read_source_window(
            self.experiment_root / entry.relative_path,
            mapping,
            bounds=bounds,
            channels=indices,
            level=level,
            memory_limit=memory_limit,
            **plane,
        )


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
