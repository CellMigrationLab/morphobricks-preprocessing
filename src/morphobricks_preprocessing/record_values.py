"""Values of a recorded JSON entry, checked as read (moved from ``conversion_record``).

Each function returns the value when it is what the record must hold and raises
``ValueError`` otherwise: nothing is converted, defaulted or repaired.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable


def repeated(values: Iterable[str]) -> list[str]:
    """Return the values found more than once, sorted (#387)."""
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _choice(value: object, allowed: tuple[str, ...], name: str) -> str:
    """Return ``value`` when it is one of ``allowed``; raise otherwise."""
    if value not in allowed:
        raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
    return str(value)


def _bits(value: object) -> int | None:
    """Return recorded significant bits: a whole number, or None."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"significant_bits {value!r} is not a whole number")
    return value


def _flag(value: object) -> bool:
    """Return a JSON boolean; raise for anything else."""
    if not isinstance(value, bool):
        raise ValueError(f"{value!r} is not true or false")
    return value
