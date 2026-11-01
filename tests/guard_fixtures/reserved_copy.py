"""A copy of a function owned by another repository."""

from __future__ import annotations


def min_max(values: list[float]) -> tuple[float, float]:
    """Return the smallest and largest value."""
    return min(values), max(values)
