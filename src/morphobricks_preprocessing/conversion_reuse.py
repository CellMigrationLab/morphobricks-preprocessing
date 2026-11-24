"""Compare a planned conversion with what the copy tree already holds.

Moved from :mod:`.conversion` (PP-11c). A copy is reused only when its plan and its copy
are unchanged: the copy's fingerprint and its SHA-256 (read whole, issue #87) as recorded;
anything else already there is refused, never overwritten. A copy tree holding a link or a
mount point anywhere below it is refused first (:func:`links_below`, issue #447): writes
through it would land outside the folder chosen for the copies.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .content_hash import unchanged_content
from .conversion_issues import differs_issue, not_empty_issue, other_experiment_issue
from .conversion_record import ConversionRecord, CopyEntry
from .conversion_write import WORK_FOLDERS
from .fingerprint import current_fingerprint
from .links import is_link
from .mounts import MountCheck
from .planned_copy import PlannedCopy
from .run.lock import LOCK_FILENAME
from .validation import ValidationIssue

logger = logging.getLogger(__name__)


def compare(
    root: Path,
    dest: Path,
    planned: tuple[PlannedCopy, ...],
    earlier: tuple[ConversionRecord, str] | None,
) -> tuple[list[CopyEntry], list[PlannedCopy], ValidationIssue | None]:
    """Split the plan into copies to reuse and to write; refuse anything else there."""
    if earlier is None:
        unknown = unknown_content(dest)
        return [], list(planned), not_empty_issue(dest, unknown) if unknown else None
    record = earlier[0]
    if record.source_root != root.as_posix():
        return [], [], other_experiment_issue(dest, record.source_root)
    reused, to_write, differing = [], [], []
    for item in planned:
        old = record.entry(item.relative_path)
        target = dest / item.relative_path
        if old is None:
            (differing if os.path.lexists(target) else to_write).append(item)
        elif _unchanged(old, item.entry, target):
            reused.append(old)
        else:
            differing.append(item)
    paths = [item.relative_path for item in differing]
    names = {item.relative_path for item in planned}
    paths += [e.relative_path for e in record.files if e.relative_path not in names]
    return reused, to_write, differs_issue(dest, sorted(paths)) if paths else None


def _unchanged(old: CopyEntry, planned: CopyEntry, target: Path) -> bool:
    """Return True when a recorded copy has the plan and the content it was written with."""
    return (
        old.same_plan(planned)
        and current_fingerprint(target) == old.copy
        and unchanged_content(target, old.copy_sha256)
    )


def unknown_content(dest: Path) -> list[str]:
    """Return the files in a folder without a record that this conversion did not leave.

    Only the lock and the work folder (checked by the caller: a conversion's is removed,
    anything else under its name refused) are allowed. A ``<name>.partial`` beside a planned
    copy is unknown content like any other file: it is not the conversion's (issue #448).
    """
    found = []
    for folder, subfolders, names in os.walk(dest):
        relative = Path(folder).relative_to(dest)
        if folder == str(dest):
            subfolders[:] = [d for d in subfolders if d not in WORK_FOLDERS]
        for name in names:
            path = (relative / name).as_posix()
            if path != LOCK_FILENAME and path not in WORK_FOLDERS:
                found.append(path)
    return sorted(found)


def links_below(dest: Path) -> tuple[list[str], list[str]]:
    """Return every link and mount point below the copy tree ``dest`` (resolved), sorted.

    A conversion writes only real files in real folders, so anything that sends a path
    below ``dest`` elsewhere is refused, wherever it leads (into the experiment, another
    folder, nowhere) and whether or not a planned copy goes through it (issue #447). Links
    and directory junctions are told by :func:`.links.is_link`, mount points by
    :class:`.mounts.MountCheck`, the rules of the experiment's walk (#81); neither is
    entered. Links are named ``link -> target``, relative to ``dest``.

    Returns
    -------
    tuple of list of str
        The links and mount points, and the folders that cannot be read (relative to
        ``dest``, ``"."`` for ``dest`` itself): a folder that cannot be listed, or that
        disappears during the walk, may hold a link, so it is refused too (issue #467).
    """
    mounts, found, unreadable, pending = MountCheck(dest), [], [], [dest]
    while pending:
        folder = pending.pop()
        try:
            found += _links_in(dest, folder, mounts, pending)
        except OSError:
            logger.debug("Cannot read %s in the copy tree", folder, exc_info=True)
            unreadable.append(folder.relative_to(dest).as_posix())
    return sorted(found), sorted(unreadable)


def _links_in(dest: Path, folder: Path, mounts: MountCheck, pending: list[Path]) -> list[str]:
    """Return the links and mount points of one folder; add its other folders to ``pending``.

    Raises :class:`OSError` when the folder cannot be read; nothing is added then.
    """
    found, below = [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            path = Path(entry.path)
            name = path.relative_to(dest).as_posix()
            if is_link(entry):
                found.append(f"{name} -> {os.readlink(path)}")
            elif mounts.is_mount(path, entry):
                found.append(name)
            elif entry.is_dir(follow_symlinks=False):
                below.append(path)
    pending += below
    return found
