"""One size or unit as image metadata write it, and what reading TIFF metadata may raise.

The helpers :mod:`.physical_sizes` reads each value of OME, ImageJ and OME-NGFF metadata
with (moved from there), and the exceptions it reports as an unusable calibration rather
than as no calibration (round-2 audit A-m6): before, any exception while reading a TIFF's
sizes was logged at debug level and the image read as merely uncalibrated.
"""

from __future__ import annotations

import math
import struct

import tifffile

from .length_units import _SPACE_UNITS, _decode_escapes

TIFF_OPEN_ERRORS = (OSError, ValueError, TypeError, struct.error)
"""What opening a file with ``tifffile.TiffFile`` raises when it cannot be read as a TIFF.

``OSError``: missing, not permitted, an I/O error. ``ValueError``: ``tifffile.TiffFileError``
(not a TIFF, a corrupt structure). ``TypeError`` and ``struct.error``: raised by tifffile
itself on some damaged headers and IFDs (seen in tifffile 2026.10 on truncated and
byte-flipped files). Only the opening is guarded by these, never this brick's own code;
:func:`.physical_sizes.read_physical_sizes` gives such a file no sizes, with the reason in
``PhysicalSizes.invalid``."""

METADATA_READ_ERRORS = (OSError, struct.error, tifffile.TiffFileError)
"""What reading an open TIFF's metadata may raise when its bytes cannot be read: tifffile
reads some tag values on access. The sizes are then unknown with the reason in
``PhysicalSizes.invalid``. Any other exception (``KeyError``, ``AttributeError``, a plain
``ValueError`` or ``TypeError``, ...) is a defect and is raised, never read as uncalibrated."""


def _header_size(axis: str, value: object) -> tuple[float | None, str | None]:
    """Read a size given in TIFF metadata, with what is wrong when it is not usable (#93).

    None and no problem when it is not given; None and the problem for ``inf``, ``NaN``,
    zero, a negative number or text that is not a number, never silently unknown.
    """
    number = _number(value)
    if value is None or number is not None:
        return number, None
    return None, f"{axis}: the size {value!r} is not a positive finite number"


def _ngff_unit(value: object, table: dict[str, str]) -> str | None:
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


def _written(value: object) -> str | None:
    """Return a unit as the metadata write it (escapes decoded); None when blank."""
    if value is None:
        return None
    text = _decode_escapes(str(value)).strip()
    return text or None


def _imagej_unit(value: object) -> str | None:
    """Return the OME-NGFF name of an ImageJ length unit; None when not a supported one."""
    return _SPACE_UNITS.get(_imagej_text(value))


def _imagej_text(value: object) -> str:
    r"""Return an ImageJ unit in lower case, with its ``\uXXXX`` escapes decoded.

    ImageJ writes non-ASCII characters as ``\u00B5`` in the TIFF description, and
    tifffile returns them as written, so ``\u00B5m`` is a micrometre.
    """
    return _decode_escapes(str(value or "")).strip().lower()


def _resolution(axis: str, tag: tifffile.TiffTag | None) -> tuple[float | None, str | None]:
    """Read a resolution tag as a pixel size, with what is wrong when it is malformed."""
    value = None if tag is None else tag.value
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(n, int) for n in value):
        return value[1] / value[0] if value[0] else math.inf, None  # 0 pixels per unit (#93)
    return None, None if tag is None else f"{axis}: the {tag.name} tag {value!r} is malformed"


def _missing_resolution(tags: tifffile.TiffTags, unit: str | None = None) -> str | None:
    """Say which resolution tag is missing when the unit is given (audit A-m6, rc5).

    ImageJ writes both. tifffile drops a tag it cannot read (a value beyond the end of the
    file, an unknown type) with only a log message, so a lone one means the other was lost:
    never read as an axis that is merely uncalibrated. A unit with neither tag (owner, 3
    November 2026) is a calibration without its sizes, never read as no calibration
    either; ``unit`` is the unit as written, for the message.
    """
    present = {axis: tags.get(f"{axis}Resolution") is not None for axis in "XY"}
    if not any(present.values()):
        return (
            f"X, Y: the unit {unit!r} is given but neither the XResolution nor the "
            "YResolution tag is (a unit without a resolution)"
        )
    if all(present.values()):
        return None
    lost, kept = ("Y", "X") if present["X"] else ("X", "Y")
    return f"{lost}: the {lost}Resolution tag is missing or unreadable, {kept}Resolution is given"


def _number(value: object) -> float | None:
    """Return a positive finite number, or None for anything else (a bool, text, inf, ...)."""
    if isinstance(value, bool):  # True would otherwise read as a size of 1
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):  # 10**400 overflows a float
        return None
    return number if math.isfinite(number) and number > 0 else None
