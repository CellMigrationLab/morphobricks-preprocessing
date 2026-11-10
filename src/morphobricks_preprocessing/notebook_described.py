"""The notebook's runs are described: the experiment is saved as ``morphobricks.toml`` first.

``PreprocessingNotebook`` follows the natural design (Stage 1, PR 16): every science answer is
given once, on the screen it belongs to, saved in the draft, and the whole experiment is
confirmed with one click on *Check and save*, which publishes the description and only then
writes the derived ``experiment_design.csv``. The run waits for that: every run needs a published
description (Spec ADR 0002 AR10; there is no undescribed notebook any more).

What the described run changes, as overrides of the step mixins:

- **Step 1.** The "Yes, the labels are correct" toggle is retired: the labels are shown and
  restated on *Check and save*. The check writes no design template; the pairing card
  (:mod:`.notebook_design`) is an answer of the draft, and the "comparable" questions are on
  *Check and save*, one per replicate. The names card's "Yes, use these IDs" is retired too: the
  save click adopts every short name.
- **Step 2.** The global *Image type* card is retired: the image type is answered per unit on
  *Your channels*; the run is given the type the saved description states.
- **Reopening.** The published file is the answers: the folders found now decide what still
  applies (removed folders need "Yes, remove"; a replicate whose layout differs from the
  published one, its folders or a unit's declared ordered channel list, is asked "same way?"
  again, at the reopen or as soon as an answer changes it); the draft carries the pairing.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from . import notebook_style as ui
from .description_file import DESCRIPTION_FILENAME
from .description_reopen import PublishedState, published_state
from .draft import Draft, DraftError, draft_from_discovery
from .draft_answers import DraftAnswers, UnitKey, apply_answers, carry_over
from .fingerprint import current_fingerprint
from .notebook_row_fold import reopen_units
from .published_answers import (
    confirmed_layouts,
    published_answers,
    reasked,
    reopened,
    resumed,
    retired_ids,
)
from .run import Preflight


class _DescribedRun:
    """The overrides of a described run (see the module docstring)."""

    # The published file ----------------------------------------------------------------

    def _published(self) -> PublishedState | None:
        """Return the published file compared with the checked folders, or None."""
        pre, output = self.preflight, self._design_folder()
        if pre is None or output is None:
            return None
        key = (id(pre), str(output), _stamp(output))
        if getattr(self, "_published_key", None) != key:
            self._published_key = key
            self._published_cache, self._published_problem = published_state(
                output, pre.discovery, pre.excluded, pre.metadata.files
            )
        return self._published_cache

    def _reopen_units(self) -> Mapping[UnitKey, str] | None:
        """Return each unit's status against the saved file (None without one): rows fold."""
        state = self._published()
        return None if state is None else reopen_units(state.reconciliation)

    def _forget_published(self) -> None:
        self._published_key = None

    # Step 1 ------------------------------------------------------------------------------

    def check_folder(self) -> Preflight | None:
        """Check the folder; the labels are taken as shown (no toggle)."""
        pre = super().check_folder()
        if pre is not None and pre.discovery.leaves and self._show_design:
            self.labels_confirmed.set(True)
        return pre

    def _show_folder_results(self, pre: Preflight) -> None:
        super()._show_folder_results(pre)
        ui.set_visible(self.labels_confirmed.widget, False)

    def _build_description_cards(self) -> None:
        super()._build_description_cards()
        ui.set_visible(self.ids_confirmed.widget, False)
        # Nothing is left as a silent default: "no comparison" is chosen.
        help_box = self.references_card.children[1]
        help_box.value = help_box.value.replace(
            "Optional. For a treated", "Answer for each condition. For a treated"
        ).replace("Nothing is chosen for you.", "Choose <i>no comparison</i> where none.")

    # Answers -----------------------------------------------------------------------------

    def _base_draft(self) -> Draft:
        state = self._published()
        used = retired_ids(state.description) if state is not None else None
        pre = self.preflight
        return draft_from_discovery(pre.discovery, pre.excluded, used_ids=used)

    def _resume_pairing(self, saved: DraftAnswers) -> dict[tuple[str, str], str]:
        """Return the saved pairing of the folders that still exist."""
        members = {m.key for r in self._draft_base.repeats.values() for m in r.members}
        return {key: label for key, label in saved.pairing.items() if key in members}

    def _new_answers(self, output: object) -> DraftAnswers:
        """Return the published file's answers that still apply, or empty answers."""
        answers = super()._new_answers(output)
        state = self._published()
        self._reopen_note = ((), ())
        if state is None:
            return answers
        derived = published_answers(state.description, state.sha256)
        kept, dropped = carry_over(self._draft_base, derived, self._resume_pairing(derived))
        try:
            kept, asked = reopened(self._draft_base, kept, state.description)
            apply_answers(self._draft_base, kept)
        except DraftError as exc:
            self._draft_note = ui.status("warn", "The saved description was not used.", [str(exc)])
            return answers
        self._reopen_note = (dropped, asked)
        return kept

    def _answer(self, answers: DraftAnswers, *, rebuild: bool = True) -> bool:
        """Save new answers; a repeat whose layout this answer changed is asked again.

        Experiment-v1 section 10: a change of a unit's declared ordered channel list, its image
        type or the grouping reopens the repeat's N35 confirmation, so its ``confirmed_on`` is
        dropped (:func:`.published_answers.reasked`). A header name added to a channel changes
        no layout and keeps the date (section 4.4a). Nothing is ticked for the user.
        """
        before = self.draft_answers
        if before is not None and self._draft_base is not None:
            state = self._published()
            published = None if state is None else state.description
            try:
                # The layouts "same way?" was answered for: kept while a tag passes through
                # no tag, forgotten at each new check of the folders (the base draft changes).
                if getattr(self, "_same_way_base", None) is not self._draft_base:
                    self._same_way_base, self._same_way_for = self._draft_base, {}
                self._same_way_for.update(confirmed_layouts(self._draft_base, before))
                answers, _ = reasked(
                    self._draft_base, before, answers, published, self._same_way_for
                )
            except DraftError:
                pass  # the answer itself is refused below, with its reason
        return super()._answer(answers, rebuild=rebuild)

    def _still_confirmed(
        self, saved: DraftAnswers, kept: DraftAnswers
    ) -> tuple[DraftAnswers, tuple[str, ...]]:
        """Take back the saved "same way?" of a replicate whose folders are not those answered.

        The draft file is read after a new check of the folders: a folder that joined or left
        a replicate since the answer reopens it (:func:`.published_answers.resumed`).
        """
        return resumed(self._draft_base, saved, kept)

    def _id_typed(self, entry: object, state: object, text: str) -> None:
        super()._id_typed(entry, state, text)
        if not self._refreshing:
            self._refresh_check_save()

    def _show_names_status(self) -> None:
        super()._show_names_status()
        self._refresh_check_save()

    def _refresh_description(self) -> None:
        super()._refresh_description()
        if self._folded:
            for card in (self.names_card, self.references_card):
                ui.set_visible(card, False)

    def _refresh_channels(self) -> None:
        """Refresh *Your channels*; the pairing card gets its pictures once they can be read."""
        super()._refresh_channels()
        thumbs = self._channel_thumbs
        if self.pairing is not None and thumbs is not self._pictures_for:
            self._pictures_for = thumbs
            self._build_design_editor()

    # Step 2 ------------------------------------------------------------------------------

    def _open_image_type(self) -> None:
        ui.set_visible(self.image_type_card, False)
        self.confirmed_image_type = self._described_image_type()

    def _described_image_type(self) -> str | None:
        """Return the run's image type from the saved description: one type, or None."""
        state = self._published()
        if state is None:
            return None
        kinds = {
            unit.image_type == "rgb"
            for repeat in state.description.repeats.values()
            for member in repeat.members.values()
            for unit in member.technical_repeats.values()
        }
        return None if len(kinds) != 1 else ("rgb" if kinds.pop() else "fluorescence")

    def _refresh_axis_next(self) -> None:
        self.axis_next.value = ui.next_steps(
            "Go to Stains.",
            "Name what you stained, then Tag channels, Masks and Check and save.",
        )

    # The run -----------------------------------------------------------------------------

    def _gate_items(self) -> list[tuple[bool, str]]:
        return [(self._run_allowed(), "Save the experiment description (Check and save)")]


def _stamp(output: Path) -> object:
    """Return what tells a changed published file apart (its fingerprint, None when absent)."""
    return current_fingerprint(output / DESCRIPTION_FILENAME)


__all__ = ["_DescribedRun"]
