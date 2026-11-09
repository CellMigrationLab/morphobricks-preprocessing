"""Widgets and plain-language texts of Step 1's description cards: names, IDs and references.

The cards show what the draft (:mod:`.draft`) proposes and collect the user's answers; they
compute nothing about identity themselves. Every rule that refuses an ID is the reader's, applied
by :meth:`.draft.Draft.confirm_id`; this module only says its codes in plain words.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

import ipywidgets as widgets

from . import notebook_style as ui
from .description_identity import nfc
from .draft import DraftError, DraftMember, IdSlot
from .draft_ids import IdKind, id_problems

ID_COLUMNS = widgets.Layout(
    grid_template_columns="minmax(160px, 2.2fr) minmax(120px, 1.3fr) minmax(120px, 1.3fr) 110px",
    grid_gap="6px 12px",
    width="99%",
    align_items="center",
)
REFERENCE_COLUMNS = widgets.Layout(
    grid_template_columns="minmax(140px, 1fr) minmax(160px, 1.5fr) minmax(160px, 2fr)",
    grid_gap="6px 12px",
    width="99%",
    align_items="center",
)

KIND_TITLES = {
    "conditions": "Conditions",
    "repeats": "Biological repeats",
    "technical_repeats": "Technical repeats (folders inside a repeat)",
}
KIND_NAMES = {
    "conditions": "condition",
    "repeats": "repeat",
    "technical_repeats": "technical repeat",
}


@dataclass(frozen=True)
class Entry:
    """One row of the names card: a condition, a repeat or a technical-repeat folder."""

    kind: IdKind
    key: str
    slot: IdSlot
    where: str
    """What the user sees: the folder or the folders joined into the repeat, with counts."""
    shown: str | None = None

    @property
    def fixed(self) -> bool:
        """True for the folder level that does not exist (``single``): its ID is ``single``."""
        return self.kind != "repeats" and self.key == "single"


def _id_reason(code: str) -> str | None:
    """Return the plain reason for a rule that refuses an ID, None for another code."""
    if code == "E_ID_TEXT":
        return "an ID cannot be empty, start or end with a space, or be a placeholder like NA"
    if code == "E_ID_NOT_NFC":
        return "the characters are not in Unicode normal form (NFC): retype the ID"
    if code == "E_SINGLE_MISUSE":
        return "single is only for a folder level that does not exist"
    if code == "E_FORBIDDEN_FIELD":
        return "this word is reserved by the file format"
    if code == "R_USED_ID_REUSED":
        return "this ID was used for something else earlier and is retired"
    if code == "E_ID_CASE_COLLISION":
        return "another ID differs from this one only by capital letters"
    return "another one already has this ID" if code == "id_in_use_in_draft" else None


def plain(code: str) -> str:
    """Return the plain-language reason for a draft refusal (the code itself if unknown).

    A presentation of the codes the reader and the draft raise; the rules are theirs.
    """
    if reason := _id_reason(code):
        return reason
    if code == "unknown_key":
        return "this folder is no longer in the experiment"
    if code == "E_REFERENCE_SELF":
        return "a condition cannot be compared against itself"
    return "conditions cannot be compared in a loop" if code == "E_REFERENCE_CYCLE" else code


def pill(kind: str, text: str) -> str:
    """HTML of a small coloured state label."""
    return f"<span class='mb-pill mb-pill-{kind}'>{html.escape(text)}</span>"


def members_text(members: tuple[DraftMember, ...]) -> str:
    """HTML for the folders of a repeat with their image counts, e.g. ``ctrl/rep1 (48)``."""
    parts = [
        f"{html.escape('/'.join(m.key))} <span class='mb-help'>({m.image_count})</span>"
        for m in members
    ]
    return "<br>".join(parts)


def id_state(entry: Entry, text: str) -> str:
    """HTML of the state of one ID box: confirmed, proposed, typed or still to name."""
    if entry.slot.confirmed is not None:
        return pill("ok", "confirmed")
    if not text:
        return pill("warn", "needs a name")
    if text == entry.slot.proposed:
        return pill("info", "proposed")
    return pill("warn", "typed")


def why_no_proposal(entry: Entry) -> str:
    """HTML saying why the folder cannot be its own ID, empty when it can."""
    if entry.slot.proposed is not None or entry.fixed:
        return ""
    reasons = id_problems(entry.kind, entry.key) or ("this is already another ID",)
    text = "; ".join(plain(code) for code in reasons)
    return f"<div class='mb-help'>cannot be its own ID: {html.escape(text)}</div>"


def refusal_card(title: str, problems: list[tuple[str, DraftError]]) -> str:
    """HTML of an error card with, for each refused answer, why in words and the reader's text."""
    lines = []
    for subject, error in problems:
        reasons = "; ".join(plain(code) for code in error.codes) or "refused"
        lines.append(
            f"<b>{html.escape(subject)}</b>: {html.escape(reasons)}."
            f"<div class='mb-help'>{html.escape(str(error))}</div>"
        )
    return ui.status("error", title, lines, raw=True)


def text_box(
    value: str, placeholder: str, *, disabled: bool = False, live: bool = True
) -> widgets.Text:
    """Build a one-line text box that keeps every character as typed."""
    return widgets.Text(
        value=value,
        placeholder=placeholder,
        disabled=disabled,
        continuous_update=live,
        layout=widgets.Layout(width="99%"),
    )


def header_row(*titles: str, layout: widgets.Layout = ID_COLUMNS) -> widgets.GridBox:
    """Build the bold header line of a grid."""
    return widgets.GridBox(
        [widgets.HTML(f"<b>{html.escape(t)}</b>") for t in titles], layout=layout
    )


def same_name(first: str, second: str) -> bool:
    """Return True when two shown-as names are equal ignoring case (empty names never are)."""
    return bool(first) and nfc(first).casefold() == nfc(second).casefold()


def duplicate_hint(kind: str, text: str, others: list[str]) -> str:
    """HTML of the small orange hint for a shown-as name another one of its kind has."""
    if not others:
        return ""
    names = ", ".join(html.escape(o) for o in others)
    return (
        "<div class='mb-help' style='color:#C2410C'>"
        f"The name {html.escape(repr(text))} is also shown for {KIND_NAMES[kind]} {names}. "
        "Names shown in plots must differ (the review refuses duplicates).</div>"
    )


def single_note(entries: list[Entry]) -> str:
    """HTML in bold naming the folder levels that do not exist (they get ``single``), or empty."""
    missing = [
        text
        for kind, text in (
            ("conditions", "no condition folders"),
            ("technical_repeats", "no technical-repeat folders"),
        )
        if any(e.kind == kind and e.fixed for e in entries)
    ]
    if not missing:
        return ""
    some = any(e.kind == "technical_repeats" and not e.fixed for e in entries)
    if some and missing == ["no technical-repeat folders"]:
        return (
            "<div class='mb-question'><b>Some biological-repeat folders have no "
            "technical-repeat folders: their images get the technical-repeat ID single.</b></div>"
        )
    return (
        "<div class='mb-question'><b>Your experiment has "
        f"{' and '.join(missing)}: they get the ID single.</b></div>"
    )
