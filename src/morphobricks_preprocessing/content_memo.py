"""Content SHA-256s taken during one press, reused while the file's identity is unchanged.

Owner decision on #578 (26 November 2026): within **one** press of **Register the masks**, a
content SHA-256 already taken of an image is not taken again when the file is provably
unchanged since then. The proof is the file's identity, captured when it was hashed and
compared at reuse: device, inode, size, modification time and status-change time
(``st_dev``, ``st_ino``, ``st_size``, ``st_mtime_ns``, ``st_ctime_ns``). The owner's rule names
the first four; the status-change time is added because any doubt means hashing again, and on
POSIX it moves with every write and every ``os.utime``, which a user cannot set back.

- A :class:`ContentMemo` is created at the start of a press and passed explicitly to every
  check of that press; it is never a module global and never outlives the press.
- A file whose identity moved while it was hashed is not remembered; an OME-Zarr folder is
  never remembered (its identity is not one file's): both are hashed every time.
- Checks across presses, and every check at publication, do not use it: they stay as strict
  as before.

What the identity rule cannot catch: a file rewritten in place with every one of those five
values the same. On POSIX the status-change time cannot be set back, so that needs a file
system that does not keep it (some network or FUSE mounts); on Windows ``st_ctime`` is the
creation time. Such a change is still caught by the run's check before it publishes, which
never reuses a SHA-256 (``tests/test_content_memo.py``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

Identity = tuple[int, int, int, int, int]
"""``(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)`` of a file."""


def file_identity(path: str | os.PathLike) -> Identity | None:
    """Return the identity of a regular file, or None for a folder or a file that is gone."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if not Path(path).is_file():
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class ContentMemo:
    """The content SHA-256s one press took, each with the file identity it was taken at.

    Attributes
    ----------
    hashed, reused
        How many SHA-256s were taken, and how many were reused (for tests and reports).
    """

    def __init__(self) -> None:
        self._seen: dict[str, tuple[Identity, str]] = {}
        self.hashed = 0
        self.reused = 0

    def sha256(self, path: str | os.PathLike, compute: Callable[[Path], str]) -> str:
        """Return ``compute(path)``, or the SHA-256 taken earlier in the press if unchanged.

        Parameters
        ----------
        path
            The image (a TIFF file or an OME-Zarr folder).
        compute
            Takes the SHA-256 (it reads the file whole); its errors propagate.

        Returns
        -------
        str
            The content SHA-256.
        """
        path = Path(path)
        key = os.fspath(path.resolve())
        before = file_identity(path)
        kept = self._seen.get(key)
        if before is not None and kept is not None and kept[0] == before:
            self.reused += 1
            return kept[1]
        sha = compute(path)
        self.hashed += 1
        after = file_identity(path)
        if before is not None and before == after:
            self._seen[key] = (before, sha)
        else:
            self._seen.pop(key, None)  # moved while hashed, or not a file: never reused
        return sha


__all__ = ["ContentMemo", "Identity", "file_identity"]
