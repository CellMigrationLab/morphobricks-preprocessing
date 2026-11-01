"""A module that hides exceptions."""

from __future__ import annotations

import contextlib


def quiet() -> None:
    """Hide an error."""
    with contextlib.suppress(ValueError):
        int("x")
