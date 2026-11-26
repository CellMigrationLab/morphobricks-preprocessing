"""The **Stop** button of the long steps: Run (with its checks before the first image) and cell 5.

Owner request (26 November 2026): a long step can be stopped cleanly, with nothing half
published, the run left resumable, and one line "Stopped after m:ss; run again to continue".

**Why the work runs in a thread.** A widget's click is a message to the kernel, handled only
when the kernel is idle: while a button's callback runs, a click of **Stop** waits in the queue
until the work is over (Jupyter, JupyterLab and Colab alike). So a step that can be stopped
runs its work in one background thread, and its button's callback returns at once; the kernel
then handles the **Stop** click, which only sets a flag (:class:`Stopper`). The work checks the
flag between images (and between the run's checks of each header and content SHA-256) and
raises :class:`StopPressed`, a ``KeyboardInterrupt``: every path that keeps an interrupted run
crash-consistent already handles it (the staged rows of the images read so far are kept, the
earlier publication is untouched, the output lock is released), so pressing the button again
resumes. While the work runs, the app's other buttons and boxes are disabled, so nothing else
changes its state; they come back as they were when it ends. The widgets are updated from the
thread, as the ticking time of #567 already is.

Outside a notebook kernel (tests, scripts), and for a programmatic call such as cell 5's
``app.run()``, the work runs in the calling thread, as before.

Cellpose segmentation (cell 4's **Segment**) is not covered: it displays its tables through
``display`` into an ``Output`` widget, which is not reliable from a thread; interrupting the
kernel (Colab: Runtime > Interrupt execution) still stops it between images, and its batch
already rolls back a mask being written (:func:`.segmentation_cellpose.run_batch`).
"""

from __future__ import annotations

import html
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import ipywidgets as widgets
from IPython import get_ipython

from . import notebook_style as ui

logger = logging.getLogger(__name__)

STOP_LABEL = " Stop"
STOPPING = " Stopping after this image…"
STOPPED = "Stopped after {took}; run again to continue."
"""The line left by a step the user stopped (``{took}``: ``m:ss``)."""


class StopPressed(KeyboardInterrupt):  # noqa: N818 (an interrupt, named for what it is)
    """The user pressed **Stop**: raised between images, handled as an interrupt is."""


def _seconds(took: float) -> str:
    """Return ``m:ss``."""
    minutes, seconds = divmod(int(took), 60)
    return f"{minutes}:{seconds:02d}"


def in_kernel() -> bool:
    """Return whether a notebook kernel handles widget clicks (then the work runs in a thread)."""
    shell = get_ipython()
    return shell is not None and getattr(shell, "kernel", None) is not None


class Stopper:
    """A **Stop** button and its flag, for one long step at a time.

    Attributes
    ----------
    button
        The button, shown only while a step that can be stopped runs.
    running
        The thread of the step running now, or None.
    """

    def __init__(self) -> None:
        self.button = ui.button(
            STOP_LABEL,
            style="danger",
            icon="stop",
            tooltip=(
                "Stop after the image being read: nothing is half written, and pressing the step's "
                "button again continues where it stopped"
            ),
        )
        self.button.on_click(lambda _: self.request())
        ui.set_visible(self.button, False)
        self._flag = threading.Event()
        self.running: threading.Thread | None = None
        self.started = 0.0

    @property
    def requested(self) -> bool:
        """True once **Stop** was pressed for the step running now."""
        return self._flag.is_set()

    def request(self) -> None:
        """Ask the step running now to stop after the image being read (the click)."""
        if self.running is None and not ui.is_visible(self.button):
            return
        self._flag.set()
        self.button.disabled = True
        self.button.description = STOPPING

    def check(self) -> None:
        """Raise :class:`StopPressed` when **Stop** was pressed; called between images."""
        if self._flag.is_set():
            raise StopPressed()

    @contextmanager
    def armed(self) -> Iterator[None]:
        """Show the button and clear the flag while the block runs; hide it afterwards."""
        self._flag.clear()
        self.started = time.monotonic()
        self.button.disabled, self.button.description = False, STOP_LABEL
        ui.set_visible(self.button, True)
        try:
            yield
        finally:
            ui.set_visible(self.button, False)

    def stopped_line(self) -> str:
        """Return the line left by a stopped step: how long it ran, and what to do."""
        took = _seconds(time.monotonic() - self.started)
        return f"<div class='mb-help'>{html.escape(STOPPED.format(took=took))}</div>"

    def start(
        self,
        work: Callable[[], object],
        on_stopped: Callable[[], object],
        *,
        background: bool | None = None,
    ) -> None:
        """Run ``work`` armed: in a thread in a kernel (so the click is handled), else here.

        ``on_stopped`` is called when **Stop** ended it (:class:`StopPressed`). In a thread, an
        error ``work`` did not show itself is logged (the step's own handlers show theirs).
        """
        if background is None:
            background = in_kernel()

        def body() -> None:
            try:
                with self.armed():
                    work()
            except StopPressed:
                on_stopped()
            except BaseException:
                if not background:
                    raise
                logger.exception("The step stopped on an error")
            finally:
                self.running = None

        if not background:
            body()
            return

        self.running = threading.Thread(target=body, name="morphobricks-step", daemon=True)
        self.running.start()


@contextmanager
def frozen(root: widgets.Widget, keep: tuple[widgets.Widget, ...] = ()) -> Iterator[None]:
    """Disable every button and input under ``root`` but ``keep`` while the block runs.

    A step running in a thread leaves the kernel free, so nothing else may change the app's
    state meanwhile; each one gets back the ``disabled`` it had.
    """
    found: list[widgets.Widget] = []

    def walk(widget: widgets.Widget) -> None:
        if widget in keep:
            return
        if (
            hasattr(widget, "disabled")
            and not widget.disabled
            and not isinstance(widget, widgets.HTML)
        ):
            found.append(widget)
        for child in getattr(widget, "children", ()):
            walk(child)

    walk(root)
    for widget in found:
        widget.disabled = True
    try:
        yield
    finally:
        for widget in found:
            widget.disabled = False


class _StopSteps:
    """The app's **Stop**: Run, with its checks before the first image, in a thread (v0.9.1).

    Mixed into the Preprocessing app before its Run step: its progress callbacks check the flag
    between images (and between the run's checks), and *Compute histograms* starts the run
    through :meth:`Stopper.start`. ``run()`` itself stays a plain call (cell 5 calls it).
    """

    def _build_stop(self) -> None:
        """Build the app's one :class:`Stopper` (cell 5 shows its button too)."""
        self.stopper = Stopper()

    def _freeze_root(self) -> widgets.Widget | None:
        """Return the app's widget whose inputs are disabled while a step runs, or None."""
        stepper = getattr(self, "_stepper", None)
        return None if stepper is None else stepper.widget

    @contextmanager
    def _frozen_app(self, *also: widgets.Widget) -> Iterator[None]:
        """Disable the app's inputs (and ``also``'s) but **Stop** while the block runs."""
        roots = [w for w in (self._freeze_root(), *also) if w is not None]
        try:
            with frozen_all(roots, keep=(self.stopper.button,)):
                yield
        finally:
            self._refresh_run_step()  # the states the run left, over the ones put back
            if getattr(self, "_stepper", None) is not None:
                self._refresh_steps()

    def _run_pressed(self) -> None:
        """*Compute histograms* pressed: the run, which **Stop** can end between images."""
        self.stopper.start(self._run_frozen, self._run_stopped)

    def _run_frozen(self) -> None:
        """Run with the app's inputs disabled."""
        with self._frozen_app():
            self.run()

    def _run_stopped(self) -> None:
        """Say that the run was stopped, how long it ran, and how to continue."""
        self.progress.bar_style = "warning"
        self.run_status.value = self.stopper.stopped_line()
        self.run_next.value = ""

    def _progress(self, done: int, total: int, path: str) -> None:
        self.stopper.check()  # between images: the staged rows of those read are kept
        super()._progress(done, total, path)

    def _checking(self, phase: str, done: int, total: int, path: str) -> None:
        self.stopper.check()  # the run's checks before its first image: nothing written yet
        super()._checking(phase, done, total, path)


@contextmanager
def frozen_all(roots: list[widgets.Widget], keep: tuple[widgets.Widget, ...]) -> Iterator[None]:
    """Apply :func:`frozen` to each of ``roots`` while the block runs."""
    if not roots:
        yield
        return
    with frozen(roots[0], keep), frozen_all(roots[1:], keep):
        yield


__all__ = ["STOPPED", "StopPressed", "Stopper", "_StopSteps", "frozen", "frozen_all", "in_kernel"]
