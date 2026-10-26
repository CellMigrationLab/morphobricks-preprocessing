"""Physical pixel sizes from image metadata, without reading pixels.

The size of one pixel along X and Y, the z-step and the time step come from OME
or ImageJ metadata in TIFF files. Units are given as OME-NGFF (UDUNITS-2) names
such as ``"micrometer"`` and ``"second"``, one per axis, so that an image whose
Y pixels are measured in another unit than its X pixels is described as it is.
Sizes are never guessed: what the metadata does not give is None.

They are recorded with an image's header (:func:`.metadata.inspect_image`) and
written as the scale of OME-Zarr copies (``docs/contract.md`` §10). The sizes of
OME-Zarr images are read from their OME-NGFF metadata by :mod:`.zarr_input`.
"""

from __future__ import annotations

import logging
import math
import os
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

# The OME schema's unit when a PhysicalSize*Unit attribute is left out.
_OME_DEFAULT_SPACE_UNIT = "µm"
_OME_DEFAULT_TIME_UNIT = "s"


@dataclass(frozen=True)
class PhysicalSizes:
    """Pixel sizes from metadata; None where unknown.

    Attributes
    ----------
    x, y, z
        Pixel size and z-step, each in its own unit (``x_unit``, ``y_unit``,
        ``z_unit``).
    t
        Time between frames in ``time_unit``.
    space_unit
        The unit of X. Kept for callers written when one unit served every space
        axis; :meth:`unit` gives each axis's own unit. Given alone, it is the unit
        of X, Y and Z.
    time_unit
        The unit of T.
    x_unit, y_unit, z_unit
        OME-NGFF unit names per space axis, e.g. ``"micrometer"``; None when the
        metadata gives none.

    Raises
    ------
    ValueError
        If ``space_unit`` and ``x_unit`` are both given and differ.
    """

    x: float | None = None
    y: float | None = None
    z: float | None = None
    t: float | None = None
    space_unit: str | None = None
    time_unit: str | None = None
    x_unit: str | None = None
    y_unit: str | None = None
    z_unit: str | None = None

    def __post_init__(self) -> None:
        if self.x_unit is None and self.y_unit is None and self.z_unit is None:
            # Sizes made with one space unit (before per-axis units) keep meaning it
            # for every space axis.
            for name in ("x_unit", "y_unit", "z_unit"):
                object.__setattr__(self, name, self.space_unit)
        elif self.x_unit is None:
            object.__setattr__(self, "x_unit", self.space_unit)
        elif self.space_unit is None:
            object.__setattr__(self, "space_unit", self.x_unit)
        elif self.space_unit != self.x_unit:
            raise ValueError(
                f"space_unit is the unit of X: {self.space_unit!r} differs from x_unit "
                f"{self.x_unit!r}"
            )

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
        size, unit = {
            "X": (self.x, self.x_unit),
            "Y": (self.y, self.y_unit),
            "Z": (self.z, self.z_unit),
        }[role]
        return unit if size is not None else None


def read_physical_sizes(path: str | os.PathLike) -> PhysicalSizes:
    """Read pixel sizes from OME or ImageJ metadata, without reading pixels.

    Each space axis has its own unit: an OME ``PhysicalSizeYUnit`` or
    ``PhysicalSizeZUnit`` is used for Y or Z (the OME default, micrometres, when
    left out), and ImageJ's ``yunit`` and ``zunit`` when given.

    Returns all-None sizes when the file has no such metadata or it cannot be
    parsed; sizes are never guessed.
    """
    try:
        with tifffile.TiffFile(path) as tif:
            return _tiff_sizes(tif)
    except Exception:  # metadata is optional; a damaged header is reported elsewhere
        logger.debug("Cannot read physical sizes of %s", path, exc_info=True)
    return PhysicalSizes()


def _tiff_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    """Return the physical sizes of an open TIFF file (all None when not given)."""
    try:
        if tif.is_ome and tif.ome_metadata:
            return _ome_sizes(tif.ome_metadata)
        if tif.is_imagej:
            return _imagej_sizes(tif)
    except Exception:  # metadata is optional; a damaged header is reported elsewhere
        logger.debug("Cannot read physical sizes of %s", tif.filename, exc_info=True)
    return PhysicalSizes()


def _ome_sizes(xml: str) -> PhysicalSizes:
    root = ElementTree.fromstring(xml)
    pixels = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Pixels"), None)
    if pixels is None:
        return PhysicalSizes()
    units = {
        axis: _SPACE_UNITS.get(pixels.get(f"PhysicalSize{axis}Unit", _OME_DEFAULT_SPACE_UNIT))
        for axis in "XYZ"
    }
    time_unit = _TIME_UNITS.get(pixels.get("TimeIncrementUnit", _OME_DEFAULT_TIME_UNIT))
    return PhysicalSizes(
        x=_number(pixels.get("PhysicalSizeX")),
        y=_number(pixels.get("PhysicalSizeY")),
        z=_number(pixels.get("PhysicalSizeZ")),
        t=_number(pixels.get("TimeIncrement")) if time_unit else None,
        space_unit=units["X"],
        time_unit=time_unit,
        x_unit=units["X"],
        y_unit=units["Y"],
        z_unit=units["Z"],
    )


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    meta = tif.imagej_metadata or {}
    unit = _imagej_unit(meta.get("unit"))
    # ImageJ writes yunit and zunit only when they differ from the X unit.
    y_unit = _imagej_unit(meta["yunit"]) if "yunit" in meta else unit
    z_unit = _imagej_unit(meta["zunit"]) if "zunit" in meta else unit
    page = tif.pages.first
    time_unit = _TIME_UNITS.get(str(meta.get("tunit", "sec")).strip().lower())
    # Without a unit the resolution tags are a default (often 1 pixel per inch).
    return PhysicalSizes(
        x=_resolution(page.tags.get("XResolution")) if unit else None,
        y=_resolution(page.tags.get("YResolution")) if y_unit else None,
        z=_number(meta.get("spacing")) if z_unit else None,
        t=_number(meta.get("finterval")) if time_unit else None,
        space_unit=unit,
        time_unit=time_unit,
        x_unit=unit,
        y_unit=y_unit,
        z_unit=z_unit,
    )


def _imagej_unit(value) -> str | None:
    return _SPACE_UNITS.get(str(value or "").strip().lower())


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
