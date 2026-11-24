"""The run's exclusive lock on its output folder (design v0.4 §1, Concurrency).

A run (and ``check``, which writes the report) holds a lock file in the output folder,
created with ``O_EXCL`` so only one process can create it. It records the host, process ID
and start time of its holder. A second run is refused and names the holder. A lock left by
a run that was killed is removed only after the user confirms it is stale
(:func:`remove_stale_lock`): the brick never decides on its own that another run is gone.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ..lock_file import LockHeldError, LockHolder, exclusive_lock, read_holder
from ..validation import ValidationIssue, error
from .generation import finish_publication

LOCK_FILENAME = ".morphobricks-preprocessing.lock"


class OutputLockedError(LockHeldError):
    """Raised when another run holds the output folder's lock.

    Attributes
    ----------
    holder
        The run holding it.
    """


def read_lock(output_folder: str | os.PathLike) -> LockHolder | None:
    """Return the holder of an output folder's lock, or None when it is not locked.

    Parameters
    ----------
    output_folder
        The output folder.

    Returns
    -------
    LockHolder or None
        The holder; a lock file that cannot be read gives a holder with ``pid`` 0.
    """
    return read_holder(Path(output_folder) / LOCK_FILENAME)


@contextmanager
def output_lock(output_folder: str | os.PathLike) -> Iterator[LockHolder]:
    """Hold the output folder's lock for the duration of the block.

    The folder must exist. The lock file is created with ``O_CREAT | O_EXCL``, so two
    runs can never both hold it, and removed when the block ends, also on an error. Once
    it is held, a publication an earlier run left unfinished is finished, or discarded
    when it was never committed (:func:`.generation.finish_publication`), so whatever
    holds the lock works on one whole generation of the outputs.

    Parameters
    ----------
    output_folder
        The output folder.

    Yields
    ------
    LockHolder
        This run, as recorded in the lock file.

    Raises
    ------
    OutputLockedError
        When another run holds the lock.
    PublicationIncompleteError
        When an unfinished publication's files cannot be moved into place.
    """
    with folder_lock(output_folder) as holder:
        finish_publication(Path(output_folder))
        yield holder


@contextmanager
def folder_lock(folder: str | os.PathLike) -> Iterator[LockHolder]:
    """Hold the lock of :func:`output_lock` on a folder that is no run's output folder.

    The same lock file, taken and refused the same way, but nothing in the folder is read
    or changed: no publication is finished or discarded. A conversion holds it on its copy
    tree (issue #467): a ``.publish`` folder there is not a run's, and finishing it would
    remove or move files, through a link even, before the copy tree is checked.

    Parameters
    ----------
    folder
        The folder; it must exist.

    Yields
    ------
    LockHolder
        This holder, as recorded in the lock file.

    Raises
    ------
    OutputLockedError
        When another run or conversion holds the lock.
    """
    stack = ExitStack()
    try:
        holder = stack.enter_context(exclusive_lock(Path(folder) / LOCK_FILENAME))
    except LockHeldError as held:
        raise OutputLockedError(held.path, held.holder) from None
    with stack:
        yield holder


def remove_stale_lock(output_folder: str | os.PathLike) -> LockHolder | None:
    """Remove a lock the user confirmed is stale (its run was killed or the computer restarted).

    Call it only after the user confirmed that no other run uses the output folder. A
    lock held by a process that is still running on this computer is never removed.

    Parameters
    ----------
    output_folder
        The output folder.

    Returns
    -------
    LockHolder or None
        The holder of the removed lock, or None when there was no lock.

    Raises
    ------
    OutputLockedError
        When the holder is a process still running on this computer.
    """
    holder = read_lock(output_folder)
    if holder is None:
        return None
    path = Path(output_folder) / LOCK_FILENAME
    if holder_running(holder):
        raise OutputLockedError(path, holder)
    path.unlink(missing_ok=True)
    return holder


def lock_issue(exc: OutputLockedError) -> ValidationIssue:
    """Return the error refusing a run because another run holds the lock.

    Parameters
    ----------
    exc
        The refusal.

    Returns
    -------
    ValidationIssue
        The error ``output_locked``, naming the holder and the lock file.
    """
    holder = exc.holder
    removal = (
        "give --remove-stale-lock to the command, press 'Remove the lock of a stopped run' "
        "in the notebook, or call remove_stale_lock(output_folder), then run again."
    )
    if looks_stale(holder):
        # Cross-brick test F11: a run killed here left its lock. It looks stale, but the
        # brick never removes a lock on its own (module docstring): the user decides.
        return error(
            "output_locked",
            f"The output folder's lock looks stale: it was left by {holder.describe()}, "
            "which is no longer running (the run was probably stopped). The lock was not "
            "removed, so nothing was done.",
            [exc.path.as_posix()],
            f"If no run is going on in this output folder, remove the lock: {removal}",
        )
    return error(
        "output_locked",
        f"Another run is using this output folder: {holder.describe()}. Two runs must "
        "not write the same results at once, so nothing was done.",
        [exc.path.as_posix()],
        "Wait for the other run to finish. If no run is going on (it was stopped or the "
        f"computer restarted), remove the lock: {removal}",
    )


def looks_stale(holder: LockHolder) -> bool:
    """Return True only when the holder ran on this computer and is no longer running.

    Only POSIX can tell (:func:`_running`); elsewhere, and for a holder on another computer,
    the answer is False. It only words the refusal: the lock is never removed on its own.
    """
    pid = holder.pid
    return (
        os.name == "posix"
        and pid > 0
        and holder.host == socket.gethostname()
        and (not _running(pid))
    )


def holder_running(holder: LockHolder) -> bool:
    """Return True when the holder is a process that may still be running on this computer.

    The liveness check of :func:`remove_stale_lock`, also used for the owner of a
    conversion's work folder (:mod:`.conversion_write`, issue #482). Only POSIX can tell
    (:func:`_running`); elsewhere, and for a holder on another computer, the answer is False.
    """
    return holder.host == socket.gethostname() and _running(holder.pid)


def _running(pid: int) -> bool:
    """Return True if a process with this ID may be running on this computer.

    Only POSIX can ask without side effects; elsewhere the answer is False and the user's
    confirmation decides.
    """
    if pid <= 0 or os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
