"""The four phases of the folder check and how their progress is told (Stage 1 design, card 2).

:func:`.preflight` reports ``progress(phase, done, total, path)`` while it works, and
:func:`progress_text` words it as the notebook and the command line show it:
``"1/4 folders"``, ``"2/4 headers 120/480"``, ``"3/4 masks"``, ``"4/4 acquisitions"``.
"""

from __future__ import annotations

PHASES = ("folders", "headers", "masks", "acquisitions")
"""The phases of the check, in order: the folder walk, one header per image, the masks found
and paired, and the acquisitions (calibration, dataset consistency, axis suggestion)."""

COUNTED = "headers"
"""The phase that counts its work (one header per image); the others are one step each."""


def progress_text(phase: str, done: int, total: int) -> str:
    """Return the progress line of ``phase`` (``"2/4 headers 120/480"``).

    Parameters
    ----------
    phase
        One of :data:`PHASES`.
    done, total
        Work done and to do in that phase; shown only for the counted phase (headers).

    Raises
    ------
    ValueError
        For a phase that is not in :data:`PHASES`.
    """
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; the phases are {', '.join(PHASES)}")
    text = f"{PHASES.index(phase) + 1}/{len(PHASES)} {phase}"
    return f"{text} {done}/{total}" if phase == COUNTED else text
