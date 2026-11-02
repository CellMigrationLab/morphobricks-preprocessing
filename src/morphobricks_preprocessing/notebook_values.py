"""Values the notebook shows, computed without widgets or files."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

import numpy as np

from .recorded_sizes import PIXELS, SIZE_ROLES, RecordedSize


def display_range(pixels: np.ndarray, low: float = 0.5, high: float = 99.5) -> tuple[float, float]:
    """Return the brightness range used to show a plane (finite values only)."""
    finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
    if finite.size == 0:
        return 0.0, 1.0
    vmin, vmax = (float(v) for v in np.percentile(finite, [low, high]))
    if vmin == vmax:
        vmax = vmin + 1
    return vmin, vmax


def size_text(size: RecordedSize, has_axis: bool = True) -> str:
    """Describe one recorded size with its source, e.g. ``"0.65 micrometer (header)"``.

    Parameters
    ----------
    size
        The size as the run records it (:class:`.recorded_sizes.RecordedSize`).
    has_axis
        False for an axis the image does not have, which records no size (design v0.4 §3).

    Returns
    -------
    str
        The value, unit and source; ``"pixels (stated)"``; ``"uncalibrated"``; or ``"no
        such axis"``.
    """
    if not has_axis:
        return "no such axis"
    if size.source == PIXELS:
        return "pixels (stated)"
    if size.value is None:
        return "uncalibrated"
    return f"{size.value:g} {size.unit or 'no unit'} ({size.source})"


def size_rows(
    sizes: Mapping[str, Sequence[RecordedSize]],
    roles: Mapping[str, Collection[str | None]] | None = None,
) -> list[tuple[tuple[str, ...], list[str]]]:
    """Group the images by the sizes they are recorded with, for the calibration card.

    Parameters
    ----------
    sizes
        ``{relative path: sizes by role}`` (:attr:`.calibration.CalibrationReview.sizes`).
    roles
        ``{relative path: the roles the image is read with}`` once the roles are confirmed:
        a Z or T it lacks is shown as ``"no such axis"``. None before: every size is shown.

    Returns
    -------
    list
        ``((X, Y, Z, T texts), images)`` per distinct description, in the order of the first
        image of each, images sorted.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for path in sorted(sizes):
        by_role = {size.role: size for size in sizes[path]}
        own = None if roles is None else roles.get(path)
        texts = tuple(
            size_text(
                by_role.get(role, RecordedSize(role)),
                own is None or role in ("X", "Y") or role in own,
            )
            for role in SIZE_ROLES
        )
        groups.setdefault(texts, []).append(path)
    return list(groups.items())
