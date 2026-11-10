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

A :class:`RunMode` also carries the lab-tool links of the run (:mod:`..lab_links`, PP-2):
provenance recorded only in the run manifest, which changes no row, check or other output and
is not taken over by the next run.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..lab_links import LabLinks

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
    """A run mode, its reuse rule and the run's lab-tool links, for ``run_experiment(mode=)``.

    Attributes
    ----------
    name
        ``"restart"`` or ``"full"``.
    verify_content
        True: record each source's content SHA-256 and reuse a row only for the same
        content; False: the fingerprint only (an explicit opt-out, recorded in the manifest's
        ``settings.verify_content``); None (the default): as the earlier run recorded, True
        without one (#159).
    lab_links
        The lab-tool links the user gave this run (:class:`..lab_links.LabLinks`), recorded
        verbatim in the run manifest only; None: none (an earlier run's are not taken over).
    """

    name: str = RESTART
    verify_content: bool | None = None
    lab_links: LabLinks | None = None


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
        For an unknown mode, a ``verify_content`` that is not True, False or None, or
        ``lab_links`` that are not :class:`..lab_links.LabLinks` or None.
    """
    if not isinstance(mode, RunMode):
        return RunMode(check_mode(mode))
    flag = mode.verify_content
    if flag is not None and not isinstance(flag, bool):
        raise ValueError(f"verify_content must be True, False or None; found {flag!r}")
    links = mode.lab_links
    if links is not None and not isinstance(links, LabLinks):
        raise ValueError(f"lab_links must be LabLinks or None; found {type(links).__name__}")
    return RunMode(check_mode(mode.name), mode.verify_content, links)
