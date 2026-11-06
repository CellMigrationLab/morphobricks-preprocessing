"""The notebook's plots (PNG images) and the intensity-checks panel."""

from __future__ import annotations

import html
import io
import math
from collections.abc import Sequence

import numpy as np
from morphobricks_normalization.checks import IntensityCheckSettings
from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import PlaneRecord

try:
    import ipywidgets as widgets
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The notebook interface needs ipywidgets and matplotlib. Install them with: "
        'pip install "morphobricks-preprocessing[notebook]"'
    ) from exc

from . import notebook_style as ui
from .intensity import (
    CHECK_TITLES,
    INTENSITY_CODES,
    IntensityReport,
    RepeatBox,
    check_intensities,
    cumulative_share,
    describe_settings,
    intensity_summary,
    repeat_boxes,
    repeat_distributions,
)
from .notebook_options import _CHECK_LABELS, _MAX_PLOTTED_GROUPS
from .notebook_render import _label
from .notebook_values import display_range
from .planes import Plane
from .validation import plural


class IntensityPanel:
    """The intensity checks of one histogram table: warnings, limits and plots (follow-up P5).

    Shown in step 3 for the table just written and in step 4 for any table.
    The checks come from :func:`.intensity.check_intensities`; the plots are
    drawn per channel and nothing is written.

    Parameters
    ----------
    title
        Card header.
    explanation
        Help line under it (HTML).
    """

    def __init__(self, title: str, explanation: str):  # noqa: ANN204
        self.records: list[PlaneRecord] = []
        self.report: IntensityReport | None = None
        self._used: tuple[tuple, IntensityCheckSettings] | None = None
        self._build_settings_box()
        self.status = widgets.HTML()
        self.tiles = widgets.HTML()
        self.findings = widgets.HTML()
        self._build_plots()
        self.card = ui.card(
            title,
            [
                ui.help_text(explanation),
                self.status,
                self.tiles,
                self.findings,
                self.settings_box,
                self.plots,
            ],
            visible=False,
        )
        for control in (self.outlier_limit, self.saturation_percent, self.min_images):
            control.observe(lambda _: self._settings_changed(), names="value")
        self.defaults_button.on_click(lambda _: self.restore_defaults())
        self.channel.observe(lambda _: self._draw(), names="value")

    def _build_settings_box(self) -> None:
        """Build the folded "Check settings": the three limits and "Restore the defaults"."""
        self._build_limits()
        self.defaults_button = ui.button(
            " Restore the defaults",
            style="warning",
            icon="undo",
            tooltip="3 robust deviations, 1 % saturated pixels, at least 5 images",
        )
        self.settings_box = ui.accordion(
            "Check settings",
            [
                widgets.VBox(
                    [
                        self.outlier_limit,
                        ui.help_text(
                            "How far, in robust deviations (the spread of the others, "
                            "insensitive to single outliers), an image's median or high "
                            "percentile may be from the other images of its condition and "
                            "biological repeat, or a biological repeat from the other repeats "
                            "of its condition. Default 3; larger values report fewer."
                        ),
                        self.saturation_percent,
                        ui.help_text(
                            "Share of an image's pixels at the largest value of its pixel type "
                            "(for example 255 in 8-bit images) above which it is reported as "
                            "saturated. Default 1 %."
                        ),
                        self.min_images,
                        ui.help_text(
                            "Comparisons with fewer images are skipped, since a few images say "
                            "little about what is usual. Default 5."
                        ),
                        self.defaults_button,
                    ]
                )
            ],
        )

    def _build_limits(self) -> None:
        """Build the three limits of the checks, at their default values."""
        defaults = IntensityCheckSettings()
        self.outlier_limit = widgets.BoundedFloatText(
            value=defaults.outlier_limit,
            min=0.5,
            max=100,
            step=0.5,
            description="Outlier limit",
            style=_label(),
        )
        self.saturation_percent = widgets.BoundedFloatText(
            value=100 * defaults.saturation_limit,
            min=0,
            max=99.9,
            step=0.5,
            description="Saturated pixels (%)",
            style=_label(),
        )
        self.min_images = widgets.BoundedIntText(
            value=defaults.min_images,
            min=2,
            max=100_000,
            description="Fewest images",
            style=_label(),
        )

    def _build_plots(self) -> None:
        """Build the channel choice and the two plots of the chosen channel."""
        self.channel = widgets.ToggleButtons(options=[("C0", 0)], style={"button_width": "64px"})
        self.channel_row = widgets.VBox(
            [
                widgets.HTML("<b>Channel</b>"),
                self.channel,
                ui.help_text("The plots show one channel at a time."),
            ]
        )
        self.boxes = widgets.Image(format="png", layout=widgets.Layout(max_width="760px"))
        self.curves = widgets.Image(format="png", layout=widgets.Layout(max_width="760px"))
        self.plots = widgets.VBox(
            [
                self.channel_row,
                widgets.HTML("<b>Image medians and high percentiles</b>"),
                ui.help_text(
                    "One box per condition and biological repeat, one point per image. Red "
                    "diamonds are images with a warning in this channel; an amber box is a "
                    "biological repeat shifted from the others of its condition."
                ),
                self.boxes,
                widgets.HTML("<b>Intensity distributions per biological repeat</b>"),
                ui.help_text(
                    "One panel per condition, one curve per biological repeat: the share of "
                    "pixels at or below each intensity. Curves of comparable repeats lie close "
                    "together; a dashed curve is a shifted repeat."
                ),
                self.curves,
            ]
        )

    def settings(self) -> IntensityCheckSettings:
        """Return the limits as currently set."""
        shown = (self.outlier_limit.value, self.saturation_percent.value, self.min_images.value)
        # The limits set by :meth:`use`, exactly, while unedited: a fraction shown as a
        # percentage does not always come back as the same float.
        if self._used is not None and shown == self._used[0]:
            return self._used[1]
        return IntensityCheckSettings(
            outlier_limit=self.outlier_limit.value,
            saturation_limit=self.saturation_percent.value / 100,
            min_images=self.min_images.value,
        )

    def use(self, settings: IntensityCheckSettings) -> None:
        """Show ``settings`` as the limits, e.g. those an earlier run recorded."""
        self.outlier_limit.value = settings.outlier_limit
        self.saturation_percent.value = 100 * settings.saturation_limit
        self.min_images.value = settings.min_images
        shown = (self.outlier_limit.value, self.saturation_percent.value, self.min_images.value)
        self._used = (shown, settings)

    def restore_defaults(self) -> None:
        """Set the default limits again."""
        self.use(IntensityCheckSettings())

    def show(self, records: Sequence[PlaneRecord]) -> None:
        """Check the intensities of ``records`` and show the warnings and plots."""
        self.records = list(records)
        channels = self.records[0].channel_count if self.records else 1
        with self.channel.hold_trait_notifications():
            self.channel.options = [(f"C{c}", c) for c in range(channels)]
            self.channel.value = 0
        ui.set_visible(self.channel_row, channels > 1)
        self._check()
        ui.set_visible(self.card, True)

    def clear(self) -> None:
        """Hide the panel and forget the table."""
        self.records, self.report = [], None
        self.status.value = self.tiles.value = self.findings.value = ""
        self.boxes.value = self.curves.value = b""
        ui.set_visible(self.card, False)

    def fail(self, exc: Exception) -> None:
        """Show that the checks could not be made."""
        self.clear()
        self.status.value = ui.status(
            "error",
            "The intensity checks could not be made.",
            [f"{type(exc).__name__}: {exc}", "Open Technical details for the full report."],
        )
        ui.set_visible(self.plots, False)
        ui.set_visible(self.card, True)

    def _settings_changed(self) -> None:
        if self.records:
            self._check()

    def _check(self) -> None:
        """Run the intensity checks with the limits shown and show their findings."""
        self.report = check_intensities(self.records, self.settings())
        report = self.report
        lines = [
            intensity_summary(report),
            f"Limits: {describe_settings(report.settings)} (change them under Check settings).",
        ]
        if report.issues:
            self.status.value = ui.status(
                "warn",
                f"{plural(len(report.issues), 'intensity warning')} to read.",
                lines,
            )
        else:
            self.status.value = ui.status("ok", "No intensity warnings.", lines)
        counts = {code: 0 for code in INTENSITY_CODES}
        for issue in report.issues:
            counts[issue.code] += 1
        self.tiles.value = ui.tiles(
            [
                ("🖼️", f"{len(report.summaries):,}", "images checked"),
                ("🎯", f"{counts['image_intensity_outlier']:,}", "image outliers"),
                ("📈", f"{counts['repeat_intensity_shift']:,}", "shifted repeats"),
                ("☀️", f"{counts['saturated_image']:,}", "saturated"),
                ("➖", f"{counts['no_intensity_spread']:,}", "without spread"),
                ("❔", f"{counts['channel_without_finite_pixels']:,}", "channels not checked"),
            ]
        )
        self.findings.value = intensity_findings_html(report)
        ui.set_visible(self.plots, bool(report.summaries))
        self._draw()

    def _draw(self) -> None:
        """Draw the plots of the chosen channel."""
        report = self.report
        if report is None or not report.summaries:
            self.boxes.value = self.curves.value = b""
            return
        channel = self.channel.value or 0
        boxes = repeat_boxes(report, channel)
        self.boxes.value = intensity_boxes_png(boxes, report.high_percentile, channel) or b""
        curves = repeat_distributions(self.records, channel)
        self.curves.value = (
            repeat_curves_png(curves, report.shifted_repeats(channel), channel) or b""
        )


def intensity_findings_html(report: IntensityReport) -> str:
    """Render the intensity warnings as a table, then what to do about each kind."""
    if not report.issues:
        return ""
    rows = [["Check", "Channel", "Image or biological repeat", "What was found"]]
    for issue in report.issues:
        rows.append(
            [_CHECK_LABELS[issue.code], f"C{issue.channel}", ", ".join(issue.items), issue.message]
        )
    actions = []
    for code in INTENSITY_CODES:
        found = [issue for issue in report.issues if issue.code == code]
        if found:
            actions.append(
                f"<b>{html.escape(CHECK_TITLES[code])}</b>: {html.escape(found[0].action)}"
            )
    return ui.table(rows) + ui.status("info", "What to do", actions, raw=True)


def intensity_boxes_png(
    boxes: Sequence[RepeatBox], high_percentile: float, channel: int
) -> bytes | None:
    """Box plots of the image medians and high percentiles per condition and biological repeat.

    Images with a warning in the channel are red diamonds; a biological repeat
    reported as shifted has an amber box.
    """
    if not boxes:
        return None
    n = len(boxes)
    figure = Figure(figsize=(max(7.2, 0.6 * n + 2.5), 5.6))
    pair = figure.subplots(2, 1, sharex=True)
    rng = np.random.default_rng(0)  # the same jitter on every redraw
    jitter = [rng.uniform(-0.15, 0.15, len(box.paths)) for box in boxes]
    statistics = (
        ("Image median", lambda box: box.medians),
        (f"Image {high_percentile:g}th percentile", lambda box: box.highs),
    )
    for axes, (name, values_of) in zip(pair, statistics, strict=True):
        parts = axes.boxplot(
            [values_of(box) for box in boxes],
            positions=list(range(n)),
            widths=0.6,
            patch_artist=True,
            showfliers=False,
            # Dark, so the median line is not taken for the amber of a shifted repeat.
            medianprops={"color": "#333333"},
        )
        for patch, box in zip(parts["boxes"], boxes, strict=True):
            patch.set_facecolor("#f6c453" if box.shifted else "#dbe7f5")
            patch.set_edgecolor("#555555")
        for i, box in enumerate(boxes):
            values = np.asarray(values_of(box), dtype=float)
            flagged = np.array([bool(codes) for codes in box.flagged])
            x = i + jitter[i]
            axes.scatter(x[~flagged], values[~flagged], s=12, color="#3b6ea8", zorder=3)
            axes.scatter(x[flagged], values[flagged], s=40, color="#d62728", marker="D", zorder=4)
            if i and box.condition != boxes[i - 1].condition:
                axes.axvline(i - 0.5, color="#999999", linestyle="--", linewidth=0.8)
        axes.set_ylabel(name, fontsize=9)
        axes.grid(axis="y", alpha=0.3)
    pair[1].set_xticks(
        list(range(n)), [box.label for box in boxes], rotation=30, ha="right", fontsize=8
    )
    pair[0].set_title(f"Channel C{channel}: one point per image", fontsize=10)
    pair[0].legend(
        handles=[
            Line2D([], [], color="#d62728", marker="D", linestyle="", label="image with a warning"),
            Patch(facecolor="#f6c453", edgecolor="#555555", label="shifted biological repeat"),
        ],
        fontsize=8,
        frameon=False,
        loc="best",
    )
    figure.tight_layout()
    return _png(figure)


def repeat_curves_png(curves: dict, shifted: set[tuple[str, str]], channel: int) -> bytes | None:
    """Overlay the cumulative intensity distribution of each biological repeat, per condition.

    ``curves`` is :func:`.intensity.repeat_distributions`; shifted repeats are
    dashed. At most 12 conditions are drawn.
    """
    conditions = list(curves)[:_MAX_PLOTTED_GROUPS]
    if not conditions:
        return None
    pooled = [p for condition in conditions for p in curves[condition].values()]
    low = min(p.percentile(0.1) for p in pooled)
    high = max(p.percentile(99.9) for p in pooled)
    columns = min(3, len(conditions))
    rows = math.ceil(len(conditions) / columns)
    figure = Figure(figsize=(3.7 * columns + 0.5, 2.9 * rows + 0.6))
    for index, condition in enumerate(conditions):
        axes = figure.add_subplot(rows, columns, index + 1)
        for repeat, histogram in curves[condition].items():
            dashed = (condition, repeat) in shifted
            axes.step(
                histogram.values,
                cumulative_share(histogram),
                where="post",
                linewidth=1.4,
                linestyle="--" if dashed else "-",
                label=f"{repeat} (shifted)" if dashed else repeat,
            )
        if high > low:
            axes.set_xlim(low, high)
        axes.set_ylim(0, 1)
        axes.set_title(condition, fontsize=9)
        axes.set_xlabel("Intensity", fontsize=8)
        if index % columns == 0:
            axes.set_ylabel("Share of pixels at or below", fontsize=8)
        axes.tick_params(labelsize=7)
        axes.grid(alpha=0.3)
        axes.legend(fontsize=7, frameon=False, loc="lower right")
    title = f"Channel C{channel}: one curve per biological repeat"
    if len(curves) > _MAX_PLOTTED_GROUPS:
        title += f" (first {_MAX_PLOTTED_GROUPS} of {len(curves)} conditions)"
    figure.suptitle(title, fontsize=10)
    figure.tight_layout()
    return _png(figure)


def _png(figure: Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100)
    return buffer.getvalue()


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
    """Plot the cumulative intensity distribution of each group, one panel per channel.

    Channels are never pooled together (issue #104): each panel holds one channel's
    curves, one per group (up to 12 groups), each with its own intensity axis.
    """
    shown = list(groups.items())[:_MAX_PLOTTED_GROUPS]
    channels = max((r.channel_count for _, members in shown for r in members), default=0)
    panels = []
    for channel in range(channels):
        curves = []
        for key, members in shown:
            try:
                pooled = PooledHistogram([r.channel_histogram(channel) for r in members])
            except NoValidPixelsError:
                continue
            label = " / ".join(key[-2:]) if len(key) > 1 else key[0]
            curves.append((label, pooled, cumulative_share(pooled)))
        if curves:
            panels.append((channel, curves))
    if not panels:
        return None
    figure = Figure(figsize=(7.2, 3.4 * len(panels)))
    for index, (channel, curves) in enumerate(panels):
        axes = figure.add_subplot(len(panels), 1, index + 1)
        low = min(p.percentile(0.1) for _, p, _ in curves)
        high = max(p.percentile(99.9) for _, p, _ in curves)
        for label, pooled, cumulative in curves:
            axes.step(pooled.values, cumulative, where="post", label=label, linewidth=1.4)
        if high > low:
            axes.set_xlim(low, high)
        axes.set_ylim(0, 1)
        axes.set_xlabel(f"Intensity, channel C{channel}")
        axes.set_ylabel("Share of pixels at or below")
        axes.grid(alpha=0.3)
        axes.legend(fontsize=8, loc="lower right", frameon=False)
        title = f"Channel C{channel}"
        if len(groups) > _MAX_PLOTTED_GROUPS:
            title += f": first {_MAX_PLOTTED_GROUPS} of {len(groups)} groups"
        axes.set_title(title, fontsize=9)
    figure.tight_layout()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100)
    return buffer.getvalue()
