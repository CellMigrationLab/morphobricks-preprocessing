"""A source image's fingerprint as it is on disk now (contract §9, design v0.4 §1).

The fingerprint is metadata only, never the pixels (design v0.4 §1):

- a TIFF file gives its own size and modification time;
- an OME-Zarr folder gives the total size of its files, the time of the newest one, and
  the SHA-256 of its **inventory**: every file's relative path, size and modification
  time, sorted by path (issue #86). Adding, removing, renaming, resizing or touching any
  chunk or metadata file changes the inventory, even when the total size and the newest
  time stay the same.

What no metadata can see is a file rewritten with the same size whose modification time
is then set back to its earlier value (as for a TIFF file): only hashing the content would,
and design v0.4 §1 keeps the fingerprint metadata only.

Moved from :mod:`.manifest` (PP-12), which re-exports :func:`source_fingerprint`.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

LABELS_GROUP = "labels"
"""An OME-Zarr image's group of label images (masks): outside the image's fingerprint."""

INVENTORY_KEY = "inventory_sha256"
"""The key of an OME-Zarr folder's inventory digest in the manifest and other records."""


def source_signature(path: str | os.PathLike) -> tuple[int, int, str | None]:
    """Return the size, modification time and inventory digest of an image.

    Parameters
    ----------
    path
        A TIFF file or an OME-Zarr folder (the ``labels`` group of the folder's root is
        left out: label images are masks, fingerprinted one by one, design v0.4 §2).

    Returns
    -------
    tuple
        ``(size, mtime_ns, inventory)``: for a file its own size and time, and None; for
        a folder the total size of its files, the newest time (the folder's own
        included) and the SHA-256 of its inventory (one line per file: the path relative
        to the folder with ``/``, its size and its ``mtime_ns``, sorted by path).

    Raises
    ------
    OSError
        If the image does not exist (``FileNotFoundError``), or a folder inside it cannot
        be listed: an inventory is never taken with a part left out.
    """
    path = Path(path)
    if not path.is_dir():
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns, None
    found = []
    newest = path.stat().st_mtime_ns
    for folder, subfolders, names in os.walk(path, onerror=_raise):
        here = Path(folder)
        if here == path and LABELS_GROUP in subfolders:
            subfolders.remove(LABELS_GROUP)
        relative = here.relative_to(path).as_posix()
        for name in names:
            stat = (here / name).stat()
            found.append((name if relative == "." else f"{relative}/{name}", stat))
            newest = max(newest, stat.st_mtime_ns)
    found.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    for name, stat in found:
        digest.update(os.fsencode(name) + f"\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return sum(stat.st_size for _, stat in found), newest, digest.hexdigest()


def source_fingerprint(path: str | os.PathLike) -> tuple[int, int]:
    """Return the size and modification time of an image, without its inventory.

    Kept for callers that show or compare only these two numbers; whether an image
    changed is decided with the inventory too (:func:`source_signature`,
    :class:`.fingerprint.Fingerprint`).

    Parameters
    ----------
    path
        A TIFF file or an OME-Zarr folder.

    Returns
    -------
    tuple
        ``(size, mtime_ns)`` as :func:`source_signature` gives them.
    """
    size, mtime_ns, _ = source_signature(path)
    return size, mtime_ns


def _raise(error: OSError) -> None:
    """Raise a listing error of :func:`os.walk`, which would otherwise skip the folder."""
    raise error
