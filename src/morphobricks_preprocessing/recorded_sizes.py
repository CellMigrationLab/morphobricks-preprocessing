"""Pixel sizes as a run records them: a value with its unit and source (manifest v2, §9).

Each analysed file's manifest entry gives the size of one step along X, Y and Z (and the
time step along T), by role, each with its unit and where it comes from: ``"header"`` (the
image's own metadata), ``"stated"`` (a size stated in ``calibration.csv``, design v0.4 §3,
:mod:`.calibration`) or ``"pixels"`` (stated as deliberately uncalibrated: no value). An axis
without a size or statement records no value and no source: it is uncalibrated, never
guessed. Bricks read these, never the headers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .length_units import _MICROMETERS
from .physical_sizes import PhysicalSizes, UnsupportedUnitError, to_micrometers

SIZE_ROLES = ("X", "Y", "Z", "T")
"""The roles a size is recorded for, in this order."""

HEADER = "header"
STATED = "stated"
PIXELS = "pixels"
SOURCES = (HEADER, STATED, PIXELS)
"""Where a recorded size comes from; a ``"pixels"`` statement has no value."""


@dataclass(frozen=True)
class RecordedSize:
    """The size of one step along one role, as recorded.

    Attributes
    ----------
    role
        ``"X"``, ``"Y"``, ``"Z"`` or ``"T"``.
    value
        The size, or None when unknown or stated as ``"pixels"`` (uncalibrated).
    unit
        Its unit as an OME-NGFF name (``"micrometer"``, ``"second"``), or None.
    source
        ``"header"`` or ``"stated"`` with a value; ``"pixels"`` (deliberately uncalibrated,
        no value); None when nothing gives a size.
    """

    role: str
    value: float | None = None
    unit: str | None = None
    source: str | None = None

    def to_dict(self) -> dict:
        """Return the size as JSON-compatible data."""
        return {"role": self.role, "value": self.value, "unit": self.unit, "source": self.source}

    def micrometers(self) -> float | None:
        """Return the size in micrometres; None when unknown or not a convertible length."""
        try:
            return to_micrometers(self.value, self.unit)
        except (UnsupportedUnitError, ValueError):
            return None

    @classmethod
    def from_dict(cls, data: Mapping) -> RecordedSize:
        """Read a size written by :meth:`to_dict`.

        Raises
        ------
        ValueError
            For an unknown role or source, a header or stated size without a value, or a
            value without a source or with ``"pixels"``.
        """
        role, source = str(data["role"]), data.get("source")
        value = data.get("value")
        if role not in SIZE_ROLES or (source is not None and source not in SOURCES):
            raise ValueError(f"the pixel size {dict(data)!r} has an unknown role or source")
        if (value is None) != (source in (None, PIXELS)):
            raise ValueError(
                f"the pixel size {dict(data)!r} needs a value with the source header or "
                "stated, and none otherwise"
            )
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
        One entry per role of :data:`SIZE_ROLES`; unknown sizes have no value. A size is
        recorded only with a supported unit (a length unit of :data:`.SPACE_UNITS`, or a
        time unit): one without a unit, or in another unit (``"pixel"``, ``"inch"``), is
        unknown, never recorded as a value of no unit (audit I3); the folder check names
        such a unit (``unsupported_pixel_size_unit``).
    """
    return tuple(
        RecordedSize(role, sizes.scale(role), sizes.unit(role), HEADER)
        if sizes.known(role) and _supported(role, sizes.unit(role))
        else RecordedSize(role)
        for role in SIZE_ROLES
    )


def _supported(role: str, unit: str | None) -> bool:
    """Return True if ``unit`` is a supported unit of ``role`` (a time unit for T)."""
    return unit is not None if role == "T" else unit in _MICROMETERS


def sizes_to_data(sizes: Iterable[RecordedSize]) -> list[dict]:
    """Return recorded sizes as JSON-compatible data."""
    return [size.to_dict() for size in sizes]


def sizes_from_data(data: object) -> tuple[RecordedSize, ...]:
    """Read recorded sizes: a list, one entry per role (always written; never absent).

    Raises
    ------
    ValueError
        If the data is not a list of sizes.
    """
    if not isinstance(data, list):
        raise ValueError(f"the pixel sizes {data!r} are not a list")
    return tuple(RecordedSize.from_dict(item) for item in data)
