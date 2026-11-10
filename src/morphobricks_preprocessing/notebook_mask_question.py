"""One mask type's question on the *Your masks* screen (a self-contained widget).

"What is this mask?" with two answers and **no default**: *separate objects, each with its own
number* (``instances``) or *regions: inside or outside* (``binary``), beside the evidence (how
many images have it, its pixel type, the number of different values in one image, the mask
drawn on that image). The mask ID is the folder name when it is a valid mask ID; otherwise the
box is empty and says why, and the user types one. Optional *made from* channels are the declared
labels, offered as toggles once the question is answered. Every answer is saved through the
:class:`.notebook_channels_view.AnswerStore` at once; a refused answer is taken back and said.
"""

from __future__ import annotations

import html
from collections.abc import Callable

import ipywidgets as widgets

from . import notebook_style as ui
from .mask_answers import (
    BINARY,
    INSTANCES,
    MaskAnswerRefused,
    mask_of_type,
    proposed_mask_id,
    with_mask,
)
from .mask_evidence import MaskEvidence
from .notebook_channels_view import AnswerStore, blank_chip, chip
from .notebook_description_view import plain, text_box
from .validation import plural

OPTIONS = [
    ("separate objects, each with its own number", INSTANCES),
    ("regions: inside or outside", BINARY),
]
PICTURE = widgets.Layout(width="49%", max_width="256px", border="1px solid #CBD5E1")


def evidence_text(found: MaskEvidence) -> str:
    """Return the evidence line: images with the mask, pixel type, values in one image."""
    parts = [f"{found.images_with} of {plural(found.images, 'image')} have one"]
    if found.dtypes:
        parts.append(" / ".join(found.dtypes))
    if found.values is not None and found.image:
        parts.append(f"{plural(found.values, 'different value')} in {found.image}")
    return " · ".join(html.escape(p) for p in parts)


MASK_REASONS = {
    "E_ID_SYNTAX": "letters, digits and single underscores only, starting with a letter",
    "E_ID_RESERVED": "a reserved name (such as cell or nucleus), or it has the part in",
}


def _id_reason(text: str, codes: tuple[str, ...]) -> str:
    """Return why a typed mask ID is refused, in words with each code."""
    if not text:
        return "type an ID: letters, digits and single underscores, starting with a letter"
    return "; ".join(f"{MASK_REASONS.get(code) or plain(code)} ({code})" for code in codes)


class MaskQuestion:
    """The question about one mask type, with its evidence and its saved answer."""

    def __init__(
        self,
        store: AnswerStore,
        source_type: str,
        evidence: Callable[[str], MaskEvidence],
        on_change: Callable[[], None],
    ) -> None:
        self.store, self.source_type, self._on_change = store, source_type, on_change
        self._evidence, self._building = evidence, False
        saved = mask_of_type(store.answers(), source_type)
        proposal = proposed_mask_id(source_type, store.answers())
        self.id_box = text_box(saved[0] if saved else proposal or "", "type a mask ID")
        self.id_box.layout.width = "220px"
        self.id_state = widgets.HTML()
        self.choice = widgets.RadioButtons(
            options=OPTIONS, value=saved[1].representation if saved else None
        )
        self.made_from: dict[str, widgets.ToggleButton] = {}
        self.made_box = widgets.HBox(layout=widgets.Layout(flex_flow="wrap"))
        self.message = widgets.HTML()
        self.facts = widgets.HTML()
        self.pictures = widgets.HBox()
        self.pictures.add_class("mb-mask-pictures")  # drawn smaller in the stepper
        self.id_box.observe(lambda _: self._id_typed(), names="value")
        self.choice.observe(lambda change: self._chosen(change["new"]), names="value")
        self.widget = widgets.VBox(
            [
                self.facts,
                self.pictures,
                widgets.HBox([widgets.HTML("<b>Mask ID</b>&nbsp;"), self.id_box, self.id_state]),
                widgets.HTML("<b>What is this mask?</b>"),
                self.choice,
                widgets.HTML("<b>Made from</b> <span class='mb-help'>(optional)</span>"),
                self.made_box,
                self.message,
            ],
            layout=widgets.Layout(
                border="1px solid #E2E8F0", padding="8px 12px", margin="0 0 10px 0"
            ),
        )

    # State -------------------------------------------------------------------------------

    @property
    def saved(self) -> tuple[str, str, tuple[str, ...]] | None:
        """Return ``(mask ID, representation, made from)`` as saved, or None."""
        found = mask_of_type(self.store.answers(), self.source_type)
        if found is None:
            return None
        return found[0], found[1].representation, tuple(found[1].source_channels or ())

    def _chosen_channels(self) -> tuple[str, ...]:
        return tuple(sorted(label for label, box in self.made_from.items() if box.value))

    # Drawing -----------------------------------------------------------------------------

    def refresh(self, labels: list[str]) -> None:
        """Redraw the evidence, the pictures, the ID state and the *made from* toggles."""
        self._building = True
        try:
            saved = self.saved
            self.choice.value = saved[1] if saved else None
            self._build_made_from(labels, saved[2] if saved else ())
            self._show_evidence(saved[1] if saved else None, labels)
            self._show_id_state()
        finally:
            self._building = False

    def _build_made_from(self, labels: list[str], chosen: tuple[str, ...]) -> None:
        """One toggle per declared label; disabled until the question is answered."""
        self.made_from = {}
        for label in labels:
            box = widgets.ToggleButton(
                value=label in chosen,
                description=label,
                icon="check" if label in chosen else "",
                button_style="info" if label in chosen else "",
                disabled=self.saved is None,
                layout=widgets.Layout(width="auto"),
            )
            box.observe(lambda _: self._made_from_changed(), names="value")
            self.made_from[label] = box
        empty = "no channel labels yet: declare them on Channels"
        hint = "answer the question first" if labels and self.saved is None else ""
        tail = [
            widgets.HTML(f"<span class='mb-help'>{hint or (empty if not labels else '')}</span>")
        ]
        self.made_box.children = [*self.made_from.values(), *tail]

    def _show_evidence(self, representation: str | None, labels: list[str]) -> None:
        """Show the facts and the two pictures: the image, then the mask as declared."""
        found = self._evidence(self.source_type)
        title = f"<b style='font-size:1.05em'>{html.escape(self.source_type)}</b>"
        lines = [f"{title} <span class='mb-help'>{evidence_text(found)}</span>"]
        if found.problem:
            lines.append(ui.status("warn", "No picture.", [html.escape(found.problem)], raw=True))
        elif found.picture is not None:
            lines.append(
                f"<div class='mb-help'>{self._caption(found, representation, labels)}</div>"
            )
        self.facts.value = "".join(lines)
        image = found.image_png()
        drawn = found.overlay_png(representation) if representation else found.mask_png()
        self.pictures.children = [
            widgets.Image(value=png, format="png", layout=PICTURE) for png in (image, drawn) if png
        ]

    def _caption(self, found: MaskEvidence, representation: str | None, labels: list[str]) -> str:
        """Say which image, channel and plane are shown, and how the mask is drawn."""
        channel = "" if found.channel is None else f"channel {found.channel + 1}"
        if found.colour:
            channel = "in colour (channel 1 red, 2 green, 3 blue)"
        elif found.colour_problem:
            channel += f" (not in colour: {found.colour_problem})"
        on = chip(found.label, labels) + " " if found.label else ""
        how = {
            INSTANCES: "each object outlined in its own colour, as you declared",
            BINARY: "the inside tinted, as you declared",
        }.get(representation or "", "the mask alone in grey: answer the question to draw it")
        where = f"{html.escape(found.image or '')}, {on}{html.escape(channel)} "
        return f"Left: {where}({html.escape(found.plane)}). Right: {how}."

    def _show_id_state(self) -> None:
        """Say whether the ID in the box can be used, and where it came from."""
        text = self.id_box.value
        try:
            with_mask(self.store.answers(), self.source_type, text, INSTANCES)
        except MaskAnswerRefused as exc:
            self.id_state.value = blank_chip(_id_reason(text, exc.codes))
            return
        note = "the folder name" if text == self.source_type else "typed by you"
        self.id_state.value = f"<span class='mb-help'>&nbsp;{note}</span>"

    # Answers -----------------------------------------------------------------------------

    def _save(self, representation: str, channels: tuple[str, ...]) -> bool:
        """Save the answer; say and return False when it is refused."""
        try:
            new = with_mask(
                self.store.answers(), self.source_type, self.id_box.value, representation, channels
            )
        except MaskAnswerRefused as exc:
            self.message.value = ui.status("error", "Not taken.", [str(exc)])
            return False
        if not self.store.update(new):
            self.message.value = ui.status("error", "Not saved.", [self.store.refusal()])
            return False
        self.message.value = ""
        return True

    def _chosen(self, representation: str | None) -> None:
        if self._building or representation is None:
            return
        saved = self.saved
        if saved is not None and saved[1] == representation:
            return
        self._save(representation, saved[2] if saved else ())
        self._on_change()

    def _id_typed(self) -> None:
        """Check the typed ID at once; save it only for an answered mask and when it is valid."""
        if self._building:
            return
        self._show_id_state()
        saved = self.saved
        if saved is not None and self.id_box.value != saved[0]:
            if self._save(saved[1], saved[2]):
                self._on_change()

    def _made_from_changed(self) -> None:
        if self._building:
            return
        saved = self.saved
        if saved is not None:
            self._save(saved[1], self._chosen_channels())
            self._on_change()


__all__ = ["OPTIONS", "MaskQuestion", "evidence_text"]
