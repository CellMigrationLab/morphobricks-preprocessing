"""What the notebook shows while a model loads: the download's progress, no Cellpose log noise.

Owner decision (issue #574): choosing a model and pressing **Preview** or **Segment** (or
**Load the model**) is the consent to download that model's weights, so there is no tick to
allow it; the download is shown instead. :class:`DownloadLine` is the ``progress`` given to
:func:`.segmentation_cellpose_model.load_model`: the first time it is told something it
appears, as one line "Downloading <model> weights…" with the megabytes received of the total,
a bar and the elapsed time (:class:`.notebook_progress.Wait`), or "Installing DINOv3…" for a
``cpdino`` model; it says at the end how long it took, or that it stopped. A model already on
this computer shows nothing.

Cellpose itself never downloads here: the model is handed to it as the absolute path of the
checked file (:mod:`.segmentation_cellpose_weights`). While it loads, :func:`quiet_cellpose`
drops the ``cellpose`` loggers' INFO lines and collects their warnings, to be shown in one card
afterwards (never lost). Everything is put back when the load ends, also when it fails.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import ipywidgets as widgets

from .notebook_progress import Wait
from .notebook_style import status

LOGGER = "cellpose"
"""The parent of every Cellpose logger (``cellpose.models``, ``cellpose.core``, ...)."""
CPDINO_IMPORT = "Could not import CPDINO"
"""How Cellpose 4.2.1.1's ``cellpose.models`` starts the warning it logs at import when DINOv3
is not importable; the warning goes on to recommend ``pip install git+.../dinov3`` unpinned."""
CPDINO_NOTE = (
    "Cellpose could not import DINOv3. Only the cpdino models need it: choosing one and pressing "
    "the button installs the pinned commit, and the runtime then needs a restart if Cellpose was "
    "already imported. The other models do not use it."
)
"""What is shown instead: the notebook's pinned installation (``ensure_dinov3``), never an
unpinned one."""
MEGABYTE = 1 << 20
"""The unit the download's line counts in (MB, as 2**20 bytes)."""


class _Collect(logging.Handler):
    """Keep the text of every warning or error logged while it is attached."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if text.startswith(CPDINO_IMPORT):
            text = CPDINO_NOTE  # never an unpinned install: the notebook's own instead
        self.lines.append(f"{record.levelname.title()}: {text}")


@contextmanager
def quiet_cellpose() -> Iterator[list[str]]:
    """Drop Cellpose's INFO lines and collect its warnings while a model loads.

    Yields
    ------
    list of str
        The warnings Cellpose logged during the block (``"Warning: ..."``), filled as it runs.
    """
    logger = logging.getLogger(LOGGER)
    handler, propagate = _Collect(), logger.propagate
    logger.addHandler(handler)  # before the load imports Cellpose: importing it may log
    logger.propagate = False
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate


class DownloadLine:
    """The ``progress(label, done, total)`` of a load, drawn as one wait line once called.

    Parameters
    ----------
    show
        Called once with :attr:`widget`, the first time there is something to show.
    clock
        Returns the time in seconds (monotonic), for the wait; replaced in tests.

    Attributes
    ----------
    widget
        The line (HTML).
    labels
        Every label shown, in order (``"Downloading cpsam_v2 weights…"``, ...).
    """

    def __init__(
        self, show: Callable[[widgets.Widget], object], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.widget = widgets.HTML()
        self.wait = Wait(self.widget, clock=clock)
        self.labels: list[str] = []
        self._show = show

    def __call__(self, label: str, done: int, total: int) -> None:
        """Show ``done`` of ``total`` bytes under ``label`` (a new label starts a new line)."""
        first = not self.labels
        if first or self.labels[-1] != label:
            self.labels.append(label)
            self.wait.start(label, total // MEGABYTE)
        self.wait.step(done // MEGABYTE, total // MEGABYTE, "MB")
        if first:  # drawn first, so that what is shown says what is happening
            self._show(self.widget)

    def finish(self, ok: bool) -> None:
        """Replace the line by how long it took, or by where it stopped (if it was shown)."""
        if not self.labels:
            return
        label = self.labels[-1].rstrip("…")
        if ok:
            self.widget.value = status("ok", f"{label}: done in {self.wait.took()}")
        else:
            self.widget.value = status("error", f"{label}: stopped after {self.wait.took()}")


__all__ = [
    "CPDINO_IMPORT",
    "CPDINO_NOTE",
    "LOGGER",
    "MEGABYTE",
    "DownloadLine",
    "quiet_cellpose",
]
