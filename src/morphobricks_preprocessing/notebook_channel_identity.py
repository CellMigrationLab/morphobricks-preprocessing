"""The notebook's "Do the channels look like themselves?" view (contract §14.1).

Under the intensity checks: a status line, the findings of :mod:`.channel_identity` with what
to do, and one plot of every unit's channels in (brightness, contrast) space. Nothing is
written; the run records the limits.
"""

from __future__ import annotations

import html
import io
from collections.abc import Sequence

from morphobricks_normalization.summaries import ImageSummary

try:
    import ipywidgets as widgets
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
except ImportError as exc:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The notebook interface needs ipywidgets and matplotlib. Install them with: "
        'pip install "morphobricks-preprocessing[notebook]"'
    ) from exc

from . import notebook_style as ui
from .channel_identity import check_channel_identity
from .channel_identity_model import (
    CHANNEL_IDENTITY_CODES,
    POSSIBLE_CHANNEL_SWAP,
    ChannelFinding,
    ChannelIdentityReport,
    ChannelSignature,
)
from .channel_identity_text import (
    TITLES,
    channel_identity_summary,
    describe_settings,
    not_compared_text,
)
from .run_labels import NO_LABELS, RunLabels

# Categorical hues in a fixed order (validated palette; red is left for nothing), each with
# its own marker so a channel is never told by colour alone.
COLOURS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
MARKERS = ("o", "s", "^", "D", "v", "P", "X")
INK = "#222222"
_CHECK_WORDS = {POSSIBLE_CHANNEL_SWAP: "Possible swap", "channel_unlike_others": "Unlike others"}


class ChannelIdentityView:
    """The channel-identity findings and plot of one table, shown inside the intensity card."""

    def __init__(self) -> None:
        self.report: ChannelIdentityReport | None = None
        self.status = widgets.HTML()
        self.findings = widgets.HTML()
        self.plot = widgets.Image(format="png", layout=widgets.Layout(max_width="760px"))
        self.box = widgets.VBox(
            [
                widgets.HTML("<b>Do the channels look like themselves?</b>"),
                ui.help_text(
                    "Each imaging unit's channels are compared with the same channels of the "
                    "other units of its condition (median, high percentile and contrast, on a "
                    "log scale), so a treatment effect is not reported when the condition has "
                    "at least 3 other units. A channel that looks like another channel may "
                    "have been tagged wrongly. A swap made in every unit of a condition is not "
                    "found: it is what that condition looks like. Warnings only."
                ),
                self.status,
                self.findings,
                self.plot,
            ]
        )
        ui.set_visible(self.box, False)

    def show(self, summaries: Sequence[ImageSummary], labels: RunLabels = NO_LABELS) -> None:
        """Check ``summaries`` and show the findings and the plot."""
        self.report = report = check_channel_identity(summaries)
        lines = [
            channel_identity_summary(report),
            f"Limits: {describe_settings(report.settings)}.",
        ]
        if not report.compared:
            kind, title = "info", "No channel could be checked."
        elif report.findings:
            kind, title = "warn", "Some channels do not look like themselves."
        else:
            kind, title = "ok", "Every channel compared looks like itself."
        self.status.value = ui.status(kind, title, lines)
        self.findings.value = channel_identity_html(report)
        self.plot.value = channel_identity_png(report, labels) or b""
        ui.set_visible(self.box, bool(report.signatures))

    def clear(self) -> None:
        """Hide the view and forget the report."""
        self.report = None
        self.status.value = self.findings.value = ""
        self.plot.value = b""
        ui.set_visible(self.box, False)


def channel_identity_html(report: ChannelIdentityReport) -> str:
    """Render the findings as a table, what to do about each kind, and what was not checked."""
    parts = []
    issues = report.validation_issues()
    if issues:
        rows = [["Check", "Unit", "What was found"]]
        rows += [[_CHECK_WORDS[i.code], ", ".join(i.paths), i.message] for i in issues]
        actions = [
            f"<b>{html.escape(TITLES[code])}</b>: {html.escape(found[0].action)}"
            for code in CHANNEL_IDENTITY_CODES
            if (found := [i for i in issues if i.code == code])
        ]
        parts += [ui.table(rows), ui.status("info", "What to do", actions, raw=True)]
    notes = [f"{n.unit.label}, channel {n.channel}: {n.reason}" for n in report.not_checked]
    note = not_compared_text(report)
    if notes or note:
        parts.append(ui.status("info", "Not checked", [*notes, *([note] if note else [])]))
    return "".join(parts)


def channel_identity_png(
    report: ChannelIdentityReport, labels: RunLabels = NO_LABELS
) -> bytes | None:
    """Plot every unit's channels: brightness against contrast, one point per unit and channel.

    Each channel forms a cluster (colour and marker by channel tag). A flagged point is
    circled and labelled with its unit; a possible swap has a dashed arrow from the middle
    of its own channel's cluster to the point, in the cluster it looks like. One panel per
    pixel type (units of different types are not compared). None without signatures.
    """
    if not report.signatures:
        return None
    types = [dtype for dtype, _ in report.pixel_types]
    channels = sorted({s.channel for s in report.signatures})
    style = {
        c: (COLOURS[i % len(COLOURS)], MARKERS[i % len(MARKERS)]) for i, c in enumerate(channels)
    }
    figure = Figure(
        figsize=(7.6 * len(types) if len(types) > 1 else 8.0, 6.0), layout="constrained"
    )
    for index, dtype in enumerate(types):
        axes = figure.add_subplot(1, len(types), index + 1)
        _draw_type(axes, report, dtype, style)
        if len(types) > 1:
            axes.set_title(f"Pixel type {dtype}", fontsize=9)
    handles = [
        Line2D(
            [],
            [],
            color=colour,
            marker=marker,
            linestyle="",
            markersize=8,
            label=labels.channel(channel),
        )
        for channel, (colour, marker) in style.items()
    ]
    handles += [
        Line2D(
            [],
            [],
            marker="o",
            markersize=15,
            markerfacecolor="none",
            markeredgecolor=INK,
            markeredgewidth=1.6,
            linestyle="",
            label="unit with a warning",
        ),
        Line2D(
            [],
            [],
            color=INK,
            linewidth=1.4,
            linestyle="--",
            label=(
                "possible swap: from its reference (other units of its condition, "
                "or of the experiment when too few)"
            ),
        ),
    ]
    figure.legend(handles=handles, loc="outside lower center", ncols=3, fontsize=8, frameon=False)
    figure.suptitle(
        "Do the channels look like themselves? One point per unit and channel", fontsize=10
    )
    return _png(figure)


def _draw_type(axes: object, report: ChannelIdentityReport, dtype: str, style: dict) -> None:
    """Draw the units of one pixel type: clusters, flagged rings, labels and swap arrows."""
    signatures = [s for s in report.signatures if s.dtype == dtype]
    for channel, (colour, marker) in style.items():
        mine = [s for s in signatures if s.channel == channel]
        if mine:
            axes.scatter(
                [s.bright for s in mine],
                [s.spread for s in mine],
                s=48,
                color=colour,
                marker=marker,
                edgecolors="white",
                linewidths=0.8,
                zorder=3,
            )
    for signature in signatures:
        found = report.finding(signature.unit, signature.channel)
        if found is not None:
            _draw_finding(axes, signature, found, style[signature.channel])
    axes.set_xlabel("Brightness: log of the high percentile", fontsize=9)
    axes.set_ylabel("Contrast: log of (high - low percentile)", fontsize=9)
    axes.tick_params(labelsize=8)
    axes.grid(alpha=0.3)


def _draw_finding(
    axes: object,
    signature: ChannelSignature,
    found: ChannelFinding,
    look: tuple[str, str],
) -> None:
    """Circle and label a flagged point; for a swap, an arrow from the reference it was compared to.

    The arrow starts at the centre of the reference the check used for this unit and channel
    (the other units of its condition, or of the experiment) and ends at the point, which lies
    in or near the cluster of the channel it looks like.
    """
    point = (signature.bright, signature.spread)
    axes.scatter(*point, s=70, color=look[0], marker=look[1], edgecolors=INK, zorder=5)
    axes.scatter(*point, s=320, facecolors="none", edgecolors=INK, linewidths=1.6, zorder=5)
    text = f"{signature.unit.label}\ntagged {signature.channel}"
    if found.resembles is not None:
        text += f", looks like {found.resembles}"
    axes.annotate(
        text,
        point,
        xytext=(14, -18),
        textcoords="offset points",
        fontsize=8,
        color=INK,
        zorder=6,
        bbox={
            "boxstyle": "round,pad=0.25",
            "facecolor": "white",
            "edgecolor": "none",
            "alpha": 0.85,
        },
    )
    if found.code == POSSIBLE_CHANNEL_SWAP:
        start = found.reference[1:]
        # Curved, so the two arrows of a mutual swap do not lie on each other.
        arrow = {"arrowstyle": "-|>", "color": INK, "linewidth": 1.4, "linestyle": "--"}
        arrow["connectionstyle"] = "arc3,rad=0.2"
        axes.annotate("", point, start, zorder=4, arrowprops={**arrow, "shrinkB": 10})


def _png(figure: Figure) -> bytes:
    """Return the figure as PNG bytes."""
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
    return buffer.getvalue()
