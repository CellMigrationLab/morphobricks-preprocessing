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
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from xml.etree import ElementTree

import tifffile

logger = logging.getLogger(__name__)

UNKNOWN_SCALE_KEY = "unknown_scale"
"""Key, in an OME-NGFF multiscales entry's ``metadata``, listing the axes (``"x"``, ...)
whose ``scale`` is a placeholder 1 because their size is not known (this brick's copies)."""

# Length units in OME, ImageJ and OME-NGFF metadata, and their OME-NGFF (UDUNITS-2) names.
# The microscopy units only (MorphoBricks decision D-a2, 30 October 2026): picometre,
# angstrom, nanometre, micrometre, millimetre, centimetre and metre, by symbol and by name
# in US and British spelling. Any other unit (inch, decimetre, kilometre, ...) is not
# converted: :func:`space_unit` refuses it by name.
_SPACE_UNITS = {
    "pm": "picometer",
    "Å": "angstrom",  # the OME symbol, LATIN CAPITAL LETTER A WITH RING ABOVE
    "\u212b": "angstrom",  # ANGSTROM SIGN
    "å": "angstrom",  # ImageJ units are compared in lower case
    "nm": "nanometer",
    "µm": "micrometer",
    "μm": "micrometer",
    "um": "micrometer",
    "micron": "micrometer",
    "microns": "micrometer",
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
}
for _name, _prefix in (
    ("picometer", "pico"),
    ("angstrom", None),
    ("nanometer", "nano"),
    ("micrometer", "micro"),
    ("millimeter", "milli"),
    ("centimeter", "centi"),
    ("meter", ""),
):
    _spellings = ("angstrom",) if _prefix is None else (_prefix + "meter", _prefix + "metre")
    for _spelling in _spellings:
        _SPACE_UNITS[_spelling] = _name
        _SPACE_UNITS[_spelling + "s"] = _name
del _name, _prefix, _spellings, _spelling

SPACE_UNITS = tuple(sorted(set(_SPACE_UNITS.values())))
"""The length units this brick reads and converts, as OME-NGFF (UDUNITS-2) names."""


class UnsupportedUnitError(ValueError):
    """Raised for a length unit that is not one of :data:`SPACE_UNITS`."""


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
    unsupported_units
        The space axes whose size the metadata give in a length unit that is not one
        of :data:`SPACE_UNITS` (decision D-a2), as ``(axis letter, unit as written)``
        pairs, for example ``(("X", "inch"), ("Y", "inch"))``. Such a size is never
        converted: the axis is uncalibrated (:meth:`micrometers` is None). Recorded so
        that the unit can be named (``unsupported_pixel_size_unit``); for information
        only, not part of equality.

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
    unsupported_units: tuple[tuple[str, str], ...] = ()

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


def space_unit(text: str | None) -> str | None:
    r"""Return the OME-NGFF (UDUNITS-2) name of a length unit, as every brick reads it.

    The units are those of microscopy (MorphoBricks decision D-a2): ``pm``, ``Å`` or
    ``angstrom``, ``nm``, ``µm`` (also ``um``, ``micron(s)``, ``micrometer(s)``,
    ``micrometre(s)``), ``mm``, ``cm`` and ``m``, each also by its name in US or British
    spelling (``nanometer``, ``nanometre``, ...). ImageJ's escaped micro sign (``\u00B5m``)
    is decoded. Symbols of two letters or fewer are matched exactly (``"Mm"`` is not
    ``"mm"``); longer names in any case.

    Parameters
    ----------
    text
        The unit as written in the metadata or by the user.

    Returns
    -------
    str or None
        For example ``"micrometer"`` for ``"µm"``; None when no unit is given (None or blank).

    Raises
    ------
    UnsupportedUnitError
        For any other unit (``"inch"``, ``"decimeter"``, ``"Mm"``, ...), named in the message:
        a size in it is never converted.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        raise UnsupportedUnitError(f"the unit {text!r} is not text")
    decoded = _decode_escapes(text).strip()
    if not decoded:
        return None
    known = _SPACE_UNITS.get(decoded)
    if known is None and len(decoded) > 2:
        known = _SPACE_UNITS.get(decoded.lower())
    if known is None:
        raise UnsupportedUnitError(
            f"the length unit {text!r} is not supported; use pm, Å (angstrom), nm, µm (um, "
            "micron), mm, cm or m, or their names (micrometer or micrometre, ...)"
        )
    return known


def to_micrometers(value: float | None, unit: str | None) -> float | None:
    """Return a length in micrometres, with the units of :func:`space_unit`.

    Parameters
    ----------
    value
        A positive, finite length, or None when it is not known.
    unit
        Its unit, as :func:`space_unit` reads it; None (or blank) when not known.

    Returns
    -------
    float or None
        The length in micrometres; None when the value or its unit is not known, so an
        unknown unit is never taken for another.

    Raises
    ------
    UnsupportedUnitError
        If the unit is not a supported length unit (:func:`space_unit`).
    ValueError
        If the value is not a positive finite number (a bool is refused).
    """
    if value is None:
        return None
    name = space_unit(unit)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"the length {value!r} is not a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"the length {value!r} is not a positive finite number")
    if name is None:
        return None
    return float(value) * _MICROMETERS[name]


PIXEL_SIZE_TOLERANCE = 1e-3
"""Relative tolerance of :func:`same_pixel_size`: two sizes are the same when they differ by
at most 0.1 % of the larger (MorphoBricks decision D-a)."""


def same_pixel_size(
    a: PhysicalSizes | Sequence[float | None],
    b: PhysicalSizes | Sequence[float | None],
    *,
    roles: Sequence[str] = ("X", "Y"),
) -> bool | None:
    """Return whether two images have the same pixel size, by the rule every brick shares.

    Decision D-a (30 October 2026): sizes are compared in micrometres, axis by axis, and are
    the same when ``|a - b| <= 1e-3 * max(a, b)`` (:data:`PIXEL_SIZE_TOLERANCE`) on every
    axis of ``roles``. One rule for a declared size against a header and for two headers.
    The relation is not transitive (1.0 and 1.0009 are the same, 1.0009 and 1.0018 are the
    same, 1.0 and 1.0018 are not): group images with :func:`pixel_size_groups`.

    Parameters
    ----------
    a, b
        :class:`PhysicalSizes` (converted with :meth:`PhysicalSizes.micrometers`), or sizes
        already in micrometres, one per role (None when unknown).
    roles
        The axes to compare: ``("X", "Y")`` for the pixel size, ``("Z",)`` for the z-step.

    Returns
    -------
    bool or None
        None when either size is unknown on any of the axes (no size, or a unit that cannot
        be converted): it cannot be compared, and is never taken as equal.

    Raises
    ------
    ValueError
        If sizes given as numbers do not have one value per role, or a size is not a
        positive finite number.
    """
    first, second = _micrometer_values(a, roles), _micrometer_values(b, roles)
    if first is None or second is None:
        return None
    return all(_same_length(x, y) for x, y in zip(first, second, strict=True))


def pixel_size_groups(
    sizes: Mapping[Hashable, PhysicalSizes | Sequence[float | None] | None],
    *,
    roles: Sequence[str] = ("X", "Y"),
) -> list[tuple[Hashable, ...]]:
    """Group images whose pixel sizes are linked by :func:`same_pixel_size`.

    Single linkage: two images are in one group when a chain of images joins them, each the
    same size as the next. So images in two groups always differ, while the first and last
    image of one group may differ when their sizes drift by small steps (1.0, 1.0009,
    1.0018). Images whose size is unknown are left out.

    Parameters
    ----------
    sizes
        Each image's sizes (as :func:`same_pixel_size` takes them), by key, for example by
        relative path.
    roles
        The axes compared.

    Returns
    -------
    list of tuple
        The keys of each group, in the mapping's order; the largest group first, then by
        their smallest sizes.
    """
    values: dict[Hashable, tuple[float, ...]] = {}
    for key, size in sizes.items():
        found = _micrometer_values(size, roles) if size is not None else None
        if found is not None:
            values[key] = found
    # Most images share exactly the same sizes: link the distinct sizes only.
    distinct = sorted(set(values.values()))
    parent = list(range(len(distinct)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, first in enumerate(distinct):
        for j in range(i + 1, len(distinct)):
            second = distinct[j]
            if not _same_length(first[0], second[0]):
                break  # sorted by the first axis: no later size can match on it
            if all(_same_length(x, y) for x, y in zip(first, second, strict=True)):
                parent[root(j)] = root(i)
    index = {size: root(i) for i, size in enumerate(distinct)}
    groups: dict[int, list[Hashable]] = {}
    for key, size in values.items():
        groups.setdefault(index[size], []).append(key)
    smallest = {g: min(values[k] for k in keys) for g, keys in groups.items()}
    order = sorted(groups, key=lambda g: (-len(groups[g]), smallest[g]))
    return [tuple(groups[g]) for g in order]


def _same_length(a: float, b: float) -> bool:
    return abs(a - b) <= PIXEL_SIZE_TOLERANCE * max(a, b)


def _micrometer_values(
    sizes: PhysicalSizes | Sequence[float | None], roles: Sequence[str]
) -> tuple[float, ...] | None:
    """Return the sizes along ``roles`` in micrometres; None when any is unknown."""
    if isinstance(sizes, PhysicalSizes):
        values = [sizes.micrometers(role) for role in roles]
    else:
        values = list(sizes)
        if len(values) != len(roles):
            raise ValueError(f"{len(values)} sizes given for the axes {''.join(roles)}")
    if any(v is None for v in values):
        return None
    for v in values:
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"the pixel size {v!r} is not a positive finite number")
    return tuple(float(v) for v in values)


def _sizes(
    x: float | None,
    y: float | None,
    z: float | None,
    t: float | None,
    units: dict[str, str | None],
    time_unit: str | None,
    unsupported: Sequence[tuple[str, str]] = (),
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
        unsupported_units=tuple(unsupported),
    )


def _unsupported(
    sizes: Mapping[str, float | None], written: Mapping[str, str | None]
) -> list[tuple[str, str]]:
    """Return ``(axis, unit)`` for the space axes with a size given in an unsupported unit.

    ``written`` holds each axis's unit as the metadata give it, when it is not one of
    :data:`SPACE_UNITS` (None otherwise).
    """
    return [
        (axis, written[axis])
        for axis in "XYZ"
        if sizes.get(axis) is not None and written.get(axis) is not None
    ]


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
    # Units other than D-a2's are kept as written (never converted): name them.
    written = {
        letter: unit if unit is not None and unit not in _MICROMETERS else None
        for letter, (_, unit) in sizes.items()
        if letter in "XYZ"
    }
    return _sizes(
        sizes["X"][0],
        sizes["Y"][0],
        sizes["Z"][0],
        sizes["T"][0],
        {letter: sizes[letter][1] for letter in "XYZ"},
        sizes["T"][1],
        _unsupported({letter: sizes[letter][0] for letter in "XYZ"}, written),
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


def _scale_value(value) -> float | None:  # noqa: ANN001
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
    given = {axis: pixels.get(f"PhysicalSize{axis}Unit", _OME_DEFAULT_SPACE_UNIT) for axis in "XYZ"}
    units = {axis: _SPACE_UNITS.get(unit) for axis, unit in given.items()}
    time_unit = _TIME_UNITS.get(pixels.get("TimeIncrementUnit", _OME_DEFAULT_TIME_UNIT))
    sizes = {axis: _number(pixels.get(f"PhysicalSize{axis}")) for axis in "XYZ"}
    written = {
        axis: _written(unit) if units[axis] is None else None for axis, unit in given.items()
    }
    return _sizes(
        sizes["X"],
        sizes["Y"],
        sizes["Z"],
        _number(pixels.get("TimeIncrement")) if time_unit else None,
        units,
        time_unit,
        _unsupported(sizes, written),
    )


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    meta = tif.imagej_metadata or {}
    # ImageJ writes yunit and zunit only when they differ from the X unit.
    given = {
        "X": meta.get("unit"),
        "Y": meta["yunit"] if "yunit" in meta else meta.get("unit"),
        "Z": meta["zunit"] if "zunit" in meta else meta.get("unit"),
    }
    units = {axis: _imagej_unit(value) for axis, value in given.items()}
    page = tif.pages.first
    time_unit = _TIME_UNITS.get(_imagej_text(meta.get("tunit", "sec")))
    given_sizes = {
        "X": _resolution(page.tags.get("XResolution")),
        "Y": _resolution(page.tags.get("YResolution")),
        "Z": _number(meta.get("spacing")),
    }
    # Without a unit the resolution tags are a default (often 1 pixel per inch).
    sizes = {axis: size if units[axis] else None for axis, size in given_sizes.items()}
    written = {
        axis: _written(value) if units[axis] is None else None for axis, value in given.items()
    }
    return _sizes(
        sizes["X"],
        sizes["Y"],
        sizes["Z"],
        _number(meta.get("finterval")) if time_unit else None,
        units,
        time_unit,
        _unsupported(given_sizes, written),
    )


def _ngff_unit(value, table: dict[str, str]) -> str | None:  # noqa: ANN001
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


def _written(value) -> str | None:  # noqa: ANN001
    """Return a unit as the metadata write it (escapes decoded); None when blank."""
    if value is None:
        return None
    text = _decode_escapes(str(value)).strip()
    return text or None


def _imagej_unit(value) -> str | None:  # noqa: ANN001
    return _SPACE_UNITS.get(_imagej_text(value))


def _imagej_text(value) -> str:  # noqa: ANN001
    r"""Return an ImageJ unit in lower case, with its ``\uXXXX`` escapes decoded.

    ImageJ writes non-ASCII characters as ``\u00B5`` in the TIFF description, and
    tifffile returns them as written, so ``\u00B5m`` is a micrometre.
    """
    return _decode_escapes(str(value or "")).strip().lower()


def _decode_escapes(text: str) -> str:
    r"""Return ``text`` with ImageJ's ``\uXXXX`` escapes decoded (``\u00B5`` is ``µ``)."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def _resolution(tag) -> float | None:  # noqa: ANN001
    if tag is None:
        return None
    try:
        numerator, denominator = tag.value
        return denominator / numerator if numerator else None
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:  # noqa: ANN001
    if isinstance(value, bool):  # True would otherwise read as a size of 1
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None
