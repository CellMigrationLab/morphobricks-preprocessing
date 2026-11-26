"""The wait shown while cell 5 registers the masks (owner Colab test, 26 Nov 2026, #574 item 7).

**Register the masks** runs, one after another, the checks of the run cell 4 segmented and the
app's own steps: its folder check with the masks folder, its *Check and save*, its run and its
*Describe the masks*. In a ``verify_content`` run (the notebook's default) the run's checks read
every image whole for its SHA-256, the app's run reads every image and mask whole again for
theirs, and *Describe the masks* reads every mask; the app draws each wait in its own step, in
cell 3, far above cell 5, which showed nothing. :class:`RegisterWait` shows them in cell 5 with
the waits of #567 (:class:`.notebook_progress.Wait` and its ticking thread): one line
"Step k of N · <step>" whose elapsed time goes on, and under it the step's own line: the run's
checks, counted here ("n of N" with the image read now), and the app's own wait line of the
folder check, the run and the mask step, copied as the app draws it. Nothing here reads a file
or decides anything.
"""

from __future__ import annotations

import html
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import ipywidgets as widgets

from .notebook_progress import Wait

STEPS = {
    "run": "Checking the run cell 4 segmented and its images",
    "folders": "Checking the folders again with the masks folder",
    "save": "Answering each mask and saving the description",
    "rerun": "Running again (the histograms of unchanged images are reused)",
    "describe": "Describing the masks",
}
"""The steps of **Register the masks**, in order; the folder check is made only when needed."""
CHECKS = {
    "run": "Reading the run's manifest, table and description",
    "images": "Checking that each image is the one the run read (size and date)",
    "contents": "Reading each image whole for its content SHA-256",
    "masks": "Checking that each mask is the one the run read (size and date)",
    "unrecorded": "Listing the folders for files added since the run",
}
"""What the step line says in each phase of the run's checks (:data:`.open_checks.OPEN_PHASES`)."""
COUNTED = ("images", "contents", "masks")
"""The phases of the run's checks shown as done of total, each with its file."""
TOOK = "Took {took}."
"""The line left once **Register the masks** ends, registered or refused (``{took}``: ``m:ss``)."""
STOPPED_AFTER = "Stopped after {took}."
"""The line left when an error or an interrupt went through it."""


def step_label(key: str) -> str:
    """Return the line of step ``key`` of :data:`STEPS`: ``"Step 2 of 5 · <step>"``."""
    number = list(STEPS).index(key) + 1
    return f"Step {number} of {len(STEPS)} · {STEPS[key]}"


class RegisterWait:
    """The wait of **Register the masks**: the step line, and the step's own line under it.

    Parameters
    ----------
    line
        The HTML widget of the step line (elapsed time of the whole press, ticking).
    detail
        The HTML widget of the step's own line (the run's checks, or the app's wait).
    clock
        Returns the time in seconds (monotonic); replaced in tests.
    """

    def __init__(
        self,
        line: widgets.HTML,
        detail: widgets.HTML,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.line, self.detail = line, detail
        self.wait = Wait(line, bar=False, clock=clock)
        self.checks = Wait(detail, clock=clock)

    @contextmanager
    def running(self) -> Iterator[RegisterWait]:
        """Show the wait while the block runs; then leave how long it took (or that it stopped)."""
        self.wait.start(step_label("run"))
        text = STOPPED_AFTER
        try:
            with self.wait.ticking():
                yield self
            text = TOOK
        finally:
            self.detail.value = ""
            took = html.escape(text.format(took=self.wait.took()))
            self.line.value = f"<div class='mb-help'>{took}</div>"

    @contextmanager
    def step(self, key: str, shown: widgets.HTML | None = None) -> Iterator[None]:
        """Name step ``key`` while the block runs; copy ``shown`` (the app's wait) under it.

        ``shown`` is the app's own status line of that step: each value the app draws there
        while the block runs is drawn under the step line too, then the copy stops and the
        line under it is emptied.
        """
        if self.wait.label != step_label(key):
            self.wait.phase(step_label(key))
        self.detail.value = ""

        def copy(change: dict) -> None:
            self.detail.value = change["new"]

        if shown is not None:
            shown.observe(copy, names="value")
        try:
            yield
        finally:
            if shown is not None:
                shown.unobserve(copy, names="value")
            self.detail.value = ""

    def checking(self, phase: str, done: int, total: int, path: str) -> None:
        """Show the run's checks (``checking`` of :func:`.segmentation_cellpose_run.open_run`)."""
        label = CHECKS[phase]
        counted = phase in COUNTED
        if self.checks.label != label:
            if phase == "run" or not self.checks.label:  # the first phase starts the clock
                self.checks.start(label, total if counted else 0)
            else:
                self.checks.phase(label, total if counted else 0)
        if counted:
            self.checks.step(done, total, path)


__all__ = ["CHECKS", "COUNTED", "STEPS", "STOPPED_AFTER", "TOOK", "RegisterWait", "step_label"]
