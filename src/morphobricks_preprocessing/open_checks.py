"""The phases of opening a run, told to a progress callback (owner Colab test, 26 Nov 2026).

Opening a run (:func:`.run_inputs.open_checked`, :meth:`.run_inputs.RunInputs.open`) checks
every image and mask against the manifest before anything is used: on Google Drive, with a
``verify_content`` run of 200 images of 10-50 MB each, that took minutes with nothing on
screen (the Cellpose step **Folders**, issue #574). A caller that shows it passes
``checking(phase, done, total, path)``, the signature of the run's own checks
(:class:`.run.modes.RunMode` ``checking``, #567), told each phase of :data:`OPEN_PHASES`.
It changes nothing: the same files are checked, in the same order, with the same result.
Nothing here reads a file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from operator import attrgetter
from typing import TypeVar

Checking = Callable[[str, int, int, str], None]
"""``checking(phase, done, total, path)``: a phase of :data:`OPEN_PHASES`, ``done`` of
``total`` items (0 of 0 for a phase that is not counted) and the item now checked."""

OPEN_PHASES = ("run", "images", "contents", "masks", "unrecorded")
"""In order: the manifest, table and description; each image's fingerprint (size and
modification time); each image's content SHA-256 (``verify_content`` runs only: every image
read whole); each mask's fingerprint; the folders listed for files added since the run."""

RELATIVE = attrgetter("relative_path")
"""The relative path of an image's manifest entry, as a progress line shows it."""

T = TypeVar("T")


def quiet(phase: str, done: int, total: int, path: str) -> None:
    """Tell nothing: the ``checking`` of a caller that shows no progress."""


def counted(
    checking: Checking, phase: str, items: Iterable[T], path: Callable[[T], str]
) -> Iterator[T]:
    """Yield ``items`` in order, telling ``checking`` before each one and once at the end.

    Parameters
    ----------
    checking
        The progress callback.
    phase
        The phase of :data:`OPEN_PHASES` these items are checked in.
    items
        What is checked, one after another.
    path
        The item's relative path, as shown.

    Yields
    ------
    T
        Each item, after ``checking(phase, done, total, path(item))``; then
        ``checking(phase, total, total, "")``.
    """
    items = list(items)
    total = len(items)
    for done, item in enumerate(items):
        checking(phase, done, total, path(item))
        yield item
    checking(phase, total, total, "")


__all__ = ["OPEN_PHASES", "RELATIVE", "Checking", "counted", "quiet"]
