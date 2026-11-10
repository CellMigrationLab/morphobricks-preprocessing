"""Step 1 of the notebook: the pairing card (which folders are the same biological repeat).

The pairing is an answer of the draft (``DraftAnswers.pairing``, rebuilt on the draft in v0.5.1,
SWEEP-PP, owner answer 4): the card shows one row per biological-repeat folder of the draft's
base (the folders found, paired by name), with a dropdown of the repeat it belongs to. A change
is saved in the draft at once; the replicates it changes are asked "same way?" again on *Check
and save*, where the comparability of every replicate is confirmed. Nothing here reads or writes
``experiment_design.csv``: the run derives it from the published description. There is no other
model behind the card: the shown pairing is :attr:`pairing`, the draft's pairing filled with each
folder's own name.
"""

from __future__ import annotations

import html
from dataclasses import replace
from pathlib import Path

import ipywidgets as widgets
from morphobricks_normalization.records import SINGLE

from . import notebook_style as ui
from .dataset import check_dataset
from .discovery import (
    IdentityError,
    check_identity_text,
    check_output_folder,
    default_output_folder,
)
from .draft import DraftMember
from .notebook_options import _PAIRING_COLUMNS
from .notebook_row_fold import RowFold, row_status
from .validation import plural


def _units(draft: object, label: str) -> frozenset:
    """Return the units of one repeat of a draft (to tell whether a pairing changed it)."""
    repeat = draft.repeats.get(label)
    if repeat is None:
        return frozenset()
    return frozenset((m.key, u.source_folder) for m in repeat.members for u in m.units)


class _DesignCard:
    """Step 1's pairing card: one repeat per biological-repeat folder, kept in the draft."""

    # Building ----------------------------------------------------------------------------

    def _build_design_card(self) -> None:
        """Build the pairing card, hidden until the folder is checked."""
        self.pairing: dict[tuple[str, str], str] | None = None
        self._design_output: Path | None = None
        self._design_note = ""
        self.design_status = widgets.HTML()
        self.pairing_grid = widgets.VBox()
        self.pairing_detail = widgets.VBox()  # a picture's Enlarge
        self.pairing_box = widgets.VBox(
            [
                widgets.HTML("<b>Pair the biological repeats across conditions</b>"),
                ui.help_text(
                    "A repeat group is one biological repeat in every condition: the same "
                    "preparation, imaged for each condition. Folders with the same name are "
                    "paired by default. If a repeat has another folder name in some "
                    "condition (acquired a day later, for example), choose its group in "
                    "the dropdown. Each group may hold one folder per condition. The dropdown "
                    "offers folder names only; the repeat's own ID is set with the names below."
                ),
                self.pairing_grid,
                self.pairing_detail,
            ]
        )
        self.design_card = ui.card(
            "Experiment design",
            [
                ui.help_text(
                    "Later bricks normalise each biological repeat over all conditions "
                    "together, so the brick needs to know which folders are the same "
                    "biological repeat. Your pairing is saved with your other answers in the "
                    "draft; whether each replicate's images are comparable is confirmed on "
                    "Check and save, and <b>experiment_design.csv</b> is written only when you "
                    "save the experiment description."
                ),
                self.design_status,
                self.pairing_box,
            ],
            visible=False,
        )

    def _design_folder(self) -> Path | None:
        """Return the output folder the answers are saved in, or None if it may not be written."""
        folder = ui.clean_path(self.folder.value)
        if not folder:
            return None
        output = Path(self._output_folder() or default_output_folder(folder))
        if check_output_folder(folder, output) or self._output_file_in_the_way():
            return None
        return output

    # Opening and closing -----------------------------------------------------------------

    def _open_design(self, *, output_changed: bool = False) -> None:
        """Open the card on the draft of the checked folder (or of the published file)."""
        self._design_output = self._design_folder()
        self._design_note = ""
        self._forget_published()
        self._load_description()
        self._folded = self._saved_current()
        self.pairing = self._pairing_of(
            dict(self.draft_answers.pairing) if self.draft_answers else {}
        )
        self._build_design_editor()
        ui.set_visible(self.design_card, True)
        self._design_changed()

    def _close_design(self) -> None:
        self.pairing = None
        ui.set_visible(self.design_card, False)
        self._hide_description()
        if self.preflight is not None:
            self.dataset = self.preflight.dataset
            self._show_issue_summary()

    def _members(self) -> tuple[DraftMember, ...]:
        """Return the folders of the draft's base, sorted by condition and folder."""
        base = self._draft_base
        found = () if base is None else (m for r in base.repeats.values() for m in r.members)
        return tuple(sorted(found, key=lambda m: m.key))

    def _pairing_of(self, saved: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
        """Return every folder's repeat: its saved pairing, else its own name.

        A saved pairing that names a folder the draft does not have, or a repeat that is not a
        name (empty, ``NA``-like or with outer spaces), is not used: the folders' own names are
        shown and a note says why. Nothing is repaired.
        """
        members = {m.key: m.source_repeat for m in self._members()}
        problems = [
            f"{'/'.join(key)} is not a folder of the draft" for key in saved if key not in members
        ]
        for key, label in saved.items():
            try:
                check_identity_text(label, "replicate")
            except IdentityError as exc:
                problems.append(f"{'/'.join(key)}: {exc}")
        if problems:
            self._design_note = ui.status("warn", "The saved pairing was not used.", problems)
            saved = {}
        return {key: saved.get(key, own) for key, own in members.items()}

    # The pairing grid --------------------------------------------------------------------

    def _build_design_editor(self) -> None:
        """Build one row per folder with the dropdown of its repeat."""
        members = self._members()
        conditions = {m.condition for m in members}
        shown = len(conditions) > 1 and any(m.source_repeat != SINGLE for m in members)
        ui.set_visible(self.pairing_box, shown)
        pairing = self.pairing or {}
        options = sorted({m.source_repeat for m in members} | set(pairing.values()))
        rows = [
            widgets.GridBox(
                [widgets.HTML(f"<b>{h}</b>") for h in ("Condition", "Folder", "Repeat group")],
                layout=_PAIRING_COLUMNS,
            )
        ]
        units = self._reopen_units()
        folded: list[tuple[widgets.Widget, str]] = []
        for member in members if shown else ():
            dropdown = widgets.Dropdown(
                options=options,
                value=pairing.get(member.key, member.source_repeat),
                layout=widgets.Layout(width="99%"),
            )
            dropdown.observe(
                lambda change, key=member.key: self._pair(key, change["new"]), names="value"
            )
            keys = [k for k in units or () if k[:2] == member.key]
            folded.append((
                widgets.GridBox(
                    [
                        widgets.HTML(html.escape(member.condition)),
                        self._folder_cell(
                            member.key,
                            f"<b>{html.escape(member.source_repeat)}</b> "
                            f"<span class='mb-help'>{plural(member.image_count, 'image')}</span>",
                        ),
                        dropdown,
                    ],
                    layout=_PAIRING_COLUMNS,
                ),
                row_status(units, keys, answered=True),
            ))  # fmt: skip
        if not hasattr(self, "_pairing_fold"):
            self._pairing_fold = RowFold("folder")
        self.pairing_grid.children = rows + self._pairing_fold.arrange(folded)

    def _pair(self, key: tuple[str, str], group: str) -> None:
        if self.pairing is None or self.pairing.get(key) == group:
            return
        self.pairing = {**self.pairing, key: group}
        self._design_changed()

    def _design_pairing(self) -> dict[tuple[str, str], str]:
        """Return the folders paired under another name than their own (the draft's answer)."""
        return {k: g for k, g in (self.pairing or {}).items() if g != k[1]}

    def _design_changed(self) -> None:
        """Update the balance, the messages, the status and the later steps after an answer."""
        self.dataset = check_dataset(
            self.preflight.discovery, self.pairing, excluded=self.preflight.excluded
        )
        self._show_issue_summary()
        self._refresh_design_status()
        self._sync_description()
        if self.confirmed_mapping is not None:
            self._refresh_axis_next()
        self._refresh_run_step()

    def _refresh_design_status(self) -> None:
        """Say how the pairing is kept (no confirmation on this card)."""
        if self.pairing is None:
            self.design_status.value = ""
            return
        self.design_status.value = self._design_note + ui.status(
            "info",
            "Folders with the same name are the same replicate.",
            [
                "Choose another replicate for a folder that was named differently. Your pairing "
                "is saved with your other answers and confirmed on Check and save."
            ],
        )

    def _sync_description(self) -> None:
        """Take the pairing into the answers; a replicate whose folders changed is asked again."""
        answers = self.draft_answers
        if answers is None or self.pairing is None:
            return
        pairing = self._design_pairing()
        if pairing == dict(answers.pairing):
            self._refresh_description()
            return
        old, new = self._draft_base.paired(answers.pairing), self._draft_base.paired(pairing)
        repeats = {
            label: answer
            if _units(old, label) == _units(new, label)
            else replace(answer, confirmed_on=None)
            for label, answer in answers.repeats.items()
            if label in new.repeats
        }
        self._answer(replace(answers, pairing=pairing, repeats=repeats))
