"""Step B of *Your channels*: "Tag each channel" (a self-contained card).

An image-first grid: one row per imaging unit (condition / biological repeat / technical repeat),
one column per C index. Each cell is a safe thumbnail of the unit's representative image (about
160 px, **Enlarge** for the 1024 px view), the C index, the header name quoted exactly as read and
a dropdown limited to the labels of step A (an unused slot needs a label too, for example
*unused*); there is no free text. A
unit whose header names equal the labels exactly is pre-tagged, but the pre-tag is only a
suggestion until the user confirms it once per layout (units with the same header names), beside
the thumbnails. Step B is complete only when every channel position of every unit is tagged and
every image type answered. Labels used in only some units say so, with the consequence.

The card reads and saves through an :class:`.notebook_channels_view.AnswerStore`; the image-type
column is a separate piece (:class:`.notebook_channels_type.ImageTypeColumn`) the card places in
each row.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_tags
from .channel_labels import (
    UnitLayout,
    positions,
    proposal_for,
    remaining,
    tags_of,
    usage,
)
from .draft_answers import UnitKey
from .notebook_channel_thumbs import CONTRAST_TEXT, Thumbnails
from .notebook_channels_type import ImageTypeColumn
from .notebook_channels_view import (
    CHOOSE,
    AnswerStore,
    blank_chip,
    chip,
    columns,
    quoted,
    unit_name,
)

THUMB_PX = "160px"


def partial_text(label: str, used: int, total: int) -> str:
    """Return the sentence for a label used in only some units, with its consequence."""
    return (
        f"used in {used} of {total} units: an analysis of {html.escape(label)} will be "
        "refused for the other units"
    )


class TagGrid:
    """The tag grid: rows of units, a dropdown per channel, one confirm per layout."""

    def __init__(
        self,
        store: AnswerStore,
        types: ImageTypeColumn,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self.store, self.types, self._on_change = store, types, on_change
        self.layouts: Sequence[UnitLayout] = ()
        self.labels: list[str] = []
        self.thumbs: Thumbnails | None = None
        self.cells: dict[tuple[UnitKey, int], widgets.Dropdown] = {}
        self.confirm_buttons: list[widgets.Button] = []
        self.message = ""
        self._building = False
        self.blocks = widgets.VBox()
        self.usage_html = widgets.HTML()
        self.status = widgets.HTML()
        self.problem = widgets.HTML()
        self.detail = widgets.VBox()
        self.contrast = widgets.HTML(f"<div class='mb-help'>{CONTRAST_TEXT}</div>")
        self.plane = widgets.HTML()
        self.widget = ui.card(
            "B. Tag each channel",
            [
                ui.help_text(
                    "For every unit, say which label each channel is, looking at its picture. "
                    "The same label should be in the same column in every row; a label used in "
                    "only some units is shown in orange. Every channel and every image type "
                    "must be answered."
                ),
                self.contrast,
                self.plane,
                self.blocks,
                self.detail,
                self.problem,
                self.usage_html,
                self.status,
            ],
            visible=False,
        )

    # Opening -----------------------------------------------------------------------------

    def open(self, layouts: Sequence[UnitLayout], labels: list[str], thumbs: Thumbnails) -> None:
        """Show the grid for these units and labels (the confirmed label list of step A)."""
        self.layouts, self.labels, self.thumbs = layouts, labels, thumbs
        self.types.open(layouts)
        self.detail.children = []
        self.refresh()
        ui.set_visible(self.widget, True)

    def close(self) -> None:
        """Hide the grid."""
        ui.set_visible(self.widget, False)

    @property
    def complete(self) -> bool:
        """True when every position of every unit is tagged and every type answered."""
        return remaining(self.layouts, self.store.answers().units, self.labels).done

    # Tags --------------------------------------------------------------------------------

    def _proposal(self, layout: UnitLayout) -> tuple[str, ...] | None:
        return proposal_for(layout, self.labels)

    def effective(self, layout: UnitLayout) -> tuple[tuple[str | None, ...], set[int]]:
        """Return the tags a unit shows and which of them are only proposals.

        The user's saved tags first; a position not tagged takes the header proposal if that
        label is not already tagged elsewhere in the unit.
        """
        saved = list(tags_of(layout, self.store.answers().units.get(layout.key)))
        proposal = self._proposal(layout)
        proposed: set[int] = set()
        if proposal is not None and self.types.saved(layout.key) != "rgb":
            for index, tag in enumerate(saved):
                if tag is None and proposal[index] not in saved:
                    saved[index] = proposal[index]
                    proposed.add(index)
        return tuple(saved), proposed

    def _tagged(self, layout: UnitLayout, index: int, value: str | None) -> None:
        """Save one cell's tag; a label already tagged elsewhere in the unit is refused."""
        if self._building:
            return
        answers = self.store.answers()
        tags = list(tags_of(layout, answers.units.get(layout.key)))
        if value and value in [t for i, t in enumerate(tags) if i != index]:
            other = tags.index(value) + 1
            self.message = ui.status(
                "error",
                f"{value} was not tagged here.",
                [
                    f"{html.escape(value)} is already channel {other} of "
                    f"{html.escape(unit_name(layout.key))}: one label cannot stand at two "
                    "positions of a unit. Change that one first."
                ],
                raw=True,
            )
            self.refresh()
            return
        tags[index] = value
        if self.store.update(synced(with_tags(answers, layout, tags), self.layouts)):
            self.message = ""
        else:
            self.message = ui.status("error", "Not saved.", [self.store.refusal()])
        self.refresh()
        self._on_change()

    def _confirm_layout(self, units: Sequence[UnitLayout]) -> None:
        """Save the header's proposal (tags and colour pre-tick) of these units as the answer."""
        answers = self.store.answers()
        rgb = [u.key for u in units if self.types.proposed(u)]
        for layout in units:
            proposal = self._proposal(layout)
            if proposal is None:
                continue
            tags = tags_of(layout, answers.units.get(layout.key))
            filled = tuple(t if t is not None else proposal[i] for i, t in enumerate(tags))
            answers = with_tags(answers, layout, filled)
        ok = self.store.update(synced(answers, self.layouts))
        self.message = "" if ok else ui.status("error", "Not saved.", [self.store.refusal()])
        if ok and rgb:
            self.types.confirm_header(rgb)
        self.refresh()
        self._on_change()

    # Building ----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the rows from the saved answers (pictures are kept, not read again)."""
        self._building = True
        try:
            self.cells, self.confirm_buttons = {}, []
            self.types.open(self.layouts)
            width = max((u.channels for u in self.layouts if not u.rgb), default=3)
            width = max(width, 3 if any(u.rgb for u in self.layouts) else 1)
            groups: dict[tuple, list[UnitLayout]] = {}
            for layout in sorted(self.layouts, key=lambda u: u.key):
                groups.setdefault(layout.signature if layout.usable else (0,), []).append(layout)
            blocks: list[widgets.Widget] = []
            header = widgets.GridBox(
                [widgets.HTML("<b>unit</b>")]
                + [widgets.HTML(f"<b>channel {i + 1}</b> (C{i})") for i in range(width)]
                + [widgets.HTML("<b>image type</b>")],
                layout=columns(width),
            )
            blocks.append(header)
            for units in groups.values():
                blocks.append(self._group(units, width))
            self.blocks.children = blocks
        finally:
            self._building = False
        self._summary()

    def _group(self, units: Sequence[UnitLayout], width: int) -> widgets.Widget:
        """Build the rows of units with one layout, and their one confirm button."""
        first = units[0]
        out: list[widgets.Widget] = []
        if first.usable:
            names = " · ".join(quoted(first.name(i)) for i in range(first.channels))
            what = f"Header names: {names}" if not first.rgb else "Header says: colour (RGB)"
            text = f"{what} &nbsp;·&nbsp; {len(units)} unit(s) with this layout."
            proposal = self._proposal(first)
            pending = [u for u in units if self._pending(u)]
            if proposal is not None:
                text += (
                    " They equal your labels exactly: pre-tagged below, not confirmed yet."
                    if pending
                    else " They equal your labels exactly."
                )
            out.append(widgets.HTML(f"<div class='mb-help' style='margin-top:10px'>{text}</div>"))
            if pending:
                button = ui.button(
                    f" Yes, these are right for these {len(pending)} unit(s)",
                    style="success",
                    icon="check",
                    tooltip="Save the pre-ticked answers of these units",
                )
                button.on_click(lambda _, p=pending: self._confirm_layout(p))
                self.confirm_buttons.append(button)
                out.append(button)
        out += [self._row(u, width) for u in units]
        return widgets.VBox(out)

    def _pending(self, layout: UnitLayout) -> bool:
        """Return True when the unit shows a proposal (tags or colour) not saved yet."""
        _, proposed = self.effective(layout)
        return bool(proposed) or self.types.proposed(layout)

    def _row(self, layout: UnitLayout, width: int) -> widgets.Widget:
        """Build the row of one unit: its name, a cell per C index and its image type."""
        info = f"<b>{html.escape(unit_name(layout.key))}</b>"
        if layout.usable and self.thumbs is not None:
            info += (
                f"<div class='mb-help'>first image by path: {html.escape(layout.image or '')}"
                f"<br>1 of {layout.images} image(s); {html.escape(self.thumbs.plane(layout))}"
                "</div>"
            )
        if not layout.usable:
            info += ui.status("warn", "Cannot be tagged.", [html.escape(layout.problem)], raw=True)
        shown = self.types.shown(layout)
        tags, proposed = self.effective(layout)
        cells: list[widgets.Widget] = [widgets.HTML(info)]
        for index in range(width):
            if not layout.usable or index >= layout.channels:
                text = "" if not layout.usable else f"no channel {index + 1} in this image"
                cells.append(widgets.HTML(f"<span class='mb-help'>{text}</span>"))
            else:
                cells.append(self._cell(layout, index, tags[index], index in proposed, shown))
        cells.append(self.types.cell(layout) if layout.usable else widgets.HTML(""))
        return widgets.GridBox(cells, layout=columns(width))

    def _picture(self, layout: UnitLayout, index: int) -> widgets.Widget:
        """Build the thumbnail of one channel, or the reason there is none."""
        thumb = self.thumbs.get(layout, index) if self.thumbs is not None else None
        if thumb is None or thumb.png is None:
            note = "" if thumb is None else html.escape(thumb.note)
            return widgets.HTML(
                f"<div class='mb-help' style='border:1px dashed #94A3B8;padding:6px'>"
                f"no picture: {note}</div>"
            )
        box = widgets.Image(
            value=thumb.png,
            format="png",
            layout=widgets.Layout(width="99%", max_width=THUMB_PX, border="1px solid #CBD5E1"),
        )
        return box

    def _cell(
        self, layout: UnitLayout, index: int, tag: str | None, proposed: bool, shown: str | None
    ) -> widgets.Widget:
        """Build one cell: picture, C index and header name, the tag and a zoom button."""
        header = f"<b>C{index}</b> {quoted(layout.name(index))}"
        children: list[widgets.Widget] = [self._picture(layout, index)]
        thumb = self.thumbs.get(layout, index) if self.thumbs is not None else None
        if thumb is not None and thumb.note:
            header += f"<div class='mb-help'>{html.escape(thumb.note)}</div>"
        children.append(widgets.HTML(header))
        if shown == "rgb":
            children.append(widgets.HTML(chip("RGB"[index], list("RGB"))))
        else:
            children.append(self._dropdown(layout, index, tag, proposed))
        zoom = ui.button(" Enlarge", icon="search-plus", tooltip="Show the 1024 px detail view")
        zoom.layout = widgets.Layout(width="99%")
        zoom.on_click(lambda _, u=layout, i=index: self._enlarge(u, i))
        children.append(zoom)
        return widgets.VBox(children)

    def _dropdown(
        self, layout: UnitLayout, index: int, tag: str | None, proposed: bool
    ) -> widgets.Widget:
        """Build the dropdown of one cell: the labels and what is shown now."""
        options = [(CHOOSE, None), *((label, label) for label in self.labels)]
        value = tag if tag in {o[1] for o in options} else None
        drop = widgets.Dropdown(options=options, value=value, layout=widgets.Layout(width="99%"))
        drop.observe(lambda change, u=layout, i=index: self._tagged(u, i, change["new"]), "value")
        self.cells[(layout.key, index)] = drop
        if tag is None:
            mark = blank_chip("not tagged")
        else:
            mark = chip(tag, self.labels, self._shown_as(tag))
            if proposed:
                mark += " <span class='mb-help'>proposed, not confirmed</span>"
        return widgets.VBox([drop, widgets.HTML(mark)])

    def _enlarge(self, layout: UnitLayout, index: int) -> None:
        """Show the 1024 px picture of one channel under the grid."""
        thumb = self.thumbs.get(layout, index, detail=True)
        close = ui.button(" Close", icon="times")
        close.on_click(lambda _: setattr(self.detail, "children", []))
        title = widgets.HTML(
            f"<b>{html.escape(unit_name(layout.key))}</b> · C{index} "
            f"{quoted(layout.name(index))} · {html.escape(layout.image or '')}"
        )
        picture: widgets.Widget = (
            widgets.Image(value=thumb.png, format="png", layout=widgets.Layout(max_width="1024px"))
            if thumb.png
            else widgets.HTML(ui.status("warn", "No picture.", [html.escape(thumb.note)], raw=True))
        )
        self.detail.children = [title, picture, close]

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
                    f"channel {i + 1} in {n} unit(s)" for i, n in sorted(places.items())
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

    def _shown_as(self, label: str) -> str | None:
        """Return the shown-as name of a saved label (the chip's tooltip), if it has one."""
        saved = self.store.answers().channels.get(label)
        return None if saved is None else saved.display_name

    def _usage_line(self, label: str, used: int, total: int) -> str:
        """Return the usage line of a label; one used in only some units is orange."""
        mark = chip(label, self.labels, self._shown_as(label))
        if used == total:
            return f"<div>{mark} used in all {total} units</div>"
        if used == 0:
            return f"<div>{mark} <span style='color:#92400E'>not used in any unit yet</span></div>"
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
            return ui.status("ok", f"Every channel of every one of the {total} units is tagged.")
        parts = []
        if left.cells:
            parts.append(f"{left.cells} channel(s) still to tag")
        if left.types:
            parts.append(f"{left.types} image type(s) still to choose")
        if left.blocked:
            parts.append(f"{left.blocked} unit(s) cannot be tagged")
        return ui.status("warn", "Not finished: " + ", ".join(parts) + ".")


__all__ = ["TagGrid", "partial_text"]
