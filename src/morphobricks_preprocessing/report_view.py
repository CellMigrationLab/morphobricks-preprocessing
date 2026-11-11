"""The check's or run's report as a formatted page: headings, tables, details folded (pure).

Owner (11 November 2026): the report shown as plain text was hard to read. This module turns
the report's data (``discovery_report.json``, :mod:`.discovery_report`) into HTML: a short
summary table, then one heading per part with a table, long lists folded behind a line that
says how many there are, and each message of the checks folded behind its first sentence.
Nothing is computed or left out: every value is the report's, and the plain text of
``discovery_report.txt`` is the last folded part.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Mapping, Sequence

from .validation import plural

SUMMARY_CHARS = 110
"""Characters of a message shown before it is unfolded."""


def _e(value: object) -> str:
    return html.escape(str(value))


def _table(rows: Sequence[Sequence[object]], numeric_from: int | None = None) -> str:
    """Return a table whose first row is the header; cells are escaped here."""
    head = "".join(f"<th>{_e(cell)}</th>" for cell in rows[0])
    body = ""
    for row in rows[1:]:
        cells = "".join(
            f"<td{' class=mb-num' if numeric_from is not None and i >= numeric_from else ''}>"
            f"{_e(cell)}</td>"
            for i, cell in enumerate(row)
        )
        body += f"<tr>{cells}</tr>"
    return (
        f"<div class='mb-table-wrap'><table class='mb-table'><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _fold(summary: str, body: str) -> str:
    """Return ``body`` folded behind ``summary`` (both HTML)."""
    return f"<details class='mb-about'><summary>{summary}</summary>{body}</details>"


def _heading(text: str) -> str:
    return f"<div class='mb-header' style='margin-top:10px'>{_e(text)}</div>"


def _source(sizes: object, role: str) -> str:
    """Return where an image's size along ``role`` comes from, as the report words it."""
    size = sizes.get(role) if isinstance(sizes, Mapping) else sizes
    if isinstance(size, Mapping):
        return str(size.get("source") or "none")
    return str(size or "none")


def _size(sizes: object, role: str) -> str:
    """Return an image's size along ``role`` as written: value, unit and source."""
    size = sizes.get(role) if isinstance(sizes, Mapping) else sizes
    if not isinstance(size, Mapping):
        return str(size or "none")
    if size.get("value") is None:
        return str(size.get("source") or "none")
    return f"{size['value']:g} {size.get('unit') or ''} ({size.get('source')})"


def _summary(data: Mapping) -> str:
    """Return the first table: what the report is of, in a few rows."""
    groups = data.get("groups") or []
    images = sum(int(group.get("images", 0)) for group in groups)
    conditions = len({group.get("condition") for group in groups})
    names = ", ".join(str(name) for name in data.get("channel_names") or [] if name)
    rows: list[tuple[str, object]] = [
        ("Step and status", f"{data.get('step')}, {data.get('status')}"),
        ("Experiment", data.get("experiment_root")),
        ("Output folder", data.get("output_folder")),
        (
            "Found",
            f"{plural(images, 'image')} in {plural(conditions, 'condition')}, "
            f"{plural(len(groups), 'folder')}",
        ),
    ]
    if data.get("masks_folder"):
        rows.append(("Masks folder", data["masks_folder"]))
    conversion = data.get("conversion")
    if conversion:
        rows.append(("Copies of", conversion.get("source_root")))
        if conversion.get("projection"):
            rows.append(("Projection", conversion["projection"]))
    if data.get("image_type"):
        rows.append(("Image type", data["image_type"]))
    if data.get("channels") is not None:
        rows.append(("Channels", f"{data['channels']}" + (f" ({names})" if names else "")))
    if data.get("mode"):
        rows.append(("Mode", data["mode"]))
    if data.get("manifest_sha256"):
        rows.append(("Run manifest SHA-256", data["manifest_sha256"]))
    return _table([("", ""), *rows])


def _folders(data: Mapping) -> str:
    groups = data.get("groups") or []
    if not groups:
        return ""
    rows = [("Condition", "Biological repeat", "Technical repeat", "Images")]
    rows += [
        (g.get("condition"), g.get("biological_repeat"), g.get("technical_repeat"), g.get("images"))
        for g in groups
    ]
    return _heading("Folders") + _fold(plural(len(groups), "folder"), _table(rows, 3))


def _images(data: Mapping) -> str:
    """Return the axes and pixel sizes: counts by source, every image folded."""
    images = data.get("images") or {}
    if not images:
        return ""
    axes = Counter(str(entry.get("axes_from")) for entry in images.values())
    sources = Counter(_source(entry.get("pixel_sizes"), "X") for entry in images.values())
    counts = [("", "Images")]
    counts += [(f"Axes from: {name}", count) for name, count in sorted(axes.items())]
    counts += [(f"Pixel size (X) from: {name}", count) for name, count in sorted(sources.items())]
    rows = [("Image", "Axes", "Roles", "Axes from", "X", "Y")]
    for path, entry in images.items():
        sizes = entry.get("pixel_sizes")
        rows.append(
            (
                path,
                entry.get("axes"),
                entry.get("roles"),
                entry.get("axes_from"),
                _size(sizes, "X"),
                _size(sizes, "Y"),
            )
        )
    return (
        _heading("Axes and pixel sizes")
        + _table(counts, 1)
        + _fold(f"Every image ({len(images):,})", _table(rows))
    )


def _design(data: Mapping) -> str:
    """Return the design rows, folded behind how many are confirmed."""
    design = data.get("design") or []
    if not design:
        return ""
    rows = [("Condition", "Biological repeat", "Repeat group", "Confirmed", "Control")]
    rows += [
        (
            row.get("condition"),
            row.get("biological_repeat"),
            row.get("repeat_group"),
            "yes" if row.get("confirmed") else "no",
            "yes" if row.get("control") else "no",
        )
        for row in design
    ]
    confirmed = sum(bool(row.get("confirmed")) for row in design)
    line = f"{confirmed} of {plural(len(design), 'repeat row')} confirmed"
    return _heading("Design") + _fold(line, _table(rows))


def _masks(data: Mapping) -> str:
    types = (data.get("masks") or {}).get("types") or {}
    if not types:
        return ""
    rows = [("Mask type", "Masks paired", "Images without one")]
    rows += [
        (name, found.get("paired"), len(found.get("images_without") or []))
        for name, found in types.items()
    ]
    return _heading("Masks") + _table(rows, 1)


def _work(data: Mapping) -> str:
    """Return what the run did: counts, and the reasons folded."""
    if "recomputed" not in data and "reused" not in data:
        return ""
    recomputed = data.get("recomputed") or {}
    rows = [
        ("", "Images"),
        ("Reused from the earlier run", len(data.get("reused") or [])),
        ("Recomputed", len(recomputed)),
        ("Resumed from an interrupted run", len(data.get("resumed") or [])),
        ("Removed", len(data.get("removed") or [])),
        ("Excluded", len(data.get("excluded") or [])),
    ]
    out = _heading("What the run did") + _table(rows, 1)
    reasons = Counter(str(reason) for reason in recomputed.values())
    if reasons:
        why = [("Recomputed because", "Images"), *sorted(reasons.items())]
        out += _fold("Why images were recomputed", _table(why, 1))
    replaced = data.get("replaced") or {}
    if replaced:
        files = [("File", "What happened"), *replaced.items()]
        out += _fold(f"Files written ({len(replaced)})", _table(files))
    return out


def _issue(issue: Mapping) -> str:
    """Return one message folded behind its code and first words."""
    message = str(issue.get("message", ""))
    short = message if len(message) <= SUMMARY_CHARS else message[:SUMMARY_CHARS] + "…"
    paths = issue.get("paths") or []
    body = f"<div>{_e(message)}</div>"
    if paths:
        listed = "".join(f"<div>{_e(path)}</div>" for path in paths)
        body += _fold(plural(len(paths), "file"), f"<div class='mb-paths'>{listed}</div>")
    if issue.get("action"):
        body += f"<div><b>What to do:</b> {_e(issue['action'])}</div>"
    code = _e(str(issue.get("code", "")).replace("_", " "))
    return _fold(f"<b>{_e(issue.get('severity'))} · {code}</b>: {_e(short)}", body)


def _checks(data: Mapping) -> str:
    issues = data.get("issues") or []
    errors = sum(issue.get("severity") == "error" for issue in issues)
    line = f"{plural(errors, 'error')}, {plural(len(issues) - errors, 'warning')}"
    return _heading("Checks") + f"<div>{line}</div>" + "".join(_issue(i) for i in issues)


def report_html(data: Mapping, text: str = "") -> str:
    """Return the report as formatted HTML.

    Parameters
    ----------
    data
        The report's data, as ``discovery_report.json`` holds it.
    text
        The plain text of ``discovery_report.txt``, folded last when given.

    Returns
    -------
    str
    """
    parts = [
        _summary(data),
        _folders(data),
        _images(data),
        _design(data),
        _masks(data),
        _work(data),
        _checks(data),
    ]
    if text:
        plain = f"<pre style='white-space:pre-wrap;font-size:12px'>{_e(text)}</pre>"
        parts.append(_heading("Plain text") + _fold("The report as plain text", plain))
    return "".join(parts)


__all__ = ["report_html"]
