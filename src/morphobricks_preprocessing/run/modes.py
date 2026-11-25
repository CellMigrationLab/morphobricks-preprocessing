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

And it says how many images the run reads at once (``workers``, ``max_ram``; issue #107,
:mod:`.workers`): operational only, in no row key, output file or recorded input.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from ..lab_links import LabLinks

RESTART = "restart"
"""Reuse the rows of unchanged files; process new or changed ones (the default)."""

FULL = "full"
"""Recompute every row and every check; reuse no earlier row."""

MODES = (RESTART, FULL)

AUTO = "auto"
"""``RunMode(workers="auto")``: as many workers as the CPUs and ``max_ram`` allow."""
MAX_RAM_ONLY_WITH_AUTO = "max_ram is used only with workers='auto'"
"""The refusal of ``max_ram`` with a whole number of workers (plan §12 item 82)."""


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
    """A run mode, its reuse rule, lab-tool links and workers, for ``run_experiment(mode=)``.

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
    workers
        How many images are read at once, each whole in its own worker thread (issue #107,
        :mod:`.workers`): 1 (the default) one after another in the calling thread; a whole
        number; or ``"auto"``: at most the CPUs this process may use and, with ``max_ram``,
        as many as the largest image's working-memory estimate fits. The outputs are byte
        for byte the same with any value; it is not recorded.
    max_ram
        With ``workers="auto"``, the bytes of memory the run may use; None: no RAM cap.
    checking
        Called while the run checks the folder and every image header again, before any
        image is read, as :func:`.preflight.preflight`'s ``progress``:
        ``(phase, done, total, path)``, a phase of :data:`.phases.PHASES`, once per header.
        On a large experiment this check takes minutes (owner test of v0.9.0). Operational
        only, like ``workers``: not recorded, not compared, and the outputs are the same.
    """

    name: str = RESTART
    verify_content: bool | None = None
    lab_links: LabLinks | None = None
    workers: int | Literal["auto"] = 1
    max_ram: int | None = None
    checking: Callable[[str, int, int, str], None] | None = field(default=None, compare=False)


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
        For an unknown mode, a ``verify_content`` that is not True, False or None,
        ``lab_links`` that are not :class:`..lab_links.LabLinks` or None, or ``workers`` and
        ``max_ram`` that :func:`check_workers` refuses.
    """
    if not isinstance(mode, RunMode):
        return RunMode(check_mode(mode))
    flag = mode.verify_content
    if flag is not None and not isinstance(flag, bool):
        raise ValueError(f"verify_content must be True, False or None; found {flag!r}")
    links = mode.lab_links
    if links is not None and not isinstance(links, LabLinks):
        raise ValueError(f"lab_links must be LabLinks or None; found {type(links).__name__}")
    check_workers(mode.workers, mode.max_ram)
    if mode.checking is not None and not callable(mode.checking):
        raise ValueError(f"checking must be callable or None; found {mode.checking!r}")
    return RunMode(
        check_mode(mode.name),
        mode.verify_content,
        links,
        mode.workers,
        mode.max_ram,
        mode.checking,
    )


def check_workers(workers: object, max_ram: object) -> None:
    """Refuse ``workers`` and ``max_ram`` that are not allowed (issue #107).

    Parameters
    ----------
    workers
        A whole number of at least 1 (not a bool), or ``"auto"``.
    max_ram
        None, or with ``workers="auto"`` a whole number of bytes above 0.

    Raises
    ------
    ValueError
        For any other ``workers``, a ``max_ram`` that is not a positive whole number, or a
        ``max_ram`` without ``workers="auto"``.
    """
    if workers != AUTO and (
        isinstance(workers, bool) or not isinstance(workers, int) or workers < 1
    ):
        raise ValueError(f"workers must be a whole number of at least 1 or 'auto', not {workers!r}")
    if max_ram is not None:
        if isinstance(max_ram, bool) or not isinstance(max_ram, int) or max_ram < 1:
            raise ValueError(f"max_ram must be a whole number of bytes above 0, not {max_ram!r}")
        if workers != AUTO:
            raise ValueError(MAX_RAM_ONLY_WITH_AUTO)
