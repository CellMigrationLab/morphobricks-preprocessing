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


# Micrometres per OME-NGFF space unit, as the power of ten that converts to them, to compare
# sizes given in different units. A length is converted by moving the decimal point of the
# shortest decimal that gives its double (``repr``), then rounding once (audit B-m7): so one
# length written in any of these units, as a decimal of at most 15 significant digits, gives
# one double. ``9 * 1e-3`` is 0.009000000000000001 and ``108.37 / 1000`` is
# 0.10837000000000001, where ``float("0.009")`` and ``float("0.10837")`` are not.
_MICROMETERS = {
    "angstrom": -4,
    "picometer": -6,
    "nanometer": -3,
    "micrometer": 0,
    "millimeter": 3,
    "centimeter": 4,
    "meter": 6,
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
        unit is not known, so an unknown unit is never taken for another. It is the double
        nearest the decimal :func:`repr` writes ``value`` as, with its point moved by the
        unit's power of ten (rounded once, by ``float``, never by a binary factor nor by the
        :mod:`decimal` context). For a value parsed from a decimal of at most 15
        significant digits (OME-XML, ImageJ ``spacing``, OME-NGFF JSON, ``calibration.csv``)
        that decimal is the one written, so the same length written in any unit gives the
        same double, the one ``float`` gives for it written in micrometres:
        ``to_micrometers(108.37, "nm") == 0.10837`` and ``to_micrometers(9, "nm") ==
        to_micrometers(0.009, "µm")`` (audit B-m7). At 16 or 17 digits two decimals can give
        one double, and the one written is not known; a size computed rather than written
        (an ImageJ resolution tag's ratio) is converted from its shortest decimal all the same.

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
    converted = _shifted(number, _MICROMETERS[name])
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(
            f"the length {value!r} {name} is not a positive finite number of micrometres "
            f"(it converts to {converted!r})"
        )
    return converted


def _shifted(number: float, power: int) -> float:
    """Return ``number`` times ``10**power``, rounded once from its shortest decimal.

    The point of ``repr(number)`` (``"108.37"``, ``"1e-05"``) is moved by ``power`` in the
    text and ``float`` rounds the result correctly, so no binary factor and no
    :mod:`decimal` context is involved. Beyond the float range it is ``inf`` or ``0.0``,
    which :func:`to_micrometers` refuses.
    """
    mantissa, _, exponent = repr(number).partition("e")
    return float(f"{mantissa}e{int(exponent or 0) + power}")


def _decode_escapes(text: str) -> str:
    r"""Return ``text`` with ImageJ's ``\uXXXX`` escapes decoded (``\u00B5`` is ``µ``)."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
