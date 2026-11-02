"""HTML for the notebook: issues, labels, axis roles, tiles and help lines."""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence

from morphobricks_normalization.dtypes import MAX_BIN_COUNT, MIN_FLOAT_BIN_COUNT, is_integer

from . import notebook_style as ui
from .axes import AxisMapping
from .dataset import BalanceTable
from .design import ExperimentDesign
from .notebook_options import _SKIPPED_CODES
from .notebook_values import plane_counts, value_range
from .planes import Plane
from .run import Preflight
from .run.recorded import InputChange
from .validation import ValidationIssue, has_errors, plural


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


def issue_count_chips(issues: Sequence[ValidationIssue]) -> str:
    """Red error and orange warning counts, shown outside a folded list of issues."""
    errors = [i for i in issues if i.is_error]
    warnings = [i for i in issues if not i.is_error]
    return "".join(
        f"<span class='mb-issue-count mb-{kind}'>{plural(len(items), name)}</span>"
        for kind, items, name in (("error", errors, "error"), ("warn", warnings, "warning"))
        if items
    )


def change_lines(changes: Iterable[InputChange]) -> list[str]:
    """Name each input that differs from the earlier run, with its effect (HTML lines)."""
    return [
        f"<b>{html.escape(change.name)}</b>: {html.escape(change.recorded)} → "
        f"{html.escape(change.given)}. Effect: {html.escape(change.effect)}."
        for change in changes
    ]


def balance_html(balance: BalanceTable) -> str:
    """Render images per condition and biological repeat; unbalanced or missing cells in amber."""
    if not balance.cells:
        return ""
    header = "".join(
        f"<th>{html.escape(h)}</th>" for h in ("Biological repeat", *balance.conditions)
    )
    rows = []
    for group in balance.repeat_groups:
        cells = [f"<td><b>{html.escape(group)}</b></td>"]
        for condition in balance.conditions:
            cell = balance.cell(group, condition)
            if cell is None:
                cells.append("<td class='mb-cell-warn'>not found</td>")
                continue
            technical = (
                plural(cell.technical_repeat_count, "technical repeat")
                if cell.technical_repeat_count
                else "no technical-repeat folders"
            )
            css = " class='mb-cell-warn'" if cell.unbalanced else ""
            cells.append(
                f"<td{css}><b>{plural(cell.image_count, 'image')}</b>"
                f"<div class='mb-help' style='margin:0'>{technical}</div></td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    median = (
        f"<div class='mb-help'>Median: {balance.median:g} images per condition and "
        "biological repeat.</div>"
        if balance.median is not None
        else ""
    )
    return (
        "<div class='mb-table-wrap'><table class='mb-table mb-labels'>"
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>" + median
    )


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
    counts = plane_counts(mapping, ranges)
    if counts is None:
        return ""
    low, high = counts
    lengths = dict(zip(mapping.roles, ranges, strict=True))
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
    found = value_range(pixels)
    values = f"values {found[0]:g} to {found[1]:g}" if found else "no finite values"
    height, width = plane.shape
    reduced = ""
    if plane.step > 1:
        # Shown values come from the kept pixels only, so the range may be narrower.
        values = values.replace("values", "shown values", 1)
        reduced = (
            f"<br>This plane is too large for the memory limit, so it is read in blocks and "
            f"shown reduced: 1 pixel in {plane.step} along each side "
            f"({pixels.shape[0]} × {pixels.shape[1]} px shown)."
        )
    return (
        f"<div class='mb-help'><b>{html.escape(relative_path)}</b> · "
        f"{html.escape(coords or 'single plane')} · {height} × {width} px · "
        f"{pixels.dtype.name}, {values}{reduced}</div>"
    )


def _labels_explanation(show_design: bool) -> str:
    """Return what to check before confirming the labels; step 3 is named only with the design."""
    text = (
        "Check that every image folder has the right condition, biological repeat and "
        "technical repeat. If a label is wrong, rename the folder and click <b>Check "
        "folder</b> again."
    )
    return (text + " Step 3 can only run once the labels are confirmed.") if show_design else text


def _group_question(design: ExperimentDesign, group: str) -> str:
    """Return the question asked for a repeat group, or for the whole experiment."""
    if design.single_repeat:
        return (
            "All images of every condition come from one biological repeat: were they "
            "prepared and acquired under the same circumstances?"
        )
    return (
        f"Repeat group {group}: were these images prepared and acquired under the same "
        "circumstances, so they are comparable with each other?"
    )


def _group_explanation(design: ExperimentDesign, group: str) -> str:
    """Describe the folders of a repeat group and anything unusual about it (HTML)."""
    items = [
        f"<b>{html.escape(row.condition)}</b>: {html.escape(row.biological_repeat)} "
        f"({plural(row.image_count, 'image')})"
        for row in design.members(group)
    ]
    text = "Folders: " + "; ".join(items) + "."
    missing = design.missing_conditions(group)
    if missing and not design.single_repeat:
        text += (
            "<br><span style='color:#92400E'>Not found in "
            + ", ".join(html.escape(c) for c in missing)
            + ": it is still used, with the conditions that have it.</span>"
        )
    if group in design.conflicts:
        text += (
            "<br><span style='color:#991B1B'>This group holds more than one folder of "
            + ", ".join(html.escape(c) for c in design.conflicts[group])
            + ". Move one of them into another group above before confirming.</span>"
        )
    return text + " If they are not comparable, process them as separate experiments."


def _folder_tiles(pre: Preflight, issues: Sequence[ValidationIssue]) -> list[tuple[str, str, str]]:
    """Return the headline numbers of a folder check."""
    skipped = sum(len(i.paths) for i in pre.discovery.issues if i.code in _SKIPPED_CODES)
    conditions = {leaf.condition for leaf in pre.discovery.leaves}
    dtype = pre.metadata.dtype
    errors = sum(i.is_error for i in issues)
    warnings = len(issues) - errors
    return [
        ("🖼️", f"{len(pre.metadata.files):,}", "images to analyse"),
        ("📂", f"{len(pre.discovery.leaves):,}", "image folders"),
        ("🧪", f"{len(conditions):,}", "conditions"),
        ("🔢", dtype.name if dtype is not None else "mixed", "pixel type"),
        ("🗂️", f"{sum(f.is_ome_zarr for f in pre.metadata.files):,}", "OME-Zarr images"),
        ("⏭️", f"{skipped:,}", "files or folders skipped"),
        (
            "⛔" if errors else "⚠️" if warnings else "✅",
            f"{errors} / {warnings}",
            "errors / warnings",
        ),
    ]


def _bins_help(dtype) -> str:  # noqa: ANN001
    """Say which bin counts the pixel type allows."""
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
