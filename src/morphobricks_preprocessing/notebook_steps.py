"""The shipped notebook's 7 steps, one on screen at a time (owner decisions 2026-11-09/10).

Pure composition of the cards the notebook already has. The order is the owner's of
10 November 2026, what the files hold first and what it means after:

1. **Images**: the folders, the quick scan, then every header read on an explicit click;
2. **Axes**: the role of each dimension with the example, the pixel size, and the image type
   (what the channel dimension holds: :mod:`.notebook_image_type_line`);
3. **Channels**: name the stains, then tag each folder's channels;
4. **Masks**: the masks paired with the images (and their axes), what each mask type is and,
   once a run recorded them, the mask step that reads every mask and describes how the types
   relate (it reads the masks the run recorded, so it cannot come earlier; bricks that use
   masks still refuse a mask it has not read, ``A_MASK_UNCHECKED``);
5. **Design**: conditions, biological repeats and comparisons in one grid, confirmed by one
   click (:mod:`.notebook_design_step`);
6. **Check and save**; 7. **Run**, with the collapsed *Compare groups*.

Each answer is still saved, refused and checked by the card that asks it; the stepper
(:mod:`.notebook_stepper`) only shows one page and says, from each card's state
(:mod:`.notebook_step_states`), which steps are done. A page holding a card the saved
reopening folded unfolds it when it is opened. ``folder_step`` and ``axis_step`` are unchanged
for embedders.
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
from .notebook_design_step import _DesignStep
from .notebook_image_type_line import ImageTypeLine
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
    "by you; nothing is guessed, and an image without one is uncalibrated.<br>The image type "
    "says what the channel dimension holds: one stain or marker per channel, or the red, green "
    "and blue of a colour picture. It is proposed from the image headers and recorded only "
    "when you click."
)
CHANNELS_INTRO = (
    "<b>What happens here:</b> first name what you stained, then say, for every folder, which "
    "stain each channel is, looking at its picture. Nothing is chosen for you: names found in "
    "the image headers are offered only as suggestions, and each one needs your click."
)
DESIGN_INTRO = (
    "<b>What happens here:</b> you say which folders are which condition and biological "
    "repeat, and what each condition is compared against. A short name (ID) is what later "
    "bricks use; it never changes once the experiment description is saved. Later bricks "
    "normalise each biological repeat over all its conditions together, so folders of the same "
    "preparation must be the same repeat."
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
OVERVIEW_STEPS = 5
"""The overview on *Check and save* restates steps 1 to 5."""
MASKS_STEP, DESIGN_STEP = 3, 4
UNFOLDING = (MASKS_STEP, DESIGN_STEP)
"""The steps (0-based) that show a card the saved reopening folds."""


class _StepsFlow(_CompactPages, _DesignStep, _QuickScan, _QuickAxes, _StepStates):
    """``steps()``: the stepper of the shipped notebook, and the hooks that keep it current."""

    def steps(self) -> widgets.Widget:
        """Return the notebook's 7 steps, one shown at a time, with a strip and Back/Next.

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
        self.channel_grid.place_types = False  # the image type is asked with the axes
        if ui.is_visible(self.channel_grid.widget):
            self.channel_grid.refresh()
        for box in (self.folder_next, self.axis_next):
            ui.set_visible(box, False)  # Next and the strip say where to go
        self.type_rows = ImageTypeRows(self.channel_types, lambda: self._channel_thumbs)
        self.type_rows.reopen = self.channel_grid.reopen = self._reopen_units
        self.type_line = ImageTypeLine(self.channel_types, self.type_rows)
        self.overview_box = widgets.VBox()
        self.overview_fold = ui.accordion("Your answers, step by step", [self.overview_box])
        self._build_quick_axes()
        self.axis_sizes_slot.children = [self.calibration_card]  # with the axes (owner)
        ui.set_visible(self.roles_help, False)  # said under About this step
        self._build_quick_scan()
        self.consistency_slot = widgets.VBox([self.consistency_card])
        self._build_design_step()
        self._stepper = Stepper(self._step_list(), self._step_shown)
        self.overview_box.children = [self._stepper.overview(OVERVIEW_STEPS)]
        if self.draft is not None:
            self._refresh_description()  # the comparisons show their proposal
        self._refresh_type_rows()
        self._refresh_quick_axes()
        self._refresh_steps()
        return self._stepper.widget

    def _step_list(self) -> list[Step]:
        """Return the 7 steps: titles, purposes, the existing cards, and their states."""
        states = self._step_states()
        pages = [
            ("Images", "Choose the folders, look at one image, then read every header.",
             self._images_page()),
            ("Axes", "What each image dimension is, the pixel size and the image type.",
             [about(AXES_INTRO), self.axis_box, self.quick_card, self.type_line.widget]),
            ("Channels", "Name what you stained, then tag each folder's channels.",
             [about(CHANNELS_INTRO), self.channel_labels.widget, self.channel_grid.widget]),
            ("Masks", "What each mask type is; after a run, how the types relate.",
             [about(MASKS_INTRO), self.masks_card, self.masks_box, self.mask_step_card]),
            ("Design", "Conditions, biological repeats and comparisons.",
             [about(DESIGN_INTRO), self.design_step_card, self.design_edit,
              self.consistency_slot]),
            ("Check and save", "Check your experiment, confirm each replicate, then save.",
             [about(CHECK_INTRO), self.fold_bar, self.check_waiting, self.check_card,
              self.overview_fold]),
            ("Run", "Compute the histogram table; then check and compare the groups.",
             self._run_page()),
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
        return {
            0: self._consistency_note,
            2: self._wait_for_channels,
            DESIGN_STEP: self._wait_for_check,
        }

    def _consistency_note(self) -> str:
        """Say where the consistency warnings are, since step 1 counts them but shows them later."""
        if not ui.is_visible(self.consistency_slot):
            return ""
        return ui.status(
            "info",
            "The consistency warnings are on Design.",
            ["They depend on which folders are one replicate: see the pairing there first."],
        )

    def _wait_for_check(self) -> str:
        if self.draft is not None:
            return ""
        return ui.status("info", "Waiting for step 1.", ["Read the image headers first (Images)."])

    def _wait_for_channels(self) -> str:
        """Say what the channels wait for: the folder check, the axis roles, the image type."""
        if self._channels_key is None:
            return self.channels_waiting.value
        if not self.type_line.complete:
            return ui.status(
                "info",
                "Waiting for the image type.",
                ["Say on Axes (step 2) what the channels hold: a folder is tagged after that."],
            )
        return ""

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
        """Open the image type for the folders the channel step opened, or close it."""
        rows = getattr(self, "type_rows", None)
        if rows is None:
            return
        if self._channels_key is None:
            rows.close()
        elif rows.types.layouts is not self._channel_layouts or not ui.is_visible(rows.widget):
            rows.open(self._channel_layouts)
        else:
            rows.refresh()
        self.type_line.refresh()

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
