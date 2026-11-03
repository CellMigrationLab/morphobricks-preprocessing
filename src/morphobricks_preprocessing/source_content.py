"""Strict reuse: a source's content SHA-256 (``verify_content``; owner, 3 November 2026).

By default a source is identified by its fingerprint (:mod:`.inventory`): metadata only,
which does not see a file rewritten with the same size whose modification time is then
set back. A run with ``verify_content`` (CLI ``--verify-content``) also records each
source's content SHA-256 in its manifest entry, and reuses an image's staged or earlier
rows only when that SHA-256 is the one recorded with them:

- a TIFF file: the SHA-256 of its bytes;
- an OME-Zarr folder: the SHA-256 of one line per file (its path relative to the folder,
  with ``/``, and its own SHA-256), in path order, over the files its fingerprint covers
  (:func:`.inventory.store_files`: the ``labels`` group left out, contained links followed).

It reads every analysed image whole once more per run (twice for an image whose pixels
are read: its SHA-256 is taken again after the read and must not have changed).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from .inventory import store_files
from .manifest import FileEntry, file_sha256

logger = logging.getLogger(__name__)


def source_content_sha256(path: str | os.PathLike) -> str:
    """Return the content SHA-256 of a TIFF file or an OME-Zarr folder.

    Parameters
    ----------
    path
        The image.

    Returns
    -------
    str
        64 hexadecimal digits.

    Raises
    ------
    OSError
        If the image or a file of it cannot be read (``FileNotFoundError`` when gone), or
        its links reach one folder twice.
    """
    path = Path(path)
    if not path.is_dir():
        return file_sha256(path)
    digest = hashlib.sha256()
    for name, _ in store_files(path)[1]:
        digest.update(os.fsencode(name) + f"\0{file_sha256(path / name)}\n".encode())
    return digest.hexdigest()


def current_content(path: Path) -> str | None:
    """Return an image's content SHA-256 now, or None when it cannot be read."""
    try:
        return source_content_sha256(path)
    except OSError:
        logger.warning("Cannot hash the content of %s", path, exc_info=True)
        return None


def with_content(
    entries: Mapping[str, FileEntry], root: Path
) -> tuple[dict[str, FileEntry], list[str]]:
    """Record each image's content SHA-256 in its entry.

    Parameters
    ----------
    entries
        The run's entries, ``{relative path: FileEntry}``.
    root
        The experiment folder.

    Returns
    -------
    tuple
        ``(entries with content_sha256, images that could not be read)``; an image that
        cannot be read keeps None, and is never reused.
    """
    found, unreadable = {}, []
    for rel, entry in entries.items():
        sha = current_content(root / rel)
        if sha is None:
            unreadable.append(rel)
        found[rel] = replace(entry, content_sha256=sha)
    return found, unreadable


def content_changed(entries: Iterable[FileEntry], root: Path) -> list[str]:
    """Return the images whose content SHA-256 now is not the one recorded (gone ones too).

    Parameters
    ----------
    entries
        The entries of a ``verify_content`` run.
    root
        The experiment folder.

    Returns
    -------
    list of str
        The changed, unreadable or unrecorded images, sorted.
    """
    return sorted(
        entry.relative_path
        for entry in entries
        if entry.content_sha256 is None
        or current_content(root / entry.relative_path) != entry.content_sha256
    )
