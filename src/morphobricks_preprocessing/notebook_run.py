"""Step 3 of the notebook: settings, the run plan and the run."""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.csv_io import read_plane_histograms
from morphobricks_normalization.dtypes import (
    MAX_BIN_COUNT,
    MIN_FLOAT_BIN_COUNT,
    default_bin_count,
    is_integer,
)
from morphobricks_normalization.ranges import DEFAULT_ROBUST_QUANTILES, HistogramSettings

from . import notebook_style as ui
from .axis_review import review_axes
from .notebook_options import (
    _WIDE,
    HISTOGRAM_RANGE_OPTIONS,
    MODE_HELP,
    MODE_OPTIONS,
    VERIFY_CONTENT_HELP,
)
from .notebook_plots import IntensityPanel
from .notebook_render import _bins_help, _label, change_lines, issues_html
from .planes import DEFAULT_MEMORY_LIMIT
from .report import issue_counts, rebuild_line
from .run import RESTART, Preflight, RunMode, RunResult, run_experiment
from .run_labels import recorded_labels
from .validation import has_errors, plural


class _RunStep:
    """Step 3: settings, what will happen, and the run."""

    # Step 3 ------------------------------------------------------------------------------

    def _build_run_step(self) -> None:
        """Build step 3: readiness, settings, mode, what will happen, the run and its results."""
        self.readiness = widgets.HTML()
        self.readiness_card = ui.card("Before you run", [self.readiness])
        self._build_settings()
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
        self.details_box = ui.accordion("Technical details (log)", [self.details])
        self.run_checks = IntensityPanel(
            "Intensity checks",
            "Computed from the table just written, without reading the images again. Each "
            "image is compared only with the others of its condition, so differences between "
            "conditions (treatment effects) are never reported. The run records the limits "
            "below in <b>run_manifest.json</b>; changing them here updates the warnings and "
            "plots at once and is recorded by the next run.",
        )
        self.recommended.observe(lambda _: self._recommended_changed(), names="value")
        for control in (
            self.bins,
            self.histogram_range,
            self.robust_low,
            self.robust_high,
            self.memory_gib,
            self.mode,
            self.verify_content,
        ):
            control.observe(lambda _: self._settings_edited(), names="value")
        self.histogram_range.observe(
            lambda change: ui.set_visible(self.robust_box, change["new"] == "robust"), names="value"
        )
        self.run_button.on_click(lambda _: self.run())

    def _build_settings(self) -> None:
        """Build the settings card: the recommended settings, the advanced ones and the mode."""
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
        self.advanced = widgets.VBox(
            [
                self.bins,
                self.bins_hint,
                widgets.HTML("<b>Histogram range</b>"),
                *self._build_histogram_range(),
                self.memory_gib,
                ui.help_text(
                    "The most working memory one plane may use. A larger plane is read in "
                    "blocks instead: the results are the same, but reading takes longer. Raise "
                    "it only if the computer has enough memory."
                ),
            ]
        )
        ui.set_visible(self.advanced, False)
        self.mode = widgets.RadioButtons(
            options=MODE_OPTIONS, value=RESTART, layout=widgets.Layout(**_WIDE)
        )
        self.verify_content = widgets.Checkbox(
            value=True,
            description="Verify each image's content (SHA-256) before reusing its rows",
            indent=False,
            layout=widgets.Layout(**_WIDE),
        )
        self.settings_card = ui.card(
            "Settings",
            [
                self.recommended,
                self.recommended_hint,
                self.advanced,
                widgets.HTML("<b>Mode</b>"),
                self.mode,
                ui.help_text(MODE_HELP),
                self.verify_content,
                ui.help_text(VERIFY_CONTENT_HELP),
            ],
        )

    def _build_histogram_range(self) -> list[widgets.Widget]:
        """Build the histogram range choice and its robust quantiles; return the widgets."""
        self.histogram_range = widgets.RadioButtons(
            options=HISTOGRAM_RANGE_OPTIONS, value="minmax", layout=widgets.Layout(**_WIDE)
        )
        self.robust_low, self.robust_high = (
            widgets.BoundedFloatText(
                value=value, min=0, max=100, step=0.01, description=text, style=_label()
            )
            for value, text in zip(
                DEFAULT_ROBUST_QUANTILES,
                ("Lower quantile (%)", "Upper quantile (%)"),
                strict=True,
            )
        )
        self.robust_box = widgets.VBox([self.robust_low, self.robust_high])
        ui.set_visible(self.robust_box, False)
        return [
            self.histogram_range,
            self.robust_box,
            ui.help_text(
                "For histograms that are not exact (float images, and integer images with "
                "more values than bins). A few extreme pixels, such as hot pixels, can "
                "squeeze every other pixel into a few bins of the minimum-to-maximum range; "
                "the run then warns about a narrow range. The robust range bins between "
                "the two quantiles and counts the pixels outside separately. 8- and 16-bit "
                "histograms are the same either way. Changing it reads every image again."
            ),
        ]

    def _reset_settings(self, pre: Preflight) -> None:
        """Set the bin count limits and default for the checked pixel type."""
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
        self._prefill_check()
        self._recommended_changed()

    def _recommended_changed(self) -> None:
        """Show or hide the advanced settings; the recommended ones are set back when ticked."""
        recommended = self.recommended.value
        ui.set_visible(self.advanced, not recommended)
        if recommended:
            if getattr(self, "_default_bins", None):
                self.bins.value = self._default_bins
            self.histogram_range.value = "minmax"
            self.robust_low.value, self.robust_high.value = DEFAULT_ROBUST_QUANTILES
            self.memory_gib.value = DEFAULT_MEMORY_LIMIT / 2**30
        bins = getattr(self, "_default_bins", None)
        bins_text = f"{bins:,} bins per plane" if bins else "the default bin count"
        self.recommended_hint.value = (
            f"<div class='mb-help'>Recommended: {bins_text}, each plane's minimum to maximum as "
            "the histogram range and a memory limit of 1 GiB per plane. Untick to change "
            "them.</div>"
        )
        self._refresh_run_step()

    def _settings_edited(self) -> None:
        """Show "What will happen" again after a setting changed: it plans the next run."""
        self._plan_for_next_run = True
        self._refresh_run_step()

    def _refresh_plan_card(self) -> None:
        """Hide "What will happen" after a run until a setting changes: it plans a *next* run."""
        done = self.result is not None and self.result.written
        ui.set_visible(self.plan_card, not done or getattr(self, "_plan_for_next_run", False))

    def _refresh_run_step(self) -> None:
        """Update what is still to do before the run, and what will happen."""
        if self.result is None:
            self.run_next.value = ""
        pre = self.preflight
        # The masks have their own item below: a mask error is fixed on the masks card.
        folder_ok = pre is not None and not has_errors(self._step1_issues())
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
            (self.confirmed_mapping is not None, "Confirm the axis roles (step 2)"),
            *self._gate_items(),
        ]
        masks = self._masks_ready_item(review)
        if masks is not None:
            items.insert(1, masks)
        if self._output_file_in_the_way() is not None:
            items.insert(0, (False, "Choose an output folder that is not a file (step 1)"))
        self._refresh_masks(review)
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
        self._refresh_plan_card()
        self._refresh_mask_step()

    def _gate_items(self) -> list[tuple[bool, str]]:
        """Return the answers after the axis roles that the run waits for (the image type)."""
        return [(self.confirmed_image_type is not None, "Confirm the image type (step 2)")]

    def run(self) -> RunResult | None:
        """Compute or update the histogram table (pass 2) and show what happened."""
        self._refresh_run_step()
        if self.run_button.disabled:
            return None
        try:
            histogram_settings = self._histogram_settings()
        except ValueError as exc:
            self.run_status.value = ui.status(
                "error",
                "The histogram range cannot be used.",
                [str(exc), "Correct the quantiles in Settings above."],
            )
            return None
        self._start_run()
        try:
            with self.log.attached():
                self.result = self._call_run(histogram_settings)
        except Exception as exc:  # unexpected failures still get a readable message
            self._run_failed(exc)
            return None
        finally:
            self._idle(self.run_button, " Compute histograms", "play")
        self._finish_run(self.result)
        return self.result

    def _start_run(self) -> None:
        """Clear the results of an earlier run and show that this one is starting."""
        self._busy(self.run_button, "Computing…")
        self.run_next.value = ""
        self.run_checks.clear()
        self.run_lock.hide()
        self.run_report.clear()
        self._mask_step_stale()
        ui.set_visible(self.progress, True)
        self.progress.value, self.progress.max, self.progress.bar_style = 0, 1, "info"
        self.run_status.value = ui.working("Preparing: checking everything again…")
        self.run_tiles.value = self.run_issues.value = ""

    def _call_run(self, histogram_settings: HistogramSettings) -> RunResult:
        """Run the brick with the inputs of :meth:`_given_inputs` and the mode chosen."""
        given = self._given_inputs()
        return run_experiment(
            given.experiment_root,
            given.mapping,
            output_folder=self._output_folder(),
            bin_count=given.bin_count,
            memory_limit=self._memory_limit(),
            missing_as_singleton=given.missing_as_singleton,
            exclude=given.exclude,
            masks_folder=given.masks_folder,
            mask_axes=given.mask_axes,
            mode=RunMode(self.mode.value, given.verify_content),
            progress=self._progress,
            image_type=given.image_type,
            intensity_settings=given.intensity_settings,
            histogram_settings=histogram_settings,
        )

    def _run_failed(self, exc: Exception) -> None:
        """Show an unexpected failure in plain words, with the traceback in the details."""
        self.log.lines.append(traceback.format_exc())
        self.progress.bar_style = "danger"
        self.run_status.value = ui.status(
            "error",
            "The run stopped because of an unexpected problem.",
            [f"{type(exc).__name__}: {exc}", "Open Technical details below for the full report."],
        )
        self._show_log()

    def _finish_run(self, result: RunResult) -> None:
        """Show what the run did and, when it wrote the table, its intensity checks."""
        self._plan_for_next_run = False
        self._refresh_plan_card()
        self._show_run_result(result)
        if result.written:
            self._recorded_after_run()
            self._show_run_checks(result.csv_path)
        self._show_log()
        if result.written:
            self.csv_file.value = str(result.csv_path)
            self.stats_status.value = ui.status(
                "info",
                "The table the run just wrote is selected.",
                ["Click Calculate statistics to compare the groups (optional)."],
            )

    def _show_run_result(self, result: RunResult) -> None:
        """Show what the run wrote, or why it stopped, with its report."""
        self.run_next.value = ""
        self.progress.bar_style = "success" if result.written else "danger"
        self.run_issues.value = issues_html(result.issues) if result.issues else ""
        if result.report_written:
            self.run_report.show(result.output_folder, "The run")
        if not result.written:
            self._show_stopped(result)
            return
        lines = [
            html.escape(f"Table: {result.csv_path}"),
            html.escape(f"Manifest: {result.manifest_path}"),
        ]
        if result.design_path is not None:
            lines.append(html.escape(f"Experiment design: {result.design_path}"))
        lines.append(f"Mode: <b>{result.mode}</b>")
        if result.rebuild_reason:
            lines.append(html.escape(rebuild_line(result)))
        if result.replaced:
            lines.append("Replaced: " + html.escape(", ".join(result.replaced)))
        changes = change_lines(result.input_changes)
        if changes:
            lines.append(
                "Inputs that differ from the earlier run:<ul>"
                + "".join(f"<li>{line}</li>" for line in changes)
                + "</ul>"
            )
        self.run_status.value = ui.status(
            "ok", f"Done: {plural(result.plane_count, 'plane')} in the table.", lines, raw=True
        )
        self.run_next.value = ui.next_steps(
            "Go to Step 4 (optional) and calculate the statistics.",
            "Run its cell, choose how to group the images, then click Calculate statistics. "
            "You can also finish here: the histogram table is ready for later MorphoBricks bricks.",
        )
        self._show_run_tiles(result)

    def _show_stopped(self, result: RunResult) -> None:
        """Say what a run that stopped wrote (its report), or that it was refused (PP-8c)."""
        if any(issue.code == "output_locked" for issue in result.issues):
            self.run_lock.show(result.output_folder)
        if result.report_written:
            title = "The run stopped: no table or manifest was written."
            first = "Its report was written; the earlier results are as they were."
        else:
            title = "Nothing was written."
            first = "The run was refused before it started."
        self.run_status.value = ui.status(
            "error",
            title,
            [first, "Read the messages below; each one says what to do.", "Then run again."],
        )

    def _show_run_tiles(self, result: RunResult) -> None:
        """Show the run's headline numbers."""
        kept = 0 if result.rebuild_reason else len(result.unchanged)
        tiles = [
            ("🧩", f"{result.plane_count:,}", "planes in the table"),
            ("📖", f"{len(result.read):,}", "images read"),
            ("⏯️", f"{len(result.resumed):,}", "images resumed from an interrupted run"),
            ("♻️", f"{kept:,}", "images kept from the last run"),
            ("🆕", f"{len(result.new):,}", "new images"),
            ("✏️", f"{len(result.changed):,}", "changed images"),
            ("🗑️", f"{len(result.removed):,}", "images removed"),
            ("⛔", f"{len(result.excluded):,}", "excluded images"),
            ("⚠️" if result.issues else "✅", issue_counts(result.issues), "checks"),
        ]
        self.run_tiles.value = ui.tiles(tiles)

    def _show_run_checks(self, path: Path) -> None:
        """Show the intensity checks and plots of the table the run wrote."""
        try:
            with self.log.attached():
                self.run_checks.show(read_plane_histograms(path), recorded_labels(path.parent))
        except Exception as exc:  # the table is written; only the checks failed
            self.log.lines.append(traceback.format_exc())
            self.run_checks.fail(exc)
            return
        report = self.run_checks.report
        if report is not None and report.issues:
            self.run_next.value = ui.next_steps(
                "Read the intensity checks above, then go to Step 4 (optional).",
                "Each warning names the image or biological repeat, the channel and the values "
                "compared, and says what to do. The histogram table is ready for later "
                "MorphoBricks bricks either way.",
            )

    def _progress(self, done: int, total: int, path: str) -> None:
        self.progress.max = max(total, 1)
        self.progress.value = done
        if path:
            self.run_status.value = ui.working(f"Reading image {done + 1} of {total}: {path}")
        else:
            self.run_status.value = ui.working("Writing the table…")
