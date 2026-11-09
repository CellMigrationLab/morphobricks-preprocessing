"""*Tag channels*: which label each channel is, one answer per block of identical headers.

The folders (imaging units: condition / biological repeat / technical repeat) are shown in
**blocks**. Folders whose image headers are identical, exactly (the same channel count, the same
raw header names at every C index, all present, and the same colour flag), form one block, and
the user tags its channels once, for every folder of the block (owner decision Q5): one dropdown
per C index, limited to the confirmed stains (an unused slot needs a label too, for example
*unused*); there is no free text. Under the dropdowns, every folder of the block has its own
pictures (:mod:`.notebook_channels_rows`) for checking. Nothing is grouped by guessing: a folder
whose header lacks a channel name, or whose saved tags differ, is a block of its own, and a
folder can be taken out of its block (**Tag this folder on its own**). A block answer writes,
for each of its folders, the same tags into the draft that tagging the folders one by one would.

On a reopen, new folders whose header is exactly that of a saved, fully tagged block (the same
block key, but no tags yet) form a block of their own. It offers **Tag like the saved group**:
one click copies those tags, as if chosen by hand (never automatic, PR 17c). A header without
every channel name is never offered it.

A block whose header names equal the labels exactly is pre-tagged, but the pre-tag is only a
suggestion until the user confirms it, once for the block. The page is complete only when every
channel position of every unit is tagged and every image type answered. Labels used in only some
folders say so, with the consequence.

The card reads and saves through an :class:`.notebook_channels_view.AnswerStore`. The image
type is answered by :class:`.notebook_channels_type.ImageTypeColumn`: placed in each folder row,
or, with ``place_types`` False (the stepper), on its own page, and only restated here.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence

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
from .notebook_channel_thumbs import COLOUR_TEXT, CONTRAST_TEXT, Thumbnails
from .notebook_channels_rows import FolderRows
from .notebook_channels_type import ImageTypeColumn
from .notebook_channels_view import (
    CHOOSE,
    AnswerStore,
    blank_chip,
    chip,
    columns,
    quoted,
)
from .notebook_row_fold import UNCHANGED, RowFold, row_status
from .notebook_values import natural_key


def partial_text(label: str, used: int, total: int) -> str:
    """Return the sentence for a label used in only some units, with its consequence."""
    return (
        f"used in {used} of {total} folders: an analysis of {html.escape(label)} will be "
        "refused for the other folders"
    )


def folder_order(layout: UnitLayout) -> tuple:
    """Return the display order of a folder: its labels, numbers as numbers (rep2, rep10)."""
    return tuple(natural_key(part) for part in layout.key)


def named(layout: UnitLayout) -> bool:
    """Return True when the header names every channel of a usable unit (a block can hold it).

    Every image of the unit must have that header: a unit whose later images differ is never
    grouped on its first image alone.
    """
    return layout.usable and layout.uniform and all(layout.name(i) for i in range(layout.channels))


class TagGrid:
    """The tag grid: blocks of folders with identical headers, one answer per block."""

    def __init__(
        self,
        store: AnswerStore,
        types: ImageTypeColumn,
        on_change: Callable[[], None] = lambda: None,
    ) -> None:
        self.store, self.types, self._on_change = store, types, on_change
        # False when the image type has a page of its own (the stepper): no type column here.
        self.place_types = True
        self.layouts: Sequence[UnitLayout] = ()
        self.labels: list[str] = []
        self.cells: dict[tuple[UnitKey, int], widgets.Dropdown] = {}
        self.confirm_buttons: list[widgets.Button] = []
        self.split_buttons: dict[UnitKey, widgets.Button] = {}
        self.like_buttons: list[widgets.Button] = []
        # Folders the user took out of their block (this session; never saved).
        self.alone: set[UnitKey] = set()
        # The status of each unit against a saved experiment (None without one): rows fold.
        self.reopen: Callable[[], Mapping[UnitKey, str] | None] = lambda: None
        self.fold = RowFold("folder block")
        self.message = ""
        self._building = False
        self.blocks = widgets.VBox()
        self.usage_html = widgets.HTML()
        self.status = widgets.HTML()
        self.problem = widgets.HTML()
        self.detail = widgets.VBox()
        self.rows = FolderRows(self.detail)
        self.contrast = widgets.HTML(f"<div class='mb-help'>{CONTRAST_TEXT}</div>")
        self.plane = widgets.HTML()
        self.widget = ui.card(
            "Tag each channel",
            [
                ui.help_text(
                    "Folders whose image headers are identical are one block: say once which "
                    "label each channel is, looking at the pictures of every folder of the "
                    "block. The same label should be in the same column in every block; a label "
                    "used in only some folders is shown in orange. Every channel and every "
                    "image type must be answered."
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

    @property
    def thumbs(self) -> Thumbnails | None:
        """The pictures of the folders' first images."""
        return self.rows.thumbs

    # Opening -----------------------------------------------------------------------------

    def open(self, layouts: Sequence[UnitLayout], labels: list[str], thumbs: Thumbnails) -> None:
        """Show the grid for these units and labels (the confirmed label list of step A)."""
        if layouts is not self.layouts:
            self.alone = set()
        self.layouts, self.labels, self.rows.thumbs = layouts, labels, thumbs
        if self.place_types:
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

    def split(self, key: UnitKey) -> None:
        """Take one folder out of its block, to tag it on its own (nothing is saved)."""
        self.alone.add(key)
        self.refresh()

    # Blocks ------------------------------------------------------------------------------

    def _colour(self, layout: UnitLayout) -> bool:
        return layout.usable and self.types.shown(layout) == "rgb"

    def _block_key(self, layout: UnitLayout) -> tuple:
        """Return what the folders of one block share exactly; a folder alone has its own key."""
        colour = self._colour(layout)
        tags = tags_of(layout, self.store.answers().units.get(layout.key))
        grouped = (colour and layout.uniform) or named(layout)
        together = grouped and layout.key not in self.alone
        return (layout.signature, colour, tags, None if together else layout.key)

    def block_list(self) -> list[list[UnitLayout]]:
        """Return the blocks of folders, each sorted, in the order of their first folder."""
        groups: dict[tuple, list[UnitLayout]] = {}
        for layout in sorted(self.layouts, key=folder_order):
            groups.setdefault(self._block_key(layout), []).append(layout)
        return list(groups.values())

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

    def _tagged(self, units: Sequence[UnitLayout], index: int, value: str | None) -> None:
        """Save one channel's tag for every folder of a block; a label used twice is refused."""
        if self._building:
            return
        answers = self.store.answers()
        first = units[0]
        tags = list(tags_of(first, answers.units.get(first.key)))
        if value and value in [t for i, t in enumerate(tags) if i != index]:
            self._refuse_twice(value, tags.index(value) + 1)
            return
        tags[index] = value
        for layout in units:  # every folder of the block holds these tags
            answers = with_tags(answers, layout, tags)
        if self.store.update(synced(answers, self.layouts)):
            self.message = ""
        else:
            self.message = ui.status("error", "Not saved.", [self.store.refusal()])
        self.refresh()
        self._on_change()

    def _refuse_twice(self, value: str, other: int) -> None:
        self.message = ui.status(
            "error",
            f"{value} was not tagged here.",
            [
                f"{html.escape(value)} is already channel {other} of these folders: one label "
                "cannot stand at two positions of a folder. Change that one first."
            ],
            raw=True,
        )
        self.refresh()

    def _confirm_layout(self, units: Sequence[UnitLayout]) -> None:
        """Save the tags these units show as the answer: exactly what is displayed.

        Only the proposals shown (:meth:`effective`) are saved; a position whose proposal is
        not shown (its label is already tagged elsewhere in the unit) stays untagged. The colour
        pre-tick is saved too only when the image type is answered here.
        """
        answers = self.store.answers()
        rgb = [u.key for u in units if self.place_types and self.types.proposed(u)]
        for layout in units:
            shown, proposed = self.effective(layout)
            if proposed:
                answers = with_tags(answers, layout, shown)
        ok = self.store.update(synced(answers, self.layouts))
        self.message = "" if ok else ui.status("error", "Not saved.", [self.store.refusal()])
        if ok and rgb:
            self.types.confirm_header(rgb)
        self.refresh()
        self._on_change()

    def saved_like(self, units: Sequence[UnitLayout]) -> list[tuple[tuple[str, ...], int]]:
        """Return the tags of saved blocks with exactly this block's header, and their sizes.

        Only for a block of new folders with no tag yet whose header names every channel; a
        saved block is one whose folders are unchanged since the save and fully tagged.
        """
        first, reopen = units[0], self.reopen()
        if reopen is None or not named(first) or self._colour(first):
            return []
        saved = self.store.answers().units
        if any(u.key in self.alone or reopen.get(u.key) == UNCHANGED for u in units):
            return []
        if any(t is not None for u in units for t in tags_of(u, saved.get(u.key))):
            return []
        found: dict[tuple[str, ...], int] = {}
        for layout in self.layouts:
            tags = tags_of(layout, saved.get(layout.key))
            same = layout.signature == first.signature and not self._colour(layout)
            if same and reopen.get(layout.key) == UNCHANGED and None not in tags:
                found[tags] = found.get(tags, 0) + 1
        return sorted(found.items())

    def _like_button(
        self, units: Sequence[UnitLayout], tags: tuple[str, ...], count: int
    ) -> widgets.Button:
        """Return the button that copies a saved block's tags to these folders."""
        group = " · ".join(tags)
        button = ui.button(
            f" Tag like the saved group ({group}; {count} folder(s))",
            style="info",
            icon="clone",
            tooltip="Copy the tags of the saved folders whose header is exactly this one",
        )
        button.layout = widgets.Layout(width="auto")
        button.on_click(lambda _, u=units, t=tags: self.tag_like(u, t))
        self.like_buttons.append(button)
        return button

    def tag_like(self, units: Sequence[UnitLayout], tags: tuple[str, ...]) -> None:
        """Save ``tags`` for every folder of a block: the user's click, as if chosen by hand."""
        answers = self.store.answers()
        for layout in units:
            answers = with_tags(answers, layout, tags)
        ok = self.store.update(synced(answers, self.layouts))
        self.message = "" if ok else ui.status("error", "Not saved.", [self.store.refusal()])
        self.refresh()
        self._on_change()

    # Building ----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the blocks from the saved answers (pictures are kept, not read again)."""
        self._building = True
        try:
            self.cells, self.confirm_buttons, self.split_buttons = {}, [], {}
            self.like_buttons = []
            if self.place_types:
                self.types.open(self.layouts)
            width = max((u.channels for u in self.layouts if not u.rgb), default=3)
            width = max(width, 3 if any(u.rgb for u in self.layouts) else 1)
            header = widgets.GridBox(
                [widgets.HTML("<b>folder</b>")]
                + [widgets.HTML(f"<b>channel {i + 1}</b> (C{i})") for i in range(width)]
                + [widgets.HTML("<b>image type</b>" if self.place_types else "")],
                layout=columns(width),
            )
            blocks = self.block_list()
            shared = [b[0].signature for b in blocks if named(b[0])]
            built = [
                (
                    self._block(units, width, shared.count(units[0].signature) > 1),
                    self._status(units),
                )
                for units in blocks
            ]
            self.blocks.children = [header, *self.fold.arrange(built)]
        finally:
            self._building = False
        self._summary()

    def _status(self, units: Sequence[UnitLayout]) -> str:
        """Return whether a block's folders are as saved; it folds once fully answered."""
        answered = remaining(units, self.store.answers().units, self.labels).done
        return row_status(self.reopen(), [u.key for u in units], answered)

    def _block(self, units: Sequence[UnitLayout], width: int, apart: bool) -> widgets.Widget:
        """Build one block: what its header says, its answer, and the rows of its folders."""
        first = units[0]
        colour = self._colour(first)
        out: list[widgets.Widget] = [widgets.HTML(self._block_text(units, colour, apart))]
        if first.usable and not colour:
            out.append(self._answer_row(units, width))
        pending = [u for u in units if u.usable and self._pending(u)]
        if pending:
            button = ui.button(
                f" Yes, these are right for these {len(pending)} folder(s)",
                style="success",
                icon="check",
                tooltip="Save the pre-ticked answers of these folders",
            )
            button.on_click(lambda _, p=pending: self._confirm_layout(p))
            self.confirm_buttons.append(button)
            out.append(button)
        out += [self._like_button(units, tags, n) for tags, n in self.saved_like(units)]
        out += [self._folder_row(u, units, width, colour) for u in units]
        box = widgets.VBox(out)
        box.add_class("mb-tag-block")
        return box

    def _block_text(self, units: Sequence[UnitLayout], colour: bool, apart: bool) -> str:
        """Return the line above a block: its header, its size and what is still to do."""
        first = units[0]
        if not first.usable:
            return ""
        if first.rgb:
            what = "Header says: colour (RGB)"
        else:
            what = "Header names: " + " · ".join(
                quoted(first.name(i)) for i in range(first.channels)
            )
        text = f"{what} &nbsp;·&nbsp; {len(units)} folder(s)"
        if colour:
            text += f". {self._type_text(first)} Pictures: {COLOUR_TEXT}."
        elif len(units) > 1:
            text += " with this header: tagged once, for all of them."
        elif not first.uniform:
            text += ": its images do not all have this header, so it is tagged on its own."
        elif not named(first):
            text += ": a header without every channel name is tagged folder by folder."
        if apart:
            text += (
                " Other folders with this header are in another block (other tags or taken out)."
            )
        if not colour and self._proposal(first) is not None:
            text += (
                " They equal your labels exactly: pre-tagged below, not confirmed yet."
                if any(self._pending(u) for u in units)
                else " They equal your labels exactly."
            )
        return f"<div class='mb-help' style='margin-top:12px'>{text}</div>"

    def _type_text(self, layout: UnitLayout) -> str:
        """Say the image-type answer of a colour block, as the image-type page gave it."""
        if self.types.saved(layout.key) == "rgb":
            where = "" if self.place_types else " (Image type, step 7)"
            return f"Answered colour (RGB){where}: its channels are R, G and B, nothing to tag."
        if self.place_types:
            return "Colour is pre-ticked from the header: confirm it with the button below."
        return "Colour is pre-ticked from the header: confirm it on Image type (step 7)."

    def _pending(self, layout: UnitLayout) -> bool:
        """Return True when the unit shows a proposal not saved yet.

        The colour pre-tick counts only when the image type is answered here.
        """
        _, proposed = self.effective(layout)
        return bool(proposed) or (self.place_types and self.types.proposed(layout))

    def _answer_row(self, units: Sequence[UnitLayout], width: int) -> widgets.Widget:
        """Build the block's answer: one dropdown per C index, for every folder of the block."""
        first = units[0]
        tags, proposed = self.effective(first)
        what = "these folders" if len(units) > 1 else "this folder"
        cells: list[widgets.Widget] = [widgets.HTML(f"<b>Tags</b> of {what}")]
        for index in range(width):
            if index >= first.channels:
                cells.append(widgets.HTML(""))
                continue
            cells.append(self._dropdown(units, index, tags[index], index in proposed))
        cells.append(widgets.HTML(""))
        return widgets.GridBox(cells, layout=columns(width))

    def _folder_row(
        self, layout: UnitLayout, units: Sequence[UnitLayout], width: int, colour: bool
    ) -> widgets.Widget:
        """Build one folder's row of pictures, with its split button and image type."""
        extra: list[widgets.Widget] = []
        if len(units) > 1 and not colour:
            button = ui.button(" Tag this folder on its own", icon="scissors")
            button.on_click(lambda _, k=layout.key: self.split(k))
            self.split_buttons[layout.key] = button
            extra.append(button)
        typed = layout.usable and self.place_types
        last = self.types.cell(layout) if typed else widgets.HTML("")
        return self.rows.row(layout, width, colour, extra, last)

    def _dropdown(
        self, units: Sequence[UnitLayout], index: int, tag: str | None, proposed: bool
    ) -> widgets.Widget:
        """Build the dropdown of one C index: the labels and what is shown now."""
        options = [(CHOOSE, None), *((label, label) for label in self.labels)]
        value = tag if tag in {o[1] for o in options} else None
        drop = widgets.Dropdown(options=options, value=value, layout=widgets.Layout(width="99%"))
        drop.observe(lambda change, u=units, i=index: self._tagged(u, i, change["new"]), "value")
        for layout in units:
            self.cells[(layout.key, index)] = drop
        if tag is None:
            mark = blank_chip("not tagged")
        else:
            mark = chip(tag, self.labels, self._shown_as(tag))
            if proposed:
                mark += " <span class='mb-help'>proposed, not confirmed</span>"
        return widgets.VBox([drop, widgets.HTML(mark)])

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

    def _shown_as(self, label: str) -> str | None:
        """Return the shown-as name of a saved label (the chip's tooltip), if it has one."""
        saved = self.store.answers().channels.get(label)
        return None if saved is None else saved.display_name

    def _usage_line(self, label: str, used: int, total: int) -> str:
        """Return the usage line of a label; one used in only some units is orange."""
        mark = chip(label, self.labels, self._shown_as(label))
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


__all__ = ["TagGrid", "folder_order", "named", "partial_text"]
