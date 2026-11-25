"""The image type in one line of the Channels step: what the channel dimension holds.

Owner decision (10 November 2026, item (p)): the image type says what the C dimension means
(one stain or marker per channel, or the red, green and blue of a colour picture). Owner
decision (12 November 2026): it is **proposed** at the quick scan (display only, from the
headers: :meth:`.notebook_scan_axes._ScanAxes._show_scan_type`, nothing recorded there) and
**confirmed** here, on the Channels step, since it decides whether the channels are R, G and B
or the user's stains; it is no longer on the Axes step. Asked in the pattern of the pixel size:

- **one line for all folders**, its choice pre-filled from what the image headers say (colour
  only when every folder's header says colour, separate channels only when none does). The
  pre-fill is a proposal: nothing is saved until the user clicks **Use for all folders**;
- **Set per folder…**, collapsed, holds the row of each folder
  (:class:`.notebook_channels_type.ImageTypeRows`: the same cells, answers and refusals); the
  rows, and the picture of each folder, are built only once it is opened;
- when the headers **differ**, nothing is pre-filled and one line says how many folders say
  what; the one answer for all folders is still offered (owner, 25 November 2026: *separate
  stains or markers* answered once for every folder, whatever their channel or image counts),
  and the per-folder part opens only when the answers themselves differ.

After the answer, every folder whose header does not fit it is listed under the line
(:func:`followups`): a header that says colour answered *separate channels*, or one that does
not say colour answered *colour* (with three interleaved samples it is the RGB header override
below, Spec RH1; otherwise the run's ``R_IMAGE_TYPE_CONTRADICTS_HEADER``). They are listed, not
a reason to refuse the others' answer. *Colour (RGB)* for all folders is recorded only when
every folder has three channels; otherwise nothing is written and every folder that cannot be
colour is named.

The answers are the per-unit ``image_type`` of the draft, saved through
:func:`.channel_answers.with_type` exactly as the per-folder radio buttons save them: clicking
**Use for all folders** records what choosing that type in every row records. A choice that
contradicts a header is still allowed and said in words (the run refuses it,
``R_IMAGE_TYPE_CONTRADICTS_HEADER``); folders answered differently stay refused by the save and
the run (``R_HETEROGENEOUS_LAYOUT_NOT_ENABLED``, Spec Stages 1-2), and the line says so at once.
Folders whose headers have three interleaved samples but do not say colour get the RGB header
override offer (:class:`.notebook_rgb_override.RgbOverrideOffer`, Spec RH1) on this card.
Replacing answers already given asks first, as the pixel size does.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import ipywidgets as widgets

from . import notebook_style as ui
from .channel_answers import synced, with_override, with_type
from .channel_labels import UnitLayout
from .draft_answers import DraftAnswers
from .notebook_channels_type import ImageTypeColumn, ImageTypeRows
from .notebook_channels_view import save_change
from .notebook_render import blocked_line
from .notebook_rgb_override import RgbOverrideOffer
from .validation import plural

OPTIONS = [("separate stains or markers, one per channel", "channels"), ("colour (RGB)", "rgb")]
WORDS = {"channels": "separate channels", "rgb": "colour (RGB)"}
WAITING = (
    "Asked once the axis roles are confirmed: the channels of each folder are read through them."
)
MIXED = (
    "This version runs one image type per experiment: Check and save refuses folders of "
    "different types (R_HETEROGENEOUS_LAYOUT_NOT_ENABLED)."
)


def followups(layouts: Sequence[UnitLayout], types: ImageTypeColumn) -> list[str]:
    """Return a line per answered folder whose header does not fit its answer (the next step).

    The existing rules, said per folder: nothing here records or refuses anything.
    """
    out = []
    for layout in layouts:
        saved = types.saved(layout.key)
        name = " / ".join(p for p in layout.key if p != "single")
        unit = types.store.answers().units.get(layout.key)
        if saved == "channels" and layout.rgb:
            out.append(
                f"{name}: its header says colour (RGB) but it is answered separate channels; "
                "the run refuses it (R_IMAGE_TYPE_CONTRADICTS_HEADER): answer it colour under "
                "Set per folder…, or correct its header"
            )
        elif saved == "rgb" and not layout.rgb and layout.three_samples:
            if unit is None or not unit.rgb_header_override:
                out.append(
                    f"{name}: three interleaved samples under a header that does not say "
                    "colour: tick the RGB header override below (RH1)"
                )
        elif saved == "rgb" and not layout.rgb:
            out.append(
                f"{name}: its header says separate channels but it is answered colour; the "
                "run refuses it (R_IMAGE_TYPE_CONTRADICTS_HEADER)"
            )
    return out


def counted(values: Sequence[str]) -> str:
    """Return ``colour (RGB) in 2 folders, separate channels in 10`` for these types."""
    found = Counter(values)
    return ", ".join(
        f"{WORDS[value]} in {plural(found[value], 'folder')}" for value in WORDS if found[value]
    )


class ImageTypeLine:
    """One image type for all folders, confirmed by a click; each folder on demand."""

    def __init__(self, types: ImageTypeColumn, rows: ImageTypeRows) -> None:
        self.types, self.rows = types, rows
        self._pending: str | None = None
        self._shown_for: object = None
        self.line = widgets.HTML()
        self.choice = widgets.Dropdown(
            options=OPTIONS, value=None, layout=widgets.Layout(width="330px")
        )
        self.apply = ui.button(
            " Use for all folders",
            style="success",
            icon="check",
            tooltip="Record this image type for every folder of the experiment",
        )
        self.form = widgets.HBox(
            [widgets.HTML("<b>The channels are</b>&nbsp;"), self.choice, self.apply],
            layout=widgets.Layout(align_items="center", flex_flow="row wrap"),
        )
        self.blocker = widgets.HTML()
        self.replace_text = widgets.HTML()
        self.replace_yes = ui.button(" Replace them", style="warning", icon="check")
        self.replace_no = ui.button(" Cancel", icon="times")
        self.replace_box = widgets.VBox(
            [self.replace_text, widgets.HBox([self.replace_yes, self.replace_no])]
        )
        ui.set_visible(self.replace_box, False)
        self.message = widgets.HTML()
        self.followups = widgets.HTML()
        self.override = RgbOverrideOffer(types)
        self.per_folder = ui.accordion("Set per folder…", [rows.widget])
        # The rows of each folder are built, and their pictures read, only once opened.
        rows.folded = lambda: self.per_folder.selected_index is None
        self.per_folder.observe(lambda _: rows.unfolded(), names="selected_index")
        self.widget = ui.card(
            "Image type",
            [
                self.line,
                self.form,
                self.blocker,
                self.replace_box,
                self.message,
                self.followups,
                self.override.widget,
                self.per_folder,
            ],
        )
        self.apply.on_click(lambda _: self._clicked())
        types.default = lambda: self.choice.value
        types.on_form = self._form_changed
        self.replace_yes.on_click(lambda _: self._replace())
        self.replace_no.on_click(lambda _: self._cancel())
        self.choice.observe(lambda _: self._choice_changed(), names="value")
        self.refresh()

    # What there is -----------------------------------------------------------------------

    def _usable(self) -> list[UnitLayout]:
        return [u for u in self.types.layouts if u.usable]

    @property
    def opened(self) -> bool:
        """True while the folders' channels are known (the axis roles are confirmed)."""
        return ui.is_visible(self.rows.widget)

    def suggestion(self) -> str | None:
        """Return the type every header suggests, None when they differ or there is none."""
        said = {"rgb" if u.rgb else "channels" for u in self._usable()}
        return said.pop() if len(said) == 1 else None

    def saved(self) -> list[str | None]:
        """Return the type the user answered for each usable folder (None where not yet)."""
        return [self.types.saved(u.key) for u in self._usable()]

    @property
    def complete(self) -> bool:
        """True when every usable folder has an answer (the folders may still differ)."""
        saved = self.saved()
        return bool(saved) and None not in saved

    # Showing -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the line for the folders as they are answered now."""
        self._cancel()
        usable = self._usable()
        shown = self.opened and bool(usable)
        for widget in (self.form, self.per_folder):
            ui.set_visible(widget, shown)
        self.override.refresh(shown)
        if not shown:
            self.line.value = ui.status("info", WAITING) if not self.opened else ""
            self.blocker.value = self.message.value = self.followups.value = ""
            self._shown_for = None
            return
        saved, suggestion = self.saved(), self.suggestion()
        answered = [value for value in saved if value is not None]
        one = answered[0] if len(set(answered)) == 1 and len(answered) == len(saved) else None
        key = (tuple(u.key for u in usable), tuple(saved), suggestion)
        if key != self._shown_for:  # the user's own choice in the box is kept otherwise
            self._shown_for = key
            self.choice.value = one or (suggestion if not answered else None)
        self.line.value = self._line(saved, answered, one, suggestion)
        later = followups(usable, self.types)
        self.followups.value = (
            ui.status("warn", f"Another step for {plural(len(later), 'folder')}:", later)
            if later
            else ""
        )
        if len(set(answered)) > 1 or self.types.message:
            self.per_folder.selected_index = 0  # the answers differ: each folder is shown
        self._choice_changed()

    def _line(
        self, saved: list, answered: list[str], one: str | None, suggestion: str | None
    ) -> str:
        """Return the line: the answer, the proposal from the headers, or how they differ."""
        folders = plural(len(saved), "folder")
        said = [("rgb" if u.rgb else "channels") for u in self._usable()]
        if one is not None:
            lines = [] if one == suggestion else [f"The image headers say: {counted(said)}."]
            return ui.status("ok", f"Image type: {WORDS[one]} in all {folders}.", lines)
        if len(set(answered)) > 1:
            left = len(saved) - len(answered)
            more = f", {left} not answered" if left else ""
            return ui.status("warn", f"Image types differ: {counted(answered)}{more}.", [MIXED])
        if answered:
            return ui.status(
                "warn",
                f"Not finished: {counted(answered)}, {len(saved) - len(answered)} not answered.",
            )
        if suggestion is None:
            return ui.status(
                "warn",
                f"The image headers differ: {counted(said)}.",
                [
                    "Nothing is proposed: choose one type for all folders in the box and click "
                    "Use for all folders, or each folder's type under Set per folder…. " + MIXED
                ],
            )
        return ui.status(
            "info",
            f"Proposed from the image headers: {WORDS[suggestion]} in all {folders}.",
            ["Not saved until you click Use for all folders."],
        )

    @property
    def deferred(self) -> bool:
        """True with the form of the stepper: choices change the form, Validate saves."""
        return self.types.form is not None

    def _choice_changed(self) -> None:
        """Offer the click for a type that is not already every folder's answer."""
        if self.deferred:
            self._form_changed(cells=True)
            return
        self._cancel()
        value, saved = self.choice.value, self.saved()
        everywhere = value is not None and all(s == value for s in saved)
        ui.set_visible(self.apply, not everywhere)
        self.apply.disabled = value is None
        self.blocker.value = blocked_line(
            ["choose a type in the box, or set each folder below"] if value is None else []
        )

    # The form (the stepper: owner, 25 November 2026) -----------------------------------

    def final(self) -> dict:
        """Return the type Validate would record for each usable folder (None: not chosen)."""
        return {u.key: self.types.chosen(u) for u in self._usable()}

    def _form_changed(self, cells: bool = False) -> None:
        """Update what the form shows in place after a choice: no save, no redraw.

        ``cells``: the box changed, so the folders without their own choice show it too.
        """
        self._cancel()
        types = self.types
        if cells:
            types._building = True
            try:
                for layout in self._usable():
                    radio = types.radios.get(layout.key)
                    if radio is not None and layout.key not in types.form:
                        radio.value = types.chosen(layout)
            finally:
                types._building = False
            for layout in self._usable():
                if layout.key in types.notes:
                    types.notes[layout.key].value = types.note(layout)
        self.apply.description = " Validate"
        ui.set_visible(self.apply, True)
        missing = [k for k, v in self.final().items() if v is None]
        self.apply.disabled = bool(missing)
        self.blocker.value = blocked_line(
            [f"choose a type in the box, or set each folder below ({len(missing)} left)"]
            if missing
            else []
        )
        self.override.refresh(True)

    def _validate(self, replace_ok: bool = False) -> bool:
        """Check the form and save every image-type answer in one draft write.

        Every folder needs a type; colour only for folders with three channels (all or none,
        naming each); replacing answers already saved asks first. The folders whose header does
        not fit their answer are listed afterwards (:func:`followups`), never a refusal here.
        """
        final, usable = self.final(), self._usable()
        missing = [u for u in usable if final[u.key] is None]
        if missing:
            self.message.value = ui.status(
                "error", "Not saved: choose the type of every folder.", self._names(missing)
            )
            return False
        not3 = [u for u in usable if final[u.key] == "rgb" and u.channels != 3]
        if not3:
            self.message.value = ui.status(
                "error",
                "Nothing was changed: a colour image has 3 channels (RGB).",
                [
                    f"{n} ({u.channels} channels)"
                    for n, u in zip(self._names(not3), not3, strict=True)
                ],
            )
            return False
        others = [u for u in usable if self.types.saved(u.key) not in (None, final[u.key])]
        if others and not replace_ok:
            self._pending = "form"
            self.replace_text.value = ui.status(
                "warn",
                f"Change the type of {plural(len(others), 'folder')} already answered? "
                "Nothing is written until you click Replace them.",
                ["Their channel tags are cleared: a channel is tagged again after its type."],
            )
            ui.set_visible(self.replace_box, True)
            return False
        return self._write_form(final)

    @staticmethod
    def _names(layouts: Sequence[UnitLayout]) -> list[str]:
        return [" / ".join(p for p in u.key if p != "single") for u in layouts]

    def _write_form(self, final: dict) -> bool:
        """Save the form's types (and the override box) in one draft write."""
        store, layouts, usable = self.types.store, self.types.layouts, self._usable()
        override = self.override.pending
        offered = {u.key for u in self.override.offered()}

        def change(answers: DraftAnswers) -> DraftAnswers:
            for layout in usable:
                if self.types.saved(layout.key) != final[layout.key]:
                    answers = with_type(answers, layout, final[layout.key])
            if override is not None:
                for layout in usable:
                    if layout.key in offered and final[layout.key] == "rgb":
                        answers = with_override(answers, layout, override)
            return synced(answers, layouts)

        try:
            ok = save_change(store, change)
        except ValueError as exc:
            self.message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return False
        if not ok:
            self.message.value = ui.status("error", "Not saved.", [store.refusal()])
            return False
        self.types.form.clear()
        self.override.pending = None
        self.message.value = self.types.message = ""
        self.types._on_change()  # the channel step opens on the saved types
        return True

    # The click ---------------------------------------------------------------------------

    def _clicked(self) -> None:
        """Validate the form (the stepper), or record the box's type for every folder."""
        if self.deferred:
            self._validate()
        else:
            self._use_for_all()

    def _use_for_all(self) -> None:
        """Record the chosen type for every folder, asking first when it replaces answers."""
        value = self.choice.value
        if value is None:
            return
        others = [u for u in self._usable() if self.types.saved(u.key) not in (None, value)]
        if not others:
            self._write(value)
            return
        self._pending = value
        self.replace_text.value = ui.status(
            "warn",
            f"Use {WORDS[value]} for all folders? {plural(len(others), 'folder')} answered "
            "otherwise; nothing is written until you click Replace them.",
            ["Their channel tags are cleared: a channel is tagged again after its type changes."],
        )
        ui.set_visible(self.replace_box, True)

    def _replace(self) -> None:
        pending = self._pending
        self._cancel()
        if pending == "form":
            self._validate(replace_ok=True)
        elif pending is not None:
            self._write(pending)

    def _cancel(self) -> None:
        self._pending = None
        ui.set_visible(self.replace_box, False)

    def _write(self, value: str) -> bool:
        """Save ``value`` as the type of every usable folder, all or none; say a refusal."""
        store, usable, layouts = self.types.store, self._usable(), self.types.layouts
        if value == "rgb":
            other = [u for u in usable if u.channels != 3]
            if other:  # all or none: every folder that cannot be colour is named
                names = [
                    f"{' / '.join(p for p in u.key if p != 'single')} ({u.channels} channels)"
                    for u in other
                ]
                self.message.value = ui.status(
                    "error",
                    "Nothing was changed: a colour image has 3 channels (RGB).",
                    names,
                )
                return False

        def change(answers: DraftAnswers) -> DraftAnswers:
            for layout in usable:
                answers = with_type(answers, layout, value)
            return synced(answers, layouts)

        try:
            ok = save_change(store, change)
        except ValueError as exc:
            self.message.value = ui.status("error", "Nothing was changed.", [str(exc)])
            return False
        if not ok:
            self.message.value = ui.status("error", "Not saved.", [store.refusal()])
            return False
        self.message.value = ""
        self.types.message = ""
        self.types._on_change()
        return True


__all__ = ["MIXED", "OPTIONS", "WAITING", "ImageTypeLine", "counted"]
