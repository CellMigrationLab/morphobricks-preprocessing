"""The wait shown while the Cellpose step **Folders** opens a run (owner Colab test, 26 Nov 2026).

Opening a run checks every image against the manifest (:mod:`.open_checks`); on Google Drive,
with a run that verified content, every image is read whole for its SHA-256, which took about
4 minutes on 200 images with nothing on screen (issue #574). :func:`opening` shows that wait
with the run's own progress line (:class:`.notebook_progress.Wait`, #567): the phase, "n of N"
with the image being read, and an elapsed time that ticks in a thread while nothing is
reported. Nothing here reads a file or decides anything.
"""

from __future__ import annotations

import html
from collections.abc import Iterator
from contextlib import contextmanager

import ipywidgets as widgets

from .notebook_progress import Wait
from .open_checks import Checking

OPENING = {
    "run": "Opening the run: reading its manifest, table and description",
    "images": "Checking that each image is the one the run read (size and date)",
    "contents": "Reading each image whole for its content SHA-256",
    "masks": "Checking that each mask is the one the run read (size and date)",
    "unrecorded": "Listing the folders for images added since the run",
    "viewer": "Reading the first image for the viewer",
}
"""What the wait says in each phase (:data:`.open_checks.OPEN_PHASES`, then ``viewer``)."""
COUNTED = ("images", "contents", "masks")
"""The phases shown as done of total, each with its file."""
TOOK = "Opened and checked in {took}."
"""The line left once the wait ends (``{took}``: ``m:ss``), refused or not."""


@contextmanager
def opening(target: widgets.HTML) -> Iterator[Checking]:
    """Show the wait of opening a run in ``target`` while the block runs.

    Parameters
    ----------
    target
        The HTML widget the line is drawn in; it is left saying how long it took.

    Yields
    ------
    Checking
        The ``checking(phase, done, total, path)`` to pass to the opening
        (:func:`.segmentation_cellpose_run.open_run`): each phase of :data:`OPENING` is
        named, the counted ones as done of total with the file read now.
    """
    wait = Wait(target)
    wait.start(OPENING["run"])

    def checking(phase: str, done: int, total: int, path: str) -> None:
        label = OPENING[phase]
        if wait.label != label:
            wait.phase(label, total if phase in COUNTED else 0)
        if phase in COUNTED:
            wait.step(done, total, path)

    try:
        with wait.ticking():
            yield checking
    finally:
        target.value = f"<div class='mb-help'>{html.escape(TOOK.format(took=wait.took()))}</div>"


__all__ = ["COUNTED", "OPENING", "TOOK", "opening"]
