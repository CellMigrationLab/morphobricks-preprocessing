"""Publishing ``morphobricks.toml`` under the output folder's lock (experiment-v1 sections 4.6, 10).

``publish_description`` is the only writer of the published file. Under
:func:`.lock.output_lock` it refuses a symbolic link, checks that the file on disk is still the
base the draft was derived from (the SHA-256 of its exact bytes, or ``absent``), writes the
candidate with the canonical writer, reads those exact bytes back with the strict reader,
compares the candidate with the published file (:mod:`..description_transition`), and only then
replaces the file atomically. Every refusal is reported as a set and writes nothing.

The user's re-confirmations that the candidate needed, a ``removed`` record for every removal
the user confirmed and an ``image_count_changed`` record for a changed image count
(:mod:`.description_records`) are appended, in the same publication, to
the append-only ``morphobricks.records.jsonl`` next to the file (one JSON object per line, of one
of the three Spec record types, each with its fixed key set). The records are never edited or
dropped: the new file is the old bytes plus the new lines, written in full to a temporary file and
put in place with one atomic replace, so the live records file is never half written. A later run
copies it unchanged into its manifest. If any step after the description was replaced fails, the
previous description and the previous records file are put back, so the publication is whole or
absent (a process killed in the instant between the two replaces can leave a published file
without its records, and the records file is then the one that cannot be trusted to be complete).

The hashes in a record are history, not a check on the file now published: later publications
change the file without a record, so an older record rightly names a hash that is no longer the
published one. A reader never compares them with the published file and never drops or trusts a
record because they match or differ; it reads the records in order, unchanged.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .._io import give_output_mode, write_text_atomic
from ..description import Description, DescriptionError, description_from_bytes
from ..description_file import (
    DESCRIPTION_FILENAME,
    RECORDS_FILENAME,
    read_file_bytes,
    sha256_hex,
)
from ..description_removal import Removal
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
from ..records_file import CODE as RECORDS_FILE_INVALID
from ..records_file import RecordsIssue, check_records, encode_record
from .description_records import image_count_records, removal_records
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
    """Raised when ``morphobricks.records.jsonl`` is refused (``R_RECORDS_FILE_INVALID``).

    ``code`` is the catalogue code, ``issues`` every refusal and ``reasons`` their distinct
    ``E_RECORDS_*`` names.
    """

    def __init__(self, issues: tuple[RecordsIssue, ...]) -> None:
        self.code = RECORDS_FILE_INVALID
        self.issues = issues
        self.reasons = frozenset(issue.reason for issue in issues)
        shown = "; ".join(f"{i.reason} at line {i.line}" for i in issues[:6])
        super().__init__(f"{RECORDS_FILENAME} is refused ({self.code}): {shown}")


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


def check_base(base: str | None, current: bytes | None) -> str:
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
        ``R_RECORDS_FILE_INVALID`` (section 4.6) for every file the Spec's reference validator
        refuses; ``reasons`` lists the ``E_RECORDS_*`` names, the message the first line of each.
    """
    data = read_file_bytes(Path(output_folder) / RECORDS_FILENAME)
    records, issues = check_records(data or b"")
    if issues:
        raise RecordsFileError(issues)
    return records


def _replace_file_durably(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``: a synced temporary file, then one atomic replace."""
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        give_output_mode(temporary, path)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _candidate_records(folder: Path, records: tuple[dict, ...]) -> bytes:
    """Return the records file's bytes with ``records`` added, checked as the reader checks them.

    Raises
    ------
    RecordsFileError
        ``R_RECORDS_FILE_INVALID`` when the existing file (a symbolic link, a torn or foreign
        file) or the complete candidate bytes would be refused by the reader: a new record with
        a wrong key set, a bad date or a malformed SHA-256 is never written.
    """
    read_records(folder)  # refuses a symbolic link and a torn or foreign file before writing
    current = read_file_bytes(folder / RECORDS_FILENAME) or b""
    data = current + b"".join(encode_record(r) for r in records)
    _, issues = check_records(data)
    if issues:
        raise RecordsFileError(issues)
    return data


def _append_records(folder: Path, records: tuple[dict, ...]) -> None:
    """Add the records as lines after the existing ones, in one atomic replace of the file.

    The complete new bytes are checked first (:func:`_candidate_records`); the live file is
    replaced only once the old bytes and the new lines are all on disk in a temporary file, so a
    refusal or a failure leaves it exactly as it was.
    """
    if not records:
        return
    _replace_file_durably(folder / RECORDS_FILENAME, _candidate_records(folder, records))


def transition_refusals(
    old: Description | None,
    candidate: Description,
    confirmations: tuple[Reconfirmation, ...] = (),
) -> tuple[DescriptionIssue, ...]:
    """Return the ``R_*`` refusals of ``candidate`` over the published ``old`` (none without one).

    These are exactly the transition refusals publication raises (sections 4.4a, 4.6, 4.9 and
    10) once the user's re-confirmations accepted what they name: the notebook's pre-save check
    calls this too, so Save is enabled only for a candidate that publication accepts.
    """
    if old is None:
        return ()
    left = unwaived(transition_issues(old, candidate), confirmations)
    return tuple(_transition_refusal(i) for i in left)


def _compare(
    published: bytes | None,
    candidate: Description,
    confirmations: tuple[Reconfirmation, ...],
) -> Description | None:
    """Refuse every ``R_*`` that applies, or a re-confirmation the candidate does not need.

    Returns the published description (None when there was none), parsed once.
    """
    old = description_from_bytes(published) if published is not None else None
    issues = transition_issues(old, candidate) if old is not None else ()
    left = transition_refusals(old, candidate, confirmations)
    if left:
        raise PublicationRefused(left)
    if unused(issues, confirmations):
        names = ", ".join(f"{c.kind} {c.old_id}" for c in unused(issues, confirmations))
        raise ValueError(f"a re-confirmation names a change the candidate does not make: {names}")
    return old


def publish_description(
    output_folder: str | os.PathLike,
    candidate: Description,
    *,
    base_sha256: str | None,
    confirmations: tuple[Reconfirmation, ...] = (),
    removals: tuple[Removal, ...] = (),
    recorded_on: str | None = None,
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
    removals
        The user's confirmations of the removed units (each with its date). Every unit the
        candidate drops must be named by one; each is written as a ``removed`` record, with the
        records of the condition, repeat and technical repeat that no longer exist.
    recorded_on
        The date (``YYYY-MM-DD``) of the ``image_count_changed`` records: required when any
        confirmed member's ``image_count`` changes, alone or with other changes; never
        defaulted and never read from the clock.

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
        When a re-confirmation is not needed by the candidate, a dropped unit has no removal, a
        removal names a unit the candidate keeps, or ``recorded_on`` is missing when needed.
    """
    folder = Path(output_folder)
    with output_lock(folder):
        return publish_locked(
            folder,
            candidate,
            base_sha256=base_sha256,
            confirmations=confirmations,
            removals=removals,
            recorded_on=recorded_on,
        )


def publish_locked(
    folder: Path,
    candidate: Description,
    *,
    base_sha256: str | None,
    confirmations: tuple[Reconfirmation, ...] = (),
    removals: tuple[Removal, ...] = (),
    recorded_on: str | None = None,
) -> Publication:
    """Publish like :func:`publish_description` for a caller that already holds the lock.

    The caller holds :func:`.lock.output_lock` on ``folder`` for the whole call (the lock is not
    re-entrant), so it can do more under the same lock, such as deleting the draft that was
    published (:mod:`.draft_file`). The refusals and effects are those of
    :func:`publish_description`.
    """
    path = folder / DESCRIPTION_FILENAME
    try:
        published = read_file_bytes(path)
        read_records(folder)  # a symbolic or torn records file refuses before anything
        records_before = read_file_bytes(folder / RECORDS_FILENAME)
    except DescriptionError as error:
        raise PublicationRefused(error.issues) from error
    base = check_base(base_sha256, published)
    data = canonical_bytes(candidate)
    description_from_bytes(data)  # the exact bytes to be written pass the strict reader
    old = _compare(published, candidate, confirmations)
    new_sha = sha256_hex(data)
    records = tuple(_record(c, base, new_sha) for c in sorted(confirmations, key=_order))
    if old is not None:
        records += removal_records(old, candidate, removals, confirmations)
        records += image_count_records(old, candidate, recorded_on, confirmations)
    elif removals:
        raise ValueError("a removal needs a published file to remove from")
    if records:
        _candidate_records(folder, records)  # a record the reader refuses: nothing is written
    write_text_atomic(path, data.decode("utf-8"))
    try:
        _append_records(folder, records)
    except BaseException:
        _restore(path, published)
        _restore(folder / RECORDS_FILENAME, records_before)
        raise
    return Publication(path, new_sha, base, records)


def _restore(path: Path, before: bytes | None) -> None:
    """Put the previous bytes of ``path`` back (or remove the file when there was none)."""
    if before is None:
        path.unlink(missing_ok=True)
    else:
        write_text_atomic(path, before.decode("utf-8"))
