"""Plain-language summaries of a preflight, a run and the group statistics.

The command-line interface prints these texts and the notebook shows them, so
both tell the user the same thing. Nothing here reads images or writes files. The
masks have theirs in :mod:`.mask_text`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from morphobricks_normalization.fitting import ChannelScopeIncompleteError
from morphobricks_normalization.histogram import PlaneHistogram
from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import LEVELS, PlaneRecord, group_records
from morphobricks_normalization.settings import PARTIAL_CHANNELS

from .axes import AxisMapping, AxisReview, axis_length_ranges, axis_name
from .dataset import BalanceTable
from .discovery import DiscoveryResult
from .discovery_report import REPORT_FILENAME, REPORT_TEXT_FILENAME
from .run import Preflight, RunResult
from .validation import Severity, ValidationIssue, plural

LEVEL_NAMES = {
    "condition": "condition",
    "biological_repeat": "biological repeat",
    "technical_repeat": "technical repeat",
    "image": "image",
}

# Warnings whose paths are files or folders that will not be analysed.
_SKIPPED_CODES = {
    "non_tiff_file": ("file that is not an image", "files that are not images"),
    "empty_folder": ("empty folder", "empty folders"),
}


def format_issues(issues: Iterable[ValidationIssue]) -> str:
    """Format issues as plain text, errors first, separated by blank lines."""
    issues = sorted(issues, key=lambda issue: not issue.is_error)
    return "\n\n".join(str(issue) for issue in issues)


def issue_counts(issues: Iterable[ValidationIssue]) -> str:
    """Count issues, e.g. ``"1 error, 2 warnings"`` or ``"no problems found"``."""
    counts = Counter(issue.severity for issue in issues)
    if not counts:
        return "no problems found"
    parts = [
        plural(counts[severity], severity.value)
        for severity in (Severity.ERROR, Severity.WARNING)
        if counts[severity]
    ]
    return ", ".join(parts)


def label_tree(discovery: DiscoveryResult) -> str:
    """Show the folders that will be analysed as an indented tree with their labels.

    Each leaf folder shows its number of images and, where levels are missing,
    which labels become ``single``.
    """
    lines = [f"{discovery.root.name}/"]
    printed: set[tuple[str, ...]] = set()
    for leaf in discovery.leaves:
        parts = () if leaf.relative_path == "." else tuple(leaf.relative_path.split("/"))
        for depth in range(1, len(parts)):
            if parts[:depth] not in printed:
                printed.add(parts[:depth])
                lines.append(f"{'    ' * depth}{parts[depth - 1]}/")
        images = plural(len(leaf.images), "image")
        if not parts:
            lines.append(f"    ({images} directly in the experiment folder; all labels: single)")
            continue
        missing = [LEVEL_NAMES[level] for level in LEVELS[len(parts) :]]
        note = f"{images}; {' and '.join(missing)}: single" if missing else images
        lines.append(f"{'    ' * len(parts)}{parts[-1]}/  ({note})")
    return "\n".join(lines)


def balance_rows(balance: BalanceTable) -> list[list[str]]:
    """Turn the balance table into rows of text, with a header row.

    Each cell gives the number of images and, in brackets, of technical-repeat
    folders; ``!`` marks an unbalanced cell and ``-`` a repeat group the
    condition does not have.
    """
    rows = [["biological repeat", *balance.conditions]]
    for group in balance.repeat_groups:
        row = [group]
        for condition in balance.conditions:
            cell = balance.cell(group, condition)
            if cell is None:
                row.append("-")
                continue
            text = f"{cell.image_count:,} ({cell.technical_repeat_count:,})"
            row.append(f"{text} !" if cell.unbalanced else text)
        rows.append(row)
    return rows


def balance_summary(balance: BalanceTable) -> str:
    """Show the images per condition and biological repeat as an aligned table."""
    if not balance.cells:
        return ""
    lines = [
        "Images per condition (columns) and biological repeat (rows), with technical-repeat "
        "folders in brackets; ! marks a number far from the median"
        + (f" of {balance.median:g} images:" if balance.median is not None else ":"),
        format_table(balance_rows(balance)),
    ]
    if balance.not_counted():
        lines.append(balance.not_counted())
    return "\n".join(lines)


def mapping_summary(mapping: AxisMapping, files: Iterable = ()) -> str:
    """Describe each axis on one line: its label, its length across files and its role."""
    ranges = axis_length_ranges(mapping, files)
    lines = []
    for axis, role, (low, high) in zip(mapping.axes, mapping.roles, ranges, strict=True):
        length = "" if high == 0 else (f"{low}" if low == high else f"{low}–{high}")
        size = f", length {length}" if length else ""
        lines.append(f"  {axis_name(axis)}{size} -> {role or 'no role'}")
    return "\n".join(lines)


def axis_check_summary(review: AxisReview) -> str:
    """Say in one line how the axis check went (contract §2), for the CLI.

    The success message names what was checked. With axis errors it says how many images
    cannot be read and that the errors (with the likely cause and the two fixes) are listed;
    with plain stacks, that their roles are recorded as confirmed.
    """
    if review.axis_errors:
        n = plural(len(review.axis_error_files), "image")
        return (
            f"Axes check: {n} cannot be read with these roles (see the errors below): correct "
            "the axes, or save named-axis copies with your roles (the convert command)."
        )
    if review.confirmed_files:
        n = plural(len(review.confirmed_files), "image")
        return (
            f"Axes checked: the roles agree with every header; {n} do not name their axes, "
            "so their roles are recorded as confirmed by you (see the warning)."
        )
    return (
        "Axes checked: every image names its axes in its metadata, and the roles agree with them."
    )


def preflight_summary(pre: Preflight) -> str:
    """Summarise pass 1: what will be analysed, what is skipped, pixel type and axes."""
    discovery, metadata = pre.discovery, pre.metadata
    lines = [f"Experiment folder: {discovery.root}"]
    lines.append(
        f"Images to analyse: {len(metadata.files):,} in "
        f"{plural(len(discovery.leaves), 'leaf folder')}"
    )
    zarr = sum(f.is_ome_zarr for f in metadata.files)
    if zarr:
        lines.append(
            f"Formats: {len(metadata.files) - zarr:,} TIFF, {zarr:,} OME-Zarr "
            "(OME-Zarr axes come from their metadata)"
        )
    if pre.excluded:
        lines.append(f"Excluded at your request: {plural(len(pre.excluded), 'image')}")
    for issue in discovery.issues:
        if issue.code in _SKIPPED_CODES:
            lines.append(f"Skipped: {plural(len(issue.paths), *_SKIPPED_CODES[issue.code])}")
    dtype = metadata.dtype
    lines.append(f"Pixel type: {dtype.name if dtype is not None else 'not one single type'}")
    arrangements = Counter(f.axes for f in metadata.files if f.readable)
    if arrangements:
        described = ", ".join(
            f"{axes} ({plural(n, 'image')})" for axes, n in arrangements.most_common()
        )
        lines.append(f"Axes in the files: {described}")
    if pre.suggested_mapping is not None:
        lines.append("Suggested axis roles (please check):")
        lines.append(mapping_summary(pre.suggested_mapping, metadata.files))
    elif arrangements:
        lines.append("No axis mapping can be suggested: the images' dimensions conflict.")
    lines.append(f"Checks: {issue_counts(pre.issues)}")
    return "\n".join(lines)


def run_summary(result: RunResult) -> str:
    """Summarise pass 2: whether output was written and what was read or kept.

    Parameters
    ----------
    result
        What the run did.

    Returns
    -------
    str
        For a stopped run, what it wrote (the report) and that no table or manifest was
        written.
    """
    if not result.written:
        return _stopped_summary(result)
    lines = [
        f"Wrote {plural(result.plane_count, 'plane')} to {result.csv_path}",
        f"Run manifest: {result.manifest_path}",
    ]
    if result.image_type is not None:
        lines.append(f"Image type: {result.image_type}")
    if result.rebuild_reason:
        lines.append(rebuild_line(result))
    for label, files in (
        ("Read", result.read),
        ("Resumed (rows staged by an interrupted run)", result.resumed),
        ("New since the last run", result.new),
        ("Changed since the last run", result.changed),
        ("Unchanged (rows kept)", () if result.rebuild_reason else result.unchanged),
        ("Removed from the table", result.removed),
        ("Excluded", result.excluded),
    ):
        if files:
            lines.append(f"{label}: {plural(len(files), 'image')}")
    lines.extend(
        f"Changed {c.name}: {c.recorded} -> {c.given} ({c.effect})" for c in result.input_changes
    )
    lines.append(f"Checks: {issue_counts(result.issues)}")
    return "\n".join(lines)


def _stopped_summary(result: RunResult) -> str:
    """Say that a stopped run wrote no table or manifest, and what it did write."""
    written = []
    if result.report_written:
        written.append(f"{REPORT_FILENAME} and {REPORT_TEXT_FILENAME} (the report)")
    lines = [
        f"The run stopped ({issue_counts(result.issues)}): no table or manifest was written.",
        f"Written to {result.output_folder}: {', '.join(written)}."
        if written
        else "Nothing was written.",
        "Resolve the errors and run again.",
    ]
    return "\n".join(lines)


ALL_CHANNELS = None
"""The ``channel`` of a deliberate all-channel aggregate (``pool_channels=True``)."""


@dataclass(frozen=True)
class GroupSummary:
    """Pooled statistics of one channel of one group of XYC planes (issue #104).

    Attributes
    ----------
    labels
        The group's labels, from condition down to the chosen level.
    channel
        The canonical channel ID summarised (``DNA``), or :data:`ALL_CHANNELS` (None) for a
        deliberate aggregate of every channel, asked for with ``pool_channels=True``.
    xyc_plane_count
        XYC planes (rows of the table: an image at one Z and T) in the group, including
        planes without finite pixels and planes without the channel.
    channel_histogram_count
        Channel histograms pooled: the planes that have the channel for one channel, and
        the planes times their channels for an all-channel aggregate.
    pixel_count
        Finite pixels pooled.
    minimum, maximum, mean, std, median
        Pooled statistics; None if the group has no finite pixels in that channel.
    percentiles
        ``{q: value}`` for the requested percentiles.
    planes_without_channel
        The planes of the group that lack the channel (its acquisitions do not list it),
        left out by the user's recorded choice ``partial_channels="planes_with_channel"``;
        0 when every plane has it.
    """

    labels: tuple[str, ...]
    channel: str | None
    xyc_plane_count: int
    channel_histogram_count: int
    pixel_count: int
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    std: float | None = None
    median: float | None = None
    percentiles: dict[float, float] | None = None
    planes_without_channel: int = 0


def group_summaries(
    records: Sequence[PlaneRecord],
    level: str,
    percentiles: Sequence[float] = (1.0, 99.8),
    *,
    pool_channels: bool = False,
    partial_channels: str = "refuse",
) -> list[GroupSummary]:
    """Summarise every channel of every group at ``level``, each channel on its own.

    Distinct channels are different measurements (two fluorescence markers, or the red,
    green and blue of an RGB image), so by default each group has one summary per
    channel ID, IDs sorted, and nothing is pooled across channels (issue #104). Channels are
    matched by canonical ID, never by position in the image (Stage 3: units may list
    different channels, in any order). A group whose planes all lack a channel has no
    summary for it.

    Parameters
    ----------
    records
        Rows of the histogram table.
    level
        ``"condition"``, ``"biological_repeat"``, ``"technical_repeat"`` or
        ``"image"``.
    percentiles
        Percentiles to report, from 0 to 100.
    pool_channels
        True pools every channel of a group into one summary whose ``channel`` is
        :data:`ALL_CHANNELS`, the behaviour before issue #104. Only on explicit request:
        the table labels it as an aggregate of all channels.
    partial_channels
        What to do with a channel only some planes of a group have (partial presence):
        ``"refuse"`` (default) raises; ``"planes_with_channel"`` summarises the planes that
        have it, an explicit choice of the user that each summary records
        (``planes_without_channel``). The values of normalization's
        ``NormalizationSettings.partial_channels``.

    Raises
    ------
    ChannelScopeIncompleteError
        With ``partial_channels="refuse"``, for every channel only some planes of a group
        have (Spec ``A_SCOPE_INCOMPLETE``, "present in k of n planes"); nothing is
        summarised. ``channels`` maps ``"<group>: <channel>"`` to the planes that lack it.
    ValueError
        For an unknown ``partial_channels``.
    """
    if partial_channels not in PARTIAL_CHANNELS:
        raise ValueError(
            f"partial_channels {partial_channels!r} does not exist; choose one of "
            f"{', '.join(PARTIAL_CHANNELS)}."
        )
    groups = group_records(list(records), level)
    if not pool_channels and partial_channels == "refuse":
        _refuse_partial(groups)
    summaries = []
    for key, members in groups.items():
        if pool_channels:
            selections = [(ALL_CHANNELS, channel_histograms(members), 0)]
        else:
            selections = []
            for channel in sorted({c for record in members for c in record.channels}):
                having = [r.channel_histogram(channel) for r in members if r.has_channel(channel)]
                selections.append((channel, having, len(members) - len(having)))
        summaries += [
            _summary(key, channel, len(members), histograms, percentiles, lacking)
            for channel, histograms, lacking in selections
        ]
    return summaries


def _refuse_partial(groups: Mapping[tuple[str, ...], list[PlaneRecord]]) -> None:
    """Raise ``ChannelScopeIncompleteError`` naming every partially present channel."""
    lacking: dict[str, tuple[str, ...]] = {}
    lines = []
    for key, members in groups.items():
        for channel in sorted({c for record in members for c in record.channels}):
            without = tuple(r.plane_id for r in members if not r.has_channel(channel))
            if without:
                name = f"{' / '.join(key)}: {channel}"
                lacking[name] = without
                have = len(members) - len(without)
                lines.append(f"{name} (present in {have} of {len(members)} planes)")
    if lacking:
        raise ChannelScopeIncompleteError(
            "A_SCOPE_INCOMPLETE: some groups have a channel in only some of their planes, so "
            "their statistics would not describe the whole group: "
            + "; ".join(lines)
            + ". Choose a grouping in which every plane of a group has the channel, or "
            "explicitly choose to summarise only the planes that have it "
            "(partial_channels='planes_with_channel'); that choice is shown with the results.",
            lacking,
        )


def _summary(
    labels: tuple[str, ...],
    channel: str | None,
    planes: int,
    histograms: list[PlaneHistogram],
    percentiles: Sequence[float],
    lacking: int = 0,
) -> GroupSummary:
    """Pool ``histograms`` (one channel of a group, or all of them) and summarise them."""
    try:
        pooled = PooledHistogram(histograms)
    except NoValidPixelsError:
        return GroupSummary(
            labels, channel, planes, len(histograms), 0, planes_without_channel=lacking
        )
    return GroupSummary(
        labels=labels,
        channel=channel,
        xyc_plane_count=planes,
        channel_histogram_count=len(histograms),
        pixel_count=pooled.pixel_count,
        minimum=pooled.minimum,
        maximum=pooled.maximum,
        mean=pooled.mean,
        std=pooled.std,
        median=pooled.median,
        percentiles={q: pooled.percentile(q) for q in percentiles},
        planes_without_channel=lacking,
    )


def channel_histograms(records: Iterable[PlaneRecord]) -> list[PlaneHistogram]:
    """Return the histogram of every channel of every row, rows first.

    Only for a deliberate all-channel aggregate (``pool_channels=True``): the default
    statistics keep each channel apart (issue #104).
    """
    return [histogram for record in records for histogram in record.histograms]


def channel_label(channel: str | None) -> str:
    """Return how the tables name a channel: its ID, or ``all channels (pooled)``."""
    return "all channels (pooled)" if channel is ALL_CHANNELS else channel


def group_table(summaries: Sequence[GroupSummary], level: str) -> list[list[str]]:
    """Turn the summaries into rows of text, with a header row, for printing or HTML.

    One row per group and channel: the labels, the ``channel`` (``C0``, or ``all
    channels (pooled)`` for a deliberate aggregate), the ``XYC planes`` and the ``channel
    histograms`` pooled, then the statistics.
    """
    label_columns = [LEVEL_NAMES[name] for name in (*LEVELS, "image")]
    depth = (*LEVELS, "image").index(level) + 1
    qs = next((list(s.percentiles) for s in summaries if s.percentiles), [])
    header = [
        *label_columns[:depth],
        "channel",
        "XYC planes",
        "channel histograms",
        "pixels",
        "min",
        "max",
        "mean",
        "std",
        "median",
        *(f"p{q:g}" for q in qs),
    ]
    rows = [header]
    for s in summaries:
        values = [s.minimum, s.maximum, s.mean, s.std, s.median]
        values += [None if not s.percentiles else s.percentiles[q] for q in qs]
        counts = (s.xyc_plane_count, s.channel_histogram_count, s.pixel_count)
        rows.append(
            [
                *s.labels,
                _channel_cell(s),
                *(f"{n:,}" for n in counts),
                *map(_number, values),
            ]
        )
    return rows


def _channel_cell(summary: GroupSummary) -> str:
    """Return the channel's name, with the planes it covers when the user chose a subset."""
    name = channel_label(summary.channel)
    if not summary.planes_without_channel:
        return name
    return (
        f"{name} (only the {summary.channel_histogram_count:,} of "
        f"{summary.xyc_plane_count:,} planes that have it, as chosen)"
    )


def format_table(rows: Sequence[Sequence[str]]) -> str:
    """Align rows of text in columns."""
    if not rows:
        return ""
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


def _number(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:.6g}"


def rebuild_line(result: RunResult) -> str:
    """Say why no earlier row was kept; a resumed run names the rows it resumed (F11)."""
    if result.resumed:
        return (
            f"No earlier run's rows were kept because {result.rebuild_reason}; the rows "
            f"staged by the interrupted run were resumed ({plural(len(result.resumed), 'image')})"
            " and the others computed."
        )
    return f"All rows were computed because {result.rebuild_reason}."
