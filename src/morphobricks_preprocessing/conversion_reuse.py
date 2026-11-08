"""Compare a planned conversion with what the copy tree already holds.

Moved from :mod:`.conversion` (PP-11c). A copy is reused only when its plan and its copy
are unchanged: the copy's fingerprint and its SHA-256 (read whole, issue #87) as recorded;
anything else already there is refused, never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

from .content_hash import unchanged_content
from .conversion_issues import differs_issue, not_empty_issue, other_experiment_issue
from .conversion_record import ConversionRecord, CopyEntry
from .conversion_write import PARTIAL_SUFFIX
from .fingerprint import current_fingerprint
from .planned_copy import PlannedCopy
from .run.lock import LOCK_FILENAME
from .validation import ValidationIssue


def compare(
    root: Path,
    dest: Path,
    planned: tuple[PlannedCopy, ...],
    earlier: tuple[ConversionRecord, str] | None,
) -> tuple[list[CopyEntry], list[PlannedCopy], ValidationIssue | None]:
    """Split the plan into copies to reuse and to write; refuse anything else there."""
    if earlier is None:
        unknown = unknown_content(dest, planned)
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


def unknown_content(dest: Path, planned: tuple[PlannedCopy, ...]) -> list[str]:
    """Return the files in a folder without a record that this conversion did not leave.

    Only the lock and the ``.partial`` files of the planned copies (left by an interrupted
    conversion, removed before they are written again) are allowed.
    """
    partials = {item.relative_path + PARTIAL_SUFFIX for item in planned}
    found = []
    for folder, subfolders, names in os.walk(dest):
        relative = Path(folder).relative_to(dest)
        subfolders[:] = [d for d in subfolders if (relative / d).as_posix() not in partials]
        for name in names:
            path = (relative / name).as_posix()
            if path != LOCK_FILENAME and path not in partials:
                found.append(path)
    return sorted(found)
