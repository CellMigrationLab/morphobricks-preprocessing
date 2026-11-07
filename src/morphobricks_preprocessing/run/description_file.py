"""Publishing ``morphobricks.toml`` under the output folder's lock (experiment-v1 sections 4.6, 10).

``publish_description`` is the only writer of the published file. Under
:func:`.lock.output_lock` it refuses a symbolic link, checks that the file on disk is still the
base the draft was derived from (the SHA-256 of its exact bytes, or ``absent``), writes the
candidate with the canonical writer, reads those exact bytes back with the strict reader,
compares the candidate with the published file (:mod:`..description_transition`), and only then
replaces the file atomically. Every refusal is reported as a set and writes nothing.

The user's re-confirmations that the candidate needed are appended, in the same publication, to
the append-only ``morphobricks.records.jsonl`` next to the file (one JSON object per line: old
ID, new ID, date and the SHA-256 of the file before and after). The file is never rewritten or
truncated; a later run copies it unchanged into its manifest. If the append fails after the file
was replaced, the previous file is put back, so the publication is whole or absent (a process
killed in the instant between the two steps can leave a published file without its records, and
the records file is then the one that cannot be trusted to be complete).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .._io import write_text_atomic
from ..description import Description, DescriptionError, description_from_bytes
from ..description_file import (
    DESCRIPTION_FILENAME,
    RECORDS_FILENAME,
    read_file_bytes,
    sha256_hex,
)
from ..description_report import DescriptionIssue, key_path
from ..description_rules import Refusal
from ..description_transition import (
    Reconfirmation,
    TransitionIssue,
    transition_issues,
    unused,
    unwaived,
)
from ..description_write import canonical_bytes
from .lock import output_lock

ABSENT = "absent"
"""The recorded base of a draft derived when no ``morphobricks.toml`` existed."""
BASE = re.compile(r"[0-9a-f]{64}")


class PublicationRefused(Exception):
    """Raised when a candidate is not published; ``issues`` is every refusal that applies."""

    def __init__(self, issues: tuple[DescriptionIssue, ...]) -> None:
        self.issues = issues
        self.codes = frozenset(issue.code for issue in issues)
        shown = "; ".join(f"{i.code.value} at {i.path}" for i in issues[:6])
        super().__init__(f"the description was not published: {shown}")


class RecordsFileError(OSError):
    """Raised when ``morphobricks.records.jsonl`` cannot be appended to as it stands."""


@dataclass(frozen=True)
class Publication:
    """A published description: the file, its SHA-256, the base it replaced, the records."""

    path: Path
    sha256: str
    base_sha256: str
    records: tuple[dict, ...]


def _refuse(code: Refusal, path: str, value: object = None) -> PublicationRefused:
    return PublicationRefused((DescriptionIssue(code, path, value),))


def _transition_refusal(issue: TransitionIssue) -> DescriptionIssue:
    """Name the table of a refusal: ``used_ids.<kind>`` for a registry rule, else the ID's own."""
    if issue.code in (Refusal.R_USED_ID_DROPPED, Refusal.R_USED_ID_REUSED):
        return DescriptionIssue(issue.code, f"used_ids.{issue.kind}", issue.old_id)
    return DescriptionIssue(issue.code, key_path(issue.kind, issue.old_id), issue.new_id)


def _check_base(base: str | None, current: bytes | None) -> str:
    """Return the current file's identity (hex SHA-256 or ``absent``) after checking the base."""
    if base is None or not (base == ABSENT or BASE.fullmatch(base)):
        raise _refuse(Refusal.R_DRAFT_BASE_MISSING, "base_sha256", base)
    now = ABSENT if current is None else sha256_hex(current)
    if now != base:
        raise _refuse(Refusal.R_PUBLISHED_FILE_CHANGED, DESCRIPTION_FILENAME, now)
    return now


def _record(item: Reconfirmation, old_sha: str, new_sha: str) -> dict:
    return {
        "type": "reconfirmation",
        "id_kind": item.kind,
        "old_id": item.old_id,
        "new_id": item.new_id,
        "confirmed_on": item.confirmed_on,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
    }


def _order(item: Reconfirmation) -> tuple[str, str, str, str]:
    return (item.kind, item.old_id, item.new_id or "", item.confirmed_on)


def read_records(output_folder: str | os.PathLike) -> tuple[dict, ...]:
    """Return the records written so far, in order (empty when there is no records file).

    Raises
    ------
    DescriptionError
        ``R_FILE_SYMLINK`` when the records file is a symbolic link.
    RecordsFileError
        When a line is not a JSON object or the last line is torn (no final newline).
    """
    data = read_file_bytes(Path(output_folder) / RECORDS_FILENAME)
    if not data:
        return ()
    if not data.endswith(b"\n"):
        raise RecordsFileError(f"{RECORDS_FILENAME} ends in the middle of a line")
    try:
        # Lines end at LF only (Spec 4.6): str.splitlines would also cut at U+2028, U+0085, ...
        rows = tuple(json.loads(line) for line in data.decode("utf-8").split("\n")[:-1])
    except ValueError as error:
        raise RecordsFileError(f"{RECORDS_FILENAME} holds a line that is not JSON") from error
    if not all(isinstance(row, dict) for row in rows):
        raise RecordsFileError(f"{RECORDS_FILENAME} holds a line that is not an object")
    return rows


def _append_records(folder: Path, records: tuple[dict, ...]) -> None:
    """Append the records as lines; the file is created if absent and never rewritten."""
    if not records:
        return
    read_records(folder)  # refuses a symbolic link and a torn or foreign file before writing
    lines = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    handle = os.open(folder / RECORDS_FILENAME, flags, 0o644)
    with os.fdopen(handle, "ab") as stream:
        stream.write(lines.encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def _compare(
    published: bytes | None,
    candidate: Description,
    confirmations: tuple[Reconfirmation, ...],
) -> None:
    """Refuse every ``R_*`` that applies, or a re-confirmation the candidate does not need."""
    old = description_from_bytes(published) if published is not None else None
    issues = transition_issues(old, candidate) if old is not None else ()
    left = unwaived(issues, confirmations)
    if left:
        raise PublicationRefused(tuple(_transition_refusal(i) for i in left))
    if unused(issues, confirmations):
        names = ", ".join(f"{c.kind} {c.old_id}" for c in unused(issues, confirmations))
        raise ValueError(f"a re-confirmation names a change the candidate does not make: {names}")


def publish_description(
    output_folder: str | os.PathLike,
    candidate: Description,
    *,
    base_sha256: str | None,
    confirmations: tuple[Reconfirmation, ...] = (),
) -> Publication:
    """Publish ``candidate`` as ``morphobricks.toml``, or refuse and write nothing.

    Parameters
    ----------
    output_folder
        The output folder (it must exist).
    candidate
        The description to publish. ``confirmed_on`` is written as it holds it.
    base_sha256
        The SHA-256 of the published bytes the draft was derived from, or ``"absent"``; None
        (a draft that recorded none) is ``R_DRAFT_BASE_MISSING``.
    confirmations
        The user's recorded re-confirmations of changed IDs; each must be needed.

    Returns
    -------
    Publication
        What was written.

    Raises
    ------
    OutputLockedError
        When a run holds the lock.
    PublicationRefused
        With every applicable refusal: ``R_FILE_SYMLINK`` (the file or the records file),
        ``R_DRAFT_BASE_MISSING``, ``R_PUBLISHED_FILE_CHANGED``, or the changed-ID, used-ID and
        ``confirmed_on`` refusals.
    DescriptionError
        When the candidate's canonical bytes, or the published file, is not a valid description.
    ValueError
        When a re-confirmation is not needed by the candidate.
    """
    folder = Path(output_folder)
    path = folder / DESCRIPTION_FILENAME
    with output_lock(folder):
        try:
            published = read_file_bytes(path)
            read_records(folder)  # a symbolic or torn records file refuses before anything
        except DescriptionError as error:
            raise PublicationRefused(error.issues) from error
        base = _check_base(base_sha256, published)
        data = canonical_bytes(candidate)
        description_from_bytes(data)  # the exact bytes to be written pass the strict reader
        _compare(published, candidate, confirmations)
        new_sha = sha256_hex(data)
        records = tuple(_record(c, base, new_sha) for c in sorted(confirmations, key=_order))
        write_text_atomic(path, data.decode("utf-8"))
        try:
            _append_records(folder, records)
        except BaseException:
            _restore(path, published)
            raise
        return Publication(path, new_sha, base, records)


def _restore(path: Path, published: bytes | None) -> None:
    """Put the previous published file back (or remove the new one when there was none)."""
    if published is None:
        path.unlink(missing_ok=True)
    else:
        write_text_atomic(path, published.decode("utf-8"))
