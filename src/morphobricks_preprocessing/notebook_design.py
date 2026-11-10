"""Step 1 of the notebook: the experiment design card (the pairing of the repeats).

The pairing is kept in the draft and confirmed on *Check and save* (:mod:`.notebook_described`,
which opens the card and reports its status); nothing here reads or writes
``experiment_design.csv``: the run derives it from the published description. The card's model
(:class:`.design.ExperimentDesign`, :func:`.design_edit.apply_design_edit`) is rebuilt on the
draft in a later change (SWEEP-PP, owner answer 4).
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .dataset import check_dataset
from .design_edit import DesignEdit, DesignEditError, apply_design_edit
from .discovery import check_output_folder, default_output_folder
from .notebook_options import _PAIRING_COLUMNS
from .notebook_render import _group_explanation, _group_question
from .notebook_row_fold import RowFold, row_status
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
                    "biological repeat, and that their images are comparable. Your pairing is "
                    "saved with your other answers in the draft; <b>experiment_design.csv</b> "
                    "is written only when you save the experiment description (Check and "
                    "save)."
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

    def _design_confirmed(self) -> bool:
        return self.design is not None and self.design.confirmed
