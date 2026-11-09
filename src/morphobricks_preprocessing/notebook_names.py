"""The rows of the names card: an ID and a shown-as name per condition, repeat and folder.

Moved out of :mod:`.notebook_description` so the stepper can place the rows on two steps: the
condition rows on *Conditions* (``condition_rows``) and the replicate and technical-repeat rows
on *Replicates*, beside the pairing (``replicate_rows``). The same row widgets are also the
children of the names card's grid (``names_grid``), which embedding pages show as before.
"""

from __future__ import annotations

import html
from dataclasses import replace

import ipywidgets as widgets

from .draft_answers import ConditionAnswer, DraftAnswers, RepeatAnswer
from .notebook_description_view import (
    ID_COLUMNS,
    KIND_TITLES,
    Entry,
    duplicate_hint,
    header_row,
    id_state,
    members_text,
    same_name,
    single_note,
    text_box,
    why_no_proposal,
)
from .notebook_values import natural_key


def _natural(item: tuple[str, object]) -> tuple:
    """Return the display order of a replicate's row: its folder label, numbers as numbers."""
    return natural_key(item[0])


class _NameRows:
    """The ID and shown-as rows of the names card (a mixin of the description card)."""

    def _entries(self) -> list[Entry]:
        """Return the rows of the names card: conditions, repeats, technical-repeat folders."""
        draft, answers = self.draft, self.draft_answers
        entries = [
            Entry(
                "conditions",
                folder,
                slot,
                f"<b>{html.escape(folder)}</b>",
                answers.conditions.get(folder, ConditionAnswer()).display_name,
            )
            for folder, slot in sorted(draft.conditions.items())
        ]
        entries += [
            Entry(
                "repeats",
                label,
                repeat.id,
                f"<b>{html.escape(label)}</b><br>{members_text(repeat.members)}",
                answers.repeats.get(label, RepeatAnswer()).display_name,
            )
            for label, repeat in sorted(draft.repeats.items(), key=_natural)
        ]
        entries += [
            Entry("technical_repeats", folder, slot, f"<b>{html.escape(folder)}</b>")
            for folder, slot in sorted(draft.technical_repeats.items(), key=_natural)
        ]
        return entries

    def _build_names(self) -> None:
        """Rebuild the ID and shown-as boxes, keeping what was typed and not yet confirmed."""
        typed = {key: box.value for key, box in self.id_inputs.items()}
        self.id_inputs, self.name_inputs, self._id_states = {}, {}, {}
        self.name_hints = {}
        entries = self._entries()
        blocks: list[widgets.Widget] = []
        kinds: list[str] = []
        for kind, title in KIND_TITLES.items():
            shown = [e for e in entries if e.kind == kind and not e.fixed]
            if not shown:
                continue
            asks_name = kind != "technical_repeats"
            rows: list[widgets.Widget] = [
                header_row(title, "ID", "Shown as (optional)" if asks_name else "", "")
            ]
            for entry in shown:
                rows += self._entry_row(entry, typed, asks_name=asks_name)
            blocks.append(widgets.VBox(rows))
            kinds.append(kind)
        self.single_note.value = single_note(entries)
        self.names_grid.children = blocks
        pairs = list(zip(kinds, blocks, strict=True))
        self.condition_rows.children = [b for k, b in pairs if k == "conditions"]
        self.replicate_rows.children = [b for k, b in pairs if k != "conditions"]

    def _entry_row(self, entry: Entry, typed: dict, *, asks_name: bool) -> list[widgets.Widget]:
        """Build the grid row of one entry (and why it has no proposal, if so)."""
        key = (entry.kind, entry.key)
        confirmed = entry.slot.confirmed
        text = confirmed or typed.get(key) or entry.slot.proposed or ""
        box = text_box(text, "type an ID", disabled=confirmed is not None)
        state = widgets.HTML(id_state(entry, text))
        box.observe(
            lambda change, e=entry, s=state: self._id_typed(e, s, change["new"]), names="value"
        )
        self.id_inputs[key], self._id_states[key] = box, state
        cells: list[widgets.Widget] = [widgets.HTML(entry.where), box]
        if asks_name:
            name = text_box(entry.shown or "", "same as the ID")
            name.observe(lambda change, e=entry: self._name_typed(e, change["new"]), names="value")
            self.name_inputs[key] = name
            cells.append(name)
        else:
            cells.append(widgets.HTML(""))
        cells.append(state)
        row: list[widgets.Widget] = [widgets.GridBox(cells, layout=ID_COLUMNS)]
        if asks_name:
            self.name_hints[key] = widgets.HTML()
            row.append(self.name_hints[key])
        if why := why_no_proposal(entry):
            row.append(widgets.HTML(why))
        return row

    def _name_typed(self, entry: Entry, text: str) -> None:
        """Save the name shown in plots and tables; an empty box means the ID is shown.

        The hint about a duplicate follows every keystroke; the answer is saved without
        rebuilding the boxes, so typing is never interrupted. A duplicate is still saved: the
        reader refuses it when the description is reviewed.
        """
        if self._refreshing or self.draft is None:
            return
        self._update_name_hints(entry.kind)
        self._draft_note = ""
        answers = self.draft_answers
        kind = "conditions" if entry.kind == "conditions" else "repeats"
        new = _updated(answers, kind, entry.key, display_name=text or None)
        if new != answers:
            self._names_refusal = ""
            self._answer(new, rebuild=False)

    def _update_name_hints(self, kind: str) -> None:
        """Show a small orange hint beside every shown-as name that another one of its kind has."""
        boxes = {key: box.value for key, box in self.name_inputs.items() if key[0] == kind}
        for key, text in boxes.items():
            same = [k for k, other in boxes.items() if k != key and same_name(text, other)]
            self.name_hints[key].value = duplicate_hint(kind, text, [k[1] for k in same])


def _updated(answers: DraftAnswers, kind: str, key: str, **fields: str | None) -> DraftAnswers:
    """Return the answers with fields of one condition or repeat set; an empty entry is dropped."""
    table = dict(getattr(answers, kind))
    empty = ConditionAnswer() if kind == "conditions" else RepeatAnswer()
    entry = replace(table.get(key, empty), **fields)
    if entry == empty:
        table.pop(key, None)
    else:
        table[key] = entry
    return replace(answers, **{kind: table})


__all__ = ["_NameRows", "_updated"]
