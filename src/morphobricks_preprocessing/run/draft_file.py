"""``morphobricks.draft.json`` in the output folder: save, resume and publish (experiment-v1 12).

The draft file holds the user's answers so far (:mod:`..draft_answers`, bytes in
:mod:`..draft_format`), the SHA-256 of the published ``morphobricks.toml`` it was derived from
(or ``absent``) and the pinned Spec commit. It is never named ``morphobricks.toml`` and no
consumer reads it as a description.

- :func:`save_draft` writes it atomically under :func:`.lock.output_lock`, as the design CSV is
  saved (:func:`.design_file.write_design`), once the answers pass the rules: an answer the draft
  refuses is never saved, so the file on disk is always one that :func:`load_draft` accepts
  (against the same folder).
- :func:`load_draft` resumes: the file is read strictly and refused when anything is wrong, and
  refused with ``spec_commit_mismatch`` when it was written under another pinned Spec commit
  (its confirmations were given under other rules) and with ``R_PUBLISHED_FILE_CHANGED`` when
  the published file is no longer the base the draft recorded. The refused draft is never merged
  or repaired: it is derived again from the folders and the current file.
- :func:`publish_draft` builds the description from the draft file
  (:func:`..draft_answers.description_from_draft`: every confirmation or nothing), publishes it
  with the checks of :func:`.description_file.publish_locked`, and deletes the draft file, all
  under one hold of the lock. Any refusal writes nothing: the draft and the published file stay
  byte for byte as they were. If the draft file cannot be deleted after a successful
  publication, the error is raised and the publication stands; the draft then records a base
  that no longer matches, so it is refused when resumed.

Nothing here confirms anything or reads the clock: every date is the user's, in the answers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .._io import write_text_atomic
from ..description import DescriptionError
from ..description_file import DESCRIPTION_FILENAME, DRAFT_FILENAME, read_file_bytes, sha256_hex
from ..description_transition import Reconfirmation
from ..draft import Draft
from ..draft_answers import DraftAnswers, apply_answers, description_from_draft
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


def save_draft(output_folder: str | os.PathLike, draft: Draft, answers: DraftAnswers) -> Path:
    """Save the answers as ``morphobricks.draft.json``, holding the output folder's lock.

    The answers are applied to ``draft`` first and the exact bytes are read back with the strict
    reader, so a draft that would be refused on resume is refused here and nothing is written.

    Parameters
    ----------
    output_folder
        The output folder; created if needed.
    draft
        The draft derived from the folder (discovery or a legacy design), without the answers.
    answers
        All the user's answers so far (the file is replaced, not merged).

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
    """
    folder = Path(output_folder).resolve()
    refuse_output_inside_masks(folder)
    apply_answers(draft, answers)
    data = answers_to_bytes(answers)
    _require_current_spec(answers_from_bytes(data))
    folder.mkdir(parents=True, exist_ok=True)
    with output_lock(folder):
        _read_draft_bytes(folder)  # a symbolic link is refused, never replaced
        return write_text_atomic(folder / DRAFT_FILENAME, data.decode("utf-8"))


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
        The draft derived again from the folder (or the legacy design) now.

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
    confirmations: tuple[Reconfirmation, ...] = (),
) -> Publication:
    """Publish the saved draft as ``morphobricks.toml`` and delete the draft, or refuse.

    Under the output folder's lock: the draft file is resumed (:func:`load_draft`), the
    description built from it (every confirmation present or :class:`DraftIncomplete`), published
    with all the checks of :func:`.description_file.publish_description`, and then the draft file
    is deleted. Nothing is written on any refusal.

    Raises
    ------
    OutputLockedError
        If a run holds the lock.
    DraftFileError
        ``no_draft`` when no draft is saved, or the file is refused (see :func:`load_draft`).
    DraftIncomplete
        Listing every missing confirmation.
    PublicationRefused, DescriptionError, ValueError
        As :func:`.description_file.publish_description`.
    """
    folder = Path(output_folder)
    with output_lock(folder):
        resumed = _resume(folder, draft)
        candidate = description_from_draft(draft, resumed.answers)
        publication = publish_locked(
            folder, candidate, base_sha256=resumed.answers.base_sha256, confirmations=confirmations
        )
        (folder / DRAFT_FILENAME).unlink()
        return publication
