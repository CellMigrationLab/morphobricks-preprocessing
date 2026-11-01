"""Pixel sizes as a run records them: a value with its unit and source (manifest v2, §9).

Each analysed file's manifest entry gives the size of one step along X, Y and Z (and the
time step along T), by role, each with its unit and where it comes from: ``"header"`` (the
image's own metadata) or, once stated calibration exists (design v0.4 §3), ``"stated"`` or
``"pixels"``. An axis without a size records no value and no source: it is uncalibrated,
never guessed. Bricks read these, never the headers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .physical_sizes import PhysicalSizes

SIZE_ROLES = ("X", "Y", "Z", "T")
"""The roles a size is recorded for, in this order."""

SOURCES = ("header", "stated", "pixels")
"""Where a recorded size comes from."""


@dataclass(frozen=True)
class RecordedSize:
    """The size of one step along one role, as recorded.

    Attributes
    ----------
    role
        ``"X"``, ``"Y"``, ``"Z"`` or ``"T"``.
    value
        The size, or None when unknown (uncalibrated).
    unit
        Its unit as an OME-NGFF name (``"micrometer"``, ``"second"``), or None.
    source
        ``"header"``, ``"stated"`` or ``"pixels"``; None when the size is unknown.
    """

    role: str
    value: float | None = None
    unit: str | None = None
    source: str | None = None

    def to_dict(self) -> dict:
        """Return the size as JSON-compatible data."""
        return {"role": self.role, "value": self.value, "unit": self.unit, "source": self.source}

    @classmethod
    def from_dict(cls, data: Mapping) -> RecordedSize:
        """Read a size written by :meth:`to_dict`.

        Raises
        ------
        ValueError
            For an unknown role or source, or a source without a value.
        """
        role, source = str(data["role"]), data.get("source")
        value = data.get("value")
        if role not in SIZE_ROLES or (source is not None and source not in SOURCES):
            raise ValueError(f"the pixel size {dict(data)!r} has an unknown role or source")
        if (value is None) != (source is None):
            raise ValueError(f"the pixel size {dict(data)!r} needs both a value and a source")
        unit = data.get("unit")
        return cls(role, None if value is None else float(value), unit and str(unit), source)


def header_sizes(sizes: PhysicalSizes) -> tuple[RecordedSize, ...]:
    """Record the sizes an image's header gives, by role, with the source ``"header"``.

    The header's sizes are by its own axis letters, which are the roles of every image
    that passes the axis check (contract §2): named axes agree with their roles, and a
    plain stack's header gives no size.

    Parameters
    ----------
    sizes
        The image's physical sizes (:attr:`.metadata.TiffMetadata.physical_sizes`).

    Returns
    -------
    tuple of RecordedSize
        One entry per role of :data:`SIZE_ROLES`; unknown sizes have no value.
    """
    return tuple(
        RecordedSize(role, sizes.scale(role), sizes.unit(role), "header")
        if sizes.known(role)
        else RecordedSize(role)
        for role in SIZE_ROLES
    )


def sizes_to_data(sizes: Iterable[RecordedSize]) -> list[dict]:
    """Return recorded sizes as JSON-compatible data."""
    return [size.to_dict() for size in sizes]


def sizes_from_data(data: object) -> tuple[RecordedSize, ...]:
    """Read recorded sizes (absent in v1 manifests, which gives none).

    Raises
    ------
    ValueError
        If the data is not a list of sizes.
    """
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ValueError(f"the pixel sizes {data!r} are not a list")
    return tuple(RecordedSize.from_dict(item) for item in data)
