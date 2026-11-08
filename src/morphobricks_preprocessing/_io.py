"""File writing shared by the brick's modules (private).

Every file the brick writes is written atomically: to a temporary file in the same folder,
then moved over the target with :func:`os.replace`, so a reader never sees half a file
and an interrupted write leaves the earlier file as it was (contract §9). One function
does it, so the rule is not copied per output file.

A temporary file is created readable by its owner only, so before it replaces the target
it is given the mode the file is to have (:func:`give_output_mode`): the mode of the file
it replaces, kept as the user set it, or for a new file the mode a plain ``open()`` gives,
``0666`` without the process's umask, so colleagues can read the results in a shared
folder. On Windows, where files have no such modes, nothing is changed.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path

_UMASK_LOCK = threading.Lock()
"""Serialises reading the umask by setting it, the only portable way to read it."""


def write_text_atomic(path: str | os.PathLike, text: str, *, parents: bool = False) -> Path:
    """Write UTF-8 text to ``path`` atomically, with the line endings as given.

    Parameters
    ----------
    path
        The file to write; replaced only once the whole text is written.
    text
        The content. Line endings are written as they are (no translation), so a text
        with LF line endings gives LF on every platform.
    parents
        Create the missing parent folders first.

    Returns
    -------
    Path
        The file written.
    """
    path = Path(path)
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="", encoding="utf-8") as stream:
            stream.write(text)
        give_output_mode(temporary, path)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def new_file_mode() -> int:
    """Return the mode a plain ``open()`` gives a new file: ``0666`` without the umask.

    Returns
    -------
    int
        The permission bits, e.g. ``0o644`` with the usual umask ``022``.
    """
    return 0o666 & ~_umask()


def _umask() -> int:
    """Return the process's umask, reading it without changing it where the system can.

    Linux gives it in ``/proc/self/status``. Elsewhere it is read by setting it, under a
    lock; while it is set, it is the most restrictive one, so a file another thread
    creates meanwhile is never more readable than asked.
    """
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    with _UMASK_LOCK:
        umask = os.umask(0o077)
        os.umask(umask)
    return umask


def give_output_mode(temporary: str | os.PathLike, target: str | os.PathLike) -> None:
    """Give a temporary file the mode it is to have once it replaces ``target``.

    Parameters
    ----------
    temporary
        The finished temporary file.
    target
        The file it replaces: its mode is kept when it exists, else the mode a plain
        ``open()`` would give (:func:`new_file_mode`). Nothing is changed on Windows.
    """
    if os.name != "posix":
        return
    try:
        wanted = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:  # no such file (or a link to none): a new file
        wanted = new_file_mode()
    os.chmod(temporary, wanted)


def free_space(path: str | os.PathLike) -> int | None:
    """Return the free space on the drive holding ``path`` (or its nearest existing parent).

    Parameters
    ----------
    path
        A file or folder, which may not exist yet.

    Returns
    -------
    int or None
        Free bytes; None when it cannot be determined.
    """
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free
            except OSError:
                return None
    return None


def file_sha256(path: str | os.PathLike) -> str:
    """SHA-256 of a file, read in blocks (moved from :mod:`.manifest`, which re-exports it)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
