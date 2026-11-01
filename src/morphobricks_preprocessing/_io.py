"""File writing shared by the brick's modules (private).

Every file the brick writes is written atomically: to a temporary file in the same folder,
then moved over the target with :func:`os.replace`, so a reader never sees half a file
and an interrupted write leaves the earlier file as it was (contract §9). One function
does it, so the rule is not copied per output file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


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
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
