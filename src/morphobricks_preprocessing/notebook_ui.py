"""Widgets for the biologist-facing notebook.

The notebook ``notebooks/preprocessing.ipynb`` shows the four steps of
:class:`PreprocessingNotebook`, one per cell:

1. **Folder** - check the experiment folder and image headers, and confirm the
   labels taken from the folder names.
2. **Axes** - review the suggested axis roles, preview a plane and confirm.
3. **Run** - compute or update the histogram table.
4. **Results** - pooled statistics per condition, repeat or image.

The widgets only collect choices and show results; every check and computation
is done by the same functions the command line and the tests use. Needs the
``notebook`` extra (``ipywidgets`` and ``matplotlib``).
"""

from __future__ import annotations

import html
import io
import logging
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

from .axes import AxisMapping, axis_length_ranges, axis_name, review_axes
from .csv_io import OUTPUT_FILENAME, read_plane_histograms
from .discovery import default_output_folder
from .dtypes import UnsupportedDtypeError, default_bin_count, is_integer
from .planes import DEFAULT_MEMORY_LIMIT, Plane
from .records import LEVELS
from .report import (
    LEVEL_NAMES,
    group_summaries,
    group_table,
    label_tree,
    preflight_summary,
    run_summary,
)
from .run import Preflight, RunResult, preflight, run_experiment, sample_planes
from .validation import ValidationIssue, has_errors, plural

ROLE_OPTIONS = [
    ("no role", "-"),
    ("X (width)", "X"),
    ("Y (height)", "Y"),
    ("Z (depth)", "Z"),
    ("C (channel)", "C"),
    ("T (time)", "T"),
]

# Problems a user can get past by leaving the affected images out (open question 6).
EXCLUDABLE_CODES = ("unreadable_tiff", "multiple_series", "unsupported_dtype")

_MAX_PATHS_SHOWN = 20
_PACKAGE_LOGGER = "morphobricks_preprocessing"
_LABEL_STYLE = {"description_width": "11em"}


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

        # Step 1
        self.folder = widgets.Text(
            value=str(experiment_folder),
            description="Experiment folder",
            placeholder="paste the path of the experiment folder",
            layout=widgets.Layout(width="95%"),
            style=_LABEL_STYLE,
        )
        self.output = widgets.Text(
            value=str(output_folder),
            description="Output folder",
            placeholder="empty: next to the experiment folder (<name>_MorphoBricks_Preprocessing)",
            layout=widgets.Layout(width="95%"),
            style=_LABEL_STYLE,
        )
        self.exclude = widgets.Textarea(
            description="Exclude images",
            placeholder="optional: one image path per line, relative to the experiment folder",
            layout=widgets.Layout(width="95%", height="5em"),
            style=_LABEL_STYLE,
        )
        self.check_button = widgets.Button(
            description="Check folder", button_style="primary", icon="search"
        )
        self.folder_summary = widgets.HTML()
        self.label_tree = widgets.HTML()
        self.folder_issues = widgets.HTML()
        self.exclude_button = widgets.Button(
            description="Exclude these images and check again",
            layout=widgets.Layout(width="auto", display="none"),
        )
        self.labels_confirmed = widgets.Checkbox(
            description="The folders and labels above are correct",
            disabled=True,
            indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.check_button.on_click(lambda _: self.check_folder())
        self.exclude_button.on_click(lambda _: self._exclude_blocking_images())
        self.labels_confirmed.observe(lambda _: self._update_run_state(), names="value")

        # Step 2
        self.role_dropdowns: list[widgets.Dropdown] = []
        self.missing_as_singleton = widgets.Checkbox(
            description="Images lacking some of these axes have a single plane along them "
            "(for example one channel)",
            indent=False,
            layout=widgets.Layout(width="auto"),
        )
        self.axis_issues = widgets.HTML()
        self.preview_button = widgets.Button(description="Show preview", icon="image")
        self.preview_box = widgets.HBox(layout=widgets.Layout(flex_flow="row wrap"))
        self.confirm_button = widgets.Button(
            description="Confirm axis roles", button_style="success", icon="check"
        )
        self.axis_box = widgets.VBox()
        self.missing_as_singleton.observe(lambda _: self._mapping_changed(), names="value")
        self.preview_button.on_click(lambda _: self.show_preview())
        self.confirm_button.on_click(lambda _: self._toggle_confirmation())

        # Step 3
        self.bins = widgets.IntText(description="Bins per plane", style=_LABEL_STYLE)
        self.bins_hint = widgets.HTML()
        self.memory_gib = widgets.BoundedFloatText(
            value=DEFAULT_MEMORY_LIMIT / 2**30,
            min=0.01,
            max=4096,
            step=0.5,
            description="Memory limit (GiB)",
            style=_LABEL_STYLE,
        )
        self.force_rebuild = widgets.Checkbox(
            description="Recompute every row, even for unchanged images", indent=False
        )
        self.run_button = widgets.Button(
            description="Run", button_style="primary", icon="play", disabled=True
        )
        self.run_status = widgets.HTML()
        self.progress = widgets.IntProgress(
            value=0, min=0, max=1, layout=widgets.Layout(width="95%", visibility="hidden")
        )
        self.run_output = widgets.HTML()
        self.run_issues = widgets.HTML()
        self.details = widgets.Textarea(
            layout=widgets.Layout(width="95%", height="12em"), disabled=True
        )
        self.details_box = widgets.Accordion(children=[self.details])
        self.details_box.set_title(0, "Technical details")
        self.details_box.selected_index = None
        self.run_button.on_click(lambda _: self.run())

        # Step 4
        self.level = widgets.Dropdown(
            options=[(LEVEL_NAMES[level].capitalize(), level) for level in (*LEVELS, "image")],
            value="condition",
            description="Group by",
            style=_LABEL_STYLE,
        )
        self.percentiles = widgets.Text(
            value="1, 99.8", description="Percentiles", style=_LABEL_STYLE
        )
        self.statistics_button = widgets.Button(description="Show statistics", icon="table")
        self.statistics = widgets.HTML()
        self.statistics_button.on_click(lambda _: self.show_statistics())

        self._refresh_axis_step()
        self._update_run_state()

    # Steps ------------------------------------------------------------------------------

    def folder_step(self) -> widgets.Widget:
        """Step 1: experiment folder, folder check and label confirmation."""
        return widgets.VBox(
            [
                widgets.HTML("<h3>Step 1 · Check the experiment folder</h3>"),
                self.folder,
                self.output,
                self.exclude,
                self.check_button,
                self.folder_summary,
                self.label_tree,
                self.folder_issues,
                self.exclude_button,
                self.labels_confirmed,
            ]
        )

    def axis_step(self) -> widgets.Widget:
        """Step 2: axis roles, preview and confirmation."""
        return widgets.VBox(
            [widgets.HTML("<h3>Step 2 · Review the axis roles</h3>"), self.axis_box]
        )

    def run_step(self) -> widgets.Widget:
        """Step 3: settings and the run."""
        return widgets.VBox(
            [
                widgets.HTML("<h3>Step 3 · Compute the histograms</h3>"),
                widgets.HBox([self.bins, self.bins_hint]),
                self.memory_gib,
                self.force_rebuild,
                self.run_button,
                self.run_status,
                self.progress,
                self.run_output,
                self.run_issues,
                self.details_box,
            ]
        )

    def results_step(self) -> widgets.Widget:
        """Step 4: pooled statistics from the histogram table."""
        note = (
            "Statistics are pooled from the histograms of all planes in each group, never "
            "averaged over images."
        )
        return widgets.VBox(
            [
                widgets.HTML("<h3>Step 4 · Look at the results</h3>"),
                widgets.HTML(f"<p>{note}</p>"),
                widgets.HBox([self.level, self.percentiles, self.statistics_button]),
                self.statistics,
            ]
        )

    # Actions ----------------------------------------------------------------------------

    def check_folder(self) -> Preflight | None:
        """Run the folder and header check (pass 1) and show its results."""
        folder = self.folder.value.strip()
        if not folder:
            self.folder_summary.value = _note("Enter the experiment folder first.")
            return None
        with self.log.attached():
            self.preflight = preflight(folder, self._output_folder(), exclude=self._excluded())
        pre = self.preflight
        self.folder_summary.value = _pre(preflight_summary(pre))
        self.label_tree.value = (
            "<b>Labels from the folder names</b>" + _pre(label_tree(pre.discovery))
            if pre.discovery.leaves
            else ""
        )
        self.folder_issues.value = issues_html(pre.issues)
        blocking = [i for i in pre.issues if i.code in EXCLUDABLE_CODES]
        self.exclude_button.layout.display = "" if blocking else "none"
        self.labels_confirmed.value = False
        self.labels_confirmed.disabled = not pre.discovery.leaves
        self.mapping = pre.suggested_mapping
        self.confirmed_mapping = None
        self.missing_as_singleton.value = False
        self.missing_as_singleton.disabled = False
        self.confirm_button.description = "Confirm axis roles"
        self.confirm_button.button_style = "success"
        dtype = pre.metadata.dtype
        try:
            self.bins.value = default_bin_count(dtype) if dtype is not None else 0
            self.bins_hint.value = _bins_hint(dtype) if dtype is not None else ""
        except UnsupportedDtypeError:
            self.bins.value, self.bins_hint.value = 0, ""
        self._refresh_axis_step()
        self._update_run_state()
        self._show_log()
        return pre

    def show_preview(self) -> None:
        """Show one plane per arrangement of axes under the current axis roles."""
        if self.preflight is None or self.mapping is None:
            return
        try:
            mapping = self._current_mapping()
        except ValueError:
            return
        with self.log.attached():
            samples = sample_planes(self.preflight, mapping, memory_limit=self._memory_limit())
        images = []
        for sample in samples:
            if sample.plane is None:
                images.append(widgets.HTML(_note(f"{sample.relative_path}: {sample.error}")))
                continue
            images.append(
                widgets.VBox(
                    [
                        widgets.Image(value=plane_png(sample.plane), format="png"),
                        widgets.HTML(
                            f"<small>{html.escape(sample.relative_path)} "
                            f"({html.escape(_coordinates(sample.plane))})</small>"
                        ),
                    ]
                )
            )
        images.append(
            widgets.HTML(
                "<small>Brightness is stretched for display only; the images are not "
                "changed.</small>"
            )
        )
        self.preview_box.children = images
        self._show_log()

    def run(self) -> RunResult | None:
        """Compute or update the histogram table (pass 2) and show what happened."""
        if not self._ready():
            return None
        self.progress.layout.visibility = "visible"
        self.progress.value, self.progress.max = 0, 1
        self.run_output.value = _note("Running...")
        self.run_issues.value = ""
        with self.log.attached():
            self.result = run_experiment(
                self.folder.value.strip(),
                self.confirmed_mapping,
                output_folder=self._output_folder(),
                bin_count=self.bins.value or None,
                memory_limit=self._memory_limit(),
                missing_as_singleton=self.missing_as_singleton.value,
                exclude=self._excluded(),
                force_rebuild=self.force_rebuild.value,
                progress=self._progress,
            )
        self.run_output.value = _pre(run_summary(self.result))
        self.run_issues.value = issues_html(self.result.issues)
        self._show_log()
        if self.result.written:
            self.show_statistics()
        return self.result

    def show_statistics(self) -> None:
        """Show pooled statistics per group from the histogram table."""
        path = self._csv_path()
        if path is None or not path.exists():
            self.statistics.value = _note("There is no histogram table yet. Run step 3 first.")
            return
        try:
            percentiles = [float(q) for q in self.percentiles.value.replace(";", ",").split(",")]
            if not all(0 <= q <= 100 for q in percentiles):
                raise ValueError
        except ValueError:
            self.statistics.value = _note(
                "Percentiles must be numbers from 0 to 100, separated by commas."
            )
            return
        records = read_plane_histograms(path)
        summaries = group_summaries(records, self.level.value, percentiles)
        self.statistics.value = (
            f"<p>{html.escape(str(path))}: {plural(len(records), 'plane')}</p>"
            + table_html(group_table(summaries, self.level.value))
        )

    # State ------------------------------------------------------------------------------

    def _refresh_axis_step(self) -> None:
        pre = self.preflight
        if pre is None:
            self.axis_box.children = [widgets.HTML(_note("Check the experiment folder in step 1."))]
            return
        if self.mapping is None:
            self.axis_box.children = [
                widgets.HTML(
                    _note(
                        "No axis roles can be suggested because the images have no readable "
                        "headers or their dimensions are arranged differently. See the "
                        "messages in step 1."
                    )
                )
            ]
            return
        ranges = axis_length_ranges(self.mapping, pre.metadata.files)
        self.role_dropdowns = []
        rows = []
        for axis, role, (low, high) in zip(
            self.mapping.axes, self.mapping.roles, ranges, strict=True
        ):
            dropdown = widgets.Dropdown(options=ROLE_OPTIONS, value=role or "-")
            dropdown.observe(lambda _: self._mapping_changed(), names="value")
            self.role_dropdowns.append(dropdown)
            length = f"{low}" if low == high else f"{low} to {high}"
            rows.append(
                widgets.HBox(
                    [
                        widgets.HTML(
                            f"<b>{html.escape(axis_name(axis))}</b>, length {length}",
                            layout=widgets.Layout(width="20em"),
                        ),
                        dropdown,
                    ]
                )
            )
        lacking = any(f.readable and f.axes != self.mapping.axes for f in pre.metadata.files)
        self.missing_as_singleton.layout.display = "" if lacking else "none"
        self.preview_box.children = []
        intro = (
            "Choose the role of each axis. The suggestion comes from the image metadata; "
            "check it before confirming. Axes with more than one plane need Z, C or T."
        )
        self.axis_box.children = [
            widgets.HTML(f"<p>{intro}</p>"),
            *rows,
            self.missing_as_singleton,
            self.axis_issues,
            widgets.HBox([self.preview_button, self.confirm_button]),
            self.preview_box,
        ]
        self._mapping_changed()

    def _mapping_changed(self) -> None:
        if self.preflight is None or self.mapping is None:
            return
        try:
            self.mapping = self._current_mapping()
        except ValueError as exc:
            self.axis_issues.value = _note(str(exc))
            self.confirm_button.disabled = True
            return
        review = review_axes(
            self.preflight.metadata,
            self.mapping,
            missing_as_singleton=self.missing_as_singleton.value,
        )
        if review.issues:
            self.axis_issues.value = issues_html(review.issues)
        else:
            self.axis_issues.value = _ok("These axis roles are valid for every image.")
        self.confirm_button.disabled = self.confirmed_mapping is None and not review.ok

    def _toggle_confirmation(self) -> None:
        if self.confirmed_mapping is None:
            self.confirmed_mapping = self._current_mapping()
        else:
            self.confirmed_mapping = None
        locked = self.confirmed_mapping is not None
        for dropdown in self.role_dropdowns:
            dropdown.disabled = locked
        self.missing_as_singleton.disabled = locked
        self.confirm_button.description = "Change axis roles" if locked else "Confirm axis roles"
        self.confirm_button.button_style = "" if locked else "success"
        self._mapping_changed()
        self._update_run_state()

    def _update_run_state(self) -> None:
        missing = []
        if self.preflight is None:
            missing.append("check the experiment folder (step 1)")
        elif not self.preflight.ok:
            missing.append("resolve the errors found in step 1")
        if self.preflight is not None and not self.labels_confirmed.value:
            missing.append("confirm the labels (step 1)")
        if self.confirmed_mapping is None:
            missing.append("confirm the axis roles (step 2)")
        self.run_button.disabled = bool(missing)
        self.run_status.value = (
            _note("Before running: " + "; ".join(missing) + ".") if missing else ""
        )

    def _ready(self) -> bool:
        self._update_run_state()
        return not self.run_button.disabled

    def _current_mapping(self) -> AxisMapping:
        return AxisMapping(self.mapping.axes, tuple(d.value for d in self.role_dropdowns))

    def _excluded(self) -> list[str]:
        return [line.strip() for line in self.exclude.value.splitlines() if line.strip()]

    def _exclude_blocking_images(self) -> None:
        if self.preflight is None:
            return
        paths = sorted(
            {p for i in self.preflight.issues if i.code in EXCLUDABLE_CODES for p in i.paths}
        )
        self.exclude.value = "\n".join(sorted(set(self._excluded()) | set(paths)))
        self.check_folder()

    def _output_folder(self) -> str | None:
        return self.output.value.strip() or None

    def _csv_path(self) -> Path | None:
        if self.result is not None and self.result.written:
            return self.result.csv_path
        folder = self.folder.value.strip()
        if not folder:
            return None
        output = self._output_folder()
        return Path(output or default_output_folder(folder)) / OUTPUT_FILENAME

    def _memory_limit(self) -> int:
        return int(self.memory_gib.value * 2**30)

    def _progress(self, done: int, total: int, path: str) -> None:
        self.progress.max = max(total, 1)
        self.progress.value = done
        if path:
            self.run_output.value = _note(f"Reading {done + 1} of {total}: {path}")

    def _show_log(self) -> None:
        self.details.value = "\n".join(self.log.lines[-500:])


def issues_html(issues: Iterable[ValidationIssue]) -> str:
    """Render issues as HTML: errors first, each with its paths and suggested action."""
    issues = sorted(issues, key=lambda issue: not issue.is_error)
    if not issues:
        return _ok("No problems found.")
    parts = []
    for issue in issues:
        colour = "#c62828" if issue.is_error else "#b26a00"
        label = "Error" if issue.is_error else "Warning"
        paths = list(issue.paths)
        shown = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in paths[:_MAX_PATHS_SHOWN])
        if len(paths) > _MAX_PATHS_SHOWN:
            shown += f"<li>... and {len(paths) - _MAX_PATHS_SHOWN:,} more</li>"
        action = (
            f"<div><i>What to do:</i> {html.escape(issue.action)}</div>" if issue.action else ""
        )
        parts.append(
            f'<div style="border-left: 4px solid {colour}; padding: 2px 8px; margin: 6px 0">'
            f'<b style="color: {colour}">{label}:</b> {html.escape(issue.message)}'
            f"{f'<ul>{shown}</ul>' if shown else ''}{action}</div>"
        )
    verdict = (
        _note("Processing is blocked until the errors are resolved.") if has_errors(issues) else ""
    )
    return verdict + "".join(parts)


def table_html(rows: Sequence[Sequence[str]]) -> str:
    """Render rows of text (first row the header) as an HTML table."""
    if not rows:
        return ""
    header = "".join(f"<th>{html.escape(cell)}</th>" for cell in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows[1:]
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def display_range(pixels: np.ndarray, low: float = 0.5, high: float = 99.5) -> tuple[float, float]:
    """Return the brightness range used to show a plane (finite values only)."""
    finite = pixels[np.isfinite(pixels)] if pixels.dtype.kind == "f" else pixels
    if finite.size == 0:
        return 0.0, 1.0
    vmin, vmax = (float(v) for v in np.percentile(finite, [low, high]))
    if vmin == vmax:
        vmax = vmin + 1
    return vmin, vmax


def plane_png(plane: Plane, size: float = 3.2) -> bytes:
    """Render a plane as a PNG image for the preview."""
    pixels = plane.pixels
    vmin, vmax = display_range(pixels)
    figure = Figure(figsize=(size, size * pixels.shape[0] / max(pixels.shape[1], 1)))
    axes = figure.add_axes((0, 0, 1, 1))
    axes.imshow(pixels, cmap="gray", vmin=vmin, vmax=vmax, interpolation="nearest")
    axes.set_axis_off()
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


def _bins_hint(dtype) -> str:
    if is_integer(dtype):
        top = min(65_536, 2 ** (dtype.itemsize * 8))
        text = f"{dtype.name}: 1 to {top:,}; the default gives an exact histogram for 8/16-bit"
    else:
        text = f"{dtype.name}: 2 to 65,536"
    return f"<small>{html.escape(text)}</small>"


def _coordinates(plane: Plane) -> str:
    parts = [f"{k}={v}" for k, v in plane.coordinates.items() if v is not None]
    return ", ".join(parts) or "single plane"


def _pre(text: str) -> str:
    return f'<pre style="white-space: pre-wrap; margin: 4px 0">{html.escape(text)}</pre>'


def _note(text: str) -> str:
    return f"<p><i>{html.escape(text)}</i></p>"


def _ok(text: str) -> str:
    return f'<p style="color: #2e7d32">{html.escape(text)}</p>'
