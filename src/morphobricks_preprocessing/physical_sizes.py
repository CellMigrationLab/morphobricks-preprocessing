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

They are recorded with an image's header (:func:`.metadata.inspect_image`),
written as the scale of OME-Zarr copies (``docs/contract.md`` §2 and §10), and
compared between the images of an experiment in the folder check (§11).
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from xml.etree import ElementTree

import tifffile

logger = logging.getLogger(__name__)

UNKNOWN_SCALE_KEY = "unknown_scale"
"""Key, in an OME-NGFF multiscales entry's ``metadata``, listing the axes (``"x"``, ...)
whose ``scale`` is a placeholder 1 because their size is not known (this brick's copies)."""

# Units in OME and ImageJ metadata, and their OME-NGFF (UDUNITS-2) names.
_SPACE_UNITS = {
    "pm": "picometer",
    "Å": "angstrom",  # the OME symbol, LATIN CAPITAL LETTER A WITH RING ABOVE
    "\u212b": "angstrom",  # ANGSTROM SIGN
    "å": "angstrom",  # ImageJ units are compared in lower case
    "angstrom": "angstrom",
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

# The OME schema's unit when a PhysicalSize*Unit attribute is left out.
_OME_DEFAULT_SPACE_UNIT = "µm"
_OME_DEFAULT_TIME_UNIT = "s"


@dataclass(frozen=True, eq=False)
class PhysicalSizes:
    """Pixel sizes from metadata; None where unknown.

    Attributes
    ----------
    x, y, z
        Pixel size and z-step, each in its own unit (see :meth:`unit`).
    t
        Time between frames in ``time_unit``.
    space_unit
        The unit of the space axes that have no unit of their own. Given alone, as
        before per-axis units, it is the unit of X, Y and Z; the readers give it
        the unit of X (None when a space axis with a size has no unit).
    time_unit
        The unit of T.
    x_unit, y_unit, z_unit
        An axis's own unit, as an OME-NGFF unit name such as ``"micrometer"``;
        None means ``space_unit``. :meth:`unit` resolves them, so they stay
        consistent under :func:`dataclasses.replace` and pickling.

    Two sizes are equal when their sizes, ``space_unit`` and resolved units are.
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

    def _space_units(self) -> tuple[str | None, str | None, str | None]:
        """Return the units of X, Y and Z, each falling back to ``space_unit``."""
        return tuple(
            own if own is not None else self.space_unit
            for own in (self.x_unit, self.y_unit, self.z_unit)
        )

    def _key(self) -> tuple:
        return (self.x, self.y, self.z, self.t, self.space_unit, self.time_unit) + (
            self._space_units()
        )

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def scale(self, role: str) -> float:
        """Return the size of one step along ``role`` (1 when unknown, see :meth:`known`)."""
        value = {"X": self.x, "Y": self.y, "Z": self.z, "T": self.t}.get(role)
        return 1.0 if value is None or not value > 0 else float(value)

    def known(self, role: str) -> bool:
        """Return True if the size of one step along ``role`` (X, Y, Z or T) is known."""
        value = {"X": self.x, "Y": self.y, "Z": self.z, "T": self.t}.get(role)
        return value is not None and value > 0

    def unit(self, role: str) -> str | None:
        """Return the unit of ``role``, or None when unknown or for channels."""
        if role == "T":
            return self.time_unit if self.t is not None else None
        if role == "C":
            return None
        index = "XYZ".index(role)
        size = (self.x, self.y, self.z)[index]
        return self._space_units()[index] if size is not None else None

    def micrometers(self, role: str) -> float | None:
        """Return the size along ``"X"``, ``"Y"`` or ``"Z"`` in micrometres.

        Uses the axis's own unit. None when the size or its unit is unknown, so
        that sizes in different units can be compared and an unknown unit is
        never taken for another.
        """
        value = {"X": self.x, "Y": self.y, "Z": self.z}[role]
        factor = _MICROMETERS.get(self.unit(role) or "")
        if value is None or factor is None:
            return None
        return value * factor


def _sizes(
    x: float | None,
    y: float | None,
    z: float | None,
    t: float | None,
    units: dict[str, str | None],
    time_unit: str | None,
) -> PhysicalSizes:
    """Build sizes from metadata with each space axis's own unit (``units`` by letter).

    ``space_unit`` is the unit of X, as before per-axis units, unless a space axis
    with a size has no unit: it would then fall back to X's unit, so ``space_unit``
    is None.
    """
    unitless = any(
        size is not None and units[a] is None for a, size in zip("XYZ", (x, y, z), strict=True)
    )
    return PhysicalSizes(
        x=x,
        y=y,
        z=z,
        t=t,
        space_unit=None if unitless else units["X"],
        time_unit=time_unit,
        x_unit=units["X"],
        y_unit=units["Y"],
        z_unit=units["Z"],
    )


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
    each axis's ``unit`` as UDUNITS-2 names, the names OME-NGFF uses and
    :func:`read_physical_sizes` gives: common spellings such as ``"µm"``, ``"um"``
    or ``"micron"`` become ``"micrometer"``, other units are kept as written.

    Nothing is guessed: without a level-0 scale (OME-NGFF 0.1 to 0.3, or a scale
    stored in a separate file through ``path``) or with a malformed one, every
    size is None; an axis without a ``unit`` has the unit None, and a scale that
    is not a positive finite number is None. A scale given without a unit is
    kept: it may be a placeholder (some writers write 1 when the size is not
    known). This brick's OME-Zarr copies name such axes in the multiscales
    entry's ``metadata`` (``"unknown_scale": ["x", "y"]``); their sizes are None.

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
        units = [axis.get("unit") if isinstance(axis, dict) else None for axis in declared]
    meta = multiscale.get("metadata")
    unknown = meta.get(UNKNOWN_SCALE_KEY) if isinstance(meta, dict) else None
    unknown = {str(a).upper() for a in unknown} if isinstance(unknown, list) else set()
    found: dict[str, tuple[float | None, str | None]] = {}
    for letter, size, unit in zip(axes, scale, units, strict=True):
        if letter in unknown:
            size = None
        if letter in "XYZT":
            table = _TIME_UNITS if letter == "T" else _SPACE_UNITS
            found.setdefault(letter, (_number(size), _ngff_unit(unit, table)))
    sizes = {letter: found.get(letter, (None, None)) for letter in "XYZT"}
    return _sizes(
        sizes["X"][0],
        sizes["Y"][0],
        sizes["Z"][0],
        sizes["T"][0],
        {letter: sizes[letter][1] for letter in "XYZ"},
        sizes["T"][1],
    )


def _ngff_scale(entry: dict, ndim: int) -> list[float | None] | None:
    """Return the ``scale`` in an entry's ``coordinateTransformations``.

    None when the entry has no scale transformation; raises ValueError when its
    transformations are not a list, or its scale cannot be used (stored in a
    file, or not one number per axis).
    """
    transforms = entry.get("coordinateTransformations") if isinstance(entry, dict) else None
    if transforms is None:
        return None
    if not isinstance(transforms, list):
        raise ValueError("the coordinate transformations are not a list")
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
    return _sizes(
        _number(pixels.get("PhysicalSizeX")),
        _number(pixels.get("PhysicalSizeY")),
        _number(pixels.get("PhysicalSizeZ")),
        _number(pixels.get("TimeIncrement")) if time_unit else None,
        units,
        time_unit,
    )


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    meta = tif.imagej_metadata or {}
    unit = _imagej_unit(meta.get("unit"))
    # ImageJ writes yunit and zunit only when they differ from the X unit.
    y_unit = _imagej_unit(meta["yunit"]) if "yunit" in meta else unit
    z_unit = _imagej_unit(meta["zunit"]) if "zunit" in meta else unit
    page = tif.pages.first
    time_unit = _TIME_UNITS.get(_imagej_text(meta.get("tunit", "sec")))
    # Without a unit the resolution tags are a default (often 1 pixel per inch).
    return _sizes(
        _resolution(page.tags.get("XResolution")) if unit else None,
        _resolution(page.tags.get("YResolution")) if y_unit else None,
        _number(meta.get("spacing")) if z_unit else None,
        _number(meta.get("finterval")) if time_unit else None,
        {"X": unit, "Y": y_unit, "Z": z_unit},
        time_unit,
    )


def _ngff_unit(value, table: dict[str, str]) -> str | None:
    """Return an OME-NGFF axis unit with common spellings as UDUNITS-2 names.

    ``"µm"``, ``"um"`` and ``"micron"`` become ``"micrometer"``, ``"s"``
    becomes ``"second"``; other units are kept as written, and spellings of two
    letters or fewer are matched exactly (``"Mm"`` is not ``"mm"``).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    known = table.get(text) or (table.get(text.lower()) if len(text) > 2 else None)
    return known or text


def _imagej_unit(value) -> str | None:
    return _SPACE_UNITS.get(_imagej_text(value))


def _imagej_text(value) -> str:
    r"""Return an ImageJ unit in lower case, with its ``\uXXXX`` escapes decoded.

    ImageJ writes non-ASCII characters as ``\u00B5`` in the TIFF description, and
    tifffile returns them as written, so ``\u00B5m`` is a micrometre.
    """
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), str(value or ""))
    return text.strip().lower()


def _resolution(tag) -> float | None:
    if tag is None:
        return None
    try:
        numerator, denominator = tag.value
        return denominator / numerator if numerator else None
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:
    if isinstance(value, bool):  # True would otherwise read as a size of 1
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None
