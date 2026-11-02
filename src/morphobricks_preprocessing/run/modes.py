"""The two run modes, ``restart`` and ``full`` (design v0.4 §1).

- ``restart`` (the default) reuses the rows of unchanged files and processes new or
  changed ones.
- ``full`` recomputes every row and every check, and reuses no earlier row.

Both keep the user's confirmations (design, axis and mask-axis statements, image type),
publish atomically and only on success, and list in the report what they reused,
recomputed and replaced.
"""

from __future__ import annotations

RESTART = "restart"
"""Reuse the rows of unchanged files; process new or changed ones (the default)."""

FULL = "full"
"""Recompute every row and every check; reuse no earlier row."""

MODES = (RESTART, FULL)


def check_mode(mode: object) -> str:
    """Return ``mode`` if it is one of :data:`MODES`.

    Parameters
    ----------
    mode
        The mode asked for.

    Returns
    -------
    str
        ``"restart"`` or ``"full"``.

    Raises
    ------
    ValueError
        For any other value.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}; found {mode!r}")
    return str(mode)
