"""The Channels step's questions about saved channels that moved or lost a header name.

When the user re-tags a saved unit's channels in another order (the images were re-exported with
the channels re-ordered), each confirmed channel ID that moved is listed under the tag grid with
one explicit question (issue #258). When a re-tag leaves a confirmed channel without a header name
it had (the images now give another name or none, or the folder that gave it is gone), that
channel is listed under a second question (issue #281). Only the user's click confirms either;
publication is then given one re-confirmation per channel (experiment-v1 section 4.6: old ID,
``new_id`` null, the date of the click) and accepts the change (:mod:`.channel_moves`). A change
that differs after the click is asked again; nothing is ticked for the user. No removal or rename
answer confirms a lost header name: the published file keeps no names per folder, so it could
not show that the name went with the removed folder.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import astuple, replace
from datetime import date

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_moves import (
    ChannelMove,
    NameChange,
    channel_moves,
    move_confirmations,
    name_changes,
)
from .description import Description
from .description_removal import reconfirmations
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
NAMES_QUESTION = "Did these header names change?"
NAMES_HELP = (
    "The saved header names of these channels are no longer all given. Answer yes only if the "
    "images now give these names (or none). Recorded when you save."
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


def _names(names: tuple[str, ...]) -> str:
    return ", ".join(f"“{name}”" for name in names) or "none"


def name_change_text(change: NameChange) -> str:
    """Return what changed, in plain words: the channel, its header names saved and now."""
    word = "header name" if len(change.saved) == 1 else "header names"
    return f"{change.channel}: {word} {_names(change.saved)} → {_names(change.now)}"


def names_html(changes: tuple[NameChange, ...]) -> str:
    """Return one orange line per channel that lost a saved header name."""
    return "".join(
        f"<div style='color:{ORANGE}'>{html.escape(name_change_text(c))}</div>" for c in changes
    )


class _Question:
    """One list of changes under the tag grid, its explicit question, and the user's clicks.

    ``clicked`` maps each change the user confirmed, exactly as it was shown, to the date of
    the click; a change that differs since is not in it, so it is asked again.
    """

    def __init__(self, question: str, help_text: str, label: str, changed: Callable) -> None:
        self.clicked: dict = {}
        self.shown: tuple = ()
        self.list = widgets.HTML()
        self.confirm = ui.ConfirmToggle(question, help_text, label, "", "Undo")
        self.confirm.observe(self._answered)
        self.box = widgets.VBox([self.list, self.confirm.widget])
        self._changed = changed
        ui.set_visible(self.box, False)

    def _answered(self, value: bool) -> None:
        """Record (or take back) the user's confirmation of the changes shown now."""
        day = date.today().isoformat()
        for change in self.shown:
            if value:
                self.clicked[change] = day
            else:
                self.clicked.pop(change, None)
        self._changed()

    def open(self) -> bool:
        """Return True while a change shown is not confirmed (Save waits for it)."""
        return any(change not in self.clicked for change in self.shown)

    def show(self, changes: tuple, title: str, lines: str) -> None:
        """Show ``changes`` (none hides the question) and whether the user confirmed them."""
        self.shown = changes
        done = bool(changes) and not self.open()
        days = sorted({self.clicked[c] for c in changes if c in self.clicked})
        self.confirm.confirmed_text = f"Confirmed on {', '.join(days)}." if done else ""
        self.confirm.set(done, notify=False)
        self.list.value = ui.status("warn", title) + lines if changes else ""
        ui.set_visible(self.box, bool(changes))


class _ChannelMoves:
    """The moved-channels and header-name questions under the tag grid, and their records."""

    def _build_channel_moves(self) -> None:
        """Build both questions, hidden until a saved channel moved or lost a header name."""
        refresh = self._refresh_check_save
        self._moves = _Question(
            MOVED_QUESTION, MOVED_HELP, "Yes, these are the new positions", refresh
        )
        self._names = _Question(
            NAMES_QUESTION, NAMES_HELP, "Yes, this header name changed", refresh
        )
        self.moves_list, self.moves_confirm, self.moves_box = (
            self._moves.list,
            self._moves.confirm,
            self._moves.box,
        )
        self.names_list, self.names_confirm, self.names_box = (
            self._names.list,
            self._names.confirm,
            self._names.box,
        )

    @property
    def _moves_clicked(self) -> dict:
        return self._moves.clicked

    @property
    def _moves_shown(self) -> tuple:
        return self._moves.shown

    @property
    def _names_clicked(self) -> dict:
        return self._names.clicked

    def _forget_channel_clicks(self) -> None:
        """Forget the clicks once saved: they were recorded against the file replaced."""
        self._moves.clicked.clear()
        self._names.clicked.clear()

    def _moves_of(self, state: object, candidate: Description) -> tuple[ChannelMove, ...]:
        """Return the moves of ``candidate`` (a removal or rename answer confirms none)."""
        return () if state is None else channel_moves(state.description, candidate)

    def _names_of(self, state: object, candidate: Description) -> tuple[NameChange, ...]:
        """Return the header-name changes of ``candidate`` (no other answer confirms one)."""
        return () if state is None else name_changes(state.description, candidate)

    def _moves_open(self) -> bool:
        """Return True while a move or header-name change on Channels is not confirmed."""
        return self._moves.open() or self._names.open()

    def _names_confirmed(self, state: object, candidate: Description) -> set[str]:
        """Return the channels whose header-name change the user confirmed exactly as shown."""
        return {c.channel for c in self._names_of(state, candidate) if c in self._names.clicked}

    def _move_terms(self, state: object, candidate: Description) -> tuple[Reconfirmation, ...]:
        """Return the re-confirmations of the moves and header-name changes the user confirmed.

        An ID a removal or rename answer (or a move) already re-confirms keeps that one record
        (one line per ID); each click is still required before saving (:meth:`_moves_open`).
        """
        taken = {(c.kind, c.old_id) for c in self._removal_terms(state, candidate)[1]}
        out = []
        for changes, clicked in (
            (self._moves_of(state, candidate), self._moves.clicked),
            (self._names_of(state, candidate), self._names.clicked),
        ):
            for record in move_confirmations(changes, clicked):
                if (record.kind, record.old_id) not in taken:
                    taken.add((record.kind, record.old_id))
                    out.append(record)
        return tuple(out)

    def _open_change_lines(self) -> list[str]:
        """Say which change on Channels still waits for its click (beside the Save button)."""
        lines = ["the moved channels are not confirmed: see Channels"] * self._moves.open()
        return lines + [
            f"{name_change_text(c)} is not confirmed: see Channels (Yes, this header name changed)"
            for c in self._names.shown
            if c not in self._names.clicked
        ]

    def _unseen_lines(self) -> list[str]:
        """Say which channel lost a position in a folder still there that no answer confirms.

        A removal or rename answer re-confirms a channel ID, never one folder of it, so that
        change keeps the ID refused until it is undone; nothing is confirmed for the user.
        """
        state, check = self._published(), self._check
        if state is None or check is None or check.description is None:
            return []
        candidate = check.description
        found = reconfirmations(
            state.description,
            candidate,
            state.reconciliation,
            self._removal_terms(state, candidate)[0],
            self._names_confirmed(state, candidate),
        )
        kept = {c.old_id for c in self._publication_terms(state, candidate)[1]}
        return [
            f"{channel} lost a saved {what} in {_where(astuple(folders))}, which is still there:"
            " a removal or rename does not confirm it (undo that tag on Channels)"
            for channel, folders, what in found.unseen
            if channel not in kept
        ]

    def _refresh_channel_moves(self) -> None:
        """Show the moved channels and lost header names of the answers now, and the clicks."""
        moves: tuple[ChannelMove, ...] = ()
        names: tuple[NameChange, ...] = ()
        state = self._published() if self.draft_answers is not None else None
        if state is not None and self._draft_base is not None:
            answers = self.draft_answers
            # The positions and names do not depend on the "same way?" dates: a replicate still
            # to answer gets a placeholder here, so the changes show while it is open. Nothing
            # is saved.
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
                names = self._names_of(state, candidate)
            except DraftError:
                pass  # other answers missing or refused: the tags say so
        title = f"{plural(len(moves), 'saved channel')} moved"
        self._moves.show(moves, title, moves_html(moves))
        title = f"{plural(len(names), 'saved channel')} lost a header name"
        self._names.show(names, title, names_html(names))


__all__ = [
    "MOVED_HELP",
    "MOVED_QUESTION",
    "NAMES_HELP",
    "NAMES_QUESTION",
    "_ChannelMoves",
    "moves_html",
    "name_change_text",
    "names_html",
]
