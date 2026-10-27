"""Widgets for the biologist-facing notebook.

The notebook ``notebooks/MorphoBricks_Preprocessing.ipynb`` shows the four steps of
:class:`PreprocessingNotebook`, one per cell:

1. **Folder** - choose the experiment folder, check it, confirm the labels
   taken from the folder names, and confirm the experiment design: which
   biological repeats belong together across conditions, that they are
   comparable, and which conditions are controls.
2. **Axes** - review the role of every image axis, look at planes with the
   preview, and confirm.
3. **Run** - check that everything is ready, see what will happen, and compute
   or update the histogram table.
4. **Results** - pooled statistics and intensity distributions per group.

Each step is a page of cards (see :mod:`.notebook_style`): a blue box says what
the step is for, every control has a help line, the main button is at the
bottom, a status card says what happened, and a separate "What next?" box explains
the next action. Later steps stay locked until the earlier ones are confirmed, and
changing the folder resets them. The widgets only collect choices and show results; every check and
computation is done by the same functions the command line and the tests use.
Needs the ``notebook`` extra (``ipywidgets`` and ``matplotlib``).
"""

from __future__ import annotations

import html
import io
import logging
import math
import traceback
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

import numpy as np

try:
    import ipywidgets as widgets
    from matplotlib.figure import Figure
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The notebook interface needs ipywidgets and matplotlib. Install them with: "
        'pip install "morphobricks-preprocessing[notebook]"'
    ) from exc

from morphobricks_normalization.csv_io import OUTPUT_FILENAME, CsvFormatError, read_plane_histograms
from morphobricks_normalization.dtypes import (
    MAX_BIN_COUNT,
    MIN_FLOAT_BIN_COUNT,
    default_bin_count,
    is_integer,
)
from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import group_records

from . import notebook_style as ui
from .axes import (
    AxisMapping,
    AxisReview,
    axis_length_ranges,
    axis_name,
    file_mapping_for,
    review_axes,
)
from .dataset import CONSISTENCY_CODES, BalanceTable, DatasetReport, check_dataset
from .design import (
    DESIGN_FILENAME,
    DesignChanges,
    ExperimentDesign,
    default_design,
    load_design,
    write_experiment_design,
)
from .discovery import check_output_folder, default_output_folder
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .planes import (
    DEFAULT_MEMORY_LIMIT,
    Plane,
    PlaneLayout,
    PlaneReadError,
    format_bytes,
    plane_layout,
    read_plane,
)
from .report import LEVEL_NAMES, group_summaries, group_table, issue_counts
from .run import Preflight, RunResult, preflight, run_experiment
from .validation import ValidationIssue, has_errors, plural
from .zarr_export import (
    CHUNK_SIDE,
    COMPRESSIONS,
    MAX_CHUNK_SIZE,
    MAX_LEVELS,
    MIN_CHUNK_SIZE,
    OME_ZARR_FOLDER,
    ConversionSettings,
    estimate_conversion,
    free_space,
    zarr_available,
)

ROLE_OPTIONS = [
    ("Z - depth (focal planes)", "Z"),
    ("C - channel (stain or colour)", "C"),
    ("T - time (frames)", "T"),
    ("Y - height (image rows)", "Y"),
    ("X - width (image columns)", "X"),
    ("No role (only for length 1)", "-"),
]

LEVEL_OPTIONS = [
    ("Condition", "condition"),
    ("Biological repeat", "biological_repeat"),
    ("Technical repeat", "technical_repeat"),
    ("Image", "image"),
]

# Problems a user can get past by leaving the affected images out (open question 6).
EXCLUDABLE_CODES = ("unreadable_tiff", "multiple_series", "unsupported_dtype")

_SKIPPED_CODES = ("non_tiff_file", "tiff_outside_leaf", "empty_folder")

# "What will happen" is a short summary, so its lists of images fold early.
_PLAN_PATHS = 5

CONVERSION_OPTIONS = [
    ("Read the images directly (recommended; nothing extra is saved)", "read"),
    ("Convert the TIFF images to OME-Zarr, then process the converted copies", "convert"),
]

PYRAMID_OPTIONS = [
    ("Automatic (recommended)", "auto"),
    ("Full resolution only", "none"),
    ("Choose the number of smaller copies", "fixed"),
]
_MAX_PLOTTED_GROUPS = 12
_PACKAGE_LOGGER = "morphobricks_preprocessing"
_WIDE = {"width": "99%"}
_PAIRING_COLUMNS = widgets.Layout(
    grid_template_columns="minmax(120px, 1fr) minmax(120px, 1fr) minmax(160px, 2fr)",
    grid_gap="4px 12px",
    width="99%",
)


class PreprocessingNotebook:
    """The steps of the notebook, sharing one state.

    Parameters
    ----------
    experiment_folder
        Folder to prefill in step 1.
    output_folder
        Output folder to prefill; empty means the default next to the experiment.

    Attributes
    ----------
    preflight
        Result of the last folder check, or None.
    mapping
        The axis roles as currently edited in step 2.
    confirmed_mapping
        The axis roles the user confirmed, or None.
    design
        The experiment design as answered in step 1, or None until the labels
        are confirmed.
    dataset
        Images per condition and repeat group with the current pairing, and the
        warnings about them.
    result
        Result of the last run, or None.
    """

    def __init__(self, experiment_folder: str = "", output_folder: str = ""):
        self.preflight: Preflight | None = None
        self.design: ExperimentDesign | None = None
        self.dataset = DatasetReport()
        self.mapping: AxisMapping | None = None
        self.confirmed_mapping: AxisMapping | None = None
        self.result: RunResult | None = None
        self.log = _LogCapture()
        self._checked_folder = ""
        self._preview_shown = False
        self._layouts_cache: tuple[tuple, dict[str, PlaneLayout]] | None = None
        self._build_folder_step(str(experiment_folder), str(output_folder))
        self._build_axis_step()
        self._build_run_step()
        self._build_results_step()
        self._refresh_axis_step()
        self._refresh_run_step()

    # Pages -------------------------------------------------------------------------------

    def welcome(self) -> widgets.Widget:
        """Confirm that the notebook started, with the package version and what to do next."""
        from . import __version__

        return ui.page(
            "MorphoBricks preprocessing",
            "Per-plane intensity histograms for an experiment folder of TIFF and OME-Zarr images.",
            [
                widgets.HTML(
                    ui.status(
                        "ok",
                        f"The notebook is ready (morphobricks-preprocessing {__version__}).",
                    )
                ),
                widgets.HTML(
                    ui.next_steps(
                        "Go to Step 1 and check your experiment folder.",
                        "Run its cell, choose your image folder, then click Check folder.",
                    )
                ),
            ],
        )

    def folder_step(self) -> widgets.Widget:
        """Step 1: choose and check the experiment folder, confirm the labels."""
        return ui.page(
            "Step 1 · Check the experiment folder",
            "Choose the folder with your images. It is checked without reading any pixels "
            "and without changing anything.",
            [
                ui.intro(
                    "<b>What happens here:</b> the folders are scanned, the labels are taken "
                    "from the folder names (condition / biological repeat / technical repeat) "
                    "and every image header is checked. Problems are listed with what to do."
                ),
                self.folder_card,
                self.output_card,
                self.exclude_box,
                self.check_button,
                self.folder_status,
                self.folder_tiles,
                self.labels_card,
                self.design_card,
                self.consistency_card,
                self.issues_card,
                self.folder_next,
            ],
        )

    def axis_step(self) -> widgets.Widget:
        """Step 2: axis roles, preview and confirmation."""
        return ui.page(
            "Step 2 · Review the axis roles",
            "Tell the brick which image dimension is width, height, depth, channel and time.",
            [
                ui.intro(
                    "<b>Why this matters:</b> every image is cut into flat 2D planes, one per "
                    "depth slice, channel and time point. The roles below are suggested from "
                    "the image metadata, which is not always right, so check them with the "
                    "preview before confirming."
                ),
                self.axis_box,
            ],
        )

    def run_step(self) -> widgets.Widget:
        """Step 3: readiness, settings and the run."""
        return ui.page(
            "Step 3 · Compute the histograms",
            "Read the images one plane at a time and write the histogram table.",
            [
                self.readiness_card,
                self.settings_card,
                self.conversion_card,
                self.plan_card,
                self.run_button,
                self.progress,
                self.run_status,
                self.run_tiles,
                self.run_issues,
                self.details_box,
                self.run_next,
            ],
        )

    def results_step(self) -> widgets.Widget:
        """Step 4: pooled statistics and intensity distributions from the table."""
        return ui.page(
            "Step 4 · Calculate the statistics (optional)",
            "Compare conditions and repeats using the histograms computed in step 3.",
            [
                ui.intro(
                    "<b>This step is optional.</b> The histogram table written in step 3 is "
                    "all that later MorphoBricks bricks need; this step does not change it "
                    "and writes no files. Use it to compare groups and check that the "
                    "brightness looks as expected.<br>Statistics are <b>pooled</b> from the "
                    "histograms of all planes in each group: a group's median is the median "
                    "of all its pixels, not an average of per-image medians."
                ),
                self.csv_card,
                self.results_card,
                self.stats_tiles,
                self.statistics_card,
                self.distribution_card,
                self.stats_next,
            ],
        )

    # Step 1 ------------------------------------------------------------------------------

    def _build_folder_step(self, folder: str, output: str) -> None:
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
        self.exclude = widgets.Textarea(
            placeholder="control/bio_rep1/tech_rep1/damaged_image.tif",
            layout=widgets.Layout(width="99%", height="5em"),
        )
        self.exclude_box = widgets.Accordion(
            children=[
                widgets.VBox(
                    [
                        self.exclude,
                        ui.help_text(
                            "One image per line, relative to the experiment folder. Excluded "
                            "images are not read and their earlier rows are removed. Use this "
                            "for damaged files; remove a line to analyse the image again."
                        ),
                    ]
                )
            ],
            titles=("Exclude images (optional)",),
            selected_index=None,
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
        self.labels_table = widgets.HTML()
        self.labels_confirmed = ui.ConfirmToggle(
            "Do the labels in the table match your experiment?",
            "Check that every image folder has the right condition, biological repeat and "
            "technical repeat. If a label is wrong, rename the folder and click <b>Check "
            "folder</b> again. Step 3 can only run once the labels are confirmed.",
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
                    "<i>single</i> means the folder level is missing. To change a label, "
                    "rename the folder and press <b>Check folder</b> again."
                ),
                self.labels_table,
                self.labels_confirmed.widget,
            ],
            visible=False,
        )
        self.balance_table = widgets.HTML()
        self.consistency_counts = widgets.HTML()
        self.consistency_issues = widgets.HTML()
        self.consistency_details = widgets.Accordion(
            children=[self.consistency_issues],
            titles=("Show consistency warnings",),
            selected_index=None,
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
        self.folder_issues = widgets.HTML()
        self.exclude_button = ui.button(
            " Exclude these images and check again",
            style="warning",
            icon="ban",
            tooltip="Add the images that cannot be processed to the exclusion list",
        )
        ui.set_visible(self.exclude_button, False)
        self.issue_counts = widgets.HTML()
        self.issue_details = widgets.Accordion(
            children=[widgets.VBox([self.folder_issues, self.exclude_button])],
            titles=("Show warnings and errors",),
            selected_index=None,
        )
        self.issues_card = ui.card(
            "Messages", [self.issue_counts, self.issue_details], visible=False
        )

        self.folder_browse.on_click(lambda _: self._browse(self.folder))
        self.output_browse.on_click(lambda _: self._browse(self.output))
        self.check_button.on_click(lambda _: self.check_folder())
        self.exclude_button.on_click(lambda _: self._exclude_blocking_images())
        self.folder.observe(lambda _: self._folder_edited(), names="value")
        self.output.observe(lambda _: self._output_edited(), names="value")
        self.labels_confirmed.observe(lambda _: self._labels_changed())
        self._build_design_card()
        self._update_output_hint()

    def _build_design_card(self) -> None:
        self.design_status = widgets.HTML()
        self.design_change = ui.button(
            " Change the experiment design",
            style="warning",
            icon="pencil",
            tooltip="Show the pairing, the confirmations and the controls again",
        )
        ui.set_visible(self.design_change, False)
        self.pairing_grid = widgets.VBox()
        self.pairing_box = widgets.VBox(
            [
                widgets.HTML("<b>1. Pair the biological repeats across conditions</b>"),
                ui.help_text(
                    "A repeat group is one biological repeat in every condition: the same "
                    "preparation, imaged for each condition. Folders with the same name are "
                    "paired by default. If a repeat has another folder name in some "
                    "condition (acquired a day later, for example), choose its group in "
                    "the dropdown. Each group may hold one folder per condition."
                ),
                self.pairing_grid,
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
        self.folder_status.value = ui.working("Checking the folders and image headers…")
        try:
            with self.log.attached():
                pre = preflight(folder, self._output_folder(), exclude=self._excluded())
        finally:
            self._idle(self.check_button, " Check folder", "search")
        self.preflight = pre
        self._checked_folder = folder
        self.mapping = pre.suggested_mapping
        self.confirmed_mapping = None
        self.result = None
        self.missing_as_singleton.set(False, notify=False)
        self._show_folder_results(pre)
        self._reset_settings(pre)
        self._refresh_axis_step()
        self._refresh_run_step()
        self._show_log()
        return pre

    def _show_folder_results(self, pre: Preflight) -> None:
        self.design = None
        self.dataset = pre.dataset
        ui.set_visible(self.design_card, False)
        self.labels_table.value = labels_html(pre)
        self.labels_confirmed.set(False, notify=False)
        self.labels_confirmed.disabled = not pre.discovery.leaves
        ui.set_visible(self.labels_card, bool(pre.discovery.leaves))
        self.consistency_details.selected_index = None
        self.issue_details.selected_index = None
        self._show_issue_summary()

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
        pre = self.preflight
        issues = self._step1_issues()
        errors = [i for i in issues if i.is_error]
        warnings = [i for i in issues if not i.is_error]
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
        self.folder_tiles.value = ui.tiles(_folder_tiles(pre, issues))
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
        blocking = any(i.code in EXCLUDABLE_CODES for i in issues)
        ui.set_visible(self.exclude_button, blocking)
        self._refresh_folder_next()

    def _labels_changed(self) -> None:
        if self.labels_confirmed.value and self.preflight is not None:
            self._open_design()
        else:
            self._close_design()
        self._refresh_folder_next()
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    # Experiment design (step 1) -------------------------------------------------------------

    def _design_folder(self) -> Path | None:
        """Return the output folder the design is saved in, or None if it may not be written."""
        folder = ui.clean_path(self.folder.value)
        if not folder:
            return None
        output = Path(self._output_folder() or default_output_folder(folder))
        return None if check_output_folder(folder, output) else output

    def _open_design(self) -> None:
        """Load the saved design, or start a new one, once the labels are confirmed."""
        repeats = self.preflight.dataset.repeats
        output = self._design_folder()
        changes, problem, saved = DesignChanges(), None, False
        if output is None:
            design = default_design(repeats)
        else:
            design, changes, problem = load_design(output, repeats)
            saved = problem is None and (output / DESIGN_FILENAME).exists()
        self.design = design
        self._build_design_editor()
        # A design confirmed earlier for the same folders is not asked again.
        reopen = not (saved and design.confirmed and not changes.any)
        ui.set_visible(self.design_editor, reopen)
        ui.set_visible(self.design_change, not reopen)
        self._design_note = self._design_load_note(design, changes, problem, saved)
        ui.set_visible(self.design_card, True)
        self._save_design()
        self._design_changed()

    def _design_load_note(
        self, design: ExperimentDesign, changes: DesignChanges, problem: str | None, saved: bool
    ) -> str:
        if problem:
            return ui.status(
                "warn",
                f"The saved {DESIGN_FILENAME} could not be read, so the questions are asked again.",
                [problem, "Answering replaces the file."],
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
        return ""

    def _close_design(self) -> None:
        self.design = None
        ui.set_visible(self.design_card, False)
        if self.preflight is not None:
            self.dataset = self.preflight.dataset
            self._show_issue_summary()

    def _reopen_design(self) -> None:
        ui.set_visible(self.design_editor, True)
        ui.set_visible(self.design_change, False)
        self._refresh_design_status()

    def _build_design_editor(self) -> None:
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
        for row in design.rows if pairing else ():
            dropdown = widgets.Dropdown(
                options=options, value=row.repeat_group, layout=widgets.Layout(width="99%")
            )
            dropdown.observe(
                lambda change, key=row.key: self._pair(key, change["new"]), names="value"
            )
            rows.append(
                widgets.GridBox(
                    [
                        widgets.HTML(html.escape(row.condition)),
                        widgets.HTML(f"<b>{html.escape(row.biological_repeat)}</b>"),
                        dropdown,
                    ],
                    layout=_PAIRING_COLUMNS,
                )
            )
        self.pairing_grid.children = rows
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
        self.design = self.design.with_group(key, group)
        self._build_group_questions()
        self._save_design()
        self._design_changed()

    def _confirm_group(self, group: str, confirmed: bool) -> None:
        if self.design is None:
            return
        self.design = self.design.with_confirmed(
            group, confirmed, date.today().isoformat(), self.preflight.dataset.repeats
        )
        self._save_design()
        self._design_changed()

    def _set_control(self, condition: str, control: bool) -> None:
        if self.design is None:
            return
        self.design = self.design.with_control(condition, control)
        self._save_design()
        self._refresh_design_status()

    def _save_design(self) -> None:
        output = self._design_folder()
        self._design_saved = False
        if output is None or self.design is None:
            return
        try:
            write_experiment_design(self.design, output / DESIGN_FILENAME)
        except OSError:
            logging.getLogger(__name__).warning("Cannot save the design", exc_info=True)
            return
        self._design_saved = True

    def _design_changed(self) -> None:
        """Update the balance, the messages, the status and the later steps after an answer."""
        self.dataset = check_dataset(
            self.preflight.discovery,
            self.design.repeat_groups() if self.design is not None else None,
            excluded=self.preflight.excluded,
        )
        self._show_issue_summary()
        self._refresh_design_status()
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    def _refresh_design_status(self) -> None:
        design = self.design
        if design is None:
            self.design_status.value = ""
            return
        note = getattr(self, "_design_note", "")
        saved = getattr(self, "_design_saved", False)
        where = (
            f"Saved in <b>{html.escape(str(self._design_folder() / DESIGN_FILENAME))}</b>."
            if saved
            else "Not saved: the output folder is not set or cannot be written; the answers "
            "are kept while the notebook is open and saved with the run."
        )
        controls = ", ".join(html.escape(c) for c in design.controls) or "none"
        if design.confirmed:
            dates = sorted({design.confirmed_on(g) for g in design.groups} - {""})
            when = f" on {', '.join(dates)}" if dates else ""
            status = ui.status(
                "ok",
                f"The biological repeats are confirmed{when}.",
                [
                    f"{plural(len(design.groups), 'repeat group')}: "
                    + ", ".join(html.escape(g) for g in design.groups),
                    f"Controls: {controls}",
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
                    + "). Step 3 runs once every group is confirmed."
                )
            lines.append(where)
            status = ui.status("warn", "Please answer the questions below.", lines, raw=True)
        self.design_status.value = note + status

    def _design_confirmed(self) -> bool:
        return self.design is not None and self.design.confirmed

    def _refresh_folder_next(self) -> None:
        self.folder_next.value = ""
        if self.preflight is None or not self.preflight.ok:
            return
        if self.labels_confirmed.value and not self._design_confirmed():
            self.folder_next.value = ui.next_steps(
                "Confirm the biological repeats in Step 1.",
                "Under Experiment design, pair the repeats if needed and answer each yellow "
                "question with Yes, they are comparable.",
            )
        elif self.labels_confirmed.value:
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
        self._update_output_hint()
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
        self.design = None
        ui.set_visible(self.design_card, False)
        ui.set_visible(self.labels_card, False)
        ui.set_visible(self.consistency_card, False)
        ui.set_visible(self.issues_card, False)
        self._refresh_axis_step()
        self._refresh_run_step()

    def _output_edited(self) -> None:
        self._update_output_hint()
        # "What will happen" and the free disk space depend on the output folder.
        if hasattr(self, "readiness"):
            self._refresh_run_step()

    def _update_output_hint(self) -> None:
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
        self.output_hint.value = f"<div class='mb-help'>{'<br>'.join(lines)}</div>"

    def _browse(self, target: widgets.Text) -> None:
        chosen = ui.ask_directory(ui.clean_path(target.value))
        if chosen is None:
            self.folder_status.value = ui.status(
                "warn",
                "The folder dialog could not be opened here.",
                ["Paste the path of the folder into the box instead."],
            )
        elif chosen:
            target.value = chosen

    def _exclude_blocking_images(self) -> None:
        if self.preflight is None:
            return
        paths = {p for i in self.preflight.issues if i.code in EXCLUDABLE_CODES for p in i.paths}
        self.exclude.value = "\n".join(sorted(set(self._excluded()) | paths))
        self.exclude_box.selected_index = 0
        self.check_folder()

    # Step 2 ------------------------------------------------------------------------------

    def _build_axis_step(self) -> None:
        self.axis_box = widgets.VBox()
        self.role_dropdowns: list[widgets.Dropdown] = []
        self.role_notes: list[widgets.HTML] = []
        self.plane_summary = widgets.HTML()
        self.axis_status = widgets.HTML()
        self.missing_note = widgets.HTML()
        self.missing_as_singleton = ui.ConfirmToggle(
            "Do these images really have a single plane along the missing axes?",
            "Usually the file leaves out a dimension of length 1, for example when an image "
            "has only one channel. If these images were exported differently, export them "
            "again instead.",
            "Yes, they have a single plane there",
            "Confirmed: the missing axes are read as length 1 (their coordinate is 0).",
            "Undo: they are different",
        )
        self.missing_card = ui.card(
            "Images with fewer axes",
            [self.missing_note, self.missing_as_singleton.widget],
            visible=False,
        )
        self.preview_file = widgets.Dropdown(layout=widgets.Layout(width="99%"))
        self.preview_sliders = {
            role: widgets.IntSlider(
                description=f"{role.lower()} ({name})",
                min=0,
                max=0,
                continuous_update=False,
                style={"description_width": "90px"},
                layout=widgets.Layout(width="420px"),
            )
            for role, name in (("Z", "depth"), ("C", "channel"), ("T", "time"))
        }
        self.preview_button = ui.button(
            " Show preview",
            style="info",
            icon="eye",
            tooltip="Read one plane with the roles above and show it",
        )
        self.preview_image = widgets.Image(format="png", layout=widgets.Layout(max_width="420px"))
        self.preview_caption = widgets.HTML()
        self.preview_checked = ui.ConfirmToggle(
            "Does the preview look like a normal image of your sample?",
            "Look at a few planes with the sliders. Stripes, repeating patterns or a smeared "
            "picture mean that X or Y is on the wrong dimension: change the roles above. "
            "The axis roles can only be confirmed once the preview looks right.",
            "Yes, the preview looks right",
            "Preview checked. Now click Confirm axis roles below.",
            "Undo: the preview does not look right",
        )
        self.preview_checked.disabled = True
        self.preview_card = ui.card(
            "Preview",
            [
                ui.help_text(
                    "Pick an image and move the sliders to look at different planes. "
                    "Brightness is stretched for display only; the images are not changed."
                ),
                self.preview_file,
                self.preview_button,
                *self.preview_sliders.values(),
                widgets.HBox([self.preview_image]),
                self.preview_caption,
                self.preview_checked.widget,
            ],
        )
        self.confirm_button = ui.button(
            " Confirm axis roles",
            style="success",
            icon="check",
            tooltip="Use these roles for every image of the experiment",
            main=True,
        )
        self.confirmed_status = widgets.HTML()
        self.axis_next = widgets.HTML()
        self.missing_as_singleton.observe(lambda _: self._mapping_changed())
        self.preview_button.on_click(lambda _: self.show_preview())
        self.preview_file.observe(lambda _: self._preview_file_changed(), names="value")
        for slider in self.preview_sliders.values():
            slider.observe(lambda _: self._render_preview_if_shown(), names="value")
        self.preview_checked.observe(lambda _: self._update_confirm_button())
        self.confirm_button.on_click(lambda _: self._toggle_confirmation())

    def _refresh_axis_step(self) -> None:
        pre = self.preflight
        self.axis_next.value = ""
        self._preview_shown = False
        if pre is None:
            self.axis_box.children = [
                widgets.HTML(
                    ui.status("info", "Waiting for step 1.", ["Check the experiment folder first."])
                )
            ]
            return
        if self.mapping is None:
            self.axis_box.children = [
                widgets.HTML(
                    ui.status(
                        "error",
                        "No axis roles can be suggested.",
                        [
                            "The images have no readable headers, or their dimensions are "
                            "arranged differently.",
                            "See the messages in step 1.",
                        ],
                    )
                )
            ]
            return
        ranges = axis_length_ranges(self.mapping, pre.metadata.files)
        self.role_dropdowns, self.role_notes, axis_cards = [], [], []
        for axis, role, (low, high) in zip(
            self.mapping.axes, self.mapping.roles, ranges, strict=True
        ):
            dropdown = widgets.Dropdown(
                options=ROLE_OPTIONS, value=role or "-", layout=widgets.Layout(width="99%")
            )
            note = widgets.HTML()
            dropdown.observe(lambda _: self._roles_edited(), names="value")
            self.role_dropdowns.append(dropdown)
            self.role_notes.append(note)
            length = f"{low}" if low == high else f"{low} to {high}"
            box = widgets.VBox(
                [
                    widgets.HTML(
                        f"<b>{html.escape(axis_name(axis))}</b> in the file"
                        f"<div class='mb-help'>Length {length} · suggested: "
                        f"{html.escape(role or 'no role')}</div>"
                    ),
                    dropdown,
                    note,
                ]
            )
            box.add_class("mb-card")
            axis_cards.append(box)
        grid = widgets.GridBox(
            axis_cards,
            layout=widgets.Layout(
                grid_template_columns="repeat(auto-fill, minmax(240px, 1fr))", grid_gap="10px"
            ),
        )
        self._refresh_missing_card(pre)
        self.preview_checked.set(False, notify=False)
        self.preview_checked.disabled = True
        for slider in self.preview_sliders.values():
            ui.set_visible(slider, False)
        self.preview_image.value = b""
        self.preview_caption.value = ""
        self.confirmed_status.value = ""
        self._set_locked(False)
        self.axis_box.children = [
            ui.card(
                "Axis roles",
                [
                    ui.help_text(
                        "One box per dimension of the images, in the order stored in the "
                        "files. Dimensions with more than one plane need Z, C or T; the image "
                        "itself needs exactly one Y and one X."
                    ),
                    grid,
                    self.plane_summary,
                ],
            ),
            self.missing_card,
            self.axis_status,
            self.preview_card,
            self.confirm_button,
            self.confirmed_status,
            self.axis_next,
        ]
        self._fill_preview_files()
        self._mapping_changed()

    def _refresh_missing_card(self, pre: Preflight) -> None:
        # OME-Zarr images may store the same roles in another order; only roles that
        # an image lacks need the user's confirmation.
        lacking: dict[str, str] = {}
        for f in pre.metadata.files:
            resolved = file_mapping_for(f, self.mapping) if f.readable else None
            if resolved is not None and resolved.singleton_roles:
                lacking[f.relative_path] = "".join(resolved.singleton_roles)
        ui.set_visible(self.missing_card, bool(lacking))
        if not lacking:
            return
        by_axes: dict[tuple[str, str], list[str]] = {}
        headers = {f.relative_path: f for f in pre.metadata.files}
        for rel, missing in lacking.items():
            by_axes.setdefault((headers[rel].axes, missing), []).append(rel)
        lines = []
        for (axes, missing), paths in sorted(by_axes.items()):
            names = ", ".join(axis_name(a) for a in missing)
            lines.append(
                f"{plural(len(paths), 'image')} with axes <b>{html.escape(axes)}</b> "
                f"lack {html.escape(names)}" + ui.path_list(paths, limit=5)
            )
        self.missing_note.value = ui.status(
            "warn",
            "Some images have fewer axes than the others.",
            [
                *lines,
                "This usually means they have a single channel or slice, which the file "
                "does not store. Confirm below only if that is the case.",
            ],
            raw=True,
        )

    def _roles_edited(self) -> None:
        # A preview checked with other roles says nothing about the new ones.
        if self.preview_checked.value:
            self.preview_checked.set(False, notify=False)
        self._mapping_changed()

    def _mapping_changed(self) -> None:
        if self.preflight is None or self.mapping is None or not self.role_dropdowns:
            return
        try:
            self.mapping = self._current_mapping()
        except ValueError as exc:
            self.axis_status.value = ui.status("error", str(exc))
            return
        ranges = axis_length_ranges(self.mapping, self.preflight.metadata.files)
        for note, role, (low, high) in zip(
            self.role_notes, self.mapping.roles, ranges, strict=True
        ):
            note.value = f"<div class='mb-help'>{role_effect(role, low, high)}</div>"
        self.plane_summary.value = plane_summary_html(self.mapping, ranges)
        review = review_axes(
            self.preflight.metadata,
            self.mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        if review.issues:
            self.axis_status.value = issues_html(review.issues)
            self.axis_next.value = ""
        else:
            self.axis_status.value = ui.status(
                "ok",
                "These roles are valid for every image.",
            )
            self.axis_next.value = ui.next_steps(
                "Check the preview in Step 2, then confirm the axis roles.",
                "Click Show preview and inspect a few planes before confirming.",
            )
        self._review_ok = review.ok
        if self._preview_shown:
            self._configure_sliders(reset=False)
            self._render_preview()
        self._update_confirm_button()

    def _update_confirm_button(self) -> None:
        if self.confirmed_mapping is not None:
            self.confirm_button.disabled = False
            return
        ready = getattr(self, "_review_ok", False) and self.preview_checked.value
        self.confirm_button.disabled = not ready
        self.confirm_button.tooltip = (
            "Use these roles for every image of the experiment"
            if ready
            else "Fix the axis messages and check the preview first"
        )

    def _fill_preview_files(self) -> None:
        files = [
            f
            for f in self.preflight.metadata.files
            if f.readable and file_mapping_for(f, self.mapping) is not None
        ]
        options = [
            (
                f"{f.relative_path}  ({'OME-Zarr, ' if f.is_ome_zarr else ''}{f.axes}, "
                f"{' × '.join(map(str, f.shape))})",
                f.relative_path,
            )
            for f in files
        ]
        self.preview_file.options = options
        self.preview_file.value = options[0][1] if options else None

    def _preview_file_changed(self) -> None:
        if self._preview_shown:
            self._configure_sliders(reset=True)
            self._render_preview()

    def _configure_sliders(self, reset: bool) -> None:
        header = self._preview_header()
        file_mapping = file_mapping_for(header, self.mapping) if header else None
        for role, slider in self.preview_sliders.items():
            dim = None if file_mapping is None else file_mapping.index_of(role)
            length = 1 if dim is None else header.shape[dim]
            middle = length // 2 if role == "Z" else 0
            with slider.hold_trait_notifications():
                slider.max = max(length - 1, 0)
                slider.value = middle if reset else min(slider.value, slider.max)
            ui.set_visible(slider, length > 1)

    def show_preview(self) -> None:
        """Show a plane of the chosen image with the current roles; sliders pick others."""
        if self.preflight is None or self.mapping is None:
            return
        if not self._preview_shown:
            self._configure_sliders(reset=True)
            self._preview_shown = True
        self._render_preview()
        self.preview_checked.disabled = False

    def _render_preview_if_shown(self) -> None:
        if self._preview_shown:
            self._render_preview()

    def _render_preview(self) -> None:
        header = self._preview_header()
        if header is None:
            self.preview_caption.value = ui.status("warn", "No image fits these axis roles.")
            return
        file_mapping = file_mapping_for(header, self.mapping)
        coords = {
            role.lower(): self.preview_sliders[role].value
            for role in ("Z", "C", "T")
            if file_mapping.index_of(role) is not None
        }
        try:
            with self.log.attached():
                plane = read_plane(
                    self.preflight.discovery.root / header.relative_path,
                    file_mapping,
                    memory_limit=self._memory_limit(),
                    preview=True,
                    **coords,
                )
        except (PlaneReadError, IndexError, ValueError) as exc:
            self.preview_image.value = b""
            self.preview_caption.value = ui.status(
                "warn", "This plane cannot be shown with these roles.", [str(exc)]
            )
            return
        self.preview_image.value = plane_png(plane)
        self.preview_caption.value = preview_caption(header.relative_path, plane)
        self._show_log()

    def _preview_header(self):
        value = self.preview_file.value
        return next((f for f in self.preflight.metadata.files if f.relative_path == value), None)

    def _toggle_confirmation(self) -> None:
        if self.confirmed_mapping is None:
            if self.confirm_button.disabled:
                return
            self.confirmed_mapping = self._current_mapping()
        else:
            self.confirmed_mapping = None
        locked = self.confirmed_mapping is not None
        self._set_locked(locked)
        if locked:
            roles = [
                f"{axis_name(a)} → {r}"
                for a, r in zip(
                    self.confirmed_mapping.axes, self.confirmed_mapping.roles, strict=True
                )
                if r is not None
            ]
            self.confirmed_status.value = ui.status("ok", "Axis roles confirmed.", roles)
            self._refresh_axis_next()
        else:
            self.confirmed_status.value = ""
            self._mapping_changed()
        self._update_confirm_button()
        self._refresh_run_step()

    def _refresh_axis_next(self) -> None:
        if not self.labels_confirmed.value:
            self.axis_next.value = ui.next_steps(
                "Go to Step 1 and confirm the folder labels.",
                "Review the labels and click Yes, the labels are correct before running Step 3.",
            )
        elif not self._design_confirmed():
            self.axis_next.value = ui.next_steps(
                "Go to Step 1 and confirm the biological repeats.",
                "Under Experiment design, answer each yellow question before running Step 3.",
            )
        else:
            self.axis_next.value = ui.next_steps(
                "Go to Step 3 and compute the histograms.",
                "Run its cell and review the settings and What will happen. "
                "Then click Compute histograms.",
            )

    def _set_locked(self, locked: bool) -> None:
        for dropdown in self.role_dropdowns:
            dropdown.disabled = locked
        self.missing_as_singleton.disabled = locked
        self.preview_checked.disabled = locked or not self._preview_shown
        self.confirm_button.description = " Change axis roles" if locked else " Confirm axis roles"
        self.confirm_button.icon = "pencil" if locked else "check"
        self.confirm_button.button_style = "warning" if locked else "success"

    def _current_mapping(self) -> AxisMapping:
        return AxisMapping(self.mapping.axes, tuple(d.value for d in self.role_dropdowns))

    # Step 3 ------------------------------------------------------------------------------

    def _build_run_step(self) -> None:
        self.readiness = widgets.HTML()
        self.readiness_card = ui.card("Before you run", [self.readiness])
        self.recommended = widgets.Checkbox(
            value=True, description="Use the recommended settings", indent=False
        )
        self.recommended_hint = ui.help_text("")
        self.bins = widgets.BoundedIntText(
            value=65_536, min=1, max=MAX_BIN_COUNT, description="Bins per plane", style=_label()
        )
        self.bins_hint = ui.help_text("")
        self.memory_gib = widgets.BoundedFloatText(
            value=DEFAULT_MEMORY_LIMIT / 2**30,
            min=0.01,
            max=4096,
            step=0.5,
            description="Memory limit (GiB)",
            style=_label(),
        )
        self.force_rebuild = widgets.Checkbox(
            description="Recompute every row, even for unchanged images", indent=False
        )
        self.advanced = widgets.VBox(
            [
                self.bins,
                self.bins_hint,
                self.memory_gib,
                ui.help_text(
                    "The most working memory one plane may use. A larger plane is read in "
                    "blocks instead: the results are the same, but reading takes longer. Raise "
                    "it only if the computer has enough memory."
                ),
                self.force_rebuild,
                ui.help_text(
                    "Normally a new run only reads images that are new or changed since the "
                    "last run. Tick this to start from scratch."
                ),
            ]
        )
        ui.set_visible(self.advanced, False)
        self.settings_card = ui.card(
            "Settings", [self.recommended, self.recommended_hint, self.advanced]
        )
        self._build_conversion_card()
        self.plan = widgets.HTML()
        self.plan_card = ui.card("What will happen", [self.plan])
        self.run_button = ui.button(
            " Compute histograms",
            style="success",
            icon="play",
            tooltip="Read every new or changed image and write the table",
            main=True,
        )
        self.progress = widgets.IntProgress(
            value=0, min=0, max=1, bar_style="info", layout=widgets.Layout(width="99%")
        )
        ui.set_visible(self.progress, False)
        self.run_status = widgets.HTML()
        self.run_next = widgets.HTML()
        self.run_tiles = widgets.HTML()
        self.run_issues = widgets.HTML()
        self.details = widgets.Textarea(
            layout=widgets.Layout(width="99%", height="14em"), disabled=True
        )
        self.details_box = widgets.Accordion(
            children=[self.details], titles=("Technical details (log)",), selected_index=None
        )
        self.recommended.observe(lambda _: self._recommended_changed(), names="value")
        for control in (self.bins, self.memory_gib, self.force_rebuild):
            control.observe(lambda _: self._refresh_run_step(), names="value")
        self.run_button.on_click(lambda _: self.run())

    def _build_conversion_card(self) -> None:
        # Converting is a choice about the data, not a tuning value, so it has its own
        # card and the recommended settings leave it alone.
        available = zarr_available()
        self.conversion = widgets.RadioButtons(
            options=CONVERSION_OPTIONS,
            value="read",
            disabled=not available,
            layout=widgets.Layout(width="99%"),
        )
        self.conversion_help = ui.help_text(_conversion_help(available))
        self.space_warning = widgets.HTML()
        self.chunk_size = widgets.BoundedIntText(
            value=CHUNK_SIDE,
            min=MIN_CHUNK_SIZE,
            max=MAX_CHUNK_SIZE,
            step=256,
            description="Side length (px)",
            style=_label(),
            layout=widgets.Layout(width="99%"),
        )
        self.compression = widgets.Dropdown(
            options=[(label, key) for key, label in COMPRESSIONS.items()],
            value="lz4",
            style=_label(),
            layout=widgets.Layout(width="99%"),
        )
        self.compression_level = widgets.BoundedIntText(
            value=5,
            min=1,
            max=9,
            description="Level (1–9)",
            style=_label(),
            layout=widgets.Layout(width="99%"),
        )
        self.compression_level_box = widgets.VBox(
            [
                self.compression_level,
                ui.help_text(
                    "1 favours speed; 9 favours smaller files. The default is 5. "
                    "Every level keeps the full-resolution pixel values unchanged."
                ),
            ]
        )
        self.pyramid = widgets.Dropdown(
            options=PYRAMID_OPTIONS,
            value="auto",
            style=_label(),
            layout=widgets.Layout(width="99%"),
        )
        self.pyramid_count = widgets.BoundedIntText(
            value=2,
            min=1,
            max=MAX_LEVELS - 1,
            description="Extra levels",
            style=_label(),
            layout=widgets.Layout(width="99%"),
        )
        self.pyramid_count_box = widgets.VBox(
            [
                self.pyramid_count,
                ui.help_text(
                    f"Add 1 to {MAX_LEVELS - 1} smaller copies, each half the width and "
                    "height of the previous level. Two adds half-size and quarter-size copies."
                ),
            ]
        )
        storage_card = ui.card(
            "Storage block size",
            [
                ui.help_text(
                    "Each plane is stored in square blocks called chunks. "
                    "This changes how the copy is stored and read."
                ),
                self.chunk_size,
                ui.help_text(
                    f"Default: {CHUNK_SIDE:,} × {CHUNK_SIDE:,} pixels per block, or smaller "
                    f"at an image edge. Allowed side lengths: {MIN_CHUNK_SIZE}–{MAX_CHUNK_SIZE:,} "
                    "pixels. Each block belongs to one depth slice, channel and time point."
                ),
                ui.help_text(
                    "Smaller blocks let viewers read smaller regions; larger blocks mean fewer "
                    "blocks to manage. Leave the default if you are unsure."
                ),
            ],
        )
        compression_card = ui.card(
            "Lossless compression",
            [
                ui.help_text("Reduce the space used by the copies without changing pixel values."),
                self.compression,
                ui.help_text(
                    "LZ4 (recommended) favours speed. Zstandard favours smaller files and "
                    "may take longer. No compression skips compression and uses more disk space."
                ),
                self.compression_level_box,
            ],
        )
        preview_card = ui.card(
            "Preview resolutions",
            [
                ui.help_text(
                    "Add smaller versions for viewers to zoom out quickly. These versions "
                    "form an image pyramid; histograms always use the full resolution."
                ),
                self.pyramid,
                ui.help_text(
                    "Automatic (recommended) halves each plane until it fits within "
                    "512 × 512 pixels. Full resolution only saves space by omitting smaller "
                    "versions. Choose the number of copies to set how many to add."
                ),
                self.pyramid_count_box,
            ],
        )
        self.conversion_grid = widgets.GridBox(
            [storage_card, compression_card, preview_card],
            layout=widgets.Layout(
                width="99%",
                grid_template_columns="repeat(auto-fit, minmax(min(99%, 320px), 1fr))",
                grid_gap="10px",
            ),
        )
        self.conversion_settings = widgets.Accordion(
            children=[self.conversion_grid],
            titles=("OME-Zarr conversion settings",),
            selected_index=None,
            layout=widgets.Layout(width="99%"),
        )
        self.conversion_card = ui.card(
            "(Optional) Image conversion",
            [self.conversion, self.conversion_help, self.space_warning, self.conversion_settings],
        )
        for control in (
            self.conversion,
            self.chunk_size,
            self.compression,
            self.compression_level,
            self.pyramid,
            self.pyramid_count,
        ):
            control.observe(lambda _: self._conversion_changed(), names="value")
        self._refresh_conversion()

    def _conversion_changed(self) -> None:
        self._refresh_run_step()

    def _refresh_conversion(self) -> None:
        converting = self._convert_tiffs()
        # Open on choosing conversion, but preserve a deliberate collapse while editing.
        if converting and not ui.is_visible(self.conversion_settings):
            self.conversion_settings.selected_index = 0
        ui.set_visible(self.conversion_settings, converting)
        ui.set_visible(self.compression_level_box, self.compression.value != "none")
        ui.set_visible(self.pyramid_count_box, self.pyramid.value == "fixed")
        self.space_warning.value = self._space_warning_html() if converting else ""

    def _space_warning_html(self) -> str:
        if self.preflight is None:
            return ui.status(
                "warn",
                "Converting stores your images a second time.",
                ["Check the experiment folder in step 1 to see how much disk space it needs."],
            )
        folder = ui.clean_path(self.folder.value)
        estimate = estimate_conversion(
            folder, self.preflight.metadata.files, self._conversion_settings()
        )
        if not estimate.image_count:
            return ui.status(
                "info",
                "There is nothing to convert.",
                ["Every image is already OME-Zarr; they are always read directly."],
            )
        output = Path(self._output_folder() or default_output_folder(folder))
        free = free_space(output)
        lines = [
            f"All {plural(estimate.image_count, 'TIFF image')} will be stored again in the "
            "converted format, so all the space they occupy is duplicated.",
            f"The TIFF images take {format_bytes(estimate.source_bytes)}. The copies need up "
            f"to about {format_bytes(estimate.copy_bytes)} (before compression, including the "
            "smaller levels); compression usually makes them smaller.",
            "Free space on the drive of the output folder: "
            + (format_bytes(free) if free is not None else "unknown")
            + ".",
            "The copies are kept in the output folder; choosing Read the images directly "
            "later removes them.",
        ]
        if free is not None and estimate.copy_bytes > free:
            lines.append(
                "This may not be enough space. Free some space, or choose an output folder "
                "on another drive in step 1."
            )
            return ui.status("error", "The converted images may not fit on the drive.", lines)
        return ui.status("warn", "Converting duplicates the space your images take.", lines)

    def _conversion_settings(self) -> ConversionSettings:
        return ConversionSettings(
            chunk_size=self.chunk_size.value,
            compression=self.compression.value,
            compression_level=self.compression_level.value,
            pyramid_levels={"auto": None, "none": 0}.get(
                self.pyramid.value, self.pyramid_count.value
            ),
        )

    def _reset_settings(self, pre: Preflight) -> None:
        dtype = pre.metadata.dtype
        self._default_bins = None
        if (
            dtype is not None
            and pre.metadata.files
            and all(f.dtype_supported for f in pre.metadata.files if f.readable)
        ):
            self._default_bins = default_bin_count(dtype)
            top = min(MAX_BIN_COUNT, 2 ** (dtype.itemsize * 8)) if is_integer(dtype) else None
            self.bins.min = 1 if is_integer(dtype) else MIN_FLOAT_BIN_COUNT
            self.bins.max = top or MAX_BIN_COUNT
            self.bins.value = self._default_bins
            self.bins_hint.value = f"<div class='mb-help'>{_bins_help(dtype)}</div>"
        self._recommended_changed()
        self._refresh_conversion()

    def _recommended_changed(self) -> None:
        recommended = self.recommended.value
        ui.set_visible(self.advanced, not recommended)
        if recommended:
            if getattr(self, "_default_bins", None):
                self.bins.value = self._default_bins
            self.memory_gib.value = DEFAULT_MEMORY_LIMIT / 2**30
            self.force_rebuild.value = False
        bins = getattr(self, "_default_bins", None)
        bins_text = f"{bins:,} bins per plane" if bins else "the default bin count"
        self.recommended_hint.value = (
            f"<div class='mb-help'>Recommended: {bins_text}, a memory limit of 1 GiB per plane, "
            "and only new or changed images are read. Untick to change them.</div>"
        )
        self._refresh_run_step()

    def _refresh_run_step(self) -> None:
        if self.result is None:
            self.run_next.value = ""
        pre = self.preflight
        folder_ok = pre is not None and pre.ok
        review = None
        if pre is not None and self.confirmed_mapping is not None:
            # Axis confirmation can resolve a preflight missing-axes error, but
            # folder, exclusion and other metadata errors must still block the run.
            review = review_axes(
                pre.metadata,
                self.confirmed_mapping,
                missing_as_singleton=self.missing_as_singleton.value,
            )
            folder_ok = not has_errors(
                (
                    *pre.discovery.issues,
                    *pre.exclusion_issues,
                    *review.combined_issues(pre.metadata),
                )
            )
        items = [
            (folder_ok, "Check the experiment folder and fix any errors (step 1)"),
            (pre is not None and self.labels_confirmed.value, "Confirm the labels (step 1)"),
            (self._design_confirmed(), "Confirm the biological repeats (step 1)"),
            (self.confirmed_mapping is not None, "Confirm the axis roles (step 2)"),
        ]
        missing = [text for done, text in items if not done]
        if missing:
            self.run_next.value = ""
            title = ui.status(
                "warn", "Almost ready.", [f"Still to do: {missing[0][0].lower()}{missing[0][1:]}."]
            )
        else:
            title = ui.status("ok", "Ready to run.")
        self.readiness.value = title + ui.checklist(items)
        self.run_button.disabled = bool(missing)
        self.plan.value = self._plan_html(review if folder_ok else None)
        self._refresh_conversion()

    def _plan_layouts(self, review: AxisReview) -> dict[str, PlaneLayout]:
        """Return how each image's planes are read, from the headers only.

        Opening every header takes a moment on large experiments, and this runs on
        every settings edit, so the layouts are kept until the checked folder, the
        axis roles, the memory limit or the bin count change.
        """
        bins = self.bins.value or None
        key = (
            self.confirmed_mapping,
            self.missing_as_singleton.value,
            self._memory_limit(),
            bins,
        )
        cached = self._layouts_cache
        # A new check of the folder gives a new Preflight, even for the same folder.
        if cached is not None and cached[0][0] is self.preflight and cached[0][1:] == key:
            return cached[1]
        root = self.preflight.discovery.root
        layouts = {}
        for rel, mapping in sorted(review.file_mappings.items()):
            try:
                layouts[rel] = plane_layout(
                    root / rel, mapping, memory_limit=self._memory_limit(), bin_count=bins
                )
            except PlaneReadError:
                continue  # the run reports these images as unsupported_layout
        self._layouts_cache = ((self.preflight, *key), layouts)
        return layouts

    def _memory_plan(self, review: AxisReview) -> tuple[str, str]:
        """Return the "read in blocks" bullet and the "cannot be read" card, if any."""
        layouts = self._plan_layouts(review)
        limit = format_bytes(self._memory_limit())
        blocked = {rel: lay for rel, lay in layouts.items() if lay.fits and lay.in_blocks}
        over = {rel: lay for rel, lay in layouts.items() if not lay.fits}
        line = card = ""
        if blocked:
            largest = format_bytes(max(lay.memory_estimate for lay in blocked.values()))
            line = (
                f"<b>{plural(len(blocked), 'image')}</b> {'has' if len(blocked) == 1 else 'have'} "
                f"planes too large to read whole within the memory limit of {limit} (they need "
                f"up to {largest}), so they will be read in blocks. The histograms are exactly the "
                "same; reading takes longer. To read them whole, raise the memory limit to "
                f"{largest} or more in <b>Settings</b> above (untick <b>Use the recommended "
                "settings</b>), "
                "if this computer has enough memory."
                + ui.path_list(list(blocked), limit=_PLAN_PATHS)
            )
        if over:
            need = format_bytes(max(lay.min_memory_estimate for lay in over.values()))
            card = ui.status(
                "error",
                f"{plural(len(over), 'image')} cannot be read within the memory limit; "
                "the run will stop.",
                [
                    f"The smallest part of a plane that can be read on its own needs up to "
                    f"{need}, more than the limit of {limit}. A plane stored compressed in "
                    "one piece has to be decoded whole."
                    + ui.path_list(list(over), limit=_PLAN_PATHS),
                    f"<b>What to do:</b> raise the memory limit to {need} or more in "
                    "<b>Settings</b> above, if this computer has enough memory, or exclude "
                    "these images in step 1. "
                    "Saving them again as tiled or uncompressed TIFF files, or as OME-Zarr, "
                    "also works.",
                ],
                raw=True,
            )
        return line, card

    def _plan_html(self, review: AxisReview | None = None) -> str:
        folder = ui.clean_path(self.folder.value)
        if self.preflight is None or not folder:
            return "<div class='mb-help'>Check the experiment folder in step 1 first.</div>"
        output = Path(self._output_folder() or default_output_folder(folder))
        n = len(self.preflight.metadata.files)
        lines = [
            f"<b>{plural(n, 'image')}</b> will be analysed"
            + (
                f" ({len(self.preflight.excluded):,} excluded)." if self.preflight.excluded else "."
            ),
            f"Output folder: <b>{html.escape(str(output))}</b>",
            f"Files written: <b>{OUTPUT_FILENAME}</b> (one row per plane) and "
            f"<b>{MANIFEST_FILENAME}</b> (what was read, with which settings).",
        ]
        files = self.preflight.metadata.files
        tiffs = sum(not f.is_ome_zarr for f in files)
        zarrs = len(files) - tiffs
        if zarrs:
            lines.append(
                f"<b>{plural(zarrs, 'OME-Zarr image')}</b> will be read directly, with the axes "
                f"{'its' if zarrs == 1 else 'their'} metadata declares."
            )
        if tiffs and self._convert_tiffs():
            copies = html.escape(str(output / OME_ZARR_FOLDER))
            settings = html.escape(self._conversion_settings().describe())
            lines.append(
                f"<b>{plural(tiffs, 'TIFF image')}</b> will be converted to OME-Zarr in "
                f"<b>{copies}</b> first ({settings}); their histograms are computed from the "
                "copies. Copies of unchanged images are kept."
            )
        # Which images are read in blocks depends on the axis roles, so it is only
        # worked out once they are confirmed (``review`` is None until then).
        blocks_line, over_card = self._memory_plan(review) if review is not None else ("", "")
        if blocks_line:
            lines.append(blocks_line)
        manifest = output / MANIFEST_FILENAME
        if self.force_rebuild.value:
            lines.append("Every image will be read (recompute every row is ticked).")
        elif manifest.exists():
            try:
                earlier = read_manifest(manifest)
                lines.append(
                    f"An earlier run was found ({plural(len(earlier.files), 'image')}, "
                    f"{html.escape(earlier.written_at or 'unknown date')}). Only new or "
                    "changed images will be read; if the bin count or axis roles changed, "
                    "every image is read again."
                )
            except ManifestFormatError:
                lines.append("An earlier run was found but cannot be read; it will be replaced.")
        else:
            lines.append("No earlier run was found: every image will be read.")
        lines.append("Nothing is written unless the whole run succeeds.")
        return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>" + over_card

    def run(self) -> RunResult | None:
        """Compute or update the histogram table (pass 2) and show what happened."""
        self._refresh_run_step()
        if self.run_button.disabled:
            return None
        self._busy(self.run_button, "Computing…")
        self.run_next.value = ""
        ui.set_visible(self.progress, True)
        self.progress.value, self.progress.max, self.progress.bar_style = 0, 1, "info"
        self.run_status.value = ui.working("Preparing: checking everything again…")
        self.run_tiles.value = self.run_issues.value = ""
        try:
            with self.log.attached():
                self.result = run_experiment(
                    ui.clean_path(self.folder.value),
                    self.confirmed_mapping,
                    output_folder=self._output_folder(),
                    bin_count=self.bins.value or None,
                    memory_limit=self._memory_limit(),
                    missing_as_singleton=self.missing_as_singleton.value,
                    exclude=self._excluded(),
                    force_rebuild=self.force_rebuild.value,
                    progress=self._progress,
                    convert_tiffs=self._convert_tiffs(),
                    conversion=self._conversion_settings(),
                    design=self.design,
                )
        except Exception as exc:  # unexpected failures still get a readable message
            self.log.lines.append(traceback.format_exc())
            self.progress.bar_style = "danger"
            self.run_status.value = ui.status(
                "error",
                "The run stopped because of an unexpected problem.",
                [
                    f"{type(exc).__name__}: {exc}",
                    "Open Technical details below for the full report.",
                ],
            )
            self._show_log()
            return None
        finally:
            self._idle(self.run_button, " Compute histograms", "play")
        self._show_run_result(self.result)
        self._show_log()
        if self.result.written:
            self.csv_file.value = str(self.result.csv_path)
            self.stats_status.value = ui.status(
                "info",
                "The table from step 3 is selected.",
                ["Click Calculate statistics to compare the groups (optional)."],
            )
        return self.result

    def _show_run_result(self, result: RunResult) -> None:
        self.run_next.value = ""
        self.progress.bar_style = "success" if result.written else "danger"
        self.run_issues.value = issues_html(result.issues) if result.issues else ""
        if not result.written:
            self.run_status.value = ui.status(
                "error",
                "Nothing was written.",
                ["Read the messages below; each one says what to do.", "Then run again."],
            )
            return
        lines = [f"Table: {result.csv_path}", f"Manifest: {result.manifest_path}"]
        if result.design_path is not None:
            lines.append(f"Experiment design: {result.design_path}")
        if result.converted or self._convert_tiffs():
            lines.append(
                f"Converted TIFF images: {result.output_folder / OME_ZARR_FOLDER} "
                f"({plural(len(result.converted), 'image')} converted in this run)"
            )
        if result.rebuild_reason:
            lines.append(f"Every image was read because {result.rebuild_reason}.")
        self.run_status.value = ui.status(
            "ok", f"Done: {plural(result.plane_count, 'plane')} in the table.", lines
        )
        self.run_next.value = ui.next_steps(
            "Go to Step 4 (optional) and calculate the statistics.",
            "Run its cell, choose how to group the images, then click Calculate statistics. "
            "You can also finish here: the histogram table is ready for later MorphoBricks bricks.",
        )
        kept = 0 if result.rebuild_reason else len(result.unchanged)
        tiles = [
            ("🧩", f"{result.plane_count:,}", "planes in the table"),
            ("📖", f"{len(result.read):,}", "images read"),
            ("♻️", f"{kept:,}", "images kept from the last run"),
            ("🆕", f"{len(result.new):,}", "new images"),
            ("✏️", f"{len(result.changed):,}", "changed images"),
            ("🗑️", f"{len(result.removed):,}", "images removed"),
            ("⛔", f"{len(result.excluded):,}", "excluded images"),
        ]
        if result.converted or self._convert_tiffs():
            tiles.append(("🗂️", f"{len(result.converted):,}", "TIFF images converted"))
        tiles.append(("⚠️", issue_counts(result.issues), "checks"))
        self.run_tiles.value = ui.tiles(tiles)

    def _convert_tiffs(self) -> bool:
        return self.conversion.value == "convert"

    def _progress(self, done: int, total: int, path: str) -> None:
        self.progress.max = max(total, 1)
        self.progress.value = done
        if path:
            self.run_status.value = ui.working(f"Reading image {done + 1} of {total}: {path}")
        else:
            self.run_status.value = ui.working("Writing the table…")

    # Step 4 ------------------------------------------------------------------------------

    def _build_results_step(self) -> None:
        self.csv_file = widgets.Text(
            placeholder="filled in after step 3, or choose a plane_histograms.csv",
            layout=widgets.Layout(flex="1 1 auto", width="auto"),
        )
        self.csv_browse = ui.button(
            "Browse…", icon="folder-open", tooltip="Choose a plane_histograms.csv file"
        )
        self.csv_card = ui.card(
            "Histogram table",
            [
                widgets.HBox([self.csv_file, self.csv_browse], layout=widgets.Layout(**_WIDE)),
                ui.help_text(
                    "The table to calculate the statistics from. After a successful run in "
                    "step 3 this is the table just written. To look at another experiment, "
                    "paste the path of its <b>plane_histograms.csv</b> or use "
                    "<b>Browse…</b>."
                ),
            ],
        )
        self.level = widgets.ToggleButtons(
            options=LEVEL_OPTIONS,
            value="condition",
            tooltips=[
                "One row per condition",
                "One row per biological repeat within each condition",
                "One row per technical repeat within each biological repeat",
                "One row per image file",
            ],
            style={"button_width": "150px"},
        )
        self.percentiles = widgets.Text(value="1, 99.8", layout=widgets.Layout(width="200px"))
        self.statistics_button = ui.button(
            " Calculate statistics",
            style="info",
            icon="calculator",
            tooltip="Read the histogram table and pool the planes of every group",
            main=True,
        )
        self.stats_status = widgets.HTML()
        self.stats_next = widgets.HTML()
        self.results_card = ui.card(
            "Group by",
            [
                self.level,
                ui.help_text("Choose how planes are grouped before pooling."),
                widgets.HTML("<b>Percentiles</b>"),
                self.percentiles,
                ui.help_text(
                    "Numbers from 0 to 100 separated by commas. 1 and 99.8 are the default "
                    "range for percentile normalisation in later bricks."
                ),
                self.statistics_button,
                self.stats_status,
            ],
        )
        self.stats_tiles = widgets.HTML()
        self.statistics = widgets.HTML()
        self.statistics_card = ui.card(
            "Statistics per group",
            [
                ui.help_text(
                    "<b>planes</b>: planes in the group · <b>pixels</b>: finite pixels pooled · "
                    "<b>min</b>/<b>max</b>: exact extremes · <b>mean</b>, <b>std</b> (standard "
                    "deviation), <b>median</b> and <b>p…</b> (percentiles, e.g. p99.8 = 99.8th "
                    "percentile) are computed from all pooled pixels."
                ),
                self.statistics,
            ],
            visible=False,
        )
        self.distribution = widgets.Image(format="png", layout=widgets.Layout(max_width="760px"))
        self.distribution_card = ui.card(
            "Intensity distributions",
            [
                ui.help_text(
                    "For each group, the share of pixels at or below each intensity. Curves "
                    "shifted to the right are brighter; a steeper curve means less spread."
                ),
                self.distribution,
            ],
            visible=False,
        )
        self.statistics_button.on_click(lambda _: self.show_statistics())
        self.csv_browse.on_click(lambda _: self._browse_csv())
        self.csv_file.observe(lambda _: self._csv_changed(), names="value")
        self.level.observe(lambda _: self._statistics_option_changed(), names="value")

    def _browse_csv(self) -> None:
        current = ui.clean_path(self.csv_file.value)
        start = str(Path(current).parent) if current else ui.clean_path(self.folder.value)
        chosen = ui.ask_open_file(start, (("Histogram tables", "*.csv"), ("All files", "*.*")))
        if chosen is None:
            self.stats_status.value = ui.status(
                "warn",
                "The file dialog could not be opened here.",
                ["Paste the path of the plane_histograms.csv file into the box instead."],
            )
        elif chosen:
            self.csv_file.value = chosen

    def _csv_changed(self) -> None:
        # Results of another table must not stay on screen next to the new path.
        self.stats_status.value = self.stats_tiles.value = self.statistics.value = ""
        self.stats_next.value = ""
        ui.set_visible(self.statistics_card, False)
        ui.set_visible(self.distribution_card, False)

    def _statistics_option_changed(self) -> None:
        if self.statistics.value:
            self.show_statistics()

    def show_statistics(self) -> None:
        """Show pooled statistics and distributions per group from the histogram table."""
        if self.statistics_button.disabled:
            return
        self.stats_next.value = ""
        path = self._csv_path()
        if path is None or not path.is_file():
            if path is None:
                self.stats_status.value = ui.status(
                    "info",
                    "Choose a histogram table first.",
                    ["Run step 3, or click Browse… above to pick a plane_histograms.csv."],
                )
            else:
                self.stats_status.value = ui.status(
                    "warn", "This histogram table does not exist.", [str(path)]
                )
            self.statistics.value = ""
            ui.set_visible(self.statistics_card, False)
            ui.set_visible(self.distribution_card, False)
            return
        try:
            percentiles = [float(q) for q in self.percentiles.value.replace(";", ",").split(",")]
            if not percentiles or not all(0 <= q <= 100 for q in percentiles):
                raise ValueError
        except ValueError:
            self.stats_status.value = ui.status(
                "warn",
                "The percentiles cannot be read.",
                ["Use numbers from 0 to 100 separated by commas, for example 1, 99.8."],
            )
            return
        self._busy(self.statistics_button, "Calculating…")
        self.stats_status.value = ui.working("Reading the histogram table…")
        self.stats_tiles.value = self.statistics.value = ""
        ui.set_visible(self.statistics_card, False)
        ui.set_visible(self.distribution_card, False)
        try:
            self._calculate_statistics(path, percentiles)
        except Exception as exc:
            self.log.lines.append(traceback.format_exc())
            self.stats_status.value = ui.status(
                "error",
                "The statistics could not be calculated.",
                [f"{type(exc).__name__}: {exc}", "Check the histogram table and try again."],
            )
            ui.set_visible(self.statistics_card, False)
            ui.set_visible(self.distribution_card, False)
        finally:
            self._idle(self.statistics_button, " Calculate statistics", "calculator")
            self._show_log()

    def _calculate_statistics(self, path: Path, percentiles: Sequence[float]) -> None:
        """Read, summarise and plot the table while reporting the current stage."""
        try:
            records = read_plane_histograms(path)
        except (CsvFormatError, OSError) as exc:
            self.stats_status.value = ui.status("error", "The table cannot be read.", [str(exc)])
            return
        self.stats_status.value = ui.working("Calculating statistics for each group…")
        level = self.level.value
        summaries = group_summaries(records, level, percentiles)
        groups = group_records(records, level)
        images = {r.source_relative_path for r in records}
        first = records[0].histogram if records else None
        self.stats_tiles.value = ui.tiles(
            [
                ("🧪", f"{len(summaries):,}", f"{LEVEL_NAMES[level]} groups"),
                ("🖼️", f"{len(images):,}", "images"),
                ("🧩", f"{len(records):,}", "planes"),
                ("🔢", first.dtype.name if first else "-", "pixel type"),
                ("📊", f"{first.bin_count:,}" if first else "-", "bins per plane"),
            ]
        )
        rows = group_table(summaries, level)
        depth = len(summaries[0].labels) if summaries else 1
        self.statistics.value = ui.table(rows, numeric_from=depth)
        ui.set_visible(self.statistics_card, True)
        self.stats_status.value = ui.working("Drawing the intensity distributions…")
        png = distribution_png(groups)
        self.distribution.value = png or b""
        ui.set_visible(self.distribution_card, bool(png))
        self.stats_status.value = ui.status(
            "ok",
            f"Statistics of {plural(len(summaries), LEVEL_NAMES[level])} calculated.",
            [f"From the table {path}"],
        )
        self.stats_next.value = ui.next_steps(
            "Review the group statistics and intensity distributions above.",
            "Choose another grouping to compare repeats or images. The histogram table "
            "is ready for later MorphoBricks bricks; this optional step writes no files.",
        )

    # Shared ------------------------------------------------------------------------------

    def _busy(self, button: widgets.Button, text: str) -> None:
        button.disabled = True
        button.description = f" {text}"
        button.icon = "spinner"

    def _idle(self, button: widgets.Button, text: str, icon: str) -> None:
        button.description = text
        button.icon = icon
        button.disabled = False
        if button is self.run_button:
            self._refresh_run_step()

    def _excluded(self) -> list[str]:
        return [line.strip() for line in self.exclude.value.splitlines() if line.strip()]

    def _output_folder(self) -> str | None:
        return ui.clean_path(self.output.value) or None

    def _csv_path(self) -> Path | None:
        path = ui.clean_path(self.csv_file.value)
        return Path(path) if path else None

    def _memory_limit(self) -> int:
        return int(self.memory_gib.value * 2**30)

    def _show_log(self) -> None:
        self.details.value = "\n".join(self.log.lines[-500:])


# Rendering helpers ---------------------------------------------------------------------------


def issues_html(issues: Iterable[ValidationIssue]) -> str:
    """Render issues as status cards: errors first, each with its paths and what to do."""
    issues = sorted(issues, key=lambda issue: not issue.is_error)
    if not issues:
        return ui.status("ok", "No problems found.")
    parts = []
    for issue in issues:
        lines = [html.escape(issue.message)]
        if issue.paths:
            lines.append(ui.path_list(issue.paths))
        if issue.action:
            lines.append(f"<b>What to do:</b> {html.escape(issue.action)}")
        kind, label = ("error", "Error") if issue.is_error else ("warn", "Warning")
        parts.append(ui.status(kind, f"{label} · {issue.code.replace('_', ' ')}", lines, raw=True))
    if has_errors(issues):
        parts.insert(
            0,
            ui.status("error", "Processing is blocked until the errors below are resolved."),
        )
    return "".join(parts)


def issue_count_chips(issues: Sequence[ValidationIssue]) -> str:
    """Red error and orange warning counts, shown outside a folded list of issues."""
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    return "".join(
        f"<span class='mb-issue-count mb-{kind}'>{plural(len(items), name)}</span>"
        for kind, items, name in (("error", errors, "error"), ("warn", warnings, "warning"))
        if items
    )


def balance_html(balance: BalanceTable) -> str:
    """Render images per condition and biological repeat; unbalanced or missing cells in amber."""
    if not balance.cells:
        return ""
    header = "".join(
        f"<th>{html.escape(h)}</th>" for h in ("Biological repeat", *balance.conditions)
    )
    rows = []
    for group in balance.repeat_groups:
        cells = [f"<td><b>{html.escape(group)}</b></td>"]
        for condition in balance.conditions:
            cell = balance.cell(group, condition)
            if cell is None:
                cells.append("<td class='mb-cell-warn'>not found</td>")
                continue
            technical = (
                plural(cell.technical_repeat_count, "technical repeat")
                if cell.technical_repeat_count
                else "no technical-repeat folders"
            )
            css = " class='mb-cell-warn'" if cell.unbalanced else ""
            cells.append(
                f"<td{css}><b>{plural(cell.image_count, 'image')}</b>"
                f"<div class='mb-help' style='margin:0'>{technical}</div></td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    median = (
        f"<div class='mb-help'>Median: {balance.median:g} images per condition and "
        "biological repeat.</div>"
        if balance.median is not None
        else ""
    )
    return (
        "<div class='mb-table-wrap'><table class='mb-table mb-labels'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>" + median
    )


def labels_html(pre: Preflight) -> str:
    """Render the leaf folders and their labels as a table, ``single`` greyed out."""
    rows = []
    for leaf in pre.discovery.leaves:
        cells = [
            "<span class='mb-pill mb-pill-single'>single</span>"
            if label == "single"
            else f"<b>{html.escape(label)}</b>"
            for label in leaf.labels
        ]
        folder = "experiment folder" if leaf.relative_path == "." else leaf.relative_path
        rows.append(
            "<tr>"
            + "".join(f"<td>{cell}</td>" for cell in cells)
            + f"<td class='mb-num'>{len(leaf.images):,}</td>"
            + f"<td><b>{html.escape(folder)}</b></td></tr>"
        )
    header = "".join(
        f"<th>{h}</th>"
        for h in ("Condition", "Biological repeat", "Technical repeat", "Images", "Folder")
    )
    return (
        "<div class='mb-table-wrap'><table class='mb-table mb-labels'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def role_effect(role: str | None, low: int, high: int) -> str:
    """One plain sentence on what a role means for an axis of this length."""
    length = f"{low}" if low == high else f"{low} to {high}"
    last = f"{high - 1}"
    if role in ("X", "Y"):
        side = "width" if role == "X" else "height"
        return f"Each plane is {length} pixels in {side}."
    if role in ("Z", "C", "T"):
        noun, letter = {
            "Z": ("depth slices", "z"),
            "C": ("channels", "c"),
            "T": ("time points", "t"),
        }[role]
        if high <= 1:
            return f"A single {noun[:-1]}: {letter} = 0."
        return f"{length} {noun}, numbered {letter} = 0 to {last}; each becomes its own plane."
    if high > 1:
        return (
            f"<span style='color:#991B1B'>This axis has {length} planes and needs a role: "
            "Z, C or T.</span>"
        )
    return "Length 1 in every image, so no role is needed."


def plane_summary_html(mapping: AxisMapping, ranges: Sequence[tuple[int, int]]) -> str:
    """Say how many planes of which size each image is split into."""
    lengths = dict(zip(mapping.roles, ranges, strict=True))
    if "X" not in lengths or "Y" not in lengths:
        return ""
    low = math.prod(lengths[r][0] for r in ("Z", "C", "T") if r in lengths)
    high = math.prod(lengths[r][1] for r in ("Z", "C", "T") if r in lengths)
    count = f"{low:,}" if low == high else f"{low:,} to {high:,}"
    (y_low, y_high), (x_low, x_high) = lengths["Y"], lengths["X"]
    size = (
        f"{y_low} × {x_low}"
        if (y_low, x_low) == (y_high, x_high)
        else f"{y_low}–{y_high} × {x_low}–{x_high}"
    )
    return ui.status(
        "info",
        f"Each image will be split into {count} plane(s) of {size} pixels (height × width).",
    )


def preview_caption(relative_path: str, plane: Plane) -> str:
    """Caption under the preview: which plane, its size and its value range."""
    coords = ", ".join(f"{k} = {v}" for k, v in plane.coordinates.items() if v is not None)
    pixels = plane.pixels
    finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
    values = f"values {finite.min():g} to {finite.max():g}" if finite.size else "no finite values"
    height, width = plane.shape
    reduced = ""
    if plane.step > 1:
        # Shown values come from the kept pixels only, so the range may be narrower.
        values = values.replace("values", "shown values", 1)
        reduced = (
            f"<br>This plane is too large for the memory limit, so it is read in blocks and "
            f"shown reduced: 1 pixel in {plane.step} along each side "
            f"({pixels.shape[0]} × {pixels.shape[1]} px shown)."
        )
    return (
        f"<div class='mb-help'><b>{html.escape(relative_path)}</b> · "
        f"{html.escape(coords or 'single plane')} · {height} × {width} px · "
        f"{pixels.dtype.name}, {values}{reduced}</div>"
    )


def display_range(pixels: np.ndarray, low: float = 0.5, high: float = 99.5) -> tuple[float, float]:
    """Return the brightness range used to show a plane (finite values only)."""
    finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
    if finite.size == 0:
        return 0.0, 1.0
    vmin, vmax = (float(v) for v in np.percentile(finite, [low, high]))
    if vmin == vmax:
        vmax = vmin + 1
    return vmin, vmax


def plane_png(plane: Plane, size: float = 4.2) -> bytes:
    """Render a plane as a PNG image for the preview."""
    pixels = plane.pixels
    vmin, vmax = display_range(pixels)
    height = size * pixels.shape[0] / max(pixels.shape[1], 1)
    figure = Figure(figsize=(size, max(min(height, 2 * size), 0.5)))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.imshow(pixels, cmap="gray", vmin=vmin, vmax=vmax, interpolation="nearest", aspect="auto")
    axes.set_axis_off()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100)
    return buffer.getvalue()


def distribution_png(groups: dict[tuple[str, ...], list]) -> bytes | None:
    """Plot the cumulative intensity distribution of each group (up to 12 groups)."""
    curves = []
    for key, members in list(groups.items())[:_MAX_PLOTTED_GROUPS]:
        try:
            pooled = PooledHistogram(members)
        except NoValidPixelsError:
            continue
        cumulative = np.cumsum(pooled.counts) / pooled.pixel_count
        label = " / ".join(key[-2:]) if len(key) > 1 else key[0]
        curves.append((label, pooled, cumulative))
    if not curves:
        return None
    low = min(p.percentile(0.1) for _, p, _ in curves)
    high = max(p.percentile(99.9) for _, p, _ in curves)
    figure = Figure(figsize=(7.2, 3.4))
    axes = figure.add_subplot()
    for label, pooled, cumulative in curves:
        axes.step(pooled.values, cumulative, where="post", label=label, linewidth=1.4)
    if high > low:
        axes.set_xlim(low, high)
    axes.set_ylim(0, 1)
    axes.set_xlabel("Intensity")
    axes.set_ylabel("Share of pixels at or below")
    axes.grid(alpha=0.3)
    axes.legend(fontsize=8, loc="lower right", frameon=False)
    if len(groups) > _MAX_PLOTTED_GROUPS:
        axes.set_title(f"First {_MAX_PLOTTED_GROUPS} of {len(groups)} groups", fontsize=9)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100)
    return buffer.getvalue()


class _LogCapture(logging.Handler):
    """Collects the package's log messages for the "Technical details" panel."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def attached(self):
        return _Attached(self)


class _Attached:
    def __init__(self, handler: logging.Handler):
        self.handler = handler
        self.logger = logging.getLogger(_PACKAGE_LOGGER)

    def __enter__(self):
        self.level = self.logger.level
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)
        return self.handler

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.level)
        return False


def _group_question(design: ExperimentDesign, group: str) -> str:
    """Return the question asked for a repeat group, or for the whole experiment."""
    if design.single_repeat:
        return (
            "All images of every condition come from one biological repeat: were they "
            "prepared and acquired under the same circumstances?"
        )
    return (
        f"Repeat group {group}: were these images prepared and acquired under the same "
        "circumstances, so they are comparable with each other?"
    )


def _group_explanation(design: ExperimentDesign, group: str) -> str:
    """Describe the folders of a repeat group and anything unusual about it (HTML)."""
    items = [
        f"<b>{html.escape(row.condition)}</b>: {html.escape(row.biological_repeat)} "
        f"({plural(row.image_count, 'image')})"
        for row in design.members(group)
    ]
    text = "Folders: " + "; ".join(items) + "."
    missing = design.missing_conditions(group)
    if missing and not design.single_repeat:
        text += (
            "<br><span style='color:#92400E'>Not found in "
            + ", ".join(html.escape(c) for c in missing)
            + ": it is still used, with the conditions that have it.</span>"
        )
    if group in design.conflicts:
        text += (
            "<br><span style='color:#991B1B'>This group holds more than one folder of "
            + ", ".join(html.escape(c) for c in design.conflicts[group])
            + ". Move one of them into another group above before confirming.</span>"
        )
    return text + " If they are not comparable, process them as separate experiments."


def _folder_tiles(pre: Preflight, issues: Sequence[ValidationIssue]) -> list[tuple[str, str, str]]:
    skipped = sum(len(i.paths) for i in pre.discovery.issues if i.code in _SKIPPED_CODES)
    conditions = {leaf.condition for leaf in pre.discovery.leaves}
    dtype = pre.metadata.dtype
    errors = sum(i.is_error for i in issues)
    warnings = len(issues) - errors
    return [
        ("🖼️", f"{len(pre.metadata.files):,}", "images to analyse"),
        ("📂", f"{len(pre.discovery.leaves):,}", "image folders"),
        ("🧪", f"{len(conditions):,}", "conditions"),
        ("🔢", dtype.name if dtype is not None else "mixed", "pixel type"),
        ("🗂️", f"{sum(f.is_ome_zarr for f in pre.metadata.files):,}", "OME-Zarr images"),
        ("⏭️", f"{skipped:,}", "files or folders skipped"),
        (
            "⛔" if errors else "⚠️" if warnings else "✅",
            f"{errors} / {warnings}",
            "errors / warnings",
        ),
    ]


def _conversion_help(available: bool) -> str:
    """Help lines under the choice between reading and converting."""
    items = [
        "<b>Read the images directly</b> (recommended): TIFF and OME-Zarr images are read "
        "as they are, and nothing extra is saved.",
        "<b>Convert</b>: every TIFF image is first written in OME-Zarr format to the "
        f"<b>{OME_ZARR_FOLDER}</b> folder of the output, and its histograms are computed from "
        "that copy, like for any OME-Zarr image. The results are the same.",
        "OME-Zarr images are always read directly; they are never converted.",
        "The copies keep the exact pixel values and can be opened in viewers such as napari. "
        "Your images are never changed.",
        "Switching between the two later recomputes every TIFF image.",
    ]
    if not available:
        items.append(
            "<b>Converting is not available:</b> it needs the zarr package. Install it with "
            '<b>pip install "morphobricks-preprocessing[zarr]"</b> and restart the notebook.'
        )
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def _bins_help(dtype) -> str:
    if is_integer(dtype):
        top = min(MAX_BIN_COUNT, 2 ** (dtype.itemsize * 8))
        exact = (
            " The default gives an exact histogram (one bin per value)."
            if dtype.itemsize <= 2
            else ""
        )
        return f"For {dtype.name} images: 1 to {top:,} bins.{exact}"
    return (
        f"For {dtype.name} images: {MIN_FLOAT_BIN_COUNT} to {MAX_BIN_COUNT:,} bins. More bins "
        "give more precise percentiles and a larger table."
    )


def _label() -> dict:
    return {"description_width": "140px"}
