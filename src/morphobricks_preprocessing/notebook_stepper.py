"""The notebook's stepper: one step on screen at a time, a strip of chips, Back and Next.

A layout only (``ui`` layer, owner decision 2026-11-09, UX proposal Q1). Each step is a page of
cards that already exist; the stepper shows one page and hides the others with
:func:`.notebook_style.set_visible`, so a hidden page keeps every widget, answer and picture as
it is. The strip has one chip per step: green with a check mark when the step is done, blue for
the step on screen, grey for a step still to do, and orange for a step a reopened experiment
changed. A chip is a button that opens its step. **Next** is never blocked: what must be answered
is still refused where it was (the save and the run say what is missing).

What a step says about itself (done or not, a one-line summary) comes from a callable the owner
gives, which reads the cards' own state; the stepper decides nothing. The same lines make the
overview (:meth:`Stepper.overview`), each with a *change* button that opens its step.

The page is one column and scrolls as a whole (owner's test of 10 November 2026): no box inside
it scrolls on its own, and nothing is laid out in side-by-side cards. A step is short because it
says less, folds its details and draws smaller pictures.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import ipywidgets as widgets

from . import notebook_style as ui

CHIP_CLASSES = (
    "mb-chip-done",
    "mb-chip-noted",
    "mb-chip-current",
    "mb-chip-todo",
    "mb-chip-changed",
)
STEPPER_CSS = """
<style>
.mb-strip { flex-wrap: wrap !important; gap: 4px; margin: 0 0 6px 0; }
.mb-strip .mb-chip { border-radius: 999px; width: auto; height: auto; padding: 2px 9px;
  font-size: 12px; line-height: 1.5; border: none; box-shadow: none; }
.mb-chip.mb-chip-done { background: #16A34A; color: #FFFFFF; }
.mb-chip.mb-chip-noted { background: #DCFCE7; color: #166534; border: 1px solid #16A34A; }
.mb-chip.mb-chip-current { background: #2563EB; color: #FFFFFF; font-weight: 600; }
.mb-chip.mb-chip-todo { background: #E2E8F0; color: #334155; }
.mb-chip.mb-chip-changed { background: #EA580C; color: #FFFFFF; }
.mb-nav { gap: 8px; margin: 4px 0 4px 0; }
.mb-compact .mb-root { padding: 10px 14px; }
.mb-compact .mb-title { font-size: 17px; margin: 0 0 4px 0; }
.mb-compact .mb-purpose { font-size: 13px; font-weight: 400; color: #475569; margin-left: 8px; }
.mb-compact .jupyter-widget-Collapse-header, .mb-compact .p-Collapse-header,
.mb-compact .lm-Collapse-header { padding: 3px 10px; font-size: 13px; }
.mb-compact .jupyter-widget-Accordion, .mb-compact .p-Accordion { margin: 0 0 4px 0; }
.mb-compact .mb-card { padding: 8px 12px; margin: 0 0 8px 0; }
.mb-compact .mb-header { font-size: 11px; padding-bottom: 3px; margin-bottom: 5px; }
.mb-compact .mb-help { font-size: 12px; margin: 1px 0 4px 0; line-height: 1.35; }
.mb-compact .mb-status { padding: 5px 10px; margin: 4px 0; line-height: 1.4; }
.mb-compact .mb-tiles { gap: 2px 18px; margin: 4px 0 4px 2px; }
.mb-compact .mb-tile { display: flex; align-items: baseline; gap: 5px; padding: 0; min-width: 0;
  flex: 0 0 auto; border: none; background: transparent; box-shadow: none; }
.mb-compact .mb-tile .mb-tile-value { font-size: 13px; }
.mb-compact .mb-tile .mb-tile-label { font-size: 12px; }
.mb-compact .mb-confirm { padding: 6px 10px; margin: 6px 0 2px 0; }
.mb-compact .mb-question { margin-bottom: 4px; }
.mb-compact .mb-table td { padding: 3px 10px; }
.mb-compact .mb-table th { padding: 4px 10px; }
.mb-compact .mb-table-wrap, .mb-compact .mb-paths { max-height: none; overflow-y: visible; }
.mb-compact .mb-label-rows { max-height: none !important; overflow: visible !important; }
.mb-compact button.jupyter-button { height: 32px !important; line-height: 30px; }
.mb-compact .mb-strip button.jupyter-button { height: auto !important; line-height: 1.5; }
.mb-compact .widget-html-content { line-height: 1.4; }
.mb-compact .mb-step-body > * { flex: 0 0 auto; }
.mb-compact .mb-card > * { flex-shrink: 0; }
.mb-compact .mb-next { display: none; }
.mb-compact .mb-card .mb-help { display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 1; overflow: hidden; cursor: help; }
.mb-compact .mb-card .mb-help:hover, .mb-compact .mb-confirm .mb-help,
.mb-compact .mb-card .mb-card .mb-help,
.mb-compact .mb-table-wrap + .mb-help { -webkit-line-clamp: unset; display: block; }
.mb-compact img.mb-grey, .mb-compact img.mb-colour, .mb-compact img.mb-crop {
  width: 108px !important; height: auto; }
.mb-compact .mb-scan img.mb-grey, .mb-compact .mb-scan img.mb-colour,
.mb-compact .mb-scan img.mb-crop { width: 180px !important; }
.mb-channel-row { gap: 8px; }
.mb-compact .mb-mask-pictures img { width: 170px !important; height: auto; }
.mb-compact .mb-channel-row .mb-channel img.mb-grey { width: 99% !important; }
.mb-about { margin: 0 0 6px 0; font-size: 12.5px; color: #475569; }
.mb-about summary { cursor: pointer; color: #2563EB; }
.mb-about .mb-intro { margin: 4px 0 0 0; }
.mb-fields { align-items: center; }
.mb-fields .mb-field { font-weight: 600; white-space: nowrap; }
.mb-overview .mb-change { width: auto; height: auto; padding: 0 6px; background: transparent;
  color: #2563EB; border: none; box-shadow: none; text-decoration: underline; }
</style>
"""


@dataclass(frozen=True)
class StepState:
    """What a step says about itself.

    Attributes
    ----------
    done
        True when the step needs nothing more.
    text
        One line saying what was answered or found (plain text).
    changed
        True when a reopened experiment changed what the step holds (orange).
    note
        A word the chip shows after its title when the step is done with a reservation
        (``uncalibrated``); empty for a plain done.
    """

    done: bool
    text: str
    changed: bool = False
    note: str = ""


def _no_wait() -> str:
    return ""


@dataclass(frozen=True)
class Step:
    """One step: its chip title, its page and what it says about itself.

    Attributes
    ----------
    title
        The short name on the chip and in the page title.
    purpose
        The sentence under the page title (HTML).
    children
        The page's cards, in order.
    state
        Returns the step's :class:`StepState` from the cards' current state.
    waiting
        Returns why the step cannot be answered yet (HTML), or an empty string.
    chip
        The name on the chip when it is shorter than the title (the strip is one row).
    """

    title: str
    purpose: str
    children: Sequence[widgets.Widget]
    state: Callable[[], StepState]
    waiting: Callable[[], str] = _no_wait
    chip: str = ""


class Stepper:
    """The strip, the pages and Back/Next of a list of steps; one page shown at a time."""

    def __init__(
        self, steps: Sequence[Step], on_show: Callable[[int], None] = lambda _: None
    ) -> None:
        self.steps = list(steps)
        self._on_show = on_show
        self.current = 0
        count = len(self.steps)
        self.chips = [self._chip(i) for i in range(count)]
        self.waits = [widgets.HTML() for _ in self.steps]
        self.pages = [self._page(i, count) for i in range(count)]
        self.back = ui.button("Back", icon="arrow-left")
        self.next = ui.button("Next", style="primary", icon="arrow-right")
        self.back.on_click(lambda _: self.go(self.current - 1))
        self.next.on_click(lambda _: self.go(self.current + 1))
        self.strip = widgets.HBox(self.chips)
        self.strip.add_class("mb-strip")
        nav = widgets.HBox([self.back, self.next])
        nav.add_class("mb-nav")
        self._overview_lines: list[tuple[int, widgets.HTML]] = []
        self.widget = widgets.VBox(
            [widgets.HTML(STEPPER_CSS), self.strip, *self.pages, nav],
            layout=widgets.Layout(width="99%"),
        )
        self.widget.add_class("mb-compact")
        self.go(0)

    def _page(self, index: int, count: int) -> widgets.VBox:
        """Return a step's page: its title and purpose on one line, then its cards."""
        step = self.steps[index]
        head = widgets.HTML(
            f"<div class='mb-title'>Step {index + 1} of {count} · {html.escape(step.title)}"
            f" <span class='mb-purpose'>{step.purpose}</span></div>"
        )
        root = widgets.VBox([head, self._body(index)])
        root.add_class("mb-root")
        outer = widgets.VBox([widgets.HTML(ui.STYLE_CSS), root], layout=widgets.Layout(width="99%"))
        outer.add_class("mb-page")
        return outer

    def _body(self, index: int) -> widgets.VBox:
        """Return a step's cards, one under the other (the page scrolls, never a box in it)."""
        body = widgets.VBox([self.waits[index], *self.steps[index].children])
        body.add_class("mb-step-body")
        return body

    def _chip(self, index: int) -> widgets.Button:
        chip = widgets.Button(layout=widgets.Layout(width="auto"))
        chip.add_class("mb-chip")
        chip.on_click(lambda _: self.go(index))
        return chip

    def go(self, index: int) -> None:
        """Show step ``index`` (0-based; clamped to the steps there are)."""
        self.current = max(0, min(index, len(self.steps) - 1))
        for i, page in enumerate(self.pages):
            ui.set_visible(page, i == self.current)
        self._on_show(self.current)
        self.refresh()

    def states(self) -> list[StepState]:
        """Return what every step says about itself now."""
        return [step.state() for step in self.steps]

    def refresh(self) -> None:
        """Update the chips, the waiting line of the step shown, Back/Next and the overview."""
        states = self.states()
        for i, (chip, step, state) in enumerate(zip(self.chips, self.steps, states, strict=True)):
            for name in CHIP_CLASSES:
                chip.remove_class(name)
            chip.add_class(f"mb-chip-{chip_kind(state, i == self.current)}")
            noted = f" · {state.note}" if state.done and state.note else ""
            name = step.chip or step.title
            chip.description = f"{'✓ ' if state.done else ''}{i + 1} {name}{noted}"
            chip.tooltip = state.text
        self.waits[self.current].value = self.steps[self.current].waiting()
        last = len(self.steps) - 1
        ui.set_visible(self.back, self.current > 0)
        ui.set_visible(self.next, self.current < last)
        if self.current > 0:
            self.back.description = f" Back: {self.steps[self.current - 1].title}"
        if self.current < last:
            self.next.description = f" Next: {self.steps[self.current + 1].title}"
        for index, line in self._overview_lines:
            line.value = overview_line(self.steps[index].title, states[index])

    def overview(self, count: int) -> widgets.Widget:
        """Return the one-line summaries of the first ``count`` steps, each with *change*."""
        rows = []
        for index in range(count):
            line = widgets.HTML()
            change = ui.button("change", tooltip=f"Open step {index + 1}")
            change.add_class("mb-change")
            change.on_click(lambda _, i=index: self.go(i))
            self._overview_lines.append((index, line))
            rows.append(widgets.HBox([line, change], layout=widgets.Layout(align_items="center")))
        box = ui.card("Your experiment, step by step", rows)
        box.add_class("mb-overview")
        self.refresh()
        return box


def chip_kind(state: StepState, current: bool) -> str:
    """Return how a chip looks: current, changed, done (noted when it has a note) or to do."""
    if current:
        return "current"
    if state.changed:
        return "changed"
    if state.done:
        return "noted" if state.note else "done"
    return "todo"


def overview_line(title: str, state: StepState) -> str:
    """Return one overview line: a mark, the step's name and its summary (HTML)."""
    mark, colour = (
        ("●", "#EA580C") if state.changed else ("✓", "#16A34A") if state.done else ("○", "#64748B")
    )
    return (
        f"<span style='color:{colour};font-weight:700'>{mark}</span> "
        f"<b>{html.escape(title)}</b> · {html.escape(state.text)}"
    )


def about(text: str) -> widgets.HTML:
    """Return a step's explanation folded behind *About this step* (``text`` is HTML)."""
    return widgets.HTML(
        "<details class='mb-about'><summary>About this step</summary>"
        f"<div class='mb-intro'>{text}</div></details>"
    )


def field_rows(rows: Sequence[tuple[str, Sequence[widgets.Widget]]]) -> widgets.GridBox:
    """Return labelled input rows: a short label, then the row's widgets side by side."""
    cells: list[widgets.Widget] = []
    for label, parts in rows:
        name = widgets.HTML(f"<span class='mb-field'>{html.escape(label)}</span>")
        cells += [name, widgets.HBox(list(parts), layout=widgets.Layout(width="99%"))]
    box = widgets.GridBox(
        cells,
        layout=widgets.Layout(grid_template_columns="max-content 1fr", grid_gap="4px 10px"),
    )
    box.add_class("mb-fields")
    return box


__all__ = [
    "CHIP_CLASSES",
    "STEPPER_CSS",
    "Step",
    "StepState",
    "Stepper",
    "about",
    "chip_kind",
    "field_rows",
    "overview_line",
]
