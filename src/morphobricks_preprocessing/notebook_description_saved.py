"""Step 1's saved draft: resume the answers that still apply, keep one that cannot be used.

Part of the description cards (:mod:`.notebook_description`). The saved
``morphobricks.draft.json`` is read as the file holds it and reconciled with the folders and the
design's pairing as they are now (:func:`.draft_answers.carry_over`): answers for what still
exists are kept exactly, the others are dropped and named in the card. A draft that cannot be
read or applied at all is never overwritten: it is copied aside first.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import notebook_style as ui
from .description import DescriptionError
from .description_file import DRAFT_FILENAME
from .draft import DraftError
from .draft_answers import DraftAnswers, apply_answers, carry_over
from .draft_format import ABSENT, SPEC_COMMIT, DraftFileError
from .run.description_file import PublicationRefused
from .run.draft_file import new_answers, read_answers

UNUSED_PREFIX = "morphobricks.draft.unused-"


class _SavedDraft:
    """Resuming the saved draft file for the description cards."""

    def _read_saved_draft(self, output: Path) -> tuple[DraftAnswers | None, str]:
        """Return the saved answers that still apply and a note, or None and why none do.

        The folders and the design's pairing are the ones now: answers for what still exists
        are kept exactly, and the others are dropped and named in the note. A draft that cannot
        be read at all is kept: it is copied aside before the next answer is saved.
        """
        try:
            saved = read_answers(output)
        except (DraftFileError, PublicationRefused) as exc:
            return None, self._set_aside(str(exc))
        if saved is None:
            return None, ""
        pairing = self._design_pairing()
        answers, dropped = carry_over(self._draft_base, saved, pairing)
        try:
            apply_answers(self._draft_base, answers)
        except DraftError as exc:
            return None, self._set_aside(str(exc))
        lines = [f"From {DRAFT_FILENAME} in the output folder."]
        if dict(saved.pairing) != pairing:
            lines.append("The pairing of the experiment design above is used.")
        if dropped:
            lines.append("No longer in the experiment, so not kept: " + "; ".join(dropped) + ".")
            return answers, ui.status("warn", "Your saved answers were read.", lines)
        return answers, ui.status("info", "Your saved answers were read.", lines)

    def _set_aside(self, reason: str) -> str:
        """Note that the saved draft cannot be used; it is copied aside before any save."""
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

    def _keep_unused_draft(self, output: Path) -> bool:
        """Copy the unusable draft aside under a name that is free; False if that failed."""
        source = output / DRAFT_FILENAME
        if source.is_symlink() or not source.exists():
            return True
        number = 1
        while (copy := output / f"{UNUSED_PREFIX}{number}.json").exists():
            number += 1
        try:
            shutil.copyfile(source, copy)
        except OSError as exc:
            self._names_refusal = ui.status(
                "error", "Not saved: the unused draft could not be kept.", [str(exc)]
            )
            return False
        self._keep_old_draft = False
        return True

    def _new_answers(self, output: Path | None) -> DraftAnswers:
        """Return empty answers based on the published file (or ``absent`` with no folder)."""
        if output is None:
            return DraftAnswers(ABSENT, SPEC_COMMIT)
        try:
            return new_answers(output)
        except DescriptionError:
            return DraftAnswers(ABSENT, SPEC_COMMIT)


__all__ = ["_SavedDraft"]
