"""Step 1 of the notebook: the experiment folder, its check and the labels."""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.csv_io import OUTPUT_FILENAME

from . import notebook_style as ui
from .dataset import CONSISTENCY_CODES
from .discovery import check_output_folder, default_output_folder
from .manifest import MANIFEST_FILENAME
from .notebook_folder_previews import DRAWN_AT_ONCE, LabelPreviews
from .notebook_options import _WIDE, EXCLUDABLE_CODES
from .notebook_progress import Wait
from .notebook_render import (
    _folder_tiles,
    _labels_explanation,
    balance_html,
    issue_count_chips,
    issues_html,
    labels_html,
)
from .run import Preflight, preflight
from .unit_consistency import unit_checked
from .validation import ValidationIssue, plural

CHECK_LABEL = (" Check folder", "search")
CHECK_PHASES = {
    "folders": "Listing the folders",
    "headers": "Reading image headers",
    "masks": "Reading mask headers",
    "acquisitions": "Comparing the images",
}
"""What the folder check's wait says in each phase of :data:`.run.phases.PHASES`."""


def check_progress(wait: Wait) -> Callable[[str, int, int, str], None]:
    """Return the folder check's progress callback: it words each phase in ``wait``.

    The headers and the mask headers are counted (done of total, the file being read); the
    folder walk says how many folders were listed so far, since its total is not known.
    """
    state = {"phase": "", "listed": 0}

    def report(phase: str, done: int, total: int, path: str) -> None:
        if phase != state["phase"]:
            state["phase"] = phase
            wait.phase(CHECK_PHASES[phase], total if phase == "headers" else 0)
        if not path:
            return
        if phase == "folders":
            state["listed"] += 1
            wait.step(state["listed"], 0, path)
        else:
            wait.step(done, total, path)

    return report


class _FolderStep:
    """Step 1's folder and labels: check the folder, show its issues, confirm the labels."""

    # Step 1 ------------------------------------------------------------------------------

    def _build_folder_step(self, folder: str, output: str) -> None:
        """Build step 1: the folders, the check, the labels, consistency and messages."""
        self._build_folder_inputs(folder, output)
        self.exclude = widgets.Textarea(
            placeholder="control/bio_rep1/tech_rep1/damaged_image.tif",
            layout=widgets.Layout(width="99%", height="5em"),
        )
        self.exclude_box = ui.accordion(
            "Exclude images (optional)",
            [
                widgets.VBox(
                    [
                        self.exclude,
                        ui.help_text(
                            "One image per line, relative to the experiment folder. Excluded "
                            "images are not read and their earlier rows are removed. Use this "
                            "for damaged files; remove a line to analyse the image again. "
                            "When the output folder holds an earlier run, the list starts "
                            "with the images it excluded."
                        ),
                    ]
                )
            ],
        )
        self.check_button = ui.button(
            " Check folder",
            style="primary",
            icon="search",
            tooltip="Scan the folders and image headers (no pixels are read)",
            main=True,
        )
        self.folder_status = widgets.HTML()
        self.folder_next = widgets.HTML()
        self.folder_tiles = widgets.HTML()
        self._build_labels_card()
        self._build_consistency_card()
        self._build_issues_card()
        self.folder_browse.on_click(lambda _: self._browse(self.folder))
        self.output_browse.on_click(lambda _: self._browse(self.output))
        self.check_button.on_click(lambda _: self.check_folder())
        self.exclude_button.on_click(lambda _: self._exclude_blocking_images())
        self.folder.observe(lambda _: self._folder_edited(), names="value")
        self.output.observe(lambda _: self._output_edited(), names="value")
        self.labels_confirmed.observe(lambda _: self._labels_changed())
        self._build_design_card()
        self._build_description_cards()
        self._update_output_hint()

    def _build_folder_inputs(self, folder: str, output: str) -> None:
        """Build the experiment folder and output folder cards."""
        self.folder = widgets.Text(
            value=folder,
            placeholder="e.g. D:/microscopy/experiment_2026_10",
            layout=widgets.Layout(flex="1 1 auto", width="auto"),
        )
        self.folder_browse = ui.button(
            "Browse…", icon="folder-open", tooltip="Choose the experiment folder in a dialog"
        )
        self.output = widgets.Text(
            value=output,
            placeholder="leave empty to use the default folder shown below",
            layout=widgets.Layout(flex="1 1 auto", width="auto"),
        )
        self.output_browse = ui.button(
            "Browse…", icon="folder-open", tooltip="Choose another output folder"
        )
        self.output_hint = ui.help_text("")
        self.folder_card = ui.card(
            "Experiment folder",
            [
                widgets.HBox([self.folder, self.folder_browse], layout=widgets.Layout(**_WIDE)),
                ui.help_text(
                    "The folder that contains your condition folders, or the images "
                    "themselves. Paste the path or use <b>Browse…</b>. Nothing inside it is "
                    "ever changed."
                ),
            ],
        )
        self.output_card = ui.card(
            "Output folder (optional)",
            [
                widgets.HBox([self.output, self.output_browse], layout=widgets.Layout(**_WIDE)),
                self.output_hint,
            ],
        )

    def _build_labels_card(self) -> None:
        """Build the card of the labels taken from the folder names, and its confirmation."""
        self.labels_table = widgets.HTML()
        # A described run (the shipped notebook) draws the table with a picture per folder.
        self.label_previews = LabelPreviews()
        ui.set_visible(self.label_previews.widget, False)
        self.labels_confirmed = ui.ConfirmToggle(
            "Do the labels in the table match your experiment?",
            _labels_explanation(show_design=True),
            "Yes, the labels are correct",
            "Labels confirmed.",
            "Undo: the labels are not right",
        )
        self.labels_confirmed.disabled = True
        self.labels_card = ui.card(
            "Labels from the folder names",
            [
                ui.help_text(
                    "Each row is a folder of images and the labels its images get. "
                    "<i>single</i> means the level has one member: the folder level is "
                    "missing, or the folder is named single. To change a label, "
                    "rename the folder and press <b>Check folder</b> again."
                ),
                self.labels_table,
                self.label_previews.widget,
                self.labels_confirmed.widget,
            ],
            visible=False,
        )

    def _build_consistency_card(self) -> None:
        """Build the dataset consistency card: the balance table and its warnings."""
        self.balance_table = widgets.HTML()
        self.consistency_counts = widgets.HTML()
        self.consistency_issues = widgets.HTML()
        self.consistency_details = ui.accordion(
            "Show consistency warnings", [self.consistency_issues]
        )
        self.consistency_card = ui.card(
            "Dataset consistency",
            [
                ui.help_text(
                    "How many images each condition (column) and biological repeat (row) has, "
                    "over all its technical repeats. Amber cells are far from the others "
                    "(fewer than half or more than twice the median), or a biological repeat "
                    "the condition does not have. Below, the checks that "
                    "compare the images with each other: pixel type, dimensions, sizes, "
                    "numbers of planes, channel names and pixel sizes. Warnings do not stop "
                    "processing; they say what to check."
                ),
                self.balance_table,
                self.consistency_counts,
                self.consistency_details,
            ],
            visible=False,
        )

    def _build_issues_card(self) -> None:
        """Build the messages card, with the button that excludes unreadable images."""
        self.folder_issues = widgets.HTML()
        self.exclude_button = ui.button(
            " Exclude these images and check again",
            style="warning",
            icon="ban",
            tooltip="Add the images that cannot be processed to the exclusion list",
        )
        ui.set_visible(self.exclude_button, False)
        self.issue_counts = widgets.HTML()
        self.issue_details = ui.accordion(
            "Show warnings and errors", [widgets.VBox([self.folder_issues, self.exclude_button])]
        )
        self.issues_card = ui.card(
            "Messages", [self.issue_counts, self.issue_details], visible=False
        )

    def check_folder(self) -> Preflight | None:
        """Run the folder and header check (pass 1) and show its results."""
        folder = ui.clean_path(self.folder.value)
        if not folder:
            self.folder_status.value = ui.status(
                "warn", "Enter the experiment folder first.", ["Paste its path or use Browse…."]
            )
            return None
        self._busy(self.check_button, "Checking…")
        self.folder_next.value = ""
        wait = self._folder_wait = Wait(self.folder_status)
        wait.start(CHECK_PHASES["folders"])
        try:
            with self.log.attached():
                masks, mask_axes = self._check_masks_arguments(Path(folder).resolve())
                pre = preflight(
                    folder,
                    self._output_folder(),
                    exclude=self._excluded(),
                    masks_folder=masks,
                    mask_axes=mask_axes,
                    # Each header is bound to the file's fingerprint: every picture and the
                    # save are of the images as this check read them (audit batch 33).
                    bind_headers=True,
                    progress=check_progress(wait),
                )
                pre = unit_checked(pre)  # a described run checks inside each unit
                self._note_edits()  # a check fills only what the user left as filled
                self.missing_as_singleton.set(self._recorded_missing_as_singleton(), notify=False)
                # The report reads images lacking some axes by that answer, as 'check
                # --missing-as-singleton' does (F10); step 2 asks again before the run.
                wait.phase("Writing the check report")
                self._write_check(pre, self.missing_as_singleton.value)
        finally:
            self._idle(self.check_button, *getattr(self, "_check_label", CHECK_LABEL))
        self.preflight = pre
        self._checked_folder = folder
        self.mapping = pre.suggested_mapping
        self.confirmed_mapping = None
        self.result = None
        self._show_folder_results(pre)
        self._reset_settings(pre)
        self._refresh_axis_step()
        self._refresh_run_step()
        self._show_log()
        return pre

    def _show_folder_results(self, pre: Preflight) -> None:
        """Show the labels, messages and pixel sizes of a new check; the pairing starts over."""
        self.pairing = None
        self.dataset = pre.dataset
        ui.set_visible(self.design_card, False)
        self._hide_description()
        self.labels_table.value = labels_html(pre)
        pictures = getattr(self, "_folder_pictures", True)  # the stepper has its viewer
        wait = getattr(self, "_folder_wait", None)
        if wait is not None and pictures:
            wait.phase(
                "Drawing a picture per folder", min(len(pre.discovery.leaves), DRAWN_AT_ONCE)
            )
        if pictures:
            self.label_previews.show(pre, None if wait is None else wait.step)
        else:
            self.label_previews.clear()
        ui.set_visible(self.labels_table, not pictures)
        ui.set_visible(self.label_previews.widget, pictures)
        self.labels_confirmed.set(False, notify=False)
        self.labels_confirmed.disabled = not pre.discovery.leaves
        ui.set_visible(self.labels_card, bool(pre.discovery.leaves))
        self.consistency_details.selected_index = None
        self.issue_details.selected_index = None
        self._show_issue_summary()
        self._show_calibration()

    def _step1_issues(self) -> list[ValidationIssue]:
        """Return the folder-check issues, with the balance warnings of the current pairing."""
        pre = self.preflight
        merged = [
            *pre.discovery.issues,
            *pre.exclusion_issues,
            *pre.metadata.issues,
            *self.dataset.issues,
        ]
        return sorted(merged, key=lambda issue: not issue.is_error)

    def _show_issue_summary(self) -> None:
        """Show the check's status, tiles, consistency and messages (masks on their card)."""
        pre = self.preflight
        issues = self._step1_issues()
        counted = [*issues, *self._mask_issues_now()]
        errors = [i for i in counted if i.is_error]
        warnings = [i for i in counted if not i.is_error]
        if errors:
            self.folder_status.value = ui.status(
                "error",
                f"Action required: {plural(len(errors), 'problem')} must be fixed first.",
                ["Read the messages below; each one says what to do.", "Then check again."],
            )
        elif warnings:
            self.folder_status.value = ui.status(
                "warn",
                f"Ready, with {plural(len(warnings), 'warning')} to read.",
            )
        else:
            self.folder_status.value = ui.status(
                "ok",
                "The folder looks good.",
            )
        self.folder_tiles.value = ui.tiles(_folder_tiles(pre, counted))
        # Consistency issues have their own card next to the balance table; the
        # messages card keeps the folder, file and readability problems.
        consistency = [i for i in issues if i.code in CONSISTENCY_CODES]
        others = [i for i in issues if i.code not in CONSISTENCY_CODES]
        self.balance_table.value = balance_html(self.dataset.balance)
        self.consistency_counts.value = issue_count_chips(consistency) or ui.status(
            "ok", "The images are consistent with each other."
        )
        self.consistency_issues.value = issues_html(consistency)
        ui.set_visible(self.consistency_details, bool(consistency))
        ui.set_visible(self.consistency_card, bool(pre.discovery.leaves))
        self.folder_issues.value = issues_html(others)
        self.issue_counts.value = issue_count_chips(others)
        ui.set_visible(self.issues_card, bool(others))
        # A hidden output card comes back when its folder is the problem to fix.
        ui.set_visible(
            self.output_card,
            self._show_output or any(i.code == "output_inside_experiment" for i in issues),
        )
        blocking = any(i.code in EXCLUDABLE_CODES for i in issues)
        ui.set_visible(self.exclude_button, blocking)
        self._refresh_folder_next()

    def _labels_changed(self) -> None:
        if self.labels_confirmed.value and self.preflight is not None and self._show_design:
            self._open_design()
        else:
            self._close_design()
        self._refresh_folder_next()
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    def _refresh_folder_next(self) -> None:
        """Say what to do next in step 1."""
        self.folder_next.value = ""
        if self.preflight is None or not self.preflight.ok:
            return
        if self.labels_confirmed.value:
            self.folder_next.value = ui.next_steps(
                "Go to Step 2 and review the axis roles.",
                "Run its cell, check the preview, then click Confirm axis roles.",
            )
        else:
            self.folder_next.value = ui.next_steps(
                "Check the labels in Step 1 and confirm them.",
                "Read any warnings and review the labels, then click Yes, the labels are correct.",
            )

    def _folder_edited(self) -> None:
        """Make every later choice stale when the folder differs from the one checked."""
        self._update_output_hint()
        self._prefill_from_output()  # the default output folder follows the experiment
        if self.preflight is None or ui.clean_path(self.folder.value) == self._checked_folder:
            return
        # A different folder makes every later choice stale.
        self.preflight = None
        self.mapping = self.confirmed_mapping = None
        self.result = None
        self.folder_status.value = ui.status(
            "info", "The folder changed.", ["Press Check folder to check the new folder."]
        )
        self.folder_tiles.value = ""
        self.folder_next.value = ""
        self.labels_confirmed.set(False, notify=False)
        self.pairing = None
        ui.set_visible(self.design_card, False)
        self._hide_description()
        ui.set_visible(self.labels_card, False)
        self.label_previews.clear()
        ui.set_visible(self.consistency_card, False)
        ui.set_visible(self.issues_card, False)
        ui.set_visible(self.calibration_card, False)
        self._reset_masks()
        self.folder_lock.hide()
        self.check_report.clear()
        self._refresh_axis_step()
        self._refresh_run_step()

    def _output_edited(self) -> None:
        self._update_output_hint()
        # The inputs start from the earlier run in the new output folder (audit I4).
        self._prefill_from_output()
        # The answers belong to their output folder (issue #98): another folder's draft is
        # read, or the questions asked again; nothing is carried over or written here.
        if self.pairing is not None and self._design_folder() != self._design_output:
            self._open_design(output_changed=True)
        # "What will happen" and the free disk space depend on the output folder.
        if hasattr(self, "readiness"):
            self._refresh_run_step()

    def _update_output_hint(self) -> None:
        """Say where the results will be written, and warn about a folder inside."""
        folder = ui.clean_path(self.folder.value)
        output = self._output_folder()
        if not folder and not output:
            self.output_hint.value = (
                "<div class='mb-help'>Results go next to the experiment folder, in "
                "<b>&lt;experiment&gt;_MorphoBricks_Preprocessing</b>.</div>"
            )
            return
        target = Path(output) if output else default_output_folder(folder)
        lines = [
            f"Results will be written to <b>{html.escape(str(target))}</b>"
            + ("" if output else " (default)")
            + f": <b>{OUTPUT_FILENAME}</b> and <b>{MANIFEST_FILENAME}</b>."
        ]
        if folder and output and check_output_folder(folder, output):
            lines.append(
                "<span style='color:#991B1B'>This folder is inside the experiment folder; "
                "choose one outside it.</span>"
            )
        blocking = self._output_file_in_the_way()
        if blocking is not None:
            lines.append(
                f"<span style='color:#991B1B'><b>{html.escape(str(blocking))}</b> is a file, "
                "not a folder, so nothing can be written there; choose another output "
                "folder.</span>"
            )
        self.output_hint.value = f"<div class='mb-help'>{'<br>'.join(lines)}</div>"

    def _output_file_in_the_way(self) -> Path | None:
        """Return the file the output folder is, or is under; None if it can be a folder.

        A path such as ``somefile/out`` can never be created, so the notebook says so
        instead of failing with ``NotADirectoryError`` when it looks there.
        """
        folder = ui.clean_path(self.folder.value)
        output = self._output_folder()
        if not folder and not output:
            return None
        target = Path(output or default_output_folder(folder)).absolute()
        for path in (target, *target.parents):
            if path.exists():
                return None if path.is_dir() else path
        return None

    def _browse(self, target: widgets.Text) -> None:
        chosen = ui.ask_directory(ui.clean_path(target.value))
        if chosen is None:
            self.folder_status.value = ui.no_dialog_status("folder", "the folder")
        elif chosen:
            target.value = chosen

    def _exclude_blocking_images(self) -> None:
        if self.preflight is None:
            return
        paths = {p for i in self.preflight.issues if i.code in EXCLUDABLE_CODES for p in i.paths}
        self.exclude.value = "\n".join(sorted(set(self._excluded()) | paths))
        self.exclude_box.selected_index = 0
        self.check_folder()
