"""The summary lines under *Tag channels*: where each label is used, and what is still open.

Part of :class:`.notebook_channels_grid.TagGrid` (a mixin, so the grid stays one screen of
code); it reads the saved answers only.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import TYPE_CHECKING

from . import notebook_style as ui
from .channel_labels import positions, remaining, usage
from .notebook_channels_view import chip, partial_text

if TYPE_CHECKING:
    import ipywidgets as widgets

    from .channel_labels import UnitLayout
    from .notebook_channels_view import AnswerStore


class _GridSummary:
    """Where each label is used and whether tagging is finished (the grid's last lines)."""

    store: AnswerStore
    layouts: Sequence[UnitLayout]
    labels: list[str]
    message: str
    usage_html: widgets.HTML
    problem: widgets.HTML
    status: widgets.HTML

    # Summary -----------------------------------------------------------------------------

    def _summary(self) -> None:
        """Say where each label is used and whether tagging is finished."""
        answers = self.store.answers()
        total = len(self.layouts)
        found = usage(self.layouts, answers.units, self.labels)
        lines = [self._usage_line(label, len(found[label]), total) for label in self.labels]
        for label, places in positions(self.layouts, answers.units, self.labels).items():
            if len(places) > 1:
                where = ", ".join(
                    f"channel {i + 1} in {n} folder(s)" for i, n in sorted(places.items())
                )
                lines.append(
                    f"<div class='mb-help'>{html.escape(label)} is not always in the same "
                    f"column: {where}.</div>"
                )
        self.usage_html.value = (
            "<div class='mb-help'><b>Where each label is used</b></div>" + "".join(lines)
        )
        self.problem.value = self.message
        self.status.value = self._progress(total)

    def shown_as(self, label: str) -> str | None:
        """Return the shown-as name of a saved label (the chip's tooltip), if it has one."""
        return getattr(self.store.answers().channels.get(label), "display_name", None)

    def _usage_line(self, label: str, used: int, total: int) -> str:
        """Return the usage line of a label; one used in only some units is orange."""
        mark = chip(label, self.labels, self.shown_as(label))
        if used == total:
            return f"<div>{mark} used in all {total} folders</div>"
        if used == 0:
            return (
                f"<div>{mark} <span style='color:#92400E'>not used in any folder yet</span></div>"
            )
        return (
            "<div style='background:#FFFBEB;border-left:3px solid #D97706;padding:2px 8px;"
            f"margin:2px 0'>{mark} <span style='color:#92400E'>"
            f"{partial_text(label, used, total)} (A_SCOPE_INCOMPLETE)</span></div>"
        )

    def _progress(self, total: int) -> str:
        """Return the status: what is still to answer, or that every channel is tagged."""
        answers = self.store.answers()
        left = remaining(self.layouts, answers.units, self.labels)
        if left.done:
            return ui.status("ok", f"Every channel of every one of the {total} folders is tagged.")
        parts = []
        if left.cells:
            parts.append(f"{left.cells} channel(s) still to tag")
        if left.types:
            parts.append(f"{left.types} image type(s) still to choose")
        if left.blocked:
            parts.append(f"{left.blocked} folder(s) cannot be tagged")
        return ui.status("warn", "Not finished: " + ", ".join(parts) + ".")


__all__ = ["_GridSummary"]
