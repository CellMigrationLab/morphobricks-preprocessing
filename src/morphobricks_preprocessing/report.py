"""Plain-language summaries of a preflight, a run, the group statistics and the masks.

The command-line interface prints these texts and the notebook shows them, so
both tell the user the same thing. Nothing here reads images or writes files.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from morphobricks_normalization.histogram import PlaneHistogram
from morphobricks_normalization.pooling import NoValidPixelsError, PooledHistogram
from morphobricks_normalization.records import LEVELS, PlaneRecord, group_records

from .axes import AxisMapping, AxisReview, axis_length_ranges, axis_name
from .dataset import BalanceTable
from .design import ExperimentDesign
from .discovery import DiscoveryResult
from .mask_scan import DEFAULT_THRESHOLDS, MaskReport, PairRelation
from .masks import MaskInventory
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
    return "\n".join(lines)


def design_summary(design: ExperimentDesign) -> str:
    """Describe the repeat groups, their folders and confirmation, and the controls."""
    lines = []
    for group in design.groups:
        folders = ", ".join(
            f"{row.condition}/{row.biological_repeat}" for row in design.members(group)
        )
        if group in design.conflicts:
            state = "more than one folder of " + ", ".join(design.conflicts[group])
        elif design.group_confirmed(group):
            on = design.confirmed_on(group)
            state = f"confirmed{f' on {on}' if on else ''}"
        else:
            state = "not confirmed"
        lines.append(f"  {group}: {folders} ({state})")
    controls = ", ".join(design.controls) or "none marked"
    lines.append(f"  Controls: {controls}")
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
            "the axes, or save named-axis copies with 'convert'."
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
    if result.image_type is not None:
        lines.append(f"Image type: {result.image_type}")
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
        Planes in the group, each channel of an XYC plane counted as one plane,
        including planes without finite pixels.
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
        histograms = channel_histograms(members)
        try:
            pooled = PooledHistogram(histograms)
        except NoValidPixelsError:
            summaries.append(GroupSummary(key, len(histograms), 0))
            continue
        summaries.append(
            GroupSummary(
                labels=key,
                plane_count=len(histograms),
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


def channel_histograms(records: Iterable[PlaneRecord]) -> list[PlaneHistogram]:
    """Return the histogram of every channel of every row, rows first.

    The group statistics pool every channel of a group together, as they did
    when the table had one row per channel (schema version 2), so the table and
    plot of step 4 are unchanged.
    """
    return [histogram for record in records for histogram in record.histograms]


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


_MASK_KINDS = {
    "instances": "one object per label",
    "grouped": "each label in several pieces (for example organelles grouped by cell)",
    "one_value": "one value per image (a binary mask or a single object)",
    "empty": "no objects",
}
_RELATIONS = {
    "inside": "inside",
    "grouped_by": "grouped by the labels of",
    "same_objects": "the same objects as",
}


def mask_summary(masks: MaskInventory) -> str:
    """Summarise the masks found: each mask type and how many images have it."""
    if not masks.types:
        return "Masks: none found."
    lines = [
        f"Masks: {plural(len(masks.types), 'mask type')} for {plural(len(masks.images), 'image')}:"
    ]
    for name in masks.types:
        found = [f for f in masks.files if f.mask_type == name]
        sources = " and ".join(sorted({f.source for f in found}))
        lines.append(f"  {name}: {plural(len(found), 'image')} ({sources})")
    return "\n".join(lines)


def mask_report_summary(report: MaskReport) -> str:
    """Describe the mask types, their relationships and the hierarchy in plain words.

    Every statement is a suggestion from the masks' pixels; the text ends by asking the
    user to name the masks and check the relationships.
    """
    lines = [f"Mask types ({plural(report.images, 'image')} scanned):"]
    for name, summary in sorted(report.types.items()):
        lines.append(
            f"  {name}: {_MASK_KINDS[summary.kind]}; {plural(summary.objects, 'object')}, "
            f"median {summary.median_pieces_per_label:g} piece(s) per label"
        )
    relationships = [pair for pair in report.relationships if pair.relation != "same_objects"]
    if relationships:
        lines += ["", "Relationships found:"]
        lines += [_relationship_line(pair) for pair in relationships]
    if report.same_objects:
        lines += ["", "Masks that look like two segmentations of the same objects:"]
        lines += [f"  {first} and {second}" for first, second in report.same_objects]
    lines += ["", "Hierarchy (direct parents only):"]
    lines += _hierarchy_lines(report.hierarchy)
    changed = [
        f"{name} {value:g}"
        for name, value in report.thresholds.items()
        if value != DEFAULT_THRESHOLDS[name]
    ]
    used = ", ".join(changed) + "; the others the defaults" if changed else "the defaults"
    lines += ["", f"Thresholds: {used}"]
    lines += [
        "",
        "These are suggestions from the masks' pixels: name each mask, then check the "
        "relationships before using them.",
    ]
    return "\n".join(lines)


def _relationship_line(pair: PairRelation) -> str:
    """One relationship with its evidence, e.g. how many parents have 0, 1, 2, 3+ children."""
    e = pair.evidence
    text = (
        f"  {pair.child} {_RELATIONS[pair.relation]} {pair.parent}"
        f"{'' if pair.direct else ' (through another mask)'}: "
        f"{e['inside']} of {plural(e['children'], 'object')} inside; "
        f"{pair.parent} with 0/1/2/3+ {pair.child}: {e['parents_with_0_children']}/"
        f"{e['parents_with_1_child']}/{e['parents_with_2_children']}/"
        f"{e['parents_with_3_or_more_children']}"
    )
    extra = []
    if e["outside_parents"]:
        extra.append(f"{e['outside_parents']} outside every {pair.parent}")
    if e["split_between_parents"]:
        extra.append(f"{e['split_between_parents']} split between {pair.parent}")
    return text + (f"; {', '.join(extra)}" if extra else "")


def _hierarchy_lines(hierarchy) -> list[str]:
    """Return the mask types as an indented tree; a type with two parents shows twice."""
    children: dict[str, list[str]] = {name: [] for name in hierarchy}
    for name, parents in hierarchy.items():
        for parent in parents:
            children[parent].append(name)
    lines: list[str] = []

    def show(name: str, depth: int) -> None:
        lines.append(f"{'    ' * (depth + 1)}{name}")
        for child in sorted(children[name]):
            show(child, depth + 1)

    for name in sorted(n for n, parents in hierarchy.items() if not parents):
        show(name, 0)
    return lines
