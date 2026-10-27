"""Physical pixel sizes from image metadata, without reading pixels.

TIFF files give them in OME or ImageJ metadata, OME-Zarr images in the scale of
their full-resolution level. Sizes are never guessed: a size without a known
unit is left unknown. They are written into OME-Zarr copies
(:mod:`.zarr_export`) and compared between the images of an experiment in the
folder check (``docs/contract.md`` §11).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from xml.etree import ElementTree

import tifffile

logger = logging.getLogger(__name__)

# Units in OME and ImageJ metadata, and their OME-NGFF (UDUNITS-2) names.
_SPACE_UNITS = {
    "pm": "picometer",
    "nm": "nanometer",
    "µm": "micrometer",
    "μm": "micrometer",
    "um": "micrometer",
    "micron": "micrometer",
    "microns": "micrometer",
    "micrometer": "micrometer",
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
}
_TIME_UNITS = {
    "ns": "nanosecond",
    "µs": "microsecond",
    "μs": "microsecond",
    "us": "microsecond",
    "ms": "millisecond",
    "s": "second",
    "sec": "second",
    "min": "minute",
    "h": "hour",
}

# Micrometres per OME-NGFF space unit, to compare sizes given in different units.
_MICROMETERS = {
    "angstrom": 1e-4,
    "picometer": 1e-6,
    "nanometer": 1e-3,
    "micrometer": 1.0,
    "millimeter": 1e3,
    "centimeter": 1e4,
    "meter": 1e6,
}


@dataclass(frozen=True)
class PhysicalSizes:
    """Pixel sizes from metadata; None where unknown.

    Attributes
    ----------
    x, y, z
        Pixel size and z-step in ``space_unit``.
    t
        Time between frames in ``time_unit``.
    space_unit, time_unit
        OME-NGFF unit names, e.g. ``"micrometer"`` and ``"second"``.
    """

    x: float | None = None
    y: float | None = None
    z: float | None = None
    t: float | None = None
    space_unit: str | None = None
    time_unit: str | None = None

    def scale(self, role: str) -> float:
        """Return the size of one step along ``role`` (1 when unknown)."""
        value = {"X": self.x, "Y": self.y, "Z": self.z, "T": self.t}.get(role)
        return 1.0 if value is None or not value > 0 else float(value)

    def unit(self, role: str) -> str | None:
        """Return the unit of ``role``, or None when unknown or for channels."""
        if role == "T":
            return self.time_unit if self.t is not None else None
        if role == "C":
            return None
        sizes = {"X": self.x, "Y": self.y, "Z": self.z}
        return self.space_unit if sizes[role] is not None else None

    def micrometers(self, role: str) -> float | None:
        """Return the size along ``"X"``, ``"Y"`` or ``"Z"`` in micrometres.

        None when the size or its unit is unknown, so that sizes in different
        units can be compared and an unknown unit is never taken for another.
        """
        value = {"X": self.x, "Y": self.y, "Z": self.z}[role]
        factor = _MICROMETERS.get(self.space_unit or "")
        if value is None or factor is None:
            return None
        return value * factor


def read_physical_sizes(path: str | os.PathLike) -> PhysicalSizes:
    """Read pixel sizes from OME or ImageJ metadata, without reading pixels.

    Returns all-None sizes when the file has no such metadata or it cannot be
    parsed; sizes are never guessed.
    """
    try:
        with tifffile.TiffFile(path) as tif:
            return tiff_physical_sizes(tif)
    except Exception:  # metadata is optional; a damaged header is reported elsewhere
        logger.debug("Cannot read physical sizes of %s", path, exc_info=True)
    return PhysicalSizes()


def tiff_physical_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    """Return the pixel sizes of an open TIFF file (see :func:`read_physical_sizes`)."""
    try:
        if tif.is_ome and tif.ome_metadata:
            return _ome_sizes(tif.ome_metadata)
        if tif.is_imagej:
            return _imagej_sizes(tif)
    except Exception:  # metadata is optional; never let it stop reading the header
        logger.debug("Cannot read physical sizes of %s", tif.filename, exc_info=True)
    return PhysicalSizes()


def ngff_physical_sizes(
    axes: str, scale: Sequence[float | None], units: Sequence[str | None]
) -> PhysicalSizes:
    """Return the pixel sizes of an OME-Zarr image from its level-0 scale.

    Parameters
    ----------
    axes
        One letter per dimension (:func:`.zarr_input.ngff_axes`).
    scale
        The scale of each dimension at level 0; empty when not given.
    units
        The unit of each dimension from the axes metadata; empty when not given.
        A space size without a unit is unknown: OME-NGFF writers put 1 there
        when they do not know the size.
    """

    def size(letter: str) -> tuple[float | None, str | None]:
        if letter not in axes or len(scale) != len(axes):
            return None, None
        index = axes.index(letter)
        unit = units[index] if index < len(units) else None
        if unit is None:
            return None, None
        return _number(scale[index]), unit

    (x, x_unit), (y, _), (z, z_unit) = size("X"), size("Y"), size("Z")
    t, t_unit = size("T")
    space_unit = x_unit or z_unit
    space_unit = _SPACE_UNITS.get(space_unit, space_unit)
    return PhysicalSizes(
        x=x,
        y=y,
        z=z,
        t=t,
        space_unit=space_unit,
        time_unit=_TIME_UNITS.get(t_unit, t_unit) if t is not None else None,
    )


def _ome_sizes(xml: str) -> PhysicalSizes:
    root = ElementTree.fromstring(xml)
    pixels = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Pixels"), None)
    if pixels is None:
        return PhysicalSizes()
    unit = _SPACE_UNITS.get(pixels.get("PhysicalSizeXUnit", "µm"))
    time_unit = _TIME_UNITS.get(pixels.get("TimeIncrementUnit", "s"))
    return PhysicalSizes(
        x=_number(pixels.get("PhysicalSizeX")),
        y=_number(pixels.get("PhysicalSizeY")),
        z=_number(pixels.get("PhysicalSizeZ")),
        t=_number(pixels.get("TimeIncrement")) if time_unit else None,
        space_unit=unit,
        time_unit=time_unit,
    )


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    meta = tif.imagej_metadata or {}
    unit = _SPACE_UNITS.get(str(meta.get("unit", "")).strip().lower())
    page = tif.pages.first
    x = _resolution(page.tags.get("XResolution"))
    y = _resolution(page.tags.get("YResolution"))
    time_unit = _TIME_UNITS.get(str(meta.get("tunit", "sec")).strip().lower())
    if unit is None:
        # Without a unit the resolution tags are a default (often 1 pixel per inch).
        x = y = None
    return PhysicalSizes(
        x=x,
        y=y,
        z=_number(meta.get("spacing")) if unit else None,
        t=_number(meta.get("finterval")) if time_unit else None,
        space_unit=unit,
        time_unit=time_unit,
    )


def _resolution(tag) -> float | None:
    if tag is None:
        return None
    try:
        numerator, denominator = tag.value
        return denominator / numerator if numerator else None
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None
