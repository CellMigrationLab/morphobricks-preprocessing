"""The two run modes, ``restart`` and ``full`` (design v0.4 §1).

- ``restart`` (the default) reuses the rows of unchanged files and processes new or
  changed ones.
- ``full`` recomputes every row and every check, and reuses no earlier row.

Both keep the user's confirmations (design, axis and mask-axis statements, image type),
publish atomically and only on success, and list in the report what they reused,
recomputed and replaced.

Either can verify content (:class:`RunMode`, CLI ``--verify-content``; owner, 3 November
2026): rows are then reused only when the source's content SHA-256 is the one recorded with
them, not only its fingerprint (:mod:`..source_content`). It is a recorded input: a run not
asking keeps the earlier run's choice, and turning it on or off recomputes every row.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class RunMode:
    """A run mode with its reuse rule, for ``run_experiment(mode=...)``.

    Attributes
    ----------
    name
        ``"restart"`` or ``"full"``.
    verify_content
        True: record each source's content SHA-256 and reuse a row only for the same
        content; False: the fingerprint only (the default); None: as the earlier run
        recorded (False without one).
    """

    name: str = RESTART
    verify_content: bool | None = None


def run_mode(mode: object) -> RunMode:
    """Return ``mode`` as a :class:`RunMode`: a mode's name, or a :class:`RunMode`.

    Parameters
    ----------
    mode
        ``"restart"``, ``"full"`` or a :class:`RunMode`.

    Returns
    -------
    RunMode
        With its name checked (:func:`check_mode`).

    Raises
    ------
    ValueError
        For an unknown mode, or a ``verify_content`` that is not True, False or None.
    """
    if not isinstance(mode, RunMode):
        return RunMode(check_mode(mode))
    flag = mode.verify_content
    if flag is not None and not isinstance(flag, bool):
        raise ValueError(f"verify_content must be True, False or None; found {flag!r}")
    return RunMode(check_mode(mode.name), mode.verify_content)
