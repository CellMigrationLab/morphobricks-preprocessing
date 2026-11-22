"""An exclusive lock file: created with ``O_EXCL``, naming its holder, removed only by it.

The run's lock on its output folder (:mod:`.run.lock`) and the segmentation record's lock
(:mod:`.segmentation_record`) are both this lock. A held lock is refused at once and names
its holder; it never waits. It protects the writers of one computer: a file system shared by
several computers (a Drive folder open in two Colab sessions) does not make the exclusive
create reliable.
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


class LockHeldError(RuntimeError):
    """Raised when another writer holds a lock file.

    Attributes
    ----------
    path
        The lock file.
    holder
        The writer holding it.
    """

    def __init__(self, path: Path, holder: LockHolder) -> None:
        self.path = path
        self.holder = holder
        super().__init__(f"{path} is locked by {holder.describe()}")


def read_holder(path: Path) -> LockHolder | None:
    """Return the holder recorded in the lock file ``path``, or None when it does not exist.

    A lock file that cannot be read gives a holder with ``pid`` 0.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockHolder(str(data["host"]), int(data["pid"]), str(data.get("started_at", "")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return LockHolder("", 0)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[LockHolder]:
    """Hold the lock file ``path`` for the block: created with ``O_CREAT | O_EXCL``.

    It records this process as its holder and is removed when the block ends, also on an
    error, but only while it still names this process (a lock another writer took after a
    user deleted this one is never removed). It never waits.

    Raises
    ------
    LockHeldError
        When the file exists: another writer holds it (named by its holder).
    """
    holder = LockHolder(
        socket.gethostname(), os.getpid(), datetime.now(UTC).isoformat(timespec="seconds")
    )
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise LockHeldError(path, read_holder(path) or LockHolder("", 0)) from None
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(holder)) + "\n")
    except BaseException:
        path.unlink(missing_ok=True)  # just created by this process: a lock it could not write
        raise
    try:
        yield holder
    finally:
        if read_holder(path) == holder:
            path.unlink(missing_ok=True)
