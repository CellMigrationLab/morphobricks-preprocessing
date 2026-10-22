"""Plain-language summaries of a preflight, a run and the group statistics.

The command-line interface prints these texts and the notebook shows them, so
both tell the user the same thing. Nothing here reads images or writes files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import LEVELS, PlaneRecord, group_records

from .axes import AxisMapping, axis_length_ranges, axis_name
from .discovery import DiscoveryResult
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
    "tiff_outside_leaf": ("image outside leaf folders", "images outside leaf folders"),
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


def mapping_summary(mapping: AxisMapping, files: Iterable = ()) -> str:
    """Describe each axis on one line: its label, its length across files and its role."""
    ranges = axis_length_ranges(mapping, files)
    lines = []
    for axis, role, (low, high) in zip(mapping.axes, mapping.roles, ranges, strict=True):
        length = "" if high == 0 else (f"{low}" if low == high else f"{low}–{high}")
        size = f", length {length}" if length else ""
        lines.append(f"  {axis_name(axis)}{size} -> {role or 'no role'}")
    return "\n".join(lines)


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
    """Summarise pass 2: whether output was written and what was read or kept."""
    if not result.written:
        return (
            f"Nothing was written ({issue_counts(result.issues)}). Resolve the errors "
            "and run again."
        )
    lines = [
        f"Wrote {plural(result.plane_count, 'plane')} to {result.csv_path}",
        f"Run manifest: {result.manifest_path}",
    ]
    if result.rebuild_reason:
        lines.append(f"All rows were computed because {result.rebuild_reason}.")
    for label, files in (
        ("Read", result.read),
        ("New since the last run", result.new),
        ("Changed since the last run", result.changed),
        ("Unchanged (rows kept)", () if result.rebuild_reason else result.unchanged),
        ("Removed from the table", result.removed),
        ("Excluded", result.excluded),
        ("TIFF images converted to OME-Zarr", result.converted),
    ):
        if files:
            lines.append(f"{label}: {plural(len(files), 'image')}")
    lines.append(f"Checks: {issue_counts(result.issues)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class GroupSummary:
    """Pooled statistics of one group of planes.

    Attributes
    ----------
    labels
        The group's labels, from condition down to the chosen level.
    plane_count
        Planes in the group, including planes without finite pixels.
    pixel_count
        Finite pixels pooled.
    minimum, maximum, mean, std, median
        Pooled statistics; None if the group has no finite pixels.
    percentiles
        ``{q: value}`` for the requested percentiles.
    """

    labels: tuple[str, ...]
    plane_count: int
    pixel_count: int
    minimum: float | None = None
    maximum: float | None = None
    mean: float | None = None
    std: float | None = None
    median: float | None = None
    percentiles: dict[float, float] | None = None


def group_summaries(
    records: Sequence[PlaneRecord],
    level: str,
    percentiles: Sequence[float] = (1.0, 99.8),
) -> list[GroupSummary]:
    """Pool the planes of every group at ``level`` and summarise them.

    Parameters
    ----------
    records
        Rows of the histogram table.
    level
        ``"condition"``, ``"biological_repeat"``, ``"technical_repeat"`` or
        ``"image"``.
    percentiles
        Percentiles to report, from 0 to 100.
    """
    summaries = []
    for key, members in group_records(list(records), level).items():
        try:
            pooled = PooledHistogram(members)
        except NoValidPixelsError:
            summaries.append(GroupSummary(key, len(members), 0))
            continue
        summaries.append(
            GroupSummary(
                labels=key,
                plane_count=len(members),
                pixel_count=pooled.pixel_count,
                minimum=pooled.minimum,
                maximum=pooled.maximum,
                mean=pooled.mean,
                std=pooled.std,
                median=pooled.median,
                percentiles={q: pooled.percentile(q) for q in percentiles},
            )
        )
    return summaries


def group_table(summaries: Sequence[GroupSummary], level: str) -> list[list[str]]:
    """Turn the summaries into rows of text, with a header row, for printing or HTML."""
    label_columns = [LEVEL_NAMES[name] for name in (*LEVELS, "image")]
    depth = (*LEVELS, "image").index(level) + 1
    qs = next((list(s.percentiles) for s in summaries if s.percentiles), [])
    header = [
        *label_columns[:depth],
        "planes",
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
        rows.append([*s.labels, f"{s.plane_count:,}", f"{s.pixel_count:,}", *map(_number, values)])
    return rows


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
