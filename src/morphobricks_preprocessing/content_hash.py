"""The SHA-256 of a copy's content: a file, or an OME-Zarr folder (contract §17, PP-11c).

A copy's fingerprint (size and modification time) does not see a change that keeps both,
so the conversion record also gives each copy's SHA-256 (owner decision of 2 November
2026, plan §12 question 59 (16)), and reuse, :func:`.conversion_record.review_conversion`
and :class:`.run_inputs.RunInputs` check it. Sources keep the fingerprint rule (design v0.4
§1).

An OME-Zarr copy is hashed as the SHA-256 of its files' relative paths and SHA-256s, in
path order. Its ``labels`` group is left out, as :func:`.inventory.source_fingerprint`
leaves it out: label images are masks, recorded and checked one by one by the run, and
a brick may add a label image to an OME-Zarr image without changing the image. A folder
holding a link to a folder is not hashed (:class:`OSError`): the files behind the link
would be left out, and a copy is never hashed in part (the conversion writes no links).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .inventory import LABELS_GROUP
from .manifest import file_sha256


def content_sha256(path: str | os.PathLike) -> str:
    """Return the SHA-256 of a file, or of an OME-Zarr folder's files (``labels`` aside).

    Parameters
    ----------
    path
        The file or folder.

    Returns
    -------
    str
        The SHA-256, as 64 hexadecimal digits.

    Raises
    ------
    OSError
        If the file or folder cannot be read (``FileNotFoundError`` when it is gone), or
        the folder holds a link to a folder.
    """
    path = Path(path)
    if not path.is_dir():
        return file_sha256(path)
    digest = hashlib.sha256()
    for relative in _folder_files(path):
        digest.update(f"{relative}\0{file_sha256(path / relative)}\n".encode())
    return digest.hexdigest()


def _folder_files(folder: Path) -> list[str]:
    """Return the files of a folder, relative and sorted, without its ``labels`` group."""
    found = []
    for current, subfolders, names in os.walk(folder, onerror=_raise):
        if Path(current) == folder and LABELS_GROUP in subfolders:
            subfolders.remove(LABELS_GROUP)
        relative = Path(current).relative_to(folder)
        # os.walk does not enter a link to a folder, so its files would not be hashed.
        linked = [name for name in subfolders if (Path(current) / name).is_symlink()]
        if linked:
            raise OSError(
                f"{folder} holds a link to a folder ({(relative / linked[0]).as_posix()})"
            )
        found += [(relative / name).as_posix() for name in names]
    return sorted(found)


def _raise(exc: OSError) -> None:
    """Raise a folder that cannot be listed: a copy is never hashed in part."""
    raise exc


def unchanged_content(path: str | os.PathLike, expected: str | None) -> bool:
    """Return True when a copy is there, readable, and its SHA-256 is ``expected``.

    A copy that cannot be read is not known to be unchanged, so it counts as changed.

    Parameters
    ----------
    path
        The copy.
    expected
        The SHA-256 recorded when it was written; None (not recorded) is never unchanged.

    Returns
    -------
    bool
    """
    if expected is None:
        return False
    try:
        return content_sha256(path) == expected
    except OSError:
        return False
