"""``morphobricks.draft.json`` in the output folder: save, resume and publish (experiment-v1 12).

The draft file holds the user's answers so far (:mod:`..draft_answers`, bytes in
:mod:`..draft_format`), the SHA-256 of the published ``morphobricks.toml`` it was derived from
(or ``absent``) and the pinned Spec commit. It is never named ``morphobricks.toml`` and no
consumer reads it as a description.

- :func:`save_draft` writes it atomically under :func:`.lock.output_lock`, as the design CSV is
  saved (:func:`.design_file.write_design`), once the answers pass the rules: an answer the draft
  refuses is never saved, so the file on disk is always one that :func:`load_draft` accepts
  (against the same folder). Given the draft's generation the writer last read or wrote
  (``expected``, :func:`draft_generation`), it replaces the file only while the file is still
  that generation, checked under the same hold of the lock as the replacement; otherwise
  :class:`DraftChanged` is raised and nothing is written.
- :func:`draft_generation` is the SHA-256 of the file's bytes (:data:`NO_DRAFT` without one);
  :func:`answers_generation` is the generation :func:`save_draft` writes for given answers.
- :func:`load_draft` resumes: the file is read strictly and refused when anything is wrong, and
  refused with ``spec_commit_mismatch`` when it was written under another pinned Spec commit
  (its confirmations were given under other rules) and with ``R_PUBLISHED_FILE_CHANGED`` when
  the published file is no longer the base the draft recorded. The refused draft is never merged
  or repaired: it is derived again from the folders and the current file.
- :func:`publish_draft` builds the description from the draft file
  (:func:`..draft_answers.description_from_draft`: every confirmation or nothing), publishes it
  with the checks of :func:`.description_file.publish_locked` together with its derived
  ``experiment_design.csv``, and deletes the draft file, all under one hold of the lock (and,
  given ``expected``, only while the draft file is still that generation). Any
  refusal writes nothing, and a failed write of the description, its records or its design
  changes none of them (issue #400; :mod:`.description_file` says how, and its limits): the
  draft, the published file and the design stay byte for byte as they were, so the draft can be
  published again. If the draft file cannot be deleted
  after a successful publication, the error is raised and the publication stands (its
  description and design match); the draft then records a base that no longer matches, so it
  is refused when resumed.

Nothing here confirms anything or reads the clock: every date is the user's, in the answers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .._io import write_text_atomic
from ..description import DescriptionError
from ..description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME, read_file_bytes, sha256_hex
from ..description_removal import Removal
from ..description_transition import Reconfirmation
from ..draft import Draft
from ..draft_answers import DraftAnswers, UnitKey, apply_answers, description_from_draft
from ..draft_format import (
    ABSENT,
    SPEC_COMMIT,
    DraftFileError,
    answers_from_bytes,
    answers_to_bytes,
)
from .description_file import Publication, PublicationRefused, check_base, publish_locked
from .lock import output_lock
from .output_guard import refuse_output_inside_masks

NO_DRAFT = "no draft"
"""The generation of an output folder without a draft file (:func:`draft_generation`)."""

UNREAD = "unread"
"""A generation no file has: a writer that cannot tell which draft it holds writes nothing."""

UNUSED_PREFIX = "morphobricks.draft.unused-"
"""The name of a copy of a draft that could not be used, before ``N.json`` (:func:`save_draft`)."""


class UnusedDraftNotKept(OSError):
    """The draft to be replaced could not be copied aside; nothing was written."""


class DraftChanged(DraftFileError):
    """The draft file is no longer the generation the writer read or wrote; nothing written.

    External re-check av (24 November 2026): two notebooks open on the same output folder each
    hold the whole draft as they read it. The lock of the output folder makes their writes one
    at a time but not current: the second wrote its stale answers over the first's saved
    answers (a refusal, a confirmation, tags). A writer now names the generation it holds, and
    the file is replaced only while it is still that one.
    """

    def __init__(self, folder: Path) -> None:
        super().__init__(
            f"{folder / DRAFT_FILENAME} was changed since this notebook read or saved it "
            "(another notebook or program saved its own answers there). Nothing was saved, "
            "so the answers saved there are kept: check the folder again (step 1, Check "
            "folder) to read them, then give your change again",
            "R_DRAFT_CHANGED",
        )


@dataclass(frozen=True)
class ResumedDraft:
    """A draft file read and applied: the draft with the answers in it, and the answers."""

    draft: Draft
    answers: DraftAnswers


def published_base(output_folder: str | os.PathLike) -> str:
    """Return the base a new draft records: the SHA-256 of ``morphobricks.toml``, or ``absent``.

    Raises
    ------
    DescriptionError
        ``R_FILE_SYMLINK`` when the published path is a symbolic link.
    """
    current = read_file_bytes(Path(output_folder) / DESCRIPTION_FILENAME)
    return ABSENT if current is None else sha256_hex(current)


def new_answers(output_folder: str | os.PathLike) -> DraftAnswers:
    """Return empty answers based on the file published now (or ``absent``), pinned to the Spec."""
    return DraftAnswers(published_base(output_folder), SPEC_COMMIT)


def _read_draft_bytes(folder: Path) -> bytes | None:
    try:
        return read_file_bytes(folder / DRAFT_FILENAME)
    except DescriptionError as error:
        raise DraftFileError(
            f"{DRAFT_FILENAME} is a symbolic link; it is never followed", "R_FILE_SYMLINK"
        ) from error


def _require_current_spec(answers: DraftAnswers) -> None:
    """Refuse answers recorded under another pinned Spec commit; never rewrite the commit."""
    if answers.spec_commit != SPEC_COMMIT:
        raise DraftFileError(
            f"the draft was written under Spec commit {answers.spec_commit}, but this version is "
            f"pinned to {SPEC_COMMIT}; its answers are not carried over. The published "
            f"{DESCRIPTION_FILENAME} is untouched and the draft file is left as it is: start a "
            "fresh draft from the folders",
            "spec_commit_mismatch",
        )


def draft_generation(output_folder: str | os.PathLike) -> str:
    """Return the draft file's generation: the SHA-256 of its bytes, or :data:`NO_DRAFT`.

    Raises
    ------
    DraftFileError
        ``R_FILE_SYMLINK`` when the draft path is a symbolic link.
    OSError
        When the file cannot be read.
    """
    data = _read_draft_bytes(Path(output_folder).resolve())
    return NO_DRAFT if data is None else sha256_hex(data)


def answers_generation(answers: DraftAnswers) -> str:
    """Return the generation of the draft file :func:`save_draft` writes for ``answers``."""
    return sha256_hex(answers_to_bytes(answers))


def _require_generation(folder: Path, data: bytes | None, expected: str | None) -> None:
    """Refuse a write over a draft file that is not the ``expected`` generation (None: any)."""
    if expected is not None and (NO_DRAFT if data is None else sha256_hex(data)) != expected:
        raise DraftChanged(folder)


def save_draft(
    output_folder: str | os.PathLike,
    draft: Draft,
    answers: DraftAnswers,
    *,
    expected: str | None = None,
    keep_previous: bool = False,
) -> Path:
    """Save the answers as ``morphobricks.draft.json``, holding the output folder's lock.

    The answers are applied to ``draft`` first and the exact bytes are read back with the strict
    reader, so a draft that would be refused on resume is refused here and nothing is written.
    With ``expected`` the file is replaced only while it is that generation
    (:func:`draft_generation`), read under the same hold of the lock as the replacement: the
    notebook names the draft it read or last wrote, so answers held from an older draft are
    never written over a newer one. The generation written is :func:`answers_generation`.

    Parameters
    ----------
    output_folder
        The output folder; created if needed.
    draft
        The draft derived from the folder, without the answers.
    answers
        All the user's answers so far (the file is replaced, not merged).
    expected
        The generation the writer holds (:data:`NO_DRAFT`: no file); None checks nothing (a
        writer that read no draft before, as the tests set one up).
    keep_previous
        Copy the draft file being replaced (one the notebook could not use) to the first free
        ``morphobricks.draft.unused-N.json`` first, never over an existing copy. The copy is made
        under the same hold of the lock, after the ``expected`` check: a refused save copies
        nothing (external re-check ay, 24 November 2026: the copy was made before the check, so
        a stale notebook copied another notebook's newer draft and then was refused).

    Returns
    -------
    Path
        The file written.

    Raises
    ------
    OutputLockedError
        If a run holds the lock; nothing is written.
    OutputInsideMasksError
        If the run in the output folder recorded a masks folder that holds it.
    DraftError
        If the draft refuses an answer.
    DraftFileError
        If the answers cannot be a draft file, the draft path is a symbolic link, or the answers
        carry a ``spec_commit`` other than the pinned one (``spec_commit_mismatch``).
    DraftChanged
        ``R_DRAFT_CHANGED``: the file is not the ``expected`` generation.
    UnusedDraftNotKept
        With ``keep_previous``, when the copy cannot be written; the draft is not replaced.
    """
    folder = Path(output_folder).resolve()
    refuse_output_inside_masks(folder)
    apply_answers(draft, answers)
    data = answers_to_bytes(answers)
    _require_current_spec(answers_from_bytes(data))
    folder.mkdir(parents=True, exist_ok=True)
    with output_lock(folder):
        # a symbolic link is refused, never replaced; another generation is never replaced
        current = _read_draft_bytes(folder)
        _require_generation(folder, current, expected)
        if keep_previous and current is not None:
            _keep_aside(folder, current)
        return write_text_atomic(folder / DRAFT_FILENAME, data.decode("utf-8"))


def _keep_aside(folder: Path, data: bytes) -> Path:
    """Write ``data`` to the first free ``morphobricks.draft.unused-N.json``; never overwrite."""
    number = 1
    while True:
        copy = folder / f"{UNUSED_PREFIX}{number}.json"
        try:
            stream = copy.open("xb")  # exclusive: an existing copy is never replaced
        except FileExistsError:
            number += 1
            continue
        except OSError as error:
            raise UnusedDraftNotKept(f"{copy} could not be written: {error}") from error
        try:
            with stream:
                stream.write(data)
        except BaseException as error:
            copy.unlink(missing_ok=True)  # never a partial copy
            if isinstance(error, OSError):
                raise UnusedDraftNotKept(f"{copy} could not be written: {error}") from error
            raise
        return copy


def _read_answers(folder: Path) -> DraftAnswers:
    """Read the saved answers strictly and check the Spec commit and the base; apply nothing."""
    data = _read_draft_bytes(folder)
    if data is None:
        raise DraftFileError(f"{folder / DRAFT_FILENAME} does not exist", "no_draft")
    answers = answers_from_bytes(data)
    _require_current_spec(answers)
    try:
        check_base(answers.base_sha256, read_file_bytes(folder / DESCRIPTION_FILENAME))
    except DescriptionError as error:
        raise PublicationRefused(error.issues) from error
    return answers


def read_answers(output_folder: str | os.PathLike) -> DraftAnswers | None:
    """Return the saved answers as the file holds them, or None when none is saved.

    The same refusals as :func:`load_draft` (malformed file, other Spec commit, changed
    published file) but the answers are not applied to a draft: the caller, which knows the
    folders as they are now, decides which answers still apply (the notebook keeps those of
    repeats that still exist and says which it dropped).
    """
    folder = Path(output_folder)
    return None if _read_draft_bytes(folder) is None else _read_answers(folder)


def _resume(folder: Path, draft: Draft) -> ResumedDraft:
    answers = _read_answers(folder)
    return ResumedDraft(apply_answers(draft, answers), answers)


def load_draft(output_folder: str | os.PathLike, draft: Draft) -> ResumedDraft | None:
    """Resume the saved draft: its answers applied to ``draft``, or None when none is saved.

    Parameters
    ----------
    output_folder
        The output folder.
    draft
        The draft derived again from the folder now.

    Raises
    ------
    DraftFileError
        For a file that is malformed, of an unknown version, has a key the format does not have,
        or has no usable base (``R_DRAFT_BASE_MISSING``); a symbolic link; or was written under
        a Spec commit other than the pinned one (``spec_commit_mismatch``: nothing is carried over,
        the draft file and the published file are left untouched, a fresh draft is derived).
    PublicationRefused
        ``R_PUBLISHED_FILE_CHANGED`` when ``morphobricks.toml`` is no longer the recorded base.
    DraftError
        When ``draft`` does not have a key the answers name, or refuses an answer.
    """
    folder = Path(output_folder)
    if _read_draft_bytes(folder) is None:
        return None
    return _resume(folder, draft)


def publish_draft(
    output_folder: str | os.PathLike,
    draft: Draft,
    *,
    channel_counts: Mapping[UnitKey, int],
    confirmations: tuple[Reconfirmation, ...] = (),
    removals: tuple[Removal, ...] = (),
    recorded_on: str | None = None,
    expected: str | None = None,
) -> Publication:
    """Publish the saved draft as ``morphobricks.toml``, derive the CSV, delete the draft.

    Under the output folder's lock: the draft file is resumed (:func:`load_draft`), the
    description built from it (every confirmation present or :class:`DraftIncomplete`), published
    with all the checks of :func:`.description_file.publish_description`, then the derived
    ``experiment_design.csv`` is written in the same publication (experiment-v1 section 11: it is
    written only here, from the published file; if it cannot be written, the description,
    records and design stay as they were) and the draft file is deleted. Nothing is
    written on any refusal.
    ``removals`` and ``recorded_on`` are the user's, as :func:`.description_file.publish_locked`
    takes them (a removed folder, a changed image count).

    ``channel_counts`` are the channel counts of each unit's image now, from its header read
    through the axis roles the user confirmed (one entry per unit). It is required: a unit
    whose saved tags are not one per channel, or that has no count, is refused
    (:func:`.draft_answers.missing_confirmations`), so tags saved for another image are never
    published.

    ``expected`` is the generation of the draft the caller checked (:func:`save_draft`): the
    file is published only while it is that one, so a draft another notebook saved since is
    never published on the strength of this one's check. None checks nothing.

    Raises
    ------
    OutputLockedError
        If a run holds the lock.
    DraftChanged
        ``R_DRAFT_CHANGED``: the file is not the ``expected`` generation; nothing is written.
    DraftFileError
        ``no_draft`` when no draft is saved, or the file is refused (see :func:`load_draft`).
    DraftIncomplete
        Listing every missing confirmation.
    PublicationRefused, DescriptionError, ValueError
        As :func:`.description_file.publish_description`.
    """
    folder = Path(output_folder)
    with output_lock(folder):
        _require_generation(folder, _read_draft_bytes(folder), expected)
        resumed = _resume(folder, draft)
        candidate = description_from_draft(draft, resumed.answers, channel_counts=channel_counts)
        publication = publish_locked(
            folder,
            candidate,
            base_sha256=resumed.answers.base_sha256,
            confirmations=confirmations,
            removals=removals,
            recorded_on=recorded_on,
            switch_mode=resumed.answers.switch_mode,
            write_design=True,
        )
        (folder / DRAFT_FILENAME).unlink()
        return publication
