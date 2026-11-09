"""The shipped notebook's 13 steps, one on screen at a time (owner decision 2026-11-09).

Pure composition of the cards the notebook already has (UX proposal 2026-11-09, PR 17a): the
order is images found, axes, pixel sizes, conditions, replicates, compared against, image type,
stains, tag channels, masks, check and save (with the overview of steps 1-10), run, and describe
the masks. Each answer is still saved, refused and checked by the card that asks it; the stepper
(:mod:`.notebook_stepper`) only shows one page and says, from each card's state
(:mod:`.notebook_step_states`), which steps are done.

Three things follow the layout and nothing else: the tag grid leaves the image type to its own
page (:class:`.notebook_channels_type.ImageTypeRows`, the same answers), the condition names and
the replicate names sit on their own steps (:mod:`.notebook_names`), and the consistency card
follows the pairing, shown only when it warns. A page holding a card the saved reopening folded
unfolds it when it is opened. ``folder_step`` and ``axis_step`` are unchanged for embedders.
"""

from __future__ import annotations

import ipywidgets as widgets

from . import notebook_style as ui
from .dataset import CONSISTENCY_CODES
from .notebook_channels import INTRO as CHANNELS_INTRO
from .notebook_channels_type import ImageTypeRows
from .notebook_check_save import INTRO as CHECK_INTRO
from .notebook_masks_screen import INTRO as MASKS_INTRO
from .notebook_step_states import _StepStates
from .notebook_stepper import Step, Stepper
from .run import Preflight

FOLDER_INTRO = (
    "<b>What happens here:</b> the folders are scanned, the labels are taken from the folder "
    "names (condition / biological repeat / technical repeat) and every image header is "
    "checked. Problems are listed with what to do. Nothing in the folder is changed."
)
AXES_INTRO = (
    "<b>Why this matters:</b> every image is cut into flat 2D planes, one per depth slice, "
    "channel and time point. The roles below are suggested from the image metadata, which is "
    "not always right, so check them with the preview before confirming. The pictures of the "
    "later steps are read only through the roles you confirm."
)
CONDITIONS_HELP = (
    "Each condition gets a short name (its ID), which later bricks use and which never changes "
    "once the experiment description is saved. The folder name is proposed when it can be used "
    "as it is; otherwise type one. The <i>shown as</i> name is only for plots and tables."
)
REPLICATES_HELP = (
    "Each biological replicate, and each technical-repeat folder, gets a short name (its ID), "
    "which never changes once the experiment description is saved. A replicate pairs one folder "
    "per condition (above); its name is proposed from the folder name when it can be used."
)
OVERVIEW_STEPS = 10
"""The overview on *Check and save* restates steps 1 to 10."""
UNFOLDING = {4: "design_card", 5: "references_card", 9: "masks_box"}
"""The steps (0-based) that show a card the saved reopening folds."""


class _StepsFlow(_StepStates):
    """``steps()``: the stepper of the shipped notebook, and the hooks that keep it current."""

    def steps(self) -> widgets.Widget:
        """Return the notebook's 13 steps, one shown at a time, with a strip and Back/Next.

        The shipped notebook shows this (with ``describe=True``) instead of the stacked
        pages. Every answer is given and saved on the card that asks it, as on the pages;
        Next is never blocked, and the save and the run still refuse what is missing.

        Returns
        -------
        ipywidgets.Widget
            The stepper; calling again returns the same one.
        """
        if getattr(self, "_stepper", None) is not None:
            return self._stepper.widget
        self.channel_grid.place_types = False  # the image type has its own step
        if ui.is_visible(self.channel_grid.widget):
            self.channel_grid.refresh()
        for box in (self.folder_next, self.axis_next):
            ui.set_visible(box, False)  # Next and the strip say where to go
        self.type_rows = ImageTypeRows(self.channel_types)
        self.overview_box = widgets.VBox()
        self.consistency_slot = widgets.VBox([self.consistency_card])
        self._stepper = Stepper(self._step_list(), self._step_shown)
        self.overview_box.children = [self._stepper.overview(OVERVIEW_STEPS)]
        self._refresh_type_rows()
        self._refresh_steps()
        return self._stepper.widget

    def _step_list(self) -> list[Step]:
        """Return the 13 steps: titles, purposes, the existing cards, and their states."""
        states = self._step_states()
        pages = [
            ("Images found", "Choose the folder with your images and check it.",
             self._images_page()),
            ("Axes", "Say which image dimension is width, height, channel and time.",
             [ui.intro(AXES_INTRO), self.axis_box]),
            ("Pixel sizes", "The size of one pixel, and where it comes from.",
             [self.calibration_card]),
            ("Conditions", "A short name for each condition.", [self._conditions_card()]),
            ("Replicates", "Which folders are one replicate, and their names.",
             [self.design_card, self._replicates_card(), self.consistency_slot]),
            ("Compared against", "One question per condition.", [self.references_card]),
            ("Image type", "Separate channels or a colour (RGB) picture, per folder.",
             [self.type_rows.widget]),
            ("Stains", "Name what you stained.",
             [ui.intro(CHANNELS_INTRO), self.channel_labels.widget]),
            ("Tag channels", "Say which stain each channel is, looking at its picture.",
             [self.channel_grid.widget]),
            ("Masks", "Say what each mask type is.", [ui.intro(MASKS_INTRO), self.masks_box]),
            ("Check and save", "Check your experiment, confirm each replicate, then save.",
             [self.overview_box, ui.intro(CHECK_INTRO), self.fold_bar, self.check_waiting,
              self.check_card]),
            ("Run", "Read the images one plane at a time and write the histogram table.",
             self._run_page()),
            ("Describe the masks", "Optional: how the mask types relate.",
             [self.mask_step_card]),
        ]  # fmt: skip
        waits = self._step_waits()
        return [
            Step(title, purpose, children, state, waits.get(i, _nothing))
            for i, ((title, purpose, children), state) in enumerate(zip(pages, states, strict=True))
        ]

    def _images_page(self) -> list[widgets.Widget]:
        """Return step 1's cards: the folders, the check and what it found."""
        return [
            ui.intro(FOLDER_INTRO),
            self.folder_card,
            self.output_card,
            self.masks_input_card,
            self.exclude_box,
            self.check_button,
            self.folder_status,
            self.folder_lock.card,
            self.check_report.widget,
            self.folder_tiles,
            self.labels_card,
            self.masks_card,
            self.issues_card,
        ]

    def _run_page(self) -> list[widgets.Widget]:
        """Return the run's cards (the mask step has its own step after it)."""
        return [
            self.readiness_card,
            self.settings_card,
            self.plan_card,
            self.run_button,
            self.progress,
            self.run_status,
            self.run_lock.card,
            self.run_report.widget,
            self.run_tiles,
            self.run_issues,
            self.run_checks.card,
            self.details_box,
        ]

    def _conditions_card(self) -> widgets.Widget:
        return ui.card("Conditions", [ui.help_text(CONDITIONS_HELP), self.condition_rows])

    def _replicates_card(self) -> widgets.Widget:
        return ui.card(
            "Replicate and technical-repeat names",
            [ui.help_text(REPLICATES_HELP), self.names_status, self.replicate_rows,
             self.single_note],
        )  # fmt: skip

    def _step_waits(self) -> dict:
        """Return, by step (0-based), why it cannot be answered yet (HTML, empty when it can)."""
        after_check = self._wait_for_check
        return {
            2: lambda: "" if self.preflight is not None else after_check(),
            3: after_check,
            4: after_check,
            5: self._wait_for_references,
            6: self._wait_for_channels,
            7: self._wait_for_channels,
            8: self._wait_for_tags,
            12: self._wait_for_mask_step,
        }

    def _wait_for_check(self) -> str:
        if self.draft is not None:
            return ""
        return ui.status("info", "Waiting for step 1.", ["Check the experiment folder first."])

    def _wait_for_references(self) -> str:
        if self.draft is not None and len(self.draft.conditions) < 2:
            return ui.status("ok", "There is one condition: nothing to compare.")
        return self._wait_for_check()

    def _wait_for_channels(self) -> str:
        return "" if self._channels_key is not None else self.channels_waiting.value

    def _wait_for_tags(self) -> str:
        waiting = self._wait_for_channels()
        if waiting or self.channel_labels.confirmed:
            return waiting
        return ui.status("info", "Waiting for the stains.", ["Confirm the stains in step 8 first."])

    def _wait_for_mask_step(self) -> str:
        if ui.is_visible(self.mask_step_card):
            return ""
        return ui.status("info", "Not offered.", ["The output folder is not shown."])

    # Hooks -------------------------------------------------------------------------------

    def _step_shown(self, index: int) -> None:
        """Unfold the saved reopening when a step shows one of the cards it folds."""
        if index in UNFOLDING and getattr(self, "_folded", False):
            self._unfold()

    def _refresh_steps(self) -> None:
        """Update the strip and the consistency card's place (shown only when it warns)."""
        stepper = getattr(self, "_stepper", None)
        if stepper is None:
            return
        warns = self.preflight is not None and any(
            i.code in CONSISTENCY_CODES for i in self._step1_issues()
        )
        ui.set_visible(self.consistency_slot, warns)
        stepper.refresh()

    def _refresh_run_step(self) -> None:
        """Update the run step, then the strip (every answer ends here)."""
        super()._refresh_run_step()
        self._refresh_steps()

    def check_folder(self) -> Preflight | None:
        """Check the folder; a reopened experiment opens on its first step still to do."""
        pre = super().check_folder()
        stepper = getattr(self, "_stepper", None)
        if stepper is not None and pre is not None and self._published() is not None:
            states = stepper.states()
            first = next(
                (i for i, s in enumerate(states[: OVERVIEW_STEPS + 1]) if not s.done),
                OVERVIEW_STEPS + 1,
            )
            stepper.go(first)
        return pre

    def _refresh_type_rows(self) -> None:
        """Open the image-type step for the folders the channel step opened, or close it."""
        rows = getattr(self, "type_rows", None)
        if rows is None:
            return
        if self._channels_key is None:
            rows.close()
        elif rows.types.layouts is not self._channel_layouts or not ui.is_visible(rows.widget):
            rows.open(self._channel_layouts)
        else:
            rows.refresh()

    def _refresh_channels(self) -> None:
        super()._refresh_channels()
        self._refresh_type_rows()

    def _hide_channels(self, why: str = "") -> None:
        super()._hide_channels(why)
        self._refresh_type_rows()

    def _types_changed(self) -> None:
        super()._types_changed()
        self._refresh_type_rows()
        self._refresh_steps()


def _nothing() -> str:
    return ""


__all__ = ["_StepsFlow"]
