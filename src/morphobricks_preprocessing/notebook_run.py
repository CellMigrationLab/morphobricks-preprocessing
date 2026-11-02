"""Step 3 of the notebook: settings, the run plan and the run."""

from __future__ import annotations

import html
import traceback
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.csv_io import OUTPUT_FILENAME, read_plane_histograms
from morphobricks_normalization.dtypes import (
    MAX_BIN_COUNT,
    MIN_FLOAT_BIN_COUNT,
    default_bin_count,
    is_integer,
)
from morphobricks_normalization.ranges import DEFAULT_ROBUST_QUANTILES

from . import notebook_style as ui
from .axes import AxisReview
from .axis_review import review_axes
from .discovery import default_output_folder
from .manifest import MANIFEST_FILENAME, ManifestFormatError, read_manifest
from .notebook_options import _PLAN_PATHS, HISTOGRAM_RANGE_OPTIONS, IMAGE_TYPE_NAMES
from .notebook_plots import IntensityPanel
from .notebook_render import _bins_help, _label, issues_html
from .planes import DEFAULT_MEMORY_LIMIT, PlaneLayout, PlaneReadError, format_bytes, plane_layout
from .report import issue_counts
from .run import FULL, RESTART, Preflight, RunResult, run_experiment
from .validation import has_errors, plural


class _RunStep:
    """Step 3: settings, what will happen, and the run."""

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
        self.histogram_range = widgets.RadioButtons(
            options=HISTOGRAM_RANGE_OPTIONS, value="minmax", layout=widgets.Layout(width="99%")
        )
        self.robust_low = widgets.BoundedFloatText(
            value=DEFAULT_ROBUST_QUANTILES[0],
            min=0,
            max=100,
            step=0.01,
            description="Lower quantile (%)",
            style=_label(),
        )
        self.robust_high = widgets.BoundedFloatText(
            value=DEFAULT_ROBUST_QUANTILES[1],
            min=0,
            max=100,
            step=0.01,
            description="Upper quantile (%)",
            style=_label(),
        )
        self.robust_box = widgets.VBox([self.robust_low, self.robust_high])
        ui.set_visible(self.robust_box, False)
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
                widgets.HTML("<b>Histogram range</b>"),
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
            self.force_rebuild,
        ):
            control.observe(lambda _: self._refresh_run_step(), names="value")
        self.histogram_range.observe(
            lambda change: ui.set_visible(self.robust_box, change["new"] == "robust"), names="value"
        )
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
            self.histogram_range.value = "minmax"
            self.robust_low.value, self.robust_high.value = DEFAULT_ROBUST_QUANTILES
            self.memory_gib.value = DEFAULT_MEMORY_LIMIT / 2**30
            self.force_rebuild.value = False
        bins = getattr(self, "_default_bins", None)
        bins_text = f"{bins:,} bins per plane" if bins else "the default bin count"
        self.recommended_hint.value = (
            f"<div class='mb-help'>Recommended: {bins_text}, each plane's minimum to maximum as "
            "the histogram range, a memory limit of 1 GiB per plane, and only new or changed "
            "images are read. Untick to change them.</div>"
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
            *(
                [(self._design_confirmed(), "Confirm the biological repeats (step 1)")]
                if self._show_design
                else []
            ),
            (self.confirmed_mapping is not None, "Confirm the axis roles (step 2)"),
            (self.confirmed_image_type is not None, "Confirm the image type (step 2)"),
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

    def _plan_layouts(self, review: AxisReview) -> dict[str, PlaneLayout]:
        """Return how each image's planes are read, from the headers only.

        Opening every header takes a moment on large experiments, and this runs on
        every settings edit, so the layouts are kept until the checked folder, the
        axis roles, the memory limit or the bin count change.
        """
        bins = self.bins.value or None
        histogram = self._histogram_settings_or_none()
        key = (
            self.confirmed_mapping,
            self.missing_as_singleton.value,
            self._memory_limit(),
            bins,
            histogram,
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
                    root / rel,
                    mapping,
                    memory_limit=self._memory_limit(),
                    bin_count=bins,
                    histogram_settings=histogram,
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
            f"Files written: <b>{OUTPUT_FILENAME}</b> (one row per plane, its channels in "
            "columns) and "
            f"<b>{MANIFEST_FILENAME}</b> (what was read, with which settings).",
        ]
        if self.confirmed_image_type is not None:
            lines.append(
                f"Image type: <b>{IMAGE_TYPE_NAMES[self.confirmed_image_type]}</b>, recorded in "
                "the manifest for later bricks (the histograms are the same for either type)."
            )
        files = self.preflight.metadata.files
        tiffs = sum(not f.is_ome_zarr for f in files)
        zarrs = len(files) - tiffs
        if zarrs:
            lines.append(
                f"<b>{plural(zarrs, 'OME-Zarr image')}</b> will be read directly, with the axes "
                f"{'its' if zarrs == 1 else 'their'} metadata declares."
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
                    "changed images will be read; if the bin count, histogram range or axis "
                    "roles changed, every image is read again."
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
        try:
            histogram_settings = self._histogram_settings()
        except ValueError as exc:
            self.run_status.value = ui.status(
                "error",
                "The histogram range cannot be used.",
                [str(exc), "Correct the quantiles in Settings above."],
            )
            return None
        self._busy(self.run_button, "Computing…")
        self.run_next.value = ""
        self.run_checks.clear()
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
                    mode=FULL if self.force_rebuild.value else RESTART,
                    progress=self._progress,
                    design=self.design,
                    image_type=self.confirmed_image_type,
                    intensity_settings=self.run_checks.settings(),
                    histogram_settings=histogram_settings,
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
        if self.result.written:
            self._show_run_checks(self.result.csv_path)
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
                "The run stopped: no table or manifest was written.",
                ["Read the messages below; each one says what to do.", "Then run again."],
            )
            return
        lines = [f"Table: {result.csv_path}", f"Manifest: {result.manifest_path}"]
        if result.design_path is not None:
            lines.append(f"Experiment design: {result.design_path}")
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
        tiles.append(("⚠️", issue_counts(result.issues), "checks"))
        self.run_tiles.value = ui.tiles(tiles)

    def _show_run_checks(self, path: Path) -> None:
        """Show the intensity checks and plots of the table the run wrote."""
        try:
            with self.log.attached():
                self.run_checks.show(read_plane_histograms(path))
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
