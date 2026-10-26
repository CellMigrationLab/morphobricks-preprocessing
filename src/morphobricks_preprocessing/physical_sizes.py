"""Physical pixel sizes from image metadata, without reading pixels.

The size of one pixel along X and Y, the z-step and the time step come from OME
or ImageJ metadata in TIFF files. Units are given as OME-NGFF (UDUNITS-2) names
such as ``"micrometer"`` and ``"second"``, one per axis, so that an image whose
Y pixels are measured in another unit than its X pixels is described as it is.
Sizes are never guessed: what the metadata does not give is None.

OME-Zarr images give the same sizes in their OME-NGFF metadata (0.4 and 0.5):
the ``scale`` of the level-0 dataset's ``coordinateTransformations``, times the
``scale`` of the image's own ``coordinateTransformations`` when it has them, with
each axis's ``unit`` (:func:`ngff_physical_sizes`, used by :mod:`.zarr_input`).

They are recorded with an image's header (:func:`.metadata.inspect_image`) and
written as the scale of OME-Zarr copies (``docs/contract.md`` §2 and §10).
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


def ngff_physical_sizes(multiscale: dict, axes: str) -> PhysicalSizes:
    """Return the level-0 physical sizes of an OME-NGFF image (0.4 or 0.5).

    The size of one step along each axis is the ``scale`` of the first dataset's
    ``coordinateTransformations`` (level 0), multiplied by the ``scale`` of the
    multiscales entry's own ``coordinateTransformations`` when it has them, as
    OME-NGFF applies them in turn; translations do not change sizes. Units are
    each axis's ``unit``, as written (OME-NGFF uses UDUNITS-2 names such as
    ``"micrometer"``, the names :func:`read_physical_sizes` gives).

    Nothing is guessed: without a level-0 scale (OME-NGFF 0.1 to 0.3, or a scale
    stored in a separate file through ``path``) or with a malformed one, every
    size is None; an axis without a ``unit`` has the unit None, and a scale that
    is not a positive finite number is None. A scale given without a unit is
    kept: it may be a placeholder (some writers, this brick's OME-Zarr copies
    included, write 1 when the size is not known).

    Parameters
    ----------
    multiscale
        One entry of the image's ``multiscales`` metadata.
    axes
        One letter per dimension, as :func:`.zarr_input.ngff_axes` gives them;
        X, Y, Z and T are read, the others (C, Q) ignored.

    Returns
    -------
    PhysicalSizes
        Sizes and units of X, Y, Z and T.
    """
    datasets = multiscale.get("datasets") or []
    first = datasets[0] if isinstance(datasets, list) and datasets else {}
    try:
        scale = _ngff_scale(first, len(axes))
        overall = _ngff_scale(multiscale, len(axes))
    except ValueError:
        return PhysicalSizes()
    if scale is None:
        return PhysicalSizes()
    if overall is not None:
        scale = [
            a * b if a is not None and b is not None else None
            for a, b in zip(scale, overall, strict=True)
        ]
    declared = multiscale.get("axes")
    units: list[str | None] = [None] * len(axes)
    if isinstance(declared, list) and len(declared) == len(axes):
        units = [
            axis["unit"]
            if isinstance(axis, dict) and isinstance(axis.get("unit"), str) and axis["unit"]
            else None
            for axis in declared
        ]
    found: dict[str, tuple[float | None, str | None]] = {}
    for letter, size, unit in zip(axes, scale, units, strict=True):
        if letter in "XYZT":
            found.setdefault(letter, (_number(size), unit))
    sizes = {letter: found.get(letter, (None, None)) for letter in "XYZT"}
    return PhysicalSizes(
        x=sizes["X"][0],
        y=sizes["Y"][0],
        z=sizes["Z"][0],
        t=sizes["T"][0],
        space_unit=sizes["X"][1],
        time_unit=sizes["T"][1],
        x_unit=sizes["X"][1],
        y_unit=sizes["Y"][1],
        z_unit=sizes["Z"][1],
    )


def _ngff_scale(entry: dict, ndim: int) -> list[float | None] | None:
    """Return the ``scale`` in an entry's ``coordinateTransformations``.

    None when the entry has no scale transformation; raises ValueError when it
    has one that cannot be used (stored in a file, or not one number per axis).
    """
    transforms = entry.get("coordinateTransformations") if isinstance(entry, dict) else None
    if not isinstance(transforms, list):
        return None
    for transform in transforms:
        if not isinstance(transform, dict) or transform.get("type") != "scale":
            continue
        values = transform.get("scale")
        if not isinstance(values, Sequence) or isinstance(values, str) or len(values) != ndim:
            raise ValueError("the scale is not given as one number per axis")
        return [_scale_value(v) for v in values]
    return None


def _scale_value(value) -> float | None:
    """Return one scale value as a float; None when it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:  # an integer too large for a float is not a pixel size
        return None


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
