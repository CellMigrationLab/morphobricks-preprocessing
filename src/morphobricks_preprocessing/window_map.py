"""Map a function over windows of one image in threads, results in window order.

The processing-agnostic concurrency of ``docs/design/lazy-windows.md`` §5.2 (b), with (a) as
its special case: a consumer gives a reader of one image, the windows it wants (plane-frame
bounds, Spec ADR 0004 EW1/EW2; their geometry is the consumer's, for example CellPatcher's grid
and ``context_bounds``, EW5), the channels and a function, and gets the function's results in
the order of the windows, whatever thread finished first. Nothing here knows what the function
computes.

Two steps, so that memory is checked before any pixel is read:

1. :func:`map_plan` works out from the headers what each window read costs and what the run
   holds at most: ``max_in_flight`` windows at once (read, being read or computed, or done and
   waiting for their turn) plus the reader's cache (:class:`MapPlan`). Nothing is read but the
   headers.
2. :func:`map_windows` (or :func:`iter_windows`, which yields the windows themselves) runs the
   plan in ``workers`` threads after checking it against the consumer's ``memory_budget``; a plan
   over it is refused (:class:`.windows.WindowMemoryError`) before any pixel is read. The
   number of workers is never lowered to fit.

Rules (design note §5.3):

- **Order.** Results come in the order of the windows of the plan, so they are identical for any
  number of workers.
- **Memory.** At most ``max_in_flight`` windows are in flight; the one last delivered counts
  until the next is asked. The plan's ``memory_estimate`` is the upper bound of those windows'
  read memory (``window_plan``) plus the cache. What ``fn`` allocates and the results the
  consumer keeps are the consumer's to add.
- **Errors and early stop.** The first error in window order is re-raised, its type unchanged,
  with a note naming the window; results of the windows before it are delivered first. Then,
  as when the consumer stops early (``break``, :meth:`WindowMap.close`, leaving its ``with``
  block), windows not started are cancelled and running ones waited for; no thread is left
  running. The reader stays the caller's: it is never closed here.
- **Identity.** ``workers`` and ``max_in_flight`` are recorded in the plan and the summary
  (:class:`MapSummary`) for the consumer's record; they never change a result.
- **No fallback.** An invalid worker count is refused; threads are never replaced by processes,
  a failed window is never read again, and nothing is retried.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Generic, TypeVar

import numpy as np

from .planes import format_bytes
from .run_inputs_window import RunWindowReader
from .window_reader import WindowReader
from .windows import Bounds, WindowMemoryError, WindowPlan

R = TypeVar("R")

THREAD_PREFIX = "map_windows"
"""The name prefix of the threads :func:`map_windows` runs (tests look for leftovers)."""


@dataclass(frozen=True)
class MapPlan:
    """What mapping a function over windows of one image holds at most, from the headers.

    Attributes
    ----------
    windows
        The windows' ``(y0, y1, x0, x1)`` bounds, checked, in the order results are given.
    channels
        The channels every window reads (C indices for a ``WindowReader``, canonical IDs for
        a ``RunWindowReader``), in the order of each window's first axis.
    t, z
        The plane read.
    workers
        Threads that read windows and call the function.
    max_in_flight
        Windows in flight at most (read, being read or computed, or done and waiting).
    cache_bytes
        The reader's cache bound, held once whatever the workers.
    window_bytes
        The largest working memory of one window read, cache excluded (``window_plan``).
    in_flight_bytes
        The ``max_in_flight`` largest window working memories added together.
    memory_estimate
        ``in_flight_bytes + cache_bytes``: an upper bound of the windows' memory, the
        function's own memory and the results kept by the consumer not included.
    reader
        The reader the plan is for (not part of the plan's comparison or text).
    """

    windows: tuple[Bounds, ...]
    channels: tuple
    t: int | None
    z: int | None
    workers: int
    max_in_flight: int
    cache_bytes: int
    window_bytes: int
    in_flight_bytes: int
    memory_estimate: int
    reader: WindowReader | RunWindowReader = field(repr=False, compare=False)


@dataclass(frozen=True)
class MapSummary:
    """What a map did, for the consumer's execution record (never part of an identity).

    Attributes
    ----------
    windows
        Windows in the plan.
    delivered
        Results given to the consumer.
    finished
        Whether every window's result was given (False after an error or an early stop).
    workers, max_in_flight
        As planned.
    """

    windows: int
    delivered: int
    finished: bool
    workers: int
    max_in_flight: int


def map_plan(
    reader: WindowReader | RunWindowReader,
    windows: Iterable[Bounds],
    *,
    channels: Sequence,
    workers: int,
    t: int | None = None,
    z: int | None = None,
    max_in_flight: int | None = None,
) -> MapPlan:
    """Work out from the headers what mapping over ``windows`` holds at most; no pixel read.

    Parameters
    ----------
    reader
        A ``WindowReader`` or ``RunWindowReader`` of one image, shared by every worker.
    windows
        ``(y0, y1, x0, x1)`` half-open plane-frame bounds; they may overlap or repeat.
    channels
        C indices (``WindowReader``) or canonical channel IDs (``RunWindowReader``).
    workers
        Threads, a whole number of at least 1.
    t, z
        The plane; required for a ``RunWindowReader``, as its ``read_window`` requires them.
    max_in_flight
        Windows in flight at most, at least ``workers``; None gives ``2 × workers``.

    Raises
    ------
    ValueError
        For ``workers`` or ``max_in_flight`` not as above.
    TypeError
        For a reader of another kind, or a ``RunWindowReader`` without ``t`` and ``z``.
    WindowError, RunInputsError
        As the reader's ``window_plan`` refuses a window (bounds, channels, memory limit, a
        changed image, a closed reader), with a note naming the window.
    """
    in_flight = _check_counts(workers, max_in_flight)
    channels = tuple(channels)
    plan_one, _ = _calls(reader, channels, t, z)
    plans = [_named(plan_one, bounds, i) for i, bounds in enumerate(windows)]
    cache_bytes = reader.stats().max_bytes
    sizes = sorted((p.memory_estimate - cache_bytes for p in plans), reverse=True)
    in_flight_bytes = sum(sizes[:in_flight])
    return MapPlan(
        windows=tuple(p.bounds for p in plans),
        channels=channels,
        t=t,
        z=z,
        workers=workers,
        max_in_flight=in_flight,
        cache_bytes=cache_bytes,
        window_bytes=sizes[0] if sizes else 0,
        in_flight_bytes=in_flight_bytes,
        memory_estimate=in_flight_bytes + cache_bytes,
        reader=reader,
    )


def map_windows(
    plan: MapPlan, fn: Callable[[Bounds, np.ndarray], R], *, memory_budget: int
) -> WindowMap[R]:
    """Call ``fn(bounds, window)`` for every window of ``plan`` in threads; results in order.

    Parameters
    ----------
    plan
        From :func:`map_plan`.
    fn
        Called in a worker thread with a window's bounds and its ``(C, h, w)`` array (a new
        array, the reader's ``read_window``); it should release the GIL to gain from threads.
    memory_budget
        Bytes the consumer allows for the windows and the cache: the plan's
        ``memory_estimate`` must not be over it.

    Returns
    -------
    WindowMap
        An iterator of the results in the plan's window order; threads start at the first
        result asked. Close it (or use it in a ``with`` block) when stopping early.

    Raises
    ------
    WindowMemoryError
        When the plan is over ``memory_budget``, before any pixel is read.
    """
    if isinstance(memory_budget, bool) or not isinstance(memory_budget, int):
        raise ValueError(f"A memory budget is a whole number of bytes; got {memory_budget!r}.")
    if plan.memory_estimate > memory_budget:
        raise WindowMemoryError(
            f"Mapping over {len(plan.windows)} windows with {plan.workers} workers holds up to "
            f"{format_bytes(plan.memory_estimate)} ({plan.max_in_flight} windows in flight of "
            f"up to {format_bytes(plan.window_bytes)} each, and a cache of "
            f"{format_bytes(plan.cache_bytes)}), over the budget of "
            f"{format_bytes(memory_budget)}; no window was read. Plan fewer windows in flight "
            "or workers, a smaller cache or smaller windows, or allow more memory."
        )
    return WindowMap(plan, fn)


def iter_windows(plan: MapPlan, *, memory_budget: int) -> WindowMap[tuple[Bounds, np.ndarray]]:
    """Yield ``(bounds, window)`` in the plan's order while later windows are read in threads.

    :func:`map_windows` with a function that returns its arguments (design note §5.2 (a)): up
    to ``max_in_flight - 1`` windows after the one delivered are read ahead.
    """
    return map_windows(plan, _pair, memory_budget=memory_budget)


class WindowMap(Generic[R]):
    """The results of :func:`map_windows`, in window order; an iterator and a context manager.

    Attributes
    ----------
    plan
        The plan run.
    """

    def __init__(self, plan: MapPlan, fn: Callable[[Bounds, np.ndarray], R]) -> None:
        self.plan = plan
        self._delivered = 0
        self._failed = False
        # A module-level generator holding no reference to the map: dropping the map's last
        # reference closes it at once (no cycle waiting for the collector), in that thread.
        self._run = _ordered_results(plan, fn)

    def __iter__(self) -> WindowMap[R]:
        """Return the map itself."""
        return self

    def __next__(self) -> R:
        """Return the next window's result, in window order."""
        try:
            result = next(self._run)
        except StopIteration:
            raise
        except BaseException:
            self._failed = True
            raise
        self._delivered += 1
        return result

    def close(self) -> None:
        """Cancel the windows not started, wait for the running ones; later results end."""
        self._run.close()

    def summary(self) -> MapSummary:
        """Return the windows planned and delivered, and the workers recorded."""
        plan = self.plan
        finished = not self._failed and self._delivered == len(plan.windows)
        return MapSummary(
            len(plan.windows), self._delivered, finished, plan.workers, plan.max_in_flight
        )

    def __enter__(self) -> WindowMap[R]:
        """Return the map, closed when the ``with`` block is left."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the map (:meth:`close`)."""
        self.close()


def _ordered_results(plan: MapPlan, fn: Callable[[Bounds, np.ndarray], R]) -> Iterator[R]:
    """Run the plan's windows in a bounded pool and yield their results in window order.

    A window is submitted only when the next result is asked, so at most ``max_in_flight``
    windows are submitted and not yet delivered. Closing (or dropping) the generator cancels
    the windows not started and waits for the running ones, except in one of the pool's own
    threads, which cannot wait for itself: there the pool is only told to stop.
    """
    windows = plan.windows
    _, read = _calls(plan.reader, plan.channels, plan.t, plan.z)
    own: set[int] = set()
    pool = ThreadPoolExecutor(
        plan.workers, thread_name_prefix=THREAD_PREFIX, initializer=_record_thread, initargs=(own,)
    )
    pending: deque[tuple[int, Future]] = deque()
    submitted = 0
    try:
        while True:
            while submitted < len(windows) and len(pending) < plan.max_in_flight:
                pending.append((submitted, pool.submit(_task, read, fn, windows[submitted])))
                submitted += 1
            if not pending:
                return
            index, future = pending.popleft()
            yield _named(lambda bounds, f=future: f.result(), windows[index], index)
            del future
    finally:
        for _, future in pending:
            future.cancel()
        pool.shutdown(wait=threading.get_ident() not in own, cancel_futures=True)


def _record_thread(own: set[int]) -> None:
    """Record a pool thread's identity (the pool's initializer)."""
    own.add(threading.get_ident())


def _task(read: Callable[[Bounds], np.ndarray], fn: Callable, bounds: Bounds) -> object:
    """Read one window and call the consumer's function on it (in a worker thread)."""
    return fn(bounds, read(bounds))


def _pair(bounds: Bounds, window: np.ndarray) -> tuple[Bounds, np.ndarray]:
    """Return a window with its bounds (:func:`iter_windows`)."""
    return bounds, window


def _named(call: Callable[[Bounds], R], bounds: Bounds, index: int) -> R:
    """Return ``call(bounds)``; an error is re-raised unchanged with a note naming the window."""
    try:
        return call(bounds)
    except Exception as exc:
        exc.add_note(f"(window {index}, bounds {bounds!r}, in a window map)")
        raise


def _check_counts(workers: object, max_in_flight: object) -> int:
    """Return the windows in flight; refuse a worker count or bound that is not valid."""
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(f"workers is a whole number of at least 1; got {workers!r}.")
    if max_in_flight is None:
        return 2 * workers
    if (
        isinstance(max_in_flight, bool)
        or not isinstance(max_in_flight, int)
        or max_in_flight < workers
    ):
        raise ValueError(
            f"max_in_flight is a whole number of at least workers ({workers}); got "
            f"{max_in_flight!r}. It is never lowered or raised to fit."
        )
    return max_in_flight


def _calls(
    reader: object, channels: tuple, t: int | None, z: int | None
) -> tuple[Callable[[Bounds], WindowPlan], Callable[[Bounds], np.ndarray]]:
    """Return a reader's window plan and window read, bound to the channels and plane."""
    if isinstance(reader, RunWindowReader):
        if t is None or z is None:
            raise TypeError("A RunWindowReader reads a plane given by t and z; give both.")
        plane = {"t": t, "z": z, "channels": channels}
        return (
            lambda bounds: reader.window_plan(bounds=bounds, **plane),
            lambda bounds: reader.read_window(bounds=bounds, **plane),
        )
    if isinstance(reader, WindowReader):
        return (
            lambda bounds: reader.window_plan(bounds=bounds, channels=channels),
            lambda bounds: reader.read_window(bounds=bounds, channels=channels, t=t, z=z),
        )
    raise TypeError(
        f"A window map reads through a WindowReader or RunWindowReader; got "
        f"{type(reader).__name__}."
    )
