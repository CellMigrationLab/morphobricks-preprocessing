"""The compact pages of the shipped notebook: what a step shows at once, and what on demand.

Owner's test of v0.5.0 (10 November 2026, items (a) and (b)): each step fits a laptop screen,
with fewer cards, short labels and the details collapsed. This module only arranges widgets the
other modules own; no answer, check or refusal is decided here.

- **Images** has one *Folders* card (three labelled rows); the pictures per folder and the
  check's report are collapsed once the headers are read.
- **Run** shows what is still missing only while something is, with the settings and *What
  will happen* collapsed; after a run, the intensity checks, **Compare groups** (the former
  optional statistics page: pooled statistics and the distribution plot of this run's table)
  and the report are collapsed panels. Its technical log is text in the page, not a box that
  scrolls by itself (owner's test of the redesign: the page scrolls, nothing inside it).
"""

from __future__ import annotations

import html

import ipywidgets as widgets

from . import notebook_style as ui
from .notebook_stepper import about, field_rows

FOLDER_INTRO = (
    "<b>What happens here:</b> first the folders are listed (labels from the folder names: "
    "condition / biological repeat / technical repeat) and one image is shown, so you can see "
    "that this is the right experiment. Then every image header is read and checked; problems "
    "are listed with what to do. Nothing in the folder is changed."
)
FOLDERS_HELP = (
    "<br><b>The folders:</b> paste a path or use <b>Browse…</b> (on Google Colab: Files panel, "
    "right-click, <b>Copy path</b>). Masks are optional: a folder outside the images, with one "
    "subfolder per mask type laid out like the images (<b>masks/cells/control/rep1/"
    "image1.tif</b>), or the images' folders with the mask type after the image name "
    "(<b>image1_cells.tif</b>); no mask pixel is read by the check. The output folder is where "
    "the results go; leave it empty for the default next to the images."
)
COMPARE_HELP = (
    "Optional; nothing is written. Statistics are <b>pooled</b> from the histograms of all "
    "planes of a group and channel: a group's median is the median of all its pixels."
)
MASKS_HINT = (
    "This run recorded masks. Bricks that use masks accept only masks that were read once: "
    "<b>Describe the masks</b> on Masks (step 4) reads them and reports how the types relate."
)
CHIPS = {"Check and save": "Save"}
"""The shorter names on the chips, so that the strip is one row on a laptop screen."""


class _CompactPages:
    """The cards of the *Images* and *Run* steps, arranged for one screen."""

    def _images_page(self) -> list[widgets.Widget]:
        """Return step 1's widgets: the folders, the scan, the check and what it found."""
        rows = field_rows(
            [
                ("Images", [self.folder, self.folder_browse]),
                ("Masks (optional)", [self.masks_folder, self.masks_browse]),
                ("Output (optional)", [self.output, self.output_browse]),
            ]
        )
        self.folders_card = ui.card(
            "Folders", [rows, self.output_hint, self.masks_recorded, self.masks_drop]
        )
        self.found_fold = ui.accordion("Folders found, with a picture of each", [self.labels_card])
        self.report_fold = ui.accordion("Check report", [self.check_report.widget])
        return [
            about(FOLDER_INTRO + FOLDERS_HELP),
            self.folders_card,
            self.scan_button,
            self.scan_status,
            self.scan_box,
            self.check_button,
            self.check_blocker,
            self.folder_status,  # the check's progress, right under the button clicked
            self.folder_lock.card,
            self.folder_tiles,
            self.issues_card,
            self.found_fold,
            self.report_fold,
            self.exclude_box,
        ]

    def _run_page(self) -> list[widgets.Widget]:
        """Return the run's widgets (the optional mask description is on the Masks step)."""
        self.settings_fold = ui.accordion("Settings and mode", [self.settings_card])
        self.plan_fold = ui.accordion("What will happen", [self.plan_card])
        self.checks_fold = ui.accordion("Intensity checks and plots", [self.run_checks.card])
        self.compare_fold = ui.accordion("Compare groups", [self._compare_box()])
        self.run_report_fold = ui.accordion("Run report", [self.run_report.widget])
        self.masks_hint = ui.help_text(MASKS_HINT)  # after a run that recorded masks
        self.log_text = widgets.HTML()
        self.details.observe(lambda _: self._write_log_text(), names="value")
        self.details_box.children = (self.log_text,)
        self._write_log_text()
        return [
            self.readiness_card,
            self.settings_fold,
            self.plan_fold,
            self.run_button,
            self.run_blocker,
            self.progress,
            self.run_status,
            self.run_lock.card,
            self.run_tiles,
            self.run_issues,
            self.masks_hint,
            self.checks_fold,
            self.compare_fold,
            self.run_report_fold,
            self.details_box,
        ]

    def _write_log_text(self) -> None:
        """Write the technical log as wrapped text, so the page scrolls and not the log."""
        text = self.details.value
        self.log_text.value = (
            "<pre style='white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; "
            f"font-size: 12px'>{html.escape(text)}</pre>"
            if text
            else ""
        )

    def _compare_box(self) -> widgets.Widget:
        """Return *Compare groups*: the grouping, the pooled statistics and the plot.

        The table is the one this run wrote: there is no table to choose, and the intensity
        checks are not repeated here (they are in their own panel above).
        """
        self._compare_only = True
        percentiles = widgets.HBox(
            [widgets.HTML("<b>Percentiles</b>&nbsp;"), self.percentiles],
            layout=widgets.Layout(align_items="center"),
        )
        return widgets.VBox(
            [
                ui.help_text(COMPARE_HELP),
                self.level,
                percentiles,
                self.statistics_button,
                self.stats_status,
                self.stats_tiles,
                self.statistics_card,
                self.distribution_card,
            ]
        )

    def _finish_run(self, result: object) -> None:
        super()._finish_run(result)
        self._refresh_folds()  # the checks, Compare groups and the report now have content

    def _refresh_folds(self) -> None:
        """Show each collapsed panel only while it has something in it."""
        if getattr(self, "compare_fold", None) is None:
            return
        written = self.result is not None and self.result.written
        ui.set_visible(self.found_fold, ui.is_visible(self.labels_card))
        ui.set_visible(self.report_fold, bool(self.check_report.where.value))
        ui.set_visible(self.readiness_card, self.run_button.disabled)  # only what is missing
        ui.set_visible(self.plan_fold, ui.is_visible(self.plan_card))
        ui.set_visible(self.checks_fold, written and ui.is_visible(self.run_checks.card))
        ui.set_visible(self.compare_fold, written)
        ui.set_visible(self.run_report_fold, bool(self.run_report.where.value))
        ui.set_visible(self.masks_hint, written and not self.mask_step_button.disabled)


__all__ = ["CHIPS", "COMPARE_HELP", "FOLDERS_HELP", "FOLDER_INTRO", "MASKS_HINT", "_CompactPages"]
