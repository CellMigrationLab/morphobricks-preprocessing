"""The Channels step's question about confirmed channels at new positions (issue #258).

When the user re-tags a saved unit's channels in another order (the images were re-exported with
the channels re-ordered), each confirmed channel ID that moved is listed under the tag grid with
one explicit question. Only the user's click confirms the new positions; publication is then
given one re-confirmation per moved channel (experiment-v1 section 4.6: old ID, ``new_id`` null,
the date of the click) and accepts the change (:mod:`.channel_moves`). A move that changes again
after the click is asked again; nothing is ticked for the user.
"""

from __future__ import annotations

import html
from dataclasses import replace
from datetime import date

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_moves import ChannelMove, channel_moves, move_confirmations
from .description import Description
from .description_transition import Reconfirmation
from .draft import DraftError
from .draft_answers import description_from_draft
from .notebook_channels_view import ORANGE
from .validation import plural

MOVED_QUESTION = "Are these the new positions of these channels?"
MOVED_HELP = (
    "Answer yes only if the images now hold these channels in this order (re-exported, for "
    "example). Recorded when you save."
)
_PLACEHOLDER_DAY = "2000-01-01"
"""A date for the replicates still to answer, used only to compare positions; never saved."""


def _where(unit: tuple[str, str, str]) -> str:
    return "/".join(part for part in unit if part != "single")


def moves_html(moves: tuple[ChannelMove, ...]) -> str:
    """Return one orange line per moved position: the channel, its folder, from and to."""
    lines = [
        f"{html.escape(move.channel)}: {html.escape(_where(m.unit))} channel {m.saved + 1} → "
        + (f"channel {m.now + 1}" if m.now is not None else "not in it any more")
        for move in moves
        for m in move.moved
    ]
    return "".join(f"<div style='color:{ORANGE}'>{line}</div>" for line in lines)


class _ChannelMoves:
    """The moved-channels question under the tag grid, and its re-confirmations."""

    def _build_channel_moves(self) -> None:
        self._moves_clicked: dict[ChannelMove, str] = {}
        self._moves_shown: tuple[ChannelMove, ...] = ()
        self.moves_list = widgets.HTML()
        self.moves_confirm = ui.ConfirmToggle(
            MOVED_QUESTION, MOVED_HELP, "Yes, these are the new positions", "", "Undo"
        )
        self.moves_confirm.observe(self._moves_answered)
        self.moves_box = widgets.VBox([self.moves_list, self.moves_confirm.widget])
        ui.set_visible(self.moves_box, False)

    def _moves_answered(self, value: bool) -> None:
        """Record (or take back) the user's confirmation of the moves shown now."""
        day = date.today().isoformat()
        for move in self._moves_shown:
            if value:
                self._moves_clicked[move] = day
            else:
                self._moves_clicked.pop(move, None)
        self._refresh_check_save()

    def _moves_of(self, state: object, candidate: Description) -> tuple[ChannelMove, ...]:
        """Return the moves of ``candidate`` that no removal or rename answer already covers."""
        if state is None:
            return ()
        accepted = self._removal_terms(state, candidate)[1]
        return channel_moves(state.description, candidate, accepted)

    def _move_terms(self, state: object, candidate: Description) -> tuple[Reconfirmation, ...]:
        """Return the re-confirmations of the moves the user confirmed (for publication)."""
        return move_confirmations(self._moves_of(state, candidate), self._moves_clicked)

    def _refresh_channel_moves(self) -> None:
        """Show the moved channels of the answers now and whether the user confirmed them."""
        moves: tuple[ChannelMove, ...] = ()
        state = self._published() if self.draft_answers is not None else None
        if state is not None and self._draft_base is not None:
            answers = self.draft_answers
            # The positions do not depend on the "same way?" dates: a replicate still to answer
            # gets a placeholder here, so the moves show while it is open. Nothing is saved.
            repeats = {
                key: entry if entry.confirmed_on else replace(entry, confirmed_on=_PLACEHOLDER_DAY)
                for key, entry in answers.repeats.items()
            }
            try:
                candidate = description_from_draft(
                    self._draft_base,
                    replace(answers, repeats=repeats),
                    channel_counts=self._channel_counts(),
                )
                moves = self._moves_of(state, candidate)
            except DraftError:
                moves = ()  # other answers missing or refused: the tags say so
        self._moves_shown = moves
        done = bool(moves) and all(move in self._moves_clicked for move in moves)
        days = sorted({self._moves_clicked[m] for m in moves if m in self._moves_clicked})
        self.moves_confirm.confirmed_text = f"Confirmed on {', '.join(days)}." if done else ""
        self.moves_confirm.set(done, notify=False)
        title = f"{plural(len(moves), 'saved channel')} moved"
        self.moves_list.value = ui.status("warn", title) + moves_html(moves) if moves else ""
        ui.set_visible(self.moves_box, bool(moves))


__all__ = ["MOVED_HELP", "MOVED_QUESTION", "_ChannelMoves", "moves_html"]
