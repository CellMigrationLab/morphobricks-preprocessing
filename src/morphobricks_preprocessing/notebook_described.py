"""A *described run* of the notebook: the experiment is saved as ``morphobricks.toml`` first.

``PreprocessingNotebook(..., describe=True)`` (the shipped notebook) follows the natural design
(Stage 1, PR 16): every science answer is given once, on the screen it belongs to, saved in the
draft, and the whole experiment is confirmed with one click on *Check and save*, which publishes
the description and only then writes the derived ``experiment_design.csv``. The run waits for
that. Without ``describe`` the notebook keeps the v0.4 cards unchanged (embedding bricks, the
legacy flow).

What changes in a described run, as overrides of the step mixins (each calls the original
when the run is not described):

- **Step 1.** The "Yes, the labels are correct" toggle is retired: the labels are shown and
  restated on *Check and save*. The check writes no design template, the design card keeps only
  the pairing (in the draft, never written to ``experiment_design.csv`` before the save) and its
  "comparable" questions move to *Check and save*, one per replicate. The names card's "Yes, use
  these IDs" is retired too: the save click adopts every short name.
- **Step 2.** The global *Image type* card is retired: the image type is answered per unit on
  *Your channels*; the run is given the type the saved description states.
- **Reopening.** The published file is the answers: the folders found now decide what still
  applies (removed folders need "Yes, remove"; a replicate whose layout differs from the
  published one, its folders or a unit's declared ordered channel list, is asked "same way?"
  again, at the reopen or as soon as an answer changes it); the draft carries the pairing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import notebook_style as ui
from .description_file import DESCRIPTION_FILENAME
from .description_reopen import PublishedState, published_state
from .design import ExperimentDesign, default_design
from .design_derive import derive_design
from .design_edit import DesignEdit, DesignEditError, apply_design_edit
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


def _units(draft: Draft, label: str) -> frozenset:
    repeat = draft.repeats.get(label)
    if repeat is None:
        return frozenset()
    return frozenset((m.key, u.source_folder) for m in repeat.members for u in m.units)


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
        state = self._published() if self._describe else None
        return None if state is None else reopen_units(state.reconciliation)

    def _forget_published(self) -> None:
        self._published_key = None

    # Step 1 ------------------------------------------------------------------------------

    def check_folder(self) -> Preflight | None:
        """Check the folder; in a described run the labels are taken as shown (no toggle)."""
        pre = super().check_folder()
        if self._describe and pre is not None and pre.discovery.leaves and self._show_design:
            self.labels_confirmed.set(True)
        return pre

    def _show_folder_results(self, pre: Preflight) -> None:
        super()._show_folder_results(pre)
        ui.set_visible(self.labels_confirmed.widget, not self._describe)

    def _build_description_cards(self) -> None:
        super()._build_description_cards()
        ui.set_visible(self.ids_confirmed.widget, not self._describe)
        if self._describe:  # nothing is left as a silent default: "no comparison" is chosen
            help_box = self.references_card.children[1]
            help_box.value = help_box.value.replace(
                "Optional. For a treated", "Answer for each condition. For a treated"
            ).replace("Nothing is chosen for you.", "Choose <i>no comparison</i> where none.")

    def _build_design_card(self) -> None:
        """Build the design card; a described run says where the pairing is kept."""
        super()._build_design_card()
        if self._describe:
            help_box = self.design_card.children[1]
            help_box.value = help_box.value.split("The answers")[0] + (
                "Your pairing is saved with your other answers in the draft; "
                "<b>experiment_design.csv</b> is written only when you save the experiment "
                "description (Check and save).</div>"
            )

    def _save_design(self) -> None:
        """Save the design card's answers (never in a described run: the draft holds them)."""
        if not self._describe:
            super()._save_design()
            return
        self._design_saved = self._design_waiting = False

    def _build_group_questions(self) -> None:
        super()._build_group_questions()
        ui.set_visible(self.groups_box, not self._describe)

    def _design_pending(self) -> bool:
        return False if self._describe else super()._design_pending()

    def _open_design(self, *, output_changed: bool = False) -> None:
        """Open the pairing from the draft (or the published file) in a described run."""
        if not self._describe:
            super()._open_design(output_changed=output_changed)
            return
        self._design_output = self._design_folder()
        self.design = default_design(self.preflight.dataset.repeats)
        self._design_unreadable, self._design_saved = False, False
        self._design_note = self._design_refusal = ""
        self._forget_published()
        self._load_description()
        self._folded = self._saved_current()
        self._take_pairing()
        ui.set_visible(self.design_editor, True)
        ui.set_visible(self.design_change, False)
        ui.set_visible(self.design_card, True)
        self._design_changed()

    def _take_pairing(self) -> None:
        """Set the design card's pairing to the one the answers hold."""
        pairing = dict(self.draft_answers.pairing) if self.draft_answers else {}
        if pairing:
            try:
                self.design = apply_design_edit(
                    self.design, DesignEdit(repeat_groups=pairing), date.today().isoformat()
                )
            except DesignEditError as exc:
                self._design_note = ui.status("warn", "The saved pairing was not used.", [str(exc)])
        self._build_design_editor()

    def _refresh_design_status(self) -> None:
        """Say how the pairing is kept in a described run (no confirmation on this card)."""
        if not self._describe:
            super()._refresh_design_status()
            return
        if self.design is None:
            self.design_status.value = ""
            return
        note = self._design_note + self._design_refusal
        self.design_status.value = note + ui.status(
            "info",
            "Folders with the same name are the same replicate.",
            [
                "Choose another replicate for a folder that was named differently. Your pairing "
                "is saved with your other answers and confirmed on Check and save."
            ],
        )

    # Answers -----------------------------------------------------------------------------

    def _base_draft(self) -> Draft:
        if not self._describe:
            return super()._base_draft()
        state = self._published()
        used = retired_ids(state.description) if state is not None else None
        pre = self.preflight
        return draft_from_discovery(pre.discovery, pre.excluded, used_ids=used)

    def _resume_pairing(self, saved: DraftAnswers) -> dict[tuple[str, str], str]:
        """Return the saved pairing of the folders that still exist (described run)."""
        if not self._describe:
            return super()._resume_pairing(saved)
        members = {m.key for r in self._draft_base.repeats.values() for m in r.members}
        return {key: label for key, label in saved.pairing.items() if key in members}

    def _new_answers(self, output: object) -> DraftAnswers:
        """Return the published file's answers that still apply, or empty answers."""
        answers = super()._new_answers(output)
        state = self._published() if self._describe else None
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
        if self._describe and before is not None and self._draft_base is not None:
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
        if not self._describe:
            return super()._still_confirmed(saved, kept)
        return resumed(self._draft_base, saved, kept)

    def _sync_description(self) -> None:
        """Take the design card's pairing into the answers; changed replicates are asked again."""
        if not self._describe:
            super()._sync_description()
            return
        answers = self.draft_answers
        if answers is None or self.design is None:
            return
        pairing = self._design_pairing()
        if pairing == dict(answers.pairing):
            self._refresh_description()
            return
        old, new = self._draft_base.paired(answers.pairing), self._draft_base.paired(pairing)
        repeats = {
            label: answer
            if _units(old, label) == _units(new, label)
            else replace(answer, confirmed_on=None)
            for label, answer in answers.repeats.items()
            if label in new.repeats
        }
        self._answer(replace(answers, pairing=pairing, repeats=repeats))

    def _id_typed(self, entry: object, state: object, text: str) -> None:
        super()._id_typed(entry, state, text)
        if self._describe and not self._refreshing:
            self._refresh_check_save()

    def _show_names_status(self) -> None:
        super()._show_names_status()
        if self._describe:
            self._refresh_check_save()

    def _refresh_description(self) -> None:
        super()._refresh_description()
        if self._describe and self._folded:
            for card in (self.names_card, self.references_card):
                ui.set_visible(card, False)

    def _refresh_channels(self) -> None:
        """Refresh *Your channels*; the pairing card gets its pictures once they can be read."""
        super()._refresh_channels()
        thumbs = self._channel_thumbs
        if self._describe and self.design is not None and thumbs is not self._pictures_for:
            self._pictures_for = thumbs
            self._build_design_editor()

    # Step 2 ------------------------------------------------------------------------------

    def _open_image_type(self) -> None:
        if not self._describe:
            super()._open_image_type()
            return
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
        if not self._describe:
            super()._refresh_axis_next()
            return
        self.axis_next.value = ui.next_steps(
            "Go to Stains.",
            "Name what you stained, then Tag channels, Masks and Check and save.",
        )

    # The run -----------------------------------------------------------------------------

    def _design_items(self) -> list[tuple[bool, str]]:
        return [] if self._describe else super()._design_items()

    def _gate_items(self) -> list[tuple[bool, str]]:
        if not self._describe:
            return super()._gate_items()
        return [(self._run_allowed(), "Save the experiment description (Check and save)")]

    def _design_for_run(self) -> ExperimentDesign | None:
        if not self._describe:
            return super()._design_for_run()
        state = self._published()
        return None if state is None else derive_design(state.description)


def _stamp(output: Path) -> object:
    """Return what tells a changed published file apart (its fingerprint, None when absent)."""
    return current_fingerprint(output / DESCRIPTION_FILENAME)


__all__ = ["_DescribedRun"]
