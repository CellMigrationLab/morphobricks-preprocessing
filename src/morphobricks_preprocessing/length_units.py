"""Length units and their conversion to micrometres, as every brick reads them.

The microscopy units of MorphoBricks decision D-a2 (:func:`space_unit`) and the shared
conversion :func:`to_micrometers`, whose result is always a positive finite length (issue
#93). Moved from :mod:`.physical_sizes`, which re-exports them; pure: no I/O.
"""

from __future__ import annotations

import math
import numbers
import re

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
        A positive, finite length (any real number, numpy scalars included, audit m4),
        or None when it is not known.
    unit
        Its unit, as :func:`space_unit` reads it; None (or blank) when not known.

    Returns
    -------
    float or None
        The length in micrometres, always positive and finite; None when the value or its
        unit is not known, so an unknown unit is never taken for another.

    Raises
    ------
    UnsupportedUnitError
        If the unit is not a supported length unit (:func:`space_unit`).
    ValueError
        If the value is not a positive finite number (a bool is refused), or is not one in
        micrometres: ``1e308`` metres overflows to infinity and ``1e-320`` picometres
        underflows to zero (issue #93).
    """
    if value is None:
        return None
    name = space_unit(unit)
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"the length {value!r} is not a number")
    try:
        number = float(value)
    except OverflowError:  # an int beyond the float range
        number = math.inf
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"the length {value!r} is not a positive finite number")
    if name is None:
        return None
    converted = number * _MICROMETERS[name]
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(
            f"the length {value!r} {name} is not a positive finite number of micrometres "
            f"(it converts to {converted!r})"
        )
    return converted


def _decode_escapes(text: str) -> str:
    r"""Return ``text`` with ImageJ's ``\uXXXX`` escapes decoded (``\u00B5`` is ``µ``)."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
