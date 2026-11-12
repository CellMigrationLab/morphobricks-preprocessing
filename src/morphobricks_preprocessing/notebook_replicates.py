"""The replicate boxes of *Check and save*: folders, pictures, channels and "same way?".

One box per replicate (biological repeat): its folders with a picture of each folder's first
image, the channels, what an acquisition lacks, and the replicate's own comparability question
(the N35 answer, :data:`.notebook_check_view.SAME_WAY`). Moved out of
:mod:`.notebook_check_save` (code rules: 600 lines a module).

Every answer anywhere in the notebook refreshes *Check and save*, so the boxes are rebuilt only
when something they show changed (owner, 12 November 2026: *Looks right: confirm the design*
rebuilt every box twice and seemed to do nothing on large experiments).
"""

from __future__ import annotations

import html

import ipywidgets as widgets

from . import notebook_style as ui
from .description_review import replicates
from .notebook_channel_thumbs import Thumb
from .notebook_check_view import (
    SAME_WAY,
    SAME_WAY_HELP,
    member_html,
    missing_html,
    replicate_title,
)
from .notebook_row_fold import RowFold, row_status
from .notebook_thumb_tile import ThumbTile, show_detail
from .preview_crop import TilePictures


def _enlarge(detail: widgets.VBox, title: str, found: TilePictures | Thumb) -> None:
    """Show a folder's enlarged picture in ``detail``, or why there is none."""
    if isinstance(found, Thumb):
        show_detail(detail, title, None, found.note)
    else:
        show_detail(detail, title, found, "")


def _same(before: tuple, now: tuple) -> bool:
    """Return True when two states of the replicate boxes are the same.

    The pictures and the folders' layouts are compared by identity, the rest by value.
    """
    return before[0] is now[0] and before[1] is now[1] and before[2:] == now[2:]


class _ReplicateBoxes:
    """One box per replicate on *Check and save*, rebuilt only when what it shows changed."""

    def _show_replicates(self, applied: object, answers: object, labels: list[str]) -> None:
        """One box per replicate: folders, pictures, channels, and its own question."""
        boxes, toggles = [], {}
        units, counts = self._reopen_units(), self._channel_counts()
        state = self._published()
        saved_dates = (
            {}
            if state is None
            else {key: r.confirmed_on for key, r in state.description.repeats.items()}
        )
        views = replicates(applied, answers)
        shown_as = self._replicates_state(views, labels, counts, units, saved_dates)
        built = getattr(self, "_replicates_built", None)
        if built is not None and built[0] is self.same_way and _same(built[1], shown_as):
            return  # the boxes on the page show exactly this (each answer refreshes the screen)
        for view in views:
            toggles[view.label], box = self._replicate_box(view, labels, counts)
            keys = [u.key for m in view.members for u in m.units]
            # A box with a warning (an acquisition lacking a label) never folds, nor one whose
            # date is not the published one (publication's R_CONFIRMED_ON_CHANGED names it).
            settled = (
                view.confirmed_on is not None
                and not view.missing
                and view.confirmed_on == saved_dates.get(view.label, view.confirmed_on)
            )
            boxes.append((box, row_status(units, keys, settled)))
        self.same_way = toggles
        shown = (
            self._channel_thumbs,
            [[(m.condition, m.source_repeat) for m in v.members] for v in views],
        )
        if shown != getattr(self, "_replicates_shown", None):  # other folders or pictures (#252)
            self.replicates_detail.children = []  # an Enlarge of the boxes before is closed
        self._replicates_shown = shown
        if not hasattr(self, "_replicate_fold"):
            self._replicate_fold = RowFold("replicate")
        self.replicates_box.children = self._replicate_fold.arrange(boxes)
        self._replicates_built = (toggles, shown_as)

    def _member_picture(self, key: tuple[str, str], detail: widgets.VBox) -> widgets.Widget:
        """Return the picture of a folder's first image (channel 1, or in colour when RGB).

        A large image gets its crop at native pixels beside it; **Enlarge** shows both in
        ``detail``.
        """
        thumbs, layouts = self._channel_thumbs, self._channel_layouts
        layout = next((u for u in layouts if u.key[:2] == key), None)
        if thumbs is None or layout is None:
            return widgets.HTML(
                "<div class='mb-help' style='width:120px'>picture once the axis roles are "
                "confirmed</div>"
            )
        colour = self.channel_types.shown(layout) == "rgb"
        found = thumbs.tile(layout, colour)
        if isinstance(found, Thumb):
            return ThumbTile(None, problem=found.note, width="120px").widget
        title = f"<b>{html.escape('/'.join(key))}</b> · {html.escape(layout.image or '')}"
        return ThumbTile(
            found,
            colour=colour,
            enlarge=lambda: _enlarge(detail, title, thumbs.enlarged(layout, colour)),
            width="120px",
        ).widget

    def _replicate_box(
        self, view: object, labels: list[str], counts: object
    ) -> tuple[ui.ConfirmToggle, widgets.VBox]:
        """Return one replicate's "same way?" question and its box (folders, pictures)."""
        toggle = ui.ConfirmToggle(
            SAME_WAY,
            SAME_WAY_HELP,
            "Yes, the same way",
            f"Confirmed on {view.confirmed_on}: one replicate." if view.confirmed_on else "",
            "Undo",
        )
        toggle.set(view.confirmed_on is not None, notify=False)
        toggle.observe(lambda value, label=view.label: self._same_way(label, value))
        members = [
            widgets.HBox(
                [
                    self._member_picture((m.condition, m.source_repeat), self.replicates_detail),
                    widgets.HTML(member_html(m, labels, counts)),
                ]
            )
            for m in view.members
        ]
        body = [widgets.HTML(replicate_title(view)), *members, widgets.HTML(missing_html(view))]
        box = widgets.VBox(
            [*body, toggle.widget],
            layout=widgets.Layout(
                border="1px solid #E2E8F0", padding="8px 12px", margin="0 0 10px 0"
            ),
        )
        return toggle, box

    def _replicates_state(
        self, views: tuple, labels: list[str], counts: object, units: object, dates: dict
    ) -> tuple:
        """Return everything the replicate boxes show, to tell whether they must be rebuilt."""
        layouts = self._channel_layouts
        return (
            self._channel_thumbs,
            layouts,
            self._cards_show_pictures(),
            tuple(self.channel_types.shown(u) for u in layouts or ()),
            views,
            tuple(labels),
            None if counts is None else tuple(sorted(counts.items())),
            None if units is None else tuple(sorted(units.items())),
            tuple(sorted(dates.items())),
        )


__all__ = ["_ReplicateBoxes"]
