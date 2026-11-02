"""The run's exclusive lock on its output folder (design v0.4 §1, Concurrency).

A run (and ``check``, which writes the report) holds a lock file in the output folder,
created with ``O_EXCL`` so only one process can create it. It records the host, process ID
and start time of its holder. A second run is refused and names the holder. A lock left by
a run that was killed is removed only after the user confirms it is stale
(:func:`remove_stale_lock`): the brick never decides on its own that another run is gone.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..validation import ValidationIssue, error

LOCK_FILENAME = ".morphobricks-preprocessing.lock"


@dataclass(frozen=True)
class LockHolder:
    """The run holding an output folder's lock.

    Attributes
    ----------
    host
        The computer it runs on.
    pid
        Its process ID on that computer (0 when the lock file cannot be read).
    started_at
        When it took the lock (UTC, ISO 8601), or empty when unknown.
    """

    host: str
    pid: int
    started_at: str = ""

    def describe(self) -> str:
        """Name the holder in words, e.g. ``"process 412 on lab-pc, since 2026-11-02T10:00"``."""
        if not self.pid:
            return "a run whose lock file cannot be read"
        since = f", since {self.started_at}" if self.started_at else ""
        return f"process {self.pid} on {self.host}{since}"


class OutputLockedError(RuntimeError):
    """Raised when another run holds the output folder's lock.

    Attributes
    ----------
    holder
        The run holding it.
    """

    def __init__(self, path: Path, holder: LockHolder) -> None:
        self.path = path
        self.holder = holder
        super().__init__(f"{path} is locked by {holder.describe()}")


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
    path = Path(output_folder) / LOCK_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockHolder(str(data["host"]), int(data["pid"]), str(data.get("started_at", "")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return LockHolder("", 0)


@contextmanager
def output_lock(output_folder: str | os.PathLike) -> Iterator[LockHolder]:
    """Hold the output folder's lock for the duration of the block.

    The folder must exist. The lock file is created with ``O_CREAT | O_EXCL``, so two
    runs can never both hold it, and removed when the block ends, also on an error.

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
    """
    path = Path(output_folder) / LOCK_FILENAME
    holder = LockHolder(
        socket.gethostname(), os.getpid(), datetime.now(UTC).isoformat(timespec="seconds")
    )
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise OutputLockedError(path, read_lock(output_folder) or LockHolder("", 0)) from None
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(holder)) + "\n")
    except BaseException:
        path.unlink(missing_ok=True)  # just created by this run: a lock it could not write
        raise
    try:
        yield holder
    finally:
        if read_lock(output_folder) == holder:
            path.unlink(missing_ok=True)


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
    if holder.host == socket.gethostname() and _running(holder.pid):
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
    return error(
        "output_locked",
        f"Another run is using this output folder: {exc.holder.describe()}. Two runs must "
        "not write the same results at once, so nothing was done.",
        [exc.path.as_posix()],
        "Wait for the other run to finish. If no run is going on (it was stopped or the "
        "computer restarted), remove the lock: give --remove-stale-lock to the command, or "
        "call remove_stale_lock(output_folder), then run again.",
    )


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
