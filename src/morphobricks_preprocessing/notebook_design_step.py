"""The *Design* step of the shipped notebook: conditions, repeats and comparisons in one grid.

Owner decision (10 November 2026, item (q)): the conditions, the replicates and "compared
against" are one step with an easy default. The step shows one grid, conditions down and
biological repeats across, each cell the folder that is that repeat of that condition, with
what each condition is compared against in the last column. Everything in it is a proposal:

- the IDs are the folder names (only where the reader accepts a folder name as an ID),
- folders with the same name are the same biological repeat,
- no condition is compared against another.

Nothing of it is taken until the user clicks **Looks right: confirm the design**. That one
click adopts every ID as shown (the same all-or-none adoption as *Yes, use these IDs*,
:meth:`.notebook_description._DescriptionCard._confirm_ids`, with the reader's refusals listed)
and records "no comparison" for each condition still showing that proposal. A name that cannot
be proposed, a different pairing or a shown-as name is edited under **Edit names or
pairing…**, which holds the rows of the earlier steps unchanged. *Check and save* waits for the
click, and says so.

The grid is built once for the conditions and repeats there are and then updated in place, so
a choice never replaces the control the user is using.
"""

from __future__ import annotations

import html

import ipywidgets as widgets

from . import notebook_style as ui
from .notebook_description import _updated
from .notebook_description_view import pill
from .notebook_render import blocked_line
from .notebook_values import natural_key
from .validation import plural

CONFIRM_LABEL = " Looks right: confirm the design"
HELP = (
    "Rows are conditions, columns are biological repeats, and a cell is the folder of that "
    "repeat. The names are the folder names and nothing is compared, until you say otherwise."
)
NOT_CONFIRMED = "the design is not confirmed: click Looks right: confirm the design (Design)"


class _DesignStep:
    """The grid of the *Design* step, its one confirmation and the fold that edits it."""

    def _build_design_step(self) -> None:
        """Build the grid, the confirmation and the fold of the names and the pairing."""
        self._design_shape: tuple | None = None
        self._design_cells: dict[tuple, widgets.HTML] = {}
        self._design_problem = ""
        self.design_table = widgets.GridBox()
        self.design_technical = widgets.HTML()
        self.design_confirm = ui.button(
            CONFIRM_LABEL,
            style="success",
            icon="check",
            tooltip="Use these names, this pairing and these comparisons",
            main=True,
        )
        self.design_blocker = widgets.HTML()
        self.design_result = widgets.HTML()
        self.design_change = ui.button(
            " Change the design", icon="pencil", tooltip="Take the names back to edit them"
        )
        self.design_edit = ui.accordion(
            "Edit names or pairing…",
            [widgets.VBox([self._conditions_card(), self.design_card, self._replicates_card()])],
        )
        self.design_step_card = ui.card(
            "Your design",
            [
                ui.help_text(HELP),
                self.design_table,
                self.design_technical,
                self.design_result,
                self.design_confirm,
                self.design_blocker,
                self.design_change,
            ],
            visible=False,
        )
        self.design_confirm.on_click(lambda _: self._confirm_design())
        self.design_change.on_click(lambda _: self._change_design())

    # State -------------------------------------------------------------------------------

    def _design_confirmed(self) -> bool:
        """Return True when every ID is adopted and every comparison answered."""
        draft = self.draft
        if draft is None or self.draft_answers is None or not self._ids_all_confirmed():
            return False
        if len(draft.conditions) < 2:
            return True
        return all(self._chosen_reference(c) is not None for c in draft.conditions)

    def _shown_reference(self, condition: str) -> str | None:
        """Show "no comparison" as the proposal of a condition nobody answered for."""
        chosen = self._chosen_reference(condition)
        return "" if chosen is None and self._in_steps() else chosen

    def _reference_options(self, condition: str) -> list[tuple[str, str | None]]:
        """Leave out "choose" in the stepper: the proposal there is "no comparison"."""
        options = super()._reference_options(condition)
        return options[1:] if self._in_steps() else options

    def _in_steps(self) -> bool:
        return getattr(self, "design_table", None) is not None

    # The click ---------------------------------------------------------------------------

    def _confirm_design(self) -> bool:
        """Adopt the design as shown: every ID, and "no comparison" where nothing was chosen.

        Returns
        -------
        bool
            True when the design is now confirmed; False when an ID was refused (the step
            lists each refusal and nothing was adopted) or the answers could not be saved.
        """
        if self.draft is None or not self._in_steps():
            return False
        self._names_refusal = self._draft_note = self._design_problem = ""
        if not self._confirm_ids():
            self._design_problem = self._names_refusal
            self.design_edit.selected_index = 0
            self._refresh_design_step()
            return False
        answers = self.draft_answers
        if len(self.draft.conditions) > 1:
            for condition in sorted(self.draft.conditions):
                if self._chosen_reference(condition) is None:
                    answers = _updated(answers, "conditions", condition, reference="")
        if answers != self.draft_answers and not self._answer(answers):
            self._design_problem = ui.status("error", "Not taken.", [str(self._refusal)])
            self._refresh_design_step()
            return False
        self._refresh_description()
        self._refresh_design_status()
        return self._design_confirmed()

    def _change_design(self) -> None:
        """Take the IDs back so the names can be edited (as *Change the IDs* does)."""
        if self.draft is None:
            return
        self._design_problem = ""
        self.ids_confirmed.set(False)
        self.design_edit.selected_index = 0
        self._refresh_description()

    # Showing -----------------------------------------------------------------------------

    def _refresh_design_step(self) -> None:
        """Show the grid for the draft as it is answered now."""
        if not self._in_steps():
            return
        draft = self.draft
        ui.set_visible(self.design_step_card, draft is not None)
        ui.set_visible(self.design_edit, draft is not None)
        if draft is None:
            self._design_shape = None
            return
        conditions = sorted(draft.conditions)
        repeats = sorted(draft.repeats, key=natural_key)
        compared = len(conditions) > 1
        boxes = tuple(id(self.reference_boxes.get(c)) for c in conditions) if compared else ()
        shape = (tuple(conditions), tuple(repeats), boxes)
        if shape != self._design_shape:
            self._design_shape = shape
            self._build_design_table(conditions, repeats, compared)
        self._fill_design_table(conditions, repeats)
        confirmed = self._design_confirmed()
        missing = [key for (_kind, key), text in self._wanted_ids().items() if not text]
        ui.set_visible(self.design_confirm, not confirmed)
        ui.set_visible(self.design_change, confirmed)
        self.design_confirm.disabled = bool(missing)
        self.design_blocker.value = blocked_line(
            [f"{plural(len(missing), 'name')} missing ({', '.join(missing[:3])}): to type "
             "under Edit names or pairing"]
            if missing and not confirmed
            else []
        )  # fmt: skip
        if missing and not confirmed:
            self.design_edit.selected_index = 0
        self.design_result.value = (
            ui.status(
                "ok",
                f"Design confirmed: {plural(len(conditions), 'condition')} × "
                f"{plural(len(repeats), 'biological repeat')}.",
            )
            if confirmed
            else self._design_problem
        )

    def _build_design_table(
        self, conditions: list[str], repeats: list[str], compared: bool
    ) -> None:
        """Build the grid's cells once: a header row, then one row per condition."""
        cells: dict[tuple, widgets.HTML] = {}
        children: list[widgets.Widget] = []

        def cell(key: tuple) -> widgets.HTML:
            cells[key] = widgets.HTML()
            return cells[key]

        children.append(widgets.HTML("<b>Condition</b>"))
        children += [cell(("repeat", label)) for label in repeats]
        if compared:
            children.append(widgets.HTML("<b>Compared against</b>"))
        for condition in conditions:
            children.append(cell(("condition", condition)))
            children += [cell((condition, label)) for label in repeats]
            if compared:
                children.append(self.reference_boxes[condition])
        self._design_cells = cells
        columns = f"minmax(120px, 1.3fr) repeat({len(repeats)}, minmax(90px, 1fr))"
        if compared:
            columns += " minmax(170px, 1.4fr)"
        self.design_table.layout = widgets.Layout(
            grid_template_columns=columns, grid_gap="4px 12px", width="99%", align_items="center"
        )
        self.design_table.children = children

    def _fill_design_table(self, conditions: list[str], repeats: list[str]) -> None:
        """Write the names and the folders into the grid's cells, in place."""
        draft, wanted, cells = self.draft, self._wanted_ids(), self._design_cells
        for condition in conditions:
            slot = draft.conditions[condition]
            cells[("condition", condition)].value = _named(
                wanted.get(("conditions", condition), ""), condition, slot.confirmed is not None
            )
        for label in repeats:
            repeat = draft.repeats[label]
            cells[("repeat", label)].value = _named(
                wanted.get(("repeats", label), ""), label, repeat.id.confirmed is not None
            )
            by_condition = {m.condition: m for m in repeat.members}
            for condition in conditions:
                cells[(condition, label)].value = _folder(by_condition.get(condition))
        technical = [
            wanted.get(("technical_repeats", folder), "") or f"{folder} (no name)"
            for folder in sorted(draft.technical_repeats, key=natural_key)
            if folder != "single"
        ]
        self.design_technical.value = (
            f"<div class='mb-help'>Technical repeats (folders inside a repeat): "
            f"{html.escape(', '.join(technical))}</div>"
            if technical
            else ""
        )

    # Hooks -------------------------------------------------------------------------------

    def _refresh_description(self) -> None:
        super()._refresh_description()
        self._refresh_design_step()

    def _hide_description(self) -> None:
        super()._hide_description()
        self._refresh_design_step()

    def _id_typed(self, entry: object, state: widgets.HTML, text: str) -> None:
        super()._id_typed(entry, state, text)
        if not self._refreshing:
            self._refresh_design_step()  # the grid shows the names as they are typed

    def _todo(self, applied: object, answers: object, problem: str) -> tuple[list, list[str]]:
        """Ask for the design's one click instead of each comparison it would record."""
        questions, extra = super()._todo(applied, answers, problem)
        if not self._in_steps() or self._design_confirmed():
            return questions, extra
        kept = [line for line in extra if " is compared against (choose" not in line]
        return questions, [NOT_CONFIRMED, *kept]


def _named(shown: str, folder: str, confirmed: bool) -> str:
    """Return a row or column title: the ID as shown, its folder when another, and its state."""
    if not shown:
        return f"<b>{html.escape(folder)}</b> {pill('warn', 'needs a name')}"
    other = "" if shown == folder else f" <span class='mb-help'>folder {html.escape(folder)}</span>"
    state = "" if confirmed else f" {pill('info', 'proposed')}"
    return f"<b>{html.escape(shown)}</b>{other}{state}"


def _folder(member: object) -> str:
    """Return a cell: the folder of one condition in one repeat, with its images, or a dash."""
    if member is None:
        return "<span class='mb-help'>no folder</span>"
    inside = len(member.units)
    folders = f" · {inside} folders" if inside > 1 else ""
    return (
        f"{html.escape(member.source_repeat)}"
        f"<div class='mb-help'>{plural(member.image_count, 'image')}{folders}</div>"
    )


__all__ = ["CONFIRM_LABEL", "NOT_CONFIRMED", "_DesignStep"]
