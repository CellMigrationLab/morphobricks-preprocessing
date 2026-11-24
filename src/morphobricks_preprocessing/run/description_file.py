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
copies it unchanged into its manifest.

With ``write_design`` (the notebook and CLI publications), the derived ``experiment_design.csv``
(experiment-v1 section 11) is published with them (issue #400). It is derived and checked by the
design writer before anything is written, so a design the writer refuses writes nothing.

Every file of a publication (the description, the whole new records file, the design) is first
written in full to a temporary file beside it and synced; only once all are on disk are they
renamed into place, one after the other. A write that fails (a full disk, permissions) therefore
fails before any published file changes, and the temporary files are removed. A rename
allocates no space; if one still fails, the files already renamed are put back byte for byte
from the bytes read at the start (with no decoding), so the publication is whole or absent.
This holds for an exception in the process. A process killed between two renames can still
leave them unmatched (then the records file is the one that cannot be trusted to be complete),
and a put-back that itself fails is added as a note to the error raised.

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

from .._io import give_output_mode
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
from ..design import DESIGN_FILENAME
from ..design_derive import derived_design_bytes
from ..records_file import CODE as RECORDS_FILE_INVALID
from ..records_file import RecordsIssue, check_records, encode_record
from .description_records import (
    FolderIdChange,
    folder_id_changes,
    image_count_records,
    removal_records,
)
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


MODE_SWITCH_UNCONFIRMED = "mode_switch_unconfirmed"
"""Preprocessing's refusal of a publication that switches between fast and strict mode
(Spec ADR 0006) without the user's recorded switch: no reader or reconciliation switches."""


class ModeSwitchRefused(ValueError):
    """Raised when a candidate changes the mode of the published file without the user's switch.

    Experiment-v1 section 10 (ADR 0006): adopting fast mode on a strict file, or giving it up,
    is a user action published as a new file; a candidate whose mode differs from the
    published one is refused unless the user's switch is recorded (``switch_mode``), and a
    recorded switch that changes nothing is refused too. Nothing is written.
    """

    code = MODE_SWITCH_UNCONFIRMED


class FolderIdRefused(ValueError):
    """Raised when a candidate changes the folder and the ID of one entity in one save (#475).

    Neither is guessed from the other: ``changes`` names each entity, its folders and the IDs
    the candidate holds (:func:`.description_records.folder_id_changes`), and ``codes`` their
    codes (``folder_and_id_changed``, ``removal_mismatch``). Nothing is written.
    """

    def __init__(self, changes: tuple[FolderIdChange, ...]) -> None:
        self.changes = changes
        self.codes = frozenset(c.code for c in changes)
        shown = " ".join(f"{c.code}: {c.message} {c.action}" for c in changes)
        super().__init__(f"the description was not published: {shown}")


def check_mode_switch(old: Description | None, new: Description, switch_mode: bool) -> None:
    """Refuse an unrecorded switch between fast and strict mode, or a switch that is none.

    Raises
    ------
    ModeSwitchRefused
        When ``old`` is a fast-mode file and ``new`` a strict one (or the reverse) and
        ``switch_mode`` is False, or when ``switch_mode`` is True and the mode is unchanged
        (or there is no published file).
    """
    changed = old is not None and old.fast_mode != new.fast_mode
    if changed and not switch_mode:
        before, after = ("fast", "strict") if old.fast_mode else ("strict", "fast")
        raise ModeSwitchRefused(
            f"the published description is in {before} mode and this one in {after} mode, but "
            "no switch of mode was recorded: only you switch it (tick or untick “every imaging "
            "unit has the same channels in the same order”, then save again)"
        )
    if switch_mode and not changed:
        raise ModeSwitchRefused("a switch of mode was recorded, but the mode does not change")


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


def _prepare_file(path: Path, data: bytes) -> str:
    """Write ``data`` to a synced temporary file beside ``path``, with its mode; return its name.

    On any failure the temporary file is removed and the error raised; ``path`` is untouched.
    """
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        give_output_mode(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return temporary


def _replace_file_durably(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data``: a synced temporary file, then one atomic replace."""
    temporary = _prepare_file(path, data)
    try:
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
    switch_mode: bool = False,
    write_design: bool = False,
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
    switch_mode
        True only for the user's recorded switch between fast and strict mode (ADR 0006); see
        :func:`check_mode_switch`.
    write_design
        Also publish the derived ``experiment_design.csv`` (experiment-v1 section 11) with the
        description and records: all are written to temporary files before any is renamed into
        place (module docstring).

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
        ``confirmed_on`` refusals; with ``write_design``, also ``R_FILE_SYMLINK`` for a
        symbolic-link ``experiment_design.csv`` (it could not be put back as it was).
    DesignFormatError
        With ``write_design``, when the design writer refuses the derived design; nothing is
        written.
    OSError
        When a write fails (nothing published changes; the temporary files are removed), or a
        rename fails (the files already renamed are put back byte for byte; a put-back that
        fails is named in a note on the error).
    DescriptionError
        When the candidate's canonical bytes, or the published file, is not a valid description.
    ValueError
        When a re-confirmation is not needed by the candidate or ``recorded_on`` is missing when
        needed; :class:`FolderIdRefused` (coded) when the folder and the ID of one entity change
        together, a dropped unit has no removal or a removal names a unit the candidate keeps;
        :class:`ModeSwitchRefused` for a switch of mode that is not the user's.
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
            switch_mode=switch_mode,
            write_design=write_design,
        )


def publish_locked(
    folder: Path,
    candidate: Description,
    *,
    base_sha256: str | None,
    confirmations: tuple[Reconfirmation, ...] = (),
    removals: tuple[Removal, ...] = (),
    recorded_on: str | None = None,
    switch_mode: bool = False,
    write_design: bool = False,
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
        design_before = read_file_bytes(folder / DESIGN_FILENAME) if write_design else None
    except DescriptionError as error:
        raise PublicationRefused(error.issues) from error
    base = check_base(base_sha256, published)
    data = canonical_bytes(candidate)
    description_from_bytes(data)  # the exact bytes to be written pass the strict reader
    old = _compare(published, candidate, confirmations)
    check_mode_switch(old, candidate, switch_mode)
    new_sha = sha256_hex(data)
    records = tuple(_record(c, base, new_sha) for c in sorted(confirmations, key=_order))
    if old is not None:
        changes = folder_id_changes(old, candidate, removals, confirmations)
        if changes:
            raise FolderIdRefused(changes)
        records += removal_records(old, candidate, removals, confirmations)
        records += image_count_records(old, candidate, recorded_on, confirmations)
    elif removals:
        raise ValueError("a removal needs a published file to remove from")
    files = [(path, data, published)]
    if records:  # a record the reader refuses, or a design the writer refuses: nothing written
        files.append(
            (folder / RECORDS_FILENAME, _candidate_records(folder, records), records_before)
        )
    if write_design:
        files.append((folder / DESIGN_FILENAME, derived_design_bytes(candidate), design_before))
    _publish_files(tuple(files))
    return Publication(path, new_sha, base, records)


def _publish_files(files: tuple[tuple[Path, bytes, bytes | None], ...]) -> None:
    """Write every ``(path, new, before)`` to a synced temporary file, then rename each in place.

    A failed write removes the temporary files and changes no published file. A failed rename
    also puts back the previous bytes (``before``) of every file already renamed.
    """
    temporaries: list[str] = []
    renaming = False
    try:
        for target, new, _ in files:
            temporaries.append(_prepare_file(target, new))
        renaming = True
        for temporary, (target, _, _) in zip(temporaries, files, strict=True):
            os.replace(temporary, target)
    except BaseException as error:
        # The put-back comes first, so a temporary file that cannot be removed never skips it;
        # each failure here is a note on the original error, which is the one raised.
        if renaming:
            for target, new, before in reversed(files):
                try:
                    _restore(target, new, before)
                except OSError as failure:
                    error.add_note(f"{target.name} could not be put back: {failure}")
        for temporary in temporaries:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError as failure:
                error.add_note(f"{temporary} could not be removed: {failure}")
        raise


def _restore(path: Path, new: bytes, before: bytes | None) -> None:
    """Put the previous bytes of ``path`` back, unless it was not replaced; never decode them.

    The file is removed when there was none. A file whose bytes are not ``new`` was not
    replaced and is left as it is.
    """
    try:
        now = path.read_bytes()
    except FileNotFoundError:
        now = None
    if now != new or new == before:
        return
    if before is None:
        path.unlink()
    else:
        _replace_file_durably(path, before)
