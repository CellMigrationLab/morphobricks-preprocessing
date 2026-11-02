"""Publish a run's outputs as one generation, made current in one atomic step (contract §9).

A run's published outputs (``plane_histograms.csv``, ``experiment_design.csv``,
``run_manifest.json`` and the report files) are one **generation**. Publication is
crash-consistent (owner decision of 2 November 2026, PP-12):

1. The whole new generation is written into the publication folder ``.publish`` in the
   output folder, each file flushed to disk, and then the **publication record**
   ``.publish/publication.json`` naming every file with its SHA-256 (and the files the
   generation removes, such as ``mask_relationships.json``).
2. **Commit, the one atomic step:** the new ``run_manifest.json`` replaces the old one
   (:func:`os.replace`). Before it nothing in the output folder has changed: a run
   killed then leaves the earlier generation exactly as it was, and the next run discards
   the publication folder. After it the new generation is the current one.
3. The other files are moved into place (:func:`os.replace` each), the removed files
   removed, and the publication folder deleted.

A run killed during step 3 leaves the new manifest with some files still in ``.publish``.
The next run, ``check``, ``masks``, ``design`` or ``calibrate`` (whatever takes the output
folder's lock) finishes the moves first (:func:`finish_publication`). Readers that go
through the manifest (``RunInputs``) never see a mix: they read each file the manifest
names by its SHA-256, from the output folder or, while a publication is unfinished, from
``.publish`` (:func:`published_path`). A plain reader of ``plane_histograms.csv`` alone
always reads one whole table of one generation (each move is one rename), the old one
until its move; one that reads several files without the manifest can pair files of two
generations during step 3 or after a kill in it, until the next run finishes it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from .._io import give_output_mode, write_text_atomic
from ..design import DESIGN_FILENAME
from ..discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from ..manifest import MANIFEST_FILENAME, file_sha256

logger = logging.getLogger(__name__)

T = TypeVar("T")

PUBLISH_FOLDER = ".publish"
"""The publication folder in the output folder, holding a generation being published."""
PUBLICATION_RECORD = "publication.json"
"""The record of a generation being published, written last into :data:`PUBLISH_FOLDER`."""
PUBLICATION_VERSION = "1"
MOVED_IN_ORDER = (OUTPUT_FILENAME, DESIGN_FILENAME, REPORT_TEXT_FILENAME, REPORT_FILENAME)
"""The files moved into place after the commit, in this order (the manifest is the commit)."""


class PublicationIncompleteError(RuntimeError):
    """Raised when a committed generation's files cannot all be moved into place.

    The new generation is the current one (its manifest is in place) and readers that go
    through the manifest read it; the next run that takes the output folder's lock finishes
    the moves.
    """


@contextmanager
def staged_generation(output: Path, removes: Iterable[str] = ()) -> Iterator[Path]:
    """Give a fresh publication folder to write a generation into, then publish it.

    Parameters
    ----------
    output
        The output folder (its lock held).
    removes
        Output files the new generation removes (for example ``mask_relationships.json``).

    Yields
    ------
    Path
        The publication folder: write the manifest there and any of
        :data:`MOVED_IN_ORDER`. When the block ends without an exception, the generation
        is published (:func:`publish_generation`); on an exception the folder is removed
        and nothing in the output folder has changed.
    """
    finish_publication(output)
    staged = output / PUBLISH_FOLDER
    staged.mkdir()
    try:
        yield staged
        _commit(output, staged, tuple(removes))
    except BaseException:
        # Not committed (or interrupted right after the commit): discarded, or finished.
        try:
            finish_publication(output)
        except Exception:
            logger.warning("Cannot clean up the publication in %s", staged, exc_info=True)
        raise
    _finish_committed(output, staged)


def _commit(output: Path, staged: Path, removes: tuple[str, ...]) -> None:
    """Write the publication record, then replace the manifest: the one atomic step.

    Nothing in the output folder changes before the replacement, which either happens
    whole or not at all.
    """
    names = [name for name in (*MOVED_IN_ORDER, MANIFEST_FILENAME) if (staged / name).exists()]
    if MANIFEST_FILENAME not in names:
        raise ValueError("a generation needs its manifest")
    for name in names:
        _flush(staged / name)
    record = {
        "publication_version": PUBLICATION_VERSION,
        "files": {name: file_sha256(staged / name) for name in names},
        "removes": sorted(removes),
    }
    write_text_atomic(staged / PUBLICATION_RECORD, json.dumps(record, indent=2) + "\n")
    _flush(staged / PUBLICATION_RECORD)
    _flush_folder(staged)
    give_output_mode(staged / MANIFEST_FILENAME, output / MANIFEST_FILENAME)
    os.replace(staged / MANIFEST_FILENAME, output / MANIFEST_FILENAME)


def finish_publication(output: Path) -> str | None:
    """Finish or discard a publication an earlier run left in the output folder.

    Called by everything that takes the output folder's lock, before it reads or writes
    the outputs. A publication whose manifest is in place (committed) is finished: its
    remaining files are moved into place and the files it removes removed. Any other
    publication folder (not committed: the run was killed before its manifest replaced
    the earlier one) is deleted; the earlier generation is then intact.

    Parameters
    ----------
    output
        The output folder (its lock held).

    Returns
    -------
    str or None
        ``"finished"``, ``"discarded"``, or None when there was no publication folder.

    Raises
    ------
    PublicationIncompleteError
        If a committed generation's files cannot be moved into place.
    """
    staged = output / PUBLISH_FOLDER
    if not staged.exists():
        return None
    record = _record(staged)
    manifest = output / MANIFEST_FILENAME
    committed = (
        record is not None
        and manifest.is_file()
        and file_sha256(manifest) == record["files"].get(MANIFEST_FILENAME)
    )
    if not committed:
        logger.info("Discarding the unfinished publication in %s", staged)
        shutil.rmtree(staged)
        return "discarded"
    logger.info("Finishing the publication an earlier run left in %s", staged)
    _finish_committed(output, staged)
    return "finished"


def _finish_committed(output: Path, staged: Path) -> None:
    """Move a committed generation's files into place, remove what it removes, clean up."""
    record = _record(staged) or {"removes": []}
    try:
        _flush_folder(output)
        for name in MOVED_IN_ORDER:
            if (staged / name).exists():
                give_output_mode(staged / name, output / name)
                os.replace(staged / name, output / name)
        for name in record["removes"]:
            (output / name).unlink(missing_ok=True)
    except OSError as exc:
        raise PublicationIncompleteError(
            f"The new results are published ({MANIFEST_FILENAME} names them), but some "
            f"files could not be moved into place from {staged} ({exc}). Close programs "
            "using the output files and run again: the next run finishes the move first. "
            "Later bricks already read the new results."
        ) from exc
    try:
        shutil.rmtree(staged)
    except OSError:
        # Every file is in place: a leftover folder is removed by the next run.
        logger.warning("Cannot remove the publication folder %s", staged, exc_info=True)


def published_path(output: Path, name: str, sha256: str) -> Path:
    """Return where the file of the current generation named ``name`` is (for readers).

    Parameters
    ----------
    output
        The output folder.
    name
        The output file, for example ``plane_histograms.csv``.
    sha256
        Its SHA-256 as the current manifest records it.

    Returns
    -------
    Path
        The file in the output folder when its SHA-256 is ``sha256``; else the copy an
        unfinished publication holds in :data:`PUBLISH_FOLDER` when its SHA-256 is; else
        the file in the output folder (the caller's check then refuses it).
    """
    path = output / name
    if _has_sha(path, sha256):
        return path
    pending = output / PUBLISH_FOLDER / name
    return pending if _has_sha(pending, sha256) else path


def read_stable(output: Path, read: Callable[[], T], attempts: int = 3) -> T | None:
    """Call ``read`` until no publication happened while it read (for readers).

    A publication changes the manifest before it moves any other file into place, so a
    reader that finds the same manifest before and after reading the files it names has
    read one generation, whatever a run did meanwhile.

    Parameters
    ----------
    output
        The output folder.
    read
        Reads the files the manifest names (through :func:`published_path`). A
        ``FileNotFoundError`` (a file moved into place meanwhile) counts as a try.
    attempts
        How many times to try.

    Returns
    -------
    object or None
        What ``read`` returned, or None when every try overlapped a publication.
    """
    for _ in range(attempts):
        before = _manifest_bytes(output)
        try:
            value = read()
        except FileNotFoundError:
            continue
        if _manifest_bytes(output) == before:
            return value
    return None


def _manifest_bytes(output: Path) -> bytes | None:
    """Return the manifest's bytes, or None when there is none."""
    try:
        return (output / MANIFEST_FILENAME).read_bytes()
    except FileNotFoundError:
        return None


def _has_sha(path: Path, sha256: str) -> bool:
    """Return True when ``path`` is a file whose SHA-256 is ``sha256``."""
    try:
        return path.is_file() and file_sha256(path) == sha256
    except FileNotFoundError:  # moved away meanwhile
        return False


def _record(staged: Path) -> dict | None:
    """Read a publication record, or None when it is absent or cannot be read."""
    try:
        data = json.loads((staged / PUBLICATION_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("publication_version") != PUBLICATION_VERSION:
        return None
    files, removes = data.get("files"), data.get("removes")
    if not isinstance(files, dict) or not isinstance(removes, list):
        return None
    return data


def _flush(path: Path) -> None:
    """Flush a written file to the disk, so a power cut cannot commit a partial file."""
    with open(path, "rb+") as stream:
        os.fsync(stream.fileno())


def _flush_folder(folder: Path) -> None:
    """Flush a folder's entries (POSIX only; Windows cannot open a folder)."""
    if os.name != "posix":
        return
    handle = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
