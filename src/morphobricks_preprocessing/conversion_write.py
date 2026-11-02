"""Write the copies of a conversion as ``.partial`` files, then move them into place.

Moved from :mod:`.conversion` (PP-11c). On any failure, every file and folder the
conversion wrote is removed, so the copy tree is left as it was.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .conversion_issues import failed_issue
from .conversion_plan import PlannedCopy
from .conversion_record import CopyEntry
from .fingerprint import Fingerprint, current_fingerprint
from .ome_tiff_copy import CopyWriteError, write_named_copy
from .planes import PlaneReadError
from .validation import ValidationIssue

logger = logging.getLogger(__name__)

PARTIAL_SUFFIX = ".partial"
"""A copy is written as ``<name>.partial`` and renamed once every copy is written."""


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
) -> list[CopyEntry]:
    """Write every copy as ``.partial``, then move them all into place.

    On any failure, an interruption included, every ``.partial`` file of the conversion,
    every copy already moved into place (each where nothing was before) and every folder
    the conversion made are removed, so the copy tree is left as it was; a failure to read
    or write raises :class:`ConversionFailed`, anything else is re-raised.
    """
    made = _missing_folders(dest, to_write)
    written: list[Path] = []
    current = to_write[0].relative_path if to_write else ""
    try:
        for done, item in enumerate(to_write):
            current = item.relative_path
            if progress is not None:
                progress(done, len(to_write), current)
            _write_partial(root, dest, item, memory_limit)
        if progress is not None:
            progress(len(to_write), len(to_write), "")
        entries = []
        for item in to_write:
            current, target = item.relative_path, dest / item.relative_path
            os.replace(partial_path(dest, item), target)
            written.append(target)
            entries.append(replace(item.entry, copy=Fingerprint.of(target)))
    except BaseException as exc:
        logger.debug("Conversion of %s failed", current, exc_info=True)
        for path in [*written, *(partial_path(dest, item) for item in to_write)]:
            _remove(path)
        _remove_empty(made)
        if isinstance(exc, (*_FAILURES, _SourceChanged)):
            raise ConversionFailed(failed_issue(current, exc)) from exc
        raise
    return entries


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


def _write_partial(root: Path, dest: Path, item: PlannedCopy, memory_limit: int) -> None:
    """Write one copy as ``<name>.partial``, refusing a source that changes meanwhile."""
    source, partial = root / item.relative_path, partial_path(dest, item)
    partial.parent.mkdir(parents=True, exist_ok=True)
    _remove(partial)  # left by an interrupted conversion: it is never reused
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed since it was checked")
    if item.layout is not None:
        write_named_copy(source, partial, item.layout, item.mapping, memory_limit=memory_limit)
    elif source.is_dir():
        shutil.copytree(source, partial, copy_function=shutil.copyfile)
    else:
        shutil.copyfile(source, partial)
    if current_fingerprint(source) != item.entry.source:
        raise _SourceChanged("it changed while it was copied")


def partial_path(dest: Path, item: PlannedCopy) -> Path:
    """Return the ``.partial`` path of a copy."""
    return dest / (item.relative_path + PARTIAL_SUFFIX)


def _remove(path: Path) -> None:
    """Remove a file or folder this conversion wrote (or left earlier) if it is there."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
