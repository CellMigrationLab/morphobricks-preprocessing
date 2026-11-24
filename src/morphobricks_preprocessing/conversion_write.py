"""Write the copies of a conversion in its work folder, then move them into place.

Moved from :mod:`.conversion` (PP-11c). On any failure, every file and folder the
conversion wrote is removed, so the copy tree is left as it was.

The work folder (issue #448): every copy is first written below
``<copy tree>/.morphobricks-conversion.partial/files/``, at its own relative path. The
conversion makes that folder itself (it must not exist) and writes its owner file
(``owner.json``: what made it, and the lock holder) into it before any copy, so whatever is
below it was written by a conversion. A conversion killed before its clean-up leaves it;
the next conversion removes it only when that owner file is there
(:func:`work_folder_state`), and refuses a work folder without it
(``conversion_work_folder_unowned``). Nothing else in the copy tree is ever removed but the
copies this conversion moved into place and the folders it made: a ``<name>.partial`` file
or folder beside a copy is not the conversion's (it was, before #448) and is never removed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from .content_hash import content_sha256
from .conversion_issues import failed_issue
from .conversion_record import CopyEntry
from .fingerprint import Fingerprint, current_fingerprint
from .lock_file import LockHolder
from .ome_tiff_copy import CopyWriteError, write_named_copy, write_projected_copy
from .ome_zarr_copy import OME_ZARR, write_named_zarr, write_projected_zarr
from .planes import PlaneReadError
from .planned_copy import PlannedCopy
from .projection import ProjectionRefused
from .projection_issues import projection_failed_issue
from .source_content import source_content_sha256
from .validation import ValidationIssue

logger = logging.getLogger(__name__)

WORK_FOLDER = ".morphobricks-conversion.partial"
"""The conversion's work folder in the copy tree; a hidden name, never read as an image."""
OWNER_FILENAME = "owner.json"
"""The owner file of a work folder, written before any copy: it proves the folder is one."""
OWNER_KIND = "morphobricks-preprocessing conversion work folder"
FILES_FOLDER = "files"
"""The folder below the work folder holding the copies, each at its own relative path."""
OWNED, UNOWNED = "owned", "unowned"
"""What :func:`work_folder_state` finds."""


class ConversionFailed(Exception):
    """A copy could not be written; carries the error for the user."""

    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


_FAILURES = (PlaneReadError, CopyWriteError, OSError, ValueError)
"""What reading a source or writing a copy raises when it fails (``tifffile`` raises
:class:`ValueError` for a file it cannot write); anything else is re-raised, after the same
clean-up."""


def write_all(
    root: Path,
    dest: Path,
    to_write: list[PlannedCopy],
    memory_limit: int,
    progress: Callable[[int, int, str], None] | None,
    holder: LockHolder,
) -> list[CopyEntry]:
    """Write every copy in the work folder, then move them all into place.

    An earlier conversion's work folder (its owner file there: :func:`work_folder_state`) is
    removed first; the caller refused one without it. On any failure, an interruption
    included, the work folder, every copy already moved into place (each where nothing was
    before) and every folder the conversion made are removed, so the copy tree is left as it
    was; a failure to read or write raises :class:`ConversionFailed`, anything else is
    re-raised.
    """
    work = dest / WORK_FOLDER
    if work_folder_state(dest) == OWNED:
        shutil.rmtree(work)  # left by a conversion that was killed: proven by its owner file
    if not to_write:
        return []
    made = _missing_folders(dest, to_write)
    written: list[Path] = []
    contents: dict[str, str] = {}
    current, mine = to_write[0].relative_path, False
    try:
        _make_work_folder(work, holder)
        mine = True
        for done, item in enumerate(to_write):
            current = item.relative_path
            if progress is not None:
                progress(done, len(to_write), current)
            found = _write_partial(root, work, item, memory_limit)
            if found is not None:
                contents[item.relative_path] = found
        if progress is not None:
            progress(len(to_write), len(to_write), "")
        entries = []
        for item in to_write:
            current, target = item.relative_path, dest / item.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(partial_path(work, item), target)
            written.append(target)
            copy, sha256 = Fingerprint.of(target), content_sha256(target)
            entry = replace(item.entry, copy=copy, copy_sha256=sha256)
            if item.relative_path in contents:
                done = entry.projection.with_content(contents[item.relative_path])
                entry = replace(entry, projection=done)
            entries.append(entry)
    except BaseException as exc:
        logger.debug("Conversion of %s failed", current, exc_info=True)
        _clean_up(work if mine else None, written, made)
        if isinstance(exc, ProjectionRefused):
            raise ConversionFailed(projection_failed_issue(current, exc)) from exc
        if isinstance(exc, (*_FAILURES, _SourceChanged)):
            raise ConversionFailed(failed_issue(current, exc)) from exc
        raise
    _remove_work_folder(work)
    return entries


def work_folder_state(dest: Path) -> str | None:
    """Return whether the copy tree holds a work folder, and whether a conversion made it.

    Returns
    -------
    str or None
        None when there is none; :data:`OWNED` when it is a folder (not a link) holding the
        owner file a conversion writes into it before anything else; else :data:`UNOWNED`
        (something else under that name: it is never removed).
    """
    work = dest / WORK_FOLDER
    if not os.path.lexists(work):
        return None
    owner = work / OWNER_FILENAME
    if work.is_symlink() or not work.is_dir() or owner.is_symlink():
        return UNOWNED
    try:
        found = json.loads(owner.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNOWNED
    return OWNED if isinstance(found, dict) and found.get("kind") == OWNER_KIND else UNOWNED


def _make_work_folder(work: Path, holder: LockHolder) -> None:
    """Make the work folder (it must not exist) and write its owner file before any copy."""
    work.mkdir()
    owner = {"kind": OWNER_KIND, "holder": asdict(holder)}
    with open(work / OWNER_FILENAME, "x", encoding="utf-8") as file:
        json.dump(owner, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())


def _clean_up(work: Path | None, written: list[Path], made: list[Path]) -> None:
    """Remove what a failed conversion wrote: its work folder, its copies, its folders."""
    for path in written:
        _remove(path)
    if work is not None:
        _remove_work_folder(work)
    _remove_empty(made)


def _remove_work_folder(work: Path) -> None:
    """Remove this conversion's work folder; one that cannot be removed is left for the next."""
    try:
        shutil.rmtree(work)
    except OSError:
        logger.warning("Cannot remove the work folder %s; the next conversion will", work)


def _missing_folders(dest: Path, to_write: list[PlannedCopy]) -> list[Path]:
    """Return the folders of the copy tree that writing these copies will make."""
    missing = set()
    for item in to_write:
        folder = (dest / item.relative_path).parent
        while folder != dest and not folder.exists():
            missing.add(folder)
            folder = folder.parent
    return sorted(missing, key=lambda folder: len(folder.parts), reverse=True)


def _remove_empty(folders: list[Path]) -> None:
    """Remove the folders this conversion made, deepest first, when they are empty."""
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            logger.debug("Folder %s kept: not empty or gone", folder, exc_info=True)


class _SourceChanged(Exception):
    """A source image changed since it was checked, or while it was copied."""


def _write_partial(root: Path, work: Path, item: PlannedCopy, memory_limit: int) -> str | None:
    """Write one copy in the work folder, refusing a source that changes meanwhile.

    Returns the source's content SHA-256 for a projected copy (taken before and after the
    projection, which must agree), else None.
    """
    source, partial = root / item.source_path, partial_path(work, item)
    partial.parent.mkdir(parents=True, exist_ok=True)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed since it was checked")
    content = None
    if item.entry.projection is not None:
        content = source_content_sha256(source)
        details = item.entry.projection.with_content(content)
        project = write_projected_zarr if item.store == OME_ZARR else write_projected_copy
        project(source, partial, item.layout, item.mapping, details, memory_limit=memory_limit)
        if source_content_sha256(source) != content:
            raise _SourceChanged("its content changed while it was projected")
    elif item.layout is not None:
        write = write_named_zarr if item.store == OME_ZARR else write_named_copy
        write(
            source, partial, item.layout, item.mapping, dtype=item.dtype, memory_limit=memory_limit
        )
    elif source.is_dir():
        shutil.copytree(source, partial, copy_function=shutil.copyfile)
    else:
        shutil.copyfile(source, partial)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed while it was copied")
    return content


def partial_path(work: Path, item: PlannedCopy) -> Path:
    """Return the path in the work folder where a copy is written before its move."""
    return work / FILES_FOLDER / item.relative_path


def _remove(path: Path) -> None:
    """Remove a copy this conversion moved into place (a file, or an OME-Zarr folder)."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
