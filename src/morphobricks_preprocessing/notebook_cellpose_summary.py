"""What Segment will use, in one line, and whether the last Preview used the same.

Shown on the Segment step (owner review of #548): the model, the device and every setting the
model form shows now, plus the nuclei's own model and settings when the output Both has them.
Segment may run with settings never previewed (it is not blocked); the line then says, in an
amber card, what differs from the last Preview. It reads the form only; it decides nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import ipywidgets as widgets

from . import notebook_style as ui
from .notebook_cellpose_fields import SEPARATE_NUCLEI

SETTINGS = (
    ("diameter", "diameter"),
    ("flow_threshold", "flow"),
    ("cellprob_threshold", "cellprob"),
    ("batch_size", "batch"),
)
"""The settings in the line, by field name and the short name shown."""


def _value(field: widgets.Widget) -> str:
    """Return a field's value as the line shows it (a blank diameter is the native scale)."""
    value = field.value
    if isinstance(value, str):
        return value.strip() or "native scale"
    return f"{value:g}" if isinstance(value, float) else str(value)


def used(fields: Mapping[str, widgets.Widget]) -> tuple[tuple[str, str], ...]:
    """Return what Segment will use, as ``(name, value)`` pairs in the order shown."""
    groups = [""]
    if fields[SEPARATE_NUCLEI].value:
        groups.append("nuclei_")
    pairs = []
    for prefix in groups:
        who = "nuclei " if prefix else ""
        own = fields[f"{prefix}custom_model_file"].value.strip()
        model = Path(own).name if own else fields[f"{prefix}model"].value
        pairs.append((f"{who}model", model))
        if not prefix:
            pairs.append(("device", fields["device"].value))
        for name, short in SETTINGS:
            pairs.append((f"{who}{short}", _value(fields[f"{prefix}{name}"])))
    return tuple(pairs)


def summary_html(
    now: Sequence[tuple[str, str]], previewed: Sequence[tuple[str, str]] | None
) -> str:
    """Return the line of what Segment will use, and how it differs from the last Preview."""
    values = dict(now)
    cells = f"Model {values['model']} on {values['device']}"
    rest = [f"{name} {value}" for name, value in now if name not in ("model", "device")]
    line = " · ".join([cells, *rest])
    if previewed is None:
        return ui.status("info", "Segment will use", [line + " (not previewed yet)"])
    before = dict(previewed)
    changed = [
        f"{name}: {before.get(name, 'not set')} → {value}"
        for name, value in now
        if before.get(name) != value
    ]
    changed += [f"{name}: {before[name]} → not set" for name in before if name not in values]
    if not changed:
        return ui.status("ok", "Segment will use what the last Preview used", [line])
    return ui.status(
        "warn",
        "Segment will use settings the last Preview did not use",
        [line, "Changed since the last Preview: " + "; ".join(changed)],
    )


def summary_widget() -> widgets.HTML:
    """Return the empty widget the line is written into."""
    box = widgets.HTML()
    box.add_class("mb-segment-summary")
    return box


__all__ = ["SETTINGS", "summary_html", "summary_widget", "used"]
