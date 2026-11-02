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
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, replace
from xml.etree import ElementTree

import tifffile

# The shared unit rule (contract §16), re-exported where it was before its own module.
from .length_units import _MICROMETERS, _SPACE_UNITS, _decode_escapes, to_micrometers
from .length_units import SPACE_UNITS as SPACE_UNITS
from .length_units import UnsupportedUnitError as UnsupportedUnitError
from .length_units import space_unit as space_unit
from .ngff_scale import read_scale

logger = logging.getLogger(__name__)

UNKNOWN_SCALE_KEY = "unknown_scale"
"""Key, in an OME-NGFF multiscales entry's ``metadata``, listing the axes (``"x"``, ...)
whose ``scale`` is a placeholder 1 because their size is not known (this brick's copies)."""

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
    invalid
        What is wrong with a calibration the metadata give but that cannot be used (a
        malformed OME-NGFF ``coordinateTransformations``, :mod:`.ngff_scale`), in plain
        words; None when the calibration is usable or not given. The sizes it would give
        are None (never substituted), and the folder check names the image
        (``invalid_physical_calibration``, issue #94). For information only, not part of
        equality.

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
    invalid: str | None = None

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

        Uses the axis's own unit, with :func:`to_micrometers`. None when the size or its
        unit is unknown, so that sizes in different units can be compared and an unknown
        unit is never taken for another; None too when the size is not a positive finite
        length in micrometres (``1e308`` metres overflows: issue #93), never ``inf``.
        """
        value, unit = {"X": self.x, "Y": self.y, "Z": self.z}[role], self.unit(role)
        try:
            return to_micrometers(value, unit) if unit in _MICROMETERS else None
        except ValueError:
            return None


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
    finite = math.isfinite(a) and math.isfinite(b)  # inf is never equal to anything (#93)
    return finite and abs(a - b) <= PIXEL_SIZE_TOLERANCE * max(a, b)


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
    is None. A size that is not a positive finite length in micrometres (``1e308``
    metres overflows, issue #93) is None, with what is wrong in ``invalid``.
    """
    given = {"X": x, "Y": y, "Z": z}
    problems = {a: _unconvertible(a, size, units[a]) for a, size in given.items()}
    x, y, z = (None if problems[a] else size for a, size in given.items())
    unitless = any(
        size is not None and units[a] is None for a, size in zip("XYZ", (x, y, z), strict=True)
    )
    return PhysicalSizes(
        invalid="; ".join(problem for problem in problems.values() if problem) or None,
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


def _unconvertible(axis: str, size: float | None, unit: str | None) -> str | None:
    """Say why a size cannot be given in micrometres (issue #93); None when it can."""
    try:
        if size is not None and unit in _MICROMETERS:
            to_micrometers(size, unit)
    except ValueError as exc:
        return f"{axis}: {exc}"
    return None


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

    Nothing is guessed: without a level-0 scale (OME-NGFF 0.3) every size
    is None. Nothing is dropped silently either (issue #94): with a malformed
    scale (transformations that are not a list or of another type, a scale stored
    in a separate file through ``path``, not one number per axis) every size is
    None, and a scale value that is not a positive finite number is None, each
    with what is wrong in :attr:`PhysicalSizes.invalid` (:mod:`.ngff_scale`). An
    axis without a ``unit`` has the unit None. A scale given without a unit is
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
    scale, problems = read_scale(first, axes, "level 0")
    overall, more = read_scale(multiscale, axes, "the image")
    problems += more
    if scale is not None and overall is not None:
        scale = [
            a * b if a is not None and b is not None else None
            for a, b in zip(scale, overall, strict=True)
        ]
        problems += [  # two usable scales whose product is not (issue #93)
            f"the scale along {letter}, level 0's times the image's, is {size!r}"
            for letter, size in zip(axes, scale, strict=True)
            if letter in "XYZT" and size is not None and not 0 < size < math.inf
        ]
    invalid = "; ".join(problems) or None
    if scale is None or (more and overall is None):
        return PhysicalSizes(invalid=invalid)
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
    found = _sizes(
        *(sizes[letter][0] for letter in "XYZT"),
        {letter: sizes[letter][1] for letter in "XYZ"},
        sizes["T"][1],
        _unsupported({letter: sizes[letter][0] for letter in "XYZ"}, written),
    )
    return _with_invalid(found, [invalid])


def _with_invalid(sizes: PhysicalSizes, problems: Sequence[str | None]) -> PhysicalSizes:
    """Return ``sizes`` with the problems found first in ``invalid`` (issues #93, #94)."""
    return replace(sizes, invalid="; ".join(filter(None, (*problems, sizes.invalid))) or None)


def _header_size(axis: str, value: object) -> tuple[float | None, str | None]:
    """Read a size given in TIFF metadata, with what is wrong when it is not usable (#93).

    None and no problem when it is not given; None and the problem for ``inf``, ``NaN``,
    zero, a negative number or text that is not a number, never silently unknown.
    """
    number = _number(value)
    if value is None or number is not None:
        return number, None
    return None, f"{axis}: the size {value!r} is not a positive finite number"


def _tiff_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    """Return the physical sizes of an open TIFF file (all None when not given).

    OME-XML that cannot be parsed gives no sizes, with what is wrong in ``invalid``
    (audit m6, the TIFF twin of issue #94): never read as merely uncalibrated.
    """
    if tif.is_ome and tif.ome_metadata:
        try:
            return _ome_sizes(tif.ome_metadata)
        except ElementTree.ParseError as exc:
            return PhysicalSizes(invalid=f"the OME-XML metadata cannot be read ({exc})")
    return _imagej_sizes(tif) if tif.is_imagej else PhysicalSizes()


def _ome_sizes(xml: str) -> PhysicalSizes:
    """Read the sizes of an OME-XML ``Pixels`` element, naming the unusable ones (#93)."""
    root = ElementTree.fromstring(xml)
    pixels = next((e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Pixels"), None)
    if pixels is None:
        return PhysicalSizes()
    given = {axis: pixels.get(f"PhysicalSize{axis}Unit", _OME_DEFAULT_SPACE_UNIT) for axis in "XYZ"}
    units = {axis: _SPACE_UNITS.get(unit) for axis, unit in given.items()}
    time_unit = _TIME_UNITS.get(pixels.get("TimeIncrementUnit", _OME_DEFAULT_TIME_UNIT))
    read = {axis: _header_size(axis, pixels.get(f"PhysicalSize{axis}")) for axis in "XYZ"}
    read["T"] = _header_size("T", pixels.get("TimeIncrement")) if time_unit else (None, None)
    sizes = {axis: size for axis, (size, _) in read.items()}
    written = {
        axis: _written(unit) if units[axis] is None else None for axis, unit in given.items()
    }
    found = _sizes(
        sizes["X"],
        sizes["Y"],
        sizes["Z"],
        sizes["T"],
        units,
        time_unit,
        _unsupported(sizes, written),
    )
    return _with_invalid(found, [problem for _, problem in read.values()])


def _imagej_sizes(tif: tifffile.TiffFile) -> PhysicalSizes:
    """Read the sizes of an ImageJ TIFF, naming the unusable ones of a known unit (#93)."""
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
    spacing = _header_size("Z", meta.get("spacing"))
    given_sizes = {
        "X": _resolution(page.tags.get("XResolution")),
        "Y": _resolution(page.tags.get("YResolution")),
        "Z": spacing[0],
    }
    # Without a unit the resolution tags are a default (often 1 pixel per inch).
    sizes = {axis: size if units[axis] else None for axis, size in given_sizes.items()}
    written = {
        axis: _written(value) if units[axis] is None else None for axis, value in given.items()
    }
    step = _header_size("T", meta.get("finterval")) if time_unit else (None, None)
    found = _sizes(
        sizes["X"],
        sizes["Y"],
        sizes["Z"],
        step[0],
        units,
        time_unit,
        _unsupported(given_sizes, written),
    )
    return _with_invalid(found, [spacing[1] if units["Z"] else None, step[1]])


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


def _resolution(tag) -> float | None:  # noqa: ANN001
    if tag is None:
        return None
    try:
        numerator, denominator = tag.value
        return denominator / numerator if numerator else math.inf  # 0 pixels per unit (#93)
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:  # noqa: ANN001
    if isinstance(value, bool):  # True would otherwise read as a size of 1
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):  # 10**400 overflows a float
        return None
    return number if math.isfinite(number) and number > 0 else None
