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

    Each card reads its own slice from :meth:`answers` and saves a changed copy with
    :meth:`update`; they know nothing of each other, so a later layout can put them on separate
    steps.
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
    "rgb_chip",
    "unit_name",
]
