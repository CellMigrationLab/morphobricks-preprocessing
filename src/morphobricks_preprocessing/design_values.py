"""The values of ``experiment_design.csv``: its header, booleans, counts and dates.

The rules both design readers share (``docs/contract.md`` §12, §16): a header names
every column once (issue #83: a column named twice would leave a reader to choose one
value, silently), ``control`` and ``confirmed`` are booleans, the counts whole numbers
of at least 0 and ``confirmed_on`` a date in :data:`DATE_FORMAT`. Nothing here reads
files; :mod:`.design` reads them and re-exports :class:`DesignFormatError`.
"""

from __future__ import annotations

import datetime
import unicodedata
from collections.abc import Iterable

DATE_FORMAT = "YYYY-MM-DD"
"""The form of ``confirmed_on``: an ISO 8601 calendar date, e.g. ``2026-10-26``."""

_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0", ""})


class DesignFormatError(ValueError):
    """Raised when a file is not an experiment design this version can read."""


def check_header(columns: Iterable[str], name: str) -> None:
    """Refuse a header that names a column twice, or has a column without a name.

    Names are compared ignoring case and outer whitespace, so ``repeat_group`` and
    `` Repeat_Group`` are the same column: no reader chooses between two values.
    Invisible format characters (a byte-order mark or zero-width space inside the file,
    Unicode category ``Cf``) are ignored too: a column that looks like ``condition`` is
    ``condition``.

    Parameters
    ----------
    columns
        The header's column names, in order.
    name
        The file's name, for the message.

    Raises
    ------
    DesignFormatError
        Naming the column and both positions, or the position of the unnamed column.
    """
    seen: dict[str, tuple[int, str]] = {}
    for number, column in enumerate(columns, start=1):
        key = _visible(column).strip().casefold()
        if not key:
            raise DesignFormatError(
                f"{name}: column {number} of the header has no name. Name it or delete it."
            )
        first_number, first = seen.setdefault(key, (number, column))
        if first_number != number:
            spelled = "" if first == column else f", the second written {column!r}"
            raise DesignFormatError(
                f"{name}: the header names the column {first!r} twice (columns "
                f"{first_number} and {number}{spelled}), so it is not clear which value "
                "to read. Delete one of the two columns."
            )


def _visible(text: str) -> str:
    """Return ``text`` without invisible format characters (Unicode category ``Cf``)."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def parse_bool(value: str, column: str, where: str) -> bool:
    """Return a ``control`` or ``confirmed`` value: true/false, yes/no, 1/0 in any case.

    Raises
    ------
    DesignFormatError
        For any other value, naming the line and column (``where``, ``column``).
    """
    lowered = value.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise DesignFormatError(f"{where}: {column} must be true or false, not {value!r}.")


def parse_count(value: str, column: str, where: str) -> int:
    """Return a count: a whole number of at least 0, or 0 for an empty value.

    Raises
    ------
    DesignFormatError
        For anything else, naming the line and column (``where``, ``column``).
    """
    if not value:
        return 0
    try:
        number = int(value)
    except ValueError:
        raise DesignFormatError(
            f"{where}: {column} must be a whole number, not {value!r}."
        ) from None
    if number < 0:
        raise DesignFormatError(f"{where}: {column} must not be negative.")
    return number


def is_date(value: str) -> bool:
    """Return True if ``value`` is a calendar date written as :data:`DATE_FORMAT`."""
    if len(value) != len(DATE_FORMAT) or value[4] != "-" or value[7] != "-":
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True
