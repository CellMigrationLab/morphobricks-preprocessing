"""Step 1's saved draft: resume the answers that still apply, keep one that cannot be used.

Part of the description cards (:mod:`.notebook_description`). The saved
``morphobricks.draft.json`` is read as the file holds it and reconciled with the folders and the
design's pairing as they are now (:func:`.draft_answers.carry_over`): answers for what still
exists are kept exactly, the others are dropped and named in the card. A draft that cannot be
read or applied at all is never overwritten: the next save copies it aside first
(:func:`.run.draft_file.save_draft` with ``keep_previous``, after its generation check).
"""

from __future__ import annotations

from pathlib import Path

from . import notebook_style as ui
from .description import DescriptionError
from .description_file import DRAFT_FILENAME
from .draft import DraftError
from .draft_answers import DraftAnswers, apply_answers, carry_over
from .draft_format import ABSENT, SPEC_COMMIT, DraftFileError
from .run.description_file import PublicationRefused
from .run.draft_file import UNUSED_PREFIX, new_answers, read_answers


class _SavedDraft:
    """Resuming the saved draft file for the description cards."""

    def _read_saved_draft(
        self, output: Path
    ) -> tuple[DraftAnswers | None, str, DraftAnswers | None]:
        """Return the saved answers that still apply, a note, and the answers as read.

        The folders and the design's pairing are the ones now: answers for what still exists
        are kept exactly (:meth:`_carried`), and the others are dropped and named in the note.
        None and why when none apply. A draft that cannot be read at all is kept: it is copied
        aside before the next answer is saved.
        """
        try:
            saved = read_answers(output)
        except (DraftFileError, PublicationRefused) as exc:
            return None, self._set_aside(str(exc)), None
        if saved is None:
            return None, "", None
        try:
            answers, dropped, asked = self._carried(saved)
        except DraftError as exc:
            return None, self._set_aside(str(exc)), saved
        return answers, self._read_note(saved, dropped, asked), saved

    def _carried(
        self, saved: DraftAnswers
    ) -> tuple[DraftAnswers, tuple[str, ...], tuple[str, ...]]:
        """Return the saved answers carried over to the folders now, those dropped and asked.

        Raises :class:`.draft.DraftError` when the answers kept cannot be applied.
        """
        answers, dropped = carry_over(self._draft_base, saved, self._resume_pairing(saved))
        answers, asked = self._still_confirmed(saved, answers)
        apply_answers(self._draft_base, answers)
        return answers, dropped, asked

    def _read_note(
        self, saved: DraftAnswers, dropped: tuple[str, ...], asked: tuple[str, ...]
    ) -> str:
        """Return the note on the saved answers read: what was dropped and asked again."""
        pairing = self._resume_pairing(saved)
        lines = [f"From {DRAFT_FILENAME} in the output folder."]
        if dict(saved.pairing) != pairing:
            lines.append("The pairing of the experiment design above is used.")
        if asked:
            lines.append(
                "Asked again on Check and save (a folder joined or left it since your answer "
                '"the same way", or its channels were not all tagged then): '
                + ", ".join(asked)
                + "."
            )
        if dropped:
            lines.append("No longer in the experiment, so not kept: " + "; ".join(dropped) + ".")
        if dropped or asked:
            return ui.status("warn", "Your saved answers were read.", lines)
        return ui.status("info", "Your saved answers were read.", lines)

    def _still_confirmed(
        self, saved: DraftAnswers, kept: DraftAnswers
    ) -> tuple[DraftAnswers, tuple[str, ...]]:
        """Return the kept answers and the repeats asked again (none here: the design card asks).

        A described run overrides this: its "same way?" answers live in the draft.
        """
        return kept, ()

    def _resume_pairing(self, saved: DraftAnswers) -> dict[tuple[str, str], str]:
        """Return the pairing that holds on resume: the experiment design card's."""
        return self._design_pairing()

    def _set_aside(self, reason: str) -> str:
        """Note that the saved draft cannot be used; the next save copies it aside first."""
        self._keep_old_draft = True
        return ui.status(
            "warn",
            f"The saved {DRAFT_FILENAME} was not used.",
            [
                reason,
                "It is kept: before your next answer is saved it is copied next to it as "
                f"{UNUSED_PREFIX}N.json, never overwritten.",
            ],
        )

    def _new_answers(self, output: Path | None) -> DraftAnswers:
        """Return empty answers based on the published file (or ``absent`` with no folder)."""
        if output is None:
            return DraftAnswers(ABSENT, SPEC_COMMIT)
        try:
            return new_answers(output)
        except DescriptionError:
            return DraftAnswers(ABSENT, SPEC_COMMIT)


__all__ = ["_SavedDraft"]
