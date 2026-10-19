"""Widgets for the biologist-facing notebook.

The notebook ``notebooks/MorphoBricks_Preprocessing.ipynb`` shows the four steps of
:class:`PreprocessingNotebook`, one per cell:

1. **Folder** - choose the experiment folder, check it, and confirm the labels
   taken from the folder names.
2. **Axes** - review the role of every image axis, look at planes with the
   preview, and confirm.
3. **Run** - check that everything is ready, see what will happen, and compute
   or update the histogram table.
4. **Results** - pooled statistics and intensity distributions per group.

Each step is a page of cards (see :mod:`.notebook_style`): a blue box says what
the step is for, every control has a help line, the main button is at the
bottom, and one status card says what happened and what to do next. Later steps
stay locked until the earlier ones are confirmed, and changing the folder resets
them. The widgets only collect choices and show results; every check and
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

from . import notebook_style as ui
from .axes import AxisMapping, axis_length_ranges, axis_name, review_axes
from .csv_io import OUTPUT_FILENAME, CsvFormatError, read_plane_histograms
from .discovery import check_output_folder, default_output_folder
from .dtypes import MAX_BIN_COUNT, MIN_FLOAT_BIN_COUNT, default_bin_count, is_integer
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .planes import DEFAULT_MEMORY_LIMIT, Plane, PlaneReadError, read_plane
from .pooling import NoValidPixelsError, PooledHistogram
from .records import group_records
from .report import LEVEL_NAMES, group_summaries, group_table, issue_counts
from .run import Preflight, RunResult, preflight, run_experiment
from .validation import ValidationIssue, has_errors, plural
from .zarr_export import OME_ZARR_FOLDER, zarr_available

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
_MAX_PLOTTED_GROUPS = 12
_PACKAGE_LOGGER = "morphobricks_preprocessing"
_WIDE = {"width": "99%"}


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
    result
        Result of the last run, or None.
    """

    def __init__(self, experiment_folder: str = "", output_folder: str = ""):
        self.preflight: Preflight | None = None
        self.mapping: AxisMapping | None = None
        self.confirmed_mapping: AxisMapping | None = None
        self.result: RunResult | None = None
        self.log = _LogCapture()
        self._checked_folder = ""
        self._preview_shown = False
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
            "Per-plane intensity histograms for an experiment folder of TIFF images.",
            [
                widgets.HTML(
                    ui.status(
                        "ok",
                        f"The notebook is ready (morphobricks-preprocessing {__version__}).",
                        ["Continue with step 1 below: run its cell to choose your folder."],
                    )
                )
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
                self.issues_card,
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
                self.plan_card,
                self.run_button,
                self.progress,
                self.run_status,
                self.run_tiles,
                self.run_issues,
                self.details_box,
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
        self.folder_tiles = widgets.HTML()
        self.labels_table = widgets.HTML()
        self.labels_confirmed = ui.ConfirmToggle(
            "Do the labels in the table match your experiment?",
            "Check that every image folder has the right condition, biological repeat and "
            "technical repeat. If a label is wrong, rename the folder and click <b>Check "
            "folder</b> again. Step 3 can only run once the labels are confirmed.",
            "Yes, the labels are correct",
            "Labels confirmed. Continue with step 2.",
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
        self.folder_issues = widgets.HTML()
        self.exclude_button = ui.button(
            " Exclude these images and check again",
            style="warning",
            icon="ban",
            tooltip="Add the images that cannot be processed to the exclusion list",
        )
        ui.set_visible(self.exclude_button, False)
        self.issues_card = ui.card(
            "Messages", [self.folder_issues, self.exclude_button], visible=False
        )

        self.folder_browse.on_click(lambda _: self._browse(self.folder))
        self.output_browse.on_click(lambda _: self._browse(self.output))
        self.check_button.on_click(lambda _: self.check_folder())
        self.exclude_button.on_click(lambda _: self._exclude_blocking_images())
        self.folder.observe(lambda _: self._folder_edited(), names="value")
        self.output.observe(lambda _: self._update_output_hint(), names="value")
        self.labels_confirmed.observe(lambda _: self._refresh_run_step())
        self._update_output_hint()

    def check_folder(self) -> Preflight | None:
        """Run the folder and header check (pass 1) and show its results."""
        folder = ui.clean_path(self.folder.value)
        if not folder:
            self.folder_status.value = ui.status(
                "warn", "Enter the experiment folder first.", ["Paste its path or use Browse…."]
            )
            return None
        self._busy(self.check_button, "Checking…")
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
        errors = [i for i in pre.issues if i.is_error]
        warnings = [i for i in pre.issues if not i.is_error]
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
                ["Check the labels table below and click Yes, the labels are correct."],
            )
        else:
            self.folder_status.value = ui.status(
                "ok",
                "The folder looks good.",
                ["Check the labels table below and click Yes, the labels are correct."],
            )
        self.folder_tiles.value = ui.tiles(_folder_tiles(pre))
        self.labels_table.value = labels_html(pre)
        self.labels_confirmed.set(False, notify=False)
        self.labels_confirmed.disabled = not pre.discovery.leaves
        ui.set_visible(self.labels_card, bool(pre.discovery.leaves))
        self.folder_issues.value = issues_html(pre.issues)
        ui.set_visible(self.issues_card, bool(pre.issues))
        blocking = any(i.code in EXCLUDABLE_CODES for i in pre.issues)
        ui.set_visible(self.exclude_button, blocking)

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
        self.labels_confirmed.set(False, notify=False)
        ui.set_visible(self.labels_card, False)
        ui.set_visible(self.issues_card, False)
        self._refresh_axis_step()
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
        self.missing_as_singleton.observe(lambda _: self._mapping_changed())
        self.preview_button.on_click(lambda _: self.show_preview())
        self.preview_file.observe(lambda _: self._preview_file_changed(), names="value")
        for slider in self.preview_sliders.values():
            slider.observe(lambda _: self._render_preview_if_shown(), names="value")
        self.preview_checked.observe(lambda _: self._update_confirm_button())
        self.confirm_button.on_click(lambda _: self._toggle_confirmation())

    def _refresh_axis_step(self) -> None:
        pre = self.preflight
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
        ]
        self._fill_preview_files()
        self._mapping_changed()

    def _refresh_missing_card(self, pre: Preflight) -> None:
        lacking = [f for f in pre.metadata.files if f.readable and f.axes != self.mapping.axes]
        ui.set_visible(self.missing_card, bool(lacking))
        if not lacking:
            return
        by_axes: dict[str, list[str]] = {}
        for f in lacking:
            by_axes.setdefault(f.axes, []).append(f.relative_path)
        lines = []
        for axes, paths in sorted(by_axes.items()):
            missing = "".join(a for a in self.mapping.axes if a not in axes) or "?"
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
        else:
            self.axis_status.value = ui.status(
                "ok",
                "These roles are valid for every image.",
                ["Click Show preview, check the picture, then confirm below."],
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
            if f.readable and self.mapping.for_axes(f.axes) is not None
        ]
        options = [
            (f"{f.relative_path}  ({f.axes}, {' × '.join(map(str, f.shape))})", f.relative_path)
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
        file_mapping = self.mapping.for_axes(header.axes) if header else None
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
        file_mapping = self.mapping.for_axes(header.axes)
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
            self.confirmed_status.value = ui.status(
                "ok", "Axis roles confirmed.", [*roles, "Continue to step 3."]
            )
        else:
            self.confirmed_status.value = ""
        self._update_confirm_button()
        self._refresh_run_step()

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
                    "The most working memory one plane may use. A plane that needs more stops "
                    "the run with a message. Raise it only if the computer has enough memory."
                ),
                self.force_rebuild,
                ui.help_text(
                    "Normally a new run only reads images that are new or changed since the "
                    "last run. Tick this to start from scratch."
                ),
            ]
        )
        ui.set_visible(self.advanced, False)
        # A separate output, not a tuning value, so the recommended settings leave it alone.
        self.ome_zarr = widgets.Checkbox(
            description="Also save OME-Zarr copies of the images",
            indent=False,
            disabled=not zarr_available(),
            layout=widgets.Layout(width="99%"),
        )
        self.ome_zarr_hint = ui.help_text(_ome_zarr_help(zarr_available()))
        self.settings_card = ui.card(
            "Settings",
            [
                self.recommended,
                self.recommended_hint,
                self.advanced,
                self.ome_zarr,
                self.ome_zarr_hint,
            ],
        )
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
        self.run_tiles = widgets.HTML()
        self.run_issues = widgets.HTML()
        self.details = widgets.Textarea(
            layout=widgets.Layout(width="99%", height="14em"), disabled=True
        )
        self.details_box = widgets.Accordion(
            children=[self.details], titles=("Technical details (log)",), selected_index=None
        )
        self.recommended.observe(lambda _: self._recommended_changed(), names="value")
        for control in (self.bins, self.memory_gib, self.force_rebuild, self.ome_zarr):
            control.observe(lambda _: self._refresh_run_step(), names="value")
        self.run_button.on_click(lambda _: self.run())

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
        pre = self.preflight
        folder_ok = pre is not None and pre.ok
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
            (self.confirmed_mapping is not None, "Confirm the axis roles (step 2)"),
        ]
        missing = [text for done, text in items if not done]
        if missing:
            title = ui.status(
                "warn", "Almost ready.", [f"Still to do: {missing[0][0].lower()}{missing[0][1:]}."]
            )
        else:
            title = ui.status("ok", "Ready to run.", ["Press Compute histograms below."])
        self.readiness.value = title + ui.checklist(items)
        self.run_button.disabled = bool(missing)
        self.plan.value = self._plan_html()

    def _plan_html(self) -> str:
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
        if self.ome_zarr.value:
            copies = html.escape(str(output / OME_ZARR_FOLDER))
            lines.append(
                f"OME-Zarr copies of the images go to <b>{copies}</b>. Copies of unchanged "
                "images are kept; new and changed images, and images without a copy yet, are "
                "copied."
            )
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
        return "<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"

    def run(self) -> RunResult | None:
        """Compute or update the histogram table (pass 2) and show what happened."""
        self._refresh_run_step()
        if self.run_button.disabled:
            return None
        self._busy(self.run_button, "Computing…")
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
                    ome_zarr=self.ome_zarr.value,
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
        if result.converted or self.ome_zarr.value:
            lines.append(
                f"OME-Zarr copies: {result.output_folder / OME_ZARR_FOLDER} "
                f"({plural(len(result.converted), 'image')} copied in this run)"
            )
        if result.rebuild_reason:
            lines.append(f"Every image was read because {result.rebuild_reason}.")
        lines.append("Step 4 (optional) calculates statistics per group from this table.")
        self.run_status.value = ui.status(
            "ok", f"Done: {plural(result.plane_count, 'plane')} in the table.", lines
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
        if result.converted or self.ome_zarr.value:
            tiles.append(("🗂️", f"{len(result.converted):,}", "OME-Zarr copies written"))
        tiles.append(("⚠️", issue_counts(result.issues), "checks"))
        self.run_tiles.value = ui.tiles(tiles)

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
        ui.set_visible(self.statistics_card, False)
        ui.set_visible(self.distribution_card, False)

    def _statistics_option_changed(self) -> None:
        if self.statistics.value:
            self.show_statistics()

    def show_statistics(self) -> None:
        """Show pooled statistics and distributions per group from the histogram table."""
        if self.statistics_button.disabled:
            return
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
    return (
        f"<div class='mb-help'><b>{html.escape(relative_path)}</b> · "
        f"{html.escape(coords or 'single plane')} · {pixels.shape[0]} × {pixels.shape[1]} px · "
        f"{pixels.dtype.name}, {values}</div>"
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


def _folder_tiles(pre: Preflight) -> list[tuple[str, str, str]]:
    skipped = sum(len(i.paths) for i in pre.discovery.issues if i.code in _SKIPPED_CODES)
    conditions = {leaf.condition for leaf in pre.discovery.leaves}
    dtype = pre.metadata.dtype
    errors = sum(i.is_error for i in pre.issues)
    warnings = len(pre.issues) - errors
    return [
        ("🖼️", f"{len(pre.metadata.files):,}", "images to analyse"),
        ("📂", f"{len(pre.discovery.leaves):,}", "image folders"),
        ("🧪", f"{len(conditions):,}", "conditions"),
        ("🔢", dtype.name if dtype is not None else "mixed", "pixel type"),
        ("⏭️", f"{skipped:,}", "files or folders skipped"),
        (
            "⛔" if errors else "⚠️" if warnings else "✅",
            f"{errors} / {warnings}",
            "errors / warnings",
        ),
    ]


def _ome_zarr_help(available: bool) -> str:
    """Help line under the OME-Zarr option."""
    text = (
        "Off by default. When ticked, every image is also saved in OME-Zarr format in the "
        f"<b>{OME_ZARR_FOLDER}</b> folder of the output, for viewers such as napari and for "
        "later bricks. The full-resolution level keeps the exact pixel values; your images "
        "are not changed. Copying takes extra time and disk space."
    )
    if not available:
        text += (
            " <b>Not available:</b> it needs the zarr package. Install it with "
            '<b>pip install "morphobricks-preprocessing[zarr]"</b> and restart the notebook.'
        )
    return text


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
