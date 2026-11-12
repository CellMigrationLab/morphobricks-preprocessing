"""Step 1 of the notebook: the description cards (names, IDs, pairing and references).

The experiment design card (:mod:`.notebook_design`) pairs the biological repeats and asks whether
they are comparable. The cards here follow it: *Names for your experiment* (the ID and the
optional shown-as name of each condition, repeat and technical-repeat folder) and *What is each
condition compared against?* (references). Together they hold the answers of the draft
experiment description (experiment-v1 section 12): the draft is derived from the folders, the
cards show what it proposes, and every answer is applied to it by
:func:`.draft_answers.apply_answers` and saved at once to ``morphobricks.draft.json`` in the
output folder (:func:`.run.draft_file.save_draft`).

Nothing is confirmed on the user's behalf: an ID is proposed (the folder name, only when the
reader accepts it as it is) and adopted only when the user clicks *Yes, use these IDs*. The
pairing and the date of each repeat's comparability come from the design card's own answers
(the date is the day of the user's click, taken there); this module never reads the clock. Cards
for the channels and masks of the draft, and the final review, follow in later steps.
"""

from __future__ import annotations

import html
import logging
from dataclasses import replace

import ipywidgets as widgets

from . import notebook_style as ui
from .description_file import DRAFT_FILENAME
from .draft import Draft, DraftError, draft_from_discovery
from .draft_answers import (
    ConditionAnswer,
    DraftAnswers,
    RepeatAnswer,
    apply_answers,
)
from .draft_format import DraftFileError
from .notebook_description_saved import _SavedDraft
from .notebook_description_view import (
    KIND_NAMES,
    REFERENCE_COLUMNS,
    Entry,
    header_row,
    id_state,
    refusal_card,
)
from .notebook_names import _NameRows, _updated
from .run import OutputLockedError
from .run.draft_file import save_draft
from .run.output_guard import OutputInsideMasksError

IDS_QUESTION = "Are these IDs right?"
IDS_EXPLANATION = (
    "An ID is the short name later bricks use to refer to a condition or a repeat, and it never "
    "changes once the experiment description is saved: read each one before you answer. A "
    "proposal is the folder name exactly as it is, offered only when it can be an ID; where "
    "there is none, type one (letters and digits, no space at the start or end). "
    "The <i>Shown as</i> name is only for plots and tables, and can be changed at any time."
)


class AnswerNotSaved(DraftError):
    """An answer refused because it could not be saved; the message says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Not saved: {reason}.", ("not_saved",))


class _DescriptionCard(_NameRows, _SavedDraft):
    """Step 1's names and references cards: the user's answers to the draft description."""

    def _build_description_cards(self) -> None:
        """Build the names and references cards, hidden until the labels are confirmed."""
        self.draft: Draft | None = None
        self.draft_answers: DraftAnswers | None = None
        self._draft_base: Draft | None = None
        self._draft_note = ""
        self._names_refusal = ""
        self._references_refusal = ""
        self._draft_saved = False
        self._keep_old_draft = False
        self._refreshing = False
        self._refusal: DraftError | None = None
        self._save_problem = ""
        self.id_inputs: dict[tuple[str, str], widgets.Text] = {}
        self.name_inputs: dict[tuple[str, str], widgets.Text] = {}
        self.name_hints: dict[tuple[str, str], widgets.HTML] = {}
        self.single_note = widgets.HTML()
        self.reference_boxes: dict[str, widgets.Dropdown] = {}
        self.reference_notes: dict[str, widgets.HTML] = {}
        self._id_states: dict[tuple[str, str], widgets.HTML] = {}
        self.names_status = widgets.HTML()
        self.names_grid = widgets.VBox()
        self.condition_rows, self.replicate_rows = widgets.VBox(), widgets.VBox()
        self.ids_confirmed = ui.ConfirmToggle(
            IDS_QUESTION,
            IDS_EXPLANATION,
            "Yes, use these IDs",
            "Confirmed: these IDs are used.",
            "Change the IDs",
        )
        self.ids_confirmed.observe(self._ids_toggled)
        self.names_card = ui.card(
            "Names for your experiment",
            [
                ui.help_text(
                    "Each condition and each biological repeat gets a short name (its ID). The "
                    "folder names are proposed when they can be used as they are. Your answers "
                    "are saved as you give them."
                ),
                self.names_status,
                self.names_grid,
                self.single_note,
                self.ids_confirmed.widget,
            ],
            visible=False,
        )
        self.references_status = widgets.HTML()
        self.references_grid = widgets.VBox()
        self.references_card = ui.card(
            "What is each condition compared against?",
            [
                ui.help_text(
                    "Optional. For a treated condition choose the condition it is compared "
                    "with, for example the untreated one. A condition may itself be compared "
                    "against another (a chain); a loop is refused. Nothing is chosen for you."
                ),
                self.references_grid,
                self.references_status,
            ],
            visible=False,
        )

    # Opening, resuming and closing -------------------------------------------------------

    def _hide_description(self) -> None:
        self.draft = self.draft_answers = self._draft_base = None
        ui.set_visible(self.names_card, False)
        ui.set_visible(self.references_card, False)
        self._hide_channels()

    def _load_description(self) -> None:
        """Derive the draft from the folders and resume the saved answers, if any are usable.

        A saved draft that is refused (other Spec commit, changed published file, unknown
        folder) or was made for another pairing than the design shows is left as it is on
        disk and not used; the next answer replaces it. Nothing is merged or guessed.
        """
        self._draft_base = self._base_draft()
        self._keep_old_draft = False
        output = self._design_output
        answers, note = None, ""
        if output is not None:
            answers, note = self._read_saved_draft(output)
        self._draft_saved = answers is not None
        if answers is None:
            answers = self._new_answers(output)
        self.draft_answers = answers
        self.draft = apply_answers(self._draft_base, answers)
        self._draft_note = note
        self._names_refusal = self._references_refusal = ""
        self.ids_confirmed.set(self._ids_all_confirmed(), notify=False)

    def _base_draft(self) -> Draft:
        """Return the draft of the checked folders, before any answer."""
        return draft_from_discovery(self.preflight.discovery, self.preflight.excluded)

    # Answers -----------------------------------------------------------------------------

    def _answer(self, answers: DraftAnswers, *, rebuild: bool = True) -> bool:
        """Apply new answers to the draft and save them; False when they are refused.

        A refused answer changes nothing and is shown by the card that asked for it. An answer
        that cannot be saved (the output folder's lock, the draft file, a write error) is
        refused too: the answers before it are kept, so nothing is shown as answered that is
        not on disk. With ``rebuild`` False the boxes are left as they are (a name being typed
        keeps its focus).
        """
        try:
            draft = apply_answers(self._draft_base, answers)
        except DraftError as exc:
            self._refusal = exc
            return False
        before = self.draft, self.draft_answers
        self.draft, self.draft_answers = draft, answers
        saved = self._save_description()
        if not saved:
            self.draft, self.draft_answers = before
            self._refusal = AnswerNotSaved(self._save_problem)
        else:
            self._refusal = None
        if rebuild:
            self._refresh_description()
        else:
            self._show_names_status()
        return saved

    def _save_description(self) -> bool:
        """Save the answers to ``morphobricks.draft.json`` under the output folder's lock.

        Returns
        -------
        bool
            False when the save was refused or failed (``_save_problem`` says why); True when
            the draft was written, or when no output folder is set (the answers are then kept
            while the notebook is open, and the page says they are not saved).
        """
        output = self._design_folder()
        self._draft_saved, self._save_problem = False, ""
        if output is None:
            return True
        if self._keep_old_draft and not self._keep_unused_draft(output):
            self._save_problem = "the unused draft could not be kept"
            return False
        with self.log.attached():
            try:
                save_draft(output, self._draft_base, self.draft_answers)
            except OutputLockedError:
                self.folder_lock.show(output)
                self._save_problem = "another run holds the lock of the output folder"
                self._names_refusal = ui.status(
                    "error",
                    "Not saved: another run holds the lock of the output folder.",
                    ["Answer again once it is free."],
                )
                return False
            except (OutputInsideMasksError, DraftFileError, DraftError) as exc:
                self._save_problem = str(exc)
                self._names_refusal = ui.status("error", "Not saved.", [str(exc)])
                return False
            except OSError as exc:
                logging.getLogger(__name__).warning("Cannot save the draft", exc_info=True)
                self._save_problem = f"the draft could not be written: {exc}"
                self._names_refusal = ui.status("error", "Not saved.", [self._save_problem])
                return False
            finally:
                self._show_log()
        self._draft_saved = True
        return True

    # The names card ----------------------------------------------------------------------

    def _ids_all_confirmed(self) -> bool:
        return not any(q.code == "id_pending" for q in self.draft.open_questions())

    def _refresh_description(self) -> None:
        """Rebuild the cards from the draft and the answers."""
        if self.draft is None:
            return
        self._refreshing = True
        try:
            self._build_names()
            self._build_references()
            for kind in ("conditions", "repeats"):
                self._update_name_hints(kind)
            self.ids_confirmed.set(self._ids_all_confirmed(), notify=False)
            self._show_names_status()
        finally:
            self._refreshing = False
        ui.set_visible(self.names_card, True)
        ui.set_visible(self.references_card, len(self.draft.conditions) > 1)
        self._refresh_channels()

    def _show_names_status(self) -> None:
        """Say where the answers are saved (or why not), after any note or refusal."""
        if self._draft_saved:
            where = (
                f"Your answers are saved as you give them in <b>{html.escape(DRAFT_FILENAME)}</b> "
                "in the output folder (a draft, not yet the final experiment description)."
            )
        elif self._design_folder() is None:
            where = (
                "Not saved: the output folder is not set or cannot be written; your answers "
                "are kept while the notebook is open."
            )
        else:
            where = (
                "Nothing is saved yet: your first answer is saved in "
                f"<b>{html.escape(DRAFT_FILENAME)}</b> in the output folder."
            )
        self.names_status.value = (
            self._draft_note + self._names_refusal + f"<div class='mb-help'>{where}</div>"
        )

    def _id_typed(self, entry: Entry, state: widgets.HTML, text: str) -> None:
        state.value = id_state(entry, text)
        if entry.kind == "conditions" and not self._refreshing:
            self._relabel_references()

    def _ids_toggled(self, confirmed: bool) -> None:
        if self.draft is None:
            return
        self._names_refusal = self._draft_note = ""
        if confirmed:
            if not self._confirm_ids():
                self.ids_confirmed.set(False, notify=False)
        else:
            self._answer(_with_ids(self.draft_answers, {}))
        self._show_names_status()

    def _wanted_ids(self) -> dict[tuple[str, str], str]:
        """Return every ID as the names card shows it now (typed, proposed or confirmed)."""
        return {
            (e.kind, e.key): (
                "single"
                if e.fixed
                else self.id_inputs[(e.kind, e.key)].value
                if (e.kind, e.key) in self.id_inputs
                else e.slot.confirmed or e.slot.proposed or ""
            )
            for e in self._entries()
        }

    def _confirm_ids(self) -> bool:
        """Adopt every ID as shown, or none: each refusal is listed with the reader's reason."""
        wanted = self._wanted_ids()
        return self._adopt_ids(wanted, _with_ids(self.draft_answers, wanted))

    def _adopt_ids(self, wanted: dict[tuple[str, str], str], answers: DraftAnswers) -> bool:
        """Save ``answers`` (which adopt ``wanted``) in one answer, unless an ID is refused.

        Each ID is first tried on its own, so every refusal is listed with the reader's
        reason; then ``answers`` are saved once. Nothing is adopted when anything is refused.
        """
        problems = []
        for (kind, key), text in wanted.items():
            try:
                apply_answers(self._draft_base, _with_ids(self.draft_answers, {(kind, key): text}))
            except DraftError as exc:
                problems.append((f"{KIND_NAMES[kind].capitalize()} {key}", exc))
        if not problems:
            if self._answer(answers):
                return True
            problems.append(("These IDs together", self._refusal))
        self._names_refusal = refusal_card(
            "These IDs were not taken: nothing was confirmed.", problems
        )
        return False

    # The references card -----------------------------------------------------------------

    def _build_references(self) -> None:
        """Show one dropdown per condition from the draft's references.

        The dropdowns are built once for the conditions there are and then updated in place: a
        choice must not replace the control the user is using (owner test, 10 November 2026).
        """
        conditions = sorted(self.draft.conditions)
        if list(self.reference_boxes) != conditions or not self.references_grid.children:
            self._new_reference_rows(conditions)
        references = self.draft.references
        for condition in conditions:
            box = self.reference_boxes[condition]
            options, chosen = self._reference_options(condition), self._shown_reference(condition)
            if list(box.options) != options:
                box.options = options
            if box.value != chosen:
                box.value = chosen
            users = sorted(c for c, target in references.items() if target == condition)
            note = f"the reference of {', '.join(users)}" if users else ""
            self.reference_notes[condition].value = html.escape(note)
        self.references_status.value = self._references_refusal

    def _new_reference_rows(self, conditions: list[str]) -> None:
        """Build the rows of the references card: a dropdown and a note per condition."""
        rows: list[widgets.Widget] = [
            header_row("Condition", "Is compared against", "", layout=REFERENCE_COLUMNS)
        ]
        self.reference_boxes, self.reference_notes = {}, {}
        for condition in conditions:
            box = widgets.Dropdown(
                options=self._reference_options(condition),
                value=self._shown_reference(condition),
                layout=widgets.Layout(width="99%"),
            )
            box.observe(
                lambda change, c=condition: self._reference_chosen(c, change["new"]), names="value"
            )
            self.reference_boxes[condition] = box
            self.reference_notes[condition] = widgets.HTML()
            rows.append(
                widgets.GridBox(
                    [
                        widgets.HTML(f"<b>{html.escape(condition)}</b>"),
                        box,
                        self.reference_notes[condition],
                    ],
                    layout=REFERENCE_COLUMNS,
                )
            )
        self.references_grid.children = rows

    def _chosen_reference(self, condition: str) -> str | None:
        """Return the saved choice: None if not answered, ``""`` for no comparison, a folder."""
        answer = self.draft_answers.conditions.get(condition, ConditionAnswer()).reference
        return self.draft.references.get(condition) if answer is None else answer

    def _shown_reference(self, condition: str) -> str | None:
        """Return what the dropdown shows: the saved choice (the stepper shows a proposal)."""
        return self._chosen_reference(condition)

    def _references_answered(self) -> bool:
        """Return True once any condition was answered (including "no comparison")."""
        answers = self.draft_answers
        return answers is not None and any(
            a.reference is not None for a in answers.conditions.values()
        )

    def _reference_label(self, condition: str) -> str:
        """Return ``ID (folder name)`` with the ID entered or confirmed now, else the folder."""
        box = self.id_inputs.get(("conditions", condition))
        entered = box.value if box is not None else self.draft.conditions[condition].confirmed
        return f"{entered} (folder {condition})" if entered else f"folder {condition}"

    def _reference_options(self, condition: str) -> list[tuple[str, str | None]]:
        """Return the options: not answered, no comparison (``""``) and each other condition."""
        others = sorted(c for c in self.draft.conditions if c != condition)
        return [("— choose —", None), ("no comparison", "")] + [
            (self._reference_label(c), c) for c in others
        ]

    def _relabel_references(self) -> None:
        """Update the dropdown texts after an ID was typed, keeping each choice."""
        if self.draft is None:
            return
        self._refreshing = True
        try:
            for condition, box in self.reference_boxes.items():
                chosen = box.value
                box.options = self._reference_options(condition)
                box.value = chosen
        finally:
            self._refreshing = False

    def _reference_chosen(self, condition: str, target: str | None) -> None:
        """Save the choice of what ``condition`` is compared against; a loop is refused."""
        if self._refreshing or self.draft is None:
            return
        answers = self.draft_answers
        if self._chosen_reference(condition) == target:
            return
        self._draft_note = ""
        new = _updated(answers, "conditions", condition, reference=target)
        # One refresh, of the cards as they are: the dropdown chosen in stays on the page.
        if self._answer(new, rebuild=False):
            self._references_refusal = ""
        else:
            self._references_refusal = refusal_card(
                "This answer was not taken.",
                [(f"{condition} compared against {target}", self._refusal)],
            )
        self._refresh_description()
        self._refresh_design_status()


def _with_ids(answers: DraftAnswers, ids: dict[tuple[str, str], str]) -> DraftAnswers:
    """Return the answers with exactly these IDs (any ID not in ``ids`` is taken back)."""
    conditions = {
        k: replace(v, id=ids.get(("conditions", k))) for k, v in answers.conditions.items()
    }
    conditions.update(
        {
            key: ConditionAnswer(id=text)
            for (kind, key), text in ids.items()
            if kind == "conditions" and key not in conditions
        }
    )
    repeats = {k: replace(v, id=ids.get(("repeats", k))) for k, v in answers.repeats.items()}
    repeats.update(
        {
            key: RepeatAnswer(id=text)
            for (kind, key), text in ids.items()
            if kind == "repeats" and key not in repeats
        }
    )
    technical = {key: text for (kind, key), text in ids.items() if kind == "technical_repeats"}
    return replace(
        answers,
        conditions={k: v for k, v in conditions.items() if v != ConditionAnswer()},
        repeats={k: v for k, v in repeats.items() if v != RepeatAnswer()},
        technical_repeats=technical,
    )


__all__ = ["AnswerNotSaved", "_DescriptionCard"]
