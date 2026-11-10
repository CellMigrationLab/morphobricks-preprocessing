"""The shipped notebook's 12 steps, one on screen at a time (owner decision 2026-11-09).

Pure composition of the cards the notebook already has (UX proposal 2026-11-09, PR 17a): the
order is images found, axes (with the pixel size, owner 2026-11-10), conditions, replicates,
compared against, image type,
stains, tag channels, masks, check and save (with the overview of steps 1-9), run, and describe
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
from .notebook_axes_quick import _QuickAxes
from .notebook_axis_step import ROLES_HELP
from .notebook_channels_type import ImageTypeRows
from .notebook_check_save import INTRO as CHECK_INTRO
from .notebook_compact import CHIPS, _CompactPages
from .notebook_masks_screen import INTRO as MASKS_INTRO
from .notebook_quick_scan import _QuickScan
from .notebook_step_states import _StepStates
from .notebook_stepper import Step, Stepper, about
from .run import Preflight

AXES_INTRO = (
    "<b>Why this matters:</b> every image is cut into flat 2D planes, one per depth slice, "
    "channel and time point. The roles below are suggested from the image metadata, which is "
    "not always right, so check them with the example before confirming. The pictures of the "
    "later steps are read only through the roles you confirm.<br>"
    + ROLES_HELP
    + " The pixel size is the size of one pixel along X and Y, from the image headers or stated "
    "by you; nothing is guessed, and an image without one is uncalibrated."
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
OVERVIEW_STEPS = 9
"""The overview on *Check and save* restates steps 1 to 9."""
UNFOLDING = {3: "design_card", 4: "references_card", 8: "masks_box"}
"""The steps (0-based) that show a card the saved reopening folds."""


class _StepsFlow(_CompactPages, _QuickScan, _QuickAxes, _StepStates):
    """``steps()``: the stepper of the shipped notebook, and the hooks that keep it current."""

    def steps(self) -> widgets.Widget:
        """Return the notebook's 12 steps, one shown at a time, with a strip and Back/Next.

        The shipped notebook shows this instead of the stacked
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
        self.type_rows = ImageTypeRows(self.channel_types, lambda: self._channel_thumbs)
        self.type_rows.reopen = self.channel_grid.reopen = self._reopen_units
        self.overview_box = widgets.VBox()
        self.overview_fold = ui.accordion("Your answers, step by step", [self.overview_box])
        self._build_quick_axes()
        self.axis_sizes_slot.children = [self.calibration_card]  # with the axes (owner)
        ui.set_visible(self.roles_help, False)  # said under About this step
        self._build_quick_scan()
        self.consistency_slot = widgets.VBox([self.consistency_card])
        self._stepper = Stepper(self._step_list(), self._step_shown)
        self.overview_box.children = [self._stepper.overview(OVERVIEW_STEPS)]
        self._refresh_type_rows()
        self._refresh_quick_axes()
        self._refresh_steps()
        return self._stepper.widget

    def _step_list(self) -> list[Step]:
        """Return the 12 steps: titles, purposes, the existing cards, and their states."""
        states = self._step_states()
        pages = [
            ("Images found", "Choose the folder with your images and check it.",
             self._images_page()),
            ("Axes", "Say which image dimension is width, height, channel and time.",
             [about(AXES_INTRO), self.axis_box, self.quick_card]),
            ("Conditions", "A short name for each condition.", [self._conditions_card()]),
            ("Replicates", "Which folders are one replicate, and their names.",
             [self.design_card, self._replicates_card(), self.consistency_slot]),
            ("Compared against", "One question per condition.", [self.references_card]),
            ("Image type", "Separate channels or a colour (RGB) picture, per folder.",
             [self.type_rows.widget]),
            ("Stains", "Name what you stained.",
             [self.channel_intro, self.channel_labels.widget]),
            ("Tag channels", "Say which stain each channel is, looking at its picture.",
             [self.channel_grid.widget]),
            ("Masks", "Say what each mask type is.", [about(MASKS_INTRO), self.masks_box]),
            ("Check and save", "Check your experiment, confirm each replicate, then save.",
             [about(CHECK_INTRO), self.fold_bar, self.check_waiting, self.check_card,
              self.overview_fold]),
            ("Run", "Read the images one plane at a time and write the histogram table.",
             self._run_page()),
            ("Describe the masks", "Optional: how the mask types relate.",
             [self.mask_step_card]),
        ]  # fmt: skip
        waits = self._step_waits()
        return [
            Step(title, purpose, children, state, waits.get(i, _nothing), CHIPS.get(title, ""))
            for i, ((title, purpose, children), state) in enumerate(zip(pages, states, strict=True))
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
            0: self._consistency_note,
            2: after_check,
            3: after_check,
            4: self._wait_for_references,
            5: self._wait_for_channels,
            6: self._wait_for_channels,
            7: self._wait_for_tags,
            11: self._wait_for_mask_step,
        }

    def _consistency_note(self) -> str:
        """Say where the consistency warnings are, since step 1 counts them but shows them later."""
        if not ui.is_visible(self.consistency_slot):
            return ""
        return ui.status(
            "info",
            "The consistency warnings are on Replicates.",
            ["They depend on which folders are one replicate: pair the folders there first."],
        )

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
        if waiting or self.channel_labels.confirmed or self._stains_not_applicable:
            return waiting
        return ui.status("info", "Waiting for the stains.", ["Confirm the stains first (Stains)."])

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
        self._refresh_folds()
        stepper.refresh()

    def _refresh_run_step(self) -> None:
        """Update the run step, then the strip (every answer ends here)."""
        super()._refresh_run_step()
        self._refresh_steps()

    def check_folder(self) -> Preflight | None:
        """Check the folder; a reopened experiment opens on its first step still to do."""
        pre = super().check_folder()
        if pre is not None and getattr(self, "_stepper", None) is not None:
            # The headers are read: the quick look made way for what the check found.
            ui.set_visible(self.scan_box, False)
            self.scan_status.value = ""
            self._refresh_folds()
            self._go_first_open()
        return pre

    def _go_first_open(self) -> None:
        """Open a reopened experiment's first step still to do (Run when none is)."""
        stepper = getattr(self, "_stepper", None)
        if stepper is None or self._published() is None:
            return
        states = stepper.states()
        first = next(
            (i for i, s in enumerate(states[: OVERVIEW_STEPS + 1]) if not s.done),
            OVERVIEW_STEPS + 1,
        )
        stepper.go(first)

    def _confirm_quick_axes(self) -> bool:
        """Confirm the offered axes; a reopened experiment then opens its next open step."""
        confirmed = super()._confirm_quick_axes()
        if confirmed:
            self._go_first_open()
        return confirmed

    def _mapping_changed(self) -> None:
        super()._mapping_changed()
        self._refresh_quick_axes(drawn=True)  # the example was just redrawn

    def _toggle_confirmation(self) -> None:
        super()._toggle_confirmation()
        self._refresh_quick_axes()

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
