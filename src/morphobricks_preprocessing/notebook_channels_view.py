"""Colours and small HTML pieces shared by the *Your channels* cards.

Each label has one colour chip, taken from its place in the sorted label list, used by the label
list, the column of the grid and every cell. Orange is kept for what needs attention (an untagged
cell, a label used in only some units) and is not in the palette.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Protocol

import ipywidgets as widgets

from .draft_answers import DraftAnswers
from .strict_draft import Change

PALETTE = (
    "#1D4ED8",
    "#0F766E",
    "#7E22CE",
    "#BE185D",
    "#4D7C0F",
    "#0E7490",
    "#4338CA",
    "#475569",
    "#A21CAF",
    "#166534",
)
ORANGE = "#D97706"
ORANGE_FILL = "#FFFBEB"
CHOOSE = "— tag this channel —"
COLUMNS_PREFIX = "minmax(150px, 190px)"


class AnswerStore(Protocol):
    """Where the cards read and save the draft answers (the notebook gives one).

    Each card reads its own slice from :meth:`answers` and saves a user's change with
    :func:`save_change` (the change as a function of the answers); they know nothing of each
    other, so a later layout can put them on separate steps.

    The notebook's store also keeps the strict page's draft of fast mode
    (:mod:`.strict_draft`): it has ``change(change)`` (a user's change, applied to the draft and
    to the strict page's draft), ``unattended(new)`` (a save with no click, the draft only) and
    ``restore_strict()`` (the statement unticked: the strict page's draft written back). A store
    without them (an in-memory one) has no strict page apart: :meth:`update` saves.
    """

    def answers(self) -> DraftAnswers:
        """Return the current answers."""
        ...

    def update(self, new: DraftAnswers) -> bool:
        """Save the new answers; False when they are refused (see :meth:`refusal`)."""
        ...

    def refusal(self) -> str:
        """Return the reason the last update was refused, in words."""
        ...

    def used_channels(self) -> tuple[str, ...]:
        """Return the channel IDs the experiment's lineage retired (never reusable)."""
        ...


def save_change(store: AnswerStore, change: Change) -> bool:
    """Save a user's change of the answers; False when it is refused.

    The store's ``change`` applies it to the draft and to the strict page's draft; a store
    without one saves ``change(answers)``. ``ValueError`` from the change is the caller's.
    """
    apply = getattr(store, "change", None)
    if apply is not None:
        return bool(apply(change))
    return store.update(change(store.answers()))


def save_unattended(store: AnswerStore, new: DraftAnswers) -> bool:
    """Save answers made with no click (fast mode's trusted headers): the draft only."""
    apply = getattr(store, "unattended", None)
    return bool(apply(new)) if apply is not None else store.update(new)


def restore_strict(store: AnswerStore) -> bool:
    """Write the strict page's draft back (the statement unticked); False if refused."""
    apply = getattr(store, "restore_strict", None)
    return bool(apply()) if apply is not None else True


def colour(label: str, labels: Sequence[str]) -> str:
    """Return the chip colour of a label: its place in the sorted list, cycling the palette."""
    order = sorted(labels)
    return PALETTE[(order.index(label) if label in order else 0) % len(PALETTE)]


def chip(label: str, labels: Sequence[str], display: str | None = None) -> str:
    """Return the colour chip of a label as HTML: always the ID, the shown-as name as tooltip."""
    tip = f" title='{html.escape(display, quote=True)}'" if display else ""
    return (
        f"<span{tip} style='display:inline-block;padding:1px 9px;border-radius:999px;color:#fff;"
        f"font-weight:600;background:{colour(label, labels)}'>{html.escape(label)}</span>"
    )


RGB_COLOURS = {"R": "#DC2626", "G": "#16A34A", "B": "#2563EB"}
"""The chips of a colour image's fixed channels (never labels) are drawn in their colour."""


def rgb_chip(token: str) -> str:
    """Return the chip of a colour channel (``R``, ``G``, ``B``) in red, green or blue."""
    return (
        "<span style='display:inline-block;padding:1px 9px;border-radius:999px;color:#fff;"
        f"font-weight:600;background:{RGB_COLOURS[token]}'>{html.escape(token)}</span>"
    )


def blank_chip(text: str) -> str:
    """Return an orange outlined chip for something not answered."""
    return (
        f"<span style='display:inline-block;padding:0 9px;border-radius:999px;color:#92400E;"
        f"font-weight:600;background:{ORANGE_FILL};border:1px dashed {ORANGE}'>"
        f"{html.escape(text)}</span>"
    )


def quoted(name: str | None) -> str:
    """Return a raw header name quoted so outer spaces show, or a note that there is none."""
    if not name:
        return "<i>no header name</i>"
    return f"&lsquo;{html.escape(name).replace(' ', '&nbsp;')}&rsquo;"


def columns(count: int) -> widgets.Layout:
    """Return the grid layout of the tag grid: unit, one column per C index, image type."""
    return widgets.Layout(
        grid_template_columns=f"{COLUMNS_PREFIX} repeat({max(count, 1)}, minmax(120px, 1fr)) "
        "minmax(150px, 190px)",
        grid_gap="8px 10px",
        width="99%",
        align_items="flex-start",
    )


def unit_name(key: tuple[str, str, str]) -> str:
    """Return ``condition / repeat / technical repeat`` of a unit; ``single`` is left out."""
    return " / ".join(part for part in key if part != "single") or "single"


__all__ = [
    "CHOOSE",
    "AnswerStore",
    "ORANGE",
    "ORANGE_FILL",
    "PALETTE",
    "RGB_COLOURS",
    "blank_chip",
    "chip",
    "colour",
    "columns",
    "quoted",
    "restore_strict",
    "rgb_chip",
    "save_change",
    "save_unattended",
    "unit_name",
]
