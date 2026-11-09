"""Step 1 of the notebook: the experiment design card."""

from __future__ import annotations

import html
import logging
from datetime import date
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .dataset import check_dataset
from .design import (
    DESIGN_FILENAME,
    DesignChanges,
    DesignCheckError,
    ExperimentDesign,
    default_design,
    load_design,
)
from .design_edit import DesignEdit, DesignEditError, apply_design_edit
from .discovery import check_output_folder, default_output_folder
from .notebook_options import _PAIRING_COLUMNS
from .notebook_render import _group_explanation, _group_question
from .notebook_row_fold import RowFold, row_status
from .run import OutputLockedError
from .run.design_file import write_design
from .run.output_guard import OutputInsideMasksError
from .validation import plural


class _DesignCard:
    """Step 1's experiment design card: repeat groups, comparability and controls."""

    # Experiment design (step 1) -------------------------------------------------------------

    def _build_design_card(self) -> None:
        """Build the experiment design card, hidden until the labels are confirmed."""
        self.design_status = widgets.HTML()
        self.design_change = ui.button(
            " Change the experiment design",
            style="warning",
            icon="pencil",
            tooltip="Show the pairing, the confirmations and the controls again",
        )
        ui.set_visible(self.design_change, False)
        self.pairing_grid = widgets.VBox()
        self.pairing_detail = widgets.VBox()  # a picture's Enlarge (described run)
        self.pairing_box = widgets.VBox(
            [
                widgets.HTML("<b>1. Pair the biological repeats across conditions</b>"),
                ui.help_text(
                    "A repeat group is one biological repeat in every condition: the same "
                    "preparation, imaged for each condition. Folders with the same name are "
                    "paired by default. If a repeat has another folder name in some "
                    "condition (acquired a day later, for example), choose its group in "
                    "the dropdown. Each group may hold one folder per condition. The dropdown "
                    "offers "
                    "folder names only; the repeat's own ID is set with the names below."
                ),
                self.pairing_grid,
                self.pairing_detail,
            ]
        )
        self.groups_box = widgets.VBox()
        self.control_checks: dict[str, widgets.Checkbox] = {}
        self.controls_grid = widgets.VBox()
        self.controls_box = widgets.VBox(
            [
                widgets.HTML("<b>Controls (optional)</b>"),
                ui.help_text(
                    "Tick the control conditions, if any: none, one or several. This is "
                    "recorded for later bricks; nothing uses it yet."
                ),
                self.controls_grid,
            ]
        )
        self.design_editor = widgets.VBox([self.pairing_box, self.groups_box, self.controls_box])
        self.design_card = ui.card(
            "Experiment design",
            [
                ui.help_text(
                    "Later bricks normalise each biological repeat over all conditions "
                    "together, so the brick needs to know which folders are the same "
                    "biological repeat, and that their images are comparable. The answers "
                    f"are saved in <b>{DESIGN_FILENAME}</b> in the output folder, so they are "
                    "not asked again while the folders stay the same."
                ),
                self.design_status,
                self.design_change,
                self.design_editor,
            ],
            visible=False,
        )
        self.design_change.on_click(lambda _: self._reopen_design())

    def _design_folder(self) -> Path | None:
        """Return the output folder the design is saved in, or None if it may not be written."""
        folder = ui.clean_path(self.folder.value)
        if not folder:
            return None
        output = Path(self._output_folder() or default_output_folder(folder))
        if check_output_folder(folder, output) or self._output_file_in_the_way():
            return None
        return output

    def _open_design(self, *, output_changed: bool = False) -> None:
        """Load the saved design, or start a new one, once the labels are confirmed.

        A saved file that cannot be read is left as it is (owner decision of 31 October
        2026): nothing is written until the user corrects it or answers a question again,
        and the questions start unanswered, so step 3 cannot run on a guessed design.

        After the output folder changed (``output_changed``, issue #98) the design is read
        from the new folder, or asked again without one, and nothing is written until an
        answer: the folder is still being typed, and the earlier folder's answers (its
        confirmations and controls) belong to that folder.
        """
        repeats = self.preflight.dataset.repeats
        output = self._design_folder()
        self._design_output = output
        changes, problem, saved = DesignChanges(), None, False
        if output is None:
            design = default_design(repeats)
        else:
            try:
                design, changes = load_design(output, repeats)
                saved = (output / DESIGN_FILENAME).exists()
            except DesignCheckError as exc:
                # Refused and left as it is; the questions start unanswered.
                design, problem = default_design(repeats), exc.issues[0].message
        self.design = design
        self._design_unreadable = problem is not None
        self._build_design_editor()
        # A design confirmed earlier for the same folders is not asked again.
        reopen = not (saved and design.confirmed and not changes.any)
        ui.set_visible(self.design_editor, reopen)
        ui.set_visible(self.design_change, not reopen)
        self._design_note = self._design_load_note(design, changes, problem, saved, output)
        if output_changed:
            self._design_note = _output_changed_note(output, saved, problem) + self._design_note
        self._design_refusal = ""
        ui.set_visible(self.design_card, True)
        if self._design_unreadable or output_changed:
            self._design_saved = saved and not changes.any and not self._design_unreadable
            self._design_waiting = output_changed and not self._design_saved
        else:
            self._save_design()
        self._load_description()
        self._design_changed()

    def _design_load_note(
        self,
        design: ExperimentDesign,
        changes: DesignChanges,
        problem: str | None,
        saved: bool,
        output: Path | None,
    ) -> str:
        """Say what was found in the output folder: an unreadable file, changes, a template."""
        if problem:
            return ui.status(
                "error",
                f"The saved {DESIGN_FILENAME} cannot be read. It was left unchanged.",
                [
                    problem,
                    "Correct the file (a spreadsheet program may have rewritten a date such "
                    "as 2026-10-26 as 26/10/2026; dates are written YYYY-MM-DD), then undo "
                    "and confirm the labels again to read it.",
                    "Or answer the questions below again: your first answer replaces the "
                    "file with the answers shown here.",
                ],
            )
        if saved and changes.any:
            lines = []
            if changes.added:
                lines.append(
                    "New biological-repeat folders: "
                    + ", ".join(f"{c}/{b}" for c, b in changes.added)
                    + ". Only the repeat groups they join are asked again."
                )
            if changes.removed:
                lines.append(
                    "No longer found: " + ", ".join(f"{c}/{b}" for c, b in changes.removed) + "."
                )
            return ui.status("info", "The folders changed since the design was saved.", lines)
        template = self._design_template
        if (
            template is not None
            and output is not None
            and template.parent == output.resolve()
            and not design.confirmed
        ):
            return ui.status(
                "info",
                f"Check folder wrote {DESIGN_FILENAME}, prefilled from the folders.",
                [
                    "Nothing is confirmed and no condition is a control until you answer the "
                    "questions below. Each answer is saved at once."
                ],
            )
        return ""

    def _close_design(self) -> None:
        self.design = None
        ui.set_visible(self.design_card, False)
        self._hide_description()
        if self.preflight is not None:
            self.dataset = self.preflight.dataset
            self._show_issue_summary()

    def _reopen_design(self) -> None:
        ui.set_visible(self.design_editor, True)
        ui.set_visible(self.design_change, False)
        self._refresh_design_status()

    def _build_design_editor(self) -> None:
        """Build the pairing dropdowns and the control checkboxes of the design."""
        design = self.design
        pairing = len(design.conditions) > 1 and not design.single_repeat
        ui.set_visible(self.pairing_box, pairing)
        options = sorted({row.biological_repeat for row in design.rows} | set(design.groups))
        rows = [
            widgets.GridBox(
                [widgets.HTML(f"<b>{h}</b>") for h in ("Condition", "Folder", "Repeat group")],
                layout=_PAIRING_COLUMNS,
            )
        ]
        units = self._reopen_units()
        folded: list[tuple[widgets.Widget, str]] = []
        for row in design.rows if pairing else ():
            dropdown = widgets.Dropdown(
                options=options, value=row.repeat_group, layout=widgets.Layout(width="99%")
            )
            dropdown.observe(
                lambda change, key=row.key: self._pair(key, change["new"]), names="value"
            )
            keys = [k for k in units or () if k[:2] == row.key]
            folded.append((
                widgets.GridBox(
                    [
                        widgets.HTML(html.escape(row.condition)),
                        self._folder_cell(
                            row.key,
                            f"<b>{html.escape(row.biological_repeat)}</b> "
                            f"<span class='mb-help'>{plural(row.image_count, 'image')}</span>",
                        ),
                        dropdown,
                    ],
                    layout=_PAIRING_COLUMNS,
                ),
                row_status(units, keys, answered=True),
            ))  # fmt: skip
        if not hasattr(self, "_pairing_fold"):
            self._pairing_fold = RowFold("folder")
        self.pairing_grid.children = rows + self._pairing_fold.arrange(folded)
        self.control_checks = {}
        for condition in design.conditions:
            check = widgets.Checkbox(
                value=condition in design.controls,
                description=condition,
                indent=False,
                layout=widgets.Layout(width="auto"),
            )
            check.observe(
                lambda change, c=condition: self._set_control(c, change["new"]), names="value"
            )
            self.control_checks[condition] = check
        self.controls_grid.children = [
            widgets.HBox(
                list(self.control_checks.values()), layout=widgets.Layout(flex_flow="wrap")
            )
        ]
        self._build_group_questions()

    def _folder_cell(self, key: tuple[str, str], text: str) -> widgets.Widget:
        """Return the cell naming a folder of the pairing (with its image count)."""
        return widgets.HTML(text)

    def _build_group_questions(self) -> None:
        """One yellow question per repeat group; rebuilt when the pairing changes."""
        design = self.design
        self.group_confirmations: dict[str, ui.ConfirmToggle] = {}
        boxes = [
            widgets.HTML(
                "<b>2. Confirm that the biological repeats are comparable</b>"
                if len(design.conditions) > 1 and not design.single_repeat
                else "<b>Confirm that the images are comparable</b>"
            )
        ]
        for group in design.groups:
            toggle = ui.ConfirmToggle(
                _group_question(design, group),
                _group_explanation(design, group),
                "Yes, they are comparable",
                "Confirmed: these images are comparable.",
                "Undo: they are not comparable",
            )
            toggle.set(design.group_confirmed(group), notify=False)
            toggle.disabled = group in design.conflicts
            toggle.observe(lambda value, g=group: self._confirm_group(g, value))
            self.group_confirmations[group] = toggle
            boxes.append(toggle.widget)
        self.groups_box.children = boxes

    def _pair(self, key: tuple[str, str], group: str) -> None:
        if self.design is None:
            return
        if self._edit_design(DesignEdit(repeat_groups={key: group})):
            self._build_group_questions()
        self._save_design()
        self._design_changed()

    def _confirm_group(self, group: str, confirmed: bool) -> None:
        if self.design is None:
            return
        edit = DesignEdit(confirm=(group,)) if confirmed else DesignEdit(unconfirm=(group,))
        if not self._edit_design(edit):
            self.group_confirmations[group].set(False, notify=False)
        self._save_design()
        self._design_changed()

    def _set_control(self, condition: str, control: bool) -> None:
        if self.design is None:
            return
        edit = DesignEdit(control=(condition,)) if control else DesignEdit(not_control=(condition,))
        self._edit_design(edit)
        self._save_design()
        self._refresh_design_status()

    def _edit_design(self, edit: DesignEdit) -> bool:
        """Apply an answer with the ``design`` command's rules; False when it is refused.

        :func:`.design_edit.apply_design_edit` confirms a repeat group only when its rows
        pass the run's validator; a refused answer changes nothing and is shown.
        """
        try:
            self.design = apply_design_edit(self.design, edit, date.today().isoformat())
        except DesignEditError as exc:
            self._design_refusal = ui.status("error", "This answer was not taken.", [str(exc)])
            return False
        self._design_refusal = ""
        return True

    def _save_design(self) -> None:
        """Write the design to the output folder under its lock; the log goes to the details."""
        output = self._design_folder()
        self._design_saved = self._design_waiting = False
        if output is None or self.design is None:
            return
        with self.log.attached():
            try:
                write_design(self.design, output)
            except OutputLockedError:
                self.folder_lock.show(output)
                self._design_refusal = ui.status(
                    "error",
                    "Not saved: another run holds the lock of the output folder.",
                    ["See The output folder is locked above; answer again once it is free."],
                )
                return
            except OutputInsideMasksError as exc:
                self._design_refusal = ui.status("error", "Not saved.", [str(exc)])
                return
            except OSError:
                logging.getLogger(__name__).warning("Cannot save the design", exc_info=True)
                return
            finally:
                self._show_log()
        self._design_saved = True
        if self._design_unreadable:
            self._design_unreadable = False
            self._design_note = ui.status(
                "info", f"Your answers replaced the unreadable {DESIGN_FILENAME}."
            )

    def _design_changed(self) -> None:
        """Update the balance, the messages, the status and the later steps after an answer."""
        self.dataset = check_dataset(
            self.preflight.discovery,
            self.design.repeat_groups() if self.design is not None else None,
            excluded=self.preflight.excluded,
        )
        self._show_issue_summary()
        self._refresh_design_status()
        self._sync_description()
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    def _refresh_design_status(self) -> None:
        """Show whether the design is confirmed and saved, with any note or refused answer."""
        design = self.design
        if design is None:
            self.design_status.value = ""
            return
        note = getattr(self, "_design_note", "") + getattr(self, "_design_refusal", "")
        saved = getattr(self, "_design_saved", False)
        if saved:
            where = f"Saved in <b>{html.escape(str(self._design_folder() / DESIGN_FILENAME))}</b>."
        elif self._design_unreadable:
            where = (
                f"Not saved: the unreadable {DESIGN_FILENAME} is kept as it is until you "
                "correct it or answer a question."
            )
        elif getattr(self, "_design_waiting", False) and self._design_folder() is not None:
            where = (
                "Not saved yet: your next answer saves it in "
                f"<b>{html.escape(str(self._design_folder() / DESIGN_FILENAME))}</b>."
            )
        else:
            where = (
                "Not saved: the output folder is not set or cannot be written; the answers "
                "are kept while the notebook is open and saved with the run."
            )
        controls = ", ".join(html.escape(c) for c in design.controls) or "none"
        # The counts leave out excluded images and folders (issue #82); say which.
        not_counted = html.escape(self.dataset.balance.not_counted())
        where = f"{not_counted}<br>{where}" if not_counted else where
        if design.confirmed:
            dates = sorted({design.confirmed_on(g) for g in design.groups} - {""})
            when = f" on {', '.join(dates)}" if dates else ""
            status = ui.status(
                "ok",
                f"The biological repeats are confirmed{when}.",
                [
                    f"{plural(len(design.groups), 'repeat group')}: "
                    + ", ".join(html.escape(g) for g in design.groups),
                    self._compared_line(controls),
                    where,
                ],
                raw=True,
            )
        else:
            waiting = [g for g in design.unconfirmed_groups if g not in design.conflicts]
            lines = []
            if design.conflicts:
                lines.append(
                    "A repeat group holds more than one folder of a condition: "
                    + ", ".join(html.escape(g) for g in design.conflicts)
                    + ". Move one of the folders into another group."
                )
            if waiting:
                lines.append(
                    f"Still to confirm: {plural(len(waiting), 'repeat group')} ("
                    + ", ".join(html.escape(g) for g in waiting)
                    + "). The run waits until every group is confirmed."
                )
            lines.append(where)
            status = ui.status("warn", "Please answer the questions below.", lines, raw=True)
        self.design_status.value = note + status

    def _compared_line(self, controls: str) -> str:
        """Return the status line about controls: the references' own words once answered."""
        if not self._references_answered():
            return f"Controls: {controls}"
        pairs = [
            f"{html.escape(c)} → {html.escape(t)}" for c, t in sorted(self.draft.references.items())
        ]
        return "Compared against (from references): " + (", ".join(pairs) or "nothing")

    def _design_confirmed(self) -> bool:
        return self.design is not None and self.design.confirmed

    def _design_pending(self) -> bool:
        """Return True while the design shown in step 1 still has questions to answer."""
        return self._show_design and not self._design_confirmed()


def _output_changed_note(output: Path | None, saved: bool, problem: str | None) -> str:
    """Say that the design follows the new output folder and what was found there (#98)."""
    if problem:
        return ""  # the refusal of the new folder's file says it all
    if output is None:
        title = "The output folder changed: the experiment design is asked again."
        lines = [
            "Choose an output folder that can be written, outside the experiment folder, to "
            "save the answers."
        ]
    elif saved:
        title = "The output folder changed: its experiment design was read."
        lines = [
            f"Read from <b>{html.escape(str(output / DESIGN_FILENAME))}</b>; nothing from the "
            "previous output folder was carried over."
        ]
    else:
        title = "The output folder changed: the experiment design is asked again."
        lines = [
            f"<b>{html.escape(str(output))}</b> holds no {DESIGN_FILENAME}. The answers given "
            "for the previous output folder (confirmations and controls) are not carried "
            "over: answer the questions below; each answer is saved in the new folder."
        ]
    return ui.status("info", title, lines, raw=True)
