"""The notebook's *Your masks* screen (screen 4 of the natural design; Stage 1, PR 16).

Shown only when the folder check paired masks (or the draft already declares some). One question
per mask type (:class:`.notebook_mask_question.MaskQuestion`): separate objects or regions, no
default (experiment-v1 section 4.5), the mask ID being the folder name when it is a valid ID, and
optional *made from* channels, beside the evidence (:mod:`.mask_evidence`). :class:`MasksCard` is
self-contained: it reads and saves through an :class:`.notebook_channels_view.AnswerStore` and
tells its owner when an answer changed, so a later layout can move it without rewriting logic.
The pictures are read only through the axis roles the user confirmed, as on *Your channels*.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
from dataclasses import replace

import ipywidgets as widgets

from . import notebook_style as ui
from .axis_review import review_axes
from .channel_answers import label_ids
from .channel_units import unit_images
from .mask_answers import (
    drawn_on,
    dropped_channels,
    mask_ids,
    mask_kinds,
    masks_unanswered,
    masks_without_files,
    without_mask,
)
from .mask_evidence import MaskEvidence, mask_evidence, representative
from .notebook_channels import _NotebookStore
from .notebook_channels_view import ORANGE, AnswerStore
from .notebook_mask_question import MaskQuestion
from .preview import PreviewCache
from .validation import plural

INTRO = (
    "<b>What happens here:</b> say what each mask type is: <b>separate objects</b> (each object "
    "has its own number, as nuclei or cells from a segmentation) or <b>regions</b> (inside or "
    "outside, as a tissue area). Nothing is chosen for you: the pixel type and the number of "
    "values are shown only to help you answer. The mask ID is the folder name when it can be "
    "one; it is used everywhere after, also by <i>Describe the masks</i> (step 13)."
)


class MasksCard:
    """The questions about every mask type, their status and the masks that have no files now."""

    def __init__(self, store: AnswerStore, on_change: Callable[[], None]) -> None:
        self.store, self._on_change = store, on_change
        self.questions: dict[str, MaskQuestion] = {}
        self.types: tuple[str, ...] = ()
        self.labels: list[str] = []
        self.status = widgets.HTML()
        self.notes = widgets.HTML()
        self.box = widgets.VBox()
        self.gone = widgets.VBox()
        self.widget = ui.card(
            "What is each mask?",
            [
                ui.help_text(
                    "Answer once per mask type. Your answers are saved as you give them; the "
                    "mask IDs are kept for good when you save the experiment description."
                ),
                self.status,
                self.notes,
                self.box,
                self.gone,
            ],
            visible=False,
        )

    @property
    def complete(self) -> bool:
        """True when every mask type found has an answer."""
        return not masks_unanswered(self.store.answers(), self.types)

    def open(
        self, types: Sequence[str], evidence: Callable[[str], MaskEvidence], labels: list[str]
    ) -> None:
        """Build one question per mask type and show the card."""
        self.types, self.labels = tuple(types), labels
        self.questions = {
            t: MaskQuestion(self.store, t, evidence, self._changed) for t in self.types
        }
        self.box.children = [q.widget for q in self.questions.values()]
        ui.set_visible(self.widget, True)
        self.refresh(labels)

    def close(self) -> None:
        """Hide the card and forget its questions (the answers stay saved)."""
        self.questions, self.types = {}, ()
        self.box.children = self.gone.children = ()
        self.status.value = self.notes.value = ""
        ui.set_visible(self.widget, False)

    def refresh(self, labels: list[str]) -> None:
        """Redraw every question with the current answers and channel labels."""
        self.labels = labels
        for question in self.questions.values():
            question.refresh(labels)
        self._show_status()
        self._show_gone()

    def _changed(self) -> None:
        self.refresh(self.labels)
        self._on_change()

    def _show_status(self) -> None:
        """Say how many mask types are answered, and which channels are no longer labels."""
        answers = self.store.answers()
        left = masks_unanswered(answers, self.types)
        if left:
            self.status.value = ui.status(
                "warn",
                f"Still to answer: {plural(len(left), 'mask type')}.",
                [", ".join(left)],
            )
        else:
            self.status.value = ui.status(
                "ok", f"Every mask type is answered ({plural(len(self.types), 'mask type')})."
            )
        lines = [
            f"{html.escape(key)} is made from {html.escape(', '.join(gone))}, which is no "
            "longer a channel label: untick it or declare the label again."
            for key, gone in sorted(dropped_channels(answers).items())
        ]
        self.notes.value = (
            ui.status("warn", "Check these answers.", lines, raw=True) if lines else ""
        )

    def _show_gone(self) -> None:
        """List the declared masks whose type has no files now, each with a Remove button."""
        rows = []
        for source_type in masks_without_files(self.store.answers(), self.types):
            remove = ui.button(f" Remove {source_type}", style="warning", icon="trash")
            remove.on_click(lambda _, t=source_type: self._remove(t))
            text = (
                f"<span style='color:{ORANGE}'><b>{html.escape(source_type)}</b> is declared "
                "but no image has such a mask now. A declared mask without files is allowed "
                "(segmentation may come later); remove it if it is gone for good.</span>"
            )
            rows.append(widgets.HBox([widgets.HTML(text), remove]))
        self.gone.children = rows

    def _remove(self, source_type: str) -> None:
        if not self.store.update(without_mask(self.store.answers(), source_type)):
            self.notes.value = ui.status("error", "Not saved.", [self.store.refusal()])
        self._changed()


class _MasksScreen:
    """The *Your masks* screen of the notebook."""

    def _build_masks_screen(self) -> None:
        """Build the card (hidden until the folder check paired masks)."""
        self.mask_card = MasksCard(_NotebookStore(self), self._masks_answered)
        self.masks_waiting = widgets.HTML()
        self._masks_key: tuple | None = None
        self._mask_found: dict[tuple[str, int | None], MaskEvidence] = {}
        self._mask_cache = PreviewCache()
        self.masks_box = widgets.VBox([self.masks_waiting, self.mask_card.widget])

    def masks_step(self, title: str | None = None, purpose: str | None = None) -> widgets.Widget:
        """Return the masks screen: one question per mask type, beside its pictures."""
        return ui.page(
            "Your masks" if title is None else title,
            "Say what each mask type is: separate objects or regions."
            if purpose is None
            else purpose,
            [ui.intro(INTRO), self.masks_box],
        )

    def _mask_types(self) -> tuple[str, ...]:
        pre = self.preflight
        return tuple(pre.masks.types) if pre is not None and pre.masks is not None else ()

    def _hide_masks_screen(self, why: str) -> None:
        self._masks_key = None
        self._mask_found = {}
        self.mask_card.close()
        self.masks_waiting.value = why

    def _refresh_masks_screen(self) -> None:
        """Open or update the masks screen, then restate the experiment on *Check and save*."""
        self._refresh_masks_only()
        self._refresh_check_save()

    def _refresh_masks_only(self) -> None:
        """Open the screen for the checked folder's masks once the axis roles are confirmed."""
        types = self._mask_types()
        answers = self.draft_answers
        if self.draft is None or answers is None:
            self._hide_masks_screen(
                ui.status("info", "Waiting for step 1.", ["Check the folder and answer its cards."])
            )
            return
        if not types and not answers.masks:
            self._hide_masks_screen(
                ui.status(
                    "info", "No masks.", ["The folder check paired no masks: nothing to say."]
                )
            )
            return
        if self.confirmed_mapping is None:
            self._hide_masks_screen(
                ui.status(
                    "info",
                    "Waiting for the axis roles.",
                    ["Confirm the axis roles in step 2 first: the pictures are read through them."],
                )
            )
            return
        labels = label_ids(answers)
        key = (
            str(self.preflight.discovery.root),
            self.confirmed_mapping,
            types,
            id(self.preflight),
        )
        self.masks_waiting.value = ""
        if key != self._masks_key:
            self._masks_key, self._mask_found = key, {}
            self.mask_card.open(types, self._mask_evidence, labels)
        else:
            self.mask_card.refresh(labels)

    def _mask_evidence(self, source_type: str) -> MaskEvidence:
        """Return the evidence of a mask type, drawn on the channel its answer names."""
        pre = self.preflight
        images = unit_images(pre.discovery, pre.excluded)
        analysed = sorted(i for found in images.values() for i in found)
        first = representative(pre.masks, source_type, analysed)
        unit = next((k for k, found in images.items() if first and first.image in found), None)
        channel = drawn_on(self.draft_answers, source_type, unit)
        if (source_type, channel) not in self._mask_found:
            review = review_axes(
                pre.metadata,
                self.confirmed_mapping,
                missing_as_singleton=self.missing_as_singleton.value,
            )
            self._mask_found[(source_type, channel)] = mask_evidence(
                pre.masks,
                source_type,
                pre.discovery.root,
                analysed,
                review,
                channel,
                self._mask_cache,
                self._memory_limit(),
            )
        found = self._mask_found[(source_type, channel)]
        tags = self.draft_answers.units.get(unit) if unit else None
        index = found.channel or 0
        tag = tags.channels[index] if tags and index < len(tags.channels) else None
        return replace(found, label=tag)

    def _mask_ids(self) -> dict[str, str] | None:
        """Return the mask ID of every described mask type (step 3 names the masks by them).

        None when the notebook describes no experiment (an embedding without the design).
        """
        answers = getattr(self, "draft_answers", None)
        return None if answers is None else mask_ids(answers)

    def _mask_kinds(self) -> dict[str, str] | None:
        """Return what the user declared each mask type to be (objects or regions), or None."""
        answers = getattr(self, "draft_answers", None)
        return None if answers is None else mask_kinds(answers)

    def _masks_answered(self) -> None:
        """Take *Check and save* into account after a mask answer."""
        self._refresh_check_save()


__all__ = ["INTRO", "MasksCard", "_MasksScreen"]
