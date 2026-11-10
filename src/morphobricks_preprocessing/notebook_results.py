"""Step 4 of the notebook: pooled statistics per group."""

from __future__ import annotations

import traceback
from collections.abc import Sequence
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.csv_io import CsvFormatError, read_plane_histograms
from morphobricks_normalization.records import group_records

from . import notebook_style as ui
from .notebook_options import _WIDE, LEVEL_OPTIONS
from .notebook_plots import IntensityPanel, distribution_png
from .notebook_progress import Wait
from .report import LEVEL_NAMES, group_summaries, group_table
from .validation import plural

STAGES = 4
"""The parts of the statistics: read the table, pool, plot, check."""


class _ResultsStep:
    """Step 4: pooled statistics and distributions per group of any histogram table."""

    # Step 4 ------------------------------------------------------------------------------

    def _build_results_step(self) -> None:
        """Build step 4: the table, the grouping, the statistics, plots and checks."""
        self._build_csv_card()
        self._build_grouping_card()
        self._build_statistics_cards()
        self.table_checks = IntensityPanel(
            "Intensity checks",
            "The same checks as after the run, for the chosen table. Each image is compared only "
            "with the others of its condition, so differences between conditions are never "
            "reported. Nothing is written.",
        )
        self.statistics_button.on_click(lambda _: self.show_statistics())
        self.csv_browse.on_click(lambda _: self._browse_csv())
        self.csv_file.observe(lambda _: self._csv_changed(), names="value")
        self.level.observe(lambda _: self._statistics_option_changed(), names="value")

    def _build_csv_card(self) -> None:
        """Build the card that chooses the histogram table."""
        self.csv_file = widgets.Text(
            placeholder="filled in after the run, or choose a plane_histograms.csv",
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
                    "The table to calculate the statistics from. After a successful run "
                    "this is the table just written. To look at another experiment, "
                    "paste the path of its <b>plane_histograms.csv</b> or use "
                    "<b>Browse…</b>."
                ),
            ],
        )

    def _build_grouping_card(self) -> None:
        """Build the grouping and percentile choices with the button that calculates."""
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

    def _build_statistics_cards(self) -> None:
        """Build the statistics table and the intensity distribution cards, hidden."""
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

    def _browse_csv(self) -> None:
        current = ui.clean_path(self.csv_file.value)
        start = str(Path(current).parent) if current else ui.clean_path(self.folder.value)
        chosen = ui.ask_open_file(start, (("Histogram tables", "*.csv"), ("All files", "*.*")))
        if chosen is None:
            self.stats_status.value = ui.no_dialog_status("file", "the plane_histograms.csv file")
        elif chosen:
            self.csv_file.value = chosen

    def _csv_changed(self) -> None:
        # Results of another table must not stay on screen next to the new path.
        self.stats_status.value = self.stats_tiles.value = self.statistics.value = ""
        self.stats_next.value = ""
        ui.set_visible(self.statistics_card, False)
        ui.set_visible(self.distribution_card, False)
        self.table_checks.clear()

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
                    [
                        "Run the images first (Run), or click Browse… above to pick a "
                        "plane_histograms.csv."
                    ],
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
        self._stats_wait = Wait(self.stats_status)
        self._stats_wait.start("Reading the histogram table")
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
        wait = self._stats_wait
        wait.phase("Pooling the planes of each group", STAGES, 1)
        level = self.level.value
        summaries = group_summaries(records, level, percentiles)
        groups = group_records(records, level)
        images = {r.source_relative_path for r in records}
        first = records[0].channel_histogram(0) if records else None
        self.stats_tiles.value = ui.tiles(
            [
                ("🧪", f"{len(groups):,}", f"{LEVEL_NAMES[level]} groups"),
                ("🖼️", f"{len(images):,}", "images"),
                ("🧩", f"{len(records):,}", "XYC planes"),
                ("🎨", f"{records[0].channel_count:,}" if records else "-", "channels"),
                ("🔢", first.dtype.name if first else "-", "pixel type"),
                ("📊", f"{first.bin_count:,}" if first else "-", "bins per plane"),
            ]
        )
        rows = group_table(summaries, level)
        depth = len(summaries[0].labels) if summaries else 1
        # The labels and the channel column are text; the counts and statistics numbers.
        self.statistics.value = ui.table(rows, numeric_from=depth + 1)
        ui.set_visible(self.statistics_card, True)
        wait.phase("Drawing the intensity distributions", STAGES, 2)
        png = distribution_png(groups)
        self.distribution.value = png or b""
        ui.set_visible(self.distribution_card, bool(png))
        wait.phase("Checking the intensities of every image", STAGES, 3)
        self.table_checks.show(records)
        self.stats_status.value = ui.status(
            "ok",
            f"Statistics of {plural(len(groups), LEVEL_NAMES[level])} calculated.",
            [f"From the table {path}"],
        )
        self.stats_next.value = ui.next_steps(
            "Review the group statistics, intensity distributions and intensity checks above.",
            "Choose another grouping to compare repeats or images. The histogram table "
            "is ready for later MorphoBricks bricks; this optional step writes no files.",
        )
