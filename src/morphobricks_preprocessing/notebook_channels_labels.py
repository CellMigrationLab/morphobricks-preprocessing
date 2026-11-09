"""Step A of *Your channels*: "Which labels did you image?" (a self-contained card).

One list of channel IDs naming **what was stained** (the target, not the dye or the microscope's
slot), each with an optional shown-as name. The list starts with as many slots as the largest
channel count found in any image and is prefilled only from header names that are valid IDs as
they stand and not generic (:func:`.channel_labels.propose_labels`). Near-identical header
spellings are shown as a group for the user to decide, never merged; a typed ID that differs from
another only by capital letters is refused at once; **Add label** adds a slot. The labels are saved
(to ``channels`` of the draft) only when the user confirms the list.

The card reads and saves through an :class:`.notebook_channels_view.AnswerStore` and tells its
owner when the confirmed list changed (``on_change``); it knows nothing of the tag grid.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import label_ids, synced, untagged_by, with_labels
from .channel_labels import (
    GENERIC,
    NOT_VALID,
    LabelProposal,
    UnitLayout,
    case_only,
    generic_reason,
    label_problems,
    propose_labels,
    usable_as_label,
)
from .notebook_channels_view import AnswerStore, chip
from .notebook_description_view import plain

ROW_COLUMNS = "minmax(150px, 1fr) minmax(150px, 1fr) minmax(200px, 2fr)"
QUESTION = "Are these the labels you imaged?"
EXPLANATION = (
    "A label is the short name of <b>what was stained</b>, for example <i>DNA</i> or "
    "<i>ACTIN</i>, not the dye (<i>AF488</i>) or the microscope's slot (<i>Ch1</i>). The same "
    "label must be used for the same stain in every folder, and different stains must never share "
    "a label. Letters and digits, no space at the start or end; capital letters count as the "
    "same label."
)


def problem_text(codes: Sequence[str]) -> str:
    """Return the plain words for the codes that refuse a label (the code is always named)."""
    out = []
    for code in codes:
        if code == "E_DUPLICATE_VALUE":
            reason = "is listed twice"
        elif code == "E_RGB_TOKEN_MISUSE":
            reason = "R, G and B are the fixed names of colour images: use another label"
        elif code == "E_ID_CASE_COLLISION":
            reason = "differs from another label only by capital letters: refused"
        else:
            reason = plain(code)
        out.append(f"{reason} ({code})")
    return "; ".join(out)


@dataclass
class _Row:
    """One label slot: its ID box, shown-as box and what is said beside it."""

    id_box: widgets.Text
    name_box: widgets.Text
    state: widgets.HTML
    widget: widgets.GridBox


class LabelsCard:
    """The label list: slots, proposals, look-alike groups, **Add label** and the confirmation."""

    def __init__(self, store: AnswerStore, on_change: Callable[[], None]) -> None:
        self.store, self._on_change = store, on_change
        self.slots = 1
        self.header_names: list[str] = []
        self.rows: list[_Row] = []
        self.groups: list[tuple[str, ...]] = []
        self.proposal = LabelProposal()
        self.layouts: Sequence[UnitLayout] = ()
        self._building = False
        self._ok = False
        self.note = widgets.HTML()
        self.rows_box = widgets.VBox()
        self.groups_box = widgets.VBox()
        self.skipped = widgets.HTML()
        self.add_button = ui.button(
            " Add label", style="info", icon="plus", tooltip="Add one more label slot"
        )
        self.add_button.on_click(lambda _: self._add_row())
        self.confirm = ui.ConfirmToggle(
            QUESTION,
            EXPLANATION,
            "Yes, these are the labels",
            "Confirmed: these are the labels of the experiment.",
            "Change the labels",
        )
        self.confirm.observe(self._toggled)
        self.status = widgets.HTML()
        self.problem = widgets.HTML()
        self.widget = ui.card(
            "A. Which labels did you image?",
            [
                ui.help_text(
                    "Name what was stained, once per stain. The number of slots is the largest "
                    "number of channels found in any image; <b>Add label</b> adds more. Header "
                    "names are offered only when they are valid labels as they are and not "
                    "generic. "
                    "Every channel position of every folder needs a label, so an unused slot "
                    "needs one too: declare it here (for example <i>unused</i>)."
                ),
                self.note,
                self.groups_box,
                self.rows_box,
                self.add_button,
                self.skipped,
                self.problem,
                self.status,
                self.confirm.widget,
            ],
            visible=False,
        )

    @property
    def labels(self) -> list[str]:
        """Return the saved labels, sorted (what the grid's dropdowns offer)."""
        return label_ids(self.store.answers())

    @property
    def confirmed(self) -> bool:
        """True while the list is confirmed (saved and not being changed)."""
        return self.confirm.value

    def display_names(self) -> dict[str, str | None]:
        """Return the shown-as name of each saved label."""
        return {k: v.display_name for k, v in self.store.answers().channels.items()}

    # Opening -----------------------------------------------------------------------------

    def open(self, slots: int, header_names: list[str], layouts: Sequence[UnitLayout]) -> None:
        """Show the card for an experiment: its slot count, header names and units; resume."""
        self.slots, self.header_names, self.layouts = max(slots, 1), header_names, layouts
        saved = self.labels
        self.proposal = (
            LabelProposal() if saved else propose_labels(header_names, self.store.used_channels())
        )
        self.groups = list(self.proposal.groups)
        names = self.display_names()
        self._building = True
        try:
            texts = saved or list(self.proposal.single)
            self.rows = []
            for text in texts:
                self.rows.append(self._row(text, names.get(text) or ""))
            while len(self.rows) < self.slots:
                self.rows.append(self._row("", ""))
            self.confirm.set(bool(saved), notify=False)
        finally:
            self._building = False
        self._render()
        ui.set_visible(self.widget, True)

    def close(self) -> None:
        """Hide the card."""
        ui.set_visible(self.widget, False)

    # Rows --------------------------------------------------------------------------------

    def _row(self, text: str, name: str) -> _Row:
        """Build one label slot with its ID box, shown-as box and state."""
        id_box = widgets.Text(
            value=text,
            placeholder="what you stained, not the dye",
            layout=widgets.Layout(width="99%"),
        )
        name_box = widgets.Text(
            value=name, placeholder="shown as (optional)", layout=widgets.Layout(width="99%")
        )
        state = widgets.HTML()
        row = _Row(
            id_box,
            name_box,
            state,
            widgets.GridBox(
                [id_box, name_box, state],
                layout=widgets.Layout(
                    grid_template_columns=ROW_COLUMNS,
                    grid_gap="6px 12px",
                    width="99%",
                    align_items="center",
                ),
            ),
        )
        id_box.observe(lambda _: self._typed(), names="value")
        name_box.observe(lambda _: self._typed(), names="value")
        return row

    def _add_row(self) -> None:
        if self.confirm.value:
            return
        self.rows.append(self._row("", ""))
        self._render()

    def _typed(self) -> None:
        if not self._building:
            self._validate()

    def _render(self) -> None:
        self.rows_box.children = [r.widget for r in self.rows]
        self._render_groups()
        self._validate()
        locked = self.confirm.value
        for row in self.rows:
            row.id_box.disabled = row.name_box.disabled = locked
        self.add_button.disabled = locked
        self._render_skipped()

    def _validate(self) -> None:
        """Say, per slot, whether its label is allowed; refuse a case collision at once."""
        texts = [r.id_box.value for r in self.rows]
        filled = [(i, t) for i, t in enumerate(texts) if t != ""]
        problems = label_problems([t for _, t in filled], self.store.used_channels())
        by_slot = {filled[i][0]: codes for i, codes in problems.items()}
        everything = [t for _, t in filled]
        for index, row in enumerate(self.rows):
            text = texts[index]
            if index in by_slot:
                row.state.value = ui.status("error", f"“{text}” {problem_text(by_slot[index])}")
            elif text:
                row.state.value = chip(text, everything, row.name_box.value or None)
            else:
                row.state.value = "<span class='mb-help'>empty slot</span>"
        ok = not by_slot and bool(filled)
        self._ok = ok
        self.confirm.disabled = not ok and not self.confirm.value
        self.problem.value = (
            ui.status("error", "These labels cannot be used yet.", ["Fix the red entries."])
            if by_slot
            else ""
        )

    # Look-alike groups -------------------------------------------------------------------

    def _render_groups(self) -> None:
        boxes: list[widgets.Widget] = []
        for group in self.groups:
            boxes.append(self._group_box(group))
        self.groups_box.children = boxes

    def _group_box(self, group: tuple[str, ...]) -> widgets.Widget:
        """Build the question about one group of look-alike header names."""
        found = ", ".join(f"‘{n}’" for n in group)
        usable = [n for n in group if usable_as_label(n, self.store.used_channels())]
        clash = any(case_only(a, b) for a in usable for b in usable)
        pick = widgets.Dropdown(
            options=usable, layout=widgets.Layout(width="220px"), disabled=not usable
        )
        same = ui.button(" Same label", style="success", icon="link", tooltip="One label")
        same.disabled = not usable or self.confirm.value
        same.on_click(lambda _: self._decide(group, [pick.value]))
        different = ui.button(" Different labels", style="", icon="unlink")
        different.disabled = len(usable) < 2 or clash or self.confirm.value
        different.on_click(lambda _: self._decide(group, usable))
        dismiss = ui.button(" Decide later", style="", icon="times")
        dismiss.on_click(lambda _: self._decide(group, []))
        why = ""
        if clash:
            why = (
                "Some of these differ only by capital letters: they cannot be different labels "
                "(E_ID_CASE_COLLISION). "
            )
        if not usable:
            why += "None of them can be a label as it is written: type the label yourself. "
        text = widgets.HTML(
            ui.status(
                "warn",
                f"These header names look alike: {found}. Same label?",
                [
                    why + "Nothing is merged for you: choose the spelling to use if they are "
                    "one label, or say they are different."
                ],
                raw=True,
            )
        )
        return widgets.VBox([text, widgets.HBox([pick, same, different, dismiss])])

    def _decide(self, group: tuple[str, ...], chosen: list[str]) -> None:
        """Put the chosen spellings into the first empty slots; the group is then settled."""
        self.groups = [g for g in self.groups if g != group]
        for text in chosen:
            free = next((r for r in self.rows if not r.id_box.value), None)
            if free is None:
                free = self._row("", "")
                self.rows.append(free)
            self._building = True
            free.id_box.value = text
            self._building = False
        self._render()

    def _render_skipped(self) -> None:
        """Say which header names were not offered as labels, and why."""
        said = []
        for name, why in self.proposal.skipped:
            if why == GENERIC:
                said.append(f"<b>{html.escape(name)}</b> ({generic_reason(name)})")
            elif why == NOT_VALID:
                said.append(f"<b>{html.escape(name)}</b> (not valid as a label as written)")
        self.skipped.value = (
            "<div class='mb-help'>Not offered as labels: " + "; ".join(said) + ". Type a label "
            "if you stained something these names stand for.</div>"
            if said
            else ""
        )

    # Confirming --------------------------------------------------------------------------

    def _toggled(self, confirmed: bool) -> None:
        if self._building:
            return
        if confirmed:
            if not self._ok or not self._save():
                self.confirm.set(False, notify=False)
        self._render()
        self._on_change()

    def _save(self) -> bool:
        """Save the label list; False when it is refused. Tags of a gone label are taken back."""
        labels: dict[str, str | None] = {}
        for row in self.rows:
            if row.id_box.value:
                labels[row.id_box.value] = row.name_box.value or None
        answers = self.store.answers()
        gone = untagged_by(answers, labels)
        new = synced(with_labels(answers, labels), self.layouts)
        if not self.store.update(new):
            self.status.value = ui.status(
                "error", "Not saved: the labels were refused.", [self.store.refusal()]
            )
            return False
        self.status.value = (
            ui.status(
                "warn",
                f"{gone} tag(s) of a label that is no longer in the list were taken back.",
                ["Tag those channels again."],
            )
            if gone
            else ""
        )
        return True


__all__ = ["LabelsCard", "problem_text"]
