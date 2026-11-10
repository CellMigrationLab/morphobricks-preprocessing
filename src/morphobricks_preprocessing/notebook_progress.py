"""Progress of a wait in the notebook: what is done of the total, elapsed time, time left.

Every wait that can take more than about a second shows one (owner test of v0.5.0, 10 November
2026): the folder walk, the image headers, the mask headers, the pictures, the save, the run,
the mask step and the group statistics. A :class:`Wait` is started with a label, told
``step(done, total, item)`` by the reader's progress callback, and renders one status line
with a bar. The time left is said only once it can be measured: after at least one item and
one second (:func:`time_left`); before that, and for work that cannot be counted, only the
elapsed time is shown. Nothing here reads a file or decides anything.
"""

from __future__ import annotations

import html
import time
from collections.abc import Callable

import ipywidgets as widgets

MEASURABLE_SECONDS = 1.0
"""No estimate of the time left before this much time was spent on at least one item."""
MIN_INTERVAL = 0.1
"""Seconds between two redraws of a wait (the first and the last are always drawn)."""


def clock_text(seconds: float) -> str:
    """Return a duration as ``m:ss`` (``h:mm:ss`` from an hour on)."""
    whole = max(int(round(seconds)), 0)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def time_left(done: int, total: int, elapsed: float) -> float | None:
    """Return the estimated seconds left, or None while it cannot be measured.

    The estimate is the mean time per item done so far times the items left. It needs at
    least one item done, :data:`MEASURABLE_SECONDS` elapsed and a known total.
    """
    if total <= 0 or done <= 0 or done > total or elapsed < MEASURABLE_SECONDS:
        return None
    return elapsed / done * (total - done)


def progress_html(
    label: str,
    done: int,
    total: int,
    item: str,
    elapsed: float,
    *,
    bar: bool = True,
    measured: float | None = None,
) -> str:
    """Return the status line of a wait (HTML): label, count, item, elapsed and time left.

    Parameters
    ----------
    label
        What is being done, e.g. ``"Reading image headers"``.
    done, total
        Items done and to do; a ``total`` of 0 means the work cannot be counted (``done``
        is then shown alone when it is not 0, e.g. folders listed so far).
    item
        The item being read now (a relative path), or an empty string.
    elapsed
        Seconds since the wait started.
    bar
        False leaves the bar out, for a wait that already has a bar widget.
    measured
        Seconds spent on these ``done`` items, when the wait had other parts before them
        (the estimate of the time left uses it); None means ``elapsed``.
    """
    parts = [f"<b>{html.escape(label)}</b>"]
    if total > 0:
        parts.append(f"{min(done, total):,} of {total:,}")
    elif done > 0:
        parts.append(f"{done:,} so far")
    if item:
        parts.append(html.escape(item))
    times = [f"{clock_text(elapsed)} elapsed"]
    left = time_left(done, total, elapsed if measured is None else measured)
    if left is not None:
        times.append(f"about {clock_text(left)} left")
    head = (
        f"<div class='mb-wait-head'><span>{' · '.join(parts)}</span>"
        f"<span class='mb-wait-time'>{' · '.join(times)}</span></div>"
    )
    if not bar:
        return f"<div class='mb-status mb-info mb-wait'>{head}</div>"
    if total > 0:
        fill = (
            f"<div class='mb-bar-fill' style='width:{100 * min(done, total) / total:.1f}%'></div>"
        )
    else:
        fill = "<div class='mb-bar-fill mb-bar-unknown'></div>"
    return f"<div class='mb-status mb-info mb-wait'>{head}<div class='mb-bar'>{fill}</div></div>"


class Wait:
    """One wait shown in an HTML widget: started, stepped by a progress callback, finished.

    Parameters
    ----------
    target
        The HTML widget the line is drawn in (a step's status line, usually).
    bar
        False for a wait that has its own bar widget beside the line.
    clock
        Returns the time in seconds (monotonic); replaced in tests.

    Attributes
    ----------
    drawn
        How many times the line was drawn since :meth:`start` (tests, and the throttle).
    """

    def __init__(
        self,
        target: widgets.HTML,
        *,
        bar: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.target = target
        self.bar = bar
        self.clock = clock
        self.label = ""
        self.drawn = 0
        self._started = self._phase_started = self._last = 0.0
        self._state: tuple[int, int, str] = (0, 0, "")

    @property
    def elapsed(self) -> float:
        """Seconds since :meth:`start`."""
        return self.clock() - self._started

    def start(self, label: str, total: int = 0) -> None:
        """Start the wait: the clock is set and the line drawn with nothing done."""
        self.label = label
        self._started = self._phase_started = self.clock()
        self.drawn = 0
        self._draw(0, total, "")

    def phase(self, label: str, total: int = 0, done: int = 0) -> None:
        """Name the next part of the same wait; the elapsed time goes on.

        ``done`` of ``total`` says which part this is when the parts themselves are counted.
        """
        self.label = label
        self._phase_started = self.clock()
        self._draw(done, total, "")

    def step(self, done: int, total: int = 0, item: str = "") -> None:
        """Report progress; redrawn at most every :data:`MIN_INTERVAL` seconds, and at the end."""
        self._state = (done, total, item)
        last = total > 0 and done >= total
        if last or self.clock() - self._last >= MIN_INTERVAL:
            self._draw(done, total, item)

    def _draw(self, done: int, total: int, item: str) -> None:
        self._state = (done, total, item)
        self._last = self.clock()
        self.drawn += 1
        self.target.value = self.html(done, total, item)

    def html(self, done: int, total: int, item: str) -> str:
        """Return the line as it would be drawn now.

        The time left is estimated from the current part's own items (a part starts with
        :meth:`start` or :meth:`phase`); the elapsed time shown is that of the whole wait.
        """
        now = self.clock()
        return progress_html(
            self.label,
            done,
            total,
            item,
            now - self._started,
            bar=self.bar,
            measured=now - self._phase_started,
        )

    def took(self) -> str:
        """Return how long the wait took, for the message that replaces it (``"0:12"``)."""
        return clock_text(self.elapsed)


__all__ = ["MEASURABLE_SECONDS", "MIN_INTERVAL", "Wait", "clock_text", "progress_html", "time_left"]
