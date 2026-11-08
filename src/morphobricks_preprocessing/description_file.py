"""Reading ``morphobricks.toml`` from disk: the entry point of every reader (experiment-v1 2.1).

``read_description(path)`` is the one way a consumer gets a :class:`.description.Description`
from a file. A symbolic link is refused (``R_FILE_SYMLINK``), never followed, whatever it points
to; the bytes are read once and the same bytes are hashed and parsed. Everything else is the
strict reader's: nothing is converted, defaulted or repaired.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

from .description import Description, DescriptionError, description_from_bytes
from .description_report import DescriptionIssue
from .description_rules import Refusal

DESCRIPTION_FILENAME = "morphobricks.toml"
RECORDS_FILENAME = "morphobricks.records.jsonl"
DRAFT_FILENAME = "morphobricks.draft.json"
FILE_PHASE = 0
"""The ``phase`` of a ``DescriptionError`` about the file itself, before any byte is parsed."""


def symlink_error(path: Path) -> DescriptionError:
    """Return the refusal of a symbolic link (``R_FILE_SYMLINK``) at ``path``."""
    issue = DescriptionIssue(Refusal.R_FILE_SYMLINK, path.name, path.as_posix())
    return DescriptionError((issue,), FILE_PHASE)


def read_file_bytes(path: str | os.PathLike) -> bytes | None:
    """Return the bytes of the file at ``path``, or None when there is no such file.

    Raises
    ------
    DescriptionError
        ``R_FILE_SYMLINK`` when ``path`` is a symbolic link (also one that is dangling or that
        appears between the check and the open).
    OSError
        When the file exists and cannot be read, or is not a regular file; nothing is guessed.
    """
    path = Path(path)
    if path.is_symlink():
        raise symlink_error(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise symlink_error(path) from error
        raise
    with os.fdopen(handle, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", path.as_posix())
        return stream.read()


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 of ``data`` in lower-case hex (the file's provenance identity)."""
    return hashlib.sha256(data).hexdigest()


def read_description(path: str | os.PathLike) -> Description:
    """Read and validate a ``morphobricks.toml`` and return its model.

    Parameters
    ----------
    path
        The file. A missing file is a ``FileNotFoundError``, not an empty description.

    Returns
    -------
    Description
        The model of a file that passed phases 1 to 5 of the strict reader.

    Raises
    ------
    DescriptionError
        With every issue of the first failing phase; for a symbolic link, the one issue
        ``R_FILE_SYMLINK`` (``phase`` 0).
    FileNotFoundError
        When there is no file.
    """
    data = read_file_bytes(path)
    if data is None:
        raise FileNotFoundError(errno.ENOENT, "no experiment description", str(path))
    return description_from_bytes(data)
