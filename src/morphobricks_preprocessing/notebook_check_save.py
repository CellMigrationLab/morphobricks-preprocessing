"""The notebook's *Check and save* screen (screen 5 of the natural design; Stage 1, PR 16).

The experiment restated in sentences (what each condition is compared against, "compared
against nothing" said in words), each replicate with its folders, image counts, pictures and
channels (a label some acquisitions lack in orange), and beside it the one comparability
question of that replicate ("Were these prepared and imaged the same way…?", the N35 answer, its
date the day of the click). Then the short names that never change once saved, what the strict
reader and the run's checks say about the exact bytes (:mod:`.description_candidate`), the
change of the derived ``experiment_design.csv``, and one **Yes, save the experiment
description** click: it adopts every short name, publishes ``morphobricks.toml`` under the lock
and writes ``experiment_design.csv`` (:func:`.run.draft_file.publish_draft`). Run waits for it.

On reopening, a published file whose folders did not change folds every screen behind one green
line ("Saved on …, nothing changed. [Run] [Change…]"); new or removed folders are listed in
orange, a removed folder needs **Yes, remove**, and a replicate whose folders changed is asked
again here only.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .axes import AxisReview
from .axis_review import review_axes
from .description import DescriptionError
from .description_candidate import CandidateCheck, check_candidate
from .description_removal import Removal, reconfirmations
from .description_review import comparisons, short_names
from .draft import DraftError
from .draft_answers import (
    RepeatAnswer,
    apply_answers,
    description_from_draft,
    missing_confirmations,
)
from .draft_format import DraftFileError
from .fingerprint import changed_since_check
from .mask_answers import masks_unanswered
from .name_issues import NAME_CODES
from .notebook_check_view import (
    blocked_html,
    changes_html,
    checks_html,
    comparisons_html,
    names_html,
    removal_text,
    run_stop_html,
    saved_html,
    todo_html,
)
from .notebook_description import _with_ids
from .notebook_render import blocked_line
from .notebook_replicates import _ReplicateBoxes
from .published_answers import kept_date
from .run import OutputLockedError
from .run.description_file import PublicationRefused
from .run.draft_file import publish_draft
from .validation import plural

INTRO = (
    "<b>What happens here:</b> your experiment is restated in sentences. Answer, for each "
    "replicate, whether its folders were prepared and imaged the same way, read the short names "
    "(they never change once saved), then save. Nothing is saved for good before your click."
)
FOLDED = ("design_card", "names_card", "references_card", "channels_box", "masks_box")


class _CheckSaveScreen(_ReplicateBoxes):
    """The *Check and save* screen, its save click and the folded reopening."""

    def _build_check_save(self) -> None:
        """Build the screen (its content follows the answers)."""
        self._folded = False
        self._pictures_for: object = None
        self._removals: dict[str, str] = {}
        self._saved_on: str | None = None
        self._check: CandidateCheck | None = None
        self.same_way: dict[str, ui.ConfirmToggle] = {}
        self.saved_line = widgets.HTML()
        self.run_now = ui.button(
            " Run", style="success", icon="play", tooltip="Compute the histograms (Run)"
        )
        self.change_saved = ui.button(" Change…", style="warning", icon="pencil")
        self.fold_bar = widgets.HBox([self.saved_line, self.run_now, self.change_saved])
        ui.set_visible(self.fold_bar, False)
        self.run_now.on_click(lambda _: self._run_from_fold())
        self.change_saved.on_click(lambda _: self._unfold())
        self.check_waiting = widgets.HTML()
        self.folder_changes = widgets.HTML()
        self.words = widgets.HTML()
        self.replicates_box = widgets.VBox()
        self.replicates_detail = widgets.VBox()  # a picture's Enlarge
        self.removals_box = widgets.VBox()
        self.short_names = widgets.HTML()
        self.todo = widgets.HTML()
        self.checks = widgets.HTML()
        self.save_button = ui.button(
            " Yes, save the experiment description", style="success", icon="check", main=True
        )
        self.save_button.on_click(lambda _: self.save_experiment())
        self.save_note = widgets.HTML(layout=widgets.Layout(margin="0 0 0 12px"))
        ui.set_visible(self.save_note, False)
        self.save_row = widgets.HBox([self.save_button, self.save_note])
        self.save_blocker = widgets.HTML()
        self.save_status = widgets.HTML()
        self.check_card = ui.card(
            "Your experiment, in words",
            [
                self.folder_changes,
                self.words,
                self.replicates_box,
                self.replicates_detail,
                self.removals_box,
                self.short_names,
                self.todo,
                self.checks,
                self.save_row,
                self.save_blocker,
                self.save_status,
            ],
            visible=False,
        )

    def check_save_step(self, title: str | None = None) -> widgets.Widget:
        """Return the *Check and save* screen."""
        return ui.page(
            "Check and save" if title is None else title,
            "Check your experiment in words, confirm each replicate, then save.",
            [ui.intro(INTRO), self.fold_bar, self.check_waiting, self.check_card],
        )

    # State -------------------------------------------------------------------------------

    def _saved_current(self) -> bool:
        """Return whether the published file holds every answer and the folders are as saved."""
        state = self._published()
        output = self._design_folder()
        if state is None or output is None or state.changed:
            return False
        draft_file = output / "morphobricks.draft.json"
        return (
            not draft_file.exists()
            and self.draft_answers is not None
            and (self.draft_answers.base_sha256 == state.sha256)
        )

    def _run_allowed(self) -> bool:
        """Return whether the saved description is current and nothing stops the run."""
        if not self._saved_current():
            return False
        check = self._published_check()
        return check is not None and check.can_run

    def _published_refused(self) -> bool:
        """Return whether the run's checks ran on the published file and refuse it."""
        if self.confirmed_mapping is None:
            return False  # the physical checks wait for the roles: nothing is decided yet
        check = self._published_check()
        return check is not None and check.physical_run and not check.can_run

    def _published_check(self) -> CandidateCheck | None:
        """Return the run's checks of the published file (None without one)."""
        state = self._published()
        if state is None:
            return None
        roles = None if self.confirmed_mapping is None else self._review().file_mappings
        key = (id(state), id(self.confirmed_mapping))
        if getattr(self, "_published_check_key", None) != key:
            self._published_check_key = key
            self._published_checked = check_candidate(
                state.description, self.preflight, roles, state.description
            )
        return self._published_checked

    def _review(self) -> AxisReview:
        """Return the axis review of the confirmed roles."""
        return review_axes(
            self.preflight.metadata,
            self.confirmed_mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )

    # Refresh -----------------------------------------------------------------------------

    def _refresh_check_save(self) -> None:
        """Restate the experiment from the answers and say what the save would do."""
        self._refresh_channel_moves()
        if self.draft is None or self.draft_answers is None:
            ui.set_visible(self.check_card, False)
            ui.set_visible(self.fold_bar, False)
            self.check_waiting.value = ui.status(
                "info", "Waiting for step 1.", ["Check the folder and answer its cards."]
            )
            return
        self.check_waiting.value = ""
        ui.set_visible(self.check_card, not self._folded)
        answers = _with_ids(self.draft_answers, self._wanted_ids())
        try:
            applied, problem = apply_answers(self._draft_base, answers), ""
        except DraftError as exc:
            applied, problem = self.draft, f"A short name is refused: {exc}"
        labels = sorted(self.draft_answers.channels)  # R, G and B get their chips too
        self.words.value = comparisons_html(comparisons(applied, answers))
        self._show_replicates(applied, answers, labels)
        self.short_names.value = names_html(short_names(applied, answers))
        state = self._published()
        self.folder_changes.value = changes_html(state.reconciliation) if state else ""
        self._show_removals(state)
        todo = self._todo(applied, answers, problem)
        self._check = None
        if not todo[0] and not todo[1]:
            self._check = self._candidate_check(answers)
        self.todo.value = todo_html(*todo)
        self.checks.value = checks_html(self._check) or self._blocked(state)
        ready = self._check is not None and self._check.can_save and not self._moves_open()
        self.save_button.disabled = not ready or self._saved_current()
        self.save_note.value = "" if self.save_button.disabled else run_stop_html(self._check)
        ui.set_visible(self.save_note, bool(self.save_note.value))
        self.save_blocker.value = blocked_line(self._save_blockers(todo))
        self._show_saved_state()

    def _save_blockers(self, todo: tuple[list, list[str]]) -> list[str]:
        """Return what stops the save button now (shown beside it), or nothing when saved."""
        if not self.save_button.disabled or self._saved_current():
            return []  # saved already (the green line says so): nothing is blocked
        missing = len(todo[0]) + len(todo[1])
        if missing:  # and the Channels questions waiting, so none is found only afterwards
            line = f"{plural(missing, 'answer')} still missing (listed above the button)"
            return [line, *self._open_change_lines()]
        return (
            self._open_change_lines()
            or self._unseen_lines()
            or ["these answers are refused: see the red message above the button"]
        )

    def _blocked(self, state: object) -> str:
        """Say what already stops the run while the candidate cannot be checked (answers missing).

        The run's refusal of the saved description, or the header change the folder check found,
        is shown at once, never only after the reopened "same way?" is answered again.
        """
        if state is None:
            return ""
        answers = self.draft_answers.repeats
        asked = sorted(
            label
            for label in state.reconciliation.reopened
            if label in answers and answers[label].confirmed_on is None
        )
        return blocked_html(self._published_check(), state.reconciliation, asked)

    def _todo(self, applied: object, answers: object, problem: str) -> tuple[list, list[str]]:
        """Return the open questions and the other answers still missing before saving."""
        counts = self._channel_counts()
        questions = list(missing_confirmations(applied, answers, channel_counts=counts))
        extra = [problem] if problem else []
        left = masks_unanswered(answers, self._mask_types())
        extra += [f"mask {m}: separate objects, regions or grouped labels? (Masks)" for m in left]
        if len(applied.conditions) > 1:
            extra += [
                f"what {folder} is compared against (choose, or no comparison)"
                for folder in sorted(applied.conditions)
                if folder not in applied.references
                and getattr(answers.conditions.get(folder), "reference", None) is None
            ]
        state = self._published()
        if state is not None:
            extra += [
                f"the removal of {q.condition_id}'s folders (Yes, remove)"
                for q in state.removals
                if q.condition_id not in self._removals
            ]
        if self._design_folder() is None:
            extra.append("an output folder that can be written (step 1)")
        # Spec experiment-v1 section 3.3 and AQ11: names refused at discovery block the save.
        found = () if self.preflight is None else self.preflight.issues
        refused = {i.code for i in found if i.code in NAME_CODES}
        extra += [
            f"the folder or mask type names that step 1 refuses ({code}): rename them on disk "
            "and check the folder again"
            for code in sorted(refused)
        ]
        return questions, extra

    def _candidate_check(self, answers: object) -> CandidateCheck | None:
        """Build the candidate from the answers and check its exact bytes; None if refused."""
        try:
            candidate = description_from_draft(
                self._draft_base, answers, channel_counts=self._channel_counts()
            )
        except DraftError as exc:
            self.todo.value = ui.status("error", "The answers cannot be saved.", [str(exc)])
            return None
        roles = None if self.confirmed_mapping is None else self._review().file_mappings
        state = self._published()
        published = state.description if state is not None else None
        _, confirmations = self._publication_terms(state, candidate)
        return check_candidate(candidate, self.preflight, roles, published, confirmations)

    def _folder_cell(self, key: tuple[str, str], text: str) -> widgets.Widget:
        """Return a folder of the pairing card, with its picture."""
        return widgets.VBox([widgets.HTML(text), self._member_picture(key, self.pairing_detail)])

    def _show_removals(self, state: object) -> None:
        """One orange question per condition with removed folders, with **Yes, remove**."""
        rows = []
        for question in state.removals if state is not None else ():
            done = self._removals.get(question.condition_id)
            button = ui.button(" Yes, remove", style="warning", icon="trash")
            blocked = question.last_member and bool(question.referenced_by)
            button.disabled = done is not None or blocked
            button.on_click(lambda _, q=question: self._remove(q.condition_id))
            said = f" <span class='mb-help'>removed on {done}</span>" if done else ""
            rows.append(widgets.HBox([widgets.HTML(removal_text(question) + said), button]))
        self.removals_box.children = rows

    def _show_saved_state(self) -> None:
        """Show the green saved line (folded or not) and the run gate."""
        state = self._published()
        current = self._saved_current() and not self._published_refused()
        self._folded = self._folded and current  # a refusal or a change since saved: unfolded
        if current:
            when = self._saved_on or (state.saved_on if state else "")
            self.save_status.value = saved_html(
                when, self._run_allowed(), self.confirmed_mapping is not None
            )
            self.saved_line.value = ui.status("ok", f"Saved on {when}, nothing changed.")
        else:
            self.saved_line.value = ""
            if self.save_status.value.find("Saved") >= 0:
                self.save_status.value = ""
        ui.set_visible(self.fold_bar, self._folded)
        ui.set_visible(self.check_card, not self._folded)
        for name in FOLDED:
            ui.set_visible(getattr(self, name), not self._folded)
        self._refresh_run_step()

    # Answers -----------------------------------------------------------------------------

    def _same_way(self, label: str, value: bool) -> None:
        """Save the N35 answer of one replicate, dated the day of the click.

        A published replicate whose layout is as published is not reopened (experiment-v1
        section 10): its "Yes" takes the published date back ("the draft copies it from the
        published file, never from the clock"; section 4.4a). Only the user's click sets it.
        """
        answers = self.draft_answers
        entry = answers.repeats.get(label, RepeatAnswer())
        day = date.today().isoformat() if value else None
        state = self._published()
        if value and state is not None:
            try:
                day = kept_date(self._draft_base, answers, state.description, label) or day
            except DraftError:
                pass  # the layout cannot be compared: the day of the click, checked before Save
        repeats = {**answers.repeats, label: replace(entry, confirmed_on=day)}
        if not self._answer(replace(answers, repeats=repeats)):
            self.save_status.value = ui.status("error", "Not taken.", [str(self._refusal)])
            self._replicates_built = None  # the clicked box shows an answer that was not taken
        self._refresh_check_save()

    def _remove(self, condition_id: str) -> None:
        self._removals[condition_id] = date.today().isoformat()
        self._refresh_check_save()

    def _unfold(self) -> None:
        self._folded = False
        self._refresh_description()
        self._refresh_check_save()

    def _run_from_fold(self) -> None:
        if self.run() is None:
            self.saved_line.value += ui.status(
                "warn",
                "Not run yet.",
                ["See Run: its checklist says what is still to do."],
            )

    # Saving ------------------------------------------------------------------------------

    def save_experiment(self) -> bool:
        """Adopt every short name, publish the description and write the derived CSV.

        Returns
        -------
        bool
            True once ``morphobricks.toml`` and ``experiment_design.csv`` are written.
        """
        self._refresh_check_save()
        if self.save_button.disabled:
            return False
        if not self._confirm_ids():
            self.save_status.value = ui.status(
                "error",
                "Not saved: a short name was refused.",
                ["See Design: the names are under Edit names or pairing."],
            )
            return False
        if not self._save_description():
            self.save_status.value = ui.status(
                "error", "Not saved: your answers could not be saved first.", [self._save_problem]
            )
            return False
        changed = self._changed_images()
        if changed:
            self.save_status.value = ui.status(
                "error",
                f"Not saved: {plural(len(changed), 'image')} changed since the folder check.",
                [
                    "Check the folder again (step 1): the channels and pictures were of the "
                    "images as the check read them. Changed: "
                    + ", ".join(changed[:5])
                    + (" …" if len(changed) > 5 else "")
                ],
            )
            return False
        try:
            publication = self._publish()
        except OutputLockedError:
            self.save_status.value = ui.status(
                "error", "Not saved: a run holds the lock of the output folder.", ["Try again."]
            )
            return False
        except PublicationRefused as exc:
            lines = [f"{i.code.value} at {i.path}" for i in exc.issues]
            self.save_status.value = ui.status("error", "Not saved: refused.", lines)
            return False
        except (DraftError, DraftFileError, DescriptionError, ValueError, OSError) as exc:
            self.save_status.value = ui.status("error", "Not saved.", [str(exc)])
            return False
        self._saved_on = date.today().isoformat()
        self.draft_answers = replace(self.draft_answers, base_sha256=publication.sha256)
        self._removals = {}
        self._forget_channel_clicks()
        self._draft_note = ""
        self._forget_published()
        self.confirmed_image_type = self._described_image_type()
        self._refresh_description()
        self._refresh_check_save()
        return True

    def _changed_images(self) -> tuple[str, ...]:
        """Return the images to analyse that are no longer as the folder check read them."""
        pre = self.preflight
        analysed = [m.relative_path for m in pre.metadata.files]
        return changed_since_check(Path(pre.discovery.root), pre.header_fingerprints, analysed)

    def _publication_terms(self, state: object, candidate: object) -> tuple[tuple, tuple]:
        """Return the removals the user confirmed and the re-confirmations they give publication.

        The same terms go to publication (:meth:`_publish`) and to the pre-save check, so Save
        is enabled only for what publication accepts (with the moves and header-name changes
        confirmed on Channels, #258 and #281).
        """
        removals, accepted = self._removal_terms(state, candidate)
        return removals, accepted + self._move_terms(state, candidate)

    def _removal_terms(self, state: object, candidate: object) -> tuple[tuple, tuple]:
        """Return the removals the user confirmed and their (and the renames') re-confirmations."""
        if state is None:
            return (), ()
        removals = tuple(
            Removal(q.units, self._removals[q.condition_id])
            for q in state.removals
            if q.condition_id in self._removals
        )
        names = self._names_confirmed(state, candidate)
        confirmations = reconfirmations(
            state.description, candidate, state.reconciliation, removals, names
        ).accepted
        return removals, confirmations

    def _publish(self) -> object:
        """Publish the saved draft with the removals the user confirmed."""
        state, today = self._published(), date.today().isoformat()
        removals, confirmations, recorded = (), (), None
        if state is not None:
            candidate = description_from_draft(
                self._draft_base, self.draft_answers, channel_counts=self._channel_counts()
            )
            removals, confirmations = self._publication_terms(state, candidate)
            recorded = today if state.reconciliation.counts else None
        return publish_draft(
            self._design_folder(),
            self._draft_base,
            channel_counts=self._channel_counts(),
            confirmations=confirmations,
            removals=removals,
            recorded_on=recorded,
        )


__all__ = ["INTRO", "_CheckSaveScreen"]
