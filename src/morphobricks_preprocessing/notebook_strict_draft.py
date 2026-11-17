"""Every save of the draft answers, and the strict page's draft kept beside them in fast mode.

Part of the description cards (:mod:`.notebook_description`). Every answer of the notebook is
saved by :meth:`_StrictDraft._answer`. *Tag channels* fast mode (owner decisions of 16 November
2026) tags units from their headers with no click; unticking must give back the strict page's
draft after the same user actions, byte for byte. The notebook keeps that draft
(:class:`.strict_draft.StrictShadow`) while it differs:

- a save with no click (:meth:`_StrictDraft._answer_unattended`) goes to the draft only, and the
  strict page keeps the draft as it was before;
- every other save goes to both: a change of the *Your channels* cards is run on each draft
  (``change``), any other answer is the same edit, field by field; each is then settled as a save
  settles it (:meth:`_StrictDraft._reask`, and the draft's check);
- unticking (:meth:`_StrictDraft._restore_strict`) saves the strict page's draft back;
- a new check of the folders closes the cards (:meth:`_StrictDraft._put_strict_aside`) and reads
  the saved draft again: the strict page's draft is carried over the same way
  (:meth:`_StrictDraft._carried_strict`); any other draft read drops it.
"""

from __future__ import annotations

from .draft import DraftError
from .draft_answers import DraftAnswers, apply_answers
from .strict_draft import Change, StrictShadow


class _StrictDraft:
    """The notebook's saves of the draft answers, with fast mode's strict page's draft."""

    def _answer(
        self, answers: DraftAnswers, *, rebuild: bool = True, change: Change | None = None
    ) -> bool:
        """Save new answers (:meth:`_save_answers`); False when they are refused.

        ``change`` is the user's change that made ``answers`` from the current ones (a card of
        *Your channels*): the strict page's draft takes the same change. Without one it takes
        the same edit, field by field (:func:`.strict_draft.patched`).
        """
        before = self.draft_answers
        ok = self._save_answers(self._reask(before, answers, strict=False), rebuild=rebuild)
        if ok and before is not None:
            self._strict.follow(before, answers, change, self._strict_settled)
        return ok

    def _answer_unattended(self, answers: DraftAnswers) -> bool:
        """Save answers made with no click (fast mode's trusted headers): the draft only."""
        before = self.draft_answers
        ok = self._save_answers(self._reask(before, answers, strict=False), rebuild=False)
        if ok and before is not None and self._strict.draft is None:
            self._strict_held()
            self._strict.hold(before)
        return ok

    def _restore_strict(self) -> bool:
        """Save the strict page's draft back (fast mode unticked); False when refused."""
        strict = self._strict.draft
        if strict is None:
            return True
        if strict != self.draft_answers and not self._save_answers(strict, rebuild=False):
            return False
        self._strict_restored()
        self._strict.forget()
        return True

    def _strict_settled(self, before: DraftAnswers, after: DraftAnswers) -> DraftAnswers | None:
        """Return the strict page's new draft as its save settles it; None when refused."""
        after = self._reask(before, after, strict=True)
        try:
            apply_answers(self._draft_base, after)
        except DraftError:
            return None
        return after

    def _put_strict_aside(self) -> None:
        """Keep the draft and the strict page's draft aside while the cards close (a check)."""
        if self.draft_answers is None:
            return  # already closed: what was put aside stays
        self._strict_aside = (self.draft_answers, self._strict.draft)
        self._strict.forget()

    def _carried_strict(self, read: DraftAnswers | None) -> None:
        """Carry the strict page's draft over a new read of the saved draft (``read``).

        Kept only when the file read holds the draft as it was when the strict page's draft was
        last kept (the draft's last save): it is then carried over to the folders as the file
        was (:meth:`_carried`). Any other draft read (another output folder, none) drops it.
        """
        before, strict = getattr(self, "_strict_aside", (None, None))
        if self._strict.draft is not None:
            before, strict = self.draft_answers, self._strict.draft
        self._strict_aside = (None, None)
        self._strict.forget()
        if strict is None or read is None or read != before:
            return
        try:
            carried = self._carried(strict)[0]
        except DraftError:
            return
        self._strict.hold(carried)

    # Hooks of a described run --------------------------------------------------------------

    def _reask(
        self, before: DraftAnswers | None, after: DraftAnswers, *, strict: bool
    ) -> DraftAnswers:
        """Return ``after`` as a save settles it (a described run asks "same way?" again)."""
        return after

    def _strict_held(self) -> None:
        """Note that the strict page's draft starts apart from the draft."""

    def _strict_restored(self) -> None:
        """Note that the strict page's draft was saved back as the draft."""

    @property
    def _strict(self) -> StrictShadow:
        """The strict page's draft of fast mode, while it differs from the draft."""
        shadow = getattr(self, "_strict_shadow", None)
        if shadow is None:
            shadow = self._strict_shadow = StrictShadow()
        return shadow


__all__ = ["_StrictDraft"]
